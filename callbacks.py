"""callbacks.py — vong long-polling: nhan nut bam, sua tin nhan tai cho."""
from __future__ import annotations

import asyncio
import time

import render
import store
import tgapi

RF_COOLDOWN = 8.0          # giay, chong spam nut Refresh


def parse_cb(data: str) -> tuple[str, str, str]:
    p = (data or "").split("|")
    if len(p) < 3 or p[0] != render.CB_VER:
        return "", "", ""
    return p[1], p[2], (p[3] if len(p) > 3 else "")


class Callbacks:
    def __init__(self, db, refresh, view_builder, log=print):
        self.db = db
        self.refresh = refresh          # async (sym) -> dict | None
        self.build = view_builder       # (h, kind, detail) -> AlertView
        self.log = log
        self._last_rf: dict[str, float] = {}

    def _cool(self, sym: str) -> bool:
        """True = duoc phep cham diem lai. Chan spam cho ca Refresh va Chi tiet."""
        now = time.time()
        if now - self._last_rf.get(sym, 0) < RF_COOLDOWN:
            return False
        self._last_rf[sym] = now
        return True

    async def run(self) -> None:
        off = int(store.get_kv(self.db, "tg_offset", "0") or 0)
        fails = 0
        self.log("callbacks: bat dau lang nghe nut bam")
        while True:
            try:
                ups = await tgapi.get_updates(off)
                if ups is None:
                    fails += 1
                    await asyncio.sleep(min(5 * fails, 60))
                    continue
                fails = 0
                for u in ups:
                    off = u["update_id"] + 1
                    store.set_kv(self.db, "tg_offset", off)
                    if "callback_query" in u:
                        asyncio.create_task(self._handle(u["callback_query"]))
            except asyncio.CancelledError:
                raise
            except Exception as e:                               # noqa: BLE001
                self.log(f"callbacks: {type(e).__name__}: {e}")
                await asyncio.sleep(5)

    async def _handle(self, cq: dict) -> None:
        cid = cq["id"]
        mid = (cq.get("message") or {}).get("message_id")
        act, sym, arg = parse_cb(cq.get("data", ""))
        if not act or not mid:
            await tgapi.answer_cb(cid, "Nut da het hieu luc")
            return
        try:
            if act == "trk":
                on = arg == "1"
                store.set_track(self.db, sym, on)
                await tgapi.answer_cb(
                    cid, f"Dang theo doi {sym}, tin nhan se tu cap nhat"
                    if on else f"Da bo theo doi {sym}")
                await self._rerender(sym, mid, markup_only=True)
            elif act == "dtl":
                # Cung an RF_COOLDOWN: nut nay cung cham diem lai (goi
                # scorer), khong the de bam lien tuc khong gioi han.
                if not self._cool(sym):
                    await tgapi.answer_cb(cid, "Cham thoi, doi 8 giay")
                    return
                await tgapi.answer_cb(cid)
                await self._rerender(sym, mid, detail=(arg == "1"))
            elif act == "rf":
                if not self._cool(sym):
                    await tgapi.answer_cb(cid, "Vua cap nhat roi")
                    return
                await tgapi.answer_cb(cid, "Dang cap nhat...")
                await self._rerender(sym, mid, refresh=True)
            else:
                await tgapi.answer_cb(cid)
        except Exception as e:                                   # noqa: BLE001
            self.log(f"callbacks {act} {sym}: {type(e).__name__}: {e}")
            await tgapi.answer_cb(cid, "Loi, xem log")

    async def _rerender(self, sym: str, mid: int, *, detail: bool = False,
                        refresh: bool = False,
                        markup_only: bool = False) -> None:
        h = None
        if not markup_only:
            try:
                h = await self.refresh(sym)
            except Exception as e:                               # noqa: BLE001
                self.log(f"refresh {sym}: {type(e).__name__}: {e}")
        if h is None:
            snap = store.get_snap(self.db, sym) or {}
            if not snap and not markup_only:
                self.log(f"refresh {sym}: khong co du lieu, bo qua")
                return
            h = {"sym": sym, "px": snap.get("px") or 0, "chg": 0,
                 "score": snap.get("score") or 0, "rvol": snap.get("rvol"),
                 "atr_move": snap.get("atr_move"),
                 "float_rot": snap.get("float_rot"),
                 "freshness": "DELAYED", "explain": ""}
        v = self.build(h, "UPD", detail)
        kb = render.render_keyboard(v)
        if markup_only:
            await tgapi.edit_markup(mid, kb)
            return
        if await tgapi.edit(mid, render.render_alert(v), kb) and refresh:
            store.put_snap(self.db, sym, v.snapshot())
