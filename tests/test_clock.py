"""clock.py — moc thoi gian sai thi bot quet sai gio, va RVOL sai theo.

Hai thu de sai:

  1. Bien trang thai (PREP/PREMARKET/OPENING/LIVE/CLOSING/AFTERHOURS/CLOSED).
     Lech mot phut o day thi vong quet chay som/muon ca phien.
  2. Nua phien va DST. Nua phien 210 phut ma van chia theo 390 -> RVOL bi dim
     gan 2 lan, ca buoi khong ma nao vuot nguong.

Cach viet: KHONG hardcode "15:30 gio Duc". Gio mo cua lay tu chinh clock roi
kiem tra may bien trang thai quanh no — test khong vo khi DST doi hay khi NYSE
doi gio phien, ma van bat duoc loi logic.
"""
from __future__ import annotations

import datetime as dt

import _util

c = _util.need("clock")

REG = dt.date(2026, 9, 4)          # thu Sau thuong (Labor Day la 07/09)
SAT = dt.date(2026, 9, 5)          # thu Bay
HALF = dt.date(2026, 11, 27)       # sau Le Ta on -> nua phien


def _et(day: dt.date, h: int, m: int = 0) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, h, m, tzinfo=c.ET)


def _ck() -> "c.SessionClock":
    return c.SessionClock()


def _at(ck, o: dt.datetime, mins: int) -> str:
    return ck.state(o + dt.timedelta(minutes=mins))


# ───────────────────────── ngay giao dich ─────────────────────────
def test_ngay_thuong_la_ngay_giao_dich():
    ck = _ck()
    assert ck.is_trading_day(_et(REG, 12))
    assert ck.session_minutes(_et(REG, 12)) == 390
    assert not ck.is_half(_et(REG, 12))


def test_cuoi_tuan_dong_cua():
    ck = _ck()
    n = _et(SAT, 12)
    assert not ck.is_trading_day(n)
    assert ck.state(n) == "CLOSED"
    assert ck.mso(n) is None, "None = khong phai ngay GD, khong duoc tra 0"
    assert ck.session_minutes(n) == 0
    assert ck.open_et(n) is None and ck.local_open(n) is None
    assert not ck.scanning(n)


def test_doi_ngay_thi_nap_lai_lich():
    """Bug de mac: cache lich cua hom qua, sang hom sau van dung gio cu."""
    ck = _ck()
    assert ck.is_trading_day(_et(REG, 12))
    assert not ck.is_trading_day(_et(SAT, 12))
    assert ck.is_trading_day(_et(REG, 12)), "phai nap lai duoc"


# ───────────────────────── bien trang thai ─────────────────────────
def test_chuoi_trang_thai_trong_ngay():
    ck = _ck()
    o = ck.open_et(_et(REG, 12))
    assert o is not None
    assert _at(ck, o, -1) == "PREMARKET"
    assert _at(ck, o, 0) == "OPENING"
    assert _at(ck, o, c.OPENING_MIN - 1) == "OPENING"
    assert _at(ck, o, c.OPENING_MIN) == "LIVE"
    cl = ck.close_et(_et(REG, 12))
    assert ck.state(cl - dt.timedelta(minutes=c.CLOSING_MIN + 1)) == "LIVE"
    assert ck.state(cl - dt.timedelta(minutes=c.CLOSING_MIN)) == "CLOSING"
    assert ck.state(cl - dt.timedelta(minutes=1)) == "CLOSING"
    assert ck.state(cl) == "AFTERHOURS"


def test_truoc_gio_mo_cua():
    ck = _ck()
    assert ck.state(_et(REG, 6, 59)) == "CLOSED"
    assert ck.state(_et(REG, 7, 0)) == "PREP"
    assert ck.state(_et(REG, 8, 59)) == "PREP"
    assert ck.state(_et(REG, 9, 0)) == "PREMARKET"


def test_sau_gio_dong_cua():
    ck = _ck()
    assert ck.state(_et(REG, 19, 59)) == "AFTERHOURS"
    assert ck.state(_et(REG, 20, 0)) == "CLOSED"
    assert ck.state(_et(REG, 23, 30)) == "CLOSED"


def test_chi_quet_trong_phien():
    ck = _ck()
    o = ck.open_et(_et(REG, 12))
    assert ck.scanning(o + dt.timedelta(minutes=60))
    assert not ck.scanning(o - dt.timedelta(minutes=1)), "premarket khong quet"
    assert not ck.scanning(_et(REG, 18))
    assert not ck.scanning(_et(SAT, 12))


def test_mso():
    ck = _ck()
    o = ck.open_et(_et(REG, 12))
    assert ck.mso(o) == 0
    assert ck.mso(o + dt.timedelta(minutes=90)) == 90
    assert ck.mso(o - dt.timedelta(minutes=30)) == -30, \
        "premarket phai am, khong duoc kep ve 0"
    # Lam tron xuong: 59 giay sau moc van la phut do.
    assert ck.mso(o + dt.timedelta(minutes=5, seconds=59)) == 5


# ───────────────────────── nua phien ─────────────────────────
def test_nua_phien():
    ck = _ck()
    n = _et(HALF, 12)
    if not ck.is_trading_day(n):                  # lich cua thu vien doi
        _util._skip("27/11/2026 khong phai ngay giao dich theo lich hien tai")
    assert ck.is_half(n), "sau Le Ta on la nua phien"
    sm = ck.session_minutes(n)
    assert 180 <= sm <= 240, sm
    assert sm < 390


def test_nua_phien_dong_som_thi_trang_thai_theo_do():
    """CLOSING/AFTERHOURS phai theo gio dong THAT, khong phai 16:00 cung."""
    ck = _ck()
    n = _et(HALF, 12)
    if not ck.is_trading_day(n):
        _util._skip("khong phai ngay giao dich")
    cl = ck.close_et(n)
    assert ck.state(cl + dt.timedelta(minutes=1)) == "AFTERHOURS"
    assert ck.state(cl - dt.timedelta(minutes=1)) == "CLOSING"


# ───────────────────────── DST ─────────────────────────
def test_dst_skew():
    """My doi gio 08/03, EU doi 29/03 -> giua hai moc lech 5 tieng, khong 6."""
    ck = _ck()
    assert ck.dst_skew(_et(dt.date(2026, 6, 10), 12)) == 6
    assert ck.dst_skew(_et(dt.date(2026, 3, 10), 12)) == 5
    assert ck.dst_skew(_et(dt.date(2026, 1, 20), 12)) == 6


def test_gio_duc_lech_dung_skew():
    ck = _ck()
    n = _et(REG, 12)
    o, lo = ck.open_et(n), ck.local_open(n)
    assert (lo.hour - o.hour) % 24 == ck.dst_skew(n)
    assert lo.minute == o.minute


# ───────────────────────── describe ─────────────────────────
def test_describe_khong_nem_loi():
    ck = _ck()
    for n in (_et(REG, 12), _et(SAT, 12), _et(REG, 3), _et(HALF, 12)):
        s = ck.describe(n)
        assert isinstance(s, str) and s
    assert "khong phai ngay giao dich" in ck.describe(_et(SAT, 12))


if __name__ == "__main__":
    _util.main(globals())
