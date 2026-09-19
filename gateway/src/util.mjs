/**
 * Konkred AI Gateway - shared utilities.
 * Zero external dependencies: only the Node.js standard library is used.
 */
import crypto from 'node:crypto';

/** Monotonic-ish wall clock helper (ms). */
export const now = () => Date.now();

/**
 * Promise-based sleep.
 * NOTE: the timer is deliberately NOT unref()-ed. An unref()-ed timer that is
 * the only pending handle lets the event loop drain, which would leave this
 * promise permanently unsettled and hang the awaiting caller.
 */
export const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, Math.max(0, ms)); });

/** Clamp a number between min and max. */
export function clamp(value, min, max) {
  if (Number.isNaN(Number(value))) return min;
  return Math.min(max, Math.max(min, Number(value)));
}

/** Round to N decimals (returns a Number, not a String). */
export function round(value, decimals = 2) {
  const factor = 10 ** decimals;
  return Math.round(Number(value) * factor) / factor;
}

/**
 * Deterministic JSON serialisation: object keys are sorted recursively so that
 * two semantically identical payloads always hash to the same cache key.
 */
export function stableStringify(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null';
  if (Array.isArray(value)) return `[${value.map((item) => stableStringify(item)).join(',')}]`;
  const keys = Object.keys(value).sort();
  const parts = [];
  for (const key of keys) {
    const serialized = stableStringify(value[key]);
    if (serialized === undefined) continue;
    parts.push(`${JSON.stringify(key)}:${serialized}`);
  }
  return `{${parts.join(',')}}`;
}

/** SHA-256 hex digest of an arbitrary (JSON-serialisable) value. */
export function sha256(value) {
  const payload = typeof value === 'string' ? value : stableStringify(value);
  return crypto.createHash('sha256').update(payload, 'utf8').digest('hex');
}

/** Short, collision-resistant request identifier. */
export function requestId() {
  return crypto.randomBytes(9).toString('base64url');
}

/**
 * Constant-time string comparison that never throws and never leaks length
 * information through early returns (both sides are hashed to a fixed width).
 */
export function timingSafeEqualStr(a, b) {
  const left = crypto.createHash('sha256').update(String(a ?? ''), 'utf8').digest();
  const right = crypto.createHash('sha256').update(String(b ?? ''), 'utf8').digest();
  try {
    return crypto.timingSafeEqual(left, right) && String(a ?? '').length > 0;
  } catch {
    return false;
  }
}

/**
 * Heuristic token estimator. We deliberately over-estimate slightly (3.6 chars
 * per token instead of the usual ~4) so quota accounting stays conservative.
 */
export function estimateTokens(input) {
  if (input == null) return 0;
  if (typeof input === 'number') return Math.ceil(input);
  if (typeof input === 'string') return Math.ceil(input.length / 3.6) + 1;
  if (Array.isArray(input)) return input.reduce((sum, item) => sum + estimateTokens(item), 0);
  if (typeof input === 'object') {
    // { role, content } message or multimodal part
    let total = 4; // per-message overhead
    for (const [key, value] of Object.entries(input)) {
      if (key === 'inlineData' || key === 'data' || key === 'base64') {
        const len = typeof value === 'string' ? value.length : stableStringify(value).length;
        // base64 media: ~258 tokens per second of audio / per image tile is the
        // published Gemini figure; 750 bytes per token is a safe approximation.
        total += Math.ceil(len / 750);
        continue;
      }
      total += estimateTokens(value);
    }
    return total;
  }
  return 1;
}

/** Estimate tokens for a full chat message array. */
export function estimateMessageTokens(messages) {
  if (!Array.isArray(messages)) return estimateTokens(messages);
  return messages.reduce((sum, message) => sum + estimateTokens(message), 8);
}

/** Random jitter multiplier in [1 - spread, 1 + spread]. */
export function jitter(spread = 0.1) {
  return 1 + (Math.random() * 2 - 1) * spread;
}

/**
 * Exponential backoff with +/- 10% jitter.
 * delay = base * 2^(errors - 1) * jitter, capped at `max`.
 */
export function backoffDelay(errors, base = 1000, max = 15 * 60 * 1000, spread = 0.1) {
  const exponent = Math.max(0, Math.floor(errors) - 1);
  const raw = base * 2 ** Math.min(exponent, 24);
  return Math.round(Math.min(max, raw) * jitter(spread));
}

/** JSON.parse that returns a fallback instead of throwing. */
export function safeJsonParse(text, fallback = null) {
  if (typeof text !== 'string' || text.length === 0) return fallback;
  try {
    return JSON.parse(text);
  } catch {
    return fallback;
  }
}

/**
 * Unique sentinel for "parse failed". A plain `undefined` cannot be used here
 * because passing it as `safeJsonParse`'s second argument would trigger the
 * `fallback = null` default parameter and make a failed parse indistinguishable
 * from a successful `null` result.
 */
const PARSE_FAILED = Symbol('parse-failed');

/**
 * Extract the first balanced JSON object/array embedded in a text blob.
 * Useful when a model wraps structured output in prose or ``` fences.
 */
export function extractJson(text, fallback = null) {
  if (typeof text !== 'string') return fallback;
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const candidates = [];
  if (fenced && fenced[1]) candidates.push(fenced[1].trim());
  candidates.push(text.trim());
  for (const candidate of candidates) {
    const direct = safeJsonParse(candidate, PARSE_FAILED);
    if (direct !== PARSE_FAILED) return direct;
    const start = candidate.search(/[[{]/);
    if (start === -1) continue;
    const opener = candidate[start];
    const closer = opener === '{' ? '}' : ']';
    let depth = 0;
    let inString = false;
    let escaped = false;
    for (let i = start; i < candidate.length; i += 1) {
      const char = candidate[i];
      if (inString) {
        if (escaped) escaped = false;
        else if (char === '\\') escaped = true;
        else if (char === '"') inString = false;
        continue;
      }
      if (char === '"') inString = true;
      else if (char === opener) depth += 1;
      else if (char === closer) {
        depth -= 1;
        if (depth === 0) {
          const parsed = safeJsonParse(candidate.slice(start, i + 1), PARSE_FAILED);
          if (parsed !== PARSE_FAILED) return parsed;
          break;
        }
      }
    }
  }
  return fallback;
}

/** Truncate a string for log output. */
export function truncate(text, max = 220) {
  const value = typeof text === 'string' ? text : stableStringify(text);
  if (value.length <= max) return value;
  return `${value.slice(0, max)}…(+${value.length - max})`;
}

/**
 * Redact obvious secrets from a string (API keys, bearer tokens) before logging.
 */
export function redact(text) {
  if (typeof text !== 'string') return text;
  return text
    .replace(/(sk-[A-Za-z0-9_-]{6})[A-Za-z0-9_-]+/g, '$1***')
    .replace(/(AIza[A-Za-z0-9_-]{4})[A-Za-z0-9_-]+/g, '$1***')
    .replace(/(gsk_[A-Za-z0-9]{4})[A-Za-z0-9]+/g, '$1***')
    .replace(/(csk-[A-Za-z0-9]{4})[A-Za-z0-9]+/g, '$1***')
    .replace(/(Bearer\s+[A-Za-z0-9_-]{4})[A-Za-z0-9._-]+/gi, '$1***');
}

/** Mask a credential so it can be surfaced on the dashboard safely. */
export function maskSecret(secret) {
  const value = String(secret ?? '');
  if (value.length === 0) return '(empty)';
  if (value.length <= 8) return `${value.slice(0, 2)}***`;
  return `${value.slice(0, 4)}***${value.slice(-2)}`;
}

const LEVELS = { debug: 10, info: 20, warn: 30, error: 40, silent: 100 };

/**
 * Structured JSON logger. One line per event keeps container logs greppable and
 * machine-parseable without pulling in pino/winston.
 */
export function createLogger(scope, level = process.env.LOG_LEVEL || 'info') {
  const threshold = LEVELS[String(level).toLowerCase()] ?? LEVELS.info;
  const emit = (levelName, message, meta) => {
    if (LEVELS[levelName] < threshold) return;
    const record = {
      ts: new Date().toISOString(),
      level: levelName,
      scope,
      msg: typeof message === 'string' ? redact(message) : message,
    };
    if (meta && typeof meta === 'object') Object.assign(record, meta);
    const line = JSON.stringify(record, (_key, value) => (typeof value === 'string' ? redact(value) : value));
    if (levelName === 'error') process.stderr.write(`${line}\n`);
    else process.stdout.write(`${line}\n`);
  };
  return {
    debug: (message, meta) => emit('debug', message, meta),
    info: (message, meta) => emit('info', message, meta),
    warn: (message, meta) => emit('warn', message, meta),
    error: (message, meta) => emit('error', message, meta),
    child: (childScope) => createLogger(`${scope}:${childScope}`, level),
  };
}

/**
 * fetch() with an AbortController timeout. Returns the Response or throws a
 * DOMException named 'AbortError' / 'TimeoutError'.
 */
export async function fetchWithTimeout(url, options = {}, timeoutMs = 60000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error(`timeout after ${timeoutMs}ms`)), timeoutMs);
  timer.unref?.();
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

/** Start-of-day epoch ms for an IANA timezone (handles DST via Intl). */
export function startOfDayInZone(timeZone = 'UTC', reference = Date.now()) {
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
  const parts = Object.fromEntries(formatter.formatToParts(new Date(reference)).map((p) => [p.type, p.value]));
  const hour = parts.hour === '24' ? 0 : Number(parts.hour);
  const elapsedMs = ((hour * 60 + Number(parts.minute)) * 60 + Number(parts.second)) * 1000;
  return reference - elapsedMs - (reference % 1000);
}

/** Epoch ms of the next midnight in the given timezone. */
export function nextMidnightInZone(timeZone = 'UTC', reference = Date.now()) {
  const startOfToday = startOfDayInZone(timeZone, reference);
  // Add 25h then re-normalise so DST transitions never produce a past timestamp.
  return startOfDayInZone(timeZone, startOfToday + 25 * 60 * 60 * 1000);
}

/** Human readable duration. */
export function humanizeMs(ms) {
  const value = Math.max(0, Math.round(Number(ms) || 0));
  if (value < 1000) return `${value}ms`;
  const seconds = Math.floor(value / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

/** Escape a string for safe interpolation into HTML. */
export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
