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
import os
import urllib.parse
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

# Nut "Hoi ChatGPT": mo ChatGPT voi prompt da dien san qua tham so ?q=.
# Dat CHATGPT_GPT_ID trong .env (vd "g-abc123...") de mo mot GPT rieng thay
# vi ChatGPT thuong. Xem README muc 6 ve gioi han cua cach nay.
CHATGPT_GPT_ID = os.getenv("CHATGPT_GPT_ID", "").strip()
ASK_MAX = 700             # do dai prompt truoc khi encode; URL dai de bi cat

# Mau URL cho nut Bieu do. {sym} = ma chung khoan.
# De trong .env de thu cac dang link khac ma khong phai sua code: TradingView
# chi mo app khi duong dan nam trong danh sach ho khai bao (universal link),
# va ta khong biet chac ho khai nhung duong dan nao. Xem README muc 6.
# LUU Y: Telegram chi nhan http(s):// hoac tg:// -> khong dat tradingview://
# truc tiep vao day, nut se bi API tu choi.
TV_URL = os.getenv("TV_URL", "").strip() \
    or "https://www.tradingview.com/chart/?symbol={sym}"

# Do rong cot trong panel <pre>. Tat ca nhan phai la ASCII va <= W_LAB.
W_IND, W_LAB, W_VAL, W_DLT = 2, 11, 8, 6

# Uu tien giu lai khi tin nhan vuot SAFE_LEN — khoi diem thap bi bo truoc.
# P_HALT cao hon ca header: dang bi tam dung thi moi so lieu con lai la thu yeu.
# P_NEWS tren P_SEC: khoi CATALYST chi 3-4 dong ma noi duoc "vi sao chay", bo
# no de giu danh sach ho so SEC la nguoc thu tu gia tri.
P_HALT, P_HEAD, P_FOOT, P_DATA, P_BADGE, P_RISK, P_NEWS, P_SEC, P_WHY = \
    10, 9, 8, 7, 6, 5, 4, 3, 2

NEWS_HEAD_MAX = 170     # tieu de dai hon the nay bi cat — Benzinga co ban 200+

TXT = {
    # xep loai muc do — den mau la emoji DUY NHAT o header
    "lvl1": "WATCH", "lvl2": "STRONG MOMENTUM", "lvl3": "EXTREME EVENT",
    "ico1": "🟡", "ico2": "🟠", "ico3": "🔴",
    # loai tin
    "new": "Tín hiệu mới", "up": "Tăng điểm", "upd": "Cập nhật",
    "trk": "Đang theo dõi · tự cập nhật",
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
    "h_news": "<b>CATALYST</b>",
    # khoi CATALYST — nhan nhom lay tu news.py, day chi la phan khung
    "n_none": "Không thấy tin nào trong 4 giờ qua — chạy không rõ lý do",
    "n_now": "vừa xong", "n_min": "{m} phút trước",
    "n_hour": "{h} giờ {m} phút trước",
    "n_more": "còn {n} tin khác",
    # muc rui ro: tieu de + giai thich
    "r_dil_hi": "PHA LOÃNG — CAO", "r_dil_mid": "ĐÃ ĐĂNG KÝ KÊ PHÁT HÀNH",
    "r_vol": "BIẾN ĐỘNG CỰC MẠNH", "r_float": "ÁP LỰC FLOAT",
    "r_micro": "GIÁ THẤP / PENNY",
    # dong halt (tren cung, ngoai panel) — du kien tu feed Nasdaq, khong doan
    "hl_on": "TẠM DỪNG GIAO DỊCH", "hl_off": "VỪA MỞ LẠI GIAO DỊCH",
    "hl_open": "chưa có giờ mở lại",
    "hl_quote": "mở báo giá {t} ET",
    "hl_since": "từ {t} ET",
    "hl_back": "dừng {a} ET → mở lại {b} ET",
    "r_dil_hi_n": "Đang chào bán — cổ phiếu mới có thể ra thị trường bất kỳ lúc nào.",
    "r_dil_mid_n": "Có kế hoạch phát hành — không cần báo trước.",
    "r_vol_n": "Biên độ {a:.1f} lần ATR ngày thường.",
    "r_float_n": "Sổ lệnh mỏng, giá giật mạnh theo cả hai chiều.",
    "r_micro_n": "Spread rộng, trượt giá lớn khi vào và ra.",
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
    "k_ref": "Cập nhật", "k_track": "Theo dõi", "k_untrack": "Đang theo dõi",
    "k_ask": "Hỏi ChatGPT",
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
    news_url: str | None = None
    halt: dict | None = None        # halts.HaltBook.view() — None = khong halt
    # news.NewsBook.view(). None = khong biet gi (thieu key / feed chet) ->
    # khong ve khoi nao. {"n": 0} = feed song va that su khong co tin.
    news: dict | None = None

    @classmethod
    def from_scan(cls, h: dict, *, sec: dict | None = None,
                  prev: dict | None = None, kind: str = "NEW",
                  session: str = "LIVE", updated: str = "",
                  mso: int | None = None, session_min: int = 390,
                  detail: bool = False, tracked: bool = False,
                  news_url: str | None = None,
                  halt: dict | None = None,
                  news: dict | None = None) -> "AlertView":
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
            news_url=news_url, halt=halt, news=news)

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
    def news_risk(self) -> float:
        return float((self.news or {}).get("risk") or 0.0)

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


# ───────────────────────── CATALYST ─────────────────────────
def _ago(m: int) -> str:
    """Tuoi tin bang chu. Tin la chuyen cua phut nen khong dung gio ET."""
    m = max(0, int(m or 0))
    if m < 1:
        return TXT["n_now"]
    if m < 60:
        return TXT["n_min"].format(m=m)
    return TXT["n_hour"].format(h=m // 60, m=m % 60)


def _link(url: str, label: str) -> str:
    """Telegram chi nhan http(s):// trong the <a>. Scheme khac -> chu tran.

    Mot URL sai lam CA tin nhan bi tra 400; khong duoc de tin tuc lam mat alert.
    """
    u = (url or "").strip()
    return (f'<a href="{esc(u)}">{esc(label)}</a>'
            if u.startswith(("http://", "https://")) else esc(label))


def render_news(v: AlertView) -> list[str]:
    """Khoi CATALYST — dat TREN so lieu: "vi sao chay" doc truoc "chay bao nhieu".

    Khong emoji: nhan nhom la chu IN HOA dam, giong khoi HALT (quy uoc toi da
    2 emoji/tin nhan — den mau o header va dau ⚠️ o dong the).
    """
    n = v.news
    if n is None:
        return []                    # khong biet gi -> im lang, khong doan
    if not n.get("n"):
        # Feed dang song ma khong co tin nao: day la KET LUAN, khong phai
        # thieu du lieu. README goi day la nhan "chay khong co ly do".
        return [TXT["h_news"], f"<i>{TXT['n_none']}</i>"]

    head = (n.get("headline") or "").strip()
    if len(head) > NEWS_HEAD_MAX:
        head = head[:NEWS_HEAD_MAX].rsplit(" ", 1)[0] + "…"

    body: list[str] = []
    if n.get("label"):
        body.append(f"<b>{esc(n['label'])}</b>")
    body.append(_link(n.get("url") or "", head))
    meta = [x for x in (esc(n.get("source") or ""), _ago(n.get("age") or 0)) if x]
    if (n.get("n") or 0) > 1:
        meta.append(TXT["n_more"].format(n=n["n"] - 1))
    body.append(f"<i>{' · '.join(meta)}</i>")
    # Giai thich chi hien cho nhom xau: nhom tot khong can day ai ra quyet dinh.
    if n.get("note") and v.news_risk > 0:
        body.append(f"<i>{esc(n['note'])}</i>")
    return [TXT["h_news"], _quote("\n".join(body))]


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
    # Truoc day co muc "NGUY CO HALT (LULD)" doan tu chg/rvol. Da bo: halts.py
    # doc feed that cua Nasdaq va in o dong dau tin nhan, con phan "bien dong
    # manh" thi muc r_vol o tren da noi. Giu ca hai chi la noi hai lan.
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
    try:
        return TV_URL.format(sym=sym)
    except (KeyError, IndexError, ValueError):
        # TV_URL trong .env viet sai (dau { le) -> ve mac dinh, dung de mot
        # loi cau hinh lam chet moi alert.
        return f"https://www.tradingview.com/chart/?symbol={sym}"


def fviz_url(sym: str) -> str:
    return f"https://finviz.com/quote.ashx?t={sym}"


def edgar_url(cik: str | None) -> str | None:
    if not cik:
        return None
    return (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&CIK={cik}&type=8-K&dateb=&owner=include&count=20")


def ask_prompt(v: AlertView) -> str:
    """Prompt gui sang ChatGPT: dua san so lieu, hoi dung 3 cau.

    Ngan gon la bat buoc - prompt di trong URL, moi chu tieng Viet co dau
    thanh 9 byte sau khi encode. Khong dua nhan dinh cua bot vao (diem so,
    xep loai) vi day la thang diem rieng, ChatGPT khong hieu duoc.
    """
    f = []
    if v.rvol:
        f.append(f"khối lượng gấp {v.rvol:.0f} lần bình thường")
    if v.atr_move:
        f.append(f"biên độ {v.atr_move:.1f} lần ATR")
    if v.float_sh:
        f.append(f"float {v.float_sh / 1e6:.1f}M cp")
    if v.float_rot:
        f.append(f"quay vòng {v.float_rot:.1f} lần float")
    if v.dollar_vol:
        f.append(f"giá trị giao dịch {_money(v.dollar_vol)}")

    move = (f"tăng {v.chg:.1f}% lên" if v.chg >= 0
            else f"giảm {abs(v.chg):.1f}% về")
    p = [f"Cổ phiếu Mỹ {v.sym} hôm nay {move} ${v.px:.2f}"
         f"{', ' + ', '.join(f) if f else ''}."]
    if (d := (v.sec or {}).get("detail")):
        p.append("Hồ sơ SEC gần đây: "
                 + "; ".join(f"{x['form']} cách {x['age']} ngày" for x in d[:3])
                 + ".")
    p.append("1) Vì sao nó tăng — có tin/thông báo nào hôm nay? "
             "2) Rủi ro pha loãng và thanh khoản ra sao? "
             "3) Đây là đợt tăng có cơ sở hay chỉ là bơm giá? "
             "Trả lời ngắn bằng tiếng Việt, dẫn nguồn.")
    return " ".join(p)[:ASK_MAX]


def ask_url(v: AlertView) -> str:
    base = "https://chatgpt.com/"
    if CHATGPT_GPT_ID:
        gid = CHATGPT_GPT_ID if CHATGPT_GPT_ID.startswith("g-") \
            else f"g-{CHATGPT_GPT_ID}"
        base += f"g/{gid}"
    return f"{base}?q={urllib.parse.quote(ask_prompt(v))}"


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
    return {"NEW": TXT["new"], "UP": TXT["up"],
            "TRK": TXT["trk"]}.get(v.kind, TXT["upd"])


# ───────────────────────── HALT ─────────────────────────
def render_halt(v: AlertView) -> list[str]:
    """Dong tam dung giao dich — dat TREN header, la thu doc dau tien.

    Khong dung emoji: ca tin nhan chi co dung mot emoji (den mau o header).
    Chu IN HOA dam da du nang, va giong quy uoc tieu de section.
    """
    h = v.halt
    if not h:
        return []
    lab = h.get("label") or h.get("code") or "?"
    code = h.get("code") or ""
    head = TXT["hl_off"] if h.get("resumed") else TXT["hl_on"]
    tail = f" · <code>{esc(code)}</code>" if code else ""

    if h.get("resumed"):
        when = TXT["hl_back"].format(a=h.get("since") or "?",
                                     b=h.get("until") or "?")
    else:
        when = TXT["hl_since"].format(t=h.get("since") or "?")
        # ResumptionQuoteTime co truoc ResumptionTradeTime ~5 phut: da biet gio
        # mo bao gia thi da biet sap mo lai, khac han "chua co gio mo lai".
        q = h.get("quote")
        when += f" · {TXT['hl_quote'].format(t=q)}" if q else \
            f" · {TXT['hl_open']}"

    out = [f"<b>{esc(head)} · {esc(lab)}</b>{tail}"]
    note = h.get("note") or ""
    out.append(f"<i>{esc(when)}{(' — ' + esc(note)) if note else ''}</i>")
    return out


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
    out: list[tuple[int, list[str]]] = []
    if (hl := render_halt(v)):
        out.append((P_HALT, hl))
    out.append((P_HEAD, render_header(v)))
    if (b := _badges(v)):
        out.append((P_BADGE, b))
    # CATALYST truoc SO LIEU: doc "vi sao" roi moi doc "bao nhieu". README chi
    # yeu cau dat tren WHY; dat tren ca DATA vi mot dong "Pricing of Offering"
    # doi hoan toan cach hieu day so ben duoi.
    if (nw := render_news(v)):
        out.append((P_NEWS, nw))
    if (m := render_metrics(v)):
        out.append((P_DATA, m))
    if (r := render_risk(v)):
        out.append((P_RISK, r))
    if v.level >= 2 or v.sec_risk >= SEC_MID:
        out.append((P_SEC, render_sec(v)))
    # Chi dua vao v.detail. Truoc day co "or v.level == 3" nen o muc 3 khoi
    # WHY luon hien -> nut "Thu gon" bam ma khong thu gon duoc gi. Viec bat
    # san detail cho muc 3 giao cho main.build_view (detail=None = tu quyet).
    if v.detail:
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
         "callback_data": cb("dtl", v.sym, "0" if v.detail else "1")},
        {"text": TXT["k_ask"], "url": ask_url(v)}])

    row = []
    if (eu := edgar_url(v.cik)):
        row.append({"text": TXT["k_sec"], "url": eu})
    if v.level >= 2:
        row.append({"text": TXT["k_untrack"] if v.tracked else TXT["k_track"],
                    "callback_data": cb("trk", v.sym,
                                        "0" if v.tracked else "1")})
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
        v.detail = v.level == 3          # main.build_view lam viec nay
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

    # Dong halt: lay tu chinh halts.py de kiem tra luon hop dong giua hai file
    # (doi ten khoa trong halts.view() ma quen sua o day thi thay ngay).
    import time as _t

    import halts
    _b = halts.HaltBook()
    _now = _t.time()
    _b.load(halts._sample(_now), now=_now)
    print("\n" + "=" * 64)
    for _s in ("WETO", "NEWSY"):
        _v = AlertView.from_scan(_DEMO, sec=_DEMO_SEC,
                                 halt=_b.view(_s, _now))
        _txt = render_alert(_v)
        print(f"halt {_s}: " + " / ".join(degrade(_txt, 3).split("\n")[:2]))
        assert TXT["hl_on"] in _txt or TXT["hl_off"] in _txt, _s
    # Khong halt -> khong duoc co dong nao.
    _v = AlertView.from_scan(_DEMO, sec=_DEMO_SEC, halt=None)
    assert TXT["hl_on"] not in render_alert(_v)
    print("khong halt -> khong co dong halt: OK")

    # Khoi CATALYST: lay tu chinh news.py de kiem tra hop dong giua hai file.
    import news
    _nb = news.NewsBook()
    _nb.load(news._sample(_now), now=_now)
    print("\n" + "=" * 64)
    for _s in ("WETO", "BIOX", "PLAIN", "AAPL"):
        _v = AlertView.from_scan(_DEMO, sec=_DEMO_SEC,
                                 news=_nb.view(_s, _now))
        _txt = render_alert(_v)
        assert TXT["h_news"] in _txt, _s
        _got = [x for x in degrade(_txt, 3).split("\n") if x.strip()]
        _i = _got.index("CATALYST")
        print(f"catalyst {_s:<6}: " + " / ".join(_got[_i + 1:_i + 4]))
    # Ma pha loang phai co nhan nhom, va nhan do phai den TU news.py.
    _v = AlertView.from_scan(_DEMO, sec=_DEMO_SEC, news=_nb.view("WETO", _now))
    assert _v.news_risk >= news.NEWS_RISK_MAX
    assert "PHA LOÃNG — TIN VỪA RA" in render_alert(_v)
    # Khoi CATALYST phai nam TREN khoi SO LIEU va TREN khoi VI SAO.
    _v.detail = True
    _t2 = render_alert(_v)
    assert _t2.index(TXT["h_news"]) < _t2.index(TXT["h_data"]) < \
        _t2.index(TXT["h_why"]), "thu tu khoi sai"
    # Khong biet gi (thieu key / feed chet) -> KHONG duoc noi "khong co tin".
    _v = AlertView.from_scan(_DEMO, sec=_DEMO_SEC, news=None)
    assert TXT["h_news"] not in render_alert(_v)
    print("news=None -> khong co khoi CATALYST (khong doan bua): OK")

    # Nut Chi tiet: phai bat/tat duoc khoi WHY o CA hai muc.
    for sc, tag in ((12.4, "L3"), (8.3, "L2")):
        sec = _DEMO_SEC if sc > 10 else {"risk": 0.0, "n": 2, "detail": []}
        for d in (False, True):
            v = AlertView.from_scan({**_DEMO, "score": sc}, sec=sec, detail=d)
            print(f"nut Chi tiet {tag} detail={d!s:<5} -> khoi WHY hien:",
                  TXT["h_why"] in render_alert(v))

    # Telegram chi nhan http(s):// va tg:// cho nut url. Scheme khac -> API
    # tra 400 va CA alert khong gui duoc, nen phai chan ngay tu day.
    tu = tv_url("WETO")
    print(f"\nURL Bieu do: {tu}")
    if not tu.startswith(("http://", "https://", "tg://")):
        print("  [X] Telegram chi nhan http(s):// hoac tg:// -> nut se bi tu "
              "choi. Sua TV_URL trong .env.")
    if tu == "https://www.tradingview.com/chart/?symbol=WETO":
        print("  (dang dung mau mac dinh; dat TV_URL trong .env de thu dang khac)")

    # URL nut ChatGPT: URL qua dai se bi trinh duyet/Telegram cat.
    v = AlertView.from_scan(_DEMO, sec=_DEMO_SEC)
    u = ask_url(v)
    print(f"\nURL ChatGPT: {len(u)} ky tu"
          f" ({'OK' if len(u) < 2000 else 'QUA DAI'})"
          f"{' | GPT rieng: ' + CHATGPT_GPT_ID if CHATGPT_GPT_ID else ''}")
    print("prompt:", ask_prompt(v))
