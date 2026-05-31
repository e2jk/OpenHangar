"""Convert a ZAP JSON report to SARIF 2.1.0 for upload to GitHub Security tab.

Usage:
    python3 zap_to_sarif.py [zap_json] [output_sarif]

Defaults: report_json.json → zap-results.sarif
"""

import json
import sys

_RISK_TO_LEVEL = {0: "note", 1: "note", 2: "warning", 3: "error"}


def convert(zap_json_path: str, sarif_path: str) -> None:
    with open(zap_json_path) as f:
        zap = json.load(f)

    rules: list[dict] = []
    results: list[dict] = []
    seen_rules: set[str] = set()

    for site in zap.get("site", []):
        for alert in site.get("alerts", []):
            rule_id = str(alert.get("pluginid", "0"))
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
                        "helpUri": alert.get("reference", "https://www.zaproxy.org/"),
                        "defaultConfiguration": {"level": level},
                    }
                )

            for inst in alert.get("instances", []):
                uri = inst.get("uri", site.get("@name", ""))
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
                                "physicalLocation": {
                                    "artifactLocation": {
                                        "uri": uri,
                                        "uriBaseId": "%SRCROOT%",
                                    },
                                    "region": {"startLine": 1},
                                }
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
    )


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "report_json.json"
    dst = sys.argv[2] if len(sys.argv) > 2 else "zap-results.sarif"
    convert(src, dst)
