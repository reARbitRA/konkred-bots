/**
 * Konkred AI Gateway - task-aware smart router.
 *
 * Maps a logical TASK_TYPE onto a prioritised candidate model list, then
 * filters that list by:
 *   1. availability (provider enabled + credential present)
 *   2. capability requirements derived from the payload (audio/vision/document)
 *   3. context-window validation against the estimated prompt+output tokens
 *
 * The ordered result is handed to the fallback engine, which walks it until one
 * slot produces a completion.
 */
import { CONFIG } from '../config.mjs';
import { estimateMessageTokens, createLogger } from '../util.mjs';

const log = createLogger('router');

export const TASK_TYPES = Object.freeze([
  'general',
  'code-generation',
  'spec-generation',
  'summarization',
  'classification',
  'bug-fixing',
  'architecture',
  'translate',
]);

/**
 * Prioritised candidate chains per task.
 * Ordering rationale:
 *  - `summarization` / `spec-generation` lead with Gemini (1M context + native
 *    audio/PDF understanding) because the voice and pdf bots depend on it.
 *  - `classification` leads with the turbo-latency Cerebras/Groq slots.
 *  - `code-generation` leads with reasoning-grade OSS models then Codestral.
 */
export const TASK_ROUTES = Object.freeze({
  general: [
    'groq:llama-70b',
    'cerebras:gpt-oss-120b',
    'gemini:flash',
    'groq:gpt-oss-120b',
    'cerebras:qwen3-235b',
    'mistral:small',
    'github:gpt-4o-mini',
    'openrouter:free-auto',
    'groq:llama-8b',
    'cloudflare:llama-8b',
    'gemini:flash-lite',
    'mock:general',
  ],
  'code-generation': [
    'cerebras:gpt-oss-120b',
    'groq:gpt-oss-120b',
    'mistral:codestral',
    'cerebras:qwen3-235b',
    'groq:qwen3-32b',
    'gemini:flash',
    'github:gpt-4o',
    'groq:kimi-k2',
    'openrouter:free-auto',
    'mock:general',
  ],
  'spec-generation': [
    'gemini:flash',
    'groq:kimi-k2',
    'cerebras:qwen3-235b',
    'groq:gpt-oss-120b',
    'gemini:flash-lite',
    'mistral:small',
    'github:gpt-4o',
    'openrouter:free-auto',
    'mock:general',
  ],
  summarization: [
    'gemini:flash',
    'gemini:flash-lite',
    'groq:kimi-k2',
    'groq:llama-70b',
    'cerebras:qwen3-235b',
    'mistral:small',
    'github:gpt-4o-mini',
    'groq:llama-8b',
    'mock:general',
  ],
  classification: [
    'cerebras:llama-8b',
    'groq:llama-8b',
    'groq:qwen3-32b',
    'cerebras:gpt-oss-120b',
    'gemini:flash-lite',
    'cloudflare:llama-8b',
    'groq:llama-70b',
    'github:gpt-4o-mini',
    'mock:fast',
  ],
  'bug-fixing': [
    'groq:gpt-oss-120b',
    'cerebras:gpt-oss-120b',
    'mistral:codestral',
    'gemini:flash',
    'cerebras:qwen3-235b',
    'groq:qwen3-32b',
    'github:gpt-4o',
    'mock:general',
  ],
  architecture: [
    'cerebras:qwen3-235b',
    'gemini:flash',
    'groq:kimi-k2',
    'groq:gpt-oss-120b',
    'github:gpt-4o',
    'cerebras:gpt-oss-120b',
    'mistral:small',
    'mock:general',
  ],
  translate: [
    'gemini:flash-lite',
    'groq:llama-70b',
    'mistral:small',
    'gemini:flash',
    'cerebras:llama-8b',
    'groq:llama-8b',
    'cloudflare:llama-8b',
    'mock:fast',
  ],
});

/** Normalise an arbitrary task string to a supported TASK_TYPE. */
export function normalizeTaskType(taskType) {
  const value = String(taskType ?? 'general').trim().toLowerCase();
  if (TASK_TYPES.includes(value)) return value;
  const aliases = {
    chat: 'general',
    qa: 'general',
    code: 'code-generation',
    coding: 'code-generation',
    codegen: 'code-generation',
    spec: 'spec-generation',
    document: 'spec-generation',
    docs: 'spec-generation',
    summary: 'summarization',
    summarize: 'summarization',
    transcription: 'summarization',
    sentiment: 'classification',
    classify: 'classification',
    debug: 'bug-fixing',
    fix: 'bug-fixing',
    design: 'architecture',
    translation: 'translate',
  };
  return aliases[value] ?? 'general';
}

/** Detect the capabilities a payload requires (audio, vision, document). */
export function requiredCapabilities(messages) {
  const required = new Set();
  for (const message of messages ?? []) {
    const content = message?.content;
    if (!Array.isArray(content)) continue;
    for (const part of content) {
      const inline = part?.inlineData ?? part?.inline_data;
      const mime = String(inline?.mimeType ?? inline?.mime_type ?? '').toLowerCase();
      if (mime.startsWith('audio/')) required.add('audio');
      else if (mime.startsWith('image/')) required.add('vision');
      else if (mime === 'application/pdf' || mime.startsWith('application/')) required.add('document');
      else if (part?.type === 'image_url') required.add('vision');
      else if (part?.type === 'input_audio') required.add('audio');
    }
  }
  return [...required];
}

export class SmartRouter {
  constructor({ policyStore, config = CONFIG } = {}) {
    if (!policyStore) throw new Error('SmartRouter requires a policyStore');
    this.policyStore = policyStore;
    this.config = config;
    /** @type {Map<string, number>} model key -> benched-until epoch ms */
    this.bench = new Map();
    this.routes = { ...TASK_ROUTES };
  }

  /** Temporarily remove a model from routing (safety blocks, hard errors). */
  benchModel(modelKey, durationMs, reason = 'unspecified') {
    const until = Date.now() + Math.max(1000, durationMs);
    if ((this.bench.get(modelKey) ?? 0) < until) {
      this.bench.set(modelKey, until);
      log.warn('model benched', { model: modelKey, ms: Math.round(durationMs), reason });
    }
    return until;
  }

  isBenched(modelKey, nowMs = Date.now()) {
    const until = this.bench.get(modelKey) ?? 0;
    if (until <= nowMs) {
      if (until) this.bench.delete(modelKey);
      return false;
    }
    return true;
  }

  clearBench() {
    this.bench.clear();
  }

  /** Candidate chain for a task, before filtering. */
  candidatesFor(taskType) {
    const task = normalizeTaskType(taskType);
    const chain = [...(this.routes[task] ?? this.routes.general)];
    // Append every other available model as a last-resort tail so an unusual
    // capability requirement can still find a home.
    for (const key of this.policyStore.availableKeys()) {
      if (!chain.includes(key)) chain.push(key);
    }
    return chain;
  }

  /**
   * Resolve the ordered, filtered candidate plan for a request.
   *
   * @param {object} request
   * @param {string} request.taskType
   * @param {Array}  request.messages
   * @param {string} [request.model]        explicit model pin
   * @param {number} [request.maxTokens]
   * @param {number} [request.minContext]   raised by the fallback engine after a context_length error
   * @returns {{task:string, estimatedTokens:number, requiredContext:number, capabilities:string[], candidates:string[], rejected:Array}}
   */
  plan({ taskType, messages, model, maxTokens = 2048, minContext = 0 }) {
    const task = normalizeTaskType(taskType);
    const estimatedTokens = estimateMessageTokens(messages);
    const requiredContext = Math.max(
      minContext,
      Math.ceil(estimatedTokens * 1.15) + Number(maxTokens || 0) + 512,
    );
    const capabilities = requiredCapabilities(messages);
    const nowMs = Date.now();
    const rejected = [];

    const ordered = model && this.policyStore.has(model)
      ? [model, ...this.candidatesFor(task).filter((key) => key !== model)]
      : this.candidatesFor(task);

    const candidates = [];
    for (const key of ordered) {
      const policy = this.policyStore.get(key);
      if (!policy) {
        rejected.push({ model: key, reason: 'unknown-model' });
        continue;
      }
      if (!this.policyStore.isAvailable(key)) {
        rejected.push({ model: key, reason: 'no-credentials' });
        continue;
      }
      if (this.isBenched(key, nowMs)) {
        rejected.push({ model: key, reason: 'benched' });
        continue;
      }
      const missing = capabilities.filter((capability) => !policy.capabilities.includes(capability));
      if (missing.length) {
        rejected.push({ model: key, reason: `missing-capability:${missing.join('+')}` });
        continue;
      }
      if (policy.contextWindow < requiredContext) {
        rejected.push({
          model: key,
          reason: `context-too-small:${policy.contextWindow}<${requiredContext}`,
        });
        continue;
      }
      candidates.push(key);
    }

    // Explicit pins must stay first; otherwise keep the curated task order but
    // push the deterministic mock slots to the very end.
    const isMock = (key) => this.policyStore.get(key)?.provider === 'mock';
    const nonMock = candidates.filter((key) => !isMock(key));
    const mocks = candidates.filter(isMock);
    const finalCandidates = [...nonMock, ...mocks];

    return {
      task,
      estimatedTokens,
      requiredContext,
      capabilities,
      candidates: finalCandidates,
      rejected,
    };
  }

  snapshot(nowMs = Date.now()) {
    return {
      tasks: TASK_TYPES,
      benched: [...this.bench.entries()]
        .filter(([, until]) => until > nowMs)
        .map(([model, until]) => ({ model, msRemaining: until - nowMs })),
      routes: Object.fromEntries(
        Object.entries(this.routes).map(([task, chain]) => [
          task,
          chain.filter((key) => this.policyStore.isAvailable(key)),
        ]),
      ),
    };
  }
}

export default SmartRouter;
