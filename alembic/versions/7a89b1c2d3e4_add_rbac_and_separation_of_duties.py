"""add_rbac_and_separation_of_duties

Revision ID: 7a89b1c2d3e4
Revises: 6d06776c41b1
Create Date: 2026-10-24 19:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a89b1c2d3e4'
down_revision: Union[str, Sequence[str], None] = '6d06776c41b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema for RBAC roles and separation of duties tracking."""
    # Add created_by_user_id to distribution_plans using batch operations
    with op.batch_alter_table('distribution_plans') as batch_op:
        batch_op.add_column(
            sa.Column('created_by_user_id', sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            'fk_distribution_plans_created_by_user_id',
            'users',
            ['created_by_user_id'],
            ['id'],
            ondelete='SET NULL'
        )
        batch_op.create_index(
            op.f('ix_distribution_plans_created_by_user_id'),
            ['created_by_user_id'],
            unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('distribution_plans') as batch_op:
        batch_op.drop_index(op.f('ix_distribution_plans_created_by_user_id'))
        batch_op.drop_constraint('fk_distribution_plans_created_by_user_id', type_='foreignkey')
        batch_op.drop_column('created_by_user_id')

