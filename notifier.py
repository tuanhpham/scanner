import os, asyncio, httpx, time, html
from collections import deque
from dotenv import load_dotenv

load_dotenv()
API  = f"https://api.telegram.org/bot{os.environ['TG_TOKEN']}"
CHAT = os.environ["TG_CHAT_ID"]


class Notifier:
    """Gui Telegram co token-bucket va cooldown theo key."""

    def __init__(self, max_per_min: int = 15):
        self.q: asyncio.Queue = asyncio.Queue()
        self.sent = deque()
        self.max = max_per_min
        self.last: dict[str, float] = {}

    async def push(self, key: str, text: str, loud=False, cooldown=900):
        if time.time() - self.last.get(key, 0) < cooldown:
            return False
        self.last[key] = time.time()
        await self.q.put((text, loud))
        return True

    async def raw(self, text: str, loud=False):
        await self.q.put((text, loud))

    async def _throttle(self):
        while len(self.sent) >= self.max:
            if time.time() - self.sent[0] > 60:
                self.sent.popleft()
            else:
                await asyncio.sleep(1)

    async def worker(self):
        async with httpx.AsyncClient(timeout=20) as c:
            while True:
                text, loud = await self.q.get()
                await self._throttle()
                for attempt in range(3):
                    try:
                        r = await c.post(f"{API}/sendMessage", json={
                            "chat_id": CHAT, "text": text,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": True,
                            "disable_notification": not loud})
                        if r.status_code == 429:
                            await asyncio.sleep(
                                r.json()["parameters"]["retry_after"] + 1)
                            continue
                        if r.status_code >= 400:
                            print("TG", r.status_code, r.text[:300])
                        break
                    except Exception as e:
                        print("TG fail", type(e).__name__, e)
                        await asyncio.sleep(2 ** attempt)
                self.sent.append(time.time())
                await asyncio.sleep(0.4)


def esc(s) -> str:
    return html.escape(str(s), quote=False)
