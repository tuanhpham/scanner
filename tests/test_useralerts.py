"""useralerts.py - canh bao theo watchlist cua nguoi dung.

Bat bien:
1. CHI THI TRUONG DANG MO duoc lay gia. Ma Duc ngoai gio Frankfurt khong ton
   mot request nao, va khong bao gi ca.
2. BAO GIA CU THI KHONG BAO. Ngay le, nghi trua, nguon chet: `fresh` chan.
3. KHONG BIET KHOI LUONG LA KHONG BIET. Thieu trung binh -> bo qua co ly do,
   khong phai "khoi luong thap", khong phai "bat thuong".
4. GUI HONG THI KHONG GHI, GUI DUOC THI KHONG GUI LAI trong ngay.
5. DOI MUC GIUA PHIEN THI MUC MOI DUOC BAO.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import tempfile
from pathlib import Path

import _util

ua = _util.need("useralerts")
import quotes                                                    # noqa: E402
import watch                                                     # noqa: E402

# Thu Sau 02/10/2026 14:00 UTC = 10:00 New York, 16:00 Frankfurt, 21:00 Ha Noi.
NOW = dt.datetime(2026, 10, 2, 14, 0, tzinfo=dt.UTC)


def q(sym, px, vol=1_000_000, age_min=5, ts=None):
    t = ts or (NOW - dt.timedelta(minutes=age_min)).isoformat()
    return {"sym": sym, "ts": t, "o": px, "h": px, "l": px, "c": px, "v": vol}


def prov(*rows):
    return quotes.FixtureProvider([{"now": NOW.isoformat(), "rows": list(rows)}])


def runner(rules: dict, refs: dict | None = None, seen: list | None = None):
    def load():
        return ua.parse({"on": True, "syms": rules}, 1)

    def fetch(syms, day_of):
        return {s: v for s, v in (refs or {}).items() if s in syms}

    def put(v, dry):
        if seen is not None:
            seen.append(v)
        return "sent"

    return ua.Runner({"max_quote_age_sec": 1200}, None, False, load, fetch, put)


def db():
    return watch.con(Path(tempfile.mkdtemp()) / "t.db")


class Box:
    def __init__(self, ok=True):
        self.ok, self.msgs = ok, []

    async def __call__(self, txt, loud=False, markup=None):
        self.msgs.append(txt)
        return self.ok


def test_market_of_and_session():
    assert ua.market_of("NVDA") == "US"
    assert ua.market_of("BRK.B") == "US"
    assert ua.market_of("SAP.DE") == "EU"
    assert ua.market_of("FPT.VN") == "VN"
    assert ua.market_of("VOD.L") is None
    assert ua.session("US", NOW)["open"] is True
    assert ua.session("EU", NOW)["open"] is True
    assert ua.session("VN", NOW)["open"] is False
    sat = NOW + dt.timedelta(days=1)
    assert ua.session("US", sat)["open"] is False


def test_parse_unknown_off_and_bad_rows():
    assert ua.parse(None)["known"] is False
    off = ua.parse({"on": False, "syms": {"NVDA": {"above": 1}}})
    assert off["on"] is False and off["note"]
    p = ua.parse({"syms": {"nvda": {"above": "x", "rvol": 2}, "VOD.L": {"above": 1},
                           "AMD": {"lists": ["a"]}}})
    assert p["syms"] == {"NVDA": {"lists": [], "rvol": 2.0}}


def test_price_level_fires_once_and_new_level_fires_again():
    c, box = db(), Box()
    r = runner({"NVDA": {"lists": ["Semis"], "above": 200}})
    out = asyncio.run(r.step(c, prov(q("NVDA", 201.5)), box, NOW))
    assert out["sent"] == 1 and "NVDA" in box.msgs[0] and "200.00" in box.msgs[0]
    assert "Semis" in box.msgs[0]
    out = asyncio.run(r.step(c, prov(q("NVDA", 202)), box, NOW))
    assert out["sent"] == 0 and len(box.msgs) == 1
    # Nguoi dung nang muc len 202: muc moi la mot quy tac moi.
    r.rules = ua.parse({"syms": {"NVDA": {"above": 202}}})
    assert asyncio.run(r.step(c, prov(q("NVDA", 202)), box, NOW))["sent"] == 1


def test_send_failure_is_not_recorded():
    c = db()
    r = runner({"NVDA": {"below": 100}})
    assert asyncio.run(r.step(c, prov(q("NVDA", 99)), Box(ok=False), NOW))["sent"] == 0
    assert ua.recent(c, "2026-10-01") == []
    assert asyncio.run(r.step(c, prov(q("NVDA", 99)), Box(), NOW))["sent"] == 1


def test_stale_quote_never_alerts():
    c, box = db(), Box()
    r = runner({"NVDA": {"above": 100}})
    out = asyncio.run(r.step(c, prov(q("NVDA", 150, age_min=60)), box, NOW))
    assert out["sent"] == 0 and out["skip"] == 1 and not box.msgs


def test_closed_market_is_not_fetched():
    c, box = db(), Box()
    r = runner({"FPT.VN": {"above": 1}})
    calls = []

    class P:
        def fetch(self, syms):
            calls.append(syms)
            return {}

    out = asyncio.run(r.step(c, P(), box, NOW))
    assert out["n_sym"] == 0 and calls == [] and not box.msgs


def test_volume_needs_an_average_and_scales_with_time_of_day():
    c, box = db(), Box()
    # 10:00 New York = 30 phut vao phien ~ 12.5% khoi luong ca ngay.
    # TB 1M -> nhip thuong 125k. 300k = x2.4.
    r = runner({"NVDA": {"rvol": 2}}, refs={"NVDA": {"prev": 100, "adv": 1_000_000}})
    out = asyncio.run(r.step(c, prov(q("NVDA", 101, vol=300_000)), box, NOW))
    assert out["sent"] == 1 and "×2.4" in box.msgs[0], box.msgs
    # Khong co trung binh: bo qua co ly do, khong bao.
    c2, box2 = db(), Box()
    r2 = runner({"NVDA": {"rvol": 2}})
    out = asyncio.run(r2.step(c2, prov(q("NVDA", 101, vol=9_000_000)), box2, NOW))
    assert out["sent"] == 0 and out["skip"] == 1 and not box2.msgs


def test_volume_muted_in_the_first_minutes():
    c, box = db(), Box()
    early = dt.datetime(2026, 10, 2, 13, 35, tzinfo=dt.UTC)      # 9:35 NY
    r = runner({"NVDA": {"rvol": 2}}, refs={"NVDA": {"prev": 100, "adv": 1_000_000}})
    p = prov(q("NVDA", 101, vol=900_000, ts=(early - dt.timedelta(minutes=1)).isoformat()))
    assert asyncio.run(r.step(c, p, box, early))["sent"] == 0


def test_day_move_both_ways():
    c, box = db(), Box()
    r = runner({"SAP.DE": {"move": 4, "lists": ["DE"]}},
               refs={"SAP.DE": {"prev": 200, "adv": None}})
    out = asyncio.run(r.step(c, prov(q("SAP.DE", 190)), box, NOW))
    assert out["sent"] == 1 and "−5.0%" in box.msgs[0] and "🔻" in box.msgs[0]


def test_ref_from_rows_excludes_today():
    rows = [{"d": f"2026-09-{d:02d}", "c": 100 + d, "v": 1000} for d in range(1, 31)]
    rows.append({"d": "2026-10-02", "c": 999, "v": 10**9})
    ref = ua.ref_from_rows(rows, "2026-10-02")
    assert ref["prev"] == 130 and ref["adv"] == 1000 and ref["d"] == "2026-09-30"
    assert ua.ref_from_rows(rows[:3], "2026-10-02")["adv"] is None


def test_seen_is_pushed_on_change_only():
    c, seen = db(), []
    r = runner({"NVDA": {"above": 200}}, seen=seen)
    asyncio.run(r.step(c, prov(q("NVDA", 150)), Box(), NOW))
    asyncio.run(r.step(c, prov(q("NVDA", 150)), Box(), NOW + dt.timedelta(minutes=1)))
    assert len(seen) == 1 and seen[0]["n"] == 1 and seen[0]["open"] == ["US", "EU"]
    asyncio.run(r.step(c, prov(q("NVDA", 201)), Box(), NOW + dt.timedelta(minutes=2)))
    assert len(seen) == 2 and seen[1]["fired"][0]["sym"] == "NVDA"


def test_user_alerts_do_not_touch_tier1_state():
    c = db()
    r = runner({"NVDA": {"above": 1}})
    asyncio.run(r.step(c, prov(q("NVDA", 5)), Box(), NOW))
    assert watch.today_rows(c, "2026-10-02") == []
    assert watch.load_state(c, "2026-10-02")["last"] == {}
