"""outcome.py — bang do chat luong alert.

Cho nay sai thi khong ai biet: bao cao van in ra bang, chi la so sai. Hai thu
de sai nhat, va ca hai deu lam bao cao ĐEP HON THUC TE:

  1. Dien px15/px60 sai cua so thoi gian -> do gia o thoi diem khac
  2. Dien gia hom nay vao dong con mo cua hom qua

Khong test backfill(): no goi yfinance. Phan logic thuan (chon nen theo moc
thoi gian) duoc test rieng qua _pick().
"""
from __future__ import annotations

import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

import _util

o = _util.need("outcome")

T0 = 1_757_000_000              # moc alert co dinh
H = {"sym": "AAA", "px": 10.0, "score": 8.4, "rvol": 40.0}


def _db(td: str) -> Path:
    return Path(td) / "t.db"


def _rows(db) -> dict[str, tuple]:
    with closing(sqlite3.connect(str(db))) as c:
        return {r[0]: r for r in c.execute(
            "SELECT sym,px0,px15,px60,px_close,hi_after,lo_after,src,score,"
            "level,day FROM outcome")}


# ───────────────────────── record ─────────────────────────
def test_record_ghi_dung():
    with tempfile.TemporaryDirectory() as td:
        db = _db(td)
        assert o.record(db, H, 2, ts=T0)
        r = _rows(db)["AAA"]
        assert r[1] == 10.0 and r[7] == "live" and r[8] == 8.4 and r[9] == 2
        # hi/lo mo bang px0 -> max()/min() sau nay khong phai xu ly NULL
        assert r[5] == 10.0 and r[6] == 10.0
        assert r[2] is None and r[3] is None and r[4] is None


def test_record_bo_du_lieu_rac():
    with tempfile.TemporaryDirectory() as td:
        db = _db(td)
        o._con(db).close()                 # tao bang truoc: record() bo som
        assert not o.record(db, {"sym": "X", "px": 0}, 1)
        assert not o.record(db, {"sym": "X", "px": None}, 1)
        assert not o.record(db, {"px": 10.0}, 1)          # thieu sym
        assert _rows(db) == {}


def test_record_khong_ghi_trung():
    with tempfile.TemporaryDirectory() as td:
        db = _db(td)
        assert o.record(db, H, 2, ts=T0)
        o.record(db, {**H, "px": 99.0}, 3, ts=T0)
        r = _rows(db)["AAA"]
        assert r[1] == 10.0 and r[9] == 2, "phai giu dong dau"


# ───────────────────────── update ─────────────────────────
def test_dien_px15_dung_cua_so():
    """Ngoai cua so [15p, 15p+FILL_TOL] thi KHONG duoc dien."""
    for age, want in ((o.MIN15 - 60, None),            # chua den 15 phut
                      (o.MIN15 + 10, 11.0),           # dung cua so
                      (o.MIN15 + o.FILL_TOL + 60, None)):   # tre qua
        with tempfile.TemporaryDirectory() as td:
            db = _db(td)
            o.record(db, H, 2, ts=T0)
            o.update(db, {"AAA": {"px": 11.0}}, now=T0 + age)
            assert _rows(db)["AAA"][2] == want, (age, want)


def test_dien_px60():
    with tempfile.TemporaryDirectory() as td:
        db = _db(td)
        o.record(db, H, 2, ts=T0)
        o.update(db, {"AAA": {"px": 11.0}}, now=T0 + o.MIN15 + 10)
        o.update(db, {"AAA": {"px": 12.0}}, now=T0 + o.MIN60 + 10)
        r = _rows(db)["AAA"]
        assert r[2] == 11.0 and r[3] == 12.0


def test_px15_da_dien_khong_bi_ghi_de():
    with tempfile.TemporaryDirectory() as td:
        db = _db(td)
        o.record(db, H, 2, ts=T0)
        o.update(db, {"AAA": {"px": 11.0}}, now=T0 + o.MIN15 + 10)
        o.update(db, {"AAA": {"px": 20.0}}, now=T0 + o.MIN15 + 60)
        assert _rows(db)["AAA"][2] == 11.0


def test_hi_lo_noi_rong():
    with tempfile.TemporaryDirectory() as td:
        db = _db(td)
        o.record(db, H, 2, ts=T0)
        for i, px in enumerate((12.0, 8.0, 11.0, 9.5)):
            o.update(db, {"AAA": {"px": px}}, now=T0 + 60 * (i + 1))
        r = _rows(db)["AAA"]
        assert r[5] == 12.0 and r[6] == 8.0


def test_ma_roi_khoi_universe_de_null():
    """Khong duoc lay gia cu thay the — de NULL de `cov` phoi bay ra cho thieu."""
    with tempfile.TemporaryDirectory() as td:
        db = _db(td)
        o.record(db, H, 2, ts=T0)
        o.update(db, {"BBB": {"px": 5.0}}, now=T0 + o.MIN15 + 10)
        assert _rows(db)["AAA"][2] is None


def test_khong_lay_gia_hom_nay_dien_vao_dong_hom_qua():
    """Loi thiet ke de mac nhat: thieu bo loc `day` trong update()."""
    with tempfile.TemporaryDirectory() as td:
        db = _db(td)
        old = T0 - 3 * 86400
        o.record(db, H, 2, ts=old)                     # alert cua 3 ngay truoc
        o.update(db, {"AAA": {"px": 99.0}}, now=T0 + o.MIN15 + 10)
        r = _rows(db)["AAA"]
        assert r[2] is None and r[5] == 10.0, \
            "gia hom nay khong duoc dien vao dong cua ngay khac"


def test_update_universe_rong():
    with tempfile.TemporaryDirectory() as td:
        db = _db(td)
        o.record(db, H, 2, ts=T0)
        assert o.update(db, {}, now=T0 + o.MIN15 + 10) == 0
        assert o.update(db, None, now=T0 + o.MIN15 + 10) == 0


def test_universe_thieu_gia():
    with tempfile.TemporaryDirectory() as td:
        db = _db(td)
        o.record(db, H, 2, ts=T0)
        for u in ({"AAA": {}}, {"AAA": {"px": None}}, {"AAA": {"px": 0}},
                  {"AAA": None}):
            o.update(db, u, now=T0 + o.MIN15 + 10)
        assert _rows(db)["AAA"][2] is None


# ───────────────────────── freeze ─────────────────────────
def test_freeze_chi_dien_ma_con_trong_universe():
    with tempfile.TemporaryDirectory() as td:
        db = _db(td)
        o.record(db, H, 2, ts=T0)
        o.record(db, {**H, "sym": "BBB"}, 2, ts=T0)
        assert o.freeze(db, {"AAA": {"px": 10.5}}, day=o._day_et(T0)) == 1
        r = _rows(db)
        assert r["AAA"][4] == 10.5 and r["BBB"][4] is None


def test_freeze_khong_ghi_de():
    with tempfile.TemporaryDirectory() as td:
        db = _db(td)
        o.record(db, H, 2, ts=T0)
        d = o._day_et(T0)
        o.freeze(db, {"AAA": {"px": 10.5}}, day=d)
        o.freeze(db, {"AAA": {"px": 20.0}}, day=d)
        assert _rows(db)["AAA"][4] == 10.5


# ───────────────────────── report ─────────────────────────
def test_report_chia_nhom_dung():
    with tempfile.TemporaryDirectory() as td:
        db = _db(td)
        now = time.time()
        # 7.5 -> nhom 7.0-8.0 (thang, +10%), 8.5 -> nhom 8.0-9.0 (lo, -10%)
        for i, (sym, sc, px15) in enumerate((("AAA", 7.5, 11.0),
                                             ("BBB", 8.5, 9.0))):
            ts = now - 3600 + i
            o.record(db, {"sym": sym, "px": 10.0, "score": sc, "rvol": 5}, 1,
                     ts=ts)
            o.update(db, {sym: {"px": px15}}, now=ts + o.MIN15 + 10)
        rows = {r["bucket"]: r for r in o.report(db, days=3)}
        assert rows["7.0-8.0"]["win15"] == 1.0
        assert abs(rows["7.0-8.0"]["med15"] - 10.0) < 0.01
        assert rows["8.0-9.0"]["win15"] == 0.0
        assert all(r["cov"] == 1.0 and r["final"] == 0.0 for r in rows.values())


def test_report_db_rong():
    with tempfile.TemporaryDirectory() as td:
        assert o.report(_db(td), days=30) == []
        assert "Chua co du lieu" in o.fmt_report([])


def test_fmt_report_none_khong_thanh_0_phan_tram():
    """win60=None nghia la 'chua co so', in '0%' la noi doi."""
    out = o.fmt_report([{"bucket": "8.0-9.0", "n": 1, "cov": 1.0, "final": 0.0,
                         "win15": 1.0, "med15": 5.0, "win60": None,
                         "med60": None, "medcl": None, "medmfe": 5.0,
                         "medmae": 0.0}])
    assert "0%" not in out.split("\n")[-1].replace("100%", ""), out
    assert "-" in out.split("\n")[-1]


def test_med_va_pct():
    assert o._med([]) is None
    assert o._med([1.0]) == 1.0
    assert o._med([1.0, 3.0]) == 2.0
    assert o._med([3.0, 1.0, 2.0]) == 2.0
    assert abs(o._pct(11.0, 10.0) - 10.0) < 1e-9
    assert abs(o._pct(9.0, 10.0) + 10.0) < 1e-9
    assert o._pct(None, 10.0) is None and o._pct(11.0, 0) is None


# ───────────────────────── pending / last_open_day / purge ─────────────────
def test_pending_va_last_open_day():
    with tempfile.TemporaryDirectory() as td:
        db = _db(td)
        old = T0 - 5 * 86400
        o.record(db, H, 2, ts=old)
        assert o.pending(db, o._day_et(old)) == 1
        # last_open_day KHONG duoc tra ve hom nay (phien chua xong).
        o.record(db, {**H, "sym": "BBB"}, 2, ts=time.time())
        assert o.last_open_day(db) == o._day_et(old)


def test_purge():
    with tempfile.TemporaryDirectory() as td:
        db = _db(td)
        o.record(db, H, 2, ts=T0 - 500 * 86400)
        o.record(db, {**H, "sym": "BBB"}, 2, ts=time.time())
        assert o.purge(db, keep_days=400) == 1
        assert set(_rows(db)) == {"BBB"}


def test_db_khong_ghi_duoc_khong_lam_chet_alert():
    """Moi ham phai bat loi: alert quan trong hon so lieu do luong."""
    bad = Path(tempfile.gettempdir()) / "khong-co-thu-muc-nay" / "x" / "t.db"
    assert o.record(bad if bad.parent.exists() else "/:/x", H, 2) is False
    assert o.update("/:/x", {"AAA": {"px": 1.0}}) == 0
    assert o.freeze("/:/x", {"AAA": {"px": 1.0}}) == 0
    assert o.report("/:/x") == []
    assert o.pending("/:/x") == 0 and o.last_open_day("/:/x") is None
    assert o.purge("/:/x") == 0


def test_smoke_cua_module_chay_duoc():
    o._smoke()


if __name__ == "__main__":
    _util.main(globals())
