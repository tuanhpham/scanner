"""outcome.py - Do chat luong alert: sau khi bot bao, gia di dau?

Muc dich duy nhat: tra loi cau hoi "ma 8.3 diem co that su tot hon ma 7.1
diem khong". Khong co bang nay thi moi lan tinh chinh ALERT_SCORE hay trong so
trong scorer.py chi la doi cam giac.

Bang `outcome`, moi alert la MOT dong:

    px0        gia luc gui alert (lay tu chinh du lieu da render, khong goi API)
    px15/px60  gia sau 15 / 60 phut
    px_close   gia dong phien
    hi_after   dinh cao nhat sau alert  (MFE - "neu ban ban dung dinh")
    lo_after   day thap nhat sau alert  (MAE - "ban da phai chiu lo bao nhieu")

HAI DUONG DIEN SO, co y de nhu vay:

  1. update()   - vong quet 60s dien tu st.universe. KHONG goi API them, nhung
                  ma nao nguoi di va roi khoi screener thi mat du lieu, va
                  hi/lo chi la dinh/day CUA CAC LAN LAY MAU (thap hon that).
  2. backfill() - sau phien, doc nen 1 phut cua yfinance -> so chinh xac,
                  dien ca nhung ma da roi khoi universe. Day moi la so dung
                  de doc bao cao; src='yf' danh dau dong da duoc chot lai.

Vi vay bao cao co cot `cov` (coverage): ty le dong da co du so. Doc win%/med%
khi cov con thap la tu lua minh - phan thieu chinh la nhung ma da nguoi.

    python outcome.py                  # smoke test tren DB tam, khong can mang
    python outcome.py --backfill       # chot lai so cua phien hom nay
    python outcome.py --backfill 2026-09-04
    python outcome.py --report 30      # bang chat luong 30 ngay gan nhat
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    ET = ZoneInfo("America/New_York")
except Exception:                                                # noqa: BLE001
    ET = None      # thieu goi `tzdata` (Windows chay python he thong, khong venv)
ROOT = Path(__file__).resolve().parent
DB_DEFAULT = ROOT / "state" / "baseline.db"

log = print          # main.py co the gan lai: outcome.log = log

MIN15, MIN60 = 15 * 60, 60 * 60
FILL_TOL = 240       # giay: cua so cho phep khi dien px15/px60 tu vong quet
                     # (vong chay moi 60s, 4 phut du de restart giua duong)

# Nguong chia nhom diem. 12.0 la SCORE_MAX cua render.py (= muc 3).
BUCKETS = ((7.0, 8.0), (8.0, 9.0), (9.0, 10.0), (10.0, 12.0), (12.0, 1e9))

DDL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS outcome(
  sym TEXT, day TEXT, alert_ts INTEGER, score REAL, level INTEGER, rvol REAL,
  px0 REAL, px15 REAL, px60 REAL, px_close REAL,
  hi_after REAL, lo_after REAL, src TEXT, updated TEXT,
  PRIMARY KEY(sym, alert_ts));
CREATE INDEX IF NOT EXISTS ix_outcome_day ON outcome(day);
"""


def _con(db: str | Path) -> sqlite3.Connection:
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(db), timeout=15)
    c.execute("PRAGMA busy_timeout=15000")
    c.executescript(DDL)
    return c


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _day_et(ts: float | None = None) -> str:
    """Ngay theo gio ET: alert cua phien nao thi thuoc ngay do.

    Thieu tzdata thi ve ngay local - giong store._today(). Sai lech chi xay ra
    voi may dat mui gio khien nua dem local nam trong phien My; VM dat
    Europe/Berlin (mục 5.4) thi khong bi.
    """
    t = dt.datetime.fromtimestamp(ts if ts is not None else time.time(),
                                  dt.timezone.utc)
    return (t.astimezone(ET) if ET else t.astimezone()).date().isoformat()


# ───────────────────────── ghi luc gui alert ─────────────────────────
def record(db, h: dict, level: int, ts: float | None = None) -> bool:
    """Mo mot dong outcome ngay khi alert duoc gui. True = da ghi.

    hi_after/lo_after duoc mo bang chinh px0 de max()/min() sau nay khong phai
    xu ly NULL. ON CONFLICT DO NOTHING: hai alert cung ma cung giay (khong xay
    ra thuc te) thi giu dong dau.
    """
    px = float(h.get("px") or 0.0)
    if not px or not h.get("sym"):
        return False
    t = int(ts if ts is not None else time.time())
    try:
        with closing(_con(db)) as c, c:
            c.execute(
                "INSERT INTO outcome(sym,day,alert_ts,score,level,rvol,px0,"
                "hi_after,lo_after,src,updated) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(sym,alert_ts) DO NOTHING",
                (h["sym"], _day_et(t), t, float(h.get("score") or 0.0),
                 int(level or 0), float(h.get("rvol") or 0.0), px, px, px,
                 "live", _now()))
        return True
    except Exception as e:                                       # noqa: BLE001
        log(f"outcome.record {h.get('sym')}: {type(e).__name__}: {e}")
        return False


# ───────────────────────── dien tu vong quet ─────────────────────────
def update(db, universe: dict[str, dict], now: float | None = None) -> int:
    """Dien px15/px60 va noi rong hi/lo cho cac alert con mo CUA HOM NAY.

    Chi lay gia co san trong st.universe -> khong goi API nao. Loc theo `day`
    la bat buoc: khong loc thi dong con mo cua hom qua se bi gia hom nay ghi vao.
    """
    if not universe:
        return 0
    t = now if now is not None else time.time()
    day = _day_et(t)
    n = 0
    try:
        with closing(_con(db)) as c, c:
            rows = c.execute(
                "SELECT sym,alert_ts,px15,px60,hi_after,lo_after FROM outcome "
                "WHERE px_close IS NULL AND src='live' AND day=?",
                (day,)).fetchall()
            for sym, a_ts, p15, p60, hi, lo in rows:
                px = (universe.get(sym) or {}).get("px")
                if not px:
                    continue                  # da roi khoi screener -> de backfill
                px = float(px)
                age = t - a_ts
                new15 = px if (p15 is None and MIN15 <= age <= MIN15 + FILL_TOL) \
                    else None
                new60 = px if (p60 is None and MIN60 <= age <= MIN60 + FILL_TOL) \
                    else None
                c.execute(
                    "UPDATE outcome SET hi_after=?, lo_after=?, "
                    "px15=COALESCE(?,px15), px60=COALESCE(?,px60), updated=? "
                    "WHERE sym=? AND alert_ts=?",
                    (max(hi or px, px), min(lo or px, px), new15, new60,
                     _now(), sym, a_ts))
                n += 1
    except Exception as e:                                       # noqa: BLE001
        log(f"outcome.update: {type(e).__name__}: {e}")
    return n


def freeze(db, universe: dict[str, dict] | None = None,
           day: str | None = None) -> int:
    """Cuoi phien: dien px_close TAM tu lan quet cuoi cung.

    Chi de bang co so ngay trong toi hom do. backfill() se ghi de bang gia
    dong cua that (va dat src='yf'). Ma khong con trong universe thi de NULL,
    dung doan.
    """
    if not universe:
        return 0
    d = day or _day_et()
    n = 0
    try:
        with closing(_con(db)) as c, c:
            rows = c.execute("SELECT sym,alert_ts FROM outcome "
                             "WHERE day=? AND px_close IS NULL AND src='live'",
                             (d,)).fetchall()
            for sym, a_ts in rows:
                px = (universe.get(sym) or {}).get("px")
                if not px:
                    continue
                c.execute("UPDATE outcome SET px_close=?, updated=? "
                          "WHERE sym=? AND alert_ts=?",
                          (float(px), _now(), sym, a_ts))
                n += 1
    except Exception as e:                                       # noqa: BLE001
        log(f"outcome.freeze: {type(e).__name__}: {e}")
    return n


# ───────────────────────── chot lai bang nen 1 phut ─────────────────────────
def _bars(syms: list[str], day: str) -> dict[str, list[tuple[int, float, float, float]]]:
    """{sym: [(epoch, high, low, close), ...]} nen 1 phut cua mot ngay.

    yfinance chi giu nen 1 phut ~30 ngay gan nhat -> phai backfill trong thang.
    """
    import yfinance as yf

    d0 = dt.date.fromisoformat(day)
    df = yf.download(syms, start=d0.isoformat(),
                     end=(d0 + dt.timedelta(days=1)).isoformat(),
                     interval="1m", auto_adjust=False, prepost=False,
                     progress=False, group_by="ticker", threads=True)
    if df is None or df.empty:
        return {}
    out: dict[str, list[tuple[int, float, float, float]]] = {}
    # 1 ma -> cot phang; nhieu ma -> cot 2 tang (sym, field). Tuy phien ban
    # yfinance nen phai kiem tra thay vi gia dinh.
    multi = getattr(df.columns, "nlevels", 1) > 1
    for s in syms:
        try:
            if multi:
                if s not in set(df.columns.get_level_values(0)):
                    continue
                sub = df[s]
            else:
                sub = df
            sub = sub[["High", "Low", "Close"]].dropna()
        except Exception:                                        # noqa: BLE001
            continue
        bars = []
        for ts, row in sub.iterrows():
            try:
                e = int(ts.timestamp()) if ts.tzinfo else \
                    int((ts.tz_localize(ET) if ET else ts).timestamp())
                bars.append((e, float(row["High"]), float(row["Low"]),
                             float(row["Close"])))
            except Exception:                                    # noqa: BLE001
                continue
        if bars:
            out[s] = bars
    return out


def backfill(db, day: str | None = None) -> dict:
    """Chot lai so cua mot ngay bang nen 1 phut. Tra ve thong ke ngan.

    Ghi de px15/px60/px_close/hi_after/lo_after va dat src='yf'. px0 GIU
    NGUYEN - do la gia ban thuc su nhin thay trong alert, khong phai gia nen.
    """
    d = day or _day_et()
    try:
        with closing(_con(db)) as c:
            rows = c.execute("SELECT sym,alert_ts FROM outcome "
                             "WHERE day=? AND src<>'yf'", (d,)).fetchall()
    except Exception as e:                                       # noqa: BLE001
        log(f"outcome.backfill doc: {type(e).__name__}: {e}")
        return {"day": d, "rows": 0, "fixed": 0, "err": str(e)}
    if not rows:
        return {"day": d, "rows": 0, "fixed": 0}

    syms = sorted({s for s, _ in rows})
    try:
        bars = _bars(syms, d)
    except Exception as e:                                       # noqa: BLE001
        log(f"outcome.backfill yfinance: {type(e).__name__}: {e}")
        return {"day": d, "rows": len(rows), "fixed": 0, "err": str(e)}

    fixed = 0
    try:
        with closing(_con(db)) as c, c:
            for sym, a_ts in rows:
                b = bars.get(sym)
                if not b:
                    continue
                after = [x for x in b if x[0] >= a_ts - 60]
                if not after:
                    continue
                p15 = next((x[3] for x in after if x[0] >= a_ts + MIN15), None)
                p60 = next((x[3] for x in after if x[0] >= a_ts + MIN60), None)
                c.execute(
                    "UPDATE outcome SET px15=?, px60=?, px_close=?, hi_after=?,"
                    " lo_after=?, src='yf', updated=? WHERE sym=? AND alert_ts=?",
                    (p15, p60, after[-1][3], max(x[1] for x in after),
                     min(x[2] for x in after), _now(), sym, a_ts))
                fixed += 1
    except Exception as e:                                       # noqa: BLE001
        log(f"outcome.backfill ghi: {type(e).__name__}: {e}")
    n_miss = len(syms) - len(bars)
    if n_miss:
        log(f"outcome.backfill {d}: {n_miss}/{len(syms)} ma khong co nen 1p "
            f"(huy niem yet / yfinance thieu du lieu)")
    return {"day": d, "rows": len(rows), "fixed": fixed,
            "syms": len(syms), "no_bars": n_miss}


# ───────────────────────── bao cao ─────────────────────────
def _med(xs: list[float]) -> float | None:
    s = sorted(xs)
    if not s:
        return None
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def _pct(a: float | None, b: float | None) -> float | None:
    if not a or not b:
        return None
    return (a / b - 1.0) * 100.0


def report(db, days: int = 30, day_from: str | None = None) -> list[dict]:
    """Mot dong cho moi nhom diem. `cov` = ty le dong da co px15."""
    cut = day_from or (dt.date.today() - dt.timedelta(days=days)).isoformat()
    try:
        with closing(_con(db)) as c:
            rows = c.execute(
                "SELECT score,px0,px15,px60,px_close,hi_after,lo_after,src "
                "FROM outcome WHERE day>=? AND px0>0", (cut,)).fetchall()
    except Exception as e:                                       # noqa: BLE001
        log(f"outcome.report: {type(e).__name__}: {e}")
        return []

    out = []
    for lo_s, hi_s in BUCKETS:
        grp = [r for r in rows if lo_s <= (r[0] or 0) < hi_s]
        if not grp:
            continue
        r15 = [p for p in (_pct(r[2], r[1]) for r in grp) if p is not None]
        r60 = [p for p in (_pct(r[3], r[1]) for r in grp) if p is not None]
        rcl = [p for p in (_pct(r[4], r[1]) for r in grp) if p is not None]
        mfe = [p for p in (_pct(r[5], r[1]) for r in grp) if p is not None]
        mae = [p for p in (_pct(r[6], r[1]) for r in grp) if p is not None]
        out.append({
            "bucket": f"{lo_s:.1f}-{hi_s:.1f}" if hi_s < 1e8 else f"{lo_s:.1f}+",
            "n": len(grp),
            "cov": len(r15) / len(grp),
            "final": sum(1 for r in grp if r[7] == "yf") / len(grp),
            "win15": (sum(1 for p in r15 if p > 0) / len(r15)) if r15 else None,
            "med15": _med(r15),
            "win60": (sum(1 for p in r60 if p > 0) / len(r60)) if r60 else None,
            "med60": _med(r60),
            "medcl": _med(rcl),
            "medmfe": _med(mfe),
            "medmae": _med(mae),
        })
    return out


def fmt_report(rows: list[dict]) -> str:
    if not rows:
        return ("Chua co du lieu. Bang `outcome` chi duoc ghi khi bot gui alert "
                "that (khong tinh --dry).")
    def f(v, s="+.1f"):
        return "-" if v is None else format(v, s)

    def w(v):                       # ty le thang: None = chua co so, khong phai 0%
        return "-" if v is None else f"{v * 100:.0f}%"
    h = (f"{'BUCKET':<10}{'n':>4}{'cov':>6}{'win15':>7}{'med15':>8}"
         f"{'win60':>7}{'med60':>8}{'medCls':>8}{'medMFE':>8}{'medMAE':>8}")
    lines = [h, "-" * len(h)]
    for r in rows:
        lines.append(
            f"{r['bucket']:<10}{r['n']:>4}{r['cov'] * 100:>5.0f}%"
            f"{w(r['win15']):>7}{f(r['med15']):>8}"
            f"{w(r['win60']):>7}{f(r['med60']):>8}"
            f"{f(r['medcl']):>8}{f(r['medmfe']):>8}{f(r['medmae']):>8}")
    return "\n".join(lines)


def purge(db, keep_days: int = 400) -> int:
    """Don dong qua cu. Mac dinh giu hon mot nam - day la du lieu quy."""
    cut = (dt.date.today() - dt.timedelta(days=keep_days)).isoformat()
    try:
        with closing(_con(db)) as c, c:
            return c.execute("DELETE FROM outcome WHERE day<?", (cut,)).rowcount
    except Exception as e:                                       # noqa: BLE001
        log(f"outcome.purge: {type(e).__name__}: {e}")
        return 0


def pending(db, day: str | None = None) -> int:
    """So alert cua mot ngay chua duoc chot lai (src<>'yf')."""
    try:
        with closing(_con(db)) as c:
            return c.execute("SELECT COUNT(*) FROM outcome WHERE day=? "
                             "AND src<>'yf'",
                             (day or _day_et(),)).fetchone()[0]
    except Exception as e:                                       # noqa: BLE001
        log(f"outcome.pending: {type(e).__name__}: {e}")
        return 0


def last_open_day(db) -> str | None:
    """Ngay gan nhat con dong chua chot, khong tinh hom nay."""
    try:
        with closing(_con(db)) as c:
            r = c.execute("SELECT MAX(day) FROM outcome WHERE src<>'yf' "
                          "AND day<?", (_day_et(),)).fetchone()
    except Exception as e:                                       # noqa: BLE001
        log(f"outcome.last_open_day: {type(e).__name__}: {e}")
        return None
    return r[0] if r and r[0] else None


# ───────────────────────── CLI ─────────────────────────
def _smoke() -> None:
    """Chay duoc tren may khong co pandas/yfinance: chi dung stdlib."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "t.db"
        t0 = time.time() - MIN60 - 30           # alert cua 60 phut truoc
        assert record(db, {"sym": "AAA", "px": 10.0, "score": 8.4,
                           "rvol": 40.0}, 2, ts=t0)
        assert record(db, {"sym": "BBB", "px": 4.0, "score": 12.6,
                           "rvol": 90.0}, 3, ts=t0)
        assert not record(db, {"sym": "CCC", "px": 0}, 1), "px=0 phai bi bo"

        # t = alert + 15 phut -> chi px15 duoc dien
        n = update(db, {"AAA": {"px": 11.0}, "BBB": {"px": 3.6}},
                   now=t0 + MIN15 + 10)
        print(f"update @+15p: {n} dong")
        # t = alert + 60 phut -> px60. AAA roi khoi universe.
        update(db, {"BBB": {"px": 5.0}}, now=t0 + MIN60 + 10)
        update(db, {"BBB": {"px": 4.4}}, now=t0 + MIN60 + 20)

        with closing(_con(db)) as c:
            for r in c.execute("SELECT sym,px0,px15,px60,px_close,hi_after,"
                               "lo_after,src FROM outcome ORDER BY sym"):
                print("  ", r)
            got = dict(c.execute(
                "SELECT sym,px60 FROM outcome").fetchall())
        assert got["AAA"] is None, "AAA khong con trong universe -> px60 NULL"
        assert got["BBB"] == 5.0, got

        print(f"\nfreeze: {freeze(db, {'BBB': {'px': 4.2}})} dong")
        print(f"pending hom nay: {pending(db, _day_et(t0))}")
        print("\n" + fmt_report(report(db, days=3)))
        print("\nsmoke test OK (khong can mang, khong can pandas)")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                            # noqa: BLE001
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_DEFAULT))
    ap.add_argument("--backfill", nargs="?", const="", metavar="YYYY-MM-DD",
                    help="chot lai so bang nen 1 phut (mac dinh: hom nay)")
    ap.add_argument("--report", nargs="?", const=30, type=int, metavar="NGAY")
    a = ap.parse_args()

    if a.backfill is not None:
        print(backfill(a.db, a.backfill or None))
    elif a.report is not None:
        print(f"Chat luong alert, {a.report} ngay gan nhat\n")
        print(fmt_report(report(a.db, days=a.report)))
    else:
        _smoke()
