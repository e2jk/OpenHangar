"""add_sole_pilot_and_sole_operator_to_demo_slots

Revision ID: 3f8a2c91b047
Revises: 119bc545da12
Create Date: 2026-05-19 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3f8a2c91b047'
down_revision = '119bc545da12'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('demo_slots', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sole_pilot_user_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('sole_operator_user_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_demo_slots_sole_pilot_user_id',
            'users', ['sole_pilot_user_id'], ['id'],
            ondelete='CASCADE',
        )
        batch_op.create_foreign_key(
            'fk_demo_slots_sole_operator_user_id',
            'users', ['sole_operator_user_id'], ['id'],
            ondelete='CASCADE',
        )


def downgrade():
    with op.batch_alter_table('demo_slots', schema=None) as batch_op:
        batch_op.drop_constraint('fk_demo_slots_sole_operator_user_id', type_='foreignkey')
        batch_op.drop_constraint('fk_demo_slots_sole_pilot_user_id', type_='foreignkey')
        batch_op.drop_column('sole_operator_user_id')
        batch_op.drop_column('sole_pilot_user_id')
