#!/usr/bin/env python3
"""
tools/scripts/setup_pyocd.py
Install the pyOCD target pack for the current project's MCU.

Reads the PYOCD_TARGET from the first argument (or detects it from *.ld / *.ioc).
Checks whether the target is already known to pyOCD; only runs the slow
"pack update" step when the target is genuinely missing.

Usage (called by "make setup"):
    python tools/scripts/setup_pyocd.py <target>   e.g. stm32f334r8tx
    python tools/scripts/setup_pyocd.py            # auto-detect from *.ld
"""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
PYOCD_CMD = [sys.executable, "-m", "pyocd"]


def _detect_target() -> "str | None":
    """Derive a pyOCD target name from the project linker script name."""
    ld_files = list(PROJECT_ROOT.glob("*.ld"))
    if not ld_files:
        return None
    stem = ld_files[0].stem
    m = re.match(r"(STM32\w+?)_", stem, re.I)
    return m.group(1).lower() if m else None


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, **kwargs)


def _run_pyocd(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return _run(PYOCD_CMD + args, **kwargs)


def _pyocd_installed() -> bool:
    return importlib.util.find_spec("pyocd") is not None


def _target_known(target: str) -> bool:
    """Return True if pyOCD already knows about *target* (pack installed)."""
    r = _run_pyocd(["list", "--targets"])
    return r.returncode == 0 and target.lower() in r.stdout.lower()


def setup(target: str) -> None:
    print(f"[pyOCD] Target: {target}")

    if not _pyocd_installed():
        print("[pyOCD] Error: pyOCD is not installed for the current Python interpreter.")
        print(f"[pyOCD] Run first:  {sys.executable} tools/scripts/setup_python_tools.py")
        print(f"[pyOCD] Or manually: {sys.executable} -m pip install --user pyocd")
        sys.exit(1)

    if _target_known(target):
        print(f"[pyOCD] Pack already installed for '{target}' - nothing to do.")
        return

    print("[pyOCD] Target not found - updating pack index (this may take ~30 s) ...")
    r = _run_pyocd(["pack", "update"])
    if r.returncode != 0:
        print("[pyOCD] Warning: pack update failed:\n", r.stderr.strip())

    print(f"[pyOCD] Installing pack for '{target}' ...")
    r = subprocess.run(PYOCD_CMD + ["pack", "install", target])
    if r.returncode != 0:
        print(f"[pyOCD] Error: failed to install pack for '{target}'.")
        print(
            f"[pyOCD] Try manually:  {sys.executable} -m pyocd pack update && "
            f"{sys.executable} -m pyocd pack install {target}"
        )
        sys.exit(1)

    print("[pyOCD] Pack installed. Ready to flash with: make flash FLASH_TOOL=pyocd")


def main() -> None:
    if len(sys.argv) > 1:
        target = sys.argv[1].strip()
    else:
        target = _detect_target()
        if not target:
            print("[pyOCD] Could not detect target from *.ld - pass target as argument.")
            sys.exit(1)
        print(f"[pyOCD] Auto-detected target: {target}")

    setup(target)


if __name__ == "__main__":
    main()
