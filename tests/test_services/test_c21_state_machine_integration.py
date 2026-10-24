"""
Shekel Budget App -- C-21 State Machine Integration Tests

Verifies that the state-machine helper (``app.services.state_machine``)
is correctly wired into the transfer service, and that the partial
unique index ``uq_transactions_transfer_type_active`` is still declared.
The index enforced the single-active-shadow-of-each-type invariant at the
database tier; since plan step ``balance:X-bi-6-4d-2`` a transfer is one
row and ``ck_transactions_names_no_transfer`` (ruling **R-BAL258**)
refuses every row naming one, so each row the index would judge is a row
that CHECK refuses.

Audit reference: F-046 / F-047 / F-161 / commit C-21 of the
2026-04-15 security remediation plan.
"""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.category import Category
from app.models.ref import Status
from app.services import transfer_service
from app.models.amount_ownership import AmountOwnership
from tests._test_helpers import (
    a_twin_of,
    constraint_name_from,
    one_off_row_of,
    transfer_side_settle_day,
)


UNIQUE_INDEX_NAME = "uq_transactions_transfer_type_active"


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


# ── Partial unique index shape and predicate ────────────────────────


class TestPartialUniqueIndexShape:
    """The partial unique index is still present in the live schema with
    the predicate it was declared with; the module docstring says why
    nothing relies on it since plan step ``balance:X-bi-6-4d-2``."""

    def test_index_exists_in_pg_catalog(self, app):
        """``pg_indexes`` carries the index with the expected definition."""
        with app.app_context():
            row = db.session.execute(text(
                "SELECT indexname, indexdef "
                "FROM pg_indexes "
                "WHERE schemaname = 'budget' "
                "  AND indexname = :name"
            ), {"name": UNIQUE_INDEX_NAME}).fetchone()
            assert row is not None, (
                f"Partial unique index {UNIQUE_INDEX_NAME!r} not found in "
                "pg_indexes -- migration c21a1f0b8e74 may not have been "
                "applied."
            )
            indexdef = row[1]
            # Composite columns and uniqueness must both be present.
            assert "UNIQUE INDEX" in indexdef.upper()
            assert "transfer_id" in indexdef
            assert "transaction_type_id" in indexdef
            # Predicate covers exactly the active-shadow lifecycle.
            assert "transfer_id IS NOT NULL" in indexdef
            assert "is_deleted = false" in indexdef.lower()


# ── Partial unique index behaviour ──────────────────────────────────


class TestPartialUniqueIndexBehaviour:
    """What the partial unique index governed is refused before it matters.

    The index rejected a third active shadow row of the same
    transaction_type and allowed a re-insert after a soft-delete cleared its
    predicate.  Since plan step ``balance:X-bi-6-4d-2`` no row may name a
    transfer (``ck_transactions_names_no_transfer``, ruling **R-BAL258**),
    so a shadow-shaped row is refused by that CHECK whether live or hidden,
    and a row naming no transfer is outside the index as it always was.
    The hidden one is graded here (ruling R-BAL167 class 2).  The live one
    is the CHECK's own case, ``test_a_transfer_is_one_row``'s
    ``TestTheUpgrade::test_the_database_refuses_a_twin``, and this class's
    copy of it was deleted under ruling **R-BAL397**.
    """

    def test_a_hidden_twin_is_refused(self, app, db, transfer_data):
        """A soft-deleted twin is refused too, so no predicate has one to exclude.

        It soft-deleted the create door's expense shadow, which took it out of
        the partial index (the predicate excludes ``is_deleted = TRUE``), and
        then inserted a fresh active expense shadow for the same transfer,
        asserting the index ALLOWED it -- the predicate guarantee behind
        restore-after-create, mirroring the credit-payback partial-index
        lifecycle in commit C-19.  Since plan step ``balance:X-bi-6-4d-2``
        ``ck_transactions_names_no_transfer`` (ruling R-BAL258) reads no
        ``is_deleted``: the hidden twin this case staged is refused like a
        live one (ruling R-BAL167 class 2).  The fresh live twin it inserted
        after is the live one the class docstring names.
        """
        td = transfer_data
        with app.app_context():
            xfer = _create_basic_transfer(td)
            db.session.commit()

            hidden = a_twin_of(xfer, is_income=False)
            hidden.is_deleted = True
            db.session.add(hidden)
            with pytest.raises(IntegrityError) as caught:
                db.session.flush()
            db.session.rollback()
            assert constraint_name_from(caught.value) == (
                "ck_transactions_names_no_transfer"
            )

    def test_index_does_not_apply_to_non_transfer_rows(self, app, db, transfer_data):
        """Regular transactions (transfer_id IS NULL) are excluded from
        the index -- two projected expense rows in the same period and
        type must continue to coexist as they always have."""
        td = transfer_data
        with app.app_context():
            expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

            txn_a = one_off_row_of(
                td["periods"][0],
                name="Regular A",
                amount=Decimal("100.00"),
                user_id=td['periods'][0].user_id,
                account_id=td["account"].id,
                scenario_id=td["scenario"].id,
                transaction_type_id=expense_type_id,
                category_id=td["categories"]["Rent"].id,
            )
            txn_b = one_off_row_of(
                td["periods"][0],
                name="Regular B",
                amount=Decimal("200.00"),
                user_id=td['periods'][0].user_id,
                account_id=td["account"].id,
                scenario_id=td["scenario"].id,
                transaction_type_id=expense_type_id,
                category_id=td["categories"]["Rent"].id,
            )
            db.session.add_all([txn_a, txn_b])
            # No IntegrityError expected; the predicate excludes NULL
            # transfer_id rows from the index.
            db.session.commit()
            assert txn_a.id is not None
            assert txn_b.id is not None
