"""watch.py - luat Tier 1 va bo chong spam.

Nam bat bien, xep theo do IM LANG cua loi neu no vo:

1. RVOL PHAI CHUAN HOA THEO GIO. Luc 10:00 mot ma binh thuong moi chay ~25%
   khoi luong ngay. Neu mau so la adv50 tho thi nguong 1.5 khong bao gio dat
   trong nua dau phien: bo loc khong bao loi, no chi khong bao gio kich.
2. KHONG BIET KHONG PHAI LA DAT, VA CUNG KHONG PHAI LA KHONG DAT. Thieu adv50,
   nguon khong cho khoi luong hop nhat, bao gia qua cu -> khong canh, nhung PHAI
   di vao `skip` de tin nhan mo phien noi ra.
3. CHE DO CHAN VAO LENH, KHONG CHAN CAT LO. DOWNTREND phai im lang voi moi luat
   vao lenh va van canh stop cho vi the dang mo. Vo theo huong nguoc lai = mua
   trong thi truong giam; vo theo huong nay = khong ai bao ve vi the.
4. RESTART GIUA PHIEN KHONG BAN LAI. Trang thai nam o khoa chinh (d, sym, rule)
   cua SQLite, khong nam o mot cai set trong RAM.
5. CANH BAO KHONG LOT QUA CHONG SPAM. Cooldown khong duoc chan `stop`, va tran
   Tier 2 khong duoc chan Tier 1 - ca hai la "mat mot canh bao that", kieu loi
   khong sua duoc sau khi da mat.
6. GHI TRUNG KHONG DUOC GIU KHOA. Duong "da co roi" chay moi vong quet ca phien
   tren MOT ket noi song ca phien, nen mot transaction bo mo o do la khoa ghi bi
   giu ca dem - va nguoi bao loi se la nightly, o mot file khac, vai gio sau.

Thuan stdlib: evaluate()/decide() la ham thuan, DB dung file tam.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import tempfile
import time
from pathlib import Path

import _util

w = _util.need("watch")
import config                                                    # noqa: E402
import positions                                                 # noqa: E402

NOW = dt.datetime(2026, 9, 25, 15, 0, tzinfo=dt.UTC)
D = "2026-09-25"
G = dict(config.INTRADAY)
SESS = {"state": "LIVE", "mso": 90, "minutes": 390, "d": D}

FULL = {"mode": "full", "allow_new": True}
DOWN = {"mode": "stop_only", "allow_new": False}
NO_POS = {"known": False, "rows": {}, "note": "x"}


def row(**kw) -> dict:
    """Mot dong watchlist da co ke hoach lenh."""
    return {"sym": "NVDA", "ref_close": 100.0, "pivot": 101.0, "atr_pct": 0.02,
            "sector": "Tech", "quality": [], "trigger": 102.0, "stop": 97.0,
            "target": 112.0, "stop_pct": 0.049, "risk_pct": 0.01,
            "size_pct": 0.2, "adv50": 1_000_000.0, "px": 100.0, **kw}


def q(px=102.5, vol=400_000, op=100.5, sym="NVDA", age=300.0, **kw) -> dict:
    """Mot bao gia dung hinh dang cua quotes.build()."""
    return {sym: {"sym": sym, "px": px, "open": op, "hi": max(px, op),
                  "lo": min(px, op), "vol": vol, "ts": NOW.isoformat(),
                  "age_sec": age, "n_bars": 60, "src": "t", "vol_ok": True,
                  "err": None, **kw}}


def held(sym="NVDA", stops=(97.0,), cur="USD", shares=10) -> dict:
    """Vi the dang mo, di qua positions.parse() that chu khong dung tay."""
    return positions.parse(
        {"rows": [{"sym": sym, "shares": shares, "avgCost": 100.0, "cur": cur,
                   "stops": list(stops), "accts": ["A"]}]},
        int(NOW.timestamp() * 1000) - 60_000, now=NOW)


def rules(cands) -> list[str]:
    return sorted(a["rule"] for a in cands)


def why(skip, sym="NVDA") -> str:
    return " | ".join(s[1] for s in skip if s[0] == sym)


def db() -> Path:
    p = Path(tempfile.mkdtemp()) / "t.db"
    return p


# ────────────── 1. RVol chuan hoa theo gio ──────────────
def test_rvol_lay_mau_so_tu_duong_cong_chu_khong_phai_adv50_tho():
    """BAT BIEN 1. 400k tren adv50 1M luc phut 90: tho la 0.4 (khong bao gio
    kich), chuan hoa theo ~25% khoi luong ky vong la ~1.6."""
    rv = w.rvol_adj(400_000, 1_000_000, SESS)
    assert rv is not None and 1.5 < rv < 1.7, rv
    assert 400_000 / 1_000_000 < G["min_rvol_adj"], "day la cho bo loc tho im lang"


def test_cung_mot_khoi_luong_cang_muon_cang_it_y_nghia():
    som = w.rvol_adj(400_000, 1_000_000, {**SESS, "mso": 30})
    muon = w.rvol_adj(400_000, 1_000_000, {**SESS, "mso": 300})
    assert som > muon > 0, (som, muon)


def test_nua_phien_duoc_co_gian():
    """Phien nua ngay dai 210 phut. Phut 105 la giua phien, khong phai 27%."""
    a = w.rvol_adj(500_000, 1_000_000, {**SESS, "mso": 105, "minutes": 210})
    b = w.rvol_adj(500_000, 1_000_000, {**SESS, "mso": 195, "minutes": 390})
    assert abs(a - b) < 1e-9, (a, b)


def test_premarket_khong_dung_duong_cong_trong_phien():
    pre = w.rvol_adj(100_000, 1_000_000, {**SESS, "state": "PREMARKET", "mso": -60})
    assert pre is not None and pre > 1, pre


def test_khong_biet_adv50_tra_ve_None_chu_khong_phai_0():
    """0.0 doc nhu mot ket luan ("khoi luong rat thap"), None la "khong biet".
    Ca hai deu duoi nguong, nen chi co the phan biet o day."""
    for bad in (None, 0, -1, "abc"):
        assert w.rvol_adj(400_000, bad, SESS) is None, bad
    assert w.rvol_adj(None, 1_000_000, SESS) is None


# ────────────── 2. khong biet -> khong canh, nhung noi ra ──────────────
def test_cham_diem_vao_kem_rvol_dat_thi_canh():
    c, _ = w.evaluate([row()], q(), FULL, NO_POS, SESS, G)
    assert rules(c) == ["trigger"] and c[0]["tier"] == 1, c
    assert c[0]["fields"]["stop"] == 97.0 and c[0]["fields"]["target"] == 112.0
    assert c[0]["fields"]["size_pct"] == 0.2, "ke hoach phai di kem alert"


def test_chua_cham_diem_vao_thi_im():
    assert w.evaluate([row()], q(px=101.9, vol=9_000_000), FULL, NO_POS,
                      SESS, G)[0] == []


def test_cham_diem_vao_nhung_khoi_luong_khong_dat_thi_im():
    c, sk = w.evaluate([row()], q(px=102.5, vol=50_000), FULL, NO_POS, SESS, G)
    assert c == [] and why(sk) == "", "khong dat nguong la mot KET LUAN, khong phai skip"


def test_thieu_adv50_thi_khong_canh_va_phai_noi_ra():
    """BAT BIEN 2."""
    c, sk = w.evaluate([row(adv50=None)], q(), FULL, NO_POS, SESS, G)
    assert c == [] and "adv50" in why(sk), (c, sk)


def test_nguon_khong_cho_khoi_luong_hop_nhat_thi_khong_canh_va_noi_ra():
    """Feed IEX cho ~2% khoi luong: RVol se luon ~0.02 va nguong khong bao gio
    kich. Mot bo loc khong bao gio kich khong bao loi - no chi im lang."""
    c, sk = w.evaluate([row()], q(vol_ok=False), FULL, NO_POS, SESS, G)
    assert c == [] and "hợp nhất" in why(sk), (c, sk)


def test_bao_gia_qua_cu_thi_khong_canh_va_noi_ro_tre_bao_nhieu():
    c, sk = w.evaluate([row()], q(age=3600), FULL, NO_POS, SESS, G)
    assert c == [] and "quá cũ" in why(sk) and "phút" in why(sk), sk


def test_nguong_tuoi_lay_tu_config():
    gia_han = float(G["max_quote_age_sec"])
    assert w.evaluate([row()], q(age=gia_han - 1), FULL, NO_POS, SESS, G)[0]
    assert w.evaluate([row()], q(age=gia_han + 1), FULL, NO_POS, SESS, G)[0] == []


def test_khong_co_bao_gia_hoac_co_loi_thi_hien_ra():
    c, sk = w.evaluate([row()], {}, FULL, NO_POS, SESS, G)
    assert c == [] and "không có báo giá" in why(sk), sk
    fr = q()
    fr["NVDA"]["err"] = "không có dữ liệu trong phiên"
    c2, sk2 = w.evaluate([row()], fr, FULL, NO_POS, SESS, G)
    assert c2 == [] and "không có dữ liệu" in why(sk2), sk2


def test_moi_ly_do_skip_deu_la_tieng_viet_co_dau():
    """`skip` di thang vao tin nhan mo phien. Mot chuoi ASCII o day la mot dong
    tieng Anh lot vao giua tin nhan tieng Viet."""
    for fr in (q(age=3600), q(vol_ok=False), {}):
        for _, s in w.evaluate([row(adv50=None)], fr, FULL, NO_POS, SESS, G)[1]:
            assert s != s.encode("ascii", "ignore").decode(), s


# ────────────── 2b. gia mo cua, khong phai lan quet truoc ──────────────
def test_mo_cua_da_o_tren_diem_vao_thi_khong_goi_la_cham_diem_vao():
    """Gia da di qua truoc khi minh co co hoi. Goi do la "cham diem vao" se lam
    minh dat lenh o mot muc da bo lai phia sau."""
    c, sk = w.evaluate([row()], q(px=103.0, vol=9_000_000, op=102.5), FULL,
                       NO_POS, SESS, G)
    assert c == [] and "mở cửa đã ở trên" in why(sk), (c, sk)


def test_kiem_bang_gia_mo_cua_chu_khong_bang_bien_trong_ram():
    """Cung mot khung bao gia phai cho ra cung mot ket luan sau khi restart. Neu
    "cham" duoc do bang lan quet truoc thi ket luan phu thuoc vao bien RAM, va
    bien do mat khi process chet - mat theo dung cai cach khong ai thay."""
    fr = q(px=102.5, op=100.5)
    a = w.evaluate([row()], fr, FULL, NO_POS, SESS, G)[0]
    b = w.evaluate([row()], fr, FULL, NO_POS, SESS, G)[0]
    assert rules(a) == rules(b) == ["trigger"]


def test_gap_len_qua_nguong_thi_canh_tier_1():
    c, _ = w.evaluate([row()], q(px=104.5, op=104.0), FULL, NO_POS, SESS, G)
    assert rules(c) == ["gap"] and c[0]["tier"] == 1, c
    assert "gap lên 4" in c[0]["detail"], c[0]["detail"]
    assert c[0]["fields"]["trigger"] == 102.0, "ke hoach phai di kem"


def test_gap_xuong_cung_canh():
    """Gap -4% co the da xuyen stop truoc khi phien bat dau. Chi canh gap len la
    bo dung nua nguy hiem."""
    c, _ = w.evaluate([row()], q(px=95.0, op=96.0), FULL, NO_POS, SESS, G)
    assert rules(c) == ["gap"] and "gap xuống 4" in c[0]["detail"], c


def test_gap_duoi_nguong_thi_im():
    assert rules(w.evaluate([row()], q(px=101.0, op=102.0 * 0.99 - 0.01),
                            FULL, NO_POS, SESS, G)[0]) == []


def test_thieu_nen_quyet_dinh_thi_khong_doan_gap():
    c, sk = w.evaluate([row(ref_close=None)], q(px=104.5, op=101.0), FULL,
                       NO_POS, SESS, G)
    assert rules(c) == ["trigger"] and "không tính được gap" in why(sk), (c, sk)


# ────────────── 3. che do chan vao lenh, khong chan cat lo ──────────────
def test_DOWNTREND_im_lang_voi_moi_luat_vao_lenh():
    """BAT BIEN 3. Ca trigger va gap deu la thong tin de VAO LENH."""
    c, _ = w.evaluate([row()], q(px=104.5, op=104.0), DOWN, NO_POS, SESS, G)
    assert c == [], c


def test_DOWNTREND_van_canh_cat_lo_cho_vi_the_dang_mo():
    c, _ = w.evaluate([], q(px=96.0, sym="AAPL"), DOWN, held("AAPL"), SESS, G)
    assert rules(c) == ["stop"] and c[0]["val"] == 97.0, c
    assert "xuyên mức cắt lỗ" in c[0]["detail"]


def test_che_do_manage_chi_canh_gap_cua_ma_DANG_GIU():
    m = {"mode": "manage", "allow_new": False}
    c1, _ = w.evaluate([row()], q(px=104.5, op=104.0), m, NO_POS, SESS, G)
    assert c1 == [], "gap cua ma khong giu la thong tin de vao lenh"
    c2, _ = w.evaluate([row()], q(px=104.5, op=104.0), m, held("NVDA"), SESS, G)
    assert "gap" in rules(c2), c2
    assert "trigger" not in rules(c2), "manage khong duoc vao lenh moi"


def test_che_do_khong_biet_thi_chi_con_stop():
    """watchlist.gate() tra mode "stop_only" khi khong doc duoc regime. Mot mode
    la (hoac rong) cung phai cho ra dung the - khong duoc mo het cua."""
    for g in ({}, {"mode": "xxx"}, {"mode": None}):
        c, _ = w.evaluate([row()], q(), g, NO_POS, SESS, G)
        assert c == [], g


def test_moi_che_do_deu_canh_cat_lo():
    for mode in ("full", "revert", "manage", "stop_only"):
        c, _ = w.evaluate([], q(px=96.0, sym="AAPL"), {"mode": mode},
                          held("AAPL"), SESS, G)
        assert rules(c) == ["stop"], mode


def test_vi_the_khong_nam_trong_danh_sach_van_duoc_canh_stop():
    """Va chi DUNG viec do: khong luat vao lenh nao chay tren mot ma khong co
    trong danh sach cua dem qua. Do la thu chan co phieu rac quay lai."""
    c, _ = w.evaluate([], q(px=96.0, sym="AAPL", vol=9_000_000), FULL,
                      held("AAPL"), SESS, G)
    assert rules(c) == ["stop"], c


def test_vi_the_EUR_khong_bi_canh_bang_gia_USD():
    """positions.py da dung ngoai; day la kiem rang watch.py khong lach qua."""
    c, _ = w.evaluate([], q(px=96.0, sym="AAPL"), DOWN, held("AAPL", cur="EUR"),
                      SESS, G)
    assert c == [], c


def test_vi_the_khong_co_stop_khong_sinh_ra_canh_bao_nao():
    c, _ = w.evaluate([], q(px=96.0, sym="AAPL"), DOWN, held("AAPL", stops=()),
                      SESS, G)
    assert c == []


def test_muc_bi_bao_la_muc_cao_nhat_bi_xuyen():
    c, _ = w.evaluate([], q(px=90.0, sym="AAPL"), DOWN,
                      held("AAPL", stops=(85.0, 112.0)), SESS, G)
    assert c[0]["val"] == 112.0 and c[0]["fields"]["n_hit"] == 1, c
    # Xuyen ca hai muc thi phai dem du - so luong di vao tin nhan.
    c2, _ = w.evaluate([], q(px=80.0, sym="AAPL"), DOWN,
                       held("AAPL", stops=(85.0, 112.0)), SESS, G)
    assert c2[0]["val"] == 112.0 and c2[0]["fields"]["n_hit"] == 2, c2
    assert "2 mức bị xuyên" in c2[0]["detail"], c2[0]["detail"]


def test_canh_bao_mang_theo_dau_moc_va_do_tre():
    """Yeu cau ro rang cua prompt 2: moi tin nhan phai co dau moc bao gia va do
    tre. Neu evaluate() khong chuyen tiep thi render khong the them vao."""
    for c in (w.evaluate([row()], q(), FULL, NO_POS, SESS, G)[0]
              + w.evaluate([], q(px=96.0, sym="AAPL"), DOWN, held("AAPL"),
                           SESS, G)[0]):
        assert c["ts"] == NOW.isoformat() and c["age_sec"] == 300.0, c


# ────────────── 4. restart giua phien khong ban lai ──────────────
def test_moi_ma_moi_luat_mot_lan_moi_phien():
    st = {"fired": {}, "last": {}, "n2": 0}
    c, _ = w.evaluate([row()], q(), FULL, NO_POS, SESS, G)
    assert len(w.decide(c, st, NOW, SESS, G)[0]) == 1
    gui, giu = w.decide(c, st, NOW, SESS, G)
    assert gui == [] and "đã cảnh báo" in giu[0][1], giu


def test_trang_thai_doc_lai_tu_DB_nen_restart_khong_ban_lai():
    """BAT BIEN 4. Day la ca ly do bang `watch_alert` ton tai."""
    p = db()
    c = w.con(p)
    cands, _ = w.evaluate([row()], q(), FULL, NO_POS, SESS, G)
    gui, _ = w.decide(cands, w.load_state(c, D), NOW, SESS, G)
    assert len(gui) == 1
    assert w.record(c, D, gui[0], NOW) is True
    c.close()

    # process moi, ket noi moi, khong con gi trong RAM.
    c2 = w.con(p)
    st = w.load_state(c2, D)
    assert ("NVDA", "trigger") in st["fired"], st
    assert w.decide(cands, st, NOW, SESS, G)[0] == [], "restart khong duoc ban lai"
    c2.close()


def test_khoa_chinh_chan_ghi_trung_chu_khong_phai_mot_dong_python():
    p = db()
    c = w.con(p)
    a = {"rule": "trigger", "tier": 1, "sym": "NVDA", "px": 102.5, "detail": "x"}
    assert w.record(c, D, a, NOW) is True
    assert w.record(c, D, a, NOW) is False, "lan hai phai bi SQLite chan"
    assert len(w.today_rows(c, D)) == 1
    c.close()


def test_sang_hom_sau_thi_ban_lai_duoc():
    """Khoa la (d, sym, rule): "mot lan moi PHIEN", khong phai mot lan mai mai."""
    p = db()
    c = w.con(p)
    a = {"rule": "trigger", "tier": 1, "sym": "NVDA", "px": 102.5, "detail": "x"}
    assert w.record(c, D, a, NOW) is True
    assert w.record(c, "2026-09-28", a, NOW) is True
    assert w.load_state(c, "2026-09-28")["fired"] == {("NVDA", "trigger"):
                                                      NOW.isoformat(timespec="seconds")}
    c.close()


def test_tin_mo_phien_chi_gui_mot_lan_ke_ca_sau_restart():
    p = db()
    c = w.con(p)
    assert w.once(c, D, "open", NOW) is True
    c.close()
    c2 = w.con(p)
    assert w.once(c2, D, "open", NOW) is False, "restart khong duoc gui lai tin mo phien"
    assert w.once(c2, D, "summary", NOW) is True, "tong ket la tin khac"
    c2.close()


def test_tin_mot_lan_khong_lam_ban_bang_canh_bao():
    p = db()
    c = w.con(p)
    w.once(c, D, "open", NOW)
    w.record(c, D, {"rule": "trigger", "tier": 1, "sym": "NVDA", "px": 1.0,
                    "detail": "x"}, NOW)
    assert [r["sym"] for r in w.today_rows(c, D)] == ["NVDA"], w.today_rows(c, D)
    st = w.load_state(c, D)
    assert "" not in st["last"], "tin mo phien khong duoc tinh vao cooldown"
    c.close()


def test_moi_tin_mot_lan_deu_doc_lap_va_deu_chi_mot_lan():
    """`src_down` nam trong day chu khong o mot bien rieng: mot tin "nguon chet"
    gui lai moi vong la 390 tin mot phien, va bo han thi mot API chet giong het
    mot phien binh thuong."""
    p = db()
    c = w.con(p)
    for k in w.ONCE:
        assert w.once(c, D, k, NOW) is True, k
        assert w.once(c, D, k, NOW) is False, k
    assert set(w.ONCE) == {"open", "summary", "src_down"}
    c.close()


def test_kind_la_khong_hop_le_thi_bao_ngay_chu_khong_ghi_bua():
    p = db()
    c = w.con(p)
    try:
        w.once(c, D, "trigger", NOW)
        raise AssertionError("phai nem ValueError")
    except ValueError:
        pass
    c.close()


def test_ghi_duoc_tren_DB_da_ton_tai_va_khong_xoa_gi():
    p = db()
    c = w.con(p)
    w.record(c, D, {"rule": "gap", "tier": 1, "sym": "AAA", "px": 1.0,
                    "detail": "x"}, NOW)
    c.close()
    c2 = w.con(p)          # ensure lai lan hai: CREATE IF NOT EXISTS
    assert len(w.today_rows(c2, D)) == 1
    c2.close()


# ────────────── 6. ghi trung khong giu khoa ──────────────
def test_ghi_trung_khong_de_lai_transaction_giu_khoa():
    """BAT BIEN 6. Loi that da xay ra: `once()`/`record()` goi execute() roi
    commit(), nen khi khoa chinh chan lai thi IntegrityError bay ra TRUOC
    commit() va transaction ghi o lai. watchd.py goi `once(c, d, "open")` moi
    vong quet, tu vong thu hai tra False, nen no giu khoa ghi lien tuc - va
    `bars.sync` cua nightly do `database is locked` sau 30 giay dung dem do.
    """
    p = db()
    c = w.con(p)
    a = {"rule": "gap", "tier": 1, "sym": "AAA", "px": 1.0, "detail": "x"}
    assert w.once(c, D, "open", NOW) is True
    assert w.once(c, D, "open", NOW) is False
    assert not c.in_transaction, "tin mot-lan trung de lai transaction mo"
    assert w.record(c, D, a, NOW) is True
    assert w.record(c, D, a, NOW) is False
    assert not c.in_transaction, "canh bao trung de lai transaction mo"

    # Cau hoi that su quan trong, va la cai ma `in_transaction` chi la dai dien:
    # nguoi ghi KHAC con xin duoc khoa khong. BEGIN IMMEDIATE la dung thu ma
    # bars.save()/nightly.save_run() can.
    o = sqlite3.connect(str(p), timeout=2, isolation_level=None)
    try:
        o.execute("PRAGMA busy_timeout=2000")
        t0 = time.time()
        o.execute("BEGIN IMMEDIATE")
        o.execute("ROLLBACK")
        assert time.time() - t0 < 1.0, "phai lay duoc khoa ngay, khong phai doi"
    finally:
        o.close()
        c.close()


# ────────────── 5. canh bao khong lot qua chong spam ──────────────
def test_muoi_phut_dau_phien_giu_tat_ca_lai():
    """Gia mo cua thuong la mot cai rang cua; mot canh bao o do hay noi ve mot
    muc gia khong ton tai qua 60 giay. `stop` cung khong duoc mien: neu gia that
    su da xuyen thi 10 phut nua no van xuyen."""
    c, _ = w.evaluate([row()], q(), FULL, NO_POS, SESS, G)
    gui, giu = w.decide(c, {"fired": {}, "last": {}, "n2": 0}, NOW,
                        {**SESS, "mso": 3}, G)
    assert gui == [] and "đầu phiên" in giu[0][1], giu


def test_giu_lai_khong_phai_la_mat_han():
    """Sau 10 phut, cung mot dieu kien phai duoc gui. Neu `fired` bi danh dau
    luc bi giu thi canh bao mat vinh vien."""
    c, _ = w.evaluate([row()], q(), FULL, NO_POS, SESS, G)
    st = {"fired": {}, "last": {}, "n2": 0}
    assert w.decide(c, st, NOW, {**SESS, "mso": 3}, G)[0] == []
    assert st["fired"] == {}, "bi giu thi khong duoc danh dau la da gui"
    assert len(w.decide(c, st, NOW, {**SESS, "mso": 12}, G)[0]) == 1


def test_cooldown_chan_luat_khac_cung_mot_ma():
    c, _ = w.evaluate([row()], q(px=104.5, op=104.0), FULL, NO_POS, SESS, G)
    st = {"fired": {}, "last": {"NVDA": NOW.timestamp() - 60}, "n2": 0}
    gui, giu = w.decide(c, st, NOW, SESS, G)
    assert gui == [] and "thời gian nghỉ" in giu[0][1], giu


def test_cooldown_KHONG_chan_cat_lo():
    """BAT BIEN 5. Mot canh bao cat lo bi mot canh bao gap cua 10 phut truoc
    chan lai la kieu loi khong the bien minh."""
    c, _ = w.evaluate([], q(px=96.0, sym="AAPL"), DOWN, held("AAPL"), SESS, G)
    st = {"fired": {}, "last": {"AAPL": NOW.timestamp() - 10}, "n2": 0}
    assert len(w.decide(c, st, NOW, SESS, G)[0]) == 1


def test_cooldown_het_thi_gui_lai_duoc():
    c, _ = w.evaluate([row()], q(px=104.5, op=104.0), FULL, NO_POS, SESS, G)
    st = {"fired": {}, "last": {"NVDA": NOW.timestamp() - G["cooldown_sec"] - 1},
          "n2": 0}
    assert len(w.decide(c, st, NOW, SESS, G)[0]) == 1


def test_hai_luat_cung_mot_vong_chiu_cooldown_cua_nhau():
    """Cap nhat trang thai phai xay ra TRONG vong, khong doi vong sau."""
    c = [{"rule": "gap", "tier": 1, "sym": "NVDA", "px": 104.0, "detail": "a"},
         {"rule": "trigger", "tier": 1, "sym": "NVDA", "px": 104.0, "detail": "b"}]
    gui, giu = w.decide(c, {"fired": {}, "last": {}, "n2": 0}, NOW, SESS, G)
    assert len(gui) == 1 and "thời gian nghỉ" in giu[0][1], (gui, giu)


def test_tran_tier_2_chan_tier_2():
    t2 = [{"rule": "x", "tier": 2, "sym": f"A{i}", "px": 1.0, "detail": ""}
          for i in range(G["tier2_cap"] + 4)]
    gui, giu = w.decide(t2, {"fired": {}, "last": {}, "n2": 0}, NOW, SESS, G)
    assert len(gui) == G["tier2_cap"] and len(giu) == 4, (len(gui), len(giu))
    assert "trần" in giu[0][1]


def test_tran_tier_2_KHONG_chan_tier_1():
    """BAT BIEN 5. Tier 1 la cac muc gia minh da quyet dinh tu dem truoc. Mot
    tran cho chung nghia la ke hoach bi bo qua vi qua nhieu tin nhan phu."""
    c, _ = w.evaluate([row()], q(), FULL, NO_POS, SESS, G)
    st = {"fired": {}, "last": {}, "n2": 999}
    assert len(w.decide(c, st, NOW, SESS, G)[0]) == 1


def test_tier_1_duoc_xet_truoc_tier_2():
    """Chi quan trong khi tran sap day, nhung luc do no quan trong that."""
    c = [{"rule": "x", "tier": 2, "sym": "AAA", "px": 1.0, "detail": ""},
         {"rule": "stop", "tier": 1, "sym": "ZZZ", "px": 1.0, "detail": ""}]
    gui, _ = w.decide(c, {"fired": {}, "last": {}, "n2": 0}, NOW, SESS, G)
    assert [a["sym"] for a in gui] == ["ZZZ", "AAA"], gui


def test_tran_dem_tu_DB_chu_khong_tu_dau_lai_moi_lan_restart():
    p = db()
    c = w.con(p)
    for i in range(3):
        w.record(c, D, {"rule": f"r{i}", "tier": 2, "sym": f"A{i}", "px": 1.0,
                        "detail": ""}, NOW)
    assert w.load_state(c, D)["n2"] == 3
    c.close()


def test_nguong_chong_spam_lay_tu_config_chu_khong_viet_cung():
    c, _ = w.evaluate([row()], q(), FULL, NO_POS, SESS, G)
    st = {"fired": {}, "last": {"NVDA": NOW.timestamp() - 60}, "n2": 0}
    assert w.decide(c, dict(st), NOW, SESS, G)[0] == []
    # Nguong 0 / thieu khoa = khong chong spam, chu khong phai chan sach.
    assert len(w.decide(c, dict(st), NOW, SESS, {})[0]) == 1


def test_khong_co_gi_de_canh_thi_khong_tra_ve_gi_ca():
    """Im lang la mac dinh. decide([]) phai la mot no-op tuyet doi."""
    assert w.decide([], {"fired": {}, "last": {}, "n2": 0}, NOW, SESS, G) == ([], [])


# ────────────── 6. rac vao, khong chet ra ──────────────
def test_dong_watchlist_thieu_diem_vao_bi_bo_qua_khong_nem():
    c, _ = w.evaluate([row(trigger=None), {"sym": ""}, row(sym="B", trigger="x")],
                      {**q(), **q(sym="B")}, FULL, NO_POS, SESS, G)
    assert c == [], c


def test_danh_sach_rong_khong_sinh_ra_gi():
    assert w.evaluate([], {}, FULL, NO_POS, SESS, G) == ([], [])


def test_khong_doc_duoc_vi_the_thi_khong_canh_stop_nhung_khong_chet():
    c, _ = w.evaluate([row()], q(), FULL, {"known": False, "rows": {}}, SESS, G)
    assert rules(c) == ["trigger"], c


if __name__ == "__main__":
    _util.main(globals())
