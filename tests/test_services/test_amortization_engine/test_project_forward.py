"""project_forward: projection as a pure forward-only function of a known starting state."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.enums import InterestAccrualConventionEnum
from app.services.amortization_engine import (
    PeriodTerms,
    ProjectionInputs,
    calculate_monthly_payment,
    project_forward,
)

from ._helpers import _terms


class TestProjectForward:
    """Tests for ``project_forward``.

    The second half of the amortization-engine split (Commit 2 of
    ``docs/plans/2026-05-21-amortization-engine-split-implementation.md``;
    architectural plan
    ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``).
    Verifies that projection is a pure forward-only function of a
    known starting state:

      - ``extra_monthly`` lives only on this surface, and applies to
        EVERY forward month (C2-4 -- the target-date search's contract-only
        what-if);
      - a ``monthly_override`` channel routed the user's planned payments
        through this projection until plan step R7d-g-3 (ruling **R-R88**):
        the balance seam's plan fold is the one walk of what a loan is
        projected to pay, and the channel went with its last caller;
      - negative amortization, overpayment cap, and ARM rate-change
        re-amortization all mirror ``generate_schedule``'s existing
        behavior on the projection side;
      - every projected row carries ``is_confirmed=False``.

    Standard scenario: $300,000 / 6% / 360 months / payment_day=1
    with a known starting date of 2026-02-01.  Monthly P&I:
        M = P * [r(1+r)^n] / [(1+r)^n - 1]
        r = 0.06/12 = 0.005, n = 360
        (1.005)^360 ~ 6.022575
        M = 300000 * 0.005 * 6.022575 / 5.022575 = $1,798.65
    """

    PRINCIPAL = Decimal("300000.00")
    RATE = Decimal("0.06")
    TERM_MONTHS = 360
    PAYMENT_DAY = 1
    CONTRACTUAL_PAYMENT = Decimal("1798.65")
    STARTING_DATE = date(2026, 2, 1)

    # ── C2-1: no override, no extra -> contractual schedule ───────

    def test_no_override_no_extra_is_contractual(self):
        """C2-1: with no override and no extra, ``project_forward``
        produces the standard contractual schedule over
        ``remaining_months``.

        Hand arithmetic at $300,000 / 6% / 360, payment_day=1, first
        payment 2026-02-01:
          row 1: interest = 300000.00 * 0.005 = $1,500.00;
                 principal = 1798.65 - 1500.00 = $298.65;
                 balance = $299,701.35
          row 360: schedule terminates with balance == $0.00 by the
                   ``is_final`` absorbing branch (final row rolls up
                   any sub-penny residue).
        ``extra_payment`` is $0.00 on every row.
        """
        rows = project_forward(
            ProjectionInputs(
                starting_balance=self.PRINCIPAL,
                starting_date=self.STARTING_DATE,
                remaining_months=self.TERM_MONTHS,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=_terms(
                    self.STARTING_DATE, self.RATE, self.CONTRACTUAL_PAYMENT,
                ),
            ),
        )
        assert len(rows) == self.TERM_MONTHS
        # Row 1.
        assert rows[0].payment_date == date(2026, 2, 1)
        assert rows[0].interest == Decimal("1500.00")
        assert rows[0].principal == Decimal("298.65")
        assert rows[0].extra_payment == Decimal("0.00")
        assert rows[0].remaining_balance == Decimal("299701.35")
        assert rows[0].is_confirmed is False
        # Every row uses the contractual payment (sans final-row
        # absorption rounding), no extra.
        for row in rows[:-1]:
            assert row.payment == self.CONTRACTUAL_PAYMENT
            assert row.extra_payment == Decimal("0.00")
            assert row.is_confirmed is False
        # Final row absorbs the residue and closes the balance at 0.
        assert rows[-1].payment_date == date(2056, 1, 1)
        assert rows[-1].remaining_balance == Decimal("0.00")
        assert rows[-1].extra_payment == Decimal("0.00")

    # ── C2-3: extra_monthly only, no override ─────────────────────

    def test_extra_monthly_only_no_override(self):
        """C2-3: ``extra_monthly`` applies to every non-final month;
        the schedule shortens to ~17.7 years and the final row
        absorbs the residue with ``extra_payment == 0``.

        Hand arithmetic at row 1:
          interest = 300000.00 * 0.005 = $1,500.00
          principal = 1798.65 - 1500.00 = $298.65
          extra = $500.00 (uncapped: 500.00 < 299701.35)
          balance = 300000.00 - 298.65 - 500.00 = $299,201.35
        At $300,000 / 6% with $500 extra, the loan amortizes in 212
        rows; the final row's ``is_final`` branch absorbs the balance
        and reports ``extra_payment == $0.00``.
        """
        rows = project_forward(
            ProjectionInputs(
                starting_balance=self.PRINCIPAL,
                starting_date=self.STARTING_DATE,
                remaining_months=self.TERM_MONTHS,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=_terms(
                    self.STARTING_DATE, self.RATE, self.CONTRACTUAL_PAYMENT,
                ),
            ),
            extra_monthly=Decimal("500.00"),
        )
        # Row 1 hand-computed values.
        assert rows[0].payment == self.CONTRACTUAL_PAYMENT
        assert rows[0].extra_payment == Decimal("500.00")
        assert rows[0].principal == Decimal("298.65")
        assert rows[0].interest == Decimal("1500.00")
        assert rows[0].remaining_balance == Decimal("299201.35")
        # Schedule shortens substantially (under the unaccelerated
        # 360 months).
        assert len(rows) == 212
        # Final row absorbs residue.
        assert rows[-1].remaining_balance == Decimal("0.00")
        # Every non-final row has the configured extra applied.
        for row in rows[:-1]:
            assert row.extra_payment == Decimal("500.00")

    # ── C2-4: override + extra -- the standing extra rides override months ──

    # ── C2-5: override below interest -> negative amortization ────

    # ── C2-6: ARM rate change during projection ───────────────────

    def test_arm_rate_change_during_projection(self):
        """C2-6: a second ``terms_schedule`` entry recasts the rate AND
        payment from its ``start_date`` -- to the entry's SSOT values.

        Retargeted for the DH-#1 terms-schedule reshape (developer
        ratified "full SSOT", 2026-06-11): the engine no longer
        re-amortizes from its own balance at a rate boundary; it pays
        the governing entry's ``monthly_pi`` verbatim (the rate-period
        engine's derived-or-recorded figure, the same one the loan
        card shows).

        Setup: 30 yr / $300,000 / starts at 6%; recast at 2027-02-01
        to 7.5% with the schedule-derived level payment.  Hand
        arithmetic at month 13 (Feb 2027):
          balance entering Feb 2027 (post-Jan 2027) = $296,316.00
          new monthly_rate = 0.075 / 12 = 0.00625
          interest = 296316.00 * 0.00625 = 1851.97500 -> $1,851.98
          level payment M(296316.00, 0.075, 348) = $2,091.16
        (Identical to the pre-reshape re-amortized value here, because
        a no-override/no-extra projection's balance at the boundary IS
        the scheduled balance -- the reshape changes behavior only when
        a what-if balance path diverges from the schedule.)
        """
        contractual = calculate_monthly_payment(
            self.PRINCIPAL, self.RATE, self.TERM_MONTHS,
        )
        recast_pi = calculate_monthly_payment(
            Decimal("296316.00"), Decimal("0.075"), 348,
        )
        # Hand-anchored: the derived recast level payment.
        assert recast_pi == Decimal("2091.16")
        rows = project_forward(
            ProjectionInputs(
                starting_balance=self.PRINCIPAL,
                starting_date=self.STARTING_DATE,
                remaining_months=self.TERM_MONTHS,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=[
                    PeriodTerms(
                        start_date=self.STARTING_DATE,
                        annual_rate=self.RATE,
                        monthly_pi=contractual,
                        convention=InterestAccrualConventionEnum.MONTHLY_1_12,
                    ),
                    PeriodTerms(
                        start_date=date(2027, 2, 1),
                        annual_rate=Decimal("0.075"),
                        monthly_pi=recast_pi,
                        convention=InterestAccrualConventionEnum.MONTHLY_1_12,
                    ),
                ],
            ),
        )
        # Row 12 (month 13, Feb 2027) is the first row at the new
        # rate.  Pre-change row 11 (Jan 2027, month 12) keeps the
        # base 6% rate.
        jan_2027 = rows[11]
        feb_2027 = rows[12]
        assert jan_2027.payment_date == date(2027, 1, 1)
        assert jan_2027.interest_rate == Decimal("0.06")
        assert jan_2027.payment == self.CONTRACTUAL_PAYMENT
        assert feb_2027.payment_date == date(2027, 2, 1)
        assert feb_2027.interest_rate == Decimal("0.075")
        # interest at the new rate: 296316.00 * 0.075/12.
        assert feb_2027.interest == Decimal("1851.98")
        # The payment IS the second entry's SSOT level payment.
        assert feb_2027.payment == recast_pi

    def test_recorded_recast_pi_paid_verbatim(self):
        """DH-#1 engine pin: a recorded recast P&I is paid VERBATIM.

        The user's lender stated the post-recast payment on the
        adjustment notice ($2,500.00, recorded as
        ``RateHistory.monthly_pi`` and carried into the terms entry).
        Every post-recast row must pay exactly that figure -- the loan
        card shows it, so a schedule that re-derives a different
        payment contradicts the card (the original DH-#1 divergence:
        the pre-reshape engine re-amortized $2,582.13 here).
        Revert-proof: restoring balance-reactive re-amortization fails
        the equality.
        """
        contractual = calculate_monthly_payment(
            self.PRINCIPAL, self.RATE, self.TERM_MONTHS,
        )
        recorded = Decimal("2500.00")
        rows = project_forward(
            ProjectionInputs(
                starting_balance=self.PRINCIPAL,
                starting_date=self.STARTING_DATE,
                remaining_months=self.TERM_MONTHS,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=[
                    PeriodTerms(
                        start_date=self.STARTING_DATE,
                        annual_rate=self.RATE,
                        monthly_pi=contractual,
                        convention=InterestAccrualConventionEnum.MONTHLY_1_12,
                    ),
                    PeriodTerms(
                        start_date=date(2027, 2, 1),
                        annual_rate=Decimal("0.075"),
                        monthly_pi=recorded,
                        convention=InterestAccrualConventionEnum.MONTHLY_1_12,
                    ),
                ],
            ),
        )
        post_recast = [
            r for r in rows if r.payment_date >= date(2027, 2, 1)
        ]
        assert post_recast, "projection must reach the recast"
        # Every post-recast row except the residue-absorbing final one
        # pays the recorded figure exactly.
        for row in post_recast[:-1]:
            assert row.payment == recorded, (
                f"{row.payment_date}: paid {row.payment}, recorded "
                f"recast is {recorded}"
            )
            assert row.interest_rate == Decimal("0.075")

    # ── C2-7: overpayment cap on the final row ────────────────────

    def test_overpayment_cap_final_row(self):
        """C2-7: when extra (or an override) would drive the balance
        below zero, the final row absorbs the remaining balance
        exactly, ``extra_payment`` is capped, and the closing balance
        is $0.00.

        Hand arithmetic at $1,000 balance, 6%, contractual $1,798.65,
        extra $500 over a 360-month projection:
          interest = 1000.00 * 0.005 = $5.00
          principal (contractual - interest) = 1798.65 - 5.00 = $1,793.65
          ``is_final`` triggers because principal >= balance:
            principal := 1000.00
            actual_payment = 1000.00 + 5.00 = $1,005.00
            extra := $0.00 (final-row absorption clears extra)
            balance := $0.00
        The schedule terminates in a single row.
        """
        rows = project_forward(
            ProjectionInputs(
                starting_balance=Decimal("1000.00"),
                starting_date=self.STARTING_DATE,
                remaining_months=self.TERM_MONTHS,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=_terms(
                    self.STARTING_DATE, self.RATE, self.CONTRACTUAL_PAYMENT,
                ),
            ),
            extra_monthly=Decimal("500.00"),
        )
        assert len(rows) == 1
        final = rows[0]
        assert final.payment == Decimal("1005.00")
        assert final.principal == Decimal("1000.00")
        assert final.interest == Decimal("5.00")
        assert final.extra_payment == Decimal("0.00")
        assert final.remaining_balance == Decimal("0.00")

    # ── C2-8: zero starting balance returns empty ─────────────────

    def test_zero_starting_balance_returns_empty(self):
        """C2-8: ``starting_balance == 0`` returns an empty list
        (the loan is already paid off; nothing to project).
        """
        rows = project_forward(
            ProjectionInputs(
                starting_balance=Decimal("0.00"),
                starting_date=self.STARTING_DATE,
                remaining_months=self.TERM_MONTHS,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=_terms(
                    self.STARTING_DATE, self.RATE, self.CONTRACTUAL_PAYMENT,
                ),
            ),
        )
        assert rows == []
        # Negative balance is treated the same way (defensive).
        rows = project_forward(
            ProjectionInputs(
                starting_balance=Decimal("-10.00"),
                starting_date=self.STARTING_DATE,
                remaining_months=self.TERM_MONTHS,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=_terms(
                    self.STARTING_DATE, self.RATE, self.CONTRACTUAL_PAYMENT,
                ),
            ),
        )
        assert rows == []

    # ── C2-9: zero remaining_months returns empty ─────────────────

    def test_zero_remaining_months_returns_empty(self):
        """C2-9: ``remaining_months == 0`` returns an empty list
        (no time horizon for projection).
        """
        rows = project_forward(
            ProjectionInputs(
                starting_balance=self.PRINCIPAL,
                starting_date=self.STARTING_DATE,
                remaining_months=0,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=_terms(
                    self.STARTING_DATE, self.RATE, self.CONTRACTUAL_PAYMENT,
                ),
            ),
        )
        assert rows == []
        # Negative remaining_months is treated the same way.
        rows = project_forward(
            ProjectionInputs(
                starting_balance=self.PRINCIPAL,
                starting_date=self.STARTING_DATE,
                remaining_months=-3,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=_terms(
                    self.STARTING_DATE, self.RATE, self.CONTRACTUAL_PAYMENT,
                ),
            ),
        )
        assert rows == []

    # ── C2-10: hand-computed payoff lock ──────────────────────────

    def test_hand_computed_payoff_with_extra(self):
        """C2-10: $279,985 starting balance / 6% / 336 remaining
        months / $200 extra produces the hand-computed payoff baked
        into the architectural plan.

        Hand-arithmetic anchor for the contractual payment used by
        the projection:
          r = 0.005, n = 336
          (1.005)^336 ~ 5.34324
          M = 279985 * 0.005 * 5.34324 / (5.34324 - 1)
            = 7479.4 / 4.34324
            ~ $1,722.25
        Independently-computed payoff: 262 rows; final pay_date
        2048-02-29 (payment_day=30 clamped to 29 in February); final
        ``remaining_balance == $0.00``.

        Pre-Commit-9 this test also cross-checked against
        ``generate_schedule`` for row-by-row identity; Commit 9
        deletes ``generate_schedule`` and the cross-check.  The
        hand-anchored payoff is the lasting regression lock.
        """
        starting = Decimal("279985.00")
        remaining = 336
        extra = Decimal("200.00")
        payment_day = 30
        contractual = calculate_monthly_payment(
            starting, self.RATE, remaining,
        )
        # Hand-anchored contractual value.
        assert contractual == Decimal("1722.25")
        rows = project_forward(
            ProjectionInputs(
                starting_balance=starting,
                starting_date=date(2026, 5, 30),
                remaining_months=remaining,
                payment_day=payment_day,
                terms_schedule=_terms(
                    date(2026, 5, 30), self.RATE, contractual,
                ),
            ),
            extra_monthly=extra,
        )
        # Hand-anchored independently-computed payoff.
        assert len(rows) == 262
        assert rows[-1].payment_date == date(2048, 2, 29)
        assert rows[-1].remaining_balance == Decimal("0.00")

    # ── C2-11: round_money is the only rounding boundary ──────────

    def test_round_money_is_only_rounding_boundary(self):
        """C2-11: ``project_forward``'s body contains no bare
        ``.quantize(Decimal("0.01"))`` without an explicit
        ``rounding=`` keyword (coding standards: rounding goes
        through ``round_money`` or an explicit ``ROUND_HALF_UP``).

        Source-level guard against silent ``ROUND_HALF_EVEN``
        regressions: a bare ``.quantize(Decimal("0.01"))`` would use
        Python's default banker's rounding and silently drift away
        from the project's hand-computed test assertions.  The
        ``round_money`` helper rejects ``float`` and pins
        ``ROUND_HALF_UP``; this test pins the helper as the only
        rounding surface inside the new primitive.
        """
        engine_source = Path(
            "app/services/amortization_engine/_projection.py"
        ).read_text(encoding="utf-8")
        # Slice out the project_forward body for the assertion.  The
        # body ends at the next top-level ``def`` or ``class`` after
        # ``project_forward``, or at end-of-file -- since the package
        # split, ``project_forward`` is the last definition in
        # ``_projection.py`` (the payoff layer lives in ``_payoff.py``).
        # Taking the min of both markers keeps the slice correct
        # regardless of what construct comes next.
        marker = "def project_forward("
        start = engine_source.index(marker)
        next_def = engine_source.find("\ndef ", start + len(marker))
        next_class = engine_source.find("\nclass ", start + len(marker))
        candidates = [c for c in (next_def, next_class) if c != -1]
        end = min(candidates) if candidates else len(engine_source)
        body = engine_source[start:end]
        # No bare cents quantize without explicit rounding= keyword.
        assert '.quantize(Decimal("0.01"))' not in body
        assert '.quantize(TWO_PLACES)' not in body
        # round_money IS used (positive assertion that the helper
        # is the rounding boundary).
        assert "round_money(" in body
