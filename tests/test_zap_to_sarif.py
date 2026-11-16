"""The ZAP-to-SARIF converter leaves out what the rules file ignores.

``.github/scripts/zap_to_sarif.py`` turns ZAP's JSON report into the alerts
shown on GitHub's Security tab. ZAP's report lists every alert, including
those the rules file marks IGNORE or OUTOFSCOPE (ZAP only leaves them out
of its pass/fail count), so the converter has to drop them itself.
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).parent.parent / ".github" / "scripts" / "zap_to_sarif.py"


def _converter() -> ModuleType:
    spec = importlib.util.spec_from_file_location("zap_to_sarif", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alert(rule_id: str, *urls: str) -> dict:
    return {
        "pluginid": rule_id,
        "name": f"rule {rule_id}",
        "riskcode": "2",
        "desc": "d",
        "reference": "",
        "instances": [{"uri": u} for u in urls],
    }


def _convert(tmp_path: Path, rules: str, *alerts: dict) -> dict[str, list[str]]:
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"site": [{"@name": "http://h", "alerts": alerts}]}))
    tsv = tmp_path / "rules.tsv"
    tsv.write_text(rules)
    out = tmp_path / "out.sarif"
    _converter().convert(str(report), str(out), str(tsv), "ZAP test")
    results = json.loads(out.read_text())["runs"][0]["results"]
    return {
        r["ruleId"]: [
            loc["fullyQualifiedName"] for loc in r["locations"][0]["logicalLocations"]
        ]
        for r in results
    }


def test_out_of_scope_urls_are_dropped_for_their_rule_only(tmp_path: Path) -> None:
    rules = (
        "# comment\n"
        "40038\tOUTOFSCOPE\t^https?://[^/]+/$\n"
        "*\tOUTOFSCOPE\thttp://h/never-.*\n"
        "10000\tWARN\n"
    )
    results = _convert(
        tmp_path,
        rules,
        _alert("40038", "http://h/", "http://h/admin", "http://h/never-this"),
        _alert("10000", "http://h/", "http://h/never-that"),
    )
    # 40038 keeps only the URL its pattern does not cover; another rule keeps
    # the home page; "*" applies to every rule.
    assert results == {"40038": ["http://h/admin"], "10000": ["http://h/"]}


def test_a_rule_with_every_url_out_of_scope_disappears(tmp_path: Path) -> None:
    results = _convert(
        tmp_path,
        "40038\tOUTOFSCOPE\t^https?://[^/]+/$\n",
        _alert("40038", "http://h/", "https://h/"),
        _alert("10000", "http://h/x"),
    )
    assert results == {"10000": ["http://h/x"]}


def test_ignored_rules_are_dropped(tmp_path: Path) -> None:
    results = _convert(
        tmp_path,
        "90027\tIGNORE\treason\n",
        _alert("90027", "http://h/"),
        _alert("10000", "http://h/"),
    )
    assert results == {"10000": ["http://h/"]}


def test_a_missing_rules_file_keeps_everything(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {"site": [{"@name": "http://h", "alerts": [_alert("1", "http://h/")]}]}
        )
    )
    out = tmp_path / "out.sarif"
    _converter().convert(str(report), str(out), str(tmp_path / "none.tsv"))
    assert [r["ruleId"] for r in json.loads(out.read_text())["runs"][0]["results"]] == [
        "1"
    ]
