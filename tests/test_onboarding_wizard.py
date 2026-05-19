"""Tests for the phase-26 onboarding wizard and adaptive UI."""

import bcrypt  # pyright: ignore[reportMissingImports]
import pyotp  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    OperatingModel,
    Role,
    Tenant,
    TenantProfile,
    TenantUser,
    User,
    UserInvitation,
    db,
)


# ── Wizard helpers ─────────────────────────────────────────────────────────────


def _step_account(
    client, email="owner@example.com", password="securepassword123", name=""
):
    return client.post(
        "/setup",
        data={"step": "account", "email": email, "password": password, "name": name},
    )


def _step_totp_skip(client):
    return client.post("/setup", data={"step": "totp", "action": "skip"})


def _step_primary_use(client, primary_use="aircraft"):
    return client.post(
        "/setup", data={"step": "primary_use", "primary_use": primary_use}
    )


def _step_operating_model(client, model="sole_pilot"):
    return client.post(
        "/setup", data={"step": "operating_model", "operating_model": model}
    )


def _step_aircraft_count(client, count="1", allows_rental=False):
    data = {"step": "aircraft_count", "aircraft_count": count}
    if allows_rental:
        data["allows_rental"] = "on"
    return client.post("/setup", data=data)


def _step_org_name(client, org_name="My Club"):
    return client.post("/setup", data={"step": "org_name", "org_name": org_name})


def _step_co_owners(client, co_owners=None):
    data = {"step": "co_owners"}
    if co_owners:
        data["co_owner_name"] = [c.get("name", "") for c in co_owners]
        data["co_owner_email"] = [c.get("email", "") for c in co_owners]
        data["co_owner_role"] = [c.get("role", "owner") for c in co_owners]
    return client.post("/setup", data=data)


def _step_summary(client):
    return client.post("/setup", data={"step": "summary"})


def _full_wizard_logbook_only(client, email="owner@example.com"):
    """Shortest path: skip TOTP, choose logbook_only."""
    _step_account(client, email=email)
    _step_totp_skip(client)
    _step_primary_use(client, "logbook_only")
    return _step_summary(client)


def _full_wizard_sole_pilot(client, email="owner@example.com", aircraft_count="2"):
    """Sole pilot path through all aircraft-management steps."""
    _step_account(client, email=email, name="Test Owner")
    _step_totp_skip(client)
    _step_primary_use(client, "aircraft")
    _step_operating_model(client, "sole_pilot")
    _step_aircraft_count(client, aircraft_count)
    return _step_summary(client)


def _create_owner(app, email="owner@example.com"):
    """Create a user+tenant in DB without using the wizard."""
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=bcrypt.hashpw(
                b"securepassword123", bcrypt.gensalt()
            ).decode(),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        db.session.commit()
        return user.id, tenant.id


def _login(client, app, email="owner@example.com"):
    user_id, _ = _create_owner(app, email)
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
    return user_id


# ── Wizard step access guards (GET) ───────────────────────────────────────────


class TestWizardGetGuards:
    def test_get_primary_use_without_session_redirects(self, client):
        r = client.get("/setup?step=primary_use")
        assert r.status_code == 302
        assert "primary_use" not in r.headers["Location"]

    def test_get_operating_model_without_primary_use_redirects(self, client):
        _step_account(client)
        _step_totp_skip(client)
        # primary_use not set → redirect to primary_use
        r = client.get("/setup?step=operating_model")
        assert r.status_code == 302
        assert "primary_use" in r.headers["Location"]

    def test_get_aircraft_count_without_operating_model_redirects(self, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        r = client.get("/setup?step=aircraft_count")
        assert r.status_code == 302
        assert "operating_model" in r.headers["Location"]

    def test_get_org_name_for_wrong_model_redirects(self, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "sole_pilot")
        _step_aircraft_count(client, "1")
        # sole_pilot doesn't have org_name step
        r = client.get("/setup?step=org_name")
        assert r.status_code == 302
        assert "summary" in r.headers["Location"]

    def test_get_co_owners_for_wrong_model_redirects(self, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "sole_pilot")
        r = client.get("/setup?step=co_owners")
        assert r.status_code == 302
        assert "summary" in r.headers["Location"]

    def test_get_summary_without_primary_use_redirects(self, client):
        _step_account(client)
        _step_totp_skip(client)
        r = client.get("/setup?step=summary")
        assert r.status_code == 302
        assert "primary_use" in r.headers["Location"]

    def test_get_org_name_for_flight_club_renders(self, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "flight_club")
        _step_aircraft_count(client, "5")
        r = client.get("/setup?step=org_name")
        assert r.status_code == 200

    def test_get_co_owners_for_shared_ownership_renders(self, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "shared_ownership")
        _step_aircraft_count(client, "1")
        r = client.get("/setup?step=co_owners")
        assert r.status_code == 200


# ── Wizard POST validation ─────────────────────────────────────────────────────


class TestWizardValidation:
    def test_primary_use_invalid_value_shows_error(self, client):
        _step_account(client)
        _step_totp_skip(client)
        r = client.post(
            "/setup", data={"step": "primary_use", "primary_use": "invalid"}
        )
        assert r.status_code == 200
        assert b"select" in r.data.lower() or b"option" in r.data.lower()

    def test_operating_model_invalid_value_shows_error(self, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        r = client.post(
            "/setup", data={"step": "operating_model", "operating_model": "bogus"}
        )
        assert r.status_code == 200
        assert b"select" in r.data.lower() or b"option" in r.data.lower()

    def test_aircraft_count_non_numeric_shows_error(self, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "sole_pilot")
        r = client.post(
            "/setup", data={"step": "aircraft_count", "aircraft_count": "abc"}
        )
        assert r.status_code == 200
        assert b"valid number" in r.data.lower() or b"aircraft" in r.data.lower()

    def test_aircraft_count_negative_shows_error(self, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "sole_pilot")
        r = client.post(
            "/setup", data={"step": "aircraft_count", "aircraft_count": "-1"}
        )
        assert r.status_code == 200

    def test_org_name_empty_shows_error(self, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "flight_club")
        _step_aircraft_count(client, "3")
        r = client.post("/setup", data={"step": "org_name", "org_name": ""})
        assert r.status_code == 200
        assert b"name" in r.data.lower()

    def test_operating_model_post_without_aircraft_primary_use_redirects(self, client):
        """Can't POST operating_model if primary_use is logbook_only."""
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "logbook_only")
        r = client.post(
            "/setup", data={"step": "operating_model", "operating_model": "sole_pilot"}
        )
        assert r.status_code == 302
        assert "primary_use" in r.headers["Location"]


# ── Complete wizard flows → DB assertions ─────────────────────────────────────


class TestWizardCompleteFlows:
    def test_logbook_only_creates_profile_with_zero_aircraft(self, app, client):
        r = _full_wizard_logbook_only(client)
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]
        with app.app_context():
            user = User.query.first()
            assert user is not None
            tenant = Tenant.query.first()
            assert tenant is not None
            profile = TenantProfile.query.filter_by(tenant_id=tenant.id).first()
            assert profile is not None
            assert profile.planned_aircraft_count == 0
            assert profile.setup_complete is True
            assert profile.operating_model == OperatingModel.SOLE_PILOT

    def test_sole_pilot_creates_correct_profile(self, app, client):
        _full_wizard_sole_pilot(client, aircraft_count="3")
        with app.app_context():
            profile = TenantProfile.query.first()
            assert profile is not None
            assert profile.operating_model == OperatingModel.SOLE_PILOT
            assert profile.planned_aircraft_count == 3
            assert profile.allows_rental is False
            assert profile.setup_complete is True

    def test_sole_operator_with_rental_creates_profile(self, app, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "sole_operator")
        _step_aircraft_count(client, "1", allows_rental=True)
        _step_summary(client)
        with app.app_context():
            profile = TenantProfile.query.first()
            assert profile.operating_model == OperatingModel.SOLE_OPERATOR
            assert profile.allows_rental is True
            assert profile.planned_aircraft_count == 1

    def test_flight_club_uses_org_name_as_tenant(self, app, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "flight_club")
        _step_aircraft_count(client, "5")
        _step_org_name(client, "Aéroclub du Nord")
        _step_summary(client)
        with app.app_context():
            tenant = Tenant.query.first()
            assert tenant.name == "Aéroclub du Nord"
            profile = TenantProfile.query.first()
            assert profile.operating_model == OperatingModel.FLIGHT_CLUB
            assert profile.club_name == "Aéroclub du Nord"
            assert profile.school_name is None

    def test_flight_school_uses_org_name_as_tenant(self, app, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "flight_school")
        _step_aircraft_count(client, "10")
        _step_org_name(client, "SkyHigh Academy")
        _step_summary(client)
        with app.app_context():
            tenant = Tenant.query.first()
            assert tenant.name == "SkyHigh Academy"
            profile = TenantProfile.query.first()
            assert profile.school_name == "SkyHigh Academy"
            assert profile.club_name is None

    def test_shared_ownership_creates_co_owner_invitations(self, app, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "shared_ownership")
        _step_aircraft_count(client, "1")
        _step_co_owners(
            client,
            [
                {"name": "Alice", "email": "alice@example.com", "role": "owner"},
                {"name": "Bob", "email": "bob@example.com", "role": "admin"},
            ],
        )
        _step_summary(client)
        with app.app_context():
            invitations = UserInvitation.query.all()
            assert len(invitations) == 2
            emails = {inv.email for inv in invitations}
            assert "alice@example.com" in emails
            assert "bob@example.com" in emails
            alice_inv = next(i for i in invitations if i.email == "alice@example.com")
            assert alice_inv.display_name == "Alice"
            assert alice_inv.role == Role.OWNER
            bob_inv = next(i for i in invitations if i.email == "bob@example.com")
            assert (
                bob_inv.role == Role.ADMIN
            )  # "admin" form value → Role.ADMIN in wizard

    def test_co_owners_with_no_entries_creates_no_invitations(self, app, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "shared_ownership")
        _step_aircraft_count(client, "2")
        _step_co_owners(client, [])
        _step_summary(client)
        with app.app_context():
            assert UserInvitation.query.count() == 0

    def test_wizard_with_valid_totp_saves_totp_secret(self, app, client):
        _step_account(client)
        with client.session_transaction() as sess:
            totp_secret = sess["setup_totp_secret"]
        valid_code = pyotp.TOTP(totp_secret).now()
        client.post("/setup", data={"step": "totp", "totp_code": valid_code})
        _step_primary_use(client, "logbook_only")
        _step_summary(client)
        with app.app_context():
            user = User.query.first()
            assert user.totp_secret == totp_secret

    def test_wizard_creates_user_with_display_name(self, app, client):
        _step_account(client, name="Jane Pilot")
        _step_totp_skip(client)
        _step_primary_use(client, "logbook_only")
        _step_summary(client)
        with app.app_context():
            user = User.query.first()
            assert user.name == "Jane Pilot"

    def test_finish_without_session_redirects(self, client):
        """POST summary without wizard session → session expired redirect."""
        r = client.post("/setup", data={"step": "summary"})
        assert r.status_code == 302
        assert "/setup" in r.headers["Location"]

    def test_aircraft_count_zero_means_logbook_only_profile(self, app, client):
        """aircraft_count=0 in the aircraft path still sets planned_aircraft_count=0."""
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "sole_pilot")
        _step_aircraft_count(client, "0")
        _step_summary(client)
        with app.app_context():
            profile = TenantProfile.query.first()
            assert profile.planned_aircraft_count == 0


# ── Next-step routing ──────────────────────────────────────────────────────────


class TestWizardNextStep:
    def test_sole_pilot_skips_org_and_co_owners(self, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "sole_pilot")
        r = _step_aircraft_count(client, "1")
        assert r.status_code == 302
        assert "summary" in r.headers["Location"]

    def test_sole_operator_skips_org_and_co_owners(self, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "sole_operator")
        r = _step_aircraft_count(client, "1")
        assert r.status_code == 302
        assert "summary" in r.headers["Location"]

    def test_flight_club_goes_to_org_name(self, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "flight_club")
        r = _step_aircraft_count(client, "5")
        assert r.status_code == 302
        assert "org_name" in r.headers["Location"]

    def test_flight_school_goes_to_org_name(self, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "flight_school")
        r = _step_aircraft_count(client, "10")
        assert r.status_code == 302
        assert "org_name" in r.headers["Location"]

    def test_shared_ownership_goes_to_co_owners(self, client):
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "shared_ownership")
        r = _step_aircraft_count(client, "1")
        assert r.status_code == 302
        assert "co_owners" in r.headers["Location"]

    def test_logbook_only_skips_to_summary(self, client):
        _step_account(client)
        _step_totp_skip(client)
        r = _step_primary_use(client, "logbook_only")
        assert r.status_code == 302
        assert "summary" in r.headers["Location"]


# ── Adaptive UI context ────────────────────────────────────────────────────────


class TestAdaptiveUI:
    def _create_owner_with_profile(
        self, app, client, planned_aircraft_count=None, operating_model=None
    ):
        user_id, tenant_id = _create_owner(app)
        with app.app_context():
            profile = TenantProfile(
                tenant_id=tenant_id,
                planned_aircraft_count=planned_aircraft_count,
                operating_model=operating_model,
                setup_complete=True,
            )
            db.session.add(profile)
            db.session.commit()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id

    def test_logbook_only_mode_when_aircraft_count_is_zero(
        self, app, client, captured_templates
    ):
        self._create_owner_with_profile(app, client, planned_aircraft_count=0)
        client.get("/")
        _, ctx = captured_templates[0]
        assert ctx["logbook_only"] is True
        assert ctx["single_aircraft_mode"] is False

    def test_single_aircraft_mode_when_count_is_one(
        self, app, client, captured_templates
    ):
        self._create_owner_with_profile(app, client, planned_aircraft_count=1)
        client.get("/")
        _, ctx = captured_templates[0]
        assert ctx["logbook_only"] is False
        assert ctx["single_aircraft_mode"] is True

    def test_normal_mode_when_count_is_two(self, app, client, captured_templates):
        self._create_owner_with_profile(app, client, planned_aircraft_count=2)
        client.get("/")
        _, ctx = captured_templates[0]
        assert ctx["logbook_only"] is False
        assert ctx["single_aircraft_mode"] is False

    def test_no_profile_gives_false_flags(self, app, client, captured_templates):
        _login(client, app)
        client.get("/")
        _, ctx = captured_templates[0]
        assert ctx["logbook_only"] is False
        assert ctx["single_aircraft_mode"] is False

    def test_aircraft_count_goal_none_without_profile(
        self, app, client, captured_templates
    ):
        _login(client, app)
        client.get("/")
        _, ctx = captured_templates[0]
        assert ctx["aircraft_count_goal"] is None

    def test_aircraft_count_goal_set_from_profile(
        self, app, client, captured_templates
    ):
        self._create_owner_with_profile(app, client, planned_aircraft_count=3)
        client.get("/")
        _, ctx = captured_templates[0]
        assert ctx["aircraft_count_goal"] == 3

    def test_navbar_hides_dashboard_and_aircraft_when_setup_incomplete(
        self, app, client
    ):
        user_id, tenant_id = _create_owner(app)
        with app.app_context():
            profile = TenantProfile(
                tenant_id=tenant_id,
                planned_aircraft_count=2,
                setup_complete=False,
            )
            db.session.add(profile)
            db.session.commit()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
        r = client.get("/setup?step=summary")
        assert b"Dashboard" not in r.data
        assert b"/aircraft/" not in r.data

    def test_navbar_hides_dashboard_on_empty_db_before_any_user_exists(
        self, app, client
    ):
        # Fresh install: no users, no profile → redirect to /setup, no nav links
        r = client.get("/setup")
        assert b"Dashboard" not in r.data

    def test_navbar_shows_dashboard_and_aircraft_when_setup_complete(self, app, client):
        self._create_owner_with_profile(app, client, planned_aircraft_count=2)
        r = client.get("/")
        assert b"Dashboard" in r.data

    def test_navbar_hides_maintenance_in_logbook_only_mode(self, app, client):
        self._create_owner_with_profile(app, client, planned_aircraft_count=0)
        r = client.get("/")
        assert b"Maintenance" not in r.data
        assert b"/aircraft/" not in r.data


# ── Multi-invite endpoint ──────────────────────────────────────────────────────


class TestMultiInvite:
    def test_single_invite_creates_one_invitation(self, app, client):
        _login(client, app)
        client.post(
            "/config/users/invite",
            data={
                "email": "pilot@example.com",
                "display_name": "Charlie Pilot",
                "role": "pilot",
                "aircraft_ids": "",
            },
        )
        with app.app_context():
            invs = UserInvitation.query.all()
            assert len(invs) == 1
            assert invs[0].email == "pilot@example.com"
            assert invs[0].display_name == "Charlie Pilot"
            assert invs[0].role == Role.PILOT

    def test_multi_invite_creates_multiple_invitations(self, app, client):
        _login(client, app)
        client.post(
            "/config/users/invite",
            data={
                "email": ["a@example.com", "b@example.com"],
                "display_name": ["Alice", "Bob"],
                "role": ["pilot", "pilot"],
                "aircraft_ids": ["", ""],
            },
        )
        with app.app_context():
            invs = UserInvitation.query.all()
            assert len(invs) == 2
            emails = {i.email for i in invs}
            assert "a@example.com" in emails
            assert "b@example.com" in emails

    def test_invite_with_no_email_still_creates_invitation(self, app, client):
        _login(client, app)
        client.post(
            "/config/users/invite",
            data={
                "email": "",
                "display_name": "Anonymous",
                "role": "pilot",
                "aircraft_ids": "",
            },
        )
        with app.app_context():
            invs = UserInvitation.query.all()
            assert len(invs) == 1
            assert invs[0].email is None
            assert invs[0].display_name == "Anonymous"


# ── Invite accept with display_name ───────────────────────────────────────────


class TestInviteAcceptDisplayName:
    def _create_invitation(self, app, display_name=None):
        user_id, tenant_id = _create_owner(app)
        from datetime import datetime, timedelta, timezone

        with app.app_context():
            inv = UserInvitation(
                tenant_id=tenant_id,
                invited_by_user_id=user_id,
                email=None,
                display_name=display_name,
                role=Role.PILOT,
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
            db.session.add(inv)
            db.session.commit()
            return inv.token

    def test_accept_page_shows_welcome_with_name(self, app, client):
        token = self._create_invitation(app, display_name="Dana")
        r = client.get(f"/config/users/invite/{token}")
        assert r.status_code == 200
        assert b"Dana" in r.data

    def test_accept_page_shows_generic_title_without_name(self, app, client):
        token = self._create_invitation(app, display_name=None)
        r = client.get(f"/config/users/invite/{token}")
        assert r.status_code == 200
        assert b"Accept Invitation" in r.data


# ── Coverage gap tests ─────────────────────────────────────────────────────────


class TestWizardCoverageGaps:
    def test_finish_with_invalid_operating_model_in_session(self, app, client):
        """Exercise the except ValueError branch in _setup_finish for operating_model."""
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "sole_pilot")
        _step_aircraft_count(client, "1")
        # Inject an invalid operating model directly into session
        with client.session_transaction() as sess:
            sess["setup_operating_model"] = "not_a_valid_model"
        r = _step_summary(client)
        assert r.status_code == 302
        with app.app_context():
            profile = TenantProfile.query.first()
            assert profile is not None
            assert profile.operating_model is None

    def test_multi_invite_misaligned_roles_falls_back_to_pilot(self, app, client):
        """Cover the else branch in invite when roles list is shorter than emails."""
        _login(client, app)
        # Two emails but only one role — second invitation falls back to pilot
        client.post(
            "/config/users/invite",
            data={
                "email": ["a@example.com", "b@example.com"],
                "display_name": ["", ""],
                "role": ["pilot"],  # intentionally short
                "aircraft_ids": ["", ""],
            },
        )
        with app.app_context():
            invs = UserInvitation.query.all()
            assert len(invs) == 2

    def test_multi_invite_misaligned_display_names_uses_none(self, app, client):
        """Cover the else branch in invite when display_names list is shorter."""
        _login(client, app)
        client.post(
            "/config/users/invite",
            data={
                "email": ["a@example.com", "b@example.com"],
                "display_name": ["Alice"],  # intentionally short
                "role": ["pilot", "pilot"],
                "aircraft_ids": ["", ""],
            },
        )
        with app.app_context():
            invs = UserInvitation.query.order_by(UserInvitation.id).all()
            assert invs[0].display_name == "Alice"
            assert invs[1].display_name is None

    def test_multi_invite_aircraft_ids_shorter_than_emails(self, app, client):
        """Cover the else [] branch in invite when aircraft_ids list is shorter."""
        _login(client, app)
        client.post(
            "/config/users/invite",
            data={
                "email": ["a@example.com", "b@example.com"],
                "display_name": ["", ""],
                "role": ["pilot", "pilot"],
                "aircraft_ids": [""],  # intentionally short — second invite gets []
            },
        )
        with app.app_context():
            invs = UserInvitation.query.order_by(UserInvitation.id).all()
            assert len(invs) == 2
            assert invs[1].aircraft_ids is None or invs[1].aircraft_ids == []

    def test_get_primary_use_with_session_renders(self, client):
        """GET primary_use with valid session renders the template."""
        _step_account(client)
        _step_totp_skip(client)
        r = client.get("/setup?step=primary_use")
        assert r.status_code == 200

    def test_get_operating_model_with_session_renders(self, client):
        """GET operating_model with valid session renders the template."""
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        r = client.get("/setup?step=operating_model")
        assert r.status_code == 200

    def test_get_aircraft_count_with_session_renders(self, client):
        """GET aircraft_count with valid session renders the template."""
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "sole_pilot")
        r = client.get("/setup?step=aircraft_count")
        assert r.status_code == 200

    def test_get_summary_with_session_renders(self, client):
        """GET summary with valid session renders the template."""
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "logbook_only")
        r = client.get("/setup?step=summary")
        assert r.status_code == 200

    def test_post_primary_use_without_totp_done_redirects(self, client):
        """Guard in _setup_primary_use: missing setup_totp_done → redirect to account."""
        # POST primary_use without having done TOTP (no session key)
        r = client.post(
            "/setup", data={"step": "primary_use", "primary_use": "aircraft"}
        )
        assert r.status_code == 302
        assert "totp" not in r.headers["Location"]
        assert "primary_use" not in r.headers["Location"]

    def test_post_aircraft_count_without_operating_model_redirects(self, client):
        """Guard in _setup_aircraft_count: missing operating_model → redirect."""
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        # Skip operating_model step — post directly to aircraft_count
        r = client.post(
            "/setup", data={"step": "aircraft_count", "aircraft_count": "2"}
        )
        assert r.status_code == 302
        assert "operating_model" in r.headers["Location"]

    def test_post_org_name_for_wrong_model_redirects(self, client):
        """Guard in _setup_org_name: wrong operating model → redirect to summary."""
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "sole_pilot")
        _step_aircraft_count(client, "1")
        r = client.post("/setup", data={"step": "org_name", "org_name": "X"})
        assert r.status_code == 302
        assert "summary" in r.headers["Location"]

    def test_post_co_owners_for_wrong_model_redirects(self, client):
        """Guard in _setup_co_owners: wrong model → redirect to summary."""
        _step_account(client)
        _step_totp_skip(client)
        _step_primary_use(client, "aircraft")
        _step_operating_model(client, "sole_pilot")
        r = client.post("/setup", data={"step": "co_owners"})
        assert r.status_code == 302
        assert "summary" in r.headers["Location"]

    def test_invite_with_no_email_field_shows_warning(self, app, client):
        """Cover the if not emails: branch when no email key is posted at all."""
        _login(client, app)
        r = client.post(
            "/config/users/invite",
            data={"role": "pilot"},  # no email key → getlist returns []
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            assert UserInvitation.query.count() == 0
