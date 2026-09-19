/**
 * Google AI Studio (Gemini) provider adapter.
 *
 * Uses the v1beta `generateContent` REST endpoint. Native multimodal support:
 * audio (voice bot), documents (pdf bot) and images are forwarded as
 * `inlineData` parts, which is what makes Gemini Flash the primary slot for the
 * transcription and long-document task routes.
 */
import { BaseProvider, ERROR_CLASS, classifyHttpError, classifyTransportError, isSafetyFinishReason, parseRetryAfter } from './base.mjs';
import { fetchWithTimeout, truncate } from '../util.mjs';

/** Convert canonical messages into Gemini `contents` + `systemInstruction`. */
export function toGeminiPayload(messages, { system, temperature, maxTokens, jsonMode, stopSequences, maxOutputCap }) {
  const contents = [];
  const systemParts = [];
  if (system) systemParts.push({ text: String(system) });

  for (const message of messages ?? []) {
    const role = message.role === 'assistant' ? 'model' : 'user';
    const parts = [];
    const content = message.content;

    if (message.role === 'system') {
      systemParts.push({ text: typeof content === 'string' ? content : JSON.stringify(content) });
      continue;
    }
    if (typeof content === 'string') {
      if (content.length) parts.push({ text: content });
    } else if (Array.isArray(content)) {
      for (const part of content) {
        if (typeof part === 'string') {
          if (part.length) parts.push({ text: part });
        } else if (part?.type === 'text' && typeof part.text === 'string') {
          parts.push({ text: part.text });
        } else if (part?.inlineData || part?.inline_data || part?.type === 'inline_data') {
          const inline = part.inlineData ?? part.inline_data ?? part;
          const mimeType = inline.mimeType ?? inline.mime_type;
          const data = inline.data;
          if (mimeType && data) parts.push({ inlineData: { mimeType, data } });
        } else if (part?.type === 'image_url' && part.image_url?.url) {
          const match = String(part.image_url.url).match(/^data:([^;]+);base64,(.+)$/);
          if (match) parts.push({ inlineData: { mimeType: match[1], data: match[2] } });
          else parts.push({ fileData: { fileUri: part.image_url.url } });
        } else if (part?.fileData || part?.file_data) {
          parts.push({ fileData: part.fileData ?? part.file_data });
        } else if (typeof part?.text === 'string') {
          parts.push({ text: part.text });
        }
      }
    } else if (content != null) {
      parts.push({ text: String(content) });
    }

    if (!parts.length) continue;
    const previous = contents[contents.length - 1];
    if (previous && previous.role === role) previous.parts.push(...parts);
    else contents.push({ role, parts });
  }

  if (!contents.length) contents.push({ role: 'user', parts: [{ text: '(empty request)' }] });

  const generationConfig = {
    temperature: Number.isFinite(temperature) ? temperature : 0.3,
    maxOutputTokens: Math.min(Number(maxTokens) || 2048, Number(maxOutputCap) || 65536),
  };
  if (jsonMode) generationConfig.responseMimeType = 'application/json';
  if (Array.isArray(stopSequences) && stopSequences.length) {
    generationConfig.stopSequences = stopSequences.slice(0, 5);
  }

  const payload = { contents, generationConfig };
  if (systemParts.length) payload.systemInstruction = { role: 'user', parts: systemParts };
  // Free-tier safety: keep the default thresholds but never hard-block on
  // civic/medical categories that routinely trip on legitimate business docs.
  payload.safetySettings = [
    'HARM_CATEGORY_HARASSMENT',
    'HARM_CATEGORY_HATE_SPEECH',
    'HARM_CATEGORY_SEXUALLY_EXPLICIT',
    'HARM_CATEGORY_DANGEROUS_CONTENT',
  ].map((category) => ({ category, threshold: 'BLOCK_ONLY_HIGH' }));
  return payload;
}

/** Concatenate all text parts of the first candidate. */
export function extractGeminiText(body) {
  const candidate = body?.candidates?.[0];
  const parts = candidate?.content?.parts ?? [];
  return parts
    .map((part) => (typeof part?.text === 'string' ? part.text : ''))
    .filter(Boolean)
    .join('')
    .trim();
}

export class GeminiProvider extends BaseProvider {
  constructor(options) {
    super({ name: 'gemini', ...options });
  }

  async complete(request) {
    const {
      policy, credential, messages, system, temperature, maxTokens, jsonMode, stopSequences, signal,
    } = request;
    const url = `${this.baseUrl}/models/${encodeURIComponent(policy.upstreamModel)}:generateContent`;
    const payload = toGeminiPayload(messages, {
      system,
      temperature,
      maxTokens,
      jsonMode,
      stopSequences,
      maxOutputCap: policy.maxOutputTokens,
    });

    let response;
    try {
      response = await fetchWithTimeout(url, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'x-goog-api-key': credential,
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
      const errorClass = classifyHttpError(response.status, rawText);
      let message = `Gemini ${response.status}`;
      try {
        const parsed = JSON.parse(rawText);
        message = parsed?.error?.message ?? message;
      } catch { /* keep default */ }
      throw this.error(truncate(message, 400), {
        errorClass,
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
      throw this.error('Gemini returned non-JSON payload', {
        errorClass: ERROR_CLASS.SERVER, status: response.status, model: policy.key, cause: error,
      });
    }

    const promptBlock = body?.promptFeedback?.blockReason;
    if (promptBlock) {
      throw this.error(`Gemini blocked the prompt (${promptBlock})`, {
        errorClass: ERROR_CLASS.SAFETY_BLOCK, status: 200, model: policy.key, retryable: true,
      });
    }

    const candidate = body?.candidates?.[0];
    const finishReason = candidate?.finishReason ?? 'STOP';
    const text = extractGeminiText(body);

    if (!text) {
      if (isSafetyFinishReason(finishReason)) {
        throw this.error(`Gemini returned an empty completion (finishReason=${finishReason})`, {
          errorClass: ERROR_CLASS.SAFETY_BLOCK, status: 200, model: policy.key,
        });
      }
      if (finishReason === 'MAX_TOKENS') {
        throw this.error('Gemini hit MAX_TOKENS before emitting any text', {
          errorClass: ERROR_CLASS.CONTEXT_LENGTH, status: 200, model: policy.key,
        });
      }
      throw this.error(`Gemini returned an empty completion (finishReason=${finishReason})`, {
        errorClass: ERROR_CLASS.EMPTY, status: 200, model: policy.key,
      });
    }

    return {
      text,
      finishReason,
      usage: BaseProvider.normalizeUsage(body?.usageMetadata, { messages, text }),
      raw: body,
    };
  }
}

export default GeminiProvider;
