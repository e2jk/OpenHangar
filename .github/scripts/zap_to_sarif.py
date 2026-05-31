"""Convert a ZAP JSON report to SARIF 2.1.0 for upload to GitHub Security tab.

ZAP is a DAST tool — it scans HTTP URLs, not source files. GitHub Code Scanning
requires a physicalLocation in every result. We map scanned URLs to the most
semantically appropriate source file:
  - /static/...  →  app/static/... (the actual static file)
  - everything else  →  app/init.py (where _security_headers() configures CSP/headers)

The scanned URL is preserved in logicalLocations for context.

Rules marked IGNORE in .zap/rules.tsv are excluded from the SARIF output so
they do not clutter the GitHub Security tab.

Usage:
    python3 zap_to_sarif.py [zap_json] [output_sarif] [rules_tsv]

Defaults: report_json.json → zap-results.sarif  (rules from .zap/rules.tsv)
"""

import json
import os
import re
import sys
from urllib.parse import urlparse

_RISK_TO_LEVEL = {0: "note", 1: "note", 2: "warning", 3: "error"}

_FALLBACK_FILE = "app/init.py"


def _first_url(html_text: str) -> str:
    """Extract the first plain URL from ZAP's HTML-encoded reference field."""
    urls = re.findall(r"https?://[^\s<>\"']+", html_text)
    return urls[0] if urls else "https://www.zaproxy.org/"


def _url_to_file(url: str) -> str:
    """Map a scanned HTTP URL to a repo-relative source path for physicalLocation."""
    path = urlparse(url).path.lstrip("/")
    if path.startswith("static/"):
        return f"app/{path}"
    return _FALLBACK_FILE


def _load_ignored_rules(rules_tsv: str) -> set[str]:
    """Return the set of rule IDs marked IGNORE in a ZAP rules.tsv file."""
    ignored: set[str] = set()
    if not os.path.exists(rules_tsv):
        return ignored
    with open(rules_tsv) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1].strip().upper() == "IGNORE":
                ignored.add(parts[0].strip())
    return ignored


def convert(zap_json_path: str, sarif_path: str, rules_tsv: str) -> None:
    ignored = _load_ignored_rules(rules_tsv)

    with open(zap_json_path) as f:
        zap = json.load(f)

    rules: list[dict] = []
    results: list[dict] = []
    seen_rules: set[str] = set()
    skipped = 0

    for site in zap.get("site", []):
        for alert in site.get("alerts", []):
            rule_id = str(alert.get("pluginid", "0"))
            if rule_id in ignored:
                skipped += len(alert.get("instances", []))
                continue

            risk = int(alert.get("riskcode", "0"))
            level = _RISK_TO_LEVEL.get(risk, "note")

            if rule_id not in seen_rules:
                seen_rules.add(rule_id)
                rules.append(
                    {
                        "id": rule_id,
                        "name": alert.get("name", ""),
                        "shortDescription": {"text": alert.get("name", "")},
                        "fullDescription": {"text": alert.get("desc", "")[:1000]},
                        "helpUri": _first_url(alert.get("reference", "")),
                        "defaultConfiguration": {"level": level},
                    }
                )

            for inst in alert.get("instances", []):
                url = inst.get("uri", site.get("@name", ""))
                evidence = inst.get("evidence", "")
                msg = alert.get("desc", "")
                if evidence:
                    msg = f"{msg}\n\nEvidence: {evidence}"
                results.append(
                    {
                        "ruleId": rule_id,
                        "level": level,
                        "message": {"text": msg[:1000]},
                        "locations": [
                            {
                                # physicalLocation required by GitHub Code Scanning.
                                # Map URL to nearest source file; see _url_to_file().
                                "physicalLocation": {
                                    "artifactLocation": {
                                        "uri": _url_to_file(url),
                                        "uriBaseId": "%SRCROOT%",
                                    },
                                    "region": {"startLine": 1},
                                },
                                # logicalLocations preserves the actual scanned URL.
                                "logicalLocations": [
                                    {"fullyQualifiedName": url, "kind": "url"}
                                ],
                            }
                        ],
                    }
                )

    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "ZAP",
                        "version": "stable",
                        "informationUri": "https://www.zaproxy.org/",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }

    with open(sarif_path, "w") as f:
        json.dump(sarif, f, indent=2)

    print(
        f"Converted {len(results)} ZAP instance(s) across {len(rules)} rule(s) → {sarif_path}"
        + (f"  ({skipped} suppressed by rules.tsv)" if skipped else "")
    )


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "report_json.json"
    dst = sys.argv[2] if len(sys.argv) > 2 else "zap-results.sarif"
    tsv = sys.argv[3] if len(sys.argv) > 3 else ".zap/rules.tsv"
    convert(src, dst, tsv)
