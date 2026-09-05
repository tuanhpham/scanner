"""news.py — phan loai tin va so tay cuon 4 gio (Phase 3).

Gia tri that cua module nay nam trong BANG TU KHOA, khong nam trong cach hien
thi. Sai mot dong trong bang la mot alert "+80% vi FDA" that ra la "+80% vi
dang ban them co phieu". Vi vay phan lon test o day la test phan loai.

Khong test phan mang: fetch() bi thay bang ham gia, hoac khong duoc goi.
"""
from __future__ import annotations

import time

import _util

nw = _util.need("news")


def _book(now: float | None = None):
    t = now if now is not None else time.time()
    b = nw.NewsBook()
    b.load(nw._sample(t), now=t)
    return b, t


# ───────────────────────── bang tu khoa ─────────────────────────
def test_pha_loang_la_nhom_quan_trong_nhat():
    """Ly do ton tai cua module. Moi cach viet thuong gap phai vao DILUTION."""
    for h in (
        "Weto Announces Pricing of $15.0 Million Registered Direct Offering",
        "Weto Prices $8.0 Million Underwritten Public Offering",
        "Weto Announces Proposed Public Offering of Common Stock",
        "Weto Enters Into Securities Purchase Agreement For $5M",
        "Weto Announces At-The-Market Sales Agreement",
        "Weto Closes Private Placement Of Convertible Notes",
        "Weto Announces Warrant Inducement Transaction",
        "Weto Files S-3 Shelf Registration Statement",
    ):
        k, _, risk, _ = nw.classify(h)
        assert k == "DILUTION", h
        assert risk >= nw.NEWS_RISK_MAX, h


def test_tin_vua_tot_vua_xau_phai_doc_theo_huong_xau():
    """Bay pho bien: gan tin FDA vao cung ban cong bo chao ban.

    Neu bang tu khoa xet nhom tot truoc thi ma nay se duoc gan nhan tich cuc
    va KHONG bi tru diem — dung cai bay ma Phase 3 muon chan.
    """
    k, _, risk, _ = nw.classify(
        "Trapz Announces FDA Clearance And Pricing Of $20 Million Offering")
    assert k == "DILUTION" and risk >= nw.NEWS_RISK_MAX


def test_thu_tu_nhom_xau_truoc_nhom_tot():
    """Khoa cung giao uoc: moi nhom risk > 0 phai dung TRUOC moi nhom risk 0."""
    risks = [g[2] for g in nw.GROUPS]
    xau = [i for i, r in enumerate(risks) if r > 0]
    tot = [i for i, r in enumerate(risks) if r == 0]
    assert xau and tot
    assert max(xau) < min(tot), "nhom xau phai nam truoc trong GROUPS"


def test_nhom_tot_khong_co_risk():
    """Nhom tot khong cong diem va khong tru diem: chua co so lieu de lam vay."""
    for k, _, risk, _, _ in nw.GROUPS:
        if k in ("BIO", "DEAL", "EARN"):
            assert risk == 0.0, k


def test_cac_nhom_con_lai():
    assert nw.classify("Announces 1-for-20 Reverse Stock Split")[0] == "SPLIT"
    assert nw.classify("Receives FDA Approval For Lead Candidate")[0] == "BIO"
    assert nw.classify("Reports Positive Phase 3 Topline Results")[0] == "BIO"
    assert nw.classify("Awarded $42M U.S. Army Contract")[0] == "DEAL"
    assert nw.classify("Signs Definitive Agreement To Acquire XYZ")[0] == "DEAL"
    assert nw.classify("Reports Record Third Quarter Revenue")[0] == "EARN"
    assert nw.classify("Files For Chapter 11 Protection")[0] == "BANKRUPT"
    assert nw.classify("Receives Nasdaq Deficiency Letter")[0] == "DELIST"


def test_khong_khop_thi_khong_bia_nhom():
    for h in ("", "Names Jane Doe As Chief Operating Officer",
              "To Present At Investor Conference",
              "Announces Participation In Webinar Series"):
        assert nw.classify(h)[0] is None, h


def test_tu_ngan_phai_co_ranh_gioi_tu():
    """"atm" khong co \\b se khop "atmosphere" -> gan nhan pha loang oan."""
    assert nw.classify("Unveils New ATMosphere Sensor Line")[0] is None
    assert nw.classify("Launches New ATM Program For $50M")[0] == "DILUTION"


def test_gach_ngang_dai_va_nhay_cong_van_khop():
    """Ban tin hay dung U+2011 / U+2019. Khong chuan hoa la truot het tu khoa."""
    assert nw.classify("Enters At‑The‑Market Agreement")[0] == "DILUTION"
    assert nw.classify("Announces Pricing Of Company’s Public Offering"
                       )[0] == "DILUTION"


def test_khong_phan_biet_chu_hoa_thuong():
    for h in ("ANNOUNCES PRICING OF OFFERING",
              "announces pricing of offering",
              "Announces Pricing Of Offering"):
        assert nw.classify(h)[0] == "DILUTION", h


# ───────────────────────── parse ─────────────────────────
def test_parse_mau():
    b, t = _book()
    assert b.by_sym, "mau phai ra ban ghi"
    assert b.view("WETO", t)["group"] == "DILUTION"
    assert b.view("BIOX", t)["group"] == "BIO"


def test_bo_bai_tong_hop_thi_truong():
    """Bai gan >4 ma la "10 Stocks Moving Today", khong phai catalyst rieng."""
    b, t = _book()
    for s in ("AAA", "BBB", "FFF"):
        assert s not in b.by_sym, s
    assert nw.MAX_SYMS <= 5


def test_chi_giu_4_gio():
    b, t = _book()
    assert "OLDIE" not in b.by_sym, "tin 5 gio truoc phai bi cat ngay khi parse"
    assert nw.WINDOW_H == 4


def test_bo_trung_theo_id():
    b, t = _book()
    n1 = sum(len(v) for v in b.by_sym.values())
    assert b.load(nw._sample(t), now=t) == 0
    assert sum(len(v) for v in b.by_sym.values()) == n1


def test_thieu_truong_khong_nem_loi():
    assert nw.parse({"news": [None, {}, {"headline": "x"},
                              {"created_at": "2020-01-01T00:00:00Z"},
                              {"headline": "y", "created_at": "hong"}]}) == []


def test_json_hong_tra_ve_rong():
    for bad in ("{khong phai json}", "", None, [], 7, {"khac": 1}):
        assert nw.parse(bad) == [], bad


def test_moc_thoi_gian_khong_co_z():
    """Alpaca tra '...Z'; neu doi sang '+00:00' hoac thieu tz thi van phai doc."""
    now = time.time()
    import datetime as dt
    d = dt.datetime.fromtimestamp(now - 300, dt.timezone.utc)
    for iso in (d.strftime("%Y-%m-%dT%H:%M:%SZ"),
                d.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                d.strftime("%Y-%m-%dT%H:%M:%S.123456Z")):
        r = nw.parse({"news": [{"id": 1, "headline": "Prices Offering",
                                "created_at": iso, "symbols": ["X"]}]}, now)
        assert len(r) == 1, iso
        assert abs(r[0]["ts"] - (now - 300)) < 2, iso


# ───────────────────────── view ─────────────────────────
def test_tin_xau_thang_tin_moi():
    """WETO co "Record Revenue" 5 phut truoc va "Pricing of Offering" 30 phut
    truoc. Neu lay tin moi nhat thi alert se khoe tin tot va che mat cai bay."""
    b, t = _book()
    v = b.view("WETO", t)
    assert v["group"] == "DILUTION" and v["age"] == 30 and v["n"] == 2


def test_khong_co_tin_khac_han_khong_biet_gi():
    """n=0 la KET LUAN ("chay khong ro ly do"). None la THIEU DU LIEU."""
    b, t = _book()
    v = b.view("AAPL", t)
    assert v is not None and v["n"] == 0 and v["group"] is None

    b.ts = t - nw.STALE - 1
    assert b.stale and not b.ok
    assert b.view("AAPL", t) is None and b.view("WETO", t) is None


def test_so_tay_chua_bao_gio_nhap_thi_im_lang():
    b = nw.NewsBook()
    assert not b.ok and b.view("WETO") is None


def test_khong_phan_loai_duoc_van_dua_tieu_de():
    b, t = _book()
    v = b.view("PLAIN", t)
    assert v["group"] is None and v["n"] == 1 and v["headline"]


def test_risk_va_danh_sach_ma_xau():
    b, t = _book()
    assert b.risk("WETO", t) >= nw.NEWS_RISK_MAX
    assert b.risk("BIOX", t) == 0.0
    assert b.risk("KHONGCO", t) == 0.0
    assert b.risky_syms() == ["DELIZ", "TRAPZ", "WETO"]


def test_view_co_du_khoa_cho_render():
    b, t = _book()
    for s in ("WETO", "AAPL"):
        v = b.view(s, t)
        for k in ("group", "label", "risk", "note", "headline", "source",
                  "url", "age", "n"):
            assert k in v, (s, k)


def test_ten_nguon_duoc_lam_dep():
    b, t = _book()
    assert b.view("WETO", t)["source"] == "Benzinga"
    assert nw.source_name("globenewswire") == "GlobeNewswire"
    assert nw.source_name("") == ""


# ───────────────────────── prune ─────────────────────────
def test_prune_bo_ma_ngoai_universe():
    b, t = _book()
    b.prune(keep={"WETO"}, now=t)
    assert list(b.by_sym) == ["WETO"]


def test_prune_keep_rong_khong_xoa_sach():
    """Ngoai gio quet universe rong. Prune luc do phai la viec vo hai."""
    b, t = _book()
    n = len(b.by_sym)
    b.prune(keep=set(), now=t)
    b.prune(keep=None, now=t)
    assert len(b.by_sym) == n


def test_prune_theo_tuoi():
    b, t = _book()
    b.prune(now=t + nw.WINDOW_H * 3600 + 60)
    assert not b.by_sym


def test_gioi_han_so_tin_moi_ma():
    """Mot ma ra 30 ban tin trong mot gio khong duoc lam phinh so tay."""
    import datetime as dt
    now = time.time()
    iso = dt.datetime.fromtimestamp(now - 60, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    b = nw.NewsBook()
    b.load({"news": [{"id": i, "headline": f"Weto Update Number {i}",
                      "created_at": iso, "symbols": ["W"],
                      "source": "benzinga"} for i in range(30)]}, now=now)
    assert len(b.by_sym["W"]) == nw.MAX_PER_SYM


# ───────────────────────── chiu loi ─────────────────────────
def test_loi_mang_giu_du_lieu_cu():
    b, t = _book()
    real = nw.fetch
    nw.fetch = lambda *a, **k: (_ for _ in ()).throw(OSError("no net"))
    try:
        assert b.refresh(now=t) == -1
        assert b.n_fail == 1 and b.err
        assert b.items("WETO", t), "loi mang khong duoc xoa tin cu"
    finally:
        nw.fetch = real


def test_lat_trang_khong_lap_vo_han():
    """API luon tra next_page_token -> phai dung o MAX_PAGES."""
    calls = []

    def fake(since=None, page_token="", limit=nw.LIMIT, timeout=15.0):
        calls.append(page_token)
        return {"news": [], "next_page_token": "con-nua"}

    real = nw.fetch
    nw.fetch = fake
    try:
        b = nw.NewsBook()
        b.refresh()
    finally:
        nw.fetch = real
    assert len(calls) == nw.MAX_PAGES


def test_khong_goi_mang_khi_chi_tra_cuu():
    """view()/items()/risk() phai la ham thuan: main.py moi quyet dinh goi mang."""
    b, t = _book()
    real = nw.fetch
    nw.fetch = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("tra cuu khong duoc goi mang"))
    try:
        b.view("WETO", t)
        b.items("WETO", t)
        b.risk("WETO", t)
        b.prune(keep={"WETO"}, now=t)
    finally:
        nw.fetch = real


# ───────────────────────── noi vao render / main ─────────────────────────
def test_render_ve_khoi_catalyst():
    r = _util.need("render")
    b, t = _book()
    v = r.AlertView(sym="WETO", px=10.0, chg=85.0, score=12.0,
                    news=b.view("WETO", t))
    txt = r.render_alert(v)
    assert r.TXT["h_news"] in txt
    assert "PHA LOÃNG" in txt
    assert v.news_risk >= nw.NEWS_RISK_MAX


def test_render_khoi_catalyst_nam_tren_khoi_vi_sao():
    """README: tin tuc quan trong hon giai thich diem."""
    r = _util.need("render")
    b, t = _book()
    v = r.AlertView(sym="WETO", px=10.0, chg=85.0, score=12.0, detail=True,
                    explain="RVOL 66x (+4.0)", news=b.view("WETO", t))
    txt = r.render_alert(v)
    assert txt.index(r.TXT["h_news"]) < txt.index(r.TXT["h_why"])


def test_render_khong_biet_thi_khong_ve():
    r = _util.need("render")
    v = r.AlertView(sym="WETO", px=10.0, chg=85.0, score=12.0, news=None)
    assert r.TXT["h_news"] not in r.render_alert(v)


def test_render_khong_co_tin_thi_noi_ro():
    r = _util.need("render")
    b, t = _book()
    txt = r.render_alert(r.AlertView(sym="AAPL", px=10.0, chg=85.0, score=12.0,
                                     news=b.view("AAPL", t)))
    assert r.TXT["h_news"] in txt and r.TXT["n_none"] in txt


def test_render_khong_them_emoji():
    """Quy uoc render.py: toi da 2 emoji/tin nhan (den mau + dau canh bao)."""
    r = _util.need("render")
    b, t = _book()
    for s in ("WETO", "BIOX", "AAPL", "PLAIN"):
        blk = "\n".join(r.render_news(
            r.AlertView(sym=s, px=10.0, chg=85.0, score=12.0,
                        news=b.view(s, t))))
        assert all(ord(c) < 0x2000 or c in "…·—" for c in blk), (s, blk)


def test_render_url_sai_scheme_khong_lam_chet_alert():
    """Mot the <a href> sai lam Telegram tra 400 va mat CA alert."""
    r = _util.need("render")
    v = r.AlertView(sym="X", px=1.0, chg=9.0, score=8.0,
                    news={"group": "BIO", "label": "L", "risk": 0.0, "note": "",
                          "headline": "Tieu de", "source": "Benzinga",
                          "url": "javascript:alert(1)", "age": 3, "n": 1})
    txt = r.render_alert(v)
    assert "javascript:" not in txt and "Tieu de" in txt


def test_render_cat_tieu_de_dai():
    r = _util.need("render")
    v = r.AlertView(sym="X", px=1.0, chg=9.0, score=8.0,
                    news={"group": None, "label": "", "risk": 0.0, "note": "",
                          "headline": "Weto " * 100, "source": "", "age": 0,
                          "url": "", "n": 1})
    assert "…" in r.render_alert(v)


def test_main_tru_diem_dung_mot_lan():
    """Tin pha loang va 424B5 la CUNG su kien: tru hai lan la phat trung."""
    src = (_util.ROOT / "main.py").read_text(encoding="utf-8")
    assert "news.NewsBook(" in src
    assert src.count("-= SEC_PENALTY") == 1, "chi duoc mot cho tru diem"
    assert "max(_sr, h[\"news_risk\"])" in src


def test_main_don_so_tay_theo_universe():
    src = (_util.ROOT / "main.py").read_text(encoding="utf-8")
    assert "_NB.prune(keep=" in src, "khong don thi RAM phinh ca ngay"
    assert "loop_news" in src


def test_events_ghi_nhom_tin():
    """Phase 1 can hai cot nay de danh gia xem nhan tin co dung khong."""
    ev = _util.need("events")
    assert "news_group" in ev.FIELDS and "news_risk" in ev.FIELDS


if __name__ == "__main__":
    _util.main(globals())
