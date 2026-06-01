#!/usr/bin/env python3
"""
Download and verify frontend vendor assets for OpenHangar.

Populates app/static/vendor/ with pinned, hash-verified copies of Bootstrap,
Bootstrap Icons, Leaflet, qrcodejs, and canvas-confetti so no CDN is needed
at runtime.

Usage:
    python scripts/fetch_vendor_assets.py              # project root (dev)
    python scripts/fetch_vendor_assets.py --output-dir /app/static/vendor  # Dockerfile

The script is idempotent: files already present with a matching hash are skipped.
All hashes are SHA-384 encoded as standard base64 (same format as HTML SRI).

Run scripts/check_vendor_updates.py to check for newer library versions and
update this file automatically.
"""

import argparse
import base64
import hashlib
import sys
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Package manifest — managed by scripts/check_vendor_updates.py
#
# Each entry: npm package name, pinned version, CDN base URL template (with
# {v} placeholder), and a list of (local_dest, cdn_relative_path, sha384).
#
# Local paths are version-agnostic (vendor/bootstrap/css/...). The version
# lives here only, so automated updates touch one file instead of all templates.
# ---------------------------------------------------------------------------
# BEGIN_PACKAGES
_PACKAGES = [
    {
        "npm": "bootstrap",
        "version": "5.3.8",
        "cdn_base": "https://cdn.jsdelivr.net/npm/bootstrap@{v}",
        "files": [
            (
                "bootstrap/css/bootstrap.min.css",
                "dist/css/bootstrap.min.css",
                "sRIl4kxILFvY47J16cr9ZwB07vP4J8+LH7qKQnuqkuIAvNWLzeN8tE5YBujZqJLB",
            ),
            (
                "bootstrap/js/bootstrap.bundle.min.js",
                "dist/js/bootstrap.bundle.min.js",
                "FKyoEForCGlyvwx9Hj09JcYn3nv7wiPVlz7YYwJrWVcXK/BmnVDxM+D2scQbITxI",
            ),
        ],
    },
    {
        "npm": "bootstrap-icons",
        "version": "1.13.1",
        "cdn_base": "https://cdn.jsdelivr.net/npm/bootstrap-icons@{v}",
        "files": [
            (
                "bootstrap-icons/font/bootstrap-icons.min.css",
                "font/bootstrap-icons.min.css",
                "CK2SzKma4jA5H/MXDUU7i1TqZlCFaD4T01vtyDFvPlD97JQyS+IsSh1nI2EFbpyk",
            ),
            (
                "bootstrap-icons/font/fonts/bootstrap-icons.woff2",
                "font/fonts/bootstrap-icons.woff2",
                "xEoI56EFpIZiDZZKBZxsn3gO3u/FvXtOpHbtkMWmSdfzDw3x9XdVc3i70O9hm4SC",
            ),
            (
                "bootstrap-icons/font/fonts/bootstrap-icons.woff",
                "font/fonts/bootstrap-icons.woff",
                "IYfD9pNP/nesQsPyYtTdGCb4uhEWUmNF8GxaCvqcJFH+Of3c1b0VbH6hdHUonDSC",
            ),
        ],
    },
    {
        "npm": "leaflet",
        "version": "1.9.4",
        "cdn_base": "https://unpkg.com/leaflet@{v}",
        "files": [
            (
                "leaflet/leaflet.css",
                "dist/leaflet.css",
                "sHL9NAb7lN7rfvG5lfHpm643Xkcjzp4jFvuavGOndn6pjVqS6ny56CAt3nsEVT4H",
            ),
            (
                "leaflet/leaflet.js",
                "dist/leaflet.js",
                "cxOPjt7s7Iz04uaHJceBmS+qpjv2JkIHNVcuOrM+YHwZOmJGBXI00mdUXEq65HTH",
            ),
            (
                "leaflet/images/marker-icon.png",
                "dist/images/marker-icon.png",
                "wg83fCOXjBtqzFAWhTL9Sd9vmLUNhfEEzfmNUX9zwv2igKlz/YQbdapF4ObdxF+R",
            ),
            (
                "leaflet/images/marker-icon-2x.png",
                "dist/images/marker-icon-2x.png",
                "bDEa1RhAAKIr/VQnMZ7gUhhXwmKYB4V0g8AsxOvCEPwGxfHCUEzAEMAEEzkjuxiA",
            ),
            (
                "leaflet/images/marker-shadow.png",
                "dist/images/marker-shadow.png",
                "dB8ivfvPGb1MSIzX8oWTakCxmq+VwqP/QL1TX4jT4INR3pM5T4FgF3Gx4mN3NTMq",
            ),
            (
                "leaflet/images/layers.png",
                "dist/images/layers.png",
                "80x85ZS+G189o0xL8E8D7BnfhuNss6EwUPHzG7e+qByRD2xnpxikZ6UQU4Re5nNy",
            ),
            (
                "leaflet/images/layers-2x.png",
                "dist/images/layers-2x.png",
                "+F2ZWK/HTpkV9kN2HnMGCQOTM/cnQJLs770FLOeHznwVWRfDESI8z4JwcGYmy2Au",
            ),
        ],
    },
    {
        "npm": "qrcodejs",
        "version": "1.0.0",
        "cdn_base": "https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/{v}",
        "files": [
            (
                "qrcodejs/qrcode.min.js",
                "qrcode.min.js",
                "3zSEDfvllQohrq0PHL1fOXJuC/jSOO34H46t6UQfobFOmxE5BpjjaIJY5F2/bMnU",
            ),
        ],
    },
    {
        "npm": "canvas-confetti",
        "version": "1.9.4",
        "cdn_base": "https://cdn.jsdelivr.net/npm/canvas-confetti@{v}",
        "files": [
            (
                "canvas-confetti/confetti.browser.min.js",
                "dist/confetti.browser.min.js",
                "JSZXO0kKYHTylAsDYTb+7Kg2eUyalm19b8Pydcdf8sQ1cCKYZr9lLahoKT9+LFY5",
            ),
        ],
    },
]
# END_PACKAGES

ASSETS = [
    (dest, f"{pkg['cdn_base'].format(v=pkg['version'])}/{cdn_path}", sha384)
    for pkg in _PACKAGES
    for dest, cdn_path, sha384 in pkg["files"]
]


def _sha384_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha384(data).digest()).decode()


def _download(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "OpenHangar-asset-fetcher/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return resp.read()


def fetch_all(output_dir: Path, *, recompute: bool = False) -> bool:
    """Download and verify all assets. Returns True if all succeeded."""
    ok = True
    for rel, url, expected_b64 in ASSETS:
        dest = output_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.exists() and not recompute:
            actual = _sha384_b64(dest.read_bytes())
            if actual == expected_b64:
                print(f"  ok  {rel}")
                continue
            print(f"  HASH MISMATCH — re-downloading  {rel}")

        print(f"  dl  {rel}", end="", flush=True)
        try:
            data = _download(url)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            ok = False
            continue

        actual = _sha384_b64(data)
        if actual != expected_b64:
            print(
                f"\n  HASH MISMATCH for {rel}\n"
                f"    expected: {expected_b64}\n"
                f"    got:      {actual}"
            )
            ok = False
            continue

        dest.write_bytes(data)
        print("  ✓")

    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default="app/static/vendor",
        help="Directory to write vendor assets into (default: app/static/vendor)",
    )
    parser.add_argument(
        "--recompute",
        action="store_true",
        help="Re-download and re-verify all files even if they already exist",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    print(f"Fetching vendor assets → {output_dir.resolve()}")

    if not fetch_all(output_dir, recompute=args.recompute):
        print("\nOne or more assets failed. See errors above.")
        sys.exit(1)

    print(f"\nAll {len(ASSETS)} assets verified.")


if __name__ == "__main__":
    main()
