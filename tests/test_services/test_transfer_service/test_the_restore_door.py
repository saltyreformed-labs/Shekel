"""The restore door, transfer_service.restore_transfer: a hidden transfer comes back unhidden and
unchanged, and the restores it refuses (a missing or another user's transfer, an archived source
or destination account, or both).
"""

from decimal import Decimal

import pytest

from app.models.account import Account
from app.services import transfer_service
from app.services.transfer_legs import planned_transfer_legs
from app.exceptions import NotFoundError, ValidationError
from tests._test_helpers import transfer_amount

from ._helpers import _create_basic_transfer, _reread


# ── Restore Tests (M1) ──────────────────────────────────────────


class TestRestoreTransfer:
    """``transfer_service.restore_transfer``: a hidden transfer comes back unhidden, unchanged.

    A restore brings a hidden transfer back unhidden, its amount, status and
    pay period unchanged and back in both accounts' plans; refuses while
    either account is archived, leaving the transfer hidden; and answers not
    found for someone else's transfer or none (ruling R-BAL290 part 3, the
    developer, 2026-10-14, "Delete the seven").  Each case that reads the
    transfer reads the row re-read from the database since plan step
    ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-2b, where they read the
    object in the session and the transfer's two shadow rows (ruling
    R-BAL290 part 1).  The nonexistent-transfer case and the both-archived
    refusal read no row; the wrong-owner case reads it after the refusal
    and after the owner's restore since that step's vacuous-residue
    checkpoint.  "Unchanged" is the ruling's three fields: no case here
    reads a restored transfer's due date, category, override flag or notes,
    and a restore that moved its due date or cleared its category fails
    none (measured by the review of that checkpoint).

    Every case holds an AD-HOC transfer.  The shape production restores is
    a transfer of a definition (un-archiving a template restores its
    transfers), and a restore that un-hid ad-hoc transfers only fails six
    route cases (two in the templates' route tests, two in
    ``test_archived_rows_bound_the_books.py``, one each in
    ``test_archived_definition_edit.py`` and
    ``test_a_revert_below_the_books.py``; measured by that review).  A PAID transfer
    hidden and restored comes back a plan, and a ``$0.00`` close comes back
    Paid: :class:`TestDeleteTransfer`'s
    ``test_a_paid_transfer_comes_off_as_a_hidden_plan_and_restores_as_one``
    and ``test_a_zero_close_held_nothing_and_keeps_paid`` (ruling R-BAL246).

    Eight cases were deleted at that checkpoint.  Seven planted a WRONG
    value on a hidden transfer's stored side row and checked that the
    restore repaired it or that nothing read it:
    ``test_repairable_status_drift_is_still_repaired``,
    ``test_a_repair_into_a_projected_status_clears_the_instant`` and
    ``test_corrects_drifted_shadow_status`` (its status),
    ``test_corrects_drifted_shadow_due_date``,
    ``test_corrects_drifted_shadow_category``,
    ``test_corrects_drifted_shadow_is_override`` and
    ``test_a_shadow_amount_CANNOT_drift_from_its_transfer`` (its amount).
    No side row could be stored from that step's create door
    (``ck_transactions_names_no_transfer``, ruling R-BAL258), and no row can
    name a transfer since plan step ``balance:X-bi-6-5`` dropped the column,
    so there is nothing to plant.  The refusal was pinned by
    ``test_a_transfer_is_one_row/test_the_upgrade.py``'s
    ``test_the_database_refuses_a_twin`` (the expense side; it runs below the
    drop) and by a twin case in
    ``test_a_transfer_shadow_is_derived/test_the_declaration.py`` (both
    sides) until plan step ``balance:X-bi-6-5`` dropped the column it
    guarded, which took that case's twin refusal with it (ruling
    plan#1850).  The eighth,
    ``test_raises_on_missing_shadows``, asked the restore to refuse a
    transfer with no side rows, which every transfer is now (ruling
    R-BAL273, "Delete it").

    Three of the seven also read a side of the restored transfer as the grid
    draws it, and the amount case priced both shadow rows (none of those
    reads ran after that step's create door: each case failed at its
    plant).  The restored transfer's amount is
    :meth:`test_restores_transfer_and_shadows`'s, and what a side shows of
    it ``test_transfer_legs/test_what_a_side_shows.py``'s ``TestBothSidesShowTheTransfer``.  Each
    state the side reads asked is asked elsewhere, failing under a mutant
    gated on that state and side (measured at that checkpoint), with one
    exception.  A restored ``$0.00`` close's side reads Paid: on the expense
    side, ``test_transfer_legs/test_the_grids_legs.py``'s ``TestTheLegMapProducers`` and
    ``TestTheLegContributionTwins`` cases
    ``test_a_settled_leg_with_no_movement_is_the_zero_record`` and
    ``test_routes/test_a_zero_close_has_no_day.py``.  On the INCOME side no
    case reads it: the mutant fails only ``test_loan_settled_legs/``'s
    loan payments, each while building its data rather than at a read of
    the side, and limited to a destination that is not a loan it fails
    none (measured by the review of that checkpoint).  The close's own
    Paid is :class:`TestDeleteTransfer`'s
    ``test_a_zero_close_held_nothing_and_keeps_paid``.  A restored planned
    transfer's sides read Projected:
    ``test_transfer_legs/test_what_a_side_shows.py``'s
    ``TestBothSidesShowTheTransfer``, each side.  They hold no settle day:
    ``test_transfer_legs/test_the_grids_legs.py``'s
    ``test_a_planned_and_an_undated_leg_answer_none`` on the expense side,
    and ``test_statement_match/test_leg_candidates.py``'s
    ``test_each_side_is_offered_on_its_own_account_keyed_by_the_transfer``
    on the income side.
    """

    def test_restores_transfer_and_shadows(self, app, transfer_data):
        """A hidden planned transfer comes back unhidden, unchanged, and in both plans.

        The restore un-hides the row and returns it; its amount ($250.00, as
        created), status (Projected) and pay period (the first) are unchanged
        (ruling R-BAL273's condition), and each endpoint's plan holds a leg
        of it again (ruling R-BAL290 part 3).  The plan read is a PROJECTION
        read: that a live transfer is in both plans and a hidden one in
        neither is the plan loader's fact, and a loader that kept hidden
        transfers and dropped live ones fails this case first at it
        (measured at plan step ``balance:X-bi-6-4d-2``'s checkpoint
        2e-2b-2b).  Until that checkpoint it read the object in the session
        and asked the transfer's two shadow rows for their delete flags
        (ruling R-BAL290 part 1).  The name is kept: ruling R-BAL273 names
        it.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            endpoints = (xfer.from_account_id, xfer.to_account_id)
            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            assert _reread(xfer_id).is_deleted is True, "precondition: hidden"

            restored = transfer_service.restore_transfer(xfer_id, td["user"].id)

            row = _reread(xfer_id)
            assert restored is row, "the door returns the session's row, the one it restored"
            assert row.is_deleted is False
            # As created: $250.00, Projected, in the first period.
            assert (transfer_amount(row), row.status_id, row.pay_period_id) == (
                Decimal("250.00"), td["projected_status"].id, td["periods"][0].id,
            )
            for account_id in endpoints:
                assert [
                    leg.transfer.id for leg in planned_transfer_legs(
                        account_id, td["scenario"].id, options=(),
                    )
                ] == [xfer_id]

    def test_rejects_nonexistent_transfer(self, app, db, transfer_data):
        """Verify that restore_transfer raises NotFoundError for a transfer
        ID that does not exist, using the same generic message as other
        not-found conditions to avoid leaking valid ID information.
        """
        with app.app_context():
            with pytest.raises(NotFoundError, match="not found"):
                transfer_service.restore_transfer(
                    999999, transfer_data["user"].id
                )

    def test_rejects_wrong_user(self, app, db, transfer_data, second_user):
        """Verify that restore_transfer raises NotFoundError when called
        with a user_id that does not own the transfer.  The error message
        must be identical to the nonexistent case to prevent ownership
        enumeration.

        The refused restore leaves the owner's transfer hidden: the row is
        read re-read after a COMMIT, which writes whatever the refused
        restore staged, so a restore that un-hid the transfer before its
        ownership refusal fails it (never a rollback before the read, which
        would discard such a staged un-hide).  The owner's own restore then
        lands on the same id, so the refusal is the ownership check's and not
        a wrong id's.  It read the raise alone until plan step
        ``balance:X-bi-6-4d-2``'s vacuous-residue checkpoint.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id
            transfer_service.delete_transfer(
                xfer_id, td["user"].id, soft=True
            )
            db.session.commit()

            with pytest.raises(NotFoundError, match="not found"):
                transfer_service.restore_transfer(
                    xfer_id, second_user["user"].id
                )

            assert _reread(xfer_id).is_deleted is True, (
                "the refused restore un-hid the owner's transfer"
            )

            transfer_service.restore_transfer(xfer_id, td["user"].id)
            assert _reread(xfer_id).is_deleted is False

    def test_idempotent_on_active_transfer(self, app, transfer_data):
        """Verify that calling restore_transfer on an already-active
        transfer completes without error.  This idempotency ensures that
        bulk reactivation workflows do not fail when processing a mix of
        deleted and active transfers.

        The row it leaves is read re-read from the database since plan step
        ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-2b, where it read the
        object the door returned (ruling R-BAL290 part 1); the door returns
        the session's own row.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            # Not deleted -- restore is a no-op.
            restored = transfer_service.restore_transfer(
                xfer.id, td["user"].id
            )

            row = _reread(xfer.id)
            assert restored is row, "the door returns the session's row"
            assert (row.is_deleted, transfer_amount(row)) == (
                False, Decimal("250.00"),
            )

    def test_rejects_when_source_account_archived(
        self, app, db, transfer_data,
    ):
        """F-164 / C-20: refuse to restore a transfer onto an archived
        source account.

        Soft-delete a transfer, then archive the source account.  The
        next ``restore_transfer`` must raise ``ValidationError`` and
        leave the transfer hidden.  This prevents the user from silently
        resurrecting balance entries against an account they have
        withdrawn from active projections.

        The refusal runs before the restore un-hides anything
        (``assert_restorable``, plan step X-aj1), so the row is read
        re-read after a COMMIT, which writes whatever the refused restore
        staged: a restore that un-hid the row before refusing fails it
        (measured at plan step ``balance:X-bi-6-4d-2``'s checkpoint
        2e-2b-2b).  Until that checkpoint it rolled the session back before
        re-reading, which discards such a staged un-hide, and asked the
        transfer's two shadow rows for their delete flags (ruling R-BAL290
        part 1).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            from_account_id = td["account"].id
            db.session.commit()

            transfer_service.delete_transfer(
                xfer_id, td["user"].id, soft=True,
            )
            db.session.commit()

            # Archive the source account post-soft-delete.
            from_account = db.session.get(Account, from_account_id)
            from_account.is_active = False
            db.session.commit()

            with pytest.raises(ValidationError, match="archived"):
                transfer_service.restore_transfer(xfer_id, td["user"].id)

            assert _reread(xfer_id).is_deleted is True

    def test_rejects_when_destination_account_archived(
        self, app, db, transfer_data,
    ):
        """F-164 / C-20: refuse to restore a transfer onto an archived
        destination account.

        Symmetric counterpart to
        ``test_rejects_when_source_account_archived``: archives the
        ``to_account`` instead of the ``from_account`` and verifies the
        same refusal, read the same way, off the row re-read after a
        commit.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            to_account_id = td["savings_account"].id
            db.session.commit()

            transfer_service.delete_transfer(
                xfer_id, td["user"].id, soft=True,
            )
            db.session.commit()

            # Archive the destination account post-soft-delete.
            to_account = db.session.get(Account, to_account_id)
            to_account.is_active = False
            db.session.commit()

            with pytest.raises(ValidationError, match="archived"):
                transfer_service.restore_transfer(xfer_id, td["user"].id)

            assert _reread(xfer_id).is_deleted is True

    def test_rejects_when_both_accounts_archived(
        self, app, db, transfer_data,
    ):
        """F-164 / C-20: refuse to restore when both accounts are archived.

        Defense-in-depth case: if the user has archived both ends of
        the transfer, the refusal must still trigger and the error
        message must remain the same generic ``archived`` text (no
        information about which account is at fault, since both are).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            from_id = td["account"].id
            to_id = td["savings_account"].id
            db.session.commit()

            transfer_service.delete_transfer(
                xfer_id, td["user"].id, soft=True,
            )
            db.session.commit()

            db.session.get(Account, from_id).is_active = False
            db.session.get(Account, to_id).is_active = False
            db.session.commit()

            with pytest.raises(ValidationError, match="archived"):
                transfer_service.restore_transfer(xfer_id, td["user"].id)

    def test_succeeds_when_accounts_active(self, app, db, transfer_data):
        """F-164 / C-20 sanity check: the active-account guard does
        not regress the happy path.

        The archived refusals' control, set up as they are (the transfer
        hidden and committed) with neither account archived, so each of
        their refusals is the archive's: the restore lands, returns the
        row, and leaves it unhidden with its amount, status and pay period
        unchanged (ruling R-BAL273's condition).  Read off the row re-read
        from the database since plan step ``balance:X-bi-6-4d-2``'s
        checkpoint 2e-2b-2b, where it read the object the door returned and
        the transfer's two shadow rows (ruling R-BAL290 part 1).  The name
        is kept: ruling R-BAL273 names it.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            db.session.commit()

            transfer_service.delete_transfer(
                xfer_id, td["user"].id, soft=True,
            )
            db.session.commit()

            restored = transfer_service.restore_transfer(
                xfer_id, td["user"].id,
            )

            row = _reread(xfer_id)
            assert restored is row, "the door returns the session's row"
            # Unhidden, and as created: $250.00, Projected, the first period.
            assert (
                row.is_deleted, transfer_amount(row), row.status_id,
                row.pay_period_id,
            ) == (
                False, Decimal("250.00"), td["projected_status"].id,
                td["periods"][0].id,
            )
