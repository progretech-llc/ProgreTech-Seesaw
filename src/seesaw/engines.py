"""Installed engines are package-owned and integrity checked; PATH is dev-only."""

import hashlib
import json
import os
import re
import shutil
from functools import lru_cache
from pathlib import Path


class EngineError(ValueError):
    pass


def installed_root():
    # Wheel is installed into /opt/progretech-seesaw/site/seesaw.
    module = Path(__file__).resolve()
    if (
        module.parent.parent.name == "site"
        and module.parent.parent.parent.name == "progretech-seesaw"
    ):
        return module.parent.parent.parent / "engines"
    return None


@lru_cache(maxsize=4)
def _verified(root, manifest_digest, signatures):
    data = json.loads((root / "manifest.json").read_text())
    if data.get("schema") != 1 or data.get("platform") != "ubuntu26-amd64":
        raise EngineError("Unsupported bundled engine manifest.")
    for relative, expected in data["files"].items():
        path = root / relative
        if path.is_symlink() or not path.is_file() or root not in path.resolve().parents:
            raise EngineError("Unsafe bundled engine file.")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise EngineError(f"Bundled engine integrity mismatch: {relative}")
    return data


def bundled(root):
    root = root.resolve(strict=True)
    path = root / "manifest.json"
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise EngineError("Unsafe bundled engine manifest.")
    raw = path.read_bytes()
    data = json.loads(raw)
    if type(data) is not dict:
        raise EngineError("Invalid bundled engine manifest object.")
    files = data.get("files")
    if type(files) is not dict or not 1 <= len(files) <= 10000:
        raise EngineError("Invalid bundled engine inventory.")
    executables = data.get("executables")
    if (
        type(executables) is not dict
        or set(executables) != {"prusa_slicer", "uvtools"}
        or any(type(v) is not str or v not in files for v in executables.values())
    ):
        raise EngineError("Invalid bundled engine entry points.")
    if any(type(v) is not str or not re.fullmatch("[0-9a-f]{64}", v) for v in files.values()):
        raise EngineError("Invalid bundled engine checksums.")
    signatures = []
    for relative in files:
        if (
            type(relative) is not str
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise EngineError("Invalid bundled engine path.")
        stat = (root / relative).stat()
        signatures.append((relative, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    checked = _verified(root, hashlib.sha256(raw).hexdigest(), tuple(signatures))
    result = {}
    for key in ("prusa_slicer", "uvtools"):
        relative = checked["executables"][key]
        if relative not in files or not os.access(root / relative, os.X_OK):
            raise EngineError("Missing bundled engine entry point.")
        result[key] = str(root / relative)
    return result


def resolve():
    root = installed_root()
    if root is not None:
        try:
            return bundled(root)
        except (OSError, KeyError, ValueError) as exc:
            raise EngineError(
                "Installed engine bundle is missing or damaged; reinstall Seesaw."
            ) from exc
    # Explicit override permits extracted package qualification without altering /opt.
    override = os.environ.get("SEESAW_ENGINE_ROOT")
    if override:
        return bundled(Path(override))
    return {
        "prusa_slicer": shutil.which("prusa-slicer") or shutil.which("PrusaSlicer"),
        "uvtools": shutil.which("UVtoolsCmd")
        or (
            "/usr/lib/uvtools/UVtoolsCmd"
            if os.access("/usr/lib/uvtools/UVtoolsCmd", os.X_OK)
            else None
        ),
    }
