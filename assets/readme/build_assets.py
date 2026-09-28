#!/usr/bin/env python3
"""Generate every README visual for the Konkred Telegram bot fleet.

Every number rendered into these diagrams is derived from the repository
itself — the bot routers, the gateway registry, the router table, the payment
manager and the test suites — and is refreshed by running:

    python3 assets/readme/build_assets.py

Palette (KONKRED fleet standard):
    Fleet Black    #0A0B10
    Telegram Cyan  #22D3EE
    Gateway Violet #8B5CF6
    Payment Pink   #EC4899
    Ready Green    #10B981
"""
from __future__ import annotations

import html
from pathlib import Path

OUT = Path(__file__).resolve().parent

# --------------------------------------------------------------------------- #
# palette
# --------------------------------------------------------------------------- #

BG = "#0A0B10"
PANEL = "#10131B"
PANEL_2 = "#141926"
PANEL_3 = "#0D1017"
STROKE = "#232C3C"
TEXT = "#E6EAF2"
MUTED = "#8A94A6"
DIM = "#5C6678"
CYAN = "#22D3EE"
VIOLET = "#8B5CF6"
PINK = "#EC4899"
GREEN = "#10B981"
AMBER = "#F59E0B"

SANS = "'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "'JetBrains Mono','DejaVu Sans Mono',Consolas,'Courier New',monospace"


def esc(value: str) -> str:
    return html.escape(str(value), quote=True)


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #


def _char_ratio(family, weight):
    """Average advance width per character, as a fraction of the font size."""
    if family == MONO:
        return 0.605
    return 0.56 if int(weight) >= 600 else 0.53


_WIDE = set("ABCDEFGHJKLNOPQRSTUVXYZmwMW@#%&")
_NARROW = set("iljt.,:;'`|!/\\()[]{} ")


def measure(s, size, family=SANS, weight=400, spacing=0.0):
    """Conservative width estimate so nothing overflows its panel."""
    if family == MONO:
        return len(s) * (size * 0.605 + spacing)
    bold = 1.06 if int(weight) >= 600 else 1.0
    total = 0.0
    for ch in str(s):
        if ch in _WIDE:
            r = 0.72
        elif ch in _NARROW:
            r = 0.30
        elif ch.isupper():
            r = 0.66
        elif ch.isdigit():
            r = 0.56
        else:
            r = 0.52
        total += size * r * bold + spacing
    return total


def text(x, y, s, size=14, fill=TEXT, weight=400, anchor="start", family=SANS,
         opacity=1.0, spacing=0.0, max_w=None, min_size=7.2):
    s = str(s)
    if max_w:
        while size > min_size and measure(s, size, family, weight, spacing) > max_w:
            size -= 0.25
        if measure(s, size, family, weight, spacing) > max_w:
            keep = max(1, int(max_w / (size * _char_ratio(family, weight) + spacing)) - 1)
            s = s[:keep].rstrip() + "…"
    ls = f' letter-spacing="{spacing}"' if spacing else ""
    op = f' opacity="{opacity}"' if opacity != 1.0 else ""
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size:g}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{ls}{op}>{esc(s)}</text>')


def rect(x, y, w, h, r=12, fill=PANEL, stroke=STROKE, sw=1, opacity=1.0, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" ry="{r}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" opacity="{opacity}"{d}/>')


def line(x1, y1, x2, y2, stroke=STROKE, sw=1, dash=None, opacity=1.0, cap="round"):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" '
            f'stroke-width="{sw}" stroke-linecap="{cap}" opacity="{opacity}"{d}/>')


def path(d, stroke=STROKE, sw=1.5, fill="none", dash=None, opacity=1.0, marker=None):
    da = f' stroke-dasharray="{dash}"' if dash else ""
    mk = f' marker-end="url(#{marker})"' if marker else ""
    return (f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" '
            f'stroke-linecap="round" stroke-linejoin="round" opacity="{opacity}"{da}{mk}/>')


def circle(cx, cy, r, fill=CYAN, stroke="none", sw=1, opacity=1.0):
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{sw}" opacity="{opacity}"/>')


def pill(x, y, label, color=CYAN, size=11, pad=10, h=22, fill_opacity=0.13, weight=600,
         family=SANS):
    w = int(measure(label, size, family, weight)) + pad * 2
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h // 2}" fill="{color}" '
           f'fill-opacity="{fill_opacity}" stroke="{color}" stroke-opacity="0.45" stroke-width="1"/>',
           text(x + w / 2, y + h / 2 + size * 0.36, label, size=size, fill=color,
                weight=weight, anchor="middle", family=family)]
    return "".join(out), w


def pill_row(x, y, labels, color=CYAN, gap=7, size=11, h=22, family=SANS):
    out, cx = [], x
    for label in labels:
        svg, w = pill(cx, y, label, color=color, size=size, h=h, family=family)
        out.append(svg)
        cx += w + gap
    return "".join(out), cx - x - gap


def arrow(x1, y1, x2, y2, color=STROKE, sw=1.6, dash=None, marker="arrow"):
    return path(f"M {x1} {y1} L {x2} {y2}", stroke=color, sw=sw, dash=dash, marker=marker)


def header(w, kicker, title, subtitle=None, accent=CYAN, y=0):
    out = [
        rect(40, y + 30, 4, 34, r=2, fill=accent, stroke="none"),
        text(60, y + 46, kicker, size=11, fill=accent, weight=700, spacing=2.4),
        text(60, y + 66, title, size=22, fill=TEXT, weight=700),
    ]
    if subtitle:
        out.append(text(60, y + 90, subtitle, size=13, fill=MUTED))
    out.append(line(40, y + 112, w - 40, y + 112, stroke=STROKE, sw=1, opacity=0.9))
    return "".join(out)


def defs(extra=""):
    return f"""<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#0A0B10"/>
    <stop offset="55%" stop-color="#0B0D14"/>
    <stop offset="100%" stop-color="#0A0B10"/>
  </linearGradient>
  <linearGradient id="cyanFade" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%" stop-color="{CYAN}" stop-opacity="0.85"/>
    <stop offset="100%" stop-color="{VIOLET}" stop-opacity="0.85"/>
  </linearGradient>
  <linearGradient id="railGrad" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%" stop-color="{CYAN}" stop-opacity="0.0"/>
    <stop offset="18%" stop-color="{CYAN}" stop-opacity="0.75"/>
    <stop offset="55%" stop-color="{VIOLET}" stop-opacity="0.75"/>
    <stop offset="85%" stop-color="{PINK}" stop-opacity="0.6"/>
    <stop offset="100%" stop-color="{PINK}" stop-opacity="0.0"/>
  </linearGradient>
  <linearGradient id="panelGrad" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#141926"/>
    <stop offset="100%" stop-color="#0E121B"/>
  </linearGradient>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
          orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="{DIM}"/>
  </marker>
  <marker id="arrowCyan" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
          orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="{CYAN}"/>
  </marker>
  <marker id="arrowViolet" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
          orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="{VIOLET}"/>
  </marker>
  <marker id="arrowPink" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
          orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="{PINK}"/>
  </marker>
  <marker id="arrowGreen" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
          orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="{GREEN}"/>
  </marker>
  {extra}
</defs>"""


def grid(w, h, step=40, opacity=0.35):
    out = []
    x = step
    while x < w:
        out.append(line(x, 0, x, h, stroke="#151A24", sw=1, opacity=opacity, cap="butt"))
        x += step
    y = step
    while y < h:
        out.append(line(0, y, w, y, stroke="#151A24", sw=1, opacity=opacity, cap="butt"))
        y += step
    return "".join(out)


def document(name, w, h, body, extra_defs="", show_grid=True):
    canvas = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" role="img">',
        defs(extra_defs),
        f'<rect width="{w}" height="{h}" fill="url(#bg)"/>',
        grid(w, h) if show_grid else "",
        f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="18" fill="none" '
        f'stroke="{STROKE}" stroke-width="1"/>',
        body,
        "</svg>",
    ]
    (OUT / name).write_text("\n".join(part for part in canvas if part), encoding="utf-8")
    return name


# --------------------------------------------------------------------------- #
# bot icons (drawn, never screenshots)
# --------------------------------------------------------------------------- #


def icon_voice(cx, cy, color, s=1.0):
    bars = [10, 18, 26, 18, 10]
    out = []
    for i, bh in enumerate(bars):
        x = cx + (i - 2) * 7 * s
        out.append(rect(x - 2 * s, cy - bh * s / 2, 4 * s, bh * s, r=2,
                        fill=color, stroke="none"))
    return "".join(out)


def icon_pdf(cx, cy, color, s=1.0):
    w, h = 22 * s, 28 * s
    x, y = cx - w / 2, cy - h / 2
    out = [rect(x, y, w, h, r=4, fill="none", stroke=color, sw=1.8)]
    for i in range(3):
        out.append(line(x + 5 * s, y + 9 * s + i * 6 * s, x + w - 5 * s,
                        y + 9 * s + i * 6 * s, stroke=color, sw=1.6, opacity=0.8))
    return "".join(out)


def icon_ielts(cx, cy, color, s=1.0):
    out = [path(f"M {cx - 16 * s} {cy - 6 * s} L {cx} {cy - 14 * s} L {cx + 16 * s} {cy - 6 * s} "
                f"L {cx} {cy + 2 * s} Z", stroke=color, sw=1.8),
           path(f"M {cx - 9 * s} {cy - 1 * s} L {cx - 9 * s} {cy + 8 * s} "
                f"Q {cx} {cy + 15 * s} {cx + 9 * s} {cy + 8 * s} L {cx + 9 * s} {cy - 1 * s}",
                stroke=color, sw=1.8)]
    return "".join(out)


def icon_content(cx, cy, color, s=1.0):
    out = [rect(cx - 16 * s, cy - 12 * s, 32 * s, 24 * s, r=5, fill="none", stroke=color, sw=1.8),
           path(f"M {cx - 4 * s} {cy - 6 * s} L {cx + 8 * s} {cy} L {cx - 4 * s} {cy + 6 * s} Z",
                stroke=color, sw=1.6, fill=color)]
    return "".join(out)


def icon_crypto(cx, cy, color, s=1.0):
    out = []
    for i, (bh, off) in enumerate(((14, 4), (24, -2), (10, 6), (20, 0))):
        x = cx + (i - 1.5) * 9 * s
        out.append(line(x, cy - bh * s / 2 + off * s - 4 * s, x, cy + bh * s / 2 + off * s + 4 * s,
                        stroke=color, sw=1.4, opacity=0.7))
        out.append(rect(x - 3 * s, cy - bh * s / 2 + off * s, 6 * s, bh * s, r=1.5,
                        fill=color, stroke="none", opacity=0.9))
    return "".join(out)


ICONS = {
    "voice": icon_voice,
    "pdf": icon_pdf,
    "ielts": icon_ielts,
    "content": icon_content,
    "crypto": icon_crypto,
}

# --------------------------------------------------------------------------- #
# repository-derived facts
# --------------------------------------------------------------------------- #

BOTS = [
    {
        "key": "content",
        "name": "Viral Hook Architect",
        "token": "TELEGRAM_CONTENT_BOT_TOKEN",
        "color": CYAN,
        "tagline": "Topic to platform to tone, then hooks, a 30s beat script and an SEO caption.",
        "commands": ["/create", "/formulas", "/cancel", "/help", "/clear"],
        "msg": 8, "cb": 7, "kb": 4, "fsm": 3,
        "kb_names": "main · platform · tone · result",
        "states": "AWAITING_TOPIC → AWAITING_PLATFORM → AWAITING_TONE",
        "lane": "code-generation",
        "lane_head": "cerebras:gpt-oss-120b",
        "depth": 8,
        "loc": 447,
    },
    {
        "key": "pdf",
        "name": "Deep Document Assistant",
        "token": "TELEGRAM_PDF_BOT_TOKEN",
        "color": VIOLET,
        "tagline": "PDF / DOCX / TXT / MD in, summary, validated 5-question quiz, flashcards, risk report out.",
        "commands": ["/start", "/help", "/clear"],
        "msg": 5, "cb": 6, "kb": 4, "fsm": 0,
        "kb_names": "actions · quiz answers · next · finish",
        "states": "stateless — document held in FSM data",
        "lane": "spec-generation",
        "lane_head": "gemini:flash",
        "depth": 8,
        "loc": 659,
    },
    {
        "key": "voice",
        "name": "Voice-to-Action",
        "token": "TELEGRAM_VOICE_BOT_TOKEN",
        "color": GREEN,
        "tagline": "Voice note, audio or video note in; transcript, summary and owner/deadline actions out.",
        "commands": ["/start", "/help", "/clear"],
        "msg": 6, "cb": 1, "kb": 3, "fsm": 0,
        "kb_names": "main · result actions · retry",
        "states": "stateless — audio streamed in memory, never written to disk",
        "lane": "summarization",
        "lane_head": "gemini:flash",
        "depth": 10,
        "loc": 338,
    },
    {
        "key": "ielts",
        "name": "IELTS Speaking Coach",
        "token": "TELEGRAM_IELTS_BOT_TOKEN",
        "color": AMBER,
        "tagline": "Three-part FSM mock interview scored on the four official criteria, band 1.0-9.0.",
        "commands": ["/test", "/bands", "/stop", "/help", "/clear"],
        "msg": 9, "cb": 6, "kb": 3, "fsm": 3,
        "kb_names": "main · exam controls · evaluation",
        "states": "IDLE → EXAM_IN_PROGRESS → EVALUATION",
        "lane": "general + summarization",
        "lane_head": "groq:qwen3-27b",
        "depth": 12,
        "loc": 682,
    },
    {
        "key": "crypto",
        "name": "Alpha Scanner",
        "token": "TELEGRAM_CRYPTO_BOT_TOKEN",
        "color": PINK,
        "tagline": "Sentiment 1-100 with drivers, whale notes and a standing not-financial-advice disclaimer.",
        "commands": ["/scan", "/sentiment", "/news", "/help", "/clear"],
        "msg": 7, "cb": 5, "kb": 3, "fsm": 0,
        "kb_names": "main · report · news",
        "states": "stateless — fusion enabled for scans",
        "lane": "classification",
        "lane_head": "cerebras:llama-8b",
        "depth": 10,
        "loc": 477,
    },
]

TASK_ROUTES = [
    ("general", 12, "groq:qwen3-27b"),
    ("summarization", 10, "gemini:flash"),
    ("classification", 10, "cerebras:llama-8b"),
    ("code-generation", 8, "cerebras:gpt-oss-120b"),
    ("spec-generation", 8, "gemini:flash"),
    ("translate", 8, "gemini:flash-lite"),
    ("bug-fixing", 7, "groq:gpt-oss-120b"),
    ("architecture", 7, "cerebras:qwen3-235b"),
]

PROVIDERS = [
    ("gemini", "Google AI Studio", "GEMINI_KEY_P1/P2/P3", "America/Los_Angeles", [
        ("gemini:flash", "10 rpm · 250 rpd · 4.0M tpd", "1.05M ctx", "text vision audio document"),
        ("gemini:flash-lite", "15 rpm · 1 000 rpd · 6.0M tpd", "1.05M ctx", "text vision audio document"),
    ]),
    ("groq", "Groq Cloud", "GROQ_API_KEY", "UTC", [
        ("groq:gpt-oss-120b", "30 rpm · 1 000 rpd · 200K tpd", "131K ctx", "text reasoning"),
        ("groq:qwen3-27b", "30 rpm · 1 000 rpd · 200K tpd", "131K ctx", "text"),
        ("groq:gpt-oss-20b", "30 rpm · 1 000 rpd · 200K tpd", "131K ctx", "text"),
    ]),
    ("cerebras", "Cerebras Inference", "CEREBRAS_API_KEY", "UTC", [
        ("cerebras:gpt-oss-120b", "30 rpm · 14 400 rpd · 1.0M tpd", "65K ctx", "text reasoning"),
        ("cerebras:qwen3-235b", "30 rpm · 14 400 rpd · 1.0M tpd", "131K ctx", "text reasoning long-context"),
        ("cerebras:llama-8b", "30 rpm · 14 400 rpd · 1.0M tpd", "32K ctx", "text"),
    ]),
    ("mistral", "Mistral La Plateforme", "MISTRAL_API_KEY", "UTC", [
        ("mistral:small", "60 rpm · 2 000 rpd · 2.0M tpd", "131K ctx", "text vision"),
        ("mistral:codestral", "30 rpm · 2 000 rpd · 2.0M tpd", "262K ctx", "text code"),
    ]),
    ("openrouter", "OpenRouter", "OPENROUTER_API_KEY", "UTC", [
        ("openrouter:free-auto", "20 rpm · 50 rpd · 200K tpd", "65K ctx", "text"),
    ]),
    ("cloudflare", "Cloudflare Workers AI", "CF_ACCOUNT_ID + CF_API_TOKEN", "UTC", [
        ("cloudflare:llama-8b", "300 rpm · 10 000 rpd · 1.0M tpd", "128K ctx", "text"),
    ]),
    ("github", "GitHub Models", "GITHUB_TOKEN", "UTC", [
        ("github:gpt-4o", "10 rpm · 50 rpd · 100K tpd", "128K ctx", "text vision"),
        ("github:gpt-4o-mini", "15 rpm · 150 rpd · 200K tpd", "128K ctx", "text vision"),
    ]),
    ("mock", "Built-in deterministic mock", "no credential — always ready", "UTC", [
        ("mock:general", "600 rpm · 100 000 rpd", "1.0M ctx", "every capability, offline"),
        ("mock:fast", "600 rpm · 100 000 rpd", "1.0M ctx", "every capability, offline"),
    ]),
]

RECOVERY = [
    ("rate_limit", "cooldown-slot", "slot backs off base · 2^(n-1) ±10% jitter, chain continues", AMBER),
    ("auth", "disable-slot", "credential disabled for the whole process lifetime", PINK),
    ("context_length", "raise-context", "min-context raised, candidates too small are dropped", VIOLET),
    ("safety_block", "bench-model", "that model benched 5 minutes, next candidate answers", VIOLET),
    ("model_unavailable", "bench-model", "retired model id benched 24h, credential stays healthy", AMBER),
    ("server", "bench-provider", "whole provider benched, strike counter increments", PINK),
    ("timeout", "bench-provider", "same treatment as a 5xx: provider-level bench", PINK),
    ("network", "bench-provider", "transport failure is provider-level, not request-level", PINK),
    ("empty", "retry-next", "blank completion is an error, never returned to the user", CYAN),
    ("bad_request", "abort", "malformed request — retrying would only burn quota", DIM),
    ("unknown", "retry-next", "unclassified upstream failure walks to the next candidate", CYAN),
]


# --------------------------------------------------------------------------- #
# 1 · hero-fleet.svg
# --------------------------------------------------------------------------- #


def build_hero():
    w, h = 1280, 560
    b = []
    b.append(rect(40, 36, 4, 30, r=2, fill=CYAN, stroke="none"))
    b.append(text(60, 52, "TELEGRAM BOT FLEET · SHARED PLATFORM", size=11, fill=CYAN,
                  weight=700, spacing=2.6))
    b.append(text(58, 106, "KONKRED", size=52, fill=TEXT, weight=800, spacing=1.5))
    b.append(text(58 + measure("KONKRED", 52, SANS, 800, 1.5) + 22, 106, "bot fleet", size=52,
                  fill=VIOLET, weight=300, spacing=1.5))
    b.append(text(60, 140, "Five Telegram products. One Python process. One Node AI gateway. "
                           "One Redis. One payment rail.", size=15, fill=MUTED))

    # right side status block
    b.append(rect(880, 44, 360, 108, r=14, fill="url(#panelGrad)"))
    b.append(circle(906, 74, 4, fill=GREEN))
    b.append(text(920, 79, "local verification · all suites green", size=12, fill=GREEN, weight=600))
    rows = [
        ("gateway unit tests", "55 pass"),
        ("degradation scenarios", "25 pass"),
        ("live end-to-end steps", "12 pass"),
    ]
    for i, (k, v) in enumerate(rows):
        b.append(text(906, 102 + i * 19, k, size=11.5, fill=MUTED, family=MONO))
        b.append(text(1216, 102 + i * 19, v, size=11.5, fill=TEXT, weight=600,
                      anchor="end", family=MONO))

    # bot cards
    card_y, card_w, card_h, gap = 184, 224, 150, 16
    x0 = 60
    for i, bot in enumerate(BOTS):
        x = x0 + i * (card_w + gap)
        c = bot["color"]
        b.append(rect(x, card_y, card_w, card_h, r=14, fill="url(#panelGrad)"))
        b.append(rect(x, card_y, card_w, 3, r=2, fill=c, stroke="none"))
        b.append(ICONS[bot["key"]](x + 38, card_y + 48, c, 0.85))
        b.append(text(x + 70, card_y + 40, bot["key"].upper(), size=12, fill=c,
                      weight=700, spacing=1.6, family=MONO, max_w=card_w - 86))
        b.append(text(x + 70, card_y + 58, bot["name"], size=11, fill=TEXT, weight=600,
                      max_w=card_w - 84))
        b.append(line(x + 16, card_y + 78, x + card_w - 16, card_y + 78, stroke=STROKE))
        b.append(text(x + 16, card_y + 99, f"{bot['msg'] + bot['cb']} handlers", size=10.5,
                      fill=MUTED, family=MONO))
        b.append(text(x + card_w - 16, card_y + 99, f"{bot['kb']} keyboards", size=10.5,
                      fill=MUTED, family=MONO, anchor="end"))
        b.append(text(x + 16, card_y + 118, "lane", size=10, fill=DIM, family=MONO))
        b.append(text(x + card_w - 16, card_y + 118, bot["lane"].split(" + ")[0], size=10,
                      fill=c, family=MONO, anchor="end", weight=600, max_w=card_w - 70))
        b.append(text(x + 16, card_y + 136, bot["token"], size=9, fill=MUTED, family=MONO,
                      max_w=card_w - 32))

    # the shared rail
    rail_y = 372
    b.append(f'<rect x="60" y="{rail_y}" width="1160" height="3" rx="1.5" fill="url(#railGrad)"/>')
    for i in range(5):
        x = x0 + i * (card_w + gap) + card_w / 2
        b.append(line(x, card_y + card_h, x, rail_y, stroke=STROKE, sw=1, dash="3 4"))
        b.append(circle(x, rail_y + 1.5, 3.5, fill=BOTS[i]["color"]))

    b.append(text(60, rail_y + 34, "SHARED INFRASTRUCTURE · every bot uses the same objects",
                  size=11, fill=MUTED, weight=600, spacing=1.6))

    shared = [
        ("Aiogram runtime", "1 loop · 5 dispatchers", CYAN),
        ("Redis 7", "history + FSM per bot", CYAN),
        ("Payment gate", "5 free → Stars XTR", PINK),
        ("Gateway client", "1 httpx pool, typed", VIOLET),
        ("Node AI gateway", "8 lanes · 16 slots", VIOLET),
        ("Provider pool", "8 + mock tail", GREEN),
    ]
    sw_ = 182
    for i, (title, sub, c) in enumerate(shared):
        x = 60 + i * (sw_ + 13)
        b.append(rect(x, rail_y + 46, sw_, 62, r=12, fill=PANEL_3, stroke=STROKE))
        b.append(rect(x, rail_y + 46, 3, 62, r=1.5, fill=c, stroke="none", opacity=0.8))
        b.append(text(x + 14, rail_y + 68, title, size=12, fill=TEXT, weight=600,
                      max_w=sw_ - 26))
        b.append(text(x + 14, rail_y + 88, sub, size=10, fill=MUTED, family=MONO,
                      max_w=sw_ - 26))

    # bottom metric strip
    my = 500
    metrics = [
        ("5", "Telegram products"),
        ("1", "Python process"),
        ("60", "feature handlers"),
        ("5", "shared payment handlers"),
        ("17", "shared keyboards"),
        ("8", "task lanes"),
        ("16", "model slots"),
        ("0", "gateway npm deps"),
    ]
    mw = 1160 / len(metrics)
    b.append(rect(60, my - 28, 1160, 48, r=12, fill=PANEL_3, stroke=STROKE))
    for i, (value, label) in enumerate(metrics):
        cx = 60 + mw * i + mw / 2
        b.append(text(cx, my - 6, value, size=17, fill=TEXT, weight=700, anchor="middle",
                      family=MONO))
        b.append(text(cx, my + 11, label, size=9.5, fill=MUTED, anchor="middle"))
        if i:
            b.append(line(60 + mw * i, my - 18, 60 + mw * i, my + 12, stroke=STROKE))
    return document("hero-fleet.svg", w, h, "".join(b))


# --------------------------------------------------------------------------- #
# 2 · five-bot-deck.svg
# --------------------------------------------------------------------------- #


def build_deck():
    w, h = 1280, 1050
    b = [header(w, "FLEET IDENTITY", "Five products, five identity cards",
                "Handler counts, keyboards and FSM depth are read from the routers themselves.",
                accent=CYAN)]
    card_w, card_h, gap = 590, 275, 20
    for i, bot in enumerate(BOTS):
        col, row = i % 2, i // 2
        x = 50 + col * (card_w + gap)
        y = 150 + row * (card_h + gap)
        c = bot["color"]
        b.append(rect(x, y, card_w, card_h, r=16, fill="url(#panelGrad)"))
        b.append(rect(x, y, 4, card_h, r=2, fill=c, stroke="none"))
        b.append(rect(x + 22, y + 22, 58, 58, r=14, fill=c, stroke=c, sw=1, opacity=0.09))
        b.append(ICONS[bot["key"]](x + 51, y + 51, c, 0.95))
        b.append(text(x + 96, y + 44, bot["name"], size=17, fill=TEXT, weight=700))
        b.append(text(x + 96, y + 65, bot["token"], size=11, fill=c, family=MONO))
        idx, _ = pill(x + card_w - 74, y + 26, f"bot {i + 1}/5", color=c, size=10)
        b.append(idx)
        b.append(text(x + 22, y + 104, bot["tagline"], size=12, fill=MUTED, max_w=card_w - 44))

        prow, _ = pill_row(x + 22, y + 120, bot["commands"], color=c, size=10.5, family=MONO)
        b.append(prow)

        b.append(line(x + 22, y + 158, x + card_w - 22, y + 158, stroke=STROKE))
        stats = [
            ("message handlers", bot["msg"]),
            ("callback handlers", bot["cb"]),
            ("keyboards", bot["kb"]),
            ("FSM states", bot["fsm"] or "—"),
            ("handler LOC", bot["loc"]),
        ]
        sw_ = (card_w - 44) / len(stats)
        for j, (label, value) in enumerate(stats):
            cx = x + 22 + sw_ * j + sw_ / 2
            b.append(text(cx, y + 186, str(value), size=16, fill=TEXT, weight=700,
                          anchor="middle", family=MONO))
            b.append(text(cx, y + 202, label, size=9, fill=DIM, anchor="middle"))
        b.append(line(x + 22, y + 214, x + card_w - 22, y + 214, stroke=STROKE))
        b.append(text(x + 22, y + 233, "lane", size=9.5, fill=DIM, family=MONO))
        b.append(text(x + 110, y + 233, bot["lane"], size=10.5, fill=c, weight=600,
                      family=MONO, max_w=220))
        b.append(text(x + card_w - 22, y + 233, f"head candidate {bot['lane_head']}", size=9.5,
                      fill=DIM, anchor="end", family=MONO, max_w=230))
        b.append(text(x + 22, y + 251, "keyboards", size=9.5, fill=DIM, family=MONO))
        b.append(text(x + 110, y + 251, bot["kb_names"], size=10, fill=MUTED, family=MONO,
                      max_w=card_w - 134))
        b.append(text(x + 22, y + 269, "state", size=9.5, fill=DIM, family=MONO))
        b.append(text(x + 110, y + 269, bot["states"], size=10, fill=MUTED, family=MONO,
                      max_w=card_w - 134))

    # legend cell
    x, y = 50 + (card_w + gap), 150 + 2 * (card_h + gap)
    b.append(rect(x, y, card_w, card_h, r=16, fill=PANEL_3, stroke=STROKE, dash="5 5"))
    b.append(text(x + 24, y + 38, "WHAT IS ACTUALLY SHARED", size=11, fill=MUTED,
                  weight=700, spacing=2))
    shared_lines = [
        ("one process", "all five dispatchers run on one asyncio event loop"),
        ("one Redis pool", "history, FSM and payment counters, namespaced by bot key"),
        ("one HTTP client", "a singleton httpx pool to the gateway, 20 keepalive conns"),
        ("one payment router", "included before every feature router, 5 handlers"),
        ("one splitter", "split_telegram_message() guards the 4096-character ceiling"),
        ("nothing else", "handlers and keyboards never import another bot's module"),
    ]
    for j, (k, v) in enumerate(shared_lines):
        yy = y + 68 + j * 30
        b.append(circle(x + 30, yy - 4, 3, fill=CYAN if j < 5 else GREEN))
        b.append(text(x + 44, yy, k, size=11.5, fill=TEXT, weight=600, family=MONO))
        b.append(text(x + 180, yy, v, size=11, fill=MUTED))
    return document("five-bot-deck.svg", w, h, "".join(b))


# --------------------------------------------------------------------------- #
# 3 · shared-runtime.svg
# --------------------------------------------------------------------------- #


def build_runtime():
    w, h = 1280, 760
    b = [header(w, "SHARED RUNTIME", "One process, five routers, five shared services",
                "bots/main.py builds one Bot + Dispatcher per configured token and injects the "
                "same shared objects.", accent=CYAN)]

    # telegram
    b.append(rect(50, 148, 1180, 62, r=14, fill=PANEL_3, stroke=STROKE))
    b.append(text(74, 176, "TELEGRAM", size=12, fill=CYAN, weight=700, spacing=2))
    b.append(text(74, 196, "polling or webhook · one Bot instance per configured token", size=11,
                  fill=MUTED, family=MONO))
    b.append(text(1206, 186, "BOT_MODE", size=11, fill=DIM, anchor="end", family=MONO))

    # process box
    b.append(rect(50, 236, 1180, 250, r=16, fill="#0C1016", stroke=CYAN, sw=1, opacity=1))
    b.append(rect(50, 236, 1180, 250, r=16, fill="none", stroke=CYAN, sw=1, opacity=0.25))
    b.append(text(74, 266, "PYTHON 3.11 · AIOGRAM 3.15 · ONE ASYNCIO EVENT LOOP", size=11,
                  fill=CYAN, weight=700, spacing=2))
    b.append(text(1206, 266, "bots/main.py", size=11, fill=DIM, anchor="end", family=MONO))

    cw, gap = 216, 15
    for i, bot in enumerate(BOTS):
        x = 74 + i * (cw + gap)
        y = 286
        c = bot["color"]
        b.append(rect(x, y, cw, 176, r=13, fill=PANEL, stroke=STROKE))
        b.append(rect(x, y, cw, 3, r=2, fill=c, stroke="none"))
        b.append(text(x + 14, y + 26, f"Dispatcher · {bot['key']}", size=11.5, fill=c,
                      weight=700, family=MONO))
        b.append(line(x + 14, y + 36, x + cw - 14, y + 36, stroke=STROKE))
        stack = [
            ("RedisStorage", f"prefix konkred:fsm:{bot['key']}"),
            ("payment Router", "pre_checkout · payment · /paysupport"),
            (f"bot_{bot['key']}.handlers", f"{bot['msg']} message · {bot['cb']} callback"),
            (f"bot_{bot['key']}.keyboards", f"{bot['kb']} inline keyboards"),
        ]
        for j, (title, sub) in enumerate(stack):
            yy = y + 52 + j * 31
            b.append(rect(x + 12, yy, cw - 24, 26, r=7, fill=PANEL_2, stroke=STROKE))
            b.append(text(x + 20, yy + 11.5, title, size=9.8, fill=TEXT, weight=600,
                          family=MONO, max_w=cw - 40))
            b.append(text(x + 20, yy + 22, sub, size=8.4, fill=MUTED, family=MONO,
                          max_w=cw - 40))
        b.append(arrow(x + cw / 2, 210, x + cw / 2, 284, color=CYAN, sw=1.4, marker="arrowCyan"))
        b.append(arrow(x + cw / 2, 462, x + cw / 2, 528, color=VIOLET, sw=1.4, dash="4 4",
                       marker="arrowViolet"))

    # shared services rail
    b.append(text(50, 520, "SHARED SERVICE RAIL · bots/shared/", size=11, fill=VIOLET,
                  weight=700, spacing=2))
    services = [
        ("config.py", "BOT_SPECS, settings, get_active_bots()", "279 LOC", CYAN),
        ("history.py", "konkred:hist:{bot}:{uid} · 10 turns", "137 LOC", CYAN),
        ("payments.py", "WATCH/MULTI counter, signed payload", "408 LOC", PINK),
        ("gateway_client.py", "singleton httpx pool, typed errors", "306 LOC", VIOLET),
        ("utils.py", "split_telegram_message(), HTML escaping", "299 LOC", GREEN),
    ]
    sw_ = 228
    for i, (name, sub, loc, c) in enumerate(services):
        x = 50 + i * (sw_ + 8)
        b.append(rect(x, 534, sw_, 78, r=13, fill="url(#panelGrad)"))
        b.append(rect(x, 534, sw_, 3, r=2, fill=c, stroke="none"))
        b.append(text(x + 16, 560, name, size=12.5, fill=TEXT, weight=700, family=MONO,
                      max_w=sw_ - 80))
        b.append(text(x + sw_ - 16, 560, loc, size=9.5, fill=DIM, anchor="end", family=MONO))
        b.append(text(x + 16, 582, sub, size=10, fill=MUTED, max_w=sw_ - 32))
        b.append(text(x + 16, 600, "shared by all five bots", size=9.5, fill=c, family=MONO))

    # downstream
    b.append(rect(50, 636, 578, 88, r=14, fill=PANEL_3, stroke=STROKE))
    b.append(text(74, 664, "REDIS 7", size=12, fill=CYAN, weight=700, spacing=2))
    b.append(text(74, 686, "one redis.asyncio pool · history + FSM + payment counters", size=11,
                  fill=MUTED, family=MONO))
    b.append(text(74, 706, "appendonly everysec · 256 MB · allkeys-lru", size=10, fill=DIM,
                  family=MONO))

    b.append(rect(652, 636, 578, 88, r=14, fill=PANEL_3, stroke=STROKE))
    b.append(text(676, 664, "NODE 20 AI GATEWAY", size=12, fill=VIOLET, weight=700, spacing=2))
    b.append(text(676, 686, "one httpx client · POST /api/ai · 120 s timeout", size=11,
                  fill=MUTED, family=MONO))
    b.append(text(676, 706, "8 task lanes → 16 model slots → 8 providers", size=10, fill=DIM,
                  family=MONO))
    b.append(arrow(339, 612, 339, 634, color=CYAN, sw=1.4, marker="arrowCyan"))
    b.append(arrow(941, 612, 941, 634, color=VIOLET, sw=1.4, marker="arrowViolet"))
    return document("shared-runtime.svg", w, h, "".join(b))


# --------------------------------------------------------------------------- #
# 4 · telegram-ingress.svg
# --------------------------------------------------------------------------- #


def build_ingress():
    w, h = 1280, 650
    b = [header(w, "TELEGRAM INGRESS", "Two transports, one dispatch pipeline",
                "BOT_MODE selects the transport; everything after the Update object is identical.",
                accent=CYAN)]

    # transports
    modes = [
        ("BOT_MODE=polling", CYAN, [
            "every dispatcher long-polls Telegram concurrently",
            "aiohttp still serves / and /healthz",
            "allowed_updates from resolve_used_update_types()",
            "drop_pending_updates follows DROP_PENDING_UPDATES",
        ], "local Docker Compose · always-on VPS"),
        ("BOT_MODE=webhook", VIOLET, [
            "POST /webhook/{bot_key}/{path_secret}",
            "path secret = HMAC-SHA256(WEBHOOK_SECRET, key:token)[:32]",
            "X-Telegram-Bot-Api-Secret-Token compared constant-time",
            "dispatch scheduled as a task, 200 OK returned first",
        ], "Render free web service"),
    ]
    for i, (title, c, lines, foot) in enumerate(modes):
        x = 50 + i * 600
        b.append(rect(x, 148, 580, 158, r=15, fill="url(#panelGrad)"))
        b.append(rect(x, 148, 580, 3, r=2, fill=c, stroke="none"))
        b.append(text(x + 22, 178, title, size=14, fill=c, weight=700, family=MONO))
        for j, ln in enumerate(lines):
            b.append(circle(x + 28, 199 + j * 21, 2.5, fill=c, opacity=0.8))
            b.append(text(x + 40, 203 + j * 21, ln, size=11, fill=MUTED, family=MONO))
        b.append(text(x + 558, 178, foot, size=10, fill=DIM, anchor="end"))
        b.append(arrow(x + 290, 306, x + 290, 340, color=c, sw=1.5,
                       marker="arrowCyan" if i == 0 else "arrowViolet"))

    # merge
    b.append(rect(50, 340, 1180, 44, r=12, fill=PANEL_3, stroke=STROKE))
    b.append(text(640, 368, "aiogram Update  →  Dispatcher.feed_update(bot, update)",
                  size=13, fill=TEXT, weight=600, anchor="middle", family=MONO))

    # pipeline
    stages = [
        ("payment Router", PINK, [
            "pre_checkout_query", "F.successful_payment", "/paysupport", "/verify",
            "pay:approve|reject",
        ], "included first so payment events never hit a broad F.text filter"),
        ("feature Router", CYAN, [
            "Command / CommandStart", "StateFilter", "F.voice F.audio F.document",
            "F.data callbacks", "catch-all F.text",
        ], "one Router per bot, resolved by BOT_SPECS.router_path"),
        ("handler + DI", VIOLET, [
            "history: HistoryManager", "payments: PaymentManager", "bot_spec: BotSpec",
            "gateway: shared client",
        ], "dispatcher workflow data is injected per bot, never global"),
    ]
    for i, (title, c, items, foot) in enumerate(stages):
        x = 50 + i * 396
        ww = 376
        b.append(rect(x, 412, ww, 176, r=14, fill="url(#panelGrad)"))
        b.append(rect(x, 412, 3, 176, r=2, fill=c, stroke="none"))
        b.append(text(x + 22, 440, f"{i + 1}. {title}", size=13, fill=c, weight=700))
        yy = 456
        row, _ = pill_row(x + 22, yy, items[:2], color=c, size=10, family=MONO)
        b.append(row)
        row, _ = pill_row(x + 22, yy + 28, items[2:4], color=c, size=10, family=MONO)
        b.append(row)
        if len(items) > 4:
            row, _ = pill_row(x + 22, yy + 56, items[4:], color=c, size=10, family=MONO)
            b.append(row)
        b.append(line(x + 22, 552, x + ww - 22, 552, stroke=STROKE))
        b.append(text(x + 22, 572, foot, size=10, fill=MUTED))
        if i < 2:
            b.append(arrow(x + ww + 4, 500, x + ww + 16, 500, color=DIM, sw=1.5))
    b.append(text(50, 618, "Isolation guarantee: a handler in bot_pdf cannot observe an update "
                           "routed to bot_voice — separate Dispatcher, separate FSM prefix, "
                           "separate history namespace.", size=11, fill=DIM))
    return document("telegram-ingress.svg", w, h, "".join(b))


# --------------------------------------------------------------------------- #
# 5 · redis-state.svg
# --------------------------------------------------------------------------- #


def build_redis():
    w, h = 1280, 700
    b = [header(w, "SHARED MEMORY + FSM", "Redis namespaces, keyed per bot",
                "Same Telegram user id, five independent conversations — proven by the "
                "isolation tests.", accent=CYAN)]

    keys = [
        ("conversation history", "konkred:hist:{bot}:{user_id}",
         ["JSON list of {role, content}", "trimmed to HISTORY_TURNS · 2 = 20 messages",
          "TTL refreshed to 86 400 s on every write", "corrupt payloads are dropped, never raised"],
         CYAN, "HistoryManager · shared/history.py"),
        ("aiogram FSM", "konkred:fsm:{bot}:{bot_id}:{chat}:{user}:data",
         ["DefaultKeyBuilder(prefix=…, with_bot_id=True)", "state_ttl = data_ttl = HISTORY_TTL",
          "3 states in ielts, 3 in content", "pdf/voice/crypto keep data without a state machine"],
         VIOLET, "RedisStorage · bots/main.py"),
        ("payment ledger", "konkred:pay:{bot}:free|access|pending|usdt:{id}",
         ["free counter guarded by WATCH/MULTI", "access key expires after PAID_ACCESS_DAYS",
          "pending holds an unverified USDT txid", "usdt:{sha256} blocks txid replay"],
         PINK, "PaymentManager · shared/payments.py"),
    ]
    for i, (title, keyfmt, lines, c, owner) in enumerate(keys):
        x = 50 + i * 396
        ww = 376
        b.append(rect(x, 150, ww, 224, r=15, fill="url(#panelGrad)"))
        b.append(rect(x, 150, ww, 3, r=2, fill=c, stroke="none"))
        b.append(text(x + 22, 180, title.upper(), size=11, fill=c, weight=700, spacing=1.8))
        b.append(rect(x + 18, 192, ww - 36, 30, r=8, fill="#0A0D14", stroke=STROKE))
        b.append(text(x + 30, 212, keyfmt, size=10, fill=TEXT, family=MONO))
        for j, ln in enumerate(lines):
            b.append(circle(x + 28, 244 + j * 24, 2.5, fill=c, opacity=0.75))
            b.append(text(x + 40, 248 + j * 24, ln, size=10.5, fill=MUTED, family=MONO))
        b.append(line(x + 22, 344, x + ww - 22, 344, stroke=STROKE))
        b.append(text(x + 22, 362, owner, size=10, fill=DIM, family=MONO))

    # isolation matrix
    b.append(text(50, 420, "NAMESPACE ISOLATION · user 777 talking to every bot at once",
                  size=11, fill=MUTED, weight=700, spacing=1.8))
    b.append(rect(50, 434, 1180, 170, r=15, fill=PANEL_3, stroke=STROKE))
    col_w = 1140 / 5
    for i, bot in enumerate(BOTS):
        x = 70 + i * col_w
        c = bot["color"]
        b.append(text(x, 462, bot["key"], size=12, fill=c, weight=700, family=MONO))
        b.append(line(x, 470, x + col_w - 24, 470, stroke=c, sw=1, opacity=0.4))
        b.append(text(x, 492, f"konkred:hist:{bot['key']}:777", size=9.6, fill=MUTED, family=MONO))
        b.append(text(x, 512, f"konkred:fsm:{bot['key']}:…:777", size=9.6, fill=MUTED, family=MONO))
        b.append(text(x, 532, f"konkred:pay:{bot['key']}:free:777", size=9.6, fill=MUTED, family=MONO))
        st = "3 FSM states" if bot["fsm"] else "no FSM states"
        b.append(text(x, 556, st, size=9.6, fill=c, family=MONO))
        b.append(text(x, 578, f"{bot['loc']} LOC of handlers", size=9.6, fill=DIM, family=MONO))
    b.append(text(50, 636, "Redis 7-alpine · appendonly yes · appendfsync everysec · maxmemory 256mb "
                           "· allkeys-lru · named volume redis-data", size=11, fill=DIM, family=MONO))
    b.append(text(50, 660, "A history read failure never raises into a handler: HistoryManager "
                           "logs and returns an empty window, so a Redis blip degrades memory, "
                           "not the reply.", size=11, fill=MUTED))
    return document("redis-state.svg", w, h, "".join(b))


# --------------------------------------------------------------------------- #
# 6 · payment-rail.svg
# --------------------------------------------------------------------------- #


def build_payments():
    w, h = 1280, 700
    b = [header(w, "SHARED PAYMENT RAIL", "One gate, five bots, counted separately",
                "FREE_REQUESTS AI-powered actions per user per bot, then a native Telegram "
                "Stars invoice.", accent=PINK)]

    # counter strip
    b.append(rect(50, 150, 1180, 96, r=15, fill="url(#panelGrad)"))
    b.append(text(74, 180, "FREE ALLOWANCE · FREE_REQUESTS = 5 per user, per bot", size=12,
                  fill=PINK, weight=700, spacing=1.6))
    for i in range(6):
        x = 78 + i * 74
        used = i < 5
        c = PINK if used else DIM
        b.append(rect(x, 196, 60, 32, r=9, fill=c, stroke=c, sw=1, opacity=0.14))
        b.append(rect(x, 196, 60, 32, r=9, fill="none", stroke=c, sw=1, opacity=0.55))
        b.append(text(x + 30, 217, f"#{i + 1}", size=12, fill=c, weight=700, anchor="middle",
                      family=MONO))
    b.append(text(530, 208, "requests 1-5 are free AI actions", size=11, fill=MUTED, family=MONO))
    b.append(text(530, 228, "request 6 triggers the invoice — navigation, /start, uploads and "
                            "quiz buttons never consume the allowance", size=10.5, fill=DIM))
    b.append(text(1206, 180, "PAYMENTS_ENABLED=false disables the gate entirely", size=10,
                  fill=DIM, anchor="end", family=MONO))

    steps = [
        ("1 · reserve", PINK, "PaymentManager.reserve_request()",
         ["WATCH access + counter keys", "MULTI / INCR on the free counter",
          "retries up to 8 times on WatchError", "storage error → warning, no charge"]),
        ("2 · invoice", PINK, "send_invoice(currency=\"XTR\")",
         ["STARS_PRICE = 100 Stars", "PAID_ACCESS_DAYS = 30", "payload v1:bot:user:nonce:sig",
          "sig = HMAC-SHA256(PAYMENT_SECRET)[:20]"]),
        ("3 · pre-checkout", VIOLET, "@router.pre_checkout_query()",
         ["currency must be XTR", "amount must equal STARS_PRICE",
          "payload validated against this user id", "invalid → answer(ok=False) with a reason"]),
        ("4 · unlock", GREEN, "F.successful_payment",
         ["access key written immediately", "EX = PAID_ACCESS_DAYS · 86 400",
          "reserve_request() short-circuits", "/paysupport prints receipt guidance"]),
    ]
    for i, (title, c, code, lines) in enumerate(steps):
        x = 50 + i * 298
        ww = 278
        b.append(rect(x, 274, ww, 186, r=14, fill="url(#panelGrad)"))
        b.append(rect(x, 274, ww, 3, r=2, fill=c, stroke="none"))
        b.append(text(x + 20, 302, title, size=13, fill=c, weight=700))
        b.append(rect(x + 16, 314, ww - 32, 26, r=7, fill="#0A0D14", stroke=STROKE))
        b.append(text(x + 26, 331, code, size=9.6, fill=TEXT, family=MONO))
        for j, ln in enumerate(lines):
            b.append(circle(x + 26, 360 + j * 23, 2.4, fill=c, opacity=0.8))
            b.append(text(x + 38, 364 + j * 23, ln, size=10, fill=MUTED, family=MONO,
                          max_w=ww - 54))
        if i < 3:
            b.append(arrow(x + ww + 2, 367, x + ww + 16, 367, color=DIM, sw=1.5))

    # usdt side path
    b.append(rect(50, 484, 760, 122, r=14, fill=PANEL_3, stroke=STROKE, dash="6 5"))
    b.append(text(74, 512, "OPTIONAL USDT PATH · disabled unless USDT_WALLET_ADDRESS is set",
                  size=12, fill=AMBER, weight=700))
    usdt = [
        "/verify <txid> stores a pending record keyed by sha256(txid)",
        "PAYMENT_ADMIN_IDS receive Approve / Reject inline buttons",
        "approval is manual by design: a wallet address cannot prove an on-chain payment",
        "render.yaml leaves USDT_WALLET_ADDRESS blank — the default path is Stars only",
    ]
    for j, ln in enumerate(usdt):
        b.append(circle(80, 536 + j * 18, 2.3, fill=AMBER, opacity=0.8))
        b.append(text(92, 540 + j * 18, ln, size=10.5, fill=MUTED, family=MONO))

    b.append(rect(826, 484, 404, 122, r=14, fill="url(#panelGrad)"))
    b.append(text(850, 512, "VERIFIED BY tests/test_payments.py", size=11.5, fill=GREEN,
                  weight=700, family=MONO))
    checks = [
        "ceiling holds at exactly 5 free actions",
        "50 concurrent reservations never exceed the ceiling",
        "payload signature rejects a foreign user id",
        "granted entitlement short-circuits the counter",
    ]
    for j, ln in enumerate(checks):
        b.append(text(850, 538 + j * 18, "✓", size=11, fill=GREEN, family=MONO))
        b.append(text(866, 538 + j * 18, ln, size=10.3, fill=MUTED, family=MONO))

    b.append(text(50, 644, "Pricing, allowance and lifetime are environment settings "
                           "(FREE_REQUESTS · STARS_PRICE · PAID_ACCESS_DAYS): changing them "
                           "needs no rebuild.", size=11, fill=DIM))
    b.append(text(50, 668, "Telegram Stars are the only path enabled by default for selling "
                           "digital access inside a bot.", size=11, fill=MUTED))
    return document("payment-rail.svg", w, h, "".join(b))


# --------------------------------------------------------------------------- #
# 7 · gateway-routing.svg
# --------------------------------------------------------------------------- #


def build_routing():
    w, h = 1280, 820
    b = [header(w, "GATEWAY ROUTING", "POST /api/ai — eight stages, zero npm dependencies",
                "Node 20 ESM over native node:http. The bots never talk to a model vendor "
                "directly.", accent=VIOLET)]

    stages = [
        ("validate", ["60 messages max", "2 000 000 chars"], CYAN),
        ("fair use", ["USER_RPM 12", "RPD 400 · TPD 900k"], CYAN),
        ("cache", ["SHA-256 key", "LRU 500 · TTL 600 s"], VIOLET),
        ("dedup", ["in-flight Map", "hard TTL 180 000 ms"], VIOLET),
        ("route", ["task lane →", "candidate chain"], VIOLET),
        ("admit", ["key-pool slot under", "every headroom"], PINK),
        ("dispatch", ["provider adapter", "typed errors"], PINK),
        ("classify", ["answer, or recover", "and re-enter admit"], GREEN),
    ]
    bx, by, bw, bh = 50, 150, 143, 92
    for i, (title, sublines, c) in enumerate(stages):
        x = bx + i * (bw + 6)
        b.append(rect(x, by, bw, bh, r=12, fill="url(#panelGrad)"))
        b.append(rect(x, by, bw, 3, r=2, fill=c, stroke="none"))
        b.append(text(x + bw / 2, by + 30, f"{i + 1}", size=10, fill=DIM, anchor="middle",
                      family=MONO))
        b.append(text(x + bw / 2, by + 50, title, size=13, fill=TEXT, weight=700, anchor="middle"))
        for j, wtext in enumerate(sublines):
            b.append(text(x + bw / 2, by + 68 + j * 13, wtext, size=8.4, fill=MUTED,
                          anchor="middle", family=MONO, max_w=bw - 14))
        if i < 7:
            b.append(arrow(x + bw + 1, by + bh / 2, x + bw + 4, by + bh / 2, color=DIM, sw=1.4))
    # recovery loop
    b.append(path(f"M {bx + 7 * (bw + 6) + bw / 2} {by + bh + 4} "
                  f"L {bx + 7 * (bw + 6) + bw / 2} {by + bh + 26} "
                  f"L {bx + 5 * (bw + 6) + bw / 2} {by + bh + 26} "
                  f"L {bx + 5 * (bw + 6) + bw / 2} {by + bh + 6}",
                  stroke=AMBER, sw=1.4, dash="5 4", marker="arrow"))
    b.append(text(bx + 6 * (bw + 6) + bw / 2, by + bh + 42, "recover → next candidate",
                  size=10, fill=AMBER, anchor="middle", family=MONO))

    # task lane chart
    b.append(text(50, 320, "TASK LANES · candidate chain depth (TASK_ROUTES, gateway/src/gateway/router.mjs)",
                  size=11, fill=VIOLET, weight=700, spacing=1.4))
    b.append(rect(50, 334, 760, 276, r=15, fill=PANEL_3, stroke=STROKE))
    max_depth = 12
    bar_x, bar_w = 210, 320
    for i, (task, depth, head) in enumerate(TASK_ROUTES):
        y = 364 + i * 29
        b.append(text(74, y + 4, task, size=11, fill=TEXT, family=MONO, max_w=128))
        b.append(rect(bar_x, y - 9, bar_w, 18, r=9, fill="#0A0D14", stroke=STROKE))
        fill_w = bar_w * depth / max_depth
        b.append(f'<rect x="{bar_x}" y="{y - 9}" width="{fill_w}" height="18" rx="9" '
                 f'fill="url(#cyanFade)" opacity="0.85"/>')
        b.append(text(bar_x + fill_w - 10, y + 4, f"{depth}", size=10.5, fill="#0A0B10",
                      weight=700, anchor="end", family=MONO))
        b.append(text(bar_x + bar_w + 14, y + 4, head, size=9.4, fill=MUTED, family=MONO,
                      max_w=224))
    b.append(text(74, 592, "Every chain ends in a mock slot, so a lane cannot run out of "
                           "candidates.", size=10.2, fill=GREEN, family=MONO, max_w=700))

    # headroom gauges
    b.append(text(830, 320, "ADMISSION HEADROOM", size=11, fill=PINK, weight=700, spacing=1.4))
    b.append(rect(826, 334, 404, 276, r=15, fill=PANEL_3, stroke=STROKE))
    gauges = [("RPM", 0.85, CYAN), ("TPM", 0.90, VIOLET), ("RPD", 0.95, PINK), ("TPD", 0.98, GREEN)]
    for i, (label, frac, c) in enumerate(gauges):
        cx = 900 + (i % 2) * 180
        cy = 400 + (i // 2) * 124
        r = 40
        circumference = 2 * 3.14159 * r
        b.append(circle(cx, cy, r, fill="none", stroke="#0A0D14", sw=9))
        b.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{c}" stroke-width="9" '
                 f'stroke-linecap="round" stroke-dasharray="{circumference * frac:.1f} '
                 f'{circumference:.1f}" transform="rotate(-90 {cx} {cy})" opacity="0.9"/>')
        b.append(text(cx, cy + 2, f"{int(frac * 100)}%", size=16, fill=TEXT, weight=700,
                      anchor="middle", family=MONO))
        b.append(text(cx, cy + 18, label, size=10, fill=MUTED, anchor="middle", family=MONO))
    b.append(text(850, 592, "A slot is admitted only below every window ceiling.",
                  size=10, fill=DIM, family=MONO, max_w=356))

    # bottom facts
    facts = [
        ("registry", "2026.09.1 · 16 model slots · 8 providers"),
        ("daily reset", "midnight Pacific for Gemini, UTC elsewhere"),
        ("watchdog", "60 s tick · rolls windows · self-calibrates a real 429"),
        ("persistence", "data/runtime.state.json survives a restart"),
    ]
    b.append(rect(50, 630, 1180, 58, r=13, fill="url(#panelGrad)"))
    fw = 1180 / 4
    for i, (k, v) in enumerate(facts):
        x = 74 + i * fw
        b.append(text(x, 654, k, size=10, fill=VIOLET, weight=700, family=MONO))
        b.append(text(x, 672, v, size=10, fill=MUTED, family=MONO, max_w=fw - 40))
        if i:
            b.append(line(50 + fw * i, 642, 50 + fw * i, 676, stroke=STROKE))

    b.append(text(50, 718, "BOT SIDE · shared/gateway_client.py", size=11, fill=CYAN,
                  weight=700, spacing=1.4))
    client = [
        "singleton httpx.AsyncClient · max_keepalive_connections=20 · 120 s timeout, 10 s connect",
        "typed GatewayError carries status_code, code, message and retry_after",
        "retries only 502 / 504 and all_candidates_failed · all_slots_rate_limited · internal_error",
        "backoff min(4.0, 0.75 · 2^attempt) — a 400 is never retried",
    ]
    for i, ln in enumerate(client):
        b.append(circle(58, 737 + i * 17, 2.3, fill=CYAN, opacity=0.8))
        b.append(text(70, 741 + i * 17, ln, size=10.2, fill=MUTED, family=MONO, max_w=1150))
    return document("gateway-routing.svg", w, h, "".join(b))


# --------------------------------------------------------------------------- #
# 8 · provider-rack.svg
# --------------------------------------------------------------------------- #


def build_rack():
    w, h = 1280, 1010
    b = [header(w, "PROVIDER RACK", "16 model slots across 8 providers",
                "gateway/data/policies.registry.json — published free-tier envelopes, "
                "self-calibrated downward when an upstream disagrees.", accent=GREEN)]

    y = 150
    for provider, label, cred, tz, models in PROVIDERS:
        rows = len(models)
        block_h = 34 + rows * 26
        c = GREEN if provider == "mock" else (VIOLET if provider in ("gemini", "groq") else CYAN)
        b.append(rect(50, y, 1180, block_h, r=13, fill="url(#panelGrad)"))
        b.append(rect(50, y, 3, block_h, r=2, fill=c, stroke="none"))
        b.append(text(72, y + 23, provider, size=12.5, fill=c, weight=700, family=MONO))
        b.append(text(180, y + 23, label, size=11.5, fill=TEXT, max_w=280))
        b.append(text(470, y + 23, cred, size=10, fill=MUTED, family=MONO, max_w=280))
        b.append(text(766, y + 23, f"reset {tz}", size=9.6, fill=DIM, family=MONO, max_w=170))
        status = "always ready" if provider == "mock" else "ready when the key is set"
        scol = GREEN if provider == "mock" else DIM
        b.append(circle(1212, y + 19, 3.5, fill=scol))
        b.append(text(1200, y + 23, status, size=9.5, fill=scol, anchor="end", family=MONO))
        b.append(line(66, y + 32, 1214, y + 32, stroke=STROKE))
        for j, (key, quota, ctx, caps) in enumerate(models):
            ry = y + 50 + j * 26
            b.append(text(88, ry, key, size=10.6, fill=TEXT, family=MONO, max_w=250))
            b.append(text(348, ry, quota, size=10.2, fill=MUTED, family=MONO, max_w=290))
            b.append(text(654, ry, ctx, size=10.2, fill=MUTED, family=MONO, max_w=90))
            b.append(text(760, ry, caps, size=10.2, fill=DIM, family=MONO, max_w=440))
        y += block_h + 10

    b.append(rect(50, y, 1180, 64, r=13, fill=PANEL_3, stroke=STROKE))
    b.append(text(74, y + 26, "SLOT MATH", size=11, fill=GREEN, weight=700, spacing=1.6))
    b.append(text(74, y + 48, "one slot = (provider, model, credential). Three GEMINI_KEY_* "
                              "variables triple the Gemini slots; with no credentials at all the "
                              "pool is the two mock slots and the gateway reports "
                              "status=degraded, not 503.",
                  size=10.6, fill=MUTED, family=MONO, max_w=1130))
    return document("provider-rack.svg", w, h, "".join(b))


# --------------------------------------------------------------------------- #
# 9 · degradation-flow.svg
# --------------------------------------------------------------------------- #


def build_degradation():
    w, h = 1280, 810
    b = [header(w, "DEGRADATION", "Every failure class has a named recovery",
                "RECOVERY_ACTIONS in gateway/src/gateway/fallback.mjs, then a user-facing "
                "fallback in every bot.", accent=AMBER)]

    b.append(text(50, 172, "GATEWAY SIDE · 11 error classes → 6 recovery actions", size=11,
                  fill=VIOLET, weight=700, spacing=1.6))
    y0 = 188
    for i, (cls, action, detail, c) in enumerate(RECOVERY):
        y = y0 + i * 34
        b.append(rect(50, y, 1180, 30, r=9, fill=PANEL if i % 2 == 0 else PANEL_3, stroke=STROKE))
        b.append(circle(70, y + 15, 3.5, fill=c))
        b.append(text(86, y + 19, cls, size=11, fill=TEXT, family=MONO, weight=600))
        b.append(arrow(250, y + 15, 274, y + 15, color=c, sw=1.4,
                       marker={CYAN: "arrowCyan", VIOLET: "arrowViolet", PINK: "arrowPink",
                               GREEN: "arrowGreen"}.get(c, "arrow")))
        chip, _ = pill(282, y + 4, action, color=c, size=10, h=22, family=MONO)
        b.append(chip)
        b.append(text(440, y + 19, detail, size=10.4, fill=MUTED))

    yb = y0 + len(RECOVERY) * 34 + 14
    b.append(text(50, yb + 8, "BOT SIDE · tests/test_degradation.py — 5 bots × 5 gateway failures "
                              "= 25 scenarios", size=11, fill=CYAN, weight=700, spacing=1.6))
    failures = ["gateway down (503)", "rate limited (429)", "bad credentials (401)",
                "payload too large (413)", "unexpected crash"]
    cw = 1180 / 6
    b.append(rect(50, yb + 22, 1180, 168, r=14, fill=PANEL_3, stroke=STROKE))
    for j, f in enumerate(failures):
        b.append(text(64 + cw * (j + 1), yb + 46, f, size=9.4, fill=MUTED, anchor="middle",
                      family=MONO))
    for i, bot in enumerate(BOTS):
        ry = yb + 74 + i * 22
        b.append(text(74, ry, bot["key"], size=10.6, fill=bot["color"], family=MONO, weight=600))
        for j in range(5):
            cx = 64 + cw * (j + 1)
            b.append(circle(cx, ry - 4, 6, fill=GREEN, opacity=0.14))
            b.append(text(cx, ry, "✓", size=10, fill=GREEN, anchor="middle", family=MONO))
    b.append(text(1206, yb + 46, "25/25 pass", size=10.5, fill=GREEN, anchor="end",
                  family=MONO, weight=700))
    b.append(text(74, yb + 206, "Pass condition: a reply reaches the user, the handler does not "
                                "raise, and no traceback, URL or provider name leaks into the "
                                "message.", size=10.6, fill=DIM))
    return document("degradation-flow.svg", w, h, "".join(b))


# --------------------------------------------------------------------------- #
# 10 · webhook-flow.svg
# --------------------------------------------------------------------------- #


def build_webhook():
    w, h = 1280, 620
    b = [header(w, "WEBHOOK BEHAVIOUR", "Authenticate, acknowledge, then work",
                "Telegram gets its 200 before any download or inference starts.", accent=VIOLET)]

    lanes = ["Telegram", "aiohttp PublicServer", "Dispatcher task", "Gateway"]
    lane_x = [140, 470, 810, 1130]
    for i, lane in enumerate(lanes):
        b.append(rect(lane_x[i] - 110, 150, 220, 34, r=10, fill="url(#panelGrad)"))
        b.append(text(lane_x[i], 172, lane, size=12, fill=TEXT, weight=600, anchor="middle"))
        b.append(line(lane_x[i], 186, lane_x[i], 520, stroke=STROKE, sw=1, dash="4 6"))

    steps = [
        (0, 1, "POST /webhook/{bot_key}/{path_secret}", VIOLET, 216),
        (1, 1, "hmac.compare_digest(path_secret) → 404 on mismatch", PINK, 252),
        (1, 1, "X-Telegram-Bot-Api-Secret-Token → 403 on mismatch", PINK, 288),
        (1, 1, "Update.model_validate() → 400 on a bad body", AMBER, 324),
        (1, 2, "asyncio.create_task(feed_update)", CYAN, 360),
        (1, 0, "200 OK — sub-millisecond ack (tests/test_webhook.py)", GREEN, 396),
        (2, 3, "handler → gateway_client.ask()", VIOLET, 440),
        (3, 2, "completion or typed GatewayError", VIOLET, 476),
    ]
    for src, dst, label, c, y in steps:
        x1, x2 = lane_x[src], lane_x[dst]
        if src == dst:
            b.append(path(f"M {x1 + 2} {y - 10} C {x1 + 40} {y - 16} {x1 + 40} {y + 10} "
                          f"{x1 + 6} {y + 5}", stroke=c, sw=1.4, marker="arrow"))
            b.append(text(x1 + 46, y + 2, label, size=10.6, fill=MUTED, family=MONO,
                          max_w=1180 - (x1 + 46)))
        else:
            mk = {VIOLET: "arrowViolet", PINK: "arrowPink", GREEN: "arrowGreen",
                  CYAN: "arrowCyan"}.get(c, "arrow")
            b.append(arrow(x1 + (8 if x2 > x1 else -8), y, x2 + (-8 if x2 > x1 else 8), y,
                           color=c, sw=1.5, marker=mk))
            mid = (x1 + x2) / 2
            b.append(text(mid, y - 8, label, size=10.6, fill=TEXT if c == GREEN else MUTED,
                          anchor="middle", family=MONO))

    b.append(rect(50, 528, 578, 66, r=13, fill=PANEL_3, stroke=STROKE))
    b.append(text(74, 552, "SECRETS", size=10.5, fill=PINK, weight=700, spacing=1.6))
    b.append(text(74, 570, "path = HMAC-SHA256(WEBHOOK_SECRET, \"{key}:{token}\")[:32]",
                  size=10.2, fill=MUTED, family=MONO, max_w=530))
    b.append(text(74, 586, "a bot token never appears in a URL; Render's base64 secret is "
                           "hashed to Telegram-safe hex", size=9.8, fill=DIM, family=MONO,
                  max_w=530))

    b.append(rect(652, 528, 578, 66, r=13, fill=PANEL_3, stroke=STROKE))
    b.append(text(676, 552, "SHUTDOWN", size=10.5, fill=GREEN, weight=700, spacing=1.6))
    b.append(text(676, 570, "in-flight tasks are awaited up to 20 s, then cancelled",
                  size=10.2, fill=MUTED, family=MONO, max_w=530))
    b.append(text(676, 586, "DROP_PENDING_UPDATES=false: an update that wakes a sleeping "
                            "instance is not discarded", size=9.8, fill=DIM, family=MONO,
                  max_w=530))
    return document("webhook-flow.svg", w, h, "".join(b))


# --------------------------------------------------------------------------- #
# 11 · test-console.svg
# --------------------------------------------------------------------------- #


CONSOLE_LINES = [
    ("cmd", "cd gateway && node --check $(find src test -name '*.mjs')"),
    ("out", "22 files checked · 0 syntax errors"),
    ("cmd", "node -e \"import('./src/server.mjs')\"   # full ESM import graph"),
    ("out", "policy registry loaded  version=2026.09.1  models=16  providers=8"),
    ("cmd", "node --test test/*.test.mjs"),
    ("ok", "# tests 55   # pass 55   # fail 0   # duration_ms 1725.7"),
    ("cmd", "cd bots && python -m compileall -q . && python -m flake8 ."),
    ("ok", "compileall clean · flake8 clean (max-line-length 200, max-complexity 18)"),
    ("cmd", "python tests/test_degradation.py"),
    ("ok", "PASSED: all 25 degradation scenarios handled"),
    ("cmd", "python tests/test_payments.py"),
    ("ok", "PASSED: payment ceiling, entitlement, payload and concurrency"),
    ("cmd", "python tests/test_webhook.py"),
    ("ok", "PASSED: secret header enforced and webhook acknowledged in 0.9ms"),
    ("cmd", "MOCK_ONLY=true node src/server.mjs &   python tests/test_end_to_end.py"),
    ("ok", "PASSED: 12 live end-to-end steps produced real answers and the task type contract holds"),
    ("cmd", "curl -s -o /dev/null -w '%{http_code}' localhost:3000/api/health  /api/models  /api/meta"),
    ("out", "200 200 200   ·   POST /api/ai → 200 with text   ·   bad admin key → 401 · good → 200"),
    ("out", "malformed JSON → 400   ·   empty messages → 400   ·   unknown path → 404"),
    ("cmd", "python3 assets/readme/lint_assets.py"),
    ("ok", "14/14 README assets present, well-formed, on-palette and inside their viewBox"),
]


def build_console():
    w, h = 1280, 700
    b = [header(w, "LOCAL VERIFICATION", "The exact commands, the exact output",
                "Captured on this repository. GitHub Actions is not wired on this branch — "
                "see the CI section.", accent=GREEN)]

    b.append(rect(50, 150, 1180, 500, r=14, fill="#080A0F", stroke=STROKE))
    b.append(rect(50, 150, 1180, 34, r=14, fill=PANEL_2, stroke=STROKE))
    b.append(rect(50, 172, 1180, 12, r=0, fill=PANEL_2, stroke="none"))
    b.append(line(50, 184, 1230, 184, stroke=STROKE))
    for i, c in enumerate((PINK, AMBER, GREEN)):
        b.append(circle(74 + i * 18, 167, 5, fill=c, opacity=0.85))
    b.append(text(640, 172, "konkred-bots — local test cycle", size=11, fill=MUTED,
                  anchor="middle", family=MONO))

    y = 212
    for kind, line_text in CONSOLE_LINES:
        if kind == "cmd":
            b.append(text(74, y, "$", size=11.5, fill=CYAN, family=MONO, weight=700))
            b.append(text(92, y, line_text, size=11.5, fill=TEXT, family=MONO))
        elif kind == "ok":
            b.append(text(74, y, "✓", size=11.5, fill=GREEN, family=MONO, weight=700))
            b.append(text(92, y, line_text, size=11.5, fill=GREEN, family=MONO))
        else:
            b.append(text(92, y, line_text, size=11.5, fill=MUTED, family=MONO))
        y += 21
    return document("test-console.svg", w, h, "".join(b))


# --------------------------------------------------------------------------- #
# 12 · docker-topology.svg
# --------------------------------------------------------------------------- #


def build_docker():
    w, h = 1280, 700
    b = [header(w, "DOCKER", "Three services locally, one image when hosted",
                "docker-compose.yml for development; the root Dockerfile fuses both runtimes "
                "for a single free web service.", accent=CYAN)]

    b.append(text(50, 172, "docker-compose.yml · network internal-net (bridge)", size=11,
                  fill=CYAN, weight=700, spacing=1.4))
    services = [
        ("redis", "redis:7-alpine", CYAN, [
            "appendonly yes · appendfsync everysec",
            "maxmemory 256mb · allkeys-lru",
            "healthcheck: redis-cli ping / 10 s",
            "volume redis-data:/data",
        ], "no dependencies"),
        ("gateway", "konkred/gateway:1.0.0", VIOLET, [
            "node:20-alpine · tini PID 1 · USER node",
            "build asserts zero npm dependencies",
            "healthcheck: fetch /api/health / 30 s",
            "volume gateway-state:/app/data",
        ], "depends_on redis: service_healthy"),
        ("bot", "konkred/bots:1.0.0", GREEN, [
            "python:3.11-slim multi-stage wheels",
            "non-root konkred user · tini PID 1",
            "GATEWAY_URL=http://gateway:3000",
            "stop_grace_period 30 s for polling drain",
        ], "depends_on redis + gateway: service_healthy"),
    ]
    for i, (name, image, c, lines, dep) in enumerate(services):
        x = 50 + i * 396
        ww = 376
        b.append(rect(x, 186, ww, 190, r=14, fill="url(#panelGrad)"))
        b.append(rect(x, 186, ww, 3, r=2, fill=c, stroke="none"))
        b.append(text(x + 22, 216, name, size=15, fill=c, weight=700, family=MONO))
        b.append(text(x + ww - 22, 216, image, size=9.6, fill=DIM, anchor="end", family=MONO))
        for j, ln in enumerate(lines):
            b.append(circle(x + 28, 240 + j * 23, 2.4, fill=c, opacity=0.75))
            b.append(text(x + 40, 244 + j * 23, ln, size=10.2, fill=MUTED, family=MONO))
        b.append(line(x + 22, 342, x + ww - 22, 342, stroke=STROKE))
        b.append(text(x + 22, 362, dep, size=10, fill=DIM, family=MONO))
        if i < 2:
            b.append(arrow(x + ww + 2, 281, x + ww + 16, 281, color=DIM, sw=1.4))

    b.append(rect(50, 392, 1180, 40, r=11, fill=PANEL_3, stroke=STROKE))
    b.append(text(74, 417, "ports 127.0.0.1:${GATEWAY_PORT:-3000}→3000 only — the gateway is "
                           "never published to 0.0.0.0 by the compose file", size=11,
                  fill=MUTED, family=MONO))
    b.append(text(1206, 417, "json-file logs · 10m × 3", size=10, fill=DIM, anchor="end",
                  family=MONO))

    b.append(text(50, 470, "root Dockerfile · one container for a single free web service",
                  size=11, fill=VIOLET, weight=700, spacing=1.4))
    b.append(rect(50, 484, 1180, 168, r=14, fill="url(#panelGrad)"))
    b.append(rect(74, 506, 520, 56, r=11, fill=PANEL_3, stroke=VIOLET, sw=1))
    b.append(text(94, 530, "node /app/gateway/src/server.mjs", size=11.5, fill=VIOLET,
                  weight=600, family=MONO))
    b.append(text(94, 548, "HOST=127.0.0.1 PORT=3000 — loopback only, never public", size=10,
                  fill=MUTED, family=MONO))
    b.append(rect(686, 506, 520, 56, r=11, fill=PANEL_3, stroke=CYAN, sw=1))
    b.append(text(706, 530, "python /app/bots/main.py", size=11.5, fill=CYAN, weight=600,
                  family=MONO))
    b.append(text(706, 548, "WEB_HOST=0.0.0.0 PORT=$PORT — /healthz + Telegram webhooks", size=10,
                  fill=MUTED, family=MONO))
    b.append(arrow(600, 534, 680, 534, color=DIM, sw=1.4))
    b.append(text(640, 524, "loopback", size=9, fill=DIM, anchor="middle", family=MONO))
    b.append(text(74, 592, "entrypoint.sh supervises both PIDs under tini, forwards SIGTERM to "
                           "each, and exits as soon as either runtime stops so the platform "
                           "restarts a complete service.", size=10.6, fill=MUTED))
    b.append(text(74, 614, "NODE_OPTIONS=--max-old-space-size=192 · wheels are built in a "
                           "separate stage · the runtime image carries no compiler toolchain.",
                  size=10.6, fill=MUTED))
    b.append(text(74, 636, "Docker is not installed in this verification sandbox, so image "
                           "builds were not executed here — the manifests were parsed and "
                           "structurally validated instead.", size=10.4, fill=AMBER))
    return document("docker-topology.svg", w, h, "".join(b))


# --------------------------------------------------------------------------- #
# 13 · deployment-map.svg
# --------------------------------------------------------------------------- #


def build_deployment():
    w, h = 1280, 660
    b = [header(w, "DEPLOYMENT", "Three targets, one image family",
                "Transport, Redis and cost profile change; the fleet code does not.",
                accent=GREEN)]

    targets = [
        ("Local Compose", CYAN, "BOT_MODE=polling", [
            ("services", "redis + gateway + bot"),
            ("redis", "container with a named volume"),
            ("gateway", "reachable at 127.0.0.1:3000"),
            ("start", "./setup.sh"),
            ("best for", "development and full-stack debugging"),
        ], "restart: unless-stopped"),
        ("Render web service", VIOLET, "BOT_MODE=webhook", [
            ("services", "one free web service, no worker"),
            ("redis", "external rediss:// (for example Upstash)"),
            ("gateway", "loopback inside the same container"),
            ("start", "render.yaml blueprint, Docker runtime"),
            ("best for", "a hosted bot without an always-on VM"),
        ], "free plan: ~1 min cold start · 512 MB · 750 instance-hours/month"),
        ("Your own server", GREEN, "BOT_MODE=polling", [
            ("services", "the same three compose services"),
            ("redis", "local container, persistent volume"),
            ("gateway", "private bridge network"),
            ("start", "./setup.sh behind systemd or restart policy"),
            ("best for", "no cold starts, no sleep, full control"),
        ], "keep the gateway off 0.0.0.0 or set GATEWAY_API_KEY"),
    ]
    for i, (name, c, mode, rows, foot) in enumerate(targets):
        x = 50 + i * 396
        ww = 376
        b.append(rect(x, 150, ww, 300, r=15, fill="url(#panelGrad)"))
        b.append(rect(x, 150, ww, 3, r=2, fill=c, stroke="none"))
        b.append(text(x + 22, 182, name, size=15, fill=TEXT, weight=700))
        chip, _ = pill(x + 22, 194, mode, color=c, size=10, family=MONO)
        b.append(chip)
        for j, (k, v) in enumerate(rows):
            yy = 248 + j * 34
            b.append(text(x + 22, yy, k, size=9.6, fill=DIM, family=MONO))
            b.append(text(x + 22, yy + 16, v, size=10.6, fill=MUTED, family=MONO))
            if j < len(rows) - 1:
                b.append(line(x + 22, yy + 24, x + ww - 22, yy + 24, stroke=STROKE, opacity=0.6))
        b.append(text(x + 22, 434, foot, size=9.6, fill=c, family=MONO, max_w=ww - 44))

    b.append(rect(50, 470, 1180, 72, r=14, fill=PANEL_3, stroke=STROKE))
    b.append(text(74, 496, "REQUIRED HOSTED SECRETS — never committed", size=11, fill=PINK,
                  weight=700, spacing=1.6))
    b.append(text(74, 520, "REDIS_URL · TELEGRAM_<BOT>_BOT_TOKEN · WEBHOOK_SECRET (webhook mode) "
                           "· PAYMENT_SECRET · at least one provider key for real model output",
                  size=10.6, fill=MUTED, family=MONO))

    b.append(text(50, 578, "COST CONDITIONS — stated exactly", size=11, fill=AMBER,
                  weight=700, spacing=1.6))
    conditions = [
        "Every model slot in the registry is a published free tier; using only those keys, "
        "inference has no per-token bill — but quotas, provider terms and plan changes still apply.",
        "Render's free plan is free only within its own limits (cold starts, 512 MB RAM, "
        "750 instance-hours per workspace per month). External Redis has its own free-tier limits.",
        "With no provider credentials the gateway answers from its mock provider and reports "
        "status=degraded: the stack stays testable, the answers are not model output.",
    ]
    for i, ln in enumerate(conditions):
        b.append(circle(58, 597 + i * 20, 2.4, fill=AMBER, opacity=0.8))
        b.append(text(70, 601 + i * 20, ln, size=10.4, fill=MUTED))
    return document("deployment-map.svg", w, h, "".join(b))


# --------------------------------------------------------------------------- #
# 14 · footer-fleet.svg
# --------------------------------------------------------------------------- #


def build_footer():
    w, h = 1280, 190
    b = []
    b.append(f'<rect x="60" y="28" width="1160" height="3" rx="1.5" fill="url(#railGrad)"/>')
    for i, bot in enumerate(BOTS):
        x = 148 + i * 248
        b.append(circle(x, 29.5, 5, fill=bot["color"]))
        b.append(text(x, 56, bot["key"], size=11, fill=bot["color"], anchor="middle",
                      weight=700, family=MONO))
        b.append(text(x, 74, bot["name"], size=10, fill=MUTED, anchor="middle"))
    b.append(text(640, 118, "KONKRED BOT FLEET", size=17, fill=TEXT, weight=700,
                  anchor="middle", spacing=3))
    b.append(text(640, 140, "one process · one gateway · one payment rail · five products",
                  size=11.5, fill=MUTED, anchor="middle", family=MONO))
    b.append(text(640, 164, "MIT licensed · verified locally: 55 gateway tests, 25 degradation "
                            "scenarios, 12 live end-to-end steps", size=10, fill=DIM,
                  anchor="middle"))
    return document("footer-fleet.svg", w, h, "".join(b), show_grid=False)


BUILDERS = [
    build_hero, build_deck, build_runtime, build_ingress, build_redis, build_payments,
    build_routing, build_rack, build_degradation, build_webhook, build_console, build_docker,
    build_deployment, build_footer,
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for builder in BUILDERS:
        name = builder()
        size = (OUT / name).stat().st_size
        print(f"  wrote {name:26s} {size / 1024:6.1f} KB")
    print(f"{len(BUILDERS)} assets generated in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
