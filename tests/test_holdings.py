"""holdings.py - thanh phan 11 sector doc tu CSV tinh.

Bon thu phai dung, xep theo muc do "sai thi khong ai phat hien ra":

1. MA GO SAI PHAI BI PHAT HIEN. "BRK.B" thay vi "BRK-B" khong sai dinh dang,
   khong nem ngoai le, khong bao gi ca - no chi vinh vien khong co nen, va bi
   loai im lang o Stage 3 mai mai. `check()` doi chieu voi kho nen chinh la de
   bat chuyen nay, va no la phan huu ich nhat cua module.
2. FILE CU PHAI BIET LA CU. Thanh phan sector doi vai lan mot nam. Danh sach
   "co phieu dan dat" dua tren ro cua nam ngoai trong khong khac gi danh sach
   dung - no chi sai.
3. MOT DONG XAU KHONG DUOC LAM SAP CA CHUOI CRON. load() tra ve `warn`, khong
   nem ngoai le: 08:00 tren VM thi mot dau phay thua khong duoc lam mat ca tin
   nhan.
4. "KHONG BIET" KHAC "KHONG THUOC". Ma trung o hai sector -> giu lan dau va
   CANH BAO, chu khong chon bua.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import _util

h, cf = _util.need("holdings", "config")


def _f(body: str, as_of: str | None = "2025-06-30", ten: str = "t.csv") -> Path:
    p = Path(tempfile.mkdtemp()) / ten
    return h._write(p, body, as_of=as_of)


# ───────────────────── 1. doi chieu kho nen bat ma go sai ─────────────────────
def test_ma_go_sai_bi_phat_hien_qua_kho_nen():
    b = _util.need("bars")
    db = Path(tempfile.mkdtemp()) / "t.db"
    c = b.con(db)
    b.save(c, {s: [("2024-01-02", 1.0, 1.0, 1.0, 1.0, 1.0, 100)]
               for s in ("AAPL", "MSFT")})
    r = h.check(_f("XLK,AAPL\nXLK,MSFT\nXLF,BRK.B\n"), db=c)
    assert r["khong_nen"] == ["BRK.B"], r["khong_nen"]
    assert any("khong co nen" in x for x in r["canh_bao"])
    assert not r["loi"], "ma go sai la CANH BAO, khong phai loi lam dung cron"
    # file dung hoan toan -> khong con canh bao ve kho nen
    r2 = h.check(_f("XLK,AAPL\nXLK,MSFT\n"), db=c, today="2025-07-01")
    assert r2["khong_nen"] == [] and r2["loi"] == []
    c.close()


def test_khong_co_kho_nen_thi_khong_doi_chieu_chu_khong_bao_sai():
    r = h.check(_f("XLK,AAPL\nXLF,BRK.B\n"), db=None)
    assert r["khong_nen"] == [], "khong co DB thi khong duoc ket luan gi"
    assert not r["loi"]


def test_file_thuc_trong_repo_dung_quy_uoc_yfinance():
    """Dau CHAM la loi im lang duy nhat khong the phat hien tu chinh file.

    yfinance dung BRK-B, con moi bang xep hang tren mang dung BRK.B. Copy tu
    web vao la co ngay 4-5 ma vinh vien khong co nen.
    """
    d = h.load()
    assert not d["err"], d["err"]
    sai = [s for s in d["by_sym"] if "." in s or " " in s or s != s.upper()]
    assert sai == [], sai


# ───────────────────── 2. tuoi cua file ─────────────────────
def test_qua_tran_tuoi_thi_canh_bao_kem_huong_dan():
    assert h.stale("2025-06-30", today="2025-07-01") is None
    tr = cf.HOLDINGS["max_age_days"]
    import datetime as dt
    bien = (dt.date.fromisoformat("2025-06-30")
            + dt.timedelta(days=tr)).isoformat()
    assert h.stale("2025-06-30", today=bien) is None, "dung tran thi chua cu"
    qua = (dt.date.fromisoformat(bien) + dt.timedelta(days=1)).isoformat()
    s = h.stale("2025-06-30", today=qua)
    assert s and "SPDR" in s and "README" in s, s


def test_thieu_as_of_la_canh_bao_chu_khong_phai_im_lang():
    d = h.load(_f("XLK,AAPL\n", as_of=None))
    assert d["as_of"] is None and not d["err"]
    assert any("as_of" in w for w in d["warn"])
    assert h.age_days(None) is None
    assert "không biết" in h.stale(None)
    # as_of sai dinh dang cung phai thanh None, khong duoc doan
    assert h._as_of(["# as_of=30/06/2025"]) is None
    assert h._as_of(["# as_of=2025-06-30"]) == "2025-06-30"


def test_as_of_chi_doc_trong_khoi_chu_thich_dau_file():
    assert h._as_of(["sector,sym", "# as_of=2025-06-30"]) is None


# ───────────────────── 3. dong xau khong lam sap gi ─────────────────────
def test_dong_xau_bi_bo_qua_va_duoc_dem_chu_khong_nem_ngoai_le():
    d = h.load(_f("SPY,AAPL\n,MSFT\nXLK,\nXLK,NVDA\n"))
    assert list(d["by_sym"]) == ["NVDA"], d["by_sym"]
    assert not d["err"], "mot dong xau khong duoc thanh loi ca file"
    assert len(d["warn"]) >= 4, d["warn"]
    assert any("SPY" in w for w in d["warn"]), "sector la phai duoc neu ten"


def test_file_hong_han_thi_moi_la_loi():
    assert h.load(Path(tempfile.mkdtemp()) / "khong_co.csv")["err"]
    assert h.load(_f(""))["err"], "khong doc duoc dong nao"
    sai = Path(tempfile.mkdtemp()) / "sai.csv"
    sai.write_text("# x\nma,nganh\nXLK,AAPL\n", encoding="utf-8")
    assert "sector,sym" in (h.load(sai)["err"] or "")


def test_sector_rong_thi_canh_bao_vi_stage3_se_khong_tim_duoc_gi():
    d = h.load(_f("XLK,AAPL\n"))
    assert d["by_sym"] == {"AAPL": "XLK"}
    assert any("khong co ma nao" in w for w in d["warn"]), d["warn"]
    # phai neu du 10 sector con lai
    w = [x for x in d["warn"] if "khong co ma nao" in x][0]
    assert "XLU" in w and "XLE" in w and "10 sector" in w


# ───────────────────── 4. "khong biet" khac "khong thuoc" ─────────────────────
def test_ma_trung_giu_lan_dau_va_canh_bao():
    d = h.load(_f("XLK,AAPL\nXLV,AAPL\nXLK,MSFT\n"))
    assert d["by_sym"]["AAPL"] == "XLK", "giu lan dau, khong ghi de"
    assert len(d["by_sym"]) == 2
    assert d["by_sector"]["XLK"] == ["AAPL", "MSFT"]
    assert "AAPL" not in d["by_sector"].get("XLV", [])
    assert any("AAPL" in w and "ban sao" in w for w in d["warn"]), d["warn"]


def test_allowed_chi_giu_top_sector():
    by = {"AAPL": "XLK", "XOM": "XLE", "PG": "XLP"}
    assert h.allowed(by, ("XLK", "XLE")) == {"AAPL": "XLK", "XOM": "XLE"}
    assert h.allowed(by, ()) == {}, "khong co top sector -> khong co ma nao"
    assert h.allowed({}, ("XLK",)) == {}
    assert h.allowed(by, ("XLK", "XLE")) is not by, "khong duoc sua dict goc"


# ───────────────────── file thuc trong repo ─────────────────────
def test_file_thuc_du_11_sector_va_moi_ma_mot_sector():
    d = h.load()
    assert set(d["by_sector"]) == set(cf.SECTOR_ETFS)
    assert len(d["by_sym"]) > 200, len(d["by_sym"])
    for s in cf.SECTOR_ETFS:
        assert len(d["by_sector"][s]) >= 15, (s, len(d["by_sector"][s]))
    # tong thanh phan == so ma: khong co ma nao o hai sector
    assert sum(len(v) for v in d["by_sector"].values()) == len(d["by_sym"])
    assert not d["warn"], d["warn"]


def test_file_thuc_phan_sector_theo_gics_hien_hanh():
    """Vai ma da bi doi sector. Sai thi Stage 3 tim dan dat trong ro sai.

    V/MA sang Financials (GICS 3/2023), TGT sang Consumer Staples (2023), META
    sang Communication Services (2018). Day la nhung cho de nham nhat.
    """
    by = h.load()["by_sym"]
    assert by["V"] == "XLF" and by["MA"] == "XLF"
    assert by["META"] == "XLC" and by["GOOGL"] == "XLC"
    assert by["AMZN"] == "XLY" and by["TSLA"] == "XLY"
    assert by["XOM"] == "XLE" and by["NEE"] == "XLU" and by["PG"] == "XLP"


def test_file_thuc_qua_check_khong_loi():
    r = h.check(today="2025-07-01")
    assert r["loi"] == [], r["loi"]


if __name__ == "__main__":
    _util.main(globals())
