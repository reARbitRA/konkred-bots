/**
 * OpenAI-compatible provider adapter.
 *
 * Covers Groq, Cerebras, Mistral, OpenRouter and GitHub Models - they all speak
 * `POST {baseUrl}/chat/completions` with a Bearer token, differing only in
 * auxiliary headers and a few payload quirks handled by `variant`.
 */
import { BaseProvider, ERROR_CLASS, classifyHttpError, classifyTransportError, isSafetyFinishReason, parseRetryAfter } from './base.mjs';
import { fetchWithTimeout, truncate } from '../util.mjs';

const MULTIMODAL_PROVIDERS = new Set(['openrouter', 'github', 'mistral', 'groq']);

export class OpenAICompatProvider extends BaseProvider {
  constructor({ name, providerConfig, timeoutMs, variant }) {
    super({ name, providerConfig, timeoutMs });
    this.variant = variant ?? name;
  }

  /** Provider-specific auth + telemetry headers. */
  buildHeaders(credential) {
    const headers = {
      'content-type': 'application/json',
      authorization: `Bearer ${credential}`,
      'user-agent': 'konkred-gateway/1.0',
      accept: 'application/json',
    };
    if (this.variant === 'openrouter') {
      headers['http-referer'] = this.providerConfig.referer ?? 'https://github.com/reARbitRA/konkred-bots';
      headers['x-title'] = this.providerConfig.title ?? 'Konkred Gateway';
    }
    if (this.variant === 'github') {
      headers['api-version'] = '2024-12-01-preview';
      headers['x-ms-useragent'] = 'konkred-gateway/1.0';
    }
    return headers;
  }

  buildPayload({ policy, messages, system, temperature, maxTokens, jsonMode, stopSequences }) {
    const supportsMultimodal = MULTIMODAL_PROVIDERS.has(this.variant)
      && policy.capabilities.includes('vision');
    const payload = {
      model: policy.upstreamModel,
      messages: BaseProvider.toPlainMessages(messages, { system, supportsMultimodal }),
      temperature: Number.isFinite(temperature) ? temperature : 0.3,
      stream: false,
    };

    const cap = Math.min(Number(maxTokens) || 2048, policy.maxOutputTokens || 4096);
    // Groq/Cerebras accept both, OpenRouter prefers max_tokens, GitHub Models
    // rejects max_completion_tokens on some deployments.
    if (this.variant === 'groq') payload.max_completion_tokens = cap;
    else payload.max_tokens = cap;

    if (jsonMode) payload.response_format = { type: 'json_object' };
    if (Array.isArray(stopSequences) && stopSequences.length) payload.stop = stopSequences.slice(0, 4);
    if (this.variant === 'groq' && /gpt-oss|qwen3/.test(policy.upstreamModel)) {
      payload.reasoning_effort = 'medium';
    }
    if (this.variant === 'cerebras') {
      // Cerebras rejects unknown fields strictly; keep the payload minimal.
      delete payload.stream;
    }
    return payload;
  }

  async complete(request) {
    const { policy, credential, messages, signal } = request;
    const url = `${this.baseUrl}/chat/completions`;
    const payload = this.buildPayload(request);

    let response;
    try {
      response = await fetchWithTimeout(url, {
        method: 'POST',
        headers: this.buildHeaders(credential),
        body: JSON.stringify(payload),
        signal,
      }, this.timeoutMs);
    } catch (error) {
      throw classifyTransportError(error, { provider: this.name, model: policy.key });
    }

    const rawText = await response.text();
    if (!response.ok) {
      const errorClass = classifyHttpError(response.status, rawText);
      let message = `${this.name} ${response.status}`;
      try {
        const parsed = JSON.parse(rawText);
        message = parsed?.error?.message ?? parsed?.message ?? parsed?.detail ?? message;
      } catch { /* keep default */ }
      const retryAfterMs = parseRetryAfter(
        response.headers.get('retry-after')
        ?? response.headers.get('x-ratelimit-reset-requests')
        ?? response.headers.get('x-ratelimit-reset-tokens'),
      );
      throw this.error(truncate(message, 400), {
        errorClass,
        status: response.status,
        model: policy.key,
        retryAfterMs,
        details: truncate(rawText, 600),
      });
    }

    let body;
    try {
      body = JSON.parse(rawText);
    } catch (error) {
      throw this.error(`${this.name} returned non-JSON payload`, {
        errorClass: ERROR_CLASS.SERVER, status: response.status, model: policy.key, cause: error,
      });
    }

    const choice = body?.choices?.[0];
    const finishReason = choice?.finish_reason ?? choice?.finishReason ?? 'stop';
    const rawContent = choice?.message?.content;
    let text = '';
    if (typeof rawContent === 'string') text = rawContent;
    else if (Array.isArray(rawContent)) {
      text = rawContent.map((part) => (typeof part === 'string' ? part : part?.text ?? '')).join('');
    }
    text = String(text ?? '').trim();

    // Reasoning models may place the answer in `reasoning`/`reasoning_content`
    // when the content channel ends up empty.
    if (!text) {
      const reasoning = choice?.message?.reasoning ?? choice?.message?.reasoning_content;
      if (typeof reasoning === 'string' && reasoning.trim()) text = reasoning.trim();
    }

    if (!text) {
      if (isSafetyFinishReason(finishReason)) {
        throw this.error(`${this.name} blocked the completion (finish_reason=${finishReason})`, {
          errorClass: ERROR_CLASS.SAFETY_BLOCK, status: 200, model: policy.key,
        });
      }
      if (finishReason === 'length') {
        throw this.error(`${this.name} hit the output limit before emitting text`, {
          errorClass: ERROR_CLASS.CONTEXT_LENGTH, status: 200, model: policy.key,
        });
      }
      throw this.error(`${this.name} returned an empty completion (finish_reason=${finishReason})`, {
        errorClass: ERROR_CLASS.EMPTY, status: 200, model: policy.key,
      });
    }

    return {
      text,
      finishReason,
      usage: BaseProvider.normalizeUsage(body?.usage, { messages, text }),
      raw: body,
    };
  }
}

export default OpenAICompatProvider;
