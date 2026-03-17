#!/usr/bin/env python3
"""
gen_openocd_cfg.py — Generate .openocd/target.cfg and download SVD from project sources.

Reads:
  *.ioc  →  Mcu.Family  →  OpenOCD target config file name
           Mcu.CPN     →  SVD file search key
  *.ld   →  RAM LENGTH  →  safe WORKAREASIZE

Writes:
  .openocd/target.cfg   (TCL snippet; sourced by Cortex-Debug / OpenOCD)
  <MCU>x.svd            (CMSIS-SVD peripheral description; enables register view)
  .vscode/launch.json   (device / executable / svdFile updated automatically)

Run directly or via 'make gen-openocd-cfg'.
"""

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from net_fallback import fetch_bytes as curl_fetch_bytes

# ---------------------------------------------------------------------------
# OpenOCD target config file names (relative to OpenOCD scripts/target/).
# Most families use the '<family>x.cfg' pattern; exceptions are listed here.
# ---------------------------------------------------------------------------
_FAMILY_NO_X_SUFFIX = {"stm32l0", "stm32mp1"}

# GitHub raw base URL for cmsis-svd-data
# The vendor directory is "STMicro" (not "STMicroelectronics")
_SVD_RAW_BASE = (
    "https://raw.githubusercontent.com/cmsis-svd/cmsis-svd-data"
    "/main/data/STMicro"
)
_SVD_API_URL = (
    "https://api.github.com/repos/cmsis-svd/cmsis-svd-data"
    "/contents/data/STMicro"
)

# ---------------------------------------------------------------------------

def _parse_mcu_family(ioc_path: Path) -> "str | None":
    """Return lowercased Mcu.Family from a CubeMX .ioc file (e.g. 'stm32f3')."""
    text = ioc_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^Mcu\.Family\s*=\s*(\S+)", text, re.MULTILINE)
    return m.group(1).lower() if m else None


def _parse_mcu_cpn(ioc_path: Path) -> "str | None":
    """Return Mcu.CPN from .ioc (e.g. 'STM32F334R8T6')."""
    text = ioc_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^Mcu\.CPN\s*=\s*(\S+)", text, re.MULTILINE)
    return m.group(1) if m else None


def _project_name_from_ioc(ioc_path: Path) -> str:
    """CubeMX project name is the .ioc stem."""
    return ioc_path.stem


def _jlink_device_name(cpn: str) -> str:
    """
    Convert a CubeMX full part number to the shorter J-Link device name.

    Examples:
      STM32F334R8T6 -> STM32F334R8
      STM32L476RGTx -> STM32L476RG
      STM32H743VITx -> STM32H743VI
    """
    match = re.match(r"(STM32[A-Z0-9]+?)(?:[A-Z]\d|[A-Z]x)$", cpn, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    if cpn.upper().startswith("STM32") and len(cpn) > 2:
        return cpn[:-2].upper()
    return cpn.upper()


def _parse_mcu_family_from_ld(ld_path: Path) -> "str | None":
    """Fallback: derive family from linker script name (e.g. STM32F334XX → stm32f3)."""
    m = re.match(r"(STM32[A-Z]\d)", ld_path.stem, re.IGNORECASE)
    return m.group(1).lower() if m else None


def _parse_ram_bytes(ld_path: Path) -> "int | None":
    """Return RAM size in bytes from the MEMORY block of a linker script."""
    text = ld_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(
        r"(?m)^\s*RAM\b[^:]*:.*?LENGTH\s*=\s*(\d+)\s*([KkMm]?)",
        text,
    )
    if not m:
        return None
    value, unit = int(m.group(1)), (m.group(2) or "").upper()
    return value * (1024 if unit == "K" else 1_048_576 if unit == "M" else 1)


def _work_area_bytes(ram_bytes: int) -> int:
    """
    Compute a safe OpenOCD WORKAREASIZE:
      - Leave 2 KB headroom for the flash algorithm's own stack.
      - Cap at 16 KB (flash algorithms don't need more).
      - Round down to a 1 KB boundary.
    """
    raw = min(ram_bytes - 2 * 1024, 16 * 1024)
    raw = max(raw, 1024)
    return (raw // 1024) * 1024


def _target_cfg_name(family: str) -> str:
    """Return the OpenOCD target/<name>.cfg stem for the given MCU family."""
    return family if family in _FAMILY_NO_X_SUFFIX else family + "x"


def _mcu_series_prefix(cpn: str) -> str:
    """
    Derive the MCU series prefix used for SVD filename matching.

    Examples:
      STM32F334R8T6  →  STM32F334   (pin=R, flash=8, pkg=T, temp=6)
      STM32L476RGTx  →  STM32L476
      STM32H743VITx  →  STM32H743
      STM32L4R5VITx  →  STM32L4R5  (special: sub-family has alpha chars)

    Strategy: split at the first uppercase letter that is immediately
    followed by a digit AND is preceded by at least one digit — this
    marks the start of the pin-count/package field.
    """
    m = re.match(r"(STM32[A-Z0-9]+?)(?=[A-Z]\d)", cpn, re.IGNORECASE)
    return m.group(1).upper() if m else cpn[:8].upper()


def _fetch_url(url: str, timeout: int = 15) -> "bytes | None":
    """Fetch *url*, return bytes on success or None on any error."""
    try:
        # NOTE: do NOT send a custom Accept header — it triggers SSL handshake
        # failures in Python's urllib on some Windows TLS stacks.
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "gen-openocd-cfg/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        try:
            return curl_fetch_bytes(
                url,
                headers={"User-Agent": "gen-openocd-cfg/1.0"},
                timeout=timeout,
            )
        except RuntimeError:
            return None


def _svd_matches_series(svd_stem: str, series: str) -> bool:
    """
    Check if an SVD filename stem matches the MCU series, treating 'x'/'X'
    as a single-character wildcard.

    Examples:
      STM32F3x4  matches  STM32F334  (x→3, 4==4)  ✓
      STM32F3x8  matches  STM32F334  (x→3, 8≠4)   ✗
      STM32F334  matches  STM32F334                ✓
    """
    pat = re.escape(svd_stem).replace(r"x", ".").replace(r"X", ".") + "$"
    return bool(re.match(pat, series, re.IGNORECASE))


def download_svd(root: Path, cpn: str) -> "Path | None":
    """
    Download the CMSIS-SVD file for *cpn* (e.g. 'STM32F334R8T6') into *root*.

    Strategy:
      1. Check if any *.svd already exists on disk for this series.
      2. List the vendor directory via GitHub Contents API.
      3. Score candidates with wildcard matching (STM32F3x4 matches STM32F334).
      4. Download the best match.

    Returns the local Path on success, or None if unreachable / not found.
    """
    series = _mcu_series_prefix(cpn)   # e.g. STM32F334

    # ── Already on disk? ─────────────────────────────────────────────────
    for p in root.glob("*.svd"):
        if _svd_matches_series(p.stem, series):
            print(f"  SVD     already present: {p.name}")
            return p

    print(f"  SVD     searching for {series} in cmsis-svd-data …")

    # ── List directory via GitHub Contents API ────────────────────────────
    data = _fetch_url(_SVD_API_URL, timeout=15)
    if not data:
        return None

    try:
        files = json.loads(data.decode("utf-8"))
    except Exception:
        return None

    # Score each SVD file against the series name
    matches = []
    for f in files:
        if not isinstance(f, dict) or not f.get("name", "").endswith(".svd"):
            continue
        stem = f["name"][:-4]   # strip .svd
        if _svd_matches_series(stem, series):
            # Prefer exact match over wildcard; shorter name = more specific
            score = (0 if "x" not in stem.lower() else 1, len(stem))
            matches.append((score, f))

    if not matches:
        return None

    best = sorted(matches, key=lambda x: x[0])[0][1]
    dest = root / best["name"]

    if not dest.exists():
        # Derive raw URL from the API entry's path
        raw_url = f"{_SVD_RAW_BASE}/{best['name']}"
        raw = _fetch_url(raw_url, timeout=30)
        if not raw:
            return None
        dest.write_bytes(raw)

    print(f"  SVD     downloaded: {best['name']}")
    return dest


def update_launch_json(
    root: Path,
    *,
    executable_rel: "str | None" = None,
    jlink_device: "str | None" = None,
    svd_path: "Path | None" = None,
) -> None:
    """
    Update launch.json fields managed by the template scripts.
    """
    launch = root / ".vscode" / "launch.json"
    if not launch.exists():
        return

    text = launch.read_text(encoding="utf-8")
    svd_rel = svd_path.relative_to(root).as_posix() if svd_path is not None else None
    executable_val = (
        f"${{workspaceFolder}}/{executable_rel}"
        if executable_rel is not None
        else None
    )

    out_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        active = stripped if not stripped.startswith("//") else ""
        indent = len(line) - len(line.lstrip())

        if stripped.startswith("//") and "svdFile" in stripped:
            continue

        if active.startswith('"device"') and jlink_device is not None:
            out_lines.append(" " * indent + f'"device": "{jlink_device}",\n')
            continue

        if active.startswith('"executable"') and executable_val is not None:
            out_lines.append(" " * indent + f'"executable": "{executable_val}",\n')
            continue

        if active.startswith('"svdFile"'):
            continue

        if active.startswith('"liveWatch"') and svd_rel is not None:
            livewatch_line = line.rstrip()
            if not livewatch_line.endswith(","):
                livewatch_line += ","
            out_lines.append(livewatch_line + "\n")
            out_lines.append(" " * indent + f'"svdFile": "${{workspaceFolder}}/{svd_rel}"\n')
            continue

        out_lines.append(line)

    launch.write_text("".join(out_lines), encoding="utf-8")

    updates = []
    if executable_rel is not None:
        updates.append(f"executable -> {executable_rel}")
    if jlink_device is not None:
        updates.append(f"device -> {jlink_device}")
    if svd_rel is not None:
        updates.append(f"svdFile -> {svd_rel}")
    if updates:
        print(f"  launch.json  {', '.join(updates)}")
    return

    launch = root / ".vscode" / "launch.json"
    if not launch.exists():
        return

    text = launch.read_text(encoding="utf-8")
    svd_rel = svd_path.relative_to(root).as_posix() if svd_path is not None else None
    executable_val = (
        f"${{workspaceFolder}}/{executable_rel}"
        if executable_rel is not None
        else None
    )

    out_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip().rstrip(",")

        # Replace existing active svdFile value
        if stripped.startswith('"svdFile"'):
            indent = len(line) - len(line.lstrip())
            out_lines.append(" " * indent + f'"svdFile": "{val}"\n')
            continue

        out_lines.append(line)

        # After a liveWatch line, insert svdFile if not already following
        if '"liveWatch"' in line:
            out_lines.append(f'            "svdFile": "{val}"\n')

    # 3. Remove accidental duplicate svdFile lines
    seen_svd  = False
    final: list[str] = []
    for line in out_lines:
        if '"svdFile"' in line and not line.lstrip().startswith("//"):
            if seen_svd:
                continue   # drop duplicate
            seen_svd = True
            # Reset for next configuration block
        if '"name"' in line and '"type"' not in line:
            seen_svd = False
        final.append(line)

    launch.write_text("".join(final), encoding="utf-8")
    print(f"  launch.json  svdFile → {rel}")


# ---------------------------------------------------------------------------

def generate(root: Path = Path("."), skip_svd: bool = False) -> Path:
    """Generate .openocd/target.cfg (and optionally download SVD)."""

    # ── Locate source files ─────────────────────────────────────────────────
    ioc_files = sorted(root.glob("*.ioc"))
    ld_files  = sorted(root.glob("*.ld"))

    if not ld_files:
        sys.exit("ERROR: No *.ld linker script found in project root.")

    ld_path = ld_files[0]

    # ── MCU family ──────────────────────────────────────────────────────────
    family = None
    if ioc_files:
        family = _parse_mcu_family(ioc_files[0])
        if family:
            print(f"  .ioc    Mcu.Family = {family}")

    if not family:
        family = _parse_mcu_family_from_ld(ld_path)
        if family:
            print(f"  {ld_path.name}  family = {family}  (fallback)")

    if not family:
        sys.exit(f"ERROR: Cannot determine MCU family from {ld_path.name} or *.ioc.")

    target_name = _target_cfg_name(family)

    # ── RAM size ────────────────────────────────────────────────────────────
    ram_bytes = _parse_ram_bytes(ld_path)
    if ram_bytes is None:
        sys.exit(f"ERROR: Cannot parse RAM LENGTH from {ld_path.name}.")

    print(f"  {ld_path.name}  RAM = {ram_bytes // 1024} KB")

    work_area = _work_area_bytes(ram_bytes)
    print(f"  WORKAREASIZE = {hex(work_area)}  ({work_area // 1024} KB)")

    # ── Generate .openocd/target.cfg ────────────────────────────────────────
    out_dir = root / ".openocd"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "target.cfg"

    ioc_note = ioc_files[0].name if ioc_files else ld_path.name
    content = (
        f"# Auto-generated by tools/scripts/gen_openocd_cfg.py — do not edit manually.\n"
        f"# Source : {ioc_note}  |  {ld_path.name}\n"
        f"# MCU    : {family.upper()}   RAM = {ram_bytes // 1024} KB\n"
        f"#\n"
        f"# WORKAREASIZE is reduced from the OpenOCD default to stay within the SRAM\n"
        f"# boundary: RAM - 2 KB = {(ram_bytes - 2048) // 1024} KB,"
        f" capped at 16 KB → {work_area // 1024} KB.\n"
        f"set WORKAREASIZE {hex(work_area)}\n"
        f"\n"
        f"source [find target/{target_name}.cfg]\n"
    )
    out_path.write_text(content, encoding="utf-8")
    print(f"  Written: {out_path.relative_to(root)}")

    launch_svd_path = None
    launch_executable = None
    launch_device = None
    if ioc_files:
        launch_executable = f"build/Debug/{_project_name_from_ioc(ioc_files[0])}.elf"
        cpn = _parse_mcu_cpn(ioc_files[0])
        if cpn:
            launch_device = _jlink_device_name(cpn)

    # ── SVD download ────────────────────────────────────────────────────────
    if not skip_svd and ioc_files:
        cpn = _parse_mcu_cpn(ioc_files[0])
        if cpn:
            launch_svd_path = download_svd(root, cpn)
            if not launch_svd_path:
                print("  SVD     not found online — skipping (peripheral view unavailable)")
        else:
            print("  SVD     Mcu.CPN not found in .ioc — skipping")

    if ioc_files:
        update_launch_json(
            root,
            executable_rel=launch_executable,
            jlink_device=launch_device,
            svd_path=launch_svd_path,
        )

    return out_path


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate .openocd/target.cfg and download SVD from *.ioc / *.ld",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Project root directory (default: current directory)",
    )
    parser.add_argument(
        "--no-svd",
        action="store_true",
        help="Skip SVD download (offline / CI use)",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    print(f"Project: {root}")
    out = generate(root, skip_svd=args.no_svd)
    print(f"Done → {out.relative_to(root)}")


if __name__ == "__main__":
    main()
