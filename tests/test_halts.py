"""halts.py — parser feed tam dung giao dich cua Nasdaq.

Feed nay la XML cua nguoi khac: no se doi ma khong bao truoc (namespace, thu tu
the, dinh dang gio). Test o day dam bao khi no doi thi bot **im lang bo qua**
chu khong chet, va khong bao giờ doan bua khi thieu du lieu.
"""
from __future__ import annotations

import datetime as dt
import time

import _util

h = _util.need("halts")

NOW = 1_757_000_000.0            # moc co dinh: test khong phu thuoc dong ho


def _xml(items: str) -> str:
    return ('<?xml version="1.0"?><rss xmlns:ndaq="http://www.nasdaqtrader.com/">'
            f"<channel>{items}</channel></rss>")


def _item(sym: str, code: str = "LUDP", d: str = "09/04/2026",
          t: str = "15:42:00", rd: str = "", rt: str = "", qt: str = "") -> str:
    return ("<item>"
            f"<ndaq:IssueSymbol>{sym}</ndaq:IssueSymbol>"
            f"<ndaq:ReasonCode>{code}</ndaq:ReasonCode>"
            f"<ndaq:HaltDate>{d}</ndaq:HaltDate>"
            f"<ndaq:HaltTime>{t}</ndaq:HaltTime>"
            f"<ndaq:ResumptionDate>{rd}</ndaq:ResumptionDate>"
            f"<ndaq:ResumptionQuoteTime>{qt}</ndaq:ResumptionQuoteTime>"
            f"<ndaq:ResumptionTradeTime>{rt}</ndaq:ResumptionTradeTime>"
            "</item>")


def _at(mins_ago: float) -> tuple[str, str]:
    """(ngay, gio) cua NOW - mins_ago phut, theo mui gio ma _epoch se doc."""
    n = dt.datetime.fromtimestamp(NOW - mins_ago * 60, h.ET)
    return f"{n:%m/%d/%Y}", f"{n:%H:%M:%S}"


# ───────────────────────── parse ─────────────────────────
def test_parse_lay_dung_truong():
    d, t = _at(20)
    got = h.parse(_xml(_item("WETO", "LUDP", d, t)), now=NOW)
    assert set(got) == {"WETO"}
    r = got["WETO"]
    assert r["code"] == "LUDP" and r["sev"] == 1 and r["active"]
    assert r["resume_ts"] is None
    assert abs(r["halt_ts"] - (NOW - 1200)) < 61, r["halt_ts"]


def test_resumption_trade_time_trong_nghia_la_chua_mo_lai():
    """Day la truong quan trong nhat cua ca feed."""
    d, t = _at(10)
    assert h.parse(_xml(_item("A", d=d, t=t)), now=NOW)["A"]["active"]
    dr, tr = _at(2)
    assert not h.parse(_xml(_item("A", d=d, t=t, rd=dr, rt=tr)),
                       now=NOW)["A"]["active"]


def test_quote_time_khong_dong_nghia_voi_mo_lai():
    """Co gio mo BAO GIA nhung chua co gio KHOP LENH -> van dang halt."""
    d, t = _at(10)
    dq, tq = _at(1)
    r = h.parse(_xml(_item("A", d=d, t=t, rd=dq, qt=tq)), now=NOW)["A"]
    assert r["active"] and r["quote_ts"] and r["resume_ts"] is None


def test_bo_qua_namespace():
    """Nasdaq doi URI namespace -> parser van phai doc duoc."""
    d, t = _at(5)
    x = _xml(_item("A", d=d, t=t)).replace(
        "http://www.nasdaqtrader.com/", "https://example.com/v9/")
    assert h.parse(x, now=NOW)["A"]["code"] == "LUDP"
    # Ke ca khong co namespace nao.
    assert h.parse(_xml(_item("A", d=d, t=t)).replace("ndaq:", ""),
                   now=NOW)["A"]["code"] == "LUDP"


def test_mot_ma_nhieu_ban_ghi_giu_ban_moi_nhat():
    d1, t1 = _at(120)
    d2, t2 = _at(10)
    got = h.parse(_xml(_item("A", "T1", d1, t1) + _item("A", "LUDP", d2, t2)),
                  now=NOW)
    assert got["A"]["code"] == "LUDP"
    # Thu tu nguoc lai cung phai cho cung ket qua.
    got = h.parse(_xml(_item("A", "LUDP", d2, t2) + _item("A", "T1", d1, t1)),
                  now=NOW)
    assert got["A"]["code"] == "LUDP"


def test_xml_hong_khong_nem_loi():
    for bad in ("", "<rss>", "<rss><channel><item>", "khong phai xml",
                '{"json": true}', "<rss/>"):
        assert h.parse(bad, now=NOW) == {}


def test_item_thieu_truong_khong_nem_loi():
    assert h.parse(_xml("<item></item>"), now=NOW) == {}          # khong co sym
    got = h.parse(_xml("<item><ndaq:IssueSymbol>A</ndaq:IssueSymbol></item>"
                       .replace("ndaq:", "")), now=NOW)
    assert got["A"]["halt_ts"] is None and not got["A"]["active"]


def test_gio_sai_dinh_dang_khong_nem_loi():
    for bad_t in ("", "khong phai gio", "25:99:99", "3:42 PM"):
        got = h.parse(_xml(_item("A", t=bad_t)), now=NOW)
        assert got["A"]["halt_ts"] is None, bad_t
        assert not got["A"]["active"]


def test_ma_ly_do_la_van_duoc_canh_bao():
    """Nasdaq them code moi -> khong duoc coi la vo hai."""
    d, t = _at(5)
    r = h.parse(_xml(_item("A", "ZZ99", d, t)), now=NOW)["A"]
    assert r["sev"] == 2 and r["note"]


def test_halt_qua_cu_khong_con_tinh_la_dang_halt():
    d, t = _at(h.MAX_AGE_H * 60 + 30)
    assert not h.parse(_xml(_item("A", d=d, t=t)), now=NOW)["A"]["active"]


# ───────────────────────── HaltBook ─────────────────────────
def _book(items: str) -> "h.HaltBook":
    b = h.HaltBook()
    b.load(_xml(items), now=NOW)
    b.ts = time.time()          # `stale` doc dong ho that -> phai lam moi
    return b


def test_view_va_blocked():
    d, t = _at(10)
    b = _book(_item("PAUSE", "LUDP", d, t) + _item("BAD", "H10", d, t))
    v = b.view("PAUSE")
    assert v and v["since"] and not v["resumed"]
    assert b.blocked("PAUSE") is None, "LUDP khong duoc chan alert"
    assert b.blocked("BAD")["code"] == "H10"
    assert b.is_halted("BAD") and b.active_syms() == ["BAD", "PAUSE"]


def test_view_khong_phan_biet_hoa_thuong():
    d, t = _at(10)
    b = _book(_item("weto", d=d, t=t))
    assert b.view("WETO") and b.view("weto")


def test_ma_khong_co_trong_feed():
    b = _book(_item("A", d=_at(5)[0], t=_at(5)[1]))
    assert b.view("AAPL") is None and not b.is_halted("AAPL")
    assert b.blocked("AAPL") is None


def test_feed_cu_thi_khong_doan():
    """Mat mang > STALE -> noi 'khong biet', khong in dong halt het han."""
    d, t = _at(10)
    b = _book(_item("A", d=d, t=t))
    b.ts = time.time() - h.STALE - 1
    assert b.stale
    assert b.view("A") is None and b.blocked("A") is None
    # is_halted() KHONG loc stale (dung cho log noi bo), phai con True.
    assert b.is_halted("A")


def test_vua_mo_lai_trong_5_phut():
    d, t = _at(30)
    dr, tr = _at(2)
    b = _book(_item("A", "T2", d, t, rd=dr, rt=tr))
    v = b.view("A", now=NOW)
    assert v and v["resumed"] and v["until"]
    assert b.just_resumed("A", now=NOW)


def test_mo_lai_lau_roi_thi_khong_hien():
    d, t = _at(120)
    dr, tr = _at(30)
    b = _book(_item("A", "T2", d, t, rd=dr, rt=tr))
    assert b.view("A", now=NOW) is None
    assert not b.just_resumed("A", now=NOW)


def test_h10_da_mo_lai_thi_khong_chan_nua():
    d, t = _at(30)
    dr, tr = _at(2)
    b = _book(_item("A", "H10", d, t, rd=dr, rt=tr))
    assert b.blocked("A") is None, "da mo lai roi thi khong chan"


def test_loi_mang_giu_du_lieu_cu():
    d, t = _at(10)
    b = _book(_item("A", d=d, t=t))
    real, h.fetch = h.fetch, lambda *a, **k: 1 / 0
    try:
        assert b.refresh() == -1
    finally:
        h.fetch = real
    assert b.get("A"), "loi mang khong duoc xoa du lieu cu"
    assert b.n_fail == 1 and b.err


def test_book_rong():
    b = h.HaltBook()
    assert b.stale and b.view("A") is None and b.active_syms() == []
    assert not b.is_halted("A") and b.blocked("A") is None


def test_moi_reason_du_ba_truong():
    for code, val in h.REASONS.items():
        lab, sev, note = val
        assert lab and note, code
        assert 0 <= sev <= 3, code
        assert "HALT" not in lab.upper(), \
            f"{code}: render.py da co chu 'TAM DUNG GIAO DICH' o dau dong"


def test_selftest_cua_module_chay_duoc():
    h._selftest()


if __name__ == "__main__":
    _util.main(globals())
