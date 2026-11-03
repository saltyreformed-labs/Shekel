"""
Shekel Budget App -- migration ``e2b8c4f1a9d3``: a repeat on each statement's due date

Plan step **credit_card:CC-6**, its task plan#2673 (ruling plan#2671): one
``ref.recurrence_units`` row, ``due_date``.  What is pinned, each on this
test's own clone:

* the write door stores a due-date repeat on it, its ``starts_on`` the first
  day the repeat covers, as authored (plan#2673's reading);
* the DOWNGRADE deletes the row while no rule repeats by it, and the UPGRADE
  seeds it again, idempotently;
* the downgrade REFUSES while a rule repeats by it, names the rule, and
  changes nothing.
"""

from datetime import date

import pytest
import sqlalchemy

from app import ref_cache
from app.enums import RecurrenceUnitEnum
from app.extensions import db
from app.models.transfer_template import TransferTemplate
from app.services.pay_calendar import calendar_for
from app.services.recurrence import RecurrenceSpec, author_rule
from tests._test_helpers import (
    create_account_of_type,
    load_migration_module,
    run_migration_callable,
)

_MIGRATION = load_migration_module(
    "e2b8c4f1a9d3_a_repeat_on_each_statements_due_date.py",
)


def _unit_rows():
    """How many ``ref.recurrence_units`` rows are named ``due_date``."""
    return db.session.execute(sqlalchemy.text(
        "SELECT count(*) FROM ref.recurrence_units WHERE name = 'due_date'"
    )).scalar()


def _due_date_payment(seed_user):
    """A transfer into a card repeating on its due dates from Dec 4 2026."""
    card = create_account_of_type(seed_user, db.session, "Credit Card", "Visa")
    user_id = seed_user["user"].id
    template = TransferTemplate(
        user_id=user_id, from_account_id=seed_user["account"].id,
        to_account_id=card.id, name="Pay the card",
    )
    db.session.add(template)
    db.session.flush()
    rule = author_rule(
        RecurrenceSpec(
            user_id=user_id, unit=RecurrenceUnitEnum.DUE_DATE,
            starts_on=date(2026, 12, 4),
        ),
        calendar_for(user_id), template,
    )
    db.session.commit()
    return rule


class TestTheWriteDoorStoresTheStart:
    """The rule a due-date repeat is stored as."""

    def test_the_rule_names_the_unit_and_the_first_day_covered(
        self, app, seed_user,
    ):
        """Dec 4 is stored as Dec 4, on the ``due_date`` row.

        Not the first due date: that is the card's terms' to decide on every
        read, so a corrected due day moves it (plan#2673).
        """
        with app.app_context():
            rule = _due_date_payment(seed_user)

            assert rule.unit_id == ref_cache.recurrence_unit_id(
                RecurrenceUnitEnum.DUE_DATE,
            )
            assert rule.starts_on == date(2026, 12, 4)
            assert rule.interval_n == 1
            assert rule.nominal_day is None
            assert rule.max_per_month is None


class TestTheMigration:
    """Down and up again on this test's clone."""

    def test_the_downgrade_deletes_the_row_and_the_upgrade_restores_it(
        self, app, seed_user,
    ):
        """No rule repeats by it, so it goes; up twice leaves one row."""
        with app.app_context():
            assert seed_user
            assert _unit_rows() == 1

            run_migration_callable(_MIGRATION.downgrade, db.session)
            assert _unit_rows() == 0

            run_migration_callable(_MIGRATION.upgrade, db.session)
            run_migration_callable(_MIGRATION.upgrade, db.session)
            assert _unit_rows() == 1

    def test_the_downgrade_refuses_while_a_rule_repeats_by_it(
        self, app, seed_user,
    ):
        """It names the rule and deletes nothing."""
        with app.app_context():
            rule = _due_date_payment(seed_user)

            with pytest.raises(
                RuntimeError, match=rf"recurrence rules {rule.id} repeat by it",
            ):
                run_migration_callable(_MIGRATION.downgrade, db.session)

            db.session.rollback()
            assert _unit_rows() == 1
