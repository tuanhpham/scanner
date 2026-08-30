"""scorer.py - Cham diem bat thuong: ghep baseline (SQLite) voi universe live.

Tinh RVOL chuan hoa theo thoi diem trong phien, ATR move, float rotation.
Float duoc lay LUOI (chi cho ~60 ma dan dau) va cache vinh vien trong DB.
"""
from __future__ import annotations

import datetime as dt
import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from clock import SessionClock
from vprofile import cum_frac, rvol as calc_rvol

ROOT = Path(__file__).resolve().parent
DB = ROOT / "state" / "baseline.db"

# --- Nguong loc ---
MIN_PX = 1.0
MIN_CHG = 0.05          # +5%
MIN_DOLLAR_VOL = 2_000_000
MIN_RVOL = 3.0
SPLIT_GUARD_CHG = 1.50  # >150% ma rvol thap -> nghi ngo gop/chia co phieu
FLOAT_TOP_N = 60        # so ma duoc lay float moi vong

# --- Trong so ---
W_RVOL, W_ATR, W_ROT, W_DV, W_FRESH = 2.2, 1.6, 1.4, 0.8, 2.0
ALERT_SCORE = 8.0


def load_baseline() -> dict[str, dict]:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT sym,adv20,atr14,prev_close,float_sh,cik,is_etf FROM base "
        "WHERE is_etf=0").fetchall()
    con.close()
    return {r["sym"]: dict(r) for r in rows}


# ---------------- Float lay luoi ----------------
def _fetch_float(sym: str) -> float | None:
    import yfinance as yf
    try:
        info = yf.Ticker(sym).get_info() or {}
    except Exception:  # noqa: BLE001
        return None
    for k in ("floatShares", "impliedSharesOutstanding", "sharesOutstanding"):
        v = info.get(k)
        if v and float(v) > 0:
            return float(v)
    return None


def enrich_float(syms: list[str], base: dict[str, dict]) -> int:
    """Lay float cho cac ma chua co, ghi vao DB va vao dict base."""
    need = [s for s in syms if s in base and not base[s].get("float_sh")]
    if not need:
        return 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        vals = list(ex.map(_fetch_float, need))

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    rows = [(v, now, s) for s, v in zip(need, vals) if v]
    if rows:
        con = sqlite3.connect(DB)
        con.executemany(
            "UPDATE base SET float_sh=?, float_ts=? WHERE sym=?", rows)
        con.commit()
        con.close()
        for v, _, s in rows:
            base[s]["float_sh"] = v
    return len(rows)


# ---------------- Cham diem ----------------
def score_one(u: dict, b: dict, mso: int, smin: int) -> dict:
    px = u.get("px") or 0.0
    chg = u.get("chg")
    vol = u.get("vol") or 0.0
    adv20 = b.get("adv20") or 0.0
    atr14 = b.get("atr14") or 0.0
    pc = b.get("prev_close") or 0.0
    flt = b.get("float_sh") or 0.0

    if chg is None and pc and px:
        chg = px / pc - 1.0

    rv = calc_rvol(vol, adv20, mso, smin) if vol else 0.0
    atr_move = abs(px - pc) / atr14 if (atr14 and px and pc) else 0.0
    rot = (vol / flt) if (flt and vol) else 0.0
    dvol = px * vol

    s = 0.0
    parts = []
    if rv > 1:
        c = W_RVOL * min(math.log(rv), 3.5)
        s += c
        parts.append(f"rvol {rv:.1f}x(+{c:.1f})")
    if atr_move > 0:
        c = W_ATR * min(atr_move / 2.0, 3.0)
        s += c
        parts.append(f"atr {atr_move:.1f}(+{c:.1f})")
    if rot > 0:
        c = W_ROT * min(rot, 3.0)
        s += c
        parts.append(f"rot {rot:.2f}(+{c:.1f})")
    if dvol > 1e6:
        c = W_DV * min(math.log10(dvol / 1e6), 2.5)
        s += c
        parts.append(f"${dvol / 1e6:.0f}M(+{c:.1f})")
    if u.get("sources") and u["sources"] <= {"alpaca_mover", "alpaca_active"}:
        s += W_FRESH
        parts.append(f"alpaca-only(+{W_FRESH:.1f})")

    fresh = "REALTIME" if u.get("fresh", 0) >= 3 else "~15min"
    return {
        "sym": u["sym"], "score": round(s, 2), "px": px, "chg": chg,
        "vol": vol, "rvol": round(rv, 2), "atr_move": round(atr_move, 2),
        "float_sh": flt, "float_rot": round(rot, 3), "dollar_vol": dvol,
        "cik": b.get("cik"), "freshness": fresh,
        "sources": sorted(u.get("sources", [])),
        "explain": " ".join(parts),
    }


def rank(universe: dict[str, dict], base: dict[str, dict] | None = None,
         clock: SessionClock | None = None) -> tuple[list[dict], dict[str, int]]:
    base = base if base is not None else load_baseline()
    ck = clock or SessionClock()
    mso = ck.mso()
    smin = ck.session_minutes() or 390
    rej: dict[str, int] = {}

    def no(reason: str) -> None:
        rej[reason] = rej.get(reason, 0) + 1

    # Vong 1: loc tho, khong can float
    pre = []
    for sym, u in universe.items():
        b = base.get(sym)
        if not b:
            no("khong co baseline (ETF/moi/kem thanh khoan)")
            continue
        px = u.get("px") or 0.0
        chg = u.get("chg")
        if chg is None and b.get("prev_close") and px:
            chg = px / b["prev_close"] - 1.0
        vol = u.get("vol") or 0.0
        if px < MIN_PX:
            no("gia < $1")
            continue
        if chg is None or chg < MIN_CHG:
            no("tang < 5%")
            continue
        if not vol:
            no("thieu volume")
            continue
        if px * vol < MIN_DOLLAR_VOL:
            no("thanh khoan < $2M")
            continue
        rv = calc_rvol(vol, b.get("adv20") or 0, mso, smin)
        if rv < MIN_RVOL:
            no(f"rvol < {MIN_RVOL}")
            continue
        if chg > SPLIT_GUARD_CHG and rv < 5:
            no("nghi ngo gop co phieu")
            continue
        pre.append((sym, u, b, rv))

    # Vong 2: lay float cho cac ma dan dau roi cham diem day du
    pre.sort(key=lambda t: -t[3])
    head = [t[0] for t in pre[:FLOAT_TOP_N]]
    n_new = enrich_float(head, base)

    out = [score_one(u, base[sym], mso, smin) for sym, u, b, _ in pre]
    out.sort(key=lambda d: -d["score"])
    rej["_float_moi_lay"] = n_new
    rej["_qua_loc"] = len(out)
    return out, rej


if __name__ == "__main__":
    import universe_live

    ck = SessionClock()
    print(ck.describe())
    print("\nDang lay universe...")
    u = universe_live.build()
    print(f"universe = {len(u)} ma")

    hits, rej = rank(u, clock=ck)
    print(f"\n{'SYM':<7}{'SCORE':>7}{'%CHG':>8}{'PRICE':>9}{'RVOL':>7}"
          f"{'ATR':>6}{'ROT':>7}  {'FRESH':<9} CHI TIET")
    for h in hits[:20]:
        flag = "***" if h["score"] >= ALERT_SCORE else "   "
        print(f"{h['sym']:<7}{h['score']:>7.1f}{h['chg'] * 100:>7.1f}%"
              f"{h['px']:>9.2f}{h['rvol']:>7.1f}{h['atr_move']:>6.1f}"
              f"{h['float_rot']:>7.2f}  {h['freshness']:<9}{flag} {h['explain']}")

    print(f"\nVuot nguong alert ({ALERT_SCORE}): "
          f"{sum(1 for h in hits if h['score'] >= ALERT_SCORE)} ma")
    print("\nLy do bi loai:")
    for k, v in sorted(rej.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<45} {v}")

