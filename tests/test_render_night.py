"""render_night.py — tin nhan swing buoi sang.

Cho nay khong co mang, khong co DB, khong co pandas: NightView la mot dataclass
thuan nen moi truong hop deu dung duoc bang dict gia. Do la ly do render duoc
tach khoi nightly.py.

Cai dang kiem nhat o day khong phai dinh dang, ma la mot cau HOI NGHIA: bang
rong vi khong co ma nao dat, hay bang rong vi buoc loc chua chay? Hai cau do
doc gan giong nhau va dan den hai hanh dong nguoc nhau.
"""
from __future__ import annotations

import _util

rn = _util.need("render_night")
import config                                                    # noqa: E402
import render                                                     # noqa: E402


def _v(**kw) -> "rn.NightView":
    d = {"day": "2026-09-25", "bar": "2026-09-24",
         "regime": rn._demo_regime(), "url": ""}
    d.update(kw)
    return rn.NightView(**d)


# ───────────────────────── nen quyet dinh ─────────────────────────
def test_bar_truoc_ngay_chay_la_binh_thuong():
    txt = rn.render_night(_v())
    assert "24/09/2026 (phiên đã chốt)" in txt
    assert "chưa chốt" not in txt


def test_bar_trung_ngay_chay_phai_canh_bao_nhin_truoc_tuong_lai():
    """Loi nang nhat trong he thong, va no khong tu bao -> tin nhan phai bao."""
    txt = rn.render_night(_v(bar="2026-09-25"))
    assert "trùng ngày chạy" in txt.lower()
    assert "chưa chốt" in txt
    assert "Đừng vào lệnh" in txt


def test_khong_co_bar_thi_khong_duoc_noi_la_da_chot():
    txt = rn.render_night(_v(bar=None))
    assert "không xác định được" in txt
    assert "đã chốt" not in txt, "khang dinh sai theo huong lam yen long"


# ───────────────────────── bang rong: hai nghia khac nhau ─────────────────────
def test_watch_rong_va_da_loc_xong_la_ket_qua_binh_thuong():
    stages = [{"stage": "setups", "ok": True}]
    txt = rn.render_night(_v(watch=[], stages=stages))
    assert "Không mã nào qua hết sàn chất lượng" in txt
    assert "Chưa lọc được" not in txt


def test_watch_rong_vi_buoc_loc_khong_chay_phai_noi_ro():
    stages = [{"stage": "setups", "ok": False, "fatal": True, "err": "vo"}]
    txt = rn.render_night(_v(watch=[], stages=stages))
    assert "Chưa lọc được" in txt
    assert "Không mã nào qua hết sàn" not in txt, \
        "bang rong vi loi bi ke thanh ket qua"


def test_stages_rong_thi_coi_nhu_da_chay():
    """Goi tu backfill/test, khong co ai bao ngo lai -> im lang hon la to cao bua."""
    txt = rn.render_night(_v(watch=[], stages=[]))
    assert "Không mã nào qua hết sàn" in txt


def test_sector_rong_vi_loi_thi_noi_ra_nguon_bi_mat():
    stages = [{"stage": "sectors", "ok": False, "fatal": True, "err": "vo"}]
    txt = rn.render_night(_v(sectors=[], stages=stages))
    assert "Chưa xếp được" in txt


def test_sector_rong_ma_da_xep_xong_thi_bo_han_khoi_do():
    stages = [{"stage": "sectors", "ok": True}]
    txt = rn.render_night(_v(sectors=[], stages=stages))
    assert "XẾP HẠNG NGÀNH" not in txt


# ─────────────── bang theo doi la KE HOACH LENH, khong phai chi so ───────────
def _panel(txt: str) -> list[str]:
    """Cac dong cua panel <pre> ngay sau tieu de DANH SACH THEO DOI."""
    i = txt.index("DANH SÁCH THEO DÕI")
    blk = txt[txt.index("<pre>", i) + 5: txt.index("</pre>", i)]
    return blk.split("\n")


def test_bang_theo_doi_in_du_bon_con_so_de_vao_lenh():
    """Vao, cat lo, muc tieu, co - do la ca ly do bang nay ton tai.

    Neu mot trong bon cot nay bien mat thi tin nhan lai tro thanh mot bang chi
    so, va nguoi doc lai phai tu quyet dinh giua phien - dung cai ma he thong
    nay duoc dung de khong phai lam.
    """
    w = rn._demo_watch(2)
    lines = _panel(rn.render_night(_v(watch=w)))
    for nhan in ("VAO", "STOP", "MUCTIEU", "CO"):
        assert nhan in lines[0], f"mat cot {nhan}"
    dong = lines[2]
    assert f"{w[0]['trigger']:.2f}" in dong
    assert f"{w[0]['stop']:.2f}" in dong
    assert f"{w[0]['target']:.2f}" in dong


def test_panel_du_hep_de_khong_vo_cot_tren_dien_thoai():
    """Font monospace cua Telegram tren dien thoai vo cot khi rong hon ~48."""
    lines = _panel(rn.render_night(_v(watch=rn._demo_watch(8))))
    rong = max(len(x) for x in lines)
    assert rong <= 48, f"panel rong {rong} ky tu"


def test_bang_40_dong_van_khong_bo_mat_bang_nganh():
    """watch_top = 40, va do la truong hop dai nhat co the xay ra thuc te.

    Neu bang theo doi day tin nhan qua SAFE_LEN thi fit() bo bang xep hang
    nganh - mat nguon cua chinh danh sach do ma khong noi mot cau nao.
    """
    w = (rn._demo_watch(8) * 6)[: int(config.NIGHTLY["watch_top"])]
    txt = rn.render_night(_v(watch=w, sectors=rn._demo_sectors()[0],
                             stages=[{"stage": "setups", "ok": True}]))
    assert len(txt) <= render.SAFE_LEN
    assert "XẾP HẠNG NGÀNH" in txt


def test_ma_khong_lap_duoc_ke_hoach_duoc_noi_ra_chu_khong_im_lang():
    """Thieu ATR -> plan.make() tra None. Mot dong toan dau `-` de bi doc thanh
    "vao lenh tuy y", nen phai co mot cau noi ro la KHONG vao lenh."""
    w = rn._demo_watch(4)
    txt = rn.render_night(_v(watch=w))
    assert "chưa lập được kế hoạch" in txt
    assert "không</u> vào lệnh" in txt


def test_moi_ma_co_ke_hoach_thi_khong_canh_bao_thua():
    w = [x for x in rn._demo_watch(4) if x.get("trigger")]
    assert w, "fixture phai con it nhat mot dong co ke hoach"
    assert "chưa lập được kế hoạch" not in rn.render_night(_v(watch=w))


def test_co_vi_the_toan_bo_bang_0_phai_giai_thich_vi_sao():
    """Playbook bat dung ngoai -> ca cot CO la 0%. Bang van gui de con theo doi,
    nhung mot cot 0% khong loi giai doc y het nhu mot loi tinh toan."""
    w = [{**x, "size_pct": 0.0} for x in rn._demo_watch(3)]
    txt = rn.render_night(_v(watch=w))
    assert "không cho mở vị thế mới" in txt
    # Va khi co vi the binh thuong thi khong duoc hien cau do.
    assert "không cho mở vị thế mới" not in rn.render_night(_v(watch=rn._demo_watch(2)))


def test_khong_duoc_noi_la_phan_trong_phien_se_tinh_diem_vao():
    """Cau cu ("diem vao do phan canh bao trong phien tinh") gio la SAI: ke hoach
    da co san tu dem truoc, phan trong phien chi so sanh gia voi no."""
    txt = rn.render_night(_v(watch=rn._demo_watch(3)))
    assert "không đổi trong phiên" in txt
    assert "chứ không tính lại" in txt


# ───────────────────────── bao loi ─────────────────────────
def _fail_stages() -> list[dict]:
    return [
        {"stage": "regime", "ok": False, "fatal": False, "err": "SPY thieu nen"},
        {"stage": "sectors", "ok": False, "fatal": True, "err": "11/11 sector do"},
        {"stage": "structure", "ok": False, "fatal": True, "blocked": True,
         "err": "khong chay"},
        {"stage": "setups", "ok": False, "fatal": True, "blocked": True,
         "err": "khong chay"},
    ]


def test_buoc_bi_chan_khong_dem_la_loi_rieng():
    """Mot nguyen nhan -> mot loi duoc ke. Hau qua di thanh mot cau "keo theo"."""
    v = _v(stages=_fail_stages())
    assert len(rn._failed(v)) == 2
    assert len(rn._blocked(v)) == 2
    txt = rn.render_night(v)
    assert "2 bước lỗi" in txt
    assert "Kéo theo, không chạy: đo cấu trúc giá, lọc danh sách." in txt


def test_bao_loi_goi_ten_buoc_bang_tieng_viet_va_neu_ly_do():
    txt = rn.render_night(_v(stages=_fail_stages()))
    assert "xếp hạng ngành" in txt and "dừng chuỗi" in txt
    assert "bối cảnh thị trường" in txt and "bỏ qua, chạy tiếp" in txt
    assert "11/11 sector do" in txt, "phai neu ly do, khong chi ten buoc"


def test_buoc_dung_chuoi_dung_truoc_buoc_bo_qua_duoc():
    txt = rn.render_night(_v(stages=_fail_stages()))
    assert txt.index("xếp hạng ngành") < txt.index("bối cảnh thị trường")


def test_co_loi_thi_icon_doi_sang_bao_dong():
    assert rn.ICON_FAIL in rn.render_night(_v(stages=_fail_stages()))
    assert rn.ICON["UPTREND"] in rn.render_night(_v())


def test_ly_do_qua_dai_bi_cat_nhung_van_co_dau_hieu_bi_cat():
    stages = [{"stage": "sectors", "ok": False, "fatal": True, "err": "x" * 500}]
    txt = rn.render_night(_v(stages=stages))
    assert "..." in txt and "x" * 500 not in txt


# ───────────────────────── khoi bat buoc vs khoi bo duoc ─────────────────────
def test_tin_nhan_dai_thi_bo_bang_nganh_truoc_bao_loi():
    """Quy uoc 3: khoi loi khong bao gio bi bo truoc so lieu."""
    v = _v(stages=_fail_stages(), sectors=rn._demo_sectors()[0],
           watch=rn._demo_watch(8),
           warn=["canh bao rat dai " * 60, "canh bao rat dai " * 60])
    txt = rn.render_night(v)
    assert len(txt) <= render.SAFE_LEN
    assert "CHUỖI CHẠY KHÔNG XONG" in txt, "bao loi bi bo -> that bai im lang"


def test_cac_ban_demo_deu_vua_gioi_han():
    for k in ("binh thuong", "downtrend", "fail", "lookahead"):
        txt = rn.render_night(rn._demo(k))
        assert 0 < len(txt) <= render.SAFE_LEN, (k, len(txt))


# ───────────────────────── bang nganh ─────────────────────────
def test_top3_duoc_danh_dau_va_nhom_phong_thu_duoc_canh_bao():
    rows, chg = rn._demo_sectors()
    top = ["XLP", "XLU", "XLK"]
    txt = rn.render_night(_v(sectors=rows, chg=chg, top_sectors=top))
    assert "Nhóm phòng thủ vào top 3" in txt
    assert "XLP, XLU" in txt


def test_chu_thich_dau_sao_o_ngoai_panel_va_co_dau():
    """Chu thich `*` phai co dau, va phai NGOAI <pre>.

    Hai rang buoc keo nguoc nhau: trong panel thi font monospace cua Telegram
    khong co glyph tieng Viet (test_panel_pre_chi_dung_ascii), ma bo dau di thi
    doc rat kho chiu. Cho dung la ngay duoi panel, nhu moi dong <i> khac.
    """
    rows, chg = rn._demo_sectors()
    txt = rn.render_night(_v(sectors=rows, chg=chg, top_sectors=["XLK"]))
    assert "<i>* = 3 ngành dẫn dắt (nguồn của danh sách theo dõi).</i>" in txt
    assert "ngành dẫn dắt" not in txt[:txt.index("</pre>")]


def test_khong_co_nhom_phong_thu_thi_khong_canh_bao():
    rows, chg = rn._demo_sectors()
    txt = rn.render_night(_v(sectors=rows, chg=chg,
                             top_sectors=["XLK", "XLY", "XLF"]))
    assert "Nhóm phòng thủ" not in txt


def test_thay_doi_hang_khong_biet_khac_khong_doi():
    assert rn._arrow(None) == "-"
    assert rn._arrow(0) == "0"
    assert rn._arrow(3) == "+3" and rn._arrow(-2) == "-2"


def test_cot_thay_doi_hang_theo_dung_config():
    rows, chg = rn._demo_sectors()
    txt = rn.render_night(_v(sectors=rows, chg=chg))
    for w in config.SECTORS["change_wins"]:
        assert f"{int(w)}d" in txt, f"thieu cot {w}d"


# ───────────────────────── boi canh + playbook ─────────────────────────
def test_playbook_lay_tu_config_khong_phai_ban_sao():
    r = rn._demo_regime(trend="DOWNTREND", vol="EXPANDED", size=0.0)
    txt = rn.render_night(_v(regime=r))
    assert config.PLAYBOOK[("DOWNTREND", "EXPANDED")]["note"] in txt


def test_moi_o_trong_playbook_co_note_co_dau():
    """`note` di nguyen van vao tin nhan -> phai la chu cho nguoi doc."""
    assert len(config.PLAYBOOK) == 12
    for k, pb in config.PLAYBOOK.items():
        assert pb.get("note"), k
        assert not pb["note"].isascii(), f"{k}: note khong co dau"


def test_doi_regime_duoc_noi_ra():
    txt = rn.render_night(_v(prev=rn._demo_regime(trend="RANGE")))
    assert "đổi từ" in txt and "đi ngang" in txt


def test_khong_doi_regime_thi_khong_them_chu():
    txt = rn.render_night(_v(prev=rn._demo_regime(trend="UPTREND")))
    assert "đổi từ" not in txt


def test_co_vi_the_bang_khong_duoc_dich_ra_chu():
    txt = rn.render_night(_v(regime=rn._demo_regime(size=0.0)))
    assert "không mở vị thế mới" in txt


# ───────────────────────── quy uoc dinh dang ─────────────────────────
def test_panel_pre_chi_dung_ascii():
    """Font monospace cua Telegram khong co glyph tieng Viet -> vo can cot."""
    txt = rn.render_night(rn._demo("binh thuong"))
    i = 0
    n = 0
    while (i := txt.find("<pre>", i)) >= 0:
        j = txt.index("</pre>", i)
        body = txt[i + 5:j]
        assert body.isascii(), f"panel co chu co dau: {body[:80]!r}"
        n += 1
        i = j
    assert n >= 2, "demo phai co it nhat 2 panel"


def test_toi_da_hai_icon_trong_mot_tin_nhan():
    """Quy uoc 2. Emoji la de MAT dung vao, nhieu emoji thi khong con la dung."""
    for k in ("binh thuong", "downtrend", "fail", "lookahead"):
        txt = rn.render_night(rn._demo(k))
        n = sum(txt.count(e) for e in
                (*rn.ICON.values(), rn.ICON_FAIL, "⚠️"))
        assert n <= 4, (k, n)   # 1 header + toi da 3 dau ⚠️ (loi/canh bao/phong thu)


def test_ket_qua_da_chuan_hoa_nfc():
    import unicodedata
    txt = rn.render_night(rn._demo("binh thuong"))
    assert txt == unicodedata.normalize("NFC", txt), \
        "NFD lam do dai phinh len va tin nhan bi Telegram cat"


def test_html_khong_bi_cat_giua_tag():
    """fit() bo TUNG KHOI, khong cat giua chuoi -> tag luon can."""
    for k in ("binh thuong", "downtrend", "fail", "lookahead"):
        txt = rn.render_night(rn._demo(k))
        for tag in ("b", "i", "u", "pre", "blockquote"):
            assert txt.count(f"<{tag}>") == txt.count(f"</{tag}>"), (k, tag)


def test_ten_buoc_khop_voi_nightly():
    """STAGE_VI thieu mot buoc -> tin nhan in ten ky thuat, hoac in "?"."""
    ng = _util.need("nightly")
    assert set(rn.STAGE_VI) == set(ng.NAMES), \
        set(rn.STAGE_VI) ^ set(ng.NAMES)


def test_moi_nhan_enum_co_ban_dich():
    for t in config.PLAYBOOK:
        assert t[0] in rn.TREND_VI, t[0]
        assert t[1] in rn.VOL_VI, t[1]


# ───────────────────────── nut bang dieu khien ─────────────────────────
def test_link_dashboard_la_nut_chu_khong_phai_chu_trong_tin_nhan():
    """Mot the <a> o cuoi tin nhan an vao gioi han 4096 ky tu VA nam o khoi uu
    tien thap nhat, nen no la thu bi cat dau tien dung nhung dem bao cao dai -
    tuc dung nhung dem can mo dashboard nhat. `reply_markup` thi khong."""
    v = _v(url="https://x.pages.dev/#scanner")
    txt = rn.render_night(v)
    assert "<a href" not in txt, "link dashboard quay lai than tin nhan"
    assert "Xem bảng điều khiển" not in txt
    assert rn.render_keyboard(v) == {"inline_keyboard": [
        [{"text": rn.BTN_DASH, "url": "https://x.pages.dev/#scanner"}]]}


def test_chua_cau_hinh_dashboard_thi_khong_co_ban_phim():
    """`{"inline_keyboard": [[]]}` bi Telegram tu choi bang 400 -> phai la None."""
    assert rn.render_keyboard(_v(url="")) is None


def test_moi_ban_demo_deu_co_nut():
    for k in ("binh thuong", "downtrend", "fail", "lookahead"):
        kb = rn.render_keyboard(rn._demo(k))
        assert kb and kb["inline_keyboard"][0], k


if __name__ == "__main__":
    _util.main(globals())
