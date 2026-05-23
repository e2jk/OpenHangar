#!/usr/bin/env python3
"""
Reads GitHub Container Registry package-versions JSON from stdin and
prints the next SemVer build version.

--bump minor (default): MAJOR.(MINOR+1).0  — app/ was changed since last release
                        promotes to (MAJOR+1).0.0 when MINOR+1 would reach 100
--bump patch:           MAJOR.MINOR.(PATCH+1) — only non-app changes

Falls back to 0.1.0 if no semver tags are found.
"""

import argparse
import json
import re
import sys

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _highest_semver(data: object) -> tuple[int, int, int] | None:
    if not isinstance(data, list):
        return None
    best: tuple[int, int, int] | None = None
    for version in data:
        for tag in version.get("metadata", {}).get("container", {}).get("tags", []):
            m = _SEMVER.match(tag)
            if m:
                t = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if best is None or t > best:
                    best = t
    return best


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bump",
        choices=["minor", "patch"],
        default="minor",
        help="Which component to increment: 'minor' when app/ changed, 'patch' otherwise (default: minor)",
    )
    args = parser.parse_args()

    try:
        data = json.load(sys.stdin)
    except Exception:
        data = None
    best = _highest_semver(data)
    if best is None:
        print("0.1.0")
    elif args.bump == "patch":
        print(f"{best[0]}.{best[1]}.{best[2] + 1}")
    elif best[1] + 1 >= 100:
        print(f"{best[0] + 1}.0.0")
    else:
        print(f"{best[0]}.{best[1] + 1}.0")


if __name__ == "__main__":
    main()
