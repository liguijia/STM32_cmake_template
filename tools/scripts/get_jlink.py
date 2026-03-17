#!/usr/bin/env python3
"""
tools/scripts/get_jlink.py
Download and manage J-Link Software Pack for embedded development.

Usage:
    python tools/scripts/get_jlink.py              # interactive version picker
    python tools/scripts/get_jlink.py --latest     # auto-select latest version
    python tools/scripts/get_jlink.py --version V8.40
    python tools/scripts/get_jlink.py --list       # list available versions and exit
    python tools/scripts/get_jlink.py --source system  # switch to system J-Link
    python tools/scripts/get_jlink.py --source local   # switch to local J-Link
    python tools/scripts/get_jlink.py --proxy <url>    # use HTTP/HTTPS proxy
    python tools/scripts/get_jlink.py --keep-archive   # keep archive after install
    python tools/scripts/get_jlink.py --no-patch-makefile
"""

import argparse
import os
import platform
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import tarfile
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
JLINK_DIR    = PROJECT_ROOT / "tools" / "jlink"
ENV_MK       = JLINK_DIR / "env.mk"

DOWNLOADS_PAGE = "https://www.segger.com/downloads/jlink/"
INCLUDE_LINE   = "-include tools/jlink/env.mk"
DEFAULT_VERSION = "8.40"

# ── Colours ───────────────────────────────────────────────────────────────────
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


# ── Platform detection ────────────────────────────────────────────────────────
def detect_platform() -> tuple[str, str]:
    """
    Return (link_keyword, ext) used to filter SEGGER download links.

    SEGGER file naming conventions
    ───────────────────────────────
    Windows (installer, NSIS):
      JLink_Windows_V924a_x86_64.exe   ← new format  (V9.xx+)
      JLink_Windows_x86_64_V752a.exe   ← old format  (V7.5x)

    Linux (tar.gz):
      JLink_Linux_V924a_x86_64.tgz
      JLink_Linux_x86_64_V752a.tgz

    macOS (pkg, manual install):
      JLink_MacOSX_V924a_universal.pkg
    """
    system  = platform.system()
    machine = platform.machine().lower()

    if system == "Windows":
        # x86_64 covers both native 64-bit and ARM64 Windows (via WoW64)
        return "Windows_x86_64", "exe"
    if system == "Linux":
        arch = "arm64" if machine in ("aarch64", "arm64") else "x86_64"
        return f"Linux_{arch}", "tgz"
    if system == "Darwin":
        arch = "arm64" if machine in ("aarch64", "arm64") else "x86_64"
        tag = "arm64" if arch == "arm64" else "universal"
        return f"MacOSX_{tag}", "pkg"

    raise RuntimeError(f"Unsupported platform: {system} / {machine}")


# ── Version helpers ───────────────────────────────────────────────────────────
def _compact_to_display(compact: str) -> str:
    """
    Convert URL compact version to display string.
    '924a' → '9.24a',  '752' → '7.52',  '9.24a' → '9.24a' (already dotted)
    """
    if "." in compact:
        return compact        # already in dot notation
    m = re.match(r"(\d)(\d{2})([a-z]?)", compact)
    if m:
        letter = m.group(3)
        return f"{m.group(1)}.{m.group(2)}{letter}"
    return compact


def _version_key(display: str) -> tuple[int, int, int]:
    """'9.24a' / 'V9.24a' → (9, 24, 1).  Used for sorting newest-first."""
    m = re.match(r"V?(\d+)\.(\d+)([a-z]?)", display, re.I)
    if not m:
        return (0, 0, 0)
    letter = ord(m.group(3).lower()) - ord("a") + 1 if m.group(3) else 0
    return (int(m.group(1)), int(m.group(2)), letter)


def _normalize_version(version: str) -> str:
    version = version.strip()
    if version.lower().startswith("v"):
        version = version[1:]
    return version.lower()


def _find_version(
    versions: list[tuple[str, str]],
    requested: str,
) -> "tuple[str, str] | None":
    wanted = _normalize_version(requested)
    for ver, url in versions:
        if _normalize_version(ver) == wanted:
            return ver, url
    return None


def _extract_version_from_link(link: str) -> "str | None":
    """
    Parse the version string from a SEGGER download link path.

    Handles both URL formats:
      …/JLink_Windows_V924a_x86_64.exe  → '9.24a'
      …/JLink_Windows_x86_64_V752a.exe  → '7.52a'
    """
    # New format: _V<compact>_  (e.g. _V924a_)
    m = re.search(r"[/_]V(\d{3,}[a-z]?)[_.]", link, re.I)
    if m:
        return _compact_to_display(m.group(1).lower())
    # Old format with dot notation: _V7.52a.  or _V7.52_
    m = re.search(r"[/_]V(\d+\.\d+[a-z]?)[_.]", link, re.I)
    if m:
        return m.group(1).lower()
    return None


# ── Network helpers ───────────────────────────────────────────────────────────
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


# ── Version discovery ─────────────────────────────────────────────────────────
def get_available_versions(proxy: "str | None" = None) -> list[tuple[str, str]]:
    """
    Scrape the SEGGER downloads page.
    Returns list of (display_version, url), newest-first.
    Only includes versioned links (skips the generic 'latest' links).
    """
    info("Querying SEGGER J-Link download page …")
    opener = _make_opener(proxy)
    req = urllib.request.Request(
        DOWNLOADS_PAGE,
        headers={"User-Agent": "Mozilla/5.0 (compatible; get-jlink/1.0)"},
    )
    try:
        with opener.open(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach SEGGER downloads page: {exc}") from exc

    kw, ext = detect_platform()

    # Match versioned links only (contain '_V' or '_v' in the filename)
    pattern = rf'href="(/downloads/jlink/JLink[^"]*{re.escape(kw)}[^"]*\.{ext})"'
    links = re.findall(pattern, html, re.I)
    # Also match old format where arch comes first: JLink_Windows_x86_64_V...
    kw_base = kw.split("_")[0]   # e.g. "Windows"
    arch    = "_".join(kw.split("_")[1:])  # e.g. "x86_64"
    pattern2 = rf'href="(/downloads/jlink/JLink[^"]*{re.escape(kw_base)}[^"]*{re.escape(arch)}[^"]*\.{ext})"'
    links += re.findall(pattern2, html, re.I)

    seen: dict[str, str] = {}
    for path in links:
        ver = _extract_version_from_link(path)
        if ver and ver not in seen:
            seen[ver] = "https://www.segger.com" + path

    versions = sorted(seen.items(), key=lambda x: _version_key(x[0]), reverse=True)
    return versions


# ── Download ──────────────────────────────────────────────────────────────────
def _download(url: str, dest: Path, proxy: "str | None" = None) -> None:
    """
    Download *url* → *dest*.  Posts license acceptance required by SEGGER.
    Supports partial-download resume.
    """
    info(f"Downloading: {url}")
    opener   = _make_opener(proxy)
    tmp      = Path(str(dest) + ".part")
    existing = tmp.stat().st_size if tmp.exists() else 0

    post_data = urllib.parse.urlencode({
        "accept_license_agreement": "accepted",
        "submit": "Download Software",
    }).encode()
    headers = {
        "User-Agent":   "Mozilla/5.0 (compatible; get-jlink/1.0)",
        "Referer":      DOWNLOADS_PAGE,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if existing:
        headers["Range"] = f"bytes={existing}-"
        info(f"Resuming from {existing / 1_048_576:.1f} MB")

    req = urllib.request.Request(url, data=post_data, headers=headers)
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


# ── Install / extract ─────────────────────────────────────────────────────────
def _run_installer_windows(archive: Path, dest_dir: Path) -> None:
    """
    Run a Windows installer silently, requesting UAC elevation if needed.

    Tries flags in this order:
      1. NSIS   : /S /D=<path>
      2. Inno   : /VERYSILENT /DIR=<path> /SUPPRESSMSGBOXES /NORESTART

    If the current process is not elevated, PowerShell Start-Process -Verb RunAs
    is used so Windows shows the UAC consent dialog.
    """
    import ctypes

    dest_abs = str(dest_dir.resolve())
    is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())

    def run(flags: list[str]) -> int:
        if is_admin:
            return subprocess.run([str(archive)] + flags, timeout=180).returncode
        # Not admin — ask PowerShell to re-launch with elevation
        args_str = ", ".join(f'"{f}"' for f in flags)
        ps = (
            f'$p = Start-Process -FilePath "{archive}" '
            f'-ArgumentList {args_str} '
            f'-Verb RunAs -Wait -PassThru; '
            f'exit $p.ExitCode'
        )
        return subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            timeout=180,
        ).returncode

    # NSIS format  (SEGGER may return 2="restart suggested" even on success)
    info(f"Installing {archive.name} (silent, requesting admin) …")
    rc = run(["/S", f"/D={dest_abs}"])
    if rc in (0, 2, 3010):   # 3010 = restart required (MSI/NSIS)
        return

    # Inno Setup format
    warn(f"NSIS flags failed (exit {rc}), trying Inno Setup flags …")
    rc = run(["/VERYSILENT", f"/DIR={dest_abs}", "/SUPPRESSMSGBOXES", "/NORESTART"])
    if rc in (0, 2, 3010):
        return

    raise RuntimeError(
        f"Installer exited with code {rc}.\n"
        f"  Try running this script from an elevated (Administrator) terminal,\n"
        f"  or install J-Link manually and use:\n"
        f"    python tools/scripts/get_jlink.py --source system"
    )


def install(archive: Path, dest_dir: Path) -> None:
    """Install the downloaded package into *dest_dir*."""
    name = archive.name.lower()
    dest_dir.mkdir(parents=True, exist_ok=True)

    if name.endswith(".exe"):
        _run_installer_windows(archive, dest_dir)

    elif name.endswith((".tgz", ".tar.gz")):
        info(f"Extracting {archive.name} …")
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(dest_dir)

    elif name.endswith(".pkg"):
        warn(".pkg (macOS) requires manual installation.")
        warn(f"Open and install: {archive}")
        warn("Then run:  python tools/scripts/get_jlink.py --source system")
        sys.exit(0)

    else:
        raise RuntimeError(f"Unknown package format: {archive.name}")


def find_jlink_exe(base: Path) -> "Path | None":
    """Locate JLink.exe / JLinkExe inside *base* (up to 3 levels deep)."""
    names = ["JLink.exe", "JLinkExe"]
    if not base.exists():
        return None
    # Collect candidate directories: base itself + up to 2 levels of subdirs
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
            if p.exists():
                return p
    return None


# ── env.mk ────────────────────────────────────────────────────────────────────
def write_env_mk(jlink_exe: "Path | None") -> None:
    ENV_MK.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Auto-generated by tools/scripts/get_jlink.py — do not edit manually.\n"
        "# Re-run the script to change the active J-Link.\n"
    )
    if jlink_exe is None:
        content = (
            header
            + "# Active source: system  (JLink resolved from PATH)\n"
            + "# To switch:  python tools/scripts/get_jlink.py --source local\n"
        )
    else:
        rel = jlink_exe.relative_to(PROJECT_ROOT).as_posix()
        content = (
            header
            + f"# Active source: local  ({jlink_exe.parent.name})\n"
            + "# To switch:  python tools/scripts/get_jlink.py --source system\n"
            + f'override JLINK := $(CURDIR)/{rel}\n'
        )
    ENV_MK.write_text(content, encoding="utf-8")
    ok(f"Written: {ENV_MK.relative_to(PROJECT_ROOT)}")

    # Also update .vscode/settings.json with the GDB server path for Cortex-Debug
    if jlink_exe is not None:
        gdbserver = jlink_exe.parent / (
            "JLinkGDBServerCL.exe" if platform.system() == "Windows" else "JLinkGDBServerCLExe"
        )
        if gdbserver.exists():
            _patch_vscode_settings(gdbserver)


def _patch_vscode_settings(gdbserver: Path) -> None:
    """Update cortex-debug.JLinkGDBServerPath in .vscode/settings.json."""
    import json

    settings_path = PROJECT_ROOT / ".vscode" / "settings.json"
    if not settings_path.exists():
        return

    # settings.json may contain // comments — load as text, use regex to update
    text = settings_path.read_text(encoding="utf-8")
    key  = "cortex-debug.JLinkGDBServerPath"
    # Use forward slashes and ${workspaceFolder}-relative path
    rel  = gdbserver.relative_to(PROJECT_ROOT).as_posix()
    new_val = f"${{workspaceFolder}}/{rel}"

    if key in text:
        # Replace existing value
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


# ── Makefile patching ─────────────────────────────────────────────────────────
def patch_makefile() -> None:
    makefile = PROJECT_ROOT / "Makefile"
    if not makefile.exists():
        warn("Makefile not found — skipping patch")
        return
    content = makefile.read_text(encoding="utf-8")
    if INCLUDE_LINE in content:
        return
    pat = re.compile(r"^#\s*-{3,}.*CONFIGURE", re.MULTILINE | re.IGNORECASE)
    m   = pat.search(content)
    if m:
        content = content[:m.start()] + INCLUDE_LINE + "\n\n" + content[m.start():]
    else:
        lines = content.splitlines(keepends=True)
        i = next((i for i, l in enumerate(lines) if l.strip() and not l.startswith("#")), 0)
        lines.insert(i, INCLUDE_LINE + "\n\n")
        content = "".join(lines)
    makefile.write_text(content, encoding="utf-8")
    ok(f"Makefile patched: added `{INCLUDE_LINE}`")


# ── Local state helpers ───────────────────────────────────────────────────────
def _local_installations() -> list[Path]:
    """Sorted list of JLink exe paths found under tools/jlink/."""
    if not JLINK_DIR.exists():
        return []
    result = []
    for child in JLINK_DIR.iterdir():
        if child.is_dir():
            exe = find_jlink_exe(child)
            if exe:
                result.append(exe)
    ver_re = re.compile(r"(\d+\.\d+[a-z]?)", re.I)
    result.sort(key=lambda p: _version_key(
        m.group(1) if (m := ver_re.search(p.parent.name)) else "0.0"
    ))
    return result


def _active_exe() -> "Path | None":
    if not ENV_MK.exists():
        return None
    m = re.search(r"override JLINK\s*:=\s*\$\(CURDIR\)/(.+)", ENV_MK.read_text(encoding="utf-8"))
    return PROJECT_ROOT / m.group(1).strip() if m else None


# ── Commands ──────────────────────────────────────────────────────────────────
def cmd_list(proxy: "str | None") -> None:
    local  = _local_installations()
    active = _active_exe()

    if local:
        print("Local J-Link installations:")
        for exe in reversed(local):
            tag = _c("1;33", " [active]") if active == exe else ""
            print(f"  {exe.parent.name}{tag}")
    else:
        warn("No local J-Link installations found.")

    print()
    if active:
        ok(f"Active: local  ({active.parent.name})")
    elif ENV_MK.exists():
        ok("Active: system")
    else:
        info("Active: system  (env.mk not yet created)")

    print()
    try:
        versions = get_available_versions(proxy)
        if versions:
            print("Available online (newest first):")
            for ver, _ in versions[:15]:
                print(f"  V{ver}")
        else:
            warn("No versions found on SEGGER downloads page.")
    except RuntimeError as exc:
        warn(f"Could not fetch online list: {exc}")


def cmd_switch(source: str) -> None:
    if source == "system":
        write_env_mk(None)
        ok("Switched to: system J-Link")
        return
    local = _local_installations()
    if not local:
        error("No local J-Link found. Run without --source to download one.")
        sys.exit(1)
    write_env_mk(local[-1])
    ok(f"Switched to: local  ({local[-1].parent.name})")


def cmd_download(
    latest: bool,
    version: "str | None",
    keep_archive: bool,
    no_patch: bool,
    proxy: "str | None",
) -> None:
    try:
        versions = get_available_versions(proxy)
    except RuntimeError as exc:
        error(str(exc))
        sys.exit(1)

    if not versions:
        error("No J-Link versions found on SEGGER downloads page.")
        sys.exit(1)

    # ── Version selection ──────────────────────────────────────────────────────
    default_choice = _find_version(versions, DEFAULT_VERSION)

    if version is not None:
        selected = _find_version(versions, version)
        if selected is None:
            error(f"Requested J-Link version not found: {version}")
            print("Available versions (newest first):")
            for ver, _ in versions[:15]:
                print(f"  V{ver}")
            sys.exit(1)
        chosen_ver, chosen_url = selected
        info(f"Selected requested version: V{chosen_ver}")
    elif latest:
        chosen_ver, chosen_url = versions[0]
        info(f"Auto-selected latest: V{chosen_ver}")
    else:
        default_ver, default_url = default_choice or versions[0]
        print()
        print("Available J-Link versions (newest first):")
        for i, (ver, _) in enumerate(versions):
            print(f"  [{i + 1:2d}]  V{ver}")
        print()
        while True:
            try:
                raw = input(f"Select [1–{len(versions)}]  (Enter = latest): ").strip()
                if not raw:
                    chosen_ver, chosen_url = default_ver, default_url
                    break
                idx = int(raw) - 1
                if 0 <= idx < len(versions):
                    chosen_ver, chosen_url = versions[idx]
                    break
                print(f"  Enter a number between 1 and {len(versions)}.")
            except (ValueError, EOFError):
                chosen_ver, chosen_url = default_ver, default_url
                break

    info(f"Version : V{chosen_ver}")
    info(f"URL     : {chosen_url}")

    # ── Install directory ──────────────────────────────────────────────────────
    # Derive a clean folder name from the archive filename
    archive_name = Path(chosen_url).name
    folder_name  = Path(archive_name).stem   # strip extension
    install_dir  = JLINK_DIR / folder_name
    existing_exe = find_jlink_exe(install_dir)

    if existing_exe:
        ok(f"Already installed: {install_dir.relative_to(PROJECT_ROOT)}")
    else:
        JLINK_DIR.mkdir(parents=True, exist_ok=True)
        archive = JLINK_DIR / archive_name

        if archive.exists() and not Path(str(archive) + ".part").exists():
            info(f"Archive already present: {archive.name}")
        else:
            try:
                _download(chosen_url, archive, proxy)
            except RuntimeError as exc:
                error(str(exc))
                sys.exit(1)

        try:
            install(archive, install_dir)
        except Exception as exc:
            error(f"Install failed: {exc}")
            sys.exit(1)

        if not keep_archive:
            archive.unlink(missing_ok=True)
            info(f"Archive removed: {archive.name}")

        existing_exe = find_jlink_exe(install_dir)
        if not existing_exe:
            error(f"Could not locate JLink executable in {install_dir}")
            sys.exit(1)

        ok(f"Installed: {install_dir.relative_to(PROJECT_ROOT)}")

    write_env_mk(existing_exe)
    if not no_patch:
        patch_makefile()

    print()
    ok(f"J-Link ready: {existing_exe.relative_to(PROJECT_ROOT)}")
    print()
    print("Next steps:")
    print("  make toolchain              # verify detected J-Link path")
    print("  make flash FLASH_TOOL=jlink")
    print()
    print("To switch back to system J-Link:")
    print("  python tools/scripts/get_jlink.py --source system")


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        prog="get_jlink.py",
        description="Download and manage J-Link Software Pack.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python tools/scripts/get_jlink.py
      Interactive version picker — download and configure.

  python tools/scripts/get_jlink.py --latest
      Auto-download the latest version without prompting.

  python tools/scripts/get_jlink.py --version V8.40
      Download and configure the default pinned version.

  python tools/scripts/get_jlink.py --list
      Show local and available online versions.

  python tools/scripts/get_jlink.py --source system
      Switch back to the J-Link found in PATH.

  python tools/scripts/get_jlink.py --proxy http://127.0.0.1:7890
      Download using a proxy.
""",
    )
    parser.add_argument("--latest",    action="store_true", help="Auto-select the latest version.")
    parser.add_argument("--version",   default=None, metavar="VER", help="Download a specific version, e.g. V8.40.")
    parser.add_argument("--list",      action="store_true", help="List versions and exit.")
    parser.add_argument("--source",    choices=["local", "system"], help="Switch active source.")
    parser.add_argument("--proxy",     default=None, metavar="URL", help="HTTP/HTTPS proxy URL.")
    parser.add_argument("--keep-archive", action="store_true", help="Keep archive after install.")
    parser.add_argument("--no-patch-makefile", action="store_true", help="Skip Makefile patching.")

    args = parser.parse_args()

    if args.latest and args.version is not None:
        parser.error("--latest cannot be used together with --version")

    if args.list:
        cmd_list(args.proxy)
        return
    if args.source is not None:
        cmd_switch(args.source)
        return
    cmd_download(
        latest=args.latest,
        version=args.version,
        keep_archive=args.keep_archive,
        no_patch=args.no_patch_makefile,
        proxy=args.proxy,
    )


if __name__ == "__main__":
    main()
