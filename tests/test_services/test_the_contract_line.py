"""A loan's CONTRACT line is the one replay with the contract's cash (recurrence:R16-f).

What the loan is charged and pays if every installment the contract still
bills is paid as billed: the loan's recorded facts, then one projection per
installment after the record's last event
(:func:`~app.services.loan_ledger.contract_projections`), each at its level
P&I and escrow, the term's last closing the loan
(:class:`~app.services.loan_ledger.ClosingCash`), composed and replayed by
``balance_at._loan_stream.contract_line``.  An installment due before the read
that nothing paid is billed the day after the read and splits as its
installment would (ruling plan#4029, amending plan#3983's figures).

Every interest figure is the balance x rate / 12, rounded once to the cent
(:func:`~app.utils.money.installment_interest`, the monthly convention).
"""

from datetime import date
from decimal import Decimal

from app.services import balance_at
from app.services.balance_at import BalanceContext
# Pylint: ``shekel-private-module-import`` -- the seam publishes the contract
# line's projections alone (``balance_at.loan_contract_installments``), so the
# tests of the line itself, its fact prefix and its from-origination reading
# reach the seam module that composes it, as the timeline's do
# (``test_loan_plan_assembly``).
# pylint: disable-next=shekel-private-module-import
from app.services.balance_at._loan_stream import (
    contract_line,
    origination_contract_line,
)
from app.services.loan_ledger import (
    ClosingCash,
    LoanCalendar,
    LoanCashEvent,
    LoanEventStream,
    LoanResetEvent,
    contract_projections,
    replay_loan_stream,
    with_contract_charges,
)
from tests._test_helpers import (
    add_escrow_line,
    create_loan_account,
    create_loan_with_trueup,
)
from tests.oracles.loan_monthly_composition import rate_period

_ZERO = Decimal("0.00")
_CLOSES = ClosingCash.WHAT_CLOSES_THE_LOAN


def _facts(
    calendar: LoanCalendar,
    *,
    payments: tuple[tuple[date, str], ...] = (),
    true_ups: tuple[tuple[date, str], ...] = (),
) -> LoanEventStream:
    """A hand-stated recorded stream: the opening, *payments* and *true_ups*, charged."""
    return with_contract_charges(
        LoanEventStream(
            charges=(),
            payments=[
                LoanCashEvent(
                    on_date=on_date, cash=Decimal(cash), source=None,
                    visible_on=on_date,
                )
                for on_date, cash in payments
            ],
            resets=[
                LoanResetEvent(
                    on_date=calendar.origination_date,
                    balance=calendar.original_principal, source=None,
                    is_opening=True, is_tracking_start=False,
                ),
                *(
                    LoanResetEvent(
                        on_date=on_date, balance=Decimal(balance), source=None,
                        is_opening=False, is_tracking_start=False,
                    )
                    for on_date, balance in true_ups
                ),
            ],
        ),
        calendar,
    )


def _calendar(
    principal: str, rate: str, term: int, origination: date, level: str,
) -> LoanCalendar:
    """A loan's terms due on its origination's day, at one rate, escrowing nothing."""
    return LoanCalendar(
        origination_date=origination,
        payment_day=origination.day,
        term_months=term,
        original_principal=Decimal(principal),
        periods=[rate_period(Decimal(rate), period_pi=Decimal(level))],
        escrow_lines=(),
    )


#: Ruling plan#4029's example: $3,000.00 at 5% over three months from Jan 15,
#: whose level payment is $1,008.34 (amortization_engine's formula).
_THREE_MONTHS = _calendar("3000.00", "0.05", 3, date(2026, 1, 15), "1008.34")


def _split(outcome) -> tuple:
    """A payment's due date, cash, interest, principal and balance after."""
    return (
        outcome.due_date, outcome.cash, outcome.interest,
        outcome.principal, outcome.balance_after,
    )


class TestTheContractBillsWhatTheRecordHasNotReached:
    """One projection per installment after the record's last event, the last closing."""

    def test_missed_installments_count_the_day_after_the_read(self):
        """Nothing paid, read Mar 20: Feb 15 and Mar 15 count Mar 21 and split on time.

        Feb 15: $12.50 of interest (3,000.00 x 0.05 / 12), $995.84 of
        principal, $2,004.16 left.  Mar 15: $8.35 ($8.3507), $999.99, leaving
        $1,004.17.  Apr 15 closes it: $1,004.17 and $4.18 ($4.1840),
        $1,008.35.  Interest $25.03 in all, ruling plan#4029's option A,
        where walking the two on Mar 21 (option B) would charge $29.20.
        """
        facts = _facts(_THREE_MONTHS)
        as_of = date(2026, 3, 20)

        projections = contract_projections(facts, _THREE_MONTHS, as_of)
        walk = contract_line(facts, as_of)

        assert [
            (event.on_date, event.cash, event.visible_on)
            for event in projections
        ] == [
            (date(2026, 2, 15), Decimal("1008.34"), date(2026, 3, 21)),
            (date(2026, 3, 15), Decimal("1008.34"), date(2026, 3, 21)),
            (date(2026, 4, 15), _CLOSES, date(2026, 4, 15)),
        ]
        assert walk.settled_splits == []
        assert [_split(outcome) for outcome in walk.projected_splits] == [
            (date(2026, 2, 15), Decimal("1008.34"), Decimal("12.50"),
             Decimal("995.84"), Decimal("2004.16")),
            (date(2026, 3, 15), Decimal("1008.34"), Decimal("8.35"),
             Decimal("999.99"), Decimal("1004.17")),
            (date(2026, 4, 15), Decimal("1008.35"), Decimal("4.18"),
             Decimal("1004.17"), _ZERO),
        ]
        assert sum(
            (outcome.interest for outcome in walk.projected_splits), _ZERO,
        ) == Decimal("25.03")

    def test_a_loan_500_dollars_short_closes_on_its_balloon(self):
        """$12,000.00 at 6% over 12 months, Feb 1 paid $532.80: Jan 1, 2027 closes for $1,560.98.

        The level payment is $1,032.80; the first was $500.00 short of it.
        The contract bills Mar 1 through Dec 1 at $1,032.80, and the last
        installment pays what closes the loan: ruling plan#3982's figure, the
        $500.00 shortfall grown at 0.5% a month for eleven months on top of
        the on-schedule last payment.  The recorded payment splits exactly as
        the facts alone split it.
        """
        calendar = _calendar(
            "12000.00", "0.06", 12, date(2026, 1, 1), "1032.80",
        )
        facts = _facts(calendar, payments=((date(2026, 2, 1), "532.80"),))

        walk = contract_line(facts, date(2026, 2, 10))
        projected = walk.projected_splits

        assert walk.settled_splits == (
            replay_loan_stream(facts).settled_splits
        )
        assert [outcome.due_date for outcome in projected] == [
            date(2026, month, 1) for month in range(3, 13)
        ] + [date(2027, 1, 1)]
        assert {outcome.cash for outcome in projected[:-1]} == {
            Decimal("1032.80"),
        }
        assert (projected[-1].cash, projected[-1].balance_after) == (
            Decimal("1560.98"), _ZERO,
        )

    def test_a_record_past_the_last_installment_leaves_nothing_to_bill(self):
        """A true-up on Apr 20, after the Apr 15 last installment: no projection.

        The record's last event is past the term, so the first installment
        after it (May 15) is past the contract's last, and the line is the
        facts alone: the $1,500.00 the owner stated stands with no payoff.
        """
        facts = _facts(
            _THREE_MONTHS, true_ups=((date(2026, 4, 20), "1500.00"),),
        )

        assert contract_projections(
            facts, _THREE_MONTHS, date(2026, 4, 25),
        ) == []
        assert contract_line(facts, date(2026, 4, 25)).projected_splits == []


class TestTheContractFromOrigination:
    """The opening alone, read on the origination day: every installment on its own date."""

    def test_it_bills_every_installment_as_scheduled_whatever_the_record(self):
        """A short payment and a true-up on record change nothing from origination.

        The record holds Feb 15's $500.00 and a Mar 1 statement of
        $2,600.00; the from-origination contract reads neither.  It pays
        $1,008.34 on Feb 15 and Mar 15, each counting on its own date, and
        Apr 15 closes for $1,008.35, the three-month schedule of the case
        above.
        """
        facts = _facts(
            _THREE_MONTHS,
            payments=((date(2026, 2, 15), "500.00"),),
            true_ups=((date(2026, 3, 1), "2600.00"),),
        )

        walk = origination_contract_line(facts)

        assert walk.settled_splits == []
        assert [
            (*_split(outcome), outcome.visible_on)
            for outcome in walk.projected_splits
        ] == [
            (date(2026, 2, 15), Decimal("1008.34"), Decimal("12.50"),
             Decimal("995.84"), Decimal("2004.16"), date(2026, 2, 15)),
            (date(2026, 3, 15), Decimal("1008.34"), Decimal("8.35"),
             Decimal("999.99"), Decimal("1004.17"), date(2026, 3, 15)),
            (date(2026, 4, 15), Decimal("1008.35"), Decimal("4.18"),
             Decimal("1004.17"), _ZERO, date(2026, 4, 15)),
        ]


class TestAStoredLoansContractLine:
    """The pass's own facts and calendar: each installment bills its escrow."""

    def test_each_installment_bills_its_pi_and_escrow_and_the_last_closes(
        self, app, db, seed_user,
    ):
        """$12,000.00 at 6% over 12 months, $1,200.00 a year of escrow, read Jan 10, 2026.

        Each installment is $1,032.80 of P&I and $100.00 of escrow,
        $1,132.80.  After Dec 1 the balance is $1,027.64, so Jan 1, 2027
        closes it for $1,027.64 + $5.14 of interest (1,027.64 x 0.06 / 12 =
        $5.1382) + $100.00 of escrow = $1,132.78.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=Decimal("12000.00"),
                rate=Decimal("0.06000"), term=12,
                origination_date=date(2026, 1, 1), payment_day=1,
            )
            add_escrow_line(db.session, loan.id, "Tax", Decimal("1200.00"))
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id, date(2026, 1, 10))

            walk = contract_line(ctx.loan_walk(loan).stream, ctx.as_of)
            projected = walk.projected_splits

            assert len(projected) == 12
            assert {
                (outcome.cash, outcome.escrow) for outcome in projected[:-1]
            } == {(Decimal("1132.80"), Decimal("100.00"))}
            assert (
                projected[-1].due_date, projected[-1].cash,
                projected[-1].interest, projected[-1].escrow,
                projected[-1].principal, projected[-1].balance_after,
            ) == (
                date(2027, 1, 1), Decimal("1132.78"), Decimal("5.14"),
                Decimal("100.00"), Decimal("1027.64"), _ZERO,
            )


class TestTheLoanPagesReading:
    """``balance_at.loan_contract_installments``: what the loan page reads of the line."""

    def test_it_is_the_contract_lines_projections(self, app, db, seed_user):
        """$12,000.00 at 6% over 12 months, read Jan 10, 2026: twelve installments.

        The seam entry hands the loan page the contract line's projected
        outcomes as they stand: Feb 1, 2026 through Jan 1, 2027, the last
        closing the loan.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=Decimal("12000.00"),
                rate=Decimal("0.06000"), term=12,
                origination_date=date(2026, 1, 1), payment_day=1,
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id, date(2026, 1, 10))

            installments = balance_at.loan_contract_installments(loan, ctx)

            assert installments == contract_line(
                ctx.loan_walk(loan).stream, ctx.as_of,
            ).projected_splits
            assert [installments[0].due_date, installments[-1].due_date] == [
                date(2026, 2, 1), date(2027, 1, 1),
            ]
            assert len(installments) == 12
            assert installments[-1].balance_after == _ZERO

    def test_a_retired_loan_has_no_contract_left(self, app, db, seed_user):
        """Trued up to $0.00 on Mar 15, 2026, read Mar 20: nothing for the page to show.

        The same $12,000.00 loan.  The line itself still bills the ten
        installments after the true-up, Apr 1, 2026 through Jan 1, 2027:
        the first nine each $1,032.80 of P&I into a zero balance, every cent
        of it a refund (finding REC-539), and the last pays nothing, nothing
        standing to close.  The page reads none of them, as it reads no
        planned payment of a retired loan (``balance_at.loan_installments``).
        """
        with app.app_context():
            loan = create_loan_with_trueup(
                seed_user, db.session,
                origination_principal=Decimal("12000.00"),
                anchor_balance=Decimal("0.00"), anchor_date=date(2026, 3, 15),
                rate=Decimal("0.06000"), origination_date=date(2026, 1, 1),
                term=12,
            )
            ctx = BalanceContext.build(seed_user["user"].id, date(2026, 3, 20))

            line = contract_line(ctx.loan_walk(loan).stream, ctx.as_of)

            assert len(line.projected_splits) == 10
            assert {
                (outcome.excess, outcome.balance_after)
                for outcome in line.projected_splits[:-1]
            } == {(Decimal("1032.80"), _ZERO)}
            assert line.projected_splits[-1].cash == _ZERO
            assert balance_at.loan_contract_installments(loan, ctx) == []
