"""watchd.py - tien trinh canh bao TRONG PHIEN (Tier 1).

    python watchd.py                      # chay that, tu bat/tat theo phien
    python watchd.py --dry-run            # khong gui Telegram, in ra stdout
    python watchd.py --once --dry-run      # dung mot vong roi thoat (de kiem)
    QUOTE_SRC=fixture:t.json python watchd.py --once --dry-run   # khong mang

MOT TIEN TRINH RIENG, KHONG PHAI MOT CHE DO CUA main.py. Ly do:
  · main.py goi Telegram getUpdates, va CHI MOT tien trinh duoc phep lam the
    (409 Conflict). watchd.py chi GUI, khong doc update, nen no chay song song
    voi main.py duoc trong tuan chuyen doi - va do la dieu kien de bo main.py
    ma khong co mot ngay nao khong ai canh phien.
  · main.py la 721 dong asyncio voi tam vong lap va bay bien toan cuc. Luon mot
    che do thu hai qua do de test cai moi phai dung ca cai cu.

BA THU DEN TU BEN NGOAI, VA DO LA TOAN BO DAU VAO
-------------------------------------------------
    watchlist.load()   danh sach + KE HOACH LENH chot tu dem qua (SQLite)
    watchlist.gate()   che do phien, tu bang PLAYBOOK cua nightly
    positions.load()   vi the dang mo, tu khoa `scanner:positions`
    quotes.Provider    bao gia

⚠️ KHONG BAO GIO THEM MOT MA NAO NGOAI DANH SACH. Do la dieu kien khong thuong
luong cua prompt 2 va la thu chan co phieu rac quay lai. Them tay thi di qua dung
cua cua watchlist._manual: INSERT INTO watch (sym, kind) VALUES ('XYZ','manual'),
va ma do VAN phai co dong trong `candidates` - khong co ke hoach lenh thi trong
phien lai phai ung bien, dung cai ma ca thiet ke nay dung de tranh. Vong lap doc
lai danh sach moi 5 phut nen khong can restart.

⚠️ GHI VAO DB SAU KHI GUI DUOC, KHONG PHAI TRUOC. Mot tin nhan khong den ma da
ghi "da canh bao" thi mat han. Gui hai lan chi la mot tin trung - re hon nhieu.

⚠️ IM LANG PHAI LA MOT LUA CHON, KHONG BAO GIO LA MOT TRIEU CHUNG. Nguon bao gia
khong phan hoi, danh sach rong, regime cam vao lenh: moi truong hop deu co MOT
tin nhan noi ra, roi im. "Tha bo lo mot alert hon la nhan 40 cai" chi dung khi
minh biet chac vi sao hom nay khong co cai nao.

Ma thoat: 0 dung binh thuong · 1 chet giua phien (da gui Telegram) · 3 khong mo
duoc DB.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import logging.handlers
import sqlite3
import sys
from pathlib import Path

import config
import positions
import quotes
import render_watch
import watch
import watchlist

ROOT = Path(__file__).resolve().parent
DB = ROOT / "state" / "baseline.db"
LOG_FILE = ROOT / "state" / "watchd.log"
LOG_MAX_BYTES = 2_000_000
LOG_BACKUPS = 5

# Doc lai danh sach + vi the moi 5 phut. Danh sach de bat ma them tay (INSERT
# truc tiep) ma khong phai restart; vi the de bat mot lo vua mua duoc bao ve
# ngay trong phien do. Khong doc moi vong: mot lan doc vi the la mot request
# Cloudflare, va 390 request/phien chi de bat mot thay doi hiem la vo ich.
REFRESH_SEC = 300

# Bao nhieu vong lay bao gia THAT BAI lien tiep thi noi ra. 5 vong x 60 giay =
# 5 phut khong co du lieu. Duoi nguong do thi mot cu nhay mang la binh thuong;
# tren nguong do thi im lang khong con la mot lua chon.
SRC_DOWN_AFTER = 5


def _utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:                                        # noqa: BLE001
            pass


def log_setup(quiet: bool = False) -> logging.Logger:
    """File xoay vong + stdout. Giong nightly._log_setup, logger khac ten."""
    lg = logging.getLogger("watchd")
    if lg.handlers:
        return lg
    _utf8()
    lg.setLevel(logging.INFO)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS,
        encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    lg.addHandler(fh)
    if not quiet:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter("%(message)s"))
        lg.addHandler(sh)
    return lg


# ───────────────────────── dau vao cua mot phien ─────────────────────────
def load_ctx(day: str, db=DB, lg: logging.Logger | None = None,
             now: dt.datetime | None = None) -> dict:
    """Doc danh sach + che do + vi the. KHONG NEM: mot phan doc duoc van chay.

    Moi that bai o day bien thanh mot cau trong `warn` va di vao tin nhan mo
    phien. Doc khong duoc danh sach roi chay tiep voi danh sach rong ma khong
    noi gi la kieu that bai giong het mot ngay thi truong khong co co hoi nao.
    """
    ctx: dict = {"day": day, "rows": [], "gate": {}, "pos": None, "warn": [],
                 "loaded": now or dt.datetime.now(dt.UTC)}
    try:
        wl = watchlist.load(db)
        ctx["rows"] = wl.get("rows") or []
        ctx["warn"] += [w for w in (wl.get("warn") or []) if w]
    except sqlite3.Error as e:
        ctx["warn"].append(f"không đọc được danh sách theo dõi "
                           f"({type(e).__name__}) — hôm nay chỉ canh cắt lỗ")
        if lg:
            lg.error(f"watchlist.load: {type(e).__name__}: {e}")
    try:
        ctx["gate"] = watchlist.gate(db)
    except sqlite3.Error as e:
        # gate() tu no da khong nem, nhung neu co thi mac dinh phai la DUNG
        # NGOAI chu khong phai mo het cua.
        ctx["gate"] = {"mode": "stop_only",
                       "why": f"không đọc được trạng thái thị trường "
                              f"({type(e).__name__}) → đứng ngoài"}
        if lg:
            lg.error(f"watchlist.gate: {type(e).__name__}: {e}")
    ctx["pos"] = positions.load(now=now)
    # Thieu bang mui gio thi nen khong co mui gio bi doc thanh UTC, tuc do tre
    # lech 4-5 gio va MOI bao gia bi coi la qua cu - ca phien im lang.
    ctx["warn"] += quotes.tz_warn()
    return ctx


def syms_of(ctx: dict) -> list[str]:
    """Cac ma can lay bao gia: danh sach HOP vi the dang mo.

    Vi the dang mo co mat o day CHI de canh cat lo (watch.evaluate khong chay
    luat vao lenh nao tren chung). Bo chung ra thi mot ma da ban khoi danh sach
    dem qua se khong con ai canh - dung cai truong hop nguy hiem nhat.
    """
    out = {str(r.get("sym") or "").upper() for r in ctx.get("rows") or []}
    out |= set((ctx.get("pos") or {}).get("rows") or {})
    return sorted(s for s in out if s)


def sess_of(ck, now: dt.datetime | None = None) -> dict:
    """{"state","mso","minutes","d"} tu SessionClock. Tach ra de test khong can
    pandas_market_calendars."""
    return {"state": ck.state(now), "mso": ck.mso(now),
            "minutes": ck.session_minutes(now),
            "d": ck.now_et(now).date().isoformat()}


# ───────────────────────── gui ─────────────────────────
async def tg_send(text: str, loud: bool = False, dry: bool = False,
                  lg: logging.Logger | None = None,
                  markup: dict | None = None) -> bool:
    """True = da den tay nguoi doc. Chi khi True moi duoc ghi 'da canh bao'.

    `markup` la inline_keyboard (nut "Bang dieu khien"). No nam NGOAI than tin
    nhan nen --dry-run phai in rieng, khong thi chay thu se khong thay no ton
    tai va bug "mat nut" chi lo ra tren Telegram that.
    """
    if dry:
        print(f"\n{'─' * 60}\n{text}\n")
        for row in (markup or {}).get("inline_keyboard") or []:
            print("  [ " + " ] [ ".join(b.get("text", "?") for b in row) + " ]")
        return True
    import tgapi
    if lg:
        tgapi.log = lg.info
    if not tgapi.ready():
        if lg:
            lg.error("tgapi: chua cau hinh TG_TOKEN/TG_CHAT_ID — khong gui duoc")
        return False
    return bool(await tgapi.send(text, markup, loud=loud))


# ───────────────────────── mot vong ─────────────────────────
async def tick(c: sqlite3.Connection, prov, ctx: dict, sess: dict,
               now: dt.datetime, send, g: dict | None = None,
               lg: logging.Logger | None = None) -> dict:
    """Mot vong quet: lay gia -> do luat -> loc spam -> gui -> ghi.

    `send(text, loud, markup)` duoc TIEM VAO de test khong can mang. Tra ve mot
    ban tom tat de vong ngoai ghi log va quyet dinh co lui nhip khong.
    """
    g = config.INTRADAY if g is None else g
    d = sess.get("d") or ctx["day"]
    syms = syms_of(ctx)
    out = {"n_sym": len(syms), "sent": 0, "held": 0, "skip": 0, "err": 0,
           "cands": 0}
    if not syms:
        return out

    frame = prov.fetch(syms)
    out["err"] = sum(1 for q in frame.values() if q.get("err"))
    cands, skip = watch.evaluate(ctx.get("rows") or [], frame, ctx.get("gate") or {},
                                ctx.get("pos") or {}, sess, g)
    out["cands"], out["skip"] = len(cands), len(skip)
    ctx["skip"] = skip

    gui, giu = watch.decide(cands, watch.load_state(c, d), now, sess, g)
    out["held"] = len(giu)
    for a, ly_do in giu:
        if lg:
            lg.info(f"giu lai {a['sym']} {a['rule']}: {ly_do}")

    for a in gui:
        txt = render_watch.render_alert(a, src=getattr(prov, "name", ""),
                                       pos=ctx.get("pos"))
        # loud=True: Tier 1 la nhung thu phai lam ngay, khong phai thong tin.
        if await send(txt, True, render_watch.keyboard(ctx.get("url", ""))):
            watch.record(c, d, a, now)
            out["sent"] += 1
            if lg:
                lg.info(f"GUI {a['sym']} {a['rule']} @ {a.get('px')}: "
                        f"{a.get('detail')}")
        elif lg:
            # KHONG ghi -> vong sau thu lai. Mot tin khong den ma da danh dau
            # "da canh bao" thi mat han.
            lg.error(f"khong gui duoc {a['sym']} {a['rule']} — se thu lai vong sau")
    return out


def view(ctx: dict, prov, sess: dict, alerts=None, url: str = "",
         dry: bool = False) -> render_watch.WatchView:
    return render_watch.WatchView(
        day=sess.get("d") or ctx["day"], gate=ctx.get("gate") or {},
        watch=ctx.get("rows") or [], pos=ctx.get("pos"),
        skip=ctx.get("skip") or [], warn=ctx.get("warn") or [],
        alerts=alerts or [], src=getattr(prov, "name", ""),
        sess=ctx.get("sess_txt", ""), url=url, dry=dry)


# ───────────────────────── vong doi phien ─────────────────────────
async def run(args, lg: logging.Logger) -> int:
    """Vong lap chinh. Tu bat/tat theo lich phien THAT (nghi le, nua phien, DST).

    Lich lay tu clock.SessionClock (pandas_market_calendars + zoneinfo), khong
    bao gio tu mot offset co dinh: mot nam co hai tuan ma My va Chau Au lech
    nhau mot gio, va trong hai tuan do mot offset cung se lam bot thuc muon
    dung mot gio moi ngay.
    """
    async def chet(cau: str, ma: int) -> int:
        """Khong khoi dong duoc thi PHAI co tin nhan, roi moi thoat.

        Mot tien trinh chet luc khoi dong la truong hop im lang nhat trong ca he
        thong: khong co tin mo phien, khong co canh bao, va trong y het mot ngay
        khong co gi xay ra. Ma thoat chi den duoc cron; tin nhan den duoc nguoi.
        """
        lg.error(cau)
        try:
            await tg_send(f"⛔ <b>Không khởi động được tiến trình canh phiên</b>"
                          f"\n<blockquote>{cau}\nHôm nay sẽ <u>không có cảnh "
                          f"báo nào</u> cho tới khi khởi động lại.</blockquote>",
                          True, args.dry, lg)
        except Exception:                                        # noqa: BLE001
            pass
        return ma

    try:
        from clock import SessionClock
    except Exception as e:                                       # noqa: BLE001
        return await chet(f"không nạp được clock ({type(e).__name__}: {e}) — "
                          "thiếu pandas_market_calendars", 1)
    ck = SessionClock()
    g = dict(config.INTRADAY)
    poll = float(g.get("poll_sec") or 60)
    try:
        c = watch.con(args.db)
    except sqlite3.Error as e:
        return await chet(f"không mở được cơ sở dữ liệu {args.db} "
                          f"({type(e).__name__}: {e})", 3)

    try:
        prov = quotes.get_provider(args.src)
    except Exception as e:                                       # noqa: BLE001
        # Ten nguon sai, hoac file fixture khong co. Khong bao gio duoc lui ve
        # mot nguon khac trong im lang: mot phien chay bang fixture cu se cho ra
        # canh bao theo gia cua hom khac.
        c.close()
        return await chet(f"không mở được nguồn báo giá {args.src!r} "
                          f"({type(e).__name__}: {e})", 1)
    if not args.dry:
        # .env thieu thi KHONG co tin nhan nao ca phien, va do la kieu im lang
        # te nhat: khong the tu bao bang chinh duong da hong. Chi con log.
        try:
            import tgapi
            if not tgapi.ready():
                lg.error("⛔ thieu TG_TOKEN/TG_CHAT_ID trong .env — se KHONG gui "
                         "duoc canh bao nao. Chay scripts/check_tg.py de kiem.")
        except Exception as e:                                   # noqa: BLE001
            lg.error(f"khong nap duoc tgapi ({type(e).__name__}: {e}) — "
                     "se KHONG gui duoc canh bao nao")
    url = ""
    try:
        import nightly
        url = nightly.dashboard_url()
    except Exception:                                            # noqa: BLE001
        pass

    async def send(txt: str, loud: bool = False,
                   markup: dict | None = None) -> bool:
        return await tg_send(txt, loud, args.dry, lg, markup)

    async def nap(sec: float) -> bool:
        """Ngu giua hai vong. Tra False khi --once: khong con vong sau de cho.

        Cho roi moi kiem --once la mot cai bay: `--once --dry-run` in ra ket qua
        trong mot giay roi treo them 60s (ngoai phien) hoac 240s (AFTERHOURS)
        truoc khi thoat, nen no giong het mot tien trinh chet dung. Ngu phai la
        viec dau tien bo qua khi biet minh khong chay vong nua.
        """
        if args.once:
            return False
        await asyncio.sleep(sec)
        return True

    ctx: dict = {"day": "", "rows": [], "gate": {}, "pos": None, "warn": []}
    fail = 0
    lg.info(f"watchd: bat dau · nguon {prov.name} · "
            f"{'CHAY THU (khong gui)' if args.dry else 'gui that'}")
    try:
        while True:
            now = dt.datetime.now(dt.UTC)
            sess = sess_of(ck, now)
            d, state = sess["d"], sess["state"]

            # Doi ngay, hoac dau vao da cu -> doc lai. Sang som doc lai la cach
            # danh sach cua dem qua vao duoc phien hom nay.
            if d != ctx.get("day") or (
                    now - ctx.get("loaded", now)).total_seconds() >= REFRESH_SEC:
                ctx = load_ctx(d, args.db, lg, now)
                ctx["url"] = url
                ctx["sess_txt"] = ck.describe(now).splitlines()[0]
                for w in ctx["warn"]:
                    lg.info(f"canh bao dau vao: {w}")

            if state in ("OPENING", "LIVE", "CLOSING"):
                if watch.once(c, d, "open", now):
                    # Tin mo phien: hom nay canh gi va vi sao. Trong che do
                    # stop_only day la tin DUY NHAT ca phien.
                    v = view(ctx, prov, sess, url=url, dry=args.dry)
                    if not await send(render_watch.render_open(v), False,
                                      render_watch.render_keyboard(v)):
                        lg.error("khong gui duoc tin mo phien")
                r = await tick(c, prov, ctx, sess, now, send, g, lg)
                lg.info(f"{state} mso={sess['mso']} · {r['n_sym']} ma · "
                        f"{r['cands']} ung vien · gui {r['sent']} · giu "
                        f"{r['held']} · bo qua {r['skip']} · loi gia {r['err']}")
                # Nguon im lang thi phai co mot tieng noi. Nguoc lai mot API
                # chet giong het mot phien khong co gi xay ra.
                if r["n_sym"] and r["err"] >= r["n_sym"]:
                    fail += 1
                    if fail >= SRC_DOWN_AFTER and watch.once(c, d, "src_down", now):
                        await send(
                            "⚠️ <b>Nguồn báo giá không phản hồi</b>\n"
                            f"<blockquote>{fail} vòng liên tiếp không lấy được "
                            "giá. Cảnh báo trong phiên đang <u>không hoạt "
                            "động</u> — im lặng sau tin này không có nghĩa là "
                            "không có gì xảy ra.</blockquote>", True)
                else:
                    fail = 0
                # Lui nhip khi that bai lien tiep, toi da 4x.
                if not await nap(poll * min(4, 1 + fail)):
                    return 0
            elif state == "AFTERHOURS":
                if watch.once(c, d, "summary", now):
                    v = view(ctx, prov, sess, watch.today_rows(c, d), url,
                             args.dry)
                    await send(render_watch.render_summary(v), False,
                               render_watch.render_keyboard(v))
                    lg.info("da gui tong ket phien")
                if not await nap(240):
                    return 0
            else:
                # Ngoai phien: khong lay gia, khong gui gi. Ngu ngan de bat kip
                # luc mo cua (va de Ctrl-C khong phai cho 10 phut).
                if not await nap(60):
                    return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        lg.info("watchd: dung theo yeu cau")
        return 0
    except Exception as e:                                       # noqa: BLE001
        # Chet giua phien PHAI co tin nhan. Mot tien trinh canh phien chet am
        # tham thi ngay hom do khong ai canh cat lo, va khong ai biet.
        lg.exception("watchd: chet giua phien")
        try:
            await send(f"⛔ <b>Tiến trình canh phiên đã chết</b>\n"
                       f"<blockquote>{type(e).__name__}: {str(e)[:300]}\n"
                       f"Không còn cảnh báo nào cho tới khi khởi động lại."
                       f"</blockquote>", True)
        except Exception:                                        # noqa: BLE001
            pass
        return 1
    finally:
        c.close()


# ───────────────────────── selftest / CLI ─────────────────────────
def _smoke() -> None:
    """Kiem vong `tick` bang fixture: khong mang, khong DB that, khong Telegram."""
    import tempfile

    now = dt.datetime(2026, 9, 25, 15, 0, tzinfo=dt.UTC)
    sess = {"state": "LIVE", "mso": 90, "minutes": 390, "d": "2026-09-25"}
    ctx = {"day": "2026-09-25", "gate": {"mode": "full"}, "pos": None,
           "warn": [], "loaded": now,
           "rows": [{"sym": "NVDA", "ref_close": 100.0, "trigger": 102.0,
                     "stop": 97.0, "target": 112.0, "adv50": 1_000_000.0,
                     "size_pct": 0.2, "stop_pct": 0.049, "risk_pct": 0.01}]}
    prov = quotes.FixtureProvider([{"now": now.isoformat(), "rows": [
        {"sym": "NVDA", "ts": "2026-09-25T14:55:00+00:00", "o": 100.5,
         "h": 103, "l": 100, "c": 102.5, "v": 400_000}]}])
    ctx["url"] = "https://example.com/#scanner"
    sent: list[str] = []
    kbs: list[dict | None] = []

    async def send(txt: str, loud: bool = False,
                   markup: dict | None = None) -> bool:
        sent.append(txt)
        kbs.append(markup)
        return True

    c = watch.con(Path(tempfile.mkdtemp()) / "t.db")
    r = asyncio.run(tick(c, prov, ctx, sess, now, send))
    assert r["sent"] == 1 and r["cands"] == 1, r
    assert "NVDA" in sent[0] and "102.00" in sent[0], sent[0]
    # Nut "Bang dieu khien" di theo canh bao qua reply_markup, khong nam trong chu.
    assert "<a href" not in sent[0], "link dashboard quay lai than tin nhan"
    assert kbs[0] and kbs[0]["inline_keyboard"][0][0]["url"] == ctx["url"], kbs[0]
    # Vong hai: da ghi vao DB -> khong gui lai.
    r2 = asyncio.run(tick(c, prov, ctx, sess, now, send))
    assert r2["sent"] == 0 and r2["held"] == 1, r2
    assert len(sent) == 1

    # Gui that bai -> KHONG ghi -> vong sau thu lai.
    async def fail(txt: str, loud: bool = False,
                   markup: dict | None = None) -> bool:
        return False

    c2 = watch.con(Path(tempfile.mkdtemp()) / "t.db")
    assert asyncio.run(tick(c2, prov, ctx, sess, now, fail))["sent"] == 0
    assert watch.today_rows(c2, sess["d"]) == [], "gui hong thi khong duoc ghi"
    assert asyncio.run(tick(c2, prov, ctx, sess, now, send))["sent"] == 1

    # Vi the dang mo duoc gop vao danh sach ma can lay gia, du khong o watchlist.
    ctx2 = dict(ctx, pos=positions.parse(
        {"rows": [{"sym": "AAPL", "shares": 1, "cur": "USD", "stops": [90]}]},
        int(now.timestamp() * 1000), now=now))
    assert syms_of(ctx2) == ["AAPL", "NVDA"], syms_of(ctx2)
    c.close()
    c2.close()
    print("watchd.py: smoke ok")


def main() -> int:
    ap = argparse.ArgumentParser(description="canh bao trong phien (Tier 1)")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--src", help="nguon bao gia: yf | fixture:duong/dan.json")
    ap.add_argument("--dry-run", dest="dry", action="store_true",
                    help="khong gui Telegram, in ra stdout")
    ap.add_argument("--once", action="store_true", help="mot vong roi thoat")
    ap.add_argument("--quiet", action="store_true", help="chi ghi file log")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        _smoke()
        return 0
    return asyncio.run(run(a, log_setup(a.quiet)))


if __name__ == "__main__":
    sys.exit(main())
