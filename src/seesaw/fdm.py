"""Pinned PrusaSlicer filament route and bounded actual G-code layer preview."""

import hashlib
import io
import json
import math
import re
import time
from dataclasses import asdict
from pathlib import Path
from threading import Event

from PIL import Image, ImageDraw

from seesaw.pipeline import PipelineError, run_process

ASSETS = Path(__file__).parent / "assets"


def profile_text(settings):
    settings.validate()
    values = json.loads((ASSETS / "mk3s-printer.json").read_text())
    values.update(json.loads((ASSETS / "generic-pla.json").read_text()))
    for key in list(values):
        if key.startswith(("compatible_", "default_")) or key in {"renamed_from", "inherits"}:
            del values[key]
    values.update(
        {
            "printer_technology": "FFF",
            "layer_height": settings.layer_mm,
            "first_layer_height": settings.layer_mm,
            "temperature": settings.nozzle_c,
            "first_layer_temperature": settings.nozzle_c,
            "bed_temperature": settings.bed_c,
            "first_layer_bed_temperature": settings.bed_c,
            "fill_density": f"{settings.infill_percent}%",
            "perimeter_speed": settings.speed_mm_s,
            "infill_speed": settings.speed_mm_s,
            "support_material": int(settings.supports),
            "support_material_auto": 1,
            "support_material_buildplate_only": 0,
            "perimeters": 2,
            "top_solid_layers": 4,
            "bottom_solid_layers": 4,
            "post_process": "",
            "binary_gcode": 0,
            "complete_objects": 0,
            "use_relative_e_distances": 1,
            "thumbnails": "",
        }
    )
    values["end_gcode"] = ";SEESAW_END_MODEL\\n" + values["end_gcode"]
    return "\n".join(f"{key} = {value}" for key, value in values.items()) + "\n"


def motions(stream):
    """Read the generated relative-extrusion subset, retaining layer boundaries."""
    x = y = z = 0.0
    active = False
    for line in stream:
        if line.startswith(";SEESAW_END_MODEL"):
            break
        if line.startswith(";LAYER_CHANGE"):
            active = True
            yield "layer", None
        if not active:
            continue
        if line.startswith(";Z:"):
            yield "z", float(line[3:])
        command = line.split(";", 1)[0].strip()
        if re.match(r"G[01](?:\s|$)", command):
            values = {
                key: float(value)
                for key, value in re.findall(r"([XYZE])([-+]?(?:\d+(?:\.\d*)?|\.\d+))", command)
            }
            nx, ny, nz = values.get("X", x), values.get("Y", y), values.get("Z", z)
            if values.get("E", 0) > 0 and (nx != x or ny != y):
                yield "segment", (x, y, nx, ny, nz)
            x, y, z = nx, ny, nz


class GCodePreview:
    @classmethod
    def from_validated_layers(cls, path, layer_z):
        """Reuse the worker's index; avoid rescanning the entire file on the UI thread."""
        if (
            not layer_z
            or len(layer_z) > 5000
            or any(
                type(z) not in (float, int) or not math.isfinite(z) or not 0 < z <= 210
                for z in layer_z
            )
            or any(b <= a for a, b in zip(layer_z, layer_z[1:]))
        ):
            raise ValueError("Invalid validated G-code layer index.")
        result = cls.__new__(cls)
        result.path, result.build = Path(path), (250, 210, 210)
        result.z_values, result.names = list(layer_z), [str(i) for i in range(len(layer_z))]
        return result

    def __init__(self, path, build_mm=(250, 210, 210), cancel=None):
        self.path = Path(path)
        self.build = build_mm
        self.names = []
        self.z_values = []
        self.segment_counts = []
        count = 0
        with self.path.open() as stream:
            for kind, value in motions(stream):
                if cancel is not None and cancel.is_set():
                    raise PipelineError("Job cancelled.")
                if kind == "layer":
                    self.names.append(str(len(self.names)))
                    if len(self.names) > 5000:
                        raise PipelineError("G-code exceeds 5000 layers.")
                    self.segment_counts.append(0)
                elif kind == "z":
                    self.z_values.append(value)
                else:
                    x, y, nx, ny, z = value
                    if not (
                        0 <= min(x, nx)
                        and max(x, nx) <= build_mm[0]
                        and 0 <= min(y, ny)
                        and max(y, ny) <= build_mm[1]
                        and 0 < z <= build_mm[2]
                    ):
                        raise PipelineError("Extruded toolpath exceeds the selected build volume.")
                    self.segment_counts[-1] += 1
                    count += 1
                    if count > 2_000_000:
                        raise PipelineError(
                            "G-code preview exceeds two million extrusion segments."
                        )
        if not self.names or len(self.names) != len(self.z_values) or not all(self.segment_counts):
            raise PipelineError("G-code has missing or empty printable layers.")
        if any(b <= a for a, b in zip(self.z_values, self.z_values[1:])):
            raise PipelineError("G-code layer heights are not strictly increasing.")

    def thumbnail(self, index):
        if type(index) is not int or not 0 <= index < len(self.names):
            raise ValueError("Layer index is out of range.")
        picture = Image.new("RGB", (900, 756), "#162336")
        draw = ImageDraw.Draw(picture)
        layer = -1
        sx, sy = 899 / self.build[0], 755 / self.build[1]
        with self.path.open() as stream:
            for kind, value in motions(stream):
                if kind == "layer":
                    layer += 1
                    if layer > index:
                        break
                elif kind == "segment" and layer == index:
                    x, y, nx, ny, _ = value
                    draw.line(
                        (x * sx, 755 - y * sy, nx * sx, 755 - ny * sy), fill="#39c9ce", width=2
                    )
        output = io.BytesIO()
        picture.save(output, format="PNG")
        return output.getvalue()


def validate_settings(path, settings):
    """Check native settings readback and the trusted start/end sequence."""
    with Path(path).open("rb") as stream:
        header = stream.read(65536).decode("utf-8")
        stream.seek(max(0, Path(path).stat().st_size - 131072))
        footer = stream.read().decode("utf-8", errors="replace")
    if "; generated by PrusaSlicer 2.9.4" not in header or "\n;SEESAW_END_MODEL\n" not in footer:
        raise PipelineError("G-code generator or completion marker is missing.")
    expected = {
        "temperature": settings.nozzle_c,
        "bed_temperature": settings.bed_c,
        "layer_height": settings.layer_mm,
        "fill_density": settings.infill_percent,
        "perimeter_speed": settings.speed_mm_s,
        "support_material": int(settings.supports),
        "use_relative_e_distances": 1,
    }
    for key, value in expected.items():
        match = re.search(r"^; " + key + r" = ([0-9.]+)%?$", footer, re.MULTILINE)
        if not match or not math.isclose(float(match[1]), value, abs_tol=0.0001):
            raise PipelineError(f"G-code settings readback mismatch: {key}.")
    for code, expected_temperature in (("M109", settings.nozzle_c), ("M190", settings.bed_c)):
        match = re.search(r"^" + code + r" S([0-9.]+)(?:\s|$)", header, re.MULTILINE)
        if not match or not math.isclose(float(match[1]), expected_temperature, abs_tol=0.01):
            raise PipelineError(f"G-code heating command mismatch: {code}.")
    if not re.search(r"^M83(?:\s|$)", header, re.MULTILINE):
        raise PipelineError("G-code relative extrusion setup is missing.")


def run_fdm(model, directory, settings, center, cancel=None, progress=lambda _: None):
    cancel = cancel or Event()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    pending, candidate = directory / ".pending.gcode", directory / "candidate.gcode"
    manifest = {
        "status": "running",
        "hardware_qualified": False,
        "printer_id": "mk3s",
        "settings": asdict(settings),
        "extension": ".gcode",
    }
    try:
        from seesaw.engines import resolve

        prusa = resolve()["prusa_slicer"]
        if not prusa:
            raise PipelineError("Slicer unavailable; reinstall the Seesaw bundle.")
        version = run_process([prusa, "--help"], directory / "version.log", cancel)
        if not re.match(r"^PrusaSlicer-2\.9\.4(?:[+ -]|$)", version):
            raise PipelineError("Filament adapter requires PrusaSlicer 2.9.4.")
        ini = directory / "generated.ini"
        ini.write_text(profile_text(settings))
        progress("slice-filament")
        run_process(
            [
                prusa,
                "--datadir",
                str(directory / "config"),
                "--load",
                str(ini),
                "--center",
                f"{center[0]},{center[1]}",
                "--export-gcode",
                "--output",
                str(pending),
                str(model),
            ],
            directory / "slice.log",
            cancel,
        )
        if not pending.is_file() or not 100 < pending.stat().st_size < 256 * 1024**2:
            raise PipelineError("Native slicer produced no bounded G-code file.")
        progress("inspect-toolpaths")
        validate_settings(pending, settings)
        preview = GCodePreview(pending, cancel=cancel)
        if cancel.is_set():
            raise PipelineError("Job cancelled.")
        pending.replace(candidate)
        manifest.update(
            status="software_validated_not_print_qualified",
            layer_count=len(preview.names),
            layer_z_mm=preview.z_values,
            output_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
            backends={"prusa": version.splitlines()[0]},
            settings_readback=True,
            warning="Software toolpath checks only; firmware/material unqualified.",
        )
        return manifest
    except BaseException as exc:
        pending.unlink(missing_ok=True)
        candidate.unlink(missing_ok=True)
        manifest.update(status="failed", error=str(exc))
        raise
    finally:
        manifest["elapsed_s"] = round(time.monotonic() - started, 3)
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2))
