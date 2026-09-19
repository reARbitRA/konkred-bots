/**
 * Konkred AI Gateway - orchestrator.
 *
 * Request pipeline:
 *   validate -> user fair-use limiter -> cache lookup -> in-flight dedup ->
 *   router plan -> key-pool slot acquisition -> provider call ->
 *   (fallback recovery loop) -> cache store -> metrics
 */
import { CONFIG } from '../config.mjs';
import policyStoreSingleton, { PolicyStore } from '../policy-store.mjs';
import { createProviders } from '../providers/index.mjs';
import { ResponseCache } from './cache.mjs';
import { InflightDeduplicator } from './dedup.mjs';
import { KeyPool } from './key-pool.mjs';
import { SmartRouter, TASK_TYPES, normalizeTaskType } from './router.mjs';
import { FallbackEngine, ExhaustedError } from './fallback.mjs';
import { FusionEngine } from './fusion.mjs';
import { UserLimiter } from './user-limiter.mjs';
import { createLogger, estimateMessageTokens, requestId, round } from '../util.mjs';

const log = createLogger('gateway');

/** Public, typed request-validation error. */
export class ValidationError extends Error {
  constructor(message, { code = 'invalid_request', status = 400, field = null } = {}) {
    super(message);
    this.name = 'ValidationError';
    this.code = code;
    this.status = status;
    this.field = field;
  }
}

/** Per-user throttle rejection. */
export class RateLimitError extends Error {
  constructor(message, { retryAfterMs = 1000, reason = 'user_rpm', limits = {}, usage = {} } = {}) {
    super(message);
    this.name = 'RateLimitError';
    this.code = reason;
    this.status = 429;
    this.retryAfterMs = retryAfterMs;
    this.limits = limits;
    this.usage = usage;
  }
}

const MAX_MESSAGES = 60;
const MAX_TEXT_CHARS = 2_000_000;

/** Validate + normalise the inbound JSON body into a canonical request. */
export function validateRequest(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    throw new ValidationError('Request body must be a JSON object');
  }

  const rawMessages = Array.isArray(body.messages) ? body.messages : null;
  const prompt = typeof body.prompt === 'string' ? body.prompt : null;
  if (!rawMessages && !prompt) {
    throw new ValidationError('Either "messages" (array) or "prompt" (string) is required', { field: 'messages' });
  }

  const source = rawMessages ?? [{ role: 'user', content: prompt }];
  if (!source.length) throw new ValidationError('"messages" must not be empty', { field: 'messages' });
  if (source.length > MAX_MESSAGES) {
    throw new ValidationError(`"messages" must contain at most ${MAX_MESSAGES} entries`, { field: 'messages' });
  }

  let totalChars = 0;
  const messages = source.map((message, index) => {
    if (!message || typeof message !== 'object') {
      throw new ValidationError(`messages[${index}] must be an object`, { field: `messages[${index}]` });
    }
    const role = ['user', 'assistant', 'system'].includes(message.role) ? message.role : 'user';
    const content = message.content;
    if (typeof content === 'string') {
      totalChars += content.length;
      return { role, content };
    }
    if (Array.isArray(content)) {
      const parts = content.map((part, partIndex) => {
        if (typeof part === 'string') {
          totalChars += part.length;
          return { type: 'text', text: part };
        }
        if (!part || typeof part !== 'object') {
          throw new ValidationError(`messages[${index}].content[${partIndex}] is invalid`, {
            field: `messages[${index}].content[${partIndex}]`,
          });
        }
        if (typeof part.text === 'string' && !part.inlineData && !part.inline_data) {
          totalChars += part.text.length;
          return { type: 'text', text: part.text };
        }
        const inline = part.inlineData ?? part.inline_data;
        if (inline) {
          const mimeType = inline.mimeType ?? inline.mime_type;
          const data = inline.data;
          if (typeof mimeType !== 'string' || typeof data !== 'string' || !data.length) {
            throw new ValidationError(
              `messages[${index}].content[${partIndex}].inlineData requires mimeType and base64 data`,
              { field: `messages[${index}].content[${partIndex}]` },
            );
          }
          totalChars += Math.round(data.length / 100); // media counted lightly
          return { type: 'inline_data', inlineData: { mimeType, data } };
        }
        if (part.type === 'image_url' && part.image_url?.url) {
          totalChars += 64;
          return { type: 'image_url', image_url: { url: String(part.image_url.url) } };
        }
        throw new ValidationError(`messages[${index}].content[${partIndex}] has an unsupported shape`, {
          field: `messages[${index}].content[${partIndex}]`,
        });
      });
      return { role, content: parts };
    }
    throw new ValidationError(`messages[${index}].content must be a string or an array of parts`, {
      field: `messages[${index}].content`,
    });
  });

  if (totalChars > MAX_TEXT_CHARS) {
    throw new ValidationError(`Payload too large: ${totalChars} chars exceeds ${MAX_TEXT_CHARS}`, {
      code: 'payload_too_large', status: 413,
    });
  }

  const temperature = body.temperature === undefined ? 0.3 : Number(body.temperature);
  if (!Number.isFinite(temperature) || temperature < 0 || temperature > 2) {
    throw new ValidationError('"temperature" must be a number between 0 and 2', { field: 'temperature' });
  }
  const maxTokens = body.maxTokens === undefined && body.max_tokens === undefined
    ? 2048
    : Number(body.maxTokens ?? body.max_tokens);
  if (!Number.isFinite(maxTokens) || maxTokens < 1 || maxTokens > 65536) {
    throw new ValidationError('"maxTokens" must be a number between 1 and 65536', { field: 'maxTokens' });
  }

  const model = body.model === undefined || body.model === null || body.model === ''
    ? null
    : String(body.model);
  const privacy = body.privacy === 'private' ? 'private' : 'shared';

  return {
    taskType: normalizeTaskType(body.taskType ?? body.task_type ?? body.task),
    messages,
    system: typeof body.system === 'string' && body.system.trim() ? body.system.trim() : null,
    temperature,
    maxTokens: Math.floor(maxTokens),
    model,
    jsonMode: Boolean(body.jsonMode ?? body.json_mode ?? body.json),
    stopSequences: Array.isArray(body.stopSequences)
      ? body.stopSequences.filter((entry) => typeof entry === 'string').slice(0, 4)
      : [],
    privacy,
    noCache: Boolean(body.noCache ?? body.no_cache),
    fusion: Boolean(body.fusion),
    userId: body.userId === undefined || body.userId === null ? 'anonymous' : String(body.userId).slice(0, 64),
  };
}

export class Gateway {
  constructor({ config = CONFIG, policyStore = policyStoreSingleton, providers = null } = {}) {
    this.config = config;
    this.policyStore = policyStore instanceof PolicyStore ? policyStore : policyStoreSingleton;
    this.providers = providers ?? createProviders(config);
    this.cache = new ResponseCache({
      maxEntries: config.cacheMaxEntries, ttlMs: config.cacheTtlMs, enabled: config.cacheEnabled,
    });
    this.dedup = new InflightDeduplicator({ ttlMs: config.dedupTtlMs, enabled: config.dedupEnabled });
    this.keyPool = new KeyPool({ policyStore: this.policyStore, config });
    this.router = new SmartRouter({ policyStore: this.policyStore, config });
    this.limiter = new UserLimiter({ config });
    this.metrics = {
      startedAt: Date.now(),
      requests: 0,
      succeeded: 0,
      failed: 0,
      throttled: 0,
      cacheHits: 0,
      coalesced: 0,
      tokens: { prompt: 0, completion: 0, total: 0 },
      latencyMsSum: 0,
      byTask: Object.fromEntries(TASK_TYPES.map((task) => [task, { requests: 0, errors: 0 }])),
      byModel: {},
      byErrorClass: {},
      recordSuccess: ({ model, latencyMs, usage, task }) => {
        const bucket = this.metrics.byModel[model] ?? { success: 0, errors: 0, tokens: 0, latencyMsSum: 0 };
        bucket.success += 1;
        bucket.tokens += usage?.totalTokens ?? 0;
        bucket.latencyMsSum += latencyMs ?? 0;
        this.metrics.byModel[model] = bucket;
        if (this.metrics.byTask[task]) this.metrics.byTask[task].requests += 1;
      },
      recordFailure: ({ model, errorClass, task }) => {
        const bucket = this.metrics.byModel[model] ?? { success: 0, errors: 0, tokens: 0, latencyMsSum: 0 };
        bucket.errors += 1;
        this.metrics.byModel[model] = bucket;
        this.metrics.byErrorClass[errorClass] = (this.metrics.byErrorClass[errorClass] ?? 0) + 1;
        if (this.metrics.byTask[task]) this.metrics.byTask[task].errors += 1;
      },
    };
    this.fallback = new FallbackEngine({
      policyStore: this.policyStore,
      keyPool: this.keyPool,
      router: this.router,
      providers: this.providers,
      config,
      metrics: this.metrics,
    });
    this.fusion = new FusionEngine({
      fallbackEngine: this.fallback, policyStore: this.policyStore, config,
    });
  }

  /**
   * Handle one canonical AI request end-to-end.
   * @param {object} body raw JSON body
   * @param {{signal?:AbortSignal, requestId?:string}} [context]
   */
  async handle(body, context = {}) {
    const id = context.requestId ?? requestId();
    const startedAt = Date.now();
    const request = validateRequest(body);
    this.metrics.requests += 1;

    const estimatedTokens = estimateMessageTokens(request.messages) + request.maxTokens;

    // 1. Fair-use limiter -------------------------------------------------
    const verdict = this.limiter.consume(request.userId, { estimatedTokens });
    if (!verdict.allowed) {
      this.metrics.throttled += 1;
      throw new RateLimitError(
        `Per-user limit reached (${verdict.reason}). Try again in ${Math.ceil(verdict.retryAfterMs / 1000)}s.`,
        verdict,
      );
    }

    // 2. Cache -------------------------------------------------------------
    const cacheable = this.cache.isCacheable(request) && !request.fusion;
    const cacheKey = this.cache.key(request);
    if (cacheable) {
      const cached = this.cache.get(cacheKey);
      if (cached) {
        this.metrics.cacheHits += 1;
        this.metrics.succeeded += 1;
        log.info('cache hit', { requestId: id, task: request.taskType, model: cached.model });
        return {
          ...cached,
          requestId: id,
          cached: true,
          latencyMs: Date.now() - startedAt,
        };
      }
    }

    // 3. Dedup + execute ---------------------------------------------------
    const execute = async () => {
      const result = request.fusion && this.config.fusionEnabled
        ? await this.fusion.execute({ ...request, signal: context.signal })
        : await this.fallback.execute({ ...request, signal: context.signal });
      return result;
    };

    let result;
    try {
      result = request.privacy === 'private'
        ? await execute()
        : await this.dedup.run(cacheKey, execute, {
          onCoalesced: () => {
            this.metrics.coalesced += 1;
            log.debug('request coalesced', { requestId: id, task: request.taskType });
          },
        });
    } catch (error) {
      this.metrics.failed += 1;
      if (error instanceof ExhaustedError) {
        log.error('request exhausted', {
          requestId: id,
          task: request.taskType,
          attempts: error.attempts?.length ?? 0,
          lastErrorClass: error.lastError?.errorClass,
        });
      }
      throw error;
    }

    // 4. Accounting --------------------------------------------------------
    const latencyMs = Date.now() - startedAt;
    this.metrics.succeeded += 1;
    this.metrics.latencyMsSum += latencyMs;
    this.metrics.tokens.prompt += result.usage?.promptTokens ?? 0;
    this.metrics.tokens.completion += result.usage?.completionTokens ?? 0;
    this.metrics.tokens.total += result.usage?.totalTokens ?? 0;
    this.limiter.recordTokens(request.userId, result.usage?.totalTokens ?? 0);

    const payload = {
      requestId: id,
      text: result.text,
      task: result.task ?? request.taskType,
      model: result.model,
      provider: result.provider,
      upstreamModel: result.upstreamModel,
      finishReason: result.finishReason,
      usage: result.usage,
      attempts: result.attempts,
      fusion: result.fusion ?? { enabled: false },
      cached: false,
      latencyMs,
    };

    // 5. Cache store -------------------------------------------------------
    if (cacheable) this.cache.set(cacheKey, { ...payload, requestId: undefined });

    log.info('request completed', {
      requestId: id,
      task: payload.task,
      model: payload.model,
      provider: payload.provider,
      tokens: payload.usage?.totalTokens,
      latencyMs,
      attempts: payload.attempts?.length ?? 1,
    });

    return payload;
  }

  /** Liveness + capacity summary for /api/health. */
  health() {
    const pool = this.keyPool.snapshot();
    const readyModels = pool.models.filter((model) => model.ready > 0);
    const realReady = readyModels.filter((model) => model.provider !== 'mock');
    return {
      status: realReady.length > 0 ? 'healthy' : readyModels.length > 0 ? 'degraded' : 'unhealthy',
      service: this.config.serviceName,
      uptimeSeconds: Math.round((Date.now() - this.metrics.startedAt) / 1000),
      registryVersion: this.policyStore.version,
      models: { total: pool.models.length, ready: readyModels.length, realReady: realReady.length },
      slots: { total: pool.totalSlots, ready: pool.readySlots },
      cache: this.cache.snapshot(),
      dedup: this.dedup.snapshot(),
      requests: {
        total: this.metrics.requests,
        succeeded: this.metrics.succeeded,
        failed: this.metrics.failed,
        throttled: this.metrics.throttled,
        cacheHits: this.metrics.cacheHits,
        coalesced: this.metrics.coalesced,
        avgLatencyMs: this.metrics.succeeded
          ? Math.round(this.metrics.latencyMsSum / this.metrics.succeeded)
          : 0,
      },
      tokens: this.metrics.tokens,
    };
  }

  /** Full diagnostic payload for the admin dashboard. */
  stats() {
    return {
      health: this.health(),
      pool: this.keyPool.snapshot(),
      router: this.router.snapshot(),
      limiter: this.limiter.snapshot(),
      byModel: Object.fromEntries(
        Object.entries(this.metrics.byModel).map(([model, bucket]) => [model, {
          ...bucket,
          avgLatencyMs: bucket.success ? Math.round(bucket.latencyMsSum / bucket.success) : 0,
          successRate: bucket.success + bucket.errors
            ? round(bucket.success / (bucket.success + bucket.errors), 4)
            : 0,
        }]),
      ),
      byTask: this.metrics.byTask,
      byErrorClass: this.metrics.byErrorClass,
    };
  }

  /** Release timers/maps held by the gateway (graceful shutdown). */
  shutdown() {
    this.dedup.clear();
    this.cache.clear();
  }
}

export default Gateway;
