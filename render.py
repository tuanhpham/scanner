"""render.py — dung message alert cho Telegram (HTML parse mode).

Thuan ham: khong network, khong DB -> test bang dict gia.
    python render.py          # in ra 3 alert mau (L1/L2/L3)

═══ HAI QUY UOC QUAN TRONG, DUNG "SUA" LAI ═══

1. PANEL <pre> CHI DUNG ASCII.
   Font monospace cua Telegram khong co glyph tieng Viet co dau. Chu nao co
   dau (o, e, o, u...) se roi sang font khac -> hien nho hon, lech co, va
   pha vo can cot. Nen nhan trong panel la thuat ngu tieng Anh ASCII
   (RVOL, $ Volume, Turnover, ATR move, Float, Float cap). Chu tieng Viet
   co dau chi dung NGOAI panel, noi Telegram dung font UI (day du glyph).

2. TOI GIAN EMOJI — ca message chi co 2 icon:
   · 1 den bao muc do o dau header: 🟡 L1 · 🟠 L2 · 🔴 L3 (do = manh nhat).
     Day la neo de mat quet nhanh trong danh sach chat.
   · 1 dau ⚠️ mo dong the canh bao, chi khi co canh bao.
   Tieu de section dung IN HOA + <b>, khong emoji. Muc trong khoi RUI RO
   khong dung den mau nua — tranh trung nghia voi den muc do o header.

Telegram KHONG ho tro mau chu. Chi co: pre (khoi nen xam),
blockquote (vach doc ben trai), b/i/u/s/code, emoji.

Cau truc mot alert (moi khoi cach nhau 1 dong trong):
    HEADER   ticker · gia · %thay doi / xep loai · thanh diem / trang thai
    BADGE    the canh bao, chi ten the (so lieu da nam o panel/header)
    DATA     panel <pre> 3 nhom so lieu, ASCII, cot thang hang
    RISK     blockquote, tieu de + giai thich
    SEC      blockquote expandable, danh sach ho so
    WHY      blockquote expandable, breakdown diem
    FOOT     mien tru trach nhiem

Khong con dong link chu o cuoi: inline keyboard da co san cac nut do.
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

# ───────────────────────── cau hinh hien thi ─────────────────────────
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

# Do rong cot trong panel <pre>. Tat ca nhan phai la ASCII va <= W_LAB.
W_IND, W_LAB, W_VAL, W_DLT = 2, 11, 8, 6

# Uu tien giu lai khi tin nhan vuot SAFE_LEN — khoi diem thap bi bo truoc.
P_HEAD, P_FOOT, P_DATA, P_BADGE, P_RISK, P_SEC, P_WHY = 9, 8, 7, 6, 5, 3, 2

TXT = {
    # xep loai muc do — den mau la emoji DUY NHAT o header
    "lvl1": "WATCH", "lvl2": "STRONG MOMENTUM", "lvl3": "EXTREME EVENT",
    "ico1": "🟡", "ico2": "🟠", "ico3": "🔴",
    # loai tin
    "new": "Tín hiệu mới", "up": "Tăng điểm", "upd": "Cập nhật",
    # trang thai du lieu (ngoai panel -> duoc dung dau)
    "live": "Realtime", "delayed": "Trễ ~15 phút",
    "pre": "Trước phiên", "post": "Sau phiên", "closed": "Đã đóng phiên",
    # ten nhom trong panel — ASCII
    "g_flow": "FLOW", "g_vol": "VOLATILITY", "g_str": "STRUCTURE",
    # nhan hang trong panel — ASCII, <= W_LAB ky tu
    "m_rvol": "RVOL", "m_dvol": "$ Volume", "m_rot": "Turnover",
    "m_atr": "ATR move", "m_float": "Float", "m_fcap": "Float cap",
    # tieu de section — in hoa dam, khong emoji
    "h_data": "<b>SỐ LIỆU</b>", "h_risk": "<b>RỦI RO</b>",
    "h_sec": "<b>HỒ SƠ SEC</b>", "h_why": "<b>VÌ SAO CÓ TÍN HIỆU</b>",
    # muc rui ro: tieu de + giai thich
    "r_dil_hi": "PHA LOÃNG — CAO", "r_dil_mid": "ĐÃ ĐĂNG KÝ KÊ PHÁT HÀNH",
    "r_vol": "BIẾN ĐỘNG CỰC MẠNH", "r_float": "ÁP LỰC FLOAT",
    "r_micro": "GIÁ THẤP / PENNY", "r_halt": "NGUY CƠ HALT (LULD)",
    "r_dil_hi_n": "Đang chào bán — cổ phiếu mới có thể ra thị trường bất kỳ lúc nào.",
    "r_dil_mid_n": "Có kế hoạch phát hành — không cần báo trước.",
    "r_vol_n": "Biên độ {a:.1f} lần ATR ngày thường.",
    "r_float_n": "Sổ lệnh mỏng, giá giật mạnh theo cả hai chiều.",
    "r_micro_n": "Spread rộng, trượt giá lớn khi vào và ra.",
    "r_halt_n": "Biến động {c:+.0f}% trong phiên — dễ bị tạm dừng giao dịch.",
    # ket luan SEC
    "sec_clean": "Không thấy dấu hiệu pha loãng",
    "sec_none": "Không tra được hồ sơ (thiếu CIK)",
    "sec_earn": "Vừa báo cáo kết quả kinh doanh",
    # the canh bao — chi ten the, so lieu da co o panel/header
    "b_low_float": "FLOAT THẤP", "b_micro_float": "FLOAT SIÊU NHỎ",
    "b_press": "ÁP LỰC FLOAT", "b_penny": "PENNY",
    # chan trang
    "foot_raw": "Dữ liệu thô, chưa kiểm chứng · Không phải lời khuyên đầu tư",
    "foot_part": "Nguồn có thể trễ · Tự xác minh trước khi quyết định",
    # nhan nut — chu tran, khong emoji
    "k_chart": "Biểu đồ", "k_fviz": "Finviz", "k_sec": "Hồ sơ SEC",
    "k_news": "Tin", "k_more": "Chi tiết", "k_less": "Thu gọn",
    "k_ref": "Cập nhật", "k_track": "Theo dõi", "k_untrack": "Bỏ theo dõi",
    "k_wl": "Watchlist", "k_wl_on": "Đã lưu",
}

CB_VER = "a1"


# ───────────────────────── helper ─────────────────────────
def esc(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=False)


def _money(v: float | None) -> str | None:
    """Duoi 100M giu 1 chu so thap phan: small-cap thi $2.5M khac han $2M."""
    if not v:
        return None
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e8:
        return f"${v / 1e6:.0f}M"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    return f"${v / 1e3:.0f}K"


def _shares(v: float | None) -> str | None:
    if not v:
        return None
    return f"{v / 1e6:.1f}M" if v >= 1e6 else f"{v / 1e3:.0f}K"


def _bar(score: float) -> str:
    p = max(0.0, min((score or 0) / SCORE_MAX, 1.0))
    n = int(round(p * BAR_CELLS))
    return "█" * n + "░" * (BAR_CELLS - n)


def _chg(chg: float) -> str:
    """Moi alert deu la ma tang (MIN_CHG = +5%) nen khong can o vuong mau."""
    if chg > 0.05:
        return f"▲ +{chg:.1f}%"
    if chg < -0.05:
        return f"▼ {abs(chg):.1f}%"
    return "0.0%"


def _delta(cur: float | None, prev: float | None, dig: int = 1,
           floor: float = 0.05) -> str:
    """Thay doi so voi lan gui truoc. ASCII: nam trong panel <pre>."""
    if cur is None or prev is None:
        return ""
    d = cur - prev
    if abs(d) < floor:
        return "="
    return f"{'+' if d > 0 else '-'}{abs(d):.{dig}f}"


def _quote(body: str, expand: bool = False) -> str:
    tag = ("<blockquote expandable>" if (expand and EXPANDABLE)
           else "<blockquote>")
    return f"{tag}{body}</blockquote>"


def _prow(lab: str, val: str, dlt: str = "") -> str:
    """Mot hang trong panel: thut le, nhan trai, gia tri phai, delta cot rieng."""
    row = f"{' ' * W_IND}{lab:<{W_LAB}}{val:>{W_VAL}}"
    return f"{row}  {dlt:<{W_DLT}}".rstrip() if dlt else row


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
    session_min: int = 390          # 210 neu la nua phien
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
                  mso: int | None = None, session_min: int = 390,
                  detail: bool = False, tracked: bool = False,
                  watched: bool = False,
                  news_url: str | None = None) -> "AlertView":
        return cls(
            sym=h["sym"], px=float(h.get("px") or 0),
            chg=float(h.get("chg") or 0) * 100,
            score=float(h.get("score") or 0), kind=kind,
            rvol=h.get("rvol"), atr_move=h.get("atr_move"),
            dollar_vol=h.get("dollar_vol"), float_sh=h.get("float_sh"),
            float_rot=h.get("float_rot"), session=session,
            freshness=h.get("freshness") or "REALTIME", updated=updated,
            mso=mso, session_min=session_min or 390,
            explain=h.get("explain") or "", cik=h.get("cik"),
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

    @property
    def float_cap(self) -> float:
        """Von hoa phan float — khac von hoa tong, day moi la phan giao dich."""
        return (self.float_sh or 0) * (self.px or 0)


# ───────────────────────── DATA: panel so lieu ─────────────────────────
def _groups(v: AlertView) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """[(ten_nhom, [(nhan, gia_tri, delta), ...]), ...] — nhom rong bi bo.

    TAT CA chuoi tra ve day phai la ASCII: chung di vao khoi <pre>.
    Gia va %thay doi khong nam trong panel: header da hien to va dam roi.
    """
    p = v.prev or {}
    g: list[tuple[str, list[tuple[str, str, str]]]] = []

    rows = []
    if v.rvol:
        rows.append((TXT["m_rvol"], f"{v.rvol:.1f}x",
                     _delta(v.rvol, p.get("rvol"))))
    if (dv := _money(v.dollar_vol)):
        rows.append((TXT["m_dvol"], dv, ""))
    if v.float_rot:
        rows.append((TXT["m_rot"], f"{v.float_rot:.2f}x",
                     _delta(v.float_rot, p.get("float_rot"), 2)))
    if rows:
        g.append((TXT["g_flow"], rows))

    rows = []
    if v.atr_move:
        rows.append((TXT["m_atr"], f"{v.atr_move:.1f}x",
                     _delta(v.atr_move, p.get("atr_move"))))
    if rows:
        g.append((TXT["g_vol"], rows))

    rows = []
    if (fl := _shares(v.float_sh)):
        rows.append((TXT["m_float"], fl, ""))
    if (fc := _money(v.float_cap)):
        rows.append((TXT["m_fcap"], fc, ""))
    if rows:
        g.append((TXT["g_str"], rows))
    return g


def render_metrics(v: AlertView) -> list[str]:
    """Mot khoi <pre>: ten nhom o le, cac hang thut vao W_IND ky tu."""
    g = _groups(v)
    if not g:
        return []
    body: list[str] = []
    for i, (name, rows) in enumerate(g):
        if i:
            body.append("")
        body.append(name)
        body += [_prow(*r) for r in rows]
    return [TXT["h_data"], f"<pre>{esc(chr(10).join(body))}</pre>"]


# ───────────────────────── BADGE: the canh bao ─────────────────────────
def _badges(v: AlertView) -> list[str]:
    """Mot dong, mot dau ⚠️, chi ten the — so lieu da co o panel/header."""
    tags = []
    if v.float_sh and v.float_sh < TINY_FLOAT:
        tags.append(TXT["b_micro_float"])
    elif v.low_float and (v.float_rot or 0) >= 2.0:
        tags.append(TXT["b_press"])
    elif v.low_float:
        tags.append(TXT["b_low_float"])
    if v.px and v.px < MICRO_PRICE:
        tags.append(TXT["b_penny"])
    return [f"⚠️ <b>{esc(' · '.join(tags))}</b>"] if tags else []


# ───────────────────────── RISK ─────────────────────────
def render_risk(v: AlertView, max_items: int = 3) -> list[str]:
    """Muc da sap theo do nghiem trong nen khong can den mau danh dau."""
    items: list[tuple[int, str, str]] = []
    if v.sec_risk >= SEC_HIGH:
        items.append((3, TXT["r_dil_hi"], TXT["r_dil_hi_n"]))
    elif v.sec_risk >= SEC_MID:
        items.append((2, TXT["r_dil_mid"], TXT["r_dil_mid_n"]))
    if (v.atr_move or 0) >= HOT_ATR:
        items.append((2, TXT["r_vol"], TXT["r_vol_n"].format(a=v.atr_move)))
    if v.low_float and (v.float_rot or 0) >= 2.0:
        items.append((2, TXT["r_float"], TXT["r_float_n"]))
    if v.px and v.px < MICRO_PRICE:
        items.append((1, TXT["r_micro"], TXT["r_micro_n"]))
    if abs(v.chg) >= 40 and (v.rvol or 0) >= 10 and v.session in ("LIVE", "PRE"):
        items.append((1, TXT["r_halt"], TXT["r_halt_n"].format(c=v.chg)))
    if not items:
        return []
    items.sort(key=lambda x: -x[0])
    body = []
    for i, (_, title, note) in enumerate(items[:max_items]):
        if i:
            body.append("")
        body.append(f"<b>{esc(title)}</b>")
        body.append(f"<i>{esc(note)}</i>")
    return [TXT["h_risk"], _quote("\n".join(body))]


# ───────────────────────── SEC ─────────────────────────
def _sec_lines(v: AlertView) -> list[str]:
    s = v.sec or {}
    if (det := s.get("detail")):
        out = []
        for d in det[:5]:
            n = d.get("n") or 1
            extra = f" · {n} lần/120 ngày" if n > 1 else ""
            out.append(f"<b>{esc(d['form'])}</b> · {d['age']} ngày trước · "
                       f"{esc(d.get('desc', ''))}{extra}")
        return out
    return [f"<b>{esc(f.split(' ')[0])}</b> {esc(f.partition(' ')[2])}"
            for f in (s.get("flags") or [])[:5]]


def render_sec(v: AlertView) -> list[str]:
    """Rui ro cao -> chi liet ke ho so, vi khoi RUI RO da ket luan giup.
    Rui ro thap -> can mot dong ket luan, khong thi nguoi doc phai tu suy."""
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
    """Breakdown diem, moi thanh phan mot dong + mot dong xuat xu du lieu."""
    body = []
    for part in (v.explain or "").split(" · "):
        if part.strip():
            body.append("· " + esc(part.strip()))
    prov = ["nguồn realtime" if v.freshness == "REALTIME" else "nguồn trễ ~15 phút"]
    if v.updated:
        prov.append(f"quét {v.updated} ET")
    if (p := v.prev or {}).get("score") is not None:
        prov.append(f"điểm trước {p['score']:.1f}")
    if body:
        body.append("")
    body.append(f"<i>{esc(' · '.join(prov))}</i>")
    return [TXT["h_why"], _quote("\n".join(body), True)]


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


# ───────────────────────── HEADER ─────────────────────────
def _status(v: AlertView) -> str:
    """Do tuoi du lieu + thoi diem + vi tri trong phien."""
    if v.session == "CLOSED":
        tag = TXT["closed"]
    elif v.session == "PRE":
        tag = TXT["pre"]
    elif v.session == "POST":
        tag = TXT["post"]
    else:
        tag = TXT["live"] if v.freshness == "REALTIME" else TXT["delayed"]
    bits = [tag]
    if v.updated:
        bits.append(f"{v.updated} ET")
    smin = v.session_min or 390
    if v.session == "LIVE" and v.mso is not None and 0 <= v.mso <= smin:
        bits.append(f"phút {v.mso}/{smin}")
    return " · ".join(bits)


def _kind_label(v: AlertView) -> str:
    if v.kind == "UP" and (p := v.prev or {}).get("score"):
        return f"{TXT['up']} +{v.score - p['score']:.1f}"
    return {"NEW": TXT["new"], "UP": TXT["up"]}.get(v.kind, TXT["upd"])


def render_header(v: AlertView) -> list[str]:
    lvl = v.level
    return [
        f"{TXT[f'ico{lvl}']} <b>{esc(v.sym)}</b> · <b>${v.px:.2f}</b>"
        f" · <b>{_chg(v.chg)}</b>",
        f"<b>{TXT[f'lvl{lvl}']}</b> · <i>{esc(_kind_label(v))}</i>",
        f"<code>{_bar(v.score)}</code> <b>{v.score:.1f}</b>/{SCORE_MAX:.0f}",
        f"<i>{_status(v)}</i>",
    ]


# ───────────────────────── lap message ─────────────────────────
def _blocks(v: AlertView) -> list[tuple[int, list[str]]]:
    """(uu_tien, cac_dong) cho tung khoi. Uu tien thap bi bo neu qua dai."""
    out: list[tuple[int, list[str]]] = [(P_HEAD, render_header(v))]
    if (b := _badges(v)):
        out.append((P_BADGE, b))
    if (m := render_metrics(v)):
        out.append((P_DATA, m))
    if (r := render_risk(v)):
        out.append((P_RISK, r))
    if v.level >= 2 or v.sec_risk >= SEC_MID:
        out.append((P_SEC, render_sec(v)))
    if v.detail or v.level == 3:
        out.append((P_WHY, render_why(v)))
    foot = TXT["foot_raw"] if v.freshness == "REALTIME" else TXT["foot_part"]
    out.append((P_FOOT, [f"<i>{foot}</i>"]))
    return out


def _join(blocks: list[tuple[int, list[str]]]) -> str:
    lines: list[str] = []
    for _, blk in blocks:
        if lines:
            lines.append("")
        lines += blk
    return "\n".join(lines)


# Cap tag can dong lai neu buoc phai cat cung. <blockquote khop ca expandable.
_PAIRS = (("<code>", "</code>"), ("<i>", "</i>"), ("<b>", "</b>"),
          ("<pre>", "</pre>"), ("<blockquote", "</blockquote>"))


def _hard_cut(txt: str) -> str:
    """Luoi an toan cuoi: cat o ranh gioi dong, roi dong cac tag con ho."""
    cut = txt[:SAFE_LEN].rsplit("\n", 1)[0]
    for open_t, close_t in _PAIRS:
        if cut.count(open_t) > cut.count(close_t):
            cut += close_t
    return cut


def render_alert(v: AlertView) -> str:
    """Bo TUNG KHOI khi vuot SAFE_LEN, khong cat giua tag HTML."""
    blocks = _blocks(v)
    txt = _join(blocks)
    while len(txt) > SAFE_LEN and len(blocks) > 1:
        i = min(range(len(blocks)), key=lambda j: (blocks[j][0], -j))
        blocks.pop(i)
        txt = _join(blocks)
    return txt if len(txt) <= SAFE_LEN else _hard_cut(txt)


# ───────────────────────── keyboard ─────────────────────────
def cb(action: str, sym: str, arg: str = "") -> str:
    return (f"{CB_VER}|{action}|{sym}" + (f"|{arg}" if arg else ""))[:64]


def render_keyboard(v: AlertView) -> dict:
    """3 hang: link ngoai · hanh dong · ho so + theo doi. Toi da 3 nut/hang."""
    rows = []

    row = [{"text": TXT["k_chart"], "url": tv_url(v.sym)},
           {"text": TXT["k_fviz"], "url": fviz_url(v.sym)}]
    if v.news_url:
        row.append({"text": TXT["k_news"], "url": v.news_url})
    rows.append(row)

    rows.append([
        {"text": TXT["k_ref"], "callback_data": cb("rf", v.sym)},
        {"text": TXT["k_less"] if v.detail else TXT["k_more"],
         "callback_data": cb("dtl", v.sym, "0" if v.detail else "1")}])

    row = []
    if (eu := edgar_url(v.cik)):
        row.append({"text": TXT["k_sec"], "url": eu})
    if v.level >= 2:
        row.append({"text": TXT["k_untrack"] if v.tracked else TXT["k_track"],
                    "callback_data": cb("trk", v.sym,
                                        "0" if v.tracked else "1")})
        row.append({"text": TXT["k_wl_on"] if v.watched else TXT["k_wl"],
                    "callback_data": cb("wl", v.sym,
                                        "0" if v.watched else "1")})
    if row:
        rows.append(row)
    return {"inline_keyboard": rows}


def degrade(txt: str, level: int = 1) -> str:
    """1: bo expandable. 2: bo blockquote. 3: strip het tag."""
    import re
    if level >= 1:
        txt = txt.replace("<blockquote expandable>", "<blockquote>")
    if level >= 2:
        txt = txt.replace("<blockquote>", "").replace("</blockquote>", "")
    if level >= 3:
        txt = re.sub(r"<[^>]+>", "", txt)
    return txt


# ───────────────────────── demo ─────────────────────────
_DEMO = {
    "sym": "WETO", "px": 10.61, "chg": 0.855, "score": 12.4,
    "rvol": 66.2, "atr_move": 4.1, "dollar_vol": 311e6,
    "float_sh": 8.4e6, "float_rot": 3.49, "cik": "0001941158",
    "freshness": "REALTIME",
    "explain": "RVOL 66.2× (+4.0) · biên độ 4.1× ATR (+3.3) · "
               "quay vòng 3.49× (+4.2) · 311 triệu USD (+0.5)",
}
_DEMO_SEC = {
    "risk": 4.5, "n": 7, "earn": True,
    "detail": [{"form": "424B5", "age": 2, "n": 1,
                "desc": "đang chào bán cổ phiếu (shelf takedown)"},
               {"form": "8-K", "age": 1, "n": 3,
                "desc": "tin trọng yếu"},
               {"form": "S-3", "age": 46, "n": 1,
                "desc": "đăng ký kê hàng - có thể bán bất cứ lúc nào"}],
}

if __name__ == "__main__":
    import re
    import sys
    try:                      # terminal Windows mac dinh cp1252, khong co dau
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    cases = [
        ("L3 · rui ro SEC cao · co snapshot truoc", _DEMO, _DEMO_SEC,
         {"score": 9.8, "rvol": 53.8, "atr_move": 3.6, "float_rot": 3.19}),
        ("L2 · sach SEC", {**_DEMO, "score": 8.3, "float_sh": 42e6,
                           "float_rot": 0.7, "atr_move": 1.8},
         {"risk": 0.0, "n": 2, "earn": True, "detail": []}, None),
        ("L1 · penny · khong tra duoc SEC",
         {**_DEMO, "sym": "ABCD", "score": 7.1, "px": 1.18, "chg": 0.42,
          "cik": None, "freshness": "~15min", "float_sh": 2.1e6,
          "float_rot": 2.4}, None, None),
    ]
    for title, h, sec, prev in cases:
        v = AlertView.from_scan(h, sec=sec, prev=prev, kind="NEW",
                                session="LIVE", updated="15:42", mso=12,
                                news_url="https://example.com")
        txt = render_alert(v)
        print("\n" + "=" * 64)
        print(f"{title}  ->  level {v.level}, {len(txt)} ky tu")
        print("=" * 64)
        print(degrade(txt, 3))          # strip tag cho de doc tren terminal
        print("-" * 64)
        for r in render_keyboard(v)["inline_keyboard"]:
            print("  [ " + " ] [ ".join(b["text"] for b in r) + " ]")

    # Canh bao ngay neu co ky tu ngoai ASCII lot vao panel <pre>: do la
    # nguyen nhan chu tieng Viet bi lech co font tren Telegram.
    print("\n" + "=" * 64)
    bad = set()
    for title, h, sec, prev in cases:
        v = AlertView.from_scan(h, sec=sec, prev=prev)
        for blk in re.findall(r"<pre>(.*?)</pre>", render_alert(v), re.S):
            bad |= {c for c in blk if ord(c) > 127}
    print("ky tu ngoai ASCII trong panel <pre>:",
          " ".join(sorted(bad)) if bad else "khong co -> OK")
