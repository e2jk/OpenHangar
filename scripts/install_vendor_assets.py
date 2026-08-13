#!/usr/bin/env python3
"""
Install vendor frontend assets for OpenHangar.

Runs `npm ci --ignore-scripts` and copies the needed files into
app/static/vendor/.  Used by the Dockerfile vendor stage and for local
development outside Docker.

`npm ci` runs inside the same pinned node:*-alpine image the Dockerfile's
vendor stage uses (read straight from docker/Dockerfile, so the two can't
drift apart) rather than requiring Node/npm on the host — every developer is
already expected to have Docker installed to run the project itself. Pass
--host-npm to use a local npm install instead (e.g. if Docker isn't
available, or Docker-in-Docker isn't possible in some CI/sandbox setups).

Usage (dev — from the project root):
    python scripts/install_vendor_assets.py               # npm ci (via Docker) + copy
    python scripts/install_vendor_assets.py --copy-only   # skip npm, copy from existing node_modules
    python scripts/install_vendor_assets.py --host-npm    # npm ci using the host's own npm

Usage (Dockerfile vendor stage):
    python3 install_vendor_assets.py \
        --copy-only \
        --requirements-dir /tmp/vendor-build \
        --output-dir /tmp/vendor
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOCKERFILE = _REPO_ROOT / "docker" / "Dockerfile"

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


def _vendor_stage_node_image() -> str:
    """Read the pinned `node:*-alpine` image off the Dockerfile's vendor
    stage, so this script and the Docker build always use the same digest —
    Renovate bumps the Dockerfile, this just follows along."""
    dockerfile_text = _DOCKERFILE.read_text()
    match = re.search(r"^FROM (node:\S+) AS vendor", dockerfile_text, re.MULTILINE)
    if not match:
        print(
            f"ERROR: could not find a 'FROM node:... AS vendor' line in {_DOCKERFILE}",
            file=sys.stderr,
        )
        sys.exit(1)
    return match.group(1)


def _run_npm_ci_via_docker(requirements_dir: Path) -> int:
    image = _vendor_stage_node_image()
    print(f"Running npm ci via Docker ({image}) in {requirements_dir} …")
    docker_cmd = [
        "docker",
        "run",
        "--rm",
        # Run as the host uid/gid so node_modules ends up owned by the
        # invoking user, not root — and point HOME at /tmp (world-writable
        # in the base image) since an arbitrary numeric uid has no
        # /etc/passwd entry, so npm can't resolve a real home directory to
        # place its cache in.
        "-e",
        "HOME=/tmp",
        "-v",
        f"{requirements_dir.resolve()}:/work",
        "-w",
        "/work",
        image,
        "npm",
        "ci",
        "--ignore-scripts",
    ]
    if hasattr(os, "getuid"):
        docker_cmd[3:3] = ["--user", f"{os.getuid()}:{os.getgid()}"]
    try:
        return subprocess.run(docker_cmd, check=False).returncode
    except FileNotFoundError:
        print(
            "ERROR: docker not found.\n"
            "install_vendor_assets.py runs npm inside a container by default "
            "so developers don't need Node/npm installed directly — install "
            "Docker (see docs/development.md), or pass --host-npm to use a "
            "local npm install instead.",
            file=sys.stderr,
        )
        sys.exit(1)


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
    parser.add_argument(
        "--host-npm",
        action="store_true",
        help="Run npm ci with the host's own npm instead of the pinned "
        "node:*-alpine Docker image (requires Node/npm installed locally)",
    )
    args = parser.parse_args()

    requirements_dir = args.requirements_dir
    node_modules = requirements_dir / "node_modules"

    if not args.copy_only:
        if args.host_npm:
            print(f"Running npm ci in {requirements_dir} …")
            try:
                returncode = subprocess.run(
                    ["npm", "ci", "--ignore-scripts"],
                    cwd=requirements_dir,
                    check=False,  # returncode checked explicitly below
                ).returncode
            except FileNotFoundError:
                print(
                    "ERROR: npm not found. Install Node/npm, or drop "
                    "--host-npm to run npm ci via Docker instead.",
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            returncode = _run_npm_ci_via_docker(requirements_dir)
        if returncode != 0:
            sys.exit(returncode)

    if not node_modules.exists():
        print(
            f"ERROR: {node_modules} does not exist.\n"
            "Run without --copy-only to install first.",
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
