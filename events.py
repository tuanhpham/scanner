"""events.py - Log co cau truc: moi dong mot JSON, de MAY doc.

Log tieng Viet trong `state/bot.log` la de nguoi doc luc dang chay. Con cau hoi
"trong 3 tuan qua, ma co rvol > 50 va SEC risk cao thi thang bao nhieu lan" thi
`grep` tren tieng Viet khong bao gio tra loi duoc. File nay la nguon so lieu cho
Phase 1 (xem mục 11 README).

Ghep voi bang `outcome` bang cap (sym, ts): outcome.record() dung cung moc thoi
gian voi luc gui alert.

Nguyen tac: KHONG BAO GIO nem loi len tren. Mat mot dong log con hon mat mot
alert - moi ham tra ve bool/so, loi thi im lang bo qua.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PATH = ROOT / "state" / "events.jsonl"

MAX_BYTES = 5_000_000      # ~10k alert; vuot thi doi ten thanh .1 va mo file moi

# Cac truong lay tu dict cua scorer. Liet ke tuong minh chu khong copy ca dict:
# them truong moi vao scorer khong duoc am tham lam doi format file nay.
FIELDS = ("sym", "score", "px", "chg", "vol", "rvol", "atr_move",
          "float_sh", "float_rot", "dollar_vol", "diverge", "freshness",
          "sources", "cik", "sec_risk",
          # Phase 3: nhom tin (DILUTION/BIO/DEAL/...) va risk cua nhom do.
          # Phase 1 se doc hai cot nay de tra loi "nhom tin nao thi alert dung".
          "news_group", "news_risk")


def _safe(o):
    """Doi thu JSON khong hieu thanh chuoi. set -> list da sap xep cho on dinh."""
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    return str(o)


def _rotate(p: Path) -> None:
    try:
        if p.exists() and p.stat().st_size > MAX_BYTES:
            p.replace(p.with_suffix(p.suffix + ".1"))
    except Exception:                                            # noqa: BLE001
        pass


def emit(kind: str, path: str | Path = PATH, ts: float | None = None,
         **fields) -> bool:
    """Ghi mot dong JSON. Tra ve False neu khong ghi duoc (khong nem loi).

    `ts` la epoch giay - de nguyen dang so de sort/join, khong format.
    """
    rec = {"ts": round(ts if ts is not None else time.time(), 3),
           "kind": kind, **fields}
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        _rotate(p)
        line = json.dumps(rec, ensure_ascii=False, default=_safe)
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
    except Exception:                                            # noqa: BLE001
        return False


def alert(h: dict, level: int, kind: str = "NEW", path: str | Path = PATH,
          ts: float | None = None, **extra) -> bool:
    """Mot alert -> mot dong. `h` la dict cua scorer.score_one().

    Truong thieu duoc ghi la null, KHONG bo qua: "khong biet rvol" va "rvol = 0"
    la hai chuyen khac nhau, va bang phan tich phai phan biet duoc.
    """
    if not isinstance(h, dict) or not h.get("sym"):
        return False
    row = {k: h.get(k) for k in FIELDS}
    row["level"] = level
    row["alert_kind"] = kind          # NEW / UP - khac `kind` cua dong log
    row.update(extra)
    return emit("alert", path=path, ts=ts, **row)


def read(path: str | Path = PATH, kind: str | None = None) -> list[dict]:
    """Doc lai file. Dong hong -> bo qua, khong lam chet ca ban phan tich."""
    out: list[dict] = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:                                            # noqa: BLE001
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:                                        # noqa: BLE001
            continue
        if isinstance(r, dict) and (kind is None or r.get("kind") == kind):
            out.append(r)
    return out


def _smoke() -> None:
    """Chay duoc tren may khong co thu vien gi: chi stdlib."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "e.jsonl"
        h = {"sym": "AAA", "score": 8.4, "px": 10.0, "chg": 0.9,
             "rvol": 40.0, "sources": ["finviz", "alpaca_mover"]}
        assert alert(h, 2, "NEW", path=p, ts=1_757_000_000.0, dry=True)
        assert emit("halt_block", path=p, sym="SCAMZ", code="H10")
        rows = read(p)
        assert len(rows) == 2 and rows[0]["kind"] == "alert"
        assert rows[0]["sym"] == "AAA" and rows[0]["level"] == 2
        assert rows[0]["atr_move"] is None, "truong thieu phai la null"
        assert read(p, kind="halt_block")[0]["code"] == "H10"
        for r in rows:
            print("  ", json.dumps(r, ensure_ascii=False)[:120])
    print("events smoke OK")


if __name__ == "__main__":
    import argparse
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                            # noqa: BLE001
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--tail", nargs="?", const=20, type=int, metavar="N",
                    help="in N dong cuoi cua state/events.jsonl")
    ap.add_argument("--kind", default=None)
    a = ap.parse_args()

    if a.tail is None:
        _smoke()
    else:
        rows = read(PATH, kind=a.kind)
        print(f"{len(rows)} dong trong {PATH}")
        for r in rows[-a.tail:]:
            print(json.dumps(r, ensure_ascii=False))
