"""Konkred bots - centralised configuration.

Loads the ``.env`` file once, exposes typed settings and reports which of the
five bots actually have a token configured, so partial deployments (e.g. only
the IELTS bot) start cleanly instead of crashing on a missing variable.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

# --------------------------------------------------------------------------- #
# .env loading
# --------------------------------------------------------------------------- #

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
BOTS_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

for _candidate in (
    Path(os.getenv("DOTENV_PATH", "")) if os.getenv("DOTENV_PATH") else None,
    REPO_ROOT / ".env",
    BOTS_ROOT / ".env",
):
    if _candidate and _candidate.is_file():
        # Real environment variables always win over the file.
        load_dotenv(_candidate, override=False)


def _str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _int(name: str, default: int) -> int:
    try:
        return int(float(_str(name, str(default))))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_str(name, str(default)))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = _str(name, "").lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "enabled"}


# --------------------------------------------------------------------------- #
# Bot registry
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BotSpec:
    """Static description of one Telegram bot in the ecosystem."""

    key: str
    token_env: str
    title: str
    emoji: str
    description: str
    router_path: str
    history_prefix: str

    @property
    def token(self) -> str:
        return _str(self.token_env)

    @property
    def enabled(self) -> bool:
        return bool(self.token)


BOT_SPECS: Final[tuple[BotSpec, ...]] = (
    BotSpec(
        key="voice",
        token_env="TELEGRAM_VOICE_BOT_TOKEN",
        title="Voice-to-Action",
        emoji="🎙",
        description="Transcribes voice notes and extracts summaries, decisions and action items.",
        router_path="bot_voice.handlers:router",
        history_prefix="voice",
    ),
    BotSpec(
        key="pdf",
        token_env="TELEGRAM_PDF_BOT_TOKEN",
        title="Deep Document Assistant",
        emoji="📄",
        description="Reads PDF/DOCX/TXT/MD files and builds summaries, quizzes, flashcards and risk reports.",
        router_path="bot_pdf.handlers:router",
        history_prefix="pdf",
    ),
    BotSpec(
        key="ielts",
        token_env="TELEGRAM_IELTS_BOT_TOKEN",
        title="IELTS Speaking Coach",
        emoji="🎓",
        description="Runs a 3-part mock interview and scores the four official IELTS criteria.",
        router_path="bot_ielts.handlers:router",
        history_prefix="ielts",
    ),
    BotSpec(
        key="content",
        token_env="TELEGRAM_CONTENT_BOT_TOKEN",
        title="Viral Hook Architect",
        emoji="🎬",
        description="Writes retention hooks, beat-by-beat short-form scripts and SEO captions.",
        router_path="bot_content.handlers:router",
        history_prefix="content",
    ),
    BotSpec(
        key="crypto",
        token_env="TELEGRAM_CRYPTO_BOT_TOKEN",
        title="Alpha Scanner",
        emoji="📊",
        description="Scans tickers and topics for sentiment, key drivers, whale activity and risk.",
        router_path="bot_crypto.handlers:router",
        history_prefix="crypto",
    ),
)


def get_active_bots() -> list[BotSpec]:
    """Return only the bots whose token is configured (allows partial deploys)."""
    return [spec for spec in BOT_SPECS if spec.enabled]


def get_bot(key: str) -> BotSpec | None:
    """Look up a bot spec by its short key."""
    for spec in BOT_SPECS:
        if spec.key == key:
            return spec
    return None


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Settings:
    """Runtime settings shared by every bot."""

    # Gateway
    gateway_url: str = field(default_factory=lambda: _str("GATEWAY_URL", "http://gateway:3000").rstrip("/"))
    gateway_api_key: str = field(default_factory=lambda: _str("GATEWAY_API_KEY"))
    gateway_timeout: float = field(default_factory=lambda: _float("GATEWAY_TIMEOUT", 120.0))
    gateway_max_retries: int = field(default_factory=lambda: _int("GATEWAY_MAX_RETRIES", 2))

    # Redis
    redis_url: str = field(default_factory=lambda: _str("REDIS_URL", "redis://redis:6379/0"))

    # Conversation memory
    history_turns: int = field(default_factory=lambda: _int("HISTORY_TURNS", 10))
    history_ttl: int = field(default_factory=lambda: _int("HISTORY_TTL", 86400))

    # Telegram payload limits
    max_message_length: int = field(default_factory=lambda: _int("MAX_MESSAGE_LENGTH", 4000))
    max_file_mb: int = field(default_factory=lambda: _int("MAX_FILE_MB", 20))

    # Logging / behaviour
    log_level: str = field(default_factory=lambda: _str("LOG_LEVEL", "INFO").upper())
    drop_pending_updates: bool = field(default_factory=lambda: _bool("DROP_PENDING_UPDATES", True))

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024

    @property
    def history_messages(self) -> int:
        """A 'turn' is one user message plus one assistant reply."""
        return self.history_turns * 2


settings: Final[Settings] = Settings()


def configure_logging(level: str | None = None) -> None:
    """Install a single, consistent log format for the whole daemon."""
    logging.basicConfig(
        level=getattr(logging, (level or settings.log_level), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    # Third-party libraries are noisy at INFO.
    for noisy in ("httpx", "httpcore", "aiogram.event", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


__all__ = [
    "BOT_SPECS",
    "BotSpec",
    "REPO_ROOT",
    "Settings",
    "configure_logging",
    "get_active_bots",
    "get_bot",
    "settings",
]
