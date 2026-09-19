"""Inline keyboards for the Alpha Scanner."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CB_PREFIX = "crypto"

QUICK_TICKERS = ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE")


def main_menu() -> InlineKeyboardMarkup:
    """Idle menu with quick-scan shortcuts."""
    rows = [
        [
            InlineKeyboardButton(text=f"🔍 {ticker}", callback_data=f"{CB_PREFIX}:scan:{ticker}")
            for ticker in QUICK_TICKERS[:3]
        ],
        [
            InlineKeyboardButton(text=f"🔍 {ticker}", callback_data=f"{CB_PREFIX}:scan:{ticker}")
            for ticker in QUICK_TICKERS[3:]
        ],
        [
            InlineKeyboardButton(text="📰 Market news", callback_data=f"{CB_PREFIX}:news"),
            InlineKeyboardButton(text="😱 Fear & Greed", callback_data=f"{CB_PREFIX}:feargreed"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def report_menu(ticker: str) -> InlineKeyboardMarkup:
    """Follow-up actions on a scan report."""
    safe = ticker.upper()[:12]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Rescan", callback_data=f"{CB_PREFIX}:scan:{safe}"),
                InlineKeyboardButton(text="⚖️ Bull vs Bear", callback_data=f"{CB_PREFIX}:debate:{safe}"),
            ],
            [
                InlineKeyboardButton(text="🛡 Risk checklist", callback_data=f"{CB_PREFIX}:risk:{safe}"),
                InlineKeyboardButton(text="📚 Explain simply", callback_data=f"{CB_PREFIX}:eli5:{safe}"),
            ],
            [InlineKeyboardButton(text="🏠 Menu", callback_data=f"{CB_PREFIX}:menu")],
        ]
    )


def news_menu() -> InlineKeyboardMarkup:
    """Follow-ups on the news digest."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Refresh", callback_data=f"{CB_PREFIX}:news")],
            [InlineKeyboardButton(text="🏠 Menu", callback_data=f"{CB_PREFIX}:menu")],
        ]
    )


__all__ = ["CB_PREFIX", "QUICK_TICKERS", "main_menu", "news_menu", "report_menu"]
