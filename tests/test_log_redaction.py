"""Tests for access-log token redaction (security finding N-25, CWE-532).

Secret tokens carried in the URL path of password-reset, share and invite
links must be masked before they reach the gunicorn access log.
"""

from log_redaction import redact_sensitive_path


class TestRedactSensitivePath:
    def test_reset_password_token_is_masked(self):
        assert (
            redact_sensitive_path("/reset-password/abc123XYZ_token")
            == "/reset-password/[REDACTED]"
        )

    def test_share_token_is_masked(self):
        assert redact_sensitive_path("/share/Ix9k2lQ0aB7v") == "/share/[REDACTED]"

    def test_invite_token_is_masked(self):
        assert (
            redact_sensitive_path("/config/users/invite/Zk3p-Qm7tWeR")
            == "/config/users/invite/[REDACTED]"
        )

    def test_query_string_is_preserved(self):
        assert (
            redact_sensitive_path("/share/secrettok?lang=fr")
            == "/share/[REDACTED]?lang=fr"
        )

    def test_token_in_request_line_context_is_masked(self):
        # PATH_INFO is what gets redacted; the request line replacement reuses
        # the same transform on the path component.
        assert redact_sensitive_path("/reset-password/tok") == (
            "/reset-password/[REDACTED]"
        )

    def test_share_create_sibling_is_not_redacted(self):
        # /aircraft/<id>/share/create is a different, non-secret route.
        assert (
            redact_sensitive_path("/aircraft/42/share/create")
            == "/aircraft/42/share/create"
        )

    def test_invite_revoke_sibling_is_not_redacted(self):
        assert (
            redact_sensitive_path("/config/users/invite/7/revoke")
            == "/config/users/invite/7/revoke"
        )

    def test_share_qr_sibling_is_not_redacted(self):
        assert (
            redact_sensitive_path("/aircraft/42/share/3/qr")
            == "/aircraft/42/share/3/qr"
        )

    def test_unrelated_path_is_unchanged(self):
        assert redact_sensitive_path("/aircraft/12/flights") == "/aircraft/12/flights"

    def test_health_endpoint_is_unchanged(self):
        assert redact_sensitive_path("/health") == "/health"

    def test_prefix_without_token_segment_is_unchanged(self):
        # No token after the prefix → nothing to redact.
        assert redact_sensitive_path("/share/") == "/share/"

    def test_token_must_be_at_path_start(self):
        # A sensitive prefix appearing mid-path must not trigger redaction.
        assert (
            redact_sensitive_path("/foo/reset-password/tok")
            == "/foo/reset-password/tok"
        )

    def test_empty_path_is_unchanged(self):
        assert redact_sensitive_path("") == ""
