"""Real handlers -> real HTTP -> real gateway, with nothing mocked out.

The degradation suite proves the bots survive a *broken* gateway. This proves
the opposite seam: that a *working* gateway is actually usable end to end -
that task types, field names, fusion and token bounds agree across the
Python/Node boundary, and that handlers cope with whatever text a model
returns rather than only the shape they hoped for.

Needs a gateway on GATEWAY_URL (default http://127.0.0.1:3000). Start one with
no credentials and no outbound calls:

    cd gateway && MOCK_ONLY=true node src/server.mjs
    cd bots   && python tests/test_end_to_end.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import importlib
import io
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
GATEWAY_URL = os.environ.setdefault("GATEWAY_URL", "http://127.0.0.1:3000")

import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402
from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage  # noqa: E402
from aiogram.methods import (  # noqa: E402
    AnswerCallbackQuery,
    EditMessageText,
    GetMe,
    SendMessage,
)
from aiogram.types import (  # noqa: E402
    CallbackQuery,
    Chat,
    Document,
    File,
    Message,
    Update,
    User,
    Voice,
)

from shared.history import HistoryManager  # noqa: E402

CHAT = Chat(id=555, type="private")
USER = User(id=777, is_bot=False, first_name="Tester", username="tester")

# The gateway enforces a per-user RPM limit (userRpm=12 by default), so one
# synthetic user firing every flow back to back would throttle itself. Real
# traffic is distinct people on distinct bots, so give each flow its own id.
USER_IDS = {"crypto": 90001, "voice": 90002, "pdf": 90003,
            "content": 90004, "ielts": 90005}


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
        if isinstance(method, AnswerCallbackQuery):
            return True
        return True

    async def download_file(self, path, *args, **kwargs):
        return io.BytesIO(b"Quarterly revenue rose 12 percent. " * 20)

    async def get_file(self, file_id, **kwargs):
        return File(file_id=file_id, file_unique_id="u", file_size=700,
                    file_path="f/x.bin")


def _user(user_id: int) -> User:
    return User(id=user_id, is_bot=False, first_name="Tester", username="tester")


def _message(text: str | None = None, user: User = USER, **kwargs) -> Message:
    return Message(message_id=1, date=dt.datetime.now(), chat=CHAT,
                   from_user=user, text=text, **kwargs)


def _fresh_router(module_name: str):
    """aiogram refuses to attach one Router to two Dispatchers."""
    for name in [m for m in list(sys.modules) if m.startswith(module_name)]:
        del sys.modules[name]
    return importlib.import_module(module_name).router


class Harness:
    """A live dispatcher for one bot, reusable across several updates."""

    def __init__(self, bot_key: str, module_name: str) -> None:
        self.user = _user(USER_IDS[bot_key])
        self.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.bot = FakeBot("111:AAA")
        self.dispatcher = Dispatcher(storage=RedisStorage(
            redis=self.redis,
            key_builder=DefaultKeyBuilder(prefix=f"konkred:fsm:{bot_key}",
                                          with_bot_id=True),
        ))
        self.dispatcher.include_router(_fresh_router(module_name))
        self.dispatcher["history"] = HistoryManager(self.redis, bot_key)
        self._update_id = 0

    async def feed(self, build) -> list[str]:
        """Feed one update and return only the messages it produced."""
        before = len(self.bot.sent)
        self._update_id += 1
        await self.dispatcher.feed_update(
            self.bot, build(self.bot, self._update_id, self.user))
        return self.bot.sent[before:]


def _text(text: str):
    def build(b, uid, user):
        return Update(update_id=uid, message=_message(text, user=user).as_(b))
    return build


def _callback(data: str):
    def build(b, uid, user):
        return Update(update_id=uid, callback_query=CallbackQuery(
            id=str(uid), from_user=user, chat_instance="ci",
            data=data, message=_message("anchor", user=user).as_(b),
        ).as_(b))
    return build


def _voice(b, uid, user):
    return Update(update_id=uid, message=_message(user=user, voice=Voice(
        file_id="v", file_unique_id="u", duration=9,
        mime_type="audio/ogg", file_size=700)).as_(b))


def _document(b, uid, user):
    return Update(update_id=uid, message=_message(user=user, document=Document(
        file_id="d", file_unique_id="u", file_name="report.txt",
        mime_type="text/plain", file_size=700)).as_(b))


# Each flow: (bot key, module, [(step label, update builder, min answer chars)])
# min answer chars is the floor for the *longest* message a step produces, so
# a bare status line or keyboard prompt can never pass as a real answer.
FLOWS = [
    ("crypto", "bot_crypto", [
        ("/scan BTC", _text("/scan BTC"), 200),
        ("/sentiment ethereum", _text("/sentiment ethereum"), 200),
        ("/news", _text("/news"), 200),
    ]),
    ("voice", "bot_voice", [
        ("voice note", _voice, 200),
    ]),
    ("pdf", "bot_pdf", [
        ("upload report.txt", _document, 80),
        ("Executive Summary button", _callback("pdf:summary"), 200),
    ]),
    ("content", "bot_content", [
        ("/start", _text("/start"), 120),
        ("niche: cold brew coffee", _text("cold brew coffee"), 40),
        ("platform: reels", _callback("content:platform:reels"), 40),
        ("tone: educational", _callback("content:tone:educational"), 200),
    ]),
    ("ielts", "bot_ielts", [
        ("/test", _text("/test"), 60),
        ("part 1 answer", _text(
            "I live in a small flat near the river and I have been there "
            "for about three years now, mostly because it is quiet."), 40),
    ]),
]

# Text that means the handler gave up rather than produced a real answer.
FAILURE_MARKERS = (
    "Something went wrong on my side",
    "Every AI provider I can reach is currently unavailable",
    "The AI gateway rejected my credentials",
    "I'm at my free-tier rate limit",
    "That input is too large",
)
LEAKS = ("Traceback", "GatewayError(", "RuntimeError(", "KeyError(",
         ": None", "None)", "NoneType", "{'", "dict_keys")


TASK_CALL = re.compile(r'task_type\s*=\s*["\']([a-z0-9-]+)["\']')


async def _task_type_contract() -> list[str]:
    """Every task type the bots send must be one the gateway really supports.

    The gateway deliberately normalises anything unknown to "general" instead
    of erroring, which is right for a public API but means a typo in a bot
    would silently degrade routing forever with no signal. The bots are a
    known client, so hold them to the exact list /api/models publishes.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{GATEWAY_URL}/api/models")
        supported = set(response.json().get("tasks") or [])

    problems = []
    if not supported:
        return ["/api/models published no task list"]

    for handler in sorted(Path(__file__).resolve().parent.parent.glob(
            "bot_*/handlers.py")):
        for task in sorted(set(TASK_CALL.findall(handler.read_text()))):
            if task not in supported:
                problems.append(
                    f"{handler.parent.name} sends task_type={task!r}, which "
                    f"the gateway does not support - it would be silently "
                    f"coerced to 'general'")
    return problems


async def _gateway_reachable() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{GATEWAY_URL}/api/health")
        return response.status_code in (200, 503)
    except Exception:  # noqa: BLE001 - any failure means "not reachable"
        return False


async def main() -> int:
    if not await _gateway_reachable():
        print(f"SKIP: no gateway on {GATEWAY_URL}")
        print("  start one with: cd gateway && MOCK_ONLY=true node src/server.mjs")
        return 0

    failures = 0

    print("=== task type contract ===")
    contract_problems = await _task_type_contract()
    for problem in contract_problems:
        print(f"  FAIL {problem}")
        failures += 1
    if not contract_problems:
        print("  ok   every task_type the bots send is supported by the gateway")

    for bot_key, module, steps in FLOWS:
        print(f"\n=== {bot_key} ===")
        harness = Harness(bot_key, module)
        for label, build, min_chars in steps:
            try:
                replies = await harness.feed(build)
            except Exception as exc:  # noqa: BLE001 - the point of the test
                failures += 1
                print(f"  FAIL {label:28s} handler raised "
                      f"{type(exc).__name__}: {exc}")
                continue

            problems = []
            if not replies:
                problems.append("no reply at all")
            if any(not reply.strip() for reply in replies):
                problems.append("sent an empty message")

            # Check every message the step produced, not just the last one -
            # a trailing "What next?" keyboard prompt must never be mistaken
            # for a successful answer.
            for reply in replies:
                hit = next((m for m in FAILURE_MARKERS if m in reply), None)
                if hit:
                    problems.append(f"degraded reply: {hit!r}")
                leak = next((token for token in LEAKS if token in reply), None)
                if leak:
                    problems.append(f"leaked {leak!r} into user text")

            # The substantive answer is the longest message of the step; a
            # status line plus a keyboard prompt is not an answer.
            longest = max((len(r) for r in replies), default=0)
            if longest < min_chars:
                problems.append(
                    f"longest message {longest} chars < {min_chars} expected "
                    f"- no real content")

            if problems:
                failures += 1
                print(f"  FAIL {label:28s} {'; '.join(problems)}")
            else:
                body = max(replies, key=len)
                flat = " ".join(body.split())
                print(f"  ok   {label:28s} {len(replies)} msg, "
                      f"{longest:4d} chars -> {flat[:52]!r}")

    total = sum(len(steps) for _, _, steps in FLOWS)
    print()
    if failures:
        print(f"FAILED: {failures} live check(s) failed across "
              f"{total} steps + the task type contract")
        return 1
    print(f"PASSED: {total} live end-to-end steps produced real answers "
          f"and the task type contract holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
