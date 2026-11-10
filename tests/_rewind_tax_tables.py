"""Put the five per-user tax tables and ``ref.tax_types`` back, for a test of an older revision.

Plan step **salary:X-at-2** (``5f2c46a35a79``) dropped them.  A test whose
subject is an EARLIER revision's shipped SQL that names them -- C-42's index on
``salary.tax_brackets``, C-43's keys on ``salary.state_tax_configs`` and
``salary.tax_bracket_sets`` -- would meet ``UndefinedTable`` on the head
template, so it runs X-at-2's own ``downgrade()`` first (ruling **plan#3235**),
the shape :func:`tests._test_helpers.rewind_paycheck_lines_rename` gives the
tables a later rename moved.  Kept out of ``tests/_test_helpers.py``, which is
past the 1,000-line bound already.
"""

from tests._test_helpers import load_migration_module, run_migration_callable

#: Plan step ``salary:X-at-2``'s revision.
_X_AT_2_REVISION_FILE = "5f2c46a35a79_the_per_user_tax_tables_go.py"


def rewind_the_per_user_tax_tables(db_session):
    """Run plan step ``salary:X-at-2``'s own ``downgrade()``: the six tables back, empty.

    What it leaves is head's schema with the five tables and ``ref.tax_types``
    recreated in production's shape, their audit triggers attached and
    ``ref.tax_types``' three names seeded -- and no row in the five.  The ORM
    on this tree maps none of them, so a case reads and writes them in SQL.
    Alembic's order is newest first, and X-at-2 is newer than every rename
    another rewind undoes, so it runs before those.

    Args:
        db_session: The test ``db.session``, in the scope that holds the
            tables' locks (see :func:`tests._test_helpers.run_migration_callable`).
    """
    run_migration_callable(
        load_migration_module(_X_AT_2_REVISION_FILE).downgrade, db_session,
    )
