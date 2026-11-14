"""Balance-at seam tests: a loan not yet originated, an upcoming loan, and a broken loan failing
loud.

Split from ``tests/test_services/test_balance_at.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date
from decimal import Decimal

from app.services import balance_at, liability_sign
from app.services.balance_at import BalanceContext
from app.services.balance_at._resolution import resolved_loan
from tests._ledger_reads import posted_loan_balance_at
from tests._test_helpers import (
    current_pay_period,
    last_covered_day,
)


class TestLoanNotYetOriginated:
    """An upcoming loan owes NOTHING until it originates.

    ``origination_date`` carries no not-future validator (unlike a true-up's
    ``anchor_date``), so configuring a mortgage that closes next month is a
    legitimate, reachable state -- and the developer ruled (2026-07-13) that the
    app must SUPPORT it.

    Before the origination-event fix the app reported the loan's FULL PRINCIPAL as
    owed at every pay period, for months before it closed.  The cause was not in
    the seam: ``select_latest_anchor`` picks the latest anchor BY DATE with no
    ``as_of`` filter, so the resolver seeded ``current_balance`` from an anchor
    dated in the FUTURE.  Six surfaces outside the seam read that field.

    The loan here originates 2026-04-15 -- AFTER the suite's frozen today
    (2026-03-20) and INSIDE the seeded period range -- so all four regions of its
    trajectory are assertable:

      1. before origination                 -> $0.00 (it does not exist)
      2. originated, before first payment   -> $200,000.00 (the opening balance)
      3. after the first payment            -> amortizing
      4. ``current_balance`` today          -> $0.00 (it owes nothing)

    Arithmetic (200,000 @ 5.000% / 360 months, payment day 1):
      monthly P&I = 1,073.64
      first payment 2026-05-01: interest = round(200000 * 0.05/12) = 833.33;
                                principal = 1073.64 - 833.33 = 240.31;
                                remaining = 200000.00 - 240.31 = 199,759.69
    """

    ORIGINATION = date(2026, 4, 15)
    OPENING = Decimal("200000.00")
    AFTER_FIRST_PAYMENT = Decimal("199759.69")
    ZERO = Decimal("0.00")

    def _upcoming_mortgage(self, seed_user, db_session, periods):
        """Create the mortgage that has not closed yet."""
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctTypeEnum
        from tests._test_helpers import create_loan_account

        return create_loan_account(
            seed_user, db_session, name="Closing In April",
            principal=self.OPENING, rate=Decimal("0.05000"),
            term=360, origination_date=self.ORIGINATION, payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )

    def test_scalar_owes_nothing_before_origination_then_its_opening(
        self, app, db, seed_user, seed_periods,
    ):
        """balance_at across all four regions of the trajectory.

        Every figure is what the loan OWES.  The scalar reports what it HOLDS,
        negative when owed (ruling R-CC47, plan step credit_card:CC-5-5c), so
        each read goes through ``liability_sign.owed``.
        """
        with app.app_context():
            periods = seed_periods
            acct = self._upcoming_mortgage(seed_user, db.session, periods)
            bctx = BalanceContext.build(seed_user["user"].id)
            assert bctx.as_of == date(2026, 3, 20)

            # 1. The PAST, before it exists.  The ledger has no opening posting
            #    for it -- correctly, nothing has happened -- and the projection
            #    answers the honest zero.
            assert liability_sign.owed(balance_at.balance_at(
                acct, bctx, date(2026, 2, 1))) == self.ZERO
            # ...including TODAY.  This read $200,000.00 before the fix.
            assert liability_sign.owed(balance_at.balance_at(
                acct, bctx, bctx.as_of)) == self.ZERO
            # 2. The FUTURE, still before it exists.
            assert liability_sign.owed(balance_at.balance_at(
                acct, bctx, date(2026, 4, 14))) == self.ZERO
            # 3. Originated, before the first payment: the full opening balance.
            assert liability_sign.owed(balance_at.balance_at(
                acct, bctx, date(2026, 4, 20))) == self.OPENING
            assert liability_sign.owed(balance_at.balance_at(
                acct, bctx, date(2026, 4, 30))) == self.OPENING
            # 4. After the first payment (2026-05-01): amortizing.
            assert liability_sign.owed(balance_at.balance_at(
                acct, bctx, date(2026, 5, 7))) == self.AFTER_FIRST_PAYMENT

    def test_period_map_owes_nothing_before_origination(
        self, app, db, seed_user, seed_periods,
    ):
        """The per-period map walks the same four regions as the scalar.

        Periods 0-5 have BEGUN (they start on or before the frozen today), so
        they read the ledger -- which knows nothing about this loan, and whose
        zero is TRUE here: the loan did not exist in any of them.

        The map reports what the loan HOLDS, negative when owed (ruling R-CC47,
        plan step credit_card:CC-5-5c), so each period is read through
        ``liability_sign.owed`` -- the figures below are what it owes.
        """
        with app.app_context():
            periods = seed_periods
            acct = self._upcoming_mortgage(seed_user, db.session, periods)
            bctx = BalanceContext.build(seed_user["user"].id)
            bmap = balance_at.balance_map(acct, bctx)

            # Periods 0-5: BEGUN, all before origination.  Every one read
            # $200,000.00 before the fix.
            for period in periods[:6]:
                assert period.start_date <= bctx.as_of
                assert liability_sign.owed(bmap[period.id]) == self.ZERO
            # Period 6 (2026-03-27..2026-04-09): future, still pre-origination.
            assert liability_sign.owed(bmap[periods[6].id]) == self.ZERO
            # Period 7 (2026-04-10..2026-04-23): contains the 2026-04-15
            # origination, and its end precedes the first payment (2026-05-01),
            # so the loan owes exactly its opening balance.
            assert liability_sign.owed(bmap[periods[7].id]) == self.OPENING
            # Periods 8-9: the first payment (2026-05-01) has landed.
            assert liability_sign.owed(
                bmap[periods[8].id],
            ) == self.AFTER_FIRST_PAYMENT
            assert liability_sign.owed(
                bmap[periods[9].id],
            ) == self.AFTER_FIRST_PAYMENT

    def test_current_period_agrees_with_the_hero_when_origination_is_inside_it(
        self, app, db, seed_user, seed_periods,
    ):
        """The cross-page guard: origination INSIDE the current pay period.

        This is the shape that killed the first design of the fix.  The forward
        projection is period-END keyed.  The current period runs
        2026-03-13..2026-03-26 and today is 2026-03-20, so a loan originating
        2026-03-25 sits inside it -- AFTER today but BEFORE the period ends.

        Valuing the current period at its END therefore reports the full
        $200,000.00 while the hero (``balance_at`` today) reports $0.00: one page
        contradicting itself about one loan on one day, which is the exact failure
        this arc exists to delete.  The map's current-period CLAMP -- sampling
        ``positions`` at ``min(period.end, ctx.as_of)`` -- is what prevents it,
        keeping the current period on today's value, and this test is why the
        clamp holds for EVERY loan with no exception.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctTypeEnum
        from app.services.balance_at import positions
        from tests._test_helpers import create_loan_account

        with app.app_context():
            periods = seed_periods
            acct = create_loan_account(
                seed_user, db.session, name="Closing Friday",
                principal=self.OPENING, rate=Decimal("0.05000"),
                term=360, origination_date=date(2026, 3, 25), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
            )
            bctx = BalanceContext.build(seed_user["user"].id)
            current = next(
                p for p in periods
                if p.start_date <= bctx.as_of <= last_covered_day(p)
            )
            assert current.start_date == date(2026, 3, 13)
            assert last_covered_day(current) == date(2026, 3, 26)

            hero = balance_at.balance_at(acct, bctx, bctx.as_of)
            trend = balance_at.balance_map(acct, bctx)[current.id]
            assert hero == self.ZERO
            assert trend == hero, (
                "the /savings hero and the net-worth trend's current-period "
                f"point disagree: hero={hero} trend={trend}"
            )

            # Negative control: sampling positions() at the period END -- what the
            # map would do WITHOUT the current-period clamp -- really does report
            # the full opening here (period end 2026-03-26 is after origination
            # 2026-03-25).  The clamp to ctx.as_of is load-bearing, not decorative.
            unclamped = positions(acct, bctx, [last_covered_day(current)])
            assert unclamped[last_covered_day(current)] == self.OPENING

    def test_liability_band_owes_nothing_before_origination(
        self, app, db, seed_user, seed_periods,
    ):
        """The long-horizon liability band walks the same trajectory."""
        with app.app_context():
            periods = seed_periods
            acct = self._upcoming_mortgage(seed_user, db.session, periods)
            bctx = BalanceContext.build(seed_user["user"].id)
            current = balance_at.balance_at(acct, bctx, bctx.as_of)

            owed = balance_at.liability_owed_at_dates(
                [acct], bctx,
                [date(2026, 4, 14), date(2026, 4, 20), date(2026, 5, 7)],
                {acct.id: current},
            )
            assert owed[acct.id] == [
                self.ZERO, self.OPENING, self.AFTER_FIRST_PAYMENT,
            ]

    def test_seam_reports_nothing_owed_today(
        self, app, db, seed_user, seed_periods,
    ):
        """An unclosed mortgage owes $0.00 on every surface -- the fold's rule.

        The loan detail page's "current principal", the payoff and refinance
        calculators, home equity, and the property equity chart all read the
        seam's folded balance (plan step D2a deleted the resolver's
        ``current_balance`` field; the fold answers ``0.00`` for a date before
        any event -- the honest fold of an empty prefix).  The pre-D2a resolver
        guard existed because its replay reported $200,000.00 for a mortgage
        that had not closed; the fold needs no guard, since a future-dated
        origination event simply has not happened yet.

        The SCHEDULE must survive: it is what the projection walks once
        the loan closes, and filtering the anchor out of the old anchor replay
        (``_replay_from_anchor``, deleted with walk 3 at plan step recurrence:R16-e; rather than the
        balance following the events) would have collapsed it to nothing.
        """
        with app.app_context():
            periods = seed_periods
            acct = self._upcoming_mortgage(seed_user, db.session, periods)
            bctx = BalanceContext.build(seed_user["user"].id)
            resolved = resolved_loan(acct, bctx)

            assert balance_at.balance_at(acct, bctx, bctx.as_of) == self.ZERO
            # The schedule is intact: 360 contractual rows from the first payment.
            assert len(resolved.state.schedule) == 360
            assert resolved.state.schedule[0].payment_date == date(2026, 5, 1)
            assert (resolved.state.schedule[0].remaining_balance
                    == self.AFTER_FIRST_PAYMENT)

    def test_the_day_it_closes_the_seam_answers_without_a_re_sync(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """B-1: the loan must not break simply because its closing date arrived.

        The loan is configured on 2026-03-20 (the suite's frozen today) to close
        on 2026-04-15.  Then the CLOCK MOVES to 2026-05-07 and nothing else
        happens -- which is production: no chokepoint fires because a date
        arrived, so no sync re-runs between configuring the loan and reading it.

        Before A3 the write walk dropped any anchor dated after its as-of, so the
        params-create sync posted NO OPENING for this loan.  The moment the clock
        passed 2026-04-15 the seam considered it originated, found no opening, and
        raised the fail-loud error -- taking the loan card, /savings, the net-worth
        trend, the Horizon and the property equity chart down with a 500, fired by
        the clock alone, with the user's data untouched and correct.  (A3 posts the
        opening, so no raise fires here now; and since the seam folds a missing
        opening from source, steps C3b1/C3b3, that read-outage class is retired
        entirely.)

        NEGATIVE CONTROL: restore the ``anchor.anchor_date <= as_of`` filter --
        it would now go in ``loan_ledger.loan_event_stream``, where the reset
        events are built (it was ``merge_anchor_and_payment_events`` until plan
        step X-au-g-2c-3b-2 split the mapping from the ordering) -- and both
        asserts below raise.

        $200,000.00 owed, not $199,759.69: with the clock at 2026-05-07 the loan
        has originated, so the 2026-05-01 installment is now a PAST due date -- and
        with NO payment record behind it, an installment nobody paid pays nothing
        down (finding B-9 / plan D1).  The map's last period is future, but its
        window holds no future installment WITH a record, so the balance holds at
        the opening rather than amortizing a payment that never happened.  (In the
        sibling tests the clock stays at 2026-03-20, where the whole schedule is
        still ahead, so every installment is a projected ESTIMATED slot and the
        05-01 one DOES pay down -- hence ``AFTER_FIRST_PAYMENT`` there.)

        Both entries report what the loan HOLDS, negative when owed (ruling
        R-CC47, plan step credit_card:CC-5-5c), so each is read through
        ``liability_sign.owed`` to meet the $200,000.00 it owes.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import freeze_today

        with app.app_context():
            periods = seed_periods
            acct = self._upcoming_mortgage(seed_user, db.session, periods)

            freeze_today(monkeypatch, date(2026, 5, 7))
            bctx = BalanceContext.build(seed_user["user"].id)
            assert bctx.as_of == date(2026, 5, 7)

            assert liability_sign.owed(
                balance_at.balance_at(acct, bctx, bctx.as_of),
            ) == self.OPENING
            assert liability_sign.owed(balance_at.balance_map(acct, bctx)[
                periods[-1].id
            ]) == self.OPENING


class TestUpcomingLoanDoesNotCorruptTheSurfaces:
    """The consumers that read a $0.00 balance as "this debt is gone".

    ``balance_at`` correctly answers ``$0.00`` for a loan that has not been
    borrowed yet.  Three surfaces then read that zero as "repaid" and reported the
    OPPOSITE of the truth.  All three were found by an adversarial review of the
    origination fix, which CREATED them -- the pre-fix code reported the loan's
    full principal as owed, which was wrong in a different direction and happened
    not to trip these particular consumers.

    ``LoanFigures.is_originated`` is the one fact that separates "owes nothing"
    from "has no debt ahead of it", and each test below is the negative control for
    one consumer that needs it.
    """

    ORIGINATION = date(2026, 4, 15)
    MORTGAGE = Decimal("200000.00")
    AUTO = Decimal("100000.00")

    def _both_loans(self, seed_user, db_session, periods):
        """An unclosed mortgage beside a real, never-paid auto loan."""
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctTypeEnum
        from tests._test_helpers import create_loan_account

        auto = create_loan_account(
            seed_user, db_session, name="Auto",
            principal=self.AUTO, rate=Decimal("0.06000"),
            term=60, origination_date=date(2026, 1, 5), payment_day=5,
            account_type=AcctTypeEnum.AUTO_LOAN,
        )
        mortgage = create_loan_account(
            seed_user, db_session, name="Closing In April",
            principal=self.MORTGAGE, rate=Decimal("0.05000"),
            term=360, origination_date=self.ORIGINATION, payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        return auto, mortgage

    def test_debt_track_does_not_count_an_unborrowed_loan_as_repaid(
        self, app, db, seed_user, seed_periods,
    ):
        """The dashboard debt marker: 0% repaid, not 66.67%.

        ``_compute_principal_paid_fraction`` is an ALL-LOANS-EVER basis: every loan
        contributes its ``original_principal`` to the denominator, and a loan owing
        $0.00 therefore counts as fully repaid.  An unclosed $200,000 mortgage owes
        $0.00, so it landed in the denominator with its whole principal in the
        "paid" portion: beside a never-paid $100,000 auto loan the marker read
        200000/300000 = **66.67% of principal repaid** for a borrower who had
        repaid nothing.

        It also broke the marker's one design invariant -- monotonicity.  On
        closing day the mortgage's balance steps $0 -> $200,000 and the fraction
        would COLLAPSE from 66.67% to 0%.

        **It asserts through the PRODUCTION producer, which is finding B-17's
        repair** (plan step X-h).  It used to hand-build the per-account dict
        ``_compute_principal_paid_fraction`` reads and assert on that, so the
        builder that publishes the dict in production
        (``_projections._project_one_account``) never ran: change what IT
        publishes and the test stayed green, proving where the value comes FROM
        while never proving production puts it there.  ``compute_debt_summary``
        is the entry the budget dashboard's tracks section actually calls, and
        it runs the whole chain --
        load core data, load params, project every account through the real
        builder, then reduce.  The firing control this repair was shown against:
        forcing ``is_originated=True`` into the builder's published figures makes
        this read ``0.666...`` and fail, where the hand-built version could not
        see the change at all.

        The entry it names changed at plan step X-u (finding N-109), which
        deleted the ``compute_debt_principal_progress`` producer this read
        through and made the fraction a ``DebtSummary`` field; the chain it
        exercises is the same one, and the repair's whole point -- that the
        production builder RUNS -- is unaffected.
        """
        # pylint: disable=import-outside-toplevel
        from app.services import savings_dashboard_service

        with app.app_context():
            periods = seed_periods
            auto, mortgage = self._both_loans(seed_user, db.session, periods)
            bctx = BalanceContext.build(seed_user["user"].id)

            # The fixture really is in the hazardous state: an unclosed mortgage
            # owing $0.00 that is neither retired nor paid off, beside a real
            # originated loan.  Read from the seam directly -- these are premises
            # about the FIXTURE, not about the dict under test.
            assert balance_at.balance_at(mortgage, bctx, bctx.as_of) == Decimal(
                "0.00",
            )
            mortgage_figures = balance_at.loan_figures(mortgage, bctx)
            assert mortgage_figures.is_retired is False
            assert mortgage_figures.is_paid_off is False
            assert mortgage_figures.terms.is_originated is False
            assert balance_at.loan_figures(auto, bctx).terms.is_originated is True
            # Non-tautological denominator: the two principals differ, so a
            # marker that counted the mortgage would read 2/3, not 0.
            assert resolved_loan(auto, bctx).params.original_principal == self.AUTO

            summary = savings_dashboard_service.compute_debt_summary(
                BalanceContext.build(seed_user["user"].id),
            )
            # The auto loan is originated and never paid: 0 of 100,000 repaid.
            # The mortgage is in NEITHER sum -- it has not been borrowed.
            assert summary.principal_paid_fraction == Decimal("0")

    def test_property_chart_keeps_a_mortgage_that_closes_next_month(
        self, app, db, seed_user, seed_periods,
    ):
        """The equity chart: the upcoming mortgage is charted, not dropped.

        The chart dropped any loan whose ``current_balance <= 0`` -- a test whose
        INTENT is "retired", and which an unborrowed loan satisfies.  So a $200,000
        mortgage closing in 26 days vanished, the no-outstanding-debt fallback
        fired, and the page drew ten years of debt-free equity on a house that is
        about to carry a mortgage.

        Today's $0.00 debt is right.  The chart is a FORWARD projection, and it was
        omitting a real liability for its entire horizon.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctTypeEnum
        from app.services import balance_at, property_equity_chart
        from tests._test_helpers import create_account_of_type

        with app.app_context():
            periods = seed_periods
            _auto, mortgage = self._both_loans(seed_user, db.session, periods)
            # The mortgage is secured BY a Property, as production's is: the seam
            # walks ``property_account.secured_loans``, so the fixture has to build
            # the real collateral link rather than hand the producer a list.
            house = create_account_of_type(
                seed_user, db.session, AcctTypeEnum.PROPERTY.value, "House",
                anchor_balance=Decimal("400000.00"),
            )
            db.session.flush()
            mortgage.collateral_account_id = house.id
            db.session.commit()

            bctx = BalanceContext.build(seed_user["user"].id)
            series = balance_at.secured_loan_series(house, bctx)
            assert len(series) == 1
            loan = series[0]
            assert loan.account_id == mortgage.id
            # It owes nothing today -- and it is emphatically NOT done.  The seam's
            # ONE predicate carries the origination guard; the series no longer
            # carries a balance for the chart to re-derive "retired" from.
            assert loan.is_retired is False
            assert balance_at.balance_at(
                mortgage, bctx, bctx.as_of,
            ) == Decimal("0.00")

            chart = property_equity_chart.build_property_equity_chart(
                series, Decimal("400000.00"), Decimal("0.03000"), bctx.as_of,
            )
            assert chart.chart_state != property_equity_chart.CHART_STATE_NO_LOANS
            # The mortgage really is drawn: the debt line reaches its principal
            # once the loan closes.
            assert max(chart.debt) >= self.MORTGAGE - Decimal("500.00")


class TestBrokenLoanFailsLoud:
    """A configured, ORIGINATED loan whose POSTING ledger is missing.

    The seam used to fall back to the loan's amortization SCHEDULE when the genesis
    ledger could not answer for a past date.  A schedule row is a payment the
    borrower was SUPPOSED to make, not one they did, so the fallback quietly paid
    the debt down with money that never moved: a $240,000 loan originated 18 months
    ago and never paid read as $236,544.21 owed by the per-period map, while the
    scalar said $240,000 -- two producers, one loan, one day, $3,455.79 apart.  A
    wrong balance that looks plausible is worse than a page that fails, so both
    producers were briefly made to RAISE rather than walk the schedule.

    **Neither raises now -- both FOLD the loan's SOURCE facts (steps C3b1/C3b3).**
    The scalar ``balance_at.balance_at`` (C3b1) and the per-period map
    ``balance_at.balance_map`` (C3b3) both read
    :func:`~app.services.balance_at.positions`, which folds the loan's origination
    anchor and settled shadows -- neither of which lives in the posting ledger --
    so a missing OPENING posting is a repairable cache inconsistency (plan step
    E1), not a read-time outage (B-8).  They answer the CORRECT balance the
    schedule-walk fallback could not, without the posting cache; the fail-loud
    ``LoanLedgerNotOpenedError`` is retired.  The class name is kept for the B-21
    finding its cash-degrade value assertion pins.

    Replaces ``TestUnpaidScheduleRowsNeverReduceTheDebt``, which pinned the
    behaviour of the fallback this deletes.
    """

    def _broken_loan(
        self, seed_user, db_session, periods, origination_date=date(2024, 9, 1),
    ):
        """A configured loan whose genesis POSTING ledger has been removed.

        A case that settles a payment passes the month before that payment's
        installment as *origination_date*: every contractual installment from
        origination is charged since plan step recurrence:R16-c-2, so the
        2024-09-01 default would have the payment clear the months before it
        (ruling R-R103).
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctTypeEnum
        from tests._test_helpers import clear_loan_ledger, create_loan_account

        acct = create_loan_account(
            seed_user, db_session, name="Broken",
            principal=Decimal("240000.00"), rate=Decimal("0.06000"),
            term=360, origination_date=origination_date, payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        # The ONE way to build a ledger-less loan: production cannot make one.
        # It clears only the POSTINGS; the source facts (LoanParams + shadows) that
        # the fold reads are untouched.
        clear_loan_ledger(acct.id)
        return acct

    def test_scalar_folds_source_events_when_the_posting_ledger_is_missing(
        self, app, db, seed_user, seed_periods,
    ):
        """balance_at answers a broken loan from the fold, not a raise (step C3b).

        The $240,000 loan originated 2024-09-01 and never paid; its posting ledger
        is cleared.  The scalar folds the loan's SOURCE facts -- the synthesized
        origination anchor ($240,000) plus its (empty) settled shadows -- and the
        loan owes $240,000.00, flat: the honest balance of a loan borrowed and
        never paid down.  The missing posting is a repairable cache miss (plan E1),
        not the read-time outage the old fail-loud raised, and NOT the
        schedule-walk's phantom $236,544.21 paid down by installments never made.

        The scalar reports what the loan HOLDS, negative when owed (ruling
        R-CC47, plan step credit_card:CC-5-5c), so it is read through
        ``liability_sign.owed``.
        """
        with app.app_context():
            periods = seed_periods
            acct = self._broken_loan(seed_user, db.session, periods)
            bctx = BalanceContext.build(seed_user["user"].id)

            # Originated 2024-09-01, no payments: the fold holds the origination
            # principal flat -- no debt paid, because no cash moved.
            assert liability_sign.owed(
                balance_at.balance_at(acct, bctx, bctx.as_of),
            ) == Decimal("240000.00")

    def test_map_folds_source_events_when_the_posting_ledger_is_missing(
        self, app, db, seed_user, seed_periods,
    ):
        """balance_map on a broken loan folds from source, like the scalar (C3b3).

        The per-period map cut over to :func:`~app.services.balance_at.positions`
        at step C3b3, so it no longer raises for a broken loan -- it folds the same
        SOURCE facts the scalar does.  Originated 2024-09-01, never paid: every
        begun period owes the origination principal ($240,000.00) flat, no debt
        paid because no cash moved.  Same E1 repairable-cache decision as C3b1's
        scalar.  The map reports what the loan HOLDS, negative when owed (ruling
        R-CC47, plan step credit_card:CC-5-5c), so it is read through
        ``liability_sign.owed``.
        """
        with app.app_context():
            periods = seed_periods
            acct = self._broken_loan(seed_user, db.session, periods)
            bctx = BalanceContext.build(seed_user["user"].id)

            result = balance_at.balance_map(acct, bctx)
            begun = [p for p in periods if p.start_date <= bctx.as_of]
            assert begun, "expected a begun period"
            # No payment ever recorded -> the origination principal owed flat,
            # the same $240,000.00 the scalar folds (not a fail-loud raise).
            assert liability_sign.owed(
                result[begun[-1].id],
            ) == Decimal("240000.00")

    def test_broken_loan_figures_follow_the_fold_not_the_replay(
        self, app, db, seed_user, seed_periods,
    ):
        """A broken loan's is_retired / payoff read the FOLD (plan step D2a).

        The control for the D2a re-route: with the posting ledger cleared, the
        pre-D2a ``LoanState.current_balance`` fell back to the money-blind
        anchor replay (it advances one SCHEDULED step per confirmed payment and
        discards the cash), while every displayed balance already folded the
        SOURCE facts -- one loan, one page, two derivations.  D2a deleted the
        field and pointed the seam's last two readers (the ``is_retired``
        predicate and the forward projection's seed) at the fold.

        Arithmetic, hand-computed.  $240,000 at 6% for 360 months (scheduled
        P&I 1,438.92); ONE settled payment of $241,200.00 cash:

            interest  = 240000.00 * 0.06/12          =   1,200.00
            principal = 241200.00 - 1200.00          = 240,000.00
            fold      = 240000.00 - 240000.00        =       0.00  (paid off)

        The replay instead advances one scheduled step:

            principal = 1438.92 - 1200.00            =     238.92
            replay    = 240000.00 - 238.92           = 239,761.08

        So under the old reader this loan reads as owing $239,761.08 --
        ``is_retired`` False and a REAL payoff date -- while its page shows
        $0.00.  Under D2a both follow the fold: retired, paid off, no forward
        payoff to date.  The fold's hand-worked $0.00 is the control's teeth:
        the replay's $239,761.08 is a different figure.  The replay's own
        balance was also asserted in-test until plan task plan#3393 deleted its
        suite assembly (ruling plan#3383).
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import (
            clear_loan_ledger, create_settled_transfer,
        )

        with app.app_context():
            periods = seed_periods
            # Originated the month before the payment's 2026-02-01 installment
            # and settled on its period's first day, so nothing is left unpaid
            # ahead of it (ruling R-R103; it was originated 2024-09-01 and
            # settled 2024-10-01 until plan step recurrence:R16-c-2 began
            # charging every installment from origination).
            acct = self._broken_loan(
                seed_user, db.session, periods,
                origination_date=date(2026, 1, 1),
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], acct,
                periods[1], amount=Decimal("241200.00"),
                settled_on=periods[1].start_date,
            )
            db.session.commit()
            # Re-break the cache: settling re-synced the loan's postings.
            clear_loan_ledger(acct.id)
            db.session.commit()

            bctx = BalanceContext.build(seed_user["user"].id)
            # The fold: the whole cash paid the loan off.
            assert balance_at.balance_at(acct, bctx, bctx.as_of) == (
                Decimal("0.00")
            )
            figures = balance_at.loan_figures(acct, bctx)
            assert figures is not None
            # Both re-routed readers follow the fold, not the $239,761.08 replay:
            # retired (fold <= 0) with a confirmed payment behind it, and no
            # forward payoff left to date (the seed folds to 0.00).
            assert figures.is_retired is True
            assert figures.is_paid_off is True
            assert figures.payoff_date is None

    def test_an_amortizing_account_with_no_loan_params_does_NOT_raise(
        self, app, db, seed_user, seed_periods,
    ):
        """A Mortgage account whose loan terms were never filled in routes to CASH.

        Two clicks in the UI produce it, and it has no OPENING posting either -- so
        the ledger answers ``None`` for it exactly as a broken loan does.  What
        keeps them apart is ORDER: the seam resolves the loan BEFORE reading the
        ledger, and no schedule means "not a configured loan", not "corrupt".

        Without that ordering the fail-loud raise would 500 the /savings page for a
        user who did nothing wrong.

        The account is seeded owing $150,000.00, which it HOLDS as
        ``-150,000.00`` (ruling R-CC47; every door that takes a liability's
        balance stores the held sign, ruling R-CC52, plan step
        credit_card:CC-5-5b).  It was seeded ``+150,000.00`` until plan step
        credit_card:CC-5-5c, which under the one sign is a $150,000 CREDIT --
        the opposite of the debt the assertions below describe.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_account_of_type

        with app.app_context():
            acct = create_account_of_type(
                seed_user, db.session, "Mortgage", "Terms Never Entered",
                anchor_balance=Decimal("-150000.00"),
            )
            db.session.commit()
            bctx = BalanceContext.build(seed_user["user"].id)

            # It really is in the hazardous state: the ledger cannot answer for it.
            # pylint: disable=import-outside-toplevel
            assert posted_loan_balance_at(
                acct.id, bctx.scenario.id, bctx.as_of) is None

            # And it degrades to the cash producer instead of raising -- pinned
            # by VALUE, not by ``is not None``.  The account holds a
            # -$150,000.00 anchor and no transactions, so the cash producer
            # holds exactly the anchor and the account owes $150,000.00 (the
            # held figure through ``liability_sign.owed``, ruling R-CC47);
            # asserting only non-None would have passed on any number the
            # fallback invented, including a $0.00 that would render this
            # Mortgage debt-free (B-21).
            assert liability_sign.owed(balance_at.balance_at(
                acct, bctx, bctx.as_of,
            )) == Decimal("150000.00")

            # The per-period MAP degrades identically (C3b3 hazard 1): the seam's
            # map dispatch reads ``_resolution.configured_loan``, which is None for
            # an unconfigured loan, so it falls through to the cash producer
            # rather than reaching positions()'s fail-loud for a schedule-less
            # loan.  Pinned by value at the current period (the -$150,000.00
            # anchor held flat, which owes $150,000.00).
            current = current_pay_period(seed_user["user"].id)
            loan_map = balance_at.balance_map(acct, bctx)
            assert loan_map is not None
            assert liability_sign.owed(
                loan_map[current.id],
            ) == Decimal("150000.00")
