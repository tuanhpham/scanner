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
import notifier as notif_mod
import render
import scorer
import store
import tgapi
import universe_live
from callbacks import Callbacks
from clock import DE, SessionClock
from notifier import esc

ROOT = Path(__file__).resolve().parent
DB = ROOT / "state" / "baseline.db"

UNIVERSE_SEC = 60      # Yahoo gioi han ~1 req/60s
SCORE_SEC = 25
ALERT_SCORE = 7.0
ESCALATE_DELTA = 3.0   # diem tang thap nay -> gui lai
COOLDOWN = 540         # 9 phut moi ma
MAX_ALERTS = 45        # tran moi phien, chong spam
SEC_RISK_MAX = 3.0     # risk >= nguong nay -> tru diem
SEC_PENALTY = 2.0
MIN_MSO = 5            # bo qua alert trong 5 phut dau phien
BAD_SUFFIX = ("W", "WS", "R", "RT", "U", "UN", "PR")

# Trang thai dung chung cho nut Refresh (callbacks chay ngoai vong scan).
_ST: "State | None" = None
_CK: SessionClock | None = None
_SENT: dict[str, float] = {}     # sym -> lan gui gan nhat (cooldown)

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
               detail: bool = False) -> render.AlertView:
    """Dict tu scorer -> AlertView day du (SEC, delta, watchlist)."""
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
        tracked, watched = store.watch_state(DB, h["sym"])
        prev = store.get_snap(DB, h["sym"])
    except Exception as e:  # noqa: BLE001
        log(f"store doc loi {h['sym']}: {e}")
        tracked, watched, prev = False, False, None
    return render.AlertView.from_scan(
        h, sec=sec, prev=prev, kind=kind, session=session_state(ck),
        updated=upd, mso=mso, session_min=smin, detail=detail,
        tracked=tracked, watched=watched,
        news_url=f"https://www.google.com/search?q={h['sym']}+stock&tbm=nws")


def fmt(h: dict, kind: str, ck: SessionClock) -> str:
    """Giu lai cho tuong thich - chi tra ve text, khong nut."""
    return render.render_alert(build_view(h, kind, ck))


async def tg_send(n, v: render.AlertView, loud: bool) -> bool:
    """Gui kem nut qua tgapi. That bai -> ve notifier cu (co spool, khong nut)."""
    now = time.time()
    if now - _SENT.get(v.sym, 0) < COOLDOWN:
        log(f"{v.sym}: trong cooldown, bo qua")
        return False
    txt = render.render_alert(v)
    mid = await tgapi.send(txt, markup=render.render_keyboard(v), loud=loud)
    if mid:
        _SENT[v.sym] = now
        try:
            store.put_msg(DB, v.sym, mid, v.snapshot())
        except Exception as e:  # noqa: BLE001
            log(f"store ghi loi {v.sym}: {e}")
        return True
    if n is None:
        return False
    log(f"{v.sym}: tgapi that bai -> gui qua notifier, khong co nut")
    ok = await n.send(txt, key=v.sym, loud=loud, cooldown=COOLDOWN)
    if ok:
        _SENT[v.sym] = now
    return ok


async def refresh_one(sym: str) -> dict | None:
    """Cham diem lai mot ma khi nguoi dung bam Refresh."""
    if _ST is None or _CK is None:
        return None
    row = (_ST.universe or {}).get(sym)
    if row is None:
        return None                      # ngoai phien / khong con trong universe
    hits, _ = await asyncio.to_thread(scorer.rank, {sym: row}, _ST.base, _CK)
    return hits[0] if hits else None


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


async def loop_score(st: State, n, ck: SessionClock, dry: bool) -> None:
    con = db()
    while True:
        if ck.scanning() and st.universe:
            try:
                hits, rej = await asyncio.to_thread(
                    scorer.rank, st.universe, st.base, ck)
                st.scans += 1
                now = dt.datetime.now(dt.timezone.utc)
                et = ck.now_et(now)

                for h in hits:
                    if h["score"] < ALERT_SCORE:
                        continue
                    sym = h["sym"]
                    if junk_ticker(sym):
                        log(f"bo qua {sym}: warrant/unit/right")
                        continue
                    _m = ck.mso()
                    if _m is not None and _m < MIN_MSO:
                        continue
                    prev = st.seen.get(sym)
                    if prev is None:
                        kind = "NEW"
                    elif h["score"] >= prev["best"] + ESCALATE_DELTA:
                        kind = "UP"
                    else:
                        st.seen[sym]["best"] = max(prev["best"], h["score"])
                        continue
                    try:
                        _a = edgar.assess(sym)
                        h["sec_risk"] = _a["risk"]
                        if _a["risk"] >= SEC_RISK_MAX:
                            h["score"] -= SEC_PENALTY
                            log(f"{sym}: SEC risk {_a['risk']} -> tru "
                                f"{SEC_PENALTY} diem, con {h['score']:.1f}")
                            if h["score"] < ALERT_SCORE:
                                st.seen[sym] = {
                                    "best": h["score"],
                                    "alerts": prev["alerts"] if prev else 0}
                                continue
                    except Exception:  # noqa: BLE001
                        h["sec_risk"] = None
                    if st.n_alerts >= MAX_ALERTS:
                        continue

                    st.seen[sym] = {"best": h["score"],
                                    "alerts": (prev["alerts"] + 1) if prev else 1}
                    v = build_view(h, kind, ck)
                    if dry:
                        log(f"[DRY {kind}] {sym} {h['score']:.1f} L{v.level}")
                    else:
                        if not await tg_send(n, v, loud_mode(h["score"])):
                            continue
                    st.n_alerts += 1
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


async def loop_clock(st: State, n, ck: SessionClock, dry: bool) -> None:
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
                st.base = await asyncio.to_thread(scorer.load_baseline)
                log(f"=== ngay moi {st.day} | baseline {len(st.base)} ma ===")
                k = restore_today(st)
                if k:
                    log(f"khoi phuc {k} ma da alert hom nay ({st.n_alerts} alert)")

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
                    await n.send(
                        f"🟢 <b>Scanner san sang</b>\n"
                        f"Phien {lo:%d/%m} {lo:%H:%M}–{lc:%H:%M} gio Duc{half}\n"
                        f"Baseline: {len(st.base)} co phieu"
                        f"{warn}", loud=False)
                log(f"heartbeat: phien {lo:%H:%M}-{lc:%H:%M} DE")

            if state == "AFTERHOURS" and "sum" not in st.done and st.scans > 0:
                st.done.add("sum")
                top = sorted(st.seen.items(), key=lambda kv: -kv[1]["best"])[:8]
                body = "\n".join(
                    f"${esc(s)}  score {v['best']:.1f}" for s, v in top) or "—"
                if not dry:
                    await n.send(
                        f"📋 <b>Ket phien {st.day:%d/%m}</b>\n"
                        f"{st.n_alerts} alert · {st.scans} lan quet · "
                        f"{st.errors} loi\n\n{body}", loud=False)
                log(f"summary: {st.n_alerts} alert, {st.errors} loi")
        except Exception:  # noqa: BLE001
            st.errors += 1
            log("[clock] " + traceback.format_exc(limit=2))
        await asyncio.sleep(20)


async def run(dry: bool, once: bool) -> None:
    global _ST, _CK

    ck = SessionClock()
    st = State()
    _ST, _CK = st, ck
    tgapi.log = log
    db().close()                     # tao bang alerts ngay tu dau

    st.base = await asyncio.to_thread(scorer.load_baseline)
    log(ck.describe().replace("\n", " | "))
    log(f"baseline {len(st.base)} ma | dry={dry}")

    n = None if dry else notif_mod.from_env()
    tasks = [] if dry else [asyncio.create_task(n.worker())]

    if once:
        st.universe = await asyncio.to_thread(universe_live.build)
        log(f"universe = {len(st.universe)} ma")
        hits, rej = await asyncio.to_thread(scorer.rank, st.universe, st.base, ck)
        log(f"qua_loc={rej.get('_qua_loc')} frac={rej.get('_frac')}")
        for h in hits[:5]:
            log(f"  {h['sym']:<6} {h['score']:>5.1f}  rvol {h['rvol']:.1f}x")
        if hits and not dry:
            h = hits[0]
            await tg_send(n, build_view(h, "NEW", ck), True)
            await n.q.join()
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
            log(f"da gui + luu DB: {h['sym']} score={h['score']:.1f} "
                f"| tg sent={n.sent} failed={n.failed}")
        for t in tasks:
            t.cancel()
        return

    tasks += [
        asyncio.create_task(loop_clock(st, n, ck, dry)),
        asyncio.create_task(loop_universe(st, ck)),
        asyncio.create_task(loop_score(st, n, ck, dry)),
    ]
    if not dry:
        tasks.append(asyncio.create_task(Callbacks(
            DB, refresh_one,
            lambda h, kind, detail: build_view(h, kind, None, detail),
            log=log).run()))
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
