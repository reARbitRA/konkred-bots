"""Shared infrastructure for the Konkred multi-bot daemon.

Exposes configuration, the pooled gateway client, Redis-backed conversation
memory and the Telegram-safe formatting helpers used by all five bots.
"""

from .config import (
    BOT_SPECS,
    BotSpec,
    Settings,
    configure_logging,
    get_active_bots,
    get_bot,
    settings,
)
from .gateway_client import (
    GatewayClient,
    GatewayError,
    gateway,
    inline_data_part,
    text_part,
)
from .history import HistoryManager
from .utils import (
    UNEXPECTED_ERROR,
    clean_model_output,
    escape_html,
    extract_json_block,
    format_duration,
    humanize_bytes,
    send_long_message,
    split_telegram_message,
    truncate,
)

__all__ = [
    "BOT_SPECS",
    "UNEXPECTED_ERROR",
    "BotSpec",
    "GatewayClient",
    "GatewayError",
    "HistoryManager",
    "Settings",
    "clean_model_output",
    "configure_logging",
    "escape_html",
    "extract_json_block",
    "format_duration",
    "gateway",
    "get_active_bots",
    "get_bot",
    "humanize_bytes",
    "inline_data_part",
    "send_long_message",
    "settings",
    "split_telegram_message",
    "text_part",
    "truncate",
]
