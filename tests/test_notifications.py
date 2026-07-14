"""
Tests for Phase 34: Email Notifications.
Covers: 3-level preference lookup, dispatch, health tracking, notification prefs UI.
"""

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from unittest.mock import MagicMock, patch

from models import (  # pyright: ignore[reportMissingImports]
    AppSetting,
    NotificationPreference,
    NotificationType,
    Role,
    Tenant,
    TenantNotificationDefault,
    TenantUser,
    User,
    db,
)
import contextlib


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_user(
    app,
    email,
    role=Role.OWNER,
    is_pilot=False,
    is_maintenance=False,
    is_instance_admin=False,
):
    with app.app_context():
        t = Tenant(name="Test Hangar")
        db.session.add(t)
        db.session.flush()
        u = User(
            email=email,
            password_hash=_pw_hash.hash("pw"),
            is_active=True,
            is_pilot=is_pilot,
            is_maintenance=is_maintenance,
            is_instance_admin=is_instance_admin,
        )
        db.session.add(u)
        db.session.flush()
        db.session.add(TenantUser(user_id=u.id, tenant_id=t.id, role=role))
        db.session.commit()
        return u.id, t.id


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


# ── OPENHANGAR_NOTIFICATION_TIME validation ────────────────────────────────────


class TestParseNotificationTime:
    def _parse(self, value=None):
        import os
        from unittest.mock import patch as _patch
        from init import _parse_notification_time  # pyright: ignore[reportMissingImports]

        env = {} if value is None else {"OPENHANGAR_NOTIFICATION_TIME": value}
        with _patch.dict(os.environ, env, clear=(value is None)):
            return _parse_notification_time()

    def test_default_returns_07_00(self):
        import os
        from unittest.mock import patch as _patch
        from init import _parse_notification_time  # pyright: ignore[reportMissingImports]

        with _patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENHANGAR_NOTIFICATION_TIME", None)
            assert _parse_notification_time() == (7, 0)

    def test_valid_time_parsed(self):
        assert self._parse("14:30") == (14, 30)

    def test_midnight_valid(self):
        assert self._parse("00:00") == (0, 0)

    def test_end_of_day_valid(self):
        assert self._parse("23:59") == (23, 59)

    def test_missing_colon_raises(self):
        import pytest

        with pytest.raises(ValueError, match="OPENHANGAR_NOTIFICATION_TIME"):
            self._parse("0700")

    def test_non_numeric_raises(self):
        import pytest

        with pytest.raises(ValueError, match="OPENHANGAR_NOTIFICATION_TIME"):
            self._parse("ab:cd")

    def test_hour_out_of_range_raises(self):
        import pytest

        with pytest.raises(ValueError, match="OPENHANGAR_NOTIFICATION_TIME"):
            self._parse("24:00")

    def test_minute_out_of_range_raises(self):
        import pytest

        with pytest.raises(ValueError, match="OPENHANGAR_NOTIFICATION_TIME"):
            self._parse("07:60")

    def test_negative_values_raise(self):
        import pytest

        with pytest.raises(ValueError, match="OPENHANGAR_NOTIFICATION_TIME"):
            self._parse("-1:00")

    def test_validate_config_raises_on_bad_value(self, app):
        import os
        import pytest
        from unittest.mock import patch as _patch
        from init import _validate_config  # pyright: ignore[reportMissingImports]

        with _patch.dict(os.environ, {"OPENHANGAR_NOTIFICATION_TIME": "99:99"}):
            with pytest.raises(RuntimeError, match="OPENHANGAR_NOTIFICATION_TIME"):
                _validate_config(app)

    def test_validate_config_accepts_valid_value(self, app):
        import os
        from unittest.mock import patch as _patch
        from init import _validate_config  # pyright: ignore[reportMissingImports]

        with _patch.dict(os.environ, {"OPENHANGAR_NOTIFICATION_TIME": "06:30"}):
            _validate_config(app)  # must not raise


# ── NotificationType constants ─────────────────────────────────────────────────


class TestNotificationTypeConstants:
    def test_all_has_17_types(self):
        assert len(NotificationType.ALL) == 17

    def test_system_defaults_cover_all_types(self):
        for t in NotificationType.ALL:
            assert t in NotificationType.SYSTEM_DEFAULTS

    def test_required_caps_cover_all_types(self):
        for t in NotificationType.ALL:
            assert t in NotificationType.REQUIRED_CAPS

    def test_grounding_snag_enabled_by_default(self):
        assert (
            NotificationType.SYSTEM_DEFAULTS[NotificationType.GROUNDING_SNAG_OPENED][
                "enabled"
            ]
            is True
        )

    def test_snag_reported_disabled_by_default(self):
        assert (
            NotificationType.SYSTEM_DEFAULTS[NotificationType.SNAG_REPORTED]["enabled"]
            is False
        )

    def test_threshold_types_have_threshold(self):
        for t in NotificationType.HAS_THRESHOLD:
            default = NotificationType.SYSTEM_DEFAULTS[t]
            assert default["threshold_days"] is not None, (
                f"{t} should have a threshold_days default"
            )


# ── Three-level preference lookup ──────────────────────────────────────────────


class TestEffectivePreference:
    def test_system_default_when_no_overrides(self, app):
        uid, tid = _make_user(app, "admin@test.com")
        with app.app_context():
            from services.notification_service import get_effective_preference

            pref = get_effective_preference(
                uid, tid, NotificationType.GROUNDING_SNAG_OPENED
            )
            assert pref["enabled"] is True
            assert pref["threshold_days"] is None

    def test_tenant_default_overrides_system(self, app):
        uid, tid = _make_user(app, "admin2@test.com")
        with app.app_context():
            db.session.add(
                TenantNotificationDefault(
                    tenant_id=tid,
                    notification_type=NotificationType.GROUNDING_SNAG_OPENED,
                    enabled=False,
                    threshold_days=None,
                    updated_at=__import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc
                    ),
                )
            )
            db.session.commit()

            from services.notification_service import get_effective_preference

            pref = get_effective_preference(
                uid, tid, NotificationType.GROUNDING_SNAG_OPENED
            )
            assert pref["enabled"] is False

    def test_user_override_wins_over_tenant_default(self, app):
        uid, tid = _make_user(app, "admin3@test.com")
        with app.app_context():
            from datetime import datetime, timezone

            db.session.add(
                TenantNotificationDefault(
                    tenant_id=tid,
                    notification_type=NotificationType.GROUNDING_SNAG_OPENED,
                    enabled=False,
                    threshold_days=None,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            db.session.add(
                NotificationPreference(
                    user_id=uid,
                    tenant_id=tid,
                    notification_type=NotificationType.GROUNDING_SNAG_OPENED,
                    enabled=True,
                    threshold_days=None,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            db.session.commit()

            from services.notification_service import get_effective_preference

            pref = get_effective_preference(
                uid, tid, NotificationType.GROUNDING_SNAG_OPENED
            )
            assert pref["enabled"] is True

    def test_threshold_days_from_tenant_default(self, app):
        uid, tid = _make_user(app, "admin4@test.com")
        with app.app_context():
            from datetime import datetime, timezone

            db.session.add(
                TenantNotificationDefault(
                    tenant_id=tid,
                    notification_type=NotificationType.MAINTENANCE_DUE_SOON,
                    enabled=True,
                    threshold_days=14,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            db.session.commit()

            from services.notification_service import get_effective_preference

            pref = get_effective_preference(
                uid, tid, NotificationType.MAINTENANCE_DUE_SOON
            )
            assert pref["threshold_days"] == 14


# ── Email health tracking ──────────────────────────────────────────────────────


class TestEmailHealth:
    def test_unconfigured_when_no_smtp_host(self, app):
        with app.app_context():
            import os
            from services.email_service import get_email_health

            with patch.dict(os.environ, {"OPENHANGAR_SMTP_HOST": ""}, clear=False):
                health = get_email_health()
            assert health["status"] == "unconfigured"

    def test_ok_status_when_no_failures(self, app):
        with app.app_context():
            import os
            from services.email_service import get_email_health

            with patch.dict(
                os.environ,
                {
                    "OPENHANGAR_SMTP_HOST": "smtp.example.com",
                    "OPENHANGAR_SMTP_FROM_ADDRESS": "a@b.com",
                },
                clear=False,
            ):
                # Ensure no failure record in DB
                existing = db.session.get(AppSetting, "email_consecutive_failures")
                if existing:
                    db.session.delete(existing)
                    db.session.commit()
                health = get_email_health()
            assert health["status"] == "ok"

    def test_degraded_when_had_success_then_failures(self, app):
        with app.app_context():
            import os
            from services.email_service import get_email_health
            from datetime import datetime, timezone

            for key, val in [
                ("email_last_success_at", datetime.now(timezone.utc).isoformat()),
                ("email_consecutive_failures", "3"),
            ]:
                s = db.session.get(AppSetting, key)
                if s:
                    s.value = val
                else:
                    db.session.add(AppSetting(key=key, value=val))
            db.session.commit()

            with patch.dict(
                os.environ,
                {
                    "OPENHANGAR_SMTP_HOST": "smtp.example.com",
                    "OPENHANGAR_SMTP_FROM_ADDRESS": "a@b.com",
                },
                clear=False,
            ):
                health = get_email_health()
            assert health["status"] == "degraded"
            assert health["consecutive_failures"] == 3

    def test_never_worked_when_only_failures(self, app):
        with app.app_context():
            import os
            from services.email_service import get_email_health

            # Remove any prior success
            s = db.session.get(AppSetting, "email_last_success_at")
            if s:
                db.session.delete(s)
            f = db.session.get(AppSetting, "email_consecutive_failures")
            if f:
                f.value = "2"
            else:
                db.session.add(AppSetting(key="email_consecutive_failures", value="2"))
            db.session.commit()

            with patch.dict(
                os.environ,
                {
                    "OPENHANGAR_SMTP_HOST": "smtp.example.com",
                    "OPENHANGAR_SMTP_FROM_ADDRESS": "a@b.com",
                },
                clear=False,
            ):
                health = get_email_health()
            assert health["status"] == "never_worked"


# ── Dispatch (with mocked send_email) ─────────────────────────────────────────


class TestDispatch:
    def _ctx(self):
        return {
            "subject": "Test grounding",
            "notification_title": "Grounding test",
            "notification_message": "A snag was opened.",
            "details": [],
        }

    def test_dispatch_sends_to_eligible_owner(self, app):
        uid, tid = _make_user(app, "owner@dispatch.com", role=Role.OWNER)
        with app.app_context():
            with (
                patch("services.email_service.send_email") as mock_send,
                patch("services.email_service._record_health"),
                patch(
                    "services.notification_service._render_email",
                    return_value=("plain text", "<p>html</p>"),
                ),
            ):
                from services.notification_service import dispatch

                dispatch(NotificationType.GROUNDING_SNAG_OPENED, tid, self._ctx())
                assert mock_send.called

    def test_dispatch_skips_disabled_preference(self, app):
        uid, tid = _make_user(app, "owner@nodispatch.com", role=Role.OWNER)
        with app.app_context():
            from datetime import datetime, timezone

            db.session.add(
                NotificationPreference(
                    user_id=uid,
                    tenant_id=tid,
                    notification_type=NotificationType.GROUNDING_SNAG_OPENED,
                    enabled=False,
                    threshold_days=None,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            db.session.commit()

            with (
                patch("services.email_service.send_email") as mock_send,
                patch("services.email_service._record_health"),
                patch(
                    "services.notification_service._render_email",
                    return_value=("plain text", "<p>html</p>"),
                ),
            ):
                from services.notification_service import dispatch

                dispatch(NotificationType.GROUNDING_SNAG_OPENED, tid, self._ctx())
                assert not mock_send.called

    def test_dispatch_with_target_user_ids_only_notifies_target(self, app):
        uid1, tid = _make_user(app, "pilot1@target.com", role=Role.PILOT, is_pilot=True)
        with app.app_context():
            # Add second pilot to same tenant
            u2 = User(
                email="pilot2@target.com",
                password_hash=_pw_hash.hash("pw"),
                is_active=True,
                is_pilot=True,
            )
            db.session.add(u2)
            db.session.flush()
            db.session.add(TenantUser(user_id=u2.id, tenant_id=tid, role=Role.PILOT))
            db.session.commit()

            with (
                patch("services.email_service.send_email") as mock_send,
                patch("services.email_service._record_health"),
                patch(
                    "services.notification_service._render_email",
                    return_value=("plain text", "<p>html</p>"),
                ),
            ):
                from services.notification_service import dispatch

                dispatch(
                    NotificationType.RESERVATION_CONFIRMED,
                    tid,
                    {
                        "subject": "Confirmed",
                        "notification_title": "Confirmed",
                        "notification_message": "msg",
                        "details": [],
                    },
                    target_user_ids=[uid1],
                )
                assert mock_send.call_count == 1
                assert mock_send.call_args[1]["to"] == "pilot1@target.com"


# ── Notification preferences route ────────────────────────────────────────────


class TestNotificationPreferencesRoute:
    def test_get_accessible_to_all_logged_in_users(self, app, client):
        uid, _tid = _make_user(app, "pilot@prefs.com", role=Role.PILOT)
        _login(client, uid)
        response = client.get("/config/notifications/")
        assert response.status_code == 200

    def test_get_shows_pilot_relevant_types(self, app, client):
        uid, _tid = _make_user(app, "pilot2@prefs.com", role=Role.PILOT)
        _login(client, uid)
        response = client.get("/config/notifications/")
        html = response.data.decode()
        # Pilot-relevant notifications
        assert "Reservation confirmed" in html
        assert "Medical certificate expiring" in html
        # Owner-only should not appear for a pure pilot
        assert "New member joined" not in html

    def test_post_saves_preference(self, app, client):
        uid, tid = _make_user(app, "owner@prefs.com", role=Role.OWNER)
        _login(client, uid)
        response = client.post(
            "/config/notifications/",
            data={"csrf_token": "test", "enabled_grounding_snag_opened": "on"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            # The preference is same as default (enabled=True), so should NOT be stored
            pref = NotificationPreference.query.filter_by(
                user_id=uid,
                tenant_id=tid,
                notification_type=NotificationType.GROUNDING_SNAG_OPENED,
            ).first()
            assert pref is None

    def test_post_saves_non_default_preference(self, app, client):
        uid, tid = _make_user(app, "owner2@prefs.com", role=Role.OWNER)
        _login(client, uid)
        # Disable SNAG_REPORTED (system default: disabled) — same as default, should not persist
        # Disable GROUNDING_SNAG_OPENED (system default: enabled) — different from default, should persist
        response = client.post(
            "/config/notifications/",
            data={"csrf_token": "test"},  # no "enabled_*" checkbox = all disabled
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            # GROUNDING_SNAG_OPENED was default enabled=True, user sent enabled=False → should persist
            pref = NotificationPreference.query.filter_by(
                user_id=uid,
                tenant_id=tid,
                notification_type=NotificationType.GROUNDING_SNAG_OPENED,
            ).first()
            assert pref is not None
            assert pref.enabled is False

    def test_redirects_to_login_when_unauthenticated(self, client):
        response = client.get("/config/notifications/")
        assert response.status_code in (302, 401)

    def test_owner_sees_tenant_defaults_section(self, app, client):
        uid, _tid = _make_user(app, "owner3@prefs.com", role=Role.OWNER)
        _login(client, uid)
        response = client.get("/config/notifications/")
        html = response.data.decode()
        assert "Tenant defaults" in html

    def test_pilot_does_not_see_tenant_defaults(self, app, client):
        uid, _tid = _make_user(app, "pilot3@prefs.com", role=Role.PILOT)
        _login(client, uid)
        response = client.get("/config/notifications/")
        html = response.data.decode()
        assert "Tenant defaults" not in html

    def test_post_saves_threshold_for_threshold_type(self, app, client):
        uid, tid = _make_user(app, "owner4@prefs.com", role=Role.OWNER)
        _login(client, uid)
        response = client.post(
            "/config/notifications/",
            data={
                "csrf_token": "test",
                "enabled_maintenance_due_soon": "on",
                "threshold_maintenance_due_soon": "14",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            pref = NotificationPreference.query.filter_by(
                user_id=uid,
                tenant_id=tid,
                notification_type=NotificationType.MAINTENANCE_DUE_SOON,
            ).first()
            assert pref is not None
            assert pref.threshold_days == 14

    def test_tenant_defaults_shows_stored_override(self, app, client):
        uid, tid = _make_user(app, "owner5@prefs.com", role=Role.OWNER)
        _login(client, uid)
        with app.app_context():
            from datetime import datetime, timezone

            db.session.add(
                TenantNotificationDefault(
                    tenant_id=tid,
                    notification_type=NotificationType.MAINTENANCE_DUE_SOON,
                    enabled=True,
                    threshold_days=7,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            db.session.commit()
        response = client.get("/config/notifications/")
        assert response.status_code == 200
        assert b"7" in response.data

    def test_maintenance_role_sees_maintenance_types(self, app, client):
        uid, _tid = _make_user(app, "maint@prefs.com", role=Role.MAINTENANCE)
        _login(client, uid)
        response = client.get("/config/notifications/")
        html = response.data.decode()
        assert "Grounding snag reported" in html
        assert "Reservation confirmed" not in html


# ── Welcome email ──────────────────────────────────────────────────────────────


class TestWelcomeEmail:
    def test_sends_when_flag_absent_and_smtp_set(self, app):
        _make_user(app, "admin@welcome.com", role=Role.OWNER, is_instance_admin=True)
        with app.app_context():
            row = db.session.get(AppSetting, "welcome_email_sent")
            if row:
                db.session.delete(row)
            db.session.commit()

        with (
            patch("services.email_service.send_email") as mock_send,
            patch.dict(
                __import__("os").environ,
                {
                    "OPENHANGAR_SMTP_HOST": "smtp.example.com",
                    "OPENHANGAR_ENV": "production",
                },
            ),
        ):
            from services.notification_service import send_welcome_email_if_needed

            send_welcome_email_if_needed(app)
            assert mock_send.called

        with app.app_context():
            assert db.session.get(AppSetting, "welcome_email_sent") is not None

    def test_skips_when_flag_already_set(self, app):
        with app.app_context():
            row = db.session.get(AppSetting, "welcome_email_sent")
            if not row:
                db.session.add(AppSetting(key="welcome_email_sent", value="true"))
                db.session.commit()

        with patch("services.email_service.send_email") as mock_send:
            from services.notification_service import send_welcome_email_if_needed

            send_welcome_email_if_needed(app)
            assert not mock_send.called

    def test_skips_when_smtp_not_configured(self, app):
        with app.app_context():
            row = db.session.get(AppSetting, "welcome_email_sent")
            if row:
                db.session.delete(row)
            db.session.commit()

        import os

        env_without_smtp = {
            k: v for k, v in os.environ.items() if k != "OPENHANGAR_SMTP_HOST"
        }
        env_without_smtp["OPENHANGAR_SMTP_HOST"] = ""
        with (
            patch("services.email_service.send_email") as mock_send,
            patch.dict(os.environ, env_without_smtp, clear=True),
        ):
            from services.notification_service import send_welcome_email_if_needed

            send_welcome_email_if_needed(app)
            assert not mock_send.called

    def test_send_failure_is_swallowed(self, app):
        _make_user(app, "admin@fail.com", role=Role.OWNER, is_instance_admin=True)
        with app.app_context():
            row = db.session.get(AppSetting, "welcome_email_sent")
            if row:
                db.session.delete(row)
            db.session.commit()

        with (
            patch(
                "services.email_service.send_email",
                side_effect=Exception("SMTP down"),
            ),
            patch.dict(
                __import__("os").environ,
                {
                    "OPENHANGAR_SMTP_HOST": "smtp.example.com",
                    "OPENHANGAR_ENV": "production",
                },
            ),
        ):
            from services.notification_service import send_welcome_email_if_needed

            send_welcome_email_if_needed(app)  # must not raise

    def test_skips_when_flag_set_after_lock_acquired(self, app):
        """Another worker committed welcome_email_sent between our pre-check and lock."""
        _make_user(app, "admin@recheck.com", role=Role.OWNER, is_instance_admin=True)
        with app.app_context():
            row = db.session.get(AppSetting, "welcome_email_sent")
            if row:
                db.session.delete(row)
            db.session.commit()

        def _set_flag_then_lock(*_a, **_kw):
            with app.app_context():
                if not db.session.get(AppSetting, "welcome_email_sent"):
                    db.session.add(AppSetting(key="welcome_email_sent", value="true"))
                    db.session.commit()
            return True

        with (
            patch(
                "services.notification_service._try_welcome_lock",
                side_effect=_set_flag_then_lock,
            ),
            patch("services.email_service.send_email") as mock_send,
            patch.dict(
                __import__("os").environ,
                {
                    "OPENHANGAR_SMTP_HOST": "smtp.example.com",
                    "OPENHANGAR_ENV": "production",
                },
            ),
        ):
            from services.notification_service import send_welcome_email_if_needed

            send_welcome_email_if_needed(app)
            assert not mock_send.called

    def test_skips_when_advisory_lock_not_acquired(self, app):
        _make_user(app, "admin@lock.com", role=Role.OWNER, is_instance_admin=True)
        with app.app_context():
            row = db.session.get(AppSetting, "welcome_email_sent")
            if row:
                db.session.delete(row)
            db.session.commit()

        with (
            patch(
                "services.notification_service._try_welcome_lock", return_value=False
            ),
            patch("services.email_service.send_email") as mock_send,
            patch.dict(
                __import__("os").environ,
                {
                    "OPENHANGAR_SMTP_HOST": "smtp.example.com",
                    "OPENHANGAR_ENV": "production",
                },
            ),
        ):
            from services.notification_service import send_welcome_email_if_needed

            send_welcome_email_if_needed(app)
            assert not mock_send.called


class TestTryWelcomeLock:
    def test_non_postgresql_returns_true_without_db_call(self):
        from unittest.mock import MagicMock

        from services.notification_service import _try_welcome_lock  # pyright: ignore[reportMissingImports]

        mock_db = MagicMock()
        mock_db.engine.dialect.name = "sqlite"
        result = _try_welcome_lock(mock_db)
        assert result is True
        mock_db.session.execute.assert_not_called()

    def test_postgresql_returns_true_when_lock_acquired(self):
        from unittest.mock import MagicMock

        from services.notification_service import _try_welcome_lock  # pyright: ignore[reportMissingImports]

        mock_db = MagicMock()
        mock_db.engine.dialect.name = "postgresql"
        mock_db.session.execute.return_value.scalar.return_value = True
        assert _try_welcome_lock(mock_db) is True

    def test_postgresql_returns_false_when_lock_not_acquired(self):
        from unittest.mock import MagicMock

        from services.notification_service import _try_welcome_lock  # pyright: ignore[reportMissingImports]

        mock_db = MagicMock()
        mock_db.engine.dialect.name = "postgresql"
        mock_db.session.execute.return_value.scalar.return_value = False
        assert _try_welcome_lock(mock_db) is False


# ── Daily expiry checks ────────────────────────────────────────────────────────


def _make_aircraft(app, tenant_id, registration="OO-TST"):
    from models import Aircraft  # pyright: ignore[reportMissingImports]

    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id,
            registration=registration,
            make="Cessna",
            model="172",
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


class TestDailyChecks:
    def test_maintenance_overdue_dispatches(self, app):
        _uid, tid = _make_user(app, "owner@maint-check.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid, "OO-OVD")
        with app.app_context():
            from datetime import date, timedelta
            from models import MaintenanceTrigger, TriggerType  # pyright: ignore[reportMissingImports]

            db.session.add(
                MaintenanceTrigger(
                    aircraft_id=ac_id,
                    name="Annual inspection",
                    trigger_type=TriggerType.CALENDAR,
                    due_date=date.today() - timedelta(days=5),
                )
            )
            db.session.commit()

            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_maintenance

                _check_maintenance(app)
                types_dispatched = [c.args[0] for c in mock_dispatch.call_args_list]
                assert NotificationType.MAINTENANCE_OVERDUE in types_dispatched

    def test_maintenance_due_soon_dispatches(self, app):
        _uid, tid = _make_user(app, "owner@maint-soon.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid, "OO-SON")
        with app.app_context():
            from datetime import date, timedelta
            from models import MaintenanceTrigger, TriggerType  # pyright: ignore[reportMissingImports]

            db.session.add(
                MaintenanceTrigger(
                    aircraft_id=ac_id,
                    name="Oil change",
                    trigger_type=TriggerType.CALENDAR,
                    due_date=date.today() + timedelta(days=10),
                )
            )
            db.session.commit()

            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_maintenance

                _check_maintenance(app)
                types_dispatched = [c.args[0] for c in mock_dispatch.call_args_list]
                assert NotificationType.MAINTENANCE_DUE_SOON in types_dispatched

    def test_insurance_dispatches_within_threshold(self, app):
        _uid, tid = _make_user(app, "owner@ins.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid, "OO-INS")
        with app.app_context():
            from datetime import date, timedelta
            from models import Aircraft  # pyright: ignore[reportMissingImports]

            ac = db.session.get(Aircraft, ac_id)
            ac.insurance_expiry = date.today() + timedelta(days=10)
            db.session.commit()

            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_insurance

                _check_insurance(app)
                types_dispatched = [c.args[0] for c in mock_dispatch.call_args_list]
                assert NotificationType.INSURANCE_EXPIRING in types_dispatched

    def test_insurance_skips_when_far_away(self, app):
        _uid, tid = _make_user(app, "owner@ins-ok.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid, "OO-OK")
        with app.app_context():
            from datetime import date, timedelta
            from models import Aircraft  # pyright: ignore[reportMissingImports]

            ac = db.session.get(Aircraft, ac_id)
            ac.insurance_expiry = date.today() + timedelta(days=90)
            db.session.commit()

            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_insurance

                _check_insurance(app)
                assert not mock_dispatch.called

    def test_medical_expiry_dispatches(self, app):
        uid, tid = _make_user(app, "pilot@med.com", role=Role.PILOT, is_pilot=True)
        with app.app_context():
            from datetime import date, timedelta
            from models import PilotProfile  # pyright: ignore[reportMissingImports]

            db.session.add(
                PilotProfile(
                    user_id=uid,
                    medical_expiry=date.today() + timedelta(days=30),
                )
            )
            db.session.commit()

            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_medical_and_sep

                _check_medical_and_sep(app)
                types_dispatched = [c.args[0] for c in mock_dispatch.call_args_list]
                assert NotificationType.MEDICAL_EXPIRING in types_dispatched

    def test_sep_expiry_dispatches(self, app):
        uid, tid = _make_user(app, "pilot@sep.com", role=Role.PILOT, is_pilot=True)
        with app.app_context():
            from datetime import date, timedelta
            from models import PilotProfile  # pyright: ignore[reportMissingImports]

            db.session.add(
                PilotProfile(
                    user_id=uid,
                    sep_expiry=date.today() + timedelta(days=20),
                )
            )
            db.session.commit()

            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_medical_and_sep

                _check_medical_and_sep(app)
                types_dispatched = [c.args[0] for c in mock_dispatch.call_args_list]
                assert NotificationType.SEP_RATING_EXPIRING in types_dispatched

    def test_document_expiry_dispatches(self, app):
        _uid, tid = _make_user(app, "owner@doc.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid, "OO-DOC")
        with app.app_context():
            from datetime import date, timedelta
            from models import Document  # pyright: ignore[reportMissingImports]

            db.session.add(
                Document(
                    aircraft_id=ac_id,
                    filename="test.pdf",
                    original_filename="test.pdf",
                    title="Test Certificate",
                    valid_until=date.today() + timedelta(days=7),
                )
            )
            db.session.commit()

            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_documents

                _check_documents(app)
                types_dispatched = [c.args[0] for c in mock_dispatch.call_args_list]
                assert NotificationType.DOCUMENT_EXPIRING in types_dispatched

    def test_airworthiness_review_dispatches(self, app):
        _uid, tid = _make_user(app, "owner@aw.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid, "OO-AW")
        with app.app_context():
            from datetime import date, timedelta
            from models import AirworthinessDocument, AirworthinessDocumentStatus  # pyright: ignore[reportMissingImports]

            doc = AirworthinessDocument(
                doc_type="ARC",
                reference="ARC-TEST-001",
            )
            db.session.add(doc)
            db.session.flush()
            db.session.add(
                AirworthinessDocumentStatus(
                    aircraft_id=ac_id,
                    document_id=doc.id,
                    next_review_date=date.today() + timedelta(days=5),
                )
            )
            db.session.commit()

            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_airworthiness_reviews

                _check_airworthiness_reviews(app)
                types_dispatched = [c.args[0] for c in mock_dispatch.call_args_list]
                assert NotificationType.AIRWORTHINESS_REVIEW_DUE in types_dispatched


# ── email_service health tracking – additional paths ──────────────────────────


class TestEmailServiceHealthTrackingExtra:
    def test_record_health_increments_existing_failure_row(self, app):
        with app.app_context():
            from services.email_service import _record_health

            row = db.session.get(AppSetting, "email_consecutive_failures")
            if row:
                row.value = "2"
            else:
                db.session.add(AppSetting(key="email_consecutive_failures", value="2"))
            db.session.commit()

            _record_health(False)

            updated = db.session.get(AppSetting, "email_consecutive_failures")
            assert updated is not None
            assert int(updated.value) == 3

    def test_record_health_creates_failure_row_when_absent(self, app):
        with app.app_context():
            from services.email_service import _record_health

            row = db.session.get(AppSetting, "email_consecutive_failures")
            if row:
                db.session.delete(row)
            db.session.commit()

            _record_health(False)

            created = db.session.get(AppSetting, "email_consecutive_failures")
            assert created is not None
            assert int(created.value) == 1


# ── _user_caps additional role paths ──────────────────────────────────────────


class TestUserCapsExtra:
    def test_maintenance_role_adds_maint_cap(self):
        from unittest.mock import MagicMock
        from services.notification_service import _user_caps  # pyright: ignore[reportMissingImports]

        user = MagicMock(is_pilot=False, is_maintenance=False)
        caps = _user_caps(Role.MAINTENANCE, user)
        assert "is_maint" in caps
        assert "is_owner" not in caps

    def test_instructor_role_adds_pilot_and_maint_caps(self):
        from unittest.mock import MagicMock
        from services.notification_service import _user_caps  # pyright: ignore[reportMissingImports]

        user = MagicMock(is_pilot=False, is_maintenance=False)
        caps = _user_caps(Role.INSTRUCTOR, user)
        assert "is_pilot" in caps
        assert "is_maint" in caps
        assert "is_owner" not in caps


# ── _tenant_display_name ──────────────────────────────────────────────────────


class TestTenantDisplayName:
    def test_none_profile_returns_openhangar(self):
        from services.notification_service import _tenant_display_name  # pyright: ignore[reportMissingImports]

        assert _tenant_display_name(None) == "OpenHangar"

    def test_profile_with_club_name_returns_club_name(self):
        from unittest.mock import MagicMock
        from services.notification_service import _tenant_display_name  # pyright: ignore[reportMissingImports]

        profile = MagicMock(
            club_name="Sky Club", school_name=None, organisation_name=None
        )
        assert _tenant_display_name(profile) == "Sky Club"

    def test_profile_all_none_returns_openhangar(self):
        from unittest.mock import MagicMock
        from services.notification_service import _tenant_display_name  # pyright: ignore[reportMissingImports]

        profile = MagicMock(club_name=None, school_name=None, organisation_name=None)
        assert _tenant_display_name(profile) == "OpenHangar"


# ── _render_email function body ───────────────────────────────────────────────


class TestRenderEmail:
    def test_returns_text_and_html_tuple(self, app):
        with app.app_context():
            with patch("flask.render_template", return_value="<p>body</p>"):
                from services.notification_service import _render_email  # pyright: ignore[reportMissingImports]

                text, html = _render_email(
                    "generic.html",
                    text_body="plain",
                    subject="Subj",
                    notification_title="T",
                    notification_message="M",
                    details=[],
                )
                assert text == "plain"
                assert html == "<p>body</p>"


# ── _text_for cta_url path ────────────────────────────────────────────────────


class TestTextFor:
    def test_cta_url_appended_to_output(self):
        from services.notification_service import _text_for  # pyright: ignore[reportMissingImports]

        result = _text_for(
            "GROUNDING_SNAG_OPENED",
            {
                "notification_title": "Snag",
                "notification_message": "A grounding snag.",
                "cta_url": "https://example.com/snag/42",
            },
        )
        assert "https://example.com/snag/42" in result


# ── dispatch exception paths ──────────────────────────────────────────────────


class TestDispatchEmailExceptions:
    def _ctx(self):
        return {
            "subject": "T",
            "notification_title": "T",
            "notification_message": "M",
            "details": [],
        }

    def test_email_not_configured_stops_loop(self, app):
        from services.email_service import EmailNotConfiguredError  # pyright: ignore[reportMissingImports]

        _uid, tid = _make_user(app, "owner@exc1.com", role=Role.OWNER)
        with app.app_context():
            with (
                patch(
                    "services.notification_service._render_email",
                    return_value=("t", "<p>h</p>"),
                ),
                patch(
                    "services.email_service.send_email",
                    side_effect=EmailNotConfiguredError,
                ),
            ):
                from services.notification_service import dispatch  # pyright: ignore[reportMissingImports]

                dispatch(NotificationType.GROUNDING_SNAG_OPENED, tid, self._ctx())

    def test_email_send_error_is_logged_and_loop_continues(self, app):
        from services.email_service import EmailSendError  # pyright: ignore[reportMissingImports]

        _uid, tid = _make_user(app, "owner@exc2.com", role=Role.OWNER)
        with app.app_context():
            with (
                patch(
                    "services.notification_service._render_email",
                    return_value=("t", "<p>h</p>"),
                ),
                patch(
                    "services.email_service.send_email",
                    side_effect=EmailSendError("smtp err"),
                ),
            ):
                from services.notification_service import dispatch  # pyright: ignore[reportMissingImports]

                dispatch(NotificationType.GROUNDING_SNAG_OPENED, tid, self._ctx())


# ── run_daily_checks ──────────────────────────────────────────────────────────


class TestRunDailyChecks:
    def test_calls_all_five_check_functions(self, app):
        with (
            patch("services.notification_service._check_maintenance") as m1,
            patch("services.notification_service._check_insurance") as m2,
            patch("services.notification_service._check_medical_and_sep") as m3,
            patch("services.notification_service._check_documents") as m4,
            patch("services.notification_service._check_airworthiness_reviews") as m5,
        ):
            from services.notification_service import run_daily_checks  # pyright: ignore[reportMissingImports]

            run_daily_checks(app)
            assert m1.called
            assert m2.called
            assert m3.called
            assert m4.called
            assert m5.called

    def test_exception_in_check_is_swallowed(self, app):
        with patch(
            "services.notification_service._check_maintenance",
            side_effect=RuntimeError("fail"),
        ):
            from services.notification_service import run_daily_checks  # pyright: ignore[reportMissingImports]

            run_daily_checks(app)  # must not raise

    def test_skips_when_advisory_lock_not_acquired(self, app):
        """Another gunicorn worker already holds the daily-checks lock."""
        with (
            patch("services.advisory_lock.advisory_lock_scope") as mock_scope,
            patch("services.notification_service._check_maintenance") as m1,
            patch("services.notification_service._check_insurance") as m2,
        ):
            mock_scope.return_value.__enter__.return_value = False
            from services.notification_service import run_daily_checks  # pyright: ignore[reportMissingImports]

            run_daily_checks(app)
            assert not m1.called
            assert not m2.called


# ── daily checks — skip paths (None values) ───────────────────────────────────


class TestDailyCheckSkipPaths:
    def test_insurance_skips_aircraft_with_null_expiry(self, app):
        _uid, tid = _make_user(app, "owner@ins-null.com", role=Role.OWNER)
        _make_aircraft(app, tid, "OO-NULL-EXP")
        # insurance_expiry is None by default
        with app.app_context():
            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_insurance  # pyright: ignore[reportMissingImports]

                _check_insurance(app)
                assert not mock_dispatch.called

    def test_medical_skips_inactive_user(self, app):
        with app.app_context():
            from datetime import date, timedelta
            from models import PilotProfile  # pyright: ignore[reportMissingImports]

            u = User(
                email="inactive@med.com",
                password_hash=_pw_hash.hash("pw"),
                is_active=False,
                is_pilot=True,
            )
            db.session.add(u)
            db.session.flush()
            db.session.add(
                PilotProfile(
                    user_id=u.id,
                    medical_expiry=date.today() + timedelta(days=30),
                )
            )
            db.session.commit()

            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_medical_and_sep  # pyright: ignore[reportMissingImports]

                _check_medical_and_sep(app)
                assert not mock_dispatch.called

    def test_medical_skips_user_without_tenant_user(self, app):
        with app.app_context():
            from datetime import date, timedelta
            from models import PilotProfile  # pyright: ignore[reportMissingImports]

            u = User(
                email="notu@med.com",
                password_hash=_pw_hash.hash("pw"),
                is_active=True,
                is_pilot=True,
            )
            db.session.add(u)
            db.session.flush()
            # No TenantUser — active user but orphaned from any tenant
            db.session.add(
                PilotProfile(
                    user_id=u.id,
                    medical_expiry=date.today() + timedelta(days=30),
                )
            )
            db.session.commit()

            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_medical_and_sep  # pyright: ignore[reportMissingImports]

                _check_medical_and_sep(app)
                assert not mock_dispatch.called

    def test_documents_skips_null_valid_until(self, app):
        _uid, tid = _make_user(app, "owner@doc-null.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid, "OO-NULL-DOC")
        with app.app_context():
            from models import Document  # pyright: ignore[reportMissingImports]

            db.session.add(
                Document(
                    aircraft_id=ac_id,
                    filename="manual.pdf",
                    original_filename="manual.pdf",
                    title="Manual",
                    valid_until=None,
                )
            )
            db.session.commit()

            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_documents  # pyright: ignore[reportMissingImports]

                _check_documents(app)
                assert not mock_dispatch.called

    def test_airworthiness_skips_null_review_date(self, app):
        _uid, tid = _make_user(app, "owner@aw-null.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid, "OO-NULL-AW")
        with app.app_context():
            from models import AirworthinessDocument, AirworthinessDocumentStatus  # pyright: ignore[reportMissingImports]

            doc = AirworthinessDocument(doc_type="ARC", reference="ARC-NULL-001")
            db.session.add(doc)
            db.session.flush()
            db.session.add(
                AirworthinessDocumentStatus(
                    aircraft_id=ac_id,
                    document_id=doc.id,
                    next_review_date=None,
                )
            )
            db.session.commit()

            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_airworthiness_reviews  # pyright: ignore[reportMissingImports]

                _check_airworthiness_reviews(app)
                assert not mock_dispatch.called


# ── _dispatch_in_context exception path ──────────────────────────────────────


class TestDispatchInContext:
    def test_dispatch_exception_is_swallowed(self, app):
        _uid, tid = _make_user(app, "owner@disp-exc.com", role=Role.OWNER)
        with app.app_context():
            with patch(
                "services.notification_service.dispatch",
                side_effect=RuntimeError("db gone"),
            ):
                from services.notification_service import _dispatch_in_context  # pyright: ignore[reportMissingImports]

                _dispatch_in_context(
                    NotificationType.GROUNDING_SNAG_OPENED,
                    tid,
                    {
                        "subject": "T",
                        "notification_title": "T",
                        "notification_message": "M",
                        "details": [],
                    },
                )  # must not raise


# ── welcome email: no instance admin ─────────────────────────────────────────


class TestWelcomeEmailNoAdmin:
    def test_skips_when_no_instance_admin_exists(self, app):
        with app.app_context():
            row = db.session.get(AppSetting, "welcome_email_sent")
            if row:
                db.session.delete(row)
            db.session.commit()

        with (
            patch("services.email_service.send_email") as mock_send,
            patch.dict(
                __import__("os").environ,
                {"OPENHANGAR_SMTP_HOST": "smtp.example.com"},
                clear=False,
            ),
        ):
            from services.notification_service import send_welcome_email_if_needed  # pyright: ignore[reportMissingImports]

            send_welcome_email_if_needed(app)
            assert not mock_send.called


# ── email_service additional health tracking paths ───────────────────────────


class TestEmailHealthExtra:
    def test_record_health_success_updates_existing_rows(self, app):
        with app.app_context():
            from services.email_service import _record_health  # pyright: ignore[reportMissingImports]

            for key in ("email_last_success_at", "email_consecutive_failures"):
                row = db.session.get(AppSetting, key)
                if row:
                    row.value = "old"
                else:
                    db.session.add(AppSetting(key=key, value="old"))
            db.session.commit()

            _record_health(True)

            failures = db.session.get(AppSetting, "email_consecutive_failures")
            assert failures is not None
            assert failures.value == "0"

    def test_record_health_db_error_is_swallowed(self, app):
        with app.app_context():
            from services.email_service import _record_health  # pyright: ignore[reportMissingImports]

            with patch("models.db.session.commit", side_effect=Exception("db error")):
                _record_health(True)  # must not raise

    def test_get_email_health_db_error_returns_ok(self, app):
        with app.app_context():
            from services.email_service import get_email_health  # pyright: ignore[reportMissingImports]

            with (
                patch.dict(
                    __import__("os").environ,
                    {"OPENHANGAR_SMTP_HOST": "smtp.x.com"},
                    clear=False,
                ),
                patch("models.db.session.get", side_effect=Exception("db error")),
            ):
                result = get_email_health()
                assert result["status"] == "ok"


# ── notification preferences route — edge cases ───────────────────────────────


class TestNotificationPreferencesRouteExtra:
    def test_session_user_not_found_aborts_403(self, app, client):
        with client.session_transaction() as sess:
            sess["user_id"] = 99999
        response = client.get("/config/notifications/")
        assert response.status_code == 403

    def test_post_when_user_has_no_tenant_shows_error(self, app, client):
        with app.app_context():
            u = User(
                email="notenant@edge.com",
                password_hash=_pw_hash.hash("pw"),
                is_active=True,
                is_pilot=True,
            )
            db.session.add(u)
            db.session.commit()
            uid = u.id
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        response = client.post("/config/notifications/", data={}, follow_redirects=True)
        assert b"Cannot save" in response.data

    def test_post_invalid_threshold_uses_none(self, app, client):
        uid, tid = _make_user(app, "owner9@prefs.com", role=Role.OWNER)
        _login(client, uid)
        client.post(
            "/config/notifications/",
            data={
                "enabled_maintenance_due_soon": "on",
                "threshold_maintenance_due_soon": "not-a-number",
            },
            follow_redirects=True,
        )
        with app.app_context():
            pref = NotificationPreference.query.filter_by(
                user_id=uid,
                notification_type=NotificationType.MAINTENANCE_DUE_SOON,
            ).first()
            if pref:
                assert pref.threshold_days is None

    def test_post_deletes_pref_matching_system_default(self, app, client):
        uid, tid = _make_user(app, "owner10@prefs.com", role=Role.OWNER)
        _login(client, uid)
        with app.app_context():
            from datetime import datetime, timezone

            db.session.add(
                NotificationPreference(
                    user_id=uid,
                    tenant_id=tid,
                    notification_type=NotificationType.GROUNDING_SNAG_OPENED,
                    enabled=False,
                    threshold_days=None,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            db.session.commit()
        # POST with system-default value (enabled=True) — deletes the non-default row
        client.post(
            "/config/notifications/",
            data={"enabled_grounding_snag_opened": "on"},
        )
        with app.app_context():
            pref = NotificationPreference.query.filter_by(
                user_id=uid,
                notification_type=NotificationType.GROUNDING_SNAG_OPENED,
            ).first()
            assert pref is None

    def test_post_updates_existing_non_default_pref(self, app, client):
        uid, tid = _make_user(app, "owner11@prefs.com", role=Role.OWNER)
        _login(client, uid)
        with app.app_context():
            from datetime import datetime, timezone

            db.session.add(
                NotificationPreference(
                    user_id=uid,
                    tenant_id=tid,
                    notification_type=NotificationType.MAINTENANCE_DUE_SOON,
                    enabled=True,
                    threshold_days=7,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            db.session.commit()
        client.post(
            "/config/notifications/",
            data={
                "enabled_maintenance_due_soon": "on",
                "threshold_maintenance_due_soon": "21",
            },
        )
        with app.app_context():
            pref = NotificationPreference.query.filter_by(
                user_id=uid,
                notification_type=NotificationType.MAINTENANCE_DUE_SOON,
            ).first()
            assert pref is not None
            assert pref.threshold_days == 21

    def test_get_with_no_tenant_uses_system_defaults(self, app, client):
        with app.app_context():
            u = User(
                email="notenant2@edge.com",
                password_hash=_pw_hash.hash("pw"),
                is_active=True,
                is_pilot=True,
            )
            db.session.add(u)
            db.session.commit()
            uid = u.id
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        response = client.get("/config/notifications/")
        assert response.status_code == 200


# ── _notification_daily_loop try/except body ──────────────────────────────────


class TestNotificationDailyLoop:
    def test_exception_in_daily_check_is_logged(self, app):
        """The try/except block inside the loop catches run_daily_checks failures."""
        call_count = {"n": 0}

        def mock_sleep(_seconds):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                raise StopIteration("exit loop")

        with (
            patch("time.sleep", side_effect=mock_sleep),
            patch(
                "services.notification_service.run_daily_checks",
                side_effect=RuntimeError("check failed"),
            ),
        ):
            from init import _notification_daily_loop  # pyright: ignore[reportMissingImports]

            with contextlib.suppress(
                StopIteration
            ):  # expected — second sleep breaks the loop
                _notification_daily_loop(app, 7, 0)

    def test_next_run_advanced_to_tomorrow_when_time_already_passed(self, app):
        """When run_hour=0 (midnight already passed) next_run is bumped +1 day."""
        # run_hour=0, run_minute=0 → next_run is midnight today, always <= now
        # except at the exact moment of midnight — acceptable for a test.
        sleep_calls = []

        def mock_sleep(seconds):
            sleep_calls.append(seconds)
            raise StopIteration("exit loop")

        with (
            patch("time.sleep", side_effect=mock_sleep),
            patch("services.notification_service.run_daily_checks"),
        ):
            from init import _notification_daily_loop  # pyright: ignore[reportMissingImports]

            with contextlib.suppress(StopIteration):
                _notification_daily_loop(app, 0, 0)

        # Without the +1 day branch, next_run would be midnight today (already past),
        # giving a negative sleep duration.  A positive value proves the branch ran.
        assert sleep_calls, "time.sleep was never called"
        assert sleep_calls[0] > 0, (
            "sleep duration is negative — +1 day branch not taken"
        )
        assert sleep_calls[0] <= 24 * 3600 + 60  # sanity: at most one day ahead

    def test_start_notification_scheduler_creates_named_daemon_thread(self, app):
        from init import _start_notification_scheduler  # pyright: ignore[reportMissingImports]

        with patch("threading.Thread") as MockThread:
            mock_t = MagicMock()
            MockThread.return_value = mock_t
            _start_notification_scheduler(app)
        call_kwargs = MockThread.call_args[1]
        assert call_kwargs["name"] == "notification-daily"
        assert call_kwargs["daemon"] is True
        mock_t.start.assert_called_once()


# ── Per-recipient locale translation in dispatch() ───────────────────────────


class TestDispatchI18n:
    def test_subject_key_formatted_with_args_for_english_user(self, app):
        """dispatch() uses subject_key + subject_args; English user gets key % args."""
        uid, tid = _make_user(app, "owner@i18n-subj.com", role=Role.OWNER)
        with app.app_context():
            subjects_sent = []

            def _capture(to, subject, **kw):
                subjects_sent.append(subject)

            with (
                patch("services.email_service.send_email", side_effect=_capture),
                patch("services.email_service._record_health"),
                patch(
                    "services.notification_service._render_email",
                    return_value=("plain", "<p>html</p>"),
                ),
            ):
                from services.notification_service import dispatch

                dispatch(
                    NotificationType.GROUNDING_SNAG_OPENED,
                    tid,
                    {
                        "subject_key": "Grounding snag reported: %(title)s — %(reg)s",
                        "subject_args": {"title": "Gear collapse", "reg": "OO-TST"},
                        "notification_title_key": "Grounding snag reported: %(title)s",
                        "notification_title_args": {"title": "Gear collapse"},
                        "notification_message_key": "A grounding snag was reported on %(reg)s.",
                        "notification_message_args": {"reg": "OO-TST"},
                        "details": [],
                    },
                )

            assert len(subjects_sent) == 1
            assert "Gear collapse" in subjects_sent[0]
            assert "OO-TST" in subjects_sent[0]

    def test_translated_title_and_message_passed_to_render(self, app):
        """dispatch() passes per-locale notification_title/message to _render_email."""
        uid, tid = _make_user(app, "owner@i18n-render.com", role=Role.OWNER)
        with app.app_context():
            render_ctx: list[dict] = []

            def _capture_render(template, locale="en", text_body="", **ctx):
                render_ctx.append(ctx)
                return ("plain", "<p>html</p>")

            with (
                patch("services.email_service.send_email"),
                patch("services.email_service._record_health"),
                patch(
                    "services.notification_service._render_email",
                    side_effect=_capture_render,
                ),
            ):
                from services.notification_service import dispatch

                dispatch(
                    NotificationType.GROUNDING_SNAG_OPENED,
                    tid,
                    {
                        "subject_key": "Test subject %(x)s",
                        "subject_args": {"x": "val"},
                        "notification_title_key": "Title: %(name)s",
                        "notification_title_args": {"name": "MyTitle"},
                        "notification_message_key": "Message for %(reg)s",
                        "notification_message_args": {"reg": "OO-XYZ"},
                        "details": [],
                    },
                )

            assert render_ctx
            assert render_ctx[0]["notification_title"] == "Title: MyTitle"
            assert render_ctx[0]["notification_message"] == "Message for OO-XYZ"

    def test_backward_compat_plain_subject_still_works(self, app):
        """dispatch() with legacy plain subject/title/message fields still sends email."""
        uid, tid = _make_user(app, "owner@i18n-compat.com", role=Role.OWNER)
        with app.app_context():
            subjects_sent: list[str] = []

            def _capture(to, subject, **kw):
                subjects_sent.append(subject)

            with (
                patch("services.email_service.send_email", side_effect=_capture),
                patch("services.email_service._record_health"),
                patch(
                    "services.notification_service._render_email",
                    return_value=("plain", "<p>html</p>"),
                ),
            ):
                from services.notification_service import dispatch

                dispatch(
                    NotificationType.GROUNDING_SNAG_OPENED,
                    tid,
                    {
                        "subject": "Legacy subject",
                        "notification_title": "Legacy title",
                        "notification_message": "Legacy message",
                        "details": [],
                    },
                )

            assert subjects_sent[0] == "Legacy subject"
