"""The day an assertion is for: what governs as of a day, the observation-day rule both assertion
writers share, and a cash true-up back-dated to a past day.
"""

from datetime import date, time, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.exceptions import ValidationError
from app.models.account import Account, AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.services import anchor_service, pay_period_service
from app.services.anchor_service import AnchorTrueUpOutcome, apply_anchor_true_up
from app.services.balance_at import BalanceContext, cash_balance_at
from app.utils.dates import display_today
from tests._test_helpers import create_settled_cash_transaction, freeze_today
from app.services import cash_ledger

from ._helpers import _make_checking_account


class TestGoverningAnchorOnIsDayScoped:
    """``cash_ledger.governing_anchor_on`` answers as of a DAY, not as of now.

    The cash half of ruling R-EQ's horizon, and the unit that makes plan step
    X-f1c4c safe.  The cash door stamps ``display_today()`` until that step, so
    today the day-scoped answer and ``resolve_anchor`` coincide -- which is
    exactly why the rule is installed BEFORE the field that makes them diverge.
    Graded here directly rather than through a door that cannot yet reach it,
    because a rule with no reachable caller has no negative control.
    """

    def test_it_ignores_assertions_made_for_later_days(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An assertion for a LATER day does not govern an earlier one.

        Three assertions on the days AFTER the account's opening -- $100 on
        O+1, $200 on O+5, $300 on O+10 -- then ask what governed on O+5: $200,
        not the $300 that ``resolve_anchor`` returns.  Asking ``resolve_anchor``
        here is what made every back-dated re-submit append a duplicate on the
        loan door.

        Every day is derived from the account's OWN opening rather than from
        ``display_today()``, so the fixture holds wherever in the calendar it
        runs -- the seeded origination is itself dated by the fixture's clock,
        and days offset backwards from today can land before it.
        """
        with app.app_context():
            account = _make_checking_account(seed_user, anchor_balance="50.00")
            db.session.flush()
            opening = cash_ledger.resolve_anchor(account).observed_on
            for offset, balance in ((1, "100.00"), (5, "200.00"), (10, "300.00")):
                db.session.add(AccountAnchorHistory(
                    account_id=account.id,
                    anchor_balance=Decimal(balance),
                    observed_on=opening + timedelta(days=offset),
                ))
            db.session.commit()

            governing = cash_ledger.governing_anchor_on(
                account.id, opening + timedelta(days=5),
            )
            assert governing.balance == Decimal("200.00"), (
                f"the assertion governing O+5 is the one made ON O+5, not the "
                f"later O+10 one; got {governing.balance}"
            )
            assert governing.observed_on == opening + timedelta(days=5)
            assert cash_ledger.resolve_anchor(account).balance == Decimal(
                "300.00"
            ), "resolve_anchor still answers as of NOW -- the two differ"

    def test_it_answers_none_before_the_first_assertion(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A day before every assertion has nothing governing it.

        The writer's honest answer, and the reason this returns ``None`` where
        ``resolve_anchor`` raises: a submission for a day nothing governs is
        necessarily new, which is a fact about the write, not a broken
        invariant.
        """
        with app.app_context():
            account = _make_checking_account(seed_user, anchor_balance="75.00")
            db.session.commit()
            opening = cash_ledger.resolve_anchor(account).observed_on

            assert cash_ledger.governing_anchor_on(
                account.id, opening - timedelta(days=1),
            ) is None


class TestResolveObservationDay:
    """``resolve_observation_day``: the day rule both assertion writers share.

    Ruling **R-ER**, plan step X-f1c4c.  It was ``account_service``'s private
    ``_reject_undatable_observation`` while the account factory was its only
    caller; the true-up door became the second, so the rule moved to the module
    that owns what an assertion IS and the two bounds are now graded here rather
    than only through the create route.

    The class also pins the SPLIT: the arm that refused an owner with no pay
    periods stayed behind as ``account_service``'s own precondition, because
    asking it here would have re-imposed on this door exactly the refusal ruling
    R-EO deleted from it.
    """

    def test_an_absent_day_is_the_users_today(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``None`` means "the balance I am asserting is true now"."""
        with app.app_context():
            assert anchor_service.resolve_observation_day(
                seed_user["user"].id, None,
            ).civil_day == display_today()

    def test_the_default_day_is_the_DISPLAY_clock_not_the_process_clock(
        self, app, db, monkeypatch, seed_user, seed_periods_today,
    ):
        """The default is ``display_today()``, and this is what proves it.

        **The sibling above cannot grade this claim**, which a neutral review of
        plan step X-f1c4c demonstrated: ``tests/test_services/conftest.py``
        applies a directory-wide autouse ``freeze_today`` that pins BOTH clocks
        to the same civil day, so a mutant reading ``date.today()`` passed all
        30 tests in this file.  It was caught only by the route suite, and only
        under CI's ``TZ=Pacific/Kiritimati`` -- invisible on the developer's own
        machine.

        This overrides the freeze with an EVENING instant, the one shape that
        tells the two clocks apart: ``00:30`` UTC is still the PREVIOUS evening
        in ``America/New_York``, so the process day and the user's civil day
        differ by one.  The rule matters because the day is what an assertion is
        filed under -- a true-up entered at 8pm Eastern belongs to the day the
        user is living in, not to the UTC day that has already rolled over.
        """
        with app.app_context():
            split_day = date(2026, 3, 20)
            freeze_today(monkeypatch, split_day, at_time=time(0, 30))
            # The precondition IS the point: if these ever coincide the case
            # grades nothing, exactly as the sibling above does not.
            assert date.today() == split_day
            assert display_today() == split_day - timedelta(days=1), (
                "00:30 UTC must still be the previous evening in the display "
                "zone, or this case cannot tell the two clocks apart"
            )

            assert anchor_service.resolve_observation_day(
                seed_user["user"].id, None,
            ).civil_day == split_day - timedelta(days=1)

    def test_a_future_day_is_judged_on_the_DISPLAY_clock(
        self, app, db, monkeypatch, seed_user, seed_periods_today,
    ):
        """The future bound uses the user's civil day too, not the server's.

        The refusal half of the case above, and the one with the sharper
        consequence: on the process clock the user's OWN current day looks like
        tomorrow for the hours the two disagree, so a true-up entered at 8pm
        Eastern would be refused as "that day has not happened yet" -- about the
        day the user is standing in.
        """
        with app.app_context():
            split_day = date(2026, 3, 20)
            freeze_today(monkeypatch, split_day, at_time=time(0, 30))
            users_today = split_day - timedelta(days=1)
            assert display_today() == users_today

            # The user's own civil day is accepted...
            assert anchor_service.resolve_observation_day(
                seed_user["user"].id, users_today,
            ).civil_day == users_today
            # ...and the PROCESS day, which is already tomorrow for them, is not.
            with pytest.raises(ValidationError) as exc:
                anchor_service.resolve_observation_day(
                    seed_user["user"].id, split_day,
                )
            assert "has not happened yet" in str(exc.value)

    def test_a_supplied_day_inside_both_bounds_survives(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A day the user typed, within bounds, is returned unchanged.

        Non-vacuity: the day is derived from the schedule's own floor and
        asserted to differ from today, so it cannot pass by coinciding with the
        default the absent-day case returns.
        """
        with app.app_context():
            floor = pay_period_service.earliest_recordable_day(
                seed_user["user"].id,
            )
            typed = floor + timedelta(days=1)
            assert typed != display_today(), (
                "the fixture's schedule now starts one day before today, so "
                "this case would pass vacuously against the default"
            )

            assert anchor_service.resolve_observation_day(
                seed_user["user"].id, typed,
            ).civil_day == typed

    def test_a_future_day_is_refused(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A balance cannot have been observed on a day nobody has seen.

        The message names the offending value and the bound, because the route
        renders it verbatim into the editor the user is looking at.
        """
        with app.app_context():
            tomorrow = display_today() + timedelta(days=1)

            with pytest.raises(ValidationError) as exc:
                anchor_service.resolve_observation_day(
                    seed_user["user"].id, tomorrow,
                )
            assert "has not happened yet" in str(exc.value)
            assert tomorrow.isoformat() in str(exc.value)

    def test_a_day_below_the_schedule_is_refused(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A day before the recorded history is refused at the SAME floor R-EL
        gave the settle door.

        Not cosmetic: ``observed_on`` opens the modelled-return accrual window
        and the contribution model's first period, so an unbounded day both
        fabricates history and folds over every calendar day since.  The bound
        is read from ``pay_period_service.earliest_recordable_day`` here rather
        than hardcoded, so this test cannot pass against a second definition.
        """
        with app.app_context():
            floor = pay_period_service.earliest_recordable_day(
                seed_user["user"].id,
            )

            with pytest.raises(ValidationError) as exc:
                anchor_service.resolve_observation_day(
                    seed_user["user"].id, floor - timedelta(days=1),
                )
            assert "recorded history starts on" in str(exc.value)
            assert floor.isoformat() in str(exc.value)

            # The floor itself is INSIDE the bound -- an off-by-one here would
            # refuse the first day the user has a schedule for.
            assert anchor_service.resolve_observation_day(
                seed_user["user"].id, floor,
            ).civil_day == floor

    def test_an_owner_with_no_pay_periods_can_still_assert_today(
        self, app, db, seed_user,
    ):
        """The schedule arm SPLIT OUT, and this is its negative control.

        Ruling R-ER.  The rule this replaced refused outright when the owner had
        no pay periods -- so sharing it whole would have refused a balance the
        user read off their bank because a BUDGETING artifact was missing, which
        is finding N-134's shape and precisely what ruling R-EO deleted from the
        true-up door.  Deleting the owner's periods and asserting today must
        still answer.

        ``seed_user`` without ``seed_periods_today`` is used deliberately, and
        the periods it does seed are removed, so the state is the real one
        rather than a mocked query.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            db.session.query(PayPeriod).filter_by(user_id=user_id).delete()
            db.session.commit()
            assert db.session.query(PayPeriod).filter_by(
                user_id=user_id,
            ).count() == 0

            # With no schedule the floor collapses to today, so today -- and
            # only today -- is assertable.  Both halves are graded: the answer
            # comes back, and it is not silently something else.
            assert anchor_service.resolve_observation_day(
                user_id, None,
            ).civil_day == display_today()
            assert anchor_service.resolve_observation_day(
                user_id, display_today(),
            ).civil_day == display_today()


class TestBackDatedCashTrueUp:
    """A cash true-up can assert a PAST day (plan step X-f1c4c, ruling R-EE).

    The capability ruling R-EQ was installed one leaf early for: the write
    door's duplicate rule compares against the assertion governing THE DAY THE
    SUBMISSION ASSERTS, so a back-dated re-submit is idempotent instead of
    appending a permanent row every time.

    **Every day here is derived BACKWARD from ``display_today()``, and the
    account is opened with an explicit one.**  The factory's default opening is
    today, which leaves no room behind it -- a first version of these tests
    offset FORWARD from that opening and every case landed in the future.
    ``seed_periods_today`` starts its schedule ``today.weekday() + 56`` days
    back, so 30 days of room is guaranteed on any calendar day; the precondition
    is asserted rather than assumed.
    """

    #: Days back from today for the three fixture instants: the opening, the
    #: back-dated correction, and a later assertion that must keep governing.
    #: Named once because five tests share them and a drift between two of them
    #: would silently change what a case grades.
    OPENING_DAYS_BACK = 30
    BACK_DATED_DAYS_BACK = 20
    LATER_DAYS_BACK = 10

    def _fixture_days(self, seed_user):
        """Return ``(opening, back_dated, later)``, all in the past and in order.

        Asserts the fixture actually affords the room, so a change to
        ``seed_periods_today`` fails loudly here instead of making these cases
        grade the default path.
        """
        today = display_today()
        floor = pay_period_service.earliest_recordable_day(seed_user["user"].id)
        opening = today - timedelta(days=self.OPENING_DAYS_BACK)
        back_dated = today - timedelta(days=self.BACK_DATED_DAYS_BACK)
        later = today - timedelta(days=self.LATER_DAYS_BACK)
        assert floor <= opening, (
            f"the schedule starts {floor} but these cases need {opening} to be "
            "assertable; seed_periods_today no longer affords 30 days of "
            "history"
        )
        # The ORDERING, not just the room.  A neutral review of this step proved
        # the omission mattered: with the constants drifted so that ``later``
        # falls before ``back_dated``, ``test_a_back_dated_resubmit_is_idempotent``
        # goes GREEN under the very latest-horizon defect it exists to catch,
        # because the two horizons stop disagreeing.  The guard above only ever
        # checked the floor.
        assert opening < back_dated < later < today, (
            f"these cases need opening < back_dated < later < today; got "
            f"{opening} / {back_dated} / {later} / {today}"
        )
        return opening, back_dated, later

    def test_the_submitted_day_is_what_the_assertion_carries(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A back-dated true-up is dated by the submission, not by the clock."""
        with app.app_context():
            opening, back_dated, _ = self._fixture_days(seed_user)
            account = _make_checking_account(
                seed_user, anchor_balance="100.00", observed_on=opening,
            )
            db.session.commit()

            outcome = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("250.00"),
                observed_on=back_dated,
            ).outcome

            assert outcome is AnchorTrueUpOutcome.COMMITTED
            row = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id, observed_on=back_dated)
                .one()
            )
            assert row.anchor_balance == Decimal("250.00")
            # Non-vacuity: the day it carries is genuinely not the default, so
            # a door that ignored the parameter could not pass this.
            assert back_dated != display_today()

    def test_a_back_dated_resubmit_is_idempotent(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Re-submitting a back-dated assertion appends nothing.

        **The defect this grades was reproduced on the loan door twice**, before
        the cash door could reach it: comparing a submission against the
        account's LATEST assertion means a submission for an EARLIER day can
        never compare equal, so every double-click on a back-dated correction
        appended a permanent row.  The cash door could not be graded for it
        until this step gave it a date field -- so this is the control that was
        missing, not a duplicate of the loan one.

        A LATER assertion is recorded first, precisely so the two horizons
        disagree: with only the back-dated row present, "latest" and "governs
        this day" would coincide and a broken rule would still pass.
        """
        with app.app_context():
            opening, back_dated, later = self._fixture_days(seed_user)
            account = _make_checking_account(
                seed_user, anchor_balance="100.00", observed_on=opening,
            )
            db.session.commit()

            apply_anchor_true_up(
                account=account, new_balance=Decimal("900.00"),
                observed_on=later,
            )
            first = apply_anchor_true_up(
                account=account, new_balance=Decimal("250.00"),
                observed_on=back_dated,
            ).outcome
            rows_after_first = db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).count()

            second = apply_anchor_true_up(
                account=account, new_balance=Decimal("250.00"),
                observed_on=back_dated,
            ).outcome

            assert first is AnchorTrueUpOutcome.COMMITTED
            assert second is AnchorTrueUpOutcome.UNCHANGED, (
                "the re-submit was compared against the LATEST assertion "
                "($900 on a later day), which it can never equal"
            )
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).count() == rows_after_first

    def test_a_back_dated_assertion_does_not_become_the_current_one(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Recording a past balance does not re-point what the app displays.

        ``resolve_anchor`` orders on the BUSINESS day first, so an assertion
        recorded later but dated earlier is history, not the current balance.
        Without this the correction of an old statement would silently replace
        today's figure on the six surfaces that render the resolved anchor.
        """
        with app.app_context():
            opening, back_dated, later = self._fixture_days(seed_user)
            account = _make_checking_account(
                seed_user, anchor_balance="100.00", observed_on=opening,
            )
            db.session.commit()

            apply_anchor_true_up(
                account=account, new_balance=Decimal("900.00"),
                observed_on=later,
            )
            apply_anchor_true_up(
                account=account, new_balance=Decimal("250.00"),
                observed_on=back_dated,
            )

            current = cash_ledger.resolve_anchor(account)
            assert current.balance == Decimal("900.00")
            assert current.observed_on == later

    def test_a_back_dated_assertion_rebases_the_balance_at_its_own_day(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The fold re-bases at the back-dated day AND replays the flows around it.

        The MONEY control for this capability, read at the seam every surface
        reads rather than off the row that was written.

        **It carried no money until a neutral review of this step said so.**  The
        first version asserted only the three asserted balances back out of an
        account with NO transactions, so every expected value was an anchor
        echoed verbatim -- and a fold that dropped dated deltas entirely still
        passed it (proved by mutating ``_cash_fold._planned_day_nets`` to return
        nothing: all five cases stayed green).  The real hazard of back-dating is
        what happens to the flows on either side of the new assertion, so those
        are what this now grades.

        Hand-computed, with a settled expense on each side of the back-date.
        Assertions: ``$1,000.00`` at O, ``$250.00`` at O+10 (the back-date).
        Settled: ``-$25.00`` at O+5, ``-$40.00`` at O+15.

        * O..O+4 -- ``1,000.00`` (the opening; nothing has moved yet)
        * O+5..O+9 -- ``1,000.00 - 25.00 = 975.00``
        * O+10..O+14 -- ``250.00``: the assertion RESETS the running total, and
          the ``-25.00`` before it is absorbed into that reset rather than
          subtracted again
        * O+15 onward -- ``250.00 - 40.00 = 210.00``: the later spend rides on
          top of the assertion instead of being swallowed by it
        """
        with app.app_context():
            opening, back_dated, _ = self._fixture_days(seed_user)
            account = _make_checking_account(
                seed_user, anchor_balance="1000.00", observed_on=opening,
            )
            db.session.commit()
            # One settle on each side of the day about to be asserted: the first
            # must be ABSORBED by the new assertion, the second must RIDE on it.
            # Without both, a fold that swallowed everything and a fold that
            # swallowed nothing would answer identically.
            create_settled_cash_transaction(
                seed_user, seed_periods_today[0],
                Decimal("25.00"), account=account,
                settled_on=opening + timedelta(days=5), name="before",
            )
            create_settled_cash_transaction(
                seed_user, seed_periods_today[0],
                Decimal("40.00"), account=account,
                settled_on=opening + timedelta(days=15), name="after",
            )
            db.session.commit()

            apply_anchor_true_up(
                account=account, new_balance=Decimal("250.00"),
                observed_on=back_dated,
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            account = db.session.get(Account, account.id)
            reads = {
                day: cash_balance_at(account, ctx, opening + timedelta(days=day))
                for day in (0, 4, 5, 9, 10, 14, 15, 20)
            }
            assert reads[0] == Decimal("1000.00")
            assert reads[4] == Decimal("1000.00")
            assert reads[5] == Decimal("975.00")
            assert reads[9] == Decimal("975.00")
            assert reads[10] == Decimal("250.00")
            assert reads[14] == Decimal("250.00")
            assert reads[15] == Decimal("210.00")
            assert reads[20] == Decimal("210.00")

    def test_a_refused_day_stages_nothing_and_holds_no_lock(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A refusal stages nothing, and the door takes no lock of its own.

        The day is resolved at the TOP of :func:`apply_anchor_true_up`, before
        anything is staged, so a refusal has appended nothing.  And since plan
        step ``balance:X-bn`` no service takes the owner's write lock -- a
        signed-in request's transaction takes it where it begins
        (:mod:`app.db_transaction`) and holds it until the request ends,
        refused or not -- so a direct call that is refused holds none.  The
        refusal leaves the transaction open, so the no-lock assertion below is
        a regression pin for a service acquisition re-added ABOVE the refusal
        (measured 2026-09-29: one re-added at the door's top turns it red).
        *Until that step the day was resolved above the service's own
        ``lock_user_writes``, so a refusal never took the lock.*
        """
        with app.app_context():
            opening, _, _ = self._fixture_days(seed_user)
            account = _make_checking_account(
                seed_user, anchor_balance="100.00", observed_on=opening,
            )
            db.session.commit()
            before = db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).count()

            with pytest.raises(ValidationError):
                apply_anchor_true_up(
                    account=account,
                    new_balance=Decimal("250.00"),
                    observed_on=display_today() + timedelta(days=1),
                )

            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).count() == before
            # Nothing pending, so no rollback was needed to leave a clean
            # session -- the refusal never reached the staging line.
            assert not db.session.new
            # No service takes the owner's lock (plan step ``balance:X-bn``),
            # and nothing ended this transaction, so one taken above the
            # refusal would still show here.
            assert db.session.execute(
                sa.text("SELECT count(*) FROM pg_locks WHERE locktype = "
                        "'advisory' AND pid = pg_backend_pid()")
            ).scalar() == 0
