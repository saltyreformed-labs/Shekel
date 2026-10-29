"""Plan step ``balance:X-bi-6-4c-3``: each side of a transfer keeps its own day.

Through the transfer service's verbs -- the ONE writer of a side's day
(``transfer_service._status.apply_status_to_all_three``) and the doors onto it
-- on real rows, covering movements and ledger postings.  The pure day function
is graded in ``test_transfer_side_days.py``; the evidence doors that state a
side (the statement matcher, the reconcile tick) in their own suites; the
popover's boxes in ``tests/test_routes/test_transfers/test_settle_day.py``.

Every case builds a Checking -> Savings transfer whose Savings books open
before any day used here, so the only refusal a day can meet is the one under
test.
"""

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.services import transfer_legs, transfer_service
from app.services.settle_day import SettleDay, record_settle_day
from app.utils.dates import display_today
from tests._test_helpers import (
    an_entered_day,
    an_observed_day,
    create_savings_account,
    create_transfer,
    net_posted_by_day,
    open_books_before_the_first_assertion,
    refused_by_database_rule,
    transfer_family_journal_filter,
    transfer_side_record,
    transfer_side_settle_day,
    typed,
)

BORROWED = SettledDayBasisEnum.BORROWED


def _borrowed(day):
    """Return *day* as a side holding no evidence of its own records it."""
    return SettleDay(day=day, basis=BORROWED)


def _transfer(seed_user, period):
    """Return a Projected $300.00 Checking -> Savings transfer, committed."""
    savings = create_savings_account(
        seed_user, db.session, "Savings", Decimal("0.00"),
    )
    open_books_before_the_first_assertion(db.session, savings)
    xfer = create_transfer(
        seed_user, db.session, seed_user["account"], savings, period,
        amount=Decimal("300.00"),
    )
    db.session.commit()
    return xfer


def _records(xfer):
    """Return each side's payment record: ``(expense, income)``, freshly read.

    Off the TRANSFER by its side links (ruling R-BAL167 class 4, plan step
    balance:X-bi-6-4d-2); each record hung off its side's shadow until then.
    """
    db.session.expire_all()
    return (
        transfer_side_record(db.session, xfer.id, xfer.from_account_id),
        transfer_side_record(db.session, xfer.id, xfer.to_account_id),
    )


def _days(xfer):
    """Return each side's recorded day: ``(expense, income)``, off its record.

    A side's day is its record's since plan step balance:X-bi-6-4d-2 (ruling
    R-BAL167 class 4); it was read off each side's shadow until then.
    """
    db.session.expire_all()
    return (
        transfer_side_settle_day(db.session, xfer.id, xfer.from_account_id),
        transfer_side_settle_day(db.session, xfer.id, xfer.to_account_id),
    )


class TestAPaidPress:
    """Declared change 1: Mark Paid states no day, so both sides borrow the press day."""

    def test_both_sides_borrow_the_day_paid_was_pressed(
        self, app, seed_user, seed_periods_today,
    ):
        """Both sides' records ``borrowed`` on the owner's today.

        The shadows' days were asserted beside their movements' until plan
        step balance:X-bi-6-4d-2; a side's day is its record's alone now
        (ruling R-BAL167 class 1), which is what ``_days`` reads.
        """
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3])

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()

            today = _borrowed(display_today())
            assert _days(xfer) == (today, today)

    def test_a_revert_clears_both_and_a_second_press_borrows_again(
        self, app, seed_user, seed_periods_today,
    ):
        """A revert releases each side's day, evidence included; Paid again is a fresh guess."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3])
            owner = seed_user["user"].id
            day = display_today() - timedelta(days=5)
            transfer_service.settle_transfer(
                xfer.id, owner,
                side_days=(transfer_service.SideDay(
                    xfer.from_account_id, an_observed_day(day),
                ),),
            )
            db.session.commit()

            transfer_service.update_transfer(
                xfer.id, owner,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            assert _days(xfer) == (None, None)

            transfer_service.settle_transfer(xfer.id, owner)
            db.session.commit()
            today = _borrowed(display_today())
            assert _days(xfer) == (today, today)


class TestACorrection:
    """Declared change 3: a day stated for one side re-dates that side only."""

    def test_a_borrowing_side_follows_and_the_ledger_with_it(
        self, app, seed_user, seed_periods_today,
    ):
        """Paid pressed today, Checking corrected: Savings follows, and so do both entries."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3])
            owner = seed_user["user"].id
            transfer_service.settle_transfer(xfer.id, owner)
            db.session.commit()
            corrected = display_today() - timedelta(days=4)

            transfer_service.update_transfer(
                xfer.id, owner,
                side_days=(transfer_service.SideDay(
                    xfer.from_account_id, an_entered_day(corrected),
                ),),
            )
            db.session.commit()

            assert _days(xfer) == (an_entered_day(corrected), _borrowed(corrected))
            assert net_posted_by_day(transfer_family_journal_filter(xfer.id)) == {
                corrected: Decimal("300.00"),
            }

    def test_sides_on_two_days_post_on_two_days(
        self, app, seed_user, seed_periods_today,
    ):
        """Each side's entry is filed under ITS day (R-BAL108's worked example).

        The money left Checking on one day and reached Savings on a later one;
        between them it is in transit, and the ledger books each side's
        movement on its own day rather than one day for the pair.
        """
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3])
            left = display_today() - timedelta(days=6)
            arrived = display_today() - timedelta(days=4)

            transfer_service.settle_transfer(
                xfer.id, seed_user["user"].id,
                side_days=(
                    transfer_service.SideDay(
                        xfer.from_account_id, an_observed_day(left),
                    ),
                    transfer_service.SideDay(
                        xfer.to_account_id, an_entered_day(arrived),
                    ),
                ),
            )
            db.session.commit()

            assert _days(xfer) == (an_observed_day(left), an_entered_day(arrived))
            assert net_posted_by_day(transfer_family_journal_filter(xfer.id)) == {
                left: Decimal("300.00"), arrived: Decimal("300.00"),
            }

    def test_typing_the_borrowed_day_makes_it_the_sides_own_to_its_movement(
        self, app, seed_user, seed_periods_today,
    ):
        """The same day, now the owner's: the side's record's label RISES (D4).

        Ruling **R-BAL164**: a borrowed side's box is empty, so any day typed
        there -- the borrowed one included -- is that side's own, ``entered``.
        The day does not move, so only the seam's equal-day arm can carry the
        new label onto the covering movement; it copies a raise, and a label
        over a movement holding no evidence is one.  The shadow's label was
        asserted beside the movement's until plan step balance:X-bi-6-4d-2;
        the side's record is its one home now (ruling R-BAL167 class 1).
        """
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3])
            owner = seed_user["user"].id
            day = display_today() - timedelta(days=3)
            transfer_service.settle_transfer(
                xfer.id, owner,
                side_days=(transfer_service.SideDay(
                    xfer.from_account_id, an_observed_day(day),
                ),),
            )
            db.session.commit()
            assert _days(xfer)[1] == _borrowed(day)

            transfer_service.update_transfer(
                xfer.id, owner,
                side_days=(transfer_service.SideDay(
                    xfer.to_account_id, an_entered_day(day),
                ),),
            )
            db.session.commit()

            assert _days(xfer)[1] == an_entered_day(day)


class TestAFigureCorrection:
    """Finding N-304: a figure correction states no day, so no side moves."""

    def test_each_side_keeps_its_own_day_and_its_link(
        self, app, seed_user, seed_periods_today,
    ):
        """Two sides on two days, Checking cleared by a statement; the Actual box re-typed.

        Until plan step ``balance:X-bi-6-4c-3`` a figure correction ran the
        pair-day repair, re-dating a leg whose day differed from its sibling's
        and releasing its clearing link (finding **N-304**).  The tick links
        the Checking side's RECORD, its one home since plan step
        balance:X-bi-6-4d-2, where it linked the side's shadow and its
        movement until then (ruling R-BAL167 class 1).
        """
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3])
            owner = seed_user["user"].id
            left = display_today() - timedelta(days=6)
            arrived = display_today() - timedelta(days=4)
            transfer_service.settle_transfer(
                xfer.id, owner,
                side_days=(
                    transfer_service.SideDay(
                        xfer.from_account_id, an_observed_day(left),
                    ),
                    transfer_service.SideDay(
                        xfer.to_account_id, an_entered_day(arrived),
                    ),
                ),
            )
            anchor = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=xfer.from_account_id)
                .order_by(AccountAnchorHistory.id.desc())
                .first()
            )
            transfer_service.record_leg_clearing(
                transfer_legs.leg_of(xfer, xfer.from_account_id),
                anchor.id,
            )
            db.session.commit()
            assert _records(xfer)[0].reconciled_by_id == anchor.id

            transfer_service.update_transfer(
                xfer.id, owner, figure=typed(Decimal("310.00")),
            )
            db.session.commit()

            assert _days(xfer) == (an_observed_day(left), an_entered_day(arrived))
            assert _records(xfer)[0].reconciled_by_id == anchor.id


class TestASettleOverADriftedSide:
    """Ledger row BAL-578: a settle over a settled parent dates a drifted side on ITS day.

    The day stated for that side, not the owner's today -- until plan step
    ``balance:X-bi-6-4d-2``, when the drift became unstorable (the band rule)
    and the repair went with it; the first case says what still holds.
    """

    def test_a_drifted_side_is_refused_and_a_settle_in_its_save_keeps_the_others_day(
        self, app, seed_user, seed_periods_today,
    ):
        """A drifted side is refused at commit; a settle inside the save keeps the other's day.

        The drift is the RETAINED shape (the Savings side reverted alone, its
        record kept and un-dated), planted behind the seam's back because no
        door writes it.  Until plan step ``balance:X-bi-6-4c-3`` the settle
        kept only its status over an already-settled parent, so the stated day
        was dropped and the repair dated the side on today.

        **The drift is refused at COMMIT since plan step
        ``balance:X-bi-6-4d-2``** (ruling **R-BAL167** class 2): the side's
        day is its RECORD's, off the transfer, and a settled transfer holding
        an un-dated record breaks the band rule.  So the drift is planted and
        its commit refused, then planted again and the settle run inside the
        save, which is all the state can ever exist in.  The transfer still
        says Paid (the shadow's status was asserted until then, class 1) and
        the Checking side keeps its own day.  DROPPED: the Savings side on
        ``an_observed_day(savings_day)``.  The repair that honoured a day
        stated for a drifted side (ledger row BAL-578) went at that step with
        the state, and inside the save the side borrows the Checking side's
        day instead (measured).  The plant also wrote the Savings shadow's
        status and day, which no reader read (class 3), until plan step
        ``balance:X-bi-6-4d-2`` made a transfer one row (ruling
        **R-BAL258**); the record is the whole plant now.
        """
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3])
            owner = seed_user["user"].id
            checking_day = display_today() - timedelta(days=6)
            transfer_service.settle_transfer(
                xfer.id, owner,
                side_days=(transfer_service.SideDay(
                    xfer.from_account_id, an_observed_day(checking_day),
                ),),
            )
            db.session.commit()

            def _drift():
                """Plant the drift, staged: the Savings side's record un-dated."""
                record_settle_day(
                    transfer_side_record(db.session, xfer.id, xfer.to_account_id),
                    None,
                )
                db.session.flush()

            _drift()
            with refused_by_database_rule(
                "is settled but its sides hold 1 dated and 1 un-dated",
            ) as caught:
                db.session.commit()
            db.session.rollback()
            assert f"transfer {xfer.id} " in str(caught.value)

            _drift()
            savings_day = display_today() - timedelta(days=2)

            transfer_service.settle_transfer(
                xfer.id, owner,
                side_days=(transfer_service.SideDay(
                    xfer.to_account_id, an_observed_day(savings_day),
                ),),
            )

            assert xfer.status.is_settled
            assert _days(xfer)[0] == an_observed_day(checking_day)
            db.session.rollback()

    def test_an_in_band_side_ignores_a_stated_day(
        self, app, seed_user, seed_periods_today,
    ):
        """A settle is idempotent over money already recorded: an in-band side keeps its day."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3])
            owner = seed_user["user"].id
            day = display_today() - timedelta(days=6)
            transfer_service.settle_transfer(
                xfer.id, owner,
                side_days=(transfer_service.SideDay(
                    xfer.from_account_id, an_observed_day(day),
                ),),
            )
            db.session.commit()

            assert transfer_service.settle_transfer(
                xfer.id, owner,
                side_days=(transfer_service.SideDay(
                    xfer.from_account_id,
                    an_observed_day(display_today() - timedelta(days=1)),
                ),),
            ) is False
            db.session.commit()

            assert _days(xfer) == (an_observed_day(day), _borrowed(day))
