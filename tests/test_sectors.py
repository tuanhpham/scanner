"""sectors.py - xep hang 11 sector SPDR.

Nam thu phai dung, xep theo muc do "sai thi khong ai phat hien ra":

1. KHONG NHIN TRUOC TUONG LAI. Xep hang lai mot phien qua khu phai ra y nguyen
   ket qua da tinh hom do. Sai o day thi bieu do lich su hang - cai view dang
   gia nhat tren dashboard - la mot loi noi doi kem bang chung.
2. CUNG MOT NEN QUYET DINH CHO CA 11 MA. Percentile la so sanh cheo; mot ma tre
   mot phien lam lech ca 11 hang ma bang van in ra binh thuong.
3. DAU CUA "DOI HANG". Hang nho la tot, nen di tu 5 len 2 la +3. Nham dau thi
   mui ten tren dashboard chi sai nguoc suot doi.
4. DIEM TONG = trung binh percentile, khong phai trung binh loi nhuan.
5. CHUA CO LICH SU -> None, khong phai 0. "Khong doi hang" khac "khong biet".
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import _util

sec, cf, b = _util.need("sectors", "config", "bars")
_ser = None  # gan lai duoi day, sau khi biet sectors import duoc
_ser = sec._ser


def _rows(bs_by_sym: dict[str, list]) -> dict[str, list[tuple]]:
    return {s: [(x.d, x.o, x.h, x.l, x.c, x.c, x.v) for x in v]
            for s, v in bs_by_sym.items()}


def _db(bs_by_sym: dict[str, list], bench: bool = True):
    """DB tam co san nen. Tra ve Connection (tu dong don khi process ket thuc).

    Ma chuan phai dai DUNG bang chuoi sector dai nhat: backfill() lay lich phien
    tu ma chuan, nen mot SPY dai hon se sinh ra nhung ngay ma ca ro sector khong
    co nen - dung that (build() ghi de dong cu) nhung lam test dem sai.
    """
    db = Path(tempfile.mkdtemp()) / "t.db"
    c = sec.con(db)
    b.save(c, _rows(bs_by_sym))
    if bench:
        n = max(len(v) for v in bs_by_sym.values())
        b.save(c, _rows({cf.BENCH: _ser([(400.0, 460.0, n)])}))
    return c


def _ro(n: int = 300) -> dict[str, list]:
    return sec._fake_ro(n)


# ───────────────────────── 1. khong nhin truoc tuong lai ─────────────────────────
def test_xep_lai_phien_qua_khu_ra_ket_qua_y_nguyen():
    """Kho nen CO tuong lai vs kho nen KHONG co tuong lai -> cung ket qua.

    Day la test dang gia nhat file: `--backfill` dung ca lich su bieu do tu
    bars.load(upto=d). Neu upto bi ho, hang cua thang 3 se duoc tinh bang gia
    thang 9, va bieu do se cho thay moi sector "doan dung" tuong lai.
    """
    ro = _ro(300)
    moc = ro["XLK"][249].d

    # A: kho chi co nen den `moc`, khong co gi sau do
    ca = _db({s: v[:250] for s, v in ro.items()})
    a = sec.build(ca, dry=True)
    assert a["err"] is None and a["d"] == moc

    # B: kho co du 300 nen, nhung yeu cau xep den `moc`
    cb = _db(ro)
    bb = sec.build(cb, dry=True, upto=moc)
    assert bb["err"] is None and bb["d"] == moc

    ka = {r["sym"]: (r["rank"], round(r["composite"], 9),
                     round(r["ret21"], 12), round(r["ret126"], 12),
                     round(r["sma50"], 9), round(r["ema21"], 9))
          for r in a["rows"]}
    kb = {r["sym"]: (r["rank"], round(r["composite"], 9),
                     round(r["ret21"], 12), round(r["ret126"], 12),
                     round(r["sma50"], 9), round(r["ema21"], 9))
          for r in bb["rows"]}
    assert ka == kb
    ca.close()
    cb.close()


def test_backfill_ghi_dung_hang_cua_tung_phien():
    ro = _ro(300)
    c = _db(ro)
    bf = sec.backfill(c, days=40, quiet=True)
    assert bf["err"] is None and bf["ok"] == 40
    ds = sec.dates(c)
    assert len(ds) == 40 and ds == sorted(ds)
    # Xep lai bat ky phien nao trong so do -> giong dong da luu
    for d in (ds[0], ds[17], ds[-1]):
        luu = {r["sym"]: r["rank"] for r in sec.load_rank(c, d)}
        lai = {r["sym"]: r["rank"]
               for r in sec.build(c, dry=True, upto=d)["rows"]}
        assert luu == lai, d
    c.close()


def test_backfill_khong_dem_phien_khong_co_nen_moi():
    """SPY da co nen den thu Sau ma ca ro sector chi den thu Ba.

    20 ngay lich cuoi cua SPY khong co nen sector nao moi -> build(upto=d) ghi
    de dung dong cu. Phai dem vao `trung`, khong duoc bao la 40 phien: bao sai
    se che mat viec kho nen sector dang thieu nen.
    """
    ro = _ro(300)
    db = Path(tempfile.mkdtemp()) / "t.db"
    c = sec.con(db)
    b.save(c, _rows(ro))
    b.save(c, _rows({cf.BENCH: _ser([(400.0, 460.0, 320)])}))   # dai hon 20 nen
    bf = sec.backfill(c, days=40, quiet=True)
    assert bf["ok"] == 20 and bf["trung"] == 20, bf
    assert len(sec.dates(c)) == 20
    c.close()


def test_build_bo_nen_dang_chay():
    """upto=None -> qua regime._closed(), nen dang chay khong duoc tinh."""
    ro = _ro(300)
    c = _db(ro)
    goc = b.partial_day
    b.partial_day = lambda: ro["XLK"][-1].d
    try:
        res = sec.build(c, dry=True)
    finally:
        b.partial_day = goc
    assert res["d"] == ro["XLK"][-2].d, "phai la phien da dong cua"
    c.close()


# ───────────────────────── 2. cung mot nen quyet dinh ─────────────────────────
def test_mot_ma_tre_thi_tu_choi_xep_hang():
    ro = _ro(300)
    ro["XLRE"] = ro["XLRE"][:-1]          # XLRE thieu nen moi nhat
    c = _db(ro)
    res = sec.build(c)
    assert res["err"] and not res["rows"]
    assert "XLRE" in res["err"], res["err"]
    assert not sec.dates(c), "tu choi thi khong duoc ghi nua ro vao DB"
    c.close()


def test_thieu_han_mot_sector_thi_neu_ten():
    ro = _ro(300)
    del ro["XLE"]
    c = _db(ro)
    res = sec.build(c)
    assert res["err"] and "XLE" in res["err"] and not res["rows"]
    c.close()


def test_khong_du_nen_thi_tra_none_chu_khong_bot_cua_so():
    cfg = cf.SECTORS
    assert sec.measure([], cfg) is None
    assert sec.measure(_ser([(100.0, 120.0, cfg["min_bars"] - 1)]), cfg) is None
    assert sec.measure(_ser([(100.0, 120.0, cfg["min_bars"])]), cfg) is not None
    # du min_bars nhung cua so dai hon so nen -> None, khong im lang bo ret126
    dai = {**cfg, "ret_wins": (21, 63, 400)}
    assert sec.measure(_ser([(100.0, 120.0, 300)]), dai) is None
    # gia am/0 trong kho (du lieu ban) -> None chu khong chia cho 0
    xau = _ser([(100.0, 120.0, 300)])
    xau[-1] = xau[-1]._replace(c=0.0)
    assert sec.measure(xau, cfg) is None


# ───────────────────────── 3. dau cua "doi hang" ─────────────────────────
def test_hang_di_len_la_so_duong():
    """XLK tu hang 5 len hang 2 -> +3. Hang nho = manh hon."""
    c = _db(_ro(300))
    cu = [{"sym": s, "rank": i} for i, s in enumerate(cf.SECTOR_ETFS, 1)]
    moi = [{"sym": s, "rank": i} for i, s in enumerate(cf.SECTOR_ETFS, 1)]
    # cu: XLK=1 ... ; dung mot bang gia don gian roi hoan doi hai ma
    cu_map = {r["sym"]: r["rank"] for r in cu}
    moi_map = dict(cu_map)
    moi_map["XLK"], moi_map["XLV"] = cu_map["XLV"], cu_map["XLK"]
    assert cu_map["XLK"] < cu_map["XLV"], "fixture: XLK khoi dau cao hon"

    sec.save(c, "2024-03-01", [{"sym": s, "rank": r} for s, r in cu_map.items()])
    sec.save(c, "2024-03-04", [{"sym": s, "rank": r} for s, r in moi_map.items()])
    chg = sec.changes(c, "2024-03-04", {**cf.SECTORS, "change_wins": (1,)})

    di_xuong = cu_map["XLV"] - cu_map["XLK"]      # XLK tut xuong bang nay bac
    assert chg["XLK"][1] == -di_xuong, "tut hang -> so am"
    assert chg["XLV"][1] == +di_xuong, "len hang -> so duong"
    assert chg["XLF"][1] == 0, "khong doi -> 0, khong phai None"
    assert sum(v[1] for v in chg.values()) == 0, "tong doi hang ca ro = 0"
    c.close()


def test_chua_co_lich_su_thi_la_none_khong_phai_0():
    c = _db(_ro(300))
    assert not sec.build(c)["err"]
    chg = sec.changes(c)
    assert set(chg) == set(cf.SECTOR_ETFS)
    for s, v in chg.items():
        for w in cf.SECTORS["change_wins"]:
            assert v[w] is None, (s, w)
    assert sec.changes(c, d="1999-01-01") == {}
    c.close()


def test_mui_ten_phan_biet_ba_truong_hop():
    assert sec._arrow(None).strip() == "-", "chua biet"
    assert sec._arrow(0).strip() == "=", "khong doi"
    assert sec._arrow(4).startswith("^") and "4" in sec._arrow(4)
    assert sec._arrow(-4).startswith("v") and "4" in sec._arrow(-4)


# ───────────────────────── 4. diem tong ─────────────────────────
def test_diem_tong_la_trung_binh_ba_percentile():
    ro = _ro(300)
    ms = {s: sec.measure(v) for s, v in ro.items()}
    rows = sec.rank(ms)
    for r in rows:
        goc = (r["pct21"] + r["pct63"] + r["pct126"]) / 3.0
        assert abs(r["composite"] - goc) < 1e-9, r["sym"]
    # 11 ma, khong dong hang -> percentile chay tu 0 den 100 tung buoc 10
    assert rows[0]["composite"] == 100.0 and rows[-1]["composite"] == 0.0
    assert [r["rank"] for r in rows] == list(range(1, 12))
    assert all(rows[i]["composite"] >= rows[i + 1]["composite"]
               for i in range(10))


def test_percentile_la_so_sanh_cheo_khong_phai_diem_tuyet_doi():
    """Mot ma bung no khong duoc lam nhoe thu tu 10 ma con lai.

    Day la ly do chon trung binh percentile: neu dung trung binh loi nhuan thi
    XLE +300% se lam moi khoang cach khac thanh nhieu lam tron.
    """
    ro = _ro(300)
    goc = [r["sym"] for r in sec.rank({s: sec.measure(v)
                                       for s, v in ro.items()})]
    ro2 = dict(ro)
    ro2["XLE"] = _ser([(100.0, 2000.0, 300)])     # XLE tang 20 lan
    moi = [r["sym"] for r in sec.rank({s: sec.measure(v)
                                       for s, v in ro2.items()})]
    assert moi[0] == "XLE", "ma manh nhat phai len dau"
    # thu tu tuong doi cua nhung ma con lai khong doi
    assert [s for s in moi if s != "XLE"] == [s for s in goc if s != "XLE"]


def test_dong_diem_thi_thu_tu_on_dinh():
    """Thu tu phai khong phu thuoc thu tu dict dau vao.

    Neu khong, hom sau se bao mot cu "doi hang" khong he xay ra.
    """
    tie = {s: sec.measure(_ser([(100.0, 130.0, 300)]))
           for s in cf.SECTOR_ETFS}
    a = [r["sym"] for r in sec.rank(tie)]
    z = [r["sym"] for r in sec.rank(dict(reversed(list(tie.items()))))]
    assert a == z == sorted(cf.SECTOR_ETFS)
    assert all(r["composite"] == a and False for r in []) or True


def test_co_hieu_xu_huong_khong_vao_diem_tong():
    """above_sma50 / above_ema21 / slope_up la bo loc de doc, khong phai diem.

    Mot sector co the dung dau bang chi vi no giam it nhat ca ro - luc do ba co
    hieu nay la thu duy nhat noi cho ta biet.
    """
    xuong = {s: sec.measure(_ser([(200.0, 100.0 + i, 300)]))
             for i, s in enumerate(cf.SECTOR_ETFS)}
    rows = sec.rank(xuong)
    assert rows[0]["composite"] == 100.0, "van co ma hang 1"
    assert not rows[0]["above_sma50"] and not rows[0]["slope_up"], \
        "nhung hang 1 trong mot ro giam thi khong he o tren SMA50"


def test_rank_rong():
    assert sec.rank({}) == []


# ───────────────────────── canh bao phong thu ─────────────────────────
def test_canh_bao_khi_xlp_hoac_xlu_vao_top3():
    ro = {s: _ser([(100.0, 160.0 if s in cf.DEFENSIVE else 104.0, 300)])
          for s in cf.SECTOR_ETFS}
    rows = sec.rank({s: sec.measure(v) for s, v in ro.items()})
    assert sec.defensive_top(rows) == ["XLP", "XLU"]
    assert "CANH BAO" in sec.panel(rows, {}, sec.defensive_top(rows))


def test_khong_canh_bao_khi_top3_la_tang_truong():
    rows = sec.rank({s: sec.measure(v) for s, v in _ro(300).items()})
    assert [r["sym"] for r in rows[:3]] == ["XLK", "XLY", "XLF"]
    assert sec.defensive_top(rows) == []
    assert "CANH BAO" not in sec.panel(rows, {}, [])


def test_top_n_doc_tu_config():
    ro = {s: _ser([(100.0, 160.0 if s == "XLU" else 104.0 + i, 300)])
          for i, s in enumerate(cf.SECTOR_ETFS)}
    rows = sec.rank({s: sec.measure(v) for s, v in ro.items()})
    assert sec.defensive_top(rows, {**cf.SECTORS, "top_n": 1}) == ["XLU"]
    assert sec.defensive_top(rows, {**cf.SECTORS, "top_n": 0}) == []


# ───────────────────────── luu tru ─────────────────────────
def test_cols_khop_ddl():
    db = Path(tempfile.mkdtemp()) / "t.db"
    c = sec.con(db)
    have = {x[1] for x in c.execute("PRAGMA table_info(sector_rank)")}
    c.close()
    assert set(sec.COLS) | {"d", "sym", "updated"} == have


def test_upsert_11_dong_moi_phien():
    c = _db(_ro(300))
    assert not sec.build(c)["err"]
    assert not sec.build(c)["err"]
    assert len(sec.dates(c)) == 1, "chay lai khong duoc them phien"
    assert len(sec.load_rank(c)) == 11, "khoa (d,sym) phai chan trung dong"
    assert [r["rank"] for r in sec.load_rank(c)] == list(range(1, 12))
    c.close()


def test_dry_run_khong_ghi_gi():
    c = _db(_ro(300))
    assert sec.build(c, dry=True)["rows"]
    assert sec.dates(c) == [] and sec.load_rank(c) == []
    bf = sec.backfill(c, days=10, dry=True, quiet=True)
    assert bf["ok"] == 10 and sec.dates(c) == []
    c.close()


def test_lich_su_cho_bieu_do_dung_hinh():
    c = _db(_ro(300))
    sec.backfill(c, days=30, quiet=True)
    h = sec.history(c, 30)
    assert len(h) == 30 * 11
    assert set(h[0]) == {"d", "sym", "rank", "composite"}, \
        "bieu do chi can 4 cot - day 16 cot qua D1 la phi han muc ghi"
    assert [x["d"] for x in h] == sorted(x["d"] for x in h), "phai tang dan"
    # moi phien du 11 ma, hang 1..11 khong trung
    for d in sec.dates(c):
        ngay = [x for x in h if x["d"] == d]
        assert sorted(x["rank"] for x in ngay) == list(range(1, 12)), d
    assert sec.history(c, 5) and len(sec.history(c, 5)) == 5 * 11
    c.close()


def test_bang_rong_tra_ve_rong_chu_khong_loi():
    db = Path(tempfile.mkdtemp()) / "t.db"
    c = sec.con(db)
    assert sec.dates(c) == [] and sec.load_rank(c) == []
    assert sec.history(c, 30) == [] and sec.changes(c) == {}
    assert "khong co du lieu" in sec.panel([], {}, [])
    c.close()


def test_backfill_khong_co_ma_chuan_thi_noi_ro():
    c = _db(_ro(300), bench=False)
    bf = sec.backfill(c, days=10, quiet=True)
    assert bf["err"] and cf.BENCH in bf["err"] and "lich phien" in bf["err"]
    c.close()


# ───────────────────────── trinh bay ─────────────────────────
def test_panel_in_du_11_dong_va_danh_dau_top3():
    c = _db(_ro(300))
    sec.backfill(c, days=30, quiet=True)
    rows = sec.load_rank(c)
    txt = sec.panel(rows, sec.changes(c), [])
    assert "XEP HANG SECTOR" in txt and rows[0]["d"] in txt
    for s in cf.SECTOR_ETFS:
        assert s in txt, s
    danh_dau = [x for x in txt.splitlines() if x.lstrip().startswith(">")]
    assert len(danh_dau) == cf.SECTORS["top_n"], danh_dau
    assert "TOP 3: XLK XLY XLF" in txt
    c.close()


def test_config_snapshot_json_duoc():
    import json
    js = json.loads(json.dumps(cf.snapshot()))
    assert js["sectors"]["ret_wins"] == [21, 63, 126]
    assert js["sectors"]["top_n"] == 3
    assert len(js["sector_etfs"]) == 11


if __name__ == "__main__":
    _util.main(globals())
