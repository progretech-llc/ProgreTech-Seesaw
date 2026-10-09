"""Native hollow/drain editing; positions stay attached to original STL coordinates."""

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from seesaw.hollowing import DrainHole, Hollowing


def edit_hollowing(parent, current):
    dialog = QDialog(parent)
    dialog.setWindowTitle("Hollow and drain")
    layout = QVBoxLayout(dialog)
    note = QLabel(
        "Hole positions use original STL coordinates (mm). Direction points INTO "
        "the model. Holes follow rotation, scale and every copy. Inspect sliced "
        "layers and cavity findings before export; model view shows hole guides."
    )
    note.setWordWrap(True)
    layout.addWidget(note)
    enabled = QCheckBox("Hollow using native slicer")
    enabled.setObjectName("hollow.enabled")
    enabled.setChecked(current.enabled)
    layout.addWidget(enabled)
    form = QFormLayout()
    thickness = QDoubleSpinBox()
    thickness.setObjectName("hollow.thickness")
    thickness.setRange(1, 10)
    thickness.setValue(current.thickness_mm)
    thickness.setSuffix(" mm")
    form.addRow("Wall thickness", thickness)
    controls = {}
    for key, label, low, high, initial in (
        ("x", "Surface X", -10000, 10000, 0),
        ("y", "Surface Y", -10000, 10000, 0),
        ("z", "Surface Z", -10000, 10000, 0),
        ("dx", "Inward direction X", -1, 1, 0),
        ("dy", "Inward direction Y", -1, 1, 0),
        ("dz", "Inward direction Z", -1, 1, 1),
        ("radius", "Radius", 0.2, 20, 1),
        ("depth", "Depth into model", 0.2, 200, 5),
    ):
        spin = QDoubleSpinBox()
        spin.setObjectName("hole." + key)
        spin.setDecimals(3)
        spin.setRange(low, high)
        spin.setValue(initial)
        if key not in ("dx", "dy", "dz"):
            spin.setSuffix(" mm")
        form.addRow(label, spin)
        controls[key] = spin
    layout.addLayout(form)
    holes = list(current.holes)
    listing = QListWidget()
    layout.addWidget(listing)

    def refresh():
        listing.clear()
        for hole in holes:
            listing.addItem(
                f"{hole.position_mm} • inward {hole.direction} • "
                f"r {hole.radius_mm:g}, depth {hole.depth_mm:g} mm"
            )

    def add():
        try:
            if len(holes) >= 16:
                raise ValueError("At most 16 holes per source model.")
            holes.append(
                DrainHole(
                    tuple(controls[k].value() for k in ("x", "y", "z")),
                    tuple(controls[k].value() for k in ("dx", "dy", "dz")),
                    controls["radius"].value(),
                    controls["depth"].value(),
                )
            )
            refresh()
        except ValueError as exc:
            QMessageBox.warning(dialog, "Check drain hole", str(exc))

    def remove():
        index = listing.currentRow()
        if index >= 0:
            del holes[index]
            refresh()

    row = QHBoxLayout()
    for text, callback in (("Add hole", add), ("Remove selected", remove)):
        button = QPushButton(text)
        button.setObjectName("hole.add" if text == "Add hole" else "hole.remove")
        button.clicked.connect(callback)
        row.addWidget(button)
    layout.addLayout(row)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.setObjectName("hollow.buttons")
    result = None

    def accept():
        nonlocal result
        try:
            result = Hollowing(enabled.isChecked(), thickness.value(), tuple(holes))
            dialog.accept()
        except ValueError as exc:
            QMessageBox.warning(dialog, "Check hollowing", str(exc))

    buttons.accepted.connect(accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    refresh()
    return result if dialog.exec() == QDialog.DialogCode.Accepted else None
