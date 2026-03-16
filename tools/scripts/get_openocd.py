#!/usr/bin/env python3
"""
tools/scripts/get_openocd.py
Download and manage xPack OpenOCD for embedded development.

Source: https://github.com/xpack-binaries/openocd/releases
Installs into tools/openocd/ and writes tools/openocd/env.mk so the
Makefile picks up the local binary automatically via -include.

Usage:
    python tools/scripts/get_openocd.py              # interactive version picker
    python tools/scripts/get_openocd.py --latest     # auto-select latest version
    python tools/scripts/get_openocd.py --list       # list local + online versions
    python tools/scripts/get_openocd.py --source system  # switch to system openocd
    python tools/scripts/get_openocd.py --source local   # switch to local openocd
    python tools/scripts/get_openocd.py --proxy <url>    # use HTTP/HTTPS proxy
    python tools/scripts/get_openocd.py --keep-archive   # keep archive after install
    python tools/scripts/get_openocd.py --no-patch-makefile
"""

import argparse
import json
import os
import platform
import re
import stat
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
OPENOCD_DIR  = PROJECT_ROOT / "tools" / "openocd"
ENV_MK       = OPENOCD_DIR / "env.mk"

GITHUB_API   = "https://api.github.com/repos/xpack-dev-tools/openocd-xpack/releases?per_page=20"
INCLUDE_LINE = "-include tools/openocd/env.mk"

# ── Colours ────────────────────────────────────────────────────────────────────
_USE_COLOR = sys.stdout.isatty() and (
    platform.system() != "Windows"
    or os.environ.get("TERM") is not None
    or os.environ.get("WT_SESSION") is not None
    or os.environ.get("ANSICON") is not None
)


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def info(msg: str)  -> None: print(_c("1;36", "[info]"),  msg)
def ok(msg: str)    -> None: print(_c("1;32", "[ok]"),    msg)
def warn(msg: str)  -> None: print(_c("1;33", "[warn]"),  msg)
def error(msg: str) -> None: print(_c("1;31", "[error]"), msg)


# ── Platform detection ─────────────────────────────────────────────────────────
def detect_platform() -> tuple[str, str]:
    """
    Return (asset_suffix, ext) matching xPack OpenOCD asset names.

    xPack asset naming:
      Windows:      xpack-openocd-<ver>-win32-x64.zip
      Linux x64:    xpack-openocd-<ver>-linux-x64.tar.gz
      Linux arm64:  xpack-openocd-<ver>-linux-arm64.tar.gz
      macOS x64:    xpack-openocd-<ver>-darwin-x64.tar.gz
      macOS arm64:  xpack-openocd-<ver>-darwin-arm64.tar.gz
    """
    system  = platform.system()
    machine = platform.machine().lower()

    if system == "Windows":
        return "win32-x64", "zip"
    if system == "Linux":
        arch = "arm64" if machine in ("aarch64", "arm64") else "x64"
        return f"linux-{arch}", "tar.gz"
    if system == "Darwin":
        arch = "arm64" if machine in ("aarch64", "arm64") else "x64"
        return f"darwin-{arch}", "tar.gz"

    raise RuntimeError(f"Unsupported platform: {system} / {machine}")


# ── Version helpers ────────────────────────────────────────────────────────────
def _version_key(tag: str) -> tuple[int, int, int, int]:
    """'v0.12.0-4' / '0.12.0-4' → (0, 12, 0, 4).  Newest-first sort key."""
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)(?:-(\d+))?", tag)
    if not m:
        return (0, 0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4) or 0))


# ── Network helpers ────────────────────────────────────────────────────────────
def _make_opener(proxy: "str | None") -> urllib.request.OpenerDirector:
    handlers: list = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler())
    handlers.append(urllib.request.HTTPSHandler())
    return urllib.request.build_opener(*handlers)


def _print_progress(done: int, total: int) -> None:
    BAR = 30
    if total:
        pct    = done * 100 // total
        filled = pct * BAR // 100
        bar    = "#" * filled + "." * (BAR - filled)
        mb_d, mb_t = done / 1_048_576, total / 1_048_576
        print(f"\r  [{bar}] {pct:3d}%  {mb_d:6.1f} / {mb_t:.1f} MB", end="", flush=True)
    else:
        print(f"\r  {done / 1_048_576:.1f} MB downloaded …", end="", flush=True)


# ── Version discovery ──────────────────────────────────────────────────────────
def get_available_versions(proxy: "str | None" = None) -> "list[tuple[str, str, str]]":
    """
    Query GitHub Releases API for xpack-binaries/openocd.
    Returns [(tag, version, asset_url), …], newest-first, non-pre-releases only.
    """
    info("Querying xPack OpenOCD releases on GitHub …")
    opener = _make_opener(proxy)
    req = urllib.request.Request(
        GITHUB_API,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; get-openocd/1.0)",
            "Accept":     "application/vnd.github.v3+json",
        },
    )
    try:
        with opener.open(req, timeout=20) as resp:
            releases = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach GitHub API: {exc}") from exc

    suffix, ext = detect_platform()

    result: list[tuple[str, str, str]] = []
    for rel in releases:
        if rel.get("prerelease") or rel.get("draft"):
            continue
        tag = rel["tag_name"]        # e.g. "v0.12.0-4"
        ver = tag.lstrip("v")        # e.g. "0.12.0-4"
        for asset in rel.get("assets", []):
            name = asset["name"]
            # Match e.g. xpack-openocd-0.12.0-4-win32-x64.zip
            if suffix in name and name.endswith(ext):
                result.append((tag, ver, asset["browser_download_url"]))
                break

    result.sort(key=lambda x: _version_key(x[0]), reverse=True)
    return result


# ── Download ───────────────────────────────────────────────────────────────────
def _download(url: str, dest: Path, proxy: "str | None" = None) -> None:
    """Download *url* → *dest* with resume support."""
    info(f"Downloading: {url}")
    opener   = _make_opener(proxy)
    tmp      = Path(str(dest) + ".part")
    existing = tmp.stat().st_size if tmp.exists() else 0

    headers = {"User-Agent": "Mozilla/5.0 (compatible; get-openocd/1.0)"}
    if existing:
        headers["Range"] = f"bytes={existing}-"
        info(f"Resuming from {existing / 1_048_576:.1f} MB")

    req = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(req, timeout=60) as resp:
            status = resp.status if hasattr(resp, "status") else resp.getcode()
            if status == 200 and existing:
                warn("Server does not support resume — restarting")
                existing = 0
                tmp.unlink(missing_ok=True)

            total      = int(resp.headers.get("Content-Length", 0))
            if status == 206:
                total += existing
            downloaded = existing

            with tmp.open("ab" if existing else "wb") as f:
                while True:
                    chunk = resp.read(131_072)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    _print_progress(downloaded, total)
        print()
    except urllib.error.HTTPError as exc:
        print()
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {url}") from exc
    except Exception:
        print()
        raise

    tmp.rename(dest)


# ── Install / extract ──────────────────────────────────────────────────────────
def install(archive: Path, dest_dir: Path) -> None:
    """
    Extract *archive* into *dest_dir*.

    xPack archives always contain a single root folder, e.g.:
      xpack-openocd-0.12.0-4-win32-x64.zip
        └─ xpack-openocd-0.12.0-4/
             ├─ bin/openocd.exe
             └─ openocd/scripts/…

    After extractall(OPENOCD_DIR), binary is at:
      OPENOCD_DIR/xpack-openocd-<ver>/bin/openocd(.exe)
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    info(f"Extracting {archive.name} …")

    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest_dir)

    elif archive.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(dest_dir)

    else:
        raise RuntimeError(f"Unknown package format: {archive.name}")


def find_openocd_exe(base: Path) -> "Path | None":
    """
    Locate openocd(.exe) inside *base* (searches up to 3 levels deep).
    Sets executable bit on Linux/macOS.
    """
    if not base.exists():
        return None
    names = ["openocd.exe", "openocd"]
    dirs = [base]
    for child in base.iterdir():
        if child.is_dir():
            dirs.append(child)
            for grandchild in child.iterdir():
                if grandchild.is_dir():
                    dirs.append(grandchild)
    for d in dirs:
        for n in names:
            p = d / n
            if p.is_file():
                if platform.system() != "Windows":
                    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
                return p
    return None


# ── env.mk ─────────────────────────────────────────────────────────────────────
def write_env_mk(openocd_exe: "Path | None") -> None:
    """Write tools/openocd/env.mk and update .vscode/settings.json."""
    ENV_MK.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Auto-generated by tools/scripts/get_openocd.py — do not edit manually.\n"
        "# Re-run the script to change the active OpenOCD.\n"
    )
    if openocd_exe is None:
        content = (
            header
            + "# Active source: system  (openocd resolved from PATH)\n"
            + "# To switch:  python tools/scripts/get_openocd.py --source local\n"
        )
    else:
        rel = openocd_exe.relative_to(PROJECT_ROOT).as_posix()
        ver_dir = openocd_exe.parent.parent.name   # e.g. xpack-openocd-0.12.0-4
        content = (
            header
            + f"# Active source: local  ({ver_dir})\n"
            + "# To switch:  python tools/scripts/get_openocd.py --source system\n"
            + f"override OPENOCD := $(CURDIR)/{rel}\n"
        )
    ENV_MK.write_text(content, encoding="utf-8")
    ok(f"Written: {ENV_MK.relative_to(PROJECT_ROOT)}")

    if openocd_exe is not None:
        _patch_vscode_settings(openocd_exe)


def _patch_vscode_settings(openocd_exe: Path) -> None:
    """Update cortex-debug.openocdPath in .vscode/settings.json."""
    settings_path = PROJECT_ROOT / ".vscode" / "settings.json"
    if not settings_path.exists():
        return

    text    = settings_path.read_text(encoding="utf-8")
    key     = "cortex-debug.openocdPath"
    rel     = openocd_exe.relative_to(PROJECT_ROOT).as_posix()
    new_val = f"${{workspaceFolder}}/{rel}"

    if key in text:
        text = re.sub(
            rf'("{re.escape(key)}"\s*:\s*)"[^"]*"',
            rf'\1"{new_val}"',
            text,
        )
    else:
        # Insert before the closing brace
        text = text.rstrip()
        if text.endswith("}"):
            text = text[:-1].rstrip().rstrip(",") + f',\n    "{key}": "{new_val}"\n}}'

    settings_path.write_text(text, encoding="utf-8")
    ok(f"settings.json: {key} updated")


# ── Makefile patching ──────────────────────────────────────────────────────────
def patch_makefile() -> None:
    makefile = PROJECT_ROOT / "Makefile"
    if not makefile.exists():
        warn("Makefile not found — skipping patch")
        return
    content = makefile.read_text(encoding="utf-8")
    if INCLUDE_LINE in content:
        return   # already present

    # Insert right after the last existing '-include tools/...' line
    pat = re.compile(r"^-include\s+tools/", re.MULTILINE)
    matches = list(pat.finditer(content))
    if matches:
        last_end = content.index("\n", matches[-1].start()) + 1
        content = content[:last_end] + INCLUDE_LINE + "\n" + content[last_end:]
    else:
        # Fallback: insert before the CONFIGURE block header
        m = re.compile(r"^#\s*-{3,}.*CONFIGURE", re.MULTILINE | re.IGNORECASE).search(content)
        if m:
            content = content[:m.start()] + INCLUDE_LINE + "\n\n" + content[m.start():]
        else:
            lines = content.splitlines(keepends=True)
            i = next((i for i, l in enumerate(lines) if l.strip() and not l.startswith("#")), 0)
            lines.insert(i, INCLUDE_LINE + "\n")
            content = "".join(lines)

    makefile.write_text(content, encoding="utf-8")
    ok(f"Makefile patched: added `{INCLUDE_LINE}`")


# ── .gitignore patching ────────────────────────────────────────────────────────
def patch_gitignore() -> None:
    """Add tools/openocd/xpack-*/ to .gitignore if not already present."""
    gitignore = PROJECT_ROOT / ".gitignore"
    if not gitignore.exists():
        return
    content = gitignore.read_text(encoding="utf-8")
    marker = "tools/openocd/xpack-*/"
    if marker in content:
        return
    block = (
        "\n# Downloaded xPack OpenOCD binaries (managed by tools/scripts/get_openocd.py)\n"
        f"{marker}\n"
        "!tools/openocd/env.mk\n"
    )
    gitignore.write_text(content + block, encoding="utf-8")
    ok(".gitignore updated")


# ── Local state helpers ────────────────────────────────────────────────────────
def _local_installations() -> "list[Path]":
    """Sorted list of openocd exe paths found under tools/openocd/."""
    if not OPENOCD_DIR.exists():
        return []
    result = []
    for child in OPENOCD_DIR.iterdir():
        if child.is_dir():
            exe = find_openocd_exe(child)
            if exe:
                result.append(exe)
    result.sort(key=lambda p: _version_key(p.parent.parent.name))
    return result


def _active_exe() -> "Path | None":
    if not ENV_MK.exists():
        return None
    m = re.search(
        r"override OPENOCD\s*:=\s*\$\(CURDIR\)/(.+)",
        ENV_MK.read_text(encoding="utf-8"),
    )
    return PROJECT_ROOT / m.group(1).strip() if m else None


# ── Commands ───────────────────────────────────────────────────────────────────
def cmd_list(proxy: "str | None") -> None:
    local  = _local_installations()
    active = _active_exe()

    if local:
        print("Local xPack OpenOCD installations:")
        for exe in reversed(local):
            tag = _c("1;33", " [active]") if active == exe else ""
            print(f"  {exe.parent.parent.name}{tag}")
    else:
        warn("No local xPack OpenOCD installations found.")

    print()
    if active:
        ok(f"Active: local  ({active.parent.parent.name})")
    elif ENV_MK.exists():
        ok("Active: system")
    else:
        info("Active: system  (env.mk not yet created)")

    print()
    try:
        versions = get_available_versions(proxy)
        if versions:
            print("Available online (newest first):")
            for _, ver, _ in versions[:15]:
                print(f"  {ver}")
        else:
            warn("No versions found on GitHub.")
    except RuntimeError as exc:
        warn(f"Could not fetch online list: {exc}")


def cmd_switch(source: str) -> None:
    if source == "system":
        write_env_mk(None)
        ok("Switched to: system OpenOCD")
        return
    local = _local_installations()
    if not local:
        error("No local OpenOCD found. Run without --source to download one.")
        sys.exit(1)
    write_env_mk(local[-1])
    ok(f"Switched to: local  ({local[-1].parent.parent.name})")


def cmd_download(
    latest: bool, keep_archive: bool, no_patch: bool, proxy: "str | None"
) -> None:
    try:
        versions = get_available_versions(proxy)
    except RuntimeError as exc:
        error(str(exc))
        sys.exit(1)

    if not versions:
        error("No xPack OpenOCD releases found on GitHub.")
        sys.exit(1)

    # ── Version selection ───────────────────────────────────────────────────────
    if latest:
        chosen_tag, chosen_ver, chosen_url = versions[0]
        info(f"Auto-selected latest: {chosen_ver}")
    else:
        print()
        print("Available xPack OpenOCD versions (newest first):")
        for i, (_, ver, _) in enumerate(versions):
            print(f"  [{i + 1:2d}]  {ver}")
        print()
        while True:
            try:
                raw = input(f"Select [1–{len(versions)}]  (Enter = latest): ").strip()
                if not raw:
                    chosen_tag, chosen_ver, chosen_url = versions[0]
                    break
                idx = int(raw) - 1
                if 0 <= idx < len(versions):
                    chosen_tag, chosen_ver, chosen_url = versions[idx]
                    break
                print(f"  Enter a number between 1 and {len(versions)}.")
            except (ValueError, EOFError):
                chosen_tag, chosen_ver, chosen_url = versions[0]
                break

    info(f"Version : {chosen_ver}")
    info(f"URL     : {chosen_url}")

    # ── Install ─────────────────────────────────────────────────────────────────
    # xPack archives unpack into a root dir named xpack-openocd-<ver>/
    expected_dir = OPENOCD_DIR / f"xpack-openocd-{chosen_ver}"
    existing_exe = find_openocd_exe(expected_dir)

    if existing_exe:
        ok(f"Already installed: {expected_dir.relative_to(PROJECT_ROOT)}")
    else:
        OPENOCD_DIR.mkdir(parents=True, exist_ok=True)
        archive_name = Path(chosen_url).name
        archive      = OPENOCD_DIR / archive_name

        if archive.exists() and not Path(str(archive) + ".part").exists():
            info(f"Archive already present: {archive.name}")
        else:
            try:
                _download(chosen_url, archive, proxy)
            except RuntimeError as exc:
                error(str(exc))
                sys.exit(1)

        try:
            install(archive, OPENOCD_DIR)
        except Exception as exc:
            error(f"Extract failed: {exc}")
            sys.exit(1)

        if not keep_archive:
            archive.unlink(missing_ok=True)
            info(f"Archive removed: {archive.name}")

        existing_exe = find_openocd_exe(expected_dir)
        if not existing_exe:
            # Fallback: search entire OPENOCD_DIR
            existing_exe = find_openocd_exe(OPENOCD_DIR)
        if not existing_exe:
            error(f"Could not locate openocd executable under {OPENOCD_DIR}")
            sys.exit(1)

        ok(f"Extracted: {expected_dir.relative_to(PROJECT_ROOT)}")

    write_env_mk(existing_exe)

    if not no_patch:
        patch_makefile()
        patch_gitignore()

    print()
    ok(f"OpenOCD ready: {existing_exe.relative_to(PROJECT_ROOT)}")
    print()
    print("Next steps:")
    print("  make toolchain                   # verify detected OpenOCD path")
    print("  make flash FLASH_TOOL=openocd")
    print("  make gdbserver                   # start OpenOCD GDB server on :3333")
    print()
    print("To switch back to system OpenOCD:")
    print("  python tools/scripts/get_openocd.py --source system")


# ── Entry point ────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        prog="get_openocd.py",
        description="Download and manage xPack OpenOCD.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python tools/scripts/get_openocd.py
      Interactive version picker — download and configure.

  python tools/scripts/get_openocd.py --latest
      Auto-download the latest version without prompting.

  python tools/scripts/get_openocd.py --list
      Show local and available online versions.

  python tools/scripts/get_openocd.py --source system
      Switch back to the openocd found in PATH.

  python tools/scripts/get_openocd.py --proxy http://127.0.0.1:7890
      Download using a proxy.
""",
    )
    parser.add_argument("--latest",    action="store_true", help="Auto-select latest version.")
    parser.add_argument("--list",      action="store_true", help="List versions and exit.")
    parser.add_argument("--source",    choices=["local", "system"], help="Switch active source.")
    parser.add_argument("--proxy",     default=None, metavar="URL", help="HTTP/HTTPS proxy URL.")
    parser.add_argument("--keep-archive",       action="store_true", help="Keep archive after install.")
    parser.add_argument("--no-patch-makefile",  action="store_true", help="Skip Makefile/.gitignore patching.")

    args = parser.parse_args()

    if args.list:
        cmd_list(args.proxy)
        return
    if args.source is not None:
        cmd_switch(args.source)
        return
    cmd_download(
        latest=args.latest,
        keep_archive=args.keep_archive,
        no_patch=args.no_patch_makefile,
        proxy=args.proxy,
    )


if __name__ == "__main__":
    main()
