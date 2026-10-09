"""Offline Mono 4 desktop preparation and software-validated calibration export."""

import json
import os
import sys
import uuid
from pathlib import Path
from zipfile import BadZipFile

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from pyvistaqt import QtInteractor

from seesaw import __version__
from seesaw.desktop_jobs import ExportWorker, PreviewWorker, SliceWorker
from seesaw.fdm import GCodePreview
from seesaw.geometry import prepare_mesh
from seesaw.model import load_stl
from seesaw.pipeline import Settings
from seesaw.preview import LayerPreview
from seesaw.profiles import printer_by_id
from seesaw.project import JobGate, Project, load_project, save_project
from seesaw.update_ui import UpdateButton
from seesaw.workspace_ui import WorkspaceControls


class ImportWorker(QThread):
    loaded = Signal(object, object)
    failed = Signal(str)

    def __init__(self, path, parent, project_file=False):
        super().__init__(parent)
        self.path = path
        self.project_file = project_file
        self.settings_template = Settings(1, 1)
        self.project = None

    def run(self):
        try:
            self.project = (
                load_project(self.path) if self.project_file else Project.from_stl_path(self.path)
            )
            mesh, info = load_stl(self.project.model_path)
            self.project.verify_source()
            prepare_mesh(mesh, self.project.transform)
            self.loaded.emit(mesh, info)
        except Exception as exc:
            self.failed.emit(str(exc))


class Window(WorkspaceControls, QMainWindow):
    def __init__(self):
        super().__init__()
        self.import_active = False
        self.slice_active = False
        self.export_active = False
        self.worker = None
        self.project = None
        self.project_path = None
        self.mesh = None
        self.inspection = None
        self.slice_worker = None
        self.preview_worker = None
        self.export_worker = None
        self.layer_preview = None
        self.preview_generation = 0
        self.gate = JobGate()
        self.candidate = None
        self.undo_stack = []
        self.job_directory = None
        self.setWindowTitle(f"ProgreTech Seesaw {__version__} — Mono 4 workspace")
        self.resize(1280, 800)
        experimental = self.menuBar().addMenu("Experimental")
        generation = experimental.addAction("Image to 3D / OpenClaw + Tailscale…")
        generation.triggered.connect(self.open_generation)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        title = QLabel("ProgreTech Seesaw")
        title.setStyleSheet("font-size: 26px; font-weight: bold")
        header = QHBoxLayout()
        header.addWidget(title, 1)
        self.updates = UpdateButton(self)
        help_button = QPushButton("Help")
        help_button.clicked.connect(self.show_help)
        self.open_project_button = QPushButton("Open project")
        self.open_project_button.clicked.connect(self.open_project)
        self.save_project_button = QPushButton("Save project")
        self.save_project_button.clicked.connect(self.save_current_project)
        self.save_project_button.setEnabled(False)
        header.addWidget(self.open_project_button)
        header.addWidget(self.save_project_button)
        header.addWidget(help_button)
        header.addWidget(self.updates)
        layout.addLayout(header)
        layout.addWidget(QLabel("Add model   →   Prepare   →   Preview   →   Export"))
        layout.addWidget(
            QLabel(
                "Offline workspace • Printer-specific calibration candidates "
                "• Hardware qualification pending"
            )
        )
        row = QHBoxLayout()
        layout.addLayout(row, 1)
        left_panel = QWidget()
        left = QVBoxLayout(left_panel)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setMinimumWidth(250)
        left_scroll.setWidget(left_panel)
        row.addWidget(left_scroll, 1)
        left.addWidget(QLabel("Your model"))
        self.add = QPushButton("＋ Add STL model")
        self.add.clicked.connect(self.open_model)
        left.addWidget(self.add)
        self.info = QLabel("Choose an STL to inspect.\nSTL units are assumed to be millimetres.")
        self.info.setWordWrap(True)
        left.addWidget(self.info)
        self.scale = QDoubleSpinBox()
        self.scale.setRange(0.01, 100)
        self.scale.setValue(1)
        self.scale.setSingleStep(0.1)
        self.scale.valueChanged.connect(self.invalidate_result)
        self.rotation = []
        form = QFormLayout()
        form.addRow("Scale", self.scale)
        for axis in "XYZ":
            spin = QDoubleSpinBox()
            spin.setRange(-360, 360)
            spin.setSuffix("°")
            spin.valueChanged.connect(self.invalidate_result)
            self.rotation.append(spin)
            form.addRow(f"Rotate {axis}", spin)
        left.addLayout(form)
        self.apply_transform = QPushButton("Apply transform")
        self.apply_transform.clicked.connect(self.transform_model)
        left.addWidget(self.apply_transform)
        self.undo = QPushButton("Undo")
        self.undo.clicked.connect(self.undo_transform)
        left.addWidget(self.undo)
        left.addStretch()
        frame = QFrame()
        viewport_layout = QVBoxLayout(frame)
        self.viewport = QtInteractor(frame)
        viewport_layout.addWidget(self.viewport.interactor)
        self.edge_toggle = QCheckBox("Show mesh edges")
        self.edge_toggle.setToolTip("Reveal triangulation without changing printable geometry.")
        self.edge_toggle.toggled.connect(self.toggle_edges)
        view_tools = QHBoxLayout()
        view_tools.addWidget(self.edge_toggle)
        focus_model = QPushButton("Focus model")
        focus_model.clicked.connect(self.focus_model)
        view_tools.addWidget(focus_model)
        viewport_layout.addLayout(view_tools)
        self.tabs = QTabWidget()
        self.tabs.addTab(frame, "Model")
        preview_page = QWidget()
        preview_layout = QVBoxLayout(preview_page)
        self.layer_image = QLabel("Slice to inspect decoded PM4N layers.")
        self.layer_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self.layer_image, 1)
        self.finding_box = QComboBox()
        self.finding_box.addItem("Full layer view", None)
        self.finding_box.currentIndexChanged.connect(self.select_finding)
        preview_layout.addWidget(self.finding_box)
        self.layer_info = QLabel("")
        preview_layout.addWidget(self.layer_info)
        self.layer_slider = QSlider(Qt.Orientation.Horizontal)
        self.layer_slider.setEnabled(False)
        self.layer_slider.valueChanged.connect(self.request_layer)
        preview_layout.addWidget(self.layer_slider)
        self.tabs.addTab(preview_page, "Layers")
        row.addWidget(self.tabs, 3)
        self.viewport.set_background("#eff3f4")
        self.viewport.add_axes()
        right_panel = QWidget()
        right = QVBoxLayout(right_panel)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setMinimumWidth(380)
        right_scroll.setWidget(right_panel)
        row.addWidget(right_scroll, 1)
        reset = QPushButton("Reset view")
        reset.clicked.connect(self.viewport.reset_camera)
        right.addWidget(reset)
        settings_form = QFormLayout()
        self.exposure = QDoubleSpinBox()
        self.bottom_exposure = QDoubleSpinBox()
        for spin, maximum in ((self.exposure, 120), (self.bottom_exposure, 300)):
            spin.setRange(0, maximum)
            spin.setSpecialValueText("Auto (material)")
            spin.setToolTip(
                "Auto uses this material's saved exposure at its saved layer height. "
                "Type a positive number for a manual override."
            )
            spin.setSuffix(" s")
            spin.valueChanged.connect(self.settings_changed)
        self.exposure_fields = []
        self.auto_buttons = []
        for label, spin in (
            ("Normal exposure", self.exposure),
            ("Bottom exposure", self.bottom_exposure),
        ):
            field = QWidget()
            row = QHBoxLayout(field)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(spin, 1)
            auto = QPushButton("Auto")
            auto.setMaximumWidth(58)
            auto.setToolTip("Use the selected material profile's saved exposure.")
            auto.clicked.connect(lambda checked=False, target=spin: target.setValue(0))
            row.addWidget(auto)
            self.auto_buttons.append(auto)
            self.exposure_fields.append(field)
            settings_form.addRow(label, field)
        self.supports = QCheckBox("Generate supports and raft")
        self.supports.toggled.connect(self.settings_changed)
        settings_form.addRow(self.supports)
        right.addLayout(settings_form)
        self.settings_summary = QLabel("Set exposure values to enable slicing.")
        self.settings_summary.setWordWrap(True)
        right.addWidget(self.settings_summary)
        self.advanced = QPushButton("Layer and motion settings")
        self.advanced.clicked.connect(self.edit_advanced)
        right.addWidget(self.advanced)
        right.addStretch()
        self.status = QLabel("Offline workspace • No account required")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.slice = QPushButton("Slice and validate")
        self.slice.clicked.connect(self.start_slice)
        actions.addWidget(self.slice)
        self.cancel = QPushButton("Cancel job")
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.cancel_slice)
        actions.addWidget(self.cancel)
        self.job_details = QPushButton("Open job details")
        self.job_details.setEnabled(False)
        self.job_details.clicked.connect(self.open_job_details)
        actions.addWidget(self.job_details)
        self.export = QPushButton("Export calibration candidate")
        self.export.setEnabled(False)
        self.export.clicked.connect(self.export_print)
        actions.addWidget(self.export)
        layout.addLayout(actions)
        self.build_workspace_controls(left, right, settings_form)
        self.setStyleSheet("""
            QWidget { background: #fafaf8; color: #102243; font-size: 15px; }
            QPushButton { background: #008d98; color: white; padding: 12px;
                          border-radius: 7px; }
            QPushButton:disabled { background: #dfe4e5; color: #526373; }
            QLabel { padding: 6px; }
        """)

    def open_generation(self):
        from seesaw.generation_ui import open_generation

        open_generation(self)

    def open_model(self):
        name, _ = QFileDialog.getOpenFileName(self, "Add model", "", "STL models (*.stl *.STL)")
        if name:
            self.import_model(Path(name))

    def import_model(self, path, project_file=False):
        if self.busy():
            return
        self.add.setEnabled(False)
        self.open_project_button.setEnabled(False)
        self.save_project_button.setEnabled(False)
        self.invalidate_result()
        self.status.setText("Reading model…")
        self.worker = ImportWorker(path, self, project_file)
        self.worker.loaded.connect(self.show_model)
        self.worker.failed.connect(self.import_failed)
        self.worker.finished.connect(self.import_finished)
        self.import_active = True
        self.worker.start()

    def open_project(self):
        name, _ = QFileDialog.getOpenFileName(
            self, "Open project", "", "Seesaw projects (*.seesaw)"
        )
        if name:
            self.import_model(Path(name), project_file=True)

    def save_current_project(self):
        if self.project is None:
            return
        self.transform_model()
        name, _ = QFileDialog.getSaveFileName(
            self,
            "Save project",
            str(self.project_path or "Untitled.seesaw"),
            "Seesaw projects (*.seesaw)",
        )
        if name:
            try:
                if Path(name).suffix.lower() != ".seesaw":
                    raise ValueError("Choose a .seesaw project filename.")
                save_project(self.project, Path(name))
                self.project_path = Path(name)
                self.status.setText(
                    "Project saved. Keep the referenced STL in its current location."
                )
            except (OSError, ValueError) as exc:
                self.status.setText(f"Could not save project: {exc}")

    def import_finished(self):
        self.import_active = False
        self.add.setEnabled(True)
        self.open_project_button.setEnabled(True)
        self.save_project_button.setEnabled(self.project is not None)

    def import_failed(self, message):
        self.status.setText(f"Could not import model: {message}")

    def busy(self):
        # A native thread may exit before its queued completion handlers run.
        return self.import_active or self.slice_active or self.export_active or self.updates.busy()

    def invalidate_result(self):
        if getattr(self, "syncing", False):
            return
        self.gate.invalidate()
        self.candidate = None
        self.layer_preview = None
        self.preview_generation += 1
        self.layer_slider.setEnabled(False)
        self.finding_box.blockSignals(True)
        self.finding_box.clear()
        self.finding_box.addItem("Full layer view", None)
        self.finding_box.blockSignals(False)
        self.layer_image.clear()
        self.layer_info.setText("Slice to inspect decoded PM4N layers.")
        self.export.setEnabled(False)

    def job_controls(self, enabled):
        for control in (
            self.updates,
            self.add,
            self.open_project_button,
            self.save_project_button,
            self.advanced,
            self.scale,
            *self.rotation,
            self.apply_transform,
            self.undo,
            self.exposure,
            self.bottom_exposure,
            *self.auto_buttons,
            self.supports,
            self.pixel_repair,
            self.hollow_button,
            self.slice,
            self.printer_box,
            self.material_box,
            self.save_material_button,
            self.import_material_button,
            self.test_button,
            self.instance_box,
            self.duplicate_button,
            self.remove_button,
            self.arrange_button,
            *self.position,
            *self.filament_controls.values(),
        ):
            control.setEnabled(enabled)
        self.cancel.setEnabled(not enabled)
        self.layer_slider.setEnabled(enabled and self.layer_preview is not None)
        self.save_project_button.setEnabled(enabled and self.project is not None)

    def start_slice(self):
        if self.project is None or self.busy():
            return
        self.transform_model()
        self.invalidate_result()
        if self.project.settings is None:
            self.status.setText(
                "Auto needs a matching material/layer profile, or enter both exposures manually."
            )
            return
        try:
            snapshot = self.gate.begin(self.project)
            root = Path(
                os.environ.get(
                    "SEESAW_JOB_ROOT",
                    str(
                        Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
                        / "progretech-seesaw"
                        / "jobs"
                    ),
                )
            ) / str(uuid.uuid4())
            self.job_directory = root
            self.job_details.setEnabled(True)
            self.slice_worker = SliceWorker(self.project, root, self)
            self.slice_worker.progress.connect(
                lambda stage: self.status.setText(f"Working: {stage}…")
            )
            self.slice_worker.succeeded.connect(
                lambda directory, manifest: self.slice_complete(snapshot, directory, manifest)
            )
            self.slice_worker.failed.connect(self.slice_failed)
            self.slice_worker.finished.connect(self.slice_finished)
            self.job_controls(False)
            self.slice_active = True
            self.slice_worker.start()
        except (OSError, ValueError) as exc:
            self.slice_failed(str(exc))

    def slice_finished(self):
        self.slice_active = False
        self.job_controls(True)

    def cancel_slice(self):
        for worker in (self.slice_worker, self.export_worker):
            if worker is not None and worker.isRunning():
                worker.cancel.set()
        if self.busy():
            self.invalidate_result()
            self.status.setText("Cancelling the current job…")

    def open_job_details(self):
        if self.job_directory is not None and self.job_directory.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.job_directory)))
        else:
            self.status.setText("This job stopped before creating its diagnostics folder.")

    def slice_failed(self, message):
        self.invalidate_result()
        if "UVTools reported issues" in message:
            from seesaw.issues import summarize_issues

            try:
                report = (self.job_directory / "pipeline/issues.log").read_text()[:65536]
                message = "Layer issues found. " + summarize_issues(report)
            except (OSError, ValueError, AttributeError):
                message = "Layer issues found. Inspect the Layers tab and Job details."
            if self.project and self.project.settings and not self.project.settings.supports:
                message += " Supports are off; enable Generate supports and raft, then slice again."
            else:
                message += " Review the highlighted layers and adjust supports or orientation."
        self.status.setText(f"No export available: {message}")
        if "Layer issues found" in message:
            self.inspect_failed_layers()

    def inspect_failed_layers(self):
        if self.job_directory is None:
            return
        directory = self.job_directory / "pipeline"
        try:
            manifest = json.loads((directory / "manifest.json").read_text())
            count = manifest["layer_count"]
            self.layer_preview = LayerPreview(directory / "readback.sl1", count)
            from seesaw.issues import parse_islands

            try:
                findings = parse_islands((directory / "issues.log").read_text(), count)
            except ValueError:
                findings = []  # Unknown types remain blockers; full layers can still be inspected.
            self.finding_box.blockSignals(True)
            for finding in findings:
                self.finding_box.addItem(
                    f"Layer {finding.layer + 1}: island, {finding.pixels} pixel(s)", finding
                )
            self.finding_box.blockSignals(False)
            self.layer_slider.setRange(0, count - 1)
            self.layer_slider.setValue(findings[0].layer if findings else 0)
            self.layer_slider.setEnabled(True)
            self.tabs.setCurrentIndex(1)
            self.request_layer()
        except (OSError, ValueError, KeyError, TypeError, BadZipFile):
            self.layer_preview = None
            self.layer_slider.setEnabled(False)
        self.export.setEnabled(False)

    def select_finding(self):
        if not isinstance(self.layer_preview, LayerPreview):
            return
        finding = self.finding_box.currentData()
        self.preview_generation += 1
        self.layer_preview = LayerPreview(
            self.layer_preview.archive, len(self.layer_preview.names), focus=finding
        )
        if finding is not None:
            self.layer_slider.setValue(finding.layer)
        self.request_layer()

    def slice_complete(self, snapshot, directory, manifest):
        if (
            not self.gate.finish(self.project, snapshot)
            or manifest.get("status") != "software_validated_not_print_qualified"
        ):
            self.slice_failed("Inputs changed or validation did not pass; slice again.")
            return
        self.candidate = (directory, manifest)
        try:
            self.layer_preview = (
                LayerPreview(directory / "readback.sl1", manifest["layer_count"])
                if self.project.printer_id == "mono4"
                else GCodePreview.from_validated_layers(
                    directory / "candidate.gcode", manifest["layer_z_mm"]
                )
            )
            self.layer_slider.blockSignals(True)
            self.layer_slider.setRange(0, manifest["layer_count"] - 1)
            self.layer_slider.setValue(0)
            self.layer_slider.blockSignals(False)
            self.layer_slider.setEnabled(True)
            self.tabs.setCurrentIndex(1)
            self.request_layer()
        except (OSError, ValueError, BadZipFile) as exc:
            self.slice_failed(str(exc))
            return
        self.status.setText(
            f"{manifest['layer_count']} layers validated. "
            "Review before exporting a calibration candidate."
            + (
                f" Removed {manifest['repair']['removed_pixels']} isolated pixel(s)."
                if manifest.get("repair", {}).get("removed_pixels")
                else ""
            )
        )

    def request_layer(self):
        if self.layer_preview is None:
            return
        if self.preview_worker is not None and self.preview_worker.isRunning():
            return  # The finish handler reads the latest slider position.
        generation = self.preview_generation
        index = self.layer_slider.value()
        self.preview_worker = PreviewWorker(self.layer_preview, index, self)
        self.preview_worker.loaded.connect(
            lambda number, data: self.show_layer(generation, number, data)
        )
        self.preview_worker.failed.connect(
            lambda message: (
                self.slice_failed(message) if generation == self.preview_generation else None
            )
        )
        self.preview_worker.finished.connect(lambda: self.next_layer(generation, index))
        self.preview_worker.start()

    def next_layer(self, generation, index):
        if self.layer_preview is not None and (
            generation != self.preview_generation or index != self.layer_slider.value()
        ):
            self.request_layer()

    def show_layer(self, generation, index, data):
        if generation != self.preview_generation or self.layer_preview is None:
            return
        picture = QPixmap()
        if not picture.loadFromData(data, "PNG"):
            self.slice_failed("Could not display the decoded layer.")
            return
        self.layer_image.setPixmap(picture.scaled(600, 400, Qt.AspectRatioMode.KeepAspectRatio))
        settings = self.project.settings
        if self.project.printer_id == "mono4":
            exposure = (
                settings.bottom_exposure_s
                if index < settings.bottom_layers
                else settings.exposure_s
            )
            text = (
                f"Z {(index + 1) * settings.layer_mm:.3f} mm • {exposure:g} s"
                " • Decoded printer pixels"
            )
        else:
            text = f"Z {self.layer_preview.z_values[index]:.3f} mm • Actual extruded G-code paths"
        self.layer_info.setText(f"Layer {index + 1}/{len(self.layer_preview.names)} • {text}")
        if self.candidate is None:
            self.layer_info.setText(self.layer_info.text() + " • Inspection only; export blocked")
        self.export.setEnabled(
            self.candidate is not None
            and self.gate.is_current(self.project)
            and not self.export_active
        )

    def export_print(self):
        if self.busy():
            return
        if self.candidate is None or not self.gate.is_current(self.project):
            self.slice_failed("Inputs changed; slice again.")
            return
        if (
            QMessageBox.warning(
                self,
                "Calibration candidate",
                "File validation passed. Printer firmware and material settings have not been "
                "physically qualified. Export for a supervised calibration test?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        extension = printer_by_id(self.project.printer_id).extension
        name, _ = QFileDialog.getSaveFileName(
            self, "Export printer file", "calibration" + extension, f"Printer files (*{extension})"
        )
        if name:
            try:
                directory, manifest = self.candidate
                self.export_worker = ExportWorker(
                    self.project,
                    directory / ("candidate" + extension),
                    name,
                    manifest["output_sha256"],
                    self,
                )
                self.export_worker.succeeded.connect(
                    lambda path: self.status.setText(
                        f"Calibration candidate exported and checksum verified: {path}"
                    )
                )
                self.export_worker.failed.connect(self.slice_failed)
                self.export_worker.finished.connect(self.export_finished)
                self.job_controls(False)
                self.export.setEnabled(False)
                self.status.setText("Copying and verifying calibration candidate…")
                self.export_active = True
                self.export_worker.start()
            except (OSError, ValueError) as exc:
                self.slice_failed(str(exc))

    def export_finished(self):
        self.export_active = False
        self.job_controls(True)
        self.export.setEnabled(self.candidate is not None and self.gate.is_current(self.project))

    def closeEvent(self, event):
        if self.export_worker is not None and self.export_worker.isRunning():
            self.cancel_slice()
            event.ignore()
            return
        if self.preview_worker is not None and self.preview_worker.isRunning():
            self.status.setText("Waiting for the current layer preview before closing.")
            event.ignore()
            return
        if self.slice_worker is not None and self.slice_worker.isRunning():
            self.cancel_slice()
            event.ignore()
            return
        if self.updates.busy():
            self.updates.worker.cancel.set()
            self.status.setText("Waiting for the update operation to finish before closing.")
            event.ignore()
            return
        if self.worker is not None and self.worker.isRunning():
            self.status.setText("Wait for model import to finish before closing.")
            event.ignore()
            return
        self.viewport.close()
        event.accept()


def main():
    # The embedded VTK window is qualified here through X11/XWayland only.
    # Respect an explicit platform override for further portability testing.
    if sys.platform == "linux" and os.environ.get("DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    app = QApplication(sys.argv)
    app.setApplicationName("progretech-seesaw")
    app.setDesktopFileName("progretech-seesaw")
    window = Window()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
