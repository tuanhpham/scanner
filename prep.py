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
def fetch_universe() -> list[str]:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.trading.requests import GetAssetsRequest

    tc = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=True)
    assets = tc.get_all_assets(GetAssetsRequest(
        asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE))
    ok_ex = {"NASDAQ", "NYSE", "AMEX", "ARCA"}
    syms = []
    for a in assets:
        if not a.tradable:
            continue
        if str(a.exchange).split(".")[-1] not in ok_ex:
            continue
        s = a.symbol.upper()
        if any(ch in s for ch in ".-/ ") or len(s) > 5:
            continue  # bo preferred / warrant / unit
        syms.append(s)
    syms = sorted(set(syms))
    log(f"Universe Alpaca: {len(syms)} ma")
    return syms


# ---------------- 3. Tinh chi so tu daily bars ----------------
ET_TZ = ZoneInfo("America/New_York")


def compute(df: pd.DataFrame) -> dict | None:
    df = df.dropna(subset=["Close", "Volume"])
    # Bo bar cua ngay hom nay: neu prep chay giua phien, bar do chua ket thuc
    # -> prev_close se bang gia hien tai -> chg va atr_move = 0 tren toan bo DB.
    try:
        today_et = dt.datetime.now(dt.timezone.utc).astimezone(ET_TZ).date()
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
  float_sh REAL, cik TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS ix_base_adv ON base(adv20);
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if not ALPACA_KEY or not ALPACA_SECRET:
        log("!! Thieu ALPACA_KEY / ALPACA_SECRET trong .env")
        return 1

    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(DDL)

    cik = fetch_cik_map()
    syms = fetch_universe()
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
                         cik.get(s), now))
        if rows:
            con.executemany(
                "INSERT INTO base(sym,adv20,atr14,prev_close,cik,updated) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(sym) DO UPDATE SET "
                "adv20=excluded.adv20, atr14=excluded.atr14, "
                "prev_close=excluded.prev_close, cik=excluded.cik, "
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

