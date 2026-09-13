"""bars.py — kho nen ngay.

Hai vung dang test nhat:

1. `load(adj=True)` — sai o day thi mot lan chia 1:10 bien thanh "roi 90%" gia
   va setup RV se alert mot su kien khong ton tai.
2. `_rows_of()` — doc DataFrame cua yfinance. May dev khong co pandas nen day
   la CACH DUY NHAT kiem tra logic nay truoc khi push: dung DataFrame gia,
   chi can dung dung ba thu ma _rows_of() thuc su dua vao (.columns, .index,
   df[col].tolist()).
"""
from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path

import _util

b = _util.need("bars")


def _db() -> Path:
    return Path(tempfile.mkdtemp()) / "t.db"


def _rows(n=30, px=10.0, vol=1e6, start="2025-01-06", ac=None):
    d0 = dt.date.fromisoformat(start)
    out = []
    for i in range(n):
        d = (d0 + dt.timedelta(days=i)).isoformat()
        out.append((d, px, px * 1.01, px * 0.99, px, ac if ac else px, vol))
    return out


# ───────────────────────── ghi / doc ─────────────────────────
def test_upsert_khong_nhan_doi():
    c = b.con(_db())
    r = _rows(10)
    b.save(c, {"AAA": r})
    b.save(c, {"AAA": r})
    assert len(b.load(c, "AAA", limit=99)) == 10


def test_ghi_de_gia_moi():
    """Ngay dang chay duoc ghi lai khi --sync chay lan hai trong ngay."""
    c = b.con(_db())
    b.save(c, {"AAA": [("2025-03-03", 1.0, 1.0, 1.0, 1.0, 1.0, 100.0)]})
    b.save(c, {"AAA": [("2025-03-03", 2.0, 2.0, 2.0, 2.0, 2.0, 900.0)]})
    got = b.load(c, "AAA")
    assert len(got) == 1 and got[0].c == 2.0 and got[0].v == 900.0


def test_xep_tang_dan_du_ghi_lung_tung():
    c = b.con(_db())
    r = _rows(5)
    b.save(c, {"AAA": list(reversed(r))})
    got = [x.d for x in b.load(c, "AAA")]
    assert got == sorted(got)


def test_limit_lay_nen_moi_nhat():
    c = b.con(_db())
    r = _rows(50)
    b.save(c, {"AAA": r})
    got = b.load(c, "AAA", limit=3)
    assert [x.d for x in got] == [x[0] for x in r[-3:]]


def test_upto_khong_nhin_truoc_tuong_lai():
    """Tham so nen tang cua backtest: chan het nen sau ngay chi dinh."""
    c = b.con(_db())
    r = _rows(20)
    b.save(c, {"AAA": r})
    cut = r[7][0]
    got = b.load(c, "AAA", limit=999, upto=cut)
    assert len(got) == 8 and got[-1].d == cut


def test_bo_nen_thieu_du_lieu():
    c = b.con(_db())
    b.save(c, {"AAA": [("2025-01-02", 1.0, 1.0, 1.0, None, None, None),
                       ("2025-01-03", 1.0, 1.0, 1.0, 0.0, 0.0, 5.0),
                       ("2025-01-06", 1.0, 1.0, 1.0, 1.0, 1.0, 5.0)]})
    got = b.load(c, "AAA")
    assert [x.d for x in got] == ["2025-01-06"], "close None hoac 0 phai bi bo"


# ───────────────────────── dieu chinh gia ─────────────────────────
def test_split_duoc_back_adjust():
    """ac = c/4 (chia 1:4) -> gia /4, volume x4, khong tao cu roi 75% gia."""
    c = b.con(_db())
    b.save(c, {"AAA": [("2025-01-02", 400.0, 404.0, 396.0, 400.0, 100.0, 1000.0)]})
    a = b.load(c, "AAA", adj=True)[0]
    assert (round(a.o, 6), round(a.h, 6), round(a.l, 6), round(a.c, 6)) == \
        (100.0, 101.0, 99.0, 100.0)
    assert round(a.v, 6) == 4000.0


def test_adj_false_giu_gia_tho():
    """prep.py can gia THO: prev_close phai khop quote live, khong duoc dieu chinh."""
    c = b.con(_db())
    b.save(c, {"AAA": [("2025-01-02", 400.0, 404.0, 396.0, 400.0, 100.0, 1000.0)]})
    r = b.load(c, "AAA", adj=False)[0]
    assert r.c == 400.0 and r.v == 1000.0


def test_khong_co_ac_thi_khong_doi_gi():
    c = b.con(_db())
    b.save(c, {"AAA": _rows(3)})           # ac = c
    for x in b.load(c, "AAA"):
        assert abs(x.c - 10.0) < 1e-9 and abs(x.v - 1e6) < 1e-9


# ───────────────────────── kiem ke ─────────────────────────
def test_syms_va_coverage():
    c = b.con(_db())
    b.save(c, {"AAA": _rows(70), "BBB": _rows(5)})
    assert b.syms(c) == ["AAA", "BBB"]
    assert b.syms(c, min_rows=60) == ["AAA"], "ma qua ngan phai bi loc"
    cov = b.coverage(c)
    assert cov["syms"] == 2 and cov["rows"] == 75 and cov["thin"] == 1
    assert cov["last"] == b.last_date(c) == b.last_date(c, "AAA")


def test_purge_giu_nen_moi():
    c = b.con(_db())
    old = (dt.date.today() - dt.timedelta(days=900)).isoformat()
    b.save(c, {"AAA": [(old, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)] + _rows(3)})
    assert b.purge(c, keep_days=800) == 1
    assert len(b.load(c, "AAA")) == 3


# ───────────────────────── doc DataFrame cua yfinance ─────────────────────────
class _Ser:
    def __init__(self, v):
        self._v = v

    def tolist(self):
        return list(self._v)


class _DF:
    """DataFrame gia — chi cai gi _rows_of() thuc su dung."""

    def __init__(self, cols: dict, index):
        self._c = cols
        self.index = index

    @property
    def columns(self):
        return list(self._c)

    def __getitem__(self, k):
        return _Ser(self._c[k])


def test_rows_of_doc_dung_ten_cot():
    """'Adj Close' co dau cach: doc theo ten, khong theo thu tu cot."""
    idx = [dt.datetime(2025, 1, 2), dt.datetime(2025, 1, 3)]
    df = _DF({"Open": [1.0, 2.0], "High": [1.5, 2.5], "Low": [0.5, 1.5],
              "Close": [1.2, 2.2], "Adj Close": [0.6, 1.1],
              "Volume": [100.0, 200.0]}, idx)
    rows = b._rows_of(df)
    assert rows == [("2025-01-02", 1.0, 1.5, 0.5, 1.2, 0.6, 100.0),
                    ("2025-01-03", 2.0, 2.5, 1.5, 2.2, 1.1, 200.0)]


def test_rows_of_bo_ngay_nan():
    nan = float("nan")
    idx = [dt.datetime(2025, 1, 2), dt.datetime(2025, 1, 3)]
    df = _DF({"Open": [1.0, nan], "High": [1.5, nan], "Low": [0.5, nan],
              "Close": [1.2, nan], "Adj Close": [1.2, nan],
              "Volume": [100.0, nan]}, idx)
    rows = b._rows_of(df)
    assert len(rows) == 1 and rows[0][0] == "2025-01-02"


def test_rows_of_thieu_adj_close():
    idx = [dt.datetime(2025, 1, 2)]
    df = _DF({"Open": [1.0], "High": [1.5], "Low": [0.5], "Close": [1.2],
              "Volume": [100.0]}, idx)
    assert b._rows_of(df)[0][5] == 1.2, "khong co Adj Close -> ac = c"


def test_rows_of_index_khong_phai_timestamp():
    df = _DF({"Close": [1.2], "Volume": [1.0]}, ["2025-01-02 00:00:00"])
    assert b._rows_of(df)[0][0] == "2025-01-02"


def test_rows_of_khong_co_cot_close():
    assert b._rows_of(_DF({"Volume": [1.0]}, [dt.datetime(2025, 1, 2)])) == []


if __name__ == "__main__":
    _util.main(globals())
