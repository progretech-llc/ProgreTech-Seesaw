"""Desktop job orchestration; published files remain unqualified hardware candidates."""

from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Signal

from seesaw.model import load_stl
from seesaw.pipeline import PipelineError, run_pipeline
from seesaw.profiles import printer_by_id
from seesaw.scene import check_placement, scene_mesh


class SliceWorker(QThread):
    progress = Signal(str)
    succeeded = Signal(object, object)
    failed = Signal(str)

    def __init__(self, project, root, parent=None):
        super().__init__(parent)
        self.project = project
        self.root = Path(root)
        self.cancel = Event()

    def run(self):
        try:
            project = self.project
            if project.settings is None:
                raise ValueError("Enter explicit resin exposure settings first.")
            project.verify_source()
            mesh, _ = load_stl(project.model_path)
            project.verify_source()
            prepared = scene_mesh(mesh, project)
            printer = printer_by_id(project.printer_id)
            check_placement(prepared, printer.build_mm)
            if any(t.translation_mm[2] for t in (project.transform, *project.copies)):
                raise ValueError("Individual Z offsets are unsupported; keep copies on the bed.")
            self.root.mkdir(parents=True, exist_ok=False)
            source = self.root / "prepared.stl"
            prepared.export(source)
            if self.cancel.is_set():
                raise PipelineError("Job cancelled.")
            directory = self.root / "pipeline"
            center = prepared.bounds.mean(axis=0)[:2] + [
                value / 2 for value in printer.build_mm[:2]
            ]
            if printer.technology == "resin":
                from seesaw.hollowing import placed_holes

                holes = tuple(h for transform in (project.transform, *project.copies)
                              for h in placed_holes(mesh, transform, project.hollowing.holes))
                manifest = run_pipeline(
                    source,
                    directory,
                    project.settings,
                    self.cancel,
                    self.progress.emit,
                    center=center,
                    repair_single_pixels=project.repair_single_pixels,
                    hollowing=project.hollowing,
                    drain_holes=holes,
                )
            else:
                from seesaw.fdm import run_fdm

                manifest = run_fdm(
                    source, directory, project.settings, center, self.cancel, self.progress.emit
                )
            manifest.update(
                printer_profile={"id": printer.id, "revision": printer.revision},
                material_profile=project.material.to_dict() if project.material else None,
                project_inputs=project.to_dict(),
            )
            import json

            (directory / "manifest.json").write_text(json.dumps(manifest, indent=2))
            if self.cancel.is_set():
                raise PipelineError("Job cancelled.")
            project.verify_source()
            self.succeeded.emit(directory, manifest)
        except Exception as exc:
            self.failed.emit(str(exc))


class PreviewWorker(QThread):
    loaded = Signal(int, bytes)
    failed = Signal(str)

    def __init__(self, preview, index, parent=None):
        super().__init__(parent)
        self.preview, self.index = preview, index

    def run(self):
        try:
            self.loaded.emit(self.index, self.preview.thumbnail(self.index))
        except Exception as exc:
            self.failed.emit(str(exc))


class ExportWorker(QThread):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, project, source, destination, expected_hash, parent=None):
        super().__init__(parent)
        self.project = project
        self.source, self.destination, self.expected_hash = source, destination, expected_hash
        self.cancel = Event()

    def run(self):
        from seesaw.export import export_candidate

        try:
            self.project.verify_source()
            export_candidate(
                self.source,
                self.destination,
                self.expected_hash,
                self.cancel,
                self.project.verify_source,
                extension=printer_by_id(self.project.printer_id).extension,
            )
            self.succeeded.emit(str(self.destination))
        except Exception as exc:
            self.failed.emit(str(exc))
