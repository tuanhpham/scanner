"""report_quality.py - Diem cao co that su tot hon khong? Bang tra loi.

    python scripts/report_quality.py            # 30 ngay gan nhat
    python scripts/report_quality.py 90         # 90 ngay
    python scripts/report_quality.py 30 --syms  # kem danh sach alert chi tiet

Doc bang nay the nao (quan trong hon chinh bang):

  cov     ty le dong da co gia sau 15 phut. cov thap = phan lon alert chua
          duoc chot lai; dung ket luan gi ca. Chay `python outcome.py
          --backfill <ngay>` de chot.
  win15   ty le alert co gia sau 15 phut CAO HON luc bao. 50% = ngang tung
          xuc xac, tuc la nhom diem do khong co gia tri.
  med15   trung vi % thay doi sau 15 phut. Day va med60 la hai cot dang tin
          nhat trong bang.
  medMFE  trung vi dinh cao nhat sau alert. Trong RAT dep va vo dung: do la
          dinh hoan hao khong ai ban dung. Dung dua quyet dinh vao cot nay.
  medMAE  trung vi day thap nhat sau alert - muc lo phai chiu neu vao lenh.
          medMFE lon nhung medMAE cung lon = dao dong manh, khong phai co hoi.

Va ba dieu bang nay KHONG tinh den: spread (ma float nho RVOL 60x co spread
rat rong), slippage, va viec ban co kip vao lenh hay khong.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import outcome                                                   # noqa: E402

DB = ROOT / "state" / "baseline.db"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                                # noqa: BLE001
    pass


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    days = int(args[0]) if args else 30
    rows = outcome.report(DB, days=days)

    print(f"CHAT LUONG ALERT - {days} ngay gan nhat\n")
    print(outcome.fmt_report(rows))

    if not rows:
        return

    n = sum(r["n"] for r in rows)
    cov = sum(r["cov"] * r["n"] for r in rows) / n
    fin = sum(r["final"] * r["n"] for r in rows) / n
    print(f"\n{n} alert · da chot lai {fin * 100:.0f}% · co gia +15p "
          f"{cov * 100:.0f}%")

    if cov < 0.6:
        print("\n[!] cov thap. Chay `python outcome.py --backfill <YYYY-MM-DD>` "
              "cho tung ngay con thieu roi doc lai.")

    # Ket luan may moc, de khoi tu doc y minh muon doc.
    lo = next((r for r in rows if r["bucket"] == "7.0-8.0"), None)
    if lo and lo["n"] >= 30 and lo["win15"] is not None:
        if abs(lo["win15"] - 0.5) < 0.05:
            print(f"\n=> Nhom 7.0-8.0 co win15 = {lo['win15'] * 100:.0f}% "
                  f"tren {lo['n']} alert: ngang tung xuc xac.\n"
                  f"   Nang ALERT_SCORE trong main.py len 8.0 se cat bo nhom nay.")
        else:
            print(f"\n=> Nhom 7.0-8.0: win15 = {lo['win15'] * 100:.0f}% "
                  f"({lo['n']} alert). Con dang ke, giu nguyen nguong 7.0.")
    elif lo:
        print(f"\n(Nhom 7.0-8.0 moi co {lo['n']} alert - can >=30 moi noi "
              f"duoc dieu gi. Cu de bot chay tiep.)")

    if "--syms" in sys.argv:
        print("\n" + "-" * 74)
        _detail(days)


def _detail(days: int) -> None:
    import datetime as dt
    import sqlite3
    from contextlib import closing

    cut = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    with closing(sqlite3.connect(str(DB))) as c:
        rows = c.execute(
            "SELECT day,sym,score,level,px0,px15,px60,px_close,hi_after,"
            "lo_after,src FROM outcome WHERE day>=? ORDER BY day DESC, "
            "score DESC", (cut,)).fetchall()

    def p(a, b):
        return f"{(a / b - 1) * 100:+6.1f}" if (a and b) else "     -"

    print(f"{'NGAY':<11}{'SYM':<7}{'DIEM':>5}{'L':>2}{'PX0':>8}"
          f"{'+15p':>7}{'+60p':>7}{'close':>7}{'MFE':>7}{'MAE':>7}  NGUON")
    for d, s, sc, lv, p0, p15, p60, pc, hi, lo, src in rows:
        print(f"{d:<11}{s:<7}{sc or 0:>5.1f}{lv or 0:>2}{p0 or 0:>8.2f}"
              f"{p(p15, p0):>7}{p(p60, p0):>7}{p(pc, p0):>7}"
              f"{p(hi, p0):>7}{p(lo, p0):>7}  "
              f"{'da chot' if src == 'yf' else 'tam (vong quet)'}")


if __name__ == "__main__":
    main()
