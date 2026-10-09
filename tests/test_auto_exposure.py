from dataclasses import replace

import pytest
import trimesh

from seesaw.issues import summarize_issues
from seesaw.material_store import builtins, resolve_exposures, settings_for
from seesaw.pipeline import Settings
from seesaw.project import JobGate, Project, load_project, save_project


def test_auto_uses_profile_and_manual_override_without_layer_extrapolation():
    material = next(p for p in builtins() if p.id == "anycubic-water-wash-2-mono4")
    settings = settings_for(material)
    assert resolve_exposures(material, settings, 0, 0, (True, True)) == settings
    mixed = resolve_exposures(material, settings, 3.1, 0, (False, True))
    assert mixed.exposure_s == 3.1 and mixed.bottom_exposure_s == 30
    assert resolve_exposures(material, replace(settings, layer_mm=0.1), 0, 0, (True, True)) is None
    assert resolve_exposures(builtins()[0], Settings(1, 1), 0, 0, (True, True)) is None
    assert resolve_exposures(None, Settings(1, 1), 0, 25, (True, False)) is None


def test_auto_roundtrip_migration_and_gate(tmp_path):
    path = tmp_path / "fixture.stl"
    trimesh.creation.box().export(path)
    material = next(p for p in builtins() if p.id == "anycubic-water-wash-2-mono4")
    old = Project.from_stl_path(path, settings=settings_for(material)).edited(material=material)
    gate = JobGate()
    snapshot = gate.begin(old)
    assert gate.finish(old, snapshot)
    auto = old.edited(auto_exposure=(True, True))
    assert not gate.is_current(auto)
    saved = tmp_path / "auto.seesaw"
    save_project(auto, saved)
    assert load_project(saved).fingerprint() == auto.fingerprint()
    assert load_project(saved).auto_exposure == (True, True)
    data = auto.to_dict()
    data["schema"] = "version4"
    del data["auto_exposure"]
    assert Project.from_dict(data).auto_exposure == (False, False)
    data["auto_exposure"] = [True, True]
    with pytest.raises(ValueError):
        Project.from_dict(data)
    with pytest.raises(ValueError, match="match"):
        auto.edited(settings=replace(auto.settings, exposure_s=9))
    with pytest.raises(ValueError, match="boolean"):
        auto.edited(auto_exposure=(1, True))


def test_summary_reports_real_layer_and_large_island():
    report = "Issues: 1\nIsland, 176, 286704px², {X=4424,Y=1131,Width=176,Height=1629}\n"
    assert "layer 177" in summarize_issues(report)
    assert "286,704" in summarize_issues(report)
    assert "Exposure changes do not" in summarize_issues(report)
    assert "ResinTrap" in summarize_issues("Issues: 1\nResinTrap, 10-39  (30)\n")
    with pytest.raises(ValueError):
        summarize_issues("Issues: 0\n")
