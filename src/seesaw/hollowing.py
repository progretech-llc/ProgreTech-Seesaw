"""Typed source-STL drain coordinates and native PrusaSlicer metadata interchange."""

import math
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import trimesh


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class DrainHole:
    # Original STL coordinates, millimetres; direction points INTO the material.
    position_mm: tuple[float, float, float]
    direction: tuple[float, float, float]
    radius_mm: float = 1.0
    depth_mm: float = 5.0

    def __post_init__(self):
        for key in ("position_mm", "direction"):
            vector = getattr(self, key)
            if type(vector) is not tuple or len(vector) != 3 or not all(map(number, vector)):
                raise ValueError(f"{key} requires three finite numbers.")
            object.__setattr__(self, key, tuple(float(v) for v in vector))
        length = math.hypot(*self.direction)
        if not math.isfinite(length) or length < 1e-9:
            raise ValueError("Drain direction must be nonzero and point into the model.")
        object.__setattr__(self, "direction", tuple(v / length for v in self.direction))
        for key, low, high in (("radius_mm", 0.2, 20), ("depth_mm", 0.2, 200)):
            value = getattr(self, key)
            if not number(value) or not low <= value <= high:
                raise ValueError(f"{key} must be between {low} and {high} mm.")
            object.__setattr__(self, key, float(value))

    @classmethod
    def from_dict(cls, data):
        if type(data) is not dict or set(data) != {
            "position_mm",
            "direction",
            "radius_mm",
            "depth_mm",
        }:
            raise ValueError("Invalid drain-hole fields.")
        if any(type(data[k]) not in (list, tuple) for k in ("position_mm", "direction")):
            raise ValueError("Drain vectors must be arrays.")
        return cls(
            tuple(data["position_mm"]),
            tuple(data["direction"]),
            data["radius_mm"],
            data["depth_mm"],
        )


@dataclass(frozen=True)
class Hollowing:
    enabled: bool = False
    thickness_mm: float = 2.0
    holes: tuple[DrainHole, ...] = ()

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError("Hollow choice must be boolean.")
        if not number(self.thickness_mm) or not 1 <= self.thickness_mm <= 10:
            raise ValueError("Hollow wall thickness must be between 1 and 10 mm.")
        object.__setattr__(self, "thickness_mm", float(self.thickness_mm))
        if (
            type(self.holes) is not tuple
            or len(self.holes) > 16
            or any(type(h) is not DrainHole for h in self.holes)
        ):
            raise ValueError("At most 16 typed drain holes per source model.")
        if self.enabled and not self.holes:
            raise ValueError("Add an inward drain hole before enabling hollowing.")

    def to_dict(self):
        data = asdict(self)
        data["holes"] = list(data["holes"])
        return data

    @classmethod
    def from_dict(cls, data):
        if type(data) is not dict or set(data) != {"enabled", "thickness_mm", "holes"}:
            raise ValueError("Invalid hollowing fields.")
        if type(data["holes"]) is not list:
            raise ValueError("Drain holes must be an array.")
        return cls(
            data["enabled"],
            data["thickness_mm"],
            tuple(DrainHole.from_dict(h) for h in data["holes"]),
        )


def placed_holes(mesh, transform, holes):
    """Use the exact geometry placement affine transform, including rotated grounding."""
    from seesaw.scene import placed_mesh

    rotation = trimesh.transformations.euler_matrix(
        *np.radians(transform.rotation_deg), axes="sxyz"
    )[:3, :3]
    linear = rotation * transform.scale
    prepared = placed_mesh(mesh, transform)
    # Every source vertex follows the same affine map; derive its translation.
    offset = prepared.vertices[0] - linear @ mesh.vertices[0]
    return tuple(
        DrainHole(
            tuple(float(v) for v in linear @ np.array(h.position_mm) + offset),
            tuple(float(v) for v in rotation @ np.array(h.direction)),
            h.radius_mm * transform.scale,
            h.depth_mm * transform.scale,
        )
        for h in holes
    )


def native_drain_metadata(holes):
    lines = ["drain_holes_format_version=1"]
    records = []
    for hole in holes:
        # Native compatibility importer adds direction to position and removes 1mm
        # from height. Store the inverse, so typed positions are the surface origin.
        position = np.array(hole.position_mm) - np.array(hole.direction)
        values = (*position, *hole.direction, hole.radius_mm, hole.depth_mm + 1)
        records.append(" ".join(format(v, ".12g") for v in values))
    lines.append("object_id=1|" + " ".join(records))
    return "\n".join(lines) + "\n"


def add_native_drains(path: Path, holes, expected_z=0.0):
    """Add metadata only to our newly exported native single-object 3MF."""
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(path) as archive:
        xml = ET.fromstring(archive.read("3D/3dmodel.model"))
        ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
        objects = xml.findall("m:resources/m:object", ns)
        items = xml.findall("m:build/m:item", ns)
        if len(objects) != 1 or objects[0].get("id") != "1" or len(items) != 1:
            raise ValueError("Native drain interchange requires one merged mesh object.")
        transform = items[0].get("transform", "1 0 0 0 1 0 0 0 1 0 0 0")
        values = [float(v) for v in transform.split()]
        if len(values) != 12 or not np.allclose(
            values, [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, expected_z], atol=1e-7, rtol=0
        ):
            raise ValueError("Native exporter changed placement; drain coordinates rejected.")
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("Metadata/Slic3r_PE_sla_drain_holes.txt", native_drain_metadata(holes))
