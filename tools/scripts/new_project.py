#!/usr/bin/env python3
"""
tools/scripts/new_project.py
Create a new STM32 project directory next to this template.

The new project inherits the toolchain selection from this template. If the
template uses a downloaded local toolchain, the generated project redirects its
env.mk back to the template copy. If the template uses the system toolchain,
the generated project keeps using arm-none-eabi-* from PATH. OpenOCD and
J-Link continue to point at the template's downloaded debug tools.

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

DEBUG_SETTINGS_KEYS = (
    "cortex-debug.openocdPath",
    "cortex-debug.JLinkGDBServerPath",
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def _redirect_env_mk(src: Path, dst: Path, template_posix: str) -> bool:
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
    return "$(CURDIR)" in text


def _tool_name(rel: str) -> str:
    """'tools/openocd/env.mk'  →  'openocd'"""
    return Path(rel).parent.name


def _extract_json_string(text: str, key: str) -> "str | None":
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]*)"', text)
    return match.group(1) if match else None


def _replace_json_string(text: str, key: str, value: str) -> str:
    pattern = rf'("{re.escape(key)}"\s*:\s*)"[^"]*"'
    if re.search(pattern, text):
        return re.sub(pattern, lambda m: f'{m.group(1)}"{value}"', text)

    stripped = text.rstrip()
    if stripped.endswith("}"):
        return stripped[:-1].rstrip().rstrip(",") + f',\n    "{key}": "{value}"\n}}\n'

    return text


def _patch_shared_debug_settings(project_dir: Path, template_posix: str) -> bool:
    """
    Rewrite copied VS Code debug tool paths to point back at the template.
    """
    template_settings = TEMPLATE_ROOT / ".vscode" / "settings.json"
    project_settings = project_dir / ".vscode" / "settings.json"
    if not template_settings.exists() or not project_settings.exists():
        return False

    template_text = template_settings.read_text(encoding="utf-8")
    project_text = project_settings.read_text(encoding="utf-8")
    changed = False

    for key in DEBUG_SETTINGS_KEYS:
        value = _extract_json_string(template_text, key)
        if not value:
            continue

        value = value.replace("\\", "/")
        if value.startswith("${workspaceFolder}/"):
            value = f"{template_posix}/{value[len('${workspaceFolder}/'):]}"

        updated = _replace_json_string(project_text, key, value)
        if updated != project_text:
            project_text = updated
            changed = True

    if changed:
        project_settings.write_text(project_text, encoding="utf-8")

    return changed


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
            redirected = _redirect_env_mk(src, dst, template_posix)
            if tool == "toolchain":
                detail = (
                    f"shared from {TEMPLATE_ROOT.name}"
                    if redirected
                    else "uses system Arm GNU Toolchain from PATH"
                )
            else:
                detail = (
                    f"shared from {TEMPLATE_ROOT.name}"
                    if redirected
                    else f"uses system {tool} from PATH"
                )
            print(f"  [env.mk] {rel}  ({detail})")
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
    if _patch_shared_debug_settings(project_dir, template_posix):
        print("  [vscode] .vscode/settings.json  (shared debug tool paths from template)")

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
