"""A coverage withdrawal is ACCEPTED (R-PC1's financial half was deleted, developer 2026-08-11): an
append past a late row, a truncate that strands a settled row or a transfer, a regenerate and
the rolling top-up.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import text

from app import ref_cache
from app.enums import StatusEnum
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import (
    pay_period_admin,
    pay_period_rolling,
    pay_period_write,
    pay_schedule_service,
    transfer_service,
)
from tests._test_helpers import (
    rhythm_of,
    add_txn,
    restate_fixture_era,
    create_savings_account,
    freeze_today,
    record_paydays_across_a_hole,
    transfer_side_settle_day,
)
from app.models.amount_ownership import AmountOwnership

from ._helpers import _paydays


class TestACoverageWithdrawalIsAccepted:
    """Shortening the schedule past a settled row's cash day is ALLOWED.

    **Ruling R-PC1's financial half was DELETED (developer, 2026-08-11), and
    this class is what replaced it.**  It refused any write that moved a day
    from COVERED to UNCOVERED underneath a SETTLED row filed in a surviving
    period, on the claim that stranding such a day reproduces ``balance:N-128``
    -- the two halves of the cash period view disagreeing.  The claim was false.

    ``_cash_periods._assemble_figures`` values each column at that period's OWN
    ``end_date`` and computes ``period_timing`` as ``moved - net``, so a settle
    day past the last reported end is absent from BOTH sides of ruling R-K's
    identity and cancels.  The money reports as a timing remainder -- the row
    ruling R-DH split out to carry exactly this -- and the balance is right
    either way: on the shortened schedule's last day the bank genuinely had not
    taken the money yet.  ``test_cash_period_view.py``'s
    ``test_a_settle_day_past_the_window_keeps_every_column_exact`` is the
    arithmetic; these are the doors.

    **Production is SILENT on this rule rather than supporting its removal**, and
    an adversarial review had to point that out: **0** of its settled rows fall
    outside the schedule's coverage, so the refused state has never occurred
    there.  What production shows is the design the deletion rests on -- 21 of
    160 settled rows settle outside their OWN paycheck (2026-08-11), carried by
    the remainder with nothing refusing.  What decided it is the measured COST:
    5 of the owner's 61 truncation points refused, one over three rows totalling
    ``$177.47`` that cleared the bank ONE day late.  Its message offered three
    ways out -- re-date the row, move it, or abandon the schedule change -- the
    first two of which falsify when money moved.
    """

    #: ``seed_periods``' last paycheck: payday 2026-05-08, covering to
    #: 2026-05-21 (10 fortnightly periods from 2026-01-02).  Named so the
    #: arithmetic below reads as dates rather than as indices.
    _LAST_PAYDAY = date(2026, 5, 8)
    _LAST_COVERED = date(2026, 5, 21)

    def _settled_row(self, db_session, seed_user, period, day):
        """File a SETTLED row in *period* whose money moved on *day*."""
        return add_txn(
            db_session, seed_user, period, "Insurance renewal", "340.00",
            status_enum=StatusEnum.DONE, due_date=period.start_date,
            settled_on=day,
        )

    def test_an_append_past_a_late_row_is_ACCEPTED(
        self, app, db, seed_user, seed_periods,
    ):
        """The false refusal R-PC1's first wording would have produced.

        The last paycheck ends 2026-05-21 and holds a row that SETTLED
        2026-05-30 -- a state production reaches routinely (7 of its 61
        paychecks hold a row 1 to 17 days past their own end, and the last
        one's margin is zero days).  Appending 2026-05-22 leaves that end
        exactly where it was, so the row's situation is unchanged and the write
        is allowed.  "Refuse any row dated on or after the new payday" would
        have refused it -- and since the rolling top-up appends with no handler
        on ``/grid`` and ``/dashboard``, that refusal was a permanent 500 on
        both pages.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            self._settled_row(
                db.session, seed_user, seed_periods[-1], date(2026, 5, 30),
            )
            db.session.commit()
            assert _paydays(user_id)[-1] == (
                self._LAST_PAYDAY, self._LAST_COVERED, 9,
            )

            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 5, 22),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()

            rows = _paydays(user_id)
            assert rows[-2] == (self._LAST_PAYDAY, self._LAST_COVERED, 9)
            assert rows[-1] == (date(2026, 5, 22), date(2026, 6, 4), 10)

    def test_a_truncate_that_strands_a_settled_row_is_ACCEPTED(
        self, app, db, seed_user, seed_periods,
    ):
        """The case the deleted rule refused, and it must now go through.

        A payday recorded at 2026-07-01 stretches the 2026-05-08 paycheck to
        2026-06-30, and it holds a row that SETTLED 2026-06-15.  Truncating
        that successor away drops the paycheck back to its cadence projection,
        2026-05-21, so 2026-06-15 belongs to no paycheck afterwards.

        The truncate runs.  The row is NOT touched -- it keeps its period, its
        settle day and its existence -- because the schedule shrinking is not a
        fact about when the bank moved money, and re-dating it to satisfy a
        calendar edit would falsify the one clock the ledger reads.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            row = self._settled_row(
                db.session, seed_user, seed_periods[-1], date(2026, 6, 15),
            )
            row_id, home_period_id = row.id, seed_periods[-1].id
            # Seven weeks past the 05-08 record: a hole the writer refuses
            # (plan step C17-c-2a, ruling R-PC67), built through the tree's
            # helper because the subject here is the TRUNCATE that follows.
            record_paydays_across_a_hole(
                user_id, date(2026, 7, 1), 1, rhythm_of(14),
            )
            db.session.commit()
            assert _paydays(user_id)[-2] == (
                self._LAST_PAYDAY, date(2026, 6, 30), 9,
            )

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=seed_periods[-1].id,
                confirm_discard=True,
            )
            db.session.commit()

            assert deleted == 1
            assert _paydays(user_id)[-1] == (
                self._LAST_PAYDAY, self._LAST_COVERED, 9,
            )
            survivor = db.session.get(Transaction, row_id)
            assert survivor is not None
            assert survivor.pay_period_id == home_period_id
            # The settle day is now outside every paycheck, and untouched.
            assert survivor.settled_on == date(2026, 6, 15)

    def test_a_truncate_that_strands_NOTHING_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):
        """The control, and the point is that it reads IDENTICALLY to the above.

        Same truncate, and the row settled 2026-05-20 -- a day the shortened
        paycheck still covers.  It was the accepted half of a pair the rule
        split; it is now simply the same outcome, which is what "the writer has
        no opinion about a settled row's cash day" means.  Kept rather than
        merged so a future rule that starts discriminating on that day again
        fails one of the two.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            self._settled_row(
                db.session, seed_user, seed_periods[-1], date(2026, 5, 20),
            )
            # Seven weeks past the 05-08 record: a hole the writer refuses
            # (plan step C17-c-2a, ruling R-PC67), built through the tree's
            # helper because the subject here is the TRUNCATE that follows.
            record_paydays_across_a_hole(
                user_id, date(2026, 7, 1), 1, rhythm_of(14),
            )
            db.session.commit()

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=seed_periods[-1].id,
                confirm_discard=True,
            )
            db.session.commit()

            assert deleted == 1
            assert _paydays(user_id)[-1] == (
                self._LAST_PAYDAY, self._LAST_COVERED, 9,
            )

    def test_a_stranded_TRANSFER_keeps_both_shadows_and_its_settle_day(
        self, app, db, seed_user, seed_periods,
    ):
        """The transfer and its two sides' day survive a truncate that strands them.

        A transfer has no ``settled_on`` COLUMN -- the day lives on its two
        sides' payment records, which hang off the transfer since plan step
        ``balance:X-bi-6-4d-2`` (ruling **R-BAL88**; on its two shadow
        ``Transaction`` rows until then) -- so a shortening that puts that day
        outside every paycheck touches BOTH sides at once.  Graded here rather
        than left to the transaction case because the failure mode is
        different: a writer that "repaired" a stranded transfer by deleting
        it, or by re-dating or deleting a side's record, would break this
        shape while looking correct on the other.  The writer touches none of
        them, which is what makes that unreachable.  The day is planted on the
        two records and read back off each side's own, which fails on a side
        holding none (ruling **R-BAL167** class 1, where the shadows carried
        it).  The case also counted the shadows and their two types
        (Invariants 1 and 2: exactly two shadows, never orphaned) until plan
        step ``balance:X-bi-6-4d-2`` made a transfer one row (ruling
        **R-BAL258**), so those reads went with them.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            xfer = transfer_service.create_transfer(transfer_service.TransferSpec(
                user_id=user_id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                pay_period_id=seed_periods[-1].id,
                scenario_id=seed_user["scenario"].id,
                amount_ownership=AmountOwnership.own(Decimal("150.00")),
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                category_id=None,
            ))
            xfer_id = xfer.id
            # Created Projected, then settled: the create makes no settled
            # transfer (ruling R-BAL379).
            transfer_service.settle_transfer(xfer_id, user_id)
            db.session.execute(
                text(
                    "UPDATE budget.transaction_entries SET settled_on = :day "
                    "WHERE expense_transfer_id = :t OR income_transfer_id = :t"
                ),
                {"day": date(2026, 6, 15), "t": xfer_id},
            )
            # Seven weeks past the 05-08 record: a hole the writer refuses
            # (plan step C17-c-2a, ruling R-PC67), built through the tree's
            # helper because the subject here is the TRUNCATE that follows.
            record_paydays_across_a_hole(
                user_id, date(2026, 7, 1), 1, rhythm_of(14),
            )
            db.session.commit()

            deleted = pay_period_admin.truncate_pay_periods(
                user_id, keep_through_period_id=seed_periods[-1].id,
                confirm_discard=True,
            )
            db.session.commit()

            assert deleted == 1
            assert _paydays(user_id)[-1] == (
                self._LAST_PAYDAY, self._LAST_COVERED, 9,
            )
            assert db.session.get(Transfer, xfer_id) is not None
            assert {
                transfer_side_settle_day(db.session, xfer_id, account_id).day
                for account_id in (seed_user["account"].id, savings.id)
            } == {date(2026, 6, 15)}

    def test_a_REGENERATE_derives_ONCE_over_the_end_state(
        self, app, db, seed_user, seed_periods, monkeypatch, caplog,
    ):
        """Retire and record are ONE write, and this is what that buys.

        Regenerate RETIRES a tail and RECORDS a new one.  While those were two
        write calls, everything downstream saw the schedule BETWEEN them -- an
        interval that exists for one statement -- and the deleted coverage rule
        measured it as a false refusal: a settled row that cleared after its own
        paycheck refused every regenerate, naming a day the rebuilt schedule
        covers comfortably.

        That rule is gone; the composition is not, because
        ``_write_derivation`` still runs against whatever it is handed.  Applied
        separately it shortens the newly-last survivor to a cadence projection
        and logs a WARNING repair, then immediately undoes both.  One call, one
        derivation, and the intermediate shape is never materialised.

        Today is pinned inside the 2026-03-13 paycheck so that regenerate
        RETAINS it (with the five before it) and rebuilds from 2026-03-27: the
        retained schedule covers only to 2026-03-26 for one statement, and the
        rebuilt one runs to 2026-07-16 -- past the 2026-06-15 the row settled
        on.
        """
        freeze_today(monkeypatch, date(2026, 3, 20))
        with app.app_context():
            user_id = seed_user["user"].id
            retained = next(
                period for period in seed_periods
                if period.start_date == date(2026, 3, 13)
            )
            self._settled_row(
                db.session, seed_user, retained, date(2026, 6, 15),
            )
            db.session.commit()

            with caplog.at_level(
                "WARNING", logger="app.services.pay_period_write",
            ):
                pay_period_admin.regenerate_pay_periods(
                    user_id, date(2026, 3, 27), 8, rhythm_of(14),
                    confirm_discard=True,
                )
            db.session.commit()

            rows = _paydays(user_id)
            assert rows[-1] == (date(2026, 7, 3), date(2026, 7, 16), 13)
            # The day the two-call version refused on is covered by the result.
            assert any(
                start <= date(2026, 6, 15) <= end for start, end, _index in rows
            )
            # And the retained tail was never materialised at its intermediate
            # shape: a two-call regenerate rewrites 2026-03-13's end to the
            # cadence projection and logs that as a repair before undoing it.
            assert [
                record for record in caplog.records
                if getattr(record, "event", None) == "pay_periods_rematerialised"
            ] == []

    def test_the_rolling_topup_APPENDS_where_it_used_to_decline(
        self, app, db, seed_user, seed_periods,
    ):
        """The read-path write has nothing left to swallow.

        ``/grid`` and ``/dashboard`` call the top-up with no handler of their
        own, so anything raised inside it is a 500 on both of the app's main
        screens.  The deleted coverage rule COULD raise here, which is why
        ``top_up_rolling_window`` carried a ``try/except`` and an event
        (``pay_periods_topup_refused``) whose whole purpose was to turn a
        business refusal into a silent no-op on a read path.  An opportunistic
        writer needing a swallow was the clearest evidence the rule did not
        belong there; both are gone, and the top-up simply appends.

        The state that used to trip it needs a stored cadence SHORTER than the
        schedule it generated -- no door can create that since C3-b's cadence
        rule, so it is built by restating the era directly, the shape
        pre-C3-b data carries (finding **P28**).  With cadence 3 the append
        lands 2026-05-11 and pulls the last paycheck's end back to 2026-05-10,
        leaving the row that settled 2026-05-18 outside every paycheck.  The
        row is untouched and the window fills.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            row = self._settled_row(
                db.session, seed_user, seed_periods[-1], date(2026, 5, 18),
            )
            row_id = row.id
            pay_schedule_service.set_rolling(
                user_id, enabled=True, target_periods=11,
            )
            # The stored rhythm is an ERA since plan step C17-a, restated here
            # as one 3-day era phased on the last recorded payday -- a day the
            # 14-day record already holds, so the grid the top-up continues
            # runs 05-11, 05-14 from it.
            restate_fixture_era(user_id, self._LAST_PAYDAY, 3)
            db.session.commit()

            created = pay_period_rolling.top_up_rolling_window(
                user_id, as_of=date(2026, 1, 5),
            )
            db.session.commit()

            assert len(created) == 1
            rows = _paydays(user_id)
            assert rows[-1] == (date(2026, 5, 11), date(2026, 5, 13), 10)
            # The previous last paycheck gave up 2026-05-11..2026-05-21 to the
            # append, so the settle day below is now covered by nothing.
            assert rows[-2] == (self._LAST_PAYDAY, date(2026, 5, 10), 9)
            survivor = db.session.get(Transaction, row_id)
            assert survivor.settled_on == date(2026, 5, 18)
            assert not any(
                start <= date(2026, 5, 18) <= end for start, end, _index in rows
            )
