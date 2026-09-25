"""holdings.py - Doc thanh phan 11 sector SPDR tu file CSV tinh.

File du lieu: holdings/sector_holdings.csv (committed vao repo). Ly do chon file
tinh thay vi scrape nam ngay trong dau file do - doc no truoc khi doi cach lam.

Viec cua module nay chi co ba thu, va thu thu ba la quan trong nhat:

  1. Doc CSV -> {sym: sector} va {sector: [sym, ...]}.
  2. Doc `as_of` tu dong chu thich, canh bao khi file qua cu.
  3. NOI THAT VE NHUNG GI NO KHONG KIEM DUOC. `check()` bat duoc ma trung,
     sector la, dong sai dinh dang, va ma khong co nen trong kho. No KHONG bat
     duoc viec mot cong ty da bi doi sector, hay mot ma moi vao ro chua co
     trong file. Do la gioi han cua cach lam nay, khong phai loi co the sua
     bang code - chi sua duoc bang cach lam moi file.

Thuan stdlib (chi csv + pathlib) -> chay duoc tren may dev khong co pandas.

    python holdings.py                   # selftest
    python holdings.py --check           # kiem file, doi chieu voi kho nen
    python holdings.py --show XLK        # liet ke thanh phan mot sector

Ma thoat: 0 = xong, 1 = loi, 2 = file co van de can sua.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "holdings" / "sector_holdings.csv"
DB = ROOT / "state" / "baseline.db"

log = print


def _as_of(lines: list[str]) -> str | None:
    """Ngay `as_of=YYYY-MM-DD` trong phan chu thich, hoac None."""
    for ln in lines:
        s = ln.strip()
        if not s.startswith("#"):
            break
        if "as_of=" in s:
            v = s.split("as_of=", 1)[1].strip().split()[0]
            try:
                dt.date.fromisoformat(v)
            except ValueError:
                return None
            return v
    return None


def load(path: Path | str = CSV_PATH) -> dict:
    """Doc file. Tra ve {"by_sym", "by_sector", "as_of", "err", "warn"}.

    Khong bao gio nem ngoai le vi mot dong xau: tra ve `warn` de --check in ra
    va de build() quyet dinh. Mot dong go sai khong duoc lam sap ca chuoi cron.
    """
    p = Path(path)
    out: dict = {"by_sym": {}, "by_sector": {}, "as_of": None,
                 "err": None, "warn": []}
    if not p.exists():
        out["err"] = f"khong thay {p}. File nay phai duoc commit vao repo."
        return out

    txt = p.read_text(encoding="utf-8")
    lines = txt.splitlines()
    out["as_of"] = _as_of(lines)
    if not out["as_of"]:
        out["warn"].append("thieu dong chu thich `as_of=YYYY-MM-DD` -> khong "
                           "biet file cu bao nhieu, khong canh bao duoc")

    hop_le = set(config.SECTOR_ETFS)
    data = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    rd = csv.DictReader(data)
    if not rd.fieldnames or "sector" not in rd.fieldnames or "sym" not in rd.fieldnames:
        out["err"] = (f"{p.name}: can dong tieu de `sector,sym`, "
                      f"dang co {rd.fieldnames}")
        return out

    for i, row in enumerate(rd, 2):
        sec = (row.get("sector") or "").strip().upper()
        sym = (row.get("sym") or "").strip().upper()
        if not sec or not sym:
            out["warn"].append(f"dong {i}: thieu sector hoac sym -> bo qua")
            continue
        if sec not in hop_le:
            out["warn"].append(f"dong {i}: sector la '{sec}' -> bo qua")
            continue
        if sym in out["by_sym"]:
            # Mot ma o hai sector thi percentile va "top 5 moi sector" deu nhoe.
            # Giu lan dau, bao lan sau - khong im lang chon bua.
            out["warn"].append(f"dong {i}: {sym} da co o "
                               f"{out['by_sym'][sym]}, bo ban sao o {sec}")
            continue
        out["by_sym"][sym] = sec
        out["by_sector"].setdefault(sec, []).append(sym)

    thieu = [s for s in config.SECTOR_ETFS if not out["by_sector"].get(s)]
    if thieu:
        out["warn"].append(f"{len(thieu)} sector khong co ma nao: "
                           f"{' '.join(thieu)} -> Stage 3 se khong tim duoc gi "
                           f"neu chung vao top")
    if not out["by_sym"]:
        out["err"] = f"{p.name}: khong doc duoc dong nao."
    return out


def age_days(as_of: str | None, today: str | None = None) -> int | None:
    if not as_of:
        return None
    t = dt.date.fromisoformat(today) if today else dt.date.today()
    return (t - dt.date.fromisoformat(as_of)).days


def stale(as_of: str | None, cfg: dict | None = None,
          today: str | None = None) -> str | None:
    """Cau canh bao khi file qua cu, hoac None.

    Thanh phan sector doi vai lan mot nam. File cu mot nam nghia la Stage 3
    dang tim co phieu dan dat trong mot ro khong con dung - va khong co gi
    trong so lieu cho thay dieu do.

    Cau tra ve CO DAU: no chay len Telegram va len dashboard, hai noi doc bang
    mat. Ban ASCII cho panel cron duoc suy ra bang nightly._ascii(), khong viet
    tay ban thu hai.
    """
    cfg = cfg or config.HOLDINGS
    n = age_days(as_of, today)
    if n is None:
        return "không biết file holdings cũ bao nhiêu (thiếu as_of)"
    if n > int(cfg["max_age_days"]):
        return (f"file holdings cũ {n} ngày (as_of {as_of}, trần "
                f"{cfg['max_age_days']}) → làm mới từ file chính thức của "
                f"SPDR, xem README")
    return None


def allowed(by_sym: dict[str, str], secs) -> dict[str, str]:
    """Chi giu nhung ma thuoc `secs`. Dung cho "top 3 sector" cua Stage 3."""
    keep = set(secs)
    return {s: v for s, v in by_sym.items() if v in keep}


def check(path: Path | str = CSV_PATH, db=None,
          today: str | None = None) -> dict:
    """Kiem file + doi chieu voi kho nen. Tra ve {"h", "loi", "canh_bao"}.

    Phan doi chieu kho nen la phan huu ich nhat: mot ma go sai (BRK.B thay vi
    BRK-B) khong he sai dinh dang, no chi... khong bao gio co nen, va bi loai
    im lang o Stage 3 mai mai.
    """
    h = load(path)
    loi = [h["err"]] if h["err"] else []
    cb = list(h["warn"])
    s = stale(h["as_of"], today=today)
    if s:
        cb.append(s)

    khong_nen: list[str] = []
    if db is not None and h["by_sym"]:
        import bars
        c, mine = bars._c(db)
        try:
            co = set(bars.syms(c, min_rows=1))
        finally:
            if mine:
                c.close()
        khong_nen = sorted(s for s in h["by_sym"] if s not in co)
        if khong_nen:
            cb.append(f"{len(khong_nen)}/{len(h['by_sym'])} ma khong co nen "
                      f"trong kho: {' '.join(khong_nen[:12])}"
                      + (" ..." if len(khong_nen) > 12 else ""))
    return {"h": h, "loi": loi, "canh_bao": cb, "khong_nen": khong_nen}


# ───────────────────────── selftest ─────────────────────────
def _write(p: Path, body: str, as_of: str | None = "2025-06-30") -> Path:
    head = "# thu\n" + (f"# as_of={as_of}\n" if as_of else "") + "sector,sym\n"
    p.write_text(head + body, encoding="utf-8")
    return p


def _smoke() -> None:
    import tempfile

    tmp = Path(tempfile.mkdtemp())

    # --- file thuc trong repo phai doc duoc ---
    h = load()
    assert not h["err"], h["err"]
    assert h["as_of"], "file thuc phai co as_of"
    assert len(h["by_sector"]) == 11, sorted(h["by_sector"])
    assert len(h["by_sym"]) > 200, len(h["by_sym"])
    for s in config.SECTOR_ETFS:
        assert len(h["by_sector"][s]) >= 15, (s, len(h["by_sector"][s]))
    # moi ma chi thuoc MOT sector
    tong = sum(len(v) for v in h["by_sector"].values())
    assert tong == len(h["by_sym"]) == len(set(h["by_sym"])), (tong,)
    # quy uoc ma yfinance: khong duoc co dau cham
    assert not [s for s in h["by_sym"] if "." in s], "phai dung BRK-B kieu gach"
    assert all(s == s.upper().strip() for s in h["by_sym"])
    assert "AAPL" in h["by_sym"] and h["by_sym"]["AAPL"] == "XLK"
    assert h["by_sym"]["XOM"] == "XLE" and h["by_sym"]["NEE"] == "XLU"
    assert h["by_sym"]["V"] == "XLF", "V/MA sang Financials tu GICS 2023"

    # --- ma trung -> giu lan dau, canh bao lan sau ---
    h2 = load(_write(tmp / "dup.csv", "XLK,AAPL\nXLV,AAPL\nXLK,MSFT\n"))
    assert h2["by_sym"]["AAPL"] == "XLK" and len(h2["by_sym"]) == 2
    assert any("AAPL" in w for w in h2["warn"])

    # --- sector la / dong thieu cot -> bo qua, khong nem ngoai le ---
    h3 = load(_write(tmp / "bad.csv", "SPY,AAPL\n,MSFT\nXLK,\nXLK,NVDA\n"))
    assert list(h3["by_sym"]) == ["NVDA"], h3["by_sym"]
    assert len(h3["warn"]) >= 4, h3["warn"]      # 3 dong xau + 10 sector rong

    # --- file khong co / rong / sai tieu de ---
    assert load(tmp / "khong_ton_tai.csv")["err"]
    assert load(_write(tmp / "rong.csv", ""))["err"]
    sai = tmp / "sai.csv"
    sai.write_text("# x\nma,nganh\nXLK,AAPL\n", encoding="utf-8")
    assert "sector,sym" in (load(sai)["err"] or "")

    # --- as_of ---
    assert _as_of(["# as_of=2025-06-30"]) == "2025-06-30"
    assert _as_of(["# as_of=30/06/2025"]) is None
    assert _as_of(["sector,sym", "# as_of=2025-06-30"]) is None, \
        "chi doc trong khoi chu thich dau file"
    assert age_days("2025-06-30", "2025-07-30") == 30
    assert stale("2025-06-30", today="2025-07-01") is None
    assert "cũ 400 ngày" in stale("2024-06-30", today="2025-08-04")
    assert stale(None) and "không biết" in stale(None)
    assert load(_write(tmp / "noas.csv", "XLK,AAPL\n", as_of=None))["as_of"] is None

    # --- allowed ---
    by = {"AAPL": "XLK", "XOM": "XLE", "PG": "XLP"}
    assert allowed(by, ("XLK", "XLE")) == {"AAPL": "XLK", "XOM": "XLE"}
    assert allowed(by, ()) == {}

    # --- check: doi chieu kho nen bat duoc ma go sai ---
    import bars
    db = tmp / "t.db"
    c = bars.con(db)
    bars.save(c, {"AAPL": [("2024-01-02", 1, 1, 1, 1, 1, 100)]})
    r = check(_write(tmp / "typo.csv", "XLK,AAPL\nXLF,BRK.B\n"), db=c)
    assert r["khong_nen"] == ["BRK.B"], r["khong_nen"]
    assert any("khong co nen" in x for x in r["canh_bao"])
    r2 = check(tmp / "khong_ton_tai.csv", db=c)
    assert r2["loi"] and not r2["khong_nen"]
    c.close()

    print("holdings.py selftest: ok")


# ───────────────────────── CLI ─────────────────────────
if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                            # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(description="thanh phan sector tu CSV tinh")
    ap.add_argument("--check", action="store_true",
                    help="kiem file va doi chieu voi kho nen")
    ap.add_argument("--show", metavar="SECTOR", help="liet ke mot sector")
    ap.add_argument("--csv", default=str(CSV_PATH))
    ap.add_argument("--db", default=str(DB))
    a = ap.parse_args()

    if a.show:
        h = load(a.csv)
        if h["err"]:
            print(f"holdings: {h['err']}")
            raise SystemExit(2)
        syms = h["by_sector"].get(a.show.upper())
        if not syms:
            print(f"holdings: khong co sector '{a.show.upper()}'. Co: "
                  f"{' '.join(sorted(h['by_sector']))}")
            raise SystemExit(2)
        print(f"{a.show.upper()}  {len(syms)} ma (as_of {h['as_of']})")
        print("  " + " ".join(syms))
        raise SystemExit(0)

    if not a.check:
        _smoke()
        raise SystemExit(0)

    db = Path(a.db)
    r = check(a.csv, db=db if db.exists() else None)
    h = r["h"]
    if h["by_sym"]:
        print(f"{len(h['by_sym'])} ma / {len(h['by_sector'])} sector "
              f"(as_of {h['as_of']}, {age_days(h['as_of'])} ngay)")
        for s in config.SECTOR_ETFS:
            print(f"  {s:<5} {len(h['by_sector'].get(s, [])):3d} ma")
    if not db.exists():
        print(f"  (khong co {db} -> khong doi chieu duoc voi kho nen)")
    for x in r["loi"]:
        print(f"  LOI: {x}")
    for x in r["canh_bao"]:
        print(f"  canh bao: {x}")
    print("\nLUU Y: --check kiem DINH DANG, khong kiem duoc TINH DUNG DAN. "
          "Mot cong ty da doi sector van qua het cac phep kiem tren.")
    raise SystemExit(2 if r["loi"] else 0)
