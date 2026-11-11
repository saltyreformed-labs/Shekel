"""The ONE way the migration chain runs on a caller's connection.

Plan step ``balance:X-cv``, rulings **R-BAL114** (one runner) and **R-BAL120**
(its home is here).

**One spelling of "run the migrations".**  The deploy
(``scripts/init_database.py``, step 3 of ``scripts/deploy.sh``) and the test-template build
(``scripts/build_test_template.py``) both run the chain through this module, on
a connection they hand over inside a transaction they opened, and nothing here
commits it.  So every template build -- every CI run -- pushes the whole chain
through the path a deploy takes, and a migration that cannot run inside a
caller's transaction fails the template build on the day it is written instead
of failing a release: one PostgreSQL refuses inside a transaction block
(``CREATE INDEX CONCURRENTLY``) or one that asks Alembic for an
``autocommit_block`` (whose ``assert self._transaction is not None`` fires on a
transaction Alembic did not begin).  A migration that COMMITS the connection
itself (``op.get_bind().commit()``, or ``COMMIT`` sent as SQL) does not fail
there -- it commits early -- and ``scripts/init_database.py`` states the census
that finds none.  ``flask db`` is the one runner that is not this one:
``migrations/env.py`` opens and commits a connection of its own when none is
handed over.

**Alembic's shared-connection recipe.**  ``Config.attributes["connection"]``
tells ``migrations/env.py`` to configure the migration context on that
connection.  The connection is in a transaction, so Alembic treats it as
EXTERNAL (``MigrationContext._in_external_transaction``): it neither begins nor
commits one, and the migrations and the stamp commit or roll back with the
caller's own work.  **This module never commits, whatever the caller hands
over**: Alembic begins and COMMITS a transaction of its own only on a
connection that has none, so a connection outside a transaction is put inside
one first (:func:`_config`), and it is the caller's to commit or discard.  A
caller that never commits loses the migrations with its connection, which is
SQLAlchemy 2.0's own rule for work it did not commit.

**Paths resolve from the repository root**, not the working directory:
``alembic.ini`` and ``migrations/`` sit beside ``app/`` in the checkout and in
the image, and a test process need not run from either.
"""

import os

from alembic import command
from alembic.config import Config

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _config(connection):
    """Return the Alembic config that runs on *connection*, inside a transaction.

    A connection outside a transaction is put inside one here, before Alembic
    sees it: Alembic would otherwise begin one of its own and COMMIT it.  The
    deploy reaches this with no transaction when a stray rollback has already
    ended its own, and the migrations then land in a transaction nobody
    commits instead of committing on their own.

    Args:
        connection: The caller's SQLAlchemy connection.

    Returns:
        alembic.config.Config: The config both commands below take.
    """
    if not connection.in_transaction():
        connection.begin()
    config = Config(os.path.join(_REPO_ROOT, "alembic.ini"))
    config.set_main_option(
        "script_location", os.path.join(_REPO_ROOT, "migrations"),
    )
    config.attributes["connection"] = connection
    return config


def upgrade_to_head(connection):
    """Run every pending migration on *connection*, committing nothing.

    Args:
        connection: The caller's SQLAlchemy connection; the migrations and
            the stamp commit when the caller commits it.
    """
    command.upgrade(_config(connection), "head")


def stamp_head(connection):
    """Mark every migration applied on *connection*, committing nothing.

    The first-boot build's last step: it creates the schema from the models
    and never runs the chain, so the stamp is what tells the next deploy there
    is nothing to migrate.

    Args:
        connection: The caller's SQLAlchemy connection; the stamp commits
            when the caller commits it.
    """
    command.stamp(_config(connection), "head")
