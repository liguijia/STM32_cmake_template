#!/usr/bin/env python3
"""
tools/scripts/new_project.py
Create a new STM32 project directory next to this template.

The new project reuses the toolchain, OpenOCD, and J-Link already downloaded
in the template — no re-downloading required.  Each tool's env.mk is copied
from the template with $(CURDIR) replaced by the template's absolute path, so
Make resolves the binaries correctly even though they live outside the project.

Usage:
    python tools/scripts/new_project.py <project-name>
    make new-project NAME=my_blinky

The new directory is created at  ../project-name/  relative to this template.
After creation:
  1. Point STM32CubeMX at the new directory and generate CMake code.
  2. Open <project-name>.code-workspace in VS Code.
  3. Run  make gen-openocd-cfg  (generates .openocd/target.cfg, downloads SVD).
  4. Ctrl+Shift+B to build, F5 to debug.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
TEMPLATE_ROOT = Path(__file__).resolve().parent.parent.parent

# ── Items copied verbatim from template → new project ─────────────────────────
COPY_ITEMS = [
    "Makefile",
    ".clangd",
    ".gitignore",
    ".vscode",
    "tools/scripts",
    "project.code-workspace",
    "user",
]

# ── env.mk files whose $(CURDIR) references must be redirected ─────────────────
TOOL_ENV_MKS = [
    "tools/toolchain/env.mk",
    "tools/openocd/env.mk",
    "tools/jlink/env.mk",
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def _redirect_env_mk(src: Path, dst: Path, template_posix: str) -> None:
    """
    Copy src → dst, replacing every $(CURDIR) with the template's absolute path.

    Both get_toolchain.py and get_openocd.py write:
        override PREFIX  := $(CURDIR)/<rel>/arm-none-eabi-
        override OPENOCD := $(CURDIR)/<rel>/openocd[.exe]
    Replacing $(CURDIR) makes those paths point at the shared template tools
    regardless of where the new project lives.
    """
    text = src.read_text(encoding="utf-8")
    redirected = text.replace("$(CURDIR)", template_posix)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(redirected, encoding="utf-8")


def _tool_name(rel: str) -> str:
    """'tools/openocd/env.mk'  →  'openocd'"""
    return Path(rel).parent.name


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a new STM32 project that shares tools from this template"
    )
    parser.add_argument(
        "name",
        help="Project name — a directory with this name is created next to the template",
    )
    args = parser.parse_args()

    name = args.name.strip()
    if not name:
        parser.error("project name cannot be empty")

    if re.search(r'[\\/:*?"<>|]', name):
        parser.error(f"project name contains invalid characters: {name!r}")

    project_dir = TEMPLATE_ROOT.parent / name
    if project_dir.exists():
        print(f"[Error] Already exists: {project_dir}")
        sys.exit(1)

    # Use POSIX separators — Make on Windows accepts forward slashes.
    template_posix = TEMPLATE_ROOT.as_posix()

    print(f"  Template     {TEMPLATE_ROOT}")
    print(f"  New project  {project_dir}")
    print()

    project_dir.mkdir()

    # ── 1. Copy template files ─────────────────────────────────────────────────
    for item in COPY_ITEMS:
        src = TEMPLATE_ROOT / item
        if not src.exists():
            print(f"  [skip]   {item}")
            continue
        _copy(src, project_dir / item)
        print(f"  [copy]   {item}")

    # ── 2. Redirect env.mk → template tools ───────────────────────────────────
    for rel in TOOL_ENV_MKS:
        src = TEMPLATE_ROOT / rel
        dst = project_dir / rel
        tool = _tool_name(rel)
        if src.exists():
            _redirect_env_mk(src, dst, template_posix)
            print(f"  [env.mk] {rel}  (→ {TEMPLATE_ROOT.name})")
        else:
            # Tool not yet downloaded in the template — write a helpful stub.
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(
                f"# {rel}\n"
                f"# '{tool}' has not been set up in the template yet.\n"
                f"# Run the following in  {TEMPLATE_ROOT} :\n"
                f"#   make setup-{tool}\n"
                f"# then delete this file and re-run new_project.py,\n"
                f"# or copy the generated env.mk manually.\n",
                encoding="utf-8",
            )
            print(f"  [stub]   {rel}  (run 'make setup-{tool}' in template first)")

    # ── 3. Rename workspace file ───────────────────────────────────────────────
    ws_src = project_dir / "project.code-workspace"
    ws_dst = project_dir / f"{name}.code-workspace"
    if ws_src.exists():
        ws_src.rename(ws_dst)
        print(f"  [rename] project.code-workspace → {name}.code-workspace")

    # ── Done ───────────────────────────────────────────────────────────────────
    print()
    print("Done!  Next steps:")
    print(f"  1. Open STM32CubeMX → set Toolchain/IDE = CMake")
    print(f"     → generate code into:  {project_dir}")
    print(f"  2. Open  {ws_dst.name}  in VS Code")
    print(f"  3. Add your source files to  user/Src/  and  user/Inc/")
    print(f"     then reference them in CMakeLists.txt (target_sources / include_directories)")
    print(f"  4. Run in VS Code terminal:  make gen-openocd-cfg")
    print(f"       generates .openocd/target.cfg, downloads SVD, updates launch.json")
    print(f"  5. Ctrl+Shift+B  to build  |  F5  to debug")


if __name__ == "__main__":
    main()
