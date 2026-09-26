"""watchlist.py - san chat luong + cong regime + doc danh sach cua hom nay.

Ba bat bien, xep theo do IM LANG cua loi neu no vo:

1. THIEU DU LIEU KHONG BAO GIO LA "DAT". Mot tieu chi khong do duoc phai roi vao
   `unsure` va di nguyen van vao tin nhan. Neu no bi lam tron thanh dau tich thi
   san $2B tro thanh mot cau trong config chu khong phai mot cai san - va khong
   co cach nao phat hien tu ben ngoai.
2. KHONG BIET TRANG THAI THI TRUONG -> DUNG NGOAI. gate() mac dinh "stop_only".
   Neu mac dinh la "full" thi mot Stage 1 chet se cho ra mot phien full size ma
   khong ai duoc bao.
3. KE HOACH HET HAN PHAI BI GOI TEN. Cron chet ba ngay truoc ma bot van canh
   dung dinh dang, ve mot phien khong con ton tai, la kieu loi te nhat.

Thuan stdlib: khong mang, khong pandas. refresh_mktcap() nhan tham so `fetch` lam
moi khau, nen ca duong LAY DUOC va duong THAT BAI deu duoc kiem ma khong ra mang
- va duong that bai la duong quan trong hon (xem bat bien 1).
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import _util

wl = _util.need("watchlist")
import config                                                    # noqa: E402
import regime                                                     # noqa: E402

G = config.INTRADAY

OK = {"px": 50.0, "adv50": 2_000_000, "exch": "NASDAQ", "mktcap": 50e9,
      "n_bars": 300, "spread_pct": 0.0005, "trigger": 51.0, "stop": 48.0}


# ───────────────────────── 1. san chat luong ─────────────────────────
def test_ma_dat_thi_khong_co_ly_do_nao():
    assert wl.check(OK, G, 300) == ([], [])


def test_tung_san_loai_rieng_biet():
    """Moi san mot ly do. Gop lai thanh "khong dat" thi khong con sua duoc gi."""
    cases = [
        ({"px": 5.0, "adv50": 20_000_000}, "gia_thap"),
        ({"adv50": 100}, "thanh_khoan"),
        ({"mktcap": 1e9}, "von_hoa"),
        ({"exch": "AMEX"}, "san_niem_yet"),
        ({"spread_pct": 0.01}, "spread_rong"),
        ({"trigger": None}, "khong_ke_hoach"),
        ({"stop": None}, "khong_ke_hoach"),
    ]
    for sua, want in cases:
        bad, _ = wl.check({**OK, **sua}, G, 300)
        assert bad == [want], f"{sua} -> {bad}, mong doi [{want}]"


def test_arca_duoc_nhan_vi_moi_etf_niem_yet_o_do():
    """Bo ARCA la bo SPY va 11 ma XL*, tuc la pha Stage 1-2."""
    assert "ARCA" in G["exchanges"]
    assert wl.check({**OK, "exch": "ARCA"}, G, 300)[0] == []
    assert wl.check({**OK, "exch": "arca"}, G, 300)[0] == [], "phai khong phan biet hoa/thuong"


def test_amex_bi_loai_vi_do_la_cho_ro_ri_that():
    """prep.fetch_universe() nhan AMEX (de con tra loi duoc "vi sao bi loai"),
    nen san o day la cho duy nhat chan no."""
    assert "AMEX" not in G["exchanges"]
    assert wl.check({**OK, "exch": "AMEX"}, G, 300)[0] == ["san_niem_yet"]


def test_thanh_khoan_do_bang_TIEN_khong_bang_so_co_phieu():
    """1 trieu co phieu/ngay la nhieu voi ma $200 va khong la gi voi ma $11."""
    assert wl.check({**OK, "px": 11.0, "adv50": 1_000_000}, G, 300)[0] == ["thanh_khoan"]
    assert wl.check({**OK, "px": 200.0, "adv50": 1_000_000}, G, 300)[0] == []


def test_khong_co_gia_thi_khong_doan():
    m = {k: v for k, v in OK.items() if k != "px"}
    assert wl.check(m, G, 300)[0] == ["gia_thap"]
    # ref_close (cot cua bang `candidates`) dung thay px duoc.
    assert wl.check({**m, "ref_close": 50.0}, G, 300)[0] == []


# ───────────────────────── 2. thieu != dat ─────────────────────────
def test_thieu_du_lieu_khong_bao_gio_la_dat():
    for truong, want in (("mktcap", "von_hoa"), ("exch", "san_niem_yet"),
                         ("spread_pct", "spread_rong")):
        bad, unsure = wl.check({**OK, truong: None}, G, 300)
        assert bad == [], f"{truong} thieu -> khong duoc LOAI"
        assert unsure == [want], f"{truong} thieu -> phai vao unsure, thay {unsure}"


def test_thieu_adv50_thi_LOAI_chu_khong_phai_khong_biet():
    """Ma den duoc day thi da qua LEAD (adv50 x gia > $20M). Thieu adv50 nghia la
    `struct` va `candidates` lech phien - luc do bo qua ma la dung."""
    assert wl.check({**OK, "adv50": None}, G, 300)[0] == ["thanh_khoan"]


def test_moi_ly_do_va_moi_muc_khong_biet_deu_co_cau_tieng_viet():
    for k in wl.WHY:
        assert wl.fmt_why([k]) != k, f"thieu cau cho `{k}`"
    for k in wl.UNSURE:
        assert wl.fmt_why([k], wl.UNSURE) != k, f"thieu cau cho `{k}`"
    assert "vốn hóa" in wl.fmt_why(["von_hoa"])


# ───────────────────────── 3. tuoi niem yet: ba nhanh ─────────────────────────
def test_tuoi_niem_yet_it_nen_ma_kho_sau_thi_that_su_moi():
    assert wl.check({**OK, "n_bars": 100}, G, ref_bars=300)[0] == ["moi_niem_yet"]


def test_tuoi_niem_yet_ca_kho_deu_nong_thi_khong_kiem_duoc():
    """Kho nen vua backfill -> MOI ma deu "moi", ke ca SPY. Loai sach danh sach
    o day la bien mot van de ha tang thanh mot phien im lang."""
    bad, unsure = wl.check({**OK, "n_bars": 100}, G, ref_bars=100)
    assert bad == [] and unsure == ["moi_niem_yet"]


def test_khong_co_moc_tham_chieu_thi_cung_khong_kiem_duoc():
    assert wl.check({**OK, "n_bars": 100}, G, None)[1] == ["moi_niem_yet"]


# ───────────────────────── 4. cong regime ─────────────────────────
def _db(d: str, trend: str | None = None, vol: str = "NORMAL") -> Path:
    p = Path(d) / "t.db"
    c = sqlite3.connect(p)
    c.executescript(regime.DDL)
    c.executescript("""
      CREATE TABLE candidates(
        sym TEXT, setup TEXT, d TEXT, ref_close REAL, pivot REAL, atr_pct REAL,
        sector TEXT, quality REAL, trigger REAL, stop REAL, target REAL,
        stop_pct REAL, risk_pct REAL, size_pct REAL, updated TEXT,
        PRIMARY KEY(sym, setup));
      CREATE TABLE struct(sym TEXT PRIMARY KEY, px REAL, adv50 REAL,
        n_bars INTEGER, atr14 REAL);
      CREATE TABLE base(sym TEXT PRIMARY KEY, exch TEXT, mktcap REAL);
      CREATE TABLE watch(sym TEXT, kind TEXT, ts TEXT, PRIMARY KEY(sym, kind));
    """)
    if trend:
        with c:
            c.execute("INSERT INTO regime(d,trend,vol,size,updated) "
                      "VALUES('2026-09-24',?,?,1.0,'x')", (trend, vol))
    c.close()
    return p


def test_khong_co_regime_thi_dung_ngoai():
    """Mac dinh phai la dung ngoai, khong phai full size."""
    with tempfile.TemporaryDirectory() as d:
        g = wl.gate(_db(d))
        assert g["mode"] == "stop_only"
        assert g["allow_new"] is False
        assert g["size"] == 0.0
        assert "chưa có" in g["why"]


def test_db_khong_mo_duoc_thi_cung_dung_ngoai():
    g = wl.gate(Path(tempfile.gettempdir()) / "khong-he-ton-tai-xyz.db")
    assert g["mode"] == "stop_only" and g["allow_new"] is False


def test_cap_trend_vol_la_vo_nghia_thi_dung_ngoai():
    with tempfile.TemporaryDirectory() as d:
        g = wl.gate(_db(d, "KHONG_CO_TRANG_THAI_NAY"))
        assert g["allow_new"] is False and g["mode"] == "stop_only"


def test_bon_che_do_khop_voi_playbook():
    want = {
        ("UPTREND", "NORMAL"): "full",
        ("UPTREND", "EXPANDED"): "full",          # nua co, van vao moi
        ("UPTREND_UNDER_STRESS", "NORMAL"): "manage",
        ("RANGE", "NORMAL"): "revert",
        ("RANGE", "EXPANDED"): "manage",          # size 0 -> khong vao moi
        ("DOWNTREND", "NORMAL"): "stop_only",
        ("DOWNTREND", "CONTRACTED"): "stop_only",
    }
    with tempfile.TemporaryDirectory() as d:
        for (tr, vo), mode in want.items():
            p = Path(d) / f"{tr}_{vo}.db"
            c = sqlite3.connect(p)
            c.executescript(regime.DDL)
            with c:
                c.execute("INSERT INTO regime(d,trend,vol,size,updated) "
                          "VALUES('2026-09-24',?,?,1.0,'x')", (tr, vo))
            c.close()
            g = wl.gate(p)
            assert g["mode"] == mode, f"{tr}/{vo} -> {g['mode']}, mong doi {mode}"


def test_moi_o_cua_playbook_deu_ra_mot_che_do_hop_le():
    """12 o, khong o nao roi ra ngoai. Mot o thieu se thanh KeyError trong phien."""
    with tempfile.TemporaryDirectory() as d:
        for tr in config.TRENDS:
            for vo in config.VOLS:
                p = Path(d) / f"{tr}{vo}.db"
                c = sqlite3.connect(p)
                c.executescript(regime.DDL)
                with c:
                    c.execute("INSERT INTO regime(d,trend,vol,size,updated) "
                              "VALUES('2026-09-24',?,?,1.0,'x')", (tr, vo))
                c.close()
                g = wl.gate(p)
                assert g["mode"] in wl.MODE_VI, (tr, vo, g["mode"])
                # Khong bao gio duoc mo vi the moi khi playbook cho size 0.
                if config.PLAYBOOK[(tr, vo)]["size"] <= 0:
                    assert g["allow_new"] is False, f"{tr}/{vo} cho vao moi voi size 0"
                assert g["why"] and g["why"] != ""


def test_downtrend_khong_bao_gio_cho_vao_moi():
    with tempfile.TemporaryDirectory() as d:
        for vo in config.VOLS:
            p = Path(d) / f"dn{vo}.db"
            c = sqlite3.connect(p)
            c.executescript(regime.DDL)
            with c:
                c.execute("INSERT INTO regime(d,trend,vol,size,updated) "
                          "VALUES('2026-09-24','DOWNTREND',?,1.0,'x')", (vo,))
            c.close()
            g = wl.gate(p)
            assert g["mode"] == "stop_only" and g["allow_new"] is False
            assert g["allow_manage"] is True, "van phai canh stop cho vi the dang mo"


# ───────────────────────── 5. doc danh sach ─────────────────────────
def _add(p: Path, sym: str, setup: str = "LEAD", **kw) -> None:
    r = {"sym": sym, "setup": setup, "d": "2026-09-24", "ref_close": 50.0,
         "quality": 9.0, "sector": "XLK", "atr_pct": 0.03, "trigger": 51.0,
         "stop": 48.0, "target": 57.0, "stop_pct": 0.06, "risk_pct": 0.0075,
         "size_pct": 0.12, "updated": "x"}
    r.update({k: v for k, v in kw.items() if k in r})
    st = {"sym": sym, "px": kw.get("px", 50.0), "adv50": kw.get("adv50", 2e6),
          "n_bars": kw.get("n_bars", 300), "atr14": 1.5}
    ba = {"sym": sym, "exch": kw.get("exch", "NASDAQ"),
          "mktcap": kw.get("mktcap", 50e9)}
    c = sqlite3.connect(p)
    with c:
        c.execute(f"INSERT INTO candidates({','.join(r)}) VALUES"
                  f"({','.join('?' * len(r))})", tuple(r.values()))
        c.execute(f"INSERT OR REPLACE INTO struct({','.join(st)}) VALUES"
                  f"({','.join('?' * len(st))})", tuple(st.values()))
        c.execute(f"INSERT OR REPLACE INTO base({','.join(ba)}) VALUES"
                  f"({','.join('?' * len(ba))})", tuple(ba.values()))
    c.close()


def test_load_chi_lay_LEAD():
    with tempfile.TemporaryDirectory() as d:
        p = _db(d, "UPTREND")
        _add(p, "AAA")
        _add(p, "BBB", setup="BO")
        w = wl.load(p)
        assert [r["sym"] for r in w["rows"]] == ["AAA"]


def test_load_ep_san_va_noi_ly_do():
    with tempfile.TemporaryDirectory() as d:
        p = _db(d, "UPTREND")
        _add(p, "TOT")
        _add(p, "RE", px=4.0, adv50=1e5)
        _add(p, "NHO", mktcap=5e8)
        w = wl.load(p)
        assert [r["sym"] for r in w["rows"]] == ["TOT"]
        drop = dict(w["drop"])
        assert "gia_thap" in drop["RE"] and "thanh_khoan" in drop["RE"]
        assert drop["NHO"] == ["von_hoa"]


def test_load_khong_co_bang_candidates_thi_noi_ro():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "tron.db"
        sqlite3.connect(p).close()
        w = wl.load(p)
        assert w["rows"] == [] and "chưa tồn tại" in w["note"]


def test_load_danh_sach_rong_la_mot_ket_qua_co_cau_giai_thich():
    with tempfile.TemporaryDirectory() as d:
        w = wl.load(_db(d, "UPTREND"))
        assert w["rows"] == [] and w["note"], "rong ma khong noi gi la im lang"


def test_load_thieu_cot_mktcap_thi_la_khong_biet_chu_khong_vo():
    """DB cu chua co cot `mktcap`: van phai ra danh sach, kem ghi chu."""
    with tempfile.TemporaryDirectory() as d:
        p = _db(d, "UPTREND")
        _add(p, "AAA")
        c = sqlite3.connect(p)
        with c:
            c.execute("ALTER TABLE base RENAME TO base_cu")
            c.execute("CREATE TABLE base(sym TEXT PRIMARY KEY, exch TEXT)")
            c.execute("INSERT INTO base(sym,exch) VALUES('AAA','NASDAQ')")
        c.close()
        w = wl.load(p)
        assert [r["sym"] for r in w["rows"]] == ["AAA"]
        assert "von_hoa" in w["rows"][0]["unsure"]


def test_nen_quyet_dinh_cuoi_tuan_khong_bi_goi_la_het_han():
    """Sang thu Hai, nen cua thu Sau la BINH THUONG. Mot bao dong bao moi thu
    Hai la mot bao dong bi tat."""
    with tempfile.TemporaryDirectory() as d:
        p = _db(d, "UPTREND")
        _add(p, "AAA", d="2026-09-25")            # thu Sau
        w = wl.load(p, day="2026-09-28")          # thu Hai
        assert w["stale"] is False, w["note"]


def test_nen_quyet_dinh_qua_cu_thi_bi_goi_ten():
    with tempfile.TemporaryDirectory() as d:
        p = _db(d, "UPTREND")
        _add(p, "AAA", d="2026-09-21")            # thu Hai
        w = wl.load(p, day="2026-09-28")          # thu Hai tuan sau
        assert w["stale"] is True
        assert "ngày làm việc" in w["note"]


def test_biz_days_khong_dem_cuoi_tuan():
    assert wl._biz_days("2026-09-25", "2026-09-28") == 1     # Sau -> Hai
    assert wl._biz_days("2026-09-21", "2026-09-28") == 5
    assert wl._biz_days("2026-09-28", "2026-09-28") == 0
    assert wl._biz_days("khong-phai-ngay", "2026-09-28") is None


# ───────────────────────── 6. them tay ─────────────────────────
def test_ma_them_tay_van_phai_qua_san():
    """"Them tay" khong co nghia la "mien san": san la san."""
    with tempfile.TemporaryDirectory() as d:
        p = _db(d, "UPTREND")
        _add(p, "RAC", setup="BO", px=3.0, adv50=1e5, mktcap=1e8)
        c = sqlite3.connect(p)
        with c:
            c.execute("INSERT INTO watch(sym,kind,ts) VALUES('RAC','manual','x')")
        c.close()
        w = wl.load(p)
        assert w["rows"] == []
        assert dict(w["drop"])["RAC"], "phai co ly do bi loai, khong im lang"


def test_ma_them_tay_duoc_danh_dau():
    with tempfile.TemporaryDirectory() as d:
        p = _db(d, "UPTREND")
        _add(p, "TAY", setup="BO")
        c = sqlite3.connect(p)
        with c:
            c.execute("INSERT INTO watch(sym,kind,ts) VALUES('TAY','manual','x')")
        c.close()
        w = wl.load(p)
        assert [r["sym"] for r in w["rows"]] == ["TAY"]
        assert w["rows"][0].get("_manual") is True
        assert w["manual"] == 1


def test_ma_them_tay_da_co_trong_danh_sach_thi_khong_thanh_hai_dong():
    """Hai dong cung mot ma = hai alert cho cung mot su viec."""
    with tempfile.TemporaryDirectory() as d:
        p = _db(d, "UPTREND")
        _add(p, "AAA")
        c = sqlite3.connect(p)
        with c:
            c.execute("INSERT INTO watch(sym,kind,ts) VALUES('AAA','manual','x')")
        c.close()
        w = wl.load(p)
        assert [r["sym"] for r in w["rows"]] == ["AAA"]


def test_chi_nhan_kind_manual():
    """Bang `watch` cung giu `kind='track'` cua phan cu. Nhan lan la mo lai
    dung cai cua ma scanner cu di vao."""
    with tempfile.TemporaryDirectory() as d:
        p = _db(d, "UPTREND")
        _add(p, "CU", setup="BO")
        c = sqlite3.connect(p)
        with c:
            c.execute("INSERT INTO watch(sym,kind,ts) VALUES('CU','track','x')")
        c.close()
        assert wl.load(p)["rows"] == []


def test_khong_co_bang_watch_thi_khong_vo():
    with tempfile.TemporaryDirectory() as d:
        p = _db(d, "UPTREND")
        _add(p, "AAA")
        c = sqlite3.connect(p)
        with c:
            c.execute("DROP TABLE watch")
        c.close()
        assert [r["sym"] for r in wl.load(p)["rows"]] == ["AAA"]


# ───────────────────────── 7. von hoa ─────────────────────────
def test_refresh_mktcap_that_bai_thi_KHONG_dong_dau_la_da_kiem():
    """Ghi mktcap_ts khi that bai la bien "khong lay duoc" thanh "da kiem roi",
    va TTL 7 ngay se giu cai sai do mot tuan - im lang, vi ma chi bi loai khoi
    danh sach chu khong bao loi o dau ca.

    `fetch` duoc tiem vao: tren may DA cai yfinance, khong tiem thi chinh test
    nay di ra mang that (va cho 3 giay cho mot ma khong ton tai).
    """
    with tempfile.TemporaryDirectory() as d:
        p = _db(d, "UPTREND")
        _add(p, "AAA")
        r = wl.refresh_mktcap(p, ["AAA"], fetch=lambda s: None)
        c = sqlite3.connect(p)
        cols = {x[1] for x in c.execute("PRAGMA table_info(base)")}
        row = c.execute("SELECT mktcap, mktcap_ts FROM base WHERE sym='AAA'"
                        ).fetchone()
        c.close()
        assert {"mktcap", "mktcap_ts"} <= cols, "phai tu them cot"
        assert r["asked"] == 1 and r["ok"] == 0 and r["fail"] == 1, r
        # Khong ghi 0 va khong xoa so cu: mot lan hong khong duoc lam mat con so
        # da biet (ghi 0 se loai ma vinh vien vi san $2B).
        assert row[0] == 50e9, row
        assert row[1] is None, "that bai khong duoc dong dau la da kiem"


def test_refresh_mktcap_nguon_nem_thi_cung_chi_la_that_bai():
    """Mot ma sai chinh ta / vua bi go niem yet lam yfinance nem. Ca vong phai di
    tiep: chin ma con lai khong duoc mat von hoa vi ma thu muoi."""
    def no(s):
        raise RuntimeError("404")

    with tempfile.TemporaryDirectory() as d:
        p = _db(d, "UPTREND")
        _add(p, "AAA")
        r = wl.refresh_mktcap(p, ["AAA"], fetch=no)
        assert r["fail"] == 1 and r["ok"] == 0 and not r["err"], r


def test_refresh_mktcap_lay_duoc_thi_ghi_va_lan_sau_dung_cache():
    with tempfile.TemporaryDirectory() as d:
        p = _db(d, "UPTREND")
        _add(p, "AAA")
        r = wl.refresh_mktcap(p, ["AAA"], fetch=lambda s: 3.4e9)
        assert r["ok"] == 1 and r["fail"] == 0, r
        c = sqlite3.connect(p)
        row = c.execute("SELECT mktcap, mktcap_ts FROM base WHERE sym='AAA'"
                        ).fetchone()
        c.close()
        assert row[0] == 3.4e9 and row[1], row
        # Lan hai: con han -> khong hoi lai. `1/0` de neu no hoi thi test do,
        # chu khong am tham hoi lai moi dem.
        r2 = wl.refresh_mktcap(p, ["AAA"], fetch=lambda s: 1 / 0)
        assert r2["cached"] == 1 and r2["asked"] == 0, r2


def test_refresh_mktcap_bo_qua_ma_con_han():
    with tempfile.TemporaryDirectory() as d:
        p = _db(d, "UPTREND")
        _add(p, "AAA")
        c = sqlite3.connect(p)
        with c:
            c.execute("ALTER TABLE base ADD COLUMN mktcap_ts TEXT")
            c.execute("UPDATE base SET mktcap=9e9, mktcap_ts=? WHERE sym='AAA'",
                      ("2999-01-01T00:00:00+00:00",))
        c.close()
        r = wl.refresh_mktcap(p, ["AAA"])
        assert r["cached"] == 1 and r["asked"] == 0 and not r["err"]


def test_refresh_mktcap_nhan_Connection_nhu_nightly_dua_vao():
    """Loi that da xay ra: `mktcap LOI TypeError: expected str, bytes or
    os.PathLike object, not Connection`.

    nightly.py mo MOT ket noi cho ca chuoi chay dem roi dua chinh no vao tung
    buoc (xem `do(name, fatal, fn, c, dry, lg)`), nen `sqlite3.connect(db)` tran
    o day nem TypeError va buoc mktcap chet - im lang o cho te nhat: mktcap la
    buoc KHONG bat buoc, nen chuoi van "gan nhu chay xong" va cac ma di vao phien
    kem ghi chu "chưa biết vốn hóa" thay vi kem von hoa that.

    Kiem CA BA dieu, vi hai dieu sau la nhung cach hong nang hon chinh TypeError:
      - khong dong ket noi cua nguoi khac (buoc push/telegram doc tiep tren no),
      - khong de lai transaction ghi MO (chinh la goc cua `database is locked`),
      - khong doi row_factory cua nguoi khac.
    """
    with tempfile.TemporaryDirectory() as d:
        p = _db(d, "UPTREND")
        _add(p, "AAA")
        c = sqlite3.connect(p, timeout=5)
        try:
            r = wl.refresh_mktcap(c, ["AAA"], fetch=lambda s: 7.5e9)
            assert r["ok"] == 1 and r["fail"] == 0, r
            assert not c.in_transaction, "de lai transaction ghi mo tren ket noi dung chung"
            assert c.row_factory is None, "doi row_factory cua ket noi nguoi khac"
            # Ket noi phai con song: day la dieu buoc push/telegram phu thuoc vao.
            row = c.execute("SELECT mktcap FROM base WHERE sym='AAA'").fetchone()
            assert row[0] == 7.5e9, row
        finally:
            c.close()


def test_refresh_mktcap_danh_sach_rong_khong_lam_gi():
    with tempfile.TemporaryDirectory() as d:
        r = wl.refresh_mktcap(_db(d, "UPTREND"))
        assert r["asked"] == 0 and r["ok"] == 0


if __name__ == "__main__":
    _util.main(globals())
