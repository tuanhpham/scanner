"""Thu ket noi Telegram: gui mot tin nhan HTML don gian.

    python scripts/check_tg.py

Khong dung tin nhan alert (xem scripts/preview_alert.py cho viec do) - chi de
tra loi mot cau: .env co dung va bot co gui duoc vao nhom khong.

Ten file KHONG duoc bat dau bang "test_": pytest o goc repo se tuong day la
file test, import no, va `asyncio.run` o duoi chay ngay luc collect. Truoc day
file nay ten scripts/test_tg.py va lam ca job pytest chet voi INTERNALERROR
(SystemExit "Thieu TG_TOKEN") truoc khi mot test nao kip chay.
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
          else "That bai - xem dong log tgapi ngay tren")


if __name__ == "__main__":       # import khong duoc gui gi ca
    asyncio.run(main())
