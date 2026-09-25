"""push.py - day snapshot len Cloudflare.

Bon thu phai dung, xep theo muc do "sai thi khong ai phat hien ra":

1. push.py KHONG BAO GIO lam scanner chet. Thieu env, DB thieu bang, DB khong
   ton tai, mang dut, payload phinh - moi truong hop phai tra ve mot chu va di
   tiep. File nay la dashboard; no khong duoc quyen keo bot xuong theo.
2. DB phai mo che do CHI DOC. Trong baseline.db la 3 nam nen, tai lai mat 30-60
   phut. Mot cau DELETE viet lam o day khong duoc phep chay.
3. Khoa "khong doi thi khong day". D1 free co han so write moi ngay; cron
   --status chay moi phut, neu moi lan deu ghi thi 1400 write/ngay bi tieu vao
   viec ghi lai dung thu vua ghi. Va dau moc thoi gian KHONG duoc tinh vao
   digest, khong thi khong lan nao "khong doi".
4. `age` cua bang struct/candidates phai co trong status. setups.MAX_AGE = 5:
   bang cu hon 5 ngay -> load_candidates() tra ve rong -> bot im lang HOAN TOAN,
   khong loi, khong tin nhan. Da mot tuan khong alert thi day la con so phai
   xem truoc, nen thieu no la dashboard vo dung.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import _util

ph = _util.need("push")


def _db(d: str, name: str = "t.db") -> Path:
    p = Path(d) / name
    ph._mkdb(p)
    return p


# ── 1. khong bao gio nem ra ngoai ───────────────────────────────────────────
def test_thieu_env_tra_off():
    old = ph.URL, ph.TOKEN
    try:
        ph.URL = ph.TOKEN = ""
        assert ph.ready() is False
        assert ph.put("scanner:status", {"a": 1}) == "off"
        assert ph.get("scanner:config") is None
        assert ph.prune() == 0
    finally:
        ph.URL, ph.TOKEN = old


def test_db_khong_ton_tai():
    with tempfile.TemporaryDirectory() as d:
        s = ph.status_payload(Path(d) / "khong-co.db")
        assert "db_error" in s
        assert "ts" in s                     # van phai la payload dung dinh dang
        assert ph.candidates_payload(Path(d) / "khong-co.db") is None
        assert ph.alerts_payload(Path(d) / "khong-co.db") is None


def test_db_thieu_bang():
    """VM moi chua chay structure.py --build lan nao: `struct` chua ton tai."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "tron.db"
        sqlite3.connect(p).close()
        s = ph.status_payload(p)
        assert "db_error" not in s           # DB mo duoc, chi la rong
        assert "bars" not in s and "struct" not in s
        assert ph.candidates_payload(p) is None
        assert ph.rejects_payload(p) is None


def test_mang_dut_tra_err_khong_nem():
    """URL khong the ket noi -> put() tra 'err', khong nem exception."""
    old = ph.URL, ph.TOKEN, ph.TRIES
    try:
        # Cong 1 tren localhost: bi tu choi ngay, khong cho het timeout.
        ph.URL, ph.TOKEN, ph.TRIES = "http://127.0.0.1:1/api/scanner", "x", 1
        assert ph.put("scanner:status", {"a": 1}, force=True) == "err"
        assert ph.get("scanner:config") is None
    finally:
        ph.URL, ph.TOKEN, ph.TRIES = old


def test_payload_to_bi_chan_tai_cho():
    """Chan o day, khong doi HTTP 413: loi cua minh thi bao bang tieng Viet."""
    assert ph.put("scanner:x", {"a": "y" * (ph.MAX_BYTES + 10)}) == "err"


def test_none_thi_bo_qua():
    """payload builder tra None (chua co du lieu) -> khong goi mang."""
    assert ph.put("scanner:candidates", None) == "skip"


def test_body_khong_phai_json_van_doc_duoc():
    """403 tra ve trang HTML tung bi bien thanh `{}` -> "HTTP 403 {}", tuc la xoa
    sach cau tra loi ngay trong thong bao loi. Function LUON tra JSON, nen body
    khong phai JSON = thu chan nam TRUOC function, va body la bang chung duy nhat."""
    m = ph._snip("<!DOCTYPE html><html>  Sorry, you have been blocked  </html>")
    assert "HTML" in m and "blocked" in m
    assert "\n" not in m                      # mot dong, de vao log cron
    assert ph._snip("") == "body rong"
    assert "text" in ph._snip("khong phai html")
    assert len(ph._snip("x" * 9999)) < 300     # khong nem ca trang vao log


def test_user_agent_duoc_gui():
    """urllib khong dat User-Agent thi gui "Python-urllib/3.x", va Bot Fight Mode
    cua Cloudflare (ban free cung co) tra 403 HTML cho dung chuoi do - TRUOC khi
    request cham tay function. Token dung, deploy dung, van 403."""
    import urllib.request

    seen = {}

    class _Fake:
        status = 200

        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        seen["tok"] = req.get_header("X-scanner-token")
        return _Fake()

    old_open, old = urllib.request.urlopen, (ph.URL, ph.TOKEN)
    try:
        urllib.request.urlopen = _fake_urlopen
        ph.URL, ph.TOKEN = "https://x.invalid/api/scanner", "abc"
        assert ph._req("GET", "ping") == (200, {"ok": True})
    finally:
        urllib.request.urlopen = old_open
        ph.URL, ph.TOKEN = old

    assert seen["ua"] == ph.UA
    assert "urllib" not in (seen["ua"] or "").lower()
    assert seen["tok"] == "abc"


# ── 2. DB chi doc ───────────────────────────────────────────────────────────
def test_db_mo_che_do_chi_doc():
    with tempfile.TemporaryDirectory() as d:
        c = ph._con(_db(d))
        try:
            try:
                c.execute("DELETE FROM bars")
            except sqlite3.OperationalError:
                pass                          # dung
            else:
                raise AssertionError("mode=ro phai chan moi cau ghi")
        finally:
            c.close()


# ── 3. khong doi thi khong day ──────────────────────────────────────────────
def test_digest_bo_qua_dau_moc_thoi_gian():
    a = {"ts": 1, "age_sec": 3, "bars": {"syms": 10}}
    b = {"ts": 999_999, "age_sec": 90, "bars": {"syms": 10}}
    assert ph._digest(a) == ph._digest(b)


def test_digest_doi_khi_so_lieu_doi():
    a = {"ts": 1, "bars": {"syms": 10}}
    assert ph._digest(a) != ph._digest({"ts": 1, "bars": {"syms": 11}})


def test_digest_khong_phu_thuoc_thu_tu_khoa():
    assert ph._digest({"a": 1, "b": 2}) == ph._digest({"b": 2, "a": 1})


def test_digest_chiu_duoc_list_va_none():
    assert ph._digest([1, 2, None]) == ph._digest([1, 2, None])
    assert ph._digest(None) == ph._digest(None)


# ── 4. tuoi bang trong status ───────────────────────────────────────────────
def test_tuoi_bang_co_trong_status():
    with tempfile.TemporaryDirectory() as d:
        s = ph.status_payload(_db(d), today="2026-09-14")
        assert s["bars"]["last"] == "2026-09-12"
        assert s["bars"]["age"] == 2
        assert s["candidates"]["age"] == 2     # so phai xem khi bot im lang
        assert s["struct"]["rows"] == 0        # bang rong van phai co mat


def test_age_days():
    assert ph._age_days("2026-09-10", "2026-09-14") == 4
    assert ph._age_days("2026-09-10T13:00:00", "2026-09-14") == 4
    assert ph._age_days(None) is None
    assert ph._age_days("rac") is None
    assert ph._age_days("") is None


# ── payload doc dung DB ─────────────────────────────────────────────────────
def test_status_dem_alert_theo_ngay_et():
    """Coc: alert luu ts_et (gio New York). Dem theo ngay UTC thi phien toi
    (sau 20:00 ET = hom sau UTC) se bien mat khoi bang hom nay."""
    with tempfile.TemporaryDirectory() as d:
        p = _db(d)
        assert ph.status_payload(p, today="2026-09-12")["alerts_today"]["n"] == 1
        assert ph.status_payload(p, today="2026-09-14")["alerts_today"]["n"] == 0


def test_status_doc_beat_cua_main():
    with tempfile.TemporaryDirectory() as d:
        assert ph.status_payload(_db(d))["beat"] == {"scans": 12}


def test_status_chiu_duoc_beat_rac():
    """main.py ghi beat khong hop le -> bo qua khoa do, khong sap ca payload."""
    with tempfile.TemporaryDirectory() as d:
        p = _db(d)
        c = sqlite3.connect(p)
        with c:
            c.execute("UPDATE kv SET v='{khong phai json' WHERE k='beat'")
        c.close()
        s = ph.status_payload(p)
        assert "beat" not in s
        assert "bars" in s


def test_candidates_payload():
    with tempfile.TemporaryDirectory() as d:
        cp = ph.candidates_payload(_db(d))
        assert cp["by_setup"]["BO"][0]["sym"] == "AAA"
        assert cp["by_setup"]["BO"][0]["pivot"] == 21
        assert cp["by_setup"]["BO_total"] == 1
        assert "RV" not in cp["by_setup"]      # setup khong co ma thi khong bay ra


def test_candidates_cat_top_nhung_van_bao_tong():
    """Cat bot dong nhung phai noi that con bao nhieu: 40 dong tren mot bang co
    500 ma la thong tin khac han 40 dong tren bang co 40 ma."""
    with tempfile.TemporaryDirectory() as d:
        p = _db(d)
        c = sqlite3.connect(p)
        with c:
            for i in range(5):
                c.execute("INSERT INTO candidates(sym,setup,d,pivot,quality,updated)"
                          " VALUES(?,'BO','2026-09-12',10,?,'x')", (f"B{i}", i))
        c.close()
        cp = ph.candidates_payload(p, top=2)
        assert len(cp["by_setup"]["BO"]) == 2
        assert cp["by_setup"]["BO_total"] == 6
        assert cp["by_setup"]["BO"][0]["sym"] == "AAA"    # quality 7.5, cao nhat


def _lead(p: Path, **kw) -> None:
    """Them mot dong LEAD co ke hoach lenh day du vao DB da dung san."""
    row = {"sym": "LLL", "setup": "LEAD", "d": "2026-09-12", "ref_close": 100.0,
           "pivot": 101.0, "quality": 9.0, "sector": "XLK", "atr_pct": 0.03,
           "trigger": 101.1, "stop": 96.6, "target": 110.1, "stop_pct": 0.0445,
           "risk_pct": 0.0075, "size_pct": 0.168, "updated": "x"}
    row.update(kw)
    cols = ",".join(row)
    c = sqlite3.connect(p)
    with c:
        c.execute(f"INSERT INTO candidates({cols}) VALUES"
                  f"({','.join('?' * len(row))})", tuple(row.values()))
    c.close()


def test_watchlist_mang_theo_ke_hoach_lenh():
    """Ke hoach phai di TOI cho nguoi doc, khong chi nam trong DB.

    `trigger/stop/target/size_pct` la ca ly do bang nay ton tai: quyet dinh duoc
    lap tu dem truoc thi phai doc duoc tu dem truoc.
    """
    with tempfile.TemporaryDirectory() as d:
        p = _db(d)
        _lead(p)
        w = ph.watchlist_payload(p)
        r = w["rows"][0]
        assert r["sym"] == "LLL"
        for k in ph.PLAN_COLS:
            assert r[k] is not None, f"mat cot ke hoach `{k}`"
        assert r["trigger"] == 101.1 and r["stop"] == 96.6


def test_size_va_size_pct_la_hai_con_so_khac_nhau():
    """`size` = he so cua o playbook; `size_pct` = co vi the CUOI CUNG (da nhan
    he so do roi). Gop chung lai, hoac nhan chung voi nhau o dashboard, la tu
    giam vi the mot cach khong ai thay."""
    with tempfile.TemporaryDirectory() as d:
        p = _db(d)                    # _mkdb() da co san mot dong regime
        _lead(p)
        w = ph.watchlist_payload(p)
        assert w["size"] == 1.0                        # he so playbook
        assert w["rows"][0]["size_pct"] == 0.168       # co vi the thuc te
        assert "size_pct" in ph.PLAN_COLS and "size" not in ph.PLAN_COLS


def test_db_cu_thieu_cot_ke_hoach_thi_bang_van_ra_chu_khong_bien_mat():
    """push.py chay nhu mot tien trinh rieng voi setups.py: hai ben CO THE lech
    phien ban trong vai gio sau khi deploy.

    `_rows()` nuot sqlite3.Error va tra [] -> mot cau SELECT co cot thieu khong
    bao loi, no lam CA bang watchlist bien mat khoi dashboard va doc giong het
    mot dem khong co ma nao dat. Do la dung kieu that bai im lang ma ca he thong
    nay duoc dung de chan.
    """
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "cu.db"
        c = sqlite3.connect(p)
        with c:      # so do `candidates` nhu TRUOC khi co ke hoach lenh
            c.execute("""CREATE TABLE candidates(
                sym TEXT, setup TEXT, d TEXT, ref_close REAL, pivot REAL,
                sma20 REAL, adv20 REAL, atr_pct REAL, base_len INTEGER,
                base_depth REAL, off_high REAL, rs_pct REAL, dist_pivot REAL,
                fund_ok INTEGER, sector TEXT, rs21 REAL, rs63 REAL,
                quality REAL, updated TEXT, PRIMARY KEY(sym, setup))""")
            c.execute("INSERT INTO candidates(sym,setup,d,ref_close,quality,"
                      "updated) VALUES('LLL','LEAD','2026-09-12',100,9.0,'x')")
        c.close()
        w = ph.watchlist_payload(p)
        assert w and w["rows"][0]["sym"] == "LLL", "bang bien mat khong noi gi"
        assert "trigger" not in w["rows"][0]      # thieu that thi thieu, khong bua
        # Va bang candidates cung phai qua duoc.
        assert ph.candidates_payload(p)["by_setup"]["LEAD"][0]["sym"] == "LLL"


def test_alert_chua_co_outcome_van_ra():
    """LEFT JOIN, khong JOIN: alert vua gui la dong dang muon xem nhat."""
    with tempfile.TemporaryDirectory() as d:
        r = ph.alerts_payload(_db(d), "2026-09-12")["rows"][0]
        assert r["sym"] == "AAA" and r["score"] == 9.1
        assert r["px15"] is None


def test_ngay_khong_co_alert_tra_none():
    with tempfile.TemporaryDirectory() as d:
        assert ph.alerts_payload(_db(d), "2026-01-01") is None


def test_alerts_khong_nhan_ma_cua_ngay_khac():
    with tempfile.TemporaryDirectory() as d:
        p = _db(d)
        c = sqlite3.connect(p)
        with c:
            c.execute("INSERT INTO alerts(ts_et,kind,sym,score) VALUES"
                      "('2026-09-13T10:00:00','NEW','BBB',8.0)")
        c.close()
        syms = [r["sym"] for r in ph.alerts_payload(p, "2026-09-12")["rows"]]
        assert syms == ["AAA"]


# ── dry run: dung het payload ma khong ra mang ──────────────────────────────
def test_dry_khong_can_env():
    with tempfile.TemporaryDirectory() as d:
        old = ph.URL, ph.TOKEN
        try:
            ph.URL = ph.TOKEN = ""
            r = ph.push_all(_db(d), dry=True)
        finally:
            ph.URL, ph.TOKEN = old
        assert r["status"] == "dry"
        assert r["candidates"] == "dry"
        assert "pruned" not in r               # dry khong duoc xoa gi tren cloud


def test_khoa_dung_tien_to_scanner():
    """Function phia Cloudflare chan moi khoa khong bat dau bang 'scanner:'.
    Neu doi ten khoa o day thi phai doi ca KEY_RE ben kia."""
    with tempfile.TemporaryDirectory() as d:
        p = _db(d)
        sent = []
        old = ph.put
        try:
            ph.put = lambda k, v, force=False, dry=False: (sent.append(k), "dry")[1]
            ph.push_all(p, dry=True)
        finally:
            ph.put = old
        assert sent, "push_all phai day it nhat mot khoa"
        assert all(k.startswith("scanner:") for k in sent), sent


# ── contract giua main.write_beat va push.status_payload ────────────────────
# Hai ham nay o hai process khac nhau va noi chuyen qua DUY NHAT mot dong trong
# bang `kv`. Bo qua tren may dev (main.py can httpx); job pytest cua CI chay.
def test_write_beat_khop_voi_status_payload():
    m = _util.need("main")
    with tempfile.TemporaryDirectory() as d:
        p = _db(d)
        old_db = m.DB
        try:
            m.DB = p
            m.write_beat(m.State(), None, dry=True)
        finally:
            m.DB = old_db

        beat = ph.status_payload(p)["beat"]
        assert beat["dry"] is True
        assert beat["scans"] == 0 and beat["n_alerts"] == 0
        assert beat["universe"] == 0
        assert beat["universe_age"] is None    # chua quet lan nao, khong phai 0
        assert isinstance(beat["up_sec"], int)
        assert "session" in beat


def test_write_beat_khong_nem_khi_db_hong():
    """set_kv tu bat loi. Neu no nem thi loop_clock cong st.errors moi 20 giay
    va ghi traceback vao prep.log suot phien - vi mot dashboard."""
    m = _util.need("main")
    old_db, old_log = m.DB, m.store.log
    try:
        m.DB = Path("/khong-ton-tai") / "x.db"
        m.store.log = lambda *a, **k: None     # dung lam ban output cua test
        m.write_beat(m.State(), None, dry=True)
    finally:
        m.DB, m.store.log = old_db, old_log


if __name__ == "__main__":
    _util.main(globals())
