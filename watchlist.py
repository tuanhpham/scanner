"""watchlist.py - danh sach DUY NHAT ma phan trong phien duoc phep canh bao.

HAI TIEN TRINH, MOT NGUON SU THAT
---------------------------------
Dem truoc (nightly.py) GHI: danh sach + ke hoach lenh cho tung ma. Trong phien
DOC: lay danh sach cua hom nay mot lan luc khoi dong, roi chi so gia voi ke
hoach da co. Trong phien KHONG BAO GIO them mot ma khong co trong danh sach, va
KHONG BAO GIO tinh lai ke hoach.

Do la thay doi lon nhat so voi scanner cu. Cai cu di TIM ma trong phien theo
"% tang lon nhat", va vi phan tram tang tren co phieu gia thap la chuyen tam
thuong nen no tra ve toan co phieu rac: spread rong, khong co to chuc tham gia,
rui ro gap. Danh sach o day duoc chot tu dem truoc, sau khi da qua sang loc cua
Stage 1-3, nen van de do khong con cua de vao.

SAN CHAT LUONG (config.INTRADAY)
--------------------------------
Sang loc cua Stage 3 va san o day KHONG trung nhau va do la co y: Stage 3 chon
"ma dan dat", san o day chan "ma khong nen giao dich bang tien that". Mot ma co
the la ma dan dat cua mot nganh dan dat va van khong qua duoc san (vi du von hoa
1.2 ty). Kiem hai lan re hon mot lan bo lot.

⚠️ THIEU DU LIEU KHONG BAO GIO LA "DAT". Mot tieu chi khong do duoc thi no vao
danh sach `unknown` va di nguyen van vao tin nhan, chu khong bao gio bi lam
tron thanh mot dau tich. Do la ly do check() tra ve HAI danh sach.

Thuan stdlib (chi sqlite3). Ham duy nhat can mang la refresh_mktcap(), va no
import yfinance BEN TRONG ham - de test va selftest chay duoc o may khong co
yfinance.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent
DB = ROOT / "state" / "baseline.db"

# Ly do bi loai. Khoa ASCII de dem va de ghi log; cau tieng Viet co dau de di
# vao Telegram va dashboard. Viet MOT lan o day, khong co ban thu hai nao.
WHY = {
    "gia_thap": "giá dưới sàn",
    "thanh_khoan": "thanh khoản (giá × ADV50) dưới sàn",
    "von_hoa": "vốn hóa dưới sàn",
    "san_niem_yet": "sàn niêm yết không nằm trong danh sách cho phép",
    "moi_niem_yet": "niêm yết chưa đủ lâu",
    "spread_rong": "spread rộng hơn sàn",
    "khong_ke_hoach": "không có kế hoạch lệnh (thiếu ATR hoặc thiếu mốc giá)",
}

# Tieu chi khong do duoc. Cung di vao tin nhan, nhung KHAC voi bi loai.
UNSURE = {
    "von_hoa": "chưa biết vốn hóa",
    "san_niem_yet": "chưa biết sàn niêm yết",
    "moi_niem_yet": "chưa kiểm được tuổi niêm yết (kho nến chưa đủ sâu)",
    "spread_rong": "chưa kiểm được spread",
}


def _num(x) -> float | None:
    """So thuc huu han, hoac None. Giong plan._num."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


# ───────────────────────── san chat luong ─────────────────────────
def check(m: dict, g: dict | None = None,
          ref_bars: int | None = None) -> tuple[list[str], list[str]]:
    """Kiem mot ma. Tra ve (ly do bi loai, tieu chi khong do duoc).

    `m` gom cac truong: px (hoac ref_close), adv50, exch, mktcap, n_bars,
    spread_pct, trigger, stop. Thieu truong nao thi tieu chi do thanh "khong do
    duoc" - KHONG thanh "dat".

    `ref_bars` = so nen cua ma tham chieu (SPY) trong kho. Dung de tra loi cau
    "ma nay moi niem yet, hay ca kho nen vua backfill?". Khong co no thi tuoi
    niem yet la khong kiem duoc, chu khong phai la loai sach ca danh sach.

    Danh sach tra ve la THU TU ON DINH (theo thu tu kiem o duoi), de tin nhan
    hai phien lien tiep doc giong nhau.
    """
    g = g or config.INTRADAY
    bad: list[str] = []
    unsure: list[str] = []

    px = _num(m.get("px")) or _num(m.get("ref_close"))
    if px is None:
        # Khong co gia thi khong con gi kiem duoc: day khong phai mot ma, day la
        # mot dong du lieu vo.
        return ["gia_thap"], []
    if px < float(g["min_px"]):
        bad.append("gia_thap")

    # adv50 thieu -> LOAI, khong phai "khong do duoc". Mot ma vao duoc den day
    # thi da qua Stage 3 (LEAD doi adv50 x gia > $20M), nen thieu adv50 chi xay
    # ra khi bang `struct` va bang `candidates` lech phien - va luc do dung hon
    # la bo qua ma, chu khong phai tin no.
    adv50 = _num(m.get("adv50"))
    if adv50 is None:
        bad.append("thanh_khoan")
    elif adv50 * px < float(g["min_dollar_vol"]):
        bad.append("thanh_khoan")

    cap = _num(m.get("mktcap"))
    if cap is None:
        unsure.append("von_hoa")
    elif cap < float(g["min_mktcap"]):
        bad.append("von_hoa")

    ex = (m.get("exch") or "").strip().upper()
    if not ex:
        unsure.append("san_niem_yet")
    elif ex not in tuple(g["exchanges"]):
        bad.append("san_niem_yet")

    nb = _num(m.get("n_bars"))
    need = float(g["min_listed_days"])
    if nb is None:
        unsure.append("moi_niem_yet")
    elif nb >= need:
        pass
    elif ref_bars is not None and float(ref_bars) >= need:
        # Kho nen du sau ma ma nay it nen -> no that su moi niem yet.
        bad.append("moi_niem_yet")
    else:
        # Ca kho chi co bay nhieu nen: moi ma deu "moi", ke ca SPY. Loai sach
        # danh sach o day la bien mot van de ha tang thanh mot phien im lang.
        unsure.append("moi_niem_yet")

    sp = _num(m.get("spread_pct"))
    if sp is None:
        unsure.append("spread_rong")
    elif sp > float(g["max_spread_pct"]):
        bad.append("spread_rong")

    # Khong co ke hoach thi khong co gi de canh: khong biet vao o dau, khong
    # biet stop o dau. Mot ma nhu the thuoc dashboard, khong thuoc alert.
    if _num(m.get("trigger")) is None or _num(m.get("stop")) is None:
        bad.append("khong_ke_hoach")

    return bad, unsure


def fmt_why(keys: list[str], table: dict[str, str] | None = None) -> str:
    """Cac ly do thanh mot cau tieng Viet co dau."""
    t = table or WHY
    return ", ".join(t.get(k, k) for k in keys)


# ───────────────────────── doc danh sach cua hom nay ─────────────────────────
def _con(db=DB) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    return c


PLAN_COLS = ("trigger", "stop", "target", "stop_pct", "risk_pct", "size_pct")

# Cot lay tu `candidates`. `d` la NGAY CUA NEN QUYET DINH, khong phai ngay chay.
CAND_COLS = ("sym", "d", "ref_close", "pivot", "atr_pct", "sector",
             "quality") + PLAN_COLS


def _cols(c, table: str) -> set[str]:
    try:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def ref_bars(c) -> int | None:
    """So nen cua ma tham chieu trong kho. Xem check(ref_bars=)."""
    r = c.execute("SELECT n_bars FROM struct WHERE sym=?",
                  (config.BENCH,)).fetchone()
    if r and r[0]:
        return int(r[0])
    # SPY khong co trong `struct` (chay --limit, hoac kho chua co SPY): lay ma
    # sau nhat lam moc. Do uoc luong "kho co the sau den bao nhieu".
    r = c.execute("SELECT MAX(n_bars) FROM struct").fetchone()
    return int(r[0]) if r and r[0] else None


def load(db=DB, day: str | None = None, g: dict | None = None,
         manual: bool = True) -> dict:
    """Danh sach hom nay, da qua san chat luong.

    Tra ve dict:
      rows     cac ma DUOC canh bao, kem ke hoach + cac tieu chi khong do duoc
      drop     [(sym, [ly do]), ...] bi san loai
      day      ngay cua nen quyet dinh (tu bang `candidates`)
      stale    True neu nen quyet dinh cu hon `max_age` phien lam viec
      note     cau tieng Viet noi tinh trang, de di thang vao tin nhan mo phien

    `day` = ngay chay (ET). Dung de biet danh sach co phai cua hom nay khong.
    KHONG dung de loc: bang `candidates` chi giu mot phien, va no la phien DA
    CHOT - so sanh voi hom nay la cach duy nhat phat hien cron chet tu hai ngay
    truoc ma bot van canh mot ke hoach da het han.
    """
    g = g or config.INTRADAY
    out: dict = {"rows": [], "drop": [], "day": None, "stale": False,
                 "note": "", "manual": 0}
    try:
        c = _con(db)
    except sqlite3.Error as e:
        out["note"] = f"không mở được cơ sở dữ liệu ({type(e).__name__})"
        return out
    try:
        have = _cols(c, "candidates")
        if not have:
            out["note"] = ("bảng `candidates` chưa tồn tại — chuỗi chạy buổi "
                           "sáng chưa chạy lần nào")
            return out
        cols = [x for x in CAND_COLS if x in have]
        rows = [dict(r) for r in c.execute(
            f"SELECT {','.join(cols)} FROM candidates WHERE setup='LEAD' "
            f"ORDER BY quality DESC")]

        # Ma them tay duoc gop vao TRUOC khi xet "danh sach trong". Truong hop
        # dung nhat cua no la dem qua khong ma nao dat nhung minh van muon theo
        # mot ma: thoat som o day lam cai dong `watch` khong lam gi ca, im lang.
        if manual:
            them = _manual(c, cols, {r["sym"] for r in rows})
            out["manual"] = len(them)
            rows += them

        if not rows:
            out["note"] = ("danh sách theo dõi trống — không mã nào qua sàng "
                           "lọc đêm qua")
            return out

        out["day"] = rows[0].get("d")
        rb = ref_bars(c)

        # `base` va `struct` co the thieu cot (DB cu) hoac thieu ca bang (VM
        # moi). Thieu thi cac tieu chi tuong ung thanh "khong do duoc" - do la
        # viec cua check(), khong phai cho no mot gia tri gia o day.
        base = _by_sym(c, "base", ("exch", "mktcap"))
        st = _by_sym(c, "struct", ("px", "adv50", "n_bars", "atr14"))

        for r in rows:
            m = {**st.get(r["sym"], {}), **base.get(r["sym"], {}), **r}
            bad, unsure = check(m, g, rb)
            if bad:
                out["drop"].append((r["sym"], bad))
                continue
            m["unsure"] = unsure
            out["rows"].append(m)
    finally:
        c.close()

    if day and out["day"]:
        n = _biz_days(out["day"], day)
        if n is not None and n > MAX_AGE_BIZ:
            # Cron chet vai ngay truoc ma bot van canh mot ke hoach het han la
            # dung kieu that bai im lang te nhat: no van gui alert, van dung
            # dinh dang, chi la ve mot phien khong con ton tai.
            out["stale"] = True
            out["note"] = (f"kế hoạch tính từ nến {out['day']}, đã cách "
                           f"{n} ngày làm việc — chuỗi chạy buổi sáng có vấn đề")
    if not out["rows"] and not out["note"]:
        out["note"] = ("không mã nào qua sàn chất lượng: "
                       + "; ".join(f"{s} ({fmt_why(w)})" for s, w in out["drop"]))
    return out


# Nen quyet dinh cu hon bay nhieu NGAY LAM VIEC thi danh sach la do. 3 chu khong
# 1: sang thu Ba sau mot thu Hai nghi le, nen cua thu Sau da cach 2 ngay lam
# viec; con nghi le hai ngay (Thanksgiving) thi cach 3. Duoi muc nay bao dong
# moi tuan thi khong ai doc bao dong nua.
MAX_AGE_BIZ = 3


def _biz_days(d0: str, d1: str) -> int | None:
    """So ngay lam viec tu d0 den d1 (ISO). None neu ngay khong doc duoc.

    Khong dem thu Bay/Chu Nhat: mot nguong tinh theo ngay lich se bao dong MOI
    thu Hai, va mot bao dong bao moi thu Hai la mot bao dong bi tat.
    """
    try:
        a = dt.date.fromisoformat(d0[:10])
        b = dt.date.fromisoformat(d1[:10])
    except (TypeError, ValueError):
        return None
    if b < a:
        return 0
    n = 0
    while a < b:
        a += dt.timedelta(days=1)
        if a.weekday() < 5:
            n += 1
    return n


def _by_sym(c, table: str, want: tuple[str, ...]) -> dict[str, dict]:
    """{sym: {cot: gia tri}} cho cac cot THAT SU co trong bang.

    Cot thieu thi khong co trong dict, nen check() nhin thay `None` va ghi
    "khong do duoc". Do la ket qua dung: mot cot chua ton tai khong phai mot
    tieu chi da dat.
    """
    have = _cols(c, table)
    cols = [x for x in want if x in have]
    if "sym" not in have or not cols:
        return {}
    return {r["sym"]: dict(r) for r in
            c.execute(f"SELECT sym,{','.join(cols)} FROM {table}")}


def _manual(c, cols: list[str], da_co: set[str]) -> list[dict]:
    """Ma them tay: bang `watch` voi kind='manual'.

    "Danh sach bat bien trong phien" va "them tay qua DB/CLI" hoi xung dot nhau.
    Cach dung hoa: chi nhan dong co co `manual`, van ep san chat luong y nguyen,
    va danh dau `_manual` de tin nhan noi ro day la ma ban tu them - de sau nay
    doc lai lich su thi biet cai nao do he thong chon.

    Ma them tay VAN phai co dong trong `candidates` (tuc la van phai co ke hoach
    lenh): khong co ke hoach thi trong phien lai phai ung bien, dung cai ma ca
    thiet ke nay dung de tranh.
    """
    try:
        syms = [r[0] for r in
                c.execute("SELECT sym FROM watch WHERE kind='manual'")]
    except sqlite3.Error:
        return []                       # bang `watch` chua ton tai
    syms = [s for s in syms if s not in da_co]
    if not syms:
        return []
    q = ",".join("?" * len(syms))
    # ORDER BY: mot ma co the co dong o nhieu setup. Uu tien LEAD (cung setup
    # voi danh sach dem) roi lay dong dau moi ma - khong de SQLite tu chon.
    seen: dict[str, dict] = {}
    for r in c.execute(f"SELECT {','.join(cols)}, setup FROM candidates "
                       f"WHERE sym IN ({q}) "
                       f"ORDER BY CASE setup WHEN 'LEAD' THEN 0 ELSE 1 END",
                       syms):
        seen.setdefault(r["sym"], {**dict(r), "_manual": True})
    return list(seen.values())


# ───────────────────────── cong regime ─────────────────────────
# Bon che do, theo prompt 2. Day la CONG CUNG luc khoi dong: no quyet dinh loai
# alert nao ton tai trong phien, chu khong phai loc tung alert mot.
MODE_VI = {
    "full": "đầy đủ — cả vào mới và quản lý vị thế",
    "manage": "chỉ quản lý vị thế đang có, không vào mới",
    "revert": "chỉ mua lại trong kênh giá (mean-reversion)",
    "stop_only": "chỉ canh cắt lỗ cho vị thế đang mở",
}


def gate(db=DB) -> dict:
    """Cong regime. Tra ve {mode, allow_new, allow_manage, setups, size, why}.

    ⚠️ MAC DINH KHI KHONG BIET LA DUNG NGOAI. Khong doc duoc `regime`, hoac
    khong co dong nao, hoac cap (trend, vol) khong co trong PLAYBOOK -> mode
    "stop_only". Mot Stage 1 chet khong duoc sinh ra mot phien full size ma
    khong ai duoc bao.

    Dung DUNG config.PLAYBOOK, khong co bang thu hai: prompt 2 doi "same config
    lookup table as nightly", va hai bang se lech nhau mot cach im lang.
    """
    out = {"mode": "stop_only", "allow_new": False, "allow_manage": True,
           "setups": (), "size": 0.0, "trend": None, "vol": None,
           "why": "chưa đọc được trạng thái thị trường → đứng ngoài"}
    try:
        c = _con(db)
    except sqlite3.Error as e:
        out["why"] = f"không mở được cơ sở dữ liệu ({type(e).__name__})"
        return out
    try:
        r = c.execute("SELECT d, trend, vol FROM regime "
                      "ORDER BY d DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        r = None
    finally:
        c.close()
    if not r:
        out["why"] = ("chưa có bản ghi trạng thái thị trường → chỉ canh cắt lỗ, "
                      "không vào lệnh mới")
        return out

    trend, vol = r["trend"], r["vol"]
    pb = config.PLAYBOOK.get((trend, vol))
    if not pb:
        out["why"] = (f"không có dòng playbook cho {trend}/{vol} → đứng ngoài")
        return out

    out.update({"trend": trend, "vol": vol, "d": r["d"],
                "setups": tuple(pb["setups"]), "size": float(pb["size"])})
    # Che do suy ra tu chinh PLAYBOOK, khong tu mot bang if rieng: `setups` rong
    # + size 0 la "khong vao moi", va DOWNTREND la truong hop dac biet duy nhat
    # (im lang hoan toan tru stop).
    if trend == "DOWNTREND":
        out["mode"] = "stop_only"
    elif not pb["setups"] or pb["size"] <= 0:
        out["mode"] = "manage"
    elif tuple(pb["setups"]) == ("RV",):
        out["mode"] = "revert"
    else:
        out["mode"] = "full"
    out["allow_new"] = out["mode"] in ("full", "revert")
    out["why"] = f"{trend} · {vol} → {MODE_VI[out['mode']]}"
    return out


# ───────────────────────── von hoa (can mang) ─────────────────────────
# Hai cot nay cung nam trong prep.ADD_COLS - do la cho chinh thuc de migrate
# bang `base`. Giu ban sao o day vi refresh_mktcap() co the chay tren mot DB ma
# prep.py chua cham tay vao lan nao, va watchlist.py phai thuan stdlib (prep.py
# import pandas + yfinance ngay dong dau). test_prep_base.py giu hai ben khop
# nhau; sua o mot ben ma quen ben kia se bi bat o do.
MKTCAP_COLS = (("mktcap", "REAL"), ("mktcap_ts", "TEXT"))


def refresh_mktcap(db=DB, syms: list[str] | None = None,
                   ttl_days: int | None = None) -> dict:
    """Lay `marketCap` tu yfinance cho cac ma trong danh sach, cache vao `base`.

    Chi <= 10 ma moi dem (tran cua config.LEAD), va cache TTL 7 ngay, nen day la
    mot chu so request moi dem. Goi tu nightly.py sau buoc setups.

    KHONG dung float_sh x gia: do la von hoa FLOAT, khac von hoa thuc khi noi bo
    giu nhieu co phan - va goi no la "von hoa" thi san $2B tro thanh mot con so
    khac han cai da viet trong config.

    Import yfinance BEN TRONG ham: moi thu con lai cua file nay thuan stdlib va
    phai chay duoc o may khong co yfinance.
    """
    g = config.INTRADAY
    ttl = int(g["mktcap_ttl_days"] if ttl_days is None else ttl_days)
    out = {"asked": 0, "ok": 0, "fail": 0, "cached": 0, "err": ""}

    con = sqlite3.connect(db, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        have = {r[1] for r in con.execute("PRAGMA table_info(base)")}
        for name, typ in MKTCAP_COLS:
            if name not in have:
                con.execute(f"ALTER TABLE base ADD COLUMN {name} {typ}")
        con.commit()

        if syms is None:
            syms = [r[0] for r in con.execute(
                "SELECT sym FROM candidates WHERE setup='LEAD'")]
        if not syms:
            return out

        cut = (dt.datetime.now(dt.timezone.utc)
               - dt.timedelta(days=ttl)).isoformat(timespec="seconds")
        fresh = {r[0] for r in con.execute(
            f"SELECT sym FROM base WHERE mktcap IS NOT NULL AND mktcap_ts > ? "
            f"AND sym IN ({','.join('?' * len(syms))})", (cut, *syms))}
        todo = [s for s in syms if s not in fresh]
        out["cached"] = len(fresh)
        if not todo:
            return out

        try:
            import yfinance as yf
        except Exception as e:                                   # noqa: BLE001
            out["err"] = f"không import được yfinance: {type(e).__name__}"
            return out

        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        for s in todo:
            out["asked"] += 1
            cap = None
            try:
                info = yf.Ticker(s).get_info()
                cap = _num(info.get("marketCap"))
            except Exception:                                    # noqa: BLE001
                cap = None
            if cap and cap > 0:
                con.execute("UPDATE base SET mktcap=?, mktcap_ts=? WHERE sym=?",
                            (cap, now, s))
                out["ok"] += 1
            else:
                # KHONG ghi 0 va KHONG ghi mktcap_ts: mot lan hong khong duoc
                # bien thanh "da biet von hoa = 0" (se loai ma vinh vien) lan
                # thanh "da kiem roi" (se khong thu lai trong 7 ngay).
                out["fail"] += 1
        con.commit()
    finally:
        con.close()
    return out


# ───────────────────────── CLI + selftest ─────────────────────────
def _show(db=DB) -> int:
    g = gate(db)
    print(f"CONG REGIME: {g['mode']}  ({g['why']})")
    print(f"  vao moi: {'co' if g['allow_new'] else 'KHONG'}"
          f" · co vi the playbook: {g['size']:.0%}"
          f" · setup: {', '.join(g['setups']) or '-'}")
    w = load(db)
    print(f"\nDANH SACH ({len(w['rows'])} qua san, {len(w['drop'])} bi loai)"
          f"  nen quyet dinh: {w['day'] or '-'}")
    if w["note"]:
        print(f"  {w['note']}")
    if w["rows"]:
        print(f"\n{'MA':<6}{'VAO':>9}{'STOP':>9}{'CO':>6}  GHI CHU")
        for r in w["rows"]:
            note = fmt_why(r.get("unsure") or [], UNSURE)
            print(f"{r['sym']:<6}{r.get('trigger') or 0:>9.2f}"
                  f"{r.get('stop') or 0:>9.2f}"
                  f"{(r.get('size_pct') or 0):>6.0%}  "
                  f"{'[tay] ' if r.get('_manual') else ''}{note}")
    for s, why in w["drop"]:
        print(f"  loai {s}: {fmt_why(why)}")
    return 0


def _smoke() -> None:
    g = config.INTRADAY
    ok = {"px": 50.0, "adv50": 2_000_000, "exch": "NASDAQ",
          "mktcap": 50e9, "n_bars": 300, "spread_pct": 0.0005,
          "trigger": 51.0, "stop": 48.0}
    bad, unsure = check(ok, g, ref_bars=300)
    assert bad == [] and unsure == [], (bad, unsure)

    # Moi san mot lan, de mot san hong khong bi che boi san khac.
    # Gia thap + thanh khoan van dat (adv50 bu lai): phai chi bao gia, de doc
    # duoc ly do THAT chu khong phai mot dong loi keo theo.
    assert check({**ok, "px": 5.0, "adv50": 20_000_000}, g, 300)[0] == ["gia_thap"]
    assert check({**ok, "adv50": 100}, g, 300)[0] == ["thanh_khoan"]
    assert check({**ok, "mktcap": 1e9}, g, 300)[0] == ["von_hoa"]
    assert check({**ok, "exch": "AMEX"}, g, 300)[0] == ["san_niem_yet"]
    assert check({**ok, "exch": "ARCA"}, g, 300)[0] == [], "ARCA phai duoc nhan"
    assert check({**ok, "spread_pct": 0.01}, g, 300)[0] == ["spread_rong"]
    assert check({**ok, "trigger": None}, g, 300)[0] == ["khong_ke_hoach"]
    assert check({**ok, "stop": None}, g, 300)[0] == ["khong_ke_hoach"]

    # Thieu du lieu != dat. Day la cho de sai nhat ca file.
    for k, want in (("mktcap", "von_hoa"), ("exch", "san_niem_yet"),
                    ("spread_pct", "spread_rong")):
        bad, unsure = check({**ok, k: None}, g, 300)
        assert bad == [] and unsure == [want], (k, bad, unsure)

    # Tuoi niem yet: ma it nen + kho sau = that su moi -> loai.
    assert check({**ok, "n_bars": 100}, g, ref_bars=300)[0] == ["moi_niem_yet"]
    # Ma it nen + kho cung nong = khong kiem duoc -> KHONG loai.
    bad, unsure = check({**ok, "n_bars": 100}, g, ref_bars=100)
    assert bad == [] and unsure == ["moi_niem_yet"], (bad, unsure)
    # Khong co moc tham chieu -> cung khong kiem duoc.
    assert check({**ok, "n_bars": 100}, g, None)[1] == ["moi_niem_yet"]

    # ref_close dung thay px duoc (bang `candidates` khong co cot px).
    m = {k: v for k, v in ok.items() if k != "px"}
    assert check({**m, "ref_close": 50.0}, g, 300)[0] == []
    assert check(m, g, 300)[0] == ["gia_thap"], "khong co gia thi khong doan"

    # Cau tieng Viet co dau, va ly do nao cung phai co cau cua no.
    assert "vốn hóa" in fmt_why(["von_hoa"])
    for k in WHY:
        assert fmt_why([k]) != k, f"thieu cau tieng Viet cho `{k}`"
    for k in UNSURE:
        assert fmt_why([k], UNSURE) != k

    print("watchlist.py: smoke ok")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--show", action="store_true",
                    help="cong regime + danh sach hom nay + ly do bi loai")
    ap.add_argument("--mktcap", action="store_true",
                    help="lay von hoa cho cac ma trong danh sach (can mang)")
    args = ap.parse_args()
    if args.mktcap:
        r = refresh_mktcap(args.db)
        print(f"von hoa: {r['ok']} lay moi, {r['cached']} con han, "
              f"{r['fail']} that bai" + (f" — {r['err']}" if r["err"] else ""))
        return 0 if not r["err"] else 1
    if args.show:
        return _show(args.db)
    _smoke()
    return 0


if __name__ == "__main__":
    sys.exit(main())
