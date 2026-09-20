"""IELTS Speaking Coach - FSM-driven mock interview and band-score evaluation.

State machine:

    IDLE ──/test──▶ EXAM_IN_PROGRESS ──last answer──▶ EVALUATION ──report──▶ IDLE

During ``EXAM_IN_PROGRESS`` the bot walks a scripted 3-part interview (Part 1
warm-up, Part 2 long turn with a cue card, Part 3 abstract discussion), routing
each turn through the gateway's low-latency ``classification``/``general`` lanes
so questions arrive fast. The final evaluation scores the four official criteria
and is deliberately routed to a higher-quality slot.
"""

from __future__ import annotations

import json
import logging
import random

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from shared.gateway_client import GatewayError, gateway, inline_data_part, text_part
from shared.history import HistoryManager
from shared.utils import (UNEXPECTED_ERROR, clean_model_output, escape_html,
                          send_long_message)

from .keyboards import CB_PREFIX, evaluation_menu, exam_controls, main_menu

logger = logging.getLogger(__name__)
router = Router(name="bot_ielts")


class UserPhase(StatesGroup):
    """Explicit phases of the mock-test lifecycle."""

    IDLE = State()
    EXAM_IN_PROGRESS = State()
    EVALUATION = State()


SYSTEM_PROMPT = (
    "You are an accredited IELTS Speaking examiner with 15 years of experience. You apply the "
    "official public band descriptors strictly and consistently, you never inflate scores, and "
    "every judgement you make is justified with a direct quotation from the candidate."
)

# --------------------------------------------------------------------------- #
# Question bank (a real examiner-style script, not placeholders)
# --------------------------------------------------------------------------- #

PART1_TOPICS: list[list[str]] = [
    [
        "Let's talk about where you live. Do you live in a house or an apartment?",
        "What do you like most about your neighbourhood?",
        "Is there anything you would like to change about where you live?",
    ],
    [
        "Let's talk about work and study. Do you work, or are you a student?",
        "What is the most interesting part of your work or studies?",
        "Do you think you will still be doing the same thing in five years?",
    ],
    [
        "Let's talk about free time. What do you usually do in your free time?",
        "Has the way you spend your free time changed over the last few years?",
        "Do you prefer spending free time alone or with other people? Why?",
    ],
    [
        "Let's talk about technology. How often do you use a smartphone?",
        "What app do you find most useful, and why?",
        "Do you think people spend too much time online these days?",
    ],
    [
        "Let's talk about food. What kind of food do you enjoy most?",
        "Do you prefer eating at home or eating out? Why?",
        "Have your eating habits changed since you were a child?",
    ],
]

PART2_CUE_CARDS: list[dict[str, object]] = [
    {
        "topic": "Describe a skill you would like to learn.",
        "bullets": [
            "what the skill is",
            "why you want to learn it",
            "how you would go about learning it",
            "and explain how it would change your life",
        ],
    },
    {
        "topic": "Describe a decision you made that took a long time.",
        "bullets": [
            "what the decision was",
            "why it took so long",
            "who you consulted",
            "and explain whether you think it was the right decision",
        ],
    },
    {
        "topic": "Describe a place you have visited that made a strong impression on you.",
        "bullets": [
            "where it is",
            "when you went there",
            "what you did there",
            "and explain why it made such an impression",
        ],
    },
    {
        "topic": "Describe a piece of technology you find difficult to use.",
        "bullets": [
            "what it is",
            "when you first used it",
            "what you find difficult about it",
            "and explain how you think it could be improved",
        ],
    },
    {
        "topic": "Describe a person who has influenced your career or studies.",
        "bullets": [
            "who the person is",
            "how you know them",
            "what they did",
            "and explain how they influenced you",
        ],
    },
]

PART3_QUESTIONS: list[list[str]] = [
    [
        "Let's consider this more broadly. Why do you think some skills are valued more than others in modern society?",
        "Do you think schools should focus more on practical skills than academic knowledge?",
        "How might the skills people need change over the next twenty years?",
    ],
    [
        "Let's widen the discussion. How has decision-making changed now that people have access to so much information?",
        "Do you think groups make better decisions than individuals?",
        "Should governments consult the public on major national decisions?",
    ],
    [
        "Let's think about this more generally. How does travel affect the way people see their own country?",
        "Do you think tourism always benefits local communities?",
        "What responsibilities do travellers have towards the places they visit?",
    ],
]

CRITERIA = (
    "Fluency & Coherence",
    "Lexical Resource",
    "Grammatical Range & Accuracy",
    "Pronunciation",
)

BAND_DESCRIPTORS = """📊 <b>IELTS Speaking band descriptors (summary)</b>

<b>Band 9 — Expert</b>
Fluent with only rare, content-related hesitation. Fully natural, precise vocabulary. Consistently accurate structures. Effortless pronunciation.

<b>Band 8 — Very good</b>
Fluent with occasional hesitation. Wide, flexible vocabulary including idiom. Mostly error-free grammar. Easily understood throughout.

<b>Band 7 — Good</b>
Speaks at length without noticeable effort; some repetition. Flexible vocabulary for a range of topics. Frequent error-free complex sentences. Generally clear pronunciation.

<b>Band 6 — Competent</b>
Willing to speak at length though coherence can slip. Adequate vocabulary with some imprecision. Mix of simple and complex forms with errors. Generally understandable.

<b>Band 5 — Modest</b>
Noticeable hesitation and self-correction. Limited vocabulary, frequent paraphrase. Basic forms with limited complex accuracy. Mispronunciation reduces clarity at times.

<b>Band 4 — Limited</b>
Frequent pauses and breakdowns. Vocabulary sufficient only for familiar topics. Rare subordinate clauses. Often difficult to understand.

<i>The four criteria are weighted equally; the overall score is their average, rounded to the nearest half band.</i>"""

WELCOME = """🎓 <b>IELTS Speaking Coach</b>

I run a complete, examiner-style mock test and score it against the four official criteria.

<b>The test (about 12 minutes)</b>
1️⃣ <b>Part 1</b> — 3 warm-up questions on familiar topics
2️⃣ <b>Part 2</b> — a cue card, 1–2 minute long turn
3️⃣ <b>Part 3</b> — 3 abstract discussion questions

<b>How to answer</b>
Reply with text <i>or</i> a voice note — voice answers also get pronunciation and fluency feedback.

<b>Your report</b>
A band score (1.0–9.0) for Fluency &amp; Coherence, Lexical Resource, Grammatical Range &amp; Accuracy and Pronunciation, plus an overall band, quoted errors with corrections, and upgrade vocabulary.

<b>Commands</b>: /test · /bands · /stop · /help"""

HELP = """🎓 <b>IELTS Speaking Coach — help</b>

<b>Commands</b>
/test — start a full 3-part mock test
/bands — show the band descriptors
/stop — abandon the current test
/clear — wipe my memory of your sessions
/help — this message

<b>Tips for a realistic result</b>
• Answer as you would in the real exam — don't write an essay.
• In Part 2, speak for 1–2 minutes and cover every bullet on the cue card.
• Voice notes give the most accurate score because pronunciation is assessed directly.
• Don't look anything up mid-test; the value is in the honest baseline.

<b>Scoring</b>
Each criterion is scored 1.0–9.0 in half bands and averaged for the overall band,
exactly as in the official public descriptors."""


# --------------------------------------------------------------------------- #
# Exam plan helpers
# --------------------------------------------------------------------------- #

def _build_plan(parts: tuple[int, ...] = (1, 2, 3)) -> list[dict[str, object]]:
    """Build the ordered question plan for this session."""
    plan: list[dict[str, object]] = []

    if 1 in parts:
        for question in random.choice(PART1_TOPICS):
            plan.append({"part": 1, "question": question, "type": "short"})

    if 2 in parts:
        card = random.choice(PART2_CUE_CARDS)
        bullets = "\n".join(f"  • {bullet}" for bullet in card["bullets"])
        plan.append({
            "part": 2,
            "question": (
                f"<b>Part 2 — Cue card</b>\n\n<b>{escape_html(str(card['topic']))}</b>\n\n"
                f"You should say:\n{bullets}\n\n"
                "<i>You have 1 minute to prepare, then speak for 1–2 minutes.</i>"
            ),
            "plain": str(card["topic"]),
            "type": "long",
        })

    if 3 in parts:
        for question in random.choice(PART3_QUESTIONS):
            plan.append({"part": 3, "question": question, "type": "discussion"})

    return plan


async def _ask_next(message: Message, state: FSMContext) -> None:
    """Send the next question in the plan, or move to evaluation."""
    data = await state.get_data()
    plan = json.loads(data.get("plan") or "[]")
    index = int(data.get("index", 0))

    if index >= len(plan):
        await _evaluate(message, state)
        return

    step = plan[index]
    header = f"<b>Part {step['part']} · Question {index + 1} of {len(plan)}</b>\n\n"
    body = step["question"] if step["type"] == "long" else escape_html(str(step["question"]))
    footer = (
        "\n\n🎤 <i>Reply with text or a voice note.</i>"
        if step["type"] != "long"
        else "\n\n🎤 <i>Record your long turn as a voice note for the most accurate score.</i>"
    )
    await message.answer(header + body + footer, reply_markup=exam_controls())


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.set_state(UserPhase.IDLE)
    await message.answer(WELCOME, reply_markup=main_menu())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("bands"))
async def cmd_bands(message: Message) -> None:
    await message.answer(BAND_DESCRIPTORS)


@router.message(Command("clear"))
async def cmd_clear(message: Message, state: FSMContext, history: HistoryManager) -> None:
    await state.clear()
    await history.clear(message.from_user.id)
    await message.answer("🧹 Cleared. Use /test whenever you'd like to start a new mock test.")


@router.message(Command("stop"))
async def cmd_stop(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current != UserPhase.EXAM_IN_PROGRESS.state:
        await message.answer("There's no test running. Use /test to start one.", reply_markup=main_menu())
        return
    await state.set_state(UserPhase.IDLE)
    await state.update_data(plan=None, index=0, answers=None)
    await message.answer("🛑 Test abandoned. Use /test to start again.", reply_markup=main_menu())


@router.message(Command("test"))
async def cmd_test(message: Message, state: FSMContext) -> None:
    await _begin(message, state, (1, 2, 3))


async def _begin(message: Message, state: FSMContext, parts: tuple[int, ...]) -> None:
    plan = _build_plan(parts)
    await state.set_state(UserPhase.EXAM_IN_PROGRESS)
    await state.update_data(plan=json.dumps(plan), index=0, answers=json.dumps([]))
    label = "full mock test" if len(parts) == 3 else f"Part {parts[0]} practice"
    await message.answer(
        f"🎤 <b>Starting your {label}</b>\n\n"
        f"{len(plan)} question(s). Answer naturally — text or voice.\n"
        "Use 🛑 <b>End test</b> at any time to stop.",
    )
    await _ask_next(message, state)


# --------------------------------------------------------------------------- #
# Answer collection
# --------------------------------------------------------------------------- #

@router.message(StateFilter(UserPhase.EXAM_IN_PROGRESS), F.voice | F.audio)
async def handle_voice_answer(message: Message, bot: Bot, state: FSMContext) -> None:
    """Transcribe a spoken answer, then record it like a text answer."""
    media = message.voice or message.audio
    status = await message.answer("🎧 Listening to your answer…")
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    try:
        file = await bot.get_file(media.file_id)
        buffer = await bot.download_file(file.file_path)
        audio = buffer.read()
    except Exception as exc:  # noqa: BLE001
        logger.exception("ielts audio download failed")
        await status.edit_text(f"⚠️ I couldn't download that recording.\n<code>{exc}</code>")
        return

    mime = getattr(media, "mime_type", None) or "audio/ogg"
    try:
        transcript = await gateway.ask(
            task_type="summarization",
            messages=[{
                "role": "user",
                "content": [
                    text_part(
                        "Transcribe this IELTS candidate's spoken answer verbatim. Preserve hesitations "
                        "(um, er), false starts and self-corrections exactly as spoken — they are needed "
                        "for fluency assessment. After the transcript add one line starting with "
                        "'PRONUNCIATION NOTES:' describing clarity, word stress, intonation and any "
                        "sounds that impede understanding. Output nothing else."
                    ),
                    inline_data_part(mime, audio),
                ],
            }],
            system=SYSTEM_PROMPT,
            max_tokens=2000,
            temperature=0.2,
            user_id=message.from_user.id,
        )
    except GatewayError as exc:
        await status.edit_text(exc.user_message())
        return
    except Exception:  # noqa: BLE001 - last resort: never strand the user
        logger.exception("unexpected handler failure")
        await status.edit_text(UNEXPECTED_ERROR)
        return

    if not transcript.strip():
        await status.edit_text("🤔 I couldn't hear any speech. Please try recording again.")
        return

    await status.delete()
    await _record_answer(message, state, transcript, spoken=True)


@router.message(StateFilter(UserPhase.EXAM_IN_PROGRESS), F.text & ~F.text.startswith("/"))
async def handle_text_answer(message: Message, state: FSMContext) -> None:
    await _record_answer(message, state, message.text, spoken=False)


async def _record_answer(message: Message, state: FSMContext, answer: str, spoken: bool) -> None:
    """Persist one answer and advance the plan."""
    data = await state.get_data()
    plan = json.loads(data.get("plan") or "[]")
    index = int(data.get("index", 0))
    answers = json.loads(data.get("answers") or "[]")

    if index >= len(plan):
        await _evaluate(message, state)
        return

    step = plan[index]
    answers.append({
        "part": step["part"],
        "question": step.get("plain") or step["question"],
        "answer": answer.strip(),
        "spoken": spoken,
    })
    await state.update_data(answers=json.dumps(answers), index=index + 1)

    if index + 1 < len(plan):
        await message.answer("✅ Noted. Next question…")
    await _ask_next(message, state)


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #

EVALUATION_PROMPT = """Evaluate this IELTS Speaking mock test against the official public band descriptors.

Produce EXACTLY this structure:

<b>🎯 OVERALL BAND: X.X</b>

<b>1. Fluency &amp; Coherence — X.X</b>
Two or three sentences justifying the score, quoting the candidate.

<b>2. Lexical Resource — X.X</b>
Two or three sentences, quoting specific vocabulary choices.

<b>3. Grammatical Range &amp; Accuracy — X.X</b>
Two or three sentences, naming the structures attempted.

<b>4. Pronunciation — X.X</b>
Two or three sentences. If answers were typed rather than spoken, state that pronunciation was estimated from written fluency and mark it provisional.

<b>✏️ TOP CORRECTIONS</b>
Up to 6 lines, each formatted:
❌ "exact candidate words" → ✅ "corrected version" (what the rule is)

<b>📈 UPGRADE YOUR VOCABULARY</b>
5 lines: a word the candidate used → a band 7-9 alternative, with the phrase it fits.

<b>🎯 TO REACH THE NEXT BAND</b>
Three concrete, actionable practice instructions specific to this candidate's weaknesses.

Scoring rules:
- Band scores are 1.0 to 9.0 in 0.5 increments only.
- The overall band is the average of the four criteria, rounded to the nearest half band.
- Be strict and realistic. A typical intermediate learner scores 5.5-6.5. Do not inflate.
- Quote the candidate's actual words in every justification."""


async def _evaluate(message: Message, state: FSMContext) -> None:
    """Score the completed test and emit the report."""
    data = await state.get_data()
    answers = json.loads(data.get("answers") or "[]")

    await state.set_state(UserPhase.EVALUATION)

    if not answers:
        await state.set_state(UserPhase.IDLE)
        await message.answer("No answers were recorded, so there's nothing to score.", reply_markup=main_menu())
        return

    status = await message.answer("📝 <b>Marking your test…</b>\n<i>Applying the four official criteria.</i>")

    spoken_count = sum(1 for answer in answers if answer.get("spoken"))
    transcript = "\n\n".join(
        f"[Part {item['part']}] EXAMINER: {item['question']}\n"
        f"CANDIDATE ({'spoken' if item.get('spoken') else 'typed'}): {item['answer']}"
        for item in answers
    )
    delivery = (
        f"{spoken_count} of {len(answers)} answers were spoken aloud; "
        "pronunciation notes from the transcription are included inline."
        if spoken_count
        else "All answers were typed, so pronunciation must be marked provisional."
    )

    try:
        report = await gateway.ask(
            task_type="general",
            messages=[{
                "role": "user",
                "content": f"{EVALUATION_PROMPT}\n\nDelivery: {delivery}\n\n--- TRANSCRIPT ---\n{transcript}",
            }],
            system=SYSTEM_PROMPT,
            max_tokens=4000,
            temperature=0.25,
            user_id=message.from_user.id,
        )
    except GatewayError as exc:
        await state.set_state(UserPhase.IDLE)
        await status.edit_text(exc.user_message(), reply_markup=main_menu())
        return
    except Exception:  # noqa: BLE001 - last resort: never strand the user
        logger.exception("unexpected handler failure")
        await status.edit_text(UNEXPECTED_ERROR)
        return

    await state.update_data(last_report=report[:6000], plan=None, index=0)
    await state.set_state(UserPhase.IDLE)
    await status.delete()
    await send_long_message(message, clean_model_output(report))
    await message.answer(
        "📚 That's your assessment. What next?",
        reply_markup=evaluation_menu(),
    )


# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #

@router.callback_query(F.data == f"{CB_PREFIX}:start")
async def callback_start(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    await _begin(query.message, state, (1, 2, 3))


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:part:"))
async def callback_part(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    try:
        part = int(query.data.rsplit(":", 1)[1])
    except ValueError:
        return
    await _begin(query.message, state, (part,))


@router.callback_query(F.data == f"{CB_PREFIX}:bands")
async def callback_bands(query: CallbackQuery) -> None:
    await query.answer()
    await query.message.answer(BAND_DESCRIPTORS)


@router.callback_query(F.data == f"{CB_PREFIX}:skip")
async def callback_skip(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer("Skipped.")
    data = await state.get_data()
    plan = json.loads(data.get("plan") or "[]")
    index = int(data.get("index", 0))
    answers = json.loads(data.get("answers") or "[]")
    if index < len(plan):
        answers.append({
            "part": plan[index]["part"],
            "question": plan[index].get("plain") or plan[index]["question"],
            "answer": "(skipped - no answer given)",
            "spoken": False,
        })
        await state.update_data(answers=json.dumps(answers), index=index + 1)
    await _ask_next(query.message, state)


@router.callback_query(F.data == f"{CB_PREFIX}:stop")
async def callback_stop(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer("Test ended.")
    data = await state.get_data()
    answers = json.loads(data.get("answers") or "[]")
    if answers:
        await _evaluate(query.message, state)
    else:
        await state.set_state(UserPhase.IDLE)
        await query.message.answer("🛑 Test ended before any answers.", reply_markup=main_menu())


@router.callback_query(F.data.in_({f"{CB_PREFIX}:improve", f"{CB_PREFIX}:model"}))
async def callback_followup(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    data = await state.get_data()
    report = data.get("last_report")
    if not report:
        await query.message.answer("I don't have a recent report. Use /test to take a mock test first.")
        return

    if query.data.endswith("improve"):
        instruction = (
            "Based on the assessment above, write a focused 14-day improvement plan for this candidate. "
            "Give a specific daily drill (15-20 minutes), the weakness it targets and how to self-check progress. "
            "Be concrete: name structures, vocabulary sets and speaking exercises."
        )
        label = "📈 Building your improvement plan…"
    else:
        instruction = (
            "For each question in the assessment above, write a band 8-9 model answer of appropriate length "
            "for its part of the test. After each model answer add a short line of 'Why this scores highly:' "
            "pointing at the specific features an examiner rewards."
        )
        label = "✍️ Writing model answers…"

    status = await query.message.answer(label)
    try:
        answer = await gateway.ask(
            task_type="general",
            messages=[{"role": "user", "content": f"--- ASSESSMENT ---\n{report}\n\n{instruction}"}],
            system=SYSTEM_PROMPT,
            max_tokens=3500,
            temperature=0.35,
            user_id=query.from_user.id,
        )
    except GatewayError as exc:
        await status.edit_text(exc.user_message())
        return
    except Exception:  # noqa: BLE001 - last resort: never strand the user
        logger.exception("unexpected handler failure")
        await status.edit_text(UNEXPECTED_ERROR)
        return

    await status.delete()
    await send_long_message(query.message, clean_model_output(answer))


# --------------------------------------------------------------------------- #
# Idle chatter
# --------------------------------------------------------------------------- #

@router.message(StateFilter(None, UserPhase.IDLE, UserPhase.EVALUATION), F.text & ~F.text.startswith("/"))
async def handle_idle(message: Message, history: HistoryManager) -> None:
    """Answer general IELTS questions when no test is running."""
    thinking = await message.answer("💭 Thinking…")
    try:
        answer = await gateway.ask(
            task_type="general",
            messages=await history.build_messages(
                message.from_user.id,
                (
                    f"{message.text}\n\n(Answer as an IELTS speaking coach. If the user seems ready to "
                    "practise, remind them they can run a full mock test with /test.)"
                ),
            ),
            system=SYSTEM_PROMPT,
            max_tokens=1800,
            temperature=0.4,
            user_id=message.from_user.id,
        )
    except GatewayError as exc:
        await thinking.edit_text(exc.user_message())
        return
    except Exception:  # noqa: BLE001 - last resort: never strand the user
        logger.exception("unexpected handler failure")
        await thinking.edit_text(UNEXPECTED_ERROR)
        return

    await history.add_exchange(message.from_user.id, message.text, answer)
    await thinking.delete()
    await send_long_message(message, clean_model_output(answer))


__all__ = ["router", "UserPhase", "CRITERIA"]
