"""Each assertion closes its day and is replayed, and the latest asserted day is stated alike."""

from datetime import date
from decimal import Decimal

from app.services.cash_ledger import (
    ReconciledThrough,
    reconciled_through,
    settled_cash_facts,
    walk_cash_ledger,
)
from app.services.balance_at._assertions import assertion_corrections
from app.utils.dates import DISPLAY_TIMEZONE, to_display_date
from tests._test_helpers import (
    account_never_asserted,
    create_savings_account,
    create_settled_cash_transaction,
)

from ._helpers import (
    _instant,
    _opened_at,
    _assert_balance,
    _money_steps,
    _corrections,
    _running_balance,
    _fold_at,
)


class TestTheClosingBalancePartition:
    """An assertion is the CLOSING BALANCE for its civil day (ruling R-DH (a)).

    The production shape, reproduced: opening $1,000.00, then an assertion of
    $2,932.41 on 2026-07-24, with expenses settling either side of it on that
    same civil day.

    **This class asserted the opposite until 2026-07-31**, when the INSTANT
    partition it pinned rendered the developer's own grid at ``-$4,021.37``
    against a true ``-$19.95``: three payments recorded in the nine seconds
    AFTER an anchor were subtracted from a bank balance that already contained
    them.  Neither instant the partition compared is a fact about money --
    the settle day was ``db.func.now()`` at the click, before plan step X-f1, and
    an ``AccountAnchorHistory`` row has no date column at all -- so it decided
    which of two BUTTONS was pressed first and spent that answer on cash.  See
    ``docs/audits/balance_architecture/archive/anchor_settle_partition.md``.
    """

    def test_a_settle_recorded_after_the_assertion_is_still_absorbed(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """$108.15 and $131.60 recorded 10 minutes later move NOTHING.

        Hand-computed: both expenses carry the assertion's own civil day, so the
        day's closing balance is the asserted $2,932.41 and the walk ends there.

        **The ruling, and what it costs and buys.**  It buys the developer's
        actual workflow: read the bank, enter the anchor, then tick off what
        cleared -- an order in which every ticked row is already inside the
        number just entered.  It costs the case where a payment genuinely clears
        AFTER the balance was read on the same day; that one is absorbed here and
        surfaces at the next assertion.  Measured over four months of real data
        the trade is decisive: the correction the model must plug falls from
        ``$40,554.34`` gross / ``-$6,998.90`` net to ``$14,286.82`` /
        ``-$940.06``, and this rule is the only one under which the walk lands on
        the balance the bank actually shows.

        Both fixture rows are PLAIN transactions, so each would move its full
        amount; on the real account the second row's purchases were entirely
        credit-card, so its confirmed cash effect is $0.00 (see
        :class:`TestSourceFactValuation`'s credit-entry case).
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[6]
        _opened_at(account, _instant(2026, 1, 1))
        _assert_balance(
            account, Decimal("2932.41"),
            _instant(2026, 7, 24, 12, 57, 8),
        )
        # Both were RECORDED minutes after the assertion and both carry its own
        # civil day, which is the shape ruling R-DH (a) turns on: the settle day
        # is what partitions, and the recording minute is not a fact about money.
        for amount in (Decimal("108.15"), Decimal("131.60")):
            create_settled_cash_transaction(
                seed_user, db.session, period, amount,
                settled_on=date(2026, 7, 24),
            )
        db.session.commit()

        assert _running_balance(account, scenario) == Decimal("2932.41")

    def test_a_settle_before_the_assertion_is_absorbed(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A $50.00 expense settled 57 minutes EARLIER moves nothing.

        The user asserted $2,932.41 at 12:57:08 having already spent the $50.00
        at 12:00:00, so it is inside the asserted figure.  Hand-computed: the
        assertion's ``balance_before`` is ``1000.00 - 50.00 = 950.00`` and the
        walk ends on the asserted $2,932.41 exactly.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[6]
        _opened_at(account, _instant(2026, 1, 1))
        asserted_at = _instant(2026, 7, 24, 12, 57, 8)
        _assert_balance(account, Decimal("2932.41"), asserted_at)
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("50.00"),
            settled_on=date(2026, 7, 24),
        )
        db.session.commit()

        before, delta = _corrections(account, scenario)[asserted_at]
        assert before == Decimal("950.00")
        assert delta == Decimal("1982.41")
        assert _running_balance(account, scenario) == Decimal("2932.41")

    def test_a_settle_at_exactly_the_assertion_instant_is_absorbed(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The boundary is ``<=``, matching the account posting walk.

        A source dated the assertion's own civil day is subsumed by its reset
        -- the same ``fact.reconciled_through.covers(sources[i][0])`` boundary
        ``ledger_lines.walk_account_ledger`` applies, so the read fold
        and the posted ledger partition one boundary rather than two.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[6]
        _opened_at(account, _instant(2026, 1, 1))
        asserted_at = _instant(2026, 7, 24, 12, 57, 8)
        _assert_balance(account, Decimal("2932.41"), asserted_at)
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("77.00"),
            settled_on=asserted_at.astimezone(DISPLAY_TIMEZONE).date(),
        )
        db.session.commit()

        before, _delta = _corrections(account, scenario)[asserted_at]
        assert before == Decimal("923.00")
        assert _running_balance(account, scenario) == Decimal("2932.41")

    def test_both_same_day_settles_go_with_the_assertion_whatever_the_order(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The discriminating control: one civil day, ONE answer (ruling R-DH (a)).

        Both expenses carry the civil date 2026-07-24, exactly as the assertion
        does -- one recorded hours BEFORE it and one hours AFTER.  The assertion
        is that day's closing balance, so both are inside it and neither moves
        the walk.  Hand-computed: ``balance_before`` is
        ``1000.00 - 40.00 - 60.00 = 900.00`` and the walk ends on the asserted
        $2,932.41.

        **This is the order-independence property, at walk grain.**  The whole
        defect was that these two rows got different treatment for no reason a
        user could see or control; here they get the same one.  An
        instant-keyed implementation cannot pass this test -- it answers
        ``2872.41`` -- which is the same discriminating role the class had
        before, pointed the other way.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[6]
        _opened_at(account, _instant(2026, 1, 1))
        asserted_at = _instant(2026, 7, 24, 12, 57, 8)
        _assert_balance(account, Decimal("2932.41"), asserted_at)
        earlier = create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("40.00"),
            settled_on=date(2026, 7, 24), name="before",
        )
        later = create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("60.00"),
            settled_on=date(2026, 7, 24), name="after",
        )
        db.session.commit()

        facts = {
            fact.transaction_id: fact
            for fact in settled_cash_facts(account.id, scenario.id)
        }
        # One civil day for the assertion and both settles -- which is exactly
        # the information the rule acts on.
        assert facts[earlier.id].settled_on == date(2026, 7, 24)
        assert facts[later.id].settled_on == date(2026, 7, 24)
        assert to_display_date(asserted_at) == date(2026, 7, 24)

        before, _delta = _corrections(account, scenario)[asserted_at]
        assert before == Decimal("900.00")
        assert _running_balance(account, scenario) == Decimal("2932.41")


class TestEveryAssertionIsReplayed:
    """The past is the assertion history, not today's assertion carried back."""

    def test_three_assertions_each_reset_the_running_balance(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Hand-computed corrections and a per-date fold across the history.

        Stream: the account's own origination assertion, $1,000.00 on the
        2024 bootstrap day the seeded Checking was opened on; -$200.00
        (2026-02-01); assert $900.00 (2026-03-01); -$300.00 (2026-04-01);
        assert $500.00 (2026-05-01).  Five events, and the origination is the
        first of them since plan step X-f3c-2c -- the case used to re-stamp it
        onto 2026-01-01, which an append-only table has no act for.

        ``balance_before`` at the March assertion is ``1000 - 200 = 800``, so its
        correction is ``+100.00``; at the May assertion it is ``900 - 300 = 600``,
        so its correction is ``-100.00``.  The account therefore reads
        1000 / 800 / 900 / 600 / 500 across the five event days -- the balance
        the user actually asserted at each point, where the shipping scalar
        answers today's $500.00 for every one of them (finding B-18).

        **Graded through the FOLD rather than by prefix-summing the leaf's dated
        deltas**, which is where plan step X-f3c-1 left the question: the
        assertion steps are the fold's to apply (ruling R-FO gives the modelled
        kinds one answer and plan step X-f3c gives the PLAIN ones another), so
        the five figures live in the producer a screen reads and not in the
        leaf.  The instrument is stronger for it -- ``_fold_at``'s own docstring
        records the review that caught a walk-graded clearing test passing while
        the rendered balance was $500.00 short.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("200.00"),
            settled_on=date(2026, 2, 1), name="feb spend",
        )
        march = _instant(2026, 3, 1)
        _assert_balance(account, Decimal("900.00"), march)
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("300.00"),
            settled_on=date(2026, 4, 1), name="apr spend",
        )
        may = _instant(2026, 5, 1)
        _assert_balance(account, Decimal("500.00"), may)
        db.session.commit()

        corrections = _corrections(account, scenario)
        assert corrections[march] == (Decimal("800.00"), Decimal("100.00"))
        assert corrections[may] == (Decimal("600.00"), Decimal("-100.00"))

        event_days = [
            date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1),
            date(2026, 4, 1), date(2026, 5, 1),
        ]
        assert [
            _fold_at(account, scenario, day) for day in event_days
        ] == [
            Decimal("1000.00"), Decimal("800.00"), Decimal("900.00"),
            Decimal("600.00"), Decimal("500.00"),
        ]
        # Sampling five NAMED dates cannot see a spurious step on a sixth day,
        # which the prefix-sum this replaced did see.  The count and the total
        # are what restore it.
        walk = walk_cash_ledger(account.id, scenario.id)
        assert len(_money_steps(walk)) + len(assertion_corrections(walk)) == 5
        assert _running_balance(account, scenario) == Decimal("500.00")


class TestTheTwoStatementsOfTheLatestAssertedDay:
    """``reconciled_through`` (SQL) == ``CashLedgerWalk.reconciled_through``.

    The arc's central question -- *is this movement already inside the balance
    the user declared?* -- is asked of ONE boundary, and that boundary has two
    statements on purpose (plan step S1-c, 12.9):

    * :attr:`~app.services.cash_ledger.CashLedgerWalk.reconciled_through` reads
      the last element of a list the walk already holds, so the seam's cash fold
      pays no query for a day it is standing on; and
    * :func:`~app.services.cash_ledger.reconciled_through` is one indexed
      ``MAX`` for the callers that hold no walk -- the posting self-heal's skip
      predicate, the entry list's reconciled indicator, and the reconcile panel,
      none of which should walk an account to render one row.

    **A THIRD statement is what this pins against.**  ``account_posting_service``
    grew its own (``MAX(created_at)`` as an instant, compared against a civil
    date pushed through midnight UTC) and it carried a silent timezone-sign
    dependency for the whole time it lived -- finding N-133 / F4.  Plan step S1-c
    deleted that copy; these two are what remain, and "two statements that
    happen to agree" is precisely the shape this arc exists to remove, so the
    agreement is pinned rather than argued.

    The shapes below are the ones the two could disagree on: several assertions,
    a BUSINESS day that does not follow the recording order, and none at all.
    """

    def test_they_agree_on_an_account_with_several_assertions(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Three assertions on three different days -- both answer the last.

        Hand-computed: the account's own origination, a re-assertion on
        2026-01-01 and true-ups on 02-15 and 03-20, so the latest asserted day
        is 2026-03-20.  The figure is
        asserted absolutely as well as for equality: two implementations that
        both returned the OPENING would agree with each other and be wrong
        together, which equality alone cannot catch.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        _assert_balance(
            account, Decimal("1200.00"),
            _instant(2026, 2, 15, 9, 0, 0),
        )
        _assert_balance(
            account, Decimal("1400.00"),
            _instant(2026, 3, 20, 9, 0, 0),
        )
        db.session.commit()

        walk = walk_cash_ledger(account.id, scenario.id)

        assert walk.reconciled_through == ReconciledThrough(date(2026, 3, 20))
        assert reconciled_through(account.id) == ReconciledThrough(
            date(2026, 3, 20),
        )

    def test_they_agree_when_the_business_day_defies_the_recording_order(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The shape that broke the retired third statement, reproduced.

        ``observed_on`` has been USER-SUPPLIED since plan step 2, so a user can
        correct a balance for an EARLIER day after recording a later one: here
        the 03-20 reading is entered first and a forgotten 02-15 reading is
        back-filled afterwards.  The answer is the latest BUSINESS day
        (2026-03-20), not the latest recording instant.

        An implementation ordering by ``created_at`` -- which is what the
        deleted ``account_posting_service`` copy did, and what the walk's own
        loader did before ``observed_on`` existed -- answers 2026-02-15 here and
        would reconcile purchases against a balance five weeks stale.  Both
        statements are asserted against the literal, so neither can drift onto
        the recording clock without failing.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        # Recorded FIRST, for the LATER business day.
        _assert_balance(
            account, Decimal("1400.00"),
            _instant(2026, 3, 20, 9, 0, 0),
        )
        # Recorded SECOND, for an EARLIER business day -- the back-fill.  The
        # two clocks MUST be given separately here: the fixture built both from
        # one instant until an adversarial review proved that made this test
        # blind (the recording order and the business order agreed row for row,
        # so a loader ordering by ``created_at`` answered the same thing and
        # the test could not fail).
        _assert_balance(
            account, Decimal("1200.00"),
            _instant(2026, 2, 15, 9, 0, 0),
            recorded_at=_instant(2026, 4, 1, 9, 0, 0),
        )
        db.session.commit()

        walk = walk_cash_ledger(account.id, scenario.id)

        assert walk.reconciled_through == ReconciledThrough(date(2026, 3, 20))
        assert reconciled_through(account.id) == ReconciledThrough(
            date(2026, 3, 20),
        )

    def test_they_agree_that_an_unasserted_account_has_no_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Both answer ``None``, which reconciles nothing.

        The honest answer for an account with no declared balance to be inside
        of, and the arm ``ReconciledThrough.covers`` is total over.  A statement
        that raised, or returned a sentinel date, would make every caller carry
        a precondition -- and one of them would forget.
        """
        # Built rather than emptied, for the reason the case above states.
        scenario = seed_user["scenario"]
        account = account_never_asserted(
            seed_user, db.session, name="Never asserted",
            opening_equity=Decimal("0.00"),
        )
        db.session.commit()

        walk = walk_cash_ledger(account.id, scenario.id)

        assert walk.reconciled_through == ReconciledThrough(None)
        assert reconciled_through(account.id) == ReconciledThrough(None)
        assert not walk.reconciled_through.covers(date(2026, 3, 20))

    def test_the_sql_form_answers_for_ONE_account(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A second account's later assertion does not become this one's.

        ``reconciled_through`` is a ``MAX`` and the account scope is the only
        thing making it an ACCOUNT's boundary rather than the database's.  An
        adversarial review deleted that ``.filter`` and the whole 7,726-test
        suite stayed green, because every fixture in this class holds exactly
        one asserted account -- the shape a scoping clause is invisible to.

        What it would cost is not abstract.  The three consumers are the entry
        list's reconciled indicator, the posting self-heal's skip, and the
        reconcile panel -- and the panel uses this day as an SQL bound AND
        STAMPS it onto every ticked purchase as its posting day.  Unscoped, a
        savings account trued up today would reconcile a checking envelope's
        purchases against a balance checking never declared, emptying the
        reservation and writing the wrong ``settled_on`` to the row.

        Hand-computed: Checking asserts for 2026-03-01, Savings for 2026-09-09
        five months later.  Checking's boundary is 2026-03-01, and it does NOT
        cover a purchase posted 2026-09-09.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        _assert_balance(
            account, Decimal("1000.00"),
            _instant(2026, 3, 1, 9, 0, 0),
        )
        other = create_savings_account(
            seed_user, db.session, "Other", Decimal("5000.00"),
        )
        _assert_balance(
            other, Decimal("5000.00"),
            _instant(2026, 9, 9, 9, 0, 0),
        )
        db.session.commit()

        boundary = reconciled_through(account.id)

        assert boundary == ReconciledThrough(date(2026, 3, 1))
        assert not boundary.covers(date(2026, 9, 9))
        # The walk's in-memory twin is scoped by its own account_id argument,
        # so the two must still agree -- which is what makes the SQL form's
        # scope checkable against something rather than against itself.
        assert walk_cash_ledger(account.id, scenario.id).reconciled_through == (
            boundary
        )
        assert reconciled_through(other.id) == ReconciledThrough(
            date(2026, 9, 9),
        )
