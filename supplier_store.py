"""Supplier mapping, pricing/profit maths and response parsing (pure, testable).

No network calls here. All money values stored in INR except supplier cost,
which may be USD (converted with the admin-set USD->INR rate).
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from typing import Any

PROVIDER_IDS = ("couponshops", "acczone", "elitetoolz")


def init_tables(conn: sqlite3.Connection) -> None:
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS supplier_map (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        service_key   TEXT NOT NULL,
        duration      TEXT NOT NULL,
        provider      TEXT NOT NULL,
        product_id    TEXT NOT NULL,
        cost          REAL DEFAULT 0,
        cost_currency TEXT DEFAULT 'INR',
        markup_type   TEXT DEFAULT 'fixed',
        markup_value  REAL DEFAULT 0,
        auto_fulfill  INTEGER DEFAULT 0,
        UNIQUE(service_key, duration)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS supplier_orders (
        order_id     TEXT PRIMARY KEY,
        provider     TEXT,
        product_id   TEXT,
        sale_amount  REAL DEFAULT 0,
        cost         REAL DEFAULT 0,
        status       TEXT,
        supplier_ref TEXT DEFAULT '',
        delivery     TEXT DEFAULT '',
        error        TEXT DEFAULT '',
        created_at   REAL,
        updated_at   REAL
    )""")
    conn.commit()


# ── Pricing ───────────────────────────────────────
def cost_in_inr(cost: float, currency: str, usd_rate: float) -> float:
    cost = float(cost or 0)
    return cost * float(usd_rate) if (currency or "INR").upper() == "USD" else cost


def compute_price(cost: float, currency: str, usd_rate: float,
                  markup_type: str, markup_value: float) -> int:
    """Selling price (whole rupees, rounded up, minimum ₹1)."""
    base = cost_in_inr(cost, currency, usd_rate)
    mv = float(markup_value or 0)
    price = base * (1 + mv / 100) if markup_type == "percent" else base + mv
    return max(1, math.ceil(round(price, 6)))


_ADJ = re.compile(r"^\s*([+-])\s*(\d+(?:\.\d+)?)\s*(%?)\s*$")


def parse_adjust(expr: str):
    """'+10%' -> ('percent', 10); '-15' -> ('fixed', -15). None if invalid."""
    m = _ADJ.match(expr or "")
    if not m:
        return None
    val = float(m.group(2)) * (1 if m.group(1) == "+" else -1)
    return ("percent" if m.group(3) else "fixed", val)


def apply_adjust(price: int, kind: str, val: float) -> int:
    new = price * (1 + val / 100) if kind == "percent" else price + val
    return max(1, int(round(new)))


def bulk_adjust(conn: sqlite3.Connection, expr: str, service_key: str | None = None) -> int:
    """Increase/decrease prices in service_prices. Returns rows changed."""
    parsed = parse_adjust(expr)
    if not parsed:
        raise ValueError("Format: +10% / -5% / +20 / -15")
    kind, val = parsed
    c = conn.cursor()
    if service_key:
        c.execute("SELECT id, price FROM service_prices WHERE service_key=?", (service_key,))
    else:
        c.execute("SELECT id, price FROM service_prices")
    rows = c.fetchall()
    for rid, price in rows:
        c.execute("UPDATE service_prices SET price=? WHERE id=?",
                  (apply_adjust(int(price or 0), kind, val), rid))
    conn.commit()
    return len(rows)


# ── Mapping CRUD ──────────────────────────────────
MAP_COLS = ("id", "service_key", "duration", "provider", "product_id", "cost",
            "cost_currency", "markup_type", "markup_value", "auto_fulfill")


def _row(r):
    return dict(zip(MAP_COLS, r)) if r else None


def parse_mapping_line(text: str) -> dict:
    """'service_key | duration | provider | product_id | cost [| USD]'"""
    parts = [p.strip() for p in (text or "").split("|")]
    if len(parts) not in (5, 6):
        raise ValueError("5 ya 6 parts chahiye: service_key | duration | provider | product_id | cost [| INR/USD]")
    sk, dur, prov, pid, cost = parts[:5]
    cur = (parts[5] if len(parts) == 6 else "INR").upper()
    prov = prov.lower()
    if prov not in PROVIDER_IDS:
        raise ValueError("provider: couponshops / acczone / elitetoolz")
    if not sk or not dur or not pid:
        raise ValueError("service_key, duration, product_id khali nahi ho sakte")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", pid):
        raise ValueError("product_id format galat hai")
    try:
        cost_f = float(cost)
    except ValueError:
        raise ValueError("cost number hona chahiye") from None
    if cost_f < 0:
        raise ValueError("cost negative nahi ho sakta")
    if cur not in ("INR", "USD"):
        raise ValueError("currency INR ya USD")
    return {"service_key": sk, "duration": dur, "provider": prov,
            "product_id": pid, "cost": cost_f, "cost_currency": cur}


def upsert_mapping(conn, m: dict) -> int:
    c = conn.cursor()
    c.execute("""INSERT INTO supplier_map (service_key, duration, provider, product_id, cost, cost_currency)
                 VALUES (?,?,?,?,?,?)
                 ON CONFLICT(service_key, duration) DO UPDATE SET
                   provider=excluded.provider, product_id=excluded.product_id,
                   cost=excluded.cost, cost_currency=excluded.cost_currency""",
              (m["service_key"], m["duration"], m["provider"], m["product_id"],
               m["cost"], m["cost_currency"]))
    conn.commit()
    c.execute("SELECT id FROM supplier_map WHERE service_key=? AND duration=?",
              (m["service_key"], m["duration"]))
    return c.fetchone()[0]


def get_mapping(conn, map_id: int):
    c = conn.cursor()
    c.execute(f"SELECT {','.join(MAP_COLS)} FROM supplier_map WHERE id=?", (map_id,))
    return _row(c.fetchone())


def find_mapping(conn, service_key: str, duration: str):
    c = conn.cursor()
    c.execute(f"SELECT {','.join(MAP_COLS)} FROM supplier_map WHERE service_key=? AND duration=?",
              (service_key, duration))
    return _row(c.fetchone())


def list_mappings(conn):
    c = conn.cursor()
    c.execute(f"SELECT {','.join(MAP_COLS)} FROM supplier_map ORDER BY service_key, duration")
    return [_row(r) for r in c.fetchall()]


def update_mapping(conn, map_id: int, **fields):
    allowed = {"cost", "cost_currency", "markup_type", "markup_value", "auto_fulfill", "product_id"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    conn.execute(f"UPDATE supplier_map SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                 (*sets.values(), map_id))
    conn.commit()


def delete_mapping(conn, map_id: int):
    conn.execute("DELETE FROM supplier_map WHERE id=?", (map_id,))
    conn.commit()


def parse_markup(text: str):
    """'20%' -> ('percent', 20); '50' -> ('fixed', 50)."""
    t = (text or "").strip().replace("₹", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(%?)", t)
    if not m:
        raise ValueError("Format: 50 (₹ fixed) ya 20% (percent)")
    return ("percent" if m.group(2) else "fixed", float(m.group(1)))


def apply_mapping_price(conn, m: dict, usd_rate: float) -> int:
    price = compute_price(m["cost"], m["cost_currency"], usd_rate, m["markup_type"], m["markup_value"])
    conn.execute("UPDATE service_prices SET price=? WHERE service_key=? AND duration=?",
                 (price, m["service_key"], m["duration"]))
    conn.commit()
    return price


# ── Supplier orders (idempotency + profit) ────────
def claim_order(conn, order_id, provider, product_id, sale_amount, cost_inr) -> bool:
    """Insert a 'placing' row. False if this order was already sent to a supplier."""
    now = time.time()
    try:
        conn.execute("""INSERT INTO supplier_orders
            (order_id, provider, product_id, sale_amount, cost, status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (order_id, provider, product_id, float(sale_amount), float(cost_inr), "placing", now, now))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def finish_order(conn, order_id, status, supplier_ref="", delivery="", error=""):
    conn.execute("""UPDATE supplier_orders SET status=?, supplier_ref=?, delivery=?, error=?, updated_at=?
                    WHERE order_id=?""",
                 (status, str(supplier_ref)[:100], delivery, str(error)[:300], time.time(), order_id))
    conn.commit()


def get_supplier_order(conn, order_id):
    c = conn.cursor()
    c.execute("SELECT order_id, provider, product_id, sale_amount, cost, status, supplier_ref, delivery, error "
              "FROM supplier_orders WHERE order_id=?", (order_id,))
    r = c.fetchone()
    keys = ("order_id", "provider", "product_id", "sale_amount", "cost", "status",
            "supplier_ref", "delivery", "error")
    return dict(zip(keys, r)) if r else None


def profit_summary(conn, since_ts: float | None = None) -> dict:
    """Profit of supplier-delivered orders (sale - supplier cost)."""
    q = "SELECT provider, COUNT(*), COALESCE(SUM(sale_amount),0), COALESCE(SUM(cost),0) FROM supplier_orders WHERE status='delivered'"
    args: tuple = ()
    if since_ts is not None:
        q += " AND created_at>=?"
        args = (since_ts,)
    q += " GROUP BY provider"
    c = conn.cursor()
    c.execute(q, args)
    per = {p: {"orders": n, "sales": s, "cost": k, "profit": s - k} for p, n, s, k in c.fetchall()}
    tot = {"orders": sum(v["orders"] for v in per.values()),
           "sales": sum(v["sales"] for v in per.values()),
           "cost": sum(v["cost"] for v in per.values())}
    tot["profit"] = tot["sales"] - tot["cost"]
    return {"providers": per, "total": tot}


def counts_by_status(conn) -> dict:
    c = conn.cursor()
    c.execute("SELECT status, COUNT(*) FROM supplier_orders GROUP BY status")
    return dict(c.fetchall())


# ── Response parsing (shapes differ per supplier) ─
_ID_KEYS = ("id", "service_id", "serviceId", "product_id", "productId", "service_key", "key", "code")
_NAME_KEYS = ("name", "title", "service_name", "product_name", "service", "product")
_PRICE_KEYS = ("price", "rate", "cost", "amount", "price_usd", "price_inr")
_STOCK_KEYS = ("stock", "available", "quantity", "qty", "in_stock")
_LIST_KEYS = ("data", "services", "products", "items", "result", "results")


def _first(d: dict, keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def catalog_items(resp: Any) -> list[dict]:
    items = resp
    for _ in range(3):
        if isinstance(items, dict):
            nxt = _first(items, _LIST_KEYS)
            if nxt is None:
                # dict keyed by id -> values
                vals = list(items.values())
                if vals and all(isinstance(v, dict) for v in vals):
                    items = [{"id": k, **v} for k, v in items.items()]
                break
            items = nxt
        else:
            break
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if str(it.get("is_active", 1)) in ("0", "False", "false"):
            continue
        price = _first(it, _PRICE_KEYS)
        price_inr = it.get("price_inr")
        tiers = it.get("pricing")
        if isinstance(tiers, list) and tiers and isinstance(tiers[0], dict):
            t = tiers[0]
            if price_inr is None and t.get("price_inr") is not None:
                price_inr = t.get("price_inr")
            if price is None:
                price = t.get("price_usdt", t.get("price_usd", t.get("price")))
        out.append({
            "id": str(_first(it, _ID_KEYS) or "?"),
            "name": str(_first(it, _NAME_KEYS) or "-")[:60],
            "price": price,
            "price_inr": price_inr,
            "stock": _first(it, _STOCK_KEYS),
            "desc": str(it.get("description") or it.get("warranty") or "")[:700],
        })
    return out


def balance_text(resp: Any) -> str:
    if isinstance(resp, (int, float, str)):
        return str(resp)
    if isinstance(resp, dict):
        for k in ("balance", "wallet", "credits", "amount"):
            if k in resp:
                v = resp[k]
                cur = resp.get("currency", "")
                return f"{v} {cur}".strip()
        for k in _LIST_KEYS:
            if isinstance(resp.get(k), dict):
                return balance_text(resp[k])
    return json.dumps(resp, ensure_ascii=False)[:200]


_DELIVERY_KEYS = ("delivery", "deliveries", "codes", "coupons", "coupon", "accounts", "account",
                  "credentials", "items", "keys", "licenses", "details", "content", "code")
_REF_KEYS = ("order_id", "orderId", "id", "reference", "ref", "transaction_id")


def is_error_response(resp: Any) -> str | None:
    if isinstance(resp, dict):
        if resp.get("success") is False or resp.get("ok") is False:
            return str(resp.get("message") or resp.get("error") or "supplier ne order reject kiya")[:200]
        st = str(resp.get("status", "")).lower()
        if st in ("error", "failed", "fail", "rejected", "cancelled"):
            return str(resp.get("message") or resp.get("error") or st)[:200]
        if resp.get("error") and not _first(resp, _DELIVERY_KEYS):
            return str(resp.get("error"))[:200]
    return None


def _flatten(v: Any) -> str:
    if isinstance(v, list):
        return "\n".join(_flatten(x) for x in v if x not in (None, ""))
    if isinstance(v, dict):
        return "\n".join(f"{k}: {_flatten(x)}" for k, x in v.items() if x not in (None, ""))
    return str(v)


def extract_delivery(resp: Any) -> str:
    if not isinstance(resp, dict):
        return _flatten(resp) if resp not in (None, "") else ""
    for k in _DELIVERY_KEYS:
        if resp.get(k) not in (None, "", [], {}):
            return _flatten(resp[k])[:3500]
    for k in _LIST_KEYS:
        if isinstance(resp.get(k), (dict, list)):
            got = extract_delivery(resp[k]) if isinstance(resp[k], dict) else _flatten(resp[k])
            if got:
                return got[:3500]
    return ""


def extract_ref(resp: Any) -> str:
    if isinstance(resp, dict):
        v = _first(resp, _REF_KEYS)
        if v is not None:
            return str(v)
        for k in _LIST_KEYS:
            if isinstance(resp.get(k), dict):
                return extract_ref(resp[k])
    return ""
