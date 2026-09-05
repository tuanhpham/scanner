"""edgar.py — diem rui ro pha loang. Sai o day thi alert khuyen NGUOC.

Chi test assess(): do la ham duy nhat main.py dung (main.py:154, :285). filings()
va _fetch() goi mang + DB that nen duoc thay the trong test — logic can bao ve
la "tu danh sach ho so ra diem rui ro", khong phai HTTP.

Diem quan trong nhat: 424B5 MOI (dang ban co phieu ngay bay gio) va 424B5 CU
phai cho diem khac han nhau. Lan lon hai cai la canh bao sai chieu.
"""
from __future__ import annotations

import datetime as dt

import _util

e = _util.need("edgar")


def _f(form: str, age: int, items: str = "") -> dict:
    return {"form": form, "date": dt.date.today() - dt.timedelta(days=age),
            "age": age, "items": items,
            "url": "https://www.sec.gov/Archives/edgar/data/1/x/y.htm"}


def _assess(fs: list[dict], sym: str = "AAA") -> dict:
    """assess() nhung filings() da bi thay the — khong goi mang, khong doc DB."""
    real, e.filings = e.filings, lambda s, days=e.SHELF_DAYS: list(fs)
    try:
        return e.assess(sym)
    finally:
        e.filings = real


# ───────────────────────── khong co ho so ─────────────────────────
def test_khong_co_ho_so():
    a = _assess([])
    assert a["risk"] == 0.0 and a["n"] == 0 and a["flags"] == []
    assert a["earn"] is False and a["note"]
    assert a["sym"] == "AAA"


# ───────────────────────── pha loang: moi vs cu ─────────────────────────
def test_chao_ban_moi_nang_diem_het_trong_so():
    a = _assess([_f("424B5", 2)])
    assert a["risk"] == 3.0
    assert len(a["flags"]) == 1 and "(cũ)" not in a["flags"][0]
    assert "424B5" in a["flags"][0] and "2d" in a["flags"][0]


def test_chao_ban_cu_chi_con_mot_phan():
    """Shelf takedown 60 ngay truoc khong phai ly do hom nay giam gia."""
    a = _assess([_f("424B5", e.HOT_DAYS + 1)])
    assert a["risk"] == round(3.0 * 0.3, 1) == 0.9
    assert "(cũ)" in a["flags"][0]


def test_nguong_nong_la_nho_hon_bang():
    """age == HOT_DAYS van tinh la nong."""
    assert _assess([_f("424B5", e.HOT_DAYS)])["risk"] == 3.0
    assert _assess([_f("424B5", e.HOT_DAYS + 1)])["risk"] < 3.0


def test_dang_ky_ke_hang_dung_moc_30_ngay():
    """S-3 dung moc rieng (30 ngay), khong dung HOT_DAYS."""
    assert _assess([_f("S-3", 30)])["risk"] == 1.5
    assert _assess([_f("S-3", 31)])["risk"] == round(1.5 * 0.4, 1) == 0.6
    assert "(cũ)" in _assess([_f("S-3", 31)])["flags"][0]


def test_huy_niem_yet():
    assert _assess([_f("25-NSE", 1)])["risk"] == 3.0
    assert _assess([_f("25-NSE", 60)])["risk"] == 1.5
    assert _assess([_f("NT 10-K", 1)])["risk"] == 2.0


# ───────────────────────── tin hieu tich cuc ─────────────────────────
def test_gom_hang_lam_diem_am():
    """Duong = nguy hiem, am = tich cuc. Dao dau day la doi ca ket luan."""
    a = _assess([_f("SC 13D", 10)])
    assert a["risk"] == -1.0 and len(a["flags"]) == 1
    assert _assess([_f("SC 13G", 10)])["risk"] == -0.5


def test_gom_hang_qua_cu_thi_bo_han():
    a = _assess([_f("SC 13D", 31)])
    assert a["risk"] == 0.0 and a["flags"] == [], "cu qua thi khong ke ca flag"
    assert a["n"] == 1, "van dem vao tong so ho so"


# ───────────────────────── 8-K ─────────────────────────
def test_8k_bao_ket_qua_kinh_doanh():
    a = _assess([_f("8-K", 1, items="2.02")])
    assert a["earn"] is True and a["risk"] == 0.0
    assert e.ITEMS["2.02"] in a["flags"][0]


def test_8k_item_nguy_hiem_cong_diem():
    assert _assess([_f("8-K", 1, items="1.03")])["risk"] == 3.0     # pha san
    assert _assess([_f("8-K", 1, items="3.01")])["risk"] == 2.5     # huy NY
    assert _assess([_f("8-K", 1, items="3.02")])["risk"] == 2.0     # ban ngoai


def test_8k_nhieu_item():
    a = _assess([_f("8-K", 1, items="2.02, 3.02")])
    assert a["risk"] == 2.0 and a["earn"] is True
    assert a["flags"][0].count(";") == 1, a["flags"]


def test_8k_item_la_khong_nem_loi():
    a = _assess([_f("8-K", 1, items="9.99")])
    assert a["risk"] == 0.0 and a["flags"] == ["8-K 1d trước"]
    assert _assess([_f("8-K", 1, items="")])["flags"] == ["8-K 1d trước"]
    assert _assess([_f("8-K", 1, items=" , ,")])["flags"] == ["8-K 1d trước"]


def test_8k_cu_thi_bo_qua_ca_earn():
    """8-K 30 ngay truoc khong giai thich cu tang hom nay."""
    a = _assess([_f("8-K", e.HOT_DAYS + 1, items="2.02, 1.03")])
    assert a["risk"] == 0.0 and a["flags"] == [] and a["earn"] is False


# ───────────────────────── cong don & detail ─────────────────────────
def test_cong_don_nhieu_ho_so():
    a = _assess([_f("424B5", 1), _f("S-3", 5), _f("SC 13D", 5)])
    assert a["risk"] == round(3.0 + 1.5 - 1.0, 1) == 3.5
    assert a["n"] == 3 and len(a["flags"]) == 3


def test_detail_gop_theo_form_va_dem_so_lan():
    fs = [_f("424B5", 1), _f("424B5", 20), _f("424B5", 50), _f("S-3", 60)]
    a = _assess(fs)
    forms = [d["form"] for d in a["detail"]]
    assert forms == ["424B5", "S-3"], forms
    d = a["detail"][0]
    assert d["n"] == 3 and d["age"] == 1, "phai giu ban MOI NHAT lam dai dien"
    assert d["desc"] == e.FORMS["424B5"][2]


def test_gioi_han_do_dai():
    """Tin nhan Telegram co han: flags <= 6, detail <= 5."""
    fs = [_f(f, 1) for f in ("424B5", "424B4", "424B3", "424B2", "FWP",
                             "S-3", "S-1", "EFFECT", "NT 10-Q")]
    a = _assess(fs)
    assert len(a["flags"]) <= 6 and len(a["detail"]) <= 5
    assert a["n"] == len(fs), "n van la tong that, khong bi cat"


def test_risk_lam_tron_mot_chu_so():
    a = _assess([_f("424B5", 10), _f("424B3", 10)])
    assert a["risk"] == round(3.0 * 0.3 + 2.0 * 0.3, 1)
    assert len(str(a["risk"]).split(".")[-1]) <= 1, a["risk"]


def test_top_la_ho_so_moi_nhat():
    a = _assess([_f("8-K", 1, items="8.01"), _f("424B5", 30)])
    assert a["top"]["form"] == "8-K"


# ───────────────────────── giao uoc voi render.py ─────────────────────────
def test_detail_du_khoa_cho_render():
    """render._sec_lines() doc d['form'] va d['age'] TRUC TIEP (khong .get):
    thieu khoa la TypeError giua phien, mat ca alert."""
    a = _assess([_f("424B5", 1), _f("8-K", 2, items="2.02")])
    for d in a["detail"]:
        assert {"form", "age", "desc", "n"} <= set(d), d
        assert isinstance(d["age"], int) and isinstance(d["n"], int)


def test_render_ve_duoc_ket_qua_that_cua_assess():
    r = _util.need("render")
    base = {"sym": "AAA", "px": 4.2, "chg": 0.9, "score": 9.1, "rvol": 20.0,
            "atr_move": 3.0, "dollar_vol": 5e6, "float_sh": 5e6,
            "float_rot": 1.2, "cik": "0000000001", "freshness": "REALTIME",
            "explain": "RVOL 20.0x (+2.9)"}
    for fs in ([], [_f("424B5", 1)], [_f("8-K", 1, items="2.02")],
               [_f("SC 13D", 5)], [_f("424B5", 1), _f("424B5", 9),
                                   _f("S-3", 40), _f("25-NSE", 2)]):
        v = r.AlertView.from_scan(base, sec=_assess(fs), detail=True)
        txt = r.render_alert(v)
        assert "AAA" in txt and len(txt) <= r.SAFE_LEN


def test_main_khong_dung_ham_co_emoji():
    """label()/block()/line() la cho CLI: moi cai in mot emoji rieng. Dung chung
    trong alert la pha quy uoc 2 emoji/tin nhan (mục 9 README)."""
    src = (_util.ROOT / "main.py").read_text(encoding="utf-8")
    for fn in ("edgar.label", "edgar.block", "edgar.line"):
        assert fn not in src, f"{fn} khong duoc dung trong duong gui alert"


# ───────────────────────── bang tra ─────────────────────────
def test_bang_forms_hop_le():
    groups = {"BAN_NGAY", "SAN_SANG", "TIN", "GOM_HANG", "XAU"}
    for form, val in e.FORMS.items():
        grp, w, desc = val
        assert grp in groups, (form, grp)
        assert isinstance(w, float) and desc, form
    assert set(e.ITEM_RISK) <= set(e.ITEMS), "item co diem ma khong co mo ta"
    for code in e.ITEMS:
        assert code.count(".") == 1, code


def test_label_theo_nguong():
    assert "CAO" in e.label(3.0)
    assert e.label(1.5) != e.label(3.0)
    assert e.label(1.4) != e.label(1.5)
    assert e.label(-0.5) != e.label(0.0)
    assert e.label(0.0, earn=True) != e.label(0.0, earn=False)
    # earn khong duoc de len rui ro cao: pha loang quan trong hon.
    assert e.label(3.0, earn=True) == e.label(3.0)


if __name__ == "__main__":
    _util.main(globals())
