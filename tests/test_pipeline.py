import io
import json
import sys
import time
import zipfile
from dataclasses import replace
from threading import Event, Timer

import numpy as np
import pytest
import trimesh
from PIL import Image

from seesaw import pipeline


@pytest.mark.parametrize(
    "change",
    [
        {"exposure_s": float("nan")},
        {"bottom_exposure_s": 0},
        {"bottom_layers": 0},
        {"lift_mm_min": float("inf")},
        {"layer_mm": -0.05},
    ],
)
def test_rejects_invalid_settings(change):
    with pytest.raises(pipeline.PipelineError):
        replace(pipeline.Settings(2.5, 25), **change).validate()


def test_profile_maps_bottom_count_and_speed_units_explicitly():
    profile = pipeline.profile_text(pipeline.Settings(2.5, 25, bottom_layers=6, lift_mm_min=90))
    assert "faded_layers = 6\n" in profile
    assert "LiftSpeed_90" in profile
    assert "TransitionLayerCount_0" in profile
    assert "post_process" not in profile
    assert "gamma_correction = 0\n" in profile


def test_metadata_mismatch_and_nonfinite_fail_closed():
    with pytest.raises(pipeline.PipelineError):
        pipeline.expect({"BottomLayerCount": "0"}, "BottomLayerCount", 5)
    with pytest.raises(pipeline.PipelineError):
        pipeline.expect({"ExposureTime": "NaN"}, "ExposureTime", 2.5)
    with pytest.raises(pipeline.PipelineError):
        pipeline.properties("ExposureTime: 2.5\nExposureTime: 20\n")


def archive(path, values):
    data = io.BytesIO()
    Image.fromarray(values).save(data, format="PNG")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("layer00000.png", data.getvalue())


def test_quantized_roundtrip_is_exact_not_an_arbitrary_tolerance(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "MONO4", replace(pipeline.MONO4, resolution_px=(4, 2)))
    original = np.array([[0, 16, 58, 255], [250, 75, 127, 128]], dtype=np.uint8)
    expected = (original >> 4) * 17
    a, b = tmp_path / "a.sl1", tmp_path / "b.sl1"
    archive(a, original)
    archive(b, expected)
    assert pipeline.compare_layers(a, b, 1, Event())["verified_layers"] == 1
    archive(b, np.fliplr(expected))
    with pytest.raises(pipeline.PipelineError, match="quantization"):
        pipeline.compare_layers(a, b, 1, Event())


def test_process_cancel_terminates_worker(tmp_path):
    event = Event()
    timer = Timer(0.15, event.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(pipeline.PipelineError, match="cancelled"):
            pipeline.run_process(
                [sys.executable, "-c", "import time;time.sleep(30)"], tmp_path / "cancel.log", event
            )
    finally:
        timer.cancel()
    assert time.monotonic() - started < 5


def test_backend_failure_and_timeout_are_not_success(tmp_path):
    with pytest.raises(pipeline.PipelineError, match="failed"):
        pipeline.run_process(
            [sys.executable, "-c", "raise SystemExit(7)"], tmp_path / "failure.log", Event()
        )
    with pytest.raises(pipeline.PipelineError, match="deadline"):
        pipeline.run_process(
            [sys.executable, "-c", "import time;time.sleep(30)"],
            tmp_path / "timeout.log",
            Event(),
            timeout=0.1,
        )


def test_bad_decoded_metadata_removes_candidate(tmp_path, monkeypatch):
    model = tmp_path / "cube.stl"
    trimesh.creation.box(extents=(1, 1, 1)).export(model)
    job = tmp_path / "job"
    monkeypatch.setattr(pipeline, "discover", lambda: {"prusa_slicer": "prusa", "uvtools": "uv"})

    def fake_run(argv, log, cancel):
        if log.stem == "prusa-version":
            return "PrusaSlicer-2.9.4 test"
        if log.stem == "uvtools-version":
            return "7.0.1"
        if log.stem == "slice":
            with zipfile.ZipFile(job / "layers.sl1", "w") as z:
                for i in range(6):
                    z.writestr(f"layer{i:05}.png", b"not needed before metadata validation")
        if log.stem == "encode":
            (job / ".pending.pm4n").write_bytes(b"x" * 200)
        if log.stem == "metadata":
            return "MachineName: Wrong printer\n"
        return ""

    monkeypatch.setattr(pipeline, "run_process", fake_run)
    with pytest.raises(pipeline.PipelineError, match="MachineName"):
        pipeline.run_pipeline(model, job, pipeline.Settings(2.5, 25))
    assert not (job / "candidate.pm4n").exists()
    assert not (job / ".pending.pm4n").exists()
    assert json.loads((job / "manifest.json").read_text())["status"] == "failed"


def test_smart_preflight_blocks_large_islands_before_encoding(tmp_path, monkeypatch):
    model = tmp_path / "cube.stl"
    trimesh.creation.box(extents=(1, 1, 1)).export(model)
    job = tmp_path / "job"
    stages = []
    report = "Issues: 1\nIsland, 3, 100px², {X=10,Y=20,Width=10,Height=10}\n"
    monkeypatch.setattr(pipeline, "discover", lambda: {"prusa_slicer": "prusa", "uvtools": "uv"})

    def fake_run(argv, log, cancel):
        stages.append(log.stem)
        if log.stem == "prusa-version":
            return "PrusaSlicer-2.9.4 test"
        if log.stem == "uvtools-version":
            return "7.0.1"
        if log.stem == "slice":
            with zipfile.ZipFile(job / "layers.sl1", "w") as archive:
                for index in range(6):
                    archive.writestr(f"layer{index:05}.png", b"not used before rejection")
        if log.stem == "repair-findings":
            return report
        return ""

    monkeypatch.setattr(pipeline, "run_process", fake_run)
    with pytest.raises(pipeline.PipelineError, match="unsupported islands"):
        pipeline.run_pipeline(model, job, pipeline.Settings(2.5, 25),
                              repair_single_pixels=True, reject_large_islands_early=True)
    assert "encode" not in stages and "repair-single-pixels" not in stages
    assert (job / "issues.log").read_text() == report
    assert json.loads((job / "manifest.json").read_text())["layer_count"] == 6
    assert not (job / "candidate.pm4n").exists()
