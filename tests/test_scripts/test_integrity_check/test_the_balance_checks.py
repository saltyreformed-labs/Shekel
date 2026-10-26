"""The BA-* checks: an account with no balance assertion, the checks that stay warnings, and
BA-06's settle day that no paycheck covers, at each bound of the calendar and on a settled
transfer's sides.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.enums import StatusEnum
from app.services.pay_calendar import calendar_for
from tests._test_helpers import (
    account_never_asserted,
    add_txn,
    create_account_of_type,
    create_settled_transfer,
)
from scripts.integrity_check import check_balance_anomalies


# ── Balance Anomalies ────────────────────────────────────────────


class TestBalanceAnomalies:
    """Tests for BA-* balance anomaly checks."""

    @pytest.mark.usefixtures("seed_user", "seed_periods")
    def test_clean_database_no_anomalies(self, db):
        """No balance anomalies on a properly seeded database."""
        results = check_balance_anomalies(db.session)
        # 3: BA-02 ("anchor period beyond the user's last period") went with
        # the column it queried at plan step X-f1c3c and BA-01 was RE-POINTED
        # at "an account with no balance assertion at all" -- the state that
        # actually breaks a producer -- leaving 4; BA-06 was added 2026-08-11
        # with the deletion of pay_calendar C3-b's coverage rule, and BA-07
        # with pay_calendar C2-b2.  Plan step pay_calendar:C4-c then took
        # BA-03 (an ordinal gap), BA-04 (a span overlap) and BA-07 (a day
        # covered by no period) together: all three query columns that step
        # DROPPED, and all three name states the derived calendar cannot
        # express.
        assert len(results) == 3
        ba01 = next(r for r in results if r.check_id == "BA-01")
        assert ba01.passed

    @pytest.mark.usefixtures("seed_periods")
    def test_ba01_detects_account_with_no_assertion(self, db, seed_user):
        """BA-01 detects an account carrying no balance assertion at all.

        **The check was RE-POINTED at plan step X-f1c3c and became
        exercisable again.**  It used to look for "balance set but no anchor
        period (or vice versa)", a state the storage tier made unreachable --
        so its detection test was deleted as unexercisable and the check
        survived as raw-SQL defence only.  What it asks now is the invariant
        those columns existed to serve: every account carries at least one
        ``account_anchor_history`` row (E-19 / Commit 3), because an account
        the resolver cannot answer for breaks every producer downstream.

        **The firing control BUILDS such an account rather than emptying one**
        (plan step X-f3c-2c).  It used to delete the seeded account's
        assertions with one raw statement; ``budget.account_anchor_history`` is
        append-only at the database tier now, so no statement does that.  What
        remains reachable -- and what this check exists for -- is an ``Account``
        row that never went through the factory, which is exactly the shape a
        restore, a hand-written script or a future writer would leave behind.
        """
        account = account_never_asserted(
            seed_user, db.session, name="Unanswerable",
            opening_equity=Decimal("0.00"),
        )
        account_id = account.id
        db.session.flush()

        results = check_balance_anomalies(db.session)
        ba01 = next(r for r in results if r.check_id == "BA-01")
        assert not ba01.passed
        assert ba01.detail_count == 1
        assert ba01.details[0]["id"] == account_id
        # The SEVERITY is the operationally load-bearing half, and the
        # re-pointing nearly lost it: this family used to stamp one
        # ``"warning"`` across every member, so an account the resolver RAISES
        # for would have exited 2 rather than 1 and ``verify_backup.sh`` would
        # have logged a broken restore as a WARNING.
        assert ba01.severity == "critical", (
            "an account with no assertion makes resolve_anchor raise on every "
            "page; it must fail the sweep, not warn"
        )

    @pytest.mark.usefixtures("seed_user", "seed_periods")
    def test_the_other_balance_checks_stay_warnings(self, db):
        """BA-05 and BA-06 are warnings; only BA-01 escalated.

        The complement of the assertion above, so "severity is per check"
        is graded in both directions: an edit that promoted the whole family
        to critical would pass the BA-01 test alone.

        **BA-06 belongs on this side deliberately.**  A settled row whose cash
        day no paycheck covers writes no wrong figure -- each column is valued
        at its own period end, so the day cancels on both sides of ruling
        R-K's identity and reports as ``period_timing``.  Promoting it would
        exit the backup sweep 2 over a state the developer ruled ACCEPTABLE on
        2026-08-11 when deleting the writer refusal that used to forbid it.
        """
        results = check_balance_anomalies(db.session)
        by_id = {r.check_id: r.severity for r in results}
        assert by_id["BA-01"] == "critical"
        assert by_id["BA-05"] == "warning"
        assert by_id["BA-06"] == "warning"

    def _settled_txn(self, seed_user, period, settled_on):
        """File a SETTLED transaction in *period* whose cash moved on a day."""
        return add_txn(
            db.session, seed_user, period, "Gas", "59.04",
            status_enum=StatusEnum.DONE, due_date=period.start_date,
            settled_on=settled_on,
        )

    def test_ba06_detects_a_settle_day_no_paycheck_covers(
        self, db, seed_user, seed_periods,
    ):
        """BA-06 fires on the state C3-b's deleted coverage rule used to refuse.

        The last seeded paycheck ends 2026-05-21.  A row filed in it that the
        bank took on 2026-06-15 has a cash day outside every period the owner
        has -- reachable since 2026-08-11, when the developer deleted the
        writer refusal that forbade producing it, on the ground that it writes
        no wrong figure.  It still deserves a human's attention, and this is
        where the arc puts a derivable question rather than in the writer.
        """
        txn = self._settled_txn(
            seed_user, seed_periods[-1], date(2026, 6, 15),
        )

        results = check_balance_anomalies(db.session)
        ba06 = next(r for r in results if r.check_id == "BA-06")
        assert not ba06.passed
        assert ba06.detail_count == 1
        assert ba06.details[0]["transaction_id"] == txn.id
        assert ba06.details[0]["settled_on"] == date(2026, 6, 15)

    def _ba06(self, db_session):
        """Return the BA-06 result from a fresh sweep."""
        return next(
            r for r in check_balance_anomalies(db_session)
            if r.check_id == "BA-06"
        )

    def _schedule_bounds(self, user_id):
        """Return the owner's ``(first payday, horizon)`` from the DERIVATION.

        The two days BA-06's rewritten predicate compares against, taken from
        ``pay_calendar`` rather than restated here: the check's whole claim
        since plan step ``pay_calendar:C4-c`` is that it bounds the span the
        application derives, and a hand-written expectation would only prove
        this file can do the same arithmetic twice.
        """
        saved = calendar_for(user_id).saved()
        return saved[0].start_date, saved[-1].end_date

    # **The four cases below are the boundary PAIRS, and an adversarial review
    # is why they exist** (2026-09-01).  BA-06's predicate was rewritten at
    # plan step ``pay_calendar:C4-c`` from ``NOT EXISTS (a period whose STORED
    # span contains the day)`` to two comparisons against the derived opening
    # and the derived horizon -- and the two cases that stood used days 25 days
    # past the horizon and deep inside it, so NEITHER sat at a boundary.  Three
    # mutations of the horizon term were driven against them and all passed:
    # dropping ``+ (cadence_days - 1)`` entirely, making it ``+ cadence_days``,
    # and making it ``- (cadence_days - 1)`` -- the last of which would report
    # every settled row in the owner's final paycheck, on every backup sweep.

    def test_ba06_accepts_the_horizon_day_itself(
        self, db, seed_user, seed_periods,
    ):
        """The last day the schedule covers is COVERED.

        ``MAX(start_date) + (cadence_days - 1)`` is the derivation's own
        projected end for the last period, so a row settling exactly there is
        inside the schedule.  A horizon term one day short reports it.
        """
        _first, horizon = self._schedule_bounds(seed_user["user"].id)
        self._settled_txn(seed_user, seed_periods[-1], horizon)

        assert self._ba06(db.session).passed, self._ba06(db.session).details

    def test_ba06_detects_the_day_after_the_horizon(
        self, db, seed_user, seed_periods,
    ):
        """One day past the last covered day is outside every paycheck.

        The pair to the case above, and the ``last_day`` it reports is asserted
        too: a check that fires on the right row while naming the wrong bound
        tells an operator to look in the wrong place.
        """
        _first, horizon = self._schedule_bounds(seed_user["user"].id)
        beyond = horizon + timedelta(days=1)
        txn = self._settled_txn(seed_user, seed_periods[-1], beyond)

        ba06 = self._ba06(db.session)
        assert not ba06.passed
        assert ba06.detail_count == 1
        assert ba06.details[0]["transaction_id"] == txn.id
        assert ba06.details[0]["settled_on"] == beyond
        assert ba06.details[0]["last_day"] == horizon

    def test_ba06_accepts_the_first_payday_itself(
        self, db, seed_user, seed_periods,
    ):
        """A period covers its own opening day, so the first payday is inside."""
        first, _horizon = self._schedule_bounds(seed_user["user"].id)
        self._settled_txn(seed_user, seed_periods[0], first)

        assert self._ba06(db.session).passed, self._ba06(db.session).details

    def test_ba06_detects_the_day_before_the_first_payday(
        self, db, seed_user, seed_periods,
    ):
        """The OPENING arm, which had no case at all until plan step C4-c.

        Derived periods TILE -- each ends the day before the next opens -- so
        the only days outside every paycheck are the ones before the first
        payday and the ones past the horizon.  Two comparisons say that, and
        an arm with no case is how a rewrite comes to be half graded.  The
        ``first_day`` it reports is asserted for the reason its sibling asserts
        ``last_day``.
        """
        first, _horizon = self._schedule_bounds(seed_user["user"].id)
        before = first - timedelta(days=1)
        txn = self._settled_txn(seed_user, seed_periods[0], before)

        ba06 = self._ba06(db.session)
        assert not ba06.passed
        assert ba06.detail_count == 1
        assert ba06.details[0]["transaction_id"] == txn.id
        assert ba06.details[0]["settled_on"] == before
        assert ba06.details[0]["first_day"] == first

    def test_ba06_ignores_a_row_merely_outside_its_own_paycheck(
        self, db, seed_user, seed_periods,
    ):
        """The control, and it is the whole reason this check is narrow.

        Production carries 21 of 160 settled rows dated outside their OWN
        paycheck and that state is ACCEPTED -- the cash clock and the budget
        clock are different clocks, and ``period_timing`` exists to carry the
        difference.  A check that fired on it would report the app working as
        designed, on every sweep, forever.  This row is filed in the first
        paycheck and settles inside the second: outside its own, inside the
        schedule.  The day is the SECOND paycheck's last covered day, taken
        from the derived calendar since plan step ``pay_calendar:C4-c`` dropped
        the column that used to carry it.
        """
        second = calendar_for(seed_user["user"].id).period_by_id(
            seed_periods[1].id,
        )
        self._settled_txn(seed_user, seed_periods[0], second.end_date)

        results = check_balance_anomalies(db.session)
        ba06 = next(r for r in results if r.check_id == "BA-06")
        assert ba06.passed, ba06.details

    def test_ba06_grades_a_settled_transfer_by_its_legs(
        self, db, seed_user, seed_periods,
    ):
        """One row per leg, naming the transfer, dated by the leg's MOVEMENT.

        Plan step ``balance:X-bi-6-4c-4`` (ruling **R-BAL160**): the shape
        DC-11's leg arm had until plan step ``balance:X-bi-6-4d-2`` deleted
        it.  A transfer settled one day past the horizon is
        two legs outside every paycheck -- reported once each under the
        transfer and that leg's account, on the day that side's movement
        carries, because the money is the movement (ruling **R-BAL80**).  A
        transfer hidden around the service is not reported.

        It also moved its two shadow rows' own day inside the schedule, and
        then hid the two, asserting both legs were still reported each time,
        until plan step ``balance:X-bi-6-4d-2``: a transfer is one row and no
        row may name one (``ck_transactions_names_no_transfer``, ruling
        **R-BAL258**), so each plant updated no row and nothing replaces
        them -- a transfer carries no settle day of its own to move, and has
        no twin to hide.
        """
        _first, horizon = self._schedule_bounds(seed_user["user"].id)
        beyond = horizon + timedelta(days=1)
        checking = seed_user["account"]
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "BA-06 Savings",
        )
        db.session.commit()
        transfer = create_settled_transfer(
            seed_user, db.session, checking, savings, seed_periods[-1],
            settled_on=beyond,
        )
        db.session.commit()
        expected = sorted(
            [(None, transfer.id, checking.id, beyond),
             (None, transfer.id, savings.id, beyond)],
            key=lambda row: row[2],
        )

        def reported():
            return [
                (row["transaction_id"], row["transfer_id"], row["account_id"],
                 row["settled_on"])
                for row in self._ba06(db.session).details
            ]

        assert reported() == expected

        # The TRANSFER's own soft delete ends it: the leg arm asks the
        # transfer's ``is_deleted``.
        db.session.execute(db.text(
            "UPDATE budget.transfers SET is_deleted = :gone WHERE id = :id"
        ), {"gone": True, "id": transfer.id})
        assert reported() == []
        db.session.execute(db.text(
            "UPDATE budget.transfers SET is_deleted = :gone WHERE id = :id"
        ), {"gone": False, "id": transfer.id})
        assert reported() == expected
