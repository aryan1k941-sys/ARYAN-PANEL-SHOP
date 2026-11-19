// Vercel serverless proxy: browser -> /api/gw?u=<gateway url> -> gateway (server-to-server, no CORS / mixed-content problem)
const ALLOWED = new Set(['famgateway.in', 'www.famgateway.in', 'pay.zapupi.com',
  ...String(process.env.GW_ALLOWED_HOSTS || '').split(',').map(x => x.trim().toLowerCase()).filter(Boolean)]);

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Content-Type', 'application/json');
  let u;
  try { u = new URL(String((req.query && req.query.u) || '')); } catch (e) { return res.status(400).send('{"status":"error","message":"bad url"}'); }
  if (!ALLOWED.has(u.hostname.toLowerCase())) return res.status(403).send('{"status":"error","message":"host not allowed"}');
  u.protocol = 'https:';
  const key = u.searchParams.get('api_key');
  try {
    const r = await fetch(u.toString(), { headers: Object.assign({ Accept: 'application/json' }, key ? { 'X-Api-Key': key } : {}), signal: AbortSignal.timeout(15000) });
    const t = await r.text();
    try { JSON.parse(t); } catch (e) { return res.status(502).send(JSON.stringify({ status: 'error', message: 'Gateway ne JSON nahi diya (HTTP ' + r.status + ')' })); }
    return res.status(200).send(t);
  } catch (e) {
    return res.status(502).send(JSON.stringify({ status: 'error', message: 'Gateway timeout / unreachable' }));
  }
};
