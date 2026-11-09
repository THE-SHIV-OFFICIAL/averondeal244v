# AVERON DEALS BOT — main.py

## Run
pip install -r requirements.txt
python main.py

## Payment Gateway — GlobalNexPay AUTOPAY (already configured)
- Panel: https://manage.globalnexpay.site
- API key (user_token) aur secret key code me pre-set hain, gateway **by default ON** hai.
- Admin panel -> ⚡ PAYMENT GATEWAY se status, min amount, test aur pending payments dekh sakte ho.
- Payment aane par wallet/order har 15 second me AUTO confirm hota hai (koi screenshot nahi).

## Payment options (dono hamesha available)
- ⚡ AUTO PAY — instant gateway link, auto verify.
- 📷 MANUAL PAY (QR SCANNER) — UPI QR + "I HAVE PAID", admin manually approve karta hai.
- 💚 WALLET / WALLET + PAY — partial wallet ke baad user AUTO ya MANUAL koi bhi 1 chun sakta hai.
- Wallet screen par bhi dono buttons: ⚡ AUTO PAY — ADD MONEY aur 📷 MANUAL PAY — QR SCANNER.

## Design
- Har button stylish bold font + random colour (primary/success/danger) + premium emoji icon.
- /start par bot-format header + random motivational quote (blockquote bold).
- Premium emoji pack updated (✅ id 6269141158111219856, plus naye 💳 🌐 💬 💲 🎧 📱 🛒 🎬 🚗 🔥 🏀 🎮 📺 ❤️ 📲).

## 📊 Payment History (Owner + Admin)
- Admin panel -> **📊 PAYMENT HISTORY**, ya command **/history**.
- Gateway panel me bhi: **⚡ AUTOPAY LOG DETAILS** aur **📊 AUTO vs MANUAL HISTORY**.
- Report me: AUTOPAY total/today/7-day, MANUAL total/today/7-day, %, grand total + latest 12 payment logs (user id, ref id, time, approving admin).
- Har confirm payment `payment_ledger` table me save hota hai (autopay gateway settle, manual QR deposit approve, manual order approve).

## 🔐 Secrets (.env) — ZARURI
Ab bot token / gateway keys / supplier keys code me nahi hain.
1. `.env.example` ko copy karke `.env` banao, sab NAYE (rotate kiye hue) keys daalo.
2. `pip install -r requirements.txt` phir `python main.py`.
3. Purani DB me gateway key pehle se save ho sakti hai — Admin -> ⚡ PAYMENT GATEWAY se nayi token set karo.

## 🔌 Supplier API + Profit (Admin -> 🔌 SUPPLIER API + PROFIT)
- CouponShops / Acczone / EliteToolz — har API alag **ON/OFF**, balance check, catalog view (product IDs).
- **PRODUCT MAPPING**: `service_key | duration | provider | product_id | cost | INR/USD`
- Har mapping par supplier cost, profit (₹ fixed ya %), **APPLY PRICE** = cost + profit, aur AUTO ON/OFF.
- **BULK PRICE + / -**: `+10%`, `-5%`, `+20`, `-15`, ya `netflix +10%`.
- **USD RATE**: USD cost ko ₹ me badalne ke liye.
- **AUTO DELIVERY** (global) OFF by default. ON + mapping AUTO ON + API ON ho tabhi payment confirm hone ke baad supplier se 1 order lagta hai aur user ko code/account auto bhej diya jata hai.
- Safety: order kabhi double nahi lagta, timeout par "uncertain" mark hota hai (dashboard check karo), price < cost ho to auto order nahi lagta (LOSS GUARD).
- **PROFIT REPORT**: today / 7 days / all-time sale − supplier cost (gateway fees alag).
- **SUPPLIER ORDERS**: latest orders + delivered order ka Resend (naya order nahi lagta).

## ✅ Tests
`python -m unittest discover -s tests -v`

## 🆕 v4 — Service 1 / Service 2 + MongoDB + QR Gateway
- **/start** par 2 buttons: **SERVICE 1** (sab supplier API products — CouponShops, Acczone, EliteToolz — live list, auto delivery) aur **SERVICE 2** (admin ke add kiye services — purana BUY NOW flow, sab same).
- **Service 1 price** = supplier cost + profit (default `+20%`). Admin → **⚡ SERVICE 1 (API)** → `profit +25%`, `price c:service_5 149`, `name ...`, `hide ...`, `rate 88`.
- **Button texts** (Service 1/2, Wallet, Referral, Orders, Support, headings): Admin → ✏️ EDIT MESSAGES → 🎨. `reset` = default.
- **MongoDB**: `.env` me `MONGODB_URI` daalo. Sab tables (users, orders, wallet, payments, referrals, settings...) MongoDB me save hote hain. Pehli baar purana SQLite data automatic upload hota hai; naya server/Render restart par MongoDB se automatic restore.
- **GlobalNexPay**: bot ab gateway se **direct QR image** bhejta hai exact amount ke saath (gateway ₹0.01 add kar sakta hai — QR wala amount hi pay karna). Har 15 sec auto verify.
- ⚠️ Gateway abhi reply de raha hai: *"No Subscribed. Please activate a plan first."* — GlobalNexPay panel me plan activate karna zaruri hai.

## 🆕 v5 — Sales Logger + API Control Center

### 🧾 Sales Logger (Admin → 🧾 SALES LOGGER, ya `/sales`)
- Order **DELIVERED** hote hi sale automatically `sales_log` me save hoti hai (ek order = ek entry, kabhi double nahi). Ye 3 raaste cover karta hai: Service 1 auto delivery, Service 2 supplier auto delivery, aur admin ka manual **DELIVERED** button.
- **Public channel post** (screenshot jaisa):
  `🛍️ New Purchase!` → ID (masked) · Product · Quantity · Price · Time IST
- **Private log** (sirf owner / aapka private chat): product, qty, sold price, cost, **profit**, source (API / Service 2 / Manual), payment method (AutoPay / Manual QR / Wallet), buyer, admin.
- **Manual sale** (bot ke bahar bechi cheez): ➕ ADD MANUAL SALE ya
  `/sold product | quantity | price | cost | buyer`
  e.g. `/sold Apple Music 5M | 1 | 149 | 100 | Rahul 123456789`
- Setup: bot ko channel me **Admin** banao → Admin → 🧾 SALES LOGGER → 🔗 SET PUBLIC CHANNEL (@username / -100… id / channel ka post forward) → 🧪 SEND TEST LOG.
- Settings: channel ON/OFF, private ON/OFF, price dikhana ON/OFF, ID mask (full `**********` / partial `87******81` / show), daily closing report (11:55 PM IST).
- 📊 Report: today / yesterday / 7 / 30 days / all-time, source-wise, API-wise, top products, 7-day bar chart. 🧾 Recent sales (detail, repost, delete) aur 📤 CSV export.
- Post fail ho (bot admin nahi, channel galat) to sale **kho nahi hoti** — retry loop 5 baar tak dobara bhejta hai, error panel me dikhta hai.
- Service 1 list me top sellers par 🔥 badge lagta hai.
- Cost sirf wahin dikhti hai jahan pata ho (API order ya mapping). Baaki me profit "n/a" aur report me alag count hota hai.

### 🔌 API Control Center (Admin → 🔌 API CONTROL CENTER)
- Har API (CouponShops / Acczone / EliteToolz) ka **ON/OFF**: OFF karte hi uske sab products customers se turant chhup jaate hain (purana 3 minute cache delay hata diya).
- API par tap → uske **saare premium products ki list**: product par tap = 🟢 ON ↔ 🔴 OFF. 🟡 = ON hai par stock 0.
- ✅ ALL ON / ⛔ ALL OFF, 🔎 SEARCH (jaise `netflix` likho → saare matching ek saath ON/OFF), 💰 balance, 🔄 refresh.
- OFF product ko customer kharid bhi nahi sakta (purchase par bhi check).
- Yahin se Service 1 / Service 2 **AUTO DELIVERY** ON/OFF aur sab APIs ka balance ek saath.

### 🛠 Fix
- `/gateway`, `/addmoney` jaise commands pehle `auto_detect_service` ki wajah se chalte nahi the (jab koi state na ho) — ab chalte hain.
- Ek API fail ho to uska purana catalog rehta hai, baaki APIs ka list nahi bigadta.

### ✅ Tests
`python -m unittest discover -s tests -v`
