ARYAN PANEL SHOP - RENDER WEB SERVICE DEPLOYMENT

IMPORTANT
Your previous project was configured as a static site, but /api/gw is a server endpoint.
A static site cannot execute api/gw.js. This package adds server.js (Express) to serve
index.html/Admin.html and handle /api/gw on the SAME origin.

DEPLOY ON RENDER
1. Upload/replace these files in your GitHub repository root:
   index.html, Admin.html, FF_PANEL_SELLING_BOT_FILE.py, server.js, package.json.
2. In Render create a NEW Web Service from this repository (or change service type if Render allows).
3. Runtime: Node. Build Command: npm install. Start Command: npm start.
4. Add environment variables if needed:
   FIREBASE_DB_URL = https://aryan-mods-4c084-default-rtdb.firebaseio.com
   FIREBASE_DB_SECRET = (only if your Firebase rules require REST admin access)
   GW_ALLOWED_HOSTS = comma-separated gateway hostnames only if needed, e.g. api.example.com
5. Deploy. Check https://YOUR-SERVICE.onrender.com/health; it should show
   {"service":"Aryan Panel","status":"online"}.
6. Open the same Web Service URL and test Admin > Payment Gateways > Test API.

NOTES / SAFETY
- Keep FAMPAY.XL configuration and API key unchanged during first test.
- Do not paste API keys into chat. Keep secrets in admin settings/Firebase and Render env vars.
- Dynamic gateway hosts are read from Firebase payment_gateways.baseUrl and suppliers.apiUrl/balanceUrl.
- Proxy allows HTTPS only and rejects private-network DNS results.
- This fixes the missing server-side proxy route; it does NOT guarantee that a gateway's
  endpoint paths, authentication headers, or request/response format are correct.
- Admin's current Test API probes verifyPath with a dummy order_id. A gateway can correctly
  respond "order not found" while being reachable; a future UI improvement should separate
  transport connectivity from payment verification.
- Do not deploy the Vercel-specific api/gw.js on Render static hosting and expect it to run.
