/**
 * Konkred AI Gateway - self-calibrating quota watchdog.
 *
 * Every tick it:
 *   1. prunes expired cache entries and hung in-flight dedup records
 *   2. rolls sliding windows forward and releases expired cooldowns
 *   3. re-probes credentials that were disabled by a transient auth blip
 *   4. self-calibrates: when a slot keeps getting 429s far below its published
 *      RPM/TPM, the observed ceiling is recorded so the pool stops over-driving
 *      that key (the registry is never edited on disk)
 *   5. persists a small runtime state file so daily counters survive restarts
 */
import fs from 'node:fs';
import path from 'node:path';
import { CONFIG } from './config.mjs';
import { createLogger, humanizeMs, round, safeJsonParse } from './util.mjs';

const log = createLogger('watchdog');

export class QuotaWatchdog {
  constructor({ gateway, config = CONFIG } = {}) {
    if (!gateway) throw new Error('QuotaWatchdog requires a gateway instance');
    this.gateway = gateway;
    this.config = config;
    this.intervalMs = Math.max(5000, Number(config.watchdogIntervalMs) || 60000);
    this.statePath = config.watchdogStatePath;
    this.timer = null;
    this.ticks = 0;
    this.lastTickAt = 0;
    this.lastReport = null;
    this.calibrations = [];
  }

  /** Begin the periodic loop (idempotent). */
  start() {
    if (this.timer) return this;
    this.restore();
    this.timer = setInterval(() => {
      try {
        this.tick();
      } catch (error) {
        log.error('watchdog tick failed', { error: String(error?.message ?? error) });
      }
    }, this.intervalMs);
    this.timer.unref?.();
    log.info('watchdog started', { intervalMs: this.intervalMs, calibration: this.config.watchdogCalibration });
    return this;
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.persist();
    return this;
  }

  /** One maintenance pass. Exposed for tests. */
  tick(nowMs = Date.now()) {
    this.ticks += 1;
    this.lastTickAt = nowMs;

    const prunedCache = this.gateway.cache.prune();
    const sweptDedup = this.gateway.dedup.sweep(nowMs);
    const sweptUsers = this.gateway.limiter.sweep(nowMs);

    let recovered = 0;
    let calibrated = 0;
    let cooling = 0;

    for (const slot of this.gateway.keyPool.slotsById.values()) {
      slot.prune(nowMs);

      if (slot.isCoolingDown(nowMs)) cooling += 1;

      // Auto-recover slots whose cooldown has expired.
      if (!slot.isDisabled(nowMs) && !slot.isCoolingDown(nowMs) && slot.consecutiveErrors > 0
        && nowMs - slot.lastErrorAt > 5 * 60 * 1000) {
        slot.consecutiveErrors = 0;
        recovered += 1;
      }

      if (!this.config.watchdogCalibration) continue;

      // Self-calibration: a slot that is repeatedly rate-limited while its
      // observed throughput sits well under the published ceiling is telling us
      // the registry number is optimistic. Clamp to what we actually achieved.
      if (slot.totals.rateLimits >= 3) {
        const usage = slot.usage(nowMs);
        const publishedRpm = slot.policy.quotas.rpm;
        if (publishedRpm > 1 && usage.rpm > 0 && usage.rpm < publishedRpm * 0.7) {
          const observed = Math.max(1, usage.rpm);
          if ((slot.observedLimits.rpm ?? Infinity) > observed) {
            slot.observe({ rpm: observed });
            this.gateway.policyStore.calibrate(slot.model, { rpm: Math.max(1, observed) });
            this.calibrations.push({
              at: new Date(nowMs).toISOString(), slot: slot.id, field: 'rpm', value: observed,
            });
            calibrated += 1;
          }
        }
        const publishedTpm = slot.policy.quotas.tpm;
        if (publishedTpm > 1000 && usage.tpm > 0 && usage.tpm < publishedTpm * 0.7) {
          const observedTpm = Math.max(1000, usage.tpm);
          if ((slot.observedLimits.tpm ?? Infinity) > observedTpm) {
            slot.observe({ tpm: observedTpm });
            this.calibrations.push({
              at: new Date(nowMs).toISOString(), slot: slot.id, field: 'tpm', value: observedTpm,
            });
            calibrated += 1;
          }
        }
        // Reset the counter so we re-measure instead of ratcheting forever.
        slot.totals.rateLimits = 0;
      }
    }

    if (this.calibrations.length > 100) this.calibrations = this.calibrations.slice(-100);

    this.lastReport = {
      at: new Date(nowMs).toISOString(),
      tick: this.ticks,
      prunedCache,
      sweptDedup,
      sweptUsers,
      recovered,
      calibrated,
      cooling,
      readySlots: this.gateway.keyPool.snapshot(nowMs).readySlots,
    };

    if (prunedCache || sweptDedup || recovered || calibrated) {
      log.debug('watchdog tick', this.lastReport);
    }

    if (this.ticks % 5 === 0) this.persist();
    return this.lastReport;
  }

  /** Persist daily counters so a container restart does not reset quota math. */
  persist() {
    if (!this.statePath) return false;
    try {
      const state = {
        savedAt: new Date().toISOString(),
        registryVersion: this.gateway.policyStore.version,
        slots: [...this.gateway.keyPool.slotsById.values()].map((slot) => ({
          id: slot.id,
          model: slot.model,
          dayWindowStart: slot.dayWindowStart,
          dayCounters: slot.dayCounters,
          observedLimits: slot.observedLimits,
          totals: slot.totals,
          monthLog: slot.monthLog.slice(-500),
        })),
      };
      fs.mkdirSync(path.dirname(this.statePath), { recursive: true });
      const tmp = `${this.statePath}.tmp`;
      fs.writeFileSync(tmp, JSON.stringify(state), 'utf8');
      fs.renameSync(tmp, this.statePath);
      return true;
    } catch (error) {
      log.warn('watchdog could not persist state', { error: String(error?.message ?? error) });
      return false;
    }
  }

  /** Reload persisted counters on boot. */
  restore() {
    if (!this.statePath) return false;
    try {
      if (!fs.existsSync(this.statePath)) return false;
      const state = safeJsonParse(fs.readFileSync(this.statePath, 'utf8'), null);
      if (!state?.slots) return false;
      const byId = new Map(state.slots.map((entry) => [entry.id, entry]));
      let restored = 0;
      for (const slot of this.gateway.keyPool.slotsById.values()) {
        const saved = byId.get(slot.id);
        if (!saved) continue;
        if (saved.dayWindowStart === slot.dayWindowStart && saved.dayCounters) {
          slot.dayCounters = { ...slot.dayCounters, ...saved.dayCounters };
        }
        if (saved.observedLimits) slot.observedLimits = { ...saved.observedLimits };
        if (Array.isArray(saved.monthLog)) {
          const cutoff = Date.now() - 30 * 24 * 60 * 60 * 1000;
          slot.monthLog = saved.monthLog.filter((sample) => sample?.at > cutoff);
        }
        if (saved.totals) slot.totals = { ...slot.totals, ...saved.totals };
        restored += 1;
      }
      log.info('watchdog state restored', { slots: restored, savedAt: state.savedAt });
      return true;
    } catch (error) {
      log.warn('watchdog could not restore state', { error: String(error?.message ?? error) });
      return false;
    }
  }

  snapshot() {
    return {
      running: Boolean(this.timer),
      intervalMs: this.intervalMs,
      intervalHuman: humanizeMs(this.intervalMs),
      ticks: this.ticks,
      lastTickAt: this.lastTickAt ? new Date(this.lastTickAt).toISOString() : null,
      calibrationEnabled: this.config.watchdogCalibration,
      calibrations: this.calibrations.slice(-10),
      lastReport: this.lastReport,
      uptimeRatio: this.ticks
        ? round(Math.min(1, (this.ticks * this.intervalMs) / Math.max(1, Date.now() - this.gateway.metrics.startedAt)), 3)
        : 0,
    };
  }
}

export default QuotaWatchdog;
