"""Plan step ``balance:X-bi-6-4a-3``: a transfer answers the pay-period doors for itself.

Rulings **R-BAL125** and **R-BAL157** (developer 2026-09-23, 2026-09-30):
every door that asks "does this transfer hold a payment or purchase" asks it
ONCE, of the transfer, through ``transfer_legs``' one join
(``transfer_holds_a_movement``, or ``held_transfer_entries``, its rows) --
the pay-period lock, the reset gate, "Remove earlier paychecks", the
transfer archive and the recurring transfer's delete -- so plan step
``X-bi-6-4d-2``, which moved a transfer's movements off its shadow rows onto
the transfer, had one place to move.  The account's delete was a sixth until
that step deleted its arm: the database refuses a hidden row or transfer
that holds a payment (rulings **R-CC89**, **R-CC92**), so a transfer the
account's cleanup removes holds none.  And the lock and the reset read a
transfer's settled state off its OWN status, not its shadows'.  Ruling
**R-BAL126**: the reset refusal counts a transfer ONCE.

Each door is graded in the module that holds its own cases:
``test_cc5_4a4_history_doors`` (the recurring transfer's delete and archive)
and ``test_pay_period_remove_earlier/test_what_a_paycheck_may_hold.py``'s
``test_a_transfer_done_then_set_back_is_refused_once`` (each side holding its
record, and again once the transfer's hide is refused).
What this module adds is what none of those grades: the clause's
own scope, the lock reading a transfer that no row in its period explains,
and the two reset counts at their new unit.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import PayPeriodResetBlocked
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import pay_period_admin, pay_period_gates, transfer_service
from app.services.pay_calendar import calendar_for
from app.services.pay_period_locks import (
    PeriodLockReason,
    classify_schedule_locks,
)
from app.services.transfer_legs import transfer_holds_a_movement
from app.utils.balance_predicates import is_projected
from app.utils.dates import display_today
from tests._test_helpers import (
    add_txn,
    create_account_of_type,
    create_settled_transfer,
    create_transfer,
    refused_by_database_rule,
    rhythm_of,
    transfer_side_record,
    typed,
)


def _savings(seed_user, periods):
    """A Savings account the transfers pay into, its books open before them."""
    savings = create_account_of_type(
        seed_user, db.session, "Savings", "Savings",
        anchor_balance=Decimal("0.00"), observed_on=periods[0].start_date,
    )
    db.session.commit()
    return savings


def _paid_500(seed_user, periods):
    """A $500.00 Checking -> Savings transfer in today's period, Paid today."""
    xfer = create_settled_transfer(
        seed_user, db.session, seed_user["account"],
        _savings(seed_user, periods), periods[4],
        amount=Decimal("500.00"), settled_on=display_today(),
    )
    db.session.commit()
    return xfer


def _reverted_500(seed_user, periods):
    """The $500.00 transfer Paid, then set back: each side KEEPS its payment.

    Ruling **R-BAL61**: a revert keeps the covering movement, un-dated, so a
    bank match on it still stands.  The transfer is Projected and holds two
    records, one per side -- on the TRANSFER since plan step
    ``balance:X-bi-6-4d-2`` (ruling **R-BAL88**), where they hung off its
    legs until then.
    """
    xfer = _paid_500(seed_user, periods)
    transfer_service.update_transfer(
        xfer.id, seed_user["user"].id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()
    return xfer


def _side_records(xfer):
    """The records the transfer's two sides hold, read by their own SQL.

    From-side then to-side, ``None`` for a side holding none
    (:func:`~tests._test_helpers.transfer_side_record`, never the clause
    under test).
    """
    return [
        transfer_side_record(db.session, xfer.id, account_id)
        for account_id in (xfer.from_account_id, xfer.to_account_id)
    ]


def _held_transfer_ids():
    """The ids of the transfers the one clause says hold a movement."""
    return {
        transfer_id for (transfer_id,) in db.session.query(Transfer.id).filter(
            transfer_holds_a_movement(),
        )
    }


class TestTheOneClause:
    """``transfer_holds_a_movement``: any record on either side.

    It asked about any entry under any shadow, live or dead, until plan step
    ``balance:X-bi-6-4d-2`` moved each side's record onto the transfer
    (ruling **R-BAL88**); a transfer has no twin since plan step
    ``balance:X-bi-6-4d-3`` made it one row (ruling **R-BAL258**).
    """

    def test_a_transfer_holding_nothing_is_not_held(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A Projected transfer never paid holds no entry: the control."""
        with app.app_context():
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"],
                _savings(seed_user, seed_periods_today), seed_periods_today[5],
                amount=Decimal("500.00"),
            )
            db.session.commit()

            assert xfer.id not in _held_transfer_ids()

    def test_a_reverted_transfer_is_held_by_the_payment_it_kept(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Projected again, and still holding two $500.00 payments.

        Each SIDE keeps its record through the revert, where each leg kept
        its entry until plan step ``balance:X-bi-6-4d-2`` (ruling
        **R-BAL167** class 1).
        """
        with app.app_context():
            xfer = _reverted_500(seed_user, seed_periods_today)
            assert [
                record is not None for record in _side_records(xfer)
            ] == [True, True], (
                "the plant: each side must keep its payment through the revert"
            )

            assert _held_transfer_ids() == {xfer.id}

    def test_hiding_a_transfer_that_kept_its_payments_is_refused_and_it_stays_held(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Finding BAL-532: the hide is refused at commit, and the clause still holds the transfer.

        Until plan step ``balance:X-bi-6-4d-2`` the occurrence delete hid the
        transfer and its legs with the payments inside, and the clause had to
        see a DEAD shadow's entry.  That state is REFUSED at commit since (the
        deleted-row rule's transfer arm; the delete takes the payments off,
        ruling **R-CC75**), so it is asserted refused (ruling **R-BAL167**
        class 2), and after the refusal the transfer still holds both
        records and the clause still holds it.  The case also hid both twins
        around the service, before the refused hide and again over the live
        transfer's two kept records, until plan step
        ``balance:X-bi-6-4d-3``'s vacuous-residue checkpoint: a transfer is
        one row since that step (ruling **R-BAL258**), so each of those
        updates touched no row and the plant's read of the twins walked an
        empty list (both measured).  Renamed from
        ``test_a_hidden_legs_kept_payment_is_held``.
        """
        with app.app_context():
            xfer = _reverted_500(seed_user, seed_periods_today)
            db.session.execute(
                db.text(
                    "UPDATE budget.transfers SET is_deleted = TRUE WHERE id = :t"
                ),
                {"t": xfer.id},
            )
            with refused_by_database_rule(
                "was deleted while it still holds a recorded payment",
            ) as caught:
                db.session.commit()
            db.session.rollback()
            assert f"transfer {xfer.id} " in str(caught.value)

            db.session.expire_all()
            assert all(
                record is not None for record in _side_records(xfer)
            ), "the plant: the refused hide left the transfer holding both payments"

            assert _held_transfer_ids() == {xfer.id}


class TestThePeriodLock:
    """The classifier reads a transfer's own status and the one clause."""

    def test_a_paid_transfer_alone_locks_its_period(
        self, app, db, seed_user, seed_periods_today,
    ):
        """No row of the period is settled; the $500.00 transfer is.

        A transfer's status lives in its one row since plan step
        ``balance:X-bi-6-4d-3`` (ruling **R-BAL258**).  Until plan step
        ``balance:X-bi-6-4d-2`` its two shadow rows carried the same status
        on this door-written state, so the case could not tell which of the
        three the lock read, and :class:`TestTheParentDecidesADrift` planted
        a drift to tell them apart; from that step a settle left the twins
        Projected under the Paid transfer, until the next deleted them.  The
        plant's count also left the shadows out
        (``transfer_id IS NULL``), a term no row can fail since
        (``ck_transactions_names_no_transfer``), dropped at that step's
        vacuous-residue checkpoint: the count was 0 with it and without it
        (measured).
        """
        with app.app_context():
            period = seed_periods_today[4]
            _paid_500(seed_user, seed_periods_today)
            assert db.session.query(Transaction).filter(
                Transaction.pay_period_id == period.id,
            ).count() == 0, "the plant: the transfer is all the period holds"

            locks = classify_schedule_locks(
                calendar_for(seed_user["user"].id), as_of=display_today(),
            )

            assert locks[period.id] is PeriodLockReason.SETTLED_TXN

    def test_a_reverted_transfer_locks_its_period_for_its_kept_payment(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Projected, so not settled; it still holds $500.00 on each leg."""
        with app.app_context():
            period = seed_periods_today[4]
            _reverted_500(seed_user, seed_periods_today)

            locks = classify_schedule_locks(
                calendar_for(seed_user["user"].id), as_of=display_today(),
            )

            assert locks[period.id] is PeriodLockReason.HOLDS_MOVEMENT


class TestTheParentDecidesADrift:
    """The TRANSFER's status is what the lock reads (ruling **R-JM**).

    As ``pay_period_locks.settled_items`` states it.  The class planted the
    two status drifts Transfer Invariant 3 forbade around the service,
    because on every door-written state the transfer and its legs shared
    one status, so a lock still reading the LEGS' status passed every test
    above.  Drift A, a leg set Paid under a Projected transfer, was deleted
    at plan step ``balance:X-bi-6-4d-3`` (ruling **R-BAL313**): a transfer
    came with two shadow rows until that step made it one row (ruling
    **R-BAL258**).  Its three reads of the reverted transfer it planted over
    -- the HOLDS_MOVEMENT lock, no settled item, one holding row -- are
    :class:`TestThePeriodLock`'s
    ``test_a_reverted_transfer_locks_its_period_for_its_kept_payment`` and
    :class:`TestTheResetCounts`' ``test_a_transfer_holding_its_payment_counts_once``,
    on the same ``_reverted_500``.  Drift B, the transfer set Paid alone
    over its two Projected legs, is the one case left, and it is no drift
    any more: a Paid transfer whose sides hold no record is the ``$0.00``
    close the status-band rule admits (``side_band_infrastructure``), so
    the case closes the transfer at ``$0.00`` through its settle door.  The
    class's name is its history.
    """

    def test_a_transfer_closed_at_zero_is_settled(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The $500.00 transfer marked Paid at $0.00: no record, and its period locked as settled.

        A ``$0.00`` close stores no record on either side (rulings
        **R-BAL82**, **R-BAL141**), so the lock and the settled count read
        the transfer's own status alone.  Until plan step
        ``balance:X-bi-6-4d-3``'s vacuous-residue checkpoint the case wrote
        the status Done around the service: drift B, over two Projected
        shadows until that step made a transfer one row (a lock reading the
        legs' status found no settled item there), and its plant read those
        legs, an empty list since (measured).  Renamed from
        ``test_a_paid_transfer_over_projected_legs_is_settled``.
        """
        with app.app_context():
            period = seed_periods_today[4]
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"],
                _savings(seed_user, seed_periods_today), period,
                amount=Decimal("500.00"),
            )
            db.session.commit()
            transfer_service.settle_transfer(
                xfer.id, seed_user["user"].id, submitted=typed(Decimal("0.00")),
            )
            db.session.commit()
            db.session.expire_all()
            assert not is_projected(db.session.get(Transfer, xfer.id)) and (
                _side_records(xfer) == [None, None]
            ), "the plant: a Paid transfer holding no record on either side"
            user_id = seed_user["user"].id

            locks = classify_schedule_locks(
                calendar_for(user_id), as_of=display_today(),
            )

            assert locks[period.id] is PeriodLockReason.SETTLED_TXN
            assert pay_period_gates.settled_transaction_count(user_id) == 1


class TestTheResetCounts:
    """Ruling R-BAL126: the reset refusal counts a transfer ONCE."""

    @staticmethod
    def _refusal(seed_user, seed_periods_today):
        """Press Reset and return what refused it; nothing is written."""
        with pytest.raises(PayPeriodResetBlocked) as caught:
            pay_period_admin.reset_pay_periods(
                seed_user["user"].id,
                new_start_date=seed_periods_today[0].start_date,
                num_periods=4, rhythm=rhythm_of(14),
            )
        db.session.rollback()
        return caught.value

    def test_a_paid_transfer_counts_once_beside_a_paid_row(
        self, app, db, seed_user, seed_periods_today,
    ):
        """One Received $2,000.00 paycheck + one Paid $500.00 transfer = 2.

        It read 3 until this step: the paycheck, and the transfer once per
        settled shadow.
        """
        with app.app_context():
            add_txn(
                db.session, seed_user, seed_periods_today[2], "Paycheck",
                "2000.00", status_enum=StatusEnum.RECEIVED, is_income=True,
            )
            _paid_500(seed_user, seed_periods_today)
            user_id = seed_user["user"].id

            assert pay_period_gates.settled_transaction_count(user_id) == 2
            refusal = self._refusal(seed_user, seed_periods_today)
            assert (refusal.settled_count, refusal.holding_count) == (2, 0)
            assert str(refusal).startswith(
                "Cannot reset the schedule: you have 2 settled transaction(s)."
            )

    def test_a_transfer_holding_its_payment_counts_once(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Nothing settled; one reverted transfer, its payment on both legs = 1.

        It read 2 until this step, once per holding leg.
        """
        with app.app_context():
            _reverted_500(seed_user, seed_periods_today)
            user_id = seed_user["user"].id

            assert pay_period_gates.settled_transaction_count(user_id) == 0
            assert pay_period_gates.movement_holding_row_count(user_id) == 1
            refusal = self._refusal(seed_user, seed_periods_today)
            assert (refusal.settled_count, refusal.holding_count) == (0, 1)
            assert str(refusal).startswith(
                "Cannot reset the schedule: 1 row(s) hold a recorded payment "
                "or purchase; delete it from its row first."
            )
