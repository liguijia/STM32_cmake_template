#!/usr/bin/env python3
"""
tools/scripts/get_toolchain.py
Download and manage the Arm GNU Toolchain for STM32 development.

Usage:
    python tools/scripts/get_toolchain.py                    # download latest, use local
    python tools/scripts/get_toolchain.py --latest           # explicit latest alias
    python tools/scripts/get_toolchain.py --source system    # switch to system toolchain
    python tools/scripts/get_toolchain.py --source local     # switch to local toolchain
    python tools/scripts/get_toolchain.py --list             # list local toolchains
    python tools/scripts/get_toolchain.py --version 14.2.rel1
    python tools/scripts/get_toolchain.py --mirror <url>     # use alternative download base URL
    python tools/scripts/get_toolchain.py --proxy <url>      # use HTTP/HTTPS proxy
    python tools/scripts/get_toolchain.py --keep-archive     # keep .zip/.tar.xz after extract
    python tools/scripts/get_toolchain.py --no-patch-makefile
"""

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from net_fallback import download_file as curl_download_file
from net_fallback import fetch_bytes as curl_fetch_bytes

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR    = Path(__file__).resolve().parent
PROJECT_ROOT  = SCRIPT_DIR.parent.parent
TOOLCHAIN_DIR = PROJECT_ROOT / "tools" / "toolchain"
ENV_MK        = TOOLCHAIN_DIR / "env.mk"

# ── Arm downloads ─────────────────────────────────────────────────────────────
DOWNLOADS_PAGE   = "https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads"
DEFAULT_BASE_URL = "https://developer.arm.com/-/media/Files/downloads/gnu"

# Fallback version used when the downloads page cannot be reached
FALLBACK_VERSION = "14.2.rel1"

# ── Colours (disabled on Windows without ANSI support) ────────────────────────
_USE_COLOR = sys.stdout.isatty() and (
    platform.system() != "Windows"
    or os.environ.get("TERM") is not None
    or os.environ.get("WT_SESSION") is not None   # Windows Terminal
    or os.environ.get("ANSICON") is not None
)


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def info(msg: str)  -> None: print(_c("1;36", "[info]"),  msg)
def ok(msg: str)    -> None: print(_c("1;32", "[ok]"),    msg)
def warn(msg: str)  -> None: print(_c("1;33", "[warn]"),  msg)
def error(msg: str) -> None: print(_c("1;31", "[error]"), msg)


# ── Platform detection ────────────────────────────────────────────────────────
def detect_system_toolchain() -> "tuple[Path, str] | None":
    gcc = shutil.which("arm-none-eabi-gcc")
    if not gcc:
        return None

    gcc_path = Path(gcc).resolve()
    try:
        result = subprocess.run(
            [str(gcc_path), "--version"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None

    first_line = result.stdout.splitlines()[0].strip() if result.stdout else str(gcc_path)
    return gcc_path, first_line


def detect_platform() -> tuple[str, str]:
    """Return (host_tag, extension) for the current OS/arch."""
    system  = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        # Newer releases ship x86_64 host; older ones only i686.
        # We try x86_64 first and fall back to i686 on HTTP error.
        return "mingw-w64-x86_64", "zip"
    if system == "linux":
        if machine in ("aarch64", "arm64"):
            return "aarch64", "tar.xz"
        return "x86_64", "tar.xz"
    if system == "darwin":
        if machine in ("arm64", "aarch64"):
            return "darwin-arm64", "pkg"
        return "darwin-x86_64", "pkg"

    raise RuntimeError(f"Unsupported platform: {system} / {machine}")


# ── Version detection ─────────────────────────────────────────────────────────
def _version_key(v: str) -> tuple[int, int, int]:
    m = re.match(r"(\d+)\.(\d+)\.rel(\d+)", v)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)


def _get_latest_version_legacy() -> str:
    """Fetch the Arm downloads page and return the latest toolchain version string."""
    info("Querying latest Arm GNU Toolchain version ...")
    req = urllib.request.Request(
        DOWNLOADS_PAGE,
        headers={"User-Agent": "Mozilla/5.0 (compatible; get-toolchain/1.0)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach downloads page: {exc}") from exc

    versions = re.findall(r"arm-gnu-toolchain-(\d+\.\d+\.rel\d+)", html)
    if not versions:
        raise RuntimeError("No version strings found on downloads page")

    latest = max(set(versions), key=_version_key)
    return latest


# ── Download helpers ──────────────────────────────────────────────────────────
def _build_url(version: str, host_tag: str, ext: str, base_url: str) -> tuple[str, str]:
    filename = f"arm-gnu-toolchain-{version}-{host_tag}-arm-none-eabi.{ext}"
    # Strip trailing slash from base_url for clean joining
    url = f"{base_url.rstrip('/')}/{version}/binrel/{filename}"
    return url, filename


def _make_opener(proxy: "str | None") -> urllib.request.OpenerDirector:
    """Build a urllib opener, optionally with an explicit proxy."""
    handlers: list = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        # urllib respects HTTP_PROXY / HTTPS_PROXY env vars by default via
        # ProxyHandler(); nothing extra needed — just build a plain opener.
        handlers.append(urllib.request.ProxyHandler())   # reads env vars
    handlers.append(urllib.request.HTTPSHandler())
    return urllib.request.build_opener(*handlers)


def _extract_latest_version(html: str) -> str:
    versions = re.findall(r"arm-gnu-toolchain-(\d+\.\d+\.rel\d+)", html)
    if not versions:
        raise RuntimeError("No version strings found on downloads page")
    return max(set(versions), key=_version_key)


def get_latest_version(proxy: "str | None" = None) -> str:
    """Fetch the Arm downloads page and return the latest toolchain version string."""
    info("Querying latest Arm GNU Toolchain version ...")
    req = urllib.request.Request(
        DOWNLOADS_PAGE,
        headers={"User-Agent": "Mozilla/5.0 (compatible; get-toolchain/1.0)"},
    )
    try:
        with _make_opener(proxy).open(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        warn(f"urllib failed for Arm downloads page ({exc}); retrying with curl")
        try:
            html = curl_fetch_bytes(
                DOWNLOADS_PAGE,
                headers={"User-Agent": "Mozilla/5.0 (compatible; get-toolchain/1.0)"},
                proxy=proxy,
                timeout=20,
            ).decode("utf-8", errors="replace")
        except RuntimeError as curl_exc:
            raise RuntimeError(f"Cannot reach downloads page: {curl_exc}") from exc

    return _extract_latest_version(html)


def _download(url: str, dest: Path, proxy: "str | None" = None) -> None:
    """
    Stream-download *url* to *dest* with a progress bar.
    Resumes automatically if a partial .part file exists (server must support Range).
    """
    info(f"Downloading: {url}")
    opener   = _make_opener(proxy)
    tmp      = Path(str(dest) + ".part")
    existing = tmp.stat().st_size if tmp.exists() else 0
    total = 0
    downloaded = existing

    headers = {"User-Agent": "Mozilla/5.0 (compatible; get-toolchain/1.0)"}
    req = urllib.request.Request(url, headers=headers)
    if existing:
        req.add_header("Range", f"bytes={existing}-")
        info(f"Resuming from {existing / 1_048_576:.1f} MB")

    try:
        with opener.open(req, timeout=60) as resp:
            # HTTP 206 Partial Content → server accepted Range
            # HTTP 200 OK              → server ignored Range, restart
            status = resp.status if hasattr(resp, "status") else resp.getcode()
            if status == 200 and existing:
                warn("Server does not support resume — restarting download")
                existing = 0
                tmp.unlink(missing_ok=True)

            total      = int(resp.headers.get("Content-Length", 0))
            if status == 206:
                total += existing   # Content-Length is the remaining bytes

            chunk_size = 131_072   # 128 KiB

            mode = "ab" if existing else "wb"
            with tmp.open(mode) as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    _print_progress(downloaded, total)

        print()  # newline after progress bar

    except urllib.error.HTTPError as exc:
        print()
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {url}") from exc
    except Exception as exc:
        print()
        warn(f"urllib download failed ({exc}); retrying with curl")
        curl_download_file(url, dest, headers=headers, proxy=proxy, timeout=0)
        return

    if total and downloaded < total:
        warn(
            f"Download ended early ({downloaded} of {total} bytes); retrying with curl"
        )
        curl_download_file(url, dest, headers=headers, proxy=proxy, timeout=0)
        return

    tmp.rename(dest)

    if not archive_is_valid(dest):
        warn("Downloaded archive appears incomplete or corrupted; retrying with curl")
        dest.unlink(missing_ok=True)
        curl_download_file(url, dest, headers=headers, proxy=proxy, timeout=0)
        if not archive_is_valid(dest):
            raise RuntimeError(f"Downloaded archive is invalid: {dest.name}")


def _print_progress(done: int, total: int) -> None:
    BAR = 30
    if total:
        pct    = done * 100 // total
        filled = pct * BAR // 100
        bar    = "#" * filled + "." * (BAR - filled)
        mb_d   = done  / 1_048_576
        mb_t   = total / 1_048_576
        print(f"\r  [{bar}] {pct:3d}%  {mb_d:6.1f} / {mb_t:.1f} MB", end="", flush=True)
    else:
        mb_d = done / 1_048_576
        print(f"\r  {mb_d:.1f} MB downloaded …", end="", flush=True)


# ── Archive extraction ────────────────────────────────────────────────────────
def extract_archive(archive: Path, dest_dir: Path) -> None:
    """Extract a .zip or .tar.xz into *dest_dir*."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = archive.name

    if name.endswith(".zip"):
        info(f"Extracting {name} …")
        with zipfile.ZipFile(archive, "r") as zf:
            members = zf.infolist()
            total   = len(members)
            for i, member in enumerate(members, 1):
                zf.extract(member, dest_dir)
                if i % 500 == 0 or i == total:
                    print(f"\r  {i}/{total} files", end="", flush=True)
        print()

    elif name.endswith(".tar.xz"):
        info(f"Extracting {name} …")
        with tarfile.open(archive, "r:xz") as tf:
            tf.extractall(dest_dir)

    elif name.endswith(".pkg"):
        warn(".pkg format (macOS) is not auto-extracted.")
        warn(f"Install manually: {archive}")
        sys.exit(0)

    else:
        raise RuntimeError(f"Unknown archive format: {name}")


# ── env.mk management ─────────────────────────────────────────────────────────
def archive_is_valid(archive: Path) -> bool:
    """Return True if *archive* looks structurally valid for its file type."""
    if not archive.exists() or archive.stat().st_size == 0:
        return False

    name = archive.name
    if name.endswith(".zip"):
        return zipfile.is_zipfile(archive)
    if name.endswith(".tar.xz"):
        return tarfile.is_tarfile(archive)
    if name.endswith(".pkg"):
        return True
    return False


def write_env_mk(bin_dir: "Path | None") -> None:
    """
    Write tools/toolchain/env.mk.

    bin_dir=None  → comment-only file (system toolchain remains in effect)
    bin_dir=Path  → override PREFIX to point at the local toolchain
    """
    ENV_MK.parent.mkdir(parents=True, exist_ok=True)

    header = (
        "# Auto-generated by tools/scripts/get_toolchain.py — do not edit manually.\n"
        "# Re-run the script to change the active toolchain.\n"
    )

    if bin_dir is None:
        content = (
            header
            + "# Active source: system  (arm-none-eabi-* resolved from PATH)\n"
            + "# To switch:  python tools/scripts/get_toolchain.py --source local\n"
        )
    else:
        # Use POSIX-style path with $(CURDIR) so the project stays relocatable.
        rel = bin_dir.relative_to(PROJECT_ROOT).as_posix()
        content = (
            header
            + f"# Active source: local  ({bin_dir.parent.name})\n"
            + "# To switch:  python tools/scripts/get_toolchain.py --source system\n"
            + f"override PREFIX := $(CURDIR)/{rel}/arm-none-eabi-\n"
        )

    ENV_MK.write_text(content, encoding="utf-8")
    ok(f"Written: {ENV_MK.relative_to(PROJECT_ROOT)}")


# ── Makefile patching ─────────────────────────────────────────────────────────
INCLUDE_LINE = "-include tools/toolchain/env.mk"


def patch_makefile() -> None:
    """
    Insert `-include tools/toolchain/env.mk` just before the CONFIGURE section
    so that env.mk can override PREFIX before it is used.
    """
    makefile = PROJECT_ROOT / "Makefile"
    if not makefile.exists():
        warn("Makefile not found — skipping patch")
        return

    content = makefile.read_text(encoding="utf-8")
    if INCLUDE_LINE in content:
        return  # already patched

    # Insert just before the CONFIGURE comment block
    pat = re.compile(r"^#\s*-{3,}.*CONFIGURE", re.MULTILINE | re.IGNORECASE)
    m   = pat.search(content)
    if m:
        pos     = m.start()
        content = content[:pos] + INCLUDE_LINE + "\n\n" + content[pos:]
    else:
        # Fallback: prepend after the initial comment block
        lines     = content.splitlines(keepends=True)
        insert_at = next(
            (i for i, l in enumerate(lines) if l.strip() and not l.startswith("#")),
            0,
        )
        lines.insert(insert_at, INCLUDE_LINE + "\n\n")
        content = "".join(lines)

    makefile.write_text(content, encoding="utf-8")
    ok(f"Makefile patched: added `{INCLUDE_LINE}`")


# ── List helpers ──────────────────────────────────────────────────────────────
def _local_toolchains() -> list[Path]:
    if not TOOLCHAIN_DIR.exists():
        return []
    return sorted(
        d for d in TOOLCHAIN_DIR.iterdir()
        if d.is_dir() and re.match(r"arm-gnu-toolchain-", d.name)
    )


def _active_bin_dir() -> "Path | None":
    if not ENV_MK.exists():
        return None
    text = ENV_MK.read_text(encoding="utf-8")
    m    = re.search(r"override PREFIX\s*:=\s*\$\(CURDIR\)/(.+)/arm-none-eabi-", text)
    if m:
        return PROJECT_ROOT / m.group(1).strip()
    return None


def cmd_list() -> None:
    dirs       = _local_toolchains()
    active_bin = _active_bin_dir()

    if not dirs:
        warn("No local toolchains installed.")
    else:
        print("Local toolchains:")
        for d in dirs:
            bin_dir = d / "bin"
            gcc     = bin_dir / (
                "arm-none-eabi-gcc.exe" if platform.system() == "Windows"
                else "arm-none-eabi-gcc"
            )
            status  = _c("1;32", "ok") if gcc.exists() else _c("1;31", "incomplete")
            active  = _c("1;33", " [active]") if active_bin == bin_dir else ""
            print(f"  {d.name}  ({status}){active}")

    print()
    if active_bin:
        ok(f"Active: local  ({active_bin.parent.name})")
    elif ENV_MK.exists():
        ok("Active: system")
    else:
        info("Active: system  (env.mk not yet created)")


# ── Switch-only mode ──────────────────────────────────────────────────────────
def cmd_switch(source: str) -> None:
    if source == "system":
        write_env_mk(None)
        ok("Switched to: system toolchain")
        return

    # source == "local"
    dirs = _local_toolchains()
    if not dirs:
        error("No local toolchain found.  Run without --source to download one.")
        sys.exit(1)

    best    = dirs[-1]          # highest version (dirs are sorted)
    bin_dir = best / "bin"
    write_env_mk(bin_dir)
    ok(f"Switched to: local  ({best.name})")


def _print_network_recovery_tips(base_url: str, proxy: "str | None") -> None:
    print()
    print("Recovery options:")
    print("  Re-run the same command to resume from any .part file.")
    if proxy:
        print("  Proxy is already set; verify it can reach developer.arm.com.")
    else:
        print("  Use a proxy:  make setup-toolchain PROXY=http://127.0.0.1:7890")
    if base_url == DEFAULT_BASE_URL:
        print("  Use a mirror: make setup-toolchain TOOLCHAIN_MIRROR=<mirror-base-url>")
    else:
        print(f"  Current mirror: {base_url}")
    print(
        "  Tune slow-link detection with "
        "STM32_DOWNLOAD_MIN_SPEED and STM32_DOWNLOAD_STALL_TIME if needed."
    )


# ── Download mode ─────────────────────────────────────────────────────────────
def _should_try_windows_i686_fallback(exc: RuntimeError) -> bool:
    message = str(exc).upper()
    return "HTTP 404" in message or "NOT FOUND" in message


def cmd_download(
    version: "str | None",
    keep_archive: bool,
    no_patch: bool,
    base_url: str,
    proxy: "str | None",
    prefer_system: bool,
) -> None:
    try:
        host_tag, ext = detect_platform()
    except RuntimeError as exc:
        error(str(exc))
        sys.exit(1)

    if prefer_system:
        system_tc = detect_system_toolchain()
        if system_tc is not None:
            gcc_path, gcc_version = system_tc
            info("Detected Arm GNU Toolchain in PATH")
            info(f"GCC      : {gcc_path}")
            info(f"Version  : {gcc_version}")
            write_env_mk(None)
            if not no_patch:
                patch_makefile()

            print()
            ok("Toolchain ready: system")
            print()
            print("Next steps:")
            print("  make toolchain   # verify detected toolchain")
            print("  make             # build your project")
            return

    # Resolve version
    if version is None:
        try:
            version = get_latest_version(proxy)
        except RuntimeError as exc:
            warn(str(exc))
            warn(f"Falling back to known version: {FALLBACK_VERSION}")
            version = FALLBACK_VERSION

    info(f"Version  : {version}")
    info(f"Host     : {host_tag}")
    if base_url != DEFAULT_BASE_URL:
        info(f"Mirror   : {base_url}")
    if proxy:
        info(f"Proxy    : {proxy}")

    TOOLCHAIN_DIR.mkdir(parents=True, exist_ok=True)

    # Build name / path
    tc_name = f"arm-gnu-toolchain-{version}-{host_tag}-arm-none-eabi"
    tc_path = TOOLCHAIN_DIR / tc_name
    bin_dir = tc_path / "bin"

    if tc_path.exists():
        ok(f"Already installed: {tc_path.relative_to(PROJECT_ROOT)}")
    else:
        url, filename = _build_url(version, host_tag, ext, base_url)
        archive       = TOOLCHAIN_DIR / filename

        if archive.exists() and not Path(str(archive) + ".part").exists() and archive_is_valid(archive):
            info(f"Archive already present: {archive.name}")
        else:
            if archive.exists() and not archive_is_valid(archive):
                warn(f"Existing archive is invalid; removing {archive.name}")
                archive.unlink(missing_ok=True)
            def _try_download(h_tag: str) -> Path:
                nonlocal tc_name, tc_path, bin_dir
                u, fn = _build_url(version, h_tag, ext, base_url)
                arc   = TOOLCHAIN_DIR / fn
                tc_name = f"arm-gnu-toolchain-{version}-{h_tag}-arm-none-eabi"
                tc_path = TOOLCHAIN_DIR / tc_name
                bin_dir = tc_path / "bin"
                _download(u, arc, proxy)
                return arc

            # Try download; fall back to i686 on Windows if x86_64 is not found
            try:
                archive = _try_download(host_tag)
            except RuntimeError as exc:
                if host_tag == "mingw-w64-x86_64" and _should_try_windows_i686_fallback(exc):
                    warn(str(exc))
                    warn("Retrying with mingw-w64-i686 host")
                    try:
                        archive = _try_download("mingw-w64-i686")
                    except RuntimeError as exc2:
                        error(str(exc2))
                        _print_network_recovery_tips(base_url, proxy)
                        sys.exit(1)
                else:
                    error(str(exc))
                    _print_network_recovery_tips(base_url, proxy)
                    sys.exit(1)

        if not archive_is_valid(archive):
            error(f"Downloaded archive is invalid: {archive.name}")
            archive.unlink(missing_ok=True)
            _print_network_recovery_tips(base_url, proxy)
            sys.exit(1)

        try:
            extract_archive(archive, TOOLCHAIN_DIR)
        except Exception as exc:
            error(f"Extraction failed: {exc}")
            _print_network_recovery_tips(base_url, proxy)
            sys.exit(1)

        if not keep_archive:
            archive.unlink()
            info(f"Archive removed: {archive.name}")

        ok(f"Installed: {tc_path.relative_to(PROJECT_ROOT)}")

    # Write env.mk and patch Makefile
    write_env_mk(bin_dir)
    if not no_patch:
        patch_makefile()

    print()
    ok(f"Toolchain ready: {tc_name}")
    print()
    print("Next steps:")
    print("  make toolchain   # verify detected toolchain")
    print("  make             # build your project")
    print()
    print("To switch back to the system toolchain:")
    print("  python tools/scripts/get_toolchain.py --source system")


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        prog="get_toolchain.py",
        description="Download and manage the Arm GNU Toolchain for STM32 development.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python tools/scripts/get_toolchain.py
      Download latest toolchain and configure project to use it.

  python tools/scripts/get_toolchain.py --latest
      Explicitly request the latest toolchain version.

  python tools/scripts/get_toolchain.py --source system
      Switch back to the toolchain in your system PATH.

  python tools/scripts/get_toolchain.py --source local
      Switch to the latest locally installed toolchain.

  python tools/scripts/get_toolchain.py --list
      List all locally installed toolchains.

  python tools/scripts/get_toolchain.py --version 14.2.rel1
      Download a specific toolchain version.

  python tools/scripts/get_toolchain.py --keep-archive
      Download and install but keep the .zip / .tar.xz file.

  python tools/scripts/get_toolchain.py --prefer-system
      Use arm-none-eabi-gcc from PATH if already installed; otherwise download.
""",
    )

    parser.add_argument(
        "--latest", action="store_true",
        help="Explicitly request the latest toolchain version.",
    )
    parser.add_argument(
        "--source", choices=["local", "system"], default=None,
        help="Switch active toolchain source without downloading.",
    )
    parser.add_argument(
        "--version", default=None, metavar="VER",
        help="Toolchain version to download (e.g. 14.2.rel1). Default: latest.",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List locally installed toolchains and exit.",
    )
    parser.add_argument(
        "--mirror", default=None, metavar="URL",
        help=(
            "Alternative base URL for downloads. "
            "The script appends /{version}/binrel/{filename} to this URL. "
            "Example: --mirror https://mirrors.example.com/arm-gnu"
        ),
    )
    parser.add_argument(
        "--proxy", default=None, metavar="URL",
        help=(
            "HTTP/HTTPS proxy URL (e.g. http://127.0.0.1:7890). "
            "The script also reads HTTP_PROXY / HTTPS_PROXY env vars automatically."
        ),
    )
    parser.add_argument(
        "--keep-archive", action="store_true",
        help="Keep the downloaded archive after extraction.",
    )
    parser.add_argument(
        "--prefer-system", action="store_true",
        help="Use arm-none-eabi-gcc from PATH if available before downloading.",
    )
    parser.add_argument(
        "--no-patch-makefile", action="store_true",
        help="Do not add the -include directive to the Makefile.",
    )

    args = parser.parse_args()

    if args.latest and args.version is not None:
        parser.error("--latest cannot be used together with --version")

    if args.list:
        cmd_list()
        return

    if args.source is not None:
        cmd_switch(args.source)
        return

    cmd_download(
        version=None if args.latest else args.version,
        keep_archive=args.keep_archive,
        no_patch=args.no_patch_makefile,
        base_url=args.mirror or DEFAULT_BASE_URL,
        proxy=args.proxy,
        prefer_system=args.prefer_system,
    )


if __name__ == "__main__":
    main()
