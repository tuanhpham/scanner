"""store.py — trạng thái nhỏ dùng chung, ghi vào state/baseline.db (đã WAL)."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS alert_msg(
  sym TEXT, day TEXT, message_id INTEGER, snap TEXT, ts TEXT,
  PRIMARY KEY(sym, day));
CREATE TABLE IF NOT EXISTS watch(
  sym TEXT PRIMARY KEY, kind TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


def _con(db: str | Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db), timeout=10)
    c.executescript(DDL)
    return c


def _today() -> str:
    return dt.date.today().isoformat()


def put_msg(db, sym: str, message_id: int, snap: dict | None = None) -> None:
    with _con(db) as c:
        c.execute("INSERT INTO alert_msg(sym,day,message_id,snap,ts) "
                  "VALUES(?,?,?,?,?) ON CONFLICT(sym,day) DO UPDATE SET "
                  "message_id=excluded.message_id, snap=excluded.snap, "
                  "ts=excluded.ts",
                  (sym, _today(), message_id, json.dumps(snap or {}),
                   dt.datetime.now(dt.timezone.utc).isoformat()))


def put_snap(db, sym: str, snap: dict) -> None:
    with _con(db) as c:
        c.execute("UPDATE alert_msg SET snap=? WHERE sym=? AND day=?",
                  (json.dumps(snap), sym, _today()))


def get_msg(db, sym: str) -> tuple[int | None, dict | None]:
    with _con(db) as c:
        r = c.execute("SELECT message_id, snap FROM alert_msg "
                      "WHERE sym=? AND day=?", (sym, _today())).fetchone()
    if not r:
        return None, None
    try:
        return r[0], json.loads(r[1] or "{}")
    except Exception:                                        # noqa: BLE001
        return r[0], None


def get_snap(db, sym: str) -> dict | None:
    return get_msg(db, sym)[1] or None


def set_watch(db, sym: str, kind: str, on: bool) -> None:
    with _con(db) as c:
        if on:
            c.execute("INSERT OR REPLACE INTO watch VALUES(?,?,?)",
                      (sym, kind, dt.datetime.now(dt.timezone.utc).isoformat()))
        else:
            c.execute("DELETE FROM watch WHERE sym=? AND kind=?", (sym, kind))


def watch_state(db, sym: str) -> tuple[bool, bool]:
    """(tracked, watched)"""
    with _con(db) as c:
        rows = {r[0] for r in c.execute(
            "SELECT kind FROM watch WHERE sym=?", (sym,))}
    return "track" in rows, "wl" in rows


def get_kv(db, k: str, default: str = "") -> str:
    with _con(db) as c:
        r = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def set_kv(db, k: str, v: str) -> None:
    with _con(db) as c:
        c.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, str(v)))
