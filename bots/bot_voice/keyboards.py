"""Inline keyboards for the Voice-to-Action bot."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Callback data namespace: voice:<action>[:<arg>]
CB_PREFIX = "voice"


def main_menu() -> InlineKeyboardMarkup:
    """Menu shown by /start."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❓ How it works", callback_data=f"{CB_PREFIX}:help")],
            [InlineKeyboardButton(text="🧹 Clear my history", callback_data=f"{CB_PREFIX}:clear")],
        ]
    )


def result_actions() -> InlineKeyboardMarkup:
    """Follow-up actions offered after a successful transcription."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 Action items only", callback_data=f"{CB_PREFIX}:actions"),
                InlineKeyboardButton(text="✉️ Draft a reply", callback_data=f"{CB_PREFIX}:reply"),
            ],
            [
                InlineKeyboardButton(text="🌍 Translate to English", callback_data=f"{CB_PREFIX}:translate"),
                InlineKeyboardButton(text="📝 Meeting minutes", callback_data=f"{CB_PREFIX}:minutes"),
            ],
        ]
    )


def retry_keyboard() -> InlineKeyboardMarkup:
    """Offered when a transcription fails."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔄 Try again", callback_data=f"{CB_PREFIX}:retry")]]
    )


__all__ = ["CB_PREFIX", "main_menu", "result_actions", "retry_keyboard"]
