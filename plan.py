"""plan.py - Ke hoach lenh cho mot ma: vao o dau, stop o dau, co bao nhieu.

VI SAO CO FILE NAY
------------------
Bang `candidates` truoc day chi co `pivot`. Mot diem vao, khong stop, khong co
vi the. Tuc la cai goi la "ke hoach lap tu dem truoc" chua bao gio ton tai:
trong phien van phai tu nghi ra stop va tu doan nen mua bao nhieu - dung vao
luc khong nen phai nghi gi ca. Ba con so o day chinh la phan con thieu.

Toan bo tinh toan o day dung DUY NHAT so lieu cua NEN QUYET DINH (nen da dong
cua phien truoc). Khong co gia trong phien nao di vao day. Xem `decision_bar`
trong docstring cua make().

Tach khoi setups.py co chu dinh, giong cach structure.py (do) tach khoi
setups.py (xu):
  - setups.py tra loi "ma nay co dang chu y khong"
  - plan.py tra loi "neu chu y thi vao o dau, cat o dau, bao nhieu"
Va quan trong hon: phan intraday import duoc file nay ma khong keo theo
setups.py, vi trong phien no KHONG duoc phep tinh lai ke hoach - chi doc lai.

Thuan stdlib.
"""
from __future__ import annotations

import config


def _num(x) -> float | None:
    """So thuc duong, hoac None. Giong setups._num - lap lai de khong phai
    import setups.py tu day (xem docstring dau file)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def trigger(m: dict, g: dict | None = None) -> float | None:
    """Diem vao = max(pivot, dinh cua nen quyet dinh) x (1 + buf).

    `m` = mot dong cua bang `struct`. `m["hi1"]` la dinh cua NEN QUYET DINH,
    `m["pivot"]` la dinh cua nen tich luy (co the None: mot ma LEAD khong bat
    buoc phai co nen tich luy nao).

    Lay max chu khong lay pivot: neu gia da chay len tren pivot roi thi mua o
    pivot la mua o mot gia khong con ton tai. Va neu khong co pivot thi "vuot
    dinh hom qua" van la mot dinh nghia ro rang.
    """
    g = g or config.PLAN
    hi1, pv = _num(m.get("hi1")), _num(m.get("pivot"))
    ref = max(x for x in (hi1, pv, 0.0) if x is not None)
    if ref <= 0:
        return None
    return ref * (1.0 + float(g["trigger_buf"]))


def make(m: dict, size_mult: float = 1.0, g: dict | None = None) -> dict | None:
    """Ke hoach day du, hoac None neu khong du so lieu de lap.

    `m`         mot dong cua bang `struct` (can: hi1 hoac pivot, atr14, px)
    `size_mult` co vi the cua playbook theo trang thai thi truong: 1.0 full,
                0.5 half, 0.0 khong mo vi the moi. Do nightly.py truyen vao tu
                config.PLAYBOOK[(trend, vol)]["size"].

    Tra ve None chu khong tra ke hoach "gan dung": mot stop tinh tu ATR = None
    se thanh mot con so vo nghia ma trong phien khong con cach nao phat hien.

    `size_pct` DA NHAN size_mult. Tuc la size_mult = 0 cho ra size_pct = 0, va
    do la tin hieu may doc duoc cho "co ke hoach nhung hom nay khong vao" -
    dung no lam cong tac, dung doan qua viec thieu truong.
    """
    g = g or config.PLAN
    trg = trigger(m, g)
    atr = _num(m.get("atr14"))
    if trg is None or not atr or atr <= 0:
        return None

    stop = trg - float(g["stop_atr"]) * atr
    if stop <= 0:
        # ATR lon hon gia chia stop_atr: khong the xay ra voi mot ma da qua bo
        # san LEAD, nhung neu xay ra thi khong co ke hoach nao hop ly.
        return None

    # Khoang cach stop tinh theo % cua diem VAO, khong cua gia hien tai: rui ro
    # duoc do tu cho thuc su mua.
    risk_per_share = (trg - stop) / trg

    # size_pct = ngan sach rui ro / rui ro moi don vi. Stop rong hon -> vi the
    # nho hon, tu dong, khong phai quyet dinh tay.
    size = float(g["risk_pct"]) / risk_per_share
    size = min(size, float(g["max_pos_pct"])) * float(size_mult)

    return {
        "trigger": round(trg, 4),
        "stop": round(stop, 4),
        "target": round(trg + float(g["rr"]) * (trg - stop), 4),
        # Rui ro THUC SU chiu neu vao het co nay, tinh theo % von. Bang
        # risk_pct khi chua bi cap va nho hon khi da bi cap - nen no la con so
        # de doc, khong phai ban copy cua config.
        "risk_pct": round(risk_per_share * size, 6),
        "size_pct": round(size, 6),
        # Stop cach diem vao bao nhieu %. Di vao tin nhan de doc duoc ngay
        # "lenh nay stop rong hay chat" ma khong phai tu tinh.
        "stop_pct": round(risk_per_share, 6),
    }


def fmt(p: dict | None) -> str:
    """Mot dong nguoi doc duoc. CO DAU: no di vao Telegram va dashboard."""
    if not p:
        return "chưa lập được kế hoạch (thiếu ATR hoặc thiếu mốc giá)"
    if not p.get("size_pct"):
        return (f"vào {p['trigger']:.2f} · stop {p['stop']:.2f} "
                f"(−{p['stop_pct']:.1%}) · hôm nay KHÔNG mở vị thế mới")
    return (f"vào {p['trigger']:.2f} · stop {p['stop']:.2f} "
            f"(−{p['stop_pct']:.1%}) · mục tiêu {p['target']:.2f} · "
            f"cỡ {p['size_pct']:.1%} vốn (rủi ro {p['risk_pct']:.2%})")


def _smoke() -> None:
    import math

    g = config.PLAN

    # ATR 3 tren gia 100 = 3%. Stop = 1.5 x 3 = 4.5 duoi trigger.
    m = {"px": 100.0, "hi1": 101.0, "pivot": 99.0, "atr14": 3.0}
    p = make(m)
    assert p is not None
    assert abs(p["trigger"] - 101.0 * 1.001) < 1e-6, "trigger lay dinh nen quyet dinh"
    assert abs(p["stop"] - (p["trigger"] - 4.5)) < 1e-6
    assert abs(p["stop_pct"] - 4.5 / p["trigger"]) < 1e-6
    assert p["target"] > p["trigger"], "muc tieu phai tren diem vao"

    # Co vi the: rui ro 0.75% / stop ~4.45% = ~16.9% von, duoi tran 20%.
    want = g["risk_pct"] / p["stop_pct"]
    assert abs(p["size_pct"] - want) < 1e-6, f"co vi the tu ngan sach rui ro: {p}"
    assert p["size_pct"] < g["max_pos_pct"]
    # Rui ro thuc su phai bang ngan sach khi chua bi cap.
    assert abs(p["risk_pct"] - g["risk_pct"]) < 1e-6

    # Stop rat chat -> bi tran max_pos_pct chan, va luc do rui ro THUC SU nho
    # hon ngan sach. Day la cho de sai nhat ca file: neu cap ma van bao
    # risk_pct = 0.75% thi bao cao noi sai ve rui ro dang chiu.
    tight = make({"px": 100.0, "hi1": 100.0, "pivot": None, "atr14": 0.5})
    assert tight is not None
    assert abs(tight["size_pct"] - g["max_pos_pct"]) < 1e-9, "phai bi tran chan"
    assert tight["risk_pct"] < g["risk_pct"], "bi cap -> rui ro thuc su nho hon"

    # size_mult cua playbook nhan vao co, khong vao stop.
    half = make(m, size_mult=0.5)
    assert abs(half["size_pct"] - p["size_pct"] / 2) < 1e-9
    assert half["stop"] == p["stop"], "half size khong duoc doi stop"
    zero = make(m, size_mult=0.0)
    assert zero["size_pct"] == 0.0 and zero["trigger"] == p["trigger"], (
        "size 0 van phai co ke hoach: no la cong tac 'khong vao hom nay'")
    assert "KHÔNG mở vị thế mới" in fmt(zero)

    # Khong co pivot van lap duoc ke hoach (LEAD khong can nen tich luy).
    assert make({"hi1": 50.0, "atr14": 1.0}) is not None
    # Thieu ATR thi KHONG duoc doan.
    assert make({"hi1": 50.0, "atr14": None}) is None
    assert make({"hi1": 50.0, "atr14": 0.0}) is None
    assert make({"hi1": None, "pivot": None, "atr14": 1.0}) is None
    assert make({"hi1": float("nan"), "pivot": None, "atr14": 1.0}) is None

    # Gia nho hon stop_atr x ATR -> khong co ke hoach hop ly.
    assert make({"hi1": 2.0, "atr14": 2.0}) is None

    assert "thiếu ATR" in fmt(None)
    assert "vào" in fmt(p) and "cỡ" in fmt(p)
    assert not math.isnan(p["size_pct"])
    print("plan.py: smoke ok")


if __name__ == "__main__":
    _smoke()
