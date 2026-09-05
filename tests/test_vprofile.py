"""vprofile.py — duong cong khoi luong. Day la mau so cua RVOL, tuc la mau so
cua toan bo thang diem.

Sai o day khong bao gio thay ro: bot van chay, chi la 9h35 khong ma nao vuot
nguong (mau so qua lon) hoac 15h ma nao cung vuot (mau so qua nho).
"""
from __future__ import annotations

import _util

v = _util.need("vprofile")


# ───────────────────────── cum_frac ─────────────────────────
def test_truoc_gio_mo_cua_dung_san():
    """mso <= 0 hoac None -> FLOOR, khong duoc tra 0 (se chia cho 0)."""
    assert v.cum_frac(None) == v.FLOOR
    assert v.cum_frac(0) == v.FLOOR
    assert v.cum_frac(-30) == v.FLOOR
    assert v.FLOOR > 0


def test_het_phien_la_mot():
    assert v.cum_frac(v.FULL_SESSION) == 1.0
    assert v.cum_frac(v.FULL_SESSION + 120) == 1.0, "AH khong lam frac > 1"


def test_tang_dan_khong_giam():
    """Khoi luong tich luy khong the giam — mot diem gay nguoc la RVOL nhay vot."""
    prev = 0.0
    for m in range(0, v.FULL_SESSION + 1, 5):
        f = v.cum_frac(m)
        assert f >= prev - 1e-12, f"giam tai mso={m}: {prev} -> {f}"
        assert 0 < f <= 1.0, (m, f)
        prev = f


def test_dau_phien_it_hon_cuoi_phien():
    assert v.cum_frac(15) < v.cum_frac(195) < v.cum_frac(380)
    # 30 phut dau chiem duoi 20% ca ngay, nua phien dau duoi 60%.
    assert v.cum_frac(30) < 0.20
    assert v.cum_frac(195) < 0.60


def test_nua_phien_co_gian_theo_ty_le():
    """Nua phien 210 phut: het phien phai la 1.0, khong phai 0.54."""
    assert v.cum_frac(210, 210) == 1.0
    # Giua nua phien ~ giua ca phien.
    assert abs(v.cum_frac(105, 210) - v.cum_frac(195, 390)) < 0.02
    assert v.cum_frac(105, 210) > v.cum_frac(105, 390)


def test_session_minutes_khong_hop_le_khong_chia_cho_khong():
    for smin in (0, -1):
        assert 0 < v.cum_frac(100, smin) <= 1.0


# ───────────────────────── rvol ─────────────────────────
def test_rvol_co_ban():
    assert v.rvol(5_000_000, 1_000_000, v.FULL_SESSION) == 5.0
    assert v.rvol(1_000_000, 1_000_000, v.FULL_SESSION) == 1.0


def test_rvol_thieu_adv20_tra_ve_khong():
    """Khong co adv20 -> 0.0 chu khong duoc doan. scorer se bo qua thanh phan."""
    for adv in (0, None, -1):
        assert v.rvol(5_000_000, adv, 100) == 0.0
        assert v.rvol_at(5_000_000, adv, 0.5) == 0.0


def test_rvol_chuan_hoa_theo_thoi_diem():
    """Cung 1 trieu co phieu: luc 9h35 la bat thuong, luc 16h la binh thuong."""
    som = v.rvol(1_000_000, 1_000_000, 5)
    muon = v.rvol(1_000_000, 1_000_000, v.FULL_SESSION)
    assert som > muon and muon == 1.0


def test_rvol_at_ket_san_frac():
    """frac = 0 (moi mo cua) khong duoc ZeroDivisionError."""
    assert v.rvol_at(1_000, 1_000_000, 0.0) == v.rvol_at(1_000, 1_000_000,
                                                         v.FLOOR)
    assert v.rvol_at(1_000, 1_000_000, -5) == v.rvol_at(1_000, 1_000_000,
                                                        v.FLOOR)


def test_rvol_va_rvol_at_khop_nhau():
    """scorer dung rvol_at, CLI dung rvol — hai duong phai cho cung so."""
    for mso in (5, 60, 195, 380, 390):
        f = v.cum_frac(mso)
        assert abs(v.rvol(3_000_000, 500_000, mso)
                   - v.rvol_at(3_000_000, 500_000, f)) < 1e-9, mso


# ───────────────────────── session_frac ─────────────────────────
def test_session_frac_theo_trang_thai():
    for st in ("OPENING", "LIVE", "CLOSING"):
        assert v.session_frac(st, 60) == v.cum_frac(60)
    for st in ("PREP", "PREMARKET"):
        assert v.session_frac(st, None) == v.PREMKT_FRAC
    # Dong cua/AH: volume nhan duoc la CA PHIEN da xong -> khong duoc phong dai.
    for st in ("CLOSED", "AFTERHOURS"):
        assert v.session_frac(st, 500) == 1.0
    assert v.session_frac("TRANG THAI LA", 60) == 1.0, "mac dinh phai la 1.0"


def test_premarket_khong_phong_dai_rvol():
    """Premarket 3%: 300k co phieu tren adv20 1M la 10x, khong phai 83x."""
    assert 0 < v.PREMKT_FRAC < 0.1
    rv = v.rvol_at(300_000, 1_000_000, v.session_frac("PREMARKET", -30))
    assert 5 < rv < 20, rv


# ───────────────────────── bang tra ─────────────────────────
def test_bang_hop_le():
    assert len(v._MIN) == len(v._FRAC)
    assert list(v._MIN) == sorted(v._MIN)
    assert list(v._FRAC) == sorted(v._FRAC)
    assert v._MIN[0] == 0 and v._MIN[-1] == v.FULL_SESSION
    assert v._FRAC[-1] == 1.0


if __name__ == "__main__":
    _util.main(globals())
