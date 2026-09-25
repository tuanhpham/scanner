"""regime.py - Trang thai thi truong tu nen ngay cua SPY. Stage 1 phan swing.

Quan he voi config.py giong quan he structure.py <-> setups.py: file nay DO
(`measure`) roi TRA BANG (`classify`). Khong mot con so nao nam trong logic -
tat ca o config.REGIME / config.PLAYBOOK. Ly do giong muc 9.1 README: khi
backtest cho biet nguong sai, ta muon sua NGUONG chu khong phai sua cach do.

NEN QUYET DINH (decision bar) - phan quan trong nhat cua file nay:

    Nen dung de phan loai LUON la phien DA DONG CUA cuoi cung: `bs[-1]` sau
    khi `_closed()` bo nen dang chay bang bars.partial_day().

Khong bao gio duoc phan loai bang nen cua chinh ngay dang giao dich roi hanh
dong trong ngay do. Lam vay la nhin truoc tuong lai, va no KHONG BAO GIO bao
loi - chi cho ra mot ket qua dep hon su that. Hai lop bao ve:

  1. bars.sync(drop_partial=True) da khong ghi nen dang chay vao kho.
  2. `_closed()` o day bo lai lan nua, vi kho nen co the da co nen hom nay do
     mot lan --sync sau 16:00 ET hom truoc, hoac do backfill.

Chuoi cron chay 08:00 ET (truoc gio mo cua) nen nen cuoi trong kho luon la
phien hom truoc - dung hoan toan. `--dry-run` in ra NGAY cua nen quyet dinh
de kiem duoc bang mat, chu khong phai tin vao lap luan tren.

Thuan stdlib (dung lai _sma/_atr cua structure.py) -> selftest chay duoc tren
may dev khong co pandas, khong mang, khong DB.

    python regime.py                   # selftest
    python regime.py --dry-run         # doc kho nen, in panel, KHONG ghi DB
    python regime.py --build           # ghi mot dong vao bang `regime`
    python regime.py --show 30         # 30 phien gan nhat da luu
    python regime.py --config          # in bang playbook dang chay

Ma thoat (cho cron): 0 = xong, 1 = loi, 2 = khong du du lieu.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

import bars
import config
import structure
from bars import Bar

ROOT = Path(__file__).resolve().parent
DB = ROOT / "state" / "baseline.db"

log = print


# ───────────────────────── do ─────────────────────────
def _closed(bs: list[Bar]) -> list[Bar]:
    """Bo nen dang chay o cuoi danh sach, neu co.

    Tra ve chinh `bs` khi nen cuoi da chot. Xem khoi docstring dau file: day la
    lop bao ve thu hai, khong phai lop duy nhat.
    """
    skip = bars.partial_day()
    if skip and bs and bs[-1].d == skip:
        return bs[:-1]
    return bs


def measure(bs: list[Bar], cfg: dict | None = None) -> dict | None:
    """Do cac con so cua trang thai thi truong tai nen CUOI CUNG cua `bs`.

    Ham thuan: khong DB, khong mang, khong doc gio he thong. Goi voi danh sach
    da qua `_closed()`.

    None = khong du du lieu. Co y KHONG tra ve ket qua "gan dung": mot regime
    tinh tren 150 nen se noi sai ve sma200 va khong co gi bao loi.
    """
    cfg = cfg or config.REGIME
    n = len(bs)
    if n < cfg["min_bars"]:
        return None
    cl = [b.c for b in bs]
    px = cl[-1]
    if px <= 0:
        return None

    s50 = structure._sma(cl, 50)
    s200 = structure._sma(cl, 200)
    if not s50[-1] or not s200[-1]:
        return None

    # Do doc sma50: so voi sma50 cua `slope_win` phien truoc.
    w = int(cfg["slope_win"])
    prev50 = s50[-1 - w] if len(s50) > w else None
    slope50 = (s50[-1] / prev50 - 1.0) if prev50 else None

    atr = structure._atr(bs, 14)
    atr14 = atr[-1]
    if not atr14:
        return None
    atr_pct = atr14 / px

    # atr_pct cua TUNG phien, roi trung binh `vol_win` phien gan nhat. Doi hoi
    # du ca cua so: thieu thi tra None de build() noi ra, chu khong lay trung
    # binh cua 20 phien roi goi no la "trung binh 100 phien".
    series = [a / c for a, c in zip(atr, cl) if a and c]
    win = int(cfg["vol_win"])
    if len(series) < win:
        return None
    avg = sum(series[-win:]) / win
    if not avg:
        return None

    return {
        "d": bs[-1].d,
        "px": px,
        "sma50": s50[-1],
        "sma200": s200[-1],
        "slope50": slope50,
        "atr14": atr14,
        "atr_pct": atr_pct,
        "atr_pct_avg": avg,
        "atr_ratio": atr_pct / avg,
        "n_bars": n,
    }


# ───────────────────────── phan loai ─────────────────────────
def slope_dir(slope: float | None, cfg: dict | None = None) -> str:
    """'rising' / 'falling' / 'flat'. None (chua du nen) -> 'flat'."""
    cfg = cfg or config.REGIME
    if slope is None:
        return "flat"
    if slope > cfg["slope_up"]:
        return "rising"
    if slope < cfg["slope_dn"]:
        return "falling"
    return "flat"


def vol_label(ratio: float | None, cfg: dict | None = None) -> str:
    """CONTRACTED / NORMAL / EXPANDED theo ty le atr_pct voi trung binh cua no."""
    cfg = cfg or config.REGIME
    if ratio is None:
        return "NORMAL"
    if ratio < cfg["vol_contract"]:
        return "CONTRACTED"
    if ratio > cfg["vol_expand"]:
        return "EXPANDED"
    return "NORMAL"


def trend_label(px: float, s50: float, s200: float, sdir: str) -> str:
    """Bang quyet dinh xu huong. Toan phan: moi to hop deu ra mot nhan.

    | 50 vs 200 | gia vs 50 | do doc      | ->                    |
    |-----------|-----------|-------------|-----------------------|
    | 50 > 200  | tren      | rising/flat | UPTREND               |
    | 50 > 200  | tren      | falling     | UPTREND_UNDER_STRESS  |
    | 50 > 200  | duoi      | bat ky      | UPTREND_UNDER_STRESS  |
    | 50 < 200  | duoi      | bat ky      | DOWNTREND             |
    | 50 < 200  | tren      | rising      | RANGE                 |
    | 50 < 200  | tren      | flat/fall   | DOWNTREND             |

    Hai dong dang chu y:

    - "50 > 200, gia tren 50, do doc GIAM" -> UNDER_STRESS chu khong phai
      UPTREND. Spec goc khong noi to hop nay thuoc dau; xep vao UPTREND se cho
      full size ngay giua luc xu huong dang quay dau.
    - "50 < 200, gia tren 50, do doc phang" -> DOWNTREND chu khong phai RANGE.
      Gia nhoi len tren sma50 trong mot xu huong giam la chuyen xay ra moi thang
      va phan lon lan la bull trap; doi do doc sma50 thuc su len moi goi la
      RANGE.
    """
    above50 = px > s50
    if s50 > s200:
        if not above50 or sdir == "falling":
            return "UPTREND_UNDER_STRESS"
        return "UPTREND"
    if above50 and sdir == "rising":
        return "RANGE"
    return "DOWNTREND"


def classify(m: dict, cfg: dict | None = None,
             book: dict | None = None) -> dict:
    """measure() -> nhan + playbook. Ham thuan, la phan duoc test ky nhat."""
    cfg = cfg or config.REGIME
    book = book or config.PLAYBOOK
    sdir = slope_dir(m["slope50"], cfg)
    trend = trend_label(m["px"], m["sma50"], m["sma200"], sdir)
    vol = vol_label(m["atr_ratio"], cfg)
    p = book[(trend, vol)]
    return {
        "trend": trend,
        "vol": vol,
        "slope_dir": sdir,
        # Chuoi rong = khong setup nao duoc phep. `size == 0.0` la cong tac
        # may doc duoc; dung kiem tra chuoi nay de quyet dinh.
        "playbook": ",".join(p["setups"]),
        "size": p["size"],
        "note": p["note"],
    }


def assess(bs: list[Bar], cfg: dict | None = None) -> dict | None:
    """measure + classify tren mot chuoi nen da qua `_closed()`."""
    m = measure(bs, cfg)
    return {**m, **classify(m, cfg)} if m else None


# ───────────────────────── bang `regime` ─────────────────────────
COLS = ("trend", "vol", "slope_dir", "px", "sma50", "sma200", "slope50",
        "atr14", "atr_pct", "atr_pct_avg", "atr_ratio", "playbook", "size",
        "bench", "n_bars")

DDL = """
CREATE TABLE IF NOT EXISTS regime(
  d TEXT PRIMARY KEY,
  trend TEXT, vol TEXT, slope_dir TEXT,
  px REAL, sma50 REAL, sma200 REAL, slope50 REAL,
  atr14 REAL, atr_pct REAL, atr_pct_avg REAL, atr_ratio REAL,
  playbook TEXT, size REAL, bench TEXT, n_bars INTEGER,
  updated TEXT);
"""


def con(db=DB) -> sqlite3.Connection:
    c = bars.con(db) if not isinstance(db, sqlite3.Connection) else db
    c.executescript(DDL)
    return c


def save(db, d: str, row: dict) -> None:
    """Upsert mot phien. `d` la khoa chinh -> chay lai bao nhieu lan cung duoc.

    Mot dong moi phien, khong phai mot dong duy nhat bi ghi de: lich su regime
    la thu de doi chieu voi ket qua trong bang `outcome` sau nay, va no mien
    phi - 252 dong moi nam.
    """
    c, mine = bars._c(db)
    try:
        c.executescript(DDL)
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        sets = ", ".join(f"{k}=excluded.{k}" for k in COLS) + ", updated=excluded.updated"
        with c:
            c.execute(
                f"INSERT INTO regime(d,{','.join(COLS)},updated) "
                f"VALUES({','.join('?' * (len(COLS) + 2))}) "
                f"ON CONFLICT(d) DO UPDATE SET {sets}",
                (d, *(row.get(k) for k in COLS), now))
    finally:
        if mine:
            c.close()


def latest(db=DB) -> dict | None:
    """Dong gan nhat, hoac None khi bang chua co dong nao."""
    rows = history(db, n=1)
    return rows[0] if rows else None


def history(db=DB, n: int = 90) -> list[dict]:
    """`n` phien gan nhat, XEP TANG DAN theo ngay (cuoi = moi nhat)."""
    c, mine = bars._c(db)
    try:
        c.executescript(DDL)
        rows = c.execute(
            f"SELECT d,{','.join(COLS)},updated FROM regime "
            f"ORDER BY d DESC LIMIT ?", (int(n),)).fetchall()
    finally:
        if mine:
            c.close()
    keys = ("d", *COLS, "updated")
    return [dict(zip(keys, r)) for r in reversed(rows)]


# ───────────────────────── dung ─────────────────────────
def build(db=DB, cfg: dict | None = None, dry: bool = False,
          sym: str | None = None) -> dict:
    """Do + phan loai + ghi. Tra ve {"row": ..., "err": ...}.

    `dry=True`: tinh het nhung khong ghi DB. Dung cho --dry-run va cho test.
    """
    cfg = cfg or config.REGIME
    sym = sym or config.BENCH
    c = con(db)
    try:
        bs = _closed(bars.load(c, sym, limit=int(cfg["load_n"])))
        if len(bs) < cfg["min_bars"]:
            return {"err": f"{sym}: chi co {len(bs)} nen da chot, can "
                           f"{cfg['min_bars']}. Chay `python bars.py --sync "
                           f"--full` truoc.", "row": None}
        m = measure(bs, cfg)
        if not m:
            return {"err": f"{sym}: {len(bs)} nen nhung khong do duoc "
                           f"(sma200 hoac ATR thieu).", "row": None}
        row = {**m, **classify(m, cfg), "bench": sym}
        if not dry:
            save(c, row["d"], row)
        return {"err": None, "row": row}
    finally:
        if not isinstance(db, sqlite3.Connection):
            c.close()


# ───────────────────────── trinh bay ─────────────────────────
def _pct(v, d=2) -> str:
    return "-" if v is None else f"{v * 100:+.{d}f}%"


def panel(row: dict, prev: dict | None = None) -> str:
    """Panel text cho stdout. Stage 4 se lam ban HTML cho Telegram trong
    render.py; day la ban de doc bang mat khi chay --dry-run."""
    arrow = {"rising": "len", "falling": "xuong", "flat": "phang"}
    sets = row["playbook"] or "(khong setup nao)"
    size = {1.0: "FULL", 0.5: "HALF", 0.0: "KHONG VAO"}.get(
        row["size"], str(row["size"]))
    out = [
        f"NEN QUYET DINH   {row['d']}   ({row['bench']}, {row['n_bars']} nen)",
        "",
        f"  {row['trend']} / {row['vol']}",
        f"  {row['note']}",
        "",
        f"  gia            {row['px']:.2f}",
        f"  sma50          {row['sma50']:.2f}   "
        f"({'tren' if row['px'] > row['sma50'] else 'DUOI'} sma50)",
        f"  sma200         {row['sma200']:.2f}   "
        f"({'50>200' if row['sma50'] > row['sma200'] else '50<200'})",
        f"  do doc sma50   {_pct(row['slope50'])} / "
        f"{config.REGIME['slope_win']} phien  -> {arrow[row['slope_dir']]}",
        f"  atr14          {row['atr14']:.2f}  = {_pct(row['atr_pct'])} cua gia",
        f"  trung binh     {_pct(row['atr_pct_avg'])} tren "
        f"{config.REGIME['vol_win']} phien  -> {row['atr_ratio']:.2f}x",
        "",
        f"  PLAYBOOK       {sets}",
        f"  CO VI THE      {size}",
    ]
    if prev and prev.get("d") != row["d"]:
        if prev.get("trend") != row["trend"] or prev.get("vol") != row["vol"]:
            out += ["", f"  DOI TRANG THAI: {prev['trend']}/{prev['vol']}"
                        f" -> {row['trend']}/{row['vol']}"
                        f"  (phien truoc {prev['d']})"]
    return "\n".join(out)


def _config_table() -> str:
    rows = config.playbook_rows()
    out = [f"bench {config.BENCH}   sector {len(config.SECTOR_ETFS)} ma   "
           f"phong thu {','.join(config.DEFENSIVE)}", ""]
    for k, v in config.REGIME.items():
        out.append(f"  {k:<14}{v}")
    out += ["", f"  {'TREND':<22}{'VOL':<12}{'SIZE':>5}  SETUP"]
    for r in rows:
        out.append(f"  {r['trend']:<22}{r['vol']:<12}{r['size']:>5.1f}  "
                   f"{','.join(r['setups']) or '-'}")
    return "\n".join(out)


# ───────────────────────── selftest ─────────────────────────
def _ser(specs, start="2023-01-02", vol=1e6, spread=0.005) -> list[Bar]:
    """Chuoi nen tong hop: moi spec = (gia dau, gia cuoi, so phien). Giong
    structure._series nhung nhan `spread` de dieu khien ATR."""
    out: list[Bar] = []
    d = dt.date.fromisoformat(start)
    for p0, p1, n in specs:
        for i in range(n):
            p = p0 + (p1 - p0) * (i / max(n - 1, 1))
            out.append(Bar(d.isoformat(), p, p * (1 + spread),
                           p * (1 - spread), p, vol))
            d += dt.timedelta(days=1)
    return out


def _smoke() -> None:
    import tempfile
    cfg = config.REGIME

    # --- bang playbook phai toan phan ---
    for t in config.TRENDS:
        for v in config.VOLS:
            assert (t, v) in config.PLAYBOOK, f"thieu playbook ({t}, {v})"
    assert len(config.PLAYBOOK) == len(config.TRENDS) * len(config.VOLS)
    assert len(config.SECTOR_ETFS) == 11, "phai du 11 sector SPDR"
    assert config.BENCH in config.REGIME_SYMS

    # --- trend_label: ca 6 dong cua bang quyet dinh ---
    assert trend_label(100, 95, 90, "rising") == "UPTREND"
    assert trend_label(100, 95, 90, "flat") == "UPTREND"
    assert trend_label(100, 95, 90, "falling") == "UPTREND_UNDER_STRESS"
    assert trend_label(90, 95, 90, "rising") == "UPTREND_UNDER_STRESS"
    assert trend_label(85, 90, 95, "falling") == "DOWNTREND"
    assert trend_label(100, 95, 105, "rising") == "RANGE"
    assert trend_label(100, 95, 105, "flat") == "DOWNTREND"

    # --- nhan do doc / bien do ---
    assert slope_dir(0.02) == "rising" and slope_dir(-0.02) == "falling"
    assert slope_dir(0.0) == "flat" and slope_dir(None) == "flat"
    assert slope_dir(cfg["slope_up"]) == "flat", "nguong la '>' khong phai '>='"
    assert vol_label(0.5) == "CONTRACTED" and vol_label(2.0) == "EXPANDED"
    assert vol_label(1.0) == "NORMAL" and vol_label(None) == "NORMAL"

    # --- measure: thi truong tang deu ---
    up = _ser([(300.0, 460.0, 320)])
    m = measure(up)
    assert m, "320 nen phai du"
    assert m["px"] > m["sma50"] > m["sma200"], "tang deu -> gia > 50 > 200"
    assert m["slope50"] > cfg["slope_up"]
    c1 = classify(m)
    assert c1["trend"] == "UPTREND", c1
    assert c1["size"] > 0 and "BO" in c1["playbook"]

    # --- measure: thi truong giam deu ---
    dn = _ser([(460.0, 300.0, 320)])
    c2 = classify(measure(dn))
    assert c2["trend"] == "DOWNTREND", c2
    assert c2["size"] == 0.0 and c2["playbook"] == "", c2

    # --- dinh roi: 50 van tren 200 nhung gia da mat 50 ---
    stress = _ser([(300.0, 460.0, 300)]) + _ser(
        [(460.0, 415.0, 20)], start="2024-01-02")
    ms = measure(stress)
    assert ms["sma50"] > ms["sma200"], "50 chua kip cat xuong 200"
    assert ms["px"] < ms["sma50"], "gia da mat sma50"
    assert classify(ms)["trend"] == "UPTREND_UNDER_STRESS"
    assert classify(ms)["size"] == 0.0

    # --- bien do: cung duong gia, chi khac bien do trong ngay o doan cuoi ---
    calm = _ser([(300.0, 460.0, 300)], spread=0.004) + _ser(
        [(460.0, 470.0, 40)], start="2024-01-02", spread=0.0005)
    mc = measure(calm)
    assert mc["atr_ratio"] < cfg["vol_contract"], mc["atr_ratio"]
    assert classify(mc)["vol"] == "CONTRACTED"

    wild = _ser([(300.0, 460.0, 300)], spread=0.004) + _ser(
        [(460.0, 470.0, 40)], start="2024-01-02", spread=0.03)
    mw = measure(wild)
    assert mw["atr_ratio"] > cfg["vol_expand"], mw["atr_ratio"]
    assert classify(mw)["vol"] == "EXPANDED"
    assert classify(mw)["size"] == 0.5, "uptrend + bien do rong -> nua co"

    # --- khong du du lieu thi tra None, khong tra ket qua gan dung ---
    assert measure(_ser([(300.0, 400.0, cfg["min_bars"] - 1)])) is None
    assert measure([]) is None
    # du min_bars nhung thieu cua so atr -> None chu khong lay trung binh ngan
    assert measure(_ser([(300.0, 400.0, 260)]), {**cfg, "vol_win": 400}) is None

    # --- vong tron DB: COLS phai khop DDL ---
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    c = con(tmp)
    have = [r[1] for r in c.execute("PRAGMA table_info(regime)")]
    assert set(COLS) | {"d", "updated"} == set(have), (
        f"COLS lech DDL: {set(COLS) ^ (set(have) - {'d', 'updated'})}")

    # --- build tren kho nen that ---
    bars.save(c, {"SPY": [(b.d, b.o, b.h, b.l, b.c, b.c, b.v) for b in up]})
    r = build(c, dry=True)
    assert r["row"] and not r["err"], r
    assert r["row"]["trend"] == "UPTREND"
    assert latest(c) is None, "dry=True khong duoc ghi DB"

    r = build(c)
    assert not r["err"], r["err"]
    got = latest(c)
    assert got and got["d"] == up[-1].d and got["trend"] == "UPTREND"
    assert got["bench"] == "SPY" and got["size"] == 1.0
    build(c)                      # chay lai: upsert, khong nhan doi dong
    assert len(history(c, 999)) == 1, "khoa chinh `d` phai chan trung dong"

    # thieu ma chuan -> loi noi ro, khong im lang
    r2 = build(c, sym="KHONGCO")
    assert r2["row"] is None and "nen da chot" in r2["err"], r2

    # panel khong duoc nem loi voi bat ky trang thai nao
    for row in (r["row"], {**r["row"], "size": 0.0, "playbook": ""}):
        assert "NEN QUYET DINH" in panel(row)
    assert "DOI TRANG THAI" in panel(
        {**r["row"], "d": "2024-01-02"},
        {**r["row"], "d": "2024-01-01", "trend": "DOWNTREND"})

    # snapshot phai json duoc (push.py se day len scanner:config)
    assert json.dumps(config.snapshot())
    c.close()
    print("regime.py selftest: ok")


# ───────────────────────── CLI ─────────────────────────
if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                            # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", action="store_true", help="ghi vao bang regime")
    ap.add_argument("--dry-run", action="store_true",
                    help="tinh va in, KHONG ghi DB")
    ap.add_argument("--show", nargs="?", type=int, const=20, metavar="N",
                    help="in N phien gan nhat da luu")
    ap.add_argument("--config", action="store_true", help="in config dang chay")
    ap.add_argument("--db", default=str(DB))
    a = ap.parse_args()

    if a.config:
        print(_config_table())
        raise SystemExit(0)

    if a.show is not None:
        rows = history(a.db, a.show)
        if not rows:
            print("bang `regime` chua co dong nao. Chay --build truoc.")
            raise SystemExit(2)
        print(f"{'NGAY':<12}{'TREND':<22}{'VOL':<12}{'SIZE':>5}  SETUP")
        for r in rows:
            print(f"{r['d']:<12}{r['trend']:<22}{r['vol']:<12}"
                  f"{(r['size'] or 0):>5.1f}  {r['playbook'] or '-'}")
        raise SystemExit(0)

    if not (a.build or a.dry_run):
        _smoke()
        raise SystemExit(0)

    res = build(a.db, dry=a.dry_run)
    if res["err"]:
        print(f"regime: {res['err']}")
        raise SystemExit(2)
    prev = None
    rows = history(a.db, 2)
    if rows and rows[-1]["d"] == res["row"]["d"]:
        prev = rows[-2] if len(rows) > 1 else None
    elif rows:
        prev = rows[-1]
    print(panel(res["row"], prev))
    if a.dry_run:
        print("\n(--dry-run: khong ghi DB)")
    raise SystemExit(0)
