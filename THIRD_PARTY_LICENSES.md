# Third-party licenses

OpenHangar itself is [MIT-licensed](LICENSE). This file documents the small
set of runtime dependencies whose own licenses carry copyleft terms (GPL,
LGPL) or a content-specific license (OFL), and the reasoning for why using
them here doesn't put any copyleft obligation on OpenHangar's own code.

This file is not legal advice. It's a good-faith compliance record — what's
used, under what license, and why we believe it's fine — written so the
reasoning is on record rather than assumed.

## pyphen (hyphenation) — LGPL-2.1, by election

`weasyprint` (used to generate the AMP PDF export, `app/maintenance/routes.py`)
has a hard, non-optional runtime dependency on
[pyphen](https://pypi.org/project/Pyphen/) for text hyphenation.

pyphen's own code is **tri-licensed: GPL-2.0 OR LGPL-2.1 OR MPL-1.1** — a
choice, not a single mandatory license. As the downstream user we elect the
**LGPL-2.1** option, which is written specifically to allow combining an
LGPL library with differently-licensed code (including permissive/MIT code)
without the combined work becoming LGPL, provided that:

1. **Notice is given** that the library is used and under what license, with
   the license text available. *This file is that notice.* pyphen's full
   license text is available at
   <https://github.com/Kozea/Pyphen/blob/master/LICENSE.GPLv2.txt> (and the
   LGPL/MPL alternatives alongside it in the same repo).
2. **The library's own source is available.** It is, unmodified, and
   `requirements/runtime.txt` in this repo pins the exact version with a
   SHA-256 hash — a stronger, more verifiable disclosure of what's actually
   shipped than LGPL requires.
3. **The library can be replaced/modified independently.** It's a normal pip
   dependency, not statically vendored — anyone building this image can
   substitute a different pyphen build without touching OpenHangar's own
   code.

None of this requires OpenHangar's own source to be licensed under
GPL/LGPL/MPL — that's the entire point of LGPL's design, and why it's
already on this project's accepted-license list (`LGPL-3.0-or-later`, see
`.github/workflows/ci.yml`'s `dependency-review` job) for exactly this kind
of library-dependency usage.

pyphen also bundles hyphenation dictionary *data* files (not code) for many
languages, inherited from LibreOffice's dictionaries project, under a mix of
GPL/LGPL/MPL licenses per language — this is what
`actions/dependency-review-action` actually flags (it reports the union of
every license found in the package). OpenHangar's AMP export never invokes
non-English hyphenation, so none of that per-language dictionary code path
is ever exercised — but even setting that aside, distributing an unmodified
data file alongside a program is a substantially weaker copyleft trigger
than importing GPL *code* (data isn't a "program" in the sense GPL's
derivative-work analysis concerns itself with), and isn't the basis of the
election above regardless — the election is made against pyphen's own
top-level code license, which is where the actual "do we link against GPL
code" question lives.

## fonttools — mostly MIT, one unused bundled test font

`weasyprint` also depends on `fonttools[woff]` for font processing.
fonttools' own code is MIT-licensed. The license scan additionally reports
an OFL-1.1 (SIL Open Font License) component — this is a font-specific
license restricting *redistributing that specific font*, not a software
copyleft license, and it's almost certainly a font bundled in fonttools'
own test suite as a test fixture. It's never loaded or executed by anything
in this repo — WeasyPrint uses fonttools' library code, never its test
assets — and OFL doesn't attach any obligation to software that merely
depends on a package containing an OFL font it doesn't use.

## Native libraries in the Docker image (apk layer)

`docker/Dockerfile`'s runtime stage installs `pango` (for WeasyPrint's PDF
rendering) via Alpine's package manager, which pulls in `cairo` and `glib`
as transitive dependencies. Both are **LGPL-2.1-or-later**. The same
LGPL analysis as pyphen above applies and is satisfied the same way: used
as unmodified, independently-replaceable system libraries (Alpine's own
official packages, not vendored or modified), dynamically loaded by
WeasyPrint via `cffi` at runtime — not statically linked into anything
OpenHangar ships.

The image also includes **`bash`** (GPL-3.0-or-later), used only as the
shebang interpreter for `docker/docker-entrypoint.sh`, invoked as a
separate OS process by the container runtime. Running a GPL-licensed
program as a subprocess — rather than importing/linking its code into your
own process — is the "mere aggregation" case both GPL and LGPL explicitly
carve out as not creating a combined/derivative work. The same applies to
`postgresql18-client`'s `pg_dump`/`psql` binaries, invoked as separate
processes for backup/restore, never imported as a library.

Alpine's own `apk` package manager doesn't publish machine-readable SPDX
license metadata for its packages (verified directly:
`apk info -a <pkg> | grep license` returns nothing for any package checked),
so `actions/dependency-review-action` — which reads pip/npm manifests it has
a dependency graph for — has no visibility into anything installed via
`apk add` in the Dockerfile at all. That's a genuine blind spot in what CI
enforces, not evidence that these packages are license-clean; this section
exists so the same disclosure applies to them as to the pip-layer
dependencies above, on the same reasoning.
