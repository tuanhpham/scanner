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
import config
import plan
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

# Stage 3. Nguong that nam trong config.py de bang "Config" tren dashboard hien
# dung bo dang chay; o day chi la bi danh, va lead_candidate(m, g=...) van nhan
# dict tuy y cho backtest.py quet.
LEAD: dict = config.LEAD

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


def lead_candidate(m: dict, g: dict = LEAD, sector: str | None = None,
                   rej: dict | None = None) -> dict | None:
    """Co phieu dan dat trong mot sector manh. Stage 3 phan swing.

    Khac han BO/RV: day KHONG phai mot hinh mau ky thuat, ma la mot BO SAN
    chat luong. Y tuong: neu dong tien dang chay vao XLK, thi nhung ma XLK vua
    manh hon SPY vua o gan dinh vua du thanh khoan la nhung ma no chay vao.

    `sector` do build() truyen tu holdings.py. None = khong biet ma nay thuoc
    sector nao -> LOAI. "Khong biet" khong bao gio duoc coi la "thuoc top 3".
    """
    if not sector:
        return _rej(rej, "khong biet sector")

    px, adv50 = _num(m.get("px")), _num(m.get("adv50"))
    if not px or px < g["min_px"]:
        return _rej(rej, f"gia < ${g['min_px']:.0f}")

    # San thanh khoan tinh bang TIEN, khong bang so co phieu: 1 trieu co phieu
    # $3 va 1 trieu co phieu $300 la hai the gioi khac nhau. Day chinh la cho
    # scanner trong phien hien tai bi ro ri co phieu rac.
    dvol = (adv50 or 0.0) * px
    if dvol < g["min_dollar_vol"]:
        return _rej(rej, f"thanh khoan < ${g['min_dollar_vol'] / 1e6:.0f}M/phien")

    rvol = _num(m.get("vol_ratio"))
    if rvol is None or rvol < g["min_rvol"]:
        return _rej(rej, f"rvol < {g['min_rvol']}")

    atr = _num(m.get("atr_pct"))
    if atr is None:
        return _rej(rej, "khong do duoc bien do")
    if atr < g["min_atr_pct"]:
        return _rej(rej, f"bien do < {g['min_atr_pct']:.0%} (khong du dong)")
    if atr > g["max_atr_pct"]:
        return _rej(rej, f"bien do > {g['max_atr_pct']:.0%} (stop qua rong)")

    rs21, rs63 = _num(m.get("rs21")), _num(m.get("rs63"))
    if rs21 is None or rs63 is None:
        # Thieu ma chuan trong kho nen. Xem structure.build().
        return _rej(rej, "khong co so lieu RS (thieu ma chuan?)")
    if rs21 < g["min_rs21"]:
        return _rej(rej, "yeu hon SPY trong 21 phien")
    if rs63 < g["min_rs63"]:
        return _rej(rej, "yeu hon SPY trong 63 phien")

    off = _num(m.get("off_high"))
    if off is None or off > g["max_off_high"]:
        return _rej(rej, f"cach dinh 52 tuan > {g['max_off_high']:.0%}")

    return {"sym": m.get("sym"), "setup": "LEAD", "d": m.get("d"),
            "ref_close": px, "pivot": _num(m.get("pivot")),
            "sma20": _num(m.get("sma20")), "adv20": _num(m.get("adv20")),
            "atr_pct": atr, "base_len": int(m.get("base_len") or 0),
            "base_depth": _num(m.get("base_depth")), "off_high": off,
            "rs_pct": _num(m.get("rs_pct")), "dist_pivot": _num(m.get("dist_pivot")),
            "fund_ok": None, "sector": sector, "rs21": rs21, "rs63": rs63,
            "quality": _lead_quality(m, g)}


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


def _lead_quality(m: dict, g: dict = LEAD) -> float:
    """0..1, CHI la diem RS - dung nhu spec: "xep survivors theo diem RS".

    Co y khong cong them "gan dinh" hay "thanh khoan" vao day: chung da la BO
    LOC o tren. Mot tieu chi vua dung de loai vua dung de cham diem se tinh hai
    lan, va luc do khong con biet diem cao nghia la gi.

    63 phien nang hon 21: xu huong ba thang la cai ta muon bam theo, con mot
    thang chi de xac nhan no chua tat.
    """
    a = _clip((_num(m.get("rs21")) or 0.0) / g["rs_cap21"])
    b = _clip((_num(m.get("rs63")) or 0.0) / g["rs_cap63"])
    return round(0.6 * b + 0.4 * a, 4)


def lead_pick(lst: list[dict], g: dict = LEAD,
              rej: dict | None = None) -> list[dict]:
    """Ap tran: top `per_sector` moi sector, roi `max_total` tong.

    Hai tran chu khong mot: chi cat tong thi mot sector duy nhat co the chiem
    het 10 cho, va luc do "dan dat o top 3 sector" thanh "dan dat o mot sector".
    """
    lst = sorted(lst, key=lambda c: (-c["quality"], c["sym"]))
    dem: dict[str, int] = {}
    giu = []
    for c in lst:
        s = c.get("sector") or "?"
        if dem.get(s, 0) >= g["per_sector"]:
            _rej(rej, f"da du {g['per_sector']} ma cua {s}")
            continue
        dem[s] = dem.get(s, 0) + 1
        giu.append(c)
    if len(giu) > g["max_total"]:
        _rej(rej, f"qua tran {g['max_total']} ma tong")
    return giu[:g["max_total"]]


def scan(struct: dict[str, dict], fund: dict[str, dict] | None = None,
         max_cand: int = MAX_CAND,
         lead_sectors: dict[str, str] | None = None,
         size_mult: float = 1.0) -> tuple[list[dict], dict]:
    """Ca bang `struct` -> danh sach candidate + bang ly do bi loai.

    `lead_sectors` = {sym: sector} CHI gom nhung ma thuoc top 3 sector (xem
    holdings.allowed()). None/rong -> khong sinh LEAD nao, va bang rejects ghi
    ro ly do la "khong co danh sach sector" chu khong phai im lang.

    `size_mult` = co vi the cua playbook theo trang thai thi truong hom nay
    (config.PLAYBOOK[(trend, vol)]["size"]). Di thang vao plan.make() nen cot
    `size_pct` trong DB da la co vi the CUOI CUNG - phan intraday khong phai
    nhan lai voi cai gi, va do la mot phep nhan khong the bi quen.

    Thuan: khong DB, khong mang. Nho vay backtest.py quet duoc nhieu bo nguong,
    va test dung duoc fixture.
    """
    fund = fund or {}
    lead_sectors = lead_sectors or {}
    rej: dict = {"BO": {}, "RV": {}, "LEAD": {}}
    out: dict[str, list[dict]] = {"BO": [], "RV": [], "LEAD": []}
    if not lead_sectors:
        rej["LEAD"]["khong co danh sach sector"] = len(struct)
    for sym, m in struct.items():
        m = {**m, "sym": sym}
        c = bo_candidate(m, rej=rej["BO"])
        if c:
            out["BO"].append(c)
        c = rv_candidate(m, fund=fund.get(sym), rej=rej["RV"])
        if c:
            out["RV"].append(c)
        if lead_sectors:
            c = lead_candidate(m, sector=lead_sectors.get(sym), rej=rej["LEAD"])
            if c:
                out["LEAD"].append(c)

    # Ghi so ma qua duoc BO SAN truoc khi ap tran: neu 40 ma qua san ma chi
    # nhan 10, bang rejects phai cho thay ca hai con so. Chi thay "10" thi
    # khong biet la san chat qua hay tran chat qua.
    rej["LEAD"]["_qua_san"] = len(out["LEAD"])
    out["LEAD"] = lead_pick(out["LEAD"], rej=rej["LEAD"])

    rows = []
    for k, lst in out.items():
        lst.sort(key=lambda c: -c["quality"])
        rej[k]["_qua_loc"] = len(lst)
        rej[k]["_bi_cat_tran"] = max(0, len(lst) - max_cand)
        rows += lst[:max_cand]

    # Ke hoach lenh, gan sau khi da cat tran: khong tinh cho nhung ma khong vao
    # danh sach. `m` la dong struct goc, nen trigger/stop tinh tu nen quyet
    # dinh chu khong tu cac gia tri da lam tron trong candidate.
    dem_thieu = 0
    for c in rows:
        p = plan.make({**struct.get(c["sym"], {}), "sym": c["sym"]}, size_mult)
        if p is None:
            dem_thieu += 1
        c.update(p or {"trigger": None, "stop": None, "target": None,
                       "stop_pct": None, "risk_pct": None, "size_pct": None})
    if dem_thieu:
        # Khong chan, nhung phai dem duoc: mot ma khong co stop la mot ma phan
        # intraday se BO QUA, va "bi bo qua im lang" la dung cai loi phai tranh.
        rej["_khong_lap_duoc_ke_hoach"] = dem_thieu
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
        "fund_ok", "sector", "rs21", "rs63", "quality",
        # Ke hoach lenh, do plan.make() tinh tu nen quyet dinh. Phan intraday
        # DOC sau cot nay va khong tinh lai bat cu cai gi - do la ca diem cua
        # thiet ke: "toi khong ung bien giua phien, toi thuc hien mot quyet
        # dinh da lap tu dem truoc".
        "trigger", "stop", "target", "stop_pct", "risk_pct", "size_pct")

DDL = """
CREATE TABLE IF NOT EXISTS candidates(
  sym TEXT, setup TEXT, d TEXT,
  ref_close REAL, pivot REAL, sma20 REAL, adv20 REAL, atr_pct REAL,
  base_len INTEGER, base_depth REAL, off_high REAL, rs_pct REAL,
  dist_pivot REAL, fund_ok INTEGER,
  sector TEXT, rs21 REAL, rs63 REAL,
  quality REAL,
  trigger REAL, stop REAL, target REAL,
  stop_pct REAL, risk_pct REAL, size_pct REAL,
  updated TEXT,
  PRIMARY KEY(sym, setup)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_cand_setup ON candidates(setup, quality);
"""


def _migrate(c: sqlite3.Connection) -> bool:
    """Bang `candidates` thieu cot -> xoa va tao lai. Giong structure._migrate.

    An toan vi build() da DELETE ca bang moi lan chay: day la anh chup cua mot
    phien, khong phai du lieu tich luy. Lan build() ke tiep dien lai day du.
    """
    have = {x[1] for x in c.execute("PRAGMA table_info(candidates)")}
    if not have or have >= set(COLS) | {"sym", "updated"}:
        return False
    log(f"  [setups] bang `candidates` thieu cot {sorted(set(COLS) - have)} "
        f"-> xoa va tao lai")
    with c:
        c.execute("DROP TABLE candidates")
    c.executescript(DDL)
    return True


def con(db=DB) -> sqlite3.Connection:
    c = bars.con(db) if not isinstance(db, sqlite3.Connection) else db
    c.executescript(DDL)
    _migrate(c)
    return c


def lead_ctx(db, cfg: dict | None = None) -> dict:
    """Danh sach {sym: sector} cua top N sector, + ghi chu de bao cao.

    Day la cho DUY NHAT Stage 3 doc DB ngoai `struct`: bang `sector_rank` cua
    Stage 2 va file holdings tinh. Tach ra khoi scan() de scan() con thuan.
    """
    import holdings
    import sectors

    out: dict = {"map": {}, "top": [], "as_of": None, "warn": [], "d": None}
    rows = sectors.load_rank(db)
    if not rows:
        out["warn"].append("bang `sector_rank` chua co dong nao -> chay "
                           "`python sectors.py --build` truoc; khong co LEAD "
                           "nao trong phien nay")
        return out
    top = int((cfg or config.SECTORS)["top_n"])
    out["top"] = [r["sym"] for r in rows[:top]]
    out["d"] = rows[0]["d"]

    h = holdings.load()
    out["as_of"] = h["as_of"]
    if h["err"]:
        out["warn"].append(f"holdings: {h['err']}")
        return out
    s = holdings.stale(h["as_of"])
    if s:
        out["warn"].append(s)
    out["map"] = holdings.allowed(h["by_sym"], out["top"])
    if not out["map"]:
        out["warn"].append(f"khong co ma nao thuoc {' '.join(out['top'])} "
                           f"trong file holdings")
    return out


def size_mult(db, override: float | None = None) -> tuple[float, str]:
    """Co vi the cua playbook hom nay + mot cau giai thich (co dau).

    Doc dong `regime` moi nhat. KHONG co dong nao -> tra 0.0, tuc la "lap ke
    hoach nhung khong mo vi the moi". Do la lua chon co chu dinh: khong biet
    trang thai thi truong thi mac dinh phai la dung ngoai, chu khong phai full
    size. Mac dinh 1.0 o day se bien mot cron Stage 1 that bai thanh mot ngay
    vao lenh het co ma khong co gi bao.
    """
    if override is not None:
        return float(override), f"cỡ vị thế đặt tay: {float(override):.0%}"
    try:
        import regime
        r = regime.latest(db)
    except Exception as e:                              # noqa: BLE001 - xem duoi
        # Bat rong o day la co chu dinh va co pham vi: regime.py import pandas.
        # Tren may dev khong co pandas, `setups.py --build` van phai chay duoc.
        # Ly do duoc GHI LAI chu khong bo qua.
        return 0.0, f"không đọc được trạng thái thị trường ({type(e).__name__})"
    if not r:
        return 0.0, "chưa có bản ghi trạng thái thị trường → không mở vị thế mới"
    pb = config.PLAYBOOK.get((r["trend"], r["vol"]))
    if not pb:
        return 0.0, f"không có dòng playbook cho {r['trend']}/{r['vol']}"
    return float(pb["size"]), f"{r['trend']} · {r['vol']} → {pb['size']:.0%}"


def build(db=DB, fund: dict[str, dict] | None = None,
          max_cand: int = MAX_CAND, dry: bool = False,
          size: float | None = None) -> dict:
    """Bang `struct` -> bang `candidates`. Ghi de ca bang trong 1 transaction.

    Ly do ghi de thay vi upsert giong structure.build(): mot ma khong con nen
    thi phai BIEN MAT khoi danh sach theo doi. Upsert se de lai pivot cu cua
    hom qua va bot se canh mot muc gia khong con y nghia gi.

    `dry=True`: tinh het, in het, khong ghi gi. Dung de xem mot thay doi nguong
    lam gi TRUOC khi no thanh danh sach that.
    """
    c = con(db)
    st = structure.load_struct(c)
    ctx = lead_ctx(c)
    sz, sz_why = size_mult(c, size)
    rows, rej = scan(st, fund, max_cand, lead_sectors=ctx["map"], size_mult=sz)

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    ins = (f"INSERT INTO candidates(sym,{','.join(COLS)},updated) "
           f"VALUES({','.join('?' * (len(COLS) + 2))})")
    if not dry:
        with c:
            c.execute("DELETE FROM candidates")
            c.executemany(ins, [(r["sym"], *(r.get(k) for k in COLS), now)
                                for r in rows])
    if not isinstance(db, sqlite3.Connection):
        c.close()
    n = {k: sum(1 for r in rows if r["setup"] == k) for k in ("BO", "RV", "LEAD")}
    return {"struct": len(st), "BO": n["BO"], "RV": n["RV"], "LEAD": n["LEAD"],
            "cho_fund": rej["_cho_fund"], "rej": rej,
            "top_sector": ctx["top"], "holdings_as_of": ctx["as_of"],
            "size_mult": sz, "size_why": sz_why,
            "warn": ctx["warn"], "rows": rows, "dry": dry}


def load_candidates(db=DB, setup: str | None = None,
                    max_age: int | None = MAX_AGE,
                    today: str | None = None) -> dict[str, dict]:
    """Danh sach theo doi cho phien hom nay.

    `max_age` la chot an toan that su can: struct/candidates dung yen vi cron
    chet thi bot van canh pivot cu suot nhieu tuan va khong co gi bao loi. Qua
    han -> tra ve rong, tot hon la alert dua tren nen da vo tu lau.

    LUU Y khoa: dict nay khoa theo `sym`, con bang khoa theo (sym, setup). Mot
    ma vua la BO vua la LEAD (rat hay xay ra: ca hai deu doi gan dinh) thi chi
    con MOT dong o day. Truoc Stage 3 chuyen do khong the xay ra vi BO va RV
    loai tru nhau. Nen luon truyen `setup=` khi can chac chan lay dung dong;
    push.py doc truc tiep tu bang, khong qua ham nay.
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
         "hi52": 21.0, "lo52": 12.0, "hi1": 20.2, "lo1": 19.7,
         "off_high": 0.048, "up_from_low": 0.67,
         "adv20": 800_000.0, "adv50": 900_000.0, "dryup": 0.8,
         "atr14": 0.4, "atr_pct": 0.02, "atr_contract": 0.6, "ret63": 0.1,
         "ret21": 0.04, "rs_pct": 80.0, "rs21": 0.02, "rs63": 0.05,
         "base_len": 40, "base_depth": 0.09,
         "base_slope": 0.0002, "base_dryup": 0.7, "pivot": 20.6,
         "dist_pivot": 0.03, "depth20": 0.05, "tight10": 0.01,
         "close_pos": 0.6, "gap": 0.0, "vol_ratio": 1.1,
         "below20_streak": 0, "days_since_low": 180, "n_bars": 300}
    m.update(kw)
    return m


def _dip_row(**kw) -> dict:
    """Mot dong `struct` cua ma vua roi 70%, day 52 tuan cach day 8 phien."""
    m = _base_row(px=6.0, sma20=6.4, sma50=8.0, sma200=12.0, hi1=6.1, lo1=5.9,
                  sma50_slope=-0.004, hi52=22.0, lo52=5.4, off_high=0.727,
                  up_from_low=0.11, ret63=-0.45, ret21=-0.20, rs_pct=3.0,
                  rs21=-0.22, rs63=-0.50, base_len=0,
                  base_depth=None, base_slope=None, pivot=None,
                  dist_pivot=None, days_since_low=8, below20_streak=30,
                  adv20=1_200_000.0)
    m.update(kw)
    return m


def _lead_row(**kw) -> dict:
    """Mot dong `struct` cua co phieu dan dat: $60, thanh khoan $210M/phien.

    Co y de gia cao va khoi luong tien lon: day la hinh mau ma scanner trong
    phien hien tai KHONG BAO GIO tim thay, vi no xep hang theo % tang va co
    phieu $60 khong tang 15% mot ngay.
    """
    m = _base_row(sym="LDR", px=60.0, sma20=58.0, sma50=54.0, sma200=45.0,
                  hi1=60.5, lo1=59.1,
                  sma50_slope=0.010, hi52=62.0, lo52=38.0, off_high=0.032,
                  up_from_low=0.58, adv20=4_000_000.0, adv50=3_500_000.0,
                  atr14=1.8, atr_pct=0.03, vol_ratio=1.8,
                  ret63=0.22, ret21=0.09, rs_pct=95.0, rs21=0.05, rs63=0.11,
                  base_len=25, base_depth=0.08, pivot=61.0, dist_pivot=0.016)
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

    # --- buoc 1: LEAD (Stage 3) ---
    assert lead_candidate(_lead_row(), sector="XLK")["sector"] == "XLK"
    # "khong biet sector" khong bao gio duoc coi la "thuoc top 3"
    assert lead_candidate(_lead_row()) is None
    assert lead_candidate(_lead_row(), sector="") is None
    # tung san mot, va MOI san phai chan duoc rieng
    assert lead_candidate(_lead_row(px=9.0), sector="XLK") is None, "duoi $10"
    assert lead_candidate(_lead_row(adv50=200_000.0), sector="XLK") is None, \
        "200k x $60 = $12M < san $20M"
    assert lead_candidate(_lead_row(vol_ratio=1.1), sector="XLK") is None
    assert lead_candidate(_lead_row(atr_pct=0.012), sector="XLK") is None, \
        "qua yen"
    assert lead_candidate(_lead_row(atr_pct=0.09), sector="XLK") is None, \
        "qua dong"
    assert lead_candidate(_lead_row(rs21=-0.01), sector="XLK") is None
    assert lead_candidate(_lead_row(rs63=-0.01), sector="XLK") is None
    assert lead_candidate(_lead_row(rs63=None), sector="XLK") is None, \
        "thieu RS = LOAI, khong phai 'tam coi la 0'"
    assert lead_candidate(_lead_row(off_high=0.30), sector="XLK") is None

    # Day la khac biet cot loi so voi scanner hien tai. Hai kieu rac, hai san
    # khac nhau chan:
    #   1. $3 tang 15% -> chan boi san gia.
    ly_do: dict = {}
    assert lead_candidate(_lead_row(px=3.0, adv50=2_000_000.0), sector="XLK",
                          rej=ly_do) is None
    assert any("gia <" in k for k in ly_do), ly_do
    #   2. $12, RS cao, sat dinh, nhung moi phien chi giao dich $12M -> khong co
    #      to chuc nao trong do, spread rong, thoat lenh se truot. Day la kieu
    #      rac kho thay hon, va no la ly do san tinh bang TIEN.
    ly_do = {}
    mong = _lead_row(px=12.0, adv50=1_000_000.0, sma50=10.5, hi52=12.4,
                     off_high=0.03, atr_pct=0.05)
    assert 1_000_000.0 * 12.0 < LEAD["min_dollar_vol"]
    assert lead_candidate(mong, sector="XLK", rej=ly_do) is None
    assert any("thanh khoan" in k for k in ly_do), ly_do

    # BO candidate KHONG tu dong la LEAD: _base_row thanh khoan $18M
    assert bo_candidate(_base_row()) and lead_candidate(_base_row(),
                                                        sector="XLK") is None

    # diem = RS, 63 phien nang hon 21
    assert (_lead_quality(_lead_row(rs63=0.25))
            > _lead_quality(_lead_row(rs63=0.05)))
    assert _lead_quality(_lead_row(rs63=0.9)) == _lead_quality(
        _lead_row(rs63=LEAD["rs_cap63"])), "phai cat tran"
    manh21 = _lead_quality(_lead_row(rs21=LEAD["rs_cap21"], rs63=0.0))
    manh63 = _lead_quality(_lead_row(rs21=0.0, rs63=LEAD["rs_cap63"]))
    assert manh63 > manh21

    # --- tran: 5 moi sector, 10 tong ---
    nhieu = []
    for i in range(8):
        for s in ("XLK", "XLF", "XLE"):
            nhieu.append(lead_candidate(
                _lead_row(sym=f"{s}{i}", rs63=0.05 + i * 0.01), sector=s))
    ly_do = {}
    giu = lead_pick(nhieu, rej=ly_do)
    assert len(giu) == LEAD["max_total"] == 10
    dem: dict[str, int] = {}
    for x in giu:
        dem[x["sector"]] = dem.get(x["sector"], 0) + 1
    assert max(dem.values()) <= LEAD["per_sector"], dem
    assert len(dem) >= 2, "mot sector khong duoc chiem het 10 cho"
    assert any("da du 5 ma" in k for k in ly_do), ly_do

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
    st_in = {"AAA": _base_row(), "DIP": _dip_row(),
             "TIN": _base_row(base_len=5, base_depth=None,
                              pivot=None, dist_pivot=None)}
    rows, rej = scan(st_in)
    assert {r["sym"] for r in rows} == {"AAA", "DIP"}
    assert rej["BO"]["_qua_loc"] == 1 and rej["RV"]["_qua_loc"] == 1
    assert rej["BO"]["khong co nen tich luy"] == 2  # DIP + TIN
    assert rej["_cho_fund"] == 1
    # khong co danh sach sector -> khong co LEAD, va NOI RO ly do
    assert rej["LEAD"]["_qua_loc"] == 0
    assert rej["LEAD"]["khong co danh sach sector"] == 3

    # co danh sach sector -> LEAD chay, va chi voi nhung ma trong danh sach
    rows2, rej2 = scan({**st_in, "LDR": _lead_row(), "NGO": _lead_row()},
                       lead_sectors={"LDR": "XLK"})
    ldr = [r for r in rows2 if r["setup"] == "LEAD"]
    assert [r["sym"] for r in ldr] == ["LDR"], ldr
    assert rej2["LEAD"]["khong biet sector"] == 4, rej2["LEAD"]
    assert rej2["LEAD"]["_qua_san"] == 1 and rej2["LEAD"]["_qua_loc"] == 1
    # LDR cung la BO candidate -> mot ma duoc phep o hai setup, hai dong rieng
    assert {(r["sym"], r["setup"]) for r in rows2} >= {("LDR", "BO"),
                                                       ("LDR", "LEAD")}

    # --- bang candidates ---
    db = Path(tempfile.mkdtemp()) / "t.db"
    c2 = con(db)
    st = structure.con(c2)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    ins = (f"INSERT INTO struct(sym,{','.join(structure.COLS)},updated) "
           f"VALUES({','.join('?' * (len(structure.COLS) + 2))})")
    for sym, row in (("AAA", _base_row()), ("DIP", _dip_row()),
                     ("LDR", _lead_row()), ("AAPL", _lead_row(sym="AAPL"))):
        st.execute(ins, (sym, *(row[k] for k in structure.COLS), now))
    st.commit()

    # Chua co bang `sector_rank` -> khong LEAD nao, nhung phai CANH BAO chu
    # khong im lang tra 0.
    r = build(c2)
    assert r["LEAD"] == 0 and any("sector_rank" in w for w in r["warn"]), r
    assert r["top_sector"] == []

    # Co xep hang sector -> LEAD chay. XLK trong top 3 nen AAPL... o day dung
    # chinh LDR, va de duoc chon thi LDR phai co trong file holdings that.
    import holdings as _h
    import sectors as _sec
    _sec.con(c2)
    _sec.save(c2, "2024-06-03",
              [{"sym": s, "rank": i, "composite": 100.0 - i * 10}
               for i, s in enumerate(config.SECTOR_ETFS, 1)])
    r = build(c2)
    assert r["top_sector"] == list(config.SECTOR_ETFS[:3]), r["top_sector"]
    assert r["holdings_as_of"] == _h.load()["as_of"]
    # AAPL co trong holdings.csv o XLK -> duoc chon. LDR thi khong co o dau ca
    # -> bi loai. Danh sach chi duoc lay tu thanh phan sector, khong tu ca kho
    # nen: do la toan bo y nghia cua "co phieu dan dat trong sector manh".
    assert r["LEAD"] == 1, r
    assert _h.load()["by_sym"].get("AAPL") == "XLK"

    assert r["BO"] == 3 and r["RV"] == 1 and r["cho_fund"] == 1, r
    got = load_candidates(c2, today="2024-06-05")
    assert set(got) == {"AAA", "DIP", "LDR", "AAPL"}
    # cot moi phai di duoc qua DB, khong chi ton tai trong bo nho
    ld = load_candidates(c2, setup="LEAD", today="2024-06-05")["AAPL"]
    assert ld["sector"] == "XLK"
    assert abs(ld["rs63"] - 0.11) < 1e-9 and abs(ld["rs21"] - 0.05) < 1e-9
    assert got["AAA"]["setup"] == "BO" and got["AAA"]["pivot"] == 20.6
    assert load_candidates(c2, setup="BO", today="2024-06-05").keys() == {
        "AAA", "LDR", "AAPL"}
    # struct cu 3 tuan -> danh sach phai rong, khong duoc canh pivot da vo
    assert load_candidates(c2, today="2024-06-30") == {}
    assert len(load_candidates(c2, today="2024-06-30", max_age=None)) == 4

    # build lai khi khong con nen -> ma phai BIEN MAT, khong con pivot cu
    st.execute("UPDATE struct SET base_len=0, pivot=NULL, dist_pivot=NULL "
               "WHERE sym='AAA'")
    st.commit()
    build(c2)
    assert "AAA" not in load_candidates(c2, today="2024-06-05")
    c2.close()

    print("setups.py selftest: ok")


# ───────────────────────── CLI ─────────────────────────
def _table(rows: list[dict], n: int = 25) -> None:
    """Mot bang duy nhat cho ca --show va --dry-run.

    Hai ham in rieng se lech nhau dung luc can so sanh "danh sach dry-run" voi
    "danh sach da ghi".
    """
    def pc(x, dp=0, sign="") -> str:
        return "-" if x is None else format(x, f"{sign}.{dp}%")

    log(f"{'MA':<7}{'SET':<6}{'SECTOR':<7}{'NGAY':<12}{'GIA':>8}{'PIVOT':>8}"
        f"{'CACH':>7}{'NEN':>5}{'ROI':>7}{'RS21':>7}{'RS63':>7}{'DIEM':>7}")
    for r in rows[:n]:
        log(f"{r['sym']:<7}{r['setup']:<6}{r.get('sector') or '-':<7}"
            f"{r['d'] or '':<12}"
            f"{r['ref_close'] or 0:>8.2f}{r['pivot'] or 0:>8.2f}"
            f"{pc(r['dist_pivot'], 1, '+'):>7}{r['base_len'] or 0:>5}"
            f"{pc(r['off_high']):>7}"
            f"{pc(r.get('rs21'), 1, '+'):>7}{pc(r.get('rs63'), 1, '+'):>7}"
            f"{r['quality'] or 0:>7.3f}")
    log(f"\nTong {len(rows)} ma dang theo doi.")


def _show(db, setup: str | None, n: int) -> None:
    rows = sorted(load_candidates(db, setup, max_age=None).values(),
                  key=lambda r: (r["setup"], -(r["quality"] or 0)))
    if not rows:
        log("Bang candidates rong. Chay: python setups.py --build")
        return
    _table(rows, n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="quet struct -> candidates")
    ap.add_argument("--dry-run", action="store_true", dest="dry",
                    help="tinh va in het, khong ghi bang candidates")
    ap.add_argument("--show", nargs="?", const="",
                    help="xem danh sach (BO/RV/LEAD)")
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()

    if not args.build and not args.dry and args.show is None:
        _smoke()
        return 0

    db = Path(args.db)
    if args.build or args.dry:
        r = build(db, dry=args.dry)
        log(f"struct: {r['struct']} ma -> BO {r['BO']}, RV {r['RV']}, "
            f"LEAD {r['LEAD']} (cho so lieu co ban: {r['cho_fund']})")
        log(f"top sector: {' '.join(r['top_sector']) or '(chua co)'}"
            f"   holdings as_of {r['holdings_as_of'] or '?'}")
        for w in r["warn"]:
            log(f"  canh bao: {w}")
        for k in ("BO", "RV", "LEAD"):
            rj = r["rej"][k]
            log(f"\nLy do bi loai ({k}):")
            for why, cnt in sorted(rj.items(), key=lambda t: -t[1]):
                if not why.startswith("_"):
                    log(f"  {why:<45}{cnt:>6}")
            if k == "LEAD":
                # Hai con so nay phai in canh nhau: "qua san" la bo loc chat hay
                # long, "nhan" la tran chat hay long. Chi thay mot con so thi
                # khong biet nen sua cai nao.
                log(f"  {'-> qua het san chat luong':<45}"
                    f"{rj.get('_qua_san', 0):>6}")
                log(f"  {'-> nhan vao danh sach (sau khi ap tran)':<45}"
                    f"{rj.get('_qua_loc', 0):>6}")
        if args.dry:
            ld = sorted((x for x in r["rows"] if x["setup"] == "LEAD"),
                        key=lambda x: -x["quality"])
            log("\nDANH SACH LEAD:")
            _table(ld, args.n)
            log("\n(--dry-run: khong ghi bang candidates)")
    if args.show is not None:
        setup = args.show.upper() or None
        _show(db, setup, args.n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
