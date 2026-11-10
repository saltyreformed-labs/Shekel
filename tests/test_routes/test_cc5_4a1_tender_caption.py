"""Plan step ``credit_card:CC-5-4a-1``: the "Paid from" picker says what a pick withdraws.

Ruling **R-CC46** (developer 2026-09-21): a payment re-pointed onto another
account leaves the acts naming it, withdrawn and DISCLOSED as a delete's are.
The delete discloses through its confirm dialog before the press; the "Paid
from" picker is a ``<select>`` inside the popover's Save form, so it discloses
beside the control, before the pick, in a caption the route derives through
the withdrawal's own read twin (``match_withdrawal.pending_for_movements``,
the same derivation the seam's write uses at ``_re_point``).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app.enums import SettledDayBasisEnum
from app.extensions import db
from app.services.settle_day import SettleDay
from app.services.statement_match import accept_match
from app.services.transaction_service import settle_transaction
from tests._test_helpers import create_account_of_type
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a bank line, a scope and an accepted act as the
# app does; the convention ``test_statement_reconcile`` keeps.
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_submission,
    a_transaction,
    an_import,
)


def _savings(seed_user):
    """A Savings account, so the picker has a choice to offer (ruling R-CC34).

    A card until plan task plan#3232: "Paid from" lists no card since ruling
    plan#3227, so a card is no choice on a bill.
    """
    return create_account_of_type(
        seed_user, db.session, "Savings", "Rainy Day",
        anchor_balance=Decimal("500.00"),
        observed_on=seed_user["bootstrap_period"].start_date,
    )


def _popover(auth_client, txn_id):
    response = auth_client.get(f"/transactions/{txn_id}/full-edit")
    assert response.status_code == 200
    return response.get_data(as_text=True)


class TestThePickerSaysWhatADifferentPickWithdraws:
    """The disclosure beside the control, from the door's own derivation."""

    def test_a_matched_payment_names_its_line_under_the_picker(
        self, app, auth_client, seed_user,
    ):
        with app.app_context():
            _savings(seed_user)
            bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=1)
            txn = a_transaction(seed_user, name="Hotel", amount="120.00")
            db.session.commit()
            settle_transaction(
                txn, settle_day=SettleDay(
                    day=bank_day, basis=SettledDayBasisEnum.ENTERED,
                ),
            )
            db.session.commit()
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-120.00",
                posted_on=bank_day, description="GROCERIES",
            )
            db.session.commit()
            scope = a_scope(seed_user)
            accept_match(
                a_submission(scope, lines=[line], transactions=[txn]), scope,
            )
            db.session.commit()

            html = _popover(auth_client, txn.id)

            assert f'id="tender-{txn.id}"' in html, "the picker is drawn"
            assert f'id="tender-withdraws-{txn.id}"' in html
            assert "Picking another account withdraws 1 accepted match" in html
            assert "1 bank line is unexplained again" in html
            assert f"{bank_day.month}/{bank_day.day} GROCERIES" in html
            assert "-$120.00" in html

    def test_an_unmatched_payment_renders_no_caption(
        self, app, auth_client, seed_user,
    ):
        with app.app_context():
            _savings(seed_user)
            txn = a_transaction(seed_user, name="Hotel", amount="120.00")
            db.session.commit()
            settle_transaction(txn)
            db.session.commit()

            html = _popover(auth_client, txn.id)

            assert f'id="tender-{txn.id}"' in html, "the picker is drawn"
            assert "tender-withdraws-" not in html
            assert "Picking another account withdraws" not in html

    def test_a_projected_row_with_no_payment_renders_no_caption(
        self, app, auth_client, seed_user,
    ):
        with app.app_context():
            _savings(seed_user)
            txn = a_transaction(seed_user, name="Hotel", amount="120.00")
            db.session.commit()

            html = _popover(auth_client, txn.id)

            assert f'id="tender-{txn.id}"' in html, "the picker is drawn"
            assert "tender-withdraws-" not in html
