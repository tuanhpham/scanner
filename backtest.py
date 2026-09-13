"""backtest.py - Chay lai hai setup BO/RV tren nen ngay qua khu. Phase 9.7.

Ly do ton tai: dinh nghia nen trong structure.py va nguong trong setups.py hien
gio chi la GIA THUYET. Chung da qua test ve mat "do dung cai minh dinh do",
nhung chua co gi chung minh nen phang 30 phien voi volume can bao truoc mot cu
tang. Chinh nguong bang cam giac roi chay live 3 thang de biet ket qua la dung
cai sai ma Phase 9 dang sua.

Ba nguyen tac, khong duoc pha:

1. GOI DUNG HAM CUA BOT. bo_candidate/rv_candidate/trig_* la cung mot doan code
   bot chay that. Neu backtest viet lai logic thi ket qua khong noi len dieu gi
   ve bot.
2. KHONG NHIN TRUOC TUONG LAI. Candidate sinh tu nen den ngay T, kich hoat doc
   nen ngay T+1. structure.metrics() da co test cho viec nay.
3. LUON SO VOI MOC NGAU NHIEN. "55% thang sau 10 phien" khong co nghia gi neu
   vao mu bat ky ma nao cung thang 54%. Bao cao luon in dong `ngau nhien`.

Han che phai doc cung ket qua:

- Vao lenh o GIA DONG CUA ngay kich hoat, con bot alert GIUA phien. Ket qua that
  se khac - thuong la xau hon, vi alert giua phien vao gia cao hon gia dong cua
  o nhung ngay dao chieu.
- yfinance chi con ma DANG SONG (survivorship bias): ma huy niem yet khong co
  trong kho nen, nen ket qua se dep hon thuc te. Voi RV thi thien lech nay
  manh nhat - dung so sanh win% cua RV va BO nhu hai con so cung thang do.
- rs_pct xep hang trong pham vi cac ma DUOC BACKTEST, khong phai ca thi truong.

    python backtest.py                    # selftest, khong can DB
    python backtest.py --run              # chay tren kho nen
    python backtest.py --run --limit 300  # nhanh hon, it ma hon
    python backtest.py --run --by base_len
    python backtest.py --run --setup BO --rvol 2.5
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

import bars
import setups
import structure

ROOT = Path(__file__).resolve().parent
DB = ROOT / "state" / "baseline.db"

HORIZONS = (1, 5, 10, 20)
COOLDOWN = 10       # cung mot ma, cung setup: khong dem lai trong 10 phien
MIN_BARS = 300      # duoi nay thi khong con bao nhieu ngay de thu
LOAD_N = 900

log = print


def _pct(x) -> str:
    return "-" if x is None else f"{x * 100:+.1f}%"


# ───────────────────── quote gia lap tu nen ngay ─────────────────────
def quote_of(b) -> dict:
    """Nen ngay -> dict quote nhu setups.trig_* mong doi.

    `rvol` do goi tu ngoai vao: no can adv20 cua ngay HOM TRUOC, ma ham nay
    khong biet. Xem _rvol() trong walk().
    """
    return {"px": b.c, "vol": b.v, "hi": b.h, "lo": b.l, "open": b.o}


# ───────────────────── pass 1: rs_pct theo tung ngay ─────────────────────
def rs_by_day(series: dict[str, list], n: int = 63) -> dict[str, dict[str, float]]:
    """{ngay: {ma: percentile}} tu ret63. Re, nen tinh truoc cho ca ro.

    metrics() de rs_pct=None vi mot ma khong tu biet minh xep thu may. Trong
    bot, structure.build() dien cho ca ro; o day phai lam dieu tuong duong -
    khong thi moi nguong lien quan rs_pct khong duoc backtest.
    """
    by_day: dict[str, dict[str, float]] = {}
    for sym, bs in series.items():
        for i in range(n, len(bs)):
            c0 = bs[i - n].c
            if c0:
                by_day.setdefault(bs[i].d, {})[sym] = bs[i].c / c0 - 1.0
    return {d: structure.rank_pct(v) for d, v in by_day.items()}


# ───────────────────── pass 2: di tung ngay ─────────────────────
def _adv(vols: list[float], i: int, n: int = 20) -> float:
    """adv20 tinh den nen i-1 (KHONG gom nen i) - giong structure.metrics()."""
    a = max(0, i - n)
    return sum(vols[a:i]) / (i - a) if i > a else 0.0


def _could_fire(px: float, vol: float, adv: float, g_rvol: float,
                dvol: float) -> bool:
    """Sang loc re truoc khi goi metrics().

    metrics() la phan dat nhat (O(so nen) moi ngay). Ca hai setup deu doi
    rvol >= 1.8 va dollar-vol >= $2M, hai dieu kien tinh duoc trong O(1) tu
    mang tho. Bo qua som nhung ngay khong the kich hoat cat ~90% cong viec ma
    KHONG doi ket qua - day la dieu kien can, khong phai dieu kien du.
    """
    if not adv or vol / adv < g_rvol:
        return False
    return px * vol >= dvol


def walk(series: dict[str, list], bo: dict | None = None, rv: dict | None = None,
         horizons=HORIZONS, cooldown: int = COOLDOWN,
         start: str | None = None, end: str | None = None,
         fund: dict[str, dict] | None = None) -> list[dict]:
    """Di tung ma, tung ngay -> danh sach lenh gia dinh.

    `series` = {ma: [Bar, ...]} tang dan. Tra ve moi lenh mot dict.

    `fund` la so lieu co ban theo ma. De None thi RV KHONG ra lenh nao, dung
    nhu bot: `fund_ok=None` khong bao gio duoc coi la dat. Muon do rieng phan
    CAU TRUC cua RV thi truyen rv={**setups.RV, "require_fund": False} - va
    nho rang con so thu duoc lac quan hon thuc te.
    """
    bo = bo if bo is not None else setups.BO
    rv = rv if rv is not None else setups.RV
    rs = rs_by_day(series)
    hmax = max(horizons)
    gate_rvol = min(bo["rvol"], rv["rvol"])
    gate_dvol = min(bo["dvol"], rv["dvol"])

    out: list[dict] = []
    for sym, bs in series.items():
        n = len(bs)
        vols = [b.v for b in bs]
        last: dict[str, int] = {}
        # i = ngay sinh candidate (buoi toi), j = i+1 = ngay kich hoat
        for i in range(structure.MIN_HIST, n - 1):
            j = i + 1
            if start and bs[j].d < start:
                continue
            if end and bs[j].d > end:
                break
            adv = _adv(vols, j)          # adv20 den het ngay j-1 = ngay i
            if not _could_fire(bs[j].c, bs[j].v, adv, gate_rvol, gate_dvol):
                continue

            m = structure.metrics(bs[:i + 1])
            if not m:
                continue
            m["sym"] = sym
            m["rs_pct"] = rs.get(bs[i].d, {}).get(sym)

            cands = []
            c = setups.bo_candidate(m, bo)
            if c:
                cands.append(c)
            c = setups.rv_candidate(m, rv, (fund or {}).get(sym))
            if c:
                cands.append(c)
            if not cands:
                continue

            q = quote_of(bs[j])
            q["rvol"] = bs[j].v / adv if adv else 0.0
            for c in cands:
                g = bo if c["setup"] == "BO" else rv
                t = setups.TRIG[c["setup"]](c, q, g)
                if not t:
                    continue
                if j - last.get(c["setup"], -10**6) < cooldown:
                    continue     # con dang trong cung mot cu breakout
                last[c["setup"]] = j
                out.append(_trade(sym, bs, i, j, c, t, horizons, hmax))
    out.sort(key=lambda t: t["d_entry"])
    return out


def _trade(sym: str, bs: list, i: int, j: int, c: dict, t: dict,
           horizons, hmax: int) -> dict:
    """Mot lenh: vao o gia dong cua ngay kich hoat, do ket qua sau h phien."""
    entry = bs[j].c
    fw = bs[j + 1: j + 1 + hmax]
    r = {f"r{h}": (bs[j + h].c / entry - 1.0 if j + h < len(bs) else None)
         for h in horizons}
    return {"sym": sym, "setup": c["setup"], "d_setup": bs[i].d,
            "d_entry": bs[j].d, "entry": entry, "rvol": t.get("rvol"),
            "chg": t.get("chg"), "quality": c.get("quality"),
            "base_len": c.get("base_len"), "base_depth": c.get("base_depth"),
            "off_high": c.get("off_high"), "rs_pct": c.get("rs_pct"),
            "over_pivot": t.get("over_pivot"), "gap": t.get("gap"),
            "warn": bool(t.get("warn")),
            "mfe": (max(b.h for b in fw) / entry - 1.0) if fw else None,
            "mae": (min(b.l for b in fw) / entry - 1.0) if fw else None,
            **r}


# ───────────────────── moc so sanh: vao mu ─────────────────────
def random_baseline(series: dict[str, list], horizons=HORIZONS,
                    start: str | None = None,
                    end: str | None = None) -> dict[str, float | None]:
    """Loi nhuan cua viec vao MU: moi (ma, ngay) deu vao.

    Khong co dong nay thi khong doc duoc bang ket qua. Trong mot nam thi truong
    tang, "55% thang sau 10 phien" co the te hon ca viec mua bat ky thu gi.
    """
    acc: dict[str, list[float]] = {f"r{h}": [] for h in horizons}
    for bs in series.values():
        n = len(bs)
        for j in range(structure.MIN_HIST, n - 1):
            if start and bs[j].d < start:
                continue
            if end and bs[j].d > end:
                break
            for h in horizons:
                if j + h < n and bs[j].c:
                    acc[f"r{h}"].append(bs[j + h].c / bs[j].c - 1.0)
    return {k: (statistics.median(v) if v else None) for k, v in acc.items()}


# ───────────────────── tong hop ─────────────────────
def summarize(trades: list[dict], horizons=HORIZONS) -> dict:
    """Mot dong so lieu cho mot nhom lenh."""
    row: dict = {"n": len(trades)}
    for h in horizons:
        xs = [t[f"r{h}"] for t in trades if t[f"r{h}"] is not None]
        row[f"n{h}"] = len(xs)
        row[f"win{h}"] = (sum(1 for x in xs if x > 0) / len(xs)) if xs else None
        row[f"med{h}"] = statistics.median(xs) if xs else None
        row[f"avg{h}"] = (sum(xs) / len(xs)) if xs else None
    mae = [t["mae"] for t in trades if t["mae"] is not None]
    mfe = [t["mfe"] for t in trades if t["mfe"] is not None]
    row["mae"] = statistics.median(mae) if mae else None
    row["mfe"] = statistics.median(mfe) if mfe else None
    return row


BUCKETS = {
    "base_len": ((20, 35), (35, 50), (50, 10**6)),
    "base_depth": ((0, 0.08), (0.08, 0.14), (0.14, 0.20), (0.20, 1.0)),
    "rs_pct": ((0, 50), (50, 75), (75, 90), (90, 101)),
    "off_high": ((0, 0.05), (0.05, 0.15), (0.15, 0.30), (0.30, 1.0)),
    "rvol": ((1.8, 2.5), (2.5, 4.0), (4.0, 10.0), (10.0, 10**6)),
    "quality": ((0, 0.4), (0.4, 0.55), (0.55, 0.7), (0.7, 1.01)),
}


def by_bucket(trades: list[dict], key: str, horizons=HORIZONS) -> list[tuple]:
    """Cat theo mot bien -> thay nguong nao thuc su co tra tien."""
    rngs = BUCKETS.get(key)
    if not rngs:
        vals = sorted({t[key] for t in trades if t[key] is not None})
        return [(str(v), summarize([t for t in trades if t[key] == v], horizons))
                for v in vals]
    out = []
    for lo, hi in rngs:
        grp = [t for t in trades
               if t.get(key) is not None and lo <= t[key] < hi]
        if grp:
            name = f"{lo:g}-{hi:g}" if hi < 10**6 else f"{lo:g}+"
            out.append((name, summarize(grp, horizons)))
    return out


# ───────────────────── in bao cao ─────────────────────
def print_table(rows: list[tuple], horizons=HORIZONS, title: str = "") -> None:
    if title:
        log(f"\n{title}")
    head = f"{'nhom':<14}{'n':>5}"
    for h in horizons:
        head += f"{'win' + str(h):>7}{'med' + str(h):>8}"
    head += f"{'MFE':>8}{'MAE':>8}"
    log(head)
    log("-" * len(head))
    for name, r in rows:
        line = f"{name:<14}{r['n']:>5}"
        for h in horizons:
            w = r[f"win{h}"]
            line += f"{('-' if w is None else f'{w:.0%}'):>7}"
            line += f"{_pct(r[f'med{h}']):>8}"
        line += f"{_pct(r['mfe']):>8}{_pct(r['mae']):>8}"
        log(line)


def report(trades: list[dict], series: dict[str, list], horizons=HORIZONS,
           by: str | None = None, start=None, end=None) -> None:
    log(f"\nSo lenh: {len(trades)}  ·  so ma trong kho: {len(series)}")
    if trades:
        log(f"Khoang thoi gian: {trades[0]['d_entry']} -> {trades[-1]['d_entry']}")

    rows = [(k, summarize([t for t in trades if t["setup"] == k], horizons))
            for k in ("BO", "RV")]
    rows = [(k, r) for k, r in rows if r["n"]]
    base = random_baseline(series, horizons, start, end)
    rows.append(("ngau nhien", {"n": 0, "mfe": None, "mae": None,
                                **{f"win{h}": None for h in horizons},
                                **{f"med{h}": base[f"r{h}"] for h in horizons}}))
    print_table(rows, horizons, "Theo setup (dong cuoi = vao mu, de so sanh)")

    if by:
        for k in ("BO", "RV"):
            grp = [t for t in trades if t["setup"] == k]
            if grp:
                print_table(by_bucket(grp, by, horizons), horizons,
                            f"{k} cat theo {by}")

    log("\nDoc ket qua:")
    log("  · med10 khong hon dong `ngau nhien` -> setup khong co loi the gi.")
    log("  · MAE la muc lo phai chiu truoc khi lai. MAE -12% thi backtest lai")
    log("    12% cung khong giao dich duoc.")
    log("  · Vao lenh o gia dong cua, con bot alert giua phien -> that se xau hon.")
    log("  · Kho nen chi co ma DANG SONG: ma huy niem yet khong co o day, nen")
    log("    con so nay dep hon thuc te (RV bi thien lech nang nhat).")


# ───────────────────── chay tren kho nen ─────────────────────
def load_series(db=DB, limit: int = 0, min_bars: int = MIN_BARS,
                load_n: int = LOAD_N) -> dict[str, list]:
    c, mine = bars._c(db)
    try:
        sl = bars.syms(c, min_rows=min_bars)
        if limit:
            sl = sl[:limit]
        return {s: bars.load(c, s, limit=load_n) for s in sl}
    finally:
        if mine:
            c.close()


def run(db=DB, limit: int = 0, bo: dict | None = None, rv: dict | None = None,
        horizons=HORIZONS, by: str | None = None, start=None,
        end=None, fund: dict[str, dict] | None = None) -> list[dict]:
    t0 = time.time()
    series = load_series(db, limit)
    if not series:
        log("Kho nen rong. Chay: python bars.py --sync --full")
        return []
    log(f"Nap {len(series)} ma, {sum(len(v) for v in series.values())} nen "
        f"({time.time() - t0:.0f}s)")
    trades = walk(series, bo, rv, horizons, start=start, end=end, fund=fund)
    log(f"Quet xong ({time.time() - t0:.0f}s)")
    report(trades, series, horizons, by, start, end)
    return trades


# ───────────────────── selftest ─────────────────────
def _bar(d: str, px: float, vol: float, spread: float = 0.005):
    return bars.Bar(d, px, px * (1 + spread), px * (1 - spread), px, vol)


def _mk(specs, start="2022-01-03", vol=1e6, spread=0.005) -> list:
    """Chuoi nen tong hop. Spec = (gia dau, gia cuoi, so phien[, bien do ngay]).

    Bien do ngay khai bao rieng tung doan vi nen tich luy THAT co bien do hep
    hon doan tang truoc do - do chinh la thu atr_contract do. Dung mot bien do
    cho ca chuoi thi chuoi giu khong con giong nen nao ngoai doi.
    """
    import datetime as dt
    out, d = [], dt.date.fromisoformat(start)
    for spec in specs:
        p0, p1, n = spec[:3]
        sp = spec[3] if len(spec) > 3 else spread
        for i in range(n):
            p = p0 + (p1 - p0) * (i / max(n - 1, 1))
            out.append(_bar(d.isoformat(), p, vol, sp))
            d += dt.timedelta(days=1)
    return out


def _smoke() -> None:
    # Chuoi: tang 60 phien (bien do 2%) -> nen phang 40 phien (bien do 0.8%)
    # -> mot phien breakout volume 4x dong sat dinh -> tang tiep 30 phien.
    bs = _mk([(10.0, 20.0, 60, 0.02), (20.0, 20.0, 40, 0.008)])
    d0 = bs[-1].d
    import datetime as dt
    d = dt.date.fromisoformat(d0)

    def nxt():
        nonlocal d
        d += dt.timedelta(days=1)
        return d.isoformat()

    # Nen breakout that: mo gan gia cu, dong sat dinh ngay.
    bs.append(bars.Bar(nxt(), 20.2, 21.6, 20.1, 21.5, 4e6))
    for k in range(30):                                 # +25% sau do
        bs.append(_bar(nxt(), 21.5 * (1 + 0.008 * (k + 1)), 1.5e6))

    tr = walk({"AAA": bs})
    assert len(tr) == 1, f"phai bat dung mot lan, duoc {len(tr)}"
    t = tr[0]
    assert t["setup"] == "BO" and t["sym"] == "AAA"
    assert t["d_setup"] == d0 and t["d_entry"] > d0, "candidate phai co TRUOC"
    assert abs(t["entry"] - 21.5) < 1e-9, "vao o gia dong cua ngay kich hoat"
    assert t["r10"] and t["r10"] > 0.05, t["r10"]
    assert t["rvol"] > 3.0 and t["base_len"] >= 20
    assert t["mfe"] > t["r1"] and t["mae"] is not None

    # Sau khi vuot pivot roi thi KHONG vao lai: dist_pivot da duoi min_dist.
    # Day la tinh chat cua setups.py, khong phai cua cooldown - va no la ly do
    # cooldown gan nhu khong bao gio can dung cho BO.
    assert len([t for t in tr if t["setup"] == "BO"]) == 1

    # khong nhin truoc tuong lai: cat chuoi ngay sau khi vao lenh thi cac cot
    # r5/r10 phai la None, nhung LENH VAN PHAI DUOC SINH RA
    j = next(k for k, b in enumerate(bs) if b.d == t["d_entry"])
    tr3 = walk({"AAA": bs[:j + 1]})
    assert len(tr3) == 1 and tr3[0]["r5"] is None
    assert tr3[0]["d_entry"] == t["d_entry"]
    assert tr3[0]["entry"] == t["entry"], "gia vao khong duoc phu thuoc tuong lai"

    # doi nguong -> ket qua doi theo, khong can sua backtest
    assert walk({"AAA": bs}, bo={**setups.BO, "rvol": 9.0}) == []
    assert walk({"AAA": bs}, bo={**setups.BO, "min_base_len": 80}) == []

    # nen KHONG breakout thi khong co lenh nao
    flat = _mk([(10.0, 20.0, 60), (20.0, 20.0, 60)])
    assert walk({"BBB": flat}) == []

    # volume khong tang thi khong tinh la breakout
    quiet = _mk([(10.0, 20.0, 60), (20.0, 20.0, 40)])
    quiet.append(_bar("2022-06-01", 21.5, 1.0e6))
    quiet += _mk([(21.5, 26.0, 30)], start="2022-06-02")
    assert walk({"CCC": quiet}) == [], "rvol 1.0 khong duoc kich hoat"

    # --- RV: khong co so lieu co ban thi khong ra lenh nao ---
    # roi 100 -> 25 trong 150 phien, nam im 15 phien, roi 3 phien bat +9%
    dip = _mk([(100.0, 25.0, 150, 0.02), (25.0, 25.0, 15, 0.01)])
    dd = dt.date.fromisoformat(dip[-1].d)
    px = 25.0
    for _ in range(3):
        dd += dt.timedelta(days=1)
        px *= 1.09
        dip.append(bars.Bar(dd.isoformat(), px / 1.08, px * 1.002, px / 1.085,
                            px, 6e6))
    dip += _mk([(px, px * 1.1, 25, 0.02)],
               start=(dd + dt.timedelta(days=1)).isoformat())

    assert walk({"DIP": dip}) == [], "thieu so lieu co ban -> khong lenh nao"
    fund = {"DIP": {"ok": True, "score": 0.8}}
    tr_rv = walk({"DIP": dip}, fund=fund)
    assert tr_rv and all(t["setup"] == "RV" for t in tr_rv), tr_rv
    assert len(tr_rv) == 1, f"cooldown 10 phien: chi mot lenh, duoc {len(tr_rv)}"
    # cooldown=1 thi ca ba phien bat lien tiep deu thanh lenh rieng
    assert len(walk({"DIP": dip}, fund=fund, cooldown=1)) == 3

    # do rieng phan cau truc cua RV, bo qua so lieu co ban
    rv_open = {**setups.RV, "require_fund": False}
    assert walk({"DIP": dip}, rv=rv_open)

    # --- rs_pct: xep hang trong ca ro, theo tung ngay ---
    up = _mk([(10.0, 30.0, 130)])
    dn = _mk([(30.0, 10.0, 130)])
    rs = rs_by_day({"UP": up, "DN": dn})
    day = up[-1].d
    assert rs[day]["UP"] == 100.0 and rs[day]["DN"] == 0.0

    # --- tong hop ---
    fake = [{"setup": "BO", "r1": 0.01, "r5": 0.10, "r10": 0.20, "r20": None,
             "mae": -0.03, "mfe": 0.25, "base_len": 30, "rs_pct": 80.0},
            {"setup": "BO", "r1": -0.02, "r5": -0.05, "r10": -0.10, "r20": None,
             "mae": -0.12, "mfe": 0.02, "base_len": 60, "rs_pct": 40.0}]
    s = summarize(fake)
    assert s["n"] == 2 and s["n20"] == 0 and s["win10"] == 0.5
    assert abs(s["med5"] - 0.025) < 1e-9
    assert s["win20"] is None, "khong du du lieu -> None, khong phai 0"
    b = dict(by_bucket(fake, "base_len"))
    assert b["20-35"]["n"] == 1 and b["50+"]["n"] == 1

    # moc vao mu doc duoc
    base = random_baseline({"AAA": bs})
    assert base["r10"] is not None

    # sang loc re khong duoc thay doi ket qua
    assert _could_fire(20.0, 4e6, 1e6, 1.8, 2_000_000)
    assert not _could_fire(20.0, 1e6, 1e6, 1.8, 2_000_000)
    assert not _could_fire(0.05, 4e6, 1e6, 1.8, 2_000_000), "dollar-vol thap"

    print("backtest.py selftest: ok")


# ───────────────────── CLI ─────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="chay tren kho nen")
    ap.add_argument("--limit", type=int, default=0, help="chi N ma dau")
    ap.add_argument("--setup", choices=["BO", "RV"], help="chi mot setup")
    ap.add_argument("--by", help="cat ket qua theo bien: "
                    + ", ".join(BUCKETS))
    ap.add_argument("--start", help="chi tinh lenh tu ngay YYYY-MM-DD")
    ap.add_argument("--end")
    ap.add_argument("--rvol", type=float, help="thu mot nguong rvol khac")
    ap.add_argument("--min-base-len", type=int)
    ap.add_argument("--max-depth", type=float)
    ap.add_argument("--rv-no-fund", action="store_true",
                    help="do rieng phan cau truc cua RV, bo loc co ban "
                         "(ket qua lac quan hon thuc te)")
    args = ap.parse_args()

    if not args.run:
        _smoke()
        return 0

    bo, rv = dict(setups.BO), dict(setups.RV)
    if args.rvol:
        bo["rvol"] = rv["rvol"] = args.rvol
    if args.min_base_len:
        bo["min_base_len"] = args.min_base_len
    if args.max_depth:
        bo["max_depth"] = bo["max_depth_cheap"] = args.max_depth
    if args.setup == "BO":
        rv = {**rv, "min_off_high": 9.9}      # khong ma nao dat -> tat RV
    elif args.setup == "RV":
        bo = {**bo, "min_base_len": 10**6}
    if args.rv_no_fund:
        rv["require_fund"] = False
        log("!! Bo loc so lieu co ban cua RV: dang do RIENG phan cau truc.")
        log("   Ket qua lac quan hon bot that, va RV la setup bi survivorship")
        log("   bias nang nhat. Dung dung con so nay de chinh nguong RV.")
    elif not args.setup or args.setup == "RV":
        log("(RV se khong ra lenh nao: chua co Phase 9.3. Dung --rv-no-fund "
            "de do rieng phan cau truc.)")

    run(DB, args.limit, bo, rv, by=args.by, start=args.start, end=args.end)
    return 0


if __name__ == "__main__":
    sys.exit(main())
