/**
 * Konkred AI Gateway - quota-aware key pool manager.
 *
 * A *slot* is the tuple (provider, model, credential). Each slot maintains:
 *   - `minuteLog`: sliding 60s window of { at, tokens } samples -> RPM / TPM
 *   - `dayCounters`: request + token counters reset at midnight in the model's
 *     own timezone (Pacific for Gemini, UTC elsewhere) -> RPD / TPD
 *   - `monthLog`: rolling 30-day token samples -> monthly token ceilings
 *   - failure state: consecutive errors, exponential backoff with jitter,
 *     hard disable on auth failures, cooldowns on 429.
 *
 * Safety headroom keeps us strictly below the published limits:
 *   85% RPM · 90% TPM · 95% RPD · 98% TPD
 */
import { CONFIG } from '../config.mjs';
import {
  backoffDelay, createLogger, maskSecret, nextMidnightInZone, round, sha256, startOfDayInZone,
} from '../util.mjs';

const log = createLogger('key-pool');

const MINUTE_MS = 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
const MONTH_MS = 30 * DAY_MS;

export class KeySlot {
  constructor({ policy, credential, credentialSource, headroom }) {
    this.policy = policy;
    this.provider = policy.provider;
    this.model = policy.key;
    this.credential = credential;
    this.credentialSource = credentialSource;
    this.id = `${policy.key}#${sha256(credential).slice(0, 8)}`;
    this.headroom = headroom;

    /** @type {{at:number, tokens:number}[]} sliding 60s window */
    this.minuteLog = [];
    /** @type {{at:number, tokens:number}[]} rolling 30-day window */
    this.monthLog = [];

    this.dayWindowStart = startOfDayInZone(policy.resetTimezone);
    this.dayCounters = { requests: 0, tokens: 0 };

    this.inFlight = 0;
    this.consecutiveErrors = 0;
    this.cooldownUntil = 0;
    this.disabledUntil = 0;
    this.disabledReason = null;
    this.lastUsedAt = 0;
    this.lastErrorAt = 0;
    this.lastError = null;
    this.totals = { requests: 0, tokens: 0, failures: 0, rateLimits: 0, latencyMsSum: 0 };
    this.observedLimits = {};
  }

  /** Effective quota after safety headroom. 0 means "no published limit". */
  effective(field) {
    const limit = Number(this.policy.quotas?.[field] ?? 0);
    if (!limit) return Infinity;
    const observed = Number(this.observedLimits?.[field] ?? 0);
    const base = observed > 0 ? Math.min(limit, observed) : limit;
    const factor = this.headroom?.[field] ?? 1;
    return Math.max(1, Math.floor(base * factor));
  }

  /** Drop samples that fell out of their window and roll the daily counters. */
  prune(nowMs = Date.now()) {
    const minuteCutoff = nowMs - MINUTE_MS;
    while (this.minuteLog.length && this.minuteLog[0].at <= minuteCutoff) this.minuteLog.shift();

    const monthCutoff = nowMs - MONTH_MS;
    while (this.monthLog.length && this.monthLog[0].at <= monthCutoff) this.monthLog.shift();

    const currentDayStart = startOfDayInZone(this.policy.resetTimezone, nowMs);
    if (currentDayStart > this.dayWindowStart) {
      this.dayWindowStart = currentDayStart;
      this.dayCounters = { requests: 0, tokens: 0 };
    }
    return this;
  }

  /** Current sliding-window usage. */
  usage(nowMs = Date.now()) {
    this.prune(nowMs);
    const minuteRequests = this.minuteLog.length;
    const minuteTokens = this.minuteLog.reduce((sum, sample) => sum + sample.tokens, 0);
    const monthTokens = this.monthLog.reduce((sum, sample) => sum + sample.tokens, 0);
    return {
      rpm: minuteRequests,
      tpm: minuteTokens,
      rpd: this.dayCounters.requests,
      tpd: this.dayCounters.tokens,
      tpmonth: monthTokens,
      inFlight: this.inFlight,
    };
  }

  /** Is the slot administratively usable right now? */
  isDisabled(nowMs = Date.now()) {
    if (!this.disabledUntil) return false;
    if (this.disabledUntil === Infinity) return true;
    if (this.disabledUntil > nowMs) return true;
    this.disabledUntil = 0;
    this.disabledReason = null;
    return false;
  }

  isCoolingDown(nowMs = Date.now()) {
    return this.cooldownUntil > nowMs;
  }

  /**
   * Can this slot absorb `estimatedTokens` more tokens right now?
   * @returns {{ok:boolean, reason?:string, retryAfterMs?:number}}
   */
  canServe(estimatedTokens = 0, nowMs = Date.now()) {
    if (this.isDisabled(nowMs)) {
      return {
        ok: false,
        reason: `disabled:${this.disabledReason ?? 'unknown'}`,
        retryAfterMs: this.disabledUntil === Infinity ? Infinity : Math.max(0, this.disabledUntil - nowMs),
      };
    }
    if (this.isCoolingDown(nowMs)) {
      return { ok: false, reason: 'cooldown', retryAfterMs: Math.max(0, this.cooldownUntil - nowMs) };
    }

    const usage = this.usage(nowMs);
    const projectedRequests = usage.rpm + this.inFlight + 1;
    if (projectedRequests > this.effective('rpm')) {
      const oldest = this.minuteLog[0]?.at ?? nowMs;
      return { ok: false, reason: 'rpm', retryAfterMs: Math.max(250, oldest + MINUTE_MS - nowMs) };
    }

    const projectedMinuteTokens = usage.tpm + estimatedTokens;
    if (projectedMinuteTokens > this.effective('tpm')) {
      const oldest = this.minuteLog[0]?.at ?? nowMs;
      return { ok: false, reason: 'tpm', retryAfterMs: Math.max(250, oldest + MINUTE_MS - nowMs) };
    }

    if (usage.rpd + this.inFlight + 1 > this.effective('rpd')) {
      return { ok: false, reason: 'rpd', retryAfterMs: Math.max(1000, nextMidnightInZone(this.policy.resetTimezone, nowMs) - nowMs) };
    }

    if (usage.tpd + estimatedTokens > this.effective('tpd')) {
      return { ok: false, reason: 'tpd', retryAfterMs: Math.max(1000, nextMidnightInZone(this.policy.resetTimezone, nowMs) - nowMs) };
    }

    const monthLimit = Number(this.policy.quotas?.tpmonth ?? 0);
    if (monthLimit > 0 && usage.tpmonth + estimatedTokens > monthLimit) {
      const oldest = this.monthLog[0]?.at ?? nowMs;
      return { ok: false, reason: 'tpmonth', retryAfterMs: Math.max(1000, oldest + MONTH_MS - nowMs) };
    }

    return { ok: true };
  }

  /** Remaining head-room ratio (0..1) - used to load-balance between slots. */
  capacityScore(nowMs = Date.now()) {
    const usage = this.usage(nowMs);
    const ratios = [
      1 - (usage.rpm + this.inFlight) / this.effective('rpm'),
      1 - usage.tpm / this.effective('tpm'),
      1 - usage.rpd / this.effective('rpd'),
      1 - usage.tpd / this.effective('tpd'),
    ].filter((value) => Number.isFinite(value));
    if (!ratios.length) return 1;
    return Math.max(0, Math.min(1, Math.min(...ratios)));
  }

  /** Reserve capacity before dispatching (prevents concurrent over-commit). */
  reserve() {
    this.inFlight += 1;
    return this;
  }

  release() {
    this.inFlight = Math.max(0, this.inFlight - 1);
    return this;
  }

  /** Record a successful call. */
  recordSuccess({ tokens = 0, latencyMs = 0, nowMs = Date.now() } = {}) {
    this.prune(nowMs);
    const sample = { at: nowMs, tokens: Math.max(0, Math.round(tokens)) };
    this.minuteLog.push(sample);
    this.monthLog.push(sample);
    this.dayCounters.requests += 1;
    this.dayCounters.tokens += sample.tokens;
    this.totals.requests += 1;
    this.totals.tokens += sample.tokens;
    this.totals.latencyMsSum += Math.max(0, latencyMs);
    this.lastUsedAt = nowMs;
    this.consecutiveErrors = 0;
    this.cooldownUntil = 0;
    return this;
  }

  /**
   * Record a failure and apply the matching penalty.
   * @param {string} errorClass canonical class from providers/base.mjs
   * @param {{retryAfterMs?:number, message?:string, nowMs?:number}} options
   */
  recordFailure(errorClass, { retryAfterMs = 0, message = '', nowMs = Date.now() } = {}) {
    this.prune(nowMs);
    // A failed attempt still consumed an upstream request slot.
    this.minuteLog.push({ at: nowMs, tokens: 0 });
    this.dayCounters.requests += 1;
    this.consecutiveErrors += 1;
    this.totals.failures += 1;
    this.lastErrorAt = nowMs;
    this.lastError = { errorClass, message: String(message).slice(0, 240), at: nowMs };

    switch (errorClass) {
      case 'rate_limit': {
        this.totals.rateLimits += 1;
        const backoff = backoffDelay(this.consecutiveErrors, 1000, 15 * MINUTE_MS);
        this.cooldownUntil = nowMs + Math.max(retryAfterMs, backoff);
        break;
      }
      case 'auth': {
        // A bad credential will never fix itself: disable until restart.
        this.disabledUntil = Infinity;
        this.disabledReason = 'auth';
        break;
      }
      case 'server':
      case 'timeout':
      case 'network': {
        this.cooldownUntil = nowMs + backoffDelay(this.consecutiveErrors, 2000, 5 * MINUTE_MS);
        break;
      }
      case 'safety_block':
      case 'context_length':
      case 'bad_request':
      case 'model_unavailable': {
        // Not the credential's fault - the router benches the *model*, not the key.
        // A retired model id in particular says nothing about the API key, which
        // must stay fully available for every other model on that provider.
        this.consecutiveErrors = Math.max(0, this.consecutiveErrors - 1);
        break;
      }
      default: {
        this.cooldownUntil = nowMs + backoffDelay(this.consecutiveErrors, 1500, 5 * MINUTE_MS);
      }
    }
    return this;
  }

  /** Apply upstream-reported limits (watchdog calibration). */
  observe(limits = {}) {
    for (const [field, value] of Object.entries(limits)) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric) || numeric <= 0) continue;
      const previous = Number(this.observedLimits[field] ?? Infinity);
      if (numeric < previous) this.observedLimits[field] = Math.floor(numeric);
    }
    return this;
  }

  /** Manually re-enable a disabled/cooling slot. */
  reset(nowMs = Date.now()) {
    this.cooldownUntil = 0;
    this.disabledUntil = 0;
    this.disabledReason = null;
    this.consecutiveErrors = 0;
    this.lastError = null;
    this.prune(nowMs);
    return this;
  }

  snapshot(nowMs = Date.now()) {
    const usage = this.usage(nowMs);
    return {
      id: this.id,
      provider: this.provider,
      model: this.model,
      credential: maskSecret(this.credential),
      credentialSource: this.credentialSource,
      resetTimezone: this.policy.resetTimezone,
      usage,
      limits: {
        rpm: this.policy.quotas.rpm,
        tpm: this.policy.quotas.tpm,
        rpd: this.policy.quotas.rpd,
        tpd: this.policy.quotas.tpd,
        tpmonth: this.policy.quotas.tpmonth,
      },
      effective: {
        rpm: Number.isFinite(this.effective('rpm')) ? this.effective('rpm') : null,
        tpm: Number.isFinite(this.effective('tpm')) ? this.effective('tpm') : null,
        rpd: Number.isFinite(this.effective('rpd')) ? this.effective('rpd') : null,
        tpd: Number.isFinite(this.effective('tpd')) ? this.effective('tpd') : null,
      },
      observedLimits: { ...this.observedLimits },
      capacity: round(this.capacityScore(nowMs), 4),
      state: this.isDisabled(nowMs) ? 'disabled' : this.isCoolingDown(nowMs) ? 'cooldown' : 'ready',
      disabledReason: this.disabledReason,
      cooldownMsRemaining: Math.max(0, this.cooldownUntil - nowMs),
      consecutiveErrors: this.consecutiveErrors,
      totals: {
        ...this.totals,
        avgLatencyMs: this.totals.requests ? Math.round(this.totals.latencyMsSum / this.totals.requests) : 0,
      },
      lastUsedAt: this.lastUsedAt ? new Date(this.lastUsedAt).toISOString() : null,
      lastError: this.lastError
        ? { ...this.lastError, at: new Date(this.lastError.at).toISOString() }
        : null,
    };
  }
}

export class KeyPool {
  constructor({ policyStore, config = CONFIG } = {}) {
    if (!policyStore) throw new Error('KeyPool requires a policyStore');
    this.policyStore = policyStore;
    this.config = config;
    this.headroom = { ...config.headroom };
    /** @type {Map<string, KeySlot[]>} model key -> slots */
    this.slotsByModel = new Map();
    /** @type {Map<string, KeySlot>} slot id -> slot */
    this.slotsById = new Map();
    /** @type {Map<string, number>} round-robin cursor per model */
    this.cursors = new Map();
    /** @type {Map<string, number>} provider -> benched-until timestamp */
    this.providerBench = new Map();
    this.build();
  }

  /** (Re)create every slot from the policy store + credentials. */
  build() {
    this.slotsByModel.clear();
    this.slotsById.clear();
    let created = 0;
    for (const policy of this.policyStore.all()) {
      if (!this.policyStore.isAvailable(policy.key)) continue;
      const credentials = policy.provider === 'mock'
        ? [{ key: 'mock-local', source: 'builtin' }]
        : this.policyStore.credentials(policy.provider);
      const slots = credentials.map((entry) => new KeySlot({
        policy,
        credential: entry.key,
        credentialSource: entry.source,
        headroom: this.headroom,
      }));
      if (!slots.length) continue;
      this.slotsByModel.set(policy.key, slots);
      for (const slot of slots) this.slotsById.set(slot.id, slot);
      created += slots.length;
    }
    log.info('key pool built', { models: this.slotsByModel.size, slots: created });
    return this;
  }

  /** All slots for a model key. */
  slotsFor(modelKey) {
    return this.slotsByModel.get(modelKey) ?? [];
  }

  get size() {
    return this.slotsById.size;
  }

  /** Bench an entire provider (used for repeated 5xx / timeouts). */
  benchProvider(provider, durationMs, reason = 'server-errors') {
    const until = Date.now() + Math.max(1000, durationMs);
    const current = this.providerBench.get(provider) ?? 0;
    if (until > current) {
      this.providerBench.set(provider, until);
      log.warn('provider benched', { provider, ms: Math.round(durationMs), reason });
    }
    return until;
  }

  isProviderBenched(provider, nowMs = Date.now()) {
    const until = this.providerBench.get(provider) ?? 0;
    if (until <= nowMs) {
      if (until) this.providerBench.delete(provider);
      return false;
    }
    return true;
  }

  /**
   * Pick the best slot for a model.
   * Strategy: filter to serveable slots, then prefer the highest remaining
   * capacity; ties are broken round-robin (least-recently-used) so multi-key
   * pools spread load evenly instead of hammering key #1.
   *
   * @returns {{slot?:KeySlot, reason?:string, retryAfterMs?:number}}
   */
  acquire(modelKey, estimatedTokens = 0, nowMs = Date.now()) {
    const slots = this.slotsFor(modelKey);
    if (!slots.length) return { reason: 'no-slots' };

    const provider = slots[0].provider;
    if (this.isProviderBenched(provider, nowMs)) {
      return { reason: 'provider-benched', retryAfterMs: (this.providerBench.get(provider) ?? nowMs) - nowMs };
    }

    const eligible = [];
    let bestRetryAfter = Infinity;
    let blockReason = 'exhausted';
    for (const slot of slots) {
      const verdict = slot.canServe(estimatedTokens, nowMs);
      if (verdict.ok) {
        eligible.push(slot);
      } else {
        if (Number.isFinite(verdict.retryAfterMs) && verdict.retryAfterMs < bestRetryAfter) {
          bestRetryAfter = verdict.retryAfterMs;
          blockReason = verdict.reason;
        } else if (blockReason === 'exhausted') {
          blockReason = verdict.reason;
        }
      }
    }

    if (!eligible.length) {
      return {
        reason: blockReason,
        retryAfterMs: Number.isFinite(bestRetryAfter) ? bestRetryAfter : 0,
      };
    }

    eligible.sort((a, b) => {
      const capacityDelta = b.capacityScore(nowMs) - a.capacityScore(nowMs);
      if (Math.abs(capacityDelta) > 0.02) return capacityDelta;
      if (a.inFlight !== b.inFlight) return a.inFlight - b.inFlight;
      return a.lastUsedAt - b.lastUsedAt;
    });

    const cursor = (this.cursors.get(modelKey) ?? 0) % eligible.length;
    // Round-robin only across the top tier (same capacity band) to keep the
    // distribution even without ever picking a saturated key.
    const topCapacity = eligible[0].capacityScore(nowMs);
    const topTier = eligible.filter((slot) => topCapacity - slot.capacityScore(nowMs) <= 0.02);
    const slot = topTier[cursor % topTier.length];
    this.cursors.set(modelKey, (cursor + 1) % Math.max(1, topTier.length));
    slot.reserve();
    return { slot };
  }

  /** Aggregate per-model view for the dashboard / health endpoint. */
  snapshot(nowMs = Date.now()) {
    const models = [];
    for (const [modelKey, slots] of this.slotsByModel) {
      const slotSnapshots = slots.map((slot) => slot.snapshot(nowMs));
      const ready = slotSnapshots.filter((slot) => slot.state === 'ready').length;
      models.push({
        model: modelKey,
        provider: slots[0].provider,
        slots: slotSnapshots.length,
        ready,
        capacity: round(Math.max(0, ...slotSnapshots.map((slot) => slot.capacity)), 4),
        requestsToday: slotSnapshots.reduce((sum, slot) => sum + slot.usage.rpd, 0),
        tokensToday: slotSnapshots.reduce((sum, slot) => sum + slot.usage.tpd, 0),
        detail: slotSnapshots,
      });
    }
    models.sort((a, b) => a.model.localeCompare(b.model));
    return {
      totalSlots: this.slotsById.size,
      readySlots: models.reduce((sum, model) => sum + model.ready, 0),
      benchedProviders: [...this.providerBench.entries()]
        .filter(([, until]) => until > nowMs)
        .map(([provider, until]) => ({ provider, msRemaining: until - nowMs })),
      headroom: this.headroom,
      models,
    };
  }
}

export default KeyPool;
