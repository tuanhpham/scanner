"""Gui mot alert mau vao Telegram de xem layout that (co ca nut).

    python scripts/preview_alert.py

Di qua dung duong cua bot: build_view -> render_alert + render_keyboard ->
tgapi.send. Nen no cung goi edgar.assess(WETO) va doc state/baseline.db.
"""
import asyncio
import sys

sys.path.insert(0, ".")
import render                                                    # noqa: E402
import tgapi                                                     # noqa: E402

SAMPLE = {
    "sym": "WETO", "chg": 0.855, "px": 10.61, "score": 8.3,
    "rvol": 66.2, "atr_move": 4.1, "dollar_vol": 311e6,
    "float_sh": 8.4e6, "float_rot": 3.49, "freshness": "REALTIME",
    "cik": "0001941158", "sec_risk": 4.0,
    "explain": "rvol 66.2x(+2.0) · atr 4.1x(+1.5) · quay vòng 3.49x(+1.2)",
}


async def main():
    import time

    import main as app
    import news
    from clock import SessionClock

    # Khoi CATALYST doi so tay tin, ma o day khong co vong loop_news nao chay.
    # Nap mau cua news.py -> xem duoc layout that ma khong can key Alpaca.
    app._NB.load(news._sample(time.time()))

    v = app.build_view(SAMPLE, "NEW", SessionClock())
    txt = render.render_alert(v)
    print(txt)

    if not tgapi.ready():
        print("\n(thieu TG_TOKEN / TG_CHAT_ID trong .env -> chi in, khong gui)")
        return
    mid = await tgapi.send(txt, markup=render.render_keyboard(v), loud=False)
    print(f"\nda gui, message_id={mid}" if mid else "\n!! gui that bai")


asyncio.run(main())
