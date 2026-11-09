"""
Shekel Budget App -- test helper: a payment matched to its bank line, then set back to Projected.

A row Paid on checking and matched to its bank line, then set back to
Projected, keeps its payment un-dated and the match naming it (ruling
R-BAL61).  A Credit press then puts that payment on the card, which takes it
out of the match (ruling R-CC46), so the press must name the line first
(ruling R-CC127), the popover names it beside Credit (plan task plan#3056),
and the C key and the palette open the popover there rather than press (ruling
plan#3112, plan task plan#3114).  The one spelling of that state for the three
suites that grade it.
"""

from __future__ import annotations

from datetime import timedelta

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.services import transaction_service
from app.services.settle_day import SettleDay
from app.services.statement_match import accept_match, matched_subjects
from tests._cards import add_card

# Pylint: ``shekel-private-module-import`` -- the one way a test stages a bank
# line, a scope and an accepted act as the app does (``test_statement_reconcile``'s).
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_submission,
    a_transaction,
    an_import,
)


def matched_and_reverted(seed_user, *, card=True):
    """$120 Hotel Paid on checking, matched to its HOTEL -$120.00 line, set back to Projected.

    Paid the day after the seeded paycheck opens, the bank line posted that
    day, the match accepted on checking's screen, then Status set back to
    Projected through the popover's door: the row keeps its $120.00 payment
    un-dated on checking, and the match still names it.  Committed.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        card: Whether the owner has a card (a Visa, added first).

    Returns:
        The row, its bank line, and the Visa (``None`` without *card*).
    """
    visa = add_card(seed_user) if card else None
    bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=1)
    txn = a_transaction(seed_user, name="Hotel", amount="120.00")
    db.session.commit()
    transaction_service.settle_transaction(
        txn, settle_day=SettleDay(day=bank_day, basis=SettledDayBasisEnum.ENTERED),
    )
    db.session.commit()
    line = a_bank_line(
        seed_user, an_import(seed_user), amount="-120.00",
        posted_on=bank_day, description="HOTEL",
    )
    db.session.commit()
    scope = a_scope(seed_user)
    accept_match(a_submission(scope, lines=[line], transactions=[txn]), scope)
    db.session.commit()
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()
    assert matched_subjects(seed_user["account"].id).lines == {line.id}, (
        "precondition: matched on checking"
    )
    return txn, line, visa
