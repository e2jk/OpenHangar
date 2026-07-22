"""Convert an Atheris crash reproduction log to SARIF 2.1.0 for GitHub
Security tab upload.

Atheris/libFuzzer print a plain Python traceback on crash, not SARIF. This
script parses that traceback out of the captured reproduction log and
converts it. The physical location points to the deepest `app/`-relative
frame in the traceback (the actual application code that crashed), falling
back to the fuzz harness file itself if no such frame is found.

Usage:
    python3 fuzz_crash_to_sarif.py <harness_name> <repro_log> [output_sarif]

Defaults: output → crash.sarif
"""

import json
import re
import sys

_HARNESS = sys.argv[1]
_INPUT = sys.argv[2]
_OUTPUT = sys.argv[3] if len(sys.argv) > 3 else "crash.sarif"

_SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/"
    "Schemata/sarif-schema-2.1.0.json"
)
_FRAME_RE = re.compile(r'^\s*File "([^"]+)", line (\d+)', re.MULTILINE)
_EXC_MARKER = "=== Uncaught Python exception: ==="


def _extract_location(log: str, harness: str) -> tuple[str, int]:
    """Return the deepest app/-relative traceback frame, or the harness file."""
    app_frames = []
    for path, line in _FRAME_RE.findall(log):
        norm = path.replace("\\", "/")
        idx = norm.find("/app/")
        if idx != -1:
            app_frames.append((norm[idx + 1 :], int(line)))
    if app_frames:
        return app_frames[-1]
    return f"fuzz/{harness}.py", 1


def _extract_message(log: str) -> str:
    """Return the exception line Atheris prints right after its crash marker."""
    marker_idx = log.find(_EXC_MARKER)
    if marker_idx != -1:
        after = log[marker_idx + len(_EXC_MARKER) :].lstrip("\n")
        lines = after.splitlines()
        if lines and lines[0].strip():
            return lines[0].strip()
    # Fallback: last non-indented, non-frame, non-libFuzzer-banner line.
    for line in reversed(log.strip().splitlines()):
        stripped = line.strip()
        if (
            stripped
            and not line.startswith(" ")
            and not stripped.startswith("File ")
            and not stripped.startswith("SUMMARY:")
            and not stripped.startswith("==")
        ):
            return stripped
    return "Atheris fuzz target crashed."


def _build_sarif(harness: str, log: str) -> dict:
    path, line = _extract_location(log, harness)
    message = _extract_message(log)
    return {
        "version": "2.1.0",
        "$schema": _SARIF_SCHEMA,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "atheris",
                        "informationUri": "https://github.com/google/atheris",
                        "rules": [
                            {
                                "id": harness,
                                "name": "FuzzCrash",
                                "shortDescription": {
                                    "text": f"Fuzz crash in {harness}"
                                },
                                "fullDescription": {
                                    "text": "Atheris found an input that crashes this fuzz target."
                                },
                                "properties": {"tags": ["security", "fuzzing"]},
                            }
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": harness,
                        "message": {"text": message},
                        "level": "error",
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {
                                        "uri": path,
                                        "uriBaseId": "%SRCROOT%",
                                    },
                                    "region": {"startLine": max(line, 1)},
                                }
                            }
                        ],
                    }
                ],
            }
        ],
    }


with open(_INPUT) as fh:
    _log = fh.read()

_sarif = _build_sarif(_HARNESS, _log)

with open(_OUTPUT, "w") as fh:
    json.dump(_sarif, fh, indent=2)

print(f"Wrote crash SARIF for {_HARNESS} to {_OUTPUT}")
