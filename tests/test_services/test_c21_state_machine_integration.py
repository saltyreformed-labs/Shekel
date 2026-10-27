"""
Shekel Budget App -- C-21 State Machine Integration Tests

Verifies that the state-machine helper (``app.services.state_machine``)
is correctly wired into the transfer service.  It also graded the partial
unique index ``uq_transactions_transfer_type_active``, which enforced the
single-active-shadow-of-each-type invariant at the database tier, until
plan step ``balance:X-bi-6-5`` dropped the index with the column it read
(plan step ``balance:X-bi-6-4d-2`` having made a transfer one row): its
three cases went with it (ruling plan#1850).

Audit reference: F-046 / F-047 / F-161 / commit C-21 of the
2026-04-15 security remediation plan.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.category import Category
from app.models.ref import Status
from app.services import transfer_service
from app.models.amount_ownership import AmountOwnership
from tests._test_helpers import transfer_side_settle_day


# ── Fixture ─────────────────────────────────────────────────────────


@pytest.fixture()
def transfer_data(seed_full_user_data):
    """Provide everything the transfer service needs for these tests.

    Adds the default Transfers: Incoming and Transfers: Outgoing
    categories that the conftest seed_full_user_data fixture does not
    include.  Mirrors the fixture in ``test_transfer_service/conftest.py`` so
    these tests can use the same ``_create_basic_transfer`` helper.
    """
    data = seed_full_user_data
    user = data["user"]

    projected = db.session.query(Status).filter_by(name="Projected").one()

    incoming_cat = Category(
        user_id=user.id, group_name="Transfers", item_name="Incoming",
        sort_order=90,
    )
    outgoing_cat = Category(
        user_id=user.id, group_name="Transfers", item_name="Outgoing",
        sort_order=91,
    )
    db.session.add_all([incoming_cat, outgoing_cat])
    db.session.commit()

    return {
        **data,
        "projected_status": projected,
        "incoming_cat": incoming_cat,
        "outgoing_cat": outgoing_cat,
    }


def _create_basic_transfer(td):
    """Helper: create a transfer using the test data dict."""
    return transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=td["user"].id,
            from_account_id=td["account"].id,
            to_account_id=td["savings_account"].id,
            pay_period_id=td["periods"][0].id,
            scenario_id=td["scenario"].id,
            amount_ownership=AmountOwnership.own(Decimal("250.00")),
            status_id=td["projected_status"].id,
            category_id=td["categories"]["Rent"].id,
        ),
    )


# ── Transfer service: legal transitions reach both sides ────────────


class TestTransferServiceLegalTransitions:
    """update_transfer accepts every legal transition listed in
    state_machine.  The transfer's status is the one status both sides
    show, and a settle dates each side's payment record."""

    def test_projected_to_done_propagates(self, app, db, transfer_data):
        """projected -> done is legal and reaches both sides.

        The transfer's status is the one status, and each side's payment
        record is dated by the move, where each shadow said Paid until plan
        step ``balance:X-bi-6-4d-2`` (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        td = transfer_data
        with app.app_context():
            xfer = _create_basic_transfer(td)
            db.session.commit()
            done_id = ref_cache.status_id(StatusEnum.DONE)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done_id,
            )
            db.session.commit()

            db.session.refresh(xfer)
            assert xfer.status_id == done_id
            for account_id in (xfer.from_account_id, xfer.to_account_id):
                assert transfer_side_settle_day(
                    db.session, xfer.id, account_id,
                ) is not None

    def test_done_to_projected_revert_propagates(self, app, db, transfer_data):
        """done -> projected (revert) is legal."""
        td = transfer_data
        with app.app_context():
            xfer = _create_basic_transfer(td)
            db.session.commit()
            done_id = ref_cache.status_id(StatusEnum.DONE)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done_id,
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=projected_id,
            )
            db.session.commit()

            db.session.refresh(xfer)
            assert xfer.status_id == projected_id

    def test_identity_transition_succeeds(self, app, db, transfer_data):
        """projected -> projected on a still-projected transfer is a no-op."""
        td = transfer_data
        with app.app_context():
            xfer = _create_basic_transfer(td)
            db.session.commit()
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=projected_id,
            )
            db.session.commit()

            db.session.refresh(xfer)
            assert xfer.status_id == projected_id


# ── Transfer service: illegal transitions are rejected ──────────────


class TestTransferServiceIllegalTransitions:
    """update_transfer rejects illegal transitions by raising
    ValidationError.  No partial mutation occurs -- the transfer's
    status_id remains at its pre-call value.

    **Two cases left this class at plan step balance:X-am**, which deleted the
    terminal ``Settled`` ARCHIVE: ``projected -> settled`` (Projected now
    reaches every status the enum has) and ``settled -> projected`` (that
    revert is now LEGAL from every state in both maps, which is the step's own
    content -- see ``state_machine``'s ``TestNeitherMapHasAnAbsorbingState``).
    The two below are the transfer map's remaining refusals and they are the
    two that matter: one is shared with the transaction map, one is the
    asymmetry the transfer map exists for.
    """

    def test_projected_to_credit_rejected(self, app, db, transfer_data):
        """A transfer may never be Credit, whatever its current status.

        The refusal the transfer map exists for: the credit / auto-payback
        workflow is expense-only, so a Credit transfer would be
        balance-excluded on BOTH accounts with no compensating payback and
        would vanish from both projections.  Graded at the SERVICE tier here
        because a transfer's status is both legs' status -- each side shows
        it -- so an accepted move would be wrong on both accounts at once.

        It also read the two shadow rows back as Projected until plan step
        ``balance:X-bi-6-4d-2`` (ruling R-BAL167 class 1): a transfer is one
        row, so the transfer's own status read below is the whole of it.
        """
        td = transfer_data
        with app.app_context():
            xfer = _create_basic_transfer(td)
            db.session.commit()
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

            with pytest.raises(ValidationError) as excinfo:
                transfer_service.update_transfer(
                    xfer.id, td["user"].id,
                    status_id=ref_cache.status_id(StatusEnum.CREDIT),
                )
            assert "transfer" in str(excinfo.value)

            db.session.rollback()
            db.session.refresh(xfer)
            assert xfer.status_id == projected_id

    def test_done_to_cancelled_rejected(self, app, db, transfer_data):
        """done -> cancelled would erase the Paid audit trail."""
        td = transfer_data
        with app.app_context():
            xfer = _create_basic_transfer(td)
            db.session.commit()
            done_id = ref_cache.status_id(StatusEnum.DONE)
            cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)

            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done_id,
            )
            db.session.commit()

            with pytest.raises(ValidationError):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, status_id=cancelled_id,
                )

            db.session.rollback()
            db.session.refresh(xfer)
            assert xfer.status_id == done_id
