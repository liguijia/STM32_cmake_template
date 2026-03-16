#!/usr/bin/env python3
"""
tools/scripts/parse_ldscript.py
Print FLASH and RAM sizes (bytes, space-separated) from the project's linker
script.  Called by the Makefile to populate the build summary.

Output: <flash_bytes> <ram_bytes>
        0 on either field means "not found" — Makefile uses its fallback.

Usage: python tools/scripts/parse_ldscript.py [project_root]
"""

import re
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
ld   = next(root.glob("*.ld"), None)

if not ld:
    print("0 0")
    sys.exit(0)

text = ld.read_text(encoding="utf-8", errors="replace")


def parse_bytes(pattern: str) -> int:
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    if not m:
        return 0
    value, unit = int(m.group(1)), (m.group(2) or "").upper()
    return value * (1024 if unit == "K" else 1_048_576 if unit == "M" else 1)


flash = parse_bytes(r"^\s*FLASH\b[^:]*:.*?LENGTH\s*=\s*(\d+)\s*([KkMm]?)")
ram   = parse_bytes(r"^\s*RAM\b[^:]*:.*?LENGTH\s*=\s*(\d+)\s*([KkMm]?)")

print(flash, ram)
