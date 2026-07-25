"""Unify airframe and pilot logbook into a single Flight table

Replaces the FlightEntry + FlightCrew + PilotLogbookEntry three-table split
with one `flights` table (renamed from `flight_entries`) carrying both the
airframe-log fields and the EASA pilot-log figures, plus two crew identity
slots (pic_user_id/pic_name, second_crew_user_id/second_crew_name/
second_crew_role). See docs/backlog.md "Major refactor: unify the airframe
and pilot logbook into one record" for the full design rationale.

Breaking change, no automated data-migration path (pre-1.0, no known other
instance to preserve): `flight_crew` and `pilot_logbook_entries` are dropped
outright if empty; if either still has rows, both tables are left fully
intact and untouched (orphaned — nothing in the app reads from them after
this migration) and an `legacy_logbook_data_present` AppSetting flag is set
instead, driving an admin-only banner (see app/utils.py
check_legacy_logbook_data) that points affected operators at filing an
issue for manual extraction help.

Revision ID: 893349d2be22
Revises: 8a3da16e7596
Create Date: 2026-07-24 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "893349d2be22"
down_revision = "8a3da16e7596"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    op.rename_table("flight_entries", "flights")

    with op.batch_alter_table("flights", schema=None) as batch_op:
        batch_op.alter_column("aircraft_id", existing_type=sa.Integer(), nullable=True)
        batch_op.alter_column(
            "departure_icao",
            existing_type=sa.String(length=4),
            type_=sa.String(length=64),
            nullable=True,
        )
        batch_op.alter_column(
            "arrival_icao",
            existing_type=sa.String(length=4),
            type_=sa.String(length=64),
            nullable=True,
        )
        batch_op.drop_index("ix_flight_entries_aircraft_id_date_id")

        batch_op.add_column(
            sa.Column("other_aircraft_type", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("other_aircraft_type_icao", sa.String(length=16), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "other_aircraft_registration", sa.String(length=16), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column("night_time", sa.Numeric(precision=4, scale=1), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "instrument_time", sa.Numeric(precision=4, scale=1), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column("cross_country", sa.Numeric(precision=4, scale=1), nullable=True)
        )
        batch_op.add_column(sa.Column("landings_day", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("landings_night", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "single_pilot_se", sa.Numeric(precision=4, scale=1), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column(
                "single_pilot_me", sa.Numeric(precision=4, scale=1), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column("multi_pilot", sa.Numeric(precision=4, scale=1), nullable=True)
        )
        batch_op.add_column(
            sa.Column("function_pic", sa.Numeric(precision=4, scale=1), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "function_copilot", sa.Numeric(precision=4, scale=1), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column("function_dual", sa.Numeric(precision=4, scale=1), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "function_instructor", sa.Numeric(precision=4, scale=1), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column(
                "entry_type",
                sa.String(length=16),
                nullable=False,
                server_default="flight",
            )
        )
        batch_op.add_column(sa.Column("fstd_type", sa.String(length=16), nullable=True))
        batch_op.add_column(
            sa.Column("fstd_duration", sa.Numeric(precision=4, scale=1), nullable=True)
        )
        batch_op.add_column(sa.Column("pic_user_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("pic_name", sa.String(length=128), nullable=True))
        batch_op.add_column(
            sa.Column("second_crew_user_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("second_crew_name", sa.String(length=128), nullable=True)
        )
        batch_op.add_column(
            sa.Column("second_crew_role", sa.String(length=16), nullable=True)
        )
        batch_op.add_column(sa.Column("import_batch_id", sa.Integer(), nullable=True))

        batch_op.create_foreign_key(
            "fk_flights_pic_user_id_users",
            "users",
            ["pic_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_flights_second_crew_user_id_users",
            "users",
            ["second_crew_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_flights_import_batch_id_logbook_import_batches",
            "logbook_import_batches",
            ["import_batch_id"],
            ["id"],
            ondelete="SET NULL",
        )

        batch_op.create_index(
            "ix_flights_aircraft_id_date_id",
            [
                "aircraft_id",
                sa.literal_column("date DESC"),
                sa.literal_column("id DESC"),
            ],
        )
        batch_op.create_index(
            "ix_flights_pic_user_id_date_id",
            [
                "pic_user_id",
                sa.literal_column("date DESC"),
                sa.literal_column("id DESC"),
            ],
        )
        batch_op.create_index(
            "ix_flights_second_crew_user_id_date_id",
            [
                "second_crew_user_id",
                sa.literal_column("date DESC"),
                sa.literal_column("id DESC"),
            ],
        )

    with op.batch_alter_table("flights", schema=None) as batch_op:
        batch_op.alter_column("entry_type", server_default=None)

    fc_count = conn.execute(sa.text("SELECT COUNT(*) FROM flight_crew")).scalar()
    ple_count = conn.execute(
        sa.text("SELECT COUNT(*) FROM pilot_logbook_entries")
    ).scalar()
    if not fc_count and not ple_count:
        op.drop_table("flight_crew")
        op.drop_table("pilot_logbook_entries")
    else:
        conn.execute(
            sa.text(
                "INSERT INTO app_settings (key, value) "
                "VALUES ('legacy_logbook_data_present', 'true') "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM app_settings WHERE key = 'legacy_logbook_data_present'")
    )

    # Only recreated empty — this migration never drops flight_crew/
    # pilot_logbook_entries when they still had rows, so a downgrade after
    # that path is a no-op for those two (they're already still there).
    op.create_table(
        "pilot_logbook_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pilot_user_id", sa.Integer(), nullable=False),
        sa.Column("flight_id", sa.Integer(), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("aircraft_type", sa.String(length=64), nullable=True),
        sa.Column("aircraft_type_icao", sa.String(length=16), nullable=True),
        sa.Column("aircraft_registration", sa.String(length=16), nullable=True),
        sa.Column("departure_place", sa.String(length=64), nullable=True),
        sa.Column("departure_time", sa.Time(), nullable=True),
        sa.Column("arrival_place", sa.String(length=64), nullable=True),
        sa.Column("arrival_time", sa.Time(), nullable=True),
        sa.Column("pic_name", sa.String(length=128), nullable=True),
        sa.Column("night_time", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("instrument_time", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("cross_country", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("landings_day", sa.Integer(), nullable=True),
        sa.Column("landings_night", sa.Integer(), nullable=True),
        sa.Column("single_pilot_se", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("single_pilot_me", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("multi_pilot", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("function_pic", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("function_copilot", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("function_dual", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column(
            "function_instructor", sa.Numeric(precision=4, scale=1), nullable=True
        ),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column(
            "entry_type",
            sa.String(length=16),
            nullable=False,
            server_default="flight",
        ),
        sa.Column("fstd_type", sa.String(length=16), nullable=True),
        sa.Column("fstd_duration", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.Column("import_batch_id", sa.Integer(), nullable=True),
        sa.Column("gps_batch_id", sa.Integer(), nullable=True),
        sa.Column("gps_track_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["pilot_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["import_batch_id"], ["logbook_import_batches.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["gps_batch_id"], ["aircraft_gps_import_batches.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["gps_track_id"], ["gps_tracks.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "flight_crew",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("flight_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    with op.batch_alter_table("flights", schema=None) as batch_op:
        batch_op.drop_index("ix_flights_second_crew_user_id_date_id")
        batch_op.drop_index("ix_flights_pic_user_id_date_id")
        batch_op.drop_index("ix_flights_aircraft_id_date_id")
        batch_op.drop_constraint(
            "fk_flights_import_batch_id_logbook_import_batches", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_flights_second_crew_user_id_users", type_="foreignkey"
        )
        batch_op.drop_constraint("fk_flights_pic_user_id_users", type_="foreignkey")
        batch_op.drop_column("import_batch_id")
        batch_op.drop_column("second_crew_role")
        batch_op.drop_column("second_crew_name")
        batch_op.drop_column("second_crew_user_id")
        batch_op.drop_column("pic_name")
        batch_op.drop_column("pic_user_id")
        batch_op.drop_column("fstd_duration")
        batch_op.drop_column("fstd_type")
        batch_op.drop_column("entry_type")
        batch_op.drop_column("function_instructor")
        batch_op.drop_column("function_dual")
        batch_op.drop_column("function_copilot")
        batch_op.drop_column("function_pic")
        batch_op.drop_column("multi_pilot")
        batch_op.drop_column("single_pilot_me")
        batch_op.drop_column("single_pilot_se")
        batch_op.drop_column("landings_night")
        batch_op.drop_column("landings_day")
        batch_op.drop_column("cross_country")
        batch_op.drop_column("instrument_time")
        batch_op.drop_column("night_time")
        batch_op.drop_column("other_aircraft_registration")
        batch_op.drop_column("other_aircraft_type_icao")
        batch_op.drop_column("other_aircraft_type")
        batch_op.alter_column(
            "arrival_icao",
            existing_type=sa.String(length=64),
            type_=sa.String(length=4),
            nullable=False,
        )
        batch_op.alter_column(
            "departure_icao",
            existing_type=sa.String(length=64),
            type_=sa.String(length=4),
            nullable=False,
        )
        batch_op.alter_column("aircraft_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index(
            "ix_flight_entries_aircraft_id_date_id", ["aircraft_id", "date", "id"]
        )

    op.rename_table("flights", "flight_entries")
