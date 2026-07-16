"""
Tests for the top-of-page HTMX loading indicator.

Covers:
- base.html renders the #oh-progress-bar element and loads progress_bar.js
- progress_bar.js wires up the expected HTMX lifecycle events
- base.css defines the element as a fixed overlay (no layout shift)
"""

from pathlib import Path

_STATIC_DIR = Path(__file__).parent.parent / "app" / "static"
_TEMPLATES_DIR = Path(__file__).parent.parent / "app" / "templates"


class TestProgressBarAssets:
    def test_progress_bar_js_exists(self):
        assert (_STATIC_DIR / "js" / "progress_bar.js").exists()

    def test_progress_bar_js_listens_for_htmx_lifecycle_events(self):
        content = (_STATIC_DIR / "js" / "progress_bar.js").read_text()
        assert "htmx:beforeRequest" in content
        assert "htmx:afterRequest" in content

    def test_progress_bar_css_is_fixed_overlay(self):
        content = (_STATIC_DIR / "css" / "base.css").read_text()
        assert "#oh-progress-bar" in content
        rule = content.split("#oh-progress-bar {", 1)[1].split("}", 1)[0]
        assert "position: fixed" in rule


class TestBaseTemplateLoadsProgressBar:
    def test_base_html_has_progress_bar_element(self):
        content = (_TEMPLATES_DIR / "base.html").read_text()
        assert 'id="oh-progress-bar"' in content

    def test_base_html_loads_progress_bar_js(self):
        content = (_TEMPLATES_DIR / "base.html").read_text()
        assert "progress_bar.js" in content
