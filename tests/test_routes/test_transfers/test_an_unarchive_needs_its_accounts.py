"""
The transfer unarchive door refuses while an account the transfer names is archived.

Task plan#3697: the unarchive became one act an account's unarchive calls
too (``app/routes/_transfer_lifecycle.unarchive_transfer_definition``), and it
asks first whether both of the transfer's accounts are active.  Measured on
this branch before the refusal (2026-11-13, a scratch probe through this
door): a hidden row to bring back answered a 500
(``transfer_service.restore_transfer``'s archived-account refusal, finding
F-164, uncaught), and with no hidden row the maintain pass wrote 6 live rows
onto the archived savings account, the state an account's own archive refuses
to leave behind.
"""

from app.extensions import db
from app.models.account import Account
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import transfer_service

from ._helpers import _create_savings_account, _create_template, _create_transfer


def _flashes(auth_client):
    """Return the unconsumed flash messages left by an unfollowed redirect."""
    with auth_client.session_transaction() as sess:
        return [message for _category, message in sess.get("_flashes", [])]


def _live_rows(template_id):
    """Return how many of the template's transfers are not soft-deleted."""
    return db.session.query(Transfer).filter_by(
        transfer_template_id=template_id, is_deleted=False,
    ).count()


def _archive(*rows):
    """Set each row's ``is_active`` False and commit."""
    for row in rows:
        row.is_active = False
    db.session.commit()


class TestAnUnarchiveNeedsItsAccounts:
    """POST /transfers/<id>/unarchive while one of its accounts is archived."""

    def test_a_hidden_row_stays_hidden_and_the_door_says_why(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Savings archived, one row the archive hid: refused, nothing restored.

        The sentence names the transfer, its two accounts and the archived
        one.  NEGATIVE CONTROL: delete the ``_archived_endpoint_refusal``
        call and this request is a 500 (measured before the refusal).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)
            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=True,
            )
            _archive(template, savings)
            template_id, xfer_id = template.id, xfer.id

            resp = auth_client.post(f"/transfers/{template_id}/unarchive")

            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/transfers")
            assert _flashes(auth_client) == [
                "Nothing was unarchived: 'Monthly Savings' moves money from "
                "'Checking' to 'Savings', and 'Savings' is archived. "
                "Unarchive 'Savings' first."
            ]
            db.session.expire_all()
            assert db.session.get(TransferTemplate, template_id).is_active is False
            assert db.session.get(Transfer, xfer_id).is_deleted is True
            assert db.session.get(Account, savings.id).is_active is False

    def test_no_row_is_written_onto_the_archived_account(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Savings archived, no hidden row: refused, and no row generated.

        NEGATIVE CONTROL: without the refusal the maintain pass wrote 6 live
        rows into the archived savings account (measured before the refusal).
        """
        assert seed_periods_today
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            _archive(template, savings)
            template_id = template.id
            assert _live_rows(template_id) == 0

            resp = auth_client.post(f"/transfers/{template_id}/unarchive")

            assert resp.status_code == 302
            assert _flashes(auth_client) == [
                "Nothing was unarchived: 'Monthly Savings' moves money from "
                "'Checking' to 'Savings', and 'Savings' is archived. "
                "Unarchive 'Savings' first."
            ]
            db.session.expire_all()
            assert db.session.get(TransferTemplate, template_id).is_active is False
            assert _live_rows(template_id) == 0

    def test_both_accounts_archived_are_both_named(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Both accounts archived: the sentence names both, in its plural."""
        assert seed_periods_today
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            checking = db.session.get(Account, seed_user["account"].id)
            _archive(template, savings, checking)

            resp = auth_client.post(f"/transfers/{template.id}/unarchive")

            assert resp.status_code == 302
            assert _flashes(auth_client) == [
                "Nothing was unarchived: 'Monthly Savings' moves money from "
                "'Checking' to 'Savings', and 'Checking' and 'Savings' are "
                "archived. Unarchive 'Checking' and 'Savings' first."
            ]

    def test_both_accounts_active_unarchives_as_before(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The control: both accounts active, the hidden row comes back."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)
            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=True,
            )
            _archive(template)
            template_id, xfer_id = template.id, xfer.id

            resp = auth_client.post(f"/transfers/{template_id}/unarchive")

            assert resp.status_code == 302
            assert _flashes(auth_client)[0].startswith(
                "Recurring transfer 'Monthly Savings' unarchived. ",
            )
            db.session.expire_all()
            assert db.session.get(TransferTemplate, template_id).is_active is True
            assert db.session.get(Transfer, xfer_id).is_deleted is False
