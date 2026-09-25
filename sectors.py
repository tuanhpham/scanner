"""sectors.py - Xep hang 11 sector SPDR. Stage 2 phan swing.

Giong regime.py: file nay DO (`measure`) roi XEP (`rank`), moi con so dieu chinh
duoc nam trong config.SECTORS.

BA DIEU quan trong hon phan con lai cua file:

1. TAT CA 11 MA PHAI CUNG MOT NEN QUYET DINH. Diem tong la percentile trong ca
   ro, tuc la mot phep so sanh CHEO. Neu 10 ma tinh den thu Nam ma XLRE tinh den
   thu Tu (nen hom sau chua ve), percentile cua ca 11 ma deu lech, va bang xep
   hang van in ra binh thuong. `build()` tu choi xep hang trong truong hop do va
   noi ro ma nao tre - mot bang xep hang sai te hon khong co bang nao.

2. LICH SU LUU VAO SQLITE, khong phai JSON/CSV. Spec cho phep tranh luan; toi
   dong y voi SQLite, ly do cu the:
     - "hang thay doi so voi 5 va 21 phien truoc" la mot phep truy van theo
       ngay. Voi JSON thi phai doc ca file roi tu loc; voi SQL la mot cau.
     - Bieu do lich su hang can 90 phien x 11 ma = ~1000 dong. Day ca 1000 dong
       moi ngay len D1 thi phi han muc ghi; co SQL thi push.py chi can lay
       khoang ngay con thieu.
     - DB da co san, da WAL, da duoc sao luu cung 3 nam nen. Them mot file
       dinh dang khac la them mot thu co the lech voi phan con lai.

3. HANG LA SO NHO LA TOT (1 = manh nhat). Doi hang tu 5 len 2 la TANG 3 bac,
   nen `delta = hang_cu - hang_moi` va so duong = di len. Nham dau nay thi mui
   ten tren dashboard chi sai nguoc, va khong co gi bao loi.

Thuan stdlib (dung lai _sma/_ema/rank_pct cua structure.py) -> selftest chay
duoc tren may dev khong co pandas, khong mang.

    python sectors.py                    # selftest
    python sectors.py --dry-run          # doc kho nen, in bang, KHONG ghi DB
    python sectors.py --build            # ghi 11 dong vao bang `sector_rank`
    python sectors.py --backfill 120     # dung lai lich su 120 phien gan nhat
    python sectors.py --show 20          # lich su hang da luu
    python sectors.py --config           # in nguong dang chay

Ma thoat (cho cron): 0 = xong, 1 = loi, 2 = khong du du lieu.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path

import bars
import config
import structure
from bars import Bar
from regime import _closed

ROOT = Path(__file__).resolve().parent
DB = ROOT / "state" / "baseline.db"

log = print


# ───────────────────────── do ─────────────────────────
def measure(bs: list[Bar], cfg: dict | None = None) -> dict | None:
    """Do mot sector tai nen CUOI CUNG cua `bs`. None = chua du du lieu.

    Thuan: khong DB, khong mang, khong dong ho he thong. Tra None chu khong bao
    gio tra mot ket qua thieu cot - xem config.SECTORS["min_bars"].
    """
    cfg = cfg or config.SECTORS
    wins = tuple(cfg["ret_wins"])
    if len(bs) < cfg["min_bars"] or len(bs) <= max(wins):
        return None
    cl = [b.c for b in bs]
    px = cl[-1]
    if px <= 0:
        return None

    out: dict = {"d": bs[-1].d, "px": px, "n_bars": len(bs)}
    for n in wins:
        base = cl[-1 - n]
        if base <= 0:
            return None
        out[f"ret{n}"] = px / base - 1.0

    sma = structure._sma(cl, int(cfg["sma_win"]))
    ema = structure._ema(cl, int(cfg["ema_win"]))
    if sma[-1] is None or ema[-1] is None:
        return None
    w = int(cfg["slope_win"])
    if len(sma) <= w or sma[-1 - w] is None or sma[-1 - w] <= 0:
        return None

    out["sma50"] = sma[-1]
    out["ema21"] = ema[-1]
    out["slope50"] = sma[-1] / sma[-1 - w] - 1.0
    out["above_sma50"] = int(px > sma[-1])
    out["above_ema21"] = int(px > ema[-1])
    out["slope_up"] = int(out["slope50"] > 0.0)
    return out


def rank(ms: dict[str, dict], cfg: dict | None = None) -> list[dict]:
    """Xep hang tu cac ket qua `measure`. Tra ve danh sach manh nhat truoc.

    Diem tong = trung binh percentile cua ba cua so loi nhuan. Xem chu thich
    config.SECTORS["ret_wins"] ve ly do percentile chu khong phai loi nhuan.

    Dong diem thi pha bang ret63 roi bang ten ma: thu tu PHAI on dinh, neu
    khong thi "doi hang" hom sau se bao mot cu doi hang khong he xay ra.
    """
    cfg = cfg or config.SECTORS
    wins = tuple(cfg["ret_wins"])
    if not ms:
        return []
    pcts = {n: structure.rank_pct({s: m[f"ret{n}"] for s, m in ms.items()})
            for n in wins}
    rows = []
    for s, m in ms.items():
        row = dict(m, sym=s)
        for n in wins:
            row[f"pct{n}"] = pcts[n][s]
        row["composite"] = structure._mean(row[f"pct{n}"] for n in wins)
        rows.append(row)
    rows.sort(key=lambda r: (-r["composite"], -r[f"ret{wins[1]}"], r["sym"]))
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def defensive_top(rows: list[dict], cfg: dict | None = None) -> list[str]:
    """Sector phong thu dang nam trong top N, theo thu tu hang.

    XLP/XLU len top khong phai "loi" - no la thong tin: dong tien dang tra tien
    cho hang tieu dung thiet yeu va dien nuoc thay vi cho tang truong. Thuong
    di truoc mot doan yeu cua ca thi truong.
    """
    cfg = cfg or config.SECTORS
    top = int(cfg["top_n"])
    return [r["sym"] for r in rows[:top] if r["sym"] in config.DEFENSIVE]


# ───────────────────────── bang `sector_rank` ─────────────────────────
COLS = ("rank", "composite", "ret21", "ret63", "ret126",
        "pct21", "pct63", "pct126", "px", "sma50", "ema21", "slope50",
        "above_sma50", "above_ema21", "slope_up", "n_bars")

DDL = """
CREATE TABLE IF NOT EXISTS sector_rank(
  d            TEXT NOT NULL,
  sym          TEXT NOT NULL,
  rank         INTEGER,
  composite    REAL,
  ret21        REAL,
  ret63        REAL,
  ret126       REAL,
  pct21        REAL,
  pct63        REAL,
  pct126       REAL,
  px           REAL,
  sma50        REAL,
  ema21        REAL,
  slope50      REAL,
  above_sma50  INTEGER,
  above_ema21  INTEGER,
  slope_up     INTEGER,
  n_bars       INTEGER,
  updated      TEXT,
  PRIMARY KEY(d, sym)
);
CREATE INDEX IF NOT EXISTS ix_sector_rank_sym ON sector_rank(sym, d);
"""


def con(db=DB) -> sqlite3.Connection:
    c = bars.con(db) if not isinstance(db, sqlite3.Connection) else db
    c.executescript(DDL)
    return c


def save(db, d: str, rows: list[dict]) -> int:
    """Upsert ca ro cua mot phien. (d, sym) la khoa -> chay lai an toan.

    Ghi CA RO trong mot transaction: nua ro cua hom nay cong nua ro cua hom qua
    la mot bang xep hang khong ton tai. Khong co gi phat hien ra dieu do sau do.
    """
    if not rows:
        return 0
    c, mine = bars._c(db)
    try:
        c.executescript(DDL)
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        sets = ", ".join(f"{k}=excluded.{k}" for k in COLS) + ", updated=excluded.updated"
        with c:
            c.executemany(
                f"INSERT INTO sector_rank(d,sym,{','.join(COLS)},updated) "
                f"VALUES({','.join('?' * (len(COLS) + 3))}) "
                f"ON CONFLICT(d,sym) DO UPDATE SET {sets}",
                [(d, r["sym"], *(r.get(k) for k in COLS), now) for r in rows])
    finally:
        if mine:
            c.close()
    return len(rows)


def dates(db=DB, n: int = 0) -> list[str]:
    """Cac phien da luu, xep tang dan. `n=0` = tat ca."""
    c, mine = bars._c(db)
    try:
        c.executescript(DDL)
        q = "SELECT DISTINCT d FROM sector_rank ORDER BY d DESC"
        rows = c.execute(q + (" LIMIT ?" if n else ""),
                         (int(n),) if n else ()).fetchall()
    finally:
        if mine:
            c.close()
    return [r[0] for r in reversed(rows)]


def load_rank(db=DB, d: str | None = None) -> list[dict]:
    """Ca ro cua mot phien (mac dinh: phien moi nhat), manh nhat truoc."""
    c, mine = bars._c(db)
    try:
        c.executescript(DDL)
        if d is None:
            r = c.execute("SELECT MAX(d) FROM sector_rank").fetchone()
            d = r and r[0]
            if not d:
                return []
        rows = c.execute(
            f"SELECT d,sym,{','.join(COLS)},updated FROM sector_rank "
            f"WHERE d=? ORDER BY rank", (d,)).fetchall()
    finally:
        if mine:
            c.close()
    keys = ("d", "sym", *COLS, "updated")
    return [dict(zip(keys, r)) for r in rows]


def history(db=DB, n: int | None = None) -> list[dict]:
    """Lich su hang `n` phien gan nhat, xep tang dan theo (ngay, hang).

    Day la nguon cua bieu do lich su hang tren dashboard. Chi lay `d`, `sym`,
    `rank`, `composite`: bieu do khong can 16 cot, va push.py se phai nhoi cai
    nay qua gioi han 500KB cua mot khoa D1.
    """
    n = int(config.SECTORS["hist_days"] if n is None else n)
    ds = dates(db, n)
    if not ds:
        return []
    c, mine = bars._c(db)
    try:
        rows = c.execute(
            "SELECT d,sym,rank,composite FROM sector_rank WHERE d>=? "
            "ORDER BY d, rank", (ds[0],)).fetchall()
    finally:
        if mine:
            c.close()
    return [dict(zip(("d", "sym", "rank", "composite"), r)) for r in rows]


def changes(db=DB, d: str | None = None,
            cfg: dict | None = None) -> dict[str, dict[int, int | None]]:
    """Thay doi hang so voi N phien truoc, cho tung ma: {sym: {5: +2, 21: None}}.

    So duong = DI LEN (hang nho hon). None = chua co du lich su de so, va phai
    hien la "-" chu khong phai 0: "khong doi hang" va "khong biet" la hai cau
    tra loi khac nhau, gop chung lai la noi doi.

    Dem theo CAC PHIEN CO TRONG BANG chu khong theo ngay lich. Neu lich su bi
    thung (VM tat may mot tuan) thi "5 phien truoc" o day la 5 ban ghi truoc -
    lech so voi thuc te. `--backfill` lap lai lich su lien tuc de tranh chuyen
    do; README ghi ro han che nay.
    """
    cfg = cfg or config.SECTORS
    wins = [int(w) for w in cfg["change_wins"]]
    ds = dates(db)
    if not ds:
        return {}
    d = d or ds[-1]
    if d not in ds:
        return {}
    i = ds.index(d)
    now = {r["sym"]: r["rank"] for r in load_rank(db, d)}
    out: dict[str, dict[int, int | None]] = {s: {} for s in now}
    for w in wins:
        j = i - w
        old = {r["sym"]: r["rank"] for r in load_rank(db, ds[j])} if j >= 0 else {}
        for s, rk in now.items():
            o = old.get(s)
            out[s][w] = None if o is None or rk is None else o - rk
    return out


# ───────────────────────── dung ─────────────────────────
def build(db=DB, cfg: dict | None = None, dry: bool = False,
          upto: str | None = None, syms=None) -> dict:
    """Do + xep + ghi mot phien. Tra ve {"rows", "d", "err", "warn"}.

    `upto`: chi dung nen den ngay nay (cho --backfill). None = phien da chot
    gan nhat.
    """
    cfg = cfg or config.SECTORS
    syms = tuple(syms or config.SECTOR_ETFS)
    c = con(db)
    try:
        ms: dict[str, dict] = {}
        thieu: list[str] = []
        for s in syms:
            bs = bars.load(c, s, limit=int(cfg["load_n"]), upto=upto)
            # `upto` da la mot ngay lich su -> khong con nen dang chay de bo.
            m = measure(bs if upto else _closed(bs), cfg)
            if m:
                ms[s] = m
            else:
                thieu.append(f"{s}({len(bs)} nen)")
        if thieu:
            return {"rows": [], "d": None, "warn": [],
                    "err": f"khong do duoc {len(thieu)}/{len(syms)} sector: "
                           f"{', '.join(thieu)}. Can it nhat "
                           f"{cfg['min_bars']} nen moi ma - chay "
                           f"`python bars.py --sync --full`."}

        # DIEU 1 cua docstring dau file: percentile la phep so sanh cheo, nen
        # mot ma tinh den ngay khac la ca bang sai. Tu choi, khong xep bua.
        dd = {s: m["d"] for s, m in ms.items()}
        newest = max(dd.values())
        tre = sorted(s for s, x in dd.items() if x != newest)
        if tre:
            return {"rows": [], "d": None, "warn": [],
                    "err": f"nen quyet dinh khong dong nhat: {len(tre)} ma con "
                           f"o phien cu ({', '.join(f'{s}={dd[s]}' for s in tre)}) "
                           f"trong khi ca ro da den {newest}. Khong xep hang vi "
                           f"percentile so sanh cheo se lech ca 11 ma."}

        rows = rank(ms, cfg)
        if not dry:
            save(c, newest, rows)
        warn = defensive_top(rows, cfg)
        return {"rows": rows, "d": newest, "err": None, "warn": warn}
    finally:
        if not isinstance(db, sqlite3.Connection):
            c.close()


def backfill(db=DB, cfg: dict | None = None, days: int = 120,
             dry: bool = False, quiet: bool = False) -> dict:
    """Dung lai lich su hang cho `days` phien gan nhat.

    Lay lich phien tu ma chuan (SPY) trong kho nen, roi goi build(upto=d) cho
    tung phien. Vi build() chi doc nen <= d, ket qua giong y nhu no da duoc
    chay vao dung buoi sang phien do - khong nhin truoc tuong lai.

    Can lich su vi "doi hang so voi 21 phien truoc" khong the tinh ra tu mot
    dong duy nhat. Chay mot lan luc lap dat, sau do cron ghi them moi ngay.
    """
    cfg = cfg or config.SECTORS
    c = con(db)
    try:
        cal = [b.d for b in _closed(bars.load(c, config.BENCH, limit=10_000))]
        if not cal:
            return {"ok": 0, "skip": 0, "err": f"kho nen khong co "
                                               f"{config.BENCH}, khong biet "
                                               f"lich phien."}
        ds = cal[-int(days):]
        ok = skip = trung = 0
        da: set[str] = set()
        loi: list[str] = []
        for d in ds:
            res = build(c, cfg, dry=dry, upto=d)
            if res["err"]:
                skip += 1
                if len(loi) < 3:
                    loi.append(f"{d}: {res['err'][:70]}")
            elif res["d"] in da:
                # Ngay `d` co trong lich cua ma chuan nhung ca ro sector khong
                # co nen moi nao -> build() ghi de dung dong cu. Dem rieng chu
                # khong dem la "xong": bao "60 phien" khi chi co 40 dong la
                # bao cao sai, va no che mat viec kho nen sector dang thieu nen.
                trung += 1
            else:
                da.add(res["d"])
                ok += 1
                if not quiet:
                    top = " ".join(r["sym"] for r in res["rows"][:3])
                    log(f"  {res['d']}  top3 {top}")
        err = None
        if not ok:
            err = ("khong dung duoc phien nao. " + " | ".join(loi)) if loi else \
                  "khong co phien nao trong khoang yeu cau."
        return {"ok": ok, "skip": skip, "trung": trung, "err": err,
                "dau": ds[0], "cuoi": ds[-1], "loi": loi}
    finally:
        if not isinstance(db, sqlite3.Connection):
            c.close()


# ───────────────────────── trinh bay ─────────────────────────
def _pct(v, d=1) -> str:
    return "     -" if v is None else f"{v * 100:+6.1f}%"


def _arrow(v) -> str:
    """Mui ten doi hang. `None` -> "-" (chua biet), 0 -> "=" (khong doi)."""
    if v is None:
        return "  -"
    if v == 0:
        return "  ="
    return f"{'^' if v > 0 else 'v'}{abs(v):<2}"


def panel(rows: list[dict], chg: dict | None = None,
          warn: list[str] | None = None, cfg: dict | None = None) -> str:
    """Bang cho stdout. Ban dep co dau cho Telegram se o render.py (Stage 4)."""
    cfg = cfg or config.SECTORS
    if not rows:
        return "XEP HANG SECTOR: khong co du lieu"
    chg = chg or {}
    wins = tuple(cfg["ret_wins"])
    cw = [int(w) for w in cfg["change_wins"]]
    top = int(cfg["top_n"])
    out = [
        f"XEP HANG SECTOR   {rows[0]['d']}   ({len(rows)} ma, "
        f"{rows[0]['n_bars']} nen)",
        "",
        f"   #  MA     DIEM  {wins[0]:>6}d {wins[1]:>6}d {wins[2]:>6}d  "
        + "  ".join(f"{w}p" for w in cw) + "  SMA50 EMA21 DOC",
    ]
    for r in rows:
        cs = chg.get(r["sym"], {})
        mark = ">" if r["rank"] <= top else " "
        out.append(
            f" {mark}{r['rank']:2d}  {r['sym']:<5} {r['composite']:5.1f}  "
            f"{_pct(r[f'ret{wins[0]}'])} {_pct(r[f'ret{wins[1]}'])} "
            f"{_pct(r[f'ret{wins[2]}'])}  "
            + " ".join(_arrow(cs.get(w)) for w in cw)
            + f"  {'tren' if r['above_sma50'] else 'DUOI':<5} "
            f"{'tren' if r['above_ema21'] else 'DUOI':<5} "
            f"{'len' if r['slope_up'] else 'xuong'}")
    out += ["", f"  TOP {top}: " + " ".join(r["sym"] for r in rows[:top])]
    if warn:
        out += [f"  CANH BAO: sector phong thu trong top {top} "
                f"({' '.join(warn)}) -> dong tien dang rut khoi rui ro, "
                f"ha ky vong breakout."]
    return "\n".join(out)


def _config_table() -> str:
    c = config.SECTORS
    out = [f"cua so loi nhuan {'/'.join(str(w) for w in c['ret_wins'])} phien"
           f"   top {c['top_n']}   phong thu "
           f"{','.join(config.DEFENSIVE)}", ""]
    for k, v in c.items():
        out.append(f"  {k:<13} {'/'.join(map(str, v)) if isinstance(v, tuple) else v}")
    return "\n".join(out)


# ───────────────────────── selftest ─────────────────────────
def _ser(specs, start="2023-01-02", vol=1e6, spread=0.005) -> list[Bar]:
    """Chuoi nen tuyen tinh tung doan: [(gia_dau, gia_cuoi, so_nen), ...]."""
    out: list[Bar] = []
    d = dt.date.fromisoformat(start)
    for p0, p1, n in specs:
        for i in range(n):
            p = p0 + (p1 - p0) * (i / max(n - 1, 1))
            out.append(Bar(d.isoformat(), p, p * (1 + spread), p * (1 - spread),
                           p, vol))
            d += dt.timedelta(days=1)
    return out


def _fake_ro(n: int = 300) -> dict[str, list[Bar]]:
    """11 sector, moc tang khac nhau -> thu tu biet truoc.

    XLK tang manh nhat, XLU giam - nen XLK phai hang 1 va XLU hang 11.
    """
    end = {"XLK": 200.0, "XLY": 180.0, "XLF": 165.0, "XLI": 150.0,
           "XLC": 140.0, "XLV": 130.0, "XLB": 120.0, "XLE": 112.0,
           "XLRE": 106.0, "XLP": 102.0, "XLU": 92.0}
    return {s: _ser([(100.0, e, n)]) for s, e in end.items()}


def _smoke() -> None:
    import json
    import tempfile

    cfg = config.SECTORS

    # --- config day du ---
    assert len(config.SECTOR_ETFS) == 11
    assert len(cfg["ret_wins"]) == 3 and cfg["top_n"] == 3
    assert cfg["min_bars"] > max(cfg["ret_wins"]), "min_bars phai phu ret126"
    assert json.dumps(config.snapshot())            # tuple -> list duoc

    # --- measure ---
    bs = _ser([(100.0, 150.0, 300)])
    m = measure(bs, cfg)
    assert m and m["d"] == bs[-1].d and m["n_bars"] == 300
    assert m["above_sma50"] and m["above_ema21"] and m["slope_up"]
    assert m["slope50"] > 0 and m["ret21"] > 0 < m["ret126"]
    # ret126 > ret63 > ret21 tren mot duong tang deu (cua so dai = tang nhieu)
    assert m["ret126"] > m["ret63"] > m["ret21"], m
    # gia tri dung, khong chi dung dau
    assert abs(m["ret21"] - (bs[-1].c / bs[-22].c - 1.0)) < 1e-12

    # chuoi giam -> moi co hieu doi chieu
    md = measure(_ser([(150.0, 100.0, 300)]), cfg)
    assert md and not md["above_sma50"] and not md["above_ema21"]
    assert not md["slope_up"] and md["ret63"] < 0

    # --- khong du du lieu -> None, khong phai ket qua thieu cot ---
    assert measure([], cfg) is None
    assert measure(_ser([(100.0, 110.0, cfg["min_bars"] - 1)]), cfg) is None
    assert measure(_ser([(100.0, 110.0, 200)]), {**cfg, "ret_wins": (21, 63, 500)}) is None
    assert measure(_ser([(100.0, 110.0, 150)]), {**cfg, "sma_win": 400}) is None

    # --- rank: thu tu biet truoc ---
    ro = _fake_ro()
    ms = {s: measure(v, cfg) for s, v in ro.items()}
    rows = rank(ms, cfg)
    assert [r["rank"] for r in rows] == list(range(1, 12))
    assert rows[0]["sym"] == "XLK" and rows[-1]["sym"] == "XLU"
    assert rows[0]["composite"] == 100.0 and rows[-1]["composite"] == 0.0
    assert all(rows[i]["composite"] >= rows[i + 1]["composite"]
               for i in range(len(rows) - 1))
    # percentile la cua CA RO: doi mot ma thi hang cua ma khac co the doi
    assert set(rows[0]) >= {"pct21", "pct63", "pct126", "composite", "rank"}

    # dong diem -> thu tu on dinh (khong phu thuoc thu tu dict dau vao)
    tie = {s: measure(_ser([(100.0, 130.0, 300)]), cfg)
           for s in config.SECTOR_ETFS}
    a = [r["sym"] for r in rank(tie, cfg)]
    b = [r["sym"] for r in rank(dict(reversed(list(tie.items()))), cfg)]
    assert a == b == sorted(config.SECTOR_ETFS), "dong diem phai pha bang ten"

    assert rank({}, cfg) == []

    # --- canh bao phong thu ---
    assert defensive_top(rows, cfg) == [], "XLK/XLY/XLF dung dau -> khong canh bao"
    xoay = rank({s: measure(_ser([(100.0, 160.0 if s in config.DEFENSIVE
                                  else 105.0, 300)]), cfg)
                 for s in config.SECTOR_ETFS}, cfg)
    assert defensive_top(xoay, cfg) == ["XLP", "XLU"], [r["sym"] for r in xoay[:3]]
    # top_n=1 thi chi con mot ma duoc tinh
    assert len(defensive_top(xoay, {**cfg, "top_n": 1})) == 1

    # --- DDL khop COLS ---
    db = Path(tempfile.mkdtemp()) / "s.db"
    c = con(db)
    have = {x[1] for x in c.execute("PRAGMA table_info(sector_rank)")}
    assert set(COLS) | {"d", "sym", "updated"} == have, have ^ set(COLS)

    # --- vong tron DB ---
    bars.save(c, {s: [(x.d, x.o, x.h, x.l, x.c, x.c, x.v) for x in v]
                  for s, v in ro.items()})
    bars.save(c, {config.BENCH: [(x.d, x.o, x.h, x.l, x.c, x.c, x.v)
                                 for x in _ser([(400.0, 460.0, 300)])]})

    res = build(c, cfg, dry=True)
    assert res["err"] is None and len(res["rows"]) == 11
    assert not dates(c), "--dry-run khong duoc ghi gi"

    res = build(c, cfg)
    assert res["err"] is None and res["d"] == ro["XLK"][-1].d
    assert len(load_rank(c)) == 11
    build(c, cfg)                                    # chay lai
    assert len(load_rank(c)) == 11, "khoa (d,sym) phai chan trung dong"
    assert load_rank(c)[0]["sym"] == "XLK"
    assert load_rank(c)[0]["rank"] == 1

    # --- nen quyet dinh khong dong nhat -> tu choi, va noi ten ma tre ---
    db2 = Path(tempfile.mkdtemp()) / "s2.db"
    c2 = con(db2)
    lech = {s: (v[:-1] if s == "XLRE" else v) for s, v in ro.items()}
    bars.save(c2, {s: [(x.d, x.o, x.h, x.l, x.c, x.c, x.v) for x in v]
                   for s, v in lech.items()})
    bad = build(c2, cfg)
    assert bad["err"] and "XLRE" in bad["err"] and not bad["rows"], bad["err"]
    assert not dates(c2), "tu choi thi khong duoc ghi nua ro"

    # thieu han mot sector -> loi khac, cung phai neu ten
    db3 = Path(tempfile.mkdtemp()) / "s3.db"
    c3 = con(db3)
    bars.save(c3, {s: [(x.d, x.o, x.h, x.l, x.c, x.c, x.v) for x in v]
                   for s, v in ro.items() if s != "XLE"})
    bad3 = build(c3, cfg)
    assert bad3["err"] and "XLE" in bad3["err"] and "1/11" in bad3["err"]
    c3.close()
    c2.close()

    # --- backfill + doi hang ---
    db4 = Path(tempfile.mkdtemp()) / "s4.db"
    c4 = con(db4)
    # XLE be bet 200 phien roi bung len 100 phien cuoi -> hang phai di LEN
    ro4 = dict(ro)
    ro4["XLE"] = _ser([(100.0, 70.0, 200), (70.0, 260.0, 100)])
    bars.save(c4, {s: [(x.d, x.o, x.h, x.l, x.c, x.c, x.v) for x in v]
                   for s, v in ro4.items()})
    bars.save(c4, {config.BENCH: [(x.d, x.o, x.h, x.l, x.c, x.c, x.v)
                                  for x in _ser([(400.0, 460.0, 300)])]})
    bf = backfill(c4, cfg, days=60, quiet=True)
    assert bf["err"] is None and bf["ok"] == 60, bf
    ds = dates(c4)
    assert len(ds) == 60 and ds == sorted(ds)

    chg = changes(c4, cfg=cfg)
    assert set(chg) == set(config.SECTOR_ETFS)
    assert chg["XLE"][5] is not None, "60 phien lich su -> phai so duoc 5 phien"
    # XLE dang leo len: hang moi <= hang cu -> delta >= 0 (duong = di len)
    assert chg["XLE"][21] >= 0, chg["XLE"]
    assert sum(v[21] for v in chg.values() if v[21] is not None) == 0, \
        "tong thay doi hang cua ca ro phai bang 0"

    # chua du lich su -> None, KHONG phai 0
    db5 = Path(tempfile.mkdtemp()) / "s5.db"
    c5 = con(db5)
    bars.save(c5, {s: [(x.d, x.o, x.h, x.l, x.c, x.c, x.v) for x in v]
                   for s, v in ro.items()})
    build(c5, cfg)
    c1 = changes(c5, cfg=cfg)
    assert all(v[5] is None and v[21] is None for v in c1.values()), \
        "mot phien duy nhat thi khong biet doi hang - phai la None"
    assert changes(c5, d="1999-01-01", cfg=cfg) == {}
    assert len(history(c5, 30)) == 11, "mot phien -> 11 dong"
    c5.close()

    # bang rong -> danh sach rong, khong phai loi
    db6 = Path(tempfile.mkdtemp()) / "s6.db"
    c6 = con(db6)
    assert history(c6, 30) == [] and dates(c6) == [] and load_rank(c6) == []
    assert changes(c6, cfg=cfg) == {}
    c6.close()

    # --- backfill khong nhin truoc tuong lai ---
    giua = ds[30]
    truoc = {r["sym"]: r["rank"] for r in load_rank(c4, giua)}
    lai = build(c4, cfg, dry=True, upto=giua)
    assert {r["sym"]: r["rank"] for r in lai["rows"]} == truoc, \
        "xep lai mot phien qua khu phai ra y nguyen ket qua cu"

    # --- lich su cho bieu do ---
    h = history(c4, 30)
    assert len(h) == 30 * 11
    assert set(h[0]) == {"d", "sym", "rank", "composite"}
    assert [x["d"] for x in h] == sorted(x["d"] for x in h)
    assert len(history(c4, 999)) == 60 * 11

    # --- panel chiu moi trang thai ---
    for w in ([], ["XLP"], ["XLP", "XLU"]):
        txt = panel(load_rank(c4), chg, w, cfg)
        assert "XEP HANG SECTOR" in txt and "TOP 3" in txt
        assert ("CANH BAO" in txt) == bool(w)
    assert "khong co du lieu" in panel([], {}, [], cfg)
    assert _arrow(None) == "  -" and _arrow(0) == "  =" and "^3" in _arrow(3)
    assert "v2" in _arrow(-2)
    assert _config_table()

    for x in (c, c4):
        x.close()
    print("sectors.py selftest: ok")


# ───────────────────────── CLI ─────────────────────────
if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                            # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(description="xep hang 11 sector SPDR")
    ap.add_argument("--build", action="store_true", help="ghi vao sector_rank")
    ap.add_argument("--dry-run", action="store_true",
                    help="tinh va in, KHONG ghi DB")
    ap.add_argument("--backfill", type=int, metavar="N",
                    help="dung lai lich su N phien gan nhat")
    ap.add_argument("--show", nargs="?", type=int, const=20, metavar="N",
                    help="lich su hang da luu")
    ap.add_argument("--config", action="store_true", help="in nguong dang chay")
    ap.add_argument("--db", default=str(DB), help="duong dan DB khac")
    a = ap.parse_args()
    db = Path(a.db)

    if a.config:
        print(_config_table())
        raise SystemExit(0)

    if a.show is not None:
        ds = dates(db, a.show)
        if not ds:
            print("sector_rank: chua co dong nao. Chay --build hoac --backfill.")
            raise SystemExit(2)
        print(f"{len(ds)} phien: {ds[0]} -> {ds[-1]}")
        for d in ds:
            rs = load_rank(db, d)
            print(f"  {d}  " + " ".join(f"{r['sym']}" for r in rs))
        raise SystemExit(0)

    if a.backfill is not None:
        bf = backfill(db, days=a.backfill, dry=a.dry_run)
        if bf["err"]:
            print(f"sectors: {bf['err']}")
            raise SystemExit(2)
        print(f"XONG: {bf['ok']} phien dung lai, {bf['skip']} bo qua, "
              f"{bf['trung']} khong co nen moi ({bf['dau']} -> {bf['cuoi']})"
              + ("  [--dry-run: khong ghi DB]" if a.dry_run else ""))
        for x in bf["loi"]:
            print(f"  bo qua {x}")
        raise SystemExit(0)

    if not (a.build or a.dry_run):
        _smoke()
        raise SystemExit(0)

    res = build(db, dry=a.dry_run)
    if res["err"]:
        print(f"sectors: {res['err']}")
        raise SystemExit(2)
    print(panel(res["rows"], changes(db, res["d"]), res["warn"]))
    if a.dry_run:
        print("\n(--dry-run: khong ghi DB)")
    raise SystemExit(0)
