import asyncio, sys
sys.path.insert(0, ".")
import notifier

SAMPLE = {
    "sym": "WETO", "chg": 0.855, "px": 10.61, "score": 8.3,
    "rvol": 66.2, "atr_move": 4.1, "dollar_vol": 311e6,
    "float_sh": 8.4e6, "float_rot": 3.49, "freshness": "REALTIME",
    "cik": "0001941158", "sec_risk": 4.0,
    "explain": "rvol 66.2x(+2.0) · atr 4.1x(+1.5) · quay vòng 3.49x(+1.2)",
}


async def main():
    import main as app
    from clock import SessionClock
    txt = app.fmt(SAMPLE, "NEW", SessionClock())
    print(txt)

    n = notifier.from_env()
    w = asyncio.create_task(n.worker())
    await n.send(txt, key="preview", cooldown=0)
    await asyncio.sleep(6)          # cho worker post xong
    w.cancel()
    if n.spool:
        print("!! Telegram tu choi, tin nam trong spool:", len(n.spool))
    else:
        print("da gui xong")

asyncio.run(main())
