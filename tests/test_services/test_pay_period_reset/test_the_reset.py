"""The bounded reset: it wipes every period and rebuilds the schedule with every balance and rule's
start intact, and it refuses a settled row or an invalid cadence before it deletes anything,
changing nothing.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.exceptions import PayPeriodResetBlocked, ValidationError
from app.enums import StatusEnum, TxnTypeEnum
from app.models.account import Account, AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app.services.pay_calendar import calendar_for
from app.services.pay_rhythm import FixedDays
from app.services.recurrence import reauthor_rule, recurrence_spec, resolve
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.services import (
    cash_ledger,
    match_withdrawal,
    movement_removal,
    pay_period_admin,
    pay_period_write,
    pay_schedule_service,
)
from scripts.integrity_check import check_balance_anomalies, check_referential_integrity
from tests._test_helpers import (
    rhythm_of,
    add_txn,
    all_periods,
    assert_pay_period_invariants,
    create_savings_account,
    derived_span,
    last_covered_day,
    make_cadence_rule,
    make_expense_template,
    make_transfer_template,
    seam_cash_balance_at,
)
from tests.oracles.recurrence_baseline import EVERY_N_PERIODS

from ._helpers import _NEW_START, _seed_old_schedule, _reset_and_populate


def _all_indices(user_id):
    """The set of period_index values the user currently has."""
    return {derived_span(p).period_index for p in all_periods(user_id)}


def _make_every_n_template(db_session, seed_user, start_period, interval_n=2):
    """Build an EVERY_N_PERIODS expense template phased to ``start_period``.

    States the FIRST OCCURRENCE -- ``start_period``'s own payday -- and lets
    the write door derive the phase from it (plan step R7b-4), which is the
    exact phased state a reset must re-base onto the new schedule.  The
    ``offset_periods`` column comes out of that derivation against the OLD
    schedule, so it really is the stale value a pre-reset rule holds, and the
    point of the case is what happens to it.

    **Authored through the write door since plan step R7c-b** rather than
    written column by column: the two-axis columns are ``NOT NULL``, and the
    phase is now derived on every read rather than trusted off the column, so
    hand-writing a phase would state a fact nothing consults.

    Returns the created template (flushed; the caller commits).
    """
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name="Every-other Bill",
        default_amount=Decimal("300.00"),
    )
    db_session.add(template)
    db_session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    make_cadence_rule(
        template,
        EVERY_N_PERIODS,
        starts_on=start_period.start_date,
        interval_n=interval_n,
    )
    return template


class TestResetHappyPath:
    """Reset wipes every period and rebuilds the schedule."""

    def test_wipes_all_and_the_balance_survives(self, app, db, seed_user):
        """Every old period is deleted and the asserted balance is untouched.

        It proved the DEFERRED-FK path end to end: the account's anchor period
        was among the deleted rows, and the commit succeeded only because the
        FK was validated after the account had been re-pointed.  Rulings R-EH
        and R-EO deleted both the account's anchor column and the assertion's
        pay period, so there is no FK to defer, nothing to re-point, and no
        window in which the schema is inconsistent.  What is worth proving is
        what the deferral existed to protect: the user's balance comes through
        a schedule rebuild unchanged.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            account = seed_user["account"]
            old_period_ids = {
                p.id for p in all_periods(user_id)
            }
            balance_before = cash_ledger.resolve_anchor(account).balance

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=6,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            # Whole schedule rebuilt from index 0; every old period gone.
            assert _all_indices(user_id) == {0, 1, 2, 3, 4, 5}
            assert [derived_span(p).period_index for p in new_periods] == [0, 1, 2, 3, 4, 5]
            for old_id in old_period_ids:
                assert db.session.get(PayPeriod, old_id) is None

            # The asserted balance is untouched by the rebuild.
            account = db.session.get(Account, account.id)
            assert cash_ledger.resolve_anchor(account).balance == balance_before
            assert balance_before == Decimal("1000.00")

            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))

    def test_the_reset_preserves_every_balance_assertion(
        self, app, db, seed_user,
    ):
        """A schedule rebuild does not touch what the user said their bank held.

        **This test asserted the OPPOSITE until ruling R-EO** (plan step
        X-f1c3b), and the inversion is the finding.  It read: "the cascade
        deletes the old ``AccountAnchorHistory`` rows along with their pay
        periods, so after reset the account has exactly one history row -- the
        new origination".  That was a true description of a defect.  A balance
        assertion is a fact about a BANK -- "on day D this account held $B" --
        and it stays true however the user re-schedules their paychecks;
        ``account_anchor_history.pay_period_id`` filed it under a budgeting
        artifact on an ``ON DELETE CASCADE`` FK, so a reset destroyed it.
        Measured on the developer's production data before the column was
        dropped: **all 78 assertions deleted, 9 fabricated
        ``"origination (pay-period reset)"`` rows written in their place.**

        The row-for-row comparison is what makes this falsifiable: asserting
        only a COUNT would pass against a reset that deleted every real
        assertion and wrote the same number of synthetic ones, which is very
        nearly what the old behaviour did.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            account = seed_user["account"]

            def _assertions():
                return sorted(
                    (row.id, row.anchor_balance, row.observed_on)
                    for row in db.session.query(AccountAnchorHistory)
                    .filter_by(account_id=account.id)
                )

            before = _assertions()
            assert before, "fixture must write at least one assertion"

            pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=4,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            after = _assertions()
            # **The named diagnostic MOVED ONTO this assertion at plan step
            # X-f1e2**, and nothing is ungraded by the move.  A second line
            # used to scan the post-reset snapshot for a row whose ``notes``
            # read ``"origination (pay-period reset)"`` -- the exact string the
            # old behaviour fabricated -- purely so a failure would NAME the
            # defect; its own comment recorded that it was a diagnostic and not
            # an independent grader, because the row-for-row equality here
            # already fails for any fabricated row and fails first.  Ruling
            # R-ES deleted the ``notes`` column, so the string cannot be
            # searched for; the message it carried is stated here instead.
            # What is lost is the ability to distinguish "fabricated by the
            # reset" from "fabricated by something else", which no assertion
            # in this test ever made use of.
            assert after == before, (
                "the reset changed this account's assertion history.  Measured "
                "on production before ruling R-EO: a reset deleted all 78 "
                "assertions and wrote 9 fabricated replacements, because the "
                "row was filed under a pay period on a CASCADE FK.  A balance "
                "assertion is a fact about a BANK and survives any schedule "
                f"rebuild.\nbefore={before}\nafter ={after}"
            )

    def test_the_door_leaves_the_rebuilt_schedule_EMPTY(
        self, app, db, seed_user,
    ):
        """The door wipes, rebuilds, re-syncs -- and generates nothing.

        The door's half of ruling **R-R38**, over BOTH engines: the fixture
        seeds a transaction template AND a transfer template, because a door
        that regressed to running only one of the two would otherwise pass.
        ``test_repopulates_transactions_and_transfers`` runs both halves over
        this same fixture and finds a row and a transfer in every period, so
        this case cannot pass by the templates being unable to generate.

        *The transaction assertion carried a ``transfer_id=None`` filter until
        an adversarial review of this step: copied from the positive sibling,
        where the fixture made it load-bearing while a transfer still wrote two
        shadow rows (the sibling's term went at plan step
        ``balance:X-bi-6-4d-2``), and inert here in a way that narrowed what
        the negative case could see.*
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            make_expense_template(db.session, seed_user, amount="1200.00")
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            make_transfer_template(db.session, seed_user, savings)
            db.session.commit()

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=4,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            for period in new_periods:
                assert db.session.query(Transaction).filter_by(
                    pay_period_id=period.id,
                ).count() == 0, (
                    "reset_pay_periods generated a recurring row; since R-R38 "
                    "it rebuilds the schedule and the caller populates it"
                )
                assert db.session.query(Transfer).filter_by(
                    pay_period_id=period.id,
                ).count() == 0, (
                    "reset_pay_periods generated a recurring TRANSFER; the "
                    "transfer engine is the other half of the repopulation "
                    "and the door must run neither"
                )

    def test_balance_preserved_and_correct_after_reset(self, app, db, seed_user):
        """Disciplines 2 + 3: anchor balance preserved, balances recompute.

        The Checking's $1000 assertion is dated 2024-01-05 (the seed's
        bootstrap day), before the rebuilt schedule, and the reset keeps it
        row for row, so the new index 0 (the period containing today) opens
        at $1000; a $1200 every-period expense repopulates all six new
        periods, index 0 included.  A balance read at the CLOSE of a
        period counts that period's own net, so the end balance at the close of
        index ``n`` is ``1000 - (n + 1) * 1200``.
        """
        account = seed_user["account"]
        scen = seed_user["scenario"].id
        user_id = seed_user["user"].id
        with app.app_context():
            _seed_old_schedule(db.session, seed_user)
            make_expense_template(db.session, seed_user, amount="1200.00")
            db.session.commit()

            new_periods = _reset_and_populate(
                user_id, new_start_date=_NEW_START, num_periods=6,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            # End of index 0: 1000 - 1*1200.
            assert seam_cash_balance_at(
                account, scen, last_covered_day(new_periods[0]),
            ) == Decimal("-200.00")
            # End of index 5: 1000 - 6*1200.
            assert seam_cash_balance_at(
                account, scen, last_covered_day(new_periods[5]),
            ) == Decimal("-6200.00")

            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))

    def test_repopulates_transactions_and_transfers(self, app, db, seed_user):
        """The rebuilt periods get recurring transactions AND transfers.

        Each new period holds one template expense and one transfer, and the
        transfer path exercises ``assert_pay_period_invariants``' transfer
        invariant: every transfer sits in one of the owner's own periods.  The
        helper also counted two shadows per transfer until plan step
        ``balance:X-bi-6-4d-2``, and the expense count carried a
        ``transfer_id=None`` term that kept those shadows out of it; no row
        names a transfer since (``ck_transactions_names_no_transfer`` refused
        one until plan step balance:X-bi-6-5 dropped the column), so the term
        changed no count and is gone.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            make_expense_template(db.session, seed_user, amount="1200.00")
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            make_transfer_template(db.session, seed_user, savings)
            db.session.commit()

            new_periods = _reset_and_populate(
                user_id, new_start_date=_NEW_START, num_periods=4,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            for period in new_periods:
                # One template expense per period.
                assert db.session.query(Transaction).filter_by(
                    pay_period_id=period.id,
                ).count() == 1
                # One transfer per period.
                assert db.session.query(Transfer).filter_by(
                    pay_period_id=period.id,
                ).count() == 1
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_referential_integrity(db.session))

    def test_a_rules_stated_start_survives_the_wipe(self, app, db, seed_user):
        """The reset does not touch the rule, and its bound is still there.

        The wipe used to SET NULL every rule's ``start_period_id`` -- the FK
        is ``ON DELETE SET NULL`` and reset deletes every period -- so the
        reset had to capture the anchored rules first and re-point them at the
        rebuilt schedule's first period afterwards, or a rule with an explicit
        start silently became one with none.

        Plan step R7b-4 made the bound a DATE, which the cascade cannot reach.
        There is nothing to capture, nothing to re-point, and the stated start
        is still stated when the reset returns -- so the rule opens where the
        user said, measured against whatever schedule now exists.

        **Asserted on ``starts_on``, which is where the fact lives since plan
        step R7c-b.**  This read ``start_date`` and set it directly, and both
        halves rotted with that step: the write door stopped writing that
        column and ``start_period_id`` entirely, so BOTH assertions read
        ``None``-forever values and held for any reset -- including one that
        rewrote the rule from end to end, which is the whole state they exist
        to refuse.  The rule is authored through the door here for the same
        reason: a hand-set column is not a bound the application would ever
        produce.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            # A date inside the OLD schedule and a year before the rebuilt
            # one, so a reset that re-pointed anything could not leave it here.
            stated_start = date(2026, 1, 30)
            template = make_expense_template(db.session, seed_user)
            # The shared factory gives the template an every-paycheck rule
            # opening at the schedule's own start; this case needs one opening
            # INSIDE the old schedule, so the rule it carries is re-pointed
            # rather than a second one authored beside it -- which
            # ``uq_recurrence_rules_transaction_template_id`` refuses since
            # plan step R-F6, and refuses for the reason this line relies on:
            # a definition has ONE cadence.
            rule = template.recurrence_rule
            reauthor_rule(
                rule,
                replace(recurrence_spec(rule), starts_on=stated_start),
                calendar_for(user_id),
            )
            db.session.commit()
            assert rule.starts_on == stated_start
            assert stated_start < _NEW_START

            pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=4,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            rule = db.session.get(RecurrenceRule, rule.id)
            assert rule.starts_on == stated_start

    def test_every_n_rule_rephased_onto_new_schedule(self, app, db, seed_user):
        """An EVERY_N_PERIODS rule re-phases onto the rebuilt schedule.

        The regression the re-point half existed for: a rule phased to an OLD
        odd index must, after a reset, generate every other period STARTING at
        the new schedule's opening (indices 0, 2, 4) -- not on the stale odd
        phase (1, 3, 5).

        **Nothing re-points it now** (plan step R7b-4).  The rule keeps its
        stated bound, which precedes the rebuilt schedule, so the effective
        start is ``max(new opening payday, start_date)`` -- the new opening,
        index 0 -- and the phase is that paycheck's ordinal modulo 2, which is
        0.  The generated rows are the same ones the re-point produced,
        without a writer that had to remember to produce them.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            old_periods = all_periods(user_id)
            # Phase the rule to an OLD odd index (3) -> offset 1 under n=2.
            template = _make_every_n_template(
                db.session, seed_user, old_periods[3], interval_n=2,
            )
            rule = template.recurrence_rule
            assert resolve(
                recurrence_spec(rule), calendar_for(rule.user_id),
            ).offset_periods == 1
            db.session.commit()

            new_periods = _reset_and_populate(
                user_id, new_start_date=_NEW_START, num_periods=6,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            # Generated rows land on indices 0, 2, 4 -- phased to the new
            # first period, not the stale 1, 3, 5.
            counts = {
                derived_span(p).period_index: db.session.query(Transaction).filter_by(
                    pay_period_id=p.id, template_id=template.id,
                ).count()
                for p in new_periods
            }
            assert counts == {0: 1, 1: 0, 2: 1, 3: 0, 4: 1, 5: 0}
            assert_pay_period_invariants(db.session, user_id)

    def test_multiple_accounts_all_reanchored(self, app, db, seed_user):
        """Every account keeps its own asserted balance through the reset.

        It used to prove that the deferred FK let the reset re-point BOTH
        accounts' dangling anchors before the single commit validated.
        Rulings R-EH and R-EO deleted the account's anchor column and the
        assertion's pay period, so nothing dangles and nothing re-anchors.
        What stays is what the re-point protected: each of two accounts keeps
        its own, distinct balance.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            db.session.commit()

            pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=4,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            checking = db.session.get(Account, checking.id)
            savings = db.session.get(Account, savings.id)
            assert cash_ledger.resolve_anchor(checking).balance == Decimal("1000.00")
            assert cash_ledger.resolve_anchor(savings).balance == Decimal("500.00")
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))

    def test_not_yet_anchored_user_can_reset(self, app, db, bare_user):
        """A user with periods but no accounts resets cleanly.

        bare_user has a schedule (generated below) but no account, so there
        is nothing to re-anchor; reset must wipe and rebuild without
        touching the (empty) account set.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id, date(2026, 1, 2), num_periods=4, rhythm=rhythm_of(14),
            )
            db.session.commit()

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=3,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            assert [derived_span(p).period_index for p in new_periods] == [0, 1, 2]
            assert _all_indices(user_id) == {0, 1, 2}
            assert_pay_period_invariants(db.session, user_id)

    def test_persists_new_cadence(self, app, db, seed_user):
        """Reset stores the new cadence and builds at it."""
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=3,
                rhythm=rhythm_of(7),
            )
            db.session.commit()

            assert pay_schedule_service.resolve_cadence(user_id) == FixedDays(7)
            assert (
                last_covered_day(new_periods[0]) - new_periods[0].start_date
            ).days + 1 == 7


class TestResetRefusals:
    """The bounded reset refuses unsafe states (Discipline 4)."""

    def test_settled_transaction_blocks_and_changes_nothing(
        self, app, db, seed_user,
    ):
        """ANY settled transaction refuses the reset; the DB is unchanged.

        The gate runs before any delete, under the owner's write lock that
        the one caller took before the door ran, so a refused reset leaves the
        schedule and the settled row intact -- never a partial wipe.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            periods = all_periods(user_id)
            settled = add_txn(
                db.session, seed_user, periods[2], "Paycheck", "2000.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
            )
            db.session.commit()

            before_ids = {p.id for p in periods}

            with pytest.raises(PayPeriodResetBlocked) as exc_info:
                pay_period_admin.reset_pay_periods(
                    user_id, new_start_date=_NEW_START, num_periods=4,
                    rhythm=rhythm_of(14),
                )
            db.session.rollback()

            assert exc_info.value.settled_count == 1
            after_ids = {p.id for p in all_periods(user_id)}
            assert after_ids == before_ids  # nothing deleted
            assert db.session.get(Transaction, settled.id) is not None
            assert_pay_period_invariants(db.session, user_id)

    def test_soft_deleted_settled_does_not_block(self, app, db, seed_user):
        """A soft-deleted settled row does not count -- matches the classifier.

        The gate mirrors the lock classifier's notion of "settled"
        (non-deleted), so a removed settled row neither locks a period nor
        blocks a reset.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            periods = all_periods(user_id)
            deleted = add_txn(
                db.session, seed_user, periods[2], "Paycheck", "2000.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
            )
            # The state the delete door leaves (ruling R-CC75): its payment
            # taken off through the one removal act, then the row hidden --
            # in that order, because the database refuses a payment written
            # under a hidden row (R-CC89) and a row hidden holding one
            # (R-CC92).  Rule-5 re-expressions, developer-confirmed 2026-09-23.
            movement_removal.remove_movements(
                list(deleted.entries), user_id,
                because=match_withdrawal.LEFT_THE_BOOKS,
                press=None,
            )
            deleted.is_deleted = True
            db.session.commit()

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=3,
                rhythm=rhythm_of(14),
            )
            db.session.commit()
            assert [derived_span(p).period_index for p in new_periods] == [0, 1, 2]
            assert_pay_period_invariants(db.session, user_id)

    def test_invalid_cadence_rolls_back_partial_wipe(self, app, db, seed_user):
        """An invalid cadence refuses the reset; the schedule is unchanged.

        This said the wipe ran before the cadence was validated and that the
        route's rollback restored the deleted schedule; neither is true.
        ``record_paydays`` asks every precondition, the cadence bound
        (``pay_schedule_service.reject_out_of_range_cadence``) among them,
        before it issues a statement, so a refused batch deletes nothing and
        the rollback (simulated here, as the route's) has nothing of the
        door's to undo.  What the case grades holds either way: every old
        period survives.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            before_ids = {p.id for p in all_periods(user_id)}

            with pytest.raises(ValidationError):
                pay_period_admin.reset_pay_periods(
                    user_id, new_start_date=_NEW_START, num_periods=4,
                    rhythm=rhythm_of(0),  # the cadence bound rejects < 1
                )
            db.session.rollback()

            after_ids = {p.id for p in all_periods(user_id)}
            assert after_ids == before_ids
            assert_pay_period_invariants(db.session, user_id)
