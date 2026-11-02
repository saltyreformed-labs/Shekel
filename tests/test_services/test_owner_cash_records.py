"""Tests for the owner's cash load (plan task plan#2648, ruling plan#2623).

:func:`app.services.cash_ledger.owner_cash_records` loads one owner's dated
movements, and every account's opening and balance checks it is asked to
walk, each kind ONCE for the owner, and hands back each account's walk built
from its own slice.  What these cases pin:

* **each walk is its account's own**: equal, field for field, to
  :func:`app.services.cash_ledger.walk_cash_ledger` for that account, over an
  owner holding a restated opening, a true-up, a bill and transfers to an
  account it walks and to one it does not;
* **an account with books and no balance check walks no source**, from
  either door, though a settled movement sits on it;
* **the movements are the owner's alone**, each once, beside its parent:
  asked for another owner's scenario, the owner's load holds none of that
  owner's movements;
* **an account the owner does not hold is refused**, and **an account whose
  opening is missing is refused** by the message
  :func:`app.services.cash_ledger.account_opening_fact` refuses it with.

That the read's statements stay fixed as the accounts grow is
``test_ledger_lines/test_the_worked_out_read.py``'s.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import AccountOpeningSourceEnum
from app.extensions import db as _db
from app.models.account import AccountAnchorHistory
from app.models.transaction_entry import TransactionEntry
from app.services import opening_service
from app.services.cash_ledger import (
    account_opening_fact,
    owner_cash_records,
    walk_cash_ledger,
)
from tests._test_helpers import (
    account_never_asserted,
    create_account_of_type,
    create_loan_with_trueup,
    create_settled_cash_transaction,
    create_settled_transfer,
    freeze_today,
)

# Inside seed_periods' last paycheck, after every paycheck a case files in.
_TODAY = date(2026, 5, 15)


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch):
    """Freeze the app's today at :data:`_TODAY` for every case."""
    freeze_today(monkeypatch, _TODAY)


def _owner_with_records(seed_user, seed_periods):
    """Return the seed owner's Checking, a Savings and a loan, each with records.

    Checking's books are restated once (a second opening row, the later
    governing), Savings carries a true-up on top of its opening assertion,
    and a bill, a transfer into Savings and a payment into the loan each
    settle in the second paycheck.

    Returns:
        ``(checking, savings, loan)``.
    """
    checking = seed_user["account"]
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "Owned Savings",
    )
    loan = create_loan_with_trueup(
        seed_user, _db.session,
        origination_principal=Decimal("250000.00"),
        anchor_balance=Decimal("100000.00"),
        anchor_date=date(2026, 1, 10), rate=Decimal("0.06000"),
        origination_date=date(2025, 1, 1), name="Owned Loan",
    )
    opening_service.stage_account_opening(
        account=checking,
        opening=opening_service.BooksOpening(
            opened_on=account_opening_fact(checking.id).opened_on,
            equity=Decimal("1234.56"),
        ),
        source=AccountOpeningSourceEnum.USER_DECLARED,
    )
    trued_on = _TODAY - timedelta(days=1)
    _db.session.add(AccountAnchorHistory(
        account_id=savings.id, anchor_balance=Decimal("400.00"),
        observed_on=trued_on, recorded_on=trued_on,
    ))
    period = seed_periods[1]
    create_settled_cash_transaction(
        seed_user, _db.session, period, Decimal("40.00"),
    )
    create_settled_transfer(
        seed_user, _db.session, checking, savings, period,
        amount=Decimal("250.00"),
    )
    create_settled_transfer(
        seed_user, _db.session, checking, loan, period,
        amount=Decimal("1000.00"),
    )
    _db.session.commit()
    _db.session.expire_all()
    return checking, savings, loan


class TestEachWalkIsItsAccountsOwn:
    """The owner's one load hands each account the walk its own read gives."""

    def test_each_walk_equals_its_accounts_walk(
        self, app, db, seed_user, seed_periods,
    ):
        """Checking's and Savings' walks equal ``walk_cash_ledger``'s for each.

        Equal as frozen values: the same opening, the same balance checks
        (each account's first marked its opening) and the same source facts
        in the same order.  Checking's opening is the restatement, 1,234.56,
        because the later row governs; Savings walks its opening check and
        the 400.00 true-up, and its one source is the 250.00 transfer in.
        """
        with app.app_context():
            checking, savings, _loan = _owner_with_records(
                seed_user, seed_periods,
            )
            scenario_id = seed_user["scenario"].id

            records = owner_cash_records(
                seed_user["user"].id, scenario_id, [checking, savings],
            )

            assert records.walks == {
                checking.id: walk_cash_ledger(checking.id, scenario_id),
                savings.id: walk_cash_ledger(savings.id, scenario_id),
            }
            assert records.walks[checking.id].opening.opening_equity == (
                Decimal("1234.56")
            )
            savings_walk = records.walks[savings.id]
            assert [
                (fact.anchor_balance, fact.is_opening)
                for fact in savings_walk.anchor_facts
            ] == [(Decimal("100.00"), True), (Decimal("400.00"), False)]
            assert [fact.delta for fact in savings_walk.source_facts] == [
                Decimal("250.00"),
            ]

    def test_an_account_with_books_and_no_balance_check_walks_no_source(
        self, app, db, seed_user, seed_periods,
    ):
        """Books opened, no balance check, a $40.00 bill settled on it: no source.

        An account built outside the factory carries neither row; its books
        are then opened (500.00 on 2026-01-01) and a bill settles on it.  Its
        walk from either door holds the opening, no balance check and no
        source, as :func:`app.services.cash_ledger.walk_cash_ledger` states
        for an account with no assertion; the owner's movements still hold
        the bill's.
        """
        with app.app_context():
            account = account_never_asserted(
                seed_user, _db.session, name="Unchecked Checking",
            )
            opening_service.stage_account_opening(
                account=account,
                opening=opening_service.BooksOpening(
                    opened_on=date(2026, 1, 1), equity=Decimal("500.00"),
                ),
                source=AccountOpeningSourceEnum.USER_DECLARED,
            )
            create_settled_cash_transaction(
                seed_user, _db.session, seed_periods[1], Decimal("40.00"),
                account=account,
            )
            _db.session.commit()
            scenario_id = seed_user["scenario"].id

            records = owner_cash_records(
                seed_user["user"].id, scenario_id, [account],
            )

            walk = records.walks[account.id]
            assert walk == walk_cash_ledger(account.id, scenario_id)
            assert walk.opening.opening_equity == Decimal("500.00")
            assert (walk.anchor_facts, walk.source_facts) == ([], [])
            assert account.id in {
                entry.account_id for entry, _parent in records.movements
            }

    def test_the_movements_are_the_owners_each_once(
        self, app, db, seed_user, seed_periods, seed_second_user,
        seed_second_periods,
    ):
        """Every dated movement the owner holds, the loan's too, and no other's.

        The loan is not walked, so its payment's record is in the movements
        and in no walk.  The second owner's $75.00 bill is in their own
        scenario, so the owner's load asked for THAT scenario holds nothing:
        what keeps another owner's movement out is the owner scope, which the
        scenario alone would not grade.
        """
        with app.app_context():
            checking, savings, loan = _owner_with_records(
                seed_user, seed_periods,
            )
            create_settled_cash_transaction(
                seed_second_user, _db.session, seed_second_periods[1],
                Decimal("75.00"),
            )
            _db.session.commit()
            owner_id = seed_user["user"].id

            records = owner_cash_records(
                owner_id, seed_user["scenario"].id, [checking, savings],
            )
            elsewhere = owner_cash_records(
                owner_id, seed_second_user["scenario"].id, [],
            )

            dated = sorted(
                entry.id for entry in _db.session.query(TransactionEntry)
                .filter(
                    TransactionEntry.owner_id == owner_id,
                    TransactionEntry.settled_on.isnot(None),
                )
            )
            assert [entry.id for entry, _parent in records.movements] == dated
            assert loan.id in {
                entry.account_id for entry, _parent in records.movements
            }
            assert set(records.walks) == {checking.id, savings.id}
            assert _db.session.query(TransactionEntry).filter(
                TransactionEntry.owner_id == seed_second_user["user"].id,
                TransactionEntry.settled_on.isnot(None),
            ).count() == 1
            assert elsewhere.movements == []


class TestTheLoadRefusesWhatItCannotWalk:
    """The owner's load refuses an account it would walk wrongly."""

    def test_an_account_another_owner_holds_is_refused(
        self, app, db, seed_user, seed_periods, seed_second_user,
    ):
        """The second owner's Checking, asked for under the first owner: refused.

        Its opening and balance checks would load by its id while its
        movements, scoped by the first owner, would not.
        """
        with app.app_context():
            with pytest.raises(ValueError, match="not owner id="):
                owner_cash_records(
                    seed_user["user"].id, seed_user["scenario"].id,
                    [seed_user["account"], seed_second_user["account"]],
                )

    def test_an_account_built_without_its_opening_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """An account carrying no ``AccountOpening`` row: the reader's refusal."""
        with app.app_context():
            account = account_never_asserted(
                seed_user, _db.session, name="Doomed Checking",
            )
            _db.session.commit()

            with pytest.raises(RuntimeError, match="zero AccountOpening rows"):
                owner_cash_records(
                    seed_user["user"].id, seed_user["scenario"].id,
                    [seed_user["account"], account],
                )
