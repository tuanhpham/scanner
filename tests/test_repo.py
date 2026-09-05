"""Giao uoc o muc repo: nhung thu lam ca job CI chet chu khong lam mot test chet.

Loi that da xay ra: scripts/test_tg.py trung mau ten test_*.py, pytest o goc
import no luc collect, `asyncio.run(main())` o cap module chay va nem
SystemExit -> INTERNALERROR, "no tests ran". Khong mot test nao bao loi vi
khong mot test nao duoc chay. Vi vay hai test duoi day.
"""
from __future__ import annotations

import _util

ROOT = _util.ROOT


def test_khong_co_file_test_nao_ngoai_tests():
    """File ten test_*.py nam ngoai tests/ se bi pytest import va chay."""
    lac = [str(p.relative_to(ROOT)) for p in ROOT.rglob("test_*.py")
           if p.parent.name != "tests" and ".venv" not in p.parts]
    lac += [str(p.relative_to(ROOT)) for p in ROOT.rglob("*_test.py")
            if p.parent.name != "tests" and ".venv" not in p.parts]
    assert not lac, f"doi ten cac file nay (vd check_*.py): {lac}"


def test_pytest_chi_quet_thu_muc_tests():
    """Lop chan thu hai, doc lap voi cach dat ten file."""
    ini = (ROOT / "pytest.ini")
    assert ini.exists(), "thieu pytest.ini"
    assert "testpaths = tests" in ini.read_text(encoding="utf-8")


def test_script_khong_chay_khi_bi_import():
    """Script trong scripts/ phai boc than trong `if __name__ == '__main__'`.

    Khong bat buoc voi moi script, nhung check_tg.py thi co: no gui tin nhan
    that, va chinh no la file da lam do CI.
    """
    src = (ROOT / "scripts" / "check_tg.py").read_text(encoding="utf-8")
    i = src.index("asyncio.run(")
    assert '__name__ == "__main__"' in src[:i]


if __name__ == "__main__":
    _util.main(globals())
