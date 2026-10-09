"""Desktop hollow/drain import, undo, persistence, layer/readback/export acceptance."""

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

import trimesh
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialogButtonBox,
    QDoubleSpinBox,
    QPushButton,
)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", required=True, type=Path)
parser.add_argument("--printer", choices=["mono4", "mk3s"], default="mono4")
args = parser.parse_args()
root = args.output.resolve()
root.mkdir(parents=True, exist_ok=False)
os.environ["SEESAW_JOB_ROOT"] = str(root / "jobs")
os.environ["XDG_CONFIG_HOME"] = str(root / "config")
from seesaw.app import Window  # noqa: E402
from seesaw.hollowing import DrainHole, Hollowing  # noqa: E402
from seesaw.project import load_project, save_project  # noqa: E402

app = QApplication([])
window = Window()
window.resize(1280, 800)
window.show()
window.printer_box.setCurrentIndex(window.printer_box.findData(args.printer))
source = root / "source.stl"
# Off-origin original STL proves source-coordinate anchoring.
mesh = trimesh.creation.box(extents=(10, 10, 10))
mesh.vertices += [7, -4, 13]
mesh.export(source)
window.import_model(source)
state = {"phase": "import", "passed": False, "printer": args.printer}
started = time.monotonic()


def fail(kind, value, traceback):
    state.update(error=str(value), passed=False)
    window.cancel_slice()
    app.quit()


sys.excepthook = fail


def tick_step():
    if time.monotonic() - started > 900:
        raise AssertionError("Desktop smoke exceeded bounded deadline")
    if state["phase"] == "import" and not window.busy() and window.project:
        window.rotation[2].setValue(90)
        window.position[0].setValue(-10)
        window.transform_model()
        window.duplicate_model()
        window.arrange_models()
        if args.printer == "mono4":
            window.exposure.setValue(2.5)
            window.bottom_exposure.setValue(25)
            window.project = window.project.edited(
                settings=replace(window.project.settings, layer_mm=0.2))
            hollow = Hollowing(True, 2, (DrainHole((7, -4, 8), (0, 0, 1), 1, 5),))
            def fill_dialog():
                dialog = QApplication.activeModalWidget()
                assert dialog is not None
                for key, value in {"x": 7, "y": -4, "z": 8, "dz": 1,
                                   "radius": 1, "depth": 5}.items():
                    dialog.findChild(QDoubleSpinBox, "hole." + key).setValue(value)
                dialog.findChild(QPushButton, "hole.add").click()
                dialog.findChild(QCheckBox, "hollow.enabled").setChecked(True)
                dialog.grab().save(str(root / "hollow-editor.png"))
                dialog.findChild(QDialogButtonBox, "hollow.buttons").button(
                    QDialogButtonBox.StandardButton.Ok).click()

            QTimer.singleShot(200, fill_dialog)
            window.edit_hollowing()
            assert window.project.hollowing == hollow
            window.undo_transform()
            assert not window.project.hollowing.enabled
            QTimer.singleShot(200, fill_dialog)
            window.edit_hollowing()
            assert window.project.hollowing == hollow
        window.sync_controls()
        save_project(window.project, root / "saved.seesaw")
        reopened = load_project(root / "saved.seesaw")
        assert reopened.fingerprint() == window.project.fingerprint()
        assert reopened.hollowing == window.project.hollowing
        window.grab().save(str(root / "workspace.png"))
        window.viewport.screenshot(str(root / "viewport.png"))
        window.import_model(root / "saved.seesaw", project_file=True)
        state["phase"] = "reopen"
    elif state["phase"] == "reopen" and not window.busy():
        assert window.project == load_project(root / "saved.seesaw")
        window.start_slice()
        state["phase"] = "slice"
    elif state["phase"] == "slice" and not window.busy():
        if window.candidate is None:
            raise AssertionError(window.status.text())
        assert window.export.isEnabled()
        assert window.gate.is_current(window.project)
        state.update(manifest=window.candidate[1] if isinstance(window.candidate, tuple) else None)
        window.request_layer()
        state["phase"] = "preview"
    elif state["phase"] == "preview" and not window.layer_image.pixmap().isNull():
        from seesaw.export import export_candidate
        directory, manifest = window.candidate
        candidate = directory / ("candidate.pm4n" if args.printer == "mono4" else "candidate.gcode")
        target = root / candidate.name
        export_candidate(candidate, target, manifest["output_sha256"],
                         extension=target.suffix)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == manifest["output_sha256"]
        window.grab().save(str(root / "layers.png"))
        window.project = window.project.edited(hollowing=Hollowing())
        window.invalidate_result()
        assert not window.export.isEnabled() and not window.gate.is_current(window.project)
        state.update(passed=True, phase="complete", manifest=manifest,
                     elapsed_s=time.monotonic() - started, hardware_qualified=False)
        app.quit()


def tick():
    if state.get("inside"):
        return
    state["inside"] = True
    try:
        tick_step()
    finally:
        state["inside"] = False


timer = QTimer()
timer.timeout.connect(tick)
timer.start(100)
app.exec()
(root / "result.json").write_text(json.dumps(state, indent=2))
window.close()
raise SystemExit(0 if state["passed"] else 1)
