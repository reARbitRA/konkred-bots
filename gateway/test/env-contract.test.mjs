/**
 * The .env.example file is the only instruction a deployer gets. If it
 * documents a knob that no code reads, the deployer sets it, nothing happens,
 * and the failure is invisible - the system just quietly behaves differently
 * than promised.
 *
 * Every test in this repo runs with MOCK_ONLY, which makes exactly this class
 * of bug undetectable: a misspelled or unread credential variable still ends
 * up falling back to the mock provider and every suite stays green. These
 * tests hold the documentation and the code to each other instead.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');

const envExample = fs.readFileSync(path.join(ROOT, '.env.example'), 'utf8');
const configSrc = fs.readFileSync(
  path.join(ROOT, 'gateway', 'src', 'config.mjs'), 'utf8');

/** Variables the bots own; the gateway is not expected to read these. */
const PYTHON_OWNED = new Set([
  'TELEGRAM_VOICE_BOT_TOKEN', 'TELEGRAM_PDF_BOT_TOKEN', 'TELEGRAM_IELTS_BOT_TOKEN',
  'TELEGRAM_CONTENT_BOT_TOKEN', 'TELEGRAM_CRYPTO_BOT_TOKEN',
  'REDIS_URL', 'GATEWAY_URL', 'GATEWAY_TIMEOUT', 'GATEWAY_MAX_RETRIES',
  'HISTORY_TURNS', 'HISTORY_TTL', 'MAX_FILE_MB', 'MAX_MESSAGE_LENGTH',
  'DROP_PENDING_UPDATES', 'LOG_LEVEL', 'DOTENV_PATH',
]);

/** Variables consumed by docker-compose / the host, not by application code. */
const INFRA_OWNED = new Set(['GATEWAY_PORT', 'REDIS_PORT', 'TZ']);

/** Every `NAME=` assignment in .env.example, including commented examples. */
function documentedVars(text) {
  const names = [];
  for (const line of text.split('\n')) {
    const match = /^#?\s*([A-Z][A-Z0-9_]{2,})\s*=/.exec(line);
    if (match) names.push(match[1]);
  }
  return [...new Set(names)];
}

/**
 * Same, but tagged with the `# --- Section ---` heading each variable sits
 * under. A name documented under a gateway heading must be read by the
 * gateway even if the bots happen to read a variable of the same name -
 * otherwise a bot-owned name can hide a fabricated gateway knob.
 */
function documentedBySection(text) {
  const rows = [];
  let section = '(preamble)';
  let subLabel = '';
  for (const line of text.split('\n')) {
    const heading = /^#\s*-{2,}\s*(.+?)\s*-{2,}\s*$/.exec(line);
    if (heading) { section = heading[1]; subLabel = ''; continue; }
    // Prose sub-labels like "# Gateway server internals:" group the entries
    // that follow them inside a broader section.
    const label = /^#\s*([A-Z][^=]*?):\s*$/.exec(line);
    if (label) { subLabel = label[1].trim(); continue; }
    if (/^\s*$/.test(line)) { subLabel = ''; continue; }
    const match = /^#?\s*([A-Z][A-Z0-9_]{2,})\s*=/.exec(line);
    if (match) {
      rows.push({
        name: match[1],
        section: subLabel ? `${section} / ${subLabel}` : section,
      });
    }
  }
  return rows;
}

/** Headings whose variables the gateway process must actually read. */
const GATEWAY_SECTIONS = [
  /gateway server internals/i,
  /response cache/i,
  /in-flight deduplication/i,
  /per-user fair use/i,
  /quota safety headroom/i,
  /fallback behaviour/i,
  /watchdog/i,
];

/**
 * Names config.mjs reads. collectKeys() also expands NAME_1..NAME_9 and
 * NAME_P1..NAME_P9, so a documented pool variant counts as read when its base
 * name is read.
 */
function readByConfig(source) {
  const names = new Set();
  for (const m of source.matchAll(/collectKeys\(([^)]*)\)/g)) {
    for (const q of m[1].matchAll(/'([A-Z0-9_]+)'/g)) names.add(q[1]);
  }
  for (const m of source.matchAll(/\b(?:str|num|bool|int)\(\s*'([A-Z0-9_]+)'/g)) {
    names.add(m[1]);
  }
  for (const m of source.matchAll(/process\.env\.([A-Z0-9_]+)/g)) names.add(m[1]);
  for (const m of source.matchAll(/process\.env\[\s*'([A-Z0-9_]+)'/g)) names.add(m[1]);
  return names;
}

const POOL_SUFFIX = /_(?:P?[1-9])$/;

test('.env.example documents no gateway variable that config.mjs ignores', () => {
  const read = readByConfig(configSrc);
  const ghosts = documentedVars(envExample).filter((name) => {
    if (PYTHON_OWNED.has(name) || INFRA_OWNED.has(name)) return false;
    if (read.has(name)) return false;
    // NAME_1 / NAME_P1 are synthesised by collectKeys from NAME.
    const base = name.replace(POOL_SUFFIX, '');
    return !read.has(base);
  });

  assert.deepEqual(ghosts, [],
    `.env.example documents ${ghosts.length} variable(s) that no gateway code ` +
    `reads: ${ghosts.join(', ')}. Either wire them up or drop them - a ` +
    'documented knob that does nothing is worse than no knob.');
});

test('variables under a gateway heading are read by the gateway', () => {
  // The allowlist above exempts names the bots own, which is right in
  // general - but it must not let a fabricated gateway knob hide behind a
  // bot-owned name. Anything filed under a gateway section is held to the
  // gateway's config regardless of what Python reads.
  const read = readByConfig(configSrc);
  const ghosts = documentedBySection(envExample)
    .filter(({ section }) => GATEWAY_SECTIONS.some((re) => re.test(section)))
    .filter(({ name }) => !INFRA_OWNED.has(name)
      && !read.has(name)
      && !read.has(name.replace(POOL_SUFFIX, '')))
    .map(({ name, section }) => `${name} (under "${section}")`);

  assert.deepEqual(ghosts, [],
    `${ghosts.length} variable(s) are documented as gateway settings but no ` +
    `gateway code reads them: ${ghosts.join(', ')}.`);
});

test('every provider credential variable reaches the key pool', async () => {
  // Prove the documented credential names actually produce usable slots,
  // rather than being silently ignored and falling back to mock.
  // Gemini is documented only as pool variants (GEMINI_KEY_P1..P3), so strip
  // the pool suffix and dedupe to get one name per provider credential.
  const credentialVars = [...new Set(documentedVars(envExample)
    .filter((name) => /_(?:API_)?KEY(?:_P?[1-9])?$|_TOKEN$/.test(name)
      && !PYTHON_OWNED.has(name)
      && !/^(?:ADMIN|GATEWAY_API)_KEY$/.test(name))
    .map((name) => name.replace(POOL_SUFFIX, '')))];

  // gemini, groq, cerebras, mistral, openrouter, cloudflare, github = 7
  assert.equal(credentialVars.length, 7,
    `expected one documented credential per real provider, got ` +
    `${credentialVars.length}: ${credentialVars.join(', ')}`);

  const { collectKeys } = await import('../src/config.mjs');
  for (const name of credentialVars) {
    const previous = process.env[name];
    process.env[name] = `probe-${name}`;
    try {
      const keys = collectKeys(name);
      assert.ok(keys.some((entry) => entry.key === `probe-${name}`),
        `${name} is documented but collectKeys() does not pick it up`);
    } finally {
      if (previous === undefined) delete process.env[name];
      else process.env[name] = previous;
    }
  }
});

test('collectKeys expands numbered and comma-separated pools', async () => {
  // GEMINI_KEY_P1..P3 appear in .env.example; make sure that is real and not
  // just documentation folklore.
  const { collectKeys } = await import('../src/config.mjs');
  const names = ['PROBE_POOL', 'PROBE_POOL_1', 'PROBE_POOL_P2'];
  const saved = names.map((name) => [name, process.env[name]]);
  try {
    process.env.PROBE_POOL = 'alpha, beta';
    process.env.PROBE_POOL_1 = 'gamma';
    process.env.PROBE_POOL_P2 = 'delta';
    const keys = collectKeys('PROBE_POOL').map((entry) => entry.key);
    assert.deepEqual(keys, ['alpha', 'beta', 'gamma', 'delta']);
  } finally {
    for (const [name, value] of saved) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
});
