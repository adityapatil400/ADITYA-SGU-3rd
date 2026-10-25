"""complete_schema_sync_and_authoritative_nodes

Revision ID: 902fdd865d81
Revises: a01b2c3d4e5f
Create Date: 2026-10-24 23:28:27.343724

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '902fdd865d81'
down_revision: Union[str, Sequence[str], None] = 'a01b2c3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    # In SQLite batch mode, dependent views must be temporarily dropped during table renames
    op.execute("DROP VIEW IF EXISTS plan_runs;")
    op.execute("DROP VIEW IF EXISTS audit_events;")
    op.execute("DROP VIEW IF EXISTS allocation_lines;")
    op.execute("DROP VIEW IF EXISTS reservations;")
    op.execute("DROP VIEW IF EXISTS orders;")
    op.execute("DROP VIEW IF EXISTS order_items;")
    op.execute("DROP VIEW IF EXISTS retailers;")
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_distribution_plans;")

    if 'distribution_nodes' not in tables:
        op.create_table('distribution_nodes',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('code', sa.String(length=50), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('node_type', sa.Enum('CENTRAL_HUB', 'REGIONAL_DC', 'RETAIL_STORE', 'MICRO_FULFILLMENT', name='node_type_enum'), nullable=False),
            sa.Column('region', sa.String(length=50), nullable=False),
            sa.Column('capacity_limit', sa.Integer(), nullable=False),
            sa.Column('is_active', sa.Boolean(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('distribution_nodes', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_distribution_nodes_code'), ['code'], unique=True)
            batch_op.create_index(batch_op.f('ix_distribution_nodes_id'), ['id'], unique=False)
            batch_op.create_index(batch_op.f('ix_distribution_nodes_region'), ['region'], unique=False)

    if 'authoritative_inventories' not in tables:
        op.create_table('authoritative_inventories',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('node_id', sa.Integer(), nullable=False),
            sa.Column('sku', sa.String(length=100), nullable=False),
            sa.Column('on_hand_quantity', sa.Integer(), nullable=False),
            sa.Column('reserved_quantity', sa.Integer(), nullable=False),
            sa.Column('committed_quantity', sa.Integer(), nullable=False),
            sa.Column('safety_stock', sa.Integer(), nullable=False),
            sa.Column('version', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint('on_hand_quantity >= (reserved_quantity + committed_quantity)', name='chk_auth_available_non_negative'),
            sa.CheckConstraint('on_hand_quantity >= 0', name='chk_auth_on_hand_non_negative'),
            sa.CheckConstraint('reserved_quantity >= 0', name='chk_auth_reserved_non_negative'),
            sa.CheckConstraint('version >= 1', name='chk_auth_version_positive'),
            sa.ForeignKeyConstraint(['node_id'], ['distribution_nodes.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('node_id', 'sku', name='uq_node_sku')
        )
        with op.batch_alter_table('authoritative_inventories', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_authoritative_inventories_id'), ['id'], unique=False)
            batch_op.create_index(batch_op.f('ix_authoritative_inventories_node_id'), ['node_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_authoritative_inventories_sku'), ['sku'], unique=False)

    dp_columns = [c['name'] for c in inspector.get_columns('distribution_plans')]
    if 'scenario_id' not in dp_columns:
        with op.batch_alter_table('distribution_plans', schema=None) as batch_op:
            batch_op.add_column(sa.Column('scenario_id', sa.Integer(), nullable=True))
            batch_op.create_index(batch_op.f('ix_distribution_plans_scenario_id'), ['scenario_id'], unique=False)
            batch_op.create_foreign_key('fk_distribution_plans_scenario_id', 'seeded_scenarios', ['scenario_id'], ['id'], ondelete='SET NULL')

    with op.batch_alter_table('plan_events', schema=None) as batch_op:
        batch_op.alter_column('event_type',
               existing_type=sa.VARCHAR(length=20),
               type_=sa.Enum('PLAN_CREATED', 'SCENARIO_SNAPSHOT_TAKEN', 'CANDIDATE_GENERATED', 'CANDIDATE_PROPOSED', 'VALIDATION_STARTED', 'VALIDATION_PASSED', 'VALIDATION_FAILED', 'PLAN_APPROVED', 'PLAN_COMMITTED', 'PLAN_REJECTED', 'PLAN_CANCELLED', 'PLAN_FAILED', 'RESERVATION_CREATED', 'RESERVATION_RELEASED', name='plan_event_type_enum'),
               existing_nullable=False)

    so_columns = [c['name'] for c in inspector.get_columns('sales_observations')]
    if 'updated_at' not in so_columns:
        with op.batch_alter_table('sales_observations', schema=None) as batch_op:
            batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False))

    # Recreate compatibility views
    op.execute("CREATE VIEW IF NOT EXISTS retailers AS SELECT * FROM stores;")
    op.execute("CREATE VIEW IF NOT EXISTS orders AS SELECT * FROM distribution_orders;")
    op.execute("CREATE VIEW IF NOT EXISTS order_items AS SELECT * FROM distribution_order_items;")
    op.execute("CREATE VIEW IF NOT EXISTS plan_runs AS SELECT * FROM distribution_plans;")
    op.execute("CREATE VIEW IF NOT EXISTS allocation_lines AS SELECT * FROM plan_allocations;")
    op.execute("CREATE VIEW IF NOT EXISTS reservations AS SELECT * FROM inventory_reservations;")
    op.execute("CREATE VIEW IF NOT EXISTS audit_events AS SELECT * FROM plan_events;")


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    op.execute("DROP VIEW IF EXISTS plan_runs;")
    op.execute("DROP VIEW IF EXISTS audit_events;")
    op.execute("DROP VIEW IF EXISTS allocation_lines;")
    op.execute("DROP VIEW IF EXISTS reservations;")
    op.execute("DROP VIEW IF EXISTS orders;")
    op.execute("DROP VIEW IF EXISTS order_items;")
    op.execute("DROP VIEW IF EXISTS retailers;")

    if 'sales_observations' in tables:
        so_columns = [c['name'] for c in inspector.get_columns('sales_observations')]
        if 'updated_at' in so_columns:
            with op.batch_alter_table('sales_observations', schema=None) as batch_op:
                batch_op.drop_column('updated_at')

    if 'plan_events' in tables:
        with op.batch_alter_table('plan_events', schema=None) as batch_op:
            batch_op.alter_column('event_type',
                   existing_type=sa.Enum('PLAN_CREATED', 'SCENARIO_SNAPSHOT_TAKEN', 'CANDIDATE_GENERATED', 'CANDIDATE_PROPOSED', 'VALIDATION_STARTED', 'VALIDATION_PASSED', 'VALIDATION_FAILED', 'PLAN_APPROVED', 'PLAN_COMMITTED', 'PLAN_REJECTED', 'PLAN_CANCELLED', 'PLAN_FAILED', 'RESERVATION_CREATED', 'RESERVATION_RELEASED', name='plan_event_type_enum'),
                   type_=sa.VARCHAR(length=20),
                   existing_nullable=False)

    if 'distribution_plans' in tables:
        dp_columns = [c['name'] for c in inspector.get_columns('distribution_plans')]
        if 'scenario_id' in dp_columns:
            with op.batch_alter_table('distribution_plans', schema=None) as batch_op:
                batch_op.drop_constraint('fk_distribution_plans_scenario_id', type_='foreignkey')
                batch_op.drop_index(batch_op.f('ix_distribution_plans_scenario_id'))
                batch_op.drop_column('scenario_id')

    if 'authoritative_inventories' in tables:
        with op.batch_alter_table('authoritative_inventories', schema=None) as batch_op:
            batch_op.drop_index(batch_op.f('ix_authoritative_inventories_sku'))
            batch_op.drop_index(batch_op.f('ix_authoritative_inventories_node_id'))
            batch_op.drop_index(batch_op.f('ix_authoritative_inventories_id'))
        op.drop_table('authoritative_inventories')

    if 'distribution_nodes' in tables:
        with op.batch_alter_table('distribution_nodes', schema=None) as batch_op:
            batch_op.drop_index(batch_op.f('ix_distribution_nodes_region'))
            batch_op.drop_index(batch_op.f('ix_distribution_nodes_id'))
            batch_op.drop_index(batch_op.f('ix_distribution_nodes_code'))
        op.drop_table('distribution_nodes')

    op.execute("CREATE VIEW IF NOT EXISTS retailers AS SELECT * FROM stores;")
    op.execute("CREATE VIEW IF NOT EXISTS orders AS SELECT * FROM distribution_orders;")
    op.execute("CREATE VIEW IF NOT EXISTS order_items AS SELECT * FROM distribution_order_items;")
    op.execute("CREATE VIEW IF NOT EXISTS plan_runs AS SELECT * FROM distribution_plans;")
    op.execute("CREATE VIEW IF NOT EXISTS allocation_lines AS SELECT * FROM plan_allocations;")
    op.execute("CREATE VIEW IF NOT EXISTS reservations AS SELECT * FROM inventory_reservations;")
    op.execute("CREATE VIEW IF NOT EXISTS audit_events AS SELECT * FROM plan_events;")
