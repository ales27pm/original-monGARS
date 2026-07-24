from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from mongars.adaptation.models import (
    ExplicitFeedbackRecord,
    PersonalityProfileRecord,
    PersonalityProfileRevisionRecord,
)
from mongars.autobiography.tables import (
    AutobiographicalEventRecord,
    ConversationTurn,
    GenerationEvidence,
    GenerationRun,
)
from mongars.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import every separately packaged model before exposing metadata. This prevents a later
# autogenerate run from misclassifying modular tables as unmanaged database objects.
_REGISTERED_PACKAGED_MODELS = (
    ExplicitFeedbackRecord,
    PersonalityProfileRecord,
    PersonalityProfileRevisionRecord,
    AutobiographicalEventRecord,
    ConversationTurn,
    GenerationEvidence,
    GenerationRun,
)
target_metadata = Base.metadata


def database_url() -> str:
    value = os.getenv("MONGARS_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not value:
        raise RuntimeError("MONGARS_DATABASE_URL is required to run database migrations")
    return value


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
