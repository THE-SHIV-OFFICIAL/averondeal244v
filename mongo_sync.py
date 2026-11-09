"""MongoDB persistence for the bot.

How it works
------------
* MongoDB is the permanent store: every table (users, orders, wallet logs,
  payments, referrals, settings, supplier orders, Service 1 orders ...) is
  mirrored as a MongoDB collection with one document per row.
* A local SQLite file is kept only as a fast working cache. SQLite triggers
  record every INSERT / UPDATE / DELETE in `_mongo_changes`; a background loop
  pushes those changes to MongoDB every few seconds.
* On boot, if the local cache is empty (fresh server / Render restart) the
  whole database is restored from MongoDB. If MongoDB is empty but the local
  file has data (first run after upgrade), all old data is uploaded once —
  so nothing old is lost.

Set MONGODB_URI (and optionally MONGODB_DB) in .env. Without MONGODB_URI the
bot still runs on SQLite only.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time

log = logging.getLogger("mongo_sync")

SKIP_TABLES = {"_mongo_changes", "sqlite_sequence"}
SCHEMA_COLL = "_schema"
_client = None
_db = None


def enabled() -> bool:
    return _db is not None


def connect() -> bool:
    global _client, _db
    uri = (os.getenv("MONGODB_URI") or "").strip()
    if not uri:
        log.warning("MONGODB_URI not set — running on local SQLite only")
        return False
    from pymongo import MongoClient
    _client = MongoClient(uri, serverSelectionTimeoutMS=15000)
    _client.admin.command("ping")
    _db = _client[(os.getenv("MONGODB_DB") or "averon_bot").strip()]
    return True


def _tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    return [r[0] for r in rows if r[0] not in SKIP_TABLES]


def _cols(conn, table):
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


def _doc(cols, row):
    d = {"_id": row[0]}
    for k, v in zip(cols, row[1:]):
        d[k] = v
    return d


# ── restore / initial upload ────────────────────────
def restore_if_needed(db_path: str) -> str:
    """Call BEFORE init_db(). Returns what happened (for the startup log)."""
    if not enabled():
        return "off"
    conn = sqlite3.connect(db_path)
    try:
        local_tables = _tables(conn)
        has_local = False
        for t in local_tables:
            if conn.execute(f'SELECT 1 FROM "{t}" LIMIT 1').fetchone():
                has_local = True
                break
        schemas = list(_db[SCHEMA_COLL].find({}))
        if has_local or not schemas:
            return "local"
        for s in schemas:
            conn.execute(s["sql"].replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)
                         if "IF NOT EXISTS" not in s["sql"] else s["sql"])
        conn.commit()
        n = 0
        for s in schemas:
            t = s["_id"]
            cols = _cols(conn, t)
            for d in _db[t].find({}):
                keys = [c for c in cols if c in d]
                q = (f'INSERT OR REPLACE INTO "{t}" (rowid,{",".join(chr(34)+k+chr(34) for k in keys)}) '
                     f'VALUES (?{",?" * len(keys)})')
                conn.execute(q, [d["_id"], *[d[k] for k in keys]])
                n += 1
        conn.commit()
        return f"restored {n} rows from MongoDB"
    finally:
        conn.close()


def install(db_path: str) -> str:
    """Call AFTER init_db(): adds change triggers for every table and uploads
    everything once if MongoDB has no copy yet."""
    if not enabled():
        return "off"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS _mongo_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tbl TEXT, rid INTEGER)""")
        first_upload = _db[SCHEMA_COLL].count_documents({}) == 0
        for t in _tables(conn):
            for op, ref in (("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD")):
                conn.execute(
                    f'CREATE TRIGGER IF NOT EXISTS "_ms_{t}_{op.lower()}" AFTER {op} ON "{t}" '
                    f"BEGIN INSERT INTO _mongo_changes(tbl,rid) VALUES ('{t}', {ref}.rowid); END")
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0]
            _db[SCHEMA_COLL].replace_one({"_id": t}, {"_id": t, "sql": sql}, upsert=True)
            if first_upload:
                _full_push(conn, t)
        conn.commit()
        return "uploaded old data to MongoDB" if first_upload else "sync ready"
    finally:
        conn.close()


def _full_push(conn, t):
    from pymongo import ReplaceOne
    cols = _cols(conn, t)
    rows = conn.execute(f'SELECT rowid,* FROM "{t}"').fetchall()
    ops = [ReplaceOne({"_id": r[0]}, _doc(cols, r), upsert=True) for r in rows]
    for i in range(0, len(ops), 500):
        _db[t].bulk_write(ops[i:i + 500], ordered=False)


def flush(db_path: str, limit: int = 1000) -> int:
    if not enabled():
        return 0
    from pymongo import ReplaceOne, DeleteOne
    conn = sqlite3.connect(db_path)
    try:
        ch = conn.execute("SELECT id, tbl, rid FROM _mongo_changes ORDER BY id LIMIT ?", (limit,)).fetchall()
        if not ch:
            return 0
        latest = {}
        for cid, t, rid in ch:
            latest[(t, rid)] = cid
        by_table: dict[str, list] = {}
        cols_cache = {}
        for (t, rid) in latest:
            if t not in cols_cache:
                cols_cache[t] = _cols(conn, t)
            row = conn.execute(f'SELECT rowid,* FROM "{t}" WHERE rowid=?', (rid,)).fetchone()
            op = ReplaceOne({"_id": rid}, _doc(cols_cache[t], row), upsert=True) if row else DeleteOne({"_id": rid})
            by_table.setdefault(t, []).append(op)
        for t, ops in by_table.items():
            _db[t].bulk_write(ops, ordered=False)
        conn.execute("DELETE FROM _mongo_changes WHERE id<=?", (ch[-1][0],))
        conn.commit()
        return len(latest)
    finally:
        conn.close()


async def loop(db_path: str, every: float = 3.0):
    """Background task: keeps MongoDB updated. Also picks up tables created later."""
    last_install = time.time()
    while True:
        try:
            await asyncio.to_thread(flush, db_path)
            if time.time() - last_install > 300:
                await asyncio.to_thread(install, db_path)
                last_install = time.time()
        except Exception as e:  # network blips: changes stay queued and retry
            log.warning("mongo sync: %s", type(e).__name__)
        await asyncio.sleep(every)
