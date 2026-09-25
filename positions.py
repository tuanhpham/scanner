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
  3. Gia nhap co the bang EUR trong khi bao gia la USD. File nay KHONG doi tien
     te va khong doan ty gia: mot muc cat lo lech 12% con te hon khong co muc
     nao, vi no tao ra long tin. Nhung ma nhu vay di vao `unchecked()` va phai
     duoc noi ra o tin nhan mo phien - dung ngoai NHUNG noi ra.

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

# Cau tieng Viet cho tung ly do khong canh duoc stop. Viet mot lan o day, va
# PHAI phu het cac khoa stop_hit() co the tra ve trong `why`: ben goi se tra cuu
# bang nay de viet tin nhan, va mot khoa thieu la mot KeyError giua phien.
WHY_UNCHECKED = {
    "tien_te": "giá nhập bằng {cur}, chưa quy đổi được sang USD",
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


def _row(raw: dict) -> dict | None:
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
    return {"sym": sym, "shares": shares, "avg_cost": _num(raw.get("avgCost")),
            "stops": stops, "cur": cur,
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
      note     mot cau tieng Viet di thang vao tin nhan mo phien
    """
    # `is None` chu khong phai `g or ...`: g={} co nghia la "khong co nguong
    # nao", va bien no thanh ca config mac dinh la kieu im lang khong ai doan ra.
    g = config.INTRADAY if g is None else g
    out: dict = {"known": False, "n": 0, "rows": {}, "warn": [], "bad": 0,
                 "ts": None, "age_h": None, "old": False, "note": ""}
    # `rows` phai la mot LIST. Mot dict khong co khoa do, hoac co nhung la mot
    # chuoi, la mot hinh dang khac - khong phai "khong co vi the nao". Lay
    # `value.get("rows") or []` roi coi la rong chinh la cach bien mot thay doi
    # hinh dang thanh mot phien im lang.
    if not isinstance(value, dict) or not isinstance(value.get("rows"), list):
        out["note"] = ("chưa đọc được danh sách vị thế đang mở — sẽ KHÔNG coi "
                       "là không có vị thế nào")
        return out

    rows: dict[str, dict] = {}
    bad = 0
    for raw in value["rows"]:
        r = _row(raw)
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
        now = now or dt.datetime.now(dt.UTC)
        # Toi da voi 0: dong ho lech ve tuong lai khong duoc bien thanh tuoi am.
        age_h = max(0.0, (now.timestamp() - ts_ms / 1000) / 3600)

    limit = float(g.get("pos_stale_h") or 0) or None
    out.update({"known": True, "n": len(rows), "rows": rows, "bad": bad,
                "ts": ts, "age_h": age_h,
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

    "MIXED" (cung mot ma co lo nhap bang EUR va lo bang USD) cung roi vao day:
    khong the chon mot trong hai ma khong doan.
    """
    cur = row.get("cur")
    if cur is None:
        return "khong_biet_tien_te"
    return None if cur == USD else "tien_te"


def stop_hit(row: dict, px) -> dict:
    """Gia hien tai da xuyen muc cat lo nao chua.

    Tra {"hit": muc cao nhat bi xuyen | None, "n": so muc bi xuyen,
         "ok": co so sanh duoc khong, "why": khoa ly do khi khong}.

    Bang nhau TINH LA xuyen: stop 90.00 va gia 90.00 la stop da cham. Lenh stop
    that o san giao dich cung kich o dung muc do.
    """
    out = {"hit": None, "n": 0, "ok": False, "why": None}
    why = comparable(row)
    if why:
        out["why"] = why
        return out
    out["ok"] = True
    if not row.get("stops"):
        out["why"] = "khong_co_stop"
        return out
    p = _num(px)
    if p is None or p <= 0:
        out["ok"] = False
        out["why"] = "khong_co_gia"
        return out
    xuyen = [s for s in row["stops"] if p <= s]
    out["n"] = len(xuyen)
    out["hit"] = xuyen[0] if xuyen else None    # stops xep giam dan
    return out


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
            print(f"{sym:<6}{r['shares']:>9.0f}{r['avg_cost'] or 0:>10.2f}"
                  f"{r['cur'] or '?':>6}  "
                  + (", ".join(f"{s:.2f}" for s in r["stops"]) or "-"))
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

    # EUR: khong doan ty gia, nhung phai noi ra.
    e = stop_hit(d["rows"]["AAPL"], 170)
    assert e["ok"] is False and e["hit"] is None and e["why"] == "tien_te", e
    uc = dict(unchecked(d))
    assert "AAPL" in uc and "EUR" in uc["AAPL"], uc
    assert watched(d) == ["NVDA"], watched(d)

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
