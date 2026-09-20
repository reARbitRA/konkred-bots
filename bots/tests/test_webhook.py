"""Webhook authentication and fast-ack regression test.

Run directly: ``python tests/test_webhook.py``.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

os.environ["BOT_MODE"] = "webhook"
os.environ["WEBHOOK_HOST"] = "https://example.test"
os.environ["WEBHOOK_SECRET"] = "webhook_test_secret_123456"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from main import PublicServer  # noqa: E402


class RecordingDispatcher:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.finished = asyncio.Event()

    async def feed_update(self, bot, update):
        self.started.set()
        await asyncio.sleep(0.25)
        self.finished.set()


async def main() -> None:
    bot = Bot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
    dispatcher = RecordingDispatcher()
    runtime = SimpleNamespace(
        spec=SimpleNamespace(key="voice"),
        bot=bot,
        dispatcher=dispatcher,
        webhook_path_secret="a" * 32,
    )
    public = PublicServer([runtime], redis_client=None)
    client = TestClient(TestServer(public.app))
    await client.start_server()

    update = {
        "update_id": 100,
        "message": {
            "message_id": 10,
            "date": 1_700_000_000,
            "chat": {"id": 777, "type": "private"},
            "from": {"id": 888, "is_bot": False, "first_name": "Tester"},
            "text": "hello",
        },
    }
    path = f"/webhook/voice/{'a' * 32}"

    denied = await client.post(path, json=update)
    assert denied.status == 403

    started = time.monotonic()
    accepted = await client.post(
        path,
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": os.environ["WEBHOOK_SECRET"]},
    )
    latency = time.monotonic() - started
    assert accepted.status == 200
    assert latency < 0.20, f"webhook acknowledgement took {latency:.3f}s"
    await asyncio.wait_for(dispatcher.started.wait(), timeout=0.2)
    assert not dispatcher.finished.is_set(), "handler was awaited before the HTTP acknowledgement"
    await asyncio.wait_for(dispatcher.finished.wait(), timeout=1.0)

    await client.close()
    await bot.session.close()
    print(f"PASSED: secret header enforced and webhook acknowledged in {latency * 1000:.1f}ms")


if __name__ == "__main__":
    asyncio.run(main())
