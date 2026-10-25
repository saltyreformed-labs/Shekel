"""A settled payment whose record carries no day is refused and named, never dated by a guess, and
the repair the refusal points to works through the app: the days typed into the transfer's
popover date each side.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import UndatedSettleError
from app.extensions import db
from app.models.transfer import Transfer
from app.services.loan_ledger import _visible
from app.services.loan_ledger import walk_loan_ledger
from app.services.transfer_legs import transfer_side_leg
from app.utils.dates import display_today
from tests._test_helpers import (
    create_savings_account,
    create_settled_transfer,
    create_transfer,
    refused_by_database_rule,
)

from ._helpers import (
    _DUE,
    _PERIOD,
    _CLOSED_ON,
    _loan,
    _SETTLED_UNDATED,
    _stage_an_undated_loan_side,
)


class TestASettledPaymentWithAnUndatedRecordFailsLoud:
    """A settled transfer whose record carries no day is REFUSED, never dated by a guess."""

    def test_a_paid_transfer_over_a_reverted_twin_refuses_and_names_the_transfer(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Drift B over a kept movement: the walk refuses, naming the TRANSFER.

        The transfer says Paid, and the record its leg carries -- the kept
        covering movement -- carries no day.  Read off the transfer, that is
        the broken settled-without-a-day state the refusal exists for: a day
        guessed here would put money on a day nothing recorded, and the
        R-BAL139 installment day is for a payment that moved NOTHING, not
        for one whose movement lost its day.  The refusal names the payment as
        its owner finds it in the app -- the transfer's own name (its
        popover's title) and its two accounts, the figure, the paycheck and
        the due date -- with the
        transfer's and the movement's ids, because ruling R-BAL147's "you fix
        the row through the app" is the TRANSFER once the movement re-parents.
        It named the row the movement hangs off, the twin, whose own status is
        Projected, until leaf balance:X-bi-6-4d-1 (a re-expression under
        R-BAL167 class 1).  Until plan step balance:X-bi-6-4b the twin's
        status kept this payment out of the loan entirely.

        Two unrelated transfers are built FIRST, which is how this case made
        the transfer's and the movement's ids differ while each table counted
        from 1 in a fresh database; a message naming the twin's id where the
        movement's belongs passed the leaf's first version of this case.  The
        test database now numbers each table from a range of its own (ruling
        ``balance:R-BAL356``), so the two ids differ without the decoys.  The
        twin's id joined the check until plan step ``balance:X-bi-6-4d-2``
        made a transfer one row
        (ruling **R-BAL258**; ruling **R-BAL167** class 4, through the
        helper :func:`_stage_an_undated_loan_side`).

        **Staged inside the save since plan step ``balance:X-bi-6-4d-2``**
        (ruling **R-BAL167** class 2): the movement is the loan side's record
        on the transfer (ruling **R-BAL88**), and the status-band rule
        refuses a settled transfer holding an un-dated one at COMMIT, so the
        state exists only inside the save that writes it -- where the walk is
        asked, and refuses, as before -- and the commit is then asserted
        refused.
        """
        with app.app_context():
            loan = _loan(seed_user)
            decoy = create_savings_account(
                seed_user, db.session, "Decoy Savings", Decimal("0.00"),
            )
            for amount in ("10.00", "20.00"):
                create_transfer(
                    seed_user, db.session, seed_user["account"], decoy,
                    seed_periods[_PERIOD], amount=Decimal(amount),
                )
            db.session.commit()
            transfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"),
                settled_on=_CLOSED_ON, due_date=_DUE,
            )
            db.session.commit()
            movement = _stage_an_undated_loan_side(transfer, loan)
            assert transfer.id != movement.id, (
                "the two ids must differ for the message to grade which one "
                f"it names: transfer {transfer.id}, movement {movement.id}"
            )

            with pytest.raises(UndatedSettleError) as refused:
                walk_loan_ledger(loan.id, seed_user["scenario"].id)
            message = str(refused.value)
            assert message.startswith(
                f"The loan-side payment (movement {movement.id}) of transfer "
                f"{transfer.id} \"{transfer.name}\" (Checking to Settled Leg "
                f"Loan, $1,000.00 in the paycheck of "
                f"{seed_periods[_PERIOD].start_date}, due {_DUE}) is in a "
                "settled status but carries no settled_on"
            ), message
            assert transfer.name == "Checking to Settled Leg Loan", (
                "the create door's default name, the popover's title"
            )
            assert "\"Money moved on\" box" in message, message
            transfer_id = transfer.id

            with refused_by_database_rule(_SETTLED_UNDATED) as caught:
                db.session.commit()
            db.session.rollback()
            assert f"transfer {transfer_id} " in str(caught.value)

    def test_a_dated_payment_never_builds_the_refusals_words(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):  # pylint: disable=unused-argument
        """The walk over a payment WITH its day never composes the refusal's subject.

        The subject reads the transfer's relationships, and the walk asks
        ``payment_visible_on`` for every settled payment it folds, so it is
        built only when the refusal fires
        (:func:`~app.utils.balance_predicates.require_settled_day`).  The
        builder is replaced by one that fails the case if called.
        """
        def never(_leg):
            raise AssertionError("the refusal's subject was built on a dated payment")

        monkeypatch.setattr(_visible, "_undated_payment_subject", never)
        with app.app_context():
            loan = _loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"),
                settled_on=_CLOSED_ON, due_date=_DUE,
            )
            db.session.commit()

            [outcome] = walk_loan_ledger(
                loan.id, seed_user["scenario"].id,
            ).settled_splits
            assert outcome.visible_on == _CLOSED_ON


class TestTheRefusalsRepairWorksThroughTheApp:
    """The loan reads the days typed into the transfer's popover, through its own door.

    ``_visible._UNDATED_PAYMENT_CAUSE`` tells the reader of the log to type
    each account's day into that account's "Money moved on" box, and that a
    revert then Mark Paid would date the payment to the day of the click.
    What those doors do to a loan payment is graded here: the PATCH posts the
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
    unchanged).  The refusal's message is graded inside the save that stages
    the state, in :class:`TestASettledPaymentWithAnUndatedRecordFailsLoud`.

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
