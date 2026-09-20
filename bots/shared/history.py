"""Konkred bots - Redis-backed sliding-window conversation memory.

Each bot gets its own namespace, so the same Telegram user talking to the voice
bot and the crypto bot keeps two independent conversations:

    konkred:hist:{prefix}:{user_id}  ->  JSON list of {role, content} messages

The list is trimmed to the last N turns and refreshed with a 24h TTL on every
write, so idle conversations expire on their own without a cleanup job.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from .config import settings

logger = logging.getLogger(__name__)

KEY_TEMPLATE = "konkred:hist:{prefix}:{user_id}"


class HistoryManager:
    """Sliding-window conversational memory for one bot namespace."""

    def __init__(
        self,
        redis_client: Redis,
        prefix: str,
        max_turns: int | None = None,
        ttl: int | None = None,
    ) -> None:
        self.redis = redis_client
        self.prefix = prefix
        self.max_turns = max_turns if max_turns is not None else settings.history_turns
        self.ttl = ttl if ttl is not None else settings.history_ttl

    # ------------------------------------------------------------- keys

    def key(self, user_id: int | str) -> str:
        return KEY_TEMPLATE.format(prefix=self.prefix, user_id=user_id)

    @property
    def max_messages(self) -> int:
        """Two messages (user + assistant) make one turn."""
        return max(2, self.max_turns * 2)

    # ------------------------------------------------------------- reads

    async def get(self, user_id: int | str) -> list[dict[str, Any]]:
        """Return the stored messages, oldest first. Never raises."""
        try:
            raw = await self.redis.get(self.key(user_id))
        except RedisError as exc:
            logger.warning("history read failed for %s: %s", user_id, exc)
            return []
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("discarding corrupt history for %s", user_id)
            await self.clear(user_id)
            return []
        if not isinstance(data, list):
            return []
        return [
            item for item in data
            if isinstance(item, dict) and item.get("role") in {"user", "assistant"} and "content" in item
        ]

    # ------------------------------------------------------------ writes

    async def _write(self, user_id: int | str, messages: list[dict[str, Any]]) -> None:
        trimmed = messages[-self.max_messages:]
        try:
            await self.redis.set(self.key(user_id), json.dumps(trimmed, ensure_ascii=False), ex=self.ttl)
        except RedisError as exc:
            logger.warning("history write failed for %s: %s", user_id, exc)

    async def append(self, user_id: int | str, role: str, content: str) -> None:
        """Append one message and re-arm the TTL."""
        if role not in {"user", "assistant"}:
            raise ValueError(f"role must be 'user' or 'assistant', got {role!r}")
        text = (content or "").strip()
        if not text:
            return
        history = await self.get(user_id)
        history.append({"role": role, "content": text})
        await self._write(user_id, history)

    async def add_exchange(self, user_id: int | str, user_text: str, assistant_text: str) -> None:
        """Append a full user/assistant turn in a single round-trip."""
        history = await self.get(user_id)
        if (user_text or "").strip():
            history.append({"role": "user", "content": user_text.strip()})
        if (assistant_text or "").strip():
            history.append({"role": "assistant", "content": assistant_text.strip()})
        await self._write(user_id, history)

    async def clear(self, user_id: int | str) -> bool:
        """Delete the conversation. Returns True when something was removed."""
        try:
            return bool(await self.redis.delete(self.key(user_id)))
        except RedisError as exc:
            logger.warning("history clear failed for %s: %s", user_id, exc)
            return False

    # ------------------------------------------------------- composition

    async def build_messages(
        self,
        user_id: int | str,
        new_content: Any,
        include_history: bool = True,
    ) -> list[dict[str, Any]]:
        """Compose the gateway payload: prior turns plus the new user message.

        ``new_content`` may be a plain string or a list of multimodal parts.
        """
        messages: list[dict[str, Any]] = []
        if include_history:
            messages.extend(await self.get(user_id))
        messages.append({"role": "user", "content": new_content})
        return messages

    async def turn_count(self, user_id: int | str) -> int:
        """Number of completed turns currently remembered."""
        return len(await self.get(user_id)) // 2


__all__ = ["HistoryManager", "KEY_TEMPLATE"]
