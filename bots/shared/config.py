"""Konkred bots - centralised, environment-driven configuration.

The same Python process can run in two modes:

* ``webhook`` — an aiohttp server receives Telegram updates (Render).
* ``polling`` — aiogram long-polls Telegram while aiohttp only serves health
  endpoints (local Docker Compose and Hugging Face Spaces).

Only bots whose token is configured are started, so partial deployments boot
cleanly. Secrets always come from the environment or an untracked ``.env``.
"""

from __future__ import annotations

import logging
import os
import re
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


def _int_tuple(name: str) -> tuple[int, ...]:
    values: list[int] = []
    for raw in _str(name).replace(",", " ").split():
        try:
            values.append(int(raw))
        except ValueError:
            continue
    return tuple(dict.fromkeys(values))


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

    # Gateway. The root unified container starts Node on this loopback address.
    gateway_url: str = field(default_factory=lambda: _str("GATEWAY_URL", "http://127.0.0.1:3000").rstrip("/"))
    gateway_api_key: str = field(default_factory=lambda: _str("GATEWAY_API_KEY"))
    gateway_timeout: float = field(default_factory=lambda: _float("GATEWAY_TIMEOUT", 120.0))
    gateway_max_retries: int = field(default_factory=lambda: _int("GATEWAY_MAX_RETRIES", 2))

    # External Redis. Use a rediss:// Upstash endpoint in hosted deployments.
    # The localhost fallback keeps direct local development intuitive; Compose
    # explicitly overrides it with redis://redis:6379/0.
    redis_url: str = field(default_factory=lambda: _str("REDIS_URL", "redis://127.0.0.1:6379/0"))

    # Runtime transport.
    bot_mode: str = field(default_factory=lambda: _str("BOT_MODE", "polling").lower())
    web_host: str = field(default_factory=lambda: _str("WEB_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int("PORT", 7860))
    webhook_host: str = field(
        default_factory=lambda: _str("WEBHOOK_HOST", _str("RENDER_EXTERNAL_URL")).rstrip("/")
    )
    webhook_secret: str = field(default_factory=lambda: _str("WEBHOOK_SECRET"))

    # Revenue gate: five free AI-powered actions per user, per bot by default.
    payments_enabled: bool = field(default_factory=lambda: _bool("PAYMENTS_ENABLED", True))
    free_requests: int = field(default_factory=lambda: max(0, _int("FREE_REQUESTS", 5)))
    stars_price: int = field(default_factory=lambda: max(1, _int("STARS_PRICE", 100)))
    paid_access_days: int = field(default_factory=lambda: max(1, _int("PAID_ACCESS_DAYS", 30)))
    payment_secret: str = field(default_factory=lambda: _str("PAYMENT_SECRET"))
    usdt_wallet_address: str = field(default_factory=lambda: _str("USDT_WALLET_ADDRESS"))
    usdt_network: str = field(default_factory=lambda: _str("USDT_NETWORK", "TRC20"))
    usdt_price: str = field(default_factory=lambda: _str("USDT_PRICE", "2.99"))
    payment_admin_ids: tuple[int, ...] = field(default_factory=lambda: _int_tuple("PAYMENT_ADMIN_IDS"))
    payment_support: str = field(default_factory=lambda: _str("PAYMENT_SUPPORT"))

    # Conversation memory.
    history_turns: int = field(default_factory=lambda: _int("HISTORY_TURNS", 10))
    history_ttl: int = field(default_factory=lambda: _int("HISTORY_TTL", 86400))

    # Telegram payload limits.
    max_message_length: int = field(default_factory=lambda: _int("MAX_MESSAGE_LENGTH", 4000))
    max_file_mb: int = field(default_factory=lambda: _int("MAX_FILE_MB", 20))

    # Logging / behaviour.
    log_level: str = field(default_factory=lambda: _str("LOG_LEVEL", "INFO").upper())
    drop_pending_updates: bool = field(default_factory=lambda: _bool("DROP_PENDING_UPDATES", True))

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024

    @property
    def history_messages(self) -> int:
        """A 'turn' is one user message plus one assistant reply."""
        return self.history_turns * 2

    @property
    def paid_access_seconds(self) -> int:
        return self.paid_access_days * 86400


settings: Final[Settings] = Settings()


def validate_settings() -> list[str]:
    """Return human-readable configuration errors without exposing secrets."""
    errors: list[str] = []
    if settings.bot_mode not in {"polling", "webhook"}:
        errors.append("BOT_MODE must be either 'polling' or 'webhook'")
    if settings.bot_mode == "webhook":
        if not settings.webhook_host.startswith("https://"):
            errors.append("WEBHOOK_HOST (or RENDER_EXTERNAL_URL) must be an https:// URL in webhook mode")
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", settings.webhook_secret):
            errors.append(
                "WEBHOOK_SECRET must be 16-256 characters using only letters, digits, '_' or '-'"
            )
    return errors


def configure_logging(level: str | None = None) -> None:
    """Install a single, consistent log format for the whole daemon."""
    logging.basicConfig(
        level=getattr(logging, (level or settings.log_level), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    # Third-party libraries are noisy at INFO.
    for noisy in ("httpx", "httpcore", "aiogram.event", "asyncio", "aiohttp.access"):
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
    "validate_settings",
]
