"""Research-stage, software-validated Mono 4 conversion. Never claims hardware readiness.

Only generated allowlisted SLA configuration is executed; arbitrary imported INI
profiles (which may carry post-processing scripts) are not accepted.
"""

import hashlib
import io
import json
import math
import os
import re
import shutil
import signal
import subprocess
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event

import numpy as np
from PIL import Image

from seesaw.backends import discover
from seesaw.model import MONO4, load_stl


class PipelineError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    exposure_s: float
    bottom_exposure_s: float
    layer_mm: float = 0.05
    bottom_layers: int = 5
    lift_mm: float = 8.0
    lift_mm_min: float = 120.0
    retract_mm_min: float = 120.0
    rest_s: float = 2.5
    supports: bool = False
    antialias: bool = False

    def validate(self):
        limits = {
            "exposure_s": (0.1, 120),
            "bottom_exposure_s": (0.1, 300),
            "layer_mm": (0.01, 0.2),
            "lift_mm": (1, 20),
            "lift_mm_min": (1, 300),
            "retract_mm_min": (1, 300),
            "rest_s": (0, 120),
        }
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if not math.isfinite(value) or not low <= value <= high:
                raise PipelineError(f"{name} must be finite and between {low} and {high}.")
        if type(self.bottom_layers) is not int or not 1 <= self.bottom_layers <= 20:
            raise PipelineError("bottom_layers must be an integer from 1 to 20.")


def profile_text(settings: Settings) -> str:
    settings.validate()
    custom = {
        "WaitTimeBeforeCure": settings.rest_s,
        "BottomWaitTimeBeforeCure": settings.rest_s,
        "BottomLiftHeight": settings.lift_mm,
        "LiftHeight": settings.lift_mm,
        "BottomLiftSpeed": settings.lift_mm_min,
        "LiftSpeed": settings.lift_mm_min,
        "BottomRetractSpeed": settings.retract_mm_min,
        "RetractSpeed": settings.retract_mm_min,
        "TransitionLayerCount": 0,
        "BottomLightPWM": 255,
        "LightPWM": 255,
    }
    notes = "\\n".join(
        [
            "FILEFORMAT_PM4N",
            "START_CUSTOM_VALUES",
            *(f"{key}_{value}" for key, value in custom.items()),
            "END_CUSTOM_VALUES",
        ]
    )
    # UVTools maps SL1 numFade to BottomLayerCount. Explicitly verify the result;
    # zero faded layers otherwise silently becomes zero bottom layers.
    values = {
        "printer_technology": "SLA",
        "printer_model": "SL1",
        "sla_archive_format": "SL1",
        "bed_shape": "0x0,153.408x0,153.408x87.04,0x87.04",
        "max_print_height": 165,
        "display_width": 153.408,
        "display_height": 87.040,
        "display_pixels_x": 9024,
        "display_pixels_y": 5120,
        "display_orientation": "landscape",
        "display_mirror_x": 1,
        "display_mirror_y": 0,
        "gamma_correction": 1 if settings.antialias else 0,
        "relative_correction": "1,1",
        "relative_correction_x": 1,
        "relative_correction_y": 1,
        "relative_correction_z": 1,
        "absolute_correction": 0,
        "elefant_foot_compensation": 0,
        "layer_height": settings.layer_mm,
        "initial_layer_height": settings.layer_mm,
        "exposure_time": settings.exposure_s,
        "initial_exposure_time": settings.bottom_exposure_s,
        "faded_layers": settings.bottom_layers,
        "supports_enable": int(settings.supports),
        "pad_enable": int(settings.supports),
        "hollowing_enable": 0,
        "support_object_elevation": 5,
        "pad_wall_thickness": 0.5,
        "printer_notes": notes,
        "thumbnails": "224x168/PNG",
    }
    return (
        "# Seesaw generated research profile; resin calibration NOT validated\n"
        + "\n".join(f"{key} = {value}" for key, value in values.items())
        + "\n"
    )


def run_process(argv: list[str], log: Path, cancel: Event, timeout=900) -> str:
    env = os.environ.copy()
    env.update({"LC_ALL": "C", "LANG": "C"})
    with log.open("wb") as output:
        process = subprocess.Popen(
            argv,
            cwd=log.parent,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        started = time.monotonic()
        try:
            while process.poll() is None:
                if cancel.is_set():
                    raise PipelineError("Job cancelled.")
                if time.monotonic() - started > timeout:
                    raise PipelineError("Backend deadline exceeded.")
                if log.stat().st_size > 50 * 1024**2:
                    raise PipelineError("Backend log exceeded its size limit.")
                time.sleep(0.05)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            except ProcessLookupError:
                pass
            raise
    if process.returncode:
        raise PipelineError(f"Backend failed ({process.returncode}); see {log.name}.")
    if log.stat().st_size > 50 * 1024**2:
        raise PipelineError("Backend log exceeded its size limit.")
    return log.read_text(errors="replace")


def properties(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9]+): (.*)", line)
        if match:
            key, value = match.groups()
            if key in result and result[key] != value:
                raise PipelineError(f"Ambiguous decoded property {key}.")
            result[key] = value
    return result


def expect(values: dict, key: str, expected):
    actual = values.get(key)
    if isinstance(expected, (int, float)):
        try:
            valid = math.isfinite(float(actual)) and math.isclose(
                float(actual), expected, rel_tol=0, abs_tol=0.001
            )
        except (TypeError, ValueError):
            valid = False
    else:
        valid = actual == expected
    if not valid:
        raise PipelineError(f"Decoded {key} is {actual!r}; expected {expected!r}.")


def compare_layers(
    original: Path, roundtrip: Path, count: int, cancel: Event, progress=lambda _: None
) -> dict:
    changed = 0
    max_delta = 0
    with zipfile.ZipFile(original) as before, zipfile.ZipFile(roundtrip) as after:
        left = sorted(n for n in before.namelist() if re.fullmatch(r"[^/]+\d{5}\.png", n))
        right = sorted(n for n in after.namelist() if re.fullmatch(r"[^/]+\d{5}\.png", n))
        if (
            len(left) != count
            or len(right) != count
            or len(set(left)) != count
            or len(set(right)) != count
        ):
            raise PipelineError("Layer archive counts or names do not match.")
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            progress(f"verify layer pixels {index + 1}/{count}")
            if cancel.is_set():
                raise PipelineError("Job cancelled during layer verification.")
            arrays = []
            for archive, name in ((before, a), (after, b)):
                if archive.getinfo(name).file_size > 100 * 1024**2:
                    raise PipelineError("Layer PNG exceeds its size limit.")
                with Image.open(io.BytesIO(archive.read(name))) as picture:
                    if picture.size != MONO4.resolution_px:
                        raise PipelineError("Unexpected raster dimensions.")
                    arrays.append(np.asarray(picture.convert("L")))
            x, y = arrays
            # UVTools PW0 codec: high 4 bits encoded, then expanded by nibble replication.
            # Verify the precise transformation, not a blanket pixel-error tolerance.
            expected = (x >> 4) * 17
            if not np.array_equal(expected, y):
                raise PipelineError(
                    "Decoded layer differs beyond the documented 4-bit quantization."
                )
            changed += int(np.count_nonzero(x != y))
            max_delta = max(max_delta, int(np.abs(x.astype(np.int16) - y).max()))
    return {
        "verified_layers": count,
        "changed_pixels": changed,
        "max_gray_delta": max_delta,
        "quantization": "exact (input >> 4) * 17",
        "rotation_or_mirror_introduced": False,
    }


def validate_metadata(values: dict, settings: Settings, count: int):
    expected = {
        "MachineName": "Photon Mono 4",
        "ResolutionX": 9024,
        "ResolutionY": 5120,
        "DisplayWidth": 153.408,
        "DisplayHeight": 87.040,
        "MachineZ": 165,
        "LayerHeight": settings.layer_mm,
        "LayerCount": count,
        "BottomLayerCount": settings.bottom_layers,
        "TransitionLayerCount": 0,
        "BottomExposureTime": settings.bottom_exposure_s,
        "ExposureTime": settings.exposure_s,
        "BottomLiftHeight": settings.lift_mm,
        "LiftHeight": settings.lift_mm,
        "BottomLiftSpeed": settings.lift_mm_min,
        "LiftSpeed": settings.lift_mm_min,
        "BottomRetractSpeed": settings.retract_mm_min,
        "RetractSpeed": settings.retract_mm_min,
        "BottomWaitTimeBeforeCure": settings.rest_s,
        "WaitTimeBeforeCure": settings.rest_s,
        "Version": 1,
        "DisplayMirror": "Horizontally",
    }
    for key, value in expected.items():
        expect(values, key, value)


def run_pipeline(
    model: Path,
    directory: Path,
    settings: Settings,
    cancel=None,
    progress=lambda _: None,
    center=None,
    repair_single_pixels=False,
    hollowing=None,
    drain_holes=(),
    reject_large_islands_early=False,
):
    settings.validate()
    if type(reject_large_islands_early) is not bool:
        raise PipelineError("Preflight choice must be boolean.")
    if reject_large_islands_early and not repair_single_pixels:
        raise PipelineError("Smart preflight requires verified singleton repair.")
    if type(repair_single_pixels) is not bool:
        raise PipelineError("Repair choice must be boolean.")
    model = model.resolve(strict=True)
    input_mesh, info = load_stl(model)
    if not info.watertight or not info.fits_unrotated:
        raise PipelineError("Research pipeline requires a closed mesh that fits the Mono 4.")
    estimated_layers = math.ceil(
        (info.dimensions_mm[2] + (7 if settings.supports else 0)) / settings.layer_mm
    )
    if estimated_layers > 512:
        raise PipelineError("Research adapter is limited to 512 layers until large-job profiling.")
    # UVTools expands layers in native memory. Reserve room for decoding and comparison.
    available = re.search(r"^MemAvailable:\s+(\d+) kB", Path("/proc/meminfo").read_text(), re.M)
    required = estimated_layers * 9024 * 5120 * 2 + 1024**3
    if available is None or required > int(available[1]) * 1024 * 0.6:
        raise PipelineError("Insufficient available memory for conservative 10K layer processing.")
    tools = discover()
    if not tools["prusa_slicer"] or not tools["uvtools"]:
        raise PipelineError(
            "Engine bundle unavailable; reinstall Seesaw or configure development engines."
        )
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    cancel = cancel or Event()
    manifest = {"status": "running", "hardware_qualified": False, "settings": asdict(settings)}
    started = time.monotonic()
    candidate = directory / "candidate.pm4n"
    pending = directory / ".pending.pm4n"
    manifest_file = directory / "manifest.json"
    try:
        if shutil.disk_usage(directory).free < 2 * 1024**3:
            raise PipelineError(
                "At least 2 GiB of scratch space is required for this research job."
            )
        shutil.copyfile(model, directory / "model.stl")
        manifest["input_sha256"] = hashlib.sha256(
            (directory / "model.stl").read_bytes()
        ).hexdigest()

        def execute(label, argv):
            progress(label)
            return run_process([str(a) for a in argv], directory / f"{label}.log", cancel)

        prusa = tools["prusa_slicer"]
        uv = tools["uvtools"]
        version = execute("prusa-version", [prusa, "--help"])
        if not re.match(r"^PrusaSlicer-2\.9\.4(?:[+ -]|$)", version):
            raise PipelineError("This research adapter is qualified only with PrusaSlicer 2.9.4.")
        uv_version = execute("uvtools-version", [uv, "--core-version"]).strip()
        if uv_version != "7.0.1":
            raise PipelineError("This research adapter requires UVTools core 7.0.1.")
        manifest["backends"] = {"prusa": version.splitlines()[0], "uvtools_core": uv_version}
        ini = directory / "generated.ini"
        config = profile_text(settings)
        if hollowing is not None and hollowing.enabled:
            if not drain_holes:
                raise PipelineError("Hollowing requires transformed drain holes.")
            config = config.replace("hollowing_enable = 0", "hollowing_enable = 1")
            config += (f"hollowing_min_thickness = {hollowing.thickness_mm}\n"
                       "hollowing_quality = 0.5\nhollowing_closing_distance = 0.5\n")
        ini.write_text(config)
        slice_source = directory / "model.stl"
        if hollowing is not None and hollowing.enabled:
            from dataclasses import replace

            from seesaw.hollowing import add_native_drains

            # Prusa SLA drain records are object coordinates. Its SLA transform does
            # not apply the volume's centering offset; export a centered mesh so that
            # volume and object frames coincide, then rebase holes by the same vector.
            shift = input_mesh.bounds.mean(axis=0)
            centered_mesh = input_mesh.copy()
            centered_mesh.vertices -= shift
            centered_source = directory / "centered.stl"
            centered_mesh.export(centered_source)
            native_holes = tuple(replace(h, position_mm=tuple(
                float(v) for v in np.asarray(h.position_mm) - shift)) for h in drain_holes)
            slice_source = directory / "prepared.3mf"
            execute("drain-interchange", [prusa, "--datadir", directory / "prusa-config",
                                          "--load", ini, "--export-3mf", "--output", slice_source,
                                          centered_source])
            add_native_drains(slice_source, native_holes,
                              expected_z=-float(centered_mesh.bounds[0, 2]))
            manifest["hollowing"] = hollowing.to_dict()
        sl1 = directory / "layers.sl1"
        execute(
            "slice",
            [
                prusa,
                "--datadir",
                directory / "prusa-config",
                "--load",
                ini,
                "--export-sla",
                "--center",
                f"{center[0]},{center[1]}" if center is not None else "76.704,43.52",
                "--output",
                sl1,
                slice_source,
            ],
        )
        if not sl1.is_file():
            raise PipelineError("PrusaSlicer produced no layer archive; inspect slice.log.")
        with zipfile.ZipFile(sl1) as archive:
            count = len([n for n in archive.namelist() if re.fullmatch(r"[^/]+\d{5}\.png", n)])
        if not settings.bottom_layers < count <= 512:
            raise PipelineError("Job needs normal layers after bottom layers (max 512 total).")
        manifest["layer_count"] = count
        if repair_single_pixels or reject_large_islands_early:
            from seesaw.issues import parse_islands, repair_properties, verify_repair

            report = execute("repair-findings", [uv, "print-issues", sl1, "--islands"])
            islands = parse_islands(report, count)
            if reject_large_islands_early and any(i.pixels > 1 for i in islands):
                (directory / "issues.log").write_text(report)
                raise PipelineError("UVTools reported issues: unsupported islands remain.")
            singletons = [i for i in islands if i.pixels == i.width == i.height == 1]
            if len(singletons) > 64:
                raise PipelineError("More than 64 singleton islands; change geometry or supports.")
            manifest["repair"] = {"requested": True, "removed_pixels": 0}
            if singletons:
                repaired = directory / "repaired.sl1"
                argv = [uv, "run", sl1, "OperationRepairLayers"]
                for key, value in repair_properties().items():
                    argv.extend(["-p", f"{key}={value}"])
                argv.extend(["-o", repaired])
                execute("repair-single-pixels", argv)
                manifest["repair"].update(
                    verify_repair(sl1, repaired, islands, count, cancel, progress)
                )
                sl1 = repaired
        execute("encode", [uv, "convert", sl1, "pm4n", pending, "--no-overwrite"])
        if not pending.is_file() or pending.stat().st_size < 100:
            raise PipelineError("UVTools did not produce a native file.")
        decoded = properties(execute("metadata", [uv, "print-properties", pending]))
        validate_metadata(decoded, settings, count)
        layers = execute(
            "layer-metadata",
            [
                uv,
                "print-properties",
                pending,
                "--range",
                f"0:{count - 1}",
                "--names",
                "PositionZ",
                "ExposureTime",
            ],
        )
        blocks = re.split(r"# Layer: (\d+)\s*\n", layers)
        if len(blocks) != count * 2 + 1:
            raise PipelineError("Could not read all per-layer metadata.")
        for offset in range(count):
            if int(blocks[2 * offset + 1]) != offset:
                raise PipelineError("Layer order mismatch.")
            fields = properties(blocks[2 * offset + 2])
            expect(fields, "PositionZ", (offset + 1) * settings.layer_mm)
            expect(
                fields,
                "ExposureTime",
                settings.bottom_exposure_s
                if offset < settings.bottom_layers
                else settings.exposure_s,
            )
        roundtrip = directory / "readback.sl1"
        execute("readback", [uv, "convert", pending, "sl1", roundtrip, "--no-overwrite"])
        progress("verify-layer-pixels")
        manifest["pixels"] = compare_layers(sl1, roundtrip, count, cancel, progress)
        issues = execute(
            "issues",
            [
                uv,
                "print-issues",
                pending,
                "--islands",
                "--touching-bounds",
                "--print-height",
                *( ["--resin-traps", "--suction-cups"]
                   if hollowing is not None and hollowing.enabled else [] ),
                "--empty-layers",
            ],
        )
        if not re.search(r"^Issues: 0\s*$", issues, re.MULTILINE):
            raise PipelineError("UVTools reported issues; inspect issues.log before proceeding.")
        pending.replace(candidate)
        manifest.update(
            {
                "status": "software_validated_not_print_qualified",
                "layer_count": count,
                "output_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                "warning": "Research candidate only; resin settings and firmware unqualified.",
            }
        )
        return manifest
    except BaseException as exc:
        candidate.unlink(missing_ok=True)
        pending.unlink(missing_ok=True)
        manifest.update({"status": "failed", "error": str(exc)})
        raise
    finally:
        manifest["elapsed_s"] = round(time.monotonic() - started, 3)
        temporary = manifest_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(manifest, indent=2) + "\n")
        temporary.replace(manifest_file)
