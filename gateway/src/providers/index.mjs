/**
 * Provider registry: maps a provider id to a live adapter instance.
 */
import { CONFIG } from '../config.mjs';
import { BaseProvider, ERROR_CLASS, ProviderError, TERMINAL_ERROR_CLASSES, classifyHttpError, classifyTransportError, isSafetyFinishReason, parseRetryAfter } from './base.mjs';
import { GeminiProvider } from './gemini.mjs';
import { OpenAICompatProvider } from './openai-compat.mjs';
import { CloudflareProvider } from './cloudflare.mjs';
import { MockProvider } from './mock.mjs';

export { BaseProvider, ProviderError, ERROR_CLASS, TERMINAL_ERROR_CLASSES, classifyHttpError, classifyTransportError, isSafetyFinishReason, parseRetryAfter };
export { GeminiProvider, OpenAICompatProvider, CloudflareProvider, MockProvider };

/**
 * Build the provider map from configuration.
 * @param {object} config CONFIG-shaped object (injectable for tests)
 * @returns {Map<string, BaseProvider>}
 */
export function createProviders(config = CONFIG) {
  const timeoutMs = config.upstreamTimeoutMs ?? 90000;
  const providers = new Map();

  providers.set('gemini', new GeminiProvider({ providerConfig: config.providers.gemini, timeoutMs }));
  providers.set('cloudflare', new CloudflareProvider({ providerConfig: config.providers.cloudflare, timeoutMs }));
  providers.set('mock', new MockProvider({ timeoutMs }));

  for (const name of ['groq', 'cerebras', 'mistral', 'openrouter', 'github']) {
    providers.set(name, new OpenAICompatProvider({
      name,
      variant: name,
      providerConfig: config.providers[name],
      timeoutMs,
    }));
  }

  return providers;
}

export default createProviders;
