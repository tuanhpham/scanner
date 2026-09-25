"""structure.py - Do cau truc gia theo nen NGAY. Phase 9.1.

Quan he voi setups.py giong quan he vprofile.py <-> scorer.py: file nay chi
DO, khong PHAN XET. Khong co nguong "the nao la nen tot" o day - no chi tra ve
"nen dai 34 phien, bien do 12%, pivot 10.05, volume can con 0.7 lan". Viec noi
"the la dat" thuoc setups.py, va se duoc backtest.py chinh bang so lieu.

Tach the vi mot ly do cu the: khi backtest cho biet nguong sai, ta muon sua
NGUONG ma khong phai sua lai cach do.

Thuan stdlib (doc kho nen cua bars.py qua sqlite3) -> chay va tu test duoc
tren may khong cai pandas/numpy.

    python structure.py                # selftest, khong can mang, khong can DB
    python structure.py --build        # tinh cho ca kho -> bang `struct`
    python structure.py --build --limit 300
    python structure.py --show NVDA    # xem so lieu mot ma
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import statistics
import sys
from bisect import bisect_left
from pathlib import Path

import bars
import config
from bars import Bar

ROOT = Path(__file__).resolve().parent
DB = ROOT / "state" / "baseline.db"

MIN_HIST = 60          # duoi 60 nen thi adv50/atr chua co y nghia
MIN_BASE, MAX_BASE = 20, 90
BASE_MAX_DEPTH = 0.35  # tran de find_base con noi rong; setups.py siet lai
# Nen that thi gia dao dong qua lai: bien do >> do troi. Kenh gia thi do troi
# xap xi bien do (20 phien dau va 20 phien cuoi khong con giao nhau). Nen do
# troi phai duoc do THEO bien do, khong phai bang mot con so % co dinh.
BASE_DRIFT_RATIO = 0.5
BASE_DRIFT_FLOOR = 0.02   # nen rat chat khong bi loai vi 1% troi
YEAR = 252             # so phien mot nam

log = print


# ───────────────────────── so hoc co ban ─────────────────────────
def _mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _sma(vals: list[float], n: int) -> list[float | None]:
    """Trung binh truot, cung do dai voi `vals`, chua du nen thi None."""
    out: list[float | None] = [None] * len(vals)
    if n <= 0 or len(vals) < n:
        return out
    s = sum(vals[:n])
    out[n - 1] = s / n
    for i in range(n, len(vals)):
        s += vals[i] - vals[i - n]
        out[i] = s / n
    return out


def _ema(vals: list[float], n: int) -> list[float | None]:
    """EMA, cung do dai voi `vals`, chua du nen thi None.

    Mam la SMA cua n nen dau (khong phai vals[0]): lay vals[0] lam mam thi
    nen dau tien co trong so qua lon va EMA lech trong ~3n nen dau - du de doi
    dau mot cau tra loi "gia tren hay duoi ema21".

    De o structure.py chu khong o sectors.py: tang nay la tang DO, va repo nay
    giu dung MOT dinh nghia cho moi chi bao (xem atr_last).
    """
    out: list[float | None] = [None] * len(vals)
    if n <= 0 or len(vals) < n:
        return out
    k = 2.0 / (n + 1.0)
    e = sum(vals[:n]) / n
    out[n - 1] = e
    for i in range(n, len(vals)):
        e = vals[i] * k + e * (1.0 - k)
        out[i] = e
    return out


def _tr(bs: list[Bar]) -> list[float]:
    """True range tung nen. Nen dau tien khong co prev_close -> dung h-l."""
    if not bs:
        return []
    out = [bs[0].h - bs[0].l]
    for i in range(1, len(bs)):
        pc = bs[i - 1].c
        out.append(max(bs[i].h - bs[i].l, abs(bs[i].h - pc), abs(bs[i].l - pc)))
    return out


def _atr(bs: list[Bar], n: int = 14) -> list[float | None]:
    """ATR = trung binh DON GIAN cua n true range - giong prep.compute().

    Co y giu giong prep.py: hai cho tinh ATR khac nhau thi so `atr_move` trong
    alert va so `atr_contract` o day se noi hai chuyen khac nhau ve cung mot ma.
    """
    return _sma(_tr(bs), n)


def atr_last(bs: list[Bar], n: int = 14) -> float | None:
    """ATR tai nen cuoi. prep.py dung ham nay de chi co MOT dinh nghia ATR."""
    a = _atr(bs, n)
    return a[-1] if a else None


def baseline(bs: list[Bar], min_adv: float = 200_000.0,
             min_px: float = 1.0) -> dict | None:
    """Ba con so cua bang `base`: adv20, atr14, prev_close. None = khong dat.

    Day la ban sao KHONG DUNG PANDAS cua prep.compute(), de prep.py --from-bars
    dung lai duoc kho nen thay vi tai lai toan bo tu yfinance. Dat o day chu
    khong o prep.py vi prep.py import pandas/yfinance ngay tu dau file -> khong
    the test tren may khong co thu vien.

    Goi voi bars.load(adj=False): prev_close phai khop quote live, gia da dieu
    chinh co tuc thi lech vai xu va lam chg% cua ma vua chia co tuc sai.
    """
    if len(bs) < 25:
        return None
    adv20 = _mean(b.v for b in bs[-20:])
    atr14 = atr_last(bs, 14)
    px = bs[-1].c
    if not atr14 or atr14 <= 0 or adv20 < min_adv or px < min_px:
        return None
    return {"adv20": adv20, "atr14": atr14, "prev_close": px}


def _slope(vals: list[float]) -> float:
    """Do doc hoi quy tuyen tinh, chuan hoa: %thay doi moi phien.

    Dung de biet nen dang PHANG (~0) hay dang truot xuong (<0). Chuan hoa theo
    gia trung binh de so sanh duoc giua ma $3 va ma $300.
    """
    n = len(vals)
    if n < 3:
        return 0.0
    mx = (n - 1) / 2.0
    my = _mean(vals)
    den = sum((i - mx) ** 2 for i in range(n))
    if not den or not my:
        return 0.0
    num = sum((i - mx) * (v - my) for i, v in enumerate(vals))
    return (num / den) / my


def _depth(bs: list[Bar]) -> float:
    """Bien do cua mot doan: (dinh - day) / dinh."""
    if not bs:
        return 1.0
    hi = max(b.h for b in bs)
    lo = min(b.l for b in bs)
    return (hi - lo) / hi if hi > 0 else 1.0


# ───────────────────────── tim nen tich luy ─────────────────────────
def _pref(cl: list[float]) -> tuple[list[float], list[float]]:
    """Tong tich luy cua c va i*c -> tinh do doc MOI cua so trong O(1)."""
    p = [0.0] * (len(cl) + 1)
    q = [0.0] * (len(cl) + 1)
    for i, v in enumerate(cl):
        p[i + 1] = p[i] + v
        q[i + 1] = q[i] + i * v
    return p, q


def _slope_win(p, q, a: int, b: int) -> float:
    """Do doc hoi quy (chuan hoa, %/phien) cua cl[a:b] tu tong tich luy."""
    L = b - a
    if L < 3:
        return 0.0
    sy = p[b] - p[a]
    sjy = (q[b] - q[a]) - a * sy
    mj = (L - 1) / 2.0
    den = L * (L * L - 1) / 12.0
    if not den or not sy:
        return 0.0
    return ((sjy - mj * sy) / den) / (sy / L)


def drift_cap(depth: float) -> float:
    """Do troi toi da cho phep voi mot nen co bien do `depth`."""
    return max(BASE_DRIFT_RATIO * depth, BASE_DRIFT_FLOOR)


def find_base(bs: list[Bar], max_depth: float = BASE_MAX_DEPTH,
              min_len: int = MIN_BASE, max_len: int = MAX_BASE) -> dict | None:
    """Doan tich luy TOT NHAT tinh tu nen cuoi tro ve. None = khong co nen.

    Hai dieu kien phai dat, va mot diem de chon giua cac doan hop le:

    - `depth` <= max_depth. Bien do khong bao gio giam khi cua so dai ra (dinh
      chi cao len, day chi thap di) nen gap cai dau tien vuot nguong la dung
      duoc: khong can quet 20..90 x 90 lan.
    - `drift` = |do doc| x do dai <= drift_cap(depth). Day la dieu kien QUAN
      TRONG nhat va la thu de bo sot nhat. Chi lay "doan dai nhat con duoi 35%"
      thi mot doan phang 45 phien di sau mot cu tang cham se bi bao thanh nen
      90 phien - nuot ca doan tang vao nen; con mot doan truot deu tu 30 xuong
      26 trong 90 phien cung se thanh "nen 13%". Nen tich luy phai PHANG.
    - Diem = (do dai) x (do chat), de doan dai hon chi thang khi khong rong
      hon dang ke. Chon "dai nhat" don thuan luon keo ve phia rong.

    QUAN TRONG: goi ham nay voi danh sach da BO nen cuoi cung (nen hom nay).
    Nen hom nay phai nam ngoai de "px > pivot" con y nghia; neu khong thi cu
    breakout hom nay tu nang pivot cua chinh no len va khong bao gio vuot.
    """
    n = len(bs)
    if n < min_len:
        return None
    cl = [b.c for b in bs]
    p, q = _pref(cl)

    hi = max(b.h for b in bs[-min_len:])
    lo = min(b.l for b in bs[-min_len:])
    if hi <= 0:
        return None

    best = None
    for L in range(min_len, min(max_len, n) + 1):
        if L > min_len:                 # noi rong cua so them mot nen ve truoc
            b = bs[-L]
            hi = max(hi, b.h)
            lo = min(lo, b.l)
        depth = (hi - lo) / hi if hi > 0 else 1.0
        if depth > max_depth:
            break
        slope = _slope_win(p, q, n - L, n)
        if abs(slope) * L > drift_cap(depth):
            continue                    # dang truot len/xuong, khong phai nen
        score = (L / max_len) * (1.0 - depth / max_depth)
        if best is None or score > best[0]:
            best = (score, L, depth, hi, lo, slope)

    if best is None:
        return None
    _, L, depth, hi, lo, slope = best
    return {"len": L, "depth": depth, "pivot": hi, "low": lo, "slope": slope,
            "i0": n - L, "vol": _mean(b.v for b in bs[-L:])}


# ───────────────────────── bo so lieu mot ma ─────────────────────────
def metrics(bs: list[Bar], max_depth: float = BASE_MAX_DEPTH) -> dict | None:
    """Do cau truc tai nen CUOI CUNG cua `bs`. None = chua du du lieu.

    `bs` phai den tu bars.load() (o/h/l khong None, xep tang dan theo ngay).
    Moi thu chi doc bs[:len] nen goi voi upto=<ngay> la co dung anh ngay do -
    day la cach backtest.py tranh nhin truoc tuong lai.
    """
    n = len(bs)
    if n < MIN_HIST:
        return None
    cl = [b.c for b in bs]
    vo = [b.v for b in bs]
    px = cl[-1]
    if px <= 0:
        return None
    today = bs[-1]

    s20, s50, s200 = _sma(cl, 20), _sma(cl, 50), _sma(cl, 200)
    atr = _atr(bs, 14)

    # sma50 dang huong len hay xuong - do tren 20 phien
    slope50 = None
    if s50[-1] and len(s50) > 21 and s50[-21]:
        slope50 = s50[-1] / s50[-21] - 1.0

    win = bs[-YEAR:] if n >= YEAR else bs
    hi52 = max(b.h for b in win)
    lo52 = min(b.l for b in win)

    # adv KHONG tinh nen hom nay: adv la thuoc do "binh thuong", con hom nay
    # la cai dang so voi binh thuong. Gop hom nay vao mau so lam RVOL nho lai
    # dung luc no can to.
    prev = vo[:-1]
    adv20 = _mean(prev[-20:])
    adv50 = _mean(prev[-50:])
    dryup = (_mean(prev[-5:]) / adv50) if adv50 else None

    atr14 = atr[-1]
    atr_pct = (atr14 / px) if atr14 else None
    atr_contract = None
    if atr14 and len(atr) > 51 and atr[-51]:
        atr_contract = atr14 / atr[-51]

    ret63 = (px / cl[-64] - 1.0) if (n >= 64 and cl[-64]) else None
    ret21 = (px / cl[-22] - 1.0) if (n >= 22 and cl[-22]) else None

    base = find_base(bs[:-1], max_depth)
    base_dryup = None
    if base:
        # Co ngot volume NGAY TRONG nen: 5 phien cuoi so voi ca nen.
        #
        # So voi adv50 (nhu `dryup` ben tren) khong dung viec o day: nen cang
        # dai va cang im lang thi adv50 cang thap theo, ty le tien ve 1 va
        # khong con noi len dieu gi. Con "5 phien cuoi im hon ca nen" thi dung
        # la dau hieu can truoc khi bat.
        body = vo[base["i0"]:n - 1]
        if len(body) >= 10 and _mean(body):
            base_dryup = _mean(body[-5:]) / _mean(body)

    rng = today.h - today.l
    below = 0
    for i in range(n - 1, -1, -1):
        if s20[i] is None or cl[i] >= s20[i]:
            break
        below += 1

    # bao lau roi ke tu day 52 tuan (lay lan xuat hien GAN NHAT)
    since_low = None
    for k in range(len(win) - 1, -1, -1):
        if win[k].l <= lo52:
            since_low = len(win) - 1 - k
            break

    chg = [cl[i] / cl[i - 1] - 1.0 for i in range(max(1, n - 10), n) if cl[i - 1]]

    return {
        "d": today.d, "px": px, "n_bars": n,
        "sma20": s20[-1], "sma50": s50[-1], "sma200": s200[-1],
        "sma50_slope": slope50,
        "hi52": hi52, "lo52": lo52,
        # Dinh/day cua CHINH nen quyet dinh. plan.trigger() dung hi1 lam moc
        # "vuot dinh hom qua" cho nhung ma khong co nen tich luy (pivot None).
        # Khong the lay tu hi52 - do la dinh cua ca nam.
        "hi1": today.h, "lo1": today.l,
        "off_high": (hi52 - px) / hi52 if hi52 else None,
        "up_from_low": px / lo52 - 1.0 if lo52 else None,
        "adv20": adv20, "adv50": adv50, "dryup": dryup,
        "atr14": atr14, "atr_pct": atr_pct, "atr_contract": atr_contract,
        "ret63": ret63, "ret21": ret21,
        # build() dien ba cot duoi sau khi biet ca ro / biet ma chuan.
        # rs_pct = percentile ret63 trong ca ro (so voi CAC MA KHAC).
        # rs21/rs63 = loi nhuan vuot troi so voi SPY (so voi THI TRUONG).
        # Hai khai niem khac nhau, dung lan nhau la mot loi im lang.
        "rs_pct": None, "rs21": None, "rs63": None,
        "base_len": base["len"] if base else 0,
        "base_depth": base["depth"] if base else None,
        "base_slope": base["slope"] if base else None,
        "base_dryup": base_dryup,
        "pivot": base["pivot"] if base else None,
        # >0 = con cach pivot bao nhieu %; <=0 = da o tren pivot
        "dist_pivot": (base["pivot"] / px - 1.0) if base else None,
        "depth20": _depth(bs[-20:]),
        "tight10": statistics.pstdev(chg) if len(chg) > 1 else None,
        "close_pos": ((today.c - today.l) / rng) if rng > 0 else None,
        "gap": (today.o / cl[-2] - 1.0) if (n >= 2 and cl[-2]) else None,
        "vol_ratio": (today.v / adv20) if adv20 else None,
        "below20_streak": below, "days_since_low": since_low,
    }


def rank_pct(vals: dict[str, float]) -> dict[str, float]:
    """Percentile 0-100 cua tung ma trong ca ro. Dong hang -> lay muc duoi."""
    xs = sorted(v for v in vals.values() if v is not None)
    if len(xs) < 2:
        return {k: 50.0 for k in vals}
    d = len(xs) - 1
    return {k: (100.0 * bisect_left(xs, v) / d) if v is not None else None
            for k, v in vals.items()}


# ───────────────────────── bang `struct` ─────────────────────────
COLS = ("d", "px", "n_bars", "sma20", "sma50", "sma200", "sma50_slope",
        "hi52", "lo52", "hi1", "lo1",
        "off_high", "up_from_low", "adv20", "adv50", "dryup",
        "atr14", "atr_pct", "atr_contract", "ret63", "ret21", "rs_pct",
        "rs21", "rs63", "base_len",
        "base_depth", "base_slope", "base_dryup", "pivot", "dist_pivot",
        "depth20", "tight10", "close_pos", "gap", "vol_ratio",
        "below20_streak", "days_since_low")

DDL = """
CREATE TABLE IF NOT EXISTS struct(
  sym TEXT PRIMARY KEY,
  d TEXT, px REAL, n_bars INTEGER,
  sma20 REAL, sma50 REAL, sma200 REAL, sma50_slope REAL,
  hi52 REAL, lo52 REAL, hi1 REAL, lo1 REAL,
  off_high REAL, up_from_low REAL,
  adv20 REAL, adv50 REAL, dryup REAL,
  atr14 REAL, atr_pct REAL, atr_contract REAL,
  ret63 REAL, ret21 REAL, rs_pct REAL, rs21 REAL, rs63 REAL,
  base_len INTEGER, base_depth REAL, base_slope REAL, base_dryup REAL,
  pivot REAL, dist_pivot REAL, depth20 REAL, tight10 REAL,
  close_pos REAL, gap REAL, vol_ratio REAL,
  below20_streak INTEGER, days_since_low INTEGER,
  updated TEXT);
CREATE INDEX IF NOT EXISTS ix_struct_base ON struct(base_len);
"""


def _migrate(c: sqlite3.Connection) -> bool:
    """Bang `struct` thieu cot so voi COLS -> XOA va tao lai. Tra ve co xoa hay khong.

    `CREATE TABLE IF NOT EXISTS` khong them cot vao bang da ton tai, nen khi
    COLS dai ra (them ret21/rs21/rs63 cho Stage 3) thi DB cu tren VM se vo o
    cau SELECT dau tien - luc 08:00, giua chuoi cron.

    Xoa la an toan: bang nay la BAN TINH LAI hoan toan tu `bars`, va build() da
    ghi de ca bang moi lan chay. Khong mat du lieu goc nao. Lam tu dong chu
    khong de trong README vi mot buoc thu cong se bi quen dung mot lan.
    """
    have = {x[1] for x in c.execute("PRAGMA table_info(struct)")}
    if not have or have >= set(COLS) | {"sym", "updated"}:
        return False
    log(f"  [structure] bang `struct` thieu cot "
        f"{sorted(set(COLS) - have)} -> xoa va tinh lai tu `bars`")
    with c:
        c.execute("DROP TABLE struct")
    c.executescript(DDL)
    return True


def con(db=DB) -> sqlite3.Connection:
    c = bars.con(db) if not isinstance(db, sqlite3.Connection) else db
    c.executescript(DDL)
    _migrate(c)
    return c


def build(db=DB, limit: int = 0, min_bars: int = MIN_HIST) -> dict:
    """Tinh cau truc cho moi ma trong kho nen, ghi de bang `struct`.

    Ghi de toan bang trong MOT transaction: bang nay la anh chup cua mot phien,
    tron dong cua hom nay voi dong cua hom qua thi setups.py se so hai ma o hai
    thoi diem khac nhau ma khong biet.
    """
    c = con(db)
    sl = bars.syms(c, min_rows=min_bars)
    if limit:
        sl = sl[:limit]

    out: dict[str, dict] = {}
    bad = 0
    for s in sl:
        try:
            m = metrics(bars.load(c, s))
        except Exception as e:                                   # noqa: BLE001
            log(f"  [structure] {s}: {type(e).__name__}: {e}")
            m = None
            bad += 1
        if m:
            out[s] = m

    r = rank_pct({s: m["ret63"] for s, m in out.items()})
    for s, m in out.items():
        m["rs_pct"] = r.get(s)

    # Suc manh tuong doi so voi MA CHUAN (khac rs_pct - xem chu thich trong
    # metrics). Tinh o day chu khong trong metrics() vi metrics() chi biet mot
    # ma; con phep so nay can ca SPY.
    #
    # Thieu SPY -> rs21/rs63 = None, va lead_candidate() loai het. Do la co y:
    # "khong biet co manh hon thi truong hay khong" khong bao gio duoc coi la
    # "co". Bao thanh mot dong log de con tim ra, thay vi de Stage 3 tra ve
    # danh sach rong ma khong noi ly do.
    bench = out.get(config.BENCH)
    if not bench:
        log(f"  [structure] khong co {config.BENCH} trong kho nen -> rs21/rs63 "
            f"= NULL, profile LEAD se khong chon duoc ma nao. Chay "
            f"`python bars.py --sync --full`.")
    else:
        for w in (21, 63):
            b = bench.get(f"ret{w}")
            if b is None:
                log(f"  [structure] {config.BENCH} chua du nen cho ret{w} "
                    f"-> rs{w} = NULL")
                continue
            for m in out.values():
                mine = m.get(f"ret{w}")
                if mine is not None:
                    m[f"rs{w}"] = mine - b

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    ins = (f"INSERT INTO struct(sym,{','.join(COLS)},updated) "
           f"VALUES({','.join('?' * (len(COLS) + 2))})")
    with c:
        c.execute("DELETE FROM struct")
        c.executemany(ins, [(s, *(m[k] for k in COLS), now)
                            for s, m in out.items()])
    if not isinstance(db, sqlite3.Connection):
        c.close()
    return {"da_xet": len(sl), "co_so_lieu": len(out), "loi": bad,
            "co_nen": sum(1 for m in out.values() if m["base_len"] >= MIN_BASE)}


def load_struct(db=DB, syms: list[str] | None = None) -> dict[str, dict]:
    c, mine = bars._c(db)
    try:
        c.executescript(DDL)
        # Bang cu (thieu cot) -> xoa va tra ve rong, thay vi vo o SELECT. Rong
        # thi setups.py ghi 0 candidate va MAX_AGE se bao "du lieu cu"; con vo
        # thi ca chuoi cron dung. Lan build() tiep theo dien lai day du.
        _migrate(c)
        q = f"SELECT sym,{','.join(COLS)} FROM struct"
        if syms:
            q += f" WHERE sym IN ({','.join('?' * len(syms))})"
            rows = c.execute(q, syms).fetchall()
        else:
            rows = c.execute(q).fetchall()
    finally:
        if mine:
            c.close()
    return {r[0]: dict(zip(COLS, r[1:])) for r in rows}


# ───────────────────────── selftest ─────────────────────────
def _series(specs: list[tuple[float, float, int]], start: str = "2024-01-02",
            vol: float = 1_000_000.0) -> list[Bar]:
    """Chuoi nen tong hop: moi spec = (gia dau, gia cuoi, so phien), noi tiep.

    Bien do trong ngay co dinh +-0.5% de _depth() va close_pos co so that.
    """
    out: list[Bar] = []
    d = dt.date.fromisoformat(start)
    for p0, p1, n in specs:
        for i in range(n):
            p = p0 + (p1 - p0) * (i / max(n - 1, 1))
            out.append(Bar(d.isoformat(), p, p * 1.005, p * 0.995, p, vol))
            d += dt.timedelta(days=1)
    return out


def _smoke() -> None:
    import tempfile

    # --- _ema ---
    assert _ema([1.0] * 5, 10) == [None] * 5, "chua du nen -> None het"
    flat = _ema([7.0] * 30, 10)
    assert flat[8] is None and abs(flat[9] - 7.0) < 1e-9
    assert all(abs(v - 7.0) < 1e-9 for v in flat[9:]), "chuoi phang -> ema phang"
    ramp = [float(i) for i in range(1, 61)]
    up = _ema(ramp, 21)
    assert up[-1] < ramp[-1], "EMA phai tre sau gia trong xu huong tang"
    # Tren duong tang DEU, EMA va SMA cung ky tre nhu nhau ((n-1)/2 = (1-k)/k
    # = 10 phien) -> gan bang nhau. Cho nen phep thu "EMA nhanh hon SMA" phai
    # dung mot cu NHAY, khong phai mot doan tang deu.
    assert abs(up[-1] - _sma(ramp, 21)[-1]) < 0.5
    nhay = [10.0] * 40 + [20.0] * 5
    assert _ema(nhay, 21)[-1] > _sma(nhay, 21)[-1], "EMA phai bat cu nhay nhanh hon"

    # --- find_base: nen phang 45 phien di sau mot cu tang 50% ---
    bs = _series([(20.0, 30.0, 60), (30.0, 30.0, 45)])
    b = find_base(bs)
    # Khong doi dung 45: cua so lan sang vai nen cuoi cua doan tang thi van
    # phang va van tinh la nen. Dieu phai dat la KHONG nuot ca doan tang.
    assert b and 40 <= b["len"] <= 58, f"len={b and b['len']}"
    assert b["depth"] < 0.08
    assert abs(b["slope"]) * b["len"] <= drift_cap(b["depth"])
    assert abs(b["pivot"] - 30.0 * 1.005) < 1e-6, "pivot = dinh cao nhat cua nen"

    # do dai bi chan tren, va doan dang truot thi khong phai nen
    assert find_base(_series([(10.0, 10.0, 200)]))["len"] == MAX_BASE
    assert find_base(_series([(10.0, 30.0, 60)])) is None, "tang 200% khong la nen"
    assert find_base(_series([(30.0, 26.0, 90)])) is None, "truot xuong 13%"
    assert find_base(_series([(10.0, 10.0, 10)])) is None, "ngan hon min_len"

    # --- metrics: breakout khoi nen ---
    bo = _series([(8.0, 10.0, 80), (10.0, 10.0, 40)])
    bo.append(Bar("2024-06-01", 10.05, 10.9, 10.0, 10.85, 3_200_000.0))
    m = metrics(bo)
    assert m and m["base_len"] >= 38, m["base_len"]
    assert m["dist_pivot"] < 0, "gia dong cua tren pivot -> dist_pivot am"
    assert 3.0 < m["vol_ratio"] < 3.4, m["vol_ratio"]
    assert m["close_pos"] > 0.9, "dong cua sat dinh ngay"
    assert m["off_high"] < 0.01 and m["below20_streak"] == 0
    assert m["px"] > m["sma20"] > 0 and m["rs_pct"] is None

    # pivot phai tinh tu nen TRUOC do, khong tu nen hom nay
    assert m["pivot"] < bo[-1].h, "nen hom nay bi lot vao trong nen tich luy"

    # --- metrics: roi tham roi bat lai ---
    dn = _series([(100.0, 25.0, 150), (25.0, 26.0, 10)])
    m2 = metrics(dn)
    assert m2 and m2["off_high"] > 0.7, m2["off_high"]
    assert m2["up_from_low"] < 0.1
    assert m2["days_since_low"] is not None and m2["days_since_low"] <= 12
    assert m2["ret63"] < 0 and m2["atr_contract"] is not None

    # dang roi lien tuc -> chuoi ngay duoi sma20 phai dai
    m3 = metrics(_series([(100.0, 25.0, 160)]))
    assert m3["below20_streak"] > 30, m3["below20_streak"]
    assert m3["base_len"] == 0 and m3["pivot"] is None

    # --- thieu du lieu ---
    assert metrics(_series([(10.0, 10.0, MIN_HIST - 1)])) is None

    # --- volume can ---
    quiet = _series([(9.0, 10.0, 60)], vol=5e6) + _series(
        [(10.0, 10.0, 40)], start="2024-04-01", vol=1e6)
    mq = metrics(quiet)
    assert mq["base_dryup"] is not None and mq["base_dryup"] < 0.7, mq["base_dryup"]
    assert mq["dryup"] < 0.7, mq["dryup"]
    # Nen im lang tu dau den cuoi -> KHONG co co ngot, ca hai ty le ve ~1.
    # Do la ket qua dung, va la ly do setups.py khong duoc coi dryup thap la
    # dieu kien bat buoc cua BO.
    flat = metrics(_series([(10.0, 10.0, 100)], vol=1e6))
    assert 0.9 < flat["base_dryup"] < 1.1 and 0.9 < flat["dryup"] < 1.1

    # --- rank_pct ---
    r = rank_pct({"A": 0.5, "B": -0.1, "C": 0.2})
    assert r["B"] == 0.0 and r["A"] == 100.0 and 40 < r["C"] < 60
    assert rank_pct({"A": 1.0})["A"] == 50.0, "mot ma thi khong xep hang duoc"

    # --- vong tron DB: COLS phai khop DDL, build() phai chay ---
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    c = con(tmp)
    have = [r[1] for r in c.execute("PRAGMA table_info(struct)")]
    assert set(COLS) | {"sym", "updated"} == set(have), (
        f"COLS lech DDL: {set(COLS) ^ (set(have) - {'sym', 'updated'})}")

    rows = [(b.d, b.o, b.h, b.l, b.c, b.c, b.v) for b in bo]
    bars.save(c, {"BOO": rows, "DIP": [(b.d, b.o, b.h, b.l, b.c, b.c, b.v)
                                       for b in dn]})
    st = build(c)
    assert st["co_so_lieu"] == 2 and st["co_nen"] == 1, st
    got = load_struct(c)
    assert abs(got["BOO"]["pivot"] - m["pivot"]) < 1e-9
    assert got["BOO"]["rs_pct"] is not None, "rs_pct phai duoc dien khi build"
    assert got["DIP"]["base_len"] == 0
    c.close()
    print("structure.py selftest: ok")


# ───────────────────────── CLI ─────────────────────────
def _fmt(v, w=8, p=2) -> str:
    return f"{v:>{w}.{p}f}" if isinstance(v, (int, float)) else f"{'-':>{w}}"


def _show(sym: str, db=None) -> None:
    db = DB if db is None else db
    m = load_struct(db, [sym]).get(sym)
    if not m:
        bs = bars.load(db, sym)
        m = metrics(bs)
        print(f"(chua co trong bang struct, tinh truc tiep tu {len(bs)} nen)")
    if not m:
        print(f"{sym}: khong du du lieu")
        return
    print(f"\n{sym}  ({m['d']})")
    for k in COLS:
        if k == "d":
            continue
        v = m[k]
        print(f"  {k:<16}{_fmt(v, 12, 4)}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                            # noqa: BLE001
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show", metavar="SYM")
    # Giong regime.py/sectors.py: chay thu tren mot ban copy cua kho nen ma
    # khong cham vao state/baseline.db that.
    ap.add_argument("--db", default=str(DB))
    a = ap.parse_args()
    DB = Path(a.db)

    if a.show:
        _show(a.show.upper(), db=DB)
        raise SystemExit(0)

    if not a.build:
        _smoke()
        raise SystemExit(0)

    t0 = dt.datetime.now()
    st = build(DB, limit=a.limit)
    print(f"struct: {st['co_so_lieu']}/{st['da_xet']} ma co so lieu, "
          f"{st['co_nen']} ma dang trong nen tich luy, {st['loi']} loi "
          f"({(dt.datetime.now() - t0).total_seconds():.0f}s)")

    d = load_struct(DB)
    near = [(s, m) for s, m in d.items()
            if m["base_len"] >= MIN_BASE and m["dist_pivot"] is not None
            and -0.02 <= m["dist_pivot"] <= 0.04
            and (m["off_high"] or 1) <= 0.30]
    near.sort(key=lambda t: (t[1]["dist_pivot"], -t[1]["base_len"]))
    print(f"\nSAP / VUA VUOT PIVOT: {len(near)} ma\n")
    print(f"{'SYM':<7}{'GIA':>9}{'PIVOT':>9}{'CACH':>8}{'NEN':>5}"
          f"{'SAU':>7}{'CAN':>7}{'ATRc':>7}{'RS':>6}")
    for s, m in near[:25]:
        print(f"{s:<7}{_fmt(m['px'], 9)}{_fmt(m['pivot'], 9)}"
              f"{(m['dist_pivot'] or 0) * 100:>7.1f}%{m['base_len']:>5}"
              f"{(m['base_depth'] or 0) * 100:>6.0f}%{_fmt(m['dryup'], 7)}"
              f"{_fmt(m['atr_contract'], 7)}{_fmt(m['rs_pct'], 6, 0)}")
