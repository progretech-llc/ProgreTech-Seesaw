from dataclasses import replace

import numpy as np
import pytest
import trimesh

from seesaw.hollowing import DrainHole, Hollowing, native_drain_metadata, placed_holes
from seesaw.pipeline import Settings
from seesaw.project import JobGate, Project, Transform, load_project, save_project
from seesaw.scene import placed_mesh


@pytest.mark.parametrize("rotation", [(0, 0, 0), (90, 0, 0), (0, 90, 0), (33, 47, 91)])
@pytest.mark.parametrize("scale", [0.5, 1, 2])
def test_holes_follow_source_geometry_affine(rotation, scale):
    mesh = trimesh.creation.box(extents=(10, 12, 14))
    mesh.vertices += [18, -31, 27]
    transform = Transform((22, -11, 0), rotation, scale)
    hole = DrainHole(tuple(float(v) for v in mesh.vertices[0]), (0, 0, 2), 1, 5)
    changed = placed_holes(mesh, transform, (hole,))[0]
    assert np.allclose(changed.position_mm, placed_mesh(mesh, transform).vertices[0])
    assert changed.radius_mm == scale and changed.depth_mm == 5 * scale
    assert np.linalg.norm(changed.direction) == pytest.approx(1)
    moved = placed_holes(mesh, replace(transform, translation_mm=(25, -4, 0)), (hole,))[0]
    assert np.allclose(np.array(moved.position_mm) - changed.position_mm, [3, 7, 0])


@pytest.mark.parametrize(
    "changes",
    [
        dict(direction=(0, 0, 0)),
        dict(radius_mm=True),
        dict(depth_mm=float("nan")),
        dict(position_mm=(1, 2)),
    ],
)
def test_invalid_holes(changes):
    with pytest.raises(ValueError):
        DrainHole(**(dict(position_mm=(0, 0, -5), direction=(0, 0, 1)) | changes))


def test_hollowing_roundtrip_migration_and_stale_gate(tmp_path):
    source = tmp_path / "cube.stl"
    trimesh.creation.box(extents=(10, 10, 10)).export(source)
    old = Project.from_stl_path(source, settings=Settings(2.5, 25))
    gate = JobGate()
    snapshot = gate.begin(old)
    hollowing = Hollowing(True, 2, (DrainHole((0, 0, -5), (0, 0, 1)),))
    new = old.edited(hollowing=hollowing)
    assert not gate.finish(new, snapshot)
    assert old.fingerprint() != new.fingerprint()
    target = tmp_path / "cube.seesaw"
    save_project(new, target)
    assert load_project(target) == new
    assert Project.from_dict(new.to_dict()) == new
    legacy = old.to_dict()
    legacy["schema"] = "version3"
    del legacy["hollowing"]
    assert not Project.from_dict(legacy).hollowing.enabled
    legacy["hollowing"] = hollowing.to_dict()
    with pytest.raises(ValueError):
        Project.from_dict(legacy)
    with pytest.raises(ValueError):
        Hollowing(True)
    with pytest.raises(ValueError, match="resin"):
        from seesaw.fdm_settings import FDMSettings

        new.edited(printer_id="mk3s", settings=FDMSettings())


def test_metadata_importer_compatibility_offsets_are_inverted():
    hole = DrainHole((0, 0, 0), (0, 0, 1), 1, 5)
    assert native_drain_metadata((hole,)) == (
        "drain_holes_format_version=1\nobject_id=1|0 0 -1 0 0 1 1 6\n"
    )


def test_direction_overflow_rejected():
    with pytest.raises(ValueError):
        DrainHole((0, 0, 0), (1.7976931348623157e308,) * 3)
