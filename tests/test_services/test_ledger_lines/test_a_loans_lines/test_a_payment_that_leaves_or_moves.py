"""A loan payment that leaves the walk or moves its day takes its split with it, at its own key."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db as _db
from app.services import transfer_service
from tests._ledger_reads import account_net, linked_key_of, linked_net_by_date
from tests._test_helpers import an_entered_day, on_both_sides

from ._helpers import (
    _P1,
    _P3,
    _a_split,
    _interest_net,
    _loan_side_movement,
    _make_loan,
    _settle_payment,
    _split_lines,
    _split_lines_at,
)


class TestARevertedOrDeletedPayment:
    """A payment that leaves the walk leaves its key with no split.

    The split is a date-keyed correction with no row link since plan step
    ``balance:X-bi-6-3`` (ruling **R-BAL102**), so a payment that leaves the
    walk -- reverted, deleted, moved -- leaves its key with nothing, and its
    cash line goes with its record's day.  The stored ledger reversed the key
    at its own period (the R2 rule) until plan step plan#2914 deleted the
    store; each case here checks the split was on the read before the act, so
    its absence afterwards cannot pass on a payment that never booked.
    """

    def test_revert_and_move_leaves_neither_period_a_split(
        self, app, db, seed_user, seed_periods,
    ):
        """A reverted-and-moved payment's split is in neither its old period nor its new.

        The loan twin of the R2 attribution rule (the 2026-07-02 review's H1
        class): one ``update_transfer`` call reverts the payment to Projected
        AND moves it to a later period.  A revert releases the loan side's day
        (its record's, off the transfer since plan step balance:X-bi-6-4d-2;
        the shadow's until then, ruling R-BAL167 class 1), so the walk no
        longer prices the payment: P1's key, captured before the revert, holds
        no split, the new period holds none either, and the interest line nets
        back to zero.  Arithmetic: the P1 split was interest 500.00 / principal
        500.00.  It was ``test_revert_and_move_reverses_into_the_original_period``,
        reading the store's reversal pair in P1, until plan step plan#2914.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            original_period_id = seed_periods[_P1].id
            original_day = _loan_side_movement(xfer, loan).settled_on
            moved_to = seed_periods[_P3]
            assert _split_lines(xfer, loan) == _a_split(
                loan, Decimal("-500.00"), Decimal("500.00"),
            ), "CONTROL: the settle split the payment in P1"

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                pay_period_id=moved_to.id,
            )
            db.session.commit()

            assert _split_lines_at(
                loan, scenario_id, original_period_id, original_day,
            ) == {}
            assert _split_lines_at(
                loan, scenario_id, moved_to.id, original_day,
            ) == {}
            assert _interest_net(loan, scenario_id) == Decimal("0.00")

    def test_a_hard_deleted_payment_leaves_its_key_no_split(
        self, app, db, seed_user, seed_periods,
    ):
        """A hard-deleted payment's split and cash go; the loan returns to its anchors.

        The walk no longer prices the payment, so its key ``(P1, the settle
        day)`` holds nothing, and the payment's cash line goes with its
        record.  Nothing about the split needed the payment's row: it linked
        none.  The loan's own line returns to the opening (-250000) + true-up
        (+150000) = -100000.00 and the interest line to 0.00.  It was
        ``test_hard_delete_reverses_the_correction_at_its_own_key``, reading
        the store's reversal, until plan step plan#2914.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            # The day is the loan side's record's (ruling R-BAL167 class 1,
            # plan step balance:X-bi-6-4d-2; the shadow's own until then).
            period_id = xfer.pay_period_id
            day = _loan_side_movement(xfer, loan).settled_on
            assert _split_lines_at(
                loan, scenario_id, period_id, day,
            ) == _a_split(loan, Decimal("-500.00"), Decimal("500.00")), (
                "CONTROL: the settle split the payment at its key"
            )

            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=False,
            )
            db.session.commit()

            assert _split_lines_at(loan, scenario_id, period_id, day) == {}
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100000.00")
            assert _interest_net(loan, scenario_id) == Decimal("0")


class TestASettleDayEditReDatesTheLines:
    """A settled settle-day edit re-dates a loan payment's lines (finding N-13).

    The one legitimate edit that moves a date without moving an amount: the
    worked-out ledger moves the payment's effect to the new day, in both
    directions.  The class was ``TestCheckedProjection`` and held the
    checked-projection assert's negative control and its lineage pass's two
    heals until plan task plan#3093 deleted the assert and the pass ahead of
    the store; its two cases checked the stored reconcile converged in one
    pass until plan step plan#2914 deleted the store.
    """

    def test_settle_day_edit_redates_the_lines(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled settle-day edit moves every line to the new settle date.

        The N-13 regression.  P1's $1,000.00 payment settles 2026-01-20:
        interest 100000 * 0.06 / 12 = 500.00, principal 500.00, so the loan's
        own line nets +1000.00 (cash) - 500.00 (split) = +500.00 on 01-20.
        A PURE settle-day edit to 2026-02-05 changes no amount, so the
        pre-E1a reconcile wrote nothing and the entries kept the old date --
        while the fold moved to 02-05.  After the edit: 01-20 holds no line
        and 02-05 nets +500.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 1, 20),
            )
            db.session.commit()
            linked = linked_key_of(_db.session, loan.id)
            assert linked_net_by_date(_db.session, linked, scenario_id)[
                date(2026, 1, 20)
            ] == Decimal("500.00")

            # The pure settle-day edit -- no amount, status, or period change.
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_entered_day(date(2026, 2, 5)),
                ),
            )
            db.session.commit()

            by_date = linked_net_by_date(_db.session, linked, scenario_id)
            # Old date: nothing left there, the read holding no $0.00 line.
            assert date(2026, 1, 20) not in by_date
            # New date: the full effect -- cash +1000.00, split -500.00.
            assert by_date[date(2026, 2, 5)] == Decimal("500.00")

    def test_a_backward_settle_day_move_redates_the_lines(
        self, app, db, seed_user, seed_periods,
    ):
        """Moving the settle day EARLIER re-dates the lines too.

        The churn control of the stored reconcile, kept on the read: the
        backward move (02-05 -> 01-20) leaves 02-05 with no line and 01-20
        netting +500.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 2, 5),
            )
            db.session.commit()
            linked = linked_key_of(_db.session, loan.id)
            assert linked_net_by_date(_db.session, linked, scenario_id)[
                date(2026, 2, 5)
            ] == Decimal("500.00"), "CONTROL: the payment's lines before the move"

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_entered_day(date(2026, 1, 20)),
                ),
            )
            db.session.commit()

            by_date = linked_net_by_date(_db.session, linked, scenario_id)
            assert date(2026, 2, 5) not in by_date
            assert by_date[date(2026, 1, 20)] == Decimal("500.00")
