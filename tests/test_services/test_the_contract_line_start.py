"""Where the contract line starts: the first installment after the record's last event.

Ruling **plan#3382** (1A), plan step **recurrence:R16-e**.  The payoff
composer's contractual forward (``PayoffScenarios.original_forward``, the band
chart's x-axis and the lever's contract reference) starts at the first
contractual installment after the loan record's LAST EVENT -- its latest
payment's own due date or its latest balance assertion's date, read off the
walk the confirmed balance folds -- and runs ``term - k + 1`` installments
from installment ``k``.  With no confirmed view it starts after the latest
assertion, from that assertion's balance, with no history rows.

Until that step the start was the deleted walk 3's (``rate_period_engine
.replay_schedule``): one contractual step per payment RECORD from the latest
anchor, then the calendar month after its last row.  The two part on exactly
three shapes, each pinned here against hand-worked dates: two records paying
one installment, a payment due before the loan's day in its month, and a
balance asserted before that day.

Every figure is worked by hand from the loan's terms: interest is
``round(balance x 0.06 / 12)``, a level payment's principal is ``P&I -
interest``, and the line's last row absorbs the residue.
"""

import dataclasses
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app import ref_cache
from app.enums import InterestAccrualConventionEnum
from app.extensions import db as _db
from app.models.loan_params import LoanParams
from app.services import balance_at
from app.services.amortization_engine import RateChangeRecord
from app.services.balance_at import BalanceContext
from app.services.balance_at._resolution import resolved_loan
from app.services.loan_resolver import (
    ConfirmedLedgerView,
    LoanInputs,
    compute_payoff_scenarios,
)
from tests._test_helpers import (
    create_loan_account,
    create_settled_transfer,
    freeze_today,
)

_ZERO = Decimal("0.00")
_RATE = Decimal("0.06")
#: The ruling's example: $12,000.00 at 6% over 12 months.  Its level P&I is
#: round(12000 x 0.005 / (1 - 1.005^-12)) = 1,032.80.
_PRINCIPAL = Decimal("12000.00")
_PI = Decimal("1032.80")
#: After the Jan 16 and Jan 30 paychecks both pay February's installment:
#: the first faces February's charge, 12000.00 x 0.005 = 60.00, and pays
#: 1032.80 - 60.00 = 972.80 of principal; the second faces nothing and pays
#: 1032.80.  12000.00 - 972.80 - 1032.80 = 9,994.40.
_AFTER_FEBRUARY = Decimal("9994.40")
#: The contract line from 9,994.40 at 1,032.80 a month, Mar 1 - Dec 1 2026,
#: each row's interest round(balance x 0.005) on the balance the row before
#: left (9,994.40, 9,011.57, 8,023.83, 7,031.15, 6,033.51, 5,030.88,
#: 4,023.23, 3,010.55, 1,992.80, 969.96); the tenth clears the balance.
_LINE_ROW_INTEREST = [
    Decimal("49.97"), Decimal("45.06"), Decimal("40.12"), Decimal("35.16"),
    Decimal("30.17"), Decimal("25.15"), Decimal("20.12"), Decimal("15.05"),
    Decimal("9.96"), Decimal("4.85"),
]
#: Their sum, the ruling's figure.
_LINE_INTEREST = Decimal("275.61")


def _params(origination=date(2026, 1, 1), payment_day=1, term=12):
    """A duck-typed fixed-rate loan: $12,000.00 at 6%, due on *payment_day*."""
    return SimpleNamespace(
        origination_date=origination,
        term_months=term,
        original_principal=_PRINCIPAL,
        payment_day=payment_day,
        is_arm=False,
        arm_first_adjustment_months=None,
        arm_adjustment_interval_months=None,
        interest_accrual_convention_id=ref_cache.interest_accrual_convention_id(
            InterestAccrualConventionEnum.MONTHLY_1_12,
        ),
    )


def _anchor(on_date, balance, event_id=0):
    """An anchor fact: the three chronology terms and the asserted balance."""
    return SimpleNamespace(
        anchor_date=on_date,
        anchor_balance=balance,
        created_at=datetime(on_date.year, on_date.month, on_date.day,
                            tzinfo=timezone.utc),
        event_id=event_id,
    )


def _inputs(params, anchors=None):
    """The loan's inputs: its origination anchor unless stated, its 6% rate."""
    return LoanInputs(
        loan_params=params,
        anchor_events=(
            [_anchor(params.origination_date, _PRINCIPAL)]
            if anchors is None else anchors
        ),
        rate_changes=[RateChangeRecord(
            effective_date=params.origination_date,
            interest_rate=_RATE,
            monthly_pi=None,
        )],
    )


class TestTheLineStartsAfterTheViewsLastEvent:
    """A confirmed view: its balance, from the installment after its last event."""

    def test_two_records_paying_one_installment_start_the_line_a_month_after_it(self):
        """The ruling's example: February charged once, so the line starts Mar 1.

        Walk 3 counted the two February records as two months and started
        the line on Apr 1.  The record's last event is February's due date,
        so the line starts Mar 1, installment 2 of 12 (Feb 1 is installment
        1), and runs at most 12 - 2 + 1 = 11 installments; February's second
        payment paid principal ahead, so the balance clears on the tenth, Dec 1
        2026, with the interest stated at :data:`_LINE_INTEREST`.
        """
        view = ConfirmedLedgerView(
            balance=_AFTER_FEBRUARY, history_rows=[],
            last_recorded_on=date(2026, 2, 1),
        )
        line = compute_payoff_scenarios(
            loan_inputs=_inputs(_params()), confirmed_view=view,
        ).original_forward

        assert [row.payment_date for row in line] == [
            date(2026, month, 1) for month in range(3, 13)
        ]
        assert [row.interest for row in line] == _LINE_ROW_INTEREST
        assert sum(_LINE_ROW_INTEREST, _ZERO) == _LINE_INTEREST
        assert line[-1].remaining_balance == _ZERO
        # The line is the contract's estimate, never recorded fact.
        assert not any(row.is_confirmed for row in line)

    def test_an_off_day_event_starts_at_the_installment_closing_its_interval(self):
        """Due Mar 10 on a loan due the 22nd: the line starts Mar 22, not Apr 22.

        The payment pays the Feb 22 installment (ruling R-R109), whose
        interval Mar 22 closes; walk 3 stepped the calendar month after the
        due date.  Mar 22 is installment 2 of a loan from Jan 22, so the
        line runs at most 12 - 2 + 1 = 11 installments, and 11,027.20 is the
        on-schedule balance after installment 1, so all eleven run: the
        last on Jan 22 2027, clearing it.
        """
        view = ConfirmedLedgerView(
            balance=Decimal("11027.20"), history_rows=[],
            last_recorded_on=date(2026, 3, 10),
        )
        line = compute_payoff_scenarios(
            loan_inputs=_inputs(_params(date(2026, 1, 22), payment_day=22)),
            confirmed_view=view,
        ).original_forward

        assert line[0].payment_date == date(2026, 3, 22)
        assert len(line) == 11
        assert line[-1].payment_date == date(2027, 1, 22)
        assert line[-1].remaining_balance == _ZERO

    def test_the_view_supplies_the_history_and_the_balance(self):
        """The view's rows are the history verbatim; its balance seeds row one.

        Row one, Mar 1: interest round(9994.40 x 0.005) = 49.97, principal
        1032.80 - 49.97 = 982.83, leaving 9,011.57.
        """
        history = [SimpleNamespace(is_confirmed=True)]
        view = ConfirmedLedgerView(
            balance=_AFTER_FEBRUARY, history_rows=history,
            last_recorded_on=date(2026, 2, 1),
        )
        scenarios = compute_payoff_scenarios(
            loan_inputs=_inputs(_params()), confirmed_view=view,
        )

        assert scenarios.history_rows == history
        assert scenarios.original_forward[0].remaining_balance == Decimal("9011.57")


class TestWithNoViewTheLineStartsAfterTheLatestAssertion:
    """No view (no baseline, a read before origination, the from-origination contract)."""

    def test_a_balance_asserted_before_the_due_day_starts_that_months_installment(self):
        """A true-up on Mar 5 of a loan due the 22nd: the line starts Mar 22.

        Walk 3 started it Apr 22, the calendar month after the true-up.
        Seeded at the asserted 11,000.00 with no history rows: Mar 22's
        interest is round(11000.00 x 0.005) = 55.00.
        """
        params = _params(date(2026, 1, 22), payment_day=22)
        scenarios = compute_payoff_scenarios(
            loan_inputs=_inputs(params, [
                _anchor(params.origination_date, _PRINCIPAL, event_id=1),
                _anchor(date(2026, 3, 5), Decimal("11000.00"), event_id=2),
            ]),
        )

        assert scenarios.history_rows == []
        assert scenarios.original_forward[0].payment_date == date(2026, 3, 22)
        assert scenarios.original_forward[0].interest == Decimal("55.00")

    def test_the_origination_starts_the_line_at_the_first_installment(self):
        """The from-origination contract: Feb 1 - Jan 1 2027, all twelve.

        Row one: interest round(12000.00 x 0.005) = 60.00, principal
        1032.80 - 60.00 = 972.80, leaving 11,027.20 -- the same balance
        the first February payment leaves above.
        """
        line = compute_payoff_scenarios(
            loan_inputs=_inputs(_params()),
        ).original_forward

        assert [row.payment_date for row in line] == [
            date(2026, month, 1) for month in range(2, 13)
        ] + [date(2027, 1, 1)]
        assert line[0].remaining_balance == Decimal("11027.20")


class TestTheLineRunsTheInstallmentsLeft:
    """Installment ``k`` of ``term`` leaves ``term - k + 1``, and none past the term."""

    def test_a_start_on_the_last_installment_runs_one(self):
        """Last event Dec 1 2026: Jan 1 2027 is installment 12 of 12, one row.

        A 1,000.00 balance at the last installment: interest 5.00, and the
        final row clears it, principal 1,000.00.
        """
        view = ConfirmedLedgerView(
            balance=Decimal("1000.00"), history_rows=[],
            last_recorded_on=date(2026, 12, 1),
        )
        line = compute_payoff_scenarios(
            loan_inputs=_inputs(_params()), confirmed_view=view,
        ).original_forward

        assert [(row.payment_date, row.interest, row.principal) for row in line] == [
            (date(2027, 1, 1), Decimal("5.00"), Decimal("1000.00")),
        ]

    def test_a_record_past_the_term_has_no_contract_line(self):
        """Last event Jan 15 2027: Feb 1 2027 is installment 13 of 12, none left."""
        view = ConfirmedLedgerView(
            balance=Decimal("1000.00"), history_rows=[],
            last_recorded_on=date(2027, 1, 15),
        )
        assert compute_payoff_scenarios(
            loan_inputs=_inputs(_params()), confirmed_view=view,
        ).original_forward == []


def test_the_resolver_takes_no_payment_feed():
    """``LoanInputs`` is the loan's params, its assertions and its rates: no payments.

    Its fourth field, the prepared payment feed, fed the deleted replay and
    nothing else (plan step recurrence:R16-e); a bundle without it cannot
    route a payment record back into the contract line.
    """
    assert [field.name for field in dataclasses.fields(LoanInputs)] == [
        "loan_params", "anchor_events", "rate_changes",
    ]


class TestTheSeamCarriesTheRecordsLastEvent:
    """The worked example end to end: two paychecks settle into February's installment."""

    def _loan_with_two_february_payments(self, seed_user, seed_periods):
        """$12,000 / 6% / 12 months from 2026-01-01, paid Jan 16 and Jan 30, both due Feb 1."""
        loan = create_loan_account(
            seed_user, _db.session, name="Ruling Loan",
            principal=_PRINCIPAL, rate=_RATE, term=12,
            origination_date=date(2026, 1, 1), payment_day=1,
        )
        for period in (seed_periods[1], seed_periods[2]):
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], loan, period,
                amount=_PI, settled_on=period.start_date,
                due_date=date(2026, 2, 1),
            )
        _db.session.commit()
        return loan

    def test_the_view_carries_februarys_due_date_and_the_line_starts_in_march(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """The walk folds 9,994.40, its last event is Feb 1, and the line starts Mar 1.

        The resolved schedule is the two February rows, then the contract's
        Mar 1 - Dec 1 at :data:`_LINE_INTEREST`.
        """
        freeze_today(monkeypatch, date(2026, 2, 10))
        with app.app_context():
            assert (seed_periods[1].start_date, seed_periods[2].start_date) == (
                date(2026, 1, 16), date(2026, 1, 30),
            )
            loan = self._loan_with_two_february_payments(seed_user, seed_periods)
            ctx = BalanceContext(
                user_id=seed_user["user"].id, scenario=seed_user["scenario"],
                as_of=date(2026, 2, 10),
            )
            view = balance_at.confirmed_view(loan, ctx)
            assert view.balance == _AFTER_FEBRUARY
            assert view.last_recorded_on == date(2026, 2, 1)
            assert len(view.history_rows) == 2

            schedule = resolved_loan(loan, ctx).state.schedule
            forward = [row for row in schedule if not row.is_confirmed]
            assert [row.payment_date for row in forward] == [
                date(2026, month, 1) for month in range(3, 13)
            ]
            assert [row.interest for row in forward] == _LINE_ROW_INTEREST
            params = _db.session.query(LoanParams).filter_by(account_id=loan.id).one()
            assert (
                params.original_principal, params.term_months, params.payment_day,
            ) == (_PRINCIPAL, 12, 1)

    def test_a_pass_dated_after_today_reads_the_record(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A read of 2026-06-01 while today is 2026-02-10 answers the same record.

        The view returned ``None`` for a pass after today until plan step
        recurrence:R16-e (ruling plan#3382).  Nothing is recorded after
        Jan 30, so the later pass folds the same 9,994.40 and the same last
        event.
        """
        freeze_today(monkeypatch, date(2026, 2, 10))
        with app.app_context():
            loan = self._loan_with_two_february_payments(seed_user, seed_periods)
            later = BalanceContext(
                user_id=seed_user["user"].id, scenario=seed_user["scenario"],
                as_of=date(2026, 6, 1),
            )
            view = balance_at.confirmed_view(loan, later)
            assert view is not None
            assert view.balance == _AFTER_FEBRUARY
            assert view.last_recorded_on == date(2026, 2, 1)
