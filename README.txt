DEPLOY (2 jagah)
1) WEBSITE (Vercel): index.html, Admin.html aur api/gw.js ko apne project me replace/add karo -> redeploy.
   (api/gw.js gateway ka proxy hai: payment create/verify yahi se jaata hai.)
2) BOT RUNNER (ek always-on server: VPS / Render worker / Railway; Vercel par bot nahi chal sakta):
   pip install -r requirements.txt
   export FIREBASE_DB_SECRET=<Firebase Console > Project settings > Service accounts > Database secrets>
   python bot_manager.py
   (bot_manager.py aur FF_PANEL_SELLING_BOT_FILE.py ek hi folder me)
3) Admin panel > "Telegram Bots (Paid)" > price + validity set karo. Runner ONLINE dikhna chahiye.
Agar purana logo browser me dikhe: Admin > Media, Videos & Branding me purana Logo/Favicon URL khali karo.
