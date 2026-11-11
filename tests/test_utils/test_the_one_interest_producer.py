"""The ONE interest producer and the window an installment charges for.

Plan step **recurrence:R16-d** (finding **D52**; rulings plan#3348, plan#3354).
:func:`~app.utils.money.accrued_interest` prices a window under the agreement's
accrual convention at full precision; :func:`~app.utils.money.installment_interest`
rounds it once; :func:`~app.services.installment_calendar.accrual_window` is the
window an installment walk passes.  Graded here:

* ``MONTHLY_1_12``'s arithmetic, every figure worked by hand in its comment.
* Every total arm, and that the convention is looked up before them.
* One installment's window is byte-identical to the spelling every loan has
  been charged by since E-24, ``round_money(balance * (annual_rate / 12))``
  (``accrue_monthly_interest``'s body), over seeded randomised draws and the
  one pinned input where the OTHER association parts from it.
* The window spans exactly one month for every due day, and the windows of a
  loan's installments abut -- the property that keeps the monthly convention
  from moving money once the window is an input.

Pure: no database, no app context.
"""

import random
from datetime import date
from decimal import Decimal

import pytest

from app.enums import InterestAccrualConventionEnum
from app.services.installment_calendar import accrual_window, installment_dates
from app.utils.dates import clamped_day, month_ordinal
from app.utils.money import (
    MONTHS_PER_YEAR,
    accrued_interest,
    installment_interest,
    round_money,
)

_MONTHLY = InterestAccrualConventionEnum.MONTHLY_1_12
_TEN_THOUSAND = Decimal("10000.00")
_SIX_PERCENT = Decimal("0.06")       # 0.06 / 12 = 0.005 exactly: $50.00 a month
_ONE_THOUSAND = Decimal("1000.00")
_FIVE_PERCENT = Decimal("0.05")      # 0.05 / 12 repeats: the precision cases


def _retired_spelling(balance: Decimal, annual_rate: Decimal) -> Decimal:
    """``accrue_monthly_interest``'s body, verbatim: what every loan was charged by.

    Spelled here rather than imported so the byte-identity below still grades
    the producer once that function is deleted (plan#3359).
    """
    if annual_rate <= 0:
        return Decimal("0.00")
    return round_money(balance * (annual_rate / MONTHS_PER_YEAR))


class TestMonthlyOneTwelfthChargesPerCalendarMonth:
    """``MONTHLY_1_12``: a twelfth of the annual rate per calendar month spanned."""

    def test_one_month_is_a_twelfth_of_the_rate(self):
        """$10,000.00 at 6% for one month: 10,000 x 0.005 = $50.00."""
        assert accrued_interest(
            _TEN_THOUSAND, _SIX_PERCENT,
            date(2026, 1, 1), date(2026, 2, 1), _MONTHLY,
        ) == Decimal("50")

    def test_the_figure_is_returned_at_full_precision(self):
        """$1,000.00 at 5% for one month: 50 / 12, to the context's 28 digits.

        Unrounded: the cent figure is $4.17, and the producer must not have
        reached it.
        """
        interest = accrued_interest(
            _ONE_THOUSAND, _FIVE_PERCENT,
            date(2026, 1, 15), date(2026, 2, 15), _MONTHLY,
        )
        assert interest == Decimal("4.166666666666666666666666667")
        assert interest != Decimal("4.17")

    def test_two_months_are_the_one_month_figure_twice(self):
        """$1,000.00 at 5% for two months: ``(balance * (rate / 12)) * 2``.

        By hand: the one-month figure 4.166666666666666666666666667, doubled,
        is 4.166...667 x 2 = 8.333333333333333333333333334.  Folding the months
        into the rate instead, ``balance * (rate * 2 / 12)``, is 1,000 x
        0.008333...333 = 8.333333333333333333333333333 -- a different 28th
        digit, so this pins where the month count enters: after the twelfth is
        taken.  It does NOT separate ``balance * (rate / 12)`` from ``(balance
        * rate) / 12``, which agree here to the last digit; the pinned
        $271,375.20 case (:class:`TestOneInstallmentIsTheChargeEveryLoanHasBorne`)
        is what tells those two apart.
        """
        assert accrued_interest(
            _ONE_THOUSAND, _FIVE_PERCENT,
            date(2026, 1, 15), date(2026, 3, 15), _MONTHLY,
        ) == Decimal("8.333333333333333333333333334")

    @pytest.mark.parametrize(
        ("start", "end"),
        [
            (date(2026, 1, 31), date(2026, 2, 28)),   # 28 days
            (date(2026, 2, 28), date(2026, 3, 31)),   # 31 days
            (date(2026, 12, 15), date(2027, 1, 15)),  # across a year
            (date(2026, 3, 1), date(2026, 4, 30)),    # 60 days, still April
        ],
    )
    def test_the_windows_days_never_enter_only_its_months(self, start, end):
        """Each window spans one calendar month by ordinal, so each is $50.00.

        ``[Mar 1, Apr 30)`` is 60 days and one month: March to April.
        """
        assert accrued_interest(
            _TEN_THOUSAND, _SIX_PERCENT, start, end, _MONTHLY,
        ) == Decimal("50")

    def test_a_window_inside_one_month_charges_nothing(self):
        """``[Mar 1, Mar 31)`` spans no month boundary, so the convention charges $0.

        Not a guard but the convention's own answer, and the reason a walk's
        window must come from :func:`accrual_window` rather than open on a day
        inside its due date's month (see
        :class:`TestTheWindowSpansExactlyOneMonth`).
        """
        assert accrued_interest(
            _TEN_THOUSAND, _SIX_PERCENT,
            date(2026, 3, 1), date(2026, 3, 31), _MONTHLY,
        ) == Decimal("0")


class TestItIsTotalOverItsInputs:
    """A non-positive balance, rate or window accrues ``0``, unrounded and rounded."""

    @pytest.mark.parametrize(
        ("balance", "annual_rate", "start", "end"),
        [
            (Decimal("0.00"), _SIX_PERCENT, date(2026, 1, 1), date(2026, 2, 1)),
            (Decimal("-0.01"), _SIX_PERCENT, date(2026, 1, 1), date(2026, 2, 1)),
            (Decimal("-5000.00"), _SIX_PERCENT, date(2026, 1, 1), date(2026, 2, 1)),
            (_TEN_THOUSAND, Decimal("0"), date(2026, 1, 1), date(2026, 2, 1)),
            (_TEN_THOUSAND, Decimal("-0.01"), date(2026, 1, 1), date(2026, 2, 1)),
            (_TEN_THOUSAND, _SIX_PERCENT, date(2026, 2, 1), date(2026, 2, 1)),
            (_TEN_THOUSAND, _SIX_PERCENT, date(2026, 3, 1), date(2026, 1, 1)),
        ],
        ids=[
            "zero-balance", "a-cent-negative-balance", "negative-balance",
            "zero-rate", "negative-rate", "empty-window", "inverted-window",
        ],
    )
    def test_a_non_positive_input_accrues_nothing(
        self, balance, annual_rate, start, end,
    ):
        """Each arm is ``0``, and the installment charge is ``0.00``, cents and all.

        The negative-balance and inverted-window rows are the ones arithmetic
        alone would get wrong: ``-5,000 x 0.005`` is ``-$25.00``, and an
        inverted window's month count is negative.
        """
        assert accrued_interest(
            balance, annual_rate, start, end, _MONTHLY,
        ) == Decimal("0")
        assert str(installment_interest(
            balance, annual_rate, start, end, _MONTHLY,
        )) == "0.00"


class TestEveryConventionHasARule:
    """The dispatch is total over the enum, and nothing else is a convention."""

    @pytest.mark.parametrize("convention", list(InterestAccrualConventionEnum))
    def test_every_member_prices_a_window(self, convention):
        """A member added to the list without its rule fails here, not in a walk."""
        interest = accrued_interest(
            _TEN_THOUSAND, _SIX_PERCENT,
            date(2026, 1, 1), date(2026, 2, 1), convention,
        )
        assert isinstance(interest, Decimal)

    @pytest.mark.parametrize(
        "not_a_member",
        [1, None, "monthly_1_12"],
        ids=["a-stored-id", "none", "the-members-name"],
    )
    def test_a_value_that_is_not_a_member_is_refused(self, not_a_member):
        """The stored id, ``None`` and the name string are each refused.

        These are the three a caller could hand over by mistake: the column's
        id, the cache's answer for an unknown id, and the row's name.
        """
        with pytest.raises(KeyError):
            accrued_interest(
                _TEN_THOUSAND, _SIX_PERCENT,
                date(2026, 1, 1), date(2026, 2, 1), not_a_member,
            )

    def test_the_refusal_precedes_the_total_arms(self):
        """A zero balance with a non-member is still refused, never a silent ``0``.

        The savings producer's unrecognised-frequency arm returns ``0``; this
        is the shape that refuses it.
        """
        with pytest.raises(KeyError):
            accrued_interest(
                Decimal("0.00"), _SIX_PERCENT,
                date(2026, 1, 1), date(2026, 2, 1), None,
            )


class TestTheInstallmentChargeRoundsOnce:
    """:func:`installment_interest` is the window's figure rounded once, half up."""

    def test_one_month_rounds_to_its_cent(self):
        """$1,000.00 at 5%, one month: 4.1666... rounds to $4.17."""
        assert installment_interest(
            _ONE_THOUSAND, _FIVE_PERCENT,
            date(2026, 1, 15), date(2026, 2, 15), _MONTHLY,
        ) == Decimal("4.17")

    def test_a_two_month_window_rounds_once_not_per_month(self):
        """$1,000.00 at 5%, two months: 8.333... is $8.33, not $4.17 x 2 = $8.34."""
        interest = installment_interest(
            _ONE_THOUSAND, _FIVE_PERCENT,
            date(2026, 1, 15), date(2026, 3, 15), _MONTHLY,
        )
        assert interest == Decimal("8.33")
        assert interest != Decimal("4.17") * 2

    def test_a_half_cent_rounds_up(self):
        """$1.00 at 6%, one month: exactly $0.005, which is $0.01 half up.

        Banker's rounding (Python's default) would give $0.00.
        """
        assert installment_interest(
            Decimal("1.00"), _SIX_PERCENT,
            date(2026, 1, 1), date(2026, 2, 1), _MONTHLY,
        ) == Decimal("0.01")


class TestOneInstallmentIsTheChargeEveryLoanHasBorne:
    """A month's window, rounded, is byte-identical to ``accrue_monthly_interest``."""

    # Measured 2026-09-02 over 500,000 randomised draws: the ONE input where
    # the two associations part.  b*(r/12) -> 565.36;  (b*r)/12 -> 565.37.
    _DIVERGENT_BALANCE = Decimal("271375.20")
    _DIVERGENT_RATE = Decimal("0.025")

    def test_the_pinned_case_separates_the_two_associations(self):
        """On the pinned input the charge is the canonical $565.36, not $565.37.

        The premise is asserted rather than assumed -- the other association
        really does give $565.37 here -- so a producer that swapped the
        brackets would fail this test rather than pass the sweep below on
        inputs where the two agree.
        """
        other_association = round_money(
            self._DIVERGENT_BALANCE * self._DIVERGENT_RATE / MONTHS_PER_YEAR,
        )
        assert other_association == Decimal("565.37")
        assert _retired_spelling(
            self._DIVERGENT_BALANCE, self._DIVERGENT_RATE,
        ) == Decimal("565.36")
        start, end = accrual_window(date(2026, 3, 15), 15)
        assert installment_interest(
            self._DIVERGENT_BALANCE, self._DIVERGENT_RATE, start, end, _MONTHLY,
        ) == Decimal("565.36")

    def test_seeded_draws_agree_with_the_retired_spelling_to_the_byte(self):
        """50,000 seeded draws over the stored domains, each window an installment's.

        Balances span ``Numeric(12, 2)`` (a cent to $9,999,999,999.99) and
        rates ``Numeric(7, 5)`` (0.00001 to 1); each window is
        :func:`accrual_window` of a random due day in a random month, so the
        sweep also grades that every such window is one month.  Compared as
        strings, so a figure equal in value but carrying another exponent
        would fail.  A sweep is not proof of agreement -- the pinned case is
        what tells the associations apart -- but it is the evidence that one
        month moves nothing.
        """
        rng = random.Random(20261110)
        first_ordinal = month_ordinal(date(2000, 1, 1))
        for _ in range(50_000):
            # scaleb keeps the stored column's exponent (cents, five places).
            balance = Decimal(rng.randint(1, 999_999_999_999)).scaleb(-2)
            annual_rate = Decimal(rng.randint(1, 100_000)).scaleb(-5)
            payment_day = rng.randint(1, 31)
            due = clamped_day(first_ordinal + rng.randint(0, 600), payment_day)
            start, end = accrual_window(due, payment_day)
            assert str(installment_interest(
                balance, annual_rate, start, end, _MONTHLY,
            )) == str(_retired_spelling(balance, annual_rate)), (
                balance, annual_rate, due, payment_day,
            )


class TestTheWindowSpansExactlyOneMonth:
    """:func:`accrual_window`: the month an installment's interest is in arrears for."""

    def test_it_opens_on_the_previous_months_due_day_and_ends_on_the_due_date(self):
        """Due Mar 15 on the 15th: ``[Feb 15, Mar 15)``."""
        assert accrual_window(date(2026, 3, 15), 15) == (
            date(2026, 2, 15), date(2026, 3, 15),
        )

    def test_a_january_installment_opens_in_december(self):
        """Due Jan 5, 2027 on the 5th: ``[Dec 5, 2026, Jan 5, 2027)``."""
        assert accrual_window(date(2027, 1, 5), 5) == (
            date(2026, 12, 5), date(2027, 1, 5),
        )

    @pytest.mark.parametrize(
        ("due", "payment_day", "start"),
        [
            (date(2026, 2, 28), 31, date(2026, 1, 31)),  # clamped due, meant start
            (date(2026, 3, 31), 31, date(2026, 2, 28)),  # meant due, clamped start
            (date(2028, 3, 30), 30, date(2028, 2, 29)),  # a leap February
            (date(2026, 3, 29), 29, date(2026, 2, 28)),  # the 29th, no leap day
        ],
        ids=["feb-on-the-31st", "mar-on-the-31st", "leap-feb", "the-29th"],
    )
    def test_the_start_is_read_from_the_meant_day(self, due, payment_day, start):
        """A loan due the 31st opens February's installment on Jan 31, not Jan 28.

        Reading the start from *due*'s own day would take a clamped Feb 28 back
        to Jan 28, overlapping January's window ``[Dec 31, Jan 31)`` by three
        days that a convention counting days would charge twice.
        """
        assert accrual_window(due, payment_day) == (start, due)

    def test_every_window_spans_one_month_for_every_due_day(self):
        """All 31 due days over four years: each window is one month by ordinal.

        This is the property that makes the monthly convention charge exactly
        one month per installment, whatever the clamp did.
        """
        for payment_day in range(1, 32):
            for ordinal in range(
                month_ordinal(date(2026, 1, 1)), month_ordinal(date(2030, 1, 1)),
            ):
                due = clamped_day(ordinal, payment_day)
                start, end = accrual_window(due, payment_day)
                assert start < end == due
                assert month_ordinal(end) - month_ordinal(start) == 1, (
                    due, payment_day,
                )

    @pytest.mark.parametrize("payment_day", [1, 15, 28, 29, 30, 31])
    def test_a_loans_installment_windows_abut(self, payment_day):
        """Each installment's window opens on the installment before it.

        Over a loan's installments from origination (Nov 20, 2025) through
        2028, so the windows tile the contract with no gap and no overlap --
        no day is charged twice or skipped by a convention that counts days.
        """
        dues = installment_dates(date(2025, 11, 20), payment_day, date(2028, 12, 31))
        for previous, due in zip(dues, dues[1:]):
            assert accrual_window(due, payment_day)[0] == previous

    def test_a_window_opened_in_the_due_dates_own_month_would_charge_nothing(self):
        """The trap the helper exists to close: a projection's first row.

        A forward projection starting Mar 10 on a loan due the 20th has its
        first row on Mar 20.  Its window from the starting day, ``[Mar 10,
        Mar 20)``, spans no month and charges $0.00; its installment's window,
        ``[Feb 20, Mar 20)``, charges the month: $50.00 on $10,000.00 at 6%.
        """
        due = date(2026, 3, 20)
        assert installment_interest(
            _TEN_THOUSAND, _SIX_PERCENT, date(2026, 3, 10), due, _MONTHLY,
        ) == Decimal("0.00")
        start, end = accrual_window(due, 20)
        assert installment_interest(
            _TEN_THOUSAND, _SIX_PERCENT, start, end, _MONTHLY,
        ) == Decimal("50.00")
