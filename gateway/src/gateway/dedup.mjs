/**
 * Konkred AI Gateway - in-flight request deduplication (request coalescing).
 *
 * Identical concurrent requests share a single upstream call. Every in-flight
 * entry carries a hard TTL timer (`unref()`-ed so it can never keep the event
 * loop alive) that force-evicts hung promises, guaranteeing the map cannot grow
 * without bound if an upstream never settles.
 */
import { CONFIG } from '../config.mjs';

export class InflightDeduplicator {
  /**
   * @param {object} options
   * @param {number} options.ttlMs hard eviction TTL (default 180_000)
   * @param {boolean} options.enabled
   */
  constructor({ ttlMs = CONFIG.dedupTtlMs, enabled = CONFIG.dedupEnabled } = {}) {
    this.ttlMs = Math.max(1000, Number(ttlMs) || 180000);
    this.enabled = Boolean(enabled);
    /** @type {Map<string, { startedAt:number, promise:Promise<any>, timer:NodeJS.Timeout, waiters:number }>} */
    this.inflight = new Map();
    this.stats = { started: 0, coalesced: 0, evicted: 0, completed: 0, failed: 0 };
  }

  /** Number of currently tracked in-flight calls. */
  get size() {
    return this.inflight.size;
  }

  has(key) {
    return this.inflight.has(key);
  }

  /**
   * Run `factory()` for `key`, or attach to the call already in flight.
   * @param {string} key
   * @param {() => Promise<any>} factory
   * @param {{ onCoalesced?: (entry:object)=>void }} [hooks]
   */
  async run(key, factory, hooks = {}) {
    if (!this.enabled) return factory();

    const existing = this.inflight.get(key);
    if (existing) {
      existing.waiters += 1;
      this.stats.coalesced += 1;
      hooks.onCoalesced?.(existing);
      // Attach as a follower: the leader owns cleanup.
      return existing.promise;
    }

    const entry = { startedAt: Date.now(), promise: null, timer: null, waiters: 1 };

    const timer = setTimeout(() => {
      // Hard TTL: the upstream never settled - stop tracking it so future
      // callers can retry and the Map cannot leak.
      if (this.inflight.get(key) === entry) {
        this.inflight.delete(key);
        this.stats.evicted += 1;
      }
    }, this.ttlMs);
    timer.unref?.();
    entry.timer = timer;

    const settle = (outcome) => {
      clearTimeout(entry.timer);
      if (this.inflight.get(key) === entry) this.inflight.delete(key);
      if (outcome === 'ok') this.stats.completed += 1;
      else this.stats.failed += 1;
    };

    entry.promise = (async () => {
      try {
        const result = await factory();
        settle('ok');
        return result;
      } catch (error) {
        settle('error');
        throw error;
      }
    })();

    this.inflight.set(key, entry);
    this.stats.started += 1;
    return entry.promise;
  }

  /** Force-clear everything (shutdown path). */
  clear() {
    for (const entry of this.inflight.values()) clearTimeout(entry.timer);
    const size = this.inflight.size;
    this.inflight.clear();
    return size;
  }

  /** Evict entries older than the TTL (belt-and-braces sweep for the watchdog). */
  sweep(nowMs = Date.now()) {
    let removed = 0;
    for (const [key, entry] of this.inflight) {
      if (nowMs - entry.startedAt >= this.ttlMs) {
        clearTimeout(entry.timer);
        this.inflight.delete(key);
        removed += 1;
      }
    }
    this.stats.evicted += removed;
    return removed;
  }

  snapshot() {
    const nowMs = Date.now();
    return {
      enabled: this.enabled,
      inflight: this.inflight.size,
      ttlMs: this.ttlMs,
      oldestAgeMs: this.inflight.size
        ? Math.max(...[...this.inflight.values()].map((entry) => nowMs - entry.startedAt))
        : 0,
      ...this.stats,
    };
  }
}

export default InflightDeduplicator;
