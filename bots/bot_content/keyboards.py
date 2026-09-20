"""Inline keyboards for the Viral Hook Architect."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CB_PREFIX = "content"

PLATFORMS: dict[str, str] = {
    "reels": "📸 Instagram Reels",
    "tiktok": "🎵 TikTok",
    "shorts": "▶️ YouTube Shorts",
}

TONES: dict[str, str] = {
    "educational": "🎓 Educational",
    "entertaining": "😄 Entertaining",
    "inspirational": "🔥 Inspirational",
    "contrarian": "⚡ Contrarian",
}


def main_menu() -> InlineKeyboardMarkup:
    """Idle menu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎬 Create a new script", callback_data=f"{CB_PREFIX}:new")],
            [InlineKeyboardButton(text="💡 Hook formulas cheat-sheet", callback_data=f"{CB_PREFIX}:formulas")],
        ]
    )


def platform_menu() -> InlineKeyboardMarkup:
    """Platform selection step of the FSM."""
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"{CB_PREFIX}:platform:{key}")]
        for key, label in PLATFORMS.items()
    ]
    rows.append([InlineKeyboardButton(text="❌ Cancel", callback_data=f"{CB_PREFIX}:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tone_menu() -> InlineKeyboardMarkup:
    """Optional tone selection step."""
    items = list(TONES.items())
    rows = [
        [
            InlineKeyboardButton(text=items[i][1], callback_data=f"{CB_PREFIX}:tone:{items[i][0]}"),
            InlineKeyboardButton(text=items[i + 1][1], callback_data=f"{CB_PREFIX}:tone:{items[i + 1][0]}"),
        ]
        for i in range(0, len(items) - 1, 2)
    ]
    rows.append([InlineKeyboardButton(text="❌ Cancel", callback_data=f"{CB_PREFIX}:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def result_menu() -> InlineKeyboardMarkup:
    """Follow-up actions on a generated script."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Regenerate", callback_data=f"{CB_PREFIX}:regen"),
                InlineKeyboardButton(text="🪝 More hooks", callback_data=f"{CB_PREFIX}:hooks"),
            ],
            [
                InlineKeyboardButton(text="🎞 Shot list", callback_data=f"{CB_PREFIX}:shotlist"),
                InlineKeyboardButton(text="📅 7-day series", callback_data=f"{CB_PREFIX}:series"),
            ],
            [InlineKeyboardButton(text="🎬 New topic", callback_data=f"{CB_PREFIX}:new")],
        ]
    )


__all__ = ["CB_PREFIX", "PLATFORMS", "TONES", "main_menu", "platform_menu", "result_menu", "tone_menu"]
