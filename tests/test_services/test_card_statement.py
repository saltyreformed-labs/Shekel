"""Unit tests for the card statement's pure derivations.

Pure-function tests (no database) for :mod:`app.services.card_statement`
(plan step **credit_card:CC-3**; since plan#2331, developer ruling
**plan#2333**, every function takes the card's dated terms SERIES).  Every
monetary expectation is hand-computed with the arithmetic shown in a comment.
What is pinned:

* the walk (:func:`~app.services.card_statement.terms_in_effect`): before, at,
  between and after two versions -- the earlier governs up to the day before
  the later, the later from its own day -- and before the EARLIEST version the
  earliest governs (the series holds flat backwards), while an empty series
  is ``None``; in any input order;
* the cycle is CLOSED-OPEN: a day at the close belongs to the next cycle, and
  the statement's valuation date is the day before the close;
* every nominal day clamps through the ONE clamp and never decays: a close
  day of 31 is Jan 31, Feb 28 (29 in a leap year), Mar 31, Apr 30;
* the due date is the first due day strictly AFTER the close (**R-CC26**), so
  a due day later in the month than the close is the SAME month's;
* the sign is NOT pinned here: R-CC29's one flip left this module at plan
  step credit_card:CC-5-5a and its three tests left this file with it at
  CC-5-5b (``tests/test_services/test_liability_sign.py``, beside
  :func:`app.services.liability_sign.owed`);
* the minimum is ``max(floor, round_money(pct x balance))`` clamped to the
  balance and floored at zero, rounded HALF_UP at that one boundary;
* grace is kept when the prior statement was paid in full by its due date,
  and lost one cent short;
* with one version every figure is the one close day's, due day's and
  minimum rule's (the cases above, each on a one-version series); with two or
  more, a day is a close when it is the clamped close day of the version in
  force on it: plan#2333's changeover cycle, a changeover month holding two
  closes, a change taking effect ON an old close date, a long transitional
  cycle, a version whose days hold no close at all, the earliest version's
  close day governing before its date, and a scan of every day against the
  definition itself; the due date and the minimum read the version in force
  at the CLOSE, not a later one; a version dated ON a close date governs the
  statement closing that day (its due date and minimum, and a new close day
  entered as its first close closes then: the issuer's convention); and an
  empty series is refused in words.

The done-when of plan#2331 -- changing a card's terms leaves every statement
and reward before the change as it was, read as every statement closing before
the change's date and every reward on a day before it -- is graded on saved
versions in ``tests/test_services/test_card_terms.py``.
"""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.card_statement import (
    CycleWindow,
    cycle_containing,
    due_date_for,
    grace_kept,
    minimum_payment,
    statement_sequence,
    terms_in_effect,
)
from app.utils.dates import clamped_day, month_ordinal

#: The fixture card's terms: closes on the 20th, due on the 15th.
CLOSE_DAY = 20
DUE_DAY = 15


def _version(effective_date, close_day=CLOSE_DAY, due_day=DUE_DAY,
             percent="0.0250", floor="25.00"):
    """A stand-in terms version: the attributes the statement reads."""
    return SimpleNamespace(
        effective_date=effective_date,
        statement_close_day=close_day,
        payment_due_day=due_day,
        min_payment_percent=Decimal(percent),
        min_payment_floor=Decimal(floor),
    )


def _one(close_day=CLOSE_DAY, due_day=DUE_DAY, percent="0.0250", floor="25.00"):
    """A one-version series: every day, before and after its date, reads it."""
    return [_version(date(2026, 1, 1), close_day, due_day, percent, floor)]


#: The fixture card as a series.
SERIES = _one()


def _closing_in(series, year, month):
    """The one statement of *series* closing in *year*-*month* (one version: one a month)."""
    first = date(year, month, 1)
    last = clamped_day(month_ordinal(first), 31)
    (window,) = statement_sequence(series, first, last)
    return window


class TestTheWalk:
    """Which version governs a day (moved here from ``test_card_terms`` with the walk)."""

    JAN = _version(date(2026, 1, 1))
    MAR = _version(date(2026, 3, 1))

    def test_the_earlier_version_governs_up_to_the_day_before_the_later(self):
        """Jan 1 through Feb 28 read January's; Mar 1 is March's from its own day."""
        versions = [self.JAN, self.MAR]
        assert terms_in_effect(versions, date(2026, 1, 1)) is self.JAN
        assert terms_in_effect(versions, date(2026, 2, 10)) is self.JAN
        assert terms_in_effect(versions, date(2026, 2, 28)) is self.JAN
        assert terms_in_effect(versions, date(2026, 3, 1)) is self.MAR
        assert terms_in_effect(versions, date(2027, 6, 1)) is self.MAR

    def test_before_the_earliest_version_the_earliest_governs(self):
        """The series holds flat backwards: Dec 31 and years before read January's.

        So the date the first terms are saved never makes a statement before
        it vanish -- the cycle open on that day began at the close before it.
        """
        versions = [self.JAN, self.MAR]
        assert terms_in_effect(versions, date(2025, 12, 31)) is self.JAN
        assert terms_in_effect(versions, date(2020, 1, 1)) is self.JAN

    def test_the_input_order_does_not_matter(self):
        """Newest-first input answers exactly as earliest-first does."""
        versions = [self.MAR, self.JAN]
        assert terms_in_effect(versions, date(2025, 12, 31)) is self.JAN
        assert terms_in_effect(versions, date(2026, 2, 28)) is self.JAN
        assert terms_in_effect(versions, date(2026, 3, 1)) is self.MAR

    def test_an_empty_series_is_none(self):
        """No version: the dormant card, and the one ``None`` the walk returns."""
        assert terms_in_effect([], date(2026, 3, 1)) is None


class TestTheCycleWindowIsClosedOpen:
    """``[opens, closes)``: the close day starts the NEXT cycle."""

    def test_the_cycle_closing_in_october_opens_at_septembers_close(self):
        """Close day 20: the October statement is Sep 20 up to Oct 20."""
        assert _closing_in(SERIES, 2026, 10) == CycleWindow(
            opens=date(2026, 9, 20), closes=date(2026, 10, 20),
        )

    def test_the_opening_day_is_inside_and_the_close_day_is_not(self):
        """A movement dated at the close belongs to the next statement."""
        window = _closing_in(SERIES, 2026, 10)
        assert window.contains(date(2026, 9, 20)) is True
        assert window.contains(date(2026, 10, 19)) is True
        assert window.contains(date(2026, 10, 20)) is False
        assert window.contains(date(2026, 9, 19)) is False

    def test_the_valuation_date_is_the_day_before_the_close(self):
        """The close instant is the END of Oct 19: the seam values a day at
        its end and the cycle excludes its close date."""
        assert _closing_in(SERIES, 2026, 10).valuation_date == date(
            2026, 10, 19,
        )

    def test_a_day_before_the_close_is_in_the_cycle_closing_that_month(self):
        """Oct 19 belongs to the statement closing Oct 20."""
        assert cycle_containing(SERIES, date(2026, 10, 19)) == CycleWindow(
            opens=date(2026, 9, 20), closes=date(2026, 10, 20),
        )

    def test_the_close_day_itself_is_in_the_next_cycle(self):
        """Oct 20 belongs to the statement closing Nov 20."""
        assert cycle_containing(SERIES, date(2026, 10, 20)) == CycleWindow(
            opens=date(2026, 10, 20), closes=date(2026, 11, 20),
        )

    @pytest.mark.parametrize("close_day", range(1, 32))
    def test_every_day_placed_is_contained_by_its_cycle(self, close_day):
        """The placement and the membership test agree on every day of two
        years for every nominal close day -- a leap February (2028) and the
        year boundary included, so the clamped months are pinned facts."""
        series = _one(close_day)
        day = date(2027, 1, 1)
        while day <= date(2028, 12, 31):
            assert cycle_containing(series, day).contains(day) is True
            day = date.fromordinal(day.toordinal() + 1)

    def test_the_year_boundary(self):
        """Dec 25, 2026 and Jan 5, 2027 share the statement closing Jan 20."""
        expected = CycleWindow(opens=date(2026, 12, 20), closes=date(2027, 1, 20))
        assert cycle_containing(SERIES, date(2026, 12, 25)) == expected
        assert cycle_containing(SERIES, date(2027, 1, 5)) == expected


class TestTheCloseDayClampsAndNeverDecays:
    """A nominal 31 is each month's last day, and April's 30 does not
    become May's (rulings R-PC79 / R-R3, through ``clamped_day``)."""

    @pytest.mark.parametrize(
        ("closing_month", "expected"),
        [
            (date(2026, 2, 1), CycleWindow(date(2026, 1, 31), date(2026, 2, 28))),
            (date(2026, 3, 1), CycleWindow(date(2026, 2, 28), date(2026, 3, 31))),
            (date(2026, 5, 1), CycleWindow(date(2026, 4, 30), date(2026, 5, 31))),
            (date(2028, 2, 1), CycleWindow(date(2028, 1, 31), date(2028, 2, 29))),
            (date(2028, 3, 1), CycleWindow(date(2028, 2, 29), date(2028, 3, 31))),
        ],
        ids=["feb", "mar-after-feb", "may-after-apr", "leap-feb", "mar-after-leap"],
    )
    def test_a_close_day_of_31_is_each_months_last_day(
        self, closing_month, expected,
    ):
        """Feb 28 (29 in 2028), then Mar 31 again: no decay."""
        assert _closing_in(
            _one(31), closing_month.year, closing_month.month,
        ) == expected

    def test_february_28_is_the_next_cycles_first_day_for_a_31_close(self):
        """Feb 27 is inside the cycle closing Feb 28; Feb 28 opens the next."""
        assert cycle_containing(_one(31), date(2026, 2, 27)) == CycleWindow(
            date(2026, 1, 31), date(2026, 2, 28),
        )
        assert cycle_containing(_one(31), date(2026, 2, 28)) == CycleWindow(
            date(2026, 2, 28), date(2026, 3, 31),
        )

    def test_a_close_day_of_1(self):
        """The March statement is Feb 1 up to Mar 1."""
        assert _closing_in(_one(1), 2026, 3) == CycleWindow(
            date(2026, 2, 1), date(2026, 3, 1),
        )


class TestTheStatementSequence:
    """Every cycle whose close date lies in the inclusive span, oldest first."""

    def test_three_closes_inside_a_quarter(self):
        """Sep 1 - Nov 30 holds the Sep 20, Oct 20 and Nov 20 closes."""
        assert statement_sequence(
            SERIES, date(2026, 9, 1), date(2026, 11, 30),
        ) == [
            CycleWindow(date(2026, 8, 20), date(2026, 9, 20)),
            CycleWindow(date(2026, 9, 20), date(2026, 10, 20)),
            CycleWindow(date(2026, 10, 20), date(2026, 11, 20)),
        ]

    def test_both_ends_are_inclusive(self):
        """A span from one close date to another includes both."""
        closes = [
            w.closes for w in statement_sequence(
                SERIES, date(2026, 9, 20), date(2026, 11, 20),
            )
        ]
        assert closes == [date(2026, 9, 20), date(2026, 10, 20), date(2026, 11, 20)]

    def test_a_close_the_day_before_the_span_is_excluded(self):
        """Sep 21 - Nov 20: the Sep 20 close is out, Oct and Nov in."""
        closes = [
            w.closes for w in statement_sequence(
                SERIES, date(2026, 9, 21), date(2026, 11, 20),
            )
        ]
        assert closes == [date(2026, 10, 20), date(2026, 11, 20)]

    def test_a_close_the_day_after_the_span_is_excluded(self):
        """Sep 20 - Nov 19: the Nov 20 close is out."""
        closes = [
            w.closes for w in statement_sequence(
                SERIES, date(2026, 9, 20), date(2026, 11, 19),
            )
        ]
        assert closes == [date(2026, 9, 20), date(2026, 10, 20)]

    def test_a_span_holding_no_close_is_empty(self):
        """Sep 21 - Oct 19 straddles no close date."""
        assert statement_sequence(
            SERIES, date(2026, 9, 21), date(2026, 10, 19),
        ) == []

    def test_a_reversed_span_is_empty(self):
        """``last`` before ``first`` holds nothing."""
        assert statement_sequence(
            SERIES, date(2026, 11, 30), date(2026, 9, 1),
        ) == []

    def test_a_31_close_walks_each_months_last_day(self):
        """Jan 31, Feb 28, Mar 31, Apr 30: the clamp per month, no decay."""
        closes = [
            w.closes for w in statement_sequence(
                _one(31), date(2026, 1, 1), date(2026, 4, 30),
            )
        ]
        assert closes == [
            date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31),
            date(2026, 4, 30),
        ]


class TestTheDueDateIsTheFirstDueDayAfterTheClose:
    """Ruling R-CC26: the same month when the due day is still ahead of the
    close, the next month otherwise."""

    def test_a_due_day_earlier_in_the_month_is_next_months(self):
        """Closes Sep 20, due on the 15th: Oct 15 (25 days)."""
        assert due_date_for(_one(20, 15), date(2026, 9, 20)) == date(2026, 10, 15)

    def test_a_due_day_later_in_the_month_is_this_months(self):
        """Closes Sep 5, due on the 28th: Sep 28 (23 days), NOT Oct 28."""
        assert due_date_for(_one(5, 28), date(2026, 9, 5)) == date(2026, 9, 28)

    def test_a_due_day_equal_to_the_close_day_is_next_months(self):
        """Strictly after: closes Sep 20, due on the 20th: Oct 20."""
        assert due_date_for(_one(20, 20), date(2026, 9, 20)) == date(2026, 10, 20)

    def test_the_year_boundary(self):
        """Closes Dec 20, 2026, due on the 15th: Jan 15, 2027."""
        assert due_date_for(_one(20, 15), date(2026, 12, 20)) == date(2027, 1, 15)

    def test_a_clamped_due_day_equal_to_a_clamped_close_is_next_months(self):
        """Close day 31 and due day 31 in February both clamp to Feb 28, so
        the due date is Mar 31."""
        assert due_date_for(_one(31, 31), date(2026, 2, 28)) == date(2026, 3, 31)

    def test_the_due_day_clamps_in_its_own_month(self):
        """Closes Jan 31, due on the 30th: Jan 30 is past, so Feb 28."""
        assert due_date_for(_one(31, 30), date(2026, 1, 31)) == date(2026, 2, 28)

    def test_a_due_day_one_day_after_the_close(self):
        """Closes Jan 30, due on the 31st: Jan 31, as the terms state."""
        assert due_date_for(_one(30, 31), date(2026, 1, 30)) == date(2026, 1, 31)


class TestTheMinimumPayment:
    """``max(floor, round_money(pct x balance))`` clamped to the balance and
    floored at zero (design 3.4; R-CC29's numbers)."""

    #: 2.5% with a $25.00 floor, closing on the 20th; Oct 20, 2026 is a close.
    SERIES = _one(percent="0.0250", floor="25.00")
    CLOSES = date(2026, 10, 20)

    def test_the_percentage_wins_above_the_floor(self):
        """2.5% x 1,234.56 = 30.864 -> 30.86; max(25.00, 30.86) = 30.86."""
        assert minimum_payment(
            self.SERIES, self.CLOSES, Decimal("1234.56"),
        ) == Decimal("30.86")

    def test_the_floor_wins_below_it(self):
        """2.5% x 400.00 = 10.00; max(25.00, 10.00) = 25.00."""
        assert minimum_payment(
            self.SERIES, self.CLOSES, Decimal("400.00"),
        ) == Decimal("25.00")

    def test_a_balance_below_the_floor_is_due_whole(self):
        """2.5% x 10.00 = 0.25; max(25.00, 0.25) = 25.00; clamped to 10.00."""
        assert minimum_payment(
            self.SERIES, self.CLOSES, Decimal("10.00"),
        ) == Decimal("10.00")

    def test_a_zero_balance_owes_nothing(self):
        """max(25.00, 0.00) = 25.00, clamped to max(0.00, 0) = 0.00."""
        assert minimum_payment(
            self.SERIES, self.CLOSES, Decimal("0.00"),
        ) == Decimal("0.00")

    def test_a_credit_balance_owes_nothing(self):
        """2.5% x -50.00 = -1.25; max(25.00, -1.25) = 25.00; clamped to
        max(-50.00, 0) = 0.00: no minimum on money the issuer owes."""
        assert minimum_payment(
            self.SERIES, self.CLOSES, Decimal("-50.00"),
        ) == Decimal("0.00")

    @pytest.mark.parametrize(
        ("balance", "expected"),
        [
            # 2.5% x 999.60 = 24.99 < 25.00 floor -> 25.00
            (Decimal("999.60"), Decimal("25.00")),
            # 2.5% x 1000.00 = 25.00 = floor -> 25.00
            (Decimal("1000.00"), Decimal("25.00")),
            # 2.5% x 1000.40 = 25.01 > floor -> 25.01
            (Decimal("1000.40"), Decimal("25.01")),
        ],
        ids=["one-cent-under", "at-the-floor", "one-cent-over"],
    )
    def test_the_floor_crossover(self, balance, expected):
        """The cent where the percentage overtakes the floor."""
        assert minimum_payment(self.SERIES, self.CLOSES, balance) == expected

    def test_the_percentage_rounds_half_up_at_the_boundary(self):
        """2.5% x 1,234.60 = 30.865 exactly: HALF_UP gives 30.87 (bankers'
        rounding would give 30.86)."""
        assert minimum_payment(
            self.SERIES, self.CLOSES, Decimal("1234.60"),
        ) == Decimal("30.87")

    def test_a_whole_balance_rule(self):
        """100% with no floor: the minimum IS the balance."""
        assert minimum_payment(
            _one(percent="1.0000", floor="0.00"), self.CLOSES, Decimal("1234.56"),
        ) == Decimal("1234.56")

    def test_no_rule_at_all_owes_nothing(self):
        """0% and a 0.00 floor (both admitted by the CHECKs): 0.00."""
        assert minimum_payment(
            _one(percent="0.0000", floor="0.00"), self.CLOSES, Decimal("1234.56"),
        ) == Decimal("0.00")

    def test_the_result_is_cent_quantized_on_every_arm(self):
        """Percentage, floor, clamp-to-balance, zero AND credit all answer in
        cents -- the credit arm returned the bare ``Decimal("0")`` under
        review, equal to ``0.00`` numerically and one place short."""
        for balance in (
            Decimal("1234.56"), Decimal("400.00"), Decimal("10.00"),
            Decimal("0.00"), Decimal("-50.00"),
        ):
            result = minimum_payment(self.SERIES, self.CLOSES, balance)
            assert result.as_tuple().exponent == -2, (balance, result)


class TestGraceKept:
    """The prior statement paid in full by its due date keeps grace (R-CC2)."""

    PRIOR = Decimal("1234.56")

    def test_paid_in_full_keeps_grace(self):
        """1,234.56 credited against 1,234.56 owed."""
        assert grace_kept(self.PRIOR, Decimal("1234.56")) is True

    def test_one_cent_short_loses_grace(self):
        """1,234.55 credited against 1,234.56 owed: the control fires."""
        assert grace_kept(self.PRIOR, Decimal("1234.55")) is False

    def test_overpaid_keeps_grace(self):
        """1,300.00 credited against 1,234.56 owed."""
        assert grace_kept(self.PRIOR, Decimal("1300.00")) is True

    def test_nothing_paid_loses_grace(self):
        """0.00 credited against a balance."""
        assert grace_kept(self.PRIOR, Decimal("0.00")) is False

    def test_nothing_owed_keeps_grace_with_nothing_paid(self):
        """A zero statement has nothing to pay by its due date."""
        assert grace_kept(Decimal("0.00"), Decimal("0.00")) is True

    def test_a_credit_statement_keeps_grace(self):
        """A statement the issuer owes on (-50.00) has nothing to pay."""
        assert grace_kept(Decimal("-50.00"), Decimal("0.00")) is True


#: plan#2333's example: close 20, due 15, 2% with a $25.00 floor through
#: February; from Mar 1 the close moves to the 5th, the due day to the 30th,
#: and the minimum to 3% with a $35.00 floor (the floor and percent changed
#: here so the minimum's version can be told apart).
PLAN_2333 = [
    _version(date(2026, 1, 1), close_day=20, due_day=15, percent="0.0200", floor="25.00"),
    _version(date(2026, 3, 1), close_day=5, due_day=30, percent="0.0300", floor="35.00"),
]


def _window(opens, closes):
    """A :class:`CycleWindow` from two ``(month, day)`` pairs in 2026."""
    return CycleWindow(date(2026, *opens), date(2026, *closes))


class TestTheCloseFollowsTheVersionInForce:
    """A day is a close when it is the clamped close day of the version in force on it."""

    def test_plan_2333s_changeover_cycle(self):
        """Feb 20 closes under the 20th; the next close is Mar 5, under the 5th.

        The transitional cycle is ``[Feb 20, Mar 5)``, 13 days; Mar 20 is no
        longer a close, so the statement after runs ``[Mar 5, Apr 5)``.
        """
        assert cycle_containing(PLAN_2333, date(2026, 2, 10)) == _window((1, 20), (2, 20))
        assert cycle_containing(PLAN_2333, date(2026, 2, 25)) == _window((2, 20), (3, 5))
        assert cycle_containing(PLAN_2333, date(2026, 3, 5)) == _window((3, 5), (4, 5))
        assert cycle_containing(PLAN_2333, date(2026, 3, 20)) == _window((3, 5), (4, 5))
        assert statement_sequence(PLAN_2333, date(2026, 1, 1), date(2026, 4, 30)) == [
            CycleWindow(date(2025, 12, 20), date(2026, 1, 20)),
            _window((1, 20), (2, 20)),
            _window((2, 20), (3, 5)),
            _window((3, 5), (4, 5)),
        ]

    def test_a_changeover_month_holds_two_closes(self):
        """Close 5 through Mar 9, close 25 from Mar 10: March closes on the 5th AND the 25th."""
        series = [_version(date(2026, 1, 1), close_day=5),
                  _version(date(2026, 3, 10), close_day=25)]
        assert statement_sequence(series, date(2026, 3, 1), date(2026, 3, 31)) == [
            _window((2, 5), (3, 5)),
            _window((3, 5), (3, 25)),
        ]

    def test_a_change_taking_effect_on_an_old_close_date_moves_that_close(self):
        """Close 5 from Feb 20: Feb 20 is the 5th-closing version's day, so not a close.

        The cycle open on Jan 20 runs to Mar 5, 44 days, and Feb 20 is inside it.
        The boundary is the issuer's (the module docstring): a change effective
        Feb 20 governs the billing cycle containing Feb 20, the one the issuer
        closes on or after it, so the cycle the old terms would have closed on
        Feb 20 is the changeover cycle instead.
        """
        series = [_version(date(2026, 1, 1), close_day=20),
                  _version(date(2026, 2, 20), close_day=5)]
        assert cycle_containing(series, date(2026, 2, 19)) == _window((1, 20), (3, 5))
        assert cycle_containing(series, date(2026, 2, 20)) == _window((1, 20), (3, 5))

    def test_a_long_transitional_cycle(self):
        """Close 5 from Mar 10: Mar 5 is still the 20th's, Mar 20 already the 5th's.

        Neither is a close, so the cycle open on Feb 20 runs to Apr 5: 44 days.
        """
        series = [_version(date(2026, 1, 1), close_day=20),
                  _version(date(2026, 3, 10), close_day=5)]
        assert cycle_containing(series, date(2026, 3, 1)) == _window((2, 20), (4, 5))
        assert cycle_containing(series, date(2026, 3, 25)) == _window((2, 20), (4, 5))

    def test_a_version_whose_days_hold_no_close(self):
        """Close 10 from Feb 21 to Mar 4 holds no 10th: both walks cross its span.

        Feb 20 (the 20th's) and Mar 20 (the 20th's again, from Mar 5) are the
        closes either side.  From Mar 15 the backward walk passes the 10th's
        span to reach Feb 20; from Feb 25 the forward walk passes it to Mar 20.
        """
        series = [_version(date(2026, 1, 1), close_day=20),
                  _version(date(2026, 2, 21), close_day=10),
                  _version(date(2026, 3, 5), close_day=20)]
        assert cycle_containing(series, date(2026, 2, 25)) == _window((2, 20), (3, 20))
        assert cycle_containing(series, date(2026, 3, 15)) == _window((2, 20), (3, 20))

    def test_before_the_earliest_version_its_close_day_governs(self):
        """Terms first dated Mar 1, 2026 still cut Nov 20, Dec 20, Jan 20 and Feb 20."""
        series = [_version(date(2026, 3, 1), close_day=20),
                  _version(date(2026, 6, 1), close_day=5)]
        assert cycle_containing(series, date(2026, 1, 10)) == CycleWindow(
            date(2025, 12, 20), date(2026, 1, 20),
        )
        assert [
            w.closes for w in statement_sequence(
                series, date(2025, 11, 1), date(2026, 2, 28),
            )
        ] == [date(2025, 11, 20), date(2025, 12, 20), date(2026, 1, 20),
              date(2026, 2, 20)]

    def test_the_input_order_does_not_matter(self):
        """plan#2333's series newest first cuts the same cycles."""
        reversed_series = PLAN_2333[::-1]
        assert cycle_containing(reversed_series, date(2026, 2, 25)) == _window((2, 20), (3, 5))
        assert statement_sequence(
            reversed_series, date(2026, 1, 1), date(2026, 4, 30),
        ) == statement_sequence(PLAN_2333, date(2026, 1, 1), date(2026, 4, 30))


def _is_close(series, day):
    """The DEFINITION, by brute force: *day* is the clamped close day of the version in force on it."""
    close_day = terms_in_effect(series, day).statement_close_day
    return clamped_day(month_ordinal(day), close_day) == day


#: Series the scan grades, each a shape the close walks jump across.
_SCANNED = {
    "one-31": [_version(date(2026, 6, 1), close_day=31)],
    "plan-2333": PLAN_2333,
    "two-closes-a-month": [_version(date(2026, 1, 1), close_day=5),
                           _version(date(2026, 3, 10), close_day=25)],
    "on-an-old-close": [_version(date(2026, 1, 1), close_day=20),
                        _version(date(2026, 2, 20), close_day=5)],
    "no-close-span": [_version(date(2026, 1, 1), close_day=20),
                      _version(date(2026, 2, 21), close_day=10),
                      _version(date(2026, 3, 5), close_day=20)],
    "31-then-28-then-1": [_version(date(2026, 1, 15), close_day=31),
                          _version(date(2027, 2, 27), close_day=28),
                          _version(date(2027, 3, 2), close_day=1)],
}


@pytest.mark.parametrize("name", sorted(_SCANNED))
def test_every_day_agrees_with_the_definition(name):
    """Two years of days, each placed by the walks and by a scan of the definition.

    The scan reads every day from late 2025 to early 2028 and keeps the ones
    :func:`_is_close` names; each day's cycle must open at the last of them
    on or before it and close at the first after it, and the statement
    sequence over the two years must be exactly the scanned closes.  The
    walks jump span to span on the monthly grid; the scan steps day by day,
    so it shares none of their arithmetic but the clamp and the walk.
    """
    series = _SCANNED[name]
    scan_first, scan_last = date(2025, 11, 1), date(2028, 3, 31)
    closes = []
    day = scan_first
    while day <= scan_last:
        if _is_close(series, day):
            closes.append(day)
        day += timedelta(days=1)
    first, last = date(2026, 1, 1), date(2027, 12, 31)
    day = first
    while day <= last:
        expected = CycleWindow(
            opens=max(c for c in closes if c <= day),
            closes=min(c for c in closes if c > day),
        )
        assert cycle_containing(series, day) == expected, (name, day)
        day += timedelta(days=1)
    in_span = [c for c in closes if first <= c <= last]
    assert [w.closes for w in statement_sequence(series, first, last)] == in_span
    assert [w.opens for w in statement_sequence(series, first, last)] == [
        max(c for c in closes if c < close) for close in in_span
    ]


class TestTheStatementReadsTheVersionAtItsClose:
    """The due date and the minimum are the CLOSE's version's, not a later one's."""

    def test_the_due_date_is_the_closes_versions(self):
        """Feb 20 closes under due-the-15th: Mar 15, though due-the-30th governs by then.

        The Mar 5 close is the 30th's: Mar 30.
        """
        assert due_date_for(PLAN_2333, date(2026, 2, 20)) == date(2026, 3, 15)
        assert due_date_for(PLAN_2333, date(2026, 3, 5)) == date(2026, 3, 30)

    @pytest.mark.parametrize(
        ("closes", "balance", "expected"),
        [
            # Feb 20 (2%, $25.00): 2% x 600.00 = 12.00; max(25.00, 12.00) = 25.00
            (date(2026, 2, 20), Decimal("600.00"), Decimal("25.00")),
            # Mar 5 (3%, $35.00): 3% x 600.00 = 18.00; max(35.00, 18.00) = 35.00
            (date(2026, 3, 5), Decimal("600.00"), Decimal("35.00")),
            # Feb 20: 2% x 2,000.00 = 40.00 > 25.00
            (date(2026, 2, 20), Decimal("2000.00"), Decimal("40.00")),
            # Mar 5: 3% x 2,000.00 = 60.00 > 35.00
            (date(2026, 3, 5), Decimal("2000.00"), Decimal("60.00")),
        ],
        ids=["feb-floor", "mar-floor", "feb-percent", "mar-percent"],
    )
    def test_the_minimum_is_the_closes_versions(self, closes, balance, expected):
        """Each close's own percent and floor, on the same balance."""
        assert minimum_payment(PLAN_2333, closes, balance) == expected


class TestAVersionDatedOnACloseDate:
    """A version dated ON a close date governs the statement closing that day.

    The issuer's convention (the module docstring): a change effective on a
    date governs the billing cycle containing it, the one closing on or after
    it.  Read from the day BEFORE the close instead, each case below would
    answer the old terms or a month later.
    """

    def test_it_sets_that_statements_due_date_and_minimum(self):
        """Due the 28th, 3% with a $35.00 floor, from Feb 20: the Feb 20 statement's.

        Close 20 throughout.  The Jan 20 statement is the old terms': due Feb
        15, minimum max(25.00, 2% x 600.00 = 12.00) = $25.00.  The Feb 20
        statement is the new version's: due Feb 28, the first 28th after the
        close, and minimum max(35.00, 3% x 600.00 = 18.00) = $35.00.
        """
        series = [
            _version(date(2026, 1, 1), due_day=15, percent="0.0200", floor="25.00"),
            _version(date(2026, 2, 20), due_day=28, percent="0.0300", floor="35.00"),
        ]
        assert due_date_for(series, date(2026, 1, 20)) == date(2026, 2, 15)
        assert minimum_payment(series, date(2026, 1, 20), Decimal("600.00")) == (
            Decimal("25.00")
        )
        assert due_date_for(series, date(2026, 2, 20)) == date(2026, 2, 28)
        assert minimum_payment(series, date(2026, 2, 20), Decimal("600.00")) == (
            Decimal("35.00")
        )

    def test_a_new_close_day_entered_as_its_first_close_closes_then(self):
        """Close 5 from Mar 5 (a "statements closing on or after Mar 5" notice): Mar 5 closes.

        The cycle open on Feb 20 closes on Mar 5 under the new day, not on
        Apr 5, and the next runs to Apr 5.
        """
        series = [_version(date(2026, 1, 1), close_day=20),
                  _version(date(2026, 3, 5), close_day=5)]
        assert cycle_containing(series, date(2026, 3, 1)) == _window((2, 20), (3, 5))
        assert cycle_containing(series, date(2026, 3, 5)) == _window((3, 5), (4, 5))


class TestADormantCardHasNoStatement:
    """An empty series is refused in words, never an ``AttributeError``."""

    @pytest.mark.parametrize(
        "call",
        [
            lambda: cycle_containing([], date(2026, 3, 1)),
            lambda: statement_sequence([], date(2026, 1, 1), date(2026, 3, 31)),
            lambda: due_date_for([], date(2026, 3, 5)),
            lambda: minimum_payment([], date(2026, 3, 5), Decimal("600.00")),
        ],
        ids=["cycle", "sequence", "due", "minimum"],
    )
    def test_each_reader_refuses_an_empty_series(self, call):
        """The message names what is missing and the gate to take."""
        with pytest.raises(ValueError, match="The card has no terms version"):
            call()
