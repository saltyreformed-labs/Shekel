"""The equity chart's date-anchored producer: fallback, date-keyed value, calendar merge."""

from datetime import date
from decimal import Decimal

from app.services import balance_at, home_equity_service, property_equity_chart
from app.services.balance_at._loan_stream import loan_timeline
from app.services.balance_at._plan import memoized_plan
from app.services.loan_loaders import load_loan_params, load_rate_changes
from app.services.balance_at._resolution import (
    contractual_schedule_from_origination,
    resolve_loan_bundle,
)
from app.services.balance_at import BalanceContext
from app.services.liability_sign import owed
from app.utils.dates import add_months
from app.utils.money import round_money
from tests._test_helpers import (
    create_loan_account,
    create_settled_transfer,
    freeze_today,
    insert_tracking_start_event,
    insert_trueup_event,
)

from ._helpers import _make_property


# ── Property equity-over-time chart (date-anchored rebuild) ─────────

_FOUR_HUNDRED_K = Decimal("400000.00")


def _series_for(prop, loan, today):
    """Return *loan*'s ``SecuredLoanSeries`` through the PRODUCTION seam.

    Calls :func:`app.services.balance_at.secured_loan_series` -- the same entry
    the property route calls -- and picks out *loan*'s series, so the producer
    tests are fed the rows production feeds it.

    It used to hand-roll the assembly (``resolve_account_loan`` + a clipped
    ``contractual_schedule_from_origination`` + ``state.current_balance``), which
    is a verbatim re-implementation of the route helper it was mirroring.  A
    fixture that re-implements the code it is testing cannot catch that code being
    wrong -- the founding lesson of this arc -- and it went stale the moment the
    assembly moved into the seam.

    *today* is taken, not re-derived: the caller holds the frozen ``today`` it
    also passes to ``build_property_equity_chart``, and building the context off an
    independent ``date.today()`` would resolve the series at one date while the
    chart was built at another -- silently, and only correct by the grace of
    ``freeze_today`` happening to patch ``balance_at._context`` too.

    Args:
        prop: The Property account the loan is secured by.
        loan: The secured loan account whose series to return.
        today: The read pass's as-of -- the SAME date the caller charts at.

    Returns:
        The loan's :class:`~app.services.balance_at.SecuredLoanSeries`.
    """
    ctx = BalanceContext.build(prop.user_id, as_of=today)
    series = balance_at.secured_loan_series(prop, ctx)
    return next(sec for sec in series if sec.account_id == loan.id)


def _expected_value(market_value, rate, month_date, today):
    """Hand-compute the compounded market value at ``month_date``.

    Mirrors ``growth_engine.span_return_rate`` exactly: the anchor grows by
    ``(1 + rate) ** (days / 365)`` where ``days`` is the INCLUSIVE span
    ``(month_date - today).days + 1``, rounded to cents.
    """
    days = Decimal(str((month_date - today).days + 1))
    factor = (Decimal("1") + rate) ** (days / Decimal("365"))
    return round_money(market_value * factor)


class TestPropertyEquityChartProducer:
    """The date-anchored producer: fallback, date-keyed value, calendar merge.

    The rebuild's H1 / H2 / H3 fixes plus the value/equity identity, asserted on
    the pure ``build_property_equity_chart`` (fed the rows the route resolves
    once).  Loans originate at a known date relative to ``today`` so the axis is
    date-robust in CI.
    """

    def test_no_loans_uses_ten_year_appreciation_fallback(self, app):
        """No secured loan at all -> the 120-month appreciation-only arc."""
        with app.app_context():
            today = date.today()
            chart = property_equity_chart.build_property_equity_chart(
                [], _FOUR_HUNDRED_K, Decimal("0.03000"), today,
            )
            assert chart.chart_state == "no_loans"
            assert chart.debt == []
            assert chart.equity == []
            assert chart.debt_tier == []
            assert chart.today_index == 0
            assert len(chart.value) == 120
            assert len(chart.labels) == 120
            assert chart.value[0] == _FOUR_HUNDRED_K
            assert chart.value[1] == _expected_value(
                _FOUR_HUNDRED_K, Decimal("0.03000"), add_months(today, 1), today,
            )

    def test_lump_sum_payoff_via_trueup_drops_the_loan_end_to_end(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A loan paid off by a LUMP SUM (a true-up, no payment rows) is dropped.

        THE regression guard for the $197,049.32 phantom, and it runs the whole
        production path: the real collateral link, the real seam entry, the real
        chart, and the real equity hero -- not a hand-built series.

        The chart's drop rule is the seam's ``is_retired`` (originated + ledger
        balance <= 0).  It is deliberately NOT ``is_paid_off``, which ALSO demands a
        confirmed payment -- a BADGING guard.  A mortgage paid off by a single lump
        sum recorded as a balance true-up has NO payment rows, so ``is_paid_off`` is
        False.  Charting on that predicate charted the loan; and since a
        zero-balance loan resolves to an EMPTY schedule, the seam's back-projection
        clip (``tracking_start is None`` -> admit everything) then handed the chart
        its ENTIRE contractual walk.  Measured before the fix:

            HERO  : total_debt $0.00        equity $400,000.00
            CHART : debt[today] $197,049.32 equity[today] $202,950.68

        Two producers, one page, $197,049.32 apart -- the exact failure this arc
        exists to end.

        NEGATIVE CONTROL: swap ``is_retired=figures.is_retired`` for
        ``figures.is_paid_off`` in ``balance_at._secured_debt`` and this test fails
        with a non-empty debt line.
        """
        # pylint: disable=import-outside-toplevel
        from app.models.loan_params import LoanParams

        with app.app_context():
            today = date.today()
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Paid Off Mortgage",
                principal=Decimal("200000.00"), term=360,
                origination_date=add_months(today, -12), payment_day=1,
            )
            loan.collateral_account_id = prop.id
            db.session.commit()
            # The lump-sum payoff, recorded the way the UI records one: a balance
            # true-up asserting $0.00.  No settled payment rows exist.
            params = db.session.query(LoanParams).filter_by(
                account_id=loan.id,
            ).one()
            insert_trueup_event(params, Decimal("0.00"))
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            figures = balance_at.loan_figures(loan, ctx)
            # The ledger says the debt is gone...
            assert balance_at.balance_at(loan, ctx, ctx.as_of) == Decimal("0.00")
            assert figures.is_retired is True
            # ...but there is no confirmed payment behind it, so it is not BADGED.
            assert figures.is_paid_off is False

            series = balance_at.secured_loan_series(prop, ctx)
            assert len(series) == 1
            assert series[0].is_retired is True

            chart = property_equity_chart.build_property_equity_chart(
                series, _FOUR_HUNDRED_K, Decimal("0.03000"), today,
            )
            # Dropped: the appreciation-only arc, no debt line at all.
            assert chart.chart_state == property_equity_chart.CHART_STATE_NO_LOANS
            assert chart.debt == []
            assert chart.equity == []
            # And the hero agrees, to the cent -- which is the whole point.
            equity = home_equity_service.resolve_home_equity(prop, ctx)
            assert equity.total_debt == Decimal("0.00")
            assert equity.equity == _FOUR_HUNDRED_K

    def test_retired_loan_dropped_regardless_of_its_debt_map(self, app):
        """A RETIRED loan is dropped even when its fold map still carries debt.

        The H1 fix in the C5 shape.  The drop keys on the seam's ``is_retired``
        predicate, NOT on the loan's data being absent: a retired loan whose fold
        map still carries balances (its pre-payoff history) is dropped all the same,
        and a property with only retired loans falls through to the 120-month
        appreciation-only arc.  Pure -- the producer is fed a hand-built series, so
        no DB or resolution is involved.

        NEGATIVE CONTROL: flip ``is_retired`` to ``False`` and this loan is charted,
        so the fallback does not fire and ``chart.debt`` is non-empty.
        """
        with app.app_context():
            today = date.today()
            retired = balance_at.SecuredLoanSeries(
                account_id=1,
                # A non-empty debt map -- the loan owed $900 this month -- so the
                # drop cannot be a side effect of the map being empty.
                month_balances={
                    (today.year, today.month): (
                        Decimal("900.00"), property_equity_chart.TIER_CONFIRMED,
                    ),
                },
                is_retired=True,
            )
            chart = property_equity_chart.build_property_equity_chart(
                [retired], _FOUR_HUNDRED_K, Decimal("0.03000"), today,
            )
            assert chart.chart_state == "no_loans"
            assert chart.debt == []
            assert len(chart.value) == 120

    def test_multi_loan_merge_is_date_aligned(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A younger loan's balance is never summed into a month before it existed.

        The H2 fix.  An old $300k loan (36 months ago) and a new $50k loan (1
        month ago) secure the property.  The axis starts at the OLD loan's first
        month, years before the new loan; at that earliest month the debt is the
        OLD loan's balance ALONE -- the new loan contributes $0.00 because it did
        not exist.  The old front-aligned merge summed the new loan in there.
        """
        with app.app_context():
            today = date.today()
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0"),
            )
            old = create_loan_account(
                seed_user, db.session, name="Old",
                principal=Decimal("300000.00"), term=360,
                origination_date=add_months(today, -36),
            )
            new = create_loan_account(
                seed_user, db.session, name="New",
                principal=Decimal("50000.00"), term=120,
                origination_date=add_months(today, -1),
            )
            old.collateral_account_id = prop.id
            new.collateral_account_id = prop.id
            db.session.commit()
            old_series = _series_for(prop, old, today)
            new_series = _series_for(prop, new, today)

            chart = property_equity_chart.build_property_equity_chart(
                [old_series, new_series], _FOUR_HUNDRED_K, Decimal("0"), today,
            )
            # Parallel series (the data contract).
            assert (
                len(chart.labels) == len(chart.value) == len(chart.debt)
                == len(chart.equity) == len(chart.debt_tier)
            )
            # The earliest axis month is the OLD loan's origination month; the new
            # loan does not span it, so its map has no entry there and the debt is
            # the old loan's fold balance ALONE.
            old_first = old_series.month_balances[min(old_series.month_balances)][0]
            new_first = new_series.month_balances[min(new_series.month_balances)][0]
            assert chart.debt[0] == old_first
            # Guard against the old front-aligned bug (new summed in at index 0).
            assert chart.debt[0] != old_first + new_first

    def test_past_dated_projected_months_hold_value_flat(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Past months hold the anchor flat -- never a fabricated compounded value.

        The H3 fix.  A loan originated 18 months ago with no confirmed payments
        gives the schedule past-dated PROJECTED rows.  The value line holds the
        $400,000 anchor flat for every month up to today (keyed on the DATE, not
        a confirmed-row count), so none of those past months gets the phantom
        value the old degenerate-span clamp produced; the line compounds only
        after today.
        """
        with app.app_context():
            today = date.today()
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Stale",
                principal=Decimal("100000.00"), term=120,
                origination_date=add_months(today, -18),
            )
            loan.collateral_account_id = prop.id
            db.session.commit()
            series = _series_for(prop, loan, today)

            chart = property_equity_chart.build_property_equity_chart(
                [series], _FOUR_HUNDRED_K, Decimal("0.03000"), today,
            )
            # There ARE past months on the axis (origination predates today).
            assert chart.today_index > 0
            # Every month up to and including today holds the anchor flat.
            assert all(
                value == _FOUR_HUNDRED_K
                for value in chart.value[:chart.today_index + 1]
            )
            # The month after today compounds at 3%/yr from today.
            first_of_month = date(today.year, today.month, 1)
            assert chart.value[chart.today_index + 1] == _expected_value(
                _FOUR_HUNDRED_K, Decimal("0.03000"),
                add_months(first_of_month, 1), today,
            )

    def test_equity_is_value_minus_debt_each_month(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Equity is the exact per-month ``value - debt`` (the internal identity)."""
        with app.app_context():
            today = date.today()
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Mtg",
                principal=Decimal("2400.00"), term=36, origination_date=today,
            )
            loan.collateral_account_id = prop.id
            db.session.commit()
            series = _series_for(prop, loan, today)

            chart = property_equity_chart.build_property_equity_chart(
                [series], _FOUR_HUNDRED_K, Decimal("0.03000"), today,
            )
            assert chart.equity == [
                chart.value[i] - chart.debt[i] for i in range(len(chart.debt))
            ]

    def test_zero_rate_holds_value_flat_with_debt(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The 0 appreciation sentinel holds value flat; the zero_rate state fires."""
        with app.app_context():
            today = date.today()
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Flat",
                principal=Decimal("2400.00"), term=24, origination_date=today,
            )
            loan.collateral_account_id = prop.id
            db.session.commit()
            series = _series_for(prop, loan, today)

            chart = property_equity_chart.build_property_equity_chart(
                [series], _FOUR_HUNDRED_K, Decimal("0"), today,
            )
            assert chart.chart_state == "zero_rate"
            assert all(value == _FOUR_HUNDRED_K for value in chart.value)

    def test_chart_reconciles_with_hero_at_last_confirmed_month(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Chart debt/equity == the equity hero at the LAST CONFIRMED month.

        Review finding M1: the reconciliation guarantee the value/equity
        identity test cannot reach.  A loan's last confirmed schedule row
        carries ``remaining_balance == current_balance``, so at that month's
        axis index the summed debt equals ``home_equity.total_debt`` and the
        equity band equals ``home_equity.equity`` to the cent -- both read from
        the SAME single resolution.  This needs REAL confirmed history: settling
        monthly payments INTO the loan books confirmed payments the resolver
        replays.  The reconciliation month is the LAST CONFIRMED month, NOT
        ``today_index`` -- a still-projected current month sits one payment below
        ``current_balance``, which is exactly why the identity test alone is not
        enough.  Today is frozen to April with the last confirmed payment in
        March, so the reconciliation month strictly PRECEDES today's month and
        the test can prove today's month does not reconcile.
        """
        freeze_today(monkeypatch, date(2026, 4, 20))
        today = date(2026, 4, 20)
        with app.app_context():
            prop = _make_property(
                db, seed_user, seed_periods, rate=Decimal("0.03000"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Mortgage",
                principal=Decimal("240000.00"), term=360,
                origination_date=date(2025, 11, 1), payment_day=1,
            )
            loan.collateral_account_id = prop.id
            # Imported mid-life: tracked from 2026-01-01 at the contract's own
            # balance then, so the months before it are the estimated
            # back-projection (ruling R-R111 reads the loan's RECORDED start;
            # an in-app loan, tracked from its origination, has none -- its
            # unpaid months read the ledger).  Until plan step
            # recurrence:R16-c-2 this loan was in-app and the stand-in (the
            # first schedule row's date) made its months before the first
            # payment "pre-tracking"; Josh approved this fixture change
            # (rule 5, 2026-09-25).
            insert_tracking_start_event(
                load_loan_params(loan.id), Decimal("239422.06"),
                date(2026, 1, 1),
            )
            db.session.commit()
            # Two confirmed monthly payments, both historical (Jan/Feb periods),
            # settled on their period starts so they are visible by the frozen
            # April today under C2's settled-date clock.
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[1], amount=Decimal("1500.00"),
                settled_on=seed_periods[1].start_date,
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[3], amount=Decimal("1500.00"),
                settled_on=seed_periods[3].start_date,
            )
            db.session.commit()

            # ONE resolution feeds both the equity hero and the chart (D1).
            resolved = resolve_loan_bundle(
                loan, BalanceContext.build(seed_user["user"].id, as_of=today),
            )
            assert resolved is not None, "configured loan must resolve"
            state = resolved.state
            confirmed = [row for row in state.schedule if row.is_confirmed]
            assert confirmed, "settled payments must produce confirmed history"
            last_confirmed = confirmed[-1]
            # The loan's balance is the seam's fold (plan step D2a deleted the
            # resolver's balance field); the reconciliation guarantee is that
            # the last confirmed schedule row IS what that folded balance owes.
            # The seam reports the balance HELD, negative when owed, since plan
            # step credit_card:CC-5-5c (ruling R-CC47), while the schedule row,
            # the chart's debt and the hero's total debt are all OWED -- so the
            # fold is read through ``owed()`` once, here, for every comparison
            # below.
            fold_balance = owed(balance_at.balance_at(
                loan, BalanceContext.build(seed_user["user"].id), today,
            ))
            assert last_confirmed.remaining_balance == fold_balance

            # The loan's series comes from the PRODUCTION seam
            # (``balance_at.secured_loan_series``), which is what the property
            # route calls.  The months from origination to the tracking start
            # are the estimated back-projection (a real, non-empty prefix
            # here); the reconciliation keys off the confirmed / fold tier.
            series = _series_for(prop, loan, today)
            assert any(
                tier == property_equity_chart.TIER_ESTIMATED
                for _balance, tier in series.month_balances.values()
            ), "precondition: this loan has pre-tracking months to estimate"
            equity = home_equity_service.compute_home_equity(
                _FOUR_HUNDRED_K, [fold_balance],
            )
            chart = property_equity_chart.build_property_equity_chart(
                [series], _FOUR_HUNDRED_K, Decimal("0.03000"), today,
            )

            # Reconcile at the LAST CONFIRMED month, found by its label.  No payment
            # settles between it and today, so the fold holds the balance
            # flat from there to today.
            index = chart.labels.index(
                last_confirmed.payment_date.strftime("%b %Y"),
            )
            assert chart.debt_tier[index] == "confirmed"
            assert chart.debt[index] == fold_balance
            assert chart.debt[index] == equity.total_debt
            assert chart.equity[index] == equity.equity
            # The last confirmed month is at/before today, so value holds the
            # flat anchor there -- equity == market_value - total_debt.
            assert chart.value[index] == _FOUR_HUNDRED_K

            # C5 closed the M1 gap: TODAY's month now reconciles too.  The fold
            # values the current month at ``ctx.as_of`` itself (not a projected
            # month end), so its debt is what the folded balance owes -- the
            # hero's total debt -- and its tier is ``confirmed``, where the
            # pre-C5 schedule-row producer read today's still-projected row one
            # payment below.
            assert index < chart.today_index
            assert chart.debt_tier[chart.today_index] == "confirmed"
            assert chart.debt[chart.today_index] == fold_balance
            assert chart.equity[chart.today_index] == equity.equity

    def test_debt_span_ends_at_the_derived_payoff(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """The axis runs to the DERIVED payoff, not the schedule's endpoint (C8d).

        The chart's span used to end at ``LoanState.payoff_date`` -- the last row
        of the contractual schedule walk.  It now ends at the same derived payoff
        the loan card's chip and the /savings cockpit show, so the axis cannot
        outlive or fall short of the payoff rendered beside it.

        The loan here is trued up $500 ABOVE its contractual schedule, so the
        fold clears it two installments LATE (the C8c extension): the two
        producers disagree, and the last charted month must be the fold's.
        """
        freeze_today(monkeypatch, date(2026, 4, 20))
        today = date(2026, 4, 20)
        with app.app_context():
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Underpaid Mortgage",
                principal=Decimal("240000.00"), term=360,
                origination_date=date(2026, 4, 1), payment_day=1,
            )
            loan.collateral_account_id = prop.id
            db.session.commit()
            insert_trueup_event(
                load_loan_params(loan.id), Decimal("240500.00"),
            )
            db.session.commit()

            ctx = BalanceContext.build(prop.user_id, as_of=today)
            derived = balance_at.loan_payoff_date(loan, ctx)
            contractual = contractual_schedule_from_origination(
                load_loan_params(loan.id), load_rate_changes(loan.id),
            )[-1].payment_date
            assert derived is not None
            assert derived > contractual, (
                "precondition: the underpayment must push the fold past the "
                "contractual date, or this test cannot tell the two apart"
            )

            series = _series_for(prop, loan, today)
            last_month = max(series.month_balances)
            assert last_month == (derived.year, derived.month), (
                f"axis ends {last_month}, expected the DERIVED payoff month "
                f"{(derived.year, derived.month)} (contractual is "
                f"{(contractual.year, contractual.month)})"
            )

    def test_a_retired_loan_spans_only_its_history(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """A retired loan (derived payoff ``None``) charts to today, not beyond.

        ``None`` is two states, and this is the one where there IS no future
        debt: the loan owes nothing, so its line is history.  Collapsing both
        ``None`` states onto the plan's horizon would fold thirty years of
        ``$0.00`` months for a loan the chart drops anyway.
        """
        freeze_today(monkeypatch, date(2026, 4, 20))
        today = date(2026, 4, 20)
        with app.app_context():
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Retired Mortgage",
                principal=Decimal("240000.00"), term=360,
                origination_date=date(2026, 1, 1), payment_day=1,
            )
            loan.collateral_account_id = prop.id
            db.session.commit()
            insert_trueup_event(load_loan_params(loan.id), Decimal("0.00"))
            db.session.commit()

            ctx = BalanceContext.build(prop.user_id, as_of=today)
            assert balance_at.loan_payoff_date(loan, ctx) is None
            series = _series_for(prop, loan, today)
            assert series.is_retired is True
            assert max(series.month_balances) == (today.year, today.month)

    def test_a_loan_that_never_clears_still_charts_its_future(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """The OTHER ``None``: a loan that never pays off keeps a forward line.

        Trued up so far above the schedule that the level payment cannot cover
        the monthly interest, the balance grows and the fold never reaches zero.
        The loan still owes money every month ahead, so the span must run out to
        the last installment the PLAN models.  Ending it at today instead would
        draw no forward debt beside a market value that keeps appreciating --
        future equity overstated by the entire balance, the finding-B-2 shape.
        """
        freeze_today(monkeypatch, date(2026, 4, 20))
        today = date(2026, 4, 20)
        with app.app_context():
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Negam Mortgage",
                principal=Decimal("240000.00"), term=360,
                origination_date=date(2026, 4, 1), payment_day=1,
            )
            loan.collateral_account_id = prop.id
            db.session.commit()
            insert_trueup_event(
                load_loan_params(loan.id), Decimal("900000.00"),
            )
            db.session.commit()

            ctx = BalanceContext.build(prop.user_id, as_of=today)
            assert balance_at.loan_payoff_date(loan, ctx) is None
            series = _series_for(prop, loan, today)
            assert series.is_retired is False
            last_month = max(series.month_balances)
            assert last_month > (today.year, today.month), (
                "a loan that never pays off must still chart its future debt; "
                "ending the span at today draws phantom debt-free equity"
            )
            # It runs to the plan's last modelled installment, and the debt is
            # still real there (growing, not zero).
            plan_end = max(
                payment.due_date
                for payment in memoized_plan(loan, ctx).payments
            )
            assert last_month == (plan_end.year, plan_end.month)
            assert series.month_balances[last_month][0] > Decimal("240000.00")

    def test_a_matured_loan_still_owing_spans_only_its_history(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """A loan whose TERM has run out while it still owes charts no future.

        The empty-plan case, and it is not a degenerate: every contractual
        installment AND the ESTIMATED tail's post-contractual extension are in
        the past, so the plan synthesizes nothing.  The loan is not retired (it
        owes $50,000, asserted TODAY) and has no derived payoff, so it takes the
        never-clears branch -- which must fall back to today rather than index
        an empty plan.

        The $50,000 is asserted today rather than on the helper's default
        (the day after origination) since plan step R16-b-2 (ruling R-R71): a
        loan's forward plan charges every contractual installment after its
        LATEST assertion, so a balance asserted in 2005 and never paid since
        would carry 255 months of standing interest -- a delinquent balloon,
        which is not what "still owing $50,000" means here.  It is stated at
        SETUP, the way the setup door records it (plan step R20: a
        ``tracking_start``); it was a true-up appended after the fact until
        then, and the two differ in label alone.
        """
        freeze_today(monkeypatch, date(2026, 4, 20))
        today = date(2026, 4, 20)
        with app.app_context():
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
            )
            # 10-year term from 2005: matured 2015, and even the 60-month
            # extension past that ran out in 2020.
            loan = create_loan_account(
                seed_user, db.session, name="Matured Balloon",
                principal=Decimal("240000.00"), term=120,
                origination_date=date(2005, 1, 1), payment_day=1,
                tracked_balance=Decimal("50000.00"), tracked_from=today,
            )
            loan.collateral_account_id = prop.id
            db.session.commit()

            ctx = BalanceContext.build(prop.user_id, as_of=today)
            empty = memoized_plan(loan, ctx)
            assert empty.payments == [], (
                "precondition: the whole term and its extension must be past, "
                "or this does not exercise the empty-plan fallback"
            )
            # Every contractual installment from 2005 is charged since plan
            # step recurrence:R16-c-2 (ruling R-R100), and today's
            # tracking-start clears them all (R-R72 part (2)); nothing is
            # charged AFTER it.  Until that step the check read the plan's own
            # charge list, which was empty; the developer approved the
            # re-expressed check (rule 5).
            assert not [
                charge for charge in loan_timeline(loan, ctx).stream.charges
                if charge.on_date > today
            ], (
                "nothing is charged after today's assertion: every "
                "installment of this term, its extension included, precedes "
                "it and is cleared by it"
            )
            figures = balance_at.loan_figures(loan, ctx)
            assert figures.payoff_date is None
            assert figures.is_retired is False
            series = _series_for(prop, loan, today)
            assert max(series.month_balances) == (today.year, today.month)

    def test_back_projection_estimated_tier_and_tracking_start_seam(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Pre-tracking months are an honest 'estimated' contractual estimate.

        Proves the (a) contractual back-projection end to end.  A loan originated
        60 months ago but tracked only from 24 months ago (with a tracking
        balance deliberately OFF the contractual curve) gets a non-empty
        ``back_projection`` for its origination..tracking-start months.  Those
        months carry the ``estimated`` tier and EXACTLY the
        ``contractual_schedule_from_origination`` balances (re-derived here to
        pin the chart's month-mapping and tiering -- the amortization math itself
        is pinned by ``test_balance_at_resolution.py``, not this test); the resolved
        region that follows -- no settled payments -- is ``projected``; and the
        tracking-start seam (the contractual balance the month before tracking vs
        the recorded opening) is shown honestly, on adjacent axis months, never
        smoothed into a reconciled curve.
        """
        with app.app_context():
            today = date.today()
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Imported",
                principal=Decimal("300000.00"), term=360,
                origination_date=add_months(today, -60), payment_day=1,
            )
            loan.collateral_account_id = prop.id
            params = load_loan_params(loan.id)
            # 260k at 24 months in is well below the ~285k contractual balance,
            # so the seam gap is unmistakable.
            tracking_start = add_months(today, -24)
            insert_tracking_start_event(
                params, Decimal("260000.00"), tracking_start,
            )
            db.session.commit()

            series = _series_for(prop, loan, today)
            assert any(
                tier == property_equity_chart.TIER_ESTIMATED
                for _balance, tier in series.month_balances.values()
            ), "a tracking-start loan must have a pre-tracking back-projection"
            chart = property_equity_chart.build_property_equity_chart(
                [series], _FOUR_HUNDRED_K, Decimal("0.03000"), today,
            )

            # The tracking start -- where the recorded ledger opens -- is the date
            # this test recorded above, the same boundary the seam clips the
            # back-projection at (the loan's recorded start, ruling R-R111).  It
            # read the resolved schedule's first row's date until plan step
            # recurrence:R16-c-2, which parts from the recorded date when today
            # is the 1st (the tracking start then lands on an installment);
            # Josh approved the re-derived boundary (rule 5, 2026-09-25).
            resolved = resolve_loan_bundle(
                loan, BalanceContext.build(seed_user["user"].id, as_of=today),
            )
            assert resolved is not None, "configured loan must resolve"

            # Re-derive the expected pre-tracking rows from the same contractual
            # producer the seam feeds in, clipped to the months before tracking
            # begins.  This pins the chart's month-mapping and tiering of those
            # rows (not the amortization math -- that is test_balance_at_resolution.py).
            contractual = contractual_schedule_from_origination(
                params, load_rate_changes(loan.id),
            )
            oracle_pre = [
                row for row in contractual if row.payment_date < tracking_start
            ]
            assert oracle_pre, "the oracle must have pre-tracking rows too"

            # The ORIGINATION month (axis index 0, min(origination, today)) reads
            # the fold's recorded opening principal tagged 'confirmed', NOT the
            # estimated back-projection: its contractual grid opens a month later,
            # so the opening -- a hard fact -- sits one 'confirmed' point before the
            # estimated run.  Pinned so the choice is deliberate, not incidental.
            assert chart.labels[0] == add_months(today, -60).strftime("%b %Y")
            assert chart.debt_tier[0] == "confirmed"
            assert chart.debt[0] == Decimal("300000.00")

            # Every pre-tracking month is 'estimated' and equals the contractual
            # balance to the cent.
            for row in oracle_pre:
                month_index = chart.labels.index(
                    row.payment_date.strftime("%b %Y"),
                )
                assert chart.debt_tier[month_index] == "estimated"
                assert chart.debt[month_index] == row.remaining_balance

            # The first tracked month is 'confirmed': the fold reads the recorded
            # $260,000 opening (this loan has no settled payments, so the balance
            # holds flat there), where the pre-C5 schedule-row producer read the
            # unconfirmed row as 'projected'.  It is the month of the first
            # installment on or after the tracking start.
            first_tracked = chart.labels.index(
                next(
                    row.payment_date for row in contractual
                    if row.payment_date >= tracking_start
                ).strftime("%b %Y"),
            )
            assert chart.debt_tier[first_tracked] == "confirmed"
            assert chart.debt[first_tracked] == Decimal("260000.00")

            # The seam sits on the month immediately before the first tracked month
            # (no gap), and the estimated contractual balance there (~$285k, pinned
            # by the loop above) differs from the recorded $260k opening -- the
            # producer shows the honest step, it does NOT reconcile it.
            seam = chart.labels.index(
                oracle_pre[-1].payment_date.strftime("%b %Y"),
            )
            assert seam == first_tracked - 1
            assert chart.debt[seam] != chart.debt[first_tracked]

    def test_producer_sums_overlapping_loans_and_takes_weakest_tier(self, app):
        """The producer unions loan months, sums debt, and keeps the weakest tier.

        A pure unit test of the presentation layer (no DB): two loans' fold maps
        are handed in directly.  The OLD loan spans two months the YOUNGER loan
        does not, so there the debt is the old loan's ALONE -- a younger loan never
        lands a balance in a month before it existed (the H2 guarantee).  Where the
        two overlap the balances sum, and the least-confident contributing tier
        wins (an estimated dollar beside a confirmed dollar reads as estimated).
        Under C5 the fold has no gaps, so there is nothing to forward-fill; the
        producer's job is this union and sum.
        """
        with app.app_context():
            today = date(2026, 6, 15)
            old = balance_at.SecuredLoanSeries(
                account_id=1,
                month_balances={
                    (2026, 4): (Decimal("300.00"), property_equity_chart.TIER_CONFIRMED),
                    (2026, 5): (Decimal("290.00"), property_equity_chart.TIER_CONFIRMED),
                    (2026, 6): (Decimal("280.00"), property_equity_chart.TIER_CONFIRMED),
                    (2026, 7): (Decimal("270.00"), property_equity_chart.TIER_PROJECTED),
                },
                is_retired=False,
            )
            young = balance_at.SecuredLoanSeries(
                account_id=2,
                month_balances={
                    (2026, 6): (Decimal("50.00"), property_equity_chart.TIER_ESTIMATED),
                    (2026, 7): (Decimal("45.00"), property_equity_chart.TIER_PROJECTED),
                },
                is_retired=False,
            )
            chart = property_equity_chart.build_property_equity_chart(
                [old, young], _FOUR_HUNDRED_K, Decimal("0"), today,
            )
            # Axis spans Apr..Jul; today (Jun) is index 2 (Apr, May, Jun).
            assert chart.today_index == 2
            # Apr, May: only the old loan exists -> its balance alone (H2).
            assert chart.debt[0] == Decimal("300.00")
            assert chart.debt_tier[0] == "confirmed"
            assert chart.debt[1] == Decimal("290.00")
            # Jun: 280 + 50 summed; weakest tier (confirmed vs estimated) wins.
            assert chart.debt[2] == Decimal("330.00")
            assert chart.debt_tier[2] == "estimated"
            # Jul: 270 + 45; both projected.
            assert chart.debt[3] == Decimal("315.00")
            assert chart.debt_tier[3] == "projected"
            # The young loan is never summed into a month before it existed.
            assert chart.debt[0] != Decimal("300.00") + Decimal("50.00")

    def test_producer_zero_fills_a_month_no_loan_spans(self, app):
        """A calendar month no outstanding loan spans reads $0.00 debt.

        The empty-month branch: two non-overlapping loans leave a gap month with no
        contributor at all.  Its debt is ``$0.00`` and its tier is styled
        ``confirmed`` up to today and ``projected`` after, purely to colour the
        zero-line segment.  Pure (no DB).
        """
        with app.app_context():
            today = date(2026, 6, 15)
            early = balance_at.SecuredLoanSeries(
                account_id=1,
                month_balances={
                    (2026, 4): (Decimal("100.00"), property_equity_chart.TIER_CONFIRMED),
                },
                is_retired=False,
            )
            late = balance_at.SecuredLoanSeries(
                account_id=2,
                month_balances={
                    (2026, 8): (Decimal("200.00"), property_equity_chart.TIER_PROJECTED),
                },
                is_retired=False,
            )
            chart = property_equity_chart.build_property_equity_chart(
                [early, late], _FOUR_HUNDRED_K, Decimal("0"), today,
            )
            # Axis Apr..Aug; today (Jun) is index 2.  May (1), Jun (2), Jul (3)
            # have no contributor.
            assert chart.debt[0] == Decimal("100.00")   # Apr, early loan
            assert chart.debt[1] == Decimal("0.00")     # May gap, <= today
            assert chart.debt_tier[1] == "confirmed"
            assert chart.debt[3] == Decimal("0.00")     # Jul gap, > today
            assert chart.debt_tier[3] == "projected"
            assert chart.debt[4] == Decimal("200.00")   # Aug, late loan
