"""prep.py - so do bang `base` va migrate cot.

File nay CHI kiem tra phan schema, khong kiem tra phan tai du lieu: tai du lieu
can pandas + mang, va do la viec cua mot lan chay that.

Vi sao dang test rieng: hai cot da tung bi ghi/doc ma khong he duoc khai bao o
dau ca -
  - scorer.py GHI `float_ts` (UPDATE base SET float_sh=?, float_ts=?)
  - scorer.py DOC `is_etf` (SELECT ... WHERE is_etf=0)
va `is_etf` truoc day chi xuat hien khi chay tay scripts/mark_etf.py. Tuc la mot
DB dung tu dau thi scorer.py nem "no such column" ngay lan chay dau. Loi do im
lang cho den dung luc can no nhat, nen no xung dang mot test rieng.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import _util

pr = _util.need("prep")


def _db(sql: str) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.executescript(sql)
    return c


def cols(c: sqlite3.Connection) -> set[str]:
    return {r[1] for r in c.execute("PRAGMA table_info(base)")}


def test_ddl_moi_da_co_du_moi_cot_ma_code_khac_dung():
    c = _db(pr.DDL)
    have = cols(c)
    # Danh sach nay la cac cot co code that su doc/ghi. Them cot moi vao DB ma
    # quen dong nay thi test khong bao gi - nhung quen KHAI BAO cot thi bao.
    for need in ("sym", "adv20", "atr14", "prev_close", "float_sh", "float_ts",
                 "cik", "exch", "is_etf", "updated"):
        assert need in have, f"DDL thieu cot `{need}`"


def test_cau_update_cua_scorer_chay_duoc_tren_ddl_moi():
    # Day la dung cau lenh o scorer.py:69. Truoc khi co `float_ts` trong DDL,
    # cau nay nem OperationalError tren mot DB moi.
    c = _db(pr.DDL)
    c.execute("INSERT INTO base(sym) VALUES('AAPL')")
    c.execute("UPDATE base SET float_sh=?, float_ts=? WHERE sym=?",
              (1e9, "2026-01-01", "AAPL"))
    # Va cau SELECT o scorer.py:39-40.
    row = c.execute("SELECT sym,adv20,atr14,prev_close,float_sh,cik,is_etf "
                    "FROM base WHERE is_etf=0").fetchone()
    assert row and row[0] == "AAPL"
    assert row[6] == 0, "is_etf phai mac dinh 0, khong phai NULL"


def test_ensure_cols_them_cot_vao_bang_cu():
    # Bang `base` nhu no ton tai TRUOC thay doi lan nay.
    cu = """CREATE TABLE base (
              sym TEXT PRIMARY KEY, adv20 REAL, atr14 REAL, prev_close REAL,
              float_sh REAL, cik TEXT, updated TEXT);"""
    c = _db(cu)
    c.execute("INSERT INTO base(sym,adv20) VALUES('MSFT',123.0)")
    assert "exch" not in cols(c)

    added = pr.ensure_cols(c)
    assert set(added) == {"float_ts", "exch", "is_etf", "mktcap", "mktcap_ts"}
    assert {"float_ts", "exch", "is_etf", "mktcap"} <= cols(c)
    # Du lieu cu con nguyen - ALTER TABLE ADD COLUMN khong duoc lam mat gi.
    assert c.execute("SELECT adv20 FROM base WHERE sym='MSFT'").fetchone()[0] == 123.0


def test_cot_mktcap_khai_bao_giong_nhau_o_hai_noi():
    """watchlist.refresh_mktcap() tu ALTER hai cot nay vi no co the chay tren mot
    DB prep.py chua cham vao, va watchlist.py phai thuan stdlib (khong import
    duoc prep.py - prep import pandas + yfinance).

    Hai ban khai bao thi phai khop KIEU, khong chi khop ten: `mktcap TEXT` mot
    ben va `REAL` ben kia se cho ra so sanh von hoa theo thu tu chuoi, tuc la
    "9e9" > "20000000000" - san $2B im lang doi chieu sai.
    """
    wl = _util.need("watchlist")
    assert set(wl.MKTCAP_COLS) <= set(pr.ADD_COLS), (wl.MKTCAP_COLS, pr.ADD_COLS)


def test_ensure_cols_chay_lai_khong_lam_gi():
    # Cron goi prep.py moi ngay. Lan thu hai phai la khong-lam-gi, chu khong
    # phai "duplicate column name".
    c = _db(pr.DDL)
    assert pr.ensure_cols(c) == []
    assert pr.ensure_cols(c) == []


def test_exch_khong_bi_xoa_boi_lan_chay_from_bars():
    # --from-bars doc tu kho nen, khong goi Alpaca -> khong biet san. Cau UPSERT
    # cua nhanh do KHONG duoc chua cot `exch`; neu no ghi NULL vao thi mot lan
    # chay se xoa san cua ca DB va cong chat luong im lang loai sach moi ma.
    src = Path(pr.__file__).read_text(encoding="utf-8")
    i = src.index("def compute_from_bars") if "def compute_from_bars" in src else 0
    # Tim cau INSERT khong co `exch` (nhanh --from-bars) va cau co `exch`
    # (nhanh tai tu yfinance). Phai co dung mot cai moi loai.
    khong_exch = src.count(
        "INSERT INTO base(sym,adv20,atr14,prev_close,cik,updated)")
    co_exch = src.count(
        "INSERT INTO base(sym,adv20,atr14,prev_close,cik,exch,updated)")
    assert khong_exch == 1, f"mong doi 1 nhanh khong ghi exch, thay {khong_exch}"
    assert co_exch == 1, f"mong doi 1 nhanh co ghi exch, thay {co_exch}"
    assert "exch=COALESCE(excluded.exch, base.exch)" in src, (
        "nhanh co exch phai dung COALESCE, khong ghi de thang")
    assert i >= 0


if __name__ == "__main__":
    _util.main(globals())
