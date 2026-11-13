"""The reversible data edits the decorated set was narrowed away from: an account update and a
transaction- or transfer-template hard delete each go through without step-up.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType, TransactionType
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import account_service
from app.utils.session_helpers import FRESH_LOGIN_AT_KEY
from tests._test_helpers import make_every_period_rule


# ---------------------------------------------------------------------------
# Additional decorated routes (post-C-10 audit follow-up)
# ---------------------------------------------------------------------------


class TestUpdateAccountIsNotStepUpGated:
    """``POST /accounts/<id>`` (the form-edit route) is NOT step-up gated.

    Step-up was removed from this route (and from the inline anchor
    editors it shares an ``anchor_balance`` write with): an account
    edit is a reversible, version-locked change, not a credential
    operation, so the friction of a 5-minute re-auth window was judged
    not worth it for this single-owner, low-sensitivity app.  These
    tests are the regression lock for that decision -- they fail if the
    decorator is ever re-added.  The route remains owner-only via
    ``@require_owner``.
    """

    def test_stale_fresh_login_still_updates(
        self, app, auth_client, seed_user,
    ):
        """A stale fresh-login no longer blocks /accounts/<id>.

        A session whose last password entry is well past the old
        5-minute window must update the account normally, NOT bounce
        to /reauth.

        **The anchor half of this test left with the field** (plan step X-f1e,
        finding N-195): the payload used to carry an ``anchor_balance`` and the
        assertion used to check it was written, because this route accepted one.
        It no longer does -- a balance is asserted at ``accounts.true_up`` -- so
        the subject is the name edit, which is what this route still owns.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            resp = auth_client.post(
                f"/accounts/{account_id}",
                data={
                    "name": "Renamed Checking",
                    "account_type_id": checking_type.id,
                },
                follow_redirects=False,
            )

            assert resp.status_code == 302
            assert "/reauth" not in resp.headers.get("Location", "")

            # The edit went through despite the stale step-up stamp.
            acct = db.session.get(Account, account_id)
            assert acct.name == "Renamed Checking"

    def test_fresh_session_succeeds(
        self, app, auth_client, seed_user,
    ):
        """A fresh session updates the account name normally."""
        with app.app_context():
            account_id = seed_user["account"].id
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )

            resp = auth_client.post(
                f"/accounts/{account_id}",
                data={
                    "name": "Primary Checking",
                    "account_type_id": checking_type.id,
                },
                follow_redirects=False,
            )
            # Happy-path 302 to the accounts list (NOT to /reauth).
            assert resp.status_code == 302
            assert "/reauth" not in resp.headers.get("Location", "")

            acct = db.session.get(Account, account_id)
            assert acct.name == "Primary Checking"


class TestHardDeleteTemplateIsNotStepUpGated:
    """``POST /templates/<id>/hard-delete`` is NOT step-up gated.

    Unlike :func:`accounts.hard_delete_account` (which keeps its
    gate), a recurring-transaction template is a lower-stakes,
    regenerable object: deleting it only blocks future generation and
    cannot destroy settled financial history (the route refuses when
    settled rows exist).  Step-up was therefore removed.  This is the
    regression lock for that decision; the route stays owner-only via
    ``@require_owner``.
    """

    @staticmethod
    def _create_minimal_template(seed_user):
        """Create a template with no transactions for hard-delete tests."""
        # Authored through the write door (plan step R7c-b): the two-axis
        # columns are NOT NULL, so a rule naming only a pattern cannot be
        # stored.
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Rent"].id,
            transaction_type_id=db.session.query(TransactionType)
            .filter_by(name="Expense").one().id,
            name="Step-Up Test Template",
        )
        db.session.add(template)
        db.session.commit()
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_every_period_rule(db.session, template)
        return template

    def test_stale_fresh_login_still_deletes(
        self, app, auth_client, seed_user,
    ):
        """A stale fresh-login no longer blocks the template hard-delete.

        With step-up removed, a session whose last password entry is
        past the old 5-minute window deletes the template normally
        (302 to the list, NOT /reauth), and the row is gone.
        """
        with app.app_context():
            template = self._create_minimal_template(seed_user)
            template_id = template.id

            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            resp = auth_client.post(
                f"/templates/{template_id}/hard-delete",
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert "/reauth" not in resp.headers.get("Location", "")

            # The delete went through despite the stale step-up stamp.
            assert db.session.get(TransactionTemplate, template_id) is None

    def test_fresh_session_succeeds(
        self, app, auth_client, seed_user,
    ):
        """A fresh session permanently deletes the template normally."""
        with app.app_context():
            template = self._create_minimal_template(seed_user)
            template_id = template.id

            resp = auth_client.post(
                f"/templates/{template_id}/hard-delete",
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert "/reauth" not in resp.headers.get("Location", "")
            assert db.session.get(TransactionTemplate, template_id) is None


class TestHardDeleteTransferTemplateIsNotStepUpGated:
    """``POST /transfers/<id>/hard-delete`` is NOT step-up gated.

    Mirrors :func:`templates.hard_delete_template`: a transfer
    template is a lower-stakes, regenerable object and the route
    refuses when settled history exists, so step-up was removed.  The
    shadow invariants documented in CLAUDE.md are still enforced by
    the transfer service on every delete -- they do not depend on the
    step-up gate.  This is the regression lock for the removal; the
    route stays owner-only via ``@require_owner``.
    """

    @staticmethod
    def _create_minimal_transfer_template(seed_user):
        """Create a transfer template with no transfers for hard-delete tests."""
        savings_type = (
            db.session.query(AccountType).filter_by(name="Savings").one()
        )
        savings = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Step-Up Test Savings",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(savings)
        db.session.flush()

        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name="Step-Up Test Transfer",
        )
        db.session.add(template)
        db.session.commit()
        return template

    def test_stale_fresh_login_still_deletes(
        self, app, auth_client, seed_user,
    ):
        """A stale fresh-login no longer blocks the transfer-template delete.

        With step-up removed, a session whose last password entry is
        past the old 5-minute window deletes the transfer template
        normally (302 to the list, NOT /reauth), and the row is gone.
        """
        with app.app_context():
            template = self._create_minimal_transfer_template(seed_user)
            template_id = template.id

            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert "/reauth" not in resp.headers.get("Location", "")

            # The delete went through despite the stale step-up stamp.
            assert db.session.get(TransferTemplate, template_id) is None

    def test_fresh_session_succeeds(
        self, app, auth_client, seed_user,
    ):
        """A fresh session permanently deletes the transfer template normally."""
        with app.app_context():
            template = self._create_minimal_transfer_template(seed_user)
            template_id = template.id

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert "/reauth" not in resp.headers.get("Location", "")
            assert db.session.get(TransferTemplate, template_id) is None
