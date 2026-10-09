"""Immutable local project inputs; saved projects never restore export readiness."""

import hashlib
import json
import math
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from seesaw.fdm_settings import FDMSettings
from seesaw.hollowing import Hollowing
from seesaw.pipeline import PipelineError, Settings
from seesaw.profiles import MaterialProfile, printer_by_id

MAX_MODEL_BYTES = 256 * 1024 * 1024
MAX_PROJECT_BYTES = 64 * 1024


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def exact_keys(value, names):
    names = tuple(names)
    if type(value) is not dict or set(value) != set(names):
        raise ValueError(f"Expected exactly these fields: {', '.join(sorted(names))}")


def checked_settings(settings):
    if settings is None:
        return
    if type(settings) is FDMSettings:
        settings.validate()
        return
    if type(settings) is not Settings:
        raise ValueError("Settings must be a Settings record or None.")
    for name, value in asdict(settings).items():
        if name in ("supports", "antialias"):
            valid = type(value) is bool
        elif name == "bottom_layers":
            valid = type(value) is int
        else:
            valid = finite(value)
        if not valid:
            raise ValueError(f"Invalid type or non-finite value for {name}.")
    try:
        settings.validate()
    except PipelineError as exc:
        raise ValueError(str(exc)) from exc


def mesh_hash(path):
    if not path.is_absolute() or path.suffix.lower() != ".stl":
        raise ValueError("Model reference must be an absolute STL path.")
    if not path.is_file():
        raise ValueError(f"Source STL is missing or not a regular file: {path}")
    digest = hashlib.sha256()
    consumed = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            consumed += len(chunk)
            if consumed > MAX_MODEL_BYTES:
                raise ValueError("Source STL exceeds 256 MiB.")
            digest.update(chunk)
    if not consumed:
        raise ValueError("Source STL is empty.")
    return digest.hexdigest()


@dataclass(frozen=True)
class Transform:
    translation_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: float = 1.0

    def __post_init__(self):
        for name in ("translation_mm", "rotation_deg"):
            value = getattr(self, name)
            if type(value) is not tuple or len(value) != 3 or not all(map(finite, value)):
                raise ValueError(f"{name} must contain three finite numbers.")
            object.__setattr__(self, name, tuple(float(v) for v in value))
        if not finite(self.scale) or self.scale <= 0:
            raise ValueError("Scale must be a positive finite number.")
        object.__setattr__(self, "scale", float(self.scale))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        exact_keys(data, ("translation_mm", "rotation_deg", "scale"))
        for name in ("translation_mm", "rotation_deg"):
            if type(data[name]) not in (list, tuple):
                raise ValueError(f"{name} must be an array.")
        return cls(tuple(data["translation_mm"]), tuple(data["rotation_deg"]), data["scale"])


@dataclass(frozen=True)
class Project:
    model_path: Path
    model_sha256: str
    transform: Transform = Transform()
    settings: Settings | FDMSettings | None = None
    revision: int = 1
    printer_id: str = "mono4"
    copies: tuple[Transform, ...] = ()
    material: MaterialProfile | None = None
    printer_revision: int = 1
    repair_single_pixels: bool = False
    hollowing: Hollowing = Hollowing()
    auto_exposure: tuple[bool, bool] = (False, False)

    def __post_init__(self):
        if not isinstance(self.model_path, Path) or not self.model_path.is_absolute():
            raise ValueError("Model path must be absolute.")
        if self.model_path.suffix.lower() != ".stl":
            raise ValueError("Model reference must be STL.")
        if type(self.model_sha256) is not str or not re.fullmatch(
            "[0-9a-f]{64}", self.model_sha256
        ):
            raise ValueError("Invalid source SHA-256.")
        if type(self.transform) is not Transform:
            raise ValueError("Invalid transform.")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("Revision must be a positive integer.")
        printer = printer_by_id(self.printer_id)
        if self.printer_revision != printer.revision or type(self.printer_revision) is not int:
            raise ValueError("Unsupported printer profile revision.")
        if (
            type(self.copies) is not tuple
            or len(self.copies) > 31
            or any(type(value) is not Transform for value in self.copies)
        ):
            raise ValueError("A project supports at most 32 model instances.")
        if self.settings is not None and type(self.settings) is not (
            Settings if printer.technology == "resin" else FDMSettings
        ):
            raise ValueError("Settings do not match the selected printer technology.")
        if self.material is not None and (
            type(self.material) is not MaterialProfile
            or self.material.printer_id != self.printer_id
        ):
            raise ValueError("Material profile does not match the selected printer.")
        if type(self.repair_single_pixels) is not bool:
            raise ValueError("Repair choice must be boolean.")
        if self.repair_single_pixels and printer.technology != "resin":
            raise ValueError("Pixel repair is only available for resin printers.")
        if type(self.hollowing) is not Hollowing:
            raise ValueError("Invalid hollowing record.")
        if self.hollowing.enabled and printer.technology != "resin":
            raise ValueError("Hollowing is available only for resin printers.")
        if (
            type(self.auto_exposure) is not tuple
            or len(self.auto_exposure) != 2
            or any(type(v) is not bool for v in self.auto_exposure)
        ):
            raise ValueError("Auto exposure must contain two boolean choices.")
        if any(self.auto_exposure) and printer.technology != "resin":
            raise ValueError("Auto exposure is only available for resin printers.")
        checked_settings(self.settings)
        if self.settings is not None and any(self.auto_exposure):
            from seesaw.material_store import resolve_exposures

            resolved = resolve_exposures(
                self.material,
                self.settings,
                self.settings.exposure_s,
                self.settings.bottom_exposure_s,
                self.auto_exposure,
            )
            if resolved is None or resolved != self.settings:
                raise ValueError("Auto exposure does not match the material/layer profile.")

    def edited(self, **changes):
        if "revision" in changes:
            raise ValueError("Revision is managed by the project.")
        return replace(self, revision=self.revision + 1, **changes)

    def verify_source(self):
        if mesh_hash(self.model_path) != self.model_sha256:
            raise ValueError("Source STL changed; reimport it before slicing.")

    def to_dict(self):
        return {
            "schema": "version5",
            "model_path": str(self.model_path),
            "model_sha256": self.model_sha256,
            "transform": self.transform.to_dict(),
            "settings": asdict(self.settings) if self.settings is not None else None,
            "revision": self.revision,
            "printer_id": self.printer_id,
            "copies": [value.to_dict() for value in self.copies],
            "material": self.material.to_dict() if self.material else None,
            "printer_revision": self.printer_revision,
            "repair_single_pixels": self.repair_single_pixels,
            "hollowing": self.hollowing.to_dict(),
            "auto_exposure": list(self.auto_exposure),
        }

    def fingerprint(self):
        data = self.to_dict()
        del data["model_path"], data["revision"]
        # Normalize equivalent integer/float settings so save/reopen is stable.
        if data["settings"] is not None:
            for name, value in data["settings"].items():
                if name not in ("supports", "antialias", "bottom_layers"):
                    data["settings"][name] = float(value)
        return hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()

    @classmethod
    def from_dict(cls, data):
        if type(data) is dict and data.get("schema") == "version1":
            exact_keys(
                data,
                (
                    "schema",
                    "model_path",
                    "model_sha256",
                    "transform",
                    "settings",
                    "revision",
                    "printer_id",
                ),
            )
            data = dict(data, schema="version2", copies=[], material=None, printer_revision=1)
        if type(data) is dict and data.get("schema") == "version2":
            exact_keys(
                data,
                (
                    "schema",
                    "model_path",
                    "model_sha256",
                    "transform",
                    "settings",
                    "revision",
                    "printer_id",
                    "copies",
                    "material",
                    "printer_revision",
                ),
            )
            data = dict(data, schema="version3", repair_single_pixels=False)
        if type(data) is dict and data.get("schema") == "version3":
            exact_keys(
                data,
                (
                    "schema",
                    "model_path",
                    "model_sha256",
                    "transform",
                    "settings",
                    "revision",
                    "printer_id",
                    "copies",
                    "material",
                    "printer_revision",
                    "repair_single_pixels",
                ),
            )
            data = dict(data, schema="version4", hollowing=Hollowing().to_dict())
        if type(data) is dict and data.get("schema") == "version4":
            exact_keys(
                data,
                (
                    "schema",
                    "model_path",
                    "model_sha256",
                    "transform",
                    "settings",
                    "revision",
                    "printer_id",
                    "copies",
                    "material",
                    "printer_revision",
                    "repair_single_pixels",
                    "hollowing",
                ),
            )
            data = dict(data, schema="version5", auto_exposure=[False, False])
        exact_keys(
            data,
            (
                "schema",
                "model_path",
                "model_sha256",
                "transform",
                "settings",
                "revision",
                "printer_id",
                "copies",
                "material",
                "printer_revision",
                "repair_single_pixels",
                "hollowing",
                "auto_exposure",
            ),
        )
        if data["schema"] != "version5":
            raise ValueError("Unsupported project schema.")
        if type(data["model_path"]) is not str:
            raise ValueError("Model path must be a string.")
        settings = data["settings"]
        if settings is not None:
            kind = (
                Settings if printer_by_id(data["printer_id"]).technology == "resin" else FDMSettings
            )
            exact_keys(settings, (f.name for f in fields(kind)))
            settings = kind(**settings)
        if type(data["copies"]) is not list:
            raise ValueError("Copies must be a list.")
        if type(data["auto_exposure"]) is not list:
            raise ValueError("Auto exposure must be an array.")
        return cls(
            Path(data["model_path"]),
            data["model_sha256"],
            Transform.from_dict(data["transform"]),
            settings,
            data["revision"],
            data["printer_id"],
            tuple(Transform.from_dict(value) for value in data["copies"]),
            MaterialProfile.from_dict(data["material"]) if data["material"] is not None else None,
            data["printer_revision"],
            data["repair_single_pixels"],
            Hollowing.from_dict(data["hollowing"]),
            tuple(data["auto_exposure"]),
        )

    @classmethod
    def from_stl_path(cls, path, transform=None, settings=None):
        path = Path(path).expanduser().resolve()
        return cls(path, mesh_hash(path), transform or Transform(), settings)


def save_project(project, dest_path):
    destination = Path(dest_path).expanduser().resolve()
    if destination.suffix.lower() == ".stl" or destination == project.model_path.resolve():
        raise ValueError("A project save must not overwrite an STL source.")
    data = json.dumps(project.to_dict(), allow_nan=False, indent=2).encode() + b"\n"
    if len(data) > MAX_PROJECT_BYTES:
        raise ValueError("Project exceeds 64 KiB.")
    fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".seesaw-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _json_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"Duplicate project field: {name}")
        result[name] = value
    return result


def load_project(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError("Project must be an existing regular file.")
    with path.open("rb") as stream:
        raw = stream.read(MAX_PROJECT_BYTES + 1)
    if len(raw) > MAX_PROJECT_BYTES:
        raise ValueError("Project exceeds 64 KiB.")
    project = Project.from_dict(json.loads(raw, object_pairs_hook=_json_object))
    project.verify_source()
    return project


@dataclass(frozen=True)
class JobSnapshot:
    job_id: str
    revision: int
    fingerprint: str


class JobGate:
    """Track input currency only; this is not a print validation/export decision."""

    def __init__(self):
        self.active = None
        self.completed = None

    def invalidate(self):
        self.active = None
        self.completed = None

    def begin(self, project):
        project.verify_source()
        self.completed = None
        self.active = JobSnapshot(str(uuid.uuid4()), project.revision, project.fingerprint())
        return self.active

    @staticmethod
    def _matches(project, snapshot):
        if snapshot is None or project.revision != snapshot.revision:
            return False
        if project.fingerprint() != snapshot.fingerprint:
            return False
        try:
            project.verify_source()
        except (OSError, ValueError):
            return False
        return True

    def finish(self, project, snapshot):
        if self.active is None or self.active != snapshot:
            return False
        self.active = None
        if not self._matches(project, snapshot):
            self.completed = None
            return False
        self.completed = snapshot
        return True

    def is_current(self, project):
        return self._matches(project, self.completed)
