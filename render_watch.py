"""render_watch.py - tin nhan TRONG PHIEN (Telegram, HTML parse mode).

Thuan ham: khong network, khong DB -> test bang dict gia.
    python render_watch.py            # in ra cac tin nhan mau

BA LOAI TIN, VA CHI BA LOAI
---------------------------
    render_open()     mot lan luc mo phien: hom nay canh gi, va vi sao
    render_alert()    mot canh bao Tier 1 - mot ma, mot muc gia
    render_summary()  mot lan cuoi phien: da canh gi, va con gi dang mo

Tach khoi render.py va render_night.py vi ba thu khac nhau ve muc dich:
render.py la mot alert CHAM DIEM cua he thong cu (co ban nut, co link EDGAR,
co diem so), render_night.py la mot BAO CAO hang ngay. O day thi mot canh bao
chi phai tra loi dung mot cau: "gia da cham mot muc BAN DA CHON tu toi qua".

⚠️ KHONG CO CON SO NAO O DAY DUOC TINH MOI. Toan bo diem vao / cat lo / muc
tieu / co vi the den tu ke hoach da chot tu dem truoc (plan.py). Neu module nay
tinh lai bat ky con so nao thi tin nhan se khac ke hoach, va luc do khong con
biet nen tin cai nao - tuc la mat dung cai thu prompt 2 muon co: "toi khong ung
bien giua phien, toi thuc hien mot quyet dinh da lam tu toi qua".

⚠️ MOI CANH BAO PHAI CO DAU MOC BAO GIA VA DO TRE. Nguon mien phi tre ~15 phut.
Mot dong "gia vua cham 102.00" khong kem do tre se lam nguoi doc dat lenh o mot
muc da di qua. Bot khong xoa duoc do tre - no chi noi that ve do tre.

Dung lai tu render.py: esc(), SAFE_LEN, fit(), degrade(). Ba quy uoc cua
render_night.py con nguyen gia tri o day:
  1. Panel <pre> CHI ASCII (font monospace cua Telegram khong co glyph co dau).
  2. Toi gian emoji: mot icon o header, mot dau ⚠️ cho khoi canh bao.
  3. Khoi quan trong nhat khong bao gio bi cat truoc khoi phu (thu tu P_*).
"""
from __future__ import annotations

import datetime as dt
import unicodedata
from dataclasses import dataclass, field

import positions
import quotes
import render
from render import esc

# ───────────────────────── uu tien khoi ─────────────────────────
P_HEAD = 9
P_MODE = 8          # che do phien: quyet dinh co vao lenh hay khong
P_POS = 7           # vi the dang mo - cai duy nhat co the mat tien hom nay
P_LIST = 6
P_SKIP = 5          # nhung ma KHONG kiem duoc: phai thay, khong duoc im
P_WARN = 4
P_FOOT = 2

# Mot icon moi tin. Theo LUAT chu khong theo muc do "vui": mot canh bao cat lo
# va mot canh bao vao lenh phai phan biet duoc tu man hinh khoa dien thoai.
ICON = {"stop": "🛑", "trigger": "🎯", "gap": "⚡"}
TITLE = {"stop": "XUYÊN MỨC CẮT LỖ", "trigger": "CHẠM ĐIỂM VÀO",
         "gap": "GAP MỞ CỬA"}

MODE_ICON = {"full": "🟢", "revert": "🟡", "manage": "🟡", "stop_only": "🔴"}
MODE_VI = {
    "full": "vào lệnh bình thường",
    "revert": "chỉ mua hồi về vùng hỗ trợ (mean-reversion)",
    "manage": "chỉ quản lý vị thế đang có, không vào lệnh mới",
    "stop_only": "chỉ canh cắt lỗ — không một cảnh báo vào lệnh nào",
}
# Cai gi duoc canh trong tung che do. Viet bang tieng Viet o day de nguoi doc
# khong phai doi chieu voi watch.RULES_BY_MODE.
MODE_RULES_VI = {
    "full": "chạm điểm vào · xuyên cắt lỗ · gap mở cửa",
    "revert": "chạm điểm vào · xuyên cắt lỗ · gap mở cửa",
    "manage": "xuyên cắt lỗ · gap của mã đang giữ",
    "stop_only": "xuyên cắt lỗ của vị thế đang mở",
}


# ───────────────────────── so lieu -> chu ─────────────────────────
def _pct(v, d: int = 1, sign: bool = False) -> str:
    if v is None:
        return "-"
    return f"{v * 100:+.{d}f}%" if sign else f"{v * 100:.{d}f}%"


def _px(v) -> str:
    n = quotes._num(v)
    return "-" if n is None else f"{n:.2f}"


def _dmy(d: str | None) -> str:
    if not d or len(d) < 10:
        return "-"
    return f"{d[8:10]}/{d[5:7]}/{d[0:4]}"


def _pre(lines: list[str]) -> str:
    return "<pre>" + "\n".join(esc(x) for x in lines) + "</pre>"


def _et_hm(ts) -> tuple[str, str]:
    """Dau moc ISO -> ("15:42", "ET"). Khong doi duoc mui gio thi noi that.

    Nhan "ET" la mot KHANG DINH: neu khong co bang mui gio (Windows thieu goi
    `tzdata`) thi gio hien ra la UTC, va dan nhan ET vao do la lech 4-5 gio o
    dung cho nguoi doc khong the kiem chung.
    """
    s = str(ts or "").strip()
    if not s:
        return ("-", "")
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return (s[:16].replace("T", " "), "")
    tz = quotes._et()
    if tz is not None:
        d = d.astimezone(tz)
        return (d.strftime("%H:%M"), "ET")
    d = d.astimezone(dt.UTC)
    return (d.strftime("%H:%M"), "UTC")


def _stamp(a: dict, src: str = "") -> str:
    """Dong dau moc + do tre. Co trong MOI canh bao, khong co ngoai le."""
    hm, tz = _et_hm(a.get("ts"))
    bits = [f"báo giá {hm}" + (f" {tz}" if tz else ""), quotes.fmt_age(a.get("age_sec"))]
    if src:
        bits.append(f"nguồn {src}")
    return "<i>" + esc(" · ".join(bits)) + "</i>"


# ───────────────────────── du lieu vao ─────────────────────────
@dataclass
class WatchView:
    """Tat ca thu ba tin nhan can. Khong co gi doc DB hay mang o day."""

    day: str
    gate: dict = field(default_factory=dict)      # watchlist.gate()
    watch: list[dict] = field(default_factory=list)
    pos: dict | None = None                       # positions.load()
    skip: list[tuple] = field(default_factory=list)
    warn: list[str] = field(default_factory=list)
    alerts: list[dict] = field(default_factory=list)   # watch.today_rows()
    src: str = ""
    sess: str = ""                                # clock.describe()
    url: str = ""
    dry: bool = False


# ───────────────────────── 1. tin mo phien ─────────────────────────
def render_mode(v: WatchView) -> list[str]:
    g = v.gate or {}
    mode = g.get("mode") or "stop_only"
    body = [f"Bối cảnh: <b>{esc(g.get('why') or 'chưa đọc được')}</b>",
            f"Hôm nay: <b>{esc(MODE_VI.get(mode, mode))}</b>",
            f"<i>Sẽ cảnh báo: {esc(MODE_RULES_VI.get(mode, '-'))}</i>"]
    if mode == "stop_only":
        # Day la tin DUY NHAT cua ca phien trong truong hop nay. No phai noi ro
        # rang im lang sau do la CO Y, khong phai bot chet.
        body.append("Sau tin này sẽ <u>im lặng</u> trừ khi một vị thế đang mở "
                    "xuyên mức cắt lỗ. Im lặng là đúng, không phải là lỗi.")
    return ["<b>CHẾ ĐỘ PHIÊN</b>",
            "<blockquote>" + "\n".join(body) + "</blockquote>"]


def render_list(v: WatchView) -> list[str]:
    n = len(v.watch)
    if not n:
        return ["<b>DANH SÁCH THEO DÕI</b>",
                "<blockquote>Tối qua không mã nào qua hết sàn chất lượng, nên "
                "hôm nay <u>không có mã nào để canh vào lệnh</u>. Vị thế đang "
                "mở vẫn được canh cắt lỗ bình thường.</blockquote>"]
    head = f"{'SYM':<6}{'VAO':>8}{'STOP':>8}{'MUCTIEU':>9}{'CO':>5}"
    lines = [head, "-" * len(head)]
    for c in v.watch:
        sz = c.get("size_pct")
        lines.append(f"{str(c.get('sym', '?')):<6}{_px(c.get('trigger')):>8}"
                     f"{_px(c.get('stop')):>8}{_px(c.get('target')):>9}"
                     f"{(f'{sz:.0%}' if sz is not None else '-'):>5}")
    out = [f"<b>DANH SÁCH THEO DÕI ({n})</b>", _pre(lines)]
    out.append("<i>Các mốc này chốt từ nến đã đóng tối qua và "
               "<u>không đổi trong phiên</u>.</i>")
    return out


def render_pos(v: WatchView) -> list[str]:
    """Vi the dang mo. KHOI QUAN TRONG NHAT cua tin mo phien.

    Day la cho duy nhat phan biet duoc "khong co vi the nao" voi "khong doc
    duoc danh sach vi the". Hai truong hop do cho ra cung mot hanh vi (khong
    canh stop) nhung mot cai la binh thuong va mot cai la he thong dang hong.
    """
    p = v.pos
    if p is None:
        return []
    body = [esc(p.get("note") or "")]
    if p.get("known") and p.get("n"):
        xem = positions.watched(p)
        if xem:
            body.append(f"Đang canh cắt lỗ: <b>{esc(', '.join(xem))}</b>")
    # Ma bi dung ngoai phai hien ra kem LY DO. "Khong canh" ma khong noi thi
    # doc thanh "khong co gi dang lo".
    for sym, why in positions.unchecked(p):
        body.append(f"⚠️ <b>{esc(sym)}</b>: {esc(why)} → <u>không canh</u>")
    for wr in (p.get("warn") or []):
        body.append(f"· {esc(wr)}")
    return ["<b>VỊ THẾ ĐANG MỞ</b>",
            "<blockquote>" + "\n".join(x for x in body if x) + "</blockquote>"]


def render_skip(v: WatchView) -> list[str]:
    """Nhung ma KHONG kiem duoc trong phien nay, kem ly do.

    Mot ma bi bo qua ma khong ai biet la cach de nhat de mot bo loc hong nam im
    ca thang. Mot ma bi bo qua CO ghi chu thi lan sau con nguoi doc de y."""
    if not v.skip:
        return []
    # Gop theo ma: mot ma co the co hai ly do (khong tinh duoc gap + mo cua tren
    # diem vao), va hai dong cho cung mot ma doc nhu hai van de.
    gop: dict[str, list[str]] = {}
    for sym, why in v.skip:
        gop.setdefault(str(sym), [])
        if why not in gop[str(sym)]:
            gop[str(sym)].append(str(why))
    rows = [f"<b>{esc(s)}</b>: {esc('; '.join(w))}" for s, w in sorted(gop.items())]
    return [f"⚠️ <b>Không kiểm được ({len(gop)} mã)</b>",
            "<blockquote>" + "\n".join(rows) + "</blockquote>"]


def render_warn(v: WatchView) -> list[str]:
    ws = [w for w in v.warn if w]
    if not ws:
        return []
    return ["⚠️ <b>Cần để ý</b>",
            "<blockquote>" + "\n".join(f"· {esc(w)}" for w in ws) + "</blockquote>"]


def render_foot(v: WatchView) -> list[str]:
    bits = [b for b in (v.sess, f"nguồn {v.src}" if v.src else "") if b]
    out = [f"<i>{esc(' · '.join(bits))}</i>"] if bits else []
    if v.url:
        out.append(f'<a href="{esc(v.url)}">Xem bảng điều khiển →</a>')
    return out


def open_blocks(v: WatchView) -> list[tuple[int, list[str]]]:
    mode = (v.gate or {}).get("mode") or "stop_only"
    ico = MODE_ICON.get(mode, "🟡")
    head = [f"{ico} <b>MỞ PHIÊN · {_dmy(v.day)}</b>"]
    if v.dry:
        head.append("<i>Chạy thử (--dry-run): không gửi cảnh báo nào.</i>")
    out = [(P_HEAD, head), (P_MODE, render_mode(v))]
    for pri, fn in ((P_POS, render_pos), (P_LIST, render_list),
                    (P_SKIP, render_skip), (P_WARN, render_warn),
                    (P_FOOT, render_foot)):
        if (blk := fn(v)):
            out.append((pri, blk))
    return out


def render_open(v: WatchView) -> str:
    return unicodedata.normalize("NFC", render.fit(open_blocks(v)))


# ───────────────────────── 2. mot canh bao ─────────────────────────
def _plan_panel(f: dict) -> str:
    """Bang ke hoach. Cac con so lay NGUYEN VAN tu ke hoach dem qua."""
    lines = [f"{'VAO':<9}{_px(f.get('trigger')):>9}",
             f"{'STOP':<9}{_px(f.get('stop')):>9}"
             + (f"   {_pct(-(f['stop_pct']), 1, sign=True)}"
                if f.get("stop_pct") else ""),
             f"{'MUCTIEU':<9}{_px(f.get('target')):>9}"]
    sz, rk = f.get("size_pct"), f.get("risk_pct")
    if sz is not None:
        lines.append(f"{'CO':<9}{sz * 100:>8.0f}%"
                     + (f"   rui ro {rk * 100:.1f}% tai khoan" if rk else ""))
    return _pre(lines)


def _stop_panel(a: dict) -> str:
    f = a.get("fields") or {}
    lines = [f"{'GIA':<9}{_px(a.get('px')):>9}",
             f"{'STOP':<9}{_px(f.get('hit')):>9}"]
    if (sh := quotes._num(f.get("shares"))) is not None:
        acc = ", ".join(str(x) for x in (f.get("accts") or []))
        lines.append(f"{'SO CP':<9}{sh:>9.0f}" + (f"   ({acc})" if acc else ""))
    if (f.get("n_hit") or 0) > 1:
        lines.append(f"{'BI XUYEN':<9}{f['n_hit']:>9} muc")
    return _pre(lines)


def render_alert(a: dict, src: str = "", url: str = "",
                 pos: dict | None = None) -> str:
    """Mot canh bao Tier 1 -> mot tin nhan.

    Mot tin mot canh bao, co y: tin nhan gom nhieu ma phai doc het moi biet ma
    nao can lam gi, va Tier 1 la nhung thu can lam ngay.
    """
    rule = a.get("rule", "?")
    blk: list[tuple[int, list[str]]] = [(P_HEAD, [
        f"{ICON.get(rule, '🔔')} <b>{esc(a.get('sym', '?'))} · "
        f"{esc(TITLE.get(rule, rule.upper()))}</b>",
        _stamp(a, src),
    ])]
    blk.append((P_MODE, [f"<blockquote>{esc(a.get('detail') or '')}</blockquote>"]))

    f = a.get("fields") or {}
    if rule == "stop":
        blk.append((P_POS, [_stop_panel(a)]))
        note = ["Mức cắt lỗ lấy từ ảnh chụp danh mục của app, không phải lệnh "
                "stop thật ở sàn — nếu chưa đặt lệnh thì phải tự đặt."]
        if pos and pos.get("old"):
            # Anh chup cu VAN dung (khoa duoc ghi theo su kien), nhung neu vua
            # doi stop ma chua luu app thi con so tren day la so cu.
            note.append(f"⚠️ App cập nhật lần cuối {pos.get('age_h') or 0:.0f} "
                        "giờ trước — nếu vừa đổi mức cắt lỗ thì mở app lưu lại.")
        blk.append((P_WARN, ["<blockquote>" + "\n".join(esc(x) for x in note)
                             + "</blockquote>"]))
    else:
        blk.append((P_LIST, [_plan_panel(f)]))
        if rule == "gap":
            g = f.get("gap") or 0
            blk.append((P_WARN, ["<blockquote>" + esc(
                "Gap thì kế hoạch tối qua có thể không còn dùng được: "
                + ("giá đã bỏ điểm vào lại phía sau, đuổi theo là mua ở chỗ "
                   "rủi ro đã khác." if g > 0 else
                   "khoảng cách tới mức cắt lỗ đã thu hẹp, cỡ vị thế tính tối "
                   "qua không còn đúng.")) + "</blockquote>"]))
        else:
            bits = ["Kế hoạch chốt từ nến đã đóng tối qua, không tính lại trong "
                    "phiên. Cỡ là con số cuối cùng — đừng nhân thêm hệ số nào."]
            if f.get("manual"):
                bits.append("Mã này do bạn thêm tay vào danh sách.")
            for u in (f.get("unsure") or []):
                bits.append(f"⚠️ {u}")
            blk.append((P_WARN, ["<blockquote>"
                                 + "\n".join(esc(x) for x in bits)
                                 + "</blockquote>"]))
    if url:
        blk.append((P_FOOT, [f'<a href="{esc(url)}">Xem bảng điều khiển →</a>']))
    return unicodedata.normalize("NFC", render.fit(blk))


# ───────────────────────── 3. tong ket cuoi phien ─────────────────────────
def render_summary(v: WatchView) -> str:
    n = len(v.alerts)
    head = [f"{'🌙' if n == 0 else '📋'} <b>TỔNG KẾT PHIÊN · "
            f"{_dmy(v.day)}</b>"]
    blk: list[tuple[int, list[str]]] = [(P_HEAD, head)]

    if not n:
        blk.append((P_MODE, ["<blockquote>Không có cảnh báo nào hôm nay. Đây là "
                             "trạng thái mặc định của thiết kế: chỉ báo khi giá "
                             "chạm đúng một mốc đã chọn từ tối qua."
                             "</blockquote>"]))
    else:
        head_p = f"{'GIO':<6}{'SYM':<6}{'LUAT':<9}{'GIA':>8}"
        lines = [head_p, "-" * len(head_p)]
        for r in v.alerts:
            hm, _ = _et_hm(r.get("ts_utc"))
            lines.append(f"{hm:<6}{str(r.get('sym', '?')):<6}"
                         f"{str(r.get('rule', '?')):<9}{_px(r.get('px')):>8}")
        blk.append((P_MODE, [f"<b>{n} cảnh báo</b>", _pre(lines)]))

    if (p := render_pos(v)):
        blk.append((P_POS, p))
    if (s := render_skip(v)):
        blk.append((P_SKIP, s))
    if (w := render_warn(v)):
        blk.append((P_WARN, w))
    if (ft := render_foot(v)):
        blk.append((P_FOOT, ft))
    return unicodedata.normalize("NFC", render.fit(blk))


# ───────────────────────── mau / selftest ─────────────────────────
def _demo() -> list[tuple[str, str]]:
    day = "2026-09-25"
    ts = "2026-09-25T15:42:00+00:00"
    pos = positions.parse(
        {"rows": [{"sym": "AAPL", "shares": 40, "avgCost": 210.0, "cur": "USD",
                   "stops": [198.0], "accts": ["IBKR"]},
                  {"sym": "ASML", "shares": 5, "avgCost": 700.0, "cur": "EUR",
                   "stops": [640.0], "accts": ["DEGIRO"]}],
         "warn": []}, int(dt.datetime.now(dt.UTC).timestamp() * 1000) - 3_600_000)
    watch = [{"sym": "NVDA", "trigger": 102.0, "stop": 97.0, "target": 112.0,
              "size_pct": 0.2},
             {"sym": "MSFT", "trigger": 431.5, "stop": 418.0, "target": 458.0,
              "size_pct": 0.1}]
    v = WatchView(day=day, gate={"mode": "full", "why": "UPTREND · NORMAL → "
                                 "vào lệnh bình thường"},
                  watch=watch, pos=pos, src="yf",
                  skip=[("MSFT", "báo giá quá cũ (trễ 31 phút)")],
                  sess="phiên 15:30–22:00 giờ Đức", url="https://example.com")
    trig = {"rule": "trigger", "tier": 1, "sym": "NVDA", "px": 102.5, "ts": ts,
            "age_sec": 960.0, "val": 1.62,
            "detail": "giá 102.50 chạm điểm vào 102.00, RVol 1.6× (ngưỡng 1.5×)",
            "fields": {"trigger": 102.0, "stop": 97.0, "target": 112.0,
                       "rvol": 1.62, "size_pct": 0.2, "risk_pct": 0.01,
                       "stop_pct": 0.049, "unsure": [], "manual": False}}
    stop = {"rule": "stop", "tier": 1, "sym": "AAPL", "px": 197.4, "ts": ts,
            "age_sec": 960.0, "val": 198.0,
            "detail": "giá 197.40 đã xuyên mức cắt lỗ 198.00",
            "fields": {"hit": 198.0, "n_hit": 1, "shares": 40.0,
                       "accts": ["IBKR"], "stops": [198.0]}}
    gap = {"rule": "gap", "tier": 1, "sym": "MSFT", "px": 452.0, "ts": ts,
           "age_sec": 960.0, "val": 0.043,
           "detail": "mở cửa gap lên 4.3% so với nến quyết định (433.00 → 451.60)",
           "fields": {"gap": 0.043, "open": 451.6, "ref_close": 433.0,
                      "trigger": 431.5, "stop": 418.0}}
    sm = WatchView(day=day, gate=v.gate, pos=pos, src="yf", sess=v.sess,
                   alerts=[{"sym": "NVDA", "rule": "trigger", "px": 102.5,
                            "tier": 1, "ts_utc": ts},
                           {"sym": "AAPL", "rule": "stop", "px": 197.4,
                            "tier": 1, "ts_utc": ts}])
    im = WatchView(day=day, pos=pos, src="yf", sess=v.sess,
                   gate={"mode": "stop_only",
                         "why": "DOWNTREND · EXPANDED → đứng ngoài"})
    return [("MO PHIEN (full)", render_open(v)),
            ("MO PHIEN (downtrend, tin duy nhat)", render_open(im)),
            ("CANH BAO: cham diem vao", render_alert(trig, "yf")),
            ("CANH BAO: xuyen cat lo", render_alert(stop, "yf", pos=pos)),
            ("CANH BAO: gap", render_alert(gap, "yf")),
            ("TONG KET", render_summary(sm)),
            ("TONG KET (khong canh gi)",
             render_summary(WatchView(day=day, pos=pos, src="yf")))]


def _smoke() -> None:
    for ten, txt in _demo():
        assert txt and len(txt) <= render.SAFE_LEN, (ten, len(txt))
        assert "None" not in txt, (ten, "mot o thieu du lieu lot ra tin nhan")
        assert txt == unicodedata.normalize("NFC", txt), ten
    d = dict(_demo())
    assert "trễ 16 phút" in d["CANH BAO: cham diem vao"]
    assert "im lặng" in d["MO PHIEN (downtrend, tin duy nhat)"]
    assert "EUR" in d["MO PHIEN (full)"], "ma dung ngoai phai hien ra"
    print("render_watch.py: smoke ok")


def main() -> int:
    # render.py va render_night.py deu lam viec nay o __main__; module nay thi
    # khong, nen tren terminal Windows (cp1252 mac dinh) no chet o dong dau tien
    # vi mot chu 🟢 - tuc la cach xem tin nhan re nhat lai la cach duy nhat
    # khong chay duoc o day. Tin nhan Telegram thi luon co emoji, khong tranh
    # duoc; cai tranh duoc la de stdout o mot encoding khong ta noi chung.
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    for ten, txt in _demo():
        print(f"\n{'=' * 60}\n{ten}  ({len(txt)} ky tu)\n{'=' * 60}\n{txt}")
    print()
    _smoke()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
