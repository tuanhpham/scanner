"""quotes.py - bao gia trong phien cho DUNG cac ma trong danh sach.

TANG DU LIEU NAM SAU MOT GIAO DIEN (yeu cau khong thuong luong cua prompt 1).
Phan canh bao khong duoc biet no dang noi voi yfinance, voi Alpaca, hay voi mot
file fixture. No goi:

    q = quotes.get_provider()          # chon theo env QUOTE_SRC
    frame = q.fetch(["NVDA", "AAPL"])  # {sym: bao gia}

MOI BAO GIA TU MANG THEO DAU MOC VA DO TRE CUA CHINH NO. Do la yeu cau ro rang
cua prompt 2 ("stamp every Telegram alert with quote timestamp and delay"), va
ly do that su thi nghiem tuc hon mot dong chu trong tin nhan: nguon mien phi tre
~15 phut. Mot canh bao "gia vua cham 51.0" ma khong noi ro con so do la cua 15
phut truoc se lam minh dat lenh o mot muc da khong con ton tai. Bot khong the
xoa do tre di - no chi co the noi that ve do tre, va do la thu duy nhat dung dan
o day.

HAI NUA, VA RANH GIOI GIUA CHUNG LA CO Y
----------------------------------------
    _rows_from_yf(df, syms)   mong nhat co the, cham vao pandas/yfinance
    build(rows, now)          THUAN: gop nen phut -> bao gia, tinh do tre

Toan bo phan de sai nam o `build()`, va `build()` chay duoc tren may khong co
pandas. Cach chia nay la co y: may dev khong cai duoc yfinance, nen neu logic
gop nen nam trong ham cham DataFrame thi no khong bao gio duoc kiem truoc khi
len VM.

KHOI LUONG PHAI LA KHOI LUONG HOP NHAT (consolidated)
-----------------------------------------------------
⚠️ Cai bay dat nhat cua ca file. RVol = khoi luong hom nay / ky vong tu adv50,
va adv50 trong bang `base` la khoi luong HOP NHAT tu ca thi truong. Neu nguon
bao gia chi tra ve khoi luong cua MOT san (Alpaca goi mien phi tra feed IEX,
chiem ~2% khoi luong), thi RVol tinh ra se luon ~0.02 va nguong 1.5 KHONG BAO
GIO kich. Mot bo loc khong bao gio kich khong bao loi - no chi im lang.
Nen moi bao gia mang co `vol_ok`: False nghia la "co gia nhung khong duoc dung
de tinh RVol", va phan canh bao phai coi RVol la KHONG BIET chu khong phai 0.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ET = "America/New_York"

# Nen 1 phut la don vi nho nhat cac nguon mien phi cho. Dau moc cua mot nen la
# THOI DIEM BAT DAU no, nen mot nen "15:30" phu 15:30-15:31: tuoi tinh tu dau
# nen se NOI QUA do tre toi 60 giay. Do la huong an toan va giu nguyen co y.
BAR_SEC = 60

_ET: object | None = None
_ET_ERR: str | None = None


def _et():
    """Mui gio New York, lay MUON va khong bao gio nem loi. Giong nightly._et().

    zoneinfo la stdlib nhung BANG DU LIEU mui gio thi khong: Windows can goi
    `tzdata`. Thieu thi lui ve UTC va NOI RA (tz_warn), khong bao gio cung mot
    offset co dinh -4/-5 gio.
    """
    global _ET, _ET_ERR
    if _ET is None and _ET_ERR is None:
        try:
            from zoneinfo import ZoneInfo
            _ET = ZoneInfo(ET)
        except Exception as e:                       # noqa: BLE001
            _ET_ERR = f"{type(e).__name__}: {e}"
    return _ET


def tz_warn() -> list[str]:
    """Cau canh bao cho tin nhan mo phien. Rong khi moi thu binh thuong.

    Khong co bang mui gio thi nen KHONG co mui gio bi doc thanh UTC, tuc la do
    tre lech 4-5 gio - va do tre la thu quyet dinh mot bao gia co duoc dung hay
    khong. Im lang o day nghia la ca phien khong canh gi ca ma khong ai biet vi
    sao. (yfinance thuc te tra ve dau moc CO mui gio, nen day la luoi thu hai.)
    """
    if _et() is not None:
        return []
    return [f"Không đọc được múi giờ {ET} ({_ET_ERR}) — nến không có múi giờ sẽ "
            "bị đọc thành UTC, tức là độ trễ lệch 4–5 giờ"]


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def _iso(t) -> str | None:
    """Dau moc -> ISO co mui gio. Nhan datetime, so epoch, hoac chuoi ISO."""
    if isinstance(t, dt.datetime):
        d = t if t.tzinfo else t.replace(tzinfo=dt.UTC)
        return d.isoformat(timespec="seconds")
    v = _num(t)
    if v is not None:
        # Epoch giay hoac milli-giay: > 1e11 thi chac chan la milli.
        return dt.datetime.fromtimestamp(v / 1000 if v > 1e11 else v,
                                         dt.UTC).isoformat(timespec="seconds")
    s = str(t or "").strip()
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        # Nen khong co mui gio thi la gio ET: do la mui gio cua san giao dich,
        # va doan sang UTC se lam do tre lech 4-5 gio roi moi canh bao nao cung
        # bi coi la "qua cu". Thieu bang mui gio thi lui ve UTC - tz_warn() la
        # cho noi ra chuyen do.
        d = d.replace(tzinfo=_et() or dt.UTC)
    return d.isoformat(timespec="seconds")


def build(rows, now: dt.datetime | None = None, src: str = "?",
          vol_ok: bool = True) -> dict:
    """Gop nen phut thanh mot bao gia moi ma. HAM THUAN, khong mang.

    `rows`: lap duoc cua dict {"sym","ts","o","h","l","c","v"} - `h`/`l` bo
    duoc. Thu tu khong quan trong: xep theo `ts` o day, vi yfinance tra ve theo
    nhom ma chu khong theo thoi gian khi hoi nhieu ma.

    Tra ve {sym: {sym, px, open, hi, lo, vol, ts, age_sec, n_bars, src, vol_ok,
                  err}}.
    `err` khac None nghia la co ma nhung khong co du lieu dung duoc - va do la
    mot trang thai PHAI di vao tin nhan, khong phai mot dong trong DataFrame bi
    bo qua.
    """
    now = now or dt.datetime.now(dt.UTC)
    gom: dict[str, list[dict]] = {}
    for r in rows or ():
        if not isinstance(r, dict):
            continue
        sym = str(r.get("sym") or "").strip().upper()
        ts = _iso(r.get("ts"))
        c = _num(r.get("c"))
        if not sym or not ts or c is None or c <= 0:
            continue
        gom.setdefault(sym, []).append({"ts": ts, "o": _num(r.get("o")),
                                        "h": _num(r.get("h")),
                                        "l": _num(r.get("l")), "c": c,
                                        "v": _num(r.get("v")) or 0.0})

    out: dict[str, dict] = {}
    for sym, bars in gom.items():
        bars.sort(key=lambda b: b["ts"])
        cuoi = bars[-1]
        t = dt.datetime.fromisoformat(cuoi["ts"])
        his = [b["h"] for b in bars if b["h"] is not None] or [b["c"] for b in bars]
        los = [b["l"] for b in bars if b["l"] is not None] or [b["c"] for b in bars]
        out[sym] = {
            "sym": sym,
            "px": cuoi["c"],
            "open": bars[0]["o"] if bars[0]["o"] is not None else bars[0]["c"],
            "hi": max(his), "lo": min(los),
            "vol": sum(b["v"] for b in bars),
            "ts": cuoi["ts"],
            # Toi da voi 0: dong ho lech khong duoc thanh do tre am, va mot do
            # tre am se lam moi kiem tra "du moi de canh" thanh vo dung.
            "age_sec": max(0.0, (now - t).total_seconds()),
            "n_bars": len(bars), "src": src, "vol_ok": vol_ok, "err": None,
        }
    return out


def missing(frame: dict, syms, src: str = "?") -> dict:
    """Bo sung dong `err` cho cac ma hoi ma khong co du lieu.

    Ton tai vi mot ma VANG MAT khong duoc phep giong mot ma "chua dat dieu
    kien". Ma bi halt, ma sai chinh ta, ma nguon bo qua - ca ba phai hien ra.
    """
    out = dict(frame)
    for s in syms or ():
        s = str(s or "").strip().upper()
        if s and s not in out:
            out[s] = {"sym": s, "px": None, "open": None, "hi": None,
                      "lo": None, "vol": None, "ts": None, "age_sec": None,
                      "n_bars": 0, "src": src, "vol_ok": False,
                      "err": "không có dữ liệu trong phiên"}
    return out


def fresh(q: dict, max_age_sec: float) -> bool:
    """Bao gia con du moi de dung. Khong co dau moc -> KHONG du moi.

    Khong co `ts` la truong hop de bo sot nhat: mot nguon tra gia ma khong tra
    thoi gian van "co gia", va neu mac dinh la du moi thi bot se canh bao theo
    mot con so khong biet cua luc nao.
    """
    if not q or q.get("err") or q.get("px") is None:
        return False
    a = q.get("age_sec")
    return a is not None and a <= max_age_sec


def fmt_age(sec) -> str:
    """Do tre thanh cau tieng Viet ngan, de dan vao moi tin nhan."""
    v = _num(sec)
    if v is None:
        return "không rõ độ trễ"
    if v < 90:
        return f"trễ {v:.0f} giây"
    return f"trễ {v / 60:.0f} phút"


# ───────────────────────── cac nguon ─────────────────────────
class Provider:
    """Giao dien. Ba dong, va do la toan bo hop dong."""

    name = "?"
    vol_ok = True

    def fetch(self, syms) -> dict:
        raise NotImplementedError


class YFProvider(Provider):
    """yfinance, nen 1 phut. Khoi luong HOP NHAT, tre ~15 phut.

    Mac dinh vi no khong can khoa API nao, va vi do tre 15 phut la dieu chap
    nhan duoc voi cach dung o day: ke hoach lenh duoc chot tu dem truoc, nen
    viec can biet la "gia da cham muc nay chua", khong phai "gia dang la bao
    nhieu ngay giay nay".
    """

    name = "yf"

    def fetch(self, syms) -> dict:
        syms = [str(s).strip().upper() for s in syms if str(s or "").strip()]
        if not syms:
            return {}
        rows, err = _rows_from_yf(syms)
        if err:
            # Mot loi mang khong duoc bien thanh "khong ma nao co du lieu" mot
            # cach im lang: moi ma nhan dong `err` cua rieng no.
            out = {}
            for s in syms:
                out[s] = {"sym": s, "px": None, "open": None, "hi": None,
                          "lo": None, "vol": None, "ts": None,
                          "age_sec": None, "n_bars": 0, "src": self.name,
                          "vol_ok": False, "err": err}
            return out
        return missing(build(rows, src=self.name, vol_ok=self.vol_ok), syms,
                       self.name)


def _rows_from_yf(syms: list[str]) -> tuple[list[dict], str | None]:
    """Cham vao yfinance + pandas. Giu mong nhat co the - moi thu kho o `build`.

    Tra (rows, err). Import ben trong ham de may khong co yfinance van nap
    duoc module nay.
    """
    try:
        import yfinance as yf
    except Exception as e:                           # noqa: BLE001
        return [], f"không nạp được yfinance ({type(e).__name__})"
    try:
        df = yf.download(syms, period="1d", interval="1m", prepost=False,
                         progress=False, auto_adjust=False, threads=False)
    except Exception as e:                           # noqa: BLE001
        return [], f"không lấy được nến 1 phút ({type(e).__name__}: {e})"
    if df is None or getattr(df, "empty", True):
        return [], "chưa có nến 1 phút nào cho phiên hôm nay"

    rows: list[dict] = []
    nhieu = hasattr(df.columns, "levels") and len(df.columns.levels) > 1
    for ts, r in df.iterrows():
        for s in syms:
            try:
                o, h, l, c, v = ((r[("Open", s)], r[("High", s)], r[("Low", s)],
                                  r[("Close", s)], r[("Volume", s)]) if nhieu
                                 else (r["Open"], r["High"], r["Low"],
                                       r["Close"], r["Volume"]))
            except (KeyError, IndexError):
                continue
            if _num(c) is None:
                continue                              # nen trong: bo qua
            rows.append({"sym": s, "ts": ts.to_pydatetime() if hasattr(
                ts, "to_pydatetime") else ts, "o": o, "h": h, "l": l,
                "c": c, "v": v})
            if not nhieu:
                break
    return rows, None


class FixtureProvider(Provider):
    """Doc mot chuoi bao gia tu file JSON. Khong mang.

    Dung cho test va cho `--replay`: yeu cau cua prompt 2 la test phai chay
    duoc voi "fixture quote streams, no live network". Moi lan fetch() tra ve
    mot khung ke tiep; het khung thi giu khung cuoi (de vong lap khong tu nhien
    thay doi hanh vi o cuoi file fixture).

    Dang file:
        {"src": "fixture", "frames": [
           {"now": "2026-09-25T14:00:00Z",
            "rows": [{"sym":"NVDA","ts":"2026-09-25T09:45:00","o":..,"c":..,"v":..}]}
        ]}
    """

    name = "fixture"

    def __init__(self, frames: list[dict], vol_ok: bool = True) -> None:
        self.frames = list(frames or [])
        self.i = 0
        self.vol_ok = vol_ok

    @classmethod
    def from_file(cls, path) -> "FixtureProvider":
        js = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(js.get("frames") or [], bool(js.get("vol_ok", True)))

    def fetch(self, syms) -> dict:
        if not self.frames:
            return missing({}, syms, self.name)
        f = self.frames[min(self.i, len(self.frames) - 1)]
        self.i += 1
        now = None
        if f.get("now"):
            iso = _iso(f["now"])
            now = dt.datetime.fromisoformat(iso) if iso else None
        return missing(build(f.get("rows") or [], now=now, src=self.name,
                            vol_ok=self.vol_ok), syms, self.name)


def get_provider(name: str | None = None) -> Provider:
    """Chon nguon. `QUOTE_SRC=fixture:duong/dan.json` de chay khong mang."""
    name = (name or os.getenv("QUOTE_SRC") or "yf").strip()
    if name.startswith("fixture:"):
        return FixtureProvider.from_file(name.partition(":")[2])
    if name == "yf":
        return YFProvider()
    raise ValueError(f"nguồn báo giá không biết: {name!r}")


# ───────────────────────── CLI ─────────────────────────
def _show(syms: list[str], src: str | None) -> int:
    p = get_provider(src)
    frame = p.fetch(syms)
    print(f"nguon: {p.name}  (khoi luong hop nhat: "
          f"{'co' if p.vol_ok else 'KHONG - khong dung de tinh RVol'})")
    print(f"\n{'MA':<6}{'GIA':>9}{'MO':>9}{'KHOI LUONG':>13}{'NEN':>5}  DAU MOC")
    for s in sorted(frame):
        q = frame[s]
        if q["err"]:
            print(f"{s:<6}  — {q['err']}")
            continue
        print(f"{s:<6}{q['px']:>9.2f}{q['open'] or 0:>9.2f}"
              f"{q['vol'] or 0:>13,.0f}{q['n_bars']:>5}  "
              f"{q['ts']}  ({fmt_age(q['age_sec'])})")
    return 0


def _smoke() -> None:
    now = dt.datetime(2026, 9, 25, 14, 0, tzinfo=dt.UTC)
    rows = [
        {"sym": "nvda", "ts": "2026-09-25T13:31:00+00:00", "o": 100, "h": 101,
         "l": 99, "c": 100.5, "v": 1000},
        {"sym": "NVDA", "ts": "2026-09-25T13:30:00+00:00", "o": 99.5, "h": 100,
         "l": 99, "c": 99.8, "v": 500},
        {"sym": "NVDA", "ts": "2026-09-25T13:32:00+00:00", "o": 100.5,
         "h": 103, "l": 100, "c": 102.0, "v": 2000},
    ]
    f = build(rows, now=now, src="t")
    q = f["NVDA"]
    assert q["px"] == 102.0, "gia = nen MOI NHAT, khong phai nen dau tien"
    assert q["open"] == 99.5, "mo cua = nen SOM NHAT, du gui len khong theo thu tu"
    assert q["vol"] == 3500 and q["n_bars"] == 3
    assert q["hi"] == 103 and q["lo"] == 99
    assert q["ts"] == "2026-09-25T13:32:00+00:00"
    assert q["age_sec"] == 28 * 60, q["age_sec"]

    # Ma vang mat phai hien ra, khong duoc im lang.
    m = missing(f, ["NVDA", "AAPL"], "t")
    assert m["AAPL"]["err"] and m["AAPL"]["px"] is None
    assert fresh(f["NVDA"], 30 * 60) and not fresh(f["NVDA"], 10 * 60)
    assert not fresh(m["AAPL"], 10 ** 9), "khong co du lieu thi khong du moi"
    assert not fresh({"px": 10, "age_sec": None}, 10 ** 9), "khong co dau moc"

    # Nen khong co mui gio = gio ET, khong phai UTC. May khong co goi `tzdata`
    # thi lui ve UTC - va PHAI co mot cau canh bao, khong duoc im lang.
    et = build([{"sym": "X", "ts": "2026-09-25T09:30:00", "c": 10, "v": 1}],
               now=now)["X"]
    if _et() is not None:
        assert et["ts"].endswith(("-04:00", "-05:00")), et["ts"]
        assert abs(et["age_sec"] - 30 * 60) < 1, et["age_sec"]
        assert tz_warn() == []
    else:
        assert et["ts"].endswith("+00:00") and tz_warn(), "lui ve UTC thi phai noi"

    # Rac vao: bo dong, khong nem.
    assert build([{"sym": "", "c": 1}, {"sym": "A"}, "x", None,
                  {"sym": "A", "ts": "2026-09-25T13:30:00Z", "c": 0}],
                 now=now) == {}

    ff = FixtureProvider([{"now": now.isoformat(), "rows": rows}])
    assert ff.fetch(["NVDA"])["NVDA"]["px"] == 102.0
    assert ff.fetch(["NVDA"])["NVDA"]["px"] == 102.0, "het khung thi giu khung cuoi"
    assert "trễ 28 phút" == fmt_age(28 * 60) and "không rõ" in fmt_age(None)
    print("quotes.py: smoke ok")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("syms", nargs="*", help="ma can lay, vd: NVDA AAPL")
    ap.add_argument("--src", help="yf | fixture:duong/dan.json")
    a = ap.parse_args()
    if a.syms:
        return _show(a.syms, a.src)
    _smoke()
    return 0


if __name__ == "__main__":
    sys.exit(main())
