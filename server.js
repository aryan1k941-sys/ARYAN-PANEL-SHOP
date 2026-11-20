// Render Web Service: serves the static panel and proxies gateway/supplier requests.
const express = require('express');
const path = require('path');
const dns = require('dns').promises;
const net = require('net');

const app = express();
app.disable('x-powered-by');
app.use(express.raw({ type: '*/*', limit: '2mb' }));

const DB = (process.env.FIREBASE_DB_URL || 'https://aryan-mods-4c084-default-rtdb.firebaseio.com').replace(/\/$/, '');
const SECRET = process.env.FIREBASE_DB_SECRET || '';
const STATIC = new Set(['famgateway.in', 'www.famgateway.in', 'pay.zapupi.com', 'bantibhaiya.to',
  ...String(process.env.GW_ALLOWED_HOSTS || '').split(',').map(x => x.trim().toLowerCase()).filter(Boolean)]);
let hostCache = { at: 0, hosts: new Set() };

function isPrivateAddress(ip) {
  if (net.isIPv4(ip)) {
    const [a,b] = ip.split('.').map(Number);
    return a === 10 || a === 127 || a === 0 || (a === 169 && b === 254) ||
      (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || a >= 224;
  }
  if (net.isIPv6(ip)) return ip === '::1' || ip.startsWith('fc') || ip.startsWith('fd') || ip.startsWith('fe80:') || ip === '::';
  return true;
}
async function allowedHosts() {
  if (Date.now() - hostCache.at < 60000) return hostCache.hosts;
  const hosts = new Set();
  for (const [node, fields] of [['payment_gateways', ['baseUrl']], ['suppliers', ['apiUrl', 'balanceUrl']]]) {
    try {
      const auth = SECRET ? `?auth=${encodeURIComponent(SECRET)}` : '';
      const r = await fetch(`${DB}/${node}.json${auth}`, { signal: AbortSignal.timeout(6000) });
      if (!r.ok) continue;
      const data = await r.json();
      for (const item of Object.values(data || {})) for (const field of fields) {
        try { if (item && item[field]) hosts.add(new URL(item[field]).hostname.toLowerCase()); } catch {}
      }
    } catch {}
  }
  hostCache = { at: Date.now(), hosts };
  return hosts;
}

app.all('/api/gw', async (req, res) => {
  res.set('Cache-Control', 'no-store');
  res.set('Content-Type', 'application/json');
  const fail = (status, message) => res.status(status).set('x-gw', 'blocked').json({ status: 'error', message });
  let target;
  try { target = new URL(String(req.query.u || '')); } catch { return fail(400, 'Bad gateway URL'); }
  if (target.protocol !== 'https:') return fail(400, 'Only HTTPS gateway URLs are allowed');
  const hostname = target.hostname.toLowerCase();
  if (!STATIC.has(hostname) && !(await allowedHosts()).has(hostname)) return fail(403, 'Gateway host is not in admin gateway/supplier settings');
  try {
    const addresses = await dns.lookup(hostname, { all: true });
    if (!addresses.length || addresses.some(x => isPrivateAddress(x.address))) return fail(403, 'Gateway host resolves to a blocked address');
  } catch { return fail(502, 'Gateway DNS lookup failed'); }

  const method = req.method === 'POST' ? 'POST' : 'GET';
  const headers = { Accept: 'application/json' };
  const apiKey = target.searchParams.get('api_key');
  if (apiKey) headers['X-Api-Key'] = apiKey;
  if (req.headers['x-master-key']) headers['x-master-key'] = String(req.headers['x-master-key']);
  let body;
  if (method === 'POST') {
    headers['Content-Type'] = req.headers['content-type'] || 'application/x-www-form-urlencoded';
    body = Buffer.isBuffer(req.body) ? req.body : undefined;
  }
  res.set('x-gw', 'ok');
  try {
    const upstream = await fetch(target.toString(), { method, headers, body, redirect: 'manual', signal: AbortSignal.timeout(20000) });
    const content = await upstream.text();
    res.status(upstream.status);
    const ct = upstream.headers.get('content-type');
    if (ct) res.set('Content-Type', ct);
    return res.send(content);
  } catch (e) {
    return res.status(502).json({ status: 'error', message: 'Gateway unreachable or timed out' });
  }
});

app.get('/health', (_req, res) => res.json({ service: 'Aryan Panel', status: 'online' }));
app.use(express.static(__dirname, { index: 'index.html' }));
app.get('*', (_req, res) => res.sendFile(path.join(__dirname, 'index.html')));
const port = Number(process.env.PORT || 10000);
app.listen(port, '0.0.0.0', () => console.log(`Aryan Panel listening on ${port}`));
