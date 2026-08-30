"""vprofile.py - Duong cong khoi luong noi phien (U-shape) de tinh RVOL."""
from __future__ import annotations

import numpy as np

# (phut ke tu mo cua, ty le % khoi luong ca ngay da giao dich)
_MIN = np.array([0, 5, 10, 15, 30, 45, 60, 90, 120, 150, 180,
                 210, 240, 270, 300, 330, 350, 370, 380, 390], dtype=float)
_FRAC = np.array([0.000, 0.035, 0.055, 0.072, 0.125, 0.163, 0.196, 0.253,
                  0.303, 0.350, 0.395, 0.440, 0.487, 0.540, 0.600, 0.675,
                  0.735, 0.830, 0.895, 1.000], dtype=float)

FLOOR = 0.012  # tranh chia cho 0 ngay sau khi mo cua / trong premarket
FULL_SESSION = 390


def cum_frac(mso: int | float | None, session_minutes: int = FULL_SESSION) -> float:
    """Ty le khoi luong ky vong da giao dich tai phut `mso`.

    mso < 0 (premarket) -> tra ve FLOOR.
    Nua phien (210 phut) duoc co gian theo ty le.
    """
    if mso is None:
        return FLOOR
    scale = FULL_SESSION / max(session_minutes, 1)
    m = float(mso) * scale
    if m <= 0:
        return FLOOR
    if m >= FULL_SESSION:
        return 1.0
    return max(FLOOR, float(np.interp(m, _MIN, _FRAC)))


def rvol(volume_today: float, adv20: float, mso: int | float | None,
         session_minutes: int = FULL_SESSION) -> float:
    """Relative volume da chuan hoa theo thoi diem trong phien."""
    if not adv20 or adv20 <= 0:
        return 0.0
    expected = adv20 * cum_frac(mso, session_minutes)
    if expected <= 0:
        return 0.0
    return float(volume_today) / expected

PREMKT_FRAC = 0.03   # premarket ~3% khoi luong ca ngay


def session_frac(state: str, mso: int | float | None,
                 session_minutes: int = FULL_SESSION) -> float:
    """Ty le khoi luong ky vong, CO XET trang thai phien.

    Trong phien   -> duong cong U theo mso.
    Premarket     -> hang so nho.
    Dong cua/AH   -> 1.0, vi volume nhan duoc la CA PHIEN da hoan tat.
    """
    if state in ("OPENING", "LIVE", "CLOSING"):
        return cum_frac(mso, session_minutes)
    if state in ("PREP", "PREMARKET"):
        return PREMKT_FRAC
    return 1.0


def rvol_at(volume_today: float, adv20: float, frac: float) -> float:
    """RVOL khi da biet truoc `frac` - dung thay cho rvol() trong scorer."""
    if not adv20 or adv20 <= 0:
        return 0.0
    exp = adv20 * max(frac, FLOOR)
    return float(volume_today) / exp if exp > 0 else 0.0

if __name__ == "__main__":
    print(f"{'mso':>5} {'cum_frac':>9}   vi du: vol=5M, adv20=1M -> rvol")
    for m in (-30, 0, 5, 15, 30, 60, 120, 195, 300, 380, 390):
        f = cum_frac(m)
        print(f"{m:>5} {f:>9.3f}   {rvol(5_000_000, 1_000_000, m):>6.1f}x")

