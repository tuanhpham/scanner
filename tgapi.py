"""tgapi.py — DUONG GUI TELEGRAM DUY NHAT.

Truoc day co hai duong song song (file nay va `notifier.py`), moi duong co ban
sao rieng cua rate-limit, xu ly 429 va ha cap HTML — va hai ban da bat dau lech
nhau. Gio tat ca goi API nam o day; `notifier.py` chi con la hang doi tren dia
cho luc mat mang (xem mục 11 README, Phase 4a).

Ba lop bao ve, tung lop cho mot kieu that bai khac nhau:

  1. Nhip goi   — MIN_GAP giay giua hai lan goi + tran PER_MIN tin/phut.
                  Chi ap cho sendMessage: editMessageText va answerCallbackQuery
                  phai di ngay, khong thi nguoi bam nut cho ca phut.
  2. Thu lai    — mat mang / 429 thi thu lai (429 cho dung `retry_after`).
                  Loi 4xx khac thi KHONG thu lai: gui lai cung the thoi.
  3. Ha cap HTML— Telegram tu choi tag -> bo expandable -> bo blockquote ->
                  bo het tag. Tin nhan xau con hon khong co tin nhan.

Doc TG_TOKEN / TG_CHAT_ID tu .env theo duong dan tuyet doi.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

TOKEN = os.getenv("TG_TOKEN", "")
CHAT = os.getenv("TG_CHAT_ID", "")
API = f"https://api.telegram.org/bot{TOKEN}"

MIN_GAP = 1.2            # giay giua 2 lan goi, tranh 429
PER_MIN = 18             # tran tin/phut cho sendMessage (Telegram: 20/phut/nhom)
TRIES = 3                # so lan thu lai khi mat mang / 429
MAX_LEN = 4096           # gioi han cung cua Telegram

# `_call` tra ve chuoi nay khi Telegram tu choi vi 400 — ha cap HTML co the cuu.
# Dung chuoi chu khong dung int: `isinstance(True, int)` la True, de nham.
BAD_REQ = "bad_request"

log = print                    # main.py gan lai de ghi vao service.log

_lock = asyncio.Lock()
_last = 0.0
_stamps: list[float] = []      # moc gui trong 60s gan nhat (tran PER_MIN)


async def _pace(bucket: bool) -> None:
    """Giu nhip goi. Chi duoc goi khi da nam trong `_lock`."""
    global _last
    if bucket:
        now = time.time()
        _stamps[:] = [t for t in _stamps if now - t < 60]
        if len(_stamps) >= PER_MIN:
            wait = 60 - (now - _stamps[0]) + 0.5
            log(f"tgapi: da {PER_MIN} tin/phut, cho {wait:.0f}s")
            await asyncio.sleep(wait)
            now = time.time()
            _stamps[:] = [t for t in _stamps if now - t < 60]
        _stamps.append(now)
    gap = time.monotonic() - _last
    if gap < MIN_GAP:
        await asyncio.sleep(MIN_GAP - gap)
    _last = time.monotonic()


def _retry_after(r: httpx.Response) -> float:
    try:
        return float(r.json().get("parameters", {}).get("retry_after", 5))
    except Exception:                                            # noqa: BLE001
        return 5.0


async def _call(method: str, body: dict, timeout: float = 20,
                bucket: bool = False,
                tries: int = TRIES) -> dict | bool | str | None:
    """Goi mot method cua Bot API.

    dict    = result cua Telegram
    True    = coi nhu xong ('not modified', 'message to edit not found')
    BAD_REQ = Telegram tu choi noi dung (400) -> nguoi goi co the ha cap HTML
    None    = khong goi duoc (thieu token / mat mang / 429 het luot thu)
    """
    if not TOKEN or not CHAT:
        log("tgapi: thieu TG_TOKEN / TG_CHAT_ID")
        return None
    for attempt in range(max(1, tries)):
        r = None
        async with _lock:
            await _pace(bucket)
            try:
                async with httpx.AsyncClient(timeout=timeout) as c:
                    r = await c.post(f"{API}/{method}", json=body)
            except Exception as e:                               # noqa: BLE001
                log(f"tgapi {method}: {type(e).__name__}: {e}")
        if r is None:                                  # mat mang -> lui roi thu
            await asyncio.sleep(2 ** attempt)
            continue
        if r.status_code == 200:
            return r.json().get("result")
        if r.status_code == 429:
            w = _retry_after(r)
            log(f"tgapi {method} 429: cho {w:.0f}s")
            await asyncio.sleep(w + 1)
            continue
        if "not modified" in r.text or "message to edit not found" in r.text:
            return True
        log(f"tgapi {method} {r.status_code}: {r.text[:200]}")
        if 400 <= r.status_code < 500:
            return BAD_REQ           # loi noi dung/quyen: thu lai vo ich
    return None


def _body(text: str, lv: int, markup: dict | None) -> dict:
    import render
    b = {"chat_id": CHAT, "parse_mode": "HTML",
         "disable_web_page_preview": True,
         "text": (text if lv == 0 else render.degrade(text, lv))[:MAX_LEN]}
    if markup:
        b["reply_markup"] = markup
    return b


async def send(text: str, markup: dict | None = None,
               loud: bool = False) -> int | None:
    """Gui tin nhan moi. Tra ve message_id, None neu that bai han.

    None nghia la "chua den tay nguoi doc" -> nguoi goi nen day vao spool
    (notifier.Spool) chu khong duoc coi la da gui.
    """
    for lv in (0, 1, 2, 3):
        b = _body(text, lv, markup)
        b["disable_notification"] = not loud
        res = await _call("sendMessage", b, bucket=True)
        if isinstance(res, dict):
            return res.get("message_id")
        if res is not BAD_REQ:
            return None              # mat mang: ha cap HTML khong cuu duoc gi
        if lv < 3:
            log(f"tgapi: Telegram tu choi noi dung -> ha cap HTML muc {lv + 1}")
    return None


async def edit(message_id: int, text: str, markup: dict | None = None) -> bool:
    for lv in (0, 1, 2, 3):
        b = _body(text, lv, markup)
        b["message_id"] = message_id
        b.pop("disable_notification", None)
        res = await _call("editMessageText", b)
        if res is True or isinstance(res, dict):
            return True
        if res is not BAD_REQ:
            return False
    return False


async def edit_markup(message_id: int, markup: dict) -> bool:
    res = await _call("editMessageReplyMarkup",
                      {"chat_id": CHAT, "message_id": message_id,
                       "reply_markup": markup})
    return res is True or isinstance(res, dict)


async def answer_cb(cb_id: str, text: str = "", alert: bool = False) -> None:
    # tries=1: nut het hieu luc sau ~vai giay, thu lai chi lam nghen _lock.
    await _call("answerCallbackQuery",
                {"callback_query_id": cb_id, "text": text[:200],
                 "show_alert": alert}, timeout=10, tries=1)


async def get_updates(offset: int, timeout: int = 25) -> list | None:
    """Long-polling. Khong dung _lock: neu khong, 25s cho doi se chan edit."""
    if not TOKEN:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout + 15) as c:
            r = await c.get(f"{API}/getUpdates",
                            params={"offset": offset, "timeout": timeout,
                                    "allowed_updates":
                                        json.dumps(["callback_query"])})
    except Exception as e:                                       # noqa: BLE001
        log(f"tgapi getUpdates: {type(e).__name__}: {e}")
        return None
    if r.status_code == 409:
        log("getUpdates 409: co consumer khac hoac webhook dang bat")
        return None
    if r.status_code != 200:
        log(f"getUpdates {r.status_code}: {r.text[:150]}")
        return None
    return r.json().get("result", [])


def ready() -> bool:
    """Co du .env de gui khong. main.py kiem tra luc khoi dong, khong doi giua phien."""
    return bool(TOKEN and CHAT)
