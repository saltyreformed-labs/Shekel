"""Shekel Budget App -- the cash ledger WALK (plan step X-a).

Grades ``app.services.cash_ledger.walk_cash_ledger`` and its visible-day re-key
``dated_deltas``, and the fold-side assertion replay that left it at plan step
X-f3c-1 (``balance_at._assertions.assertion_corrections``) -- the facts a cash
account's balance folds over (step X-b)
and the posting writer will project (step X-d).  The walk is ADDITIVE at X-a: no
production surface reads it yet, so nothing here can move a shipped balance.

Every expected figure below is HAND-COMPUTED and written out in the test that
asserts it.  None is taken from another producer: the whole point of the leaf is
that the shipping projection is WRONG about these cases (findings cash D1-D3), so
grading it against that projection would prove the defect rather than the fix
(plan Section 7.2, finding N-7).

The load-bearing case is :class:`TestTheInstantPartition`.  It reproduces the
shape measured on production 2026-07-25: an assertion at 12:57:08 UTC with two
expenses settled at 13:07:11 and 13:07:18 -- the SAME UTC civil day, ten minutes
later.  A date-keyed partition absorbs them into the assertion and loses their
$108.15 of confirmed cash effect; the instant-keyed walk rides them on top.  The
two are separated by a test that a date-keyed implementation cannot pass, not by
a comment.
"""

from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

from app.extensions import db
from app.services import status_seam
from app.services.cash_ledger import (
    ReconciledThrough,
    cash_anchor_facts,
    dated_deltas,
    reconciled_through,
    settled_cash_facts,
    walk_cash_ledger,
)
from app.services.balance_at._assertions import assertion_corrections
from app.services.balance_at._cash_fold import assembled_fold, balances_at
from app.services import transaction_service
from app.services.row_valuation import purchases_total, settled_figure
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.utils.dates import DISPLAY_TIMEZONE, display_today, to_display_date
from tests._test_helpers import (
    account_never_asserted,
    add_txn,
    an_entered_day,
    append_balance_assertion,
    create_savings_account,
    create_settled_cash_transaction,
    create_settled_transfer,
    figure_source_columns,
    freeze_today,
    one_off_row_of,
    open_books_before_the_first_assertion,
    read_pass,
    reassert_balance_on,
    restate_account_opening,
    settle_cash_row,
    settle_day_columns,
)
from app.services.settle_day import record_settle_day


def _instant(year, month, day, hour=0, minute=0, second=0):
    """Return the aware-UTC instant of a wall-clock moment on the USER's day.

    The arguments are read as the DISPLAY timezone -- the clock the user is
    actually looking at -- and converted to UTC for storage, which is the
    direction production runs in: the settle day and ``created_at`` are stamped
    when the user acts and stored UTC.

    **It read them as UTC until ruling R-DH (b)** (2026-07-31), and the default
    ``hour=0`` then meant midnight UTC -- 7pm or 8pm the PREVIOUS Eastern day.
    So a fixture writing ``date(2026, 1, 15)`` to mean "this settled on the
    15th" pinned an event the fold correctly places on the 14th, and five tests
    in this class asserted figures for a day their own setup had not built.
    Reading the arguments as Eastern makes the helper mean what every call site
    already said it meant, and it preserves same-day ORDERING exactly: two
    moments on one day shift by the same offset.
    """
    return datetime(
        year, month, day, hour, minute, second, tzinfo=DISPLAY_TIMEZONE,
    ).astimezone(timezone.utc)


def _opened_at(account, at, books_open_on=None):
    """Open the account's books and assert its balance on *at*'s day (builder).

    **TWO acts, and they are spelled as two** (plan step X-f3c-2c).  It used to
    be one, because ``restamp_opening_assertion`` re-dated the stored
    origination assertion and restated the opening as a side effect.  An
    assertion is append-only, so a fixture has the pair a production owner has:
    say what the balance was on a day, and say when the books opened.

    **The books move BACKWARD BY DEFAULT, and the first draft of this helper
    moved them forward unconditionally.**  It restated them to the day before
    *at* whatever else stood, which shoved them two years FORWARD past the
    seeded account's own origination at ~25 sites that had asked for no such
    thing.  Found by the adversarial review of this step.

    Args:
        account: The account to open.
        at: The aware-UTC instant of the assertion.
        books_open_on: The civil day the books open.  ``None``, the default,
            bounds them before every day a row could land on
            (``open_books_before_the_first_assertion``), which is
            backward-only.  A case whose SUBJECT is the books' own day names
            it -- the two below that grade a movement dated on or before them
            do, because the day is the thing under test rather than a
            by-product.

    Returns:
        The appended assertion row.
    """
    row = reassert_balance_on(db.session, account, at)
    if books_open_on is None:
        open_books_before_the_first_assertion(db.session, account)
    else:
        restate_account_opening(db.session, account, books_open_on)
    return row


def _assert_balance(account, balance, at, recorded_at=None):
    """Append one balance ASSERTION (true-up) at a pinned instant (shared).

    *recorded_at* separates the two clocks: *at* supplies the BUSINESS day,
    *recorded_at* the moment it was typed.  They are equal unless a test is
    exercising a back-dated assertion.
    """
    return append_balance_assertion(
        db.session, account, balance, at, recorded_at=recorded_at,
    )


def _money_facts(facts):
    """Return the facts that CARRY money -- a covered row's own fact is zero.

    Plan step **X-bi-3a**: a settled bill's money sits on the covering
    movement the seam writes for it, so the fold emits the bill's own fact at
    ``0.00`` beside the movement's fact carrying the figure on the same day.
    Every day and every delta a case below asserts is unchanged; what the
    shape assertions read is the steps that move the balance.
    ``balance:X-bi-4`` re-points the fold onto movements and the zero step
    goes with the row's leg.
    """
    return [fact for fact in facts if fact.delta]


def _money_steps(walk):
    """The walk's ``(day, delta)`` steps that carry money -- see :func:`_money_facts`."""
    return [(day, delta) for day, delta in dated_deltas(walk) if delta]


def _corrections(account, scenario):
    """Return ``{asserted_at: (balance_before, delta)}`` for readable asserts.

    Through ``balance_at._assertions.assertion_corrections``, which owns the
    RESET replay since plan step X-f3c-1 -- the walk states the facts and the
    fold states what an assertion does to a running total (ruling R-FO gives
    the modelled kinds one answer and plan step X-f3c gives the PLAIN ones
    another, so a kind-blind walk could state neither).  Every figure below is
    unchanged by that move, which is what makes it a move.
    """
    walk = walk_cash_ledger(account.id, scenario.id)
    return {
        correction.anchor.asserted_at: (
            correction.balance_before,
            correction.anchor.anchor_balance - correction.balance_before,
        )
        for correction in assertion_corrections(walk)
    }


def _running_balance(account, scenario):
    """Return the replay's final running balance from the fold's own three tiers.

    The account's stored OPENING EQUITY, plus ``dated_deltas`` (the SOURCE
    facts), plus the assertion corrections -- which is exactly what
    ``_cash_fold._actual_steps`` merges.  It was ``dated_deltas`` alone until
    plan step X-f3c-1, when the assertion steps left the leaf because applying
    them is a policy an account's KIND decides (see :func:`_corrections`).

    **The SEED term arrived at plan step X-f3c-2a and its absence before then
    was not an omission**: the fold seeded at the opening's own correction and
    booked an equal-and-opposite compensator, so the two cancelled and the
    total equalled the zero-seeded walk.  With opening equity a stored fact
    there is no compensator, so the seed is a real term and a reconstruction
    that drops it is short by the whole of it.

    **This total is INVARIANT in the opening equity, and saying so is the
    honest version of a claim an earlier draft got wrong.**  The first
    assertion's delta is ``A - E - cleared``, so the leading ``E`` cancels
    against the ``-E`` inside it: perturb the stored equity by any amount and
    every figure here is unchanged.  That is a real property of the REPLAY --
    an assertion resets the total, so what preceded it cannot survive -- and it
    is exactly why "the figures did not move" is NOT evidence that this step
    moved no money.  A control on the SEED has to read a date BEFORE the first
    assertion, which is what ``test_cash_fold.py``'s
    :class:`TestTheOpeningEquityIsTheSeed` does.

    :class:`TestTheStepsReconstructTheReplay` proves this equals the replay's
    own terminal balance by reconstructing that balance INDEPENDENTLY from the
    last assertion FACT + the post-assertion ``source_facts``, so this helper
    is a convenience rather than the only statement of the total.
    """
    walk = walk_cash_ledger(account.id, scenario.id)
    return walk.opening.opening_equity + sum(
        (delta for _day, delta in dated_deltas(walk)),
        Decimal("0.00"),
    ) + sum(
        (correction.delta for correction in assertion_corrections(walk)),
        Decimal("0.00"),
    )


def _fold_at(account, scenario, day):
    """Return the balance a SCREEN renders for *account* on *day*.

    The real producer (the pass's own ``assembled_fold``, sampled by
    ``balances_at``), not a hand-rolled prefix sum, because the property under
    test is what a user sees
    and a replay written beside the thing it grades can agree with a wrong
    answer.

    **This module's other helpers read the WALK, and that difference is what an
    adversarial review caught.**  ``balance_before`` is an INPUT to a
    correction; the fold prefix-sums ``dated_deltas``, which emits each source
    at its OWN settle day.  A first version of the clearing-link tests graded
    the corrections alone, and was green while the rendered balance on an
    assertion's own day was ``$500.00`` short of what the user had asserted.

    Args:
        account: The account to value.
        scenario: Its scenario.
        day: The valuation date, passed as the reader's as-of too -- the
            ordinary case, and it keeps the PLANNED tier out of the way (ruling
            R-G clamps a plan to ``as_of + 1``).

    Returns:
        The folded balance as a ``Decimal``.
    """
    ctx = read_pass(account, scenario, day)
    return balances_at(assembled_fold(account, ctx), [day])[day]


def _replay_terminal_balance(account, scenario):
    """Reconstruct the walk's end balance WITHOUT :func:`dated_deltas`.

    The independent reference: take the LAST assertion's asserted balance and
    add every source dated strictly AFTER the day that assertion closes.  That
    is the replay's definition (ruling R-DH (a)) read off
    :class:`CashLedgerWalk`'s two FACT lists directly -- no correction, no step
    list -- so comparing it against the summed steps is a real cross-check
    rather than a producer graded on itself.

    The comparison is ``>`` on the civil DAY, not on an instant: a source
    sharing the assertion's day is inside the closing balance, so only a
    strictly later day rides on top.
    """
    walk = walk_cash_ledger(account.id, scenario.id)
    if not walk.anchor_facts:
        return Decimal("0.00")
    last = walk.anchor_facts[-1]
    return last.anchor_balance + sum(
        (
            fact.delta for fact in walk.source_facts
            if fact.settled_on > last.observed_on
        ),
        Decimal("0.00"),
    )


def _linked_ledger_net(account, scenario, *, transaction_id=None):
    """Return the net posted on *account*'s LINKED ledger, optionally per row.

    The posted-side window the walk's deltas are graded against.  Reads a
    DIFFERENT join shape from anything production runs, so the two cannot share
    a lookup bug -- the same rule the loan reconciliation suite follows.

    **``transaction_id`` scopes to a row's whole posted FAMILY** since plan step
    X-f3b (ruling **R-FM**): every leg the row's movements -- its covering
    movement and its PURCHASES -- booked on their own days, found by the row's
    id through a ``transaction_entries`` subquery, where the production
    reconcile reads one movement's ``transaction_entry_id`` at a time; that is
    what keeps the grading window a different shape from it.  The row's own
    cash leg, linked by ``journal_entries.transaction_id``,
    was unioned beside them until plan step ``balance:X-bi-6-5``'s second leaf
    (card plan#1845) deleted that link.
    """
    from app.models.journal_entry import JournalEntry, Posting  # pylint: disable=import-outside-toplevel
    from app.models.transaction_entry import TransactionEntry  # pylint: disable=import-outside-toplevel
    from app.services.posting_reads import _ledger_account_for  # pylint: disable=import-outside-toplevel

    linked = _ledger_account_for(account.id)
    query = (
        db.session.query(db.func.coalesce(db.func.sum(Posting.amount), 0))
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == linked.id,
            JournalEntry.scenario_id == scenario.id,
        )
    )
    if transaction_id is not None:
        query = query.filter(
            JournalEntry.transaction_entry_id.in_(
                db.session.query(TransactionEntry.id).filter(
                    TransactionEntry.transaction_id == transaction_id,
                )
            ),
        )
    return Decimal(str(query.scalar()))


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
        ``account_posting_service.walk_account_ledger`` applies, so the read fold
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


class TestSourceFactValuation:
    """A settled row is worth its ``effective_amount``, signed by direction."""

    def test_income_adds_and_expense_subtracts(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """+$250.00 income and -$75.00 expense: 1000 + 250 - 75 = 1175.00."""
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("250.00"),
            is_income=True, settled_on=date(2026, 2, 1), name="pay",
        )
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("75.00"),
            settled_on=date(2026, 2, 2), name="spend",
        )
        db.session.commit()

        assert _running_balance(account, scenario) == Decimal("1175.00")

    def test_actual_amount_wins_over_the_estimate(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A $100.00 estimate that settled at $84.20 is worth $84.20.

        ``effective_amount`` prefers ``actual_amount`` when populated, so the
        walk values a settled row at what really moved: 1000 - 84.20 = 915.80.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("100.00"),
            settled_amount=Decimal("84.20"),
            settled_on=date(2026, 2, 1), name="spend",
        )
        db.session.commit()

        assert _running_balance(account, scenario) == Decimal("915.80")

    def test_a_still_projected_row_is_not_in_the_walk(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """PLANNED rows belong to the reader, not the clock-free walk (R-G).

        A projected row's effective date is ``max(its date, as_of + 1d)``, which
        depends on the reader's as-of; putting it in the walk would make this
        leaf's output a function of the wall clock -- the corruption shape plan
        step A3 removed from the loan walk.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel

        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        one_off_row_of(
            period,
            name="unpaid bill",
            amount=Decimal("500.00"),
            user_id=period.user_id,
            account_id=account.id,
            scenario_id=scenario.id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        )
        db.session.commit()

        assert settled_cash_facts(account.id, scenario.id) == []
        assert _running_balance(account, scenario) == Decimal("1000.00")

    def test_a_credit_card_entry_never_leaves_checking(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """An envelope's CREDIT entries are excluded from the cash effect.

        The shape this valuation exists for, and the one an ``effective_amount``
        walk gets wrong.  A $200.00 grocery envelope settled with a $120.00 debit
        purchase and an $80.00 credit-card purchase moved only $120.00 out of
        checking -- the credit portion leaves when its CC Payback sibling
        settles, so counting it here debits the money twice.  Hand-computed:
        1000 - 120 = 880.00.

        Measured on production 2026-07-25 before the shared
        :func:`~app.services.cash_ledger.settled_cash_leg` was adopted: an
        ``effective_amount`` walk diverged from the posted ledger on 10 of the
        real Checking account's 130 settled rows, by up to $181.58.

        **The $120.00 is ONE fact, the purchase's, and ruling R-FM is why**
        (plan step X-f3b).  The debit purchase carries the day the bank took
        it (01-05), so it is a movement of its own on THAT day.  Through plan
        step ``X-bi-3e`` the envelope's close on 02-01 was a second fact
        worth ``200 - 80 credit - 120 already posted = $0.00``; since
        ``balance:X-bi-4a`` a row is no fact at all (ruling **R-BAL80**), so
        the stream holds the purchase and nothing else.  The account is
        $120.00 lighter either way -- what moved is WHEN, from the day the
        owner finished the envelope to the day the money actually left, which
        is the whole point of the step.
        """
        from app.services import posting_service  # pylint: disable=import-outside-toplevel
        from app.models.transaction_entry import TransactionEntry  # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_envelope_txn  # pylint: disable=import-outside-toplevel

        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        txn = create_envelope_txn(
            seed_user, db.session, period, "Groceries", Decimal("200.00"),
        )
        for amount, is_credit, day in (
            (Decimal("120.00"), False, date(2026, 1, 5)),
            (Decimal("80.00"), True, date(2026, 1, 6)),
        ):
            db.session.add(TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=seed_user["user"].id,
                amount=amount,
                description="purchase",
                purchased_on=day,
                is_credit=is_credit,
                **settle_day_columns(day),
            ))
        db.session.flush()
        # Settled through PRODUCTION's own verb rather than hand-set: the
        # ``recorded - credit`` formula collapses to the debit-only outflow
        # ONLY because a settled envelope is worth ALL of its entries, so a
        # test that stipulated that premise would keep passing if production
        # stopped honouring it.  The verb picks the entries branch and records
        # the ``purchases`` basis, which stores no figure at all (plan step
        # X-au-c3) -- the row's own entries state it, and the accessor is what
        # reads it back.
        transaction_service.settle_transaction(txn)
        assert settled_figure(txn) == Decimal("200.00")
        assert purchases_total(list(txn.entries)) == Decimal("200.00")
        record_settle_day(txn, an_entered_day(date(2026, 2, 1)))
        posting_service.sync_transaction_postings(txn)
        db.session.commit()

        [purchase_fact] = settled_cash_facts(account.id, scenario.id)
        assert (purchase_fact.settled_on, purchase_fact.delta) == (
            date(2026, 1, 5), Decimal("-120.00"),
        )
        assert purchase_fact.entry_id is not None
        assert _running_balance(account, scenario) == Decimal("880.00")
        # The claim the row-leg move to ``cash_ledger`` rested on (plan step
        # X-a), and the movement's fact inherits it: the walk's delta IS the
        # amount the writer booked on the linked ledger, in the SAME sign.
        # Asserting the sign here is what keeps plan step X-d from
        # wiring the writer onto a negated feed -- a flip that still balances
        # every entry, so nothing else would catch it.
        assert _linked_ledger_net(
            account, scenario, transaction_id=txn.id,
        ) == purchase_fact.delta

    def test_a_settled_transfer_out_reduces_the_walk_like_any_other_row(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Transfer Invariant 5: a settled transfer reaches the walk as its side's record.

        A $300.00 transfer out of Checking is one dated fact on Checking: its
        from-side's record, valued at the transfer's figure
        (:func:`~app.services.cash_ledger.settled_cash_facts`), exactly as a
        settled expense's movement is.  Hand-computed: 1000 - 300 = 700.00.
        It said the walk read the transfer's shadow rows and never queried
        ``Transfer``, which leaf ``balance:X-bi-6-4a`` made false.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        savings = create_savings_account(
            seed_user, db.session, "Savings", Decimal("0.00"),
        )
        create_settled_transfer(
            seed_user, db.session, account, savings, period,
            amount=Decimal("300.00"), settled_on=date(2026, 2, 1),
        )
        db.session.commit()

        assert _running_balance(account, scenario) == Decimal("700.00")


class TestAttributionIsOneKey:
    """One STORED day per fact; nothing here derives it (ruling R-EC)."""

    def test_a_settled_row_with_no_day_is_INVISIBLE_to_the_walk_and_named_by_DC11(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The walk reads nothing for a broken row; the integrity check names it.

        **This test asserted a REFUSAL through plan step ``X-bi-3e``**, and
        the opposite of that until plan step X-f1: the reader derived the day
        from ``paid_at`` and fell back to the row's pay-period ``start_date``
        when the instant was NULL (8 of 146 production settled rows took that
        fallback -- a GUESS the reader could not see), then, the guess gone,
        REFUSED an undated settled row loudly, because silently dating it
        would put real money on a fabricated day and silently dropping it
        would take money out of a balance without saying so.

        Since plan step ``balance:X-bi-4a`` the walk reads MOVEMENTS and no
        row (ruling **R-BAL80**), so it has nothing to refuse: a settled row
        with no day, built bare with ``settled_on=None`` (the only way to
        construct the state -- every write door refuses it), carries no
        dated movement and is simply absent from the stream.  That absence
        is the "silently dropping it" the old refusal forbade, and the alarm
        moved with the read: ``scripts/integrity_check.py``'s **DC-11** names
        every settled row the fold cannot see, this one included.
        """
        # pylint: disable=import-outside-toplevel  -- the script is not a
        # package; the integrity suite imports it the same way.
        from scripts.integrity_check import check_data_consistency

        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[3]
        _opened_at(account, _instant(2026, 1, 1))
        txn = add_txn(
            db.session, seed_user, period, "legacy settle", "12.34",
            status_enum=StatusEnum.DONE, settled_on=None,
        )
        db.session.commit()

        facts = settled_cash_facts(account.id, scenario.id)
        assert [fact for fact in facts if fact.transaction_id == txn.id] == []
        dc11 = next(
            r for r in check_data_consistency(db.session) if r.check_id == "DC-11"
        )
        assert not dc11.passed
        assert txn.id in {row["transaction_id"] for row in dc11.details}

    def test_an_evening_eastern_settle_counts_from_the_users_day(
        self, db, seed_user, seed_periods, monkeypatch,
    ):  # pylint: disable=unused-argument
        """A settle at 20:00 Eastern attributes to THAT day, not to UTC's next.

        The discriminating clock: 2026-03-03 20:00 Eastern is already
        2026-03-04 in UTC, so a fixture pinned where the two zones agree would
        prove nothing.  ``freeze_today`` is asked for that instant explicitly --
        its default is noon UTC, which is the same civil day in both calendars.

        **It goes through the real write door rather than setting the column**,
        and that is what makes it a walk-grain pin of ruling **R-DH (b)** rather
        than a restatement of its own fixture.  The rule lives in
        ``status_seam`` since plan step X-f1 -- nothing downstream derives a day
        any more -- so this asserts the composition: the seam records the user's
        civil day, and the walk attributes the cash to it unchanged.  Measured
        on production before the column existed: 22 of 139 settled Checking rows
        land on a different day under UTC and 5 of those in a different PAY
        PERIOD.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[2]
        _opened_at(account, _instant(2026, 1, 1))
        # 01:00 UTC on the 4th is 20:00 Eastern on the 3rd.
        freeze_today(monkeypatch, date(2026, 3, 4), at_time=time(1, 0))
        assert display_today() == date(2026, 3, 3)
        assert date.today() == date(2026, 3, 4), (
            "the freeze must separate the two calendars or this test cannot "
            "tell the display rule from the process one"
        )

        txn = create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("5.00"),
        )
        db.session.commit()

        assert txn.settled_on == date(2026, 3, 3)
        fact, = _money_facts(settled_cash_facts(account.id, scenario.id))
        assert fact.settled_on == date(2026, 3, 3)


class TestTheWalkSeesOnlyItsOwnRows:
    """Scope: this account, this scenario, contributing rows only."""

    def test_a_non_contributing_row_is_excluded_whatever_it_carries(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A soft-deleted transfer's settled leg must move nothing on the walk.

        The guard that matters most for money: every valuation is TOTAL over
        the contributing gate -- a movement under a non-contributing parent
        moves nothing (``movement_cash_leg``, ruling **R-FM**) and the row is
        worth nothing (``covered_cash_leg``, ruling **R-BAL81**) -- but without
        the SQL exclusion the row would still enter the stream.  Both defences
        were pinned until plan step ``balance:X-bi-6-4d-2`` (the row absent,
        AND every valuation of it zero); the second is pinned since (below).

        **The hidden row is a transfer's leg because no other can hold a
        movement** (ruling **R-CC92**, plan step ``credit_card:CC-5-4a-4``):
        the database refuses a commit that leaves any other row hidden holding
        one.  This was a deleted envelope carrying an $80.00 credit entry, the
        shape that once valued at a fabricated ``+$80.00`` inflow through the
        row's own leg (``settled_cash_leg``, deleted at R-BAL81).  A settled
        transfer's soft delete still hides its legs holding their dated
        payments (finding **balance:BAL-532**), so that is the state staged,
        through the door; plan step ``balance:X-bi-6-4`` closes it, and this
        staging with it.  Re-expressed under rule 5, developer-confirmed
        2026-09-23.

        **Since plan step ``balance:X-bi-6-4d-2`` the state is STAGED inside
        the save and refused at its commit** (ruling **R-BAL167** class 2):
        the delete door takes the payments off (ruling
        **credit_card:R-CC75**) and the deleted-row rule's transfer arm
        refuses a transfer hidden holding one.  The transfer is hidden by
        SQL with the payments left on, the Checking leg -- its transfer and
        its side's record -- is valued inside the save, and the commit is
        then refused.  It also hid the transfer's two twin rows by SQL; from
        plan step ``balance:X-bi-6-4d-2``'s create door on there are none, so
        that statement touched no row (measured at that step's
        vacuous-residue checkpoint), and it is dropped.
        DROPPED: ``settled_cash_facts(...) == []``.  Inside the save the walk
        lists the hidden transfer's record as a fact at ``0.00``: finding
        **BAL-534** deleted the walk's hidden-transfer filter, because the
        state cannot be committed.  Every valuation is still zero and the
        balance is unmoved.
        """
        # pylint: disable=import-outside-toplevel
        from sqlalchemy import text

        from app.models.transaction_entry import TransactionEntry
        from app.services.cash_ledger import movement_cash_leg
        from app.services.status_seam import covered_cash_leg
        from app.services.transfer_legs import TransferLeg
        from tests._test_helpers import refused_by_database_rule

        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        savings = create_savings_account(
            seed_user, db.session, "Savings", Decimal("0.00"),
        )
        xfer = create_settled_transfer(
            seed_user, db.session, account, savings, period,
            amount=Decimal("80.00"), settled_on=date(2026, 2, 1),
        )
        db.session.commit()
        assert [fact.delta for fact in settled_cash_facts(
            account.id, scenario.id,
        )] == [Decimal("-80.00")], "the leg must reach the walk while it counts"

        # Hidden the way the soft delete left it until the step, less the
        # twins it also flagged (none exist since plan step
        # ``balance:X-bi-6-4d-2``): the transfer flagged, the payments in
        # place.  STAGED.
        db.session.execute(
            text("UPDATE budget.transfers SET is_deleted = TRUE WHERE id = :t"),
            {"t": xfer.id},
        )
        db.session.expire_all()
        leg = TransferLeg(
            transfer=xfer, account_id=account.id, is_income=False,
        )
        # Read off the transfer's list (ruling plan#1984), checked by its own
        # SQL rather than handed to the leg.
        assert leg.record is db.session.query(TransactionEntry).filter(
            TransactionEntry.expense_transfer_id == xfer.id,
        ).one()

        assert leg.is_deleted is True
        assert covered_cash_leg(leg, leg.account_id) == Decimal("0.00")
        assert leg.covering_movements, (
            "the leg must carry its payment or this grades nothing"
        )
        assert {
            movement_cash_leg(leg, entry) for entry in leg.covering_movements
        } == {Decimal("0.00")}
        assert _running_balance(account, scenario) == Decimal("1000.00")

        with refused_by_database_rule(
            "was deleted while it still holds a recorded payment",
        ) as caught:
            db.session.commit()
        db.session.rollback()
        assert f"transfer {xfer.id} " in str(caught.value)

    def test_another_scenarios_rows_do_not_enter_the_walk(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Assertions are per-ACCOUNT; settled rows are per-SCENARIO.

        The split ``walk_cash_ledger`` documents as a deliberate contract: the
        same assertions replay in every scenario, each against that scenario's
        own rows.  A what-if scenario's spending must not move the baseline.
        """
        from app.models.scenario import Scenario  # pylint: disable=import-outside-toplevel

        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        other = Scenario(
            user_id=seed_user["user"].id, name="What if", is_baseline=False,
        )
        db.session.add(other)
        db.session.flush()
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("400.00"),
            scenario=other, settled_on=date(2026, 2, 1), name="what-if spend",
        )
        db.session.commit()

        assert settled_cash_facts(account.id, scenario.id) == []
        assert _running_balance(account, scenario) == Decimal("1000.00")
        # ...and the SAME assertions replay in the other scenario, against its
        # own row.
        assert _running_balance(account, other) == Decimal("600.00")

    def test_another_accounts_rows_do_not_enter_the_walk(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A settled row on a sibling account of the same user is excluded."""
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        savings = create_savings_account(
            seed_user, db.session, "Savings", Decimal("50.00"),
        )
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("25.00"),
            account=savings, settled_on=date(2026, 2, 1), name="other acct",
        )
        db.session.commit()

        assert settled_cash_facts(account.id, scenario.id) == []
        assert _running_balance(account, scenario) == Decimal("1000.00")

    def test_a_liability_accounts_negative_anchor_walks_ledger_native(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The walk never branches on account class -- the sign follows TYPE.

        A Credit Card carries an owed-as-NEGATIVE anchor, and a direct charge on
        it is an EXPENSE, so it must make the balance more negative.
        Hand-computed: an asserted -$300.00 with a $75.00 charge settled after
        it walks to -$375.00.  Nothing in the walk consults the account's class
        to get that right, which is what makes the claim structural.
        """
        from tests._test_helpers import create_account_of_type  # pylint: disable=import-outside-toplevel

        _account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        # **The card is OPENED on the calendar's first day** (plan step
        # X-f3c-2c).  ``create_account_of_type`` otherwise asserts the day
        # before today -- the frozen 2026-03-20 -- and the charge below settles
        # 2026-02-01, which would be inside that assertion rather than after
        # it.  Stated at creation because an assertion is append-only: where
        # the origination goes is decided when it is written.
        card = create_account_of_type(
            seed_user, db.session, "Credit Card", "Visa", Decimal("-300.00"),
            observed_on=seed_periods[0].start_date,
        )
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("75.00"),
            account=card, settled_on=date(2026, 2, 1), name="charge",
        )
        db.session.commit()

        assert _running_balance(card, scenario) == Decimal("-375.00")


class TestASourceCannotPredateTheBooks:
    """A settle dated at or before the account's opening is UNSTORABLE.

    **This class replaced ``TestPreOpeningSources``, which pinned the opposite**
    (plan step X-f3c-2b, ruling **R-HG**).  That class graded a walk carrying a
    source dated BEFORE the books opened, and said so deliberately: ruling R-I
    had put the reader's answer in the fold and left the leaf emitting the row
    at its own day, "so the leaf does not unilaterally re-key it".

    What changed is not the leaf's re-keying rule -- it is unchanged -- but
    whether the state it re-keys can exist.  An opening equity is the CLOSING
    balance for its own day, so such a row is already inside the level the fold
    seeds at, and carrying it twice is finding **N-378**.  The fixture below is
    the one the old class used, and the point is that it no longer builds.

    **The walk is therefore free of a filter, and its absence is the property.**
    A leaf that screened its own inputs would be a second statement of a rule
    the database holds, and the two would drift -- which is this arc's own root
    cause 1.
    """

    def test_the_fixture_the_old_contract_rested_on_is_REFUSED(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Books opened 2026-02-01; a $500.00 expense settling 2026-01-15 raises.

        The exact shape ``TestPreOpeningSources`` built, refused at the one
        settle-day writer with a message naming both days.
        """
        account = seed_user["account"]
        period = seed_periods[0]
        # The books' own day is the subject here, so the case names it rather
        # than inheriting the backward-only default (plan step X-f3c-2c).
        _opened_at(
            account, _instant(2026, 2, 1), books_open_on=date(2026, 1, 31),
        )
        db.session.flush()
        with pytest.raises(ValidationError) as exc:
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("500.00"),
                settled_on=date(2026, 1, 15), name="pre-opening",
            )
        assert "2026-01-15" in str(exc.value)
        db.session.rollback()

    def test_the_walk_yields_each_source_at_its_OWN_day_and_adds_none(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The whole source list, by day and amount, hand-computed.

        **This replaces an assertion that could not fail.**  Its first version
        checked that every source the walk yields is dated after the opening --
        over rows read from a database the constraint will not let hold a
        violating one, using the same governing-row rule the constraint uses.
        It could only have failed if the SQL and Python lookups disagreed,
        which is graded properly and from both sides by
        ``test_books_boundary.TestTheTwoGoverningLookupsElectTheSameRow``.  As
        a control over what the walk EMITS, which is what its name promised, it
        was a tautology (found by adversarial review, 2026-08-28).

        So this computes instead.  Books open 2026-01-31 (one day before the
        2026-02-01 opening assertion), a ``$500.00`` expense settles 2026-02-05
        and a ``$120.00`` one settles 2026-02-20.  Hand-computed: the walk
        yields exactly two sources, ``-$500.00`` on 02-05 and ``-$120.00`` on
        02-20, each at its OWN day.  A leaf that filtered its inputs, re-keyed
        one onto the opening, folded the two together or emitted a third would
        fail on the exact pair rather than on a predicate that is true of any
        list.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        # The books open 2026-01-31, which this case asserts below and which
        # the sources are then dated after -- so the day is named rather than
        # inherited (plan step X-f3c-2c).
        _opened_at(
            account, _instant(2026, 2, 1), books_open_on=date(2026, 1, 31),
        )
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("500.00"),
            settled_on=date(2026, 2, 5), name="after-the-books",
        )
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("120.00"),
            settled_on=date(2026, 2, 20), name="later-still",
        )
        db.session.commit()

        walk = walk_cash_ledger(account.id, scenario.id)
        assert walk.opening.opened_on == date(2026, 1, 31)
        assert sorted(
            (fact.settled_on, fact.delta) for fact in _money_facts(walk.source_facts)
        ) == [
            (date(2026, 2, 5), Decimal("-500.00")),
            (date(2026, 2, 20), Decimal("-120.00")),
        ]



class TestTheWalkReadsNoClock:
    """Its output is a function of the account's data alone."""

    def test_the_walk_is_identical_under_two_different_todays(
        self, db, seed_user, seed_periods, monkeypatch,
    ):  # pylint: disable=unused-argument,too-many-arguments,too-many-positional-arguments
        """Freezing today at two dates yields byte-identical deltas.

        A walk that read the clock would make the ledger a function of when the
        sync happened to run -- the corruption generator plan step A3 removed
        from the loan side (``4e46a0a8``).
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("42.00"),
            settled_on=date(2026, 2, 1),
        )
        db.session.commit()

        freeze_today(monkeypatch, date(2026, 2, 15))
        early = dated_deltas(walk_cash_ledger(account.id, scenario.id))
        freeze_today(monkeypatch, date(2030, 12, 31))
        late = dated_deltas(walk_cash_ledger(account.id, scenario.id))
        assert early == late


class TestDegenerateShapes:
    """Total over the states a caller can actually reach."""

    def test_an_account_with_no_assertion_history_walks_empty(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Production-unreachable, but honestly empty rather than a raise.

        A caller that must distinguish "no account" asks the account row, never
        this emptiness -- the totality rule the loan fold already follows.
        """
        # **Built rather than emptied** (plan step X-f3c-2c).  An assertion
        # is append-only at the database tier, so nothing may delete one while
        # its account stands; the only honest route to an account that has
        # asserted nothing is one the assertion factory never touched.
        scenario = seed_user["scenario"]
        account = account_never_asserted(
            seed_user, db.session, name="Silent",
            opening_equity=Decimal("0.00"),
        )
        db.session.commit()

        walk = walk_cash_ledger(account.id, scenario.id)
        assert walk.source_facts == []
        assert walk.anchor_facts == []
        assert dated_deltas(walk) == []

    def test_an_opening_that_agrees_with_the_books_corrects_NOTHING(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The first assertion's ``balance_before`` is the account's OPENING EQUITY.

        With no activity at all, the books read exactly what they opened with,
        so the owner's first assertion moves them by nothing.  That ``$0.00``
        is the healthy steady state for EVERY assertion, and since plan step
        X-f3c-2a the first one is not exempt from it.

        *It asserted ``before == 0.00`` and ``delta == 1000.00`` until then,
        because the replay seeded at zero and the first assertion's delta had
        to carry the whole of what the account held before its records began.
        That quantity is ``budget.account_openings`` now, so the correction is
        a correction.*
        """
        account, scenario = seed_user["account"], seed_user["scenario"]

        facts = cash_anchor_facts(account.id)
        # **The account's OWN origination is the first assertion, and this case
        # reads it rather than adding one** (plan step X-f3c-2c).  It used to
        # re-stamp that row onto 2026-01-01 and read the re-stamped copy; the
        # table is append-only, and the row ``account_service.create_account``
        # wrote IS the first assertion the claim is about.
        assert [fact.is_opening for fact in facts] == [True]
        before, delta = _corrections(account, scenario)[
            facts[0].asserted_at
        ]
        assert before == Decimal("1000.00")
        assert delta == Decimal("0.00")


class TestTheStepsReconstructTheReplay:
    """``dated_deltas`` plus the corrections preserve the replay's own total.

    Named for what it grades since plan step X-f3c-1: the two tiers travel in
    separate lists now, so what is pinned is that MERGING them reproduces the
    replay's terminal balance, and that both land on the day the fold samples.
    """

    def test_the_deltas_sum_to_the_walks_final_running_balance(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Summing every dated delta reproduces the replay's end state.

        Hand-computed: opening $1,000.00, assert $2,000.00 after a -$100.00
        settle (correction ``2000 - 900 = +1100``), then a -$250.00 settle after
        it.  Total ``1000 - 100 + 1100 - 250 = 1750.00``.

        Graded twice, and the second grading is the point: the summed dated
        deltas must equal the balance reconstructed straight off the walk's own
        two lists (the last assertion plus every source after it), so the re-key
        is checked against the replay rather than against itself.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("100.00"),
            settled_on=date(2026, 2, 1), name="pre",
        )
        _assert_balance(
            account, Decimal("2000.00"), _instant(2026, 3, 1),
        )
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("250.00"),
            settled_on=date(2026, 4, 1), name="post",
        )
        db.session.commit()

        assert _running_balance(account, scenario) == Decimal("1750.00")
        assert _replay_terminal_balance(account, scenario) == Decimal("1750.00")

    def test_two_assertions_at_one_instant_keep_the_loaders_order(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The LOADER's ordering is load-bearing, so it gets a test.

        ``CashAnchorFact`` carries no ``id``, so once loaded a same-instant
        pair can only be ordered by what SQL already decided:
        ``ORDER BY observed_on, created_at, id`` in ``cash_anchor_facts``.
        Nothing downstream re-sorts -- the walk advances a monotonic pointer
        over this list -- so if that ``ORDER BY`` changed, nothing else would
        put the pair back.  Two assertions at the identical instant must
        replay in insertion order -- $700.00 then $800.00 -- so the LAST one
        wins and the walk ends on $800.00, not $700.00.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        same = _instant(2026, 3, 1, 9, 0, 0)
        _assert_balance(account, Decimal("700.00"), same)
        _assert_balance(account, Decimal("800.00"), same)
        db.session.commit()

        facts = cash_anchor_facts(account.id)
        assert [fact.anchor_balance for fact in facts] == [
            Decimal("1000.00"), Decimal("700.00"), Decimal("800.00"),
        ]
        assert _running_balance(account, scenario) == Decimal("800.00")

    def test_a_source_and_an_assertion_sharing_a_day_both_land_on_it(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The two tiers key on the same civil DAY, so the assertion closes it.

        The source and the assertion travel in different lists since plan step
        X-f3c-1 -- ``dated_deltas`` carries the movement and
        ``assertion_corrections`` carries the reset -- so what has to be pinned
        is that both land on the day the fold samples, which is ruling R-DH (a):
        an assertion is the CLOSING balance for its civil day, whatever order
        the two were recorded in.  Keying one of them by instant instead is what
        cost production ``$4,001.42`` on 2026-07-31.

        Graded at the balance as well as at the steps, because the step-level
        assertion alone cannot see a day boundary drawn between them.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("30.00"),
            settled_on=date(2026, 3, 1), name="morning",
        )
        _assert_balance(
            account, Decimal("5000.00"), _instant(2026, 3, 1, 17, 0, 0),
        )
        db.session.commit()

        walk = walk_cash_ledger(account.id, scenario.id)
        shared = date(2026, 3, 1)
        assert [
            delta for day, delta in _money_steps(walk) if day == shared
        ] == [Decimal("-30.00")]
        # The assertion's correction on the same day: 5000.00 - 970.00.
        assert [
            (correction.observed_on, correction.delta)
            for correction in assertion_corrections(walk)
            if correction.observed_on == shared
        ] == [(shared, Decimal("4030.00"))]
        # And the day closes on what the user asserted, which is the property
        # the within-day ordering existed to protect.
        assert _fold_at(account, scenario, shared) == Decimal("5000.00")


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
            seed_user, db.session, seed_periods[0], Decimal("100.00"),
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
            seed_user, db.session, seed_periods[0], Decimal("100.00"),
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
            seed_user, db.session, seed_periods[0], Decimal("100.00"),
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
            seed_user, db.session, seed_periods[0], Decimal("50.00"),
            settled_on=date(2026, 2, 20), name="recorded first",
        )
        create_settled_cash_transaction(
            seed_user, db.session, seed_periods[0], Decimal("100.00"),
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
            seed_user, db.session, seed_periods[0], Decimal("40.00"),
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
