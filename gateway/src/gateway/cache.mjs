/**
 * Konkred AI Gateway - SHA-256 keyed LRU response cache.
 *
 * The cache key is a sha256 digest over the fully serialised request payload
 * `{ taskType, messages, model, temperature, maxTokens, privacy }`, so two
 * byte-identical requests share one upstream call while any change (including a
 * different privacy scope) produces a distinct key.
 *
 * Requests marked `privacy: 'private'` are never stored or served.
 */
import { CONFIG } from '../config.mjs';
import { sha256, stableStringify } from '../util.mjs';

export function buildCacheKey({ taskType, messages, model, temperature, maxTokens, privacy }) {
  return sha256(stableStringify({
    taskType: taskType ?? 'general',
    messages: messages ?? [],
    model: model ?? null,
    temperature: Number.isFinite(temperature) ? Number(temperature) : null,
    maxTokens: Number.isFinite(maxTokens) ? Number(maxTokens) : null,
    privacy: privacy ?? 'shared',
  }));
}

export class ResponseCache {
  /**
   * @param {object} options
   * @param {number} options.maxEntries hard LRU ceiling (default 500)
   * @param {number} options.ttlMs      entry lifetime (default 600_000)
   * @param {boolean} options.enabled
   */
  constructor({
    maxEntries = CONFIG.cacheMaxEntries,
    ttlMs = CONFIG.cacheTtlMs,
    enabled = CONFIG.cacheEnabled,
  } = {}) {
    this.maxEntries = Math.max(1, Number(maxEntries) || 500);
    this.ttlMs = Math.max(0, Number(ttlMs) || 0);
    this.enabled = Boolean(enabled);
    /** @type {Map<string, {value:any, expiresAt:number, storedAt:number, hits:number}>} */
    this.store = new Map(); // insertion order == LRU order (re-inserted on hit)
    this.stats = { hits: 0, misses: 0, sets: 0, evictions: 0, expirations: 0, bypasses: 0 };
  }

  /** Whether this request is cacheable at all. */
  isCacheable(request = {}) {
    if (!this.enabled || this.ttlMs <= 0) return false;
    if (request.privacy === 'private' || request.noCache === true) return false;
    // High-temperature generations are intentionally non-deterministic.
    if (Number(request.temperature) > 0.8) return false;
    return true;
  }

  key(request) {
    return buildCacheKey(request);
  }

  /** Fetch a live entry, refreshing its LRU position. */
  get(key) {
    if (!this.enabled) return null;
    const entry = this.store.get(key);
    if (!entry) {
      this.stats.misses += 1;
      return null;
    }
    if (entry.expiresAt <= Date.now()) {
      this.store.delete(key);
      this.stats.expirations += 1;
      this.stats.misses += 1;
      return null;
    }
    // Refresh recency.
    this.store.delete(key);
    entry.hits += 1;
    this.store.set(key, entry);
    this.stats.hits += 1;
    return entry.value;
  }

  /** Store a value, evicting the least-recently-used entry when full. */
  set(key, value, ttlMs = this.ttlMs) {
    if (!this.enabled || ttlMs <= 0) return false;
    if (this.store.has(key)) this.store.delete(key);
    this.store.set(key, {
      value,
      storedAt: Date.now(),
      expiresAt: Date.now() + ttlMs,
      hits: 0,
    });
    this.stats.sets += 1;
    while (this.store.size > this.maxEntries) {
      const oldestKey = this.store.keys().next().value;
      if (oldestKey === undefined) break;
      this.store.delete(oldestKey);
      this.stats.evictions += 1;
    }
    return true;
  }

  delete(key) {
    return this.store.delete(key);
  }

  clear() {
    const size = this.store.size;
    this.store.clear();
    return size;
  }

  /** Drop expired entries; called periodically by the watchdog. */
  prune() {
    const nowMs = Date.now();
    let removed = 0;
    for (const [key, entry] of this.store) {
      if (entry.expiresAt <= nowMs) {
        this.store.delete(key);
        removed += 1;
      }
    }
    this.stats.expirations += removed;
    return removed;
  }

  snapshot() {
    const total = this.stats.hits + this.stats.misses;
    return {
      enabled: this.enabled,
      size: this.store.size,
      maxEntries: this.maxEntries,
      ttlSeconds: Math.round(this.ttlMs / 1000),
      hitRate: total > 0 ? Number((this.stats.hits / total).toFixed(4)) : 0,
      ...this.stats,
    };
  }
}

export default ResponseCache;
