"""Bounded native support/orientation search; no deletion of structural islands."""

import json
import math
import time
from dataclasses import replace
from pathlib import Path

from seesaw.model import load_stl
from seesaw.pipeline import PipelineError
from seesaw.profiles import printer_by_id
from seesaw.scene import check_placement, scene_mesh

DELTAS = (
    (0, 0, 0),
    (5, 0, 0),
    (-5, 0, 0),
    (0, 5, 0),
    (0, -5, 0),
    (10, 0, 0),
    (-10, 0, 0),
    (0, 10, 0),
    (0, -10, 0),
    (35, 0, 0),
)


class SmartSliceError(PipelineError):
    def __init__(self, message, directory=None):
        super().__init__(message)
        self.directory = directory


class DeadlineCancel:
    def __init__(self, cancel, deadline):
        self.cancel, self.deadline = cancel, deadline

    def is_set(self):
        return self.cancel.is_set() or time.monotonic() >= self.deadline


def run_smart(project, root, cancel, progress=lambda _: None, *, runner=None, budget_s=1200):
    if printer_by_id(project.printer_id).technology != "resin" or project.settings is None:
        raise SmartSliceError("Smart slice requires resin settings with resolved exposures.")
    if runner is None:
        from seesaw.desktop_jobs import slice_project

        runner = slice_project
    project.verify_source()
    mesh, _ = load_stl(project.model_path)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    report = {"original_inputs": project.to_dict(), "attempts": [], "status": "running"}
    bounded = DeadlineCancel(cancel, time.monotonic() + budget_s)
    last = None

    def save():
        temporary = root / "smart-report.tmp"
        temporary.write_text(json.dumps(report, indent=2))
        temporary.replace(root / "smart-report.json")

    def stopped():
        if bounded.is_set():
            report["status"] = "cancelled" if cancel.is_set() else "budget_exhausted"
            save()
            raise SmartSliceError(
                "Smart slice cancelled."
                if cancel.is_set()
                else "Smart slice reached its 20 minute budget.",
                last,
            )

    try:
        for number, delta in enumerate(DELTAS, 1):
            stopped()

            def tilted(transform):
                return replace(
                    transform,
                    rotation_deg=tuple(
                        a + b for a, b in zip(transform.rotation_deg, delta, strict=True)
                    ),
                )

            trial = project.edited(
                transform=tilted(project.transform),
                copies=tuple(tilted(t) for t in project.copies),
                settings=replace(project.settings, supports=True),
                repair_single_pixels=True,
            )
            entry = {"rotation_delta_deg": delta, "status": "preflight"}
            report["attempts"].append(entry)
            try:
                prepared = scene_mesh(mesh, trial)
                check_placement(prepared, printer_by_id(trial.printer_id).build_mm)
                if math.ceil((prepared.extents[2] + 7) / trial.settings.layer_mm) > 512:
                    raise ValueError("Preparation would exceed the 512-layer limit.")
            except ValueError as exc:
                entry.update(status="skipped", reason=str(exc))
                save()
                continue
            last = root / f"attempt-{number:02d}"
            entry["directory"] = last.name
            progress(f"Smart slice attempt {number}/{len(DELTAS)}: supports, tilt {delta}")
            save()
            try:
                directory, manifest = runner(trial, last, bounded, progress, smart_preflight=True)
            except PipelineError as exc:
                stopped()
                entry.update(status="rejected", reason=str(exc))
                save()
                if "UVTools reported issues" in str(exc) or "singleton islands" in str(exc):
                    continue
                raise
            stopped()
            trial.verify_source()
            if (
                manifest.get("status") != "software_validated_not_print_qualified"
                or manifest.get("project_inputs") != trial.to_dict()
            ):
                raise PipelineError("Smart slice result did not match validated inputs.")
            entry["status"] = "accepted"
            report["status"] = "accepted"
            save()
            manifest["smart_slice"] = {
                "rotation_delta_deg": delta,
                "report": str(root / "smart-report.json"),
            }
            (directory / "manifest.json").write_text(json.dumps(manifest, indent=2))
            return directory, manifest, trial
        report["status"] = "exhausted"
        save()
        raise SmartSliceError(
            "Smart slice could not resolve layer issues within the bounded "
            "support/orientation search. Review job details; export is blocked.",
            last,
        )
    except SmartSliceError:
        raise
    except Exception as exc:
        report["status"] = "failed"
        save()
        raise SmartSliceError(str(exc), last) from exc
