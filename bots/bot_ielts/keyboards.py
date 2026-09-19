"""Inline keyboards for the IELTS Speaking Coach."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CB_PREFIX = "ielts"


def main_menu() -> InlineKeyboardMarkup:
    """Idle-state menu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎤 Start full mock test", callback_data=f"{CB_PREFIX}:start")],
            [
                InlineKeyboardButton(text="1️⃣ Part 1 only", callback_data=f"{CB_PREFIX}:part:1"),
                InlineKeyboardButton(text="2️⃣ Part 2 only", callback_data=f"{CB_PREFIX}:part:2"),
                InlineKeyboardButton(text="3️⃣ Part 3 only", callback_data=f"{CB_PREFIX}:part:3"),
            ],
            [InlineKeyboardButton(text="📊 Band descriptors", callback_data=f"{CB_PREFIX}:bands")],
        ]
    )


def exam_controls() -> InlineKeyboardMarkup:
    """Controls available while the exam is running."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏭ Skip question", callback_data=f"{CB_PREFIX}:skip"),
                InlineKeyboardButton(text="🛑 End test", callback_data=f"{CB_PREFIX}:stop"),
            ]
        ]
    )


def evaluation_menu() -> InlineKeyboardMarkup:
    """Shown with the final band-score report."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔁 Take another test", callback_data=f"{CB_PREFIX}:start")],
            [
                InlineKeyboardButton(text="📈 How to improve", callback_data=f"{CB_PREFIX}:improve"),
                InlineKeyboardButton(text="✍️ Model answers", callback_data=f"{CB_PREFIX}:model"),
            ],
        ]
    )


__all__ = ["CB_PREFIX", "evaluation_menu", "exam_controls", "main_menu"]
