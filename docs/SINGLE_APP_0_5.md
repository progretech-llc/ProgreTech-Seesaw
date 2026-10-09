# Seesaw 0.5 single application and hollow/drain preparation

The Ubuntu **26.04 amd64** installer bundles the Python desktop, a source-built
PrusaSlicer **2.9.4** headless engine, UVTools core **7.0.1** with its self-contained
.NET runtime, engine resources and their linked non-glibc libraries. Install one
Seesaw package. No separate Python, PrusaSlicer, UVTools or .NET installation,
account, startup download, CUDA or cloud service is needed for ordinary slicing.
Normal Ubuntu desktop/ABI libraries remain package-manager dependencies; apt can
resolve those automatically. Ubuntu 24.04 is not supported by this build's glibc
ABI. Native Wayland remains unqualified; X11/XWayland is the tested desktop route.

Installed slicing always uses package-owned engines. Missing or damaged bundle
files stop the job with a reinstall message. Development checkouts may discover
local engines; that fallback never runs from the installed desktop. The manifest
records exact payload checksums and versions. This detects accidental corruption,
not a malicious administrator who can replace the application and manifest.

## Hollow and drain

Choose **Hollow and drain…** after importing a model. Enter wall thickness and
one or more holes, then enable hollowing. Hole surface coordinates are in the
**original STL frame, in millimetres**; the direction vector points **into** the
model, radius and depth use millimetres. Rotation, scale, placement and every copy
carry those holes with the mesh. Uniform scaling also scales hole radius/depth;
wall thickness stays the selected physical millimetres. Orange viewport guides
show requested holes; the displayed model remains the original solid input.
Actual hollow geometry and openings appear in sliced layers.

Add/remove holes in the dialog. Undo restores the previous hollow or geometry
edit. Save/reopen retains the typed parameters; old projects migrate with hollowing
disabled. Any edit invalidates old previews/export readiness. An enabled hollow
job needs at least one hole and runs native resin-trap/suction-cup checks before
export. Findings block publication; a visible surface opening alone is insufficient.
The UI does not automatically choose a suitable location or certify fluid flow.
Inspect the layers; firmware, resin exposure and physical drainage need separate
owner-operated qualification. Synthetic test exposures are not recommendations.

Implementation reuses PrusaSlicer's native hollowing and 3MF drain metadata. It
exports a centered merged mesh and rebases holes into the same native object frame,
checks the exporter transformation, and retains existing full PM4N readback,
per-layer settings/pixel validation, bounded subprocess cancellation and stale-job
rejection. No new support algorithm or PM4N encoder was implemented.

## Optional generation

The CPU relief provider remains optional and its lightweight Python dependency is
bundled. Optional reconstruction weights and external agent integrations are not
required for slicing and are not bundled or downloaded on startup. They retain
explicit setup and capability checks described in `EXPERIMENTAL_GENERATION.md`.

## Rebuild and source

Prepare the Ubuntu `slic3r-prusa` **2.9.4+dfsg-4** source from its exact `.dsc`,
original tarball and Debian patch tarball using `dpkg-source -x`. Configure with
CMake/Ninja, Release, `SLIC3R_GUI=OFF`, `SLIC3R_STATIC=OFF`, `SLIC3R_FHS=OFF`,
`SLIC3R_DESKTOP_INTEGRATION=OFF`, `SLIC3R_BUILD_TESTS=OFF` and
`SLIC3R_ENABLE_FORMAT_STEP=OFF`. Build `-j4`. This uses the source's supported
relative `../resources` layout; no binary patch or relocated global resource path
is used. Optional STEP import is outside the Seesaw STL baseline.

`tools/prepare_engine_bundle.py` combines the built binary/resources with the
pinned official UVTools Linux-x64 archive and records its ELF closure and package
provenance/notices. It rejects archive path traversal, unexpected archive hashes,
missing ELF libraries and missing copyright inventory. Pass the resulting directory
to `tools/build_deb.py --engines PATH --work FRESH_BUILD --output DELIVERABLES`.

The accompanying source bundle and manifest include Seesaw, exact engine source
archives, source package metadata, native build recipe and component source-access
references. Qt/Python/wheel notices remain in the installed package. Redistribution
obligations and limitations are documented without claiming universal license
compliance solely from subprocess boundaries or an automated inventory.
