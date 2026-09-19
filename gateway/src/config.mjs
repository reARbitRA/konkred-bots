/**
 * Konkred AI Gateway - runtime configuration.
 *
 * Environment variables are the single source of truth. For local (non-Docker)
 * runs we also parse a `.env` file from the repository root / gateway folder,
 * but values already present in `process.env` always win.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { safeJsonParse } from './util.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const GATEWAY_ROOT = path.resolve(HERE, '..');
export const REPO_ROOT = path.resolve(GATEWAY_ROOT, '..');

/** Minimal, dependency-free `.env` parser (KEY=VALUE, #comments, quotes). */
export function parseDotEnv(contents) {
  const result = {};
  for (const rawLine of String(contents).split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const withoutExport = line.startsWith('export ') ? line.slice(7).trim() : line;
    const eq = withoutExport.indexOf('=');
    if (eq <= 0) continue;
    const key = withoutExport.slice(0, eq).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) continue;
    let value = withoutExport.slice(eq + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"') && value.length > 1)
      || (value.startsWith("'") && value.endsWith("'") && value.length > 1)) {
      value = value.slice(1, -1);
    } else {
      const hash = value.indexOf(' #');
      if (hash !== -1) value = value.slice(0, hash).trim();
    }
    result[key] = value.replace(/\\n/g, '\n');
  }
  return result;
}

/** Load `.env` files without clobbering real environment variables. */
export function loadDotEnvFiles(candidates = [
  process.env.DOTENV_PATH,
  path.join(REPO_ROOT, '.env'),
  path.join(GATEWAY_ROOT, '.env'),
].filter(Boolean)) {
  const loaded = [];
  for (const file of candidates) {
    try {
      if (!fs.existsSync(file)) continue;
      const parsed = parseDotEnv(fs.readFileSync(file, 'utf8'));
      for (const [key, value] of Object.entries(parsed)) {
        if (process.env[key] === undefined || process.env[key] === '') process.env[key] = value;
      }
      loaded.push(file);
    } catch {
      /* unreadable .env is never fatal */
    }
  }
  return loaded;
}

export const loadedEnvFiles = loadDotEnvFiles();

const str = (key, fallback = '') => {
  const value = process.env[key];
  return value === undefined || value === null || value === '' ? fallback : String(value).trim();
};
const num = (key, fallback) => {
  const value = Number(process.env[key]);
  return Number.isFinite(value) ? value : fallback;
};
const bool = (key, fallback = false) => {
  const value = str(key, '').toLowerCase();
  if (!value) return fallback;
  return ['1', 'true', 'yes', 'on', 'enabled'].includes(value);
};
const list = (key) => str(key, '')
  .split(/[\s,]+/)
  .map((item) => item.trim())
  .filter(Boolean);

/**
 * Collect every credential for a provider. Supports both a base variable and
 * numbered pool variants (KEY, KEY_1..KEY_9, KEY_P1..KEY_P9) plus comma lists.
 */
export function collectKeys(...envNames) {
  const keys = [];
  const seen = new Set();
  const push = (value, source) => {
    for (const part of String(value ?? '').split(',')) {
      const key = part.trim();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      keys.push({ key, source });
    }
  };
  for (const name of envNames) {
    push(process.env[name], name);
    for (let i = 1; i <= 9; i += 1) {
      push(process.env[`${name}_${i}`], `${name}_${i}`);
      push(process.env[`${name}_P${i}`], `${name}_P${i}`);
    }
  }
  return keys;
}

/** Parse USERS_JSON: { "<telegram-or-api-user-id>": { "tier": "pro", "rpm": 20, "rpd": 500 } } */
function parseUsers() {
  const raw = str('USERS_JSON', '');
  if (!raw) return {};
  const parsed = safeJsonParse(raw, null);
  if (!parsed || typeof parsed !== 'object') return {};
  const users = {};
  for (const [id, value] of Object.entries(parsed)) {
    if (value === null || value === undefined) continue;
    if (typeof value === 'string') {
      users[String(id)] = { tier: value };
    } else if (typeof value === 'object') {
      users[String(id)] = { tier: 'pro', ...value };
    }
  }
  return users;
}

export const CONFIG = {
  env: str('NODE_ENV', 'production'),
  port: num('PORT', 3000),
  host: str('HOST', '0.0.0.0'),
  logLevel: str('LOG_LEVEL', 'info'),
  serviceName: str('SERVICE_NAME', 'konkred-gateway'),

  // --- Security -----------------------------------------------------------
  adminKey: str('ADMIN_KEY', ''),
  gatewayApiKey: str('GATEWAY_API_KEY', ''),
  requireAuth: bool('GATEWAY_REQUIRE_AUTH', true),
  users: parseUsers(),
  trustedProxies: list('TRUSTED_PROXIES'),

  // --- Request handling ---------------------------------------------------
  maxBodyBytes: num('MAX_BODY_BYTES', 24 * 1024 * 1024), // 24 MB (base64 audio/pdf)
  requestTimeoutMs: num('REQUEST_TIMEOUT_MS', 120000),
  upstreamTimeoutMs: num('UPSTREAM_TIMEOUT_MS', 90000),
  maxAttempts: num('MAX_FALLBACK_ATTEMPTS', 6),

  // --- Cache --------------------------------------------------------------
  cacheEnabled: bool('CACHE_ENABLED', true),
  cacheTtlMs: num('CACHE_TTL_SECONDS', 600) * 1000,
  cacheMaxEntries: num('CACHE_MAX_ENTRIES', 500),

  // --- Deduplication ------------------------------------------------------
  dedupEnabled: bool('DEDUP_ENABLED', true),
  dedupTtlMs: num('DEDUP_TTL_MS', 180000),

  // --- Rate limiting ------------------------------------------------------
  userRpm: num('USER_RPM', 12),
  userRpd: num('USER_RPD', 400),
  userTpd: num('USER_TPD', 900000),
  proUserRpm: num('PRO_USER_RPM', 40),
  proUserRpd: num('PRO_USER_RPD', 3000),
  proUserTpd: num('PRO_USER_TPD', 6000000),

  // --- Quota safety headroom (fractions of the published limit) -----------
  headroom: {
    rpm: Number(num('HEADROOM_RPM', 0.85)),
    tpm: Number(num('HEADROOM_TPM', 0.9)),
    rpd: Number(num('HEADROOM_RPD', 0.95)),
    tpd: Number(num('HEADROOM_TPD', 0.98)),
  },

  // --- Watchdog -----------------------------------------------------------
  watchdogIntervalMs: num('WATCHDOG_INTERVAL_MS', 60000),
  watchdogCalibration: bool('WATCHDOG_CALIBRATION', true),
  watchdogStatePath: str('WATCHDOG_STATE_PATH', path.join(GATEWAY_ROOT, 'data', 'runtime.state.json')),
  registryPath: str('POLICY_REGISTRY_PATH', path.join(GATEWAY_ROOT, 'data', 'policies.registry.json')),

  // --- Behaviour flags ----------------------------------------------------
  allowMock: bool('ALLOW_MOCK', true),
  mockOnly: bool('MOCK_ONLY', false),
  fusionEnabled: bool('FUSION_ENABLED', true),
  dashboardEnabled: bool('DASHBOARD_ENABLED', true),

  // --- Provider credentials ----------------------------------------------
  providers: {
    gemini: {
      enabled: bool('GEMINI_ENABLED', true),
      baseUrl: str('GEMINI_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta'),
      keys: collectKeys('GEMINI_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY'),
    },
    groq: {
      enabled: bool('GROQ_ENABLED', true),
      baseUrl: str('GROQ_BASE_URL', 'https://api.groq.com/openai/v1'),
      keys: collectKeys('GROQ_API_KEY', 'GROQ_KEY'),
    },
    cerebras: {
      enabled: bool('CEREBRAS_ENABLED', true),
      baseUrl: str('CEREBRAS_BASE_URL', 'https://api.cerebras.ai/v1'),
      keys: collectKeys('CEREBRAS_API_KEY', 'CEREBRAS_KEY'),
    },
    mistral: {
      enabled: bool('MISTRAL_ENABLED', true),
      baseUrl: str('MISTRAL_BASE_URL', 'https://api.mistral.ai/v1'),
      keys: collectKeys('MISTRAL_API_KEY', 'MISTRAL_KEY'),
    },
    openrouter: {
      enabled: bool('OPENROUTER_ENABLED', true),
      baseUrl: str('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1'),
      keys: collectKeys('OPENROUTER_API_KEY', 'OPENROUTER_KEY'),
      referer: str('OPENROUTER_REFERER', 'https://github.com/reARbitRA/konkred-bots'),
      title: str('OPENROUTER_TITLE', 'Konkred Gateway'),
    },
    cloudflare: {
      enabled: bool('CLOUDFLARE_ENABLED', true),
      baseUrl: str('CF_BASE_URL', 'https://api.cloudflare.com/client/v4'),
      accountId: str('CF_ACCOUNT_ID', ''),
      keys: collectKeys('CF_API_TOKEN', 'CLOUDFLARE_API_TOKEN'),
    },
    github: {
      enabled: bool('GITHUB_MODELS_ENABLED', true),
      baseUrl: str('GITHUB_MODELS_BASE_URL', 'https://models.github.ai/inference'),
      keys: collectKeys('GITHUB_TOKEN', 'GH_MODELS_TOKEN'),
    },
    mock: {
      enabled: true,
      baseUrl: 'local://mock',
      keys: [{ key: 'mock-local', source: 'builtin' }],
    },
  },
};

/** True when at least one real (non-mock) credential is configured. */
export function hasRealCredentials(config = CONFIG) {
  return Object.entries(config.providers)
    .filter(([name]) => name !== 'mock')
    .some(([, provider]) => provider.enabled && provider.keys.length > 0);
}

/** Summary used by /api/meta and the dashboard (never exposes raw secrets). */
export function providerSummary(config = CONFIG) {
  return Object.entries(config.providers).map(([name, provider]) => ({
    provider: name,
    enabled: Boolean(provider.enabled),
    credentials: provider.keys.length,
    sources: provider.keys.map((entry) => entry.source),
    baseUrl: provider.baseUrl,
    ready: Boolean(provider.enabled) && (name === 'mock'
      ? true
      : provider.keys.length > 0 && (name !== 'cloudflare' || Boolean(provider.accountId))),
  }));
}

export default CONFIG;
