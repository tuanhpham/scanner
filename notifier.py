"""notifier.py - Gui alert Telegram: hang doi async, token bucket, chong trung."""
from __future__ import annotations

import asyncio
import html
import json
import os
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
SPOOL = ROOT / "state" / "spool.json"


def esc(s) -> str:
    return html.escape(str(s), quote=False)


class Notifier:
    def __init__(self, token: str, chat_id: str, per_min: int = 15) -> None:
        self.api = f"https://api.telegram.org/bot{token}"
        self.chat = str(chat_id)
        self.per_min = per_min
        self.q: asyncio.Queue = asyncio.Queue()
        self._stamps: list[float] = []
        self._cool: dict[str, float] = {}
        self.sent = 0
        self.failed = 0
        self.spool: list[dict] = self._load_spool()

    # ---------- spool (luu alert khi mat mang) ----------
    def _load_spool(self) -> list[dict]:
        try:
            return json.loads(SPOOL.read_text())
        except Exception:  # noqa: BLE001
            return []

    def _save_spool(self) -> None:
        try:
            SPOOL.parent.mkdir(parents=True, exist_ok=True)
            SPOOL.write_text(json.dumps(self.spool)[:500_000])
        except Exception:  # noqa: BLE001
            pass

    # ---------- API cong khai ----------
    async def send(self, text: str, key: str | None = None,
                   loud: bool = False, cooldown: float = 540) -> bool:
        """Dua vao hang doi. key != None -> chong gui lai trong `cooldown` giay."""
        if key:
            last = self._cool.get(key, 0)
            if time.time() - last < cooldown:
                return False
            self._cool[key] = time.time()
        await self.q.put({"text": text, "loud": loud, "ts": time.time()})
        return True

    async def worker(self) -> None:
        async with httpx.AsyncClient(timeout=25) as c:
            while True:
                item = await self.q.get()
                try:
                    await self._throttle()
                    ok = await self._post(c, item["text"], item["loud"])
                    if not ok:
                        self.spool.append(item)
                        self._save_spool()
                    elif self.spool:
                        await self._flush(c)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    self.failed += 1
                finally:
                    self.q.task_done()

    # ---------- noi bo ----------
    async def _throttle(self) -> None:
        now = time.time()
        self._stamps = [t for t in self._stamps if now - t < 60]
        if len(self._stamps) >= self.per_min:
            await asyncio.sleep(60 - (now - self._stamps[0]) + 0.5)
            now = time.time()
            self._stamps = [t for t in self._stamps if now - t < 60]
        self._stamps.append(now)

    async def _post(self, c: httpx.AsyncClient, text: str, loud: bool) -> bool:
        payload = {
            "chat_id": self.chat,
            "text": text[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": not loud,
        }
        for attempt in range(3):
            try:
                r = await c.post(f"{self.api}/sendMessage", json=payload)
                if r.status_code == 429:
                    wait = r.json().get("parameters", {}).get("retry_after", 5)
                    await asyncio.sleep(float(wait) + 1)
                    continue
                if r.status_code == 200:
                    self.sent += 1
                    return True
                if 400 <= r.status_code < 500:
                    print(f"[tg] {r.status_code}: {r.text[:200]}", flush=True)
                    self.failed += 1
                    return True  # loi format, gui lai cung vo ich
            except Exception as e:  # noqa: BLE001
                print(f"[tg] {type(e).__name__}: {e}", flush=True)
            await asyncio.sleep(2 ** attempt)
        self.failed += 1
        return False

    async def _flush(self, c: httpx.AsyncClient) -> None:
        while self.spool:
            it = self.spool[0]
            age = int((time.time() - it["ts"]) / 60)
            txt = (f"⏳ <i>(tre {age} phut do mat ket noi)</i>\n" + it["text"]
                   if age > 2 else it["text"])
            await self._throttle()
            if not await self._post(c, txt, False):
                return
            self.spool.pop(0)
            self._save_spool()


def from_env() -> "Notifier":
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    tok, chat = os.getenv("TG_TOKEN", ""), os.getenv("TG_CHAT_ID", "")
    if not tok or not chat:
        raise SystemExit("Thieu TG_TOKEN / TG_CHAT_ID trong .env")
    return Notifier(tok, chat)
