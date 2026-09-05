"""tgapi.py — duong gui Telegram duy nhat (Phase 4a).

Khong test phan mang (do la viec cua scripts/test_tg.py tren may that). Test
phan quyet dinh: khi Telegram tra ve cai gi thi send()/edit() lam gi. Day la
cho de sai nhat vi `_call` tra ve BON loai gia tri khac nhau, va
`isinstance(True, int)` la True nen thu tu kiem tra rat de nham.

`_call` bi thay bang ham gia -> khong goi mang.
"""
from __future__ import annotations

import asyncio

import _util

tg = _util.need("tgapi")


class Fake:
    """Thay _call. `answers` la danh sach gia tri tra ve, lan luot theo tung goi."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, method, body, timeout=20, bucket=False,
                       tries=tg.TRIES):
        self.calls.append((method, body))
        return self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]

    def texts(self) -> list[str]:
        return [b["text"] for _, b in self.calls]


def _run(coro_fn, fake):
    real, tg._call = tg._call, fake
    try:
        return asyncio.run(coro_fn())
    finally:
        tg._call = real


# ───────────────────────── send ─────────────────────────
def test_gui_duoc_tra_ve_message_id():
    f = Fake({"message_id": 77})
    assert _run(lambda: tg.send("xin chao"), f) == 77
    assert len(f.calls) == 1 and f.calls[0][0] == "sendMessage"


def test_loi_noi_dung_thi_ha_cap_html():
    """Telegram tu choi tag -> thu lai voi ban da bo tag, khong bo tin nhan."""
    f = Fake(tg.BAD_REQ, tg.BAD_REQ, {"message_id": 5})
    assert _run(lambda: tg.send("<blockquote expandable>x</blockquote>"), f) == 5
    assert len(f.calls) == 3
    assert f.texts()[0] != f.texts()[1], "moi lan phai la ban da ha cap"


def test_ha_cap_het_muc_van_that_bai_thi_bo():
    f = Fake(tg.BAD_REQ)
    assert _run(lambda: tg.send("<b>x</b>"), f) is None
    assert len(f.calls) == 4, "4 muc: 0,1,2,3 — roi dung"


def test_mat_mang_khong_ha_cap_html():
    """None = khong goi duoc. Bo tag khong cuu duoc mang -> dung ngay."""
    f = Fake(None)
    assert _run(lambda: tg.send("<b>x</b>"), f) is None
    assert len(f.calls) == 1


def test_tran_moi_phut_chi_ap_cho_sendMessage():
    """Nut bam phai phan hoi ngay, khong duoc cho het 60s cua tran sendMessage."""
    seen: dict[str, bool] = {}

    async def spy(m, body, timeout=20, bucket=False, tries=tg.TRIES):
        seen[m] = bucket
        return {"message_id": 1}

    async def go():
        await tg.send("x")
        await tg.edit(1, "x")
        await tg.edit_markup(1, {"inline_keyboard": []})
        await tg.answer_cb("cb")

    _run(go, spy)
    assert seen["sendMessage"] is True
    assert seen["editMessageText"] is False, "sua tin khong duoc bi xep hang"
    assert seen["editMessageReplyMarkup"] is False
    assert seen["answerCallbackQuery"] is False


def test_loud_bat_thong_bao():
    f = Fake({"message_id": 1})
    _run(lambda: tg.send("x", loud=True), f)
    assert f.calls[0][1]["disable_notification"] is False
    f2 = Fake({"message_id": 1})
    _run(lambda: tg.send("x"), f2)
    assert f2.calls[0][1]["disable_notification"] is True


def test_markup_duoc_gui_kem():
    kb = {"inline_keyboard": [[{"text": "a", "callback_data": "b"}]]}
    f = Fake({"message_id": 1})
    _run(lambda: tg.send("x", markup=kb), f)
    assert f.calls[0][1]["reply_markup"] == kb


def test_khong_markup_thi_khong_co_khoa():
    f = Fake({"message_id": 1})
    _run(lambda: tg.send("x"), f)
    assert "reply_markup" not in f.calls[0][1]


# ───────────────────────── _body ─────────────────────────
def test_cat_theo_gioi_han_cung_cua_telegram():
    """Vuot 4096 ky tu la Telegram tu choi CA tin nhan, khong phai cat ho."""
    f = Fake({"message_id": 1})
    _run(lambda: tg.send("x" * 9000), f)
    assert len(f.texts()[0]) == tg.MAX_LEN == 4096


def test_muc_0_khong_doi_noi_dung():
    assert tg._body("<b>x</b>", 0, None)["text"] == "<b>x</b>"


def test_ha_cap_dung_ham_cua_render():
    r = _util.need("render")
    txt = "<blockquote expandable>a</blockquote>"
    for lv in (1, 2, 3):
        assert tg._body(txt, lv, None)["text"] == r.degrade(txt, lv)


def test_body_luon_co_parse_mode_html():
    b = tg._body("x", 0, None)
    assert b["parse_mode"] == "HTML" and b["disable_web_page_preview"] is True
    assert b["chat_id"] == tg.CHAT


# ───────────────────────── edit ─────────────────────────
def test_sua_tin_thanh_cong():
    f = Fake({"message_id": 1})
    assert _run(lambda: tg.edit(9, "x"), f) is True
    assert f.calls[0][1]["message_id"] == 9


def test_khong_doi_gi_coi_nhu_xong():
    """'message is not modified' -> _call tra True. Khong phai loi."""
    f = Fake(True)
    assert _run(lambda: tg.edit(9, "x"), f) is True
    assert len(f.calls) == 1, "khong duoc ha cap HTML cho truong hop nay"


def test_sua_tin_loi_noi_dung_thi_ha_cap():
    f = Fake(tg.BAD_REQ, {"message_id": 1})
    assert _run(lambda: tg.edit(9, "<b>x</b>"), f) is True
    assert len(f.calls) == 2


def test_sua_tin_mat_mang_thi_bo():
    f = Fake(None)
    assert _run(lambda: tg.edit(9, "x"), f) is False
    assert len(f.calls) == 1


def test_sua_tin_khong_gui_thong_bao():
    """editMessageText khong nhan disable_notification — Telegram bao 400."""
    f = Fake({"message_id": 1})
    _run(lambda: tg.edit(9, "x"), f)
    assert "disable_notification" not in f.calls[0][1]


def test_sua_nut():
    f = Fake(True)
    assert _run(lambda: tg.edit_markup(9, {"inline_keyboard": []}), f) is True
    f2 = Fake(None)
    assert _run(lambda: tg.edit_markup(9, {"inline_keyboard": []}), f2) is False


def test_tra_loi_nut_khong_thu_lai():
    """Nut het hieu luc sau vai giay: thu lai chi lam nghen _lock."""
    seen = {}

    async def spy(m, body, timeout=20, bucket=False, tries=tg.TRIES):
        seen["tries"] = tries
        return None

    real, tg._call = tg._call, spy
    try:
        asyncio.run(tg.answer_cb("cb", "x"))
    finally:
        tg._call = real
    assert seen["tries"] == 1


def test_tra_loi_nut_cat_text():
    f = Fake(None)
    _run(lambda: tg.answer_cb("cb", "y" * 500), f)
    assert len(f.calls[0][1]["text"]) == 200


# ───────────────────────── hang so & giao uoc ─────────────────
def test_bad_req_khong_phai_bool_hay_so():
    """`isinstance(True, int)` la True. Neu BAD_REQ la so thi send() se nham no
    voi message_id, va tin nhan loi format se bi coi nhu da gui."""
    assert isinstance(tg.BAD_REQ, str)
    assert tg.BAD_REQ is not True and not isinstance(tg.BAD_REQ, (int, bool))


def test_tran_moi_phut_duoi_gioi_han_telegram():
    assert 0 < tg.PER_MIN <= 20, "Telegram: 20 tin/phut/nhom"
    assert tg.MIN_GAP >= 1.0


def test_ready_theo_env():
    assert tg.ready() == bool(tg.TOKEN and tg.CHAT)


def test_khong_con_duong_gui_thu_hai():
    """Phase 4a: notifier.py chi con la spool, moi thu goi API nam o day."""
    src = (_util.ROOT / "tgapi.py").read_text(encoding="utf-8")
    assert "retry_after" in src and "429" in src, "429 phai duoc xu ly o day"
    nf = (_util.ROOT / "notifier.py").read_text(encoding="utf-8")
    assert "api.telegram.org" not in nf


if __name__ == "__main__":
    _util.main(globals())
