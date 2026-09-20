"""Viral Hook Architect - short-form scripts, hooks and SEO captions.

FSM flow:

    IDLE ──/create──▶ AWAITING_TOPIC ──text──▶ AWAITING_PLATFORM ──button──▶
    AWAITING_TONE ──button──▶ GENERATING ──▶ IDLE

Generation is routed through ``taskType="code-generation"`` because that lane
prioritises models that follow rigid output structures, which is exactly what a
beat-by-beat script with timestamps requires.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from shared.gateway_client import GatewayError, gateway
from shared.history import HistoryManager
from shared.payments import PaymentManager
from shared.utils import (UNEXPECTED_ERROR, clean_model_output, escape_html,
                          send_long_message, truncate)

from .keyboards import CB_PREFIX, PLATFORMS, TONES, main_menu, platform_menu, result_menu, tone_menu

logger = logging.getLogger(__name__)
router = Router(name="bot_content")


class ContentFlow(StatesGroup):
    """Steps of the guided script-creation flow."""

    AWAITING_TOPIC = State()
    AWAITING_PLATFORM = State()
    AWAITING_TONE = State()


SYSTEM_PROMPT = (
    "You are Konkred Viral, a short-form content strategist who has scripted videos with over "
    "500 million cumulative views. You think in retention curves: every line either earns the "
    "next second of attention or gets cut. You write specific, concrete copy - never generic "
    "filler like 'engaging content' or 'amazing tips'."
)

PLATFORM_BRIEFS = {
    "reels": (
        "Instagram Reels: 15-30s optimal, 9:16, sound-on culture but many watch muted so on-screen "
        "text is essential. Saves and shares are the ranking signal. Hook must land in 1.5 seconds."
    ),
    "tiktok": (
        "TikTok: 21-34s optimal for completion, native-feeling and unpolished beats over-produced. "
        "Watch-time and rewatches drive the For You page. Trend-aware audio helps. Hook must land in 1 second."
    ),
    "shorts": (
        "YouTube Shorts: up to 60s, viewers accept slightly more depth. Title text and thumbnail frame "
        "matter, and the loop is heavily rewarded. Swipe-away rate in the first 3 seconds is decisive."
    ),
}

GENERATION_PROMPT = """Create a complete short-form video package.

TOPIC / NICHE: {topic}
PLATFORM: {platform_label}
PLATFORM BRIEF: {platform_brief}
TONE: {tone_label}

Produce EXACTLY these four sections, in this order:

<b>🪝 SECTION 1 — THREE HOOK VARIATIONS</b>
Give three hooks, each labelled with its psychological mechanism and its intended spoken delivery:
1. <b>Psychological</b> — targets identity, loss aversion or social proof.
2. <b>Curiosity-Gap</b> — opens an information gap the viewer must close.
3. <b>Contrarian</b> — attacks a belief the audience holds.
Each hook must be under 12 words, speakable in under 2 seconds, and specific to the topic.

<b>🎬 SECTION 2 — 30-SECOND BEAT-BY-BEAT SCRIPT</b>
A table-style breakdown with one line per beat, in this exact format:
<code>[0:00-0:03] VISUAL: what is on screen | AUDIO: exact words spoken | TEXT: on-screen overlay</code>
Cover 0:00 to 0:30 with no gaps. Include a pattern interrupt around 0:07 and a loop or CTA at the end.
The AUDIO field must contain the literal words to say, not a description.

<b>📝 SECTION 3 — SEO CAPTION</b>
A caption of 2-4 sentences that front-loads the keyword, adds context the video omits and ends with an
engagement question. Then a line of exactly 12 hashtags mixing three tiers: 3 broad (1M+ posts),
6 mid-tier (100k-1M) and 3 niche (under 100k). Label the tiers.

<b>📊 SECTION 4 — PRODUCTION NOTES</b>
Four bullets: best posting time, the single retention risk in this script and how to fix it,
one B-roll shot that is worth the effort, and the metric to watch to judge whether it worked.

Rules: no placeholders, no brackets to fill in, no generic advice. Every line must be usable as written."""

WELCOME = """🎬 <b>Viral Hook Architect</b>

I turn a topic into a complete, shootable short-form package.

<b>You get</b>
🪝 3 hook variations — psychological, curiosity-gap and contrarian
🎬 A 30-second beat-by-beat script with visual, audio and on-screen text
📝 An SEO caption plus 12 tiered hashtags
📊 Production notes — timing, retention risks and the metric to watch

<b>Platforms</b>: Instagram Reels · TikTok · YouTube Shorts

<b>Start</b>: /create — or just send me your topic.

<b>Commands</b>: /create · /formulas · /cancel · /help"""

HELP = """🎬 <b>Viral Hook Architect — help</b>

<b>How it works</b>
1. /create (or just send a topic)
2. Tell me the niche or topic — be specific: "cold plunge for desk workers" beats "health"
3. Pick the platform — I adapt pacing and length to its algorithm
4. Pick a tone — educational, entertaining, inspirational or contrarian

<b>Then you can</b>
🔄 Regenerate for a fresh angle
🪝 Ask for more hook variations
🎞 Get a shot list you can hand to a camera operator
📅 Expand it into a 7-day content series

<b>Commands</b>
/create — start the guided flow
/formulas — the hook formula cheat-sheet
/cancel — abandon the current flow
/clear — wipe my memory of your sessions"""

FORMULAS = """💡 <b>Hook formulas that reliably retain</b>

<b>1. The Contradiction</b>
"Everything you know about X is wrong."
Works because it threatens existing knowledge — the viewer stays to defend it.

<b>2. The Curiosity Gap</b>
"I tried X for 30 days. Day 12 changed everything."
Opens a loop the brain wants closed.

<b>3. The Stakes</b>
"This mistake cost me $40,000."
Concrete loss is more gripping than abstract gain.

<b>4. The Callout</b>
"If you're a [specific person], stop scrolling."
Self-identification beats broad appeal every time.

<b>5. The Impossible Claim</b>
"You can learn this in 40 seconds. Here's proof."
Promise plus immediate evidence.

<b>6. The Insider Reveal</b>
"Here's what [industry] doesn't advertise."
Exclusive access framing.

<b>7. The Negative Result</b>
"Don't do X until you've seen this."
Loss aversion outperforms gain framing roughly 2:1.

<b>Universal rules</b>
• Land the hook in under 2 seconds
• Never open with a greeting or your name
• Make the first frame visually unusual
• Speak a number — specificity is credibility"""


def _describe(data: dict) -> str:
    platform = PLATFORMS.get(data.get("platform", ""), "—")
    tone = TONES.get(data.get("tone", ""), "—")
    return f"<b>Topic:</b> {escape_html(truncate(data.get('topic', ''), 90))}\n<b>Platform:</b> {platform}\n<b>Tone:</b> {tone}"


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(WELCOME, reply_markup=main_menu())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("formulas"))
async def cmd_formulas(message: Message) -> None:
    await message.answer(FORMULAS)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Cancelled.", reply_markup=main_menu())


@router.message(Command("clear"))
async def cmd_clear(message: Message, state: FSMContext, history: HistoryManager) -> None:
    await state.clear()
    await history.clear(message.from_user.id)
    await message.answer("🧹 Cleared. Use /create to start fresh.", reply_markup=main_menu())


@router.message(Command("create"))
async def cmd_create(message: Message, state: FSMContext) -> None:
    await state.set_state(ContentFlow.AWAITING_TOPIC)
    await message.answer(
        "🎯 <b>What's your topic or niche?</b>\n\n"
        "Be specific — the narrower the topic, the sharper the hooks.\n\n"
        "<i>Good:</i> \"protein myths for vegetarian lifters\"\n"
        "<i>Too broad:</i> \"fitness\""
    )


# --------------------------------------------------------------------------- #
# FSM steps
# --------------------------------------------------------------------------- #

@router.message(StateFilter(ContentFlow.AWAITING_TOPIC), F.text & ~F.text.startswith("/"))
async def step_topic(message: Message, state: FSMContext) -> None:
    topic = message.text.strip()
    if len(topic) < 3:
        await message.answer("That's a little short — give me a few more words to work with.")
        return
    await state.update_data(topic=topic[:500])
    await state.set_state(ContentFlow.AWAITING_PLATFORM)
    await message.answer(
        f"📌 Topic: <b>{escape_html(truncate(topic, 100))}</b>\n\n"
        "<b>Which platform?</b>\nI adapt pacing, length and hook timing to each algorithm.",
        reply_markup=platform_menu(),
    )


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:platform:"))
async def step_platform(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    platform = query.data.rsplit(":", 1)[1]
    if platform not in PLATFORMS:
        # Stale keyboard from an older deployment: never leave the user hanging.
        await query.message.answer(
            "That platform button is out of date. Pick one of these:",
            reply_markup=platform_menu(),
        )
        return
    await state.update_data(platform=platform)
    await state.set_state(ContentFlow.AWAITING_TONE)
    await query.message.edit_text(
        f"✅ Platform: <b>{PLATFORMS[platform]}</b>\n\n<b>What tone should I write in?</b>",
        reply_markup=tone_menu(),
    )


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:tone:"))
async def step_tone(
    query: CallbackQuery,
    state: FSMContext,
    history: HistoryManager,
    payments: PaymentManager,
) -> None:
    await query.answer()
    tone = query.data.rsplit(":", 1)[1]
    if tone not in TONES:
        # Stale keyboard from an older deployment: never leave the user hanging.
        await query.message.answer(
            "That tone button is out of date. Pick one of these:",
            reply_markup=tone_menu(),
        )
        return
    await state.update_data(tone=tone)
    await _generate(query.message, state, history, payments, query.from_user.id)


@router.callback_query(F.data == f"{CB_PREFIX}:new")
async def callback_new(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    await state.set_state(ContentFlow.AWAITING_TOPIC)
    await query.message.answer("🎯 <b>What's your topic or niche?</b>\n\nBe as specific as you can.")


@router.callback_query(F.data == f"{CB_PREFIX}:cancel")
async def callback_cancel(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer("Cancelled.")
    await state.clear()
    await query.message.edit_text("❌ Cancelled.", reply_markup=main_menu())


@router.callback_query(F.data == f"{CB_PREFIX}:formulas")
async def callback_formulas(query: CallbackQuery) -> None:
    await query.answer()
    await query.message.answer(FORMULAS)


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #

async def _generate(
    message: Message,
    state: FSMContext,
    history: HistoryManager,
    payments: PaymentManager,
    user_id: int,
) -> None:
    data = await state.get_data()
    topic = data.get("topic", "")
    platform = data.get("platform", "reels")
    tone = data.get("tone", "educational")

    if not topic:
        await state.set_state(ContentFlow.AWAITING_TOPIC)
        await message.answer("I lost the topic — what should the video be about?")
        return
    if not await payments.require(message, user_id):
        return

    status = await message.answer(
        f"🎬 <b>Writing your script…</b>\n\n{_describe(data)}\n\n<i>Hooks, beats, caption and production notes.</i>"
    )

    prompt = GENERATION_PROMPT.format(
        topic=topic,
        platform_label=PLATFORMS.get(platform, platform),
        platform_brief=PLATFORM_BRIEFS.get(platform, ""),
        tone_label=TONES.get(tone, tone),
    )

    try:
        script = await gateway.ask(
            task_type="code-generation",
            messages=[{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
            max_tokens=4000,
            temperature=0.85,
            user_id=user_id,
        )
    except GatewayError as exc:
        await status.edit_text(exc.user_message(), reply_markup=main_menu())
        return
    except Exception:  # noqa: BLE001 - last resort: never strand the user
        logger.exception("unexpected handler failure")
        await status.edit_text(UNEXPECTED_ERROR)
        return

    await state.update_data(last_script=script[:6000])
    await state.set_state(None)
    await history.add_exchange(user_id, f"[script: {topic} / {platform} / {tone}]", script)

    await status.delete()
    await send_long_message(message, clean_model_output(script))
    await message.answer("🎬 Ready to shoot. What next?", reply_markup=result_menu())


@router.callback_query(F.data == f"{CB_PREFIX}:regen")
async def callback_regen(
    query: CallbackQuery,
    state: FSMContext,
    history: HistoryManager,
    payments: PaymentManager,
) -> None:
    await query.answer("Regenerating…")
    await _generate(query.message, state, history, payments, query.from_user.id)


FOLLOWUP_PROMPTS = {
    "hooks": (
        "Write 10 more hook variations for the same topic and platform as the script above. "
        "Label each with its mechanism (contradiction, curiosity gap, stakes, callout, impossible claim, "
        "insider reveal or negative result) and keep every hook under 12 words. "
        "Then mark the single strongest one and explain in one sentence why it wins."
    ),
    "shotlist": (
        "Turn the script above into a production shot list a camera operator could execute without asking "
        "questions. For each shot give: shot number, timecode, framing (wide/medium/close/insert), camera "
        "movement, lighting note, props needed and the exact line of dialogue it covers. "
        "End with a gear list and an estimated shoot time."
    ),
    "series": (
        "Expand the topic above into a 7-day content series. For each day give: the day number, the specific "
        "angle, the hook line, the single core message and how it connects to the previous and next posts. "
        "Design it so each video makes sense standalone but binge-watching rewards the viewer. "
        "Finish with the series-level goal and the metric that proves it worked."
    ),
}


@router.callback_query(F.data.in_({f"{CB_PREFIX}:{key}" for key in FOLLOWUP_PROMPTS}))
async def callback_followup(
    query: CallbackQuery,
    state: FSMContext,
    payments: PaymentManager,
) -> None:
    await query.answer()
    action = query.data.split(":", 1)[1]
    data = await state.get_data()
    script = data.get("last_script")
    if not script:
        await query.message.answer("I don't have a recent script. Use /create to make one.")
        return
    if not await payments.require(query.message, query.from_user.id):
        return

    labels = {"hooks": "🪝 Writing more hooks…", "shotlist": "🎞 Building the shot list…", "series": "📅 Planning the series…"}
    status = await query.message.answer(labels[action])

    try:
        answer = await gateway.ask(
            task_type="code-generation",
            messages=[{"role": "user", "content": f"--- EXISTING SCRIPT ---\n{script}\n\n{FOLLOWUP_PROMPTS[action]}"}],
            system=SYSTEM_PROMPT,
            max_tokens=3500,
            temperature=0.8,
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
    await query.message.answer("What next?", reply_markup=result_menu())


# --------------------------------------------------------------------------- #
# Shortcut: a bare message starts the flow with that text as the topic
# --------------------------------------------------------------------------- #

@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def handle_bare_topic(message: Message, state: FSMContext) -> None:
    await state.update_data(topic=message.text.strip()[:500])
    await state.set_state(ContentFlow.AWAITING_PLATFORM)
    await message.answer(
        f"📌 Topic: <b>{escape_html(truncate(message.text.strip(), 100))}</b>\n\n<b>Which platform?</b>",
        reply_markup=platform_menu(),
    )


__all__ = ["router", "ContentFlow"]
