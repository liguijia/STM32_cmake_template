#!/usr/bin/env python3
"""
tools/scripts/setup_pyocd.py
Install the pyOCD target pack for the current project's MCU.

Reads the PYOCD_TARGET from the first argument (or detects it from *.ld / *.ioc).
Checks whether the target is already known to pyOCD; only runs the slow
'pack update' step when the target is genuinely missing.

Usage (called by 'make setup'):
    python tools/scripts/setup_pyocd.py <target>   e.g. stm32f334r8tx
    python tools/scripts/setup_pyocd.py            # auto-detect from *.ld
"""

import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent


def _detect_target() -> "str | None":
    """Derive a pyOCD target name from the project linker script name."""
    ld_files = list(PROJECT_ROOT.glob("*.ld"))
    if not ld_files:
        return None
    stem = ld_files[0].stem                          # STM32F334XX_FLASH
    m    = re.match(r"(STM32\w+?)_", stem, re.I)
    return m.group(1).lower() if m else None         # stm32f334xx


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, **kwargs)


def _target_known(target: str) -> bool:
    """Return True if pyOCD already knows about *target* (pack installed)."""
    r = _run(["pyocd", "list", "--targets"])
    return target.lower() in r.stdout.lower()


def setup(target: str) -> None:
    print(f"[pyOCD] Target: {target}")

    if _target_known(target):
        print(f"[pyOCD] Pack already installed for '{target}' — nothing to do.")
        return

    # Pack not found; update the index first, then install
    print("[pyOCD] Target not found — updating pack index (this may take ~30 s) …")
    r = _run(["pyocd", "pack", "update"])
    if r.returncode != 0:
        print("[pyOCD] Warning: pack update failed:\n", r.stderr.strip())
        # Continue anyway — the pack might still be installable

    print(f"[pyOCD] Installing pack for '{target}' …")
    r = subprocess.run(["pyocd", "pack", "install", target])
    if r.returncode != 0:
        print(f"[pyOCD] Error: failed to install pack for '{target}'.")
        print( "[pyOCD] Try manually:  pyocd pack update && pyocd pack install", target)
        sys.exit(1)

    print(f"[pyOCD] Pack installed. Ready to flash with:  make flash FLASH_TOOL=pyocd")


def main() -> None:
    if len(sys.argv) > 1:
        target = sys.argv[1].strip()
    else:
        target = _detect_target()
        if not target:
            print("[pyOCD] Could not detect target from *.ld — pass target as argument.")
            sys.exit(1)
        print(f"[pyOCD] Auto-detected target: {target}")

    setup(target)


if __name__ == "__main__":
    main()
