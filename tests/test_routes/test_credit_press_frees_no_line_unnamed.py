"""
Shekel Budget App -- a Credit press that names no bank line frees none (plan task plan#2929)

A row Paid on checking and matched to a bank line, then set back to Projected,
keeps its payment un-dated and the match naming it (ruling R-BAL61).  A Credit
press puts that kept payment on the card, which takes it out of the match, so
the press must name the line first (ruling R-CC127); only the grid's one-click
Mark Paid withdraws silently (R-CC56).  The press named none, so it is refused
and nothing moves.  The popover's caption that names the line is plan#3056's.
"""

from __future__ import annotations

from datetime import timedelta

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
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


def test_a_credit_press_on_a_matched_reverted_row_is_refused(app, auth_client, seed_user):
    """$120 Paid on checking, matched, reverted: Credit is refused and the match stays.

    Measured by the review of plan#2929 before the fix: 200, the payment moved
    to the card, and the line was unexplained again with no word on the page.
    """
    with app.app_context():
        add_card(seed_user)
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
        checking_id = seed_user["account"].id
        assert matched_subjects(checking_id).lines == {line.id}, "precondition: matched"

        response = auth_client.post(f"/transactions/{txn.id}/mark-credit")

        assert response.status_code == 400
        assert "would leave a bank line unexplained that this page did not mention" in (
            response.get_data(as_text=True)
        )
        db.session.expire_all()
        row = db.session.get(Transaction, txn.id)
        assert row.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
        assert [m.account_id for m in row.entries] == [checking_id]
        assert matched_subjects(checking_id).lines == {line.id}
