"""render_night.py — dung tin nhan SWING buoi sang (Telegram, HTML parse mode).

Thuan ham: khong network, khong DB -> test bang dict gia.
    python render_night.py            # in ra 4 tin nhan mau

Tach khoi render.py CHU Y: render.py dung mot alert TRONG PHIEN cho MOT ma
(AlertView, ban nut, link EDGAR). Day la mot bao cao HANG NGAY cho ca thi
truong. Hai thu khac nhau ve cau truc; nhet chung vao render_alert() thi
AlertView phai co 15 field Optional chi de mo ta mot bang xep hang nganh.

Dung lai tu render.py: esc(), SAFE_LEN, fit() (bo khoi khi qua dai), degrade().
Do la nhung cho ma "hai cach lam khac nhau cho cung mot viec" se hong.

═══ BA QUY UOC, GIONG render.py ═══

1. PANEL <pre> CHI DUNG ASCII.
   Font monospace cua Telegram khong co glyph tieng Viet co dau: chu co dau roi
   sang font khac, lech co, vo can cot. Nen nhan trong panel la tu ASCII
   (SCORE, 5d, OFF-HI, ATR%). Chu tieng Viet co dau chi dung NGOAI panel.

2. TOI GIAN EMOJI — ca tin nhan co 2 icon:
   · 1 den trang thai o dau header, theo REGIME: 🟢 uptrend · 🟡 range/stress ·
     🔴 downtrend · ⛔ chuoi chay khong xong.
   · 1 dau ⚠️ mo khoi canh bao, chi khi co canh bao.

3. KHOI "CHUOI CHAY" UU TIEN CAO NHAT SAU HEADER.
   Yeu cau go: mot stage do thi tin nhan phai NOI RA stage nao va vi sao. Nen
   khi tin nhan qua dai, bang nganh bi bo TRUOC bao cao loi, khong bao gio
   nguoc lai. Do la y nghia cua thu tu P_* ben duoi.

Gioi han do dai: SAFE_LEN = 3800 cua render.py, dem theo KY TU. Telegram dem
theo don vi UTF-16; moi chu tieng Viet co dau deu nam trong BMP (1 ky tu = 1
don vi) nen hai cach dem trung nhau, va 3800 < 4096 con du bien.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any

import config
import render
from render import esc

# ───────────────────────── uu tien khoi ─────────────────────────
# Khoi diem thap bi bo truoc khi tin nhan vuot SAFE_LEN. Xem quy uoc 3.
P_HEAD  = 9
P_FAIL  = 8          # stage nao do, vi sao — khong bao gio bi bo truoc so lieu
P_REG   = 7          # boi canh + playbook: quyet dinh co giao dich hay khong
P_WATCH = 6          # danh sach theo doi
P_SEC   = 5          # bang xep hang nganh
P_WARN  = 4
P_FOOT  = 2

ICON = {"UPTREND": "🟢", "UPTREND_UNDER_STRESS": "🟡",
        "RANGE": "🟡", "DOWNTREND": "🔴"}
ICON_FAIL = "⛔"

# Ten tieng Viet cua cac nhan enum. Chi de HIEN THI: ma trong DB, trong config
# va tren dashboard van la chuoi ASCII in hoa.
TREND_VI = {
    "UPTREND": "xu hướng tăng",
    "UPTREND_UNDER_STRESS": "xu hướng tăng đang chịu áp lực",
    "RANGE": "đi ngang trong kênh giá",
    "DOWNTREND": "xu hướng giảm",
}
VOL_VI = {"CONTRACTED": "co hẹp", "NORMAL": "bình thường",
          "EXPANDED": "nở rộng"}
SIZE_VI = {1.0: "toàn phần", 0.5: "một nửa", 0.0: "không mở vị thế mới"}
STAGE_VI = {
    "bars": "tải nến",
    "prep": "dựng bảng cơ bản",
    "regime": "bối cảnh thị trường",
    "sectors": "xếp hạng ngành",
    "structure": "đo cấu trúc giá",
    "setups": "lọc danh sách",
    "mktcap": "vốn hóa",
    "push": "đẩy lên dashboard",
    "telegram": "gửi tin nhắn",
}


# ───────────────────────── so lieu -> chu ─────────────────────────
def _pct(v: float | None, d: int = 1, sign: bool = False) -> str:
    if v is None:
        return "-"
    return f"{v * 100:+.{d}f}%" if sign else f"{v * 100:.{d}f}%"


def _arrow(v: int | None) -> str:
    """Thay doi hang. `None` -> "-": "khong biet" khac "khong doi"."""
    if v is None:
        return "-"
    if v > 0:
        return f"+{v}"
    return str(v) if v < 0 else "0"


def _dmy(d: str | None) -> str:
    """2026-09-24 -> 24/09/2026. Dinh dang ngay cua nguoi doc, khong phai ISO."""
    if not d or len(d) < 10:
        return "-"
    return f"{d[8:10]}/{d[5:7]}/{d[0:4]}"


def _size_vi(size: float | None) -> str:
    if size is None:
        return "-"
    return SIZE_VI.get(round(float(size), 2), f"{size:.0%}")


def _pre(lines: list[str]) -> str:
    """Panel <pre>. Noi dung da la ASCII; esc() cho chac (& < > trong ten ma)."""
    return "<pre>" + "\n".join(esc(x) for x in lines) + "</pre>"


# ───────────────────────── du lieu vao ─────────────────────────
@dataclass
class NightView:
    """Tat ca thu mot tin nhan buoi sang can. Khong co gi doc DB o day.

    `bar` la NEN QUYET DINH — phien da chot ma toan bo bao cao dua tren. Nhin
    thay no trong tin nhan la cach duy nhat de phat hien nham nen: cron chay
    08:00 ET, truoc khi mo cua, nen `bar` phai la phien TRUOC ngay `day`. Neu
    hai cai bang nhau thi tin nhan tu noi ra (xem render_head).
    """
    day: str                                     # ngay chay (ET), ISO
    bar: str | None = None                       # nen quyet dinh, ISO
    regime: dict | None = None
    prev: dict | None = None                     # phien regime truoc, de so
    sectors: list[dict] = field(default_factory=list)
    chg: dict[str, dict] = field(default_factory=dict)
    watch: list[dict] = field(default_factory=list)
    warn: list[str] = field(default_factory=list)
    stages: list[dict] = field(default_factory=list)
    url: str = ""
    dry: bool = False
    n_struct: int | None = None
    top_sectors: list[str] = field(default_factory=list)


# ───────────────────────── tung khoi ─────────────────────────
def _failed(v: NightView) -> list[dict]:
    """Cac buoc that su LOI, KHONG tinh buoc bi chan.

    `blocked` = buoc khong he chay vi mot buoc bat buoc truoc no da do. Dem no
    la loi thi mot nguyen nhan duy nhat hien ra thanh bon dong loi, va nguoi doc
    phai tu doan cai nao la goc. Buoc bi chan la HAU QUA -> _blocked() ke rieng
    bang mot cau, sau phan nguyen nhan.

    Buoc do KHONG bat buoc (regime, push, telegram) van tinh la loi: no khong
    dung chuoi nhung no lam mat mot phan bao cao, va im lang ve no la dung cai
    that bai im lang ma ca module nay ton tai de chan.
    """
    return [s for s in v.stages if not s.get("ok") and not s.get("blocked")]


def _blocked(v: NightView) -> list[dict]:
    return [s for s in v.stages if not s.get("ok") and s.get("blocked")]


def _ran(v: NightView, stage: str) -> bool:
    """Buoc `stage` co chay VA xong khong.

    Khong co cai nay thi mot bang rong bi ke thanh "khong ma nao qua duoc bo
    loc" trong khi su that la buoc loc chua he chay - hai cau khac nhau hoan
    toan, va cau thu hai la cau duy nhat khien nguoi doc di xem log. Danh sach
    `stages` rong (goi render_night truc tiep tu test hoac tu backfill) coi nhu
    da chay: luc do khong co ai bao ngoc lai, va "khong biet" thi im lang con
    hon to cao bua.
    """
    if not v.stages:
        return True
    return any(s.get("stage") == stage and s.get("ok") for s in v.stages)


def render_head(v: NightView) -> list[str]:
    tr = (v.regime or {}).get("trend")
    ico = ICON_FAIL if _failed(v) else ICON.get(tr or "", "🟡")
    out = [f"{ico} <b>SWING · {_dmy(v.day)}</b>"]

    if v.bar == v.day:
        # Day la loi NHIN TRUOC TUONG LAI, kieu sai dat nhat trong ca he thong,
        # va no khong tu bao. Bao cao dua tren nen cua CHINH hom nay nghia la
        # nen do chua chot; moi con so trong tin nhan nay se doi truoc khi thi
        # truong dong cua. Noi thang ra thay vi in mot ngay trong nhu that.
        out.append("⚠️ <b>Nến quyết định trùng ngày chạy</b> — báo cáo dựa trên "
                   "một phiên <u>chưa chốt</u>. Đừng vào lệnh theo tin này.")
    elif not v.bar:
        # "(phien da chot)" khi khong biet nen nao la mot cau khang dinh SAI, va
        # sai theo huong lam yen long. Khong biet thi noi la khong biet.
        out.append("<i>Nến quyết định: <b>không xác định được</b></i>")
    else:
        out.append(f"<i>Nến quyết định: {esc(_dmy(v.bar))} (phiên đã chốt)</i>")
    if v.dry:
        out.append("<i>Chạy thử (--dry-run): không ghi gì, không đẩy gì.</i>")
    return out


def render_fail(v: NightView) -> list[str]:
    """Stage nao do va vi sao. Rong khi moi thu xong.

    Mot lieu thuoc chong that bai im lang: neu chuoi dung o giua thi tin nhan
    van den, va no noi ra chinh xac cho dung.
    """
    bad = _failed(v)
    if not bad:
        return []
    # Buoc lam dung ca chuoi len truoc: do la cai can sua, phan con lai la
    # trieu chung. Giu nguyen thu tu chay trong tung nhom (sorted on dinh).
    rows = []
    for s in sorted(bad, key=lambda s: not s.get("fatal")):
        ten = STAGE_VI.get(s.get("stage", ""), s.get("stage", "?"))
        ly_do = str(s.get("err") or "không rõ nguyên nhân").strip()
        # Mot dong, gon: ly do day du nam trong log tren VM va tren dashboard.
        if len(ly_do) > 220:
            ly_do = ly_do[:217] + "..."
        muc = "dừng chuỗi" if s.get("fatal") else "bỏ qua, chạy tiếp"
        rows.append(f"<b>{esc(ten)}</b> ({esc(muc)})\n{esc(ly_do)}")

    if (bl := _blocked(v)):
        ten = ", ".join(STAGE_VI.get(s.get("stage", ""), s.get("stage", "?"))
                        for s in bl)
        rows.append(f"<i>Kéo theo, không chạy: {esc(ten)}.</i>")

    return [f"⚠️ <b>CHUỖI CHẠY KHÔNG XONG — {len(bad)} bước lỗi</b>",
            "<blockquote>" + "\n\n".join(rows) + "</blockquote>"]


def render_regime(v: NightView) -> list[str]:
    r = v.regime
    if not r:
        return ["<b>BỐI CẢNH THỊ TRƯỜNG</b>",
                "<blockquote>Chưa đo được — xem phần lỗi ở trên.</blockquote>"]

    tr, vl = r.get("trend"), r.get("vol")
    pb = config.PLAYBOOK.get((tr, vl), {})
    setups = list(pb.get("setups") or r.get("playbook", "").split(",") or [])
    setups = [s for s in setups if s]

    doi = ""
    if v.prev and v.prev.get("trend") != tr:
        # Ngay regime doi la ngay duy nhat trong thang can doc ky tin nhan nay.
        doi = (f" <i>(đổi từ {esc(TREND_VI.get(v.prev.get('trend'), '?'))})</i>")

    body = [
        f"{esc(r.get('bench', config.BENCH))}: <b>{esc(TREND_VI.get(tr, tr))}"
        f"</b>{doi}",
        f"Biên độ: <b>{esc(VOL_VI.get(vl, vl))}</b> "
        f"({r.get('atr_ratio') or 0:.2f}× mức thường)",
        f"Được phép: <b>{esc(', '.join(setups)) if setups else 'không setup nào'}</b>",
        f"Cỡ vị thế: <b>{esc(_size_vi(r.get('size')))}</b>",
    ]
    if (note := pb.get("note")):
        body.append(f"<i>{esc(note)}</i>")

    panel = _pre([
        f"{'price':<12}{r.get('px') or 0:>10.2f}",
        f"{'SMA50':<12}{r.get('sma50') or 0:>10.2f}",
        f"{'SMA200':<12}{r.get('sma200') or 0:>10.2f}",
        f"{'SMA50 slope':<12}{_pct(r.get('slope50'), 2, sign=True):>10}",
        f"{'ATR/price':<12}{_pct(r.get('atr_pct'), 2):>10}",
    ])
    return ["<b>BỐI CẢNH THỊ TRƯỜNG</b>",
            "<blockquote>" + "\n".join(body) + "</blockquote>", panel]


def render_sectors(v: NightView) -> list[str]:
    if not v.sectors:
        # Giong render_watch: bang rong vi khong xep duoc, chu khong phai vi
        # thi truong khong co nganh nao. Noi ra thay vi bo han khoi nay di.
        if _ran(v, "sectors"):
            return []
        return ["<b>XẾP HẠNG NGÀNH</b>",
                "<blockquote>Chưa xếp được — xem phần lỗi ở trên. Không có xếp "
                "hạng ngành thì danh sách theo dõi cũng không có nguồn."
                "</blockquote>"]
    top = v.top_sectors or [r["sym"] for r in v.sectors[:3]]
    wins = sorted(int(w) for w in config.SECTORS["change_wins"])
    head = (f"{'#':>2} {'ETF':<5}{'SCORE':>7}"
            + "".join(f"{str(w) + 'd':>5}" for w in wins)
            + f"{'TREND':>7}")
    lines = [head, "-" * len(head)]
    for r in v.sectors:
        sym = r.get("sym", "?")
        ch = v.chg.get(sym, {})
        # `*` thay mau: Telegram khong to mau duoc trong panel, va mot ky tu
        # ASCII thang cot con doc duoc hon mot emoji lam lech ca bang.
        mark = "*" if sym in top else " "
        trend = "up" if r.get("above_sma50") and r.get("slope_up") else \
                "dn" if not r.get("above_sma50") else "flat"
        lines.append(
            f"{r.get('rank') or 0:>2}{mark}{sym:<5}"
            f"{r.get('composite') or 0:>7.1f}"
            + "".join(f"{_arrow(ch.get(w, ch.get(str(w)))):>5}" for w in wins)
            + f"{trend:>7}")
    lines.append("")
    # Dong chu thich, khong phai mot dong cua bang: khong phai thang cot voi cac
    # cot ASCII o tren, nen viet tieng Viet co dau nhu moi cau khac trong tin.
    lines.append("* = 3 ngành dẫn dắt (nguồn của danh sách theo dõi)")

    out = ["<b>XẾP HẠNG NGÀNH</b>", _pre(lines)]
    if (dv := [s for s in top if s in config.DEFENSIVE]):
        # Khong phai loi, la mot cau ve thi truong: tien dang chay vao noi tru
        # an. Playbook khong tu biet dieu nay nen phai noi bang chu.
        out.append("<blockquote>⚠️ <b>Nhóm phòng thủ vào top 3</b>: "
                   f"{esc(', '.join(dv))}. Tiền đang tìm chỗ trú, không phải "
                   "chỗ tăng trưởng — hạ kỳ vọng breakout.</blockquote>")
    return out


def render_watch(v: NightView) -> list[str]:
    n = len(v.watch)
    if not n and not _ran(v, "setups"):
        return ["<b>DANH SÁCH THEO DÕI</b>",
                "<blockquote>Chưa lọc được — bước lọc không chạy xong. "
                "Danh sách trống ở đây <u>không</u> có nghĩa là hôm nay không "
                "có mã nào đạt; xem phần lỗi ở trên.</blockquote>"]
    if not n:
        # Danh sach rong la mot KET QUA, khong phai mot loi. Noi ro de khong ai
        # phai doan xem scanner co chay hay khong.
        return ["<b>DANH SÁCH THEO DÕI</b>",
                "<blockquote>Không mã nào qua hết sàn chất lượng hôm nay. "
                "Đây là kết quả bình thường: sàn thanh khoản và sàn sức mạnh "
                "tương đối vốn được đặt để phần lớn phiên trả về rỗng.</blockquote>"]

    # Bang nay la KE HOACH LENH, khong con la bang chi so. Bon cot RS21/RS63/
    # OFF-HI/ATR% da chuyen sang dashboard: chung tra loi "vi sao ma nay co
    # trong danh sach", mot cau hoi cua toi qua. Luc 8h sang cau hoi la "vao o
    # dau, cat o dau, bao nhieu" - va do la bon cot o day.
    #
    # Be rong giu nguyen 47 ky tu nhu bang cu: <pre> cua Telegram tren dien
    # thoai vo cot khi rong hon the, va watch_top = 40 dong nen KHONG the them
    # panel thu hai (2 x 40 dong se day tin nhan qua SAFE_LEN va fit() se bo
    # mat bang xep hang nganh).
    head = (f"{'SYM':<6}{'SEC':<5}{'VAO':>8}{'STOP':>8}{'MUCTIEU':>9}"
            f"{'CACH':>6}{'CO':>5}")
    lines = [head, "-" * len(head)]
    thieu = 0
    for c in v.watch:
        trg, px = c.get("trigger"), c.get("ref_close")
        if trg is None:
            thieu += 1
        # CACH = gia con phai di de cham diem vao. Am = gia DA qua diem vao ->
        # mo cua la co the cham ngay, nen dong do phai doc duoc la khac biet.
        cach = (trg / px - 1) if (trg and px and px > 0) else None
        sz = c.get("size_pct")
        lines.append(
            f"{str(c.get('sym', '?')):<6}{str(c.get('sector') or '-'):<5}"
            f"{(f'{trg:.2f}' if trg else '-'):>8}"
            f"{(f'{s:.2f}' if (s := c.get('stop')) else '-'):>8}"
            f"{(f'{tg:.2f}' if (tg := c.get('target')) else '-'):>9}"
            f"{_pct(cach, 1, sign=True):>6}"
            f"{(f'{sz:.0%}' if sz is not None else '-'):>5}")

    out = [f"<b>DANH SÁCH THEO DÕI ({n})</b>", _pre(lines)]
    if thieu:
        # Thieu ATR hoac thieu moc gia -> plan.make() tra None. Khong im lang:
        # mot dong khong co ke hoach la mot dong KHONG duoc vao lenh, chu khong
        # phai mot dong vao lenh tuy y.
        out.append(f"<i>{thieu} mã chưa lập được kế hoạch (thiếu ATR hoặc thiếu "
                   "mốc giá) — những mã đó <u>không</u> vào lệnh hôm nay.</i>")
    if v.watch and all((c.get("size_pct") or 0) <= 0 for c in v.watch):
        # Ca cot CO bang 0%: playbook dang bat dung ngoai. Bang van gui de con
        # theo doi, nhung phai noi ro vi sao moi dong deu 0.
        out.append("<blockquote>Cỡ vị thế của cả danh sách là <b>0%</b>: ô "
                   "playbook hôm nay không cho mở vị thế mới. Bảng này để "
                   "theo dõi, không phải để vào lệnh.</blockquote>")
    # Ke hoach nay duoc tinh tu NEN DA CHOT cua toi qua va khong doi trong
    # phien. Phan canh bao trong phien chi so sanh gia voi cac con so tren va
    # KHONG tinh lai - do la ca diem cua thiet ke.
    out.append("<i>Vào · cắt lỗ · mục tiêu · cỡ đều tính từ nến đã chốt tối "
               "qua và <u>không đổi trong phiên</u>. Cảnh báo trong phiên chỉ "
               "báo khi giá chạm các mốc này, chứ không tính lại. Cỡ là con số "
               "cuối cùng, đã nhân hệ số playbook — đừng nhân thêm lần nữa.</i>")
    return out


def render_warn(v: NightView) -> list[str]:
    ws = [w for w in v.warn if w]
    if not ws:
        return []
    return ["⚠️ <b>Cần để ý</b>",
            "<blockquote>" + "\n".join(f"· {esc(w)}" for w in ws)
            + "</blockquote>"]


def render_foot(v: NightView) -> list[str]:
    bits = []
    if v.n_struct:
        bits.append(f"{v.n_struct} mã đo được")
    ok = sum(1 for s in v.stages if s.get("ok"))
    if v.stages:
        bits.append(f"{ok}/{len(v.stages)} bước xong")
    line = " · ".join(bits)
    return [f"<i>{esc(line)}</i>"] if line else []


BTN_DASH = "📊 Bảng điều khiển"


def render_keyboard(v: NightView) -> dict | None:
    """Nut "Bang dieu khien", thay cho the <a> tung nam o cuoi tin nhan.

    Cung ly do render.py dung inline_keyboard cho alert trong phien: mot the <a>
    la mot dong chu nam TRONG than tin nhan, nen no an vao gioi han 4096 ky tu,
    va vi no o khoi uu tien thap nhat (P_FOOT) thi no la thu dau tien bi cat khi
    bao cao dai - dung nhung dem co nhieu loi nhat, tuc dung nhung dem can mo
    dashboard nhat. `reply_markup` nam NGOAI than tin: khong tinh do dai, khong
    bi `degrade()` strip khi Telegram tu choi tag, va tren dien thoai no la mot
    o bam duoc thay vi mot doan chu gach chan rong 8 pixel.

    None = chua cau hinh dashboard. Tra None chu khong tra ban phim rong: mot
    `{"inline_keyboard": [[]]}` bi Telegram tu choi bang 400.
    """
    if not v.url:
        return None
    return {"inline_keyboard": [[{"text": BTN_DASH, "url": v.url}]]}


# ───────────────────────── lap tin nhan ─────────────────────────
def blocks(v: NightView) -> list[tuple[int, list[str]]]:
    out: list[tuple[int, list[str]]] = [(P_HEAD, render_head(v))]
    for pri, fn in ((P_FAIL, render_fail), (P_REG, render_regime),
                    (P_WATCH, render_watch), (P_SEC, render_sectors),
                    (P_WARN, render_warn), (P_FOOT, render_foot)):
        if (blk := fn(v)):
            out.append((pri, blk))
    return out


def render_night(v: NightView) -> str:
    """Tin nhan hoan chinh, da vua SAFE_LEN, da chuan hoa NFC.

    NFC: chu tieng Viet co the viet bang hai chuoi code point khac nhau ("ế" =
    1 diem hoac 2 diem). Telegram hien ca hai nhu nhau nhung DO DAI khac nhau,
    nen mot tin nhan dung 3799 ky tu o dang NFD co the thanh 4200 - va bi cat.
    Chuan hoa mot lan o cua ra, sau khi da ghep xong.
    """
    return unicodedata.normalize("NFC", render.fit(blocks(v)))


# ───────────────────────── demo ─────────────────────────
def _demo_regime(**kw) -> dict:
    r = {"d": "2026-09-24", "trend": "UPTREND", "vol": "NORMAL",
         "slope_dir": "rising", "px": 664.12, "sma50": 641.88, "sma200": 601.40,
         "slope50": 0.0182, "atr14": 6.61, "atr_pct": 0.00995,
         "atr_pct_avg": 0.0104, "atr_ratio": 0.96, "playbook": "BO,LEAD,SPIKE",
         "size": 1.0, "bench": "SPY", "n_bars": 400}
    r.update(kw)
    return r


def _demo_sectors() -> tuple[list[dict], dict]:
    names = ["XLK", "XLY", "XLF", "XLI", "XLC", "XLV", "XLE", "XLB", "XLRE",
             "XLP", "XLU"]
    rows = [{"sym": s, "rank": i, "composite": 94.0 - i * 7.3,
             "above_sma50": i <= 8, "above_ema21": i <= 6,
             "slope_up": i <= 7} for i, s in enumerate(names, 1)]
    chg = {s: {5: (i % 5) - 2, 21: None if i == 3 else 3 - (i % 7)}
           for i, s in enumerate(names, 1)}
    return rows, chg


def _demo_watch(n: int = 6) -> list[dict]:
    base = [("NVDA", "XLK", 182.4), ("AVGO", "XLK", 351.2),
            ("MSFT", "XLK", 511.8), ("AMZN", "XLY", 231.6),
            ("BKNG", "XLY", 5412.0), ("JPM", "XLF", 312.9),
            ("GS", "XLF", 741.2), ("MS", "XLF", 152.4)]
    out = []
    for i, (s, sec, px) in enumerate(base[:n]):
        atr = px * (0.028 + i * 0.002)
        trg = px * (1.004 + i * 0.006)
        stop = trg - 1.5 * atr
        out.append({
            "sym": s, "sector": sec, "ref_close": px,
            "rs21": 0.03 + i * 0.004, "rs63": 0.11 + i * 0.011,
            "off_high": 0.02 + i * 0.008, "atr_pct": 0.028 + i * 0.002,
            "quality": 0.9 - i * 0.03,
            "trigger": round(trg, 2), "stop": round(stop, 2),
            "target": round(trg + 2 * (trg - stop), 2),
            "stop_pct": (trg - stop) / trg,
            "risk_pct": 0.0075, "size_pct": min(0.20, 0.0075 * trg / (trg - stop)),
        })
    # Mot dong khong co ke hoach: demo phai in ra duoc ca truong hop nay, vi day
    # la truong hop de quen nhat va cung la truong hop KHONG duoc vao lenh.
    if len(out) > 2:
        for k in ("trigger", "stop", "target", "stop_pct", "size_pct"):
            out[2][k] = None
    return out


def _demo(kind: str) -> NightView:
    rows, chg = _demo_sectors()
    ok = [{"stage": s, "ok": True, "sec": 1.0} for s in
          ("bars", "prep", "regime", "sectors", "structure", "setups",
           "mktcap", "push")]
    v = NightView(day="2026-09-25", bar="2026-09-24", regime=_demo_regime(),
                  sectors=rows, chg=chg, watch=_demo_watch(), stages=ok,
                  url="https://theprofessional.pages.dev/#scanner",
                  n_struct=1843, top_sectors=["XLK", "XLY", "XLF"])
    if kind == "downtrend":
        v.regime = _demo_regime(trend="DOWNTREND", vol="EXPANDED",
                                atr_ratio=1.62, size=0.0, playbook="")
        v.prev = _demo_regime(trend="RANGE")
        v.watch = []
        v.top_sectors = ["XLP", "XLU", "XLV"]
        v.sectors = [{**r, "rank": i, "sym": s}
                     for i, (r, s) in enumerate(
                         zip(rows, ["XLP", "XLU", "XLV", "XLRE", "XLE", "XLB",
                                    "XLI", "XLF", "XLC", "XLY", "XLK"]), 1)]
        v.chg = {r["sym"]: chg.get(r["sym"], {}) for r in v.sectors}
    elif kind == "fail":
        v.stages = ok[:3] + [
            {"stage": "sectors", "ok": False, "fatal": True, "sec": 0.4,
             "err": "khong do duoc 2/11 sector: XLRE(12 nen), XLC(9 nen). "
                    "Can it nhat 150 nen moi ma - chay `python bars.py "
                    "--sync --full`."},
        ]
        v.sectors, v.chg, v.watch = [], {}, []
        v.warn = ["holdings.csv da 452 ngay tuoi - lam moi tu trang SPDR."]
    elif kind == "lookahead":
        v.bar = v.day
        v.warn = ["bars.partial_day() tra None: khong biet hom nay co phien."]
    return v


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")            # type: ignore[union-attr]
    for k in ("binh thuong", "downtrend", "fail", "lookahead"):
        v = _demo(k)
        txt = render_night(v)
        print("=" * 72)
        print(f"{k}  ({len(txt)} ky tu / {render.SAFE_LEN})")
        print("=" * 72)
        print(txt)
        # Nut khong nam trong `txt` (reply_markup), nen phai in rieng - khong thi
        # doc ban demo o day se tuong tin nhan da mat duong vao dashboard.
        for row in (render_keyboard(v) or {}).get("inline_keyboard") or []:
            print("  [ " + " ] [ ".join(b["text"] for b in row) + " ]")
        print()
