#!/usr/bin/env node
/**
 * CDP helper for Chrome M144+ (WebSocket-only remote debugging).
 * Usage:
 *   node cdp-chrome.js [--customer Name] page-text
 *   node cdp-chrome.js [--customer Name] navigate <url>
 *   node cdp-chrome.js [--customer Name] eval '<js>'
 *   node cdp-chrome.js [--customer Name] eval-file <path>
 *   node cdp-chrome.js [--customer Name] auth
 *
 * Requires Chrome started with --remote-debugging-port=9222 so
 * ~/Library/Application Support/Google/Chrome/DevToolsActivePort exists.
 */
const fs = require('fs');
const os = require('os');
const path = require('path');

function parseArgs(argv) {
  const out = { customer: process.env.PS_CUSTOMER || '', rest: [] };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--customer') {
      out.customer = argv[++i] || '';
    } else {
      out.rest.push(argv[i]);
    }
  }
  return out;
}

function wsUrl() {
  const p = path.join(os.homedir(), 'Library/Application Support/Google/Chrome/DevToolsActivePort');
  const [port, wsPath] = fs.readFileSync(p, 'utf8').trim().split('\n');
  return `ws://127.0.0.1:${port}${wsPath}`;
}

function connect(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    const t = setTimeout(() => reject(new Error('ws open timeout ' + url)), 30000);
    ws.addEventListener('open', () => { clearTimeout(t); resolve(ws); });
    ws.addEventListener('error', (e) => { clearTimeout(t); reject(e.error || e); });
  });
}

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.id = 1;
    this.pending = new Map();
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error)));
        else resolve(msg.result);
      }
    });
  }
  send(method, params = {}, sessionId) {
    const id = this.id++;
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error('timeout ' + method)), 60000);
      this.pending.set(id, {
        resolve: (r) => { clearTimeout(t); resolve(r); },
        reject: (e) => { clearTimeout(t); reject(e); },
      });
      const payload = { id, method, params };
      if (sessionId) payload.sessionId = sessionId;
      this.ws.send(JSON.stringify(payload));
    });
  }
  close() { this.ws.close(); }
}

async function findPerfectScaleTarget(cdp, customer) {
  const { targetInfos } = await cdp.send('Target.getTargets');
  const pages = targetInfos.filter((t) => t.type === 'page' && /app\.perfectscale\.io/.test(t.url) && !/backoffice/.test(t.url));
  const want = (customer || '').toLowerCase();
  let fallback = null;
  for (const t of pages) {
    try {
      const { sessionId } = await cdp.send('Target.attachToTarget', { targetId: t.targetId, flatten: true });
      await cdp.send('Runtime.enable', {}, sessionId);
      const r = await cdp.send('Runtime.evaluate', {
        expression: `(() => ({ href: location.href, text: (document.body.innerText||'').slice(0,800) }))()`,
        returnByValue: true,
      }, sessionId);
      const v = r.result && r.result.value;
      const blob = ((v && v.text) || '') + ' ' + ((v && v.href) || '');
      t._sessionId = sessionId;
      if (want && new RegExp(want.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i').test(blob)) {
        return t;
      }
      if (/IMPERSONATING/i.test(blob) && !fallback) fallback = t;
      if (!fallback) fallback = t;
    } catch (_) { /* try next */ }
  }
  if (!fallback) throw new Error('No PerfectScale tab found. Open app.perfectscale.io in Chrome with remote debugging.');
  return fallback;
}

async function attachPage(cdp, target) {
  if (target._sessionId) return target._sessionId;
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId: target.targetId, flatten: true });
  await cdp.send('Runtime.enable', {}, sessionId);
  await cdp.send('Page.enable', {}, sessionId);
  return sessionId;
}

async function evaluate(cdp, sessionId, expression, awaitPromise = false) {
  const r = await cdp.send('Runtime.evaluate', {
    expression,
    awaitPromise,
    returnByValue: true,
    timeout: 55000,
  }, sessionId);
  if (r.exceptionDetails) {
    const desc = r.exceptionDetails.exception?.description || r.exceptionDetails.text;
    throw new Error(desc);
  }
  return r.result.value;
}

async function withPage(customer, fn) {
  const cdp = new Cdp(await connect(wsUrl()));
  try {
    const target = await findPerfectScaleTarget(cdp, customer);
    const sessionId = await attachPage(cdp, target);
    await cdp.send('Page.enable', {}, sessionId);
    return await fn(cdp, sessionId, target);
  } finally {
    cdp.close();
  }
}

const parsed = parseArgs(process.argv.slice(2));
const cmd = parsed.rest[0];
const customer = parsed.customer;

(async () => {
  if (cmd === 'auth') {
    const out = await withPage(customer, async (cdp, sessionId, target) => {
      const info = await evaluate(cdp, sessionId, `(() => {
        const url = window.location.href.toLowerCase();
        const onLogin = /\\/login|\\/signin|\\/auth/.test(url)
          || !!document.querySelector('input[type="password"]');
        return {
          url: window.location.href,
          title: document.title,
          onLogin,
          impersonating: /IMPERSONATING/i.test(document.body.innerText || ''),
          hasImpersonationToken: !!sessionStorage.getItem('psc-impersonation-token'),
          hasPscToken: !!localStorage.getItem('psc-token'),
        };
      })()`);
      return { targetUrl: target.url, customer, ...info };
    });
    console.log(JSON.stringify(out, null, 2));
    return;
  }

  if (cmd === 'eval' || cmd === 'eval-async' || cmd === 'eval-file') {
    let expr = parsed.rest[1];
    let awaitPromise = cmd === 'eval-async';
    if (cmd === 'eval-file') {
      expr = fs.readFileSync(parsed.rest[1], 'utf8');
      awaitPromise = /await |\.then\(/.test(expr) || expr.includes('async');
    }
    const out = await withPage(customer, async (cdp, sessionId) => evaluate(cdp, sessionId, expr, awaitPromise));
    console.log(typeof out === 'string' ? out : JSON.stringify(out, null, 2));
    return;
  }

  if (cmd === 'navigate') {
    const url = parsed.rest[1];
    const out = await withPage(customer, async (cdp, sessionId) => {
      await cdp.send('Page.navigate', { url }, sessionId);
      await evaluate(cdp, sessionId, `new Promise(r => {
        if (document.readyState === 'complete') setTimeout(r, 500);
        else window.addEventListener('load', () => setTimeout(r, 500));
      })`, true);
      await evaluate(cdp, sessionId, `new Promise(r => setTimeout(r, 2500))`, true);
      return {
        url: await evaluate(cdp, sessionId, 'window.location.href'),
        title: await evaluate(cdp, sessionId, 'document.title'),
      };
    });
    console.log(JSON.stringify(out, null, 2));
    return;
  }

  if (cmd === 'page-text') {
    const out = await withPage(customer, async (cdp, sessionId) => evaluate(
      cdp, sessionId,
      `document.body && document.body.innerText ? document.body.innerText.slice(0, 80000) : ''`,
    ));
    console.log(out);
    return;
  }

  console.error('unknown command', cmd);
  process.exit(2);
})().catch((e) => {
  console.error(e.stack || e.message || e);
  process.exit(1);
});
