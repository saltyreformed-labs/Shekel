"""X-c1: the cash PERIOD VIEW -- one row set, two clocks, one named remainder.

Plan step X-c1 (``docs/audits/balance_architecture/README.md``, ruling R-K).
Grades ``app.services.balance_at._cash_periods.period_view_of`` -- the producer
plan step X-c2 points the grid's balance row, its subtotal rows and its two
remainder rows at.  The view is ADDITIVE here: no production surface reads
it yet, so nothing in this file can move a shipped balance.

**The identity is the point, and it is asserted against an INDEPENDENT fold.**
For every period::

    balance(p.end) - balance(p.start - 1 day)
        == net[p] + period_timing[p] + book_vs_bank[p]

**The remainder is TWO figures here, and that is ruling R-DH (f)** (plan step
S1-c).  It was one field, ``reconciliation``, summing two facts with different
causes and different fixes: money landing in a different column from the one it
was budgeted to (``period_timing``), and the gap between what the app had
recorded and what the bank actually held (``book_vs_bank``).  Every assertion
below that read the single field now names WHICH half carries the figure and
pins the other at ``$0.00`` -- which is strictly sharper, because the old form
could not tell a timing error from a true-up and each of these fixtures produces
exactly one of the two.

The left side is sampled from ``balances_at`` -- the X-b producer, graded
on its own hand-computed oracle -- and the right side is the view's grouping of
the same rows.  Neither is derived from the other: the view computes its
remainder from the row set (what MOVED in the column minus what was BUDGETED to
it, plus the assertions), never as ``balance_delta - net``, precisely so this
assertion can fail (plan Section 7.2 -- a residual proving itself is the
forbidden shape).

**Every expected figure below is HAND-COMPUTED and written out in the test that
asserts it**, per component: the basis change, TIMING (a row settled outside its
own column), the TRUE-UP (a balance assertion), the CLAMP (ruling R-G moving an
overdue plan forward), and the OPENING that must book nothing (ruling R-I).
Where a figure exists in the shipping producer it is asserted too, so the basis
change is shown as a measured difference rather than claimed.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction_entry import TransactionEntry
from app.services import cash_ledger, transaction_service
from app.services.balance_at import BalanceContext
from app.services.balance_at._cash_fold import assembled_fold, balances_at
from app.services.balance_at._cash_periods import (
    CashPeriodFigures,
    period_view_of,
)
from app.services.pay_calendar import PayCalendarError, PeriodWindow
from tests._test_helpers import NO_TAX_LAW
from tests._test_helpers import (
    figure_source_columns,
    add_entry,
    add_txn,
    an_entered_day,
    append_balance_assertion,
    basis_for,
    create_envelope_txn,
    create_settled_cash_transaction,
    derived_span,
    last_covered_day,
    mark_purchase_settled,
    period_window,
    read_pass,
    reassert_balance_on,
    restate_account_opening,
)
from tests.test_services.test_cash_fold import _instant

_ONE_DAY = timedelta(days=1)

#: The day the books open for the two shapes below that carry a record dated
#: before their opening ASSERTION.  Ruling **R-HG** (plan step X-f3c-2b) makes
#: a movement on or before the books unstorable, so the record has to sit
#: BETWEEN the books and the first assertion -- which is the production shape
#: after that step's migration, and is what leaves the assertion's correction
#: non-zero.  Every other shape in this file inherits the seeded account's own
#: origination assertion, whose books
#: already precede its records.
_BOOKS_OPEN_ON = date(2025, 12, 31)
# An as-of before every fixture date below, so ruling R-G's clamp is a no-op
# except in the tests that exist for it.  The tiers are graded separately on
# purpose: a test that mixed them could pass with either one wrong.
_EARLY_AS_OF = date(2026, 1, 20)


def _view(account, scenario, periods, as_of=_EARLY_AS_OF):
    """Return the view's per-period COLUMNS, keyed by period id.

    The producer returns a :class:`CashPeriodView` -- the columns plus the live
    override map the projection was valued through (ruling R-Q, wired at plan
    step X-c2b2, so the grid's cells and its balance row cannot be priced off
    two different maps).  These tests grade the columns, so the helper unwraps
    them; ``TestTheViewCarriesTheBasisItWasValuedOn`` grades the map.
    """
    return period_view_of(
        assembled_fold(account, read_pass(account, scenario, as_of)),
        period_window(periods),
    ).columns


def _identity_holds(account, scenario, periods, as_of=_EARLY_AS_OF):
    """Return ``[(period, balance_delta, explained)]`` per period.

    ``explained`` is ``net + period_timing + book_vs_bank`` -- R-DH (f) split
    the single remainder in two, and the identity is stated over BOTH halves
    rather than over a combined accessor.  No such accessor survives on the
    dataclass: leaving one would invite a surface to render the sum again,
    which is the figure the ruling exists to delete, so the sum is composed
    here at the one place that genuinely needs it.

    The balance delta is sampled from the INDEPENDENT fold at the boundary
    dates; the right-hand side is the view's own grouping.  The caller asserts
    the pairs are equal -- and reads the same pairs to check the run was not
    vacuous.

    **The boundaries come from the WINDOW, not from the ORM rows** (plan step
    C2-c).  They used to be ``period.start_date`` / ``period.end_date`` off the
    stored columns, which sampled the fold on a day the reported column may not
    actually close on -- so a schedule whose stored ends had drifted made this
    oracle disagree with the producer for a reason that was the ORACLE's.  Both
    sides now read the one calendar, which is what the identity is a property
    OF.  The tuple's first element is a
    :class:`~app.services.pay_calendar.DerivedPeriod` for the same reason.
    """
    window = period_window(periods)
    figures = _view(account, scenario, periods, as_of=as_of)
    boundaries = [period.start_date - _ONE_DAY for period in window]
    boundaries += [period.end_date for period in window]
    folded = balances_at(
        assembled_fold(account, read_pass(account, scenario, as_of)), boundaries,
    )
    return [
        (
            period,
            folded[period.end_date] - folded[period.start_date - _ONE_DAY],
            figures[period.period_id].net
            + figures[period.period_id].period_timing
            + figures[period.period_id].book_vs_bank,
        )
        for period in window
    ]


class TestTheSubtotalsCountEveryAttributedRow:
    """Ruling R-K's basis change: budget-vs-actual, not unpaid-only.

    Today's subtotal counts only rows that are still UNPAID, so every past
    column reads ``$0.00`` income and ``$0.00`` expenses while thousands moved
    through it (finding N-41, measured on the real Checking account).  These
    pin the new basis AND the old answer beside it, so the change is a measured
    difference rather than a claim.
    """

    def test_a_settled_row_counts_where_the_old_subtotal_read_zero(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A settled income and a settled expense, plus one still-projected row.

        Period 2 (2026-01-30..02-12) carries a ``$500.00`` income settled 02-03,
        a ``$200.00`` expense settled 02-05 and a ``$75.00`` still-projected
        expense.  The account's opening assertion is the seeded owner's own
        origination, ``$1,000.00`` on the bootstrap day before the calendar
        (plan step X-f3c-2c), so every figure below is measured from that level
        and nothing here re-dates it.

        Hand-computed, new basis: income ``$500.00``; expenses
        ``200.00 + 75.00 = $275.00``; net ``$225.00``.  Every row settled inside
        its own column (``period_timing`` ``$0.00``) and nobody re-anchored
        (``book_vs_bank`` ``$0.00``), so the balance is
        ``1000 + 500 - 200 - 75 = $1,225.00``.

        The RETIRED producer answered ``$0.00`` income and ``$75.00`` expenses
        for the same column -- the two settled rows were invisible to it -- which
        is the defect R-K's basis change exists to end.  Those two figures were
        asserted here against ``cash_ledger.period_subtotals`` until plan step
        X-c2b3 deleted it; the old basis is now recorded rather than executed,
        which is the cost of deleting an incumbent and the reason the deletion
        came AFTER the cutover proved the successor (the C3b3 / E1e precedent).
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[2], Decimal("500.00"),
            is_income=True, settled_on=date(2026, 2, 3), name="paycheck",
        )
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[2], Decimal("200.00"),
            settled_on=date(2026, 2, 5), name="rent",
        )
        add_txn(db.session, seed_user, seed_periods[2], "bill", "75.00")
        db.session.commit()

        figures = _view(account, scenario, seed_periods)[seed_periods[2].id]
        assert figures.income == Decimal("500.00")
        assert figures.expense == Decimal("275.00")
        assert figures.net == Decimal("225.00")
        assert figures.period_timing == Decimal("0.00")
        assert figures.book_vs_bank == Decimal("0.00")
        assert figures.balance == Decimal("1225.00")

        # Non-vacuity, now that the old basis is gone as an executable
        # reference: the settled row is what makes this column differ from what
        # an unpaid-only reduction could ever report, so assert it is PRESENT in
        # the figures rather than merely that the figures are self-consistent.
        # The retired subtotal WAS ``sum_projected`` over the account's rows,
        # so composing the two reproduces its answer exactly -- $75.00 -- and
        # the column exceeds it by the settled $200.00.  Plan step X-g4b
        # re-pointed this off ``load_balance_transactions`` (deleted with its
        # last caller) onto ``planned_cash_rows``, which is the loader the
        # producer under test ACTUALLY uses: the reference is now the same row
        # set the column is built from rather than a second loader that agreed.
        # ``sum_projected`` re-applies ``is_projected`` over whatever it is
        # handed, so the figure is unchanged.
        # The basis is REQUIRED (plan step S1-c), so the reference states the
        # one the producer itself would build for this account.  The $75.00
        # bill carries no entries, so nothing in it can move the figure --
        # which is why the basis is built honestly rather than zeroed to make
        # the call compile.  It was a ``ProjectedBasis`` carrying the account's
        # clearing rule beside this until plan step X-f3b (ruling **R-FM**),
        # when a purchase's posted-ness became a fact about the purchase.
        period_rows = [
            row for row in cash_ledger.planned_cash_rows(
                account.id, scenario.id,
            )
            if row.pay_period_id == seed_periods[2].id
        ]
        basis = basis_for(account, scenario)
        _, unpaid_only_expense = cash_ledger.sum_projected(period_rows, basis)
        assert unpaid_only_expense == Decimal("75.00")
        assert figures.expense - unpaid_only_expense == Decimal("200.00")

    def test_a_settled_envelope_counts_its_dated_debit_purchases(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Credit-card purchases never left checking, so they are not expense.

        A ``$200.00`` envelope in period 2 settled from its purchases: a
        ``$120.00`` DEBIT purchase the bank took on 2026-02-05 and an
        ``$80.00`` CREDIT purchase.

        Hand-computed: the dated debit movement is the ``$120.00`` of expense;
        the credit purchase leaves through its own CC Payback sibling and
        the row books nothing of its own (ruling **R-BAL80**, plan step
        ``balance:X-bi-4a``).  So the column's expense row reads ``$120.00``,
        not the envelope's ``$200.00`` record, and the balance reads
        ``1000 - 120 = $880.00`` -- the subtotal and the balance priced the
        family through ONE rule.  Through ``X-bi-3e`` this case laid a stored
        ``$200.00`` figure beside the purchases, a shape ruling **R-BAL78**
        refuses at the door.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        txn = create_envelope_txn(
            seed_user, db.session, seed_periods[2], "Groceries",
            Decimal("200.00"),
        )
        add_entry(
            db.session, seed_user, txn, Decimal("120.00"), date(2026, 2, 4),
            settled_on=date(2026, 2, 5),
        )
        add_entry(
            db.session, seed_user, txn, Decimal("80.00"), date(2026, 2, 4),
            is_credit=True,
        )
        transaction_service.settle_transaction(
            txn, settle_day=an_entered_day(date(2026, 2, 5)),
        )
        db.session.commit()

        figures = _view(account, scenario, seed_periods)[seed_periods[2].id]
        assert figures.expense == Decimal("120.00")
        assert figures.net == Decimal("-120.00")
        assert figures.period_timing == Decimal("0.00")
        assert figures.book_vs_bank == Decimal("0.00")
        assert figures.balance == Decimal("880.00")

    def test_a_row_counts_on_its_TYPE_row_even_when_its_cash_leg_inverts(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The one shape where the leg's SIGN and the row's TYPE disagree.

        A Groceries envelope in period 2 whose only purchase is a ``$30.00``
        REFUND the bank returned on 2026-02-05 -- a negative purchase (ruling
        **bank_import:R-II**), a merchant credit filed against the envelope --
        settled from its purchases.

        Hand-computed: the movement's cash leg is ``-(-30.00) = +$30.00`` -- a
        settled EXPENSE that nets money INTO checking.  It belongs on the
        expense row as ``-$30.00``, not on the income row as ``+$30.00``: an
        expense that came back is not income.  The net (``+$30.00``) and the
        balance are the same either way, which is exactly why this shape has
        to be pinned -- it is the only one that can tell a TYPE classification
        from a sign test, and without it the fact's ``is_income`` would be
        carrying an untested claim.  Through ``X-bi-3e`` the inversion was
        built from a ``$50.00`` figure typed over an ``$80.00`` card purchase,
        a shape ruling **R-BAL78** refuses at the door; the refund is the
        representable one.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        txn = create_envelope_txn(
            seed_user, db.session, seed_periods[2], "Groceries",
            Decimal("80.00"),
        )
        add_entry(
            db.session, seed_user, txn, Decimal("-30.00"), date(2026, 2, 4),
            settled_on=date(2026, 2, 5), description="refund",
        )
        transaction_service.settle_transaction(
            txn, settle_day=an_entered_day(date(2026, 2, 5)),
        )
        db.session.commit()

        figures = _view(account, scenario, seed_periods)[seed_periods[2].id]
        assert figures.income == Decimal("0.00")
        assert figures.expense == Decimal("-30.00")
        assert figures.net == Decimal("30.00")
        assert figures.period_timing == Decimal("0.00")
        assert figures.book_vs_bank == Decimal("0.00")
        assert figures.balance == Decimal("1030.00")

    def test_a_projected_envelope_counts_its_entries_aware_reservation(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A partially-spent envelope counts on BOTH sides, and they sum.

        A ``$200.00`` Groceries envelope due 2026-02-10 in period 2, carrying one
        ``$120.00`` debit purchase made 01-31 and RECORDED AS POSTED that same
        day, read at ``as_of = 2026-02-01``.

        Hand-computed, in two halves that are one row's cost:

        * the ``$120.00`` has LEFT the account, so it is a settled fact of its
          own on 01-31 (ruling **R-FM**, plan step X-f3b), counted on the budget
          clock at its PARENT's column -- period 2;
        * the reservation holds the rest,
          ``max(200.00 - 120.00 - 0.00, 0.00) = $80.00``, landing on the due
          date.

        So the expense row reads ``$200.00`` -- what this period actually costs
        -- rather than the ``$80.00`` it read before X-f3b, when a purchase was
        no fact at all and its spend was inside the owner's asserted balance and
        nowhere in these subtotals.  **The BALANCE does not move**: the balance is
        asserted for 01-31 (finding N-132 / R8: a purchase gets inside a
        declared balance by the user declaring the balance after it posted), so
        that assertion absorbs the ``$120.00`` and the balance stays
        ``1000 - 80 = $920.00``.  ``period_timing`` stays ``$0.00``: 01-31 is
        inside period 2's own span so the cash clock and the budget clock agree,
        and the due date is past ``as_of + 1`` so ruling R-G's clamp is a no-op.

        ``book_vs_bank`` reads ``$120.00``, and that is plan step X-f3c-2a
        rather than a defect here.  The books opened at ``$1,000.00``; the
        posted purchase took them to ``$880.00``; the owner then declared
        ``$1,000.00``, which is a ``+$120.00`` correction and belongs on the row
        that holds what the app did not know.  *It read ``$0.00`` until
        X-f3c-2a, because the opening assertion was excluded from this row
        outright -- the fold had swallowed its whole delta into the seed
        (ruling R-I), so what remained was not a correction anyone could
        report.*
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        # The balance is asserted on 01-31, which is what absorbs the
        # purchase's own movement -- and it is WRITTEN here rather than the
        # origination re-stamped onto that day (plan step X-f3c-2c).  The
        # seeded account asserts only its origination, dated on the bootstrap
        # day before the calendar, so a case turning on which day was last
        # asserted names it.
        reassert_balance_on(db.session, account, _instant(2026, 1, 31))
        txn = create_envelope_txn(
            seed_user, db.session, seed_periods[2], "Groceries",
            Decimal("200.00"),
        )
        txn.due_date = date(2026, 2, 10)
        add_entry(
            db.session, seed_user, txn, Decimal("120.00"), date(2026, 1, 31),
        )
        mark_purchase_settled(db.session, account, txn.entries[0])
        db.session.commit()

        figures = _view(
            account, scenario, seed_periods, as_of=date(2026, 2, 1),
        )[seed_periods[2].id]
        assert figures.expense == Decimal("200.00")
        assert figures.period_timing == Decimal("0.00")
        assert figures.book_vs_bank == Decimal("120.00")
        assert figures.balance == Decimal("920.00")


class TestTheRemainderHoldsWhatTheSubtotalsCannot:
    """The components of the two remainder rows, one test each.

    **Which ROW a component lands on is the property, not just its size**
    (ruling R-DH (f), plan step S1-c).  Before the split all four cases below
    asserted one combined figure, so a component booked to the wrong cause
    passed silently.  Each test now names the half that carries it and pins the
    other at ``$0.00``: timing components (a row settled outside its column, a
    plan the clamp moved forward) land on ``period_timing``, and only a balance
    ASSERTION lands on ``book_vs_bank``.
    """

    def test_timing_a_row_that_settled_outside_its_own_column(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Budgeted to period 3, paid in period 5: two timings that cancel.

        A ``$300.00`` expense attributed to period 3 (2026-02-13..02-26) but
        settled 2026-03-20, inside period 5 (2026-03-13..03-26).  This is 19 of
        the real Checking account's 130 settled rows.

        Hand-computed.  Period 3: expenses ``$300.00`` (it is that column's
        budget), nothing moved, so ``period_timing`` is ``0 - (-300) = +$300.00``
        and the balance does not change.  Period 5: no row is budgeted there, but
        ``-$300.00`` moved, so its ``period_timing`` is ``-$300.00`` and the
        balance drops by it.  The two net to ``$0.00`` across history -- the row
        is counted once as budget and once as cash, never twice.

        ``book_vs_bank`` is ``$0.00`` in BOTH columns, and that is the half of
        this the combined figure could not state: nobody re-anchored, so nothing
        here is about what the bank held.  A regression that booked a timing
        difference as a true-up would have passed the old assertion.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[3], Decimal("300.00"),
            settled_on=date(2026, 3, 20), name="late rent",
        )
        db.session.commit()

        figures = _view(account, scenario, seed_periods)
        budgeted = figures[seed_periods[3].id]
        assert budgeted.expense == Decimal("300.00")
        assert budgeted.net == Decimal("-300.00")
        assert budgeted.period_timing == Decimal("300.00")
        assert budgeted.book_vs_bank == Decimal("0.00")
        assert budgeted.balance == Decimal("1000.00")

        moved = figures[seed_periods[5].id]
        assert moved.expense == Decimal("0.00")
        assert moved.net == Decimal("0.00")
        assert moved.period_timing == Decimal("-300.00")
        assert moved.book_vs_bank == Decimal("0.00")
        assert moved.balance == Decimal("700.00")

        assert (
            budgeted.period_timing + moved.period_timing == Decimal("0.00")
        )

    def test_a_true_up_is_the_remainder_no_row_can_explain(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The user asserts a new balance mid-period; no transaction moved.

        Opening ``$1,000.00`` on the seeded bootstrap day, then a true-up on
        2026-03-01
        (inside period 4, 2026-02-27..03-12) asserting ``$1,500.00`` with no rows
        in between.

        Hand-computed: the correction is ``1500.00 - 1000.00 = +$500.00``.  Both
        subtotal rows are ``$0.00`` -- there is no transaction to count -- so
        ``book_vs_bank`` is the whole ``+$500.00`` and it is exactly the balance
        change.  This is 51 corrections and ``-$2,906.31`` net on the real
        Checking account, money no transaction row will ever explain.

        ``period_timing`` is ``$0.00``, and it is the assertion that gives this
        test its discriminating power under ruling R-DH (f): the two facts are
        rendered as separate rows with separate advice attached, so an assertion
        booked as a timing difference would tell the user to check their pay
        periods for a problem that is really untracked spend.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        append_balance_assertion(
            db.session, account, Decimal("1500.00"),
            _instant(2026, 3, 1),
        )
        db.session.commit()

        figures = _view(account, scenario, seed_periods)[seed_periods[4].id]
        assert figures.income == Decimal("0.00")
        assert figures.expense == Decimal("0.00")
        assert figures.net == Decimal("0.00")
        assert figures.period_timing == Decimal("0.00")
        assert figures.book_vs_bank == Decimal("500.00")
        assert figures.balance == Decimal("1500.00")

    def test_the_OPENING_EQUITY_books_nothing_but_a_correction_to_it_does(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Plan step X-f3c-2a: the SEED is level; the assertion is movement.

        Books opened at ``$1,000.00`` on 2025-12-31, asserted 2026-01-05 inside
        period 0 (2026-01-02..01-15), with a ``$400.00`` expense settled 01-03
        -- after the books and two days BEFORE the assertion, in the same
        column.

        Hand-computed.  The account reads its stored ``$1,000.00`` before the
        spend, ``$600.00`` after it, and the assertion resets it to
        ``$1,000.00`` -- a ``+$400.00`` correction, which is what
        ``book_vs_bank`` holds.  Period 0's balance therefore changes by
        ``$0.00`` overall: the expense row's ``-$400.00`` and the assertion's
        ``+$400.00`` cancel, and BOTH are reported rather than netted away.

        **The ``$400.00`` sits between the books and the first assertion**, and
        that is what changed at plan step X-f3c-2b.  It used to be dated before
        the books OPENED, which made it a genuine double count -- money already
        inside the declared figure, counted a second time from its own day.
        Ruling **R-HG** deletes that state rather than the reading: the books
        now open 2025-12-31 and hold the 01-03 expense honestly, so the
        assertion's ``+$400.00`` is a real correction rather than the healing
        of an error.  **Every figure below is unchanged by that move**, which
        is the evidence the state was repaired and the fold was not.  The
        remainder naming it is still the correct reporting: the app cannot
        explain that period's arithmetic from its rows alone, and says so.

        *This asserted ``book_vs_bank == 0.00`` and a ``$1,400.00`` pre-period
        balance until X-f3c-2a.  Ruling R-I back-computed a ``$1,400.00`` seed
        so the expense would be explained in full and the remainder would read
        clean -- a figure nothing recorded, chosen to make a disagreement
        disappear.*
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        restate_account_opening(db.session, account, _BOOKS_OPEN_ON)
        # **The 2026-01-05 assertion is WRITTEN here** (plan step X-f3c-2c).
        # It used to be the seeded account's origination re-stamped onto that
        # day; the table is append-only, so the case states the balance it
        # needs on the day it names, which is what an owner does.
        reassert_balance_on(db.session, account, _instant(2026, 1, 5))
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[0], Decimal("400.00"),
            settled_on=date(2026, 1, 3), name="pre-assertion spend",
        )
        db.session.commit()

        figures = _view(account, scenario, seed_periods)[seed_periods[0].id]
        assert figures.expense == Decimal("400.00")
        assert figures.net == Decimal("-400.00")
        assert figures.period_timing == Decimal("0.00")
        assert figures.book_vs_bank == Decimal("400.00")
        assert figures.balance == Decimal("1000.00")

        # The day before the window: the stored opening equity, flat, with no
        # record yet against it.
        folded = balances_at(
            assembled_fold(account, read_pass(account, scenario, _EARLY_AS_OF)),
            [seed_periods[0].start_date - _ONE_DAY],
        )
        assert folded[seed_periods[0].start_date - _ONE_DAY] == Decimal("1000.00")

    def test_the_clamp_moves_an_overdue_plan_out_of_its_own_column(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Ruling R-G: a plan cannot have already happened.

        A ``$50.00`` bill budgeted to period 5 (2026-03-13..03-26) due
        2026-03-20, read at ``as_of = 2026-04-02`` -- still unpaid, a fortnight
        overdue.

        Hand-computed: its effective day is ``max(03-20, 04-03) = 2026-04-03``,
        inside period 6 (2026-03-27..04-09).  So period 5 still BUDGETS the
        ``$50.00`` expense while nothing moves there (``period_timing``
        ``+$50.00``), and period 6 carries the ``-$50.00`` the balance actually
        takes (``period_timing`` ``-$50.00``).  Under the rejected alternative
        the bill would land on its stale due date and the next re-anchor would
        absorb it -- deleting it from the projection entirely.

        The clamp is a WHEN fact, so it belongs to ``period_timing`` and
        ``book_vs_bank`` reads ``$0.00`` in both columns.  That is the ruling's
        own advice made testable: a persistently non-zero timing row means a
        bill is budgeted to the wrong period or is being recorded late, which is
        exactly what an overdue bill is.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        as_of = date(2026, 4, 2)
        add_txn(
            db.session, seed_user, seed_periods[5], "overdue bill", "50.00",
            due_date=date(2026, 3, 20),
        )
        db.session.commit()

        figures = _view(account, scenario, seed_periods, as_of=as_of)
        budgeted = figures[seed_periods[5].id]
        assert budgeted.expense == Decimal("50.00")
        assert budgeted.period_timing == Decimal("50.00")
        assert budgeted.book_vs_bank == Decimal("0.00")
        assert budgeted.balance == Decimal("1000.00")

        landing = figures[seed_periods[6].id]
        assert landing.expense == Decimal("0.00")
        assert landing.period_timing == Decimal("-50.00")
        assert landing.book_vs_bank == Decimal("0.00")
        assert landing.balance == Decimal("950.00")


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
        from tests.test_services.test_income_service import (
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

    # ``test_an_account_with_no_plan_carries_an_empty_map`` lived here until
    # plan step X-au-d, and its SUBJECT is deleted with the field: it asserted
    # that ``CashPeriodView.amount_overrides`` was ``{}`` rather than ``None``
    # for an account with nothing planned, so that "the grid reads
    # ``view.amount_overrides.get(...)`` per row" would not raise.  The grid
    # had stopped reading it two plan steps earlier and nothing else ever did,
    # so the field is gone and there is no mapping left to be empty.
