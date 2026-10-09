# Auto exposure and test-model diagnostics — 0.5.1

Each exposure field has an **Auto** button. Auto uses the selected local material
profile's saved normal/bottom exposure; the summary displays the effective seconds.
Type a positive number for a manual override. Either field can use Auto independently.
Project save/reopen retains the choices and the material snapshot. Existing projects
migrate with their numeric settings in manual mode; export readiness is never restored.

Choose the exact product on your bottle. Bundled reference profiles for the **Photon
Mono 4** (not Ultra or 4K) include Anycubic Water-Wash Resin+ and Water-Wash Resin 2.0.
These are manufacturer starting values, not calibrated for a particular bottle/color,
printer UV power or temperature. The previously ambiguous clear-water-washable profile
stays unset. **Save profile** stores your calibrated settings for future Auto selection.
An absent profile value blocks slicing rather than inventing a number. Auto is restricted
to the profile's layer height (0.05 mm when unspecified); change to manual exposures
before using another layer height. No automatic scaling or temperature adjustment occurs.
Only reference exposures are preset; motion/rest settings retain existing adapter defaults.

Sources checked 2026-10-09:

- [Anycubic Mono 4 Water-Wash Resin+ table](https://eu.anycubic.com/pages/resin-settings-for-anycubic-photon-series-3d-printer): normal3s, bottom30s. The table does not specify layer height for this row; Seesaw conservatively restricts this reference to its0.05mm baseline, not a manufacturer layer-height claim.
- [Anycubic Water-Wash Resin2.0 manual table](https://store.anycubic.com/pages/resin-user-manual): Mono4,0.05mm, normal2.8s, bottom30s.

**Load resin test** loads Seesaw's small solid12×10×4mm L-shaped geometry fixture.
It checks import/slicing/readback behavior, not exposure calibration or physical printing.
**Load FDM test** retains the original unmodified ctrlV test with its own attribution/license.

The reported ctrlV failure was one286,704-pixel island at native index176 (UI layer177),
with supports disabled. An actual supports-enabled retry still found four islands,
including larger disconnected regions; automatic supports alone did not qualify it.
The ctrlV model is a demanding FDM challenge. Keep the failed result blocked and review
geometry/orientation/supports; exposure changes do not fix digital unsupported islands.
The desktop now names the finding, shows its pixel count and opens Layers at the first
island. One-pixel repair remains explicit and cannot remove these large features. Native
checks and export gates are unchanged.
