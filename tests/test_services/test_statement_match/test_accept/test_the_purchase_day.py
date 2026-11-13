"""Ruling R-FW, the bank owns both of a purchase's days: the correction and the money it does not
move, the earliest line, the receipt, the screen, the narrowness boundary and both clocks.
"""

from datetime import timedelta
from decimal import Decimal

from app.services import balance_at, entry_service, statement_match
from app.services.balance_at import BalanceContext
from app.services.statement_match import MatchSubmission

from tests._test_helpers import an_entered_day

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_purchase,
    a_scope,
    a_transaction,
    an_import,
)

from ._helpers import _submit


class TestItCorrectsThePurchaseDayTheBankContradicts:
    """Ruling **R-FW**: the bank owns BOTH of a purchase's days.

    A purchase carries the day it was MADE (``purchased_on``, the budget clock)
    beside the day the bank TOOK the money (``settled_on``, the cash clock).
    Accepting a match asserts that this bank line IS this purchase -- which
    asserts the purchase was made on or before the day the line posted.  A
    recorded day after that is refuted by the owner's own act, so it moves.

    **Measured, and it is why the step exists.**  On the developer's own
    2026-08-16 statement against a production clone, 14 unexplained bank lines
    worth `$1,028.66` were an exact amount at the same merchant as an unmatched
    purchase, and were blocked from being proposed only because the purchase
    had been recorded 1 to 5 days after the bank posted it -- six of them typed
    in one bookkeeping session on 2026-04-29 for swipes posted 04-24 and 04-27.
    The create-a-purchase door X-f6a-3b builds would have offered to record
    every one of them a SECOND time.

    **The narrowness is measured too.**  Taking the bank's day unconditionally
    would move 27 of the 44 purchases in today's proposals, 18 of them onto a
    CLEARING day, because the source states no transaction day on 179 of 361
    lines.  Correcting only what the bank contradicts moves 3.
    """

    @staticmethod
    def _envelope_and_purchase(seed_user, purchased_on):
        """Return an envelope and one unposted `$25.00` purchase under it."""
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        return envelope, a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=purchased_on,
        )

    def test_a_purchase_recorded_AFTER_the_posting_day_is_re_dated(
        self, app, db, seed_user,
    ):
        """The refuted day moves back onto the day the bank posted.

        This is the six-in-one-session shape: the money left on the bank's day
        and the purchase was typed days later, so the recorded purchase day is
        an impossibility rather than a disagreement.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        _, purchase = self._envelope_and_purchase(
            seed_user, bank_day + timedelta(days=5),
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
        )

        _submit(seed_user, lines=[line], entries=[purchase])

        assert purchase.purchased_on == bank_day
        assert purchase.settled_on == bank_day

    def test_it_takes_the_day_the_bank_STATED_over_the_day_it_cleared(
        self, app, db, seed_user,
    ):
        """Two different facts, and the swipe day is the one a purchase wants.

        SECU states the swipe day inside a card line's description on 182 of
        361 lines: 157 of them 1 to 4 days before it posts, and 25 on the
        posting day itself.  Writing the posting day instead would record the
        purchase as made on the day it cleared.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        swipe_day = bank_day - timedelta(days=2)
        _, purchase = self._envelope_and_purchase(
            seed_user, bank_day + timedelta(days=5),
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
            transaction_on=swipe_day,
        )

        _submit(seed_user, lines=[line], entries=[purchase])

        assert purchase.purchased_on == swipe_day
        assert purchase.settled_on == bank_day

    def test_a_purchase_the_bank_does_NOT_contradict_keeps_its_day(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL for the narrowness, and it is the whole ruling.

        A purchase recorded BEFORE the bank posted it is not refuted by
        anything, so the day the owner typed stands -- even though the bank
        states a day of its own.  Without this arm the door would overwrite 27
        of 44 correct dates on the developer's own statement in order to fix 3
        wrong ones.  Delete the ``expected_on <= posts_on`` test in
        ``corrected_purchase_day`` and this fails.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=6)
        made_on = bank_day - timedelta(days=4)
        _, purchase = self._envelope_and_purchase(seed_user, made_on)
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
            transaction_on=bank_day - timedelta(days=1),
        )

        _submit(seed_user, lines=[line], entries=[purchase])

        assert purchase.purchased_on == made_on
        assert purchase.settled_on == bank_day

    def test_a_TRANSACTION_has_no_purchase_day_to_correct(
        self, app, db, seed_user,
    ):
        """Only a purchase carries the second clock.

        A transaction's ``settled_on`` is its only date column -- what says
        when it was budgeted is its pay period -- so the correction must not
        leak onto the other kind.  ``update_entry`` is the only door that could
        write it, and a transaction never reaches one.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        row = a_transaction(seed_user, name="Electricity", amount="180.00")
        line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=bank_day,
            transaction_on=bank_day - timedelta(days=3),
        )

        accepted = _submit(seed_user, lines=[line], transactions=[row])

        assert row.settled_on == bank_day
        assert accepted.settled_count == 1

    def test_a_GROUP_re_dates_to_its_EARLIEST_stated_day(
        self, app, db, seed_user,
    ):
        """Earliest for the purchase day, latest for the posting day.

        A purchase the bank split across several lines was made no later than
        the first of them, while the row is not wholly moved until the last one
        posts -- so the two ends of :class:`MatchDays` are opposite ends on
        purpose, and a single ``max`` for both would date the purchase after
        the swipe that started it.
        """
        statement = an_import(seed_user)
        first_day = seed_user["bootstrap_period"].start_date
        last_day = first_day + timedelta(days=3)
        _, purchase = self._envelope_and_purchase(
            seed_user, last_day + timedelta(days=5),
        )
        early = a_bank_line(
            seed_user, statement, amount="-10.00", posted_on=first_day,
            transaction_on=first_day - timedelta(days=1),
        )
        late = a_bank_line(
            seed_user, statement, amount="-15.00", posted_on=last_day,
            transaction_on=last_day - timedelta(days=1),
        )

        accepted = _submit(seed_user, lines=[early, late], entries=[purchase])

        assert purchase.purchased_on == first_day - timedelta(days=1)
        assert purchase.settled_on == last_day
        assert accepted.posts_on == last_day

    def test_a_re_dated_purchase_already_settled_counts_as_CORRECTED(
        self, app, db, seed_user,
    ):
        """A day moved is work done, whichever of the two columns moved.

        A settled purchase already carrying the bank's posting day but a
        refuted purchase day is not "unchanged": the door writes to it, so
        reporting it as untouched would claim the opposite of what happened.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=9)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=bank_day + timedelta(days=2),
            settled_on=bank_day + timedelta(days=2),
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], entries=[purchase])

        assert purchase.purchased_on == bank_day
        assert purchase.settled_on == bank_day
        assert accepted.corrected_count == 1
        assert accepted.settled_count == 0


class TestCorrectingAPurchaseDayMovesNoMoney:
    """The financial property ruling **R-FW** rests on, isolated by DIFFERENCE.

    A purchase carries two clocks and only ONE is cash.  ``settled_on`` is what
    :func:`~app.services.cash_ledger.posted_purchase_sum` buckets on and what
    the walk emits a dated leg for; ``purchased_on`` is the BUDGET clock, read
    outside the write doors only by ``reconcile_service``'s offer set and by
    the grid's out-of-period warning.

    **A purchase-day-ONLY correction cannot be built, which is why this is a
    differential.**  ``corrected_purchase_day`` fires only when the recorded
    day is after the bank's, and ``ck_transaction_entries_settled_not_before_
    purchase`` then forces the settle day to move too -- so every correction
    is accompanied by a cash write, and a first draft of this class asserted a
    hand-computed allow-set around it that passed vacuously.  Found by
    adversarial test-quality review 2026-08-18, which proposed this shape.

    **The shape:** two fixtures identical in every way that touches cash --
    same amount, same envelope, same bank line, so the same ``posts_on`` --
    differing only in whether the purchase day is REFUTED.  One is re-dated and
    one is not.  If ``purchased_on`` were a cash input the two daily balance
    series would diverge; the assertion is that they are identical.
    """

    @staticmethod
    def _series(seed_user, lo, hi):
        """Return the checking account's daily balance over ``[lo, hi]``."""
        out, day = {}, lo
        while day <= hi:
            ctx = BalanceContext(
                user_id=seed_user["user"].id,
                scenario=seed_user["scenario"], as_of=day,
            )
            out[day] = balance_at.balance_at(seed_user["account"], ctx, day)
            day += timedelta(days=1)
        return out

    @staticmethod
    def _accept_one(seed_user, made_on, bank_day, name):
        """Accept a match for one `$25.00` purchase made on *made_on*."""
        statement = an_import(seed_user)
        envelope = a_transaction(
            seed_user, name=name, amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=made_on,
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
        )
        return purchase, _submit(seed_user, lines=[line], entries=[purchase])

    def test_a_re_dated_purchase_books_what_an_un_re_dated_one_books(
        self, app, db, seed_user,
    ):
        """The difference between the two runs is the purchase day and nothing.

        Delete the ``expected_on <= posted_first`` test so that BOTH are
        re-dated and this still passes -- correctly, because the property is
        that the budget clock is not cash, not that the correction is rare.
        Make ``purchased_on`` a cash input and it fails.
        """
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=9)
        lo, hi = bank_day - timedelta(days=6), bank_day + timedelta(days=12)

        refuted, accepted_a = self._accept_one(
            seed_user, bank_day + timedelta(days=4), bank_day, "Groceries A",
        )
        db.session.flush()
        with_correction = self._series(seed_user, lo, hi)

        assert refuted.purchased_on == bank_day, "the budget clock did not move"
        assert accepted_a.redated_count == 1

        db.session.rollback()

        unrefuted, accepted_b = self._accept_one(
            seed_user, bank_day - timedelta(days=4), bank_day, "Groceries B",
        )
        db.session.flush()
        without_correction = self._series(seed_user, lo, hi)

        assert unrefuted.purchased_on == bank_day - timedelta(days=4)
        assert accepted_b.redated_count == 0
        assert with_correction == without_correction, (
            "the purchase day moved a balance: "
            f"{ {d: (with_correction[d], without_correction[d])
                 for d in with_correction
                 if with_correction[d] != without_correction[d]} }"
        )

    def test_the_window_can_SEE_a_money_move(self, app, db, seed_user):
        """THE POSITIVE CONTROL, without which the test above proves nothing.

        A null result over a window is worth what the window's sensitivity is
        worth.  This moves the CASH clock on the same helper over the same
        window and asserts the series changes by the purchase's own amount, so
        a harness that had gone blind fails here rather than reporting
        agreement forever.
        """
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=9)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=bank_day, settled_on=bank_day,
        )
        lo, hi = bank_day - timedelta(days=5), bank_day + timedelta(days=12)
        before = self._series(seed_user, lo, hi)

        entry_service.update_entry(
            purchase.id, seed_user["user"].id,
            settle_day=an_entered_day(bank_day + timedelta(days=4)),
        )
        db.session.flush()

        after = self._series(seed_user, lo, hi)
        moved = {d for d in before if before[d] != after[d]}
        assert moved, "the window cannot see a cash move, so it grades nothing"
        assert {after[d] - before[d] for d in moved} == {Decimal("25.00")}


class TestWhatRefutesAPurchaseDayIsTheEARLIESTLine:
    """A group's two ends are opposite, and so are the days they answer.

    ``posts_on`` is the LATEST bank day, because a row is not wholly moved
    until its last line posts.  But what REFUTES a recorded purchase day is the
    EARLIEST: money cannot leave before it is spent, so a purchase explained by
    lines posted 06-01 and 06-10 was made on or before 06-01.

    Testing the purchase day against ``posts_on`` let an impossibility stand --
    a purchase recorded 06-05 is impossible against the 06-01 line, and
    ``update_entry``'s own check compares it against the LATEST day, so nothing
    else would catch it either.  Found by adversarial design review 2026-08-18.
    """

    def test_a_purchase_after_the_EARLIEST_line_is_corrected(
        self, app, db, seed_user,
    ):
        """It is refuted by the first line even though the last one covers it."""
        statement = an_import(seed_user)
        first_day = seed_user["bootstrap_period"].start_date
        last_day = first_day + timedelta(days=9)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=first_day + timedelta(days=4),
        )
        early = a_bank_line(
            seed_user, statement, amount="-10.00", posted_on=first_day,
        )
        late = a_bank_line(
            seed_user, statement, amount="-15.00", posted_on=last_day,
        )

        accepted = _submit(seed_user, lines=[early, late], entries=[purchase])

        assert purchase.purchased_on == first_day
        assert purchase.settled_on == last_day
        assert accepted.redated_count == 1


class TestTheReceiptNamesAPurchaseDayCorrection:
    """A re-dated purchase that was still Projected reported only "settled".

    ``AcceptedMatch`` partitions the rows it changed into settled and
    corrected, and an unsettled purchase lands in the first -- so a
    purchase-day correction on one appeared in NEITHER count, and the flash
    said only "marked 1 row(s) as having happened".  That is the step's own
    motivating case: the six purchases typed in one bookkeeping session carry
    no settle day at all.  Found by two independent adversarial reviews
    2026-08-18.
    """

    def test_an_unsettled_purchase_that_is_re_dated_is_counted(
        self, app, db, seed_user,
    ):
        """It counts as settled AND as re-dated, because it was both."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=bank_day + timedelta(days=5),
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], entries=[purchase])

        assert accepted.settled_count == 1
        assert accepted.redated_count == 1, (
            "a purchase day moved and the receipt does not say so"
        )

    def test_a_match_that_moves_no_purchase_day_reports_none(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL: the count is not simply the purchase count."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=6)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=bank_day - timedelta(days=2),
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], entries=[purchase])

        assert accepted.settled_count == 1
        assert accepted.redated_count == 0


class TestTheStoredTransactionDayReachesTheScreen:
    """The DB -> screen wiring, which nothing graded.

    Every proposer test hand-builds a :class:`BankLine`, so the path
    ``bank_statement_lines.transaction_on`` -> ``as_bank_line`` ->
    :attr:`MatchProposal.made_on` was exercised by nothing: deleting the copy
    in the reader left the whole suite green.  The consequence is the exact
    divergence :attr:`MatchProposal.days` says it exists to prevent -- the
    screen promising the CLEARING day while the door writes the STATED one.
    Found by adversarial test-quality review 2026-08-18.
    """

    def test_the_screen_offers_the_day_the_bank_STATED(
        self, app, db, seed_user,
    ):
        """Through ``review_set``, not through a hand-built value."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=6)
        swipe_day = bank_day - timedelta(days=2)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=bank_day + timedelta(days=3),
        )
        # A second purchase, so the ENVELOPE does not price at the same figure
        # as the one being matched -- otherwise the proposer offers the parent
        # and the purchase never reaches the assertion.
        a_purchase(
            seed_user, envelope, amount="31.00", description="Aldi",
            purchased_on=bank_day,
        )
        a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
            transaction_on=swipe_day,
        )

        review = statement_match.review_set(a_scope(seed_user))

        offered = [p for p in review.proposals if p.redated_purchases]
        assert len(offered) == 1
        assert offered[0].made_on == swipe_day, (
            "the screen offers a day the reader did not carry through"
        )
        assert offered[0].redate_gap == 5

    def test_a_proposal_from_the_screen_is_ACCEPTABLE(
        self, app, db, seed_user,
    ):
        """The end-to-end claim "0 proposals whose Accept can never succeed".

        Nothing took a proposal from :func:`propose` and fed it to the accept
        door, so that claim was graded by no test at all.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=6)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=bank_day + timedelta(days=3),
        )
        a_purchase(
            seed_user, envelope, amount="31.00", description="Aldi",
            purchased_on=bank_day,
        )
        a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
            transaction_on=bank_day - timedelta(days=1),
        )
        review = statement_match.review_set(a_scope(seed_user))
        assert review.proposals

        for proposal in review.proposals:
            statement_match.accept_match(
                MatchSubmission(
                    line_ids=frozenset(l.line_id for l in proposal.lines),
                    rows=frozenset(
                        statement_match.as_reviewed(r) for r in proposal.rows
                    ),
                ),
                a_scope(seed_user),
            )


class TestTheNarrownessBoundary:
    """`expected_on <= posted_first` is where the 27-of-44 over-correction begins.

    A purchase recorded EXACTLY on the day the bank posted it is not refuted by
    anything, and turning that ``<=`` into ``<`` would drag it backwards onto
    the stated swipe day -- which is the behaviour the ruling measured and
    rejected.  The nearest existing test sat seven days off the boundary.
    Found by adversarial test-quality review 2026-08-18.
    """

    def test_a_purchase_made_ON_the_posting_day_is_left_alone(
        self, app, db, seed_user,
    ):
        """Equal is not contradicted."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=6)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=bank_day,
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
            transaction_on=bank_day - timedelta(days=2),
        )

        accepted = _submit(seed_user, lines=[line], entries=[purchase])

        assert purchase.purchased_on == bank_day
        assert accepted.redated_count == 0

    def test_a_purchase_made_ONE_DAY_after_it_is_corrected(
        self, app, db, seed_user,
    ):
        """The first refuted value, so the boundary is pinned from both sides."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=6)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=bank_day + timedelta(days=1),
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
            transaction_on=bank_day - timedelta(days=2),
        )

        accepted = _submit(seed_user, lines=[line], entries=[purchase])

        assert purchase.purchased_on == bank_day - timedelta(days=2)
        assert accepted.redated_count == 1

    def test_a_GROUP_whose_lines_MIX_stated_and_unstated_days(
        self, app, db, seed_user,
    ):
        """``happened_on`` falls back per LINE, not per match.

        Both lines in the existing group test state a day, so nothing graded
        the mix -- which is the ordinary case, since the source states one on
        182 of 361 lines.
        """
        statement = an_import(seed_user)
        first_day = seed_user["bootstrap_period"].start_date
        last_day = first_day + timedelta(days=6)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=last_day + timedelta(days=2),
        )
        a_bank_line(
            seed_user, statement, amount="-10.00", posted_on=first_day,
        )
        stated = a_bank_line(
            seed_user, statement, amount="-15.00", posted_on=last_day,
            transaction_on=last_day - timedelta(days=1),
        )
        early = db.session.query(type(stated)).filter_by(
            amount=Decimal("-10.00"),
        ).one()

        _submit(seed_user, lines=[early, stated], entries=[purchase])

        # The earlier line states nothing, so it falls back to its posting day
        # -- and that is earlier than the later line's stated day.
        assert purchase.purchased_on == first_day


class TestARowCarryingTheBanksDayIsStillWrittenIfItsPurchaseDayMoves:
    """"Unchanged" asks about BOTH clocks, and only a group can prove it.

    :func:`_apply_day` returns early on ``unchanged`` without writing, so a row
    already settled on the bank's day and needing a purchase-day correction
    must not take that arm.

    **The conjunct that says so was DEAD when it was written, and the fix to
    another finding made it live.**  For a single line, ``settled_on ==
    posts_on`` plus ``ck_transaction_entries_settled_not_before_purchase``
    forces ``purchased_on <= posts_on``, so no correction was possible and the
    test could not be built.  Once what REFUTES a purchase day became the
    EARLIEST posted day rather than the latest, a group separates them: settled
    on the LAST line's day, made after the FIRST line's.  Found by adversarial
    test-quality review 2026-08-18, which measured the conjunct as unreachable
    and predicted this case.
    """

    def test_it_is_written_and_reported_as_corrected(
        self, app, db, seed_user,
    ):
        """Delete ``and purchase_day is None`` and the purchase day stops moving."""
        statement = an_import(seed_user)
        first_day = seed_user["bootstrap_period"].start_date
        last_day = first_day + timedelta(days=9)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=first_day + timedelta(days=4),
            settled_on=last_day,
        )
        early = a_bank_line(
            seed_user, statement, amount="-10.00", posted_on=first_day,
        )
        late = a_bank_line(
            seed_user, statement, amount="-15.00", posted_on=last_day,
        )

        accepted = _submit(seed_user, lines=[early, late], entries=[purchase])

        assert purchase.settled_on == last_day, "the cash clock must not move"
        assert purchase.purchased_on == first_day, "the budget clock did not move"
        assert accepted.corrected_count == 1
        assert accepted.settled_count == 0
        assert accepted.redated_count == 1
