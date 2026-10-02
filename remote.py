"""remote.py - chay mot lenh co DANH SACH TRANG, bam tu trang web.

Trang Settings & Guides co mot hang nut (Trang thai, Xem log, Day du lieu, Quet
thu, Cap nhat code...). Bam nut = trinh duyet ghi mot dong vao khoa
`scanner:commands`. File nay doc khoa do, chay lenh, roi ghi ket qua vao
`scanner:command_results` - khoa ma CHI VM ghi.

VM khong mo cong nao. Moi chieu deu la VM goi RA ngoai (giong push.py), nen
firewall cua Oracle khong phai dong vao.

Nhung dieu KHONG duoc pha:

1. Khong co shell tu do. Lenh la mot TEN trong CMDS, tham so duy nhat la ten
   mot file log trong LOGS. Moi thu khac bi tu choi - ca o day lan o function
   (functions/api/scanner/[[path]].ts), vi ai lay duoc ma sync thi khong duoc
   theo do ma lay duoc shell tren VM.
2. Khong bao gio chay main.py thu hai (Telegram getUpdates 409). "Quet thu"
   chay NGAY TRONG process dang chay (main.py truyen ham vao `extra`); goi tu
   CLI thi tu choi. "Khoi dong lai" = process tu thoat, systemd
   (Restart=always) bat lai - khong can sudo, va chay tay ngoai systemd thi tu
   choi chu khong de thanh hai process.
3. Moi lenh chay DUNG MOT LAN. Id da xu ly duoc ghi xuong state/remote.json
   TRUOC khi chay: lenh "cap nhat + khoi dong lai" ma ghi sau thi process moi
   doc lai dung lenh do va khoi dong lai mai mai.
4. Lenh cu hon 5 phut bi bo qua (vi du VM vua tat may mot dem, sang ra khong
   duoc tu dung day lam lenh hom qua).
5. Ket qua di qua redact(): token, ma, mat khau trong .env bi thay bang ***
   truoc khi roi VM, va bi cat con MAX_OUT ky tu cuoi.
6. Khong sua .env, khong sua crontab, khong dong vao firewall.

    python remote.py               # selftest, khong mang
    python remote.py --once        # doc va chay lenh dang cho mot lan
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import push

ROOT = push.ROOT
STATE = ROOT / "state" / "remote.json"
CMD_KEY = "scanner:commands"
RES_KEY = "scanner:command_results"

POLL_SEC = 20            # 4320 lan doc D1/ngay - D1 free cho 5 trieu
MAX_AGE_MS = 5 * 60 * 1000
MAX_OUT = 6000           # ky tu, giu PHAN CUOI: dong loi nam o cuoi
KEEP = 8                 # so ket qua giu trong scanner:command_results
LOG_LINES = 60

# Phai trung voi COMMANDS trong functions/api/scanner/[[path]].ts va vmPanel.ts.
CMDS = ("status", "log", "push", "scan", "nightly", "dblock", "update", "restart")
LOGS = {
    "service": "service.log",
    "prep": "prep.log",
    "push": "push.log",
    "watchd": "watchd.log",
    "bot": "bot.log",
}
ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,40}$")

log = print


# ───────────────────────── redact ─────────────────────────
_SECRET_NAME = re.compile(r"TOKEN|KEY|SECRET|PASS|CODE|CHAT_ID|PUSH_URL", re.I)
_TG = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")
_KV = re.compile(r"(?i)\b((?:token|secret|password|passwd|api[_-]?key|"
                 r"x-sync-code|x-scanner-token|authorization)\s*[=:]\s*)\S+")
_LONG = re.compile(r"[A-Za-z0-9_\-+/=]{40,}")


def _long(m: re.Match) -> str:
    s = m.group(0)
    # Hash git day du (40 hex) khong phai bi mat, va la thu can doc sau `git pull`.
    return s if re.fullmatch(r"[0-9a-f]{40}", s) else "***"


def redact(text: str) -> str:
    """Xoa moi thu giong bi mat. Thu tu: gia tri that trong env truoc (chac
    chan nhat), roi den cac dang token quen mat."""
    vals = {v for k, v in os.environ.items()
            if v and len(v) >= 8 and _SECRET_NAME.search(k)}
    for v in sorted(vals, key=len, reverse=True):
        text = text.replace(v, "***")
    text = _TG.sub("***", text)
    text = _KV.sub(lambda m: m.group(1) + "***", text)
    return _LONG.sub(_long, text)


def cap(text: str, n: int = MAX_OUT) -> str:
    if len(text) <= n:
        return text
    return f"... (bo {len(text) - n} ky tu dau)\n" + text[-n:]


# ───────────────────────── chay ─────────────────────────
def _sh(argv: list[str], timeout: int = 60) -> tuple[bool, str]:
    """Mot tien trinh con, KHONG qua shell, co timeout. Khong nem ra ngoai."""
    try:
        p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                           timeout=timeout, errors="replace")
    except FileNotFoundError:
        return False, f"khong co lenh: {argv[0]}"
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        return False, out + f"\nqua {timeout} giay, da dung"
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode == 0, out.rstrip() + ("" if p.returncode == 0 else f"\n(exit {p.returncode})")


def _py(*args: str, timeout: int = 60) -> tuple[bool, str]:
    return _sh([sys.executable, *args], timeout)


def tail(path: Path, n: int = LOG_LINES) -> str:
    """n dong cuoi, doc tu cuoi file - service.log co the vai tram MB."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 64_000))
            raw = f.read().decode(errors="replace")
    except FileNotFoundError:
        return f"chua co file {path.name}"
    return "\n".join(raw.splitlines()[-n:])


def _beat(db: Path) -> str:
    try:
        c = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            r = c.execute("SELECT v FROM kv WHERE k='beat'").fetchone()
        finally:
            c.close()
        b = json.loads(r[0]) if r else {}
    except Exception as e:                                       # noqa: BLE001
        return f"beat: khong doc duoc ({type(e).__name__})"
    if not b:
        return "beat: chua co"
    age = int(time.time() - (b.get("ts") or 0) / 1000)
    return (f"beat: {age}s truoc · pid {b.get('pid')} · len {b.get('up_sec', 0) // 60} phut"
            f" · {b.get('session')} · {b.get('scans')} lan quet · {b.get('errors')} loi"
            f" · {b.get('n_alerts')} alert")


def under_systemd() -> bool:
    # systemd dat INVOCATION_ID cho moi service no chay; chay tay thi khong co.
    return bool(os.getenv("INVOCATION_ID"))


class Remote:
    def __init__(self, extra: dict | None = None, db: Path = push.DB,
                 state: Path = STATE, systemd: bool | None = None, log=None):
        self.extra = extra or {}
        self.db = Path(db)
        self.state = Path(state)
        self.systemd = under_systemd() if systemd is None else systemd
        self.log = log or globals()["log"]

    # ── state/remote.json ──
    def load(self) -> dict:
        try:
            return json.loads(self.state.read_text())
        except Exception:                                        # noqa: BLE001
            return {}

    def save(self, st: dict) -> None:
        try:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            self.state.write_text(json.dumps(st, indent=1))
        except Exception as e:                                   # noqa: BLE001
            self.log(f"remote: khong ghi duoc {self.state.name}: {e}")

    def post(self, st: dict, cid: str, cmd: str, arg: str, state: str,
             ok: bool | None = None, out: str = "") -> None:
        res = {"id": cid, "cmd": cmd, "arg": arg, "state": state, "ok": ok,
               "out": cap(redact(out)), "at": int(time.time() * 1000),
               "pid": os.getpid()}
        keep = [r for r in st.get("results", []) if r.get("id") != cid]
        st["results"] = [res, *keep][:KEEP]
        self.save(st)
        if not push.ready():
            return
        sc, js = push.call("PUT", f"kv/{RES_KEY}",
                           {"value": {"results": st["results"], "at": res["at"]}})
        if sc != 200:
            self.log(f"remote: ghi ket qua -> HTTP {sc} {js.get('error', '')}")

    # ── vong ──
    def boot(self) -> None:
        """Goi mot lan luc main.py khoi dong. Neu lan truoc thoat vi lenh
        restart/update thi bao lai la da len, kem pid moi va commit dang chay."""
        st = self.load()
        rid = st.pop("restart_id", None)
        if not rid:
            return
        prev = next((r for r in st.get("results", []) if r.get("id") == rid), {})
        _, head = _sh(["git", "log", "-1", "--format=%h %s"], 15)
        out = (prev.get("out", "") + "\n\n" if prev.get("out") else "") \
            + f"da chay lai: pid {os.getpid()} · {head}"
        self.post(st, rid, prev.get("cmd", "restart"), prev.get("arg", ""), "done", True, out)

    def tick(self, now_ms: int | None = None) -> str | None:
        """Doc lenh dang cho, chay neu moi. Tra "restart" neu process phai thoat."""
        js = push.get_full(CMD_KEY)
        if not js:
            return None
        cmd = js.get("value") or {}
        if not isinstance(cmd, dict):
            return None
        cid = str(cmd.get("id") or "")
        st = self.load()
        if not ID_RE.match(cid) or cid == st.get("last_id"):
            return None
        name, arg = str(cmd.get("cmd") or ""), str(cmd.get("arg") or "")

        st["last_id"] = cid                    # TRUOC khi chay - xem dieu 3
        self.save(st)

        now = now_ms if now_ms is not None else int(time.time() * 1000)
        age = now - int(js.get("updatedAt") or 0)
        if age > MAX_AGE_MS:
            self.post(st, cid, name, arg, "done", False,
                      f"bo qua: lenh da cu {age // 60000} phut (VM chi chay lenh moi hon 5 phut)")
            return None
        if name not in CMDS:
            self.post(st, cid, name, arg, "done", False, f"tu choi: '{name}' khong co trong danh sach")
            return None

        self.log(f"remote: {name} {arg}".rstrip())
        self.post(st, cid, name, arg, "running")
        try:
            ok, out, after = self.run(name, arg)
        except Exception as e:                                   # noqa: BLE001
            ok, out, after = False, f"{type(e).__name__}: {e}", None
        if after == "restart":
            st["restart_id"] = cid
        # Ket qua len cloud TRUOC khi process thoat.
        self.post(st, cid, name, arg, "restarting" if after else "done", ok, out)
        return after

    def run(self, name: str, arg: str) -> tuple[bool, str, str | None]:
        if name == "status":
            parts = [_sh(["systemctl", "is-active", "scanner", "watchd"], 15)[1],
                     _sh(["uptime"], 15)[1],
                     _beat(self.db),
                     _sh(["git", "log", "-1", "--format=commit %h · %cd · %s",
                          "--date=format:%d/%m %H:%M"], 15)[1],
                     _sh(["git", "status", "-sb", "--untracked-files=no"], 15)[1],
                     _sh(["df", "-h", str(ROOT)], 15)[1],
                     _sh(["free", "-m"], 15)[1]]
            return True, "\n\n".join(p for p in parts if p), None
        if name == "log":
            if arg not in LOGS:
                return False, f"tu choi: log '{arg}' khong co trong danh sach", None
            return True, tail(ROOT / "state" / LOGS[arg]), None
        if name == "push":
            ok, out = _py("push.py", "--all", timeout=300)
            return ok, out, None
        if name == "nightly":
            ok, out = _py("nightly.py", "--status", timeout=60)
            return ok, out, None
        if name == "dblock":
            ok, out = _py("scripts/db_lock.py", timeout=60)
            return ok, out, None
        if name == "scan":
            fn = self.extra.get("scan")
            if not fn:
                return False, "quet thu chi chay ben trong main.py dang chay (khong mo main.py thu hai)", None
            return True, fn(), None
        if name == "update":
            return self.update()
        if name == "restart":
            if not self.systemd:
                return False, "tu choi: process nay khong chay duoi systemd - khoi dong lai bang tay", None
            return True, "thoat de systemd (Restart=always) bat lai sau ~30 giay", "restart"
        return False, f"tu choi: '{name}'", None

    def update(self) -> tuple[bool, str, str | None]:
        """git pull --ff-only -> cong import -> thoat de systemd chay code moi.

        Cong import (`import main, push, remote` trong process con) chan truong
        hop thuong gap nhat: pull ve mot file loi cu phap / thieu thu vien, va
        service chet ngay luc khoi dong roi systemd bat lai mai trong vong lap.
        """
        _, before = _sh(["git", "rev-parse", "HEAD"], 15)
        ok, out = _sh(["git", "pull", "--ff-only"], 120)
        lines = [f"$ git pull --ff-only\n{out}"]
        if not ok:
            return False, "\n".join(lines), None
        _, after = _sh(["git", "rev-parse", "HEAD"], 15)
        if before.strip() == after.strip():
            return True, "\n".join(lines + ["da moi nhat, khong khoi dong lai"]), None
        _, files = _sh(["git", "diff", "--name-only", before.strip(), after.strip()], 15)
        lines.append(f"$ git log\n{_sh(['git', 'log', '--oneline', f'{before.strip()}..{after.strip()}'], 15)[1]}")
        if "requirements.txt" in files.split():
            lines.append("requirements.txt da doi: chay `pip install -r requirements.txt` "
                         "tren VM roi bam Khoi dong lai. Chua khoi dong lai.")
            return False, "\n".join(lines), None
        gok, gout = _py("-c", "import main, push, remote", timeout=120)
        lines.append("$ cong import: " + ("OK" if gok else f"LOI\n{gout}"))
        if not gok:
            lines.append("KHONG khoi dong lai: code moi khong import duoc, process cu van chay.")
            return False, "\n".join(lines), None
        if not self.systemd:
            lines.append("process nay khong chay duoi systemd - khoi dong lai bang tay")
            return True, "\n".join(lines), None
        lines.append("thoat de systemd chay code moi sau ~30 giay")
        return True, "\n".join(lines), "restart"


# ───────────────────────── CLI ─────────────────────────
def _smoke() -> int:
    os.environ["_REMOTE_SMOKE_TOKEN"] = "abcdefgh12345678"
    assert "abcdefgh" not in redact("x abcdefgh12345678 y")
    assert redact("bot 123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA") == "bot ***"
    assert redact("TOKEN=hello") == "TOKEN=***"
    assert redact("a" * 39) == "a" * 39
    assert cap("x" * 10, 4).endswith("xxxx")
    print("remote selftest OK")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="doc va chay lenh dang cho mot lan")
    a = ap.parse_args()
    if not a.once:
        sys.exit(_smoke())
    act = Remote().tick()
    print(f"remote: {act or 'xong'}")
