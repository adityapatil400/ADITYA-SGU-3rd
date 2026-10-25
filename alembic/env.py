from logging.config import fileConfig
from sqlalchemy import create_engine, pool
from alembic import context

from app.core.config import settings
from app.core.logging import logger
from app.db.base import Base
# Import all models so that Alembic autogenerate inspects full metadata
import app.models  # noqa: F401

config = context.config

# Interpret config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set model MetaData object for 'autogenerate' support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.
    Configures context with database URL from environment settings.
    """
    url = settings.effective_database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.
    Creates an Engine bound to the configured database URL.
    """
    connectable = create_engine(
        settings.effective_database_url,
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()



if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
