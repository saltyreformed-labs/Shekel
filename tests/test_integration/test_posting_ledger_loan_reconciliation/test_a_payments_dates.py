"""A loan payment's dates: a late-paid payment, a true-up after the last payment, and a due-date
edit, each reconciling the ledger.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services import (
    balance_at,
    loan_anchor_service,
    loan_posting_service,
    pay_period_write,
    transfer_service,
)
from app.services.anchor_service import AnchorTrueUpOutcome
from app.services.installment_calendar import monthly_due_date
from app.services.balance_at import BalanceContext
from app.services.balance_at._resolution import resolved_loan
from app.services.liability_sign import owed
from tests._test_helpers import rhythm_of, last_covered_day

from ._helpers import (
    _ANCHOR_BALANCE,
    _AS_OF,
    _make_loan,
    _confirmed_rows_at,
    _settle,
    _ledger_balance,
    _posted_balance,
)


class TestLatePaidPaymentDating:
    """A payment settled LATE is credited to the installment it actually paid.

    The real-world case the suite was blind to, and the root defect it hid.  A
    monthly loan payment is due on the loan's ``payment_day``, but the operator
    marks it paid whenever they actually pay it -- and over a weekend or a
    holiday that is routinely a few days late, which lands the transaction in the
    NEXT biweekly pay period.  Its pay period then no longer contains its due
    date.

    The engine used to RE-DERIVE each payment's due date from its pay period
    (``monthly_due_date`` of the period start).  For a late payment that returns
    the FOLLOWING month's installment: a February payment recorded as a March
    one.  Two consequences, both pinned here:

    * the payment history / amortization table shows an installment the operator
      never paid (the reported symptom); and
    * the CONFIRMED schedule row is stamped with a date that can be in the
      FUTURE, so every date-basis balance walk that reads the schedule silently
      disagrees with the ledger, which books by pay period.

    The engine now reads the payment's OWN stored ``due_date``
    (``loan_loaders.loan_payment_due_date``), so the pay period governs only the
    CASH (which period booked it) and the due date governs only the INSTALLMENT.
    """

    def _settle_late(self, seed_user, loan, db, seed_periods):
        """Settle a payment due 2026-02-01 LATE, into the 2026-02-13 period.

        ``seed_periods[2]`` (2026-01-30 .. 02-12) is the period that CONTAINS the
        2026-02-01 due date.  Paying late lands the payment in
        ``seed_periods[3]`` (2026-02-13 .. 02-26) instead -- a period that does
        not contain its own due date, exactly the production shape.  The stored
        ``due_date`` still records the installment it paid.
        """
        payment = _settle(seed_user, loan, seed_periods[3])
        # On the TRANSFER, where a payment's installment is read since plan step
        # balance:X-bi-6-4b.  It wrote the shadow's copy alone until then
        # (rule-5 re-expression, developer approval 2026-09-24), and the
        # shadow's copy beside it until plan step balance:X-bi-6-4d-2 made a
        # transfer one row (ruling R-BAL167 class 1).
        payment.due_date = date(2026, 2, 1)
        db.session.flush()
        loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
        db.session.commit()
        return payment

    def test_late_payment_is_dated_at_the_installment_it_paid(
        self, app, db, seed_user, seed_periods,
    ):
        """The history row reads 2026-02-01, not the re-derived 2026-03-01.

        Non-vacuous: the pre-fix derivation is
        ``monthly_due_date(seed_periods[3].start_date, payment_day=1)`` ==
        2026-03-01, asserted here to be a DIFFERENT date -- so a producer that
        still derived from the pay period would report March and fail.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, name="Late Paid Loan")
            self._settle_late(seed_user, loan, db, seed_periods)

            # The re-derivation the engine used to perform lands a month later.
            assert monthly_due_date(seed_periods[3].start_date, 1) == date(
                2026, 3, 1,
            )

            rows = _confirmed_rows_at(loan.id, scenario_id, _AS_OF)
            assert [row.payment_date for row in rows] == [date(2026, 2, 1)]

            history = loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, _AS_OF,
            )
            assert [row.installment for row in history] == [date(2026, 2, 1)]

    def test_every_surface_equals_the_ledger_for_a_late_payment(
        self, app, db, seed_user, seed_periods,
    ):
        """Ledger, loan card, seam scalar and per-period map all agree at today.

        The cross-producer lock for the late-paid shape.  Before the fix the
        confirmed row carried a FUTURE date (2026-03-01 > _AS_OF), so the seam's
        date-basis scalar walked past it and read the loan's PRE-payment balance
        while the ledger had already booked the paydown -- the divergence that
        surfaced as a mortgage that appeared to GROW.

        The seam reports the balance HELD (negative for a loan, ruling R-CC47),
        so its scalar and its map are compared through ``owed()``: what the loan
        OWES, the sign the ledger and the reader state.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, name="Late Paid Surfaces")
            self._settle_late(seed_user, loan, db, seed_periods)
            scenario = seed_user["scenario"]

            ledger = _ledger_balance(loan.id, scenario_id)
            # The dated posting window (entry_date <= as_of) --
            # the fourth independent access path beside the raw posting sum,
            # the seam scalar, and the seam map (the seeded resolver window
            # died with LoanState.current_balance at plan step D2a).
            reader = _posted_balance(loan.id, scenario_id)
            bctx = BalanceContext(
                user_id=loan.user_id, scenario=scenario, as_of=_AS_OF,
            )
            scalar = balance_at.balance_at(loan, bctx, _AS_OF)
            period_map = balance_at.build_maps(
                [loan], bctx,
            )[loan.id]
            current_period = next(
                p for p in seed_periods
                if p.start_date <= _AS_OF <= last_covered_day(p)
            )

            # The ARITHMETIC, pinned first: agreement alone is not enough, since
            # a bug that moved all four producers TOGETHER (they now share one
            # ledger source) would keep them equal while being uniformly wrong.
            #   anchor 100,000.00 @ 6% -> interest = 100000 * 0.06/12 = 500.00
            #   principal = cash 1000.00 - 500.00 = 500.00
            #   balance   = 100000.00 - 500.00 = 99,500.00
            assert ledger == Decimal("99500.00")
            assert reader == ledger
            assert owed(scalar) == ledger
            assert owed(period_map[current_period.id]) == ledger
            # The paydown is real: the balance is strictly below the anchor.
            assert ledger < _ANCHOR_BALANCE

    def test_projected_liability_map_never_increases(
        self, app, db, seed_user, seed_periods,
    ):
        """What a loan OWES per period is monotonically non-increasing.

        The general invariant that makes this whole bug class impossible to ship
        again: a loan amortizes, so its debt cannot GROW.  This is the defect the
        operator saw plotted -- a mortgage that rose $276.72 for one period and
        then fell back -- and it needed BOTH halves of the bug to appear, so this
        reproduces both.

        Geometry (payment_day=5, today=2026-05-15).  ``_settle_late`` alone is not
        enough: the rise only shows when a mis-dated CONFIRMED row lands in the
        FUTURE and a projected period ENDS BEFORE it, so the projection falls back
        to an EARLIER confirmed row carrying a higher balance.

        * An on-time payment for the 2026-04-05 installment, in the period that
          contains it.
        * A LATE payment for the 2026-05-05 installment, settled into the
          2026-05-08 period.  The pre-fix derivation
          (``monthly_due_date(2026-05-08, 5)``) dates it 2026-06-05 -- the FUTURE.
        * The ledger books it now (its period has begun), so the CURRENT period
          reads the post-payment balance...
        * ...while the next period (2026-05-22 .. 06-04) ENDS BEFORE 2026-06-05,
          so the pre-fix projection walked back to the April row and handed that
          period the loan's OLDER balance, which owed more.  The plotted
          liability rose.

        The true-up is dated 2026-03-10, after the 02-05 and 03-05 installments
        no payment here pays: every contractual installment is charged since
        plan step recurrence:R16-c-2, so the suite's 2026-01-10 true-up would
        leave both standing for the April payment to clear -- a real rise, the
        arrears -- and the case pins the mis-dating, not a delinquent loan (the
        fixture is restated so nothing is left unpaid, ruling R-R103).
        """
        with app.app_context():
            loan = _make_loan(
                seed_user, name="Monotonic Loan", payment_day=5,
                anchor_date=date(2026, 3, 10),
            )

            # Two extra periods so a FUTURE period exists that ends before the
            # mis-derived 2026-06-05 date -- the window the rise appears in.
            future_periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 5, 22), num_periods=2, rhythm=rhythm_of(14),
            )
            db.session.flush()
            periods = list(seed_periods) + list(future_periods)

            # On-time: the 2026-04-05 installment, in the period containing it.
            on_time_period = next(
                p for p in periods
                if p.start_date <= date(2026, 4, 5) <= last_covered_day(p)
            )
            _settle(seed_user, loan, on_time_period)

            # Late: the 2026-05-05 installment, settled into the 05-08 period.
            late_period = next(
                p for p in periods if p.start_date == date(2026, 5, 8)
            )
            late = _settle(seed_user, loan, late_period)
            # The installment it paid, on the TRANSFER, where a payment's
            # installment is read since plan step balance:X-bi-6-4b.  It was
            # written on the shadow alone until plan step balance:X-bi-6-4d-2
            # made a transfer one row (ruling R-BAL167 class 1), a copy no
            # reader asked after X-bi-6-4b.
            late.due_date = date(2026, 5, 5)
            db.session.flush()
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            # The pre-fix derivation dates the late payment in the FUTURE, and
            # the next period ends BEFORE it -- the window the rise appeared in.
            assert monthly_due_date(date(2026, 5, 8), 5) == date(2026, 6, 5)
            assert date(2026, 6, 5) > _AS_OF
            next_period = next(
                p for p in periods if p.start_date == date(2026, 5, 22)
            )
            assert last_covered_day(next_period) < date(2026, 6, 5)

            period_map = balance_at.build_maps(
                [loan], BalanceContext.build(seed_user["user"].id),
            )[loan.id]

            # The seam's map is HELD -- negative for a loan, ruling R-CC47 -- so
            # a paydown moves it UP toward zero.  The invariant is on what the
            # loan OWES, read through ``owed()``: that series must never rise.
            series = [owed(period_map[p.id]) for p in periods]
            rises = [
                (periods[i].start_date, series[i - 1], series[i])
                for i in range(1, len(series))
                if series[i] > series[i - 1]
            ]
            assert not rises, f"loan balance ROSE across periods: {rises}"


class TestTrueUpAfterLastPaymentIsRead:
    """A true-up dated after the last payment reaches every balance surface.

    A true-up is a BALANCE event, not a payment: the ledger books it, but it has
    no amortization-schedule row.  So a schedule walk -- which sees payment rows
    only -- reports the balance as of the last PAYMENT and silently misses any
    true-up after it.  On production data that read a real loan $3.94 above what
    it owed, splitting the year-end debt-progress scalar from the loan card.

    The seam's scalar now reads the genesis ledger for any date at or before
    today, so the past comes from the one producer that books every balance
    event.  This is the structural reason the past can never be re-derived from
    the schedule.
    """

    def test_scalar_reads_the_ledger_not_the_stale_last_payment_row(
        self, app, db, seed_user, seed_periods,
    ):
        """owed(balance_at) == ledger, though the last schedule row predates the true-up.

        The seam reports the balance HELD (negative for a loan, ruling R-CC47),
        so the scalar is compared through ``owed()``: what the loan owes, the
        sign the ledger and the reader state.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            scenario = seed_user["scenario"]
            loan = _make_loan(seed_user, name="Trued Up After Payment")
            _settle(seed_user, loan, seed_periods[2])
            db.session.commit()

            # A true-up asserted AFTER that payment's due date: the ledger books
            # it; the schedule has no row for it.
            outcome = loan_anchor_service.apply_loan_anchor_true_up(
                account=loan,
                anchor_balance=Decimal("96000.00"),
                anchor_date=date(2026, 2, 20),
            )
            assert outcome is AnchorTrueUpOutcome.COMMITTED
            db.session.commit()

            ledger = _ledger_balance(loan.id, scenario_id)
            bctx = BalanceContext(
                user_id=loan.user_id, scenario=scenario, as_of=_AS_OF,
            )
            scalar = balance_at.balance_at(loan, bctx, _AS_OF)
            reader = _posted_balance(loan.id, scenario_id)
            state = resolved_loan(loan, bctx).state

            # The true-up IS the balance; the reader and scalar both report it
            # (the scalar HELD, so it is read through ``owed()``).
            assert ledger == Decimal("96000.00")
            assert reader == ledger
            assert owed(scalar) == ledger

            # Non-vacuity: the schedule's last CONFIRMED row still carries the
            # pre-true-up balance, so a walk over it would have read HIGHER --
            # the exact stale read this test exists to forbid.
            confirmed_rows = [
                row for row in state.schedule if row.is_confirmed
            ]
            assert confirmed_rows
            assert confirmed_rows[-1].remaining_balance != ledger


class TestDueDateEditReconcilesTheLedger:
    """Editing a loan payment's due_date re-derives the posted ledger.

    ``due_date`` became a POSTING INPUT with the installment-basis fix: the
    genesis write walk orders payments by it and applies its strict
    ``anchor_date < due_date`` post-anchor boundary against it, so moving it
    changes which payments an anchor SUBSUMES and therefore the POSTED balance.

    It was NOT in ``transfer_service._POSTING_RELEVANT_FIELDS``, because before
    the fix the walk derived the due date from ``pay_period_id`` (which IS in the
    set).  Left out, a due-date edit would move every live READER (the history
    rows, the payment table, the resolver's replay) while the posted ledger kept
    the old numbers -- a silent ledger-vs-resolver divergence that would persist
    until some unrelated chokepoint happened to fire.
    """

    def test_editing_due_date_reposts_the_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """A due-date edit alone moves the ledger, and it stays self-consistent."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            # Anchor 2026-01-10; the payment's due date starts AFTER it, so the
            # payment is post-anchor and its principal is booked.
            loan = _make_loan(seed_user, name="Due Date Edit Loan")
            payment = _settle(seed_user, loan, seed_periods[2])
            db.session.commit()

            posted_before = _ledger_balance(loan.id, scenario_id)
            assert posted_before == Decimal("99500.00")   # 100,000 - 500 principal

            # Move the due date to BEFORE the anchor: the payment is now
            # subsumed by the anchor's reset, so its principal is no longer a
            # post-anchor paydown and the posted balance returns to the anchor.
            transfer_service.update_transfer(
                payment.id, seed_user["user"].id, due_date=date(2026, 1, 5),
            )
            db.session.commit()

            posted_after = _ledger_balance(loan.id, scenario_id)
            assert posted_after == _ANCHOR_BALANCE

            # And every reader agrees with the ledger -- the edit reconciled,
            # rather than leaving the posted numbers behind.  The seam reports
            # the balance HELD (negative for a loan, ruling R-CC47), so it is
            # compared through ``owed()``: what the loan owes.
            bctx = BalanceContext(
                user_id=loan.user_id, scenario=seed_user["scenario"],
                as_of=_AS_OF,
            )
            assert owed(balance_at.balance_at(loan, bctx, _AS_OF)) == posted_after
            assert _posted_balance(loan.id, scenario_id) == posted_after
