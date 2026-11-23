"""Contrast rules for the setup wizard step dots and flash-toast close buttons.

The dark-theme ``.step-dot`` rule has the same specificity as ``.step-dot.active``
/ ``.step-dot.done`` and comes later, so without dark-theme restatements every
dot looked inactive. Flash toasts carry a white close button, which is barely
visible on Bootstrap's pale warning/info/light backgrounds.
"""

import re
from pathlib import Path

CSS_DIR = Path(__file__).resolve().parent.parent / "app" / "static" / "css"


def _rule_body(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, f"missing CSS rule for {selector!r}"
    return match.group(1)


def test_dark_theme_step_dots_keep_active_and_done_look() -> None:
    css = (CSS_DIR / "auth.css").read_text(encoding="utf-8")
    base_pos = css.index("[data-bs-theme=dark] .step-dot ")
    for state in ("active", "done"):
        selector = f"[data-bs-theme=dark] .step-dot.{state}"
        body = _rule_body(css, selector)
        assert "background" in body and "color" in body
        # Must come after the plain dark-theme dot rule to win the cascade.
        assert css.index(selector) > base_pos
    active = _rule_body(css, "[data-bs-theme=dark] .step-dot.active")
    inactive = _rule_body(css, "[data-bs-theme=dark] .step-dot ")
    assert active != inactive


def test_close_button_dark_on_pale_toasts() -> None:
    css = (CSS_DIR / "components.css").read_text(encoding="utf-8")
    for category in ("warning", "info", "light"):
        assert f".toast.text-bg-{category} .btn-close" in css
    body = css[css.index(".toast.text-bg-warning .btn-close") :]
    body = body[: body.index("}")]
    assert "--bs-btn-close-filter: none" in body
