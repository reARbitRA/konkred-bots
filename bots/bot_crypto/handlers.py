"""Alpha Scanner - market sentiment, key drivers, whale activity and risk.

Commands: ``/scan <ticker>``, ``/sentiment <topic>``, ``/news``.

Requests are routed with ``taskType="classification"``, whose candidate chain
leads with the turbo-latency Cerebras/Groq slots, and sentiment scans use the
gateway's fusion mode so the score is a consensus across independent model
families rather than the opinion of a single small model.

Every response carries an explicit not-financial-advice disclaimer, and the
prompt forbids inventing live prices the model cannot actually observe.
"""

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from shared.gateway_client import GatewayError, gateway
from shared.history import HistoryManager
from shared.utils import clean_model_output, escape_html, send_long_message

from .keyboards import CB_PREFIX, main_menu, news_menu, report_menu

logger = logging.getLogger(__name__)
router = Router(name="bot_crypto")

TICKER_PATTERN = re.compile(r"^[A-Za-z0-9]{1,12}$")

SYSTEM_PROMPT = (
    "You are Konkred Alpha, a disciplined crypto market analyst. You are explicitly aware that you "
    "have no live market data feed: you reason from known fundamentals, tokenomics, historical "
    "behaviour and structural factors, and you say so plainly. You never fabricate a current price, "
    "a 24h change or a specific on-chain transaction. You are risk-first and you never tell anyone "
    "to buy or sell."
)

DISCLAIMER = (
    "\n\n⚠️ <i>Not financial advice. This is an AI-generated structural analysis produced without a "
    "live price feed — verify every figure against a real exchange or on-chain explorer before acting.</i>"
)

SCAN_PROMPT = """Produce a structured intelligence report on the crypto asset: {subject}

Use EXACTLY these sections:

<b>📊 SENTIMENT SCORE</b>
One line: `Bullish | Bearish | Neutral — NN/100 (confidence: low|medium|high)`
where 1-35 is bearish, 36-64 neutral and 65-100 bullish. Then one sentence explaining the number.

<b>🔑 KEY DRIVERS</b>
4-6 bullets covering the structural forces that actually move this asset: tokenomics and emission
schedule, adoption and real usage, competitive position, regulatory exposure, technical roadmap and
macro sensitivity. Mark each bullet as (bullish), (bearish) or (neutral).

<b>🐋 WHALE & FLOW DYNAMICS</b>
Explain the typical holder structure of this asset: concentration, known large-holder categories,
unlock or vesting schedules, exchange-flow behaviour and what a large move would signal.
State explicitly that you cannot observe live on-chain transactions, and tell the reader which
explorer or analytics tool would show them the current picture.

<b>⚡ CATALYSTS TO WATCH</b>
3-4 specific, checkable upcoming or recurring events - upgrades, unlocks, regulatory decisions,
listing dynamics - and what each would imply.

<b>🛡 RISK WARNING</b>
The three most serious downside risks for this specific asset, each with the mechanism by which it
would hurt a holder. Include at least one risk that is unique to this asset rather than generic
crypto volatility.

Rules: never state a current price, market cap or 24h change as fact. If the asset is obscure or you
are not confident it exists, say so directly instead of inventing details."""

SENTIMENT_PROMPT = """Analyse market sentiment around this topic or narrative: {subject}

Use EXACTLY these sections:

<b>📊 SENTIMENT SCORE</b>
`Bullish | Bearish | Neutral — NN/100 (confidence: low|medium|high)` plus one sentence of justification.

<b>🧭 THE NARRATIVE</b>
What the market believes about this topic right now, and how that belief formed.

<b>🔑 KEY DRIVERS</b>
4-6 bullets on what is genuinely driving this narrative, each marked (bullish), (bearish) or (neutral).

<b>🎭 WHO IS ON EACH SIDE</b>
Which market participants benefit from this narrative and which are positioned against it.

<b>🚩 CONTRARIAN VIEW</b>
The strongest honest argument against the consensus, and what evidence would prove it right.

<b>🛡 RISK WARNING</b>
Three concrete risks of trading this narrative, including the risk that it is already priced in.

Rules: no invented prices or fabricated data points. Be explicit about uncertainty."""

NEWS_PROMPT = """Produce a structural market briefing for the crypto market as a whole.

You do NOT have live data, so frame everything as durable structure rather than today's headlines,
and say so in the first line.

Use EXACTLY these sections:

<b>🌐 MARKET STRUCTURE</b>
The regime the market is in structurally: liquidity conditions, BTC dominance dynamics, the
relationship between majors and alts, and how ETF/institutional access changed the mechanics.

<b>📌 THE FIVE THEMES THAT MATTER</b>
Five numbered themes currently shaping crypto (for example: regulatory clarity, real-world assets,
restaking, L2 fragmentation, stablecoin policy, AI-crypto convergence). For each: what it is, who it
benefits and the single signal that tells you it is playing out.

<b>📅 RECURRING CATALYSTS</b>
The calendar items that reliably move this market - macro prints, unlock cliffs, halving cycles,
regulatory deadlines - and how to position attention around them.

<b>⚖️ BULL VS BEAR</b>
The strongest current case on each side, in three bullets each.

<b>🛡 RISK WARNING</b>
The three structural risks most likely to be underpriced by retail participants right now.

End with one line telling the reader exactly which live sources to check for today's actual data."""

WELCOME = """📊 <b>Alpha Scanner</b>

Structural market intelligence — sentiment, drivers, flow dynamics and risk.

<b>Commands</b>
/scan <code>BTC</code> — full intelligence report on an asset
/sentiment <code>ETF approval</code> — sentiment read on a topic or narrative
/news — structural market briefing
/help — detailed usage

<b>Every report contains</b>
📊 Sentiment score (Bullish/Bearish/Neutral, 1-100)
🔑 Key drivers, tagged by direction
🐋 Whale &amp; flow dynamics
⚡ Catalysts to watch
🛡 Risk warning

⚠️ <i>No live price feed. This is structural analysis, not trading signals, and never financial advice.</i>"""

HELP = """📊 <b>Alpha Scanner — help</b>

<b>/scan &lt;ticker&gt;</b>
<code>/scan BTC</code> · <code>/scan SOL</code> · <code>/scan ARB</code>
A full report: sentiment score, key drivers, holder/flow structure, catalysts and risks.
Scans use multi-model consensus, so the score reflects agreement across independent models.

<b>/sentiment &lt;topic&gt;</b>
<code>/sentiment spot ETH ETF</code> · <code>/sentiment restaking narrative</code>
A sentiment read on a narrative rather than an asset, including the contrarian case.

<b>/news</b>
A structural briefing on the market: regime, the five themes that matter, recurring catalysts
and the bull/bear cases.

<b>What I cannot do</b>
I have no live market data connection. I will never quote a current price, market cap or 24h change
as fact, and I will not tell you to buy or sell anything. For live numbers use a real exchange,
CoinGecko, or an on-chain explorer — I'll tell you which one to check.

<b>Follow-ups on any report</b>
⚖️ Bull vs Bear · 🛡 Risk checklist · 📚 Explain simply · 🔄 Rescan"""


def _clean_subject(raw: str, limit: int = 120) -> str:
    """Normalise user input (strip $ prefixes, collapse whitespace)."""
    value = " ".join(str(raw or "").split())
    return value.lstrip("$").strip()[:limit]


async def _report(
    message: Message,
    prompt: str,
    subject: str,
    user_id: int,
    history: HistoryManager,
    keyboard=None,
    fusion: bool = False,
    status_text: str = "🔍 Analysing…",
) -> None:
    """Shared request/response path for every report type."""
    status = await message.answer(status_text)
    try:
        report = await gateway.ask(
            task_type="classification",
            messages=[{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
            max_tokens=3000,
            temperature=0.35,
            user_id=user_id,
            fusion=fusion,
        )
    except GatewayError as exc:
        await status.edit_text(exc.user_message(), reply_markup=main_menu())
        return

    await history.add_exchange(user_id, f"[report: {subject}]", report)
    await status.delete()
    await send_long_message(message, clean_model_output(report) + DISCLAIMER)
    if keyboard is not None:
        await message.answer("What next?", reply_markup=keyboard)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(WELCOME, reply_markup=main_menu())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("clear"))
async def cmd_clear(message: Message, history: HistoryManager) -> None:
    await history.clear(message.from_user.id)
    await message.answer("🧹 Cleared.", reply_markup=main_menu())


@router.message(Command("scan"))
async def cmd_scan(message: Message, command: CommandObject, history: HistoryManager) -> None:
    subject = _clean_subject(command.args or "")
    if not subject:
        await message.answer(
            "Usage: <code>/scan BTC</code>\n\nOr pick one below:",
            reply_markup=main_menu(),
        )
        return
    if not TICKER_PATTERN.match(subject):
        # Not ticker-shaped: treat it as a topic so the user still gets an answer.
        await cmd_sentiment_impl(message, subject, history)
        return

    ticker = subject.upper()
    await _report(
        message,
        SCAN_PROMPT.format(subject=ticker),
        ticker,
        message.from_user.id,
        history,
        keyboard=report_menu(ticker),
        fusion=True,
        status_text=f"🔍 <b>Scanning {escape_html(ticker)}…</b>\n<i>Cross-checking across multiple models.</i>",
    )


@router.message(Command("sentiment"))
async def cmd_sentiment(message: Message, command: CommandObject, history: HistoryManager) -> None:
    subject = _clean_subject(command.args or "", limit=200)
    if not subject:
        await message.answer(
            "Usage: <code>/sentiment spot ETH ETF</code>\n\nGive me a narrative, event or theme to read."
        )
        return
    await cmd_sentiment_impl(message, subject, history)


async def cmd_sentiment_impl(message: Message, subject: str, history: HistoryManager) -> None:
    await _report(
        message,
        SENTIMENT_PROMPT.format(subject=subject),
        subject,
        message.from_user.id,
        history,
        keyboard=main_menu(),
        fusion=True,
        status_text=f"🧭 <b>Reading sentiment on {escape_html(subject)}…</b>",
    )


@router.message(Command("news"))
async def cmd_news(message: Message, history: HistoryManager) -> None:
    await _report(
        message,
        NEWS_PROMPT,
        "market briefing",
        message.from_user.id,
        history,
        keyboard=news_menu(),
        status_text="📰 <b>Building the market briefing…</b>",
    )


# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #

@router.callback_query(F.data == f"{CB_PREFIX}:menu")
async def callback_menu(query: CallbackQuery) -> None:
    await query.answer()
    await query.message.answer("📊 What would you like to scan?", reply_markup=main_menu())


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:scan:"))
async def callback_scan(query: CallbackQuery, history: HistoryManager) -> None:
    await query.answer()
    ticker = _clean_subject(query.data.rsplit(":", 1)[1]).upper()
    if not ticker:
        return
    await _report(
        query.message,
        SCAN_PROMPT.format(subject=ticker),
        ticker,
        query.from_user.id,
        history,
        keyboard=report_menu(ticker),
        fusion=True,
        status_text=f"🔍 <b>Scanning {escape_html(ticker)}…</b>",
    )


@router.callback_query(F.data == f"{CB_PREFIX}:news")
async def callback_news(query: CallbackQuery, history: HistoryManager) -> None:
    await query.answer()
    await _report(
        query.message, NEWS_PROMPT, "market briefing", query.from_user.id, history,
        keyboard=news_menu(), status_text="📰 <b>Building the market briefing…</b>",
    )


@router.callback_query(F.data == f"{CB_PREFIX}:feargreed")
async def callback_feargreed(query: CallbackQuery, history: HistoryManager) -> None:
    await query.answer()
    prompt = (
        "Explain the Crypto Fear & Greed Index: what the five zones mean, exactly which inputs feed it "
        "(volatility, momentum/volume, social media, dominance, trends) and their weights, how "
        "contrarians actually use it, and its three main weaknesses as a signal. "
        "State clearly that you cannot read today's live value and name where to check it."
    )
    await _report(
        query.message, prompt, "fear & greed", query.from_user.id, history,
        keyboard=main_menu(), status_text="😱 <b>Explaining the Fear & Greed Index…</b>",
    )


FOLLOWUPS = {
    "debate": (
        "Stage a rigorous bull-versus-bear debate on {subject}. Three rounds: opening cases, rebuttals, "
        "closing arguments. Each side must cite structural reasoning, not price targets. "
        "Finish with a neutral verdict naming the single piece of evidence that would settle it."
    ),
    "risk": (
        "Write a practical risk-management checklist for someone considering exposure to {subject}. Cover: "
        "position sizing relative to portfolio, the specific failure modes of this asset, custody and "
        "counterparty risk, liquidity and exit considerations, tax record-keeping, and the red flags that "
        "should trigger a re-evaluation. Format as an actionable checklist, not prose."
    ),
    "eli5": (
        "Explain {subject} to an intelligent beginner with no crypto background. Cover what it actually does, "
        "what problem it solves, how it makes or loses value, and what could go wrong. "
        "Use a concrete real-world analogy. Avoid jargon; where a term is unavoidable, define it inline."
    ),
}


@router.callback_query(F.data.regexp(rf"^{CB_PREFIX}:(debate|risk|eli5):"))
async def callback_followup(query: CallbackQuery, history: HistoryManager) -> None:
    await query.answer()
    try:
        _, action, subject = query.data.split(":", 2)
    except ValueError:
        return
    subject = _clean_subject(subject).upper()
    template = FOLLOWUPS.get(action)
    if not template or not subject:
        return

    labels = {
        "debate": f"⚖️ <b>Staging the {escape_html(subject)} debate…</b>",
        "risk": f"🛡 <b>Building the {escape_html(subject)} risk checklist…</b>",
        "eli5": f"📚 <b>Explaining {escape_html(subject)} simply…</b>",
    }
    await _report(
        query.message,
        template.format(subject=subject),
        f"{action}:{subject}",
        query.from_user.id,
        history,
        keyboard=report_menu(subject),
        status_text=labels[action],
    )


# --------------------------------------------------------------------------- #
# Free-form input
# --------------------------------------------------------------------------- #

@router.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message, history: HistoryManager) -> None:
    """A bare ticker scans it; anything longer is treated as a sentiment topic."""
    subject = _clean_subject(message.text, limit=200)
    if not subject:
        return

    if TICKER_PATTERN.match(subject) and len(subject) <= 6:
        ticker = subject.upper()
        await _report(
            message,
            SCAN_PROMPT.format(subject=ticker),
            ticker,
            message.from_user.id,
            history,
            keyboard=report_menu(ticker),
            fusion=True,
            status_text=f"🔍 <b>Scanning {escape_html(ticker)}…</b>",
        )
        return

    await cmd_sentiment_impl(message, subject, history)


__all__ = ["router"]
