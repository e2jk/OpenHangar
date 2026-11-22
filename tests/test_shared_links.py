"""Finding and cleaning the link in something shared from another app
(pwa.shared_links): Android apps such as Instagram put it inside the text."""

import pytest
from pwa.shared_links import (
    MAX_LINK,
    MAX_TITLE,
    clean_link,
    shared_link,
    suggested_title,
)


def test_instagram_puts_the_link_in_the_text() -> None:
    text = "https://www.instagram.com/reel/C9xYz12AbCd/?igsh=MWQ1ZGUxMzBkMA=="
    assert shared_link("", text) == "https://www.instagram.com/reel/C9xYz12AbCd/"


def test_the_url_field_wins_when_it_is_usable() -> None:
    assert (
        shared_link("https://example.org/a", "see https://example.org/b")
        == "https://example.org/a"
    )
    # An unusable url field falls back to the text.
    assert (
        shared_link("javascript:alert(1)", "see https://example.org/b")
        == "https://example.org/b"
    )


def test_first_usable_link_in_text_without_sentence_punctuation() -> None:
    text = "Great spot (https://user:pw@evil.example/x), then https://Example.ORG/Path?a=1."
    assert shared_link(None, text) == "https://example.org/Path?a=1"


def test_no_link_at_all() -> None:
    assert shared_link(None, None) is None
    assert shared_link("", "just words, ftp://files.example/x") is None


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "javascript:alert(1)",
        "data:text/html,hi",
        "ftp://files.example/x",
        "https://",
        "https://exa mple.org/",
        "https://example.org/\x7f",
        "https://[::1/",
        "https://user@example.org/",
        "https://example.org/" + "a" * MAX_LINK,
    ],
)
def test_unusable_links_are_refused(url: str) -> None:
    assert clean_link(url) is None


def test_tracking_parameters_are_removed_and_the_rest_kept() -> None:
    url = (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=abc&utm_source=share"
        "&UTM_Medium=x&fbclid=1#t=42"
    )
    assert clean_link(url) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ#t=42"
    # Untouched when nothing tracks: no re-encoding of the query.
    assert (
        clean_link("https://maps.example/?q=a+b&z") == "https://maps.example/?q=a+b&z"
    )


def test_suggested_title() -> None:
    link = "https://www.instagram.com/reel/C9/"
    assert suggested_title("  Castle   tour ", "ignored", link) == "Castle tour"
    assert (
        suggested_title(
            "", "Amazing castle near Spa! https://www.instagram.com/reel/C9/", link
        )
        == "Amazing castle near Spa!"
    )
    assert suggested_title(None, link, link) == ""
    assert suggested_title(link, None, link) == ""
    long = suggested_title("x" * 500, None)
    assert len(long) == MAX_TITLE and long.endswith("…")
