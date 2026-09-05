"""news.py - Tin tuc theo ma, de biet VI SAO mot ma dang chay (Phase 3).

Alert noi "RVOL 66x, +85%" nhung khong noi TAI SAO. Chinh cai "tai sao" moi
quyet dinh co dang quan tam hay khong:

    "Announces FDA Clearance for ..."            -> catalyst that
    "Announces Pricing of $15M Registered Direct" -> BAY: chay vi pha loang

Nhom pha loang la ly do chinh cua module nay. `edgar.py` cung bat duoc no,
nhung chi SAU khi 424B5 len EDGAR (vai gio). Ban tin Benzinga ra TRUOC do,
thuong ngay luc gia nhay.

Nguon: Alpaca News API (v1beta1/news) - REST, dung ALPACA_KEY/ALPACA_SECRET
co san. README ban dau ghi websocket; xem "sai lech" trong README muc 11 ve
ly do chon REST.

    python news.py            # selftest bang mau co san, khong can mang
    python news.py --live     # goi that vao Alpaca, in tin 4 gio gan nhat
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
try:                                     # may dev khong co python-dotenv
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:                                                # noqa: BLE001
    pass

log = print          # main.py co the gan lai: news.log = log

API = "https://data.alpaca.markets/v1beta1/news"
KEY = os.getenv("ALPACA_KEY", "")
SECRET = os.getenv("ALPACA_SECRET", "")

POLL = 30            # giay giua 2 lan goi - tin la chuyen cua phut, khong phai giay
WINDOW_H = 4         # chi giu tin trong 4 gio: cu hon thi khong giai thich duoc cu chay
STALE = 180          # khong refresh duoc lau hon the nay -> khong dam noi "khong co tin"
MAX_PER_SYM = 6      # moi ma giu bao nhieu tin (chan phinh RAM)
MAX_SEEN = 4000      # so id da thay, de khong dem lai tin cu
LIMIT = 50           # gioi han cua Alpaca cho mot trang
MAX_PAGES = 4        # luc 9:30 tin ra don dap -> phai lat trang, khong duoc bo sot
OVERLAP = 90         # giay lay lui lai moi lan goi, tranh lot tin o ranh gio

# Bai gan nhieu hon the nay la bai tong hop thi truong ("10 Stocks Moving In
# Monday's Pre-Market Session"), khong phai catalyst cua rieng ma nao.
MAX_SYMS = 4

# ───────────────────────── bang tu khoa ─────────────────────────
# (khoa, nhan hien thi, risk 0-3, giai thich ngan, tu khoa)
#
# THU TU QUAN TRONG: nhom XAU dung TRUOC. Mot tieu de vua co tin tot vua co
# offering ("... FDA Clearance; Announces $20M Offering") la BAY - cach doc
# xau phai thang. `classify()` lay nhom dau tien khop.
#
# risk >= NEWS_RISK_MAX -> main.py tru diem (dung mot lan, xem main.py).
# Nhom tot co risk 0.0 va KHONG cong diem: chua co so lieu nao noi tin tot
# lam ma chay xa hon, cong diem bay gio la doan bua.
GROUPS: tuple[tuple[str, str, float, str, tuple[str, ...]], ...] = (
    ("BANKRUPT", "PHÁ SẢN / MẤT THANH KHOẢN", 3.0,
     "Cong ty dang trong thu tuc pha san. Co phieu thuong ve 0.",
     ("chapter 11", "chapter 7", "bankruptcy", "bankrupt", "receivership",
      "insolvency", "liquidation", "going concern", "wind-down", "winding down")),

    ("DELIST", "NGUY CƠ HỦY NIÊM YẾT", 3.0,
     "San da gui thong bao khong tuan thu. Rui ro bi day xuong OTC.",
     ("delisting", "delisted", "deficiency", "deficiency letter",
      "non-compliance", "noncompliance", "not in compliance",
      "minimum bid price", "listing qualification", "listing rule",
      "hearings panel", "form 25", "compliance period", "stockholders equity requirement")),

    ("DILUTION", "PHA LOÃNG — TIN VỪA RA", 3.0,
     "Cong ty ban them co phieu. Gia tang hom nay khong phai vi hoat dong tot.",
     ("pricing of", "priced offering", "public offering", "offering of",
      "proposed offering", "announces offering", "unit offering",
      "best efforts offering", "underwritten offering", "underwritten public",
      "registered direct", "private placement", "at-the-market", "at the market",
      "atm program", "atm facility", "atm offering",
      "securities purchase agreement", "equity distribution agreement",
      "equity line of credit", "equity purchase agreement",
      "shelf registration", "convertible note", "convertible notes",
      "convertible debenture", "convertible preferred",
      "warrant inducement", "warrant exercise", "warrant exercises",
      "dilution", "dilutive", "capital raise", "raises $", "s-1", "s-3")),

    ("SPLIT", "GỘP CỔ PHIẾU (REVERSE SPLIT)", 2.0,
     "Gia tang vi so co phieu giam, khong phai vi cong ty tot hon.",
     ("reverse split", "reverse stock split", "share consolidation",
      "ratio change")),

    ("BIO", "DỮ LIỆU / PHÊ DUYỆT", 0.0, "",
     ("fda approval", "fda clearance", "fda approves", "fda clears",
      "510(k)", "de novo", "pdufa", "breakthrough therapy", "orphan drug",
      "fast track", "priority review", "phase 1", "phase 2", "phase 3",
      "phase i", "phase ii", "phase iii", "topline", "top-line",
      "primary endpoint", "met its primary", "statistically significant",
      "ce mark", "nda submission", "bla submission", "ind clearance",
      "interim results", "interim analysis", "clinical trial", "ema approval")),

    ("DEAL", "HỢP ĐỒNG / THƯƠNG VỤ", 0.0, "",
     ("contract", "contract award", "awarded", "award from", "purchase order",
      "partnership", "partners with", "collaboration", "teams with",
      "acquisition", "to acquire", "acquires", "merger", "merge with",
      "definitive agreement", "letter of intent", "joint venture",
      "distribution agreement", "licensing agreement", "license agreement",
      "strategic investment", "selected by", "government contract",
      "master agreement", "supply agreement")),

    ("EARN", "KẾT QUẢ KINH DOANH", 0.0, "",
     ("first quarter", "second quarter", "third quarter", "fourth quarter",
      "full year results", "full-year results", "q1 results", "q2 results",
      "q3 results", "q4 results", "earnings results", "record revenue",
      "record quarterly", "revenue increased", "revenue grew",
      "financial results", "raises guidance", "raises full-year",
      "beats estimates", "profitability")),
)
NEWS_RISK_MAX = 3.0   # tu muc nay -> coi la tin xau that su

SOURCES = {"benzinga": "Benzinga", "globenewswire": "GlobeNewswire",
           "businesswire": "Business Wire", "prnewswire": "PR Newswire",
           "accesswire": "ACCESSWIRE", "newsfilecorp": "Newsfile",
           "thefly": "The Fly", "dowjones": "Dow Jones", "reuters": "Reuters"}


def _pat(words: tuple[str, ...]) -> re.Pattern[str]:
    """Ghep tu khoa thanh mot regex, co ranh gio tu.

    Ranh gio la BAT BUOC: "atm" khong co \\b se khop "atmosphere", va
    "award" se khop "awarded" (khong sao) nhung "s-3" se khop "vs-30".
    Chi them \\b o dau/cuoi khi ky tu do la chu/so - "510(k)" bat dau bang
    so nhung ket thuc bang ")" nen chi can \\b o dau.
    """
    parts = []
    for w in words:
        p = re.escape(w.lower())
        if w[:1].isalnum():
            p = r"\b" + p
        if w[-1:].isalnum():
            p = p + r"\b"
        parts.append(p)
    return re.compile("|".join(parts))


_RX: tuple[tuple[str, str, float, str, re.Pattern[str]], ...] = tuple(
    (k, label, risk, note, _pat(words)) for k, label, risk, note, words in GROUPS)

_DASH = str.maketrans({"‐": "-", "‑": "-", "‒": "-",
                       "–": "-", "—": "-", "‘": "'",
                       "’": "'", "“": '"', "”": '"',
                       " ": " "})


def _norm(s: str) -> str:
    """Chuan hoa truoc khi do tu khoa.

    Ban tin hay dung gach ngang dai va nhay cong: "at-the-market" viet bang
    U+2011 se khong khop tu khoa ascii. Doi het ve ascii roi ep chu thuong.
    """
    return re.sub(r"\s+", " ", (s or "").translate(_DASH)).strip().lower()


def classify(headline: str) -> tuple[str | None, str, float, str]:
    """Tieu de -> (khoa, nhan, risk, giai thich). Khong khop -> (None, "", 0, "")."""
    h = _norm(headline)
    if not h:
        return None, "", 0.0, ""
    for k, label, risk, note, rx in _RX:
        if rx.search(h):
            return k, label, risk, note
    return None, "", 0.0, ""


def source_name(s: str) -> str:
    s = (s or "").strip()
    return SOURCES.get(s.lower().replace(" ", ""), s.title() if s else "")


# ───────────────────────── parse ─────────────────────────
def _epoch(iso: str) -> int | None:
    """'2026-09-04T13:30:00Z' -> epoch. Alpaca luon tra UTC co hau to Z."""
    s = (iso or "").strip().replace("Z", "+00:00")
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp())


def parse(payload, now: float | None = None) -> list[dict]:
    """JSON tra ve tu Alpaca -> danh sach ban tin da phan loai, moi nhat truoc.

    Khong nem loi: API doi dang thi tra ve rong, bot van phai chay.
    """
    t = now if now is not None else time.time()
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except Exception as e:                                    # noqa: BLE001
            log(f"news.parse: JSON hong: {type(e).__name__}: {e}")
            return []
    if not isinstance(payload, dict):
        return []

    out: list[dict] = []
    cut = t - WINDOW_H * 3600
    for it in payload.get("news") or []:
        if not isinstance(it, dict):
            continue
        head = (it.get("headline") or "").strip()
        ts = _epoch(it.get("created_at") or it.get("updated_at") or "")
        if not head or ts is None or ts < cut:
            continue
        syms = [str(s).upper().strip() for s in (it.get("symbols") or [])
                if str(s).strip()]
        if not syms or len(syms) > MAX_SYMS:
            continue                    # khong gan ma nao, hoac bai tong hop
        k, label, risk, note = classify(head)
        out.append({
            "id": it.get("id") or f"{ts}:{head[:40]}",
            "ts": ts, "headline": head, "symbols": syms,
            "source": source_name(it.get("source") or ""),
            "url": (it.get("url") or "").strip(),
            "group": k, "label": label, "risk": risk, "note": note,
        })
    out.sort(key=lambda r: -r["ts"])
    return out


def fetch(since: float | None = None, page_token: str = "",
          limit: int = LIMIT, timeout: float = 15.0) -> dict:
    """Mot trang tin. Nem loi de NewsBook.refresh() dem lan that bai."""
    import httpx
    p: dict[str, object] = {"limit": int(limit), "sort": "desc",
                            "include_content": "false",
                            "exclude_contentless": "true"}
    if since:
        p["start"] = dt.datetime.fromtimestamp(
            since, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if page_token:
        p["page_token"] = page_token
    r = httpx.get(API, params=p, timeout=timeout,
                  headers={"APCA-API-KEY-ID": KEY,
                           "APCA-API-SECRET-KEY": SECRET,
                           "User-Agent": "scanner/1.0"})
    r.raise_for_status()
    return r.json()


# ───────────────────────── so tay tin ─────────────────────────
class NewsBook:
    """Dict cuon {sym: [ban tin]} chi giu WINDOW_H gio gan nhat.

    Khong tu goi mang trong tra cuu: main.py quyet dinh khi nao refresh (chay
    trong to_thread). Nho vay test duoc bang load() offline.
    """

    def __init__(self) -> None:
        self.by_sym: dict[str, list[dict]] = {}
        self.seen: set = set()
        self.ts: float = 0.0          # lan refresh thanh cong gan nhat
        self.err: str = ""
        self.n_fail = 0
        self.n_total = 0              # tong so tin da nhan, de log

    # -- cap nhat --
    def load(self, payload, now: float | None = None) -> int:
        """Nhap mot trang tin. Tra ve so tin MOI (da bo trung theo id)."""
        t = now if now is not None else time.time()
        n = 0
        for rec in parse(payload, t):
            if rec["id"] in self.seen:
                continue
            self.seen.add(rec["id"])
            n += 1
            for s in rec["symbols"]:
                lst = self.by_sym.setdefault(s, [])
                lst.append(rec)
                lst.sort(key=lambda r: -r["ts"])
                del lst[MAX_PER_SYM:]
        if len(self.seen) > MAX_SEEN:
            self.seen = {r["id"] for lst in self.by_sym.values() for r in lst}
        self.ts, self.err, self.n_fail = t, "", 0
        self.n_total += n
        self.prune(now=t)
        return n

    def refresh(self, now: float | None = None) -> int:
        """Goi mang (lat trang neu can) + nhap. Tra ve so tin moi, -1 neu loi."""
        t = now if now is not None else time.time()
        since = (self.ts - OVERLAP) if self.ts else (t - WINDOW_H * 3600)
        n, token = 0, ""
        try:
            for _ in range(MAX_PAGES):
                data = fetch(since=since, page_token=token)
                n += self.load(data, now=t)
                token = (data.get("next_page_token") or "") if isinstance(
                    data, dict) else ""
                if not token:
                    break
        except Exception as e:                                   # noqa: BLE001
            self.n_fail += 1
            self.err = f"{type(e).__name__}: {e}"
            # Khong xoa by_sym: tin cu 2 phut van giai thich duoc cu chay.
            return -1
        return n

    def prune(self, keep: set | None = None, now: float | None = None) -> int:
        """Bo tin qua WINDOW_H gio, va bo ma khong con trong universe.

        `keep` rong/None -> chi don theo tuoi. Ngoai gio quet universe rong,
        prune(keep) luc do se xoa sach so tay ma khong duoc gi.
        """
        t = now if now is not None else time.time()
        cut = t - WINDOW_H * 3600
        gone = 0
        for s in list(self.by_sym):
            if keep and s not in keep:
                gone += len(self.by_sym.pop(s))
                continue
            lst = [r for r in self.by_sym[s] if r["ts"] >= cut]
            gone += len(self.by_sym[s]) - len(lst)
            if lst:
                self.by_sym[s] = lst
            else:
                del self.by_sym[s]
        return gone

    # -- tra cuu --
    @property
    def stale(self) -> bool:
        return not self.ts or (time.time() - self.ts) > STALE

    @property
    def ok(self) -> bool:
        """Feed dang song -> duoc phep noi "khong co tin".

        `ts` chi khac 0 sau mot lan nhap thanh cong, nen khong can kiem tra key
        rieng: khong co key thi refresh() luon tra -1 va ts van la 0.
        """
        return bool(self.ts) and not self.stale

    def items(self, sym: str, now: float | None = None) -> list[dict]:
        """Tin cua mot ma trong WINDOW_H gio, moi nhat truoc."""
        t = now if now is not None else time.time()
        cut = t - WINDOW_H * 3600
        return [r for r in self.by_sym.get((sym or "").upper(), [])
                if r["ts"] >= cut]

    def risk(self, sym: str, now: float | None = None) -> float:
        its = self.items(sym, now)
        return max((r["risk"] for r in its), default=0.0)

    def view(self, sym: str, now: float | None = None) -> dict | None:
        """Ban ghi da chuan bi cho render, hoac None neu khong biet gi.

        None  = khong co key / feed chet -> render.py khong ve khoi nao. Noi
                "khong co tin" luc feed chet la noi sai.
        n = 0 = feed song va thuc su khong co tin nao -> nhan "chay khong co
                ly do ro rang", dung y README.
        """
        t = now if now is not None else time.time()
        if not self.ok:
            return None
        its = self.items(sym, t)
        if not its:
            return {"group": None, "label": "", "risk": 0.0, "note": "",
                    "headline": "", "source": "", "url": "", "age": 0, "n": 0}
        # Tin XAU thang tin MOI: mot ban "Pricing of Offering" 30 phut truoc
        # quan trong hon ban "Record Revenue" 5 phut truoc. Bang diem thi lay
        # ban moi nhat.
        best = max(its, key=lambda r: (r["risk"], r["ts"]))
        return {"group": best["group"], "label": best["label"],
                "risk": best["risk"], "note": best["note"],
                "headline": best["headline"], "source": best["source"],
                "url": best["url"], "age": max(0, int((t - best["ts"]) / 60)),
                "n": len(its)}

    def risky_syms(self) -> list[str]:
        return sorted(s for s in self.by_sym
                      if self.risk(s) >= NEWS_RISK_MAX)


# ───────────────────────── test offline ─────────────────────────
_ITEMS = [
    # (phut truoc, id, symbols, source, headline)
    (30, 101, ["WETO"], "benzinga",
     "Weto Inc Announces Pricing of $15.0 Million Registered Direct Offering"),
    (5, 102, ["WETO"], "globenewswire",
     "Weto Inc Reports Record Third Quarter Revenue, Raises Guidance"),
    (40, 103, ["BIOX"], "benzinga",
     "Biox Receives FDA 510(k) Clearance For Its Diagnostic Platform"),
    (12, 104, ["DEALZ"], "businesswire",
     "Dealz Corp Awarded $42 Million U.S. Army Contract"),
    (18, 105, ["SPLTZ"], "benzinga",
     "Spltz Ltd Announces 1-for-20 Reverse Stock Split"),
    (25, 106, ["DELIZ"], "benzinga",
     "Deliz Receives Nasdaq Notification Of Non-Compliance With Minimum Bid Price"),
    (8, 107, ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"], "benzinga",
     "10 Stocks Moving In Monday's Pre-Market Session"),
    (7, 108, ["PLAIN"], "benzinga",
     "Plain Corp Names Jane Doe As Chief Operating Officer"),
    (60 * 5, 109, ["OLDIE"], "benzinga",
     "Oldie Inc Receives FDA Approval For Its Lead Candidate"),
    (9, 110, ["TRAPZ"], "benzinga",
     "Trapz Announces FDA Clearance And Pricing Of $20 Million Offering"),
]


def _sample(now: float) -> str:
    """Mau voi moc thoi gian tuong doi so voi `now` -> test khong bao gio het han."""
    def iso(mins: int) -> str:
        return dt.datetime.fromtimestamp(
            now - mins * 60, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return json.dumps({"news": [
        {"id": i, "headline": h, "author": "x", "created_at": iso(m),
         "updated_at": iso(m), "summary": "", "url": f"https://ex.com/{i}",
         "symbols": syms, "source": src}
        for m, i, syms, src, h in _ITEMS], "next_page_token": None})


def _selftest() -> None:
    now = time.time()

    # --- bang tu khoa ---
    assert classify("Announces Pricing of $12M Offering")[0] == "DILUTION"
    assert classify("Prices $8.0 Million Public Offering")[0] == "DILUTION"
    assert classify("Enters At‑The‑Market Sales Agreement")[0] == "DILUTION"
    assert classify("XYZ Announces FDA Clearance")[0] == "BIO"
    assert classify("Wins $10M Government Contract")[0] == "DEAL"
    assert classify("Announces 1-for-15 Reverse Stock Split")[0] == "SPLIT"
    assert classify("Files For Chapter 11 Bankruptcy Protection")[0] == "BANKRUPT"
    assert classify("Regains Compliance, Avoids Delisting")[0] == "DELIST"
    assert classify("")[0] is None
    assert classify("Names New Chief Financial Officer")[0] is None
    # Tu ngan khong duoc khop trong tu khac.
    assert classify("Unveils New ATMosphere Sensor Line")[0] is None, "\\b bi thieu"
    assert classify("Reports Phase 3 Topline Results")[0] == "BIO"
    # Tieu de vua tot vua xau -> phai doc theo huong XAU.
    k, label, risk, _ = classify("Announces FDA Clearance And Pricing Of Offering")
    assert k == "DILUTION" and risk >= NEWS_RISK_MAX, (k, risk)
    print(f"bang tu khoa: {len(GROUPS)} nhom, tin vua tot vua xau -> {label}")

    # --- parse ---
    b = NewsBook()
    n = b.load(_sample(now), now=now)
    assert n == 8, f"{n} (bo 1 bai tong hop + 1 tin 5 gio truoc)"
    assert "AAA" not in b.by_sym, "bai gan 6 ma la bai tong hop, phai bo"
    assert "OLDIE" not in b.by_sym, "tin 5 gio truoc phai bi cat"
    print(f"parse: {n} tin cho {len(b.by_sym)} ma")

    # Nhap lai cung mau -> khong tin nao moi (bo trung theo id).
    assert b.load(_sample(now), now=now) == 0, "phai bo trung theo id"

    # --- view ---
    assert b.ok, "vua load xong thi feed phai duoc coi la con song"
    v = b.view("WETO", now)
    assert v and v["group"] == "DILUTION", v
    assert v["n"] == 2, "WETO co 2 tin"
    print(f"  WETO  {v['label']} · {v['source']} · {v['age']} phut truoc")
    assert v["age"] == 30, v["age"]      # tin xau (30p) thang tin moi (5p)

    v = b.view("BIOX", now)
    assert v and v["group"] == "BIO" and v["risk"] == 0.0, v
    print(f"  BIOX  {v['label']} · {v['source']} · {v['age']} phut truoc")

    v = b.view("PLAIN", now)
    assert v and v["group"] is None and v["n"] == 1, v
    assert v["headline"], "khong phan loai duoc thi van phai dua tieu de"
    print(f"  PLAIN (khong ro nhom) · van co tieu de · n={v['n']}")

    v = b.view("AAPL", now)
    assert v and v["n"] == 0 and v["group"] is None, v
    print("  AAPL  khong co tin -> n=0 (khac han voi None)")

    assert b.risk("WETO", now) >= NEWS_RISK_MAX
    assert b.risk("BIOX", now) == 0.0
    assert b.risky_syms() == ["DELIZ", "TRAPZ", "WETO"], b.risky_syms()
    print(f"  ma co tin xau: {b.risky_syms()}")

    # Feed chet -> view() tra None, KHONG duoc noi "khong co tin".
    b.ts = now - STALE - 1
    assert b.stale and not b.ok and b.view("WETO", now) is None
    print(f"  feed cu > {STALE}s -> view() tra None (khong doan bua)")

    # --- prune ---
    b2 = NewsBook()
    b2.load(_sample(now), now=now)
    n0 = len(b2.by_sym)
    assert b2.prune(keep={"WETO"}, now=now) and list(b2.by_sym) == ["WETO"]
    print(f"  prune(keep) {n0} ma -> {len(b2.by_sym)} ma")
    b3 = NewsBook()
    b3.load(_sample(now), now=now)
    b3.prune(keep=set(), now=now)
    assert len(b3.by_sym) == n0, "keep rong khong duoc xoa sach so tay"
    b3.prune(now=now + WINDOW_H * 3600 + 60)
    assert not b3.by_sym, "qua 4 gio thi khong con tin nao"
    print("  keep rong -> giu nguyen | qua 4 gio -> sach")

    # --- chiu loi ---
    assert parse("{khong phai json}") == []
    assert parse("") == [] and parse(None) == [] and parse({}) == []
    assert parse({"news": [None, {}, {"headline": "x"}]}) == []
    print("  JSON hong / thieu truong -> [] , khong nem loi")

    global fetch
    realf, fetch = fetch, lambda *a, **k: (_ for _ in ()).throw(OSError("no net"))
    try:
        b4 = NewsBook()
        b4.load(_sample(now), now=now)
        assert b4.refresh() == -1 and b4.n_fail == 1
        assert b4.items("WETO", now), "loi mang khong duoc xoa tin cu"
        print(f"  refresh() loi -> -1, giu {len(b4.by_sym)} ma ({b4.err})")
    finally:
        fetch = realf
    print("\nselftest OK (khong can mang)")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                            # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="goi that vao Alpaca")
    a = ap.parse_args()

    if not a.live:
        _selftest()
        raise SystemExit(0)

    if not (KEY and SECRET):
        print("[X] thieu ALPACA_KEY / ALPACA_SECRET trong .env")
        raise SystemExit(1)
    b = NewsBook()
    n = b.refresh()
    if n < 0:
        print(f"[X] khong lay duoc tin: {b.err}")
        raise SystemExit(1)
    print(f"{n} tin trong {WINDOW_H} gio qua, {len(b.by_sym)} ma\n")
    print(f"{'SYM':<8}{'PHUT':>5}  {'NHOM':<10} TIEU DE")
    rows = sorted(({"s": s, **(b.view(s) or {})} for s in b.by_sym
                   if b.view(s) and b.view(s)["n"]),
                  key=lambda r: (-r["risk"], r["age"]))
    for r in rows:
        print(f"{r['s']:<8}{r['age']:>5}  {(r['group'] or '-'):<10} "
              f"{r['headline'][:70]}")
    if b.risky_syms():
        print(f"\nTin xau (tru diem): {', '.join(b.risky_syms())}")
