"""prepare_payments_for_engine: the escrow taken out of each payment by its due date, the biweekly
payments redistributed across months and the year boundary, and what passes through unchanged.
"""

from datetime import date
from decimal import Decimal

from app.services.amortization_engine import PaymentDates, PaymentRecord
from app.services.loan_payment_service import prepare_payments_for_engine
from app.services.installment_calendar import monthly_due_date


# ── Tests for prepare_payments_for_engine ────────────────────────


def _standing_escrow_lines(monthly_amount):
    """Return one transient escrow line resolving to ``monthly_amount``/mo, any date.

    A single opening version effective long before any test payment, so
    ``escrow_monthly_as_of`` returns exactly ``monthly_amount`` on every date --
    the supersession-model stand-in for the old scalar ``monthly_escrow`` these
    subtraction tests passed.  Kept in-memory (never persisted) because
    ``prepare_payments_for_engine`` is a pure function that only reads each
    line's ``versions``.
    """
    from app.models.escrow_line import EscrowComponentVersion, EscrowLine
    line = EscrowLine(name="Escrow")
    line.versions = [
        EscrowComponentVersion(
            effective_date=date(2000, 1, 1),
            annual_amount=monthly_amount * Decimal("12"),
            is_removed=False,
        ),
    ]
    return [line]


class TestPreparePaymentsForEngine:
    """Tests for escrow subtraction and biweekly redistribution."""

    def test_escrow_subtraction(self):
        """C1-3: Payments above P&I are reduced by escrow amount.

        Payment of $1,800, contractual P&I $1,517, escrow $283.
        The $283 above P&I is escrow -> subtract it -> $1,517.
        """
        payments = [
            PaymentRecord(PaymentDates(date(2026, 1, 1), monthly_due_date(date(2026, 1, 1), 1), date(2026, 1, 1)), Decimal("1800.00")),
            PaymentRecord(PaymentDates(date(2026, 2, 1), monthly_due_date(date(2026, 2, 1), 1), date(2026, 2, 1)), Decimal("1800.00")),
            PaymentRecord(PaymentDates(date(2026, 3, 1), monthly_due_date(date(2026, 3, 1), 1), date(2026, 3, 1)), Decimal("1800.00")),
        ]
        result = prepare_payments_for_engine(
            payments,
            payment_day=1,
            escrow_lines=_standing_escrow_lines(Decimal("283.00")),
            contractual_pi=Decimal("1517.00"),
        )

        assert len(result) == 3
        for p in result:
            assert p.amount == Decimal("1517.00")

    def test_escrow_subtraction_keys_on_the_due_date_not_the_pay_period(self):
        """Each payment subtracts the escrow IN EFFECT FOR ITS INSTALLMENT.

        One escrow line, two versions: $283/mo (annual $3,396) from 2020-01-01,
        then $333/mo (annual $3,996) effective **2026-05-25**.  The second
        payment is booked in the pay period starting 2026-05-21 and satisfies
        the 2026-06-01 installment, so the new version lands STRICTLY inside
        that window -- the shape finding N-34 is about, and the reason this
        test discriminates:

          * DUE-date keying (ruling D5, as built): $333 backed out, so
            1850 - min(333, 1850 - 1517) = **1,517.00** (the P&I).
          * PAY-PERIOD-START keying (the N-34 defect): $283 backed out, leaving
            **1,567.00** -- $50 of escrow mis-recovered as P&I, which the
            resolver's forward override then amortizes as extra principal.

        The first payment (period start and installment both 2026-01-01, before
        either version boundary) resolves the old $283 either way, so it holds
        the non-window case still.
        """
        from app.models.escrow_line import EscrowComponentVersion, EscrowLine
        line = EscrowLine(name="Tax & Insurance")
        line.versions = [
            EscrowComponentVersion(
                effective_date=date(2020, 1, 1),
                annual_amount=Decimal("3396.00"), is_removed=False,
            ),
            EscrowComponentVersion(
                effective_date=date(2026, 5, 25),
                annual_amount=Decimal("3996.00"), is_removed=False,
            ),
        ]
        # The second record is a REAL biweekly shape: its pay period starts
        # 2026-05-21, its installment falls 2026-06-01, and the version sits
        # between them.  (Every other record in this class has
        # period_start == due_date, where the two keyings cannot disagree.)
        payments = [
            PaymentRecord(PaymentDates(date(2026, 1, 1), monthly_due_date(date(2026, 1, 1), 1), date(2026, 1, 1)), Decimal("1800.00")),
            PaymentRecord(PaymentDates(date(2026, 5, 21), date(2026, 6, 1), date(2026, 5, 21)), Decimal("1850.00")),
        ]
        assert payments[1].dates.period_start < date(2026, 5, 25) < payments[1].dates.due_date

        result = prepare_payments_for_engine(
            payments,
            payment_day=1,
            escrow_lines=[line],
            contractual_pi=Decimal("1517.00"),
        )

        assert len(result) == 2
        # Jan payment: 1800 - min(283, 1800-1517) = 1800 - 283 = 1517 (old).
        assert result[0].amount == Decimal("1517.00")
        # Jun installment: 1850 - min(333, 1850-1517) = 1850 - 333 = 1517 (NEW).
        assert result[1].amount == Decimal("1517.00")

    def test_below_pi_not_adjusted(self):
        """C1-4: Payments at or below P&I are not reduced.

        Payment of $1,500 is below contractual P&I of $1,517 --
        this payment did not include escrow, so no subtraction.
        """
        payments = [
            PaymentRecord(PaymentDates(date(2026, 1, 1), monthly_due_date(date(2026, 1, 1), 1), date(2026, 1, 1)), Decimal("1500.00")),
        ]
        result = prepare_payments_for_engine(
            payments,
            payment_day=1,
            escrow_lines=_standing_escrow_lines(Decimal("283.00")),
            contractual_pi=Decimal("1517.00"),
        )

        assert len(result) == 1
        assert result[0].amount == Decimal("1500.00")

    def test_biweekly_redistribution(self):
        """C1-5: Two payments due the same month are spread to consecutive months.

        With payment_day=1, pay periods starting 2026-01-02 and
        2026-01-16 both fall before 2026-02-01, so both have a true
        monthly DUE date of 2026-02-01 (the schedule keys rows by due
        date).  The first keeps its slot (due Feb 1); the second is
        redistributed to the next free due month, 2026-03-01.

        Only the DUE date is redistributed.  ``period_start`` -- the pay period
        funding the payment -- and ``settled_on`` -- the day its cash moved --
        are FACTS and are carried through untouched on BOTH records: the first
        is the replay's rate key, the second its "has this happened?" cap, so
        overwriting either with the invented due date would feed a fabricated
        date to a consumer expecting a fact.
        """
        payments = [
            PaymentRecord(PaymentDates(date(2026, 1, 2), monthly_due_date(date(2026, 1, 2), 1), date(2026, 1, 2)), Decimal("1517.00")),
            PaymentRecord(PaymentDates(date(2026, 1, 16), monthly_due_date(date(2026, 1, 16), 1), date(2026, 1, 16)), Decimal("1517.00")),
        ]
        result = prepare_payments_for_engine(
            payments,
            payment_day=1,
            escrow_lines=[],
            contractual_pi=Decimal("1517.00"),
        )

        assert len(result) == 2
        # First keeps its slot: due Feb 1, booked in the Jan 2 pay period.
        assert result[0].dates.period_start == date(2026, 1, 2)
        assert result[0].dates.due_date == date(2026, 2, 1)
        # Second is redistributed to the next free due month (Mar 1), but stays
        # booked in the pay period it was actually paid in (Jan 16).
        assert result[1].dates.period_start == date(2026, 1, 16)
        assert result[1].dates.due_date == date(2026, 3, 1)

    def test_empty_payments_passthrough(self):
        """Empty payment list returns unchanged."""
        result = prepare_payments_for_engine(
            [],
            payment_day=1,
            escrow_lines=_standing_escrow_lines(Decimal("283.00")),
            contractual_pi=Decimal("1517.00"),
        )
        assert result == []

    def test_no_escrow_no_subtraction(self):
        """Zero escrow means no subtraction regardless of amount."""
        payments = [
            PaymentRecord(PaymentDates(date(2026, 1, 1), monthly_due_date(date(2026, 1, 1), 1), date(2026, 1, 1)), Decimal("2000.00")),
        ]
        result = prepare_payments_for_engine(
            payments,
            payment_day=1,
            escrow_lines=[],
            contractual_pi=Decimal("1517.00"),
        )

        assert result[0].amount == Decimal("2000.00")

    def test_preserves_is_confirmed(self):
        """is_confirmed flag is preserved through preparation."""
        payments = [
            PaymentRecord(PaymentDates(date(2026, 1, 1), monthly_due_date(date(2026, 1, 1), 1), date(2026, 1, 1)), Decimal("1800.00")),
            PaymentRecord(PaymentDates(date(2026, 2, 1), monthly_due_date(date(2026, 2, 1), 1), None), Decimal("1800.00")),
        ]
        result = prepare_payments_for_engine(
            payments,
            payment_day=1,
            escrow_lines=_standing_escrow_lines(Decimal("283.00")),
            contractual_pi=Decimal("1517.00"),
        )

        assert result[0].dates.is_confirmed is True
        assert result[1].dates.is_confirmed is False

    def test_december_to_january_rollover(self):
        """Two payments both due Jan 1 2027 (year rollover): second to Feb 2027.

        With payment_day=1, pay periods starting 2026-12-05 and
        2026-12-19 both fall before 2027-01-01, so both have a true
        monthly due date of 2027-01-01 (the due date crosses the year
        boundary).  The second is redistributed to the next free due
        month, 2027-02-01 -- its DUE date only; both keep the pay period they
        were actually paid in.
        """
        payments = [
            PaymentRecord(PaymentDates(date(2026, 12, 5), monthly_due_date(date(2026, 12, 5), 1), date(2026, 12, 5)), Decimal("1517.00")),
            PaymentRecord(PaymentDates(date(2026, 12, 19), monthly_due_date(date(2026, 12, 19), 1), date(2026, 12, 19)), Decimal("1517.00")),
        ]
        result = prepare_payments_for_engine(
            payments,
            payment_day=1,
            escrow_lines=[],
            contractual_pi=Decimal("1517.00"),
        )

        assert len(result) == 2
        assert result[0].dates.period_start == date(2026, 12, 5)
        assert result[0].dates.due_date == date(2027, 1, 1)
        assert result[1].dates.period_start == date(2026, 12, 19)
        assert result[1].dates.due_date == date(2027, 2, 1)
