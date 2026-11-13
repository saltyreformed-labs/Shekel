"""The subtotals count every attributed row, and the remainder holds what they cannot."""

from datetime import date
from decimal import Decimal

from app.services import cash_ledger, transaction_service
from app.services.balance_at._cash_fold import assembled_fold, balances_at
from tests._test_helpers import (
    add_entry,
    add_txn,
    an_entered_day,
    append_balance_assertion,
    basis_for,
    create_envelope_txn,
    create_settled_cash_transaction,
    mark_purchase_settled,
    read_pass,
    reassert_balance_on,
    restate_account_opening,
)
from tests.test_services.test_cash_fold._helpers import _instant

from ._helpers import _ONE_DAY, _BOOKS_OPEN_ON, _EARLY_AS_OF, _view


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
            seed_user, seed_periods[2], Decimal("500.00"),
            is_income=True, settled_on=date(2026, 2, 3), name="paycheck",
        )
        create_settled_cash_transaction(
            seed_user, seed_periods[2], Decimal("200.00"),
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
            seed_user, seed_periods[3], Decimal("300.00"),
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
            seed_user, seed_periods[0], Decimal("400.00"),
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
