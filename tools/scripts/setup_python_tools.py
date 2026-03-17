#!/usr/bin/env python3
"""
tools/scripts/setup_python_tools.py
Install required Python packages for the STM32 template into the active Python
environment (virtualenv) or the user's site-packages.

Currently ensures:
  - pyocd
  - pyserial
"""

import importlib.util
import platform
import subprocess
import sys


REQUIRED_PACKAGES = [
    ("pyocd", "pyocd"),
    ("serial", "pyserial"),
]


def info(message: str) -> None:
    print(f"[python-tools] {message}")


def error(message: str) -> None:
    print(f"[python-tools] Error: {message}")


def _in_virtualenv() -> bool:
    return (
        getattr(sys, "base_prefix", sys.prefix) != sys.prefix
        or hasattr(sys, "real_prefix")
    )


def _missing_packages() -> list[str]:
    missing = []
    for module_name, package_name in REQUIRED_PACKAGES:
        if importlib.util.find_spec(module_name) is None:
            missing.append(package_name)
    return missing


def _pip_available() -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "--version"],
        text=True,
        capture_output=True,
    )
    return result.returncode == 0


def _install(packages: list[str]) -> None:
    cmd = [sys.executable, "-m", "pip", "install"]
    if not _in_virtualenv():
        cmd.append("--user")
    cmd.extend(packages)

    info("Installing missing packages: " + ", ".join(packages))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"pip exited with code {result.returncode}")


def main() -> None:
    missing = _missing_packages()
    if not missing:
        info("pyOCD and pyserial are already installed.")
        return

    if not _pip_available():
        error("pip is not available for the selected Python interpreter.")
        if platform.system() == "Linux":
            print("[python-tools] Arch Linux hint: sudo pacman -S python-pip")
        print(
            f"[python-tools] Then run: {sys.executable} -m pip install --user "
            + " ".join(missing)
        )
        sys.exit(1)

    try:
        _install(missing)
    except RuntimeError as exc:
        error(str(exc))
        print(
            f"[python-tools] You can retry manually with: {sys.executable} -m pip install --user "
            + " ".join(missing)
        )
        sys.exit(1)

    info("Python tool packages are ready.")


if __name__ == "__main__":
    main()
