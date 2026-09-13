"""setups.py - hai setup theo nen ngay.

Bon thu phai dung, xep theo muc do "sai thi khong ai phat hien ra":

1. RV KHONG BAO GIO kich hoat khi thieu so lieu co ban. "Chua biet" bi doi
   thanh "tot" la cach nhanh nhat de bot alert dung nhung ma dang pha loang.
2. build() phai GHI DE ca bang. Ma khong con nen ma con nam lai trong
   `candidates` thi bot canh mot pivot da vo tu lau.
3. `candidates` qua han phai tra ve rong. Cron chet thi khong co gi bao loi -
   chi co pivot cu dung yen, va alert van gui nhu that.
4. Nguong nam trong dict BO/RV, khong rai trong ham: backtest.py doi mot cho
   thi ca hai buoc (sinh danh sach + kich hoat) phai doi theo.
"""
from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path

import _util

se, st = _util.need("setups", "structure")


def base_row(**kw) -> dict:
    return se._base_row(**kw)


def dip_row(**kw) -> dict:
    return se._dip_row(**kw)


BO_Q = {"px": 21.0, "vol": 2_000_000, "rvol": 2.5, "hi": 21.1, "lo": 20.2,
        "open": 20.3}
RV_Q = {"px": 6.7, "vol": 5_000_000, "rvol": 4.0, "hi": 6.75, "lo": 6.05,
        "open": 6.1}


def _db(rows: dict[str, dict]) -> Path:
    """DB tam co bang `struct` dung san."""
    db = Path(tempfile.mkdtemp()) / "t.db"
    c = st.con(db)
    now = "2024-06-03T20:00:00+00:00"
    ins = (f"INSERT INTO struct(sym,{','.join(st.COLS)},updated) "
           f"VALUES({','.join('?' * (len(st.COLS) + 2))})")
    for sym, r in rows.items():
        c.execute(ins, (sym, *(r[k] for k in st.COLS), now))
    c.commit()
    c.close()
    return db


# ───────────────────── 1. co ban thieu = loai, khong phai "tot" ─────────────
def test_rv_khong_kich_hoat_khi_thieu_co_ban():
    c = se.rv_candidate(dip_row())
    assert c and c["fund_ok"] is None, "chua co Phase 9.3 -> phai la None"
    assert se.trig_rv(c, RV_Q) is None, "tang 11% volume 4x van khong duoc alert"
    # co so lieu va dat thi moi kich hoat
    ok = se.rv_candidate(dip_row(), fund={"ok": True, "score": 0.7})
    assert se.trig_rv(ok, RV_Q)


def test_rv_co_ban_khong_dat_thi_khong_vao_danh_sach():
    assert se.rv_candidate(dip_row(), fund={"ok": False}) is None


def test_rv_diem_co_ban_chiem_phan_lon_xep_hang():
    a = se.rv_candidate(dip_row(), fund={"ok": True, "score": 0.9})
    b = se.rv_candidate(dip_row(), fund={"ok": True, "score": 0.1})
    assert a["quality"] > b["quality"] + 0.2


# ───────────────────── 2. + 3. bang candidates ─────────────────────
def test_build_ghi_de_ma_het_nen_thi_bien_mat():
    db = _db({"AAA": base_row(), "DIP": dip_row()})
    r = se.build(db)
    assert r["BO"] == 1 and r["RV"] == 1
    assert set(se.load_candidates(db, today="2024-06-04")) == {"AAA", "DIP"}

    c = st.con(db)
    c.execute("UPDATE struct SET base_len=0, pivot=NULL, dist_pivot=NULL "
              "WHERE sym='AAA'")
    c.commit()
    se.build(c)
    got = se.load_candidates(c, today="2024-06-04")
    assert "AAA" not in got, "pivot cu con nam lai trong danh sach theo doi"
    assert set(got) == {"DIP"}
    n = c.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    assert n == 1, "build() phai ghi de ca bang, khong duoc chen them"
    c.close()


def test_candidates_qua_han_tra_ve_rong():
    db = _db({"AAA": base_row()})       # struct cua ngay 2024-06-03
    se.build(db)
    assert se.load_candidates(db, today="2024-06-05")
    assert se.load_candidates(db, today="2024-07-01") == {}, \
        "cron chet 4 tuan ma bot van canh pivot cu"
    assert se.load_candidates(db, today="2024-07-01", max_age=None)


def test_load_candidates_loc_theo_setup():
    db = _db({"AAA": base_row(), "DIP": dip_row()})
    se.build(db)
    assert set(se.load_candidates(db, "BO", today="2024-06-04")) == {"AAA"}
    assert set(se.load_candidates(db, "RV", today="2024-06-04")) == {"DIP"}


def test_cols_khop_ddl():
    c = se.con(Path(tempfile.mkdtemp()) / "t.db")
    have = {r[1] for r in c.execute("PRAGMA table_info(candidates)")}
    assert set(se.COLS) | {"sym", "updated"} == have
    cand = se.bo_candidate(base_row())
    assert set(se.COLS) - set(cand) == set(), set(se.COLS) - set(cand)
    c.close()


def test_tran_max_cand():
    rows = {f"S{i:03d}": base_row(base_depth=0.05 + i * 0.001)
            for i in range(30)}
    _, rej = se.scan(rows, max_cand=10)
    assert rej["BO"]["_qua_loc"] == 30 and rej["BO"]["_bi_cat_tran"] == 20
    kept, _ = se.scan(rows, max_cand=10)
    assert len(kept) == 10
    # bi cat la nhung ma diem thap nhat, khong phai theo thu tu alphabet
    q = [k["quality"] for k in kept]
    assert q == sorted(q, reverse=True)


# ───────────────────── 4. nguong tap trung mot cho ─────────────────
def test_doi_nguong_trong_dict_thi_ca_hai_buoc_doi_theo():
    g = {**se.BO, "min_base_len": 60}
    assert se.bo_candidate(base_row(), g) is None
    assert se.bo_candidate(base_row())          # dict goc khong bi thay doi
    g2 = {**se.BO, "rvol": 5.0}
    c = se.bo_candidate(base_row())
    assert se.trig_bo(c, BO_Q) and se.trig_bo(c, BO_Q, g2) is None


# ───────────────────── BO: sinh danh sach ─────────────────────
def test_bo_can_nen_that():
    assert se.bo_candidate(base_row())
    assert se.bo_candidate(base_row(base_len=15)) is None
    assert se.bo_candidate(base_row(base_depth=0.28)) is None
    assert se.bo_candidate(base_row(atr_contract=0.95)) is None
    assert se.bo_candidate(dip_row()) is None


def test_bo_nen_rong_hon_cho_ma_gia_thap():
    """Nen 28% o ma $20 la lung nhung o ma $8 la binh thuong."""
    assert se.bo_candidate(base_row(base_depth=0.28)) is None
    assert se.bo_candidate(base_row(px=8.0, sma20=7.9, sma50=7.6, hi52=8.3,
                                    pivot=8.2, base_depth=0.28))


def test_bo_phai_o_gan_dinh_va_tren_sma50():
    assert se.bo_candidate(base_row(off_high=0.40)) is None
    assert se.bo_candidate(base_row(px=18.0, sma50=19.0)) is None
    assert se.bo_candidate(base_row(sma50_slope=-0.001)) is None


def test_bo_chi_theo_doi_phan_tren_cua_nen():
    assert se.bo_candidate(base_row(dist_pivot=0.30)) is None, "hom nay khong the vuot"
    assert se.bo_candidate(base_row(dist_pivot=-0.10)) is None, "da vao muon"
    assert se.bo_candidate(base_row(dist_pivot=-0.01)), "vua vuot: van theo doi"


# ───────────────────── BO: kich hoat ─────────────────────
def test_bo_cham_pivot_chua_phai_vuot():
    c = se.bo_candidate(base_row())          # pivot 20.6
    assert se.trig_bo(c, {**BO_Q, "px": 20.55}) is None
    assert se.trig_bo(c, {**BO_Q, "px": 20.75})


def test_bo_khong_alert_khi_da_chay_qua_xa():
    """+19% tren pivot la gap-and-go: viec cua SPIKE, khong phai BO."""
    c = se.bo_candidate(base_row())
    assert se.trig_bo(c, {**BO_Q, "px": 24.5, "hi": 24.6, "lo": 20.5}) is None


def test_bo_can_rvol_va_thanh_khoan():
    c = se.bo_candidate(base_row())
    assert se.trig_bo(c, {**BO_Q, "rvol": 1.2}) is None
    assert se.trig_bo(c, {**BO_Q, "vol": 1000}) is None


def test_bo_khong_alert_khi_dang_o_nua_duoi_bien_do():
    c = se.bo_candidate(base_row())
    assert se.trig_bo(c, {**BO_Q, "px": 20.75, "hi": 22.5, "lo": 20.5}) is None


def test_bo_gap_to_thi_canh_bao_chu_khong_loai():
    c = se.bo_candidate(base_row())
    t = se.trig_bo(c, {**BO_Q, "open": 22.5})
    assert t and t["warn"], "gap 12%: van la breakout, nhung entry xau"
    assert not se.trig_bo(c, BO_Q)["warn"]


# ───────────────────── RV ─────────────────────
def test_rv_can_roi_sau_va_day_con_moi():
    assert se.rv_candidate(dip_row())
    assert se.rv_candidate(base_row()) is None
    assert se.rv_candidate(dip_row(off_high=0.30)) is None
    assert se.rv_candidate(dip_row(days_since_low=60)) is None
    assert se.rv_candidate(dip_row(ret63=0.05)) is None


def test_rv_khong_vao_muon():
    assert se.rv_candidate(dip_row(up_from_low=0.60)) is None


def test_rv_loc_gia_va_thanh_khoan_chat_hon_bo():
    """RV siet hon BO: roi 70% ma thanh khoan mong thi khong ban ra duoc."""
    assert se.rv_candidate(dip_row(px=2.0)) is None
    assert se.rv_candidate(dip_row(adv20=300_000.0)) is None
    assert se.bo_candidate(base_row(adv20=300_000.0)), "BO thi 300k van du"


def test_rv_phai_lay_lai_sma20():
    c = se.rv_candidate(dip_row(sma20=7.5), fund={"ok": True})
    assert se.trig_rv(c, RV_Q) is None
    c2 = se.rv_candidate(dip_row(sma20=6.4), fund={"ok": True})
    assert se.trig_rv(c2, RV_Q)


def test_rv_phai_dong_o_vung_dinh_ngay():
    c = se.rv_candidate(dip_row(), fund={"ok": True})
    assert se.trig_rv(c, {**RV_Q, "px": 6.5, "hi": 7.0, "lo": 6.4}) is None
    assert se.trig_rv(c, {**RV_Q, "rvol": 2.0}) is None


# ───────────────────── ly do bi loai ─────────────────────
def test_dem_ly_do_bi_loai():
    rows, rej = se.scan({"AAA": base_row(), "DIP": dip_row()})
    assert {r["sym"] for r in rows} == {"AAA", "DIP"}
    assert rej["BO"]["khong co nen tich luy"] == 1
    assert rej["RV"]["chua roi du 50%"] == 1
    assert rej["_cho_fund"] == 1


def test_check_goi_dung_ham_theo_setup():
    bo = se.bo_candidate(base_row())
    rv = se.rv_candidate(dip_row(), fund={"ok": True})
    assert se.check(bo, BO_Q)["setup"] == "BO"
    assert se.check(rv, RV_Q)["setup"] == "RV"
    assert se.check({"setup": "XX"}, BO_Q) is None


def test_thieu_du_lieu_khong_nem_loi():
    assert se.bo_candidate({}) is None
    assert se.rv_candidate({}) is None
    c = se.bo_candidate(base_row())
    assert se.trig_bo(c, {}) is None
    assert se.trig_rv(se.rv_candidate(dip_row(), fund={"ok": True}), {}) is None
    # thieu hi/lo (quote khong co bien do ngay) -> khong duoc loai oan BO
    assert se.trig_bo(c, {"px": 21.0, "vol": 2_000_000, "rvol": 2.5})


if __name__ == "__main__":
    _util.main(globals())
