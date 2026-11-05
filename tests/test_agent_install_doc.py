"""Keep docs/agent-install.md in sync with the files and settings it relies on.

The agent-install guide is followed literally by AI assistants, so a stale
reference (a renamed compose file, a placeholder that no longer exists, an
env var that was removed) silently breaks installs. These checks catch that
drift at test time.
"""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DOC = (_ROOT / "docs" / "agent-install.md").read_text(encoding="utf-8")
_CONFIG_DOC = (_ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
_RAW_PREFIX = "https://raw.githubusercontent.com/e2jk/OpenHangar/main/"


def _heading_anchors(markdown: str) -> set[str]:
    """GitHub-style anchors for every heading (plus explicit {#id} anchors)."""
    anchors = set()
    for line in markdown.splitlines():
        m = re.match(r"^#{1,6}\s+(.*)$", line)
        if not m:
            continue
        title = m.group(1)
        explicit = re.search(r"\{#([\w-]+)\}\s*$", title)
        if explicit:
            anchors.add(explicit.group(1))
            title = title[: explicit.start()]
        slug = re.sub(r"[^\w\- ]", "", title.strip().lower()).replace(" ", "-")
        anchors.add(slug)
    return anchors


class TestAgentInstallDoc:
    def test_referenced_repo_files_exist(self):
        """Every raw.githubusercontent URL must point at a real file on main."""
        paths = re.findall(re.escape(_RAW_PREFIX) + r"([\w./-]+)", _DOC)
        # "$BASE/<file>" downloads are relative to the docker/ folder.
        paths += ["docker/" + p for p in re.findall(r'"\$BASE/([\w./-]+)"', _DOC)]
        # Skip folder prefixes such as "BASE=…/main/docker" — files only.
        paths = [p for p in paths if "." in Path(p).name]
        assert paths, "expected at least one downloadable file reference"
        for rel in paths:
            assert (_ROOT / rel).is_file(), f"{rel} referenced but missing"

    def test_env_vars_are_documented(self):
        """Every OPENHANGAR_* var the guide mentions is a documented app setting,
        or a compose-level key defined in docker/.env.example."""
        env_example = (_ROOT / "docker" / ".env.example").read_text(encoding="utf-8")
        mentioned = set(re.findall(r"\bOPENHANGAR_[A-Z0-9_]+\b", _DOC))
        assert mentioned
        for var in mentioned:
            assert f"### `{var}`" in _CONFIG_DOC or re.search(
                rf"^#?\s*{var}=", env_example, re.MULTILINE
            ), (
                f"{var} is used in agent-install.md but is neither documented in "
                "docs/configuration.md nor defined in docker/.env.example"
            )

    def test_configuration_anchors_exist(self):
        anchors = _heading_anchors(_CONFIG_DOC)
        for anchor in re.findall(r"\(configuration\.md#([\w-]+)\)", _DOC):
            assert anchor in anchors, f"configuration.md#{anchor} does not exist"

    def test_other_doc_anchors_exist(self):
        for doc, anchor in re.findall(r"\(([\w-]+\.md)#([\w-]+)\)", _DOC):
            if doc == "configuration.md":
                continue
            target = (_ROOT / "docs" / doc).read_text(encoding="utf-8")
            assert anchor in _heading_anchors(target), f"{doc}#{anchor} missing"

    def test_internal_anchors_exist(self):
        anchors = _heading_anchors(_DOC)
        for anchor in re.findall(r"\]\(#([\w-]+)\)", _DOC):
            assert anchor in anchors, f"#{anchor} does not exist in agent-install.md"

    def test_raspberry_pi_placeholders_match_compose_file(self):
        """The sed placeholders must still exist in the Pi compose file."""
        compose = (_ROOT / "docker" / "docker-compose.raspberry-pi.yml").read_text(
            encoding="utf-8"
        )
        for placeholder in re.findall(r"s/(CHANGE_THIS_\w+)/", _DOC):
            assert placeholder in compose, f"{placeholder} gone from Pi compose file"
        assert "OPENHANGAR_SESSION_COOKIE_SECURE=false" in compose

    def test_env_example_keys_match(self):
        """The .env keys the guide rewrites must still exist in .env.example."""
        env_example = (_ROOT / "docker" / ".env.example").read_text(encoding="utf-8")
        for key in re.findall(r"s/\^([A-Z0-9_]+)=", _DOC):
            assert re.search(rf"^{key}=", env_example, re.MULTILINE), (
                f"{key} not found in docker/.env.example"
            )

    def test_readme_features_agent_install(self):
        readme = (_ROOT / "README.md").read_text(encoding="utf-8")
        assert _RAW_PREFIX + "docs/agent-install.md" in readme
