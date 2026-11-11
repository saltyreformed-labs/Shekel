"""
Shekel Budget App -- Income Service Tests (C17 / F-20 / MED-06 / F-032).

Pins the raise-aware paycheck-engine producer contract:

- The helper returns ``Decimal("0")`` when no active SalaryProfile exists.
- The helper returns the profile's pay a paycheck -- its pay list's entry
  since plan step salary:X-av-3a (ruling R-SAL59) -- byte-identical to the
  engine for a no-raise profile.
- The helper APPLIES applicable ``SalaryRaise`` rows so the post-raise
  per-period gross is returned -- the F-032 worked example: $4,000.00 a
  paycheck ($104,000 a year over 26) with a 3% raise effective in the as-of
  period yields $4,120.00 per period, not the pre-Commit-17 off-engine
  $4,000.00.
- Every downstream consumer (savings, year-end, retirement, investment)
  reads the same engine-derived value through the helper for a
  raise-applicable user.

Test fixture math (hand-computed):

- one pay entry of ``$4,000.00`` a paycheck (``$104,000`` a year over 26)
  + 3% one-time raise effective 2026-03
- Post-raise pay a paycheck: ``4000.00 * 1.03 = 4,120.00`` ->
  ``Decimal("4120.00")``, the raise rounding to the cent (ruling R-SAL60)
- Pre-fix (no raise applied): ``4,000.00`` -> the pre-Commit-17 value the
  off-engine sites returned.
"""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.services.cash_flow_set import CashFlowSet
from app.enums import RaiseTypeEnum
from app.extensions import db
from app.models.ref import FilingStatus, RaiseType, Status, TransactionType
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.transaction_template import TransactionTemplate
from app.services.pay_calendar import (
    calendar_for,
    days_paid_for,
    paydays_in_year_before,
)
from app.services.salary_raises import RaiseTerms, terms_of
from app.services.projection_inputs import load_payroll_feeds
from app.services import (
    balance_at,
    income_service,
    paycheck_calculator,
)
from tests._tax_configs import tax_configs_for
from app.tax_law import FicaRules, TaxLaw
from app.services.balance_at import BalanceContext
from tests._test_helpers import (
    NO_TAX_LAW,
    made_up_state,
    made_up_year,
    all_periods,
    counting_calls,
    freeze_today,
    generate_row_of,
    make_every_period_rule,
    make_investment_account,
    one_off_row_of,
    payroll_basis,
    pricing_over,
    repriced_by_the_owner,
    start_test_pay_list,
)


# Hand-computed expected values (see module docstring for derivation).
_RAISE_APPLIED_GROSS = Decimal("4120.00")  # the 4,000.00 entry x 1.03
_NO_RAISE_GROSS = Decimal("4000.00")  # the entry: $104,000 a year / 26
#: Inside seed_periods period 6, 03-27, the first paycheck whose pay period
#: (03-08..03-21) opens after the March 1 raise: period 5, 03-13, pays
#: 02-22..03-07 and so half of it (ruling plan#3089, task plan#3100).
_AS_OF_AFTER_RAISE = date(2026, 3, 29)
_AS_OF_BEFORE_RAISE = date(2026, 1, 5)  # inside seed_periods period 0


def _create_profile(
    user_id: int, scenario_id: int, *,
    pay: str = "4000.00",  # $104,000.00 a year / 26
) -> SalaryProfile:
    """Create an active SalaryProfile for the user.

    Helper isolates the FilingStatus lookup + required-column boilerplate
    so each test reads as fixture composition rather than ORM ceremony.
    """
    filing = db.session.query(FilingStatus).first()
    profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=user_id,
        scenario_id=scenario_id,
        filing_status_id=filing.id,
        name="Test Salary",
        state_code="NC",
        is_active=True,
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal(pay))
    db.session.flush()
    return profile


def _add_one_time_raise(
    profile: SalaryProfile, *, percentage: str = "0.0300",
    effective_month: int = 3, effective_year: int = 2026,
) -> SalaryRaise:
    """Attach a one-time percentage raise to the profile."""
    merit = db.session.query(RaiseType).filter_by(name="merit").one()
    salary_raise = SalaryRaise(
        salary_profile_id=profile.id,
        raise_type_id=merit.id,
        effective_date=date(effective_year, effective_month, 1),
        percentage=Decimal(percentage),
        is_recurring=False,
    )
    db.session.add(salary_raise)
    db.session.flush()
    return salary_raise


def _make_salary_template(seed_user, profile, *, name="Paycheck", account=None):
    """Create an Income template and link ``profile`` to it.

    The producer treats a transaction as salary-linked iff its
    ``template_id`` maps to an active SalaryProfile for the scenario, so
    the test must set ``profile.template_id`` to the created template.

    **It carries a cadence and states NO price**, and the second is not a
    choice this fixture gets to make.  The cadence is what lets the engine
    write the definition's rows (:func:`generate_row_of`, plan step
    balance:X-cf).  A salary-linked definition's amount is DERIVED rather
    than stated (``template_amount_service.owns_its_amount`` is False once
    the profile names it), so ``set_amount`` on it would set the scalar and
    open no series, and amount rule 3 refuses such a row at that arm before
    any series is read (``_stated_amount``) -- which is what keeps a
    dispatch that fell through to rule 3 from answering the scalar's
    ``$4,000.00``, the profile's own no-raise net.  Measured under review
    2026-09-11: ``_rule_within_definition`` mutated to answer TEMPLATE fails
    here with that refusal, not with an empty-series one.

    Args:
        seed_user: The seeded owner bundle.
        profile: The :class:`~app.models.salary_profile.SalaryProfile` that
            prices this definition's rows; its ``template_id`` is set here.
        name: The definition's name.
        account: The account the paycheck lands on; the seed user's checking
            account when omitted.  The engine puts a row on its definition's
            account, so a case wanting the paycheck on an HYSA says so here.
    """
    income_type = (
        db.session.query(TransactionType).filter_by(name="Income").one()
    )
    category = next(iter(seed_user["categories"].values()))
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=(seed_user["account"] if account is None else account).id,
        category_id=category.id,
        transaction_type_id=income_type.id,
        name=name,
    )
    db.session.add(template)
    db.session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    make_every_period_rule(db.session, template)
    profile.template_id = template.id
    db.session.flush()
    return template


def _make_txn(
    seed_user, period, *, template=None, type_name="Income",
    status_name="Projected", owned_amount=None,
):
    """Create a single Transaction in ``period`` for the producer tests.

    Two arms, decided by whether a definition is named (plan step
    balance:X-cf):

    * **A row of a DEFINITION** (*template* given) is the ENGINE's row of it
      in *period* (:func:`generate_row_of`): derived, dated, answering an
      occurrence, Projected.  *type_name* is not read -- the row's type is
      its definition's.  *status_name* other than Projected is then laid on
      bare, and *owned_amount* makes it the OWNER's re-priced row through the
      re-price door's two acts (``state_own_amount`` and ``is_override``).
    * **A ONE-OFF** (no *template*) is placed through the producer
      (:func:`one_off_row_of`, plan step balance:X-bi-7c): a rule-less
      definition of its own priced at *owned_amount*, which it must state,
      and the row derived from it -- the shape the grid writes for a one-off.

    ``derived`` was a switch here until X-cf-3: with the engine writing the
    row there is no other shape a non-overridden salary row can have, so the
    flag named the only state and went.

    Args:
        seed_user: The seeded owner bundle.
        period: The pay period the row is funded in.
        template: The definition whose row is wanted, or ``None``.
        type_name: The one-off's transaction type.
        status_name: The status to give the row.
        owned_amount: The figure, as a string.  On a definition's row it is
            what a human re-priced it to (the row then OWNS it); on a one-off
            it is what its own definition is priced at, and is required.

    Returns:
        The flushed :class:`~app.models.transaction.Transaction`.

    Raises:
        ValueError: A one-off with no *owned_amount*: its definition is
            priced at something, and this helper will not guess what.
    """
    status = db.session.query(Status).filter_by(name=status_name).one()
    if template is not None:
        txn = generate_row_of(template, period)
        if owned_amount is not None:
            repriced_by_the_owner(txn, owned_amount)
        txn.status_id = status.id
        db.session.flush()
        return txn
    if owned_amount is None:
        raise ValueError("a one-off's definition is priced; pass owned_amount")
    txn_type = (
        db.session.query(TransactionType).filter_by(name=type_name).one()
    )
    category = next(iter(seed_user["categories"].values()))
    txn = one_off_row_of(
        period,
        name="producer-test txn",
        amount=Decimal(owned_amount),
        user_id=period.user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=txn_type.id,
        category_id=category.id,
    )
    txn.status_id = status.id
    db.session.flush()
    return txn


def _net_map(user_id, scenario_id, rows):
    """What the salary derivation answers for each of *rows*, where it answers.

    Plan step X-au-c2b split the producer into an owner-scoped DERIVATION
    (:func:`income_service.salary_pricing`) and a per-row lookup
    (:func:`income_service.salary_net_for`), so the map these tests grade is
    something a caller builds rather than something the producer returns.

    **It was ``_live_net_map`` and asked the READ-TIME REPAIR until plan step
    X-au-d**, which deleted that repair: a salary row is DECLARED derived and
    stores no figure, so there is nothing for a repair to supersede and
    ``salary_net_for`` is the one producer.  The difference is not cosmetic --
    the repair filtered to Projected non-overridden rows and the PRICING rule
    filters on neither (finding **N-262**), which is what the class below now
    grades.
    """
    pricing = income_service.salary_pricing(
        scenario_id, income_service.derived_paycheck_pricing(user_id),
    )
    answers = {}
    for txn in rows:
        net = income_service.salary_net_for(txn, pricing)
        if net is not None:
            answers[txn.id] = net
    return answers


class TestSalaryNetFor:
    """Unit tests for ``income_service.salary_net_for`` -- amount rule 2's body.

    Locks the two properties the amount model relies on: the producer
    (a) answers what the salary PROFILE pays for the row's own period, and
    (b) answers for a row it can PRICE, which is a question about the
    definition and never about whether the row still counts.

    **Half (b) is the inversion plan step X-au-d completed.**  This class
    graded a read-time repair, whose candidate set was "Projected,
    non-overridden, salary-linked income" -- so a Cancelled or hand-priced
    paycheck was refused for reasons that have nothing to do with what a
    paycheck is worth.  Pricing asks the definition; whether a row still counts
    is finding **N-262**'s separate question, answered above this rule by
    ``row_valuation.fixed_contribution``, and WHO owns the figure is answered
    by the row's declaration rather than by ``is_override``.
    """

    def test_recomputes_live_ignoring_stored_amount(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """A salary-linked income row maps to what its PROFILE pays.

        The row is the engine's, RE-PRICED by its owner to a deliberately
        wrong ``$1.00`` (the re-price door's two acts) so the two answers
        stay distinguishable at this tier: a producer reading the column
        would answer ``$1.00`` and the profile answers ``$4,000.00``.  The
        DECLARED shape -- where the column is empty and the distinction is
        structural rather than measured -- is graded one tier up, by
        ``test_amount_source`` over the rule this producer is the body of.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()

            period = all_periods(user_id)[5]
            txn = _make_txn(
                seed_user, period, template=template, owned_amount="1.00",
            )
            db.session.commit()

            overrides = _net_map(user_id, scenario_id, [txn])

            # $104,000 profile, no raise, no tax configs seeded -> net =
            # gross = 104000 / 26 = $4,000.00 (hand-computed; the sibling
            # balance-resolver test pins the same value for this setup).
            # The producer must return this LIVE net, never the stale $1.00.
            expected_net = Decimal("4000.00")
            assert overrides == {txn.id: expected_net}
            assert overrides[txn.id] != Decimal("1.00")

    def test_a_period_this_owners_calendar_does_NOT_hold_is_refused(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """A ``pay_period_id`` off this owner's calendar answers ``None``.

        **Amount rule 2's SECOND refusal, and plan step salary:S3-d rewrote
        how it is reached.**  It was a miss in a ``{period_id: breakdown}``
        map built from this owner's saved window; it is
        :meth:`~app.services.pay_calendar.PayCalendar.period_by_id` returning
        ``None`` now.  Both refuse, and the second is scoped because the
        calendar is built from the owner's id -- but "the map was the owner's"
        and "the lookup is scoped" are different guarantees, and swapping the
        second for an unscoped lookup would leave every other case in this
        file passing.

        That is the shape where a moved door disarms the control in front of
        it, so the refusal is asserted directly rather than inferred from the
        producer that happens to sit behind it today.  An adversarial review
        of S3-d found this branch untested.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)
            _make_salary_template(seed_user, profile)
            db.session.commit()

            foreign_period_id = all_periods(seed_second_user["user"].id)[0].id
            own_period_ids = {p.period_id for p in _derived(user_id)}
            assert foreign_period_id not in own_period_ids, (
                "the two fixtures share a pay-period id, so this case would "
                "grade nothing"
            )

            pricing = income_service.salary_pricing(
                scenario_id, income_service.derived_paycheck_pricing(user_id),
            )
            assert pricing.net_for(
                profile.template_id, foreign_period_id,
            ) is None

    def test_prices_by_the_DEFINITION_and_never_by_the_rows_status(
        self, app, db, seed_user, seed_periods,
    ):
        """What a paycheck is WORTH does not depend on whether it still counts.

        **This case asserted the opposite until plan step X-au-d**, and the
        inversion is the step.  It graded a read-time repair whose candidate
        set was "Projected, non-overridden, salary-linked income", so it
        asserted that a RECEIVED paycheck and an OVERRIDDEN one were omitted.
        Those two omissions were the repair's status gate, not a pricing rule
        (finding **N-262**): a Received paycheck is worth exactly what the
        profile paid for its period, and whether the app should USE that figure
        is a different question answered above this producer.

        Five rows, and the split is now by DEFINITION rather than by status:
        the three on the salary template are all priced -- Projected, Received
        and overridden alike -- while non-salary income (a template no profile
        names) and an expense are not, because no salary profile states what
        they are worth.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)
            template = _make_salary_template(seed_user, profile)
            income_type = (
                db.session.query(TransactionType).filter_by(name="Income").one()
            )
            category = next(iter(seed_user["categories"].values()))
            other_template = TransactionTemplate(
                user_id=user_id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=income_type.id,
                name="Non-salary income",
            )
            db.session.add(other_template)
            db.session.flush()
            # The cadence the engine needs to write its row; no price, because
            # nothing here prices this row and an empty series is what refuses
            # a producer that tried.
            make_every_period_rule(db.session, other_template)
            db.session.commit()

            periods = all_periods(user_id)
            # Distinct periods: an every-paycheck definition names ONE
            # occurrence per paycheck, and the engine writes each once.
            wanted = _make_txn(seed_user, periods[5], template=template)
            received = _make_txn(
                seed_user, periods[6], template=template,
                status_name="Received",
            )
            overridden = _make_txn(
                seed_user, periods[7], template=template, owned_amount="1.00",
            )
            non_salary = _make_txn(
                seed_user, periods[5], template=other_template,
            )
            expense = _make_txn(
                seed_user, periods[5], template=None, type_name="Expense",
                owned_amount="1.00",
            )
            db.session.commit()

            priced = _net_map(
                user_id, scenario_id,
                [wanted, received, overridden, non_salary, expense],
            )

            assert set(priced) == {wanted.id, received.id, overridden.id}, (
                "every row on the salary template is PRICED by the profile "
                "whatever its status or override flag, and no other row is; "
                f"got ids {sorted(priced)}"
            )
            assert priced[received.id] == priced[wanted.id], (
                "a Received paycheck is worth what the profile paid for its "
                "period; refusing it here would be the repair's status gate "
                "re-added inside a pricing rule (finding N-262)"
            )

    def test_empty_when_no_salary_definition_prices_the_row(
        self, app, db, seed_user, seed_periods,
    ):
        """A row no salary profile's template names has no answer here."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            # Empty transaction list.
            assert _net_map(user_id, scenario_id, []) == {}

            # An income row whose template has no SalaryProfile -> omitted.
            income_type = (
                db.session.query(TransactionType).filter_by(name="Income").one()
            )
            category = next(iter(seed_user["categories"].values()))
            unlinked = TransactionTemplate(
                user_id=user_id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=income_type.id,
                name="Unlinked income",
            )
            db.session.add(unlinked)
            db.session.flush()
            make_every_period_rule(db.session, unlinked)
            db.session.commit()
            txn = _make_txn(
                seed_user, all_periods(user_id)[3],
                template=unlinked,
            )
            db.session.commit()
            assert _net_map(user_id, scenario_id, [txn]) == {}



def _derived(user_id):
    """The owner's saved schedule AS THE PAYCHECK ENGINE takes it.

    That engine moved onto :class:`~app.services.pay_calendar.DerivedPeriod`
    at pay-calendar plan step C2-f2d-3, and ``income_service`` derives this
    same window internally -- so the oracles below are handed the shape the
    producer under test uses, while the ORM rows beside them stay for the
    fixtures that WRITE a ``pay_period_id``.
    """
    return calendar_for(user_id).saved()


class TestLiveIncomeThroughBalanceResolver:
    """Balance surfaces price a projected salary row from its PROFILE.

    The drift-without-regeneration lock -- the exact failure mode (a profile,
    calibration or code change staling the grid) that motivated the income
    resolver.  **Plan step X-au-d changed what makes it hold**: the row no
    longer stores a figure a repair has to supersede, it DECLARES the
    definition that prices it, so there is one answer rather than a preferred
    one.  The class stays because the surfaces still have to reach that answer.
    """

    def test_a_declared_salary_row_reaches_the_grid_and_the_BALANCE(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """A declared salary income row contributes its profile's net to both.

        $104,000 profile, no deductions, no tax configs seeded -> net = gross =
        104000/26 = $4,000.00.  The row stores NOTHING, so a surface that read
        the column would contribute ``None`` rather than a stale figure -- the
        substitution this cutover makes unrepresentable instead of unlikely.

        The income row was read through ``cash_ledger.period_subtotal`` until
        plan step X-c2b3 deleted it; it is now the shipped
        ``GridColumn.income``, which is what the grid footer renders.  The
        $4,000.00 is unchanged across both that step and this one, because the
        rule is the same on both bases -- which is the property this test
        exists to pin.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = seed_user["scenario"]
            bctx = BalanceContext.build(seed_user["user"].id)
            account = seed_user["account"]
            profile = _create_profile(user_id, scenario.id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()

            periods = all_periods(user_id)
            period = periods[5]
            row = _make_txn(seed_user, period, template=template)
            db.session.commit()
            assert row.estimated_amount is None

            tax_configs = tax_configs_for(
                profile, period.start_date.year,
            )
            breakdowns = paycheck_calculator.project_salary(
                payroll_basis(profile, _derived(user_id)), _derived(user_id),
                tax_configs,
            )
            expected_net = {
                bd.period.period_id: bd.earnings.net_pay for bd in breakdowns
            }[period.id]
            # Sanity: the profile's answer is the hand-computed figure, and
            # it is not something the row could have been read off.
            assert expected_net == Decimal("4000.00")

            # The grid's income row reflects the live net.
            column = balance_at.grid_balance_view(
                CashFlowSet.single(account), bctx,
            ).columns[period.id]
            assert column.income == expected_net, (
                f"GridColumn.income should be the profile's {expected_net}, "
                f"got {column.income} (the row stores no figure at all)"
            )

            # The BALANCE moves by that net too, not just the rendered income
            # row -- the property that makes the derivation a basis rather than
            # a display value.  Re-pointed off the deleted anchor-forward walk
            # onto the cash view at plan step X-g4b; the delta is unchanged
            # because both fold one amount model.
            result = balance_at.cash_balance_map(
                account, bctx,
            )
            idx = next(i for i, p in enumerate(periods) if p.id == period.id)
            prior = result[periods[idx - 1].id]
            assert result[period.id] - prior == expected_net, (
                "the income period's balance delta should be the live net "
                f"{expected_net}, got {result[period.id] - prior}"
            )

    def test_overridden_income_row_keeps_user_value(
        self, app, db, seed_user, seed_periods,
    ):
        """A salary income row that OWNS its figure is not recomputed.

        The non-vacuity partner for the case above, and the one whose REASON
        changed at plan step X-au-d.  It used to hold because a read-time
        repair excluded ``is_override`` rows; it holds now because the row
        DECLARES no definition -- ``amount_ownership`` is what decides, so the
        figure a human typed is answered by amount rule 1 and no salary
        producer is consulted at all (finding **N-262**).  The flag is set here
        because that is what the edit doors do beside taking ownership, not
        because pricing reads it.  Read through ``GridColumn.income`` since
        plan step X-c2b3 deleted ``cash_ledger.period_subtotal``.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = seed_user["scenario"]
            bctx = BalanceContext.build(seed_user["user"].id)
            account = seed_user["account"]
            profile = _create_profile(user_id, scenario.id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()

            period = all_periods(user_id)[5]
            _make_txn(
                seed_user, period, template=template, owned_amount="1234.56",
            )
            db.session.commit()

            column = balance_at.grid_balance_view(
                CashFlowSet.single(account), bctx,
            ).columns[period.id]
            assert column.income == Decimal("1234.56"), (
                "a row that OWNS its figure must keep the user's amount, "
                f"got {column.income}"
            )


class TestThePerPeriodGrossIsTheENGINES:
    """What replaced ``income_service.get_current_gross_biweekly``.

    That helper was the canonical raise-aware gross producer (C17 / F-20 /
    MED-06 / F-032) and plan step **salary:R14-b** DELETED it, because its
    shape was the defect: one figure, read at ONE moment, standing in for a
    series every raise moves.  Its consumers read
    :class:`~app.services.investment_projection.AccountPayrollFeed` now, priced
    per payday by :func:`app.services.projection_inputs.load_payroll_feeds`.

    **The property C17 was written to protect is graded here, and it is
    STRONGER than it was.**  The old cases asked one question at one clock and
    got one answer; these read the same feed at two paydays and see the raise
    land between them, which is what the helper structurally could not show.
    Two of the six old cases have no successor and say so below.
    """

    @staticmethod
    def _feed_for(user_id, profile, account_id):
        """Price *account_id*'s feed off *profile* as the funding job."""
        from app.models.investment_params import InvestmentParams
        params = db.session.query(InvestmentParams).filter_by(
            account_id=account_id,
        ).one()
        params.salary_profile_id = profile.id
        db.session.flush()
        return load_payroll_feeds(
            pricing_over(calendar_for(user_id)), [account_id],
            {account_id: params},
        )[account_id]

    def test_the_raise_lands_ON_ITS_DAY_and_not_before(
        self, app, db, seed_user, seed_periods,
    ):
        """C17-1 and the effective-month case, in ONE feed.

        Hand arithmetic: ``104000 / 26 = 4000.00`` before the March 2026
        raise and ``104000 * 1.03 / 26 = 4120.00`` from it, which the
        paycheck of 03-27 pays.  The paycheck of 03-13 pays 02-22..03-07,
        five weekdays each side of Sunday 03-01 (ruling plan#3089): 2000.00
        + 2060.00 = 4060.00.  This case was named for the raise landing on
        its payday, and read 03-13 at 4120.00, until task plan#3100.
        Pre-Commit-17
        the off-engine sites returned $4,000.00 forever because the raise was
        silently dropped; the helper C17 replaced them with fixed that at ONE
        clock, and the feed fixes it at every payday -- the January and the
        March paycheck are both in this one value, which is the whole of
        finding **D45**'s remedy.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, seed_user["scenario"].id)
            _add_one_time_raise(profile)
            account = make_investment_account(
                seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            )
            db.session.commit()

            feed = self._feed_for(user_id, profile, account.id)
            calendar = calendar_for(user_id)
            before = calendar.period_containing(_AS_OF_BEFORE_RAISE)
            after = calendar.period_containing(_AS_OF_AFTER_RAISE)
            straddle = calendar.period_containing(date(2026, 3, 15))

            assert feed.gross_at(before) == _NO_RAISE_GROSS
            assert feed.gross_at(after) == _RAISE_APPLIED_GROSS
            assert straddle.start_date == date(2026, 3, 13)
            assert feed.gross_at(straddle) == Decimal("4060.00")

    def test_no_raise_yields_the_byte_identical_pre_fix_value(
        self, app, db, seed_user, seed_periods,
    ):
        """C17-2: no raises -> the engine value equals the pre-fix value.

        With zero raises the post-raise annual salary equals the base, so the
        engine's ``104000 / 26`` matches the pre-fix ``104000 / 26 = 4000.00``
        exactly.  Locks the "no regression for non-raised users" property that
        every C17 site was measured against.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, seed_user["scenario"].id)
            account = make_investment_account(
                seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            )
            db.session.commit()

            feed = self._feed_for(user_id, profile, account.id)
            calendar = calendar_for(user_id)
            after = calendar.period_containing(_AS_OF_AFTER_RAISE)
            assert feed.gross_at(after) == _NO_RAISE_GROSS

    def test_no_funding_job_REFUSES_rather_than_answering_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """C17-3 INVERTED, and the inversion is the ruling.

        The deleted helper answered ``Decimal("0")`` for an owner with no
        active profile -- and, worse, for one whose calendar simply did not
        cover the clock, which silently deleted a whole contribution plan at
        onboarding and after a horizon lapse.  That zero is why
        ``recurrence:R-F16`` had to REVERT a fix.

        The feed refuses instead: no funding job means no gross, so no
        employer money is modelled and the surface says which (developer,
        2026-09-04).  ``None`` rather than ``$0.00`` because a zero is a basis
        a percentage can be taken of.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = make_investment_account(
                seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            )
            db.session.commit()

            from app.models.investment_params import InvestmentParams
            params = db.session.query(InvestmentParams).filter_by(
                account_id=account.id,
            ).one()
            feed = load_payroll_feeds(
                pricing_over(calendar_for(user_id)),
                [account.id],
                {account.id: params},
            )[account.id]

            assert feed.funds_employer is False
            assert feed.gross_at(calendar_for(user_id).saved()[0]) is None

    # **``test_scenario_id_filter_scopes_lookup`` has no successor, and that
    # is the point rather than a gap.**  It pinned the deleted helper's
    # ``scenario_id`` keyword, which existed because the helper SEARCHED for a
    # profile -- an unordered ``.first()`` across the owner's active ones,
    # optionally narrowed to a scenario.  Nothing searches now: the employee
    # half is priced inside the deduction's OWN profile and the employer half
    # off the profile ``budget.investment_params.salary_profile_id`` NAMES
    # (**R-SAL5**).  A filter that narrows a search cannot be tested when
    # there is no search, and the 39% swing that filter was mitigating is not
    # a state the model can reach.  ``TestLoadPayrollFeeds
    # .test_ANOTHER_OWNERS_profile_prices_nothing`` grades what DID survive:
    # that the read is owner-scoped.


class TestConsumerIntegration:
    """C17-4: every downstream consumer reads the same engine value."""

    def test_the_seam_and_the_engine_agree_on_a_raised_paycheck(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C17-4: the seam's feed IS the engine's own breakdown.

        The original case called four consumers' private helpers and asserted
        they agreed on one scalar.  Three of the four read that scalar through
        ``income_service.get_current_gross_biweekly``, which plan step
        **salary:R14-b** deleted, so what is left to grade is the one
        agreement that can still fail: the gross the balance seam hands its
        contribution tier is the gross the paycheck engine put on that
        paycheck -- read back through
        :class:`income_service.ProfilePaychecks`, the ONE spelling of a
        profile's projection, rather than re-derived here.

        Hand arithmetic: ``4000.00 * 1.03 = 4120.00`` (the pay entry,
        $104,000 / 26, raised once).  The raise is effective February 2026:
        the pay entry is recorded on the window's first payday, 2026-01-19
        (today pinned to 2026-03-20), and holds every raise dated by the day
        its paycheck opens, 12-31 (ruling plan#3091), so a raise dated before
        then would be inside it; February lands after it, and before the
        days the current paycheck pays for.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, seed_user["scenario"].id)
            _add_one_time_raise(
                profile, effective_month=2, effective_year=2026,
            )
            inv = make_investment_account(
                seed_user, db.session, seed_periods_today[0],
                Decimal("10000.00"),
            )
            db.session.commit()

            from app.models.investment_params import InvestmentParams
            params = db.session.query(InvestmentParams).filter_by(
                account_id=inv.id,
            ).one()
            params.salary_profile_id = profile.id
            db.session.commit()

            bctx = BalanceContext.build(user_id)
            seam_feed = balance_at._contribution_inputs_for_account(
                inv, bctx,
            ).feed
            calendar = calendar_for(user_id)
            # Keyed on the BREAKDOWN's OWN payday rather than zipped
            # against the calendar: the loader under test keys the same way,
            # and an equality whose two sides share one SPELLING can agree
            # while both are wrong.  The hand figure below is what keeps this
            # honest even so.
            engine = {
                breakdown.period.payday: breakdown.earnings.gross_biweekly
                for breakdown in pricing_over(
                    calendar,
                ).for_profile(profile).over(calendar.saved())
            }
            current = calendar.period_containing(bctx.as_of)

            assert engine[current.start_date] == _RAISE_APPLIED_GROSS
            assert seam_feed.gross_at(current) == engine[current.start_date]

            # The scoping control: a non-investment account in the same user's
            # set gets NO feed, so the assertion above pins the
            # investment-only pricing rather than a value every account
            # carries.
            checking_inputs = balance_at._contribution_inputs_for_account(
                seed_user["account"], BalanceContext.build(user_id),
            )
            assert checking_inputs.investment_params is None
            assert checking_inputs.feed.funds_employer is False


class TestLiveProjectedNetUsesPerYearTaxConfigs:
    """DH-#30: the salary derivation resolves tax configs PER period year.

    The recurrence engine GENERATES the stored grid amount using each
    period's OWN tax year; the live recompute must resolve the same way or
    the stored cache and the live value silently disagree -- the exact
    reconciliation contract amount rule 2 advertises.  Pre-fix it
    loaded a single current-year config set and applied it across the whole
    ~2-year horizon, so a future-year period was recomputed against the
    wrong year's tax.
    """

    def test_future_year_txn_uses_future_year_state_rate(
        self, app, db, monkeypatch, seed_user, seed_periods_52, tax_law,
    ):
        """A 2027 salary income row recomputes against 2027's state rate.

        Installs NC flat state tax at different rates for 2026 (3.99%) and
        2027 (6.00%), then asserts a 2027 period's live net equals the
        2027-rate projection and differs from the 2026-rate one.  ``today``
        is frozen to 2026 so the pre-fix single current-year load would
        have used the 2026 rate -- the revert-proof property (without the
        freeze, a suite run in 2027 would make the old code pick 2027 by
        coincidence).
        """
        freeze_today(monkeypatch, date(2026, 6, 1))
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)  # $104k, NC
            template = _make_salary_template(seed_user, profile)

            # A NC state rate per year and nothing else: a law year cannot
            # lack federal rules or FICA, so each carries the zero ones.
            tax_law(TaxLaw(years=tuple(
                made_up_year(year, states={
                    "NC": made_up_state(rate, standard_deduction=Decimal("0.00")),
                })
                for year, rate in ((2026, Decimal("0.0399")), (2027, Decimal("0.0600")))
            )))
            db.session.commit()

            periods = all_periods(user_id)
            period_2027 = next(
                (p for p in periods if p.start_date.year == 2027), None,
            )
            assert period_2027 is not None, "seed_periods_52 must reach 2027"
            txn = _make_txn(seed_user, period_2027, template=template)
            db.session.commit()

            # Engine-faithful expectations that isolate WHICH year's rate
            # was applied (the resolution under test, not the paycheck
            # math): project the same periods with one year's config set at
            # a time and read the 2027 period's net from each.
            net_2027_rate = {
                bd.period.period_id: bd.earnings.net_pay
                for bd in paycheck_calculator.project_salary(
                    payroll_basis(profile, _derived(user_id)),
                    _derived(user_id),
                    tax_configs_for(profile, 2027),
                )
            }[period_2027.id]
            net_2026_rate = {
                bd.period.period_id: bd.earnings.net_pay
                for bd in paycheck_calculator.project_salary(
                    payroll_basis(profile, _derived(user_id)),
                    _derived(user_id),
                    tax_configs_for(profile, 2026),
                )
            }[period_2027.id]
            # The two state rates genuinely diverge, so the test cannot
            # pass vacuously.
            assert net_2027_rate != net_2026_rate

            overrides = _net_map(user_id, scenario_id, [txn])
            assert overrides[txn.id] == net_2027_rate
            assert overrides[txn.id] != net_2026_rate


class TestTheProjectionDoesNotMoveWhenTheCalendarYearTURNS:
    """The New Year cliff: a projection may not change because a date passed.

    The law carries only the years that have been published, so on a
    January 1 the CURRENT year can be one it lacks.  The retired
    resolution rule substituted "the current calendar year", which cannot
    answer for the year it is itself: a request for the now-current year found
    nothing to redirect to and resolved to no configuration at all.  The
    paycheck engine reads a missing ``fica_config`` as ZERO Social Security
    (``tax_calculator.capped_social_security`` documents that arm for
    bootstrap), so the whole SS line silently vanished from every projected
    paycheck in that year and later.

    Measured on a clone of production 2026-08-11, before the fix: on
    2027-01-01, with no write and no user action, 40 of 51 live-priced salary
    rows changed and projected income over the horizon rose by **$8,460.50**;
    counting the 11 periods the grid's own rolling top-up creates that same
    day, **$10,914.93** over 51 of 62 rows.  One period went from
    ``NET 2,639.30`` (ss 205.19) to ``NET 2,844.49`` (ss 0.00).

    Neither figure was only a display defect.  A settle writes the live amount
    into ``estimated_amount`` before the status flip
    (``transaction_service._reconcile_cached_amount``), after which the row
    leaves this producer's Projected-only candidate set and nothing can repair
    it; and any salary or tax-config save regenerates every row from today
    forward.  The read-time gap had two write-back doors.
    """

    @staticmethod
    def _law_2026_only():
        """Return a made-up law: NC state tax and FICA for 2026, and no other year.

        No federal bracket set, as the rows it replaced held none: the year
        carries the zero federal rules a law year cannot lack.
        """
        return TaxLaw(years=(
            made_up_year(
                2026,
                fica=FicaRules(
                    ss_rate=Decimal("0.0620"),
                    ss_wage_base=Decimal("184500.00"),
                    medicare_rate=Decimal("0.0145"),
                    medicare_surtax_rate=Decimal("0.0090"),
                    medicare_surtax_threshold=Decimal("200000.00"),
                ),
                states={"NC": made_up_state(
                    Decimal("0.0399"), standard_deduction=Decimal("0.00"),
                )},
            ),
        ))

    def test_a_2027_paycheck_is_priced_the_same_in_2026_and_in_2027(
        self, app, db, monkeypatch, seed_user, seed_periods_52, tax_law,
    ):
        """The same row, the same inputs, two different "todays", one answer.

        The property the resolution rule exists for.  Nothing about the row or
        its inputs changes between the two reads -- only the wall clock -- so
        any difference is the app rewriting a projection because a date passed.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)  # $104k, NC
            template = _make_salary_template(seed_user, profile)
            tax_law(self._law_2026_only())

            periods = all_periods(user_id)
            period_2027 = next(
                (p for p in periods if p.start_date.year == 2027), None,
            )
            assert period_2027 is not None, "seed_periods_52 must reach 2027"
            txn = _make_txn(seed_user, period_2027, template=template)
            db.session.commit()

            freeze_today(monkeypatch, date(2026, 6, 1))
            priced_in_2026 = _net_map(user_id, scenario_id, [txn])[txn.id]

            freeze_today(monkeypatch, date(2027, 6, 1))
            priced_in_2027 = _net_map(user_id, scenario_id, [txn])[txn.id]

            assert priced_in_2027 == priced_in_2026

    def test_every_withholding_line_is_what_an_unresolved_year_deletes(
        self, app, db, seed_user, seed_periods_52, tax_law,
    ):
        """Non-vacuity: an unresolved 2027 really does zero the withholding.

        Without this the sibling above could pass with both reads equally
        wrong.  It prices the same 2027 period against the three ``None``s the
        retired rule produced on 2027-01-01 (the exact-year loader that also
        returned them was deleted at plan step salary:X-at-1) and shows every
        withholding line collapsing to zero, which raises the net by their sum.

        On production only the Social Security line moved, because that
        profile then carried an ACTIVE calibration and the calibrated path
        (deleted at plan step salary:S11-c-2c) took federal and state from
        stored effective rates; only SS read ``fica_config``.  This fixture
        holds no pay stub, so the formulas alone price it and all three lines
        move.  Both are the same defect -- a config set that resolved to
        nothing -- seen through different tax paths.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)  # $104k, NC
            _make_salary_template(seed_user, profile)
            tax_law(self._law_2026_only())

            periods = all_periods(user_id)
            period_2027 = next(
                p for p in periods if p.start_date.year == 2027
            )

            derived = _derived(user_id)
            derived_2027 = next(
                p for p in derived if p.start_date.year == 2027
            )
            basis = payroll_basis(profile, derived)
            resolved = paycheck_calculator.calculate_paycheck(
                basis, derived_2027,
                tax_configs_for(profile, 2027),
            )
            # The three ``None``s the retired rule produced on 2027-01-01.
            unresolved = paycheck_calculator.calculate_paycheck(
                basis, derived_2027,
                {"bracket_set": None, "state_config": None, "fica_config": None},
            )

            # $104,000 / 26 = $4,000.00 gross, no pre-tax deductions, so each
            # line is a flat rate on the full gross:
            #   state    4000.00 * 0.0399 = 159.60
            #   SS       4000.00 * 0.0620 = 248.00  (under the $184,500 base)
            #   medicare 4000.00 * 0.0145 =  58.00
            #   federal                    =   0.00  (zero federal rules)
            # net = 4000.00 - 159.60 - 248.00 - 58.00 = 3,534.40
            assert resolved.earnings.gross_biweekly == Decimal("4000.00")
            assert resolved.taxes.state == Decimal("159.60")
            assert resolved.taxes.social_security == Decimal("248.00")
            assert resolved.taxes.medicare == Decimal("58.00")
            assert resolved.earnings.net_pay == Decimal("3534.40")

            assert unresolved.taxes.state == Decimal("0.00")
            assert unresolved.taxes.social_security == Decimal("0.00")
            assert unresolved.taxes.medicare == Decimal("0.00")
            assert unresolved.earnings.net_pay == Decimal("4000.00")

            # The whole withholding, handed back to the projection as income.
            assert (
                unresolved.earnings.net_pay - resolved.earnings.net_pay
                == Decimal("465.60")
            )


class TestThePricerAnswersPastTheSavedHORIZON:
    """A projected payday is priced by the same code and the same rules.

    **The capability plan step salary:S3-d exists for, and nothing else in the
    tree exercises it.**  Every production caller of
    :meth:`~app.services.income_service.ProfilePaychecks.over` hands it
    ``calendar.saved()``, so without this class the whole argument for the
    step -- that the engine's four calendar judgements come off
    ``basis.calendar``, which runs forward at the owner's cadence past the
    schedule's horizon -- would be asserted in a docstring and executed zero
    times.  Plan step **salary:S3-e** deletes
    :class:`~app.services.investment_projection.AccountPayrollFeed`'s hold on
    the strength of it, so it is graded here first.

    An adversarial review of S3-d found the gap; these are its cases.
    """

    @staticmethod
    def _projected(calendar, days_past_horizon):
        """Return the first projected payday at least *days_past_horizon* out.

        Taken off :meth:`~app.services.pay_calendar.PayCalendar.axis`, the
        producer that projects at the recorded cadence, rather than computed
        here -- an oracle that stepped the cadence itself would be a second
        spelling of the rule under test.
        """
        horizon = calendar.horizon()
        wanted = horizon + timedelta(days=days_past_horizon)
        beyond = calendar.axis(
            calendar.opening_bound(), wanted + timedelta(days=400),
        )
        return next(p for p in beyond if p.start_date > wanted)

    def test_a_projected_payday_prices_and_says_it_has_no_ROW(
        self, app, db, seed_user, seed_periods,
    ):
        """It answers a paycheck, and its ``period_id`` is ``None``.

        Both halves matter.  The figure proves the engine ran rather than
        refusing; the ``None`` proves the paycheck still says it is not a row
        a ``transactions.pay_period_id`` can point at -- which is the whole
        reason :attr:`~app.services.paycheck_calculator.PeriodInfo.period_id`
        was widened rather than dropped.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, seed_user["scenario"].id)
            db.session.commit()

            calendar = calendar_for(user_id)
            projected = self._projected(calendar, 0)
            breakdown = pricing_over(
                calendar,
            ).for_profile(profile).at(projected)

            assert breakdown.period.period_id is None
            assert breakdown.period.payday == projected.start_date
            # $104,000 over 26 paychecks, no raise and no deduction, so the
            # gross is the same rate every payday in every year.
            assert breakdown.earnings.gross_biweekly == Decimal("4000.00")

    def test_the_wage_base_CUMULATIVE_runs_past_the_horizon(
        self, app, db, seed_user, seed_periods,
    ):
        """A projected payday's year-to-date is counted, not reset to zero.

        **This is the case that could not fail on a docstring.**  The FICA
        Social Security cumulative is the judgement most visibly wrong if the
        rhythm stopped at the schedule's horizon: a projected payday would see
        an empty year, so the wage base would never be reached and Social
        Security would be charged on wages above it.

        Graded through the engine's own producer rather than through a second
        spelling: the paydays the cumulative walks are
        :func:`~app.services.pay_calendar.paydays_in_year_before`'s answer, so
        the assertion is that a projected payday's count matches what that
        producer reports for it and is not zero.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, seed_user["scenario"].id)
            db.session.commit()

            calendar = calendar_for(user_id)
            horizon = calendar.horizon()
            # A payday in a calendar year the SAVED schedule never reaches, so
            # every payday of its year is itself a projection.  The first
            # projected payday would not do: its year is still full of saved
            # ones, and a rhythm that stopped at the horizon would answer the
            # same non-empty list.  A first draft of this case asserted on it
            # and could not fail.
            deep = self._projected(calendar, 400)
            assert deep.start_date.year > horizon.year, (
                f"payday {deep.start_date} is in the horizon's own year "
                f"({horizon}); this case needs one past it"
            )
            earlier = paydays_in_year_before(calendar, deep.start_date)

            assert earlier, (
                "the rhythm reported NO payday before a projected one in its "
                "own calendar year -- a cumulative reset to zero past the "
                "horizon, which is what charges Social Security above the "
                "wage base for the whole tail"
            )
            assert earlier[-1] < deep.start_date
            assert min(earlier) > horizon, (
                "every payday of this year is past the saved schedule, so "
                "the cumulative walked here is entirely projected"
            )
            # And the paycheck itself prices, on that same forward rhythm.
            assert pricing_over(calendar).for_profile(
                profile,
            ).at(deep).earnings.gross_biweekly == Decimal("4000.00")

    def test_a_projected_payday_and_a_saved_one_agree_on_the_RATE(
        self, app, db, seed_user, seed_periods,
    ):
        """Same salary, same cadence, no raise between -> the same gross.

        The invariance control for the widening: nothing about crossing the
        schedule's horizon may change what the job pays, because the gross is
        a function of the salary and the cadence alone (ruling
        **balance:R-HW**).  A projected payday reading a different rate would
        mean the pricer had lost the profile or the cadence past the horizon.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, seed_user["scenario"].id)
            db.session.commit()

            calendar = calendar_for(user_id)
            paychecks = pricing_over(calendar)
            saved = paychecks.for_profile(profile).at(calendar.saved()[-1])
            projected = paychecks.for_profile(profile).at(
                self._projected(calendar, 0),
            )

            assert projected.earnings.gross_biweekly == (
                saved.earnings.gross_biweekly
            )


class TestAPaydayIsPricedONCEPerPricer:
    """The per-payday memo is load bearing, so it is measured rather than read.

    **Plan step salary:S3-d made this hotter, not cooler.**
    :meth:`~app.services.income_service.SalaryPricing.net_for` prices the one
    period a ROW names, so it is called once per salary row where the producer
    it replaced ran once per profile.  If
    :meth:`~app.services.income_service.ProfilePaychecks.over`'s memo silently
    stopped hitting, every other test in this suite would still pass and every
    salary row on the grid would re-run the engine -- restoring exactly the
    cost this step removes, invisibly.

    ``TestOnePaycheckProjectionPerProfilePerRender`` cannot see it: that gate
    counts PRICERS built per render, and one pricer with a broken memo builds
    exactly one.  An adversarial review of S3-d found the hole.
    """

    def test_asking_twice_prices_once(self, app, db, seed_user, seed_periods):
        """A second ask over the same window runs the engine zero more times."""
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, seed_user["scenario"].id)
            db.session.commit()

            calendar = calendar_for(user_id)
            saved = list(calendar.saved())
            paychecks = pricing_over(calendar)

            with counting_calls(
                ("app.services.paycheck_calculator", "calculate_paycheck"),
            ) as counts:
                paychecks.for_profile(profile).over(saved)
                first = counts["calculate_paycheck"]
                paychecks.for_profile(profile).over(saved)
                paychecks.for_profile(profile).over(saved[:3])
                paychecks.for_profile(profile).at(saved[0])

            assert first == len(saved), (
                f"the first ask priced {first} paychecks over a "
                f"{len(saved)}-payday window; it must price each exactly once"
            )
            assert counts["calculate_paycheck"] == first, (
                f"re-asking priced {counts['calculate_paycheck'] - first} "
                "more paychecks; every payday after the first ask is a memo "
                "hit and the engine must not run again"
            )

    def test_an_overlapping_ask_prices_only_the_DIFFERENCE(
        self, app, db, seed_user, seed_periods,
    ):
        """Two overlapping spans cost the union, never the overlap twice."""
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, seed_user["scenario"].id)
            db.session.commit()

            calendar = calendar_for(user_id)
            saved = list(calendar.saved())
            assert len(saved) >= 4, "fixture too short to overlap two spans"
            paychecks = pricing_over(calendar)

            with counting_calls(
                ("app.services.paycheck_calculator", "calculate_paycheck"),
            ) as counts:
                paychecks.for_profile(profile).over(saved[:3])
                paychecks.for_profile(profile).over(saved[1:4])

            assert counts["calculate_paycheck"] == 4, (
                f"two spans covering 4 distinct paydays priced "
                f"{counts['calculate_paycheck']} paychecks; the 2 they share "
                "must be priced once"
            )

    def test_a_payday_named_twice_in_ONE_ask_prices_once(
        self, app, db, seed_user, seed_periods,
    ):
        """A duplicate inside one call misses the memo and must still price once.

        The memo makes a REPEAT ask free; two periods naming one payday inside
        a SINGLE ask would both miss it, so the dedupe is a separate rule and
        this is its case.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, seed_user["scenario"].id)
            db.session.commit()

            calendar = calendar_for(user_id)
            first = calendar.saved()[0]
            paychecks = pricing_over(calendar)

            with counting_calls(
                ("app.services.paycheck_calculator", "calculate_paycheck"),
            ) as counts:
                answers = paychecks.for_profile(profile).over(
                    [first, first, first],
                )

            assert counts["calculate_paycheck"] == 1, (
                f"one payday named three times priced "
                f"{counts['calculate_paycheck']} paychecks"
            )
            assert len(answers) == 3
            assert answers[0] is answers[1] is answers[2]


class TestThePricerREFUSESAMismatchedOwner:
    """A profile and a calendar from two owners is refused, not answered.

    **The mispairing is SILENT without the refusal**, which is why it is a
    case: the profile would be priced as one owner's (its owner's tax rows
    loaded, until plan step salary:X-at-1 moved the law into the code) while
    every payday came from the other's schedule, and the engine's own cross-owner guard
    (``paycheck_calculator._month_ordinal``) cannot fire, because it refuses a
    payday its calendar cannot PLACE and a calendar places its own paydays
    perfectly well.  An adversarial review of plan step salary:S3-d found the
    unguarded pairing.
    """

    def test_a_foreign_calendar_is_refused(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Pricing one owner's profile against another's calendar raises."""
        with app.app_context():
            profile = _create_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()
            foreign = calendar_for(seed_second_user["user"].id)

            with pytest.raises(ValueError, match="belongs to user"):
                pricing_over(foreign).for_profile(profile)

    def test_a_source_answering_another_owners_calendar_is_refused(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """A pricer pinned to owner A whose source yields B's calendar raises.

        The owner is pinned BESIDE the calendar source since plan step
        salary:C12, so the amount model can scope a profile lookup without
        deriving a calendar; the pair is checked at the one moment both
        halves exist, the first derivation.  Refused rather than answered,
        because a pricer that priced A's profiles against B's paydays would
        answer plausible figures off the wrong schedule -- the same silence
        the case above refuses one tier down.
        """
        with app.app_context():
            own = seed_user["user"].id
            foreign = calendar_for(seed_second_user["user"].id)
            pricer = income_service.paycheck_pricing(
                own, lambda: foreign,
            )
            assert pricer.user_id == own
            with pytest.raises(ValueError, match="belonging to user"):
                _ = pricer.calendar


# ── salary:C12: the amount model reads the pass's pricer ────────────


class TestTheAmountModelReadsThePassPricer:
    """``SalaryPricing`` takes a pricer and reads the owner off it (ledger P63).

    **Plan step salary:C12.**  The amount basis was built from an owner and a
    scenario and its salary derivation derived a pricer of its own over a
    calendar of its own; it is built OVER a pricer now.  Three properties are
    graded here, one per case: the pass's basis prices a row through the
    pass's own memo, so the same payday is priced ONCE across the two paths;
    the owner is the pricer's, so a scenario handed a pricer of another
    owner prices nothing rather than that owner's rows; and the DERIVED
    interim a pass-less producer builds derives NOTHING until a paycheck is
    asked for -- which is what keeps a settle of a non-salary row free and
    an owner with no schedule row un-refused, the two properties the old
    ``SalaryPricing._pricing`` had and this step keeps.
    """

    def test_the_basis_prices_a_row_through_the_pass_pricer(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """A payday the pass's pricer already priced is NOT priced again.

        The pass prices the profile's paycheck for one period (as a payroll
        feed would), then the basis built over that pricer answers a salary
        row in the SAME period: zero further engine runs, because both read
        one memo.  On the tree before C12 the basis's own pricer ran the
        engine once more here.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()
            period = all_periods(user_id)[5]
            txn = _make_txn(seed_user, period, template=template)
            db.session.commit()

            ctx = BalanceContext.build(user_id)
            derived = next(
                p for p in ctx.calendar().saved() if p.period_id == period.id
            )
            priced_by_pass = ctx.paychecks().for_profile(profile).at(derived)

            with counting_calls(
                ("app.services.paycheck_calculator", "calculate_paycheck"),
            ) as counts:
                net = income_service.salary_net_for(txn, ctx.amounts().salary)

            assert net == priced_by_pass.earnings.net_pay == Decimal("4000.00")
            assert counts["calculate_paycheck"] == 0, (
                "the basis priced a payday the pass's pricer had already "
                "priced; it is reading a second pricer"
            )

    def test_the_owner_is_the_pricers(
        self, app, db, seed_user, seed_second_user, seed_periods, tax_law,
    ):
        """A basis over another owner's pricer prices THIS owner's rows never.

        The owner used to arrive as a second argument beside the scenario,
        with nothing checking the two agreed; it is read off the pricer now,
        so the only way to price owner A's rows is to hold owner A's pricer.
        Handed owner B's, the profile lookup is scoped to B and finds no
        profile naming A's template, so the row is refused (``None``) rather
        than priced off B's calendar.  **B's calendar is asserted NOT
        derived**, because the refusal alone cannot tell the two apart: a
        lookup that ignored the owner would find A's profile (the scenario is
        A's), derive B's calendar, fail to place A's period in it and answer
        ``None`` too -- the same answer by the wrong door, one calendar
        derivation later.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()
            txn = _make_txn(
                seed_user, all_periods(user_id)[5], template=template,
            )
            db.session.commit()

            own = income_service.salary_pricing(
                scenario_id, income_service.derived_paycheck_pricing(user_id),
            )
            foreign = income_service.salary_pricing(
                scenario_id,
                income_service.derived_paycheck_pricing(
                    seed_second_user["user"].id,
                ),
            )
            assert own.net_for(template.id, txn.pay_period_id) == Decimal("4000.00")
            with counting_calls(
                ("app.services.pay_calendar._derive", "derive_periods"),
            ) as counts:
                assert foreign.net_for(template.id, txn.pay_period_id) is None
            assert counts["derive_periods"] == 0, (
                "the foreign pricer's calendar was derived: the profile "
                "lookup is not scoped to the pricer's owner"
            )

    def test_the_pass_pricer_and_its_basis_derive_nothing_until_a_paycheck_is_asked(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """Building ``ctx.paychecks()`` and ``ctx.amounts()`` derives NO calendar.

        The pass hands its calendar MEMO as the pricer's source, so a pass
        that folds no cash and prices no paycheck -- the loan arm, the
        bank-agreement fragment, both of which reach ``amounts()`` and read no
        calendar anywhere else -- pays for none and cannot be refused one.  An
        adversarial review of this step measured the first build deriving it
        at ``amounts()``, which put a ``PayCalendarError`` under two ``Raises``
        contracts that promise none; this is the case that keeps it out.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()
            txn = _make_txn(
                seed_user, all_periods(user_id)[5], template=template,
            )
            db.session.commit()

            with counting_calls(
                ("app.services.pay_calendar._derive", "derive_periods"),
            ) as counts:
                ctx = BalanceContext.build(user_id)
                paychecks = ctx.paychecks()
                basis = ctx.amounts()
                assert paychecks.user_id == user_id
                assert counts["derive_periods"] == 0, (
                    "building the pass's pricer or its basis derived the "
                    "calendar before any paycheck was asked for"
                )
                assert income_service.salary_net_for(txn, basis.salary) == (
                    Decimal("4000.00")
                )
                assert counts["derive_periods"] == 1
                assert paychecks.calendar is ctx.calendar(), (
                    "the pricer's calendar is not the pass's memo"
                )
                assert counts["derive_periods"] == 1

    def test_the_derived_pricer_derives_nothing_until_a_paycheck_is_asked(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """A pass-less basis over a non-salary row derives NO calendar.

        The interim's whole reason: the twelve pass-less producers price
        arbitrary rows, most of them not paychecks, and the calendar costs
        two queries and REFUSES an owner with no schedule row.  So the
        derived pricer answers ``user_id`` without deriving, the profile
        lookup runs against it, and a template no profile names stops there.
        Then the first real paycheck derives it, exactly once.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()
            period = all_periods(user_id)[5]
            txn = _make_txn(seed_user, period, template=template)
            db.session.commit()

            with counting_calls(
                ("app.services.pay_calendar._derive", "derive_periods"),
            ) as counts:
                paychecks = income_service.derived_paycheck_pricing(user_id)
                pricing = income_service.salary_pricing(scenario_id, paychecks)
                assert paychecks.user_id == user_id
                assert pricing.net_for(template.id + 1000, period.id) is None
                assert counts["derive_periods"] == 0, (
                    "a template no profile names derived the calendar"
                )
                assert pricing.net_for(template.id, txn.pay_period_id) == (
                    Decimal("4000.00")
                )
                assert counts["derive_periods"] == 1
                pricing.net_for(template.id, txn.pay_period_id)
                assert counts["derive_periods"] == 1, (
                    "the derived pricer derived its calendar twice"
                )


# ── salary:S3-f-1: the pricer is keyed on the raise set ─────────────


def _believed_through(row: SalaryRaise, terminal_year) -> RaiseTerms:
    """The row's terms, believed through *terminal_year* instead.

    The shape a what-if hands the engine (plan step salary:S3-f): the
    production value with ONE term changed.
    """
    return replace(RaiseTerms.of(row), terminal_year=terminal_year)


class TestThePricerIsKeyedOnTheRaiseSet:
    """One pricer per profile PER RAISE SET, and the rows are one set.

    Plan step **salary:S3-f-1** (ruling **R-SAL20**).
    :meth:`~app.services.income_service.PaycheckPricing.for_profile` keyed on
    the profile's id alone, and :class:`ProfilePaychecks` memoizes by payday
    alone -- so a second raise set for one profile had nowhere to go but the
    stored set's memo.  The key carries the terms now; ``None`` spells the
    rows, which keeps every stored-plan render on the one pricer it always
    built.
    """

    @staticmethod
    def _profile_with_a_forever_raise(seed_user):
        """``$104,000`` with a recurring 5% March raise from 2026, no end."""
        profile = _create_profile(
            seed_user["user"].id, seed_user["scenario"].id,
        )
        merit = db.session.query(RaiseType).filter_by(name="merit").one()
        row = SalaryRaise(
            salary_profile_id=profile.id, raise_type_id=merit.id,
            effective_date=date(2026, 3, 1),
            percentage=Decimal("0.0500"), is_recurring=True,
            terminal_year=None,
        )
        db.session.add(row)
        db.session.commit()
        db.session.refresh(profile)
        return profile, row

    @staticmethod
    def _payday_in(calendar, year):
        """A projected payday in *year*, off the calendar's own rhythm."""
        axis = calendar.axis(calendar.opening_bound(), date(year, 12, 31))
        return next(p for p in axis if p.start_date.year == year
                    and p.start_date.month >= 6)

    def test_every_spelling_of_the_stored_set_is_ONE_pricer(
        self, app, db, seed_user, seed_periods,
    ):
        """``None``, the rows, and values carrying the rows' terms: one key.

        The finding an adversarial review of this step made: keyed on the
        caller's objects, the stored set had three spellings and each built a
        pricer of its own -- the two-derivations-of-one-figure shape
        ``PlanPoint`` refuses one tier up, and one the pricer-count gate
        cannot see on a probe request, where a second pricer is also the
        legitimate outcome.  The fourth spelling is the one S3-f-2b's rail
        actually sends on every refresh: a probe carrying the stored end year
        unchanged.
        """
        with app.app_context():
            profile, row = self._profile_with_a_forever_raise(seed_user)
            paychecks = pricing_over(
                calendar_for(profile.user_id),
            )
            stored = paychecks.for_profile(profile)

            assert paychecks.for_profile(profile, tuple(profile.raises)) is stored
            assert paychecks.for_profile(profile, terms_of(profile.raises)) is stored
            assert paychecks.for_profile(
                profile, (_believed_through(row, row.terminal_year),),
            ) is stored, (
                "a probe carrying the STORED end year built a second pricer "
                "for the stored set"
            )

    def test_the_rows_are_one_key_and_a_raise_set_is_another(
        self, app, db, seed_user, seed_periods,
    ):
        """Same terms, same pricer; the rows' pricer is untouched by them."""
        with app.app_context():
            profile, row = self._profile_with_a_forever_raise(seed_user)
            paychecks = pricing_over(
                calendar_for(profile.user_id),
            )
            stored = paychecks.for_profile(profile)
            believed = paychecks.for_profile(
                profile, (_believed_through(row, 2026),),
            )

            assert paychecks.for_profile(profile) is stored
            assert believed is not stored, (
                "a raise set other than the rows was served the rows' pricer"
            )
            # An EQUAL tuple built again is the same key: a probe repeated
            # at one raise set must not build a second pricer.
            assert paychecks.for_profile(
                profile, (_believed_through(row, 2026),),
            ) is believed
            assert paychecks.for_profile(profile) is stored

    def test_a_pricer_under_terms_prices_off_them(
        self, app, db, seed_user, seed_periods,
    ):
        """The paychecks a keyed pricer answers are the terms', end to end.

        A June 2028 payday: three applications of 5% on the ``$4,000.00``
        entry under the rows (``4,000.00 x 1.05 = 4,200.00``, ``x 1.05 =
        4,410.00``, ``x 1.05 = $4,630.50``), one under terms believed through
        2026 (``4,000.00 x 1.05 = $4,200.00``).
        """
        with app.app_context():
            profile, row = self._profile_with_a_forever_raise(seed_user)
            calendar = calendar_for(profile.user_id)
            paychecks = pricing_over(calendar)
            june_2028 = self._payday_in(calendar, 2028)

            assert paychecks.for_profile(profile).at(
                june_2028,
            ).earnings.gross_biweekly == Decimal("4630.50")
            assert paychecks.for_profile(
                profile, (_believed_through(row, 2026),),
            ).at(june_2028).earnings.gross_biweekly == Decimal("4200.00"), (
                "the pricer keyed on the terms priced the profile's rows"
            )

    def test_a_never_flushed_raise_is_priced_from_its_FK(
        self, app, db, seed_user, seed_periods,
    ):
        """The X-bl invariance control's row: appended, never flushed, priced.

        ``tests/manual/verify_amount_resolver.py`` appends a ``SalaryRaise``
        with only its ``raise_type_id`` set to a loaded profile under
        ``no_autoflush`` and prices every salary row through the pricer.
        SQLAlchemy does not lazy-load a relationship on a pending instance,
        so ``row.raise_type`` is ``None`` there; the type's name comes off the
        FK through the ref cache instead, and the paycheck moves by the
        raise.  A second adversarial review of S3-f-1 found the relationship
        read raising ``AttributeError`` on exactly this row.

        **The raise is effective February and the paycheck priced is the
        first one paid wholly in February** since task plan#3100.  The pay
        entry is recorded on the first payday, 2026-01-02, and holds every
        raise dated by the day its paycheck opens, 2025-12-14 (ruling
        plan#3091), so the February raise is after it.  The case priced
        February's first paycheck, 02-13, until a paycheck paid each weekday
        at its own pay (ruling plan#3089): 02-13 pays 01-25..02-07, half
        before the raise, so it moves by $500.00.  **Its banner is read on
        02-13** since task plan#3101: that is the first paycheck the raise
        reaches (ruling plan#3092), and 02-27 badges nothing.
        """
        with app.app_context():
            profile = _create_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()
            db.session.refresh(profile)
            calendar = calendar_for(profile.user_id)
            first = calendar.saved()[0]
            february = next(
                period for period in calendar.saved()
                if days_paid_for(
                    calendar, period.start_date,
                    profile.period_end_days_before_payday,
                ).first >= date(first.start_date.year, 2, 1)
            )
            assert february.start_date == date(first.start_date.year, 2, 27)
            reached = next(
                period for period in calendar.saved()
                if days_paid_for(
                    calendar, period.start_date,
                    profile.period_end_days_before_payday,
                ).last >= date(first.start_date.year, 2, 1)
            )
            assert reached.start_date == date(first.start_date.year, 2, 13)
            before = pricing_over(calendar).for_profile(
                profile,
            ).at(february).earnings.gross_biweekly

            with db.session.no_autoflush:
                pending = SalaryRaise(
                    salary_profile_id=profile.id,
                    raise_type_id=ref_cache.raise_type_id(
                        RaiseTypeEnum.CUSTOM,
                    ),
                    effective_date=date(first.start_date.year, 2, 1),
                    flat_amount=Decimal("26000.00"), percentage=None,
                    is_recurring=False, terminal_year=None,
                )
                profile.raises.append(pending)
                # The relationship is unloaded on a pending instance, which
                # is the state under test; ``no_autoflush`` is what keeps it
                # pending through the pricing below.
                assert pending.raise_type is None
                paychecks = pricing_over(calendar).for_profile(profile)
                after = paychecks.at(february)
                first_reached = paychecks.at(reached)
                db.session.rollback()

            # $26,000 a year is exactly $1,000.00 a paycheck over 26.
            assert after.earnings.gross_biweekly == before + Decimal("1000.00")
            assert first_reached.period.raise_event == "CUSTOM +$26,000.00"
            assert after.period.raise_event == ""

