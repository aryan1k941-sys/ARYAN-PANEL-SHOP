// Vercel serverless proxy: browser -> /api/gw?u=<url> -> gateway / supplier panel (server-to-server).
// Fixes CORS + mixed-content (http) problems. Hosts allowed: famgateway, zapupi, plus every API URL the admin
// has saved in Firebase (payment_gateways + suppliers), so a newly added gateway/supplier works automatically.
// Header x-gw: "ok" = upstream was contacted (never retry directly, a purchase may have happened),
//              "blocked" = request never left the proxy (safe to fall back to a direct call).
const DB = (process.env.FIREBASE_DB_URL || 'https://aryan-mods-4c084-default-rtdb.firebaseio.com').replace(/\/$/, '');
const SECRET = process.env.FIREBASE_DB_SECRET || '';
const STATIC = new Set(['famgateway.in', 'www.famgateway.in', 'pay.zapupi.com', 'bantibhaiya.to',
  ...String(process.env.GW_ALLOWED_HOSTS || '').split(',').map(x => x.trim().toLowerCase()).filter(Boolean)]);
let cache = { t: 0, hosts: new Set() };

async function dynamicHosts() {
  if (Date.now() - cache.t < 60000) return cache.hosts;
  const hosts = new Set();
  for (const [node, fields] of [['payment_gateways', ['baseUrl']], ['suppliers', ['apiUrl', 'balanceUrl']]]) {
    try {
      const r = await fetch(`${DB}/${node}.json${SECRET ? '?auth=' + encodeURIComponent(SECRET) : ''}`, { signal: AbortSignal.timeout(6000) });
      const j = await r.json();
      Object.values(j || {}).forEach(o => fields.forEach(f => { try { if (o && o[f]) hosts.add(new URL(o[f]).hostname.toLowerCase()); } catch (e) {} }));
    } catch (e) {}
  }
  cache = { t: Date.now(), hosts };
  return hosts;
}

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Content-Type', 'application/json');
  const blocked = (code, msg) => { res.setHeader('x-gw', 'blocked'); return res.status(code).send(JSON.stringify({ status: 'error', message: msg })); };
  let u;
  try { u = new URL(String((req.query && req.query.u) || '')); } catch (e) { return blocked(400, 'bad url'); }
  const host = u.hostname.toLowerCase();
  if (!STATIC.has(host) && !(await dynamicHosts()).has(host)) return blocked(403, 'host not allowed');
  u.protocol = 'https:';
  const method = req.method === 'POST' ? 'POST' : 'GET';
  const headers = { Accept: 'application/json' };
  const key = u.searchParams.get('api_key'); if (key) headers['X-Api-Key'] = key;
  if (req.headers['x-master-key']) headers['x-master-key'] = String(req.headers['x-master-key']);
  let body;
  if (method === 'POST') {
    headers['Content-Type'] = 'application/x-www-form-urlencoded';
    const b = req.body;
    body = typeof b === 'string' ? b : Buffer.isBuffer(b) ? b.toString() : new URLSearchParams(b && typeof b === 'object' ? b : {}).toString();
  }
  res.setHeader('x-gw', 'ok');
  try {
    const r = await fetch(u.toString(), { method, headers, body, signal: AbortSignal.timeout(20000) });
    const t = await r.text();
    try { JSON.parse(t); return res.status(200).send(t); }
    catch (e) { return res.status(200).send(JSON.stringify({ status: 'error', message: 'Gateway ne JSON nahi diya (HTTP ' + r.status + ')' })); }
  } catch (e) {
    return res.status(200).send(JSON.stringify({ status: 'error', message: 'Gateway timeout / unreachable' }));
  }
};
