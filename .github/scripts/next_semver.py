#!/usr/bin/env python3
"""
Prints the next SemVer build version, given the highest existing `vX.Y.Z`
git tag (or none at all).

--bump minor (default): MAJOR.(MINOR+1).0  — app/ was changed since last release
                        promotes to (MAJOR+1).0.0 when MINOR+1 would reach 100
--bump patch:           MAJOR.MINOR.(PATCH+1) — only non-app changes

Falls back to 0.1.0 if no semver tag is given.
"""

import argparse
import re

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bump",
        choices=["minor", "patch"],
        default="minor",
        help="Which component to increment: 'minor' when app/ changed, 'patch' otherwise (default: minor)",
    )
    parser.add_argument(
        "--last-tag",
        default="",
        help="Highest existing vX.Y.Z git tag, or empty if none exists yet",
    )
    args = parser.parse_args()

    m = _SEMVER.match(args.last_tag.strip())
    if not m:
        print("0.1.0")
        return

    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if args.bump == "patch":
        print(f"{major}.{minor}.{patch + 1}")
    elif minor + 1 >= 100:
        print(f"{major + 1}.0.0")
    else:
        print(f"{major}.{minor + 1}.0")


if __name__ == "__main__":
    main()
