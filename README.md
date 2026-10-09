> **0.5.2 preparation:** native hollow/drain editing and one Seesaw installer for
> Ubuntu 26.04 amd64. See [single-app workflow](docs/SINGLE_APP_0_5.md) and
> [Auto exposure and test diagnostics](docs/AUTO_EXPOSURE_0_5_1.md).
> [Smart slice](docs/SMART_SLICE_0_5_2.md) adds native supports and tries validated tilts.
> Physical printer/material acceptance remains separate.

# ProgreTech Seesaw — offline 3D printing software for Ubuntu

Offline 3D printing software for Ubuntu/Linux: Python workspace with PrusaSlicer and UVTools, resin/FDM profiles, and experimental image-to-3D.

A project of **[ProgreTech LLC](https://progretech.com)**, owned and maintained by **Ed Rodriguez**. Third-party components and contributions retain their respective ownership and notices.

[Project website](https://progretech.com) · [Report an issue](https://github.com/progretech-llc/ProgreTech-Seesaw/issues) · [Contribute](CONTRIBUTING.md)

Built by **[ProgreTech](https://progretech.com)** · [Download releases](https://github.com/progretech-llc/ProgreTech-Seesaw/releases) · [Technical specification](docs/TECHNICAL_SPEC.md)

A Python-driven, offline slicing workspace for Ubuntu. **Version 0.5.2** supports
software-tested Anycubic Photon Mono 4 resin and Original Prusa i3 MK3S/MK3S+ filament
workflows. Neither printer/material combination is physically qualified by this project.

Choose a printer and compatible material profile, load an STL or the bundled test,
move/rotate/resize it, duplicate and arrange copies, generate supports, slice, inspect
actual layers and export a checksum-verified `.pm4n` or `.gcode` to your USB drive.
Profiles and editable projects are local; resin exposure is intentionally unset until
you supply settings for your exact material. See [the workspace guide](docs/WORKSPACE_0_3.md).
Version 0.3.1 adds zoomed layer findings and an explicit, strictly bounded native
single-pixel repair option; see [repair validation](docs/LAYER_FINDINGS.md).

Experimental local AI tools add image-to-3D reconstruction with separately downloaded
quantized weights, a CUDA-free image-relief fallback, and optional local LLM prompt
intake through OpenClaw + Tailscale. See [setup, tested hardware and limitations](docs/EXPERIMENTAL_GENERATION.md).

PrusaSlicer **2.9.4** is required; Mono 4 additionally requires UVTools core **7.0.1**.
The Ubuntu 26.04 amd64 Seesaw installer includes these engines. The current workspace handles one source STL
with up to 32 independently transformed instances. Resin jobs remain limited to 512
layers and a conservative RAM admission check. Smart slice tries a bounded set of
orientations with native supports; native hollow/drain editing is available. Start hardware
testing with calibration geometry.
See [backend validation](docs/BACKEND_VALIDATION.md).

The intended production pipeline is:

```text
STL → PrusaSlicer (geometry, supports, layer rasterization)
    → SL1 layer archive → UVTools (inspection and encoding)
    → validated PM4N → USB → Photon Mono 4
```

UVTools' documented PrusaSlicer integration consumes **sliced SL1 archives**, not an
STL-to-printer workflow. The reviewed UVTools v7.0.1 source includes `.pm4n` support.
The backend pairing has software integration evidence; physical-printer testing remains open.
See the [technical specification](docs/TECHNICAL_SPEC.md),
[backend evidence](docs/BACKEND_REVIEW.md) and [milestones](docs/ROADMAP.md).

## Development setup

For the Ubuntu launcher installation, use the `.deb` from
[GitHub Releases](https://github.com/progretech-llc/ProgreTech-Seesaw/releases).
See [installation and manual self-updates](docs/INSTALLATION.md).
The desktop's **Check for updates** button checks published releases only when clicked.
The installer includes the Python desktop runtime. Native slicer engines are separate prerequisites.

Python 3.12 is the reference interpreter. Core dependencies are locked in `uv.lock`;
the desktop uses PySide6 and PyVista/VTK. Setup downloads dependencies; application
operation has been tested with networking disabled. A complete air-gapped engine/dependency bundle is a later milestone.

```bash
uv sync --python 3.12 --extra desktop --extra dev --locked
uv run seesaw-desktop
uv run seesaw doctor
uv run seesaw inspect /path/to/model.stl
uv run pytest -q
uv run ruff check .
```

Open this directory in PyCharm and select `.venv/bin/python` as the interpreter.
Create a Python run configuration with module `seesaw.app` and this directory as its
working directory. OpenGL/EGL and the appropriate Qt platform libraries must be
available for the desktop renderer; see [validation](docs/VALIDATION.md).

For core-only development: `uv sync --python 3.12 --extra dev --locked`.
`seesaw doctor` only checks PATH; it does not certify installed versions or detect
Flatpak packages. `seesaw plan` prints a research command sequence without executing it:

```bash
uv run seesaw plan /path/to/model.stl --profile /path/to/trusted-sla.ini --work-dir /path/to/scratch
```

That plan is not a production export command. The profile must include complete
SLA settings; printer dimensions alone are insufficient. Never execute untrusted
post-processing embedded in imported profiles.

## Product direction

- One local desktop workflow: Add model → Prepare → Preview → Export.
- Reuse PrusaSlicer and UVTools before creating custom geometry or encoder code.
- Benchmark mslicer behind an optional adapter after the reference path is correct.
- No account, telemetry, cloud slicing, runtime update checks or model downloads.
- Later: optional local image/text-to-3D orchestration with per-model hardware checks;
  no bundled weights and no CUDA requirement for ordinary slicing.

Initial material: **Anycubic clear water-washable resin**. Exact product variant,
exposure, temperature and motion settings are not yet qualified.

## License and upstream work

Seesaw code is AGPL-3.0-only; see [LICENSE](LICENSE).
Prusa printer/PLA settings are bundled with attribution; slicer engines remain separate.
The unchanged bundled ctrlV test model has its own CC BY-ND license; see its packaged
`assets/test-model/ATTRIBUTION.txt`. The archive does not identify a license version.
PrusaSlicer and UVTools use AGPL-3.0 license texts; mslicer uses GPL-3.0.
See [third-party notices](docs/THIRD_PARTY.md) before distributing backend binaries.

The UI direction follows the owner's supplied classroom-slicer reference: clear
steps, a large model view, model tools at left, printer/material setup at right and
plain-language status below. Resin terminology replaces the reference's filament controls.

## Collaboration

Reproducible bug reports, platform compatibility, installation documentation, and small regression fixes are useful ways to help. Read [CONTRIBUTING.md](CONTRIBUTING.md) for issue reports, proposed changes, and attribution requirements.

## License and reuse

The repository includes AGPL-3.0 terms in [LICENSE](LICENSE). Preserve applicable copyright and license notices. Consult the full license for modification, distribution, and any source-provision requirements.

Preserve the upstream and test-model notices described in the existing “License and upstream work” section. The bundled model and printer profiles have separate terms; application licensing does not replace them.

## More from ProgreTech

Explore [CodeSeal](https://codeseal.progretech.com) for signed software provenance and project history.

Discover the wider portfolio at [progretech.com](https://progretech.com). These links identify related products; they do not imply a bundled integration or shared license.
