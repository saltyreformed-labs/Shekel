"""The fold sees this account's rows in this scenario and nothing else, and it refuses no date and
no account.
"""

from datetime import date
from decimal import Decimal

from tests._test_helpers import (
    account_never_asserted,
    add_txn,
    create_savings_account,
    create_settled_cash_transaction,
)

from ._helpers import _instant, _fold, _BOOKS_OPEN_ON, _opened_at


class TestScope:
    """The fold sees this account's rows in this scenario, and nothing else."""

    def test_another_scenarios_planned_rows_do_not_enter_the_fold(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A what-if scenario's plan must not move the baseline.

        Assertions are per-ACCOUNT and replay in every scenario; only the
        transaction rows are scenario-scoped.  Hand-computed: a $400.00 planned
        expense in a non-baseline scenario leaves the baseline fold at
        $1,000.00 on 04-08, while the SAME assertions fold to $600.00 in the
        scenario that owns the row.
        """
        from app.models.scenario import Scenario  # pylint: disable=import-outside-toplevel

        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        other = Scenario(
            user_id=seed_user["user"].id, name="What if", is_baseline=False,
        )
        db.session.add(other)
        db.session.flush()
        add_txn(
            db.session, seed_user, seed_periods[6], "what-if bill", "400.00",
            due_date=date(2026, 4, 8), scenario=other,
        )
        db.session.commit()

        as_of = date(2026, 4, 2)
        baseline = _fold(account, scenario, [date(2026, 4, 8)], as_of=as_of)
        assert baseline[date(2026, 4, 8)] == Decimal("1000.00")
        what_if = _fold(account, other, [date(2026, 4, 8)], as_of=as_of)
        assert what_if[date(2026, 4, 8)] == Decimal("600.00")

    def test_another_accounts_planned_rows_do_not_enter_the_fold(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A sibling account's plan is not this account's plan.

        Hand-computed: a $250.00 planned expense on a Savings account leaves
        Checking's fold at $1,000.00 on 04-08.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        savings = create_savings_account(
            seed_user, db.session, "Savings", Decimal("50.00"),
        )
        add_txn(
            db.session, seed_user, seed_periods[6], "their bill", "250.00",
            due_date=date(2026, 4, 8), account=savings,
        )
        db.session.commit()

        folded = _fold(account, scenario, [date(2026, 4, 8)],
                       as_of=date(2026, 4, 2))
        assert folded[date(2026, 4, 8)] == Decimal("1000.00")


class TestTotality:
    """No date is refused and no account is (plan Section 3).

    A partial producer forces every caller to compose it with a seed, a flag or
    a fallback, and every composition is a new producer that can disagree with
    the others.  A total function has nothing to compose with.
    """

    def test_a_date_before_every_event_reads_the_seed(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A date before every event reads the STORED seed, whatever its distance.

        With a $1,000.00 opening equity and the $500.00 record on 2026-01-15,
        every date before 01-15 reads $1,000.00 -- 2020, 2025, the day before.
        Hand-computed and identical across all three: totality is a property of
        the fold, not of how far back the caller asks.

        *It asserted $1,500.00 until plan step X-f3c-2a, when the seed stopped
        being the earliest assertion back-computed over its own records and
        became the figure ``budget.account_openings`` holds.*
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 2, 1), books_open_on=_BOOKS_OPEN_ON)
        create_settled_cash_transaction(
            seed_user, seed_periods[0], Decimal("500.00"),
            settled_on=date(2026, 1, 15), name="between-books-and-assertion",
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2020, 1, 1), date(2025, 6, 30), date(2026, 1, 14),
        ])
        assert set(folded.values()) == {Decimal("1000.00")}

    def test_an_account_with_no_assertion_history_folds_from_its_opening(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Production-unreachable, and honestly its OPENING rather than zero.

        Migration ``cfb15e782f86`` plus the account factory guarantee an
        assertion row, so this state cannot occur.  When it is forced, the fold
        stays total and answers the level the account's books opened with --
        $1,000.00 here -- because that level is a fact about the ACCOUNT and
        does not depend on any assertion existing.

        *It answered $0.00 until plan step X-f3c-2a, which was the honest fold
        of no facts when the seed was derived FROM the assertion series.  With
        the seed stored, an account with no assertions still has an opening, and
        answering zero would report a level nothing recorded.  The one state
        that DOES raise is a missing opening record -- see
        ``TestAMissingOpeningRecordIsRefused``.*
        """
        # **Built rather than emptied** (plan step X-f3c-2c): an assertion is
        # append-only at the database tier, so an account that has asserted
        # nothing is one the assertion factory never touched.  It carries the
        # same ``$1,000.00`` opening the seeded account does, which is the
        # level this case says the fold answers from.
        scenario = seed_user["scenario"]
        account = account_never_asserted(
            seed_user, db.session, name="Silent",
            opening_equity=Decimal("1000.00"),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 1, 1), date(2026, 6, 1),
        ])
        assert folded[date(2026, 1, 1)] == Decimal("1000.00")
        assert folded[date(2026, 6, 1)] == Decimal("1000.00")

    def test_an_empty_dates_list_is_an_empty_map(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Asking about no dates answers about no dates."""
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        db.session.commit()

        assert _fold(account, scenario, []) == {}

    def test_a_far_future_date_holds_the_last_step_flat(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Beyond every event the running total simply stops moving.

        Hand-computed: a $1,000.00 opening with a -$250.00 planned bill due
        2026-04-08 reads $750.00 on 04-08 and still $750.00 in 2040.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        _opened_at(account, _instant(2026, 1, 1))
        add_txn(
            db.session, seed_user, seed_periods[6], "bill", "250.00",
            due_date=date(2026, 4, 8),
        )
        db.session.commit()

        folded = _fold(account, scenario, [
            date(2026, 4, 8), date(2040, 1, 1),
        ], as_of=date(2026, 4, 2))
        assert folded[date(2026, 4, 8)] == Decimal("750.00")
        assert folded[date(2040, 1, 1)] == Decimal("750.00")

    def test_a_liability_accounts_negative_anchor_folds_ledger_native(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The fold never branches on account class (ruling R-J, kind-blind).

        A Credit Card carries an owed-as-NEGATIVE anchor, and a direct charge on
        it is an EXPENSE, so it must make the balance MORE negative.
        Hand-computed: an asserted -$300.00 with a $75.00 charge settled after it
        folds to -$375.00.  Nothing in the fold consults the account's kind to
        get that right, which is what makes the claim structural.
        """
        from tests._test_helpers import create_account_of_type  # pylint: disable=import-outside-toplevel

        scenario = seed_user["scenario"]
        card = create_account_of_type(
            seed_user, db.session, "Credit Card", "Visa", Decimal("-300.00"),
        )
        _opened_at(card, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, seed_periods[4], Decimal("75.00"),
            account=card, settled_on=date(2026, 3, 1), name="charge",
        )
        db.session.commit()

        folded = _fold(card, scenario, [
            date(2026, 2, 1), date(2026, 3, 1),
        ])
        assert folded[date(2026, 2, 1)] == Decimal("-300.00")
        assert folded[date(2026, 3, 1)] == Decimal("-375.00")
