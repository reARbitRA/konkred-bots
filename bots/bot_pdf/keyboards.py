"""Inline keyboards for the Deep Document Assistant."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CB_PREFIX = "pdf"


def document_actions() -> InlineKeyboardMarkup:
    """Primary analysis menu shown once a document is ingested."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Executive Summary", callback_data=f"{CB_PREFIX}:summary")],
            [InlineKeyboardButton(text="❓ Generate 5-Question Quiz", callback_data=f"{CB_PREFIX}:quiz")],
            [InlineKeyboardButton(text="📇 Create Flashcards", callback_data=f"{CB_PREFIX}:flashcards")],
            [InlineKeyboardButton(text="⚠️ Contract Risk Analysis", callback_data=f"{CB_PREFIX}:risk")],
            [
                InlineKeyboardButton(text="🔑 Key Terms", callback_data=f"{CB_PREFIX}:terms"),
                InlineKeyboardButton(text="📊 Data & Figures", callback_data=f"{CB_PREFIX}:data"),
            ],
        ]
    )


def quiz_answer_keyboard(question_index: int, options: int = 4) -> InlineKeyboardMarkup:
    """Answer buttons (A-D) for one quiz question."""
    letters = ["🅰️ A", "🅱️ B", "🅲 C", "🅳 D"][:options]
    buttons = [
        InlineKeyboardButton(
            text=letter,
            callback_data=f"{CB_PREFIX}:answer:{question_index}:{index}",
        )
        for index, letter in enumerate(letters)
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            buttons[:2],
            buttons[2:],
            [InlineKeyboardButton(text="🛑 Stop quiz", callback_data=f"{CB_PREFIX}:quizstop")],
        ]
    )


def next_question_keyboard(next_index: int) -> InlineKeyboardMarkup:
    """Advance to the next quiz question."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Next question", callback_data=f"{CB_PREFIX}:next:{next_index}")],
            [InlineKeyboardButton(text="🛑 Stop quiz", callback_data=f"{CB_PREFIX}:quizstop")],
        ]
    )


def finish_keyboard() -> InlineKeyboardMarkup:
    """Shown after the last quiz question."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔁 Back to document menu", callback_data=f"{CB_PREFIX}:menu")],
        ]
    )


__all__ = [
    "CB_PREFIX",
    "document_actions",
    "finish_keyboard",
    "next_question_keyboard",
    "quiz_answer_keyboard",
]
