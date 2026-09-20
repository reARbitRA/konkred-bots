/**
 * Konkred AI Gateway - provider base class and shared error taxonomy.
 *
 * Every provider adapter converts the gateway's canonical request shape
 *   { messages, system, temperature, maxTokens, jsonMode, stopSequences }
 * into an upstream HTTP call, and converts the upstream response back into the
 * canonical completion shape
 *   { text, finishReason, usage: { promptTokens, completionTokens, totalTokens }, raw }
 */
import { estimateMessageTokens, estimateTokens } from '../util.mjs';

/** Canonical error classes used by the fallback engine. */
export const ERROR_CLASS = Object.freeze({
  RATE_LIMIT: 'rate_limit',
  AUTH: 'auth',
  CONTEXT_LENGTH: 'context_length',
  SAFETY_BLOCK: 'safety_block',
  BAD_REQUEST: 'bad_request',
  /**
   * The upstream no longer serves this model id (retired, renamed, or not
   * available to this account). Distinct from BAD_REQUEST because the request
   * itself is fine - only this candidate is unusable, so the chain must keep
   * going and the model should be benched rather than retried.
   */
  MODEL_UNAVAILABLE: 'model_unavailable',
  SERVER: 'server',
  TIMEOUT: 'timeout',
  NETWORK: 'network',
  EMPTY: 'empty_completion',
  UNKNOWN: 'unknown',
});

/** Error classes that should *not* be retried on another slot. */
export const TERMINAL_ERROR_CLASSES = new Set([ERROR_CLASS.BAD_REQUEST]);

export class ProviderError extends Error {
  constructor(message, {
    errorClass = ERROR_CLASS.UNKNOWN,
    status = 0,
    provider = 'unknown',
    model = 'unknown',
    retryAfterMs = 0,
    retryable = true,
    details = null,
    cause = undefined,
  } = {}) {
    super(message, cause ? { cause } : undefined);
    this.name = 'ProviderError';
    this.errorClass = errorClass;
    this.status = status;
    this.provider = provider;
    this.model = model;
    this.retryAfterMs = Math.max(0, Number(retryAfterMs) || 0);
    this.retryable = retryable && !TERMINAL_ERROR_CLASSES.has(errorClass);
    this.details = details;
  }

  toJSON() {
    return {
      name: this.name,
      message: this.message,
      errorClass: this.errorClass,
      status: this.status,
      provider: this.provider,
      model: this.model,
      retryAfterMs: this.retryAfterMs,
      retryable: this.retryable,
    };
  }
}

/** Abnormal finish reasons that mean "the model refused / was blocked". */
const SAFETY_FINISH_REASONS = new Set([
  'SAFETY', 'RECITATION', 'BLOCKLIST', 'PROHIBITED_CONTENT', 'SPII', 'IMAGE_SAFETY',
  'content_filter', 'content-filter', 'contentfilter',
]);

export function isSafetyFinishReason(reason) {
  if (!reason) return false;
  const normalized = String(reason).trim();
  return SAFETY_FINISH_REASONS.has(normalized) || SAFETY_FINISH_REASONS.has(normalized.toUpperCase());
}

/** Parse a `Retry-After` header (seconds or HTTP-date) into milliseconds. */
export function parseRetryAfter(headerValue) {
  if (!headerValue) return 0;
  const raw = String(headerValue).trim();
  const asNumber = Number(raw);
  if (Number.isFinite(asNumber)) return Math.max(0, Math.round(asNumber * 1000));
  // Groq / Cerebras style: "2m59.56s", "1.5s", "500ms"
  const composite = raw.match(/^((\d+(?:\.\d+)?)m)?((\d+(?:\.\d+)?)s)?((\d+(?:\.\d+)?)ms)?$/i);
  if (composite && (composite[2] || composite[4] || composite[6])) {
    const minutes = Number(composite[2] || 0);
    const seconds = Number(composite[4] || 0);
    const millis = Number(composite[6] || 0);
    return Math.round(minutes * 60000 + seconds * 1000 + millis);
  }
  const asDate = Date.parse(raw);
  if (Number.isFinite(asDate)) return Math.max(0, asDate - Date.now());
  return 0;
}

/** Map an HTTP status + body into a canonical error class. */
export function classifyHttpError(status, bodyText = '') {
  const body = String(bodyText || '').toLowerCase();
  if (status === 401 || status === 403) return ERROR_CLASS.AUTH;
  if (status === 429) return ERROR_CLASS.RATE_LIMIT;
  if (status === 408 || status === 504) return ERROR_CLASS.TIMEOUT;
  if (status >= 500) return ERROR_CLASS.SERVER;
  if (status === 413) return ERROR_CLASS.CONTEXT_LENGTH;
  if (status === 400 || status === 404 || status === 422) {
    if (body.includes('context length') || body.includes('context_length')
      || body.includes('too many tokens') || body.includes('maximum context')
      || body.includes('reduce the length') || body.includes('token limit')
      || body.includes('request too large') || body.includes('input is too long')) {
      return ERROR_CLASS.CONTEXT_LENGTH;
    }
    if (body.includes('safety') || body.includes('content_filter') || body.includes('content filter')
      || body.includes('blocked') || body.includes('moderation')) {
      return ERROR_CLASS.SAFETY_BLOCK;
    }
    if (body.includes('quota') || body.includes('rate limit') || body.includes('resource_exhausted')) {
      return ERROR_CLASS.RATE_LIMIT;
    }
    if (body.includes('api key') || body.includes('unauthorized') || body.includes('permission')) {
      return ERROR_CLASS.AUTH;
    }
    // Providers retire model ids on a rolling schedule. That must degrade to
    // "skip this candidate", never "abort the request" - otherwise a single
    // stale entry in the registry takes down every task routed through it.
    if (body.includes('decommission') || body.includes('model_not_found')
      || body.includes('model not found') || body.includes('does not exist')
      || body.includes('no longer supported') || body.includes('has been deprecated')
      || body.includes('unknown model') || body.includes('invalid model')
      || body.includes('unsupported model') || body.includes('model_terminated')) {
      return ERROR_CLASS.MODEL_UNAVAILABLE;
    }
    return ERROR_CLASS.BAD_REQUEST;
  }
  return ERROR_CLASS.UNKNOWN;
}

/** Convert a thrown fetch/abort error into a ProviderError. */
export function classifyTransportError(error, context = {}) {
  const name = error?.name ?? '';
  const message = String(error?.message ?? error ?? 'transport error');
  const isAbort = name === 'AbortError' || name === 'TimeoutError' || /timeout|timed out|aborted/i.test(message);
  return new ProviderError(message, {
    errorClass: isAbort ? ERROR_CLASS.TIMEOUT : ERROR_CLASS.NETWORK,
    status: 0,
    retryable: true,
    cause: error,
    ...context,
  });
}

/**
 * Base provider adapter. Subclasses implement `complete()`.
 */
export class BaseProvider {
  /**
   * @param {object} options
   * @param {string} options.name          provider id, e.g. 'groq'
   * @param {object} options.providerConfig runtime config slice (baseUrl, keys…)
   * @param {number} options.timeoutMs     upstream timeout
   */
  constructor({ name, providerConfig = {}, timeoutMs = 90000 }) {
    this.name = name;
    this.providerConfig = providerConfig;
    this.timeoutMs = timeoutMs;
  }

  get baseUrl() {
    return String(this.providerConfig.baseUrl ?? '').replace(/\/+$/, '');
  }

  /**
   * Execute a completion.
   * @abstract
   * @returns {Promise<{text: string, finishReason: string, usage: object, raw: any}>}
   */
  // eslint-disable-next-line no-unused-vars, class-methods-use-this
  async complete(_request) {
    throw new ProviderError(`Provider ${this.name} does not implement complete()`, {
      errorClass: ERROR_CLASS.BAD_REQUEST,
      provider: this.name,
      retryable: false,
    });
  }

  /** Build a ProviderError bound to this provider/model. */
  error(message, options = {}) {
    return new ProviderError(message, { provider: this.name, ...options });
  }

  /**
   * Normalise usage numbers, estimating anything the upstream omitted.
   */
  static normalizeUsage(usage, { messages, text } = {}) {
    const promptTokens = Number(usage?.prompt_tokens ?? usage?.promptTokenCount ?? usage?.input_tokens ?? 0)
      || estimateMessageTokens(messages ?? []);
    const completionTokens = Number(
      usage?.completion_tokens ?? usage?.candidatesTokenCount ?? usage?.output_tokens ?? 0,
    ) || estimateTokens(text ?? '');
    const totalTokens = Number(usage?.total_tokens ?? usage?.totalTokenCount ?? 0)
      || promptTokens + completionTokens;
    return {
      promptTokens: Math.max(0, Math.round(promptTokens)),
      completionTokens: Math.max(0, Math.round(completionTokens)),
      totalTokens: Math.max(0, Math.round(totalTokens)),
    };
  }

  /**
   * Flatten the canonical `messages` array into OpenAI chat format,
   * folding multimodal parts down to text when the provider is text-only.
   */
  static toPlainMessages(messages, { system, supportsMultimodal = false } = {}) {
    const out = [];
    if (system) out.push({ role: 'system', content: String(system) });
    for (const message of messages ?? []) {
      const role = message.role === 'assistant' ? 'assistant' : message.role === 'system' ? 'system' : 'user';
      const content = message.content;
      if (typeof content === 'string') {
        out.push({ role, content });
        continue;
      }
      if (Array.isArray(content)) {
        if (supportsMultimodal) {
          const parts = [];
          for (const part of content) {
            if (typeof part === 'string') parts.push({ type: 'text', text: part });
            else if (part?.type === 'text' && typeof part.text === 'string') parts.push({ type: 'text', text: part.text });
            else if (part?.type === 'image_url' && part.image_url?.url) parts.push(part);
            else if (part?.type === 'inline_data' || part?.inlineData) {
              const inline = part.inlineData ?? part.inline_data ?? {};
              const mime = inline.mimeType ?? inline.mime_type ?? 'application/octet-stream';
              if (String(mime).startsWith('image/')) {
                parts.push({ type: 'image_url', image_url: { url: `data:${mime};base64,${inline.data}` } });
              } else {
                parts.push({ type: 'text', text: `[attachment ${mime} omitted: provider does not accept this media type]` });
              }
            } else if (part?.text) {
              parts.push({ type: 'text', text: String(part.text) });
            }
          }
          out.push({ role, content: parts.length ? parts : '' });
        } else {
          const text = content
            .map((part) => {
              if (typeof part === 'string') return part;
              if (part?.type === 'text' || typeof part?.text === 'string') return part.text ?? '';
              const inline = part?.inlineData ?? part?.inline_data;
              if (inline) return `[attachment ${inline.mimeType ?? inline.mime_type ?? 'binary'} omitted]`;
              if (part?.type === 'image_url') return '[image omitted]';
              return '';
            })
            .filter(Boolean)
            .join('\n');
          out.push({ role, content: text });
        }
        continue;
      }
      out.push({ role, content: content == null ? '' : String(content) });
    }
    return out;
  }

  /** True when any message carries non-text media. */
  static hasMedia(messages) {
    for (const message of messages ?? []) {
      if (!Array.isArray(message?.content)) continue;
      for (const part of message.content) {
        if (part?.inlineData || part?.inline_data || part?.type === 'image_url'
          || part?.type === 'input_audio' || part?.type === 'inline_data') return true;
      }
    }
    return false;
  }
}

export default BaseProvider;
