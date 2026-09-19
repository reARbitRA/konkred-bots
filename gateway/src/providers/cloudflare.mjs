/**
 * Cloudflare Workers AI provider adapter.
 *
 * Endpoint: POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}
 * The response envelope is `{ success, result: { response, usage }, errors: [] }`.
 */
import { BaseProvider, ERROR_CLASS, classifyHttpError, classifyTransportError, parseRetryAfter } from './base.mjs';
import { fetchWithTimeout, truncate } from '../util.mjs';

export class CloudflareProvider extends BaseProvider {
  constructor(options) {
    super({ name: 'cloudflare', ...options });
  }

  get accountId() {
    return this.providerConfig.accountId ?? '';
  }

  async complete(request) {
    const {
      policy, credential, messages, system, temperature, maxTokens, signal,
    } = request;

    if (!this.accountId) {
      throw this.error('CF_ACCOUNT_ID is not configured', {
        errorClass: ERROR_CLASS.AUTH, status: 0, model: policy.key, retryable: false,
      });
    }

    const url = `${this.baseUrl}/accounts/${encodeURIComponent(this.accountId)}/ai/run/${policy.upstreamModel}`;
    const payload = {
      messages: BaseProvider.toPlainMessages(messages, { system, supportsMultimodal: false }),
      temperature: Number.isFinite(temperature) ? temperature : 0.3,
      max_tokens: Math.min(Number(maxTokens) || 1024, policy.maxOutputTokens || 4096),
      stream: false,
    };

    let response;
    try {
      response = await fetchWithTimeout(url, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          authorization: `Bearer ${credential}`,
          'user-agent': 'konkred-gateway/1.0',
        },
        body: JSON.stringify(payload),
        signal,
      }, this.timeoutMs);
    } catch (error) {
      throw classifyTransportError(error, { provider: this.name, model: policy.key });
    }

    const rawText = await response.text();
    if (!response.ok) {
      let message = `Cloudflare ${response.status}`;
      try {
        const parsed = JSON.parse(rawText);
        message = parsed?.errors?.[0]?.message ?? parsed?.messages?.[0]?.message ?? message;
      } catch { /* keep default */ }
      throw this.error(truncate(message, 400), {
        errorClass: classifyHttpError(response.status, rawText),
        status: response.status,
        model: policy.key,
        retryAfterMs: parseRetryAfter(response.headers.get('retry-after')),
        details: truncate(rawText, 600),
      });
    }

    let body;
    try {
      body = JSON.parse(rawText);
    } catch (error) {
      throw this.error('Cloudflare returned non-JSON payload', {
        errorClass: ERROR_CLASS.SERVER, status: response.status, model: policy.key, cause: error,
      });
    }

    if (body?.success === false) {
      const message = body?.errors?.[0]?.message ?? 'Cloudflare reported success=false';
      const code = Number(body?.errors?.[0]?.code ?? 0);
      throw this.error(truncate(message, 400), {
        errorClass: code === 10000 ? ERROR_CLASS.AUTH : ERROR_CLASS.SERVER,
        status: 200,
        model: policy.key,
      });
    }

    const result = body?.result ?? {};
    let text = '';
    if (typeof result.response === 'string') text = result.response;
    else if (typeof result.result === 'string') text = result.result;
    else if (Array.isArray(result.choices)) text = result.choices[0]?.message?.content ?? '';
    text = String(text ?? '').trim();

    if (!text) {
      throw this.error('Cloudflare returned an empty completion', {
        errorClass: ERROR_CLASS.EMPTY, status: 200, model: policy.key,
      });
    }

    const usage = result.usage ?? {};
    return {
      text,
      finishReason: 'stop',
      usage: BaseProvider.normalizeUsage(usage, { messages, text }),
      raw: body,
    };
  }
}

export default CloudflareProvider;
