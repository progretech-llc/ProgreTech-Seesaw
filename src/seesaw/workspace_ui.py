"""Printer/material selection and editable instances for the offline workspace."""

import uuid
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pyvista as pv
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from seesaw.fdm_settings import FDMSettings
from seesaw.hollowing import Hollowing
from seesaw.material_store import (
    catalog,
    read_material,
    resolve_exposures,
    save_material,
    settings_for,
)
from seesaw.pipeline import Settings
from seesaw.profiles import PRINTERS, MaterialProfile, materials_for_printer, printer_by_id
from seesaw.project import Transform
from seesaw.scene import check_placement, placed_mesh, scene_mesh
from seesaw.settings_ui import edit_settings


class WorkspaceControls:
    def show_help(self):
        QMessageBox.information(
            self,
            "Offline workflow",
            (
                "1. Add an STL or Load test file. STL coordinates are interpreted as "
                "millimetres.\n\n"
                "2. Select your exact printer and a matching material profile. Resin "
                "Auto exposure uses the profile's saved values at its layer height. "
                "Manufacturer references are starting values, not calibrated for your bottle. "
                "Type a positive number for manual exposure; Save profile for reuse. "
                "Filament starting temperatures come from the bundled Prusa PLA profile; "
                "check your spool.\n\n"
                "3. Select a model copy, move X/Y, rotate or scale, then Apply transform. "
                "Duplicate and "
                "Arrange place copies on the bed. Undo restores the previous geometry edit. "
                "Slice and Save project also apply the selected model's pending transform.\n\n"
                "4. Enable supports if appropriate, then Slice and validate. Inspect actual output "
                "layers before exporting to a mounted USB folder. The Model view shows input "
                "geometry; "
                "generated supports appear in Layers. Nothing starts a printer remotely.\n\n"
                "The bundled test is ctrlV's original Thingiverse 704409, CC Attribution–No "
                "Derivatives, "
                "unchanged. It is a demanding geometry test, not a resin exposure calibration. "
                "The author and original notice are included with the application.\n\n"
                "If a resin job finds islands, Layers can zoom to each finding while export "
                "stays blocked. Remove isolated single pixels is an optional, bounded UVTools "
                "repair; it cannot fix large unsupported regions. "
                "Slice again after changing it.\n\n"
                "Profiles and jobs stay local. Follow your material manufacturer's handling "
                "and cleanup "
                "instructions. Water-washable resin waste does not belong down a drain."
            ),
        )

    def build_workspace_controls(self, left, right, exposure_form):
        self.syncing = False
        self.active_instance = 0
        self.materials, profile_errors = catalog()
        self.printer_box = QComboBox()
        for printer in PRINTERS:
            self.printer_box.addItem(printer.name, printer.id)
        self.material_box = QComboBox()
        for combo in (self.printer_box, self.material_box):
            combo.setMinimumContentsLength(12)
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.printer_note = QLabel()
        self.printer_note.setWordWrap(True)
        right.insertWidget(0, QLabel("Print setup"))
        right.insertWidget(1, self.printer_box)
        right.insertWidget(2, self.printer_note)
        right.insertWidget(3, self.material_box)
        self.save_material_button = QPushButton("Save profile")
        self.save_material_button.clicked.connect(self.save_material_profile)
        self.import_material_button = QPushButton("Import profile…")
        self.import_material_button.clicked.connect(self.import_material_profile)
        profile_row = QHBoxLayout()
        for button in (self.save_material_button, self.import_material_button):
            button.setStyleSheet("padding: 6px; font-size: 12px")
            profile_row.addWidget(button)
        right.insertLayout(4, profile_row)
        self.exposure_form = exposure_form
        self.filament_panel = QWidget()
        form = QFormLayout(self.filament_panel)
        self.filament_controls = {}
        defaults = FDMSettings()
        for key, label, low, high, unit in (
            ("layer_mm", "Layer height", 0.05, 0.3, " mm"),
            ("nozzle_c", "Nozzle", 170, 280, " °C"),
            ("bed_c", "Bed", 0, 110, " °C"),
            ("infill_percent", "Infill", 0, 100, " %"),
            ("speed_mm_s", "Print speed", 5, 100, " mm/s"),
        ):
            spin = QDoubleSpinBox()
            spin.setDecimals(3 if key == "layer_mm" else 1)
            spin.setRange(low, high)
            spin.setSuffix(unit)
            spin.setValue(getattr(defaults, key))
            spin.valueChanged.connect(self.settings_changed)
            self.filament_controls[key] = spin
            form.addRow(label, spin)
        right.insertWidget(5, self.filament_panel)
        self.hollow_button = QPushButton("Hollow and drain…")
        self.hollow_button.clicked.connect(self.edit_hollowing)
        right.insertWidget(6, self.hollow_button)
        self.pixel_repair = QCheckBox("Remove isolated single pixels")
        self.pixel_repair.setToolTip(
            "Opt-in UVTools repair: removes at most 64 reported one-pixel islands. "
            "Every changed pixel is checked; larger islands still block export."
        )
        self.pixel_repair.toggled.connect(self.repair_changed)
        right.insertWidget(6, self.pixel_repair)
        self.test_button = QPushButton("Load test file")
        self.test_button.clicked.connect(self.load_test_file)
        left.insertWidget(2, self.test_button)
        self.instance_box = QComboBox()
        self.instance_box.currentIndexChanged.connect(self.select_instance)
        left.insertWidget(3, self.instance_box)
        self.duplicate_button = QPushButton("Duplicate")
        self.duplicate_button.clicked.connect(self.duplicate_model)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_instance)
        self.arrange_button = QPushButton("Arrange")
        self.arrange_button.clicked.connect(self.arrange_models)
        copies_row = QHBoxLayout()
        for button in (self.duplicate_button, self.remove_button, self.arrange_button):
            button.setStyleSheet("padding: 6px; font-size: 12px")
            copies_row.addWidget(button)
        left.insertLayout(4, copies_row)
        position = QFormLayout()
        self.position = []
        for axis in "XY":
            spin = QDoubleSpinBox()
            spin.setRange(-500, 500)
            spin.setSuffix(" mm")
            spin.valueChanged.connect(self.invalidate_result)
            position.addRow(f"Move {axis}", spin)
            self.position.append(spin)
        left.insertLayout(6, position)
        self.printer_box.currentIndexChanged.connect(self.printer_changed)
        self.material_box.currentIndexChanged.connect(self.material_changed)
        self.refresh_materials()
        self.update_technology()
        if profile_errors:
            self.status.setText("Some local profiles were rejected: " + "; ".join(profile_errors))

    def repair_changed(self):
        if self.project is None or self.syncing:
            return
        self.project = self.project.edited(repair_single_pixels=self.pixel_repair.isChecked())
        self.invalidate_result()

    def selected_printer(self):
        return printer_by_id(self.printer_box.currentData())

    def refresh_materials(self, selected=None):
        self.material_box.blockSignals(True)
        self.material_box.clear()
        available = materials_for_printer(self.materials, self.printer_box.currentData())
        if selected is not None and all(p.to_dict() != selected.to_dict() for p in available):
            available.append(selected)
        for material in available:
            self.material_box.addItem(material.name, material)
            self.material_box.setItemData(
                self.material_box.count() - 1,
                material.name + "\n" + material.provenance,
                Qt.ItemDataRole.ToolTipRole,
            )
        if selected:
            for index, profile in enumerate(available):
                if profile.to_dict() == selected.to_dict():
                    self.material_box.setCurrentIndex(index)
                    break
        self.material_box.blockSignals(False)

    def update_technology(self):
        printer = self.selected_printer()
        resin = printer.technology == "resin"
        for field in self.exposure_fields:
            field.setVisible(resin)
            self.exposure_form.labelForField(field).setVisible(resin)
        self.filament_panel.setVisible(not resin)
        self.advanced.setVisible(resin)
        self.supports.setText("Generate supports and raft" if resin else "Generate supports")
        self.printer_note.setText(
            f"{'Resin / MSLA' if resin else 'Filament / FDM'} • "
            + " × ".join(f"{v:g}" for v in printer.build_mm)
            + f" mm\nOutput: {printer.extension} • Profile v{printer.revision}"
        )
        from seesaw import __version__

        self.pixel_repair.setVisible(printer.technology == "resin")
        self.hollow_button.setVisible(resin)
        self.smart_slice.setVisible(resin)
        self.test_button.setText("Load resin test" if resin else "Load FDM test")
        self.setWindowTitle(f"ProgreTech Seesaw {__version__} — {printer.name}")

    def printer_changed(self):
        if self.syncing:
            return
        self.refresh_materials()
        self.update_technology()
        self.material_changed()
        if self.project is not None:
            self.render_model()

    def material_changed(self):
        if self.syncing:
            return
        material = self.material_box.currentData()
        if material is None:
            return
        if self.project is not None:
            self.project = self.project.edited(
                hollowing=(
                    self.project.hollowing if material.technology == "resin" else Hollowing()
                ),
                printer_id=material.printer_id,
                printer_revision=self.selected_printer().revision,
                material=material,
                settings=settings_for(material),
                auto_exposure=(material.technology == "resin",) * 2,
                repair_single_pixels=(
                    self.project.repair_single_pixels if material.technology == "resin" else False
                ),
            )
            self.invalidate_result()
            self.sync_controls()

    def load_test_file(self):
        if self.selected_printer().technology == "resin":
            self.import_model(Path(__file__).parent / "assets/resin-test/geometry.stl")
            self.status.setText(
                "Loading small solid resin geometry test; not an exposure calibration matrix."
            )
        else:
            self.import_model(Path(__file__).parent / "assets/test-model/ctrlV_3D_test.stl")
            self.status.setText(
                "Loading original FDM test by ctrlV • "
                "CC Attribution–No Derivatives • Demanding geometry test."
            )

    def show_model(self, mesh, info):
        self.project = self.worker.project
        self.project_path = self.worker.path if self.worker.project_file else None
        self.mesh, self.inspection = mesh, info
        self.active_instance = 0
        self.undo_stack.clear()
        if not self.worker.project_file:
            material = self.material_box.currentData()
            self.project = self.project.edited(
                printer_id=material.printer_id,
                material=material,
                settings=settings_for(material),
                auto_exposure=(material.technology == "resin",) * 2,
            )
        self.sync_controls()
        self.render_model()

    def sync_controls(self):
        self.syncing = True
        try:
            self.printer_box.setCurrentIndex(self.printer_box.findData(self.project.printer_id))
            self.refresh_materials(self.project.material)
            transforms = (self.project.transform, *self.project.copies)
            self.active_instance = min(self.active_instance, len(transforms) - 1)
            self.instance_box.clear()
            self.instance_box.addItems([f"Model {i + 1}" for i in range(len(transforms))])
            self.instance_box.setCurrentIndex(self.active_instance)
            transform = transforms[self.active_instance]
            for spin, value in zip(self.rotation, transform.rotation_deg):
                spin.setRange(min(-360, value), max(360, value))
                spin.setValue(value)
            for spin, value in zip(self.position, transform.translation_mm[:2]):
                spin.setRange(min(-500, value), max(500, value))
                spin.setValue(value)
            self.scale.setRange(min(0.01, transform.scale), max(100, transform.scale))
            self.scale.setValue(transform.scale)
            settings = self.project.settings
            if self.selected_printer().technology == "resin":
                partial = dict(self.project.material.parameters) if self.project.material else {}
                self.settings_template = settings or Settings(
                    **({"exposure_s": 1, "bottom_exposure_s": 1} | partial)
                )
                self.exposure.setValue(
                    0 if self.project.auto_exposure[0] or settings is None else settings.exposure_s
                )
                self.bottom_exposure.setValue(
                    0
                    if self.project.auto_exposure[1] or settings is None
                    else settings.bottom_exposure_s
                )
            else:
                for key, spin in self.filament_controls.items():
                    spin.setValue(getattr(settings or FDMSettings(), key))
            self.supports.setChecked(settings.supports if settings else False)
            self.pixel_repair.setChecked(self.project.repair_single_pixels)
            self.update_technology()
            self.update_settings_summary()
        finally:
            self.syncing = False

    def settings_changed(self):
        if self.project is None or self.syncing:
            return
        settings = None
        if self.selected_printer().technology == "fdm":
            settings = FDMSettings(
                **{k: v.value() for k, v in self.filament_controls.items()},
                supports=self.supports.isChecked(),
            )
        else:
            template = replace(
                self.project.settings or self.settings_template, supports=self.supports.isChecked()
            )
            settings = resolve_exposures(
                self.project.material,
                template,
                self.exposure.value(),
                self.bottom_exposure.value(),
                (self.exposure.value() == 0, self.bottom_exposure.value() == 0),
            )
            self.settings_template = settings or template
        self.project = self.project.edited(
            settings=settings,
            auto_exposure=(
                (self.exposure.value() == 0, self.bottom_exposure.value() == 0)
                if self.selected_printer().technology == "resin"
                else (False, False)
            ),
        )
        self.invalidate_result()
        self.update_settings_summary()

    def update_settings_summary(self):
        settings = self.project.settings if self.project else None
        if settings is None:
            text = (
                "Auto unavailable: select a material with saved exposures at this layer height, "
                "or enter manual values. Save your calibrated values as a local material."
            )
        elif isinstance(settings, FDMSettings):
            text = (
                f"{settings.layer_mm:g} mm • {settings.nozzle_c:g} °C nozzle"
                f" / {settings.bed_c:g} °C bed\n"
            )
            text += f"{settings.infill_percent:g}% infill • {settings.speed_mm_s:g} mm/s"
        else:
            text = (
                f"Normal {settings.exposure_s:g} s"
                f"{' (Auto)' if self.project.auto_exposure[0] else ''} • "
                f"Bottom {settings.bottom_exposure_s:g} s"
                f"{' (Auto)' if self.project.auto_exposure[1] else ''}\n"
            )
            text += f"{settings.layer_mm:g} mm • {settings.bottom_layers} bottom layers\n"
            text += f"Lift {settings.lift_mm:g} mm at {settings.lift_mm_min:g} mm/min\n"
            text += f"Retract {settings.retract_mm_min:g} mm/min • Rest {settings.rest_s:g} s"
        self.settings_summary.setText(text + "\nVerify settings for your material and printer.")

    def edit_hollowing(self):
        if self.project is None or self.busy():
            self.status.setText("Import a model before editing hollowing and drains.")
            return
        from seesaw.hollowing_ui import edit_hollowing

        self.transform_model()
        result = edit_hollowing(self, self.project.hollowing)
        if result is not None and result != self.project.hollowing:
            self.undo_stack.append(self.project)
            self.project = self.project.edited(hollowing=result)
            self.invalidate_result()
            self.sync_controls()
            self.render_model()

    def edit_advanced(self):
        if self.project is None or self.project.settings is None or self.busy():
            self.status.setText("Import a model and enter both exposure values first.")
            return
        settings = edit_settings(self, self.project.settings)
        if settings is not None:
            resolved = resolve_exposures(
                self.project.material,
                settings,
                settings.exposure_s,
                settings.bottom_exposure_s,
                self.project.auto_exposure,
            )
            if resolved is None:
                self.status.setText(
                    "Auto has no exposure for that layer height. "
                    "Enter manual exposures before changing it."
                )
                return
            self.settings_template = settings
            self.project = self.project.edited(settings=resolved)
            self.invalidate_result()
            self.sync_controls()

    def select_instance(self, index):
        if not self.syncing and self.project is not None and index >= 0:
            self.transform_model()
            self.active_instance = index
            self.sync_controls()
            self.render_model()

    def transform_model(self):
        if self.project is None or self.busy():
            return
        try:
            transform = Transform(
                tuple(v.value() for v in self.position) + (0.0,),
                tuple(v.value() for v in self.rotation),
                self.scale.value(),
            )
            placed_mesh(self.mesh, transform)
            transforms = [self.project.transform, *self.project.copies]
            if transforms[self.active_instance] == transform:
                return
            self.undo_stack.append(self.project)
            transforms[self.active_instance] = transform
            self.project = self.project.edited(
                transform=transforms[0], copies=tuple(transforms[1:])
            )
            self.invalidate_result()
            self.render_model()
        except ValueError as exc:
            self.status.setText(str(exc))

    def duplicate_model(self):
        if self.project is None or self.busy():
            return
        self.transform_model()
        if len(self.project.copies) >= 31:
            self.status.setText("At most 32 copies per project.")
            return
        transform = (self.project.transform, *self.project.copies)[self.active_instance]
        width = float(placed_mesh(self.mesh, transform).extents[0])
        x, y, z = transform.translation_mm
        self.undo_stack.append(self.project)
        self.project = self.project.edited(
            copies=(*self.project.copies, replace(transform, translation_mm=(x + width + 5, y, z)))
        )
        self.active_instance = len(self.project.copies)
        self.invalidate_result()
        self.sync_controls()
        self.render_model()

    def remove_instance(self):
        if self.project is None or not self.project.copies or self.busy():
            return
        transforms = [self.project.transform, *self.project.copies]
        del transforms[self.active_instance]
        self.undo_stack.append(self.project)
        self.project = self.project.edited(transform=transforms[0], copies=tuple(transforms[1:]))
        self.active_instance = 0
        self.invalidate_result()
        self.sync_controls()
        self.render_model()

    def arrange_models(self):
        if self.project is None or self.busy():
            return
        self.transform_model()
        transforms = [self.project.transform, *self.project.copies]
        sizes = [placed_mesh(self.mesh, t).extents for t in transforms]
        cell = np.max(sizes, axis=0)[:2] + 5
        bed = np.asarray(self.selected_printer().build_mm[:2])
        columns = int((bed[0] + 5) // cell[0])
        rows = int((bed[1] + 5) // cell[1])
        if columns < 1 or rows < 1 or columns * rows < len(transforms):
            self.status.setText("Copies do not fit this grid arrangement; resize or remove a copy.")
            return
        columns = min(columns, len(transforms))
        rows = (len(transforms) + columns - 1) // columns
        arranged = [
            replace(
                t,
                translation_mm=(
                    float((i % columns - (columns - 1) / 2) * cell[0]),
                    float((i // columns - (rows - 1) / 2) * cell[1]),
                    0,
                ),
            )
            for i, t in enumerate(transforms)
        ]
        self.undo_stack.append(self.project)
        self.project = self.project.edited(transform=arranged[0], copies=tuple(arranged[1:]))
        self.invalidate_result()
        self.sync_controls()
        self.render_model()

    def undo_transform(self):
        if not self.undo_stack or self.busy():
            return
        old = self.undo_stack.pop()
        self.project = self.project.edited(
            transform=old.transform, copies=old.copies, hollowing=old.hollowing,
            settings=old.settings, repair_single_pixels=old.repair_single_pixels,
            auto_exposure=old.auto_exposure
        )
        self.invalidate_result()
        self.sync_controls()
        self.render_model()

    def focus_model(self):
        if self.project is not None and self.mesh is not None:
            bounds = scene_mesh(self.mesh, self.project).bounds.T.ravel()
            self.viewport.reset_camera(bounds=bounds)

    def toggle_edges(self, enabled):
        for actor in getattr(self, "model_actors", []):
            actor.prop.show_edges = enabled
        self.viewport.render()

    def render_model(self):
        printer = self.selected_printer()
        self.viewport.clear()
        self.viewport.enable_lightkit()
        self.model_actors = []
        for index, transform in enumerate((self.project.transform, *self.project.copies)):
            mesh = placed_mesh(self.mesh, transform)
            faces = np.column_stack((np.full(len(mesh.faces), 3), mesh.faces)).ravel()
            surface = pv.PolyData(mesh.vertices, faces).compute_normals(
                cell_normals=True,
                point_normals=True,
                consistent_normals=True,
                auto_orient_normals=True,
            )
            actor = self.viewport.add_mesh(
                surface,
                color="#32aab3" if index == self.active_instance else "#91bcc0",
                lighting=True,
                ambient=0.12,
                diffuse=0.8,
                specular=0.25,
                specular_power=30,
                smooth_shading=True,
                split_sharp_edges=True,
                show_edges=self.edge_toggle.isChecked(),
                edge_color="#25464c",
            )
            self.model_actors.append(actor)
            if self.project.hollowing.enabled:
                from seesaw.hollowing import placed_holes

                for hole in placed_holes(self.mesh, transform, self.project.hollowing.holes):
                    start = np.asarray(hole.position_mm)
                    end = start + np.asarray(hole.direction) * hole.depth_mm
                    self.viewport.add_mesh(pv.Line(start, end), color="#ed8a23", line_width=5)
                    self.viewport.add_mesh(
                        pv.Sphere(radius=hole.radius_mm, center=start), color="#ed8a23", opacity=0.5
                    )

        self.viewport.add_mesh(
            pv.Plane(center=(0, 0, -0.1), i_size=printer.build_mm[0], j_size=printer.build_mm[1]),
            color="#99a6ad",
            style="wireframe",
        )
        self.viewport.view_isometric()
        self.viewport.reset_camera()
        all_mesh = scene_mesh(self.mesh, self.project)
        dimensions = " × ".join(f"{value:.2f}" for value in all_mesh.extents)
        self.info.setText(
            f"{self.inspection.name}\n{dimensions} mm total\n"
            f"{len(self.project.copies) + 1} model(s) • Closed: {self.inspection.watertight}"
        )
        if self.project.model_path.name == "ctrlV_3D_test.stl":
            self.info.setText(self.info.text() + "\nTest by ctrlV • CC BY-ND")
            self.info.setToolTip(
                (Path(__file__).parent / "assets/test-model/ATTRIBUTION.txt").read_text()
            )
        else:
            self.info.setToolTip("")
        try:
            check_placement(all_mesh, printer.build_mm)
            text = "Placed within build volume; generated supports not included."
        except ValueError as exc:
            text = str(exc)
        self.status.setText(text)

    def save_material_profile(self):
        if self.project is None or self.project.settings is None or self.busy():
            self.status.setText("Enter valid material settings before saving a profile.")
            return
        name, ok = QInputDialog.getText(
            self, "Save local material", "Product / colour / calibration name"
        )
        if not ok or not name.strip():
            return
        try:
            profile = MaterialProfile(
                "user-" + uuid.uuid4().hex,
                name.strip(),
                self.selected_printer().technology,
                self.project.printer_id,
                asdict(self.project.settings),
            )
            save_material(profile)
            self.materials.append(profile)
            self.project = self.project.edited(material=profile)
            self.refresh_materials(profile)
            self.invalidate_result()
            self.status.setText(
                "Material profile saved locally with explicit units and printer association."
            )
        except (OSError, ValueError) as exc:
            self.status.setText(f"Profile not saved: {exc}")

    def import_material_profile(self):
        name, _ = QFileDialog.getOpenFileName(
            self, "Import material profile", "", "Material JSON (*.json)"
        )
        if not name:
            return
        try:
            profile = read_material(name)
            description = f"{profile.name}\nPrinter: {printer_by_id(profile.printer_id).name}\n"
            description += "\n".join(f"{key}: {value}" for key, value in profile.parameters.items())
            if (
                QMessageBox.question(self, "Review profile import", description)
                != QMessageBox.StandardButton.Yes
            ):
                return
            profile = replace(profile, id="user-" + uuid.uuid4().hex)
            save_material(profile)
            self.materials.append(profile)
            self.refresh_materials()
            self.status.setText(
                "Profile imported locally. Select its associated printer to use it."
            )
        except (OSError, ValueError, TypeError) as exc:
            self.status.setText(f"Profile import rejected: {exc}")
