"""Two loan-payment snapshot downgrades finish on a database that never ran their upgrade.

Finding **BAL-627** (card plan#1778).  ``b2d8f3a6c541`` (the pre-origination
loan payment purge) and ``c4e91a7b2d38`` (the loan payment due date backfill)
each keep what their upgrade changed in a table of their own and restore it from
there on the way down.  A database built at head by ``scripts/init_database.py``
(``create_all``, then a stamp) never ran either upgrade and holds neither table,
so each downgrade died there with ``UndefinedTable`` -- the shape plan step
``balance:X-bi-6-4d``'s checkpoint cp1c fixed for ``82f7b7ed4332``.  The suite's
template replays the chain, so both upgrades ran and both tables exist there;
each case drops its table first, which leaves the database
``scripts/init_database.py`` builds as far as these revisions can see.  Each
downgrade now reads a missing snapshot as an empty one, so it restores nothing,
creates nothing and finishes.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.extensions import db as _db
from tests._test_helpers import (
    create_account_of_type,
    create_transfer,
    load_migration_module,
    run_migration_callable,
)

#: The two revisions, each with its snapshot table and the line its downgrade
#: prints when it finds none.
_CASES = (
    (
        "b2d8f3a6c541_purge_pre_origination_loan_payments.py",
        "system.pre_origination_purge",
        "Pre-origination loan payments restored: 0 (no snapshot table was "
        "found)",
    ),
    (
        "c4e91a7b2d38_backfill_loan_payment_due_dates.py",
        "system.loan_due_date_backfill",
        "Loan payment due dates restored: 0 (no snapshot table was found)",
    ),
)


def _table_absent(table):
    """Return whether *table* is missing from the test database."""
    return _db.session.execute(
        text("SELECT to_regclass(:table) IS NULL"), {"table": table},
    ).scalar_one()


def _transfers():
    """Return every stored transfer's ``(id, amount, due_date, is_deleted)``, by id."""
    return _db.session.execute(text(
        "SELECT id, amount, due_date, is_deleted FROM budget.transfers ORDER BY id"
    )).all()


class TestADowngradeWithNoSnapshot:
    """No snapshot table: the downgrade restores nothing and finishes."""

    @pytest.mark.parametrize("case", _CASES)
    def test_it_restores_nothing_and_leaves_no_table(
        self, app, seed_user, seed_periods, capsys, case,
    ):
        """One stored transfer with a due date comes through untouched.

        The transfer is what either downgrade would write to, given a
        snapshot: ``b2d8f3a6c541``'s re-inserts deleted transfers and
        ``c4e91a7b2d38``'s writes old due dates back by id.  With no snapshot
        the stored rows are exactly what they were, and no table is left
        behind (neither downgrade creates its table to read it).  The
        table is dropped first: the template replayed the upgrade that made
        it, and a database built by ``create_all`` has none.
        """
        filename, table, line = case
        with app.app_context():
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "Snapshot Savings",
            )
            create_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("40.00"),
                due_date=date(2026, 1, 5),
            )
            _db.session.execute(text(f"DROP TABLE {table}"))
            _db.session.commit()
            before = _transfers()
            assert len(before) == 1
            assert _table_absent(table) is True

            run_migration_callable(
                load_migration_module(filename).downgrade, _db.session,
            )

            assert _transfers() == before
            assert _table_absent(table) is True
            assert line in capsys.readouterr().out
