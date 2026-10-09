"""Collect exact public NuGet package notices/provenance for bundled UVTools files."""

import argparse
import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen


def upstream_notice(item, folder):
    """Save concrete license text; distinguish repository text from SPDX fallback."""
    repository = item["repository"] or {}
    repo = repository.get("url", "").removesuffix(".git")
    commit = repository.get("commit")
    candidates = []
    if repo.startswith("https://github.com/") and commit:
        base = repo.replace("https://github.com/", "https://raw.githubusercontent.com/")
        candidates.extend(
            (f"{base}/{commit}/{name}", "pinned-repository-license")
            for name in (
                "LICENSE",
                "LICENSE.txt",
                "LICENSE.md",
                "License.txt",
                "LICENSE.TXT",
                "License.html",
            )
        )
    url = item["license_url"]
    if url and "licenses.nuget.org" not in url:
        candidates.append((url, "declared-license-url"))
    if item["license"] == "MIT":
        candidates.append(
            (
                "https://raw.githubusercontent.com/spdx/license-list-data/v3.27.0/text/MIT.txt",
                "spdx-template-with-package-attribution",
            )
        )
    for url, provenance in candidates:
        try:
            with urlopen(url, timeout=20) as response:
                raw = response.read(1024**2 + 1)
                content_type = response.headers.get("Content-Type", "")
            if (
                len(raw) > 1024**2
                or not raw
                or ("html" in content_type and not url.endswith("License.html"))
            ):
                continue
            name = "upstream-license.txt"
            (folder / name).write_bytes(raw)
            attribution = {
                "authors": item["authors"],
                "copyright": item["copyright"],
                "source": "exact-package-nuspec",
                "license_text_provenance": provenance,
            }
            (folder / "attribution.json").write_text(json.dumps(attribution, indent=2) + "\n")
            return {
                "file": name,
                "url": url,
                "provenance": provenance,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        except (OSError, ValueError):
            continue
    raise ValueError(f"No concrete license text recovered for {item['package']}")


def fetch(package, root):
    name, version = package.split("/")
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", name + version):
        raise ValueError("Invalid public package identity.")
    lower = name.lower()
    url = f"https://api.nuget.org/v3-flatcontainer/{lower}/{version}/{lower}.{version}.nupkg"
    with urlopen(url, timeout=60) as response:
        raw = response.read(128 * 1024**2 + 1)
    if len(raw) > 128 * 1024**2:
        raise ValueError("NuGet package exceeds build bound.")
    folder = root / name / version
    folder.mkdir(parents=True)
    notices = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        nuspec_name = next(n for n in archive.namelist() if n.lower().endswith(".nuspec"))
        nuspec = archive.read(nuspec_name)
        (folder / "package.nuspec").write_bytes(nuspec)
        xml = ET.fromstring(nuspec)
        metadata = xml.find("{*}metadata")
        license_element = metadata.find("{*}license")
        license_value = license_element.text if license_element is not None else None
        license_type = license_element.get("type") if license_element is not None else None
        license_url = metadata.findtext("{*}licenseUrl")
        repository = metadata.find("{*}repository")
        names = set(
            n
            for n in archive.namelist()
            if any(
                term in Path(n).name.lower()
                for term in ("license", "copyright", "notice", "copying")
            )
        )
        if license_type == "file":
            names.add(license_value)
        for index, member in enumerate(sorted(names)):
            if member.endswith("/"):
                continue
            data = archive.read(member)
            if len(data) > 1024**2:
                raise ValueError("License notice exceeds bounded text size.")
            target = f"notice-{index}-{Path(member).name}"
            (folder / target).write_bytes(data)
            notices.append(
                {
                    "file": target,
                    "upstream_path": member,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
    if not notices and not license_value and not license_url:
        raise ValueError(f"Package has no license metadata: {package}")
    item = {
        "package": name,
        "version": version,
        "nupkg_url": url,
        "nupkg_sha256": hashlib.sha256(raw).hexdigest(),
        "license_type": license_type,
        "license": license_value,
        "license_url": license_url,
        "authors": metadata.findtext("{*}authors"),
        "copyright": metadata.findtext("{*}copyright"),
        "repository": dict(repository.attrib) if repository is not None else None,
        "project_url": metadata.findtext("{*}projectUrl"),
        "notices": notices,
    }
    if not notices or (
        license_type == "expression"
        and license_value == "MIT"
        and not any("licen" in notice["file"].lower() for notice in notices)
    ):
        item["notices"].append(upstream_notice(item, folder))
    if name == "SixLabors.ImageSharp":
        url = "https://raw.githubusercontent.com/spdx/license-list-data/v3.27.0/text/Apache-2.0.txt"
        with urlopen(url, timeout=30) as response:
            text = response.read(1024**2 + 1)
        if len(text) > 1024**2:
            raise ValueError("Granted license text exceeds build bound.")
        (folder / "Apache-2.0.txt").write_bytes(text)
        item["notices"].append(
            {
                "file": "Apache-2.0.txt",
                "url": url,
                "provenance": "spdx-granted-license-text",
                "sha256": hashlib.sha256(text).hexdigest(),
            }
        )
        item["distribution_basis"] = (
            "Seesaw is AGPL source-available; ImageSharp is a transitive UVTools dependency. "
            "Included split license specifies Apache-2.0 for these criteria; no revenue assumption."
        )
    return item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uvtools", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    packages = set()
    for name in ("UVtoolsCmd.deps.json",):
        data = json.loads((args.uvtools / name).read_text())
        packages.update(
            name for name, item in data["libraries"].items() if item.get("type") == "package"
        )
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda p: fetch(p, args.output), sorted(packages)))
    (args.output / "inventory.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"Verified {len(results)} public package identities/notices")


if __name__ == "__main__":
    main()
