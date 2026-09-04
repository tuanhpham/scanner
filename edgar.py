#!/usr/bin/env python3
"""edgar.py - tra ho so SEC cho cac ma da alert. Tra loi cau hoi 'tai sao'."""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import httpx
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

ROOT = Path(__file__).resolve().parent
DB = ROOT / "state" / "baseline.db"
CACHE = ROOT / "state" / "edgar_cache"
CACHE.mkdir(parents=True, exist_ok=True)

SEC_UA = os.getenv("SEC_UA", "")
SUB_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{doc}"

CACHE_TTL = 1800   # 30 phut - ho so moi xuat hien trong phien
SLEEP = 0.15       # SEC cho 10 req/s, di cham cho lich su
HOT_DAYS = 5       # cua so "nong"
SHELF_DAYS = 120   # shelf con hieu luc

# Phan loai form -> (nhom, trong so rui ro, mo ta)
FORMS = {
    "424B5": ("BAN_NGAY", 3.0, "đang chào bán cổ phiếu (shelf takedown)"),
    "424B4": ("BAN_NGAY", 3.0, "đang chào bán cổ phiếu"),
    "424B3": ("BAN_NGAY", 2.0, "cáo bạch bổ sung"),
    "424B2": ("BAN_NGAY", 2.0, "cáo bạch bổ sung"),
    "FWP":   ("BAN_NGAY", 1.5, "tài liệu chào bán tự do"),
    "S-3":     ("SAN_SANG", 1.5, "đăng ký kê hàng - có thể bán bất cứ lúc nào"),
    "S-3ASR":  ("SAN_SANG", 1.5, "đăng ký kê hàng tự động hiệu lực"),
    "S-1":     ("SAN_SANG", 1.5, "đăng ký phát hành"),
    "S-1/A":   ("SAN_SANG", 1.0, "sửa đăng ký phát hành"),
    "EFFECT":  ("SAN_SANG", 1.0, "đăng ký đã có hiệu lực"),
    "8-K":  ("TIN", 0.0, "tin trọng yếu"),
    "6-K":  ("TIN", 0.0, "tin trọng yếu (công ty nước ngoài)"),
    "SC 13D":   ("GOM_HANG", -1.0, "cổ đông >5% có ý định tác động"),
    "SC 13D/A": ("GOM_HANG", -0.5, "cập nhật cổ đông >5%"),
    "SC 13G":   ("GOM_HANG", -0.5, "cổ đông >5% thu đông"),
    "25-NSE":  ("XAU", 3.0, "thông báo hủy niêm yết"),
    "NT 10-K": ("XAU", 2.0, "nộp báo cáo năm trễ"),
    "NT 10-Q": ("XAU", 1.5, "nộp báo cáo quý trễ"),
}

ITEMS = {
    "1.01": "ký hợp đồng trọng yếu",
    "1.03": "PHÁ SẢN",
    "2.01": "mua/bán tài sản",
    "2.02": "công bố kết quả kinh doanh",
    "3.01": "NGUY CƠ HỦY NIÊM YẾT",
    "3.02": "bán cổ phiếu không đăng ký (pha loãng)",
    "5.02": "thay đổi lãnh đạo",
    "7.01": "công bố Reg FD",
    "8.01": "sự kiện khác",
}
ITEM_RISK = {"1.03": 3.0, "3.01": 2.5, "3.02": 2.0}


def _cik(sym: str) -> str | None:
    try:
        con = sqlite3.connect(DB)
        row = con.execute("SELECT cik FROM base WHERE sym=?", (sym,)).fetchone()
        con.close()
    except Exception:  # noqa: BLE001
        return None
    if not row or not row[0]:
        return None
    return str(row[0]).zfill(10)


def _fetch(cik: str) -> dict | None:
    """Lay submissions JSON, co cache tren dia."""
    f = CACHE / f"{cik}.json"
    if f.exists() and time.time() - f.stat().st_mtime < CACHE_TTL:
        try:
            return json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            pass
    if not SEC_UA or "@" not in SEC_UA:
        print("[!] SEC_UA chua dat dung dinh dang 'Ten email@domain.com'")
        return None
    try:
        r = httpx.get(SUB_URL.format(cik=cik),
                      headers={"User-Agent": SEC_UA,
                               "Accept-Encoding": "gzip, deflate"},
                      timeout=20)
        time.sleep(SLEEP)
        if r.status_code != 200:
            return None
        data = r.json()
        f.write_text(json.dumps(data))
        return data
    except Exception:  # noqa: BLE001
        return None


def filings(sym: str, days: int = SHELF_DAYS) -> list[dict]:
    """Danh sach ho so trong N ngay gan nhat."""
    cik = _cik(sym)
    if not cik:
        return []
    data = _fetch(cik)
    if not data:
        return []
    rec = data.get("filings", {}).get("recent", {})
    forms = rec.get("form", [])
    dates = rec.get("filingDate", [])
    accs = rec.get("accessionNumber", [])
    docs = rec.get("primaryDocument", [])
    items = rec.get("items", [])
    cutoff = dt.date.today() - dt.timedelta(days=days)
    out = []
    for i, form in enumerate(forms):
        try:
            d = dt.date.fromisoformat(dates[i])
        except Exception:  # noqa: BLE001
            continue
        if d < cutoff:
            break            # mang da sap xep giam dan
        if form not in FORMS:
            continue
        acc = accs[i] if i < len(accs) else ""
        out.append({
            "form": form,
            "date": d,
            "age": (dt.date.today() - d).days,
            "items": (items[i] if i < len(items) else "") or "",
            "url": DOC_URL.format(cik_int=int(cik),
                                  acc_nodash=acc.replace("-", ""),
                                  doc=docs[i] if i < len(docs) else ""),
        })
    return out


def assess(sym: str) -> dict:
    """Chấm điểm rủi ro từ hồ sơ SEC. Dương = nguy hiểm, âm = tích cực."""
    fs = filings(sym)
    if not fs:
        return {"sym": sym, "risk": 0.0, "flags": [], "n": 0, "earn": False,
                "note": "không có hồ sơ / thiếu CIK"}
    risk = 0.0
    flags: list[str] = []
    earn = False
    for f in fs:
        grp, w, desc = FORMS[f["form"]]
        fresh = f["age"] <= HOT_DAYS
        if grp == "BAN_NGAY":
            risk += w if fresh else w * 0.3
            if fresh:
                flags.append(f"{f['form']} {f['age']}d trước - {desc}")
            else:
                flags.append(f"{f['form']} {f['age']}d trước - {desc} (cũ)")
        elif grp == "SAN_SANG":
            risk += w if f["age"] <= 30 else w * 0.4
            if f["age"] <= 30:
                flags.append(f"{f['form']} {f['age']}d trước - {desc}")
            else:
                flags.append(f"{f['form']} {f['age']}d trước - {desc} (cũ)")
        elif grp == "XAU":
            risk += w if fresh else w * 0.5
            flags.append(f"{f['form']} {f['age']}d trước - {desc}")
        elif grp == "GOM_HANG" and f["age"] <= 30:
            risk += w
            flags.append(f"{f['form']} {f['age']}d trước - {desc}")
        elif grp == "TIN" and fresh:
            codes = [c.strip() for c in f["items"].split(",") if c.strip()]
            if "2.02" in codes:
                earn = True
            named = [ITEMS[c] for c in codes if c in ITEMS]
            risk += sum(ITEM_RISK.get(c, 0.0) for c in codes)
            if named:
                flags.append(f"8-K {f['age']}d trước - " + "; ".join(named))
            else:
                flags.append(f"8-K {f['age']}d trước")
    return {"sym": sym, "risk": round(risk, 1), "flags": flags[:6],
            "n": len(fs), "top": fs[0] if fs else None, "earn": earn}


def label(risk: float, earn: bool = False) -> str:
    if risk >= 3.0:
        return "🔴 RỦI RO PHA LOÃNG CAO"
    if risk >= 1.5:
        return "🟠 Có kế hoạch phát hành"
    if risk <= -0.5:
        return "🟢 Cổ đông lớn gom hàng"
    if earn:
        return "🔵 Vừa báo cáo kết quả kinh doanh"
    return "⚪ Không thấy tín hiệu đặc biệt"


def block(sym: str, max_flags: int = 3) -> list[str]:
    """Vài dòng mô tả hồ sơ SEC cho tin nhắn Telegram."""
    a = assess(sym)
    if a["n"] == 0:
        return ["⚪ Không tra được hồ sơ"]
    return [label(a["risk"], a.get("earn", False))] + list(a["flags"][:max_flags])

def line(sym: str) -> str:
    """Một dòng ngắn gọn để đặt vào alert Telegram."""
    a = assess(sym)
    if a["n"] == 0:
        return "SEC: không tra được hồ sơ"
    head = f"SEC: {label(a["risk"], a.get("earn", False))}"
    return head + ("\n" + "\n".join("· " + f for f in a["flags"][:3])
                   if a["flags"] else "")


def _demo(syms: list[str]) -> None:
    for s in syms:
        a = assess(s)
        print(f"\n=== {s} ===  risk={a['risk']}  {label(a["risk"], a.get("earn", False))}")
        if a["n"] == 0:
            print("   ", a.get("note", ""))
            continue
        print(f"    {a['n']} hồ sơ trong {SHELF_DAYS} ngày")
        for f in a["flags"]:
            print("    ·", f)
        if a["top"]:
            print("    mới nhất:", a["top"]["form"], a["top"]["date"],
                  "\n   ", a["top"]["url"])


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        con = sqlite3.connect(DB)
        args = [r[0] for r in con.execute(
            "SELECT DISTINCT sym FROM alerts ORDER BY ts_et DESC LIMIT 10")]
        con.close()
        print("Các mã đã alert gần đây:", ", ".join(args))
    _demo(args)
