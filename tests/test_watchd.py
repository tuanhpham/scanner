"""watchd.py - vong quet trong phien.

Nam bat bien, xep theo do IM LANG cua loi neu no vo:

1. GUI HONG THI KHONG DUOC GHI "DA CANH BAO". Ghi truoc khi gui thi mot tin
   nhan mat mang la mot canh bao mat vinh vien, va DB noi rang no da den.
2. GUI DUOC THI PHAI GHI. Nguoc lai la 390 tin nhan giong nhau mot phien.
3. VI THE DANG MO PHAI CO TRONG DANH SACH LAY GIA. Mot ma da ban khoi watchlist
   dem qua nhung con trong tai khoan thi khong ai canh cat lo cho no.
4. DOC DAU VAO HONG MOT PHAN THI VAN CHAY, VA NOI RA. Khong doc duoc danh sach
   roi chay tiep voi danh sach rong ma im lang thi giong het mot ngay khong co
   co hoi nao.
5. TIEN TRINH CHET PHAI CO TIN NHAN. Do la yeu cau ro rang cua prompt 2.

Khong mang, khong Telegram: nguon bao gia la quotes.FixtureProvider va `send`
duoc tiem vao. DB la file tam.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sqlite3
import tempfile
from pathlib import Path

import _util

wd = _util.need("watchd")
import positions                                                 # noqa: E402
import quotes                                                    # noqa: E402
import watch                                                     # noqa: E402

NOW = dt.datetime(2026, 9, 25, 15, 0, tzinfo=dt.UTC)
D = "2026-09-25"
SESS = {"state": "LIVE", "mso": 90, "minutes": 390, "d": D}


def row(**kw) -> dict:
    return {"sym": "NVDA", "ref_close": 100.0, "trigger": 102.0, "stop": 97.0,
            "target": 112.0, "adv50": 1_000_000.0, "size_pct": 0.2,
            "stop_pct": 0.049, "risk_pct": 0.01, "sector": "Tech", **kw}


def ctx(**kw) -> dict:
    d = {"day": D, "gate": {"mode": "full"}, "pos": None, "warn": [],
         "loaded": NOW, "rows": [row()], "url": ""}
    d.update(kw)
    return d


def prov(px=102.5, vol=400_000, op=100.5, sym="NVDA") -> quotes.FixtureProvider:
    return quotes.FixtureProvider([{"now": NOW.isoformat(), "rows": [
        {"sym": sym, "ts": "2026-09-25T14:55:00+00:00", "o": op, "h": max(px, op),
         "l": min(px, op), "c": px, "v": vol}]}])


def db() -> sqlite3.Connection:
    return watch.con(Path(tempfile.mkdtemp()) / "t.db")


class Sink:
    """`send` gia. `ok=False` mo phong mat mang / Telegram tu choi."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.msgs: list[tuple[str, bool]] = []

    async def __call__(self, txt: str, loud: bool = False) -> bool:
        self.msgs.append((txt, loud))
        return self.ok


def tick(c, p, x, send, sess=None, now=NOW) -> dict:
    return asyncio.run(wd.tick(c, p, x, sess or SESS, now, send))


# ────────────── 1-2. gui va ghi, dung thu tu ──────────────
def test_cham_diem_vao_thi_gui_mot_tin_va_ghi_lai():
    c, s = db(), Sink()
    r = tick(c, prov(), ctx(), s)
    assert r["sent"] == 1 and len(s.msgs) == 1, (r, s.msgs)
    assert "NVDA" in s.msgs[0][0] and s.msgs[0][1] is True, "Tier 1 phai keu"
    assert [x["rule"] for x in watch.today_rows(c, D)] == ["trigger"]
    c.close()


def test_vong_sau_khong_gui_lai():
    """BAT BIEN 2."""
    c, s = db(), Sink()
    p = prov()
    tick(c, p, ctx(), s)
    r = tick(c, p, ctx(), s)
    assert r["sent"] == 0 and r["held"] == 1 and len(s.msgs) == 1, (r, s.msgs)
    c.close()


def test_gui_hong_thi_KHONG_ghi_va_vong_sau_thu_lai():
    """BAT BIEN 1. Gui trung mot tin la kho chiu; mat mot canh bao cat lo thi
    khong sua duoc sau khi da mat."""
    c = db()
    hong, tot = Sink(ok=False), Sink()
    p = prov()
    r = tick(c, p, ctx(), hong)
    assert r["sent"] == 0 and len(hong.msgs) == 1, r
    assert watch.today_rows(c, D) == [], "gui hong thi khong duoc ghi"
    assert tick(c, p, ctx(), tot)["sent"] == 1
    assert len(watch.today_rows(c, D)) == 1
    c.close()


def test_khong_co_gi_de_canh_thi_khong_gui_gi():
    """Im lang la mac dinh: mot vong quet binh thuong khong sinh ra tin nhan."""
    c, s = db(), Sink()
    r = tick(c, prov(px=101.0), ctx(), s)
    assert r["sent"] == 0 and s.msgs == [], (r, s.msgs)
    c.close()


def test_danh_sach_rong_va_khong_co_vi_the_thi_khong_goi_nguon_gia():
    c, s = db(), Sink()
    p = prov()
    r = tick(c, p, ctx(rows=[]), s)
    assert r == {"n_sym": 0, "sent": 0, "held": 0, "skip": 0, "err": 0,
                 "cands": 0}, r
    assert p.i == 0, "khong co ma nao thi khong duoc goi nguon bao gia"
    c.close()


def test_che_do_stop_only_khong_gui_canh_bao_vao_lenh():
    c, s = db(), Sink()
    r = tick(c, prov(), ctx(gate={"mode": "stop_only"}), s)
    assert r["sent"] == 0 and s.msgs == [], (r, s.msgs)
    c.close()


def test_ma_vang_mat_duoc_dem_vao_err_chu_khong_bien_mat():
    """`err` la thu vong ngoai dung de biet nguon co chet khong. Neu mot ma vang
    mat khong hien o dau thi mot API tra ve rong se trong y nhu mot phien khong
    co gi dat dieu kien."""
    c, s = db(), Sink()
    r = tick(c, quotes.FixtureProvider([]), ctx(), s)
    assert r["n_sym"] == 1 and r["err"] == 1 and r["sent"] == 0, r
    c.close()


def test_canh_bao_mang_theo_ten_nguon():
    c, s = db(), Sink()
    tick(c, prov(), ctx(), s)
    assert "nguồn fixture" in s.msgs[0][0], s.msgs[0][0]
    c.close()


# ────────────── 3. vi the dang mo ──────────────
def test_vi_the_dang_mo_duoc_gop_vao_danh_sach_lay_gia():
    """BAT BIEN 3."""
    p = positions.parse({"rows": [{"sym": "AAPL", "shares": 1, "cur": "USD",
                                   "stops": [90]}]},
                        int(NOW.timestamp() * 1000), now=NOW)
    assert wd.syms_of(ctx(pos=p)) == ["AAPL", "NVDA"]
    assert wd.syms_of(ctx(rows=[], pos=p)) == ["AAPL"]


def test_khong_doc_duoc_vi_the_thi_chi_con_danh_sach():
    assert wd.syms_of(ctx(pos=positions.parse(None, None))) == ["NVDA"]
    assert wd.syms_of(ctx(pos=None)) == ["NVDA"]


def test_ma_trung_khong_bi_hoi_hai_lan():
    p = positions.parse({"rows": [{"sym": "nvda", "shares": 1, "cur": "USD",
                                   "stops": [90]}]},
                        int(NOW.timestamp() * 1000), now=NOW)
    assert wd.syms_of(ctx(pos=p)) == ["NVDA"]


def test_stop_cua_vi_the_ngoai_danh_sach_van_duoc_gui():
    c, s = db(), Sink()
    p = positions.parse({"rows": [{"sym": "AAPL", "shares": 10, "cur": "USD",
                                   "stops": [97.0], "accts": ["A"]}]},
                        int(NOW.timestamp() * 1000), now=NOW)
    r = tick(c, prov(px=96.0, sym="AAPL"), ctx(rows=[], pos=p), s)
    assert r["sent"] == 1 and "cắt lỗ" in s.msgs[0][0].lower(), (r, s.msgs)
    c.close()


# ────────────── 4. dau vao hong mot phan ──────────────
def test_DB_khong_co_bang_nao_thi_noi_ra_chu_khong_chet():
    x = wd.load_ctx(D, Path(tempfile.mkdtemp()) / "trong.db", None, NOW)
    assert x["rows"] == [] and x["day"] == D
    # gate() tu tra stop_only khi khong doc duoc: mac dinh phai la DUNG NGOAI.
    assert x["gate"].get("mode") == "stop_only", x["gate"]
    assert x["gate"].get("why"), "phai co cau giai thich de dua vao tin nhan"


def test_khong_doc_duoc_vi_the_la_khong_biet_chu_khong_phai_khong_co():
    """Tren may nay push.ready() False -> load() tra known=False."""
    x = wd.load_ctx(D, Path(tempfile.mkdtemp()) / "trong.db", None, NOW)
    assert x["pos"]["known"] is False and x["pos"]["note"]


def test_ly_do_bo_qua_di_tu_tick_ra_tin_nhan_mo_phien():
    """`skip` phai quay lai ctx, neu khong thi tin nhan mo phien khong bao gio
    co ly do nao va mot ma bi bo qua ca thang khong ai biet."""
    c, s = db(), Sink()
    x = ctx(rows=[row(adv50=None)])
    tick(c, prov(), x, s)
    assert x["skip"] and "adv50" in x["skip"][0][1], x.get("skip")
    txt = wd.view(x, prov(), SESS).skip
    assert txt == x["skip"]
    c.close()


def test_thieu_bang_mui_gio_thi_cau_canh_bao_di_vao_tin_mo_phien():
    """Khong co `tzdata` thi do tre lech 4-5 gio va moi bao gia bi coi la qua cu
    - ca phien im lang vi mot ly do khong ai doan duoc tu ben ngoai."""
    x = wd.load_ctx(D, Path(tempfile.mkdtemp()) / "trong.db", None, NOW)
    assert x["warn"] == [w for w in x["warn"] if w], "khong duoc co cau rong"
    if quotes._et() is None:
        assert any("múi giờ" in w for w in x["warn"]), x["warn"]


def test_view_gom_du_thu_cho_tin_nhan_mo_phien():
    v = wd.view(ctx(warn=["x"]), prov(), SESS, url="http://u")
    assert v.day == D and v.src == "fixture" and v.warn == ["x"]
    assert v.url == "http://u" and len(v.watch) == 1


# ────────────── 5. nguong va hang so ──────────────
def test_nguong_noi_ra_khi_nguon_chet_la_vai_phut_chu_khong_phai_mot_vong():
    """Mot cu nhay mang khong duoc thanh mot tin nhan; nam phut khong co du lieu
    thi phai thanh mot tin nhan."""
    import config
    assert 3 <= wd.SRC_DOWN_AFTER <= 10
    assert wd.SRC_DOWN_AFTER * float(config.INTRADAY["poll_sec"]) >= 180


def test_doc_lai_dau_vao_du_thua_de_bat_ma_them_tay_trong_phien():
    assert 60 <= wd.REFRESH_SEC <= 900


def test_moi_duong_thoat_luc_khoi_dong_deu_di_qua_chet():
    """BAT BIEN 5, kiem bang chinh ma nguon.

    Mot tien trinh chet luc khoi dong la truong hop im lang nhat ca he thong:
    khong tin mo phien, khong canh bao, trong y het mot ngay khong co gi xay ra.
    Ma thoat chi den duoc systemd; tin nhan den duoc nguoi. Nen moi `return` mang
    ma khac 0 trong `run()` phai la `return await chet(...)`.
    """
    import inspect
    import re
    src = inspect.getsource(wd.run)
    # `return 0` la duong thoat binh thuong (Ctrl-C, --once). Moi ma khac 0 la
    # mot that bai, va mot that bai phai di ra ngoai bang MOT TIN NHAN: hoac qua
    # chet() luc khoi dong, hoac qua tin "da chet giua phien" (co dau ⛔).
    dong = src.splitlines()
    for i, ln in enumerate(dong):
        m = re.search(r"\breturn\s+(\d+)\b", ln)
        if not m or m.group(1) == "0":
            continue
        truoc = "\n".join(dong[max(0, i - 12):i + 1])
        assert "chet(" in truoc or "⛔" in truoc, (
            f"ma thoat {m.group(1)} khong co tin nhan nao di kem: {ln.strip()}")
    assert src.count("await chet(") >= 3, "co duong thoat moi khong goi chet()?"


def test_src_down_la_mot_tin_mot_lan_moi_phien():
    """watchd goi watch.once(..., "src_down"); thieu khoa do la ValueError giua
    phien, tuc la vong quet chet dung luc nguon dang chet."""
    assert "src_down" in watch.ONCE


if __name__ == "__main__":
    _util.main(globals())
