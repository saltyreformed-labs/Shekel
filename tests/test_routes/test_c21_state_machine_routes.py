"""
Shekel Budget App -- C-21 Route-Level State Machine Tests

PATCH /transactions/<id> and the transfer service status path both
funnel through ``app.services.state_machine.verify_transition``.
These tests exercise the route layer's translation of an illegal
transition into a 400 -- and confirm that legal transitions still
produce the cell-render 200 the HTMX UI depends on.

The same PATCH route also enforces the finalised-row field-edit lock
(``finalised_edit_rejection``, #26): money / period / category /
due-date edits are refused on a finalised row unless the same request
reverts it to Projected.  ``TestPatchRejectsFinalisedFieldEdit`` /
``TestPatchAllowsEditableField`` cover that policy at the route layer.

Audit reference: F-046 / F-047 / F-161 / commit C-21 of the
2026-04-15 security remediation plan.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.services import status_seam
from app.models.ref import TransactionType
from tests._test_helpers import one_off_row_of, resolved_amount, settlement_if_settling


def _create_projected_expense(seed_user, seed_periods_today):
    """Insert a projected expense -- the typical PATCH starting state.

    Uses the same fixture set as ``tests/test_routes/test_grid/`` so
    these tests share the auth_client / seed_user wiring without
    introducing a parallel fixture stack.
    """
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    txn = one_off_row_of(
        seed_periods_today[0],
        name="Test Expense",
        amount=Decimal("123.45"),
        user_id=seed_periods_today[0].user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=expense_type.id,
        category_id=seed_user["categories"]["Groceries"].id,
    )
    db.session.commit()
    return txn


def _walk_to_paid(txn):
    """Drive a freshly-projected row through projected -> done -> settled.

    Bypasses the ROUTE layer to keep the helper terse -- the transitions
    exercised here are themselves covered by the legal suite and by the
    existing transfer-service tests -- but goes through the status SEAM rather
    than assigning the column: since plan step X-f1 the seam writes the settle
    day in the same call, and a bare assignment leaves a settled row with no
    day, which every balance reader refuses.

    It walked one step further, to the terminal ``Settled`` ARCHIVE, until plan
    step **balance:X-am** deleted that status.  The tests below that used it
    were attacking the archive; each is restated over a rule that is still
    live, or deleted where the move it called illegal has become legal --
    which for ``Settled -> Projected`` is the whole point of the step.
    """
    done_id = ref_cache.status_id(StatusEnum.DONE)
    status_seam.apply_status_change(txn, done_id, settlement=settlement_if_settling(txn, done_id))
    db.session.commit()


# ── Route returns 400 on illegal transition ─────────────────────────


class TestPatchRejectsIllegalTransition:
    """The PATCH /transactions/<id> handler must surface a 400 with a
    state-machine message when the proposed status_id is unreachable
    from the row's current status."""

    def test_paid_to_cancelled_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A Paid row PATCHed to Cancelled returns 400 and stays Paid.

        The route-level control for the transaction map's refusal, restated
        over a live rule at plan step **balance:X-am**.  It attacked the
        terminal ARCHIVE before -- ``Settled -> Projected``, ``Settled ->
        Cancelled``, ``Projected -> Settled``.  The first of those is now
        LEGAL, which is the step's whole content: a settled row can always be
        reverted and corrected.  ``Paid -> Cancelled`` is the refusal that
        remains and it is the same one in substance -- money that moved cannot
        be un-moved by re-labelling it; revert first, so the audit log carries
        both legs.
        """
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to_paid(txn)
            paid_id = ref_cache.status_id(StatusEnum.DONE)
            cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)
            assert txn.status_id == paid_id

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(cancelled_id)},
            )
            assert response.status_code == 400
            # Body names the transition so the user understands why
            # the request was refused.
            body = response.data.decode()
            assert "transaction" in body
            assert str(paid_id) in body
            assert str(cancelled_id) in body

            db.session.refresh(txn)
            assert txn.status_id == paid_id

    def test_paid_to_received_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A Paid row cannot become Received without reverting first.

        The WITHIN-BAND refusal, and the reason it is worth a route test of its
        own: since **X-am** the settled band is exactly ``{Paid, Received}``, so
        this is the only non-identity move inside it -- the one case
        ``balance_predicates.is_identity_move`` exists to be narrower than.
        """
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to_paid(txn)
            paid_id = ref_cache.status_id(StatusEnum.DONE)

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(ref_cache.status_id(StatusEnum.RECEIVED))},
            )
            assert response.status_code == 400
            db.session.refresh(txn)
            assert txn.status_id == paid_id


# ── Route accepts legal transitions ─────────────────────────────────


class TestPatchAcceptsLegalTransition:
    """Legal transitions still produce the HTMX cell-render 200 that
    the grid depends on -- the state-machine guard is non-disruptive
    on every workflow path the user actually exercises."""

    def test_projected_to_done_accepted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The most common transition still returns 200."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            done_id = ref_cache.status_id(StatusEnum.DONE)

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(done_id)},
            )
            assert response.status_code == 200
            db.session.refresh(txn)
            assert txn.status_id == done_id

    def test_done_to_projected_revert_accepted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Revert path stays open so users can fix mismarks."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

            # Walk to Done first.
            status_seam.apply_status_change(txn, done_id, settlement=settlement_if_settling(txn, done_id))
            db.session.commit()

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected_id)},
            )
            assert response.status_code == 200
            db.session.refresh(txn)
            assert txn.status_id == projected_id

    def test_projected_to_projected_identity_accepted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Idempotent re-submission of the same status is silently
        accepted -- HTMX double-clicks must not produce 400s."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected_id)},
            )
            assert response.status_code == 200
            db.session.refresh(txn)
            assert txn.status_id == projected_id


# ── Finalised-row field-edit lock (#26) ─────────────────────────────


class TestPatchRejectsFinalisedFieldEdit:
    """A finalised (Paid/Received/Credit/Cancelled) row's
    money / period / category / due-date fields cannot be rewritten via
    PATCH unless the same request reverts it to Projected.  Each test
    drives a row to Paid, edits a locked field with NO
    status change, and asserts a 400 plus an unchanged stored value --
    the gap was that a status-less PATCH skipped every guard.

    **The lock is what the ARCHIVE was reached for and never added to**, which
    is part of why plan step **balance:X-am** deleted that status: every
    ``is_immutable`` row already refuses these edits, and the archive's only
    extra content was that it could not be reverted to lift the lock."""

    def test_paid_row_amount_edit_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Editing estimated_amount on a Paid row is refused and the
        stored amount is unchanged (the headline silent-rewrite gap)."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            status_seam.apply_status_change(txn, ref_cache.status_id(StatusEnum.DONE), settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)))
            db.session.commit()

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"estimated_amount": "999.99", "estimated_amount_as_rendered": "123.45"},
            )
            assert response.status_code == 400
            body = response.data.decode()
            assert "finalised" in body
            assert "Paid" in body
            assert "Revert" in body

            db.session.refresh(txn)
            # The pre-edit value (123.45 from _create_projected_expense)
            # survives -- the rewrite never reached the row.
            assert resolved_amount(txn) == Decimal("123.45")

    def test_paid_row_category_edit_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Re-categorising a Paid row is refused; category unchanged."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to_paid(txn)
            original_category_id = txn.category_id
            other_category_id = seed_user["categories"]["Rent"].id
            assert other_category_id != original_category_id

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"category_id": str(other_category_id)},
            )
            assert response.status_code == 400
            db.session.refresh(txn)
            assert txn.category_id == original_category_id

    def test_paid_row_period_move_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Moving a Paid row to another period is refused; period kept."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to_paid(txn)
            original_period_id = txn.pay_period_id
            other_period_id = seed_periods_today[1].id
            assert other_period_id != original_period_id

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"pay_period_id": str(other_period_id)},
            )
            assert response.status_code == 400
            db.session.refresh(txn)
            assert txn.pay_period_id == original_period_id

    def test_paid_row_amount_edit_while_re_submitting_paid_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A PATCH that re-states Paid AND rewrites the amount is refused.

        A status change that does not REVERT the row cannot lift the lock, and
        the identity move is the case the popover actually produces: it posts
        the whole row on every Save, so an untouched status box arrives as
        ``Paid -> Paid`` beside whatever else the form carried.

        Written over ``Paid -> Settled`` (the archive) until plan step
        **balance:X-am**.  The rule under test is *a new status lifts the lock
        only when it is mutable*, which is about ``is_immutable`` on the target
        and never about which status it is, so the specimen moved and the rule
        did not.
        """
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            status_seam.apply_status_change(txn, done_id, settlement=settlement_if_settling(txn, done_id))
            db.session.commit()

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(done_id), "estimated_amount": "5.00", "estimated_amount_as_rendered": "123.45"},
            )
            assert response.status_code == 400
            db.session.refresh(txn)
            # Neither the status nor the amount moved.
            assert txn.status_id == done_id
            assert resolved_amount(txn) == Decimal("123.45")


class TestPatchAllowsEditableField:
    """The lock is scoped: it leaves Projected rows fully editable, lets a
    finalised row edit non-locked display fields, and permits the
    revert-and-correct edit in a single request."""

    def test_projected_row_amount_edit_allowed(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A Projected row's amount is still freely editable (no regression)."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"estimated_amount": "200.00", "estimated_amount_as_rendered": "123.45"},
            )
            assert response.status_code == 200
            db.session.refresh(txn)
            assert resolved_amount(txn) == Decimal("200.00")

    def test_paid_row_notes_edit_allowed(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A display field (notes) stays editable on a Paid row -- only the
        money / period / category / due-date fields are locked."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            status_seam.apply_status_change(txn, ref_cache.status_id(StatusEnum.DONE), settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)))
            db.session.commit()

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"notes": "Reconciled against statement"},
            )
            assert response.status_code == 200
            db.session.refresh(txn)
            assert txn.notes == "Reconciled against statement"

    def test_paid_row_revert_and_amount_edit_allowed(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Reverting Paid -> Projected AND editing the amount in one PATCH is
        allowed -- the escape hatch the lock deliberately preserves."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            status_seam.apply_status_change(txn, ref_cache.status_id(StatusEnum.DONE), settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)))
            db.session.commit()
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={
                    "status_id": str(projected_id),
                    "estimated_amount": "200.00",
                    "estimated_amount_as_rendered": "123.45",
                },
            )
            assert response.status_code == 200
            db.session.refresh(txn)
            assert txn.status_id == projected_id
            assert resolved_amount(txn) == Decimal("200.00")

    def test_finalised_full_edit_notes_save_with_money_omitted_allowed(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The real finalised full-edit save: the disabled money inputs are
        OMITTED from the POST and only ``notes`` + the unchanged Status
        dropdown value are submitted.  The guard passes (no locked field in
        the payload) and the notes update commits while the amount is
        untouched -- proving the template's ``disabled`` (not ``readonly``)
        choice is what lets a notes-only save through (#26)."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            status_seam.apply_status_change(txn, done_id, settlement=settlement_if_settling(txn, done_id))
            db.session.commit()

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"notes": "Reconciled", "status_id": str(done_id)},
            )
            assert response.status_code == 200
            db.session.refresh(txn)
            assert txn.notes == "Reconciled"
            assert txn.status_id == done_id
            assert resolved_amount(txn) == Decimal("123.45")
