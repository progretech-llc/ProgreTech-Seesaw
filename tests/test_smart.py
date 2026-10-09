"""Smart preparation accepts only a matching validated attempt and never retries faults."""

import json
from dataclasses import replace
from threading import Event

import pytest
import trimesh

from seesaw.hollowing import DrainHole, Hollowing
from seesaw.pipeline import PipelineError, Settings
from seesaw.project import Project, Transform
from seesaw.smart import DeadlineCancel, SmartSliceError, run_smart


@pytest.fixture
def project(tmp_path):
    source = tmp_path / "model.stl"
    trimesh.creation.box(extents=(2, 3, 4)).export(source)
    return Project.from_stl_path(source, settings=Settings(2.8, 30))


def accepting(project, root, cancel, progress, **kwargs):
    directory = root / "pipeline"
    directory.mkdir(parents=True)
    return directory, {
        "status": "software_validated_not_print_qualified",
        "project_inputs": project.to_dict(),
    }


def test_retries_findings_and_preserves_user_settings(project, tmp_path):
    project = project.edited(
        hollowing=Hollowing(True, 1, (DrainHole((0, 0, -2), (0, 0, 1)),)),
        transform=Transform((4, 2, 0), (0, 0, 15), 1.5),
        copies=(Transform((-4, 2, 0), (0, 0, 20), 0.8),),
    )
    calls = []

    def runner(trial, root, cancel, progress, **kwargs):
        calls.append(trial)
        assert kwargs == {"smart_preflight": True}
        if len(calls) == 1:
            raise PipelineError("UVTools reported issues: islands")
        return accepting(trial, root, cancel, progress)

    directory, manifest, selected = run_smart(project, tmp_path / "job", Event(), runner=runner)
    assert len(calls) == 2
    assert selected.settings == replace(project.settings, supports=True)
    assert selected.auto_exposure == project.auto_exposure
    assert selected.material == project.material and selected.hollowing == project.hollowing
    assert selected.model_sha256 == project.model_sha256
    assert selected.transform == replace(project.transform, rotation_deg=(5, 0, 15))
    assert selected.copies == (replace(project.copies[0], rotation_deg=(5, 0, 20)),)
    assert selected.repair_single_pixels
    assert manifest["project_inputs"] == selected.to_dict()
    assert directory.parent.name == "attempt-02"
    assert json.loads((tmp_path / "job/smart-report.json").read_text())["status"] == "accepted"


@pytest.mark.parametrize("failure", ["native executable missing", "corrupt archive"])
def test_backend_fault_stops_search(project, tmp_path, failure):
    calls = []

    def runner(*args, **kwargs):
        calls.append(1)
        raise PipelineError(failure)

    with pytest.raises(SmartSliceError, match=failure):
        run_smart(project, tmp_path / "job", Event(), runner=runner)
    assert len(calls) == 1
    assert json.loads((tmp_path / "job/smart-report.json").read_text())["status"] == "failed"


def test_cancel_during_attempt_cannot_accept_or_retry(project, tmp_path):
    cancel = Event()

    def runner(*args, **kwargs):
        cancel.set()
        return accepting(*args, **kwargs)

    with pytest.raises(SmartSliceError, match="cancelled"):
        run_smart(project, tmp_path / "job", cancel, runner=runner)
    report = json.loads((tmp_path / "job/smart-report.json").read_text())
    assert report["status"] == "cancelled" and len(report["attempts"]) == 1


def test_deadline_stops_owned_runner(project, tmp_path, monkeypatch):
    monkeypatch.setattr("seesaw.smart.time.monotonic", lambda: 100)
    assert DeadlineCancel(Event(), 99).is_set()
    with pytest.raises(SmartSliceError, match="budget"):
        run_smart(project, tmp_path / "job", Event(), runner=accepting, budget_s=0)


def test_mismatched_input_result_cannot_be_adopted(project, tmp_path):
    def runner(*args, **kwargs):
        directory, manifest = accepting(*args, **kwargs)
        manifest["project_inputs"] = {}
        return directory, manifest

    with pytest.raises(SmartSliceError, match="match validated inputs"):
        run_smart(project, tmp_path / "job", Event(), runner=runner)


def test_all_findings_leave_report_without_accepted_result(project, tmp_path):
    def runner(*args, **kwargs):
        raise PipelineError("UVTools reported issues")

    with pytest.raises(SmartSliceError, match="could not resolve"):
        run_smart(project, tmp_path / "job", Event(), runner=runner)
    report = json.loads((tmp_path / "job/smart-report.json").read_text())
    assert report["status"] == "exhausted" and len(report["attempts"]) == 10


def test_over_limit_candidates_skip_before_native_work(project, tmp_path):
    project = project.edited(settings=replace(project.settings, layer_mm=0.01))
    calls = []

    def runner(*args, **kwargs):
        calls.append(1)

    with pytest.raises(SmartSliceError, match="could not resolve"):
        run_smart(project, tmp_path / "job", Event(), runner=runner)
    assert calls == []


def test_unresolved_exposure_rejected_before_job(project, tmp_path):
    with pytest.raises(SmartSliceError, match="resolved exposures"):
        run_smart(project.edited(settings=None), tmp_path / "job", Event(), runner=accepting)
    assert not (tmp_path / "job").exists()
