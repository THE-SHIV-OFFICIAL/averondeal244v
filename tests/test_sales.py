import os
import sqlite3
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sales_store as ss  # noqa: E402


def mk():
    conn = sqlite3.connect(":memory:")
    ss.init_tables(conn)
    return conn


class SalesStoreTests(unittest.TestCase):
    def test_record_is_idempotent(self):
        c = mk()
        a = ss.record_sale(c, sale_id="HD1", source="api", product="Apple Music 5M", amount=149, cost=100, user_id=123456789)
        b = ss.record_sale(c, sale_id="HD1", source="api", product="Apple Music 5M", amount=149)
        self.assertIsNotNone(a)
        self.assertIsNone(b)
        self.assertEqual(ss.count(c), 1)
        self.assertEqual(a["profit"], 49)

    def test_unknown_cost_has_no_profit(self):
        c = mk()
        s = ss.record_sale(c, sale_id="X", source="service2", product="Netflix", amount=120)
        self.assertIsNone(s["profit"])
        sm = ss.summary(c)
        self.assertEqual(sm["revenue"], 120)
        self.assertEqual(sm["profit"], 0)
        self.assertEqual(sm["unknown_cost"], 1)

    def test_summary_and_breakdowns(self):
        c = mk()
        ss.record_sale(c, sale_id="1", source="api", provider="elitetoolz", product="A", amount=100, cost=60)
        ss.record_sale(c, sale_id="2", source="api", provider="acczone", product="A", amount=100, cost=70, quantity=2)
        ss.record_sale(c, sale_id="3", source="manual", product="B", amount=50, cost=20)
        sm = ss.summary(c)
        self.assertEqual((sm["orders"], sm["qty"], sm["revenue"], sm["cost"], sm["profit"]), (3, 4, 250, 150, 100))
        src = {r["source"]: r for r in ss.by_source(c)}
        self.assertEqual(src["api"]["orders"], 2)
        self.assertEqual({r["provider"] for r in ss.by_provider(c)}, {"elitetoolz", "acczone"})
        top = ss.top_products(c)
        self.assertEqual(top[0]["product"], "A")
        self.assertEqual(top[0]["qty"], 3)

    def test_time_windows(self):
        c = mk()
        now = time.time()
        ss.record_sale(c, sale_id="old", source="manual", product="P", amount=10, ts=now - 10 * 86400)
        ss.record_sale(c, sale_id="new", source="manual", product="P", amount=20, ts=now)
        self.assertEqual(ss.summary(c, now - 86400)["orders"], 1)
        self.assertEqual(ss.summary(c)["orders"], 2)
        t0, t1 = ss.day_bounds(now)
        self.assertEqual(ss.summary(c, t0, t1)["revenue"], 20)

    def test_pending_retry_states(self):
        c = mk()
        s = ss.record_sale(c, sale_id="1", source="manual", product="P", amount=1)
        self.assertEqual(len(ss.pending(c)), 1)
        ss.set_state(c, s["id"], pub=1, priv=1)
        self.assertEqual(ss.pending(c), [])
        s2 = ss.record_sale(c, sale_id="2", source="manual", product="P", amount=1)
        for _ in range(ss.MAX_ATTEMPTS):
            ss.set_state(c, s2["id"], bump=True)
        self.assertEqual(ss.pending(c), [])

    def test_delete_and_recent(self):
        c = mk()
        a = ss.record_sale(c, sale_id="1", source="manual", product="P", amount=1)
        ss.record_sale(c, sale_id="2", source="manual", product="Q", amount=1)
        self.assertEqual(ss.recent(c, 1)[0]["product"], "Q")
        self.assertTrue(ss.delete_sale(c, a["id"]))
        self.assertFalse(ss.delete_sale(c, a["id"]))
        self.assertEqual(ss.count(c), 1)

    def test_parse_manual_line(self):
        m = ss.parse_manual_line("Apple Music 5M | 2 | 298 | 200 | Rahul 123456789")
        self.assertEqual((m["product"], m["quantity"], m["amount"], m["cost"], m["user_id"]),
                         ("Apple Music 5M", 2, 298, 200, 123456789))
        m = ss.parse_manual_line("Netflix | 1 | ₹1,099")
        self.assertEqual((m["amount"], m["cost"], m["user_id"]), (1099, None, 0))
        for bad in ("only one part", "x | 0 | 10", "x | 1 | abc", "x | 1 | -5", " | 1 | 5", "x | 1 | 5 | zz"):
            with self.assertRaises(ValueError, msg=bad):
                ss.parse_manual_line(bad)

    def test_mask(self):
        self.assertEqual(ss.mask_id(8733687681), "**********")
        self.assertEqual(ss.mask_id(8733687681, "partial"), "87******81")
        self.assertEqual(ss.mask_id(8733687681, "none"), "8733687681")
        self.assertEqual(ss.mask_id(0), "**********")

    def test_public_format_matches_screenshot(self):
        c = mk()
        s = ss.record_sale(c, sale_id="1", source="api", product="Apple Music 5M", amount=149,
                           user_id=8733687681, ts=1762678320)  # 09 Nov 2026 02:22 PM IST
        txt = ss.format_public(s, show_price=False)
        self.assertIn("New Purchase!", txt)
        self.assertIn("ID: **********", txt)
        self.assertIn("Product: Apple Music 5M", txt)
        self.assertIn("Quantity: 1", txt)
        self.assertIn("IST", txt)
        self.assertNotIn("Price", txt)
        self.assertIn("Price: ₹149", ss.format_public(s, show_price=True))

    def test_html_is_escaped(self):
        c = mk()
        s = ss.record_sale(c, sale_id="1", source="manual", product="<b>x</b> & y", amount=5, buyer="<i>z</i>")
        self.assertNotIn("<b>x</b>", ss.format_public(s))
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", ss.format_private(s))

    def test_report_and_csv(self):
        c = mk()
        ss.record_sale(c, sale_id="1", source="api", provider="acczone", product="A", amount=100, cost=60)
        ss.record_sale(c, sale_id="2", source="manual", product="B", amount=50)
        rep = ss.format_report(c)
        self.assertIn("SALES REPORT", rep)
        self.assertIn("acczone", rep)
        data = ss.export_csv(c).decode("utf-8-sig")
        self.assertEqual(len(data.strip().splitlines()), 3)

    def test_daily_series_len(self):
        c = mk()
        self.assertEqual(len(ss.daily_series(c, 7)), 7)

    def test_bestsellers(self):
        c = mk()
        for i in range(3):
            ss.record_sale(c, sale_id=f"a{i}", source="api", product="Hot", amount=10)
        ss.record_sale(c, sale_id="b", source="api", product="Cold", amount=10)
        self.assertEqual(ss.bestseller_names(c), {"Hot"})


if __name__ == "__main__":
    unittest.main()
