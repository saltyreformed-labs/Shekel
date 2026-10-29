"""Transfer route tests: POST /transfers/<id>/hard-delete, a template's permanent deletion."""

from decimal import Decimal

from app.extensions import db
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
from app.models.ref import Status
from app.services import transfer_service
from tests._test_helpers import generate_transfer_of, transfer_side_record
from tests._test_helpers import transfer_amount

from ._helpers import (
    _create_savings_account,
    _ledger_of,
    _create_template,
    _create_transfer,
    _received_transfer_of,
    _create_other_user_with_template,
)


# ── Hard Delete Tests (5A.5-3) ─────────────────────────────────────


class TestTransferTemplateHardDelete:
    """Tests for POST /transfers/<id>/hard-delete (permanent deletion).

    These tests verify transfer invariant compliance: all deletions must
    flow through the transfer service.  They also counted shadow
    transactions before and after, so none was orphaned, until plan step
    ``balance:X-bi-6-4d-2``, which births none: no transaction can name a
    transfer (``ck_transactions_names_no_transfer`` refused one until plan
    step balance:X-bi-6-5 dropped the column), so an orphan count is over
    rows nothing can write, 0 before any act,
    and those counts are dropped (ruling R-BAL167 class 1, plan step
    balance:X-bi-6-4d-2).
    """

    def test_hard_delete_transfer_template_no_history(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C-5A.5-17: Template with only Projected transfers is permanently deleted."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)

            template_id = template.id
            xfer_id = xfer.id

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data

            # Template is gone.
            assert db.session.get(TransferTemplate, template_id) is None

            # Transfer is gone.
            assert db.session.get(Transfer, xfer_id) is None

    def test_hard_delete_transfer_template_no_transfers(
        self, app, auth_client, seed_user,
    ):
        """Template with zero transfers ever generated is permanently deleted."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)
            template_id = template.id

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data
            assert db.session.get(TransferTemplate, template_id) is None

    def test_hard_delete_transfer_template_with_history(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C-5A.5-18: Template with Paid transfer is blocked and archived instead.

        "The Paid transfer is untouched" read its two shadows, still there and
        not hidden, until plan step ``balance:X-bi-6-4d-2``, which births
        none; it reads the transfer, not hidden, and both sides' payment
        records, still held, which is what the settle left (ruling R-BAL167
        class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)

            # Two transfers of the definition, the engine's: one Projected,
            # one settled through the transfer service's named verb.
            xfer_projected = _create_transfer(
                seed_user, seed_periods_today, savings, template,
            )
            xfer_paid = generate_transfer_of(template, seed_periods_today[1])
            transfer_service.settle_transfer(xfer_paid.id, seed_user["user"].id)
            db.session.commit()

            resp = auth_client.post(
                f"/transfers/{template.id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"has payment history" in resp.data
            assert b"archived instead" in resp.data

            # Template still exists but is archived.
            db.session.refresh(template)
            assert template.is_active is False

            # Paid transfer and its two sides' records are untouched.
            db.session.refresh(xfer_paid)
            assert xfer_paid.is_deleted is False
            for account_id in (xfer_paid.from_account_id, xfer_paid.to_account_id):
                assert transfer_side_record(
                    db.session, xfer_paid.id, account_id,
                ) is not None

            # Projected transfer is soft-deleted.
            db.session.refresh(xfer_projected)
            assert xfer_projected.is_deleted is True

    def test_a_deleted_paid_transfer_is_no_history_so_the_template_is_deleted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Its only Paid transfer deleted, "Permanently delete" deletes the definition.

        Ruling **R-BAL246** ("Back as a plan, now", developer 2026-10-09):
        the delete that takes a Paid transfer's payments off sets it back to
        Projected, so nothing settled is left against its recurring transfer
        and the permanent delete is no longer turned into an archive -- the
        outcome Josh's answer 17 was promised a test of.  The ledger the
        transfer's own delete left is the ledger after: the permanent delete
        removes a plan, which posted nothing.  Its count of the transfer's
        shadows after the delete (0) was VACUOUS from plan step
        ``balance:X-bi-6-4d-2``, which births none and whose CHECK refuses
        one: a count over rows nothing can write, 0 before the delete too,
        so it is dropped (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = generate_transfer_of(template, seed_periods_today[0])
            transfer_service.settle_transfer(xfer.id, seed_user["user"].id)
            db.session.commit()
            template_id, xfer_id, savings_id = template.id, xfer.id, savings.id
            checking_id = seed_user["account"].id
            scenario_id = seed_user["scenario"].id
            assert auth_client.delete(
                f"/transfers/instance/{xfer_id}",
            ).status_code == 200
            ledger = _ledger_of(seed_user, checking_id, savings_id, scenario_id)
            # The delete took the payment off: Savings, opened at $0.00,
            # holds nothing.
            assert ledger[1] == Decimal("0.00")

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete", follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data
            assert b"archived instead" not in resp.data
            db.session.expire_all()
            assert db.session.get(TransferTemplate, template_id) is None
            assert db.session.get(Transfer, xfer_id) is None
            assert _ledger_of(
                seed_user, checking_id, savings_id, scenario_id,
            ) == ledger

    def test_hard_delete_transfer_template_with_history_already_archived(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Already-archived template with Paid history stays archived without re-archiving."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)

            xfer_paid = generate_transfer_of(template, seed_periods_today[0])
            transfer_service.settle_transfer(xfer_paid.id, seed_user["user"].id)

            # Pre-archive.
            template.is_active = False
            db.session.commit()

            resp = auth_client.post(
                f"/transfers/{template.id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"has payment history" in resp.data

            db.session.refresh(template)
            assert template.is_active is False

    def test_hard_delete_transfer_template_received_blocked(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C21-6: A transfer template with a RECEIVED transfer is archived, not deleted.

        Mirror of the transaction-template CRIT-05 fix proof.  The
        pre-fix predicate enumerated ``[DONE, SETTLED]`` (SETTLED being the
        terminal archive plan step **balance:X-am** has since deleted) and silently
        omitted ``RECEIVED``; ``RECEIVED`` carries ``is_settled=True``
        in ``ref_seeds.py`` so the post-fix
        ``transfer_template_has_paid_history`` -- now filtering on
        ``Status.is_settled`` -- correctly returns True for a
        RECEIVED transfer and the route archives instead of
        physically destroying the transfer.
        Verifies the predicate fix end-to-end at the route layer.  It also
        counted the transfer's two surviving shadows until plan step
        ``balance:X-bi-6-4d-2``, which births none; the surviving transfer,
        its status and figure are what it reads (ruling R-BAL167 class 1,
        plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)

            received_status = db.session.query(Status).filter_by(name="Received").one()
            xfer_received = _received_transfer_of(
                template, seed_periods_today[0], seed_user, "250.00",
            )
            db.session.commit()

            template_id = template.id
            xfer_id = xfer_received.id

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived instead" in resp.data
            # The archive-fallback flash contains "cannot be permanently
            # deleted" so the broad substring check is unsafe; assert
            # the literal success-flash text never fired instead.
            assert (
                b"Recurring transfer 'Monthly Savings' permanently deleted"
                not in resp.data
            )

            # Template archived, not deleted.
            db.session.refresh(template)
            assert template.is_active is False
            assert db.session.get(TransferTemplate, template_id) is not None

            # RECEIVED transfer preserved with original amount.  Hand-
            # verified: $250.00 stays exactly $250.00 (Decimal from
            # string per coding standards).
            surviving = db.session.get(Transfer, xfer_id)
            assert surviving is not None
            assert surviving.status_id == received_status.id
            assert surviving.is_deleted is False
            assert transfer_amount(surviving) == Decimal("250.00")

    def test_hard_delete_transfer_template_bulk_delete_skips_settled_rows(
        self, app, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """C8-1: Even if the predicate is bypassed, the bulk delete spares settled transfers.

        Defense in depth (F-14, mirror of CRIT-05 / E-22): commit C-21
        of the main remediation already fixed
        ``transfer_template_has_paid_history`` to filter on
        ``Status.is_settled`` so the guard at the route's entry
        catches every settled status.  This commit adds the second
        layer: the bulk-delete loop itself filters on
        ``Transfer.status_id.notin_(settled_status_ids)`` so a
        future regression of the predicate, a race window between
        the guard and the delete, or a different caller that bypasses
        the guard cannot physically destroy settled transfers.  This
        test forces the bypass scenario by
        monkey-patching ``transfer_template_has_paid_history`` to
        return False even when a RECEIVED transfer exists, then
        asserts the post-conditions: the settled transfer survives
        intact while the Projected transfer is deleted as intended.

        ``Transfer.transfer_template_id`` is a FK with ``ON DELETE
        SET NULL`` (``app/models/transfer.py``) so the surviving
        Received transfer has its ``transfer_template_id`` cleared
        but its financial data -- amount, status, period -- is
        intact.  "Untouched" is graded on the
        transfer's status, the one status since plan step
        ``balance:X-bi-6-4d-2``, and on its two sides still holding no
        payment record, as ``_received_transfer_of`` left them -- not on
        the twins' planted statuses, which nothing writes or reads since
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).  It also
        counted and read both transfers' shadows until plan step
        ``balance:X-bi-6-4d-2``, which births none: the deleted transfer's
        orphan count was over rows nothing can write (ruling R-BAL167 class
        1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)

            received_status = db.session.query(Status).filter_by(name="Received").one()

            # RECEIVED transfer in period 0 (must survive).
            xfer_received = _received_transfer_of(
                template, seed_periods_today[0], seed_user, "250.00",
            )
            # PROJECTED transfer in period 1 (must be deleted): the engine's.
            xfer_projected = generate_transfer_of(template, seed_periods_today[1])
            db.session.commit()

            template_id = template.id
            received_id = xfer_received.id
            projected_id = xfer_projected.id

            # Force the bypass: predicate lies and says "no history."
            # The defense-in-depth filter inside the route is what must
            # save the Received transfer.
            monkeypatch.setattr(
                "app.routes.transfers.lifecycle.archive_helpers.transfer_template_has_paid_history",
                lambda _template_id: False,
            )

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200

            # Settled (Received) transfer SURVIVES with original
            # amount and status; FK SET NULL clears transfer_template_id.
            surviving = db.session.get(Transfer, received_id)
            assert surviving is not None
            assert surviving.status_id == received_status.id
            assert surviving.is_deleted is False
            # Hand-verified: original $250.00 stays exactly $250.00
            # (Decimal from string per coding standards).
            assert transfer_amount(surviving) == Decimal("250.00")
            assert surviving.transfer_template_id is None
            for account_id in (surviving.from_account_id, surviving.to_account_id):
                assert transfer_side_record(
                    db.session, received_id, account_id,
                ) is None

            # Non-settled (Projected) transfer was deleted by the
            # bulk loop, as intended -- the defense-in-depth filter is
            # additive, not a wholesale block.
            assert db.session.get(Transfer, projected_id) is None

            # Template itself was deleted (the bypass path completed).
            assert db.session.get(TransferTemplate, template_id) is None

    def test_hard_delete_removes_every_transfer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C-5A.5-19: Hard-deleting a template removes ALL of its transfers.

        Named ``test_hard_delete_preserves_shadow_invariant`` until plan step
        ``balance:X-bi-6-4d-2``, which births no shadow: it counted six
        shadows (two per transfer) before the delete and none after, so no
        shadow was orphaned.  A transaction naming a transfer is refused by
        the database now, so neither count can read anything but 0; the
        three transfers, present before and gone after, are what this reads
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)

            # Three transfers of the definition, the engine's, one per paycheck.
            xfer_ids = [
                generate_transfer_of(template, seed_periods_today[i]).id
                for i in range(3)
            ]
            db.session.commit()
            assert db.session.query(Transfer).filter(
                Transfer.id.in_(xfer_ids),
            ).count() == 3

            template_id = template.id

            # Hard-delete the template.
            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data

            # Template and all transfers are gone.
            assert db.session.get(TransferTemplate, template_id) is None
            for xfer_id in xfer_ids:
                assert db.session.get(Transfer, xfer_id) is None

    def test_hard_delete_transfer_template_idor(
        self, app, auth_client, seed_user,
    ):
        """C-5A.5-20: Hard-deleting another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()
            other_id = other["template"].id

            resp = auth_client.post(
                f"/transfers/{other_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 404

            # Other user's template still exists.
            assert db.session.get(TransferTemplate, other_id) is not None

    def test_list_separates_active_and_archived_transfers(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C-5A.5-21: The unified Recurring surface shows active transfers in
        the Transfers section and archived transfers under the collapsed
        Archived section (reached by following the /transfers redirect)."""
        with app.app_context():
            savings = _create_savings_account(seed_user)

            active = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Active Transfer",
                default_amount=Decimal("100.00"),
            )
            archived = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Archived Transfer",
                default_amount=Decimal("50.00"),
                is_active=False,
            )
            db.session.add_all([active, archived])
            db.session.commit()

            resp = auth_client.get("/transfers", follow_redirects=True)
            assert resp.status_code == 200
            html = resp.data.decode()

            # Active transfer in the Transfers section.
            assert "Active Transfer" in html

            # Archived section with count indicator (both kinds share it).
            assert "Archived (1)" in html
            assert "Archived Transfer" in html

    def test_archive_label_in_flash_transfers(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Archive flash message says 'archived' not 'deactivated'."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            _create_transfer(seed_user, seed_periods_today, savings, template)

            resp = auth_client.post(
                f"/transfers/{template.id}/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived" in resp.data
            assert b"deactivated" not in resp.data

    def test_hard_delete_transfer_template_soft_deleted_transfers_cleaned(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Soft-deleted transfers are permanently removed on hard-delete.

        Its reads of the transfer's shadows -- all hidden before the
        hard-delete, none left after -- were VACUOUS from plan step
        ``balance:X-bi-6-4d-2``, which births none and whose CHECK refuses
        one: an ``all()`` over an empty list and a count 0 before the act,
        so they are dropped (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)
            xfer_id = xfer.id

            # Soft-delete the transfer via the service.
            transfer_service.delete_transfer(xfer.id, seed_user["user"].id, soft=True)
            db.session.commit()

            db.session.refresh(xfer)
            assert xfer.is_deleted is True

            template_id = template.id

            # Hard-delete the template.
            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data

            # Everything is gone -- no ghost data.
            assert db.session.get(TransferTemplate, template_id) is None
            assert db.session.get(Transfer, xfer_id) is None
