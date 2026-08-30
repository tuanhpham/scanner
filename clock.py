
"""clock.py - Lich phien NYSE, quy doi sang gio Duc, xu ly DST + nua phien."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

ET = ZoneInfo("America/New_York")
DE = ZoneInfo("Europe/Berlin")
UTC = dt.timezone.utc

NYSE = mcal.get_calendar("NYSE")

# Cac moc trong ngay, tinh theo gio ET
PREP_START = dt.time(7, 0)
PREMKT_START = dt.time(9, 0)
OPENING_MIN = 30      # 30 phut dau phien = OPENING
CLOSING_MIN = 15      # 15 phut cuoi phien = CLOSING
AFTER_HOURS_END = dt.time(20, 0)


class SessionClock:
    """Tra loi cau hoi: bay gio la trang thai gi cua phien NYSE?"""

    def __init__(self) -> None:
        self._day: dt.date | None = None
        self._sched: dict | None = None

    # ---------- internal ----------
    def _load(self, day_et: dt.date) -> None:
        sch = NYSE.schedule(start_date=day_et, end_date=day_et)
        if sch.empty:
            self._sched = None
        else:
            row = sch.iloc[0]
            self._sched = {
                "open": row["market_open"].tz_convert(ET).to_pydatetime(),
                "close": row["market_close"].tz_convert(ET).to_pydatetime(),
            }
        self._day = day_et

    def now_et(self, now: dt.datetime | None = None) -> dt.datetime:
        n = (now or dt.datetime.now(UTC)).astimezone(ET)
        if n.date() != self._day:
            self._load(n.date())
        return n

    # ---------- public ----------
    def is_trading_day(self, now: dt.datetime | None = None) -> bool:
        self.now_et(now)
        return self._sched is not None

    def open_et(self, now: dt.datetime | None = None) -> dt.datetime | None:
        self.now_et(now)
        return self._sched["open"] if self._sched else None

    def close_et(self, now: dt.datetime | None = None) -> dt.datetime | None:
        self.now_et(now)
        return self._sched["close"] if self._sched else None

    def local_open(self, now: dt.datetime | None = None) -> dt.datetime | None:
        o = self.open_et(now)
        return o.astimezone(DE) if o else None

    def local_close(self, now: dt.datetime | None = None) -> dt.datetime | None:
        c = self.close_et(now)
        return c.astimezone(DE) if c else None

    def is_half(self, now: dt.datetime | None = None) -> bool:
        """Nua phien: dong cua truoc 15:00 ET (thuong la 13:00 ET)."""
        c = self.close_et(now)
        return bool(c and c.hour < 15)

    def session_minutes(self, now: dt.datetime | None = None) -> int:
        o, c = self.open_et(now), self.close_et(now)
        if not o:
            return 0
        return int((c - o).total_seconds() // 60)

    def mso(self, now: dt.datetime | None = None) -> int | None:
        """Minutes since open. Am = chua mo cua. None = khong phai ngay GD."""
        n = self.now_et(now)
        if not self._sched:
            return None
        return int((n - self._sched["open"]).total_seconds() // 60)

    def dst_skew(self, now: dt.datetime | None = None) -> int:
        """Chenh lech gio Duc - gio ET. Binh thuong 6, giai doan lech DST = 5."""
        n = self.now_et(now)
        off_de = n.astimezone(DE).utcoffset() or dt.timedelta()
        off_et = n.utcoffset() or dt.timedelta()
        return int((off_de - off_et).total_seconds() // 3600)

    def state(self, now: dt.datetime | None = None) -> str:
        """PREP | PREMARKET | OPENING | LIVE | CLOSING | AFTERHOURS | CLOSED"""
        n = self.now_et(now)
        if not self._sched:
            return "CLOSED"
        o, c = self._sched["open"], self._sched["close"]
        t = n.time()
        if t < PREP_START:
            return "CLOSED"
        if n < o:
            return "PREMARKET" if t >= PREMKT_START else "PREP"
        if n < o + dt.timedelta(minutes=OPENING_MIN):
            return "OPENING"
        if n < c - dt.timedelta(minutes=CLOSING_MIN):
            return "LIVE"
        if n < c:
            return "CLOSING"
        if t < AFTER_HOURS_END:
            return "AFTERHOURS"
        return "CLOSED"

    def scanning(self, now: dt.datetime | None = None) -> bool:
        """Co nen chay vong quet khong."""
        return self.state(now) in ("OPENING", "LIVE", "CLOSING")

    def describe(self, now: dt.datetime | None = None) -> str:
        n = self.now_et(now)
        st = self.state(now)
        if not self._sched:
            return f"{n:%Y-%m-%d %H:%M} ET | {st} | khong phai ngay giao dich"
        lo, lc = self.local_open(now), self.local_close(now)
        half = " (NUA PHIEN)" if self.is_half(now) else ""
        skew = self.dst_skew(now)
        warn = "  [!] DST lech: mo cua som 1 gio" if skew == 5 else ""
        return (
            f"{n:%Y-%m-%d %H:%M} ET | {st} | mso={self.mso(now)}\n"
            f"  Phien: {self._sched['open']:%H:%M}-{self._sched['close']:%H:%M} ET"
            f" = {lo:%H:%M}-{lc:%H:%M} gio Duc{half}{warn}"
        )


if __name__ == "__main__":
    print(SessionClock().describe())

