#!/usr/bin/env node
/**
 * Konkred AI Gateway - native node:http server.
 *
 * Routes
 *   POST /api/ai                  main inference endpoint
 *   GET  /api/health              liveness + capacity (used by the compose healthcheck)
 *   GET  /api/models              registry view with live availability
 *   GET  /api/meta                build/config metadata
 *   GET  /api/admin/dashboard     HTML ops console  (x-admin-key)
 *   GET  /api/admin/stats         JSON ops payload  (x-admin-key)
 *   POST /api/admin/reset         clear caches / benches / cooldowns (x-admin-key)
 *
 * Security
 *   - `x-admin-key` is compared with crypto.timingSafeEqual (see util.timingSafeEqualStr)
 *   - `authorization: Bearer <GATEWAY_API_KEY>` (or `x-api-key`) guards /api/ai
 *   - request bodies are hard-capped at MAX_BODY_BYTES and streamed, never buffered twice
 */
import http from 'node:http';
import { CONFIG, hasRealCredentials, providerSummary, loadedEnvFiles } from './config.mjs';
import policyStore from './policy-store.mjs';
import { Gateway, RateLimitError, ValidationError } from './gateway/gateway.mjs';
import { ExhaustedError } from './gateway/fallback.mjs';
import { QuotaWatchdog } from './watchdog.mjs';
import { renderDashboard } from './dashboard.mjs';
import { TASK_TYPES } from './gateway/router.mjs';
import { createLogger, requestId, timingSafeEqualStr } from './util.mjs';

const log = createLogger('server');

export const gateway = new Gateway({ config: CONFIG, policyStore });
export const watchdog = new QuotaWatchdog({ gateway, config: CONFIG });

const SERVER_START = Date.now();

/* ------------------------------------------------------------------ utils */

function send(res, status, payload, extraHeaders = {}) {
  const body = typeof payload === 'string' ? payload : JSON.stringify(payload);
  const headers = {
    'content-type': typeof payload === 'string' ? 'text/html; charset=utf-8' : 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
    'referrer-policy': 'no-referrer',
    ...extraHeaders,
  };
  res.writeHead(status, headers);
  res.end(body);
}

function sendError(res, status, code, message, extra = {}) {
  send(res, status, { error: { code, message, ...extra } });
}

/** Read and JSON-parse the request body with a hard size cap. */
function readJsonBody(req, maxBytes = CONFIG.maxBodyBytes) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    let settled = false;
    const fail = (error) => {
      if (settled) return;
      settled = true;
      req.destroy();
      reject(error);
    };
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > maxBytes) {
        const error = new Error(`Request body exceeds ${maxBytes} bytes`);
        error.status = 413;
        error.code = 'payload_too_large';
        fail(error);
        return;
      }
      chunks.push(chunk);
    });
    req.on('error', (error) => fail(error));
    req.on('end', () => {
      if (settled) return;
      settled = true;
      const raw = Buffer.concat(chunks).toString('utf8');
      if (!raw.trim()) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(raw));
      } catch (error) {
        const parseError = new Error(`Malformed JSON body: ${error.message}`);
        parseError.status = 400;
        parseError.code = 'invalid_json';
        reject(parseError);
      }
    });
  });
}

/** Constant-time admin authentication. */
function isAdmin(req) {
  if (!CONFIG.adminKey) return false;
  const provided = req.headers['x-admin-key']
    ?? (String(req.headers.authorization ?? '').startsWith('Bearer ')
      ? String(req.headers.authorization).slice(7)
      : '');
  return timingSafeEqualStr(provided, CONFIG.adminKey);
}

/** Client authentication for /api/ai. */
function isAuthorizedClient(req) {
  if (!CONFIG.requireAuth) return true;
  if (!CONFIG.gatewayApiKey) return true; // no key configured => open (compose-internal network)
  const header = String(req.headers.authorization ?? '');
  const bearer = header.toLowerCase().startsWith('bearer ') ? header.slice(7).trim() : '';
  const provided = bearer || String(req.headers['x-api-key'] ?? '');
  return timingSafeEqualStr(provided, CONFIG.gatewayApiKey) || isAdmin(req);
}

function clientIp(req) {
  const forwarded = String(req.headers['x-forwarded-for'] ?? '').split(',')[0].trim();
  return forwarded || req.socket?.remoteAddress || 'unknown';
}

/* ----------------------------------------------------------------- routes */

async function handleAi(req, res, id) {
  if (!isAuthorizedClient(req)) {
    sendError(res, 401, 'unauthorized', 'Missing or invalid gateway API key');
    return;
  }

  let body;
  try {
    body = await readJsonBody(req);
  } catch (error) {
    sendError(res, error.status ?? 400, error.code ?? 'invalid_request', error.message);
    return;
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error('gateway request timeout')), CONFIG.requestTimeoutMs);
  timer.unref?.();
  const onClose = () => controller.abort(new Error('client disconnected'));
  req.once('aborted', onClose);

  try {
    const result = await gateway.handle(body, { signal: controller.signal, requestId: id });
    send(res, 200, result, {
      'x-request-id': id,
      'x-model': String(result.model ?? ''),
      'x-provider': String(result.provider ?? ''),
      'x-cache': result.cached ? 'HIT' : 'MISS',
    });
  } catch (error) {
    if (error instanceof ValidationError) {
      sendError(res, error.status, error.code, error.message, { field: error.field, requestId: id });
      return;
    }
    if (error instanceof RateLimitError) {
      sendError(res, 429, error.code, error.message, {
        retryAfterMs: error.retryAfterMs, limits: error.limits, usage: error.usage, requestId: id,
      });
      res.setHeader?.('retry-after', Math.ceil(error.retryAfterMs / 1000));
      return;
    }
    if (error instanceof ExhaustedError) {
      sendError(res, error.status, error.code, error.message, {
        attempts: error.attempts,
        retryAfterMs: error.retryAfterMs,
        lastError: error.lastError ? error.lastError.toJSON?.() ?? String(error.lastError.message) : null,
        requestId: id,
      });
      return;
    }
    log.error('unhandled gateway error', { requestId: id, error: String(error?.stack ?? error) });
    sendError(res, 500, 'internal_error', 'The gateway encountered an unexpected error', { requestId: id });
  } finally {
    clearTimeout(timer);
    req.removeListener('aborted', onClose);
  }
}

function handleHealth(res) {
  const health = gateway.health();
  const status = health.status === 'unhealthy' ? 503 : 200;
  send(res, status, {
    ...health,
    watchdog: { running: watchdog.snapshot().running, ticks: watchdog.ticks },
    credentials: { real: hasRealCredentials(CONFIG), mockAllowed: CONFIG.allowMock },
    timestamp: new Date().toISOString(),
  });
}

function handleModels(res) {
  send(res, 200, {
    registryVersion: policyStore.version,
    tasks: TASK_TYPES,
    routes: gateway.router.snapshot().routes,
    models: policyStore.describe(),
  });
}

function handleMeta(res) {
  send(res, 200, {
    service: CONFIG.serviceName,
    version: '1.0.0',
    env: CONFIG.env,
    node: process.version,
    startedAt: new Date(SERVER_START).toISOString(),
    uptimeSeconds: Math.round((Date.now() - SERVER_START) / 1000),
    registryVersion: policyStore.version,
    envFilesLoaded: loadedEnvFiles,
    providers: providerSummary(CONFIG),
    features: {
      cache: CONFIG.cacheEnabled,
      dedup: CONFIG.dedupEnabled,
      fusion: CONFIG.fusionEnabled,
      dashboard: CONFIG.dashboardEnabled,
      watchdog: true,
      mockFallback: CONFIG.allowMock,
      mockOnly: CONFIG.mockOnly,
      authRequired: CONFIG.requireAuth && Boolean(CONFIG.gatewayApiKey),
    },
    limits: {
      maxBodyBytes: CONFIG.maxBodyBytes,
      requestTimeoutMs: CONFIG.requestTimeoutMs,
      upstreamTimeoutMs: CONFIG.upstreamTimeoutMs,
      maxAttempts: CONFIG.maxAttempts,
      userRpm: CONFIG.userRpm,
      userRpd: CONFIG.userRpd,
    },
    headroom: CONFIG.headroom,
  });
}

function handleDashboard(req, res) {
  if (!CONFIG.dashboardEnabled) {
    sendError(res, 404, 'not_found', 'Dashboard is disabled');
    return;
  }
  if (!isAdmin(req)) {
    send(res, 401, { error: { code: 'unauthorized', message: 'A valid x-admin-key header is required' } }, {
      'www-authenticate': 'Bearer realm="konkred-gateway-admin"',
    });
    return;
  }
  const html = renderDashboard({
    stats: gateway.stats(),
    watchdog: watchdog.snapshot(),
    providers: providerSummary(CONFIG),
    registryVersion: policyStore.version,
    env: CONFIG.env,
  });
  send(res, 200, html);
}

function handleAdminStats(req, res) {
  if (!isAdmin(req)) {
    sendError(res, 401, 'unauthorized', 'A valid x-admin-key header is required');
    return;
  }
  send(res, 200, {
    ...gateway.stats(),
    watchdog: watchdog.snapshot(),
    providers: providerSummary(CONFIG),
  });
}

function handleAdminReset(req, res) {
  if (!isAdmin(req)) {
    sendError(res, 401, 'unauthorized', 'A valid x-admin-key header is required');
    return;
  }
  const clearedCache = gateway.cache.clear();
  const clearedInflight = gateway.dedup.clear();
  gateway.router.clearBench();
  gateway.keyPool.providerBench.clear();
  let resetSlots = 0;
  for (const slot of gateway.keyPool.slotsById.values()) {
    slot.reset();
    resetSlots += 1;
  }
  log.warn('admin reset executed', { clearedCache, clearedInflight, resetSlots });
  send(res, 200, { ok: true, clearedCache, clearedInflight, resetSlots });
}

/* ----------------------------------------------------------------- server */

export const server = http.createServer(async (req, res) => {
  const id = requestId();
  const startedAt = Date.now();
  res.setHeader('x-request-id', id);

  let url;
  try {
    url = new URL(req.url ?? '/', `http://${req.headers.host ?? 'localhost'}`);
  } catch {
    sendError(res, 400, 'invalid_request', 'Malformed request URL');
    return;
  }
  const route = `${req.method} ${url.pathname.replace(/\/+$/, '') || '/'}`;

  res.once('finish', () => {
    log.debug('http', { requestId: id, route, status: res.statusCode, ms: Date.now() - startedAt, ip: clientIp(req) });
  });

  if (req.method === 'OPTIONS') {
    send(res, 204, '', {
      'access-control-allow-origin': '*',
      'access-control-allow-headers': 'content-type, authorization, x-api-key, x-admin-key',
      'access-control-allow-methods': 'GET, POST, OPTIONS',
      'access-control-max-age': '600',
    });
    return;
  }

  switch (route) {
    case 'POST /api/ai':
      await handleAi(req, res, id);
      return;
    case 'GET /api/health':
    case 'GET /health':
      handleHealth(res);
      return;
    case 'GET /api/models':
      handleModels(res);
      return;
    case 'GET /api/meta':
      handleMeta(res);
      return;
    case 'GET /api/admin/dashboard':
    case 'GET /admin':
      handleDashboard(req, res);
      return;
    case 'GET /api/admin/stats':
      handleAdminStats(req, res);
      return;
    case 'POST /api/admin/reset':
      handleAdminReset(req, res);
      return;
    case 'GET /':
      send(res, 200, {
        service: CONFIG.serviceName,
        status: gateway.health().status,
        endpoints: ['POST /api/ai', 'GET /api/health', 'GET /api/models', 'GET /api/meta', 'GET /api/admin/dashboard'],
      });
      return;
    default:
      sendError(res, 404, 'not_found', `No route for ${route}`);
  }
});

server.keepAliveTimeout = 72_000;
server.headersTimeout = 75_000;
server.requestTimeout = CONFIG.requestTimeoutMs + 15_000;

/** Start listening + wire graceful shutdown. Exported for tests. */
export function start({ port = CONFIG.port, host = CONFIG.host } = {}) {
  return new Promise((resolve) => {
    server.listen(port, host, () => {
      const address = server.address();
      watchdog.start();
      log.info('gateway listening', {
        host,
        port: typeof address === 'object' && address ? address.port : port,
        env: CONFIG.env,
        models: policyStore.availableKeys().length,
        realCredentials: hasRealCredentials(CONFIG),
        mockOnly: CONFIG.mockOnly,
      });
      if (!hasRealCredentials(CONFIG)) {
        log.warn('no upstream credentials configured - the gateway will answer from the built-in mock provider');
      }
      if (!CONFIG.adminKey) {
        log.warn('ADMIN_KEY is empty - the admin dashboard and admin APIs are disabled');
      }
      resolve(server);
    });
  });
}

/** Close the server and release every timer/handle. */
export function stop() {
  return new Promise((resolve) => {
    watchdog.stop();
    gateway.shutdown();
    server.close(() => resolve());
    // Do not wait forever for keep-alive sockets.
    const timer = setTimeout(() => resolve(), 5000);
    timer.unref?.();
  });
}

let shuttingDown = false;
function installSignalHandlers() {
  for (const signal of ['SIGINT', 'SIGTERM']) {
    process.on(signal, async () => {
      if (shuttingDown) return;
      shuttingDown = true;
      log.info('shutdown signal received', { signal });
      await stop();
      log.info('gateway stopped');
      process.exit(0);
    });
  }
  process.on('unhandledRejection', (reason) => {
    log.error('unhandled promise rejection', { reason: String(reason?.stack ?? reason) });
  });
  process.on('uncaughtException', (error) => {
    log.error('uncaught exception', { error: String(error?.stack ?? error) });
  });
}

// Auto-start only when executed directly (`node src/server.mjs`), never on import.
const invokedDirectly = process.argv[1]
  && (import.meta.url === `file://${process.argv[1]}` || import.meta.url.endsWith(process.argv[1].replace(/^.*?(?=\/gateway\/)/, '')));

if (invokedDirectly) {
  installSignalHandlers();
  start();
}

export default server;
