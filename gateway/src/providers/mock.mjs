/**
 * Built-in deterministic mock provider.
 *
 * This is NOT a placeholder: it is the last link of the fallback chain and the
 * engine used by `MOCK_ONLY=true` for CI, offline development and smoke tests.
 * It produces useful, task-shaped output (valid JSON when JSON mode is on) so
 * the Telegram bots stay functional even with zero upstream credentials.
 */
import { BaseProvider, ERROR_CLASS } from './base.mjs';
import { estimateMessageTokens, estimateTokens, sha256, truncate } from '../util.mjs';

/** Flatten any canonical message array down to plain text. */
function flatten(messages) {
  const lines = [];
  for (const message of messages ?? []) {
    const content = message?.content;
    if (typeof content === 'string') lines.push(content);
    else if (Array.isArray(content)) {
      for (const part of content) {
        if (typeof part === 'string') lines.push(part);
        else if (typeof part?.text === 'string') lines.push(part.text);
        else if (part?.inlineData || part?.inline_data) {
          const inline = part.inlineData ?? part.inline_data;
          lines.push(`[attachment:${inline.mimeType ?? inline.mime_type ?? 'binary'}]`);
        }
      }
    } else if (content != null) lines.push(String(content));
  }
  return lines.join('\n').trim();
}

function firstSentences(text, count = 3) {
  const sentences = String(text)
    .replace(/\s+/g, ' ')
    .split(/(?<=[.!?])\s+/)
    .map((sentence) => sentence.trim())
    .filter(Boolean);
  if (!sentences.length) return 'No substantive content was supplied in the prompt.';
  return sentences.slice(0, count).join(' ');
}

function keywords(text, limit = 6) {
  const stop = new Set(['the', 'and', 'for', 'with', 'that', 'this', 'from', 'your', 'you', 'are', 'was', 'were',
    'has', 'have', 'will', 'into', 'about', 'their', 'they', 'them', 'our', 'out', 'not', 'but', 'can', 'all',
    'any', 'per', 'via', 'use', 'used', 'using', 'each', 'when', 'what', 'which', 'who', 'how', 'its']);
  const counts = new Map();
  for (const word of String(text).toLowerCase().match(/[a-z][a-z0-9'-]{2,}/g) ?? []) {
    if (stop.has(word)) continue;
    counts.set(word, (counts.get(word) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([word]) => word);
}

/** Build a structured JSON answer that satisfies jsonMode consumers. */
function mockJson(taskType, prompt) {
  const topics = keywords(prompt, 5);
  const base = {
    mock: true,
    taskType,
    generatedAt: new Date().toISOString(),
    summary: firstSentences(prompt, 2),
    keyTopics: topics,
  };
  switch (taskType) {
    case 'classification':
      return {
        ...base,
        sentiment: 'Neutral',
        score: 50,
        confidence: 0.35,
        drivers: topics.slice(0, 3).map((topic) => `Signal detected around "${topic}" (offline heuristic).`),
        riskWarning: 'Offline mock analysis - not investment advice and not based on live market data.',
      };
    case 'code-generation':
      return {
        ...base,
        hooks: [
          { style: 'Psychological', text: `Most people get "${topics[0] ?? 'this'}" completely backwards.` },
          { style: 'Curiosity-Gap', text: `The one ${topics[0] ?? 'detail'} nobody tells you about.` },
          { style: 'Contrarian', text: `Stop optimising ${topics[0] ?? 'it'} - do the opposite instead.` },
        ],
        script: [
          { t: '0-3s', visual: 'Tight face-cam, hard cut in', audio: 'Hook line, no intro music' },
          { t: '3-12s', visual: 'B-roll of the problem', audio: 'Name the pain point precisely' },
          { t: '12-24s', visual: 'Screen capture of the fix', audio: 'Three-step payoff' },
          { t: '24-30s', visual: 'Text card CTA', audio: 'Ask for the save/share' },
        ],
        caption: `${firstSentences(prompt, 1)} #offlinemode #konkred #mock`,
      };
    case 'spec-generation':
      return {
        ...base,
        sections: [
          { title: 'Overview', body: firstSentences(prompt, 3) },
          { title: 'Key Points', body: topics.map((topic) => `- ${topic}`).join('\n') || '- (no salient terms)' },
          { title: 'Risks', body: 'Generated offline: verify against the source document before acting.' },
        ],
      };
    default:
      return { ...base, answer: firstSentences(prompt, 4) };
  }
}

/** Build a readable markdown answer per task type. */
function mockText(taskType, prompt) {
  const topics = keywords(prompt, 6);
  const topicList = topics.length ? topics.map((topic) => `- ${topic}`).join('\n') : '- (no salient terms detected)';
  const header = '🧪 *Offline mock response* — the gateway has no upstream credentials available, '
    + 'so this answer was generated locally and is not model output.';

  switch (taskType) {
    case 'summarization':
      return `${header}

**Transcript (best effort)**
${truncate(prompt, 800) || '(no text content supplied)'}

**Summary**
${firstSentences(prompt, 3)}

**Action Items / Decisions**
${topicList}`;
    case 'classification':
      return `${header}

**Sentiment:** Neutral (50/100)
**Key Drivers**
${topicList}

**Whale Activity:** No on-chain data available offline.
**Risk Warning:** Offline heuristic output. Not financial advice.`;
    case 'bug-fixing':
      return `${header}

**Diagnosis**
${firstSentences(prompt, 2)}

**Suggested fix**
1. Reproduce the failure with a minimal case.
2. Add assertions around ${topics[0] ?? 'the failing call'}.
3. Re-run the suite and confirm the regression is covered.`;
    case 'translate':
      return `${header}

**Translation unavailable offline.** Original text preserved below:

${truncate(prompt, 1200)}`;
    default:
      return `${header}

**Answer**
${firstSentences(prompt, 4)}

**Salient topics**
${topicList}`;
  }
}

export class MockProvider extends BaseProvider {
  constructor(options = {}) {
    super({ name: 'mock', providerConfig: { baseUrl: 'local://mock' }, ...options });
  }

  async complete(request) {
    const { policy, messages, system, taskType = 'general', jsonMode, maxTokens } = request;
    const prompt = flatten([...(system ? [{ role: 'system', content: system }] : []), ...(messages ?? [])]);

    if (!prompt) {
      throw this.error('Mock provider received an empty prompt', {
        errorClass: ERROR_CLASS.BAD_REQUEST, status: 400, model: policy?.key ?? 'mock', retryable: false,
      });
    }

    // Simulate a tiny amount of latency so dedup/coalescing paths are exercised.
    // The timer is intentionally ref'ed: an unref()-ed timer would let the event
    // loop drain and leave this promise unsettled forever.
    await new Promise((resolve) => { setTimeout(resolve, 5); });

    const text = jsonMode
      ? JSON.stringify(mockJson(taskType, prompt), null, 2)
      : mockText(taskType, prompt);
    const capped = Number.isFinite(maxTokens) && maxTokens > 0
      ? text.slice(0, Math.max(200, Math.floor(maxTokens * 3.6)))
      : text;

    return {
      text: capped,
      finishReason: 'stop',
      usage: {
        promptTokens: estimateMessageTokens(messages ?? []),
        completionTokens: estimateTokens(capped),
        totalTokens: estimateMessageTokens(messages ?? []) + estimateTokens(capped),
      },
      raw: { mock: true, fingerprint: sha256(prompt).slice(0, 16), model: policy?.upstreamModel ?? 'konkred-mock' },
    };
  }
}

export default MockProvider;
