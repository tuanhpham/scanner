"""main.py - Vong lap chinh: clock -> universe -> scorer -> Telegram.

Chay lien tuc 24/7. Tu bat/tat theo lich phien NYSE.
    python main.py              # chay that
    python main.py --dry        # khong gui Telegram, chi in ra
    python main.py --once       # quet 1 lan roi thoat (test)
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sqlite3
import time
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

import edgar
import events
import halts
import news
import notifier
import outcome
import render
import scorer
import store
import tgapi
import universe_live
from callbacks import Callbacks
from clock import DE, SessionClock
from render import esc

ROOT = Path(__file__).resolve().parent
DB = ROOT / "state" / "baseline.db"

UNIVERSE_SEC = 60      # Yahoo gioi han ~1 req/60s
SCORE_SEC = 25
ALERT_SCORE = 7.0
ESCALATE_DELTA = 3.0   # diem tang thap nay -> gui lai
COOLDOWN = 540         # 9 phut moi ma
MAX_ALERTS = 45        # tran moi phien, chong spam

# --- Nut "Theo doi" ---
TRACK_SEC = 45         # tu sua lai tin nhan cua ma dang theo doi
TRACK_ESCALATE = 1.5   # nguong gui lai, nhay hon ESCALATE_DELTA thuong
MAX_TRACK = 10         # tgapi co MIN_GAP 1.2s -> 10 ma = 12s moi vong

OUTCOME_SEC = 60       # do ket qua alert: dien px15/px60/dinh/day
HALT_SEC = halts.POLL  # 60 - Nasdaq khong cho query nhanh hon 1 lan/phut
NEWS_SEC = news.POLL   # 30 - tin la chuyen cua phut, khong can nhanh hon
SEC_RISK_MAX = 3.0     # risk >= nguong nay -> tru diem
SEC_PENALTY = 2.0      # tru DUNG MOT LAN, du ca SEC va tin cung bao pha loang
MIN_MSO = 5            # bo qua alert trong 5 phut dau phien
BAD_SUFFIX = ("W", "WS", "R", "RT", "U", "UN", "PR")

# Trang thai dung chung cho nut Refresh (callbacks chay ngoai vong scan).
_ST: "State | None" = None
_CK: SessionClock | None = None
_SENT: dict[str, float] = {}     # sym -> lan gui gan nhat (cooldown)
_HB = halts.HaltBook()           # so tay halt, loop_halts cap nhat moi 60s
_NB = news.NewsBook()            # so tay tin, loop_news cap nhat moi 30s
_BLOCKED: set[str] = set()       # ma da bi chan vi halt sev 3 (chi log 1 lan)
_NEWS_BAD: set[str] = set()      # ma da bao tin xau (chi log 1 lan/phien)
_SPOOL = notifier.Spool()        # tin chua gui duoc, giu tren dia qua restart

DDL = """
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc TEXT, ts_et TEXT, kind TEXT, sym TEXT, score REAL,
  px REAL, chg REAL, rvol REAL, atr_move REAL, float_rot REAL,
  dollar_vol REAL, freshness TEXT, sources TEXT);
CREATE INDEX IF NOT EXISTS ix_alerts_sym ON alerts(sym);
CREATE INDEX IF NOT EXISTS ix_alerts_ts ON alerts(ts_utc);
"""


class State:
    def __init__(self) -> None:
        self.universe: dict = {}
        self.universe_ts: float = 0.0
        self.base: dict = {}
        self.seen: dict[str, dict] = {}   # sym -> {best, alerts}
        self.n_alerts = 0
        self.day: dt.date | None = None
        self.done: set[str] = set()
        self.errors = 0
        self.scans = 0


def log(msg: str) -> None:
    print(f"[{dt.datetime.now(DE):%H:%M:%S}] {msg}", flush=True)


def junk_ticker(s: str) -> bool:
    s = (s or "").upper()
    if "." in s or "-" in s:
        suf = s.replace("-", ".").partition(".")[2]
        return suf in BAD_SUFFIX
    return len(s) == 5 and s[-1] in ("W", "R", "U")


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.executescript(DDL)
    return con


def restore_today(st: State) -> int:
    """Nap lai cac ma da alert hom nay -> restart giua phien khong bao trung."""
    if st.day is None:
        return 0
    try:
        con = db()
        rows = con.execute(
            "SELECT sym, MAX(score), COUNT(*) FROM alerts "
            "WHERE substr(ts_et,1,10)=? GROUP BY sym",
            (st.day.isoformat(),)).fetchall()
        con.close()
    except Exception as e:  # noqa: BLE001
        log(f"restore_today loi: {e}")
        return 0
    for sym, best, cnt in rows:
        st.seen[sym] = {"best": float(best), "alerts": int(cnt)}
        st.n_alerts += int(cnt)
    return len(rows)


def loud_mode(score: float) -> bool:
    """Gio Duc: 09-17h im lang (dang lam viec), sau 17h moi reo."""
    h = dt.datetime.now(DE).hour
    if 9 <= h < 17:
        return score >= 12.0
    return True


# ---------------- render + gui ----------------
_SESS = {"PREMARKET": "PRE", "PRE": "PRE", "AFTERHOURS": "POST",
         "POST": "POST", "CLOSED": "CLOSED", "OPEN": "LIVE",
         "REGULAR": "LIVE", "RTH": "LIVE", "LIVE": "LIVE"}


def session_state(ck: SessionClock | None) -> str:
    try:
        s = (ck.state() or "").upper()
    except Exception:  # noqa: BLE001
        return "LIVE"
    return _SESS.get(s, "LIVE" if (ck.mso() or 0) > 0 else "CLOSED")


def build_view(h: dict, kind: str, ck: SessionClock | None = None,
               detail: bool | None = None) -> render.AlertView:
    """Dict tu scorer -> AlertView day du (SEC, delta, trang thai theo doi).

    detail=None nghia la "tu quyet": muc 3 mo san khoi VI SAO, muc 1-2 dong.
    Callbacks truyen True/False tuong minh khi nguoi dung bam nut Chi tiet.
    """
    ck = ck or _CK
    try:
        sec = edgar.assess(h["sym"])
    except Exception:  # noqa: BLE001
        sec = None
    upd, mso, smin = "", None, 390
    try:
        upd = ck.now_et(dt.datetime.now(dt.timezone.utc)).strftime("%H:%M")
        mso = ck.mso()
        smin = ck.session_minutes() or 390     # 210 neu la nua phien
    except Exception:  # noqa: BLE001
        pass
    try:
        tracked = store.is_tracked(DB, h["sym"])
        prev = store.get_snap(DB, h["sym"])
    except Exception as e:  # noqa: BLE001
        log(f"store doc loi {h['sym']}: {e}")
        tracked, prev = False, None
    v = render.AlertView.from_scan(
        h, sec=sec, prev=prev, kind=kind, session=session_state(ck),
        updated=upd, mso=mso, session_min=smin, detail=False,
        tracked=tracked,
        # Nut "Tin" giu tim kiem tong: khoi CATALYST da dat link vao dung ban
        # tin quan trong nhat, nut nay de xem CAC tin con lai.
        news_url=f"https://www.google.com/search?q={h['sym']}+stock&tbm=nws",
        halt=_HB.view(h["sym"]), news=_NB.view(h["sym"]))
    v.detail = (v.level == 3) if detail is None else bool(detail)
    return v


def fmt(h: dict, kind: str, ck: SessionClock) -> str:
    """Giu lai cho tuong thich - chi tra ve text, khong nut."""
    return render.render_alert(build_view(h, kind, ck))


async def notify(text: str, loud: bool = False) -> bool:
    """Gui mot tin thuong (heartbeat, ket phien). That bai -> vao spool."""
    if await tgapi.send(text, loud=loud):
        return True
    _SPOOL.add(text, loud)
    log(f"tgapi that bai -> spool ({len(_SPOOL)} tin cho)")
    return False


async def tg_send(v: render.AlertView, loud: bool) -> bool:
    """Gui alert kem nut. That bai -> vao spool va VAN tinh la da gui.

    Tinh la da gui vi tin nam trong spool se den tay nguoi doc; neu tra False
    thi loop_score se cham diem lai ma nay o vong sau va gui trung.
    """
    now = time.time()
    if now - _SENT.get(v.sym, 0) < COOLDOWN:
        log(f"{v.sym}: trong cooldown, bo qua")
        return False
    txt = render.render_alert(v)
    mid = await tgapi.send(txt, markup=render.render_keyboard(v), loud=loud)
    if not mid:
        _SPOOL.add(txt, loud)
        log(f"{v.sym}: tgapi that bai -> spool ({len(_SPOOL)} tin cho), "
            f"tin gui bu se khong co nut")
        _SENT[v.sym] = now
        return True
    _SENT[v.sym] = now
    try:
        store.put_msg(DB, v.sym, mid, v.snapshot())
    except Exception as e:  # noqa: BLE001
        log(f"store ghi loi {v.sym}: {e}")
    # Gui duoc tin nay = mang da song lai -> tra no cu.
    if len(_SPOOL):
        await _SPOOL.flush()
    return True


async def refresh_one(sym: str) -> dict | None:
    """Cham diem lai mot ma khi nguoi dung bam Cap nhat.

    Dung score_sym chu khong phai rank(): rank() ap bo loc nen ma da nguoi
    se tra ve rong -> tin nhan roi ve snapshot cu. Ma nguoi van phai xem
    duoc diem moi cua no.

    Luu y: st.universe chi lam moi moi UNIVERSE_SEC (60s), nen bam hai lan
    trong vong 60s se ra cung vol/px - chi frac (va do RVOL) nhich len.
    """
    if _ST is None or _CK is None:
        return None
    row = (_ST.universe or {}).get(sym)
    if row is None:
        return None                      # ngoai phien / khong con trong universe
    return await asyncio.to_thread(scorer.score_sym, sym, row, _ST.base, _CK)


# ---------------- cac vong lap ----------------
async def loop_universe(st: State, ck: SessionClock) -> None:
    while True:
        if ck.scanning():
            try:
                u = await asyncio.to_thread(universe_live.build)
                if u:
                    st.universe = u
                    st.universe_ts = dt.datetime.now().timestamp()
                    log(f"universe = {len(u)} ma")
            except Exception as e:  # noqa: BLE001
                st.errors += 1
                log(f"[universe] {type(e).__name__}: {e}")
        await asyncio.sleep(UNIVERSE_SEC)


async def loop_score(st: State, ck: SessionClock, dry: bool) -> None:
    con = db()
    while True:
        if ck.scanning() and st.universe:
            try:
                hits, rej = await asyncio.to_thread(
                    scorer.rank, st.universe, st.base, ck)
                st.scans += 1
                now = dt.datetime.now(dt.timezone.utc)
                et = ck.now_et(now)
                trk = set(store.tracked_syms(DB))

                for h in hits:
                    if h["score"] < ALERT_SCORE:
                        continue
                    sym = h["sym"]
                    if junk_ticker(sym):
                        log(f"bo qua {sym}: warrant/unit/right")
                        continue
                    # SEC dinh chi giao dich / huy niem yet (sev 3): khong phai
                    # co hoi, la bay. Chan han thay vi canh bao trong tin nhan.
                    if (hb := _HB.blocked(sym)):
                        if sym not in _BLOCKED:
                            _BLOCKED.add(sym)
                            log(f"CHAN {sym}: {hb['code']} · {hb['label']} "
                                f"(tu {hb['since']} ET)")
                            # Chi ghi lan dau: vong quet 25s, khong thi mot ma
                            # bi dinh chi ca ngay se chiem ca file.
                            events.emit("halt_block", sym=sym,
                                        code=hb["code"], sev=hb["sev"],
                                        score=h["score"])
                        continue
                    _m = ck.mso()
                    if _m is not None and _m < MIN_MSO:
                        continue
                    prev = st.seen.get(sym)
                    # Ma dang theo doi: nguong gui lai thap hon -> bot bao
                    # som hon khi no manh len.
                    delta = TRACK_ESCALATE if sym in trk else ESCALATE_DELTA
                    if prev is None:
                        kind = "NEW"
                    elif h["score"] >= prev["best"] + delta:
                        kind = "UP"
                    else:
                        st.seen[sym]["best"] = max(prev["best"], h["score"])
                        continue
                    try:
                        h["sec_risk"] = edgar.assess(sym)["risk"]
                    except Exception:  # noqa: BLE001
                        h["sec_risk"] = None
                    # Tin "Pricing of Offering" va ho so 424B5 la CUNG mot su
                    # kien: ban tin ra truoc vai gio, EDGAR den sau. Vi vay lay
                    # max() roi tru MOT lan — tru hai lan la phat trung.
                    _nv = _NB.view(sym) or {}
                    h["news_group"] = _nv.get("group")
                    h["news_risk"] = float(_nv.get("risk") or 0.0)
                    _sr = float(h["sec_risk"] or 0.0)
                    if max(_sr, h["news_risk"]) >= SEC_RISK_MAX:
                        _src = []
                        if _sr >= SEC_RISK_MAX:
                            _src.append(f"SEC risk {_sr}")
                        if h["news_risk"] >= SEC_RISK_MAX:
                            _src.append(f"tin {h['news_group']}")
                        h["score"] -= SEC_PENALTY
                        log(f"{sym}: {' + '.join(_src)} -> tru {SEC_PENALTY} "
                            f"diem, con {h['score']:.1f}")
                        if h["score"] < ALERT_SCORE:
                            st.seen[sym] = {
                                "best": h["score"],
                                "alerts": prev["alerts"] if prev else 0}
                            continue
                    # Ma dang theo doi khong bi tran MAX_ALERTS chan lai.
                    if st.n_alerts >= MAX_ALERTS and sym not in trk:
                        continue

                    st.seen[sym] = {"best": h["score"],
                                    "alerts": (prev["alerts"] + 1) if prev else 1}
                    v = build_view(h, kind, ck)
                    if dry:
                        log(f"[DRY {kind}] {sym} {h['score']:.1f} L{v.level}")
                    else:
                        if not await tg_send(v, loud_mode(h["score"])):
                            continue
                    st.n_alerts += 1
                    ts_now = time.time()
                    if not dry:
                        # Mo mot dong `outcome` de do xem alert nay dung hay sai.
                        # Chi ghi khi da gui that: --dry khong thu so lieu.
                        outcome.record(DB, h, v.level, ts=ts_now)
                    # Log co cau truc: CA trong --dry (chi doc, khong gui gi) va
                    # cung `ts_now` voi outcome de join duoc chinh xac.
                    events.alert(h, v.level, kind, ts=ts_now,
                                 ts_et=et.isoformat(timespec="seconds"),
                                 mso=_m, session=v.session, dry=dry,
                                 halt=(v.halt or {}).get("code"))
                    con.execute(
                        "INSERT INTO alerts(ts_utc,ts_et,kind,sym,score,px,chg,"
                        "rvol,atr_move,float_rot,dollar_vol,freshness,sources)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (now.isoformat(timespec="seconds"),
                         et.isoformat(timespec="seconds"), kind, sym,
                         h["score"], h["px"], h["chg"], h["rvol"],
                         h["atr_move"], h["float_rot"], h["dollar_vol"],
                         h["freshness"], ",".join(h["sources"])))
                    con.commit()
                    log(f"ALERT {kind} {sym} score={h['score']:.1f} "
                        f"rvol={h['rvol']:.1f}x L{v.level}")

                if st.scans % 8 == 1:
                    log(f"scan#{st.scans} qua_loc={rej.get('_qua_loc', 0)} "
                        f"frac={rej.get('_frac')} alerts={st.n_alerts}")
            except Exception:  # noqa: BLE001
                st.errors += 1
                log("[score] " + traceback.format_exc(limit=2))
        await asyncio.sleep(SCORE_SEC)


async def loop_track(st: State, ck: SessionClock, dry: bool) -> None:
    """Ma dang theo doi: tu sua lai chinh tin nhan alert cua no moi TRACK_SEC.

    Khong gui tin moi (viec do de loop_score lam khi diem tang), va KHONG ghi
    lai snapshot - nho vay cot delta trong panel van do tu lan gui that gan
    nhat, thay vi bi reset ve 0 sau moi lan tu cap nhat.
    """
    while True:
        await asyncio.sleep(TRACK_SEC)
        if dry or not ck.scanning() or not st.universe:
            continue
        syms = store.tracked_syms(DB)
        if len(syms) > MAX_TRACK:
            log(f"theo doi: {len(syms)} ma vuot tran, chi cap nhat "
                f"{MAX_TRACK} ma moi nhat")
            syms = syms[:MAX_TRACK]
        for sym in syms:
            try:
                mid, _ = store.get_msg(DB, sym)
                if not mid:
                    continue          # chua tung gui alert -> khong co gi de sua
                row = st.universe.get(sym)
                if row is None:
                    continue          # roi khoi universe, khong co gia moi
                h = await asyncio.to_thread(
                    scorer.score_sym, sym, row, st.base, ck)
                if not h:
                    continue
                v = build_view(h, "TRK", ck)
                if not await tgapi.edit(mid, render.render_alert(v),
                                        render.render_keyboard(v)):
                    log(f"theo doi {sym}: sua tin that bai")
            except Exception as e:  # noqa: BLE001
                log(f"theo doi {sym}: {type(e).__name__}: {e}")


async def loop_outcome(st: State, ck: SessionClock, dry: bool) -> None:
    """Do ket qua cua alert da gui: gia sau 15p/60p, dinh va day.

    Chi doc gia da co trong st.universe -> khong goi API nao them. Ma nguoi di
    va roi khoi screener thi khong lay duoc gia; outcome.backfill() (chay luc
    sang ngay moi) se dien lai bang nen 1 phut cua yfinance.
    """
    while True:
        await asyncio.sleep(OUTCOME_SEC)
        if dry or not ck.scanning() or not st.universe:
            continue
        try:
            await asyncio.to_thread(outcome.update, DB, st.universe)
        except Exception as e:  # noqa: BLE001
            log(f"outcome: {type(e).__name__}: {e}")


async def loop_halts(st: State, ck: SessionClock) -> None:
    """Doc feed tam dung giao dich cua Nasdaq moi 60s.

    Chay ca trong --dry (chi doc, khong gui gi) va ca ngoai gio quet ban: halt
    dat truoc phien van con hieu luc khi mo cua. Chi nghi khi da dong han
    phien, de khong goi mang vo ich luc dem.

    Nasdaq gioi han 1 query/phut nen KHONG duoc giam HALT_SEC.
    """
    n_ok = 0
    while True:
        if session_state(ck) != "CLOSED":
            r = await asyncio.to_thread(_HB.refresh)
            if r < 0:
                # Loi mang le thi bo qua; loi lien tiep moi dang bao.
                if _HB.n_fail in (3, 30):
                    log(f"halts: {_HB.n_fail} lan lien tiep khong lay duoc "
                        f"feed ({_HB.err})")
            else:
                n_ok += 1
                act = _HB.active_syms()
                # Log khi co ma DANG halt nam trong universe cua minh — halt
                # cua ca thi truong thi khong lien quan.
                mine = [s for s in act if s in st.universe]
                if mine or n_ok == 1:
                    log(f"halts: {len(act)} ma dang halt"
                        + (f" · trong universe: {' '.join(mine)}" if mine else ""))
        await asyncio.sleep(HALT_SEC)


async def loop_news(st: State, ck: SessionClock) -> None:
    """Doc tin Alpaca moi 30s de biet VI SAO mot ma dang chay.

    Chay ca trong --dry va ca ngoai gio quet: ban tin quan trong nhat trong
    ngay thuong ra truoc 09:30 (pre-market), va den luc mo cua thi no van la
    ly do. Chi nghi khi da dong han phien.

    Don so tay theo universe sau moi lan lay: khong lam vay thi mot ngay giao
    dich se tich toan bo tin cua ca thi truong trong RAM.
    """
    n_ok = 0
    while True:
        if session_state(ck) != "CLOSED":
            r = await asyncio.to_thread(_NB.refresh)
            if r < 0:
                # Loi le thi bo qua. Loi lien tiep moi dang bao — nhat la 403:
                # nghia la goi Alpaca khong kem quyen doc tin.
                if _NB.n_fail in (3, 30):
                    log(f"news: {_NB.n_fail} lan lien tiep khong lay duoc tin "
                        f"({_NB.err})")
            else:
                n_ok += 1
                if st.universe:
                    _NB.prune(keep=set(st.universe))
                # Chi ghi ma tin xau MOI: vong 30s, khong thi mot ma pha loang
                # se lap lai suot phien va lam ngap log.
                fresh = [s for s in _NB.risky_syms()
                         if s in st.universe and s not in _NEWS_BAD]
                _NEWS_BAD.update(fresh)
                if fresh:
                    log("news: tin xau -> " + " · ".join(
                        f"{s} {(_NB.view(s) or {}).get('group')}"
                        for s in fresh))
                elif n_ok == 1 or n_ok % 20 == 0:
                    log(f"news: {len(_NB.by_sym)} ma co tin trong "
                        f"{news.WINDOW_H}h")
        await asyncio.sleep(NEWS_SEC)


async def loop_clock(st: State, ck: SessionClock, dry: bool) -> None:
    while True:
        try:
            et = ck.now_et()
            if et.date() != st.day:
                st.day = et.date()
                st.done.clear()
                st.seen.clear()
                st.n_alerts = 0
                st.scans = 0
                st.errors = 0
                _SENT.clear()
                _BLOCKED.clear()
                _NEWS_BAD.clear()
                st.base = await asyncio.to_thread(scorer.load_baseline)
                log(f"=== ngay moi {st.day} | baseline {len(st.base)} ma ===")
                k = restore_today(st)
                if k:
                    log(f"khoi phuc {k} ma da alert hom nay ({st.n_alerts} alert)")
                # Theo doi chi co hieu luc trong phien. Dung tuoi thay vi
                # "xoa het luc khoi dong" de restart giua phien khong mat list.
                if (p := store.prune_track(DB)):
                    log(f"don {p} ma theo doi cua phien truoc")
                # Chot lai so lieu outcome cua phien truoc bang nen 1 phut.
                # Lam luc sang ngay moi (~00:00 ET) de chac du lieu da on dinh.
                if not dry and (od := outcome.last_open_day(DB)):
                    r = await asyncio.to_thread(outcome.backfill, DB, od)
                    log(f"outcome {od}: chot {r.get('fixed')}/{r.get('rows')} "
                        f"alert" + (f" (loi: {r['err']})" if r.get("err") else ""))

            state = ck.state()

            if state == "PREMARKET" and "hb" not in st.done:
                st.done.add("hb")
                lo, lc = ck.local_open(), ck.local_close()
                if lo is None or lc is None:
                    log("heartbeat: bo qua, clock chua co gio phien")
                    st.done.discard("hb")
                    await asyncio.sleep(20)
                    continue

                skew = ck.dst_skew()
                warn = "\n⚠️ <i>DST lech: phien mo som 1 gio</i>" if skew == 5 else ""
                half = " (NUA PHIEN)" if ck.is_half() else ""
                if not dry:
                    await notify(
                        f"🟢 <b>Scanner san sang</b>\n"
                        f"Phien {lo:%d/%m} {lo:%H:%M}–{lc:%H:%M} gio Duc{half}\n"
                        f"Baseline: {len(st.base)} co phieu"
                        f"{warn}", loud=False)
                log(f"heartbeat: phien {lo:%H:%M}-{lc:%H:%M} DE")

            if state == "AFTERHOURS" and "sum" not in st.done and st.scans > 0:
                st.done.add("sum")
                # Gia dong cua TAM tu lan quet cuoi; backfill() luc sang ngay
                # moi se ghi de bang gia dong cua that.
                if not dry and (fz := outcome.freeze(DB, st.universe)):
                    log(f"outcome: chot tam gia dong cua cho {fz} alert")
                top = sorted(st.seen.items(), key=lambda kv: -kv[1]["best"])[:8]
                trk = set(store.tracked_syms(DB))
                body = "\n".join(
                    f"${esc(s)}  score {v['best']:.1f}"
                    + ("  (theo doi)" if s in trk else "")
                    for s, v in top) or "—"
                if (extra := sorted(trk - {s for s, _ in top})):
                    body += ("\n\nDang theo doi: "
                             + " ".join("$" + esc(s) for s in extra))
                if not dry:
                    await notify(
                        f"📋 <b>Ket phien {st.day:%d/%m}</b>\n"
                        f"{st.n_alerts} alert · {st.scans} lan quet · "
                        f"{st.errors} loi\n\n{body}", loud=False)
                log(f"summary: {st.n_alerts} alert, {st.errors} loi")

            # Tra no spool moi 20s. Can rieng cho o day vi neu mat mang xay ra
            # ngoai gio quet (hoac khong con ma nao qua loc) thi tg_send khong
            # duoc goi lan nao nua, va tin trong spool se nam mai.
            if not dry and len(_SPOOL):
                await _SPOOL.flush()
        except Exception:  # noqa: BLE001
            st.errors += 1
            log("[clock] " + traceback.format_exc(limit=2))
        await asyncio.sleep(20)


async def run(dry: bool, once: bool) -> None:
    global _ST, _CK

    ck = SessionClock()
    st = State()
    _ST, _CK = st, ck
    tgapi.log = notifier.log = log
    halts.log = news.log = outcome.log = log   # log co dau thoi gian nhu cac dong khac
    db().close()                     # tao bang alerts ngay tu dau

    # Chet ngay luc khoi dong neu thieu .env, thay vi chay ca ngay roi moi phat
    # hien khong tin nao den duoc.
    if not dry and not tgapi.ready():
        raise SystemExit("Thieu TG_TOKEN / TG_CHAT_ID trong .env")

    st.base = await asyncio.to_thread(scorer.load_baseline)
    log(ck.describe().replace("\n", " | "))
    log(f"baseline {len(st.base)} ma | dry={dry}")
    if len(_SPOOL):
        log(f"spool: {len(_SPOOL)} tin con no tu lan chay truoc")

    tasks: list[asyncio.Task] = []

    if once:
        st.universe = await asyncio.to_thread(universe_live.build)
        log(f"universe = {len(st.universe)} ma")
        # --once khong co vong lap nao chay: phai tu lay halt va tin, khong thi
        # tin nhan thu se thieu dung hai khoi minh muon kiem tra.
        await asyncio.to_thread(_HB.refresh)
        if await asyncio.to_thread(_NB.refresh) < 0:
            log(f"news: khong lay duoc tin ({_NB.err})")
        hits, rej = await asyncio.to_thread(scorer.rank, st.universe, st.base, ck)
        log(f"qua_loc={rej.get('_qua_loc')} frac={rej.get('_frac')}")
        for h in hits[:5]:
            log(f"  {h['sym']:<6} {h['score']:>5.1f}  rvol {h['rvol']:.1f}x")
        if hits and not dry:
            h = hits[0]
            await tg_send(build_view(h, "NEW", ck), True)
            now = dt.datetime.now(dt.timezone.utc)
            con = db()
            con.execute(
                "INSERT INTO alerts(ts_utc,ts_et,kind,sym,score,px,chg,rvol,"
                "atr_move,float_rot,dollar_vol,freshness,sources)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now.isoformat(timespec="seconds"),
                 now.astimezone(ZoneInfo("America/New_York")).isoformat(
                     timespec="seconds"),
                 "ONCE", h["sym"], h["score"], h["px"], h["chg"], h["rvol"],
                 h["atr_move"], h["float_rot"], h["dollar_vol"],
                 h["freshness"], ",".join(h["sources"])))
            con.commit()
            con.close()
            log(f"da gui + luu DB: {h['sym']} score={h['score']:.1f}"
                + (f" | {len(_SPOOL)} tin trong spool" if len(_SPOOL) else ""))
        for t in tasks:
            t.cancel()
        return

    tasks += [
        asyncio.create_task(loop_clock(st, ck, dry)),
        asyncio.create_task(loop_universe(st, ck)),
        asyncio.create_task(loop_score(st, ck, dry)),
        # Chay ca trong --dry: dong halt / khoi CATALYST phai xem duoc khi thu
        # tin nhan. Ca hai chi doc, khong gui gi ra ngoai.
        asyncio.create_task(loop_halts(st, ck)),
        asyncio.create_task(loop_news(st, ck)),
    ]
    if not dry:
        tasks += [
            asyncio.create_task(loop_track(st, ck, dry)),
            asyncio.create_task(loop_outcome(st, ck, dry)),
            asyncio.create_task(Callbacks(
                DB, refresh_one,
                lambda h, kind, detail: build_view(h, kind, None, detail),
                log=log).run()),
        ]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    try:
        asyncio.run(run(a.dry, a.once))
    except KeyboardInterrupt:
        log("dung boi nguoi dung")
