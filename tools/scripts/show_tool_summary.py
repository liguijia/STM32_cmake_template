#!/usr/bin/env python3
"""
tools/scripts/show_tool_summary.py
Print a concise summary of the current development toolchain and debug tools.
"""

from __future__ import annotations

import importlib.metadata
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent

TOOLCHAIN_ENV = PROJECT_ROOT / "tools" / "toolchain" / "env.mk"
OPENOCD_ENV = PROJECT_ROOT / "tools" / "openocd" / "env.mk"
JLINK_ENV = PROJECT_ROOT / "tools" / "jlink" / "env.mk"
SETTINGS_JSON = PROJECT_ROOT / ".vscode" / "settings.json"


def _exe(name: str) -> str:
    if os.name == "nt" and not name.lower().endswith(".exe"):
        return name + ".exe"
    return name


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _extract_active_source(text: str) -> tuple[str | None, str | None]:
    match = re.search(r"# Active source:\s*(\w+)(?:\s+\(([^)]*)\))?", text)
    if not match:
        return None, None
    return match.group(1).lower(), match.group(2)


def _extract_assignment(text: str, key: str) -> str | None:
    match = re.search(rf"override {re.escape(key)}\s*:=\s*(.+)", text)
    if not match:
        return None
    return match.group(1).strip()


def _expand_make_path(value: str) -> str:
    return value.replace("$(CURDIR)", PROJECT_ROOT.as_posix())


def _path_from_setting(key: str) -> Path | None:
    text = _read_text(SETTINGS_JSON)
    if not text:
        return None

    match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]*)"', text)
    if not match or not match.group(1):
        return None

    value = match.group(1).replace("\\", "/")
    if value.startswith("${workspaceFolder}/"):
        value = f"{PROJECT_ROOT.as_posix()}/{value[len('${workspaceFolder}/'):]}"
    return Path(value)


def _which(*names: str) -> Path | None:
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            return Path(resolved)
    return None


def _version_line(command: list[str], timeout: int = 10) -> str | None:
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except Exception:
        return None

    for stream in (result.stdout, result.stderr):
        for line in stream.splitlines():
            line = line.strip()
            if line:
                return line
    return None


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _compact_to_display(compact: str) -> str:
    if "." in compact:
        return compact

    match = re.match(r"(\d)(\d{2})([a-z]?)", compact, re.I)
    if not match:
        return compact

    suffix = match.group(3).lower()
    return f"{match.group(1)}.{match.group(2)}{suffix}"


def _extract_jlink_version(label: str | None, path: Path | None) -> str | None:
    candidates = []
    if label:
        candidates.append(label)
    if path is not None:
        candidates.extend(part for part in path.parts if "JLink" in part or "V" in part)

    for candidate in candidates:
        match = re.search(r"V(\d+\.\d+[a-z]?)", candidate, re.I)
        if match:
            return f"V{match.group(1)}"

        match = re.search(r"V(\d{3,}[a-z]?)", candidate, re.I)
        if match:
            return f"V{_compact_to_display(match.group(1))}"

    return None


def _format_summary(source: str | None, version: str | None, fallback: str) -> str:
    if not source or source == "missing":
        return version or fallback

    parts = []
    parts.append(source)
    parts.append(version or fallback)
    return " | ".join(parts)


def _toolchain_paths() -> tuple[str | None, Path | None, Path | None]:
    text = _read_text(TOOLCHAIN_ENV)
    source, _ = _extract_active_source(text)
    prefix = _extract_assignment(text, "PREFIX")
    if prefix:
        prefix = _expand_make_path(prefix)
        gcc = Path(prefix + _exe("gcc"))
        gdb = Path(prefix + _exe("gdb"))
        return source or "local", gcc, gdb

    gcc = _which(_exe("arm-none-eabi-gcc"), "arm-none-eabi-gcc")
    gdb = _which(_exe("arm-none-eabi-gdb"), "arm-none-eabi-gdb")
    if gcc or gdb:
        return source or "system", gcc, gdb
    return source or "missing", None, None


def _openocd_path() -> tuple[str | None, Path | None]:
    text = _read_text(OPENOCD_ENV)
    source, _ = _extract_active_source(text)
    configured = _extract_assignment(text, "OPENOCD")
    if configured:
        return source or "local", Path(_expand_make_path(configured))

    detected = _which(_exe("openocd"), "openocd")
    if detected:
        return source or "system", detected
    return source or "missing", None


def _jlink_paths() -> tuple[str | None, str | None, Path | None, Path | None]:
    text = _read_text(JLINK_ENV)
    source, label = _extract_active_source(text)
    configured = _extract_assignment(text, "JLINK")
    if configured:
        jlink = Path(_expand_make_path(configured))
    else:
        jlink = _which(_exe("JLink"), "JLink", _exe("JLinkExe"), "JLinkExe")

    gdbserver = _path_from_setting("cortex-debug.JLinkGDBServerPath")
    if gdbserver is None and jlink is not None:
        candidates = [
            jlink.parent / _exe("JLinkGDBServerCL"),
            jlink.parent / "JLinkGDBServerCLExe",
        ]
        gdbserver = next((path for path in candidates if path.exists()), None)
    if gdbserver is None:
        gdbserver = _which(
            _exe("JLinkGDBServerCL"),
            "JLinkGDBServerCL",
            "JLinkGDBServerCLExe",
        )

    if jlink or gdbserver:
        return source or ("local" if configured else "system"), label, jlink, gdbserver
    return source or "missing", label, None, None


def _print_tool(name: str, summary: str, path: str | Path | None = None) -> None:
    print(f"  {name:<12}{summary}")
    if path:
        print(f"  {'':12}{path}")


def main() -> None:
    gcc_source, gcc_path, gdb_path = _toolchain_paths()
    openocd_source, openocd_path = _openocd_path()
    jlink_source, jlink_label, jlink_path, jlink_gdbserver = _jlink_paths()

    python_version = platform.python_version()
    cmake_path = _which(_exe("cmake"), "cmake")
    ninja_path = _which(_exe("ninja"), "ninja")
    clangd_path = _which(_exe("clangd"), "clangd")
    cubeprog_path = _which(_exe("STM32_Programmer_CLI"), "STM32_Programmer_CLI")

    gcc_version = _version_line([str(gcc_path), "--version"]) if gcc_path else None
    gdb_version = _version_line([str(gdb_path), "--version"]) if gdb_path else None
    openocd_version = _version_line([str(openocd_path), "--version"]) if openocd_path else None
    cmake_version = _version_line([str(cmake_path), "--version"]) if cmake_path else None
    ninja_version = _version_line([str(ninja_path), "--version"]) if ninja_path else None
    clangd_version = _version_line([str(clangd_path), "--version"]) if clangd_path else None
    cubeprog_version = _version_line([str(cubeprog_path), "--version"]) if cubeprog_path else None

    pyocd_version = _package_version("pyocd")
    pyserial_version = _package_version("pyserial")
    jlink_version = _extract_jlink_version(jlink_label, jlink_path or jlink_gdbserver)

    print("Development Tools")

    _print_tool("Python", python_version, sys.executable)
    _print_tool("CMake", cmake_version or "not found", cmake_path)
    _print_tool("Ninja", ninja_version or "not found", ninja_path)
    _print_tool(
        "GCC",
        _format_summary(gcc_source, gcc_version, "not found"),
        gcc_path,
    )
    _print_tool(
        "GDB",
        _format_summary(gcc_source, gdb_version, "not found"),
        gdb_path,
    )
    _print_tool("clangd", clangd_version or "not found", clangd_path)

    print()
    print("Debug Tools")

    _print_tool(
        "OpenOCD",
        _format_summary(openocd_source, openocd_version, "not found"),
        openocd_path,
    )
    _print_tool(
        "pyOCD",
        pyocd_version or "not installed",
        f"{sys.executable} -m pyocd" if pyocd_version else None,
    )
    _print_tool(
        "pyserial",
        pyserial_version or "not installed",
        sys.executable if pyserial_version else None,
    )
    _print_tool(
        "J-Link",
        _format_summary(jlink_source, jlink_version, "configured" if jlink_path else "not found"),
        jlink_path,
    )
    _print_tool(
        "JLinkGDB",
        _format_summary(jlink_source, jlink_version, "not configured" if not jlink_gdbserver else "configured"),
        jlink_gdbserver,
    )
    _print_tool("CubeProg", cubeprog_version or "not found", cubeprog_path)


if __name__ == "__main__":
    main()
