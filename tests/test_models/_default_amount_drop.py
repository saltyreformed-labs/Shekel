"""Revision ``4d4eb9cd3850``'s two steps down, for a case of an OLDER revision.

Plan step ``balance:X-bp`` (card plan#1045) dropped ``default_amount`` from
``budget.transaction_templates`` and ``budget.transfer_templates``.  An older
revision's shipped SQL names that column -- ``a9d3c15e7f42``'s backfill reads
it, ``596408fab6f1``'s mint and residue re-sync write it -- so a test driving
one of those statements at head meets ``UndefinedColumn`` where it used to
find the schema it expected.  Each helper here runs the X-bp revision's OWN
statements rather than DDL of its own (the reason
``tests._test_helpers.restore_rule_due_day_column`` gives), and neither puts
the database at any particular revision: it is head's schema with the column
back.  Neither comes back up; the ``db`` fixture re-clones the case's database
for the next test.

Beside the tests, not in ``tests/_test_helpers.py``: that module was over the
1,000-line ceiling, so nothing new went into it (ruling R-IR).
"""
from argparse import Namespace

from alembic.config import Config
from alembic.runtime.environment import EnvironmentContext

from tests._test_helpers import load_migration_module, run_migration_callable

_XBP_REVISION_FILE = "4d4eb9cd3850_a_definitions_price_is_its_series_alone.py"


def step_down_through_the_default_amount_drop(db_session, figures=()):
    """Run revision ``4d4eb9cd3850``'s whole downgrade: both columns back, restored.

    For a case whose definitions all state their price, or whose older
    statement writes the column itself (``596408fab6f1``'s mint): each copy
    comes back as its series' newest version, ``NOT NULL`` with its CHECK, as
    ruling plan#2695 restores it.  It REFUSES, changing nothing, while a
    definition holds no copy any stored fact gives back -- a loan payment that
    follows its loan, a paycheck no Received row records, a definition with no
    series -- which is why a case whose subject is such a definition takes
    :func:`readd_the_default_amount_columns` instead -- or states each such
    copy in *figures*, as ruling plan#2749's re-run does.

    **It runs inside an Alembic environment, as ``flask db downgrade`` does.**
    The downgrade reads its ``-x`` arguments through ``alembic.context``,
    which only an :class:`~alembic.runtime.environment.EnvironmentContext`
    establishes, and :func:`tests._test_helpers.run_migration_callable`
    configures a bare migration context.  The environment carries *figures*
    as the command line's ``-x`` values and nothing else: the downgrade
    reads only its configuration's ``cmd_opts``.

    **Build the case's ORM definitions BEFORE calling it.**  The models on
    this tree do not map the column, so an ORM insert after it omits a column
    that is ``NOT NULL`` again and is refused.

    Args:
        db_session: The test ``db.session``, in the scope that holds the
            template tables' locks (see
            :func:`tests._test_helpers.run_migration_callable`).
        figures: The ``-x`` values, each ``"<table>.<id>=<figure>"``.
    """
    config = Config()
    config.cmd_opts = Namespace(x=list(figures))
    with EnvironmentContext(config, script=None):
        run_migration_callable(
            load_migration_module(_XBP_REVISION_FILE).downgrade, db_session,
        )


def readd_the_default_amount_columns(db_session):
    """Run only the first step of revision ``4d4eb9cd3850``'s downgrade.

    Both columns back, NULLABLE and EMPTY, with no restore and no CHECK: what
    ``a9d3c15e7f42``'s backfill needs, whose input is a definition with NO
    series and a stated copy (the series is what the backfill builds, and the
    whole downgrade's restore reads it).  The case states each copy itself.
    An ORM insert after it leaves the column ``NULL``, so it may run before
    the case's fixtures.

    Args:
        db_session: The test ``db.session``, in the scope that holds the
            template tables' locks.
    """
    run_migration_callable(
        load_migration_module(_XBP_REVISION_FILE).readd_columns, db_session,
    )
