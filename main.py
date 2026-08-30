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
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

import notifier as notif_mod
import scorer
import universe_live
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


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.executescript(DDL)
    return con


def loud_mode(score: float) -> bool:
    """Gio Duc: 09-17h im lang (dang lam viec), sau 17h moi reo."""
    h = dt.datetime.now(DE).hour
    if 9 <= h < 17:
        return score >= 12.0
    return True


def fmt(h: dict, kind: str, ck: SessionClock) -> str:
    icon = {"NEW": "🔴", "UP": "⬆️"}.get(kind, "🔵")
    chg = (h["chg"] or 0) * 100
    flt = f"{h['float_sh'] / 1e6:.1f}M" if h.get("float_sh") else "?"
    mso = ck.mso()
    fresh = ("🟢 realtime" if h["freshness"] == "REALTIME"
             else "🟡 tre ~15 phut")
    lines = [
        f"{icon} <b>${esc(h['sym'])}</b>  {chg:+.1f}%  ${h['px']:.2f}"
        f"   <code>score {h['score']:.1f}</code>",
        f"RVOL <b>{h['rvol']:.1f}x</b> · ATR {h['atr_move']:.1f} · "
        f"float {flt} (quay {h['float_rot']:.2f}x)",
        f"KL ${h['dollar_vol'] / 1e6:.0f}M · phut {mso} cua phien · {fresh}",
        f"<i>{esc(h['explain'])}</i>",
        "",
        f"<a href=\"https://finviz.com/quote.ashx?t={h['sym']}\">Finviz</a> · "
        f"<a href=\"https://stockanalysis.com/stocks/{h['sym']}/\">Analysis</a>"
        + (f" · <a href=\"https://www.sec.gov/cgi-bin/browse-edgar?"
           f"action=getcompany&CIK={h['cik']}&type=8-K&dateb=&owner=include"
           f"&count=10\">EDGAR</a>" if h.get("cik") else ""),
        "",
        "⚠️ <i>Chua co lop catalyst. Tu kiem chung truoc khi lam gi.</i>",
    ]
    return "\n".join(lines)


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
                    prev = st.seen.get(sym)
                    if prev is None:
                        kind = "NEW"
                    elif h["score"] >= prev["best"] + ESCALATE_DELTA:
                        kind = "UP"
                    else:
                        st.seen[sym]["best"] = max(prev["best"], h["score"])
                        continue
                    if st.n_alerts >= MAX_ALERTS:
                        continue

                    st.seen[sym] = {"best": h["score"],
                                    "alerts": (prev["alerts"] + 1) if prev else 1}
                    txt = fmt(h, kind, ck)
                    if dry:
                        log(f"[DRY {kind}] {sym} {h['score']:.1f}")
                    else:
                        ok = await n.send(txt, key=sym,
                                          loud=loud_mode(h["score"]),
                                          cooldown=COOLDOWN)
                        if not ok:
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
                        f"rvol={h['rvol']:.1f}x")

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
                st.base = await asyncio.to_thread(scorer.load_baseline)
                log(f"=== ngay moi {st.day} | baseline {len(st.base)} ma ===")

            state = ck.state()

            if state == "PREMARKET" and "hb" not in st.done:
                st.done.add("hb")
                lo, lc = ck.local_open(), ck.local_close()
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

            if state == "AFTERHOURS" and "sum" not in st.done:
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
    ck = SessionClock()
    st = State()
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
            await n.send(fmt(hits[0], "NEW", ck), loud=True)
            await n.q.join()
        for t in tasks:
            t.cancel()
        return

    tasks += [
        asyncio.create_task(loop_clock(st, n, ck, dry)),
        asyncio.create_task(loop_universe(st, ck)),
        asyncio.create_task(loop_score(st, n, ck, dry)),
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

