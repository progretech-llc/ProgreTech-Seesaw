import hashlib
import json

import pytest

from seesaw.engines import EngineError, bundled, resolve


def bundle(root):
    root.mkdir()
    files = {}
    for name in ("prusa", "uvtools", "lib.so"):
        path = root / name
        path.write_text("pinned engine" + name)
        path.chmod(0o755)
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "platform": "ubuntu26-amd64",
                "files": files,
                "executables": {"prusa_slicer": "prusa", "uvtools": "uvtools"},
            }
        )
    )
    return root


def test_bundle_resolution_and_tampered_dependency(tmp_path, monkeypatch):
    root = bundle(tmp_path / "engines")
    monkeypatch.setenv("SEESAW_ENGINE_ROOT", str(root))
    assert resolve()["prusa_slicer"] == str(root / "prusa")
    (root / "lib.so").write_text("tampered")
    with pytest.raises(EngineError, match="integrity"):
        bundled(root)


def test_installed_missing_bundle_never_uses_ambient_path(tmp_path, monkeypatch):
    monkeypatch.setattr("seesaw.engines.installed_root", lambda: tmp_path / "absent")
    monkeypatch.setenv("SEESAW_ENGINE_ROOT", "/ignore-this-installed-override")
    with pytest.raises(EngineError, match="reinstall"):
        resolve()


def test_bundle_symlink_and_traversal_denied(tmp_path):
    root = bundle(tmp_path / "engines")
    (root / "prusa").unlink()
    (root / "prusa").symlink_to(root / "uvtools")
    with pytest.raises(EngineError):
        bundled(root)
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["files"]["../outside"] = "0" * 64
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(EngineError):
        bundled(root)


@pytest.mark.parametrize(
    "change",
    [
        [],
        {"files": []},
        {"executables": []},
        {"executables": {"prusa_slicer": 123, "uvtools": "uvtools"}},
        {"files": {"prusa": "not-a-hash"}},
    ],
)
def test_malformed_installed_inventory_is_actionable(tmp_path, monkeypatch, change):
    root = bundle(tmp_path / "engines")
    data = json.loads((root / "manifest.json").read_text())
    data = change if isinstance(change, list) else data | change
    (root / "manifest.json").write_text(json.dumps(data))
    monkeypatch.setattr("seesaw.engines.installed_root", lambda: root)
    with pytest.raises(EngineError, match="reinstall"):
        resolve()
