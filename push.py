"""push.py - day snapshot cua scanner len Cloudflare D1 qua /api/scanner.

Doi lai voi moi file khac trong repo: file nay KHONG chay ben trong process
scanner. Cron goi no. Ly do la mot dieu da thay ro trong Phase 1-8: mot cuoc goi
HTTP treo trong vong lap asyncio thi lam DUNG ca vong quet, va o day thu bi treo
la mot dashboard - thu it quan trong nhat trong ca he thong. Nen:

    main.py ghi mot dong `beat` vao bang `kv`   (nhanh, khong ra mang)
    push.py doc DB roi day len cloud            (ra mang, process rieng, chet duoc)

Scanner khong bao gio phu thuoc vao file nay. Xoa push.py di thi bot van chay y
nguyen.

KHONG co spool. notifier.Spool giu tin Telegram qua mat mang vi mot alert bo lo
la mot alert mat han; snapshot thi nguoc lai - ban cu vo gia tri ngay khi co ban
moi, nen mat mang thi bo qua vong nay, phut sau day lai.

Bien moi truong (.env):
    SCANNER_PUSH_URL   https://the-professional.pages.dev/api/scanner
    SCANNER_TOKEN      dat bang `wrangler pages secret put SCANNER_TOKEN`
Thieu mot trong hai -> module tat, khong bao loi. Day la mac dinh: repo nay clone
ve may nao cung phai chay duoc ma khong can Cloudflare.

Thuan stdlib -> selftest chay duoc tren may dev khong co thu vien va khong co DB.

    python push.py                 # selftest, khong mang, khong DB
    python push.py --status        # day scanner:status        (cron moi phut)
    python push.py --all           # day ca snapshot nang      (cron moi toi)
    python push.py --dry           # dung payload roi in ra, khong gui
    python push.py --get config    # doc nguoc scanner:config ve
    python push.py --ping          # kiem tra token
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "state" / "baseline.db"
SPOOL = ROOT / "state" / "spool.json"
SEEN = ROOT / "state" / "push.json"      # digest da day, de khong day lai y nguyen

try:                                     # may dev khong co python-dotenv
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:                        # noqa: BLE001
    pass

URL = os.getenv("SCANNER_PUSH_URL", "").strip().rstrip("/")
TOKEN = os.getenv("SCANNER_TOKEN", "").strip()

TIMEOUT = 15
TRIES = 3
# User-Agent that ai cung phai co. urllib khong dat thi gui "Python-urllib/3.x",
# va bot protection cua Cloudflare (Bot Fight Mode, ban free cung co) tra 403 cho
# dung chuoi do - 403 bang HTML, TRUOC khi request cham tay function. Trieu chung
# rat kho doan: token dung, deploy dung, van 403.
UA = "scanner-push/1.0 (+https://github.com/tuanhpham/scanner)"
MAX_BYTES = 500_000     # phia function chan o 512_000; chan som de loi ro rang
TOP_N = 120             # so candidate moi setup day len - du de xem, khong phinh
KEEP_DAYS = 10          # so ngay alert giu tren cloud

log = print

# Nhung khoa doi moi lan dung payload nhung khong mang thong tin gi moi. Neu
# tinh digest ke ca chung thi khong lan nao "khong doi", va D1 an du 1440
# write/ngay chi cho mot con dong ho.
VOLATILE = ("ts", "age_sec")


# ───────────────────────── gui ─────────────────────────
def ready() -> bool:
    return bool(URL and TOKEN)


def _snip(raw: str, n: int = 220) -> str:
    """Body khong phai JSON -> mot dong ngan doc duoc.

    Ly do co ham nay: mot loi 403 tra ve trang HTML cua Cloudflare tung bi bien
    thanh `{}` roi in ra "HTTP 403 {}", tuc la XOA sach cau tra loi ngay trong
    thong bao loi. Function nay LUON tra JSON co khoa `error`, nen body khong
    phai JSON = thu chan nam TRUOC function (Cloudflare Access, WAF, bot
    protection), va noi dung body la thu duy nhat noi ra dieu do.
    """
    one = " ".join(raw.split())
    kind = "HTML" if one[:1] == "<" else "text"
    return f"khong phai JSON ({kind}): {one[:n]}" if one else "body rong"


def _req(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Mot cuoc goi. Tra (status, json). Khong nem ra ngoai: loi mang tra (0, ...).

    Token chi nam trong header, khong bao gio vao URL hay vao log - URL bi ghi
    ra state/prep.log, header thi khong.
    """
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{URL}/{path.lstrip('/')}", data=data, method=method)
    r.add_header("X-Scanner-Token", TOKEN)
    r.add_header("User-Agent", UA)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=TIMEOUT) as resp:
            raw = resp.read().decode(errors="replace") or "{}"
            try:
                return resp.status, json.loads(raw)
            except ValueError:
                return resp.status, {"error": _snip(raw)}
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode(errors="replace")
        except Exception:                                        # noqa: BLE001
            pass
        try:
            return e.code, json.loads(raw or "{}")
        except ValueError:
            return e.code, {"error": _snip(raw)}
    except Exception as e:                                       # noqa: BLE001
        return 0, {"error": f"{type(e).__name__}: {e}"}


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """_req + thu lai. Chi thu lai loi mang va 5xx.

    403/413/401 la loi cua minh, thu lai 3 lan khong sua duoc gi ngoai viec lam
    cham cron di 30 giay.
    """
    for i in range(TRIES):
        st, js = _req(method, path, body)
        if st and st < 500:
            return st, js
        if i < TRIES - 1:
            time.sleep(1.5 * (i + 1))
    return st, js


def _seen() -> dict:
    try:
        return json.loads(SEEN.read_text())
    except Exception:                                            # noqa: BLE001
        return {}


def _digest(value) -> str:
    """Digest cua payload sau khi bo cac khoa VOLATILE o tang tren cung."""
    if isinstance(value, dict):
        value = {k: v for k, v in value.items() if k not in VOLATILE}
    raw = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def put(key: str, value, force: bool = False, dry: bool = False) -> str:
    """Day mot khoa. Tra ve mot tu de log: sent/same/off/dry/err.

    "same" la co y va quan trong: D1 free co han so write moi ngay. Bang
    `candidates` doi mot lan moi toi, con cron --status chay moi phut - neu moi
    lan deu ghi thi 1400 write/ngay bi tieu vao viec ghi lai dung thu vua ghi.
    """
    if value is None:
        return "skip"
    raw = json.dumps(value, default=str)
    if len(raw) > MAX_BYTES:
        log(f"push: {key} qua to ({len(raw)} bytes > {MAX_BYTES}), bo qua")
        return "err"
    if dry:
        log(f"push[dry]: {key} = {len(raw)} bytes")
        log(raw[:1500] + ("..." if len(raw) > 1500 else ""))
        return "dry"
    if not ready():
        return "off"

    seen, dig = _seen(), _digest(value)
    if not force and seen.get(key) == dig:
        return "same"

    st, js = call("PUT", f"kv/{key}", {"value": value})
    if st != 200:
        log(f"push: {key} -> HTTP {st} {js.get('error', '')}")
        return "err"

    seen[key] = dig
    try:
        SEEN.parent.mkdir(parents=True, exist_ok=True)
        SEEN.write_text(json.dumps(seen, indent=1, sort_keys=True))
    except Exception as e:                                       # noqa: BLE001
        log(f"push: khong ghi duoc {SEEN.name}: {e}")             # khong chi tu
    return "sent"


def get_full(key: str) -> dict | None:
    """Doc nguoc mot khoa KEM dau moc thoi gian: {"value": ..., "updatedAt": ms}.

    `updatedAt` la dong ho CUA CLOUDFLARE, khong phai cua nguoi ghi. Voi
    scanner:positions dieu do quan trong: ben ghi la trinh duyet, va mot may tinh
    chay nhanh vai phut se cho ra mot dau moc o tuong lai.
    """
    if not ready():
        return None
    st, js = call("GET", f"kv/{key}")
    if st == 404:
        return None
    if st != 200:
        log(f"push: get {key} -> HTTP {st} {js.get('error', '')}")
        return None
    return js


def get(key: str) -> dict | None:
    """Doc nguoc mot khoa. Dung cho scanner:config / scanner:commands (buoc 5-6)."""
    js = get_full(key)
    return None if js is None else js.get("value")


# ───────────────────────── doc DB ─────────────────────────
def _con(db=DB) -> sqlite3.Connection:
    """Mo DB CHI DOC. Uri mode=ro de mot loi lap trinh o day khong the ghi vao
    baseline.db - trong do la 3 nam nen phai mat 30-60 phut moi tai lai."""
    c = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _one(c, sql: str, args=()) -> sqlite3.Row | None:
    """SELECT chiu duoc bang chua ton tai.

    Khong phai phong xa: tren VM moi, `struct` va `candidates` chua co cho den
    khi chay structure.py --build lan dau, va tren may dev thi khong bao gio co.
    Ca hai truong hop dashboard phai hien "chua co" chu khong phai sap.
    """
    try:
        return c.execute(sql, args).fetchone()
    except sqlite3.Error:
        return None


def _rows(c, sql: str, args=()) -> list[sqlite3.Row]:
    try:
        return c.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def _biz_hours(t0: dt.datetime, t1: dt.datetime) -> float:
    """So gio giua hai moc, KHONG dem thu Bay va Chu Nhat.

    Ly do khong dung (t1 - t0) tron: cron swing chay cac ngay lam viec, nen sang
    thu Hai lan chay thanh cong gan nhat la sang thu Sau - hon 48 gio dong ho -
    va mot nguong tinh theo gio dong ho se bao dong MOI thu Hai. Mot banner bao
    dong moi tuan thi sau ba tuan khong ai con doc no, va luc do no khong con
    bao duoc lan mat du lieu that.

    Bo cuoi tuan di thi con so 36 gio lai co nghia dung nhu no noi: bo mot ngay
    lam viec. Thu Sau 08:00 -> thu Hai 08:00 = 24 gio lam viec, khong tre.

    Tinh theo UTC. Ranh gioi ngay UTC lech ranh gioi ET vai gio, nhung cron chay
    08:00 ET = 12:00/13:00 UTC, xa hai dau ngay, nen khong doi ket qua.
    """
    if t1 <= t0:
        return 0.0
    tot = 0.0
    cur = t0
    while cur < t1:                     # mot vong moi ngay lich, khong moi gio
        nxt = dt.datetime.combine((cur + dt.timedelta(days=1)).date(),
                                  dt.time(0), tzinfo=cur.tzinfo)
        seg = min(nxt, t1)
        if cur.weekday() < 5:
            tot += (seg - cur).total_seconds() / 3600.0
        cur = seg
    return tot


def _iso(s: str | None) -> dt.datetime | None:
    """ISO -> datetime co mui gio (coi khong co mui la UTC). Rac -> None."""
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _age_days(day: str | None, today: str | None = None) -> int | None:
    if not day:
        return None
    try:
        t = dt.date.fromisoformat(today) if today else dt.date.today()
        return (t - dt.date.fromisoformat(day[:10])).days
    except ValueError:
        return None


NIGHT_COLS = ("run_id", "day", "bar", "ok", "code", "sec", "dry")


def _night(c, now: dt.datetime | None = None) -> dict | None:
    """Lan chay swing gan nhat + lan THANH CONG gan nhat + co "so lieu cu".

    Hai dong, khong mot: "chay luc 08:00 nhung buoc sectors do" va "khong chay
    lan nao tu thu Ba" dan den hai viec phai lam khac nhau, va mot dong duy nhat
    khong phan biet duoc chung. `stale` tinh theo lan THANH CONG - mot lan chay
    do khong lam so lieu moi hon.
    """
    if not _has(c, "night"):
        return None
    cols = ",".join(NIGHT_COLS)

    def row(where: str) -> dict | None:
        r = _one(c, f"SELECT {cols},stages,warn FROM night {where} "
                    f"ORDER BY run_id DESC LIMIT 1")
        if not r:
            return None
        d = {k: r[k] for k in NIGHT_COLS}
        for k in ("stages", "warn"):
            try:
                d[k] = json.loads(r[k] or "null")
            except ValueError:
                d[k] = None
        return d

    last, ok = row(""), row("WHERE ok=1")
    if not last:
        return None
    out: dict = {"last": last, "last_ok": ok}
    if last.get("stages"):
        # Buoc nao do, goi ten. Buoc BI CHAN khong tinh: mot nguyen nhan thi ke
        # mot loi, giong render_night._failed(). Xem nightly.py.
        out["failed"] = [s.get("stage") for s in last["stages"]
                         if not s.get("ok") and not s.get("blocked")]
        out["blocked"] = [s.get("stage") for s in last["stages"]
                          if s.get("blocked")]

    try:
        import config
        lim = float(config.NIGHTLY["stale_hours"])
    except Exception:                                            # noqa: BLE001
        lim = 36.0
    now = now or dt.datetime.now(dt.timezone.utc)
    t = _iso(ok and ok.get("run_id"))
    hrs = None if t is None else round(_biz_hours(t, now), 1)
    out["stale"] = {"hours": hrs, "limit": lim,
                    # hrs is None = chua chay thanh cong lan nao. Do cung la "cu"
                    # - dashboard phai canh bao, khong duoc hien banner trang.
                    "stale": hrs is None or hrs > lim}
    return out


def status_payload(db=DB, today: str | None = None,
                   now: dt.datetime | None = None) -> dict:
    """Mot trang trang thai. Chi COUNT va MAX -> nhe, chay duoc moi phut.

    `age` la con so dang xem nhat o day. setups.MAX_AGE = 5: bang `candidates`
    cu hon 5 ngay thi load_candidates() tra ve rong va bot IM LANG hoan toan -
    khong loi, khong tin nhan. Da co mot tuan khong alert thi cau hoi dau tien
    la con so nay, khong phai nguong BO.
    """
    out: dict = {"ts": int(time.time() * 1000), "today": today or dt.date.today().isoformat()}
    try:
        c = _con(db)
    except sqlite3.Error as e:
        out["db_error"] = str(e)
        return out
    try:
        if (r := _one(c, "SELECT COUNT(DISTINCT sym) s, COUNT(*) n, MAX(d) d FROM bars")):
            out["bars"] = {"syms": r["s"], "rows": r["n"], "last": r["d"],
                           "age": _age_days(r["d"], today)}
        if (r := _one(c, "SELECT COUNT(*) n, MAX(d) d, MAX(updated) u FROM struct")):
            out["struct"] = {"rows": r["n"], "last": r["d"], "updated": r["u"],
                             "age": _age_days(r["d"], today)}
        if (r := _one(c, "SELECT COUNT(*) n, MAX(d) d, MAX(updated) u FROM candidates")):
            out["candidates"] = {"rows": r["n"], "last": r["d"], "updated": r["u"],
                                 "age": _age_days(r["d"], today)}
            out["candidates"]["by_setup"] = {
                x["setup"]: x["n"] for x in
                _rows(c, "SELECT setup, COUNT(*) n FROM candidates GROUP BY setup")}
        day = out["today"]
        if (r := _one(c, "SELECT COUNT(*) n, MAX(ts_et) t FROM alerts WHERE ts_et LIKE ?",
                      (day + "%",))):
            out["alerts_today"] = {"n": r["n"], "last": r["t"]}
        if (r := _one(c, "SELECT COUNT(*) n FROM watch WHERE kind='track'")):
            out["tracking"] = r["n"]
        # `beat` do main.py ghi. Thieu = scanner chua chay lan nao, hoac dang
        # chay ban cu chua co doan ghi beat - phan biet duoc hai thu do quan trong.
        if (r := _one(c, "SELECT v FROM kv WHERE k='beat'")):
            try:
                out["beat"] = json.loads(r["v"])
            except Exception:                                    # noqa: BLE001
                pass
        # Chuoi chay swing. Nam trong `scanner:status` chu khong thanh khoa rieng
        # vi day la SUC KHOE, va dashboard da doc khoa nay de biet bot con song
        # hay khong - "cron toi qua co chay khong" la cung mot cau hoi. Chi phi
        # la mot SELECT theo khoa chinh, chay moi phut duoc.
        if (r := _night(c, now)):
            out["night"] = r
    finally:
        c.close()

    try:
        out["spool"] = len(json.loads(SPOOL.read_text()))
    except Exception:                                            # noqa: BLE001
        out["spool"] = 0
    return out


# Ke hoach lenh do plan.make() tinh tu nen quyet dinh dem truoc. Tach rieng khoi
# CAND_COLS vi hai nhom co tuoi khac nhau: mot DB tren VM chua chay setups.py
# ban moi thi bang `candidates` chua co sau cot nay.
PLAN_COLS = ("trigger", "stop", "target", "stop_pct", "risk_pct", "size_pct")

CAND_COLS = ("sym", "setup", "d", "ref_close", "pivot", "sma20", "adv20",
             "atr_pct", "base_len", "base_depth", "off_high", "rs_pct",
             "dist_pivot", "fund_ok", "sector", "rs21", "rs63",
             "quality") + PLAN_COLS


def _table_cols(c, table: str) -> set[str]:
    """Ten cot that su co trong bang. Dung de KHONG chon cot chua ton tai.

    Vi sao can: `_rows()` nuot sqlite3.Error va tra [], nen mot cau SELECT co cot
    thieu khong nem loi - no lam ca bang watchlist bien mat khoi dashboard, im
    lang, va giong het mot dem khong co ma nao dat. push.py chay nhu mot tien
    trinh rieng voi setups.py nen hai ben CO THE lech phien ban trong vai gio.
    """
    try:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _pick(c, table: str, want) -> list[str]:
    """Giao cua `want` va cot that co, giu nguyen thu tu cua `want`."""
    have = _table_cols(c, table)
    return [x for x in want if x in have]


def candidates_payload(db=DB, top: int = TOP_N) -> dict | None:
    """Danh sach theo doi, xep theo quality, TOP_N moi setup.

    Cat bot vi day len ca 500 dong x 2 setup thi khong ai doc het, va khoa nay
    duoc doc lai moi lan mo tab.
    """
    try:
        c = _con(db)
    except sqlite3.Error:
        return None
    try:
        sets = [r["setup"] for r in
                _rows(c, "SELECT DISTINCT setup FROM candidates ORDER BY setup")]
        if not sets:
            return None
        out: dict = {"ts": int(time.time() * 1000), "top": top, "by_setup": {}}
        cols = ",".join(_pick(c, "candidates", CAND_COLS))
        for s in sets:
            rows = _rows(c, f"SELECT {cols} FROM candidates WHERE setup=?"
                            " ORDER BY quality DESC LIMIT ?", (s, top))
            out["by_setup"][s] = [dict(r) for r in rows]
            r = _one(c, "SELECT COUNT(*) n FROM candidates WHERE setup=?", (s,))
            out["by_setup"][s + "_total"] = r["n"] if r else len(rows)
    finally:
        c.close()
    return out


# ───────────────────── Stage 4: bon khoa cho trang swing ─────────────────────
# Bon khoa nay la "API" ma go yeu cau (/api/regime, /api/sectors, /api/watchlist,
# /api/status). Chung khong la bon endpoint HTTP tren VM vi VM KHONG MO CONG NAO
# - do la mot quyet dinh an ninh, khong phai mot thieu sot. Duong di la:
#
#     VM (push.py)  --PUT-->  /api/scanner/kv/<key>  -->  D1
#     browser       --GET-->  /api/scanner/kv?since  <--  D1
#
# nen mot "endpoint" o day chinh la mot khoa. Doi lai duoc mot thu: dashboard
# doc duoc ca khi VM dang tat.
#
# CHU Y ve ten khoa: nguong cau hinh di vao `scanner:thresholds`, KHONG phai
# `scanner:config`. Function o functions/api/scanner/[[path]].ts giu
# `scanner:config` va `scanner:commands` cho phia APP ghi (do la duong nguoi
# dung goi lenh xuong VM); token cua VM ghi vao do se bi tra 403. Hai chieu ghi,
# hai khong gian ten.
def _has(c, table: str) -> bool:
    """Bang co ton tai va co du lieu khong.

    Phai kiem TRUOC khi goi cac ham load cua regime.py/sectors.py: chung mo dau
    bang `executescript(DDL)`, va tren ket noi mode=ro dieu do chi im lang khi
    bang DA co (SQLite bo qua CREATE TABLE IF NOT EXISTS). Bang chua co -> loi
    "attempt to write a readonly database", mot cau khong lien quan gi den
    nguyen nhan that la "chua chay buoc do lan nao". Giong rejects_payload().
    """
    return _one(c, f"SELECT 1 FROM {table} LIMIT 1") is not None


def regime_payload(db=DB) -> dict | None:
    """Boi canh thi truong + o playbook dang ap dung.

    Gui ca `prev` (phien truoc): cau duy nhat dang doc tren panel Today la
    "hom nay khac hom qua o cho nao", va tinh no o day re hon la de dashboard
    keo them mot khoa lich su.

    `playbook` lay tu config.PLAYBOOK chu khong tu cot `playbook` trong DB: cot
    do la anh chup luc ghi, con bang Config tren dashboard hien nguong DANG
    chay. Lech nhau thi phai thay duoc, khong duoc lam phang.
    """
    try:
        import config
        import regime
    except Exception as e:                                       # noqa: BLE001
        log(f"push: khong import duoc regime/config: {e}")
        return None
    try:
        c = _con(db)
    except sqlite3.Error:
        return None
    try:
        if not _has(c, "regime"):
            return None
        hist = regime.history(c, n=2)
    except sqlite3.Error as e:
        log(f"push: regime: {e}")
        return None
    finally:
        c.close()
    if not hist:
        return None

    row = hist[-1]
    pb = config.PLAYBOOK.get((row.get("trend"), row.get("vol"))) or {}
    return {"ts": int(time.time() * 1000), "row": row,
            "prev": hist[-2] if len(hist) > 1 else None,
            "playbook": {"setups": list(pb.get("setups") or []),
                         "size": pb.get("size"), "note": pb.get("note")},
            "age": _age_days(row.get("d"))}


def sectors_payload(db=DB, chart_days: int | None = None) -> dict | None:
    """Xep hang nganh phien moi nhat + thay doi hang + lich su cho bieu do.

    Lich su o dang CT (cot): {"days": [...], "series": {sym: [rank, ...]}} chu
    khong phai 990 dict {d, sym, rank, composite}. Cung mot thong tin, nho hon
    khoang mot bac do lon, va dung hinh dang ma bieu do can - khong co vong lap
    gom nhom nao o phia trinh duyet.

    `None` trong mot series = phien do khong co ma nay (VM tat, hoac ETF chua du
    nen). Bieu do phai NGAT duong o do chu khong noi thang qua: noi thang qua la
    ve ra mot lich su chua bao gio ton tai.
    """
    try:
        import config
        import sectors
    except Exception as e:                                       # noqa: BLE001
        log(f"push: khong import duoc sectors/config: {e}")
        return None
    n = int(config.NIGHTLY["chart_days"] if chart_days is None else chart_days)
    try:
        c = _con(db)
    except sqlite3.Error:
        return None
    try:
        if not _has(c, "sector_rank"):
            return None
        rows = sectors.load_rank(c)
        if not rows:
            return None
        d = rows[0].get("d")
        chg = sectors.changes(c, d)
        hist = sectors.history(c, n)
    except sqlite3.Error as e:
        log(f"push: sectors: {e}")
        return None
    finally:
        c.close()

    days = sorted({r["d"] for r in hist})
    at = {(r["d"], r["sym"]): r["rank"] for r in hist}
    syms = [r["sym"] for r in rows]                 # thu tu hang cua hom nay
    for s in sorted({r["sym"] for r in hist}):      # ma da roi khoi ro van phai ve
        if s not in syms:
            syms.append(s)
    return {
        "ts": int(time.time() * 1000), "d": d, "rows": rows,
        # Khoa JSON phai la chuoi; changes() tra khoa int -> str() o day, va
        # dashboard doc theo cung danh sach `wins` nay chu khong doan.
        "chg": {s: {str(w): v for w, v in ws.items()} for s, ws in chg.items()},
        "wins": [int(w) for w in config.SECTORS["change_wins"]],
        "defensive": sectors.defensive_top(rows),
        "hist": {"days": days,
                 "series": {s: [at.get((x, s)) for x in days] for s in syms}},
        "age": _age_days(d),
    }


def watchlist_payload(db=DB, top: int | None = None) -> dict | None:
    """Danh sach theo doi swing: chi setup LEAD, kem co vi the cua hom nay.

    Doc THANG tu bang chu khong qua setups.load_candidates(): ham do khoa theo
    `sym`, nen mot ma vua la BO vua la LEAD (rat hay xay ra - ca hai deu doi ma
    gan dinh) chi con mot dong.

    `size` ghep vao tung dong chu khong de dashboard tu nhan: cho vi the la mot
    quyet dinh cua playbook, va no phai di cung ma o dung cho nguoi doc nhin.

    HAI CON SO VE CO VI THE, KHONG PHAI MOT:
      `size`      he so cua o playbook dang ap dung (0.0 / 0.5 / 1.0 ...) - mot
                  gia tri cho ca phien, la "hom nay duoc danh bao nhieu phan".
      `size_pct`  co vi the CUOI CUNG cua tung ma, tinh tu rui ro 0.75% chia cho
                  khoang cach stop, DA nhan `size` roi (plan.make(size_mult=)).
    Nhan lai lan nua o dashboard hay o phan intraday la tu giam vi the xuong mot
    nua ma khong ai thay. Cot can doc de vao lenh la `size_pct`.
    """
    try:
        import config
    except Exception:                                            # noqa: BLE001
        return None
    n = int(config.NIGHTLY["watch_top"] if top is None else top)
    try:
        c = _con(db)
    except sqlite3.Error:
        return None
    try:
        cols = ",".join(x for x in _pick(c, "candidates", CAND_COLS)
                        if x != "setup")
        rows = _rows(c, f"SELECT {cols} FROM candidates WHERE setup='LEAD' "
                        f"ORDER BY quality DESC LIMIT ?", (n,))
        if not rows:
            return None
        size = None
        if _has(c, "regime"):
            r = _one(c, "SELECT trend, vol, size FROM regime ORDER BY d DESC LIMIT 1")
            if r:
                pb = config.PLAYBOOK.get((r["trend"], r["vol"])) or {}
                size = pb.get("size", r["size"])
        tot = _one(c, "SELECT COUNT(*) n FROM candidates WHERE setup='LEAD'")
    finally:
        c.close()
    out = [dict(r) for r in rows]
    for x in out:
        x["size"] = size
    return {"ts": int(time.time() * 1000), "d": out[0].get("d"),
            "rows": out, "total": tot["n"] if tot else len(out),
            "size": size, "age": _age_days(out[0].get("d"))}


def thresholds_payload() -> dict | None:
    """Nguong DANG chay, chi doc. Khong doc DB - config.py la nguon duy nhat.

    Ten khoa la `scanner:thresholds`, khong phai `scanner:config`: xem ghi chu o
    dau muc nay. Day chinh cac dict dang chay (config.snapshot()) chu khong phai
    mot ban mo ta viet tay, de bang tren dashboard sai thi la loi hien thi chu
    khong bao gio la "file mo ta da cu".
    """
    try:
        import config
        return {"ts": int(time.time() * 1000), "config": config.snapshot()}
    except Exception as e:                                       # noqa: BLE001
        log(f"push: khong doc duoc config: {e}")
        return None


def rejects_payload(db=DB) -> dict | None:
    """Bang LY DO BI LOAI - tinh lai tu bang `struct`, khong doc bang nao khac.

    Tinh lai thay vi luu lai luc setups.py --build vi mot ly do: bang nay chi co
    nghia khi no ung voi NGUONG HIEN TAI. Sua dict BO roi doc mot bang loai duoc
    sinh boi nguong cu thi ket luan sai ma khong co gi bao.

    Nang (doc ca bang struct, ~5000 dong) -> chi goi trong --all, moi toi.
    """
    try:
        import setups
        import structure
    except Exception as e:                                       # noqa: BLE001
        log(f"push: khong import duoc setups/structure: {e}")
        return None
    try:
        c = _con(db)
    except sqlite3.Error:
        return None
    try:
        # Tien kiem truoc khi goi load_struct(). Ly do: load_struct() mo dau bang
        # executescript(DDL), va tren ket noi mode=ro dieu do chi im lang khi bang
        # DA ton tai (SQLite bo qua CREATE TABLE IF NOT EXISTS). Bang chua co ->
        # "attempt to write a readonly database", mot thong bao khong lien quan gi
        # den nguyen nhan that la "chua chay structure.py --build lan nao".
        if not _one(c, "SELECT 1 FROM struct LIMIT 1"):
            return None
        st = structure.load_struct(c)
        if not st:
            return None
        _, rej = setups.scan(st, None, setups.MAX_CAND)
    except Exception as e:                                       # noqa: BLE001
        log(f"push: rejects: {type(e).__name__}: {e}")
        return None
    finally:
        c.close()
    return {"ts": int(time.time() * 1000), "struct": len(st),
            "cho_fund": rej.get("_cho_fund"),
            "by_setup": {k: dict(v) for k, v in rej.items()
                         if not k.startswith("_") and isinstance(v, dict)}}


def alerts_payload(db=DB, day: str | None = None) -> dict | None:
    """Alert cua mot ngay + ket qua do duoc.

    LEFT JOIN chu khong JOIN: alert vua gui thi chua co dong outcome nao, va do
    la dong minh muon xem nhat.
    """
    day = day or dt.date.today().isoformat()
    try:
        c = _con(db)
    except sqlite3.Error:
        return None
    try:
        rows = _rows(c, """
            SELECT a.ts_et, a.kind, a.sym, a.score, a.px, a.chg, a.rvol,
                   a.atr_move, a.dollar_vol, a.freshness, a.sources,
                   o.px15, o.px60, o.px_close, o.hi_after, o.lo_after
              FROM alerts a
              LEFT JOIN outcome o ON o.sym = a.sym AND o.day = ?
             WHERE a.ts_et LIKE ?
             ORDER BY a.ts_et""", (day, day + "%"))
    finally:
        c.close()
    if not rows:
        return None
    return {"ts": int(time.time() * 1000), "day": day,
            "rows": [dict(r) for r in rows]}


# ───────────────────────── don rac ─────────────────────────
def prune(keep: int = KEEP_DAYS, today: str | None = None) -> int:
    """Xoa khoa scanner:alerts:<ngay> cu hon `keep` ngay.

    Khong co cai nay thi moi ngay them mot dong vinh vien trong D1. Nho, nhung
    free tier khong co ai don ho.
    """
    if not ready():
        return 0
    st, js = call("GET", "kv?prefix=scanner:alerts:")
    if st != 200:
        return 0
    n = 0
    for row in js.get("keys", []):
        k = row.get("key", "") if isinstance(row, dict) else str(row)
        d = k.rsplit(":", 1)[-1]
        age = _age_days(d, today)
        if age is not None and age > keep:
            if call("DELETE", f"kv/{k}")[0] in (200, 204):
                n += 1
    return n


# ───────────────────────── CLI ─────────────────────────
def push_status(db=DB, dry: bool = False, force: bool = False) -> str:
    return put("scanner:status", status_payload(db), force=force, dry=dry)


def push_all(db=DB, dry: bool = False, force: bool = False) -> dict:
    """Snapshot nang, goi mot lan moi toi sau `setups.py --build`.

    Thu tu co y: `status` truoc, vi no la khoa noi ra buoc nao do. Neu mot khoa
    sau do qua to hay mang chet giua duong thi dashboard van biet lan chay nay
    ket thuc the nao.
    """
    day = dt.date.today().isoformat()
    out = {
        "status": push_status(db, dry, force),
        # Bon khoa cua trang swing (Stage 4).
        "regime": put("scanner:regime", regime_payload(db), force, dry),
        "sectors": put("scanner:sectors", sectors_payload(db), force, dry),
        "watchlist": put("scanner:watchlist", watchlist_payload(db), force, dry),
        "thresholds": put("scanner:thresholds", thresholds_payload(), force, dry),
        # Cac khoa cua phan trong phien (Phase 1-8), khong doi.
        "candidates": put("scanner:candidates", candidates_payload(db), force, dry),
        "rejects": put("scanner:rejects", rejects_payload(db), force, dry),
        f"alerts:{day}": put(f"scanner:alerts:{day}", alerts_payload(db, day),
                             force, dry),
    }
    if not dry:
        out["pruned"] = prune()
    return out


# Moi dong o day la mot lan da mat thoi gian doan. Function LUON tra JSON, nen
# neu body "khong phai JSON" thi khong phai loi cua function.
_PING_HINT = {
    0: "khong ket noi duoc: sai domain, hoac VM khong ra duoc internet.",
    401: "token lech giua .env va Cloudflare secret. Sinh chuoi moi, dat lai CA HAI ben.",
    403: "co thu chan TRUOC function. Xem body o tren:\n"
         "  - nhac 'Cloudflare Access' / cloudflareaccess.com -> Pages project dang bat\n"
         "    Access policy. Tat cho production, hoac cho /api/scanner* di duong bypass.\n"
         "  - trang 'you have been blocked' + Ray ID -> WAF / Bot Fight Mode.\n"
         "    Them WAF custom rule: skip cho path bat dau /api/scanner.",
    404: "deploy sai cho: `pages deploy dist` phai chay TU apps/desktop, khong phai\n"
         "     tu goc monorepo, khong thi functions/ khong duoc ship.",
    503: "thieu binding D1 trong wrangler.toml.",
}


def _cli() -> int:
    global URL, TOKEN

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--status", action="store_true", help="day scanner:status")
    ap.add_argument("--all", action="store_true", help="day ca snapshot nang")
    ap.add_argument("--dry", action="store_true", help="in payload, khong gui")
    ap.add_argument("--force", action="store_true", help="gui ca khi khong doi")
    ap.add_argument("--ping", action="store_true", help="kiem tra token")
    ap.add_argument("--get", metavar="KEY", help="doc nguoc, vd: config")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--url", default="", help="ghi de SCANNER_PUSH_URL")
    a = ap.parse_args()

    if a.url:
        URL = a.url.rstrip("/")

    # Khong co co nao -> selftest, va selftest khong duoc doi env. Ngay ca khi da
    # cau hinh: `python push.py` phai chay duoc tren VM dang chay that ma khong
    # gui gi len cloud.
    wants_net = a.ping or a.get or a.all or a.status
    if not wants_net:
        return _smoke()
    if not (a.dry or ready()):
        log("push: thieu SCANNER_PUSH_URL / SCANNER_TOKEN -> khong lam gi")
        return 0                        # 0, khong phai 1: chua cau hinh != loi

    if a.ping:
        # In ra thu doc duoc tu .env TRUOC khi goi mang. Khong in token, chi in
        # do dai: "token len 13" tra loi ngay cau hoi ".env co dung khong" ma
        # khong can biet token la gi, va do la cau hoi dau tien moi lan --ping sai.
        log(f"url  {URL or '(trong)'}")
        log(f"token len {len(TOKEN)}" + ("" if len(TOKEN) >= 32 else "  <- ngan bat thuong"))
        st, js = call("GET", "ping")
        log(f"HTTP {st}  {json.dumps(js, ensure_ascii=False)}")
        log(_PING_HINT.get(st, "") if st != 200 else "")
        return 0 if st == 200 else 1
    if a.get:
        k = a.get if a.get.startswith("scanner:") else f"scanner:{a.get}"
        log(json.dumps(get(k), indent=1, ensure_ascii=False))
        return 0
    if a.all:
        r = push_all(a.db, a.dry, a.force)
        log("push: " + "  ".join(f"{k}={v}" for k, v in r.items()))
        return 1 if "err" in r.values() else 0
    if a.status:
        r = push_status(a.db, a.dry, a.force)
        log(f"push: status={r}")
        return 1 if r == "err" else 0

    return 0


# ───────────────────────── selftest ─────────────────────────
def _mkdb(p: Path) -> None:
    """DB gia du de kiem payload: dung DDL that cua cac module.

    DDL that, khong phai ban go tay: mot cot bi doi ten trong regime.py hay
    sectors.py thi test o day phai do, chu khong phai chay xanh roi dashboard
    trong rong tren VM.
    """
    import bars
    import nightly
    import regime
    import sectors
    import setups
    import structure
    c = sqlite3.connect(p)
    c.executescript(bars.DDL)
    c.executescript(structure.DDL)
    c.executescript(setups.DDL)
    c.executescript(regime.DDL)
    c.executescript(sectors.DDL)
    c.executescript(nightly.DDL)
    c.executescript("""
      CREATE TABLE IF NOT EXISTS alerts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT, ts_et TEXT, kind TEXT, sym TEXT, score REAL,
        px REAL, chg REAL, rvol REAL, atr_move REAL, float_rot REAL,
        dollar_vol REAL, freshness TEXT, sources TEXT);
      CREATE TABLE IF NOT EXISTS outcome(
        sym TEXT, day TEXT, alert_ts INTEGER, score REAL, level INTEGER,
        rvol REAL, px0 REAL, px15 REAL, px60 REAL, px_close REAL,
        hi_after REAL, lo_after REAL, src TEXT, updated TEXT,
        PRIMARY KEY(sym, alert_ts));
      CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
      CREATE TABLE IF NOT EXISTS watch(
        sym TEXT, kind TEXT, ts TEXT, PRIMARY KEY(sym, kind));
    """)
    with c:
        c.execute("INSERT INTO bars(sym,d,o,h,l,c,ac,v) VALUES('AAA','2026-09-12',"
                  "1,1,1,1,1,100)")
        c.execute("INSERT INTO candidates(sym,setup,d,ref_close,pivot,sma20,adv20,"
                  "atr_pct,base_len,base_depth,off_high,rs_pct,dist_pivot,fund_ok,"
                  "quality,updated) VALUES('AAA','BO','2026-09-12',20,21,19.8,"
                  "800000,0.02,40,0.09,0.05,80,0.048,NULL,7.5,'x')")
        c.execute("INSERT INTO alerts(ts_utc,ts_et,kind,sym,score,px,chg,rvol,"
                  "atr_move,float_rot,dollar_vol,freshness,sources) VALUES("
                  "'2026-09-12T14:00:00','2026-09-12T10:00:00','NEW','AAA',9.1,"
                  "20,0.06,3.2,1.1,0.2,5e6,'fresh','yahoo')")
        c.execute("INSERT INTO kv(k,v) VALUES('beat','{\"scans\": 12}')")
        # LEAD: mot dong thu hai cho CUNG mot ma, de bat loi dung khoa `sym`.
        c.execute("INSERT INTO candidates(sym,setup,d,ref_close,pivot,sma20,"
                  "adv20,atr_pct,base_len,base_depth,off_high,rs_pct,dist_pivot,"
                  "fund_ok,sector,rs21,rs63,quality,updated) VALUES('AAA','LEAD',"
                  "'2026-09-12',20,21,19.8,800000,0.03,40,0.09,0.04,90,0.048,"
                  "NULL,'XLK',0.05,0.12,8.5,'x')")
        for i, (d, tr) in enumerate((("2026-09-11", "RANGE"),
                                     ("2026-09-12", "UPTREND"))):
            c.execute("INSERT INTO regime(d,trend,vol,slope_dir,px,sma50,sma200,"
                      "slope50,atr14,atr_pct,atr_pct_avg,atr_ratio,playbook,"
                      "size,bench,n_bars,updated) VALUES(?,?,'NORMAL','rising',"
                      "660,640,600,0.018,6.6,0.01,0.0104,0.96,'BO,LEAD',1.0,"
                      "'SPY',400,'x')", (d, tr))
        for d in ("2026-09-11", "2026-09-12"):
            for i, s in enumerate(("XLK", "XLF", "XLP"), 1):
                c.execute("INSERT INTO sector_rank(d,sym,rank,composite,ret21,"
                          "ret63,ret126,pct21,pct63,pct126,px,sma50,ema21,"
                          "slope50,above_sma50,above_ema21,slope_up,n_bars,"
                          "updated) VALUES(?,?,?,?,0.05,0.1,0.2,90,88,86,100,"
                          "95,98,0.01,1,1,1,300,'x')",
                          (d, s, i if d == "2026-09-12" else 4 - i,
                           100.0 - i * 10))
        c.execute("INSERT INTO night(run_id,day,bar,ok,code,sec,dry,stages,warn,"
                  "updated) VALUES('2026-09-12T12:00:00+00:00','2026-09-12',"
                  "'2026-09-11',1,0,12.3,0,"
                  "'[{\"stage\":\"bars\",\"ok\":true}]','[]','x')")
    c.close()


def _smoke() -> int:
    import tempfile
    n = 0

    def ok(cond, what):
        nonlocal n
        n += 1
        if not cond:
            raise AssertionError(what)

    # digest bo qua khoa volatile -> hai lan quet giong nhau khong day lai
    a = {"ts": 1, "bars": {"syms": 10}}
    b = {"ts": 999999, "bars": {"syms": 10}}
    ok(_digest(a) == _digest(b), "ts phai bi bo khoi digest")
    ok(_digest(a) != _digest({"ts": 1, "bars": {"syms": 11}}), "so lieu doi -> digest doi")
    ok(_digest([1, 2]) == _digest([1, 2]), "digest cua list")

    # tuoi bang
    ok(_age_days("2026-09-10", "2026-09-14") == 4, "_age_days")
    ok(_age_days("2026-09-10T00:00:00", "2026-09-14") == 4, "_age_days cat gio")
    ok(_age_days(None) is None and _age_days("rac") is None, "_age_days chiu rac")

    # payload voi DB that
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.db"
        _mkdb(p)
        s = status_payload(p, today="2026-09-14")
        ok(s["bars"]["syms"] == 1 and s["bars"]["age"] == 2, f"status bars: {s.get('bars')}")
        ok(s["struct"]["rows"] == 0, "struct rong van phai co mat")
        ok(s["candidates"]["by_setup"] == {"BO": 1, "LEAD": 1}, s.get("candidates"))
        ok(s["beat"] == {"scans": 12}, "beat phai duoc parse")
        ok(s["alerts_today"]["n"] == 0, "alert cua ngay khac khong tinh vao hom nay")
        s2 = status_payload(p, today="2026-09-12")
        ok(s2["alerts_today"]["n"] == 1, "alert dung ngay phai dem")

        cp = candidates_payload(p)
        ok(cp["by_setup"]["BO"][0]["sym"] == "AAA", "candidates payload")
        ok(cp["by_setup"]["BO_total"] == 1, "tong truoc khi cat")
        ok(alerts_payload(p, "2026-09-12")["rows"][0]["px15"] is None,
           "alert chua co outcome van phai ra")
        ok(alerts_payload(p, "2026-01-01") is None, "ngay khong co alert -> None")

        # Stage 4: bon khoa cua trang swing
        rp = regime_payload(p)
        ok(rp["row"]["trend"] == "UPTREND", f"regime row: {rp.get('row')}")
        ok(rp["prev"]["trend"] == "RANGE", "phai gui ca phien truoc de so")
        ok(rp["playbook"]["setups"] and rp["playbook"]["note"],
           "playbook lay tu config, khong tu cot trong DB")

        sp = sectors_payload(p)
        ok([r["sym"] for r in sp["rows"]] == ["XLK", "XLF", "XLP"], sp["rows"])
        ok(sp["defensive"] == ["XLP"], f"defensive: {sp['defensive']}")
        ok(sp["hist"]["days"] == ["2026-09-11", "2026-09-12"], sp["hist"]["days"])
        ok(sp["hist"]["series"]["XLK"] == [3, 1], sp["hist"]["series"])
        ok(all(isinstance(k, str) for k in next(iter(sp["chg"].values()))),
           "khoa JSON phai la chuoi")

        wl = watchlist_payload(p)
        ok([r["sym"] for r in wl["rows"]] == ["AAA"], "chi lay setup LEAD")
        ok("setup" not in wl["rows"][0], "cot setup la du thua trong khoa nay")
        ok(wl["rows"][0]["sector"] == "XLK", "cot sector cua Stage 3 phai co")
        ok(wl["rows"][0]["size"] == 1.0, f"co vi the: {wl['rows'][0].get('size')}")

        th = thresholds_payload()
        ok(th["config"]["bench"] and th["config"]["playbook"], "thresholds")
        ok(len(th["config"]["playbook"]) == 12, "12 o playbook")

        # chuoi chay + co "so lieu cu". Chay THANH CONG luc thu Hai 12:00 UTC:
        # phai la ngay lam viec, khong thi _biz_hours() dem ra 0 va con so 36
        # gio khong con y nghia gi.
        t0 = dt.datetime(2026, 9, 14, 12, tzinfo=dt.timezone.utc)   # thu Hai
        cx = sqlite3.connect(p)
        with cx:
            cx.execute("INSERT INTO night(run_id,day,bar,ok,code,sec,dry,stages,"
                       "warn,updated) VALUES(?,'2026-09-14','2026-09-11',1,0,9.0,"
                       "0,'[{\"stage\":\"bars\",\"ok\":true}]','[]','x')",
                       (t0.isoformat(),))
        cx.close()
        n1 = status_payload(p, now=t0 + dt.timedelta(hours=10))["night"]
        ok(n1["last"]["run_id"] == t0.isoformat(), n1["last"])
        ok(n1["stale"]["stale"] is False, f"10 gio chua tre: {n1['stale']}")
        n2 = status_payload(p, now=t0 + dt.timedelta(hours=40))["night"]
        ok(n2["stale"]["stale"] is True, f"40 gio phai tre: {n2['stale']}")

        # Lan chay DO khong lam so lieu moi hon: `stale` phai tinh theo lan
        # thanh cong, con `last` van la lan do de dashboard goi ten buoc.
        cx = sqlite3.connect(p)
        with cx:
            cx.execute("INSERT INTO night(run_id,day,bar,ok,code,sec,dry,stages,"
                       "warn,updated) VALUES(?,'2026-09-16','2026-09-15',0,1,3.0,"
                       "0,'[{\"stage\":\"sectors\",\"ok\":false,\"err\":\"x\"},"
                       "{\"stage\":\"setups\",\"ok\":false,\"blocked\":true}]',"
                       "'[]','x')", ((t0 + dt.timedelta(days=2)).isoformat(),))
        cx.close()
        n3 = status_payload(p, now=t0 + dt.timedelta(days=2, hours=1))["night"]
        ok(n3["last"]["ok"] == 0 and n3["last_ok"]["run_id"] == t0.isoformat(),
           "phai giu ca lan cuoi va lan thanh cong cuoi")
        ok(n3["failed"] == ["sectors"], f"failed: {n3.get('failed')}")
        ok(n3["blocked"] == ["setups"], "buoc bi chan khong tinh la loi rieng")
        ok(n3["stale"]["stale"] is True, f"lan do khong lam moi: {n3['stale']}")

        # gio lam viec: bo cuoi tuan di
        fri = dt.datetime(2026, 9, 11, 12, tzinfo=dt.timezone.utc)
        ok(_biz_hours(fri, fri + dt.timedelta(days=3)) == 24.0,
           "thu Sau 12:00 -> thu Hai 12:00 = 24 gio lam viec")
        ok(_biz_hours(fri, fri + dt.timedelta(hours=2)) == 2.0, "trong ngay")
        ok(_biz_hours(fri + dt.timedelta(days=3), fri) == 0.0, "lui thoi gian")
        sat = dt.datetime(2026, 9, 12, 0, tzinfo=dt.timezone.utc)
        ok(_biz_hours(sat, sat + dt.timedelta(days=2)) == 0.0, "ca cuoi tuan")
        ok(_iso("rac") is None and _iso(None) is None, "_iso chiu rac")
        ok(_iso("2026-09-14T12:00:00").tzinfo is not None, "_iso mac dinh UTC")

        # DB thieu bang: khong duoc sap
        q = Path(d) / "tron.db"
        sqlite3.connect(q).close()
        ok("bars" not in status_payload(q), "DB rong -> khong co khoa bars")
        ok(candidates_payload(q) is None, "khong co bang candidates -> None")
        for f in (regime_payload, sectors_payload, watchlist_payload):
            ok(f(q) is None, f"{f.__name__}: bang chua co -> None, khong sap")
        ok("night" not in status_payload(q), "chua co bang night -> khong co khoa")

        # DB khong ton tai
        ok("db_error" in status_payload(Path(d) / "khong-co.db"), "DB thieu -> db_error")

        # mo che do chi doc: khong the ghi
        ro = _con(p)
        try:
            ro.execute("DELETE FROM bars")
            ok(False, "mode=ro phai chan ghi")
        except sqlite3.OperationalError:
            n += 1
        finally:
            ro.close()      # Windows khong xoa duoc file dang mo -> tempdir kep

    # put khong mang: khong nem loi
    ok(put("scanner:status", {"a": 1}, dry=True) == "dry", "dry")
    ok(put("scanner:status", None) == "skip", "None -> skip")
    old_u, old_t = URL, TOKEN
    try:
        globals()["URL"] = globals()["TOKEN"] = ""
        ok(not ready(), "thieu env -> ready() False")
        ok(put("scanner:status", {"a": 1}) == "off", "thieu env -> off, khong nem")
    finally:
        globals()["URL"], globals()["TOKEN"] = old_u, old_t

    # payload qua to bi chan tai cho, khong doi HTTP 413
    ok(put("scanner:big", {"x": "y" * (MAX_BYTES + 10)}) == "err", "chan payload to")

    log(f"push.py: {n} kiem tra OK")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
