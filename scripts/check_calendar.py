"""In lich phien NYSE 60 ngay tới theo gio Duc - de biet may gio phai chay."""
import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clock import ET, SessionClock  # noqa: E402

c = SessionClock()
today = dt.datetime.now(ZoneInfo("UTC")).astimezone(ET).date()

print(f"{'Ngay':<12} {'Thu':<4} {'Mo (DE)':<9} {'Dong (DE)':<10} Ghi chu")
print("-" * 60)
for i in range(60):
    d = today + dt.timedelta(days=i)
    probe = dt.datetime.combine(d, dt.time(12, 0), tzinfo=ET)
    if not c.is_trading_day(probe):
        if d.weekday() < 5:
            print(f"{d}   {d:%a}   {'-':<9} {'-':<10} NGHI LE")
        continue
    lo, lc = c.local_open(probe), c.local_close(probe)
    notes = []
    if c.is_half(probe):
        notes.append("NUA PHIEN")
    if c.dst_skew(probe) == 5:
        notes.append("DST LECH -> som 1h")
    print(f"{d}   {d:%a}   {lo:%H:%M}     {lc:%H:%M}      {' '.join(notes)}")

