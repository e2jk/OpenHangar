"""Formula-like user text must not become an executable CSV cell."""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal

import pw_hash
import pytest
from models import (
    Aircraft,
    AircraftOwner,
    BillingAccountKind,
    LedgerEntryType,
    OperatingModel,
    Role,
    Tenant,
    TenantProfile,
    TenantUser,
    User,
    db,
)
from services.billing import BillingService
from services.csv_export import safe_csv_text


@pytest.mark.parametrize(
    "text",
    [
        '=HYPERLINK("https://example.invalid","pilot")',
        "+SUM(1,2)",
        "-1+2",
        "@SUM(1,2)",
        "＝1+1",
        "＋1+1",
        "－1+1",
        "＠SUM(1,2)",
        "  =1+1",
        "\u202f+1+1",
        "\ufeff \x00\t@SUM(1,2)",
        "\x1f=1+1",
        "\tordinary text",
        "\rordinary text",
        "\nordinary text",
    ],
)
def test_formula_and_control_prefixes_are_text(text):
    assert safe_csv_text(text) == "'" + text


@pytest.mark.parametrize(
    "text",
    [
        "",
        " ",
        "\ufeff",
        "OO-ABC",
        "Joséphine",
        'Fuel, oil and "parts"',
        "flight\nnotes",
        "flight;=1+1",
        "already '=1+1",
        "'=1+1",
        "  Pilot",
    ],
)
def test_ordinary_text_is_unchanged(text):
    assert safe_csv_text(text) == text


@pytest.fixture()
def csv_world(app, client):
    with app.app_context():
        tenant = Tenant(name="CSV test")
        db.session.add(tenant)
        db.session.flush()
        db.session.add(
            TenantProfile(
                tenant_id=tenant.id,
                operating_model=OperatingModel.SHARED_OWNERSHIP,
                allows_rental=True,
                setup_complete=True,
            )
        )
        user = User(
            email="csv@example.com",
            password_hash=pw_hash.hash("CSVPassword123!"),
            name='=HYPERLINK("https://example.invalid","Pilot")',
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        aircraft = Aircraft(
            tenant_id=tenant.id, registration="OO-CSV", make="Cessna", model="172S"
        )
        db.session.add(aircraft)
        db.session.flush()
        db.session.add(
            AircraftOwner(
                aircraft_id=aircraft.id,
                user_id=user.id,
                share_pct=Decimal(100),
                buy_in_amount=Decimal(0),
            )
        )
        description = '\t=HYPERLINK("https://example.invalid","é, notes")\nsecond line'
        ordinary = 'Fuel, oil and "pièces"'
        for kind, aircraft_id in [
            (BillingAccountKind.RENTER, None),
            (BillingAccountKind.CO_OWNER, aircraft.id),
        ]:
            account = BillingService.get_or_create_account(
                tenant.id, user.id, kind, aircraft_id
            )
            BillingService.post(
                account,
                LedgerEntryType.OPENING,
                -8.75,
                "Opening",
                date.today() - timedelta(days=400),
            )
            BillingService.post(
                account, LedgerEntryType.CHARGE, 13.75, description, date.today()
            )
            BillingService.post(
                account, LedgerEntryType.PAYMENT, -20, ordinary, date.today()
            )
        db.session.commit()
        uid, acid = user.id, aircraft.id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid, acid, description, ordinary


@pytest.mark.parametrize(
    "route", ["renter-owner", "renter-self", "co-owner", "co-self"]
)
def test_all_statement_downloads_protect_text_without_altering_amounts(
    app, client, csv_world, route
):
    uid, acid, description, ordinary = csv_world
    paths = {
        "renter-owner": f"/config/renters/{uid}/account/statement.csv",
        "renter-self": "/my/account/statement.csv",
        "co-owner": f"/aircraft/{acid}/owners/{uid}/account/statement.csv",
        "co-self": f"/aircraft/{acid}/my-share/statement.csv",
    }
    response = client.get(paths[route])
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
    metadata = {row[0]: row[1] for row in rows[:5]}
    assert metadata["Exporter"] == '\'=HYPERLINK("https://example.invalid","Pilot")'
    assert metadata["Account holder"] == metadata["Exporter"]
    assert rows[6] == ["Opening balance", "", "", "", "-8.75"]
    assert rows[8][2:] == ["'" + description, "13.75", "5.00"]
    assert rows[9][2:] == [ordinary, "-20.00", "-15.00"]
    assert rows[10] == ["Closing balance", "", "", "", "-15.00"]
    # Text was escaped at export time, never changed in the application database.
    with app.app_context():
        assert db.session.get(User, uid).name.startswith("=HYPERLINK")


@pytest.mark.parametrize(
    "registration", ["=1+1", "+SUM(1,2)", "-1+2", "@SUM(1,2)", "＝1+1"]
)
def test_utilization_download_protects_aircraft_registration(
    app, client, csv_world, registration
):
    _uid, acid, _description, _ordinary = csv_world
    with app.app_context():
        db.session.get(Aircraft, acid).registration = registration
        db.session.commit()
    response = client.get(f"/aircraft/{acid}/reports/utilization.csv")
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
    assert rows[0] == ["Aircraft", "'" + registration]
