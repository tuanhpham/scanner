"""render_watch.py - tin nhan trong phien.

Bon bat bien, xep theo do IM LANG cua loi neu no vo:

1. MOI CANH BAO CO DAU MOC VA DO TRE. Nguon tre ~15 phut. Thieu dong do thi
   nguoi doc dat lenh o mot muc da di qua, va tin nhan khong he trong nhu sai.
2. PANEL <pre> CHI ASCII. Font monospace cua Telegram khong co glyph tieng Viet
   co dau: mot chu co dau lam vo can cot ca bang tren dien thoai.
3. KHONG CO "None" NAO LOT RA TIN NHAN. Mot o thieu du lieu phai thanh "-".
   "None" trong tin nhan doc nhu mot con so va khong ai biet no la gi.
4. MA BI DUNG NGOAI PHAI HIEN RA. Vi the EUR / khong co stop: khong canh thi
   duoc, im lang thi khong.

Thuan stdlib: khong network, khong DB. Chay duoc du khong co pandas.
"""
from __future__ import annotations

import datetime as dt
import unicodedata

import _util

rw = _util.need("render_watch")
import positions                                                 # noqa: E402
import render                                                    # noqa: E402

TS = "2026-09-25T19:42:00+00:00"
DAY = "2026-09-25"


def pos(cur="USD", stops=(198.0,), sym="AAPL", h_ago=1.0) -> dict:
    now = dt.datetime.now(dt.UTC)
    return positions.parse(
        {"rows": [{"sym": sym, "shares": 40, "avgCost": 210.0, "cur": cur,
                   "stops": list(stops), "accts": ["IBKR"]}]},
        int((now.timestamp() - h_ago * 3600) * 1000), now=now)


def alert(rule="trigger", **kw) -> dict:
    base = {
        "trigger": {"sym": "NVDA", "px": 102.5, "val": 1.62,
                    "detail": "giá 102.50 chạm điểm vào 102.00",
                    "fields": {"trigger": 102.0, "stop": 97.0, "target": 112.0,
                               "rvol": 1.62, "size_pct": 0.2, "risk_pct": 0.01,
                               "stop_pct": 0.049}},
        "stop": {"sym": "AAPL", "px": 197.4, "val": 198.0,
                 "detail": "giá 197.40 đã xuyên mức cắt lỗ 198.00",
                 "fields": {"hit": 198.0, "n_hit": 1, "shares": 40.0,
                            "accts": ["IBKR"], "stops": [198.0]}},
        "gap": {"sym": "MSFT", "px": 452.0, "val": 0.043,
                "detail": "mở cửa gap lên 4.3% so với nến quyết định",
                "fields": {"gap": 0.043, "open": 451.6, "ref_close": 433.0,
                           "trigger": 431.5, "stop": 418.0, "target": 458.0,
                           "size_pct": 0.1}},
    }[rule]
    return {"rule": rule, "tier": 1, "ts": TS, "age_sec": 960.0, **base, **kw}


def panels(txt: str) -> list[str]:
    """Noi dung cac panel <pre> trong mot tin nhan."""
    out = []
    for chunk in txt.split("<pre>")[1:]:
        out.append(chunk.split("</pre>")[0])
    return out


def moi_tin() -> list[tuple[str, str]]:
    return rw._demo()


# ────────────── 1. dau moc va do tre ──────────────
def test_moi_canh_bao_co_dau_moc_va_do_tre():
    """BAT BIEN 1."""
    for rule in ("trigger", "stop", "gap"):
        txt = rw.render_alert(alert(rule), "yf")
        assert "báo giá 19:42" in txt or "báo giá 15:42" in txt, (rule, txt[:200])
        assert "trễ 16 phút" in txt, rule
        assert "nguồn yf" in txt, rule


def test_khong_ro_do_tre_thi_noi_la_khong_ro():
    txt = rw.render_alert(alert("trigger", age_sec=None), "yf")
    assert "không rõ độ trễ" in txt, txt


def test_dau_moc_rac_khong_lam_chet_tin_nhan():
    for bad in (None, "", "abc", 0):
        txt = rw.render_alert(alert("trigger", ts=bad), "yf")
        assert txt and "trễ 16 phút" in txt, bad


def test_nhan_mui_gio_khong_bao_gio_la_mot_khang_dinh_sai():
    """Dan "ET" vao mot gio thuc ra la UTC la lech 4-5 gio o dung cho nguoi doc
    khong the tu kiem. Nhan phai theo cai thuc su doi duoc."""
    hm, tz = rw._et_hm(TS)
    import quotes
    assert tz == ("ET" if quotes._et() is not None else "UTC"), (hm, tz)


# ────────────── 2. panel chi ASCII ──────────────
def test_panel_pre_chi_co_ASCII():
    """BAT BIEN 2. Kiem tren TAT CA tin nhan mau, khong chi mot cai."""
    for ten, txt in moi_tin():
        for p in panels(txt):
            assert p == p.encode("ascii", "ignore").decode(), (ten, p)


def test_chu_tieng_viet_co_dau_van_phai_co_NGOAI_panel():
    """Neu ca tin nhan thanh ASCII thi bat bien tren dung ma tin nhan thi sai."""
    for ten, txt in moi_tin():
        ngoai = "".join(txt.split("<pre>")[0:1])
        assert ngoai != ngoai.encode("ascii", "ignore").decode(), ten


# ────────────── 3. khong co "None" lot ra ──────────────
def test_khong_co_None_trong_bat_ky_tin_nhan_nao():
    """BAT BIEN 3."""
    for ten, txt in moi_tin():
        assert "None" not in txt, ten


def test_ke_hoach_thieu_o_thi_hien_dau_gach_chu_khong_phai_None():
    a = alert("trigger")
    a["fields"] = {"trigger": None, "stop": None, "target": None}
    txt = rw.render_alert(a, "yf")
    assert "None" not in txt and "-" in txt, txt


def test_khong_co_truong_nao_thi_van_ra_duoc_tin_nhan():
    """Mot alert thieu het phan phu van phai gui duoc: mat mot dong chu con hon
    mat ca canh bao vi mot KeyError."""
    txt = rw.render_alert({"rule": "stop", "sym": "X", "px": 1.0}, "")
    assert txt and "X" in txt and "None" not in txt, txt


# ────────────── 4. ma bi dung ngoai phai hien ra ──────────────
def test_vi_the_EUR_hien_ra_kem_ly_do():
    """BAT BIEN 4."""
    txt = rw.render_open(rw.WatchView(day=DAY, gate={"mode": "full", "why": "x"},
                                      pos=pos(cur="EUR", sym="ASML")))
    assert "ASML" in txt and "EUR" in txt and "không canh" in txt, txt


def test_vi_the_khong_co_stop_hien_ra():
    txt = rw.render_open(rw.WatchView(day=DAY, gate={"mode": "full", "why": "x"},
                                      pos=pos(stops=())))
    assert "AAPL" in txt and "cắt lỗ" in txt, txt


def test_khong_doc_duoc_vi_the_khac_voi_khong_co_vi_the():
    a = rw.render_open(rw.WatchView(day=DAY, pos=positions.parse(None, None)))
    b = rw.render_open(rw.WatchView(day=DAY, pos=positions.parse({"rows": []}, None)))
    assert a != b, "hai truong hop nay chi phan biet duoc o day"
    assert "chưa đọc được" in a


def test_ma_bi_bo_qua_trong_phien_hien_ra_kem_ly_do():
    txt = rw.render_open(rw.WatchView(
        day=DAY, gate={"mode": "full", "why": "x"},
        skip=[("MSFT", "báo giá quá cũ (trễ 31 phút)"),
              ("MSFT", "không tính được gap")]))
    assert "MSFT" in txt and "quá cũ" in txt and "không tính được gap" in txt
    assert "(1 mã)" in txt, "hai ly do cung mot ma la mot dong, khong phai hai"


# ────────────── 5. che do va im lang ──────────────
def test_downtrend_noi_ro_im_lang_la_co_y():
    """Tin nay la tin DUY NHAT cua ca phien. Neu no khong noi rang sau do se im
    lang thi mot phien binh thuong trong y nhu bot da chet."""
    txt = rw.render_open(rw.WatchView(day=DAY, pos=pos(),
                                      gate={"mode": "stop_only", "why": "x"}))
    assert "im lặng" in txt and "🔴" in txt, txt
    assert "chỉ canh cắt lỗ" in txt


def test_danh_sach_rong_la_mot_ket_qua_chu_khong_phai_loi():
    txt = rw.render_open(rw.WatchView(day=DAY, gate={"mode": "full", "why": "x"}))
    assert "không có mã nào để canh vào lệnh" in txt
    assert "vẫn được canh cắt lỗ" in txt, "phai noi ro cai gi VAN chay"


def test_moi_che_do_deu_co_cau_tieng_viet_cua_no():
    """Mot mode thieu o bang se in ra chuoi enum ASCII giua tin nhan tieng Viet."""
    for m in ("full", "revert", "manage", "stop_only"):
        assert m in rw.MODE_VI and m in rw.MODE_RULES_VI and m in rw.MODE_ICON
        for d in (rw.MODE_VI[m], rw.MODE_RULES_VI[m]):
            assert d != d.encode("ascii", "ignore").decode(), m


def test_moi_luat_co_icon_va_tieu_de_rieng():
    """Phai phan biet duoc tu man hinh khoa dien thoai: mot canh bao cat lo va
    mot canh bao vao lenh doi hoi hai hanh dong nguoc nhau."""
    assert len(set(rw.ICON.values())) == len(rw.ICON) == 3
    for r in ("stop", "trigger", "gap"):
        assert r in rw.ICON and r in rw.TITLE


# ────────────── 6. canh bao cat lo noi dung ban chat cua no ──────────────
def test_canh_bao_cat_lo_noi_ro_day_khong_phai_lenh_stop_that():
    txt = rw.render_alert(alert("stop"), "yf", pos=pos())
    assert "không phải lệnh stop thật" in txt, txt
    assert "40" in txt and "IBKR" in txt, "so co phieu va tai khoan phai co"


def test_anh_chup_cu_thi_canh_bao_van_gui_nhung_co_ghi_chu():
    """Anh chup cu VAN dung (khoa ghi theo su kien). Nhung neu vua doi stop ma
    chua luu app thi con so tren tin nhan la so cu - phai noi ra."""
    old = pos(h_ago=24 * 7)
    txt = rw.render_alert(alert("stop"), "yf", pos=old)
    assert "197.40" in txt and "cập nhật lần cuối" in txt, txt


def test_gap_noi_ro_ke_hoach_co_the_khong_con_dung():
    len_ = rw.render_alert(alert("gap"), "yf")
    xuong = rw.render_alert(alert("gap", val=-0.043,
                                  fields={**alert("gap")["fields"],
                                          "gap": -0.043}), "yf")
    assert "đuổi theo" in len_ and "cỡ vị thế" in xuong, (len_, xuong)
    assert "458.00" in len_, "muc tieu cua ke hoach phai di kem"


# ────────────── 7. do dai va chuan hoa ──────────────
def test_moi_tin_nhan_vua_gioi_han_telegram():
    for ten, txt in moi_tin():
        assert len(txt) <= render.SAFE_LEN, (ten, len(txt))


def test_danh_sach_40_ma_va_40_canh_bao_van_vua():
    """fit() phai bo khoi diem thap thay vi de Telegram tu cat giua the HTML."""
    watch = [{"sym": f"SYM{i}", "trigger": 100.0 + i, "stop": 95.0,
              "target": 120.0, "size_pct": 0.1} for i in range(40)]
    al = [{"sym": f"SYM{i}", "rule": "trigger", "px": 100.0, "tier": 1,
           "ts_utc": TS} for i in range(40)]
    v = rw.WatchView(day=DAY, gate={"mode": "full", "why": "x"}, watch=watch,
                     pos=pos(), alerts=al, skip=[(f"S{i}", "báo giá quá cũ")
                                                 for i in range(20)])
    for txt in (rw.render_open(v), rw.render_summary(v)):
        assert len(txt) <= render.SAFE_LEN, len(txt)
        assert txt.count("<pre>") == txt.count("</pre>"), "cat giua the HTML"


def test_tin_nhan_da_chuan_hoa_NFC():
    """Telegram dem do dai theo UTF-16: mot chu co dau viet bang 2 code point
    dai gap doi, nen mot tin 3799 ky tu dang NFD co the thanh 4200 va bi cat."""
    for ten, txt in moi_tin():
        assert txt == unicodedata.normalize("NFC", txt), ten


def test_the_html_can_bang():
    for ten, txt in moi_tin():
        for the in ("b", "i", "u", "pre", "blockquote"):
            assert txt.count(f"<{the}>") == txt.count(f"</{the}>"), (ten, the)


# ────────────── nut bang dieu khien ──────────────
def test_khong_tin_nao_con_the_a_trong_than_tin():
    """Link dashboard la mot nut (reply_markup), khong phai mot dong chu.

    Mot the <a> trong than tin nhan an vao gioi han 4096 ky tu, va vi no o khoi
    uu tien thap nhat (P_FOOT) thi no bi cat dau tien dung nhung phien nhieu
    canh bao nhat - tuc dung luc can mo dashboard nhat. No cung bi degrade()
    strip mat khi Telegram tu choi tag."""
    for ten, txt in moi_tin():
        assert "<a href" not in txt, ten
        assert "Xem bảng điều khiển" not in txt, ten


def test_ban_phim_co_dung_mot_nut_dan_ve_dashboard():
    url = "https://x.pages.dev/#scanner"
    assert rw.keyboard(url) == {"inline_keyboard": [[{"text": rw.BTN_DASH,
                                                     "url": url}]]}
    v = rw.WatchView(day=DAY, url=url)
    assert rw.render_keyboard(v) == rw.keyboard(url), "hai duong phai ra mot nut"


def test_chua_cau_hinh_dashboard_thi_khong_co_ban_phim():
    """`{"inline_keyboard": [[]]}` bi Telegram tu choi bang 400 -> phai la None."""
    assert rw.keyboard("") is None
    assert rw.render_keyboard(rw.WatchView(day=DAY)) is None


if __name__ == "__main__":
    _util.main(globals())
