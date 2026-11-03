"""
Shekel Budget App -- Adversarial QA Test Suite

Hostile QA tests that probe for bugs, validation gaps, and edge cases
the happy-path suite doesn't cover.  Each test documents the vulnerability
it would catch.  Assertions match CURRENT behavior (even if broken) with
comments explaining ideal behavior.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.enums import MovementFigureSourceEnum
from tests._test_helpers import (
    constraint_name_from,
    cover_bare_settled_row,
    default_settle_day,
    freeze_today,
    generate_row_of,
    make_expense_template,
    one_off_row_of,
    record_paydays_across_a_hole,
    resolved_amount,
    rhythm_of,
    settle_day_columns,
    settlement_if_settling,
    start_test_pay_list,
)


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze today to date(2026, 3, 20) so seed_periods tests pass past 2026-05-22.

    Adversarial tests use seed_periods (calendar-anchored Jan-May 2026)
    and exercise route handlers that ask which paycheck contains today.
    Freezing today inside the seed range keeps those routes returning
    populated grid HTML regardless of wall-clock date, so input-
    validation assertions ("/grid?periods=10000 still renders") stay
    meaningful.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.ref import AccountType, FilingStatus, Status, TransactionType
from app.models.salary_profile import SalaryProfile
from app.models.transaction import Transaction
from app.models.transfer_template import TransferTemplate
from app.services.balance_at import BalanceContext
from app.services import (
    carry_forward_service,
    credit_workflow,
    status_seam,
)
from app.services import account_service
from app.services.cash_ledger import contribution_of
from app.services.row_valuation import settled_contribution, settled_figure
from tests._test_helpers import amount_basis_for


def _worth(txn):
    """What *txn* contributes at whatever status it is in right now.

    The state-machine cases below walk ONE row through Projected, Cancelled,
    Credit and the settled band, so they need the valuation that is total over
    status: :func:`~app.services.cash_ledger.contribution_of`, which answers a
    settled row from its record and an unsettled one from the amount model.

    They asked ``row_valuation.owned_contribution`` until plan step X-bx, which
    deleted its fall-through onto the plan column -- so it answers only for a
    row that has SETTLED now (finding **BAL-465**) and cannot grade a walk that
    passes through Projected.  Every figure these cases assert is unchanged.
    """
    return contribution_of(txn, amount_basis_for(txn))


# ── Helpers ──────────────────────────────────────────────────────────


def _make_transaction(seed_user, seed_periods, *, period_index=0, status_name="Projected",
                      txn_type_name="Expense", amount="100.00", name="Test Item",
                      category_key="Rent", settled_amount=None):
    """Create and flush a transaction with sensible defaults.

    A row built in a SETTLED status carries the whole record -- the day, the
    figure and how the figure is known -- through the one door a bare-built
    fixture uses (``_test_helpers.cover_bare_settled_row``, which writes the
    covering movement; plan steps X-au-c3 and balance:X-bi-4b-2).
    *settled_amount* is a figure a HUMAN typed, which makes the record
    ``corrected``; with none the record is ``derived`` at the row's own plan.
    """
    status = db.session.query(Status).filter_by(name=status_name).one()
    planned = Decimal(amount)
    settled_on = default_settle_day(seed_periods[period_index], status.id)
    txn = one_off_row_of(
        seed_periods[period_index],
        name=name,
        amount=planned,
        user_id=seed_periods[period_index].user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=(
            db.session.query(TransactionType).filter_by(name=txn_type_name).one().id
        ),
        category_id=seed_user["categories"][category_key].id,
    )
    # The status and the settle state laid on BARE, as ``add_txn`` lays them:
    # the row is the producer's (plan step balance:X-bi-7c), the state on top
    # is this builder's purpose.
    txn.status_id = status.id
    for _column, _value in settle_day_columns(settled_on).items():
        setattr(txn, _column, _value)
    db.session.flush()
    if settled_on is not None:
        # The record is the covering movement (X-bi-4b-1; the row's one home
        # for it since X-bi-4b-2).
        cover_bare_settled_row(db.session, txn, planned, settled_amount)
    return txn


def _make_savings_account(seed_user):
    """Create a second (savings) account for transfer tests."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            anchor_balance=Decimal("500.00"),
        ),
    )
    db.session.add(acct)
    db.session.flush()
    return acct


# ══════════════════════════════════════════════════════════════════════
# 1. State Machine Violations
# ══════════════════════════════════════════════════════════════════════


class TestStateMachineViolations:
    """Probe the PATCH /transactions/<id> endpoint for unconstrained
    status_id and the mark_done endpoint for missing guards."""

    def test_update_status_to_nonexistent_id(self, app, auth_client, seed_user, seed_periods):
        """PATCH with status_id=9999 returns 400 (FK constraint caught).

        The DB FK to ref.statuses catches invalid references and the
        IntegrityError handler returns a clean 400 response.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            db.session.commit()

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": "9999"},
            )
            assert resp.status_code == 400

    def test_update_done_back_to_projected(self, app, auth_client, seed_user, seed_periods):
        """Mark done then PATCH back to projected -- no transition guard.

        Bug: There is no domain logic preventing backward status transitions.
        A 'done' transaction can be freely reverted to 'projected'.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods, status_name="Paid")
            db.session.commit()

            projected = db.session.query(Status).filter_by(name="Projected").one()
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected.id)},
            )
            # Current behavior: allowed -- no transition guard.
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Projected"

    def test_mark_done_negative_actual_amount(self, app, auth_client, seed_user, seed_periods):
        """POST mark_done with settled_amount=-500 is rejected at the schema.

        The form field validates non-negative (``_NON_NEGATIVE_MONETARY``), and
        ``status_seam.Settlement`` refuses a negative figure at construction
        (the storage-tier CHECK on the row's own column said the same through
        plan step X-bi-4b-1; L-01).
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            db.session.commit()

            resp = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"settled_amount": "-500.00"},
            )
            assert resp.status_code == 422

    def test_mark_done_already_done_transaction(self, app, auth_client, seed_user, seed_periods):
        """POST mark_done on an already-done transaction.

        Bug: No idempotency check -- re-setting status is harmless but
        the actual_amount could be overwritten if a new value is posted.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods, status_name="Paid",
                settled_amount=Decimal("75.00"),
            )
            db.session.commit()

            # Call mark_done again without an actual_amount -- should keep the old one.
            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 200

            db.session.refresh(txn)
            # Current behavior: status is re-set, actual_amount preserved
            # because the form didn't send one.
            assert settled_figure(txn) == Decimal("75.00")

    def test_projected_to_cancelled_to_projected_double_reversal(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Projected → cancelled → projected double reversal.

        Cancel a projected transaction, then PATCH it back to projected.
        The audit flagged this round-trip as missing test coverage.
        Verifies effective_amount correctness at each stage.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            db.session.commit()
            # Through the resolver: a one-off's row stores no figure, its
            # definition prices it (ruling R-BAL60).
            original_amount = resolved_amount(txn)
            assert original_amount == Decimal("100.00")

            # Verify initial effective_amount.
            assert _worth(txn) == original_amount

            # Step 1: Cancel the transaction.
            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"
            assert _worth(txn) == Decimal("0")

            # Step 2: PATCH back to projected.
            projected = db.session.query(Status).filter_by(name="Projected").one()
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected.id)},
            )
            # Current behavior: no transition guard, reversion is allowed.
            # Ideal: cancelled → projected should require explicit confirmation
            # to prevent accidental re-projection of cancelled items.
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Projected"
            assert _worth(txn) == original_amount

    def test_done_to_cancelled_transition(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Done -> cancelled transition is now rejected (C-21 follow-up).

        After the C-21 follow-up the cancel endpoint runs every
        status change through ``verify_transition``.  Done -> Cancelled
        is illegal -- the user must revert to Projected first so the
        audit trail records both the revert and the subsequent
        cancellation.  The actual_amount stays untouched on the
        rejected request.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods, status_name="Paid",
                settled_amount=Decimal("85.00"),
            )
            db.session.commit()

            # Verify initial effective_amount uses actual_amount.
            assert settled_contribution(txn) == Decimal("85.00")

            # Cancel the transaction -- now refused.
            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 400

            db.session.refresh(txn)
            # Row stays Paid; actual_amount preserved.
            assert txn.status.name == "Paid"
            assert settled_figure(txn) == Decimal("85.00")
            assert settled_contribution(txn) == Decimal("85.00")

    def test_received_to_projected_reversion(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Received -> projected reversion on an income transaction.

        Income that RECORDED ``$2,800.00`` is reverted to projected via PATCH.

        **The revert releases the ASSERTION and KEEPS what moved** (plan step
        X-au-c3, developer 2026-08-17).  ``settled_on`` and the clearing link
        say "this money moved on this day and that statement showed it", and a
        revert withdraws exactly that; the covering movement says what the
        bank took and who said so, which the revert does not un-know.  The row is worth its PLAN again because the STATUS decides
        which figure governs (``row_valuation.settled_figure``), not because the
        record was destroyed.

        That is still the danger this adversarial case names, and it is
        unchanged in dollars: the reverted row re-projects at ``$3,000.00``,
        ``$200.00`` above what was really received.  What changed is that
        settling it again RESTORES the ``$2,800.00`` instead of re-deriving over
        it, so the round trip the full-edit popover instructs ("set Status to
        Projected to edit the amounts") no longer destroys a figure the user read
        off their statement.  A draft of this step released all three columns
        together under a CHECK pairing the day with the basis; that welded two
        facts with different lifetimes into one, which is finding **N-241**'s
        shape rebuilt one level up.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods, txn_type_name="Income",
                name="Paycheck", category_key="Salary", amount="3000.00",
                status_name="Received",
                settled_amount=Decimal("2800.00"),
            )
            db.session.commit()

            # The row is worth what it RECORDED while it is settled.
            assert _worth(txn) == Decimal("2800.00")

            # PATCH back to projected.
            projected = db.session.query(Status).filter_by(name="Projected").one()
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected.id)},
            )
            # Current behavior: no transition guard, reversion is allowed.
            # Ideal: received → projected should be rejected to prevent
            # balance corruption from re-projecting settled income.
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Projected"
            # The ASSERTION went with the revert: no day, no clearing link.
            assert txn.settled_on is None
            assert txn.reconciled_by_id is None
            # WHAT MOVED stayed, and it is still flagged as the human's figure.
            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                Decimal("2800.00"), MovementFigureSourceEnum.TYPED,
            )
            # The row is nonetheless worth its PLAN again -- $200.00 above what
            # was really received, which is the danger this case names. The
            # STATUS is what decides that, not the absence of the record.
            assert settled_figure(txn) is None
            assert _worth(txn) == Decimal("3000.00")

    def test_credit_to_projected_reversion_deletes_payback(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Credit -> projected reversion via PATCH deletes the payback.

        A transaction marked as credit (auto-generates payback) is
        reverted to projected via PATCH.  The PATCH path shares
        ``credit_workflow.delete_payback_on_credit_revert`` with
        ``unmark_credit``, so the auto-generated payback is deleted
        with the reversion -- the orphaned payback this test
        previously pinned (which inflated the next period's projected
        expenses) can no longer be created.  Behavior change ratified
        2026-06-11 (deep-quality-hunt orphaned-payback candidate).
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods, period_index=0)
            db.session.commit()

            # Mark as credit -- creates payback in next period.
            resp = auth_client.post(f"/transactions/{txn.id}/mark-credit")
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Credit"
            assert _worth(txn) == Decimal("0")

            # Find the payback transaction.
            payback = db.session.query(Transaction).filter_by(
                credit_payback_for_id=txn.id,
            ).one()
            payback_id = payback.id

            # PATCH the original back to projected.
            projected = db.session.query(Status).filter_by(name="Projected").one()
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected.id)},
            )
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Projected"
            assert _worth(txn) == Decimal("100.00")

            # The payback is hard-deleted with the reversion, exactly
            # like unmark_credit.
            orphan = db.session.get(Transaction, payback_id)
            assert orphan is None, \
                "Payback must be deleted on credit->projected reversion"

    def test_credit_identity_resubmit_keeps_payback(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """An idempotent Credit -> Credit PATCH keeps the payback.

        The PATCH path's payback cleanup fires only when the row
        actually LEAVES Credit status.  An identity re-submit (the
        state machine admits Credit -> Credit for idempotent saves,
        e.g. a notes edit from the popover that posts the unchanged
        status) must not delete the auto-generated payback out from
        under a still-credited transaction.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods, period_index=0)
            db.session.commit()

            resp = auth_client.post(f"/transactions/{txn.id}/mark-credit")
            assert resp.status_code == 200

            payback = db.session.query(Transaction).filter_by(
                credit_payback_for_id=txn.id,
            ).one()
            payback_id = payback.id

            # Re-submit the same Credit status (identity transition).
            credit = db.session.query(Status).filter_by(name="Credit").one()
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(credit.id)},
            )
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Credit"
            # The payback survives the identity re-submit.
            assert db.session.get(Transaction, payback_id) is not None

    def test_cancelled_to_done_direct(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Cancelled -> done via direct PATCH is rejected (C-21).

        After commit C-21 the PATCH endpoint runs every status change
        through ``app.services.state_machine.verify_transition``.
        ``Cancelled`` may only revert to ``Projected``; a direct jump
        to ``Paid`` skips the projected step the audit log relies on
        and is refused with a 400.  The user is expected to revert to
        Projected first and then mark the row as Paid via the
        mark-done endpoint -- which writes the settle day and (on
        envelope rows) settles entries -- preserving the workflow
        guarantees the dashboard depends on.

        Was: documented as "current behavior allows it; ideal would
        reject."  Commit C-21 is the implementation of that ideal,
        addressing audit finding F-161.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods, status_name="Cancelled")
            db.session.commit()
            cancelled_status_id = txn.status_id

            done_status = db.session.query(Status).filter_by(name="Paid").one()
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(done_status.id)},
            )
            # State machine refuses cancelled -> done.  The body names
            # both endpoints so the user understands why.
            assert resp.status_code == 400
            body = resp.data.decode()
            assert "transaction" in body
            assert str(cancelled_status_id) in body
            assert str(done_status.id) in body

            # The row stays Cancelled -- no partial mutation.
            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"
            assert settled_contribution(txn) == Decimal("0")

    def test_cancel_already_cancelled_transaction(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Cancel → cancel idempotency check.

        Cancelling an already-cancelled transaction should be harmless.
        Verifies the status remains cancelled with no side effects.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods, status_name="Cancelled")
            db.session.commit()

            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            # Current behavior: cancel sets status unconditionally. Idempotent.
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"
            assert settled_contribution(txn) == Decimal("0")

    def test_mark_done_on_cancelled_transaction(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """POST mark_done on a cancelled transaction is now rejected (C-21 follow-up).

        After the C-21 follow-up the mark_done endpoint runs every
        status change through ``verify_transition``.  Cancelled may
        only revert to Projected; a direct jump to Paid would
        resurrect a cancelled row and corrupt balance projections
        (the original concern this test documented).  The row stays
        Cancelled and no actual_amount is recorded.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods, status_name="Cancelled")
            db.session.commit()

            resp = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"settled_amount": "95.00"},
            )
            assert resp.status_code == 400

            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"
            # No actual_amount recorded -- the rejected request did
            # not commit any partial state.
            assert status_seam.recorded_settlement(txn) is None
            assert settled_contribution(txn) == Decimal("0")

    def test_mark_credit_on_done_transaction(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """POST mark-credit on a done transaction -- rejected by service.

        credit_workflow.mark_as_credit only allows projected transactions.
        Attempting to mark a done transaction as credit returns 400.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods, status_name="Paid",
                settled_amount=Decimal("100.00"),
            )
            db.session.commit()

            resp = auth_client.post(f"/transactions/{txn.id}/mark-credit")
            # credit_workflow raises ValidationError for non-projected transactions.
            # Route catches it and returns 400.
            assert resp.status_code == 400
            assert b"Only projected" in resp.data

            db.session.refresh(txn)
            assert txn.status.name == "Paid"
            assert settled_contribution(txn) == Decimal("100.00")


# ══════════════════════════════════════════════════════════════════════
# 2. Referential Integrity
# ══════════════════════════════════════════════════════════════════════


class TestReferentialIntegrity:
    """Probe FK cascade/restrict behavior on deletion."""

    def test_delete_account_with_transfers_blocked(self, app, auth_client, seed_user, seed_periods):
        """Deactivating an account with active transfer templates is blocked.

        The route checks for active transfer templates referencing the account
        and returns a flash warning instead of proceeding.
        """
        with app.app_context():
            savings = _make_savings_account(seed_user)
            checking = seed_user["account"]
            checking_id = checking.id

            # Create a transfer template referencing both accounts.
            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=checking_id,
                to_account_id=savings.id,
                name="Monthly Savings",
                is_active=True,
            )
            db.session.add(template)
            db.session.commit()

            resp = auth_client.post(f"/accounts/{checking_id}/archive")
            # Current behavior: route blocks archiving with a flash warning.
            assert resp.status_code == 302  # Redirect back to list

            # Follow redirect to verify the flash message.
            follow = auth_client.get(resp.headers["Location"])
            assert b"Cannot archive" in follow.data

            # Re-query to verify account is still active.
            acct = db.session.get(Account, checking_id)
            assert acct.is_active is True

    def test_delete_category_with_transactions_blocked(self, app, auth_client, seed_user, seed_periods):
        """DELETE category when transactions reference it -- blocked by route guard.

        The route checks for templates and transactions using the category
        before allowing deletion.
        """
        with app.app_context():
            _make_transaction(seed_user, seed_periods, category_key="Rent")
            db.session.commit()

            rent_cat = seed_user["categories"]["Rent"]
            resp = auth_client.post(f"/categories/{rent_cat.id}/delete")
            # Current behavior: route blocks with flash warning.
            assert resp.status_code == 302

            # Follow redirect to verify the flash message.
            follow = auth_client.get(resp.headers["Location"])
            assert b"in use" in follow.data

            # Category still exists.
            cat = db.session.get(Category, rent_cat.id)
            assert cat is not None

    def test_delete_pay_period_cascades_transactions(self, app, auth_client, seed_user, seed_periods):
        """DELETE period → all transactions silently destroyed (CASCADE).

        Bug: Transaction.pay_period_id has ondelete='CASCADE'.  Deleting a
        period destroys all its transactions without any application-level
        warning.  This documents the cascading data loss.

        Note: Must use raw SQL to bypass ORM relationship handling, and
        must use a non-anchor period (period 2) since the anchor period
        is FK-referenced by the account.
        """
        with app.app_context():
            # Use period index 2 (not the anchor period at index 0).
            txn = _make_transaction(seed_user, seed_periods, period_index=2)
            db.session.commit()
            txn_id = txn.id
            period_id = seed_periods[2].id

            # Use raw SQL to trigger DB-level CASCADE (ORM intercepts otherwise).
            db.session.execute(
                db.text("DELETE FROM budget.pay_periods WHERE id = :pid"),
                {"pid": period_id},
            )
            db.session.commit()

            # Expire cached objects so we get fresh DB state.
            db.session.expire_all()

            # Transaction cascaded away.
            assert db.session.get(Transaction, txn_id) is None

    def test_a_definition_with_a_row_cannot_be_deleted(self, app, auth_client, seed_user, seed_periods):
        """DELETE template under a row -> REFUSED; the row keeps its link.

        ``Transaction.template_id`` was ``ON DELETE SET NULL`` until the
        one-definition cutover (plan step ``balance:X-bi-7d-2``, ruling
        **R-BAL20**): a row survived its definition's deletion as a
        zero-link row.  ``ck_transactions_one_pricing_link`` reads ``= 1``
        and the key is ``RESTRICT`` now, so the same act is refused -- the
        ORM's flush nulls the child's link first and the CHECK refuses
        that, and a raw ``DELETE`` meets the key -- and the row keeps its
        definition.  ``definition_delete`` removes a definition's rows
        BEFORE the definition for exactly this reason.
        """
        with app.app_context():
            # A definition and its own row (the engine's, plan step
            # balance:X-cf-4).
            template = make_expense_template(
                db.session, seed_user, amount="1500.00", name="Rent Template",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            txn_id, template_id = txn.id, template.id

            db.session.delete(template)
            with pytest.raises(IntegrityError) as exc:
                db.session.commit()
            assert constraint_name_from(exc.value) in {
                "ck_transactions_one_pricing_link", "fk_transactions_template_id",
            }
            db.session.rollback()

            txn = db.session.get(Transaction, txn_id)
            assert txn is not None
            assert txn.template_id == template_id


# ══════════════════════════════════════════════════════════════════════
# 3. Credit Workflow Edge Cases
# ══════════════════════════════════════════════════════════════════════


class TestCreditWorkflowEdgeCases:
    """Probe credit_workflow.py for data loss and nonsensical states."""

    def test_unmark_credit_deletes_done_payback(self, app, auth_client, seed_user, seed_periods):
        """Unmark credit on a transaction whose payback is already marked done.

        Bug: unmark_credit deletes the payback regardless of its status.
        If the payback was already paid (done), this represents real money
        lost from the tracking -- data loss.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods, period_index=0)
            db.session.commit()

            # Mark as credit -- creates payback in next period.
            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.commit()
            payback_id = payback.id

            # Simulate: payback was paid.
            # Through the real seam, which writes the whole settlement record
            # in one act -- a bare status assign leaves the row settled with no
            # day and no figure, which the record's own CHECKs refuse.
            done_status = db.session.query(Status).filter_by(name="Paid").one()
            status_seam.apply_status_change(
                payback, done_status.id,
                settlement=settlement_if_settling(payback, done_status.id),
            )
            db.session.commit()

            # Unmark credit -- payback is deleted even though it was done.
            credit_workflow.unmark_credit(txn.id, seed_user["user"].id)
            db.session.commit()

            # Current behavior: done payback is deleted. Data loss.
            assert db.session.get(Transaction, payback_id) is None

    def test_mark_credit_on_income_transaction(self, app, auth_client, seed_user, seed_periods):
        """Mark credit on an income transaction -- rejected by service.

        The service correctly raises ValidationError for income.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods, txn_type_name="Income",
                name="Paycheck", category_key="Salary", amount="3000.00",
            )
            db.session.commit()

            from app.exceptions import ValidationError
            with pytest.raises(ValidationError, match="Cannot mark income as credit"):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)

    def test_mark_credit_on_last_period(self, app, auth_client, seed_user, seed_periods):
        """Mark credit on a transaction in the last period -- no next period.

        Bug: get_next_period() returns None when the transaction is in the
        last pay period.  mark_as_credit raises ValidationError.
        """
        with app.app_context():
            last_period_idx = len(seed_periods) - 1
            txn = _make_transaction(
                seed_user, seed_periods, period_index=last_period_idx,
            )
            db.session.commit()

            from app.exceptions import ValidationError
            with pytest.raises(ValidationError, match="No next pay period"):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)


# ══════════════════════════════════════════════════════════════════════
# 4. Carry Forward Edge Cases
# ══════════════════════════════════════════════════════════════════════


class TestCarryForwardEdgeCases:
    """Probe carry_forward_service for source==target and credit handling."""

    def test_carry_forward_source_equals_target(self, app, auth_client, seed_user, seed_periods):
        """Carry forward with source == target period returns 0 without modification.

        The source==target guard returns early, preventing the previous bug
        of setting is_override=True on template-linked transactions.
        """
        with app.app_context():
            # A definition's own row in period 0 (the engine's, plan step
            # balance:X-cf-4).
            template = make_expense_template(
                db.session, seed_user, amount="1500.00", name="Rent Template",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()

            # Carry forward from period 0 to period 0.
            period_id = seed_periods[0].id
            count = carry_forward_service.carry_forward_unpaid(
                period_id, period_id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            # Guard returns 0 -- no items processed, no side effects.
            assert count == 0
            db.session.refresh(txn)
            assert txn.pay_period_id == period_id
            assert txn.is_override is False  # Not modified by the guard

    def test_carry_forward_preserves_credit_transactions(self, app, auth_client, seed_user, seed_periods):
        """Carry forward should NOT move credit-status transactions.

        Credit transactions are settled via their payback mechanism.
        carry_forward_unpaid only moves 'projected' status items, so
        credit-status items are correctly excluded.
        """
        with app.app_context():
            # Create a transaction and mark it as credit.
            txn = _make_transaction(seed_user, seed_periods, period_index=0)
            db.session.commit()
            credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.commit()

            # Also create a projected transaction in the same period.
            projected_txn = _make_transaction(
                seed_user, seed_periods, period_index=0, name="Groceries",
                category_key="Groceries",
            )
            db.session.commit()

            # Carry forward from period 0 to period 2.
            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[2].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            # Only the projected transaction should be moved.
            assert count == 1
            db.session.refresh(projected_txn)
            assert projected_txn.pay_period_id == seed_periods[2].id

            # Credit transaction stays in its original period.
            db.session.refresh(txn)
            assert txn.pay_period_id == seed_periods[0].id


# ══════════════════════════════════════════════════════════════════════
# 5. Input Validation Bypass
# ══════════════════════════════════════════════════════════════════════


class TestInputValidationBypass:
    """Probe for unvalidated query params and schema gaps."""

    def test_grid_non_numeric_periods_param(self, app, auth_client, seed_user, seed_periods):
        """?periods=abc gracefully falls back to default periods.

        Flask's request.args.get(..., type=int) returns the default
        when the value cannot be converted, instead of raising ValueError.
        """
        with app.app_context():
            resp = auth_client.get("/grid?periods=abc")
            assert resp.status_code == 200

    def test_grid_negative_periods_param(self, app, auth_client, seed_user, seed_periods):
        """?periods=-1 → negative period count.

        Bug: No Range validation on the periods query param.
        With -1, get_periods_in_range returns empty and grid renders with
        no columns (but doesn't crash).
        """
        with app.app_context():
            resp = auth_client.get("/grid?periods=-1")
            # Current behavior: renders a valid page (grid or pay period setup).
            assert resp.status_code == 200
            assert b"Shekel" in resp.data
            assert b"Traceback" not in resp.data
            assert b"Internal Server Error" not in resp.data

    def test_grid_extreme_periods_param(self, app, auth_client, seed_user, seed_periods):
        """?periods=10000 → resource exhaustion risk.

        Bug: No upper bound on the periods count.  With 10000, the query
        is bounded by actual data (only 10 periods exist), so it doesn't
        crash, but in production with more data this could be a problem.
        """
        with app.app_context():
            resp = auth_client.get("/grid?periods=10000")
            # Current behavior: works because only 10 periods exist in test data.
            assert resp.status_code == 200
            assert b"grid-table" in resp.data
            assert b"Traceback" not in resp.data
            assert b"Internal Server Error" not in resp.data

    def test_transfer_zero_amount_via_schema(self, app, auth_client, seed_user, seed_periods):
        """TransferCreateSchema should reject amount=0.

        The schema uses validate.Range(min=0, min_inclusive=False) which
        correctly rejects zero.  The DB CHECK constraint also requires > 0.
        """
        with app.app_context():
            from app.schemas.validation import TransferCreateSchema
            schema = TransferCreateSchema()

            savings = _make_savings_account(seed_user)
            db.session.commit()

            errors = schema.validate({
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "amount": "0.00",
                "pay_period_id": str(seed_periods[0].id),
                "scenario_id": str(seed_user["scenario"].id),
            })
            # Schema correctly rejects amount=0.
            assert "amount" in errors


# ══════════════════════════════════════════════════════════════════════
# 7. Numeric Edge Cases
# ══════════════════════════════════════════════════════════════════════


class TestNumericEdgeCases:
    """Probe Numeric(12,2) field boundaries."""

    def test_transaction_amount_at_db_max(self, app, auth_client, seed_user, seed_periods):
        """Decimal("9999999999.99") -- at the Numeric(12,2) max.

        Should store OK: 10 digits before decimal + 2 after = 12 total.  The
        figure is stored on the one-off's definition (its price version,
        ``Numeric(12,2)`` too) and read back through the resolver (R-BAL60).
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods, amount="9999999999.99",
            )
            db.session.commit()
            db.session.refresh(txn)
            assert resolved_amount(txn) == Decimal("9999999999.99")

    def test_transaction_amount_exceeds_db_max(self, app, auth_client, seed_user, seed_periods):
        """Decimal("99999999999.99") -- exceeds Numeric(12,2).

        Bug: No application-level guard.  PostgreSQL raises a
        NumericValueOutOfRange (DataError) on flush.  The figure lands on
        the one-off's DEFINITION first (``transaction_templates.default_
        amount``, ``Numeric(12,2)`` too) and the producer flushes, so the
        refusal fires inside the placement, before the price series is
        opened (plan step balance:X-bi-7c).
        """
        with app.app_context():
            from sqlalchemy.exc import DataError

            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Current behavior: DB raises DataError on flush/commit.
            # Ideal: schema-level max value check would catch this first.
            with pytest.raises(DataError):
                one_off_row_of(
                    seed_periods[0],
                    name="Overflow Test",
                    amount=Decimal("99999999999.99"),
                    user_id=seed_periods[0].user_id,
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    transaction_type_id=txn_type.id,
                    category_id=seed_user["categories"]["Rent"].id,
                )
            db.session.rollback()

    def test_schema_rejects_negative_amount(self, app, seed_user, seed_periods):
        """TransactionCreateSchema with negative estimated_amount.

        The schema has validate.Range(min=0) which should reject negatives.
        """
        with app.app_context():
            from app.schemas.validation import TransactionCreateSchema
            schema = TransactionCreateSchema()

            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            errors = schema.validate({
                "name": "Bad Item",
                "estimated_amount": "-50.00",
                "pay_period_id": str(seed_periods[0].id),
                "scenario_id": str(seed_user["scenario"].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                "transaction_type_id": str(expense_type.id),
            })
            assert "estimated_amount" in errors


# ══════════════════════════════════════════════════════════════════════
# 8. Auth Edge Cases
# ══════════════════════════════════════════════════════════════════════


class TestAuthEdgeCases:
    """Cross-user resource access -- confirm defense in depth."""

    def test_access_other_users_transaction(
        self, app, auth_client, seed_user, seed_periods, second_user,
    ):
        """User A tries to view User B's transaction → must get 404.

        Routes filter by current_user.id via _get_owned_transaction().
        Cross-user access should return 404, not 403 (no info leakage).
        Uses the shared second_user fixture from conftest.py.
        """
        with app.app_context():
            # Create a pay period for user 2.
            periods2 = record_paydays_across_a_hole(
                user_id=second_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=2,
                rhythm=rhythm_of(14),
            )
            db.session.flush()

            # Create a transaction for user 2.
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            txn2 = one_off_row_of(
                periods2[0],
                name="Other's Expense",
                amount=Decimal("99.99"),
                user_id=periods2[0].user_id,
                account_id=second_user["account"].id,
                scenario_id=second_user["scenario"].id,
                transaction_type_id=txn_type.id,
                category_id=second_user["categories"]["Rent"].id,
            )
            db.session.commit()

            # Auth client is logged in as user 1 -- try to access user 2's txn.
            resp = auth_client.get(f"/transactions/{txn2.id}/cell")
            assert resp.status_code == 404

            resp = auth_client.patch(
                f"/transactions/{txn2.id}",
                data={"estimated_amount": "1.00"},
            )
            assert resp.status_code == 404

            # Verify DB state unchanged after IDOR PATCH attempt -- through
            # the resolver: the victim's row is a one-off, priced by its
            # definition (ruling R-BAL60).
            db.session.refresh(txn2)
            assert resolved_amount(txn2) == Decimal("99.99"), \
                "IDOR PATCH should not have modified the victim's amount"
            assert txn2.status.name == "Projected", \
                "IDOR PATCH should not have modified the victim's status"

    def test_access_other_users_account(
        self, app, auth_client, seed_user, seed_periods, second_user,
    ):
        """User A tries to edit User B's account -- must get 404 (security).

        Account routes check user_id == current_user.id and return 404
        when the account doesn't belong to the current user (security
        rule: 404 for both not-found and not-yours).
        Uses the shared second_user fixture from conftest.py.
        """
        with app.app_context():
            # Auth client is logged in as user 1 -- try to edit user 2's account.
            resp = auth_client.get(f"/accounts/{second_user['account'].id}/edit")
            assert resp.status_code == 404
            assert b"Other Checking" not in resp.data

            # Verify DB state unchanged after IDOR attempt.
            acct2 = db.session.get(Account, second_user["account"].id)
            assert acct2.name == "Other Checking", \
                "IDOR attempt should not have modified the victim's account"


# ══════════════════════════════════════════════════════════════════════
# 9. SQL Injection Prevention
# ══════════════════════════════════════════════════════════════════════


class TestSQLInjectionPrevention:
    """Verify SQL injection payloads are rejected or stored literally.

    SQLAlchemy uses parameterized queries, so data fields are safe by
    construction.  Query parameters parsed via int() or schema validation
    also reject injection strings.  These tests verify that explicitly.
    """

    def test_grid_period_param_sql_injection(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """SQL injection in ?periods param is safely ignored.

        Flask's request.args.get(..., type=int) returns the default
        when the value cannot be converted, rejecting the injection.
        """
        with app.app_context():
            resp = auth_client.get(
                "/?periods=1;DROP%20TABLE%20budget.transactions--"
            )
            assert resp.status_code == 200

            # Verify the transactions table still exists.
            count = db.session.execute(
                db.text("SELECT count(*) FROM budget.transactions")
            ).scalar()
            assert count is not None

    def test_transaction_patch_sql_in_name(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """SQL injection payload in transaction name is stored literally.

        SQLAlchemy parameterized queries prevent execution.  The payload
        is stored as a plain string in the name column.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            db.session.commit()

            payload = "'; DROP TABLE budget.transactions; --"
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"name": payload},
            )
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.name == payload

            # Verify the table still exists and the transaction is intact.
            count = db.session.execute(
                db.text("SELECT count(*) FROM budget.transactions")
            ).scalar()
            assert count >= 1

    def test_transaction_patch_sql_in_amount(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """SQL injection in estimated_amount is rejected by schema validation.

        The Marshmallow schema validates estimated_amount as a Decimal.
        A SQL injection string is not a valid decimal and returns 422.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            db.session.commit()
            # Through the resolver on both sides (ruling R-BAL60): a one-off's
            # row stores no figure, so the raw column read None == None and
            # the "unchanged" half graded nothing (found by 7c-5's review).
            original_amount = resolved_amount(txn)
            assert original_amount == Decimal("100.00")

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"estimated_amount": "0; DROP TABLE budget.transactions"},
            )
            assert resp.status_code == 422

            db.session.refresh(txn)
            assert resolved_amount(txn) == original_amount

            # Verify the table still exists.
            count = db.session.execute(
                db.text("SELECT count(*) FROM budget.transactions")
            ).scalar()
            assert count >= 1


# ══════════════════════════════════════════════════════════════════════
# 10. Cross-Resource IDOR
# ══════════════════════════════════════════════════════════════════════


class TestCrossResourceIDOR:
    """IDOR tests for salary profiles, categories, and transfer templates.

    Authenticated as user 1, attempt to access/modify resources owned
    by user 2.  Each test verifies both the HTTP response and DB state.
    """

    def test_edit_other_users_salary_profile(
        self, app, auth_client, seed_user, seed_periods, second_user,
    ):
        """User A tries to GET User B's salary profile edit page → redirect.

        The salary route checks user_id ownership and redirects with
        a flash warning when the profile doesn't belong to the current user.
        """
        with app.app_context():
            filing_single = db.session.query(FilingStatus).filter_by(
                name="single",
            ).one()

            profile = SalaryProfile(
                user_id=second_user["user"].id,
                scenario_id=second_user["scenario"].id,
                filing_status_id=filing_single.id,
                name="Other User Job",
                state_code="NC",
            )
            db.session.add(profile)
            start_test_pay_list(profile, Decimal("2307.69"))  # $60,000.00 a year / 26
            db.session.commit()
            profile_id = profile.id
            # The salary is the profile's pay list since plan step
            # salary:X-av-3a.
            original_pay = [(e.payday, e.amount) for e in profile.pay_entries]

            # Auth client (user 1) tries to access user 2's salary profile.
            resp = auth_client.get(f"/salary/{profile_id}/edit")
            assert resp.status_code == 404

            # Verify profile is unchanged in DB.
            db.session.refresh(profile)
            assert [(e.payday, e.amount) for e in profile.pay_entries] == original_pay
            assert profile.name == "Other User Job"

    def test_delete_other_users_category(
        self, app, auth_client, seed_user, seed_periods, second_user,
    ):
        """User A tries to DELETE User B's category -- rejected.

        The categories route checks user_id ownership and returns 404
        (security: 404 for not-found and not-yours).
        """
        with app.app_context():
            cat = second_user["categories"]["Rent"]
            cat_id = cat.id

            resp = auth_client.post(f"/categories/{cat_id}/delete")
            assert resp.status_code == 404

            # Verify category still exists.
            cat_after = db.session.get(Category, cat_id)
            assert cat_after is not None

    def test_delete_other_users_transfer_template(
        self, app, auth_client, seed_user, seed_periods, second_user,
    ):
        """User A tries to DELETE User B's transfer template -- rejected.

        The transfers route checks user_id ownership and returns 404
        (security: 404 for not-found and not-yours).
        """
        with app.app_context():
            # Create a savings account and transfer template for second user.
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            savings_acct2 = account_service.create_account(
                account_service.AccountSpec(
                    user_id=second_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Other Savings",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(savings_acct2)
            db.session.flush()

            template2 = TransferTemplate(
                user_id=second_user["user"].id,
                from_account_id=second_user["account"].id,
                to_account_id=savings_acct2.id,
                name="Other Savings Transfer",
            )
            db.session.add(template2)
            db.session.commit()
            template2_id = template2.id

            # Auth client (user 1) tries to archive user 2's template.
            resp = auth_client.post(f"/transfers/{template2_id}/archive")
            assert resp.status_code == 404

            # Verify template still exists and is active.
            db.session.refresh(template2)
            assert template2.is_active is True
