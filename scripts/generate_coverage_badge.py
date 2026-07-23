"""Generate the README "Coverage" badge: percentage + passing test count.

Replaces genbadge's coverage command, which only ever encodes the
percentage — this badge also shows the number of passing tests after it
(e.g. "100.00% (3309 tests)"), which genbadge has no option for. Replicates
genbadge's own color thresholds and percentage formula (see
genbadge.utils_coverage.CoverageStats upstream) so the badge looks the same
as before, just with more information in the message.

Usage:
    python3 scripts/generate_coverage_badge.py [coverage_xml] [junit_xml] [output_svg]

Defaults: coverage.xml, test-results.xml -> htmlcov/badge.svg
"""

from __future__ import annotations

import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


def _coverage_percent(coverage_xml: str) -> float:
    root = ET.parse(coverage_xml).getroot()
    lines_covered = int(root.get("lines-covered", "0"))
    lines_valid = int(root.get("lines-valid", "0"))
    branches_covered = int(root.get("branches-covered", "0"))
    branches_valid = int(root.get("branches-valid", "0"))
    denom = lines_valid + branches_valid
    if denom == 0:
        return 0.0
    return (lines_covered + branches_covered) / denom * 100


def _color_for(percent: float) -> str:
    if percent < 50:
        return "red"
    if percent < 75:
        return "orange"
    if percent < 90:
        return "green"
    return "brightgreen"


def _passed_test_count(junit_xml: str) -> int:
    root = ET.parse(junit_xml).getroot()
    suite = root.find("testsuite") if root.tag == "testsuites" else root
    assert suite is not None
    tests = int(suite.get("tests", "0"))
    skipped = int(suite.get("skipped", "0"))
    failures = int(suite.get("failures", "0"))
    errors = int(suite.get("errors", "0"))
    return tests - skipped - failures - errors


def main() -> int:
    coverage_xml = sys.argv[1] if len(sys.argv) > 1 else "coverage.xml"
    junit_xml = sys.argv[2] if len(sys.argv) > 2 else "test-results.xml"
    output_svg = sys.argv[3] if len(sys.argv) > 3 else "htmlcov/badge.svg"

    percent = _coverage_percent(coverage_xml)
    passed = _passed_test_count(junit_xml)
    color = _color_for(percent)
    message = f"{percent:.2f}% ({passed} tests)"
    url = (
        "https://img.shields.io/badge/"
        f"{urllib.parse.quote('coverage', safe='')}-"
        f"{urllib.parse.quote(message, safe='')}-{color}"
    )

    # shields.io returns 403 Forbidden for Python's default urllib User-Agent
    # ("Python-urllib/3.x") — any other reasonable one works fine.
    req = urllib.request.Request(url, headers={"User-Agent": "OpenHangar-CI"})
    with urllib.request.urlopen(req) as resp:
        svg = resp.read()
    with open(output_svg, "wb") as f:
        f.write(svg)

    print(f"Wrote {output_svg}: coverage {percent:.2f}%, {passed} passing tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
