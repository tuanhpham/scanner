"""notifier.Spool — hang doi tren dia cho luc mat mang.

Cai phai dung tuyet doi: KHONG mat tin va KHONG doi thu tu. Spool la thu duy
nhat con lai khi Telegram khong voi tay den duoc, nen mot con bug o day nghia la
mat alert han.

Test dung ham gui gia (Spool.flush nhan `send` tu ngoai) -> khong can httpx,
khong goi mang, chay duoc tren may dev tran.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import _util

nf = _util.need("notifier")


def _p(td: str) -> Path:
    return Path(td) / "spool.json"


def _sender(fail_after: int | None = None):
    """Tra ve (ham_gui, danh_sach_da_gui). fail_after=n -> tin thu n+1 tro di loi."""
    got: list[tuple[str, bool]] = []

    async def send(text, loud):
        if fail_after is not None and len(got) >= fail_after:
            return False
        got.append((text, loud))
        return True

    return send, got


def _flush(s, send, now=None):
    return asyncio.run(s.flush(send, now=now))


# ───────────────────────── xep hang ─────────────────────────
def test_them_va_dem():
    with tempfile.TemporaryDirectory() as td:
        s = nf.Spool(_p(td))
        assert len(s) == 0
        assert s.add("mot") and s.add("hai", loud=True)
        assert len(s) == 2


def test_bo_text_rong():
    with tempfile.TemporaryDirectory() as td:
        s = nf.Spool(_p(td))
        assert not s.add("") and not s.add(None)
        assert len(s) == 0


def test_giu_qua_restart():
    """VM restart giua luc mat mang: hang doi trong RAM mat, tren dia thi khong."""
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)
        nf.Spool(p).add("alert AAA", loud=True)
        s2 = nf.Spool(p)
        assert len(s2) == 1
        assert s2.items[0]["text"] == "alert AAA" and s2.items[0]["loud"] is True


def test_file_hong_khong_lam_chet_bot():
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)
        for junk in ("khong phai json", "{}", '"chuoi"', '[1, 2, 3]',
                     '[{"khong_co_text": 1}]'):
            p.write_text(junk, encoding="utf-8")
            assert nf.Spool(p).items == [], junk


def test_khong_co_file_thi_rong():
    with tempfile.TemporaryDirectory() as td:
        assert len(nf.Spool(Path(td) / "chua-co.json")) == 0


def test_khong_ghi_duoc_thi_im_lang():
    """Dia full / duong dan sai: add() van phai chay, chi mat phan ben bi."""
    s = nf.Spool("/:/khong-the-ghi/spool.json")
    assert s.add("alert AAA")
    assert len(s) == 1


def test_giu_dau_tieng_viet():
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)
        nf.Spool(p).add("biên độ 3× ATR")
        assert "biên độ" in p.read_text(encoding="utf-8")
        assert nf.Spool(p).items[0]["text"] == "biên độ 3× ATR"


def test_tran_bo_tin_cu_nhat():
    """Mat mang ca gio: tin 2 tieng truoc vo nghia, tin vua roi thi khong."""
    with tempfile.TemporaryDirectory() as td:
        s = nf.Spool(_p(td))
        for i in range(nf.MAX_ITEMS + 3):
            s.add(f"t{i}")
        assert len(s) == nf.MAX_ITEMS
        assert s.items[0]["text"] == "t3"
        assert s.items[-1]["text"] == f"t{nf.MAX_ITEMS + 2}"


# ───────────────────────── gui bu ─────────────────────────
def test_gui_bu_dung_thu_tu():
    with tempfile.TemporaryDirectory() as td:
        s = nf.Spool(_p(td))
        for i in range(3):
            s.add(f"t{i}")
        send, got = _sender()
        assert _flush(s, send) == 3
        assert [t for t, _ in got] == ["t0", "t1", "t2"]
        assert len(s) == 0 and len(nf.Spool(_p(td))) == 0


def test_hang_rong_khong_goi_gi():
    with tempfile.TemporaryDirectory() as td:
        send, got = _sender()
        assert _flush(nf.Spool(_p(td)), send) == 0
        assert got == []


def test_that_bai_khong_mat_tin():
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)
        s = nf.Spool(p)
        s.add("t0")
        s.add("t1")
        send, got = _sender(fail_after=0)
        assert _flush(s, send) == 0
        assert len(s) == 2 and len(nf.Spool(p)) == 2, "phai con nguyen tren dia"


def test_dung_ngay_khi_loi_va_giu_thu_tu():
    """Loi giua duong: tin sau KHONG duoc gui vuot len truoc tin loi."""
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)
        s = nf.Spool(p)
        for i in range(4):
            s.add(f"t{i}")
        send, got = _sender(fail_after=2)
        assert _flush(s, send) == 2
        assert [t for t, _ in got] == ["t0", "t1"]
        assert [it["text"] for it in nf.Spool(p).items] == ["t2", "t3"]


def test_gui_bu_lan_hai_tiep_tuc_dung_cho():
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)
        s = nf.Spool(p)
        for i in range(3):
            s.add(f"t{i}")
        assert _flush(s, _sender(fail_after=1)[0]) == 1
        send, got = _sender()
        assert _flush(nf.Spool(p), send) == 2
        assert [t for t, _ in got] == ["t1", "t2"]


def test_tin_tre_co_nhan_tre():
    with tempfile.TemporaryDirectory() as td:
        s = nf.Spool(_p(td))
        s.add("alert AAA", ts=0)
        send, got = _sender()
        _flush(s, send, now=nf.LATE_MIN * 60 + 30)
        txt = got[0][0]
        assert txt.startswith(nf.PREFIX.format(m=nf.LATE_MIN))
        assert "alert AAA" in txt


def test_tin_moi_khong_co_nhan_tre():
    with tempfile.TemporaryDirectory() as td:
        s = nf.Spool(_p(td))
        s.add("alert AAA", ts=0)
        send, got = _sender()
        _flush(s, send, now=30)
        assert got[0][0] == "alert AAA"


def test_gui_bu_khong_reo():
    """Tin cu khong duoc danh thuc nguoi doc, du luc xep hang la loud."""
    with tempfile.TemporaryDirectory() as td:
        s = nf.Spool(_p(td))
        s.add("alert AAA", loud=True, ts=0)
        send, got = _sender()
        _flush(s, send, now=0)
        assert got[0][1] is False


def test_thieu_ts_khong_nem_loi():
    """File spool cu (hoac bi sua tay) co the khong co khoa ts."""
    with tempfile.TemporaryDirectory() as td:
        p = _p(td)
        p.write_text(json.dumps([{"text": "t0", "loud": False}]),
                     encoding="utf-8")
        send, got = _sender()
        assert _flush(nf.Spool(p), send, now=1_000_000) == 1
        assert got[0][0] == "t0", "khong co ts -> coi nhu tin moi, khong nhan tre"


# ───────────────────────── giao uoc voi main.py / tgapi ─────────────────
def test_khong_con_phan_gui_nao_trong_notifier():
    """Phase 4a: tgapi.py la duong gui DUY NHAT. notifier chi giu tin tren dia."""
    src = (_util.ROOT / "notifier.py").read_text(encoding="utf-8")
    for bad in ("import httpx", "sendMessage", "retry_after", "html.escape",
                "class Notifier", "def esc", "def from_env"):
        assert bad not in src, f"{bad} phai da di sang tgapi.py"


def test_main_dung_spool_chu_khong_dung_notifier_cu():
    src = (_util.ROOT / "main.py").read_text(encoding="utf-8")
    assert "notifier.Spool(" in src
    for bad in ("n.worker()", "n.q.join()", "notif_mod", "from_env()",
                "from notifier import esc"):
        assert bad not in src, f"main.py con dung {bad}"


def test_esc_chi_con_mot_ban():
    """esc() tung co ca trong render.py va notifier.py — hai ban de lech nhau."""
    r = _util.need("render")
    assert not hasattr(nf, "esc")
    assert r.esc("<b>&") == "&lt;b&gt;&amp;"


def test_smoke_cua_module_chay_duoc():
    nf._smoke()


if __name__ == "__main__":
    _util.main(globals())
