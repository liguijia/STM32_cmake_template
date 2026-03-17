#!/usr/bin/env python3
"""
Remove downloaded tools and generated setup artifacts from the workspace.

By default this script only cleans project-local state so the repository goes
back to its freshly-cloned shape. Use --python-tools to also uninstall pyocd
and pyserial from the current Python interpreter.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent

TOOL_GLOBS = (
    "tools/toolchain/arm-gnu-toolchain-*",
    "tools/openocd/xpack-openocd-*",
    "tools/jlink/JLink_*",
)
GENERATED_FILES = (
    "tools/toolchain/env.mk",
    "tools/openocd/env.mk",
    "tools/jlink/env.mk",
    "compile_commands.json",
    ".openocd/target.cfg",
)
OPTIONAL_DIRS = (
    "build",
    "tools/scripts/__pycache__",
)
EMPTY_DIRS = (
    ".openocd",
    "tools/toolchain",
    "tools/openocd",
    "tools/jlink",
)
SETTINGS_KEYS = (
    "cortex-debug.JLinkGDBServerPath",
    "cortex-debug.openocdPath",
)


def info(message: str) -> None:
    print(f"[uninstall] {message}")


def warn(message: str) -> None:
    print(f"[uninstall] Warning: {message}")


def _remove_path(path: Path, *, dry_run: bool) -> bool:
    if not path.exists():
        return False
    if dry_run:
        info(f"Would remove: {path.relative_to(PROJECT_ROOT)}")
        return True
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    info(f"Removed: {path.relative_to(PROJECT_ROOT)}")
    return True


def _remove_matching_paths(*, dry_run: bool) -> int:
    removed = 0
    seen: set[Path] = set()
    for pattern in TOOL_GLOBS:
        for path in sorted(PROJECT_ROOT.glob(pattern)):
            if path in seen:
                continue
            seen.add(path)
            removed += int(_remove_path(path, dry_run=dry_run))
    for rel in GENERATED_FILES:
        removed += int(_remove_path(PROJECT_ROOT / rel, dry_run=dry_run))
    for rel in OPTIONAL_DIRS:
        removed += int(_remove_path(PROJECT_ROOT / rel, dry_run=dry_run))
    for path in sorted(PROJECT_ROOT.glob("*.svd")):
        removed += int(_remove_path(path, dry_run=dry_run))
    return removed


def _remove_empty_dirs(*, dry_run: bool) -> int:
    removed = 0
    for rel in EMPTY_DIRS:
        path = PROJECT_ROOT / rel
        if not path.exists() or not path.is_dir():
            continue
        try:
            next(path.iterdir())
            continue
        except StopIteration:
            removed += int(_remove_path(path, dry_run=dry_run))
    return removed


def _reset_settings_json(*, dry_run: bool) -> bool:
    path = PROJECT_ROOT / ".vscode" / "settings.json"
    if not path.exists():
        return False
    original = path.read_text(encoding="utf-8")
    updated = original
    for key in SETTINGS_KEYS:
        updated = re.sub(
            rf'("{re.escape(key)}"\s*:\s*)"[^"]*"',
            r'\1""',
            updated,
        )
    if updated == original:
        return False
    if dry_run:
        info("Would reset managed debugger paths in .vscode/settings.json")
        return True
    path.write_text(updated, encoding="utf-8")
    info("Reset managed debugger paths in .vscode/settings.json")
    return True


def _reset_launch_json(*, dry_run: bool) -> bool:
    path = PROJECT_ROOT / ".vscode" / "launch.json"
    if not path.exists():
        return False
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    updated_lines = [
        line
        for line in lines
        if '"svdFile"' not in line or line.lstrip().startswith("//")
    ]
    updated = "".join(updated_lines)
    if updated == original:
        return False
    if dry_run:
        info("Would remove generated svdFile entries from .vscode/launch.json")
        return True
    path.write_text(updated, encoding="utf-8")
    info("Removed generated svdFile entries from .vscode/launch.json")
    return True


def _uninstall_python_tools(*, dry_run: bool) -> bool:
    cmd = [sys.executable, "-m", "pip", "uninstall", "-y", "pyocd", "pyserial"]
    if dry_run:
        info(f"Would run: {' '.join(cmd)}")
        return True

    info("Uninstalling pyocd and pyserial from the current Python interpreter")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        warn(f"pip uninstall exited with code {result.returncode}")
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove downloaded tools and generated setup artifacts.",
    )
    parser.add_argument(
        "--python-tools",
        action="store_true",
        help="Also uninstall pyocd and pyserial from the current Python interpreter.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without changing anything.",
    )
    args = parser.parse_args()

    info(f"Project root: {PROJECT_ROOT}")

    removed_paths = _remove_matching_paths(dry_run=args.dry_run)
    touched_files = 0
    touched_files += int(_reset_settings_json(dry_run=args.dry_run))
    touched_files += int(_reset_launch_json(dry_run=args.dry_run))
    removed_paths += _remove_empty_dirs(dry_run=args.dry_run)

    python_tools_done = False
    if args.python_tools:
        python_tools_done = _uninstall_python_tools(dry_run=args.dry_run)

    print()
    if args.dry_run:
        info(
            f"Dry run complete. Workspace items to remove/reset: "
            f"{removed_paths + touched_files}"
        )
    else:
        info(
            f"Workspace cleanup complete. Removed/reset items: "
            f"{removed_paths + touched_files}"
        )
    if args.python_tools:
        if python_tools_done:
            info("Python tool package cleanup finished.")
        else:
            warn("Python tool package cleanup may be incomplete.")
    else:
        info("Python packages were left untouched. Re-run with --python-tools if needed.")

    if args.python_tools and not args.dry_run and not python_tools_done:
        sys.exit(1)


if __name__ == "__main__":
    main()
