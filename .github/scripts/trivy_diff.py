#!/usr/bin/env python3
"""
Compares Trivy JSON scan results between a new image and a baseline.
Fails if any HIGH/CRITICAL unfixed CVEs appear in the new image but not
in the baseline. Passes with a notice when no baseline exists.

Environment:
    HAS_BASELINE  set to "true" if a baseline scan is available.
Input files:
    /tmp/trivy-new.json       Trivy JSON output for the new image
    /tmp/trivy-baseline.json  Trivy JSON output for the baseline image
"""

import json
import os
import sys


def get_cves(path: str) -> set[str]:
    data = json.load(open(path))
    cves: set[str] = set()
    for result in data.get("Results", []):
        for vuln in result.get("Vulnerabilities") or []:
            cves.add(vuln["VulnerabilityID"])
    return cves


def main() -> None:
    if os.environ.get("HAS_BASELINE") != "true":
        print(
            "::notice::No published baseline image found — skipping diff (first publish)."
        )
        sys.exit(0)

    new_cves = get_cves("/tmp/trivy-new.json")
    baseline_cves = get_cves("/tmp/trivy-baseline.json")
    introduced = new_cves - baseline_cves

    if introduced:
        print(
            f"::error::Newly introduced HIGH/CRITICAL CVEs: {', '.join(sorted(introduced))}"
        )
        sys.exit(1)

    fixed = baseline_cves - new_cves
    print("No newly introduced vulnerabilities.")
    if fixed:
        print(f"Fixed since baseline: {', '.join(sorted(fixed))}")


if __name__ == "__main__":
    main()
