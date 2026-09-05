"""store.py — trang thai nho dung chung: message_id, snapshot, watchlist, offset.

Ghi vao state/baseline.db (da bat WAL) trong 3 bang rieng, khong dinh
gi toi bang `base` cua prep.py hay bang `alerts` cua main.py.

  alert_msg  sym + ngay -> message_id + so lieu lan truoc (de tinh delta)
  watch      danh sach ma dang theo doi trong phien (kind='track')
  kv         cap khoa-gia tri, hien dung cho tg_offset cua getUpdates

Moi ham deu bat loi va tra ve gia tri mac dinh: DB phu nay hong thi
alert van phai gui duoc.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from contextlib import closing
from pathlib import Path

log = print          # main.py co the gan lai: store.log = log

DDL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS alert_msg(
  sym TEXT, day TEXT, message_id INTEGER, snap TEXT, ts TEXT,
  PRIMARY KEY(sym, day));
CREATE TABLE IF NOT EXISTS watch(
  sym TEXT, kind TEXT, ts TEXT, PRIMARY KEY(sym, kind));
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


def _con(db: str | Path) -> sqlite3.Connection:
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(db), timeout=15)
    c.execute("PRAGMA busy_timeout=15000")
    c.executescript(DDL)
    return c


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    """Ngay theo gio ET: alert cua phien nao thi thuoc ngay do."""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:                                            # noqa: BLE001
        return dt.date.today().isoformat()


# ───────────────────────── alert_msg ─────────────────────────
def put_msg(db, sym: str, message_id: int, snap: dict | None = None) -> None:
    """Ghi message_id sau khi gui alert, kem snapshot so lieu."""
    try:
        with closing(_con(db)) as c, c:
            c.execute(
                "INSERT INTO alert_msg(sym,day,message_id,snap,ts) "
                "VALUES(?,?,?,?,?) ON CONFLICT(sym,day) DO UPDATE SET "
                "message_id=excluded.message_id, snap=excluded.snap, "
                "ts=excluded.ts",
                (sym, _today(), int(message_id),
                 json.dumps(snap or {}, ensure_ascii=False), _now()))
    except Exception as e:                                       # noqa: BLE001
        log(f"store.put_msg {sym}: {type(e).__name__}: {e}")


def put_snap(db, sym: str, snap: dict) -> None:
    """Cap nhat snapshot sau khi Refresh, giu nguyen message_id."""
    try:
        with closing(_con(db)) as c, c:
            n = c.execute(
                "UPDATE alert_msg SET snap=?, ts=? WHERE sym=? AND day=?",
                (json.dumps(snap or {}, ensure_ascii=False), _now(),
                 sym, _today())).rowcount
            if not n:      # chua co dong nao (vd alert phai gui bu qua spool)
                c.execute(
                    "INSERT INTO alert_msg(sym,day,message_id,snap,ts) "
                    "VALUES(?,?,?,?,?)",
                    (sym, _today(), 0,
                     json.dumps(snap or {}, ensure_ascii=False), _now()))
    except Exception as e:                                       # noqa: BLE001
        log(f"store.put_snap {sym}: {type(e).__name__}: {e}")


def get_msg(db, sym: str) -> tuple[int | None, dict | None]:
    """(message_id, snapshot) cua alert hom nay. (None, None) neu chua co."""
    try:
        with closing(_con(db)) as c:
            r = c.execute("SELECT message_id, snap FROM alert_msg "
                          "WHERE sym=? AND day=?", (sym, _today())).fetchone()
    except Exception as e:                                       # noqa: BLE001
        log(f"store.get_msg {sym}: {type(e).__name__}: {e}")
        return None, None
    if not r:
        return None, None
    mid = r[0] or None
    try:
        return mid, json.loads(r[1] or "{}")
    except Exception:                                            # noqa: BLE001
        return mid, None


def get_snap(db, sym: str) -> dict | None:
    """Snapshot lan truoc, de render.py ve mui ten delta."""
    return get_msg(db, sym)[1] or None


def purge_msg(db, keep_days: int = 7) -> int:
    """Don dong cu hon keep_days. Goi luc sang ngay moi, khong bat buoc."""
    cut = (dt.date.today() - dt.timedelta(days=keep_days)).isoformat()
    try:
        with closing(_con(db)) as c, c:
            return c.execute("DELETE FROM alert_msg WHERE day < ?",
                             (cut,)).rowcount
    except Exception as e:                                       # noqa: BLE001
        log(f"store.purge_msg: {type(e).__name__}: {e}")
        return 0


# ───────────────────────── watch (chi con kind='track') ─────────────────────
# Cot `kind` giu lai de DB cu khong phai migrate. Chi ghi 'track'; dong 'wl'
# cua ban truoc nam im va bi prune_track() don theo tuoi.
KIND = "track"


def set_track(db, sym: str, on: bool) -> None:
    """Bat/tat theo doi mot ma. Theo doi chi co hieu luc trong phien."""
    try:
        with closing(_con(db)) as c, c:
            if on:
                c.execute("INSERT OR REPLACE INTO watch(sym,kind,ts) "
                          "VALUES(?,?,?)", (sym, KIND, _now()))
            else:
                c.execute("DELETE FROM watch WHERE sym=? AND kind=?",
                          (sym, KIND))
    except Exception as e:                                       # noqa: BLE001
        log(f"store.set_track {sym}: {type(e).__name__}: {e}")


def is_tracked(db, sym: str) -> bool:
    try:
        with closing(_con(db)) as c:
            return c.execute("SELECT 1 FROM watch WHERE sym=? AND kind=?",
                             (sym, KIND)).fetchone() is not None
    except Exception as e:                                       # noqa: BLE001
        log(f"store.is_tracked {sym}: {type(e).__name__}: {e}")
        return False


def tracked_syms(db) -> list[str]:
    """Danh sach ma dang theo doi, moi nhat truoc."""
    try:
        with closing(_con(db)) as c:
            return [r[0] for r in c.execute(
                "SELECT sym FROM watch WHERE kind=? ORDER BY ts DESC",
                (KIND,))]
    except Exception as e:                                       # noqa: BLE001
        log(f"store.tracked_syms: {type(e).__name__}: {e}")
        return []


def prune_track(db, max_age_h: int = 18) -> int:
    """Xoa moi dong watch cu hon max_age_h gio.

    Theo doi la viec cua MOT phien. Dung tuoi thay vi mo ngay vi bot co the
    restart giua phien - neu xoa moi lan khoi dong thi mat het danh sach.
    18 gio du de qua mot dem (16:00 ET -> 09:30 ET hom sau la 17.5 gio).
    """
    cut = (dt.datetime.now(dt.timezone.utc)
           - dt.timedelta(hours=max_age_h)).isoformat(timespec="seconds")
    try:
        with closing(_con(db)) as c, c:
            return c.execute("DELETE FROM watch WHERE ts < ?", (cut,)).rowcount
    except Exception as e:                                       # noqa: BLE001
        log(f"store.prune_track: {type(e).__name__}: {e}")
        return 0


# ───────────────────────── kv ─────────────────────────
def get_kv(db, k: str, default: str = "") -> str:
    try:
        with closing(_con(db)) as c:
            r = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    except Exception as e:                                       # noqa: BLE001
        log(f"store.get_kv {k}: {type(e).__name__}: {e}")
        return default
    return r[0] if r and r[0] is not None else default


def set_kv(db, k: str, v) -> None:
    try:
        with closing(_con(db)) as c, c:
            c.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)",
                      (k, str(v)))
    except Exception as e:                                       # noqa: BLE001
        log(f"store.set_kv {k}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else str(
        Path(__file__).resolve().parent / "state" / "baseline.db")
    put_msg(d, "_TEST", 999, {"score": 8.3, "rvol": 66.2})
    print("get_msg :", get_msg(d, "_TEST"))
    put_snap(d, "_TEST", {"score": 9.1, "rvol": 70.0})
    print("get_snap:", get_snap(d, "_TEST"))
    set_track(d, "_TEST", True)
    print("track   :", is_tracked(d, "_TEST"), tracked_syms(d))
    set_track(d, "_TEST", False)
    print("bo track:", is_tracked(d, "_TEST"))
    set_kv(d, "_test_kv", 42)
    print("kv      :", get_kv(d, "_test_kv"))
    with closing(_con(d)) as c, c:
        c.execute("DELETE FROM alert_msg WHERE sym='_TEST'")
        c.execute("DELETE FROM kv WHERE k='_test_kv'")
    print("dọn sạch, ok")
