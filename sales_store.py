"""Sales ledger — every sale (API, Service 2, manual) is stored here.

Pure SQLite + formatting helpers (no Telegram / network), so it is fully
unit-testable.  `main.py` calls these functions and does the Telegram part.

Table `sales_log`
  sale_id     unique key (order id, or "MS-xxxx" for manual sales) -> never logged twice
  source      'api' (Service 1) | 'service2' | 'service2_api' (S2 auto via supplier) | 'manual'
  amount      total selling price for the whole quantity (INR)
  cost        total supplier / purchase cost (NULL = unknown)
  pub_state / priv_state   0 pending | 1 sent | 2 skipped | 3 gave up (too many failures)
"""
from __future__ import annotations

import csv
import html
import io
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

SOURCES = {
    "api":          "⚡ Service 1 (API)",
    "service2_api": "🤖 Service 2 (API auto)",
    "service2":     "🛒 Service 2 (manual)",
    "manual":       "✍️ Manual sale",
}

MAX_ATTEMPTS = 5


# ── schema ─────────────────────────────────────────
def init_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS sales_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        sale_id      TEXT UNIQUE,
        source       TEXT DEFAULT 'service2',
        provider     TEXT DEFAULT '',
        user_id      INTEGER DEFAULT 0,
        buyer        TEXT DEFAULT '',
        product      TEXT DEFAULT '',
        quantity     INTEGER DEFAULT 1,
        amount       REAL DEFAULT 0,
        cost         REAL,
        method       TEXT DEFAULT '',
        admin_id     INTEGER DEFAULT 0,
        note         TEXT DEFAULT '',
        created_at   REAL,
        pub_state    INTEGER DEFAULT 0,
        priv_state   INTEGER DEFAULT 0,
        attempts     INTEGER DEFAULT 0
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_created ON sales_log(created_at)")
    conn.commit()


COLS = ("id", "sale_id", "source", "provider", "user_id", "buyer", "product", "quantity",
        "amount", "cost", "method", "admin_id", "note", "created_at",
        "pub_state", "priv_state", "attempts")


def _row(r):
    if not r:
        return None
    d = dict(zip(COLS, r))
    d["profit"] = None if d["cost"] is None else float(d["amount"] or 0) - float(d["cost"] or 0)
    return d


# ── write ──────────────────────────────────────────
def record_sale(conn, *, sale_id: str, source: str, product: str, amount: float,
                quantity: int = 1, cost: float | None = None, provider: str = "",
                user_id: int = 0, buyer: str = "", method: str = "",
                admin_id: int = 0, note: str = "", ts: float | None = None) -> dict | None:
    """Insert a sale. Returns the saved row, or None if sale_id was already logged."""
    if source not in SOURCES:
        source = "service2"
    quantity = max(1, int(quantity or 1))
    try:
        conn.execute(
            """INSERT INTO sales_log (sale_id, source, provider, user_id, buyer, product, quantity,
               amount, cost, method, admin_id, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sale_id, source, provider or "", int(user_id or 0), (buyer or "")[:80],
             (product or "")[:120], quantity, float(amount or 0),
             None if cost is None else float(cost), (method or "")[:30],
             int(admin_id or 0), (note or "")[:200], ts or time.time()))
        conn.commit()
    except sqlite3.IntegrityError:
        return None
    return get_by_sale_id(conn, sale_id)


def get_by_sale_id(conn, sale_id: str):
    return _row(conn.execute(f"SELECT {','.join(COLS)} FROM sales_log WHERE sale_id=?", (sale_id,)).fetchone())


def get_by_id(conn, rid: int):
    return _row(conn.execute(f"SELECT {','.join(COLS)} FROM sales_log WHERE id=?", (rid,)).fetchone())


def delete_sale(conn, rid: int) -> bool:
    cur = conn.execute("DELETE FROM sales_log WHERE id=?", (rid,))
    conn.commit()
    return cur.rowcount > 0


def set_state(conn, rid: int, *, pub: int | None = None, priv: int | None = None, bump: bool = False):
    if pub is not None:
        conn.execute("UPDATE sales_log SET pub_state=? WHERE id=?", (pub, rid))
    if priv is not None:
        conn.execute("UPDATE sales_log SET priv_state=? WHERE id=?", (priv, rid))
    if bump:
        conn.execute("UPDATE sales_log SET attempts=attempts+1 WHERE id=?", (rid,))
    conn.commit()


def pending(conn, limit: int = 20):
    """Sales whose Telegram log has not been delivered yet (for retry loop)."""
    rows = conn.execute(
        f"SELECT {','.join(COLS)} FROM sales_log WHERE (pub_state=0 OR priv_state=0) AND attempts<? "
        "ORDER BY id LIMIT ?", (MAX_ATTEMPTS, limit)).fetchall()
    return [_row(r) for r in rows]


# ── read / stats ───────────────────────────────────
def _where(since: float | None, until: float | None):
    parts, args = [], []
    if since is not None:
        parts.append("created_at>=?"); args.append(since)
    if until is not None:
        parts.append("created_at<?"); args.append(until)
    return (" WHERE " + " AND ".join(parts)) if parts else "", args


def summary(conn, since: float | None = None, until: float | None = None) -> dict:
    w, a = _where(since, until)
    r = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(quantity),0), COALESCE(SUM(amount),0), "
        "COALESCE(SUM(CASE WHEN cost IS NOT NULL THEN cost END),0), "
        "COALESCE(SUM(CASE WHEN cost IS NOT NULL THEN amount END),0), "
        "COALESCE(SUM(CASE WHEN cost IS NULL THEN 1 ELSE 0 END),0) "
        f"FROM sales_log{w}", a).fetchone()
    orders, qty, revenue, cost, rev_known, unknown = r
    return {"orders": orders, "qty": qty, "revenue": revenue, "cost": cost,
            "profit": rev_known - cost, "unknown_cost": unknown}


def by_source(conn, since=None, until=None) -> list[dict]:
    w, a = _where(since, until)
    rows = conn.execute(
        "SELECT source, COUNT(*), COALESCE(SUM(amount),0), "
        "COALESCE(SUM(CASE WHEN cost IS NOT NULL THEN amount-cost END),0) "
        f"FROM sales_log{w} GROUP BY source ORDER BY 3 DESC", a).fetchall()
    return [{"source": s, "orders": n, "revenue": rv, "profit": pf} for s, n, rv, pf in rows]


def by_provider(conn, since=None, until=None) -> list[dict]:
    w, a = _where(since, until)
    w2 = (w + " AND " if w else " WHERE ") + "provider<>''"
    rows = conn.execute(
        "SELECT provider, COUNT(*), COALESCE(SUM(amount),0), "
        "COALESCE(SUM(CASE WHEN cost IS NOT NULL THEN amount-cost END),0) "
        f"FROM sales_log{w2} GROUP BY provider ORDER BY 3 DESC", a).fetchall()
    return [{"provider": p, "orders": n, "revenue": rv, "profit": pf} for p, n, rv, pf in rows]


def top_products(conn, since=None, until=None, limit: int = 8) -> list[dict]:
    w, a = _where(since, until)
    rows = conn.execute(
        "SELECT product, SUM(quantity), COUNT(*), COALESCE(SUM(amount),0), "
        "COALESCE(SUM(CASE WHEN cost IS NOT NULL THEN amount-cost END),0) "
        f"FROM sales_log{w} GROUP BY product ORDER BY 2 DESC, 4 DESC LIMIT ?", (*a, limit)).fetchall()
    return [{"product": p, "qty": q, "orders": n, "revenue": rv, "profit": pf} for p, q, n, rv, pf in rows]


def bestseller_names(conn, days: int = 30, limit: int = 3) -> set[str]:
    since = time.time() - days * 86400
    return {r["product"] for r in top_products(conn, since, None, limit) if r["qty"] >= 2}


def recent(conn, limit: int = 10, offset: int = 0) -> list[dict]:
    rows = conn.execute(
        f"SELECT {','.join(COLS)} FROM sales_log ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [_row(r) for r in rows]


def count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM sales_log").fetchone()[0]


def daily_series(conn, days: int = 7, now: float | None = None) -> list[dict]:
    """Per-day orders / revenue / profit for the last `days` days (IST), oldest first."""
    now_dt = datetime.fromtimestamp(now or time.time(), IST)
    start = (now_dt - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for i in range(days):
        d0 = start + timedelta(days=i)
        d1 = d0 + timedelta(days=1)
        s = summary(conn, d0.timestamp(), d1.timestamp())
        out.append({"date": d0.strftime("%d %b"), **s})
    return out


def day_bounds(now: float | None = None, days_back: int = 0) -> tuple[float, float]:
    n = datetime.fromtimestamp(now or time.time(), IST)
    d0 = n.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_back)
    return d0.timestamp(), (d0 + timedelta(days=1)).timestamp()


def export_csv(conn) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "sale_id", "time_ist", "source", "provider", "user_id", "buyer", "product",
                "quantity", "amount", "cost", "profit", "method", "admin_id", "note"])
    for r in conn.execute(f"SELECT {','.join(COLS)} FROM sales_log ORDER BY id").fetchall():
        d = _row(r)
        w.writerow([d["id"], d["sale_id"], fmt_dt(d["created_at"]), d["source"], d["provider"],
                    d["user_id"], d["buyer"], d["product"], d["quantity"], d["amount"],
                    "" if d["cost"] is None else d["cost"],
                    "" if d["profit"] is None else d["profit"], d["method"], d["admin_id"], d["note"]])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")   # BOM -> opens fine in Excel


# ── manual sale parsing ────────────────────────────
def parse_manual_line(text: str) -> dict:
    """'Apple Music 5M | 1 | 149 | 100 | @buyer note'  (qty, cost, buyer optional)

    Parts: product | quantity | total price | [cost] | [buyer / note]
    """
    parts = [p.strip() for p in (text or "").split("|")]
    if len(parts) < 3:
        raise ValueError("Format: product | quantity | price | [cost] | [buyer/note]")
    product = parts[0]
    if not product:
        raise ValueError("Product ka naam khali hai")
    try:
        qty = int(float(parts[1]))
        amount = float(parts[2].replace("₹", "").replace(",", ""))
    except ValueError:
        raise ValueError("Quantity aur price number hone chahiye") from None
    if qty < 1 or qty > 1000:
        raise ValueError("Quantity 1 se 1000 ke beech")
    if amount < 0:
        raise ValueError("Price negative nahi ho sakta")
    cost = None
    if len(parts) >= 4 and parts[3] != "":
        try:
            cost = float(parts[3].replace("₹", "").replace(",", ""))
        except ValueError:
            raise ValueError("Cost number hona chahiye (ya khali chhodo)") from None
        if cost < 0:
            raise ValueError("Cost negative nahi ho sakta")
    buyer = parts[4] if len(parts) >= 5 else ""
    uid = 0
    m = re.search(r"\b(\d{6,12})\b", buyer)
    if m:
        uid = int(m.group(1))
    return {"product": product[:120], "quantity": qty, "amount": amount, "cost": cost,
            "buyer": buyer[:80], "user_id": uid}


# ── formatting ─────────────────────────────────────
def fmt_dt(ts: float) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%d %b %Y %I:%M %p") + " IST"


def mask_id(user_id: int, mode: str = "full") -> str:
    s = str(int(user_id or 0)) if user_id else ""
    n = len(s) if len(s) >= 6 else 10
    if mode == "none":
        return s or "—"
    if mode == "partial" and s:
        return s[:2] + "*" * max(3, n - 4) + s[-2:]
    return "*" * n


def _money(v: float) -> str:
    return f"₹{v:,.0f}" if abs(v - round(v)) < 0.005 else f"₹{v:,.2f}"


def format_public(s: dict, *, show_price: bool = True, mask_mode: str = "full") -> str:
    """Channel post — same look as the reference screenshot."""
    e = html.escape
    lines = [
        f"🆔 ID: {mask_id(s['user_id'], mask_mode)}",
        f"💲 Product: {e(s['product'])}",
        f"🔢 Quantity: {s['quantity']}",
    ]
    if show_price:
        lines.append(f"💰 Price: {_money(s['amount'])}")
    lines.append(f"📅 Time: {fmt_dt(s['created_at'])}")
    return "🛍️ <b>New Purchase!</b>\n<blockquote>" + "\n".join(lines) + "</blockquote>"


def format_private(s: dict) -> str:
    """Detailed admin-only log with cost and profit."""
    e = html.escape
    src = SOURCES.get(s["source"], s["source"])
    if s["provider"]:
        src += f" · {e(s['provider'])}"
    buyer = f"<code>{s['user_id']}</code>" if s["user_id"] else "—"
    if s["buyer"]:
        buyer += f" {e(s['buyer'])}"
    if s["profit"] is None:
        money = f"💰 Sold at: <b>{_money(s['amount'])}</b>\n🏷️ Cost: unknown | 📈 Profit: n/a"
    else:
        sign = "📈" if s["profit"] >= 0 else "📉"
        money = (f"💰 Sold at: <b>{_money(s['amount'])}</b>\n"
                 f"🏷️ Cost: {_money(s['cost'])} | {sign} Profit: <b>{_money(s['profit'])}</b>")
    lines = [
        f"🛍️ Product: {e(s['product'])}",
        f"🔢 Quantity: {s['quantity']}",
        money,
        f"📦 Source: {src}",
    ]
    if s["method"]:
        lines.append(f"💳 Paid via: {e(s['method'])}")
    lines.append(f"👤 Buyer: {buyer}")
    if s["sale_id"]:
        lines.append(f"🧾 Ref: <code>{e(s['sale_id'])}</code>")
    if s["admin_id"]:
        lines.append(f"👮 By admin: <code>{s['admin_id']}</code>")
    if s["note"]:
        lines.append(f"📝 {e(s['note'])}")
    lines.append(f"📅 {fmt_dt(s['created_at'])}")
    return f"🧾 <b>SALE LOGGED #{s['id']}</b>\n<blockquote>" + "\n".join(lines) + "</blockquote>"


def format_summary_block(label: str, s: dict) -> str:
    extra = f" (+{s['unknown_cost']} bina cost)" if s["unknown_cost"] else ""
    return (f"{label}: <b>{s['orders']}</b> sales | qty {s['qty']} | "
            f"revenue <b>{_money(s['revenue'])}</b> | profit <b>{_money(s['profit'])}</b>{extra}")


def format_report(conn, now: float | None = None) -> str:
    t0, t1 = day_bounds(now)
    y0, y1 = day_bounds(now, 1)
    n = now or time.time()
    today = summary(conn, t0, t1)
    yest = summary(conn, y0, y1)
    week = summary(conn, n - 7 * 86400)
    month = summary(conn, n - 30 * 86400)
    allt = summary(conn)
    lines = [
        "📊 <b>SALES REPORT</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        format_summary_block("📅 Today", today),
        format_summary_block("🕘 Yesterday", yest),
        format_summary_block("🗓️ 7 days", week),
        format_summary_block("📆 30 days", month),
        format_summary_block("♾️ All time", allt),
    ]
    src = by_source(conn)
    if src:
        lines += ["", "<b>📦 By source</b>"]
        lines += [f"• {SOURCES.get(r['source'], r['source'])}: {r['orders']} | {_money(r['revenue'])} | profit {_money(r['profit'])}"
                  for r in src]
    prov = by_provider(conn)
    if prov:
        lines += ["", "<b>🔌 By API</b>"]
        lines += [f"• {html.escape(r['provider'])}: {r['orders']} | {_money(r['revenue'])} | profit {_money(r['profit'])}"
                  for r in prov]
    top = top_products(conn, n - 30 * 86400, None, 5)
    if top:
        lines += ["", "<b>🏆 Top products (30 days)</b>"]
        lines += [f"{i}. {html.escape(r['product'])} — {r['qty']} sold | {_money(r['revenue'])}"
                  for i, r in enumerate(top, 1)]
    ser = daily_series(conn, 7, now)
    if any(d["orders"] for d in ser):
        mx = max(d["revenue"] for d in ser) or 1
        lines += ["", "<b>📈 Last 7 days</b>"]
        for d in ser:
            bar = "█" * int(round(8 * d["revenue"] / mx)) or ("▏" if d["orders"] else "·")
            lines.append(f"<code>{d['date']}</code> {bar} {d['orders']} | {_money(d['revenue'])}")
    return "\n".join(lines)
