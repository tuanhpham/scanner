"""backtest.py - chay lai hai setup tren nen ngay qua khu.

Backtest sai thi khong ai phat hien ra, vi no khong bao gio bao loi - no chi in
ra mot con so dep hon su that. Bon thu phai dung:

1. KHONG NHIN TRUOC TUONG LAI. Gia vao lenh va viec lenh co duoc sinh ra hay
   khong phai giong nhau du cat chuoi nen ngay sau ngay vao lenh.
2. GOI DUNG HAM CUA BOT. Doi mot nguong trong setups.BO thi ket qua backtest
   phai doi theo - khong duoc co ban sao logic nao trong backtest.py.
3. SANG LOC RE KHONG DUOC DOI KET QUA. _could_fire() bo qua ~90% ngay de chay
   nhanh; neu no bo qua oan mot ngay thi so lenh giam ma khong ai biet.
4. THIEU DU LIEU = None, KHONG PHAI 0. r20 chua du nen ma tinh la 0% thi win20
   bi keo xuong bang cach am tham.
"""
from __future__ import annotations

import datetime as dt

import _util

bt, se, st, b = _util.need("backtest", "setups", "structure", "bars")
Bar = b.Bar


def _bo_series() -> list:
    """Tang 60 phien -> nen phang 40 phien -> breakout volume 4x -> tang tiep."""
    bs = bt._mk([(10.0, 20.0, 60, 0.02), (20.0, 20.0, 40, 0.008)])
    d = dt.date.fromisoformat(bs[-1].d) + dt.timedelta(days=1)
    bs.append(Bar(d.isoformat(), 20.2, 21.6, 20.1, 21.5, 4e6))
    for k in range(30):
        d += dt.timedelta(days=1)
        p = 21.5 * (1 + 0.008 * (k + 1))
        bs.append(bt._bar(d.isoformat(), p, 1.5e6))
    return bs


def _rv_series() -> list:
    """Roi 100 -> 25, nam im 15 phien, 3 phien bat +9% volume 6x."""
    bs = bt._mk([(100.0, 25.0, 150, 0.02), (25.0, 25.0, 15, 0.01)])
    d = dt.date.fromisoformat(bs[-1].d)
    px = 25.0
    for _ in range(3):
        d += dt.timedelta(days=1)
        px *= 1.09
        bs.append(Bar(d.isoformat(), px / 1.08, px * 1.002, px / 1.085, px, 6e6))
    bs += bt._mk([(px, px * 1.1, 25, 0.02)],
                 start=(d + dt.timedelta(days=1)).isoformat())
    return bs


FUND = {"DIP": {"ok": True, "score": 0.8}}


# ───────────────────── 1. khong nhin truoc tuong lai ─────────────────────
def test_cat_chuoi_sau_khi_vao_lenh_khong_doi_gi():
    bs = _bo_series()
    full = bt.walk({"AAA": bs})
    assert len(full) == 1
    t = full[0]
    j = next(k for k, x in enumerate(bs) if x.d == t["d_entry"])
    cut = bt.walk({"AAA": bs[:j + 1]})
    assert len(cut) == 1, "lenh phai duoc sinh ra du khong biet tuong lai"
    assert cut[0]["d_entry"] == t["d_entry"]
    assert cut[0]["entry"] == t["entry"]
    assert cut[0]["rvol"] == t["rvol"] and cut[0]["base_len"] == t["base_len"]
    assert cut[0]["r5"] is None and cut[0]["mfe"] is None


def test_candidate_sinh_truoc_ngay_kich_hoat():
    t = bt.walk({"AAA": _bo_series()})[0]
    assert t["d_setup"] < t["d_entry"]


def test_vao_lenh_o_gia_dong_cua_ngay_kich_hoat():
    bs = _bo_series()
    t = bt.walk({"AAA": bs})[0]
    bar = next(x for x in bs if x.d == t["d_entry"])
    assert t["entry"] == bar.c
    # r1 doc dung nen ke tiep
    nxt = bs[bs.index(bar) + 1]
    assert abs(t["r1"] - (nxt.c / bar.c - 1.0)) < 1e-12


# ───────────────────── 2. dung dung code cua bot ─────────────────────
def test_doi_nguong_setups_thi_ket_qua_doi_theo():
    bs = _bo_series()
    assert bt.walk({"AAA": bs})
    assert bt.walk({"AAA": bs}, bo={**se.BO, "rvol": 9.0}) == []
    assert bt.walk({"AAA": bs}, bo={**se.BO, "min_base_len": 80}) == []
    assert bt.walk({"AAA": bs}, bo={**se.BO, "max_over_pivot": 0.01}) == []


def test_rv_thieu_co_ban_khong_ra_lenh():
    """Giong bot: fund_ok=None khong bao gio duoc coi la dat."""
    bs = _rv_series()
    assert bt.walk({"DIP": bs}) == []
    assert bt.walk({"DIP": bs}, fund=FUND)
    assert bt.walk({"DIP": bs}, rv={**se.RV, "require_fund": False})


def test_khong_breakout_thi_khong_co_lenh():
    assert bt.walk({"BBB": bt._mk([(10.0, 20.0, 60), (20.0, 20.0, 60)])}) == []


def test_volume_khong_tang_thi_khong_phai_breakout():
    bs = bt._mk([(10.0, 20.0, 60, 0.02), (20.0, 20.0, 40, 0.008)])
    d = dt.date.fromisoformat(bs[-1].d) + dt.timedelta(days=1)
    bs.append(Bar(d.isoformat(), 20.2, 21.6, 20.1, 21.5, 1.0e6))   # rvol 1.0
    bs += bt._mk([(21.5, 26.0, 30)],
                 start=(d + dt.timedelta(days=1)).isoformat())
    assert bt.walk({"CCC": bs}) == []


# ───────────────────── 3. sang loc re ─────────────────────
def test_sang_loc_re_la_dieu_kien_can_cua_ca_hai_setup():
    """_could_fire() chi duoc bo qua ngay ma CA HAI setup deu khong the dat."""
    g = min(se.BO["rvol"], se.RV["rvol"])
    dv = min(se.BO["dvol"], se.RV["dvol"])
    assert g <= se.BO["rvol"] and g <= se.RV["rvol"]
    assert dv <= se.BO["dvol"] and dv <= se.RV["dvol"]
    assert bt._could_fire(20.0, 4e6, 1e6, g, dv)
    assert not bt._could_fire(20.0, 1.0e6, 1e6, g, dv)
    assert not bt._could_fire(0.05, 4e6, 1e6, g, dv), "dollar-vol qua thap"
    assert not bt._could_fire(20.0, 4e6, 0.0, g, dv), "chua co adv20"


def test_adv_khong_gom_nen_dang_xet():
    vols = [1e6] * 20 + [9e6]
    assert bt._adv(vols, 20) == 1e6, "nen thu 20 bi gom vao mau so"
    assert bt._adv(vols, 21) > 1e6


# ───────────────────── 4. thieu du lieu = None ─────────────────────
def test_summarize_thieu_du_lieu_tra_none():
    tr = [{"setup": "BO", "r1": 0.01, "r5": 0.10, "r10": 0.20, "r20": None,
           "mae": -0.03, "mfe": 0.25, "base_len": 30, "rs_pct": 80.0},
          {"setup": "BO", "r1": -0.02, "r5": -0.05, "r10": -0.10, "r20": None,
           "mae": -0.12, "mfe": 0.02, "base_len": 60, "rs_pct": 40.0}]
    s = bt.summarize(tr)
    assert s["n"] == 2 and s["n20"] == 0
    assert s["win20"] is None and s["med20"] is None, "0% la sai, phai la None"
    assert s["win10"] == 0.5 and abs(s["med5"] - 0.025) < 1e-12
    assert s["mae"] == -0.075 and s["mfe"] == 0.135


def test_lenh_gan_cuoi_chuoi_co_cot_none():
    bs = _bo_series()
    j = next(k for k, x in enumerate(bs) if x.v == 4e6)
    tr = bt.walk({"AAA": bs[:j + 4]})
    assert len(tr) == 1
    assert tr[0]["r1"] is not None and tr[0]["r20"] is None


# ───────────────────── cooldown ─────────────────────
def test_cooldown_gop_cac_phien_lien_tiep():
    bs = _rv_series()
    assert len(bt.walk({"DIP": bs}, fund=FUND)) == 1
    assert len(bt.walk({"DIP": bs}, fund=FUND, cooldown=1)) == 3


def test_bo_khong_vao_lai_sau_khi_da_vuot_pivot():
    """Khong phai nho cooldown: dist_pivot cua setups.py da chan."""
    tr = bt.walk({"AAA": _bo_series()}, cooldown=1)
    assert len([t for t in tr if t["setup"] == "BO"]) == 1


# ───────────────────── rs_pct + moc ngau nhien ─────────────────────
def test_rs_pct_xep_hang_theo_tung_ngay():
    up = bt._mk([(10.0, 30.0, 130)])
    dn = bt._mk([(30.0, 10.0, 130)])
    rs = bt.rs_by_day({"UP": up, "DN": dn})
    d = up[-1].d
    assert rs[d]["UP"] == 100.0 and rs[d]["DN"] == 0.0
    assert up[10].d not in rs, "chua du 63 phien thi chua co ret63"


def test_moc_ngau_nhien_co_so():
    base = bt.random_baseline({"AAA": _bo_series()})
    assert base["r10"] is not None
    # chuoi chi tang -> vao mu cung lai, va do la ca van de
    assert base["r10"] > 0


def test_khoang_thoi_gian():
    bs = _bo_series()
    t = bt.walk({"AAA": bs})[0]
    assert bt.walk({"AAA": bs}, start="2099-01-01") == []
    assert bt.walk({"AAA": bs}, end="2000-01-01") == []
    assert bt.walk({"AAA": bs}, start=t["d_entry"], end=t["d_entry"])


# ───────────────────── cat theo bien ─────────────────────
def test_by_bucket():
    tr = [{"setup": "BO", "r1": 0.0, "r5": 0.1, "r10": 0.1, "r20": 0.1,
           "mae": -0.01, "mfe": 0.2, "base_len": 25, "base_depth": 0.05},
          {"setup": "BO", "r1": 0.0, "r5": -0.1, "r10": -0.1, "r20": -0.1,
           "mae": -0.2, "mfe": 0.01, "base_len": 70, "base_depth": 0.18}]
    d = dict(bt.by_bucket(tr, "base_len"))
    assert d["20-35"]["n"] == 1 and d["50+"]["n"] == 1
    assert d["20-35"]["win10"] == 1.0 and d["50+"]["win10"] == 0.0
    d2 = dict(bt.by_bucket(tr, "base_depth"))
    assert set(d2) == {"0-0.08", "0.14-0.2"}
    # bien khong co trong BUCKETS -> cat theo gia tri roi rac
    d3 = dict(bt.by_bucket(tr, "setup"))
    assert d3["BO"]["n"] == 2


def test_bucket_bo_qua_gia_tri_none():
    tr = [{"setup": "BO", "r1": 0.0, "r5": 0.0, "r10": 0.0, "r20": 0.0,
           "mae": 0.0, "mfe": 0.0, "base_len": 25, "rs_pct": None}]
    assert bt.by_bucket(tr, "rs_pct") == []


def test_bao_cao_khong_nem_loi_khi_rong():
    bt.report([], {"AAA": _bo_series()})
    bt.report(bt.walk({"AAA": _bo_series()}), {"AAA": _bo_series()},
              by="base_len")


if __name__ == "__main__":
    _util.main(globals())
