"""scorer.py — cho nay sai thi bot van chay, chi la canh bao SAI MA.

Hai loai loi khac nhau han nhau:

  1. Doi trong so / bo loc ma khong biet -> so lieu Phase 1 tich luy truoc va sau
     khong con so sanh duoc voi nhau. Test `test_diem_co_dinh` la CHOT: no fail
     nghia la "ban vua doi thang diem", phai co y thuc chu khong phai tinh cờ.
  2. Ly do bi loai doi chu -> log/bao cao mat dau vet, khong ai biet universe
     700 ma rot con 3 ma o buoc nao.

Khong test enrich_float()/load_baseline(): mot cai goi yfinance, mot cai doc DB
that. rank() co goi enrich_float nen test phai thay the no.
"""
from __future__ import annotations

import functools

import _util

sc, vp = _util.need("scorer", "vprofile")

# ───────────────────────── du lieu mau ─────────────────────────
# Chon so tron de diem tinh ra dung so nguyen le, khong phai "xap xi":
#   rvol = 2.0M / 200k = 10x    -> log10 = 1.0  -> 2.2 * 1.0 = 2.2
#   atr_move = |10-8| / 1.0 = 2 -> 2/2 = 1.0    -> 1.6 * 1.0 = 1.6
#   rot = 2.0M / 2.0M = 1.0     ->              -> 1.4 * 1.0 = 1.4
#   dvol = 10 * 2.0M = 20M      -> log10(20/2)=1 -> 0.5 * 1.0 = 0.5
FRAC = 1.0
U = {"sym": "AAA", "px": 10.0, "vol": 2_000_000, "chg": 0.25,
     "sources": ["finviz"], "fresh": 3}
B = {"adv20": 200_000, "atr14": 1.0, "prev_close": 8.0,
     "float_sh": 2_000_000, "cik": "0001941158"}
SCORE = 2.2 + 1.6 + 1.4 + 0.5          # = 5.7


class Clock:
    """SessionClock gia — chi 3 ham ma scorer goi."""

    def __init__(self, state="LIVE", mso=390, smin=390):
        self._s, self._m, self._n = state, mso, smin

    def state(self):
        return self._s

    def mso(self):
        return self._m

    def session_minutes(self):
        return self._n


class BadClock(Clock):
    def mso(self):
        raise RuntimeError("dong ho hong")


def _no_float_fetch(fn):
    """rank() goi enrich_float -> yfinance + DB that. Thay bang ham rong."""
    @functools.wraps(fn)
    def wrap():
        real, sc.enrich_float = sc.enrich_float, lambda syms, base: 0
        try:
            return fn()
        finally:
            sc.enrich_float = real
    return wrap


# ───────────────────────── 1. thang diem (chot) ─────────────────────────
def test_diem_co_dinh():
    """Fail o day = thang diem da doi. Xem mục 11 README truoc khi sua so."""
    assert (sc.W_RVOL, sc.W_ATR, sc.W_ROT, sc.W_DV, sc.W_FRESH) == \
        (2.2, 1.6, 1.4, 0.5, 1.5)
    assert (sc.CAP_RVOL, sc.MIN_PX, sc.MIN_CHG, sc.MIN_DOLLAR_VOL,
            sc.MIN_RVOL, sc.SPLIT_DIVERGE) == \
        (2.0, 1.0, 0.05, 2_000_000, 3.0, 0.25)
    d = sc.score_one(U, B, FRAC)
    assert abs(d["score"] - SCORE) < 1e-9, d
    assert d["rvol"] == 10.0 and d["atr_move"] == 2.0 and d["float_rot"] == 1.0
    assert d["dollar_vol"] == 20_000_000 and d["diverge"] == 0.0


def test_moi_thanh_phan_co_the_tat_rieng():
    """Bo tung dau vao -> diem giam dung phan cua thanh phan do."""
    for kw, mat in (({"adv20": 0}, 2.2),         # khong co adv20 -> rvol = 0
                    ({"atr14": 0}, 1.6),
                    ({"float_sh": 0}, 1.4)):
        d = sc.score_one(U, {**B, **kw}, FRAC)
        assert abs(d["score"] - (SCORE - mat)) < 1e-9, (kw, d["score"])
    # px * vol <= MIN_DOLLAR_VOL -> mat W_DV, nhung cung keo rot/rvol theo,
    # nen chi kiem tra rang muc do la khong con trong explain.
    d = sc.score_one({**U, "vol": 100}, B, FRAC)
    assert "USD" not in d["explain"], d["explain"]


def test_nguong_bat_thanh_phan_la_lon_hon_khong_phai_bang():
    """Dung o dung nguong thi thanh phan chua duoc tinh — tranh cong 0.0 vo ich."""
    # rv = 1.0 chan (adv20 = vol) -> rv > 1 la False
    assert "RVOL" not in sc.score_one(
        {**U, "vol": 200_000}, {**B, "adv20": 200_000}, FRAC)["explain"]
    # atr_move = 0.2 chan
    assert "ATR" not in sc.score_one(
        U, {**B, "atr14": 10.0}, FRAC)["explain"]
    # rot = 0.05 chan
    assert "vòng" not in sc.score_one(
        U, {**B, "float_sh": 40_000_000}, FRAC)["explain"]
    # dvol = MIN_DOLLAR_VOL chan
    assert "USD" not in sc.score_one(
        {**U, "px": 1.0, "vol": 2_000_000}, B, FRAC)["explain"]


def test_tran_cua_tung_thanh_phan():
    """RVOL 100x va 10.000x phai cho cung diem — khong de mot ma an ca thang."""
    a = sc.score_one({**U, "vol": 20_000_000}, B, FRAC)    # rvol 100x
    b = sc.score_one({**U, "vol": 2_000_000_000}, B, FRAC)  # rvol 10.000x
    assert a["rvol"] == 100.0 and b["rvol"] == 10_000.0
    assert sc.W_RVOL * sc.CAP_RVOL == 4.4
    # Chi so sanh phan RVOL: cac thanh phan khac cung doi theo vol.
    for d in (a, b):
        assert "(+4.4)" in d["explain"], d["explain"]
    # ATR: tran o atr_move = 6 (min(x/2, 3))
    hi = sc.score_one({**U, "px": 100.0}, B, FRAC)["explain"]
    assert f"(+{sc.W_ATR * 3.0:.1f})" in hi, hi
    # rot: tran o 3.0
    hi = sc.score_one(U, {**B, "float_sh": 100_000}, FRAC)["explain"]
    assert f"(+{sc.W_ROT * 3.0:.1f})" in hi, hi


def test_thuong_moi_chi_khi_CHI_co_alpaca():
    """W_FRESH tra cho 'nguon khac chua kip thay' — co nguon cham la het y nghia."""
    for src, want in ((["alpaca_mover"], True),
                      (["alpaca_mover", "alpaca_active"], True),
                      (["alpaca_active", "finviz"], False),
                      (["finviz"], False),
                      ([], False),          # rong -> khong thuong (u.get() falsy)
                      (None, False)):
        d = sc.score_one({**U, "sources": src}, B, FRAC)
        got = abs(d["score"] - (SCORE + sc.W_FRESH)) < 1e-9
        assert got is want, (src, d["score"])


def test_sources_duoc_sap_xep():
    d = sc.score_one({**U, "sources": ["finviz", "alpaca_mover"]}, B, FRAC)
    assert d["sources"] == ["alpaca_mover", "finviz"]


def test_freshness_theo_so_nguon():
    assert sc.score_one({**U, "fresh": 3}, B, FRAC)["freshness"] == "REALTIME"
    assert sc.score_one({**U, "fresh": 2}, B, FRAC)["freshness"] == "~15min"
    assert sc.score_one({k: v for k, v in U.items() if k != "fresh"},
                        B, FRAC)["freshness"] == "~15min"


def test_explain_dung_dau_phan_cach_render_cho():
    """render.render_why() tach tung dong bang ' · '. Doi day la mat xuong dong."""
    d = sc.score_one(U, B, FRAC)
    assert d["explain"].count(" · ") == 3, d["explain"]
    assert len(d["explain"].split(" · ")) == 4


def test_thieu_het_du_lieu_khong_nem_loi():
    for u, b in (({"sym": "A"}, {}),
                 ({"sym": "A", "px": None, "vol": None, "chg": None}, {}),
                 ({"sym": "A", "px": 0, "vol": 0}, {"adv20": 0, "atr14": 0}),
                 ({"sym": "A", "px": 5.0, "vol": 1000},
                  {"adv20": None, "atr14": None, "prev_close": None})):
        d = sc.score_one(u, b, FRAC)
        assert d["score"] == 0.0 and d["explain"] == "" and d["sym"] == "A"


def test_frac_nho_khong_chia_cho_khong():
    """frac = 0 (ngay khi mo cua) -> rvol_at ket vao FLOOR, khong ZeroDivision."""
    d = sc.score_one(U, B, 0.0)
    assert d["rvol"] == round(2_000_000 / (200_000 * vp.FLOOR), 2)


# ───────────────────────── 2. _chg ─────────────────────────
def test_chg_uu_tien_nguon():
    """px va chg cua cung mot nguon luon nhat quan; prev_close la nguon khac."""
    chg, dv = sc._chg({"px": 10.0, "chg": 0.30}, {"prev_close": 8.0})
    assert chg == 0.30, "khong duoc tra ve chg tu tinh khi nguon da co"
    assert abs(dv - 0.05) < 1e-9          # |0.25 - 0.30|
    assert sc._chg({"px": 10.0, "chg": 0.25}, {"prev_close": 8.0})[1] == 0.0


def test_chg_thieu_nguon_thi_tu_tinh():
    chg, dv = sc._chg({"px": 10.0}, {"prev_close": 8.0})
    assert abs(chg - 0.25) < 1e-9 and dv == 0.0


def test_chg_thieu_ca_hai_thi_none():
    assert sc._chg({}, {}) == (None, 0.0)
    assert sc._chg({"px": 10.0}, {}) == (None, 0.0)
    assert sc._chg({}, {"prev_close": 8.0}) == (None, 0.0)
    # Co chg nhung khong co prev_close -> khong the doi chieu -> lech 0.
    assert sc._chg({"chg": 0.4}, {}) == (0.4, 0.0)


def test_chg_split_lech_lon():
    """Gop 1:10 -> prev_close chua cap nhat -> calc phong dai gap 10."""
    _, dv = sc._chg({"px": 10.0, "chg": 0.05}, {"prev_close": 1.0})
    assert dv > sc.SPLIT_DIVERGE


# ───────────────────────── 3. rank: ly do bi loai ─────────────────────────
@_no_float_fetch
def test_rank_ma_dat_chuan_thi_qua():
    out, rej = sc.rank({"AAA": U}, {"AAA": B}, Clock())
    assert [d["sym"] for d in out] == ["AAA"]
    assert rej["_qua_loc"] == 1 and rej["_state"] == "LIVE"
    assert rej["_frac"] == 1.0 and rej["_float_moi_lay"] == 0
    assert not [k for k in rej if not k.startswith("_")], rej


@_no_float_fetch
def test_rank_tung_ly_do_loai_dung_chu():
    """Cac chuoi nay di vao log/bao cao — doi chu la mat dau vet cua ca ngay."""
    cases = [
        ("khong co baseline (ETF/moi/kem thanh khoan)", U, None),
        ("gia < $1", {**U, "px": 0.99}, B),
        ("tang < 5%", {**U, "chg": 0.04}, B),
        ("tang < 5%", {**U, "chg": None, "px": 10.0}, {**B, "prev_close": 0}),
        ("thieu volume", {**U, "vol": 0}, B),
        ("thanh khoan < $2M", {**U, "vol": 100}, B),
        (f"rvol < {sc.MIN_RVOL}", U, {**B, "adv20": 10_000_000}),
        ("nghi ngo gop/chia co phieu (chg lech nguon)",
         {**U, "chg": 0.05}, {**B, "prev_close": 1.0}),
    ]
    for reason, u, b in cases:
        out, rej = sc.rank({"AAA": u}, {} if b is None else {"AAA": b}, Clock())
        assert out == [], (reason, out)
        assert rej.get(reason) == 1, (reason, rej)


@_no_float_fetch
def test_rank_dem_don_ly_do():
    base = {f"S{i}": B for i in range(3)}
    uni = {f"S{i}": {**U, "sym": f"S{i}", "px": 0.5} for i in range(3)}
    _, rej = sc.rank(uni, base, Clock())
    assert rej["gia < $1"] == 3


@_no_float_fetch
def test_nghi_split_chi_chan_trong_phien():
    """Ngoai phien, chg va prev_close tu hai moc thoi gian khac nhau la binh
    thuong — chan o day se giet sach universe moi buoi sang."""
    u = {**U, "chg": 0.05}
    b = {**B, "prev_close": 1.0}
    _, rej = sc.rank({"AAA": u}, {"AAA": b}, Clock("LIVE"))
    assert rej.get("nghi ngo gop/chia co phieu (chg lech nguon)") == 1
    out, rej = sc.rank({"AAA": u}, {"AAA": b}, Clock("CLOSED"))
    assert [d["sym"] for d in out] == ["AAA"]
    assert "nghi ngo gop/chia co phieu (chg lech nguon)" not in rej


@_no_float_fetch
def test_rank_sap_xep_giam_dan():
    base = {"LO": B, "HI": B}
    uni = {"LO": {**U, "sym": "LO"},
           "HI": {**U, "sym": "HI", "vol": 20_000_000}}
    out, _ = sc.rank(uni, base, Clock())
    assert [d["sym"] for d in out] == ["HI", "LO"]
    assert out[0]["score"] > out[1]["score"]


@_no_float_fetch
def test_rank_universe_rong():
    out, rej = sc.rank({}, {}, Clock())
    assert out == [] and rej["_qua_loc"] == 0


@_no_float_fetch
def test_rank_premarket_dung_frac_nho():
    """Premarket: 3% khoi luong ca ngay -> rvol khong bi dim di 30 lan."""
    _, rej = sc.rank({}, {}, Clock("PREMARKET"))
    assert rej["_frac"] == round(vp.PREMKT_FRAC, 4)


# ───────────────────────── 4. score_sym ─────────────────────────
def test_score_sym_bo_qua_moi_bo_loc():
    """Nut Cap nhat phai xem duoc ma da nguoi: 'tut tu 9.8 xuong 4.1' la tin."""
    d = sc.score_sym("AAA", {"px": 0.20, "vol": 100, "chg": -0.60},
                     {"AAA": B}, Clock())
    assert d is not None and d["sym"] == "AAA" and d["px"] == 0.20


def test_score_sym_khong_co_baseline():
    assert sc.score_sym("XXX", U, {"AAA": B}, Clock()) is None
    assert sc.score_sym("AAA", U, {}, Clock()) is None


def test_score_sym_ghi_de_sym():
    d = sc.score_sym("AAA", {**U, "sym": "SAI"}, {"AAA": B}, Clock())
    assert d["sym"] == "AAA"


def test_score_sym_dong_ho_hong_van_cham_duoc():
    """clock nem loi -> frac = 1.0, van tra ve diem chu khong nem len main.py."""
    d = sc.score_sym("AAA", U, {"AAA": B}, BadClock())
    assert d is not None and abs(d["score"] - SCORE) < 1e-9


# ───────────────────────── 5. giao uoc voi render.py ─────────────────────────
def test_nguong_alert_khop_voi_render():
    r = _util.need("render")
    assert sc.ALERT_SCORE == 7.0
    assert sc.ALERT_SCORE < r.T_STRONG < r.T_EXTREME, \
        "muc 1/2/3 phai tang dan, neu khong alert nao cung ra muc cao nhat"


if __name__ == "__main__":
    _util.main(globals())
