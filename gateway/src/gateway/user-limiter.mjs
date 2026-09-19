/**
 * Konkred AI Gateway - per-user fair-use limiter.
 *
 * Protects the shared free-tier quota pool from a single noisy Telegram user.
 * Tracks a sliding 60s request window plus UTC-day request/token counters, with
 * per-user overrides supplied through USERS_JSON (tier / rpm / rpd / tpd).
 */
import { CONFIG } from '../config.mjs';
import { startOfDayInZone } from '../util.mjs';

const MINUTE_MS = 60 * 1000;

export class UserLimiter {
  constructor({ config = CONFIG } = {}) {
    this.config = config;
    /** @type {Map<string, {minute:number[], dayStart:number, requests:number, tokens:number, lastSeen:number}>} */
    this.users = new Map();
    this.stats = { allowed: 0, throttled: 0 };
  }

  /** Resolve the effective limits for a user id. */
  limitsFor(userId) {
    const override = this.config.users?.[String(userId)] ?? null;
    const isPro = override?.tier === 'pro' || override?.tier === 'admin' || override?.tier === 'unlimited';
    const base = {
      rpm: isPro ? this.config.proUserRpm : this.config.userRpm,
      rpd: isPro ? this.config.proUserRpd : this.config.userRpd,
      tpd: isPro ? this.config.proUserTpd : this.config.userTpd,
      tier: override?.tier ?? 'free',
    };
    if (override?.tier === 'unlimited') {
      return { rpm: Infinity, rpd: Infinity, tpd: Infinity, tier: 'unlimited' };
    }
    return {
      rpm: Number(override?.rpm ?? base.rpm),
      rpd: Number(override?.rpd ?? base.rpd),
      tpd: Number(override?.tpd ?? base.tpd),
      tier: base.tier,
    };
  }

  /** @private */
  bucket(userId, nowMs) {
    const id = String(userId);
    let entry = this.users.get(id);
    if (!entry) {
      entry = { minute: [], dayStart: startOfDayInZone('UTC', nowMs), requests: 0, tokens: 0, lastSeen: nowMs };
      this.users.set(id, entry);
    }
    const cutoff = nowMs - MINUTE_MS;
    while (entry.minute.length && entry.minute[0] <= cutoff) entry.minute.shift();
    const currentDay = startOfDayInZone('UTC', nowMs);
    if (currentDay > entry.dayStart) {
      entry.dayStart = currentDay;
      entry.requests = 0;
      entry.tokens = 0;
    }
    entry.lastSeen = nowMs;
    return entry;
  }

  /**
   * Check-and-consume one request slot.
   * @returns {{allowed:boolean, reason?:string, retryAfterMs?:number, limits:object, usage:object}}
   */
  consume(userId, { estimatedTokens = 0, nowMs = Date.now() } = {}) {
    const limits = this.limitsFor(userId);
    const entry = this.bucket(userId, nowMs);
    const usage = { rpm: entry.minute.length, rpd: entry.requests, tpd: entry.tokens };

    if (usage.rpm + 1 > limits.rpm) {
      this.stats.throttled += 1;
      return {
        allowed: false,
        reason: 'user_rpm',
        retryAfterMs: Math.max(500, (entry.minute[0] ?? nowMs) + MINUTE_MS - nowMs),
        limits,
        usage,
      };
    }
    if (usage.rpd + 1 > limits.rpd) {
      this.stats.throttled += 1;
      return {
        allowed: false,
        reason: 'user_rpd',
        retryAfterMs: Math.max(1000, startOfDayInZone('UTC', nowMs) + 24 * 60 * 60 * 1000 - nowMs),
        limits,
        usage,
      };
    }
    if (usage.tpd + estimatedTokens > limits.tpd) {
      this.stats.throttled += 1;
      return {
        allowed: false,
        reason: 'user_tpd',
        retryAfterMs: Math.max(1000, startOfDayInZone('UTC', nowMs) + 24 * 60 * 60 * 1000 - nowMs),
        limits,
        usage,
      };
    }

    entry.minute.push(nowMs);
    entry.requests += 1;
    this.stats.allowed += 1;
    return { allowed: true, limits, usage: { rpm: entry.minute.length, rpd: entry.requests, tpd: entry.tokens } };
  }

  /** Record actual token spend once the upstream reports usage. */
  recordTokens(userId, tokens = 0, nowMs = Date.now()) {
    const entry = this.bucket(userId, nowMs);
    entry.tokens += Math.max(0, Math.round(tokens));
    return entry.tokens;
  }

  /** Drop users idle for more than a day (memory hygiene, called by watchdog). */
  sweep(nowMs = Date.now(), idleMs = 24 * 60 * 60 * 1000) {
    let removed = 0;
    for (const [id, entry] of this.users) {
      if (nowMs - entry.lastSeen > idleMs) {
        this.users.delete(id);
        removed += 1;
      }
    }
    return removed;
  }

  snapshot(limit = 15) {
    const users = [...this.users.entries()]
      .sort((a, b) => b[1].requests - a[1].requests)
      .slice(0, limit)
      .map(([id, entry]) => ({
        user: id,
        tier: this.limitsFor(id).tier,
        rpm: entry.minute.length,
        requestsToday: entry.requests,
        tokensToday: entry.tokens,
        lastSeen: new Date(entry.lastSeen).toISOString(),
      }));
    return { tracked: this.users.size, ...this.stats, top: users };
  }
}

export default UserLimiter;
