"""authoritative_inventory_views_and_invariants

Revision ID: a01b2c3d4e5f
Revises: 9c01d2e3f4a5
Create Date: 2026-10-24 21:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a01b2c3d4e5f'
down_revision: Union[str, Sequence[str], None] = '9c01d2e3f4a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Creates compatibility views mapping FINAL_PRD Mongoose/MongoDB entity names
    to authoritative PostgreSQL relational tables.
    Preserves all entities, relationships, batch expiry, versions, and timestamps.
    """
    # 1. Retailers view (mapping to stores)
    op.execute("CREATE VIEW IF NOT EXISTS retailers AS SELECT * FROM stores;")

    # 2. Orders view (mapping to distribution_orders)
    op.execute("CREATE VIEW IF NOT EXISTS orders AS SELECT * FROM distribution_orders;")

    # 3. Order items view (mapping to distribution_order_items)
    op.execute("CREATE VIEW IF NOT EXISTS order_items AS SELECT * FROM distribution_order_items;")

    # 4. Plan runs view (mapping to distribution_plans)
    op.execute("CREATE VIEW IF NOT EXISTS plan_runs AS SELECT * FROM distribution_plans;")

    # 5. Allocation lines view (mapping to plan_allocations)
    op.execute("CREATE VIEW IF NOT EXISTS allocation_lines AS SELECT * FROM plan_allocations;")

    # 6. Reservations view (mapping to inventory_reservations)
    op.execute("CREATE VIEW IF NOT EXISTS reservations AS SELECT * FROM inventory_reservations;")

    # 7. Audit events view (mapping to plan_events)
    op.execute("CREATE VIEW IF NOT EXISTS audit_events AS SELECT * FROM plan_events;")


def downgrade() -> None:
    """
    Safely drops compatibility views without discarding any underlying authoritative table data.
    """
    op.execute("DROP VIEW IF EXISTS audit_events;")
    op.execute("DROP VIEW IF EXISTS reservations;")
    op.execute("DROP VIEW IF EXISTS allocation_lines;")
    op.execute("DROP VIEW IF EXISTS plan_runs;")
    op.execute("DROP VIEW IF EXISTS order_items;")
    op.execute("DROP VIEW IF EXISTS orders;")
    op.execute("DROP VIEW IF EXISTS retailers;")
