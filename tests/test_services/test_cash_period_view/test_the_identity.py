"""The period identity holds everywhere, through a schedule hole, on the basis it was valued on."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.services.balance_at import BalanceContext
from app.services.balance_at._cash_fold import assembled_fold
from app.services.balance_at._cash_periods import CashPeriodFigures, period_view_of
from app.services.pay_calendar import PayCalendarError, PeriodWindow
from tests._test_helpers import NO_TAX_LAW
from tests._test_helpers import (
    add_entry,
    add_txn,
    append_balance_assertion,
    create_envelope_txn,
    create_settled_cash_transaction,
    derived_span,
    last_covered_day,
    period_window,
    read_pass,
    reassert_balance_on,
    restate_account_opening,
)
from tests.test_services.test_cash_fold import _instant

from ._helpers import _BOOKS_OPEN_ON, _EARLY_AS_OF, _view, _identity_holds


class TestTheIdentityHoldsOnEveryPeriod:
    """``balance(p.end) - balance(p.start - 1d) == net + timing + book``.

    Asserted with both sides computed independently -- the left from the X-b
    fold, the right from the view's grouping -- over EVERY period of a shape
    carrying all four components at once.
    """

    def _rich_shape(self, seed_user, seed_periods):
        """Build a shape triggering every component the remainder can hold.

        A record dated between the books and the first ASSERTION (which is
        what a "pre-opening record" became at plan step X-f3c-2b -- ruling
        R-HG makes anything on or before the books unstorable), an in-column
        settle, an out-of-column settle, a mid-history true-up, an
        entries-carrying envelope, an overdue plan the clamp moves forward,
        and ordinary future rows.

        Returns:
            The ``as_of`` the caller reads at.
        """
        account = seed_user["account"]
        as_of = date(2026, 4, 2)
        restate_account_opening(db.session, account, _BOOKS_OPEN_ON)
        # The FIRST assertion of the shape, on 2026-01-05 -- written rather
        # than re-stamped onto the origination (plan step X-f3c-2c).  Its own
        # correction is what makes period 0's ``book_vs_bank`` non-zero, which
        # is half of what this shape exists to exercise.
        reassert_balance_on(db.session, account, _instant(2026, 1, 5))
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[0], Decimal("400.00"),
            settled_on=date(2026, 1, 3), name="pre-assertion spend",
        )
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[2], Decimal("1800.00"),
            is_income=True, settled_on=date(2026, 2, 3), name="paycheck",
        )
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[3], Decimal("300.00"),
            settled_on=date(2026, 3, 20), name="late rent",
        )
        append_balance_assertion(
            db.session, account, Decimal("1500.00"),
            _instant(2026, 3, 1),
        )
        envelope = create_envelope_txn(
            seed_user, db.session, seed_periods[6], "Groceries",
            Decimal("200.00"),
        )
        envelope.due_date = date(2026, 4, 5)
        add_entry(
            db.session, seed_user, envelope, Decimal("60.00"), date(2026, 4, 1),
        )
        add_txn(
            db.session, seed_user, seed_periods[5], "overdue bill", "50.00",
            due_date=date(2026, 3, 20),
        )
        add_txn(
            db.session, seed_user, seed_periods[8], "future paycheck", "1500.00",
            is_income=True, due_date=date(2026, 4, 24),
        )
        db.session.commit()
        return as_of

    def test_every_period_reconciles_against_the_independent_fold(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """All ten periods, not a sample of them.

        The shape carries a record between the books and the first assertion, a
        settle inside its own column, a settle two columns late, a true-up, an
        entries-aware envelope and a clamped overdue bill, so no single
        component can carry the whole run.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        as_of = self._rich_shape(seed_user, seed_periods)

        rows = _identity_holds(account, scenario, seed_periods, as_of=as_of)
        assert len(rows) == 10  # the loop is not vacuous
        for period, balance_delta, explained in rows:
            assert balance_delta == explained, (
                f"period {period.period_index} "
                f"({period.start_date}..{period.end_date}) "
                f"moved {balance_delta} but its rows explain {explained}"
            )

    def test_the_shape_actually_exercises_the_remainder(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Non-vacuity: the identity above is not ten copies of ``0 == 0``.

        Four of the ten periods must carry a non-zero remainder, each
        hand-computed from a different component.  Without this a view that
        returned zeros everywhere would satisfy the identity perfectly.

        **Each figure is asserted on the row its CAUSE belongs to** (ruling
        R-DH (f)), which is what makes this shape discriminating rather than
        merely non-zero: the run carries three timing components and one
        assertion, and the split is the only form in which those are told apart.

        * period 3 -- timing ``+$300.00``: it BUDGETS the late rent, which moved
          elsewhere.  No assertion touches it, so ``book_vs_bank`` is ``$0.00``.
        * period 4 -- book-vs-bank ``-$1,300.00``: the true-up, which no
          transaction row can explain.  The records had walked the account to
          ``1000.00 + 1800.00 = $2,800.00`` by 2026-03-01, and the user
          asserted ``$1,500.00``, so the correction is ``1500 - 2800``.  Note it
          is NOT ``1500 - 1000``: an assertion's correction is measured against
          the RECORDS, which is the whole reason this row exists.  Nothing
          settled or was budgeted out of column here, so ``period_timing`` is
          ``$0.00`` -- the one period in the run where the two rows disagree
          about which is non-zero, and therefore the one that would catch a
          producer that had booked them to the wrong halves.
        * period 5 -- timing ``-$250.00``: the late rent MOVED here
          (``-$300.00``, and it is budgeted two columns back) while the
          ``$50.00`` overdue bill is budgeted here and moves elsewhere
          (``+$50.00``).
        * period 6 -- timing ``-$50.00``: the overdue bill LANDS here on ruling
          R-G's clamp (2026-04-03), where nothing budgets it.  The Groceries
          envelope beside it is budgeted AND lands in this column, so it
          contributes nothing to either remainder.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        as_of = self._rich_shape(seed_user, seed_periods)

        figures = _view(account, scenario, seed_periods, as_of=as_of)
        timing = {
            derived_span(period).period_index: figures[period.id].period_timing
            for period in seed_periods
        }
        book = {
            derived_span(period).period_index: figures[period.id].book_vs_bank
            for period in seed_periods
        }
        assert timing[3] == Decimal("300.00")
        assert timing[4] == Decimal("0.00")
        assert timing[5] == Decimal("-250.00")
        assert timing[6] == Decimal("-50.00")
        # TWO non-zero book-vs-bank columns, and the pair is the point since
        # plan step X-f3c-2a: period 4 holds the true-up, and period 0 holds the
        # FIRST assertion's own correction -- which this shape makes non-zero
        # because ``_rich_shape`` settles rows before the books opened.  The
        # opening used to be excluded from this row outright (ruling R-I moved
        # its whole delta into the fold's seed), so the run read a single
        # non-zero column.
        assert book[4] == Decimal("-1300.00")
        # Hand-computed, on the row its CAUSE belongs to like every other
        # figure here: the books open at $1,000.00 on 2025-12-31, the $400.00
        # spend of 2026-01-03 walks the records to $600.00, and the opening
        # assertion on 01-05 declares $1,000.00 -- a +$400.00 correction, which
        # is what ``book_vs_bank`` holds.  It nets against period 0's own
        # -$400.00 expense, which is why the identity still closes at $0.00.
        assert book[0] == Decimal("400.00")
        assert {index for index, value in book.items() if value} == {0, 4}
        assert len({figures[p.id].balance for p in seed_periods}) > 1

    def test_a_gapped_window_is_REFUSED_rather_than_reported_over(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The NEGATIVE CONTROL for plan finding **P32**, shown firing.

        This assertion is the INVERSE of the one it replaced at plan step
        C2-c, and the reversal is the ruling.  The old test reported periods
        0 and 2 only -- a window with period 1 missing -- and asserted that
        each column still reconciled against its OWN boundaries, which it did.
        What it could not assert, because it is not true, is that the BALANCE
        COLUMN a reader scans down adds up: in its own figures the balance fell
        ``$1,000.00`` to ``$750.00`` between the two reported periods while
        every explanatory row on the second read ``$0.00``.  A user reading
        that screen sees ``$250.00`` leave with nothing on the page saying so.

        So the window type refuses the shape instead of the view reporting over
        it.  No calendar view can build one -- derived periods TILE, so every
        slice of them is a contiguous run -- which is why this reaches for the
        window constructor directly: the state has to be assembled by hand to
        be shown refused at all.

        The legitimate half of what the old test proved SURVIVES, in
        :meth:`test_a_settle_day_past_the_window_keeps_every_column_exact`: a
        settle day outside every reported span belongs to no column, and off
        the TOP of a contiguous window that cancels on both sides of the
        identity.
        """
        with pytest.raises(PayCalendarError) as excinfo:
            period_window([seed_periods[0], seed_periods[2]])

        assert "unbroken span" in str(excinfo.value)
        # The message names the hole it found, not just that there was one.
        assert "14 day(s) in no column" in str(excinfo.value)

    def test_the_producer_given_a_REVERSED_window_answers_identically(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Ordering is DERIVED at construction, so a caller cannot state it wrong.

        The other half of the window type's contract (plan finding **P24**).
        ``containing`` BISECTS, so an out-of-order window used to answer
        containment wrongly and SILENTLY -- given two periods newest-first it
        missed the day the first covers and answered correctly for the second
        by accident.  A window is identified by its period SET, so the
        constructor sorts and there is nothing left to get wrong.

        **The reversed tuple is handed to the CONSTRUCTOR directly, and the
        first draft of this test did not do that.**  It reversed the list it
        gave ``period_window``, a helper which selects out of
        ``calendar.saved()`` by SET membership -- so the caller's order was
        discarded before the type ever saw it and the assertion was a
        tautology.  An adversarial code review of C2-c proved it by mutation:
        deleting the sort left the test passing.  It fails now.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[0], Decimal("250.00"),
            settled_on=date(2026, 1, 6), name="settled in its own period",
        )
        db.session.commit()

        forwards = period_window(seed_periods)
        backwards = PeriodWindow(periods=tuple(reversed(forwards.periods)))
        folded = assembled_fold(
            account, read_pass(account, scenario, _EARLY_AS_OF),
        )

        assert period_view_of(folded, forwards).columns == period_view_of(
            folded, backwards,
        ).columns
        # And it is not vacuous: the row IS placed in a column, on its own
        # settle day, by a search that a wrong order answers wrongly.
        columns = period_view_of(folded, backwards).columns
        assert columns[seed_periods[0].id].period_timing == Decimal("0.00")
        assert columns[seed_periods[0].id].net == Decimal("-250.00")

    def test_a_settle_day_past_the_window_keeps_every_column_exact(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A day off the TOP of the schedule breaks nothing, and this is why.

        **The measurement that deleted pay_calendar C3-b's coverage rule**
        (developer ruling 2026-08-11).  That rule refused any schedule write
        which moved a day from covered to uncovered underneath a settled row,
        on the claim that stranding one reproduces ``balance:N-128``.  It does
        not, and the shape below is the one a truncate actually leaves: a
        CONTIGUOUS prefix of the schedule, with a settled row filed inside it
        whose money moved after the prefix ends.

        Reports periods 0-2 (2026-01-02 to 2026-02-12) with a ``$250.00``
        expense budgeted to period 2 and settled 2026-03-20 -- past the last
        reported ``end_date``, exactly as it would be after truncating the tail
        away.

        Hand-computed.  Periods 0 and 1 hold nothing: every figure ``$0.00``
        against a ``$1,000.00`` balance.  Period 2 budgets the ``$250.00``
        (net ``-$250.00``) while nothing moves inside its span, so its
        ``period_timing`` is ``+$250.00`` and the two cancel.  Its balance is
        ``$1,000.00`` and NOT ``$750.00``, and that is the financial claim: on
        2026-02-12 the bank had genuinely not taken the money yet, so a
        schedule that stops there is right to show it unspent.  The money is
        not lost either -- reported over the FULL ten periods it lands in
        period 5, whose span contains 2026-03-20.

        ``_period_balances`` sampling at each period's OWN ``end_date`` is what
        makes this cancel: the fact is absent from both sides of the identity.
        Only a day in a HOLE between two reported columns fails to cancel, and
        the derivation ``pay_period_write`` materialises TILES, so no writer can
        produce one -- which is the whole reason the refusal had nothing left to
        protect.

        **A still-PROJECTED row rides along, and an adversarial review asked
        for it**: after a real truncate the reader's ``as_of`` is past the new
        horizon, so ruling R-G clamps every unpaid row to ``as_of + 1`` and
        lands it outside every column too.  The ``$100.00`` bill below is that
        second cause, in the SAME cell as the stranded settled row -- which is
        what the post-truncate grid actually renders, and a seed with only
        settled money could not see it.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        as_of = date(2026, 4, 2)
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[2], Decimal("250.00"),
            settled_on=date(2026, 3, 20), name="cleared after the tail was cut",
        )
        add_txn(
            db.session, seed_user, seed_periods[2], "still unpaid", "100.00",
            due_date=seed_periods[2].start_date,
        )
        db.session.commit()

        window = seed_periods[:3]
        figures = _view(account, scenario, window, as_of=as_of)
        for period in window[:2]:
            assert figures[period.id].net == Decimal("0.00")
            assert figures[period.id].period_timing == Decimal("0.00")
            assert figures[period.id].balance == Decimal("1000.00")
        stranded_column = figures[seed_periods[2].id]
        # $250.00 settled away from the window + $100.00 clamped out of it.
        assert stranded_column.expense == Decimal("350.00")
        assert stranded_column.net == Decimal("-350.00")
        assert stranded_column.period_timing == Decimal("350.00")
        assert stranded_column.book_vs_bank == Decimal("0.00")
        # The bank had not taken it on 2026-02-12, so the balance must not move.
        assert stranded_column.balance == Decimal("1000.00")

        rows = _identity_holds(account, scenario, window, as_of=as_of)
        assert len(rows) == 3  # the loop is not vacuous
        for period, balance_delta, explained in rows:
            assert balance_delta == explained, f"period {period.period_index}"

        # Nothing was lost: over the whole schedule the money reports in the
        # column whose span actually contains 2026-03-20.
        full = _view(account, scenario, seed_periods, as_of=as_of)
        assert seed_periods[5].start_date <= date(2026, 3, 20)
        assert date(2026, 3, 20) <= last_covered_day(seed_periods[5])
        assert full[seed_periods[5].id].period_timing == Decimal("-250.00")
        assert full[seed_periods[5].id].balance == Decimal("750.00")

    def test_an_empty_period_reports_zeros_against_its_folded_balance(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Every requested period is present, TOTAL over the window.

        With no rows at all, all ten periods report ``$0.00`` on every subtotal
        and remainder while carrying the opening balance -- a missing key would
        make a consumer branch on absence, which is the partiality this whole
        arc deletes.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        db.session.commit()

        figures = _view(account, scenario, seed_periods)
        assert len(figures) == 10
        assert list(figures) == [period.id for period in seed_periods]
        empty = CashPeriodFigures(
            balance=Decimal("1000.00"),
            income=Decimal("0.00"),
            expense=Decimal("0.00"),
            net=Decimal("0.00"),
            period_timing=Decimal("0.00"),
            book_vs_bank=Decimal("0.00"),
        )
        for period in seed_periods:
            assert figures[period.id] == empty


    def test_money_that_moved_here_but_is_budgeted_off_window_is_all_remainder(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The user's sharpest question: the balance dropped, both rows are zero.

        Reports period 2 ONLY, with a ``$180.00`` expense budgeted to period 0
        -- outside the window -- but settled 2026-02-05, inside period 2.

        Hand-computed: nothing is ATTRIBUTED to period 2, so both subtotal rows
        read ``$0.00``, while ``-$180.00`` moved through it.  The whole change is
        ``period_timing`` (``-$180.00``) against a ``$820.00`` balance.  This is
        the mirror of the case above (budgeted in, moved out) and the one that
        would otherwise read as a balance dropping for no reason.

        Landing it on ``period_timing`` rather than ``book_vs_bank`` is the
        answer the user acts on: the money is accounted for, it is just budgeted
        to a column outside the window.  Booking it as book-vs-bank would say
        the bank disagreed with the app, which is false.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[0], Decimal("180.00"),
            settled_on=date(2026, 2, 5), name="budgeted two columns back",
        )
        db.session.commit()

        window = [seed_periods[2]]
        figures = _view(account, scenario, window)[seed_periods[2].id]
        assert figures.income == Decimal("0.00")
        assert figures.expense == Decimal("0.00")
        assert figures.net == Decimal("0.00")
        assert figures.period_timing == Decimal("-180.00")
        assert figures.book_vs_bank == Decimal("0.00")
        assert figures.balance == Decimal("820.00")

        rows = _identity_holds(account, scenario, window)
        assert len(rows) == 1  # the loop is not vacuous
        assert rows[0][1] == rows[0][2] == Decimal("-180.00")


# **``TestTheColumnsReadTheDERIVEDSpanNotTheStoredColumn`` was deleted at
# plan step ``pay_calendar:C4-c``, and it is worth saying what it did
# rather than leaving a gap.**  It was plan step C2-c's whole claim proved
# by CORRUPTING the column: a ``budget.pay_periods.end_date`` pushed five
# days past what the owner's paydays imply, applied with a direct UPDATE
# because ``pay_period_write`` rematerialised the derivation on every
# write and so could not produce one.  Three cases then asserted that
# neither the settled columns, nor the projected ones, nor the block
# headings moved -- with a control showing the stored column really would
# have answered differently, so the agreement was evidence rather than a
# tautology.
#
# The column is dropped, so there is no second end to corrupt and no
# reader that could prefer it.  The premise is unconstructible rather than
# merely unreached.  What the class was protecting -- that every column
# here is valued at the span the paydays give -- is now a property of
# there being one span: ``TestTheIdentityHoldsOnEveryPeriod`` above
# drives it on ordinary schedules, and the class below drives it across a
# 28-day gap in the saved rows.


class TestAStoredScheduleHoleNoLongerBreaksTheIdentity:
    """``balance:N-128``'s surviving half, closed at plan step **C2-c**.

    The finding: a pay-period HOLE breaks ruling R-K's reconciliation identity,
    and it does so in the FOLD rather than above it -- measured at
    ``-$140.63`` on a gapped clone.  Its CAUSE closed at ``C2-b2`` (the write
    door can no longer produce one), and what survived was the READER: the cash
    view bisected the stored ``[start_date, end_date]`` spans, so a day between
    two of them was attributed to no column and the balance a user scans down
    stopped telescoping.

    A calendar DERIVED from the paydays has no such day.  A period's end is the
    day before the next payday, so consecutive paydays define adjacent
    intervals and the schedule tiles ``[first payday, horizon]`` by
    construction -- the hole is absorbed into an over-long period rather than
    reported (ledger row **P27**, which owns that consequence for the
    recurrence engine and is owned by ``C4``, the column a hole lives in).
    """

    def test_every_day_between_two_paydays_lands_in_some_column(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A 28-day gap in the SAVED rows, and the identity still holds.

        Period 1 (2026-01-16..01-29) is deleted outright, which is the shape a
        hole actually takes: the paydays either side survive and nothing covers
        the days between.  A ``$250.00`` expense budgeted to period 0 settles
        2026-01-20, inside the vacated span.

        Hand-computed.  The derivation gives period 0 the span
        2026-01-02..2026-01-29 -- its end is the day before the NEXT surviving
        payday -- so the settle is inside its own column: net ``-$250.00``,
        ``period_timing`` ``$0.00``, and a balance of ``$750.00`` at 01-29.
        Reading the stored spans, 01-20 fell in the hole and was counted by no
        column at all, leaving period 0's balance change unexplained by
        ``$250.00``.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[0], Decimal("250.00"),
            settled_on=date(2026, 1, 20), name="settled inside the hole",
        )
        db.session.commit()
        gapped = [period for period in seed_periods if period != seed_periods[1]]
        db.session.query(PayPeriod).filter_by(id=seed_periods[1].id).delete()
        db.session.commit()

        figures = _view(account, scenario, gapped)

        assert figures[seed_periods[0].id].net == Decimal("-250.00")
        assert figures[seed_periods[0].id].period_timing == Decimal("0.00")
        assert figures[seed_periods[0].id].balance == Decimal("750.00")

        rows = _identity_holds(account, scenario, gapped)
        assert len(rows) == len(gapped)  # the loop is not vacuous
        for period, balance_delta, explained in rows:
            assert balance_delta == explained, (
                f"period {period.period_id}"
            )

    def test_the_reported_columns_telescope_across_the_gap(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The property the identity alone does not give: the COLUMN adds up.

        Each column reconciling against its own boundaries held even with a
        hole -- that is what made **P32** hard to see.  What did not hold is
        that consecutive reported columns MEET, so the balance a reader scans
        down moves only by figures the rows beside it explain.  Asserted
        directly here: every period's span opens the day after its
        predecessor's closes.
        """
        db.session.query(PayPeriod).filter_by(id=seed_periods[1].id).delete()
        db.session.commit()
        ctx = BalanceContext.build(seed_user["user"].id)

        window = list(ctx.reported_periods())

        assert len(window) == len(seed_periods) - 1
        for earlier, later in zip(window, window[1:]):
            assert later.start_date == earlier.end_date + timedelta(days=1)


class TestTheViewCarriesTheBasisItWasValuedOn:
    """Ruling R-Q: the live override map rides on the RESULT, not the caller.

    The grid renders each row's amount beside the balance those same rows fold
    into.  Before plan step X-c2b2 the route built its own live map and the
    seam built another, and the two were "provably identical" only by an
    argument about their candidate sets (finding N-51) -- so the view returned
    the map it actually valued the plan through and the route read it back.

    **The map is DELETED as of plan step X-au-d and the property is stronger
    for it.**  A derived row stores no figure, so there is nothing for a live
    recompute to be laid over: both sides ask ``cash_ledger.amounts_by_key``
    over the pass's own basis and cannot come to hold two answers.  The field
    went with the producer -- and an AST and grep census found NOTHING had read
    ``CashPeriodView.amount_overrides`` since X-c2b2 anyway, while the case
    below asserted, in its own docstring, that "the grid reads
    ``view.amount_overrides.get(...)`` per row".  That claim had been false for
    two plan steps.
    """

    def test_a_declared_salary_row_is_priced_by_its_PROFILE_in_the_subtotal(
        self, db, seed_user, seed_periods, tax_law,
    ):  # pylint: disable=unused-argument
        """A salary row's income subtotal is what its profile pays.

        A salary-linked Projected income row DECLARES its definition and stores
        no figure at all, against a ``$104,000`` profile whose net is
        ``$4,000.00`` (104000 / 26, hand-computed -- the same figure
        ``test_income_service`` pins for this setup).  A fold reading the row's
        own column would contribute ``None``; this one asks the amount model.
        """
        # pylint: disable=import-outside-toplevel
        tax_law(NO_TAX_LAW)
        from tests.test_services.test_income_service._helpers import (
            _create_profile,
            _make_salary_template,
            _make_txn,
        )

        account, scenario = seed_user["account"], seed_user["scenario"]
        profile = _create_profile(seed_user["user"].id, scenario.id)
        template = _make_salary_template(seed_user, profile)
        db.session.commit()
        period = seed_periods[5]
        txn = _make_txn(seed_user, period, template=template)
        db.session.commit()

        view = period_view_of(
            assembled_fold(account, read_pass(account, scenario, _EARLY_AS_OF)),
            period_window(seed_periods),
        )

        assert txn.estimated_amount is None
        assert view.columns[period.id].income == Decimal("4000.00")
