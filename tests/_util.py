"""Ho tro cho tests/ — chay duoc CA hai kieu:

    pytest -q                  # tren CI va tren may co venv day du
    python tests/test_render.py # tren may khong co pytest / khong co deps

Ly do co kieu thu hai: may dev khong cai duoc pandas / httpx /
pandas_market_calendars, ma van phai kiem tra duoc render.py va halts.py truoc
khi push. Khong co harness nay thi khong test duoc gi ca cho den khi len VM.

`need()` bo qua test khi thieu thu vien, thay vi bao loi — nho vay cung mot
file test chay o ca hai noi, chi khac so test duoc bo qua.
"""
from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest
except ImportError:                       # khong co pytest -> tu lo
    pytest = None                          # type: ignore[assignment]


class Skipped(Exception):
    """Bo qua — dung khi khong co pytest. Co san de except() luon hop le."""


def _skip(reason: str) -> None:
    """Bo qua test hien tai.

    `allow_module_level` la bat buoc voi pytest: goi skip() ngoai than mot ham
    test ma khong co co nay thi pytest bao LOI (khong phai bo qua), ca file do.
    Trong than ham test thi co nay vo hai.
    """
    if pytest is not None:
        pytest.skip(reason, allow_module_level=True)
    raise Skipped(reason)


def need(*mods: str):
    """Import cac module, thieu bat ky cai nao -> bo qua test nay.

    Tra ve module dau tien cho tien: `sc = need("scorer")`.
    """
    out = []
    for m in mods:
        try:
            out.append(importlib.import_module(m))
        except Exception as e:             # noqa: BLE001 - ImportError va ca loi
            reason = f"thieu {m} ({type(e).__name__}: {e})"
            # `python tests/test_x.py` tren may thieu deps: bo qua CA FILE va
            # thoat 0. Neu de Skipped bay len tu than module thi no thanh
            # traceback + exit 1, tuc la "test that bai" — sai han y nghia.
            if pytest is None and sys._getframe(1).f_code.co_name == "<module>":
                print(f"  SKIP ca file: {reason}")
                raise SystemExit(0) from None
            _skip(reason)
    return out[0] if len(out) == 1 else out


def run(ns: dict) -> int:
    """Chay moi ham test_* trong namespace. Tra ve so test that bai."""
    tests = sorted((k, v) for k, v in ns.items()
                   if k.startswith("test_") and callable(v))
    ok = fail = skip = 0
    for name, fn in tests:
        try:
            fn()
        except Skipped as e:
            skip += 1
            print(f"  SKIP {name}: {e}")
        except BaseException as e:          # noqa: BLE001
            # pytest.skip() nem Skipped rieng cua pytest -> nhan dien theo ten.
            if type(e).__name__ in ("Skipped", "OutcomeException"):
                skip += 1
                print(f"  SKIP {name}: {e}")
                continue
            fail += 1
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
            print("       " + traceback.format_exc(limit=3)
                  .strip().replace("\n", "\n       "))
        else:
            ok += 1
            print(f"  ok   {name}")
    print(f"\n{ok} ok · {fail} fail · {skip} skip")
    return fail


def main(ns: dict) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                       # noqa: BLE001
        pass
    print(f"{Path(ns.get('__file__', '?')).name}")
    raise SystemExit(1 if run(ns) else 0)
