"""Build an offline-runtime Ubuntu amd64 .deb using a bundled Python and locked wheels.

Build downloads happen here, never at application startup or in package maintainer scripts.
Build on Ubuntu amd64 with Python 3.12 from python-build-standalone (uv-managed).
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tomllib
from importlib.metadata import distributions
from pathlib import Path


def run(*args):
    subprocess.run([str(a) for a in args], check=True)


def write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--engines", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    os.chdir(repo)
    version = tomllib.loads((repo / "pyproject.toml").read_text())["project"]["version"]
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise SystemExit("This package builder targets Linux amd64 only.")
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("Build with the project's uv-managed Python 3.12 interpreter.")
    args.work = args.work.resolve()
    args.output = args.output.resolve()
    stage = args.work / f"deb-stage-{version}"
    if stage.exists():
        raise SystemExit(f"Build stage already exists: {stage}; use a fresh --work directory.")
    stage.mkdir(parents=True)
    args.output.mkdir(parents=True, exist_ok=True)
    runtime = stage / "opt/progretech-seesaw"
    from seesaw.engines import bundled

    bundled(args.engines)
    shutil.copytree(args.engines, runtime / "engines")
    python_root = Path(sys.base_prefix)
    shutil.copytree(python_root, runtime / "python", symlinks=True)
    # Prevent ambient packages or a working directory from altering the installed application.
    site = runtime / "site"
    site.mkdir()
    lock = args.work / "requirements-desktop.txt"
    run(
        "uv",
        "export",
        "--locked",
        "--extra",
        "desktop",
        "--extra",
        "generation",
        "--no-dev",
        "--no-emit-project",
        "--output-file",
        lock,
    )
    run(
        "uv",
        "pip",
        "install",
        "--python",
        sys.executable,
        "--target",
        site,
        "--require-hashes",
        "-r",
        lock,
    )
    wheels = args.work / "wheels"
    run("uv", "build", "--wheel", "--out-dir", wheels)
    wheel = next(wheels.glob(f"progretech_seesaw-{version}-*.whl"))
    run("uv", "pip", "install", "--python", sys.executable, "--target", site, "--no-deps", wheel)
    write(
        runtime / "launch.py",
        'import sys\nsys.path.insert(0, "/opt/progretech-seesaw/site")\n'
        "from seesaw.app import main\nmain()\n",
    )
    write(
        stage / "usr/bin/progretech-seesaw",
        "#!/bin/sh\nexec /opt/progretech-seesaw/python/bin/python3.12 -I "
        '/opt/progretech-seesaw/launch.py "$@"\n',
        0o755,
    )
    write(
        runtime / "generate.py",
        'import sys\nsys.path.insert(0, "/opt/progretech-seesaw/site")\n'
        "from seesaw.generation_cli import main\nmain()\n",
    )
    write(
        stage / "usr/bin/progretech-seesaw-generate",
        "#!/bin/sh\nexec /opt/progretech-seesaw/python/bin/python3.12 -I "
        '/opt/progretech-seesaw/generate.py "$@"\n',
        0o755,
    )
    write(
        runtime / "cli.py",
        'import sys\nsys.path.insert(0, "/opt/progretech-seesaw/site")\n'
        "from seesaw.cli import main\nmain()\n",
    )
    write(
        stage / "usr/bin/progretech-seesaw-cli",
        "#!/bin/sh\nexec /opt/progretech-seesaw/python/bin/python3.12 -I "
        '/opt/progretech-seesaw/cli.py "$@"\n',
        0o755,
    )
    helper = stage / "usr/lib/progretech-seesaw/install-update"
    helper.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(repo / "packaging/install-update", helper)
    helper.chmod(0o755)
    for source, dest in (
        ("packaging/progretech-seesaw.desktop", "usr/share/applications/progretech-seesaw.desktop"),
        (
            "packaging/progretech-seesaw.svg",
            "usr/share/icons/hicolor/scalable/apps/progretech-seesaw.svg",
        ),
        ("LICENSE", "usr/share/doc/progretech-seesaw/copyright"),
    ):
        target = stage / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / source, target)
    docs = stage / "usr/share/doc/progretech-seesaw"
    shutil.copytree(repo / "docs", docs / "project")
    shutil.copytree(repo / "packaging/licenses", docs / "licenses")
    inventory = []
    for distribution in distributions(path=[str(site)]):
        inventory.append(
            {
                "name": distribution.metadata["Name"],
                "version": distribution.version,
                "license": distribution.metadata.get("License-Expression")
                or distribution.metadata.get("License", "See packaged license files"),
                "home": distribution.metadata.get_all("Project-URL", []),
            }
        )
    write(docs / "python-dependencies.json", json.dumps(inventory, indent=2) + "\n")
    shutil.copy2(lock, docs / "requirements-desktop.txt")
    deps = (
        "libc6 (>= 2.43), libicu78, libssl3t64, libstdc++6, libgl1, libegl1, libopengl0, libgomp1, "
        "libx11-6, libxext6, libxrender1, libxcb1, libxcb-cursor0, libxcb-icccm4, "
        "libxcb-image0, libxcb-keysyms1, libxcb-render-util0, libxcb-xkb1, "
        "libxkbcommon-x11-0, libdbus-1-3, libfontconfig1, libfreetype6, libsm6, "
        "libice6, libxfixes3, libxrandr2, libxcursor1, libxi6, python3, pkexec, ca-certificates"
    )
    size = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file()) // 1024
    write(
        stage / "DEBIAN/control",
        f"""Package: progretech-seesaw
Version: {version}
Section: graphics
Priority: optional
Architecture: amd64
Maintainer: ProgreTech Seesaw <eabdiel@users.noreply.github.com>
Installed-Size: {size}
Depends: {deps}
Homepage: https://github.com/progretech-llc/ProgreTech-Seesaw
Description: Offline resin and filament slicing workspace for Ubuntu
 Python desktop with preparation, layer preview and explicit GitHub release updates.
 Bundled native PrusaSlicer and UVTools engines run offline without manual tool installs.
 Printer firmware and material settings remain physically unqualified.
""",
    )
    # No maintainer scripts and no network operations during installation.
    target = args.output / f"progretech-seesaw_{version}_amd64.deb"
    run("dpkg-deb", "--root-owner-group", "-Zxz", "-z3", "--build", stage, target)
    print(target)


if __name__ == "__main__":
    main()
