"""
Shekel Budget App -- rewinding a rule's carried unit flag (plan#2302)

Migration ``9b3e6d1f4a27`` made ``budget.recurrence_rules.unit_has_day_of_month``
``NOT NULL`` and keyed it onto a column of ``ref.recurrence_units``, and its two
CHECKs name ``max_per_month`` and ``nominal_day``.  A test driving an EARLIER
revision against head therefore meets three things that revision never knew: an
INSERT of its own refused for a null flag, a span column it cannot drop from
under the key, and a CHECK that silently leaves with a column it drops.  This is
the shape of ``restore_rule_due_day_column`` in ``tests/_test_helpers``: it runs
the revision's own ``downgrade()`` rather than DDL of its own, and Alembic undoes
the newest first, so a caller rewinding further runs this before its older
rewinds.

**``897a6663d221`` sits above the flag and names it**
(``ck_recurrence_rules_weekday_reading_unit``, ruling plan#2704), so it goes first
and comes back last.  Dropping the flag alone would take that CHECK with the
column and nothing would put it back.

It lives beside its callers because ``tests/_test_helpers.py`` is over the
1,000-line limit (plan#2000) and takes no new lines.
"""
from tests._test_helpers import load_migration_module, run_migration_callable

_REVISION_FILE = "9b3e6d1f4a27_a_rule_carries_its_units_day_of_month_flag.py"
_READING_REVISION_FILE = "897a6663d221_a_rule_states_how_its_first_date_is_read.py"


def rewind_weekday_reading(db_session):
    """Run ``897a6663d221``'s ``downgrade()``, the revision above the flag.

    Args:
        db_session: The test ``db.session``, in the scope that holds the
            table's locks (see ``run_migration_callable``).
    """
    run_migration_callable(
        load_migration_module(_READING_REVISION_FILE).downgrade, db_session,
    )


def rewind_rule_unit_flag(db_session):
    """Take the weekday reading, then the flag, its key and its two CHECKs, off the rules.

    It leaves head's schema without those objects and NOT any particular
    revision, and ``897a6663d221``'s ``downgrade()`` puts back what that revision
    dropped: ``budget.recurrence_weekday_anchors`` and its audit trigger.  The ORM
    model on this tree maps the flag and the reading, so an ORM read of a rule
    finds no column until :func:`replay_rule_unit_flag` runs.

    Args:
        db_session: The test ``db.session``, in the scope that holds the
            table's locks (see ``run_migration_callable``).
    """
    rewind_weekday_reading(db_session)
    run_migration_callable(
        load_migration_module(_REVISION_FILE).downgrade, db_session,
    )


def replay_rule_unit_flag(db_session):
    """Run both revisions' ``upgrade()``, oldest first, after :func:`rewind_rule_unit_flag`.

    Args:
        db_session: The test ``db.session``, in the scope that holds the
            table's locks.
    """
    run_migration_callable(
        load_migration_module(_REVISION_FILE).upgrade, db_session,
    )
    run_migration_callable(
        load_migration_module(_READING_REVISION_FILE).upgrade, db_session,
    )
