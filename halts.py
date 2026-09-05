"""halts.py - Feed tam dung giao dich (trading halt) cua Nasdaq.

Vi sao can: mot ma +85% voi RVOL 60x rat co the DANG BI HALT. Alert cho ma
dang halt la alert vo dung - khong mua duoc, va khi mo lai gia da nhay cho
khac. Nguoc lai, ma vua resume sau halt T2 (tin da ra) lai la tinh huong dang
chu y nhat trong ca ngay.

Nguon mien phi, khong can key:
    https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts
Nasdaq ghi ro: KHONG query qua 1 lan/phut -> POLL = 60.

Feed la RSS, moi <item> co cac the rieng cua Nasdaq (namespace ndaq:).
Module doc theo TEN THE khong ke namespace: Nasdaq co the doi URI namespace
ma khong bao truoc, va do la kieu thay doi lam chet feed mot cach im lang.

ResumptionTradeTime trong = CHUA MO LAI. Do la truong quan trong nhat.

    python halts.py           # test parser bang mau co san, khong can mang
    python halts.py --live    # goi that vao Nasdaq, in cac ma dang halt
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
import xml.etree.ElementTree as ET_
from zoneinfo import ZoneInfo

try:
    ET = ZoneInfo("America/New_York")
except Exception:                                                # noqa: BLE001
    ET = None      # thieu goi `tzdata` (Windows chay python he thong, khong venv)

log = print          # main.py co the gan lai: halts.log = log

URL = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
POLL = 60            # giay - gioi han cua Nasdaq
STALE = 300          # feed cu hon the nay -> coi nhu khong biet gi
MAX_AGE_H = 12       # halt cu hon the nay khong con tinh la dang halt
JUST_RESUMED = 300   # vua mo lai trong 5 phut -> dang chu y

# code -> (ly do ngan, do nghiem trong 0-3, giai thich)
# `label` la LY DO, khong chua chu "halt": render.py da co san chu
# "TẠM DỪNG GIAO DỊCH" o dau dong nen ghep vao se thanh trung y.
# sev 3 = chan alert. sev 2 = dung vao. sev 1 = biet de cho.
REASONS: dict[str, tuple[str, int, str]] = {
    "LUDP": ("BIẾN ĐỘNG (LULD)", 1,
             "Giá chạy quá dải LULD. Thường tự mở lại sau 5 phút."),
    "LUDS": ("BIẾN ĐỘNG (LULD, KẸP DẢI)", 1,
             "Giá kẹp ngoài dải LULD. Mở lại có thể chậm hơn 5 phút."),
    "T1": ("CHỜ CÔNG BỐ TIN", 2,
           "Tin CHƯA ra. Không ai biết giá mở lại ở đâu — đừng đoán."),
    "T2": ("TIN ĐÃ CÔNG BỐ", 1,
           "Tin đã ra, vẫn đang dừng. Sắp mở lại — đọc tin trước khi vào."),
    "T5": ("BIẾN ĐỘNG MỘT MÃ", 1, "Sàn tạm dừng riêng mã này do biến động."),
    "T6": ("HOẠT ĐỘNG BẤT THƯỜNG", 2,
           "Sàn dừng vì giao dịch bất thường — thường là dấu hiệu xấu."),
    "T8": ("ETF", 1, "Dừng giao dịch ETF."),
    "T12": ("CHỜ CÔNG TY GIẢI TRÌNH", 3,
            "Sàn/SEC đã yêu cầu giải trình và đang chờ trả lời."),
    "H4": ("VI PHẠM QUY ĐỊNH NIÊM YẾT", 3, "Không tuân thủ quy định niêm yết."),
    "H9": ("CHẬM BÁO CÁO", 3,
           "Chưa nộp báo cáo theo quy định. Rủi ro hủy niêm yết."),
    "H10": ("SEC ĐÌNH CHỈ GIAO DỊCH", 3,
            "SEC đình chỉ — thường đi kèm nghi vấn gian lận. Tránh hoàn toàn."),
    "H11": ("LO NGẠI TỪ CƠ QUAN QUẢN LÝ", 3, "Dừng vì lo ngại pháp lý."),
    "D": ("HỦY NIÊM YẾT", 3, "Mã bị xóa khỏi sàn."),
    "IPO1": ("IPO CHƯA MỞ", 0, "Mã IPO chưa giao dịch lần đầu."),
    "IPOQ": ("IPO ĐANG MỞ BÁO GIÁ", 0, "Mã IPO đang mở báo giá."),
    "M": ("HÀNH ĐỘNG DOANH NGHIỆP", 1, "Dừng do hành động doanh nghiệp."),
    "MWC1": ("NGẮT MẠCH THỊ TRƯỜNG (MỨC 1)", 2, "Cả thị trường dừng, không riêng mã này."),
    "MWC2": ("NGẮT MẠCH THỊ TRƯỜNG (MỨC 2)", 2, "Cả thị trường dừng, không riêng mã này."),
    "MWC3": ("NGẮT MẠCH THỊ TRƯỜNG (MỨC 3)", 3, "Nghỉ hết phiên."),
    "MWCQ": ("THỊ TRƯỜNG ĐANG MỞ LẠI", 1, "Mở lại sau ngắt mạch toàn thị trường."),
    "R1": ("CHƯA ĐỦ ĐIỀU KIỆN GIAO DỊCH", 2, "Mã chưa đủ điều kiện giao dịch."),
    "R4": ("CHƯA ĐỦ ĐIỀU KIỆN GIAO DỊCH", 2, "Mã chưa đủ điều kiện giao dịch."),
    "R9": ("CHƯA ĐỦ ĐIỀU KIỆN GIAO DỊCH", 2, "Mã chưa đủ điều kiện giao dịch."),
}
BLOCK_SEV = 3        # sev >= muc nay -> main.py khong gui alert


def reason(code: str) -> tuple[str, int, str]:
    """Code la khong ro -> sev 2: khong biet ly do thi coi nhu dang nghi ngo."""
    return REASONS.get((code or "").upper().strip(),
                       (f"MÃ LÝ DO {code or '?'}", 2,
                        "Mã lý do lạ, chưa có trong bảng."))


# ───────────────────────── parse ─────────────────────────
def _tag(el) -> str:
    """Ten the, bo namespace. Nasdaq doi URI namespace la chuyen tung xay ra."""
    return el.tag.rpartition("}")[2].lower()


def _epoch(day: str, tm: str) -> int | None:
    """'09/04/2026' + '15:42:00' (gio ET) -> epoch. Thieu/sai -> None."""
    day, tm = (day or "").strip(), (tm or "").strip()
    if not day or not tm:
        return None
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M"):
        try:
            n = dt.datetime.strptime(f"{day} {tm}", fmt)
        except ValueError:
            continue
        return int((n.replace(tzinfo=ET) if ET else n.astimezone()).timestamp())
    return None


def parse(xml: str, now: float | None = None) -> dict[str, dict]:
    """XML feed -> {sym: ban ghi halt}. Moi ma giu ban ghi MOI NHAT.

    Khong nem loi: feed hong thi tra ve rong, bot van phai chay.
    """
    t = now if now is not None else time.time()
    try:
        root = ET_.fromstring(xml)
    except Exception as e:                                       # noqa: BLE001
        log(f"halts.parse: XML hong: {type(e).__name__}: {e}")
        return {}

    out: dict[str, dict] = {}
    for item in root.iter():
        if _tag(item) != "item":
            continue
        f = {_tag(c): (c.text or "").strip() for c in item}
        sym = (f.get("issuesymbol") or "").upper()
        if not sym:
            continue
        h_ts = _epoch(f.get("haltdate", ""), f.get("halttime", ""))
        r_day = f.get("resumptiondate") or f.get("haltdate", "")
        r_ts = _epoch(r_day, f.get("resumptiontradetime", ""))
        q_ts = _epoch(r_day, f.get("resumptionquotetime", ""))
        code = (f.get("reasoncode") or "").upper()
        label, sev, note = reason(code)
        rec = {
            "sym": sym, "code": code, "label": label, "sev": sev, "note": note,
            "name": f.get("issuename", ""), "market": f.get("market", ""),
            "halt_ts": h_ts, "resume_ts": r_ts, "quote_ts": q_ts,
            # Con dang halt: chua co gio KHOP LENH lai, va halt khong qua cu.
            "active": (r_ts is None and h_ts is not None
                       and t - h_ts < MAX_AGE_H * 3600),
        }
        cur = out.get(sym)
        if cur is None or (h_ts or 0) >= (cur["halt_ts"] or 0):
            out[sym] = rec
    return out


def fetch(timeout: float = 15.0) -> str:
    import httpx
    r = httpx.get(URL, timeout=timeout, follow_redirects=True,
                  headers={"User-Agent": "scanner/1.0"})
    r.raise_for_status()
    return r.text


# ───────────────────────── so tay halt ─────────────────────────
class HaltBook:
    """Giu trang thai halt trong bo nho. Mot instance dung chung cho ca bot.

    Khong tu goi mang trong lop: main.py quyet dinh khi nao refresh (vong
    async, chay trong to_thread). Nho vay test duoc bang parse() offline.
    """

    def __init__(self) -> None:
        self.by_sym: dict[str, dict] = {}
        self.ts: float = 0.0          # lan refresh thanh cong gan nhat
        self.err: str = ""
        self.n_fail = 0

    # -- cap nhat --
    def load(self, xml: str, now: float | None = None) -> int:
        self.by_sym = parse(xml, now)
        self.ts = now if now is not None else time.time()
        self.err, self.n_fail = "", 0
        return len(self.by_sym)

    def refresh(self) -> int:
        """Goi mang + parse. Tra ve so ban ghi, -1 neu loi."""
        try:
            return self.load(fetch())
        except Exception as e:                                   # noqa: BLE001
            self.n_fail += 1
            self.err = f"{type(e).__name__}: {e}"
            # Khong xoa by_sym: du lieu cu 2 phut van tot hon khong co gi.
            return -1

    # -- tra cuu --
    @property
    def stale(self) -> bool:
        return not self.ts or (time.time() - self.ts) > STALE

    def get(self, sym: str) -> dict | None:
        return self.by_sym.get((sym or "").upper())

    def is_halted(self, sym: str) -> bool:
        r = self.get(sym)
        return bool(r and r["active"])

    def just_resumed(self, sym: str, within: int = JUST_RESUMED,
                     now: float | None = None) -> bool:
        r = self.get(sym)
        if not r or not r["resume_ts"]:
            return False
        t = now if now is not None else time.time()
        return 0 <= t - r["resume_ts"] <= within

    def view(self, sym: str, now: float | None = None) -> dict | None:
        """Ban ghi da chuan bi cho render, hoac None neu ma nay khong lien quan.

        Tra ve None khi: khong co ban ghi, feed qua cu, hoac halt da xong tu
        lau (khong con la thong tin). Nho vay render.py chi can kiem tra None.
        """
        r = self.get(sym)
        if not r or self.stale:
            return None
        t = now if now is not None else time.time()
        resumed = bool(r["resume_ts"] and 0 <= t - r["resume_ts"] <= JUST_RESUMED)
        if not r["active"] and not resumed:
            return None
        return {**r, "resumed": resumed,
                "since": _hhmm(r["halt_ts"]),
                "until": _hhmm(r["resume_ts"]),
                "quote": _hhmm(r["quote_ts"])}

    def blocked(self, sym: str) -> dict | None:
        """Ban ghi khien alert bi chan hoan toan (sev >= BLOCK_SEV), neu co."""
        r = self.view(sym)
        return r if (r and r["sev"] >= BLOCK_SEV and not r["resumed"]) else None

    def active_syms(self) -> list[str]:
        return sorted(s for s, r in self.by_sym.items() if r["active"])


def _hhmm(ts: int | None) -> str:
    if not ts:
        return ""
    d = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
    return (d.astimezone(ET) if ET else d.astimezone()).strftime("%H:%M")


# ───────────────────────── test offline ─────────────────────────
_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:ndaq="http://www.nasdaqtrader.com/">
 <channel>
  <title>Nasdaq Trader Trade Halts</title>
  <item>
   <ndaq:IssueSymbol>WETO</ndaq:IssueSymbol>
   <ndaq:IssueName>Weto Inc Common Stock</ndaq:IssueName>
   <ndaq:Market>NASDAQ</ndaq:Market>
   <ndaq:ReasonCode>LUDP</ndaq:ReasonCode>
   <ndaq:HaltDate>{d}</ndaq:HaltDate>
   <ndaq:HaltTime>{t1}</ndaq:HaltTime>
   <ndaq:ResumptionDate></ndaq:ResumptionDate>
   <ndaq:ResumptionQuoteTime></ndaq:ResumptionQuoteTime>
   <ndaq:ResumptionTradeTime></ndaq:ResumptionTradeTime>
  </item>
  <item>
   <ndaq:IssueSymbol>NEWSY</ndaq:IssueSymbol>
   <ndaq:ReasonCode>T2</ndaq:ReasonCode>
   <ndaq:HaltDate>{d}</ndaq:HaltDate>
   <ndaq:HaltTime>{t1}</ndaq:HaltTime>
   <ndaq:ResumptionDate>{d}</ndaq:ResumptionDate>
   <ndaq:ResumptionTradeTime>{t2}</ndaq:ResumptionTradeTime>
  </item>
  <item>
   <ndaq:IssueSymbol>SCAMZ</ndaq:IssueSymbol>
   <ndaq:ReasonCode>H10</ndaq:ReasonCode>
   <ndaq:HaltDate>{d}</ndaq:HaltDate>
   <ndaq:HaltTime>{t1}</ndaq:HaltTime>
   <ndaq:ResumptionTradeTime></ndaq:ResumptionTradeTime>
  </item>
  <item>
   <ndaq:IssueSymbol>OLDIE</ndaq:IssueSymbol>
   <ndaq:ReasonCode>T1</ndaq:ReasonCode>
   <ndaq:HaltDate>{d0}</ndaq:HaltDate>
   <ndaq:HaltTime>10:00:00</ndaq:HaltTime>
   <ndaq:ResumptionTradeTime></ndaq:ResumptionTradeTime>
  </item>
 </channel>
</rss>"""


def _sample(now: float) -> str:
    """Mau voi moc thoi gian tuong doi so voi `now` -> test khong bao gio het han.

    Dung dung mui gio ma _epoch() se gia dinh khi doc lai (ET, hoac local neu
    thieu tzdata) - lech mui gio o day lam test fail ma code that thi dung.
    """
    n = dt.datetime.fromtimestamp(now, ET)
    h = n - dt.timedelta(minutes=20)
    r = n - dt.timedelta(minutes=2)         # NEWSY vua mo lai 2 phut truoc
    old = n - dt.timedelta(days=3)
    return _SAMPLE.format(d=f"{h:%m/%d/%Y}", t1=f"{h:%H:%M:%S}",
                          t2=f"{r:%H:%M:%S}", d0=f"{old:%m/%d/%Y}")


def _selftest() -> None:
    now = time.time()
    b = HaltBook()
    n = b.load(_sample(now), now=now)
    print(f"parse: {n} ban ghi | dang halt: {b.active_syms()}")
    assert n == 4, n

    v = b.view("WETO", now)
    assert v and v["active"] and not v["resumed"], v
    print(f"  WETO  {v['label']} · tu {v['since']} ET · sev {v['sev']}")

    v = b.view("NEWSY", now)
    assert v and v["resumed"] and not v["active"], v
    print(f"  NEWSY {v['label']} · vua mo lai {v['until']} ET · sev {v['sev']}")
    assert b.just_resumed("NEWSY", now=now)

    v = b.blocked("SCAMZ")
    assert v and v["code"] == "H10", v
    print(f"  SCAMZ {v['label']} -> CHAN ALERT (sev {v['sev']})")

    assert b.view("OLDIE", now) is None, "halt 3 ngay truoc khong con la tin"
    assert b.view("AAPL", now) is None, "ma khong co trong feed"
    assert b.blocked("WETO") is None, "LUDP khong chan alert"
    print("  OLDIE (3 ngay truoc) -> bo qua | AAPL -> khong co ban ghi")

    # Feed cu hon STALE -> khong tin nua, tra None het.
    b.ts = now - STALE - 1
    assert b.stale and b.view("WETO", now) is None
    print(f"  feed cu > {STALE}s -> view() tra None (khong doan bua)")

    # XML hong -> rong, khong nem loi.
    assert parse("<rss><channel><item>") == {}
    assert parse("") == {}
    print("  XML hong / rong -> {} , khong nem loi")

    # Mang loi -> giu du lieu cu, tra -1. Thay fetch() de khong goi mang that.
    global fetch
    real, fetch = fetch, lambda *a, **k: (_ for _ in ()).throw(OSError("no net"))
    try:
        b3 = HaltBook()
        b3.load(_sample(now), now=now)
        assert b3.refresh() == -1 and b3.n_fail == 1
        assert b3.get("WETO"), "loi mang khong duoc xoa du lieu cu"
        print(f"  refresh() loi -> -1, giu {len(b3.by_sym)} ban ghi cu "
              f"({b3.err})")
    finally:
        fetch = real
    print("\nselftest OK (khong can mang)")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                            # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="goi that vao Nasdaq")
    a = ap.parse_args()

    if not a.live:
        _selftest()
        raise SystemExit(0)

    b = HaltBook()
    n = b.refresh()
    if n < 0:
        print(f"[X] khong lay duoc feed: {b.err}")
        raise SystemExit(1)
    print(f"{n} ban ghi trong feed | dang halt: {len(b.active_syms())} ma\n")
    print(f"{'SYM':<8}{'CODE':<7}{'HALT':>6}{'RESUME':>8}  NHAN")
    for s in sorted(b.by_sym, key=lambda k: -(b.by_sym[k]["halt_ts"] or 0)):
        r = b.by_sym[s]
        print(f"{s:<8}{r['code']:<7}{_hhmm(r['halt_ts']):>6}"
              f"{_hhmm(r['resume_ts']) or '-':>8}  {r['label']}"
              f"{'  [DANG HALT]' if r['active'] else ''}")
