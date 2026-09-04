"""render.py — dựng message alert cho Telegram (HTML parse mode).

Thuần hàm: không network, không DB -> test được bằng dict giả,
đổi layout không sợ vỡ luồng scan.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

# ───────────────────────── cấu hình hiển thị ─────────────────────────
SCORE_MAX   = 12.0      # điểm tối đa để vẽ bar (scorer có thể vượt 10)
BAR_CELLS   = 10
T_EXTREME   = 12.0      # score >= -> level 3
T_STRONG    = 8.0       # score >= -> level 2
LOW_FLOAT   = 10e6      # < -> badge LOW FLOAT
TINY_FLOAT  = 3e6       # < -> badge MICRO FLOAT
HOT_ATR     = 3.0       # ATR move >= -> extreme volatility
HOT_RVOL    = 50.0
SEC_HIGH    = 3.0       # edgar risk >= -> dilution high
SEC_MID     = 1.5
MICRO_PRICE = 1.50      # < -> badge penny
SAFE_LEN    = 3800      # giới hạn mềm (Telegram cứng 4096)
EXPANDABLE  = True      # <blockquote expandable> cần Bot API >= 7.3

# Toàn bộ chuỗi người dùng thấy nằm ở đây. Muốn tiếng Việt: sửa tại chỗ.
TXT = {
    "lvl1": "WATCH", "lvl2": "STRONG MOMENTUM", "lvl3": "EXTREME EVENT",
    "ico1": "🟨", "ico2": "🟧", "ico3": "🟥",
    "new": "NEW", "up": "ESCALATED", "upd": "UPDATE",
    "live": "🟢 LIVE", "delayed": "🟡 DELAYED", "closed": "⚪ MARKET CLOSED",
    "g_mom": "📈 MOMENTUM", "g_vol": "🌪 VOLATILITY", "g_liq": "💧 LIQUIDITY",
    "risk": "⚠️ RISK", "sec": "📄 SEC INTELLIGENCE", "why": "WHY",
    "r_dil_hi": "DILUTION RISK — HIGH", "r_dil_mid": "SHELF REGISTERED",
    "r_vol": "EXTREME VOLATILITY", "r_float": "FLOAT PRESSURE",
    "r_micro": "MICRO / PENNY NAME", "r_halt": "HALT RISK (LULD)",
    "r_dil_hi_n": "Shelf takedown active — new shares can hit the tape.",
    "r_dil_mid_n": "Shelf on file — issuance possible without notice.",
    "r_vol_n": "ATR {a:.1f}× normal daily range.",
    "r_float_n": "Float {f} · turnover {r:.1f}× — thin book, violent prints.",
    "r_micro_n": "Price ${p:.2f} · spread & slippage risk elevated.",
    "r_halt_n": "Move {c:+.0f}% intraday — volatility halts likely.",
    "sec_clean": "🟢 No material dilution signal detected",
    "sec_none": "⚪ No filings retrieved (missing CIK)",
    "sec_earn": "🔵 Earnings just reported",
    "b_low_float": "⚠️ LOW FLOAT", "b_micro_float": "🔥 MICRO FLOAT",
    "b_press": "🔥 FLOAT PRESSURE", "b_penny": "⚠️ PENNY",
    "foot_raw": "Data not independently verified · Not investment advice",
    "foot_part": "Partially verified · some feeds may lag",
    "k_chart": "📈 Chart", "k_sec": "📄 SEC", "k_news": "📰 News",
    "k_more": "🔎 Details", "k_less": "◀ Less", "k_ref": "🔄 Refresh",
    "k_track": "🔔 Track", "k_untrack": "🔕 Untrack",
    "k_wl": "⭐ Watchlist", "k_wl_on": "★ Saved",
}

CB_VER = "a1"   # đổi khi format callback_data thay đổi


# ───────────────────────── helper ─────────────────────────
def esc(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=False)


def esc_url(u: Any) -> str:
    return html.escape("" if u is None else str(u), quote=True)


def _money(v: float | None) -> str | None:
    if not v:
        return None
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v / 1e3:.0f}K"


def _shares(v: float | None) -> str | None:
    if not v:
        return None
    return f"{v / 1e6:.1f}M" if v >= 1e6 else f"{v / 1e3:.0f}K"


def _bar(score: float) -> str:
    p = max(0.0, min((score or 0) / SCORE_MAX, 1.0))
    n = int(round(p * BAR_CELLS))
    return "█" * n + "░" * (BAR_CELLS - n)


def _delta(cur: float | None, prev: float | None, dig: int = 1,
           floor: float = 0.05) -> str:
    """'  ↑2.0' / '  ↓0.4' / '  →' / '' nếu không có mốc so sánh."""
    if cur is None or prev is None:
        return ""
    d = cur - prev
    if abs(d) < floor:
        return "  →"
    return f"  {'↑' if d > 0 else '↓'}{abs(d):.{dig}f}"


def _row(label: str, value: str, delta: str = "") -> str:
    """Một dòng metric, cột thẳng nhờ monospace. KHÔNG lồng <b> vào <code>:
    Telegram không cho nested entity bên trong code/pre."""
    return f"<code>{label:<9}{value:>7}{delta}</code>"


def _quote(body: str) -> str:
    tag = "<blockquote expandable>" if EXPANDABLE else "<blockquote>"
    return f"{tag}{body}</blockquote>"


# ───────────────────────── data model ─────────────────────────
@dataclass
class AlertView:
    """Mọi thứ renderer cần, đã chuẩn hoá. Không phụ thuộc scorer/edgar."""
    sym: str
    px: float
    chg: float                      # dạng phần trăm, vd 85.5
    score: float
    kind: str = "NEW"               # NEW | UP | UPD
    rvol: float | None = None
    atr_move: float | None = None
    dollar_vol: float | None = None
    float_sh: float | None = None
    float_rot: float | None = None
    session: str = "LIVE"           # LIVE | PRE | POST | CLOSED
    freshness: str = "REALTIME"     # REALTIME | DELAYED
    updated: str = ""               # 'HH:MM' giờ ET
    mso: int | None = None
    explain: str = ""
    cik: str | None = None
    sec: dict | None = None         # output edgar.assess()
    prev: dict | None = None        # snapshot lần alert trước
    detail: bool = False            # người dùng đã bấm Details
    tracked: bool = False
    watched: bool = False
    news_url: str | None = None

    # ---- factory từ dict của scorer ----
    @classmethod
    def from_scan(cls, h: dict, *, sec: dict | None = None,
                  prev: dict | None = None, kind: str = "NEW",
                  session: str = "LIVE", updated: str = "",
                  mso: int | None = None, detail: bool = False,
                  tracked: bool = False, watched: bool = False,
                  news_url: str | None = None) -> "AlertView":
        return cls(
            sym=h["sym"], px=float(h.get("px") or 0),
            chg=float(h.get("chg") or 0) * 100, score=float(h.get("score") or 0),
            kind=kind, rvol=h.get("rvol"), atr_move=h.get("atr_move"),
            dollar_vol=h.get("dollar_vol"), float_sh=h.get("float_sh"),
            float_rot=h.get("float_rot"), session=session,
            freshness=h.get("freshness") or "REALTIME", updated=updated,
            mso=mso, explain=h.get("explain") or "", cik=h.get("cik"),
            sec=sec, prev=prev, detail=detail, tracked=tracked,
            watched=watched, news_url=news_url,
        )

    # ---- snapshot để lần sau tính delta ----
    def snapshot(self) -> dict:
        return {"score": self.score, "rvol": self.rvol,
                "atr_move": self.atr_move, "float_rot": self.float_rot,
                "px": self.px, "updated": self.updated}

    # ---- suy luận trạng thái ----
    @property
    def sec_risk(self) -> float:
        return float((self.sec or {}).get("risk") or 0.0)

    @property
    def has_sec(self) -> bool:
        return bool(self.sec) and (self.sec.get("n") or 0) > 0

    @property
    def level(self) -> int:
        if self.score >= T_EXTREME:
            return 3
        if self.sec_risk >= SEC_HIGH and (self.rvol or 0) >= HOT_RVOL \
                and (self.float_rot or 0) >= 2.0:
            return 3        # combo pha loãng + thanh khoản điên = sự kiện lớn
        if self.score >= T_STRONG:
            return 2
        return 1

    @property
    def low_float(self) -> bool:
        return bool(self.float_sh) and self.float_sh < LOW_FLOAT


# ───────────────────────── renderMetrics ─────────────────────────
def render_metrics(v: AlertView) -> list[str]:
    """Ba nhóm theo ý nghĩa. Nhóm rỗng thì bỏ hẳn, không in header trống."""
    p = v.prev or {}
    out: list[str] = []

    mom = []
    if v.rvol:
        mom.append(_row("RVOL", f"{v.rvol:.1f}×", _delta(v.rvol, p.get("rvol"))))
    if (dv := _money(v.dollar_vol)):
        mom.append(_row("Volume", dv))
    if v.float_rot:
        mom.append(_row("Turnover", f"{v.float_rot:.2f}×",
                        _delta(v.float_rot, p.get("float_rot"), 2)))
    if mom:
        out += [f"<b>{TXT['g_mom']}</b>", *mom]

    vol = []
    if v.atr_move:
        vol.append(_row("ATR", f"{v.atr_move:.1f}×",
                        _delta(v.atr_move, p.get("atr_move"))))
    if v.chg:
        vol.append(_row("Range", f"{v.chg:+.1f}%"))
    if vol:
        out += ["", f"<b>{TXT['g_vol']}</b>", *vol]

    if (fl := _shares(v.float_sh)):
        badge = ""
        if v.float_sh < TINY_FLOAT:
            badge = TXT["b_micro_float"]
        elif v.low_float and (v.float_rot or 0) >= 2.0:
            badge = TXT["b_press"]
        elif v.low_float:
            badge = TXT["b_low_float"]
        line = _row("Float", fl) + (f" {badge}" if badge else "")
        liq = [line]
        if v.px and v.px < MICRO_PRICE:
            liq.append(_row("Price", f"${v.px:.2f}") + f" {TXT['b_penny']}")
        out += ["", f"<b>{TXT['g_liq']}</b>", *liq]

    return out


# ───────────────────────── renderRisk ─────────────────────────
def render_risk(v: AlertView, max_items: int = 3) -> list[str]:
    """Trả về các dòng RISK đã xếp theo mức nặng. Rỗng -> không có section."""
    items: list[tuple[int, str, str, str]] = []   # (sev, icon, title, note)

    if v.sec_risk >= SEC_HIGH:
        items.append((3, "🔴", TXT["r_dil_hi"], TXT["r_dil_hi_n"]))
    elif v.sec_risk >= SEC_MID:
        items.append((2, "🟠", TXT["r_dil_mid"], TXT["r_dil_mid_n"]))

    if (v.atr_move or 0) >= HOT_ATR:
        items.append((2, "🟠", TXT["r_vol"],
                      TXT["r_vol_n"].format(a=v.atr_move)))
    if v.low_float and (v.float_rot or 0) >= 2.0:
        items.append((2, "🟠", TXT["r_float"],
                      TXT["r_float_n"].format(f=_shares(v.float_sh),
                                              r=v.float_rot)))
    if v.px and v.px < MICRO_PRICE:
        items.append((1, "🟡", TXT["r_micro"], TXT["r_micro_n"].format(p=v.px)))
    if abs(v.chg) >= 40 and (v.rvol or 0) >= 10 and v.session in ("LIVE", "PRE"):
        items.append((1, "🟡", TXT["r_halt"], TXT["r_halt_n"].format(c=v.chg)))

    if not items:
        return []
    items.sort(key=lambda x: -x[0])
    lines = [f"<b>{TXT['risk']}</b>"]
    for _, ico, title, note in items[:max_items]:
        lines.append(f"{ico} <b>{esc(title)}</b>")
        lines.append(f"<i>{esc(note)}</i>")
    return lines


# ───────────────────────── SEC section ─────────────────────────
def _sec_lines(v: AlertView) -> list[str]:
    """Ưu tiên sec['detail'] có cấu trúc; fallback về sec['flags'] dạng chuỗi."""
    s = v.sec or {}
    det = s.get("detail") or []
    if det:
        out = []
        for d in det[:5]:
            n = d.get("n") or 1
            extra = f" · {n} filings/120d" if n > 1 else ""
            out.append(f"<b>{esc(d['form'])}</b> · {d['age']}d ago · "
                       f"{esc(d.get('desc', ''))}{extra}")
        return out
    return [f"<b>{esc(f.split(' ')[0])}</b> {esc(f.partition(' ')[2])}"
            for f in (s.get("flags") or [])[:5]]


def render_sec(v: AlertView) -> list[str]:
    if not v.has_sec:
        # Không có CIK -> nói thẳng một dòng, đừng giả vờ "an toàn".
        return [f"<b>{TXT['sec']}</b>", f"<i>{TXT['sec_none']}</i>"]
    body = _sec_lines(v)
    head = f"<b>{TXT['sec']}</b>"
    if v.sec_risk < SEC_MID and not body:
        return [head, f"<i>{TXT['sec_clean']}</i>"]
    if v.sec_risk < SEC_MID:
        tag = TXT["sec_earn"] if (v.sec or {}).get("earn") else TXT["sec_clean"]
        return [head, f"<i>{tag}</i>", _quote("\n".join(body))]
    return [head, _quote("\n".join(body))]


# ───────────────────────── WHY (collapsed) ─────────────────────────
_DRIVER = re.compile(r"\(\+(\d+(?:\.\d+)?)\)")


def render_why(v: AlertView) -> list[str]:
    bits = []
    if v.explain:
        bits.append(esc(v.explain))
    prov = []
    if v.mso is not None:
        prov.append(f"session minute {v.mso}/390")
    prov.append("realtime feed" if v.freshness == "REALTIME" else "delayed ~15m")
    if v.updated:
        prov.append(f"scan {v.updated} ET")
    if (p := v.prev or {}).get("score") is not None:
        prov.append(f"prev score {p['score']:.1f}")
    bits.append("<i>" + esc(" · ".join(prov)) + "</i>")
    return [_quote(f"<b>{TXT['why']}</b>\n" + "\n".join(bits))]


# ───────────────────────── renderAlert ─────────────────────────
def _status(v: AlertView) -> str:
    if v.session == "CLOSED":
        return f"{TXT['closed']}" + (f" · last {v.updated} ET" if v.updated else "")
    tag = TXT["live"] if v.freshness == "REALTIME" else TXT["delayed"]
    return tag + (f" {v.updated}" if v.updated else "")


def render_alert(v: AlertView) -> str:
    lvl = v.level
    ico = TXT[f"ico{lvl}"]
    title = TXT[f"lvl{lvl}"]
    if lvl == 3:
        ico, title = "🚨", TXT["lvl3"]
    kind = {"NEW": TXT["new"], "UP": TXT["up"]}.get(v.kind, TXT["upd"])
    if v.kind == "UP" and (p := v.prev or {}).get("score"):
        kind = f"{TXT['up']} +{v.score - p['score']:.1f}"

    L = [
        f"{ico} <b>${esc(v.sym)}</b>",
        f"<b>{v.chg:+.1f}%</b>  ·  ${v.px:.2f}",
        "",
        f"<b>{esc(title)}</b>  ·  <i>{esc(kind)}</i>",
        f"<code>{_bar(v.score)}</code> <b>{v.score:.1f}</b>  ·  {_status(v)}",
        "",
    ]
    L += render_metrics(v)

    if (risk := render_risk(v)):
        L += ["", *risk]

    # Level 1 giữ message ngắn: chỉ hiện SEC khi thật sự có rủi ro.
    if lvl >= 2 or v.sec_risk >= SEC_MID:
        L += ["", *render_sec(v)]

    if v.detail or lvl == 3:
        L += ["", *render_why(v)]

    foot = TXT["foot_raw"] if v.freshness == "REALTIME" else TXT["foot_part"]
    L += ["", f"⚠️ <i>{foot}</i>"]

    return _clamp("\n".join(L))


def _clamp(txt: str) -> str:
    """Cắt an toàn nếu vượt giới hạn — bỏ khối collapsed trước."""
    if len(txt) <= SAFE_LEN:
        return txt
    txt = re.sub(r"<blockquote[^>]*>.*?</blockquote>\n?", "", txt,
                 flags=re.S, count=1)
    return txt[:SAFE_LEN] if len(txt) > SAFE_LEN else txt


# ───────────────────────── renderKeyboard ─────────────────────────
def cb(action: str, sym: str, arg: str = "") -> str:
    """callback_data <= 64 byte. Format: a1|action|SYM|arg"""
    data = f"{CB_VER}|{action}|{sym}" + (f"|{arg}" if arg else "")
    return data[:64]


def render_keyboard(v: AlertView) -> dict:
    chart = f"https://www.tradingview.com/chart/?symbol={v.sym}"
    fviz = f"https://finviz.com/quote.ashx?t={v.sym}"
    edgar_u = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
               f"&CIK={v.cik}&type=8-K&dateb=&owner=include&count=20"
               if v.cik else None)

    row1 = [{"text": TXT["k_chart"], "url": chart},
            {"text": "📊 Finviz", "url": fviz}]
    if edgar_u:                                   # không có CIK -> bỏ nút
        row1.append({"text": TXT["k_sec"], "url": edgar_u})

    rows = [row1]
    row2 = [{"text": TXT["k_less"] if v.detail else TXT["k_more"],
             "callback_data": cb("dtl", v.sym, "0" if v.detail else "1")},
            {"text": TXT["k_ref"], "callback_data": cb("rf", v.sym)}]
    rows.append(row2)

    if v.level >= 2:                              # level 1 không cần theo dõi
        rows.append([
            {"text": TXT["k_untrack"] if v.tracked else TXT["k_track"],
             "callback_data": cb("trk", v.sym, "0" if v.tracked else "1")},
            {"text": TXT["k_wl_on"] if v.watched else TXT["k_wl"],
             "callback_data": cb("wl", v.sym, "0" if v.watched else "1")},
        ])
    if v.news_url:
        rows.append([{"text": TXT["k_news"], "url": v.news_url}])
    return {"inline_keyboard": rows}


# ───────────────────────── fallback khi client/API cũ ─────────────────────────
def degrade(txt: str, level: int = 1) -> str:
    """level 1: bỏ 'expandable'. level 2: bỏ blockquote. level 3: text trần."""
    if level >= 1:
        txt = txt.replace("<blockquote expandable>", "<blockquote>")
    if level >= 2:
        txt = txt.replace("<blockquote>", "").replace("</blockquote>", "")
    if level >= 3:
        txt = re.sub(r"<[^>]+>", "", txt)
    return txt
