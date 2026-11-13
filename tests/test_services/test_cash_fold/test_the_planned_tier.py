"""Ruling R-G: a still-projected row lands at max(its attribution date, as_of + 1 day), never
absorbed; and a row filed in a period the pass's calendar never saw is refused rather than
placed.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services import pay_period_write
from app.services.balance_at._cash_fold import assembled_fold
from app.services.pay_calendar import calendar_for
from tests._test_helpers import (
    rhythm_of,
    add_entry,
    add_txn,
    append_balance_assertion,
    create_envelope_txn,
    create_settled_cash_transaction,
    last_covered_day,
    mark_purchase_settled,
    read_pass,
)

from ._helpers import _instant, _fold, _opened_at


class TestThePlannedTier:
    """Ruling R-G: a plan cannot have already happened.

    A still-Projected row lands at ``max(its attribution date, as_of + 1 day)``.
    Rejected at the ruling: landing it on its nominal date and letting the next
    assertion's reset erase it, which on real data (one Checking re-anchor every
    2.3 days) silently deletes nearly every unpaid past-due bill within days of
    its being entered.
    """

    def test_an_overdue_bill_clamps_forward_instead_of_being_erased(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """R-G's worked Checking figures, re-based on ruling R-DH (a).

        The ruling's shape: an assertion of $2,932.41 on 2026-04-02, a -$108.15
        expense recorded ten minutes later the same day, and a still-projected
        $50.00 bill due four days EARLIER (2026-03-29).  Read at
        ``as_of = 2026-04-02``.

        Hand-computed: the same-day settle is inside the closing balance
        (R-DH (a)), so 04-02 reads $2,932.41; the overdue bill clamps to
        ``as_of + 1`` and lands 04-03, so 04-03 reads
        ``2932.41 - 50.00 = 2882.41``.  **R-G is what this test grades and R-G
        is untouched** -- the base moved by ``$108.15`` because the partition
        did, and the ``-$50.00`` step one day later is the clamp arm, unchanged.
        The ruling's rejected alternative -- the reset erasing the bill --
        answers $2,932.41 forever, which is exactly this test's 04-02 value
        carried forward.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        as_of = date(2026, 4, 2)
        _opened_at(account, _instant(2026, 1, 1))
        append_balance_assertion(
            db.session, account, Decimal("2932.41"),
            _instant(2026, 4, 2, 12, 57, 8),
        )
        create_settled_cash_transaction(
            seed_user, seed_periods[6], Decimal("108.15"),
            settled_on=date(2026, 4, 2), name="settled late",
        )
        add_txn(
            db.session, seed_user, seed_periods[6], "overdue bill", "50.00",
            due_date=date(2026, 3, 29),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 4, 2), date(2026, 4, 3), date(2026, 4, 9),
        ], as_of=as_of)
        assert folded[date(2026, 4, 2)] == Decimal("2932.41")
        assert folded[date(2026, 4, 3)] == Decimal("2882.41")
        assert folded[date(2026, 4, 9)] == Decimal("2882.41")

    def test_a_future_bill_lands_on_its_own_due_date(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The clamp is a FLOOR, not a re-key: a future bill keeps its date.

        A $75.00 bill due 2026-04-08 read at ``as_of = 2026-04-02`` lands on
        04-08, not on 04-03.  Hand-computed: the fold reads $1,000.00 through
        04-07 and ``1000.00 - 75.00 = 925.00`` from 04-08.  An implementation
        that clamped every planned row to ``as_of + 1`` would answer $925.00 on
        04-03 and fail.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        add_txn(
            db.session, seed_user, seed_periods[6], "future bill", "75.00",
            due_date=date(2026, 4, 8),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 4, 3), date(2026, 4, 7), date(2026, 4, 8),
        ], as_of=date(2026, 4, 2))
        assert folded[date(2026, 4, 3)] == Decimal("1000.00")
        assert folded[date(2026, 4, 7)] == Decimal("1000.00")
        assert folded[date(2026, 4, 8)] == Decimal("925.00")

    def test_a_due_date_outside_its_period_is_pulled_to_the_boundary(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The shared attribution clamp, pinned at the fold.

        The recurrence engine can date a row just outside its own period, so a
        $90.00 bill in period 6 (2026-03-27 .. 2026-04-09) carrying a due date
        of 2026-05-20 is pulled back to that period's END, 04-09 -- the same
        rule the calendar groups its day cells by, so a flow's cell and the
        balance step for it cannot land on different days.

        Hand-computed: the fold reads $1,000.00 on 04-08 and $910.00 from
        04-09.  Without the clamp the step would land on 05-20 and 04-09 would
        still read $1,000.00.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        assert last_covered_day(seed_periods[6]) == date(2026, 4, 9)
        add_txn(
            db.session, seed_user, seed_periods[6], "stray due date", "90.00",
            due_date=date(2026, 5, 20),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 4, 8), date(2026, 4, 9), date(2026, 5, 20),
        ], as_of=date(2026, 4, 2))
        assert folded[date(2026, 4, 8)] == Decimal("1000.00")
        assert folded[date(2026, 4, 9)] == Decimal("910.00")
        assert folded[date(2026, 5, 20)] == Decimal("910.00")

    def test_a_row_with_no_due_date_lands_on_its_period_start(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The attribution fallback, clamped forward like any other.

        A $30.00 row with no ``due_date`` in period 8 (2026-04-24 ..) falls back
        to that period's START, 04-24.  Read at ``as_of = 2026-04-02`` that is
        already in the future, so the floor does not move it.  Hand-computed:
        $1,000.00 on 04-23, $970.00 from 04-24.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        assert seed_periods[8].start_date == date(2026, 4, 24)
        add_txn(
            db.session, seed_user, seed_periods[8], "undated", "30.00",
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 4, 23), date(2026, 4, 24),
        ], as_of=date(2026, 4, 2))
        assert folded[date(2026, 4, 23)] == Decimal("1000.00")
        assert folded[date(2026, 4, 24)] == Decimal("970.00")

    def test_income_and_expense_net_within_a_day(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Two planned rows sharing a day contribute their signed net.

        A +$1,200.00 paycheck and a -$450.00 bill both due 2026-04-08.
        Hand-computed: ``1000.00 + 1200.00 - 450.00 = 1750.00`` from 04-08.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        add_txn(
            db.session, seed_user, seed_periods[6], "paycheck", "1200.00",
            is_income=True, due_date=date(2026, 4, 8),
        )
        add_txn(
            db.session, seed_user, seed_periods[6], "bill", "450.00",
            due_date=date(2026, 4, 8),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 4, 7), date(2026, 4, 8),
        ], as_of=date(2026, 4, 2))
        assert folded[date(2026, 4, 7)] == Decimal("1000.00")
        assert folded[date(2026, 4, 8)] == Decimal("1750.00")

    def test_an_envelope_holds_back_its_entries_aware_reservation(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A planned envelope is worth its reservation, not its raw estimate.

        The fold reduces each day's group through the SHARED
        ``cash_ledger.sum_projected``, so an envelope carries the
        three-bucket reservation the grid already shows rather than a second
        copy of the formula.

        A $200.00 grocery envelope with one $120.00 debit purchase made and
        posted 2026-03-19, against a balance the user declared for that same
        day, holds back ``max(200.00 - 120.00 - 0, 0) = 80.00``: the posted
        debit is already inside the declared balance, so only the unreconciled
        remainder is still to come.  Hand-computed: $1,000.00 on 04-04 and
        $920.00 from 04-05 (the envelope is due 04-05 and the reader's as_of
        is 03-20, so ruling R-G's clamp is a no-op).  A fold valuing the row
        at ``effective_amount`` would answer $800.00 and fail.

        **The opening assertion is dated 03-19 rather than 01-01, and that is
        finding N-132 / R8 from a third direction** (plan step S1-c,
        Section 13.1).  The retired ``is_cleared`` flag let this fixture claim
        the purchase was inside an anchor asserted three months EARLIER -- a
        state production cannot reach, because the way a purchase gets inside a
        declared balance is that the user declared the balance after it posted.

        **Every date here sits at or before this suite's frozen today
        (2026-03-20), and that is the OTHER half of the same rule.**  The first
        conversion moved the assertion FORWARD to 04-01 to cover an 04-01
        purchase, which is unreachable in the opposite direction:
        ``anchor_service.resolve_observation_day`` refuses an
        ``observed_on`` after the user's today, and R-M refuses a
        ``purchased_on`` after it.  ``mark_purchase_settled`` now checks both
        bounds and named that fixture, which is why the SCENARIO moved back
        rather than the assertion moving further out.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 3, 19))
        txn = create_envelope_txn(
            seed_user, db.session, seed_periods[6], "Groceries",
            Decimal("200.00"),
        )
        txn.due_date = date(2026, 4, 5)
        add_entry(
            db.session, seed_user, txn, Decimal("120.00"), date(2026, 3, 19),
        )
        mark_purchase_settled(db.session, account, txn.entries[0])
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 4, 4), date(2026, 4, 5),
        ], as_of=date(2026, 3, 20))
        assert folded[date(2026, 4, 4)] == Decimal("1000.00")
        assert folded[date(2026, 4, 5)] == Decimal("920.00")

    def test_the_reservation_reads_no_clock_whatever_the_readers_as_of(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """RULED (R-M) and pinned: what a row is WORTH is not a function of *as_of*.

        The firing control for plan step X-c2c1's deletion, and the negative
        twin of the test above.  The SAME envelope and the same $120.00
        purchase, read at an ``as_of`` of 2026-03-10 -- NINE DAYS BEFORE the
        purchase was made -- must reduce the reservation identically: the fold
        reads ``max(200.00 - 120.00 - 0, 0) = 80.00`` and answers the same
        $920.00 on 04-05 that the test above produces.

        **The reader's clock moves, not the purchase's dates, and that is what
        makes the fixture reachable.**  The property is that a row's WORTH is
        independent of the reader's ``as_of``; the earlier form got the same
        separation by dating the purchase into the app's future, which BOTH
        write doors refuse (R-M on ``purchased_on``,
        ``anchor_service.resolve_observation_day`` on the assertion covering it).
        Sliding the READER backwards is the same experiment on a state
        production can actually hold.

        **The purchase must be in the SETTLED bucket for this to discriminate**
        (plan step S1-c).  An OUTSTANDING purchase is worth
        ``max(200 - 0 - 0, 120) = 200`` -- which is exactly what a
        re-introduced window would also answer, by dropping the entry and
        falling through the empty-entries short circuit to
        ``effective_amount``.  The two would be indistinguishable and the test
        would be over-determined (finding N-69's shape).  Only the settled
        bucket separates them, so the account asserts a balance covering the
        purchase and ``mark_purchase_settled`` checks that it does.

        This was finding N-39, and it was a genuine three-way fork: the retired
        calendar scalar windowed entries by the reader's now while the grid and
        the daily ramp did not, so the fold had to pick one.  Ruling R-M closed
        it at the SOURCE instead of picking -- plan step X-c0 refuses
        ``purchased_on > display_today()`` at both write doors, so no stored entry
        can be dated after any reader's now and the window provably dropped
        nothing.  What it could still have done is fire on a HISTORICAL read,
        whose plan is TODAY's still-Projected rows clamped forward (ruling R-G)
        rather than the plan as it stood then -- a partial as-of purity inside a
        tier that has none.

        So ``as_of`` now means exactly ONE thing in this fold, R-G's clamp
        floor, and this test is what fails if a window is ever re-introduced:
        restoring it answers $800.00 here.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 3, 19))
        txn = create_envelope_txn(
            seed_user, db.session, seed_periods[6], "Groceries",
            Decimal("200.00"),
        )
        txn.due_date = date(2026, 4, 5)
        add_entry(
            db.session, seed_user, txn, Decimal("120.00"), date(2026, 3, 19),
        )
        mark_purchase_settled(db.session, account, txn.entries[0])
        db.session.commit()

        # The purchase (03-19) is NINE DAYS after the reader's as_of.
        folded = _fold(account, scenario, [date(2026, 4, 5)],
                       as_of=date(2026, 3, 10))
        assert folded[date(2026, 4, 5)] == Decimal("920.00")

    def test_the_LAST_periods_end_is_the_PROJECTED_one_and_clamps_there(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The derivation's OTHER branch, driven through the fold.

        A middle period's end is ``lead(start_date) - 1``; the LAST one's is
        ``start_date + cadence_days - 1``, the only end the derivation PROJECTS
        rather than reading off a following payday (ruling **R-PC5**).  Every
        other case in this class clamps against a fact-derived end, so without
        this one ``_cash_plan`` reaching a projected end is graded only at the
        value level (``test_pay_calendar_value``) and never through the fold.

        **It was deleted with the plant it used to carry and it should not have
        been** (adversarial review of plan step ``pay_calendar:C4-c``,
        2026-09-01).  The version that stood pushed a STORED ``end_date`` out
        to 2026-06-30 to prove the reader preferred the derivation; that plant
        is unconstructible now, but the property under it never needed one --
        it needs only a row due past the last period's projected end.

        ``seed_periods`` is ten biweekly paydays from 2026-01-02, so period 9
        opens 2026-05-08 and its derived end is ``05-08 + 13 = 2026-05-21``.  A
        still-projected ``$250.00`` expense budgeted to it and dated 2026-06-15
        is seven weeks past that end.

        Hand-computed against a ``$1,000.00`` opening asserted 2026-01-01: the
        clamp lands the row on 2026-05-21, so 05-20 still reads ``$1,000.00``
        and both 05-21 and 06-14 read ``$750.00``.  A fold that did NOT clamp
        would leave the row on 06-15 and read ``$1,000.00`` on both of the
        later days, so each of those two assertions distinguishes the arms on
        its own.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        last = seed_periods[-1]
        _opened_at(account, _instant(2026, 1, 1))
        add_txn(
            db.session, seed_user, last, "projected bill", "250.00",
            due_date=date(2026, 6, 15),
        )
        db.session.commit()

        derived = calendar_for(account.user_id).period_by_id(last.id)
        assert derived.start_date == date(2026, 5, 8)
        assert derived.end_date == date(2026, 5, 21)
        # The branch under test: this end came from the CADENCE, not from a
        # following payday.  Without it the case is a middle period again.
        assert derived.end_is_projected is True

        folded = _fold(
            account, scenario,
            [date(2026, 5, 20), date(2026, 5, 21), date(2026, 6, 14)],
            as_of=date(2026, 1, 5),
        )

        assert folded[date(2026, 5, 20)] == Decimal("1000.00")
        assert folded[date(2026, 5, 21)] == Decimal("750.00")
        assert folded[date(2026, 6, 14)] == Decimal("750.00")


# **``TestThePlanClampsAgainstTheDerivedSpan`` was deleted at plan step
# ``pay_calendar:C4-c``, and it is worth saying what it did rather than
# leaving a gap.**  Three cases planted a ``budget.pay_periods.end_date``
# that disagreed with the owner's paydays -- with a direct UPDATE, under
# the writer, because no door could produce one -- and asserted that
# ``_cash_fold._cash_plan`` clamped a projected row against the DERIVED
# span rather than that column.  Both branches of the derivation were
# driven: a middle period's ``lead(start) - 1`` and the last one's
# ``start + cadence_days - 1`` projection.  That was plan step C4-a-1's
# grade and finding **P38**'s last site.
#
# The column is dropped.  There is no second end for a reader to prefer
# and no plant to make, so the class's premise is unconstructible rather
# than merely unreached -- which is the whole of what C4-c buys, and is
# why this is a deletion and not a rewrite.  The clamp itself is still
# graded: ``TestThePlannedTier`` above drives ``attribution_day`` on
# ordinary schedules, and ``DerivedPeriod.attribution_day``'s own cases
# grade the rule on the value.


class TestARowFiledOutsideThePassesCalendarIsRefused:
    """The one state the span lookup cannot answer, and it REFUSES.

    ``budget.transactions.pay_period_id`` is NOT NULL and a
    :class:`~app.services.pay_calendar.PayCalendar` is one owner's COMPLETE
    saved payday set, so the lookup is total whenever the two were read from one
    consistent picture of the database.  Balance finding **N-358** is the state
    where they were not, and what makes this fold exposed to it is its ORDER:
    it derives the calendar BEFORE it loads the rows, so a payday appended in
    between leaves rows filed in a period the calendar never saw.
    ``balance:X-i3-a`` binds a GET to ``REPEATABLE READ, READ ONLY`` and every
    measured caller of this fold is a GET -- but ``/grid`` and ``/dashboard``
    each split a GET into three transactions around their ``write_transaction``
    block, so "a GET is one snapshot" is NOT the rule and
    :meth:`~app.services.pay_calendar.PayCalendar.require_period` says so.
    ``balance:X-i5`` owns closing it; until then the fold refuses rather than
    placing a row against a span it cannot read.

    **The mechanism is SIMULATED rather than re-measured**, which is the only
    way to grade an inference: two statements of one pass, with the append
    committed between them, is what the concurrent case does to this reader and
    what it does can be reproduced in one session.
    """

    def test_a_payday_appended_after_the_pass_read_its_calendar_RAISES(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The pass holds ten paydays; the row it then loads names an eleventh.

        The pass memoizes the calendar FIRST (which is what a render does --
        every per-period entry asks for it), an eleventh payday is then recorded
        through the one write door and repopulated with a still-projected row,
        and the fold is assembled afterwards.  Its plan load sees the new row;
        its calendar does not hold the new period.

        What is asserted is the REFUSAL, and that it names both ids: the row and
        the period are what identify the pair for whoever has to investigate.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        ctx = read_pass(account, scenario, date(2026, 1, 5))
        held = ctx.calendar()
        assert len(held.periods) == 10

        appended = pay_period_write.record_paydays(
            user_id=seed_user["user"].id,
            first_payday=date(2026, 1, 2),
            num_periods=11,
            rhythm=rhythm_of(14),
        )
        assert len(appended) == 1
        repopulated = add_txn(
            db.session, seed_user, appended[0], "repopulated bill", "250.00",
            due_date=date(2026, 5, 25),
        )
        db.session.commit()

        with pytest.raises(RuntimeError) as raised:
            assembled_fold(account, ctx)

        # BOTH ids, each anchored: a bare ``id=11`` substring is satisfied by
        # ``budget.transactions id=115``, so the pair would not actually be
        # graded.  The TABLE is asserted with them since plan step C4-a-5 --
        # three tables carry a ``pay_period_id`` and their id spaces overlap,
        # so an unqualified ``id=115`` sends an investigator to whichever row
        # of that number they open first.
        message = str(raised.value)
        assert f"budget.transactions id={repopulated.id} " in message
        assert f"pay period id={appended[0].id}," in message
        assert "does not hold" in message

    def test_the_control_shows_the_SAME_shape_folds_on_a_fresh_pass(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The firing control: it is the STALE calendar that refuses, not the row.

        Same append, same repopulated row, a pass built AFTER it -- which is
        what ``app/`` does today (a write path that records a payday and then
        re-renders builds a fresh context) and what every GET gets for free
        under one snapshot.  It folds, and the row lands on its own due date:
        2026-05-25 is inside the appended period's derived span, so nothing
        clamps and ``$750.00`` is the close.

        Without this the test above could pass for the wrong reason -- a
        repopulated row the fold refuses on some other ground would look
        identical.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        appended = pay_period_write.record_paydays(
            user_id=seed_user["user"].id,
            first_payday=date(2026, 1, 2),
            num_periods=11,
            rhythm=rhythm_of(14),
        )
        add_txn(
            db.session, seed_user, appended[0], "repopulated bill", "250.00",
            due_date=date(2026, 5, 25),
        )
        db.session.commit()

        folded = _fold(
            account, scenario,
            [date(2026, 5, 24), date(2026, 5, 25)],
            as_of=date(2026, 1, 5),
        )

        assert folded[date(2026, 5, 24)] == Decimal("1000.00")
        assert folded[date(2026, 5, 25)] == Decimal("750.00")
