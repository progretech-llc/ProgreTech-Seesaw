# Bundled runtime notices and source access

Seesaw is AGPL-3.0-only. It uses dynamically loaded, unmodified Qt for Python
6.11.2 (PySide6 Essentials and Shiboken), offered under LGPL-3.0, and Qt 6.11.2.
Qt is copyright The Qt Company Ltd. and other contributors. License texts copied
from the exact Qt for Python source archive are installed in
`/usr/share/doc/progretech-seesaw/licenses/qt-for-python/`.

Corresponding upstream source and build instructions are available without charge:

- [Qt for Python / Shiboken 6.11.2 complete source](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/pyside-setup-everywhere-src-6.11.2.tar.xz)
- [Qt 6.11.2 complete source](https://download.qt.io/archive/qt/6.11/6.11.2/single/qt-everywhere-src-6.11.2.tar.xz)
- [Qt for Python build documentation](https://doc.qt.io/qtforpython-6/building_from_source/index.html)
- [Qt Linux source build documentation](https://doc.qt.io/qt-6/linux-building.html)
- [Seesaw source and packaging scripts](https://github.com/progretech-llc/ProgreTech-Seesaw)

These source links must also accompany the binary on each GitHub release page.
Seesaw does not modify those libraries or prevent replacing/debugging them. The
libraries are normal shared objects under `/opt/progretech-seesaw/site/`; use the
source build to replace compatible PySide6/Shiboken/Qt binaries, or rebuild the
package using `tools/build_deb.py`. Reverse engineering for debugging modifications
to LGPL libraries is permitted. No proprietary Seesaw license restricts these rights.

The bundled Python 3.12.14 runtime comes from uv's python-build-standalone distribution;
its Python license files remain in the runtime. Other Python/native dependencies
retain the notices included in their wheel distributions. An installed dependency
inventory and exact wheel requirements are under `/usr/share/doc/progretech-seesaw/`.
No PrusaSlicer, UVTools, mslicer or model weights are bundled in release 0.1.0.

## Release 0.5 engine bundle

PrusaSlicer 2.9.4 is built from exact Ubuntu `slic3r-prusa` 2.9.4+dfsg-4 source
with its distro patches, native algorithms unchanged, GUI and optional STEP import
disabled. The supported relative resources layout is bundled; no binary patch is
used. Its AGPL license/copyright and exact corresponding source accompany the
installer. The official UVTools 7.0.1 Linux-x64 archive is pinned by its published
SHA256; its .NET 10.0.12 runtime and codec files are preserved unchanged. The optional
LTTng native tracing provider is excluded because it targets an incompatible old
UST ABI. Normal conversion/readback does not use that tracing feature. This is a
repackaged deployment subset, not a byte-identical upstream archive.

The installed engine manifest records all files, original archive hash, excluded
optional file hash, native ELF libraries, exact Ubuntu binary/source versions,
notices and source-access URLs. `tools/fetch_engine_sources.py` retrieves exact
corresponding Ubuntu source packages and verifies each `.dsc` SHA256 inventory;
these accompany the release source bundle. Microsoft runtime licenses/third-party
notices are included; [runtime source](https://github.com/dotnet/runtime/tree/v10.0.12)
and [UVTools source](https://github.com/sn4k3/UVtools/tree/v7.0.1) remain available.
See `SINGLE_APP_0_5.md` for reproducible native/bundle build instructions.
