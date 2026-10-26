"""The helpers and constants two or more of this package's parts read: the module-level helpers of
``tests/test_services/test_cc5_4a1_settlement_subject.py``, moved verbatim by ``tools/split``.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.models.statement_match import StatementMatchMember
from app.services import match_press, match_withdrawal, transaction_service
from app.services.cash_ledger import settled_cash_facts
from app.services.settle_day import SettleDay
from app.services.statement_match import accept_match
from tests._test_helpers import create_account_of_type, generate_row_of, make_expense_template
from tests.test_services.test_statement_match._builders import a_scope, a_submission


_HOTEL = Decimal("120.00")


def _card(seed_user, name="Rewards Card"):
    """Create an active Credit Card account for *seed_user*.

    Its origination assertion sits on the bootstrap period's first day, as the
    checking account's does, so a payment dated after it is the ledger's own
    posting rather than money the anchor true-up absorbs (see
    :func:`_first_day`).
    """
    return create_account_of_type(
        seed_user, db.session, "Credit Card", name,
        anchor_balance=Decimal("-500.00"),
        observed_on=seed_user["bootstrap_period"].start_date,
    )


def _hotel_bill(seed_user, period, *, account=None):
    """One engine-generated `$120.00` Hotel bill on checking (or *account*)."""
    template = make_expense_template(
        db.session, seed_user, amount=str(_HOTEL),
        name="Hotel", category_key="Rent", account=account,
    )
    row = generate_row_of(template, period)
    db.session.commit()
    return row


def _movement(txn):
    """Return the row's one covering movement, re-read from the database."""
    db.session.expire(txn)
    (movement,) = txn.covering_movements
    return movement


def _revert(txn):
    """Put *txn* back to Projected through the door production reverts by."""
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()


def _correct_tender(txn, account_id):
    """The full-edit popover's identity Save naming another 'Paid from' account.

    Posting back the lines its "Paid from" caption names, as the popover does
    since plan step ``credit_card:CC-5-4a-5`` (ruling **R-CC127**).
    """
    with match_press.Press(match_press.Shown(
            match_withdrawal.pending_for_movements(
                txn.covering_movements,
            ).line_ids,
        )) as press:
        transaction_service.apply_requested_status(
            txn, txn.status_id, stated=transaction_service.StatedRecord(tender_account_id=account_id),
            press=press,
        )
    db.session.commit()


def _entered(day):
    return SettleDay(day=day, basis=SettledDayBasisEnum.ENTERED)


def _first_day(seed_user):
    """The first day a settle on the fixture accounts is NOT absorbed.

    Every fixture account's origination assertion sits on the bootstrap
    period's first day, and the anchor true-up posts a correction against any
    movement dated on or before an assertion's day -- so a settle ON that day
    reads on the fold but nets to nothing on the posted ledger.  One day
    later is the first day the ledger carries it as its own posting.
    """
    return seed_user["bootstrap_period"].start_date + timedelta(days=1)


def _offered_for(seed_user, account, txn):
    """Return what *account*'s screen offers FOR *txn*, under either subject."""
    return [
        row for row in a_scope(seed_user, account).candidates.rows
        if row.transaction_id == txn.id
    ]


def _per_day(account_id, scenario_id):
    """Sum the settled cash facts on *account_id* per settle day."""
    sums = {}
    for fact in settled_cash_facts(account_id, scenario_id):
        sums[fact.settled_on] = sums.get(fact.settled_on, Decimal("0")) + fact.delta
    return sums


def _accept(seed_user, account, lines, transactions):
    """Accept a match on *account*'s screen naming *transactions* as offered."""
    scope = a_scope(seed_user, account)
    accepted = accept_match(
        a_submission(scope, lines=lines, transactions=transactions), scope,
    )
    db.session.commit()
    return accepted


def _members_of(match_id):
    return (
        db.session.query(StatementMatchMember)
        .filter(StatementMatchMember.match_id == match_id)
        .all()
    )
