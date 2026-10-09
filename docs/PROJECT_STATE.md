# Project persistence and job currency

The Python core now stores a single local STL reference, SHA-256, immutable transform,
explicit resin or filament settings, printer identity/revision and an immutable material
profile snapshot and typed hollow/drain parameters in version-5 JSON. Up to 31 additional independently transformed
copies share the source hash. Version-1/2/3 projects migrate with hollowing disabled; reopening never
restores export readiness. The 0.5.2 desktop uses this API throughout.

`Project.from_stl_path(path, settings=...)` records source identity; geometry inspection
still belongs to `model.load_stl`. `save_project` writes a unique temporary file, flushes
it and atomically replaces the destination. It refuses STL destinations. `load_project`
limits JSON size, rejects duplicate/unknown fields and unsupported schemas, validates
types/settings, and rejects missing or changed source files. Models remain external
references; project files do not embed or relocate them. The 256 MiB STL and 64 KiB
project limits bound reads. No arbitrary backend configuration or executable fields
are accepted. Project files include absolute local source paths and should be treated
as local documents when sharing.

Transforms describe translation in millimetres, rotation in degrees, and uniform scale.
The geometry module applies the transform to an independent mesh copy. Desktop slicing permits XY movement, rotation and uniform scale independently for
each copy. XY zero is the bed center; rotated geometry is grounded on Z=0. Explicit
Z translation is rejected. Native center coordinates preserve world placement.
A saved setting is not a calibrated material preset.

`project.edited(...)` creates a new revision. `JobGate.begin(project)` produces a unique
input snapshot; `finish(project, snapshot)` rejects stale or superseded completion.
`invalidate()` clears active/completed state. Source changes also invalidate currency.
Undo must create a new revision. Use source checks in an appropriate worker for large
meshes, not repeatedly inside painting callbacks.

The gate proves input currency only. It does **not** validate a printer artifact,
qualify resin/firmware, or authorize export. Reopened projects never restore job state.
The desktop additionally requires the pipeline's readback and issue checks before offering a clearly labelled hardware-test candidate.

Validation includes settings round trips, malformed inputs, source tamper/missing
checks, atomic-save failure, source overwrite prevention, fingerprints, stale job
completion, invalidation and reopen without readiness. Desktop integration evidence and limitations are recorded in `DESKTOP_WORKFLOW.md`.

Single-pixel repair is a boolean preparation choice in project schema version3. It is
disabled on migration, rejected for FDM, and included in job currency. See LAYER_FINDINGS.md.

Auto exposure choices are persisted separately from resolved numeric settings. See
[AUTO_EXPOSURE_0_5_1.md](AUTO_EXPOSURE_0_5_1.md) for profile/layer matching and the
resin-specific geometry test. Digital island failures remain export blockers.

Smart slice adopts only a matching, fully validated supported orientation. It first checks
the original input gate, then binds the chosen project to a fresh gate. Undo restores the
previous geometry, exposure settings, support and repair choices and invalidates readiness.
See [Smart slice](SMART_SLICE_0_5_2.md).
