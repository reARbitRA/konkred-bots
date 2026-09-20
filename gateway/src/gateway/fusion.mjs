/**
 * Konkred AI Gateway - response fusion.
 *
 * For `fusion: true` requests the gateway fans the same prompt out to N
 * distinct model families in parallel and merges the results:
 *   - JSON mode  -> field-wise majority vote across parsed objects
 *   - text mode  -> highest-consensus draft (Jaccard similarity centroid),
 *                   annotated with the agreement score
 *
 * This is how the crypto bot gets a sentiment number that is not the opinion of
 * a single 8B model, without paying for a second round-trip in the common case.
 */
import { extractJson, round } from '../util.mjs';

/** Tokenise for similarity scoring. */
function tokenSet(text) {
  return new Set(String(text ?? '').toLowerCase().match(/[a-z0-9]{3,}/g) ?? []);
}

/** Jaccard similarity between two strings. */
export function similarity(a, b) {
  const left = tokenSet(a);
  const right = tokenSet(b);
  if (!left.size && !right.size) return 1;
  if (!left.size || !right.size) return 0;
  let intersection = 0;
  for (const token of left) if (right.has(token)) intersection += 1;
  return intersection / (left.size + right.size - intersection);
}

/** Majority vote over primitive values (numbers are averaged). */
function reduceValues(values) {
  const defined = values.filter((value) => value !== undefined && value !== null);
  if (!defined.length) return null;
  const numeric = defined.filter((value) => typeof value === 'number' && Number.isFinite(value));
  if (numeric.length === defined.length) {
    return round(numeric.reduce((sum, value) => sum + value, 0) / numeric.length, 2);
  }
  const counts = new Map();
  for (const value of defined) {
    const key = typeof value === 'object' ? JSON.stringify(value) : String(value);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const [winner] = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  const raw = defined.find((value) => (typeof value === 'object' ? JSON.stringify(value) : String(value)) === winner[0]);
  return raw;
}

/** Field-wise merge of parsed JSON objects. */
export function fuseJson(objects) {
  const valid = objects.filter((object) => object && typeof object === 'object' && !Array.isArray(object));
  if (!valid.length) return null;
  if (valid.length === 1) return valid[0];
  const keys = [...new Set(valid.flatMap((object) => Object.keys(object)))];
  const merged = {};
  for (const key of keys) {
    const values = valid.map((object) => object[key]);
    const objectValues = values.filter((value) => value && typeof value === 'object' && !Array.isArray(value));
    const arrayValues = values.filter((value) => Array.isArray(value));
    if (objectValues.length >= 2) merged[key] = fuseJson(objectValues);
    else if (arrayValues.length >= 2) {
      // Union of arrays, de-duplicated while preserving order.
      const seen = new Set();
      const union = [];
      for (const item of arrayValues.flat()) {
        const fingerprint = typeof item === 'object' ? JSON.stringify(item) : String(item);
        if (seen.has(fingerprint)) continue;
        seen.add(fingerprint);
        union.push(item);
      }
      merged[key] = union;
    } else merged[key] = reduceValues(values);
  }
  return merged;
}

/** Pick the draft with the highest mean similarity to all other drafts. */
export function fuseText(texts) {
  const drafts = texts.filter((text) => typeof text === 'string' && text.trim().length);
  if (!drafts.length) return { text: '', agreement: 0, index: -1 };
  if (drafts.length === 1) return { text: drafts[0], agreement: 1, index: 0 };
  let bestIndex = 0;
  let bestScore = -1;
  const scores = [];
  for (let i = 0; i < drafts.length; i += 1) {
    let sum = 0;
    for (let j = 0; j < drafts.length; j += 1) {
      if (i === j) continue;
      sum += similarity(drafts[i], drafts[j]);
    }
    const score = sum / (drafts.length - 1);
    scores.push(score);
    if (score > bestScore) {
      bestScore = score;
      bestIndex = i;
    }
  }
  return { text: drafts[bestIndex], agreement: round(bestScore, 4), index: bestIndex, scores };
}

export class FusionEngine {
  constructor({ fallbackEngine, policyStore, config }) {
    this.fallbackEngine = fallbackEngine;
    this.policyStore = policyStore;
    this.config = config;
  }

  /**
   * Run the prompt across up to `count` distinct providers and fuse the result.
   * Falls back transparently to a single completion when fan-out is impossible.
   */
  async execute(request, { count = 3 } = {}) {
    const plan = this.fallbackEngine.router.plan(request);
    // One candidate per provider family keeps the vote genuinely independent.
    const perProvider = new Map();
    for (const key of plan.candidates) {
      const policy = this.policyStore.get(key);
      if (!policy || policy.provider === 'mock') continue;
      if (!perProvider.has(policy.provider)) perProvider.set(policy.provider, key);
    }
    const chosen = [...perProvider.values()].slice(0, Math.max(2, count));

    if (chosen.length < 2) {
      const single = await this.fallbackEngine.execute(request);
      return { ...single, fusion: { enabled: false, reason: 'insufficient-providers', members: 1 } };
    }

    const settled = await Promise.allSettled(
      chosen.map((model) => this.fallbackEngine.execute({ ...request, model })),
    );
    const successes = settled.filter((entry) => entry.status === 'fulfilled').map((entry) => entry.value);

    if (!successes.length) {
      // Re-run through the normal chain so the caller gets the canonical error.
      return this.fallbackEngine.execute(request);
    }
    if (successes.length === 1) {
      return { ...successes[0], fusion: { enabled: false, reason: 'single-success', members: 1 } };
    }

    const usage = successes.reduce((sum, entry) => ({
      promptTokens: sum.promptTokens + entry.usage.promptTokens,
      completionTokens: sum.completionTokens + entry.usage.completionTokens,
      totalTokens: sum.totalTokens + entry.usage.totalTokens,
    }), { promptTokens: 0, completionTokens: 0, totalTokens: 0 });

    const members = successes.map((entry) => ({
      model: entry.model, provider: entry.provider, latencyMs: entry.latencyMs,
    }));

    if (request.jsonMode) {
      const parsed = successes.map((entry) => extractJson(entry.text, null)).filter(Boolean);
      const fused = fuseJson(parsed);
      if (fused) {
        return {
          ...successes[0],
          text: JSON.stringify(fused, null, 2),
          usage,
          fusion: { enabled: true, strategy: 'json-majority', members, count: successes.length },
        };
      }
    }

    const { text, agreement, index } = fuseText(successes.map((entry) => entry.text));
    const winner = successes[index] ?? successes[0];
    return {
      ...winner,
      text,
      usage,
      fusion: { enabled: true, strategy: 'consensus-centroid', agreement, members, count: successes.length },
    };
  }
}

export default FusionEngine;
