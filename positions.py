"""positions.py - vi the dang mo, doc tu khoa `scanner:positions`.

TAI SAO CAN FILE NAY
--------------------
Cong regime (watchlist.gate) co mot che do "stop_only": DOWNTREND thi im lang
toan bo canh bao mua, chi con canh cat lo cho vi the DANG MO. Che do do vo
nghia neu scanner khong biet minh dang giu gi - no se "im lang toan bo" that,
tuc la dung vao nhung ngay mot muc cat lo quan trong nhat.

Nguon la app lux-lookthrough: moi lan luu portfolio, no day mot ban rut gon len
`scanner:positions` (ma, so co phieu dang mo, gia von binh quan, CAC muc cat
lo, tien te). Khong co tien, khong co von, khong co lai/lo, khong co ngay.
Phia ghi: apps/desktop/src/portfolio/positionsFeed.ts.

⚠️ THIEU KHONG PHAI LA "KHONG CO VI THE". Doc khong duoc khoa (chua cau hinh
push, mang chet, 404, JSON la) -> known=False. Cai gia cua nham lan nay:
"khong co vi the" thi khong canh gi ca va do la mot phien im lang HOAN TOAN
hop le ve hinh thuc; "khong biet" thi phai noi ra trong tin nhan mo phien.

⚠️ CU KHONG PHAI LA SAI. Khoa nay duoc ghi theo SU KIEN (luc luu portfolio),
khong theo dinh ky. Khong giao dich mot tuan thi no cu mot tuan va van dung
tung chu. Nen tuoi o day CHI de noi ra (`old`), KHONG BAO GIO dung de vo hieu
hoa du lieu. Lay 30 gio lam nguong bao thi moi thu Hai deu qua nguong, va neu
"qua nguong" co nghia la bo qua thi ta vua tat canh cat lo cho phan lon cac
ngay trong nam.

BA CAI BAY O PHIA BEN KIA (da xu ly trong positionsDigest.ts, ghi lai de doc
mot cho hieu ca duong di):
  1. Nhieu lo cung mot ma, moi lo mot stop. Gia giam thi xuyen muc CAO NHAT
     truoc. Bang portfolio hien stop cua lo CU NHAT - lay so do di canh thi
     canh muon, hoac khong bao gio canh.
  2. Stop dat bang LENH CHO (STOP_LOSS order) khong nam trong `lot.stop`. Voi
     nguoi dung thi hai thu do la mot thu.
  3. So DANG LUU co the bang EUR trong khi bao gia la USD. File nay KHONG BAO
     GIO doan ty gia. Nhung tu 28.09.2026 no khong con phai dung ngoai vi ly do
     do: anh chup mang theo TY GIA (`fx`) ma chinh app dung, kem NGAY chot ty
     gia do. Co ty gia con moi thi quy doi va NOI RA da quy doi bang ty gia nao;
     khong co, hoac ty gia da qua cu (`pos_fx_stale_d`), thi van dung ngoai va
     van noi ra qua `unchecked()`.

     ⚠️ TAI SAO KHONG TU LAY TY GIA O DAY. yfinance co `EURUSD=X`, nen tu lay la
     de. Nhung luc do muc cat lo trong tin nhan se tinh bang mot ty gia KHAC voi
     ty gia bang portfolio trong app dang hien - hai con so cho cung mot cai
     stop, va nguoi doc khong co cach nao biet cai nao dung. Mot nguon thi sai
     cung sai o mot cho.

     ⚠️ TAI SAO PHAI CO `as_of`. Khoa duoc ghi luc LUU PORTFOLIO. Khong giao
     dich ba thang thi ty gia trong anh chup cu ba thang, va quy doi bang no
     dung lai y nguyen cai loi ma ca file nay sinh ra de tranh: mot muc tu tin
     nhung sai. Nen ty gia cu thi TU CHOI, chu khong "co con hon khong".

Thuan stdlib. Ham duy nhat ra mang la load(), va no chi goi push.get_full()
(push.py tu tat khi thieu bien moi truong). parse() la ham thuan, nen toan bo
test chay khong can mang.

    python positions.py            # selftest
    python positions.py --show     # doc that tu cloud
    python positions.py --show --from snapshot.json   # doc tu file, khong mang
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import config

# Tien te duy nhat so sanh duoc voi bao gia cua yfinance (thi truong My).
USD = "USD"
# Tien te duy nhat quy doi duoc, va chi khi anh chup gui kem ty gia.
EUR = "EUR"
# Cung mot ma ma phia ghi thay ca hai don vi: khong co mot don vi nao de quy doi
# TU do, nen ty gia khong giup duoc gi.
MIXED = "MIXED"

# Chan tinh than cho ty gia EURUSD. Khong phai de bat sai so nho - de bat mot
# con so KHONG PHAI ty gia (0, 1e9, mot gia co phieu loi vao truong nay). 1.0
# nam trong khoang nay va phai nam trong: nam 2022 EURUSD that su quanh 1.0.
FX_MIN, FX_MAX = 0.5, 2.0

# Cau tieng Viet cho tung ly do khong canh duoc stop. Viet mot lan o day, va
# PHAI phu het cac khoa stop_hit() co the tra ve trong `why`: ben goi se tra cuu
# bang nay de viet tin nhan, va mot khoa thieu la mot KeyError giua phien.
WHY_UNCHECKED = {
    "tien_te": "số đang lưu bằng {cur} mà báo giá là USD, ảnh chụp không gửi "
               "kèm tỷ giá",
    "ty_gia_cu": "số đang lưu bằng {cur} mà báo giá là USD, tỷ giá gửi kèm đã "
                 "quá cũ để quy đổi",
    "tron_tien_te": "cùng một mã có lô lưu bằng EUR và lô bằng USD — phải tách "
                    "tài khoản, tỷ giá không giúp được",
    "khong_biet_tien_te": "chưa biết giá nhập bằng tiền gì",
    "khong_co_stop": "chưa đặt mức cắt lỗ",
    "khong_co_gia": "chưa có báo giá",
}


def _num(x) -> float | None:
    """float huu han, hoac None. JSON co the mang theo null, "", NaN, chuoi."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def _sym(x) -> str | None:
    s = str(x or "").strip().upper()
    return s or None


def _fx(raw, now: dt.datetime, max_age_d: float | None) -> dict | None:
    """Ty gia di kem anh chup, da kiem. None = coi nhu khong gui kem.

    Tra {"rate", "as_of", "age_d", "ok", "why"}. `ok=False` van tra ve chu khong
    tra None: tin nhan phai noi duoc "co ty gia nhung cu qua nen khong dung",
    khac han voi "khong co ty gia" - hai cach sua khac nhau.
    """
    if not isinstance(raw, dict):
        return None
    rate = _num(raw.get("eurUsd"))
    if rate is None or not (FX_MIN <= rate <= FX_MAX):
        return None
    as_of = str(raw.get("asOf") or "").strip()
    try:
        d = dt.date.fromisoformat(as_of)
    except ValueError:
        # Co ty gia ma khong biet no cua ngay nao thi khong kiem duoc tuoi, va
        # khong kiem duoc tuoi thi khong duoc dung: xem docstring dau file.
        return None
    # Toi da voi 0 giong age_h: ngay cua may ghi lech ve tuong lai khong duoc
    # bien thanh mot ty gia "moi hon hom nay".
    age_d = max(0.0, (now.date() - d).days)
    ok = not (max_age_d and age_d > max_age_d)
    return {"rate": rate, "as_of": as_of, "age_d": age_d, "ok": ok,
            "why": None if ok else "ty_gia_cu"}


def _row(raw: dict, fx: dict | None = None) -> dict | None:
    """Mot dong da kiem, hoac None neu khong dung duoc.

    Kiem lai o phia doc du phia ghi da kiem: hai ben la hai repo, deploy doc
    lap, va mot ban app cu hon co the day len mot hinh dang khac. Mot dong la
    khong duoc phep lam chet vong quet.
    """
    if not isinstance(raw, dict):
        return None
    sym = _sym(raw.get("sym"))
    shares = _num(raw.get("shares"))
    if not sym or not shares or shares <= 0:
        return None

    # Muc cat lo: chi giu so duong, bo trung, XEP GIAM DAN. Thu tu la mot phan
    # cua ngu nghia chu khong phai de cho dep - stops[0] la muc bi xuyen dau
    # tien khi gia giam, va stop_hit() dua vao dieu do.
    stops = sorted({s for s in (_num(x) for x in raw.get("stops") or [])
                    if s and s > 0}, reverse=True)

    cur = str(raw.get("cur") or "").strip().upper() or None

    # `stops_usd` la thu DUY NHAT duoc dem ra so voi bao gia. None = khong so
    # duoc (va `cmp_why` noi vi sao); [] = so duoc nhung khong co muc nao.
    # Tach hai truong thay vi doi gia tri cua `stops` de mot dong luon con lai
    # con so GOC bang don vi cua no - tin nhan phai hien duoc ca hai.
    stops_usd: list[float] | None = None
    cmp_why: str | None = None
    conv: dict | None = None
    if cur is None:
        cmp_why = "khong_biet_tien_te"
    elif cur == USD:
        stops_usd = list(stops)
    elif cur == EUR:
        if fx and fx.get("ok"):
            stops_usd = [s * fx["rate"] for s in stops]
            conv = {"rate": fx["rate"], "as_of": fx["as_of"], "cur": EUR}
        else:
            cmp_why = (fx or {}).get("why") or "tien_te"
    else:
        # MIXED, va bat ky don vi nao chua biet: khong doan.
        cmp_why = "tron_tien_te" if cur == MIXED else "tien_te"

    return {"sym": sym, "shares": shares, "avg_cost": _num(raw.get("avgCost")),
            "stops": stops, "cur": cur,
            "stops_usd": stops_usd, "cmp_why": cmp_why, "conv": conv,
            "with_stop": _num(raw.get("withStop")) or 0.0,
            "no_stop": _num(raw.get("noStop")) or 0.0,
            "accts": [str(a) for a in (raw.get("accts") or [])]}


def _iso_ms(ms) -> str | None:
    v = _num(ms)
    if not v:
        return None
    try:
        return dt.datetime.fromtimestamp(v / 1000, dt.UTC).isoformat(
            timespec="seconds").replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def parse(value, updated_ms=None, now: dt.datetime | None = None,
          g: dict | None = None) -> dict:
    """Ham THUAN: doi payload cua khoa thanh trang thai dung duoc.

    `value`      than cua khoa (`js["value"]` cua /api/scanner/kv/<key>)
    `updated_ms` dau moc cua CLOUDFLARE (`js["updatedAt"]`), don vi ms.
                 Uu tien no chu khong phai `value["ts"]`: `ts` do trinh duyet
                 ghi, va mot may tinh chay lech vai phut se cho ra mot tuoi am
                 hoac mot ban "luon con moi".

    Tra ve:
      known    co doc duoc mot ban hop le hay khong (n=0 VAN la known)
      n        so ma dang mo
      rows     {sym: dong}
      warn     canh bao tu phia ghi, nguyen van tieng Viet
      bad      so dong bi bo vi khong dung hinh dang
      ts       dau moc dang ISO (None neu khong co)
      age_h    tuoi tinh bang gio (None neu khong co dau moc)
      old      age_h vuot nguong bao - CHI de noi ra, xem docstring dau file
      fx       ty gia di kem: {rate, as_of, age_d, ok, why} | None
      note     mot cau tieng Viet di thang vao tin nhan mo phien
    """
    # `is None` chu khong phai `g or ...`: g={} co nghia la "khong co nguong
    # nao", va bien no thanh ca config mac dinh la kieu im lang khong ai doan ra.
    g = config.INTRADAY if g is None else g
    out: dict = {"known": False, "n": 0, "rows": {}, "warn": [], "bad": 0,
                 "ts": None, "age_h": None, "old": False, "fx": None,
                 "note": ""}
    # `rows` phai la mot LIST. Mot dict khong co khoa do, hoac co nhung la mot
    # chuoi, la mot hinh dang khac - khong phai "khong co vi the nao". Lay
    # `value.get("rows") or []` roi coi la rong chinh la cach bien mot thay doi
    # hinh dang thanh mot phien im lang.
    if not isinstance(value, dict) or not isinstance(value.get("rows"), list):
        out["note"] = ("chưa đọc được danh sách vị thế đang mở — sẽ KHÔNG coi "
                       "là không có vị thế nào")
        return out

    # Ty gia doc TRUOC cac dong: mot dong EUR chi so duoc neu co no.
    now = now or dt.datetime.now(dt.UTC)
    fx = _fx(value.get("fx"), now, _num(g.get("pos_fx_stale_d")))

    rows: dict[str, dict] = {}
    bad = 0
    for raw in value["rows"]:
        r = _row(raw, fx)
        if r is None:
            bad += 1
            continue
        # Trung ma sau khi chuan hoa chu hoa: gop bang cach giu ban co nhieu
        # muc cat lo hon. Phia ghi da gop san, day chi la luoi cuoi.
        cu = rows.get(r["sym"])
        if cu is None or len(r["stops"]) > len(cu["stops"]):
            rows[r["sym"]] = r

    ts_ms = _num(updated_ms)
    ts = _iso_ms(ts_ms) or (str(value.get("ts")) if value.get("ts") else None)
    age_h = None
    if ts_ms:
        # Toi da voi 0: dong ho lech ve tuong lai khong duoc bien thanh tuoi am.
        age_h = max(0.0, (now.timestamp() - ts_ms / 1000) / 3600)

    limit = float(g.get("pos_stale_h") or 0) or None
    out.update({"known": True, "n": len(rows), "rows": rows, "bad": bad,
                "ts": ts, "age_h": age_h, "fx": fx,
                "old": bool(limit and age_h is not None and age_h > limit),
                "warn": [str(w) for w in (value.get("warn") or [])]})

    if not rows:
        out["note"] = "không có vị thế nào đang mở"
    else:
        out["note"] = f"{len(rows)} vị thế đang mở: " + ", ".join(sorted(rows))
    if bad:
        out["note"] += f" · {bad} dòng không đọc được"
    if out["old"]:
        d = age_h / 24 if age_h else 0
        khi = f"{age_h:.0f} giờ trước" if d < 2 else f"{d:.0f} ngày trước"
        out["note"] += (f" · danh sách này app cập nhật lần cuối {khi}; nếu đã "
                        "mua/bán ở nơi khác thì mở app một lần cho nó đẩy lên")
    return out


def load(now: dt.datetime | None = None, g: dict | None = None,
         key: str = "scanner:positions") -> dict:
    """parse() + doc that tu cloud. Khong nem ra ngoai.

    push.get_full() tra None khi thieu SCANNER_PUSH_URL/SCANNER_TOKEN, khi mang
    chet, va khi khoa chua ton tai. Ca ba truong hop deu la KHONG BIET, va
    khong phan biet duoc chung o day cung khong sao: cach xu ly giong nhau.
    """
    try:
        import push
        js = push.get_full(key)
    except Exception as e:                       # noqa: BLE001
        d = parse(None, g=g)
        d["note"] = f"lỗi khi đọc danh sách vị thế ({type(e).__name__})"
        return d
    if js is None:
        return parse(None, g=g)
    return parse(js.get("value"), js.get("updatedAt"), now=now, g=g)


# ───────────────────────── dung de canh ─────────────────────────
def comparable(row: dict) -> str | None:
    """None = so sanh duoc voi bao gia USD. Chuoi = khoa ly do vi sao khong.

    Ket luan da duoc tinh o `_row()`, vi no phu thuoc vao ty gia di kem CA anh
    chup chu khong chi vao mot dong. Ham nay giu nguyen chu ky cu de ben goi
    (watch.py, render_watch.py) khong phai mang ty gia theo khap noi.
    """
    if row.get("stops_usd") is not None:
        return None
    return row.get("cmp_why") or "khong_biet_tien_te"


def stop_hit(row: dict, px) -> dict:
    """Gia hien tai da xuyen muc cat lo nao chua.

    Tra {"hit": muc cao nhat bi xuyen, TINH BANG USD | None,
         "hit_raw": chinh muc do bang don vi dang luu (= hit neu la USD),
         "cur": don vi dang luu, "conv": {rate, as_of, cur} | None,
         "n": so muc bi xuyen, "ok": co so sanh duoc khong,
         "why": khoa ly do khi khong}.

    `hit` la so DEM RA SO voi `px`, nen no luon la USD. `hit_raw` co de tin nhan
    hien lai con so nguoi dung that su da dat: ho khong dat mot muc bang USD, va
    mot tin nhan chi co so da quy doi thi doc nhu scanner nho sai muc cat lo.

    Bang nhau TINH LA xuyen: stop 90.00 va gia 90.00 la stop da cham. Lenh stop
    that o san giao dich cung kich o dung muc do.
    """
    out = {"hit": None, "hit_raw": None, "cur": row.get("cur"),
           "conv": row.get("conv"), "n": 0, "ok": False, "why": None}
    why = comparable(row)
    if why:
        out["why"] = why
        return out
    out["ok"] = True
    muc = row.get("stops_usd") or []
    if not muc:
        out["why"] = "khong_co_stop"
        return out
    p = _num(px)
    if p is None or p <= 0:
        out["ok"] = False
        out["why"] = "khong_co_gia"
        return out
    # `stops` va `stops_usd` cung thu tu va cung do dai (quy doi la mot phep
    # nhan voi so duong), nen chi so cua muc bi xuyen dung cho ca hai.
    xuyen = [i for i, s in enumerate(muc) if p <= s]
    out["n"] = len(xuyen)
    if xuyen:
        i = xuyen[0]                            # stops xep giam dan
        goc = row.get("stops") or []
        out["hit"] = muc[i]
        out["hit_raw"] = goc[i] if i < len(goc) else muc[i]
    return out


def fx_note(d: dict) -> str:
    """Mot cau tieng Viet ve ty gia, hoac "" neu khong co gi de noi.

    PHAI co trong tin mo phien khi co quy doi: mot muc cat lo hien ra bang USD
    ma nguoi dung dat bang EUR thi ho can biet con so do tu dau ra, khong thi ho
    se tuong la minh nho sai muc cat lo cua chinh minh.
    """
    fx = d.get("fx")
    if not fx:
        return ""
    r, ngay, tuoi = fx["rate"], fx["as_of"], fx.get("age_d") or 0
    if fx.get("ok"):
        return (f"quy đổi EUR→USD bằng tỷ giá {r:.4f} chốt ngày {ngay}"
                + (f", {tuoi:.0f} ngày trước" if tuoi >= 1 else ""))
    return (f"tỷ giá gửi kèm ({r:.4f} ngày {ngay}) đã cũ {tuoi:.0f} ngày nên "
            "KHÔNG dùng để quy đổi — mở app một lần cho nó đẩy tỷ giá mới")


def unchecked(d: dict) -> list[tuple[str, str]]:
    """Cac ma DANG MO ma scanner khong the canh stop, kem ly do tieng Viet.

    Danh sach nay phai di vao tin nhan mo phien. Do la cho khac biet giua "dung
    ngoai" va "im lang": mot ma khong canh duoc thi minh phai tu canh, va minh
    chi tu canh duoc neu biet.
    """
    ra = []
    for sym in sorted(d.get("rows") or {}):
        row = d["rows"][sym]
        why = comparable(row) or (None if row["stops"] else "khong_co_stop")
        if why:
            ra.append((sym, WHY_UNCHECKED[why].format(cur=row.get("cur") or "?")))
    return ra


def watched(d: dict) -> list[str]:
    """Cac ma co the canh stop that. Rong khi known=False."""
    return [s for s in sorted(d.get("rows") or {})
            if d["rows"][s]["stops"] and not comparable(d["rows"][s])]


# ───────────────────────── CLI ─────────────────────────
def _show(d: dict) -> int:
    if not d["known"]:
        print(f"KHONG BIET: {d['note']}")
        return 1
    print(f"VI THE DANG MO: {d['n']} ma · dau moc {d['ts'] or '-'}"
          + (f" ({d['age_h']:.1f} gio truoc)" if d["age_h"] is not None else "")
          + ("  [CU]" if d["old"] else ""))
    if d["rows"]:
        print(f"\n{'MA':<6}{'CP':>9}{'GIA VON':>10}{'CUR':>6}  MUC CAT LO")
        for sym in sorted(d["rows"]):
            r = d["rows"][sym]
            # Hien ca hai con so khi co quy doi: mot cot USD khong noi ro no la
            # so da quy doi thi doc nhu scanner dang nho sai muc cat lo.
            usd = ("  = " + ", ".join(f"{s:.2f}" for s in r["stops_usd"]) + " USD"
                   if r["conv"] and r["stops_usd"] else "")
            print(f"{sym:<6}{r['shares']:>9.0f}{r['avg_cost'] or 0:>10.2f}"
                  f"{r['cur'] or '?':>6}  "
                  + (", ".join(f"{s:.2f}" for s in r["stops"]) or "-") + usd)
    if (cau := fx_note(d)):
        print(f"  ty gia: {cau}")
    for sym, why in unchecked(d):
        print(f"  khong canh duoc {sym}: {why}")
    for w in d["warn"]:
        print(f"  ! {w}")
    print(f"\n{d['note']}")
    return 0


def _smoke() -> None:
    now = dt.datetime(2026, 9, 25, 16, 0, tzinfo=dt.UTC)
    ms = int(dt.datetime(2026, 9, 25, 15, 0, tzinfo=dt.UTC).timestamp() * 1000)
    val = {"ts": "2026-09-25T15:00:00.000Z", "n": 2, "warn": ["thử"],
           "rows": [{"sym": "nvda", "shares": 15, "avgCost": 106.7,
                     "cur": "USD", "stops": [90, 112], "withStop": 15,
                     "noStop": 0, "accts": ["A"]},
                    {"sym": "AAPL", "shares": 10, "avgCost": 185,
                     "cur": "EUR", "stops": [175], "withStop": 10,
                     "noStop": 0, "accts": ["A"]}]}
    d = parse(val, ms, now=now)
    assert d["known"] and d["n"] == 2 and d["bad"] == 0, d
    assert abs(d["age_h"] - 1.0) < 1e-6 and not d["old"], d
    assert d["rows"]["NVDA"]["stops"] == [112.0, 90.0], "phai xep giam dan"
    assert d["warn"] == ["thử"]

    # KHONG BIET != khong co vi the. Cai bay chinh cua ca file.
    for v in (None, "", [], {"rows": "x"}):
        k = parse(v, ms, now=now)
        assert k["known"] is False and k["n"] == 0, v
        assert "KHÔNG" in k["note"] or "chưa đọc được" in k["note"], k["note"]
    trong = parse({"rows": []}, ms, now=now)
    assert trong["known"] is True and trong["n"] == 0, trong
    assert "không có vị thế" in trong["note"]

    # Muc cao nhat bi xuyen truoc, va bang nhau la da xuyen.
    nvda = d["rows"]["NVDA"]
    assert stop_hit(nvda, 120)["hit"] is None
    assert stop_hit(nvda, 112)["hit"] == 112.0, "bang nhau tinh la xuyen"
    assert stop_hit(nvda, 100)["hit"] == 112.0
    h = stop_hit(nvda, 80)
    assert h["hit"] == 112.0 and h["n"] == 2, h

    # EUR ma anh chup KHONG gui ty gia: van dung ngoai, van phai noi ra.
    e = stop_hit(d["rows"]["AAPL"], 170)
    assert e["ok"] is False and e["hit"] is None and e["why"] == "tien_te", e
    uc = dict(unchecked(d))
    assert "AAPL" in uc and "EUR" in uc["AAPL"], uc
    assert watched(d) == ["NVDA"], watched(d)
    assert fx_note(d) == "", "khong co ty gia thi khong co gi de noi"

    # EUR co ty gia con moi: quy doi, va noi ra da quy doi bang gi.
    q = parse(dict(val, fx={"eurUsd": 1.2, "asOf": "2026-09-25"}), ms, now=now)
    a = q["rows"]["AAPL"]
    assert a["stops"] == [175.0], "con so goc phai giu nguyen"
    assert a["stops_usd"] == [210.0], a
    assert comparable(a) is None and watched(q) == ["AAPL", "NVDA"], watched(q)
    hh = stop_hit(a, 205)
    assert hh["ok"] and hh["hit"] == 210.0 and hh["hit_raw"] == 175.0, hh
    assert hh["conv"]["rate"] == 1.2 and hh["cur"] == "EUR", hh
    # 170 la gia da xuyen stop EUR neu so truc tiep - chinh cai loi cu. Bay gio
    # muc de so la 210, nen 170 van la da xuyen, nhung 209 cung the.
    assert stop_hit(a, 209)["hit"] == 210.0
    assert stop_hit(a, 211)["hit"] is None
    assert unchecked(q) == [], unchecked(q)
    assert "1.2000" in fx_note(q) and "2026-09-25" in fx_note(q), fx_note(q)

    # Ty gia qua cu: TU CHOI quy doi, va noi ro la vi ty gia chu khong vi EUR.
    cq = parse(dict(val, fx={"eurUsd": 1.2, "asOf": "2026-06-01"}), ms, now=now)
    ca = cq["rows"]["AAPL"]
    assert ca["stops_usd"] is None and comparable(ca) == "ty_gia_cu", ca
    assert stop_hit(ca, 205)["hit"] is None
    assert "tỷ giá" in dict(unchecked(cq))["AAPL"]
    assert "KHÔNG dùng để quy đổi" in fx_note(cq), fx_note(cq)

    # Ty gia khong phai ty gia, hoac khong biet cua ngay nao -> coi nhu khong co.
    for xau in ({"eurUsd": 0}, {"eurUsd": 232.5, "asOf": "2026-09-25"},
                {"eurUsd": 1.2}, {"eurUsd": 1.2, "asOf": "hom qua"}, 1.2, None):
        x = parse(dict(val, fx=xau), ms, now=now)
        assert x["fx"] is None, xau
        assert comparable(x["rows"]["AAPL"]) == "tien_te", xau

    # MIXED: ty gia khong giup duoc, va cau phai noi dung ly do do.
    mx = parse({"rows": [{"sym": "AAPL", "shares": 1, "cur": "MIXED",
                          "stops": [175]}],
                "fx": {"eurUsd": 1.2, "asOf": "2026-09-25"}}, ms, now=now)
    assert comparable(mx["rows"]["AAPL"]) == "tron_tien_te"
    assert "tách tài khoản" in dict(unchecked(mx))["AAPL"]

    # Thieu tien te -> cung dung ngoai, cung noi ra.
    m = parse({"rows": [{"sym": "X", "shares": 1, "stops": [5]}]}, ms, now=now)
    assert stop_hit(m["rows"]["X"], 4)["why"] == "khong_biet_tien_te"
    assert watched(m) == []

    # Khong co stop: so sanh duoc nhung khong co gi de so.
    n = parse({"rows": [{"sym": "Y", "shares": 1, "cur": "USD", "stops": []}]},
              ms, now=now)
    s = stop_hit(n["rows"]["Y"], 10)
    assert s["ok"] is True and s["hit"] is None and s["why"] == "khong_co_stop"
    assert dict(unchecked(n))["Y"] == WHY_UNCHECKED["khong_co_stop"]

    # Rac vao, khong chet ra.
    r = parse({"rows": [{"sym": "", "shares": 5}, {"sym": "Z", "shares": 0},
                        "x", {"sym": "W", "shares": 3, "cur": "USD",
                              "stops": [None, "abc", -1, 0, 7, 7]}]}, ms, now=now)
    assert r["bad"] == 3 and list(r["rows"]) == ["W"], r
    assert r["rows"]["W"]["stops"] == [7.0], r["rows"]["W"]

    # Tuoi: cu thi noi ra, KHONG vo hieu hoa.
    cu = parse(val, int(ms - 100 * 3600 * 1000), now=now)
    assert cu["known"] and cu["n"] == 2 and cu["old"], cu
    assert "cập nhật lần cuối" in cu["note"] and "ngày trước" in cu["note"]
    assert stop_hit(cu["rows"]["NVDA"], 100)["hit"] == 112.0, "cu van phai canh"
    # Dong ho lech ve tuong lai -> tuoi 0, khong phai so am.
    tl = parse(val, int(ms + 9 * 3600 * 1000), now=now)
    assert tl["age_h"] == 0.0 and not tl["old"], tl
    # Khong co dau moc cua Cloudflare -> dung `ts` cua trinh duyet de HIEN THI,
    # nhung khong tinh tuoi tu no.
    kh = parse(val, None, now=now)
    assert kh["known"] and kh["age_h"] is None and kh["ts"] == val["ts"], kh

    for k in WHY_UNCHECKED.values():
        assert k != k.encode("ascii", "ignore").decode(), "phai co dau tieng Viet"
    print("positions.py: smoke ok")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="in vi the dang mo")
    ap.add_argument("--from", dest="src", metavar="FILE",
                    help="doc tu file JSON thay vi tu cloud (khong can mang)")
    a = ap.parse_args()
    if a.src:
        js = json.loads(Path(a.src).read_text(encoding="utf-8"))
        # Nhan ca ban ghi day (co "value") va ban rut gon (chinh than khoa).
        return _show(parse(js.get("value", js), js.get("updatedAt")))
    if a.show:
        return _show(load())
    _smoke()
    return 0


if __name__ == "__main__":
    sys.exit(main())
