"""A loan's ONE charge calendar: the contract's installments, through a stream's last event.

Plan step **recurrence:R16-c-2** (rulings **R-R72**, **R-R89** and **R-R100**).
A loan is charged on EVERY contractual installment from origination, whether or
not a payment lands on it, in the posted ledger's walk and a screen's alike:
:func:`~app.services.installment_calendar.installment_dates` is the one date producer,
:func:`~app.services.loan_ledger._charges.contract_charges` prices each date, and
:func:`~app.services.loan_ledger.with_contract_charges` is the one composer
that decides how far a stream is charged -- through its LAST event, a payment,
an assertion or a projection alike.

Pure: no database, no app context.  Every figure is stated by hand and checked
against :func:`~app.utils.money.installment_interest` /
:func:`~app.utils.money.apply_payment_cash`, the ONE accrual and the ONE
allocation (6% on $10,000.00 is $50.00 a month).
"""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.routes.loan._helpers import band_chart_dates
from app.services.cash_ledger._loan_installment import _installment_cash
from app.services.loan_ledger import (
    LoanCalendar,
    LoanCashEvent,
    LoanEventStream,
    LoanResetEvent,
    replay_loan_stream,
    with_contract_charges,
)
from app.services.loan_ledger._charges import contract_charges
from app.services.installment_calendar import (
    first_installment_date,
    installment_after,
    installment_dates,
    installment_of,
    installment_paid_by,
)
from tests.oracles.loan_monthly_composition import accrual_charge, rate_period

_ZERO = Decimal("0.00")
_RATE = Decimal("0.06")          # $50.00 a month on $10,000.00


def _calendar(
    origination: date = date(2026, 1, 1),
    payment_day: int = 1,
    periods=None,
) -> LoanCalendar:
    """A loan's contract terms: one 6% rate period and no escrow unless stated."""
    return LoanCalendar(
        origination_date=origination,
        payment_day=payment_day,
        periods=periods if periods is not None else [
            rate_period(_RATE, start_date=origination),
        ],
        escrow_lines=[],
    )


def _payment(on_date: date, cash: str) -> LoanCashEvent:
    """A cash event dated at its installment, visible the same day."""
    return LoanCashEvent(
        on_date=on_date, cash=Decimal(cash), source=None, visible_on=on_date,
    )


def _reset(on_date: date, balance: str, *, opening: bool = False):
    """An asserted balance: the opening, or a true-up."""
    return LoanResetEvent(
        on_date=on_date, balance=Decimal(balance), source=None,
        is_opening=opening, is_tracking_start=False,
    )


class TestInstallmentDates:
    """The one date producer: first installment the month after origination, monthly after."""

    def test_the_first_installment_is_the_month_after_origination(self):
        """Originated mid-January, due the 1st: February 1st, March 1st."""
        assert installment_dates(date(2026, 1, 15), 1, date(2026, 3, 1)) == [
            date(2026, 2, 1), date(2026, 3, 1),
        ]

    def test_a_through_before_the_first_installment_yields_nothing(self):
        """Nothing is due in the origination's own month."""
        assert installment_dates(date(2026, 1, 1), 1, date(2026, 1, 31)) == []

    def test_a_due_day_of_31_returns_to_the_31st_after_february(self):
        """Clamped to Feb 28 in 2027, then back to the 31st -- never stuck on the 28th."""
        assert installment_dates(date(2026, 12, 31), 31, date(2027, 4, 30)) == [
            date(2027, 1, 31), date(2027, 2, 28),
            date(2027, 3, 31), date(2027, 4, 30),
        ]

    def test_a_leap_february_clamps_to_the_29th(self):
        """2028 is a leap year: the 31st clamps to Feb 29, then returns."""
        assert installment_dates(date(2027, 12, 31), 31, date(2028, 3, 31)) == [
            date(2028, 1, 31), date(2028, 2, 29), date(2028, 3, 31),
        ]

    def test_a_due_day_of_30_returns_to_the_30th_after_february(self):
        """Feb 28, then Mar 30 -- the day is clamped to each month afresh."""
        assert installment_dates(date(2026, 1, 30), 30, date(2026, 3, 30)) == [
            date(2026, 2, 28), date(2026, 3, 30),
        ]


class TestContractCharges:
    """Each installment is priced on its OWN date (ruling D5)."""

    def test_each_charge_takes_the_rate_in_force_on_its_installment(self):
        """A step to 12% on 03-01 reprices March onward, never February."""
        periods = [
            rate_period(_RATE, start_date=date(2026, 1, 1)),
            replace(
                rate_period(Decimal("0.12"), start_date=date(2026, 3, 1)),
                index=1,
            ),
        ]
        charges = contract_charges(
            _calendar(periods=periods), date(2026, 4, 1),
        )
        assert [(c.on_date, c.period.annual_rate, c.escrow) for c in charges] == [
            (date(2026, 2, 1), _RATE, _ZERO),
            (date(2026, 3, 1), Decimal("0.12"), _ZERO),
            (date(2026, 4, 1), Decimal("0.12"), _ZERO),
        ]

    def test_a_through_before_the_first_installment_charges_nothing(self):
        """No installment has fallen, so nothing is owed yet."""
        assert contract_charges(_calendar(), date(2026, 1, 31)) == []


class TestWithContractCharges:
    """The one composer: charges through the stream's LAST event, of any kind."""

    def test_the_charges_reach_the_last_projection(self):
        """A projection in June is faced by June's installment."""
        stream = with_contract_charges(
            LoanEventStream(
                charges=(),
                payments=[_payment(date(2026, 2, 1), "500.00")],
                resets=[_reset(date(2026, 1, 1), "10000.00", opening=True)],
                projections=[_payment(date(2026, 6, 10), "500.00")],
            ),
            _calendar(),
        )
        assert [c.on_date for c in stream.charges] == [
            date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1),
            date(2026, 5, 1), date(2026, 6, 1),
        ]

    def test_the_charges_reach_a_last_assertion(self):
        """An assertion after the last payment still bounds the calendar."""
        stream = with_contract_charges(
            LoanEventStream(
                charges=(),
                payments=[_payment(date(2026, 2, 1), "500.00")],
                resets=[
                    _reset(date(2026, 1, 1), "10000.00", opening=True),
                    _reset(date(2026, 4, 15), "9000.00"),
                ],
            ),
            _calendar(),
        )
        assert [c.on_date for c in stream.charges][-1] == date(2026, 4, 1)

    def test_a_stream_with_no_event_is_charged_nothing(self):
        """No event, no reach: the composer charges nothing."""
        assert with_contract_charges(
            LoanEventStream(charges=(), payments=()), _calendar(),
        ).charges == ()

    def test_the_stream_carries_the_calendar_it_was_charged_on(self):
        """The stream keeps the calendar object, so a reader takes its terms from it."""
        calendar = _calendar()
        stream = with_contract_charges(
            LoanEventStream(
                charges=(), payments=[_payment(date(2026, 2, 1), "500.00")],
            ),
            calendar,
        )
        assert stream.calendar is calendar


class TestTheReplayOverTheContractsCalendar:
    """What charging every installment does to a walk (rulings R-R72, R-R89)."""

    def test_a_skipped_month_is_cleared_by_the_next_payment_first(self):
        """February paid, March skipped, April paid: April clears March and April.

        $10,000.00 at 6%: Feb 50.00 / 450.00 -> 9,550.00; March charges 47.75
        and nothing pays; April charges 47.75 more on the unreduced 9,550.00,
        so April's $500.00 clears 95.50 and pays 404.50 -> 9,145.50.
        """
        walk = replay_loan_stream(with_contract_charges(
            LoanEventStream(
                charges=(),
                payments=[
                    _payment(date(2026, 2, 1), "500.00"),
                    _payment(date(2026, 4, 1), "500.00"),
                ],
                resets=[_reset(date(2026, 1, 1), "10000.00", opening=True)],
            ),
            _calendar(),
        ))
        february, april = walk.settled_splits
        assert (february.interest, february.principal) == (
            Decimal("50.00"), Decimal("450.00"),
        )
        assert (april.interest, april.principal, april.balance_after) == (
            Decimal("95.50"), Decimal("404.50"), Decimal("9145.50"),
        )
        assert april.charge_date == date(2026, 4, 1)

    def test_an_assertion_clears_the_months_standing_before_it(self):
        """Originated 2025, never paid, asserted $10,000.00 on 2026-01-10.

        Twelve installments (2025-02-01 .. 2026-01-01) stand when the
        assertion lands; it states the balance owed and clears them (R-R72
        part (2)), so the February payment faces February's $50.00 alone.
        """
        walk = replay_loan_stream(with_contract_charges(
            LoanEventStream(
                charges=(),
                payments=[_payment(date(2026, 2, 1), "500.00")],
                resets=[
                    _reset(date(2025, 1, 1), "12000.00", opening=True),
                    _reset(date(2026, 1, 10), "10000.00"),
                ],
            ),
            _calendar(origination=date(2025, 1, 1)),
        ))
        assert len([
            c for c in walk.stream.charges if c.on_date < date(2026, 1, 10)
        ]) == 12
        [february] = walk.settled_splits
        assert (february.interest, february.principal) == (
            Decimal("50.00"), Decimal("450.00"),
        )

    def test_an_off_day_payment_faces_the_installment_its_interval_opens_on(self):
        """Due the 22nd; a payment on 03-10 falls in the Feb 22 - Mar 21 interval.

        It faces February 22nd's charge -- one month, $50.00 -- and never
        March 22nd's, which has not fallen (ruling R-R89, finding D55: the
        calendar month would have paired it with March).
        """
        walk = replay_loan_stream(with_contract_charges(
            LoanEventStream(
                charges=(),
                payments=[_payment(date(2026, 3, 10), "500.00")],
                resets=[_reset(date(2026, 1, 22), "10000.00", opening=True)],
            ),
            _calendar(origination=date(2026, 1, 22), payment_day=22),
        ))
        [payment] = walk.settled_splits
        assert payment.charge_date == date(2026, 2, 22)
        assert (payment.interest, payment.principal) == (
            Decimal("50.00"), Decimal("450.00"),
        )

    def test_the_what_if_extra_rides_no_month_skipped_behind_a_fact(self):
        """An extra $100 a month joins the charges after the facts only.

        Facts: opening $10,000.00 on Jan 1, a $500.00 payment on June 1.
        Projected: April's overdue catch-up (pushed behind June) and July's
        installment.  February through June are the facts' months, cleared
        by June's payment; the extra accrues at July's charge alone, so the
        catch-up is $500.00 of principal with or without it and July carries
        exactly one $100.00 helping.
        """
        stream = with_contract_charges(
            LoanEventStream(
                charges=(),
                payments=[_payment(date(2026, 6, 1), "500.00")],
                resets=[_reset(date(2026, 1, 1), "10000.00", opening=True)],
                projections=[
                    _payment(date(2026, 4, 1), "500.00"),
                    _payment(date(2026, 7, 1), "500.00"),
                ],
            ),
            _calendar(),
        )
        plain = replay_loan_stream(stream)
        topped = replay_loan_stream(stream, extra_per_period=Decimal("100.00"))
        assert topped.settled_splits == plain.settled_splits
        catch_up, july = topped.projected_splits
        plain_catch_up, plain_july = plain.projected_splits
        assert catch_up.principal == plain_catch_up.principal == Decimal("500.00")
        assert july.principal - plain_july.principal == Decimal("100.00")

    def test_a_catch_up_walks_before_an_installment_on_the_boundary_day(self):
        """An overdue catch-up pushed to the boundary walks ahead of that day's charge.

        $10,000.00 at 6% due the 1st.  The last fact is an off-day $500.00
        payment on Aug 31, which clears the seven installments Feb 1 - Aug 1
        (7 x 50.00 = 350.00 interest, 150.00 principal -> 9,850.00), so the
        projections walk from Sep 1 -- itself an installment.  July's overdue
        catch-up is pushed there and walks in CONTRACT order, before the Sep 1
        charge: nothing stands, 500.00 principal -> 9,350.00.  Then Sep 1
        charges 9,350.00 x 0.005 = 46.75, and September's $500.00 pays 46.75 /
        453.25 -> 8,896.75.  Walked charge-first, the catch-up would pay
        September's interest instead and the loan would end at 8,899.25.
        """
        walk = replay_loan_stream(with_contract_charges(
            LoanEventStream(
                charges=(),
                payments=[_payment(date(2026, 8, 31), "500.00")],
                resets=[_reset(date(2026, 1, 1), "10000.00", opening=True)],
                projections=[
                    _payment(date(2026, 7, 1), "500.00"),
                    _payment(date(2026, 9, 1), "500.00"),
                ],
            ),
            _calendar(),
        ))
        [fact] = walk.settled_splits
        assert (fact.interest, fact.principal, fact.balance_after) == (
            Decimal("350.00"), Decimal("150.00"), Decimal("9850.00"),
        )
        catch_up, september = walk.projected_splits
        assert (catch_up.interest, catch_up.principal, catch_up.balance_after) == (
            _ZERO, Decimal("500.00"), Decimal("9350.00"),
        )
        assert (september.interest, september.principal, september.balance_after) == (
            Decimal("46.75"), Decimal("453.25"), Decimal("8896.75"),
        )

    def test_the_what_if_extra_joins_an_installment_on_the_boundary_day(self):
        """A charge falling ON the projection boundary carries the extra.

        $10,000.00 at 6% due the 1st: July's $500.00 clears Feb - Jul (300.00
        interest) -> 9,800.00; August's 49.00 then stands until a true-up to
        $9,000.00 on Aug 31 clears it.  The boundary is Sep 1, an installment:
        it charges 9,000.00 x 0.005 = 45.00, and "an extra $100 a month" joins
        it, so September's $500.00 pays 455.00 of principal plain and 555.00
        with the extra.  The facts' splits do not move.
        """
        stream = with_contract_charges(
            LoanEventStream(
                charges=(),
                payments=[_payment(date(2026, 7, 1), "500.00")],
                resets=[
                    _reset(date(2026, 1, 1), "10000.00", opening=True),
                    _reset(date(2026, 8, 31), "9000.00"),
                ],
                projections=[_payment(date(2026, 9, 1), "500.00")],
            ),
            _calendar(),
        )
        plain = replay_loan_stream(stream)
        topped = replay_loan_stream(stream, extra_per_period=Decimal("100.00"))
        assert topped.settled_splits == plain.settled_splits
        [plain_september] = plain.projected_splits
        [september] = topped.projected_splits
        assert (plain_september.interest, plain_september.principal) == (
            Decimal("45.00"), Decimal("455.00"),
        )
        assert (september.interest, september.principal) == (
            Decimal("45.00"), Decimal("555.00"),
        )

    def test_a_hand_stated_charge_list_is_replaced_by_the_calendar(self):
        """The composer owns the charges: a stream's own list does not survive."""
        stream = with_contract_charges(
            LoanEventStream(
                charges=[accrual_charge(date(2026, 2, 15), _RATE, _ZERO)],
                payments=[_payment(date(2026, 2, 1), "500.00")],
            ),
            _calendar(),
        )
        assert [c.on_date for c in stream.charges] == [date(2026, 2, 1)]


class TestInstallmentOf:
    """Ruling R-R89's pairing: the latest installment on or before a day (R-R104, R-R105)."""

    def test_a_day_on_an_installment_is_that_installment(self):
        """A payment due on the contractual day pays its own installment."""
        assert installment_of(date(2026, 1, 22), 22, date(2026, 3, 22)) == date(2026, 3, 22)

    def test_a_day_inside_an_interval_is_the_installment_that_opened_it(self):
        """Mar 10 and Mar 21 both fall in the Feb 22 - Mar 21 interval."""
        assert installment_of(date(2026, 1, 22), 22, date(2026, 3, 10)) == date(2026, 2, 22)
        assert installment_of(date(2026, 1, 22), 22, date(2026, 3, 21)) == date(2026, 2, 22)

    def test_a_day_before_the_first_installment_has_none(self):
        """Ruling R-C's early extra: after the Jan 22 origination, before Feb 22.

        The origination day itself and a day of the origination month after
        its own due day (Jan 25) fall before the first installment too.
        """
        for day in (date(2026, 1, 22), date(2026, 1, 25), date(2026, 2, 10)):
            assert installment_of(date(2026, 1, 22), 22, day) is None
        assert installment_of(date(2026, 1, 22), 22, date(2026, 2, 22)) == date(2026, 2, 22)

    def test_a_clamped_installment_opens_its_interval(self):
        """Due the 31st: Feb 28 is an installment, so Mar 30 falls in its interval."""
        assert installment_of(date(2026, 12, 31), 31, date(2027, 3, 30)) == date(2027, 2, 28)
        assert installment_of(date(2026, 12, 31), 31, date(2027, 3, 31)) == date(2027, 3, 31)

    def test_it_agrees_with_the_list_on_every_day(self):
        """The rule inverted names, for every day, the latest listed installment.

        Every due day 1-31, every day from before the origination (a December
        31st before a leap February) to three years on: the lookup and the
        enumeration both start at :func:`first_installment_date` and step the
        one clamp, and this sweep is what holds them to one answer.
        """
        origination = date(2027, 12, 31)
        for payment_day in range(1, 32):
            listed = installment_dates(origination, payment_day, date(2031, 1, 31))
            day, last = date(2027, 11, 1), None
            while day <= date(2030, 12, 31):
                while listed and listed[0] <= day:
                    last = listed.pop(0)
                assert installment_of(origination, payment_day, day) == last, (
                    payment_day, day,
                )
                day += timedelta(days=1)


class TestInstallmentPaidBy:
    """The day a payment is NAMED by: the installment it pays (R-R104, R-R108, R-R109).

    :func:`installment_of`, or the payment's own due date before the loan's
    first installment -- the one home of the fallback the cash price and a
    ``$0.00`` close's day each spelled out beside :func:`installment_of`.
    """

    def test_an_on_day_payment_is_named_by_its_own_due_date(self):
        """Due Mar 22 on a loan due the 22nd: its installment IS its due date."""
        assert installment_paid_by(
            date(2026, 1, 22), 22, date(2026, 3, 22),
        ) == date(2026, 3, 22)

    def test_an_off_day_payment_is_named_by_its_intervals_installment(self):
        """Due Mar 10 on a loan due the 22nd: it pays, and is named by, Feb 22."""
        assert installment_paid_by(
            date(2026, 1, 22), 22, date(2026, 3, 10),
        ) == date(2026, 2, 22)

    def test_an_early_extra_keeps_its_own_due_date(self):
        """Before the Feb 22 first installment there is none to name it by."""
        for due in (date(2026, 1, 25), date(2026, 2, 10)):
            assert installment_paid_by(date(2026, 1, 22), 22, due) == due

    def test_it_never_falls_as_the_due_date_rises(self):
        """Ordering by due date orders by installment too.

        The history card and the confirmed schedule sort their rows by the
        payment's own due date and name them by this; the names stay ascending
        only if it is monotone, early extras included.  Every due day 1-31,
        every day from before the origination to three years on.
        """
        origination = date(2027, 12, 31)
        for payment_day in range(1, 32):
            day, previous = date(2027, 11, 1), None
            while day <= date(2030, 12, 31):
                named = installment_paid_by(origination, payment_day, day)
                assert named <= day, (payment_day, day)
                assert previous is None or named >= previous, (payment_day, day)
                day, previous = day + timedelta(days=1), named


class TestInstallmentAfter:
    """The first installment STRICTLY after a day: where the contract line starts (plan#3382).

    The payoff composer hands it the date of the loan record's last event,
    a payment's own due date or an assertion's date, and starts the
    contract's remaining installments there.
    """

    def test_a_day_on_an_installment_steps_to_the_next(self):
        """Due Mar 22 on a loan due the 22nd: that installment is charged, Apr 22 is next.

        Strict, because an installment due on an assertion's date is
        inside the asserted balance (``due_after_anchor``).
        """
        assert installment_after(
            date(2026, 1, 22), 22, date(2026, 3, 22),
        ) == date(2026, 4, 22)

    def test_a_day_inside_an_interval_steps_to_the_installment_closing_it(self):
        """Mar 10 and Mar 21 fall in the Feb 22 interval, which Mar 22 closes.

        The off-day case the ruling moves a month earlier than the deleted
        replay, which stepped the calendar month after the due date (Apr 22).
        """
        for day in (date(2026, 3, 10), date(2026, 3, 21)):
            assert installment_after(date(2026, 1, 22), 22, day) == date(2026, 3, 22)

    def test_a_day_before_the_first_installment_answers_the_first(self):
        """The origination, a later day of its month, and a day before Feb 22.

        The origination itself is the from-origination contract's start,
        :func:`first_installment_date`; a balance asserted between the
        origination and the first due day starts the line there too.
        """
        for day in (date(2026, 1, 22), date(2026, 1, 25), date(2026, 2, 10)):
            assert installment_after(date(2026, 1, 22), 22, day) == date(2026, 2, 22)
        assert installment_after(
            date(2026, 1, 22), 22, date(2026, 1, 22),
        ) == first_installment_date(date(2026, 1, 22), 22)

    def test_a_clamped_installment_steps_back_to_the_meant_day(self):
        """Due the 31st: past Feb 28 the next installment is Mar 31, not Mar 28."""
        assert installment_after(
            date(2026, 12, 31), 31, date(2027, 2, 28),
        ) == date(2027, 3, 31)
        assert installment_after(
            date(2026, 12, 31), 31, date(2027, 3, 5),
        ) == date(2027, 3, 31)

    def test_it_agrees_with_the_list_on_every_day(self):
        """For every day, the earliest listed installment after it.

        Every due day 1-31, every day from before the origination (a
        December 31st before a leap February) to three years on, against
        the enumeration (:func:`installment_dates`), which the stepped rule
        must never contradict.
        """
        origination = date(2027, 12, 31)
        for payment_day in range(1, 32):
            listed = installment_dates(origination, payment_day, date(2031, 3, 31))
            day = date(2027, 11, 1)
            while day <= date(2030, 12, 31):
                expected = next(due for due in listed if due > day)
                assert installment_after(origination, payment_day, day) == expected, (
                    payment_day, day,
                )
                day += timedelta(days=1)


def _escrow_line(*versions: tuple[date, str]) -> SimpleNamespace:
    """One escrow line with its versions, as ``(effective_date, annual)`` pairs."""
    return SimpleNamespace(
        id=1, name="Escrow",
        versions=[
            SimpleNamespace(
                id=index, effective_date=effective, annual_amount=Decimal(annual),
                is_removed=False, inflation_rate=None, created_at=None,
            )
            for index, (effective, annual) in enumerate(versions)
        ],
    )


class TestAPaymentIsPricedOnItsInterval:
    """Ruling R-R104 ("Price on the interval"): cash is priced on the installment it pays.

    The developer's worked example, made-up figures: $10,000.00 at 6% due the
    22nd, originated 2026-01-22 (first installment Feb 22), P&I $200.00,
    escrow $100.00 a month ($1,200.00 a year) rising to $300.00 ($3,600.00)
    on Mar 1.  A payment due Mar 10 pays the Feb 22 installment (ruling
    R-R89), whose charge is Feb 22's: interest 10,000.00 x 0.06 / 12 =
    $50.00 and escrow $100.00.  Priced on Feb 22 its cash is 200.00 + 100.00
    = $300.00 and its split $50.00 / $100.00 / $150.00, principal exactly P&I
    - interest.  Priced on its own date (ruling R-IJ before R-R104) the cash
    was 200.00 + 300.00 = $500.00, and the $200.00 escrow difference landed
    in principal ($350.00).
    """

    _ORIGINATION = date(2026, 1, 22)
    _LINES = [_escrow_line((date(2026, 1, 22), "1200.00"), (date(2026, 3, 1), "3600.00"))]

    def _basis(self, periods=None, lines=None) -> LoanCalendar:
        """The loan's terms: one 6% period at P&I $200.00 and _LINES unless stated."""
        return LoanCalendar(
            origination_date=self._ORIGINATION,
            payment_day=22,
            periods=periods if periods is not None else [
                rate_period(_RATE, start_date=self._ORIGINATION,
                            period_pi=Decimal("200.00")),
            ],
            escrow_lines=lines if lines is not None else self._LINES,
        )

    def _cash(self, due: date, basis: LoanCalendar | None = None) -> Decimal:
        """The derive-mode cash of a payment due *due*, no standing extra."""
        return _installment_cash(
            basis if basis is not None else self._basis(),
            due, date(2026, 1, 1), _ZERO,
        )

    def test_an_off_day_payment_carries_its_intervals_escrow(self):
        """Due Mar 10: Feb 22's $100.00 escrow, not Mar 10's $300.00."""
        assert self._cash(date(2026, 3, 10)) == Decimal("300.00")

    def test_an_on_day_payment_is_priced_on_its_own_due_date(self):
        """Due Mar 22 (on the day, after the Mar 1 rise): 200.00 + 300.00."""
        assert self._cash(date(2026, 3, 22)) == Decimal("500.00")

    def test_an_early_extra_is_priced_on_its_own_due_date(self):
        """Due Feb 10, before the first installment: nothing stands; 200.00 + 100.00."""
        assert self._cash(date(2026, 2, 10)) == Decimal("300.00")

    def test_an_early_extra_reads_its_own_dates_escrow(self):
        """Due Feb 10, before the first installment, after an escrow rise on Feb 1.

        No installment stands over it, so it is priced on its own date:
        200.00 + the $300.00 escrow in force on Feb 10 = $500.00.  Priced on
        the origination (Jan 22, $100.00) it would read $300.00.
        """
        lines = [_escrow_line(
            (date(2026, 1, 22), "1200.00"), (date(2026, 2, 1), "3600.00"),
        )]
        assert _installment_cash(
            self._basis(lines=lines), date(2026, 2, 10), date(2026, 1, 1), _ZERO,
        ) == Decimal("500.00")

    def test_an_off_day_payment_carries_its_intervals_rate_period(self):
        """A rate change on Mar 1 (P&I $250.00) does not reach the Feb 22 installment.

        Due Mar 10: Feb 22's period, P&I $200.00, plus Feb 22's $100.00
        escrow.  On its own date it would read $250.00 + $300.00 = $550.00.
        """
        basis = self._basis([
            rate_period(_RATE, start_date=self._ORIGINATION,
                        period_pi=Decimal("200.00")),
            replace(
                rate_period(Decimal("0.12"), start_date=date(2026, 3, 1),
                            period_pi=Decimal("250.00")),
                index=1,
            ),
        ])
        assert self._cash(date(2026, 3, 10), basis) == Decimal("300.00")

    def test_the_split_backs_out_exactly_the_escrow_the_cash_carries(self):
        """The priced cash replayed against the contract's charges: $50 / $100 / $150.

        The balance falls 10,000.00 - 150.00 = $9,850.00: every cent of
        escrow built into the cash is backed out again, so principal is P&I
        less interest, as it is for a payment due on the contractual day.
        """
        cash = self._cash(date(2026, 3, 10))
        walk = replay_loan_stream(with_contract_charges(
            LoanEventStream(
                charges=(),
                payments=[_payment(date(2026, 3, 10), str(cash))],
                resets=[_reset(self._ORIGINATION, "10000.00", opening=True)],
            ),
            replace(
                _calendar(origination=self._ORIGINATION, payment_day=22),
                escrow_lines=self._LINES,
            ),
        ))
        [payment] = walk.settled_splits
        assert payment.charge_date == date(2026, 2, 22)
        assert (
            payment.interest, payment.escrow, payment.principal,
            payment.balance_after,
        ) == (
            Decimal("50.00"), Decimal("100.00"), Decimal("150.00"),
            Decimal("9850.00"),
        )


class TestTheBandChartsExtension:
    """The loan page's grid past the contract steps the loan's own calendar."""

    _PARAMS = SimpleNamespace(origination_date=date(2026, 12, 31), payment_day=31)
    #: The loan is tracked from its origination (ruling R-R111's recorded start).
    _START = date(2026, 12, 31)
    # A contract whose last installment is a CLAMPED February 28th.
    _SCENARIOS = SimpleNamespace(
        history_rows=[SimpleNamespace(payment_date=date(2027, 1, 31))],
        original_forward=[SimpleNamespace(payment_date=date(2027, 2, 28))],
    )

    def test_a_contract_ending_on_a_clamped_february_returns_to_the_31st(self):
        """Due the 31st, the contract ends 2027-02-28, the payoff is 2027-06-30.

        The grid continues Mar 31, Apr 30, May 31, Jun 30 -- each month's due
        day clamped afresh.  Stepping a month count from the clamped last row
        would keep the clamp: Mar 28, Apr 28, and so on.
        """
        assert band_chart_dates(
            self._SCENARIOS, date(2027, 6, 30), [], self._PARAMS, self._START,
        ) == [
            date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31),
            date(2027, 4, 30), date(2027, 5, 31), date(2027, 6, 30),
        ]

    def test_a_payoff_between_installments_runs_to_the_next_one(self):
        """A payoff on 2027-04-15 (a definition's own cadence) ends on Apr 30."""
        assert band_chart_dates(
            self._SCENARIOS, date(2027, 4, 15), [], self._PARAMS, self._START,
        )[-2:] == [date(2027, 3, 31), date(2027, 4, 30)]


class TestTheBandChartsMonthsAreTheLoansInstallments:
    """Ruling R-R110: every installment from the loan's recorded start, whatever the rows say.

    The rows only say where the contract ends and whether there is anything
    to chart; the months are the loan's own installment dates.
    """

    _PARAMS = SimpleNamespace(origination_date=date(2026, 1, 22), payment_day=22)

    def test_repeated_and_skipped_row_dates_do_not_shape_the_axis(self):
        """Two confirmed rows on Feb 22 and a forward from Apr 22: Feb, Mar, Apr, May.

        The shape ruling R-R109 left the axis in: two payments inside Feb 22's
        interval both dated Feb 22, and the replay's forward starting from a
        payment's own due date (Mar 10 -> Apr 22), skipping Mar 22.  The grid
        is every installment, one point each.
        """
        scenarios = SimpleNamespace(
            history_rows=[
                SimpleNamespace(payment_date=date(2026, 2, 22)),
                SimpleNamespace(payment_date=date(2026, 2, 22)),
            ],
            original_forward=[
                SimpleNamespace(payment_date=date(2026, 4, 22)),
                SimpleNamespace(payment_date=date(2026, 5, 22)),
            ],
        )
        assert band_chart_dates(
            scenarios, date(2026, 5, 22), [], self._PARAMS, date(2026, 1, 22),
        ) == [
            date(2026, 2, 22), date(2026, 3, 22), date(2026, 4, 22),
            date(2026, 5, 22),
        ]

    def test_a_loan_tracked_mid_life_starts_at_its_first_tracked_installment(self):
        """Tracked from Mar 1: the first installment after it is Mar 22."""
        scenarios = SimpleNamespace(
            history_rows=[],
            original_forward=[SimpleNamespace(payment_date=date(2026, 5, 22))],
        )
        assert band_chart_dates(
            scenarios, None, [], self._PARAMS, date(2026, 3, 1),
        ) == [date(2026, 3, 22), date(2026, 4, 22), date(2026, 5, 22)]

    def test_a_tracking_start_on_an_installment_starts_at_the_next(self):
        """Tracked from Feb 22 itself: the assertion already states Feb 22's balance.

        The walk applies an assertion after its own day's charge and
        payments, so the balance it states is the one after Feb 22's
        installment, and the first installment the record owes is Mar 22.
        """
        scenarios = SimpleNamespace(
            history_rows=[],
            original_forward=[SimpleNamespace(payment_date=date(2026, 4, 22))],
        )
        assert band_chart_dates(
            scenarios, None, [], self._PARAMS, date(2026, 2, 22),
        ) == [date(2026, 3, 22), date(2026, 4, 22)]

    def test_a_record_starting_at_the_loans_end_charts_nothing_past_it(self):
        """A contract ending Mar 22: a start on or after it charts no month at all.

        The grid runs through the contract's last installment, so a record
        that starts on Mar 22 itself (its statement already states the
        balance after that installment) or on Apr 21, after the loan's end,
        has no installment left to chart.  The loop appended the first
        installment after the start before testing the end, so both read
        ``[Apr 22]``, a month past the loan's last installment (measured on
        R16-c-2's checkpoint 10), while a start of Apr 22 already read
        ``[]``.  A start the day before the last installment still charts it.
        """
        scenarios = SimpleNamespace(
            history_rows=[
                SimpleNamespace(payment_date=date(2026, 1, 22)),
                SimpleNamespace(payment_date=date(2026, 2, 22)),
                SimpleNamespace(payment_date=date(2026, 3, 22)),
            ],
            original_forward=[],
        )
        assert [
            band_chart_dates(scenarios, None, [], self._PARAMS, start)
            for start in (
                date(2026, 3, 21), date(2026, 3, 22), date(2026, 4, 21),
                date(2026, 4, 22),
            )
        ] == [[date(2026, 3, 22)], [], [], []]
