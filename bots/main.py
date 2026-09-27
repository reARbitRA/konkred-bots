#!/usr/bin/env python3
"""Konkred multi-bot orchestrator.

Runs up to five specialised Telegram bots in one asyncio process, with one
shared Redis pool and one shared HTTP client to the in-container Node gateway.

``BOT_MODE=webhook`` starts a public aiohttp webhook receiver (for Render).
``BOT_MODE=polling`` keeps long polling (for local Compose/Hugging Face) while
serving the same health endpoint required by web-service hosts.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import signal
from contextlib import suppress
from typing import Any

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage
from aiogram.types import BotCommand, Update
from aiohttp import web
from redis.asyncio import Redis
from redis.exceptions import RedisError

from bot_content.handlers import router as content_router
from bot_crypto.handlers import router as crypto_router
from bot_ielts.handlers import router as ielts_router
from bot_pdf.handlers import router as pdf_router
from bot_voice.handlers import router as voice_router
from shared.config import (
    BOT_SPECS,
    BotSpec,
    configure_logging,
    get_active_bots,
    settings,
    validate_settings,
)
from shared.gateway_client import gateway
from shared.history import HistoryManager
from shared.payments import PaymentManager, create_payment_router

logger = logging.getLogger("konkred.main")

# Static wiring from a bot key to its router. Keeping this explicit means a typo
# is a startup ImportError rather than a silent production failure.
ROUTERS: dict[str, Router] = {
    "voice": voice_router,
    "pdf": pdf_router,
    "ielts": ielts_router,
    "content": content_router,
    "crypto": crypto_router,
}

COMMANDS: dict[str, list[BotCommand]] = {
    "voice": [
        BotCommand(command="start", description="Introduction and how it works"),
        BotCommand(command="help", description="Usage, formats and limits"),
        BotCommand(command="clear", description="Forget our conversation"),
        BotCommand(command="paysupport", description="Payment help and receipts"),
    ],
    "pdf": [
        BotCommand(command="start", description="Introduction and how it works"),
        BotCommand(command="help", description="Supported formats and actions"),
        BotCommand(command="clear", description="Unload the current document"),
        BotCommand(command="paysupport", description="Payment help and receipts"),
    ],
    "ielts": [
        BotCommand(command="test", description="Start a full 3-part mock test"),
        BotCommand(command="bands", description="Show the band descriptors"),
        BotCommand(command="stop", description="Abandon the current test"),
        BotCommand(command="help", description="How the scoring works"),
        BotCommand(command="clear", description="Forget my sessions"),
        BotCommand(command="paysupport", description="Payment help and receipts"),
    ],
    "content": [
        BotCommand(command="create", description="Create a new short-form script"),
        BotCommand(command="formulas", description="Hook formula cheat-sheet"),
        BotCommand(command="cancel", description="Cancel the current flow"),
        BotCommand(command="help", description="How to get the best scripts"),
        BotCommand(command="clear", description="Forget my sessions"),
        BotCommand(command="paysupport", description="Payment help and receipts"),
    ],
    "crypto": [
        BotCommand(command="scan", description="Full report on a ticker, e.g. /scan BTC"),
        BotCommand(command="sentiment", description="Sentiment on a topic or narrative"),
        BotCommand(command="news", description="Structural market briefing"),
        BotCommand(command="help", description="What I can and cannot do"),
        BotCommand(command="clear", description="Forget my sessions"),
        BotCommand(command="paysupport", description="Payment help and receipts"),
    ],
}


class BotRuntime:
    """One fully wired bot: Bot + Dispatcher + namespaced state/payments."""

    def __init__(self, spec: BotSpec, bot: Bot, dispatcher: Dispatcher) -> None:
        self.spec = spec
        self.bot = bot
        self.dispatcher = dispatcher
        self.username: str | None = None
        self.webhook_path_secret: str = ""

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<BotRuntime {self.spec.key} @{self.username or 'unknown'}>"


async def build_runtime(spec: BotSpec, redis_client: Redis) -> BotRuntime:
    """Construct the Bot, Dispatcher, storage and injected dependencies."""
    bot = Bot(
        token=spec.token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    storage = RedisStorage(
        redis=redis_client,
        key_builder=DefaultKeyBuilder(prefix=f"konkred:fsm:{spec.key}", with_bot_id=True),
        state_ttl=settings.history_ttl,
        data_ttl=settings.history_ttl,
    )
    dispatcher = Dispatcher(storage=storage)
    # Payment confirmations must be observed before the feature router's broad
    # message filters. Each dispatcher receives a fresh payment Router instance.
    dispatcher.include_router(create_payment_router())
    dispatcher.include_router(ROUTERS[spec.key])

    dispatcher["history"] = HistoryManager(redis_client, spec.history_prefix)
    dispatcher["payments"] = PaymentManager(redis_client, spec)
    dispatcher["bot_spec"] = spec

    runtime = BotRuntime(spec, bot, dispatcher)
    if settings.webhook_secret:
        runtime.webhook_path_secret = hmac.new(
            settings.webhook_secret.encode("utf-8"),
            f"{spec.key}:{spec.token}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:32]
    return runtime


async def prepare(runtime: BotRuntime) -> bool:
    """Validate the token and register the command menu. Returns False on failure."""
    try:
        me = await runtime.bot.get_me()
    except Exception as exc:  # noqa: BLE001 - any Telegram error must be non-fatal
        logger.error(
            "%s bot failed to authenticate (check %s): %s",
            runtime.spec.key,
            runtime.spec.token_env,
            exc,
        )
        return False

    runtime.username = me.username
    try:
        await runtime.bot.set_my_commands(COMMANDS.get(runtime.spec.key, []))
    except Exception as exc:  # noqa: BLE001 - a menu failure must not stop the bot
        logger.warning("%s: could not set the command menu: %s", runtime.spec.key, exc)

    logger.info(
        "%s %-8s ready as @%s — %s",
        runtime.spec.emoji,
        runtime.spec.key,
        me.username,
        runtime.spec.title,
    )
    return True


async def run_bot(runtime: BotRuntime) -> None:
    """Poll one bot until cancelled, isolating its failures from the others."""
    try:
        await runtime.dispatcher.start_polling(
            runtime.bot,
            handle_signals=False,
            drop_pending_updates=settings.drop_pending_updates,
            allowed_updates=runtime.dispatcher.resolve_used_update_types(),
        )
    except asyncio.CancelledError:
        logger.info("%s: polling cancelled", runtime.spec.key)
        raise
    except Exception:  # noqa: BLE001 - one bot crashing must not kill the daemon
        logger.exception("%s: polling stopped unexpectedly", runtime.spec.key)


class PublicServer:
    """Tiny public HTTP surface for health checks and Telegram webhooks."""

    def __init__(self, runtimes: list[BotRuntime], redis_client: Redis) -> None:
        self.runtimes = {runtime.spec.key: runtime for runtime in runtimes}
        self.redis = redis_client
        self.runner: web.AppRunner | None = None
        self.background_tasks: set[asyncio.Task[Any]] = set()

        self.app = web.Application(client_max_size=2 * 1024 * 1024)
        self.app.router.add_get("/", self.index)
        self.app.router.add_get("/healthz", self.health)
        self.app.router.add_post("/webhook/{bot_key}/{path_secret}", self.webhook)

    async def index(self, _: web.Request) -> web.Response:
        return web.json_response(
            {
                "service": "konkred-bots",
                "status": "ok",
                "mode": settings.bot_mode,
                "bots": sorted(self.runtimes),
            }
        )

    async def health(self, _: web.Request) -> web.Response:
        # Do not PING hosted Redis on every platform health probe: the process
        # only reaches this point after startup already verified Redis.
        return web.json_response(
            {
                "status": "ok",
                "mode": settings.bot_mode,
                "activeBots": len(self.runtimes),
                "pendingUpdates": len(self.background_tasks),
            }
        )

    async def webhook(self, request: web.Request) -> web.Response:
        """Authenticate, parse and enqueue an update, then answer immediately."""
        if settings.bot_mode != "webhook":
            raise web.HTTPNotFound()

        runtime = self.runtimes.get(request.match_info["bot_key"])
        supplied_path = request.match_info["path_secret"]
        if runtime is None or not hmac.compare_digest(supplied_path, runtime.webhook_path_secret):
            raise web.HTTPNotFound()

        supplied_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(supplied_header, settings.webhook_header_secret):
            raise web.HTTPForbidden(text="invalid Telegram secret")

        try:
            payload = await request.json()
            update = Update.model_validate(payload, context={"bot": runtime.bot})
        except Exception as exc:  # noqa: BLE001 - pydantic/json failures become a clean 400
            logger.warning("%s: invalid webhook payload: %s", runtime.spec.key, exc)
            raise web.HTTPBadRequest(text="invalid update") from exc

        task = asyncio.create_task(
            runtime.dispatcher.feed_update(runtime.bot, update),
            name=f"webhook-{runtime.spec.key}-{update.update_id}",
        )
        self.background_tasks.add(task)
        task.add_done_callback(self._update_finished)
        # Telegram gets its 200 without waiting for downloads or AI inference.
        return web.Response(text="OK")

    def _update_finished(self, task: asyncio.Task[Any]) -> None:
        self.background_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "background Telegram update failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    async def start(self) -> None:
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, host=settings.web_host, port=settings.port)
        await site.start()
        logger.info("🌐 public server listening on http://%s:%s", settings.web_host, settings.port)

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None
        if self.background_tasks:
            logger.info("waiting for %s in-flight update(s)", len(self.background_tasks))
            _, pending = await asyncio.wait(self.background_tasks, timeout=20)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)


async def configure_webhooks(runtimes: list[BotRuntime]) -> None:
    """Register every bot's secret public URL with Telegram."""
    for runtime in runtimes:
        url = (
            f"{settings.webhook_host}/webhook/"
            f"{runtime.spec.key}/{runtime.webhook_path_secret}"
        )
        await runtime.dispatcher.emit_startup(bot=runtime.bot)
        await runtime.bot.set_webhook(
            url=url,
            allowed_updates=runtime.dispatcher.resolve_used_update_types(),
            drop_pending_updates=settings.drop_pending_updates,
            secret_token=settings.webhook_header_secret,
        )
        logger.info("🔗 %s webhook registered", runtime.spec.key)


async def shutdown(
    runtimes: list[BotRuntime],
    redis_client: Redis | None,
    public_server: PublicServer | None = None,
    webhook_started: bool = False,
) -> None:
    """Close the HTTP server, dispatchers, sessions and shared pools."""
    logger.info("shutting down…")

    if public_server is not None:
        with suppress(Exception):
            await public_server.stop()

    for runtime in runtimes:
        if webhook_started:
            with suppress(Exception):
                await runtime.dispatcher.emit_shutdown(bot=runtime.bot)
        try:
            await runtime.dispatcher.storage.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: storage close failed: %s", runtime.spec.key, exc)
        try:
            await runtime.bot.session.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: session close failed: %s", runtime.spec.key, exc)

    with suppress(Exception):
        await gateway.aclose()

    if redis_client is not None:
        with suppress(Exception):
            await redis_client.aclose()

    logger.info("shutdown complete")


def install_signal_handlers() -> asyncio.Event:
    """Trap SIGINT/SIGTERM and return the Event they set."""
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
        # Never log the URL: cloud Redis URLs contain a password.
        logger.error("❌ cannot reach Redis: %s", exc)
        if client is not None:
            await client.aclose()
        return None
    logger.info("✅ redis connected")
    return client


async def amain() -> int:
    """Async entry point. Returns the process exit code."""
    configure_logging()

    errors = validate_settings()
    if errors:
        for error in errors:
            logger.error("configuration: %s", error)
        return 1

    active = get_active_bots()
    if not active:
        logger.error(
            "No bot tokens configured. Set at least one of: %s",
            ", ".join(spec.token_env for spec in BOT_SPECS),
        )
        return 1

    logger.info("=" * 68)
    logger.info("Konkred multi-bot service starting")
    logger.info("active bots : %s", ", ".join(f"{spec.emoji} {spec.key}" for spec in active))
    logger.info("mode        : %s", settings.bot_mode)
    logger.info("gateway     : %s", settings.gateway_url)
    logger.info("free uses   : %s per user / bot", settings.free_requests)
    logger.info("=" * 68)

    redis_client = await connect_redis()
    if redis_client is None:
        return 1

    runtimes: list[BotRuntime] = []
    public_server: PublicServer | None = None
    webhook_started = False
    try:
        if not await gateway.wait_until_ready(attempts=15, delay=1.0):
            logger.warning("⚠️ gateway did not report ready; continuing (it may still be booting)")

        for spec in active:
            runtime = await build_runtime(spec, redis_client)
            if await prepare(runtime):
                runtimes.append(runtime)
            else:
                await runtime.bot.session.close()

        if not runtimes:
            logger.error("no bot could authenticate — check the tokens in your environment")
            return 1

        public_server = PublicServer(runtimes, redis_client)
        await public_server.start()
        stop_event = install_signal_handlers()

        if settings.bot_mode == "webhook":
            await configure_webhooks(runtimes)
            webhook_started = True
            logger.info("🚀 serving webhooks for %s bot(s)", len(runtimes))
            await stop_event.wait()
        else:
            logger.info("🚀 polling %s bot(s)", len(runtimes))
            polling = asyncio.gather(*[run_bot(runtime) for runtime in runtimes])
            waiter = asyncio.create_task(stop_event.wait(), name="stop-waiter")
            done, _ = await asyncio.wait({polling, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if waiter in done:
                for runtime in runtimes:
                    with suppress(Exception):
                        await runtime.dispatcher.stop_polling()
                polling.cancel()
            with suppress(asyncio.CancelledError):
                await polling
            waiter.cancel()
            with suppress(asyncio.CancelledError):
                await waiter
    except Exception:  # noqa: BLE001 - fatal startup errors are logged before clean teardown
        logger.exception("service stopped because of a fatal error")
        return 1
    finally:
        await shutdown(runtimes, redis_client, public_server, webhook_started)

    return 0


def main() -> None:
    """Synchronous entry point used by ``python main.py`` and Docker."""
    try:
        raise SystemExit(asyncio.run(amain()))
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        logging.getLogger("konkred.main").info("interrupted")
        raise SystemExit(0) from None


if __name__ == "__main__":
    main()
