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

It lives beside its callers because ``tests/_test_helpers.py`` is over the
1,000-line limit (plan#2000) and takes no new lines.
"""
from tests._test_helpers import load_migration_module, run_migration_callable

_REVISION_FILE = "9b3e6d1f4a27_a_rule_carries_its_units_day_of_month_flag.py"


def rewind_rule_unit_flag(db_session):
    """Take the flag, its key and its two CHECKs off ``budget.recurrence_rules``.

    It leaves head's schema without those four objects and NOT any particular
    revision.  The ORM model on this tree maps the flag, so an ORM read of a
    rule finds no column until :func:`replay_rule_unit_flag` runs.

    Args:
        db_session: The test ``db.session``, in the scope that holds the
            table's locks (see ``run_migration_callable``).
    """
    run_migration_callable(
        load_migration_module(_REVISION_FILE).downgrade, db_session,
    )


def replay_rule_unit_flag(db_session):
    """Run the revision's ``upgrade()`` after :func:`rewind_rule_unit_flag`.

    Args:
        db_session: The test ``db.session``, in the scope that holds the
            table's locks.
    """
    run_migration_callable(
        load_migration_module(_REVISION_FILE).upgrade, db_session,
    )
