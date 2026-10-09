"""Native layer findings and narrowly bounded, explicit single-pixel repair."""

import io
import re
import zipfile
from dataclasses import asdict, dataclass

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class Island:
    layer: int
    pixels: int
    x: int
    y: int
    width: int
    height: int

    def to_dict(self):
        return asdict(self)


def parse_islands(report, count):
    """Accept the pinned native islands-only report, never infer a missing result."""
    totals = re.findall(r"^Issues: (\d+)\s*$", report, re.MULTILINE)
    if len(totals) != 1:
        raise ValueError("Missing or ambiguous native issue count.")
    pattern = r"^Island, (\d+), (\d+)px², \{X=(\d+),Y=(\d+),Width=(\d+),Height=(\d+)\}$"
    islands = [Island(*map(int, match)) for match in re.findall(pattern, report, re.MULTILINE)]
    if len(islands) != int(totals[0]):
        raise ValueError("Unrecognized native finding; inspect the job log.")
    if len(islands) > 10000 or len(set(islands)) != len(islands):
        raise ValueError("Too many or duplicate native findings.")
    for value in islands:
        if not (
            0 <= value.layer < count
            and 0 < value.pixels <= value.width * value.height
            and value.width > 0
            and value.height > 0
            and 0 <= value.x < value.x + value.width <= 9024
            and 0 <= value.y < value.y + value.height <= 5120
        ):
            raise ValueError("Native finding is outside the layer bounds.")
    return islands


def repair_properties():
    return {
        "DetectIssues": "true",
        "RepairIslands": "true",
        "RepairResinTraps": "false",
        "RepairSuctionCups": "false",
        "RemoveEmptyLayers": "false",
        "RemoveIslandsBelowEqualPixelCount": "1",
        "RemoveIslandsRecursiveIterations": "1",
        "AttachIslandsBelowLayers": "0",
        "GapClosingIterations": "0",
        "NoiseRemovalIterations": "0",
    }


def verify_repair(before_path, after_path, islands, count, cancel, progress=lambda _: None):
    """Every changed raster pixel must be a pre-reported singleton removed to black."""
    targets = {(i.layer, i.x, i.y) for i in islands if i.pixels == i.width == i.height == 1}
    if not 1 <= len(targets) <= 64:
        raise ValueError("Single-pixel repair requires 1–64 reported singleton islands.")
    seen = set()
    with zipfile.ZipFile(before_path) as before, zipfile.ZipFile(after_path) as after:

        def names(archive):
            result = sorted(n for n in archive.namelist() if re.fullmatch(r"[^/]+\d{5}\.png", n))
            if len(result) != count or len(set(result)) != count:
                raise ValueError("Repair changed layer count or duplicated layer names.")
            return result

        for index, (left, right) in enumerate(zip(names(before), names(after), strict=True)):
            if cancel.is_set():
                raise ValueError("Job cancelled during repair verification.")
            progress(f"verify repair pixels {index + 1}/{count}")
            arrays = []
            for archive, name in ((before, left), (after, right)):
                if archive.getinfo(name).file_size > 100 * 1024**2:
                    raise ValueError("Repair layer exceeds its size bound.")
                with Image.open(io.BytesIO(archive.read(name))) as picture:
                    if picture.size != (9024, 5120):
                        raise ValueError("Repair changed raster dimensions.")
                    arrays.append(np.asarray(picture.convert("L")))
            source, repaired = arrays
            delta = source != repaired
            if np.count_nonzero(delta) > len(targets):
                raise ValueError("Repair changed more pixels than authorized.")
            yy, xx = np.nonzero(delta)
            for y, x in zip(yy, xx):
                point = (index, int(x), int(y))
                if point not in targets or repaired[y, x] != 0 or source[y, x] == 0:
                    raise ValueError("Repair changed an unauthorized pixel.")
                seen.add(point)
    if seen != targets:
        raise ValueError("Repair did not remove exactly the reported singleton islands.")
    return {"removed_pixels": len(seen), "points": sorted(seen), "verified_layers": count}


def summarize_issues(report):
    """Human summary only; never changes native acceptance or repair policy."""
    totals = re.findall(r"^Issues: (\d+)\s*$", report, re.MULTILINE)
    if len(totals) != 1 or int(totals[0]) < 1:
        raise ValueError("Missing native findings.")
    matches = re.findall(r"^Island, (\d+), (\d+)px²,", report, re.MULTILINE)
    if len(matches) == int(totals[0]):
        layer, pixels = map(int, matches[0])
        return (
            f"{totals[0]} unsupported island(s); first on layer {layer + 1}, "
            f"{pixels:,} pixels. Exposure changes do not resolve unsupported geometry."
        )
    kinds = sorted(set(re.findall(r"^([A-Za-z]+), ", report, re.MULTILINE)))
    return (
        f"{totals[0]} finding(s): {', '.join(kinds) or 'unrecognized type'}. Inspect Job details."
    )
