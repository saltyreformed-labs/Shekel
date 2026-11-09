"""Tests for pay-period CRUD slice (d): truncate (the first destructive op).

Truncate deletes the schedule tail via a single bulk DELETE (PostgreSQL
cascades its transactions and transfers).
Two gates run first: the hard lock classifier (historical / settled /
anchor / rule -- never overridable) and the broadened discard gate
(hand-entered / override / Credit-Cancelled rows -- overridable with
``confirm_discard``).

Because this is the highest-stakes operation in the feature, the suite
carries all four disciplines: structural invariants after every
successful delete (Discipline 1), a hand-computed retained-window balance
(Discipline 2), the production integrity checker (Discipline 3), and the
adversarial refusal tests that assert a bad state is BLOCKED and nothing
is deleted (Discipline 4).  See
``docs/plans/implementation_plan_pay_period_crud.md``.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    PostingSourceEnum,
    StatusEnum,
)
from app.exceptions import (
    PayPeriodDiscardRequired,
    PayPeriodLocked,
    PayPeriodUnresolved,
)
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import (
    account_posting_service,
    pay_period_admin,
    transfer_service,
)
from app.services.ledger_lines import linked_key
from app.services.pay_period_locks import PeriodLockReason
# The APP's civil day, never ``date.today()`` -- these fixtures build periods
# that must CONTAIN the day the account factory stamps on its origination
# assertion, and that day is the display-timezone one (CI runs a zone where
# the two differ, deliberately).
from app.utils.dates import display_today
from scripts.integrity_check import (
    check_balance_anomalies,
    check_referential_integrity,
)
from tests._ledger_reads import (
    owner_worked_out_lines,
    worked_out_totals,
)
from tests._test_helpers import (
    record_paydays_across_a_hole,
    rhythm_of,
    add_txn,
    all_periods,
    assert_pay_period_invariants,
    bare_expense_template,
    create_loan_account,
    create_savings_account,
    derived_span,
    last_covered_day,
    make_cadence_rule,
    make_expense_template,
    make_transfer_template,
    populate_in_a_fresh_pass,
    restate_account_opening,
    seam_cash_balance_at,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD
from app.models.amount_ownership import AmountOwnership


def _future_periods(db_session, seed_user, count=6, start=date(2026, 7, 3)):
    """Generate `count` biweekly FUTURE periods (indices 1..count)."""
    periods = record_paydays_across_a_hole(
        user_id=seed_user["user"].id,
        first_payday=start,
        num_periods=count,
        rhythm=rhythm_of(14),
    )
    db_session.commit()
    return periods


def _openings_filed_on(db_session, user_id, day):
    """Return the paychecks the worked-out ledger files the openings dated *day* in.

    Asserts both an account's and a loan's opening are among them, so a case
    reading it cannot pass on lines it never found.
    """
    openings = {
        ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING),
        ref_cache.posting_source_id(PostingSourceEnum.LOAN_OPENING),
    }
    lines = [
        line for _scenario_id, line in owner_worked_out_lines(db_session, user_id)
        if line.source_kind_id in openings and line.entry_date == day
    ]
    assert {line.source_kind_id for line in lines} == openings
    return {line.pay_period_id for line in lines}


def _count_periods(db_session, user_id):
    """Count the user's pay periods."""
    return db_session.query(PayPeriod).filter_by(user_id=user_id).count()


def _txns_in(db_session, period_id):
    """Count all transactions physically held in a period (by id).

    Takes an int id, not a PayPeriod object, so callers can query a
    period AFTER truncate has bulk-deleted (and ``expire_all``-ed) it
    without tripping ``ObjectDeletedError`` on a stale instance.
    """
    return db_session.query(Transaction).filter_by(pay_period_id=period_id).count()


def _make_adhoc_transfer(db_session, seed_user, to_account, period):
    """Create an ad-hoc (no template) projected transfer in a period."""
    xfer = transfer_service.create_transfer(transfer_service.TransferSpec(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        amount_ownership=AmountOwnership.own(Decimal("150.00")),
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        category_id=None,
    ))
    db_session.flush()
    return xfer


class TestTruncateHappyPath:
    """Truncate removes the tail and only the tail."""

    def test_deletes_only_indices_above_keep(self, app, db, seed_user):
        """Indices > keep_through go; indices <= keep_through stay."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            user_id = seed_user["user"].id

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[2].id,
            )
            db.session.commit()

            assert deleted == 3  # indices 4, 5, 6
            remaining = {
                derived_span(p).period_index
                for p in all_periods(user_id)
            }
            # Bootstrap (0) + kept future indices 1..3.
            assert remaining == {0, 1, 2, 3}
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))

    def test_cascade_removes_transactions(self, app, db, seed_user):
        """Deleting a period cascades its transactions away."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            make_expense_template(db.session, seed_user)
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            db.session.commit()
            doomed_id = periods[3].id  # index 4; capture before deletion
            keep_period_id = periods[1].id
            assert _txns_in(db.session, doomed_id) == 1

            pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=keep_period_id,
            )
            db.session.commit()
            # The deleted period's row is gone with it.
            assert _txns_in(db.session, doomed_id) == 0
            assert_pay_period_invariants(db.session, user_id)

    def test_cascade_removes_the_doomed_periods_transfer(self, app, db, seed_user):
        """A transfer in a deleted period goes with it.

        Until plan step ``balance:X-bi-6-4d-2`` the case also read that
        neither of the transfer's two shadow rows survived in the deleted
        period.  A transfer is one row since, and no row could name one
        (``ck_transactions_names_no_transfer``, until plan step
        balance:X-bi-6-5 dropped the column), so that count found no row to
        count.  Renamed from
        ``test_cascade_removes_transfers_and_both_shadows``.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            make_transfer_template(db.session, seed_user, savings)
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            db.session.commit()
            doomed_id = periods[3].id  # capture before deletion
            keep_period_id = periods[1].id
            assert db.session.query(Transfer).filter_by(
                pay_period_id=doomed_id,
            ).count() == 1
            doomed_transfer_id = db.session.query(Transfer).filter_by(
                pay_period_id=doomed_id,
            ).one().id

            # confirm_discard not needed: template transfers are regenerable.
            pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=keep_period_id,
            )
            db.session.commit()
            assert db.session.query(Transfer).filter_by(
                pay_period_id=doomed_id,
            ).count() == 0
            # The transfer itself is gone, not moved to a surviving period.
            assert db.session.query(Transfer).filter_by(
                id=doomed_transfer_id,
            ).count() == 0
            assert_pay_period_invariants(db.session, user_id)

    def test_idempotent_noop_through_the_last_period(self, app, db, seed_user):
        """Keeping through the LAST period deletes nothing.

        Truncate's no-op case.  It used to be spelled ``keep_through_index=999``
        -- an ordinal above every real one -- and plan step C3-a removed the
        spelling with the ordinal: an id above every real id names no period,
        which is now a REFUSAL rather than a no-op (see
        ``TestTruncateRefusesAnIdItCannotResolve``).  Naming the actual last
        period is the same question asked of the surviving key.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            user_id = seed_user["user"].id
            before = _count_periods(db.session, user_id)

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[-1].id,
            )
            db.session.commit()
            assert deleted == 0
            assert _count_periods(db.session, user_id) == before


    def test_balances_correct_after_truncate(self, app, db, seed_user):
        """Disciplines 1-3: the retained-window balance is unchanged.

        Anchor $1000 at the bootstrap (index 0, no expense); a $1200
        every-period expense fills indices 1..6.  Truncating to keep index
        3 removes 4..6 but leaves the projection for index 3 exactly
        1000 - 3*1200 = -2600, both before and after.
        """
        account = seed_user["account"]
        scen = seed_user["scenario"].id
        user_id = seed_user["user"].id
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            make_expense_template(db.session, seed_user, amount="1200.00")
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            db.session.commit()

            before = seam_cash_balance_at(
                account, scen, last_covered_day(periods[2]),  # index 3
            )
            assert before == Decimal("-2600.00")  # 1000 - 3*1200

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[2].id,
            )
            db.session.commit()
            assert deleted == 3

            after = seam_cash_balance_at(
                account, scen, last_covered_day(periods[2]),
            )
            assert after == before  # retained window untouched
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))


# Three ACCOUNT_ANCHOR cases were DELETED at plan step X-f1c3c (ruling R-EO):
# truncate blocking on a period an account anchored to, the same for a period
# holding no correction, and the raw bulk-DELETE raising an IntegrityError on
# the anchor FK.  All three graded a lock and an FK that are gone -- neither an
# account nor a balance assertion references a pay period, so no period delete
# can strand one.  ``LEDGER_POSTINGS``, the lock on a period's POSTED state, went
# at plan task plan#2777 (ruling plan#2782), its cases inverted in this class.


class TestTruncateRefusesAnIdItCannotResolve:
    """Finding P13: the wire key names a ROW, so an unowned one is refused.

    Until plan step C3-a the truncate form posted ``period_index``, and the
    service compared it with ``>``.  Every value the comparison accepted
    "worked": an ordinal from a stale page named whichever period now sits at
    that position, and one above the end was a silent no-op.  Keyed on ``id``
    there is exactly one correct answer -- the row -- and everything else is
    refused before the classifier, the discard gate or the DELETE runs.
    """

    def test_an_id_that_names_no_period_is_refused(self, app, db, seed_user):
        """A forged or stale id deletes nothing and says so."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            user_id = seed_user["user"].id
            before = _count_periods(db.session, user_id)
            absent = max(p.id for p in periods) + 1000

            with pytest.raises(PayPeriodUnresolved) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=absent,
                )

            # App text shows a person no system id (the developer,
            # 2026-09-23; this line asserted the echo until pay_calendar:C21,
            # approved under rule 5): the id goes to the ACCESS log only.
            assert str(absent) not in str(excinfo.value)
            assert _count_periods(db.session, user_id) == before

    def test_another_owners_period_is_refused_identically(
        self, app, db, seed_user, seed_second_user,
    ):
        """A real id belonging to someone else raises the SAME message.

        The security response rule: "not yours" and "does not exist" must be
        indistinguishable, or this door reports whether another owner's period
        id exists.  Both are asserted against the same rendered message, and
        the second owner keeps every period they had.
        """
        with app.app_context():
            _future_periods(db.session, seed_user, count=3)
            user_id = seed_user["user"].id
            theirs = seed_second_user["bootstrap_period"]
            second_id = seed_second_user["user"].id
            before = _count_periods(db.session, second_id)

            with pytest.raises(PayPeriodUnresolved) as owned_exc:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=theirs.id,
                )
            with pytest.raises(PayPeriodUnresolved) as absent_exc:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=theirs.id + 10_000,
                )

            # Same sentence, only the echoed id differs -- no existence oracle.
            assert str(owned_exc.value).replace(
                str(theirs.id), "X",
            ) == str(absent_exc.value).replace(str(theirs.id + 10_000), "X")
            assert _count_periods(db.session, second_id) == before

class TestTruncateHardLocks:
    """Hard locks refuse the delete and change nothing (Discipline 4)."""

    def test_settled_transaction_blocks_and_deletes_nothing(
        self, app, db, seed_user,
    ):
        """A settled txn in the window raises PayPeriodLocked; nothing goes."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            add_txn(
                db.session, seed_user, periods[2], "Paid Bill", "100.00",
                status_enum=StatusEnum.DONE,
            )
            db.session.commit()
            before = _count_periods(db.session, user_id)

            with pytest.raises(PayPeriodLocked):
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=periods[1].id,
                )

            assert _count_periods(db.session, user_id) == before
            assert db.session.query(Transaction).filter_by(
                pay_period_id=periods[2].id,
            ).count() == 1
            assert_pay_period_invariants(db.session, user_id)

    def test_historical_period_blocks(self, app, db, seed_user):
        """A historical period in the window is hard-locked."""
        with app.app_context():
            user_id = seed_user["user"].id
            # Spanning past->future: early indices have already ended.
            record_paydays_across_a_hole(
                user_id=user_id,
                first_payday=date(2026, 1, 2), num_periods=14, rhythm=rhythm_of(14),
            )
            db.session.commit()
            before = _count_periods(db.session, user_id)

            with pytest.raises(PayPeriodLocked) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id,
                    keep_through_period_id=seed_user["bootstrap_period"].id,
                )
            assert PeriodLockReason.HISTORICAL in excinfo.value.blocking.values()
            assert _count_periods(db.session, user_id) == before

    def test_a_paycheck_holding_only_corrections_truncates_and_they_re_file(
        self, app, db, seed_user,
    ):
        """An account's and a loan's openings in a to-delete paycheck: no lock.

        Ruling plan#2782.  The worked-out ledger files each opening by its
        day, so in the paycheck covering it before the truncate and in the
        last kept one after, clamped, with every per-key total unchanged.

        **The schedule is generated AROUND today deliberately** (plan step
        X-ai-r): the account's observed day (today) falls inside one of its
        paychecks, production's shape, and the books are restated into that
        paycheck, which is legal because the account records no movement
        (ruling **R-HG**).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            periods = _future_periods(
                db.session, seed_user, count=6,
                start=display_today() - timedelta(days=28),
            )
            anchored = next(
                period for period in periods
                if period.start_date <= display_today() <= last_covered_day(period)
            )
            previous = max(
                (p for p in periods if p.start_date < anchored.start_date),
                key=lambda p: p.start_date,
            )
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            restate_account_opening(db.session, savings, anchored.start_date)
            account_posting_service.sync_account_anchor_postings_all_scenarios(
                savings.id,
            )
            loan = create_loan_account(
                seed_user, db.session, name="Car Loan",
                principal=Decimal("20000.00"), rate=Decimal("0.05"),
                origination_date=anchored.start_date,
            )
            db.session.commit()
            scenario = seed_user["scenario"].id
            day, last_kept = anchored.start_date, previous.id
            assert _openings_filed_on(db.session, user_id, day) == {anchored.id}
            totals = worked_out_totals(db.session, user_id)
            assert totals[(scenario, linked_key(savings))] == Decimal("500.00")
            assert totals[(scenario, linked_key(loan))] == Decimal("-20000.00")
            before = _count_periods(db.session, user_id)
            doomed = [p for p in periods if p.start_date > previous.start_date]

            assert pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=last_kept,
            ) == len(doomed)
            db.session.commit()

            assert _count_periods(db.session, user_id) == before - len(doomed)
            assert _openings_filed_on(db.session, user_id, day) == {last_kept}
            assert worked_out_totals(db.session, user_id) == totals
            assert_pay_period_invariants(db.session, user_id)

    def test_a_rules_stated_start_no_longer_blocks_a_truncate(
        self, app, db, seed_user,
    ):
        """The period a rule starts in is truncatable, and the bound survives.

        ``PeriodLockReason.RECURRENCE_ANCHOR`` hard-locked it until plan step
        R7b-4, and had to: ``start_period_id`` was ``ON DELETE SET NULL``, so
        truncating the period the FK named silently ERASED the rule's opening
        bound.  The bound is a DATE now, which no cascade reaches, so the
        truncate proceeds and the rule keeps saying what it said.

        Both halves are asserted, because deleting a hard lock is only safe if
        the thing it protected really does survive: the periods go, and the
        rule's ``starts_on`` is unchanged afterwards.

        **Two things rotted at plan step R7c-b and both are fixed here.**  The
        assertion read ``start_date``, a column the write door stopped writing
        -- so it read ``None`` forever and held for any truncate, including one
        that erased the bound.  And the rule was built by
        ``make_every_period_rule``, which authors ``starts_on`` at the
        SCHEDULE'S OPENING; the periods below start at 2026-07-03, so the rule
        no longer started in the truncated period at all and the case had
        stopped describing its own subject.  It is authored ON that period now.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=6)
            user_id = seed_user["user"].id
            stated_start = periods[2].start_date  # index 3
            # The definition first, then the cadence onto it (plan step
            # R-F6): a rule carries its owner's FK, so it cannot be written
            # before there is an owner.
            rule = make_cadence_rule(
                bare_expense_template(db.session, seed_user), EVERY_PERIOD,
                starts_on=stated_start,
            )
            assert rule.starts_on == stated_start, (
                "the rule must start IN the period this truncates, or the "
                "case is not about the lock it names"
            )
            # Read the id BEFORE the delete: the instance is expired
            # afterwards and attribute access raises rather than answering.
            truncated_id = periods[2].id
            db.session.commit()

            pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[1].id,
            )
            db.session.commit()

            assert db.session.query(PayPeriod).filter_by(
                id=truncated_id,
            ).one_or_none() is None
            assert rule.starts_on == stated_start


class TestTruncateDiscardGate:
    """The overridable discard gate (Discipline 4)."""

    def test_adhoc_row_requires_confirm_then_proceeds(self, app, db, seed_user):
        """A hand-entered row blocks without confirm, deletes with it."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            add_txn(db.session, seed_user, periods[2], "Cash", "50.00")
            db.session.commit()
            before = _count_periods(db.session, user_id)

            with pytest.raises(PayPeriodDiscardRequired) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=periods[1].id,
                )
            assert excinfo.value.count == 1
            assert _count_periods(db.session, user_id) == before

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[1].id,
                confirm_discard=True,
            )
            db.session.commit()
            assert deleted == 2
            assert_pay_period_invariants(db.session, user_id)

    def test_override_row_requires_confirm(self, app, db, seed_user):
        """A template row marked override needs confirmation."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            make_expense_template(db.session, seed_user)
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            txn = db.session.query(Transaction).filter_by(
                pay_period_id=periods[2].id,
            ).one()
            txn.is_override = True
            db.session.commit()

            with pytest.raises(PayPeriodDiscardRequired):
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=periods[1].id,
                )

    def test_cancelled_row_requires_confirm(self, app, db, seed_user):
        """A Cancelled template row needs confirmation (broadened gate).

        Cancelled is not settled, so it is not hard-locked, but the user's
        cancel decision is not reproducible by regeneration -- so the gate
        must warn before discarding it.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            make_expense_template(db.session, seed_user)
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            txn = db.session.query(Transaction).filter_by(
                pay_period_id=periods[2].id,
            ).one()
            txn.status_id = ref_cache.status_id(StatusEnum.CANCELLED)
            db.session.commit()

            with pytest.raises(PayPeriodDiscardRequired):
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=periods[1].id,
                )

    def test_a_rule_less_definitions_projected_row_requires_confirm(
        self, app, db, seed_user,
    ):
        """A row no rule would write back needs confirmation, link or not.

        Plan step balance:X-bi-7a, the transaction half of ledger row
        **BAL-492**: the gate's transaction arm read ``template_id IS NULL``
        in SQL, so a rule-less definition's row -- template-linked, Projected,
        not overridden -- was counted REGENERABLE and truncate promised back
        a row no rule will write.  The arm loads the rows and asks each one
        ``recurs`` now (the shape ruling **R-BAL19** gave the companion
        query), so this row counts exactly as the hand-entered one above.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            template = make_expense_template(db.session, seed_user)
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            template.recurrence_rule = None
            db.session.commit()
            kept = db.session.query(Transaction).filter_by(
                pay_period_id=periods[2].id,
            ).one()
            assert kept.template_id == template.id
            assert kept.is_override is False
            before = _count_periods(db.session, user_id)

            with pytest.raises(PayPeriodDiscardRequired) as excinfo:
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=periods[1].id,
                )
            # Two rows in the tail (periods 3 and 4), each unrecoverable.
            assert excinfo.value.count == 2
            assert _count_periods(db.session, user_id) == before

    def test_projected_template_rows_need_no_confirm(self, app, db, seed_user):
        """Plain projected template rows are regenerable -- no confirm gate."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            make_expense_template(db.session, seed_user)
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            db.session.commit()

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[1].id,
            )
            db.session.commit()
            assert deleted == 2
            assert_pay_period_invariants(db.session, user_id)

    def test_recurring_transfer_needs_no_confirm(self, app, db, seed_user):
        """A template transfer is regenerable; its shadows do not trip the gate.

        Transfer shadows always carry template_id IS NULL, so a naive
        ``template_id IS NULL`` gate would falsely flag every recurring
        transfer.  The refined predicate counts transfers on their own
        table, so a template transfer needs no confirmation.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            make_transfer_template(db.session, seed_user, savings)
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            db.session.commit()

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=periods[1].id,
            )
            db.session.commit()
            assert deleted == 2
            assert_pay_period_invariants(db.session, user_id)

    def test_adhoc_transfer_requires_confirm(self, app, db, seed_user):
        """An ad-hoc (no-template) transfer is not regenerable -- confirm needed."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            _make_adhoc_transfer(db.session, seed_user, savings, periods[2])
            db.session.commit()

            with pytest.raises(PayPeriodDiscardRequired):
                pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=periods[1].id,
                )
