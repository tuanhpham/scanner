"""bars.py - Kho nen ngay (OHLCV) trong SQLite. Nen tang cho Phase 9.

Ly do ton tai: prep.py tai 4 thang nen ngay cho ~5000 ma roi NEM DI, chi giu
lai 3 con so (adv20, atr14, prev_close). Khong the do "nen tich luy 6 tuan
chat dan" hay "roi 70% tu dinh 52 tuan" bang 3 con so do. Giu lai nen la dieu
kien tien quyet cho structure.py, setups.py va backtest.py.

Chi phi: ~250 phien x 5000 ma x ~60 byte = 70-90 MB. Cap nhat hang ngay chi
tai 5 phien gan nhat nen NHANH HON prep.py hien tai.

TANG LUU TRU KHONG CAN THU VIEN NGOAI (chi sqlite3 cua stdlib) -> chay va tu
test duoc ca tren may khong cai pandas. Chi fetch()/sync() moi can yfinance.

    python bars.py                    # selftest, khong can mang
    python bars.py --sync --full      # lan dau: tai 1 nam cho ca universe
    python bars.py --sync             # hang ngay: chi 5 phien gan nhat
    python bars.py --info AAPL        # xem da co gi trong kho

DIEU CHINH GIA (quan trong): bang luu CA `c` (close tho) va `ac` (adj close).
load(adj=True) tra ve chuoi da back-adjust theo he so ac/c -> mot lan chia 1:10
khong con bien thanh "cu roi 90%" gia trong structure.py. Nguoc lai prep.py can
gia THO de khop voi quote live, nen goi load(adj=False).
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
import time
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent
DB = ROOT / "state" / "baseline.db"

BATCH = 150            # so ticker moi lan goi yfinance (giong prep.py)
SLEEP = 1.2            # nghi giua cac batch, tranh bi chan
LOAD_N = 300           # so nen doc mac dinh: du cho sma200 + dinh 52 tuan
KEEP_DAYS = 800        # giu ~3 nam, du de backtest 2 nam
FULL_PERIOD = "2y"
DAILY_PERIOD = "1mo"   # thua ra de bit lo hong khi cron chet vai ngay

ET = "America/New_York"

DDL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS bars(
  sym TEXT NOT NULL, d TEXT NOT NULL,
  o REAL, h REAL, l REAL, c REAL, ac REAL, v REAL,
  PRIMARY KEY(sym, d)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_bars_d ON bars(d);
"""

log = print            # cac module khac co the gan lai: bars.log = log


class Bar(NamedTuple):
    """Mot nen ngay. `d` la ngay phien theo lich ET, dang 'YYYY-MM-DD'."""
    d: str
    o: float
    h: float
    l: float
    c: float
    v: float


# ───────────────────────── ket noi ─────────────────────────
def con(db: str | Path = DB) -> sqlite3.Connection:
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(db), timeout=30)
    c.execute("PRAGMA busy_timeout=30000")
    c.executescript(DDL)
    return c


def _c(db) -> tuple[sqlite3.Connection, bool]:
    """Nhan ca duong dan va Connection.

    Tra ve (con, co_phai_ta_mo_khong). build() cua structure.py goi load() vai
    nghin lan - mo lai ket noi (kem executescript DDL) moi lan la vo ich.
    """
    if isinstance(db, sqlite3.Connection):
        return db, False
    return con(db), True


# ───────────────────────── ghi ─────────────────────────
def save(db, data: dict[str, list[tuple]]) -> int:
    """Upsert. `data` = {sym: [(d, o, h, l, c, ac, v), ...]}.

    Chay lai an toan: cung mot ngay ghi de len chinh no. Nho vay `--sync` co
    the chay bao nhieu lan cung duoc trong ngay.
    """
    rows = [(s, *r) for s, rs in data.items() for r in rs]
    if not rows:
        return 0
    c, mine = _c(db)
    try:
        with c:
            c.executemany(
                "INSERT INTO bars(sym,d,o,h,l,c,ac,v) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(sym,d) DO UPDATE SET o=excluded.o, h=excluded.h, "
                "l=excluded.l, c=excluded.c, ac=excluded.ac, v=excluded.v",
                rows)
    finally:
        if mine:
            c.close()
    return len(rows)


def purge(db, keep_days: int = KEEP_DAYS) -> int:
    cut = (dt.date.today() - dt.timedelta(days=keep_days)).isoformat()
    c, mine = _c(db)
    try:
        with c:
            return c.execute("DELETE FROM bars WHERE d < ?", (cut,)).rowcount
    finally:
        if mine:
            c.close()


# ───────────────────────── doc ─────────────────────────
def load(db, sym: str, limit: int = LOAD_N, adj: bool = True,
         upto: str | None = None) -> list[Bar]:
    """Nen cua mot ma, XEP TANG DAN theo ngay (cuoi danh sach = moi nhat).

    upto: chi lay nen <= ngay nay. Day la tham so lam backtest.py kha thi -
    goi upto='2025-04-11' la co dung nhung gi biet duoc vao toi hom do, khong
    the nhin truoc tuong lai.

    adj=True: back-adjust theo he so f = ac/c (split + co tuc). Volume chia
    cho f de tong tien giao dich khong doi - co tuc lam volume lech vai phan
    nghin, khong dang ke voi ty le RVOL.
    """
    c, mine = _c(db)
    try:
        q = "SELECT d,o,h,l,c,ac,v FROM bars WHERE sym=?"
        p: list = [sym]
        if upto:
            q += " AND d<=?"
            p.append(upto)
        q += " ORDER BY d DESC LIMIT ?"
        p.append(int(limit))
        rows = c.execute(q, p).fetchall()
    finally:
        if mine:
            c.close()

    out: list[Bar] = []
    for d, o, h, l, cl, ac, v in reversed(rows):
        if cl is None or not cl:
            continue
        f = (ac / cl) if (adj and ac) else 1.0
        out.append(Bar(d, (o or cl) * f, (h or cl) * f, (l or cl) * f, cl * f,
                       (v or 0.0) / f if f else (v or 0.0)))
    return out


def syms(db, min_rows: int = 0) -> list[str]:
    c, mine = _c(db)
    try:
        if min_rows:
            rows = c.execute("SELECT sym FROM bars GROUP BY sym "
                             "HAVING COUNT(*)>=? ORDER BY sym", (min_rows,))
        else:
            rows = c.execute("SELECT DISTINCT sym FROM bars ORDER BY sym")
        return [r[0] for r in rows]
    finally:
        if mine:
            c.close()


def last_date(db, sym: str | None = None) -> str | None:
    c, mine = _c(db)
    try:
        if sym:
            r = c.execute("SELECT MAX(d) FROM bars WHERE sym=?", (sym,)).fetchone()
        else:
            r = c.execute("SELECT MAX(d) FROM bars").fetchone()
        return r[0] if r else None
    finally:
        if mine:
            c.close()


def coverage(db) -> dict:
    """So lieu suc khoe cua kho: bao nhieu ma, bao nhieu nen, moi nhat ngay nao."""
    c, mine = _c(db)
    try:
        n_sym, n_row, dmax, dmin = c.execute(
            "SELECT COUNT(DISTINCT sym), COUNT(*), MAX(d), MIN(d) FROM bars"
        ).fetchone()
        thin = c.execute("SELECT COUNT(*) FROM (SELECT sym FROM bars "
                         "GROUP BY sym HAVING COUNT(*)<60)").fetchone()[0]
    finally:
        if mine:
            c.close()
    return {"syms": n_sym or 0, "rows": n_row or 0, "last": dmax,
            "first": dmin, "thin": thin or 0}


# ───────────────────────── phien chua chot ─────────────────────────
def partial_day() -> str | None:
    """Ngay ET cua nen CHUA chot, hoac None neu khong co.

    Truoc 16:00 ET ngay thuong, nen "hom nay" cua yfinance la nen dang chay:
    ghi vao kho thi adv20/atr14 va moi thu dua tren nen cuoi deu sai. Sau
    16:00 thi nen da chot -> giu lai binh thuong.

    Thieu tzdata (may Windows tran) -> tra None kem canh bao: khong bo nen nao
    van tot hon bo lam mat nen thuc.
    """
    try:
        from zoneinfo import ZoneInfo
        now = dt.datetime.now(dt.timezone.utc).astimezone(ZoneInfo(ET))
    except Exception as e:                                       # noqa: BLE001
        log(f"  [bars] khong xac dinh duoc gio ET ({type(e).__name__}) -> "
            f"giu nguyen nen cuoi")
        return None
    if now.weekday() < 5 and now.hour < 16:
        return now.date().isoformat()
    return None


# ───────────────────────── tai tu mang ─────────────────────────
def _nan(x) -> bool:
    return x is None or x != x            # NaN != NaN, khong can import math


def _rows_of(df) -> list[tuple]:
    """DataFrame nen ngay -> [(d,o,h,l,c,ac,v)]. Khong import pandas.

    Doc theo ten cot chu khong theo itertuples(): cot 'Adj Close' co dau cach
    nen itertuples doi ten thanh `_5` va thu tu thi phu thuoc phien ban.
    """
    try:
        cols = {str(x): x for x in df.columns}
    except Exception:                                            # noqa: BLE001
        return []

    def col(name: str):
        k = cols.get(name)
        return df[k].tolist() if k is not None else None

    o, h, l = col("Open"), col("High"), col("Low")
    cl, ac, v = col("Close"), col("Adj Close"), col("Volume")
    if cl is None:
        return []
    if ac is None:                     # auto_adjust=True -> khong co Adj Close
        ac = cl

    days = []
    for t in df.index:
        try:
            days.append(t.date().isoformat())
        except AttributeError:
            days.append(str(t)[:10])

    def at(seq, i) -> float | None:
        """Gia tri thu i, hoac None. Cot co the thieu han (seq is None)."""
        if not seq or i >= len(seq):
            return None
        return None if _nan(seq[i]) else float(seq[i])

    out = []
    for i in range(len(days)):
        c_ = at(cl, i)
        if not c_:
            continue                    # ngay khong giao dich / du lieu thieu
        out.append((days[i], at(o, i), at(h, i), at(l, i), c_,
                    at(ac, i) or c_, at(v, i) or 0.0))
    return out


def fetch(symbols: list[str], period: str = DAILY_PERIOD) -> dict[str, list[tuple]]:
    """Tai nen ngay cho MOT batch ma. Can yfinance."""
    import yfinance as yf

    raw = yf.download(symbols, period=period, interval="1d", group_by="ticker",
                      auto_adjust=False, threads=True, progress=False)
    out: dict[str, list[tuple]] = {}
    multi = False
    try:
        multi = raw.columns.nlevels > 1
    except Exception:                                            # noqa: BLE001
        pass

    if multi:
        try:
            have = set(raw.columns.get_level_values(0))
        except Exception:                                        # noqa: BLE001
            have = set()
        for s in symbols:
            if s in have:
                rows = _rows_of(raw[s])
                if rows:
                    out[s] = rows
    elif len(symbols) == 1:
        rows = _rows_of(raw)
        if rows:
            out[symbols[0]] = rows
    return out


def sync(db, symbols: list[str], period: str = DAILY_PERIOD,
         batch: int = BATCH, sleep_s: float = SLEEP,
         drop_partial: bool = True) -> dict:
    """Tai + ghi kho cho ca danh sach ma. Tra ve thong ke."""
    skip = partial_day() if drop_partial else None
    c = con(db) if not isinstance(db, sqlite3.Connection) else db
    stat = {"syms": 0, "rows": 0, "fail": 0, "bo_nen_dang_chay": 0}
    t0 = time.time()
    try:
        for i in range(0, len(symbols), batch):
            part = symbols[i:i + batch]
            try:
                data = fetch(part, period)
            except Exception as e:                               # noqa: BLE001
                log(f"  [bars] batch {i} loi: {type(e).__name__}: {e}")
                stat["fail"] += len(part)
                continue
            if skip:
                for s, rs in data.items():
                    n0 = len(rs)
                    data[s] = [r for r in rs if r[0] != skip]
                    stat["bo_nen_dang_chay"] += n0 - len(data[s])
            stat["rows"] += save(c, data)
            stat["syms"] += len(data)
            log(f"  [bars] {min(i + batch, len(symbols))}/{len(symbols)}  "
                f"nen={stat['rows']}  ({time.time() - t0:.0f}s)")
            if sleep_s and i + batch < len(symbols):
                time.sleep(sleep_s)
    finally:
        if not isinstance(db, sqlite3.Connection):
            c.close()
    return stat


# ───────────────────────── selftest ─────────────────────────
def _fake(n: int = 40, px: float = 10.0, vol: float = 1e6,
          start: str = "2025-01-06") -> list[tuple]:
    """Nen gia phang, ngay lien tuc (khong can lich phien cho selftest)."""
    d0 = dt.date.fromisoformat(start)
    out = []
    for i in range(n):
        p = px + (i % 3) * 0.01
        d = (d0 + dt.timedelta(days=i)).isoformat()
        out.append((d, p, p + 0.05, p - 0.05, p, p, vol))
    return out


def _smoke() -> None:
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    c = con(tmp)

    rows = _fake(30)
    assert save(c, {"AAA": rows}) == 30
    assert save(c, {"AAA": rows}) == 30, "ghi lai phai la upsert, khong nhan doi"
    assert len(load(c, "AAA", limit=999)) == 30, "upsert lam phinh bang"

    bs = load(c, "AAA", limit=5)
    assert [b.d for b in bs] == sorted(b.d for b in bs), "phai xep tang dan"
    assert len(bs) == 5 and bs[-1].d == rows[-1][0], "limit phai lay nen MOI nhat"

    # upto: cong cu chong nhin truoc tuong lai cua backtest
    cut = rows[10][0]
    assert load(c, "AAA", limit=999, upto=cut)[-1].d == cut

    # dieu chinh split: ac = c/2 -> gia chia doi, volume nhan doi
    save(c, {"BBB": [("2025-02-03", 100.0, 101.0, 99.0, 100.0, 50.0, 1000.0)]})
    a = load(c, "BBB", adj=True)[0]
    r = load(c, "BBB", adj=False)[0]
    assert abs(a.c - 50.0) < 1e-9 and abs(r.c - 100.0) < 1e-9
    assert abs(a.v - 2000.0) < 1e-9, "volume phai back-adjust nguoc chieu gia"
    assert abs(a.h - 50.5) < 1e-9, "h/l/o cung phai duoc dieu chinh"

    assert syms(c) == ["AAA", "BBB"]
    assert syms(c, min_rows=20) == ["AAA"]
    assert last_date(c, "AAA") == rows[-1][0]

    cov = coverage(c)
    # thin = ma duoi 60 nen: ca AAA (30) va BBB (1) deu chua du de do cau truc
    assert cov["syms"] == 2 and cov["rows"] == 31 and cov["thin"] == 2

    # nen thieu du lieu bi bo, khong lam do
    save(c, {"CCC": [("2025-02-03", None, None, None, None, None, None)]})
    assert load(c, "CCC") == []

    c.close()
    print("bars.py selftest: ok")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                            # noqa: BLE001
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--sync", action="store_true", help="tai nen tu yfinance")
    ap.add_argument("--full", action="store_true",
                    help=f"tai {FULL_PERIOD} thay vi {DAILY_PERIOD} (lan dau)")
    ap.add_argument("--limit", type=int, default=0, help="chi N ma dau (chay thu)")
    ap.add_argument("--info", nargs="?", const="", help="xem kho, khong goi mang")
    ap.add_argument("--purge", action="store_true", help="don nen cu")
    a = ap.parse_args()

    if a.info is not None:
        cov = coverage(DB)
        print(f"kho nen: {cov['syms']} ma · {cov['rows']:,} nen · "
              f"{cov['first']} -> {cov['last']} · "
              f"{cov['thin']} ma duoi 60 nen")
        if a.info:
            for b in load(DB, a.info.upper(), limit=10):
                print(f"  {b.d}  o={b.o:8.2f} h={b.h:8.2f} l={b.l:8.2f} "
                      f"c={b.c:8.2f} v={b.v:14,.0f}")
        raise SystemExit(0)

    if a.purge:
        print(f"da don {purge(DB)} nen cu hon {KEEP_DAYS} ngay")
        raise SystemExit(0)

    if not a.sync:
        _smoke()
        raise SystemExit(0)

    import prep                          # dung lai fetch_universe() cua prep.py
    sl = prep.fetch_universe()
    if a.limit:
        sl = sl[:a.limit]
        print(f"CHE DO THU: {len(sl)} ma")
    st = sync(DB, sl, FULL_PERIOD if a.full else DAILY_PERIOD)
    print(f"XONG: {st['syms']} ma, {st['rows']} nen ghi, {st['fail']} that bai, "
          f"bo {st['bo_nen_dang_chay']} nen dang chay")
    cov = coverage(DB)
    print(f"kho: {cov['syms']} ma · {cov['rows']:,} nen · den {cov['last']}")
