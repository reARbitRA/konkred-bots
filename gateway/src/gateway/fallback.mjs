/**
 * Konkred AI Gateway - strict error-class fallback engine.
 *
 * Walks the router's candidate chain, acquiring a key-pool slot for each model
 * and dispatching the upstream call. Every failure is classified and mapped to
 * a *specific* recovery action:
 *
 *   rate_limit      -> cool the slot down (Retry-After or exponential backoff),
 *                      try the next slot of the same model, then the next model
 *   auth            -> disable that credential permanently (until restart)
 *   context_length  -> raise the context requirement and re-plan with models
 *                      whose window is strictly larger
 *   safety_block    -> bench the model for a few minutes (another family may
 *                      answer the same prompt)
 *   server/timeout  -> bench the whole provider briefly (regional outage)
 *   bad_request     -> terminal, abort immediately (our payload is wrong)
 */
import { ERROR_CLASS, ProviderError } from '../providers/index.mjs';
import { CONFIG } from '../config.mjs';
import { createLogger, humanizeMs, truncate } from '../util.mjs';

const log = createLogger('fallback');

export const RECOVERY_ACTIONS = Object.freeze({
  [ERROR_CLASS.RATE_LIMIT]: 'cooldown-slot',
  [ERROR_CLASS.AUTH]: 'disable-slot',
  [ERROR_CLASS.CONTEXT_LENGTH]: 'raise-context',
  [ERROR_CLASS.SAFETY_BLOCK]: 'bench-model',
  // A retired/unknown model id is a permanent property of that candidate, not
  // of the request: bench the model hard and move to the next one.
  [ERROR_CLASS.MODEL_UNAVAILABLE]: 'bench-model',
  [ERROR_CLASS.SERVER]: 'bench-provider',
  [ERROR_CLASS.TIMEOUT]: 'bench-provider',
  [ERROR_CLASS.NETWORK]: 'bench-provider',
  [ERROR_CLASS.EMPTY]: 'retry-next',
  [ERROR_CLASS.BAD_REQUEST]: 'abort',
  [ERROR_CLASS.UNKNOWN]: 'retry-next',
});

/** Public error surfaced to API clients when every candidate failed. */
export class ExhaustedError extends Error {
  constructor(message, { attempts = [], lastError = null, retryAfterMs = 0 } = {}) {
    super(message);
    this.name = 'ExhaustedError';
    this.attempts = attempts;
    this.lastError = lastError;
    this.retryAfterMs = retryAfterMs;
    this.status = lastError?.errorClass === ERROR_CLASS.RATE_LIMIT ? 429 : 503;
    this.code = lastError?.errorClass === ERROR_CLASS.RATE_LIMIT ? 'all_slots_rate_limited' : 'all_candidates_failed';
  }
}

export class FallbackEngine {
  constructor({ policyStore, keyPool, router, providers, config = CONFIG, metrics = null } = {}) {
    if (!policyStore || !keyPool || !router || !providers) {
      throw new Error('FallbackEngine requires policyStore, keyPool, router and providers');
    }
    this.policyStore = policyStore;
    this.keyPool = keyPool;
    this.router = router;
    this.providers = providers;
    this.config = config;
    this.metrics = metrics;
  }

  /** Apply the recovery action for a failed attempt. */
  applyRecovery(error, { slot, modelKey, policy, state }) {
    const errorClass = error?.errorClass ?? ERROR_CLASS.UNKNOWN;
    const action = RECOVERY_ACTIONS[errorClass] ?? 'retry-next';

    switch (action) {
      case 'cooldown-slot':
        slot?.recordFailure(ERROR_CLASS.RATE_LIMIT, {
          retryAfterMs: error.retryAfterMs,
          message: error.message,
        });
        break;
      case 'disable-slot':
        slot?.recordFailure(ERROR_CLASS.AUTH, { message: error.message });
        log.error('credential disabled', {
          model: modelKey, provider: policy?.provider, source: slot?.credentialSource,
        });
        break;
      case 'raise-context': {
        slot?.recordFailure(ERROR_CLASS.CONTEXT_LENGTH, { message: error.message });
        const window = policy?.contextWindow ?? 0;
        state.minContext = Math.max(state.minContext, window + 1);
        // Drop every remaining candidate whose window cannot hold the prompt.
        state.remaining = state.remaining.filter((key) => {
          const candidate = this.policyStore.get(key);
          return candidate && candidate.contextWindow >= state.minContext;
        });
        break;
      }
      case 'bench-model': {
        // A safety block is transient (this prompt, this moment); a retired
        // model id is permanent until the registry is updated, so bench it for
        // the rest of the process lifetime rather than retrying every 5 min.
        const permanent = errorClass === ERROR_CLASS.MODEL_UNAVAILABLE;
        const benchMs = permanent ? 24 * 60 * 60 * 1000 : 5 * 60 * 1000;
        slot?.recordFailure(errorClass, { message: error.message });
        this.router.benchModel(modelKey, benchMs, permanent ? 'model-unavailable' : 'safety-block');
        if (permanent) {
          log.error('model id rejected by upstream - benching it for 24h; update the registry', {
            model: modelKey,
            message: String(error.message ?? '').slice(0, 300),
          });
        }
        state.remaining = state.remaining.filter((key) => key !== modelKey);
        break;
      }
      case 'bench-provider': {
        slot?.recordFailure(errorClass, { message: error.message });
        const provider = policy?.provider;
        if (provider && provider !== 'mock') {
          const strikes = (state.providerStrikes.get(provider) ?? 0) + 1;
          state.providerStrikes.set(provider, strikes);
          if (strikes >= 2) {
            this.keyPool.benchProvider(provider, 60 * 1000 * strikes, errorClass);
            state.remaining = state.remaining.filter(
              (key) => this.policyStore.get(key)?.provider !== provider,
            );
          }
        }
        break;
      }
      case 'abort':
        slot?.recordFailure(ERROR_CLASS.BAD_REQUEST, { message: error.message });
        state.aborted = true;
        break;
      default:
        slot?.recordFailure(errorClass, { message: error.message });
    }
    return action;
  }

  /**
   * Execute a request against the candidate chain.
   *
   * @param {object} request canonical gateway request
   * @param {AbortSignal} [request.signal]
   * @returns {Promise<{text:string, model:string, provider:string, usage:object, attempts:Array, finishReason:string}>}
   */
  async execute(request) {
    const startedAt = Date.now();
    const plan = this.router.plan(request);
    const state = {
      minContext: plan.requiredContext,
      remaining: [...plan.candidates],
      providerStrikes: new Map(),
      aborted: false,
    };

    const attempts = [];
    let lastError = null;
    let bestRetryAfterMs = 0;
    const maxAttempts = Math.max(1, Number(this.config.maxAttempts) || 6);

    if (!state.remaining.length) {
      throw new ExhaustedError(
        'No candidate model satisfies this request (check credentials, capabilities and context window)',
        { attempts: [], lastError: null },
      );
    }

    while (state.remaining.length && attempts.length < maxAttempts && !state.aborted) {
      const modelKey = state.remaining.shift();
      const policy = this.policyStore.get(modelKey);
      if (!policy) continue;

      const estimatedTokens = plan.estimatedTokens + Number(request.maxTokens || 0);
      const acquisition = this.keyPool.acquire(modelKey, estimatedTokens);
      if (!acquisition.slot) {
        attempts.push({
          model: modelKey,
          provider: policy.provider,
          outcome: 'skipped',
          reason: acquisition.reason,
          retryAfterMs: acquisition.retryAfterMs ?? 0,
        });
        if (Number.isFinite(acquisition.retryAfterMs) && acquisition.retryAfterMs > 0) {
          bestRetryAfterMs = bestRetryAfterMs === 0
            ? acquisition.retryAfterMs
            : Math.min(bestRetryAfterMs, acquisition.retryAfterMs);
        }
        continue;
      }

      const slot = acquisition.slot;
      const provider = this.providers.get(policy.provider);
      const attemptStartedAt = Date.now();

      if (!provider) {
        slot.release();
        attempts.push({ model: modelKey, provider: policy.provider, outcome: 'skipped', reason: 'no-adapter' });
        continue;
      }

      try {
        const completion = await provider.complete({
          policy,
          credential: slot.credential,
          messages: request.messages,
          system: request.system,
          temperature: request.temperature,
          maxTokens: Math.min(Number(request.maxTokens) || 2048, policy.maxOutputTokens),
          jsonMode: Boolean(request.jsonMode),
          stopSequences: request.stopSequences,
          taskType: plan.task,
          signal: request.signal,
        });

        const latencyMs = Date.now() - attemptStartedAt;
        slot.release();
        slot.recordSuccess({ tokens: completion.usage.totalTokens, latencyMs });

        attempts.push({
          model: modelKey,
          provider: policy.provider,
          outcome: 'success',
          latencyMs,
          tokens: completion.usage.totalTokens,
        });

        this.metrics?.recordSuccess?.({
          model: modelKey, provider: policy.provider, latencyMs, usage: completion.usage, task: plan.task,
        });

        return {
          text: completion.text,
          finishReason: completion.finishReason,
          usage: completion.usage,
          model: modelKey,
          provider: policy.provider,
          upstreamModel: policy.upstreamModel,
          task: plan.task,
          attempts,
          latencyMs,
          totalLatencyMs: Date.now() - startedAt,
          plan: { requiredContext: plan.requiredContext, capabilities: plan.capabilities },
        };
      } catch (rawError) {
        slot.release();
        const error = rawError instanceof ProviderError
          ? rawError
          : new ProviderError(String(rawError?.message ?? rawError), {
            errorClass: ERROR_CLASS.UNKNOWN, provider: policy.provider, model: modelKey, cause: rawError,
          });
        const latencyMs = Date.now() - attemptStartedAt;
        const action = this.applyRecovery(error, { slot, modelKey, policy, state });
        lastError = error;
        if (error.retryAfterMs > 0) {
          bestRetryAfterMs = bestRetryAfterMs === 0
            ? error.retryAfterMs
            : Math.min(bestRetryAfterMs, error.retryAfterMs);
        }

        attempts.push({
          model: modelKey,
          provider: policy.provider,
          outcome: 'error',
          errorClass: error.errorClass,
          status: error.status,
          action,
          latencyMs,
          message: truncate(error.message, 200),
        });

        this.metrics?.recordFailure?.({
          model: modelKey, provider: policy.provider, errorClass: error.errorClass, task: plan.task,
        });

        log.warn('attempt failed', {
          model: modelKey,
          provider: policy.provider,
          errorClass: error.errorClass,
          status: error.status,
          action,
          retryAfter: error.retryAfterMs ? humanizeMs(error.retryAfterMs) : undefined,
          message: truncate(error.message, 160),
        });

        if (action === 'abort') break;
      }
    }

    const reason = state.aborted
      ? 'request rejected by upstream (bad request)'
      : attempts.length >= maxAttempts
        ? `attempt budget exhausted after ${attempts.length} tries`
        : 'every candidate model was unavailable or failed';

    throw new ExhaustedError(`AI gateway could not complete the request: ${reason}`, {
      attempts,
      lastError,
      retryAfterMs: bestRetryAfterMs,
    });
  }
}

export default FallbackEngine;
