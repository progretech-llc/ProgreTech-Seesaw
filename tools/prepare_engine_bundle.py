"""Package a source-built CLI and pinned self-contained UVTools with ELF closure.

This runs only during release preparation. Never downloads or installs at app startup.
Ubuntu 26 amd64 base libc/loader, graphics drivers, ICU and OpenSSL stay OS dependencies.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

UV_HASH = "2de38a9fe07c2e542cc125f82b0f76884e0b5b7ea52de354b96d854c802e2dbc"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def probe_versions(root):
    prusa = subprocess.run([str(root / "bin/prusa-slicer"), "--help"],
                           capture_output=True, text=True, check=True, timeout=60)
    uv = subprocess.run([str(root / "bin/UVtoolsCmd"), "--core-version"],
                        capture_output=True, text=True, check=True, timeout=60)
    if not re.match(r"^PrusaSlicer-2\.9\.4(?:[+ -]|$)", prusa.stdout):
        raise ValueError("PrusaSlicer actual binary version is outside the qualified pin.")
    if uv.stdout.strip() != "7.0.1":
        raise ValueError("UVTools actual core version is outside the qualified pin.")
    return {"prusa_slicer": prusa.stdout.splitlines()[0], "uvtools_core": uv.stdout.strip()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prusa", required=True, type=Path)
    parser.add_argument("--resources", required=True, type=Path)
    parser.add_argument("--uvtools-zip", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--notices", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    if root.exists():
        raise SystemExit("Use a fresh output directory.")
    if digest(args.uvtools_zip) != UV_HASH:
        raise SystemExit("Pinned upstream UVTools archive checksum mismatch.")
    root.mkdir(parents=True)
    binary = root / "prusa/bin/PrusaSlicer"
    binary.parent.mkdir(parents=True)
    shutil.copy2(args.prusa, binary)
    binary.chmod(0o755)
    shutil.copytree(args.resources, root / "prusa/resources")
    uv = root / "uvtools"
    uv.mkdir()
    with zipfile.ZipFile(args.uvtools_zip) as archive:
        for entry in archive.infolist():
            relative = Path(entry.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise SystemExit("Unsafe upstream archive path.")
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise SystemExit("Upstream archive symlink requires explicit review.")
        archive.extractall(uv)
    # Optional LTTng event-tracing provider targets an old UST ABI. It is not
    # required by the CLR or codecs; do not ship an unresolved optional ELF.
    trace = uv / "libcoreclrtraceptprovider.so"
    exclusions = {trace.name: digest(trace)}
    trace.unlink()
    for name in ("UVtools", "UVtoolsCmd"):
        (uv / name).chmod(0o755)
    libraries = root / "lib"
    libraries.mkdir()
    inventory = []
    seen = set()
    roots = [binary, uv / "UVtoolsCmd", *uv.glob("*.so")]
    for executable in roots:
        output = subprocess.run(["ldd", str(executable)], text=True, capture_output=True,
                                env={**os.environ, "LD_LIBRARY_PATH": str(uv)})
        # Native upstream .so files only; managed assemblies are not passed to ldd.
        if output.returncode:
            raise SystemExit(f"ELF dependency inspection failed: {executable.name}")
        if "not found" in output.stdout:
            raise SystemExit(f"Unresolved native dependency: {executable.name}")
        for name, path in re.findall(r"\s+(\S+) => (/\S+) \(", output.stdout):
            source = Path(path).resolve(strict=True)
            if source in seen or root in source.parents:
                continue
            seen.add(source)
            # Bundle all non-glibc linked dependencies. libc and loader form the
            # supported Ubuntu ABI; copying them could break the user's host.
            package = subprocess.run(["dpkg-query", "-S", str(source)],
                                     capture_output=True, text=True).stdout.split(": ")[0]
            if not package:
                raise SystemExit(f"No package provenance for native library {source}")
            metadata = subprocess.check_output(["dpkg-query", "-W", "-f",
                "${binary:Package}\t${Version}\t${source:Package}\t${source:Version}", package],
                text=True).split("\t")
            bundled = not package.startswith("libc6:")
            if bundled:
                target = libraries / name
                if target.exists() and digest(target) != digest(source):
                    raise SystemExit("Conflicting native SONAME payloads.")
                shutil.copy2(source, target)
                copyright_file = Path("/usr/share/doc") / package.split(":")[0] / "copyright"
                if not copyright_file.is_file():
                    raise SystemExit(f"Missing library copyright notice: {package}")
                shutil.copy2(copyright_file, libraries / (name + ".copyright"))
            inventory.append({"soname": name, "sha256": digest(source),
                              "binary_package": metadata[0], "binary_version": metadata[1],
                              "source_package": metadata[2], "source_version": metadata[3],
                              "bundled": bundled,
                              "source_access": "https://launchpad.net/ubuntu/+source/" +
                                  metadata[2] + "/" + metadata[3]})
    for name, path in (("prusa-slicer", "prusa/bin/PrusaSlicer"),
                       ("UVtoolsCmd", "uvtools/UVtoolsCmd")):
        write(root / "bin" / name,
              '#!/bin/sh\nset -eu\nroot=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
              'unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH DOTNET_ROOT DOTNET_ROOT_X64\n'
              'export LD_LIBRARY_PATH="$root/lib:$root/uvtools"\n'
              'export DOTNET_MULTILEVEL_LOOKUP=0\n'
              f'exec "$root/{path}" "$@"\n', 0o755)
    shutil.copytree(args.notices, root / "notices")
    shutil.copy2(args.provenance, root / "build-provenance.json")
    versions = probe_versions(root)
    files = {str(p.relative_to(root)): digest(p) for p in sorted(root.rglob("*")) if p.is_file()}
    manifest = {"schema": 1, "platform": "ubuntu26-amd64",
                "versions": versions,
                "executables": {"prusa_slicer": "bin/prusa-slicer", "uvtools": "bin/UVtoolsCmd"},
                "files": files, "elf_dependencies": inventory,
                "excluded_optional_files": exclusions,
                "prusa_native_sha256": digest(binary), "uvtools_archive_sha256": UV_HASH,
                "prusa_build": "Ubuntu slic3r-prusa 2.9.4+dfsg-4 source; GUI OFF, FHS OFF, "
                               "STEP OFF, desktop integration OFF; unmodified source algorithms"}
    write(root / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    print(root)


if __name__ == "__main__":
    main()
