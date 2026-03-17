#!/usr/bin/env python3
"""
Fallback network helpers that use curl/curl.exe when Python's TLS stack fails.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path


def _curl_path() -> str:
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl is not available in PATH")
    return curl


def _append_headers(cmd: list[str], headers: dict[str, str] | None) -> None:
    if not headers:
        return
    for key, value in headers.items():
        cmd.extend(["-H", f"{key}: {value}"])


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _speed_options() -> tuple[int, int]:
    # Keep a conservative default so very slow links still succeed while
    # genuinely stalled transfers eventually abort.
    return (
        _env_int("STM32_DOWNLOAD_MIN_SPEED", 4 * 1024),
        _env_int("STM32_DOWNLOAD_STALL_TIME", 120),
    )


def _base_cmd(
    *,
    proxy: str | None = None,
    timeout: int = 0,
    silent: bool = False,
) -> list[str]:
    cmd = [
        _curl_path(),
        "-fL",
        "--retry",
        "3",
        "--retry-delay",
        "1",
        "--connect-timeout",
        "20",
    ]
    if silent:
        cmd.append("-sS")
    if timeout > 0:
        cmd.extend(["--max-time", str(timeout)])
    if proxy:
        cmd.extend(["--proxy", proxy])
    if platform.system() == "Windows":
        cmd.append("--ssl-no-revoke")
    return cmd


def fetch_bytes(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    proxy: str | None = None,
    timeout: int = 20,
) -> bytes:
    cmd = _base_cmd(proxy=proxy, timeout=timeout, silent=True)
    _append_headers(cmd, headers)
    cmd.append(url)

    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or f"curl exited with code {result.returncode}")
    return result.stdout


def download_file(
    url: str,
    dest: Path,
    *,
    headers: dict[str, str] | None = None,
    proxy: str | None = None,
    timeout: int = 0,
) -> None:
    tmp = Path(str(dest) + ".part")
    speed_limit, speed_time = _speed_options()
    cmd = _base_cmd(proxy=proxy, timeout=timeout, silent=False)
    if speed_limit > 0 and speed_time > 0:
        cmd.extend(["--speed-limit", str(speed_limit), "--speed-time", str(speed_time)])
    _append_headers(cmd, headers)
    if tmp.exists() and tmp.stat().st_size > 0:
        size_mb = tmp.stat().st_size / 1_048_576
        print(
            f"  resuming single-connection download from {tmp.name} "
            f"({size_mb:.1f} MB)"
        )
        cmd.extend(["-C", "-"])
    cmd.extend(["-o", str(tmp), url])

    result = subprocess.run(cmd)
    if result.returncode != 0:
        detail = f"curl exited with code {result.returncode}"
        if result.returncode == 28 and speed_limit > 0 and speed_time > 0:
            detail += (
                f" (transfer timed out or stayed below {speed_limit // 1024} KiB/s "
                f"for {speed_time}s)"
            )
        if tmp.exists() and tmp.stat().st_size > 0:
            size_mb = tmp.stat().st_size / 1_048_576
            detail += (
                f"; kept partial download {tmp.name} ({size_mb:.1f} MB), "
                "rerun will resume"
            )
        raise RuntimeError(detail)

    if not tmp.exists():
        raise RuntimeError(f"Download failed: {tmp} was not created")

    tmp.replace(dest)
