"""Tao kho nen TONG HOP de xem ca chuoi swing chay, khong can doi sync 3 nam.

Khong phai du lieu that, va khong dung de danh gia gi ca: moi chuoi gia la mot
duong thang co chu dinh, de mot ma pha DUNG MOT san chat luong. Muc dich duy
nhat la doc duoc bang "ly do bi loai" cua setups.py va biet tung san co that su
chan duoc cai no phai chan.

    python scripts/demo_stage3.py
    python regime.py  --dry-run --db state/demo_stage3.db
    python sectors.py --build   --db state/demo_stage3.db
    python structure.py --build --db state/demo_stage3.db
    python setups.py  --dry-run --db state/demo_stage3.db
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bars        # noqa: E402
import config      # noqa: E402
import holdings    # noqa: E402

DB = ROOT / "state" / "demo_stage3.db"
N = 400


def ser(p0: float, p1: float, n: int, atr: float, dvol: float,
        last_mult: float = 1.0, start: str = "2024-01-02"):
    """n nen tuyen tinh p0 -> p1, bien do `atr` (ti le gia), tien/phien `dvol`."""
    out = []
    d = dt.date.fromisoformat(start)
    for i in range(n):
        p = p0 + (p1 - p0) * (i / max(n - 1, 1))
        v = dvol / p
        if i == n - 1:
            v *= last_mult
        out.append((d.isoformat(), p, p * (1 + atr / 2), p * (1 - atr / 2),
                    p, p, round(v)))
        d += dt.timedelta(days=1)
    return out


def noi(*segs):
    """Noi nhieu doan lai, moi doan (p0, p1, n, atr, dvol, last_mult)."""
    out: list = []
    d = dt.date.fromisoformat("2024-01-02")
    for p0, p1, n, atr, dvol, lm in segs:
        s = ser(p0, p1, n, atr, dvol, lm, start=d.isoformat())
        out += s
        d = dt.date.fromisoformat(s[-1][0]) + dt.timedelta(days=1)
    return out


# ───────── ho so: moi ho so pha DUNG MOT san, de doc bang rejects ─────────
def dan_dat(manh: float):
    """Ma dan dat that: gia $60-ish, $200M/phien, bien do 3%, rvol 2x."""
    return ser(100.0, 100.0 * manh, N, 0.030, 200e6, 2.0)


HO_SO = {
    "gia_thap":    lambda: ser(6.0, 9.0, N, 0.030, 200e6, 2.0),
    "mong":        lambda: ser(100.0, 140.0, N, 0.030, 8e6, 2.0),
    "qua_dong":    lambda: ser(100.0, 140.0, N, 0.090, 200e6, 2.0),
    "qua_yen":     lambda: ser(100.0, 140.0, N, 0.012, 200e6, 2.0),
    "rvol_thap":   lambda: ser(100.0, 140.0, N, 0.030, 200e6, 1.0),
    "yeu_hon_spy": lambda: ser(100.0, 101.0, N, 0.030, 200e6, 2.0),
    # tang manh, roi 25% tu dinh tu lau, nay dang bo len lai: van manh hon SPY
    # ca 21 va 63 phien nhung KHONG con o gan dinh 52 tuan
    "cach_dinh":   lambda: noi((100.0, 200.0, 250, 0.030, 200e6, 1.0),
                               (200.0, 140.0, 60, 0.030, 200e6, 1.0),
                               (140.0, 150.0, 90, 0.030, 200e6, 2.0)),
}
THU_TU = ["gia_thap", "mong", "qua_dong", "qua_yen", "rvol_thap", "cach_dinh",
          "yeu_hon_spy"]


def main() -> int:
    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        DB.unlink()
    c = bars.con(DB)
    data: dict[str, list] = {}

    # SPY: +10% trong 400 phien, xu huong tang, bien do binh thuong
    data[config.BENCH] = ser(400.0, 440.0, N, 0.010, 40e9)

    # 11 sector: XLK > XLY > XLF > ... ; XLP/XLU yeu nhat (khong canh bao)
    manh = {"XLK": 1.55, "XLY": 1.42, "XLF": 1.30, "XLI": 1.18, "XLC": 1.14,
            "XLV": 1.10, "XLE": 1.08, "XLB": 1.06, "XLRE": 1.04,
            "XLP": 1.02, "XLU": 1.01}
    for s, m in manh.items():
        data[s] = ser(100.0, 100.0 * m, N, 0.012, 1e9)

    h = holdings.load()
    top3 = ["XLK", "XLY", "XLF"]
    ghi_chu: list[str] = []
    for sec in config.SECTOR_ETFS:
        syms = sorted(h["by_sector"][sec])
        if sec in top3:
            # 6 ma dan dat that (de thay tran 5/sector), 7 ma pha 7 san khac
            # nhau, con lai yeu hon SPY
            for i, sym in enumerate(syms[:6]):
                data[sym] = dan_dat(1.60 - i * 0.04)
            for sym, k in zip(syms[6:13], THU_TU):
                data[sym] = HO_SO[k]()
                ghi_chu.append(f"{sym:<6}{sec:<6}{k}")
            for sym in syms[13:]:
                data[sym] = HO_SO["yeu_hon_spy"]()
        else:
            # Ma manh o sector NGOAI top 3: phai bi loai vi sector, khong vi
            # chat luong. Day la cot loi cua Stage 3.
            for sym in syms[:3]:
                data[sym] = dan_dat(1.70)

    bars.save(c, data)
    c.close()
    print(f"{DB}: {len(data)} ma x {N} nen")
    print(f"top 3 sector du kien: {' '.join(top3)}")
    print("\nMa co y pha tung san (de doc bang rejects):")
    print(f"{'MA':<6}{'SECTOR':<6}SAN BI PHA")
    for x in ghi_chu:
        print("  " + x)
    return 0


if __name__ == "__main__":
    sys.exit(main())
