"""The figure each rule answers, hand-computed: a row's rules, the transfer rule ruling R-FI's
CHECK covers, the loan payment rule production cannot exercise, and a definition's price as its
rows' own rule (plan step R16-b-2).
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import AmountUnresolvable
from app.models.transfer_template import TransferTemplate
from app.services import pay_list_service, template_amount_service
from app.services.cash_ledger import (
    AmountRule,
    DefinitionRow,
    amounts_by_key,
    definition_cash,
    is_loan_payment_definition,
    resolve_transfer_amount,
)
from app.services.cash_ledger import transfer_amount_rule
from tests._test_helpers import NO_TAX_LAW
from tests._test_helpers import add_txn, create_savings_account, create_transfer, rebuild_calendar
from tests.oracles.recurrence_baseline import MONTHLY
from app.services.balance_at import BalanceContext
from app.services.row_valuation import settled_contribution
from tests._definition_price import newest_price

from ._helpers import (
    _OLD_PRICE,
    _NEW_PRICE,
    _PRICE_ROSE_ON,
    _PRICE_FELL_ON,
    _DUE_UNDER_OLD_PRICE,
    _PAYCHECK_UNDER_OLD_PRICE,
    _PAYCHECK_STRADDLING_THE_PRICE_FALL,
    _CALENDAR_OPENING_BEFORE_THE_SERIES,
    _basis_for,
    _resolve,
    _priced_template,
    _salary_template,
    _template_row,
    _legs_of,
    _priced_transfer_template,
    _template_transfer,
    _loan_payment,
    _declare_loan_payment_derived,
)


class TestWhatEachRuleAnswers:
    """The figure each rule produces, hand-computed."""

    def test_an_own_row_answers_its_stored_estimate(
        self, app, db, seed_user, seed_periods,
    ):
        """The OWN rule reads the column and nothing else."""
        txn = add_txn(db.session, seed_user, seed_periods[0], "Haircut", "35.00")
        assert _resolve(seed_user, txn) == Decimal("35.00")

    def test_an_own_row_answers_the_ESTIMATE_not_an_entered_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """The resolver answers the estimate half; a typed actual is a separate fact.

        ``effective_amount`` is ``actual ?? estimated``, and returning THAT here
        would make the resolver answer a human's realised figure where plan step
        X-au-c expects the budgeted one -- and would write it into the amount
        column at the freeze.  41 rows on the production clone carry an actual.
        """
        txn = add_txn(
            db.session, seed_user, seed_periods[0], "Groceries", "500.00",
            status_enum=StatusEnum.DONE, settled_amount="462.34",
        )
        assert settled_contribution(txn) == Decimal("462.34")
        assert _resolve(seed_user, txn) == Decimal("500.00")

    def test_an_excluded_row_answers_its_amount_not_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """A Cancelled row contributes nothing and still HAS an amount.

        ``effective_amount`` answers ``0`` for it; the amount column does not,
        and at X-au-c a resolver returning zero here would write ``0.00`` into
        the column of every excluded row.
        """
        txn = add_txn(
            db.session, seed_user, seed_periods[0], "Gym", "40.00",
            status_enum=StatusEnum.CANCELLED,
        )
        assert settled_contribution(txn) == Decimal("0")
        assert _resolve(seed_user, txn) == Decimal("40.00")

    def test_a_template_row_answers_the_price_in_effect_on_its_due_date(
        self, app, db, seed_user, seed_periods,
    ):
        """The series is resolved on the ROW's own due date, not on today.

        Due 2026-01-02 -- its paycheck's start -- falls between the 2026-01-01
        version (``$178.00``) and the 2026-03-01 one (``$165.30``), so the
        answer is the older price -- which
        is also the answer the ``default_amount`` scalar COULD NOT give, since
        ``set_amount`` kept that column at the newest stated figure.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[_PAYCHECK_UNDER_OLD_PRICE], template)
        assert _PRICE_ROSE_ON <= txn.due_date < _PRICE_FELL_ON
        assert newest_price(template) == _NEW_PRICE
        assert _resolve(seed_user, txn) == _OLD_PRICE

    def test_a_later_row_of_the_same_template_answers_the_later_price(
        self, app, db, seed_user, seed_periods,
    ):
        """One definition, two rows, two prices -- which is what a series is for.

        The row is DATED on a day its paycheck does not START on: the
        definition is MONTHLY on the 1st, its price falls on 2026-03-01, and
        the engine's row in the paycheck 2026-02-27..03-12 is due 2026-03-01
        -- so this case fails a resolver that substitutes the period's start
        for the row's due date, the mutation the transfer twin below was
        written against.  **It could not, from plan step X-cf-3b until
        X-ch**: under the every-paycheck cadence the engine dates a row on
        its paycheck's start, so both readings named one day, and the only
        thing in this package that caught the substitution on the transaction
        arm was the no-due-date refusal, which plan step X-bv-2 deletes.  The
        adversarial review of X-ch measured it on the transfer twin and the
        same mutation was run here.
        """
        template = _priced_template(seed_user, cadence=MONTHLY, fires_on_day=1)
        txn = _template_row(
            seed_periods[_PAYCHECK_STRADDLING_THE_PRICE_FALL], template,
        )
        assert txn.pay_period.start_date < _PRICE_FELL_ON <= txn.due_date
        assert _resolve(seed_user, txn) == _NEW_PRICE

    def test_a_row_older_than_the_series_holds_at_the_earliest_price(
        self, app, db, seed_user,
    ):
        """Before the first version the series holds FLAT (ruling R-I's shape).

        The arm that makes the resolver total for a row generated into a
        historical period; 23 rows on the production clone are due before their
        template's earliest version.  The seeded calendar opens the day after
        the series does, so the row's paycheck comes from a calendar rebuilt
        to open in 2025 -- BEFORE the definition is built, because an
        every-paycheck rule's first occurrence is the calendar's opening day.
        """
        periods = rebuild_calendar(
            seed_user["user"].id, _CALENDAR_OPENING_BEFORE_THE_SERIES, 4, 14,
        )
        template = _priced_template(seed_user)
        txn = _template_row(periods[0], template)
        assert txn.due_date < _PRICE_ROSE_ON
        assert _resolve(seed_user, txn) == _OLD_PRICE

    def test_a_salary_row_answers_its_PROFILE_and_stores_no_figure(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """The SALARY rule routes to the profile, and the row holds nothing.

        **There is no second producer of a net paycheck since plan step
        X-au-d**, so an equality against ``income_service.salary_net_for``
        would be one producer graded against itself -- the shape
        ``X-au-g-2c-3c`` measured false after 200,000 agreeing draws.  What is
        gradeable is that the answer TRACKS THE PROFILE and comes from nowhere
        else: the row carries no figure at all (the declaration empties it), so
        a column-reading resolver answers ``None``, and re-pricing the PROFILE
        moves the answer.  The arithmetic is the paycheck engine's and is
        graded by its own suites.

        **The profile is re-priced through its PAY ENTRY since plan step
        salary:X-av-3a**, which dropped ``annual_salary`` for
        ``salary.pay_entries``: the Fix door doubles the one paycheck the
        profile stores, where the test doubled the yearly salary the engine
        divided by 26.
        """
        tax_law(NO_TAX_LAW)
        template, profile = _salary_template(seed_user)
        txn = _template_row(seed_periods[0], template)
        assert txn.estimated_amount is None
        before = _resolve(seed_user, txn)
        # PINNED, not merely positive: this suite seeds no tax configs, so the
        # net IS the gross and the arithmetic is visible -- $75,000 over the
        # 26 paychecks the seeded calendar's cadence gives.  An adversarial
        # review of plan step X-au-d noted that ``> 0`` and ``after > before``
        # are satisfied by any monotone dependence on the salary, so a change
        # to the divisor or the rounding would pass.
        assert before == Decimal("2884.62"), "75000 / 26, no tax configs seeded"

        # Doubled through the Fix door, the route's own call; the payday is
        # unchanged, so the door checks no day.
        entry = profile.pay_entries[0]
        ctx = BalanceContext.build(seed_user["user"].id)
        pay_list_service.fix_entry(
            entry, ctx, entry.amount * 2, entry.payday, ctx.as_of,
        )
        db.session.flush()
        # A FRESH basis: the projection is memoized for the life of one, which
        # is the sharing ``SalaryPricing`` exists for, so re-asking the old one
        # would assert the memo rather than the rule.
        after = _resolve(seed_user, txn)

        # 2 x $2,884.62 = $5,769.24, the doubled paycheck with no tax.  It was
        # $5,769.23 while the engine divided a doubled YEAR: 150,000 / 26 =
        # 5,769.2308 -> 5,769.23 (ruling R-SAL59 stores the paycheck).
        assert after == Decimal("5769.24"), "2 x 2884.62"
        assert after > before, (
            "doubling the salary must move the paycheck this rule answers; a "
            f"rule reading anything else would still answer {before}"
        )

    def test_a_transfer_leg_answers_its_parents_RULE_not_its_column(
        self, app, db, seed_user, seed_periods,
    ):
        """A leg answers its transfer's RULE: one row holds what both sides read.

        Neither column can hold a rival figure any more -- the declaration empties
        both -- so the assertion discriminates between the definition's
        ``$178.00`` and a ``None`` from either. That is a stronger control than
        the three distinct figures this test used to plant, and it is the
        difference plan step X-au-c2 makes: before it, the shadow's own column
        and its parent's were both populated and only the review's three-figure
        fixture told a column-reading resolver from a rule-resolving one.

        **Renamed and re-expressed at plan step balance:X-bi-6-4d-2** (it was
        ``test_a_transfer_shadow_answers_its_parents_RULE_not_either_column``;
        ruling **R-BAL167** class 1).  No shadow is born, so the expense side
        is its LEG, priced by ``amounts_by_key``; the shadow's empty column
        went with the shadow, and the parent's empty column stands.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        template = _priced_transfer_template(seed_user, savings)
        xfer = _template_transfer(seed_periods[_PAYCHECK_UNDER_OLD_PRICE], template)
        expense_leg, _income_leg = _legs_of(xfer)
        assert xfer.amount is None
        assert amounts_by_key([expense_leg], _basis_for(seed_user)) == {
            expense_leg.cell_key: _OLD_PRICE,
        }

    def test_both_legs_of_one_transfer_answer_the_same_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """Both legs of one transfer answer their parent, not themselves.

        The two legs used to be stored at DIFFERENT figures so a resolver reading
        each shadow's own column would answer two numbers where one is right.
        The declaration emptied both columns instead, so such a resolver
        answered ``None`` twice until plan step balance:X-bi-6-4d-2.

        **Renamed and re-expressed at plan step balance:X-bi-6-4d-2** (it was
        ``test_both_shadows_of_one_transfer_answer_the_same_figure``; ruling
        **R-BAL167** class 1).  The two legs are read off one transfer and
        priced by ``amounts_by_key``, one ``resolve_transfer_amount`` on
        that transfer each, so they cannot answer two figures: what is
        asserted is that BOTH sides are priced, each at the parent's rule.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        template = _priced_transfer_template(seed_user, savings)
        xfer = _template_transfer(seed_periods[_PAYCHECK_UNDER_OLD_PRICE], template)
        assert amounts_by_key(_legs_of(xfer), _basis_for(seed_user)) == {
            (xfer.id, seed_user["account"].id): _OLD_PRICE,
            (xfer.id, savings.id): _OLD_PRICE,
        }

    def test_a_leg_of_an_adhoc_transfer_answers_the_parents_own_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """An ad-hoc parent owns its amount, and the leg follows it there.

        Zero ad-hoc transfers exist on the production clone, so this shape is
        graded here and nowhere else -- and it is the arm that decides whether
        the transfer rule is total for a transfer nobody generated.

        **Renamed and re-expressed at plan step balance:X-bi-6-4d-2** (it was
        ``test_a_shadow_of_an_adhoc_transfer_answers_the_parents_own_figure``;
        ruling **R-BAL167** class 1): the expense side is its leg, priced by
        ``amounts_by_key``, where the expense shadow was declared derived
        and resolved.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer = create_transfer(
            seed_user, seed_user["account"], savings,
            seed_periods[0], amount=Decimal("75.00"),
        )
        expense_leg, _income_leg = _legs_of(xfer)
        assert xfer.amount_source_id is None
        assert amounts_by_key([expense_leg], _basis_for(seed_user)) == {
            expense_leg.cell_key: Decimal("75.00"),
        }


class TestTheTransferRule:
    """``resolve_transfer_amount``: the second column ruling R-FI's CHECK covers."""

    def test_an_adhoc_transfer_owns_its_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """Nobody generated it, so no definition states its price."""
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer = create_transfer(
            seed_user, seed_user["account"], savings,
            seed_periods[0], amount=Decimal("75.00"),
        )
        assert resolve_transfer_amount(xfer, _basis_for(seed_user)) == Decimal("75.00")

    def test_a_generated_transfer_answers_its_definitions_series(
        self, app, db, seed_user, seed_periods,
    ):
        """There is no stored column to bypass: the series is asked on the due date."""
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        template = _priced_transfer_template(seed_user, savings)
        xfer = _template_transfer(seed_periods[_PAYCHECK_UNDER_OLD_PRICE], template)
        assert xfer.amount is None
        assert resolve_transfer_amount(xfer, _basis_for(seed_user)) == _OLD_PRICE

    def test_a_later_transfer_of_the_same_template_answers_the_later_price(
        self, app, db, seed_user, seed_periods,
    ):
        """The transfer arm resolves on the TRANSFER's own due date.

        The sibling of the transaction-side date test, and the review's mutation
        proved it was missing: substituting the pay period's START for the due
        date -- exactly what ``_stated_amount``'s docstring argues against, since
        a period begins up to two weeks before the installment it funds -- moved
        no test and no production row.  Here the definition is MONTHLY on the
        1st and the transfer is the engine's in the paycheck that covers
        2026-03-01 without starting on it: the period starts 2026-02-27,
        before the 2026-03-01 version, while the transfer is due 2026-03-01,
        on it -- so the two dates select different prices.  **An
        every-paycheck cadence cannot build this case**, because the engine
        dates such a row on its paycheck's start and the substitution reads
        the same day; the adversarial review of plan step balance:X-ch caught
        the conversion disarming it exactly that way.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        template = _priced_transfer_template(
            seed_user, savings, cadence=MONTHLY, fires_on_day=1,
        )
        xfer = _template_transfer(
            seed_periods[_PAYCHECK_STRADDLING_THE_PRICE_FALL], template,
        )
        assert xfer.pay_period.start_date < _PRICE_FELL_ON <= xfer.due_date
        assert resolve_transfer_amount(xfer, _basis_for(seed_user)) == _NEW_PRICE

    def test_a_generated_transfer_that_carries_a_figure_owns_it(
        self, app, db, seed_user, seed_periods,
    ):
        """A definition behind it does not make a transfer derived: the column does.

        The transfer twin of ``test_which_rule_prices_a_row.py``'s
        :meth:`TestWhichRulePricesARow.test_a_row_that_took_its_figure_back_owns_it_again`,
        and the shape a re-priced transfer and a frozen one both leave.  Its
        definition still states ``$178.00`` on this due date, and the transfer
        answers its own ``$111.11``.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        template = _priced_transfer_template(seed_user, savings)
        xfer = _template_transfer(
            seed_periods[_PAYCHECK_UNDER_OLD_PRICE], template, owns=True,
        )
        assert template_amount_service.amount_as_of(
            template, _DUE_UNDER_OLD_PRICE,
        ) == _OLD_PRICE
        assert resolve_transfer_amount(xfer, _basis_for(seed_user)) == Decimal("111.11")

    @pytest.mark.parametrize("status", [
        StatusEnum.DONE, StatusEnum.CANCELLED,
    ])
    def test_a_transfers_status_does_not_decide_which_rule_prices_it(
        self, app, db, seed_user, seed_periods, status,
    ):
        """Leaving Projected does not take a declared transfer's relation back.

        The transfer half of finding **N-262**, and the reason
        ``resolve_transfer_amount`` stopped consulting
        ``balance_predicates.is_projected`` at plan step X-au-c2: a Cancelled
        transfer never enters the settled band, so no freeze writes it a figure,
        and a status-driven OWN arm would refuse the empty column that is left.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        template = _priced_transfer_template(seed_user, savings)
        xfer = _template_transfer(seed_periods[_PAYCHECK_UNDER_OLD_PRICE], template)
        xfer.status_id = ref_cache.status_id(status)
        db.session.flush()
        assert resolve_transfer_amount(xfer, _basis_for(seed_user)) == _OLD_PRICE


class TestTheLoanPaymentRule:
    """The rule production cannot exercise: ``loan_payment_settings`` is empty there."""

    def test_a_derive_mode_payment_answers_the_loans_own_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """P&I + the escrow in effect on the installment's due date.

        Loan $200,000 / 6% / 360mo, escrow $3,600/yr:
            P&I    = amortize(200000, 0.06, 360) = 1,199.10
            escrow = 3,600 / 12                  =   300.00
            PITI                                 = 1,499.10

        The definition says ``$1,300.00`` -- a figure the loan's own answer is
        not.

        **Renamed and re-expressed at plan step balance:X-bi-6-4d-2** (it was
        ``test_a_derive_mode_shadow_answers_the_loans_own_figure``; ruling
        **R-BAL167** class 1): asked of the TRANSFER, whose rule the shadow
        answered.  The sentence above named three figures -- the definition's,
        the parent's ``$1,250.00`` and the shadow's ``$1,200.00`` -- and this
        fixture stores neither of the other two: the payment is not taken for
        an owner, and no shadow is born.
        """
        xfer = _loan_payment(seed_user, derive=True)
        basis = _basis_for(seed_user)
        assert resolve_transfer_amount(xfer, basis) == Decimal("1499.10")

    def test_a_manual_payment_with_no_extra_answers_its_stated_base(
        self, app, db, seed_user, seed_periods,
    ):
        """Manual mode's base is a STATED amount, so its DEFINITION holds it.

        The definition states ``$1,300.00``.  A review found the earlier
        fixture setting every figure equal, which made the test pass for every
        candidate implementation -- and the loan's own P&I of ``$1,199.10`` must
        not appear either.

        Asked of the TRANSFER since plan step balance:X-bi-6-4d-2, whose rule
        the shadow answered (ruling **R-BAL167** class 1).  The sentence above
        also named the parent's ``$1,250.00`` and the shadow's ``$1,200.00``,
        and this fixture stores neither: the payment is not taken for an
        owner, and no shadow is born.
        """
        xfer = _loan_payment(seed_user, derive=False)
        basis = _basis_for(seed_user)
        assert transfer_amount_rule(xfer) is AmountRule.LOAN_PAYMENT
        assert resolve_transfer_amount(xfer, basis) == Decimal("1300.00")

    def test_a_manual_payment_answers_the_same_base_with_and_without_an_extra(
        self, app, db, seed_user, seed_periods,
    ):
        """One mode, one expression: the extra is added, never a different base.

        The defect this test exists for, reproduced by an adversarial review:
        ``live_loan_transfer_amounts`` priced a manual payment from
        ``shadow.estimated_amount + extra``, so a rule that used the live map
        whenever it had an entry answered ``$1,200.00 + $150.00`` with a standing
        extra and ``$1,300.00`` (the series) without one -- the same payment
        priced from two different bases, one of them the stored column ruling
        R-FI deletes.

        **The rival answer is named as a NUMBER now, and plan step X-au-g-2c-2
        is why.**  It used to be produced: the payment was built owning its
        figure, ``LoanPricing.live_cash`` was asked and answered ``$1,350.00``
        from ``shadow.estimated_amount + extra``, and the row was declared
        afterwards so the resolver could be watched ignoring it.  That producer
        is deleted, so what stands is the arithmetic it would have done --
        ``$1,200.00 + $150.00`` -- asserted against the column while the shadow
        still held one, and then contradicted by the resolver once the shadow
        was derived.  The discrimination was the same and one producer shorter.

        **The rival base is the TRANSFER's stored ``$1,250.00`` since plan
        step balance:X-bi-6-4d-2** -- ruling **R-BAL272**, the developer's
        answer, picked verbatim: "Use $1,250.00 (Recommended)": "The stored
        figure the test names becomes the transfer's own $1,250.00 (a wrong
        answer would be $1,250.00 + $150.00 = $1,400.00). The test then makes
        the payment follow its definition, checks the stored figure is gone,
        and checks the price is still $1,450.00. Same check as before, on the
        one row that can still hold a figure. Listed in the commit."  The
        shadow's ``$1,200.00`` it
        named before went with the shadow, which the database refuses (ruling
        **R-BAL258**).
        """
        xfer = _loan_payment(
            seed_user, derive=False, extra=Decimal("150.00"),
            owns=True,
        )
        basis = _basis_for(seed_user)
        # The stored base a wrong rule would add the extra to: the transfer's
        # own $1,250.00, which would answer $1,250.00 + $150.00 = $1,400.00
        # rather than the definition's $1,300.00 + $150.00.
        assert xfer.amount == Decimal("1250.00")
        _declare_loan_payment_derived(xfer)
        # Declaring it EMPTIES that column, so the rival base is not merely
        # unread -- it is unconstructible.  Asserting the absence says which of
        # the two facts holds; a ``!= 1400`` beside the equality below would be
        # entailed by it and could never fail on its own.
        assert xfer.amount is None
        assert resolve_transfer_amount(xfer, basis) == Decimal("1450.00")

    def test_a_derive_mode_payment_whose_loan_will_not_resolve_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """No loan behind it, so its P&I has no answer and nothing substitutes.

        The destination is an ordinary savings account, so the loan calendar
        (``loan_ledger.LoanCalendars.loan_calendar_of``, ``_resolve_loan_basis``
        until plan step recurrence:R25) answers nothing.  A fallback here would publish
        the stored figure, which on a derive-mode payment is a snapshot of
        exactly the computation that just failed.
        """
        savings = create_savings_account(
            seed_user, db.session, "Not A Loan", Decimal("5000.00"),
        )
        xfer = _loan_payment(
            seed_user, derive=True, to_account=savings,
        )
        basis = _basis_for(seed_user)
        # Asked of the TRANSFER, whose rule the shadow answered, since plan
        # step balance:X-bi-6-4d-2 (ruling R-BAL167 class 1).
        with pytest.raises(AmountUnresolvable, match="would not resolve"):
            resolve_transfer_amount(xfer, basis)


def _row_of(xfer) -> DefinitionRow:
    """The four pricing columns a written transfer carries, as the estimate sees them."""
    return DefinitionRow(
        template=xfer.template,
        due_date=xfer.due_date,
        period_start=xfer.pay_period.start_date,
        to_account_id=xfer.to_account_id,
    )


class TestTheDefinitionPriceIsTheRowsOwnRule:
    """:func:`~app.services.cash_ledger.definition_cash`, plan step **R16-b-2** (R-R67).

    The balance seam's ESTIMATED loan tier prices an occurrence no row answers
    yet, and the estimate must be what the row would resolve to -- or the
    loan's payoff moves the moment generation writes the row.  Each test
    prices a WRITTEN transfer twice: through the row's own rule and through
    :func:`definition_cash` over the same four columns, and asserts the two
    equal.  The equality is the property; the arithmetic each case pins is
    the sibling class's, restated so a wrong shared arm cannot pass by
    agreeing with itself.
    """

    def test_a_series_priced_definition_answers_the_rows_rule_3_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """Rule 3: the definition's series as of the row's due date, $178.00."""
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        template = _priced_transfer_template(seed_user, savings)
        xfer = _template_transfer(seed_periods[_PAYCHECK_UNDER_OLD_PRICE], template)
        basis = _basis_for(seed_user)
        row = _row_of(xfer)

        estimate = definition_cash(row, basis, subject="the estimate")

        assert estimate == resolve_transfer_amount(xfer, basis)
        assert estimate == _OLD_PRICE
        assert not is_loan_payment_definition(xfer.template)

    def test_a_derive_mode_loan_payment_answers_the_rows_rule_4_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """Rule 4, derive arm: P&I $1,199.10 + escrow $300.00 = $1,499.10."""
        xfer = _loan_payment(seed_user, derive=True)
        basis = _basis_for(seed_user)
        row = _row_of(xfer)

        estimate = definition_cash(row, basis, subject="the estimate")

        assert estimate == resolve_transfer_amount(xfer, basis)
        assert estimate == Decimal("1499.10")
        assert is_loan_payment_definition(xfer.template)

    def test_a_manual_loan_payment_with_an_extra_answers_the_rows_rule_4_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """Rule 4, manual arm: the stated $1,300.00 plus the standing $150.00."""
        xfer = _loan_payment(
            seed_user, derive=False, extra=Decimal("150.00"),
        )
        basis = _basis_for(seed_user)
        row = _row_of(xfer)

        estimate = definition_cash(row, basis, subject="the estimate")

        assert estimate == resolve_transfer_amount(xfer, basis)
        assert estimate == Decimal("1450.00")

    def test_an_empty_series_refuses_and_names_the_subject(
        self, app, db, seed_user, seed_periods,
    ):
        """No contract fallback: the estimate refuses exactly where the row would.

        ``standing_installment_cash`` answered the CONTRACT for a definition
        whose series was empty, which this replaces; a figure nobody stated is
        refused, and the message names what was being priced rather than a
        row id it does not have.
        """
        savings = create_savings_account(
            seed_user, db.session, "Never Priced", Decimal("5000.00"),
        )
        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name="Never Stated",
        )
        db.session.add(template)
        db.session.flush()
        basis = _basis_for(seed_user)
        row = DefinitionRow(
            template=template, due_date=date(2026, 2, 1),
            period_start=seed_periods[0].start_date,
            to_account_id=savings.id,
        )

        with pytest.raises(AmountUnresolvable, match="price series is EMPTY") as caught:
            definition_cash(row, basis, subject="template 7's occurrence 2026-02-01")
        assert "template 7's occurrence 2026-02-01" in str(caught.value)

    def test_a_derive_mode_definition_with_no_loan_behind_it_refuses(
        self, app, db, seed_user, seed_periods,
    ):
        """The derive arm's refusal fires for an occurrence exactly as for a row."""
        savings = create_savings_account(
            seed_user, db.session, "Not A Loan", Decimal("5000.00"),
        )
        xfer = _loan_payment(
            seed_user, derive=True, to_account=savings,
        )
        basis = _basis_for(seed_user)

        with pytest.raises(AmountUnresolvable, match="would not resolve"):
            definition_cash(_row_of(xfer), basis, subject="the estimate")
