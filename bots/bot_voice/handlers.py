"""Voice-to-Action bot - transcription, summary and action-item extraction.

Pipeline: download the Telegram audio into memory -> send it to the gateway as a
multimodal ``inlineData`` part (Gemini Flash natively understands audio) ->
return a verbatim transcript, a core summary and a bulleted action list.
"""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from shared.config import settings
from shared.gateway_client import GatewayError, gateway, inline_data_part, text_part
from shared.history import HistoryManager
from shared.payments import PaymentManager
from shared.utils import (UNEXPECTED_ERROR, clean_model_output, format_duration,
                          humanize_bytes, send_long_message)

from .keyboards import CB_PREFIX, main_menu, result_actions, retry_keyboard

logger = logging.getLogger(__name__)
router = Router(name="bot_voice")

SYSTEM_PROMPT = (
    "You are Konkred Voice, an elite executive assistant that turns raw voice notes into "
    "immediately actionable intelligence. You are precise, concise and never invent content "
    "that is not present in the audio."
)

EXTRACTION_PROMPT = """Listen to the attached audio and produce EXACTLY these three sections, using these exact headings:

**📝 TRANSCRIPT**
A verbatim transcription in the original spoken language. Preserve meaning faithfully; lightly clean
filler words (um, uh) and add punctuation. If several speakers are audible, label them Speaker 1, Speaker 2, etc.

**🎯 SUMMARY**
Two to four sentences capturing the core message, context and outcome. Write it so a busy executive who never hears the audio understands what happened.

**✅ ACTION ITEMS & DECISIONS**
A bulleted list. Prefix each line with one of: [ACTION], [DECISION] or [QUESTION].
For every [ACTION] include the owner and any deadline if they were mentioned, e.g.
- [ACTION] Ana — send the revised contract by Friday.
- [DECISION] The team will ship v2 before the conference.
- [QUESTION] Unresolved: who signs off on the budget?
If the audio genuinely contains no actions, decisions or questions, write "No explicit action items were raised."

Rules: never fabricate names, numbers or deadlines. Do not add any section beyond the three above."""

SUPPORTED_AUDIO = {
    "audio/ogg", "audio/oga", "audio/mpeg", "audio/mp3", "audio/mp4", "audio/m4a",
    "audio/x-m4a", "audio/wav", "audio/x-wav", "audio/webm", "audio/flac", "audio/aac",
}

WELCOME = """🎙 <b>Voice-to-Action</b>

Send me a <b>voice note</b> or an <b>audio file</b> and I will return:

1️⃣ <b>Verbatim transcript</b> — what was actually said
2️⃣ <b>Core summary</b> — the point, in a few sentences
3️⃣ <b>Action items</b> — tasks, decisions and open questions

<b>Commands</b>
/start — this message
/help — detailed usage and limits
/clear — wipe my memory of our conversation

Just record and send — no configuration needed."""

HELP = """🎙 <b>Voice-to-Action — help</b>

<b>What I accept</b>
• Telegram voice notes (hold the mic button)
• Audio files: MP3, M4A, WAV, OGG, FLAC, AAC, WEBM
• Up to {max_mb} MB and roughly 30 minutes of speech

<b>What I return</b>
• A cleaned verbatim transcript in the original language
• A 2–4 sentence executive summary
• Tagged bullets: [ACTION] with owner/deadline, [DECISION], [QUESTION]

<b>Follow-up buttons</b>
After each transcription you can ask for action items only, a drafted reply,
an English translation, or formal meeting minutes.

<b>Privacy</b>
Audio is streamed straight to the AI provider and never written to disk.
Only the resulting text is kept (24h) so follow-ups have context. /clear removes it.

<b>Commands</b>
/start · /help · /clear"""


# NOTE: the `history: HistoryManager` parameter on each handler is injected by
# aiogram's dependency injection - main.py registers it as dispatcher workflow
# data, namespaced per bot, so every bot gets its own Redis key prefix.


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(WELCOME, reply_markup=main_menu())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP.format(max_mb=settings.max_file_mb))


@router.message(Command("clear"))
async def cmd_clear(message: Message, history: HistoryManager) -> None:
    await history.clear(message.from_user.id)
    await message.answer("🧹 Cleared. I've forgotten our previous transcriptions.")


@router.message(F.voice | F.audio | F.video_note)
async def handle_audio(
    message: Message,
    bot: Bot,
    history: HistoryManager,
    payments: PaymentManager,
) -> None:
    """Transcribe a voice note / audio file and extract actions."""
    media = message.voice or message.audio or message.video_note
    if media is None:  # pragma: no cover - guarded by the filter
        return

    size = getattr(media, "file_size", 0) or 0
    if size > settings.max_file_bytes:
        await message.answer(
            f"📦 That file is {humanize_bytes(size)}, which exceeds my "
            f"{settings.max_file_mb} MB limit.\nPlease send a shorter recording."
        )
        return

    if not await payments.require(message, message.from_user.id):
        return

    duration = getattr(media, "duration", 0) or 0
    mime = getattr(media, "mime_type", None) or ("audio/ogg" if message.voice else "audio/mpeg")
    if message.video_note:
        mime = "video/mp4"

    status = await message.answer(
        f"🎧 Processing {format_duration(duration)} of audio ({humanize_bytes(size)})…\n"
        "<i>Transcribing and extracting action items.</i>"
    )
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    try:
        file = await bot.get_file(media.file_id)
        buffer = await bot.download_file(file.file_path)
        audio_bytes = buffer.read()
    except Exception as exc:  # noqa: BLE001 - Telegram transport errors vary
        logger.exception("audio download failed")
        await status.edit_text(f"⚠️ I couldn't download that audio from Telegram.\n<code>{exc}</code>")
        return

    if not audio_bytes:
        await status.edit_text("⚠️ That file appears to be empty.")
        return

    logger.info(
        "voice: user=%s bytes=%s mime=%s duration=%ss",
        message.from_user.id, len(audio_bytes), mime, duration,
    )

    content = [text_part(EXTRACTION_PROMPT), inline_data_part(mime, audio_bytes)]

    try:
        answer = await gateway.ask(
            task_type="summarization",
            messages=[{"role": "user", "content": content}],
            system=SYSTEM_PROMPT,
            max_tokens=4000,
            temperature=0.2,
            user_id=message.from_user.id,
        )
    except GatewayError as exc:
        logger.warning("voice gateway error: %s", exc)
        await status.edit_text(exc.user_message(), reply_markup=retry_keyboard())
        return
    except Exception:  # noqa: BLE001 - last resort: never strand the user
        logger.exception("unexpected failure while transcribing audio")
        await status.edit_text(UNEXPECTED_ERROR, reply_markup=retry_keyboard())
        return

    if not answer:
        await status.edit_text(
            "🤔 I couldn't make out any speech in that recording. "
            "Try again in a quieter environment.",
            reply_markup=retry_keyboard(),
        )
        return

    # Remember the transcript so follow-up buttons have context.
    await history.add_exchange(
        message.from_user.id,
        f"[Voice note: {format_duration(duration)}]",
        answer,
    )

    await status.delete()
    await send_long_message(message, clean_model_output(answer))
    await message.answer("What next?", reply_markup=result_actions())


@router.message(F.document & F.document.mime_type.in_(SUPPORTED_AUDIO))
async def handle_audio_document(
    message: Message,
    bot: Bot,
    history: HistoryManager,
    payments: PaymentManager,
) -> None:
    """Audio sent as a file attachment rather than a voice note."""
    await handle_audio(message, bot, history, payments)


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:"))
async def handle_callbacks(
    query: CallbackQuery,
    history: HistoryManager,
    payments: PaymentManager,
) -> None:
    """Follow-up actions on the last transcription."""
    action = query.data.split(":", 1)[1]
    await query.answer()

    if action == "help":
        await query.message.answer(HELP.format(max_mb=settings.max_file_mb))
        return
    if action == "clear":
        await history.clear(query.from_user.id)
        await query.message.answer("🧹 Cleared. I've forgotten our previous transcriptions.")
        return
    if action == "retry":
        await query.message.answer("🎙 Send the audio again and I'll retry.")
        return

    prompts = {
        "actions": (
            "From the transcript above, list ONLY the action items as a checklist. "
            "Format: `- [ ] Owner — task — deadline`. If an owner or deadline is unknown write 'unassigned'. "
            "Output nothing else."
        ),
        "reply": (
            "Draft a concise, professional reply to the speaker of the voice note above. "
            "Acknowledge the key points, confirm the actions you are taking and ask about anything unresolved. "
            "Keep it under 150 words and match the original language."
        ),
        "translate": (
            "Translate the transcript above into natural, fluent English. "
            "Preserve tone and technical terminology. Output only the translation."
        ),
        "minutes": (
            "Turn the transcript above into formal meeting minutes with these sections: "
            "Attendees (or 'not stated'), Agenda, Discussion, Decisions, Action Items (owner + deadline), "
            "and Next Steps. Use clear business English."
        ),
    }
    prompt = prompts.get(action)
    if prompt is None:
        return

    turns = await history.get(query.from_user.id)
    if not turns:
        await query.message.answer("I don't have a recent transcript any more. Please send the audio again.")
        return
    if not await payments.require(query.message, query.from_user.id):
        return

    thinking = await query.message.answer("✍️ Working on it…")
    try:
        answer = await gateway.ask(
            task_type="summarization",
            messages=await history.build_messages(query.from_user.id, prompt),
            system=SYSTEM_PROMPT,
            max_tokens=2500,
            temperature=0.3,
            user_id=query.from_user.id,
        )
    except GatewayError as exc:
        await thinking.edit_text(exc.user_message())
        return
    except Exception:  # noqa: BLE001 - last resort: never strand the user
        logger.exception("unexpected failure while handling a voice action")
        await thinking.edit_text(UNEXPECTED_ERROR)
        return

    await history.add_exchange(query.from_user.id, prompt, answer)
    await thinking.delete()
    await send_long_message(query.message, clean_model_output(answer))


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text(
    message: Message,
    history: HistoryManager,
    payments: PaymentManager,
) -> None:
    """Free-form follow-up questions about the last transcript."""
    turns = await history.get(message.from_user.id)
    if not turns:
        await message.answer(
            "🎙 Send me a voice note or audio file and I'll transcribe it, "
            "summarise it and pull out the action items.\n\nUse /help for details."
        )
        return
    if not await payments.require(message, message.from_user.id):
        return

    thinking = await message.answer("💭 Thinking…")
    try:
        answer = await gateway.ask(
            task_type="summarization",
            messages=await history.build_messages(message.from_user.id, message.text),
            system=SYSTEM_PROMPT,
            max_tokens=2000,
            temperature=0.3,
            user_id=message.from_user.id,
        )
    except GatewayError as exc:
        await thinking.edit_text(exc.user_message())
        return
    except Exception:  # noqa: BLE001 - last resort: never strand the user
        logger.exception("unexpected failure while answering a follow-up")
        await thinking.edit_text(UNEXPECTED_ERROR)
        return

    await history.add_exchange(message.from_user.id, message.text, answer)
    await thinking.delete()
    await send_long_message(message, clean_model_output(answer))


__all__ = ["router"]
