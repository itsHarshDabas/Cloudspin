"""Finding mpv and yt-dlp, and fetching mpv on Windows where there is no
package manager to lean on.

yt-dlp comes with Cloudspin as a Python dependency. mpv is a separate program:
on Linux and macOS it comes from the system's package manager; on Windows
Cloudspin downloads a build once into its cache folder.
"""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from . import __version__
from .config import cache_dir

WINDOWS = sys.platform == "win32"
RELEASES = "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"


class MpvInstallError(Exception):
    """mpv could not be fetched; the message is written for the user."""


def _bundled_mpv() -> Path:
    return cache_dir() / "mpv" / "mpv.exe"


def find_mpv() -> str | None:
    """mpv from the PATH, or the copy Cloudspin downloaded earlier."""
    found = shutil.which("mpv")
    if found:
        return found
    if WINDOWS and _bundled_mpv().exists():
        return str(_bundled_mpv())
    return None


def find_ytdlp() -> str | None:
    """yt-dlp from the same Python environment as Cloudspin, or from the PATH."""
    name = "yt-dlp.exe" if WINDOWS else "yt-dlp"
    beside = Path(sys.executable).parent / name
    if beside.exists():
        return str(beside)
    return shutil.which("yt-dlp")


def install_hint() -> str:
    """The command that installs mpv on this machine, as far as can be told."""
    if sys.platform == "darwin":
        return "brew install mpv"
    for tool, command in (
        ("pacman", "sudo pacman -S mpv"),
        ("apt", "sudo apt install mpv"),
        ("dnf", "sudo dnf install mpv"),
        ("zypper", "sudo zypper install mpv"),
    ):
        if shutil.which(tool):
            return command
    return "install mpv with your package manager"


def _asset_pattern() -> re.Pattern[str]:
    arch = "aarch64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    return re.compile(rf"^mpv-{arch}-\d{{8}}-git-[0-9a-f]+\.7z$")


def _open(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": f"cloudspin/{__version__}"})
    return urllib.request.urlopen(request, timeout=30)


def download_mpv(progress: Callable[[int, int], None] | None = None) -> str:
    """Windows only: download mpv into the cache folder and return its path."""
    target = _bundled_mpv()
    folder = target.parent
    try:
        with _open(RELEASES) as response:
            release = json.loads(response.read().decode("utf-8"))
        pattern = _asset_pattern()
        asset = next((a for a in release.get("assets", []) if pattern.match(a.get("name", ""))), None)
        if asset is None:
            raise MpvInstallError("No mpv build for this computer was found to download.")
        folder.mkdir(parents=True, exist_ok=True)
        archive = folder / "mpv.7z"
        total = int(asset.get("size") or 0)
        done = 0
        with _open(asset["browser_download_url"]) as response, open(archive, "wb") as out:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as error:
        raise MpvInstallError(f"mpv couldn't be downloaded ({error}).") from error
    # Windows 10 and later ship a tar that reads 7z archives.
    try:
        subprocess.run(
            ["tar", "-xf", str(archive), "-C", str(folder)],
            check=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=300,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError) as error:
        raise MpvInstallError("mpv was downloaded but couldn't be unpacked.") from error
    finally:
        archive.unlink(missing_ok=True)
    if not target.exists():
        raise MpvInstallError("mpv was downloaded but mpv.exe wasn't inside.")
    return str(target)
