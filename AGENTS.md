# ProgreTech Seesaw

Repository: `/mnt/PT-Storage/Projects/development/factory-alt/pt-progretech-seesaw-factory-alt-dev`.
Canonical product memory project: `ProgreTech-Seesaw`; use your runtime identity.
Follow `/mnt/PT-Storage/Projects/AGENTS.md` and use the official Hindsight integration with your assigned identity.

Read `docs/TECHNICAL_SPEC.md`, `docs/BACKEND_REVIEW.md`, `docs/ROADMAP.md` and
`docs/VALIDATION.md` before changing workflow or claiming printer support.

- Preserve unrelated working changes. Keep all project-specific work and outputs in this repository, following its Git conventions. Use `/tmp` for temporary discovery files and remove them after use.
- Do not confuse Mono 4, Mono 4K and Mono 4 Ultra profiles.
- Keep the baseline offline, Python-driven and free of mandatory CUDA/model dependencies.
- Reuse backend algorithms and codecs. Never claim extension renaming is format conversion.
- A successful unit test is not a firmware/physical-print qualification.
- User meshes, raw logs, generated printer files, credentials and model weights stay out of Git.
- Run `uv run pytest -q` and `uv run ruff check .` for core changes; desktop changes
  also need an actual launch/render/import check with the limits recorded.
- Changes to job readiness must preserve fail-closed export and stale-result rejection.

Ed authorizes committing and pushing checked source changes to the canonical company GitHub repository. Verify the live remote commit before reporting completion. Deployments, messages and printer actuation require separate authorization.
