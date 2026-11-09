"""Ruling R-DH's invariants, at the grain the user actually reads.

``docs/audits/balance_architecture/archive/anchor_settle_partition.md``.  R-DH answers
one question -- *is this settled row already inside the balance the user
asserted?* -- and the three properties below are the ones the ruling STATES as
its acceptance criteria.  None of them existed in ``tests/`` when the fix
shipped: the adversarial review's finding N-133 / F2 measured ZERO net new tests
in the commit, and ``grep`` found these sentences only in the audit document.
A property named in a ruling and pinned nowhere is a property the next change
deletes for free.

Every assertion here is at the **projected end balance** grain -- the figure the
grid renders and the one production got wrong by ``-$4,001.42`` -- not at the
walk grain underneath it.  The walk has its own controls
(``test_cash_walk.py``, ``test_account_posting_service.py``); what those cannot
show is that the seam, the entries-aware reservation and the anchor reset
compose to a stable number, because each of the three is individually correct
about a different thing.

Figures are HAND-COMPUTED from the ruling's own worked example, which is the
developer's real bookkeeping session on 2026-07-31.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db as _db
from app.services import (
    anchor_service,
    balance_at,
    cash_ledger,
    reconcile_service,
)
from app.services.cash_flow_set import CashFlowSet
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import calendar_for
from app.utils.dates import display_today, to_display_date
from tests._test_helpers import (
    add_entry,
    add_txn,
    create_account_of_type,
    create_envelope_txn,
    create_settled_cash_transaction,
    current_pay_period,
    observed_day_of,
    override_anchor,
    settle_instant_on,
)


# The ruling's worked example, verbatim (R-DH (c)):
#
#     before:  1307.66 - 500.00 - 827.61  =  -19.95
#     record a $150.27 purchase and anchor to $1,157.39:
#     after:   1157.39 - 349.73 - 827.61  =  -19.95
#
_ANCHOR = Decimal("1307.66")
_ENVELOPE_BUDGET = Decimal("500.00")
_OTHER_BILLS = Decimal("827.61")
_PURCHASE = Decimal("150.27")
_ANCHOR_AFTER = _ANCHOR - _PURCHASE          # 1157.39
_PROJECTED_END = Decimal("-19.95")

# A fixed civil day for the fence tests below.  They compare no figure and
# touch no database, so the suite's frozen clock is irrelevant to them and a
# literal is clearer than a derived date.
_A_STATEMENT_DAY = date(2026, 1, 22)


def _current_period(user_id):
    """Return the pay period containing today in the USER's zone."""
    return current_pay_period(
        user_id, as_of=display_today(),
    )


def _projected_end_balance(account, user_id, period):
    """Return the grid's projected end balance for ONE period.

    Read through ``balance_at.grid_balance_view`` -- the entry the grid ROUTE
    calls -- so these invariants are pinned at the figure the user sees rather
    than at an intermediate the route does not render.  The context is rebuilt
    per call because a true-up between two reads must be visible to the second.
    """
    view = balance_at.grid_balance_view(
        CashFlowSet.single(account), BalanceContext.build(user_id),
    )
    return view.columns[period.id].balance


class TestTheRuleCannotBeAskedAnyOtherWay:
    """The partition's fence is STRUCTURAL, and this is what pins it.

    Ruling R-DH's question -- *is this movement already inside the balance the
    user declared?* -- had FOUR implementations when
    ``anchor_settle_partition.md`` was written, in three different units, and
    one of them cost production ``$4,001.42``.  The plan's answer was a pylint
    checker that would flag a fifth.  A checker cannot see through
    ``earliest <= latest``, which is the exact site finding N-133 / F4 was
    about, so it would have fenced every site except the one with the history.

    :class:`~app.services.cash_ledger.ReconciledThrough` fences it by
    construction instead: it defines no ordering against a civil day, so the
    wrong spelling does not run at all.

    **What reopens it was MEASURED, not assumed, and the first guess was
    wrong.**  ``@dataclass(order=True)`` does NOT reopen it -- the generated
    dunders compare the same class only and return ``NotImplemented`` against
    a ``date``, so every spelling below still raises.  What reopens it is a
    HAND-WRITTEN ``__le__`` / ``__ge__`` that unwraps the other operand, or
    the type being reverted to a bare ``date``.  Either restores every
    comparison below silently, with the whole suite still green.  These tests
    are what makes that loud, and they are negative-controlled against the
    hand-written dunder rather than against the keyword that looked like the
    threat.

    They assert on the LANGUAGE, not on a figure, and that is deliberate: the
    figures are pinned by the classes below, and none of them can tell a
    correct rule from a second correct rule written twice.
    """

    #: Every ordering shape a fifth answer could be spelled in.  ``==`` is
    #: deliberately absent: the dataclass defines it, it is not the coverage
    #: question, and two boundaries comparing equal is what the twin-statement
    #: test in ``test_cash_walk.py`` asserts on.
    _ORDERINGS = (
        # "is this movement inside the balance", both operand orders
        ("day <= boundary", lambda day, boundary: day <= boundary),
        ("day < boundary", lambda day, boundary: day < boundary),
        ("boundary >= day", lambda day, boundary: boundary >= day),
        ("boundary > day", lambda day, boundary: boundary > day),
        # The SAME question asked the other way round -- "is the assertion at
        # or before this movement".  These four were MISSING from the first
        # draft, and an adversarial review proved the gap by planting a
        # ``__le__``-ONLY mutant: Python's reflection meant all four above
        # still raised, while these two answered.  A control that pins one
        # direction of a symmetric operator pins half a rule.
        ("boundary <= day", lambda day, boundary: boundary <= day),
        ("boundary < day", lambda day, boundary: boundary < day),
        ("day >= boundary", lambda day, boundary: day >= boundary),
        ("day > boundary", lambda day, boundary: day > boundary),
        # Every builtin that reaches ordering through the same dunders.
        ("sorted", lambda day, boundary: sorted([boundary, day])),
        ("max", lambda day, boundary: max(day, boundary)),
        ("min", lambda day, boundary: min(day, boundary)),
    )

    def test_no_ordering_against_a_civil_day_is_defined(self):
        """Every spelling of the rule except ``covers`` raises ``TypeError``.

        Each is a way somebody could answer the arc's question a fifth time,
        in BOTH operand orders because ``__le__`` and ``__ge__`` are reached
        by reflection and a one-sided mutant leaves the other side working.
        None of them runs.
        """
        boundary = cash_ledger.ReconciledThrough(_A_STATEMENT_DAY)
        day = _A_STATEMENT_DAY - timedelta(days=1)

        # Collected rather than asserted per iteration, so a failure NAMES the
        # spelling that started working instead of stopping at the first.
        answered = []
        for name, spelling in self._ORDERINGS:
            try:
                spelling(day, boundary)
            except TypeError:
                continue
            answered.append(name)

        assert not answered, (
            f"ReconciledThrough answered an ordering comparison: {answered}.  "
            f"That is a second implementation of ruling R-DH's partition, "
            f"reachable without calling covers() -- the shape that cost "
            f"production $4,001.42.  Look for a hand-written __le__ / __ge__, "
            f"or the type reverted to a bare date; note that order=True alone "
            f"does NOT do this."
        )

    def test_covers_is_the_spelling_that_does_work(self):
        """The one implementation answers all four arms, and is TOTAL.

        A rule with a precondition is a rule a caller forgets, so both
        absences answer False rather than raising: an unobserved posting day
        is outstanding (ruling R-DH (d) as restated at S1-c -- the engine never
        guesses one), and an account that has declared no balance has nothing
        for a movement to be inside of.
        """
        boundary = cash_ledger.ReconciledThrough(_A_STATEMENT_DAY)

        assert boundary.covers(_A_STATEMENT_DAY - timedelta(days=1)) is True
        assert boundary.covers(_A_STATEMENT_DAY) is True
        assert boundary.covers(_A_STATEMENT_DAY + timedelta(days=1)) is False
        assert boundary.covers(None) is False
        assert cash_ledger.ReconciledThrough(None).covers(
            _A_STATEMENT_DAY,
        ) is False


class TestRecordingAPurchaseDoesNotMoveTheProjection:
    """R-DH (c): the envelope process is order-independent AND value-neutral.

    The ruling keeps the developer's process unchanged -- record an entry per
    purchase against the envelope, true up the anchor, either order -- and
    states the price of keeping it: **recording a purchase and truing up the
    anchor by the same amount must not move the projected end balance**, and it
    must not move if only the entry is recorded and no anchor follows.

    All three rows turn on the reservation formula
    (``cash_ledger._amounts._entry_aware_amount``):

        impact = max(estimated - settled_debit - credit, outstanding_debit)

    An OUTSTANDING debit leaves the reservation at the full budget (the money
    left checking but the anchor does not know), so the projection is
    unchanged.  A SETTLED debit reduces the reservation by exactly what the
    anchor dropped by, so the two moves cancel.  The invariant is what makes
    those two behaviours ONE design instead of two separately-plausible rules.

    **Section 12.6's table, all three rows, and the third one is what plan step
    S1-c FIXES:**

    ======================================  ==========================
    what happens                            invariant
    ======================================  ==========================
    record a purchase, nothing else         holds -- projection unmoved
    record it, then true up and tick it     holds -- the two cancel
    true up FIRST, then record it, tick it  holds -- "either order" is
                                            finally true
    ======================================  ==========================

    Section 10.5 recorded that R-DH (c)'s two promises could not both be kept
    under a stored ``is_cleared`` flag, and Section 10.6 accepted breaking the
    second.  Under the observed-date design neither is broken.  The third row is
    what today's shipped code gets wrong: the bulk clear ran before the entry
    existed, so it never cleared and the projection read ``-$170.22`` against a
    true ``-$19.95`` until the NEXT true-up.  That defect is 14 of the
    developer's 53 same-day entries and it has never had a test.
    """

    @staticmethod
    def _tick(seed_user, account, envelope):
        """Reconcile the envelope's purchases through the real service door.

        The user ticking the purchase off their statement
        (``reconcile_service.record_settled_days``) -- the step that replaced the
        bulk clear.  Returns how many rows were actually stamped, which the
        callers assert on: a reconcile that silently matched nothing would make
        every figure below hold for the wrong reason.

        **It resolves the governing ASSERTION, not its day** (plan step X-f3a-1,
        ruling **R-FL**): a tick records WHICH statement showed the purchase, so
        the writer takes the row the tick is being made against rather than a
        ``MAX`` over the day column, which cannot name one.  Every figure below
        is unchanged, which is exactly what R-DH (c)'s invariant asks of a step
        whose whole claim is that it records a fact and moves no money.
        """
        recorded = reconcile_service.record_settled_days(
            reconcile_service.Statement(
                calendar_for(seed_user["user"].id), account.id,
                cash_ledger.governing_anchor(account.id),
            ),
            {entry.id for entry in envelope.entries},
        )
        _db.session.commit()
        return recorded

    def _seed(self, seed_user, period):
        """Anchor $1,307.66, budget a $500 envelope and $827.61 of other bills."""
        account = seed_user["account"]
        override_anchor(
            _db.session, account, period, _ANCHOR,
        )
        envelope = create_envelope_txn(
            seed_user, _db.session, period, "Groceries", _ENVELOPE_BUDGET,
        )
        add_txn(
            _db.session, seed_user, period, "Every other bill", _OTHER_BILLS,
        )
        _db.session.commit()
        return account, envelope

    def test_an_entry_alone_does_not_move_the_projected_end_balance(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Row 1: recording a purchase with NO true-up leaves the figure alone.

        Anchor $1,307.66, a $500.00 Groceries envelope with nothing recorded,
        $827.61 of other projected bills:

            1307.66 - 500.00 - 827.61 = -19.95

        Record a $150.27 purchase against the envelope and record NOTHING else.
        The purchase is OUTSTANDING -- its ``settled_on`` is NULL, because the
        user has not seen it on a statement -- so the reservation stays at the
        full $500.00 (``max(500 - 0 - 0, 150.27)``) and the figure is still
        -$19.95.

        The wrong answer to guard against is $130.32 (``1307.66 - 349.73 -
        827.61``): reducing the reservation by a purchase the anchor has not
        seen counts the money as still available AND as already spent.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            period = _current_period(user_id)
            account, envelope = self._seed(seed_user, period)

            before = _projected_end_balance(account, user_id, period)
            assert before == _PROJECTED_END

            add_entry(
                _db.session, seed_user, envelope, _PURCHASE, display_today(),
            )
            _db.session.commit()

            assert _projected_end_balance(
                account, user_id, period,
            ) == _PROJECTED_END

    def test_recording_then_truing_up_then_ticking_cancels_out(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Row 2: record the purchase, true up, tick it -- the two cancel.

        The same start (-$19.95).  Record the $150.27 purchase, then true the
        anchor up to $1,157.39 -- exactly $150.27 lower, which is what the bank
        now shows -- then tick the purchase off that statement:

            1157.39 - 349.73 - 827.61 = -19.95

        The anchor fell by $150.27 and the reservation fell by $150.27, and the
        projected end balance does not move.  This is the invariant the whole
        envelope design rests on: the user's bookkeeping session changes what
        the app KNOWS without changing what it PREDICTS.

        **The tick is a separate step now, and asserting the INTERMEDIATE is
        what makes this test discriminating** (plan step S1-c).  A true-up used
        to reconcile the entry as a side effect; it no longer touches one, so
        the balance is asserted BETWEEN the true-up and the tick as well -- at
        that point the anchor has fallen while the reservation has not, and the
        figure is $150.27 lower.  Without that intermediate the final -$19.95
        could be produced by a build that reconciled nothing and never moved
        the anchor either.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            period = _current_period(user_id)
            account, envelope = self._seed(seed_user, period)
            assert _projected_end_balance(
                account, user_id, period,
            ) == _PROJECTED_END

            add_entry(
                _db.session, seed_user, envelope, _PURCHASE, display_today(),
            )
            _db.session.commit()
            anchor_service.apply_anchor_true_up(
                account=account,
                new_balance=_ANCHOR_AFTER,
            )

            # The true-up alone reconciles NOTHING (ruling R-DH (d)), so the
            # anchor has dropped and the reservation has not.
            assert envelope.entries[0].settled_on is None
            assert _projected_end_balance(
                account, user_id, period,
            ) == _PROJECTED_END - _PURCHASE

            assert self._tick(seed_user, account, envelope) == 1

            # The tick reconciled the purchase, which is what makes the two
            # moves cancel; asserting it here means a regression that stops
            # reconciling reports as itself rather than as a balance drift.
            assert envelope.entries[0].settled_on is not None
            assert _projected_end_balance(
                account, user_id, period,
            ) == _PROJECTED_END

    def test_truing_up_FIRST_then_recording_then_ticking_also_cancels(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Row 3: the order that was WRONG until plan step S1-c.

        The same session in the other order: the user reads their bank balance
        and enters $1,157.39 FIRST, then records the $150.27 purchase it
        already reflects, then ticks it off.  The same three facts are true, so
        the projection must be the same number -- ``-$19.95``.

        **This is the defect S1-c fixes, and it has never had a test.**  The
        bulk clear fired inside the true-up, so it ran BEFORE the entry existed
        and the entry was never reconciled; the projection then read the whole
        envelope budget against an anchor that had already dropped by the
        purchase -- ``-$170.22`` here (``_PROJECTED_END - _PURCHASE``), wrong by
        exactly the purchase, until the NEXT true-up happened to sweep it.  On
        the developer's real data that is 14 of 53 same-day entries.

        The negative control is stated as a figure rather than left implicit:
        ``-$170.22`` is what the shipped code answered and what this test fails
        with if the reconcile step is ever folded back into the true-up.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            period = _current_period(user_id)
            account, envelope = self._seed(seed_user, period)
            assert _projected_end_balance(
                account, user_id, period,
            ) == _PROJECTED_END

            # The anchor comes FIRST this time.
            anchor_service.apply_anchor_true_up(
                account=account,
                new_balance=_ANCHOR_AFTER,
            )
            add_entry(
                _db.session, seed_user, envelope, _PURCHASE, display_today(),
            )
            _db.session.commit()

            # Before the tick the projection is the OLD defect's figure.
            assert _projected_end_balance(
                account, user_id, period,
            ) == _PROJECTED_END - _PURCHASE

            assert self._tick(seed_user, account, envelope) == 1

            assert _projected_end_balance(
                account, user_id, period,
            ) == _PROJECTED_END


class TestTheProjectionIgnoresTheOrderOfABookkeepingSession:
    """R-DH's verification standard 4, at the PROJECTED-BALANCE grain.

    The defect that opened the ruling was decided by click order: three
    already-cleared payments recorded in the NINE SECONDS after an anchor were
    subtracted from a bank balance that already contained them, and the grid
    reported ``-$4,021.37`` against a hand-computed ``-$19.95``.

    There is a control for this at WALK grain
    (``test_cash_walk.py::test_both_same_day_settles_go_with_the_assertion_whatever_the_order``).
    A walk-grain control cannot see the projection: the walk is one of four
    inputs to the rendered figure, and the defect was reported as a rendered
    figure.  This permutes the recording order of a session and asserts the
    thing the user actually looked at.
    """

    def test_the_same_session_recorded_in_either_order_gives_one_answer(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Anchor-then-settle and settle-then-anchor render the same balance.

        The session, in miniature: the bank shows $1,307.66 and two payments
        totalling $500.00 have already cleared today.  Whether the user enters
        the anchor first and then ticks the payments off, or ticks them off
        first and then enters the anchor, the same three facts are true -- so
        the projection must be the same number.

        With $827.61 of other projected bills the answer is ``1307.66 - 827.61
        = 480.05`` both ways: the two cleared payments are INSIDE the asserted
        balance (they are dated its civil day) and are not subtracted again.
        Under the instant partition the second ordering answered ``-$19.95``,
        a $500.00 swing decided by which button was pressed first.

        **The permutation runs on TWO accounts rather than by re-asserting one**,
        because re-asserting the balance that already governs writes nothing
        (ruling R-EQ) -- correctly, since it changes nothing.  Two accounts
        holding identical facts in opposite recording orders is the same
        experiment without fighting a rule that is doing its job.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            period = _current_period(user_id)
            noon = settle_instant_on(display_today())
            cleared = (Decimal("300.00"), Decimal("200.00"))

            anchor_first = create_account_of_type(
                seed_user, _db.session, "Checking", "Anchor first",
                anchor_balance=Decimal("0.00"),
                observed_on=period.start_date - timedelta(days=1),
            )
            settles_first = create_account_of_type(
                seed_user, _db.session, "Checking", "Settles first",
                anchor_balance=Decimal("0.00"),
                observed_on=period.start_date - timedelta(days=1),
            )
            _db.session.commit()

            # ORDER A: the anchor is recorded BEFORE the payments are ticked.
            override_anchor(
                _db.session, anchor_first, period, _ANCHOR,
                at=noon,
            )
            _db.session.flush()
            for index, amount in enumerate(cleared):
                create_settled_cash_transaction(
                    seed_user, _db.session, period, amount,
                    account=anchor_first, name=f"A cleared {index}",
                    settled_on=observed_day_of(noon),
                )

            # ORDER B: the same two payments are ticked BEFORE the anchor.
            for index, amount in enumerate(cleared):
                create_settled_cash_transaction(
                    seed_user, _db.session, period, amount,
                    account=settles_first, name=f"B cleared {index}",
                    settled_on=observed_day_of(noon),
                )
            override_anchor(
                _db.session, settles_first, period, _ANCHOR,
                at=noon + timedelta(hours=3),
            )

            for account in (anchor_first, settles_first):
                add_txn(
                    _db.session, seed_user, period, "Every other bill",
                    _OTHER_BILLS, account=account,
                )
            _db.session.commit()

            # The precondition the whole case rests on: every event in both
            # sessions is on ONE civil day, so what differs between the two
            # accounts is RECORDING ORDER and nothing else.  Stated rather than
            # assumed -- an unstated day relationship is how a fixture stops
            # exercising the rule it names (finding N-132 / N-133 F2).
            #
            # The ASSERTION instants are the only instants left: the settles
            # carry a stored civil day since plan step X-f1, and both sessions
            # give them ``observed_day_of(noon)``.  A third instant was checked
            # here (``noon - timedelta(hours=2)``, order B's earlier settle) and
            # no fixture uses it any more.
            for instant in (noon, noon + timedelta(hours=3)):
                assert to_display_date(instant) == display_today()
            assert observed_day_of(noon) == display_today()

            order_a = _projected_end_balance(anchor_first, user_id, period)
            order_b = _projected_end_balance(settles_first, user_id, period)

            assert order_a == Decimal("480.05")
            assert order_b == order_a
