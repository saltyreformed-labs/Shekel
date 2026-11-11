"""The walk reads no clock, is total over its shapes, and its steps reconstruct its replay."""

from datetime import date
from decimal import Decimal

from app.services.cash_ledger import cash_anchor_facts, dated_deltas, walk_cash_ledger
from app.services.balance_at._assertions import assertion_corrections
from tests._test_helpers import (
    account_never_asserted,
    create_settled_cash_transaction,
    freeze_today,
)

from ._helpers import (
    _instant,
    _opened_at,
    _assert_balance,
    _money_steps,
    _corrections,
    _running_balance,
    _fold_at,
    _replay_terminal_balance,
)


class TestTheWalkReadsNoClock:
    """Its output is a function of the account's data alone."""

    def test_the_walk_is_identical_under_two_different_todays(
        self, db, seed_user, seed_periods, monkeypatch,
    ):  # pylint: disable=unused-argument,too-many-arguments,too-many-positional-arguments
        """Freezing today at two dates yields byte-identical deltas.

        A walk that read the clock would make the ledger a function of when it
        is read (of when the sync ran, while the store was written) -- the
        corruption generator plan step A3 removed from the loan side (``4e46a0a8``).
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _opened_at(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, period, Decimal("42.00"),
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
            seed_user, period, Decimal("100.00"),
            settled_on=date(2026, 2, 1), name="pre",
        )
        _assert_balance(
            account, Decimal("2000.00"), _instant(2026, 3, 1),
        )
        create_settled_cash_transaction(
            seed_user, period, Decimal("250.00"),
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
            seed_user, period, Decimal("30.00"),
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
