"""prep.py - Dung baseline (ADV20, ATR14, prev_close, CIK) vao SQLite.

Chay moi ngay truoc phien (cron 08:00 ET). Chay lai an toan (idempotent).
    python prep.py            # toan bo universe
    python prep.py --limit 300  # chay thu nhanh
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

DB = ROOT / "state" / "baseline.db"
SEC_UA = os.getenv("SEC_UA", "")
ALPACA_KEY = os.getenv("ALPACA_KEY", "")
ALPACA_SECRET = os.getenv("ALPACA_SECRET", "")

MIN_ADV = 200_000      # bo qua co phieu qua kem thanh khoan
MIN_PRICE = 1.0        # bo qua penny duoi 1 USD
BATCH = 150            # so ticker moi lan goi yfinance
SLEEP = 1.2            # nghi giua cac batch, tranh bi chan


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


# ---------------- 1. CIK map tu SEC ----------------
def fetch_cik_map() -> dict[str, str]:
    import httpx
    if not SEC_UA or "@" not in SEC_UA:
        log("!! SEC_UA chua hop le trong .env -> bo qua CIK")
        return {}
    url = "https://www.sec.gov/files/company_tickers.json"
    r = httpx.get(url, headers={"User-Agent": SEC_UA}, timeout=30)
    r.raise_for_status()
    out = {}
    for row in r.json().values():
        out[str(row["ticker"]).upper()] = str(row["cik_str"]).zfill(10)
    log(f"CIK map: {len(out)} ma")
    return out


# ---------------- 2. Universe tu Alpaca ----------------
def fetch_universe() -> dict[str, str]:
    """{sym: san niem yet}. Xem CHU Y ve gia tri tra ve o duoi.

    ⚠️ Truoc day ham nay tra ve `list[str]` va NEM SAN DI sau khi loc. Ket qua
    la khong cho nao trong ca he thong biet duoc mot ma niem yet o dau, nen san
    chat luong cua phan intraday (config.INTRADAY["exchanges"]) khong the kiem
    tra lai duoc. Gio tra ve dict, va `exch` duoc ghi vao bang `base`.

    `list(fetch_universe())` van cho ra danh sach ma nhu truoc (dict iterate ra
    khoa), nen bars.py:352 khong phai doi.

    `ok_ex` o day RONG HON config.INTRADAY["exchanges"]: no con nhan AMEX. Co y
    - kho nen giu ca AMEX de backtest va de tra loi "vi sao ma nay khong co
    trong danh sach". Viec loai AMEX la viec cua cong chat luong, khong phai
    cua tang du lieu: mot tang du lieu da loc san thi khong con tra loi duoc
    cau hoi "no bi loai vi sao".
    """
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.trading.requests import GetAssetsRequest

    tc = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=True)
    assets = tc.get_all_assets(GetAssetsRequest(
        asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE))
    ok_ex = {"NASDAQ", "NYSE", "AMEX", "ARCA"}
    out: dict[str, str] = {}
    for a in assets:
        if not a.tradable:
            continue
        ex = str(a.exchange).split(".")[-1]
        if ex not in ok_ex:
            continue
        s = a.symbol.upper()
        if any(ch in s for ch in ".-/ ") or len(s) > 5:
            continue  # bo preferred / warrant / unit
        out[s] = ex
    out = {s: out[s] for s in sorted(out)}
    log(f"Universe Alpaca: {len(out)} ma "
        + " ".join(f"{e}={sum(1 for v in out.values() if v == e)}"
                   for e in sorted(ok_ex)))
    return out


# ---------------- 3. Tinh chi so tu daily bars ----------------
ET_TZ = ZoneInfo("America/New_York")


def compute(df: pd.DataFrame) -> dict | None:
    df = df.dropna(subset=["Close", "Volume"])
    # Bo bar cua ngay hom nay: neu prep chay giua phien, bar do chua ket thuc
    # -> prev_close se bang gia hien tai -> chg va atr_move = 0 tren toan bo DB.
    try:
        now_et = dt.datetime.now(dt.timezone.utc).astimezone(ET_TZ)
        today_et = now_et.date()
        # Chi bo bar hom nay khi phien CHUA dong (truoc 16:00 ET, ngay thuong).
        # Sau 16:00 ET bar da chot -> giu lai, vi do chinh la prev_close cho mai.
        market_still_open = now_et.weekday() < 5 and now_et.hour < 16
        if market_still_open:
            idx = pd.DatetimeIndex(df.index)
            if idx.tz is not None:
                idx = idx.tz_convert(ET_TZ).tz_localize(None)
            df = df[idx.date < today_et]
    except Exception:  # noqa: BLE001
        pass
    if len(df) < 25:
        return None
    adv20 = float(df["Volume"].tail(20).mean())
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr14 = float(tr.tail(14).mean())
    prev_close = float(c.iloc[-1])
    if adv20 < MIN_ADV or prev_close < MIN_PRICE or atr14 <= 0:
        return None
    return {"adv20": adv20, "atr14": atr14, "prev_close": prev_close}



# ---------------- 3b. Dung baseline tu kho nen, KHONG goi mang ----------------
def from_bars(con: sqlite3.Connection, cik: dict[str, str],
              limit: int = 0) -> int:
    """Dung lai bang `base` tu kho nen cua bars.py. Phan tinh toan o
    structure.baseline() (thuan stdlib, co test rieng).

    Kho nen da bo nen dang chay (bars.partial_day()) nen o day khong phai xu ly
    lai chuyen "prep chay giua phien" nhu compute().
    """
    import bars
    import structure

    syms = bars.syms(con, min_rows=25)
    if limit:
        syms = syms[:limit]
    log(f"--from-bars: {len(syms)} ma trong kho nen")

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    rows = []
    for s in syms:
        m = structure.baseline(bars.load(con, s, limit=40, adj=False),
                              MIN_ADV, MIN_PRICE)
        if m:
            rows.append((s, m["adv20"], m["atr14"], m["prev_close"],
                         cik.get(s), now))
    if rows:
        # Khong co cot `exch` trong cau nay: --from-bars doc tu kho nen, khong
        # goi Alpaca, nen no KHONG BIET san. Khong ghi con an toan hon ghi NULL
        # - ghi NULL se xoa san da biet cua toan bo DB moi lan chay --from-bars,
        # va cong chat luong se im lang loai sach moi ma.
        con.executemany(
            "INSERT INTO base(sym,adv20,atr14,prev_close,cik,updated) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(sym) DO UPDATE SET "
            "adv20=excluded.adv20, atr14=excluded.atr14, "
            "prev_close=excluded.prev_close, "
            "cik=COALESCE(excluded.cik, base.cik), updated=excluded.updated",
            rows)
        con.commit()
    return len(rows)


def download_batch(syms: list[str]) -> dict[str, pd.DataFrame]:
    raw = yf.download(syms, period="4mo", interval="1d", group_by="ticker",
                      auto_adjust=False, threads=True, progress=False)
    out = {}
    if isinstance(raw.columns, pd.MultiIndex):
        for s in syms:
            if s in raw.columns.get_level_values(0):
                out[s] = raw[s]
    elif len(syms) == 1:
        out[syms[0]] = raw
    return out


# ---------------- 4. Ghi SQLite ----------------
DDL = """
CREATE TABLE IF NOT EXISTS base (
  sym TEXT PRIMARY KEY, adv20 REAL, atr14 REAL, prev_close REAL,
  float_sh REAL, float_ts TEXT, cik TEXT, exch TEXT, is_etf INTEGER DEFAULT 0,
  updated TEXT);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS ix_base_adv ON base(adv20);
"""

# Cot them vao sau khi bang `base` da ton tai tren VM. `CREATE TABLE IF NOT
# EXISTS` khong bao gio them cot vao bang co san, nen khong co doan migrate nay
# thi mot DB cu se thieu cot va cau UPDATE tuong ung nem "no such column".
#
# Ba cot nay la ba lan phat hien khac nhau:
#   exch      moi (san niem yet, cho cong chat luong cua phan intraday)
#   float_ts  scorer.py:69 GHI cot nay tu lau ma DDL chua bao gio khai bao no
#   is_etf    scorer.py:40 DOC cot nay; truoc day chi duoc them bang
#             scripts/mark_etf.py chay tay, nen mot DB dung tu dau thi
#             scorer.py vo ngay lan chay dau tien
# Hai cai sau la loi da ton tai, khong phai do thay doi lan nay - nhung chung o
# dung bang nay va sua o day la mot dong moi, nen sua luon.
#
# mktcap/mktcap_ts: von hoa THAT, do watchlist.refresh_mktcap() ghi cho dung cac
# ma trong danh sach dem (<= 10 ma/dem). KHONG phai float_sh x gia - do la von
# hoa FLOAT, lech han khi noi bo giu nhieu co phan. `mktcap_ts` la dau moc "da
# kiem", va no CHI duoc ghi khi lay duoc so that: mot lan hong khong duoc bien
# thanh "da biet von hoa = 0" roi bi TTL giu nguyen ca tuan.
ADD_COLS = (("float_ts", "TEXT"), ("exch", "TEXT"),
            ("is_etf", "INTEGER DEFAULT 0"),
            ("mktcap", "REAL"), ("mktcap_ts", "TEXT"))


def ensure_cols(con) -> list[str]:
    """Them cot con thieu vao `base`. Tra ve danh sach cot vua them."""
    have = {r[1] for r in con.execute("PRAGMA table_info(base)")}
    added = []
    for name, typ in ADD_COLS:
        if name not in have:
            con.execute(f"ALTER TABLE base ADD COLUMN {name} {typ}")
            added.append(name)
    if added:
        con.commit()
        log(f"bang `base`: them cot {', '.join(added)}")
    return added


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--from-bars", action="store_true",
                    help="tinh tu kho nen cua bars.py, khong tai lai tu yfinance")
    args = ap.parse_args()

    if not args.from_bars and not (ALPACA_KEY and ALPACA_SECRET):
        log("!! Thieu ALPACA_KEY / ALPACA_SECRET trong .env")
        return 1

    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(DDL)
    ensure_cols(con)

    cik = fetch_cik_map()

    if args.from_bars:
        t0 = time.time()
        kept = from_bars(con, cik, args.limit)
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        con.execute("INSERT INTO meta(k,v) VALUES('built',?) "
                    "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (now,))
        con.execute("INSERT INTO meta(k,v) VALUES('count',?) "
                    "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(kept),))
        con.commit()
        total = con.execute("SELECT COUNT(*) FROM base").fetchone()[0]
        con.close()
        log(f"XONG (--from-bars): {kept} ma cap nhat, {total} ma trong DB, "
            f"{time.time() - t0:.0f}s")
        return 0

    uni = fetch_universe()          # {sym: san}
    syms = list(uni)
    if args.limit:
        syms = syms[: args.limit]
        log(f"CHE DO THU: chi xu ly {len(syms)} ma")

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    kept = failed = 0
    t0 = time.time()

    for i in range(0, len(syms), BATCH):
        chunk = syms[i: i + BATCH]
        try:
            frames = download_batch(chunk)
        except Exception as e:  # noqa: BLE001
            log(f"batch {i} loi: {type(e).__name__}: {e}")
            failed += len(chunk)
            continue

        rows = []
        for s, df in frames.items():
            try:
                m = compute(df)
            except Exception:  # noqa: BLE001
                m = None
            if not m:
                continue
            rows.append((s, m["adv20"], m["atr14"], m["prev_close"],
                         cik.get(s), uni.get(s), now))
        if rows:
            # COALESCE cho cik: SEC_UA sai -> fetch_cik_map() tra {} -> neu ghi
            # de thang thi mot lan prep.py chay se xoa sach cik cua CA DB va
            # moi nut "Ho so SEC" chet am tham. `exch` dung cung ly le: mot lan
            # chay --from-bars khong biet san, khong duoc xoa san da biet.
            con.executemany(
                "INSERT INTO base(sym,adv20,atr14,prev_close,cik,exch,updated) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(sym) DO UPDATE SET "
                "adv20=excluded.adv20, atr14=excluded.atr14, "
                "prev_close=excluded.prev_close, "
                "cik=COALESCE(excluded.cik, base.cik), "
                "exch=COALESCE(excluded.exch, base.exch), "
                "updated=excluded.updated", rows)
            con.commit()
            kept += len(rows)
        done = min(i + BATCH, len(syms))
        log(f"{done}/{len(syms)}  giu={kept}  ({time.time() - t0:.0f}s)")
        time.sleep(SLEEP)

    con.execute("INSERT INTO meta(k,v) VALUES('built',?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (now,))
    con.execute("INSERT INTO meta(k,v) VALUES('count',?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(kept),))
    con.commit()

    total = con.execute("SELECT COUNT(*) FROM base").fetchone()[0]
    con.close()
    log(f"XONG: {kept} ma cap nhat, {total} ma trong DB, "
        f"{failed} that bai, {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

