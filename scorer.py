"""scorer.py - Cham diem bat thuong: ghep baseline (SQLite) voi universe live.

Sua v2: RVOL chuan hoa theo trang thai phien (khong con phong dai khi dong cua),
thang diem log10 khong bao hoa, uu tien small-cap thay vi mega-cap.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from clock import SessionClock
from vprofile import rvol_at, session_frac

ROOT = Path(__file__).resolve().parent
DB = ROOT / "state" / "baseline.db"

# --- Nguong loc ---
MIN_PX = 1.0
MIN_CHG = 0.05
MIN_DOLLAR_VOL = 2_000_000
MIN_RVOL = 3.0
SPLIT_DIVERGE = 0.25    # chg nguon vs chg tu prev_close lech >25pp -> nghi split
FLOAT_TOP_N = 60

# --- Trong so (da hieu chinh) ---
W_RVOL, W_ATR, W_ROT, W_DV, W_FRESH = 2.2, 1.6, 1.4, 0.5, 1.5
CAP_RVOL = 2.0          # log10 -> tran tai rvol = 100x
ALERT_SCORE = 7.0


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
    need = [s for s in syms if s in base and not base[s].get("float_sh")]
    if not need:
        return 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        vals = list(ex.map(_fetch_float, need))
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    rows = [(v, now, s) for s, v in zip(need, vals) if v]
    if rows:
        con = sqlite3.connect(DB)
        con.executemany("UPDATE base SET float_sh=?, float_ts=? WHERE sym=?", rows)
        con.commit()
        con.close()
        for v, _, s in rows:
            base[s]["float_sh"] = v
    return len(rows)


# ---------------- Cham diem ----------------
def _chg(u: dict, b: dict) -> tuple[float | None, float]:
    """Tra ve (%thay doi, do lech giua nguon va tinh tu prev_close).

    Uu tien chg CUA NGUON: px va chg tu cung mot nguon luon tu nhat quan.
    prev_close den tu nguon khac (yfinance) nen chi dung de doi chieu split.
    """
    px, pc = u.get("px") or 0.0, b.get("prev_close") or 0.0
    src = u.get("chg")
    calc = (px / pc - 1.0) if (px and pc) else None
    if src is None:
        return calc, 0.0
    if calc is None:
        return src, 0.0
    return src, abs(calc - src)


def score_one(u: dict, b: dict, frac: float) -> dict:
    px = u.get("px") or 0.0
    vol = u.get("vol") or 0.0
    adv20 = b.get("adv20") or 0.0
    atr14 = b.get("atr14") or 0.0
    pc = b.get("prev_close") or 0.0
    flt = b.get("float_sh") or 0.0

    chg, diverge = _chg(u, b)
    rv = rvol_at(vol, adv20, frac) if vol else 0.0
    atr_move = abs(px - pc) / atr14 if (atr14 and px and pc) else 0.0
    rot = (vol / flt) if (flt and vol) else 0.0
    dvol = px * vol

    s, parts = 0.0, []
    if rv > 1:
        c = W_RVOL * min(math.log10(rv), CAP_RVOL)
        s += c
        parts.append(f"rvol {rv:.1f}x(+{c:.1f})")
    if atr_move > 0.2:
        c = W_ATR * min(atr_move / 2.0, 3.0)
        s += c
        parts.append(f"atr {atr_move:.1f}(+{c:.1f})")
    if rot > 0.05:
        c = W_ROT * min(rot, 3.0)
        s += c
        parts.append(f"rot {rot:.2f}(+{c:.1f})")
    if dvol > MIN_DOLLAR_VOL:
        c = W_DV * min(math.log10(dvol / MIN_DOLLAR_VOL), 1.5)
        s += c
        parts.append(f"${dvol / 1e6:.0f}M(+{c:.1f})")
    if u.get("sources") and set(u["sources"]) <= {"alpaca_mover", "alpaca_active"}:
        s += W_FRESH
        parts.append(f"alpaca-only(+{W_FRESH:.1f})")

    return {
        "sym": u["sym"], "score": round(s, 2), "px": px, "chg": chg,
        "vol": vol, "rvol": round(rv, 2), "atr_move": round(atr_move, 2),
        "float_sh": flt, "float_rot": round(rot, 3), "dollar_vol": dvol,
        "cik": b.get("cik"), "diverge": round(diverge, 3),
        "freshness": "REALTIME" if u.get("fresh", 0) >= 3 else "~15min",
        "sources": sorted(u.get("sources", [])), "explain": " ".join(parts),
    }


def rank(universe: dict[str, dict], base: dict[str, dict] | None = None,
         clock: SessionClock | None = None) -> tuple[list[dict], dict]:
    base = base if base is not None else load_baseline()
    ck = clock or SessionClock()
    st = ck.state()
    mso = ck.mso()
    smin = ck.session_minutes() or 390
    frac = session_frac(st, mso, smin)
    intraday = st in ("OPENING", "LIVE", "CLOSING")

    rej: dict = {"_state": st, "_frac": round(frac, 4)}

    def no(r: str) -> None:
        rej[r] = rej.get(r, 0) + 1

    pre = []
    for sym, u in universe.items():
        b = base.get(sym)
        if not b:
            no("khong co baseline (ETF/moi/kem thanh khoan)")
            continue
        px, vol = u.get("px") or 0.0, u.get("vol") or 0.0
        chg, diverge = _chg(u, b)
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
        rv = rvol_at(vol, b.get("adv20") or 0, frac)
        if rv < MIN_RVOL:
            no(f"rvol < {MIN_RVOL}")
            continue
        if intraday and diverge > SPLIT_DIVERGE:
            no("nghi ngo gop/chia co phieu (chg lech nguon)")
            continue
        pre.append((sym, u, rv))

    pre.sort(key=lambda t: -t[2])
    rej["_float_moi_lay"] = enrich_float([t[0] for t in pre[:FLOAT_TOP_N]], base)

    out = [score_one(u, base[sym], frac) for sym, u, _ in pre]
    out.sort(key=lambda d: -d["score"])
    rej["_qua_loc"] = len(out)
    return out, rej


if __name__ == "__main__":
    import universe_live

    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="chay ca khi thi truong dong (du lieu khong dong bo)")
    args = ap.parse_args()

    ck = SessionClock()
    print(ck.describe())
    st = ck.state()
    if st not in ("OPENING", "LIVE", "CLOSING") and not args.force:
        print("\n[!] Ngoai phien giao dich. Diem so KHONG dang tin cay "
              "(gia = gia dong cua nen ATR=0, chg khong dong bo).")
        print("    Them --force neu chi muon kiem tra pipeline.")
        raise SystemExit(0)

    print("\nDang lay universe...")
    u = universe_live.build()
    print(f"universe = {len(u)} ma")

    hits, rej = rank(u, clock=ck)
    print(f"\ntrang thai={rej['_state']}  frac={rej['_frac']}  "
          f"(volume ky vong = adv20 x frac)")
    print(f"\n{'SYM':<7}{'SCORE':>7}{'%CHG':>8}{'PRICE':>9}{'RVOL':>7}"
          f"{'ATR':>6}{'ROT':>7}  {'FRESH':<9} CHI TIET")
    for h in hits[:20]:
        flag = "***" if h["score"] >= ALERT_SCORE else "   "
        print(f"{h['sym']:<7}{h['score']:>7.1f}{(h['chg'] or 0) * 100:>7.1f}%"
              f"{h['px']:>9.2f}{h['rvol']:>7.1f}{h['atr_move']:>6.1f}"
              f"{h['float_rot']:>7.2f}  {h['freshness']:<9}{flag} {h['explain']}")

    print(f"\nVuot nguong alert ({ALERT_SCORE}): "
          f"{sum(1 for h in hits if h['score'] >= ALERT_SCORE)} ma")
    print("\nLy do bi loai:")
    for k, v in sorted(((k, v) for k, v in rej.items() if not k.startswith("_")),
                       key=lambda kv: -kv[1]):
        print(f"  {k:<45} {v}")
