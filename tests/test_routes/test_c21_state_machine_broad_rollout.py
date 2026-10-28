"""
Shekel Budget App -- C-21 Follow-up: Broad State Machine Rollout

Verifies that ``verify_transition`` is wired into every state-changing
endpoint that previously bypassed it: mark_done, cancel_transaction,
and unmark_credit.  Cancelled and Credit rows can no longer slip
into Paid/Received via these endpoints; identity transitions still
succeed so HTMX double-clicks remain idempotent.

Audit reference: F-046 / F-047 / F-161 -- broad rollout following the
2026-04-15 commit C-21.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import account_service
from tests._test_helpers import (
    one_off_row_of,
    open_books_before_the_first_assertion,
    settlement_if_settling,
    transfer_side_settle_day,
)
from app.models.amount_ownership import AmountOwnership


# ── Helpers ─────────────────────────────────────────────────────────


def _create_projected_expense(seed_user, seed_periods_today, period_index=0):
    """Insert a projected expense in the requested period."""
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    txn = one_off_row_of(
        seed_periods_today[period_index],
        name="Test Expense",
        amount=Decimal("100.00"),
        user_id=seed_periods_today[period_index].user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=expense_type.id,
        category_id=seed_user["categories"]["Groceries"].id,
        due_date=seed_periods_today[period_index].start_date,
    )
    db.session.commit()
    return txn


def _walk_to(txn, status_name):
    """Drive a freshly-projected row to *status_name* through the SEAM.

    Bypasses the ROUTE layer so the test bodies stay short -- the route-layer
    transitions used here are themselves covered by the legal-transition tests
    in ``test_c21_state_machine_routes.py`` and the grid suite -- but not the
    service layer beneath it.

    **It used to assign ``status_id`` directly, and that made every row it
    built illegal** (plan step X-au-c3).  ``status_seam.apply_status_change`` is
    the ONE door that writes a status, and since that step it writes what the
    row RECORDS as having moved in the same call; a raw column write produced a
    settled row that recorded nothing, which no door can create and which
    ``row_valuation.settled_figure`` now refuses outright rather than valuing at
    the row's PLAN.  Seven fixtures across the suite were in that state and the
    refusal is what found them.  A test that grades a refusal against an
    impossible row grades nothing.
    """
    # pylint: disable=import-outside-toplevel  -- test-module local import.
    from app.services import status_seam

    target = db.session.query(Status).filter_by(name=status_name).one()
    status_seam.apply_status_change(
        txn, target.id,
        settlement=settlement_if_settling(txn, target.id),
    )
    db.session.commit()


# ══════════════════════════════════════════════════════════════════════
# /transactions/<id>/mark-done -- direct (non-envelope, non-transfer)
# ══════════════════════════════════════════════════════════════════════


class TestMarkDoneDirectStateMachine:
    """The grid's mark_done endpoint now refuses transitions that the
    state machine forbids.  Previously the direct branch wrote
    ``status_id`` unconditionally, so a Cancelled row could be silently
    re-marked Paid.  (It said "a Settled or Cancelled row" until plan step
    **balance:X-am** deleted the archive.)"""

    def test_cancelled_to_paid_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A Cancelled row cannot jump straight to Paid."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to(txn, "Cancelled")

            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 400
            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

    def test_credit_to_paid_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A Credit row cannot jump straight to Paid."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to(txn, "Credit")

            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 400
            db.session.refresh(txn)
            assert txn.status.name == "Credit"

    def test_projected_to_paid_accepted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The legal projected -> paid path still returns 200."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)

            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.status.name == "Paid"

    def test_paid_to_paid_identity_accepted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Re-marking a Paid row is idempotent -- HTMX double-clicks
        and dashboard re-fires must not produce 400s."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to(txn, "Paid")

            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.status.name == "Paid"


# ══════════════════════════════════════════════════════════════════════
# /transactions/<id>/cancel
# ══════════════════════════════════════════════════════════════════════


class TestCancelTransactionStateMachine:
    """Cancel is reachable only from Projected (or the Cancelled
    identity edge).  Done / Received rows must be reverted
    to Projected first so the audit log records both legs."""

    def test_paid_to_cancelled_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Cancelling a Paid row is now refused."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to(txn, "Paid")

            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 400
            db.session.refresh(txn)
            assert txn.status.name == "Paid"

    def test_credit_to_cancelled_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Credit can only revert to Projected, not Cancelled."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to(txn, "Credit")

            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 400
            db.session.refresh(txn)
            assert txn.status.name == "Credit"

    def test_projected_to_cancelled_accepted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The legal projected -> cancelled path still returns 200."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)

            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

    def test_cancelled_to_cancelled_identity_accepted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Re-cancelling a Cancelled row is idempotent."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to(txn, "Cancelled")

            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"


# ══════════════════════════════════════════════════════════════════════
# /transactions/<id>/unmark-credit
# ══════════════════════════════════════════════════════════════════════


class TestUnmarkCreditStateMachine:
    """``unmark_credit`` previously rewrote ``status_id`` to Projected
    on any caller-supplied row -- including a Paid row that had no
    payback to clean up.  The follow-up adds (a) a bespoke
    "must be in Credit status" guard and (b) ``verify_transition`` as
    a defense-in-depth layer."""

    def test_paid_row_rejected_with_friendly_message(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Calling unmark-credit on a Paid row is refused with a
        message that names the offending status."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to(txn, "Paid")

            resp = auth_client.delete(f"/transactions/{txn.id}/unmark-credit")
            assert resp.status_code == 400
            body = resp.data.decode()
            assert "Paid" in body
            assert "Only Credit" in body
            db.session.refresh(txn)
            # Row stays Paid -- the bespoke guard fires before the
            # service writes anything.
            assert txn.status.name == "Paid"

    def test_projected_row_rejected_with_friendly_message(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A Projected row also fails the bespoke guard -- there is
        no Credit state to revert from."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)

            resp = auth_client.delete(f"/transactions/{txn.id}/unmark-credit")
            assert resp.status_code == 400
            body = resp.data.decode()
            assert "Projected" in body
            db.session.refresh(txn)
            assert txn.status.name == "Projected"

    def test_credit_row_accepted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The legal Credit -> Projected path still returns 200 and
        deletes the auto-generated payback."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            # Use the real mark-credit workflow to seed a payback row;
            # _walk_to writes status only and would not produce one.
            mark_resp = auth_client.post(f"/transactions/{txn.id}/mark-credit")
            assert mark_resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Credit"
            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .one()
            )
            payback_id = payback.id

            resp = auth_client.delete(f"/transactions/{txn.id}/unmark-credit")
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Projected"
            # Payback is deleted as part of the unmark-credit workflow.
            assert db.session.get(Transaction, payback_id) is None


# ══════════════════════════════════════════════════════════════════════
# Transfer legs: route exception handling
# ══════════════════════════════════════════════════════════════════════


class TestAnIllegalMoveFromATransfersLegIsA400:
    """``transfer_service.update_transfer`` already enforces the state
    machine (commit C-21).  These tests confirm the route layer's
    exception handling translates the resulting ValidationError into
    a clean 400 instead of a 500 when mark_done is invoked from a
    transfer's grid leg while the transfer is in a non-mutable state.

    Each case reached its leg through a shadow (twin) row until plan step
    ``balance:X-bi-6-4d-2``, and a transfer stores no leg row since
    migration 82f7 (ruling **R-BAL167** class 1)."""

    def _create_transfer(
        self, app, db_session, seed_user, seed_periods_today,
    ):
        """Helper -- builds a savings account, the Transfers categories
        the service requires, and a Projected transfer.  Returns the
        transfer so tests can drive status changes through it.  Named
        ``_create_transfer_with_shadows`` until plan step
        ``balance:X-bi-6-4d-2``.
        """
        from app.models.category import Category
        from app.models.ref import AccountType
        from app.services import transfer_service

        savings_type = (
            db_session.query(AccountType).filter_by(name="Savings").one()
        )
        savings = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
                anchor_balance=Decimal("500.00"),
            ),
        )
        db_session.add(savings)
        db_session.flush()
        # Its BOOKS open before anything this fixture dates (plan step
        # X-f3c-2b, ruling **R-HG**): ``create_account`` opens them on the day it
        # asserts -- the owner's today -- and this suite settles on or before it.
        open_books_before_the_first_assertion(db_session, savings)

        for group, item in (("Transfers", "Outgoing"), ("Transfers", "Incoming")):
            db_session.add(
                Category(
                    user_id=seed_user["user"].id,
                    group_name=group,
                    item_name=item,
                )
            )
        db_session.commit()

        projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
        xfer = transfer_service.create_transfer(
            transfer_service.TransferSpec(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                amount_ownership=AmountOwnership.own(Decimal("100.00")),
                status_id=projected_id,
                category_id=seed_user["categories"]["Rent"].id,
            ),
        )
        db_session.commit()
        return xfer

    def test_mark_done_on_cancelled_transfer_from_its_leg_returns_400(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """mark_done on a CANCELLED transfer from its grid LEG returns 400.

        **Re-expressed at plan step balance:X-bi-6-1 (ruling R-BAL87)**: it
        posted to the SHADOW row's transaction door, the only cover for
        ``_shadow_mutations._mark_done_shadow``'s refusal branch; that module
        is deleted and the grid's Mark Paid is the transfer's door carrying
        ``leg_account_id``, whose ``except ValidationError ->
        _error_transfer_response`` branch renders the refusal into the LEG's
        cell.  It was written over a SETTLED shadow until plan step
        **balance:X-am** deleted that status, and a first pass deleted it
        outright -- leaving the branch with no test at all, which an
        adversarial review caught.

        **Cancelled is the better specimen anyway, because the button is
        RENDERED there.**  The card partials suppress Mark Paid on
        ``Status.is_settled``, and Cancelled is not settled -- so this refusal
        is reached by an ordinary click rather than by a crafted request.

        The refusal is graded on the TRANSFER, the one status, and on the
        leg's side, which holds no dated payment record; it was graded on the
        shadow's status until plan step ``balance:X-bi-6-4d-2`` stopped keeping
        one (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).  The leg
        is the transfer's from-side; it was the first shadow row the session
        returned until plan step ``balance:X-bi-6-4d-2``, which deleted the
        twins (ruling R-BAL167 class 1).
        """
        with app.app_context():
            xfer = self._create_transfer(
                app, db.session, seed_user, seed_periods_today,
            )
            from app.services import transfer_service

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.CANCELLED),
            )
            db.session.commit()

            leg_account_id = xfer.from_account_id
            resp = auth_client.post(
                f"/transfers/instance/{xfer.id}/mark-done",
                data={"leg_account_id": str(leg_account_id)},
            )
            assert resp.status_code == 400
            html = resp.data.decode()
            assert "Invalid transfer status transition" in html
            assert f'data-leg-account-id="{leg_account_id}"' in html

            db.session.expire_all()
            assert db.session.get(Transfer, xfer.id).status_id == (
                ref_cache.status_id(StatusEnum.CANCELLED)
            )
            assert transfer_side_settle_day(
                db.session, xfer.id, leg_account_id,
            ) is None

    def test_cancel_on_paid_transfer_from_its_leg_returns_400(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Cancel on a Paid transfer from its grid LEG returns 400 (Done ->
        Cancelled is illegal under the state machine).  Re-expressed on the
        transfer door at plan step balance:X-bi-6-1 (ruling R-BAL87); its leg
        is the from-side since plan step balance:X-bi-6-4d-2 deleted the shadow
        row it was reached through (ruling R-BAL167 class 1)."""
        with app.app_context():
            xfer = self._create_transfer(
                app, db.session, seed_user, seed_periods_today,
            )
            from app.services import transfer_service

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()

            leg_account_id = xfer.from_account_id
            resp = auth_client.post(
                f"/transfers/instance/{xfer.id}/cancel",
                data={"leg_account_id": str(leg_account_id)},
            )
            assert resp.status_code == 400
            assert f'data-leg-account-id="{leg_account_id}"' in resp.data.decode()

    def test_rejected_transfer_update_rolls_back_staged_mutations(
        self, app, seed_user, seed_periods_today,
    ):
        """deep-quality-hunt #42: a rejected amount+illegal-status transfer
        PATCH leaves NO dirty mutations staged on the session.

        **Re-expressed at plan step balance:X-bi-6-1 (ruling R-BAL87)** from
        ``_shadow_mutations._apply_shadow_update``, the deleted door this
        graded, onto the door the grid's leg uses: the transfer PATCH's own
        executor (``routes/transfers/mutations._execute_transfer_update``),
        whose ``except ValidationError -> _error_transfer_response`` is the
        rollback under test -- and which had no rollback control of its own
        until this re-expression.

        transfer_service.update_transfer mutates the transfer's amount
        in-memory BEFORE the state-machine transition check raises
        ValidationError, so without the except-branch rollback that dirty
        mutation sits on the session and any later commit in the same request
        would flush the half-applied change.  It read the shadow's amount
        beside the transfer's until plan step ``balance:X-bi-6-4d-2``: a
        transfer stores no leg row since migration 82f7 (ruling **R-BAL167**
        class 1).  Drive the path directly (a
        route-level test cannot observe this -- per-request teardown
        discards the session either way) and assert the session is clean
        after the 400.

        **Measured 2026-09-20 on the transfer executor**: with BOTH the
        ``rollback()`` and the ``expire_all()`` deleted from
        ``_error_transfer_response`` the commit below wrote ``999.00``; with
        the rollback alone deleted it still passed, because ``expire_all()``
        on its own discards a pending, unflushed change -- so a mutation of
        this control must remove both lines to prove anything.
        """
        from flask_login import login_user  # pylint: disable=import-outside-toplevel
        from app.routes.transfers.mutations import (  # pylint: disable=import-outside-toplevel
            _execute_transfer_update,
        )

        with app.test_request_context():
            login_user(seed_user["user"])
            xfer = self._create_transfer(
                app, db.session, seed_user, seed_periods_today,
            )
            # The transfer STAYS Projected, and that is deliberate: the row
            # must be MUTABLE so the finalised-edit lock does not refuse the
            # amount before anything is staged.  A test whose 400 comes from
            # the wrong guard has stopped grading the rollback.
            #
            # It walked to ``Settled`` -- the terminal archive -- until plan
            # step **balance:X-am**, which is also why the illegal transition
            # had to move: every state in both maps can now reach Projected, so
            # ``-> Projected`` is legal from everywhere.
            #
            # **The replacement must also be a move that does NOT SETTLE**, and
            # that is the part a first attempt got wrong.  ``projected ->
            # received`` is illegal for a transfer, but it ENTERS the settled
            # band, so ``update_transfer`` dispatches to the settle verb, which
            # refuses before ``_apply_remaining_fields`` has staged anything --
            # and this case then passed with the rollback DELETED, measured.
            # ``projected -> credit`` is illegal AND settles nothing, so the
            # amount is staged first and the seam raises after it: the rollback
            # is the only thing that unstages it.
            assert not db.session.dirty  # clean baseline before the PATCH

            # PATCH carries an amount change AND an illegal transition
            # (transfer projected -> credit): update_transfer stages the
            # amount mutation, then verify_transition raises ValidationError.
            result = _execute_transfer_update(
                xfer,
                {
                    "amount": Decimal("999.00"),
                    "status_id": ref_cache.status_id(StatusEnum.CREDIT),
                },
                amount_authored=True,
            )
            assert result is not None and result[1] == 400
            # The except branch must have rolled back the staged transfer
            # mutation.
            assert not db.session.dirty, (
                f"rejected transfer update left mutations staged: "
                f"{db.session.dirty}"
            )
            # **AND A LATER COMMIT WRITES NOTHING**, which is the assertion
            # that actually grades the rollback -- ``not db.session.dirty``
            # cannot.
            #
            # Measured 2026-08-27, by deleting the ``db.session.rollback()`` in
            # ``_error_transaction_response`` and then its ``expire_all()``
            # too: this case passed BOTH times on the dirty check alone.  The
            # error path re-fetches the row to render the fragment, and the
            # autoflush that comes with it FLUSHES the staged mutation -- so
            # ``session.dirty`` empties by WRITING the change, not by
            # discarding it.  An "is anything staged" assertion cannot tell a
            # rollback from a flush.
            #
            # What the rollback really protects is the next commit in the same
            # session, so the control has to be a commit.  Re-reading without
            # one proves nothing either: nothing here has committed, so a
            # ``rollback()`` in the TEST would restore the row whatever the
            # route did.
            db.session.commit()
            db.session.expire_all()
            reread_xfer = db.session.get(Transfer, xfer.id)
            assert reread_xfer.amount == Decimal("100.00"), (
                "a commit after the refused PATCH wrote the transfer's amount"
            )
            assert reread_xfer.status_id == ref_cache.status_id(
                StatusEnum.PROJECTED,
            )


# ══════════════════════════════════════════════════════════════════════
# Service-level unmark_credit guard tests
# ══════════════════════════════════════════════════════════════════════


class TestUnmarkCreditServiceGuard:
    """Direct service-level tests for the new guards in
    ``credit_workflow.unmark_credit``.  Route-level coverage above
    confirms the 400 translation; these tests pin the exception type
    and message produced by the service so future refactors do not
    silently regress the friendly-message contract."""

    def test_raises_on_paid_status(
        self, app, seed_user, seed_periods_today,
    ):
        """unmark_credit on a Paid txn raises ValidationError naming
        the status."""
        import pytest

        from app.exceptions import ValidationError
        from app.services import credit_workflow

        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to(txn, "Paid")

            with pytest.raises(ValidationError) as excinfo:
                credit_workflow.unmark_credit(txn.id, seed_user["user"].id)
            msg = str(excinfo.value)
            assert "Paid" in msg
            assert "Only Credit" in msg

    def test_raises_on_cancelled_status(
        self, app, seed_user, seed_periods_today,
    ):
        """unmark_credit on a Cancelled txn raises ValidationError too --
        the bespoke guard fires before the state machine layer.

        The second specimen exists so the guard is shown refusing more than one
        non-Credit status; it was ``Settled`` -- the terminal archive -- until
        plan step **balance:X-am** deleted it, and the guard reads
        ``status.name`` rather than a status set, so any other status does."""
        import pytest

        from app.exceptions import ValidationError
        from app.services import credit_workflow

        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to(txn, "Cancelled")

            with pytest.raises(ValidationError) as excinfo:
                credit_workflow.unmark_credit(txn.id, seed_user["user"].id)
            assert "Cancelled" in str(excinfo.value)
