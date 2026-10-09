"""Explicit, user-initiated updates from this project's published GitHub releases.

No requests happen on import or application startup. Never update from a Git checkout.
GitHub HTTPS metadata and SHA-256 protect transport/integrity, not a compromised publisher.
"""

import hashlib
import json
import os
import platform
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPOSITORY = "progretech-llc/ProgreTech-Seesaw"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_PACKAGE = 2 * 1024**3
ALLOWED_HOSTS = {
    "api.github.com",
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
}


class UpdateError(Exception):
    pass


class Cancelled(UpdateError):
    pass


def version_tuple(version: str) -> tuple[int, int, int]:
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise UpdateError("Release version must use major.minor.patch format.")
    return tuple(int(part) for part in version.split("."))


def validate_url(url: str):
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise UpdateError("Update URL is outside the approved GitHub HTTPS hosts.")


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url):
    validate_url(url)
    request = Request(
        url,
        headers={
            "User-Agent": "ProgreTech-Seesaw-Updater",
            "Accept": "application/vnd.github+json"
            if url == API_URL
            else "application/octet-stream",
            "X-GitHub-Api-Version": "2026-03-10",
        },
    )
    return build_opener(SafeRedirect).open(request, timeout=20)


@dataclass(frozen=True)
class Release:
    version: str
    filename: str
    url: str
    sha256: str
    size: int


def parse_release(data: dict, current: str) -> Release | None:
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        raise UpdateError("The release is not a published regular release.")
    tag = data.get("tag_name", "")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise UpdateError("Missing release version tag.")
    version = tag[1:]
    if version_tuple(version) <= version_tuple(current):
        return None
    filename = f"progretech-seesaw_{version}_amd64.deb"
    assets = data.get("assets", [])
    matches = [a for a in assets if isinstance(a, dict) and a.get("name") == filename]
    if len(matches) != 1:
        raise UpdateError("The release does not have exactly one Ubuntu amd64 installer.")
    asset = matches[0]
    digest = asset.get("digest", "")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise UpdateError("GitHub has not supplied a SHA-256 digest for this installer.")
    expected_url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{filename}"
    if asset.get("browser_download_url") != expected_url:
        raise UpdateError("Installer URL does not match this repository and release.")
    size = asset.get("size")
    if not isinstance(size, int) or not 0 < size <= MAX_PACKAGE:
        raise UpdateError("Installer size is missing or exceeds the 2 GiB update limit.")
    return Release(version, filename, expected_url, digest[7:], size)


def check_latest(current: str) -> Release | None:
    if platform.machine() not in ("x86_64", "AMD64"):
        raise UpdateError("Self-update currently supports Ubuntu amd64 only.")
    try:
        with open_url(API_URL) as response:
            body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise UpdateError("GitHub returned an unexpectedly large release response.")
        return parse_release(json.loads(body), current)
    except HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("No published release is available yet.") from exc
        if exc.code in (403, 429):
            raise UpdateError("GitHub rate limit reached. Please check again later.") from exc
        raise UpdateError(f"GitHub returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise UpdateError("Could not reach GitHub. Check your connection and try again.") from exc
    except (ValueError, TypeError, AttributeError) as exc:
        raise UpdateError("GitHub returned invalid release metadata.") from exc


def download_release(release: Release, cache: Path, cancel: Event, progress=lambda _: None) -> Path:
    """Download into a unique private directory; caller owns cleanup after installation."""
    cache.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(cache).free < release.size * 2 + 100 * 1024**2:
        raise UpdateError("Not enough free space to download and verify the update.")
    directory = Path(tempfile.mkdtemp(prefix="seesaw-update-", dir=cache))
    partial = directory / "download.part"
    destination = directory / release.filename
    digest = hashlib.sha256()
    count = 0
    deadline = time.monotonic() + 1800
    try:
        with open_url(release.url) as response, partial.open("xb") as output:
            while True:
                if cancel.is_set():
                    raise Cancelled("Update download cancelled.")
                if time.monotonic() > deadline:
                    raise UpdateError("Update download timed out.")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > release.size:
                    raise UpdateError("Downloaded installer exceeds its declared size.")
                output.write(chunk)
                digest.update(chunk)
                progress(int(count * 100 / release.size))
            output.flush()
            os.fsync(output.fileno())
        if count != release.size or digest.hexdigest() != release.sha256:
            raise UpdateError("Installer verification failed. Nothing was installed.")
        partial.rename(destination)
        return destination
    except Exception:
        shutil.rmtree(directory)
        raise
