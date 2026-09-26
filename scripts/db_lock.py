#!/usr/bin/env python3
"""Ai dang giu quyen ghi baseline.db, va tai sao.

    python scripts/db_lock.py

Sinh ra vi mot cau hoi khong tra loi duoc bang doc code: `python nightly.py`
bao `database is locked` NGAY TU BUOC BARS, trong khi moi ket noi trong repo
deu dat busy_timeout 15-30 giay. Hai kha nang dan den hai viec phai lam khac
nhau, va doan sai thi sua sai:

  A. Kho nen KHONG o che do WAL. Luc do mot nguoi ghi chan het ca nguoi doc:
     main.py (ghi khoa `beat` moi 20 giay) va watchd.py (giu mot ket noi mo ca
     phien) du de `bars.sync` - chay nhieu phut, ghi mot batch vai giay mot lan
     - het 30 giay cho doi. Te hon: `PRAGMA journal_mode=WAL` trong DDL cua
     bars.py CHI doi duoc che do khi khong co ket noi nao khac dang mo, va no
     nem thang `database is locked` khi doi khong duoc - tuc la ngay o
     bars.con(), truoc khi tai mot nen nao. Tren VM co main.py chay 24/7 thi
     lan doi che do do KHONG BAO GIO thanh cong.
  B. DB o WAL nhung mot tien trinh dang giu mot transaction ghi rat lau. Luc do
     phai tim tien trinh do, khong phai doi che do.

Script nay KHONG sua gi. No mo DB o che do chi doc de xem che do journal, roi
thu xin quyen ghi trong 2 giay bang mot `BEGIN IMMEDIATE` co ROLLBACK ngay -
dung cai ma bars/structure/nightly can va khong xin duoc. Khong chay DDL, khong
`PRAGMA journal_mode=...`: mot script chan doan khong duoc lam doi trang thai
cua thu no dang chan doan.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "state" / "baseline.db"

# Bang nao thuoc ai. De doc dong "ai ghi cai gi" ma khong phai mo 9 file.
CHU = {
    "bars": "bars.py --sync (nightly buoc 1)",
    "base": "prep.py --from-bars (nightly buoc 2)",
    "regime": "regime.py (nightly buoc 3)",
    "sector_rank": "sectors.py (nightly buoc 4)",
    "struct": "structure.py (nightly buoc 5)",
    "candidates": "setups.py (nightly buoc 6)",
    "night": "nightly.py (ket qua moi lan chay)",
    "alerts": "main.py (he thong cham diem, chay 24/7)",
    "kv": "main.py ghi khoa `beat` moi 20 giay + push.py doc",
    "alert_msg": "main.py / store.py",
    "watch": "main.py (nut Theo doi)",
    "watch_alert": "watchd.py (canh bao trong phien)",
}


def _giu_file(p: Path) -> list[tuple[int, str]]:
    """(pid, cmdline) cua moi tien trinh dang mo file nay. Chi tren Linux.

    Doc /proc chu khong goi `fuser`: `fuser` can sudo de thay tien trinh cua
    user khac, va o day ta chi can tien trinh CUA MINH - main.py va watchd.py
    deu chay duoi user `ubuntu`, giong nguoi go lenh.
    """
    if not Path("/proc").is_dir():
        return []
    that = str(p.resolve())
    out = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            for fd in (d / "fd").iterdir():
                if os.readlink(str(fd)).split(" (deleted)")[0] == that:
                    cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ")
                    out.append((int(d.name), cmd.decode("utf-8", "replace")
                                .strip() or "?"))
                    break
        except (PermissionError, FileNotFoundError, OSError):
            continue
    return sorted(out)


def _doc(db: Path) -> sqlite3.Connection:
    """Ket noi CHI DOC: khong tao file, khong doi che do, khong chay DDL."""
    return sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    if not DB.exists():
        print(f"✗ khong co {DB}")
        return 3
    mb = DB.stat().st_size / 1e6
    print(f"DB   {DB}  ({mb:.1f} MB)")
    for hau in ("-wal", "-shm"):
        p = DB.with_name(DB.name + hau)
        if p.exists():
            print(f"     {p.name}: {p.stat().st_size / 1e6:.1f} MB")

    # ───── 1. che do journal: cau hoi quan trong nhat ─────
    c = _doc(DB)
    try:
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
        print(f"\njournal_mode = {mode}")
        if str(mode).lower() != "wal":
            print(
                "  ✗ KHONG phai WAL. O che do nay mot nguoi ghi chan het nguoi\n"
                "    doc, nen `bars.sync` (chay nhieu phut) va main.py (ghi\n"
                "    `beat` moi 20 giay) chan nhau that su. Va `PRAGMA\n"
                "    journal_mode=WAL` trong bars.py khong doi duoc che do khi\n"
                "    con ket noi khac dang mo -> no nem thang `database is\n"
                "    locked` ngay o bars.con().\n"
                "    SUA (mot lan, phai dung het tien trinh dang mo DB):\n"
                "      sudo systemctl stop scanner.service\n"
                "      pkill -f watchd.py\n"
                "      sqlite3 state/baseline.db 'PRAGMA journal_mode=WAL;'\n"
                "      sudo systemctl start scanner.service\n"
                "    Lenh sqlite3 phai in ra `wal`. Neu in ra `delete` thi van\n"
                "    con tien trinh giu DB - xem muc 3 duoi day.")
        else:
            print("  ✓ nguoi doc va nguoi ghi khong chan nhau")

        # ───── 2. bang nao co, cu bao nhieu ─────
        print("\nbang:")
        ten = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for t in ten:
            try:
                n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error as e:
                n = f"? ({e})"
            print(f"  {t:<14} {n:>9}   {CHU.get(t, '')}")
    finally:
        c.close()

    # ───── 3. ai dang mo file ─────
    print("\ntien trinh dang mo file:")
    giu = _giu_file(DB)
    if not giu:
        print("  (khong doc duoc /proc - may khong phai Linux, hoac khong co "
              "quyen)")
    for pid, cmd in giu:
        print(f"  pid {pid:<8} {cmd[:96]}")

    # ───── 4. cau hoi that: xin duoc quyen ghi khong? ─────
    # BEGIN IMMEDIATE = xin khoa ghi ma khong ghi gi. Dung cai ma `bars.save`,
    # `structure.build` va `nightly.save_run` can. busy_timeout 2 giay chu
    # khong 30: o day ta muon BIET co ai giu hay khong, khong phai doi.
    print("\nthu xin quyen ghi (BEGIN IMMEDIATE, cho toi da 2 giay):")
    w = sqlite3.connect(str(DB), timeout=2, isolation_level=None)
    t0 = time.time()
    try:
        w.execute("PRAGMA busy_timeout=2000")
        w.execute("BEGIN IMMEDIATE")
        w.execute("ROLLBACK")
        print(f"  ✓ xin duoc sau {time.time() - t0:.2f}s — luc nay khong ai "
              "giu quyen ghi")
        ma = 0
    except sqlite3.OperationalError as e:
        print(f"  ✗ {time.time() - t0:.2f}s: {e}")
        print("    Co nguoi dang giu quyen ghi NGAY BAY GIO. Doi chieu voi muc "
              "3\n    de biet la ai; neu muc 1 da la `wal` thi day la mot "
              "transaction\n    ghi mo lau, khong phai van de che do journal.")
        ma = 1
    finally:
        w.close()

    print("\nGhi chu: dung script nay TRUOC khi chay `python nightly.py` bang "
          "tay.\nNeu muc 4 hong thi nightly cung se hong, va bao cao cua no se "
          "ke mot\nloi khong lien quan gi den so lieu.")
    return ma


if __name__ == "__main__":
    raise SystemExit(main())
