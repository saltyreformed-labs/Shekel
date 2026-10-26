"""A transfer's due date and each side's settle day, as the create and edit doors write them, and
the days a transfer refuses (a day on an unsettled transfer, a settled transfer's day cleared).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.ref import Status
from app.services import transfer_service
from app.utils.dates import display_today
from app.exceptions import ValidationError
from tests._test_helpers import an_entered_day, transfer_side_record, transfer_side_settle_day
from app.models.amount_ownership import AmountOwnership

from ._helpers import _create_basic_transfer, _reread


class TestDueDateAndSettleDayShadows:
    """A transfer's due date and each side's settle day, as the doors write them.

    The due date is a column of the transfer's row, read re-read from the
    database; each side's settle day is its payment record's, read by its
    own SQL (:func:`tests._test_helpers.transfer_side_record`).  What a side
    shows of the row's due date is ``test_transfer_legs/test_what_a_side_shows.py``'s
    ``TestBothSidesShowTheTransfer``, both sides.  Until plan step
    ``balance:X-bi-6-4d-2`` each due-date case also read the transfer's two
    shadow rows, which that step's create door no longer writes (ruling
    R-BAL290 part 1).  The name is kept as history.
    """

    def test_a_due_date_at_create_is_written_on_the_transfer(
        self, app, transfer_data,
    ):
        """create_transfer with a due date writes it on the transfer's row.

        Read off the row re-read from the database since plan step
        ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-2b, where it read the
        object the door returned and both shadow rows' due dates (ruling
        R-BAL290 part 1).  Renamed from ``test_shadow_due_date_propagation``.
        """
        with app.app_context():
            td = transfer_data
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("250.00")),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                    due_date=date(2026, 1, 15),
                ),
            )

            assert _reread(xfer.id).due_date == date(2026, 1, 15)

    def test_no_due_date_at_create_leaves_the_transfer_undated(
        self, app, transfer_data,
    ):
        """create_transfer with no due date leaves the transfer's due date empty.

        Read off the row re-read from the database since plan step
        ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-2b, where it read the
        object the door returned and both shadow rows' due dates (ruling
        R-BAL290 part 1); a create that dated an undated transfer fails it
        (measured there).  Renamed from
        ``test_shadow_due_date_null_propagation``.
        """
        with app.app_context():
            td = transfer_data
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("250.00")),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                    due_date=None,
                ),
            )

            assert _reread(xfer.id).due_date is None

    def test_a_due_date_edit_writes_the_transfers_due_date(
        self, app, transfer_data,
    ):
        """An edit naming a due date writes it on the transfer's row.

        The transfer is created with none, so the edit moves it.  Read off
        the row re-read from the database since plan step
        ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-2b, where it read the
        object in the session and both shadow rows' due dates (ruling
        R-BAL290 part 1).  Renamed from ``test_shadow_due_date_update``.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            assert xfer.due_date is None, "precondition: created undated"

            transfer_service.update_transfer(
                xfer.id, td["user"].id, due_date=date(2026, 2, 1)
            )

            assert _reread(xfer.id).due_date == date(2026, 2, 1)

    def test_a_settle_day_correction_lands_on_both_shadows(
        self, app, db, transfer_data,
    ):
        """A day corrected on ONE side reaches both sides: the other borrows it.

        The correction door (ruling **R-ED**, per side since plan step
        ``balance:X-bi-6-4c-3``, ruling **R-BAL142**): the user read their
        statement and the money left the source account on a day other than
        the one Paid was pressed on.  They correct that side; the other side
        holds no day of its own -- a Paid press states none -- so it BORROWS
        the corrected day, and both sides' payment records land on it.  Since
        plan step ``balance:X-bi-6-3`` the posting writer files each side's
        entry under that side's own covering movement's day, so the two land
        together.  Each side's day is its RECORD's since plan step
        ``balance:X-bi-6-4d-2``; the shadows carried it until then (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).  The name is kept
        as history.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            db.session.flush()
            done_status = db.session.query(Status).filter_by(name="Paid").one()
            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done_status.id,
            )
            db.session.flush()

            corrected = display_today() - timedelta(days=3)
            transfer_service.update_transfer(
                xfer.id, td["user"].id,
                side_days=(transfer_service.SideDay(
                    xfer.from_account_id, an_entered_day(corrected),
                ),),
            )
            db.session.flush()

            for account_id in (xfer.from_account_id, xfer.to_account_id):
                assert transfer_side_record(
                    db.session, xfer.id, account_id,
                ).settled_on == corrected

    def test_a_day_on_an_UNSETTLED_transfer_is_refused(
        self, app, db, transfer_data,
    ):
        """Dating a Projected transfer raises instead of recording it.

        **This test asserted the OPPOSITE until plan step X-f1b** -- it settled
        a Projected transfer's shadows with an instant and checked the column
        was non-NULL.  That is finding **N-183**: ``update_transfer`` assigned
        the column directly, so it could date a row no money had moved for,
        breaking the settled-iff-dated invariant this step establishes and
        leaving a fold that would read a settle day off an unsettled row.  The
        write goes through the status seam now, which refuses it.

        It also asked the transfer's shadow rows for a day after the refusal
        until plan step ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-2b.  From
        that step's create door no row can name a transfer
        (``ck_transactions_names_no_transfer``), so the loop ran over none
        (a vacuous read, structurally empty), and it is dropped, not moved
        onto each side's record: with the three refusals the update asks
        deleted (its own before the first write, the transfer status
        writer's and the status seam's), the update raises nothing, writes
        no record or day on either side and leaves the transfer Projected,
        so this case fails first at the raise; with its own refusal alone
        deleted, the transfer status writer's refuses (all measured at that
        checkpoint).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            db.session.flush()

            with pytest.raises(ValidationError) as exc:
                transfer_service.update_transfer(
                    xfer.id, td["user"].id,
                    side_days=(transfer_service.SideDay(
                        xfer.from_account_id, an_entered_day(display_today()),
                    ),),
                )
            assert "not a settled status" in str(exc.value)

    def test_a_settled_transfer_refuses_to_have_its_day_CLEARED(
        self, app, db, auth_client, transfer_data,
    ):
        """Clearing a settled transfer's day cannot be asked; reverting is the way.

        The other half of finding **N-183**.  A settled row with no day is the
        state ``balance_predicates.settled_day`` REFUSES, so letting an edit
        produce one would turn a form submission into a 500 on every balance
        surface that folds the row.  The service refused ``settle_day=None``
        with a ``ValidationError`` ("cannot be cleared") until plan step
        ``balance:X-bi-6-4c-3``; since then a door states a day PER SIDE as a
        :class:`~app.services.transfer_service.SideDay`, which cannot wrap
        ``None``, so the request has no spelling and the refusal moved to the
        value's construction.  The legitimate way to remove the day is to move
        the transfer out of the settled band, which the seam does as part of
        the status change -- asserted below so the refusal is not mistaken for
        "the day can never be removed".  Each side's day is its payment
        RECORD's since plan step ``balance:X-bi-6-4d-2``; the shadows carried
        it until then (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).

        **Erasing both day boxes in the popover asks nothing either** (ruling
        **R-BAL342**, the developer, 2026-10-20, finding **BAL-581**): a save
        through the popover's door, ``PATCH /transfers/instance/<id>``, with
        both boxes posted empty and the Status select still on Paid, answers
        200 and leaves each side's record on its day, still known as the day
        the owner typed.  It replaced a re-read of both records after the
        ``SideDay`` refusal, which wrote nothing, so the re-read could not
        fail.  Each side's day is typed three days back first, so both boxes
        render prefilled
        (:func:`~app.routes._render_helpers.transfer_side_boxes`) and the
        erase erases a day the owner can see.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            db.session.flush()
            done_status = db.session.query(Status).filter_by(name="Paid").one()
            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done_status.id,
            )
            db.session.flush()
            sides = (xfer.from_account_id, xfer.to_account_id)

            with pytest.raises(ValueError) as exc:
                transfer_service.SideDay(xfer.from_account_id, None)
            assert "cannot wrap None" in str(exc.value)

            typed = display_today() - timedelta(days=3)
            transfer_service.update_transfer(
                xfer.id, td["user"].id,
                side_days=tuple(
                    transfer_service.SideDay(account_id, an_entered_day(typed))
                    for account_id in sides
                ),
            )
            version = _reread(xfer.id).version_id

            erased = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "status_id": str(done_status.id),
                    "settled_on_from": "",
                    "settled_on_to": "",
                    "version_id": str(version),
                },
            )

            assert erased.status_code == 200, erased.get_data(as_text=True)
            db.session.expire_all()
            for account_id in sides:
                assert transfer_side_settle_day(
                    db.session, xfer.id, account_id,
                ) == an_entered_day(typed), (
                    f"the erased boxes moved the day on account {account_id}"
                )

            # And the supported route out clears it on both sides.
            transfer_service.update_transfer(
                xfer.id, td["user"].id,
                status_id=td["projected_status"].id,
            )
            db.session.flush()
            for account_id in sides:
                assert transfer_side_settle_day(
                    db.session, xfer.id, account_id,
                ) is None

    def test_an_undated_projected_transfer_stays_undated(
        self, app, db, auth_client, transfer_data,
    ):
        """Empty day boxes on a PROJECTED transfer are a no-op: it stays Projected.

        A no-op rather than a refusal: an empty box states no day, which
        agrees with the row's state (no money has moved, so there is no day),
        and refusing an edit that asks for what is already true would reject
        a save for saying nothing.  The popover renders no day box on a
        Projected transfer
        (``test_full_edit_offers_the_correction_only_on_a_settled_transfer``),
        so the popover never posts the two boxes here; a hand-built request
        does.

        **Asked through the popover's door since ruling R-BAL342** (the
        developer, 2026-10-20, finding **BAL-581**): ``PATCH
        /transfers/instance/<id>`` with both boxes posted empty and the Status
        select on Projected answers 200, the transfer re-read from the
        database is still Projected, and neither side holds a record.  It
        called the service with ``side_days=()``, the same call as stating
        nothing, so the case could not tell an empty statement from none.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id
            xfer = _reread(xfer_id)

            empty = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "status_id": str(td["projected_status"].id),
                    "settled_on_from": "",
                    "settled_on_to": "",
                    "version_id": str(xfer.version_id),
                },
            )

            assert empty.status_code == 200, empty.get_data(as_text=True)
            xfer = _reread(xfer_id)
            assert xfer.status_id == td["projected_status"].id
            for account_id in (xfer.from_account_id, xfer.to_account_id):
                assert transfer_side_record(
                    db.session, xfer_id, account_id,
                ) is None, f"the empty boxes recorded a side on {account_id}"
