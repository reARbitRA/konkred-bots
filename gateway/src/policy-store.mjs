/**
 * Konkred AI Gateway - policy store.
 *
 * Loads `data/policies.registry.json`, normalises every model entry against the
 * registry defaults, merges in credential availability from the environment and
 * exposes lookup helpers used by the router, key-pool and watchdog.
 */
import fs from 'node:fs';
import { CONFIG } from './config.mjs';
import { createLogger } from './util.mjs';

const log = createLogger('policy-store');

const REQUIRED_QUOTA_FIELDS = ['rpm', 'tpm', 'rpd', 'tpd'];

/** Normalise one registry row into a full policy object. */
function normalizeModel(raw, registry) {
  const defaults = registry.defaults ?? {};
  const providerMeta = registry.providers?.[raw.provider] ?? {};
  const quotas = { rpm: 0, tpm: 0, rpd: 0, tpd: 0, tpmonth: 0, ...(raw.quotas ?? {}) };
  for (const field of REQUIRED_QUOTA_FIELDS) {
    if (!Number.isFinite(Number(quotas[field]))) quotas[field] = 0;
    quotas[field] = Math.max(0, Number(quotas[field]));
  }
  quotas.tpmonth = Math.max(0, Number(quotas.tpmonth) || 0);

  return {
    key: raw.key,
    provider: raw.provider,
    upstreamModel: raw.upstreamModel,
    tier: raw.tier ?? 'balanced',
    capabilities: Array.isArray(raw.capabilities) && raw.capabilities.length
      ? [...new Set(raw.capabilities)]
      : [...(defaults.capabilities ?? ['text'])],
    contextWindow: Number(raw.contextWindow) || 8192,
    maxOutputTokens: Number(raw.maxOutputTokens) || Number(defaults.maxOutputTokens) || 4096,
    latencyClass: raw.latencyClass ?? defaults.latencyClass ?? 'standard',
    qualityScore: Number.isFinite(Number(raw.qualityScore))
      ? Number(raw.qualityScore)
      : Number(defaults.qualityScore ?? 0.5),
    resetTimezone: raw.resetTimezone ?? providerMeta.resetTimezone ?? defaults.resetTimezone ?? 'UTC',
    quotas,
    providerLabel: providerMeta.label ?? raw.provider,
    docs: providerMeta.docs ?? null,
  };
}

export class PolicyStore {
  constructor({ registryPath = CONFIG.registryPath, config = CONFIG } = {}) {
    this.registryPath = registryPath;
    this.config = config;
    this.registry = null;
    this.models = new Map();
    this.load();
  }

  /** (Re)load the registry from disk. Safe to call at runtime. */
  load() {
    let registry;
    try {
      registry = JSON.parse(fs.readFileSync(this.registryPath, 'utf8'));
    } catch (error) {
      throw new Error(`Unable to read policy registry at ${this.registryPath}: ${error.message}`);
    }
    if (!Array.isArray(registry.models) || registry.models.length === 0) {
      throw new Error('Policy registry contains no models');
    }
    const models = new Map();
    for (const raw of registry.models) {
      if (!raw?.key || !raw?.provider || !raw?.upstreamModel) {
        log.warn('skipping malformed registry entry', { entry: raw?.key ?? '(unnamed)' });
        continue;
      }
      models.set(raw.key, normalizeModel(raw, registry));
    }
    this.registry = registry;
    this.models = models;
    log.info('policy registry loaded', {
      version: registry.version,
      models: models.size,
      providers: Object.keys(registry.providers ?? {}).length,
    });
    return this;
  }

  get version() {
    return this.registry?.version ?? 'unknown';
  }

  /** All policies (array copy). */
  all() {
    return [...this.models.values()];
  }

  /** Every model key known to the registry. */
  keys() {
    return [...this.models.keys()];
  }

  /** Lookup by `provider:model` key. */
  get(key) {
    return this.models.get(key) ?? null;
  }

  has(key) {
    return this.models.has(key);
  }

  /** Provider-level runtime configuration (credentials, base url, flags). */
  providerConfig(provider) {
    return this.config.providers[provider] ?? null;
  }

  /** Credentials available for a provider, as `{ key, source }` entries. */
  credentials(provider) {
    const providerConfig = this.providerConfig(provider);
    if (!providerConfig || providerConfig.enabled === false) return [];
    if (provider === 'cloudflare' && !providerConfig.accountId) return [];
    return providerConfig.keys ?? [];
  }

  /** A model is usable when its provider is enabled and holds >=1 credential. */
  isAvailable(key) {
    const policy = this.get(key);
    if (!policy) return false;
    if (this.config.mockOnly) return policy.provider === 'mock';
    if (policy.provider === 'mock') return this.config.allowMock;
    return this.credentials(policy.provider).length > 0;
  }

  /** Every usable model key, ordered by quality descending. */
  availableKeys() {
    return this.all()
      .filter((policy) => this.isAvailable(policy.key))
      .sort((a, b) => b.qualityScore - a.qualityScore)
      .map((policy) => policy.key);
  }

  /** Models filtered by a capability tag (e.g. 'audio', 'vision'). */
  withCapability(capability) {
    return this.all().filter((policy) => policy.capabilities.includes(capability));
  }

  /**
   * Overwrite quota values discovered at runtime (watchdog calibration).
   * Only shrinks limits - we never trust an upstream that claims *more* quota.
   */
  calibrate(key, observed = {}) {
    const policy = this.get(key);
    if (!policy) return false;
    let changed = false;
    for (const field of ['rpm', 'tpm', 'rpd', 'tpd']) {
      const value = Number(observed[field]);
      if (!Number.isFinite(value) || value <= 0) continue;
      if (policy.quotas[field] === 0 || value < policy.quotas[field]) {
        policy.quotas[field] = Math.floor(value);
        changed = true;
      }
    }
    if (changed) log.info('quota calibrated', { model: key, quotas: policy.quotas });
    return changed;
  }

  /** Serialisable snapshot for /api/models. */
  describe() {
    return this.all().map((policy) => ({
      key: policy.key,
      provider: policy.provider,
      providerLabel: policy.providerLabel,
      upstreamModel: policy.upstreamModel,
      tier: policy.tier,
      capabilities: policy.capabilities,
      contextWindow: policy.contextWindow,
      maxOutputTokens: policy.maxOutputTokens,
      latencyClass: policy.latencyClass,
      qualityScore: policy.qualityScore,
      resetTimezone: policy.resetTimezone,
      quotas: { ...policy.quotas },
      available: this.isAvailable(policy.key),
      credentials: this.credentials(policy.provider).length,
    }));
  }
}

export const policyStore = new PolicyStore();
export default policyStore;
