"""The fold seeds from a recorded fact: what an account held before its records begin is its stored
opening equity, read once per walk, and the level does not move when an assertion is back-dated.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.services.balance_at._assertions import assertion_corrections
from app.services.balance_at._fold import sample_cumulative
from app.services.cash_ledger import account_opening_fact, dated_deltas, walk_cash_ledger
from tests._test_helpers import (
    append_balance_assertion,
    create_savings_account,
    create_settled_cash_transaction,
    freeze_today,
)

from ._helpers import _LATE_AS_OF, _instant, _fold, _BOOKS_OPEN_ON, _opened_at


def _recorded_steps(account, scenario):
    """Return the ACTUAL tier's whole step list, independently reassembled.

    ``dated_deltas`` (the SOURCE facts) plus the assertion RESETS -- which is
    now exactly what ``_cash_fold._actual_steps`` merges, with nothing added
    and nothing held back.  The two pieces travel separately since plan step
    X-f3c-1: applying an assertion to a running total is a policy an account's
    KIND decides (ruling R-FO for the modelled kinds, plan step X-f3c for the
    PLAIN ones), so the leaf states the facts and the fold states the policy.

    *It read "minus the opening compensator" until plan step X-f3c-2a, when
    that compensator was deleted with the derivation it existed to cancel.  The
    list is the same list the fold builds now, which is what makes
    :meth:`TestTheOpeningEquityIsTheSeed.test_the_fold_is_the_seed_plus_the_steps_and_nothing_else`
    a check on the SEED rather than on a cancellation.*
    """
    walk = walk_cash_ledger(account.id, scenario.id)
    return sorted(
        dated_deltas(walk) + [
            (correction.observed_on, correction.delta)
            for correction in assertion_corrections(walk)
        ],
        key=lambda step: step[0],
    )


def _stored_opening_equity(account):
    """Return the account's governing opening equity, read the way the fold does.

    Through the leaf's own loader rather than a query written here, so a test
    reassembling the fold cannot disagree with it about which restatement
    governs.
    """
    return account_opening_fact(account.id).opening_equity


class TestTheOpeningEquityIsTheSeed:
    """Plan step X-f3c-2a: the fold seeds from a RECORDED fact.

    What an account held before its records begin is
    ``budget.account_openings``, read once per walk
    (:attr:`app.services.cash_ledger.CashLedgerWalk.opening`) and used as the
    fold's seed.  It was DERIVED per read until this step -- the earliest
    assertion less the movements it already contained, moved into the seed and
    cancelled on its own day (ruling **R-I**) -- which made the level move when
    an assertion was back-dated, differ between scenarios, and be impossible to
    correct.

    **The FIRST assertion is now an ordinary correction**, and that is the whole
    behavioural difference: its delta is what the owner's declaration moved the
    books by, ``$0.00`` wherever the two agree.
    """

    def test_the_seed_is_the_stored_opening_equity_and_nothing_else(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A date before every event reads the stored figure, verbatim.

        The account is created asserting $1,000.00, so ``create_account`` writes
        an opening record of $1,000.00 (a brand-new account holds no records for
        the assertion to already contain, which is why that write is
        ``user_declared`` rather than a derivation).  With no movements at all,
        every date -- before the opening, on it, after it -- reads exactly that.

        **This is what the seed being a READ rather than a computation means.**
        Nothing in the fold re-derives $1,000.00 from the assertion series, so
        no later assertion can change it.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        # The books' day is named because this case SAMPLES around it -- one
        # of the four dates below is the day before them (plan step X-f3c-2c).
        _opened_at(
            account, _instant(2026, 2, 1), books_open_on=date(2026, 1, 31),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2020, 1, 1), date(2026, 1, 31), date(2026, 2, 1),
            date(2026, 3, 1),
        ])
        assert set(folded.values()) == {Decimal("1000.00")}

    def test_a_record_dated_before_the_books_opened_is_REFUSED(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """FLIPPED at plan step X-f3c-2b: the shape is now unstorable.

        **This test asserted the double count until 2026-08-28**, deliberately,
        so this step had something to flip: books opened $1,000.00 on
        2026-02-01, a $500.00 expense recorded as settling 2026-01-15, and the
        fold reading $500.00 across the gap -- a dip that never happened,
        healed only when the assertion reset the running total.

        Ruling **R-HG** deletes the state rather than the dip.  An opening
        equity is the CLOSING balance for its own day, so that expense is
        already inside the $1,000.00 the owner declared; the fold has no way to
        know it, so the write door refuses it and a deferrable constraint
        trigger makes it unstorable from any client.

        **What did NOT change is the fold.**  There is no filter in
        ``dated_deltas`` and none here: a fold that screened its own inputs
        would be a second statement of a rule the database holds.
        """
        account = seed_user["account"]
        # **The books' own day is the SUBJECT here, so the case names it**
        # (plan step X-f3c-2c).  ``books_open_on=None`` used to mean "one day
        # before the assertion", which stopped being true when the default
        # became backward-only -- and it had to, because the unconditional
        # forward restatement shoved the books past the account's own
        # origination everywhere else in this file.
        _opened_at(
            account, _instant(2026, 2, 1), books_open_on=date(2026, 1, 31),
        )
        db.session.flush()
        with pytest.raises(ValidationError) as exc:
            create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("500.00"),
                settled_on=date(2026, 1, 15), name="pre-opening",
            )
        message = str(exc.value)
        assert "2026-01-15" in message
        # ``_opened_at`` pins the opening ASSERTION at 2026-02-01 and the books
        # the day before it, which is the production shape after this step's
        # migration (Checking asserts 2026-03-27 over books that open 03-26).
        assert "2026-01-31" in message
        db.session.rollback()

    def test_the_opening_day_itself_is_refused_not_just_an_earlier_one(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The `<=` half of R-HG, on the fold's own fixture.

        Paired with the case above and with
        :meth:`test_the_real_money_market_shape` below, so the comparison
        direction is pinned from both sides: a ``<`` written where the code has
        ``<=`` passes the case above and fails this one.

        ``_opened_at`` asserts $1,000.00 for 2026-02-01 and opens the books
        2026-01-31; a $500.00 expense settling 2026-01-31 is inside that
        figure, because an opening equity is its own day's CLOSING balance.
        The next day, 2026-02-01, is recordable -- asserted below so the pair
        pins the boundary rather than only one side of it.
        """
        account = seed_user["account"]
        # Tight books, NAMED, for the reason the case above states.
        _opened_at(
            account, _instant(2026, 2, 1), books_open_on=date(2026, 1, 31),
        )
        db.session.flush()
        with pytest.raises(ValidationError):
            create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("500.00"),
                settled_on=date(2026, 1, 31), name="on-the-opening-day",
            )
        db.session.rollback()
        _opened_at(
            account, _instant(2026, 2, 1), books_open_on=date(2026, 1, 31),
        )
        create_settled_cash_transaction(
            seed_user, seed_periods[0], Decimal("500.00"),
            settled_on=date(2026, 2, 1), name="the-first-recordable-day",
        )
        db.session.commit()

    def test_the_real_money_market_shape(
        self, db, seed_user, seed_periods, monkeypatch,
    ):  # pylint: disable=unused-argument,too-many-arguments,too-many-positional-arguments
        """The production Money Market shape, as X-f3c-2b's migration leaves it.

        The real account asserts $4,879.26 for 2026-05-01 over four records
        already inside it: +$500.00 on 04-06, +$500.00 on 04-09, +$500.00 on
        04-11 and -$1,500.00 on 04-23.  Its books opened 2026-05-01 before this
        step and **2026-04-05 after it** -- the day before its earliest record,
        which is what the migration moves every such account to.

        Hand-computed: those four records sum to $0.00, so the seed is
        $4,879.26 and the fold reads $4,879.26 before 04-06, $5,379.26 on
        04-06, $5,879.26 on 04-10, $6,379.26 on 04-22 (the withdrawal not yet
        taken) and $4,879.26 from 04-23.  **Every figure is what it was before
        this step** -- the migration moves the DAY and not the money, which is
        the whole claim its own docstring makes and the harnesses measured.

        The first record now lands one day AFTER the books rather than 25 days
        before them, which is what makes the fixture buildable at all.
        """
        # The clock moves PAST the shape rather than the shape moving to the
        # clock (plan step X-f3c-2b).  ``create_settled_cash_transaction``
        # settles through the seam's own default day first -- the owner's today
        # -- before correcting it, so a fixture whose books open after that day
        # is refused on the transient stamp rather than on the day it asks for.
        # Production cannot reach the state at all: ``resolve_observation_day``
        # refuses a future ``observed_on``, so books in the future are a fixture
        # artefact and the honest repair is to place the reader after them.
        freeze_today(monkeypatch, date(2026, 6, 1))
        scenario = seed_user["scenario"]
        account = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("4879.26"),
        )
        _opened_at(account, _instant(2026, 4, 5))
        for amount, day, is_income in (
            (Decimal("500.00"), 6, True),
            (Decimal("500.00"), 9, True),
            (Decimal("500.00"), 11, True),
            (Decimal("1500.00"), 23, False),
        ):
            create_settled_cash_transaction(
                seed_user, seed_periods[7], amount,
                account=account, is_income=is_income,
                settled_on=date(2026, 4, day), name=f"apr-{day}",
            )
        append_balance_assertion(
            db.session, account, Decimal("4879.26"),
            at=_instant(2026, 5, 1),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 4, 5), date(2026, 4, 6), date(2026, 4, 10),
            date(2026, 4, 22), date(2026, 4, 23), date(2026, 5, 1),
        ])
        assert folded[date(2026, 4, 5)] == Decimal("4879.26")
        assert folded[date(2026, 4, 6)] == Decimal("5379.26")
        assert folded[date(2026, 4, 10)] == Decimal("5879.26")
        assert folded[date(2026, 4, 22)] == Decimal("6379.26")
        assert folded[date(2026, 4, 23)] == Decimal("4879.26")
        assert folded[date(2026, 5, 1)] == Decimal("4879.26")

    def test_the_real_savings_shape(
        self, db, seed_user, seed_periods, monkeypatch,
    ):  # pylint: disable=unused-argument,too-many-arguments,too-many-positional-arguments
        """The production Fidelity Savings shape, as the migration leaves it.

        The real account 2 declared $5,363.56 for 2026-04-06 with a single
        +$500.00 record on 03-27 -- one of the twelve rows this step repairs.
        Its books opened 2026-04-06 before this step and **2026-03-26 after
        it**, the day before that record.  The fixture asserts the derived
        $4,863.56 for 2026-03-26 and ``_opened_at`` opens its books the day
        before that.

        Hand-computed under the stored seed: the migration keeps the DERIVED
        equity ($5,363.56 - $500.00 = $4,863.56), so the fold reads $4,863.56
        before 03-27, $5,363.56 from 03-27, and $5,363.56 from 04-06 when the
        assertion agrees rather than corrects.  **Those are the figures
        production rendered before this step**, which is what money-neutral
        means here.

        *This test asserted $5,363.56 / $5,863.56 / $5,363.56 until 2026-08-28,
        against a fixture whose opening was ``user_declared`` at the ASSERTED
        figure -- so the deposit was counted a second time.  The production
        account's row is ``migration_derived`` at the SUBTRACTED figure, and
        the pair of readings is why that column is not decoration.  The fixture
        now models the real one.*
        """
        # See ``test_the_real_money_market_shape`` for why the clock moves.
        freeze_today(monkeypatch, date(2026, 6, 1))
        scenario = seed_user["scenario"]
        account = create_savings_account(
            seed_user, db.session, "Fidelity Savings", Decimal("4863.56"),
        )
        _opened_at(account, _instant(2026, 3, 26))
        create_settled_cash_transaction(
            seed_user, seed_periods[6], Decimal("500.00"),
            account=account, is_income=True,
            settled_on=date(2026, 3, 27), name="deposit",
        )
        append_balance_assertion(
            db.session, account, Decimal("5363.56"),
            at=_instant(2026, 4, 6),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 3, 26), date(2026, 3, 27), date(2026, 4, 6),
        ])
        assert folded[date(2026, 3, 26)] == Decimal("4863.56")
        assert folded[date(2026, 3, 27)] == Decimal("5363.56")
        assert folded[date(2026, 4, 6)] == Decimal("5363.56")

    def test_the_fold_is_the_seed_plus_the_steps_and_nothing_else(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The pin that the fold adds NO term of its own (plan step X-f3c-2a).

        The fold used to re-derive the opening's emitted correction in order to
        cancel it with a compensating step, because the step list is bare tuples
        that carry no identity -- a second statement of what the assertion
        replay emits, which had to be pinned rather than trusted.  Both the
        re-derivation and the compensator are gone: the seed is a stored fact
        and the steps are the replay's own output.

        The equality holds on EVERY date now, where it held only at and after
        the opening before -- but it is NOT a check on the seed, and an earlier
        draft of this docstring claimed it was.  Both sides read
        :func:`app.services.cash_ledger.account_opening_fact`, so a wrong
        stored equity shifts them together and this comparison survives it.
        What it does catch, which is the property it is named for, is a term
        added to ``_actual_steps`` and not to :func:`_recorded_steps` -- a
        compensator growing back, a re-key, a second seed.  The figures below
        are what pin the seed, and only the two BEFORE the opening can.

        Stream: books declared $1,000.00, opening assertion 2026-02-01 with a
        -$500.00 record dated before it (2026-01-15), a true-up to $2,000.00
        (2026-03-01), and a -$250.00 record after that (2026-04-01).
        Hand-computed: $1,000.00 on 01-14, $500.00 from 01-15 -- a record
        BETWEEN the books and the first assertion, which is the state this
        file's default ``_BOOKS_OPEN_ON`` exists to build and is not a
        pre-opening row at all -- $1,000.00 from 02-01, $2,000.00 from 03-01
        and $1,750.00 from 04-01.  **The three at-and-after figures are unchanged from before this
        step**, which is the evidence the assembly moved and the money did not.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 2, 1), books_open_on=_BOOKS_OPEN_ON)
        create_settled_cash_transaction(
            seed_user, seed_periods[0], Decimal("500.00"),
            settled_on=date(2026, 1, 15), name="between-books-and-assertion",
        )
        append_balance_assertion(
            db.session, account, Decimal("2000.00"),
            _instant(2026, 3, 1),
        )
        create_settled_cash_transaction(
            seed_user, seed_periods[6], Decimal("250.00"),
            settled_on=date(2026, 4, 1), name="post",
        )
        db.session.commit()

        at_and_after = [
            date(2026, 2, 1) + timedelta(days=offset) for offset in range(75)
        ]
        # The equality below is a claim about the ACTUAL tier alone, so the
        # comparison window must end before the PLANNED tier's clamp floor could
        # place anything inside it.  Structural, not incidental: this holds even
        # if a future fixture change adds a projected row.
        assert at_and_after[-1] < _LATE_AS_OF + timedelta(days=1)
        seeded = sample_cumulative(
            _stored_opening_equity(account),
            _recorded_steps(account, scenario),
            at_and_after,
        )
        folded = _fold(account, scenario, at_and_after)
        assert folded == seeded

        # ...and the hand-computed values the equality is standing on, so a
        # jointly-broken pair cannot pass by agreeing with each other.
        assert folded[date(2026, 2, 1)] == Decimal("1000.00")
        assert folded[date(2026, 3, 1)] == Decimal("2000.00")
        assert folded[date(2026, 4, 1)] == Decimal("1750.00")

        # Before the opening is exactly where R-I changed the answer, and the
        # two dates before it differ for two different reasons -- both asserted,
        # ...and BEFORE the opening, where the old equality did not hold at
        # all.  A ZERO seed is what the fold answered with before plan step
        # X-f3c-2a on these two dates, so asserting both figures states exactly
        # what the stored seed changed and what it did not.
        #
        #   01-14, before EVERY step: zero-seeded reads its empty prefix,
        #     $0.00; the fold reads its stored seed, $1,000.00.
        #   01-20, after the record but before the opening: zero-seeded reads
        #     the un-absorbed partial sum -$500.00 (the balance the account
        #     never had, pinned on the leaf by ``TestPreOpeningSources``); the
        #     fold reads $500.00, the seed carrying that same record.
        earlier, between = date(2026, 1, 14), date(2026, 1, 20)
        folded_before = _fold(account, scenario, [earlier, between])
        assert folded_before[earlier] == Decimal("1000.00")
        assert folded_before[between] == Decimal("500.00")
        zero_seeded_before = sample_cumulative(
            Decimal("0.00"), _recorded_steps(account, scenario),
            [earlier, between],
        )
        assert zero_seeded_before[earlier] == Decimal("0.00")
        assert zero_seeded_before[between] == Decimal("-500.00")
