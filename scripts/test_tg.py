import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from notifier import Notifier


async def main():
    n = Notifier()
    t = asyncio.create_task(n.worker())
    await n.raw("OK <b>Scanner</b> ket noi thanh cong\n"
                "<i>Cong 1 da qua</i>", loud=True)
    await asyncio.sleep(3)
    t.cancel()


asyncio.run(main())
