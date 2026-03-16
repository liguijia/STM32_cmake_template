#!/usr/bin/env python3
"""
tools/scripts/build_summary.py — Build summary renderer

Called by the Makefile `summary` target.  Displays a boxed summary of
the build configuration, artifact sizes and memory usage.

Usage (from Makefile):
    python tools/scripts/build_summary.py \
        --project my_blinky --mcu STM32F334R8 --preset Debug \
        --elf build/Debug/my_blinky.elf --hex ... --bin ... \
        --size-tool arm-none-eabi-size \
        --flash-size 65536 --ram-size 12288 \
        --gcc-ver "..." --cmake-ver "..." --ninja-ver "..." \
        --prefix arm-none-eabi-
"""

import argparse
import os
import re
import subprocess
import sys

# ── ANSI ──────────────────────────────────────────────────────────────────────

ESC   = "\033"
BOLD  = f"{ESC}[1m"
DIM   = f"{ESC}[2m"
RESET = f"{ESC}[0m"
GREEN = f"{ESC}[1;32m"
CYAN  = f"{ESC}[1;36m"
YELLOW = f"{ESC}[1;33m"
RED   = f"{ESC}[1;31m"
WHITE = f"{ESC}[1;97m"

W = 68  # box width (including border chars)


# ── Box drawing ───────────────────────────────────────────────────────────────

def _vlen(s: str) -> int:
    """Visible length of a string, ignoring ANSI escapes."""
    return len(re.sub(r"\x1b\[[0-9;]*m", "", s))


def row(content: str, indent: int = 2) -> str:
    """A single row inside the box, padded to width W."""
    c = " " * indent + content
    pad = W - 2 - _vlen(c)
    if pad < 0:
        vis = re.sub(r"\x1b\[[0-9;]*m", "", c)
        c = vis[: W - 5] + "..."
        pad = 0
    return f"{CYAN}|{RESET}{c}{' ' * pad}{CYAN}|{RESET}"


def div(title: str = "") -> str:
    """Section divider.  Empty title → top/bottom border."""
    if title:
        inner = f"-- {title} "
        return f"{BOLD}{CYAN}+{inner}{'-' * max(0, W - 2 - len(inner))}+{RESET}"
    return f"{BOLD}{CYAN}+{'=' * (W - 2)}+{RESET}"


def membar(used: int, total: int, bw: int = 22) -> str:
    """ASCII progress bar: [####............]"""
    if total <= 0:
        return DIM + "." * bw + RESET
    fi = min(used * bw // total, bw)
    return GREEN + "#" * fi + RESET + DIM + "." * (bw - fi) + RESET


def filesize(path: str) -> str:
    """Human-readable file size."""
    try:
        n = os.path.getsize(path)
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f} KB"
        return f"{n / (1024 * 1024):.2f} MB"
    except OSError:
        return "—"


# ── Size parsing ──────────────────────────────────────────────────────────────

def parse_size(size_tool: str, elf: str):
    """Run ``size --format=berkeley`` and return (text, data, bss)."""
    try:
        r = subprocess.run(
            [size_tool, "--format=berkeley", elf],
            capture_output=True, text=True,
        )
        parts = r.stdout.strip().split()
        return int(parts[6]), int(parts[7]), int(parts[8])
    except Exception:
        return 0, 0, 0


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="STM32 build summary renderer")
    ap.add_argument("--project",    default="unknown")
    ap.add_argument("--mcu",        default="")
    ap.add_argument("--preset",     default="Debug")
    ap.add_argument("--gcc-ver",    default="")
    ap.add_argument("--cmake-ver",  default="")
    ap.add_argument("--ninja-ver",  default="")
    ap.add_argument("--prefix",     default="arm-none-eabi-")
    ap.add_argument("--elf",        default="")
    ap.add_argument("--hex",        default="")
    ap.add_argument("--bin",        default="")
    ap.add_argument("--size-tool",  default="arm-none-eabi-size")
    ap.add_argument("--flash-size", type=int, default=0)
    ap.add_argument("--ram-size",   type=int, default=0)
    ap.add_argument("--status",     type=int, default=0)
    ap.add_argument("--build-log",  default="")
    args = ap.parse_args()

    # ── Memory analysis ───────────────────────────────────────────────────
    text, data, bss = 0, 0, 0
    if args.elf and os.path.isfile(args.elf):
        text, data, bss = parse_size(args.size_tool, args.elf)
    flash_used = text + data
    ram_used   = data + bss

    gcc = args.gcc_ver
    if len(gcc) > 54:
        gcc = gcc[:54] + "..."

    # ── Render ────────────────────────────────────────────────────────────
    print()
    print(div())
    print(row(f"{BOLD}BUILD SUMMARY{RESET}", indent=(W - 2 - 13) // 2))

    # Toolchain
    print(div("Toolchain"))
    print(row(f"GCC    {GREEN}{gcc or 'not found'}{RESET}"))
    print(row(f"CMake  {GREEN}{args.cmake_ver or 'not found'}{RESET}"))
    print(row(f"Ninja  {GREEN}{'ninja ' + args.ninja_ver if args.ninja_ver else 'not found'}{RESET}"))
    print(row(f"Prefix {GREEN}{args.prefix}{RESET}"))

    # Build
    print(div("Build"))
    print(row(f"MCU      {BOLD}{args.mcu or args.project}{RESET}"))
    print(row(f"Project  {BOLD}{args.project}{RESET}"))
    print(row(f"Preset   {BOLD}{args.preset}{RESET}"))

    # Artifacts
    print(div("Artifacts"))
    for label, path in [("ELF", args.elf), ("HEX", args.hex), ("BIN", args.bin)]:
        if path and os.path.isfile(path):
            print(row(f"{label}  {BOLD}{path}{RESET}  {DIM}({filesize(path)}){RESET}"))
        elif path:
            print(row(f"{label}  {path}  {DIM}(missing){RESET}"))

    # Memory
    if args.flash_size > 0 and args.ram_size > 0:
        print(div(f"Memory  {args.flash_size} B Flash / {args.ram_size} B RAM"))
        fp = flash_used * 100 / args.flash_size
        rp = ram_used * 100 / args.ram_size
        print(row(
            f"Flash  {BOLD}{flash_used:>6}{RESET} / {args.flash_size} B"
            f"  {BOLD}{fp:5.1f}%{RESET}  [{membar(flash_used, args.flash_size)}]"
        ))
        print(row(
            f"RAM    {BOLD}{ram_used:>6}{RESET} / {args.ram_size} B"
            f"  {BOLD}{rp:5.1f}%{RESET}  [{membar(ram_used, args.ram_size)}]"
        ))

    # Status
    if args.status != 0:
        print(div("Status"))
        print(row(f"{RED}BUILD FAILED  (exit={args.status}){RESET}"))
        if args.build_log and os.path.isfile(args.build_log):
            with open(args.build_log) as f:
                lines = f.readlines()
            errors = [
                l.rstrip() for l in lines
                if re.search(
                    r"error:|cmake error|failed|undefined reference"
                    r"|ninja: build stopped",
                    l, re.I,
                )
            ]
            if errors:
                print(div("Errors"))
                for e in errors[-10:]:
                    print(row(f"{RED}{e[:W - 6]}{RESET}"))
            else:
                print(div("Last output"))
                for l in lines[-20:]:
                    print(row(l.rstrip()[: W - 6]))

    print(div())
    print()


if __name__ == "__main__":
    main()
