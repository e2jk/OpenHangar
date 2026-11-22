"""The link in something shared to OpenHangar from another app (42i).

A phone share carries `title`, `text` and `url`, but apps fill them
differently: Instagram, TikTok and others on Android put the link inside
`text` ("Watch this reel https://www.instagram.com/reel/…?igsh=…") and leave
`url` empty. These helpers find that link, clean it, and suggest a title for
what the link becomes (a new place). The server never fetches the link.

Entry points: `shared_link(url, text)`, `clean_link(url)`,
`suggested_title(title, text, link)`.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Place.external_url and Place.title column lengths.
MAX_LINK = 2048
MAX_TITLE = 200

_LINK_IN_TEXT = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
# Punctuation that ends a sentence rather than the link ("see https://x.com/a.").
_TRAILING = ".,;:!?)]}»…"

# Query parameters that only track who shared what, never what is shown.
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_NAMES = frozenset(
    {
        "igsh",
        "igshid",
        "si",
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "_hsenc",
        "_hsmi",
        "share_id",
        "share_app_id",
        "is_from_webapp",
        "sender_device",
        "sender_web_id",
        "ref_src",
        "ref_url",
    }
)


def _is_tracking(name: str) -> bool:
    lowered = name.lower()
    return lowered in _TRACKING_NAMES or lowered.startswith(_TRACKING_PREFIXES)


def clean_link(url: str | None) -> str | None:
    """*url* without tracking parameters, or None unless it is a usable
    http(s) link that fits the place's URL field."""
    text = (url or "").strip()
    if not text or any(ord(c) < 33 or ord(c) == 127 for c in text):
        return None
    try:
        parts = urlsplit(text)
        hostname = parts.hostname
    except ValueError:  # e.g. an invalid IPv6 literal or port
        return None
    if parts.scheme.lower() not in {"http", "https"} or not hostname:
        return None
    if parts.username is not None or parts.password is not None:
        return None  # "https://real.example@other.example/" misleads
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(name, value) for name, value in pairs if not _is_tracking(name)]
    # Re-encode the query only when something was removed from it.
    query = urlencode(kept) if len(kept) != len(pairs) else parts.query
    cleaned = urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path,
            query,
            parts.fragment,
        )
    )
    return cleaned if len(cleaned) <= MAX_LINK else None


def shared_link(url: str | None, text: str | None) -> str | None:
    """The shared link: the `url` field when usable, else the first usable
    http(s) link found in the shared `text`."""
    direct = clean_link(url)
    if direct is not None:
        return direct
    for match in _LINK_IN_TEXT.finditer(text or ""):
        found = clean_link(match.group(0).rstrip(_TRAILING))
        if found is not None:
            return found
    return None


def suggested_title(
    title: str | None, text: str | None, link: str | None = None
) -> str:
    """A starting title for what the share becomes: the shared title, else
    the shared text without its links, on one line and within the column."""
    candidate = " ".join((title or "").split())
    if not candidate:
        without_links = _LINK_IN_TEXT.sub(" ", text or "")
        candidate = " ".join(without_links.split())
    if link and candidate == link:
        candidate = ""
    if len(candidate) > MAX_TITLE:
        candidate = candidate[: MAX_TITLE - 1].rstrip() + "…"
    return candidate
