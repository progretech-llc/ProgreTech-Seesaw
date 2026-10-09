from pathlib import Path

import pytest
import trimesh

from seesaw.backends import discover, research_plan
from seesaw.model import MONO4, load_stl


def box(tmp_path, size=(10, 20, 30)):
    path = tmp_path / "model with spaces.stl"
    trimesh.creation.box(extents=size).export(path)
    return path


def test_inspects_real_stl(tmp_path):
    _, info = load_stl(box(tmp_path))
    assert info.dimensions_mm == pytest.approx((10, 20, 30))
    assert info.triangles == 12
    assert info.watertight
    assert info.fits_unrotated
    assert not MONO4.qualified


def test_oversized_geometry_is_not_marked_as_fitting(tmp_path):
    assert not load_stl(box(tmp_path, (200, 20, 30)))[1].fits_unrotated


def test_open_mesh_is_not_claimed_watertight(tmp_path):
    mesh = trimesh.creation.box()
    mesh.update_faces(list(range(10)))
    path = tmp_path / "open.stl"
    mesh.export(path)
    assert not load_stl(path)[1].watertight


def test_rejects_corrupt_stl(tmp_path):
    path = tmp_path / "broken.stl"
    path.write_bytes(b"not a mesh")
    with pytest.raises(ValueError):
        load_stl(path)


def test_rejects_wrong_extension(tmp_path):
    path = tmp_path / "model.obj"
    path.write_text("not imported")
    with pytest.raises(ValueError, match="STL only"):
        load_stl(path)


def test_plan_preserves_paths_and_does_not_execute(tmp_path):
    model = box(tmp_path)
    profile = tmp_path / "trusted.ini"
    profile.write_text("printer_technology = SLA\n")
    output = tmp_path / "new work directory"
    commands = research_plan(model, profile, output)
    assert commands[0][-1] == str(model)
    assert commands[1][1:4] == ["convert", str(output / "layers.sl1"), "pm4n"]
    assert commands[1][-1] == "--no-overwrite"
    assert not output.exists()


def test_discovery_does_not_claim_qualification(monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("seesaw.backends.resolve",
                        lambda: {"prusa_slicer": None, "uvtools": None})
    result = discover()
    assert result["prusa_slicer"] is None
    assert result["uvtools"] is None
    assert result["print_ready"] is False


def test_plan_rejects_missing_profile(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        research_plan(box(tmp_path), Path("absent.ini"), tmp_path)
