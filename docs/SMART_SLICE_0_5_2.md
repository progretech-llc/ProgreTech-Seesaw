# Smart slice — Seesaw 0.5.2

For Mono 4 resin jobs, **Smart slice** enables native PrusaSlicer supports and raft,
tries supported orientations, and validates the actual sliced layers before adopting
one. Use your exact printer/material settings first. Smart slice preserves exposures,
layer height, model scale, XY placement, copies, material and source-anchored drains.
It does not choose a calibrated exposure for an unknown resin.

The bounded search tries the current orientation, then adds +5/-5 degrees around X,
+5/-5 around Y, +10/-10 around X, +10/-10 around Y, and +35 around X. Each copy
receives the same rotation delta relative to its own orientation and remains grounded.
Candidates outside the bed or the existing 512-layer limit are skipped. The algorithm
never reduces scale or changes layer height to fit. Attempts are sequential with a
20 minute overall budget, the existing native stage timeouts, RAM admission check and
owned-process cancellation. A backend or source-integrity fault stops the search.

Native UVTools island inspection rejects preparations containing structural islands
before encoding. Only the existing bounded repair of at most 64 isolated one-pixel
artifacts is allowed: native repair must remove exactly those pixels, and every layer
is checked for other changes. Larger islands are not erased. Passing this preflight
still requires the complete PM4N settings, layer Z/exposure, readback pixel and issue
checks. Hollow jobs retain their resin-trap and suction-cup checks.

On success the selected tilt and support/repair settings appear in the project, the
Layers tab shows the validated result, and Undo restores the previous geometry and
preparation settings while clearing export readiness. Saving preserves the selected
preparation; reopening still requires a new validated slice. Input changes or Cancel
prevent adoption. Failed searches leave the original project unchanged, keep export
blocked and retain `smart-report.json` plus per-attempt diagnostics in Job details.
Early rejected attempts can be inspected from their native SL1 layers; those are not
validated PM4N exports. Supports appear in sliced layers, not the source mesh viewport.

## Software acceptance

The owner's original unmodified `ctrlV_3D_test.stl` challenge failed with supports off.
A supports-only retry at the original orientation also left large islands. A native
5 degree X tilt with supports at **0.05 mm**, 2.8 s normal and 30 s bottom exposure
passed 449 layers, with four exact isolated pixel removals and zero remaining native
issues. Those exposure values are software-test inputs, not bottle calibration.
The challenge asset remains unmodified and retains its upstream attribution/license.

Acceptance records live under
`/mnt/pt-context/job-artifacts/seesaw-smart-slice-20261009/`:
`probe-5-result.json`, `desktop/result.json` and per-attempt native logs. The desktop
check clicks the actual button, verifies original-orientation rejection and tilt
adoption, saves/reopens the selected project, inspects native readback, and exercises
Undo with readiness invalidation. Qt widget grabs and VTK framebuffers are separate
captures under X11/XWayland using host OS libraries. Core tests cover retry bounds,
copy/settings preservation, cancellation, deadlines, backend faults, result mismatch,
all-failed searches, layer-cap skips and preflight rejection before encoding.

This is a bounded preparation search, not a guarantee for arbitrary geometry or a
physical printer qualification. Review the generated layers and supports before
exporting and printing. The one-app native engine payload is unchanged from 0.5.0;
no new tool, cloud service, GPU, account or model download is required.
