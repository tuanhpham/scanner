"""mark_etf.py - Danh dau ETF va test issue tu file Nasdaq Trader (mien phi)."""
import datetime as dt
import sqlite3
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "state" / "baseline.db"
URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"

r = httpx.get(URL, timeout=45, follow_redirects=True)
r.raise_for_status()
lines = r.text.splitlines()

hdr = lines[0].split("|")
i_sym, i_etf, i_test = hdr.index("Symbol"), hdr.index("ETF"), hdr.index("Test Issue")

etf, test = set(), set()
for ln in lines[1:]:
    p = ln.split("|")
    if len(p) < len(hdr):
        continue
    s = p[i_sym].strip().upper()
    if p[i_etf].strip() == "Y":
        etf.add(s)
    if p[i_test].strip() == "Y":
        test.add(s)

print(f"Nasdaq Trader: {len(etf)} ETF, {len(test)} test issue")

con = sqlite3.connect(DB)
cols = {c[1] for c in con.execute("PRAGMA table_info(base)")}
if "is_etf" not in cols:
    con.execute("ALTER TABLE base ADD COLUMN is_etf INTEGER DEFAULT 0")
    print("+ them cot is_etf")

con.execute("UPDATE base SET is_etf=0")
con.executemany("UPDATE base SET is_etf=1 WHERE sym=?", [(s,) for s in etf | test])
con.commit()

n_etf = con.execute("SELECT COUNT(*) FROM base WHERE is_etf=1").fetchone()[0]
n_eq = con.execute("SELECT COUNT(*) FROM base WHERE is_etf=0").fetchone()[0]
miss = con.execute(
    "SELECT COUNT(*) FROM base WHERE is_etf=0 AND cik IS NULL").fetchone()[0]
con.execute("INSERT INTO meta(k,v) VALUES('etf_marked',?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),))
con.commit()
con.close()

print(f"ETF/test: {n_etf}  |  co phieu thuong: {n_eq}  |  thieu CIK: {miss}")
sys.exit(0)

