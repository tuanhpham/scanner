"""notifier.py — HANG DOI TREN DIA cho luc mat mang. Khong goi API.

Truoc Phase 4a file nay la duong gui thu hai (co httpx, token bucket, 429, ha
cap HTML — tat ca trung voi `tgapi.py`). Gio no chi con mot viec: khi tgapi gui
that bai, giu text lai tren dia de lan sau gui bu. Moi thu lien quan den mang
nam trong `tgapi.py`.

Vi sao van can tren dia: VM co the bi restart giua luc mat mang. Hang doi trong
RAM se mat sach; `state/spool.json` thi khong.

Han che co y: spool KHONG giu nut inline. Text gui bu la text tho — nut "Theo
doi" gan voi message_id, ma tin gui bu la tin moi nen snapshot cu khong con
khop. Alert tre khong nut con hon khong co alert.

Module nay khong keo theo httpx: `import notifier` chay duoc tren may khong co
thu vien nao, va `flush()` nhan duoc ham gui tu ngoai nen test duoc.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPOOL = ROOT / "state" / "spool.json"

MAX_ITEMS = 50       # mat mang ca gio thi 50 tin cu nhat da vo nghia
LATE_MIN = 2         # tre hon bao nhieu phut thi ghi ro la tin cu
PREFIX = "⏳ <i>(tre {m} phut do mat ket noi)</i>\n"

log = print          # main.py gan lai de ghi co dau thoi gian


class Spool:
    """Danh sach tin cho gui, tu luu xuong dia sau moi thay doi."""

    def __init__(self, path: str | Path = SPOOL) -> None:
        self.path = Path(path)
        self.items: list[dict] = self._load()

    def __len__(self) -> int:
        return len(self.items)

    # ---------- dia ----------
    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:                                        # noqa: BLE001
            return []
        if not isinstance(data, list):
            return []
        return [it for it in data if isinstance(it, dict) and it.get("text")]

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.items, ensure_ascii=False),
                                 encoding="utf-8")
        except Exception as e:                                   # noqa: BLE001
            log(f"spool: khong luu duoc ({type(e).__name__}: {e})")

    # ---------- dung ----------
    def add(self, text: str, loud: bool = False,
            ts: float | None = None) -> bool:
        """Xep mot tin vao cuoi hang. Tra ve False neu text rong."""
        if not text:
            return False
        self.items.append({"text": text, "loud": bool(loud),
                           "ts": ts if ts is not None else time.time()})
        if len(self.items) > MAX_ITEMS:
            drop = len(self.items) - MAX_ITEMS
            del self.items[:drop]      # bo tin CU nhat, giu tin moi nhat
            log(f"spool: qua {MAX_ITEMS} tin, bo {drop} tin cu nhat")
        self._save()
        return True

    async def flush(self, send=None, now: float | None = None) -> int:
        """Gui bu theo dung thu tu. Tra ve so tin da gui duoc.

        Dung ngay khi mot tin that bai: mang van chua on, thu tiep chi lam
        nghen vong quet. Thu tu duoc giu -> khong duoc bo qua tin loi de gui
        tin sau.

        `send` la ham async (text, loud) -> truthy khi thanh cong. Mac dinh la
        tgapi.send; import ben trong de module nay khong keo theo httpx.
        """
        if not self.items:
            return 0
        if send is None:
            import tgapi

            async def send(text, loud):                      # noqa: ANN001
                return await tgapi.send(text, loud=loud)

        t0 = now if now is not None else time.time()
        n = 0
        while self.items:
            it = self.items[0]
            age = int((t0 - it.get("ts", t0)) / 60)
            txt = (PREFIX.format(m=age) + it["text"] if age >= LATE_MIN
                   else it["text"])
            # loud=False: tin cu khong dang danh thuc nguoi doc nua.
            if not await send(txt, False):
                break
            self.items.pop(0)
            n += 1
        self._save()
        if n:
            log(f"spool: gui bu {n} tin, con {len(self.items)}")
        return n


def _smoke() -> None:
    """Chay duoc tren may khong co thu vien gi: chi stdlib."""
    import asyncio
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "spool.json"
        s = Spool(p)
        assert len(s) == 0 and not s.add("")
        assert s.add("mot", ts=0) and s.add("hai", loud=True)
        assert len(Spool(p)) == 2, "phai doc lai duoc tu dia"

        got: list[tuple[str, bool]] = []

        async def ok(text, loud):
            got.append((text, loud))
            return True

        async def die(text, loud):
            return False

        assert asyncio.run(Spool(p).flush(die)) == 0
        assert len(Spool(p)) == 2, "that bai thi khong duoc mat tin"
        assert asyncio.run(s.flush(ok, now=600)) == 2
        assert len(s) == 0 and len(Spool(p)) == 0
        assert got[0][0].startswith("⏳"), "tin tre phai co nhan tre"
        assert "mot" in got[0][0] and got[0][1] is False
        assert got[1][0] == "hai", "tin moi khong can nhan tre"

        s2 = Spool(Path(td) / "b.json")
        for i in range(MAX_ITEMS + 5):
            s2.add(f"t{i}")
        assert len(s2) == MAX_ITEMS and s2.items[0]["text"] == "t5"
    print("notifier smoke OK")


if __name__ == "__main__":
    _smoke()
