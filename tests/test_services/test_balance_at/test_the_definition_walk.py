"""The seam's one definition walk (plan step C9, task plan#3939).

:func:`app.services.balance_at._definition_walk.unwritten_occurrences` is what
a generate pass would write for one recurring definition and has not: each
occurrence placed on the paycheck its row would live in, less any a row of the
definition answers in any state, each dated as its row would be.  The loan's
estimated tier walks every paycheck, saved or projected (ruling R-R64), and
the cash tier walks projected paychecks only (ruling plan#3932).  The loan's
figures are graded by the loan suite (``test_loan_plan_sums_definitions.py``
and its siblings), which this extraction must leave green; this file grades
what the walk now does that it did not: a TRANSACTION template, and the
projected-only walk.

**The schedule every case reads**, the ``seed_periods`` fixture: ten saved
biweekly paychecks from 2026-01-02 (01-02, 01-16, 01-30, 02-13, 02-27, 03-13,
03-27, 04-10, 04-24, 05-08), so the saved horizon is 2026-05-21.  The projected
paydays continue at the same cadence: 05-22, 06-05 and 06-19 before
:data:`_THROUGH`.  The checking account's books open in 2024 and a savings
account made here opens on 2026-01-01, the day before the first payday
(measured), so the books floor drops no occurrence here.
"""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.services._template_rows import row_selector_for
from app.services.balance_at import BalanceContext
from app.services.balance_at._definition_walk import (
    Paychecks,
    unwritten_occurrences,
)
from tests._test_helpers import (
    a_deleted_occurrence,
    bare_expense_template,
    create_savings_account,
    generate_row_of,
    generate_transfer_of,
    make_cadence_rule,
    make_expense_template,
    make_transfer_template,
)
from tests.oracles.recurrence_baseline import MONTHLY

# The last day every walk here covers: past the horizon by the three projected
# paydays 05-22, 06-05 and 06-19.
_THROUGH = date(2026, 6, 30)

# The ten saved paydays, 2026-01-02 + 14 days each.
_SAVED_PAYDAYS = [date(2026, 1, 2) + timedelta(days=14 * n) for n in range(10)]

# The projected paydays through _THROUGH: 05-08 + 14 = 05-22, then 06-05, 06-19.
_PROJECTED_PAYDAYS = [date(2026, 5, 22), date(2026, 6, 5), date(2026, 6, 19)]

# The read pass's as-of.  The walk takes no bound from it: an occurrence before
# it is "not generated yet" for the loan (ruling R-R64), so 03-01 sits inside
# the saved schedule to show both halves are walked regardless.
_AS_OF = date(2026, 3, 1)


def _occurrences(template, paychecks):
    """Return the occurrence dates *template*'s walk names through _THROUGH."""
    ctx = BalanceContext.build(template.user_id, _AS_OF)
    return [
        unwritten.occurrence
        for unwritten in unwritten_occurrences(
            template, ctx, through=_THROUGH, paychecks=paychecks,
        )
    ]


class TestWhichPaychecksTheWalkPlacesOn:
    """The caller states which paychecks it walks."""

    def test_the_loan_walks_every_paycheck_and_the_cash_tier_only_projected_ones(
        self, seed_user, seed_periods,
    ):
        """An every-paycheck bill with no rows, walked both ways.

        Hand count: the rule fires on every payday from the opening one, and
        no row answers any of them.  Every paycheck, saved or projected, is the
        ten saved paydays and the three projected ones, thirteen in all.
        Projected paychecks alone are the last three, 05-22, 06-05 and 06-19:
        nothing on or before the horizon (2026-05-21).
        """
        template = make_expense_template(db.session, seed_user, amount="100.00")
        db.session.commit()

        assert _occurrences(template, Paychecks.SAVED_OR_PROJECTED) == (
            _SAVED_PAYDAYS + _PROJECTED_PAYDAYS
        )
        assert _occurrences(template, Paychecks.PROJECTED) == _PROJECTED_PAYDAYS

    def test_each_occurrence_is_dated_as_its_row_would_be(
        self, seed_user, seed_periods,
    ):
        """A monthly bill on the 15th is due on the 15th, in the paycheck holding it.

        A calendar cadence funded by the paycheck containing it is due on the
        occurrence itself (ruling R-R94), not on the payday.  Hand count
        through 2026-06-30: the 15th of each month January through June, six in all, of
        which only 06-15 falls past the horizon, in the projected paycheck
        06-05 .. 06-18.  So the projected walk names one occurrence, due
        2026-06-15, on the paycheck that opens 2026-06-05.  A walk that dated
        the row on its payday would read 06-05.
        """
        template = bare_expense_template(db.session, seed_user, name="Gym")
        make_cadence_rule(template, MONTHLY, fires_on_day=15)
        db.session.commit()
        ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)

        every = unwritten_occurrences(
            template, ctx,
            through=_THROUGH, paychecks=Paychecks.SAVED_OR_PROJECTED,
        )
        projected = unwritten_occurrences(
            template, ctx, through=_THROUGH, paychecks=Paychecks.PROJECTED,
        )

        assert [unwritten.due_date for unwritten in every] == [
            date(2026, month, 15) for month in range(1, 7)
        ]
        assert [
            (unwritten.due_date, unwritten.period.start_date,
             unwritten.period.is_projected)
            for unwritten in projected
        ] == [(date(2026, 6, 15), date(2026, 6, 5), True)]


class TestARowOfTheDefinitionAnswersItsOccurrence:
    """Every state of a row answers its occurrence, in the definition's own table."""

    def test_a_transaction_templates_rows_answer_in_the_transactions_table(
        self, seed_user, seed_periods,
    ):
        """A deleted row and a live row each take their occurrence out of the walk.

        The every-paycheck bill's row in period 3 (payday 02-13) is generated
        and then DELETED through the delete door, and its row in period 5
        (03-13) is generated and left.  Both answer their occurrence: the
        deleted one because a row the owner removed must not be resurrected
        by an estimate any more than by a pass.  Hand count: the thirteen
        paydays less 02-13 and 03-13 is eleven.  A walk that asked the
        transfers table for a transaction template's rows would find neither
        and read all thirteen.
        """
        deleted = a_deleted_occurrence(
            db.session, seed_user, seed_periods[3], amount="100.00",
        )
        template = deleted.template
        generate_row_of(template, seed_periods[5])
        db.session.commit()

        expected = [
            payday for payday in _SAVED_PAYDAYS + _PROJECTED_PAYDAYS
            if payday not in (date(2026, 2, 13), date(2026, 3, 13))
        ]
        assert _occurrences(template, Paychecks.SAVED_OR_PROJECTED) == expected

    def test_a_transfer_templates_rows_answer_in_the_transfers_table(
        self, seed_user, seed_periods,
    ):
        """A generated transfer takes its occurrence out of the walk.

        The every-paycheck transfer to savings has its row in period 2 (payday
        01-30).  Hand count: every paycheck is the thirteen paydays less 01-30,
        twelve; projected paychecks alone are still the three, since no row
        can answer a paycheck the saved schedule does not hold.
        """
        savings = create_savings_account(
            seed_user, db.session, "Savings", Decimal("0.00"),
        )
        template = make_transfer_template(db.session, seed_user, savings)
        generate_transfer_of(template, seed_periods[2])
        db.session.commit()

        assert _occurrences(template, Paychecks.SAVED_OR_PROJECTED) == [
            payday for payday in _SAVED_PAYDAYS + _PROJECTED_PAYDAYS
            if payday != date(2026, 1, 30)
        ]
        assert _occurrences(template, Paychecks.PROJECTED) == _PROJECTED_PAYDAYS


class TestTheTableIsClosed:
    """A value of neither kind is refused rather than given a table."""

    def test_a_value_that_is_no_definition_is_refused(self):
        """Neither table is guessed for an object that is not a template.

        Answering with either would query the wrong rows silently, and a row
        the claim check cannot see is an occurrence generated twice.
        """
        with pytest.raises(
            TypeError, match="SimpleNamespace is not a recurring definition",
        ):
            row_selector_for(SimpleNamespace(id=1), scenario_id=1)
