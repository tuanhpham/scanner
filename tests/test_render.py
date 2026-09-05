"""render.py — cho nay bug im lang nhat: sai gi cung khong crash, chi ra tin xau.

Test o day khong kiem tra "tin nhan trong dep khong" (khong the tu dong hoa),
ma kiem tra bon thu se pha tin nhan tren dien thoai that:

  1. Panel <pre> co ky tu ngoai ASCII  -> chu lech co font (mục 9 README)
  2. Tag HTML khong can                -> Telegram tra 400, ca alert khong gui
  3. Du lieu thieu (None/"" )          -> TypeError giua phien
  4. URL nut sai scheme                -> Telegram tu choi CA tin nhan
"""
from __future__ import annotations

import re

import _util

r = _util.need("render")

BASE = {"sym": "WETO", "px": 10.61, "chg": 0.855, "score": 12.4,
        "rvol": 66.2, "atr_move": 4.1, "dollar_vol": 311e6,
        "float_sh": 8.4e6, "float_rot": 3.49, "cik": "0001941158",
        "freshness": "REALTIME", "explain": "RVOL 66.2x (+4.0)"}
SEC = {"risk": 4.5, "n": 7, "earn": True,
       "detail": [{"form": "424B5", "age": 2, "n": 1, "desc": "chào bán"}]}


def _v(**kw):
    """AlertView tu BASE, ghi de bang kw. kw danh cho from_scan di rieng."""
    fs = {k: kw.pop(k) for k in ("sec", "prev", "kind", "session", "updated",
                                 "mso", "session_min", "detail", "tracked",
                                 "news_url", "halt") if k in kw}
    return r.AlertView.from_scan({**BASE, **kw}, **fs)


# ───────────────────────── 1. panel ASCII ─────────────────────────
def test_panel_chi_dung_ascii():
    """Font monospace Telegram khong co glyph tieng Viet co dau."""
    for kw in ({}, {"float_sh": None}, {"score": 7.1, "px": 0.9},
               {"sec": SEC}, {"detail": True}, {"prev": {"score": 9.8}}):
        txt = r.render_alert(_v(**kw))
        for blk in re.findall(r"<pre>(.*?)</pre>", txt, re.S):
            bad = {c for c in blk if ord(c) > 127}
            assert not bad, f"{kw}: ky tu ngoai ASCII trong <pre>: {bad}"


def test_panel_thang_cot():
    """Moi hang trong panel phai co do dai on dinh -> nhan khong vuot W_LAB."""
    txt = r.render_alert(_v(sec=SEC))
    for blk in re.findall(r"<pre>(.*?)</pre>", txt, re.S):
        for line in blk.splitlines():
            if line.startswith(" " * r.W_IND) and line.strip():
                lab = line[r.W_IND:r.W_IND + r.W_LAB]
                assert lab == lab.rstrip() or lab.rstrip() == lab.strip(), line
                assert len(line) <= r.W_IND + r.W_LAB + r.W_VAL + 2 + r.W_DLT, \
                    f"hang qua dai, se xuong dong tren dien thoai: {line!r}"


# ───────────────────────── 2. HTML can ─────────────────────────
_TAGS = ("b", "i", "u", "s", "code", "pre", "blockquote", "tg-spoiler", "a")


def _balanced(txt: str) -> None:
    for t in _TAGS:
        o = len(re.findall(rf"<{t}(?:\s[^>]*)?>", txt))
        c = len(re.findall(rf"</{t}>", txt))
        assert o == c, f"<{t}>: {o} mo / {c} dong trong:\n{txt[:400]}"


def test_tag_html_can():
    for kw in ({}, {"sec": SEC}, {"detail": True, "sec": SEC},
               {"score": 7.0}, {"halt": {"code": "T1", "label": "CHỜ TIN",
                                         "sev": 2, "note": "x",
                                         "since": "15:42"}}):
        _balanced(r.render_alert(_v(**kw)))


def test_chi_dung_tag_telegram_cho_phep():
    """Telegram chi nhan mot danh sach tag rat ngan; tag la -> 400."""
    txt = r.render_alert(_v(sec=SEC, detail=True))
    for tag in set(re.findall(r"</?([a-zA-Z-]+)", txt)):
        assert tag.lower() in _TAGS, f"tag khong duoc phep: <{tag}>"


def test_khong_vuot_safe_len():
    """Tin dai -> bo tung KHOI, khong bao gio cat giua tag."""
    huge = {**BASE, "explain": "x " * 4000}
    big_sec = {**SEC, "detail": [{"form": f"F{i}", "age": i, "n": 1,
                                  "desc": "mô tả dài " * 30} for i in range(30)]}
    txt = r.render_alert(r.AlertView.from_scan(huge, sec=big_sec, detail=True))
    assert len(txt) <= r.SAFE_LEN, len(txt)
    _balanced(txt)


def test_hard_cut_dong_tag():
    """Luoi an toan cuoi cung: cat cung roi van phai dong tag."""
    txt = r._hard_cut("<b>a</b>\n<pre>chua dong\n" + "x" * r.SAFE_LEN)
    _balanced(txt)


# ───────────────────────── 3. du lieu thieu ─────────────────────────
def test_du_lieu_thieu_khong_no():
    """Giua phien cac truong nay rat hay thieu — day la loi hay gap nhat."""
    for kw in ({"float_sh": None}, {"float_rot": None}, {"rvol": None},
               {"atr_move": None}, {"dollar_vol": None}, {"cik": None},
               {"explain": ""}, {"freshness": None},
               {"float_sh": None, "float_rot": None, "rvol": None,
                "atr_move": None, "dollar_vol": None, "cik": None,
                "explain": ""}):
        v = _v(**kw)
        txt = r.render_alert(v)
        assert v.sym in txt
        _balanced(txt)
        r.render_keyboard(v)               # khong duoc nem


def test_sec_none_va_rong():
    for sec in (None, {}, {"risk": 0.0, "n": 0, "detail": []},
                {"risk": 0.0, "n": 2, "detail": None, "flags": None}):
        _balanced(r.render_alert(_v(sec=sec)))


def test_px_bang_khong():
    """px=0 khong nen xay ra nhung neu xay ra thi khong duoc chia cho 0."""
    _balanced(r.render_alert(_v(px=0, score=7.5)))


# ───────────────────────── 4. muc do & noi dung ─────────────────────────
def test_nguong_muc_do():
    assert _v(score=7.0).level == 1
    assert _v(score=7.9).level == 1
    assert _v(score=8.0).level == 2
    assert _v(score=11.9).level == 2
    assert _v(score=12.0).level == 3
    # Duong len muc 3 thu hai: SEC risk cao + RVOL nong + quay vong manh.
    assert _v(score=9.0, rvol=60.0, float_rot=3.0, sec=SEC).level == 3
    assert _v(score=9.0, rvol=60.0, float_rot=1.0, sec=SEC).level == 2


def test_gia_va_pct_chi_o_header():
    """Quy uoc: khong lap so lieu. Gia chi xuat hien mot lan."""
    txt = r.render_alert(_v(sec=SEC))
    assert txt.count("$10.61") == 1, txt[:300]


def test_hai_emoji():
    """Ca tin chi 2 emoji: den muc do + canh bao the. Xem mục 9 README."""
    txt = r.render_alert(_v(sec=SEC, detail=True, halt=_halt()))
    # U+FE0F la variation selector di kem ⚠️, khong phai emoji thu hai.
    emo = [c for c in txt
           if ord(c) > 0x2500 and c not in "─│┌┐└┘█░▲▼·—→≥️"]
    assert len(emo) <= 2, f"qua nhieu emoji: {emo}"


def test_nut_chi_tiet_bat_tat_duoc():
    """Bam Chi tiet phai doi duoc trang thai o CA hai muc, khong chi muc 3."""
    for score in (12.4, 8.3):
        assert r.TXT["h_why"] not in r.render_alert(_v(score=score, detail=False))
        assert r.TXT["h_why"] in r.render_alert(_v(score=score, detail=True))


# ───────────────────────── 5. dong HALT ─────────────────────────
def _halt(**kw):
    d = {"code": "LUDP", "label": "BIẾN ĐỘNG (LULD)", "sev": 1,
         "note": "Giá chạy quá dải LULD.", "since": "15:42", "until": "",
         "quote": "", "resumed": False, "active": True}
    return {**d, **kw}


def test_halt_hien_o_dau_tin():
    txt = r.render_alert(_v(halt=_halt()))
    assert txt.startswith("<b>" + r.TXT["hl_on"]), txt[:80]
    assert "LUDP" in txt and "15:42" in txt


def test_halt_vua_mo_lai():
    txt = r.render_alert(_v(halt=_halt(code="T2", resumed=True, active=False,
                                      until="15:58")))
    assert r.TXT["hl_off"] in txt and "15:58" in txt
    assert r.TXT["hl_on"] not in txt


def test_khong_halt_thi_khong_co_dong_nao():
    for h in (None, {}):
        txt = r.render_alert(_v(halt=h))
        assert r.TXT["hl_on"] not in txt and r.TXT["hl_off"] not in txt


def test_halt_khong_bao_gio_bi_cat_khi_tin_qua_dai():
    """P_HALT cao nhat: tin dai co may cung phai con dong halt."""
    v = r.AlertView.from_scan({**BASE, "explain": "x " * 4000},
                              sec={**SEC, "detail": [
                                  {"form": f"F{i}", "age": i, "n": 1,
                                   "desc": "mô tả dài " * 30}
                                  for i in range(30)]},
                              detail=True, halt=_halt())
    txt = r.render_alert(v)
    assert len(txt) <= r.SAFE_LEN
    assert r.TXT["hl_on"] in txt


def test_halt_thieu_truong_van_render():
    """halts.view() doi khoa / thieu gio -> khong duoc nem loi giua phien."""
    for h in ({"code": "T1"}, {"label": "X"}, {"sev": 3},
              {"code": "T1", "since": None, "note": None}):
        _balanced(r.render_alert(_v(halt=h)))


def test_da_bo_muc_doan_halt_trong_risk():
    """Muc 'NGUY CO HALT' doan tu chg/rvol da bo — feed that thay the."""
    assert "r_halt" not in r.TXT
    txt = r.render_alert(_v(chg=0.85, rvol=66.0, sec=SEC))
    assert "HALT" not in txt.upper() or r.TXT["hl_on"] in txt


# ───────────────────────── 6. nut bam ─────────────────────────
def test_url_nut_dung_scheme():
    """Telegram chi nhan http(s):// va tg://. Scheme khac -> tu choi CA tin."""
    for kw in ({}, {"cik": None}, {"score": 7.1}, {"tracked": True}):
        v = _v(**kw, news_url="https://example.com")
        for row in r.render_keyboard(v)["inline_keyboard"]:
            assert 1 <= len(row) <= 3, row
            for b in row:
                assert "text" in b and b["text"]
                if "url" in b:
                    assert b["url"].startswith(("http://", "https://", "tg://")), b
                else:
                    assert len(b["callback_data"]) <= 64, b


def test_callback_data_khong_vuot_64_byte():
    """Telegram gioi han 64 byte; ma dai + arg de vuot ma khong ai biet."""
    for sym in ("A", "WETO", "ABCDEFGHIJ", "BRK.B"):
        for act in ("rf", "dtl", "trk"):
            c = r.cb(act, sym, "1")
            assert len(c.encode()) <= 64, c
            assert c.startswith(r.CB_VER)


def test_nut_theo_doi_chi_tu_muc_2():
    def has_track(v):
        return any(b.get("callback_data", "").startswith(f"{r.CB_VER}|trk")
                   for row in r.render_keyboard(v)["inline_keyboard"]
                   for b in row)
    assert not has_track(_v(score=7.1))
    assert has_track(_v(score=8.5))


def test_url_chatgpt_khong_qua_dai():
    u = r.ask_url(_v(sec=SEC))
    assert len(u) < 2000, len(u)
    assert u.startswith("https://")


# ───────────────────────── 7. degrade ─────────────────────────
def test_degrade_ha_cap_dan():
    txt = r.render_alert(_v(sec=SEC, detail=True))
    assert "expandable" not in r.degrade(txt, 1)
    assert "blockquote" not in r.degrade(txt, 2)
    assert "<" not in r.degrade(txt, 3)


def test_degrade_3_van_giu_noi_dung():
    """Ha cap het tag la duong cuoi truoc khi khong gui duoc gi."""
    plain = r.degrade(r.render_alert(_v(sec=SEC, halt=_halt())), 3)
    assert "WETO" in plain and "10.61" in plain
    assert r.TXT["hl_on"] in plain


if __name__ == "__main__":
    _util.main(globals())
