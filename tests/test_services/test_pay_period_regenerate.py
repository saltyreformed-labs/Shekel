"""Tests for pay-period CRUD slice (e): regenerate (rebuild the future tail).

Regenerate = truncate the not-yet-started, unlocked tail, then generate a
fresh schedule from a corrected start/cadence.  It composes truncate (so it
inherits the hard-lock and discard gates) with generate, whose writer
(``record_paydays``) mints a pay era when the batch records a payday on a
rhythm the covering era does not hold.

**It does NOT repopulate; the ROUTE does, and that is ruling R-R38** (plan
step R7d-c-1).  The door records the rebuilt tail EMPTY and returns, because
the read pass the recurrence resolves in may only be opened above the service
layer and only after the periods exist.  A case here that asserts recurring
ROWS therefore runs ``_regenerate_and_populate``, which is what the route
runs; a case about the door's own contract calls the door alone.  ``POST
/pay-periods/regenerate`` is graded in the
``tests/test_routes/test_pay_period_admin/`` package.

``today`` is pinned with ``freeze_today`` so the past / current / future
split is deterministic regardless of when the suite runs.  All four
disciplines apply: structural invariants (Discipline 1), hand-computed
as-of balances across the retained and rebuilt windows (Discipline 2),
the production integrity checker (Discipline 3), and adversarial refusal
tests (Discipline 4).  The four disciplines are named in
``docs/plans/historical/implementation_plan_pay_period_crud.md``, a
historical record that governs nothing.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.exceptions import (
    PayPeriodDiscardRequired,
    PayPeriodLocked,
    ValidationError,
)
from app.enums import StatusEnum
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.routes._period_population import populate_new_periods
from app.services import (
    pay_period_admin,
    pay_period_write,
    pay_schedule_service,
)
from app.services.pay_rhythm import FixedDays
from scripts.integrity_check import (
    check_balance_anomalies,
    check_referential_integrity,
)
from tests._test_helpers import (
    rhythm_of,
    add_txn,
    all_periods,
    assert_pay_period_invariants,
    derived_span,
    freeze_today,
    last_covered_day,
    make_expense_template,
    populate_in_a_fresh_pass,
    record_paydays_across_a_hole,
    seam_cash_balance_at,
)


# Pinned "today": with the 2026-05-01 biweekly schedule below, indices
# 1..3 have ended (historical), index 4 (06-12..06-25) is in progress, and
# index 5 onward is the not-yet-started, rebuildable tail.
FROZEN_TODAY = date(2026, 6, 15)
_SPAN_START = date(2026, 5, 1)


@pytest.fixture(autouse=True)
def _freeze(monkeypatch):
    """Pin ``date.today()`` to FROZEN_TODAY for every test in this module."""
    freeze_today(monkeypatch, FROZEN_TODAY)


def _spanning_periods(db_session, seed_user, count=8):
    """Generate biweekly periods spanning FROZEN_TODAY (indices 1..count).

    Index 4 (06-12..06-25) is the in-progress period; 1..3 are historical;
    5.. are the rebuildable future tail.
    """
    # Two years past the 2024 opening payday: a hole the writer refuses
    # (plan step C17-c-2a, ruling R-PC67), built through the tree's helper.
    periods = record_paydays_across_a_hole(
        seed_user["user"].id, _SPAN_START, count, rhythm_of(14),
    )
    db_session.commit()
    return periods


def _count_periods(db_session, user_id):
    """Count the user's pay periods."""
    return db_session.query(PayPeriod).filter_by(user_id=user_id).count()


def _index_set(user_id):
    """The set of period_index values the user currently has."""
    return {
        derived_span(p).period_index
        for p in all_periods(user_id)
    }


def _regenerate_and_populate(user_id, **kwargs):
    """Run BOTH halves of a regenerate, exactly as the route does.

    ``regenerate_pay_periods`` truncates and records the rebuilt tail;
    ``populate_new_periods`` opens the generate pass afterwards and fills it.
    Ruling **R-R38**: the pass may only be opened above the service layer, and
    only after the write.

    **It confirmed the gap gate by default from plan step
    ``pay_calendar:C14-f`` until ``C17-c-2a`` deleted that gate** (ruling
    **R-PC67**): a rebuild whose first payday skips a whole paycheck of the
    owner's plan is REFUSED by the writer now, from every door, and there is
    no answer that lets it through.  So every case here rebuilds from a day
    inside the window the plan allows -- on or after the plan's next payday
    past the last kept one, and before the one after that -- which is the
    only rebuild a real owner can perform.  *The 2024 opening payday
    ``seed_user`` carries sits years below those blocks; the fixtures that
    record a 2026 block beside it write the hole through
    :func:`~tests._test_helpers.record_paydays_across_a_hole`, and a rebuild
    that keeps a 2026 prefix never sees it.*

    Args:
        user_id: The owning user's id.
        **kwargs: Forwarded to
            :func:`~app.services.pay_period_admin.regenerate_pay_periods`.

    Returns:
        The rebuilt periods, now populated.
    """
    new_periods = pay_period_admin.regenerate_pay_periods(user_id, **kwargs,
                  )
    populate_new_periods(user_id, new_periods)
    return new_periods


class TestRegenerateHappyPath:
    """Regenerate keeps the locked/current prefix and rebuilds the tail."""

    def test_period_starting_today_is_kept(self, app, db, seed_user):
        """A period starting exactly on today (payday) is the in-progress
        period and must NOT be rebuilt.

        Regression for the boundary off-by-one: containment is inclusive at
        both ends (``start_date <= today <= end_date``), so the period that
        starts on today is current; regenerate must keep it, not pull it
        into the rebuildable tail.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            # Index 1 starts ON the frozen today -- the current period.
            periods = record_paydays_across_a_hole(
                user_id, FROZEN_TODAY, num_periods=4, rhythm=rhythm_of(14),
            )
            db.session.commit()
            today_index = derived_span(periods[0]).period_index
            today_start = periods[0].start_date
            new_start = last_covered_day(periods[0]) + timedelta(days=1)

            pay_period_admin.regenerate_pay_periods(
                user_id, new_start_date=new_start, num_periods=2,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            # Keyed on the PAYDAY, which is the row's identity since plan
            # step ``pay_calendar:C4-c`` dropped the ordinal column; ``.one()``
            # is the survival half (exactly one row still opens on that day)
            # and the ordinal is asserted from the derivation, so the case
            # still says the in-progress paycheck kept its place in the order.
            kept = db.session.query(PayPeriod).filter_by(
                user_id=user_id, start_date=today_start,
            ).one()
            assert derived_span(kept).period_index == today_index
            assert_pay_period_invariants(db.session, user_id)

    def test_rebuilds_tail_keeps_current_and_historical(
        self, app, db, seed_user,
    ):
        """Indices 0..4 (bootstrap, past, current) stay; 5.. are rebuilt."""
        with app.app_context():
            periods = _spanning_periods(db.session, seed_user, count=8)
            user_id = seed_user["user"].id
            current_index = derived_span(periods[3]).period_index  # index 4
            current_start = periods[3].start_date
            new_start = last_covered_day(periods[3]) + timedelta(days=1)

            new_periods = pay_period_admin.regenerate_pay_periods(
                user_id, new_start_date=new_start, num_periods=3,
                rhythm=rhythm_of(14),
                          )
            db.session.commit()

            # Bootstrap (0) + retained 1..4 + freshly built 5..7.
            assert _index_set(user_id) == {0, 1, 2, 3, 4, 5, 6, 7}
            assert [derived_span(p).period_index for p in new_periods] == [5, 6, 7]
            assert new_periods[0].start_date == new_start
            # The in-progress period was not touched.
            kept = db.session.query(PayPeriod).filter_by(
                user_id=user_id, start_date=current_start,
            ).one()
            assert derived_span(kept).period_index == current_index
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))

    def test_the_door_leaves_the_rebuilt_tail_EMPTY(self, app, db, seed_user):
        """The door rebuilds the tail and generates nothing into it.

        The door's half of ruling **R-R38**.  The case below runs both halves
        over the same fixture and finds one row per period, so this one cannot
        pass by the template being unable to generate at all.
        """
        with app.app_context():
            periods = _spanning_periods(db.session, seed_user, count=6)
            user_id = seed_user["user"].id
            make_expense_template(db.session, seed_user)
            new_start = last_covered_day(periods[3]) + timedelta(days=1)

            new_periods = pay_period_admin.regenerate_pay_periods(
                user_id, new_start_date=new_start, num_periods=3,
                rhythm=rhythm_of(14),
                          )
            db.session.commit()
            for period in new_periods:
                assert db.session.query(Transaction).filter_by(
                    pay_period_id=period.id,
                ).count() == 0, (
                    "regenerate_pay_periods generated a recurring row; since "
                    "R-R38 it records the tail and the caller populates it"
                )

    def test_rebuilt_periods_get_recurring_rows(self, app, db, seed_user):
        """Regenerate + populate fills the rebuilt tail from the templates."""
        with app.app_context():
            periods = _spanning_periods(db.session, seed_user, count=6)
            user_id = seed_user["user"].id
            make_expense_template(db.session, seed_user)
            new_start = last_covered_day(periods[3]) + timedelta(days=1)

            new_periods = _regenerate_and_populate(
                user_id, new_start_date=new_start, num_periods=3,
                rhythm=rhythm_of(14),
            )
            db.session.commit()
            for period in new_periods:
                assert db.session.query(Transaction).filter_by(
                    pay_period_id=period.id,
                ).count() == 1
            assert_pay_period_invariants(db.session, user_id)

    def test_persists_new_cadence(self, app, db, seed_user):
        """Regenerate stores the new cadence and builds at it."""
        with app.app_context():
            periods = _spanning_periods(db.session, seed_user, count=6)
            user_id = seed_user["user"].id
            new_start = last_covered_day(periods[3]) + timedelta(days=1)

            new_periods = pay_period_admin.regenerate_pay_periods(
                user_id, new_start_date=new_start, num_periods=2,
                rhythm=rhythm_of(7),
                          )
            db.session.commit()

            assert pay_schedule_service.resolve_cadence(user_id) == FixedDays(7)
            assert (
                last_covered_day(new_periods[0]) - new_periods[0].start_date
            ).days + 1 == 7

    def test_balances_correct_after_regenerate(self, app, db, seed_user):
        """Disciplines 1-3: retained balance unchanged, rebuilt window correct.

        Anchor $1000 at the bootstrap (index 0, no expense); a $1200
        every-period expense fills indices 1..8.  Regenerate keeps 1..4
        and rebuilds 5..8 (repopulated with the same expense), so the end
        balance at index 4 stays 1000 - 4*1200 = -3800 and the new index 8
        is 1000 - 8*1200 = -8600.
        """
        account = seed_user["account"]
        scen = seed_user["scenario"].id
        user_id = seed_user["user"].id
        with app.app_context():
            periods = _spanning_periods(db.session, seed_user, count=8)
            make_expense_template(db.session, seed_user, amount="1200.00")
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            db.session.commit()
            retained_end = last_covered_day(periods[3])  # index 4
            new_start = retained_end + timedelta(days=1)

            before = seam_cash_balance_at(
                account, scen, retained_end,
            )
            assert before == Decimal("-3800.00")  # 1000 - 4*1200

            _regenerate_and_populate(
                user_id, new_start_date=new_start, num_periods=4,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            after_retained = seam_cash_balance_at(
                account, scen, retained_end,
            )
            assert after_retained == before  # retained window untouched

            last = all_periods(user_id)[-1]  # index 8
            assert seam_cash_balance_at(
                account, scen, last_covered_day(last),
            ) == Decimal("-8600.00")  # 1000 - 8*1200
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))


class TestRegenerateWhenTheWholeScheduleIsRebuildable:
    """The boundary cases at both ends of the keep-through computation.

    ``pay_period_gates.regenerate_keep_through_period`` decides where the
    rebuildable tail opens.

    **Both were unreachable in this suite until plan step C3-a**, and an
    adversarial review is what found that.  Every other test here uses
    ``seed_user``, whose bootstrap period is pinned at 2024-01-05 while every
    frozen today is in 2026 -- so the first period is always historical, always
    locked, and never the rebuildable boundary.  The "keep NONE of them" arm
    therefore had no test at all, on the single most destructive branch in the
    module: it deletes every pay period the owner has, cascading their
    transactions, their transfers and their journal entries.

    ``bare_user`` has no bootstrap period, so a schedule built entirely on one
    side of today reaches each arm.
    """

    def test_an_all_future_schedule_is_rebuilt_from_nothing(
        self, app, db, bare_user,
    ):
        """Every period is unlocked and future, so regenerate replaces them all.

        ``pay_period_gates.regenerate_keep_through_period`` answers ``None``
        here -- the rebuildable tail starts at the very first period -- and
        ``pay_period_gates.gate_deletable_tail`` reads that as "delete every
        one".  The empty state is transient inside the one transaction: the
        fresh schedule is generated before this call returns, so no committed
        state is ever period-less.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            old = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 7, 3),
                num_periods=4, rhythm=rhythm_of(14),
            )
            db.session.commit()
            old_ids = {p.id for p in old}

            new_periods = pay_period_admin.regenerate_pay_periods(
                user_id, date(2026, 8, 7), 3, rhythm_of(14),
                          )
            db.session.commit()

            # Not one original row survived, and the count is exactly the
            # rebuild -- no retained prefix, because there was nothing to keep.
            surviving = all_periods(user_id)
            assert {p.id for p in surviving} & old_ids == set()
            assert len(surviving) == 3
            assert len(new_periods) == 3
            assert [p.start_date for p in surviving] == [
                date(2026, 8, 7), date(2026, 8, 21), date(2026, 9, 4),
            ]
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_referential_integrity(db.session))

    def test_an_all_past_schedule_degrades_to_an_append(
        self, app, db, bare_user,
    ):
        """No period is rebuildable, so the truncate is a no-op and it appends.

        The other arm: the loop finds no not-yet-started unlocked period and
        falls through to the LAST one, which
        ``pay_period_gates.gate_deletable_tail`` then selects nothing after.
        The docstring has always claimed this "degrades to an append from
        ``new_start_date``"; nothing asserted it.

        **The append is bounded like every batch** (plan step
        ``pay_calendar:C17-c-2a``, ruling R-PC67): this case used to restart
        an all-past schedule on 07-03, five paychecks past its last one, and
        that is ledger row P80's set through the one door the writer still
        let it through.  The plan's next payday after 02-13 is 02-27 and the
        one after it 03-13, so 07-03 is refused and 02-27 appends.
        """
        user_id = bare_user["user"].id
        with app.app_context():
            old = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=4, rhythm=rhythm_of(14),
            )
            db.session.commit()
            old_ids = {p.id for p in old}

            with pytest.raises(ValidationError, match="before 2026-03-13"):
                pay_period_admin.regenerate_pay_periods(
                    user_id, date(2026, 7, 3), 2, rhythm_of(14),
                )
            db.session.rollback()

            new_periods = pay_period_admin.regenerate_pay_periods(
                user_id, date(2026, 2, 27), 2, rhythm_of(14),
            )
            db.session.commit()

            surviving = all_periods(user_id)
            # All four historical periods kept, two appended: 4 + 2.
            assert old_ids <= {p.id for p in surviving}
            assert len(surviving) == 6
            assert len(new_periods) == 2
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_referential_integrity(db.session))


class TestRegenerateRefusals:
    """Regenerate inherits truncate's lock + discard gates (Discipline 4)."""

    def test_settled_period_in_tail_refuses(self, app, db, seed_user):
        """A settled period inside the rebuildable tail blocks the rebuild."""
        with app.app_context():
            periods = _spanning_periods(db.session, seed_user, count=8)
            user_id = seed_user["user"].id
            add_txn(
                db.session, seed_user, periods[5], "Paid", "100.00",  # index 6
                status_enum=StatusEnum.DONE,
            )
            db.session.commit()
            before = _count_periods(db.session, user_id)
            new_start = last_covered_day(periods[3]) + timedelta(days=1)

            with pytest.raises(PayPeriodLocked):
                pay_period_admin.regenerate_pay_periods(
                    user_id, new_start_date=new_start, num_periods=3,
                    rhythm=rhythm_of(14),
                )
            db.session.rollback()
            assert _count_periods(db.session, user_id) == before

    def test_adhoc_row_requires_confirm_then_proceeds(self, app, db, seed_user):
        """A hand-entered row in the tail needs confirmation."""
        with app.app_context():
            periods = _spanning_periods(db.session, seed_user, count=8)
            user_id = seed_user["user"].id
            add_txn(db.session, seed_user, periods[5], "Cash", "50.00")  # idx 6
            db.session.commit()
            new_start = last_covered_day(periods[3]) + timedelta(days=1)

            with pytest.raises(PayPeriodDiscardRequired):
                pay_period_admin.regenerate_pay_periods(
                    user_id, new_start_date=new_start, num_periods=3,
                    rhythm=rhythm_of(14),
                )
            db.session.rollback()

            # With confirmation it rebuilds the tail (discarding the row).
            new_periods = pay_period_admin.regenerate_pay_periods(
                user_id, new_start_date=new_start, num_periods=3,
                rhythm=rhythm_of(14),
                confirm_discard=True,
            )
            db.session.commit()
            assert [derived_span(p).period_index for p in new_periods] == [5, 6, 7]
            assert_pay_period_invariants(db.session, user_id)

    def test_overlapping_new_start_rejected_and_rolls_back(
        self, app, db, seed_user,
    ):
        """A new_start below the forward-only floor is rejected, atomically.

        This said the truncate ran before the writer validated the start and
        that the route's rollback restored the deleted tail; neither is true.
        ``record_paydays`` asks the floor
        (``pay_period_batch.reject_backward_payday``) before it issues a
        statement, so a refused rebuild deletes nothing and the rollback
        (simulated here, as the route's) has nothing of the door's to undo.
        What the case grades holds either way: the period count is unchanged.

        **The refused date moved in at plan step C3-b.**  It was five days
        into the retained current period, which the OLD guard refused because
        it bounded on the retained ``end_date``; C3-b's floor was the retained
        PAYDAY plus two, which accepted that date and shortened the retained
        period (the "correct my cadence going forward" case the
        implementation plan's section 6 names).  The floor is now the day the
        owner's NEXT paycheck opens, so every day inside the retained paycheck
        is refused, five days in among them; one day in refuses under both
        floors.
        """
        with app.app_context():
            periods = _spanning_periods(db.session, seed_user, count=8)
            user_id = seed_user["user"].id
            before = _count_periods(db.session, user_id)
            # One day after the retained current period's PAYDAY: inside the
            # paycheck the owner already has, so below the floor, the day the
            # next one opens.
            bad_start = periods[3].start_date + timedelta(days=1)

            with pytest.raises(ValidationError):
                pay_period_admin.regenerate_pay_periods(
                    user_id, new_start_date=bad_start, num_periods=3,
                    rhythm=rhythm_of(14),
                )
            db.session.rollback()
            assert _count_periods(db.session, user_id) == before
            assert_pay_period_invariants(db.session, user_id)


class TestRegenerateResolvesItsFactsOnce:
    """One schedule read, one lock classification, one clock read.

    Plan step **C2-f3b**.  This door used to answer one question -- where does
    the rebuildable tail open, and may every period past it go -- out of THREE
    independent reads of "today" and TWO independent lock classifications:
    ``_regenerate_keep_through_period`` read ``date.today()`` for its
    not-yet-started test and again inside the classify it ran over the whole
    schedule, and ``_gate_deletable_tail`` classified the tail separately with a
    third.  Nothing was wrong on the day, because a period cannot become
    historical between two statements of one transaction -- but that is an
    argument from timing rather than from construction, and it is the shape
    ledger row **P56** and findings **P68** / **P69** record one layer up.

    Graded as a CALL COUNT because that is what the property is.  A test that
    only checked the outcome would pass on the old code, which computed the
    same answer twice.
    """

    def test_it_classifies_the_lock_state_exactly_once(
        self, app, db, seed_user, monkeypatch,
    ):
        """One ``classify_schedule_locks`` call per regenerate, over the whole schedule.

        The count is 1 rather than "at most 2", and the KEY SET is asserted too:
        a classification narrowed to the tail would satisfy a bare count while
        leaving the boundary computation to classify separately.
        """
        calls = []
        real = pay_period_admin.classify_schedule_locks

        def _counting(calendar, *, as_of):
            """Record each classification and delegate to the real one."""
            answer = real(calendar, as_of=as_of)
            calls.append((tuple(sorted(answer)), as_of))
            return answer

        monkeypatch.setattr(
            pay_period_admin, "classify_schedule_locks", _counting,
        )
        with app.app_context():
            periods = _spanning_periods(db.session, seed_user, count=8)
            user_id = seed_user["user"].id
            # Read BEFORE the rebuild: regenerate DELETES the tail, so these
            # instances are gone by the time the assertions run.
            expected_ids = {period.id for period in periods}
            expected_ids.add(seed_user["bootstrap_period"].id)

            # The tail reopens where the plan's next payday falls (06-26);
            # a day later than 07-09 would skip a whole paycheck and be
            # refused (plan step C17-c-2a, ruling R-PC67).  This case counts
            # CLASSIFICATIONS, not holes; the ceiling's own cases are below.
            pay_period_admin.regenerate_pay_periods(
                user_id, new_start_date=date(2026, 6, 26), num_periods=3,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            assert len(calls) == 1
            classified, as_of = calls[0]
            # The WHOLE schedule, so the boundary search and the refusal read
            # one answer: the bootstrap period plus the eight generated ones.
            assert set(classified) == expected_ids
            assert as_of == FROZEN_TODAY

    def test_the_day_it_decides_on_is_the_owners_civil_day(
        self, app, db, seed_user, monkeypatch,
    ):
        """``display_today``, not ``date.today`` -- ruled 2026-08-19.

        The two are pinned APART here: the process clock stays on this module's
        ``FROZEN_TODAY`` and the display clock is moved four periods forward, to
        a day on which two more periods have ended.  The door's answer must
        follow the display clock, which the assertion below reads off the value
        the classifier was actually handed.
        """
        owner_day = FROZEN_TODAY + timedelta(days=56)
        monkeypatch.setattr(
            pay_period_admin, "display_today", lambda: owner_day,
        )
        seen = []
        real = pay_period_admin.classify_schedule_locks

        def _capturing(calendar, *, as_of):
            """Record the day the classifier was asked about."""
            seen.append(as_of)
            return real(calendar, as_of=as_of)

        monkeypatch.setattr(
            pay_period_admin, "classify_schedule_locks", _capturing,
        )
        with app.app_context():
            _spanning_periods(db.session, seed_user, count=8)
            user_id = seed_user["user"].id
            assert date.today() != owner_day

            pay_period_admin.regenerate_pay_periods(
                user_id, new_start_date=owner_day + timedelta(days=14),
                num_periods=3, rhythm=rhythm_of(14),
            )
            db.session.commit()
            assert seen == [owner_day]


def _paydays(session, user_id):
    """The owner's recorded paydays, ascending."""
    return [
        period.start_date
        for period in session.query(PayPeriod)
        .filter_by(user_id=user_id).order_by(PayPeriod.start_date).all()
    ]


class TestABatchMayNotSkipAPaycheckOfThePlan:
    """Plan step ``pay_calendar:C17-c-2a``, rulings **R-PC67** and **R-PC76**.

    ``regenerate`` is the era-mint door: it KEEPS a prefix of the owner's
    history and states where the next paycheck lands, so the two can be any
    distance apart -- and ledger row **P80**'s 196-day paycheck is what a
    distance of more than one paycheck derives to.  The writer's ceiling
    (``pay_period_batch.reject_skipped_paycheck``) refuses the first new
    payday at or past the SECOND payday the plan projects after the last
    kept one, so a hole is unrepresentable rather than confirmed: the
    confirmation ``C14-f`` put here (``PayPeriodGapRequired``,
    ``Confirmations.gap``) is gone, and no argument to this door restores it.

    The fixture keeps paydays through 06-12 (the paycheck holding
    ``FROZEN_TODAY``), so the plan's next payday is 06-26 and the one after
    it 07-10: the window a rebuild may open in is ``[06-26, 07-10)``.
    """

    def test_a_batch_that_skips_a_paycheck_is_refused_and_writes_nothing(
        self, app, db, seed_user,
    ):
        """Opening ON the second planned payday is refused; the paydays are untouched."""
        with app.app_context():
            _spanning_periods(db.session, seed_user, count=8)
            user_id = seed_user["user"].id
            before = _paydays(db.session, user_id)

            with pytest.raises(ValidationError) as caught:
                pay_period_admin.regenerate_pay_periods(
                    user_id, new_start_date=date(2026, 7, 10), num_periods=3,
                    rhythm=rhythm_of(14),
                )
            db.session.rollback()

            assert "before 2026-07-10" in str(caught.value)
            assert "2026-06-26" in str(caught.value)
            # Nothing written: the paydays are exactly as they were.
            assert _paydays(db.session, user_id) == before

    def test_the_day_before_the_second_planned_payday_is_the_last_allowed(
        self, app, db, seed_user,
    ):
        """07-09 is accepted where 07-10 is refused: the ceiling is exclusive.

        The two directions of one bound.  07-09 is OFF the kept rhythm's
        grid, so it is a phase correction and mints an era there; the
        06-12 paycheck runs to 07-08, 27 days, which is what "I was paid
        late, and from then on every fortnight" derives to.
        """
        with app.app_context():
            _spanning_periods(db.session, seed_user, count=8)
            user_id = seed_user["user"].id

            pay_period_admin.regenerate_pay_periods(
                user_id, new_start_date=date(2026, 7, 9), num_periods=3,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            paydays = _paydays(db.session, user_id)
            assert date(2026, 7, 9) in paydays
            assert date(2026, 6, 26) not in paydays
            facts = pay_schedule_service.resolve_schedule(user_id)
            assert facts.eras[-1].effective_from == date(2026, 7, 9)

    def test_there_is_no_confirmation_that_lets_a_skipped_paycheck_through(
        self, app, db, seed_user,
    ):
        """The door has no gap answer to take: a hole is refused, not asked about."""
        with app.app_context():
            _spanning_periods(db.session, seed_user, count=8)
            user_id = seed_user["user"].id

            with pytest.raises(
                TypeError, match="unexpected keyword argument 'confirms'",
            ):
                # Pylint: ``unexpected-keyword-arg`` -- the call passes a gap
                # answer the door does not take; its TypeError is this case's
                # subject.
                pay_period_admin.regenerate_pay_periods(  # pylint: disable=unexpected-keyword-arg
                    user_id, new_start_date=date(2026, 7, 10), num_periods=3,
                    rhythm=rhythm_of(14), confirms=object(),
                )
            with pytest.raises(ValidationError):
                pay_period_admin.regenerate_pay_periods(
                    user_id, new_start_date=date(2026, 7, 10), num_periods=3,
                    rhythm=rhythm_of(14), confirm_discard=True,
                )
            db.session.rollback()

    def test_a_re_phase_of_a_few_days_is_NEVER_refused(
        self, app, db, seed_user,
    ):
        """The window is exactly one paycheck wide, and its floor day is accepted.

        The floor is the first projected payday past the last kept one and
        the ceiling is the SECOND.  This case opens ON the floor, so it pins
        that the floor is not tightened past it: a correction later inside the
        window is accepted, and an earlier one is refused, as every day inside
        the retained paycheck is
        (``test_overlapping_new_start_rejected_and_rolls_back``).  The
        ceiling's own pin is
        ``test_the_day_before_the_second_planned_payday_is_the_last_allowed``.
        """
        with app.app_context():
            _spanning_periods(db.session, seed_user, count=8)
            user_id = seed_user["user"].id

            # The tail reopens exactly where it stood: the floor day itself.
            pay_period_admin.regenerate_pay_periods(
                user_id, new_start_date=date(2026, 6, 26), num_periods=3,
                rhythm=rhythm_of(14),
            )
            db.session.commit()
            assert date(2026, 6, 26) in _paydays(db.session, user_id)

    def test_a_cadence_change_may_move_the_next_payday_but_not_skip_one(
        self, app, db, seed_user,
    ):
        """Weekly from 07-03 is legal; weekly from 07-10 skips the 06-26 paycheck.

        The ceiling reads the OLD era's plan -- the stored eras, not the
        batch's rhythm -- because the question is which paycheck of the
        rhythm the owner HAS would go missing.  A first draft keyed on the
        batch's own cadence would let a 7-day batch open at 07-10 (only one
        week past its own first paycheck) and derive a 28-day 06-12 paycheck
        the owner never described.
        """
        with app.app_context():
            _spanning_periods(db.session, seed_user, count=8)
            user_id = seed_user["user"].id

            with pytest.raises(ValidationError):
                pay_period_admin.regenerate_pay_periods(
                    user_id, new_start_date=date(2026, 7, 10), num_periods=3,
                    rhythm=rhythm_of(7),
                )
            db.session.rollback()

            pay_period_admin.regenerate_pay_periods(
                user_id, new_start_date=date(2026, 7, 3), num_periods=3,
                rhythm=rhythm_of(7),
            )
            db.session.commit()
            assert _paydays(db.session, user_id)[-3:] == [
                date(2026, 7, 3), date(2026, 7, 10), date(2026, 7, 17),
            ]
