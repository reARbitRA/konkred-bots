/**
 * Konkred AI Gateway - admin dashboard renderer.
 *
 * Emits a single self-contained HTML page (no CDN, no build step, no runtime
 * dependency) showing live quota consumption per slot, cache/dedup health,
 * routing tables and recent watchdog calibrations. Served behind the
 * constant-time `x-admin-key` check in server.mjs.
 */
import { escapeHtml, humanizeMs, round } from './util.mjs';

function bar(value, max, { warn = 0.7, danger = 0.9 } = {}) {
  const limit = Number(max);
  if (!Number.isFinite(limit) || limit <= 0) {
    return '<div class="bar"><span style="width:0%"></span></div><small>no limit</small>';
  }
  const ratio = Math.max(0, Math.min(1, Number(value) / limit));
  const level = ratio >= danger ? 'danger' : ratio >= warn ? 'warn' : 'ok';
  return `<div class="bar ${level}"><span style="width:${round(ratio * 100, 1)}%"></span></div>`
    + `<small>${Number(value).toLocaleString('en-US')} / ${limit.toLocaleString('en-US')}</small>`;
}

function stateBadge(state) {
  const cls = state === 'ready' ? 'ok' : state === 'cooldown' ? 'warn' : 'danger';
  return `<span class="badge ${cls}">${escapeHtml(state)}</span>`;
}

function renderSlotRows(models) {
  const rows = [];
  for (const model of models) {
    for (const slot of model.detail) {
      rows.push(`
        <tr>
          <td><code>${escapeHtml(slot.model)}</code><br><small>${escapeHtml(slot.provider)}</small></td>
          <td><code>${escapeHtml(slot.credential)}</code><br><small>${escapeHtml(slot.credentialSource)}</small></td>
          <td>${stateBadge(slot.state)}${slot.cooldownMsRemaining
            ? `<br><small>${escapeHtml(humanizeMs(slot.cooldownMsRemaining))}</small>` : ''}</td>
          <td>${bar(slot.usage.rpm, slot.effective.rpm)}</td>
          <td>${bar(slot.usage.tpm, slot.effective.tpm)}</td>
          <td>${bar(slot.usage.rpd, slot.effective.rpd)}</td>
          <td>${bar(slot.usage.tpd, slot.effective.tpd)}</td>
          <td class="num">${slot.totals.requests.toLocaleString('en-US')}</td>
          <td class="num">${slot.totals.failures.toLocaleString('en-US')}</td>
          <td class="num">${slot.totals.avgLatencyMs} ms</td>
          <td><small>${slot.lastError ? escapeHtml(`${slot.lastError.errorClass}: ${slot.lastError.message}`) : '—'}</small></td>
        </tr>`);
    }
  }
  return rows.join('');
}

function renderRoutes(routes) {
  return Object.entries(routes)
    .map(([task, chain]) => `
      <tr>
        <td><code>${escapeHtml(task)}</code></td>
        <td>${chain.length
      ? chain.map((model) => `<span class="chip">${escapeHtml(model)}</span>`).join(' ')
      : '<em>no available model</em>'}</td>
      </tr>`)
    .join('');
}

/**
 * Render the full dashboard document.
 * @param {object} input { stats, watchdog, providers, config, registryVersion }
 */
export function renderDashboard({ stats, watchdog, providers, registryVersion, env }) {
  const { health, pool, router, limiter, byModel, byTask, byErrorClass } = stats;
  const statusClass = health.status === 'healthy' ? 'ok' : health.status === 'degraded' ? 'warn' : 'danger';

  const providerRows = providers.map((provider) => `
    <tr>
      <td><code>${escapeHtml(provider.provider)}</code></td>
      <td>${provider.ready ? '<span class="badge ok">ready</span>' : '<span class="badge danger">not configured</span>'}</td>
      <td class="num">${provider.credentials}</td>
      <td><small>${escapeHtml(provider.sources.join(', ') || '—')}</small></td>
      <td><small>${escapeHtml(provider.baseUrl)}</small></td>
    </tr>`).join('');

  const modelStatRows = Object.entries(byModel)
    .sort((a, b) => (b[1].success + b[1].errors) - (a[1].success + a[1].errors))
    .map(([model, bucket]) => `
      <tr>
        <td><code>${escapeHtml(model)}</code></td>
        <td class="num">${bucket.success}</td>
        <td class="num">${bucket.errors}</td>
        <td class="num">${round(bucket.successRate * 100, 1)}%</td>
        <td class="num">${bucket.avgLatencyMs} ms</td>
        <td class="num">${bucket.tokens.toLocaleString('en-US')}</td>
      </tr>`).join('') || '<tr><td colspan="6"><em>no traffic yet</em></td></tr>';

  const errorRows = Object.entries(byErrorClass)
    .sort((a, b) => b[1] - a[1])
    .map(([errorClass, count]) => `<span class="chip danger">${escapeHtml(errorClass)} · ${count}</span>`)
    .join(' ') || '<em>none</em>';

  const taskRows = Object.entries(byTask)
    .map(([task, bucket]) => `<span class="chip">${escapeHtml(task)} · ${bucket.requests}✓ ${bucket.errors}✗</span>`)
    .join(' ');

  const userRows = limiter.top.length
    ? limiter.top.map((user) => `
      <tr>
        <td><code>${escapeHtml(user.user)}</code></td>
        <td>${escapeHtml(user.tier)}</td>
        <td class="num">${user.rpm}</td>
        <td class="num">${user.requestsToday}</td>
        <td class="num">${user.tokensToday.toLocaleString('en-US')}</td>
      </tr>`).join('')
    : '<tr><td colspan="5"><em>no users yet</em></td></tr>';

  const calibrationRows = (watchdog.calibrations ?? []).length
    ? watchdog.calibrations.map((entry) => `
      <tr>
        <td><small>${escapeHtml(entry.at)}</small></td>
        <td><code>${escapeHtml(entry.slot)}</code></td>
        <td>${escapeHtml(entry.field)}</td>
        <td class="num">${escapeHtml(String(entry.value))}</td>
      </tr>`).join('')
    : '<tr><td colspan="4"><em>no calibrations recorded</em></td></tr>';

  const benched = [
    ...router.benched.map((entry) => `<span class="chip warn">model ${escapeHtml(entry.model)} · ${escapeHtml(humanizeMs(entry.msRemaining))}</span>`),
    ...pool.benchedProviders.map((entry) => `<span class="chip warn">provider ${escapeHtml(entry.provider)} · ${escapeHtml(humanizeMs(entry.msRemaining))}</span>`),
  ].join(' ') || '<em>nothing benched</em>';

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Konkred Gateway · Admin</title>
<style>
  :root {
    color-scheme: dark;
    --bg: #0b0f17; --panel: #121826; --panel-2: #172033; --line: #223049;
    --text: #e6edf7; --muted: #8fa3bf; --ok: #3ddc97; --warn: #ffc857; --danger: #ff6b6b; --accent: #5b9dff;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
    font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  header { padding: 20px 28px; border-bottom: 1px solid var(--line);
    display: flex; align-items: center; gap: 16px; flex-wrap: wrap; background: var(--panel); }
  h1 { font-size: 18px; margin: 0; letter-spacing: .3px; }
  h2 { font-size: 15px; margin: 0 0 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .8px; }
  main { padding: 24px 28px 64px; display: grid; gap: 22px; max-width: 1600px; margin: 0 auto; }
  section { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 18px 20px; overflow-x: auto; }
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
  .kpi { background: var(--panel-2); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
  .kpi b { display: block; font-size: 22px; font-weight: 650; }
  .kpi span { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .6px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: .6px; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  code { background: var(--panel-2); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
  small { color: var(--muted); font-size: 11px; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
  .badge.ok { background: rgba(61,220,151,.15); color: var(--ok); }
  .badge.warn { background: rgba(255,200,87,.15); color: var(--warn); }
  .badge.danger { background: rgba(255,107,107,.15); color: var(--danger); }
  .chip { display: inline-block; background: var(--panel-2); border: 1px solid var(--line);
    border-radius: 999px; padding: 2px 9px; font-size: 11px; margin: 2px 0; }
  .chip.warn { border-color: rgba(255,200,87,.4); color: var(--warn); }
  .chip.danger { border-color: rgba(255,107,107,.4); color: var(--danger); }
  .bar { position: relative; height: 6px; border-radius: 3px; background: var(--panel-2); overflow: hidden; min-width: 90px; }
  .bar span { position: absolute; inset: 0 auto 0 0; background: var(--ok); }
  .bar.warn span { background: var(--warn); }
  .bar.danger span { background: var(--danger); }
  .grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 22px; }
  .pill { margin-left: auto; color: var(--muted); font-size: 12px; }
  a { color: var(--accent); }
</style>
</head>
<body>
<header>
  <h1>⚡ Konkred AI Gateway</h1>
  <span class="badge ${statusClass}">${escapeHtml(health.status)}</span>
  <span class="chip">registry ${escapeHtml(String(registryVersion))}</span>
  <span class="chip">env ${escapeHtml(env)}</span>
  <span class="chip">uptime ${escapeHtml(humanizeMs(health.uptimeSeconds * 1000))}</span>
  <span class="pill">auto-refresh 15s · generated ${new Date().toISOString()}</span>
</header>
<main>
  <section>
    <h2>Runtime</h2>
    <div class="kpis">
      <div class="kpi"><span>Requests</span><b>${health.requests.total.toLocaleString('en-US')}</b></div>
      <div class="kpi"><span>Succeeded</span><b>${health.requests.succeeded.toLocaleString('en-US')}</b></div>
      <div class="kpi"><span>Failed</span><b>${health.requests.failed.toLocaleString('en-US')}</b></div>
      <div class="kpi"><span>Throttled</span><b>${health.requests.throttled.toLocaleString('en-US')}</b></div>
      <div class="kpi"><span>Cache hits</span><b>${health.requests.cacheHits.toLocaleString('en-US')}</b></div>
      <div class="kpi"><span>Coalesced</span><b>${health.requests.coalesced.toLocaleString('en-US')}</b></div>
      <div class="kpi"><span>Avg latency</span><b>${health.requests.avgLatencyMs} ms</b></div>
      <div class="kpi"><span>Tokens</span><b>${health.tokens.total.toLocaleString('en-US')}</b></div>
      <div class="kpi"><span>Ready slots</span><b>${health.slots.ready}/${health.slots.total}</b></div>
      <div class="kpi"><span>Cache size</span><b>${health.cache.size}/${health.cache.maxEntries}</b></div>
      <div class="kpi"><span>Hit rate</span><b>${round(health.cache.hitRate * 100, 1)}%</b></div>
      <div class="kpi"><span>In flight</span><b>${health.dedup.inflight}</b></div>
    </div>
  </section>

  <section>
    <h2>Key pool · quota consumption</h2>
    <table>
      <thead><tr>
        <th>Model</th><th>Credential</th><th>State</th>
        <th>RPM</th><th>TPM</th><th>RPD</th><th>TPD</th>
        <th>Calls</th><th>Fails</th><th>Latency</th><th>Last error</th>
      </tr></thead>
      <tbody>${renderSlotRows(pool.models) || '<tr><td colspan="11"><em>no slots configured</em></td></tr>'}</tbody>
    </table>
    <p><small>Headroom: RPM ${round(pool.headroom.rpm * 100, 0)}% · TPM ${round(pool.headroom.tpm * 100, 0)}% ·
      RPD ${round(pool.headroom.rpd * 100, 0)}% · TPD ${round(pool.headroom.tpd * 100, 0)}%</small></p>
    <p>${benched}</p>
  </section>

  <div class="grid-2">
    <section>
      <h2>Providers</h2>
      <table>
        <thead><tr><th>Provider</th><th>Status</th><th>Keys</th><th>Sources</th><th>Base URL</th></tr></thead>
        <tbody>${providerRows}</tbody>
      </table>
    </section>

    <section>
      <h2>Model performance</h2>
      <table>
        <thead><tr><th>Model</th><th>OK</th><th>Err</th><th>Rate</th><th>Latency</th><th>Tokens</th></tr></thead>
        <tbody>${modelStatRows}</tbody>
      </table>
    </section>
  </div>

  <section>
    <h2>Routing table</h2>
    <table>
      <thead><tr><th>Task</th><th>Available candidate chain (in priority order)</th></tr></thead>
      <tbody>${renderRoutes(router.routes)}</tbody>
    </table>
    <p>${taskRows}</p>
    <p><small>Error classes:</small> ${errorRows}</p>
  </section>

  <div class="grid-2">
    <section>
      <h2>Top users</h2>
      <table>
        <thead><tr><th>User</th><th>Tier</th><th>RPM</th><th>Req today</th><th>Tokens today</th></tr></thead>
        <tbody>${userRows}</tbody>
      </table>
    </section>

    <section>
      <h2>Watchdog</h2>
      <div class="kpis">
        <div class="kpi"><span>Running</span><b>${watchdog.running ? 'yes' : 'no'}</b></div>
        <div class="kpi"><span>Ticks</span><b>${watchdog.ticks}</b></div>
        <div class="kpi"><span>Interval</span><b>${escapeHtml(watchdog.intervalHuman)}</b></div>
        <div class="kpi"><span>Calibration</span><b>${watchdog.calibrationEnabled ? 'on' : 'off'}</b></div>
      </div>
      <table>
        <thead><tr><th>When</th><th>Slot</th><th>Field</th><th>Observed</th></tr></thead>
        <tbody>${calibrationRows}</tbody>
      </table>
    </section>
  </div>
</main>
<script>
  setTimeout(() => window.location.reload(), 15000);
</script>
</body>
</html>`;
}

export default renderDashboard;
