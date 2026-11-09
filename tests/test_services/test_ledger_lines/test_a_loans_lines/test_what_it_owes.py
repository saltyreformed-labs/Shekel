"""What a loan's own lines say it owes: the sum, bounded by day and by scenario."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.enums import LedgerAccountKindEnum
from app.models.scenario import Scenario
from tests._ledger_reads import posted_loan_balance_at, posted_loan_balance_map
from tests._test_helpers import (
    create_loan_account,
    freeze_today,
    last_covered_day,
)

from ._helpers import (
    _ANCHOR_DATE,
    _AS_OF,
    _ORIGINATION_DATE,
    _ORIGINATION_PRINCIPAL,
    _P1,
    _P2,
    _P3,
    _RATE,
    _ledger_net,
    _make_loan,
    _settle_payment,
)

# The frozen "today" for the class below: after the Jan-May 2026 seed periods and
# the 2026-12-31 _AS_OF (its fixture's docstring says what the wall clock still
# decided before every settle stated its day).
_FROZEN_TODAY = date(2027, 1, 1)


class TestPostedLoanBalanceSums:
    """A loan's own lines sum to its balance: -(the sum of the lines on its own key).

    Every line the read books on the loan's own key -- the opening, each
    payment's cash and split leg, each true-up -- summed and negated, bounded
    by ``entry_date <= as_of`` and by scenario.  The literals are the same
    hand-computed figures the split tests prove (100000 - 500 - 502.50 - 505.01
    = 98492.49 over three $1,000 payments), read back off the ledger.

    **These pin the LEDGER, not a reader.**  Plan step E1e deleted the two
    production sum-of-postings readers -- nothing in ``app/`` called them once the
    balance seam folded a loan from source events -- so the sum is read through
    the test suite's own window (:func:`tests._ledger_reads.posted_loan_balance_at`).
    Every expected value here is unchanged by that move, and by plan step
    plan#2914's deleting the stored ledger: it was always the ledger's number.
    """

    @pytest.fixture(autouse=True)
    def _frozen_today(self, monkeypatch):
        """Freeze today after the seed window so every fixture is deterministic.

        2027-01-01 sits after the Jan-May 2026 seed periods and the 2026-12-31
        ``_AS_OF``, so the genesis walk is eligible for every loan built here.
        Class-scoped, so only these tests freeze here -- the package's other
        files keep ``tests/test_services/conftest.py``'s 2026-03-20.

        **The "no assertion depends on the wall clock" this docstring used to
        claim was FALSE, and finding N-65's structural fix is what proved it.**
        Six tests here settled their payment with no explicit date, so the day
        came from the DATABASE clock -- untouched by ``freeze_today``, which
        patches Python only.  The real clock happens to fall before ``_AS_OF``,
        so the payment landed inside the window and the assertions held.  Under a
        frozen database clock the settle lands on 2027-01-01, ONE DAY past
        ``_AS_OF``, and the payment is correctly excluded: the balance reads
        ``100000.00`` instead of ``99500.00``.  Which means these six would have
        gone red by themselves on 2027-01-01 with no code change at all.

        Every settle in this class therefore passes ``settled_on=`` explicitly,
        stating the date the test always meant.  No expected figure moved --
        the settle day bounds a posting's VISIBILITY (``entry_date <= as_of``) and
        never its split, which the pay period decides.
        """
        freeze_today(monkeypatch, _FROZEN_TODAY)

    def test_opening_only_loan_reads_the_original_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """An origination-only loan reads back its opening balance.

        A $250,000 loan with only its origination anchor (no true-up, no
        payments): the opening books linked -250000, so the reader negates it to
        the full $250,000 owed -- the ledger, not ``LoanParams``, is the source.
        With no payments the balance is flat, so every period's map value is the
        same $250,000.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, principal=_ORIGINATION_PRINCIPAL,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
            )
            db.session.commit()

            assert posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) == Decimal("250000.00")
            # No payments -> the opening balance carries flat across every period.
            result = posted_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            )
            assert set(result.values()) == {Decimal("250000.00")}

    def test_reads_the_balance_after_a_single_confirmed_payment(
        self, app, db, seed_user, seed_periods,
    ):
        """One $1,000 payment drops the balance by its real principal.

        Trued up to $100,000, one post-anchor $1,000 payment splits to interest
        round(100000 * 0.005) = 500.00 and principal 500.00, so the reader reports
        100000 - 500 = 99500.00 (opening -250000 + true-up +150000 + Step-2 cash
        +1000 + split -500, negated).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=seed_periods[_P1].start_date,
            )
            db.session.commit()

            assert posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) == Decimal("99500.00")

    def test_extra_payment_is_captured_without_a_trueup(
        self, app, db, seed_user, seed_periods,
    ):
        """An off-schedule extra payment lowers the read balance -- no true-up.

        This is the arc's headline.  A $1,500 payment on the $100,000 balance
        splits to interest round(100000 * 0.005) = 500.00 and principal
        1500 - 500 = 1000.00, so the reader reports 100000 - 1000 = 99000.00.  The
        resolver's contractual replay would book only the scheduled ~$500
        principal and need a balance true-up to reconcile the extra $500; the
        ledger captures the real principal from the actual cash, so the reader
        already reflects it with no anchor edit.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1500.00"),
                settled_on=seed_periods[_P1].start_date,
            )
            db.session.commit()

            assert posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) == Decimal("99000.00")

    def test_running_balance_reproduces_the_resolver_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """Three $1,000 payments read back the resolver's balance, 98492.49.

        The running-balance walk (interest 500.00 / 497.50 / 494.99 on the
        shrinking real balance; principal 500.00 / 502.50 / 505.01) leaves
        100000 - 1507.51 = 98492.49 -- the resolver's confirmed balance
        (``test_opening_payments_trueup_reproduce_resolver_balance`` proves the
        equality against ``account_net``); the reader negates the same
        linked net to the same figure.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle_payment(
                    seed_user, loan, period, Decimal("1000.00"),
                    settled_on=period.start_date,
                )
            db.session.commit()

            assert posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) == Decimal("98492.49")

    def test_paid_off_loan_reads_zero_not_none(
        self, app, db, seed_user, seed_periods,
    ):
        """A fully-paid-off loan reads 0.00, distinct from an unconfigured None.

        One $100,500 payment on the $100,000 verified balance splits to interest
        round(100000 * 0.005) = 500.00 and principal 100000.00 (cash - interest),
        exactly closing the loan, so the linked net is 0 and the reader reports
        Decimal("0.00") -- a real paid-off balance, NOT the ``None`` an
        unconfigured loan returns.  The zero is a clean +0.00 (the reader negates
        via ``0 - net``), never ``-0.00``.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("100500.00"),
                settled_on=seed_periods[_P1].start_date,
            )
            db.session.commit()

            result = posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            )
            # == 0.00 (not None): None == Decimal fails, so this proves both.
            assert result == Decimal("0.00")
            # A clean positive zero, never -0.00 (the 0 - net negation).
            assert not result.is_signed()

    def test_payoff_overpayment_reads_zero_with_excess_on_the_refund_ledger(
        self, app, db, seed_user, seed_periods,
    ):
        """An overpayment closes the loan at 0.00; the excess is not on the linked ledger.

        A $150,000 payment on the $100,000 balance: interest 500.00, principal
        capped at the 100000.00 that closes the loan, and the 49500.00 surplus
        routed to the per-loan Refund (Asset) ledger, NOT the linked ledger.  The
        reader sums only the linked ledger, so it reports 0.00 (paid off, never a
        negative balance), while the refund ledger separately holds the 49500.00
        the lender owes back -- proving the excess cannot corrupt the balance the
        reader returns.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("150000.00"),
                settled_on=seed_periods[_P1].start_date,
            )
            db.session.commit()

            assert posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) == Decimal("0.00")
            # The surplus lives on the Refund ledger, invisible to the reader.
            assert _ledger_net(
                LedgerAccountKindEnum.LOAN_REFUND, loan, scenario_id,
            ) == Decimal("49500.00")

    def test_as_of_bounds_the_sum_by_settled_date(
        self, app, db, seed_user, seed_periods,
    ):
        """A historical as_of counts only the payments SETTLED by then (step C2).

        Over the SAME fully-posted ledger (three $1,000 payments in periods 1/3/5,
        each settled ON its period start here), the reader is a point-in-time sum
        bounded by each posting's ``entry_date`` -- a payment's SETTLED date:

          * before P1's settle (period-0 end): opening + true-up only -> 100000.00
          * P1's settle .. period end:          + P1 -> 99500.00 (settled on the
                                                period start, so stable through it)
          * P2's settle:                        + P2 -> 98997.50
          * P3's settle .. _AS_OF:              + P3 -> 98492.49

        No re-walk and no period boundary rule -- just which payments have settled.
        The split still keys on the DUE date (02-01 / 03-01 / 04-01), so the
        principal amounts are unchanged; only WHEN each is visible follows settle.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle_payment(
                    seed_user, loan, period, Decimal("1000.00"),
                    settled_on=period.start_date,
                )
            db.session.commit()

            def read(as_of):
                return posted_loan_balance_at(
                    loan.id, scenario_id, as_of,
                )

            # P1 settled on period 1's start; period 0 ends the day before.
            assert read(last_covered_day(seed_periods[0])) == Decimal("100000.00")
            assert read(seed_periods[_P1].start_date) == Decimal("99500.00")
            # Through P1's period its settle (the period start) is past -> 99500.
            assert read(last_covered_day(seed_periods[_P1])) == Decimal("99500.00")
            assert read(seed_periods[_P2].start_date) == Decimal("98997.50")
            assert read(seed_periods[_P3].start_date) == Decimal("98492.49")
            assert read(_AS_OF) == Decimal("98492.49")

    def test_a_future_as_of_carries_the_confirmed_sum_flat(
        self, app, db, seed_user, seed_periods,
    ):
        """Past today the sum is unchanged -- there is nothing later to add.

        Every line is dated today or earlier, so ``entry_date <= as_of``
        selects the identical set for any future date and the window carries the
        confirmed balance FLAT.  It is NOT a projection and must never be read as
        one -- a forward balance comes from
        :func:`app.services.balance_at.balance_at`, which folds the plan.

        The deleted production reader RAISED here instead (a domain guard that
        forced its callers to route a future date to the projection).  That guard
        existed for callers; the window has none but this suite, and the flat
        answer is what lets the per-period form below check a period boundary
        past today.  Non-vacuity: the in-domain read at the true-up's own date is
        asserted too (C2 -- an anchor counts from its own civil date, so an
        earlier date would still read the origination principal).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            db.session.commit()

            assert posted_loan_balance_at(
                loan.id, scenario_id, _ANCHOR_DATE,
            ) == Decimal("100000.00")
            # After the frozen 2027-01-01 today: the same sum, carried flat.
            assert posted_loan_balance_at(
                loan.id, scenario_id, date(2027, 1, 2),
            ) == Decimal("100000.00")

    def test_window_is_scenario_scoped(
        self, app, db, seed_user, seed_periods,
    ):
        """The window isolates scenarios; a scenario with no payment reads its anchor.

        A $1,000 payment settled in the baseline is the baseline's alone: the
        baseline reads 99500.00, and a what-if with no payment reads the
        loan's anchor, 100000.00 -- neither a misleading $0 nor the baseline's
        balance.  It was ``test_window_is_scenario_scoped_and_none_off_scenario``,
        the store holding no opening in the what-if, until plan step plan#2914
        deleted the store.
        """
        with app.app_context():
            baseline = seed_user["scenario"]
            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if", is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()

            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=seed_periods[_P1].start_date,
            )
            db.session.commit()

            assert posted_loan_balance_at(
                loan.id, baseline.id, _AS_OF,
            ) == Decimal("99500.00")
            assert posted_loan_balance_at(
                loan.id, whatif.id, _AS_OF,
            ) == Decimal("100000.00")

    def test_per_period_map_runs_the_balance_and_carries_flat(
        self, app, db, seed_user, seed_periods,
    ):
        """The map gives each period the cumulative balance, flat between payments.

        Three $1,000 payments land in periods 1 / 3 / 5 (the opening + true-up in
        period 0), each settled ON its period start.  The map is period-END keyed
        (step C2), so each period holds the balance AFTER every payment settled by
        its end, carried flat across payment-less periods:

          period 0: 100000.00 (opening + true-up, pre-payment)
          period 1: 99500.00  (+ P1)      period 2: 99500.00  (flat)
          period 3: 98997.50  (+ P2)      period 4: 98997.50  (flat)
          period 5: 98492.49  (+ P3)      periods 6-9: 98492.49 (flat)

        The map keys by ``period.id`` in the passed order.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle_payment(
                    seed_user, loan, period, Decimal("1000.00"),
                    settled_on=period.start_date,
                )
            db.session.commit()

            result = posted_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            )
            expected = {
                seed_periods[0].id: Decimal("100000.00"),
                seed_periods[1].id: Decimal("99500.00"),
                seed_periods[2].id: Decimal("99500.00"),
                seed_periods[3].id: Decimal("98997.50"),
                seed_periods[4].id: Decimal("98997.50"),
                seed_periods[5].id: Decimal("98492.49"),
                seed_periods[6].id: Decimal("98492.49"),
                seed_periods[7].id: Decimal("98492.49"),
                seed_periods[8].id: Decimal("98492.49"),
                seed_periods[9].id: Decimal("98492.49"),
            }
            assert dict(result) == expected
            # Keyed in the passed period order.
            assert list(result.keys()) == [p.id for p in seed_periods]


class TestPostedLoanBalanceFuturePeriods:
    """The posted sum carries future periods flat, in both forms.

    A separate class because it freezes today MID seed-window (so some seed
    periods are genuinely after today), where
    :class:`TestPostedLoanBalanceSums` freezes after the window.
    """

    @pytest.fixture(autouse=True)
    def _frozen_mid_window(self, monkeypatch):
        """Freeze today inside the seed window so periods 4-9 are in the future.

        2026-02-20 sits in period 3 of the Jan-May seed window, leaving periods
        4-9 starting after today -- the future tail the sum must answer, carried
        flat because no line is dated later than today.
        """
        freeze_today(monkeypatch, date(2026, 2, 20))

    def test_map_carries_flat_for_future_periods(
        self, app, db, seed_user, seed_periods,
    ):
        """Post-today periods carry the confirmed sum flat, scalar and map alike.

        A payment-less trued-up loan (opening -250000 + true-up +150000 =
        -100000) books its opening + true-up before the frozen 2026-02-20.  Every
        period -- including periods 4-9, which start after today -- carries the
        flat 100000.00 confirmed balance, because no line is dated later
        than today and so none is added by a later boundary.  The scalar agrees at
        a future period's start, which is what makes the two forms one derivation
        rather than two.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)  # opening + true-up, no payments
            db.session.commit()

            # period 3 contains 2026-02-20; period 4 starts 2026-02-27 (future).
            assert seed_periods[3].start_date <= date(2026, 2, 20)
            assert seed_periods[4].start_date > date(2026, 2, 20)

            result = posted_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            )
            # Every period -- historical AND future -- carries the flat balance.
            assert set(result.values()) == {Decimal("100000.00")}
            assert len(result) == len(seed_periods)

            # Historical AND future points agree with the map.
            assert posted_loan_balance_at(
                loan.id, scenario_id, seed_periods[3].start_date,
            ) == Decimal("100000.00")
            assert posted_loan_balance_at(
                loan.id, scenario_id, seed_periods[4].start_date,
            ) == Decimal("100000.00")


class TestPrePeriodAnchor:
    """An anchor older than every pay period the user has.

    Direct coverage for the ``entry_date`` reader bound (step C2's one clock).
    The shape that once needed a special rule: a journal entry carries its true
    civil ``entry_date`` AND a NOT NULL ``pay_period_id``, and when an anchor
    predates every pay period the user has, the filing rule
    (``pay_calendar.PayCalendar.filing_period``) is FORCED to clamp it into the
    earliest period.  A reader bounding by the pay
    period believed that and reported a loan originated before the user's
    pay-period history as owing NOTHING for the whole span in between (the
    year-end summary turned that $0 into a NEGATIVE principal-paid figure).
    Bounding by ``entry_date`` -- the anchor's own civil date -- reads it
    correctly with no special case, which is what these pin.
    """

    def test_anchor_older_than_every_pay_period_is_still_owed(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan originated BEFORE the user's first pay period still owes it all.

        ``seed_periods`` begins 2026-01-02.  This loan is originated 2025-06-01, so
        its opening is forced into the earliest period (there is no period covering
        2025) -- and before this fix the reader therefore reported the loan as owing
        $0.00 for every date in 2025, despite the debt plainly existing.

        The opening asserts $250,000 and no payment is recorded, so the honest
        balance on 2025-12-31 is the full $250,000 -- and so is EVERY seed
        period's, since they all end in 2026 (after the origination) and no
        payment ever lands.  Both are pinned to that hand-computed literal, at a
        date inside the pre-period gap and at every period boundary.

        **Both halves assert the literal, deliberately.**  The obvious
        alternative -- assert the per-period value equals the scalar at that
        period's end -- is now ``f(x) == f(x)``: the per-period window is
        DEFINED as the scalar applied at each period end (plan step E1e), where
        the deleted production map was an independent prefix-sum whose boundary
        handling such a comparison did pin.  A cross-derivation check that
        cannot fail is worse than none, so the value carries the teeth here.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, name="Old Loan",
                principal=Decimal("250000.00"), rate=_RATE, term=360,
                origination_date=date(2025, 6, 1),
            )

            # A date inside the gap: after origination, before any pay period.
            owed = posted_loan_balance_at(
                loan.id, scenario_id, date(2025, 12, 31),
            )
            assert owed == Decimal("250000.00"), (
                f"a loan originated 2025-06-01 read as owing {owed} on "
                f"2025-12-31; the ledger is dating its opening by the pay period "
                f"it was FILED under, not the date it asserts"
            )

            # Every period boundary reads the same $250,000: the opening counts
            # from its own 2025-06-01 civil date (step C2), which precedes every
            # seed period's end, and nothing pays it down.
            bmap = posted_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            )
            assert bmap is not None
            assert len(bmap) == len(seed_periods)
            assert set(bmap.values()) == {Decimal("250000.00")}, (
                f"a pre-period opening read unevenly across period boundaries: "
                f"{sorted(str(v) for v in set(bmap.values()))}"
            )
