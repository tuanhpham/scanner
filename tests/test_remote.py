"""remote.py - lenh co danh sach trang bam tu trang web.

Thu phai dung, xep theo muc do "sai thi nguy hiem nhat":

1. Khong co duong nao thanh shell tu do: lenh la mot ten trong CMDS, tham so
   duy nhat la ten log trong LOGS (khong phai duong dan - `../.env` bi tu choi).
2. Moi lenh chay dung mot lan, va id duoc ghi TRUOC khi chay. Lenh restart ma
   chay lai sau khi process moi len thi la vong lap khoi dong lai vo tan.
3. Lenh cu hon 5 phut khong chay.
4. Bi mat trong env khong bao gio roi VM trong `out`.
5. Restart chi khi dang chay duoi systemd; ngoai systemd thi tu choi (khong
   bao gio thanh hai process main.py).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import _util

rm = _util.need("remote")
import push  # noqa: E402

NOW = 1_800_000_000_000


class Cloud:
    """Thay push.get_full / push.call / push.ready bang bo nho."""

    def __init__(self, cmd: dict | None, updated: int = NOW):
        self.cmd, self.updated, self.puts = cmd, updated, []

    def __enter__(self):
        self.old = push.get_full, push.call, push.ready
        push.get_full = lambda key: (None if self.cmd is None
                                     else {"value": self.cmd, "updatedAt": self.updated})
        push.call = lambda m, path, body=None: (self.puts.append((path, body)) or (200, {}))
        push.ready = lambda: True
        return self

    def __exit__(self, *a):
        push.get_full, push.call, push.ready = self.old

    def last(self) -> dict:
        return self.puts[-1][1]["value"]["results"][0]


def _rm(d: str, **kw) -> "rm.Remote":
    return rm.Remote(state=Path(d) / "remote.json", db=Path(d) / "none.db",
                     log=lambda *_: None, **kw)


# ── 1. danh sach trang ─────────────────────────────────────────────────────
def test_lenh_la_khong_chay():
    with tempfile.TemporaryDirectory() as d, Cloud({"id": "abc12345", "cmd": "rm -rf /"}) as c:
        assert _rm(d).tick(NOW) is None
        r = c.last()
        assert r["ok"] is False and "tu choi" in r["out"]
        assert len(c.puts) == 1                  # khong co buoc "running"


def test_log_khong_nhan_duong_dan():
    with tempfile.TemporaryDirectory() as d, Cloud({"id": "abc12345", "cmd": "log", "arg": "../.env"}) as c:
        _rm(d).tick(NOW)
        r = c.last()
        assert r["ok"] is False and "tu choi" in r["out"]


def test_log_doc_dong_cuoi():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.log"
        p.write_text("\n".join(f"dong {i}" for i in range(200)))
        out = rm.tail(p, 3)
        assert out.splitlines() == ["dong 197", "dong 198", "dong 199"]
        assert "chua co" in rm.tail(Path(d) / "khong.log")


def test_quet_thu_ngoai_main_tu_choi():
    with tempfile.TemporaryDirectory() as d, Cloud({"id": "abc12345", "cmd": "scan"}) as c:
        _rm(d).tick(NOW)
        assert c.last()["ok"] is False


def test_quet_thu_trong_main_dung_ham_truyen_vao():
    with tempfile.TemporaryDirectory() as d, Cloud({"id": "abc12345", "cmd": "scan"}) as c:
        _rm(d, extra={"scan": lambda: "AAPL 9.1"}).tick(NOW)
        assert c.last()["ok"] is True and c.last()["out"] == "AAPL 9.1"
        assert c.puts[0][1]["value"]["results"][0]["state"] == "running"


# ── 2. dung mot lan ────────────────────────────────────────────────────────
def test_moi_lenh_chay_mot_lan():
    n = []
    with tempfile.TemporaryDirectory() as d, Cloud({"id": "abc12345", "cmd": "scan"}):
        r = _rm(d, extra={"scan": lambda: n.append(1) or "ok"})
        r.tick(NOW)
        r.tick(NOW)
        _rm(d, extra={"scan": lambda: n.append(1) or "ok"}).tick(NOW)   # process moi
        assert n == [1]


def test_id_ghi_truoc_khi_chay():
    def boom():
        raise RuntimeError("chet giua chung")
    with tempfile.TemporaryDirectory() as d, Cloud({"id": "abc12345", "cmd": "scan"}) as c:
        _rm(d, extra={"scan": boom}).tick(NOW)
        assert json.loads((Path(d) / "remote.json").read_text())["last_id"] == "abc12345"
        assert c.last()["ok"] is False and "chet giua chung" in c.last()["out"]


def test_id_xau_bo_qua():
    with tempfile.TemporaryDirectory() as d, Cloud({"id": "a b", "cmd": "status"}) as c:
        assert _rm(d).tick(NOW) is None
        assert c.puts == []


# ── 3. lenh cu ─────────────────────────────────────────────────────────────
def test_lenh_cu_bo_qua():
    n = []
    with tempfile.TemporaryDirectory() as d, Cloud({"id": "abc12345", "cmd": "scan"},
                                                   updated=NOW - 6 * 60_000) as c:
        _rm(d, extra={"scan": lambda: n.append(1) or "ok"}).tick(NOW)
        assert n == [] and "cu" in c.last()["out"]


# ── 4. redact ──────────────────────────────────────────────────────────────
def test_redact():
    os.environ["SCANNER_TOKEN"] = "s3cr3t-value-123456"
    try:
        out = rm.redact("goi voi s3cr3t-value-123456 xong")
    finally:
        del os.environ["SCANNER_TOKEN"]
    assert "s3cr3t" not in out
    assert "***" in rm.redact("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh")
    assert rm.redact("password: hunter2") == "password: ***"
    h = "0123456789abcdef0123456789abcdef01234567"
    assert rm.redact(h) == h                          # hash git giu nguyen
    assert rm.redact("Q" * 50) == "***"
    assert rm.redact("abc1234 Sua test") == "abc1234 Sua test"


def test_out_bi_redact_va_cat():
    os.environ["TG_TOKEN"] = "tg-secret-abcdefgh"
    try:
        with tempfile.TemporaryDirectory() as d, Cloud({"id": "abc12345", "cmd": "scan"}) as c:
            _rm(d, extra={"scan": lambda: "x" * 9000 + " tg-secret-abcdefgh"}).tick(NOW)
            out = c.last()["out"]
    finally:
        del os.environ["TG_TOKEN"]
    assert "tg-secret" not in out
    assert len(out) <= rm.MAX_OUT + 60


# ── 5. restart ─────────────────────────────────────────────────────────────
def test_restart_ngoai_systemd_tu_choi():
    with tempfile.TemporaryDirectory() as d, Cloud({"id": "abc12345", "cmd": "restart"}) as c:
        assert _rm(d, systemd=False).tick(NOW) is None
        assert c.last()["ok"] is False


def test_restart_bao_ket_qua_truoc_roi_bao_lai_khi_len():
    with tempfile.TemporaryDirectory() as d, Cloud({"id": "abc12345", "cmd": "restart"}) as c:
        assert _rm(d, systemd=True).tick(NOW) == "restart"
        assert c.last()["state"] == "restarting"
        st = json.loads((Path(d) / "remote.json").read_text())
        assert st["restart_id"] == "abc12345"
        # process moi len
        r = _rm(d, systemd=True)
        r.boot()
        assert c.last()["state"] == "done" and "da chay lai" in c.last()["out"]
        assert "restart_id" not in r.load()
        assert r.tick(NOW) is None                # khong restart lan nua


def test_ket_qua_giu_toi_da_keep():
    with tempfile.TemporaryDirectory() as d:
        r = _rm(d, extra={"scan": lambda: "ok"})
        for i in range(rm.KEEP + 3):
            with Cloud({"id": f"id{i:06d}", "cmd": "scan"}) as c:
                r.tick(NOW)
        res = c.puts[-1][1]["value"]["results"]
        assert len(res) == rm.KEEP and res[0]["id"] == f"id{rm.KEEP + 2:06d}"


def test_cmds_khop_function():
    """Danh sach o day va trong functions/api/scanner/[[path]].ts phai trung."""
    assert set(rm.CMDS) == {"status", "log", "push", "scan", "nightly", "dblock", "update", "restart"}
    assert set(rm.LOGS) == {"service", "prep", "push", "watchd", "bot"}
