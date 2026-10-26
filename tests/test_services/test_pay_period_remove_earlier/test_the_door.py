"""The DOOR (``pay_period_admin.remove_earlier_pay_periods``) on an owner with no money: what it
removes, where it moves the phase, that the paycheck it starts from stays, that the earliest
rhythm keeps a paycheck (R-PC110), the refusals that name no paycheck, a stated history the
removal cannot put above the record, and an add undone that leaves nothing behind.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.exceptions import PayPeriodRemovalRefused, PayPeriodUnresolved
from app.extensions import db as _db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import (
    account_posting_service,
    loan_posting_service,
    pay_period_admin,
    pay_period_locks,
    pay_period_write,
    pay_schedule_service,
)
from app.services.pay_rhythm import Monthly, Rhythm
from tests._test_helpers import (
    all_periods,
    assert_pay_period_invariants,
    create_savings_account,
    populate_in_a_fresh_pass,
    restate_account_opening,
    rhythm_of,
)

from ._helpers import (
    NONE,
    PRIOR,
    _paydays,
    _stored_eras,
    _unchanged,
    _added_head,
    _monthly_bill_from,
    _monthly_transfer_from,
    _entries_in,
)


def _record(user_id, first_payday, num_periods, rhythm):
    """Record a batch through the writer and commit it."""
    periods = pay_period_write.record_paydays(
        user_id=user_id, first_payday=first_payday,
        num_periods=num_periods, rhythm=rhythm,
    )
    _db.session.commit()
    return periods


def _period_on(user_id, payday):
    """Return the owner's PayPeriod opening on *payday*."""
    return next(
        period for period in all_periods(user_id) if period.start_date == payday
    )


class TestTheDoorRemovesTheHeadAndMovesThePhase:
    """``pay_period_admin.remove_earlier_pay_periods`` on an owner with no money."""

    def test_it_removes_every_paycheck_before_the_one_picked(
        self, app, db, bare_user,
    ):
        """Start from 01-30: 01-02 and 01-16 go, the phase moves to 01-30."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 5, rhythm_of(14))

            removed = pay_period_admin.remove_earlier_pay_periods(
                user_id, _period_on(user_id, date(2026, 1, 30)).id,
            )
            db.session.commit()

            assert removed == 2
            assert _paydays(user_id) == [
                date(2026, 1, 30), date(2026, 2, 13), date(2026, 2, 27),
            ]
            assert _stored_eras(user_id) == [(date(2026, 1, 30), 14, None, None)]
            assert_pay_period_invariants(db.session, user_id)

    def test_starting_from_the_first_removes_nothing(self, app, db, bare_user):
        """The select's default: the current first paycheck, a no-op."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            paydays, eras = _paydays(user_id), _stored_eras(user_id)

            removed = pay_period_admin.remove_earlier_pay_periods(
                user_id, _period_on(user_id, date(2026, 1, 2)).id,
            )

            assert removed == 0
            _unchanged(user_id, paydays, eras)

    def test_the_last_paycheck_can_be_the_start_and_it_stays(
        self, app, db, bare_user,
    ):
        """Start from the LAST paycheck: every other goes, that one stays.

        R-PC110's "the same rule refuses removing every paycheck", made
        structural: the door keeps the paycheck it is told to start from,
        so no choice empties the schedule.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))

            removed = pay_period_admin.remove_earlier_pay_periods(
                user_id, _period_on(user_id, date(2026, 1, 30)).id,
            )
            db.session.commit()

            assert removed == 2
            assert _paydays(user_id) == [date(2026, 1, 30)]
            assert _stored_eras(user_id) == [(date(2026, 1, 30), 14, None, None)]

    def test_the_phase_moves_to_the_NOMINAL_day_of_a_displaced_payday(
        self, app, db, bare_user,
    ):
        """Thanksgiving 2030-11-28 is paid 11-27 under ``prior``; the phase is 11-28.

        Recorded from 11-14 on a fortnight under ``prior``: 11-14, 11-27
        (the displaced Thanksgiving), 12-12.  Starting from 11-27 moves the
        phase to the day that paycheck was paid FOR, on the grid; its cash
        day is off the grid and would move every planned payday.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2030, 11, 14), 3, rhythm_of(14, PRIOR))
            assert _paydays(user_id) == [
                date(2030, 11, 14), date(2030, 11, 27), date(2030, 12, 12),
            ]

            pay_period_admin.remove_earlier_pay_periods(
                user_id, _period_on(user_id, date(2030, 11, 27)).id,
            )
            db.session.commit()

            assert _stored_eras(user_id) == [(date(2030, 11, 28), 14, None, None)]

    def test_a_month_kind_era_moves_onto_its_clamped_day(self, app, db, bare_user):
        """Monthly on the 31st from 01-31: starting from 02-28 stores the 31st it means."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 31), 4, Rhythm(Monthly(31), NONE))
            assert _paydays(user_id)[:2] == [date(2026, 1, 31), date(2026, 2, 28)]

            pay_period_admin.remove_earlier_pay_periods(
                user_id, _period_on(user_id, date(2026, 2, 28)).id,
            )
            db.session.commit()

            assert _stored_eras(user_id) == [(date(2026, 2, 28), None, 31, None)]
            assert _paydays(user_id) == [
                date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30),
            ]

    def test_a_later_era_is_left_as_it_was(self, app, db, bare_user):
        """Only the earliest era moves; the weekly one keeps its day."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            _record(user_id, date(2026, 2, 13), 2, rhythm_of(7))

            pay_period_admin.remove_earlier_pay_periods(
                user_id, _period_on(user_id, date(2026, 1, 16)).id,
            )
            db.session.commit()

            assert _stored_eras(user_id) == [
                (date(2026, 1, 16), 14, None, None),
                (date(2026, 2, 13), 7, None, None),
            ]


class TestTheEarliestRhythmKeepsAPaycheck:
    """Ruling R-PC110: at least one paycheck of the earliest rhythm stays."""

    def _two_eras(self, user_id):
        """R-PC110's worked owner: 01-08 .. 02-19 fortnightly, then weekly from 03-12."""
        _record(user_id, date(2026, 1, 8), 4, rhythm_of(14))
        _record(user_id, date(2026, 3, 12), 3, rhythm_of(7))
        assert _paydays(user_id) == [
            date(2026, 1, 8), date(2026, 1, 22), date(2026, 2, 5),
            date(2026, 2, 19), date(2026, 3, 12), date(2026, 3, 19),
            date(2026, 3, 26),
        ]

    def test_starting_from_the_later_rhythm_is_refused_in_the_ruled_words(
        self, app, db, bare_user,
    ):
        """Choosing 03-12 is refused, naming 02-19; nothing is written."""
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_eras(user_id)
            paydays, eras = _paydays(user_id), _stored_eras(user_id)

            with pytest.raises(PayPeriodRemovalRefused) as refused:
                pay_period_admin.remove_earlier_pay_periods(
                    user_id, _period_on(user_id, date(2026, 3, 12)).id,
                )

            assert str(refused.value) == (
                "Keep at least 2026-02-19, your last paycheck paid every 14 "
                "days. Removing it would erase that pay rhythm."
            )
            _unchanged(user_id, paydays, eras)

    def test_starting_further_into_the_later_rhythm_names_the_same_payday(
        self, app, db, bare_user,
    ):
        """03-19 is refused too, and the payday to keep is still 02-19."""
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_eras(user_id)

            with pytest.raises(PayPeriodRemovalRefused, match="Keep at least 2026-02-19,"):
                pay_period_admin.remove_earlier_pay_periods(
                    user_id, _period_on(user_id, date(2026, 3, 19)).id,
                )

    def test_starting_from_the_earliest_rhythms_last_paycheck_is_admitted(
        self, app, db, bare_user,
    ):
        """02-19 stays, so the fortnight still pays a paycheck; its phase moves there."""
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_eras(user_id)

            removed = pay_period_admin.remove_earlier_pay_periods(
                user_id, _period_on(user_id, date(2026, 2, 19)).id,
            )
            db.session.commit()

            assert removed == 3
            assert _stored_eras(user_id) == [
                (date(2026, 2, 19), 14, None, None),
                (date(2026, 3, 12), 7, None, None),
            ]
            assert_pay_period_invariants(db.session, user_id)


class TestTheDoorRefusesAnIdItCannotResolve:
    """An id that is not the owner's removes nothing, as at truncate."""

    def test_another_owners_period_is_refused_and_both_schedules_stand(
        self, app, db, bare_user, seed_user, seed_periods,
    ):
        """The second owner's paycheck id: the unresolved refusal, nothing removed."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            paydays, eras = _paydays(user_id), _stored_eras(user_id)
            foreign = seed_periods[5]
            theirs = _paydays(seed_user["user"].id)

            with pytest.raises(PayPeriodUnresolved) as refused:
                pay_period_admin.remove_earlier_pay_periods(user_id, foreign.id)

            assert "choose the paycheck to start from" in str(refused.value)
            _unchanged(user_id, paydays, eras)
            assert _paydays(seed_user["user"].id) == theirs

    def test_an_id_that_names_nothing_is_refused_the_same_way(
        self, app, db, bare_user,
    ):
        """A missing id carries the same message: the door is no oracle."""
        with app.app_context():
            user_id = bare_user["user"].id
            created = _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))

            with pytest.raises(PayPeriodUnresolved, match="start from"):
                pay_period_admin.remove_earlier_pay_periods(
                    user_id, max(period.id for period in created) + 10_000,
                )


class TestAStatedHistoryStaysBelowTheRecord:
    """R-PC104's predicate cannot fire after a removal: the record opens later."""

    def test_a_removal_leaves_a_stated_history_below_the_new_first_payday(
        self, app, db, bare_user,
    ):
        """History 2025-12-05, the added paychecks removed: the record opens 01-02."""
        with app.app_context():
            user_id = bare_user["user"].id
            _record(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            pay_period_admin.add_earlier_pay_periods(user_id, 2)
            pay_schedule_service.set_history_opening(user_id, date(2025, 12, 5))
            db.session.commit()

            pay_period_admin.remove_earlier_pay_periods(
                user_id, _period_on(user_id, date(2026, 1, 2)).id,
            )
            db.session.commit()

            facts = pay_schedule_service.resolve_schedule(user_id)
            assert facts.history_opens_on == date(2025, 12, 5)
            assert _paydays(user_id)[0] == date(2026, 1, 2)
            assert pay_schedule_service.record_below_history(
                _paydays(user_id)[0], facts.history_opens_on,
            ) is False


def _rows_by_period(user_id):
    """Return every row and transfer in the owner's paychecks, deleted or not, sorted."""
    periods = {period.id: period.start_date for period in all_periods(user_id)}
    transactions = sorted(
        (periods[row.pay_period_id], row.id, row.name, row.status_id,
         row.is_deleted)
        for row in _db.session.query(Transaction).filter(
            Transaction.pay_period_id.in_(list(periods)),
        )
    )
    transfers = sorted(
        (periods[row.pay_period_id], row.id, row.status_id, row.is_deleted)
        for row in _db.session.query(Transfer).filter(
            Transfer.pay_period_id.in_(list(periods)),
        )
    )
    return transactions, transfers


class TestAnAddUndoneLeavesNothingBehind:
    """C18-b's door and this one, in turn, with the ledger re-synced between.

    The seeded owner (10 fortnightly paychecks from 2026-01-02, books open
    2024-01-04) with a monthly bill and a monthly transfer from 2025-11-24,
    both populated -- so the paychecks "Add earlier" records receive template
    rows and a recurring transfer, which the removal must take with them
    (R-PC109's "template rows go").  Between the add and the
    removal the ledger is re-synced, as any loan or balance door does, which
    files the books' opening (dated 2024-01-04, before every paycheck) in the
    earliest paycheck -- an added one (R-PC114's case).
    """

    def test_paydays_eras_rows_and_the_posted_ledger_come_back(
        self, app, db, seed_user, seed_periods,
    ):
        """Add 3, re-sync, remove them.

        Compared: the paydays, the stored eras, every row's period, id,
        name, status and delete flag, every transfer's period, id, status
        and delete flag, and every posted total per scenario and ledger
        account.  Not compared: journal entry ROWS, which a re-sync appends to
        by design (a reversal and a re-post), and amounts on rows, which no
        door here writes.

        The rows compared carried their transfer link, and the template bill
        was counted with ``transfer_id IS NULL``, until plan step
        ``balance:X-bi-6-4d-2`` made a transfer one row (ruling
        **R-BAL258**): no row holds a link since
        (``ck_transactions_names_no_transfer``), so both went -- every link
        was ``None`` and the count read 2 without the term (measured).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            # Savings' books must reach the added paychecks, or the books
            # bound (pay_calendar:C18-a) keeps the transfer out of them.
            restate_account_opening(db.session, savings, date(2023, 6, 1))
            # From 2025-11-24: the 11-24 and 12-24 occurrences fall in the
            # 11-21 and 12-19 paychecks the add records, 01-24 on in the
            # seeded ones.
            _monthly_bill_from(seed_user, date(2025, 11, 24))
            _monthly_transfer_from(seed_user, savings, date(2025, 11, 24))
            populate_in_a_fresh_pass(user_id, {period.id for period in seed_periods})
            db.session.commit()
            paydays, eras = _paydays(user_id), _stored_eras(user_id)
            rows = _rows_by_period(user_id)
            totals = pay_period_locks.posted_totals(user_id)

            head = _added_head(user_id, 3)
            added = {period.id for period in head}
            loan_posting_service.resync_user_loan_postings(user_id)
            account_posting_service.resync_user_account_anchor_postings(user_id)
            db.session.commit()
            assert _entries_in(added), "the re-sync must file the opening in the head"
            assert _db.session.query(Transfer).filter(
                Transfer.pay_period_id.in_(added),
            ).count() == 2, "the added paychecks must hold the recurring transfer"
            assert _db.session.query(Transaction).filter(
                Transaction.pay_period_id.in_(added),
            ).count() == 2, "the added paychecks must hold the template bill"

            removed = pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            )
            db.session.commit()

            assert removed == 3
            assert _paydays(user_id) == paydays
            assert _stored_eras(user_id) == eras
            assert _rows_by_period(user_id) == rows
            assert pay_period_locks.posted_totals(user_id) == totals
            assert_pay_period_invariants(db.session, user_id)
