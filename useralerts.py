"""useralerts.py - canh bao CUA NGUOI DUNG, theo watchlist tren web.

Nguoi dung chon tren tab Watchlist cua web: danh sach nao canh bao, va canh gi
(muc gia tung ma, khoi luong bat thuong, bien dong manh trong ngay). Web day mot
ban phang theo tung ma len khoa `scanner:alerts` (src/portfolio/alertRules.ts):

    {"v":1, "on":true, "n":2, "syms": {
        "NVDA":   {"lists":["Semis"], "above":200.0, "rvol":2.0},
        "SAP.DE": {"lists":["Germany"], "below":180.0, "move":4.0}}}

watchd.py chay `Runner.step()` moi phut, song song voi vong Tier 1.

KHAC TIER 1 O DAU, VA VI SAO
----------------------------
  · Ma do NGUOI DUNG chon, khong qua san chat luong cua nightly. Day khong phai
    "them ma vao danh sach" (dieu cam trong watchd.py): danh sach Tier 1 khong
    doi, va cac canh bao nay di trong bang rieng `user_alert`, khong cham
    cooldown, tran Tier 2 hay tong ket phien cua Tier 1.
  · Ba thi truong, ba gio mo cua: My (New York), Duc/EU (Frankfurt), Viet Nam
    (Ha Noi). Chi lay gia cho ma cua thi truong DANG MO.
  · Moi quy tac bao toi da MOT lan moi ma moi ngay. Khoa la (ngay dia phuong,
    ma, quy tac kem muc) - doi muc giua phien thi muc moi duoc bao.

GIU NGUYEN CAC NGUYEN TAC CUA watchd:
  · Ghi "da bao" SAU khi gui duoc.
  · Bao gia cu (le, nghi, nguon chet) thi KHONG bao - `quotes.fresh`.
  · Khong biet khoi luong (vol_ok False, chua co trung binh) la KHONG BIET,
    khong phai "khoi luong thap".
  · Im lang phai giai thich duoc: trang thai di nguoc len web qua
    `scanner:alerts_seen` (VM duoc ghi khoa nay; `scanner:alerts` thi chi web).

Thuan stdlib o tang danh gia; yfinance chi cham trong `refs_from_yf`, import ben
trong ham.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from zoneinfo import ZoneInfo

import quotes
import vprofile

KEY = "scanner:alerts"
SEEN_KEY = "scanner:alerts_seen"

# Doc lai quy tac moi 5 phut - giong REFRESH_SEC cua watchd. Web noi "VM nhan
# trong vong 5 phut", con so do o day.
REFRESH_SEC = 300
# Day trang thai len web khi co gi doi, va it nhat 30 phut mot lan de web biet
# VM con song. 48 write/ngay la khong dang ke voi quota D1.
SEEN_EVERY_SEC = 1800
# Lay gia dong cua hom qua + trung binh 20 phien that bai thi 30 phut sau thu lai.
REF_RETRY_SEC = 1800
# Khong bao khoi luong trong 15 phut dau phien: phien khop lenh mo cua mot minh
# da co the trong nhu x3.
VOL_MUTE_MIN = 15
ADV_DAYS = 20
MAX_SYMS = 120

# (mui gio, mo cua, dong cua). Viet Nam nghi trua 11:30-13:00: khi do nen phut
# dung lai, bao gia cu dan va `fresh` tu chan - khong can mot ngoai le rieng.
MARKETS = {
    "US": ("America/New_York", (9, 30), (16, 0)),
    "EU": ("Europe/Berlin", (9, 0), (17, 30)),
    "VN": ("Asia/Ho_Chi_Minh", (9, 0), (15, 0)),
}
# Giong quoteCurrency.ts cua web. Hai ban phai trung nhau.
EU_SUFFIXES = {"DE", "F", "BE", "DU", "MU", "SG", "HA",
               "PA", "AS", "BR", "LS", "MI", "MC", "VI", "HE", "IR"}
VN_SUFFIXES = {"VN", "HN", "HNX", "UP", "UPCOM"}
ONE_LETTER_VENUES = {"L", "T", "V"}

DDL = """
CREATE TABLE IF NOT EXISTS user_alert (
  d TEXT NOT NULL, sym TEXT NOT NULL, rule TEXT NOT NULL,
  ts_utc TEXT, kind TEXT, px REAL, detail TEXT,
  PRIMARY KEY (d, sym, rule));
CREATE INDEX IF NOT EXISTS ix_user_alert_d ON user_alert(d);
"""


# ───────────────────────── thi truong ─────────────────────────
def market_of(sym: str) -> str | None:
    s = str(sym or "").strip().upper()
    if "." not in s:
        return "US" if s else None
    suf = s.rsplit(".", 1)[1]
    if len(suf) == 1 and suf not in EU_SUFFIXES and suf not in ONE_LETTER_VENUES:
        return "US"                       # BRK.B: hang co phieu, khong phai san
    if suf in EU_SUFFIXES:
        return "EU"
    if suf in VN_SUFFIXES:
        return "VN"
    return None


def session(mkt: str, now: dt.datetime) -> dict:
    """{"open","d","mso","minutes"} theo gio dia phuong. Khong biet ngay le:
    ngay le thi nen phut khong co, bao gia cu, va `fresh` chan lai."""
    tz, (h0, m0), (h1, m1) = MARKETS[mkt]
    loc = now.astimezone(ZoneInfo(tz))
    t0 = loc.replace(hour=h0, minute=m0, second=0, microsecond=0)
    t1 = loc.replace(hour=h1, minute=m1, second=0, microsecond=0)
    minutes = int((t1 - t0).total_seconds() // 60)
    mso = (loc - t0).total_seconds() / 60
    return {"open": loc.weekday() < 5 and t0 <= loc < t1, "d": loc.date().isoformat(),
            "mso": mso, "minutes": minutes}


# ───────────────────────── quy tac tu web ─────────────────────────
def _pos(x) -> float | None:
    v = quotes._num(x)
    return v if v is not None and v > 0 else None


def parse(value, updated_ms=None) -> dict:
    """Gia tri `scanner:alerts` -> {"known","on","syms","rules_at","note"}.

    Khong co khoa = KHONG BIET (web chua bat bao gio), khac voi "tat". Ca hai
    deu khong canh gi, nhung cau noi len web khac nhau.
    """
    out = {"known": False, "on": False, "syms": {}, "rules_at": updated_ms,
           "note": "chưa có quy tắc nào từ web"}
    if not isinstance(value, dict):
        return out
    out["known"] = True
    out["on"] = value.get("on") is not False
    syms = value.get("syms") if isinstance(value.get("syms"), dict) else {}
    for raw, r in list(syms.items())[:MAX_SYMS]:
        sym = str(raw or "").strip().upper()
        if not sym or not isinstance(r, dict) or not market_of(sym):
            continue
        row = {"lists": [str(x) for x in (r.get("lists") or []) if x][:6]}
        for k in ("above", "below", "rvol", "move"):
            v = _pos(r.get(k))
            if v is not None:
                row[k] = v
        if len(row) > 1:
            out["syms"][sym] = row
    out["note"] = "" if out["on"] else "người dùng đã tắt cảnh báo"
    return out


# ───────────────────────── gia tham chieu ─────────────────────────
def refs_from_yf(syms: list[str], day_of: dict[str, str]) -> dict:
    """{sym: {"prev": dong cua phien truoc, "adv": TB khoi luong 20 phien}}.

    Bo cac nen co ngay >= ngay dia phuong hom nay: yfinance tra ve nen DANG
    CHAY cua hom nay trong bang ngay, va tinh no vao trung binh hay vao "hom qua"
    la sai ca hai.
    """
    if not syms:
        return {}
    try:
        import yfinance as yf
        df = yf.download(syms, period="3mo", interval="1d", progress=False,
                         auto_adjust=False, threads=False, group_by="ticker")
    except Exception:                                            # noqa: BLE001
        return {}
    if df is None or getattr(df, "empty", True):
        return {}
    out: dict[str, dict] = {}
    nhieu = hasattr(df.columns, "levels") and len(df.columns.levels) > 1
    for s in syms:
        try:
            sub = df[s] if nhieu else df
        except KeyError:
            continue
        rows = []
        for ts, r in sub.iterrows():
            d = (ts.date() if hasattr(ts, "date") else ts).isoformat()
            rows.append({"d": d, "c": r.get("Close"), "v": r.get("Volume")})
        ref = ref_from_rows(rows, day_of.get(s, ""))
        if ref:
            out[s] = ref
    return out


def ref_from_rows(rows: list[dict], today: str) -> dict | None:
    """Phan thuan cua refs_from_yf: tu cac nen ngay {d, c, v}."""
    past = [r for r in rows if r.get("d") and r["d"] < today
            and quotes._num(r.get("c")) is not None]
    past.sort(key=lambda r: r["d"])
    if not past:
        return None
    vols = [quotes._num(r.get("v")) for r in past[-ADV_DAYS:]]
    vols = [v for v in vols if v is not None and v > 0]
    adv = sum(vols) / len(vols) if len(vols) >= 5 else None
    return {"prev": quotes._num(past[-1]["c"]), "adv": adv, "d": past[-1]["d"]}


# ───────────────────────── danh gia ─────────────────────────
def evaluate(rules: dict, qs: dict, refs: dict, sess: dict,
             max_age: float) -> tuple[list[dict], list[tuple[str, str]]]:
    """(ung vien, bo qua). `sess` la {thi truong: session()} cua vong nay."""
    cands, skip = [], []
    for sym, r in sorted(rules.items()):
        mk = market_of(sym)
        ss = sess.get(mk) or {}
        if not ss.get("open"):
            continue
        q = qs.get(sym)
        if not quotes.fresh(q, max_age):
            skip.append((sym, (q or {}).get("err") or "báo giá cũ hoặc thiếu"))
            continue
        px = q["px"]
        ref = refs.get(sym) or {}
        prev = ref.get("prev")
        chg = (px / prev - 1) * 100 if prev else None
        base = {"sym": sym, "px": px, "chg": chg, "lists": r.get("lists") or [],
                "d": ss["d"], "age": q.get("age_sec")}
        if r.get("above") is not None and px >= r["above"]:
            cands.append(dict(base, kind="above", level=r["above"],
                              rule=f"above:{r['above']:g}"))
        if r.get("below") is not None and px <= r["below"]:
            cands.append(dict(base, kind="below", level=r["below"],
                              rule=f"below:{r['below']:g}"))
        if r.get("rvol") is not None:
            adv = ref.get("adv")
            if not q.get("vol_ok") or not adv:
                skip.append((sym, "chưa biết khối lượng trung bình"))
            elif ss["mso"] >= VOL_MUTE_MIN:
                frac = vprofile.cum_frac(ss["mso"], ss["minutes"])
                rv = vprofile.rvol_at(q.get("vol") or 0, adv, frac)
                if rv >= r["rvol"]:
                    cands.append(dict(base, kind="vol", level=r["rvol"], rv=rv,
                                      rule=f"vol:{r['rvol']:g}"))
        if r.get("move") is not None:
            if chg is None:
                skip.append((sym, "chưa có giá đóng cửa hôm qua"))
            elif abs(chg) >= r["move"]:
                cands.append(dict(base, kind="move", level=r["move"],
                                  rule=f"move:{r['move']:g}"))
    return cands, skip


# ───────────────────────── DB ─────────────────────────
def ensure(c: sqlite3.Connection) -> None:
    c.executescript(DDL)


def fired(c: sqlite3.Connection, days) -> set[tuple[str, str, str]]:
    days = sorted(set(days))
    if not days:
        return set()
    q = ",".join("?" * len(days))
    return {(r[0], r[1], r[2]) for r in c.execute(
        f"SELECT d, sym, rule FROM user_alert WHERE d IN ({q})", days)}


def record(c: sqlite3.Connection, a: dict, now: dt.datetime) -> bool:
    """Ghi SAU khi gui. `with c:` vi ly do trong watch.record()."""
    try:
        with c:
            c.execute("INSERT INTO user_alert(d,sym,rule,ts_utc,kind,px,detail) "
                      "VALUES(?,?,?,?,?,?,?)",
                      (a["d"], a["sym"], a["rule"],
                       now.isoformat(timespec="seconds"), a["kind"],
                       quotes._num(a.get("px")), ",".join(a.get("lists") or [])))
        return True
    except sqlite3.IntegrityError:
        return False


def recent(c: sqlite3.Connection, since_d: str, n: int = 30) -> list[dict]:
    rows = [dict(zip(("d", "sym", "kind", "px", "ts"), r)) for r in c.execute(
        "SELECT d, sym, kind, px, ts_utc FROM user_alert WHERE d>=? "
        "ORDER BY ts_utc DESC LIMIT ?", (since_d, n))]
    return list(reversed(rows))


# ───────────────────────── tin nhan ─────────────────────────
def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_px(x) -> str:
    v = quotes._num(x)
    if v is None:
        return "?"
    if v >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def _chg(x) -> str:
    return "" if x is None else f" ({x:+.1f}%)".replace("-", "−")


def render(cands: list[dict]) -> str:
    lines = []
    for a in cands:
        sym = f"<b>{_esc(a['sym'])}</b>"
        if a["kind"] == "above":
            t = f"📈 {sym} vượt <b>{fmt_px(a['level'])}</b> — giá {fmt_px(a['px'])}{_chg(a['chg'])}"
        elif a["kind"] == "below":
            t = f"📉 {sym} thủng <b>{fmt_px(a['level'])}</b> — giá {fmt_px(a['px'])}{_chg(a['chg'])}"
        elif a["kind"] == "vol":
            t = (f"📊 {sym} khối lượng <b>×{a['rv']:.1f}</b> nhịp thường "
                 f"(ngưỡng ×{a['level']:g}) — giá {fmt_px(a['px'])}{_chg(a['chg'])}")
        else:
            up = (a["chg"] or 0) >= 0
            pct = f"{a['chg']:+.1f}%".replace("-", "−")
            t = (f"{'🚀' if up else '🔻'} {sym} <b>{pct}</b> so với hôm qua"
                 f" (ngưỡng ±{a['level']:g}%) — giá {fmt_px(a['px'])}")
        if a.get("lists"):
            t += f" · <i>{_esc(', '.join(a['lists']))}</i>"
        lines.append(t)
    ages = [a.get("age") for a in cands if a.get("age") is not None]
    foot = (f"{quotes.fmt_age(max(ages)) if ages else 'không rõ độ trễ'} · "
            "mỗi quy tắc báo 1 lần/ngày · sửa ở tab Watchlist")
    return "🔔 <b>Cảnh báo của bạn</b>\n" + "\n".join(lines) + f"\n<i>{foot}</i>"


# ───────────────────────── vong chay ─────────────────────────
def _load_rules() -> dict:
    import push
    js = push.get_full(KEY)
    if js is None:
        return parse(None)
    return parse(js.get("value"), js.get("updatedAt"))


def _put_seen(value, dry: bool) -> str:
    import push
    return push.put(SEEN_KEY, value, dry=dry)


class Runner:
    """Trang thai giua cac vong: quy tac, gia tham chieu theo ngay, lan day cuoi.

    Moi thu ben ngoai deu tiem duoc - test chay khong mang, khong Telegram.
    """

    def __init__(self, g: dict | None = None, lg=None, dry: bool = False,
                 load_rules=_load_rules, fetch_refs=refs_from_yf,
                 put_seen=_put_seen) -> None:
        g = g or {}
        self.max_age = float(g.get("max_quote_age_sec") or 1200)
        self.lg, self.dry = lg, dry
        self.load_rules, self.fetch_refs, self.put_seen = load_rules, fetch_refs, put_seen
        self.rules = parse(None)
        self.loaded: dt.datetime | None = None
        self.refs: dict[tuple[str, str], dict] = {}
        self.ref_tried: dict[tuple[str, str], float] = {}
        self.seen_dig, self.seen_at = "", 0.0
        self.warn: list[str] = []
        self._ddl = False

    def _log(self, msg: str) -> None:
        if self.lg:
            self.lg.info(f"useralerts: {msg}")

    def _reload(self, now: dt.datetime) -> None:
        try:
            self.rules = self.load_rules()
        except Exception as e:                                   # noqa: BLE001
            self.warn = [f"không đọc được quy tắc ({type(e).__name__})"]
            self._log(self.warn[0])
        self.loaded = now

    def _ensure_refs(self, syms: list[str], day_of: dict[str, str], now: float) -> None:
        need = [s for s in syms if (s, day_of[s]) not in self.refs
                and now - self.ref_tried.get((s, day_of[s]), 0) >= REF_RETRY_SEC]
        if not need:
            return
        got = self.fetch_refs(need, {s: day_of[s] for s in need}) or {}
        for s in need:
            self.ref_tried[(s, day_of[s])] = now
            if s in got:
                self.refs[(s, day_of[s])] = got[s]
        # Ngay cu khong bao gio dung lai: giu cache nho.
        today = set(day_of.values())
        self.refs = {k: v for k, v in self.refs.items() if k[1] in today}

    async def step(self, c: sqlite3.Connection, prov, send, now: dt.datetime) -> dict:
        """Mot vong. Tra ve so dem de log. Loi bay ra ngoai: lop goi bat va ghi log."""
        if not self._ddl:
            ensure(c)
            self._ddl = True
        if self.loaded is None or (now - self.loaded).total_seconds() >= REFRESH_SEC:
            self._reload(now)
        r = {"open": [], "n_sym": 0, "cands": 0, "sent": 0, "skip": 0}
        sess = {m: session(m, now) for m in MARKETS}
        r["open"] = [m for m, s in sess.items() if s["open"]]
        rules = self.rules["syms"] if self.rules.get("on") else {}
        syms = [s for s in rules if market_of(s) in r["open"]]
        r["n_sym"] = len(syms)
        if syms:
            day_of = {s: sess[market_of(s)]["d"] for s in syms}
            self._ensure_refs(syms, day_of, now.timestamp())
            qs = prov.fetch(syms)
            refs = {s: self.refs.get((s, day_of[s]), {}) for s in syms}
            cands, skip = evaluate({s: rules[s] for s in syms}, qs, refs, sess, self.max_age)
            r["skip"] = len(skip)
            done = fired(c, day_of.values())
            new = [a for a in cands if (a["d"], a["sym"], a["rule"]) not in done]
            r["cands"] = len(new)
            if new and await send(render(new), True, None):
                for a in new:
                    record(c, a, now)
                r["sent"] = len(new)
            elif new:
                self._log(f"gui hong {len(new)} canh bao, vong sau thu lai")
            self.warn = sorted({f"{s}: {why}" for s, why in skip})[:10]
        self._seen(c, now, r["open"])
        return r

    def _seen(self, c: sqlite3.Connection, now: dt.datetime, open_mk: list[str]) -> None:
        since = (now - dt.timedelta(days=1)).date().isoformat()
        body = {"rules_at": self.rules.get("rules_at"), "n": len(self.rules["syms"]),
                "on": bool(self.rules.get("on")), "open": open_mk,
                "fired": recent(c, since),
                "warn": ([self.rules["note"]] if self.rules.get("note") else []) + self.warn}
        dig = hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        t = now.timestamp()
        if dig == self.seen_dig and t - self.seen_at < SEEN_EVERY_SEC:
            return
        try:
            res = self.put_seen(dict(body, at=now.isoformat(timespec="seconds")), self.dry)
        except Exception as e:                                   # noqa: BLE001
            res = f"err {type(e).__name__}"
        if res in ("sent", "same", "dry"):
            self.seen_dig, self.seen_at = dig, t
        elif res != "off":
            self._log(f"day {SEEN_KEY}: {res}")
