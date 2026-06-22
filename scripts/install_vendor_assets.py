#!/usr/bin/env python3
"""
Install vendor frontend assets for OpenHangar.

Runs `npm ci --ignore-scripts` in the requirements directory and copies the
needed files into app/static/vendor/.  Used by the Dockerfile vendor stage
and for local development outside Docker.

Usage (dev — from the project root):
    python scripts/install_vendor_assets.py               # npm ci + copy
    python scripts/install_vendor_assets.py --copy-only   # skip npm, copy from existing node_modules

Usage (Dockerfile vendor stage):
    python3 install_vendor_assets.py \
        --copy-only \
        --requirements-dir /tmp/vendor-build \
        --output-dir /tmp/vendor
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Single source of truth for the file mapping.
# (node_modules relative path, vendor output relative path)
# Add new libraries here — the change applies to both local dev and Docker builds.
_FILES = [
    ("bootstrap/dist/css/bootstrap.min.css", "bootstrap/css/bootstrap.min.css"),
    (
        "bootstrap/dist/js/bootstrap.bundle.min.js",
        "bootstrap/js/bootstrap.bundle.min.js",
    ),
    (
        "bootstrap-icons/font/bootstrap-icons.min.css",
        "bootstrap-icons/font/bootstrap-icons.min.css",
    ),
    (
        "bootstrap-icons/font/fonts/bootstrap-icons.woff2",
        "bootstrap-icons/font/fonts/bootstrap-icons.woff2",
    ),
    (
        "bootstrap-icons/font/fonts/bootstrap-icons.woff",
        "bootstrap-icons/font/fonts/bootstrap-icons.woff",
    ),
    (
        "canvas-confetti/dist/confetti.browser.js",
        "canvas-confetti/confetti.browser.min.js",
    ),
    ("leaflet/dist/leaflet.css", "leaflet/leaflet.css"),
    ("leaflet/dist/leaflet.js", "leaflet/leaflet.js"),
    ("leaflet/dist/images/marker-icon.png", "leaflet/images/marker-icon.png"),
    ("leaflet/dist/images/marker-icon-2x.png", "leaflet/images/marker-icon-2x.png"),
    ("leaflet/dist/images/marker-shadow.png", "leaflet/images/marker-shadow.png"),
    ("leaflet/dist/images/layers.png", "leaflet/images/layers.png"),
    ("leaflet/dist/images/layers-2x.png", "leaflet/images/layers-2x.png"),
    ("qrcodejs/qrcode.min.js", "qrcodejs/qrcode.min.js"),
    ("htmx.org/dist/htmx.min.js", "htmx/htmx.min.js"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--requirements-dir",
        type=Path,
        default=_REPO_ROOT / "requirements",
        help="Directory containing package.json, package-lock.json, and node_modules "
        "(default: requirements/ relative to the repo root)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_REPO_ROOT / "app" / "static" / "vendor",
        help="Destination for vendor files "
        "(default: app/static/vendor relative to the repo root)",
    )
    parser.add_argument(
        "--copy-only",
        action="store_true",
        help="Skip npm ci — copy from an already-installed node_modules",
    )
    args = parser.parse_args()

    requirements_dir = args.requirements_dir
    node_modules = requirements_dir / "node_modules"

    if not args.copy_only:
        print(f"Running npm ci in {requirements_dir} …")
        result = subprocess.run(
            ["npm", "ci", "--ignore-scripts"],
            cwd=requirements_dir,
        )
        if result.returncode != 0:
            sys.exit(result.returncode)

    if not node_modules.exists():
        print(
            f"ERROR: {node_modules} does not exist.\n"
            "Run without --no-install to install first.",
            file=sys.stderr,
        )
        sys.exit(1)

    output_dir = args.output_dir
    print(f"Copying {len(_FILES)} vendor files → {output_dir.resolve()}")
    for src_rel, dest_rel in _FILES:
        dest = output_dir / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(node_modules / src_rel, dest)
        print(f"  {dest_rel}")

    print("\nDone.")


if __name__ == "__main__":
    main()
