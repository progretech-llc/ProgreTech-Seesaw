from dataclasses import replace

import numpy as np
import pytest
import trimesh

from seesaw.fdm_settings import FDMSettings
from seesaw.pipeline import Settings
from seesaw.project import JobGate, Project, Transform, load_project, save_project
from seesaw.scene import check_placement, placed_mesh, scene_mesh


def project(tmp_path):
    path = tmp_path / "box.stl"
    mesh = trimesh.creation.box((4, 6, 2))
    mesh.export(path)
    return mesh, Project.from_stl_path(path, settings=Settings(2.5, 25))


def test_copy_placement_and_source_unchanged(tmp_path):
    mesh, p = project(tmp_path)
    before = mesh.vertices.copy()
    first = Transform((-20, 10, 0), (15, 20, 30), 2)
    second = Transform((20, -10, 0), (0, 0, 90), 1)
    p = p.edited(transform=first, copies=(second,))
    for transform in (first, second):
        prepared = placed_mesh(mesh, transform)
        assert np.allclose(prepared.bounds.mean(axis=0)[:2], transform.translation_mm[:2])
        assert prepared.bounds[0, 2] == pytest.approx(0)
    joined = scene_mesh(mesh, p)
    assert len(joined.faces) == len(mesh.faces) * 2
    check_placement(joined, (153.408, 87.04, 165))
    assert np.array_equal(mesh.vertices, before)


def test_position_not_just_dimensions_determines_fit(tmp_path):
    mesh, p = project(tmp_path)
    mesh = placed_mesh(mesh, Transform((100, 0, 0)))
    with pytest.raises(ValueError, match="outside"):
        check_placement(mesh, (153.408, 87.04, 165))


def test_copies_and_printer_invalidate_job_and_roundtrip(tmp_path):
    _, p = project(tmp_path)
    gate = JobGate()
    snapshot = gate.begin(p)
    assert gate.finish(p, snapshot)
    p = p.edited(copies=(Transform((20, 0, 0)),))
    assert not gate.is_current(p)
    path = tmp_path / "copies.seesaw"
    save_project(p, path)
    assert load_project(path).fingerprint() == p.fingerprint()
    with pytest.raises(ValueError, match="technology"):
        p.edited(printer_id="mk3s")
    fdm = p.edited(printer_id="mk3s", settings=FDMSettings())
    save_project(fdm, path)
    assert type(load_project(path).settings) is FDMSettings


def test_legacy_project_migration_and_revision_rejection(tmp_path):
    _, p = project(tmp_path)
    data = p.to_dict()
    for name in ("copies", "material", "printer_revision", "repair_single_pixels", "hollowing"):
        del data[name]
    data["schema"] = "version1"
    restored = Project.from_dict(data)
    assert restored.copies == () and restored.printer_id == "mono4"
    with pytest.raises(ValueError):
        replace(restored, printer_revision=2)
    with pytest.raises(ValueError):
        replace(restored, copies=tuple(Transform() for _ in range(32)))


def test_repair_choice_is_versioned_and_invalidates_job(tmp_path):
    _, p = project(tmp_path)
    original = p.fingerprint()
    edited = p.edited(repair_single_pixels=True)
    assert edited.fingerprint() != original
    assert Project.from_dict(edited.to_dict()).repair_single_pixels
    old = p.to_dict()
    old["schema"] = "version2"
    del old["repair_single_pixels"], old["hollowing"]
    assert not Project.from_dict(old).repair_single_pixels
    with pytest.raises(ValueError):
        p.edited(repair_single_pixels=1)
    with pytest.raises(ValueError):
        edited.edited(printer_id="mk3s", settings=FDMSettings())
