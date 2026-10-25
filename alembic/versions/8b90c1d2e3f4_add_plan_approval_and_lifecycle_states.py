"""add_plan_approval_and_lifecycle_states

Revision ID: 8b90c1d2e3f4
Revises: 7a89b1c2d3e4
Create Date: 2026-10-24 19:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8b90c1d2e3f4'
down_revision: Union[str, Sequence[str], None] = '7a89b1c2d3e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema for approved_by_user_id and approved_at on distribution_plans."""
    with op.batch_alter_table('distribution_plans') as batch_op:
        batch_op.add_column(
            sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column('approved_by_user_id', sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            'fk_distribution_plans_approved_by_user_id',
            'users',
            ['approved_by_user_id'],
            ['id'],
            ondelete='SET NULL'
        )
        batch_op.create_index(
            op.f('ix_distribution_plans_approved_by_user_id'),
            ['approved_by_user_id'],
            unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('distribution_plans') as batch_op:
        batch_op.drop_index(op.f('ix_distribution_plans_approved_by_user_id'))
        batch_op.drop_constraint('fk_distribution_plans_approved_by_user_id', type_='foreignkey')
        batch_op.drop_column('approved_by_user_id')
        batch_op.drop_column('approved_at')

