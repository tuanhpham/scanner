"""nightly.py — CHUOI SWING BUOI SANG. Mot cho duy nhat cron goi.

    python nightly.py                 # chay that
    python nightly.py --dry-run       # tinh het, khong ghi, khong gui, khong day
    python nightly.py --only regime,sectors
    python nightly.py --status        # in lai lan chay gan nhat, khong chay gi

═══ VI SAO FILE NAY TON TAI ═══

Truoc day cron chay MOT dong noi bang `&&`:

    bars.py --sync && prep.py --from-bars && structure.py --build && setups.py --build

`&&` la mot cong tac IM LANG. `structure.py` do thi moi thu phia sau bi bo qua:
khong co Telegram, khong co push, dashboard van hien snapshot cua hom qua, va
khong co gi o bat cu dau noi rang chuyen do vua xay ra. Sang hom sau nhin vao
danh sach theo doi thi no van o day - chi la cu mot ngay.

Nen file nay dao nguoc thu tu uu tien: BAO CAO la viec bat buoc, TINH TOAN la
viec co the that bai. Mot buoc do thi chuoi dung, nhung push va Telegram VAN
chay, va tin nhan noi ra chinh xac buoc nao do va vi sao.

═══ NEN QUYET DINH ═══

Cron chay 08:00 ET, TRUOC khi mo cua (09:30 ET). Nen moi nhat trong kho luc do
la nen HOM QUA, va do la nen quyet dinh: moi con so trong bao cao nay duoc tinh
tren mot phien DA CHOT.

Ba lop bao ve, doc lap nhau:
  1. bars.sync(drop_partial=True) khong ghi nen cua ngay dang chay.
  2. regime._closed() / sectors._closed() bo nen cuoi neu no la hom nay.
  3. _check_bar() o day so nen quyet dinh voi ngay ET hien tai va BAO DONG neu
     hai cai trung nhau - trong log, trong tin nhan, va tren dashboard.

Lop 3 khong thay the hai lop tren; no la cai bao rang hai lop tren da hong.

═══ MA THOAT (cron doc duoc) ═══

    0  tat ca xong
    1  mot buoc BAT BUOC do -> khong co danh sach theo doi hom nay
    2  chi buoc khong bat buoc do (push / telegram) -> so lieu van dung
    3  khong mo duoc DB -> chua chay duoc gi ca

Khac 0 thi cron ghi vao mail/log cua he thong; day la lop bao cuoi cung cho
truong hop Telegram cung chet.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import logging.handlers
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import bars            # noqa: E402
import config          # noqa: E402
import regime          # noqa: E402
import render_night    # noqa: E402
import sectors         # noqa: E402
import setups          # noqa: E402
import structure       # noqa: E402
import watchlist       # noqa: E402

DB = ROOT / "state" / "baseline.db"
LOG_FILE = ROOT / "state" / "nightly.log"

# Khong import clock.py: no keo theo pandas_market_calendars o cap module, va
# thu duy nhat viec cua no la BAO CAO LOI thi khong duoc phu thuoc vao mot goi
# co the thieu. Chi can mui gio, va zoneinfo la stdlib (DST tu dong ca hai chieu).
ET_KEY = "America/New_York"
_ET: ZoneInfo | None = None
_ET_ERR: str | None = None


def _et() -> ZoneInfo | None:
    """Mui gio New York, lay MUON va khong bao gio nem loi.

    zoneinfo la stdlib nhung BANG DU LIEU mui gio thi khong: Linux co
    /usr/share/zoneinfo, Windows khong co gi va can goi `tzdata`. Neu de
    ZoneInfo() o cap module thi tren may dev Windows ca file nay khong import
    duoc - tuc la thu duy nhat co viec bao loi lai la thu chet truoc tien.

    Khong co bang mui gio thi lui ve UTC va NOI RA (xem tz_warn). Khong bao gio
    cung mot offset co dinh: -5 gio se sai suot 8 thang trong nam.
    """
    global _ET, _ET_ERR
    if _ET is None and _ET_ERR is None:
        try:
            _ET = ZoneInfo(ET_KEY)
        except Exception as e:                                # noqa: BLE001
            _ET_ERR = f"{type(e).__name__}: {e}"
    return _ET


def tz_warn() -> list[str]:
    if _et() is not None:
        return []
    # Canh bao viet CO DAU: nguoi doc chinh la tin nhan Telegram. panel() bo dau
    # bang _ascii() nen khong can ban thu hai. Xem docstring cua _ascii().
    return [f"Không đọc được múi giờ {ET_KEY} ({_ET_ERR}) — đang dùng ngày UTC "
            f"thay thế. Cài gói `tzdata` (pip install tzdata). Lúc 08:00 ET thì "
            f"ngày UTC trùng ngày ET nên thường không lệch, nhưng giờ chạy khác "
            f"thì sẽ lệch."]

LOG_MAX_BYTES = 2_000_000
LOG_BACKUPS = 5

# Ti le ma tai that bai ma tren nguong nay thi coi buoc `bars` la do. 5 ma loi
# trong 250 la binh thuong (ma bi huy niem yet, ticker doi ten). 40% loi la
# yfinance dang chan, va luc do xep hang percentile tinh tren nua universe se
# khac han - im lang di qua thi khong ai biet.
BARS_FAIL_SHARE = 0.25

# Bao nhieu lan chay giu lai trong bang `night`. Du de tra loi "tuan nay co hom
# nao do khong", khong du de thanh mot cai kho log thu hai.
KEEP_RUNS = 60

DDL = """
CREATE TABLE IF NOT EXISTS night(
  run_id TEXT PRIMARY KEY,      -- ISO UTC luc bat dau; mot dong moi lan chay
  day TEXT,                     -- ngay ET luc chay
  bar TEXT,                     -- nen quyet dinh
  ok INTEGER,                   -- 1 = moi buoc xong
  code INTEGER,                 -- ma thoat
  sec REAL,
  dry INTEGER,
  stages TEXT,                  -- JSON: [{stage, ok, fatal, sec, err, detail}]
  warn TEXT,                    -- JSON: [str]
  updated TEXT);
"""


def _utf8() -> None:
    """Bat stdout/stderr nhan duoc chu co dau, khong nem loi.

    Tin nhan Telegram co dau di qua log. Console Windows mac dinh la cp1252:
    ghi mot chu "ế" vao do nem UnicodeEncodeError, va vi no xay ra BEN TRONG
    logging thi loi khong noi len ma chi in mot "--- Logging error ---" roi
    lang le bo dong log do. Nghia la dung cai dung de chan that bai im lang lai
    la cai that bai im lang. errors="replace" -> mat dau con hon mat dong log.

    Tren VM (Ubuntu, LANG=C.UTF-8) ham nay khong doi gi.
    """
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:                                        # noqa: BLE001
            pass


def _log_setup(quiet: bool = False) -> logging.Logger:
    """File xoay vong + stdout. Cron giu stdout, nen hai duong khong trung nhau:
    file la lich su, stdout la cai roi vao mail cua cron khi ma thoat khac 0.
    """
    lg = logging.getLogger("nightly")
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


# ───────────────────────── nen quyet dinh ─────────────────────────
def today_et() -> str:
    tz = _et()
    return dt.datetime.now(tz or dt.timezone.utc).date().isoformat()


def dashboard_url(url: str | None = None) -> str:
    """Link dashboard, suy ra TU `SCANNER_PUSH_URL` chu khong phai bien rieng.

    Cung mot domain, nen mot bien nua chi de hai bien lech nhau:
        https://x.pages.dev/api/scanner  ->  https://x.pages.dev/#scanner
    Chua cau hinh push thi cung khong co dashboard de link -> tra "".

    Doc bien moi truong TRUC TIEP chu khong qua `push.URL`: push.py chot gia tri
    do luc import, nen thu tu import lai quyet dinh ket qua cua ham nay. Trong
    cron hai cach cho cung mot gia tri, nhung "dung trong cron" khong phai la
    thu co the kiem tra duoc.
    """
    u = (os.getenv("SCANNER_PUSH_URL", "") if url is None else url).strip()
    u = u.rstrip("/")
    if not u:
        return ""
    base = u[:-len("/api/scanner")] if u.endswith("/api/scanner") else u
    return f"{base}/#scanner"


def _check_bar(bar: str | None, day: str | None = None) -> list[str]:
    """Canh bao ve nen quyet dinh. Rong = khong co gi dang noi.

    Hai truong hop, hai muc do khac nhau:
      · bar == hom nay -> NHIN TRUOC TUONG LAI. Bao cao dua tren mot phien chua
        chot; moi con so con doi truoc khi thi truong dong cua. Day la loi nang
        nhat trong ca he thong va no khong tu bao, nen no phai duoc goi ten.
      · bar cu hon 4 ngay lich -> cuoi tuan dai hoac ngay le thi binh thuong;
        hon the la kho nen dang khong duoc lam moi.
    """
    if not bar:
        return ["Không đọc được nến quyết định: bảng `regime` chưa có dòng nào."]
    day = day or today_et()
    if bar > day:
        return [f"Nến quyết định ({bar}) ở TƯƠNG LAI so với ngày ET ({day}). "
                f"Đồng hồ của VM hoặc dữ liệu nguồn có vấn đề."]
    if bar == day:
        return [f"Nến quyết định ({bar}) TRÙNG ngày chạy. Báo cáo dựa trên một "
                f"phiên chưa chốt — không được vào lệnh theo nó. Kiểm tra "
                f"bars.partial_day() và regime._closed()."]
    tre = (dt.date.fromisoformat(day) - dt.date.fromisoformat(bar)).days
    if tre > 4:
        return [f"Nến quyết định ({bar}) cách ngày chạy {tre} ngày. Kho nến "
                f"đang không được làm mới — xem bước `bars`."]
    return []


# ───────────────────────── tung buoc ─────────────────────────
def _sub(argv: list[str], timeout: float = 1800) -> tuple[int, str]:
    """Chay mot module cua repo nhu tien trinh rieng. Tra (ma_thoat, dong_cuoi).

    Dung cho `prep.py`: no import pandas o cap module, nen goi truc tiep se lam
    CA nightly.py khong import duoc tren may thieu pandas - dung cai thu ma viec
    cua no la bao loi. Tien trinh rieng con co nghia la mot MemoryError trong
    pandas khong keo theo bao cao.
    """
    r = subprocess.run([sys.executable, *argv], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
    return r.returncode, (out[-1] if out else "")


def st_bars(db, dry: bool, lg: logging.Logger) -> dict:
    if dry:
        # --dry-run khong goi mang. Khong phai de nhanh: `bars.sync` GHI vao
        # `bars`, va mot lan chay thu khong duoc thay doi kho nen.
        return {"detail": "bỏ qua (--dry-run không gọi mạng)", "skipped": True}
    syms = bars.sync_list(quiet=True)
    st = bars.sync(db, syms, drop_partial=True)
    n = max(len(syms), 1)
    share = st["fail"] / n
    if share > BARS_FAIL_SHARE:
        raise RuntimeError(
            f"{st['fail']}/{n} mã tải thất bại ({share:.0%} > "
            f"{BARS_FAIL_SHARE:.0%}). Nguồn dữ liệu đang chặn hoặc mất mạng; "
            f"xếp hạng percentile trên phần còn lại sẽ lệch.")
    return {"detail": f"{st['syms']} mã · {st['rows']} nến ghi · "
                      f"{st['fail']} lỗi · bỏ {st['bo_nen_dang_chay']} nến "
                      f"đang chạy",
            "syms": st["syms"], "rows": st["rows"], "fail": st["fail"]}


def st_prep(db, dry: bool, lg: logging.Logger) -> dict:
    if dry:
        return {"detail": "bỏ qua (--dry-run)", "skipped": True}
    code, last = _sub(["prep.py", "--from-bars"])
    if code != 0:
        raise RuntimeError(f"prep.py --from-bars thoát {code}: {last}")
    return {"detail": last}


def st_regime(db, dry: bool, lg: logging.Logger) -> dict:
    r = regime.build(db, dry=dry)
    if r["err"]:
        raise RuntimeError(r["err"])
    row = r["row"]
    return {"detail": f"{row['d']} · {row['trend']} · {row['vol']} · "
                      f"cỡ vị thế {row['size']:.0%}",
            "row": row, "bar": row["d"]}


def st_sectors(db, dry: bool, lg: logging.Logger) -> dict:
    r = sectors.build(db, dry=dry)
    if r["err"]:
        raise RuntimeError(r["err"])
    top = [x["sym"] for x in r["rows"][:3]]
    return {"detail": f"{r['d']} · top 3: {' '.join(top)}",
            "rows": r["rows"], "bar": r["d"], "warn": list(r["warn"])}


def st_structure(db, dry: bool, lg: logging.Logger) -> dict:
    # structure.build() khong co `dry`: no ghi de ca bang moi lan, va bang do la
    # so lieu DAN XUAT - chay lai tu `bars` la ra y nguyen. Trong --dry-run ta
    # KHONG goi no (xem run()); o day chi de ro rang la vay.
    r = structure.build(db)
    if not r["co_so_lieu"]:
        raise RuntimeError(
            f"đo được 0/{r['da_xet']} mã. Kho nến rỗng hoặc quá ngắn → chạy "
            f"`python bars.py --sync --full`.")
    return {"detail": f"{r['co_so_lieu']}/{r['da_xet']} mã đo được · "
                      f"{r['co_nen']} có nền tích lũy · {r['loi']} lỗi",
            "n_struct": r["co_so_lieu"]}


def st_setups(db, dry: bool, lg: logging.Logger) -> dict:
    r = setups.build(db, dry=dry)
    return {"detail": f"BO {r['BO']} · RV {r['RV']} · LEAD {r['LEAD']} · "
                      f"top sector {' '.join(r['top_sector']) or '-'}",
            "rows": r["rows"], "warn": list(r["warn"]),
            "top_sector": list(r["top_sector"])}


def st_mktcap(db, dry: bool, lg: logging.Logger) -> dict:
    """Von hoa that cho dung cac ma trong danh sach, cache vao `base.mktcap`.

    Chay SAU setups vi no doc chinh cac dong LEAD vua ghi. <= 10 ma, TTL 7 ngay,
    nen thuc te la mot chu so request moi dem - va thuong la 0 vi cache con han.

    Khong bat buoc, va co chu y: thieu von hoa KHONG loai ma nao ca, phan
    intraday se ghi "chưa biết vốn hóa" vao canh bao. Mot lan yfinance hong
    khong duoc phep huy ca chuoi chay dem.
    """
    if dry:
        return {"detail": "--dry-run: không gọi mạng", "skipped": True}
    r = watchlist.refresh_mktcap(db)
    if r["err"]:
        raise RuntimeError(r["err"])
    d = (f"{r['ok']}/{r['asked']} mã lấy được · {r['cached']} còn hạn cache")
    if r["fail"]:
        # Khong nem: cac ma khac van co so. Nhung phai noi ra, vi ma thieu von
        # hoa se di vao phien voi ghi chu "chưa biết vốn hóa" thay vi bi loai.
        d += f" · {r['fail']} mã không lấy được (sẽ vào phiên kèm ghi chú)"
    return {"detail": d, "warn": ([f"{r['fail']} mã chưa biết vốn hóa"]
                                  if r["fail"] else [])}


def st_push(db, dry: bool, lg: logging.Logger) -> dict:
    import push                                 # doc .env, co the tu vo hieu
    if not push.ready():
        # Khong cau hinh push KHONG phai loi: scanner chay duoc ma khong can
        # dashboard. Ghi ro de khong ai di tim mot loi khong ton tai.
        return {"detail": "chưa cấu hình (thiếu SCANNER_PUSH_URL / SCANNER_TOKEN)",
                "skipped": True}
    out = push.push_all(db, dry=dry)
    return {"detail": " · ".join(f"{k}={v}" for k, v in out.items())}


def st_telegram(db, dry: bool, lg: logging.Logger, view=None) -> dict:
    txt = render_night.render_night(view)
    if dry:
        # In ra de doc bang mat. Day la ca muc dich cua --dry-run: xem tin nhan
        # TRUOC khi no den dien thoai.
        print("\n" + "─" * 72 + f"\nTIN NHẮN ({len(txt)} ký tự)\n"
              + "─" * 72 + f"\n{txt}\n" + "─" * 72)
        return {"detail": f"{len(txt)} ký tự (--dry-run: không gửi)",
                "skipped": True, "text": txt}
    import asyncio
    import tgapi
    if not tgapi.ready():
        return {"detail": "chưa cấu hình (thiếu TG_TOKEN / TG_CHAT_ID)",
                "skipped": True, "text": txt}
    if not asyncio.run(tgapi.send(txt)):
        raise RuntimeError("tgapi.send() trả về False → xem log của tgapi.")
    return {"detail": f"đã gửi, {len(txt)} ký tự", "text": txt}


# `fatal`: do thi DUNG chuoi. Quy tac: buoc nao ma neu thieu no se khien buoc
# sau tinh ra mot con so TRONG NHU THAT thi buoc do bat buoc.
#   bars      - thieu nen moi thi ca bao cao la cua hom qua, giong het tin nhan
#               hom qua, va khong co gi phan biet. Tha khong co danh sach.
#   sectors   - Stage 3 lay ma tu top 3 sector. Khong co xep hang thi khong co
#               nguon; danh sach rong se bi doc thanh "hom nay khong co gi".
#   structure - setups.py doc tu bang `struct`.
#   setups    - chinh la san pham.
# Khong bat buoc:
#   prep      - chi anh huong setup RV (diem co ban). BO/LEAD chay binh thuong.
#   regime    - bao cao thieu phan boi canh nhung danh sach van dung.
#   mktcap    - thieu von hoa khong loai ma nao; phan intraday ghi "chua biet
#               von hoa" vao canh bao. Mot lan yfinance hong khong duoc huy dem.
#   push      - dashboard cu mot nhip; Telegram van den.
#   telegram  - so lieu da ghi xong; ma thoat 2 lo ra cho cron.
STAGES: tuple[tuple[str, bool, object], ...] = (
    ("bars", True, st_bars),
    ("prep", False, st_prep),
    ("regime", False, st_regime),
    ("sectors", True, st_sectors),
    ("structure", True, st_structure),
    ("setups", True, st_setups),
    ("mktcap", False, st_mktcap),
)
NAMES = tuple(n for n, _, _ in STAGES) + ("push", "telegram")
FATAL = {n for n, f, _ in STAGES if f}


# ───────────────────────── bang `night` ─────────────────────────
def con(db=DB) -> sqlite3.Connection:
    c = bars.con(db) if not isinstance(db, sqlite3.Connection) else db
    c.executescript(DDL)
    return c


def save_run(db, res: dict) -> None:
    c, mine = bars._c(db)
    try:
        c.executescript(DDL)
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        with c:
            c.execute(
                "INSERT INTO night(run_id,day,bar,ok,code,sec,dry,stages,warn,"
                "updated) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET day=excluded.day,"
                "bar=excluded.bar,ok=excluded.ok,code=excluded.code,"
                "sec=excluded.sec,dry=excluded.dry,stages=excluded.stages,"
                "warn=excluded.warn,updated=excluded.updated",
                (res["run_id"], res["day"], res.get("bar"), int(res["ok"]),
                 int(res["code"]), float(res["sec"]), int(res["dry"]),
                 json.dumps(res["stages"], ensure_ascii=False),
                 json.dumps(res.get("warn", []), ensure_ascii=False), now))
            c.execute("DELETE FROM night WHERE run_id NOT IN "
                      "(SELECT run_id FROM night ORDER BY run_id DESC LIMIT ?)",
                      (KEEP_RUNS,))
    finally:
        if mine:
            c.close()


def _row(r: tuple) -> dict:
    keys = ("run_id", "day", "bar", "ok", "code", "sec", "dry", "stages",
            "warn", "updated")
    d = dict(zip(keys, r))
    d["ok"] = bool(d["ok"])
    d["dry"] = bool(d["dry"])
    for k in ("stages", "warn"):
        try:
            d[k] = json.loads(d[k] or "[]")
        except (TypeError, ValueError):
            d[k] = []
    return d


def runs(db=DB, n: int = 10, ok_only: bool = False) -> list[dict]:
    """`n` lan chay gan nhat, MOI NHAT TRUOC. `ok_only`: chi lan xong tron."""
    c, mine = bars._c(db)
    try:
        c.executescript(DDL)
        q = ("SELECT run_id,day,bar,ok,code,sec,dry,stages,warn,updated "
             "FROM night" + (" WHERE ok=1 AND dry=0" if ok_only else "")
             + " ORDER BY run_id DESC LIMIT ?")
        rows = c.execute(q, (int(n),)).fetchall()
    finally:
        if mine:
            c.close()
    return [_row(r) for r in rows]


def last_run(db=DB) -> dict | None:
    r = runs(db, 1)
    return r[0] if r else None


def last_ok(db=DB) -> dict | None:
    """Lan chay THAT gan nhat ma moi buoc deu xong.

    Dashboard can dung con so nay chu khong phai `last_run`: "lan chay gan nhat"
    co the la mot lan do sach, va luc do "lan cuoi thanh cong" moi tra loi duoc
    cau "so lieu tren man hinh nay cu bao nhieu".
    """
    r = runs(db, 1, ok_only=True)
    return r[0] if r else None


# ───────────────────────── chay ─────────────────────────
def run(db=DB, dry: bool = False, only: set[str] | None = None,
        lg: logging.Logger | None = None) -> dict:
    """Chay ca chuoi. Khong bao gio nem loi: loi la du lieu dau ra o day."""
    lg = lg or _log_setup()
    t0 = time.time()
    run_id = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    day = today_et()
    res: dict = {"run_id": run_id, "day": day, "dry": dry, "stages": [],
                 "warn": [], "bar": None, "ok": False, "code": 0, "sec": 0.0}

    lg.info("═" * 66)
    lg.info(f"nightly {run_id} · ngay ET {day}"
            + ("  [--dry-run]" if dry else ""))

    try:
        c = con(db)
    except (sqlite3.Error, OSError) as e:
        # OSError cung phai bat: con() goi mkdir() truoc khi sqlite3 nhin thay
        # duong dan, nen dia chi doc, thu muc khong ton tai hay duong dan tro vao
        # mot file deu nem OSError chu khong phai sqlite3.Error. De no bay len thi
        # thanh traceback, khong co ma thoat 3 va khong co tin nhan - dung cai
        # that bai im lang ma ca module nay ton tai de chan.
        #
        # Chua chay duoc gi ca, va cung khong ghi lai duoc lan chay nay. Ma
        # thoat 3 la duong bao duy nhat con lai.
        lg.error(f"!! khong mo duoc DB {db}: {type(e).__name__}: {e}")
        res["code"] = 3
        res["stages"] = [{"stage": "db", "ok": False, "fatal": True, "sec": 0.0,
                          "err": f"{type(e).__name__}: {e}"}]
        res["sec"] = time.time() - t0
        return res

    ctx: dict = {}
    stopped: str | None = None

    def do(name: str, fatal: bool, fn, *a) -> dict:
        """Chay mot buoc, GHI KET QUA du thanh hay bai, khong bao gio nem loi.

        Mot ham duy nhat cho ca 8 buoc: ba ban sao cua khoi try/ghi/log nay se
        lech nhau o dung cho quan trong nhat - cho ghi lai loi.
        """
        t = time.time()
        rec: dict = {"stage": name, "fatal": fatal, "ok": False, "sec": 0.0,
                     "err": None}
        try:
            out = fn(*a) or {}
            rec.update(ok=True, detail=out.get("detail", ""),
                       skipped=bool(out.get("skipped")))
            lg.info(f"  {name:<10} OK   {time.time() - t:6.1f}s  "
                    f"{out.get('detail', '')}")
        except Exception as e:                                # noqa: BLE001
            # Bat rong CO Y, va chi o day: mot buoc vo khong duoc phep lam mat
            # bao cao. Ten lop loi di kem vao tin nhan nen khong co gi bi che.
            out = {}
            rec["err"] = f"{type(e).__name__}: {e}"
            lg.error(f"  {name:<10} LOI  {time.time() - t:6.1f}s  {rec['err']}")
        rec["sec"] = round(time.time() - t, 2)
        res["stages"].append(rec)
        return out

    try:
        for name, fatal, fn in STAGES:
            if only and name not in only:
                continue
            if stopped:
                # Khong chay, va cung KHONG ghi la "do": ghi la do thi tin nhan
                # se ke ra 4 loi trong khi chi co mot nguyen nhan.
                res["stages"].append({
                    "stage": name, "ok": False, "fatal": fatal, "sec": 0.0,
                    "err": f"khong chay: buoc `{stopped}` da dung chuoi",
                    "blocked": True})
                continue
            out = do(name, fatal, fn, c, dry, lg)
            if res["stages"][-1]["ok"]:
                ctx[name] = out
                if out.get("bar"):
                    res["bar"] = out["bar"]
                res["warn"] += list(out.get("warn") or [])
            elif fatal:
                stopped = name
                lg.error(f"  -> `{name}` bat buoc: dung chuoi, van bao cao.")

        # Nen quyet dinh: kiem tra SAU khi regime/sectors da chay, vi chinh
        # chung tra ve ngay do.
        res["warn"] += tz_warn() + _check_bar(res["bar"], day)

        try:
            view = build_view(c, res, ctx, day)
        except Exception as e:                                # noqa: BLE001
            # Dung duoc tin nhan la viec bat buoc; dung duoc tin nhan DAY DU thi
            # khong. Mot view rong + khoi "chuoi chay khong xong" van tot hon im
            # lang, nen loi o day di vao tin nhan chu khong dung tin nhan.
            lg.error(f"  build_view  LOI  {type(e).__name__}: {e}")
            res["warn"].append(f"không dựng được báo cáo đầy đủ: "
                               f"{type(e).__name__}: {e}")
            view = render_night.NightView(day=day, bar=res.get("bar"), dry=dry,
                                          url=dashboard_url())

        if not only or "push" in only:
            do("push", False, st_push, c, dry, lg)

        # Telegram sau cung va LUON chay: no can biet ca ket qua cua push.
        # `stages` duoc gan lai ngay truoc khi dung tin nhan, de khoi "chuoi chay
        # khong xong" phan anh dung trang thai cuoi cung.
        if not only or "telegram" in only:
            view.stages = list(res["stages"])
            view.warn = list(dict.fromkeys(res["warn"]))
            do("telegram", False, st_telegram, c, dry, lg, view)
    finally:
        if not isinstance(db, sqlite3.Connection):
            c.close()

    res["warn"] = list(dict.fromkeys(res["warn"]))
    bad = [s for s in res["stages"] if not s["ok"]]
    res["ok"] = not bad
    res["code"] = 0 if not bad else (1 if any(s["fatal"] for s in bad) else 2)
    res["sec"] = round(time.time() - t0, 2)

    if not dry:
        try:
            save_run(db, res)
        except sqlite3.Error as e:
            lg.error(f"!! khong ghi duoc bang `night`: {e}")

    # Dem RIENG "do" va "bi chan": mot con so gop lai ("4 buoc do") lam nguoi
    # doc di tim bon nguyen nhan trong khi chi co mot. Giong render_fail().
    n_do = sum(1 for s in res["stages"] if not s["ok"] and not s.get("blocked"))
    n_chan = sum(1 for s in res["stages"] if s.get("blocked"))
    lg.info(f"XONG sau {res['sec']:.1f}s · ma thoat {res['code']}"
            + (f" · {n_do} buoc do" if n_do else "")
            + (f" · {n_chan} buoc bi chan" if n_chan else ""))
    return res


def build_view(c, res: dict, ctx: dict, day: str) -> render_night.NightView:
    """Gom du lieu cho tin nhan. Doc lai tu DB chu khong tin vao `ctx`.

    Vi sao doc lai: khi buoc `sectors` do thi `ctx` khong co gi, nhung bang
    `sector_rank` van con xep hang cua HOM QUA - va tin nhan nen noi "day la
    xep hang cua hom qua" chu khong phai khong noi gi. Nguoc lai, o --dry-run
    thi DB khong duoc ghi, nen `ctx` moi la thu duy nhat dung. Nen: `ctx` truoc,
    DB lam du bi.
    """
    v = render_night.NightView(day=day, bar=res.get("bar"), dry=res["dry"],
                               url=dashboard_url())
    v.regime = (ctx.get("regime", {}).get("row")
                or (regime.latest(c) if not res["dry"] else None))
    hist = regime.history(c, n=2)
    if len(hist) >= 2 and v.regime and hist[-1].get("d") == v.regime.get("d"):
        v.prev = hist[-2]

    v.sectors = ctx.get("sectors", {}).get("rows") or sectors.load_rank(c)
    if v.sectors:
        v.chg = sectors.changes(c)
        v.top_sectors = [r["sym"] for r in v.sectors[:3]]

    rows = ctx.get("setups", {}).get("rows")
    if rows is not None:
        v.watch = sorted((r for r in rows if r.get("setup") == "LEAD"),
                         key=lambda r: -(r.get("quality") or 0))
    else:
        # load_candidates() khoa theo `sym` va KHONG de `sym` trong gia tri; tin
        # nhan can no de in cot dau tien. Nhet lai vao day chu khong sua ham do:
        # backtest.py dua vao hinh dang hien tai.
        got = setups.load_candidates(c, "LEAD", today=day)
        v.watch = sorted(({**r, "sym": s} for s, r in got.items()),
                         key=lambda r: -(r.get("quality") or 0))
    v.n_struct = ctx.get("structure", {}).get("n_struct")
    if v.n_struct is None:
        v.n_struct = len(structure.load_struct(c)) or None
    return v


# ───────────────────────── trinh bay ─────────────────────────
PANEL_W = 76

# Dau cau khong co trong ASCII, doi sang tuong duong thay vi bo dau.
_ASCII = {"·": "|", "→": "->", "×": "x", "…": "...", "—": "-", "–": "-",
          "“": '"', "”": '"', "’": "'", "đ": "d", "Đ": "D"}


def _ascii(txt: str) -> str:
    """Bo dau tieng Viet, giu nguyen chu. "xếp hạng ngành" -> "xep hang nganh".

    Ton tai de canh bao chi phai VIET MOT LAN. Nguoi doc chinh cua chung la tin
    nhan Telegram, noi da chac utf-8 va noi nguoi dung muon doc tieng Viet co
    dau; panel thi phai ASCII (mail cron, journalctl). Mot bien the ASCII viet
    tay cho moi canh bao la hai ban se lech nhau, va encode("ascii","replace")
    thi bien ca cau thanh "kh?ng ??c ???c" - te hon ca hai.

    NFD tach "ế" thanh "e" + dau, roi bo cac ky tu ket hop (category Mn). Chu
    "đ" khong tach duoc nen nam trong bang _ASCII o tren.
    """
    for a, b in _ASCII.items():
        txt = txt.replace(a, b)
    txt = "".join(ch for ch in unicodedata.normalize("NFD", txt)
                  if unicodedata.category(ch) != "Mn")
    return txt.encode("ascii", "replace").decode("ascii")


def _wrap(txt: str, width: int, indent: str) -> list[str]:
    """Ngat dong theo TU, khong cat giua tu.

    Quan trong hon no nghe: canh bao huu ich nhat trong panel nay la cau noi
    phai cai goi gi hoac chay lenh gi. Cat no o ky tu thu 60 thi mat dung phan
    do, va nguoi doc thay mot dong cut duoi ma khong biet co gi phia sau.
    """
    return textwrap.wrap(txt, width=width, subsequent_indent=indent,
                         initial_indent=indent, break_long_words=False,
                         break_on_hyphens=False) or [indent.rstrip()]


def panel(res: dict) -> str:
    """Bang cho stdout/log cron.

    CHI ASCII, co y: dong nay di vao mail cua cron va vao `journalctl`, hai cho
    ma khong the chac la utf-8. Chu tieng Viet co dau chi nam trong tin nhan
    Telegram, noi da biet chac la utf-8.
    """
    w = PANEL_W
    lead = "=" * w
    out = ["", lead,
           f" nightly {res['run_id']}  (ngay ET {res['day']})",
           f" nen quyet dinh: {res.get('bar') or '-'}"
           + ("   [--dry-run]" if res["dry"] else ""),
           lead,
           f" {'BUOC':<11}{'':<6}{'GIAY':>5}  CHI TIET", "-" * w]
    pad = " " * 25
    for s in res["stages"]:
        mark = "OK" if s["ok"] else ("LOI*" if s["fatal"] else "LOI")
        if s.get("skipped"):
            mark = "bo"
        elif s.get("blocked"):
            mark = "-"
        txt = _ascii((s.get("detail") or s.get("err") or "").replace("\n", " "))
        head = f" {s['stage']:<11}{mark:<6}{s['sec']:>5.1f}  "
        lines = _wrap(txt, w, pad)
        out.append(head + lines[0][len(pad):])
        out += lines[1:]
    out.append("-" * w)
    if res["warn"]:
        out.append(" CANH BAO:")
        for w_ in res["warn"]:
            ls = _wrap(_ascii(w_), w, " " * 5)
            out.append("   - " + ls[0][5:])
            out += ls[1:]
        out.append("-" * w)
    out.append(f" ma thoat {res['code']}  |  {res['sec']:.1f}s  |  "
               f"{'xong' if res['ok'] else 'CO BUOC DO'}")
    out.append(lead)
    return "\n".join(out)


# ───────────────────────── CLI ─────────────────────────
def main() -> int:
    global DB
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", dest="dry", action="store_true",
                    help="tinh het, in tin nhan, khong ghi/gui/day gi")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--only", default="",
                    help=f"chi chay cac buoc nay (phay): {','.join(NAMES)}")
    ap.add_argument("--status", action="store_true",
                    help="in lan chay gan nhat roi thoat, khong chay gi")
    ap.add_argument("--quiet", action="store_true", help="chi ghi file log")
    a = ap.parse_args()
    DB = Path(a.db)
    lg = _log_setup(a.quiet)

    if a.status:
        r = last_run(DB)
        if not r:
            print("bang `night` chua co dong nao - nightly.py chua chay lan nao.")
            return 3
        print(panel(r))
        ok = last_ok(DB)
        print(f" lan chay thanh cong gan nhat: "
              f"{ok['run_id'] if ok else 'CHUA CO LAN NAO'}")
        return 0

    only = {x.strip() for x in a.only.split(",") if x.strip()} or None
    if only and (xau := only - set(NAMES)):
        ap.error(f"buoc khong ton tai: {', '.join(sorted(xau))}. "
                 f"Chon trong: {', '.join(NAMES)}")

    res = run(DB, dry=a.dry, only=only, lg=lg)
    if not a.quiet:
        print(panel(res))
    return res["code"]


# ───────────────────────── selftest ─────────────────────────
def _smoke() -> None:
    import tempfile
    db = Path(tempfile.mkdtemp()) / "t.db"

    # 1. nen quyet dinh. Kiem qua _ascii() de mot lan goi kiem ca hai thu: cau
    #    canh bao co dung y khong, va no co con doc duoc sau khi bo dau khong.
    def ck(*a) -> str:
        return _ascii(" ".join(_check_bar(*a)))

    assert _check_bar("2026-09-24", "2026-09-25") == []
    assert "chua co dong nao" in ck(None)
    assert "TRUNG ngay chay" in ck("2026-09-25", "2026-09-25")
    assert "TUONG LAI" in ck("2026-09-26", "2026-09-25")
    assert _check_bar("2026-09-21", "2026-09-25") == [], "cuoi tuan dai: binh thuong"
    assert "khong duoc lam moi" in ck("2026-09-18", "2026-09-25")
    # Bo dau phai giu nguyen chu, khong duoc bien thanh "kh?ng ??c ???c".
    assert _ascii("xếp hạng ngành · đo lường") == "xep hang nganh | do luong"
    assert "?" not in _ascii(" ".join(tz_warn() or ["a"]))

    # 2. bang `night` + ma thoat
    res = {"run_id": "2026-09-25T12:00:00+00:00", "day": "2026-09-25",
           "bar": "2026-09-24", "ok": False, "code": 1, "sec": 12.5, "dry": False,
           "stages": [{"stage": "bars", "ok": True, "fatal": True, "sec": 1.0},
                      {"stage": "sectors", "ok": False, "fatal": True,
                       "sec": 0.2, "err": "khong do duoc 2/11 sector"}],
           "warn": ["holdings.csv cu"]}
    save_run(db, res)
    got = last_run(db)
    assert got and got["run_id"] == res["run_id"] and got["code"] == 1
    assert got["stages"][1]["err"] == "khong do duoc 2/11 sector"
    assert got["warn"] == ["holdings.csv cu"]
    assert last_ok(db) is None, "lan do khong duoc tinh la thanh cong"

    save_run(db, {**res, "run_id": "2026-09-26T12:00:00+00:00", "ok": True,
                  "code": 0, "stages": [{"stage": "bars", "ok": True,
                                         "fatal": True, "sec": 1.0}],
                  "warn": []})
    assert last_ok(db)["run_id"] == "2026-09-26T12:00:00+00:00"
    assert last_run(db)["run_id"] == "2026-09-26T12:00:00+00:00"

    # chay lai cung run_id -> ghi de, khong them dong
    save_run(db, {**res, "ok": True, "code": 0})
    c = con(db)
    assert c.execute("SELECT COUNT(*) FROM night").fetchone()[0] == 2
    c.close()

    # 3. --dry-run khong ghi gi
    r = run(db, dry=True, only={"regime"}, lg=_log_setup(quiet=True))
    assert r["dry"] and r["stages"], r
    c = con(db)
    assert c.execute("SELECT COUNT(*) FROM night").fetchone()[0] == 2, \
        "--dry-run ghi vao bang `night`"
    c.close()

    # 4. Buoc bat buoc do -> cac buoc sau ghi la `blocked`, va tin nhan VAN dung
    #    duoc. DB tam nay khong co nen nao, nen `sectors` chac chan do: do la
    #    dung tinh huong can kiem tra.
    #    Bo `telegram` de selftest khong in ca tin nhan ra stdout; tin nhan duoc
    #    dung thu cong ngay ben duoi.
    r = run(db, dry=True, lg=_log_setup(quiet=True),
            only=set(NAMES) - {"telegram"})
    bad = [s for s in r["stages"]
           if s["fatal"] and not s["ok"] and not s.get("blocked")]
    assert bad, "sectors phai do tren DB rong"
    bl = [s for s in r["stages"] if s.get("blocked")]
    assert bl, "buoc bat buoc do ma cac buoc sau van duoc thu"
    assert r["code"] == 1, r["code"]
    # Cac buoc bi chan khong duoc dem la loi RIENG trong tin nhan: mot nguyen
    # nhan thi ke mot loi, buoc bi chan chi la mot cau "keo theo".
    v = render_night.NightView(day="2026-09-25", stages=r["stages"])
    that_bai = [s for s in r["stages"] if not s["ok"] and not s.get("blocked")]
    assert len(render_night._failed(v)) == len(that_bai), r["stages"]
    assert len(render_night._blocked(v)) == len(bl)
    txt = render_night.render_night(v)
    assert "Kéo theo, không chạy" in txt, "buoc bi chan phai duoc noi ra"
    assert "CHUỖI CHẠY KHÔNG XONG" in txt
    assert "xếp hạng ngành" in txt, "tin nhan phai goi ten buoc do"
    assert "Chưa lọc được" in txt, "danh sach rong vi chua chay, phai noi ro"
    assert len(txt) <= render_night.render.SAFE_LEN

    # 5. panel() khong vo voi bat ky hinh dang nao
    assert "ma thoat" in panel(res)
    assert "ma thoat" in panel({**res, "stages": [], "warn": []})

    # 6. FATAL khop voi STAGES, va NAMES khong trung
    assert FATAL == {"bars", "sectors", "structure", "setups"}, FATAL
    assert len(NAMES) == len(set(NAMES)), NAMES
    assert set(render_night.STAGE_VI) == set(NAMES), \
        set(render_night.STAGE_VI) ^ set(NAMES)

    print("nightly: selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _smoke()
        raise SystemExit(0)
    raise SystemExit(main())
