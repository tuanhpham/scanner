"""tgapi.py — goi Telegram API truc tiep cho nhung viec notifier chua lam:
gui kem inline keyboard, lay message_id, sua tin nhan, tra loi nut bam.
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
MIN_GAP = 1.2                  # giay giua 2 lan goi, tranh loi 429
log = print                    # main.py gan lai de ghi vao service.log

_lock = asyncio.Lock()
_last = 0.0


async def _call(method: str, body: dict, timeout: float = 20) -> dict | bool | None:
    """None = that bai. dict = result. True = 'not modified' (coi nhu xong)."""
    global _last
    if not TOKEN or not CHAT:
        log("tgapi: thieu TG_TOKEN / TG_CHAT_ID")
        return None
    async with _lock:
        gap = time.monotonic() - _last
        if gap < MIN_GAP:
            await asyncio.sleep(MIN_GAP - gap)
        _last = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(f"{API}/{method}", json=body)
        except Exception as e:                                   # noqa: BLE001
            log(f"tgapi {method}: {type(e).__name__}: {e}")
            return None
    if r.status_code == 200:
        return r.json().get("result")
    if "not modified" in r.text or "message to edit not found" in r.text:
        return True
    log(f"tgapi {method} {r.status_code}: {r.text[:200]}")
    return None


def _body(text: str, lv: int, markup: dict | None) -> dict:
    import render
    b = {"chat_id": CHAT, "parse_mode": "HTML",
         "disable_web_page_preview": True,
         "text": text if lv == 0 else render.degrade(text, lv)}
    if markup:
        b["reply_markup"] = markup
    return b


async def send(text: str, markup: dict | None = None,
               loud: bool = False) -> int | None:
    """Gui tin nhan moi. Tra ve message_id, None neu that bai."""
    for lv in (0, 1, 2):                # 400 vi tag HTML -> ha cap dan
        b = _body(text, lv, markup)
        b["disable_notification"] = not loud
        res = await _call("sendMessage", b)
        if isinstance(res, dict):
            return res.get("message_id")
    return None


async def edit(message_id: int, text: str, markup: dict | None = None) -> bool:
    for lv in (0, 1, 2):
        b = _body(text, lv, markup)
        b["message_id"] = message_id
        b.pop("disable_notification", None)
        if await _call("editMessageText", b):
            return True
    return False


async def edit_markup(message_id: int, markup: dict) -> bool:
    return bool(await _call("editMessageReplyMarkup",
                            {"chat_id": CHAT, "message_id": message_id,
                             "reply_markup": markup}))


async def answer_cb(cb_id: str, text: str = "", alert: bool = False) -> None:
    await _call("answerCallbackQuery",
                {"callback_query_id": cb_id, "text": text[:200],
                 "show_alert": alert}, timeout=10)


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
