"""Thu ket noi Telegram: gui mot tin nhan HTML don gian.

    python scripts/test_tg.py

Khong dung tin nhan alert (xem scripts/preview_alert.py cho viec do) — chi de
tra loi mot cau: .env co dung va bot co gui duoc vao nhom khong.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tgapi                                                     # noqa: E402


async def main():
    if not tgapi.ready():
        raise SystemExit("Thieu TG_TOKEN / TG_CHAT_ID trong .env")
    mid = await tgapi.send("<b>Scanner</b> ket noi thanh cong\n"
                           "<i>tgapi.send hoat dong</i>", loud=True)
    print(f"OK, message_id={mid}" if mid
          else "That bai — xem dong log tgapi ngay tren")


asyncio.run(main())
