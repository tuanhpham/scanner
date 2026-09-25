"""plan.py - ke hoach lenh (vao / stop / co vi the).

Nam thu phai dung, xep theo muc do "sai thi khong ai phat hien ra":

1. size_pct DA nhan co vi the cua playbook. Neu khong, mot ngay
   UPTREND_UNDER_STRESS (size 0) van ra ke hoach vao lenh het co, va tin nhan
   buoi sang se noi hai dieu trai nguoc cung luc.
2. risk_pct phai la rui ro THUC SU chiu, khong phai ban copy cua config. Khi vi
   the bi tran max_pos_pct chan thi rui ro thuc su NHO HON ngan sach - bao cao
   0.75% luc do la bao cao sai.
3. Thieu ATR -> None, khong doan. Mot stop "gan dung" trong phien khong con
   cach nao phat hien ra.
4. Ke hoach chi duoc tinh tu NEN QUYET DINH. Khong co gia trong phien nao
   duoc di vao day - day la loi lookahead, loai loi dat nhat.
5. size_mult chi doi CO, khong doi STOP. Stop la mot muc gia ky thuat; giam co
   vi the khong lam cai muc do dich di.
"""
from __future__ import annotations

import _util

pl, cf = _util.need("plan", "config")

G = cf.PLAN


def row(**kw) -> dict:
    """Mot dong `struct` du de lap ke hoach. ATR 3 tren gia 100 = 3%."""
    return {"sym": "TEST", "px": 100.0, "hi1": 101.0, "lo1": 99.0,
            "pivot": 99.0, "atr14": 3.0, **kw}


# ───────────────────────── diem vao ─────────────────────────
def test_trigger_lay_moc_cao_hon_trong_pivot_va_dinh_nen():
    # Gia da chay len tren pivot: mua o pivot la mua o gia khong con ton tai.
    assert abs(pl.trigger(row(hi1=101.0, pivot=99.0)) - 101.0 * 1.001) < 1e-9
    # Con duoi pivot: pivot la moc.
    assert abs(pl.trigger(row(hi1=98.0, pivot=105.0)) - 105.0 * 1.001) < 1e-9


def test_khong_co_pivot_van_lap_duoc_ke_hoach():
    # Mot ma LEAD di qua BO SAN chat luong, khong qua mot hinh mau - nen no
    # khong bat buoc co nen tich luy nao. "Vuot dinh hom qua" la moc hop le.
    p = pl.make(row(pivot=None))
    assert p is not None
    assert abs(p["trigger"] - 101.0 * 1.001) < 1e-9


def test_trigger_phai_vuot_han_chu_khong_cham_vao():
    assert pl.trigger(row()) > 101.0, "cham dung dinh cu chua phai la vuot"


# ───────────────────────── stop ─────────────────────────
def test_stop_tinh_tu_atr_chu_khong_tu_phan_tram_co_dinh():
    a = pl.make(row(px=100.0, hi1=100.0, pivot=None, atr14=2.0))
    b = pl.make(row(px=100.0, hi1=100.0, pivot=None, atr14=6.0))
    # Cung gia, ATR gap 3 lan -> khoang cach stop gap 3 lan. Mot nguong % co
    # dinh se cat lien tuc o ma ATR cao va khong bao gio duoc dung o ma ATR
    # thap.
    assert abs(b["stop_pct"] / a["stop_pct"] - 3.0) < 1e-6


def test_stop_khong_the_am_hoac_bang_khong():
    # ATR lon hon gia / stop_atr: khong co ke hoach nao hop ly, tra None.
    assert pl.make(row(px=2.0, hi1=2.0, pivot=None, atr14=2.0)) is None


# ───────────────────────── co vi the ─────────────────────────
# make() lam tron 6 chu so thap phan, nen moi so sanh voi mot gia tri tinh lai
# phai chap nhan sai so o muc do. 2e-6 tren mot ty le = 0.0002%.
EPS = 2e-6

# Tran max_pos_pct chan khi stop_pct < risk_pct / max_pos_pct. Voi bo nguong
# mac dinh: 0.0075 / 0.20 = 3.75%. Tuc la o gia ~101, moi ma co ATR duoi ~2.5
# deu bi tran chan. Hai test duoi dung ATR 3.0 va 5.0 de o han duoi tran.
CAP_TAI = G["risk_pct"] / G["max_pos_pct"]


def test_co_vi_the_suy_ra_tu_ngan_sach_rui_ro():
    p = pl.make(row())
    assert p["stop_pct"] > CAP_TAI, "row() phai o duoi tran de test dung y nghia"
    assert abs(p["size_pct"] - G["risk_pct"] / p["stop_pct"]) < EPS
    # Va rui ro thuc su bang dung ngan sach khi chua bi tran chan.
    assert abs(p["risk_pct"] - G["risk_pct"]) < EPS


def test_stop_rong_hon_thi_vi_the_nho_hon():
    chat = pl.make(row(atr14=3.0))
    rong = pl.make(row(atr14=5.0))
    assert chat["stop_pct"] > CAP_TAI and rong["stop_pct"] > CAP_TAI
    assert rong["size_pct"] < chat["size_pct"]
    # Nhung rui ro chiu thi BANG NHAU - do la ca y nghia cua cach tinh nay.
    assert abs(rong["risk_pct"] - chat["risk_pct"]) < EPS


def test_tran_max_pos_pct_chan_va_rui_ro_bao_lai_cho_dung():
    # Stop rat chat -> cong thuc ra 50% von -> bi tran 20% chan.
    p = pl.make(row(px=100.0, hi1=100.0, pivot=None, atr14=0.5))
    assert abs(p["size_pct"] - G["max_pos_pct"]) < EPS
    # Va luc do rui ro thuc su phai NHO HON ngan sach. Bao 0.75% la bao sai.
    assert p["risk_pct"] < G["risk_pct"]
    assert abs(p["risk_pct"] - p["stop_pct"] * G["max_pos_pct"]) < EPS


def test_ma_bien_do_thap_nhat_cua_LEAD_bi_tran_chan():
    # LEAD nhan ATR tu 2% den 6%. Dau duoi cua khoang do (ATR 2%) NAM TRONG
    # vung bi tran chan, nen phan lon danh sach se chay o dung 20% von voi rui
    # ro DUOI ngan sach. Day khong phai loi - nhung no la mot tinh chat can
    # duoc ghi lai, vi neu mot ngay nao doi max_pos_pct thi ca danh sach doi co
    # cung mot luc chu khong phai vai ma.
    atr_thap = 100.0 * cf.LEAD["min_atr_pct"]        # ATR 2% tren gia 100
    p = pl.make(row(px=100.0, hi1=100.0, pivot=None, atr14=atr_thap))
    assert p["stop_pct"] < CAP_TAI
    assert abs(p["size_pct"] - G["max_pos_pct"]) < EPS
    assert p["risk_pct"] < G["risk_pct"]


def test_size_mult_cua_playbook_nhan_vao_co_chu_khong_vao_stop():
    full = pl.make(row(), size_mult=1.0)
    half = pl.make(row(), size_mult=0.5)
    assert abs(half["size_pct"] - full["size_pct"] / 2) < 1e-12
    assert half["stop"] == full["stop"], "giam co khong duoc lam stop dich di"
    assert half["trigger"] == full["trigger"]
    assert abs(half["risk_pct"] - full["risk_pct"] / 2) < 1e-12


def test_size_0_van_ra_ke_hoach_day_du():
    # Cong tac "hom nay khong mo vi the moi" phai la mot con so doc duoc, chu
    # khong phai mot truong bi thieu. Va ke hoach van phai co: canh stop cho vi
    # the dang mo can dung nhung muc gia nay.
    z = pl.make(row(), size_mult=0.0)
    assert z is not None
    assert z["size_pct"] == 0.0
    assert z["trigger"] > 0 and z["stop"] > 0
    assert "KHÔNG mở vị thế mới" in pl.fmt(z)


# ───────────────────────── khong duoc doan ─────────────────────────
def test_thieu_so_lieu_tra_none_chu_khong_doan():
    assert pl.make(row(atr14=None)) is None
    assert pl.make(row(atr14=0.0)) is None
    assert pl.make(row(atr14="x")) is None
    assert pl.make(row(hi1=None, pivot=None)) is None
    assert pl.make({}) is None


def test_nan_va_inf_khong_di_qua_duoc():
    for bad in (float("nan"), float("inf"), float("-inf")):
        assert pl.make(row(atr14=bad)) is None, f"atr14={bad}"
        assert pl.make(row(hi1=bad, pivot=None)) is None, f"hi1={bad}"


def test_khong_dung_gia_trong_phien():
    # `px` (gia dong cua nen quyet dinh) khong tham gia vao trigger/stop: chung
    # chi dung hi1/pivot/atr14. Doi px ma ke hoach khong doi la bang chung cau
    # truc rang khong co gia nao khac di vao duoc.
    a = pl.make(row(px=100.0))
    b = pl.make(row(px=999.0))
    assert a == b


# ───────────────────────── tin nhan ─────────────────────────
def test_fmt_co_dau_va_khong_vo_khi_thieu():
    p = pl.make(row())
    s = pl.fmt(p)
    for want in ("vào", "stop", "mục tiêu", "cỡ", "rủi ro"):
        assert want in s, f"thieu '{want}' trong: {s}"
    assert "thiếu ATR" in pl.fmt(None)
    assert pl.fmt({}) == pl.fmt(None), "dict rong cung la 'chua lap duoc'"


if __name__ == "__main__":
    _util.main(globals())
