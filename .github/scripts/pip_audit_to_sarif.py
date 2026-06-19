"""Convert pip-audit JSON output to SARIF 2.1.0 for GitHub Security tab upload.

pip-audit does not support SARIF output directly. This script reads the JSON
report produced by ``pip-audit --format json`` and converts it.

Each vulnerability becomes one SARIF result. The physical location points to
requirements/ci.txt (the pinned lock-file that CI installs from).

Usage:
    python3 pip_audit_to_sarif.py [pip_audit_json] [output_sarif]

Defaults: pip-audit.json → pip-audit.sarif
"""

import json
import sys

_INPUT = sys.argv[1] if len(sys.argv) > 1 else "pip-audit.json"
_OUTPUT = sys.argv[2] if len(sys.argv) > 2 else "pip-audit.sarif"

_REQUIREMENTS_FILE = "requirements/ci.txt"
_SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/"
    "Schemata/sarif-schema-2.1.0.json"
)


def _build_sarif(data: dict) -> dict:
    rules: list[dict] = []
    results: list[dict] = []
    seen_rules: set[str] = set()

    for dep in data.get("dependencies", []):
        pkg = dep.get("name", "unknown")
        ver = dep.get("version", "unknown")

        for vuln in dep.get("vulns", []):
            vid = vuln.get("id", "UNKNOWN")
            desc = vuln.get("description", "No description available.")
            fix = vuln.get("fix_versions", [])
            fix_str = f" Fix: upgrade to {', '.join(fix)}." if fix else ""
            aliases = vuln.get("aliases", [])
            help_uri = f"https://osv.dev/vulnerability/{vid}"

            if vid not in seen_rules:
                seen_rules.add(vid)
                rules.append(
                    {
                        "id": vid,
                        "name": "VulnerableDependency",
                        "shortDescription": {
                            "text": f"Vulnerable dependency: {pkg} {ver} ({vid})"
                        },
                        "fullDescription": {"text": desc},
                        "helpUri": help_uri,
                        "properties": {"tags": ["security", "supply-chain"]},
                    }
                )

            alias_note = (
                f" Also known as: {', '.join(aliases)}." if aliases else ""
            )
            results.append(
                {
                    "ruleId": vid,
                    "message": {
                        "text": (
                            f"{pkg} {ver} is affected by {vid}.{alias_note}"
                            f"{fix_str}"
                        )
                    },
                    "level": "error",
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {
                                    "uri": _REQUIREMENTS_FILE,
                                    "uriBaseId": "%SRCROOT%",
                                },
                                "region": {"startLine": 1},
                            }
                        }
                    ],
                }
            )

    return {
        "version": "2.1.0",
        "$schema": _SARIF_SCHEMA,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "pip-audit",
                        "informationUri": "https://github.com/pypa/pip-audit",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }


with open(_INPUT) as fh:
    data = json.load(fh)

sarif = _build_sarif(data)

with open(_OUTPUT, "w") as fh:
    json.dump(sarif, fh, indent=2)

print(f"Wrote {len(sarif['runs'][0]['results'])} result(s) to {_OUTPUT}")
