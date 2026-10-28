"""The days typed into a loan payment's popover date each side, and the loan walk reads them.

The module graded the loan walk's refusal of a settled payment whose record carries no day (ruling
**R-BAL147**) and the repair that refusal pointed to, until plan step ``balance:X-bi-6-5``'s third
leaf deleted the refusal: the status-band rule refuses that state at commit, and its own test is
``tests/test_models/test_a_transfer_side_s_record_follows_its_status.py``'s
``test_an_undated_record_under_a_paid_transfer_is_refused``.  Its two cases went with it (developer
answer 2026-10-27, "Delete all 6"); the repair's three are left, and the module's name is its
history.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transfer import Transfer
from app.services.loan_ledger import walk_loan_ledger
from app.services.transfer_legs import transfer_side_leg
from app.utils.dates import display_today
from tests._test_helpers import create_settled_transfer

from ._helpers import (
    _DUE,
    _PERIOD,
    _CLOSED_ON,
    _loan,
)


class TestTheRefusalsRepairWorksThroughTheApp:
    """The loan reads the days typed into the transfer's popover, through its own door.

    Each account's day typed into that account's "Money moved on" box dates
    that side, and a revert then Mark Paid dates the payment to the day of
    the click: the repair the loan walk's refusal of an un-dated settled
    record told the reader of the log to make, until plan step
    ``balance:X-bi-6-5``'s third leaf deleted that refusal.  What those doors
    do to a loan payment is graded here: the PATCH posts the
    popover's two day boxes as that door's own route tests do, and the loan
    walk reads the day each box states.  ``_CLOSED_ON`` (03-05) is the day
    the transfer was marked paid; the suite's frozen clock is the day of the
    click.  The class name is its history.

    **Each case starts from the ordinary Paid payment** since plan step
    ``balance:X-bi-6-4d-2``.  It started from the state the refusal
    describes -- Paid, its loan side's record un-dated -- and asserted the
    walk refused it first; the status-band rule refuses that state at
    commit, and a request reads only what is committed, so no request can
    meet it (developer answer 2026-10-09, "Rewrite from a Paid start": the
    broken-state setup and its error check dropped, every checked day
    unchanged).

    **What Checking's day does when only the loan's box is typed is not the
    message's claim and is not asserted**: today Checking's guessed day
    follows the loan's (ruling R-BAL142), and ruling R-R116 (plan step X-cu)
    reverses that for a loan.  The message asks for EACH account's day, which
    is right under both, and :meth:`test_each_box_typed_dates_its_own_side`
    holds it.
    """

    @staticmethod
    def _paid_payment(seed_user, seed_periods):
        """The $1,000.00 loan payment, Paid on 03-05 through the door."""
        loan = _loan(seed_user)
        transfer = create_settled_transfer(
            seed_user, db.session, seed_user["account"], loan,
            seed_periods[_PERIOD], amount=Decimal("1000.00"),
            settled_on=_CLOSED_ON, due_date=_DUE,
        )
        db.session.commit()
        return loan, transfer.id

    @staticmethod
    def _days(loan, transfer_id, scenario_id):
        """Return (the loan walk's day for the payment, the source side's day)."""
        db.session.expire_all()
        [outcome] = walk_loan_ledger(loan.id, scenario_id).settled_splits
        source = transfer_side_leg(
            db.session.get(Transfer, transfer_id), is_income=False,
        )
        return outcome.visible_on, source.settled_on

    def test_a_day_typed_into_the_loan_box_dates_the_payment(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Only the loan's box typed, 03-09: the walk dates the payment 03-09."""
        with app.app_context():
            loan, transfer_id = self._paid_payment(seed_user, seed_periods)
            typed = date(2026, 3, 9)

            response = auth_client.patch(
                f"/transfers/instance/{transfer_id}",
                data={"settled_on_from": "", "settled_on_to": typed.isoformat()},
            )
            assert response.status_code == 200, response.get_data(
                as_text=True,
            )[:300]

            visible_on, _ = self._days(
                loan, transfer_id, seed_user["scenario"].id,
            )
            assert visible_on == typed

    def test_each_box_typed_dates_its_own_side(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Checking's box 03-05, the loan's 03-09: each side keeps the day typed into its box."""
        with app.app_context():
            loan, transfer_id = self._paid_payment(seed_user, seed_periods)
            typed = date(2026, 3, 9)

            response = auth_client.patch(
                f"/transfers/instance/{transfer_id}",
                data={
                    "settled_on_from": _CLOSED_ON.isoformat(),
                    "settled_on_to": typed.isoformat(),
                },
            )
            assert response.status_code == 200, response.get_data(
                as_text=True,
            )[:300]

            assert self._days(
                loan, transfer_id, seed_user["scenario"].id,
            ) == (typed, _CLOSED_ON)

    def test_a_revert_then_mark_paid_dates_it_to_the_day_of_the_click(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Reverted, then Mark Paid: the walk and both sides read the click's day."""
        with app.app_context():
            loan, transfer_id = self._paid_payment(seed_user, seed_periods)

            reverted = auth_client.patch(
                f"/transfers/instance/{transfer_id}",
                data={
                    "status_id": str(ref_cache.status_id(StatusEnum.PROJECTED)),
                },
            )
            assert reverted.status_code == 200, reverted.get_data(
                as_text=True,
            )[:300]
            paid = auth_client.post(
                f"/transfers/instance/{transfer_id}/mark-done",
            )
            assert paid.status_code == 200, paid.get_data(as_text=True)[:300]

            clicked = display_today()
            assert clicked != _CLOSED_ON, "the click must fall on another day"
            assert self._days(
                loan, transfer_id, seed_user["scenario"].id,
            ) == (clicked, clicked)
