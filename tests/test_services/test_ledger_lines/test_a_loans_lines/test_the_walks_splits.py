"""``loan_ledger.compute_loan_payment_splits``: each payment's split, hand-computed, and the clock it never reads."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import AcctTypeEnum, StatusEnum
from app.extensions import db as _db
from app.models.escrow_line import EscrowComponentVersion
from app.services import loan_ledger, transfer_service
from tests._ledger_reads import account_net
from tests._test_helpers import (
    add_escrow_line,
    create_loan_account,
    freeze_today,
)

from ._helpers import (
    _ORIGINATION_BEFORE_P1,
    _ORIGINATION_DATE,
    _P1,
    _P2,
    _P3,
    _add_rate_change,
    _make_loan,
    _settle_payment,
)


class TestComputeLoanPaymentSplits:
    """The real split is computed from the actual cash, hand-computed literals."""

    def test_on_schedule_single_payment(self, app, db, seed_user, seed_periods):
        """One $1,000 payment: interest 500.00, principal 500.00, no escrow.

        Arithmetic: interest = round(100000 * 0.06 / 12) = 500.00; principal =
        cash - interest - escrow = 1000 - 500 - 0 = 500.00; excess = 0.  The
        500.00 interest also proves the walk seeds from the $100,000 trueup
        anchor (origination principal is $250,000, which would give $1,250).
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert len(splits) == 1
            split = splits[0]
            assert split.interest == Decimal("500.00")
            assert split.escrow == Decimal("0.00")
            assert split.principal == Decimal("500.00")
            assert split.excess == Decimal("0.00")

    def test_running_balance_across_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """Three $1,000 payments accrue interest on the shrinking real balance.

        Arithmetic (6%/12 = 0.5% monthly):
          P1: interest round(100000.00*0.005)=500.00, principal 500.00,
              balance 99500.00
          P2: interest round(99500.00*0.005)=497.50, principal 502.50,
              balance 98997.50
          P3: interest round(98997.50*0.005)=494.99, principal 505.01,
              balance 98492.49
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            for period in (seed_periods[_P1], seed_periods[_P2], seed_periods[_P3]):
                _settle_payment(seed_user, loan, period, Decimal("1000.00"))
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert [(s.interest, s.principal) for s in splits] == [
                (Decimal("500.00"), Decimal("500.00")),
                (Decimal("497.50"), Decimal("502.50")),
                (Decimal("494.99"), Decimal("505.01")),
            ]

    def test_extra_principal_lands_in_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """A $1,500 payment pays $1,000 principal -- the extra is captured.

        Arithmetic: interest 500.00; principal = 1500 - 500 - 0 = 1000.00.  The
        resolver's contractual replay would book only the scheduled principal
        and need a true-up; the ledger captures the extra automatically.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1500.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert splits[0].principal == Decimal("1000.00")
            assert splits[0].excess == Decimal("0.00")

    def test_short_payment_gives_negative_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """A $400 payment under-covers interest: principal -100.00, balance rises.

        Arithmetic: interest 500.00; principal = 400 - 500 - 0 = -100.00
        (negative amortization, surfaced not clamped); the next-period balance
        would be 100000 - (-100) = 100100.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("400.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert splits[0].interest == Decimal("500.00")
            assert splits[0].principal == Decimal("-100.00")
            assert splits[0].excess == Decimal("0.00")

    def test_configured_escrow_is_subtracted(
        self, app, db, seed_user, seed_periods,
    ):
        """A $1,200/yr escrow component subtracts $100.00/mo from principal.

        Arithmetic: monthly escrow = round(1200 / 12) = 100.00; interest 500.00;
        principal = 1000 - 500 - 100 = 400.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user, escrow_annual=Decimal("1200.00"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert splits[0].escrow == Decimal("100.00")
            assert splits[0].principal == Decimal("400.00")

    def test_escrow_change_is_effective_dated_not_retroactive(
        self, app, db, seed_user, seed_periods,
    ):
        """Each payment's escrow is the version in effect ON its date, and a
        LATER escrow change never re-splits an already-past payment.

        One escrow line, two effective-dated versions (supersession, no
        end_date): $1,200/yr ($100.00/mo) from origination, superseded by
        $2,400/yr ($200.00/mo) on 2026-03-01.  P1's installment (2026-02-01)
        resolves to the first version -> escrow 100.00; P2's (2026-03-01, the
        second version's own day) resolves to the second -> escrow 200.00.
        P2 and not a later payment since plan step recurrence:R16-c-2: every
        contractual installment is charged now, so a payment skipping March
        would clear March's escrow too (the fixture is restated so nothing is
        left unpaid, ruling R-R103).
        Then a THIRD version ($3,600/yr) effective 2026-06-01 supersedes the
        second FROM that date only, so it must leave BOTH earlier splits
        unchanged -- proving the split is immutable for a past date, the whole
        point of effective-dating escrow (the pre-fix code recomputed every
        payment at the current escrow, so the third change would have
        retroactively moved both to $300.00).
        """
        with app.app_context():
            loan = _make_loan(seed_user)  # no escrow via the helper
            # V1: $100/mo from origination.
            line_id = add_escrow_line(
                db.session, loan.id, "Escrow", Decimal("1200.00"),
                effective_date=_ORIGINATION_DATE,
            ).line_id
            # V2: $200/mo from 2026-03-01 (supersedes V1 from that date).
            db.session.add(EscrowComponentVersion(
                line_id=line_id, effective_date=date(2026, 3, 1),
                annual_amount=Decimal("2400.00"),
            ))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P2], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            # Chronological: P1 due 2026-02-01 (V1 $100); P2 due 2026-03-01
            # (V2 $200).  Distinct escrow proves the as-of keying.
            assert [s.escrow for s in splits] == [
                Decimal("100.00"), Decimal("200.00"),
            ]

            # A future escrow change: a THIRD version at 2026-06-01 supersedes
            # V2 from that date only.  Neither past payment is on/after it, so
            # both splits must hold (supersession never re-splits a past date).
            db.session.add(EscrowComponentVersion(
                line_id=line_id, effective_date=date(2026, 6, 1),
                annual_amount=Decimal("3600.00"),
            ))
            db.session.commit()

            resplits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert [s.escrow for s in resplits] == [
                Decimal("100.00"), Decimal("200.00"),
            ]

    def test_payoff_overpayment_routes_excess_to_refund(
        self, app, db, seed_user, seed_periods,
    ):
        """Overpaying a $300 balance caps principal and refunds the rest.

        Arithmetic (anchor balance 300.00 @ 6%): interest round(300*0.005)=1.50;
        principal0 = 1000 - 1.50 - 0 = 998.50 > 300 -> principal capped at
        300.00, excess = 998.50 - 300.00 = 698.50; balance closes at 0.
        """
        with app.app_context():
            loan = _make_loan(seed_user, anchor_balance=Decimal("300.00"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert splits[0].interest == Decimal("1.50")
            assert splits[0].principal == Decimal("300.00")
            assert splits[0].excess == Decimal("698.50")

    def test_payment_after_payoff_is_all_refund(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment on an already-closed loan is entirely a refund.

        Arithmetic: P1 (anchor 300 @ 6%) closes the loan (balance 0); P2's
        $500 cash accrues no interest and no escrow on the closed loan, so
        principal 0.00 and the whole 500.00 routes to refund.
        """
        with app.app_context():
            loan = _make_loan(seed_user, anchor_balance=Decimal("300.00"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P2], Decimal("500.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert len(splits) == 2
            assert splits[1].interest == Decimal("0.00")
            assert splits[1].escrow == Decimal("0.00")
            assert splits[1].principal == Decimal("0.00")
            assert splits[1].excess == Decimal("500.00")

    def test_arm_rate_step_changes_interest(
        self, app, db, seed_user, seed_periods,
    ):
        """A mid-history rate step to 12% changes the later payment's interest.

        Arithmetic: P1 (due 2026-02-01, governed by the 6% origination rate)
        interest 500.00, principal 500.00, balance 99500.00.  A rate change
        effective 2026-03-01 to 12% governs P2 (due 2026-03-01, the change's
        own day): interest = round(99500 * 0.12 / 12) = 995.00, principal =
        1000 - 995 = 5.00.  P2 and not a later payment since plan step
        recurrence:R16-c-2: every contractual installment is charged now, so
        a payment skipping March would clear March's interest too (the
        fixture is restated so nothing is left unpaid, ruling R-R103).
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _add_rate_change(loan, date(2026, 3, 1), Decimal("0.12000"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P2], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert [(s.interest, s.principal) for s in splits] == [
                (Decimal("500.00"), Decimal("500.00")),
                (Decimal("995.00"), Decimal("5.00")),
            ]

    def test_actual_amount_drives_the_split(
        self, app, db, seed_user, seed_periods,
    ):
        """The split uses effective (actual) cash -- the adversarial non-vacuity proof.

        Settling with a $1,300 ACTUAL over a $1,000 estimate must move principal
        to 1300 - 500 = 800.00 (not the estimate's 500.00); a split that read
        ``transfers.amount`` instead of the shadow's effective amount would
        fail here.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                actual=Decimal("1300.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert splits[0].principal == Decimal("800.00")

    def test_pre_trueup_payment_is_split_from_origination(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment due before the trueup is split from the ORIGINATION reset.

        Genesis walks EVERY confirmed payment from origination and applies each
        anchor as a running-balance reset, so a payment due BEFORE the trueup is
        NOT excluded (the Step-4 behavior) -- it is split on the origination
        balance the walk carries until the trueup resets it.  With the trueup
        moved to 2026-03-15, period 1's payment (pay-period start 2026-01-16, due
        2026-02-01) precedes it and accrues on the $250,000 origination principal:
        interest = round(250000 * 0.06 / 12) = 1250.00;
        principal = 1000 - 1250 - 0 = -250.00 (negative amortization, surfaced).
        The 1250.00 (vs the trueup's 500.00) is what proves the origination reset.

        The trueup is dated 2026-03-15 rather than 2026-04-15 (its original value)
        because this suite freezes today at 2026-03-20, and a future-dated true-up
        is a shape production forbids outright (the trueup schema rejects a future
        ``anchor_date``).  Any date
        strictly after the payment's 2026-02-01 due date and on/before the frozen
        today reproduces the case, so every asserted number is unchanged.
        """
        with app.app_context():
            loan = _make_loan(
                seed_user, anchor_date=date(2026, 3, 15),
                origination_date=_ORIGINATION_BEFORE_P1,
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert len(splits) == 1
            assert splits[0].interest == Decimal("1250.00")
            assert splits[0].principal == Decimal("-250.00")

    def test_mid_life_loan_resets_at_trueup(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment before the trueup splits from origination; one after, from the trueup.

        The genesis reset proven with a payment on each side of a trueup dated
        2026-02-15 (origination $250,000 @ 6%, trueup $100,000):
          P1 (period 1, due 2026-02-01, PRE-trueup): interest
            round(250000 * 0.005) = 1250.00, principal 1000 - 1250 = -250.00.
          Reset to 100000 at the 2026-02-15 trueup.
          P2 (period 3, due 2026-03-01, POST-trueup): interest
            round(100000 * 0.005) = 500.00, principal 1000 - 500 = 500.00.
        A from-origination walk with NO reset would accrue P2 on ~250000, not
        100000 -- so the distinct 500.00 is the reset's signature.
        """
        with app.app_context():
            loan = _make_loan(
                seed_user, anchor_date=date(2026, 2, 15),
                origination_date=_ORIGINATION_BEFORE_P1,
            )
            _settle_payment(seed_user, loan, seed_periods[_P1], Decimal("1000.00"))
            _settle_payment(seed_user, loan, seed_periods[_P2], Decimal("1000.00"))
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert [(s.interest, s.principal) for s in splits] == [
                (Decimal("1250.00"), Decimal("-250.00")),
                (Decimal("500.00"), Decimal("500.00")),
            ]

    def test_projected_payment_is_excluded(
        self, app, db, seed_user, seed_periods,
    ):
        """An unsettled (Projected) payment is a future commitment, not history.

        Un-settled through the transfer's own revert door.  *Re-expressed under
        rule 5 (developer approval, 2026-09-24): it bulk-updated the two SHADOW
        rows back to Projected under a still-Paid transfer, which the loan no
        longer reads since plan step balance:X-bi-6-4b -- the transfer decides.*
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            # Settle one, then un-settle it back to Projected directly.
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert splits == []

    def test_no_loan_params_returns_empty(self, app, db, seed_user):
        """An account with no LoanParams is not yet resolvable -- no splits."""
        with app.app_context():
            checking = seed_user["account"]  # a plain Checking, no LoanParams
            splits = loan_ledger.compute_loan_payment_splits(
                checking.id, seed_user["scenario"].id,
            )
            assert splits == []


# ---------------------------------------------------------------------------
# the walk reads no clock -- A3
# ---------------------------------------------------------------------------


class TestWalkReadsNoClock:
    """The walk is a function of the loan's DATA, not the wall clock.

    The walk used to drop any anchor dated after the sync's as-of, which made what
    the stored ledger PERSISTED depend on the moment the sync happened to run.
    That was not a cache; it was a corruption generator, and it had two live
    consequences a loan configured before its closing date could reach through
    the ordinary UI.

    Both tests here use a $200,000 / 5% / 360mo mortgage originating 2026-04-15 --
    AFTER the suite's frozen today (2026-03-20), which ``origination_date``
    permits (unlike a true-up's ``anchor_date``, it carries no not-future
    validator, and the developer ruled the app must support it).

    Arithmetic (200,000 @ 5.000%, payment_day=1):
      monthly P&I = 1,073.64
      first installment 2026-05-01:
        interest  = round(200000 * 0.05/12) = 833.33
        principal = 1073.64 - 833.33        =   240.31
    """

    ORIGINATION = date(2026, 4, 15)
    PRINCIPAL = Decimal("200000.00")

    def _upcoming_mortgage(self, seed_user):
        """Create the mortgage that has not closed yet."""
        return create_loan_account(
            seed_user, _db.session, name="Closing In April",
            principal=self.PRINCIPAL, rate=Decimal("0.05000"),
            term=360, origination_date=self.ORIGINATION, payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )

    def test_early_settled_payment_splits_against_the_real_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment settled before origination still splits on the opening balance.

        The payment is settled TODAY (2026-03-20) into the pay period starting
        2026-04-24, so its installment is DUE 2026-05-01 -- after the 2026-04-15
        origination.  Settling early is a supported state, not an abuse: the walk
        deliberately splits every settled payment whatever its pay period
        ("posting early changes when the fact is RECORDED, never when it is
        SHOWN").

        Before A3 the sync's as-of was 2026-03-20, so the origination anchor was
        dropped, the running balance seeded at $0.00, and ``split_one_payment``'s
        ``balance <= 0`` arm routed the ENTIRE $1,073.64 to ``excess`` -- real
        mortgage cash reclassified as a Refund Receivable asset, with the whole
        Schedule-A deductible interest erased.  Measured on the real Mortgage, the
        same line cost $7,643.80.

        NEGATIVE CONTROL: restore the ``anchor.anchor_date <= as_of`` filter --
        it would now go in ``loan_ledger.loan_event_stream``, where the reset
        events are built (it was ``merge_anchor_and_payment_events`` until plan
        step X-au-g-2c-3b-2) -- and this reports ``excess=1073.64`` with
        interest, escrow and principal all $0.00.
        """
        with app.app_context():
            loan = self._upcoming_mortgage(seed_user)
            due_period = next(
                p for p in seed_periods if p.start_date >= date(2026, 4, 24)
            )
            _settle_payment(
                seed_user, loan, due_period, Decimal("1073.64"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert len(splits) == 1
            split = splits[0]
            # The installment this payment satisfies is due after origination, so
            # the loan exists by then and the cash divides normally.
            assert split.interest == Decimal("833.33")
            assert split.principal == Decimal("240.31")
            assert split.escrow == Decimal("0.00")
            assert split.excess == Decimal("0.00")

    def test_the_read_is_identical_at_two_different_clocks(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Reading before vs after origination gives the SAME ledger.

        The property A3 exists to establish, stated directly: the walk's output is
        a function of the loan's facts alone.  The loan is configured and read at
        2026-03-20 (before it closes); the clock then moves past origination to
        2026-05-07 and the SAME read re-runs.  It was
        ``test_the_posted_ledger_is_identical_at_two_different_clocks``, an
        unchanged stored entry count beside the figure, until plan step
        plan#2914 deleted the store.

        Both halves are in the figure, because the clock moved both: the
        OPENING line (-$200,000.00 on the loan's own line) and the payment's
        split.

        NEGATIVE CONTROL (measured on the store, before plan#2913): restore the
        ``anchor.anchor_date <= as_of``
        filter and the first sync posts no opening AND routes the whole payment to
        Refund, so the linked ledger nets to exactly ``0.00`` -- the loan reads as
        owing NOTHING while $1,073.64 of the borrower's cash sits in a Refund
        Receivable.  The assert below then fails ``0.00 != -199759.69``.
        """
        with app.app_context():
            loan = self._upcoming_mortgage(seed_user)
            due_period = next(
                p for p in seed_periods if p.start_date >= date(2026, 4, 24)
            )
            _settle_payment(
                seed_user, loan, due_period, Decimal("1073.64"),
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id

            # Read as of 2026-03-20 -- before origination.
            linked_after_first = account_net(_db.session, loan.id, scenario_id)
            # -200,000.00 opening + 1,073.64 cash - 833.33 interest
            # - 0.00 escrow = -199,759.69 owed, read 26 days before the loan
            # even closes.
            assert linked_after_first == Decimal("-199759.69")

            # The clock crosses origination; the same read re-runs.
            freeze_today(monkeypatch, date(2026, 5, 7))

            assert account_net(_db.session, loan.id, scenario_id) == linked_after_first

