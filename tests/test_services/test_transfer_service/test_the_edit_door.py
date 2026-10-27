"""The edit door, transfer_service.update_transfer: what each edit writes on the transfer's row
(amount, paid, period, category, notes, override, settle day), the figure an unsettled transfer
refuses and a revert clears, and the edits it refuses (another user's, a missing or a hidden
transfer, a bad amount, another user's period).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.models.ref import Status
from app.services import transfer_service
from app.utils.dates import display_today
from app.exceptions import NotFoundError, ValidationError
from tests._test_helpers import (
    an_entered_day,
    on_both_sides,
    record_paydays_across_a_hole,
    rhythm_of,
    transfer_side_figure,
    transfer_side_record,
    transfer_side_settle_day,
    typed,
)
from app.models.amount_ownership import AmountOwnership

from ._helpers import _create_basic_transfer, _reread


# ── Update Tests ───────────────────────────────────────────────────


class TestUpdateTransfer:
    """Tests for transfer_service.update_transfer."""

    def test_an_amount_edit_writes_the_transfers_amount(self, app, transfer_data):
        """An amount edit writes the new amount on the transfer's row.

        What the edit wrote, re-read from the database (ruling R-BAL290
        part 1).  It also priced both legs until plan step
        ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-1b-1; what a side shows of
        the row's amount is ``test_transfer_legs/test_what_a_side_shows.py``'s
        ``TestBothSidesShowTheTransfer`` now.  The one copy of a pair: the
        invariant group's ``test_amounts_always_match_after_update`` made the
        same edit at $777.77 and was deleted there.  Renamed from
        ``test_an_amount_edit_reprices_both_legs``, and before that
        ``test_amount_syncs_shadows``.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id

            transfer_service.update_transfer(
                xfer_id, td["user"].id,
                amount_ownership=AmountOwnership.own(Decimal("400.00")),
            )

            assert _reread(xfer_id).amount == Decimal("400.00")

    def test_a_paid_edit_settles_the_transfer_and_dates_both_sides_records(
        self, app, db, transfer_data,
    ):
        """Marking Paid writes Paid on the transfer's row and a dated record on each side.

        The transfer's status is the one status; each side's payment record
        is the one thing still stored per side, read by its own SQL
        (:func:`tests._test_helpers.transfer_side_settle_day`), where each
        shadow said Paid until plan step ``balance:X-bi-6-4d-2`` (ruling
        R-BAL167 class 1).  The row is re-read from the database since that
        step's checkpoint 2e-2b-1b-1 (ruling R-BAL290 part 1).  The one copy
        of a pair: the invariant group's line-for-line twin,
        ``test_statuses_always_match_after_update``, was deleted there.
        Renamed from ``test_status_syncs_shadows``.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            sides = (xfer.from_account_id, xfer.to_account_id)
            paid_id = ref_cache.status_id(StatusEnum.DONE)

            transfer_service.update_transfer(
                xfer_id, td["user"].id, status_id=paid_id,
            )

            assert _reread(xfer_id).status_id == paid_id
            for account_id in sides:
                assert transfer_side_settle_day(
                    db.session, xfer_id, account_id,
                ) is not None

    def test_a_period_edit_writes_the_transfers_period(self, app, transfer_data):
        """A period edit writes the new period on the transfer's row.

        What the edit wrote, re-read from the database (ruling R-BAL290
        part 1).  It also read both legs' periods until plan step
        ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-1b-1; what a side shows of
        the row's period is ``test_transfer_legs/test_what_a_side_shows.py``'s
        ``TestBothSidesShowTheTransfer`` now.  The one copy of a pair: the
        invariant group's ``test_periods_always_match_after_update`` made the
        same edit to the fourth period and was deleted there.  Renamed from
        ``test_a_period_edit_moves_both_legs``, and before that
        ``test_period_syncs_shadows``.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id
            new_period = td["periods"][2]

            transfer_service.update_transfer(
                xfer_id, td["user"].id, pay_period_id=new_period.id,
            )

            assert _reread(xfer_id).pay_period_id == new_period.id

    def test_a_category_edit_files_the_transfer_under_it(self, app, transfer_data):
        """A category edit writes the new category on the transfer's row.

        It read the two shadow rows' categories until plan step
        ``balance:X-bi-6-4d-2``, whose create door stopped writing them
        (ruling R-BAL167 class 1).  **Its edit moved nothing**: the transfer
        is created under Rent and the edit named Rent, a no-op in the shadow
        version too; re-pointed onto the transfer and its legs, it PASSED with
        the edit's category arm deleted (measured at checkpoint 2e-2a-2).  The edit
        names Groceries now, a category the transfer is not under (the
        coordinator's ruling, 2026-10-13).  It also read both legs'
        categories until that step's checkpoint 2e-2b-1b-1, which re-reads
        the row from the database instead (ruling R-BAL290 part 1); what a
        side shows of the row's category is ``test_transfer_legs/test_what_a_side_shows.py``'s
        ``TestBothSidesShowTheTransfer``.  Renamed from
        ``test_a_category_edit_files_the_transfer_and_both_legs_under_it``,
        and before that ``test_category_updates_both_shadows``.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            groceries_id = td["categories"]["Groceries"].id
            # A guard on the INPUT, not a read: the edit must move the
            # category, or the case grades nothing it names.
            assert xfer.category_id != groceries_id

            transfer_service.update_transfer(
                xfer.id, td["user"].id, category_id=groceries_id,
            )

            assert _reread(xfer.id).category_id == groceries_id

    def test_a_notes_edit_writes_the_notes_and_leaves_the_amount(
        self, app, transfer_data,
    ):
        """A notes edit writes the transfer's notes and leaves its amount as it was.

        It compared the two shadow rows' amounts before and after the edit
        until plan step ``balance:X-bi-6-4d-2``, and from that step's create
        door on both reads ran over none (a vacuous read, measured at its
        checkpoint 2e-2a-2).  It then priced both legs before and after the
        edit until that step's checkpoint 2e-2b-1b-1, which reads the row's
        amount, re-read from the database (ruling R-BAL290 part 1): a side's
        price is the row's amount, which ``test_transfer_legs/test_what_a_side_shows.py``'s
        ``TestBothSidesShowTheTransfer`` grades.  Renamed from
        ``test_a_notes_edit_leaves_both_legs_priced_as_they_were``, and
        before that ``test_notes_does_not_touch_shadows``.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id

            transfer_service.update_transfer(
                xfer_id, td["user"].id, notes="Updated notes",
            )

            row = _reread(xfer_id)
            assert (row.notes, row.amount) == ("Updated notes", Decimal("250.00"))

    def test_a_figure_on_an_UNSETTLED_transfer_is_REFUSED(self, app, db, transfer_data):
        """A figure records a settle, so an unsettled pair cannot be handed one.

        **This asserted the opposite until plan step X-au-c3** -- an
        ``actual_amount`` handed to ``update_transfer`` was written onto both
        shadows whatever their status.  A figure now RECORDS what moved, and
        the seam keeps one off a row whose money has not
        (``reject_settlement_without_settled_status``); the service refuses
        the request with a designed 400 before any write.

        No form can produce it: the correction box renders only on a settled
        row.  To record a figure, settle the transfer -- the same act that
        records one.  Stated here rather than quietly rewritten, because it is a
        BEHAVIOUR change and belongs in the commit message.

        "Before any write" is read off each SIDE's payment record since plan
        step ``balance:X-bi-6-4d-2``, where it asked each of the two shadow
        rows, which that step's create door no longer writes (ruling R-BAL167
        class 1).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            with pytest.raises(ValidationError, match="is not settling"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, figure=typed(Decimal("245.00"))
                )

            for account_id in (xfer.from_account_id, xfer.to_account_id):
                assert transfer_side_record(
                    db.session, xfer.id, account_id,
                ) is None

    def test_an_override_edit_flags_the_transfer(self, app, transfer_data):
        """An override edit writes the flag on the transfer's row.

        What the edit wrote, re-read from the database (ruling R-BAL290
        part 1).  It also read both legs' flags until plan step
        ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-1b-1; what a side shows of
        the row's flag is ``test_transfer_legs/test_what_a_side_shows.py``'s
        ``TestBothSidesShowTheTransfer`` now.  Renamed from
        ``test_an_override_edit_reaches_the_transfer_and_both_legs``, and
        before that ``test_is_override_syncs_shadows``.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id

            transfer_service.update_transfer(
                xfer_id, td["user"].id, is_override=True,
            )

            assert _reread(xfer_id).is_override is True

    def test_wrong_user_rejected(self, app, db, transfer_data, second_user):
        """Update by non-owner raises NotFoundError."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            with pytest.raises(NotFoundError):
                transfer_service.update_transfer(
                    xfer.id, second_user["user"].id,
                    amount_ownership=AmountOwnership.own(Decimal("100")),
                )

    def test_nonexistent_rejected(self, app, db, transfer_data):
        """Update of non-existent transfer raises NotFoundError."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                transfer_service.update_transfer(
                    99999, transfer_data["user"].id,
                    amount_ownership=AmountOwnership.own(Decimal("100")),
                )

    def test_update_soft_deleted_transfer_raises_not_found(self, app, db, transfer_data):
        """Verify that calling update_transfer on a soft-deleted transfer
        raises NotFoundError, not a misleading data integrity ValidationError.
        The transfer service treats soft-deleted transfers as non-existent to
        prevent confusing error messages during debugging.

        An EDIT refusal, moved here from ``TestSoftDeleteHandling`` at plan
        step ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-2a, which merged that
        group's delete cases into :class:`TestDeleteTransfer` (ruling
        R-BAL290 part 2).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.flush()

            with pytest.raises(NotFoundError, match="not found"):
                transfer_service.update_transfer(
                    xfer_id, td["user"].id,
                    amount_ownership=AmountOwnership.own(Decimal("500.00")),
                )

    def test_validates_positive_amount(self, app, db, transfer_data):
        """Update with zero amount raises ValidationError."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            with pytest.raises(ValidationError, match="positive"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, amount_ownership=AmountOwnership.own(Decimal("0")), )

    def test_validates_period_ownership(self, app, db, transfer_data, second_user):
        """Update with period belonging to another user raises NotFoundError."""
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)

            other_periods = record_paydays_across_a_hole(
                user_id=second_user["user"].id,
                first_payday=date(2026, 6, 1),
                num_periods=2,
                rhythm=rhythm_of(14),
            )
            db.session.flush()

            with pytest.raises(NotFoundError):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, pay_period_id=other_periods[0].id
                )

    def test_a_category_cleared_to_none_leaves_the_transfer_under_none(
        self, app, transfer_data,
    ):
        """Clearing the category leaves the row under none.

        What the edit wrote, re-read from the database (ruling R-BAL290
        part 1).  It also read both legs' categories, as PROJECTION reads,
        until plan step ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-1b-2: both
        rows of ``test_transfer_legs/test_what_a_side_shows.py``'s ``TestBothSidesShowTheTransfer``
        carried a category until then, so a side that answered some category
        for a transfer filed under NONE failed only those reads (measured at
        checkpoint 2e-2b-1b-1, by a mutant answering ``-1`` for a missing
        category).  That table holds a transfer filed under no category
        since checkpoint 2e-2b-1b-2, and grades what a side shows of it.
        Renamed from ``test_a_category_cleared_to_none_clears_both_legs``,
        and before that ``test_category_set_to_none_propagates_none``.
        """
        with app.app_context():
            td = transfer_data
            rent_cat = td["categories"]["Rent"]

            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("100")),
                    status_id=td["projected_status"].id,
                    category_id=rent_cat.id,
                ),
            )

            transfer_service.update_transfer(
                xfer.id, td["user"].id, category_id=None,
            )

            assert _reread(xfer.id).category_id is None

    def test_a_REVERT_is_what_clears_a_settled_transfers_figure(
        self, app, db, transfer_data,
    ):
        """A revert takes back the ASSERTION and leaves what moved on the pair.

        **This asserted the opposite until the developer's 2026-08-17 ruling.**
        A draft of plan step X-au-c3 released the figure and the basis with the
        day, under a CHECK that paired them; that welded two facts with
        different lifetimes into one, so withdrawing "this moved on this day"
        also destroyed "this is what the bank took".  The full-edit popover
        TELLS the user to revert in order to edit, so the app's own instruction
        deleted a figure they had read off a statement.

        The TRANSFER keeps it, the figure's one home since plan step
        ``balance:X-bi-6-4d-2``'s 4d-4 half (rulings **R-BAL365**,
        **R-BAL366**), where both sides' payment records kept an equal copy
        until then (Transfer Invariant 3 over the record as well as over the
        amount), and neither side is worth it while unsettled -- a side's
        figure asks the TRANSFER's status, so it answers ``None`` for a
        reverted side whatever the transfer still stores.  Each side is its
        RECORD, hung off the transfer, since plan step
        ``balance:X-bi-6-4d-2``; the shadows held it until then (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            typed_id = ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.TYPED,
            )
            sides = (xfer.from_account_id, xfer.to_account_id)

            transfer_service.update_transfer(
                xfer.id, td["user"].id,
                status_id=done_id, figure=typed(Decimal("100")),
            )
            for account_id in sides:
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == Decimal("100")

            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=projected_id,
            )

            db.session.expire_all()
            for account_id in sides:
                record = transfer_side_record(db.session, xfer.id, account_id)
                # The ASSERTION is withdrawn ...
                assert record.settled_on is None
                assert record.reconciled_by_id is None
                # ... and WHAT MOVED is kept, for BOTH sides, once, on the
                # transfer they share.
                assert (xfer.settled_figure, xfer.settled_figure_source_id) == (
                    Decimal("100"), typed_id,
                )
                # Kept, but not counted: the status is what decides.
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) is None

    def test_settle_day_defaults_to_today_when_omitted(
        self, app, db, transfer_data
    ):
        """F-048 / C-22: settling with no explicit day defaults to the user's today.

        Defense-in-depth for the route layer: any caller that
        forgets to pass a settle day when transitioning to a settled
        status (Paid/Received/Settled) still produces side records with
        a recorded ``settled_on`` day -- the dashboard's "paid
        on time" indicator and the legs' ``days_paid_before_due``
        analytics rely on it.  Each side's day is its payment RECORD's
        since plan step ``balance:X-bi-6-4d-2``; the shadows carried it until
        then (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            done_status = db.session.query(Status).filter_by(name="Paid").one()
            assert done_status.is_settled is True

            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done_status.id
            )
            db.session.flush()

            for account_id in (xfer.from_account_id, xfer.to_account_id):
                record = transfer_side_record(db.session, xfer.id, account_id)
                assert record is not None and record.settled_on is not None, (
                    f"The side on account {account_id} has no settled_on "
                    f"after settle without explicit kwarg; defense-in-depth "
                    f"failed."
                )

    def test_an_explicit_day_wins_over_the_default_on_a_settle(
        self, app, db, transfer_data
    ):
        """F-048 / C-22: an explicit settle DAY takes precedence over today.

        **This test asserted that ``settled_on=None`` could settle a transfer
        with NO day, and it pinned the defect** (finding **N-183**).  That is
        precisely the row ``balance_predicates.settled_day`` refuses -- a
        settled row whose money moved on no recorded day -- so the assertion
        was defending a 500 on every balance surface that folds the pair.  The
        caller it named is gone too: ``_apply_shadow_update`` passed an explicit
        ``None`` on a revert until plan step X-f1b0, and that was a second
        statement of the seam's own clear-on-leaving-the-band rule (finding
        **N-178**'s other half), not a value anything needed.

        What the rule really is, and what is pinned here: an explicit day wins
        over the default, and the default is the user's today.  Each side's day
        is read off its payment RECORD since plan step ``balance:X-bi-6-4d-2``
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            done_status = db.session.query(Status).filter_by(name="Paid").one()

            # Settle and correct in one call: the explicit day must survive
            # the seam's "stamp today on first entry" default.
            explicit = display_today() - timedelta(days=5)
            transfer_service.update_transfer(
                xfer.id, td["user"].id,
                status_id=done_status.id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_entered_day(explicit),
                ),
            )
            db.session.flush()
            for account_id in (xfer.from_account_id, xfer.to_account_id):
                day = transfer_side_record(
                    db.session, xfer.id, account_id,
                ).settled_on
                assert day == explicit, (
                    f"The side on account {account_id} took the default day "
                    f"{day} instead of the explicit {explicit}."
                )

    def test_settle_day_cleared_on_revert_to_non_settled(
        self, app, db, transfer_data
    ):
        """F-048 / C-22: reverting to non-settled clears the stale settle day.

        Maintains the settled-iff-dated invariant: a Paid
        transfer reverted to Projected must have its sides'
        settle day cleared, otherwise a
        future settle would silently inherit the stale timestamp.  Each
        side's day is its payment RECORD's since plan step
        ``balance:X-bi-6-4d-2``; the shadows carried it until then (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            done_status = db.session.query(Status).filter_by(name="Paid").one()
            projected_status = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            sides = (xfer.from_account_id, xfer.to_account_id)

            # Settle (the seam records the user's today).
            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=done_status.id,
            )
            db.session.flush()
            for account_id in sides:
                assert transfer_side_settle_day(
                    db.session, xfer.id, account_id,
                ) is not None

            # Revert to Projected with no explicit day.
            transfer_service.update_transfer(
                xfer.id, td["user"].id, status_id=projected_status.id,
            )
            db.session.flush()
            for account_id in sides:
                assert transfer_side_settle_day(
                    db.session, xfer.id, account_id,
                ) is None, (
                    f"The side on account {account_id} retained a stale "
                    f"settle day after revert to Projected; the F-048 "
                    f"invariant is violated."
                )

    def test_a_combined_edit_writes_every_field_it_names(
        self, app, db, transfer_data,
    ):
        """An amount, Paid and a period in ONE edit land on the row; the settle stores the amount.

        The settle runs after the amount is written (``_update`` applies a
        stated amount before its settle dispatch; the period this edit also
        moves is written after the settle), so the transfer stores the
        $999.99 the same edit wrote as its settled figure, with each side's
        record dated, where the $250.00 the transfer was created at would be
        a settle that ran first.  The figure is the TRANSFER's, its one home
        since plan step ``balance:X-bi-6-4d-2``'s 4d-4 half (rulings
        **R-BAL365**, **R-BAL366**), where each side's record carried a copy
        until then.  The row is re-read from the database and each side's
        record read by its own SQL
        (:func:`tests._test_helpers.transfer_side_record`).  Moved here from
        the invariant group as ``test_multiple_updates_maintain_invariants``
        at plan step ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-1b-1 (ruling
        R-BAL290 part 1), where it read both legs' periods and, after the
        settle, their prices, and not the records' amounts.  A side's period
        is ``test_transfer_legs/test_what_a_side_shows.py``'s ``TestBothSidesShowTheTransfer``; a
        SETTLED side's price is not, since that table holds no settled row,
        but ``test_transfer_legs/test_the_grids_legs.py``'s
        ``TestTheLegMapProducers``'
        ``test_both_legs_of_a_settled_transfer_record_its_figure_beside_the_plan_and_retain_nothing``
        and ``TestTheLegContributionTwins``'
        ``test_a_settled_from_side_leg_is_worth_the_transfers_figure_and_not_its_plan``
        (the review of that checkpoint measured both failing under a mutant
        pricing a settled side at $0).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            sides = (xfer.from_account_id, xfer.to_account_id)
            paid_id = ref_cache.status_id(StatusEnum.DONE)
            period_id = td["periods"][4].id

            transfer_service.update_transfer(
                xfer_id, td["user"].id,
                amount_ownership=AmountOwnership.own(Decimal("999.99")),
                status_id=paid_id,
                pay_period_id=period_id,
            )

            row = _reread(xfer_id)
            assert (row.amount, row.status_id, row.pay_period_id) == (
                Decimal("999.99"), paid_id, period_id,
            )
            for account_id in sides:
                record = transfer_side_record(db.session, xfer_id, account_id)
                assert (row.settled_figure, record.settled_on is not None) == (
                    Decimal("999.99"), True,
                )
