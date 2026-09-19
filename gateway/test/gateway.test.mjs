/**
 * Konkred AI Gateway - test suite (node --test, zero dependencies).
 *
 * Covers: utilities, cache, dedup TTL eviction, key-pool quota math + backoff,
 * router context validation, fallback error-class recovery, fusion merging and
 * the real provider adapters (driven against a local HTTP stub so the Gemini /
 * OpenAI-compatible / Cloudflare wire formats are genuinely exercised).
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';

import {
  backoffDelay, estimateMessageTokens, extractJson, maskSecret, nextMidnightInZone, sha256,
  stableStringify, startOfDayInZone, timingSafeEqualStr,
} from '../src/util.mjs';
import { parseDotEnv, collectKeys } from '../src/config.mjs';
import { PolicyStore } from '../src/policy-store.mjs';
import { ResponseCache, buildCacheKey } from '../src/gateway/cache.mjs';
import { InflightDeduplicator } from '../src/gateway/dedup.mjs';
import { KeyPool, KeySlot } from '../src/gateway/key-pool.mjs';
import { SmartRouter, normalizeTaskType, requiredCapabilities, TASK_TYPES } from '../src/gateway/router.mjs';
import { FallbackEngine, ExhaustedError, RECOVERY_ACTIONS } from '../src/gateway/fallback.mjs';
import { fuseJson, fuseText, similarity } from '../src/gateway/fusion.mjs';
import { UserLimiter } from '../src/gateway/user-limiter.mjs';
import { Gateway, validateRequest, ValidationError } from '../src/gateway/gateway.mjs';
import { GeminiProvider, OpenAICompatProvider, CloudflareProvider, MockProvider, ProviderError, ERROR_CLASS, classifyHttpError, parseRetryAfter } from '../src/providers/index.mjs';
import { toGeminiPayload } from '../src/providers/gemini.mjs';
import { QuotaWatchdog } from '../src/watchdog.mjs';
import { renderDashboard } from '../src/dashboard.mjs';

/* --------------------------------------------------------------- helpers */

const baseConfig = {
  env: 'test',
  serviceName: 'konkred-gateway-test',
  registryPath: new URL('../data/policies.registry.json', import.meta.url).pathname,
  mockOnly: false,
  allowMock: true,
  cacheEnabled: true,
  cacheTtlMs: 600000,
  cacheMaxEntries: 500,
  dedupEnabled: true,
  dedupTtlMs: 180000,
  fusionEnabled: true,
  maxAttempts: 6,
  upstreamTimeoutMs: 5000,
  requestTimeoutMs: 10000,
  userRpm: 12,
  userRpd: 400,
  userTpd: 900000,
  proUserRpm: 40,
  proUserRpd: 3000,
  proUserTpd: 6000000,
  users: {},
  headroom: { rpm: 0.85, tpm: 0.9, rpd: 0.95, tpd: 0.98 },
  watchdogIntervalMs: 60000,
  watchdogCalibration: true,
  watchdogStatePath: '',
  providers: {},
};

function configWith(providerKeys = {}) {
  const providers = {
    gemini: { enabled: true, baseUrl: 'https://example.invalid/v1beta', keys: [] },
    groq: { enabled: true, baseUrl: 'https://example.invalid/openai/v1', keys: [] },
    cerebras: { enabled: true, baseUrl: 'https://example.invalid/v1', keys: [] },
    mistral: { enabled: true, baseUrl: 'https://example.invalid/v1', keys: [] },
    openrouter: { enabled: true, baseUrl: 'https://example.invalid/api/v1', keys: [] },
    cloudflare: { enabled: true, baseUrl: 'https://example.invalid/client/v4', accountId: 'acct', keys: [] },
    github: { enabled: true, baseUrl: 'https://example.invalid/inference', keys: [] },
    mock: { enabled: true, baseUrl: 'local://mock', keys: [{ key: 'mock-local', source: 'builtin' }] },
  };
  for (const [name, keys] of Object.entries(providerKeys)) {
    providers[name].keys = keys.map((key, index) => ({ key, source: `TEST_${name.toUpperCase()}_${index}` }));
  }
  return { ...baseConfig, providers };
}

function makeStore(providerKeys) {
  const config = configWith(providerKeys);
  return { config, store: new PolicyStore({ registryPath: baseConfig.registryPath, config }) };
}

/** Provider stub that returns scripted outcomes. */
class ScriptedProvider {
  constructor(name, script) {
    this.name = name;
    this.script = script;
    this.calls = [];
  }

  async complete(request) {
    this.calls.push(request.policy.key);
    const step = typeof this.script === 'function' ? this.script(request) : this.script;
    if (step instanceof Error) throw step;
    return {
      text: step?.text ?? `ok from ${request.policy.key}`,
      finishReason: 'stop',
      usage: { promptTokens: 10, completionTokens: 10, totalTokens: 20 },
      raw: {},
    };
  }
}

/* ----------------------------------------------------------------- utils */

test('stableStringify is key-order independent and sha256 is stable', () => {
  assert.equal(stableStringify({ b: 1, a: 2 }), stableStringify({ a: 2, b: 1 }));
  assert.equal(sha256({ a: 1, b: [2, 3] }), sha256({ b: [2, 3], a: 1 }));
  assert.equal(sha256('x').length, 64);
  assert.notEqual(sha256({ a: 1 }), sha256({ a: 2 }));
});

test('timingSafeEqualStr matches only identical non-empty strings', () => {
  assert.equal(timingSafeEqualStr('secret', 'secret'), true);
  assert.equal(timingSafeEqualStr('secret', 'secrex'), false);
  assert.equal(timingSafeEqualStr('secret', 'secret-longer'), false);
  assert.equal(timingSafeEqualStr('', ''), false);
  assert.equal(timingSafeEqualStr(undefined, ''), false);
});

test('backoffDelay grows exponentially and stays within the jitter band', () => {
  for (let errors = 1; errors <= 6; errors += 1) {
    const expected = 1000 * 2 ** (errors - 1);
    for (let i = 0; i < 40; i += 1) {
      const delay = backoffDelay(errors, 1000, 10 * 60 * 1000);
      assert.ok(delay >= expected * 0.9 - 1, `${delay} >= ${expected * 0.9}`);
      assert.ok(delay <= expected * 1.1 + 1, `${delay} <= ${expected * 1.1}`);
    }
  }
  assert.ok(backoffDelay(50, 1000, 5000) <= 5000 * 1.1 + 1, 'respects the cap');
});

test('timezone helpers produce a stable day boundary', () => {
  const reference = Date.parse('2026-03-15T06:30:00Z');
  const utcStart = startOfDayInZone('UTC', reference);
  assert.equal(new Date(utcStart).toISOString(), '2026-03-15T00:00:00.000Z');
  const pacificStart = startOfDayInZone('America/Los_Angeles', reference);
  assert.ok(pacificStart < utcStart, 'pacific midnight precedes the same UTC instant');
  assert.ok(nextMidnightInZone('UTC', reference) > reference);
  assert.ok(nextMidnightInZone('America/Los_Angeles', reference) > reference);
});

test('extractJson recovers fenced and embedded objects', () => {
  assert.deepEqual(extractJson('```json\n{"a":1}\n```'), { a: 1 });
  assert.deepEqual(extractJson('Here you go: {"a":[1,2]} cheers'), { a: [1, 2] });
  assert.deepEqual(extractJson('{"s":"}not the end"}'), { s: '}not the end' });
  assert.equal(extractJson('no json here', null), null);
});

test('estimateMessageTokens accounts for text and inline media', () => {
  const textOnly = estimateMessageTokens([{ role: 'user', content: 'hello world' }]);
  const withAudio = estimateMessageTokens([{
    role: 'user',
    content: [{ type: 'inline_data', inlineData: { mimeType: 'audio/ogg', data: 'A'.repeat(75000) } }],
  }]);
  assert.ok(textOnly > 0 && textOnly < 30);
  assert.ok(withAudio > 90, `expected media weighting, got ${withAudio}`);
});

test('maskSecret never leaks the full credential', () => {
  assert.equal(maskSecret('sk-abcdefghijklmn'), 'sk-a***mn');
  assert.equal(maskSecret('short'), 'sh***');
  assert.equal(maskSecret(''), '(empty)');
});

test('parseDotEnv and collectKeys handle pools, quotes and comments', () => {
  const parsed = parseDotEnv([
    '# comment',
    'export A=1',
    'B="quoted value"',
    "C='single'",
    'D=plain # trailing',
    'INVALID LINE',
  ].join('\n'));
  assert.deepEqual(parsed, { A: '1', B: 'quoted value', C: 'single', D: 'plain' });

  process.env.TEST_POOL = 'k0';
  process.env.TEST_POOL_P1 = 'k1,k2';
  process.env.TEST_POOL_2 = 'k1'; // duplicate must be de-duplicated
  const keys = collectKeys('TEST_POOL');
  assert.deepEqual(keys.map((entry) => entry.key), ['k0', 'k1', 'k2']);
  delete process.env.TEST_POOL;
  delete process.env.TEST_POOL_P1;
  delete process.env.TEST_POOL_2;
});

/* ----------------------------------------------------------- policy store */

test('policy store loads every specified model key', () => {
  const { store } = makeStore({});
  const expected = [
    'gemini:flash', 'gemini:flash-lite', 'groq:gpt-oss-120b', 'groq:llama-70b', 'groq:llama-8b',
    'groq:qwen3-32b', 'groq:kimi-k2', 'groq:llama-4-scout', 'cerebras:gpt-oss-120b', 'cerebras:llama-8b',
    'cerebras:qwen3-235b', 'mistral:small', 'mistral:codestral', 'openrouter:free-auto',
    'cloudflare:llama-8b', 'github:gpt-4o', 'github:gpt-4o-mini', 'mock:general', 'mock:fast',
  ];
  for (const key of expected) assert.ok(store.has(key), `missing ${key}`);
  assert.equal(store.get('gemini:flash').resetTimezone, 'America/Los_Angeles');
  assert.equal(store.get('groq:llama-70b').resetTimezone, 'UTC');
  assert.equal(store.get('gemini:flash').contextWindow, 1048576);
});

test('availability follows credential presence', () => {
  const { store } = makeStore({});
  assert.equal(store.isAvailable('groq:llama-70b'), false);
  assert.equal(store.isAvailable('mock:general'), true);
  const { store: withGroq } = makeStore({ groq: ['gsk_test'] });
  assert.equal(withGroq.isAvailable('groq:llama-70b'), true);
});

test('calibrate only shrinks quotas', () => {
  const { store } = makeStore({});
  const before = store.get('groq:llama-8b').quotas.rpm;
  store.calibrate('groq:llama-8b', { rpm: before + 100 });
  assert.equal(store.get('groq:llama-8b').quotas.rpm, before, 'never grows');
  store.calibrate('groq:llama-8b', { rpm: 5 });
  assert.equal(store.get('groq:llama-8b').quotas.rpm, 5);
});

/* ------------------------------------------------------------------ cache */

test('cache key is deterministic and privacy-scoped', () => {
  const request = {
    taskType: 'general', messages: [{ role: 'user', content: 'hi' }], model: null, temperature: 0.3, maxTokens: 100,
  };
  assert.equal(buildCacheKey(request), buildCacheKey({ ...request }));
  assert.notEqual(buildCacheKey(request), buildCacheKey({ ...request, privacy: 'private' }));
  assert.notEqual(buildCacheKey(request), buildCacheKey({ ...request, temperature: 0.7 }));
  assert.equal(buildCacheKey(request).length, 64);
});

test('cache honours TTL and LRU ceiling', () => {
  const cache = new ResponseCache({ maxEntries: 3, ttlMs: 50, enabled: true });
  cache.set('a', 1); cache.set('b', 2); cache.set('c', 3);
  assert.equal(cache.get('a'), 1);          // 'a' becomes most-recent
  cache.set('d', 4);                        // evicts 'b' (least recent)
  assert.equal(cache.store.size, 3);
  assert.equal(cache.get('b'), null);
  assert.equal(cache.get('a'), 1);
  cache.store.get('a').expiresAt = Date.now() - 1;
  assert.equal(cache.get('a'), null, 'expired entries are dropped');
});

test('cache refuses private and high-temperature requests', () => {
  const cache = new ResponseCache({ enabled: true, ttlMs: 1000 });
  assert.equal(cache.isCacheable({ privacy: 'shared', temperature: 0.2 }), true);
  assert.equal(cache.isCacheable({ privacy: 'private' }), false);
  assert.equal(cache.isCacheable({ noCache: true }), false);
  assert.equal(cache.isCacheable({ temperature: 1.5 }), false);
});

/* ------------------------------------------------------------------ dedup */

test('dedup coalesces concurrent identical calls into one execution', async () => {
  const dedup = new InflightDeduplicator({ ttlMs: 5000, enabled: true });
  let executions = 0;
  const factory = async () => {
    executions += 1;
    await new Promise((resolve) => { setTimeout(resolve, 25); });
    return 'value';
  };
  const results = await Promise.all(Array.from({ length: 8 }, () => dedup.run('k', factory)));
  assert.deepEqual(results, Array(8).fill('value'));
  assert.equal(executions, 1);
  assert.equal(dedup.stats.coalesced, 7);
  assert.equal(dedup.size, 0, 'map is emptied after settlement');
});

test('dedup propagates rejection to every waiter and cleans up', async () => {
  const dedup = new InflightDeduplicator({ ttlMs: 5000, enabled: true });
  const factory = async () => { throw new Error('boom'); };
  const settled = await Promise.allSettled([dedup.run('k', factory), dedup.run('k', factory)]);
  assert.equal(settled.filter((entry) => entry.status === 'rejected').length, 2);
  assert.equal(dedup.size, 0);
  assert.equal(dedup.stats.failed, 1);
});

test('dedup TTL timer force-evicts a hung call and is unref-ed', async () => {
  // The constructor clamps the TTL to a 1s floor, so use the floor here.
  const dedup = new InflightDeduplicator({ ttlMs: 10, enabled: true });
  assert.equal(dedup.ttlMs, 1000, 'TTL is clamped to a 1s minimum');

  let release;
  const hung = new Promise((resolve) => { release = resolve; });
  const promise = dedup.run('hung', () => hung);
  assert.equal(dedup.size, 1);

  const entry = dedup.inflight.get('hung');
  assert.equal(typeof entry.timer.unref, 'function');
  assert.equal(entry.timer.hasRef(), false, 'timer must not keep the event loop alive');

  await new Promise((resolve) => { setTimeout(resolve, 1200); });
  assert.equal(dedup.size, 0, 'hung entry evicted by TTL');
  assert.equal(dedup.stats.evicted, 1);

  // Eviction must not break the original promise for callers already attached.
  release('done');
  assert.equal(await promise, 'done');

  // A fresh call for the same key now executes instead of attaching to the corpse.
  assert.equal(await dedup.run('hung', async () => 'fresh'), 'fresh');
});

/* --------------------------------------------------------------- key pool */

test('key slot enforces RPM headroom (85% of the published limit)', () => {
  const { store } = makeStore({ groq: ['k1'] });
  const policy = store.get('groq:llama-70b'); // rpm 30 -> effective 25
  const slot = new KeySlot({ policy, credential: 'k1', credentialSource: 'T', headroom: baseConfig.headroom });
  assert.equal(slot.effective('rpm'), 25);
  for (let i = 0; i < 25; i += 1) {
    assert.equal(slot.canServe(1).ok, true, `call ${i} should fit`);
    slot.recordSuccess({ tokens: 1 });
  }
  const verdict = slot.canServe(1);
  assert.equal(verdict.ok, false);
  assert.equal(verdict.reason, 'rpm');
  assert.ok(verdict.retryAfterMs > 0 && verdict.retryAfterMs <= 60000);
});

test('sliding 60s window releases capacity as samples age out', () => {
  const { store } = makeStore({ groq: ['k1'] });
  const slot = new KeySlot({
    policy: store.get('groq:llama-70b'), credential: 'k1', credentialSource: 'T', headroom: baseConfig.headroom,
  });
  const t0 = Date.now();
  for (let i = 0; i < 25; i += 1) slot.recordSuccess({ tokens: 1, nowMs: t0 });
  assert.equal(slot.canServe(1, t0 + 1000).ok, false);
  assert.equal(slot.canServe(1, t0 + 61000).ok, true, 'window rolled forward');
  assert.equal(slot.usage(t0 + 61000).rpm, 0);
});

test('TPM headroom blocks oversized bursts', () => {
  const { store } = makeStore({ groq: ['k1'] });
  const policy = store.get('groq:llama-70b'); // tpm 12000 -> effective 10800
  const slot = new KeySlot({ policy, credential: 'k1', credentialSource: 'T', headroom: baseConfig.headroom });
  assert.equal(slot.effective('tpm'), 10800);
  slot.recordSuccess({ tokens: 10000 });
  const verdict = slot.canServe(1000);
  assert.equal(verdict.ok, false);
  assert.equal(verdict.reason, 'tpm');
});

test('daily counters reset at midnight in the model timezone', () => {
  const { store } = makeStore({ gemini: ['k1'] });
  const policy = store.get('gemini:flash'); // rpd 250 -> effective 237
  const slot = new KeySlot({ policy, credential: 'k1', credentialSource: 'T', headroom: baseConfig.headroom });
  assert.equal(slot.policy.resetTimezone, 'America/Los_Angeles');
  const t0 = Date.now();
  for (let i = 0; i < 237; i += 1) slot.recordSuccess({ tokens: 1, nowMs: t0 });
  assert.equal(slot.dayCounters.requests, 237);
  const blocked = slot.canServe(1, t0 + 61000);
  assert.equal(blocked.ok, false);
  assert.equal(blocked.reason, 'rpd');
  const tomorrow = nextMidnightInZone('America/Los_Angeles', t0) + 60000;
  assert.equal(slot.canServe(1, tomorrow).ok, true, 'counters reset after the pacific rollover');
  assert.equal(slot.dayCounters.requests, 0);
});

test('429 applies exponential cooldown, auth disables the slot permanently', () => {
  const { store } = makeStore({ groq: ['k1'] });
  const slot = new KeySlot({
    policy: store.get('groq:llama-8b'), credential: 'k1', credentialSource: 'T', headroom: baseConfig.headroom,
  });
  slot.recordFailure('rate_limit', {});
  assert.equal(slot.isCoolingDown(), true);
  const firstCooldown = slot.cooldownUntil - Date.now();
  slot.recordFailure('rate_limit', {});
  assert.ok(slot.cooldownUntil - Date.now() > firstCooldown, 'backoff grows');
  slot.recordFailure('rate_limit', { retryAfterMs: 120000 });
  assert.ok(slot.cooldownUntil - Date.now() >= 119000, 'honours Retry-After when larger');

  slot.reset();
  slot.recordFailure('auth', { message: 'invalid key' });
  assert.equal(slot.isDisabled(), true);
  assert.equal(slot.canServe(1).reason, 'disabled:auth');
  assert.equal(slot.disabledUntil, Infinity);
});

test('key pool spreads load across credentials and skips saturated slots', () => {
  const { config, store } = makeStore({ groq: ['k1', 'k2', 'k3'] });
  const pool = new KeyPool({ policyStore: store, config });
  assert.equal(pool.slotsFor('groq:llama-70b').length, 3);
  const used = new Set();
  for (let i = 0; i < 3; i += 1) {
    const { slot } = pool.acquire('groq:llama-70b', 10);
    assert.ok(slot, 'slot acquired');
    slot.release();
    slot.recordSuccess({ tokens: 10 });
    used.add(slot.id);
  }
  assert.equal(used.size, 3, 'round-robin touched every credential');

  for (const slot of pool.slotsFor('groq:llama-70b')) slot.recordFailure('rate_limit', {});
  const blocked = pool.acquire('groq:llama-70b', 10);
  assert.equal(blocked.slot, undefined);
  assert.equal(blocked.reason, 'cooldown');
  assert.ok(blocked.retryAfterMs > 0);
});

test('provider bench removes every slot of that provider', () => {
  const { config, store } = makeStore({ groq: ['k1'] });
  const pool = new KeyPool({ policyStore: store, config });
  pool.benchProvider('groq', 30000, 'server-errors');
  assert.equal(pool.isProviderBenched('groq'), true);
  assert.equal(pool.acquire('groq:llama-70b', 10).reason, 'provider-benched');
});

/* ----------------------------------------------------------------- router */

test('task types and aliases normalise correctly', () => {
  assert.equal(TASK_TYPES.length, 8);
  assert.equal(normalizeTaskType('code'), 'code-generation');
  assert.equal(normalizeTaskType('SUMMARIZE'), 'summarization');
  assert.equal(normalizeTaskType('sentiment'), 'classification');
  assert.equal(normalizeTaskType('nonsense'), 'general');
  for (const task of TASK_TYPES) assert.equal(normalizeTaskType(task), task);
});

test('requiredCapabilities detects audio, image and document payloads', () => {
  assert.deepEqual(requiredCapabilities([{
    role: 'user', content: [{ inlineData: { mimeType: 'audio/ogg', data: 'x' } }],
  }]), ['audio']);
  assert.deepEqual(requiredCapabilities([{
    role: 'user', content: [{ inlineData: { mimeType: 'application/pdf', data: 'x' } }],
  }]), ['document']);
  assert.deepEqual(requiredCapabilities([{ role: 'user', content: 'plain' }]), []);
});

test('router filters by capability, availability and context window', () => {
  const { config, store } = makeStore({ groq: ['k1'], gemini: ['g1'] });
  const router = new SmartRouter({ policyStore: store, config });

  const audioPlan = router.plan({
    taskType: 'summarization',
    messages: [{ role: 'user', content: [{ inlineData: { mimeType: 'audio/ogg', data: 'AA' } }] }],
    maxTokens: 1000,
  });
  assert.ok(audioPlan.candidates.length > 0);
  for (const key of audioPlan.candidates) {
    assert.ok(store.get(key).capabilities.includes('audio'), `${key} must support audio`);
  }
  assert.equal(audioPlan.candidates[0], 'gemini:flash', 'gemini leads the audio route');

  const hugePlan = router.plan({
    taskType: 'spec-generation',
    messages: [{ role: 'user', content: 'x'.repeat(900000) }], // ~250k tokens
    maxTokens: 4000,
  });
  for (const key of hugePlan.candidates) {
    assert.ok(store.get(key).contextWindow >= hugePlan.requiredContext, `${key} window too small`);
  }
  assert.ok(hugePlan.rejected.some((entry) => entry.reason.startsWith('context-too-small')));
});

test('router honours an explicit model pin and benching', () => {
  const { config, store } = makeStore({ groq: ['k1'], gemini: ['g1'] });
  const router = new SmartRouter({ policyStore: store, config });
  const pinned = router.plan({ taskType: 'general', messages: [{ role: 'user', content: 'hi' }], model: 'gemini:flash' });
  assert.equal(pinned.candidates[0], 'gemini:flash');
  router.benchModel('gemini:flash', 60000, 'test');
  const benched = router.plan({ taskType: 'general', messages: [{ role: 'user', content: 'hi' }], model: 'gemini:flash' });
  assert.ok(!benched.candidates.includes('gemini:flash'));
});

test('mock candidates are always ordered last', () => {
  const { config, store } = makeStore({ groq: ['k1'] });
  const router = new SmartRouter({ policyStore: store, config });
  const plan = router.plan({ taskType: 'general', messages: [{ role: 'user', content: 'hi' }] });
  const firstMock = plan.candidates.findIndex((key) => key.startsWith('mock:'));
  const lastReal = plan.candidates.reduce((acc, key, index) => (key.startsWith('mock:') ? acc : index), -1);
  assert.ok(firstMock > lastReal, 'mock slots come after every real model');
});

/* --------------------------------------------------------------- fallback */

test('error classification maps status codes and bodies correctly', () => {
  assert.equal(classifyHttpError(429, ''), ERROR_CLASS.RATE_LIMIT);
  assert.equal(classifyHttpError(401, ''), ERROR_CLASS.AUTH);
  assert.equal(classifyHttpError(503, ''), ERROR_CLASS.SERVER);
  assert.equal(classifyHttpError(400, 'maximum context length exceeded'), ERROR_CLASS.CONTEXT_LENGTH);
  assert.equal(classifyHttpError(400, 'blocked by content filter'), ERROR_CLASS.SAFETY_BLOCK);
  assert.equal(classifyHttpError(400, 'unexpected field'), ERROR_CLASS.BAD_REQUEST);
  assert.equal(RECOVERY_ACTIONS[ERROR_CLASS.RATE_LIMIT], 'cooldown-slot');
  assert.equal(RECOVERY_ACTIONS[ERROR_CLASS.AUTH], 'disable-slot');
  assert.equal(RECOVERY_ACTIONS[ERROR_CLASS.CONTEXT_LENGTH], 'raise-context');
  assert.equal(RECOVERY_ACTIONS[ERROR_CLASS.SAFETY_BLOCK], 'bench-model');
  assert.equal(RECOVERY_ACTIONS[ERROR_CLASS.BAD_REQUEST], 'abort');
});

test('parseRetryAfter understands seconds, durations and dates', () => {
  assert.equal(parseRetryAfter('2'), 2000);
  assert.equal(parseRetryAfter('1.5s'), 1500);
  assert.equal(parseRetryAfter('2m30s'), 150000);
  assert.equal(parseRetryAfter('500ms'), 500);
  assert.ok(parseRetryAfter(new Date(Date.now() + 5000).toUTCString()) > 3000);
  assert.equal(parseRetryAfter(null), 0);
});

test('fallback advances to the next model after a rate limit', async () => {
  const { config, store } = makeStore({ groq: ['k1'], gemini: ['g1'] });
  const keyPool = new KeyPool({ policyStore: store, config });
  const router = new SmartRouter({ policyStore: store, config });
  const providers = new Map([
    ['groq', new ScriptedProvider('groq', () => new ProviderError('429', {
      errorClass: ERROR_CLASS.RATE_LIMIT, status: 429, provider: 'groq', retryAfterMs: 1000,
    }))],
    ['gemini', new ScriptedProvider('gemini', { text: 'recovered by gemini' })],
    ['mock', new MockProvider()],
  ]);
  const engine = new FallbackEngine({ policyStore: store, keyPool, router, providers, config });
  const result = await engine.execute({
    taskType: 'general', messages: [{ role: 'user', content: 'hello' }], maxTokens: 100, temperature: 0.2,
  });
  assert.equal(result.text, 'recovered by gemini');
  assert.equal(result.provider, 'gemini');
  assert.ok(result.attempts.some((a) => a.errorClass === ERROR_CLASS.RATE_LIMIT && a.action === 'cooldown-slot'));
});

test('auth failure disables the credential for subsequent requests', async () => {
  const { config, store } = makeStore({ groq: ['k1'], gemini: ['g1'] });
  const keyPool = new KeyPool({ policyStore: store, config });
  const router = new SmartRouter({ policyStore: store, config });
  const providers = new Map([
    ['groq', new ScriptedProvider('groq', () => new ProviderError('bad key', {
      errorClass: ERROR_CLASS.AUTH, status: 401, provider: 'groq',
    }))],
    ['gemini', new ScriptedProvider('gemini', { text: 'gemini ok' })],
    ['mock', new MockProvider()],
  ]);
  const engine = new FallbackEngine({ policyStore: store, keyPool, router, providers, config });
  await engine.execute({ taskType: 'general', messages: [{ role: 'user', content: 'a' }], maxTokens: 50 });
  for (const slot of keyPool.slotsFor('groq:llama-70b')) {
    assert.equal(slot.isDisabled(), true, 'credential disabled after 401');
  }
});

test('context_length error re-plans onto a larger-window model', async () => {
  const { config, store } = makeStore({ cerebras: ['c1'], gemini: ['g1'] });
  const keyPool = new KeyPool({ policyStore: store, config });
  const router = new SmartRouter({ policyStore: store, config });
  const providers = new Map([
    ['cerebras', new ScriptedProvider('cerebras', () => new ProviderError('context length exceeded', {
      errorClass: ERROR_CLASS.CONTEXT_LENGTH, status: 400, provider: 'cerebras',
    }))],
    ['gemini', new ScriptedProvider('gemini', { text: 'handled by the 1M window' })],
    ['mock', new MockProvider()],
  ]);
  const engine = new FallbackEngine({ policyStore: store, keyPool, router, providers, config });
  const result = await engine.execute({
    taskType: 'code-generation', messages: [{ role: 'user', content: 'x'.repeat(50000) }], maxTokens: 500,
  });
  assert.equal(result.provider, 'gemini');
  const contextAttempt = result.attempts.find((a) => a.errorClass === ERROR_CLASS.CONTEXT_LENGTH);
  assert.equal(contextAttempt.action, 'raise-context');
  const cerebrasWindow = store.get('cerebras:gpt-oss-120b').contextWindow;
  assert.ok(store.get(result.model).contextWindow > cerebrasWindow);
});

test('safety block benches the model family', async () => {
  const { config, store } = makeStore({ groq: ['k1'], gemini: ['g1'] });
  const keyPool = new KeyPool({ policyStore: store, config });
  const router = new SmartRouter({ policyStore: store, config });
  const providers = new Map([
    ['groq', new ScriptedProvider('groq', () => new ProviderError('safety', {
      errorClass: ERROR_CLASS.SAFETY_BLOCK, status: 200, provider: 'groq',
    }))],
    ['gemini', new ScriptedProvider('gemini', { text: 'answered anyway' })],
    ['mock', new MockProvider()],
  ]);
  const engine = new FallbackEngine({ policyStore: store, keyPool, router, providers, config });
  const result = await engine.execute({ taskType: 'general', messages: [{ role: 'user', content: 'q' }], maxTokens: 50 });
  assert.equal(result.provider, 'gemini');
  assert.ok(router.isBenched('groq:llama-70b'), 'model benched after a safety block');
});

test('bad_request aborts immediately without burning the chain', async () => {
  const { config, store } = makeStore({ groq: ['k1'], gemini: ['g1'] });
  const keyPool = new KeyPool({ policyStore: store, config });
  const router = new SmartRouter({ policyStore: store, config });
  const gemini = new ScriptedProvider('gemini', { text: 'never reached' });
  const providers = new Map([
    ['groq', new ScriptedProvider('groq', () => new ProviderError('bad payload', {
      errorClass: ERROR_CLASS.BAD_REQUEST, status: 400, provider: 'groq', retryable: false,
    }))],
    ['gemini', gemini],
    ['mock', new MockProvider()],
  ]);
  const engine = new FallbackEngine({ policyStore: store, keyPool, router, providers, config });
  await assert.rejects(
    () => engine.execute({ taskType: 'general', messages: [{ role: 'user', content: 'q' }], maxTokens: 50 }),
    (error) => error instanceof ExhaustedError,
  );
  assert.equal(gemini.calls.length, 0, 'chain stopped at the terminal error');
});

test('everything failing lands on the deterministic mock provider', async () => {
  const { config, store } = makeStore({ groq: ['k1'] });
  const keyPool = new KeyPool({ policyStore: store, config });
  const router = new SmartRouter({ policyStore: store, config });
  const providers = new Map([
    ['groq', new ScriptedProvider('groq', () => new ProviderError('down', {
      errorClass: ERROR_CLASS.SERVER, status: 500, provider: 'groq',
    }))],
    ['mock', new MockProvider()],
  ]);
  const engine = new FallbackEngine({ policyStore: store, keyPool, router, providers, config });
  const result = await engine.execute({
    taskType: 'general', messages: [{ role: 'user', content: 'still answer me please' }], maxTokens: 200,
  });
  assert.equal(result.provider, 'mock');
  assert.ok(result.text.length > 0);
});

/* ----------------------------------------------------------------- fusion */

test('fusion merges JSON field-wise and averages numbers', () => {
  const fused = fuseJson([
    { sentiment: 'Bullish', score: 70, drivers: ['etf'] },
    { sentiment: 'Bullish', score: 80, drivers: ['halving'] },
    { sentiment: 'Bearish', score: 60, drivers: ['etf'] },
  ]);
  assert.equal(fused.sentiment, 'Bullish', 'majority vote');
  assert.equal(fused.score, 70, 'numeric average');
  assert.deepEqual(fused.drivers, ['etf', 'halving'], 'array union');
});

test('fusion picks the consensus centroid for text', () => {
  const { text, agreement } = fuseText([
    'the market is bullish because inflows rose sharply',
    'the market is bullish since inflows rose sharply today',
    'completely unrelated commentary about weather patterns',
  ]);
  assert.ok(text.startsWith('the market is bullish'));
  assert.ok(agreement > 0 && agreement <= 1);
  assert.ok(similarity('alpha beta gamma', 'alpha beta gamma') === 1);
  assert.ok(similarity('alpha beta', 'zulu yankee') === 0);
});

/* ----------------------------------------------------------- user limiter */

test('user limiter throttles per minute and honours USERS_JSON tiers', () => {
  const limiter = new UserLimiter({ config: { ...baseConfig, users: { vip: { tier: 'pro' }, god: { tier: 'unlimited' } } } });
  for (let i = 0; i < 12; i += 1) assert.equal(limiter.consume('free-user').allowed, true);
  const blocked = limiter.consume('free-user');
  assert.equal(blocked.allowed, false);
  assert.equal(blocked.reason, 'user_rpm');
  assert.ok(blocked.retryAfterMs > 0);

  for (let i = 0; i < 40; i += 1) assert.equal(limiter.consume('vip').allowed, true, `vip call ${i}`);
  assert.equal(limiter.consume('vip').allowed, false);

  for (let i = 0; i < 100; i += 1) assert.equal(limiter.consume('god').allowed, true);
});

/* ------------------------------------------------- request validation */

test('validateRequest normalises prompts, messages and media parts', () => {
  const fromPrompt = validateRequest({ prompt: 'hello' });
  assert.deepEqual(fromPrompt.messages, [{ role: 'user', content: 'hello' }]);
  assert.equal(fromPrompt.taskType, 'general');
  assert.equal(fromPrompt.temperature, 0.3);
  assert.equal(fromPrompt.maxTokens, 2048);

  const media = validateRequest({
    taskType: 'summarization',
    messages: [{ role: 'user', content: [{ inlineData: { mimeType: 'audio/ogg', data: 'AAAA' } }, 'transcribe'] }],
  });
  assert.equal(media.messages[0].content[0].type, 'inline_data');
  assert.equal(media.messages[0].content[1].text, 'transcribe');

  assert.throws(() => validateRequest({}), ValidationError);
  assert.throws(() => validateRequest({ prompt: 'x', temperature: 5 }), ValidationError);
  assert.throws(() => validateRequest({ prompt: 'x', maxTokens: 0 }), ValidationError);
  assert.throws(() => validateRequest({ messages: [{ role: 'user', content: [{ inlineData: { data: 'x' } }] }] }), ValidationError);
});

/* ----------------------------------------------- provider wire protocols */

test('toGeminiPayload maps roles, media, system instruction and json mode', () => {
  const payload = toGeminiPayload(
    [
      { role: 'system', content: 'be terse' },
      { role: 'user', content: [{ type: 'text', text: 'summarise' }, { inlineData: { mimeType: 'audio/ogg', data: 'AAA' } }] },
      { role: 'assistant', content: 'ok' },
      { role: 'user', content: 'go on' },
    ],
    { system: 'outer system', temperature: 0.4, maxTokens: 900, jsonMode: true, maxOutputCap: 65536 },
  );
  assert.equal(payload.systemInstruction.parts[0].text, 'outer system');
  assert.equal(payload.systemInstruction.parts[1].text, 'be terse');
  assert.equal(payload.contents[0].role, 'user');
  assert.equal(payload.contents[0].parts[1].inlineData.mimeType, 'audio/ogg');
  assert.equal(payload.contents[1].role, 'model');
  assert.equal(payload.generationConfig.responseMimeType, 'application/json');
  assert.equal(payload.generationConfig.maxOutputTokens, 900);
  assert.equal(payload.generationConfig.temperature, 0.4);
});

test('provider adapters speak their real wire formats against a stub server', async (t) => {
  const received = [];
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => {
      const body = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
      received.push({ url: req.url, headers: req.headers, body });

      if (req.url.includes(':generateContent')) {
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({
          candidates: [{ content: { parts: [{ text: 'gemini says hi' }] }, finishReason: 'STOP' }],
          usageMetadata: { promptTokenCount: 11, candidatesTokenCount: 5, totalTokenCount: 16 },
        }));
        return;
      }
      if (req.url.endsWith('/chat/completions')) {
        if (body.model === 'rate-limited') {
          res.writeHead(429, { 'content-type': 'application/json', 'retry-after': '3' });
          res.end(JSON.stringify({ error: { message: 'rate limit reached' } }));
          return;
        }
        if (body.model === 'blocked') {
          res.writeHead(200, { 'content-type': 'application/json' });
          res.end(JSON.stringify({ choices: [{ message: { content: '' }, finish_reason: 'content_filter' }] }));
          return;
        }
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({
          choices: [{ message: { content: 'openai-compatible says hi' }, finish_reason: 'stop' }],
          usage: { prompt_tokens: 7, completion_tokens: 4, total_tokens: 11 },
        }));
        return;
      }
      if (req.url.includes('/ai/run/')) {
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ success: true, result: { response: 'cloudflare says hi' }, errors: [] }));
        return;
      }
      res.writeHead(404); res.end('{}');
    });
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  t.after(() => new Promise((resolve) => server.close(resolve)));

  const { store } = makeStore({ gemini: ['g1'], groq: ['k1'], cloudflare: ['cf1'] });

  // --- Gemini ---
  const gemini = new GeminiProvider({ providerConfig: { baseUrl: `${base}/v1beta` }, timeoutMs: 4000 });
  const geminiResult = await gemini.complete({
    policy: store.get('gemini:flash'),
    credential: 'g1',
    messages: [{ role: 'user', content: 'hi' }],
    temperature: 0.2,
    maxTokens: 100,
  });
  assert.equal(geminiResult.text, 'gemini says hi');
  assert.equal(geminiResult.usage.totalTokens, 16);
  const geminiCall = received.find((entry) => entry.url.includes(':generateContent'));
  assert.equal(geminiCall.headers['x-goog-api-key'], 'g1', 'auth sent via x-goog-api-key');
  assert.equal(geminiCall.url.includes('gemini-2.5-flash'), true);

  // --- OpenAI-compatible (Groq) ---
  const groq = new OpenAICompatProvider({ name: 'groq', variant: 'groq', providerConfig: { baseUrl: base }, timeoutMs: 4000 });
  const groqResult = await groq.complete({
    policy: store.get('groq:llama-70b'),
    credential: 'k1',
    messages: [{ role: 'user', content: 'hi' }],
    system: 'be brief',
    temperature: 0.2,
    maxTokens: 100,
  });
  assert.equal(groqResult.text, 'openai-compatible says hi');
  const groqCall = received.find((entry) => entry.body.model === 'llama-3.3-70b-versatile');
  assert.equal(groqCall.headers.authorization, 'Bearer k1');
  assert.equal(groqCall.body.messages[0].role, 'system');
  assert.equal(groqCall.body.max_completion_tokens, 100, 'groq uses max_completion_tokens');

  // --- 429 mapping with Retry-After ---
  await assert.rejects(
    () => groq.complete({
      policy: { ...store.get('groq:llama-70b'), upstreamModel: 'rate-limited' },
      credential: 'k1',
      messages: [{ role: 'user', content: 'hi' }],
      maxTokens: 10,
    }),
    (error) => {
      assert.equal(error.errorClass, ERROR_CLASS.RATE_LIMIT);
      assert.equal(error.status, 429);
      assert.equal(error.retryAfterMs, 3000);
      return true;
    },
  );

  // --- empty completion + content_filter -> safety_block ---
  await assert.rejects(
    () => groq.complete({
      policy: { ...store.get('groq:llama-70b'), upstreamModel: 'blocked' },
      credential: 'k1',
      messages: [{ role: 'user', content: 'hi' }],
      maxTokens: 10,
    }),
    (error) => error.errorClass === ERROR_CLASS.SAFETY_BLOCK,
  );

  // --- Cloudflare envelope ---
  const cloudflare = new CloudflareProvider({
    providerConfig: { baseUrl: `${base}/client/v4`, accountId: 'acct' }, timeoutMs: 4000,
  });
  const cfResult = await cloudflare.complete({
    policy: store.get('cloudflare:llama-8b'),
    credential: 'cf1',
    messages: [{ role: 'user', content: 'hi' }],
    maxTokens: 50,
  });
  assert.equal(cfResult.text, 'cloudflare says hi');
  const cfCall = received.find((entry) => entry.url.includes('/ai/run/'));
  assert.ok(cfCall.url.includes('accounts/acct/ai/run/@cf/meta/llama-3.1-8b-instruct-fast'));
});

test('mock provider produces task-shaped text and valid JSON', async () => {
  const mock = new MockProvider();
  const policy = { key: 'mock:general', upstreamModel: 'konkred-mock-general', maxOutputTokens: 4096 };
  const text = await mock.complete({
    policy, messages: [{ role: 'user', content: 'Ship the release on Friday. Ana owns QA.' }], taskType: 'summarization',
  });
  assert.match(text.text, /Action Items/);
  const json = await mock.complete({
    policy, messages: [{ role: 'user', content: 'Bitcoin rallied on ETF inflows.' }], taskType: 'classification', jsonMode: true,
  });
  const parsed = JSON.parse(json.text);
  assert.equal(parsed.taskType, 'classification');
  assert.ok(Number.isFinite(parsed.score));
});

/* ------------------------------------------------------- gateway e2e */

test('gateway end-to-end: cache, dedup, limiter and stats', async () => {
  const config = { ...configWith({}), mockOnly: true, userRpm: 100 };
  const store = new PolicyStore({ registryPath: baseConfig.registryPath, config });
  const gateway = new Gateway({ config, policyStore: store });

  const first = await gateway.handle({ prompt: 'hello konkred', userId: 'u1', taskType: 'general' });
  assert.equal(first.cached, false);
  assert.equal(first.provider, 'mock');
  assert.ok(first.text.length > 0);

  const second = await gateway.handle({ prompt: 'hello konkred', userId: 'u1', taskType: 'general' });
  assert.equal(second.cached, true, 'identical request served from cache');
  assert.equal(gateway.health().requests.cacheHits, 1);

  const concurrent = await Promise.all(Array.from({ length: 6 }, () => gateway.handle({
    prompt: 'coalesce me', userId: 'u2', taskType: 'general', noCache: true,
  })));
  assert.equal(concurrent.length, 6);
  assert.ok(gateway.dedup.stats.coalesced >= 1, 'concurrent identical calls coalesced');

  const stats = gateway.stats();
  assert.ok(stats.pool.totalSlots > 0);
  assert.equal(typeof stats.byTask.general.requests, 'number');
  gateway.shutdown();
});

test('gateway rejects an over-quota user with a typed 429', async () => {
  const config = { ...configWith({}), mockOnly: true, userRpm: 2, cacheEnabled: false };
  const store = new PolicyStore({ registryPath: baseConfig.registryPath, config });
  const gateway = new Gateway({ config, policyStore: store });
  await gateway.handle({ prompt: 'a', userId: 'burst' });
  await gateway.handle({ prompt: 'b', userId: 'burst' });
  await assert.rejects(
    () => gateway.handle({ prompt: 'c', userId: 'burst' }),
    (error) => {
      assert.equal(error.status, 429);
      assert.equal(error.code, 'user_rpm');
      assert.ok(error.retryAfterMs > 0);
      return true;
    },
  );
  gateway.shutdown();
});

/* --------------------------------------------------- watchdog + dashboard */

test('watchdog prunes state and calibrates an over-optimistic quota', () => {
  const config = { ...configWith({ groq: ['k1'] }), watchdogStatePath: '' };
  const store = new PolicyStore({ registryPath: baseConfig.registryPath, config });
  const gateway = new Gateway({ config, policyStore: store });
  const watchdog = new QuotaWatchdog({ gateway, config });

  const slot = gateway.keyPool.slotsFor('groq:llama-70b')[0];
  slot.totals.rateLimits = 4;
  for (let i = 0; i < 5; i += 1) slot.recordSuccess({ tokens: 10 });

  const report = watchdog.tick();
  assert.ok(report.calibrated >= 1, 'observed ceiling recorded');
  assert.ok(slot.observedLimits.rpm <= 5);
  assert.equal(slot.totals.rateLimits, 0, 'counter reset for re-measurement');
  assert.equal(watchdog.snapshot().ticks, 1);
  gateway.shutdown();
});

test('dashboard renders valid escaped HTML', () => {
  const config = { ...configWith({}), mockOnly: true };
  const store = new PolicyStore({ registryPath: baseConfig.registryPath, config });
  const gateway = new Gateway({ config, policyStore: store });
  const watchdog = new QuotaWatchdog({ gateway, config });
  const html = renderDashboard({
    stats: gateway.stats(),
    watchdog: watchdog.snapshot(),
    providers: [{ provider: '<script>x</script>', enabled: true, credentials: 1, sources: ['ENV'], baseUrl: 'http://x', ready: true }],
    registryVersion: store.version,
    env: 'test',
  });
  assert.match(html, /^<!doctype html>/);
  assert.match(html, /Konkred AI Gateway/);
  assert.ok(!html.includes('<script>x</script>'), 'provider name escaped');
  assert.ok(html.includes('&lt;script&gt;'));
  gateway.shutdown();
});

test('HTTP 429 carries a retry-after header and never double-writes', async (t) => {
  // Regression: the rate-limit branch used to call res.setHeader() *after*
  // sendError() had already written the head, raising ERR_HTTP_HEADERS_SENT
  // as an unhandled rejection on every throttled request.
  const rejections = [];
  const onRejection = (reason) => rejections.push(reason);
  process.on('unhandledRejection', onRejection);
  t.after(() => process.off('unhandledRejection', onRejection));

  process.env.MOCK_ONLY = 'true';
  // CONFIG is a module singleton that earlier tests have already imported, so
  // USER_RPM cannot be lowered here - burst past the real default instead.
  const [{ server }, { CONFIG }] = await Promise.all([
    import('../src/server.mjs'),
    import('../src/config.mjs'),
  ]);
  const burst = CONFIG.userRpm + 3;
  t.after(() => new Promise((resolve) => server.close(resolve)));

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();

  const call = () => fetch(`http://127.0.0.1:${port}/api/ai`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      taskType: 'general',
      userId: 'retry-after-probe',
      maxTokens: 16,
      messages: [{ role: 'user', content: 'probe' }],
    }),
  });

  let throttled = null;
  for (let i = 0; i < burst && !throttled; i += 1) {
    const response = await call();
    if (response.status === 429) throttled = response;
    else await response.arrayBuffer();
  }

  assert.ok(throttled, 'the limiter should reject once USER_RPM is exceeded');
  const retryAfter = Number(throttled.headers.get('retry-after'));
  assert.ok(Number.isInteger(retryAfter) && retryAfter >= 1, `retry-after should be a positive integer, got ${retryAfter}`);

  const payload = await throttled.json();
  assert.equal(payload.error.code, 'user_rpm');
  assert.ok(payload.error.retryAfterMs > 0);

  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(rejections, [], 'no unhandled rejection should escape the 429 path');
});
