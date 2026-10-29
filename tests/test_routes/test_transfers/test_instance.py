"""Transfer route tests: updating, marking done, cancelling and deleting one transfer, and moving
its pay period.
"""

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry
from app.models.transfer import Transfer
from app.models.ref import Status
from app.services import transfer_legs
from app.services.pay_calendar import calendar_for
from app.services import transfer_service
from app.utils.dates import display_today
from tests._test_helpers import (
    an_entered_day,
    on_both_sides,
    transfer_family_journal_filter,
    transfer_side_record,
)
from app.services.row_valuation import settled_contribution
from app.services.transfer_legs import parent_of
from tests._test_helpers import rendered_transfer_amount
from tests._test_helpers import transfer_amount

from ._helpers import (
    _create_savings_account,
    _create_template,
    _create_transfer,
    _create_other_user_with_template,
)


# ── Transfer Instance Operations ──────────────────────────────────


class TestTransferInstance:
    """Tests for transfer update, mark-done, cancel, and delete."""

    def test_update_transfer_amount(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH /transfers/instance/<id> updates the transfer amount."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "250.00", "amount_as_rendered": rendered_transfer_amount(xfer)},
            )

            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            db.session.refresh(xfer)
            assert transfer_amount(xfer) == Decimal("250.00")

    def test_resubmitting_an_unchanged_status_does_not_re_date_the_money(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A notes-only save on a PAID transfer must not move its settle day.

        Finding **N-146**, plan step X-aj1.  On a finalised transfer the
        full-edit form disables the money fields, so they are omitted from the
        POST and the finalised-row lock sees nothing locked; the status
        ``<select>`` is NOT disabled and submits the row's own status, so an
        ordinary notes edit arrives as an identity transition.  The transfer
        service's own status seam re-stamped the settle instant on any entry
        into a settled status -- and since plan step E1a that day IS the
        ``entry_date`` the transfer's postings are filed under, so editing the
        notes moved the money to today.  Measured at ``HEAD`` before the fix:
        a transfer settled 7 days earlier went from ONE ledger entry to three,
        the last dated today.

        The payload here is the exact one the locked form submits.

        Each side's settle day is read off that side's payment RECORD, which
        hangs off the transfer since plan step ``balance:X-bi-6-4d-2``; the
        twins keep no day (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            sides = (xfer.from_account_id, xfer.to_account_id)

            settled_at = display_today() - timedelta(days=7)
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=done_id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_entered_day(settled_at),
                ),
            )
            db.session.commit()

            def _state():
                """Return the sides' recorded settle days and the posted days."""
                db.session.expire_all()
                paid = sorted(
                    transfer_side_record(db.session, xfer.id, account_id)
                    .settled_on
                    for account_id in sides
                )
                dated = sorted(
                    e.entry_date for e in db.session.query(JournalEntry)
                    .filter(transfer_family_journal_filter(xfer.id)).all()
                )
                return paid, dated

            before = _state()
            assert all(p is not None for p in before[0])

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "version_id": str(xfer.version_id),
                    "status_id": str(done_id),
                    "notes": "reconciled against the statement",
                },
            )
            assert response.status_code == 200, response.data[:400]

            paid_after, dated_after = _state()
            assert paid_after == before[0], (
                f"the settle instant moved: {before[0]} -> {paid_after}"
            )
            assert dated_after == before[1], (
                f"the posted entry_date moved: {before[1]} -> {dated_after}"
            )

            # A SECOND re-submit, this one with no other field at all, must
            # not re-date it either: the schema carries no settle day, so the
            # seam decides, and before X-aj1 it stamped now() on any entry
            # into a settled status.
            #
            # This was ``Paid -> Settled`` -- the ARCHIVE -- until plan step
            # **balance:X-am** deleted that status, and it was the stronger
            # case: a real state CHANGE that still had to preserve the day,
            # where an identity re-submit merely has to leave things alone.
            # With the archive gone there is no non-identity move into or
            # inside the settled band from Paid, so what remains is the bare
            # re-submit -- which is the shape the popover actually produces,
            # since it posts the whole row on every Save.
            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "version_id": str(xfer.version_id),
                    "status_id": str(done_id),
                },
            )
            assert response.status_code == 200, response.data[:400]

            paid_archived, dated_archived = _state()
            assert paid_archived == before[0], (
                f"the bare re-submit moved the settle instant: "
                f"{before[0]} -> {paid_archived}"
            )
            assert dated_archived == before[1], (
                f"the bare re-submit moved the posted entry_date: "
                f"{before[1]} -> {dated_archived}"
            )

    def test_update_transfer_clears_category(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The full-edit "-- None --" option (category_id="") clears it.

        ``transfer_service.update_transfer`` was always built to clear
        on ``category_id=None`` (the assignment sits outside the
        ownership probe's not-None guard), but the schema's pre_load
        used to DROP the empty submit so the None never arrived -- the
        nullable-field clear defect.  The transfer must come back NULL,
        re-read from the database.  Both shadows had to as well until plan
        step ``balance:X-bi-6-4d-2``, which births none: each side's
        category is derived from the transfer's (ruling R-BAL167 class 1,
        plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            assert xfer.category_id == seed_user["categories"]["Rent"].id

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"category_id": ""},
            )

            assert response.status_code == 200
            db.session.expire_all()
            assert db.session.get(Transfer, xfer.id).category_id is None

    def test_update_transfer_due_date(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH /transfers/instance/<id> with due_date updates the transfer.

        Re-read from the database.  It read both shadows' due dates too until
        plan step ``balance:X-bi-6-4d-2``, which births none (ruling R-BAL167
        class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"due_date": "2026-04-22"},
            )

            assert response.status_code == 200
            db.session.expire_all()
            assert db.session.get(Transfer, xfer.id).due_date == date(2026, 4, 22)

    def test_update_transfer_blank_due_date_clears_it(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A blank due_date on the edit form clears the stored due date.

        Retargeted with the nullable-field clear fix (developer-ratified
        behavior change): the edit form pre-fills due_date with the
        stored value, so an empty submit is the user's deliberate clear
        -- TransferUpdateSchema now loads it as an explicit None and the
        service nulls the transfer's due date.  The old "blank does
        not clobber" shield this test pinned also blocked legitimate
        clears; the stale-form clobber it guarded against (a form
        rendered before the date was set) is the C-18 ``version_id``
        pin's job -- the real form ships the pin and a stale submit gets
        a 409 before any field is applied.  The transfer is re-read from the
        database; the shadows' due dates were read too until plan step
        ``balance:X-bi-6-4d-2``, which births none (ruling R-BAL167 class
        1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, due_date=date(2026, 5, 1),
            )
            db.session.commit()

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "300.00", "amount_as_rendered": rendered_transfer_amount(xfer), "due_date": ""},
            )

            assert response.status_code == 200
            db.session.expire_all()
            xfer = db.session.get(Transfer, xfer.id)
            assert transfer_amount(xfer) == Decimal("300.00")
            assert xfer.due_date is None

    def test_mark_done(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers/instance/<id>/mark-done sets status to done."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")

            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            db.session.refresh(xfer)
            assert xfer.status.name == "Paid"

    def test_cancel_transfer(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers/instance/<id>/cancel sets status to cancelled."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.post(f"/transfers/instance/{xfer.id}/cancel")

            assert response.status_code == 200

            db.session.refresh(xfer)
            assert xfer.status.name == "Cancelled"

    def test_finalised_transfer_amount_edit_rejected(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH amount on a Paid transfer is refused; amount unchanged (#26).

        A finalised transfer's money fields must not be silently
        rewritten through the inline edit -- the user reverts to
        Projected first.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")
            db.session.refresh(xfer)
            assert xfer.status.name == "Paid"

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "999.99", "amount_as_rendered": rendered_transfer_amount(xfer)},
            )
            assert response.status_code == 400
            body = response.data.decode()
            assert "finalised" in body
            assert "transfer" in body

            db.session.refresh(xfer)
            assert transfer_amount(xfer) == Decimal("200.00")

    @pytest.mark.parametrize("fragment", [
        "/transfers/quick-edit/{id}",
        "/transfers/{id}/full-edit",
    ])
    def test_every_transfer_form_posts_the_figure_it_rendered(
        self, app, auth_client, seed_user, seed_periods_today, fragment,
    ):
        """Both transfer forms emit the companion, with the SAME value (**R-JR**).

        **The templates were the ungraded half of this ruling.**  Every other
        case hand-assembles ``amount_as_rendered`` into its payload, so the
        suite would stay green for a template that stopped emitting it -- and
        the two transfer templates are exactly the ones plan step X-au-f must
        rewrite, because they render from the raw ``xfer.amount`` column that
        step empties.

        Asserting the two values are EQUAL is the load-bearing half.  A
        companion rendered from a different expression than the box is a
        difference nobody typed, which the door would read as an authorship and
        act on -- so "both present" is not enough.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            resp = auth_client.get(fragment.format(id=xfer.id))
            assert resp.status_code == 200
            body = resp.data.decode()

            shown = re.search(r'name="amount"[^>]*value="([^"]*)"', body)
            companion = re.search(
                r'name="amount_as_rendered"[^>]*value="([^"]*)"', body,
            )
            assert shown is not None, "the form renders an amount box"
            assert companion is not None, (
                "a form that posts a figure must post what it rendered; "
                "without it the schema refuses every save this form makes"
            )
            assert companion.group(1) == shown.group(1), (
                "the box and its companion must render from ONE expression"
            )

    def test_finalised_lock_still_speaks_for_an_ECHOED_amount(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The lock grades what was SUBMITTED, not what survived authorship.

        Plan step balance:X-au-h drops an ``amount`` no human authored before
        handing the payload to the service, and WHERE that drop happens is a
        decision this case pins. Dropped before the gates, a crafted request
        naming a finalised transfer's amount would stop being refused and start
        being quietly ignored -- the row is unharmed either way, so no other
        assertion in this suite would notice, and a lock that silently stops
        speaking is how the next reader concludes it is not needed.

        The sibling above sends a CHANGED figure, which is authored and so
        survives to the lock however the ordering is written. Only an ECHO
        distinguishes the two orderings, which is why this case exists.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")
            db.session.refresh(xfer)
            assert xfer.status.is_immutable

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "amount": "200.00",
                    "amount_as_rendered": "200.00",
                },
            )

            assert response.status_code == 400, (
                "an echoed amount on a finalised transfer is still a locked "
                "field edit, and the lock must say so rather than ignore it"
            )
            assert "finalised" in response.data.decode()
            db.session.refresh(xfer)
            assert transfer_amount(xfer) == Decimal("200.00")

    def test_finalised_transfer_revert_and_amount_edit_allowed(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Reverting Paid -> Projected AND editing amount in one PATCH is
        allowed -- the escape hatch the lock preserves (#26)."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")
            projected_id = (
                db.session.query(Status).filter_by(name="Projected").one().id
            )

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"status_id": str(projected_id), "amount": "250.00", "amount_as_rendered": rendered_transfer_amount(xfer)},
            )
            assert response.status_code == 200
            db.session.refresh(xfer)
            assert xfer.status_id == projected_id
            assert transfer_amount(xfer) == Decimal("250.00")

    def test_a_LEG_patch_retyping_the_amount_reprices_the_whole_pair(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The grid door onto a transfer's amount re-prices the whole pair.

        **Re-expressed at plan step balance:X-bi-6-1 (ruling R-BAL87).**  It
        was ``PATCH /transactions/<shadow>``, the transaction door that
        re-expressed a request on a SHADOW row as a transfer update; the grid
        draws a transfer's LEG off its parent now and the leg's popover posts
        to the TRANSFER's PATCH with ``leg_account_id``.  The claim stands: a
        figure submitted from the grid is a figure submitted for the transfer,
        both legs take it (ruling **R-IO**: the figure a human types always
        wins), and the response is the LEG's cell, not the transfers page's.

        **This case exists because the door BROKE and nothing noticed.**  Plan
        step X-au-f replaced ``update_transfer``'s ``amount`` +
        ``amount_authored`` pair with one ``AmountOwnership`` (ruling
        **R-BAL11**), and this door was not converted with the other two --
        unknown kwargs are silently ignored, so a user retyping a transfer's
        amount in the grid got a 200 and the figure they started with.  The only
        shadow-amount case in the suite was its FINALISED sibling below, which
        asserts a 400; the success path was graded by nothing, which is why a
        green suite said the door worked.  Found by an adversarial review of
        that step.

        The pair is AD-HOC so the retype is the only thing moving the figure: a
        generated transfer would resolve its definition's series and the case
        could not tell a working door from a silently ignored one.

        The leg is the from-side, where the expense twin this read sat until
        plan step ``balance:X-bi-6-4d-2``, which births none; the twins'
        figures it read then are each side's projection of the transfer
        read here, graded field by field by
        ``test_transfer_legs/test_what_a_side_shows.py::TestBothSidesShowTheTransfer`` (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            xfer_id = xfer.id
            leg_account_id = xfer.from_account_id

            response = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "amount": "377.00",
                    # What the box was RENDERED with, which is what makes this a
                    # RETYPE rather than an echo (ruling R-JR).
                    "amount_as_rendered": rendered_transfer_amount(xfer),
                    "leg_account_id": str(leg_account_id),
                },
            )
            assert response.status_code == 200, response.data
            html = response.data.decode()
            assert f'data-xfer-id="{xfer_id}"' in html
            assert f'data-leg-account-id="{leg_account_id}"' in html
            assert "377" in html

            db.session.expire_all()
            reloaded = db.session.get(Transfer, xfer_id)
            assert transfer_amount(reloaded) == Decimal("377.00")

    def test_finalised_transfer_leg_amount_edit_rejected(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Editing a finalised transfer's amount from its grid LEG is refused.

        **Re-expressed at plan step balance:X-bi-6-1 (ruling R-BAL87)**: the
        SHADOW PATCH this graded is gone, and the grid's door is the transfer
        PATCH carrying ``leg_account_id``.  The lock covers that entry point,
        the refusal renders INTO the leg's cell (so the person who clicked
        sees it), and the parent amount is unchanged (#26), re-read from the
        database.  The leg is the from-side, where the twin this read sat
        until plan step ``balance:X-bi-6-4d-2``, which births none; the twins'
        figures it also read are each side's projection of that amount
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")
            leg_account_id = xfer.from_account_id

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                # Well-formed, so the refusal under test is the FINALISED lock
                # and not the schema declining an unaccompanied figure.
                data={
                    "amount": "999.99",
                    "amount_as_rendered": rendered_transfer_amount(xfer),
                    "leg_account_id": str(leg_account_id),
                },
            )
            assert response.status_code == 400
            html = response.data.decode()
            assert "finalised" in html
            assert f'data-leg-account-id="{leg_account_id}"' in html

            db.session.expire_all()
            assert transfer_amount(
                db.session.get(Transfer, xfer.id),
            ) == Decimal("200.00")

    def test_update_transfer_to_credit_rejected(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH status_id=Credit on a transfer is refused with 400.

        Credit is a transaction-only status: the credit/auto-payback
        workflow refuses transfers, so a Credit transfer would be
        balance-excluded on both accounts with no compensating payback
        -- it would silently vanish from both projections.  The
        transfer-specific transition map closes the hole the shared
        map left open (``TransferUpdateSchema.status_id`` accepts any
        integer).  "Untouched" is the transfer's status, the one status
        since plan step ``balance:X-bi-6-4d-2``, and its two sides holding
        no payment record -- it was the twins' statuses, which no door
        writes since (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            credit_id = (
                db.session.query(Status).filter_by(name="Credit").one().id
            )

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"status_id": str(credit_id)},
            )

            assert response.status_code == 400
            assert "Invalid transfer status transition" in response.data.decode()

            # Parent and both sides untouched -- still Projected, no record.
            # (It also counted two shadows until plan step
            # balance:X-bi-6-4d-2, which births none.)
            db.session.refresh(xfer)
            assert xfer.status.name == "Projected"
            assert all(
                transfer_side_record(db.session, xfer.id, account_id) is None
                for account_id in (xfer.from_account_id, xfer.to_account_id)
            )

    def test_update_transfer_to_received_rejected(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH status_id=Received on a transfer is refused with 400.

        Received is a display convention for regular income rows; the
        transfer service settles both shadows with Done.  Same
        transfer-map hole as the Credit case.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            received_id = (
                db.session.query(Status).filter_by(name="Received").one().id
            )

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"status_id": str(received_id)},
            )

            assert response.status_code == 400
            db.session.refresh(xfer)
            assert xfer.status.name == "Projected"

    def test_leg_patch_to_credit_rejected(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCHing a transfer to Credit from its grid LEG is refused with 400.

        **Re-expressed at plan step balance:X-bi-6-1 (ruling R-BAL87)**: it
        was the SHADOW PATCH, which forwarded any submitted ``status_id`` to
        ``transfer_service.update_transfer`` (before the transfer map split
        this set the parent and BOTH shadows to Credit, silently removing the
        whole transfer from both projections with no payback compensation).
        The grid's door is the transfer PATCH with ``leg_account_id`` now; the
        transfer-specific transition map refuses it there too, and the
        refusal renders into the leg's cell.  "Untouched" is the
        transfer's status and its two sides holding no payment record, not
        the twins' statuses, which no door writes since plan step
        ``balance:X-bi-6-4d-2`` (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).  The leg is the from-side, where the twin this
        read its account off sat until plan step ``balance:X-bi-6-4d-2``,
        which births none (ruling R-BAL167 class 1).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            credit_id = (
                db.session.query(Status).filter_by(name="Credit").one().id
            )
            leg_account_id = xfer.from_account_id

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "status_id": str(credit_id),
                    "leg_account_id": str(leg_account_id),
                },
            )

            assert response.status_code == 400
            html = response.data.decode()
            assert "Invalid transfer status transition" in html
            assert f'data-leg-account-id="{leg_account_id}"' in html

            # Parent and both sides untouched -- still Projected, no record.
            db.session.refresh(xfer)
            assert xfer.status.name == "Projected"
            assert all(
                transfer_side_record(db.session, xfer.id, account_id) is None
                for account_id in (xfer.from_account_id, xfer.to_account_id)
            )

    def test_delete_ad_hoc_transfer(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transfers/instance/<id> hard-deletes an ad-hoc transfer."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            # Ad-hoc transfer (no template).
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template=None)
            xfer_id = xfer.id

            response = auth_client.delete(f"/transfers/instance/{xfer_id}")

            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            # Should be hard-deleted.
            assert db.session.get(Transfer, xfer_id) is None

    def test_delete_template_transfer_soft_deletes(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """DELETE /transfers/instance/<id> soft-deletes a template transfer."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)

            response = auth_client.delete(f"/transfers/instance/{xfer.id}")

            assert response.status_code == 200

            db.session.refresh(xfer)
            assert xfer.is_deleted is True

    def test_template_transfer_override_on_amount_change(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Updating amount on a template transfer sets is_override=True."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)
            assert xfer.is_override is False

            auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "999.00", "amount_as_rendered": rendered_transfer_amount(xfer)},
            )

            db.session.refresh(xfer)
            assert xfer.is_override is True

    def test_cancelled_transfer_contributes_nothing_through_its_legs(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A cancelled transfer's two LEGS are each worth ``$0.00``.

        **It asked the wrong object until plan step X-bx, and finding BAL-465
        recorded this exact line as the occurrence.**  It handed the parent
        :class:`~app.models.transfer.Transfer` to
        ``row_valuation.settled_contribution``, which is a TRANSACTION accessor
        -- a ``Transfer`` defines no ``estimated_amount`` and no
        ``entries`` at all, so the only thing standing between that
        call and an ``AttributeError`` was the Cancelled row short-circuiting on
        ``is_balance_contributing`` before either attribute was read.  It
        therefore passed while proving nothing about production, where no code
        path asks that accessor about a ``Transfer``.

        What a balance actually reads of a transfer is its two legs (transfer
        invariant 5), each carrying the transfer's status -- since plan step
        ``balance:X-bi-6-4d-2`` the ONE status, a twin keeping whatever status
        it was created with -- so each side is asked as its LEG, through the
        leg twin of the same accessor, where it was asked as its shadow
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).  Cancelling
        the transfer is what makes both legs worth nothing.

        Named ``..._through_its_shadows`` until plan step
        ``balance:X-bi-6-4d-2``, which births no shadow; its guard that
        exactly two shadows existed went too.  The loop below runs over the
        transfer's two endpoints, a fixed pair, so it cannot run over
        nothing (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            auth_client.post(f"/transfers/instance/{xfer.id}/cancel")

            db.session.refresh(xfer)
            for account_id in (xfer.from_account_id, xfer.to_account_id):
                leg = transfer_legs.leg_of(xfer, account_id)
                assert settled_contribution(parent_of(leg)) == Decimal("0")

    def test_update_other_users_transfer(self, app, auth_client, seed_user):
        """PATCH /transfers/instance/<id> for another user's transfer returns 404.

        IDOR write-path (HIGH priority): must prove the transfer was not modified.
        """
        with app.app_context():
            other = _create_other_user_with_template()
            target = other["transfer"]
            orig_amount = target.amount
            orig_name = target.name

            response = auth_client.patch(
                f"/transfers/instance/{target.id}",
                data={"amount": "9999.00", "amount_as_rendered": str(orig_amount)},
            )

            assert response.status_code == 404

            # Prove no state change occurred.
            db.session.expire_all()
            db.session.refresh(target)
            assert target.amount == orig_amount, (
                "IDOR attack modified victim's transfer amount!"
            )
            assert target.name == orig_name, (
                "IDOR attack modified victim's transfer name!"
            )


# ── Period move (transfer-period-move follow-up) ──────────────────


class TestTransferPeriodMove:
    """Moving a transfer's pay period from the full-edit popover.

    The transfer service relocates the transfer, and each side is its
    projection, so it moves with it; these tests cover the UI wiring: the
    filtered period selector, the override flag on a template move, the
    gridRefresh trigger, and route-boundary ownership of the submitted
    period id.  A file-local ``_shadows`` read the two shadow transactions'
    periods until plan step ``balance:X-bi-6-4d-2``, which births none
    (ruling R-BAL167 class 4, plan step balance:X-bi-6-4d-2).
    """

    def test_full_edit_renders_filtered_period_selector(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Popover lists current+future periods plus the transfer's own.

        seed_periods_today places today in index 4, so index 0 is past
        (the transfer's own -- included and selected), index 5 is future
        (offered), and index 2 is past and not the transfer's own
        (excluded).  The pay-period <select> is isolated so option-value
        assertions cannot collide with the status select.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            own = seed_periods_today[0]
            future = seed_periods_today[5]
            excluded_past = seed_periods_today[2]

            resp = auth_client.get(f"/transfers/{xfer.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'name="pay_period_id"' in html
            start = html.index('name="pay_period_id"')
            period_select = html[start:html.index("</select>", start)]
            # Labels off the CALENDAR since pay-calendar plan step C4-a-5,
            # which deleted ``PayPeriod.label``: the ``<option>`` renders
            # ``DerivedPeriod.label``.
            calendar = calendar_for(seed_user["user"].id)
            assert calendar.period_by_id(own.id).label in period_select
            assert f'value="{own.id}" selected' in period_select
            assert calendar.period_by_id(future.id).label in period_select
            assert calendar.period_by_id(
                excluded_past.id,
            ).label not in period_select

    def test_move_relocates_the_transfer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A period move relocates the transfer; gridRefresh.

        The response asks for a full grid refresh so the relocated legs
        appear under the new period.  Named
        ``test_move_relocates_transfer_and_both_shadows`` until plan step
        ``balance:X-bi-6-4d-2``, which births no shadow: it read both
        shadows' periods (Transfer Invariant 3), which are each side's
        projection of the transfer's period read here
        (``test_transfer_legs/test_what_a_side_shows.py``'s ``TestBothSidesShowTheTransfer``;
        ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            target = seed_periods_today[5]

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"pay_period_id": target.id, "version_id": xfer.version_id},
            )
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "gridRefresh"

            db.session.expire_all()
            assert db.session.get(Transfer, xfer.id).pay_period_id == target.id

    def test_template_transfer_move_sets_override(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Moving a template-generated transfer flags it is_override.

        Without the flag the recurrence engine would regenerate the
        transfer in its original period, duplicating it.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(
                seed_user, seed_periods_today, savings, template=template,
            )
            assert xfer.is_override is False
            target = seed_periods_today[5]

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"pay_period_id": target.id, "version_id": xfer.version_id},
            )
            assert resp.status_code == 200
            db.session.refresh(xfer)
            assert xfer.pay_period_id == target.id
            assert xfer.is_override is True

    def test_move_to_cross_user_period_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Submitting another user's period id returns 404 and moves nothing.

        Its read of the shadows' periods was VACUOUS from plan step
        ``balance:X-bi-6-4d-2``, which births none: an ``all()`` over an
        empty list.  Dropped; the transfer's own period is the read (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            original_period_id = xfer.pay_period_id
            other = _create_other_user_with_template()
            foreign_period_id = other["transfer"].pay_period_id

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "pay_period_id": foreign_period_id,
                    "version_id": xfer.version_id,
                },
            )
            assert resp.status_code == 404
            db.session.refresh(xfer)
            assert xfer.pay_period_id == original_period_id

    def test_inplace_edit_keeps_balancechanged(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An edit that does not change the period keeps balanceChanged."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "250.00", "amount_as_rendered": rendered_transfer_amount(xfer), "version_id": xfer.version_id},
            )
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "balanceChanged"
            db.session.refresh(xfer)
            assert transfer_amount(xfer) == Decimal("250.00")
