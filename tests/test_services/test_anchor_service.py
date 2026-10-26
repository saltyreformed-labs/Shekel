"""
Shekel Budget App -- Anchor Service Tests

Unit tests for :mod:`app.services.anchor_service` and its loan half,
:mod:`app.services.loan_anchor_service` (split out at plan step
``recurrence:R20``).  Pins both outcomes of :func:`apply_anchor_true_up` and
its loan twin, ruling **R-EQ**'s duplicate rule, the cash door's report of what
governs either side of its write (ruling **R-CC79**), the stager's one read of
the latest assertion that the report's before now is (ruling **R-CC85**), and
the contract that an unexpected ``IntegrityError`` propagates.

Pre-extraction these branches were covered indirectly by the grid
HTMX-route test suites (``TestTrueUpSameDayDuplicate`` and
``TestTrueUpStaleForm``).  The route suites still exercise the
wiring; these tests pin the helper's contract directly so a future
change to the route cannot accidentally drift the shared semantics.

**The idempotency tests graded a UNIQUE INDEX until plan step X-f1c4b** and
now grade the write-door rule that replaced it: an assertion is refused only
when it changes nothing.  The index answered the double-submit cases the same
way; it answered a re-assertion of a superseded balance WRONGLY, and no test
had asked it -- so each door gained one
(``test_reasserting_a_superseded_balance_is_recorded``).
"""

import inspect
from datetime import date, time, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import LoanAnchorSourceEnum
from app.exceptions import TrackingStartRefused, ValidationError
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_params import LoanParams
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.models.transaction_entry import TransactionEntry
from app.services import (
    account_service,
    anchor_service,
    loan_loaders,
    pay_period_service,
)
from app.services.anchor_service import (
    AnchorTrueUpOutcome,
    apply_anchor_true_up,
)
from app.services.loan_anchor_service import (
    apply_loan_anchor_true_up,
    record_loan_tracking_start,
    stage_loan_tracking_start,
)
from app.services.balance_at import BalanceContext, cash_balance_at
from app.services.cash_ledger import _facts as cash_ledger_facts
from app.services.user_write_lock import _USER_WRITE_LOCK_NAMESPACE
from app.utils.dates import display_today
from tests._test_helpers import (
    figure_source_columns,
    create_loan_account,
    create_settled_cash_transaction,
    create_settled_transfer,
    current_pay_period,
    freeze_today,
    generate_row_of,
    insert_origination_rate,
    make_expense_template,
    settle_day_columns,
)
from app.services import cash_ledger


def _make_checking_account(seed_user, anchor_balance="1000.00", observed_on=None):
    """Create a fresh Checking account carrying one balance assertion.

    It took a ``periods`` argument, to anchor the account at ``periods[0]``,
    until ruling R-EH deleted ``AccountSpec.anchor_period_id`` (plan step
    X-f1c3c).  An assertion is a day and a balance now; the caller's periods
    have nothing to say about it.

    Args:
        seed_user: The ``seed_user`` fixture payload.
        anchor_balance: The opening balance, as a string.
        observed_on: The civil day that opening was true.  ``None`` takes the
            factory's default of today -- which leaves NO room behind the
            opening, so a caller grading a BACK-DATED assertion must pass one
            (plan step X-f1c4c).
    """
    checking_type = db.session.query(AccountType).filter_by(
        name="Checking",
    ).one()
    return account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=checking_type.id,
            name="Helper Checking",
            anchor_balance=Decimal(anchor_balance),
            observed_on=observed_on,
        ),
    )


def _make_savings_account(seed_user, anchor_balance="500.00"):
    """Create a fresh Savings account carrying one balance assertion.

    See :func:`_make_checking_account` for why it no longer takes ``periods``.
    """
    savings_type = db.session.query(AccountType).filter_by(
        name="Savings",
    ).one()
    return account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Helper Savings",
            anchor_balance=Decimal(anchor_balance),
        ),
    )


def _make_projected_expense_with_past_dated_entry(seed_user, period, amount):
    """Create a Projected expense with one unobserved past-dated debit entry.

    Used by the two tests that pin what a true-up does NOT do: its
    ``settled_on`` starts NULL -- the bank has not been seen to take it -- and
    it must still be NULL afterwards.  Returns the
    :class:`TransactionEntry` so the caller can re-read that column.

    The envelope is the engine's own row of a priced, repeating definition
    (:func:`generate_row_of`, plan step balance:X-cf) rather than a copy built
    by hand; the purchase is then recorded against it.
    """
    template = make_expense_template(
        db.session, seed_user, amount="500.00",
        name="Groceries", category_key="Groceries", is_envelope=True,
    )
    txn = generate_row_of(template, period)

    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=seed_user["user"].id,
        amount=Decimal(amount),
        description="Past-dated debit",
        purchased_on=date.today() - timedelta(days=1),
        is_credit=False,
        **settle_day_columns(None),
    )
    db.session.add(entry)
    db.session.commit()
    return entry


class TestApplyAnchorTrueUpCommitted:
    """COMMITTED outcome: helper writes balance + history and commits."""

    def test_savings_true_up_commits_without_touching_entries(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Non-checking account: helper commits without touching entries.

        Setup: a Savings account anchored at periods[0]; one
        projected checking-account expense with an unobserved past-
        dated debit entry (created on the seed_user checking account,
        NOT on the savings account being trued up).

        Hand-check: after ``apply_anchor_true_up`` on the savings
        account, the outcome is COMMITTED, the savings anchor balance
        is the new value, exactly one new history row exists for the
        savings account, and the unrelated checking purchase still has
        NO recorded posting day.
        """
        with app.app_context():
            savings = _make_savings_account(
                seed_user, anchor_balance="500.00",
            )
            db.session.commit()

            current_period = current_pay_period(
                seed_user["user"].id
            )
            # An unrelated past-dated checking debit -- must stay
            # uncleared because the savings true-up does not run the
            # entry reconcile.
            entry = _make_projected_expense_with_past_dated_entry(
                seed_user, current_period, amount="50.00",
            )
            history_count_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=savings.id)
                .count()
            )

            outcome = apply_anchor_true_up(
                account=savings,
                new_balance=Decimal("750.00"),
            ).outcome

            assert outcome is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            reloaded = db.session.get(Account, savings.id)
            assert cash_ledger.resolve_anchor(reloaded).balance == Decimal("750.00")

            history_count_after = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=savings.id)
                .count()
            )
            assert history_count_after == history_count_before + 1, (
                "Savings true-up must append exactly one history row."
            )

            # The purchase is untouched.  This was true before plan step S1-c
            # too -- the bulk clear was checking-only -- so this arm is a
            # rename rather than a re-ruling; the checking test beside it is
            # the one whose expectation INVERTED.
            entry_after = db.session.get(TransactionEntry, entry.id)
            assert entry_after.settled_on is None, (
                "A true-up records no posting day on any account's "
                "purchases (ruling R-DH (d))."
            )

    def test_checking_true_up_records_no_posting_day(
        self, app, db, seed_user, seed_periods_today,
    ):
        """RE-RULED: a checking true-up touches NO entry (ruling R-DH (d)).

        Setup: the seed_user account is Checking; one projected
        envelope expense with one unobserved past-dated debit entry of
        ``$50.00`` on the current period.

        Hand-check: after ``apply_anchor_true_up`` on the seed_user
        checking account, the outcome is COMMITTED, the anchor
        balance is the new value, exactly one new history row exists,
        AND the purchase still has NO recorded posting day.

        **This test asserted the OPPOSITE until plan step S1-c**, and the
        inversion is the point of the step.  A bulk ``UPDATE`` flipped
        ``is_cleared = TRUE`` on every entry dated on or before the SERVER's
        today at every true-up, so whether a purchase counted as reconciled
        was decided by the order two buttons were pressed: record then true up
        and it cleared, true up then record and it never did.  The engine now
        never guesses a posting day; the user supplies it by ticking the
        purchase off a statement (``reconcile_service.record_settled_days``, whose
        own tests live with that service and whose ROUTE is graded in
        ``test_routes/test_accounts/``'s three ``test_reconcile_*.py`` parts).

        That reconcile step is deliberately a SEPARATE request: folding it in
        here would put it inside the transaction an UNCHANGED submission rolls
        back and reports as idempotent success -- so a re-assert of the
        governing balance would silently discard every reconciliation just made
        while the UI said it saved.

        Re-fetches the account via ``db.session.get`` so it is
        attached to the current scoped session.  The conftest's ``db``
        fixture removes/disposes the session at the start of each
        test, so the cached ``seed_user["account"]`` reference is in
        a stale session and would not flush correctly otherwise.  This
        mirrors the route pattern (every route opens with
        ``db.session.get(Account, account_id)``).
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)
            current_period = current_pay_period(
                seed_user["user"].id
            )
            entry = _make_projected_expense_with_past_dated_entry(
                seed_user, current_period, amount="50.00",
            )
            history_count_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id)
                .count()
            )

            outcome = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("2500.00"),
            ).outcome

            assert outcome is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            reloaded = db.session.get(Account, account.id)
            assert cash_ledger.resolve_anchor(reloaded).balance == Decimal("2500.00")

            history_count_after = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id)
                .count()
            )
            assert history_count_after == history_count_before + 1

            entry_after = db.session.get(TransactionEntry, entry.id)
            assert entry_after.settled_on is None, (
                "A true-up must record NO posting day: whether the bank has "
                "taken a purchase is not derivable from a balance reading "
                "(ruling R-DH (d) / plan step S1-c)."
            )


class TestApplyAnchorTrueUpUnchanged:
    """UNCHANGED outcome: ruling R-EQ's idempotent success.

    An assertion is refused only when it changes nothing.  The write door
    compares the submission against the assertion that currently GOVERNS
    (``cash_ledger.resolve_anchor``) and appends only when they differ.

    **This class graded a UNIQUE INDEX until plan step X-f1c4b**
    (``uq_anchor_history_account_period_balance_day``, dropped by migration
    ``a3f6c1d84b90``).  The index answered the first two cases below the same
    way; it answered the THIRD wrongly, and no test had ever asked it.
    """

    def test_double_call_same_balance_returns_unchanged(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Two identical helper calls produce exactly one history row.

        The retry case the rule exists for.  ``apply_anchor_true_up`` called
        twice with the same balance on the same civil day returns ``COMMITTED``
        then ``UNCHANGED``, and the on-disk history shows exactly one row at
        that balance (plus whatever origination/prior history the fixture
        wrote).

        Account re-fetched via ``db.session.get`` for current-session
        attachment (see sibling-class rationale).
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)

            outcome_first = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("1234.56"),
            ).outcome
            assert outcome_first is AnchorTrueUpOutcome.COMMITTED

            # Second call: the same balance for the same civil day is now what
            # governs, so there is nothing to append.
            outcome_second = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("1234.56"),
            ).outcome
            assert outcome_second is AnchorTrueUpOutcome.UNCHANGED

            db.session.expire_all()
            rows_at_duplicate_balance = (
                db.session.query(AccountAnchorHistory)
                .filter_by(
                    account_id=account.id,
                    anchor_balance=Decimal("1234.56"),
                )
                .all()
            )
            assert len(rows_at_duplicate_balance) == 1, (
                f"A repeated submission must append nothing: expected exactly "
                f"one history row at that balance, found "
                f"{len(rows_at_duplicate_balance)}."
            )

    def test_same_day_different_balance_both_commit(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Same-day true-ups with different balances both succeed.

        A legitimate same-day correction (the user noticed an error and
        re-trued at a different amount) MUST NOT be blocked.  Both calls
        return ``COMMITTED``.

        Account re-fetched via ``db.session.get`` for current-session
        attachment (see sibling-class rationale).
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)

            outcome_a = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("1000.00"),
            ).outcome
            outcome_b = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("1100.00"),
            ).outcome
            assert outcome_a is AnchorTrueUpOutcome.COMMITTED
            assert outcome_b is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            balances = {
                row.anchor_balance for row in
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id)
                .all()
            }
            # The fixture's origination row already sits at $1000.00 for an
            # earlier day, so the first call above appends nothing new to this
            # SET while the $1100.00 row is added.  Assert both balances are
            # represented.
            assert Decimal("1100.00") in balances, (
                "$1100.00 same-day correction must commit a history row."
            )
            assert Decimal("1000.00") in balances, (
                "$1000.00 history row must survive the same-day double."
            )

    def test_reasserting_a_superseded_balance_is_recorded(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Re-asserting a balance that was corrected away is a real change.

        **The defect ruling R-EQ exists for, and the deleted unique index's
        negative control.**  Three submissions on one civil day:

          1. ``$500.00`` -- committed.
          2. ``$600.00`` -- committed (the user corrects themselves).
          3. ``$500.00`` -- the user decides the first reading was right.

        The third changes what governs from ``$600.00`` to ``$500.00``, so it
        must be recorded and the resolver must answer ``$500.00``.  Under
        ``uq_anchor_history_account_period_balance_day`` it collided with row 1,
        was swallowed as idempotent success, and every surface kept rendering
        ``$600.00`` while the app reported the correction saved.  Account 1 on
        the 2026-08-04 production clone carries 2-3 assertions on 3 of its 50
        assertion days, so this sequence is an ordinary bookkeeping session.
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)

            assert apply_anchor_true_up(
                account=account, new_balance=Decimal("500.00"),
            ).outcome is AnchorTrueUpOutcome.COMMITTED
            assert apply_anchor_true_up(
                account=account, new_balance=Decimal("600.00"),
            ).outcome is AnchorTrueUpOutcome.COMMITTED
            assert apply_anchor_true_up(
                account=account, new_balance=Decimal("500.00"),
            ).outcome is AnchorTrueUpOutcome.COMMITTED, (
                "Re-asserting a superseded balance CHANGES what governs "
                "($600.00 -> $500.00), so it is a fact and must be recorded."
            )

            db.session.expire_all()
            reloaded = db.session.get(Account, account.id)
            assert cash_ledger.resolve_anchor(reloaded).balance == Decimal(
                "500.00"
            ), (
                "The correction must be what governs.  A content-keyed unique "
                "index answered $600.00 here while reporting success."
            )
            rows_at_500 = (
                db.session.query(AccountAnchorHistory)
                .filter_by(
                    account_id=account.id, anchor_balance=Decimal("500.00"),
                )
                .all()
            )
            assert len(rows_at_500) == 2, (
                f"Both $500.00 assertions are facts -- the original and the "
                f"correction back to it -- so the append-only history holds "
                f"two, not {len(rows_at_500)}."
            )


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
                seed_user, db.session, seed_periods_today[0],
                Decimal("25.00"), account=account,
                settled_on=opening + timedelta(days=5), name="before",
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_periods_today[0],
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


# ── The cash door's report (ruling R-CC79) ───────────────────────────


def _spy_governing_reads(monkeypatch, probe=None):
    """Record every read of the governing assertion: who asked, as of when.

    Wraps ``cash_ledger._facts._governing_row`` -- the ONE query behind
    ``resolve_anchor``, ``governing_anchor`` and ``governing_anchor_on`` -- so
    this counts READS of the governing assertion whichever reader spelled
    them, not SQL statements (a refresh or an eager load moves those without
    moving what is asked).

    Args:
        monkeypatch: The test's ``monkeypatch``; it restores the query.
        probe: An optional zero-argument callable run at each read, its answer
            recorded beside it -- how a case asks what held at that instant.

    Returns:
        The list each read appends ``(caller, horizon, probed)`` to: the
        function that called the reader, the horizon (``None`` is "what
        governs now", a day is ``governing_anchor_on``'s), and the probe's
        answer or ``None``.
    """
    real = cash_ledger_facts._governing_row
    reads = []

    def spy(account_id, on_or_before):
        """Note the caller and horizon, run the probe, then ask the real query."""
        caller = inspect.currentframe().f_back.f_back.f_code.co_name
        reads.append((caller, on_or_before, None if probe is None else probe()))
        return real(account_id, on_or_before)

    monkeypatch.setattr(cash_ledger_facts, "_governing_row", spy)
    return reads


def _statements_after_the_commit(fn):
    """Run *fn*; return its result and the SQL emitted after its first COMMIT.

    One timeline of the engine's statements and its commits, so "after the
    write door committed" is a position in it rather than an inference.

    Args:
        fn: A zero-argument callable to run under capture.

    Returns:
        ``(result, statements)`` -- *fn*'s return value and the text of every
        statement emitted after the first commit; the commit itself is
        asserted to have happened, so an empty list cannot mean "no commit".
    """
    commit = object()
    timeline = []

    def on_statement(_conn, _cursor, statement, *_args, **_kwargs):
        """Append the statement's text."""
        timeline.append(statement)

    def on_commit(_conn):
        """Append the commit marker."""
        timeline.append(commit)

    event.listen(db.engine, "before_cursor_execute", on_statement)
    event.listen(db.engine, "commit", on_commit)
    try:
        result = fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", on_statement)
        event.remove(db.engine, "commit", on_commit)
    assert commit in timeline, "no commit was seen, so no window was graded"
    return result, timeline[timeline.index(commit) + 1:]


def _holds_owner_lock(user_id):
    """Return whether THIS session holds the owner's write lock right now.

    Asked of ``pg_locks`` rather than inferred from statement order: the
    two-key ``pg_advisory_xact_lock(namespace, user_id)`` form shows as
    ``classid`` / ``objid`` with ``objsubid = 2``.
    """
    return db.session.execute(
        sa.text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND pid = pg_backend_pid() AND granted AND objsubid = 2 "
            "AND classid::bigint = :namespace AND objid::bigint = :user_id"
        ),
        {"namespace": _USER_WRITE_LOCK_NAMESPACE, "user_id": user_id},
    ).scalar() > 0


class TestTheCashDoorReportsWhatGovernsEitherSide:
    """Ruling R-CC79: the door reports the governing assertion before and after.

    It returned the bare outcome until then, so the route asked the ledger
    itself what the door had decided -- once before the write, outside the
    owner's lock, and twice after it.  The report carries both records, read
    inside the lock; these grade its three shapes with hand-computed figures.
    Every day is derived backward from ``display_today()`` and checked against
    the schedule's floor, as ``TestBackDatedCashTrueUp`` does.
    """

    @staticmethod
    def _days_back(seed_user, *offsets):
        """Return ``today - offset`` for each offset, asserting each is assertable."""
        today = display_today()
        days = tuple(today - timedelta(days=offset) for offset in offsets)
        floor = pay_period_service.earliest_recordable_day(seed_user["user"].id)
        assert floor <= min(days), (
            f"the schedule starts {floor}; these cases need {min(days)}"
        )
        return days

    def test_a_save_for_today_reports_the_old_figure_and_the_new_one(
        self, app, db, seed_user, seed_periods_today,
    ):
        """$1,000.00 asserted 10 days ago; $1,200.00 saved for today.

        Hand-computed: ``governing_before`` is the $1,000.00 opening dated 10
        days ago, ``governing_after`` the $1,200.00 just recorded, dated today,
        and the outcome COMMITTED.  The after is the very record a reader
        resolves once the save has landed -- anchor id and recording instant
        included -- so what the grid draws from it is what every screen reads.
        """
        with app.app_context():
            (opened,) = self._days_back(seed_user, 10)
            account = _make_checking_account(
                seed_user, anchor_balance="1000.00", observed_on=opened,
            )
            db.session.commit()
            opening_id = cash_ledger.resolve_anchor(account).anchor_id

            report = apply_anchor_true_up(
                account=account, new_balance=Decimal("1200.00"),
            )

            assert report.outcome is AnchorTrueUpOutcome.COMMITTED
            before, after = report.governing_before, report.governing_after
            assert (before.anchor_id, before.balance, before.observed_on) == (
                opening_id, Decimal("1000.00"), opened,
            )
            assert (after.balance, after.observed_on) == (
                Decimal("1200.00"), display_today(),
            )
            assert after.anchor_id != opening_id
            db.session.expire_all()
            assert after == cash_ledger.resolve_anchor(
                db.session.get(Account, account.id),
            )

    def test_re_saving_what_governs_is_unchanged_and_reads_no_after(
        self, app, db, monkeypatch, seed_user, seed_periods_today,
    ):
        """Re-save the governing $1,000.00 for its own day: nothing is written.

        UNCHANGED (ruling R-EQ), and ``governing_after`` IS
        ``governing_before`` -- the same object, with exactly ONE read behind
        the whole call: the stager's read of the latest assertion, which is
        dated on the submitted day and so IS the record governing it (ruling
        R-CC85).  It was two until then, the door reading today's before
        itself.  A second read now would be the day's record re-asked for, or
        an after the door re-read after writing nothing.  And the session
        holds no owner's lock at the door's rollback, asked just before it
        runs: since plan step ``balance:X-bn`` no service takes the lock (the
        request's transaction does), so one re-added in the door or the stager
        turns this red.  Asked after the call, as it was until the developer's
        rule-5 answer (2026-09-29, "Move inside the step"), it could not: the
        rollback releases every lock its transaction took (measured
        2026-09-29 with one re-added at the door's top).  *Until plan step
        ``balance:X-bn`` this said the rollback is what releases the lock the
        stager took.*
        """
        with app.app_context():
            (opened,) = self._days_back(seed_user, 10)
            account = _make_checking_account(
                seed_user, anchor_balance="1000.00", observed_on=opened,
            )
            db.session.commit()
            rows = db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).count()
            # Read BEFORE the call: its rollback expires the account.
            user_id = account.user_id
            reads = _spy_governing_reads(monkeypatch)
            held_at_rollback = []
            rollback = type(db.session).rollback

            def probed_rollback(session):
                """Ask whether the owner's key is held, then roll back."""
                held_at_rollback.append(_holds_owner_lock(user_id))
                return rollback(session)

            monkeypatch.setattr(type(db.session), "rollback", probed_rollback)

            report = apply_anchor_true_up(
                account=account, new_balance=Decimal("1000.00"),
                observed_on=opened,
            )

            assert report.outcome is AnchorTrueUpOutcome.UNCHANGED
            # ONE rollback, the door's, so the lock check below measured the
            # moment it names.
            assert len(held_at_rollback) == 1, (
                f"the UNCHANGED save rolled back {len(held_at_rollback)} times "
                f"through the session, where the door rolls back once; the "
                f"lock check below grades nothing unless it is exactly one"
            )
            assert held_at_rollback == [False], (
                "the owner's write lock was held at the UNCHANGED save's "
                "rollback, so a service took it again"
            )
            assert report.governing_after is report.governing_before
            assert (
                report.governing_before.balance,
                report.governing_before.observed_on,
            ) == (Decimal("1000.00"), opened)
            assert [(caller, horizon) for caller, horizon, _ in reads] == [
                ("stage_anchor_true_up", None),
            ]
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).count() == rows

    def test_a_back_dated_save_writes_but_leaves_today_as_it_was(
        self, app, db, seed_user, seed_periods_today,
    ):
        """$1,000.00 at O, $900.00 at O+20; then $250.00 back-dated to O+10.

        Hand-computed, O being 30 days ago: the $250.00 row is appended for
        its own day, so the outcome is COMMITTED -- but an assertion for an
        earlier day does not govern today.  The $900.00 at O+20 governs on
        both sides, so ``governing_after`` equals ``governing_before``: two
        reads, one record.
        """
        with app.app_context():
            opening, back_dated, later = self._days_back(seed_user, 30, 20, 10)
            account = _make_checking_account(
                seed_user, anchor_balance="1000.00", observed_on=opening,
            )
            db.session.commit()
            apply_anchor_true_up(
                account=account, new_balance=Decimal("900.00"),
                observed_on=later,
            )

            report = apply_anchor_true_up(
                account=account, new_balance=Decimal("250.00"),
                observed_on=back_dated,
            )

            assert report.outcome is AnchorTrueUpOutcome.COMMITTED
            assert report.governing_after == report.governing_before
            assert (
                report.governing_before.balance,
                report.governing_before.observed_on,
            ) == (Decimal("900.00"), later)
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id, observed_on=back_dated,
            ).one().anchor_balance == Decimal("250.00")

    def test_every_read_the_door_makes_holds_the_owners_lock(
        self, app, auth_client, db, monkeypatch, seed_user, seed_periods_today,
    ):
        """The stager's read follows the lock; the after-read precedes the commit.

        Asked of PostgreSQL at the instant of each read, by a probe run inside
        the reader: does this session hold the owner's advisory key?  Since
        plan step ``balance:X-bn`` neither the door nor the stager takes it
        -- the signed-in request's command transaction takes it where it
        begins (:mod:`app.db_transaction`) -- so the save is driven through
        its route, ``PATCH /accounts/<id>/true-up``.  What this grades is that
        SOME acquisition on this session precedes both reads; the probe cannot
        tell who took it.  That it is the REQUEST's is graded elsewhere:
        ``tests/test_arch/test_the_owner_lock_has_one_home.py`` refuses a
        service that takes one, and
        ``tests/test_services/test_user_write_lock.py`` turns red when the
        request's is removed.  The stager's read of the latest assertion -- the
        report's before since ruling R-CC85 -- holding it proves the lock came
        first; the after-read holding it proves it came before the commit,
        which releases the key.  The probe answers ``False`` before
        the request and after it, so it can.  *Until that step this called
        :func:`apply_anchor_true_up` directly and graded the stager's own
        acquisition.*
        """
        with app.app_context():
            account_id = seed_user["account"].id
            user_id = seed_user["user"].id
            assert not _holds_owner_lock(user_id)
            reads = _spy_governing_reads(
                monkeypatch, probe=lambda: _holds_owner_lock(user_id),
            )

            response = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1750.00"},
            )

            assert response.status_code == 200, response.status_code
            assert [
                read for read in reads
                if read[0] in ("stage_anchor_true_up", "apply_anchor_true_up")
            ] == [
                ("stage_anchor_true_up", None, True),
                ("apply_anchor_true_up", None, True),
            ]
            assert not _holds_owner_lock(user_id)


class TestTheStagerReadsTheDaysRecordOnlyBeforeTheLatest:
    """Ruling R-CC85, at the stager: the latest record once, the day's when earlier.

    ``stage_anchor_true_up`` reads the latest assertion and hands it back.
    That record IS the one governing any day on or after its own (the proof is
    in the stager's docstring), so only a day BEFORE it reads the day's record
    separately -- and then that separate read, not the latest, is what ruling
    R-EQ compares against.  Graded on the stager directly, so the read list is
    the stager's alone.
    """

    def test_a_day_before_the_latest_is_compared_against_its_own_record(
        self, app, db, monkeypatch, seed_user, seed_periods_today,
    ):
        """$1,000.00 at O and $900.00 at O+20; stage $1,000.00 for O+5, then O.

        Hand-computed, O being 30 days ago.  The latest assertion is the
        $900.00 at O+20, later than both submitted days, so each call reads
        the latest and then the day's own record -- two reads, both the
        stager's -- and hands the latest back either way.

        * **$1,000.00 for O+5 is STAGED.**  The record governing O+5 is the O
          row, (O, $1,000.00), and ruling R-EQ declines only a submission
          equal to it in day AND balance ("the day included", the module's
          words), so (O+5, $1,000.00) is a new assertion: it moves the day the
          fold resets at from O to O+5.  An equal balance is not enough.
        * **$1,000.00 for O is UNCHANGED.**  The record governing O is the O
          row itself, equal in both.  This is the arm the separate read
          exists for: compared against the latest, (O+20, $900.00), no
          back-dated re-save could ever be equal, and each would append a
          duplicate (the defect ``TestBackDatedCashTrueUp`` grades through
          the door).

        Each call is rolled back, so nothing is written and the second arm
        reads what the first did.  Before each rollback, inside the stager's
        transaction, the session holds no owner's lock: since plan step
        ``balance:X-bn`` no service takes it, so one re-added in the stager
        turns this red.  The check followed the last rollback until the
        developer's rule-5 answer (2026-09-29, "Move inside the step"), where
        it could not: the rollbacks release every lock their transaction took
        (measured 2026-09-29 with one re-added at the stager's top).  *Until
        plan step ``balance:X-bn`` this said each rollback releases the lock
        the stager took.*
        """
        with app.app_context():
            user_id = seed_user["user"].id
            opening = display_today() - timedelta(days=30)
            at_o_plus_5 = opening + timedelta(days=5)
            at_o_plus_20 = opening + timedelta(days=20)
            floor = pay_period_service.earliest_recordable_day(user_id)
            assert floor <= opening, (
                f"the schedule starts {floor}; this case needs {opening}"
            )
            account = _make_checking_account(
                seed_user, anchor_balance="1000.00", observed_on=opening,
            )
            db.session.commit()
            apply_anchor_true_up(
                account=account, new_balance=Decimal("900.00"),
                observed_on=at_o_plus_20,
            )
            latest = cash_ledger.resolve_anchor(account)
            assert (latest.balance, latest.observed_on) == (
                Decimal("900.00"), at_o_plus_20,
            )
            rows = db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).count()
            reads = _spy_governing_reads(monkeypatch)

            def stage_1000_for(day):
                """Stage $1,000.00 for *day*; return the report and the row staged."""
                reads.clear()
                staging = anchor_service.stage_anchor_true_up(
                    account=account, new_balance=Decimal("1000.00"),
                    observed_on=anchor_service.resolve_observation_day(
                        user_id, day,
                    ),
                )
                staged_rows = [
                    (row.observed_on, row.anchor_balance)
                    for row in db.session.new
                    if isinstance(row, AccountAnchorHistory)
                ]
                assert not _holds_owner_lock(user_id), (
                    "the owner's write lock was held before the stage's "
                    "rollback, so the stager took it again"
                )
                db.session.rollback()
                return staging, staged_rows

            staging, staged_rows = stage_1000_for(at_o_plus_5)
            assert [(caller, horizon) for caller, horizon, _ in reads] == [
                ("stage_anchor_true_up", None),
                ("stage_anchor_true_up", at_o_plus_5),
            ]
            assert staging.staged is True
            assert staged_rows == [(at_o_plus_5, Decimal("1000.00"))]
            assert staging.latest == latest

            staging, staged_rows = stage_1000_for(opening)
            assert [(caller, horizon) for caller, horizon, _ in reads] == [
                ("stage_anchor_true_up", None),
                ("stage_anchor_true_up", opening),
            ]
            assert staging.staged is False, (
                "the re-save of the O statement was compared against the "
                "LATEST ($900.00 at O+20), which it can never equal"
            )
            assert staged_rows == []
            assert staging.latest == latest

            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).count() == rows


class TestAGridSaveReadsTheGoverningAssertionOncePerFact:
    """R-CC79's and R-CC85's count, through the route: one read per fact.

    Before ruling R-CC79 a back-dated grid save read the governing assertion
    four times -- the route before the write (outside the lock), the stager for
    the submitted day, the response after the commit, and the grid's own draw
    -- and a save for today five, the reconcile prompt asking once more.  That
    ruling had the door read today's before and after under its lock, and the
    draw, the prompt and the coverage boundary all take the after, which left
    three reads on both paths.  **Ruling R-CC85 took the third off a save for
    today**: the door's before and the stager's read of the submitted day were
    the same row whenever the day is on or after the latest assertion's, so the
    stager reads the latest once and hands it back.  A save for today reads
    TWO facts -- the latest (the before, and the record governing today) and
    the after -- and a back-dated save THREE, the day's own record being a
    different fact.  Counted as calls to the one query behind every reader
    (:func:`_spy_governing_reads`) and named by caller, so a read that returns
    is seen by WHO asks as well as by how many.  The boundary's own query
    (``cash_ledger.reconciled_through``) goes through no reader, so the last
    case here grades it by the SQL a request emits.
    """

    @staticmethod
    def _grid_save(auth_client, account_id, day):
        """PATCH the grid editor's save for *day*, as its form submits it."""
        return auth_client.patch(
            f"/accounts/{account_id}/true-up",
            data={"anchor_balance": "1234.56", "observed_on": day.isoformat()},
            headers={"HX-Request": "true"},
        )

    def test_a_back_dated_grid_save_reads_three_facts(
        self, app, auth_client, monkeypatch, seed_user, seed_periods_today,
    ):
        """The latest, the submitted day's record, today's after: three.

        A $1,100.00 assertion for today stands first, so a save back-dated
        five days is not the coverage boundary and the reconcile prompt is not
        asked (the case below is the one that asks it).  It is also what makes
        the save BACK-DATED in ruling R-CC85's sense: the latest assertion is
        later than the submitted day, so the stager reads that day's record for
        itself -- the second read, both by the stager.
        """
        with app.app_context():
            apply_anchor_true_up(
                account=db.session.get(Account, seed_user["account"].id),
                new_balance=Decimal("1100.00"),
            )
            day = display_today() - timedelta(days=5)
            reads = _spy_governing_reads(monkeypatch)

            resp = self._grid_save(auth_client, seed_user["account"].id, day)

            assert resp.status_code == 200, resp.data[:300]
            assert [(caller, horizon) for caller, horizon, _ in reads] == [
                ("stage_anchor_true_up", None),
                ("stage_anchor_true_up", day),
                ("apply_anchor_true_up", None),
            ]

    def test_a_grid_save_for_today_reads_two_facts(
        self, app, auth_client, monkeypatch, seed_user, seed_periods_today,
    ):
        """The ordinary save -- today, as the editor prefills -- reads TWICE.

        The stager's read of the latest assertion, which is dated on or before
        today and so IS the record governing the submitted day (ruling
        R-CC85), and the door's after.  It read three times until that ruling,
        the door asking for the latest itself one statement before the stager.

        A save for today IS the coverage boundary, so the reconcile prompt is
        asked, and an outstanding $50.00 purchase makes it render.  It
        reconciles against the report's after rather than asking the ledger
        (``reconcile.governing_statement``) once more, which it did until
        R-CC79 was finished.
        """
        with app.app_context():
            _make_projected_expense_with_past_dated_entry(
                seed_user, current_pay_period(seed_user["user"].id),
                amount="50.00",
            )
            day = display_today()
            reads = _spy_governing_reads(monkeypatch)

            resp = self._grid_save(auth_client, seed_user["account"].id, day)

            assert resp.status_code == 200, resp.data[:300]
            assert 'id="reconcileModal"' in resp.data.decode(), (
                "the prompt did not render, so this case graded no prompt"
            )
            assert [(caller, horizon) for caller, horizon, _ in reads] == [
                ("stage_anchor_true_up", None),
                ("apply_anchor_true_up", None),
            ]

    def test_nothing_after_the_commit_reads_an_assertion(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The prompt-or-note decision reads the report, not the ledger.

        Everything that decides between the reconcile prompt and the
        acknowledgement -- the coverage boundary, the assertion the prompt
        reconciles against, and the governing balance either side -- is the
        write door's report, read under the owner's lock.  So once the door
        has committed, the request emits no statement against
        ``account_anchor_history`` at all, whichever query would spell it:
        ``reconciled_through``'s ``MAX`` goes through no reader, so the SQL
        timeline is what sees it.  The prompt renders here, so its build is
        inside the window graded.
        """
        with app.app_context():
            _make_projected_expense_with_past_dated_entry(
                seed_user, current_pay_period(seed_user["user"].id),
                amount="50.00",
            )

            resp, after_commit = _statements_after_the_commit(
                lambda: self._grid_save(
                    auth_client, seed_user["account"].id, display_today(),
                ),
            )

            assert resp.status_code == 200, resp.data[:300]
            assert 'id="reconcileModal"' in resp.data.decode()
            assert after_commit, "the request emitted nothing after its commit"
            assert not [
                text for text in after_commit
                if "account_anchor_history" in text
            ], "a statement after the commit read the assertion history"


class TestApplyAnchorTrueUpReraisesUnknownIntegrityError:
    """An unexpected ``IntegrityError`` propagates rather than being swallowed.

    **The ``except IntegrityError`` block this graded was DELETED at plan step
    X-f1c4b** with the index that produced it.  The contract survives the
    deletion and is what this now grades directly: nothing on the true-up path
    may convert a database-level failure into an outcome the route renders as
    success.  Kept rather than deleted because the cost of re-growing such a
    handler is a silent write loss reported as a save, which is the exact defect
    ruling R-EQ was ruled on.
    """

    def test_integrity_error_from_the_resync_is_not_swallowed(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An ``IntegrityError`` raised by the posting re-sync reaches the caller.

        Engineered at the re-sync rather than at a real constraint because
        every constraint the true-up can now violate is a programming error
        with no reachable input: the decision that used to end in a unique
        violation is made before anything is staged.  Patching the one call
        that can still raise from the database keeps the contract testable
        without asserting on a specific constraint.
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)

            with patch(
                "app.services.account_posting_service."
                "sync_account_anchor_postings_all_scenarios",
                side_effect=IntegrityError("stmt", {}, Exception("boom")),
            ):
                with pytest.raises(IntegrityError):
                    apply_anchor_true_up(
                        account=account,
                        new_balance=Decimal("2222.22"),
                    )

            db.session.rollback()
            reloaded = db.session.get(Account, account.id)
            assert cash_ledger.resolve_anchor(reloaded).balance == Decimal(
                "1000.00"
            ), (
                "The failed true-up must not have committed: the seed "
                "origination's $1,000.00 still governs, because the $2,222.22 "
                "assertion was staged in the transaction the IntegrityError "
                "aborted.  Asserting the SURVIVING figure, not merely 'not the "
                "new one' -- a negative passes for any wrong value."
            )


# ``TestApplyAnchorTrueUpStaleConflict.test_helper_returns_stale_conflict_and_rolls_back``
# was DELETED at plan step X-f1c3c (ruling R-EN).  It graded
# ``AnchorTrueUpOutcome.STALE_CONFLICT``, which the enum no longer has: a
# true-up appends an assertion and never UPDATEs the ``accounts`` row that
# ``version_id`` guards, so ``StaleDataError`` is structurally unreachable on
# this path.  The append-only contract that replaced it is graded by
# ``test_accounts.test_stale_data.TestTrueUpIsAppendOnly`` (a stale form still
# records) and by ``test_race_conditions.TestConcurrentAnchorUpdate`` (both
# concurrent requests 200, both assertions survive).


#: Every unique index on either anchor table, with the columns it keys on --
#: the catalog query behind :func:`_content_keys_on_anchor_tables`.  Read from
#: ``pg_index`` rather than ``pg_indexes.indexdef`` because the question is
#: about the COLUMN SET and a text ``LIKE`` over a rendered definition cannot
#: ask it: ``indexdef`` for ``(account_id, id)`` and for
#: ``(account_id, anchor_balance)`` differ only in words.
_ANCHOR_UNIQUE_INDEX_SQL = """
    SELECT c.relname AS tablename,
           i.relname AS indexname,
           array_agg(a.attname) AS columns
    FROM pg_index x
    JOIN pg_class c ON c.oid = x.indrelid
    JOIN pg_class i ON i.oid = x.indexrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(x.indkey)
    WHERE n.nspname = 'budget'
      AND c.relname IN ('account_anchor_history', 'loan_anchor_events')
      AND x.indisunique
      AND NOT x.indisprimary
    GROUP BY c.relname, i.relname
"""


#: The columns an ASSERTION is made of -- ruling R-ES's ``(account, day,
#: balance)`` -- which are the values a transport retry and a deliberate
#: re-assertion SHARE, and so the only columns a unique key may not be built
#: over.  The loan twin's are its own three.
_OBSERVATION_COLUMNS = frozenset({
    "account_id", "observed_on", "anchor_balance",
    "anchor_date",
})


def _content_keys_on_anchor_tables(session):
    """Return the anchor tables' unique indexes that key on OBSERVATION values.

    Ruling **R-EQ**'s predicate, and the whole of it: an index over the
    values a transport retry and a deliberate re-assertion SHARE can refuse
    the correction, because the two are byte-identical by construction.  Two
    shapes are therefore NOT content keys, and each is named rather than
    excused: one that CONTAINS the primary key cannot refuse anything (the
    clearing links' target ``uq_anchor_history_account_id``), and one over a
    foreign key to the row's SOURCE keys an identity rather than a value --
    ``uq_anchor_history_statement_import`` says an import places its figure
    on at most one day, and a re-import is a NEW import row with its own id,
    so no legitimate act can collide on it (plan step ``balance:X-bj-1``,
    developer ruling 2026-09-16).  What R-EQ forbids is exactly a key built
    from :data:`_OBSERVATION_COLUMNS` and nothing else.

    Args:
        session: The test session, inside an app context.

    Returns:
        ``{(tablename, indexname), ...}`` for every offending index -- empty
        when the schema holds R-EQ.
    """
    return {
        (row.tablename, row.indexname)
        for row in session.execute(sa.text(_ANCHOR_UNIQUE_INDEX_SQL))
        if set(row.columns) <= _OBSERVATION_COLUMNS
    }


class TestApplyAnchorTrueUpModuleContract:
    """Pins the public surface of the module so renames are caught."""

    def test_outcome_enum_has_exactly_two_members(self):
        """The outcome enum is the route's switch discriminant; pin its size.

        Adding a new outcome would require route-side handling for every
        consumer; this fails loud if the enum grows without a coordinated
        route update.

        **It pinned THREE members until plan step X-f1c3c** and was briefly
        deleted there, on the mistaken ground that it graded
        ``STALE_CONFLICT`` -- which ruling R-EN did delete.  It does not: it
        grades the member SET, and that contract is fully alive with two
        members.  Restored after an adversarial review caught the deletion,
        because "the enum shrank" and "nobody is watching the enum any more"
        are not distinguishable from a green suite.
        """
        assert {m.name for m in AnchorTrueUpOutcome} == {
            "COMMITTED", "UNCHANGED",
        }

    def test_neither_anchor_table_carries_a_uniqueness_guard(self, app, db):
        """Ruling R-EQ's structural claim, asserted against the LIVE schema.

        The duplicate rule is a write-door comparison, and re-adding a unique
        index over either anchor table's own values would silently restore the
        false refusal ``TestApplyAnchorTrueUpUnchanged`` grades -- the door's
        compare would pass, the INSERT would collide, and the caller would see
        an ``IntegrityError`` on a legitimate correction.

        Asserted against the LIVE catalog rather than ``__table_args__`` because
        the model and the database can disagree: a model-only assertion passes
        on a database whose index was never dropped, which is the exact state
        migration ``a3f6c1d84b90`` exists to leave behind.

        **It grades a CONTENT key rather than "any unique index", and that
        narrowing is plan step X-f3a-1's** (developer, 2026-08-14).  This
        asserted the EMPTY SET until ``uq_anchor_history_account_id`` -- the
        ``(account_id, id)`` superkey the clearing links' composite foreign keys
        must target -- and the blanket form would have refused it while R-EQ has
        nothing to say about it: an index containing the primary key can reject
        NO row, so it cannot refuse a re-assertion.  What R-EQ forbids is a key
        over values a transport retry and a deliberate correction SHARE, which
        is exactly a key that OMITS the primary key.
        :func:`_content_keys_on_anchor_tables` is that predicate, and the test
        below plants the deleted index to show it still fires.  **A second
        narrowing at plan step ``balance:X-bj-1``** (developer, 2026-09-16):
        a key over a foreign key to the row's SOURCE
        (``uq_anchor_history_statement_import``) is identity, not content --
        a re-import is a new import row -- so the predicate names the
        observation columns R-EQ is about rather than "anything without the
        primary key".
        """
        with app.app_context():
            offenders = _content_keys_on_anchor_tables(db.session)
            assert offenders == set(), (
                f"An anchor table has regrown a uniqueness guard: "
                f"{sorted(offenders)}.  Ruling R-EQ puts the duplicate "
                f"rule at the write door, which can compare against what "
                f"governs; an index over the row's values cannot, and refuses "
                f"a legitimate re-assertion."
            )

    def test_the_content_key_predicate_fires_on_the_index_r_eq_deleted(
        self, app, db,
    ):
        """Plant the index R-EQ deleted; see the predicate report it.

        The firing control for the test above.  A predicate that answers "no
        content key" is worth exactly what its ability to FIND one is worth, and
        the sharpened form deliberately admits superkeys -- so the case that
        matters is whether the index ruling R-EQ actually deleted would still be
        caught.

        The planted key is that index's shape:
        ``(account_id, anchor_balance, observed_on)``, the three values a
        network retry and a deliberate re-assertion of an earlier figure share
        by construction.  Dropped in a ``finally`` so the per-test database is
        handed back unchanged whatever the assertion does.
        """
        with app.app_context():
            db.session.execute(sa.text(
                "CREATE UNIQUE INDEX uq_anchor_history_content_key_probe "
                "ON budget.account_anchor_history "
                "(account_id, anchor_balance, observed_on)"
            ))
            try:
                offenders = _content_keys_on_anchor_tables(db.session)
                assert (
                    "account_anchor_history",
                    "uq_anchor_history_content_key_probe",
                ) in offenders, (
                    "The content-key predicate did not report a planted key "
                    "over (account_id, anchor_balance, observed_on) -- the "
                    "exact index ruling R-EQ deleted.  A green "
                    "test_neither_anchor_table_carries_a_uniqueness_guard "
                    "would then be worth nothing."
                )
            finally:
                db.session.execute(sa.text(
                    "DROP INDEX budget.uq_anchor_history_content_key_probe"
                ))


# ── Loan Anchor Trueup ────────────────────────────────────────────────


def _make_loan_account(seed_user, name="Helper Loan",
                       original_principal="20000.00",
                       interest_rate="0.05000",
                       term_months=60,
                       origination_date=None):
    """Create a fresh Auto Loan account + LoanParams + origination event.

    Builds the minimal loan inventory the loan-anchor true-up tests
    need: an account, a :class:`LoanParams` row, and an origination
    :class:`LoanAnchorEvent`.  Mirrors the production setup in
    :func:`app.routes.loan.create_params` so the same anchor-event
    invariants the resolver relies on are satisfied.

    Returns the Account ORM instance.
    """
    if origination_date is None:
        origination_date = date.today() - timedelta(days=365)

    auto_type = db.session.query(AccountType).filter_by(
        name="Auto Loan",
    ).one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=auto_type.id,
            name=name,
            anchor_balance=Decimal(original_principal),
        ),
    )
    db.session.flush()

    params = LoanParams(
        account_id=account.id,
        original_principal=Decimal(original_principal),
        term_months=term_months,
        origination_date=origination_date,
        payment_day=1,
    )
    db.session.add(params)
    db.session.flush()

    insert_origination_rate(params, Decimal(interest_rate))

    db.session.add(LoanAnchorEvent(
        account_id=account.id,
        anchor_date=origination_date,
        anchor_balance=Decimal(original_principal),
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.ORIGINATION,
        ),
    ))
    db.session.commit()
    return account


class TestApplyLoanAnchorTrueUpCommitted:
    """COMMITTED outcome: helper appends an event and commits."""

    def test_commits_appends_new_event_without_mutating_prior(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Helper writes a single new user_trueup event; prior rows untouched.

        Hand-check: fixture seeds exactly one origination event at
        $20,000 dated 365 days ago.  After
        :func:`apply_loan_anchor_true_up` with anchor $18,500 dated
        today:
          * Outcome is COMMITTED.
          * Total event count is 2 (origination + new trueup).
          * The new event has ``source_id`` == USER_TRUEUP id, the
            posted balance, and the posted date.
          * The origination event is byte-identical (no UPDATE).
            Compared by primary key + every persisted column to
            prove append-only semantics.
          * :class:`LoanParams` is unchanged: the trueup writes an
            event, never a params column (the balance has none since
            plan step R20 dropped the demoted seed).
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            db.session.commit()

            origination = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id)
                .one()
            )
            orig_snapshot = (
                origination.id,
                origination.anchor_date,
                origination.anchor_balance,
                origination.source_id,
                origination.created_at,
            )
            params_before = (
                db.session.query(LoanParams)
                .filter_by(account_id=account.id)
                .one()
            )
            params_snapshot = (
                params_before.original_principal, params_before.term_months,
                params_before.origination_date, params_before.payment_day,
                params_before.updated_at,
            )

            outcome = apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("18500.00"),
                anchor_date=date.today(),
            )

            assert outcome is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            events = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id)
                .order_by(LoanAnchorEvent.id)
                .all()
            )
            assert len(events) == 2, (
                "Trueup must append exactly one new event."
            )

            origination_after = next(e for e in events if e.id == orig_snapshot[0])
            after_snapshot = (
                origination_after.id,
                origination_after.anchor_date,
                origination_after.anchor_balance,
                origination_after.source_id,
                origination_after.created_at,
            )
            assert after_snapshot == orig_snapshot, (
                "Prior origination event must NOT be mutated by a "
                "trueup (LoanAnchorEvent is structurally append-only)."
            )

            trueup = next(e for e in events if e.id != orig_snapshot[0])
            user_trueup_source_id = ref_cache.loan_anchor_source_id(
                LoanAnchorSourceEnum.USER_TRUEUP,
            )
            assert trueup.source_id == user_trueup_source_id
            assert trueup.anchor_balance == Decimal("18500.00")
            assert trueup.anchor_date == date.today()

            # The trueup must NOT mutate :class:`LoanParams`: the balance
            # is the event's, and the params row is not written at all.
            params_after = (
                db.session.query(LoanParams)
                .filter_by(account_id=account.id)
                .one()
            )
            assert (
                params_after.original_principal, params_after.term_months,
                params_after.origination_date, params_after.payment_day,
                params_after.updated_at,
            ) == params_snapshot


def _loan_with_open_books(seed_user):
    """A $20,000.00 loan from 2025-01-01, due the 1st, whose books open at its origination.

    :func:`_make_loan_account` opens the account's books TODAY, so the
    ledger refuses a payment dated before it; ruling R-R114's refusal is
    about payments recorded before a tracking start, which need books open
    earlier (:func:`tests._test_helpers.create_loan_account`).
    """
    account = create_loan_account(
        seed_user, db.session, name="Tracked Loan",
        principal=Decimal("20000.00"), rate=Decimal("0.05000"), term=60,
        origination_date=date(2025, 1, 1), payment_day=1,
    )
    db.session.commit()
    return account


def _anchor_rows(account):
    """Return ``[(date, balance, source id)]`` for every stored anchor row, by id.

    A query, so it autoflushes the session first: a row the code under test
    staged is counted whether or not it was flushed.
    """
    return [
        (row.anchor_date, row.anchor_balance, row.source_id)
        for row in db.session.query(LoanAnchorEvent)
        .filter_by(account_id=account.id)
        .order_by(LoanAnchorEvent.id)
    ]


class TestRecordLoanTrackingStart:
    """The tracking-start opening flow appends a tracking_start event and re-syncs."""

    def test_commits_appends_tracking_start_opening(
        self, app, db, seed_user, seed_periods_today,
    ):
        """record_loan_tracking_start appends one tracking_start event as the opening.

        The fixture loan opens at $20,000 origination.  After a tracking-start of
        $18,000 dated today:
          * outcome is COMMITTED
          * exactly one tracking_start event is appended (origination + tracking = 2)
          * it carries the TRACKING_START source, balance, and date
          * the loan still OPENS at its $20,000 origination (step C1); the
            tracking-start loads as a non-opening assertion (``is_opening`` False,
            ``is_tracking_start`` True, balance 18000) that RESETS the walk.
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            db.session.commit()

            outcome = record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=date.today(),
            )
            assert outcome is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            events = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id)
                .all()
            )
            assert len(events) == 2
            tracking_source_id = ref_cache.loan_anchor_source_id(
                LoanAnchorSourceEnum.TRACKING_START,
            )
            tracking = next(
                e for e in events if e.source_id == tracking_source_id
            )
            assert tracking.anchor_balance == Decimal("18000.00")
            assert tracking.anchor_date == date.today()

            params = (
                db.session.query(LoanParams)
                .filter_by(account_id=account.id)
                .one()
            )
            facts = loan_loaders.load_loan_anchor_facts(params)
            (opening,) = [fact for fact in facts if fact.is_opening]
            assert opening.is_tracking_start is False
            assert opening.anchor_balance == Decimal("20000.00")
            (tracking_fact,) = [
                fact for fact in facts if fact.is_tracking_start
            ]
            assert tracking_fact.is_opening is False
            assert tracking_fact.anchor_balance == Decimal("18000.00")

    def test_double_call_same_returns_unchanged(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A resubmit of the governing (date, balance) is idempotent."""
        with app.app_context():
            account = _make_loan_account(seed_user)
            db.session.commit()

            first = record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=date.today(),
            )
            second = record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=date.today(),
            )
            assert first is AnchorTrueUpOutcome.COMMITTED
            assert second is AnchorTrueUpOutcome.UNCHANGED

    def test_resubmit_is_recognised_after_a_later_trueup(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A re-submitted OPENING is recognised even under a later true-up.

        Ruling R-EQ scopes the loan comparison to the submission's own SOURCE,
        and this is why.  A ``tracking_start`` is by definition the earliest
        anchor, so once any ``user_trueup`` is recorded the latest row of ANY
        source is that true-up -- and comparing against it would let a
        double-submitted opening append a second ``tracking_start``, silently
        duplicating the assertion the operator meant to record once.

        *The reason stated here used to be that
        ``loan_loaders._opening_anchor_fact`` reads a tracking-start as the
        loan's opening whatever else was recorded later.  That function was
        deleted at step C1 and origination is the opening ALWAYS; the test is
        unchanged because the per-source scoping is right either way (plan step
        X-an-b).*
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            db.session.commit()
            opening_day = date.today() - timedelta(days=30)

            assert record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=opening_day,
            ) is AnchorTrueUpOutcome.COMMITTED
            assert apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("17000.00"),
                anchor_date=date.today(),
            ) is AnchorTrueUpOutcome.COMMITTED

            # The latest row of ANY source is now the true-up; the opening is
            # still what a re-submitted opening duplicates.
            assert record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=opening_day,
            ) is AnchorTrueUpOutcome.UNCHANGED

            db.session.expire_all()
            tracking_source_id = ref_cache.loan_anchor_source_id(
                LoanAnchorSourceEnum.TRACKING_START,
            )
            openings = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id, source_id=tracking_source_id)
                .all()
            )
            assert len(openings) == 1, (
                f"A re-submitted opening must append nothing; found "
                f"{len(openings)} tracking_start rows."
            )

    def test_refuses_on_or_after_the_day_a_payment_moved_money_and_writes_nothing(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Rulings R-R114 and R-BAL155: a tracking start on or after a payment's money day is refused.

        A $400.00 payment settled on period 1's first day moved money that
        day.  A tracking start that day or ten days later raises
        :class:`TrackingStartRefused` naming both days and appends nothing; the
        day before is a start and is recorded.
        """
        with app.app_context():
            account = _loan_with_open_books(seed_user)
            paid_on = seed_periods_today[1].start_date
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], account,
                seed_periods_today[1], amount=Decimal("400.00"),
                settled_on=paid_on,
            )
            db.session.commit()
            stored = _anchor_rows(account)

            for asked in (paid_on, paid_on + timedelta(days=10)):
                with pytest.raises(TrackingStartRefused) as refused:
                    record_loan_tracking_start(
                        account=account,
                        anchor_balance=Decimal("18000.00"),
                        anchor_date=asked,
                    )
                assert (refused.value.asked, refused.value.moved_on) == (
                    asked, paid_on,
                )
                # Counted in the refused transaction, BEFORE any rollback: the
                # query autoflushes, so a row staged ahead of the raise shows.
                assert _anchor_rows(account) == stored
                db.session.rollback()
            assert record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=paid_on - timedelta(days=1),
            ) is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            tracking_source_id = ref_cache.loan_anchor_source_id(
                LoanAnchorSourceEnum.TRACKING_START,
            )
            assert [
                event.anchor_date
                for event in db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id, source_id=tracking_source_id)
                .all()
            ] == [paid_on - timedelta(days=1)]

    def test_a_true_up_after_a_recorded_payment_is_not_refused(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The refusal is the tracking-start source's alone: a correction there is recorded."""
        with app.app_context():
            account = _loan_with_open_books(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], account,
                seed_periods_today[1], amount=Decimal("400.00"),
                settled_on=seed_periods_today[1].start_date,
            )
            db.session.commit()

            assert apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=date.today(),
            ) is AnchorTrueUpOutcome.COMMITTED

    def test_a_zero_close_counts_from_no_day(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Ruling R-BAL155: a $0.00 close moved no money, so only the real payment refuses.

        Period 1's payment is closed at $0.00 (no movement) and period 3's
        $400.00 payment settles on its first day.  A tracking start the day
        after the close is recorded -- the close counts from no day, neither
        its settle day nor the installment it skips -- and one on the $400.00
        payment's day is refused, naming THAT day.
        """
        with app.app_context():
            account = _loan_with_open_books(seed_user)
            closed_on = seed_periods_today[1].start_date
            paid_on = seed_periods_today[3].start_date
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], account,
                seed_periods_today[1], amount=Decimal("400.00"),
                settled_amount=Decimal("0.00"), settled_on=closed_on,
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], account,
                seed_periods_today[3], amount=Decimal("400.00"),
                settled_on=paid_on,
            )
            db.session.commit()

            assert record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=closed_on + timedelta(days=1),
            ) is AnchorTrueUpOutcome.COMMITTED
            with pytest.raises(TrackingStartRefused) as refused:
                record_loan_tracking_start(
                    account=account,
                    anchor_balance=Decimal("17500.00"),
                    anchor_date=paid_on,
                )
            assert refused.value.moved_on == paid_on

    def test_a_payment_in_another_scenario_refuses(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A tracking start is the ACCOUNT's, so a payment in any scenario refuses it.

        The $400.00 payment settles in a what-if scenario only; the baseline
        holds none.  The tracking start re-bases the walk in every scenario
        the loan has a payment in, so a date after the what-if payment is
        refused all the same.
        """
        with app.app_context():
            account = _loan_with_open_books(seed_user)
            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if", is_baseline=False,
            )
            db.session.add(whatif)
            db.session.flush()
            paid_on = seed_periods_today[1].start_date
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], account,
                seed_periods_today[1], amount=Decimal("400.00"),
                settled_on=paid_on, scenario=whatif,
            )
            db.session.commit()

            with pytest.raises(TrackingStartRefused) as refused:
                record_loan_tracking_start(
                    account=account,
                    anchor_balance=Decimal("18000.00"),
                    anchor_date=paid_on + timedelta(days=1),
                )
            assert refused.value.moved_on == paid_on

    def test_a_resubmit_a_payment_now_contradicts_is_refused_not_unchanged(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The refusal precedes ruling R-EQ's duplicate rule.

        A tracking start recorded on period 1's first day stands; a payment
        is then settled that same day.  Resubmitting the identical tracking
        start is refused, not answered UNCHANGED: the statement it repeats is
        one the payment now contradicts.
        """
        with app.app_context():
            account = _loan_with_open_books(seed_user)
            started_on = seed_periods_today[1].start_date
            assert record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=started_on,
            ) is AnchorTrueUpOutcome.COMMITTED
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], account,
                seed_periods_today[1], amount=Decimal("400.00"),
                settled_on=started_on,
            )
            db.session.commit()

            with pytest.raises(TrackingStartRefused):
                record_loan_tracking_start(
                    account=account,
                    anchor_balance=Decimal("18000.00"),
                    anchor_date=started_on,
                )

    def test_the_setup_door_stages_by_the_same_rule(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Ruling R-R115: the setup door's staging refuses as the dashboard's door does.

        :func:`stage_loan_tracking_start` shares the one constructor, so a
        day on or after the payment's is refused before anything is staged,
        and the day before is staged.
        """
        with app.app_context():
            account = _loan_with_open_books(seed_user)
            paid_on = seed_periods_today[1].start_date
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], account,
                seed_periods_today[1], amount=Decimal("400.00"),
                settled_on=paid_on,
            )
            db.session.commit()
            stored = _anchor_rows(account)

            with pytest.raises(TrackingStartRefused) as refused:
                stage_loan_tracking_start(
                    account=account,
                    anchor_balance=Decimal("18000.00"),
                    anchor_date=paid_on,
                )
            assert refused.value.moved_on == paid_on
            # Counted in the refused transaction, BEFORE the rollback: the
            # query autoflushes, so a row staged (or staged and flushed) ahead
            # of the raise shows, which ``session.new`` alone cannot see once
            # it is flushed.
            assert _anchor_rows(account) == stored
            db.session.rollback()

            assert stage_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=paid_on - timedelta(days=1),
            ) is True
            assert [
                (row.anchor_date, row.anchor_balance)
                for row in db.session.new
                if isinstance(row, LoanAnchorEvent)
            ] == [(paid_on - timedelta(days=1), Decimal("18000.00"))]
            db.session.rollback()


class TestApplyLoanAnchorTrueUpUnchanged:
    """UNCHANGED: a resubmit of the governing (date, balance) is idempotent."""

    def test_double_call_same_balance_returns_unchanged(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Two identical trueups produce exactly one new event row.

        Hand-check: :func:`apply_loan_anchor_true_up` called twice
        with the same ``(date, balance)`` returns ``COMMITTED`` then
        ``UNCHANGED``, and the event log shows exactly one trueup row
        (plus the origination).
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            today = date.today()

            outcome_first = apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("17000.00"),
                anchor_date=today,
            )
            assert outcome_first is AnchorTrueUpOutcome.COMMITTED

            outcome_second = apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("17000.00"),
                anchor_date=today,
            )
            assert outcome_second is AnchorTrueUpOutcome.UNCHANGED

            db.session.expire_all()
            trueups = (
                db.session.query(LoanAnchorEvent)
                .filter_by(
                    account_id=account.id,
                    anchor_balance=Decimal("17000.00"),
                    anchor_date=today,
                )
                .all()
            )
            assert len(trueups) == 1, (
                "A repeated submission must append nothing: expected exactly "
                "one event row at that (date, balance)."
            )

    def test_reasserting_a_superseded_balance_is_recorded(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Re-asserting a loan balance that was corrected away is a real change.

        The loan twin of the checking regression, and the one whose exposure is
        LIVE today: the loan form already lets the user type the date, so the
        deleted ``uq_loan_anchor_events_acct_date_bal_day`` could refuse this
        sequence on any recording day.  ``$17,000`` -> ``$16,500`` -> back to
        ``$17,000`` for one anchor date; the third changes what governs and
        must be recorded.
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            today = date.today()

            for balance in ("17000.00", "16500.00", "17000.00"):
                assert apply_loan_anchor_true_up(
                    account=account,
                    anchor_balance=Decimal(balance),
                    anchor_date=today,
                ) is AnchorTrueUpOutcome.COMMITTED, (
                    f"Asserting ${balance} changed what governs, so it is a "
                    "fact and must be recorded."
                )

            db.session.expire_all()
            trueup_source_id = ref_cache.loan_anchor_source_id(
                LoanAnchorSourceEnum.USER_TRUEUP,
            )
            governing = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id, source_id=trueup_source_id)
                .order_by(
                    LoanAnchorEvent.anchor_date.desc(),
                    LoanAnchorEvent.created_at.desc(),
                    LoanAnchorEvent.id.desc(),
                )
                .first()
            )
            assert governing.anchor_balance == Decimal("17000.00"), (
                "The correction back to $17,000.00 must be what governs; the "
                "deleted unique index left $16,500.00 governing while the "
                "route flashed that the balance was already recorded."
            )

    def test_back_dated_resubmit_is_idempotent(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A re-submitted BACK-DATED true-up appends nothing.

        **The defect the first version of ruling R-EQ's implementation had, and
        the reason the comparison is scoped to the submitted DATE.**  Comparing
        against the account's LATEST event instead, a submission for an earlier
        date can never compare equal -- so every double-click on a back-dated
        correction appended another row, permanently, in a table whose ORM
        guards refuse UPDATE and DELETE.  Two independent adversarial reviews
        reproduced it here, on the door that has had a user-supplied date field
        since Commit 16.

        Sequence: a true-up for TODAY, then one back-dated 10 days, then the
        back-dated one again.  The third governs the same date as the second and
        changes nothing, so it must write nothing -- even though a LATER event
        exists.
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            back_dated = date.today() - timedelta(days=10)

            assert apply_loan_anchor_true_up(
                account=account, anchor_balance=Decimal("17000.00"),
                anchor_date=date.today(),
            ) is AnchorTrueUpOutcome.COMMITTED
            assert apply_loan_anchor_true_up(
                account=account, anchor_balance=Decimal("16000.00"),
                anchor_date=back_dated,
            ) is AnchorTrueUpOutcome.COMMITTED
            assert apply_loan_anchor_true_up(
                account=account, anchor_balance=Decimal("16000.00"),
                anchor_date=back_dated,
            ) is AnchorTrueUpOutcome.UNCHANGED, (
                "The re-submitted back-dated true-up changes nothing at its own "
                "date, so it must append nothing -- comparing against the LATEST "
                "event instead makes every back-dated retry a duplicate."
            )

            db.session.expire_all()
            rows = (
                db.session.query(LoanAnchorEvent)
                .filter_by(
                    account_id=account.id,
                    anchor_date=back_dated,
                    anchor_balance=Decimal("16000.00"),
                )
                .all()
            )
            assert len(rows) == 1, (
                f"Expected exactly one event at the back-dated key, found "
                f"{len(rows)} -- each surplus row renders a phantom line on the "
                f"loan dashboard's drift card and cannot be deleted."
            )

    def test_same_day_different_balance_both_commit(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Same-day trueups with different balances both succeed.

        A legitimate same-day correction (the user noticed an error and
        re-trued at a different amount) MUST NOT be blocked.  Both calls
        return ``COMMITTED``; the resolver's (anchor_date, created_at) DESC
        ordering naturally picks the later one for display.
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            today = date.today()

            outcome_a = apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("17000.00"),
                anchor_date=today,
            )
            outcome_b = apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("16500.00"),
                anchor_date=today,
            )
            assert outcome_a is AnchorTrueUpOutcome.COMMITTED
            assert outcome_b is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            balances = {
                row.anchor_balance for row in
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id)
                .all()
            }
            assert Decimal("17000.00") in balances
            assert Decimal("16500.00") in balances


class TestApplyLoanAnchorTrueUpReraisesUnknownIntegrityError:
    """An unexpected ``IntegrityError`` propagates rather than being swallowed.

    The loan twin of the checking class above.  Its ``except IntegrityError``
    lived in ``loan_posting_service.sync_all_scenarios_or_duplicate``, which the
    loan anchor path stopped calling at plan step X-f1c4b (that helper survives
    for the ARM rate change, whose table is EDITABLE and whose unique key is a
    real business rule).  The contract it protected is graded here directly.
    """

    def test_integrity_error_from_the_resync_is_not_swallowed(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An ``IntegrityError`` raised by the loan re-sync reaches the caller."""
        with app.app_context():
            account = _make_loan_account(seed_user)
            today = date.today()

            with patch(
                "app.services.loan_posting_service."
                "sync_loan_postings_all_scenarios",
                side_effect=IntegrityError("stmt", {}, Exception("boom")),
            ):
                with pytest.raises(IntegrityError):
                    apply_loan_anchor_true_up(
                        account=account,
                        anchor_balance=Decimal("19000.00"),
                        anchor_date=today,
                    )

            db.session.rollback()
            trueups_at_balance = (
                db.session.query(LoanAnchorEvent)
                .filter_by(
                    account_id=account.id,
                    anchor_balance=Decimal("19000.00"),
                )
                .all()
            )
            assert not trueups_at_balance, (
                "The failed true-up must not have committed: the event was "
                "staged in the transaction the IntegrityError aborted."
            )


class TestApplyAnchorTrueUpKindGate:
    """The D4 / A1 amortizing-kind guard on the CASH true-up entry point."""

    def test_refuses_amortizing_loan_before_staging(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An amortizing account raises, with NOTHING staged or written.

        Finding B-15: this entry point wrote
        ``accounts.current_anchor_balance`` for a loan -- a second,
        stored, never-reconciled loan balance.  That COLUMN is deleted (ruling
        R-EH, plan step X-f1c3c) and the sentence above is historical, naming
        what the guard was written against; a mechanical rename over this file
        had rewritten it to say the guard prevented writing
        ``cash_ledger.resolve_anchor(accounts).balance``, which is a read and
        was never what B-15 was about.

        The guard fires before ``stage_anchor_true_up``, so the session holds
        no pending mutation and no history row: the RESOLVED balance, the
        history count, and the session's dirty/new sets are all unchanged.
        """
        with app.app_context():
            mortgage_type = db.session.query(AccountType).filter_by(
                name="Mortgage",
            ).one()
            loan = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=mortgage_type.id,
                    name="Kind Gate Loan",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.commit()
            resolved_before = cash_ledger.resolve_anchor(loan).balance
            history_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=loan.id).count()
            )

            with pytest.raises(
                anchor_service.AmortizingAccountAnchorError,
            ) as excinfo:
                apply_anchor_true_up(
                    account=loan,
                    new_balance=Decimal("1.00"),
                )

            # The message names the correct path for the caller.
            assert "apply_loan_anchor_true_up" in str(excinfo.value)
            # Raised BEFORE staging: session clean, nothing written.
            assert not db.session.dirty
            assert not db.session.new
            assert cash_ledger.resolve_anchor(loan).balance == resolved_before
            assert (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=loan.id).count()
            ) == history_before
