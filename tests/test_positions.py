"""positions.py - doc vi the dang mo tu khoa `scanner:positions`.

Bon bat bien, xep theo do IM LANG cua loi neu no vo:

1. KHONG DOC DUOC != KHONG CO VI THE. Ca hai truong hop deu cho ra "khong canh
   gi ca", nhung mot cai la hop le va mot cai la he thong dang hong. Neu parse()
   tra known=True cho mot payload rac thi phien do im lang y nhu mot phien khong
   co vi the, va khong co cach nao phat hien tu ben ngoai.
2. CU KHONG PHAI LA SAI. Khoa duoc ghi theo su kien (luc luu portfolio), nen
   khong giao dich mot tuan thi no cu mot tuan va van dung. Neu tuoi vo hieu hoa
   du lieu thi moi sang thu Hai canh cat lo tat - dung o cho nguy hiem nhat.
3. MUC CAT LO CAO NHAT BI XUYEN TRUOC. Nhieu lo cung mot ma, moi lo mot stop.
   Lay sai muc thi canh muon, hoac khong bao gio canh.
4. KHONG QUY DOI TIEN TE, NHUNG PHAI NOI RA. Mot muc cat lo lech 12% con te hon
   khong co muc nao. Dung ngoai thi duoc, im lang thi khong.

Thuan stdlib: parse() la ham thuan nen khong file nao o day can mang. Duong ra
mang (load()) chi duoc kiem o TRUONG HOP THAT BAI - va do cung la truong hop
quan trong hon.
"""
from __future__ import annotations

import datetime as dt

import _util

po = _util.need("positions")
import config                                                    # noqa: E402

NOW = dt.datetime(2026, 9, 25, 16, 0, tzinfo=dt.UTC)


def ms(h_ago: float = 1.0) -> int:
    """Dau moc cua Cloudflare, `h_ago` gio truoc NOW."""
    return int((NOW.timestamp() - h_ago * 3600) * 1000)


def val(*rows: dict, warn: list | None = None) -> dict:
    return {"ts": "2026-09-25T15:00:00.000Z", "n": len(rows),
            "rows": list(rows), "warn": warn or []}


def pos(sym="NVDA", shares=10, cur="USD", stops=(), **kw) -> dict:
    return {"sym": sym, "shares": shares, "avgCost": 100.0, "cur": cur,
            "stops": list(stops), "withStop": shares if stops else 0,
            "noStop": 0 if stops else shares, "accts": ["A"], **kw}


def one(row: dict, h_ago: float = 1.0) -> dict:
    """Mot dong da qua parse(), de khoi lap lai ba dong moi test."""
    return po.parse(val(row), ms(h_ago), now=NOW)["rows"][row["sym"].upper()]


# ────────────── 1. khong doc duoc != khong co vi the ──────────────
def test_doc_duoc_mot_ban_hop_le():
    d = po.parse(val(pos(stops=[90])), ms(), now=NOW)
    assert d["known"] is True and d["n"] == 1 and d["bad"] == 0, d


def test_payload_khong_dung_hinh_dang_la_KHONG_BIET():
    """Cai bay chinh cua ca file. known=False cho moi thu khong phai anh chup."""
    for v in (None, "", 0, [], ["NVDA"], {}, {"n": 2}, {"rows": "NVDA"},
              {"rows": {"NVDA": 1}}):
        d = po.parse(v, ms(), now=NOW)
        assert d["known"] is False, f"{v!r} bi coi la doc duoc"
        assert d["n"] == 0 and d["rows"] == {}
        assert "chưa đọc được" in d["note"], d["note"]


def test_khong_co_vi_the_nao_la_mot_CAU_TRA_LOI_chu_khong_phai_loi():
    d = po.parse(val(), ms(), now=NOW)
    assert d["known"] is True and d["n"] == 0, d
    assert "không có vị thế" in d["note"]
    assert po.watched(d) == [] and po.unchecked(d) == []


def test_hai_truong_hop_do_khong_bao_gio_cho_ra_cung_mot_cau():
    """Tin nhan mo phien la cho duy nhat phan biet duoc chung, nen phai khac."""
    trong = po.parse(val(), ms(), now=NOW)["note"]
    khong_biet = po.parse(None, ms(), now=NOW)["note"]
    assert trong != khong_biet and trong and khong_biet


def test_load_khi_khong_co_mang_tra_ve_khong_biet():
    """Tren may nay push.ready() False -> get_full() None. Duong that bai that."""
    d = po.load(now=NOW)
    assert d["known"] is False and d["n"] == 0, d
    assert d["note"], "im lang o day la kieu loi te nhat"


# ────────────── 2. tuoi chi de noi ra ──────────────
def test_tuoi_tinh_theo_dong_ho_cua_cloudflare():
    d = po.parse(val(pos()), ms(3.5), now=NOW)
    assert abs(d["age_h"] - 3.5) < 1e-6, d["age_h"]
    assert not d["old"]


def test_khong_co_dau_moc_cloudflare_thi_khong_doan_tuoi():
    """`value["ts"]` do trinh duyet ghi. Hien thi thi duoc, tinh tuoi thi khong:
    mot may chay lech vai phut se cho ra "luon con moi" hoac tuoi am."""
    d = po.parse(val(pos()), None, now=NOW)
    assert d["known"] and d["age_h"] is None and d["old"] is False
    assert d["ts"] == "2026-09-25T15:00:00.000Z"


def test_dong_ho_lech_ve_tuong_lai_khong_thanh_tuoi_am():
    d = po.parse(val(pos()), ms(-9), now=NOW)
    assert d["age_h"] == 0.0 and not d["old"], d


def test_anh_chup_cu_van_duoc_dung_de_canh_stop():
    """BAT BIEN 2. Khoa nay ghi theo su kien: mot tuan khong giao dich thi no cu
    mot tuan va van dung tung chu. Bo di la tat canh cat lo moi sang thu Hai."""
    d = po.parse(val(pos(stops=[90])), ms(24 * 7), now=NOW)
    assert d["known"] is True and d["n"] == 1, d
    assert d["old"] is True
    assert po.watched(d) == ["NVDA"]
    assert po.stop_hit(d["rows"]["NVDA"], 85)["hit"] == 90.0


def test_cu_thi_tin_nhan_phai_noi_ra_va_noi_phai_lam_gi():
    d = po.parse(val(pos(stops=[90])), ms(24 * 7), now=NOW)
    assert "cập nhật lần cuối" in d["note"] and "7 ngày trước" in d["note"]
    assert "mở app" in d["note"], "noi cu ma khong noi cach sua thi vo dung"


def test_nguong_lay_tu_config_chu_khong_viet_cung():
    gio = config.INTRADAY["pos_stale_h"]
    assert po.parse(val(pos()), ms(gio - 0.5), now=NOW)["old"] is False
    assert po.parse(val(pos()), ms(gio + 0.5), now=NOW)["old"] is True
    # Nguong 0 / thieu khoa = khong bao gio goi la cu, chu khong phai luon cu.
    d = po.parse(val(pos()), ms(1000), now=NOW, g={})
    assert d["old"] is False and d["known"] is True


def test_luu_chieu_hom_truoc_sang_hom_sau_van_con_moi():
    """Ly do nguong la 30 gio chu khong 24: dong so vao 16:00 hom truoc thi 9:30
    hom sau moi ~17.5 gio, nhung luu luc 18:00 roi mo phien 15:30 (gio VN) van
    phai duoc coi la moi."""
    assert not po.parse(val(pos()), ms(29), now=NOW)["old"]


# ────────────── 3. muc cat lo cao nhat bi xuyen truoc ──────────────
def test_cac_muc_duoc_xep_giam_dan_khong_theo_thu_tu_gui_len():
    r = one(pos(stops=[90, 112, 101]))
    assert r["stops"] == [112.0, 101.0, 90.0]


def test_muc_bi_xuyen_la_muc_CAO_NHAT():
    """BAT BIEN 3. Bang portfolio hien stop cua lo CU NHAT (90). Gia 100 da xuyen
    stop cua lo moi (112) roi. Lay so cua bang thi canh muon 12%."""
    r = one(pos(stops=[90, 112]))
    h = po.stop_hit(r, 100)
    assert h["hit"] == 112.0 and h["n"] == 1 and h["ok"] is True, h


def test_gia_tren_moi_muc_thi_khong_canh():
    assert po.stop_hit(one(pos(stops=[90, 112])), 120)["hit"] is None


def test_bang_nhau_tinh_la_da_xuyen():
    """Lenh stop that o san kich o dung muc do. > thay vi >= la mot alert bi bo
    lo tai dung cai gia minh da chon."""
    assert po.stop_hit(one(pos(stops=[90])), 90.0)["hit"] == 90.0


def test_xuyen_nhieu_muc_thi_dem_du():
    h = po.stop_hit(one(pos(stops=[90, 112])), 80)
    assert h["hit"] == 112.0 and h["n"] == 2, h


def test_khong_co_gia_thi_khong_ket_luan():
    for px in (None, 0, -5, "abc", float("nan")):
        h = po.stop_hit(one(pos(stops=[90])), px)
        assert h["hit"] is None and h["ok"] is False, px
        assert h["why"] == "khong_co_gia"


def test_khong_co_stop_thi_so_sanh_duoc_nhung_khong_co_gi_de_so():
    h = po.stop_hit(one(pos(stops=[])), 50)
    assert h["ok"] is True and h["hit"] is None
    assert h["why"] == "khong_co_stop"


def test_ma_khong_co_stop_van_phai_duoc_noi_ra():
    """Do la dung cai vi the khong ai bao ve duoc. Im lang o day doc thanh
    "khong co gi dang bao"."""
    d = po.parse(val(pos(stops=[])), ms(), now=NOW)
    assert dict(po.unchecked(d))["NVDA"] == po.WHY_UNCHECKED["khong_co_stop"]
    assert po.watched(d) == []


# ────────────── 4. tien te: dung ngoai, nhung noi ra ──────────────
def test_gia_nhap_bang_EUR_khong_duoc_so_voi_bao_gia_USD():
    """BAT BIEN 4. metrics.ts o phia app dang so stop EUR voi lastPrice USD. Sao
    y cho do sang day se cho ra alert o muc lech ~12%, va no LAI con tu tin."""
    r = one(pos(cur="EUR", stops=[175]))
    h = po.stop_hit(r, 160)
    assert h["ok"] is False and h["hit"] is None and h["why"] == "tien_te", h


def test_EUR_phai_di_vao_tin_nhan_kem_ly_do_doc_duoc():
    d = po.parse(val(pos(cur="EUR", stops=[175])), ms(), now=NOW)
    why = dict(po.unchecked(d))["NVDA"]
    assert "EUR" in why and "USD" in why, why
    assert po.watched(d) == []


def test_MIXED_cung_dung_ngoai():
    """Mot ma co lo nhap EUR va lo nhap USD: khong chon duoc ben nao ma khong doan."""
    assert po.comparable(one(pos(cur="MIXED", stops=[90]))) == "tien_te"


def test_thieu_tien_te_cung_la_dung_ngoai():
    """Khong biet thi khong phai la USD. Mot ban app cu khong gui `cur` phai lam
    scanner NOI RA, chu khong lam no doan."""
    r = one({"sym": "X", "shares": 5, "stops": [5]})
    assert r["cur"] is None
    assert po.stop_hit(r, 4)["why"] == "khong_biet_tien_te"


def test_cur_viet_thuong_van_duoc_nhan():
    assert po.comparable(one(pos(cur="usd", stops=[90]))) is None


def test_moi_ly_do_khong_canh_duoc_deu_co_cau_tieng_viet_co_dau():
    for key, cau in po.WHY_UNCHECKED.items():
        assert cau != cau.encode("ascii", "ignore").decode(), key
        assert cau != key


def test_moi_khoa_stop_hit_co_the_tra_ve_deu_co_cau_cua_no():
    """Ben goi tra cuu WHY_UNCHECKED de viet tin nhan. Mot khoa thieu o day la
    mot KeyError giua phien, tuc la mat canh bao vi mot dong chu."""
    r = one(pos(stops=[90]))
    ra = {po.stop_hit(r, None)["why"],
          po.stop_hit(one(pos(stops=[])), 50)["why"],
          po.stop_hit(one(pos(cur="EUR", stops=[90])), 50)["why"],
          po.stop_hit(one({"sym": "X", "shares": 1, "stops": [5]}), 4)["why"]}
    assert ra <= set(po.WHY_UNCHECKED), ra - set(po.WHY_UNCHECKED)


# ────────────── 5. rac vao, khong chet ra ──────────────
def test_bo_cac_dong_khong_dung_hinh_dang_va_DEM_chung():
    d = po.parse(val({"sym": "", "shares": 5}, {"sym": "Z", "shares": 0},
                     "NVDA", None, pos(sym="W", stops=[7])), ms(), now=NOW)
    assert list(d["rows"]) == ["W"], d["rows"]
    assert d["bad"] == 4 and "4 dòng không đọc được" in d["note"], d


def test_muc_cat_lo_rac_bi_bo_khong_lam_chet_ca_dong():
    r = one(pos(sym="W", stops=[None, "abc", -1, 0, float("nan"), 7, 7]))
    assert r["stops"] == [7.0], r["stops"]


def test_ma_duoc_chuan_hoa_ve_chu_hoa():
    d = po.parse(val(pos(sym="nvda", stops=[90])), ms(), now=NOW)
    assert list(d["rows"]) == ["NVDA"]


def test_trung_ma_thi_giu_ban_nhieu_muc_hon():
    """Phia ghi da gop san; day la luoi cuoi. Giu ban it muc hon = mat mot muc
    cat lo ma khong ai biet."""
    d = po.parse(val(pos(stops=[90]), pos(sym="nvda", stops=[90, 112])),
                 ms(), now=NOW)
    assert d["n"] == 1 and d["rows"]["NVDA"]["stops"] == [112.0, 90.0]


def test_canh_bao_tu_phia_ghi_di_nguyen_van():
    d = po.parse(val(pos(), warn=["2 mã chưa có mức cắt lỗ"]), ms(), now=NOW)
    assert d["warn"] == ["2 mã chưa có mức cắt lỗ"]


def test_khong_mang_theo_tien_von_lai_lo():
    """Cai gi khong co trong anh chup thi khong duoc xuat hien o day. Day la
    kiem o phia DOC, doi lai voi kiem o phia ghi: neu mot ban app sau nay day
    thua truong thi khong duoc phep tu dong chay tiep vao scanner."""
    d = po.parse(val(pos(cash=1234, equity=5678, realized=9)), ms(), now=NOW)
    r = d["rows"]["NVDA"]
    for k in ("cash", "equity", "realized", "unrealized", "accountId"):
        assert k not in r, k
    assert set(r) == {"sym", "shares", "avg_cost", "stops", "cur",
                      "with_stop", "no_stop", "accts"}, sorted(r)


if __name__ == "__main__":
    _util.main(globals())
