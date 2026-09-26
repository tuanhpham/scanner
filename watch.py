"""watch.py - luat canh bao Tier 1 trong phien, va bo chong spam.

VI TRI TRONG HE THONG
---------------------
    watchlist.load()  -> danh sach + ke hoach lenh cua dem qua
    watchlist.gate()  -> che do cua phien (full / revert / manage / stop_only)
    positions.load()  -> vi the dang mo (de canh cat lo)
    quotes.fetch()    -> bao gia, kem dau moc va do tre
        └─ watch.evaluate()  LUAT: cai gi dang xay ra (ham thuan)
           └─ watch.decide() CHONG SPAM: cai gi duoc phep gui (doc DB)
              └─ render_watch  tin nhan

HAI NUA TACH RIENG LA CO Y. evaluate() tra loi "gia da cham muc chua", decide()
tra loi "co duoc gui khong". Gop lai thi khong test duoc mot cai ma khong dung
DB cua cai kia, va bo chong spam la thu duy nhat o day co the lam MAT mot canh
bao that.

BA LUAT TIER 1
--------------
    stop     gia xuyen muc cat lo cua mot vi the DANG MO
    trigger  gia cham diem vao cua ke hoach, KEM RVol da chuan hoa theo gio
    gap      mo cua lech qua nguong so voi nen quyet dinh

⚠️ RVOL PHAI DUOC CHUAN HOA THEO GIO. Luc 10:00 mot ma binh thuong moi chay
~20% khoi luong ngay, nen vol/adv50 tho luc do la 0.2 va nguong 1.5 KHONG BAO
GIO kich trong nua dau phien. Mot bo loc khong bao gio kich thi khong bao loi -
no im lang. Mau so lay tu vprofile.py (duong cong U cho CA THI TRUONG). Duong
cong RIENG TUNG MA (20 phien gan nhat) la buoc sau; den luc do `vprofile._FRAC`
tro thanh duong du phong chu khong phai duong duy nhat.

⚠️ KHONG BIET THI KHONG CANH, NHUNG PHAI NOI RA. Khong co adv50, nguon bao gia
khong cho khoi luong hop nhat (`vol_ok=False`), bao gia qua cu, thieu dau moc:
luat `trigger` KHONG kich. Ly do di vao `skip` va tu do vao tin nhan mo phien.
Mot ma bi bo qua ma khong ai biet thi te hon mot ma bi bo qua co ghi chu.

⚠️ VI THE DANG MO KHONG PHAI LA "DUOC THEM VAO DANH SACH". Chung duoc theo doi
cho DUNG mot viec: canh cat lo. Khong co luat vao lenh nao chay tren mot ma
khong co trong danh sach cua dem qua - do la dieu kien khong thuong luong cua
prompt 2, va no la thu chan co phieu rac quay lai.

TRANG THAI NAM TRONG DB, KHONG NAM TRONG BIEN
---------------------------------------------
Bang `watch_alert` co KHOA CHINH (d, sym, rule): "moi ma moi luat mot lan moi
phien" duoc SQLite bao dam, khong phai mot cai set trong RAM. Restart giua phien
thi khong canh lai nhung gi da canh - va do la yeu cau ro rang, khong phai mot
thu tot-thi-co.

Thuan stdlib (chi sqlite3) -> test chay khong can mang, khong can pandas.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path

import config
import positions
import quotes
import vprofile

ROOT = Path(__file__).resolve().parent
DB = ROOT / "state" / "baseline.db"

# Ba luat Tier 1 + ba tin mot-lan-moi-phien. `rule` cua tin mot lan dung chung
# bang `watch_alert` voi sym='' - khoa chinh (d, sym, rule) lo luon viec "chi
# mot lan moi phien" cho ca chung.
#   open      tin mo phien: hom nay canh gi, va vi sao
#   summary   tong ket cuoi phien
#   src_down  nguon bao gia khong phan hoi. Day la tin quan trong nhat trong ba:
#             mot API chet thi moi con lai cua phien im lang y nhu mot phien
#             binh thuong khong co gi xay ra.
TIER = {"stop": 1, "trigger": 1, "gap": 1}
ONCE = ("open", "summary", "src_down")

# Che do -> cac luat duoc phep. `stop` co trong MOI che do, ke ca DOWNTREND: do
# la ca dieu khoan cua "stop_only" trong prompt 2.
RULES_BY_MODE = {
    "full": ("stop", "trigger", "gap"),
    "revert": ("stop", "trigger", "gap"),
    "manage": ("stop", "gap"),      # `gap` chi cho ma DANG GIU, xem allowed()
    "stop_only": ("stop",),
}

DDL = """
CREATE TABLE IF NOT EXISTS watch_alert (
  d TEXT NOT NULL, sym TEXT NOT NULL, rule TEXT NOT NULL,
  ts_utc TEXT, tier INTEGER, px REAL, detail TEXT,
  PRIMARY KEY (d, sym, rule));
CREATE INDEX IF NOT EXISTS ix_watch_alert_d ON watch_alert(d);
"""


def con(db=DB) -> sqlite3.Connection:
    # busy_timeout giong bars.con(): baseline.db co NHIEU nguoi ghi cung luc -
    # main.py ghi khoa `beat` moi 20 giay ca ngay, nightly.py ghi de `struct` va
    # `candidates` moi sang. Mac dinh cua sqlite3 la 5 giay, va thu bi mat khi
    # het 5 giay o day la dong "da canh bao ma X" - mat dong do thi vong sau
    # canh lai chinh ma do. 30 giay la con so cua bars.con, giu giong nhau.
    c = sqlite3.connect(db, timeout=30)
    c.execute("PRAGMA busy_timeout=30000")
    c.row_factory = sqlite3.Row
    c.executescript(DDL)
    return c


# ───────────────────────── do luong ─────────────────────────
def rvol_adj(vol, adv50, sess: dict) -> float | None:
    """RVol da chuan hoa theo phan phien da qua. None = khong tinh duoc.

    None KHAC 0.0, va day la cho de nham nhat: 0.0 doc nhu "khoi luong rat
    thap" (mot ket luan), con None la "khong biet" (khong phai ket luan). Nguong
    1.5 so voi 0.0 se im lang y nhu so voi mot con so that thap.
    """
    v = quotes._num(vol)
    a = quotes._num(adv50)
    if v is None or a is None or a <= 0:
        return None
    frac = vprofile.session_frac(sess.get("state") or "LIVE", sess.get("mso"),
                                 sess.get("minutes") or vprofile.FULL_SESSION)
    return vprofile.rvol_at(v, a, frac)


def gap_pct(open_px, ref_close) -> float | None:
    o = quotes._num(open_px)
    r = quotes._num(ref_close)
    if o is None or r is None or r <= 0:
        return None
    return o / r - 1.0


def allowed(mode: str, rule: str, held: bool) -> bool:
    """Luat nay co duoc phep trong che do nay khong."""
    if rule not in RULES_BY_MODE.get(mode, ()):
        return False
    # "manage" = chi quan ly cai dang co. Mot gap cua ma minh khong giu la thong
    # tin de VAO LENH, va vao lenh la dung thu che do nay dang cam.
    if mode == "manage" and rule == "gap" and not held:
        return False
    return True


# ───────────────────────── luat ─────────────────────────
def evaluate(rows: list[dict], frame: dict, gate: dict, pos: dict,
             sess: dict, g: dict | None = None) -> tuple[list[dict], list[tuple]]:
    """HAM THUAN. Tra (cands, skip).

    `rows`  cac dong cua watchlist.load()["rows"] (da qua san chat luong)
    `frame` {sym: bao gia} tu quotes
    `gate`  watchlist.gate()
    `pos`   positions.load()
    `sess`  {"state","mso","minutes","d"}
    `cands` [{rule,tier,sym,px,ts,age_sec,val,detail,fields}] - chua xet spam
    `skip`  [(sym, ly do tieng Viet)] - nhung thu KHONG kiem duoc, de noi ra
    """
    g = config.INTRADAY if g is None else g
    max_age = float(g.get("max_quote_age_sec") or 1200)
    mode = gate.get("mode") or "stop_only"
    plan = {r["sym"]: r for r in rows or [] if r.get("sym")}
    held = pos.get("rows") or {}

    cands: list[dict] = []
    skip: list[tuple[str, str]] = []

    def q_ok(sym: str) -> dict | None:
        q = frame.get(sym)
        if q is None:
            skip.append((sym, "không có báo giá"))
            return None
        if q.get("err"):
            skip.append((sym, q["err"]))
            return None
        if not quotes.fresh(q, max_age):
            skip.append((sym, f"báo giá quá cũ ({quotes.fmt_age(q.get('age_sec'))})"))
            return None
        return q

    # ── luat `stop`: cho VI THE DANG MO, moi che do deu duoc ──
    for sym in sorted(held):
        row = held[sym]
        if not allowed(mode, "stop", True):
            break
        why = positions.comparable(row)
        if why or not row.get("stops"):
            continue          # positions.unchecked() da noi ra chuyen nay roi
        q = q_ok(sym)
        if q is None:
            continue
        h = positions.stop_hit(row, q["px"])
        if h["hit"] is None:
            continue
        cands.append({
            "rule": "stop", "tier": 1, "sym": sym, "px": q["px"],
            "ts": q["ts"], "age_sec": q["age_sec"], "val": h["hit"],
            "detail": (f"giá {q['px']:.2f} đã xuyên mức cắt lỗ {h['hit']:.2f}"
                       + (f" ({h['n']} mức bị xuyên)" if h["n"] > 1 else "")),
            "fields": {"hit": h["hit"], "n_hit": h["n"],
                       "shares": row.get("shares"),
                       "accts": row.get("accts") or [],
                       "stops": row.get("stops") or []},
        })

    # ── luat `trigger` va `gap`: chi tren danh sach cua dem qua ──
    for sym in sorted(plan):
        r = plan[sym]
        la_giu = sym in held
        muon_trigger = allowed(mode, "trigger", la_giu)
        muon_gap = allowed(mode, "gap", la_giu)
        if not (muon_trigger or muon_gap):
            continue
        q = q_ok(sym)
        if q is None:
            continue

        if muon_gap:
            gp = gap_pct(q.get("open"), r.get("ref_close"))
            if gp is None:
                skip.append((sym, "không tính được gap (thiếu giá mở hoặc nến "
                                  "quyết định)"))
            elif abs(gp) >= float(g.get("gap_alert") or 0.03):
                huong = "lên" if gp > 0 else "xuống"
                cands.append({
                    "rule": "gap", "tier": 1, "sym": sym, "px": q["px"],
                    "ts": q["ts"], "age_sec": q["age_sec"], "val": gp,
                    "detail": (f"mở cửa gap {huong} {abs(gp):.1%} so với nến "
                               f"quyết định ({r.get('ref_close'):.2f} → "
                               f"{q['open']:.2f})"),
                    # Ca ke hoach di kem, giong luat `trigger`: mot canh bao gap
                    # ma khong co cac moc cua dem qua thi buoc nguoi doc mo app
                    # ra tra - dung luc it thoi gian nhat.
                    "fields": {"gap": gp, "open": q["open"],
                               "ref_close": r.get("ref_close"),
                               "trigger": r.get("trigger"),
                               "stop": r.get("stop"),
                               "target": r.get("target"),
                               "stop_pct": r.get("stop_pct"),
                               "risk_pct": r.get("risk_pct"),
                               "size_pct": r.get("size_pct")},
                })

        if not muon_trigger:
            continue
        trig = quotes._num(r.get("trigger"))
        if trig is None:
            continue              # watchlist.check() da loai truong hop nay
        if q["px"] < trig:
            continue
        # Mo cua DA o tren diem vao thi khong con la "gia cham diem vao": no da
        # di qua truoc khi minh co co hoi. Luat `gap` la cho noi chuyen do.
        # Kiem bang gia MO CUA chu khong bang lan quet truoc: mot bien trong RAM
        # se mat sau restart, va mat theo dung cai cach khong ai thay.
        op = quotes._num(q.get("open"))
        if op is not None and op >= trig:
            skip.append((sym, f"mở cửa đã ở trên điểm vào ({op:.2f} ≥ "
                              f"{trig:.2f}) — xem cảnh báo gap"))
            continue
        if not q.get("vol_ok"):
            skip.append((sym, "nguồn báo giá không cho khối lượng hợp nhất — "
                              "không tính được RVol"))
            continue
        rv = rvol_adj(q.get("vol"), r.get("adv50"), sess)
        if rv is None:
            skip.append((sym, "chưa biết khối lượng trung bình (adv50) — không "
                              "tính được RVol"))
            continue
        nguong = float(g.get("min_rvol_adj") or 1.5)
        if rv < nguong:
            continue
        cands.append({
            "rule": "trigger", "tier": 1, "sym": sym, "px": q["px"],
            "ts": q["ts"], "age_sec": q["age_sec"], "val": rv,
            "detail": (f"giá {q['px']:.2f} chạm điểm vào {trig:.2f}, "
                       f"RVol {rv:.1f}× (ngưỡng {nguong:.1f}×)"),
            "fields": {"trigger": trig, "stop": r.get("stop"),
                       "target": r.get("target"), "rvol": rv,
                       "size_pct": r.get("size_pct"),
                       "risk_pct": r.get("risk_pct"),
                       "stop_pct": r.get("stop_pct"),
                       "unsure": r.get("unsure") or [],
                       "manual": bool(r.get("_manual"))},
        })
    return cands, skip


# ───────────────────────── chong spam ─────────────────────────
def load_state(c: sqlite3.Connection, d: str) -> dict:
    """Trang thai chong spam cua HOM NAY, doc tu DB.

    Doc lai moi vong thay vi giu trong RAM: mot vong quet doc mot bang mot dong
    la re, con mot bien RAM lech voi DB sau restart thi dat.
    """
    st = {"fired": {}, "last": {}, "n2": 0}
    for r in c.execute("SELECT sym, rule, ts_utc, tier FROM watch_alert "
                       "WHERE d=?", (d,)):
        st["fired"][(r["sym"], r["rule"])] = r["ts_utc"]
        if r["rule"] in ONCE:
            continue
        t = _epoch(r["ts_utc"])
        if t is not None:
            st["last"][r["sym"]] = max(t, st["last"].get(r["sym"], 0.0))
        if (r["tier"] or 0) >= 2:
            st["n2"] += 1
    return st


def _epoch(iso) -> float | None:
    try:
        return dt.datetime.fromisoformat(str(iso)).timestamp()
    except (TypeError, ValueError):
        return None


def decide(cands: list[dict], st: dict, now: dt.datetime,
           sess: dict, g: dict | None = None) -> tuple[list[dict], list[tuple]]:
    """Cai gi duoc phep gui. Tra (gui, giu) - `giu` kem ly do de ghi log.

    "Tha bo lo mot alert hon la nhan 40 cai. Im lang la mac dinh."
    """
    g = config.INTRADAY if g is None else g
    mute = float(g.get("open_mute_min") or 0)
    cd = float(g.get("cooldown_sec") or 0)
    cap2 = int(g.get("tier2_cap") or 0)
    mso = sess.get("mso")
    n2 = int(st.get("n2") or 0)
    gui, giu = [], []

    # Tier 1 truoc, roi den ma nao im lang lau hon. Thu tu chi quan trong khi
    # tran Tier 2 sap day, nhung luc do no quan trong that.
    for a in sorted(cands, key=lambda x: (x.get("tier", 9), x["sym"])):
        sym, rule, tier = a["sym"], a["rule"], a.get("tier", 1)

        if (sym, rule) in st["fired"]:
            giu.append((a, "đã cảnh báo trong phiên này"))
            continue
        # 10 phut dau phien: gia mo cua nhieu khi la mot cai rang cua, va mot
        # canh bao o do thuong la canh bao ve mot muc gia khong ton tai qua 60
        # giay. `stop` KHONG duoc mien tru o day: neu gia that su da xuyen muc
        # cat lo thi 10 phut nua no van xuyen, va tin nhan se dung hon.
        if mso is not None and mso < mute:
            giu.append((a, f"còn trong {mute:.0f} phút đầu phiên"))
            continue
        # Cooldown KHONG ap dung cho `stop`. Mot canh bao cat lo bi mot canh bao
        # gap cua 10 phut truoc chan lai la dung kieu loi khong the bien minh.
        if rule != "stop" and cd > 0:
            t = st["last"].get(sym)
            if t is not None and now.timestamp() - t < cd:
                con_lai = cd - (now.timestamp() - t)
                giu.append((a, f"đang trong thời gian nghỉ ({con_lai / 60:.0f} "
                               "phút nữa)"))
                continue
        if tier >= 2 and cap2 and n2 >= cap2:
            giu.append((a, f"đã đủ trần {cap2} cảnh báo Tier 2 của phiên"))
            continue

        gui.append(a)
        # Cap nhat NGAY trong vong: hai luat cung mot ma trong cung mot vong
        # phai chiu cooldown cua nhau, khong doi den vong sau.
        st["fired"][(sym, rule)] = now.isoformat(timespec="seconds")
        st["last"][sym] = now.timestamp()
        if tier >= 2:
            n2 += 1
            st["n2"] = n2
    return gui, giu


def record(c: sqlite3.Connection, d: str, a: dict, now: dt.datetime) -> bool:
    """Ghi mot canh bao da gui. False = da co (khoa chinh chan lai).

    Ghi SAU khi gui duoc, khong phai truoc: mot tin nhan khong den duoc ma da
    ghi la "da canh bao" thi mat han. Nguoc lai - gui hai lan - chi la mot tin
    nhan trung, va do la cai gia re hon.

    `with c:` chu KHONG phai execute() roi commit(). Day la dong quan trong nhat
    trong ham, va no da tung sai: python mo transaction ngam truoc INSERT, nen
    khi khoa chinh chan lai thi IntegrityError bay ra TRUOC commit() va de lai
    mot transaction ghi MO. watchd.py giu mot ket noi duy nhat ca phien, va
    `once()` duoi day tra False o moi vong tu vong thu hai — nghia la ket noi do
    giu khoa ghi lien tuc tu phut thu hai cua phien den luc restart. Hau qua
    khong nam o day ma o cho khac trong may: `bars.sync` cua nightly doi het 30
    giay roi do `database is locked`, va `PRAGMA journal_mode=WAL` khong bao gio
    doi duoc che do nen kho nen ket o rollback journal. `with c:` commit khi
    thanh cong va ROLLBACK khi nem, nen khoa duoc tha trong ca hai duong.
    """
    try:
        with c:
            c.execute("INSERT INTO watch_alert(d,sym,rule,ts_utc,tier,px,detail) "
                      "VALUES(?,?,?,?,?,?,?)",
                      (d, a["sym"], a["rule"], now.isoformat(timespec="seconds"),
                       a.get("tier", 1), quotes._num(a.get("px")),
                       a.get("detail", "")))
        return True
    except sqlite3.IntegrityError:
        return False


def once(c: sqlite3.Connection, d: str, kind: str,
         now: dt.datetime | None = None) -> bool:
    """Danh dau mot tin mot-lan-moi-phien. True = chua gui lan nao.

    Dung cho tin mo phien va tong ket ket phien. Cung mot bang, cung mot khoa
    chinh, nen restart giua phien khong gui lai tin mo phien - thu ma mot bien
    trong RAM khong lam duoc.

    Duong "da gui roi" o day chay moi vong quet ca phien, nen `with c:` la bat
    buoc chu khong phai cho gon: xem record() ben tren.
    """
    if kind not in ONCE:
        raise ValueError(f"kind phai thuoc {ONCE}: {kind!r}")
    now = now or dt.datetime.now(dt.UTC)
    try:
        with c:
            c.execute("INSERT INTO watch_alert(d,sym,rule,ts_utc,tier,px,detail) "
                      "VALUES(?,'',?,?,0,NULL,'')",
                      (d, kind, now.isoformat(timespec="seconds")))
        return True
    except sqlite3.IntegrityError:
        return False


def today_rows(c: sqlite3.Connection, d: str) -> list[dict]:
    """Cac canh bao da gui hom nay, cu nhat truoc. Dung cho tong ket va dashboard."""
    return [dict(r) for r in c.execute(
        "SELECT sym, rule, ts_utc, tier, px, detail FROM watch_alert "
        "WHERE d=? AND sym<>'' ORDER BY ts_utc", (d,))]


# ───────────────────────── CLI / selftest ─────────────────────────
def _smoke() -> None:
    now = dt.datetime(2026, 9, 25, 15, 0, tzinfo=dt.UTC)
    sess = {"state": "LIVE", "mso": 90, "minutes": 390, "d": "2026-09-25"}
    G = dict(config.INTRADAY)
    rows = [{"sym": "NVDA", "ref_close": 100.0, "trigger": 102.0, "stop": 97.0,
             "target": 112.0, "adv50": 1_000_000, "size_pct": 0.2}]

    def frame(px, vol, op=100.5, sym="NVDA", age=300.0):
        return {sym: {"sym": sym, "px": px, "open": op, "hi": px, "lo": op,
                      "vol": vol, "ts": now.isoformat(), "age_sec": age,
                      "n_bars": 60, "src": "t", "vol_ok": True, "err": None}}

    full = {"mode": "full", "allow_new": True}
    khong = {"known": False, "rows": {}}

    # RVol phai theo GIO. Luc mso=90 ky vong ~25% khoi luong ngay, nen 400k tren
    # adv50 1M la ~1.6x, con vol/adv tho chi la 0.4 - tho thi khong bao gio kich.
    rv = rvol_adj(400_000, 1_000_000, sess)
    assert rv and 1.5 < rv < 1.7, rv
    assert 400_000 / 1_000_000 < 1.5, "day chinh la cho bo loc tho im lang"
    assert rvol_adj(400_000, None, sess) is None, "khong biet != 0"

    # trigger: cham diem vao + RVol dat.
    c1, _ = evaluate(rows, frame(102.5, 400_000), full, khong, sess, G)
    assert [a["rule"] for a in c1] == ["trigger"], c1
    assert c1[0]["fields"]["stop"] == 97.0

    # Chua cham diem vao -> im.
    assert evaluate(rows, frame(101.0, 900_000), full, khong, sess, G)[0] == []
    # Cham nhung khoi luong khong dat -> im.
    assert evaluate(rows, frame(102.5, 50_000), full, khong, sess, G)[0] == []
    # Khong tinh duoc RVol -> KHONG canh, nhung phai noi ra.
    c2, sk = evaluate([{**rows[0], "adv50": None}], frame(102.5, 400_000),
                      full, khong, sess, G)
    assert c2 == [] and any("adv50" in s[1] for s in sk), (c2, sk)
    f = frame(102.5, 400_000)
    f["NVDA"]["vol_ok"] = False
    c3, sk3 = evaluate(rows, f, full, khong, sess, G)
    assert c3 == [] and any("hợp nhất" in s[1] for s in sk3), sk3

    # Mo cua da o tren diem vao -> khong phai "cham diem vao" (day la gap).
    c4, sk4 = evaluate(rows, frame(105.0, 900_000, op=104.0), full, khong, sess, G)
    assert [a["rule"] for a in c4] == ["gap"], c4
    assert any("mở cửa đã ở trên" in s[1] for s in sk4), sk4
    # Mo cua tren diem vao nhung gap chua du nguong: khong canh gi ca. Day la
    # truong hop de bo sot nhat, va no CO Y im lang - nhung `skip` phai noi ra.
    c4b, sk4b = evaluate(rows, frame(103.0, 900_000, op=102.5), full, khong,
                         sess, G)
    assert c4b == [] and any("mở cửa đã ở trên" in s[1] for s in sk4b), (c4b, sk4b)

    # Bao gia qua cu -> khong canh gi ca, va noi ra.
    c5, sk5 = evaluate(rows, frame(102.5, 900_000, age=3600), full, khong, sess, G)
    assert c5 == [] and any("quá cũ" in s[1] for s in sk5), sk5

    # gap: 100 -> 104 la 4% > 3%.
    c6, _ = evaluate(rows, frame(104.5, 900_000, op=104.0), full, khong, sess, G)
    assert {a["rule"] for a in c6} == {"gap"}, c6
    assert "gap lên 4" in c6[0]["detail"], c6[0]["detail"]

    # stop: vi the dang mo, va DOWNTREND cung phai canh.
    pos = positions.parse({"rows": [{"sym": "AAPL", "shares": 10, "cur": "USD",
                                     "stops": [90, 112], "avgCost": 100}]},
                          int(now.timestamp() * 1000) - 60_000, now=now)
    fr = frame(100.0, 1, sym="AAPL")
    c7, _ = evaluate([], fr, {"mode": "stop_only"}, pos, sess, G)
    assert [a["rule"] for a in c7] == ["stop"] and c7[0]["val"] == 112.0, c7
    # DOWNTREND: khong luat vao lenh nao duoc chay.
    c8, _ = evaluate(rows, {**frame(102.5, 900_000), **fr},
                     {"mode": "stop_only"}, pos, sess, G)
    assert {a["rule"] for a in c8} == {"stop"}, c8
    # EUR: khong canh, va positions.unchecked() la cho noi ra.
    eur = positions.parse({"rows": [{"sym": "AAPL", "shares": 10, "cur": "EUR",
                                     "stops": [112]}]},
                          int(now.timestamp() * 1000), now=now)
    assert evaluate([], fr, {"mode": "stop_only"}, eur, sess, G)[0] == []

    # decide(): 10 phut dau phien thi giu lai, ke ca stop.
    st = {"fired": {}, "last": {}, "n2": 0}
    g1, h1 = decide(c1, dict(st, fired={}), now, {**sess, "mso": 3}, G)
    assert g1 == [] and "đầu phiên" in h1[0][1], h1

    # Moi ma moi luat mot lan moi phien.
    s2 = {"fired": {}, "last": {}, "n2": 0}
    assert len(decide(c1, s2, now, sess, G)[0]) == 1
    assert decide(c1, s2, now, sess, G)[0] == [], "lan hai phai bi chan"

    # Cooldown chan luat KHAC cung ma, nhung khong chan `stop`.
    s3 = {"fired": {}, "last": {"NVDA": now.timestamp() - 60}, "n2": 0}
    assert decide(c6, s3, now, sess, G)[0] == [], "gap phai bi cooldown chan"
    s4 = {"fired": {}, "last": {"AAPL": now.timestamp() - 60}, "n2": 0}
    assert len(decide(c7, s4, now, sess, G)[0]) == 1, "stop khong bi cooldown"

    # Tran Tier 2 (chua co luat Tier 2 nao, nhung co che phai san va dung).
    t2 = [{"rule": "x", "tier": 2, "sym": "AAA", "px": 1.0, "detail": ""}]
    s5 = {"fired": {}, "last": {}, "n2": G["tier2_cap"]}
    assert decide(t2, s5, now, sess, G)[0] == [], "tran Tier 2 phai chan"
    s6 = {"fired": {}, "last": {}, "n2": 0}
    assert len(decide(t2, s6, now, sess, G)[0]) == 1
    # Tier 1 KHONG bi tran do chan.
    s7 = {"fired": {}, "last": {}, "n2": 999}
    assert len(decide(c1, s7, now, sess, G)[0]) == 1, "Tier 1 khong co tran"

    assert allowed("stop_only", "stop", False) and not allowed("stop_only", "gap", True)
    assert allowed("manage", "gap", True) and not allowed("manage", "gap", False)
    assert not allowed("manage", "trigger", True)
    # Moi che do watchlist.gate() co the tra ve phai co mot dong o bang tren. Mot
    # che do thieu nghia la khong luat nao chay - va thieu `stop` nghia la mot
    # phien khong ai canh cat lo, mot cach im lang.
    for m in ("full", "revert", "manage", "stop_only"):
        assert "stop" in RULES_BY_MODE.get(m, ()), m
    print("watch.py: smoke ok")


def _show(db=DB) -> int:
    d = dt.datetime.now(dt.UTC).date().isoformat()
    c = con(db)
    st = load_state(c, d)
    rows = today_rows(c, d)
    c.close()
    print(f"CANH BAO HOM NAY ({d}): {len(rows)} tin"
          f" · Tier 2 da dung {st['n2']}/{config.INTRADAY['tier2_cap']}")
    for r in rows:
        print(f"  {r['ts_utc'][11:19]}  T{r['tier']} {r['sym']:<6} "
              f"{r['rule']:<8} {r['detail']}")
    for k in ONCE:
        print(f"  tin `{k}`: {'da gui' if (k in [x[1] for x in st['fired']]) else 'chua'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--show", action="store_true", help="canh bao da gui hom nay")
    a = ap.parse_args()
    if a.show:
        return _show(a.db)
    _smoke()
    return 0


if __name__ == "__main__":
    sys.exit(main())
