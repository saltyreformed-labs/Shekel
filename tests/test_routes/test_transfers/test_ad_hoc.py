"""Transfer route tests: POST /transfers/ad-hoc."""

from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.journal_entry import JournalEntry
from app.models.transfer import Transfer
from app.services import transfer_service
from app.utils.dates import display_today
from tests._test_helpers import (
    an_entered_day,
    on_both_sides,
    transfer_family_journal_filter,
    transfer_side_record,
)

from ._helpers import _create_savings_account, _create_transfer, _create_other_user_with_template


# ── Ad-Hoc Creation ───────────────────────────────────────────────


class TestAdHoc:
    """Tests for POST /transfers/ad-hoc."""

    def test_create_ad_hoc_transfer(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers/ad-hoc creates a transfer and returns 201."""
        with app.app_context():
            savings = _create_savings_account(seed_user)

            response = auth_client.post("/transfers/ad-hoc", data={
                "pay_period_id": seed_periods_today[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "50.00",
                "scenario_id": seed_user["scenario"].id,
                "name": "Quick Transfer",
                "category_id": str(seed_user["categories"]["Rent"].id),
            })

            assert response.status_code == 201
            assert response.headers.get("HX-Trigger") == "balanceChanged"

    def test_create_ad_hoc_transfer_with_due_date(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/ad-hoc with due_date sets it on the transfer.

        Re-read from the database.  It read both shadows' due dates too until
        plan step ``balance:X-bi-6-4d-2``, which births none (ruling R-BAL167
        class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)

            response = auth_client.post("/transfers/ad-hoc", data={
                "pay_period_id": seed_periods_today[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "50.00",
                "scenario_id": seed_user["scenario"].id,
                "name": "Dated Transfer",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "due_date": "2026-03-15",
            })

            assert response.status_code == 201
            db.session.expire_all()
            xfer = (
                db.session.query(Transfer)
                .filter_by(name="Dated Transfer")
                .one()
            )
            assert xfer.due_date == date(2026, 3, 15)

    def test_create_ad_hoc_validation_error(self, app, auth_client, seed_user):
        """POST /transfers/ad-hoc with missing fields returns 422."""
        with app.app_context():
            response = auth_client.post("/transfers/ad-hoc", data={
                "name": "Bad Transfer",
            })

            assert response.status_code == 422
            body = response.get_json()
            assert "errors" in body

    def test_create_ad_hoc_other_users_period(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/ad-hoc with another user's period returns 404.

        Create-path IDOR: must verify no transfer was created in the other
        user's period.
        """
        with app.app_context():
            other = _create_other_user_with_template()
            savings = _create_savings_account(seed_user)

            # Use other user's period.
            from app.models.pay_period import PayPeriod
            other_period = (
                db.session.query(PayPeriod)
                .filter_by(user_id=other["user"].id)
                .first()
            )

            # Count transfers in the other user's period before the request.
            count_before = db.session.query(Transfer).filter_by(
                pay_period_id=other_period.id,
            ).count()

            response = auth_client.post("/transfers/ad-hoc", data={
                "pay_period_id": other_period.id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "50.00",
                "scenario_id": seed_user["scenario"].id,
                "category_id": str(seed_user["categories"]["Rent"].id),
            })

            assert response.status_code == 404

            # Prove no transfer was created.
            db.session.expire_all()
            count_after = db.session.query(Transfer).filter_by(
                pay_period_id=other_period.id,
            ).count()
            assert count_after == count_before, (
                "IDOR attack created a transfer in victim's period!"
            )

    def test_create_ad_hoc_double_submit(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/ad-hoc twice with identical params returns idempotent success.

        F-050 / C-22: the partial unique index
        ``uq_transfers_adhoc_dedupe`` on (user_id, from_account_id,
        to_account_id, amount, pay_period_id, scenario_id) rejects the
        second active ad-hoc transfer with identical parameters.  The
        route translates the IntegrityError into idempotent 201 +
        cell HTML so the user sees the transfer they intended to
        create regardless of which request reached the database
        first.  After two identical submissions the period must
        contain exactly one active ad-hoc transfer.  It also counted two
        active shadow transactions, not four, until plan step
        ``balance:X-bi-6-4d-2``, which births none: a second transfer, the
        only thing that made four, fails the one-transfer count first
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            data = {
                "pay_period_id": seed_periods_today[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "50.00",
                "scenario_id": seed_user["scenario"].id,
                "name": "Double Transfer",
                "category_id": str(seed_user["categories"]["Rent"].id),
            }

            response1 = auth_client.post("/transfers/ad-hoc", data=data)
            assert response1.status_code == 201

            response2 = auth_client.post("/transfers/ad-hoc", data=data)
            # Idempotent success: the second request returns 201 too,
            # but the body references the SAME transfer the first one
            # produced (no new row was inserted).
            assert response2.status_code == 201
            assert response2.headers.get("HX-Trigger") == "balanceChanged"

            # Verify exactly 1 active ad-hoc transfer exists.
            db.session.expire_all()
            transfers = (
                db.session.query(Transfer)
                .filter_by(
                    pay_period_id=seed_periods_today[0].id,
                    user_id=seed_user["user"].id,
                    is_deleted=False,
                )
                .filter(Transfer.transfer_template_id.is_(None))
                .filter_by(amount=Decimal("50.00"))
                .all()
            )
            assert len(transfers) == 1, (
                f"Expected exactly 1 active ad-hoc transfer after "
                f"double-submit, found {len(transfers)}"
            )

    def test_create_ad_hoc_different_amount_allowed(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Two ad-hoc transfers with different amounts both succeed.

        F-050 / C-22: the unique constraint includes ``amount`` so
        a $50 transfer and a $100 transfer between the same accounts
        in the same period are treated as different ad-hoc rows --
        the user legitimately split a payment, the constraint must
        not block it.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            base = {
                "pay_period_id": seed_periods_today[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": str(seed_user["categories"]["Rent"].id),
            }

            r1 = auth_client.post(
                "/transfers/ad-hoc", data={**base, "amount": "50.00"},
            )
            r2 = auth_client.post(
                "/transfers/ad-hoc", data={**base, "amount": "100.00"},
            )

            assert r1.status_code == 201
            assert r2.status_code == 201

            db.session.expire_all()
            count = (
                db.session.query(Transfer)
                .filter_by(
                    pay_period_id=seed_periods_today[0].id,
                    user_id=seed_user["user"].id,
                    is_deleted=False,
                )
                .filter(Transfer.transfer_template_id.is_(None))
                .count()
            )
            assert count == 2, (
                f"Expected 2 distinct ad-hoc transfers, found {count}"
            )

    def test_mark_done_transfer_sets_the_settle_day_on_both_sides(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/instance/<id>/mark-done dates both sides.

        F-048 / C-22: parity with ``transactions.mark_done``.
        Settling a transfer must record when it was settled so
        ``Transaction.days_paid_before_due`` analytics, the
        dashboard's "paid on time" indicator, and any downstream
        report that reads the settle day work.  Both sides are
        checked because the parent transfer has no ``settled_on``
        column of its own: each side's day is on its payment RECORD,
        which hangs off the transfer since plan step
        ``balance:X-bi-6-4d-2``, and the transfer's status is the one
        status (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        Named ``..._on_both_shadows`` until plan step
        ``balance:X-bi-6-4d-2``, which births no shadow; its count of two
        went too (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.post(
                f"/transfers/instance/{xfer.id}/mark-done"
            )
            assert response.status_code == 200

            db.session.expire_all()
            assert db.session.get(Transfer, xfer.id).status.name == "Paid"
            for account_id in (xfer.from_account_id, xfer.to_account_id):
                record = transfer_side_record(db.session, xfer.id, account_id)
                assert record is not None and record.settled_on is not None, (
                    f"The side on account {account_id} has no settled_on "
                    f"after mark-done; the F-048 parity gap is back."
                )

    def test_re_marking_a_settled_transfer_does_not_re_date_it(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A replayed mark-done leaves a settled transfer's money where it is.

        Finding **N-178**, reproduced before it was fixed: ``done -> done`` is a
        legal transition and neither mark-done route gates on status, so a stale
        page, a second tab or a replayed POST re-submits the settle.  The route
        used to pass ``settled_on=db.func.now()`` explicitly, and
        ``update_transfer``'s explicit-day branch wrote both shadows
        VERBATIM after the status seam has already preserved the existing
        instant -- so the re-submit moved the money to today.  Since plan step
        E1a a settle's civil day IS the posted ``entry_date``, which is why this
        asserts the LEDGER and not only the column: measured at the defective
        commit, a transfer settled 7 days earlier gained a reversal at its real
        settle day plus a fresh posting at today.

        This is finding N-146 through a second door.  N-146's fix was in the
        seam; nothing stopped a caller overriding the seam, and that is what
        both mark-done routes did.

        Each side's day is read off its payment RECORD, which hangs off the
        transfer since plan step ``balance:X-bi-6-4d-2``; the twins keep no
        day (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            sides = (xfer.from_account_id, xfer.to_account_id)

            assert auth_client.post(
                f"/transfers/instance/{xfer.id}/mark-done"
            ).status_code == 200

            # Back-date THROUGH the service, so the posted ledger follows the
            # column.  Setting the attribute directly would leave the ledger at
            # today and the assertion below could pass on a stale ledger rather
            # than on a preserved one -- the control has to start from the state
            # a genuinely week-old settle is really in.
            settled_a_week_ago = display_today() - timedelta(days=7)
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_entered_day(settled_a_week_ago),
                ),
            )
            db.session.commit()

            def _ledger_days():
                return sorted(
                    entry.entry_date
                    for entry in db.session.query(JournalEntry)
                    .filter(transfer_family_journal_filter(xfer.id))
                    .all()
                )

            days_before = _ledger_days()
            side_days_before = {
                account_id: transfer_side_record(
                    db.session, xfer.id, account_id,
                ).settled_on
                for account_id in sides
            }
            assert None not in side_days_before.values(), (
                "fixture left a side undated"
            )

            # The replay.
            assert auth_client.post(
                f"/transfers/instance/{xfer.id}/mark-done"
            ).status_code == 200

            db.session.expire_all()
            for account_id in sides:
                moved = (
                    transfer_side_record(
                        db.session, xfer.id, account_id,
                    ).settled_on
                    - side_days_before[account_id]
                ).days
                assert moved == 0, (
                    f"The side on account {account_id} was re-dated by "
                    f"{moved} days by a replayed mark-done (N-178)."
                )
            assert _ledger_days() == days_before, (
                "The replayed mark-done moved the posted ledger: "
                f"{days_before} -> {_ledger_days()} (N-178)."
            )
