"""Run:  python -m unittest discover -s tests -v   (from the project folder)"""
import asyncio, os, shutil, sqlite3, sys, tempfile, unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import supplier_store as S
import supplier_clients as C


def mem_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE service_prices (id INTEGER PRIMARY KEY AUTOINCREMENT, service_key TEXT, duration TEXT, price INTEGER, stock INTEGER)")
    S.init_tables(conn)
    return conn


class Pricing(unittest.TestCase):
    def test_fixed_markup(self):
        self.assertEqual(S.compute_price(100, "INR", 88, "fixed", 50), 150)

    def test_percent_markup(self):
        self.assertEqual(S.compute_price(100, "INR", 88, "percent", 20), 120)

    def test_usd_cost_converted_and_rounded_up(self):
        self.assertEqual(S.compute_price(1.5, "USD", 88, "percent", 10), 146)  # 145.2 -> 146

    def test_price_never_below_one(self):
        self.assertEqual(S.compute_price(0, "INR", 88, "fixed", 0), 1)

    def test_parse_adjust(self):
        self.assertEqual(S.parse_adjust("+10%"), ("percent", 10.0))
        self.assertEqual(S.parse_adjust("-15"), ("fixed", -15.0))
        self.assertIsNone(S.parse_adjust("10"))

    def test_bulk_adjust_all_and_one(self):
        conn = mem_db()
        conn.executemany("INSERT INTO service_prices (service_key,duration,price,stock) VALUES (?,?,?,0)",
                         [("a", "1M", 100), ("b", "1M", 200)])
        self.assertEqual(S.bulk_adjust(conn, "+10%"), 2)
        self.assertEqual([r[0] for r in conn.execute("SELECT price FROM service_prices ORDER BY id")], [110, 220])
        S.bulk_adjust(conn, "-15", "a")
        self.assertEqual([r[0] for r in conn.execute("SELECT price FROM service_prices ORDER BY id")], [95, 220])
        S.bulk_adjust(conn, "-500")
        self.assertEqual([r[0] for r in conn.execute("SELECT price FROM service_prices ORDER BY id")], [1, 1])

    def test_parse_markup(self):
        self.assertEqual(S.parse_markup("20%"), ("percent", 20.0))
        self.assertEqual(S.parse_markup("₹50"), ("fixed", 50.0))
        with self.assertRaises(ValueError):
            S.parse_markup("abc")


class Mapping(unittest.TestCase):
    def test_parse_and_upsert(self):
        conn = mem_db()
        m = S.parse_mapping_line("netflix | 1 Month | CouponShops | 123 | 1.5 | usd")
        self.assertEqual(m["provider"], "couponshops"); self.assertEqual(m["cost_currency"], "USD")
        mid = S.upsert_mapping(conn, m)
        m["cost"] = 2; self.assertEqual(S.upsert_mapping(conn, m), mid)  # update, not duplicate
        self.assertEqual(S.get_mapping(conn, mid)["cost"], 2)

    def test_bad_lines(self):
        for line in ("a|b|c", "a|b|unknown|1|5", "a|b|acczone|1|-5", "a|b|acczone|1|x", "a|b|acczone|1|5|EUR"):
            with self.assertRaises(ValueError):
                S.parse_mapping_line(line)

    def test_claim_once_and_profit(self):
        conn = mem_db()
        self.assertTrue(S.claim_order(conn, "O1", "acczone", "p", 150, 100))
        self.assertFalse(S.claim_order(conn, "O1", "acczone", "p", 150, 100))
        S.finish_order(conn, "O1", "delivered", delivery="X")
        S.claim_order(conn, "O2", "acczone", "p", 99, 50); S.finish_order(conn, "O2", "failed")
        t = S.profit_summary(conn)["total"]
        self.assertEqual((t["orders"], t["sales"], t["cost"], t["profit"]), (1, 150, 100, 50))


class Parsing(unittest.TestCase):
    def test_catalog_shapes(self):
        self.assertEqual(S.catalog_items({"data": [{"service_id": 5, "name": "N", "price": 1}]})[0]["id"], "5")
        self.assertEqual(S.catalog_items([{"productId": "x", "title": "T"}])[0]["name"], "T")
        self.assertEqual(S.catalog_items({"abc": {"name": "Z", "price": 2}})[0]["id"], "abc")
        self.assertEqual(S.catalog_items("bad"), [])

    def test_delivery_and_errors(self):
        self.assertEqual(S.extract_delivery({"data": {"codes": ["A", "B"]}}), "A\nB")
        self.assertEqual(S.extract_ref({"orderId": 9}), "9")
        self.assertIsNotNone(S.is_error_response({"success": False, "message": "no stock"}))
        self.assertIsNone(S.is_error_response({"success": True, "codes": ["A"]}))
        self.assertEqual(S.balance_text({"balance": 12.5, "currency": "USD"}), "12.5 USD")


class Clients(unittest.TestCase):
    def _resp(self, code=200, body=None):
        return SimpleNamespace(status_code=code, json=lambda: body if body is not None else {"ok": True})

    def test_couponshops_header_and_guard(self):
        c = C.CouponShopsClient(api_key="K1")
        with mock.patch.object(c._session, "request", return_value=self._resp()) as r:
            c.balance()
            self.assertEqual(r.call_args.kwargs["headers"]["X-API-Key"], "K1")
        with self.assertRaises(ValueError):
            c.create_order("1", 1, "O1")  # no confirm_purchase

    def test_acczone_key_in_query(self):
        c = C.AcczoneClient(api_key="K2")
        with mock.patch.object(c._session, "request", return_value=self._resp()) as r:
            c.services()
            self.assertEqual(r.call_args.kwargs["params"]["apikey"], "K2")

    def test_errors_do_not_leak_key(self):
        import requests
        c = C.EliteToolzClient(api_key="SECRETKEY")
        with mock.patch.object(c._session, "request", side_effect=requests.ConnectionError("x?k=SECRETKEY")):
            with self.assertRaises(C.SupplierAPIError) as e:
                c.balance()
        self.assertNotIn("SECRETKEY", str(e.exception))
        with mock.patch.object(c._session, "request", return_value=self._resp(401)):
            with self.assertRaises(C.SupplierAPIError) as e:
                c.balance()
        self.assertEqual(e.exception.status_code, 401)

    def test_missing_key(self):
        with mock.patch.dict(os.environ, {"ACCZONE_API_KEY": ""}):
            self.assertFalse(C.key_configured("acczone"))
            with self.assertRaises(ValueError):
                C.AcczoneClient()


class BotFlow(unittest.IsolatedAsyncioTestCase):
    """Imports main.py against a throw-away copy so the real DB is untouched."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        for f in ("main.py", "supplier_clients.py", "supplier_store.py", "sales_store.py", "mongo_sync.py"):
            shutil.copy(os.path.join(ROOT, f), cls.tmp)
        os.environ.update(BOT_TOKEN="123456:TESTTOKENtesttokenTESTTOKENtest12345", COUPONSHOPS_API_KEY="k")
        sys.path.insert(0, cls.tmp)
        for name in ("main", "supplier_clients", "supplier_store", "sales_store", "mongo_sync"):
            sys.modules.pop(name, None)
        import main
        cls.M = main
        main.init_db()

    def setUp(self):
        M = self.M
        self.sent = []
        async def fake_send(chat_id, text, **kw):
            self.sent.append((chat_id, text))
        self.p_send = mock.patch.object(M.bot, "send_message", side_effect=fake_send)
        self.p_send.start()
        conn = M.db()
        conn.execute("DELETE FROM supplier_map"); conn.execute("DELETE FROM supplier_orders")
        conn.execute("DELETE FROM orders"); conn.execute("DELETE FROM service_prices WHERE service_key='tsvc'")
        conn.execute("INSERT OR REPLACE INTO services (service_key, display_name, description, keywords, is_active) VALUES ('tsvc','Test','d','t',1)")
        conn.execute("INSERT INTO service_prices (service_key,duration,price,stock) VALUES ('tsvc','1M',150,5)")
        conn.commit(); conn.close()
        M.reload_products()
        M.set_setting("sup_auto_global", "1"); M.set_setting("sup_on_couponshops", "1")

    def tearDown(self):
        self.p_send.stop()

    def _order(self, oid="HDT1", amount=150):
        conn = self.M.db()
        conn.execute("INSERT INTO orders (order_id,user_id,product_name,duration,amount,status,order_date) VALUES (?,?,?,?,?,?,?)",
                     (oid, 555, "tsvc", "1M", amount, "approved", "now"))
        conn.commit(); conn.close()

    def _map(self, cost=100, auto=1):
        conn = self.M.db()
        mid = S.upsert_mapping(conn, {"service_key": "tsvc", "duration": "1M", "provider": "couponshops",
                                      "product_id": "77", "cost": cost, "cost_currency": "INR"})
        S.update_mapping(conn, mid, auto_fulfill=auto); conn.close()
        return mid

    def _status(self, oid):
        conn = self.M.db(); r = conn.execute("SELECT status FROM orders WHERE order_id=?", (oid,)).fetchone(); conn.close()
        return r[0]

    async def test_auto_delivery_success_and_no_double_buy(self):
        self._map(); self._order()
        with mock.patch.object(self.M.supplier_clients, "place_order",
                               new=mock.AsyncMock(return_value={"order_id": "S1", "codes": ["CODE-XYZ"]})) as po:
            await self.M.supplier_autofulfill("HDT1")
            await self.M.supplier_autofulfill("HDT1")
        self.assertEqual(po.await_count, 1)
        self.assertEqual(self._status("HDT1"), "delivered")
        self.assertTrue(any(cid == 555 and "CODE-XYZ" in t for cid, t in self.sent))
        conn = self.M.db(); t = S.profit_summary(conn)["total"]; conn.close()
        self.assertEqual(t["profit"], 50)

    async def test_off_switches_block_purchase(self):
        self._map(); self._order()
        po = mock.AsyncMock()
        with mock.patch.object(self.M.supplier_clients, "place_order", new=po):
            self.M.set_setting("sup_on_couponshops", "0")
            await self.M.supplier_autofulfill("HDT1")
            self.M.set_setting("sup_on_couponshops", "1"); self.M.set_setting("sup_auto_global", "0")
            await self.M.supplier_autofulfill("HDT1")
        po.assert_not_awaited()
        self.assertEqual(self._status("HDT1"), "approved")

    async def test_loss_guard(self):
        self._map(cost=200); self._order()
        po = mock.AsyncMock()
        with mock.patch.object(self.M.supplier_clients, "place_order", new=po):
            await self.M.supplier_autofulfill("HDT1")
        po.assert_not_awaited()
        self.assertTrue(any("cost ₹200.00" in t for _, t in self.sent))

    async def test_timeout_marked_uncertain(self):
        self._map(); self._order()
        err = self.M.supplier_clients.SupplierAPIError("CouponShops", "network/timeout error")
        with mock.patch.object(self.M.supplier_clients, "place_order", new=mock.AsyncMock(side_effect=err)):
            await self.M.supplier_autofulfill("HDT1")
        conn = self.M.db(); so = S.get_supplier_order(conn, "HDT1"); conn.close()
        self.assertEqual(so["status"], "uncertain")
        self.assertEqual(self._status("HDT1"), "approved")

    async def test_admin_panel_buttons(self):
        M = self.M
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.memory import MemoryStorage
        from aiogram.fsm.storage.base import StorageKey
        state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=M.OWNER_ID, user_id=M.OWNER_ID))
        mid = self._map(cost=100, auto=0)
        edits = []
        def cb(data):
            msg = SimpleNamespace(edit_text=mock.AsyncMock(side_effect=lambda t, **k: edits.append(t)),
                                  answer=mock.AsyncMock(side_effect=lambda t, **k: edits.append(t)))
            return SimpleNamespace(data=data, from_user=SimpleNamespace(id=M.OWNER_ID),
                                   answer=mock.AsyncMock(), message=msg)
        for d in ("sup_home", "sup_maps", f"sup_m_{mid}", "sup_profit", "sup_orders", "sup_p_couponshops"):
            await M.handle_cb(cb(d), state)
        self.assertEqual(len(edits), 6)
        await M.handle_cb(cb("sup_tog_couponshops"), state)
        self.assertFalse(M.sup_on("couponshops"))
        await M.handle_cb(cb(f"sup_mauto_{mid}"), state)
        conn = M.db(); self.assertEqual(S.get_mapping(conn, mid)["auto_fulfill"], 1); conn.close()
        # set 20% profit via message, then apply
        await M.handle_cb(cb(f"sup_mmk_{mid}"), state)
        ans = []
        msg = SimpleNamespace(text="20%", from_user=SimpleNamespace(id=M.OWNER_ID),
                              answer=mock.AsyncMock(side_effect=lambda t, **k: ans.append(t)))
        await M.sup_markup_msg(msg, state)
        await M.handle_cb(cb(f"sup_mapply_{mid}"), state)
        self.assertEqual(M.PRODUCTS["tsvc"]["prices"]["1M"], 120)
        # bulk +10% for one service
        await M.sup_bulk_msg(SimpleNamespace(text="tsvc +10%", from_user=SimpleNamespace(id=M.OWNER_ID),
                                             answer=mock.AsyncMock()), state)
        self.assertEqual(M.PRODUCTS["tsvc"]["prices"]["1M"], 132)

    async def test_non_admin_blocked(self):
        M = self.M
        c = SimpleNamespace(data="sup_tog_couponshops", from_user=SimpleNamespace(id=1),
                            answer=mock.AsyncMock(), message=SimpleNamespace())
        await M.supplier_cb(c, None)
        self.assertTrue(M.sup_on("couponshops"))
        c.answer.assert_awaited()


if __name__ == "__main__":
    unittest.main()
