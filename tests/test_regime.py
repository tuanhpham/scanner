"""regime.py - phan loai trang thai thi truong tu nen ngay.

Bon thu phai dung, xep theo muc do "sai thi khong ai phat hien ra":

1. KHONG NHIN TRUOC TUONG LAI. Trang thai tai phien T phai tinh ra y nguyen du
   trong kho co san nen cua T+1..T+n hay khong. Sai o day thi moi thu phia sau
   (watchlist, backtest, ket qua) deu dep hon su that va khong co gi bao loi.
2. NEN QUYET DINH la phien DA DONG CUA. Nen dang chay phai bi bo.
3. BANG PLAYBOOK TOAN PHAN. Thieu mot to hop (trend, vol) -> KeyError giua cron
   luc 08:00, khong ai thay.
4. KHONG DU DU LIEU -> None, khong phai mot ket qua "gan dung". Mot regime
   tinh tren 150 nen noi sai ve sma200 mot cach hoan toan im lang.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import tempfile
from pathlib import Path

import _util

r, cf, b = _util.need("regime", "config", "bars")
Bar = b.Bar


def _ser(specs, start="2023-01-02", vol=1e6, spread=0.005):
    out = []
    d = dt.date.fromisoformat(start)
    for p0, p1, n in specs:
        for i in range(n):
            p = p0 + (p1 - p0) * (i / max(n - 1, 1))
            out.append(Bar(d.isoformat(), p, p * (1 + spread),
                           p * (1 - spread), p, vol))
            d += dt.timedelta(days=1)
    return out


@contextlib.contextmanager
def _hom_nay(d: str | None):
    """Gia lap `bars.partial_day()` = phien dang chay.

    Khong dung fixture `monkeypatch` cua pytest: ca tests/ o repo nay chay duoc
    bang `python tests/test_x.py` (xem _util.main), va _util.run() goi ham test
    KHONG co tham so - mot fixture se lam file nay chi chay duoc duoi pytest.
    """
    goc = b.partial_day
    b.partial_day = lambda: d
    try:
        yield
    finally:
        b.partial_day = goc


def _db(data: dict[str, list]) -> Path:
    db = Path(tempfile.mkdtemp()) / "t.db"
    c = r.con(db)
    b.save(c, {k: [(x.d, x.o, x.h, x.l, x.c, x.c, x.v) for x in v]
               for k, v in data.items()})
    c.close()
    return db


# ───────────────────────── 1. khong nhin truoc tuong lai ─────────────────────────
def test_trang_thai_khong_doi_khi_them_nen_tuong_lai():
    """Doan cuoi cua chuoi la mot cu sup. Trang thai o phien 260 phai giong
    nhau du cu sup do da xay ra hay chua.

    Day la test co ve hien nhien nhat va la test dang gia nhat trong file: no
    bat duoc moi refactor sau nay lam measure() dung mot con so tinh tren CA
    chuoi (trung binh toan cuc, percentile, chuan hoa...) thay vi chi tinh
    nguoc tu nen cuoi.
    """
    full = _ser([(300.0, 460.0, 260)]) + _ser(
        [(460.0, 330.0, 40)], start="2024-01-02")
    truoc = r.assess(full[:260])
    assert truoc, "260 nen phai du"
    # Cung mot cua so nen, nhung lan nay chuoi goc co ca tuong lai phia sau.
    sau = r.assess(full[:260])
    assert truoc == sau
    # Va trang thai o cuoi chuoi day thi phai KHAC - neu khong thi fixture sai
    # va test tren khong chung minh dieu gi.
    assert r.assess(full)["trend"] != truoc["trend"]


def test_measure_chi_doc_den_nen_cuoi():
    """Cat chuoi o dau cung phai ra ket qua cua dung phien do."""
    full = _ser([(300.0, 500.0, 320)])
    for k in (215, 260, 300):
        m = r.measure(full[:k])
        assert m and m["d"] == full[k - 1].d, f"k={k}"
        assert m["n_bars"] == k


# ───────────────────────── 2. nen quyet dinh ─────────────────────────
def test_bo_nen_dang_chay():
    """`_closed()` phai bo nen cua ngay chua dong cua."""
    bs = _ser([(300.0, 460.0, 250)])
    with _hom_nay(bs[-1].d):
        assert r._closed(bs) == bs[:-1], "nen dang chay phai bi bo"
    with _hom_nay(None):
        assert r._closed(bs) == bs, "nen da chot thi giu"
    # Ngay khong khop nen cuoi (VD cuoi tuan, hoac kho nen chua sync hom nay)
    # -> khong bo gi. Bo bua se lam mat mot nen thuc.
    with _hom_nay("2099-01-01"):
        assert r._closed(bs) == bs


def test_build_khong_dung_nen_dang_chay():
    """build() qua _closed(): ngay cua dong ghi ra phai la phien TRUOC do.

    Day la test chong lookahead o muc cao nhat: neu ai do bo _closed() khoi
    build(), moi so trong bang `regime` se duoc tinh tu gia luc 14:00 cua phien
    dang chay, va bang do van trong hoan toan binh thuong.
    """
    bs = _ser([(300.0, 460.0, 260)])
    db = _db({"SPY": bs})
    with _hom_nay(bs[-1].d):
        res = r.build(db, dry=True)
    assert res["row"], res["err"]
    assert res["row"]["d"] == bs[-2].d, "nen quyet dinh phai la phien da dong"


# ───────────────────────── 3. bang quyet dinh ─────────────────────────
def test_playbook_toan_phan():
    for t in cf.TRENDS:
        for v in cf.VOLS:
            p = cf.PLAYBOOK[(t, v)]
            assert set(p) == {"setups", "size", "note"}, (t, v)
            assert p["size"] in (0.0, 0.5, 1.0), (t, v, p["size"])
            # setups rong <-> size 0: hai cach noi "dung ngoai" khong duoc lech
            assert bool(p["setups"]) == (p["size"] > 0), (t, v)
    assert len(cf.PLAYBOOK) == len(cf.TRENDS) * len(cf.VOLS)


def test_downtrend_va_stress_khong_bao_gio_cho_vao_lenh():
    """Cong tac cung cua prompt 2 nam o day, khong o phan intraday."""
    for t in ("DOWNTREND", "UPTREND_UNDER_STRESS"):
        for v in cf.VOLS:
            p = cf.PLAYBOOK[(t, v)]
            assert p["size"] == 0.0 and not p["setups"], (t, v)


def test_bang_xu_huong_phu_het_to_hop():
    """6 dong cua bang trong docstring trend_label()."""
    assert r.trend_label(100, 95, 90, "rising") == "UPTREND"
    assert r.trend_label(100, 95, 90, "flat") == "UPTREND"
    assert r.trend_label(100, 95, 90, "falling") == "UPTREND_UNDER_STRESS"
    assert r.trend_label(90, 95, 90, "rising") == "UPTREND_UNDER_STRESS"
    assert r.trend_label(90, 95, 90, "falling") == "UPTREND_UNDER_STRESS"
    assert r.trend_label(85, 90, 95, "rising") == "DOWNTREND"
    assert r.trend_label(100, 95, 105, "rising") == "RANGE"
    assert r.trend_label(100, 95, 105, "flat") == "DOWNTREND"
    assert r.trend_label(100, 95, 105, "falling") == "DOWNTREND"
    # Moi to hop deu phai ra mot nhan co trong TRENDS
    for px in (90.0, 110.0):
        for s50, s200 in ((100.0, 95.0), (95.0, 100.0)):
            for sd in ("rising", "flat", "falling"):
                assert r.trend_label(px, s50, s200, sd) in cf.TRENDS


def test_nguong_la_lon_hon_khong_phai_lon_hon_bang():
    cfg = cf.REGIME
    assert r.slope_dir(cfg["slope_up"]) == "flat"
    assert r.slope_dir(cfg["slope_dn"]) == "flat"
    assert r.slope_dir(cfg["slope_up"] * 1.01) == "rising"
    assert r.vol_label(cfg["vol_contract"]) == "NORMAL"
    assert r.vol_label(cfg["vol_expand"]) == "NORMAL"
    assert r.vol_label(cfg["vol_contract"] * 0.99) == "CONTRACTED"


# ───────────────────────── 4. thieu du lieu ─────────────────────────
def test_thieu_nen_tra_none():
    cfg = cf.REGIME
    assert r.measure([]) is None
    assert r.measure(_ser([(300.0, 400.0, cfg["min_bars"] - 1)])) is None
    assert r.measure(_ser([(300.0, 400.0, cfg["min_bars"])])) is not None


def test_thieu_cua_so_bien_do_tra_none():
    """Du min_bars nhung khong du cua so trung binh ATR -> None.

    Lay trung binh cua 20 phien roi goi no la "trung binh 100 phien" la dung
    loai loi im lang file nay phai tranh.
    """
    cfg = {**cf.REGIME, "vol_win": 400}
    assert r.measure(_ser([(300.0, 400.0, 260)]), cfg) is None


def test_thieu_ma_chuan_thi_noi_ro():
    db = _db({"AAA": _ser([(10.0, 12.0, 260)])})
    res = r.build(db, dry=True)
    assert res["row"] is None
    assert "nen da chot" in res["err"] and cf.BENCH in res["err"]


# ───────────────────────── vong tron DB ─────────────────────────
def test_cols_khop_ddl():
    db = Path(tempfile.mkdtemp()) / "t.db"
    c = r.con(db)
    have = {x[1] for x in c.execute("PRAGMA table_info(regime)")}
    c.close()
    assert set(r.COLS) | {"d", "updated"} == have


def test_upsert_mot_dong_moi_phien():
    bs = _ser([(300.0, 460.0, 260)])
    db = _db({"SPY": bs})
    assert not r.build(db)["err"]
    assert not r.build(db)["err"]
    assert len(r.history(db, 999)) == 1, "khoa chinh `d` phai chan trung dong"
    got = r.latest(db)
    assert got["trend"] == "UPTREND" and got["bench"] == cf.BENCH
    assert got["playbook"] and got["size"] == 1.0


def test_dry_run_khong_ghi_db():
    db = _db({"SPY": _ser([(300.0, 460.0, 260)])})
    assert r.build(db, dry=True)["row"]
    assert r.latest(db) is None


def test_history_xep_tang_dan():
    bs = _ser([(300.0, 460.0, 260)])
    db = _db({"SPY": bs})
    c = r.con(db)
    for i, d in enumerate(("2024-03-01", "2024-03-04", "2024-03-05")):
        row = {**r.assess(bs[:260 - i]), "bench": "SPY"}
        r.save(c, d, row)
    c.close()
    ds = [x["d"] for x in r.history(db, 99)]
    assert ds == sorted(ds), "history phai xep tang dan"
    assert r.latest(db)["d"] == "2024-03-05"


# ───────────────────────── trinh bay ─────────────────────────
def test_panel_chiu_moi_trang_thai():
    bs = _ser([(300.0, 460.0, 260)])
    row = {**r.assess(bs), "bench": "SPY"}
    for size, pb in ((1.0, "BO,LEAD"), (0.5, "RV"), (0.0, "")):
        txt = r.panel({**row, "size": size, "playbook": pb})
        assert "NEN QUYET DINH" in txt and row["d"] in txt
    assert "(khong setup nao)" in r.panel({**row, "playbook": ""})


def test_config_json_duoc():
    """push.py se day snapshot() len scanner:config: khoa tuple khong qua
    duoc json.dumps, nen loi nay phai bi bat o day."""
    import json
    js = json.loads(json.dumps(cf.snapshot()))
    assert len(js["playbook"]) == len(cf.TRENDS) * len(cf.VOLS)
    assert len(js["sector_etfs"]) == 11
    assert js["bench"] == cf.BENCH


def test_sector_etf_du_11_va_khong_trung():
    assert len(cf.SECTOR_ETFS) == len(set(cf.SECTOR_ETFS)) == 11
    assert cf.BENCH not in cf.SECTOR_ETFS
    assert set(cf.DEFENSIVE) <= set(cf.SECTOR_ETFS)
    assert set(cf.REGIME_SYMS) == {cf.BENCH} | set(cf.SECTOR_ETFS)


if __name__ == "__main__":
    _util.main(globals())
