"""Konkred bots - async HTTP client for the AI Gateway.

A single pooled ``httpx.AsyncClient`` is shared by all five bots running inside
the same event loop, which keeps memory flat and reuses TCP connections to the
gateway. Non-200 responses are translated into a typed :class:`GatewayError`
carrying the machine-readable error code and any ``retryAfter`` hint.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, Iterable

import httpx

from .config import settings

logger = logging.getLogger(__name__)

# Error codes that are worth retrying locally (the gateway already does its own
# provider-level fallback, so we only retry transport-level hiccups).
_RETRYABLE_CODES = {
    "all_candidates_failed",
    "all_slots_rate_limited",
    "internal_error",
}


class GatewayError(RuntimeError):
    """Raised when the gateway returns a non-200 response."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        retry_after: float = 0.0,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"[{status_code}/{code}] {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after = retry_after
        self.details = details or {}

    @property
    def is_rate_limit(self) -> bool:
        return self.status_code == 429

    def user_message(self) -> str:
        """A friendly, non-technical explanation for Telegram users."""
        if self.is_rate_limit:
            wait = max(1, int(self.retry_after))
            return (
                "⏳ I'm at my free-tier rate limit right now.\n"
                f"Please try again in about {wait} second{'s' if wait != 1 else ''}."
            )
        if self.status_code == 401:
            return "🔒 The AI gateway rejected my credentials. Please contact the administrator."
        if self.status_code == 413:
            return "📦 That input is too large for me to process. Try sending a smaller chunk."
        if self.status_code >= 500:
            return (
                "🛠 Every AI provider I can reach is currently unavailable.\n"
                "This is usually brief — please try again in a minute."
            )
        return f"⚠️ I couldn't complete that request: {self.message}"


def inline_data_part(mime_type: str, payload: bytes) -> dict[str, Any]:
    """Build a multimodal ``inlineData`` part for audio/image/document bytes."""
    return {
        "inlineData": {
            "mimeType": mime_type,
            "data": base64.b64encode(payload).decode("ascii"),
        }
    }


def text_part(text: str) -> dict[str, Any]:
    """Build a multimodal text part."""
    return {"type": "text", "text": text}


class GatewayClient:
    """Thin, resilient wrapper around ``POST /api/ai``."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.base_url = (base_url or settings.gateway_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.gateway_api_key
        self.timeout = timeout or settings.gateway_timeout
        self.max_retries = settings.gateway_max_retries if max_retries is None else max_retries
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    # ----------------------------------------------------------------- pool

    async def client(self) -> httpx.AsyncClient:
        """Lazily create the shared pooled client (double-checked under a lock)."""
        if self._client is not None and not self._client.is_closed:
            return self._client
        async with self._lock:
            if self._client is None or self._client.is_closed:
                headers = {
                    "content-type": "application/json",
                    "user-agent": "konkred-bots/1.0",
                }
                if self.api_key:
                    headers["authorization"] = f"Bearer {self.api_key}"
                self._client = httpx.AsyncClient(
                    base_url=self.base_url,
                    headers=headers,
                    timeout=httpx.Timeout(self.timeout, connect=10.0),
                    limits=httpx.Limits(
                        max_keepalive_connections=20,
                        max_connections=50,
                        keepalive_expiry=60.0,
                    ),
                )
        return self._client

    async def aclose(self) -> None:
        """Close the pooled client (called on graceful shutdown)."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # ------------------------------------------------------------- requests

    async def ask(
        self,
        task_type: str,
        messages: Iterable[dict[str, Any]],
        max_tokens: int = 2500,
        temperature: float = 0.3,
        model: str | None = None,
        system: str | None = None,
        json_mode: bool = False,
        user_id: str | int | None = None,
        fusion: bool = False,
        private: bool = False,
    ) -> str:
        """Run a completion and return the plain text answer.

        Raises:
            GatewayError: for any non-200 response from the gateway.
        """
        result = await self.ask_full(
            task_type=task_type,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            model=model,
            system=system,
            json_mode=json_mode,
            user_id=user_id,
            fusion=fusion,
            private=private,
        )
        return str(result.get("text", "")).strip()

    async def ask_full(
        self,
        task_type: str,
        messages: Iterable[dict[str, Any]],
        max_tokens: int = 2500,
        temperature: float = 0.3,
        model: str | None = None,
        system: str | None = None,
        json_mode: bool = False,
        user_id: str | int | None = None,
        fusion: bool = False,
        private: bool = False,
    ) -> dict[str, Any]:
        """Run a completion and return the full gateway envelope."""
        payload: dict[str, Any] = {
            "taskType": task_type,
            "messages": list(messages),
            "maxTokens": int(max_tokens),
            "temperature": float(temperature),
        }
        if model:
            payload["model"] = model
        if system:
            payload["system"] = system
        if json_mode:
            payload["jsonMode"] = True
        if user_id is not None:
            payload["userId"] = str(user_id)
        if fusion:
            payload["fusion"] = True
        if private:
            payload["privacy"] = "private"

        client = await self.client()
        last_error: GatewayError | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = await client.post("/api/ai", json=payload)
            except httpx.TimeoutException as exc:
                last_error = GatewayError(504, "gateway_timeout", f"The gateway timed out: {exc}")
            except httpx.HTTPError as exc:
                last_error = GatewayError(502, "gateway_unreachable", f"Cannot reach the gateway: {exc}")
            else:
                if response.status_code == 200:
                    data = response.json()
                    logger.info(
                        "gateway ok task=%s model=%s tokens=%s cached=%s latency=%sms",
                        task_type,
                        data.get("model"),
                        (data.get("usage") or {}).get("totalTokens"),
                        data.get("cached"),
                        data.get("latencyMs"),
                    )
                    return data
                last_error = self._to_error(response)

            # Retry only transient classes, with a short backoff.
            retryable = last_error.status_code in (502, 504) or last_error.code in _RETRYABLE_CODES
            if attempt < self.max_retries and retryable:
                delay = min(4.0, 0.75 * (2 ** attempt))
                logger.warning(
                    "gateway attempt %s/%s failed (%s); retrying in %.1fs",
                    attempt + 1,
                    self.max_retries + 1,
                    last_error.code,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            break

        assert last_error is not None  # loop always assigns before breaking
        logger.error("gateway failed task=%s error=%s", task_type, last_error)
        raise last_error

    @staticmethod
    def _to_error(response: httpx.Response) -> GatewayError:
        """Translate a non-200 response into a typed GatewayError."""
        code = "http_error"
        message = f"HTTP {response.status_code}"
        retry_after = 0.0
        details: dict[str, Any] = {}
        try:
            body = response.json()
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                code = str(error.get("code", code))
                message = str(error.get("message", message))
                retry_after = float(error.get("retryAfterMs", 0) or 0) / 1000.0
                details = error
        except (ValueError, TypeError):
            message = (response.text or message)[:300]

        if not retry_after:
            header = response.headers.get("retry-after")
            if header:
                try:
                    retry_after = float(header)
                except ValueError:
                    retry_after = 0.0

        return GatewayError(response.status_code, code, message, retry_after, details)

    # ------------------------------------------------------------- probing

    async def health(self) -> dict[str, Any]:
        """Fetch ``/api/health`` (used by main.py on boot)."""
        client = await self.client()
        response = await client.get("/api/health", timeout=15.0)
        response.raise_for_status()
        return response.json()

    async def wait_until_ready(self, attempts: int = 30, delay: float = 2.0) -> bool:
        """Block until the gateway answers its health probe."""
        for attempt in range(1, attempts + 1):
            try:
                health = await self.health()
            except (httpx.HTTPError, ValueError) as exc:
                logger.info("waiting for gateway (%s/%s): %s", attempt, attempts, exc)
            else:
                logger.info(
                    "gateway is %s (models ready: %s)",
                    health.get("status"),
                    (health.get("models") or {}).get("ready"),
                )
                return True
            await asyncio.sleep(delay)
        logger.error("gateway did not become ready after %s attempts", attempts)
        return False


# Process-wide singleton shared by every bot.
gateway = GatewayClient()

__all__ = ["GatewayClient", "GatewayError", "gateway", "inline_data_part", "text_part"]
