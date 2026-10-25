"""add_sales_observations_table

Revision ID: 9c01d2e3f4a5
Revises: 8b90c1d2e3f4
Create Date: 2026-10-24 20:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c01d2e3f4a5'
down_revision: Union[str, Sequence[str], None] = '8b90c1d2e3f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create sales_observations table for historical demand and stockout capture."""
    op.create_table(
        'sales_observations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('store_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('observation_date', sa.Date(), nullable=False),
        sa.Column('units_sold', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_stockout_day', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['store_id'], ['stores.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('store_id', 'product_id', 'observation_date', name='uq_store_product_date'),
        sa.CheckConstraint('units_sold >= 0', name='chk_sales_units_non_negative'),
    )
    op.create_index(op.f('ix_sales_observations_id'), 'sales_observations', ['id'], unique=False)
    op.create_index(op.f('ix_sales_observations_store_id'), 'sales_observations', ['store_id'], unique=False)
    op.create_index(op.f('ix_sales_observations_product_id'), 'sales_observations', ['product_id'], unique=False)
    op.create_index(op.f('ix_sales_observations_observation_date'), 'sales_observations', ['observation_date'], unique=False)


def downgrade() -> None:
    """Drop sales_observations table."""
    op.drop_index(op.f('ix_sales_observations_observation_date'), table_name='sales_observations')
    op.drop_index(op.f('ix_sales_observations_product_id'), table_name='sales_observations')
    op.drop_index(op.f('ix_sales_observations_store_id'), table_name='sales_observations')
    op.drop_index(op.f('ix_sales_observations_id'), table_name='sales_observations')
    op.drop_table('sales_observations')
