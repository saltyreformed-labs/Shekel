"""A transaction template's unwritten occurrence is priced as its row would be.

Plan step ``pay_calendar:C9``, task plan#3940 (ruling plan#3930: past the last
saved paycheck, money comes from the recurring items, priced the way their
real rows would be).
:func:`app.services.cash_ledger.transaction_definition_cash` prices an
occurrence no row answers yet by the arms a written transaction takes: rule 2,
a paycheck, through the salary profile on the paycheck the row would live in,
saved or projected; rule 3, any other definition, through its price series on
the row's due date.  Each case grades two things: the figure an occurrence
past the horizon gets, worked by hand, and that a SAVED occurrence's estimate
equals what its written row resolves to, so the two cannot part.

**The schedule every case reads**, the ``seed_periods`` fixture: ten saved
biweekly paychecks from 2026-01-02 (01-02 .. 05-08), so the saved horizon is
2026-05-21, and the projected paydays through :data:`_THROUGH` are 05-22,
06-05 and 06-19.  The occurrences are the seam's own walk's
(:func:`app.services.balance_at._definition_walk.unwritten_occurrences`, the
walk the cash tier will price them from), on projected paychecks only (ruling
plan#3932).  An every-paycheck definition's row is due on its payday.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.exceptions import AmountUnresolvable
from app.extensions import db
from app.services.balance_at import BalanceContext
from app.services.balance_at._definition_walk import (
    Paychecks,
    unwritten_occurrences,
)
from app.services.cash_ledger import (
    TransactionDefinitionRow,
    resolve_transaction_amount,
    transaction_definition_cash,
)
from tests._test_helpers import (
    NO_TAX_LAW,
    generate_row_of,
    make_expense_template,
    state_template_price,
)
from tests.test_services.test_income_service._helpers import (
    _add_one_time_raise,
    _create_profile,
    _make_salary_template,
)

# The last day every walk here covers: past the horizon by three paydays.
_THROUGH = date(2026, 6, 30)

# The projected paydays through _THROUGH: 05-08 + 14 = 05-22, then 06-05, 06-19.
_PROJECTED_PAYDAYS = [date(2026, 5, 22), date(2026, 6, 5), date(2026, 6, 19)]

# The read pass's as-of, pinned so no case reads the ambient clock.  Neither
# the walk nor the pricing takes a bound from it.
_AS_OF = date(2026, 3, 1)


def _priced_projected_occurrences(template):
    """Return ``[(due date, price)]`` for *template*'s projected occurrences.

    Each occurrence the walk names on a projected paycheck through
    :data:`_THROUGH`, priced by the function under test over the read pass's
    own amount basis.
    """
    ctx = BalanceContext.build(template.user_id, _AS_OF)
    basis = ctx.amounts()
    return [
        (
            unwritten.due_date,
            transaction_definition_cash(
                TransactionDefinitionRow(
                    template=template,
                    due_date=unwritten.due_date,
                    period=unwritten.period,
                ),
                basis,
                subject=f"occurrence {unwritten.occurrence.isoformat()}",
            ),
        )
        for unwritten in unwritten_occurrences(
            template, ctx, through=_THROUGH, paychecks=Paychecks.PROJECTED,
        )
    ]


def _estimate_and_row(template, saved_period):
    """Return what a saved occurrence is estimated at and what its row resolves to.

    The row is the engine's own (:func:`generate_row_of`), so its due date
    and its declaration are what generation writes; the estimate is priced on
    that due date and on the saved paycheck the row lives in, as the read
    pass's calendar derives it.
    """
    row = generate_row_of(template, saved_period)
    db.session.commit()
    ctx = BalanceContext.build(template.user_id, _AS_OF)
    basis = ctx.amounts()
    estimate = transaction_definition_cash(
        TransactionDefinitionRow(
            template=template,
            due_date=row.due_date,
            period=ctx.calendar().period_by_id(saved_period.id),
        ),
        basis,
        subject="the saved occurrence",
    )
    return estimate, resolve_transaction_amount(row, basis)


class TestAStatedDefinitionIsPricedOnItsDueDate:
    """Rule 3: a bill is priced by its series on the date its row would carry."""

    @pytest.mark.usefixtures("seed_periods")
    def test_each_projected_bill_takes_the_price_in_force_on_its_due_date(
        self, seed_user,
    ):
        """A $15.49 bill that becomes $17.99 from 2026-06-01.

        Hand count: the projected rows would be due on their paydays, 05-22,
        06-05 and 06-19.  05-22 is before the new price, so $15.49; 06-05 and
        06-19 are on or after 06-01, so $17.99 each.  A pricing that read the
        newest price would answer $17.99 for 05-22, and one that read the
        price as of the read pass would answer $15.49 for all three.
        """
        template = make_expense_template(db.session, seed_user, amount="15.49")
        state_template_price(
            template, Decimal("17.99"), effective_on=date(2026, 6, 1),
        )
        db.session.commit()

        assert _priced_projected_occurrences(template) == [
            (date(2026, 5, 22), Decimal("15.49")),
            (date(2026, 6, 5), Decimal("17.99")),
            (date(2026, 6, 19), Decimal("17.99")),
        ]

    def test_a_saved_occurrence_is_estimated_at_what_its_row_resolves_to(
        self, seed_user, seed_periods,
    ):
        """The 01-16 bill's estimate and its written row both read $15.49.

        The series opens at $15.49 on the owner's today and holds flat before
        its earliest version, so a row due 01-16 is $15.49, and the estimate
        of the same occurrence must be the same figure.
        """
        template = make_expense_template(db.session, seed_user, amount="15.49")
        state_template_price(
            template, Decimal("17.99"), effective_on=date(2026, 6, 1),
        )
        db.session.commit()

        estimate, row_amount = _estimate_and_row(template, seed_periods[1])

        assert estimate == row_amount == Decimal("15.49")


class TestAPaycheckIsPricedOnItsOwnPaycheck:
    """Rule 2: a salary paycheck is priced by its profile on the period it lives in."""

    @pytest.mark.usefixtures("seed_periods")
    def test_each_projected_paycheck_is_priced_on_its_projected_period(
        self, seed_user, tax_law,
    ):
        """$4,000.00 a paycheck, a 3% raise from 2026-04-01, no tax.

        Hand count: every projected paycheck (05-22, 06-05, 06-19) is paid
        wholly after the raise, so each nets $4,000.00 x 1.03 = $4,120.00
        ($104,000.00 a year raised to $107,120.00, over 26 paychecks).  A
        pricing that held a figure from the saved schedule's first paycheck
        would read $4,000.00, and the old rule-3 arm refuses a salary
        definition outright, its price being derived.
        """
        tax_law(NO_TAX_LAW)
        profile = _create_profile(
            seed_user["user"].id, seed_user["scenario"].id,
        )
        _add_one_time_raise(profile, percentage="0.0300", effective_month=4)
        template = _make_salary_template(seed_user, profile)
        db.session.commit()

        assert _priced_projected_occurrences(template) == [
            (payday, Decimal("4120.00")) for payday in _PROJECTED_PAYDAYS
        ]

    def test_a_saved_paycheck_is_estimated_at_what_its_row_resolves_to(
        self, seed_user, seed_periods, tax_law,
    ):
        """The 01-16 paycheck's estimate and its written row both net $4,000.00.

        The raise lands 2026-04-01, after this paycheck, so it nets the
        unraised $4,000.00: the written row through the row's door
        (``income_service.salary_net_for``), the estimate through the
        occurrence's (``salary_net_on``), both ending in one net.
        """
        tax_law(NO_TAX_LAW)
        profile = _create_profile(
            seed_user["user"].id, seed_user["scenario"].id,
        )
        _add_one_time_raise(profile, percentage="0.0300", effective_month=4)
        template = _make_salary_template(seed_user, profile)
        db.session.commit()

        estimate, row_amount = _estimate_and_row(template, seed_periods[1])

        assert estimate == row_amount == Decimal("4000.00")

    def test_an_expense_a_profile_names_is_refused_as_its_row_is(
        self, seed_user, seed_periods, tax_law,
    ):
        """A salary profile naming an EXPENSE definition prices neither side.

        A profile states a net pay, so an expense row has no figure there
        (the template edit door refuses turning a paycheck into an expense,
        finding N-253; the state is built here directly).  Its written row is
        refused, and the estimate of the same definition past the horizon
        must be refused with it rather than priced at the profile's
        $4,000.00 or at the bill's dormant $100.00.
        """
        tax_law(NO_TAX_LAW)
        profile = _create_profile(
            seed_user["user"].id, seed_user["scenario"].id,
        )
        template = make_expense_template(db.session, seed_user, amount="100.00")
        profile.template_id = template.id
        db.session.flush()
        row = generate_row_of(template, seed_periods[1])
        db.session.commit()

        with pytest.raises(AmountUnresolvable):
            resolve_transaction_amount(
                row, BalanceContext.build(template.user_id, _AS_OF).amounts(),
            )
        with pytest.raises(AmountUnresolvable, match="not an income definition"):
            _priced_projected_occurrences(template)
