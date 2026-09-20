"""Konkred bots - shared helpers.

The most important function here is :func:`split_telegram_message`, which
guarantees that no chunk ever exceeds Telegram's hard 4096-character payload
limit while still splitting at the most natural boundary available
(paragraph -> line -> sentence -> word -> hard cut).
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# Telegram's hard limit is 4096; we default to 4000 to leave room for the
# "(1/3)" style part markers some handlers prepend.
TELEGRAM_HARD_LIMIT = 4096
DEFAULT_CHUNK = 4000


def split_telegram_message(text: str, max_length: int = DEFAULT_CHUNK) -> list[str]:
    """Split ``text`` into chunks that never exceed ``max_length`` characters.

    The algorithm degrades gracefully through progressively finer separators so
    that formatting survives wherever possible:

    1. paragraphs (``\\n\\n``)
    2. lines (``\\n``)
    3. sentences (``. ``, ``! ``, ``? ``)
    4. words (`` ``)
    5. hard character cut (for unbroken blobs such as base64 or long URLs)

    Returns at least one chunk (possibly empty) and never returns a chunk longer
    than ``max_length``.
    """
    limit = max(1, min(int(max_length), TELEGRAM_HARD_LIMIT))
    content = text if isinstance(text, str) else str(text)

    if not content:
        return [""]
    if len(content) <= limit:
        return [content]

    chunks: list[str] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        if buffer:
            chunks.append(buffer)
            buffer = ""

    def add(piece: str, separator: str = "") -> None:
        """Append ``piece`` to the buffer, flushing when it would overflow."""
        nonlocal buffer
        candidate = f"{buffer}{separator}{piece}" if buffer else piece
        if len(candidate) <= limit:
            buffer = candidate
            return
        flush()
        if len(piece) <= limit:
            buffer = piece
        else:
            # The piece itself is too big: recurse into a finer separator.
            for sub in _split_atom(piece, limit):
                if len(sub) <= limit:
                    add(sub)
                else:  # pragma: no cover - _split_atom already guarantees this
                    chunks.append(sub[:limit])

    for index, paragraph in enumerate(content.split("\n\n")):
        add(paragraph, "\n\n" if index else "")

    flush()
    result = [chunk for chunk in chunks if chunk != ""] or [""]

    # Final safety net: nothing may ever exceed the limit.
    safe: list[str] = []
    for chunk in result:
        while len(chunk) > limit:
            safe.append(chunk[:limit])
            chunk = chunk[limit:]
        safe.append(chunk)
    return [chunk for chunk in safe if chunk != ""] or [""]


# Separators ordered from coarsest to finest. `_split_atom` walks this list by
# strictly increasing index, which guarantees termination: every recursive call
# uses a finer separator, and the final fallback is an unconditional hard cut.
_SEPARATORS: tuple[str, ...] = ("\n", ". ", "! ", "? ", " ")


def _split_atom(text: str, limit: int, level: int = 0) -> list[str]:
    """Break one oversized block down using progressively finer separators.

    ``level`` indexes :data:`_SEPARATORS` and only ever increases, so the
    recursion depth is bounded by ``len(_SEPARATORS) + 1``.
    """
    if len(text) <= limit:
        return [text]

    # Exhausted every separator (or an unbroken blob): hard cut.
    if level >= len(_SEPARATORS):
        return [text[i:i + limit] for i in range(0, len(text), limit)]

    separator = _SEPARATORS[level]
    if separator not in text:
        return _split_atom(text, limit, level + 1)

    parts = text.split(separator)
    rebuilt: list[str] = []
    buffer = ""
    for index, part in enumerate(parts):
        piece = part if index == len(parts) - 1 else part + separator
        if buffer and len(buffer) + len(piece) > limit:
            rebuilt.append(buffer)
            buffer = piece
        else:
            buffer += piece
    if buffer:
        rebuilt.append(buffer)

    # Any fragment that is still too long is handled by the *next* separator.
    result: list[str] = []
    for part in rebuilt:
        if len(part) <= limit:
            result.append(part)
        else:
            result.extend(_split_atom(part, limit, level + 1))
    return result


async def send_long_message(message: Any, text: str, max_length: int = DEFAULT_CHUNK, **kwargs: Any) -> None:
    """Reply to a Telegram message, transparently splitting long output."""
    chunks = split_telegram_message(text, max_length)
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        body = chunk if total == 1 else f"{chunk}\n\n<i>({index}/{total})</i>"
        await message.answer(body, **kwargs)


def escape_html(text: str) -> str:
    """Escape text for Telegram's HTML parse mode."""
    return html.escape(str(text or ""), quote=False)


def clean_model_output(text: str) -> str:
    """Normalise model output for Telegram HTML rendering.

    Telegram's HTML parser only accepts a small tag subset, so Markdown emphasis
    is converted and any stray angle brackets are escaped.
    """
    value = str(text or "").strip()
    if not value:
        return ""

    # Pull fenced code blocks out before escaping so their content is preserved.
    blocks: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        blocks.append(match.group(2))
        return f"\x00BLOCK{len(blocks) - 1}\x00"

    value = re.sub(r"```(\w+)?\n?([\s\S]*?)```", _stash, value)
    value = html.escape(value, quote=False)

    # Markdown -> Telegram HTML (bold before italic so ** wins over *).
    value = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", value, flags=re.DOTALL)
    value = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", value)
    value = re.sub(r"(?<![\w_])__(.+?)__(?![\w_])", r"<b>\1</b>", value, flags=re.DOTALL)
    value = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", value)
    # Markdown headings have no Telegram equivalent - render them bold.
    value = re.sub(r"^\s{0,3}#{1,6}\s+(.+)$", r"<b>\1</b>", value, flags=re.MULTILINE)

    for index, block in enumerate(blocks):
        value = value.replace(f"\x00BLOCK{index}\x00", f"<pre>{html.escape(block, quote=False)}</pre>")

    # Collapse excessive blank lines.
    return re.sub(r"\n{4,}", "\n\n\n", value).strip()


def truncate(text: str, limit: int = 120, suffix: str = "…") -> str:
    """Shorten text for log lines and button labels."""
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - len(suffix))] + suffix


def humanize_bytes(size: int) -> str:
    """Render a byte count as a human-readable string."""
    value = float(max(0, int(size)))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"  # pragma: no cover - unreachable


def format_duration(seconds: float) -> str:
    """Render a duration as ``M:SS`` (or ``H:MM:SS`` when long enough)."""
    total = max(0, int(seconds or 0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def extract_json_block(text: str) -> Any | None:
    """Extract the first balanced JSON object/array embedded in model output."""
    import json

    value = str(text or "").strip()
    if not value:
        return None

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", value, flags=re.IGNORECASE)
    candidates: list[str] = []
    if fenced:
        candidates.append(fenced.group(1).strip())
    candidates.append(value)

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (TypeError, ValueError):
            pass
        start = next((i for i, ch in enumerate(candidate) if ch in "{["), None)
        if start is None:
            continue
        opener = candidate[start]
        closer = "}" if opener == "{" else "]"
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(candidate)):
            char = candidate[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(candidate[start:index + 1])
                    except (TypeError, ValueError):
                        break
    return None


def chunk_iterable(items: Iterable[Any], size: int) -> list[list[Any]]:
    """Split an iterable into fixed-size lists (used for keyboard layouts)."""
    bucket: list[list[Any]] = []
    row: list[Any] = []
    for item in items:
        row.append(item)
        if len(row) >= max(1, size):
            bucket.append(row)
            row = []
    if row:
        bucket.append(row)
    return bucket


#: Shown when a handler hits a failure that is not a GatewayError. Users must
#: never be left staring at a "working on it" status that never resolves.
UNEXPECTED_ERROR = (
    "⚠️ Something went wrong on my side while handling that.\n"
    "The error has been logged — please try again in a moment."
)


__all__ = [
    "UNEXPECTED_ERROR",
    "DEFAULT_CHUNK",
    "TELEGRAM_HARD_LIMIT",
    "chunk_iterable",
    "clean_model_output",
    "escape_html",
    "extract_json_block",
    "format_duration",
    "humanize_bytes",
    "send_long_message",
    "split_telegram_message",
    "truncate",
]
