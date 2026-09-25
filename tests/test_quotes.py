"""quotes.py - bao gia trong phien, va do tre cua chinh no.

Bon bat bien, xep theo do IM LANG cua loi neu no vo:

1. MA VANG MAT PHAI HIEN RA. Mot ma nguon bo qua (halt, sai chinh ta, chua co
   nen nao) ma bien mat khoi khung se giong het mot ma "chua dat dieu kien". Ca
   phien khong ai canh cat lo cho no va khong co cach nao biet tu ben ngoai.
2. KHONG CO DAU MOC = KHONG DU MOI. Mac dinh phai la TU CHOI. Mot nguon tra gia
   ma khong tra thoi gian van "co gia", va neu coi la du moi thi bot canh bao
   theo mot con so khong biet cua luc nao.
3. `vol_ok` PHAI DI THEO BAO GIA. Nguon chi cho khoi luong mot san (feed IEX
   ~2% khoi luong) thi RVol tinh ra luon ~0.02 va nguong 1.5 khong bao gio kich
   - mot bo loc khong bao gio kich khong bao loi, no chi im lang.
4. NEN KHONG CO MUI GIO LA GIO ET, KHONG PHAI UTC. Doan sai la do tre lech 4-5
   gio, tuc la MOI bao gia bi coi la qua cu. Thieu bang mui gio thi lui ve UTC
   nhung PHAI noi ra (tz_warn).

Thuan stdlib: `build()` la ham thuan nen khong test nao o day goi mang. Duong ra
mang (`YFProvider`) chi duoc kiem o TRUONG HOP THAT BAI - va do cung la truong
hop quan trong hon.
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
from pathlib import Path

import _util

q = _util.need("quotes")

NOW = dt.datetime(2026, 9, 25, 14, 0, tzinfo=dt.UTC)      # 10:00 ET


def bar(ts: str, c: float, **kw) -> dict:
    d = {"sym": "NVDA", "ts": ts, "o": c, "h": c, "l": c, "c": c, "v": 1000.0}
    d.update(kw)
    return d


def one(*rows: dict, now=NOW, **kw) -> dict:
    return q.build(list(rows), now=now, **kw)


# ────────────── 1. ma vang mat ──────────────
def test_ma_hoi_ma_khong_co_du_lieu_thi_co_dong_err():
    """BAT BIEN 1."""
    f = q.missing(one(bar("2026-09-25T13:59:00+00:00", 100.0)), ["NVDA", "AAPL"])
    assert set(f) == {"NVDA", "AAPL"}
    assert f["AAPL"]["err"] and f["AAPL"]["px"] is None


def test_ma_vang_mat_khong_duoc_coi_la_co_khoi_luong_dung_duoc():
    """px None ma vol_ok True se lam tang tren tinh RVol tu mot so khong co."""
    f = q.missing({}, ["AAPL"])
    assert f["AAPL"]["vol_ok"] is False and f["AAPL"]["n_bars"] == 0


def test_khung_rong_van_lieu_ke_du_ma_da_hoi():
    p = q.FixtureProvider([])
    f = p.fetch(["NVDA", "AAPL"])
    assert sorted(f) == ["AAPL", "NVDA"] and all(x["err"] for x in f.values())


def test_loi_mang_thanh_mot_dong_err_moi_ma_chu_khong_phai_khung_rong():
    """Mot khung rong se duoc tang tren doc thanh "khong ma nao dat dieu kien".

    Thay `_rows_from_yf` chu khong goi that: tren CI co yfinance, va mot test goi
    mang la mot test that bai vi mang chu khong vi code - README muc 8.
    """
    goc = q._rows_from_yf
    q._rows_from_yf = lambda syms: ([], "mang hong")
    try:
        f = q.YFProvider().fetch(["NVDA", "AAPL"])
    finally:
        q._rows_from_yf = goc
    assert sorted(f) == ["AAPL", "NVDA"]
    for s in f:
        assert f[s]["err"] == "mang hong" and f[s]["px"] is None
        assert f[s]["vol_ok"] is False, "khong co du lieu thi khong duoc tinh RVol"


def test_yfprovider_khong_goi_mang_khi_khong_co_ma_nao():
    """Mot vong quet voi danh sach rong khong duoc phep ra mang: ngoai phien va
    trong che do stop_only khong co vi the nao, do la truong hop binh thuong."""
    goc = q._rows_from_yf

    def no(syms):
        raise AssertionError("khong duoc goi nguon khi khong co ma nao")

    q._rows_from_yf = no
    try:
        assert q.YFProvider().fetch([]) == {}
        assert q.YFProvider().fetch(["", None]) == {}
    finally:
        q._rows_from_yf = goc


# ────────────── 2. do moi ──────────────
def test_khong_co_dau_moc_thi_khong_du_moi():
    """BAT BIEN 2."""
    assert not q.fresh({"px": 10.0, "age_sec": None}, 10 ** 9)
    assert not q.fresh({}, 10 ** 9)
    assert not q.fresh(None, 10 ** 9)


def test_co_err_thi_khong_du_moi_du_co_gia():
    assert not q.fresh({"px": 10.0, "age_sec": 1.0, "err": "halt"}, 600)


def test_nguong_la_nguong_that_chu_khong_phai_trang_tri():
    f = one(bar("2026-09-25T13:30:00+00:00", 100.0))["NVDA"]
    assert f["age_sec"] == 30 * 60
    assert q.fresh(f, 31 * 60) and not q.fresh(f, 29 * 60)


def test_do_tre_khong_bao_gio_am():
    """Dong ho lech thi mot do tre am se lam moi kiem tra 'du moi' thanh vo
    dung - no se luon dung, ke ca voi mot dau moc cua ngay mai."""
    f = one(bar("2026-09-25T15:00:00+00:00", 100.0))["NVDA"]
    assert f["age_sec"] == 0.0


def test_do_tre_thanh_cau_tieng_viet():
    assert q.fmt_age(45) == "trễ 45 giây"
    assert q.fmt_age(16 * 60) == "trễ 16 phút"
    # Khong ro thi phai NOI la khong ro, khong duoc hien "trễ 0 phút".
    assert "không rõ" in q.fmt_age(None) and "không rõ" in q.fmt_age("x")


# ────────────── 3. khoi luong hop nhat ──────────────
def test_vol_ok_cua_nguon_di_theo_tung_bao_gia():
    """BAT BIEN 3."""
    f = one(bar("2026-09-25T13:59:00+00:00", 100.0), vol_ok=False)
    assert f["NVDA"]["vol_ok"] is False
    assert one(bar("2026-09-25T13:59:00+00:00", 100.0))["NVDA"]["vol_ok"] is True


def test_fixture_giu_duoc_co_vol_ok_de_mo_phong_feed_mot_san():
    p = q.FixtureProvider([{"now": NOW.isoformat(),
                            "rows": [bar("2026-09-25T13:59:00+00:00", 100.0)]}],
                          vol_ok=False)
    assert p.fetch(["NVDA"])["NVDA"]["vol_ok"] is False


def test_khoi_luong_la_TONG_cac_nen_chu_khong_phai_nen_cuoi():
    """RVol = khoi luong tu dau phien / ky vong. Lay nen cuoi thi tu so nho hon
    hang tram lan va nguong khong bao gio kich."""
    f = one(bar("2026-09-25T13:30:00+00:00", 100.0, v=1000),
            bar("2026-09-25T13:31:00+00:00", 101.0, v=2500))["NVDA"]
    assert f["vol"] == 3500.0


def test_thieu_khoi_luong_dem_la_0_chu_khong_lam_chet_phep_tong():
    f = one(bar("2026-09-25T13:30:00+00:00", 100.0, v=None),
            bar("2026-09-25T13:31:00+00:00", 101.0, v=500))["NVDA"]
    assert f["vol"] == 500.0


# ────────────── 4. mui gio ──────────────
def test_nen_khong_co_mui_gio_la_gio_ET_hoac_noi_ra_khi_khong_biet():
    """BAT BIEN 4. "09:30" cua yfinance la 09:30 ET = 13:30 UTC. Doc thanh
    13:30 UTC thi do tre lech 4 gio va moi bao gia deu 'qua cu'."""
    f = one(bar("2026-09-25T09:30:00", 100.0))["NVDA"]
    if q._et() is not None:
        assert f["ts"].endswith(("-04:00", "-05:00")), f["ts"]
        assert abs(f["age_sec"] - 30 * 60) < 1, f["age_sec"]
        assert q.tz_warn() == []
    else:
        assert f["ts"].endswith("+00:00")
        assert q.tz_warn(), "lui ve UTC thi phai co mot cau canh bao"


def test_tz_warn_la_cau_doc_duoc_chu_khong_phai_ma_loi():
    for s in q.tz_warn():
        assert "độ trễ" in s and len(s) > 20, s


def test_dau_moc_nhan_datetime_epoch_va_chuoi():
    t = dt.datetime(2026, 9, 25, 13, 30, tzinfo=dt.UTC)
    assert q._iso(t) == "2026-09-25T13:30:00+00:00"
    assert q._iso(t.timestamp()) == "2026-09-25T13:30:00+00:00"
    assert q._iso(t.timestamp() * 1000) == "2026-09-25T13:30:00+00:00", "milli-giay"
    assert q._iso("2026-09-25T13:30:00Z") == "2026-09-25T13:30:00+00:00"
    assert q._iso("") is None and q._iso(None) is None and q._iso("hom qua") is None


# ────────────── gop nen: thuan, va khong phu thuoc thu tu ──────────────
def test_gia_la_nen_moi_nhat_va_mo_cua_la_nen_som_nhat_du_gui_lon_xon():
    """yfinance tra ve theo NHOM MA khi hoi nhieu ma, khong theo thoi gian. Neu
    build() tin vao thu tu dau vao thi 'gia mo cua' co the la nen giua phien -
    va luat `trigger` dung dung gia mo cua de biet co phai gap hay khong."""
    f = one(bar("2026-09-25T13:31:00+00:00", 100.5, o=100.5),
            bar("2026-09-25T13:30:00+00:00", 99.8, o=99.5),
            bar("2026-09-25T13:32:00+00:00", 102.0, o=100.5))["NVDA"]
    assert f["px"] == 102.0 and f["open"] == 99.5
    assert f["ts"] == "2026-09-25T13:32:00+00:00" and f["n_bars"] == 3


def test_dinh_day_tu_ca_khung_chu_khong_phai_nen_cuoi():
    f = one(bar("2026-09-25T13:30:00+00:00", 100.0, h=105.0, l=95.0),
            bar("2026-09-25T13:31:00+00:00", 101.0, h=102.0, l=100.0))["NVDA"]
    assert f["hi"] == 105.0 and f["lo"] == 95.0


def test_thieu_dinh_day_thi_lui_ve_gia_dong_chu_khong_phai_None():
    f = one(bar("2026-09-25T13:30:00+00:00", 100.0, h=None, l=None))["NVDA"]
    assert f["hi"] == 100.0 and f["lo"] == 100.0


def test_ma_duoc_chuan_hoa_thanh_chu_hoa():
    f = one(bar("2026-09-25T13:30:00+00:00", 100.0, sym=" nvda "))
    assert list(f) == ["NVDA"]


def test_rac_bi_bo_dong_chu_khong_nem():
    """Mot dong xau khong duoc lam chet ca vong quet: 389 phut con lai cua phien
    dang phu thuoc vao no."""
    assert one({"sym": "", "c": 1}, {"sym": "A"}, {"sym": "A", "c": None},
               {"sym": "A", "ts": "2026-09-25T13:30:00Z", "c": 0}) == {}
    assert one("x", None, 42) == {}                            # type: ignore[arg-type]


def test_build_khong_doi_dau_vao():
    rows = [bar("2026-09-25T13:31:00+00:00", 101.0),
            bar("2026-09-25T13:30:00+00:00", 100.0)]
    truoc = json.dumps(rows, sort_keys=True)
    one(*rows)
    assert json.dumps(rows, sort_keys=True) == truoc, "ham thuan thi khong sort tai cho"


# ────────────── giao dien nguon ──────────────
def test_moi_nguon_deu_co_ten_va_co_vol_ok():
    """Ten nguon di vao moi tin nhan; thieu no thi khong doc duoc canh bao la cua
    nguon nao khi co hai nguon."""
    for p in (q.YFProvider(), q.FixtureProvider([])):
        assert isinstance(p.name, str) and p.name and p.name != "?"
        assert isinstance(p.vol_ok, bool)


def test_fixture_chay_het_khung_thi_giu_khung_cuoi():
    """Neu het khung ma quay ve rong thi hanh vi vong lap tu nhien doi o cuoi
    file fixture, va mot test dai se 'pass' vi mot ly do sai."""
    p = q.FixtureProvider([{"now": NOW.isoformat(),
                            "rows": [bar("2026-09-25T13:59:00+00:00", 100.0)]}])
    assert p.fetch(["NVDA"])["NVDA"]["px"] == 100.0
    assert p.fetch(["NVDA"])["NVDA"]["px"] == 100.0
    assert p.i == 2


def test_fixture_dung_moc_now_cua_chinh_khung():
    """Neu fixture dung Date.now() thi mot bao gia trong fixture se gia di theo
    thoi gian thuc va test se do sau vai thang."""
    p = q.FixtureProvider([{"now": "2026-09-25T14:00:00+00:00",
                            "rows": [bar("2026-09-25T13:30:00+00:00", 100.0)]}])
    assert p.fetch(["NVDA"])["NVDA"]["age_sec"] == 30 * 60


def test_get_provider_doc_QUOTE_SRC_va_tu_choi_ten_khong_biet():
    f = Path(tempfile.mkdtemp()) / "q.json"
    f.write_text(json.dumps({"frames": [{"now": NOW.isoformat(), "rows": [
        bar("2026-09-25T13:59:00+00:00", 100.0)]}], "vol_ok": False}),
        encoding="utf-8")
    p = q.get_provider(f"fixture:{f}")
    assert isinstance(p, q.FixtureProvider) and p.vol_ok is False
    assert p.fetch(["NVDA"])["NVDA"]["px"] == 100.0
    assert isinstance(q.get_provider("yf"), q.YFProvider)
    assert isinstance(q.get_provider(None), q.Provider)
    try:
        q.get_provider("bloomberg")
    except ValueError as e:
        assert "bloomberg" in str(e)
    else:
        raise AssertionError("nguon khong biet phai nem, khong duoc im lang lui ve yf")


if __name__ == "__main__":
    _util.main(globals())
