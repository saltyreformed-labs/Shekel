"""Transfer route tests: negative paths (missing ids, IDOR, repeated acts, validation) and the
owner proof of every write door.
"""

from app.extensions import db
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
from app.models.ref import Status
from app.services import transfer_service
from tests._test_helpers import pay_periods_hydrated

from ._helpers import (
    _create_savings_account,
    _create_template,
    _create_transfer,
    _create_other_user_with_template,
    _create_second_user_transfer,
)


# ── Negative Paths ────────────────────────────────────────────────


class TestTransferNegativePaths:
    """Negative-path tests: nonexistent IDs, IDOR, idempotent ops, validation."""

    def test_update_nonexistent_transfer_instance(self, app, auth_client, seed_user):
        """PATCH /transfers/instance/999999 for a nonexistent transfer returns 404."""
        with app.app_context():
            resp = auth_client.patch(
                "/transfers/instance/999999",
                # A well-formed payload, so the 404 is about the missing row
                # rather than the schema refusing an unaccompanied figure.
                data={"amount": "100.00", "amount_as_rendered": "100.00"},
            )

            assert resp.status_code == 404

    def test_mark_done_already_done_transfer(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/instance/<id>/mark-done on an already-done transfer is idempotent."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            # Set to done first, through the service door so each side
            # records what moved (plan step X-au-c3), on the transfer since
            # plan step balance:X-bi-6-4d-2: a bare status assign on the
            # transfer writes neither side's record, so the repeat below
            # would start from a settle that recorded nothing.
            done_status = db.session.query(Status).filter_by(name="Paid").one()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, status_id=done_status.id,
            )
            db.session.commit()

            # Mark done again.
            resp = auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")

            # Route does not guard against double mark-done; it sets
            # the same status again. This is idempotent behavior.
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "balanceChanged"

            db.session.refresh(xfer)
            assert xfer.status.name == "Paid"

    def test_cancel_already_cancelled_transfer(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/instance/<id>/cancel on an already-cancelled transfer is idempotent."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            # Cancel first.
            cancelled_status = db.session.query(Status).filter_by(name="Cancelled").one()
            xfer.status_id = cancelled_status.id
            db.session.commit()

            # Cancel again.
            resp = auth_client.post(f"/transfers/instance/{xfer.id}/cancel")

            # Route does not guard against double cancel; it sets
            # the same status again. This is idempotent behavior.
            assert resp.status_code == 200

            db.session.refresh(xfer)
            assert xfer.status.name == "Cancelled"

    def test_quick_edit_other_users_transfer_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """GET /transfers/quick-edit/<id> for another user's transfer returns 404."""
        with app.app_context():
            other_xfer = _create_second_user_transfer(second_user)

            resp = auth_client.get(f"/transfers/quick-edit/{other_xfer.id}")

            assert resp.status_code == 404
            # No transfer data should leak.
            assert b"Other Transfer" not in resp.data
            assert b"100.00" not in resp.data

    def test_full_edit_other_users_transfer_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """GET /transfers/<id>/full-edit for another user's transfer returns 404."""
        with app.app_context():
            other_xfer = _create_second_user_transfer(second_user)

            resp = auth_client.get(f"/transfers/{other_xfer.id}/full-edit")

            assert resp.status_code == 404

    def test_mark_done_other_users_transfer_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /transfers/instance/<id>/mark-done for another user's transfer returns 404."""
        with app.app_context():
            other_xfer = _create_second_user_transfer(second_user)
            original_status_id = other_xfer.status_id

            resp = auth_client.post(
                f"/transfers/instance/{other_xfer.id}/mark-done"
            )

            assert resp.status_code == 404

            # Verify DB state unchanged.
            db.session.expire_all()
            refreshed = db.session.get(Transfer, other_xfer.id)
            assert refreshed.status_id == original_status_id

    def test_cancel_other_users_transfer_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /transfers/instance/<id>/cancel for another user's transfer returns 404."""
        with app.app_context():
            other_xfer = _create_second_user_transfer(second_user)
            original_status_id = other_xfer.status_id

            resp = auth_client.post(
                f"/transfers/instance/{other_xfer.id}/cancel"
            )

            assert resp.status_code == 404

            # Verify DB state unchanged.
            db.session.expire_all()
            refreshed = db.session.get(Transfer, other_xfer.id)
            assert refreshed.status_id == original_status_id

    def test_create_template_with_missing_accounts(self, app, auth_client, seed_user):
        """POST /transfers with empty from/to accounts fails schema validation."""
        with app.app_context():
            resp = auth_client.post("/transfers", data={
                "name": "Bad Transfer",
                "default_amount": "100.00",
                "from_account_id": "",
                "to_account_id": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            # Verify no template was created.
            count = db.session.query(TransferTemplate).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0

    def test_create_ad_hoc_with_zero_amount(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/ad-hoc with amount=0.00 fails validation (must be > 0)."""
        with app.app_context():
            savings = _create_savings_account(seed_user)

            resp = auth_client.post("/transfers/ad-hoc", data={
                "pay_period_id": seed_periods_today[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "0.00",
                "scenario_id": seed_user["scenario"].id,
                "category_id": str(seed_user["categories"]["Rent"].id),
            })

            # TransferCreateSchema requires amount > 0 (min_inclusive=False).
            assert resp.status_code == 422
            body = resp.get_json()
            assert "errors" in body

            # Verify no transfer was created.
            count = db.session.query(Transfer).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0

    def test_create_ad_hoc_with_negative_amount(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/ad-hoc with negative amount fails schema validation."""
        with app.app_context():
            savings = _create_savings_account(seed_user)

            resp = auth_client.post("/transfers/ad-hoc", data={
                "pay_period_id": seed_periods_today[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "-100.00",
                "scenario_id": seed_user["scenario"].id,
                "category_id": str(seed_user["categories"]["Rent"].id),
            })

            assert resp.status_code == 422
            body = resp.get_json()
            assert "errors" in body

            # Verify no transfer was created.
            count = db.session.query(Transfer).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0


class TestTransferDoorsResolveOwnershipStructurally:
    """The transfer write doors prove a paycheck's owner without loading it.

    Plan step **pay_calendar:C13-b**, the transfer half of ledger row
    **P75**'s EIGHT primary-key refetches.  Five of them lived here: two
    ``_user_owns(PayPeriod, ...)`` calls in ``transfers/mutations``, one in
    ``transfers/templates``, a bare ``db.session.get`` + compare in
    ``transfers/_instances``, and the service tier's own
    ``transfer_service._ownership._get_owned_period``.  Four were duplicates
    of the fifth -- every create and update path reaches the service one
    unconditionally -- and the developer ruled them collapsed into it
    (2026-09-03), with the service call answering the owner's derived CALENDAR
    instead of comparing a fetched row.  ``transfers/templates`` keeps ONE
    resolution because it needs the period's ``start_date`` as a ``due_date``,
    and it threads that value down rather than letting the materializer
    re-fetch it.

    **Why a hydration count.**  The 404s themselves are graded above and pass
    either way; what tells a calendar lookup apart from a primary-key compare
    is that the calendar loads COLUMN TUPLES and never an entity.  An empty
    hydration list cannot hold while the old guard exists, so this fires.

    ``expunge_all`` FIRST, for the reason
    :func:`tests._test_helpers.pay_periods_hydrated` gives: a ``session.get``
    served from the identity map fires no load event, so without it the probe
    reads zero on the very code it exists to refuse.
    """

    @staticmethod
    def _foreign_period(other):
        """Return one pay period belonging to *other*."""
        from app.models.pay_period import PayPeriod
        return (
            db.session.query(PayPeriod)
            .filter_by(user_id=other["user"].id)
            .first()
        )

    def test_ad_hoc_create_hydrates_no_pay_period_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /transfers/ad-hoc refuses a foreign period, loading none."""
        with app.app_context():
            other = _create_other_user_with_template()
            savings = _create_savings_account(seed_user)
            payload = {
                "pay_period_id": self._foreign_period(other).id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "50.00",
                "scenario_id": seed_user["scenario"].id,
                "category_id": str(seed_user["categories"]["Rent"].id),
            }
            db.session.expunge_all()
            with pay_periods_hydrated() as hydrated:
                resp = auth_client.post("/transfers/ad-hoc", data=payload)
            assert resp.status_code == 404
            assert hydrated == [], (
                f"hydrated {len(hydrated)} PayPeriod row(s); the door resolves "
                f"the submitted id against the owner's derived calendar"
            )

    def test_instance_update_hydrates_no_pay_period_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """PATCH /transfers/instance/<id> refuses a foreign period, loading none.

        The transfer is the REQUESTER'S own, so the refusal can only come from
        the submitted ``pay_period_id``.
        """
        with app.app_context():
            other = _create_other_user_with_template()
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(
                seed_user, seed_periods_today, savings, template=template,
            )
            xfer_id = xfer.id
            foreign_period_id = self._foreign_period(other).id
            version = xfer.version_id

            db.session.expunge_all()
            with pay_periods_hydrated() as hydrated:
                resp = auth_client.patch(
                    f"/transfers/instance/{xfer_id}",
                    data={
                        "pay_period_id": str(foreign_period_id),
                        "version_id": str(version),
                    },
                )
            assert resp.status_code == 404
            assert hydrated == [], (
                f"hydrated {len(hydrated)} PayPeriod row(s); the door resolves "
                f"the submitted id against the owner's derived calendar"
            )

    def test_template_create_hydrates_no_pay_period_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /transfers refuses a foreign start period, loading none.

        The refusal itself -- and that it fires for a RECURRING cadence, where
        the id is never used -- is
        :meth:`TestOneTimeTransfer.test_recurring_transfer_idor_period`'s; this
        asserts the ONE structural property that says the check is the calendar
        rather than a fetched row.
        """
        with app.app_context():
            other = _create_other_user_with_template()
            savings = _create_savings_account(seed_user)
            payload = {
                "name": "IDOR Attempt",
                "default_amount": "100.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                "recurrence_unit": "",
                "start_period_id": str(self._foreign_period(other).id),
            }
            db.session.expunge_all()
            with pay_periods_hydrated() as hydrated:
                resp = auth_client.post(
                    "/transfers", data=payload, follow_redirects=True,
                )
            assert resp.status_code == 200
            assert b"Invalid start period" in resp.data
            assert hydrated == [], (
                f"hydrated {len(hydrated)} PayPeriod row(s); the door resolves "
                f"the submitted id against the owner's derived calendar"
            )
