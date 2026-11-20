"""Identifier-only publisher adapters and bounded import previews.

The public Landegut participant endpoint and list-status schema were verified
2026-11-17. The status must allow publication before its current list is used.
No edition year is inferred from a live endpoint or an old product URL.
"""

import json
import re
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

from flask_babel import gettext as _

from passports.management import BuilderError
from passports.source_fetch import (
    TOTAL_SECONDS,
    Fetched,
    SourceError,
    fetch,
    validate_url,
)

PUBLISHER_LIST_URL = "https://www.landegut.de/api/airfields/public"
PUBLISHER_STATUS_URL = "https://www.landegut.de/api/airfields/list-state"
MAX_CODES = 5000


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "template"}:
            self.hidden += 1
        elif not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "template"} and self.hidden:
            self.hidden -= 1
        elif not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _json(response: Fetched) -> Any:
    try:
        return json.loads(response.body)
    except (ValueError, RecursionError) as exc:
        raise SourceError(
            _("The source format is unsupported. Paste the airfield codes instead.")
        ) from exc


def _unique(codes: list[str]) -> list[str]:
    result = list(dict.fromkeys(codes))
    if not result:
        raise SourceError(
            _(
                "No candidate airfield codes were found. For dynamic or unsupported pages, paste the codes instead."
            )
        )
    if len(result) > MAX_CODES:
        raise SourceError(
            _("The source contains too many airfield codes. Use a smaller list.")
        )
    return result


def generic_codes(response: Fetched) -> list[str]:
    text = response.body.decode("utf-8", errors="replace")
    if response.content_type in {"text/html", "application/xhtml+xml"}:
        parser = _VisibleText()
        parser.feed(text)
        text = " ".join(parser.parts)
    # Candidates, not promises: ordinary four-letter words are shown as unknown
    # unless unambiguously matched to the local catalogue. Never guess a place.
    return _unique(
        re.findall(
            r"(?<![A-Za-z0-9_])(?=[A-Z0-9]{0,3}[A-Z])[A-Z0-9]{4}(?![A-Za-z0-9_])", text
        )
    )


def publisher_codes(response: Fetched) -> list[str]:
    data = _json(response)
    rows = data.get("airfields") if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) > MAX_CODES:
        raise SourceError(
            _("The publisher list format changed. Paste a verified list instead.")
        )
    codes = []
    for row in rows:
        code = row.get("icao") if isinstance(row, dict) else None
        if not isinstance(code, str) or not re.fullmatch(
            r"[A-Z0-9]{3,4}", code.strip().upper()
        ):
            raise SourceError(
                _(
                    "The publisher list contains a missing or unsupported code. Paste a verified list instead."
                )
            )
        codes.append(code.strip().upper())
    return _unique(codes)


def prepare(
    tenant_id: int,
    state: dict[str, Any],
    adapter: str,
    url: str,
    *,
    user_id: int | None = None,
) -> None:
    from passports.builder import add_codes

    deadline = time.monotonic() + TOTAL_SECONDS
    if adapter == "landegutscheinheft":
        if not state["metadata"].get("edition_label", "").strip():
            raise BuilderError(
                _(
                    "Enter the booklet edition label before importing. Verify the edition with the publisher."
                )
            )
        status = _json(fetch(PUBLISHER_STATUS_URL, deadline=deadline))
        if not isinstance(status, dict) or status.get("enabled") is not True:
            raise SourceError(
                _(
                    "The publisher has not released its current participant list. Paste a list you have verified, or try again later."
                )
            )
        response = fetch(PUBLISHER_LIST_URL, deadline=deadline)
        codes = publisher_codes(response)
    elif adapter == "generic":
        response = fetch(url)
        codes = generic_codes(response)
    else:
        raise BuilderError(_("Choose a supported import source."))
    scratch: dict[str, Any] = {"selected": []}
    add_codes(tenant_id, scratch, "\n".join(codes), user_id=user_id)
    state["import_preview"] = {
        "source": {
            "url": response.url,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "adapter": adapter,
        },
        "codes": codes,
        "matched": scratch["selected"],
        "unknown": scratch["unresolved"],
    }


def apply(
    state: dict[str, Any], *, replace: bool, retained: set[str] | None = None
) -> None:
    preview = state.get("import_preview")
    if not preview:
        raise BuilderError(_("Preview the imported list before applying it."))
    state["selected"] = sorted(
        set(preview["matched"])
        | (set() if replace else set(state["selected"]))
        | (retained or set())
    )
    state["unresolved"] = list(
        dict.fromkeys(([] if replace else state["unresolved"]) + preview["unknown"])
    )
    state["codes"] = "\n".join(state["unresolved"])
    state["source"] = preview["source"]
    state["import_preview"] = None


def source_values(state: dict[str, Any]) -> dict[str, Any]:
    source = state.get("source")
    if not source:
        return {"source_url": None, "source_retrieved_at": None}
    try:
        url = validate_url(source["url"]).url
        retrieved = datetime.fromisoformat(source["retrieved_at"])
        if retrieved.tzinfo is None:
            retrieved = retrieved.replace(tzinfo=UTC)
    except (KeyError, TypeError, ValueError) as exc:
        raise BuilderError(
            _("The import provenance is invalid. Preview the source again.")
        ) from exc
    return {"source_url": url, "source_retrieved_at": retrieved}
