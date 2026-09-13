"""structure.py — do cau truc gia theo nen ngay.

Ba thu phai dung, xep theo muc do "sai thi khong ai phat hien ra":

1. KHONG NHIN TRUOC TUONG LAI. metrics() tinh tren nen den ngay T phai ra dung
   ket qua nhu khi chi co du lieu den ngay T. Sai o day thi backtest cho ket
   qua dep va bot chay ngoai doi thi khong.
2. pivot khong duoc lay tu nen hom nay, khong thi breakout tu nang pivot cua
   chinh no len va dieu kien "px > pivot" khong bao gio dat.
3. adv KHONG tinh nen hom nay: gop hom nay vao mau so lam RVOL nho lai dung
   luc no can to.
"""
from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path

import _util

s, b = _util.need("structure", "bars")
Bar = b.Bar


def _ser(specs, start="2024-01-02", vol=1e6, spread=0.005):
    out = []
    d = dt.date.fromisoformat(start)
    for p0, p1, n in specs:
        for i in range(n):
            p = p0 + (p1 - p0) * (i / max(n - 1, 1))
            out.append(Bar(d.isoformat(), p, p * (1 + spread), p * (1 - spread),
                           p, vol))
            d += dt.timedelta(days=1)
    return out


def _db(data: dict[str, list]) -> Path:
    db = Path(tempfile.mkdtemp()) / "t.db"
    c = s.con(db)
    b.save(c, {k: [(x.d, x.o, x.h, x.l, x.c, x.c, x.v) for x in v]
               for k, v in data.items()})
    c.close()
    return db


# ───────────────────────── khong nhin truoc tuong lai ─────────────────────────
def test_upto_cho_dung_ket_qua_nhu_cat_chuoi():
    """Chan tren cua ca backtest: doc DB voi upto == tinh tren chuoi da cat."""
    ser = _ser([(10.0, 12.0, 90), (12.0, 12.0, 60), (12.0, 15.0, 30)])
    db = _db({"AAA": ser})
    cut = ser[149].d                       # dung truoc doan tang cuoi
    truc_tiep = s.metrics(ser[:150])
    qua_db = s.metrics(b.load(db, "AAA", limit=999, upto=cut))
    assert truc_tiep and qua_db
    for k in truc_tiep:
        assert truc_tiep[k] == qua_db[k], f"{k} lech: {truc_tiep[k]} vs {qua_db[k]}"
    assert qua_db["d"] == cut


def test_them_nen_moi_khong_doi_qua_khu():
    """metrics() cua ngay T khong duoc thay doi khi ngay T+1 xuat hien."""
    ser = _ser([(10.0, 10.5, 120)])
    a = s.metrics(ser)
    ser2 = ser + [Bar("2024-09-09", 10.5, 14.0, 10.4, 13.9, 9e6)]
    a2 = s.metrics(ser2[:-1])
    assert a == a2


# ───────────────────────── pivot ─────────────────────────
def test_pivot_khong_lay_tu_nen_hom_nay():
    ser = _ser([(8.0, 10.0, 80), (10.0, 10.0, 40)])
    ser.append(Bar("2024-06-01", 10.05, 11.5, 10.0, 11.4, 4e6))
    m = s.metrics(ser)
    assert m["pivot"] < ser[-1].h, "nen hom nay bi tinh vao nen tich luy"
    assert m["dist_pivot"] < 0, "gia tren pivot -> dist_pivot am"


def test_chua_vuot_pivot_thi_dist_duong():
    ser = _ser([(8.0, 10.0, 80), (10.0, 10.0, 40)])
    ser.append(Bar("2024-06-01", 9.9, 9.95, 9.8, 9.9, 1e6))
    m = s.metrics(ser)
    assert m["dist_pivot"] > 0 and m["pivot"] > m["px"]


# ───────────────────────── nen tich luy ─────────────────────────
def test_nen_phai_phang_khong_phai_kenh_gia():
    """Truot deu 30 -> 26 trong 90 phien: bien do 13%, va do troi cung 13%."""
    assert s.find_base(_ser([(30.0, 26.0, 90)])) is None
    assert s.find_base(_ser([(26.0, 30.0, 90)])) is None, "kenh tang cung vay"


def test_nen_khong_nuot_doan_tang_truoc_do():
    ser = _ser([(20.0, 30.0, 60), (30.0, 30.0, 45)])
    base = s.find_base(ser)
    assert base and base["len"] <= 58, f"nuot ca doan tang: len={base['len']}"
    assert base["len"] >= 40, "bo mat phan lon doan phang"


def test_bien_do_khong_vuot_tran():
    for ser in (_ser([(10.0, 10.0, 60)]),
                _ser([(10.0, 10.0, 30), (10.0, 11.0, 30)]),
                _ser([(9.0, 11.0, 40), (11.0, 9.5, 40)])):
        base = s.find_base(ser)
        if base:
            assert base["depth"] <= s.BASE_MAX_DEPTH + 1e-9
            assert abs(base["slope"]) * base["len"] <= s.drift_cap(base["depth"]) + 1e-9


def test_khong_co_nen_thi_bao_khong():
    m = s.metrics(_ser([(100.0, 30.0, 160)]))
    assert m["base_len"] == 0 and m["pivot"] is None and m["dist_pivot"] is None


# ───────────────────────── volume ─────────────────────────
def test_adv_khong_tinh_nen_hom_nay():
    ser = _ser([(10.0, 10.0, 80)], vol=1e6)
    ser.append(Bar("2024-04-01", 10.0, 11.0, 10.0, 10.9, 8e6))
    m = s.metrics(ser)
    assert abs(m["adv20"] - 1e6) < 1e-6, "hom nay bi gop vao mau so"
    assert abs(m["vol_ratio"] - 8.0) < 1e-6


def test_dryup_do_co_ngot_trong_nen():
    quiet = (_ser([(10.0, 10.0, 60)], vol=5e6)
             + _ser([(10.0, 10.0, 40)], start="2024-04-01", vol=8e5))
    m = s.metrics(quiet)
    assert m["base_dryup"] < 0.8, m["base_dryup"]


# ───────────────────────── cu roi sau ─────────────────────────
def test_do_sau_cu_roi():
    m = s.metrics(_ser([(100.0, 20.0, 200)]))
    assert 0.79 < m["off_high"] < 0.82, m["off_high"]
    assert m["up_from_low"] < 0.02
    assert m["days_since_low"] == 0, "day 52 tuan la chinh hom nay"
    assert m["below20_streak"] > 50
    assert m["ret63"] < -0.2


def test_bat_lai_sau_khi_roi():
    # roi 100 -> 25, nam im 15 phien o day, roi mot phien bat +11% volume 6x
    ser = _ser([(100.0, 25.0, 150)]) + _ser([(25.0, 25.0, 15)], start="2024-06-01")
    # day cua nen bat lai (24.9) con TREN day cu (24.875) -> day 52 tuan la
    # nen hom qua, days_since_low = 1
    ser += [Bar("2024-07-01", 25.0, 28.0, 24.9, 27.8, 6e6)]
    m = s.metrics(ser)
    assert m["close_pos"] > 0.9, "dong cua sat dinh ngay"
    assert m["vol_ratio"] > 5 and m["days_since_low"] == 1, m["days_since_low"]
    assert m["below20_streak"] == 0, "da vuot lai sma20"
    assert m["off_high"] > 0.7 and m["up_from_low"] > 0.1


# ───────────────────────── baseline cho prep.py --from-bars ─────────────────
def test_baseline_ba_con_so():
    ser = _ser([(10.0, 10.0, 40)], vol=1e6, spread=0.01)
    m = s.baseline(ser)
    assert abs(m["adv20"] - 1e6) < 1e-6
    assert m["prev_close"] == 10.0
    assert abs(m["atr14"] - 0.2) < 1e-9, m["atr14"]   # bien do 2% cua gia 10


def test_baseline_loc_kem_thanh_khoan_va_penny():
    assert s.baseline(_ser([(10.0, 10.0, 40)], vol=1000.0)) is None
    assert s.baseline(_ser([(0.5, 0.5, 40)], vol=1e7)) is None
    assert s.baseline(_ser([(10.0, 10.0, 20)])) is None, "duoi 25 nen"


def test_baseline_atr_giong_dinh_nghia_cua_metrics():
    """Hai cho tinh ATR khac nhau thi atr_move trong alert va atr_contract
    trong struct se noi hai chuyen khac nhau ve cung mot ma."""
    ser = _ser([(9.0, 11.0, 90)], spread=0.02)
    assert s.baseline(ser)["atr14"] == s.metrics(ser)["atr14"]


def test_thieu_du_lieu_tra_none():
    assert s.metrics(_ser([(10.0, 10.0, s.MIN_HIST - 1)])) is None
    assert s.metrics([]) is None


# ───────────────────────── xep hang + bang struct ─────────────────────────
def test_rank_pct():
    r = s.rank_pct({"A": 0.5, "B": -0.1, "C": 0.2, "D": None})
    assert r["B"] == 0.0 and r["A"] == 100.0 and r["D"] is None
    assert r["C"] == 50.0
    assert s.rank_pct({"X": 1.0})["X"] == 50.0


def test_cols_khop_ddl():
    """Them mot khoa vao metrics() ma quen sua DDL -> build() nem loi luc chay."""
    c = s.con(Path(tempfile.mkdtemp()) / "t.db")
    have = {r[1] for r in c.execute("PRAGMA table_info(struct)")}
    assert set(s.COLS) | {"sym", "updated"} == have
    m = s.metrics(_ser([(10.0, 10.0, 80)]))
    assert set(m) == set(s.COLS), set(m) ^ set(s.COLS)
    c.close()


def test_build_va_load_struct():
    db = _db({"BOO": _ser([(8.0, 10.0, 80), (10.0, 10.0, 40)]),
              "DIP": _ser([(100.0, 25.0, 150)]),
              "TIN": _ser([(5.0, 5.0, 10)])})          # qua ngan
    st = s.build(db)
    assert st["co_so_lieu"] == 2 and st["co_nen"] == 1, st
    d = s.load_struct(db)
    assert set(d) == {"BOO", "DIP"}
    assert d["BOO"]["base_len"] >= s.MIN_BASE and d["DIP"]["base_len"] == 0
    assert d["BOO"]["rs_pct"] == 100.0 and d["DIP"]["rs_pct"] == 0.0


def test_build_ghi_de_khong_gop_hai_phien():
    ser = _ser([(10.0, 10.0, 80)])
    db = _db({"AAA": ser})
    s.build(db)
    c = s.con(db)
    b.save(c, {"BBB": [(x.d, x.o, x.h, x.l, x.c, x.c, x.v) for x in ser]})
    s.build(c)
    n = c.execute("SELECT COUNT(*) FROM struct").fetchone()[0]
    assert n == 2, "build() phai ghi de ca bang, khong duoc chen them"
    c.close()


if __name__ == "__main__":
    _util.main(globals())
