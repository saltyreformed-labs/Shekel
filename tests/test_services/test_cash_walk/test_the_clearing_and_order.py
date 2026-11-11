"""A clearing fact across a statement, the source order, and the same-day tie-break."""

from datetime import date
from decimal import Decimal

from app.services import status_seam
from app.services.cash_ledger import settled_cash_facts, walk_cash_ledger
from tests._test_helpers import (
    create_settled_cash_transaction,
    figure_source_columns,
    one_off_row_of,
    settle_cash_row,
    settle_day_columns,
)

from ._helpers import _instant, _opened_at, _assert_balance, _money_facts, _corrections, _fold_at


class TestARecordedClearingFactMayNotMoveALineAcrossAStatement:
    """A link may sharpen WITHIN a day; across one the date rule answers.

    Ruling **R-FL** records which statement showed a line.  While an assertion
    RESETS the ledger, that record is not free to disagree with the day, and the
    constraint is a theorem about the fold rather than a policy -- see
    ``StatementCoverage._recorded_anchor_id``, which carries the derivation.

    **A first implementation of this step let the link outrank the date and an
    adversarial review refuted it with money.**  Measured on a production clone:
    moving one ``$500.00`` source's link to a later assertion made the fold
    render ``$2,246.58`` on 2026-03-27 for an account whose owner had asserted
    ``$2,746.58`` that day -- ruling **R-S** ("an assertion always wins") broken
    on the assertion's own day.  The mirror direction reads ``anchor + X``,
    which is the ``$4,001.42`` class.  The tests below grade the refusal AND the
    balance the refusal protects, because the earlier version graded
    ``balance_before`` alone and was green while the rendered balance was wrong.
    """

    def test_a_link_across_a_statement_boundary_does_not_move_the_balance(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The state the fold cannot render answers the DATE rule instead.

        Opening $1,000.00 on 01-01, a $100.00 expense whose cash moved 02-10,
        and two assertions closing 02-15 and 02-28.  The date rule puts the
        expense inside the 02-15 balance; a link to the 02-28 statement claims
        otherwise, and no arrangement of the fold's steps satisfies both that
        claim and the 02-15 assertion's own balance.

        **Honouring it was measured**: on a production clone one such link made
        the fold render ``$2,246.58`` on a day its owner had asserted
        ``$2,746.58``.  So the record is kept on the row and the date rule
        answers, and the balance is what the user declared -- with the link
        present and without it.

        **It does not RAISE, and an adversarial review's own question is why.**
        A user may record a BACK-DATED assertion between a line's settle day and
        its statement's, which re-points the date rule and strands a link that
        was consistent when it was written.  A refusal would be a 500 on every
        screen showing that account, reached by an ordinary act.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        txn = create_settled_cash_transaction(
            seed_user, seed_periods[0], Decimal("100.00"),
            settled_on=date(2026, 2, 10), name="late-posting debit",
        )
        first_at = _instant(2026, 2, 15, 9, 0, 0)
        _assert_balance(
            account, Decimal("2000.00"), first_at,
        )
        later = _assert_balance(
            account, Decimal("3000.00"),
            _instant(2026, 2, 28, 9, 0, 0),
        )
        db.session.commit()

        control = _corrections(account, scenario)[first_at][0]
        control_balance = _fold_at(account, scenario, date(2026, 2, 15))

        txn.reconciled_by_id = later.id
        db.session.commit()

        assert _corrections(account, scenario)[first_at][0] == control
        assert _fold_at(account, scenario, date(2026, 2, 15)) == control_balance
        assert _fold_at(account, scenario, date(2026, 2, 15)) == Decimal(
            "2000.00",
        ), (
            "The 02-15 assertion is still that day's closing balance -- ruling "
            "R-S, which honouring the link would have broken by $100.00."
        )

    def test_an_assertion_BACK_DATED_under_a_link_moves_no_balance(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The act that strands a link is ordinary, and it costs nothing.

        A user ticks a line on the 02-28 statement -- consistent when written,
        because 02-28 is then the first assertion closing over its 02-10 settle
        day -- and later records a balance they read for 02-20.  The date rule
        re-points to 02-20 and the stored link is now the wrong side of it.

        This is the case that decided the rule must FALL BACK rather than
        refuse: every screen for this account would otherwise 500, with no
        in-app repair, after two acts the app invites.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        txn = create_settled_cash_transaction(
            seed_user, seed_periods[0], Decimal("100.00"),
            settled_on=date(2026, 2, 10), name="ticked on the 28th",
        )
        ticked = _assert_balance(
            account, Decimal("3000.00"),
            _instant(2026, 2, 28, 9, 0, 0),
        )
        txn.reconciled_by_id = ticked.id
        db.session.commit()

        assert _fold_at(account, scenario, date(2026, 2, 28)) == Decimal(
            "3000.00",
        )

        _assert_balance(
            account, Decimal("2500.00"),
            _instant(2026, 2, 20, 9, 0, 0),
        )
        db.session.commit()

        assert _fold_at(account, scenario, date(2026, 2, 20)) == Decimal(
            "2500.00",
        )
        assert _fold_at(account, scenario, date(2026, 2, 28)) == Decimal(
            "3000.00",
        )

    def test_a_link_WITHIN_a_statement_day_is_admitted_and_moves_nothing(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Two assertions on ONE day: the link picks between them, freely.

        This is the case the reconcile panel actually writes -- it links to the
        GOVERNING assertion while the date rule takes the FIRST of that day --
        and production carries three days on which Checking holds more than one
        assertion, so it is ordinary rather than exotic.

        Hand-computed.  Opening $1,000.00 on 01-01, a $100.00 expense whose cash
        moved 02-10, and two assertions both closing 02-15: $2,000.00 recorded
        at 09:00 and $2,500.00 at 17:00.  Linked to the SECOND, the split moves:

            first  balance_before = 1000.00            (nothing cleared)
            second balance_before = 2000.00 - 100.00 = 1900.00

        against 900.00 / 2000.00 unlinked.  The corrections differ and the
        RENDERED balance does not, which is the whole licence for admitting this
        case: the fold reads a day's boundary after every step on it, so a
        source moved between two assertions sharing a day is unobservable in any
        balance.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        txn = create_settled_cash_transaction(
            seed_user, seed_periods[0], Decimal("100.00"),
            settled_on=date(2026, 2, 10), name="ticked on the second reading",
        )
        first_at = _instant(2026, 2, 15, 9, 0, 0)
        second_at = _instant(2026, 2, 15, 17, 0, 0)
        _assert_balance(
            account, Decimal("2000.00"), first_at,
        )
        governing = _assert_balance(
            account, Decimal("2500.00"), second_at,
        )
        db.session.commit()

        control = _corrections(account, scenario)
        assert control[first_at][0] == Decimal("900.00"), (
            "CONTROL: unlinked, the date rule puts the expense in the FIRST "
            "assertion of that day -- 1000.00 - 100.00."
        )
        assert control[second_at][0] == Decimal("2000.00")
        control_balance = _fold_at(account, scenario, date(2026, 2, 15))

        # Through the panel's own writer (plan step X-bi-3a): the link has to
        # reach the covering movement whose fact carries the money, which a
        # bare assignment on the row no longer does.
        status_seam.record_clearing(txn, governing.id)
        db.session.commit()

        corrections = _corrections(account, scenario)
        assert corrections[first_at][0] == Decimal("1000.00")
        assert corrections[second_at][0] == Decimal("1900.00")
        assert _fold_at(account, scenario, date(2026, 2, 15)) == control_balance
        assert _fold_at(account, scenario, date(2026, 2, 15)) == Decimal(
            "2500.00",
        ), (
            "The day's LAST assertion is that day's closing balance, linked or "
            "not -- ruling R-S, which is what the refusal above protects."
        )

    def test_a_PURCHASE_carries_its_OWN_link_not_its_parents(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The per-purchase clearing path, which nothing else in the suite reads.

        Plan step X-f3b (ruling **R-FM**) made a purchase a cash movement of its
        own, and with it a SECOND row type carrying ``reconciled_by_id``.  The
        chain from that column to the fold -- ``_posted_purchase_facts`` ->
        ``CashSourceFact.reconciled_by_id`` -> ``StatementCoverage`` -- is read
        by no other test, so a regression that dropped a purchase's link, or
        read its PARENT's, would be silent everywhere.

        **It is the WITHIN-A-DAY shape deliberately**, and the sibling test
        above is why: across a statement boundary the record may not move a
        line, so a dropped link answers identically and a test built on that
        shape cannot fail.  Two assertions sharing 02-15 is the case the link
        genuinely decides -- and the case the reconcile panel actually writes.

        Hand-computed.  Opening $1,000.00 on 01-01, a $100.00 purchase the bank
        took on 02-10 under an envelope that has NOT settled (so it carries no
        link of its own to borrow), and two assertions both closing 02-15:
        $2,000.00 at 09:00 and $2,500.00 at 17:00.  Linked to the SECOND:

            first  balance_before = 1000.00            (nothing cleared)
            second balance_before = 2000.00 - 100.00 = 1900.00

        against 900.00 / 2000.00 unlinked -- the same split its transaction twin
        makes, from the purchase's own column.
        """
        from app.models.transaction_entry import TransactionEntry  # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_envelope_txn  # pylint: disable=import-outside-toplevel

        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        txn = create_envelope_txn(
            seed_user, db.session, seed_periods[0], "Groceries",
            Decimal("500.00"),
        )
        entry = TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
            user_id=seed_user["user"].id,
            amount=Decimal("100.00"),
            description="ticked on the second reading",
            purchased_on=date(2026, 2, 10),
            **settle_day_columns(date(2026, 2, 10)),
            is_credit=False,
        )
        db.session.add(entry)
        db.session.flush()
        first_at = _instant(2026, 2, 15, 9, 0, 0)
        second_at = _instant(2026, 2, 15, 17, 0, 0)
        _assert_balance(
            account, Decimal("2000.00"), first_at,
        )
        governing = _assert_balance(
            account, Decimal("2500.00"), second_at,
        )
        db.session.commit()

        # The purchase really is a fact of the walk, dated its OWN day and
        # carrying its OWN (empty) link -- otherwise every figure below would be
        # true of a fold that never saw it.
        fact, = [
            source for source in walk_cash_ledger(
                account.id, scenario.id,
            ).source_facts
            if source.entry_id == entry.id
        ]
        assert (fact.settled_on, fact.delta, fact.reconciled_by_id) == (
            date(2026, 2, 10), Decimal("-100.00"), None,
        )

        control = _corrections(account, scenario)
        assert control[first_at][0] == Decimal("900.00"), (
            "CONTROL: unlinked, the date rule puts the purchase in the FIRST "
            "assertion of that day -- 1000.00 - 100.00."
        )
        assert control[second_at][0] == Decimal("2000.00")
        control_balance = _fold_at(account, scenario, date(2026, 2, 15))

        entry.reconciled_by_id = governing.id
        db.session.commit()

        corrections = _corrections(account, scenario)
        assert corrections[first_at][0] == Decimal("1000.00"), (
            "the purchase's OWN link moved it to the governing assertion; "
            "reading the parent's (there is none) would leave it here"
        )
        assert corrections[second_at][0] == Decimal("1900.00")
        assert _fold_at(account, scenario, date(2026, 2, 15)) == control_balance
        assert _fold_at(account, scenario, date(2026, 2, 15)) == Decimal(
            "2500.00",
        ), (
            "The day's LAST assertion is that day's closing balance, linked or "
            "not -- ruling R-S."
        )


class TestTheSourceOrderIsLoadBearing:
    """A source with no recorded statement lands on the first assertion to close.

    The ASSERTION order is what carries this now.
    ``cash_anchor_facts`` returns its rows ``(observed_on, created_at, id)``
    ascending and :func:`~app.services.cash_ledger.statement_coverage` BISECTS
    that day list, so a fact list not ascending in ``observed_on`` sends an
    unlinked source to the wrong statement.  Nothing re-sorts downstream: the
    read side used to, inside the deleted ``merge_anchor_and_cash_events``, and
    the one-partition step removed it on ruling N-133 / R1's "one ordering,
    stated where the rows are read".

    **That made the loaders' sorts load-bearing for the first time, and an
    adversarial review proved nothing tested them**: dropping ``settled_on``
    from ``settled_cash_facts``' key left all 7,726 tests green.  The MECHANISM
    it broke was a monotonic pointer, which plan step X-f3a-1 deleted -- a
    recorded clearing fact is not monotone in the day, so a pointer could not
    survive one (``TestARecordedClearingFactOutranksTheDay`` grades that).  What
    survives is this: the SOURCE order no longer decides which assertion
    absorbs what, and the case below now grades the rule's day arm directly
    rather than the pointer's precondition.  It is kept, and kept green by a
    different mechanism, because the property it names -- a movement the
    declared balance already contains is not subtracted from the projection a
    second time -- is the ``-$4,001.42`` production defect this whole document
    exists about.

    The discriminating shape is an id order that DISAGREES with the day order,
    which is ordinary: a purchase entered late carries a higher id and an
    earlier settle day.
    """

    def test_a_later_id_on_an_earlier_day_is_still_absorbed(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A source recorded second, for an earlier day, is inside the assertion.

        Hand-computed.  Opening $1,000.00 on 01-01.  Two settled expenses on
        one account: $50.00 whose cash moved 02-20 (recorded FIRST, lower id)
        and $100.00 whose cash moved 02-10 (recorded SECOND, higher id).  An
        assertion closes 02-15, so it absorbs the 02-10 row and NOT the 02-20
        one:

            balance_before = 1000.00 - 100.00 = 900.00

        **What this discriminates changed at plan step X-f3a-1 and the figure
        did not.**  It caught a monotonic pointer halting at the 02-20 row --
        the assertion would book ``$1,000.00`` and carry the $100.00 already
        inside the declared balance forward to be subtracted again.  That
        mechanism is gone; the rule now assigns each source independently, so
        this case grades the DAY arm itself: each source lands on the first
        assertion to close on or after it, whatever order the loader returned
        them in.  The expected figure is unchanged, which is the point of
        keeping it.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, seed_periods[0], Decimal("50.00"),
            settled_on=date(2026, 2, 20), name="recorded first",
        )
        create_settled_cash_transaction(
            seed_user, seed_periods[0], Decimal("100.00"),
            settled_on=date(2026, 2, 10), name="recorded second",
        )
        asserted_at = _instant(2026, 2, 15, 9, 0, 0)
        _assert_balance(
            account, Decimal("2000.00"), asserted_at,
        )
        db.session.commit()

        before, _delta = _corrections(account, scenario)[asserted_at]

        # The MONEY first, so a regression reads as a wrong balance rather than
        # as a changed sort -- the ordering below is the mechanism, not the
        # property.
        assert before == Decimal("900.00")
        # The facts themselves arrive in DAY order, id breaking a same-day tie.
        assert [fact.settled_on for fact in _money_facts(settled_cash_facts(
            account.id, scenario.id,
        ))] == [date(2026, 2, 10), date(2026, 2, 20)]


class TestTheSameDayTieBreakIsTheMovementId:
    """Two movements on ONE day are ordered by the movement's own id (leaf ``X-bi-6-4d-1``).

    The declared change: ``settled_cash_facts`` broke a same-day tie on the
    parent row's id first, ``(settled_on, transaction_id, entry_id)``, and a
    transfer leg's ``transaction_id`` (its shadow's) is NULL once
    ``X-bi-6-4d`` re-parents it, which does not sort beside an ``int``.  It
    is ``(settled_on, entry_id)`` now.  The discriminating shape is a row
    created FIRST and settled SECOND, so its row id is the lower and its
    movement id the higher: the old key put it first, this one puts it
    second.  No balance moves (the walk sums a day); the one reader that
    shows the order is the bank-agreement listing.
    """

    def test_the_row_settled_second_sorts_second(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Electric created first and settled second, both on 02-10."""
        # pylint: disable=import-outside-toplevel  -- the module convention.
        from app import ref_cache
        from app.enums import TxnTypeEnum

        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        first_created = one_off_row_of(
            seed_periods[0], name="Electric", amount=Decimal("60.00"),
            user_id=seed_user["user"].id, account_id=account.id,
            scenario_id=scenario.id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        )
        settled_first = create_settled_cash_transaction(
            seed_user, seed_periods[0], Decimal("40.00"),
            settled_on=date(2026, 2, 10), name="Water",
        )
        settle_cash_row(first_created, settled_on=date(2026, 2, 10))
        db.session.commit()
        assert first_created.id < settled_first.id

        facts = [
            fact for fact in settled_cash_facts(account.id, scenario.id)
            if fact.settled_on == date(2026, 2, 10)
        ]

        assert [fact.transaction_id for fact in facts] == [
            settled_first.id, first_created.id,
        ]
        assert facts[0].entry_id < facts[1].entry_id
