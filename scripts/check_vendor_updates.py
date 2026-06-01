#!/usr/bin/env python3
"""
Check for updated versions of frontend vendor libraries.

Normal mode (no flags): queries the npm registry for every package in
fetch_vendor_assets.py, reports available updates, and exits non-zero if any
newer versions exist. Used by CI to detect drift.

Upgrade mode (--upgrade [pkg] [version]): upgrades one or all packages to the
latest version (or a specific version), downloads all files for the changed
packages, recomputes SHA-384 hashes, rewrites the _PACKAGES block in
fetch_vendor_assets.py, then re-populates app/static/vendor/.

Usage:
    python scripts/check_vendor_updates.py            # report available updates
    python scripts/check_vendor_updates.py --upgrade  # upgrade all to latest
    python scripts/check_vendor_updates.py --upgrade bootstrap          # one pkg, latest
    python scripts/check_vendor_updates.py --upgrade bootstrap 5.3.4   # one pkg, pinned
"""

import argparse
import base64
import hashlib
import importlib.util
import json
import re
import sys
import textwrap
import urllib.request
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).parent.parent
FETCH_SCRIPT = REPO_ROOT / "scripts" / "fetch_vendor_assets.py"
VENDOR_DIR = REPO_ROOT / "app" / "static" / "vendor"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_packages() -> list[dict]:
    spec = importlib.util.spec_from_file_location("fetch_vendor_assets", FETCH_SCRIPT)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod._PACKAGES


def _latest_npm_version(npm_name: str) -> str:
    url = f"https://registry.npmjs.org/{npm_name}/latest"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "OpenHangar-updater/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())["version"]


def _semver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def _bump_type(old: str, new: str) -> str:
    o, n = _semver(old), _semver(new)
    if n[0] != o[0]:
        return "major"
    if n[1] != o[1]:
        return "minor"
    return "patch"


def _sha384_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha384(data).digest()).decode()


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "OpenHangar-updater/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return resp.read()


# ---------------------------------------------------------------------------
# File rewrite
# ---------------------------------------------------------------------------


def _render_packages(packages: list[dict]) -> str:
    """Serialise _PACKAGES back to Python source (the BEGIN_PACKAGES block)."""
    lines = ["# BEGIN_PACKAGES", "_PACKAGES = ["]
    for pkg in packages:
        lines.append("    {")
        lines.append(f'        "npm": {pkg["npm"]!r},')
        lines.append(f'        "version": {pkg["version"]!r},')
        lines.append(f'        "cdn_base": {pkg["cdn_base"]!r},')
        lines.append('        "files": [')
        for dest, cdn_path, sha384 in pkg["files"]:
            lines.append(f"            ({dest!r}, {cdn_path!r},")
            lines.append(f"             {sha384!r}),")
        lines.append("        ],")
        lines.append("    },")
    lines.append("]")
    lines.append("# END_PACKAGES")
    return "\n".join(lines)


def _rewrite_fetch_script(packages: list[dict]) -> None:
    content = FETCH_SCRIPT.read_text()
    new_block = _render_packages(packages)
    content = re.sub(
        r"# BEGIN_PACKAGES\n.*?# END_PACKAGES",
        new_block,
        content,
        flags=re.DOTALL,
    )
    FETCH_SCRIPT.write_text(content)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _fetch_and_hash_package(pkg: dict, new_version: str) -> dict:
    """Download all files for a package at new_version; return updated pkg dict."""
    # Wipe the package's vendor folder so no stale files from the old version survive.
    # The top-level folder is the first path component of the first file's dest.
    pkg_folder = VENDOR_DIR / Path(pkg["files"][0][0]).parts[0]
    if pkg_folder.exists():
        import shutil

        shutil.rmtree(pkg_folder)
        print(f"    rm  {pkg_folder.relative_to(VENDOR_DIR)}/")

    new_files = []
    for dest, cdn_path, _old_hash in pkg["files"]:
        url = f"{pkg['cdn_base'].format(v=new_version)}/{cdn_path}"
        print(f"    dl  {dest}", end="", flush=True)
        data = _download(url)
        sha384 = _sha384_b64(data)
        out = VENDOR_DIR / dest
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        new_files.append((dest, cdn_path, sha384))
        print("  ✓")
    return {**pkg, "version": new_version, "files": new_files}


def check_updates(packages: list[dict]) -> dict[str, tuple[str, str, str]]:
    """Return {npm_name: (current, latest, bump_type)} for packages with updates."""
    updates: dict[str, tuple[str, str, str]] = {}
    for pkg in packages:
        latest = _latest_npm_version(pkg["npm"])
        current = pkg["version"]
        if _semver(latest) > _semver(current):
            updates[pkg["npm"]] = (current, latest, _bump_type(current, latest))
    return updates


def upgrade_packages(
    packages: list[dict],
    target_npm: Optional[str],
    target_version: Optional[str],
) -> tuple[list[dict], list[str]]:
    """
    Upgrade one or all packages. Returns (updated_packages, list_of_changed_npm_names).
    """
    changed: list[str] = []
    result: list[dict] = []

    for pkg in packages:
        npm = pkg["npm"]
        if target_npm and npm != target_npm:
            result.append(pkg)
            continue

        new_ver = target_version or _latest_npm_version(npm)
        if _semver(new_ver) <= _semver(pkg["version"]) and not target_version:
            print(f"  {npm} {pkg['version']} — already up to date")
            result.append(pkg)
            continue
        if _semver(new_ver) == _semver(pkg["version"]):
            print(f"  {npm} {pkg['version']} — already at requested version")
            result.append(pkg)
            continue

        bump = _bump_type(pkg["version"], new_ver)
        print(f"  {npm} {pkg['version']} → {new_ver} ({bump})")
        updated_pkg = _fetch_and_hash_package(pkg, new_ver)
        result.append(updated_pkg)
        changed.append(npm)

    return result, changed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--upgrade",
        nargs="*",
        metavar=("PACKAGE", "VERSION"),
        help=(
            "Upgrade packages. No args: all to latest. "
            "One arg: named package to latest. "
            "Two args: named package to specific version."
        ),
    )
    args = parser.parse_args()

    packages = _load_packages()

    # ── Check-only mode ────────────────────────────────────────────────────
    if args.upgrade is None:
        print("Checking for updates…")
        updates = check_updates(packages)
        if not updates:
            print("All packages up to date.")
            sys.exit(0)
        print(f"\n{len(updates)} update(s) available:\n")
        for npm, (current, latest, bump) in sorted(updates.items()):
            print(f"  {npm:30s} {current:10s} → {latest:10s}  [{bump}]")
        print(
            "\nRun with --upgrade to apply, or "
            "--upgrade <package> [version] for a specific package."
        )
        sys.exit(1)

    # ── Upgrade mode ───────────────────────────────────────────────────────
    upgrade_args = args.upgrade  # list, may be empty / 1 / 2 items

    if len(upgrade_args) > 2:
        parser.error("--upgrade accepts at most two arguments: package [version]")

    target_npm = upgrade_args[0] if upgrade_args else None
    target_version = upgrade_args[1] if len(upgrade_args) == 2 else None

    if target_npm:
        known = {p["npm"] for p in packages}
        if target_npm not in known:
            print(f"Unknown package '{target_npm}'. Known: {', '.join(sorted(known))}")
            sys.exit(1)

    scope = f"'{target_npm}'" if target_npm else "all packages"
    print(
        f"Upgrading {scope}"
        + (f" to {target_version}" if target_version else " to latest")
        + "…\n"
    )

    updated_packages, changed = upgrade_packages(packages, target_npm, target_version)

    if not changed:
        print("\nNo changes.")
        sys.exit(0)

    print(f"\nRewriting {FETCH_SCRIPT.name}…")
    _rewrite_fetch_script(updated_packages)

    summary = ", ".join(
        f"{p['npm']} → {p['version']}" for p in updated_packages if p["npm"] in changed
    )
    print(f"\nDone. Updated: {summary}")
    print(
        textwrap.dedent(f"""
        Next steps:
          git diff scripts/fetch_vendor_assets.py   # review changes
          git add scripts/fetch_vendor_assets.py
          git commit -m "chore(deps): {summary}"
        """).strip()
    )

    # Signal to CI / GitHub Actions that something changed
    sys.exit(1)


if __name__ == "__main__":
    main()
