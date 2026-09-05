"""events.py — log co cau truc. Day la nguon so lieu cho Phase 1.

Hai thu phai dung tuyet doi:

  1. Truong thieu ghi thanh null, KHONG bo khoa. Bo khoa thi ban phan tich sau
     nay khong phan biet duoc "khong do duoc rvol" voi "rvol = 0".
  2. Ghi that bai khong duoc nem loi len loop_score — mat mot dong log con hon
     mat mot alert.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import _util

ev = _util.need("events")

H = {"sym": "AAA", "score": 8.4, "px": 10.0, "chg": 0.9, "vol": 2_000_000,
     "rvol": 40.0, "atr_move": 3.1, "float_sh": 5e6, "float_rot": 0.4,
     "dollar_vol": 2e7, "diverge": 0.0, "freshness": "REALTIME",
     "sources": ["finviz", "alpaca_mover"], "cik": "0001941158",
     "explain": "RVOL 40.0× (+3.5)"}
T0 = 1_757_000_000.0


def _p(td: str) -> Path:
    return Path(td) / "e.jsonl"


# ───────────────────────── ghi & doc ─────────────────────────
def test_moi_alert_mot_dong():
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)
        assert ev.alert(H, 2, "NEW", path=p, ts=T0)
        assert ev.alert({**H, "sym": "BBB"}, 3, "UP", path=p, ts=T0 + 1)
        lines = p.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        for ln in lines:
            json.loads(ln)          # moi dong phai tu doc duoc doc lap


def test_du_truong_can_cho_phan_tich():
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)
        ev.alert(H, 2, "NEW", path=p, ts=T0)
        r = ev.read(p)[0]
        assert r["ts"] == T0 and r["kind"] == "alert"
        assert r["level"] == 2 and r["alert_kind"] == "NEW"
        for k in ev.FIELDS:
            assert k in r, k
        assert r["score"] == 8.4 and r["rvol"] == 40.0
        assert r["sources"] == ["finviz", "alpaca_mover"]


def test_truong_thieu_thanh_null_khong_bi_bo():
    """'khong biet' va 'bang 0' phai phan biet duoc."""
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)
        ev.alert({"sym": "AAA", "score": 7.1}, 1, path=p, ts=T0)
        r = ev.read(p)[0]
        assert r["rvol"] is None and r["float_sh"] is None
        assert set(ev.FIELDS) <= set(r)


def test_them_truong_ngoai_bang():
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)
        ev.alert(H, 2, "NEW", path=p, ts=T0, dry=True, halt="T1", mso=42,
                 session="LIVE")
        r = ev.read(p)[0]
        assert r["dry"] is True and r["halt"] == "T1" and r["mso"] == 42


def test_bo_du_lieu_rac():
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)
        assert not ev.alert({}, 1, path=p)
        assert not ev.alert({"score": 9.0}, 1, path=p)      # thieu sym
        assert not ev.alert("AAA", 1, path=p)               # khong phai dict
        assert not p.exists() or ev.read(p) == []


def test_emit_kind_khac():
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)
        assert ev.emit("halt_block", path=p, sym="SCAMZ", code="H10", sev=3)
        r = ev.read(p)[0]
        assert r["kind"] == "halt_block" and r["sev"] == 3
        assert isinstance(r["ts"], float), "ts tu dong ho khi khong truyen"


def test_read_loc_theo_kind():
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)
        ev.alert(H, 2, path=p, ts=T0)
        ev.emit("halt_block", path=p, sym="X", code="H10")
        assert len(ev.read(p)) == 2
        assert len(ev.read(p, kind="alert")) == 1
        assert ev.read(p, kind="khong_co_kind_nay") == []


# ───────────────────────── ben bi ─────────────────────────
def test_ghi_that_bai_khong_nem_loi():
    """Duong dan khong ghi duoc -> False, khong duoc lam chet loop_score."""
    assert ev.emit("alert", path="/:/khong-the-ghi/x.jsonl", sym="AAA") is False
    assert ev.alert(H, 2, path="/:/khong-the-ghi/x.jsonl") is False


def test_doc_file_khong_ton_tai():
    with tempfile.TemporaryDirectory() as td:
        assert ev.read(Path(td) / "chua-co.jsonl") == []


def test_dong_hong_bi_bo_qua_khong_lam_chet_ca_file():
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)
        ev.alert(H, 2, path=p, ts=T0)
        with p.open("a", encoding="utf-8") as f:
            f.write("khong phai json\n\n[1,2,3]\n{thieu ngoac\n")
        ev.alert({**H, "sym": "BBB"}, 3, path=p, ts=T0 + 1)
        rows = ev.read(p)
        assert [r["sym"] for r in rows] == ["AAA", "BBB"], rows


def test_tu_tao_thu_muc():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "chua" / "co" / "e.jsonl"
        assert ev.emit("alert", path=p, sym="AAA")
        assert p.exists()


def test_kieu_la_khong_lam_hong_dong():
    """set/Path/object khong phai JSON — phai thanh chuoi chu khong nem loi."""
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)

        class X:
            def __str__(self):
                return "doi-tuong-la"

        assert ev.emit("alert", path=p, sym="AAA", srcs={"b", "a"},
                       obj=X(), pth=Path("x/y"))
        r = ev.read(p)[0]
        assert r["srcs"] == ["a", "b"], "set phai sap xep de dong on dinh"
        assert r["obj"] == "doi-tuong-la"


def test_giu_dau_tieng_viet():
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)
        ev.emit("alert", path=p, sym="AAA", note="biên độ 3× ATR")
        assert "biên độ" in p.read_text(encoding="utf-8")
        assert ev.read(p)[0]["note"] == "biên độ 3× ATR"


def test_xoay_file_khi_qua_to():
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)
        p.write_text("x" * (ev.MAX_BYTES + 1), encoding="utf-8")
        assert ev.emit("alert", path=p, sym="AAA")
        assert p.with_suffix(".jsonl.1").exists(), "ban cu phai duoc giu lai"
        assert len(ev.read(p)) == 1, "file moi chi con dong vua ghi"


# ───────────────────────── giao uoc voi main.py / outcome ─────────────────
def test_ghi_duoc_dict_that_cua_scorer():
    sc = _util.need("scorer")
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)
        h = sc.score_one({"sym": "AAA", "px": 10.0, "vol": 2_000_000,
                          "chg": 0.25, "sources": ["finviz"], "fresh": 3},
                         {"adv20": 200_000, "atr14": 1.0, "prev_close": 8.0,
                          "float_sh": 2_000_000, "cik": "1"}, 1.0)
        assert ev.alert(h, 2, "NEW", path=p, ts=T0)
        r = ev.read(p)[0]
        assert r["score"] == h["score"] and r["rvol"] == h["rvol"]
        assert None not in [r[k] for k in ("sym", "score", "px", "rvol")]


def test_join_duoc_voi_bang_outcome():
    """main.py dung CUNG mot `ts` cho outcome.record() va events.alert().

    outcome luu `alert_ts` dang INTEGER (cat phan thap phan), con events giu
    nguyen float — nen khoa join la (sym, int(ts)), khong phai (sym, ts).
    """
    o = _util.need("outcome")
    with tempfile.TemporaryDirectory() as td:
        db, p = Path(td) / "t.db", _p(td)
        ts = T0 + 0.77
        assert o.record(db, H, 2, ts=ts)
        assert ev.alert(H, 2, "NEW", path=p, ts=ts)
        r = ev.read(p)[0]
        with closing(sqlite3.connect(str(db))) as c:
            row = c.execute("SELECT sym, alert_ts, px0, level, score "
                            "FROM outcome").fetchone()
        assert row[0] == r["sym"] and row[1] == int(r["ts"])
        assert row[2] == r["px"] and row[3] == r["level"]
        assert row[4] == r["score"]


def test_smoke_cua_module_chay_duoc():
    ev._smoke()


if __name__ == "__main__":
    _util.main(globals())
