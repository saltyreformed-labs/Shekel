"""
Shekel Budget App -- recurring_view producer tests (Recurring cluster Loop B, P1).

Locks the unified Recurring surface's display-model producer: the summary
band, the three kind-grouped sections with per-section subtotals, and per
row the defined amount, monthly + per-paycheck equivalents, engine-backed
next date, and share of section committed total.

The producer has exactly one monthly source of truth
(``obligations_aggregator.template_monthly_or_none``); the per-paycheck
value is DERIVED from it by
``PayCadence.monthly_to_per_paycheck`` -- the OWNER's cadence since plan step
R7a-2a, a hardcoded ``12 / 26`` before it.
Every expectation below is a LITERAL hand-computed at the biweekly cadence
with its arithmetic in a comment, never a re-derivation through the producer's
own value object -- an expectation computed with ``PayCadence`` would move with
a derivation bug instead of catching it.  Real ORM templates run against the
test DB so the relationship-driven attribute access and ``ref_cache`` lookups
are exercised end to end.
"""

import logging
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from tests._test_helpers import (
    create_loan_account,
    freeze_today,
    make_cadence_rule,
    make_loan_payment_template,
    make_retired_loan_payment,
    state_template_price,
)
from tests.oracles.recurrence_baseline import (
    EVERY_PERIOD,
    MONTHLY,
    QUARTERLY,
    ANNUAL,
)
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import account_service, balance_at, recurring_view
from app.services.balance_at import BalanceContext, is_standing_loan_payment
from app.services.obligations_aggregator import TodaysPrice, committed_monthly
from app.services.pay_calendar import (
    PayCadence,
    PayCalendar,
    PayCalendarError,
    calendar_for,
)
from app.services.pay_rhythm import FixedDays
from app.services.recurrence import (
    EndsOnDate,
    RecurrenceResolutionError,
    read_rule,
    reauthor_rule,
    recurrence_spec,
)
from app.services.recurrence import rule_occurrences
# Imported as a MODULE so the firing controls below patch the names the
# read door resolves at CALL time.  Patching this file's own imported
# names would leave the composition calling the real ones.
from app.services.recurrence import _reading
from app.services.recurrence import compute_due_date

#: The cadence ``seed_periods_today`` builds and every figure below is
#: hand-computed at: 14 days between paydays, 26 a year.  An explicit input
#: since plan step R7a-2a, where the per-paycheck column read a hardcoded
#: ``MONTHS_PER_YEAR / PAY_PERIODS_PER_YEAR`` ratio.
_BIWEEKLY = PayCadence(FixedDays(14))


# ── Helpers ──────────────────────────────────────────────────────────


def _calendar(periods):
    """Return the owner's schedule as the surface reads it.

    ``recurring_view.build_view`` takes a
    :class:`~app.services.pay_calendar.PayCalendar` rather than a period list
    since plan step R4b-1: a recurrence's next date is measured against the
    OWNER's schedule, so the surface has to be handed that schedule rather
    than rebuild one per row.

    **Loaded through the one door since plan step C2-b2**, rather than built
    from the rows the caller happens to hold.  ``calendar_for`` reads the whole
    payday set and the owner's cadence and derives the rest, so the calendar a
    test hands the surface is the one the route would.

    Args:
        periods: The owner's pay periods -- read only for whose they are.

    Returns:
        The :class:`~app.services.pay_calendar.PayCalendar` for their owner.
    """
    return calendar_for(periods[0].user_id)


def _ctx(seed_user, as_of=None):
    """Return the read pass the surface takes (plan step R7d-d).

    ``build_view`` and ``build_archived_rows`` take a
    :class:`~app.services.balance_at.BalanceContext` rather than a calendar
    and an as-of since that step: the pass carries the owner's schedule AND
    the scenario a loan payment's stop is folded in, so the two cannot be
    handed in disagreeing.  Built the way the route builds it -- at
    ``date.today()`` -- unless a case pins the day the way its fixtures do.

    Args:
        seed_user: The owner.
        as_of: The pass's "now", or ``None`` for today.

    Returns:
        The :class:`~app.services.balance_at.BalanceContext`.
    """
    return BalanceContext.build(
        seed_user["user"].id, date.today() if as_of is None else as_of,
    )


def _empty_schedule(seed_user):
    """Return a read pass over an owner who has a cadence and NO paydays.

    The bootstrap period is deleted AFTER the case has authored its
    definition -- a rule is resolved against the schedule when it is written
    -- and the ``budget.pay_schedule`` row stays, so the calendar the pass
    derives is empty but still carries the owner's cadence.  That is the state
    :class:`TestNoneMeansDoesNotRepeat` is about, and it is deliberately not
    the cadence-less one :class:`TestAnAbsentCadenceIsRefused` covers.

    Args:
        seed_user: The owner whose paydays to delete.

    Returns:
        A pass whose ``calendar()`` holds no periods.
    """
    db.session.query(PayPeriod).filter_by(
        user_id=seed_user["user"].id,
    ).delete(synchronize_session=False)
    db.session.flush()
    ctx = _ctx(seed_user)
    assert not ctx.calendar().periods, "precondition: no pay periods"
    return ctx


def _author_cadence(tmpl, cadence, *, interval_n=1,
                    day_of_month=None, month_of_year=None, end_date=None):
    """Author *cadence* onto *tmpl*, through the write door.

    Through that door since plan step R7c-b, which made the two-axis columns
    NOT NULL: a rule naming only a pattern no longer produces a row.  It takes
    the OWNING template since plan step R-F6, which put the owning FK on
    ``budget.recurrence_rules`` -- so a rule cannot exist before the definition
    it belongs to, and these fixtures build the definition first.
    """
    return make_cadence_rule(
        tmpl, cadence,
        interval_n=interval_n,
        fires_on_day=day_of_month,
        fires_in_month=month_of_year,
        end_date=end_date,
    )


def _create_txn_template(seed_user, cadence, amount, *, type_enum, name,
                         **rule_kwargs):
    """Create and flush an income or expense TransactionTemplate.

    ``cadence`` may be ``None`` -- that is how a definition says "does not
    repeat" since plan step R2e-3.
    """
    tmpl = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(type_enum),
        name=name,
        is_active=True,
    )
    db.session.add(tmpl)
    db.session.flush()
    # Its price, through the one write door as the create route states it:
    # the surface prices a definition by its series since plan step X-bp.
    state_template_price(tmpl, amount)
    if cadence is not None:
        _author_cadence(tmpl, cadence, **rule_kwargs)
    return tmpl


def _create_expense(seed_user, cadence, amount, *, name="Expense",
                    **rule_kwargs):
    """Create and flush an expense TransactionTemplate with its cadence."""
    return _create_txn_template(
        seed_user, cadence, amount, type_enum=TxnTypeEnum.EXPENSE, name=name,
        **rule_kwargs,
    )


def _create_income(seed_user, cadence, amount, *, name="Income",
                   **rule_kwargs):
    """Create and flush an income TransactionTemplate with its cadence."""
    return _create_txn_template(
        seed_user, cadence, amount, type_enum=TxnTypeEnum.INCOME, name=name,
        **rule_kwargs,
    )


def _create_savings(seed_user, name="Test Savings"):
    """Create and flush a savings Account for the seed user."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name=name,
            anchor_balance=Decimal("5000.00"),
        ),
    )
    db.session.add(account)
    db.session.flush()
    return account


def _create_transfer(seed_user, cadence, amount, to_account, *,
                     name="Transfer", **rule_kwargs):
    """Create and flush a recurring TransferTemplate with its cadence."""
    tmpl = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        name=name,
        is_active=True,
    )
    db.session.add(tmpl)
    db.session.flush()
    state_template_price(tmpl, amount)
    _author_cadence(tmpl, cadence, **rule_kwargs)
    return tmpl


# ── Both-units equivalents ───────────────────────────────────────────


class TestUnitEquivalents:
    """Monthly and per-paycheck equivalents for each cadence."""

    def test_biweekly_both_units(self, seed_user, seed_periods_today):
        """A $100 every-paycheck expense: monthly = 100 * 26 / 12 = $216.67,
        per-paycheck = that monthly re-expressed = exactly $100.00.
        """
        tmpl = _create_expense(seed_user, EVERY_PERIOD, Decimal("100.00"))

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )
        row = view.expenses.rows[0]
        # Hand-computed at 26 paychecks a year: 100 * 26 / 12 = 216.6667 ->
        # 216.67, and 216.6667 * 12 / 26 collapses back to the original 100.00.
        assert row.equivalent.monthly == Decimal("216.67")
        assert row.equivalent.per_paycheck == Decimal("100.00")

    def test_monthly_both_units(self, seed_user, seed_periods_today):
        """A $500 monthly expense: monthly = $500.00,
        per-paycheck = 500 * 12 / 26 = 230.7692... -> $230.77.
        """
        tmpl = _create_expense(seed_user, MONTHLY, Decimal("500.00"), day_of_month=15)

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )
        row = view.expenses.rows[0]
        # Hand-computed: 500 * 12 / 26 = 230.7692... -> 230.77.
        assert row.equivalent.monthly == Decimal("500.00")
        assert row.equivalent.per_paycheck == Decimal("230.77")

    def test_annual_both_units(self, seed_user, seed_periods_today):
        """A $1,200 annual expense: monthly = 1200 / 12 = $100.00,
        per-paycheck = 1200 / 26 = 46.1538... -> $46.15.
        """
        tmpl = _create_expense(seed_user, ANNUAL, Decimal("1200.00"), day_of_month=1, month_of_year=6)

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )
        row = view.expenses.rows[0]
        # Hand-computed: 1200 / 12 = 100.00 a month; 100 * 12 / 26 =
        # 46.1538... -> 46.15 a paycheck.
        assert row.equivalent.monthly == Decimal("100.00")
        assert row.equivalent.per_paycheck == Decimal("46.15")


# ── Subtotals and the aggregator SSOT ────────────────────────────────


class TestSubtotals:
    """Section subtotals stay identical to the canonical aggregator."""

    def test_subtotal_matches_committed_monthly(
        self, seed_user, seed_periods_today,
    ):
        """The expense section's monthly subtotal equals
        ``committed_monthly`` for the same templates, so the unified surface
        and /savings can never disagree.

        $100 biweekly (216.67) + $500 monthly (500.00) = $716.67.
        """
        e1 = _create_expense(seed_user, EVERY_PERIOD, Decimal("100.00"), name="A")
        e2 = _create_expense(seed_user, MONTHLY, Decimal("500.00"), name="B", day_of_month=15)
        as_of = date.today()

        ctx = _ctx(seed_user, as_of)
        view = recurring_view.build_view([], [e1, e2], [], ctx)
        assert view.expenses.subtotal.monthly == Decimal("716.67")
        assert view.expenses.subtotal.monthly == committed_monthly(
            [e1, e2], ctx,
        )

    def test_subtotal_per_paycheck_derives_from_monthly(
        self, seed_user, seed_periods_today,
    ):
        """The per-paycheck subtotal is the full-precision monthly total
        re-expressed per paycheck.

        Full monthly total = 100*26/12 + 500 = 716.6667;
        per-paycheck = 716.6667 * 12 / 26 = 330.769... -> $330.77.
        """
        e1 = _create_expense(seed_user, EVERY_PERIOD, Decimal("100.00"), name="A")
        e2 = _create_expense(seed_user, MONTHLY, Decimal("500.00"), name="B", day_of_month=15)

        view = recurring_view.build_view(
            [], [e1, e2], [], _ctx(seed_user),
        )
        # Hand-computed: (100 * 26 / 12) + 500 = 716.6667 a month at full
        # precision, then * 12 / 26 = 330.7692... -> 330.77 a paycheck.
        assert view.expenses.subtotal.per_paycheck == Decimal("330.77")

    def test_empty_section_subtotal_is_zero(self, seed_user, seed_periods_today):
        """A section with no templates subtotals to $0.00 in both units."""
        view = recurring_view.build_view(
            [], [], [], _ctx(seed_user),
        )
        assert view.expenses.rows == ()
        assert view.expenses.subtotal.monthly == Decimal("0.00")
        assert view.expenses.subtotal.per_paycheck == Decimal("0.00")


# ── Non-recurring rows: present but blank, excluded from totals ───────


class TestNonRecurringRows:
    """Non-repeating / expired definitions show as manageable rows
    but contribute nothing to any total (the management surface shows all
    active definitions; the totals are the /obligations kernel).

    "Does not repeat" is ``recurrence_rule_id IS NULL`` on both template
    kinds since plan step R2e-3.  These cases named a ``Once``-PATTERN rule
    before it, which is the second spelling that step removed."""

    def test_non_repeating_row_present_but_blank(
        self, seed_user, seed_periods_today,
    ):
        """A rule-less expense appears as a row with a blank equivalent
        and no next date, and adds $0 to the subtotal.
        """
        once = _create_expense(seed_user, None, Decimal("999.00"), name="OneTime")
        real = _create_expense(seed_user, EVERY_PERIOD, Decimal("100.00"), name="Real")

        view = recurring_view.build_view(
            [], [once, real], [], _ctx(seed_user),
        )
        names = {row.template.name: row for row in view.expenses.rows}
        assert "OneTime" in names, "one-time definition must still be listed"
        once_row = names["OneTime"]
        assert once_row.equivalent.monthly is None
        assert once_row.equivalent.per_paycheck is None
        assert once_row.next_date is None
        assert once_row.share_pct is None
        # Only the real recurring expense counts toward the subtotal.
        # 100.00 * 26 / 12 = 216.666... -> 216.67
        assert view.expenses.subtotal.monthly == Decimal("216.67")

    def test_non_repeating_row_logs_no_unknown_pattern_warning(
        self, seed_user, seed_periods_today, caplog,
    ):
        """A rule-less definition emits no 'unknown pattern' warning.

        ``_next_occurrence`` returns on the rule-less branch before reaching
        the reverse matcher, which logged that warning for any pattern it had no
        branch for.  Until plan step R2e-3 a second guard was needed beside it
        for the ``Once`` pattern, which the matcher also had no branch
        for -- so a one-time definition logged it on EVERY render without one.
        """
        once = _create_expense(seed_user, None, Decimal("999.00"), name="OneTime")

        with caplog.at_level(logging.WARNING):
            recurring_view.build_view(
                [], [once], [], _ctx(seed_user),
            )
        assert "Unknown recurrence pattern" not in caplog.text

    def test_no_rule_row_present_but_blank(self, seed_user, seed_periods_today):
        """A template with no recurrence rule appears with a blank equivalent."""
        tmpl = _create_expense(seed_user, None, Decimal("42.00"), name="NoRule")

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )
        row = view.expenses.rows[0]
        assert row.template.name == "NoRule"
        assert row.equivalent.monthly is None
        assert row.next_date is None
        assert view.expenses.subtotal.monthly == Decimal("0.00")

    def test_expired_row_present_but_blank(self, seed_user, seed_periods_today):
        """An active template whose rule.end_date is in the past appears as
        a manageable row with a blank equivalent and no next date, and adds
        nothing to the subtotal (it is no longer a future commitment).
        """
        tmpl = _create_expense(seed_user, EVERY_PERIOD, Decimal("1500.00"), name="Expired", end_date=date.today() - timedelta(days=1))

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )
        row = view.expenses.rows[0]
        assert row.template.name == "Expired"
        assert row.equivalent.monthly is None
        assert row.next_date is None
        assert view.expenses.subtotal.monthly == Decimal("0.00")


# ── Summary band ─────────────────────────────────────────────────────


class TestSummaryBand:
    """The obligations-kernel band: income vs committed outflow."""

    def test_band_net_and_pct(self, seed_user, seed_periods_today):
        """Income $1,500 biweekly, expense $100 biweekly, transfer $500 monthly.

        income monthly   = 1500 * 26 / 12 = 3250.00
        expense monthly  = 100 * 26 / 12  = 216.67
        transfer monthly = 500.00
        net monthly      = 3250.00 - 216.67 - 500.00 = 2533.33
        expenses % income = 216.67 / 3250.00 * 100 = 6.6667 -> 6.7
        net per-paycheck = 1500.00 - 100.00 - 230.77 = 1169.23
        """
        savings = _create_savings(seed_user)
        income = _create_income(seed_user, EVERY_PERIOD, Decimal("1500.00"))
        expense = _create_expense(seed_user, EVERY_PERIOD, Decimal("100.00"))
        transfer = _create_transfer(
            seed_user, MONTHLY, Decimal("500.00"), savings, day_of_month=1,
        )

        view = recurring_view.build_view(
            [income], [expense], [transfer], _ctx(seed_user),
        )
        band = view.band
        assert band.income.monthly == Decimal("3250.00")
        assert band.expenses.monthly == Decimal("216.67")
        assert band.transfers_out.monthly == Decimal("500.00")
        assert band.net.monthly == Decimal("2533.33")
        assert band.expenses_pct_of_income == Decimal("6.7")
        # per-paycheck net from the rounded subtotals: 1500 - 100 - 230.77.
        assert band.net.per_paycheck == Decimal("1169.23")

    def test_pct_of_income_none_without_income(
        self, seed_user, seed_periods_today,
    ):
        """With no income, the expenses-percent-of-income chip is None."""
        expense = _create_expense(seed_user, EVERY_PERIOD, Decimal("100.00"))

        view = recurring_view.build_view(
            [], [expense], [], _ctx(seed_user),
        )
        assert view.band.expenses_pct_of_income is None
        assert view.band.income.monthly == Decimal("0.00")

    def test_empty_band(self, seed_user, seed_periods_today):
        """No definitions: every band figure is $0.00 and the pct is None."""
        view = recurring_view.build_view(
            [], [], [], _ctx(seed_user),
        )
        assert view.band.net.monthly == Decimal("0.00")
        assert view.band.net.per_paycheck == Decimal("0.00")
        assert view.band.expenses_pct_of_income is None


# ── Share of committed and default ordering ──────────────────────────


class TestSharesAndOrdering:
    """Per-row share bars and the cost-descending default order."""

    def test_share_pct(self, seed_user, seed_periods_today):
        """Two expenses: $100 biweekly (216.67) and $500 monthly (500.00),
        section total 716.6667.

        share A = 216.6667 / 716.6667 * 100 = 30.2326 -> 30.2
        share B = 500 / 716.6667 * 100      = 69.7674 -> 69.8
        """
        _create_expense(seed_user, EVERY_PERIOD, Decimal("100.00"), name="Small")
        _create_expense(seed_user, MONTHLY, Decimal("500.00"), name="Big", day_of_month=15)

        view = recurring_view.build_view(
            [],
            _load_expenses(seed_user),
            [],
            _ctx(seed_user),
        )
        by_name = {row.template.name: row for row in view.expenses.rows}
        assert by_name["Small"].share_pct == Decimal("30.2")
        assert by_name["Big"].share_pct == Decimal("69.8")

    def test_rows_sorted_by_monthly_desc_then_nonrecurring_last(
        self, seed_user, seed_periods_today,
    ):
        """Rows land in monthly-cost-descending order, non-repeating last.

        The last row's amount (999.00) is the LARGEST, so ordering by amount
        would put it first; it sorts last because a rule-less definition has
        no monthly equivalent at all.
        """
        _create_expense(seed_user, MONTHLY, Decimal("300.00"), name="Mid", day_of_month=1)
        _create_expense(seed_user, MONTHLY, Decimal("900.00"), name="High", day_of_month=1)
        _create_expense(seed_user, MONTHLY, Decimal("100.00"), name="Low", day_of_month=1)
        _create_expense(seed_user, None, Decimal("999.00"), name="Once")

        view = recurring_view.build_view(
            [], _load_expenses(seed_user), [], _ctx(seed_user),
        )
        order = [row.template.name for row in view.expenses.rows]
        assert order == ["High", "Mid", "Low", "Once"]


# ── Engine-backed next dates ─────────────────────────────────────────


class TestNextDates:
    """Next occurrence is the recurrence engine's own due date."""

    def test_next_date_monthly_is_engine_due_date(
        self, seed_user, seed_periods_today,
    ):
        """A monthly-on-the-15th expense's next_date equals the engine's
        due date for the next matching period on or after today, and lands
        on the 15th.
        """
        today = date.today()
        tmpl = _create_expense(seed_user, MONTHLY, Decimal("100.00"), day_of_month=15)

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )
        next_date = view.expenses.rows[0].next_date
        # Independent engine recomputation of the contract.
        # Reached through the template that owns it (plan step R-F6): the
        # rule is authored onto the definition, so the definition is what a
        # caller holds a handle to.
        rule = tmpl.recurrence_rule
        matched = [
            placement
            for placement in rule_occurrences(
                rule, _calendar(seed_periods_today),
            )
            if placement.period is not None
            and placement.period.end_date >= today
        ]
        expected = next(
            compute_due_date(rule, p.occurrence, p.period)
            for p in matched
            if compute_due_date(rule, p.occurrence, p.period) >= today
        )
        assert next_date == expected
        assert next_date >= today
        assert next_date.day == 15

    def test_next_date_every_period_is_future_period_start(
        self, seed_user, seed_periods_today,
    ):
        """An every-paycheck expense's next_date is a pay-period start on or
        after today (the current period's start is already past).
        """
        today = date.today()
        tmpl = _create_expense(seed_user, EVERY_PERIOD, Decimal("50.00"))

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )
        next_date = view.expenses.rows[0].next_date
        assert next_date is not None
        assert next_date >= today
        assert next_date in {p.start_date for p in seed_periods_today}


# ── Module-local loader (mirrors the route's active-template load) ────


def _load_expenses(seed_user):
    """Return the seed user's active expense templates ordered like the route.

    The producer applies the cost-descending default itself, so the incoming
    order only fixes the tie-break among non-recurring rows; sort by name for
    a deterministic starting point.
    """
    expense_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    return (
        db.session.query(TransactionTemplate)
        .filter(
            TransactionTemplate.user_id == seed_user["user"].id,
            TransactionTemplate.is_active.is_(True),
            TransactionTemplate.transaction_type_id == expense_id,
        )
        .order_by(TransactionTemplate.name)
        .all()
    )


# ── The cadence phrase, and the ONE read it comes from (plan step R7a) ─


class TestTheRecurrenceDescription:
    """Every row carries how it repeats, produced here rather than in Jinja."""

    def test_an_active_row_carries_its_worded_cadence(
        self, seed_user, seed_periods_today,
    ):
        """The producer answers the phrase the cell renders.

        Until plan step R7a the Recurrence column was eight Jinja branches
        reading ``pattern_id`` / ``day_of_month`` / ``month_of_year`` off the
        rule -- columns plan step R7c drops.  The phrase is a function of what
        the recurrence MEANS against the owner's schedule, which a template
        holds neither of.
        """
        tmpl = _create_expense(seed_user, MONTHLY, Decimal("100.00"), day_of_month=22)

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )

        assert view.expenses.rows[0].recurrence.cadence == "Monthly (day 22)"

    def test_a_rule_less_row_carries_none(self, seed_user, seed_periods_today):
        """"Does not repeat" is ``recurrence_rule_id IS NULL`` since R2e-3.

        ``None`` rather than a phrase, so the cell's "One-time" wording is a
        display decision and the producer states absence honestly.
        """
        tmpl = _create_expense(seed_user, None, Decimal("100.00"))

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )

        assert view.expenses.rows[0].recurrence is None

    def test_the_closing_bound_reaches_the_row_as_its_own_line(
        self, seed_user, seed_periods_today,
    ):
        """The closing bound is carried as the cell's own second line.

        A worded phrase since plan step R7b-3 rather than a bare date, so the
        surface renders one string and the wording of a stop is decided in the
        producer beside the wording of a cadence.

        Both dates are LITERALS and so is the expectation.  Deriving the
        expected string with ``f"{end:%b}"`` would assert the locale-safe
        producer against the ``strftime`` it replaced, and could not fail for
        the reason it was written; a date built from ``today()`` would make
        the case's subject move with the calendar.  A bound in 2029 is past
        every fixture's horizon, which is what this asserts about -- the cell
        carries the stop whether or not the schedule reaches it.
        """
        end = date(2029, 9, 15)
        tmpl = _create_expense(seed_user, MONTHLY, Decimal("100.00"), day_of_month=22, end_date=end)

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )

        assert view.expenses.rows[0].recurrence.stops == "until Sep 15, 2029"

    def test_a_transfer_row_carries_one_too(
        self, seed_user, seed_periods_today,
    ):
        """The transfers section takes the same producer, not a second one."""
        savings = _create_savings(seed_user)
        tmpl = _create_transfer(seed_user, EVERY_PERIOD, Decimal("50.00"), savings)

        view = recurring_view.build_view(
            [], [], [tmpl], _ctx(seed_user),
        )

        assert view.transfers.rows[0].recurrence.cadence == "Every paycheck"

    def test_each_rule_is_RESOLVED_exactly_once_per_build(
        self, seed_user, seed_periods_today, monkeypatch,
    ):
        """The phrase and the next date are two questions about ONE read.

        ``read_rule`` returns the resolved meaning and the placements
        together, so a row costs one ``resolve``.  Composing the two steps at
        the call site instead would resolve twice per row -- a second
        resolution point in one request.

        Patched at the DEFINITION site the composition calls
        (``_reading.resolve``), not at this module's imported name: patching a
        re-export proves only that the harness reads what it reads.
        """
        expense = _create_expense(seed_user, MONTHLY, Decimal("100.00"), day_of_month=22)
        income = _create_income(seed_user, EVERY_PERIOD, Decimal("2000.00"))

        calls = []
        real_resolve = _reading.resolve

        def counting_resolve(spec, calendar):
            # The CADENCE identifies the call since plan step R7b, which is
            # what a spec carries now; the two definitions above are a monthly
            # bill and an every-paycheck income, so the two entries also say
            # the right rules were read.
            calls.append((spec.interval_n, spec.unit))
            return real_resolve(spec, calendar)

        monkeypatch.setattr(_reading, "resolve", counting_resolve)

        view = recurring_view.build_view(
            [income], [expense], [], _ctx(seed_user),
        )

        # The control must be shown to FIRE: if the patch missed, ``calls`` is
        # empty and "exactly two" would pass for the wrong reason.
        assert view.expenses.rows[0].recurrence is not None
        assert view.income.rows[0].recurrence is not None
        assert len(calls) == 2, (
            f"two rule-bearing definitions resolved {len(calls)} times; each "
            f"must be read once per build"
        )


#: The day the loan cases below are measured at, frozen so the derived payoff
#: is a LITERAL.  The door's own tests (``test_recurring_definition``) use the
#: same day and the same $12,000 / 5% / 24-month loan, whose payoff read then is
#: 2028-07-01; a case here that disagreed with one there would be the surface
#: and its door coming apart.
_LOAN_TODAY = date(2026, 7, 1)


def _loan(seed_user, **kwargs):
    """Return a 24-month $12,000 loan at 5%, originating on the frozen day.

    Args:
        seed_user: The owner.
        **kwargs: Overrides forwarded to ``create_loan_account``.

    Returns:
        The loan ``Account``.
    """
    defaults = {
        "name": "Surface Loan",
        "principal": Decimal("12000.00"),
        "rate": Decimal("0.05000"),
        "term": 24,
        "origination_date": _LOAN_TODAY,
    }
    defaults.update(kwargs)
    return create_loan_account(seed_user, db.session, **defaults)


def _retired_loan_payment(seed_user):
    """Return a loan payment whose loan was cleared AFTER its first installment.

    The loan originates 2026-05-01 with a ``payment_day`` of 1, so its first
    contractual installment is 2026-06-01; a true-up to ``$0.00`` on 2026-06-15
    retires it.  Its closing date is therefore 2026-06-15 -- the same fixture
    ``test_loan_recurrence_sync`` pins ``ClosesOn(2026-06-15)`` on -- and the
    definition HAS fired once, so the stop is a date and not "never runs".

    Args:
        seed_user: The owner.

    Returns:
        ``(loan, template)``, committed.
    """
    # The shared builder since plan step R7d-e, so the aggregator's and the
    # /savings floor's retired-loan cases describe THIS loan and not a cousin.
    return make_retired_loan_payment(
        db.session, seed_user,
        origination_date=date(2026, 5, 1), cleared_on=date(2026, 6, 15),
    )


class TestTheDestinationsStopReachesTheRow:
    """A loan payment's row stops where the LOAN says, not where a column does.

    Plan step R7d-d.  Until it, the only way a loan's payoff reached this
    surface was the cached copy ten chokepoints wrote into
    ``budget.recurrence_rules.end_date`` -- the authored bound's own column --
    so the stop line and the next date named whichever value a chokepoint had
    most recently written (plan ledger row **D35**).  The surface now reads the
    composed door, so both name the loan's DERIVED closing date.

    **Every case leaves that column NULL and asserts it stayed so.**  A phrase
    that could have come from the column would prove nothing about the
    derivation; with the column empty the rule's authored bound is
    ``NEVER_ENDS``, and before this step each of these rows showed no stop line
    and a next date the loan had already made impossible.
    """

    @pytest.fixture(autouse=True)
    def _frozen(self, monkeypatch):
        """Freeze today mid-loan so the projected payoff does not drift."""
        freeze_today(monkeypatch, _LOAN_TODAY)

    def test_a_live_loan_payments_stop_line_is_the_derived_payoff(
        self, seed_user, seed_periods_52,
    ):
        """The rule authors NO stop; the cell names the loan's payoff anyway.

        Asserted against the seam's own ``closing_date`` as well as against
        the literal, so a drift in the loan fixture reports as a precondition
        rather than as a display defect.
        """
        loan = _loan(seed_user)
        tpl = make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        assert tpl.recurrence_rule.end_date is None, (
            "precondition: nothing stored could have supplied the stop"
        )
        ctx = _ctx(seed_user, _LOAN_TODAY)
        assert balance_at.loan_figures(loan, ctx).closing_date == (
            date(2028, 7, 1)
        ), "precondition: the fixture loan pays off 2028-07-01"

        view = recurring_view.build_view([], [], [tpl], ctx)
        row = view.transfers.rows[0]

        assert row.recurrence.stops == "until Jul 01, 2028"
        assert row.next_date is not None, (
            "a loan still owing must keep a next date; the stop reached the "
            "row but the walk was narrowed to nothing"
        )
        assert row.next_date >= _LOAN_TODAY

    def test_an_owners_stop_on_the_standing_payment_reaches_the_row(
        self, seed_user, seed_periods_52,
    ):
        """Ruling **R-R82** on the surface: a stored stop is the owner's word and the row names it.

        Until plan step R7d-g this case pinned plan ledger row **D35**'s shape
        the other way round: the column was the chokepoints' cache, EARLIER
        than the loan's payoff (``2029-01-22`` stored against ``2029-02-22``
        derived on production), and the door read it as no bound at all
        (ruling **R-R56**) so the row named the payoff.  Nothing writes a
        cache there now; a date in that column is a stop the owner authored,
        the door composes it, and the row names it.  The next date is still
        placed, because the pass reads before the stop.
        """
        loan = _loan(seed_user)
        tpl = make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        ctx = _ctx(seed_user, _LOAN_TODAY)
        authored = date(2027, 1, 1)
        reauthor_rule(
            tpl.recurrence_rule,
            replace(recurrence_spec(tpl.recurrence_rule), end_bound=EndsOnDate(on=authored)),
            ctx.calendar(),
        )
        db.session.commit()
        assert tpl.recurrence_rule.end_date == authored, "precondition: stored"
        assert is_standing_loan_payment(tpl, ctx), (
            "precondition: this is the loan's standing payment"
        )

        view = recurring_view.build_view([], [], [tpl], ctx)
        row = view.transfers.rows[0]

        assert row.recurrence.stops == "until Jan 01, 2027"
        assert row.next_date is not None
        assert row.next_date <= authored

    def test_an_ARCHIVED_loan_payments_stored_stop_is_read_as_its_owners(
        self, seed_user, seed_periods_52,
    ):
        """The Archived drawer reads a stored stop as the owner's word, like every reader.

        Until plan step R7d-g the drawer showed the cache the chokepoints
        wrote while the payment was active -- ruling **R-R56** keyed on the
        account's ACTIVE transfer, so an archived one's column was read as
        authored -- and this case pinned that as the interim it was.  R7d-g's
        migration NULLed that cache on every archived transfer into a loan
        (ruling **R-R80**), so a date standing there afterwards is one an
        owner authored and the drawer names it; the migration's own test
        holds the NULLing.  Read beside
        :meth:`test_the_archived_drawer_names_the_same_stop`, which holds that
        the drawer composes the derived stop at all; alone this could not
        tell "composed, and the authored minimum wins" from "never composed".
        """
        loan = _loan(seed_user)
        tpl = make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        ctx = _ctx(seed_user, _LOAN_TODAY)
        authored = date(2027, 1, 1)
        reauthor_rule(
            tpl.recurrence_rule,
            replace(recurrence_spec(tpl.recurrence_rule), end_bound=EndsOnDate(on=authored)),
            ctx.calendar(),
        )
        tpl.is_active = False
        db.session.commit()
        assert not is_standing_loan_payment(tpl, ctx), (
            "precondition: an archived payment is not the account's active one"
        )

        rows = recurring_view.build_archived_rows([tpl], ctx)

        assert rows[0].recurrence.stops == "until Jan 01, 2027"

    def test_a_RETIRED_loan_payments_row_stops_on_the_day_the_loan_closed(
        self, seed_user, seed_periods_52,
    ):
        """The past-and-future closing date, on the surface.

        A loan cleared 2026-06-15 has no forward crossing; its closing date is
        the day it LAST became closed (plan step ``recurrence:R7d-h``), and the
        row names it.  The next date is ``None``: the definition's July
        installment falls after the loan ended, and projecting it would be a
        payment against a debt that is gone -- which is exactly what the
        NEVER_ENDS column showed before this step.

        **The monthly equivalent is asserted too, since plan step R7d-e.**  It
        comes from ``obligations_aggregator.monthly_or_none``, which reads the
        same door as the stop line and the next date; until that step it read
        the authored bound alone, so this row stated ``$200.00`` a month beside
        a stop line that said the money had stopped, and the section total
        carried a retired loan's payment.  The row's figure and the subtotal
        are both asserted: a blank cell over a total that still counts it is
        the surface disagreeing with itself.
        """
        loan, tpl = _retired_loan_payment(seed_user)
        assert tpl.recurrence_rule.end_date is None, (
            "precondition: nothing stored could have supplied the stop"
        )
        ctx = _ctx(seed_user, _LOAN_TODAY)
        assert balance_at.loan_figures(loan, ctx).closing_date == (
            date(2026, 6, 15)
        ), "precondition: the loan closed on the day it was trued to zero"

        view = recurring_view.build_view([], [], [tpl], ctx)
        row = view.transfers.rows[0]

        assert row.recurrence.stops == "until Jun 15, 2026"
        assert row.next_date is None, (
            f"a payment dated {row.next_date} was projected against a loan "
            "that closed 2026-06-15"
        )
        assert row.equivalent.monthly is None, (
            f"a retired loan's payment states {row.equivalent.monthly} a "
            "month beside a stop line that says the money has stopped"
        )
        assert view.transfers.subtotal.monthly == Decimal("0.00")

    def test_an_owners_stop_that_has_passed_BLANKS_the_monthly_figure(
        self, seed_user, seed_periods_52,
    ):
        """One row agreeing with itself, read after an owner's stop on the standing payment.

        Until plan step R7d-g this case pinned plan ledger row **D35**'s shape
        -- a cache EARLIER than the payoff, read between the two -- and held
        that the cache blanked NOTHING, because the door read it as no bound
        (ruling **R-R56**).  A date in that column is the owner's word now
        (ruling **R-R82**), and a stop the pass reads AFTER has ended the
        definition on every reader of the door: the stop line names it, the
        next date is ``None`` and the monthly equivalent is blank, which is
        what plan step R7d-e made ONE reading rather than three.  The stop
        sits after the first installment (2026-08-01) so the stored pair is
        the ordered one ``ck_recurrence_rules_valid_window`` admits, and the
        pass reads five days past it.  ``$200.00`` monthly would be
        ``200 * 12 / (1 * 12)`` for a live row; the control beside this
        (:meth:`test_an_owners_stop_on_the_standing_payment_reaches_the_row`)
        reads BEFORE its stop and keeps a next date.
        """
        loan = _loan(seed_user)
        # The loan door's own shape -- monthly on the payment day, its start
        # bound to the first contractual installment -- which the fixture
        # authors by default since plan step R16-b-2.
        tpl = make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        authored = date(2026, 8, 15)
        assert tpl.recurrence_rule.starts_on == date(2026, 8, 1), (
            "precondition: the stop must follow the first installment"
        )
        reauthor_rule(
            tpl.recurrence_rule,
            replace(recurrence_spec(tpl.recurrence_rule), end_bound=EndsOnDate(on=authored)),
            _ctx(seed_user, _LOAN_TODAY).calendar(),
        )
        db.session.commit()
        assert tpl.recurrence_rule.end_date == authored, "precondition: stored"
        ctx = _ctx(seed_user, date(2026, 8, 20))
        assert is_standing_loan_payment(tpl, ctx), (
            "precondition: this is the loan's standing payment"
        )

        view = recurring_view.build_view([], [], [tpl], ctx)
        row = view.transfers.rows[0]

        assert row.recurrence.stops == "until Aug 15, 2026"
        assert row.next_date is None, (
            f"a payment dated {row.next_date} was projected past the stop "
            "its owner authored"
        )
        assert row.equivalent.monthly is None, (
            f"the row states {row.equivalent.monthly} a month beside a stop "
            "line that says the money has stopped"
        )
        assert view.transfers.subtotal.monthly == Decimal("0.00")

    def test_the_archived_drawer_names_the_same_stop(
        self, seed_user, seed_periods_52,
    ):
        """The drawer reads the same door, so it cannot disagree with the list.

        A drawer describing a different narrowing from the active row beside
        it would be one surface disagreeing with itself about one definition.
        """
        _loan_account, tpl = _retired_loan_payment(seed_user)
        tpl.is_active = False
        db.session.commit()

        rows = recurring_view.build_archived_rows(
            [tpl], _ctx(seed_user, _LOAN_TODAY),
        )

        assert rows[0].recurrence.stops == "until Jun 15, 2026"

    def test_a_loan_payment_is_RESOLVED_exactly_once_per_build(
        self, seed_user, seed_periods_52, monkeypatch,
    ):
        """The rule-14 guard on the door: one rule, one resolution per pass.

        The resolver's EMPTY test needs the definition's first occurrence, and
        the first build of plan step R7d-d had it resolve the rule AGAIN to
        get one -- so a loan payment cost two resolutions where every other
        row cost one.  The door now hands the resolver the value it already
        built.  Patched at the DEFINITION site (``_reading.resolve``) like
        :meth:`TestTheRecurrenceDescription.test_each_rule_is_RESOLVED_exactly_once_per_build`,
        and the control is shown to fire before the count is read.

        **It covers a SYNCED column too, since plan step R7d-e.**  A loan
        payment whose column held a FUTURE date was resolved a second time on
        this surface by the monthly-equivalent producer
        (``template_monthly_or_none`` -> ``has_ended`` ->
        ``EndsOnDate.has_closed`` walked the rule again off its own columns),
        so until that step this case had to scope itself to a NULL column and
        read two otherwise.  The monthly figure now derives from the door's
        one reading, and the second sweep below -- the same loan payment with
        a cached bound written into its column -- reads ONE resolution as
        well, which is the walk R7d-e deleted, shown gone rather than
        described.
        """
        loan = _loan(seed_user)
        tpl = make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()

        calls = []
        real_resolve = _reading.resolve

        def counting_resolve(spec, calendar):
            calls.append(spec.unit)
            return real_resolve(spec, calendar)

        monkeypatch.setattr(_reading, "resolve", counting_resolve)

        view = recurring_view.build_view(
            [], [], [tpl], _ctx(seed_user, _LOAN_TODAY),
        )

        assert view.transfers.rows[0].recurrence.stops == "until Jul 01, 2028"
        assert len(calls) == 1, (
            f"one loan payment resolved its rule {len(calls)} times in one "
            f"build; the door resolves once and hands the value down"
        )

        # The synced column: the shape the monthly producer used to resolve a
        # second time.  Written through the real door, then counted afresh.
        monkeypatch.setattr(_reading, "resolve", real_resolve)
        reauthor_rule(
            tpl.recurrence_rule,
            replace(
                recurrence_spec(tpl.recurrence_rule),
                end_bound=EndsOnDate(on=date(2028, 7, 1)),
            ),
            _ctx(seed_user, _LOAN_TODAY).calendar(),
        )
        db.session.commit()
        assert tpl.recurrence_rule.end_date == date(2028, 7, 1), (
            "precondition: the column holds a synced FUTURE bound"
        )
        calls.clear()
        monkeypatch.setattr(_reading, "resolve", counting_resolve)

        view = recurring_view.build_view(
            [], [], [tpl], _ctx(seed_user, _LOAN_TODAY),
        )

        # One installment a month at the contract's level payment: $12,000.00
        # at 5% over 24 months, r = 0.05 / 12, 12000 * r / (1 - (1 + r) ** -24)
        # = 526.4569 -> $526.46.  The payment TRACKS the loan, so since plan
        # step X-bp (ruling plan#2693) it is priced by the loan rather than by
        # the fixture's stored $200.00 snapshot; the fixture authors the loan
        # door's monthly rule since plan step R16-b-2.
        assert view.transfers.rows[0].equivalent.monthly == Decimal("526.46"), (
            "precondition: the synced payment is still a live commitment"
        )
        assert len(calls) == 1, (
            f"a loan payment with a synced column resolved its rule "
            f"{len(calls)} times in one build; the monthly figure must derive "
            f"from the door's one reading (plan step R7d-e)"
        )


class TestTheArchivedDrawer:
    """Archived definitions get a producer, not raw ORM handed to Jinja."""

    def test_an_archived_row_carries_its_worded_cadence(
        self, seed_user, seed_periods_today,
    ):
        """The drawer shows how a definition repeated before it was archived."""
        tmpl = _create_expense(seed_user, ANNUAL, Decimal("400.00"), day_of_month=1, month_of_year=11)
        tmpl.is_active = False
        db.session.flush()

        rows = recurring_view.build_archived_rows(
            [tmpl], _ctx(seed_user),
        )

        assert len(rows) == 1
        assert rows[0].template is tmpl
        assert rows[0].recurrence.cadence == "Yearly (Nov 1)"

    def test_a_rule_less_archived_row_carries_none(
        self, seed_user, seed_periods_today,
    ):
        """A non-repeating archived definition reads "One-time" in the cell."""
        tmpl = _create_expense(seed_user, None, Decimal("400.00"))
        tmpl.is_active = False
        db.session.flush()

        rows = recurring_view.build_archived_rows(
            [tmpl], _ctx(seed_user),
        )

        assert rows[0].recurrence is None

    def test_it_preserves_the_callers_order(
        self, seed_user, seed_periods_today,
    ):
        """The drawer's own sort is the route's; the producer does not re-sort.

        Unlike the active sections, which apply the locked cost-descending
        default -- archived rows have no cost to sort by.
        """
        first = _create_expense(seed_user, None, Decimal("1.00"), name="Zed")
        second = _create_expense(seed_user, None, Decimal("900.00"), name="Abe")
        for tmpl in (first, second):
            tmpl.is_active = False
        db.session.flush()

        rows = recurring_view.build_archived_rows(
            [first, second], _ctx(seed_user),
        )

        assert [row.template.name for row in rows] == ["Zed", "Abe"]

    def test_it_walks_no_occurrences(
        self, seed_user, seed_periods_today, monkeypatch,
    ):
        """An archived definition generates nothing, so nothing is placed.

        It needs the cadence and not where rows land, so it takes the read
        door's FIRST step alone.  Computing placements and discarding them is
        the defect ledger row D26 names, one surface over.
        """
        tmpl = _create_expense(seed_user, MONTHLY, Decimal("50.00"), day_of_month=9)
        tmpl.is_active = False
        db.session.flush()

        def fail_if_called(*_args, **_kwargs):
            raise AssertionError(
                "the archived drawer walked occurrences it does not render"
            )

        monkeypatch.setattr(_reading, "occurrence_placements", fail_if_called)

        rows = recurring_view.build_archived_rows(
            [tmpl], _ctx(seed_user),
        )

        assert rows[0].recurrence.cadence == "Monthly (day 9)"


class TestNoneMeansDoesNotRepeat:
    """One sentinel, one meaning, on the value the cell reads.

    ``recurrence is None`` renders "One-time".  ``resolved_recurrence`` also
    answers ``None`` for an owner with no pay periods, and letting the two
    share a sentinel would report a quarterly bill as a one-off -- on the
    surface whose whole job is to say how definitions repeat.  The empty
    schedule is unreachable through any application path (registration
    bootstraps period 0, ``truncate_pay_periods`` always keeps the period its
    ``keep_through_period_id`` names -- plan step C3-a, which replaced the
    ordinal wire key and with it the Marshmallow floor this sentence used to
    cite -- ``reset_pay_periods`` deletes and regenerates in one transaction),
    so it is refused rather than worded.

    **The empty schedules below carry a CADENCE, and that is deliberate** (plan
    step R7a-2a).  "No pay periods" and "no stated pay cadence" are two
    different states, and this class is about the first: an owner who HAS said
    how often they are paid but whose payday list is empty.  The second is
    refused one door earlier, by a different value, with a different exception
    -- see :class:`TestAnAbsentCadenceIsRefused`.  The state is built by
    deleting the owner's paydays and keeping their schedule row
    (:func:`_empty_schedule`), since plan step R7d-d put the surface on a read
    pass that derives its own calendar; a hand-built calendar with
    ``cadence_days=None`` would trip that earlier door and report a pass for a
    claim these cases never reached.
    """

    def test_an_empty_schedule_refuses_rather_than_reading_one_time(
        self, seed_user,
    ):
        """A repeating definition is never described as non-repeating."""
        tmpl = _create_expense(seed_user, QUARTERLY, Decimal("60.00"), day_of_month=2, month_of_year=3)
        ctx = _empty_schedule(seed_user)

        with pytest.raises(RecurrenceResolutionError, match="no pay periods"):
            recurring_view.build_view([], [tmpl], [], ctx)

    def test_the_archived_drawer_refuses_it_too(self, seed_user):
        """Both row kinds reach the same discriminator."""
        tmpl = _create_expense(seed_user, MONTHLY, Decimal("25.00"), day_of_month=9)
        tmpl.is_active = False
        db.session.flush()
        ctx = _empty_schedule(seed_user)

        with pytest.raises(RecurrenceResolutionError, match="no pay periods"):
            recurring_view.build_archived_rows([tmpl], ctx)

    def test_a_rule_less_definition_still_answers_none(self, seed_user):
        """The control: the sentinel still means what it is supposed to.

        Without this, "raise whenever the resolution is absent" would pass the
        two cases above while breaking the only state ``None`` may describe.
        """
        tmpl = _create_expense(seed_user, None, Decimal("25.00"))
        ctx = _empty_schedule(seed_user)

        view = recurring_view.build_view([], [tmpl], [], ctx)

        assert view.expenses.rows[0].recurrence is None
        assert recurring_view.build_archived_rows(
            [tmpl], ctx,
        )[0].recurrence is None


class TestAnAbsentCadenceIsRefused:
    """No stated pay cadence -> no per-paycheck column (plan step R7a-2a).

    A DIFFERENT state from :class:`TestNoneMeansDoesNotRepeat`'s empty
    schedule, and refused one door earlier.  Every money figure this surface
    publishes exists in two units, and the second unit is "per paycheck" --
    which is unanswerable for an owner who has never said how often they are
    paid.  The alternative was to assume biweekly, which is precisely the
    hardcoded ``PAY_PERIODS_PER_YEAR = 26`` this step deleted: it would have
    reported a weekly-paid owner's commitments at double their per-paycheck
    cost with nothing on the page saying so.

    Unreachable through any application path -- registration writes the
    ``budget.pay_schedule`` row AND the paydays (plan step X-ad-a) -- so this
    is the control on a broken invariant, not on a user state.  *The paragraph
    here also said ``resolve_cadence`` "falls back to the last period's length
    for legacy owners"; plan step ``pay_calendar:C4-b-2`` deleted that
    inference and ``fk_pay_periods_schedule`` deleted the owner it served.*

    **Where the refusal LIVES moved at plan step ``pay_calendar:C4-d``**
    (ruling **R-PC45**).  This surface used to meet the cadence-less owner as a
    CALENDAR carrying ``cadence_days=None``, and refused when it read the
    cadence.  There is no such calendar now: ``pay_calendar.calendar_for``
    refuses that owner outright, so the surface is never handed one.

    *One case that stood here is DELETED rather than rewritten, and an
    adversarial review of that step is why.*  It asserted that ``build_view``
    refused a calendar built with ``cadence_days=None``.  The first rewrite
    made it assert that such a calendar cannot be BUILT and that
    ``calendar_for`` refuses -- two claims that touch this module not at all,
    already graded in ``test_pay_calendar_derivation.py`` and
    ``test_pay_calendar_loader.py``, sitting in a file about ``recurring_view``
    inside a class named for a property of it.  A duplicate in the wrong file
    is a test that goes stale where nobody looks for it.

    **No coverage was lost.**  The extra subject that case carried -- a
    RULE-LESS definition still reaching the section subtotal's cadence read --
    is exercised by ``test_a_rule_less_definition_still_answers_none``, which
    passes a rule-less template through ``build_view``.  What remains here is
    the one property that is still about THIS module: the archived drawer must
    not read a cadence the active sections do.
    """

    def test_the_archived_drawer_does_NOT_need_a_cadence(
        self, seed_user, seed_periods_today, monkeypatch,
    ):
        """The drawer has no money columns, so it must not read the cadence.

        **The asymmetry cannot be shown with a fixture, and an earlier draft of
        this test pretended otherwise.** It passed a normal calendar and
        asserted the drawer rendered -- which it would whether or not it read
        the cadence, so the control could not fail.  The two states that would
        distinguish it are both unconstructible: ``derive_periods`` refuses a
        non-empty payday set with ``cadence_days=None``, and an EMPTY calendar
        makes the drawer raise ``RecurrenceResolutionError`` for an unrelated
        reason (its sibling above pins that).

        So the cadence is made to REFUSE on a calendar that is otherwise
        healthy.  The drawer must still answer; ``build_view`` on the same
        calendar must not.  Both halves are asserted, because the first alone
        would pass if the property were removed from the value entirely.
        """
        tmpl = _create_expense(seed_user, MONTHLY, Decimal("25.00"), day_of_month=9)
        tmpl.is_active = False
        db.session.flush()
        ctx = _ctx(seed_user)

        def _refuse(_self):
            raise PayCalendarError("the cadence was read")

        monkeypatch.setattr(PayCalendar, "cadence", property(_refuse))

        # The drawer words each rule's cadence phrase off the schedule -- which
        # needs paydays -- and converts no money, so it must not touch this.
        rows = recurring_view.build_archived_rows([tmpl], ctx)
        assert rows[0].recurrence.cadence == "Monthly (day 9)"

        # The control on the control: the active sections DO convert, so the
        # same planted refusal must reach them.  Without this half, deleting
        # ``PayCalendar.cadence`` outright would pass the assertion above.
        tmpl.is_active = True
        db.session.flush()
        with pytest.raises(PayCalendarError, match="the cadence was read"):
            recurring_view.build_view([], [tmpl], [], ctx)


#: The price the two cases below hand a prepared row: the $10.00 their
#: template states, as ``todays_price`` would answer it.  Neither case reads
#: it; the row requires one (plan step X-bp).
_PRICE_TEN = TodaysPrice(amount=Decimal("10.00"), paycheck_cadence=None)


class TestTheValuesCannotDisagree:
    """The pairs are checked, not merely documented."""

    def test_a_prepared_row_refuses_a_rule_without_its_reading(
        self, seed_user, seed_periods_today,
    ):
        """A docstring guarantee the generated ``__init__`` does not enforce
        is what ``OccurrencePlacement.__post_init__`` exists to stop repeating.
        """
        tmpl = _create_expense(seed_user, EVERY_PERIOD, Decimal("10.00"))

        with pytest.raises(ValueError, match="rule and its reading"):
            recurring_view._PreparedRow(
                template=tmpl, price=_PRICE_TEN, monthly_full=None,
                rule=tmpl.recurrence_rule, reading=None,
            )

    def test_a_prepared_row_refuses_a_reading_without_its_rule(
        self, seed_user, seed_periods_today,
    ):
        """The other direction, so the check is a biconditional not a guard."""
        tmpl = _create_expense(seed_user, EVERY_PERIOD, Decimal("10.00"))
        reading = read_rule(
            tmpl.recurrence_rule, _calendar(seed_periods_today),
        )

        with pytest.raises(ValueError, match="rule and its reading"):
            recurring_view._PreparedRow(
                template=tmpl, price=_PRICE_TEN, monthly_full=None, rule=None,
                reading=reading,
            )
