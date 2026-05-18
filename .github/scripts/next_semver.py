#!/usr/bin/env python3
"""
Reads GitHub Container Registry package-versions JSON from stdin and
prints the next SemVer build version (MAJOR.MINOR.0).
Falls back to 0.1.0 if no semver tags are found.
"""

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
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = None
    best = _highest_semver(data)
    if best is None:
        print("0.1.0")
    else:
        print(f"{best[0]}.{best[1] + 1}.0")


if __name__ == "__main__":
    main()
