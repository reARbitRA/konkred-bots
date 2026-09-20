"""Focused tests for the Redis-backed revenue gate.

Run directly: ``python tests/test_payments.py``.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fakeredis.aioredis  # noqa: E402

from shared.config import BOT_SPECS, settings  # noqa: E402
from shared.payments import PaymentManager  # noqa: E402


async def main() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    manager = PaymentManager(redis, BOT_SPECS[0])
    user_id = 424242

    # Exactly the configured number of free actions is admitted.
    decisions = [await manager.reserve_request(user_id) for _ in range(settings.free_requests)]
    assert all(item.allowed and not item.paid for item in decisions)
    assert [item.used for item in decisions] == list(range(1, settings.free_requests + 1))

    blocked = await manager.reserve_request(user_id)
    assert not blocked.allowed
    assert blocked.reason == "free_limit_reached"
    assert blocked.used == settings.free_requests

    # A paid entitlement immediately bypasses the exhausted free counter.
    assert await manager.grant_access(user_id, "test:payment")
    paid = await manager.reserve_request(user_id)
    assert paid.allowed and paid.paid

    # Invoice payloads are bound to both bot and user and are tamper-evident.
    payload = manager.build_payload(user_id)
    assert manager.validate_payload(payload, user_id)
    assert not manager.validate_payload(payload, user_id + 1)
    assert not manager.validate_payload(payload + "x", user_id)

    # Concurrent reservations cannot cross the ceiling.
    concurrent_user = 999
    burst = await asyncio.gather(
        *[manager.reserve_request(concurrent_user) for _ in range(settings.free_requests + 8)]
    )
    assert sum(item.allowed for item in burst) == settings.free_requests

    await redis.aclose()
    print("PASSED: payment ceiling, entitlement, payload and concurrency")


if __name__ == "__main__":
    asyncio.run(main())
