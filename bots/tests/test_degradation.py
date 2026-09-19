"""Every bot must stay usable when the gateway cannot answer.

Free tiers exhaust, providers go down, and unexpected exceptions happen. In
all of those cases the user must get a clear message - never a silent hang on
a "working on it..." status, and never a raw traceback.

Run with:  cd bots && python tests/test_degradation.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import importlib
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("GATEWAY_URL", "http://127.0.0.1:59999")

import fakeredis.aioredis  # noqa: E402
from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage  # noqa: E402
from aiogram.methods import EditMessageText, GetMe, SendMessage  # noqa: E402
from aiogram.types import Chat, Document, File, Message, Update, User, Voice  # noqa: E402

import shared.gateway_client as gwmod  # noqa: E402
from shared.gateway_client import GatewayError  # noqa: E402
from shared.history import HistoryManager  # noqa: E402

CHAT = Chat(id=555, type="private")
USER = User(id=777, is_bot=False, first_name="Tester", username="tester")


class FakeBot(Bot):
    """Intercepts outbound Telegram calls and records what the user would see."""

    def __init__(self, token: str) -> None:
        super().__init__(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.sent: list[str] = []

    async def __call__(self, method, request_timeout=None):
        if isinstance(method, GetMe):
            return User(id=1, is_bot=True, first_name="B", username="b")
        if isinstance(method, (SendMessage, EditMessageText)):
            self.sent.append(method.text)
            return Message(
                message_id=len(self.sent), date=dt.datetime.now(),
                chat=CHAT, from_user=USER, text=method.text,
            ).as_(self)
        return True

    async def download_file(self, path, *args, **kwargs):
        return io.BytesIO(b"fake payload " * 48)

    async def get_file(self, file_id, **kwargs):
        return File(file_id=file_id, file_unique_id="u", file_size=640, file_path="f/x.bin")


def _message(text: str | None = None, **kwargs) -> Message:
    return Message(message_id=1, date=dt.datetime.now(), chat=CHAT,
                   from_user=USER, text=text, **kwargs)


async def _drive(bot_key: str, module_name: str, build_update, failure):
    """Feed one update while every gateway call raises `failure`."""
    # aiogram refuses to attach a Router to two Dispatchers, so reload the
    # module to get a fresh Router for each scenario.
    for name in [m for m in list(sys.modules) if m.startswith(module_name)]:
        del sys.modules[name]
    router = importlib.import_module(module_name).router

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    bot = FakeBot("111:AAA")
    dispatcher = Dispatcher(storage=RedisStorage(
        redis=redis,
        key_builder=DefaultKeyBuilder(prefix=f"konkred:fsm:{bot_key}", with_bot_id=True),
    ))
    dispatcher.include_router(router)
    dispatcher["history"] = HistoryManager(redis, bot_key)

    async def boom(*args, **kwargs):
        raise failure

    gwmod.gateway.ask = boom
    gwmod.gateway.ask_full = boom

    try:
        await dispatcher.feed_update(bot, build_update(bot))
    except Exception as exc:  # noqa: BLE001 - the point of the test
        return bot.sent, f"{type(exc).__name__}: {exc}"
    return bot.sent, None


FAILURES = {
    "gateway down (503)": GatewayError(503, "all_candidates_failed", "every provider failed"),
    "rate limited (429)": GatewayError(429, "user_rpm", "Per-user limit reached", retry_after=42),
    "bad credentials (401)": GatewayError(401, "unauthorized", "bad key"),
    "payload too large (413)": GatewayError(413, "payload_too_large", "too big"),
    "unexpected crash": RuntimeError("upstream exploded"),
}

SCENARIOS = [
    ("crypto", "bot_crypto",
     lambda b: Update(update_id=1, message=_message("/scan BTC").as_(b))),
    ("voice", "bot_voice",
     lambda b: Update(update_id=1, message=_message(voice=Voice(
         file_id="v", file_unique_id="u", duration=8,
         mime_type="audio/ogg", file_size=640)).as_(b))),
    ("content", "bot_content",
     lambda b: Update(update_id=1, message=_message("/formulas").as_(b))),
    ("ielts", "bot_ielts",
     lambda b: Update(update_id=1, message=_message("/test").as_(b))),
    ("pdf", "bot_pdf",
     lambda b: Update(update_id=1, message=_message(document=Document(
         file_id="d", file_unique_id="u", file_name="notes.txt",
         mime_type="text/plain", file_size=64)).as_(b))),
]

LEAKS = ("Traceback", "GatewayError(", "RuntimeError(", "Exception(")


async def main() -> int:
    failures = 0
    for name, module, build in SCENARIOS:
        print(f"\n=== {name} ===")
        for label, failure in FAILURES.items():
            sent, crashed = await _drive(name, module, build, failure)
            reply = sent[-1] if sent else ""

            problems = []
            if crashed:
                problems.append(f"handler raised {crashed}")
            if not reply:
                problems.append("no reply at all - user is left hanging")
            if any(token in reply for token in LEAKS):
                problems.append("internal error text leaked to the user")

            if problems:
                failures += 1
                print(f"  FAIL {label:24s} {'; '.join(problems)}")
            else:
                print(f"  ok   {label:24s} -> {reply[:70]!r}")

    print()
    if failures:
        print(f"FAILED: {failures} scenario(s) left the user without a usable reply")
        return 1
    print(f"PASSED: all {len(SCENARIOS) * len(FAILURES)} degradation scenarios handled")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
