"""Tests for the target rules keyed by chart key (plan task plan#2524).

Every family's target rule -- a movement's cash and counter legs, an account's
opening and true-ups, a loan payment's split and a loan's opening and
true-ups -- answers what the ledger should hold keyed by
:class:`~app.services.ledger_lines.ChartKey`, and the writer resolves the keys
to stored rows only at its emit step
(:func:`app.services._posting_write.resolve_chart_targets`).  That is what
lets the worked-out read (plan#2526) sum the very targets the writer posts.

What these cases pin:

* **the rules touch no stored chart row**: each is run under a statement
  capture, and none issues a statement naming ``budget.ledger_accounts`` (the
  rules each found or got-or-created a row there, on every call, until this
  task) or leaves a new object in the session;
* **the keys are the right ones**, with the amounts the rule books, written
  out by hand where the arithmetic is the rule's own;
* **the writer's resolve covers every kind** a rule names, and refuses the two
  broken cases (an account with no linked row, a kind no rule books on);
* **the writer mints what it minted before**: a key is resolved even when its
  merged leg nets to zero, so two same-day true-ups that cancel still mint
  their counter row, as the rule did when it resolved each correction's row
  before the merge.

The writer's own behaviour is the posting suites' to grade, and they pass
unchanged.
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
from app.models.journal_entry import JournalEntry
from app.services import account_posting_service, ledger_account_service
from app.services.ledger_lines import (
    ChartKey,
    account_correction_targets,
    account_counter_key,
    anchor_correction_targets,
    category_key,
    chart_record_type,
    linked_key,
    loan_correction_targets,
    loan_key,
    owner_bucket_key,
    payment_split_targets,
    purchase_target,
    transit_key,
    walk_account_ledger,
    worked_out_lines,
)
from app.services.loan_ledger import walk_loan_ledger
from app.services.pay_calendar import calendar_for
from app.services.posting_reads import PostingError
from app.services.transfer_legs import movement_parent, transfer_family_movements
from app.utils.dates import display_today
# The rules under test live in the worked-out ledger since plan task plan#2526,
# which moved them out of the writers that post them; the account half's two
# pieces are package-private there, and these cases reach them by name rather
# than asking for a re-export that would exist only for a test (the posture
# ``test_chart.py`` keeps for the report's label).
from app.services._posting_write import ledger_row_for, resolve_chart_targets
from app.services.ledger_lines._account_anchors import (
    _account_anchor_correction_targets,
    _counter_keys,
)
from tests._stored_ledger import (
    ledger_account_of_kind,
    stored_chart_key,
    stored_ledger_net,
)
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
                seed_user, _db.session, seed_periods[0], Decimal("40.00"),
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
                movement.account_id: purchase_target(movement, leg)
                for movement, leg in transfer_family_movements(xfer)
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
            corrections = walk_account_ledger(account.id, scenario_id)
            calendar = calendar_for(seed_user["user"].id)

            targets, statements = _statements_of(
                lambda: _account_anchor_correction_targets(
                    corrections, calendar, linked_key(account),
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
    """A loan's split and anchor targets, by key (``loan_posting_service``)."""

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
            calendar = calendar_for(seed_user["user"].id)

            (splits, anchors), statements = _statements_of(lambda: (
                payment_split_targets(walk.settled_splits, loan),
                anchor_correction_targets(
                    walk.anchor_corrections, loan, calendar,
                ),
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
            calendar = calendar_for(owner_id)

            checking = seed_user["account"]
            targets_by_account = [
                (account, account_correction_targets(
                    account, walk_account_ledger(account.id, scenario_id), calendar,
                ))
                for account in (checking, *accounts)
            ]
            targets_by_account.append((loan, loan_correction_targets(
                walk_loan_ledger(loan.id, scenario_id), loan, calendar,
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
                for line in worked_out_lines(owner_id, scenario_id, lambda: calendar)
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


class TestTheWriterResolvesEveryKind:
    """``_posting_write.ledger_row_for``: a key to the stored row it names."""

    def test_every_kind_a_rule_names_resolves_to_its_own_row(
        self, app, db, seed_user,
    ):
        """Each key resolves to a row whose own columns spell that key back.

        Graded by ``stored_chart_key``, which reads the row as stored, and
        against the family resolver's own answer, so the dispatch can neither
        cross two families nor mint a second row for one key.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            groceries = seed_user["categories"]["Groceries"]
            interest = _opened_money_market(seed_user, "Resolve Money Market")
            loan = create_loan_with_trueup(
                seed_user, _db.session,
                origination_principal=Decimal("20000.00"),
                anchor_balance=Decimal("15000.00"),
                anchor_date=date(2026, 1, 10), rate=Decimal("0.06000"),
                origination_date=date(2025, 1, 1), name="Resolve Loan",
            )
            keys = [
                linked_key(seed_user["account"]),
                category_key(groceries.id, LedgerAccountClassEnum.EXPENSE),
                owner_bucket_key(
                    LedgerAccountKindEnum.FALLBACK,
                    LedgerAccountClassEnum.INCOME,
                ),
                transit_key(),
                account_counter_key(
                    LedgerAccountKindEnum.INTEREST_INCOME, interest.id,
                ),
                loan_key(LedgerAccountKindEnum.LOAN_ESCROW, loan.id),
            ]

            rows = [ledger_row_for(user_id, key) for key in keys]

            assert [stored_chart_key(row) for row in rows] == keys
            assert [row.id for row in rows] == [
                ledger_account_of_kind(
                    _db.session, seed_user["account"].id,
                    LedgerAccountKindEnum.LINKED,
                ).id,
                ledger_account_service.get_or_create_category_ledger_account(
                    user_id, groceries.id, LedgerAccountClassEnum.EXPENSE,
                ).id,
                ledger_account_service.get_or_create_category_ledger_account(
                    user_id, None, LedgerAccountClassEnum.INCOME,
                ).id,
                ledger_account_service.get_or_create_transit_ledger_account(
                    user_id,
                ).id,
                ledger_account_service.get_or_create_account_counter_account(
                    user_id, interest.id, LedgerAccountKindEnum.INTEREST_INCOME,
                ).id,
                ledger_account_service.get_or_create_loan_ledger_account(
                    user_id, loan.id, LedgerAccountKindEnum.LOAN_ESCROW,
                ).id,
            ]

    def test_an_account_with_no_linked_row_fails_loud(
        self, app, db, seed_user,
    ):
        """A linked key whose account has no pairing raises, never mints one."""
        with app.app_context():
            unpaired = ChartKey(
                LedgerAccountKindEnum.LINKED, LedgerAccountClassEnum.ASSET,
                2_000_000_000,
            )

            with pytest.raises(PostingError, match="No ledger account is linked"):
                ledger_row_for(seed_user["user"].id, unpaired)

    def test_a_kind_no_rule_books_on_is_refused(self, app, db, seed_user):
        """An ``orphan`` key -- which no constructor builds -- names no row."""
        with app.app_context():
            orphan = ChartKey(
                LedgerAccountKindEnum.ORPHAN, LedgerAccountClassEnum.EXPENSE,
                None,
            )

            with pytest.raises(ValueError, match="no target rule books on"):
                ledger_row_for(seed_user["user"].id, orphan)


class TestTheWriterMintsWhatItMinted:
    """``resolve_chart_targets``: each key once, a zero leg included."""

    def test_a_key_shared_by_two_targets_is_resolved_once(
        self, app, db, seed_user,
    ):
        """Two targets naming one key map it to one row, looked up once.

        The cash key and the category key are each read from the store once,
        however many targets name them: two lookups of ``ledger_accounts``.
        """
        with app.app_context():
            groceries = seed_user["categories"]["Groceries"]
            cash = linked_key(seed_user["account"])
            counter = category_key(groceries.id, LedgerAccountClassEnum.EXPENSE)
            targets = {
                "first": {cash: Decimal("-5.00"), counter: Decimal("5.00")},
                "second": {cash: Decimal("-7.00"), counter: Decimal("7.00")},
            }

            resolved, statements = _statements_of(
                lambda: resolve_chart_targets(seed_user["user"].id, targets),
            )

            cash_id = ledger_row_for(seed_user["user"].id, cash).id
            counter_id = ledger_row_for(seed_user["user"].id, counter).id
            assert resolved == {
                "first": {cash_id: Decimal("-5.00"), counter_id: Decimal("5.00")},
                "second": {
                    cash_id: Decimal("-7.00"), counter_id: Decimal("7.00"),
                },
            }
            lookups = [
                s for s in statements
                if s.lstrip().upper().startswith("SELECT")
                and "FROM BUDGET.LEDGER_ACCOUNTS" in s.upper()
            ]
            assert len(lookups) == 2, lookups

    def test_two_cancelling_same_day_true_ups_still_mint_their_counter(
        self, app, db, seed_user,
    ):
        """$1,000.00 -> $1,150.00 -> $1,000.00 on one day: nothing posts, the row is minted.

        The first true-up books ``+150.00`` and the second ``-150.00``
        (``1,000.00 - 1,150.00``); sharing their source kind, period and day,
        they merge to a target that nets to zero on both legs, so no true-up
        entry is written.  The Interest Income counter row is minted all the
        same, because each correction's own delta was non-zero -- the row the
        rule minted when it resolved each correction's counter before the
        merge, and the one the writer's resolve mints now.
        """
        with app.app_context():
            account = _opened_money_market(seed_user, "Cancel Money Market")
            _assert_on(account, "1150.00", days_after_opening=1, seconds=1)
            _assert_on(account, "1000.00", days_after_opening=1, seconds=2)

            account_posting_service.sync_account_anchor_postings_all_scenarios(
                account.id,
            )

            counter = ledger_account_of_kind(
                _db.session, account.id, LedgerAccountKindEnum.INTEREST_INCOME,
            )
            assert counter is not None
            assert stored_ledger_net(
                _db.session, counter.id, seed_user["scenario"].id,
            ) == Decimal("0")
            assert _db.session.query(JournalEntry).filter_by(
                source_kind_id=ref_cache.posting_source_id(
                    PostingSourceEnum.ACCOUNT_TRUEUP,
                ),
            ).count() == 0
