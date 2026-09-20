#!/usr/bin/env python3
"""Konkred multi-bot orchestrator.

Runs up to five specialised Telegram bots concurrently inside a single asyncio
event loop and a single OS process:

* one shared ``redis.asyncio.Redis`` connection pool
* one ``RedisStorage`` per bot for FSM state, namespaced by bot key so the same
  user can hold independent state in every bot
* one pooled ``httpx.AsyncClient`` to the AI gateway, shared by all bots
* one ``HistoryManager`` per bot, namespaced by the bot's history prefix
* ``asyncio.gather`` over every dispatcher's ``start_polling``
* SIGINT/SIGTERM trapped for a graceful teardown of sessions, HTTP pool and Redis

Only bots whose token is configured are started, so a partial deployment (for
example just the IELTS bot) boots cleanly.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage
from aiogram.types import BotCommand
from redis.asyncio import Redis
from redis.exceptions import RedisError

from bot_content.handlers import router as content_router
from bot_crypto.handlers import router as crypto_router
from bot_ielts.handlers import router as ielts_router
from bot_pdf.handlers import router as pdf_router
from bot_voice.handlers import router as voice_router
from shared.config import BOT_SPECS, BotSpec, configure_logging, get_active_bots, settings
from shared.gateway_client import gateway
from shared.history import HistoryManager

logger = logging.getLogger("konkred.main")

# Static wiring from a bot key to its router. Keeping this explicit (rather than
# importing by string at runtime) means a typo is a startup ImportError instead
# of a silent "bot answers nothing" failure in production.
ROUTERS: dict[str, Router] = {
    "voice": voice_router,
    "pdf": pdf_router,
    "ielts": ielts_router,
    "content": content_router,
    "crypto": crypto_router,
}

# Commands registered with Telegram so each bot shows a proper menu.
COMMANDS: dict[str, list[BotCommand]] = {
    "voice": [
        BotCommand(command="start", description="Introduction and how it works"),
        BotCommand(command="help", description="Usage, formats and limits"),
        BotCommand(command="clear", description="Forget our conversation"),
    ],
    "pdf": [
        BotCommand(command="start", description="Introduction and how it works"),
        BotCommand(command="help", description="Supported formats and actions"),
        BotCommand(command="clear", description="Unload the current document"),
    ],
    "ielts": [
        BotCommand(command="test", description="Start a full 3-part mock test"),
        BotCommand(command="bands", description="Show the band descriptors"),
        BotCommand(command="stop", description="Abandon the current test"),
        BotCommand(command="help", description="How the scoring works"),
        BotCommand(command="clear", description="Forget my sessions"),
    ],
    "content": [
        BotCommand(command="create", description="Create a new short-form script"),
        BotCommand(command="formulas", description="Hook formula cheat-sheet"),
        BotCommand(command="cancel", description="Cancel the current flow"),
        BotCommand(command="help", description="How to get the best scripts"),
        BotCommand(command="clear", description="Forget my sessions"),
    ],
    "crypto": [
        BotCommand(command="scan", description="Full report on a ticker, e.g. /scan BTC"),
        BotCommand(command="sentiment", description="Sentiment on a topic or narrative"),
        BotCommand(command="news", description="Structural market briefing"),
        BotCommand(command="help", description="What I can and cannot do"),
        BotCommand(command="clear", description="Forget my sessions"),
    ],
}


class BotRuntime:
    """One fully wired bot: Bot + Dispatcher + namespaced state."""

    def __init__(self, spec: BotSpec, bot: Bot, dispatcher: Dispatcher) -> None:
        self.spec = spec
        self.bot = bot
        self.dispatcher = dispatcher
        self.username: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<BotRuntime {self.spec.key} @{self.username or 'unknown'}>"


async def build_runtime(spec: BotSpec, redis_client: Redis) -> BotRuntime:
    """Construct the Bot, Dispatcher, storage and injected dependencies."""
    bot = Bot(
        token=spec.token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )

    # Namespacing the storage prefix keeps each bot's FSM state independent even
    # though every bot shares one Redis database: the same Telegram user can be
    # mid-exam in the IELTS bot and mid-flow in the content bot simultaneously.
    # `with_bot_id` adds a second layer of isolation keyed on the bot's own id.
    storage = RedisStorage(
        redis=redis_client,
        key_builder=DefaultKeyBuilder(prefix=f"konkred:fsm:{spec.key}", with_bot_id=True),
        state_ttl=settings.history_ttl,
        data_ttl=settings.history_ttl,
    )

    dispatcher = Dispatcher(storage=storage)
    dispatcher.include_router(ROUTERS[spec.key])

    # Dependency injection: every handler that declares `history: HistoryManager`
    # receives this bot's namespaced manager.
    dispatcher["history"] = HistoryManager(redis_client, spec.history_prefix)
    dispatcher["bot_spec"] = spec

    return BotRuntime(spec, bot, dispatcher)


async def prepare(runtime: BotRuntime) -> bool:
    """Validate the token and register the command menu. Returns False on failure."""
    try:
        me = await runtime.bot.get_me()
    except Exception as exc:  # noqa: BLE001 - any Telegram error must be non-fatal
        logger.error(
            "%s bot failed to authenticate (check %s): %s",
            runtime.spec.key, runtime.spec.token_env, exc,
        )
        return False

    runtime.username = me.username
    try:
        await runtime.bot.set_my_commands(COMMANDS.get(runtime.spec.key, []))
    except Exception as exc:  # noqa: BLE001 - a menu failure must not stop the bot
        logger.warning("%s: could not set the command menu: %s", runtime.spec.key, exc)

    logger.info(
        "%s %-8s ready as @%s — %s",
        runtime.spec.emoji, runtime.spec.key, me.username, runtime.spec.title,
    )
    return True


async def run_bot(runtime: BotRuntime) -> None:
    """Poll one bot until cancelled, isolating its failures from the others."""
    try:
        await runtime.dispatcher.start_polling(
            runtime.bot,
            handle_signals=False,  # the orchestrator owns signal handling
            drop_pending_updates=settings.drop_pending_updates,
            allowed_updates=runtime.dispatcher.resolve_used_update_types(),
        )
    except asyncio.CancelledError:
        logger.info("%s: polling cancelled", runtime.spec.key)
        raise
    except Exception:  # noqa: BLE001 - one bot crashing must not kill the daemon
        logger.exception("%s: polling stopped unexpectedly", runtime.spec.key)


async def shutdown(runtimes: list[BotRuntime], redis_client: Redis | None) -> None:
    """Close every session, the HTTP pool and the Redis connection."""
    logger.info("shutting down…")

    for runtime in runtimes:
        try:
            await runtime.dispatcher.storage.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: storage close failed: %s", runtime.spec.key, exc)
        try:
            await runtime.bot.session.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: session close failed: %s", runtime.spec.key, exc)

    try:
        await gateway.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.warning("gateway client close failed: %s", exc)

    if redis_client is not None:
        try:
            await redis_client.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("redis close failed: %s", exc)

    logger.info("shutdown complete")


def install_signal_handlers() -> asyncio.Event:
    """Trap SIGINT/SIGTERM and return the Event they set.

    The dispatchers are started with ``handle_signals=False`` so that the
    orchestrator owns teardown: one signal stops every bot, closes the shared
    HTTP pool and the Redis connection exactly once.
    """
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def request_stop(signame: str) -> None:
        if not stop_event.is_set():
            logger.info("received %s — stopping", signame)
            stop_event.set()

    for signame in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, signame, None)
        if sig is None:  # pragma: no cover - Windows
            continue
        try:
            loop.add_signal_handler(sig, request_stop, signame)
        except NotImplementedError:  # pragma: no cover - non-Unix event loops
            signal.signal(sig, lambda *_, _name=signame: request_stop(_name))

    return stop_event


async def connect_redis() -> Redis | None:
    """Open and verify the shared Redis connection pool."""
    client: Redis | None = None
    try:
        client = Redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_keepalive=True,
            health_check_interval=30,
        )
        await client.ping()
    except (RedisError, OSError) as exc:
        logger.error("❌ cannot reach Redis at %s: %s", settings.redis_url, exc)
        if client is not None:
            await client.aclose()
        return None
    logger.info("✅ redis connected")
    return client


async def amain() -> int:
    """Async entry point. Returns the process exit code."""
    configure_logging()

    active = get_active_bots()
    if not active:
        logger.error(
            "No bot tokens configured. Set at least one of: %s",
            ", ".join(spec.token_env for spec in BOT_SPECS),
        )
        return 1

    logger.info("=" * 68)
    logger.info("Konkred multi-bot daemon starting")
    logger.info("active bots : %s", ", ".join(f"{spec.emoji} {spec.key}" for spec in active))
    logger.info("gateway     : %s", settings.gateway_url)
    logger.info("redis       : %s", settings.redis_url)
    logger.info("=" * 68)

    # --- Redis -------------------------------------------------------------
    redis_client = await connect_redis()
    if redis_client is None:
        return 1

    # --- Gateway -----------------------------------------------------------
    if not await gateway.wait_until_ready(attempts=30, delay=2.0):
        logger.warning("⚠️  gateway did not report ready; starting anyway (it may still be booting)")

    # --- Bots --------------------------------------------------------------
    runtimes: list[BotRuntime] = []
    for spec in active:
        runtime = await build_runtime(spec, redis_client)
        if await prepare(runtime):
            runtimes.append(runtime)
        else:
            await runtime.bot.session.close()

    if not runtimes:
        logger.error("no bot could authenticate — check the tokens in your .env")
        await shutdown([], redis_client)
        return 1

    # --- Signals -----------------------------------------------------------
    stop_event = install_signal_handlers()

    logger.info("🚀 polling %s bot(s) — press Ctrl+C to stop", len(runtimes))

    polling = asyncio.gather(*[run_bot(runtime) for runtime in runtimes])
    waiter = asyncio.create_task(stop_event.wait(), name="stop-waiter")

    try:
        done, _ = await asyncio.wait({polling, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if waiter in done:
            # Graceful stop requested: unwind the pollers.
            for runtime in runtimes:
                await runtime.dispatcher.stop_polling()
            polling.cancel()
        try:
            await polling
        except asyncio.CancelledError:
            pass
    finally:
        waiter.cancel()
        await shutdown(runtimes, redis_client)

    return 0


def main() -> None:
    """Synchronous entry point used by ``python main.py`` and the Dockerfile."""
    try:
        raise SystemExit(asyncio.run(amain()))
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        logging.getLogger("konkred.main").info("interrupted")
        raise SystemExit(0) from None


if __name__ == "__main__":
    main()
