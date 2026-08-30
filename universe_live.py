"""universe_live.py - Gop danh sach co phieu dang chay tu nhieu nguon mien phi.

Nguon 1: Alpaca screener (movers + most actives) - real-time SIP, ~150 ma.
Nguon 2: Yahoo screener qua yfinance - tre ~15 phut, rong hon, phan trang.
"""
from __future__ import annotations

import datetime as dt
import os
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

ALPACA_KEY = os.getenv("ALPACA_KEY", "")
ALPACA_SECRET = os.getenv("ALPACA_SECRET", "")

YH_MIN_CHG = 4.0        # % thay doi toi thieu
YH_MIN_VOL = 300_000
YH_MIN_PX = 1.0
YH_PAGE = 250           # Yahoo gioi han 250/trang
YH_MAX_PAGES = 5


def _now() -> float:
    return time.time()


# ---------------- Nguon 1: Alpaca ----------------
def alpaca_side(top: int = 50) -> list[dict]:
    from alpaca.data.historical.screener import ScreenerClient
    from alpaca.data.requests import MarketMoversRequest, MostActivesRequest

    c = ScreenerClient(ALPACA_KEY, ALPACA_SECRET)
    out: list[dict] = []
    ts = _now()

    try:
        mv = c.get_market_movers(MarketMoversRequest(top=top))
        for m in list(mv.gainers) + list(mv.losers):
            out.append({
                "sym": m.symbol.upper(),
                "px": float(m.price),
                "chg": float(m.percent_change) / 100.0,
                "vol": None,
                "src": "alpaca_mover",
                "ts": ts,
            })
    except Exception as e:  # noqa: BLE001
        print(f"  [alpaca movers] {type(e).__name__}: {e}")

    try:
        ac = c.get_most_actives(MostActivesRequest(by="volume", top=top))
        for a in ac.most_actives:
            out.append({
                "sym": a.symbol.upper(),
                "px": None,
                "chg": None,
                "vol": float(a.volume),
                "trades": int(getattr(a, "trade_count", 0) or 0),
                "src": "alpaca_active",
                "ts": ts,
            })
    except Exception as e:  # noqa: BLE001
        print(f"  [alpaca actives] {type(e).__name__}: {e}")

    return out


# ---------------- Nguon 2: Yahoo ----------------
def _yh_query():
    from yfinance import EquityQuery as Q
    return Q("and", [
        Q("gt", ["percentchange", YH_MIN_CHG]),
        Q("gt", ["dayvolume", YH_MIN_VOL]),
        Q("gt", ["intradayprice", YH_MIN_PX]),
        Q("eq", ["region", "us"]),
    ])


def yahoo_side(max_pages: int = YH_MAX_PAGES) -> list[dict]:
    import yfinance as yf

    out: list[dict] = []
    ts = _now()
    try:
        q = _yh_query()
    except Exception as e:  # noqa: BLE001
        print(f"  [yahoo] yfinance qua cu, thieu EquityQuery: {e}")
        return out

    for page in range(max_pages):
        offset = page * YH_PAGE
        try:
            res = yf.screen(q, offset=offset, size=YH_PAGE,
                            sortField="percentchange", sortAsc=False)
        except Exception as e:  # noqa: BLE001
            print(f"  [yahoo] trang {page} loi: {type(e).__name__}: {e}")
            break

        quotes = (res or {}).get("quotes") or []
        if not quotes:
            break

        for r in quotes:
            s = str(r.get("symbol", "")).upper()
            if not s or any(ch in s for ch in ".-^/ "):
                continue
            px = r.get("regularMarketPrice")
            chg = r.get("regularMarketChangePercent")
            vol = r.get("regularMarketVolume")
            if px is None or chg is None:
                continue
            out.append({
                "sym": s,
                "px": float(px),
                "chg": float(chg) / 100.0,
                "vol": float(vol) if vol else None,
                "src": "yahoo",
                "ts": ts,
            })

        if len(quotes) < YH_PAGE:
            break
        time.sleep(0.4)

    return out


# ---------------- Gop ----------------
FRESH = {"alpaca_mover": 3, "alpaca_active": 3, "yahoo": 1}  # cao = moi hon


def merge(rows: list[dict]) -> dict[str, dict]:
    """Gop theo ma, uu tien du lieu tu nguon tuoi hon (Alpaca > Yahoo)."""
    u: dict[str, dict] = {}
    for r in rows:
        s = r["sym"]
        cur = u.get(s)
        if cur is None:
            cur = {"sym": s, "px": None, "chg": None, "vol": None,
                   "trades": 0, "sources": set(), "fresh": 0, "ts": r["ts"]}
            u[s] = cur
        cur["sources"].add(r["src"])
        rank = FRESH.get(r["src"], 0)
        for f in ("px", "chg", "vol"):
            v = r.get(f)
            if v is None:
                continue
            if cur[f] is None or rank >= cur["fresh"]:
                cur[f] = v
        if r.get("trades"):
            cur["trades"] = max(cur["trades"], r["trades"])
        if rank > cur["fresh"]:
            cur["fresh"] = rank
            cur["ts"] = r["ts"]
    return u


def build() -> dict[str, dict]:
    rows = alpaca_side() + yahoo_side()
    return merge(rows)


if __name__ == "__main__":
    t0 = _now()
    a = alpaca_side()
    y = yahoo_side()
    u = merge(a + y)
    only_alp = [s for s, v in u.items() if v["sources"] <= {"alpaca_mover", "alpaca_active"}]
    print(f"\n{dt.datetime.now():%H:%M:%S}  Alpaca={len(a)}  Yahoo={len(y)}  "
          f"-> universe={len(u)} ma  ({_now() - t0:.1f}s)")
    print(f"Chi co o Alpaca (tin hieu som): {len(only_alp)} ma")
    top = sorted((v for v in u.values() if v["chg"] is not None),
                 key=lambda v: -v["chg"])[:15]
    print(f"\n{'SYM':<7}{'%CHG':>8}{'PRICE':>10}{'VOL':>14}  NGUON")
    for v in top:
        vol = f"{v['vol']:,.0f}" if v["vol"] else "-"
        print(f"{v['sym']:<7}{v['chg'] * 100:>7.1f}%{v['px'] or 0:>10.2f}"
              f"{vol:>14}  {','.join(sorted(v['sources']))}")

