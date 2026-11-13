"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_models/test_c43_ondelete_and_naming_convention.py``, moved verbatim by
``tools/split``.  A fixture here reaches exactly the tests that module held, at the scope it
declared.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests._rewind_tax_tables import rewind_the_per_user_tax_tables
from tests._test_helpers import rewind_paycheck_lines_rename

from ._helpers import _M_C43


# ---------------------------------------------------------------------------
# The schema this revision was written against
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _c43_schema(app, db):
    """Run every case here under plan step ``salary:R18-a``'s downgrade.

    Two of C-43's ``REF_FK_SPECS`` live on ``salary.paycheck_deductions``,
    which head renamed to ``salary.paycheck_lines`` with the kind FK's name
    (ruling **R-SAL38**); the specs are read straight off the migration
    module, so the DB-shape cases would look for names the head template
    no longer carries and ``upgrade()`` would alter a table that is not
    there.  :func:`~tests._test_helpers.rewind_paycheck_lines_rename` puts
    the old names back first (the stacked, newest-first shape of
    :func:`~tests._test_helpers.rewind_pay_schedule_rhythm`); every case
    then reads and drives C-43 against the schema it was written for.  The
    model-contract cases read metadata only and are unaffected.  Nothing
    is replayed: the ``db`` fixture re-clones the per-worker database for
    every test, and no case here reads a line through the models.

    Two more of the specs, ``fk_state_tax_configs_tax_type_id`` and
    ``fk_tax_bracket_sets_filing_status_id``, are on tables plan step
    salary:X-at-2 dropped, so its downgrade runs first, newest first
    (:func:`~tests._rewind_tax_tables.rewind_the_per_user_tax_tables`, ruling
    plan#3235).
    """
    with app.app_context():
        rewind_the_per_user_tax_tables(db.session)
        rewind_paycheck_lines_rename(db.session)


# ---------------------------------------------------------------------------
# Restoration fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def restore_c43_state(db):
    """Ensure every C-43 artifact is restored after the test.

    Several tests in this module force constraints back to their
    pre-C-43 names and ondelete clauses to simulate the production-
    drift state.  This fixture guarantees the post-C-43 state is
    back in place once the test exits, even if the body raised --
    otherwise every subsequent C-43 invariant test running in the
    same per-worker DB would silently pass against a degraded
    schema.

    Cleanup runs the migration's ``upgrade()`` against the current
    state of the DB.  Because the upgrade is idempotent, the
    re-run is a no-op when the test cleaned up its own changes and
    a full restore when it did not (e.g., when the test asserted on
    a partial state and raised before restoring).
    """
    yield
    # pylint: disable=import-outside-toplevel
    from alembic import op
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    db.session.rollback()
    ctx = MigrationContext.configure(connection=db.session.connection())
    with Operations.context(ctx):
        with patch.object(
            op, "get_bind",
            return_value=db.session.connection(),
        ):
            _M_C43.upgrade()
    db.session.commit()
