"""A payment that CLOSES the loan pays what the replay finds owed (recurrence:R16-f).

The contract's last installment is not its level payment.  Which figure
closes the loan depends on everything walked before it, so a closing
payment states no cash (:class:`~app.services.loan_ledger.ClosingCash`) and
:func:`~app.services.loan_ledger.replay_loan_events` pays the running
balance and every charge standing when it walks it: no excess, and the loan
closes at 0.00.  This is the rule the contractual projection's own walk
stated for itself (``amortization_engine._projection._apply_contractual_payment``'s
``is_last_month``), now in the one replay.

Every loan here is $3,000.00 at 5%, opened 2026-01-01, its installments due
on the 1st, so the level payment over three months is $1,008.34
(``amortization_engine.calculate_monthly_payment``).  A month's interest is
the balance x 0.05 / 12, rounded once to the cent
(:func:`~app.utils.money.installment_interest`).

Pure: no database, no app context.
"""

from datetime import date
from decimal import Decimal

from app.services.loan_ledger import (
    ClosingCash,
    LoanCashEvent,
    LoanEventStream,
    LoanResetEvent,
    replay_loan_events,
)
from tests.oracles.loan_monthly_composition import accrual_charge

_ZERO = Decimal("0.00")
_RATE = Decimal("0.05")
_OPENED = date(2026, 1, 1)
_FEB, _MAR, _APR = date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)
_LEVEL = "1008.34"
_CLOSES = ClosingCash.WHAT_CLOSES_THE_LOAN


def _payment(on_date: date, cash: str | ClosingCash) -> LoanCashEvent:
    """A cash event due and visible on *on_date*: a stated figure, or one that closes."""
    return LoanCashEvent(
        on_date=on_date,
        cash=cash if isinstance(cash, ClosingCash) else Decimal(cash),
        source=None, visible_on=on_date,
    )


def _reset(on_date: date, balance: str, *, is_opening: bool) -> LoanResetEvent:
    """An asserted balance: the loan's opening, or a true-up."""
    return LoanResetEvent(
        on_date=on_date, balance=Decimal(balance), source=None,
        is_opening=is_opening, is_tracking_start=False,
    )


def _stream(
    projections: list[LoanCashEvent],
    *,
    escrow: str = "0.00",
    payments: tuple[LoanCashEvent, ...] = (),
    resets: tuple[LoanResetEvent, ...] = (),
) -> LoanEventStream:
    """The $3,000.00 loan charged Feb 1, Mar 1 and Apr 1, with *projections* to walk."""
    return LoanEventStream(
        charges=[
            accrual_charge(on_date, _RATE, Decimal(escrow))
            for on_date in (_FEB, _MAR, _APR)
        ],
        payments=list(payments),
        resets=[_reset(_OPENED, "3000.00", is_opening=True), *resets],
        projections=projections,
    )


def _parts(outcome) -> tuple[Decimal, ...]:
    """A payment's cash, interest, escrow, principal, excess and balance after."""
    return (
        outcome.cash, outcome.interest, outcome.escrow,
        outcome.principal, outcome.excess, outcome.balance_after,
    )


class TestTheLastInstallmentClosesTheLoan:
    """The term's last installment pays what stands, where the level would leave a cent."""

    def test_three_months_close_on_the_third_for_1008_35(self):
        """$1,008.34 twice, then $1,008.35: the third month's $4.18 on $1,004.17.

        February: $12.50 of interest (3,000.00 x 0.05 / 12), $995.84 of
        principal, $2,004.16 left.  March: $8.35 ($8.3507), $999.99 of
        principal, $1,004.17 left.  April closes it: $1,004.17 and $4.18 of
        interest ($4.1840), $1,008.35.  A third $1,008.34 would pay $1,004.16
        of principal and leave $0.01 owing, the second replay below.
        """
        closing = replay_loan_events(_ZERO, _stream([
            _payment(_FEB, _LEVEL), _payment(_MAR, _LEVEL),
            _payment(_APR, _CLOSES),
        ])).payments
        level = replay_loan_events(_ZERO, _stream([
            _payment(_FEB, _LEVEL), _payment(_MAR, _LEVEL),
            _payment(_APR, _LEVEL),
        ])).payments

        assert [_parts(outcome) for outcome in closing] == [
            (Decimal("1008.34"), Decimal("12.50"), _ZERO,
             Decimal("995.84"), _ZERO, Decimal("2004.16")),
            (Decimal("1008.34"), Decimal("8.35"), _ZERO,
             Decimal("999.99"), _ZERO, Decimal("1004.17")),
            (Decimal("1008.35"), Decimal("4.18"), _ZERO,
             Decimal("1004.17"), _ZERO, _ZERO),
        ]
        assert [outcome.event.closes for outcome in closing] == [
            False, False, True,
        ]
        assert _parts(level[-1]) == (
            Decimal("1008.34"), Decimal("4.18"), _ZERO,
            Decimal("1004.16"), _ZERO, Decimal("0.01"),
        )

    def test_a_loan_behind_pays_its_arrears_and_escrow_at_the_close(self):
        """March unpaid: April's close pays both months' interest and escrow.

        Each month impounds $40.00 of escrow.  February pays $1,048.34
        ($1,008.34 of P&I and the $40.00), leaving $2,004.16.  Nothing pays
        March, so its $8.35 of interest (2,004.16 x 0.05 / 12 = $8.3507) and
        $40.00 still stand when April charges the same again on the same
        balance.  April's close is $2,004.16 + $16.70 + $80.00 = $2,100.86:
        the balloon a loan that is behind meets at maturity.
        """
        payments = replay_loan_events(_ZERO, _stream(
            [_payment(_FEB, "1048.34"), _payment(_APR, _CLOSES)],
            escrow="40.00",
        )).payments

        assert _parts(payments[-1]) == (
            Decimal("2100.86"), Decimal("16.70"), Decimal("80.00"),
            Decimal("2004.16"), _ZERO, _ZERO,
        )

    def test_the_what_if_extra_does_not_join_the_close(self):
        """An extra $100.00 a month pays the loan down; the close pays only what stands.

        February: $1,108.34 of cash, $12.50 of interest, $1,095.84 of
        principal, $1,904.16 left.  March: $7.93 of interest ($7.9340),
        $1,100.41 of principal, $803.75 left.  April closes it: $803.75 and
        $3.35 of interest ($3.3490), $807.10.  Had April's $100.00 joined the
        close, the cash would be $907.10 with $100.00 of excess the owner
        never pays.
        """
        payments = replay_loan_events(
            _ZERO,
            _stream([
                _payment(_FEB, _LEVEL), _payment(_MAR, _LEVEL),
                _payment(_APR, _CLOSES),
            ]),
            Decimal("100.00"),
        ).payments

        assert [outcome.balance_after for outcome in payments] == [
            Decimal("1904.16"), Decimal("803.75"), _ZERO,
        ]
        assert _parts(payments[-1]) == (
            Decimal("807.10"), Decimal("3.35"), _ZERO,
            Decimal("803.75"), _ZERO, _ZERO,
        )


class TestTheCloseFollowsTheWalk:
    """The figure that closes the loan is whatever the walk reached, not a schedule's."""

    def test_a_loan_already_paid_off_closes_for_nothing(self):
        """February pays the whole $3,012.50; March's close pays $0.00.

        February's $12.50 of interest and $3,000.00 of principal close the
        loan, so March charges nothing on a $0.00 balance and nothing stands:
        the closing payment moves no cash, where a stated $1,008.34 would be
        refunded whole.
        """
        payments = replay_loan_events(_ZERO, _stream([
            _payment(_FEB, "3012.50"), _payment(_MAR, _CLOSES),
        ])).payments

        assert _parts(payments[0])[-1] == _ZERO
        assert _parts(payments[-1]) == (_ZERO,) * 6

    def test_a_true_up_before_the_close_is_the_figure_it_closes(self):
        """A Feb 15 true-up to $1,200.00: March's close pays $1,205.00.

        February's recorded $1,008.34 leaves $2,004.16; the owner then
        states $1,200.00 on Feb 15, which overwrites it.  March's interest
        accrues on the stated figure, 1,200.00 x 0.05 / 12 = $5.00, so the
        close is $1,205.00, not the $2,012.51 that would close the replay's
        own $2,004.16.
        """
        payments = replay_loan_events(_ZERO, _stream(
            [_payment(_MAR, _CLOSES)],
            payments=(_payment(_FEB, _LEVEL),),
            resets=(_reset(date(2026, 2, 15), "1200.00", is_opening=False),),
        )).payments

        assert _parts(payments[-1]) == (
            Decimal("1205.00"), Decimal("5.00"), _ZERO,
            Decimal("1200.00"), _ZERO, _ZERO,
        )
