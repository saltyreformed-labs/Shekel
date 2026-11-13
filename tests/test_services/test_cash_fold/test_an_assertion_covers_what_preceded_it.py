"""The instant partition and the replay: an assertion covers exactly the settles that preceded it,
to the second (finding cash D1), and a past date reads the balance in force then (finding B-18,
cash D3).
"""

from datetime import date
from decimal import Decimal

from tests._test_helpers import (
    append_balance_assertion,
    create_savings_account,
    create_settled_cash_transaction,
    create_settled_transfer,
    freeze_today,
)

from ._helpers import _instant, _fold, _opened_at


class TestSettledMoneyRidesOnTheAssertionItFollowed:
    """Finding cash D1: an assertion covers the settles that PRECEDED it.

    The defect the developer hit on 2026-07-25: a $2,000.00 transfer out of the
    Money Market, marked Paid a week after that account was last anchored,
    reduced no balance on any screen.  The shipping projection excludes every
    settled row (the anchor is assumed to reflect it) and the anchor predates it,
    so it is counted by NO producer.
    """

    def test_a_transfer_settled_after_the_assertion_reduces_the_balance(
        self, db, monkeypatch, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The reported bug, reproduced: $5,644.27 - $2,000.00 = $3,644.27.

        The real Money Market shape: opening $1,000.00 (2026-01-01), the user
        asserts $5,644.27 (2026-03-01 12:20:20), and a $2,000.00 transfer to
        Checking settles a month later (2026-04-01 19:47:44).

        **Today is moved to the end of that history**, overriding this suite's
        module freeze at 2026-03-20 (which the suite's own conftest invites a
        test to do).  The narrative is a PAST: the transfer HAS settled.  Under
        the module's clock it would settle a fortnight in its own future, which
        ruling R-EJ refuses at the write door -- correctly, because a settled
        row asserts that money has already moved.  The fixture's calendar has to
        contain its own today.

        Hand-computed: the transfer is attributed AFTER the assertion, so it
        rides on top of it and the fold reads $5,644.27 through 03-31 and
        $3,644.27 from 04-01.  $3,644.27 was also what the stored double-entry
        ledger held for the real account, while every projected balance on
        screen answered $5,644.27 -- the divergence was the finding.

        The transfer reaches the fold as each LEG's covering movement, read
        with its transfer (Transfer Invariant 5); it reached it as its SHADOW
        rows until plan step balance:X-bi-6-4a.
        """
        freeze_today(monkeypatch, date(2026, 4, 5))
        scenario = seed_user["scenario"]
        # **Opened on 2026-01-02 rather than re-stamped there** (plan step
        # X-f3c-2c).  The factory defaults the origination assertion to today,
        # which this case has frozen to 2026-04-05 -- AFTER both the March
        # assertion and the April transfer -- so an account opened at the
        # default would have its own origination govern every figure below.
        # The day is the calendar's first, which is the earliest
        # ``resolve_observation_day`` accepts.
        money_market = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("1000.00"),
            observed_on=seed_periods[0].start_date,
        )
        append_balance_assertion(
            db.session, money_market, Decimal("5644.27"),
            _instant(2026, 3, 1, 12, 20, 20),
        )
        create_settled_transfer(
            seed_user, db.session, money_market, seed_user["account"],
            seed_periods[6], amount=Decimal("2000.00"),
            settled_on=date(2026, 4, 1),
        )
        db.session.commit()

        folded = _fold(money_market, scenario, [
            date(2026, 2, 1), date(2026, 3, 1), date(2026, 3, 31),
            date(2026, 4, 1), date(2026, 4, 5),
        ])
        assert folded[date(2026, 2, 1)] == Decimal("1000.00")
        assert folded[date(2026, 3, 1)] == Decimal("5644.27")
        assert folded[date(2026, 3, 31)] == Decimal("5644.27")
        assert folded[date(2026, 4, 1)] == Decimal("3644.27")
        assert folded[date(2026, 4, 5)] == Decimal("3644.27")

    def test_a_settle_before_the_assertion_stays_absorbed(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The over-correction guard: money already inside the assertion.

        A $50.00 expense settled at 12:00:00 and an assertion of $2,932.41 at
        12:57:08 the same day.  The user asserted that figure HAVING already
        spent the $50.00, so it is inside it.

        Hand-computed: the fold reads $2,932.41 on the assertion day, NOT
        $2,882.41.  A fold that counted every settled row unconditionally --
        the naive inverse of the shipping defect -- would fail here.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, seed_periods[4], Decimal("50.00"),
            settled_on=date(2026, 3, 1), name="earlier",
        )
        append_balance_assertion(
            db.session, account, Decimal("2932.41"),
            _instant(2026, 3, 1, 12, 57, 8),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 2, 28), date(2026, 3, 1),
        ])
        assert folded[date(2026, 2, 28)] == Decimal("1000.00")
        assert folded[date(2026, 3, 1)] == Decimal("2932.41")

    def test_both_same_day_settles_go_with_the_assertion(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The discriminating control: one civil day, ONE answer (ruling R-DH (a)).

        The production shape, at the FOLD.  A -$40.00 settle at 09:00, an
        assertion of $2,932.41 at 12:57:08, and a -$60.00 settle at 20:00 --
        all three on the user's SAME civil day.  The assertion is that day's
        CLOSING balance, so both settles are inside it and the fold reads the
        asserted $2,932.41 on 03-01.

        An instant-keyed implementation answers ``2872.41`` and fails, which is
        the same discriminating role this test had before ruling R-DH inverted
        it (2026-07-31) -- see ``anchor_settle_partition.md`` for the
        ``-$4,021.37`` the instant partition rendered on production.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, seed_periods[4], Decimal("40.00"),
            settled_on=date(2026, 3, 1), name="before",
        )
        append_balance_assertion(
            db.session, account, Decimal("2932.41"),
            _instant(2026, 3, 1, 12, 57, 8),
        )
        create_settled_cash_transaction(
            seed_user, seed_periods[4], Decimal("60.00"),
            settled_on=date(2026, 3, 1), name="after",
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 2, 28), date(2026, 3, 1),
        ])
        assert folded[date(2026, 2, 28)] == Decimal("1000.00")
        assert folded[date(2026, 3, 1)] == Decimal("2932.41")


class TestEveryAssertionIsReplayed:
    """Finding B-18 / cash D3: a past date reads the balance in force THEN."""

    def test_a_past_date_reads_its_own_assertion_not_todays(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Five events, five different answers across the history.

        Stream: opening $1,000.00 (2026-01-01); -$200.00 (2026-02-01); assert
        $900.00 (2026-03-01); -$300.00 (2026-04-01); assert $500.00
        (2026-05-01).

        Hand-computed, the fold reads 1000 / 800 / 900 / 600 / 500 on those five
        days.  The shipping scalar answers TODAY's $500.00 for every one of
        them, and the shipping period map omits the pre-anchor periods entirely
        -- two producers, two different wrong answers, which is the finding.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, seed_periods[2], Decimal("200.00"),
            settled_on=date(2026, 2, 1), name="feb spend",
        )
        append_balance_assertion(
            db.session, account, Decimal("900.00"),
            _instant(2026, 3, 1),
        )
        create_settled_cash_transaction(
            seed_user, seed_periods[6], Decimal("300.00"),
            settled_on=date(2026, 4, 1), name="apr spend",
        )
        append_balance_assertion(
            db.session, account, Decimal("500.00"),
            _instant(2026, 5, 1),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1),
            date(2026, 4, 1), date(2026, 5, 1),
        ])
        assert folded[date(2026, 1, 1)] == Decimal("1000.00")
        assert folded[date(2026, 2, 1)] == Decimal("800.00")
        assert folded[date(2026, 3, 1)] == Decimal("900.00")
        assert folded[date(2026, 4, 1)] == Decimal("600.00")
        assert folded[date(2026, 5, 1)] == Decimal("500.00")
