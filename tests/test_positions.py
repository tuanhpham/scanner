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
mang (load()) duoc kiem qua `_util.cloud`, tuc la cau tra loi cua Cloudflare
duoc GIA LAP ca ba kieu: None, doc duoc, va nem loi. Truoc day chi co kieu None
va no khong duoc gia lap gi ca — test do tin vao viec may dang chay khong co
`.env`, nen no do tren may co, va duong "doc duoc" thi khong ai kiem.
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


def val(*rows: dict, warn: list | None = None, fx: dict | None = None) -> dict:
    """Than cua khoa. `fx` VANG MAT khi khong truyen, chu khong phai None: mot
    ban app cu hon khong co truong do, va duong di do phai la duong mac dinh."""
    v = {"ts": "2026-09-25T15:00:00.000Z", "n": len(rows),
         "rows": list(rows), "warn": warn or []}
    if fx is not None:
        v["fx"] = fx
    return v


# Ty gia con moi so voi NOW (25.09.2026), dung cho phan lon cac test quy doi.
FX = {"eurUsd": 1.2, "asOf": "2026-09-24"}


def pos(sym="NVDA", shares=10, cur="USD", stops=(), **kw) -> dict:
    return {"sym": sym, "shares": shares, "avgCost": 100.0, "cur": cur,
            "stops": list(stops), "withStop": shares if stops else 0,
            "noStop": 0 if stops else shares, "accts": ["A"], **kw}


def one(row: dict, h_ago: float = 1.0) -> dict:
    """Mot dong da qua parse(), de khoi lap lai ba dong moi test."""
    return po.parse(val(row), ms(h_ago), now=NOW)["rows"][row["sym"].upper()]


def one_fx(row: dict, fx: dict | None = None) -> dict:
    """Nhu `one` nhung anh chup co mang ty gia con moi (FX)."""
    return po.parse(val(row, fx=fx or FX), ms(),
                    now=NOW)["rows"][row["sym"].upper()]


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
    """get_full() tra None: thieu token, mang chet, hoac khoa chua ton tai.

    Cau nay tung viet "tren may nay push.ready() False" va KHONG gia lap gi ca.
    Tren may da cau hinh xong thi no doc vi the that ve va do. Mot test khong
    duoc doc `.env` de biet minh phai khang dinh dieu gi — xem `_util.cloud`.
    """
    with _util.cloud(None):
        d = po.load(now=NOW)
    assert d["known"] is False and d["n"] == 0, d
    assert d["note"], "im lang o day la kieu loi te nhat"


def test_load_khi_doc_duoc_thi_lay_ca_dau_moc_cua_cloudflare():
    """Nua con lai cua load(), truoc day khong co test nao di qua.

    Duong nay chi chay tren may co `.env`, va o do no chay trong mot test dang
    khang dinh dieu nguoc lai — nen no vua khong duoc kiem, vua lam test kia do.
    """
    js = {"value": val(pos(stops=[90])), "updatedAt": ms(2)}
    with _util.cloud(js):
        d = po.load(now=NOW)
    assert d["known"] is True and d["n"] == 1, d
    assert abs(d["age_h"] - 2) < 1e-6, d["age_h"]


def test_load_khi_push_no_ra_loi_van_la_khong_biet_chu_khong_nem():
    """`load()` duoc goi tu vong quet trong phien: nem ra o day la tat ca canh
    cat lo cho den het phien. Ten loi phai co trong cau, neu khong thi khong ai
    biet vi sao ca ngay khong canh gi."""
    with _util.cloud(RuntimeError("cloudflare 500")):
        d = po.load(now=NOW)
    assert d["known"] is False and d["n"] == 0, d
    assert "RuntimeError" in d["note"], d["note"]


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
    """Mot ma co lo luu bang EUR va lo bang USD: khong chon duoc ben nao ma
    khong doan, va ty gia cung khong cuu duoc - khong co MOT don vi de quy doi
    TU do. Ly do rieng chu khong dung chung voi EUR: cach sua khac han (tach
    tai khoan, chu khong phai doi ty gia moi)."""
    assert po.comparable(one(pos(cur="MIXED", stops=[90]))) == "tron_tien_te"
    d = po.parse(val(pos(cur="MIXED", stops=[90]),
                     fx={"eurUsd": 1.2, "asOf": "2026-09-25"}), ms(), now=NOW)
    assert po.comparable(d["rows"]["NVDA"]) == "tron_tien_te"
    assert "tách tài khoản" in dict(po.unchecked(d))["NVDA"]


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
    cu = {"eurUsd": 1.2, "asOf": "2026-06-01"}
    ra = {po.stop_hit(r, None)["why"],
          po.stop_hit(one(pos(stops=[])), 50)["why"],
          po.stop_hit(one(pos(cur="EUR", stops=[90])), 50)["why"],
          po.stop_hit(one(pos(cur="MIXED", stops=[90])), 50)["why"],
          po.stop_hit(one_fx(pos(cur="EUR", stops=[90]), cu), 50)["why"],
          po.stop_hit(one({"sym": "X", "shares": 1, "stops": [5]}), 4)["why"]}
    assert ra <= set(po.WHY_UNCHECKED), ra - set(po.WHY_UNCHECKED)
    # Va nguoc lai: mot cau khong bao gio duoc dung la mot cau da chet.
    assert ra == set(po.WHY_UNCHECKED), set(po.WHY_UNCHECKED) - ra


# ────────────── 4b. ty gia di kem: quy doi, hoac tu choi ──────────────
def test_co_ty_gia_con_moi_thi_quy_doi_va_canh_that():
    """Ca ly do co co che nay: mot muc cat lo EUR truoc day khong duoc canh, nen
    vi the o tai khoan EUR khong duoc bao ve trong phien."""
    d = po.parse(val(pos(cur="EUR", stops=[175]), fx=FX), ms(), now=NOW)
    r = d["rows"]["NVDA"]
    assert r["stops"] == [175.0], "con so GOC phai giu nguyen de hien lai"
    assert r["stops_usd"] == [210.0], r
    assert po.comparable(r) is None and po.watched(d) == ["NVDA"]
    assert po.unchecked(d) == []


def test_muc_dem_ra_so_la_muc_da_QUY_DOI_chu_khong_phai_so_goc():
    """Day dung la loi cu, chi nguoc chieu: so 175 EUR dem so voi bao gia USD se
    canh o 175 USD, tuc la muon 17%. Phai so voi 210."""
    r = one_fx(pos(cur="EUR", stops=[175]))
    assert po.stop_hit(r, 211)["hit"] is None, "211 chua xuyen 210"
    assert po.stop_hit(r, 209)["hit"] == 210.0
    assert po.stop_hit(r, 176)["hit"] == 210.0, "176 da xuyen tu lau"


def test_canh_bao_phai_mang_ca_hai_con_so():
    """Nguoi dung dat 175 EUR chu khong dat 210 USD. Mot tin nhan chi co 210 doc
    nhu scanner nho sai muc cat lo cua ho, va lan sau ho khong tin no nua."""
    h = po.stop_hit(one_fx(pos(cur="EUR", stops=[175])), 200)
    assert h["hit"] == 210.0 and h["hit_raw"] == 175.0, h
    assert h["cur"] == "EUR" and h["conv"]["rate"] == 1.2, h
    assert h["conv"]["as_of"] == FX["asOf"]


def test_hit_raw_khop_dung_muc_bi_xuyen_khi_co_nhieu_muc():
    """Chi so cua muc trong `stops_usd` phai dung cho ca `stops`. Lech mot buoc
    la tin nhan noi mot muc ma he thong lai kiem mot muc khac."""
    r = one_fx(pos(cur="EUR", stops=[100, 175]))
    assert r["stops_usd"] == [210.0, 120.0]
    h = po.stop_hit(r, 130)
    assert h["hit"] == 210.0 and h["hit_raw"] == 175.0, h
    h2 = po.stop_hit(r, 110)
    assert h2["n"] == 2 and h2["hit_raw"] == 175.0, h2


def test_USD_khong_bi_ty_gia_lam_thay_doi_gi():
    """Co ty gia trong anh chup khong duoc lam mot dong USD lech di."""
    r = one_fx(pos(cur="USD", stops=[90, 112]))
    assert r["stops_usd"] == [112.0, 90.0] and r["conv"] is None
    h = po.stop_hit(r, 100)
    assert h["hit"] == 112.0 and h["hit_raw"] == 112.0


def test_ty_gia_qua_cu_thi_TU_CHOI_quy_doi():
    """BAT BIEN 4 chua doi: quy doi bang ty gia ba thang tuoi la dung lai chinh
    cai loi ma co che nay sinh ra de tranh - mot muc tu tin nhung sai."""
    d = po.parse(val(pos(cur="EUR", stops=[175]),
                     fx={"eurUsd": 1.2, "asOf": "2026-06-01"}), ms(), now=NOW)
    r = d["rows"]["NVDA"]
    assert r["stops_usd"] is None and po.comparable(r) == "ty_gia_cu"
    assert po.stop_hit(r, 100)["hit"] is None, "khong duoc canh"
    assert po.watched(d) == []
    # Va phai noi ro la vi TY GIA, khong phai vi EUR: cach sua khac nhau.
    why = dict(po.unchecked(d))["NVDA"]
    assert "tỷ giá" in why and "cũ" in why, why


def test_nguong_tuoi_ty_gia_lay_tu_config():
    ngay = config.INTRADAY["pos_fx_stale_d"]
    trong = (NOW.date() - dt.timedelta(days=ngay)).isoformat()
    ngoai = (NOW.date() - dt.timedelta(days=ngay + 1)).isoformat()
    for as_of, mong in ((trong, True), (ngoai, False)):
        d = po.parse(val(pos(cur="EUR", stops=[175]),
                         fx={"eurUsd": 1.2, "asOf": as_of}), ms(), now=NOW)
        assert d["fx"]["ok"] is mong, as_of
        assert (d["rows"]["NVDA"]["stops_usd"] is not None) is mong, as_of
    # Nguong 0 / thieu khoa = khong bao gio goi la cu.
    d = po.parse(val(pos(cur="EUR", stops=[175]),
                     fx={"eurUsd": 1.2, "asOf": "2020-01-01"}), ms(), now=NOW,
                 g={})
    assert d["fx"]["ok"] is True and d["rows"]["NVDA"]["stops_usd"] == [210.0]


def test_ngay_ty_gia_lech_ve_tuong_lai_khong_thanh_tuoi_am():
    d = po.parse(val(pos(cur="EUR", stops=[175]),
                     fx={"eurUsd": 1.2, "asOf": "2026-12-31"}), ms(), now=NOW)
    assert d["fx"]["age_d"] == 0.0 and d["fx"]["ok"] is True


def test_mot_so_khong_phai_ty_gia_bi_bo_thay_vi_dung():
    """Mot gia co phieu lot vao truong nay, hoac mot so 0, phai bi coi nhu KHONG
    CO ty gia - khong phai duoc dung. Dung 232.5 lam ty gia thi muc cat lo bay
    len 40 nghin va khong bao gio bi xuyen: mot canh bao im lang mai mai."""
    for xau in ({"eurUsd": 0}, {"eurUsd": -1.2, "asOf": "2026-09-24"},
                {"eurUsd": 232.5, "asOf": "2026-09-24"},
                {"eurUsd": float("nan"), "asOf": "2026-09-24"},
                {"eurUsd": "abc", "asOf": "2026-09-24"}):
        d = po.parse(val(pos(cur="EUR", stops=[175]), fx=xau), ms(), now=NOW)
        assert d["fx"] is None, xau
        assert po.comparable(d["rows"]["NVDA"]) == "tien_te", xau


def test_ty_gia_khong_biet_cua_ngay_nao_thi_khong_duoc_dung():
    """Khong co `asOf` thi khong kiem duoc tuoi, va khong kiem duoc tuoi thi
    khong duoc quy doi - de ngo cua cho dung ty gia ba thang tuoi."""
    for xau in ({"eurUsd": 1.2}, {"eurUsd": 1.2, "asOf": ""},
                {"eurUsd": 1.2, "asOf": "hom qua"},
                {"eurUsd": 1.2, "asOf": "2026-13-45"}, 1.2, "1.2", [], None):
        d = po.parse(val(pos(cur="EUR", stops=[175]), fx=xau), ms(), now=NOW)
        assert d["fx"] is None, xau
        assert d["rows"]["NVDA"]["stops_usd"] is None, xau


def test_ban_app_cu_khong_gui_ty_gia_thi_hanh_vi_y_nhu_truoc():
    """Deploy doc lap: VM co the chay ban moi truoc khi app duoc deploy."""
    d = po.parse(val(pos(cur="EUR", stops=[175])), ms(), now=NOW)
    assert d["fx"] is None
    assert po.comparable(d["rows"]["NVDA"]) == "tien_te"
    assert po.watched(d) == [] and len(po.unchecked(d)) == 1


def test_fx_note_noi_du_ty_gia_va_ngay():
    """Mot muc cat lo hien ra bang USD ma nguoi dung dat bang EUR thi ho phai
    biet con so do tu dau, khong thi ho tuong minh nho sai."""
    d = po.parse(val(pos(cur="EUR", stops=[175]), fx=FX), ms(), now=NOW)
    cau = po.fx_note(d)
    assert "1.2000" in cau and FX["asOf"] in cau, cau
    # Khong co ty gia thi khong co gi de noi - khong duoc noi mot cau rong.
    assert po.fx_note(po.parse(val(pos()), ms(), now=NOW)) == ""
    assert po.fx_note(po.parse(None, ms(), now=NOW)) == ""
    # Cu thi cau phai noi ra la KHONG dung, va noi cach sua.
    cu = po.parse(val(pos(cur="EUR", stops=[175]),
                      fx={"eurUsd": 1.2, "asOf": "2026-06-01"}), ms(), now=NOW)
    assert "KHÔNG" in po.fx_note(cu) and "mở app" in po.fx_note(cu)


def test_ty_gia_khong_lam_mot_ma_thieu_cur_thanh_canh_duoc():
    """Khong biet don vi thi ty gia khong giup gi: khong biet quy doi TU dau."""
    d = po.parse(val({"sym": "X", "shares": 1, "stops": [5]}, fx=FX),
                 ms(), now=NOW)
    assert po.comparable(d["rows"]["X"]) == "khong_biet_tien_te"


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
                      "stops_usd", "cmp_why", "conv",
                      "with_stop", "no_stop", "accts"}, sorted(r)


if __name__ == "__main__":
    _util.main(globals())
