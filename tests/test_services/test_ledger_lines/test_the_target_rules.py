"""Tests for the target rules keyed by chart key (plan task plan#2524).

Every family's target rule -- a movement's cash and counter legs, an account's
opening and true-ups, a loan payment's split and a loan's opening and
true-ups -- answers what the ledger should hold keyed by
:class:`~app.services.ledger_lines.ChartKey`, and the worked-out read
(:func:`app.services.ledger_lines.worked_out_lines`, plan#2526) sums those
targets as its lines.

What these cases pin:

* **the rules touch no stored chart row**: each is run under a statement
  capture, and none issues a statement naming ``budget.ledger_accounts`` (the
  rules each found or got-or-created a row there, on every call, until plan
  task plan#2524) or leaves a new object in the session;
* **the keys are the right ones**, with the amounts the rule books, written
  out by hand where the arithmetic is the rule's own;
* **a loan payment's split nets to zero by construction**: with a component
  planted out of its table, the loan's own leg drops it too (finding
  plan#2972), so no store need refuse an unbalanced split;
* **a correction books only its own account's lines** (ruling plan#2784).

The stored ledger's writer resolved each key to a stored row at its emit step
(``_posting_write.resolve_chart_targets``), and two classes here graded that
resolve and what it minted until plan step plan#2914 deleted the writer.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.extensions import db as _db
from app.models.account import Account, AccountAnchorHistory
from app.services.ledger_lines import (
    account_correction_targets,
    account_counter_key,
    anchor_correction_targets,
    category_key,
    chart_record_type,
    linked_key,
    loan_correction_targets,
    loan_key,
    payment_split_targets,
    purchase_target,
    transit_key,
    worked_out_lines,
)
from app.services.cash_ledger import walk_cash_ledger
from app.services.loan_ledger import walk_loan_ledger
from app.services.transfer_legs import movement_parent, transfer_sides
from app.utils.dates import display_today
# The rules under test live in the worked-out ledger since plan task plan#2526,
# which moved them out of the writers that posted them; the account half's
# pieces are package-private there -- its two rules, and since plan task
# plan#3155 the framing of an account's walk into its corrections, which the
# read calls over each account's walk -- and these cases reach them by name
# rather than asking for a re-export that would exist only for a test (the
# posture ``test_chart.py`` keeps for the report's label).
from app.services.ledger_lines._account_anchors import (
    _account_anchor_correction_targets,
    _counter_keys,
)
from app.services.ledger_lines._account_walk import account_corrections
# The split's component table, read to plant a component out of it (finding
# plan#2972); the plant itself names the module by path.
from app.services.ledger_lines._loan_payments import _LOAN_CORRECTION_COMPONENTS
from tests._test_helpers import (
    create_account_of_type,
    create_loan_with_trueup,
    create_settled_cash_transaction,
    create_settled_transfer,
    freeze_today,
)


def _statements_of(fn):
    """Run *fn* and return its result and every SQL statement it issued."""
    statements = []

    def _capture(_conn, _cursor, statement, *_args):
        """Record the statement's text."""
        statements.append(statement)

    event.listen(_db.engine, "before_cursor_execute", _capture)
    try:
        result = fn()
    finally:
        event.remove(_db.engine, "before_cursor_execute", _capture)
    return result, statements


def _assert_the_chart_is_untouched(statements):
    """Assert no statement named the stored chart and nothing new is pending."""
    chart = [s for s in statements if "ledger_accounts" in s]
    assert chart == [], chart
    assert not _db.session.new


def _opened_money_market(seed_user, name):
    """Return a Money Market (an INTEREST account) opened 3 days ago at $1,000."""
    return create_account_of_type(
        seed_user, _db.session, "Money Market", name,
        anchor_balance=Decimal("1000.00"),
        observed_on=display_today() - timedelta(days=3),
    )


def _assert_on(account, balance, *, days_after_opening, seconds):
    """Append a balance assertion on *account*, observed after its opening.

    Args:
        account: The account.
        balance: The asserted balance, a string.
        days_after_opening: How many days after the opening's own day.
        seconds: How far after the opening's recording instant it was entered,
            which orders two assertions observed on one day.
    """
    opening = (
        _db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account.id)
        .order_by(AccountAnchorHistory.observed_on, AccountAnchorHistory.id)
        .first()
    )
    observed_on = opening.observed_on + timedelta(days=days_after_opening)
    _db.session.add(AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=Decimal(balance),
        created_at=opening.created_at + timedelta(seconds=seconds),
        observed_on=observed_on,
        recorded_on=observed_on,
    ))
    _db.session.flush()


class TestTheMovementRuleNamesKeys:
    """``ledger_lines.purchase_target``: a movement's two legs, by key."""

    def test_a_paid_expense_books_its_account_against_its_category(
        self, app, db, seed_user, seed_periods,
    ):
        """A $40.00 Groceries payment: Checking -40.00, Groceries Expense +40.00.

        The cash leg is the movement's whole figure in its parent's direction
        (an expense takes money out: ``-40.00``), the counter leg its negative
        on the parent's category in the Expense class.
        """
        with app.app_context():
            groceries = seed_user["categories"]["Groceries"]
            txn = create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("40.00"),
                category=groceries,
            )
            (movement,) = txn.entries

            target, statements = _statements_of(
                lambda: purchase_target(movement, movement_parent(movement)),
            )

            assert target == {
                linked_key(seed_user["account"]): Decimal("-40.00"),
                category_key(groceries.id, LedgerAccountClassEnum.EXPENSE):
                    Decimal("40.00"),
            }
            _assert_the_chart_is_untouched(statements)

    def test_each_side_of_a_transfer_books_against_transit(
        self, app, db, seed_user, seed_periods,
    ):
        """$250.00 Checking -> Savings: each side against Transfers in transit.

        From-side ``{Checking -250.00, Transit +250.00}``, to-side
        ``{Savings +250.00, Transit -250.00}``: the transit account nets to
        zero once both have cleared (ruling R-BAL45).
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "Keys Savings",
            )
            xfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("250.00"),
            )

            targets, statements = _statements_of(lambda: {
                leg.record.account_id: purchase_target(leg.record, leg)
                for leg in transfer_sides(xfer)
            })

            assert targets == {
                seed_user["account"].id: {
                    linked_key(seed_user["account"]): Decimal("-250.00"),
                    transit_key(): Decimal("250.00"),
                },
                savings.id: {
                    linked_key(savings): Decimal("250.00"),
                    transit_key(): Decimal("-250.00"),
                },
            }
            _assert_the_chart_is_untouched(statements)


class TestTheAccountCorrectionRuleNamesKeys:
    """The account corrections' targets, by key (``ledger_lines._account_anchors``)."""

    def test_an_opening_and_a_true_up_name_their_two_counters(
        self, app, db, seed_user,
    ):
        """$1,000.00 opened, trued up to $1,150.00: Opening equity, then Interest.

        The opening books ``+1,000.00`` on the account (nothing before it) and
        ``-1,000.00`` on its equity counter; the true-up ``+150.00``
        (``1,150.00 - 1,000.00``) and ``-150.00`` on its Interest Income
        counter, an INTEREST account's true-up being interest (ruling R-FO).
        The opening's own assertion, on the opening's day, is a true-up of
        nothing (``1,000.00 - 1,000.00``): its key holds an empty target, so
        an entry it once posted would reverse, and it names no counter.
        """
        with app.app_context():
            account = _opened_money_market(seed_user, "Keys Money Market")
            _assert_on(account, "1150.00", days_after_opening=1, seconds=1)
            scenario_id = seed_user["scenario"].id
            corrections = account_corrections(
                walk_cash_ledger(account.id, scenario_id),
            )

            targets, statements = _statements_of(
                lambda: _account_anchor_correction_targets(
                    corrections, linked_key(account),
                    _counter_keys(account, corrections),
                ),
            )

            opening_kind = ref_cache.posting_kind_id(PostingKindEnum.OPENING)
            trueup_kind = ref_cache.posting_kind_id(PostingKindEnum.TRUEUP)
            own = linked_key(account)
            assert sorted(targets.values(), key=len) == [
                {},
                {
                    own: (Decimal("1000.00"), opening_kind),
                    account_counter_key(
                        LedgerAccountKindEnum.ANCHOR_EQUITY, account.id,
                    ): (Decimal("-1000.00"), opening_kind),
                },
                {
                    own: (Decimal("150.00"), trueup_kind),
                    account_counter_key(
                        LedgerAccountKindEnum.INTEREST_INCOME, account.id,
                    ): (Decimal("-150.00"), trueup_kind),
                },
            ]
            _assert_the_chart_is_untouched(statements)


class TestTheLoanRulesNameKeys:
    """A loan's split and anchor targets, by key (``ledger_lines``' loan rules)."""

    @pytest.fixture(autouse=True)
    def _freeze_today(self, monkeypatch):
        """Freeze today after every payment period the case settles in."""
        freeze_today(monkeypatch, date(2026, 5, 15))

    def test_a_payment_and_the_anchors_name_the_loans_lines(
        self, app, db, seed_user, seed_periods,
    ):
        """$1,000.00 on a 6% loan trued up to $100,000.00: interest $500.00.

        The month's interest is ``100,000.00 x 0.06 / 12 = 500.00``, so the
        split moves ``-500.00`` off the loan's own line onto its Interest line
        (no escrow, no excess).  The anchors book the $250,000.00 opening and
        the true-up to $100,000.00 (owed ``250,000.00`` before it, so
        ``150,000.00`` down) on the loan's own line against its opening
        equity.
        """
        with app.app_context():
            loan = create_loan_with_trueup(
                seed_user, _db.session,
                origination_principal=Decimal("250000.00"),
                anchor_balance=Decimal("100000.00"),
                anchor_date=date(2026, 1, 10), rate=Decimal("0.06000"),
                origination_date=date(2025, 1, 1), name="Keys Loan",
            )
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], loan,
                seed_periods[1], amount=Decimal("1000.00"),
            )
            walk = walk_loan_ledger(loan.id, seed_user["scenario"].id)

            (splits, anchors), statements = _statements_of(lambda: (
                payment_split_targets(walk.settled_splits, loan),
                anchor_correction_targets(walk.anchor_corrections, loan),
            ))

            own = linked_key(loan)
            equity = loan_key(LedgerAccountKindEnum.EQUITY_OPENING, loan.id)
            opening_kind = ref_cache.posting_kind_id(PostingKindEnum.OPENING)
            trueup_kind = ref_cache.posting_kind_id(PostingKindEnum.TRUEUP)
            assert list(splits.values()) == [{
                own: (
                    Decimal("-500.00"),
                    ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
                ),
                loan_key(LedgerAccountKindEnum.LOAN_INTEREST, loan.id): (
                    Decimal("500.00"),
                    ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
                ),
            }]
            # A loan's ledger is debit-positive owed-negative: the opening
            # puts 250,000.00 owed on (-), the true-up takes 150,000.00 off (+).
            assert sorted(
                anchors.values(), key=lambda legs: legs[own][0],
            ) == [
                {
                    own: (Decimal("-250000.00"), opening_kind),
                    equity: (Decimal("250000.00"), opening_kind),
                },
                {
                    own: (Decimal("150000.00"), trueup_kind),
                    equity: (Decimal("-150000.00"), trueup_kind),
                },
            ]
            _assert_the_chart_is_untouched(statements)

    def test_a_split_missing_a_component_still_nets_to_zero(
        self, app, seed_user, seed_periods, monkeypatch,
    ):
        """$150,000.00 on a loan trued up to $100,000.00, then the excess planted out.

        The month's interest is ``100,000.00 x 0.06 / 12 = 500.00``; the
        principal is capped at the ``100,000.00`` that closes the loan, so the
        excess is ``150,000.00 - 500.00 - 100,000.00 = 49,500.00``.  The split
        moves ``500.00 + 49,500.00 = 50,000.00`` off the loan's own line, and
        the four legs net to zero.

        The plant is finding plan#2972's dropped excess.  The finding dropped
        it from the loan's own leg, which restated ``interest + escrow +
        excess`` beside the component table; that leg is now summed from the
        component legs as built, so the one place left to drop the excess is
        the table, and the loan's leg drops it too: ``-500.00`` against
        ``+500.00``, still balanced.  The old leg kept ``-50,000.00`` here,
        ``49,500.00`` out of balance, which only the stored ledger's writer
        refused.  What the plant leaves wrong is the loan's balance, and the
        loan's figure tests grade that (``test_payoff_overpayment`` in
        ``test_loan_fold_oracle.py``).
        """
        with app.app_context():
            loan = create_loan_with_trueup(
                seed_user, _db.session,
                origination_principal=Decimal("250000.00"),
                anchor_balance=Decimal("100000.00"),
                anchor_date=date(2026, 1, 10), rate=Decimal("0.06000"),
                origination_date=date(2025, 1, 1), name="Payoff Keys Loan",
            )
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], loan,
                seed_periods[1], amount=Decimal("150000.00"),
            )
            walk = walk_loan_ledger(loan.id, seed_user["scenario"].id)
            own = linked_key(loan)
            interest = loan_key(LedgerAccountKindEnum.LOAN_INTEREST, loan.id)
            principal_kind = ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL)
            interest_kind = ref_cache.posting_kind_id(PostingKindEnum.INTEREST)

            assert list(payment_split_targets(
                walk.settled_splits, loan,
            ).values()) == [{
                own: (Decimal("-50000.00"), principal_kind),
                interest: (Decimal("500.00"), interest_kind),
                loan_key(LedgerAccountKindEnum.LOAN_REFUND, loan.id): (
                    Decimal("49500.00"),
                    ref_cache.posting_kind_id(PostingKindEnum.REFUND),
                ),
            }]

            monkeypatch.setattr(
                "app.services.ledger_lines._loan_payments."
                "_LOAN_CORRECTION_COMPONENTS",
                tuple(
                    component for component in _LOAN_CORRECTION_COMPONENTS
                    if component[0] is not LedgerAccountKindEnum.LOAN_REFUND
                ),
            )
            planted = list(payment_split_targets(
                walk.settled_splits, loan,
            ).values())

            assert planted == [{
                own: (Decimal("-500.00"), principal_kind),
                interest: (Decimal("500.00"), interest_kind),
            }]
            assert sum(amount for amount, _kind in planted[0].values()) == 0


# Every source kind that is a correction rather than a movement.  Listed by hand
# rather than by subtraction, so a new kind fails the census below until its rule
# is shown here, rather than joining the set unmeasured.
_CORRECTION_SOURCES = {
    PostingSourceEnum.ACCOUNT_OPENING,
    PostingSourceEnum.ACCOUNT_TRUEUP,
    PostingSourceEnum.LOAN_OPENING,
    PostingSourceEnum.LOAN_TRUEUP,
    PostingSourceEnum.LOAN_PAYMENT,
}


def _assert_books_only_its_own_lines(account, targets):
    """Assert every leg of *account*'s corrections is on one of its own lines.

    Args:
        account: The account the corrections are of.
        targets: Its correction targets, ``{correction key: {chart key: leg}}``.
    """
    foreign = [
        key for legs in targets.values() for key in legs
        if chart_record_type(key) is not Account or key.record_id != account.id
    ]
    assert not foreign, (
        f"a correction of account {account.id} books on another's line: {foreign}"
    )


def _sorted_lines(lines):
    """Return ``(key, amount)`` pairs in one comparable order, without a $0.00 one."""
    return sorted(
        ((repr(key), amount) for key, amount in lines if amount != 0),
    )


class TestACorrectionBooksOnlyItsOwnAccountsLines:
    """Ruling plan#2784: what lets a permanent delete take an account's balance records.

    The account hard delete deleted its ledger-history guard on this property: a
    delete takes an account's lines, and every correction it books has both halves
    among them, so what is left still balances.  A rule booking across accounts
    fails here rather than needing a guard.
    """

    @pytest.fixture(autouse=True)
    def _freeze_today(self, monkeypatch):
        """Freeze today after every payment period the case settles in."""
        freeze_today(monkeypatch, date(2026, 5, 15))

    def test_every_correction_of_every_kind_of_account(
        self, app, db, seed_user, seed_periods,
    ):
        """A true-up of each projection kind, and a loan's opening, true-up and split.

        The census says every correction source was booked, so no rule's
        absence reads as a pass; and the targets are all the read's corrections
        (it books none of its own), so a family the read adds cannot sit outside
        this.
        """
        with app.app_context():
            owner_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            accounts = [
                create_account_of_type(
                    seed_user, _db.session, type_name, type_name,
                    anchor_balance=Decimal("1000.00"),
                    observed_on=display_today() - timedelta(days=3),
                )
                for type_name in ("Savings", "Money Market", "Roth IRA", "Property")
            ]
            for account in accounts:
                _assert_on(account, "1150.00", days_after_opening=1, seconds=1)
            loan = create_loan_with_trueup(
                seed_user, _db.session,
                origination_principal=Decimal("250000.00"),
                anchor_balance=Decimal("100000.00"),
                anchor_date=date(2026, 1, 10), rate=Decimal("0.06000"),
                origination_date=date(2025, 1, 1), name="Own Lines Loan",
                escrow_annual=Decimal("1200.00"),
            )
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], loan,
                seed_periods[1], amount=Decimal("1000.00"),
            )

            checking = seed_user["account"]
            targets_by_account = [
                (account, account_correction_targets(
                    account,
                    account_corrections(walk_cash_ledger(account.id, scenario_id)),
                ))
                for account in (checking, *accounts)
            ]
            targets_by_account.append((loan, loan_correction_targets(
                walk_loan_ledger(loan.id, scenario_id), loan,
            )))

            for account, targets in targets_by_account:
                _assert_books_only_its_own_lines(account, targets)

            assert {
                source_kind_id
                for _, targets in targets_by_account
                for (source_kind_id, _period, _day), legs in targets.items()
                if legs
            } == {
                ref_cache.posting_source_id(source) for source in _CORRECTION_SOURCES
            }
            assert _sorted_lines(
                (key, amount)
                for _, targets in targets_by_account
                for legs in targets.values()
                for key, (amount, _kind) in legs.items()
            ) == _sorted_lines(
                (line.key, line.amount)
                for line in worked_out_lines(owner_id, scenario_id)
                if line.movement_id is None
            )

    def test_the_assertion_fires_on_a_leg_on_another_accounts_line(
        self, app, db, seed_user,
    ):
        """The pin's negative control: a counter leg on another account's line."""
        with app.app_context():
            account = seed_user["account"]
            other = account_counter_key(
                LedgerAccountKindEnum.ANCHOR_EQUITY, account.id + 1,
            )
            own = linked_key(account)
            with pytest.raises(AssertionError, match="another's line"):
                _assert_books_only_its_own_lines(
                    account, {("key",): {own: (Decimal("1"), 0), other: (Decimal("-1"), 0)}},
                )
            with pytest.raises(AssertionError, match="another's line"):
                _assert_books_only_its_own_lines(
                    account, {("key",): {transit_key(): (Decimal("1"), 0)}},
                )
