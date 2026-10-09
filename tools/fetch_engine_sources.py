"""Fetch exact Ubuntu ELF source packages during builds, with .dsc SHA256 checks."""

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen


def download(url, target, expected=None):
    digest = hashlib.sha256()
    size = 0
    temporary = target.with_suffix(target.suffix + ".pending")
    try:
        with urlopen(url, timeout=60) as source, temporary.open("wb") as output:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                size += len(chunk)
                if size > 1024**3:
                    raise ValueError("Source archive exceeds 1 GiB build limit.")
                digest.update(chunk)
                output.write(chunk)
        if expected is not None and digest.hexdigest() != expected:
            raise ValueError("Source archive checksum mismatch.")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    engines = json.loads(args.engine_manifest.read_text())
    packages = sorted(
        {
            (p["source_package"], p["source_version"])
            for p in engines["elf_dependencies"]
            if p["bundled"]
        }
    )
    manifest = []
    for name, version in packages:
        if not re.fullmatch("[a-z0-9.+-]+", name):
            raise ValueError("Invalid source package name.")
        filename = name + "_" + version.split(":")[-1] + ".dsc"
        directory = name[:4] if name.startswith("lib") else name[:1]
        folder = args.output / name
        folder.mkdir()
        for component in ("main", "universe", "restricted", "multiverse"):
            base = (
                "https://archive.ubuntu.com/ubuntu/pool/"
                + component
                + "/"
                + directory
                + "/"
                + name
                + "/"
            )
            try:
                dsc_digest = download(base + quote(filename), folder / filename)
                break
            except HTTPError as exc:
                if exc.code != 404:
                    raise
        else:
            raise ValueError(f"Exact source .dsc unavailable: {name} {version}")
        text = (folder / filename).read_text()
        block = re.search(r"Checksums-Sha256:\n((?: [^\n]+\n)+)", text)
        if not block:
            raise ValueError("Source .dsc has no SHA256 inventory.")
        records = [{"filename": filename, "sha256": dsc_digest, "url": base + quote(filename)}]
        for line in block[1].splitlines():
            expected, declared_size, asset = line.split()
            if Path(asset).name != asset or not re.fullmatch("[a-zA-Z0-9_.+~-]+", asset):
                raise ValueError("Invalid .dsc source asset path.")
            actual = download(base + quote(asset), folder / asset, expected)
            if (folder / asset).stat().st_size != int(declared_size):
                raise ValueError("Source archive size mismatch.")
            records.append({"filename": asset, "sha256": actual, "url": base + quote(asset)})
        manifest.append({"source_package": name, "source_version": version, "files": records})
        (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2))
        print(name, version, "verified", flush=True)


if __name__ == "__main__":
    main()
