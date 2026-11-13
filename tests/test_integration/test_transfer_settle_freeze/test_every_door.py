"""Every door reaches the same figure."""

from app import ref_cache
from app.enums import StatusEnum
from app.services.pay_calendar import FiledRow, calendar_for
from tests._test_helpers import transfer_side_figure, transfer_side_record
from tests.test_integration.test_loan_transfer_live_amount._helpers import _side_leg
from app.services.row_valuation import settled_contribution
from app.services.transfer_legs import parent_of
from tests._test_helpers import rendered_transfer_amount

from ._helpers import _LIVE_PITI, _derived_loan_transfer, _sides


class TestEveryDoorReachesTheSameFigure:
    """The census, from the doors themselves -- three of four used to differ."""

    def test_the_transfers_page_mark_done_freezes(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """``POST /transfers/instance/<id>/mark-done``, which did NOT freeze.

        The door that made this a defect rather than a design: it sent
        ``status_id`` alone, so an auto-derived loan payment settled at its
        creation-time escrow here and at the live figure from the grid.  Each
        side is read as the transfer and its side's record, not its twin row
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id = xfer.id

        response = auth_client.post(f"/transfers/instance/{xfer_id}/mark-done")
        assert response.status_code == 200

        with app.app_context():
            for account_id in _sides(xfer_id):
                assert settled_contribution(
                    parent_of(_side_leg(xfer_id, account_id)),
                ) == _LIVE_PITI
                assert transfer_side_figure(
                    db.session, xfer_id, account_id,
                ) == _LIVE_PITI

    def test_the_grid_leg_mark_done_still_freezes(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """The grid's Mark Paid on a transfer LEG, the door that always froze.

        **Re-expressed at plan step balance:X-bi-6-1 (ruling R-BAL87)**: it
        was ``POST /transactions/<shadow>/mark-done``, deleted with the shadow
        branch; the grid's Mark Paid posts to the transfer's own door with
        ``leg_account_id`` now.  The control for the move: the rule left the
        route for the service, so the figure it books must be unchanged.  Each
        side is read as the transfer and its side's record, not its twin row
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).  The leg
        pressed is the transfer's from-side, where it was the expense twin's
        account until plan step balance:X-bi-6-4d-2 stopped the twin being
        born -- the same account (ruling R-BAL167 class 1).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id, leg_account_id = xfer.id, xfer.from_account_id

        response = auth_client.post(
            f"/transfers/instance/{xfer_id}/mark-done",
            data={"leg_account_id": str(leg_account_id)},
        )
        assert response.status_code == 200
        assert f'data-leg-account-id="{leg_account_id}"' in response.data.decode()

        with app.app_context():
            for account_id in _sides(xfer_id):
                assert settled_contribution(
                    parent_of(_side_leg(xfer_id, account_id)),
                ) == _LIVE_PITI
                assert transfer_side_figure(
                    db.session, xfer_id, account_id,
                ) == _LIVE_PITI

    def test_the_transfer_full_edit_status_dropdown_freezes(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """``PATCH /transfers/instance/<id>`` with the payload the FORM sends.

        The third door, and the one whose transaction twin is finding
        **N-219**: it flipped the status through the service without ever
        asking what the payment was worth.

        **It posts ``amount`` because the real form always does**, and that is
        the whole point of this case rather than a detail.  An earlier version
        submitted ``status_id`` alone, which no browser can produce:
        ``_transfer_full_edit.html`` renders the Amount input on every editable
        row and an HTML form posts every input it renders.  Against that
        realistic payload the door did NOT freeze -- the route set
        ``is_override`` on the mere PRESENCE of the field, which tells the
        settle "the operator owns this amount" and suppresses the derivation.
        Measured before the fix: `$1.00` of cash booked against a `$1,000.00`
        interest + `$300.00` escrow split.

        **Since plan step X-au-h the route asks whether a HUMAN AUTHORED the
        figure, not whether it MOVED** (ruling R-JR): the form posts the figure
        it rendered, and an echo is one that comes back equal to it.  The
        payload below carries that companion, which is what makes this an echo
        rather than a retype -- and the door drops an unauthored figure rather
        than forwarding it, so the service never sees one.  The paragraph that
        described the old moved-against-the-stored-column test is gone with the
        comparison it named.  Each side is read as the transfer and its side's
        record, not its twin row (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id = xfer.id
            version = xfer.version_id
            # Exactly what the rendered form carries back: the value already in
            # the box, unchanged, beside the status the user did change.
            rendered_amount = rendered_transfer_amount(xfer)
            period_id = xfer.pay_period_id

        response = auth_client.patch(
            f"/transfers/instance/{xfer_id}",
            data={
                "amount": rendered_amount,
                # What the box was RENDERED with (ruling R-JR, plan step
                # X-au-h).  Equal to the figure above, which is what makes this
                # an ECHO rather than a retype -- and the echo is the whole
                # subject of this case.  Omitting it would land the save on the
                # fail-closed arm, raise the flag, suppress the freeze, and
                # reproduce the very defect the case exists to prevent.
                "amount_as_rendered": rendered_amount,
                "pay_period_id": period_id,
                # **No ``due_date``, and its absence is what a browser sends.**
                # The popover renders a GENERATED transfer's due date as TEXT
                # since plan step X-au-f (finding **BAL-476**): the date is its
                # definition's and it is now what prices the row, so the form
                # offers no input and the route REFUSES the field
                # (``_reject_generated_due_date_edit``).  Posting it here would
                # be testing a request no form can produce, against a gate that
                # correctly answers 400.
                "status_id": ref_cache.status_id(StatusEnum.DONE),
                "version_id": version,
            },
        )
        assert response.status_code == 200, response.data

        with app.app_context():
            for account_id in _sides(xfer_id):
                assert settled_contribution(
                    parent_of(_side_leg(xfer_id, account_id)),
                ) == _LIVE_PITI
                assert transfer_side_figure(
                    db.session, xfer_id, account_id,
                ) == _LIVE_PITI

    def test_a_transfer_PATCH_from_a_grid_leg_freezes(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """The transfer PATCH asked from a grid LEG -- the FOURTH door.

        **Re-expressed at plan step balance:X-bi-6-1 (ruling R-BAL87)**: it
        was ``PATCH /transactions/<shadow id>``, branched to
        ``_shadow_mutations._apply_shadow_update``, which mapped the submitted
        transaction fields onto transfer-service kwargs.  That module is
        deleted; the leg's popover posts the transfer PATCH with
        ``leg_account_id`` (its Status dropdown is how a grid user settles a
        transfer from the form), so a settling ``status_id`` arriving here is
        a settle that must freeze like any other.  Without this case the
        census is a claim in a docstring: three doors graded, one asserted.
        Each side is read as the transfer and its side's record, not its twin
        row (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).  The leg
        asked from is the transfer's from-side, where it was the expense
        twin's account until plan step balance:X-bi-6-4d-2 stopped the twin
        being born -- the same account (ruling R-BAL167 class 1).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id, leg_account_id = xfer.id, xfer.from_account_id
            version = xfer.version_id

        response = auth_client.patch(
            f"/transfers/instance/{xfer_id}",
            data={
                "status_id": ref_cache.status_id(StatusEnum.DONE),
                "version_id": version,
                "leg_account_id": str(leg_account_id),
            },
        )
        assert response.status_code == 200, response.data
        assert f'data-leg-account-id="{leg_account_id}"' in response.data.decode()

        with app.app_context():
            for account_id in _sides(xfer_id):
                assert settled_contribution(
                    parent_of(_side_leg(xfer_id, account_id)),
                ) == _LIVE_PITI
                assert transfer_side_figure(
                    db.session, xfer_id, account_id,
                ) == _LIVE_PITI

    def test_the_reconcile_panels_tick_freezes_and_dates_by_the_STATEMENT(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """The FIFTH door, which plan step X-f2-c3 opens.

        It is the only one that knows a day, so it grades the two rules
        together: the freeze books the live figure, and both sides' records
        date the money as having moved on the day the STATEMENT covers rather
        than the day the operator got round to reconciling.  Each side is read
        as the transfer and its side's record, not its twin row (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).  The landing day is
        read off the TRANSFER's own period and due date, where it was read off
        the expense twin's until plan step balance:X-bi-6-4d-2 stopped the twin
        being born; the twin copied both from the transfer, so the day is the
        same (ruling R-BAL167 class 1).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id = xfer.id
            account_id = xfer.from_account_id
            # The day the panel measures the offer against is the row's own
            # LANDING day, not its period's start: a loan payment carries a
            # due date, so the two differ and a statement asserted before the
            # landing day would (correctly) offer nothing.
            period = calendar_for(seed_user["user"].id).require_period(
                FiledRow.for_row(xfer),
            )
            observed = period.attribution_day(xfer.due_date)

        response = auth_client.patch(
            f"/accounts/{account_id}/true-up",
            data={
                "anchor_balance": "4537.66",
                "observed_on": observed.isoformat(),
            },
        )
        assert response.status_code == 200, response.data

        response = auth_client.post(
            f"/accounts/{account_id}/reconcile",
            data={"transfer_ids": [str(xfer_id)]},
        )
        assert response.status_code == 200, response.data

        with app.app_context():
            for side_account_id in _sides(xfer_id):
                assert settled_contribution(
                    parent_of(_side_leg(xfer_id, side_account_id)),
                ) == _LIVE_PITI
                assert transfer_side_figure(
                    db.session, xfer_id, side_account_id,
                ) == _LIVE_PITI
                assert transfer_side_record(
                    db.session, xfer_id, side_account_id,
                ).settled_on == observed
