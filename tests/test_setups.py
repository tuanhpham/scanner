"""setups.py - hai setup theo nen ngay.

Bon thu phai dung, xep theo muc do "sai thi khong ai phat hien ra":

1. RV KHONG BAO GIO kich hoat khi thieu so lieu co ban. "Chua biet" bi doi
   thanh "tot" la cach nhanh nhat de bot alert dung nhung ma dang pha loang.
2. build() phai GHI DE ca bang. Ma khong con nen ma con nam lai trong
   `candidates` thi bot canh mot pivot da vo tu lau.
3. `candidates` qua han phai tra ve rong. Cron chet thi khong co gi bao loi -
   chi co pivot cu dung yen, va alert van gui nhu that.
4. Nguong nam trong dict BO/RV, khong rai trong ham: backtest.py doi mot cho
   thi ca hai buoc (sinh danh sach + kich hoat) phai doi theo.
"""
from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path

import _util

se, st = _util.need("setups", "structure")


def base_row(**kw) -> dict:
    return se._base_row(**kw)


def dip_row(**kw) -> dict:
    return se._dip_row(**kw)


BO_Q = {"px": 21.0, "vol": 2_000_000, "rvol": 2.5, "hi": 21.1, "lo": 20.2,
        "open": 20.3}
RV_Q = {"px": 6.7, "vol": 5_000_000, "rvol": 4.0, "hi": 6.75, "lo": 6.05,
        "open": 6.1}


def _db(rows: dict[str, dict]) -> Path:
    """DB tam co bang `struct` dung san."""
    db = Path(tempfile.mkdtemp()) / "t.db"
    c = st.con(db)
    now = "2024-06-03T20:00:00+00:00"
    ins = (f"INSERT INTO struct(sym,{','.join(st.COLS)},updated) "
           f"VALUES({','.join('?' * (len(st.COLS) + 2))})")
    for sym, r in rows.items():
        c.execute(ins, (sym, *(r[k] for k in st.COLS), now))
    c.commit()
    c.close()
    return db


# ───────────────────── 1. co ban thieu = loai, khong phai "tot" ─────────────
def test_rv_khong_kich_hoat_khi_thieu_co_ban():
    c = se.rv_candidate(dip_row())
    assert c and c["fund_ok"] is None, "chua co Phase 9.3 -> phai la None"
    assert se.trig_rv(c, RV_Q) is None, "tang 11% volume 4x van khong duoc alert"
    # co so lieu va dat thi moi kich hoat
    ok = se.rv_candidate(dip_row(), fund={"ok": True, "score": 0.7})
    assert se.trig_rv(ok, RV_Q)


def test_rv_co_ban_khong_dat_thi_khong_vao_danh_sach():
    assert se.rv_candidate(dip_row(), fund={"ok": False}) is None


def test_rv_diem_co_ban_chiem_phan_lon_xep_hang():
    a = se.rv_candidate(dip_row(), fund={"ok": True, "score": 0.9})
    b = se.rv_candidate(dip_row(), fund={"ok": True, "score": 0.1})
    assert a["quality"] > b["quality"] + 0.2


# ───────────────────── 2. + 3. bang candidates ─────────────────────
def test_build_ghi_de_ma_het_nen_thi_bien_mat():
    db = _db({"AAA": base_row(), "DIP": dip_row()})
    r = se.build(db)
    assert r["BO"] == 1 and r["RV"] == 1
    assert set(se.load_candidates(db, today="2024-06-04")) == {"AAA", "DIP"}

    c = st.con(db)
    c.execute("UPDATE struct SET base_len=0, pivot=NULL, dist_pivot=NULL "
              "WHERE sym='AAA'")
    c.commit()
    se.build(c)
    got = se.load_candidates(c, today="2024-06-04")
    assert "AAA" not in got, "pivot cu con nam lai trong danh sach theo doi"
    assert set(got) == {"DIP"}
    n = c.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    assert n == 1, "build() phai ghi de ca bang, khong duoc chen them"
    c.close()


def test_candidates_qua_han_tra_ve_rong():
    db = _db({"AAA": base_row()})       # struct cua ngay 2024-06-03
    se.build(db)
    assert se.load_candidates(db, today="2024-06-05")
    assert se.load_candidates(db, today="2024-07-01") == {}, \
        "cron chet 4 tuan ma bot van canh pivot cu"
    assert se.load_candidates(db, today="2024-07-01", max_age=None)


def test_dry_run_tinh_het_nhung_khong_ghi_gi():
    db = _db({"AAA": base_row(), "DIP": dip_row()})
    r = se.build(db, dry=True)
    assert r["BO"] == 1 and r["RV"] == 1 and len(r["rows"]) == 2
    assert se.load_candidates(db, today="2024-06-04") == {}
    # va khong duoc xoa danh sach da co: dry-run phai vo hai ca hai chieu
    se.build(db)
    assert len(se.load_candidates(db, today="2024-06-04")) == 2
    se.build(db, dry=True)
    assert len(se.load_candidates(db, today="2024-06-04")) == 2


def test_load_candidates_loc_theo_setup():
    db = _db({"AAA": base_row(), "DIP": dip_row()})
    se.build(db)
    assert set(se.load_candidates(db, "BO", today="2024-06-04")) == {"AAA"}
    assert set(se.load_candidates(db, "RV", today="2024-06-04")) == {"DIP"}


LEAD_ONLY = {"sector", "rs21", "rs63"}
# Cot ke hoach lenh: plan.make() gan trong scan(), SAU khi da ap tran - nen mot
# dong vua ra khoi *_candidate() chua co chung. Xem
# test_scan_dien_du_moi_cot_ke_ca_cot_ke_hoach.
PLAN_COLS = {"trigger", "stop", "target", "stop_pct", "risk_pct", "size_pct"}


def test_cols_khop_ddl():
    c = se.con(Path(tempfile.mkdtemp()) / "t.db")
    have = {r[1] for r in c.execute("PRAGMA table_info(candidates)")}
    assert set(se.COLS) | {"sym", "updated"} == have
    c.close()

    # LEAD phai dien DU moi cot NGOAI cot ke hoach: ke hoach do scan() gan sau
    # khi da ap tran (plan.make() chi tinh cho nhung ma thuc su vao danh sach).
    ld = se.lead_candidate(se._lead_row(), sector="XLK")
    assert set(se.COLS) - set(ld) == PLAN_COLS, set(se.COLS) - set(ld)

    # BO/RV chi thieu dung ba cot cua rieng LEAD (build() ghi NULL) + cot ke
    # hoach. Assert theo CA HAI chieu: neu mai sau them cot cho BO ma quen
    # dien, test phai do - chu khong phai lang le ghi NULL vao bang.
    for cand in (se.bo_candidate(base_row()),
                 se.rv_candidate(dip_row(), fund={"ok": True, "score": 0.5})):
        assert set(se.COLS) - set(cand) == LEAD_ONLY | PLAN_COLS, cand["setup"]
        assert set(cand) - set(se.COLS) == {"sym"}, cand["setup"]


def test_scan_dien_du_moi_cot_ke_ca_cot_ke_hoach():
    """Dong RA KHOI scan() phai day du - do la dong duoc ghi vao DB.

    Invariant nay manh hon "tung ham candidate dien du cot": ke hoach lenh duoc
    gan trong scan() chu khong trong lead_candidate(), nen cho duy nhat kiem tra
    duoc "khong co cot nao lang le thanh NULL" la o day.
    """
    struct = {"AAA": se._lead_row(), "BBB": base_row(),
              "CCC": dip_row()}
    rows, _ = se.scan(struct, fund={"CCC": {"ok": True, "score": 0.5}},
                      lead_sectors={"AAA": "XLK"})
    assert rows, "fixture phai sinh ra it nhat mot dong"
    for r in rows:
        thieu = set(se.COLS) - set(r)
        if r["setup"] == "LEAD":
            assert thieu == set(), f"LEAD thieu cot {thieu}"
        else:
            # BO/RV van thieu ba cot cua rieng LEAD, nhung KE HOACH thi phai co:
            # phan intraday canh stop cho ca BO/RV, khong chi cho LEAD.
            assert thieu == LEAD_ONLY, f"{r['setup']} thieu cot {thieu}"
        assert r["trigger"] and r["stop"], f"{r['sym']}/{r['setup']}: thieu ke hoach"
        assert r["stop"] < r["trigger"], "stop phai duoi diem vao"


def test_size_mult_0_thi_moi_dong_co_size_pct_0():
    # UPTREND_UNDER_STRESS / DOWNTREND -> playbook size 0. Ke hoach van phai co
    # (canh stop can no), nhung co vi the phai la 0 o MOI dong. Mot dong sot lai
    # size > 0 la mot dong tin nhan buoi sang noi "vao lenh" trong ngay dang le
    # phai dung ngoai.
    struct = {"AAA": se._lead_row(), "BBB": base_row()}
    rows, _ = se.scan(struct, lead_sectors={"AAA": "XLK"}, size_mult=0.0)
    assert rows
    assert all(r["size_pct"] == 0.0 for r in rows), [
        (r["sym"], r["size_pct"]) for r in rows]
    # Nhung trigger/stop van phai co.
    assert all(r["trigger"] and r["stop"] for r in rows)


def test_migrate_them_cot_thi_tinh_lai_khong_phai_bao_loi():
    """Bang `candidates` cu (thieu cot LEAD) -> DROP va tao lai, khong crash.

    An toan vi build() ghi de ca bang moi lan chay. Tu dong chu khong phai mot
    buoc trong README, vi buoc trong README se bi quen dung mot lan: luc 08:00
    tren VM.
    """
    db = Path(tempfile.mkdtemp()) / "t.db"
    c = se.con(db)
    c.executescript("DROP TABLE candidates;"
                    "CREATE TABLE candidates(sym TEXT, setup TEXT, d TEXT,"
                    " quality REAL, updated TEXT, PRIMARY KEY(sym,setup));")
    c.execute("INSERT INTO candidates VALUES('CU','BO','2020-01-01',0.5,'x')")
    c.commit()
    assert se._migrate(c) is True
    have = {r[1] for r in c.execute("PRAGMA table_info(candidates)")}
    assert set(se.COLS) | {"sym", "updated"} == have
    assert se._migrate(c) is False, "chay lai khong duoc xoa nua"
    c.close()


def test_tran_max_cand():
    rows = {f"S{i:03d}": base_row(base_depth=0.05 + i * 0.001)
            for i in range(30)}
    _, rej = se.scan(rows, max_cand=10)
    assert rej["BO"]["_qua_loc"] == 30 and rej["BO"]["_bi_cat_tran"] == 20
    kept, _ = se.scan(rows, max_cand=10)
    assert len(kept) == 10
    # bi cat la nhung ma diem thap nhat, khong phai theo thu tu alphabet
    q = [k["quality"] for k in kept]
    assert q == sorted(q, reverse=True)


# ───────────────────── 4. nguong tap trung mot cho ─────────────────
def test_doi_nguong_trong_dict_thi_ca_hai_buoc_doi_theo():
    g = {**se.BO, "min_base_len": 60}
    assert se.bo_candidate(base_row(), g) is None
    assert se.bo_candidate(base_row())          # dict goc khong bi thay doi
    g2 = {**se.BO, "rvol": 5.0}
    c = se.bo_candidate(base_row())
    assert se.trig_bo(c, BO_Q) and se.trig_bo(c, BO_Q, g2) is None


# ───────────────────── BO: sinh danh sach ─────────────────────
def test_bo_can_nen_that():
    assert se.bo_candidate(base_row())
    assert se.bo_candidate(base_row(base_len=15)) is None
    assert se.bo_candidate(base_row(base_depth=0.28)) is None
    assert se.bo_candidate(base_row(atr_contract=0.95)) is None
    assert se.bo_candidate(dip_row()) is None


def test_bo_nen_rong_hon_cho_ma_gia_thap():
    """Nen 28% o ma $20 la lung nhung o ma $8 la binh thuong."""
    assert se.bo_candidate(base_row(base_depth=0.28)) is None
    assert se.bo_candidate(base_row(px=8.0, sma20=7.9, sma50=7.6, hi52=8.3,
                                    pivot=8.2, base_depth=0.28))


def test_bo_phai_o_gan_dinh_va_tren_sma50():
    assert se.bo_candidate(base_row(off_high=0.40)) is None
    assert se.bo_candidate(base_row(px=18.0, sma50=19.0)) is None
    assert se.bo_candidate(base_row(sma50_slope=-0.001)) is None


def test_bo_chi_theo_doi_phan_tren_cua_nen():
    assert se.bo_candidate(base_row(dist_pivot=0.30)) is None, "hom nay khong the vuot"
    assert se.bo_candidate(base_row(dist_pivot=-0.10)) is None, "da vao muon"
    assert se.bo_candidate(base_row(dist_pivot=-0.01)), "vua vuot: van theo doi"


# ───────────────────── BO: kich hoat ─────────────────────
def test_bo_cham_pivot_chua_phai_vuot():
    c = se.bo_candidate(base_row())          # pivot 20.6
    assert se.trig_bo(c, {**BO_Q, "px": 20.55}) is None
    assert se.trig_bo(c, {**BO_Q, "px": 20.75})


def test_bo_khong_alert_khi_da_chay_qua_xa():
    """+19% tren pivot la gap-and-go: viec cua SPIKE, khong phai BO."""
    c = se.bo_candidate(base_row())
    assert se.trig_bo(c, {**BO_Q, "px": 24.5, "hi": 24.6, "lo": 20.5}) is None


def test_bo_can_rvol_va_thanh_khoan():
    c = se.bo_candidate(base_row())
    assert se.trig_bo(c, {**BO_Q, "rvol": 1.2}) is None
    assert se.trig_bo(c, {**BO_Q, "vol": 1000}) is None


def test_bo_khong_alert_khi_dang_o_nua_duoi_bien_do():
    c = se.bo_candidate(base_row())
    assert se.trig_bo(c, {**BO_Q, "px": 20.75, "hi": 22.5, "lo": 20.5}) is None


def test_bo_gap_to_thi_canh_bao_chu_khong_loai():
    c = se.bo_candidate(base_row())
    t = se.trig_bo(c, {**BO_Q, "open": 22.5})
    assert t and t["warn"], "gap 12%: van la breakout, nhung entry xau"
    assert not se.trig_bo(c, BO_Q)["warn"]


# ───────────────────── RV ─────────────────────
def test_rv_can_roi_sau_va_day_con_moi():
    assert se.rv_candidate(dip_row())
    assert se.rv_candidate(base_row()) is None
    assert se.rv_candidate(dip_row(off_high=0.30)) is None
    assert se.rv_candidate(dip_row(days_since_low=60)) is None
    assert se.rv_candidate(dip_row(ret63=0.05)) is None


def test_rv_khong_vao_muon():
    assert se.rv_candidate(dip_row(up_from_low=0.60)) is None


def test_rv_loc_gia_va_thanh_khoan_chat_hon_bo():
    """RV siet hon BO: roi 70% ma thanh khoan mong thi khong ban ra duoc."""
    assert se.rv_candidate(dip_row(px=2.0)) is None
    assert se.rv_candidate(dip_row(adv20=300_000.0)) is None
    assert se.bo_candidate(base_row(adv20=300_000.0)), "BO thi 300k van du"


def test_rv_phai_lay_lai_sma20():
    c = se.rv_candidate(dip_row(sma20=7.5), fund={"ok": True})
    assert se.trig_rv(c, RV_Q) is None
    c2 = se.rv_candidate(dip_row(sma20=6.4), fund={"ok": True})
    assert se.trig_rv(c2, RV_Q)


def test_rv_phai_dong_o_vung_dinh_ngay():
    c = se.rv_candidate(dip_row(), fund={"ok": True})
    assert se.trig_rv(c, {**RV_Q, "px": 6.5, "hi": 7.0, "lo": 6.4}) is None
    assert se.trig_rv(c, {**RV_Q, "rvol": 2.0}) is None


# ───────────────────── LEAD (Stage 3) ─────────────────────
def lead_row(**kw) -> dict:
    return se._lead_row(**kw)


def test_lead_san_tinh_bang_TIEN_khong_bang_phan_tram():
    """Day la ly do Stage 3 ton tai. Ba ma cung tang manh, chi mot duoc nhan.

    Scanner trong phien hien tai xep hang theo % tang ngay, nen no luon tra ve
    hai ma dau. Dolar volume la thu duy nhat phan biet duoc chung.
    """
    rac_re = lead_row(px=4.0, adv50=5_000_000.0)          # 5M x $4  = $20M... nhung gia < $10
    rac_mong = lead_row(px=25.0, adv50=400_000.0)         # 400k x $25 = $10M
    that = lead_row(px=25.0, adv50=3_000_000.0)           # $75M
    assert se.lead_candidate(rac_re, sector="XLK") is None
    assert se.lead_candidate(rac_mong, sector="XLK") is None
    assert se.lead_candidate(that, sector="XLK")
    # va san la san: ha nguong xuong thi rac di qua ngay. Test nay la de khi ai
    # do dinh "noi long mot chut" thi thay ro minh dang mo lai cai cua nao.
    long_le = {**se.LEAD, "min_px": 1.0, "min_dollar_vol": 1_000_000.0}
    assert se.lead_candidate(rac_re, long_le, sector="XLK")
    assert se.lead_candidate(rac_mong, long_le, sector="XLK")


def test_lead_khong_biet_sector_thi_loai_chu_khong_doan():
    assert se.lead_candidate(lead_row()) is None
    assert se.lead_candidate(lead_row(), sector=None) is None
    assert se.lead_candidate(lead_row(), sector="") is None
    assert se.lead_candidate(lead_row(), sector="XLK")


def test_lead_can_manh_hon_spy_o_ca_hai_cua_so():
    """Mot cua so co the la may. Hai cua so cung duong thi kho la may hon."""
    assert se.lead_candidate(lead_row(rs21=0.05, rs63=0.11), sector="XLK")
    assert se.lead_candidate(lead_row(rs21=-0.01), sector="XLK") is None
    assert se.lead_candidate(lead_row(rs63=-0.01), sector="XLK") is None


def test_lead_thieu_rs_thi_loai_khong_coi_nhu_0():
    """structure.build() de rs21/rs63 = NULL khi kho nen khong co SPY.

    Luc do phai KHONG CO ma nao trong danh sach, chu khong phai ca universe deu
    "khong yeu hon SPY" va di qua het.
    """
    ly_do: dict = {}
    assert se.lead_candidate(lead_row(rs21=None, rs63=None), sector="XLK",
                             rej=ly_do) is None
    assert any("RS" in k for k in ly_do), ly_do
    rows, rej = se.scan({f"S{i}": lead_row(rs21=None, rs63=None)
                         for i in range(20)},
                        lead_sectors={f"S{i}": "XLK" for i in range(20)})
    assert [r for r in rows if r["setup"] == "LEAD"] == []
    assert rej["LEAD"]["_qua_san"] == 0


def test_lead_bien_do_phai_nam_trong_khoang():
    """Duoi 2% khong du dong de kiem tien; tren 6% stop rong den vo nghia."""
    assert se.lead_candidate(lead_row(atr_pct=0.03), sector="XLK")
    assert se.lead_candidate(lead_row(atr_pct=0.019), sector="XLK") is None
    assert se.lead_candidate(lead_row(atr_pct=0.061), sector="XLK") is None
    assert se.lead_candidate(lead_row(atr_pct=None), sector="XLK") is None


def test_lead_phai_o_gan_dinh_52_tuan():
    assert se.lead_candidate(lead_row(off_high=0.14), sector="XLK")
    assert se.lead_candidate(lead_row(off_high=0.16), sector="XLK") is None
    assert se.lead_candidate(lead_row(off_high=None), sector="XLK") is None


def test_lead_tran_moi_sector_chan_duoc_mot_sector_chiem_het():
    """10 ma tong nhung 5 moi sector: neu chi cat tong thi XLK an het 10 cho.

    Luc do "dan dat o top 3 sector" tren tin nhan la mot cau khong dung.
    """
    ds = [se.lead_candidate(lead_row(sym=f"K{i}", rs63=0.20 - i * 0.001),
                            sector="XLK") for i in range(12)]
    ds += [se.lead_candidate(lead_row(sym=f"F{i}", rs63=0.10), sector="XLF")
           for i in range(4)]
    ly_do: dict = {}
    giu = se.lead_pick(ds, rej=ly_do)
    dem: dict[str, int] = {}
    for x in giu:
        dem[x["sector"]] = dem.get(x["sector"], 0) + 1
    assert dem["XLK"] == se.LEAD["per_sector"] == 5, dem
    assert dem["XLF"] == 4, dem
    # XLK manh hon TAT CA ma XLF, nhung tong chi 9 chu khong phai 10: tran moi
    # sector rang buoc TRUOC tran tong. De lai mot cho trong con hon them mot ma
    # XLK thu sau.
    assert len(giu) == 9 < se.LEAD["max_total"], len(giu)
    assert ly_do["da du 5 ma cua XLK"] == 7, ly_do


def test_lead_diem_la_rs_va_cat_tran():
    a = se.lead_candidate(lead_row(rs63=0.25), sector="XLK")["quality"]
    b = se.lead_candidate(lead_row(rs63=0.05), sector="XLK")["quality"]
    assert a > b
    tran = se.lead_candidate(lead_row(rs63=se.LEAD["rs_cap63"]),
                             sector="XLK")["quality"]
    vo_cuc = se.lead_candidate(lead_row(rs63=3.0), sector="XLK")["quality"]
    assert tran == vo_cuc, "tren tran khong con phan biet duoc gi"
    # 63 phien nang hon 21 phien
    assert (se.lead_candidate(lead_row(rs21=0.0, rs63=0.30),
                              sector="XLK")["quality"]
            > se.lead_candidate(lead_row(rs21=0.15, rs63=0.0),
                                sector="XLK")["quality"])


def test_lead_khong_co_ham_kich_hoat_trong_phien():
    """CO Y. LEAD la danh sach swing; diem vao cua no la TRADE PLAN cua prompt 2.

    Neu ai do nhet LEAD vao TRIG tro den trig_bo thi LEAD se alert theo pivot
    cua nen tich luy - mot con so lead_candidate() khong he kiem tra va co the
    la None. Test nay de chan viec do.
    """
    c = se.lead_candidate(lead_row(), sector="XLK")
    assert "LEAD" not in se.TRIG
    assert se.check(c, BO_Q) is None


def test_lead_ctx_bang_sector_rank_rong_thi_canh_bao():
    sec = _util.need("sectors")
    db = _db({"AAPL": lead_row(sym="AAPL")})
    c = se.con(db)
    ctx = se.lead_ctx(c)
    assert ctx["map"] == {} and ctx["top"] == []
    assert any("sector_rank" in w for w in ctx["warn"]), ctx["warn"]

    sec.con(c)
    sec.save(c, "2024-06-03", [{"sym": s, "rank": i, "composite": 100.0 - i}
                               for i, s in enumerate(("XLK", "XLF", "XLE",
                                                      "XLV"), 1)])
    ctx = se.lead_ctx(c)
    assert ctx["top"] == ["XLK", "XLF", "XLE"], ctx["top"]
    assert ctx["map"], "phai co ma tu holdings.csv"
    assert set(ctx["map"].values()) <= {"XLK", "XLF", "XLE"}
    assert ctx["map"].get("AAPL") == "XLK"
    assert "XOM" in ctx["map"] and "PG" not in ctx["map"], "XLP khong o top 3"
    c.close()


def test_lead_chi_lay_ma_thuoc_top_sector():
    """Ma khong co trong holdings.csv thi khong bao gio vao danh sach.

    Du no manh hon moi ma khac. Do la cai gia phai tra cho mot file tinh - va
    la ly do --check phai chay khi lam moi file.
    """
    db = _db({"AAPL": lead_row(sym="AAPL"), "LDR": lead_row(sym="LDR")})
    c = se.con(db)
    sec = _util.need("sectors")
    sec.con(c)
    sec.save(c, "2024-06-03", [{"sym": s, "rank": i, "composite": 100.0 - i}
                               for i, s in enumerate(("XLK", "XLF", "XLE"), 1)])
    r = se.build(c)
    assert r["LEAD"] == 1 and r["top_sector"] == ["XLK", "XLF", "XLE"]
    got = se.load_candidates(c, "LEAD", today="2024-06-04")
    assert set(got) == {"AAPL"}
    assert got["AAPL"]["sector"] == "XLK"
    c.close()


# ───────────────────── ly do bi loai ─────────────────────
def test_dem_ly_do_bi_loai():
    rows, rej = se.scan({"AAA": base_row(), "DIP": dip_row()})
    assert {r["sym"] for r in rows} == {"AAA", "DIP"}
    assert rej["BO"]["khong co nen tich luy"] == 1
    assert rej["RV"]["chua roi du 50%"] == 1
    assert rej["_cho_fund"] == 1


def test_check_goi_dung_ham_theo_setup():
    bo = se.bo_candidate(base_row())
    rv = se.rv_candidate(dip_row(), fund={"ok": True})
    assert se.check(bo, BO_Q)["setup"] == "BO"
    assert se.check(rv, RV_Q)["setup"] == "RV"
    assert se.check({"setup": "XX"}, BO_Q) is None


def test_thieu_du_lieu_khong_nem_loi():
    assert se.bo_candidate({}) is None
    assert se.rv_candidate({}) is None
    c = se.bo_candidate(base_row())
    assert se.trig_bo(c, {}) is None
    assert se.trig_rv(se.rv_candidate(dip_row(), fund={"ok": True}), {}) is None
    # thieu hi/lo (quote khong co bien do ngay) -> khong duoc loai oan BO
    assert se.trig_bo(c, {"px": 21.0, "vol": 2_000_000, "rvol": 2.5})


if __name__ == "__main__":
    _util.main(globals())
