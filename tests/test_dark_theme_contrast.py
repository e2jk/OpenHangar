"""Dark-theme contrast of icons and glyphs drawn on dark backgrounds.

The navbar is dark in both themes. Its menu icon used to be Bootstrap's
own icon put through ``filter: invert(1)``: right in the light theme,
but Bootstrap already draws that icon light in the dark theme, so the
inversion turned it into dark lines on the dark navbar.
"""

import re
from pathlib import Path

_CSS_DIR = Path(__file__).resolve().parent.parent / "app" / "static" / "css"


def _rule_body(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, f"no CSS rule for {selector!r}"
    return match.group(1)


class TestNavbarMenuIcon:
    def test_icon_is_not_inverted(self) -> None:
        css = (_CSS_DIR / "base.css").read_text()
        assert not re.search(r"\.navbar-toggler-icon\s*\{[^}]*filter", css), (
            "an inverted toggler icon turns dark in the dark theme"
        )

    def test_icon_is_drawn_light_in_both_themes(self) -> None:
        css = (_CSS_DIR / "base.css").read_text()
        body = _rule_body(css, ".navbar .navbar-toggler-icon")
        assert "--bs-navbar-toggler-icon-bg" in body
        assert "stroke='rgba%28255, 255, 255" in body


class TestDarkThemeOverrides:
    def test_outline_dark_button_is_light_in_dark_theme(self) -> None:
        css = (_CSS_DIR / "base.css").read_text()
        body = _rule_body(css, "[data-bs-theme=dark] .btn-outline-dark")
        assert "--bs-btn-color: #dee2e6" in body

    def test_reservation_calendar_headers_are_light_in_dark_theme(self) -> None:
        css = (_CSS_DIR / "components.css").read_text()
        assert "color" in _rule_body(css, "[data-bs-theme=dark] .res-cal-table th")

    def test_setup_wizard_icons_are_light_in_dark_theme(self) -> None:
        css = (_CSS_DIR / "auth.css").read_text()
        assert "color" in _rule_body(css, "[data-bs-theme=dark] .wizard-choice-icon")
