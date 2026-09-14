"""push.py - day snapshot cua scanner len Cloudflare D1 qua /api/scanner.

Doi lai voi moi file khac trong repo: file nay KHONG chay ben trong process
scanner. Cron goi no. Ly do la mot dieu da thay ro trong Phase 1-8: mot cuoc goi
HTTP treo trong vong lap asyncio thi lam DUNG ca vong quet, va o day thu bi treo
la mot dashboard - thu it quan trong nhat trong ca he thong. Nen:

    main.py ghi mot dong `beat` vao bang `kv`   (nhanh, khong ra mang)
    push.py doc DB roi day len cloud            (ra mang, process rieng, chet duoc)

Scanner khong bao gio phu thuoc vao file nay. Xoa push.py di thi bot van chay y
nguyen.

KHONG co spool. notifier.Spool giu tin Telegram qua mat mang vi mot alert bo lo
la mot alert mat han; snapshot thi nguoc lai - ban cu vo gia tri ngay khi co ban
moi, nen mat mang thi bo qua vong nay, phut sau day lai.

Bien moi truong (.env):
    SCANNER_PUSH_URL   https://the-professional.pages.dev/api/scanner
    SCANNER_TOKEN      dat bang `wrangler pages secret put SCANNER_TOKEN`
Thieu mot trong hai -> module tat, khong bao loi. Day la mac dinh: repo nay clone
ve may nao cung phai chay duoc ma khong can Cloudflare.

Thuan stdlib -> selftest chay duoc tren may dev khong co thu vien va khong co DB.

    python push.py                 # selftest, khong mang, khong DB
    python push.py --status        # day scanner:status        (cron moi phut)
    python push.py --all           # day ca snapshot nang      (cron moi toi)
    python push.py --dry           # dung payload roi in ra, khong gui
    python push.py --get config    # doc nguoc scanner:config ve
    python push.py --ping          # kiem tra token
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "state" / "baseline.db"
SPOOL = ROOT / "state" / "spool.json"
SEEN = ROOT / "state" / "push.json"      # digest da day, de khong day lai y nguyen

try:                                     # may dev khong co python-dotenv
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:                        # noqa: BLE001
    pass

URL = os.getenv("SCANNER_PUSH_URL", "").strip().rstrip("/")
TOKEN = os.getenv("SCANNER_TOKEN", "").strip()

TIMEOUT = 15
TRIES = 3
MAX_BYTES = 500_000     # phia function chan o 512_000; chan som de loi ro rang
TOP_N = 120             # so candidate moi setup day len - du de xem, khong phinh
KEEP_DAYS = 10          # so ngay alert giu tren cloud

log = print

# Nhung khoa doi moi lan dung payload nhung khong mang thong tin gi moi. Neu
# tinh digest ke ca chung thi khong lan nao "khong doi", va D1 an du 1440
# write/ngay chi cho mot con dong ho.
VOLATILE = ("ts", "age_sec")


# ───────────────────────── gui ─────────────────────────
def ready() -> bool:
    return bool(URL and TOKEN)


def _req(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Mot cuoc goi. Tra (status, json). Khong nem ra ngoai: loi mang tra (0, ...).

    Token chi nam trong header, khong bao gio vao URL hay vao log - URL bi ghi
    ra state/prep.log, header thi khong.
    """
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{URL}/{path.lstrip('/')}", data=data, method=method)
    r.add_header("X-Scanner-Token", TOKEN)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=TIMEOUT) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:                                        # noqa: BLE001
            return e.code, {}
    except Exception as e:                                       # noqa: BLE001
        return 0, {"error": f"{type(e).__name__}: {e}"}


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """_req + thu lai. Chi thu lai loi mang va 5xx.

    403/413/401 la loi cua minh, thu lai 3 lan khong sua duoc gi ngoai viec lam
    cham cron di 30 giay.
    """
    for i in range(TRIES):
        st, js = _req(method, path, body)
        if st and st < 500:
            return st, js
        if i < TRIES - 1:
            time.sleep(1.5 * (i + 1))
    return st, js


def _seen() -> dict:
    try:
        return json.loads(SEEN.read_text())
    except Exception:                                            # noqa: BLE001
        return {}


def _digest(value) -> str:
    """Digest cua payload sau khi bo cac khoa VOLATILE o tang tren cung."""
    if isinstance(value, dict):
        value = {k: v for k, v in value.items() if k not in VOLATILE}
    raw = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def put(key: str, value, force: bool = False, dry: bool = False) -> str:
    """Day mot khoa. Tra ve mot tu de log: sent/same/off/dry/err.

    "same" la co y va quan trong: D1 free co han so write moi ngay. Bang
    `candidates` doi mot lan moi toi, con cron --status chay moi phut - neu moi
    lan deu ghi thi 1400 write/ngay bi tieu vao viec ghi lai dung thu vua ghi.
    """
    if value is None:
        return "skip"
    raw = json.dumps(value, default=str)
    if len(raw) > MAX_BYTES:
        log(f"push: {key} qua to ({len(raw)} bytes > {MAX_BYTES}), bo qua")
        return "err"
    if dry:
        log(f"push[dry]: {key} = {len(raw)} bytes")
        log(raw[:1500] + ("..." if len(raw) > 1500 else ""))
        return "dry"
    if not ready():
        return "off"

    seen, dig = _seen(), _digest(value)
    if not force and seen.get(key) == dig:
        return "same"

    st, js = call("PUT", f"kv/{key}", {"value": value})
    if st != 200:
        log(f"push: {key} -> HTTP {st} {js.get('error', '')}")
        return "err"

    seen[key] = dig
    try:
        SEEN.parent.mkdir(parents=True, exist_ok=True)
        SEEN.write_text(json.dumps(seen, indent=1, sort_keys=True))
    except Exception as e:                                       # noqa: BLE001
        log(f"push: khong ghi duoc {SEEN.name}: {e}")             # khong chi tu
    return "sent"


def get(key: str) -> dict | None:
    """Doc nguoc mot khoa. Dung cho scanner:config / scanner:commands (buoc 5-6)."""
    if not ready():
        return None
    st, js = call("GET", f"kv/{key}")
    if st == 404:
        return None
    if st != 200:
        log(f"push: get {key} -> HTTP {st} {js.get('error', '')}")
        return None
    return js.get("value")


# ───────────────────────── doc DB ─────────────────────────
def _con(db=DB) -> sqlite3.Connection:
    """Mo DB CHI DOC. Uri mode=ro de mot loi lap trinh o day khong the ghi vao
    baseline.db - trong do la 3 nam nen phai mat 30-60 phut moi tai lai."""
    c = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _one(c, sql: str, args=()) -> sqlite3.Row | None:
    """SELECT chiu duoc bang chua ton tai.

    Khong phai phong xa: tren VM moi, `struct` va `candidates` chua co cho den
    khi chay structure.py --build lan dau, va tren may dev thi khong bao gio co.
    Ca hai truong hop dashboard phai hien "chua co" chu khong phai sap.
    """
    try:
        return c.execute(sql, args).fetchone()
    except sqlite3.Error:
        return None


def _rows(c, sql: str, args=()) -> list[sqlite3.Row]:
    try:
        return c.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def _age_days(day: str | None, today: str | None = None) -> int | None:
    if not day:
        return None
    try:
        t = dt.date.fromisoformat(today) if today else dt.date.today()
        return (t - dt.date.fromisoformat(day[:10])).days
    except ValueError:
        return None


def status_payload(db=DB, today: str | None = None) -> dict:
    """Mot trang trang thai. Chi COUNT va MAX -> nhe, chay duoc moi phut.

    `age` la con so dang xem nhat o day. setups.MAX_AGE = 5: bang `candidates`
    cu hon 5 ngay thi load_candidates() tra ve rong va bot IM LANG hoan toan -
    khong loi, khong tin nhan. Da co mot tuan khong alert thi cau hoi dau tien
    la con so nay, khong phai nguong BO.
    """
    out: dict = {"ts": int(time.time() * 1000), "today": today or dt.date.today().isoformat()}
    try:
        c = _con(db)
    except sqlite3.Error as e:
        out["db_error"] = str(e)
        return out
    try:
        if (r := _one(c, "SELECT COUNT(DISTINCT sym) s, COUNT(*) n, MAX(d) d FROM bars")):
            out["bars"] = {"syms": r["s"], "rows": r["n"], "last": r["d"],
                           "age": _age_days(r["d"], today)}
        if (r := _one(c, "SELECT COUNT(*) n, MAX(d) d, MAX(updated) u FROM struct")):
            out["struct"] = {"rows": r["n"], "last": r["d"], "updated": r["u"],
                             "age": _age_days(r["d"], today)}
        if (r := _one(c, "SELECT COUNT(*) n, MAX(d) d, MAX(updated) u FROM candidates")):
            out["candidates"] = {"rows": r["n"], "last": r["d"], "updated": r["u"],
                                 "age": _age_days(r["d"], today)}
            out["candidates"]["by_setup"] = {
                x["setup"]: x["n"] for x in
                _rows(c, "SELECT setup, COUNT(*) n FROM candidates GROUP BY setup")}
        day = out["today"]
        if (r := _one(c, "SELECT COUNT(*) n, MAX(ts_et) t FROM alerts WHERE ts_et LIKE ?",
                      (day + "%",))):
            out["alerts_today"] = {"n": r["n"], "last": r["t"]}
        if (r := _one(c, "SELECT COUNT(*) n FROM watch WHERE kind='track'")):
            out["tracking"] = r["n"]
        # `beat` do main.py ghi. Thieu = scanner chua chay lan nao, hoac dang
        # chay ban cu chua co doan ghi beat - phan biet duoc hai thu do quan trong.
        if (r := _one(c, "SELECT v FROM kv WHERE k='beat'")):
            try:
                out["beat"] = json.loads(r["v"])
            except Exception:                                    # noqa: BLE001
                pass
    finally:
        c.close()

    try:
        out["spool"] = len(json.loads(SPOOL.read_text()))
    except Exception:                                            # noqa: BLE001
        out["spool"] = 0
    return out


CAND_COLS = ("sym", "setup", "d", "ref_close", "pivot", "sma20", "adv20",
             "atr_pct", "base_len", "base_depth", "off_high", "rs_pct",
             "dist_pivot", "fund_ok", "quality")


def candidates_payload(db=DB, top: int = TOP_N) -> dict | None:
    """Danh sach theo doi, xep theo quality, TOP_N moi setup.

    Cat bot vi day len ca 500 dong x 2 setup thi khong ai doc het, va khoa nay
    duoc doc lai moi lan mo tab.
    """
    try:
        c = _con(db)
    except sqlite3.Error:
        return None
    try:
        sets = [r["setup"] for r in
                _rows(c, "SELECT DISTINCT setup FROM candidates ORDER BY setup")]
        if not sets:
            return None
        out: dict = {"ts": int(time.time() * 1000), "top": top, "by_setup": {}}
        cols = ",".join(CAND_COLS)
        for s in sets:
            rows = _rows(c, f"SELECT {cols} FROM candidates WHERE setup=?"
                            " ORDER BY quality DESC LIMIT ?", (s, top))
            out["by_setup"][s] = [dict(r) for r in rows]
            r = _one(c, "SELECT COUNT(*) n FROM candidates WHERE setup=?", (s,))
            out["by_setup"][s + "_total"] = r["n"] if r else len(rows)
    finally:
        c.close()
    return out


def rejects_payload(db=DB) -> dict | None:
    """Bang LY DO BI LOAI - tinh lai tu bang `struct`, khong doc bang nao khac.

    Tinh lai thay vi luu lai luc setups.py --build vi mot ly do: bang nay chi co
    nghia khi no ung voi NGUONG HIEN TAI. Sua dict BO roi doc mot bang loai duoc
    sinh boi nguong cu thi ket luan sai ma khong co gi bao.

    Nang (doc ca bang struct, ~5000 dong) -> chi goi trong --all, moi toi.
    """
    try:
        import setups
        import structure
    except Exception as e:                                       # noqa: BLE001
        log(f"push: khong import duoc setups/structure: {e}")
        return None
    try:
        c = _con(db)
    except sqlite3.Error:
        return None
    try:
        # Tien kiem truoc khi goi load_struct(). Ly do: load_struct() mo dau bang
        # executescript(DDL), va tren ket noi mode=ro dieu do chi im lang khi bang
        # DA ton tai (SQLite bo qua CREATE TABLE IF NOT EXISTS). Bang chua co ->
        # "attempt to write a readonly database", mot thong bao khong lien quan gi
        # den nguyen nhan that la "chua chay structure.py --build lan nao".
        if not _one(c, "SELECT 1 FROM struct LIMIT 1"):
            return None
        st = structure.load_struct(c)
        if not st:
            return None
        _, rej = setups.scan(st, None, setups.MAX_CAND)
    except Exception as e:                                       # noqa: BLE001
        log(f"push: rejects: {type(e).__name__}: {e}")
        return None
    finally:
        c.close()
    return {"ts": int(time.time() * 1000), "struct": len(st),
            "cho_fund": rej.get("_cho_fund"),
            "by_setup": {k: dict(v) for k, v in rej.items()
                         if not k.startswith("_") and isinstance(v, dict)}}


def alerts_payload(db=DB, day: str | None = None) -> dict | None:
    """Alert cua mot ngay + ket qua do duoc.

    LEFT JOIN chu khong JOIN: alert vua gui thi chua co dong outcome nao, va do
    la dong minh muon xem nhat.
    """
    day = day or dt.date.today().isoformat()
    try:
        c = _con(db)
    except sqlite3.Error:
        return None
    try:
        rows = _rows(c, """
            SELECT a.ts_et, a.kind, a.sym, a.score, a.px, a.chg, a.rvol,
                   a.atr_move, a.dollar_vol, a.freshness, a.sources,
                   o.px15, o.px60, o.px_close, o.hi_after, o.lo_after
              FROM alerts a
              LEFT JOIN outcome o ON o.sym = a.sym AND o.day = ?
             WHERE a.ts_et LIKE ?
             ORDER BY a.ts_et""", (day, day + "%"))
    finally:
        c.close()
    if not rows:
        return None
    return {"ts": int(time.time() * 1000), "day": day,
            "rows": [dict(r) for r in rows]}


# ───────────────────────── don rac ─────────────────────────
def prune(keep: int = KEEP_DAYS, today: str | None = None) -> int:
    """Xoa khoa scanner:alerts:<ngay> cu hon `keep` ngay.

    Khong co cai nay thi moi ngay them mot dong vinh vien trong D1. Nho, nhung
    free tier khong co ai don ho.
    """
    if not ready():
        return 0
    st, js = call("GET", "kv?prefix=scanner:alerts:")
    if st != 200:
        return 0
    n = 0
    for row in js.get("keys", []):
        k = row.get("key", "") if isinstance(row, dict) else str(row)
        d = k.rsplit(":", 1)[-1]
        age = _age_days(d, today)
        if age is not None and age > keep:
            if call("DELETE", f"kv/{k}")[0] in (200, 204):
                n += 1
    return n


# ───────────────────────── CLI ─────────────────────────
def push_status(db=DB, dry: bool = False, force: bool = False) -> str:
    return put("scanner:status", status_payload(db), force=force, dry=dry)


def push_all(db=DB, dry: bool = False, force: bool = False) -> dict:
    """Snapshot nang, goi mot lan moi toi sau `setups.py --build`."""
    day = dt.date.today().isoformat()
    out = {
        "status": push_status(db, dry, force),
        "candidates": put("scanner:candidates", candidates_payload(db), force, dry),
        "rejects": put("scanner:rejects", rejects_payload(db), force, dry),
        f"alerts:{day}": put(f"scanner:alerts:{day}", alerts_payload(db, day),
                             force, dry),
    }
    if not dry:
        out["pruned"] = prune()
    return out


def _cli() -> int:
    global URL, TOKEN

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--status", action="store_true", help="day scanner:status")
    ap.add_argument("--all", action="store_true", help="day ca snapshot nang")
    ap.add_argument("--dry", action="store_true", help="in payload, khong gui")
    ap.add_argument("--force", action="store_true", help="gui ca khi khong doi")
    ap.add_argument("--ping", action="store_true", help="kiem tra token")
    ap.add_argument("--get", metavar="KEY", help="doc nguoc, vd: config")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--url", default="", help="ghi de SCANNER_PUSH_URL")
    a = ap.parse_args()

    if a.url:
        URL = a.url.rstrip("/")

    # Khong co co nao -> selftest, va selftest khong duoc doi env. Ngay ca khi da
    # cau hinh: `python push.py` phai chay duoc tren VM dang chay that ma khong
    # gui gi len cloud.
    wants_net = a.ping or a.get or a.all or a.status
    if not wants_net:
        return _smoke()
    if not (a.dry or ready()):
        log("push: thieu SCANNER_PUSH_URL / SCANNER_TOKEN -> khong lam gi")
        return 0                        # 0, khong phai 1: chua cau hinh != loi

    if a.ping:
        st, js = call("GET", "ping")
        log(f"HTTP {st}  {json.dumps(js)}")
        return 0 if st == 200 else 1
    if a.get:
        k = a.get if a.get.startswith("scanner:") else f"scanner:{a.get}"
        log(json.dumps(get(k), indent=1, ensure_ascii=False))
        return 0
    if a.all:
        r = push_all(a.db, a.dry, a.force)
        log("push: " + "  ".join(f"{k}={v}" for k, v in r.items()))
        return 1 if "err" in r.values() else 0
    if a.status:
        r = push_status(a.db, a.dry, a.force)
        log(f"push: status={r}")
        return 1 if r == "err" else 0

    return 0


# ───────────────────────── selftest ─────────────────────────
def _mkdb(p: Path) -> None:
    """DB gia du de kiem payload: dung DDL that cua cac module."""
    import bars
    import setups
    import structure
    c = sqlite3.connect(p)
    c.executescript(bars.DDL)
    c.executescript(structure.DDL)
    c.executescript(setups.DDL)
    c.executescript("""
      CREATE TABLE IF NOT EXISTS alerts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT, ts_et TEXT, kind TEXT, sym TEXT, score REAL,
        px REAL, chg REAL, rvol REAL, atr_move REAL, float_rot REAL,
        dollar_vol REAL, freshness TEXT, sources TEXT);
      CREATE TABLE IF NOT EXISTS outcome(
        sym TEXT, day TEXT, alert_ts INTEGER, score REAL, level INTEGER,
        rvol REAL, px0 REAL, px15 REAL, px60 REAL, px_close REAL,
        hi_after REAL, lo_after REAL, src TEXT, updated TEXT,
        PRIMARY KEY(sym, alert_ts));
      CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
      CREATE TABLE IF NOT EXISTS watch(
        sym TEXT, kind TEXT, ts TEXT, PRIMARY KEY(sym, kind));
    """)
    with c:
        c.execute("INSERT INTO bars(sym,d,o,h,l,c,ac,v) VALUES('AAA','2026-09-12',"
                  "1,1,1,1,1,100)")
        c.execute("INSERT INTO candidates(sym,setup,d,ref_close,pivot,sma20,adv20,"
                  "atr_pct,base_len,base_depth,off_high,rs_pct,dist_pivot,fund_ok,"
                  "quality,updated) VALUES('AAA','BO','2026-09-12',20,21,19.8,"
                  "800000,0.02,40,0.09,0.05,80,0.048,NULL,7.5,'x')")
        c.execute("INSERT INTO alerts(ts_utc,ts_et,kind,sym,score,px,chg,rvol,"
                  "atr_move,float_rot,dollar_vol,freshness,sources) VALUES("
                  "'2026-09-12T14:00:00','2026-09-12T10:00:00','NEW','AAA',9.1,"
                  "20,0.06,3.2,1.1,0.2,5e6,'fresh','yahoo')")
        c.execute("INSERT INTO kv(k,v) VALUES('beat','{\"scans\": 12}')")
    c.close()


def _smoke() -> int:
    import tempfile
    n = 0

    def ok(cond, what):
        nonlocal n
        n += 1
        if not cond:
            raise AssertionError(what)

    # digest bo qua khoa volatile -> hai lan quet giong nhau khong day lai
    a = {"ts": 1, "bars": {"syms": 10}}
    b = {"ts": 999999, "bars": {"syms": 10}}
    ok(_digest(a) == _digest(b), "ts phai bi bo khoi digest")
    ok(_digest(a) != _digest({"ts": 1, "bars": {"syms": 11}}), "so lieu doi -> digest doi")
    ok(_digest([1, 2]) == _digest([1, 2]), "digest cua list")

    # tuoi bang
    ok(_age_days("2026-09-10", "2026-09-14") == 4, "_age_days")
    ok(_age_days("2026-09-10T00:00:00", "2026-09-14") == 4, "_age_days cat gio")
    ok(_age_days(None) is None and _age_days("rac") is None, "_age_days chiu rac")

    # payload voi DB that
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.db"
        _mkdb(p)
        s = status_payload(p, today="2026-09-14")
        ok(s["bars"]["syms"] == 1 and s["bars"]["age"] == 2, f"status bars: {s.get('bars')}")
        ok(s["struct"]["rows"] == 0, "struct rong van phai co mat")
        ok(s["candidates"]["by_setup"] == {"BO": 1}, s.get("candidates"))
        ok(s["beat"] == {"scans": 12}, "beat phai duoc parse")
        ok(s["alerts_today"]["n"] == 0, "alert cua ngay khac khong tinh vao hom nay")
        s2 = status_payload(p, today="2026-09-12")
        ok(s2["alerts_today"]["n"] == 1, "alert dung ngay phai dem")

        cp = candidates_payload(p)
        ok(cp["by_setup"]["BO"][0]["sym"] == "AAA", "candidates payload")
        ok(cp["by_setup"]["BO_total"] == 1, "tong truoc khi cat")
        ok(alerts_payload(p, "2026-09-12")["rows"][0]["px15"] is None,
           "alert chua co outcome van phai ra")
        ok(alerts_payload(p, "2026-01-01") is None, "ngay khong co alert -> None")

        # DB thieu bang: khong duoc sap
        q = Path(d) / "tron.db"
        sqlite3.connect(q).close()
        ok("bars" not in status_payload(q), "DB rong -> khong co khoa bars")
        ok(candidates_payload(q) is None, "khong co bang candidates -> None")

        # DB khong ton tai
        ok("db_error" in status_payload(Path(d) / "khong-co.db"), "DB thieu -> db_error")

        # mo che do chi doc: khong the ghi
        ro = _con(p)
        try:
            ro.execute("DELETE FROM bars")
            ok(False, "mode=ro phai chan ghi")
        except sqlite3.OperationalError:
            n += 1
        finally:
            ro.close()      # Windows khong xoa duoc file dang mo -> tempdir kep

    # put khong mang: khong nem loi
    ok(put("scanner:status", {"a": 1}, dry=True) == "dry", "dry")
    ok(put("scanner:status", None) == "skip", "None -> skip")
    old_u, old_t = URL, TOKEN
    try:
        globals()["URL"] = globals()["TOKEN"] = ""
        ok(not ready(), "thieu env -> ready() False")
        ok(put("scanner:status", {"a": 1}) == "off", "thieu env -> off, khong nem")
    finally:
        globals()["URL"], globals()["TOKEN"] = old_u, old_t

    # payload qua to bi chan tai cho, khong doi HTTP 413
    ok(put("scanner:big", {"x": "y" * (MAX_BYTES + 10)}) == "err", "chan payload to")

    log(f"push.py: {n} kiem tra OK")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
