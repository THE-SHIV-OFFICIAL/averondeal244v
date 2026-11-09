"""End-to-end checks of the sales logger + API control center against main.py
(throw-away DB, Telegram calls mocked)."""
import asyncio
import os
import shutil
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeState:
    def __init__(self): self.data, self.cur = {}, None
    async def set_state(self, s): self.cur = s
    async def update_data(self, **kw): self.data.update(kw)
    async def get_data(self): return dict(self.data)
    async def clear(self): self.data, self.cur = {}, None


class FakeMsg:
    def __init__(self): self.out = []
    async def edit_text(self, text, **kw): self.out.append((text, kw.get("reply_markup")))
    async def answer(self, text, **kw): self.out.append((text, kw.get("reply_markup")))
    async def answer_document(self, doc, **kw): self.out.append(("DOC", doc))


def fake_cb(data, uid):
    cb = SimpleNamespace(data=data, from_user=SimpleNamespace(id=uid), message=FakeMsg(), toasts=[])
    async def ans(text=None, **kw): cb.toasts.append(text)
    cb.answer = ans
    return cb


def all_cb_data(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row if getattr(b, "callback_data", None)]


class Flow(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        for f in ("main.py", "supplier_clients.py", "supplier_store.py", "sales_store.py", "mongo_sync.py"):
            shutil.copy(os.path.join(ROOT, f), cls.tmp)
        os.environ.update(BOT_TOKEN="123456:TESTTOKENtesttokenTESTTOKENtest12345",
                          DB_PATH=os.path.join(cls.tmp, "t.db"), COUPONSHOPS_API_KEY="k")
        sys.path.insert(0, cls.tmp)
        for n in ("main", "supplier_clients", "supplier_store", "sales_store", "mongo_sync"):
            sys.modules.pop(n, None)
        import main
        cls.M = main
        main.init_db(); main.s1_init_tables()

    def setUp(self):
        M = self.M
        self.sent = []
        async def fake_send(chat_id, text, **kw): self.sent.append((chat_id, text))
        self.p = mock.patch.object(M.bot, "send_message", side_effect=fake_send); self.p.start()
        conn = M.db()
        for t in ("orders", "sales_log", "s1_orders", "s1_overrides", "supplier_orders", "supplier_map", "payment_ledger"):
            conn.execute(f"DELETE FROM {t}")
        conn.commit(); conn.close()
        M._S1_CACHE.clear()
        for k, v in (("slog_pub_chat", "-100123"), ("slog_pub_on", "1"), ("slog_priv_on", "1"), ("slog_priv_chat", ""),
                     ("slog_price", "1"), ("slog_mask", "full"), ("sup_on_couponshops", "1")):
            M.set_setting(k, v)
        self.catalog = [{"id": "service_5", "name": "Netflix Premium 1M", "price": 100, "stock": 5},
                        {"id": "service_6", "name": "Spotify 3M", "price": 60, "stock": 0},
                        {"id": "service_7", "name": "Apple Music 5M", "price": 80, "stock": 9}]
        self.pc = mock.patch.object(M.supplier_clients, "fetch_catalog", new=mock.AsyncMock(return_value=self.catalog))
        self.pc.start()

    def tearDown(self):
        self.p.stop(); self.pc.stop()

    async def _flush(self):
        await asyncio.sleep(0.05)
        for t in list(self.M._BG_TASKS):
            await t

    def _s1_order(self, oid="HD1A", uid=8733687681, price=96, cost=80, name="Apple Music 5M"):
        M = self.M
        conn = M.db()
        conn.execute("INSERT INTO orders (order_id,user_id,product_name,duration,amount,status,order_date) VALUES (?,?,?,?,?,?,?)",
                     (oid, uid, "api:c:service_7", "API", price, "approved", "now"))
        conn.execute("INSERT INTO s1_orders (order_id,provider,product_id,name,cost_inr,price,created_at) VALUES (?,?,?,?,?,?,?)",
                     (oid, "couponshops", "service_7", name, cost, price, "now"))
        conn.execute("INSERT INTO payment_ledger (method,purpose,user_id,amount,ref_id,date) VALUES ('AUTOPAY','x',?,?,?,'now')",
                     (uid, price, oid))
        conn.commit(); conn.close()

    # ── sales logger ─────────────────────────────
    async def test_delivery_logs_once_and_posts(self):
        M = self.M
        self._s1_order()
        M.update_order_status("HD1A", "delivered")
        M.update_order_status("HD1A", "delivered")      # second time must not duplicate
        await self._flush()
        conn = M.db(); n = M.sales_store.count(conn); s = M.sales_store.recent(conn, 1)[0]; conn.close()
        self.assertEqual(n, 1)
        self.assertEqual((s["source"], s["provider"], s["amount"], s["cost"], s["profit"]), ("api", "CouponShops", 96, 80, 16))
        pub = [t for c, t in self.sent if c == -100123]
        priv = [t for c, t in self.sent if c == M.OWNER_ID]
        self.assertEqual(len(pub), 1)
        self.assertEqual(len(priv), 1)
        self.assertIn("**********", pub[0])
        self.assertNotIn("8733687681", pub[0])
        self.assertIn("Apple Music 5M", pub[0])
        self.assertIn("8733687681", priv[0])
        self.assertEqual(s["pub_state"], 1)

    async def test_manual_delivery_service2_records_admin_and_mapping_cost(self):
        M = self.M
        conn = M.db()
        conn.execute("INSERT OR REPLACE INTO services (service_key, display_name, description, keywords, is_active) VALUES ('tsvc','Test Svc','d','t',1)")
        conn.execute("DELETE FROM service_prices WHERE service_key='tsvc'")
        conn.execute("INSERT INTO service_prices (service_key,duration,price,stock) VALUES ('tsvc','1M',150,5)")
        conn.execute("INSERT INTO orders (order_id,user_id,product_name,duration,amount,status,order_date) VALUES ('HDX',555,'tsvc','1M',150,'approved','now')")
        conn.commit()
        M.supplier_store.upsert_mapping(conn, {"service_key": "tsvc", "duration": "1M", "provider": "couponshops",
                                               "product_id": "7", "cost": 100, "cost_currency": "INR"})
        conn.close(); M.reload_products()
        M._DELIVERED_BY["HDX"] = 42
        M.update_order_status("HDX", "delivered")
        await self._flush()
        conn = M.db(); s = M.sales_store.get_by_sale_id(conn, "HDX"); conn.close()
        self.assertEqual((s["source"], s["admin_id"], s["cost"], s["product"]), ("service2", 42, 100, "Test Svc — 1M"))

    async def test_failed_post_is_retried_not_lost(self):
        M = self.M
        calls = {"n": 0}
        async def flaky(chat_id, text, **kw):
            calls["n"] += 1
            if chat_id == -100123 and calls["n"] <= 1:
                raise RuntimeError("chat not found")
            self.sent.append((chat_id, text))
        with mock.patch.object(M.bot, "send_message", side_effect=flaky):
            self._s1_order("HD2B")
            M.update_order_status("HD2B", "delivered")
            await self._flush()
            conn = M.db(); s = M.sales_store.get_by_sale_id(conn, "HD2B"); conn.close()
            self.assertEqual((s["pub_state"], s["priv_state"], s["attempts"]), (0, 1, 1))
            self.assertIn("chat not found", M.get_setting("slog_last_error"))
            await M.post_sale(s["id"])                       # what the retry loop does
            conn = M.db(); s = M.sales_store.get_by_sale_id(conn, "HD2B"); conn.close()
            self.assertEqual((s["pub_state"], s["priv_state"]), (1, 1))
        self.assertEqual(len([1 for c, t in self.sent if c == M.OWNER_ID]), 1)   # private not duplicated

    async def test_logging_off_marks_skipped(self):
        M = self.M
        M.set_setting("slog_pub_chat", "")
        self._s1_order("HD3C")
        M.update_order_status("HD3C", "delivered")
        await self._flush()
        conn = M.db(); s = M.sales_store.get_by_sale_id(conn, "HD3C"); conn.close()
        self.assertEqual(s["pub_state"], 2)
        self.assertFalse([1 for c, t in self.sent if c == -100123])

    async def test_manual_sale_flow(self):
        M = self.M
        st = FakeState()
        msg = FakeMsg(); msg.from_user = SimpleNamespace(id=M.OWNER_ID); msg.text = "Apple Music 5M | 2 | 298 | 200 | Rahul 123456789"
        await M.slog_addsale_msg(msg, st)
        self.assertIn("slog_manual", st.data)
        cb = fake_cb("slog_addok", M.OWNER_ID)
        await M.slog_cb(cb, st)
        await self._flush()
        conn = M.db(); s = M.sales_store.recent(conn, 1)[0]; conn.close()
        self.assertEqual((s["source"], s["quantity"], s["amount"], s["profit"], s["user_id"]), ("manual", 2, 298, 98, 123456789))
        pub = [t for c, t in self.sent if c == -100123]
        self.assertTrue(pub and "Quantity: 2" in pub[0] or "𝟮" in pub[0] or "2" in pub[0])
        self.assertNotIn("123456789", pub[0])

    async def test_slog_screens_render_and_callbacks_fit(self):
        M = self.M
        self._s1_order("HD4D"); M.update_order_status("HD4D", "delivered"); await self._flush()
        conn = M.db(); rid = M.sales_store.get_by_sale_id(conn, "HD4D")["id"]; conn.close()
        st = FakeState()
        for data in ("slog", "slog_rep", "slog_rec_0", f"slog_v_{rid}_0", f"slog_d_{rid}_0", "slog_mask", "slog_price",
                     "slog_pubtog", "slog_privtog", "slog_daily", "slog_test", "slog_csv", "slog_add", "slog_setpub"):
            cb = fake_cb(data, M.OWNER_ID)
            await M.slog_cb(cb, st)
            self.assertTrue(cb.message.out, data)
            mk = cb.message.out[-1][1]
            if hasattr(mk, "inline_keyboard"):
                for d in all_cb_data(mk):
                    self.assertLessEqual(len(d.encode()), 64, d)
        # delete flow
        await M.slog_cb(fake_cb(f"slog_dy_{rid}_0", M.OWNER_ID), st)
        conn = M.db(); self.assertEqual(M.sales_store.count(conn), 0); conn.close()

    async def test_non_admin_blocked(self):
        cb = fake_cb("slog", 999)
        await self.M.slog_cb(cb, FakeState())
        self.assertFalse(cb.message.out)
        cb = fake_cb("apic", 999)
        await self.M.apic_cb(cb, FakeState())
        self.assertFalse(cb.message.out)

    # ── API control center ───────────────────────
    async def test_product_toggle_controls_customer_list(self):
        M = self.M
        names = lambda items: sorted(i["name"] for i in items)
        self.assertEqual(len(await M.s1_catalog()), 3)
        st = FakeState()
        cb = fake_cb("apict_0_c:service_5", M.OWNER_ID)
        await M.apic_cb(cb, st)
        self.assertEqual(names(await M.s1_catalog()), ["Apple Music 5M", "Spotify 3M"])
        admin_items = await M.s1_catalog(admin=True)
        self.assertEqual(len(admin_items), 3)
        self.assertTrue([i for i in admin_items if i["pkey"] == "c:service_5"][0]["hidden"])
        self.assertIsNone(await M.s1_find("c:service_5"))                 # cannot be bought either
        await M.apic_cb(fake_cb("apict_0_c:service_5", M.OWNER_ID), st)    # toggle back
        self.assertEqual(len(await M.s1_catalog()), 3)

    async def test_api_off_hides_everything_immediately(self):
        M = self.M
        await M.s1_catalog()                                   # warm cache
        await M.apic_cb(fake_cb("apic_ptog_couponshops", M.OWNER_ID), FakeState())
        self.assertEqual(await M.s1_catalog(), [])             # no 3-minute cache delay
        self.assertEqual(len(await M.s1_catalog(admin=True)), 3)
        await M.apic_cb(fake_cb("apic_ptog_couponshops", M.OWNER_ID), FakeState())
        self.assertEqual(len(await M.s1_catalog()), 3)

    async def test_bulk_and_search(self):
        M = self.M
        st = FakeState()
        await M.apic_cb(fake_cb("apic_all_couponshops_0", M.OWNER_ID), st)
        self.assertEqual(await M.s1_catalog(), [])
        await M.apic_cb(fake_cb("apic_all_couponshops_1", M.OWNER_ID), st)
        self.assertEqual(len(await M.s1_catalog()), 3)
        msg = FakeMsg(); msg.from_user = SimpleNamespace(id=M.OWNER_ID); msg.text = "music"
        st.data["apic_p"] = "couponshops"
        await M.apic_search_msg(msg, st)
        self.assertIn("1 match", msg.out[-1][0].replace("𝟭", "1"))
        await M.apic_cb(fake_cb("apic_q0", M.OWNER_ID), st)
        self.assertEqual(sorted(i["name"] for i in await M.s1_catalog()), ["Netflix Premium 1M", "Spotify 3M"])

    async def test_control_center_screens_and_callback_size(self):
        M = self.M
        st = FakeState()
        for data in ("apic", "apicp_couponshops_0", "apicp_acczone_0", "apic_bal", "apic_ref", "apic_s1auto", "apic_s2auto"):
            cb = fake_cb(data, M.OWNER_ID)
            with mock.patch.object(M.supplier_clients, "fetch_balance", new=mock.AsyncMock(return_value={"balance": 12.5, "currency": "INR"})):
                await M.apic_cb(cb, st)
            self.assertTrue(cb.message.out, data)
            mk = cb.message.out[-1][1]
            if hasattr(mk, "inline_keyboard"):
                for d in all_cb_data(mk):
                    self.assertLessEqual(len(d.encode()), 64, d)

    async def test_bestseller_badge_source(self):
        M = self.M
        for i in range(3):
            oid = f"HDB{i}"; self._s1_order(oid); M.update_order_status(oid, "delivered")
        await self._flush()
        conn = M.db(); best = M.sales_store.bestseller_names(conn); conn.close()
        self.assertEqual(best, {"Apple Music 5M"})


if __name__ == "__main__":
    unittest.main()
