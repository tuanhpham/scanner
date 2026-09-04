"""render.py — dung message alert cho Telegram (HTML parse mode).

Thuan ham: khong network, khong DB -> test bang dict gia.
Telegram KHONG ho tro mau chu. Chi co: pre (khoi nen xam),
blockquote (vach doc ben trai), b/i/u/s/code, emoji.
Muon "mau" -> dung o vuong mau + tam giac huong.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

# ───────────────────────── cau hinh hien thi ─────────────────────────
SECTION_STYLE = "panel"   # "panel" = <pre> mot khoi | "quote" = blockquote tung nhom
SCORE_MAX   = 12.0        # diem toi da de ve bar
BAR_CELLS   = 10
T_EXTREME   = 12.0
T_STRONG    = 8.0
LOW_FLOAT   = 10e6
TINY_FLOAT  = 3e6
HOT_ATR     = 3.0
HOT_RVOL    = 50.0
SEC_HIGH    = 3.0
SEC_MID     = 1.5
MICRO_PRICE = 1.50
SAFE_LEN    = 3800        # Telegram cung 4096
EXPANDABLE  = True        # <blockquote expandable> can Bot API >= 7.3
TV_TEXT_LINK = True       # them 1 dong link cuoi (de long-press mo app)

W_LAB, W_VAL = 12, 9      # do rong cot trong panel
RULE_W = 21

GAIN, LOSS, FLAT = "🟩", "🟥", "⬜"

TXT = {
    "lvl1": "WATCH", "lvl2": "STRONG MOMENTUM", "lvl3": "EXTREME EVENT",
    "ico1": "🟨", "ico2": "🟧", "ico3": "🚨",
    "new": "TIN HIEU MOI", "up": "TANG DIEM", "upd": "CAP NHAT",
    "live": "🟢 LIVE", "delayed": "🟡 TRE ~15P", "closed": "⚪ DA DONG PHIEN",
    "g_mom": "MOMENTUM", "g_vol": "VOLATILITY", "g_liq": "LIQUIDITY",
    "h_risk": "⚠️ <b>RUI RO</b>", "h_sec": "📄 <b>HO SO SEC</b>",
    "h_why": "🧮 <b>VI SAO</b>",
    "r_dil_hi": "PHA LOANG — CAO", "r_dil_mid": "DA DANG KY KE PHAT HANH",
    "r_vol": "BIEN DONG CUC MANH", "r_float": "AP LUC FLOAT",
    "r_micro": "GIA THAP / PENNY", "r_halt": "NGUY CO HALT (LULD)",
    "r_dil_hi_n": "Dang chao ban — co phieu moi co the ra thi truong bat ky luc nao.",
    "r_dil_mid_n": "Co ke phat hanh — phat hanh khong can bao truoc.",
    "r_vol_n": "Bien do {a:.1f}x ATR ngay thuong.",
    "r_float_n": "Float {f} · quay {r:.1f}x — so lenh mong, gia giat manh.",
    "r_micro_n": "Gia ${p:.2f} — spread rong, truot gia lon.",
    "r_halt_n": "Bien dong {c:+.0f}% trong phien — de bi tam dung giao dich.",
    "sec_clean": "🟢 Khong thay dau hieu pha loang",
    "sec_none": "⚪ Khong tra duoc ho so (thieu CIK)",
    "sec_earn": "🔵 Vua bao cao ket qua kinh doanh",
    "b_low_float": "⚠️ FLOAT THAP", "b_micro_float": "🔥 FLOAT SIEU NHO",
    "b_press": "🔥 AP LUC FLOAT", "b_penny": "⚠️ PENNY",
    "foot_raw": "Du lieu tho, chua kiem chung · Khong phai loi khuyen dau tu",
    "foot_part": "Nguon co the tre · Tu xac minh truoc khi quyet dinh",
    "k_chart": "📈 TradingView", "k_fviz": "📊 Finviz", "k_sec": "📄 SEC",
    "k_news": "📰 Tin", "k_more": "🔎 Chi tiet", "k_less": "◀ Thu gon",
    "k_ref": "🔄 Cap nhat", "k_track": "🔔 Theo doi", "k_untrack": "🔕 Bo theo doi",
    "k_wl": "⭐ Watchlist", "k_wl_on": "★ Da luu",
}

CB_VER = "a1"


# ───────────────────────── helper ─────────────────────────
def esc(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=False)


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


def _chg_badge(chg: float) -> str:
    """O vuong mau + tam giac huong — cach duy nhat 'to mau' tren Telegram."""
    if chg > 0.05:
        return f"{GAIN} <b>▲ +{chg:.1f}%</b>"
    if chg < -0.05:
        return f"{LOSS} <b>▼ {abs(chg):.1f}%</b>"
    return f"{FLAT} <b>0.0%</b>"


def _delta(cur: float | None, prev: float | None, dig: int = 1,
           floor: float = 0.05) -> str:
    if cur is None or prev is None:
        return ""
    d = cur - prev
    if abs(d) < floor:
        return "  →"
    return f"  {'▲' if d > 0 else '▼'}{abs(d):.{dig}f}"


def _quote(body: str, expand: bool = False) -> str:
    tag = ("<blockquote expandable>" if (expand and EXPANDABLE)
           else "<blockquote>")
    return f"{tag}{body}</blockquote>"


def _prow(lab: str, val: str, dlt: str = "") -> str:
    return f"{lab.upper():<{W_LAB}}{val:>{W_VAL}}{dlt}"


def _prule(title: str) -> str:
    dash = max(2, RULE_W - len(title) - 4)
    return f"── {title} " + "─" * dash


# ───────────────────────── data model ─────────────────────────
@dataclass
class AlertView:
    sym: str
    px: float
    chg: float                      # phan tram, vd 85.5
    score: float
    kind: str = "NEW"               # NEW | UP | UPD
    rvol: float | None = None
    atr_move: float | None = None
    dollar_vol: float | None = None
    float_sh: float | None = None
    float_rot: float | None = None
    session: str = "LIVE"           # LIVE | PRE | POST | CLOSED
    freshness: str = "REALTIME"
    updated: str = ""
    mso: int | None = None
    explain: str = ""
    cik: str | None = None
    sec: dict | None = None
    prev: dict | None = None
    detail: bool = False
    tracked: bool = False
    watched: bool = False
    news_url: str | None = None

    @classmethod
    def from_scan(cls, h: dict, *, sec: dict | None = None,
                  prev: dict | None = None, kind: str = "NEW",
                  session: str = "LIVE", updated: str = "",
                  mso: int | None = None, detail: bool = False,
                  tracked: bool = False, watched: bool = False,
                  news_url: str | None = None) -> "AlertView":
        return cls(
            sym=h["sym"], px=float(h.get("px") or 0),
            chg=float(h.get("chg") or 0) * 100,
            score=float(h.get("score") or 0), kind=kind,
            rvol=h.get("rvol"), atr_move=h.get("atr_move"),
            dollar_vol=h.get("dollar_vol"), float_sh=h.get("float_sh"),
            float_rot=h.get("float_rot"), session=session,
            freshness=h.get("freshness") or "REALTIME", updated=updated,
            mso=mso, explain=h.get("explain") or "", cik=h.get("cik"),
            sec=sec, prev=prev, detail=detail, tracked=tracked,
            watched=watched, news_url=news_url)

    def snapshot(self) -> dict:
        return {"score": self.score, "rvol": self.rvol,
                "atr_move": self.atr_move, "float_rot": self.float_rot,
                "px": self.px, "updated": self.updated}

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
        if (self.sec_risk >= SEC_HIGH and (self.rvol or 0) >= HOT_RVOL
                and (self.float_rot or 0) >= 2.0):
            return 3
        return 2 if self.score >= T_STRONG else 1

    @property
    def low_float(self) -> bool:
        return bool(self.float_sh) and self.float_sh < LOW_FLOAT


# ───────────────────────── nhom so lieu ─────────────────────────
def _groups(v: AlertView) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """[(ten_nhom, [(label, value, delta), ...]), ...] — nhom rong bi bo."""
    p = v.prev or {}
    g: list[tuple[str, list[tuple[str, str, str]]]] = []

    rows = []
    if v.rvol:
        rows.append(("RVOL", f"{v.rvol:.1f}x", _delta(v.rvol, p.get("rvol"))))
    if (dv := _money(v.dollar_vol)):
        rows.append(("Volume", dv, ""))
    if v.float_rot:
        rows.append(("Turnover", f"{v.float_rot:.2f}x",
                     _delta(v.float_rot, p.get("float_rot"), 2)))
    if rows:
        g.append((TXT["g_mom"], rows))

    rows = []
    if v.atr_move:
        rows.append(("ATR", f"{v.atr_move:.1f}x",
                     _delta(v.atr_move, p.get("atr_move"))))
    if v.chg:
        rows.append(("Range", f"{v.chg:+.1f}%", ""))
    if v.px:
        rows.append(("Gia", f"${v.px:.2f}", ""))
    if rows:
        g.append((TXT["g_vol"], rows))

    rows = []
    if (fl := _shares(v.float_sh)):
        rows.append(("Float", fl, ""))
    if rows:
        g.append((TXT["g_liq"], rows))
    return g


def _badges(v: AlertView) -> list[str]:
    """Nhan canh bao dat NGOAI panel — trong <pre> khong bold/emoji dep duoc."""
    out = []
    if v.float_sh and v.float_sh < TINY_FLOAT:
        out.append(f"{TXT['b_micro_float']} · {_shares(v.float_sh)}")
    elif v.low_float and (v.float_rot or 0) >= 2.0:
        out.append(f"{TXT['b_press']} · {_shares(v.float_sh)} quay "
                   f"{v.float_rot:.1f}x")
    elif v.low_float:
        out.append(f"{TXT['b_low_float']} · {_shares(v.float_sh)}")
    if v.px and v.px < MICRO_PRICE:
        out.append(f"{TXT['b_penny']} · ${v.px:.2f}")
    return out


def render_metrics(v: AlertView) -> list[str]:
    """Panel so lieu. panel -> mot khoi <pre>; quote -> blockquote tung nhom."""
    g = _groups(v)
    if not g:
        return []
    if SECTION_STYLE == "panel":
        body: list[str] = []
        for i, (name, rows) in enumerate(g):
            if i:
                body.append("")
            body.append(_prule(name))
            body += [_prow(*r) for r in rows]
        return [f"<pre>{esc(chr(10).join(body))}</pre>"]
    out: list[str] = []
    for name, rows in g:
        out.append(f"<b>{name}</b>")
        out.append(_quote("\n".join(f"<code>{esc(_prow(*r))}</code>"
                                    for r in rows)))
    return out


# ───────────────────────── RISK ─────────────────────────
def render_risk(v: AlertView, max_items: int = 3) -> list[str]:
    items: list[tuple[int, str, str, str]] = []
    if v.sec_risk >= SEC_HIGH:
        items.append((3, "🔴", TXT["r_dil_hi"], TXT["r_dil_hi_n"]))
    elif v.sec_risk >= SEC_MID:
        items.append((2, "🟠", TXT["r_dil_mid"], TXT["r_dil_mid_n"]))
    if (v.atr_move or 0) >= HOT_ATR:
        items.append((2, "🟠", TXT["r_vol"], TXT["r_vol_n"].format(a=v.atr_move)))
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
    body = []
    for _, ico, title, note in items[:max_items]:
        body.append(f"{ico} <b>{esc(title)}</b>")
        body.append(esc(note))
    return [TXT["h_risk"], _quote("\n".join(body))]


# ───────────────────────── SEC ─────────────────────────
def _sec_lines(v: AlertView) -> list[str]:
    s = v.sec or {}
    if (det := s.get("detail")):
        out = []
        for d in det[:5]:
            n = d.get("n") or 1
            extra = f" · {n} lan/120 ngay" if n > 1 else ""
            out.append(f"<b>{esc(d['form'])}</b> · {d['age']} ngay truoc · "
                       f"{esc(d.get('desc', ''))}{extra}")
        return out
    return [f"<b>{esc(f.split(' ')[0])}</b> {esc(f.partition(' ')[2])}"
            for f in (s.get("flags") or [])[:5]]


def render_sec(v: AlertView) -> list[str]:
    if not v.has_sec:
        return [TXT["h_sec"], f"<i>{TXT['sec_none']}</i>"]
    body = _sec_lines(v)
    if v.sec_risk < SEC_MID:
        tag = TXT["sec_earn"] if (v.sec or {}).get("earn") else TXT["sec_clean"]
        if not body:
            return [TXT["h_sec"], f"<i>{tag}</i>"]
        return [TXT["h_sec"], f"<i>{tag}</i>", _quote("\n".join(body), True)]
    return [TXT["h_sec"], _quote("\n".join(body), True)]


# ───────────────────────── WHY ─────────────────────────
def render_why(v: AlertView) -> list[str]:
    bits = []
    if v.explain:
        bits.append(esc(v.explain))
    prov = []
    if v.mso is not None:
        prov.append(f"phut {v.mso}/390")
    prov.append("nguon realtime" if v.freshness == "REALTIME" else "tre ~15 phut")
    if v.updated:
        prov.append(f"quet {v.updated} ET")
    if (p := v.prev or {}).get("score") is not None:
        prov.append(f"diem truoc {p['score']:.1f}")
    bits.append("<i>" + esc(" · ".join(prov)) + "</i>")
    return [TXT["h_why"], _quote("\n".join(bits), True)]


# ───────────────────────── URL ─────────────────────────
def tv_url(sym: str) -> str:
    return f"https://www.tradingview.com/chart/?symbol={sym}"


def fviz_url(sym: str) -> str:
    return f"https://finviz.com/quote.ashx?t={sym}"


def edgar_url(cik: str | None) -> str | None:
    if not cik:
        return None
    return (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&CIK={cik}&type=8-K&dateb=&owner=include&count=20")


# ───────────────────────── message ─────────────────────────
def _status(v: AlertView) -> str:
    if v.session == "CLOSED":
        return TXT["closed"] + (f" · {v.updated} ET" if v.updated else "")
    tag = TXT["live"] if v.freshness == "REALTIME" else TXT["delayed"]
    return tag + (f" {v.updated}" if v.updated else "")


def render_alert(v: AlertView) -> str:
    lvl = v.level
    kind = {"NEW": TXT["new"], "UP": TXT["up"]}.get(v.kind, TXT["upd"])
    if v.kind == "UP" and (p := v.prev or {}).get("score"):
        kind = f"{TXT['up']} +{v.score - p['score']:.1f}"

    L = [
        f"{TXT[f'ico{lvl}']} <b>${esc(v.sym)}</b>  ·  <b>${v.px:.2f}</b>",
        f"{_chg_badge(v.chg)}  ·  <i>{esc(kind)}</i>",
        "",
        f"<b>{TXT[f'lvl{lvl}']}</b>",
        f"<code>{_bar(v.score)}</code> <b>{v.score:.1f}</b>  ·  {_status(v)}",
        "",
    ]
    L += render_metrics(v)
    for b in _badges(v):
        L.append(b)

    if (risk := render_risk(v)):
        L += ["", *risk]
    if lvl >= 2 or v.sec_risk >= SEC_MID:
        L += ["", *render_sec(v)]
    if v.detail or lvl == 3:
        L += ["", *render_why(v)]

    if TV_TEXT_LINK:
        links = [f"<a href=\"{tv_url(v.sym)}\">Bieu do</a>",
                 f"<a href=\"{fviz_url(v.sym)}\">Finviz</a>"]
        if (eu := edgar_url(v.cik)):
            links.append(f"<a href=\"{esc(eu)}\">EDGAR</a>")
        L += ["", "🔗 " + " · ".join(links)]

    foot = TXT["foot_raw"] if v.freshness == "REALTIME" else TXT["foot_part"]
    L += ["", f"⚠️ <i>{foot}</i>"]
    return _clamp("\n".join(L))


def _clamp(txt: str) -> str:
    if len(txt) <= SAFE_LEN:
        return txt
    txt = re.sub(r"<blockquote[^>]*>.*?</blockquote>\n?", "", txt,
                 flags=re.S, count=1)
    return txt[:SAFE_LEN]


# ───────────────────────── keyboard ─────────────────────────
def cb(action: str, sym: str, arg: str = "") -> str:
    return (f"{CB_VER}|{action}|{sym}" + (f"|{arg}" if arg else ""))[:64]


def render_keyboard(v: AlertView) -> dict:
    row1 = [{"text": TXT["k_chart"], "url": tv_url(v.sym)},
            {"text": TXT["k_fviz"], "url": fviz_url(v.sym)}]
    if (eu := edgar_url(v.cik)):
        row1.append({"text": TXT["k_sec"], "url": eu})
    rows = [row1, [
        {"text": TXT["k_less"] if v.detail else TXT["k_more"],
         "callback_data": cb("dtl", v.sym, "0" if v.detail else "1")},
        {"text": TXT["k_ref"], "callback_data": cb("rf", v.sym)}]]
    if v.level >= 2:
        rows.append([
            {"text": TXT["k_untrack"] if v.tracked else TXT["k_track"],
             "callback_data": cb("trk", v.sym, "0" if v.tracked else "1")},
            {"text": TXT["k_wl_on"] if v.watched else TXT["k_wl"],
             "callback_data": cb("wl", v.sym, "0" if v.watched else "1")}])
    if v.news_url:
        rows.append([{"text": TXT["k_news"], "url": v.news_url}])
    return {"inline_keyboard": rows}


def degrade(txt: str, level: int = 1) -> str:
    """1: bo expandable. 2: bo blockquote. 3: strip het tag."""
    if level >= 1:
        txt = txt.replace("<blockquote expandable>", "<blockquote>")
    if level >= 2:
        txt = txt.replace("<blockquote>", "").replace("</blockquote>", "")
    if level >= 3:
        txt = re.sub(r"<[^>]+>", "", txt)
    return txt
