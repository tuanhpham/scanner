"""nightly.py — chuoi chay buoi sang.

Cai duy nhat module nay ton tai de bao dam: MOT BUOC DO THI VAN CO BAO CAO, va
bao cao do noi ra buoc nao do. Nen phan lon test o day co tinh lam cho mot buoc
do roi kiem xem bao cao con dung khong — chu khong phai kiem duong chay thuan.

Khong goi mang: cac test dung `--dry-run` (bo `bars`/`prep`/`push`/`telegram`)
hoac DB rong (moi buoc tinh toan deu do that). `nightly.py` chi can sqlite3 nen
chay duoc ca tren may khong co pandas.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import _util

ng = _util.need("nightly")
import render_night                                              # noqa: E402


def _db() -> Path:
    return Path(tempfile.mkdtemp()) / "t.db"


def _quiet():
    """Logger khong in ra stdout: mot test khong duoc lam ban ket qua test khac."""
    return ng._log_setup(quiet=True)


def _res(**kw) -> dict:
    d = {"run_id": "2026-09-25T12:00:00+00:00", "day": "2026-09-25",
         "bar": "2026-09-24", "ok": False, "code": 1, "sec": 12.5, "dry": False,
         "stages": [{"stage": "bars", "ok": True, "fatal": True, "sec": 1.0},
                    {"stage": "sectors", "ok": False, "fatal": True, "sec": 0.2,
                     "err": "khong do duoc 2/11 sector"}],
         "warn": ["holdings.csv cu"]}
    d.update(kw)
    return d


# ───────────────────────── nen quyet dinh (lop chan thu 3) ─────────────────────
def test_bar_phien_truoc_la_binh_thuong():
    assert ng._check_bar("2026-09-24", "2026-09-25") == []


def test_bar_trung_ngay_chay_la_nhin_truoc_tuong_lai():
    w = ng._check_bar("2026-09-25", "2026-09-25")
    assert w and "TRÙNG ngày chạy" in w[0]
    assert "partial_day" in w[0], "phai chi cho di kiem o dau"


def test_bar_o_tuong_lai_bi_bat():
    w = ng._check_bar("2026-09-26", "2026-09-25")
    assert w and "TƯƠNG LAI" in w[0]


def test_cuoi_tuan_dai_khong_bi_bao_dong_gia():
    """Thu 6 -> thu 3 la 4 ngay lich. Bao dong moi tuan thi khong ai doc nua."""
    assert ng._check_bar("2026-09-21", "2026-09-25") == []


def test_kho_nen_cu_thi_bi_bao():
    w = ng._check_bar("2026-09-18", "2026-09-25")
    assert w and "không được làm mới" in w[0] and "`bars`" in w[0]


def test_khong_co_bar_thi_bao_la_khong_doc_duoc():
    w = ng._check_bar(None, "2026-09-25")
    assert w and "chưa có dòng nào" in w[0]


# ───────────────────────── bo dau cho panel ─────────────────────────
def test_bo_dau_giu_nguyen_chu():
    """Canh bao viet mot lan co dau; panel bo dau. Khong duoc thanh "kh?ng"."""
    assert ng._ascii("xếp hạng ngành") == "xep hang nganh"
    assert ng._ascii("đo lường · bước") == "do luong | buoc"
    assert ng._ascii("→ 1.5×") == "-> 1.5x"


def test_moi_canh_bao_deu_bo_dau_duoc_sach():
    for w in ng._check_bar(None) + ng._check_bar("2026-09-25", "2026-09-25"):
        assert "?" not in ng._ascii(w), w


def test_panel_chi_co_ascii():
    """Panel di vao mail cron va journalctl: hai cho khong chac utf-8."""
    r = _res(stages=[{"stage": "regime", "ok": True, "fatal": False, "sec": 0.1,
                      "detail": "2026-09-24 · UPTREND · co vi the 100%"}],
             warn=["Nến quyết định trùng ngày chạy"])
    p = ng.panel(r)
    assert p.isascii(), [x for x in p if not x.isascii()]
    assert "UPTREND" in p and "Nen quyet dinh trung" in p


def test_panel_ngat_dong_theo_tu_khong_cat_giua_tu():
    """Canh bao huu ich nhat la cau noi phai chay lenh gi. Cat no la mat no."""
    r = _res(warn=["cai goi `tzdata` bang lenh pip install tzdata " + "x " * 40])
    p = ng.panel(r)
    assert "pip install tzdata" in p
    assert max(len(l) for l in p.splitlines()) <= ng.PANEL_W + 1


def test_panel_khong_vo_voi_moi_hinh_dang():
    for r in (_res(), _res(stages=[], warn=[]), _res(bar=None, dry=True),
              _res(stages=[{"stage": "x", "ok": False, "fatal": True,
                            "sec": 0.0}])):
        assert "ma thoat" in ng.panel(r)


# ───────────────────────── bang `night` ─────────────────────────
def test_luu_va_doc_lai_nguyen_ven():
    db = _db()
    r = _res()
    ng.save_run(db, r)
    got = ng.last_run(db)
    assert got["run_id"] == r["run_id"] and got["code"] == 1
    assert got["stages"][1]["err"] == "khong do duoc 2/11 sector"
    assert got["warn"] == ["holdings.csv cu"]


def test_lan_do_khong_duoc_tinh_la_thanh_cong():
    """`last_ok` la nguon cua banner "so lieu cu" tren dashboard."""
    db = _db()
    ng.save_run(db, _res())
    assert ng.last_ok(db) is None
    ng.save_run(db, _res(run_id="2026-09-26T12:00:00+00:00", ok=True, code=0))
    assert ng.last_ok(db)["run_id"] == "2026-09-26T12:00:00+00:00"


def test_chay_lai_cung_run_id_la_ghi_de():
    db = _db()
    ng.save_run(db, _res())
    ng.save_run(db, _res(ok=True, code=0))
    c = ng.con(db)
    try:
        assert c.execute("SELECT COUNT(*) FROM night").fetchone()[0] == 1
        assert c.execute("SELECT code FROM night").fetchone()[0] == 0
    finally:
        c.close()


def test_chi_giu_KEEP_RUNS_dong():
    db = _db()
    for i in range(ng.KEEP_RUNS + 12):
        ng.save_run(db, _res(run_id=f"2026-01-01T{i:04d}"))
    c = ng.con(db)
    try:
        n = c.execute("SELECT COUNT(*) FROM night").fetchone()[0]
    finally:
        c.close()
    assert n == ng.KEEP_RUNS, n


def test_bang_night_rong_thi_last_run_tra_None():
    assert ng.last_run(_db()) is None


# ───────────────────────── --dry-run ─────────────────────────
def test_dry_run_khong_ghi_gi_vao_night():
    db = _db()
    r = ng.run(db, dry=True, only={"regime"}, lg=_quiet())
    assert r["dry"] and r["stages"]
    c = ng.con(db)
    try:
        assert c.execute("SELECT COUNT(*) FROM night").fetchone()[0] == 0
    finally:
        c.close()


def test_dry_run_bo_cac_buoc_goi_mang():
    db = _db()
    r = ng.run(db, dry=True, lg=_quiet(), only={"bars", "prep"})
    for s in r["stages"]:
        assert s.get("skipped"), s


# ───────────────────────── that bai: cai chinh ─────────────────────────
def _chain(db) -> dict:
    """Ca chuoi tren DB rong -> `sectors` chac chan do. Bo `telegram` (can mang)."""
    return ng.run(db, dry=True, lg=_quiet(), only=set(ng.NAMES) - {"telegram"})


def test_buoc_bat_buoc_do_thi_ma_thoat_la_1():
    assert _chain(_db())["code"] == 1


def test_buoc_bat_buoc_do_thi_cac_buoc_sau_ghi_la_blocked_chu_khong_phai_do():
    r = _chain(_db())
    bl = [s for s in r["stages"] if s.get("blocked")]
    # Moi buoc SAU `sectors`, bat buoc hay khong: mot buoc khong chay thi khong
    # the goi la "chay xong" chi vi no khong bat buoc.
    sau = [n for n, _, _ in ng.STAGES]
    assert {s["stage"] for s in bl} == set(sau[sau.index("sectors") + 1:]), bl
    for s in bl:
        assert "da dung chuoi" in s["err"]


def test_mot_nguyen_nhan_thi_ke_mot_loi():
    r = _chain(_db())
    v = render_night.NightView(day="2026-09-25", stages=r["stages"])
    do = [s for s in r["stages"] if not s["ok"] and not s.get("blocked")]
    assert len(render_night._failed(v)) == len(do)


def test_buoc_do_van_gui_duoc_tin_nhan_va_tin_nhan_goi_ten_buoc():
    """Yeu cau go: "Silent failure is worse than no scanner"."""
    r = _chain(_db())
    v = render_night.NightView(day="2026-09-25", stages=r["stages"])
    txt = render_night.render_night(v)
    assert "CHUỖI CHẠY KHÔNG XONG" in txt
    assert "xếp hạng ngành" in txt
    assert "Chưa lọc được" in txt
    assert len(txt) <= render_night.render.SAFE_LEN


def test_chi_buoc_khong_bat_buoc_do_thi_ma_thoat_la_2():
    """So lieu van dung duoc -> cron khong nen bao dong nhu khi mat so lieu."""
    db = _db()
    r = ng.run(db, dry=True, lg=_quiet(), only={"regime"})
    assert not r["ok"] and r["code"] == 2, r


def test_khong_mo_duoc_db_thi_ma_thoat_la_3():
    """Chua chay duoc gi ca — khac han "chay roi nhung mot buoc do"."""
    d = Path(tempfile.mkdtemp()) / "khong-phai-thu-muc"
    d.write_text("x", encoding="utf-8")
    r = ng.run(d / "t.db", dry=True, lg=_quiet(), only={"regime"})
    assert r["code"] == 3, r


# ───────────────────────── bang buoc ─────────────────────────
def test_FATAL_khop_voi_STAGES():
    assert ng.FATAL == {"bars", "sectors", "structure", "setups"}, ng.FATAL
    assert ng.FATAL == {n for n, f, _ in ng.STAGES if f}


def test_NAMES_khong_trung_va_phu_het_STAGES():
    """`only={...}` va bang buoc trong tin nhan deu di qua NAMES. Mot ten trung
    lam bang buoc ke doi mot dong; mot ten thieu lam buoc do khong bao gio hien."""
    assert len(ng.NAMES) == len(set(ng.NAMES)), ng.NAMES
    assert ng.NAMES == tuple(n for n, _, _ in ng.STAGES) + ("push", "telegram")
    assert ng.NAMES[-2:] == ("push", "telegram")


def test_moi_buoc_co_ten_tieng_viet_trong_tin_nhan():
    """Thieu ten thi bang buoc in ra ma khoa ASCII giua mot tin nhan tieng Viet -
    hoac tuy cach render, in ra mot dong trong."""
    for n in ng.NAMES:
        assert n in render_night.STAGE_VI, n


def test_moi_buoc_co_ten_tieng_viet():
    assert set(render_night.STAGE_VI) == set(ng.NAMES)


def test_only_loc_dung_va_khong_nhan_ten_la():
    r = ng.run(_db(), dry=True, lg=_quiet(), only={"regime", "sectors"})
    assert {s["stage"] for s in r["stages"]} == {"regime", "sectors"}


# ───────────────────────── mui gio ─────────────────────────
def test_khong_co_tzdata_thi_bao_ra_chu_khong_nem_loi():
    """Khong bao gio dung offset co dinh: -5 gio se sai suot 8 thang trong nam."""
    assert isinstance(ng.today_et(), str) and len(ng.today_et()) == 10
    w = ng.tz_warn()
    if w:                                   # may khong co tzdata (Windows tran)
        assert "tzdata" in w[0] and "UTC" in w[0]
    else:
        assert ng._et() is not None


def test_canh_bao_mui_gio_di_vao_ket_qua_chay():
    r = ng.run(_db(), dry=True, lg=_quiet(), only={"regime"})
    assert r["warn"] == ng.tz_warn() + ng._check_bar(r["bar"], r["day"]), r["warn"]


# ───────────────────────── link dashboard ─────────────────────────
def test_link_dashboard_suy_ra_tu_push_url():
    """Khong them bien moi truong thu hai: hai bien cung mot domain se lech nhau."""
    assert ng.dashboard_url("https://x.pages.dev/api/scanner") \
        == "https://x.pages.dev/#scanner"
    assert ng.dashboard_url("https://x.pages.dev/api/scanner/") \
        == "https://x.pages.dev/#scanner", "dau / cuoi khong duoc doi ket qua"
    assert ng.dashboard_url("https://x.pages.dev") == "https://x.pages.dev/#scanner"
    assert ng.dashboard_url("") == "", "chua cau hinh push thi khong co dashboard"
    assert ng.dashboard_url("   ") == ""


def test_link_dashboard_doc_bien_moi_truong_luc_goi():
    """Doc luc goi, khong chot luc import: thu tu import khong duoc quyet dinh."""
    import os
    cu = os.environ.get("SCANNER_PUSH_URL")
    try:
        os.environ["SCANNER_PUSH_URL"] = "https://y.pages.dev/api/scanner"
        assert ng.dashboard_url() == "https://y.pages.dev/#scanner"
        os.environ.pop("SCANNER_PUSH_URL")
        assert ng.dashboard_url() == ""
    finally:
        if cu is None:
            os.environ.pop("SCANNER_PUSH_URL", None)
        else:
            os.environ["SCANNER_PUSH_URL"] = cu


# ───────────────────────── JSON trong DB ─────────────────────────
def test_stages_luu_duoi_dang_json_doc_duoc_tu_ngoai():
    """Dashboard doc bang nay qua push.py -> phai la JSON hop le, khong phai repr."""
    db = _db()
    ng.save_run(db, _res())
    c = sqlite3.connect(str(db))
    try:
        raw = c.execute("SELECT stages, warn FROM night").fetchone()
    finally:
        c.close()
    assert isinstance(json.loads(raw[0]), list)
    assert isinstance(json.loads(raw[1]), list)


if __name__ == "__main__":
    _util.main(globals())
