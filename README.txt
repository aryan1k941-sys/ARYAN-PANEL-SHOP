DEPLOY
1) index.html, Admin.html aur api/gw.js ko Vercel project me replace/add karo -> redeploy.
   (api/gw.js gateway + supplier API ka proxy hai. Admin me jo bhi gateway/supplier API URL save karoge uska host apne aap allow ho jata hai.
    Agar Firebase rules payment_gateways / suppliers ko public read nahi karne dete to Vercel env me FIREBASE_DB_SECRET daalo.)
2) Video/image upload: Admin > Media, Videos & Branding > "Media library" ya "Product videos" tab me "Upload settings".
   Free Cloudinary: cloud name + Unsigned upload preset daalo. Khali = Firebase Storage (rules/plan chahiye).
3) Theme: user panel aur admin panel dono me upar palette button se 8 themes.
