"""setups.py - Hai setup theo nen ngay: BO (breakout nen tich luy) va RV
(bat lai sau cu roi sau). Phase 9.2.

Day la tang PHAN XET, doi lai voi structure.py chi DO. Moi nguong "the nao la
nen tot" nam trong hai dict BO/RV o duoi, khong rai rac trong ham - vi
backtest.py se sua chinh hai dict do, va sua mot cho thi khong co ban sao nao
bi bo sot.

Hai buoc, tach doi co y:

    1. bo_candidate()/rv_candidate()  - buoi toi, doc bang `struct`.
       "Ma nay CO nen dang theo doi khong" -> bang `candidates` + pivot.
    2. trig_bo()/trig_rv()            - trong phien, doc quote.
       "Hom nay no CO kich hoat khong" -> alert.

Buoc 1 khong duoc dung du lieu trong phien, buoc 2 khong duoc tinh lai nen.
Nho vay backtest.py goi dung hai ham nay tren nen ngay qua khu ma khong phai
viet lai logic - neu backtest va bot chay hai doan code khac nhau thi ket qua
backtest khong noi len dieu gi ve bot.

Thuan stdlib.

    python setups.py                # selftest, khong can mang, khong can DB
    python setups.py --build        # quet bang `struct` -> bang `candidates`
    python setups.py --show         # xem danh sach dang theo doi
    python setups.py --show BO --n 40
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path

import bars
import structure

ROOT = Path(__file__).resolve().parent
DB = ROOT / "state" / "baseline.db"

log = print

# ───────────────────────── nguong ─────────────────────────
# BO de rong: nen tich luy o small-cap la phan viec chinh cua setup nay.
# RV siet chat: mot ma roi 70% ma thanh khoan mong thi khong the ban ra duoc,
# va "co ban tot" o do gan nhu luon la bay pha loang.
BO: dict = {
    "min_px": 1.5,
    "min_adv": 200_000,
    "min_base_len": 20,
    "max_depth": 0.20,          # nen chat
    "max_depth_cheap": 0.35,    # duoi $10 thi nen rong hon la binh thuong
    "cheap_px": 10.0,
    "max_atr_contract": 0.75,   # bien do phai co lai so voi 50 phien truoc
    "max_off_high": 0.25,       # nen phai o gan dinh, khong phai giua cu roi
    # Chi theo doi phan tren cua nen. Xa pivot 30% thi hom nay khong the vuot,
    # lay quote cua no la lang. Tren pivot qua nhieu thi da vao muon.
    "max_dist": 0.12,
    "min_dist": -0.05,
    # kich hoat trong phien
    "over_pivot": 0.005,        # phai vuot han pivot, khong phai cham vao
    "max_over_pivot": 0.15,     # +15% tren pivot: gap-and-go, de SPIKE lo
    "rvol": 1.8,
    "close_pos": 0.5,           # dong o nua tren bien do ngay
    "dvol": 2_000_000,
    "max_gap": 0.08,            # gap to hon thi entry xau (ghi vao `warn`)
}

RV: dict = {
    "min_px": 3.0,
    "min_adv": 500_000,
    "min_off_high": 0.50,
    "max_days_since_low": 30,   # day 52 tuan phai con moi
    "max_ret63": -0.10,         # da giam it nhat 3 thang
    "max_up_from_low": 0.40,    # bat len 40% roi thi khong con la diem vao
    "require_fund": True,       # thieu so lieu co ban = LOAI, khong phai "tot"
    # kich hoat trong phien
    "chg": 0.07,
    "rvol": 3.0,
    "close_pos": 0.75,
    "dvol": 2_000_000,
    "over_sma20": 0.0,          # phai lay lai sma20 sau nhieu tuan o duoi
}

MAX_CAND = 500      # tran moi setup: gioi han so quote phai lay trong phien
MAX_AGE = 5         # bang `struct`/`candidates` cu hon 5 ngay = khong dung nua


def _num(x) -> float | None:
    """None-safe: cot trong `struct` co the la NULL (chua du nen)."""
    return None if x is None else float(x)


def _rej(d: dict | None, key: str) -> None:
    if d is not None:
        d[key] = d.get(key, 0) + 1


# ───────────────────────── buoc 1: sinh danh sach theo doi ─────────────────
def bo_candidate(m: dict, g: dict = BO, rej: dict | None = None) -> dict | None:
    """Nen tich luy dang cho vuot pivot. `m` = mot dong cua bang `struct`."""
    px, adv, pivot = _num(m.get("px")), _num(m.get("adv20")), _num(m.get("pivot"))
    if not px or px < g["min_px"]:
        return _rej(rej, f"gia < ${g['min_px']}")
    if not adv or adv < g["min_adv"]:
        return _rej(rej, "kem thanh khoan")
    if (m.get("base_len") or 0) < g["min_base_len"]:
        return _rej(rej, "khong co nen tich luy")
    if pivot is None:
        return _rej(rej, "khong co pivot")

    cap = g["max_depth_cheap"] if px < g["cheap_px"] else g["max_depth"]
    depth = _num(m.get("base_depth"))
    if depth is None or depth > cap:
        return _rej(rej, f"nen rong hon {cap:.0%}")

    ac = _num(m.get("atr_contract"))
    if ac is not None and ac > g["max_atr_contract"]:
        return _rej(rej, "bien do khong co lai")

    off = _num(m.get("off_high"))
    if off is None or off > g["max_off_high"]:
        return _rej(rej, f"cach dinh 52 tuan > {g['max_off_high']:.0%}")

    sma50 = _num(m.get("sma50"))
    if sma50 and px < sma50:
        return _rej(rej, "duoi sma50")
    if (_num(m.get("sma50_slope")) or 0.0) < 0:
        return _rej(rej, "sma50 dang di xuong")

    dist = _num(m.get("dist_pivot"))
    if dist is None or dist > g["max_dist"]:
        return _rej(rej, "con xa pivot")
    if dist < g["min_dist"]:
        return _rej(rej, "da vuot pivot qua xa")

    return {"sym": m.get("sym"), "setup": "BO", "d": m.get("d"),
            "ref_close": px, "pivot": pivot, "sma20": _num(m.get("sma20")),
            "adv20": adv, "atr_pct": _num(m.get("atr_pct")),
            "base_len": int(m.get("base_len") or 0), "base_depth": depth,
            "off_high": off, "rs_pct": _num(m.get("rs_pct")),
            "dist_pivot": dist, "fund_ok": None,
            "quality": _bo_quality(m, g)}


def rv_candidate(m: dict, g: dict = RV, fund: dict | None = None,
                 rej: dict | None = None) -> dict | None:
    """Ma da roi sau, dang cho mot phien bat manh. `fund` tu fundamentals.py.

    `fund=None` (chua co Phase 9.3) -> `fund_ok=None`: van vao danh sach de
    theo doi, nhung trig_rv() se khong cho kich hoat. "Chua biet" khong bao gio
    duoc coi la "tot".
    """
    px, adv = _num(m.get("px")), _num(m.get("adv20"))
    if not px or px < g["min_px"]:
        return _rej(rej, f"gia < ${g['min_px']}")
    if not adv or adv < g["min_adv"]:
        return _rej(rej, "kem thanh khoan")

    off = _num(m.get("off_high"))
    if off is None or off < g["min_off_high"]:
        return _rej(rej, f"chua roi du {g['min_off_high']:.0%}")

    dsl = m.get("days_since_low")
    if dsl is None or dsl > g["max_days_since_low"]:
        return _rej(rej, "day 52 tuan qua cu")

    ret = _num(m.get("ret63"))
    if ret is None or ret > g["max_ret63"]:
        return _rej(rej, "chua giam du 3 thang")

    up = _num(m.get("up_from_low"))
    if up is not None and up > g["max_up_from_low"]:
        return _rej(rej, "da bat len qua nhieu")

    ok = None if fund is None else bool(fund.get("ok"))
    if g["require_fund"] and ok is False:
        return _rej(rej, "co ban khong dat")

    return {"sym": m.get("sym"), "setup": "RV", "d": m.get("d"),
            "ref_close": px, "pivot": None, "sma20": _num(m.get("sma20")),
            "adv20": adv, "atr_pct": _num(m.get("atr_pct")),
            "base_len": int(m.get("base_len") or 0),
            "base_depth": _num(m.get("base_depth")),
            "off_high": off, "rs_pct": _num(m.get("rs_pct")),
            "dist_pivot": None, "fund_ok": ok,
            "quality": _rv_quality(m, g, fund)}


def _clip(x: float) -> float:
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def _bo_quality(m: dict, g: dict = BO) -> float:
    """0..1, CHI de xep hang danh sach theo doi cho khoi tran MAX_CAND.

    Khong phai diem alert - diem alert do scorer.py cham khi co so lieu trong
    phien. Dung chung mot con so cho hai viec la cach chac chan de sau nay
    khong biet dieu chinh no theo cai gi.
    """
    depth = _num(m.get("base_depth")) or 1.0
    ln = min((m.get("base_len") or 0) / structure.MAX_BASE, 1.0)
    tight = 1.0 - _clip(depth / g["max_depth_cheap"])
    contract = 1.0 - _clip((_num(m.get("atr_contract")) or 1.0))
    dry = 1.0 - _clip(_num(m.get("base_dryup")) or 1.0)
    rs = (_num(m.get("rs_pct")) or 50.0) / 100.0
    near = 1.0 - _clip(abs(_num(m.get("dist_pivot")) or 0.1) / g["max_dist"])
    return round(0.25 * tight + 0.20 * rs + 0.20 * near + 0.15 * ln
                 + 0.10 * contract + 0.10 * dry, 4)


def _rv_quality(m: dict, g: dict = RV, fund: dict | None = None) -> float:
    """0..1. Diem co ban chiem phan lon: voi setup nay do la ca van de."""
    deep = _clip(((_num(m.get("off_high")) or 0.0) - g["min_off_high"]) / 0.35)
    fresh = 1.0 - _clip((m.get("days_since_low") or 0) / g["max_days_since_low"])
    liq = _clip((_num(m.get("adv20")) or 0.0) / 3_000_000)
    fs = 0.0 if fund is None else _clip(float(fund.get("score") or 0.0))
    return round(0.45 * fs + 0.25 * deep + 0.15 * fresh + 0.15 * liq, 4)


def scan(struct: dict[str, dict], fund: dict[str, dict] | None = None,
         max_cand: int = MAX_CAND) -> tuple[list[dict], dict]:
    """Ca bang `struct` -> danh sach candidate + bang ly do bi loai."""
    fund = fund or {}
    rej: dict = {"BO": {}, "RV": {}}
    out: dict[str, list[dict]] = {"BO": [], "RV": []}
    for sym, m in struct.items():
        m = {**m, "sym": sym}
        c = bo_candidate(m, rej=rej["BO"])
        if c:
            out["BO"].append(c)
        c = rv_candidate(m, fund=fund.get(sym), rej=rej["RV"])
        if c:
            out["RV"].append(c)

    rows = []
    for k, lst in out.items():
        lst.sort(key=lambda c: -c["quality"])
        rej[k]["_qua_loc"] = len(lst)
        rej[k]["_bi_cat_tran"] = max(0, len(lst) - max_cand)
        rows += lst[:max_cand]
    rej["_cho_fund"] = sum(1 for c in rows
                           if c["setup"] == "RV" and c["fund_ok"] is None)
    return rows, rej


# ───────────────────────── buoc 2: kich hoat trong phien ──────────────────
def _close_pos(q: dict, px: float) -> float | None:
    hi, lo = _num(q.get("hi")), _num(q.get("lo"))
    if hi is None or lo is None or hi <= lo:
        return None
    return _clip((px - lo) / (hi - lo))


def trig_bo(c: dict, q: dict, g: dict = BO,
            rej: dict | None = None) -> dict | None:
    """Nen da vuot pivot chua? `q` = {px, vol, rvol, hi, lo, open}.

    `rvol` phai la RVOL da chuan hoa theo phan phien da qua (vprofile.py) -
    dua vao day thay vi tu tinh, de setups.py khong phu thuoc gio phien.
    """
    px, pivot = _num(q.get("px")), _num(c.get("pivot"))
    if not px or not pivot:
        return _rej(rej, "thieu gia / pivot")
    if px < pivot * (1.0 + g["over_pivot"]):
        return _rej(rej, "chua vuot pivot")
    if px > pivot * (1.0 + g["max_over_pivot"]):
        return _rej(rej, "da chay qua xa pivot")

    rv = _num(q.get("rvol")) or 0.0
    if rv < g["rvol"]:
        return _rej(rej, f"rvol < {g['rvol']}")
    vol = _num(q.get("vol")) or 0.0
    if px * vol < g["dvol"]:
        return _rej(rej, "thanh khoan trong ngay thap")

    cp = _close_pos(q, px)
    if cp is not None and cp < g["close_pos"]:
        return _rej(rej, "dang o nua duoi bien do ngay")

    ref = _num(c.get("ref_close")) or px
    op = _num(q.get("open"))
    gap = (op / ref - 1.0) if (op and ref) else 0.0
    warn = ["gap to, entry xau"] if gap > g["max_gap"] else []
    return {"sym": c["sym"], "setup": "BO", "px": px, "rvol": rv,
            "chg": px / ref - 1.0, "over_pivot": px / pivot - 1.0,
            "pivot": pivot, "base_len": c.get("base_len"),
            "base_depth": c.get("base_depth"), "rs_pct": c.get("rs_pct"),
            "close_pos": cp, "gap": gap, "quality": c.get("quality"),
            "warn": warn}


def trig_rv(c: dict, q: dict, g: dict = RV,
            rej: dict | None = None) -> dict | None:
    """Mot phien bat manh tu day, co xac nhan lay lai sma20."""
    px = _num(q.get("px"))
    ref = _num(c.get("ref_close"))
    if not px or not ref:
        return _rej(rej, "thieu gia")
    if g["require_fund"] and c.get("fund_ok") is not True:
        # Chua co so lieu co ban thi KHONG alert. Day la ca ly do ton tai cua
        # setup nay: khong co no thi RV chi la "bat dao roi".
        return _rej(rej, "chua co so lieu co ban")

    chg = px / ref - 1.0
    if chg < g["chg"]:
        return _rej(rej, f"tang < {g['chg']:.0%}")
    rv = _num(q.get("rvol")) or 0.0
    if rv < g["rvol"]:
        return _rej(rej, f"rvol < {g['rvol']}")
    vol = _num(q.get("vol")) or 0.0
    if px * vol < g["dvol"]:
        return _rej(rej, "thanh khoan trong ngay thap")

    cp = _close_pos(q, px)
    if cp is None or cp < g["close_pos"]:
        return _rej(rej, "khong dong o vung dinh ngay")

    sma20 = _num(c.get("sma20"))
    if sma20 and px < sma20 * (1.0 + g["over_sma20"]):
        return _rej(rej, "chua lay lai sma20")

    return {"sym": c["sym"], "setup": "RV", "px": px, "rvol": rv, "chg": chg,
            "off_high": c.get("off_high"), "fund_ok": c.get("fund_ok"),
            "close_pos": cp, "quality": c.get("quality"), "warn": []}


TRIG = {"BO": trig_bo, "RV": trig_rv}


def check(c: dict, q: dict, rej: dict | None = None) -> dict | None:
    """Goi dung ham kich hoat theo `c["setup"]`."""
    f = TRIG.get(c.get("setup"))
    return f(c, q, rej=rej) if f else None


# ───────────────────────── bang `candidates` ─────────────────────────
COLS = ("setup", "d", "ref_close", "pivot", "sma20", "adv20", "atr_pct",
        "base_len", "base_depth", "off_high", "rs_pct", "dist_pivot",
        "fund_ok", "quality")

DDL = """
CREATE TABLE IF NOT EXISTS candidates(
  sym TEXT, setup TEXT, d TEXT,
  ref_close REAL, pivot REAL, sma20 REAL, adv20 REAL, atr_pct REAL,
  base_len INTEGER, base_depth REAL, off_high REAL, rs_pct REAL,
  dist_pivot REAL, fund_ok INTEGER, quality REAL, updated TEXT,
  PRIMARY KEY(sym, setup)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_cand_setup ON candidates(setup, quality);
"""


def con(db=DB) -> sqlite3.Connection:
    c = bars.con(db) if not isinstance(db, sqlite3.Connection) else db
    c.executescript(DDL)
    return c


def build(db=DB, fund: dict[str, dict] | None = None,
          max_cand: int = MAX_CAND) -> dict:
    """Bang `struct` -> bang `candidates`. Ghi de ca bang trong 1 transaction.

    Ly do ghi de thay vi upsert giong structure.build(): mot ma khong con nen
    thi phai BIEN MAT khoi danh sach theo doi. Upsert se de lai pivot cu cua
    hom qua va bot se canh mot muc gia khong con y nghia gi.
    """
    c = con(db)
    st = structure.load_struct(c)
    rows, rej = scan(st, fund, max_cand)

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    ins = (f"INSERT INTO candidates(sym,{','.join(COLS)},updated) "
           f"VALUES({','.join('?' * (len(COLS) + 2))})")
    with c:
        c.execute("DELETE FROM candidates")
        c.executemany(ins, [(r["sym"], *(r.get(k) for k in COLS), now)
                            for r in rows])
    if not isinstance(db, sqlite3.Connection):
        c.close()
    n = {k: sum(1 for r in rows if r["setup"] == k) for k in ("BO", "RV")}
    return {"struct": len(st), "BO": n["BO"], "RV": n["RV"],
            "cho_fund": rej["_cho_fund"], "rej": rej}


def load_candidates(db=DB, setup: str | None = None,
                    max_age: int | None = MAX_AGE,
                    today: str | None = None) -> dict[str, dict]:
    """Danh sach theo doi cho phien hom nay.

    `max_age` la chot an toan that su can: struct/candidates dung yen vi cron
    chet thi bot van canh pivot cu suot nhieu tuan va khong co gi bao loi. Qua
    han -> tra ve rong, tot hon la alert dua tren nen da vo tu lau.
    """
    c, mine = bars._c(db)
    try:
        c.executescript(DDL)
        q = f"SELECT sym,{','.join(COLS)} FROM candidates"
        args: list = []
        if setup:
            q += " WHERE setup=?"
            args.append(setup)
        rows = c.execute(q, args).fetchall()
    finally:
        if mine:
            c.close()

    out = {}
    lim = None
    if max_age is not None:
        t = dt.date.fromisoformat(today) if today else dt.date.today()
        lim = (t - dt.timedelta(days=max_age)).isoformat()
    for r in rows:
        d = dict(zip(COLS, r[1:]))
        d["sym"] = r[0]
        if lim and (d["d"] or "") < lim:
            continue
        out[r[0]] = d
    return out


# ───────────────────────── selftest ─────────────────────────
def _base_row(**kw) -> dict:
    """Mot dong `struct` cua ma dang o nen tich luy chat, sat pivot."""
    m = {"sym": "AAA", "d": "2024-06-03", "px": 20.0, "sma20": 19.8,
         "sma50": 19.0, "sma200": 17.0, "sma50_slope": 0.001,
         "hi52": 21.0, "lo52": 12.0, "off_high": 0.048, "up_from_low": 0.67,
         "adv20": 800_000.0, "adv50": 900_000.0, "dryup": 0.8,
         "atr14": 0.4, "atr_pct": 0.02, "atr_contract": 0.6, "ret63": 0.1,
         "rs_pct": 80.0, "base_len": 40, "base_depth": 0.09,
         "base_slope": 0.0002, "base_dryup": 0.7, "pivot": 20.6,
         "dist_pivot": 0.03, "depth20": 0.05, "tight10": 0.01,
         "close_pos": 0.6, "gap": 0.0, "vol_ratio": 1.1,
         "below20_streak": 0, "days_since_low": 180, "n_bars": 300}
    m.update(kw)
    return m


def _dip_row(**kw) -> dict:
    """Mot dong `struct` cua ma vua roi 70%, day 52 tuan cach day 8 phien."""
    m = _base_row(px=6.0, sma20=6.4, sma50=8.0, sma200=12.0,
                  sma50_slope=-0.004, hi52=22.0, lo52=5.4, off_high=0.727,
                  up_from_low=0.11, ret63=-0.45, rs_pct=3.0, base_len=0,
                  base_depth=None, base_slope=None, pivot=None,
                  dist_pivot=None, days_since_low=8, below20_streak=30,
                  adv20=1_200_000.0)
    m.update(kw)
    return m


def _smoke() -> None:
    import tempfile

    # --- buoc 1: BO ---
    assert bo_candidate(_base_row())
    assert bo_candidate(_base_row(base_len=15)) is None, "nen qua ngan"
    assert bo_candidate(_base_row(base_depth=0.28)) is None, "nen qua rong"
    # duoi $10 thi nen 28% van chap nhan
    assert bo_candidate(_base_row(px=8.0, sma20=7.9, sma50=7.6, hi52=8.3,
                                  pivot=8.2, base_depth=0.28))
    assert bo_candidate(_base_row(off_high=0.40)) is None, "giua cu roi"
    assert bo_candidate(_base_row(sma50_slope=-0.001)) is None
    assert bo_candidate(_base_row(px=18.0, sma50=19.0)) is None, "duoi sma50"
    assert bo_candidate(_base_row(dist_pivot=0.30)) is None, "con xa pivot"
    assert bo_candidate(_base_row(dist_pivot=-0.10)) is None, "vao muon"
    assert bo_candidate(_base_row(adv20=50_000.0)) is None
    assert bo_candidate(_dip_row()) is None, "ma roi sau khong phai BO"

    # --- buoc 1: RV ---
    assert rv_candidate(_dip_row())
    assert rv_candidate(_base_row()) is None, "ma sat dinh khong phai RV"
    assert rv_candidate(_dip_row(px=2.0)) is None, "duoi $3"
    assert rv_candidate(_dip_row(adv20=300_000.0)) is None, "adv20 < 500k"
    assert rv_candidate(_dip_row(days_since_low=60)) is None, "day qua cu"
    assert rv_candidate(_dip_row(ret63=0.05)) is None, "chua giam"
    assert rv_candidate(_dip_row(up_from_low=0.60)) is None, "bat qua nhieu"
    assert rv_candidate(_dip_row(), fund={"ok": False}) is None
    # thieu so lieu co ban: van theo doi, nhung fund_ok phai la None
    assert rv_candidate(_dip_row())["fund_ok"] is None
    assert rv_candidate(_dip_row(), fund={"ok": True, "score": 0.8})["fund_ok"]

    # nen chat hon thi xep hang cao hon
    assert (_bo_quality(_base_row(base_depth=0.06))
            > _bo_quality(_base_row(base_depth=0.18)))

    # --- buoc 2: BO ---
    c = bo_candidate(_base_row())
    q = {"px": 21.0, "vol": 2_000_000, "rvol": 2.5, "hi": 21.1, "lo": 20.2,
         "open": 20.3}
    t = trig_bo(c, q)
    assert t and t["setup"] == "BO" and t["over_pivot"] > 0.005
    assert not t["warn"]
    assert trig_bo(c, {**q, "px": 20.55}) is None, "cham pivot chua phai vuot"
    assert trig_bo(c, {**q, "rvol": 1.2}) is None
    assert trig_bo(c, {**q, "px": 24.5, "hi": 24.6, "lo": 20.5}) is None, \
        "da chay qua xa pivot"
    assert trig_bo(c, {**q, "lo": 19.0, "hi": 21.2, "px": 19.6}) is None, \
        "nua duoi bien do ngay"
    assert trig_bo(c, {**q, "vol": 1000}) is None, "thanh khoan qua mong"
    assert trig_bo(c, {**q, "open": 22.5})["warn"], "gap 12% phai co canh bao"

    # --- buoc 2: RV ---
    cr = rv_candidate(_dip_row(), fund={"ok": True, "score": 0.7})
    qr = {"px": 6.7, "vol": 5_000_000, "rvol": 4.0, "hi": 6.75, "lo": 6.05,
          "open": 6.1}
    t = trig_rv(cr, qr)
    assert t and t["chg"] > 0.11 and t["setup"] == "RV"
    assert trig_rv(cr, {**qr, "px": 6.3, "hi": 6.35}) is None, "tang < 7%"
    assert trig_rv(cr, {**qr, "rvol": 2.0}) is None
    assert trig_rv(cr, {**qr, "px": 6.3, "hi": 6.8, "lo": 6.2}) is None, \
        "khong dong o vung dinh"
    # sma20 xa hon: tang 11% van chua lay lai duong trung binh -> chua xac nhan
    assert trig_rv(rv_candidate(_dip_row(sma20=7.5), fund={"ok": True}),
                   qr) is None, "chua lay lai sma20"

    # thieu co ban thi khong bao gio kich hoat, du gia chay bao nhieu
    assert trig_rv(rv_candidate(_dip_row()), qr) is None

    # --- ly do bi loai co dem ---
    rows, rej = scan({"AAA": _base_row(), "DIP": _dip_row(),
                      "TIN": _base_row(base_len=5, base_depth=None,
                                       pivot=None, dist_pivot=None)})
    assert {r["sym"] for r in rows} == {"AAA", "DIP"}
    assert rej["BO"]["_qua_loc"] == 1 and rej["RV"]["_qua_loc"] == 1
    assert rej["BO"]["khong co nen tich luy"] == 2  # DIP + TIN
    assert rej["_cho_fund"] == 1

    # --- bang candidates ---
    db = Path(tempfile.mkdtemp()) / "t.db"
    c2 = con(db)
    st = structure.con(c2)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    ins = (f"INSERT INTO struct(sym,{','.join(structure.COLS)},updated) "
           f"VALUES({','.join('?' * (len(structure.COLS) + 2))})")
    for sym, row in (("AAA", _base_row()), ("DIP", _dip_row())):
        st.execute(ins, (sym, *(row[k] for k in structure.COLS), now))
    st.commit()

    r = build(c2)
    assert r["BO"] == 1 and r["RV"] == 1 and r["cho_fund"] == 1, r
    got = load_candidates(c2, today="2024-06-05")
    assert set(got) == {"AAA", "DIP"}
    assert got["AAA"]["setup"] == "BO" and got["AAA"]["pivot"] == 20.6
    assert load_candidates(c2, setup="BO", today="2024-06-05").keys() == {"AAA"}
    # struct cu 3 tuan -> danh sach phai rong, khong duoc canh pivot da vo
    assert load_candidates(c2, today="2024-06-30") == {}
    assert len(load_candidates(c2, today="2024-06-30", max_age=None)) == 2

    # build lai khi khong con nen -> ma phai BIEN MAT, khong con pivot cu
    st.execute("UPDATE struct SET base_len=0, pivot=NULL, dist_pivot=NULL "
               "WHERE sym='AAA'")
    st.commit()
    build(c2)
    assert "AAA" not in load_candidates(c2, today="2024-06-05")
    c2.close()

    print("setups.py selftest: ok")


# ───────────────────────── CLI ─────────────────────────
def _show(db, setup: str | None, n: int) -> None:
    rows = sorted(load_candidates(db, setup, max_age=None).values(),
                  key=lambda r: (r["setup"], -(r["quality"] or 0)))
    if not rows:
        log("Bang candidates rong. Chay: python setups.py --build")
        return
    def pc(x, dp=0, sign="") -> str:
        return "-" if x is None else format(x, f"{sign}.{dp}%")

    log(f"{'MA':<7}{'SET':<5}{'NGAY':<12}{'GIA':>8}{'PIVOT':>8}"
        f"{'CACH':>7}{'NEN':>5}{'ROI':>7}{'RS':>5}{'DIEM':>7}")
    for r in rows[:n]:
        rs = r["rs_pct"]
        log(f"{r['sym']:<7}{r['setup']:<5}{r['d'] or '':<12}"
            f"{r['ref_close'] or 0:>8.2f}{r['pivot'] or 0:>8.2f}"
            f"{pc(r['dist_pivot'], 1, '+'):>7}{r['base_len'] or 0:>5}"
            f"{pc(r['off_high']):>7}"
            f"{('-' if rs is None else f'{rs:.0f}'):>5}"
            f"{r['quality'] or 0:>7.3f}")
    log(f"\nTong {len(rows)} ma dang theo doi.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="quet struct -> candidates")
    ap.add_argument("--show", nargs="?", const="", help="xem danh sach (BO/RV)")
    ap.add_argument("--n", type=int, default=25)
    args = ap.parse_args()

    if not args.build and args.show is None:
        _smoke()
        return 0

    if args.build:
        r = build(DB)
        log(f"struct: {r['struct']} ma -> BO {r['BO']}, RV {r['RV']} "
            f"(cho so lieu co ban: {r['cho_fund']})")
        for k in ("BO", "RV"):
            log(f"\nLy do bi loai ({k}):")
            for why, cnt in sorted(r["rej"][k].items(), key=lambda t: -t[1]):
                if not why.startswith("_"):
                    log(f"  {why:<45}{cnt:>6}")
    if args.show is not None:
        setup = args.show.upper() or None
        _show(DB, setup, args.n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
