"""Convert a ZAP JSON report to SARIF 2.1.0 for upload to GitHub Security tab.

ZAP is a DAST tool — it scans HTTP URLs, not source files. GitHub Code Scanning
requires a physicalLocation in every result. We map scanned URLs to the most
semantically appropriate source file:
  - /static/...  →  app/static/... (the actual static file)
  - everything else  →  app/init.py (where _security_headers() configures CSP/headers)

Instances are grouped into one result per (rule, mapped file); the scanned
URLs are listed in the message and preserved in logicalLocations.

Rules marked IGNORE in .zap/rules.tsv are excluded from the SARIF output so
they do not clutter the GitHub Security tab.

Usage:
    python3 zap_to_sarif.py [zap_json] [output_sarif] [rules_tsv] [tool_name]

Defaults: report_json.json → zap-results.sarif  (rules from .zap/rules.tsv,
tool name "ZAP"). ci.yml's per-PR baseline scan and zap-full-scan.yml's
scheduled active scan both call this script but pass distinct tool names
("ZAP Baseline" / "ZAP Full Scan") -- they're different scan configurations
(passive-only on every push vs. an active scan on a weekly schedule) that
never run for the same ref, so sharing one SARIF tool name made GitHub's
code scanning treat them as one tool with an incomplete config on every PR
("1 configuration not found"), not two independently-tracked tools.
"""

import hashlib
import json
import os
import re
import sys
from typing import Any
from urllib.parse import urlparse

_RISK_TO_LEVEL = {0: "note", 1: "note", 2: "warning", 3: "error"}

_FALLBACK_FILE = "app/init.py"

_MAX_MESSAGE = 1000
_MAX_URLS_LISTED = 10


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


def convert(
    zap_json_path: str, sarif_path: str, rules_tsv: str, tool_name: str = "ZAP"
) -> None:
    ignored = _load_ignored_rules(rules_tsv)

    with open(zap_json_path) as f:
        zap = json.load(f)

    rules: list[dict] = []
    results: list[dict] = []
    seen_rules: set[str] = set()
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    instances = 0
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
                group = groups.setdefault(
                    (rule_id, _url_to_file(url)),
                    {
                        "level": level,
                        "desc": alert.get("desc", ""),
                        "urls": set(),
                        "evidence": "",
                    },
                )
                group["urls"].add(url)
                instances += 1
                if not group["evidence"]:
                    group["evidence"] = inst.get("evidence", "")

    # One result per (rule, mapped file), not one per scanned URL. Every
    # non-static URL maps to the same _FALLBACK_FILE line, so per-URL
    # results all shared one primaryLocationLineHash, and upload-sarif
    # disambiguates identical hashes with a running ":N" suffix — one ZAP
    # rule seen on 30 URLs became 30 alerts, renumbered (closed and
    # reopened) whenever crawl order changed. The stable, explicit
    # partialFingerprints below also keep distinct rules on that same line
    # from being numbered against each other.
    for (rule_id, path), group in sorted(groups.items()):
        urls = sorted(group["urls"])
        shown = "\n".join(f"- {u}" for u in urls[:_MAX_URLS_LISTED])
        more = len(urls) - _MAX_URLS_LISTED
        if more > 0:
            shown += f"\n- … and {more} more"
        msg = group["desc"]
        if group["evidence"]:
            msg += f"\n\nEvidence: {group['evidence']}"
        # Truncate the description part only, so the URL list always survives.
        msg = msg[: max(200, _MAX_MESSAGE - len(shown) - 40)]
        msg += f"\n\nAffected URLs ({len(urls)}):\n{shown}"
        fingerprint = hashlib.sha256(f"{rule_id}:{path}".encode()).hexdigest()[:32]
        results.append(
            {
                "ruleId": rule_id,
                "level": group["level"],
                "message": {"text": msg},
                "partialFingerprints": {"primaryLocationLineHash": fingerprint},
                "locations": [
                    {
                        # physicalLocation required by GitHub Code Scanning.
                        # Map URL to nearest source file; see _url_to_file().
                        "physicalLocation": {
                            "artifactLocation": {
                                "uri": path,
                                "uriBaseId": "%SRCROOT%",
                            },
                            "region": {"startLine": 1},
                        },
                        # logicalLocations preserves the actual scanned URLs.
                        "logicalLocations": [
                            {"fullyQualifiedName": u, "kind": "url"} for u in urls
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
                        "name": tool_name,
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
        f"Converted {instances} ZAP instance(s) into {len(results)} result(s)"
        f" across {len(rules)} rule(s) → {sarif_path}"
        + (f"  ({skipped} suppressed by rules.tsv)" if skipped else "")
    )


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "report_json.json"
    dst = sys.argv[2] if len(sys.argv) > 2 else "zap-results.sarif"
    tsv = sys.argv[3] if len(sys.argv) > 3 else ".zap/rules.tsv"
    name = sys.argv[4] if len(sys.argv) > 4 else "ZAP"
    convert(src, dst, tsv, name)
