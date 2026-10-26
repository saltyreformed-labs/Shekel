"""Plan step ``balance:X-bi-6-4c-3``: each side of a transfer keeps its own day.

The pure day function (``transfer_service._side_days``, ruling **R-BAL142**)
and the evidence predicate it asks (:func:`app.services.settle_day.is_evidence`,
the REC-552 study's seam S1).  A side with evidence of its own -- the bank
showed it, a balance was asserted over it, or the owner typed it -- keeps that
day; a side with none BORROWS the other side's, and follows it when that day
is corrected; when neither side has one, both borrow the day Paid was pressed.

Everything here is values in, values out: no database, no clock.  The writer
that feeds the function its inputs, and the doors that state days, are graded
in ``test_each_side_keeps_its_own_day.py``.
"""

from datetime import date

import pytest

from app.enums import SettledDayBasisEnum
from app.services.settle_day import SettleDay, is_evidence
from app.services.transfer_service._side_days import (
    NO_DAYS,
    PairDays,
    SideDay,
    borrowed_day,
    pair_fallback_day,
    resolve_pair_days,
    stated_by_side,
)

OBSERVED = SettledDayBasisEnum.OBSERVED
ASSERTED = SettledDayBasisEnum.ASSERTED
ENTERED = SettledDayBasisEnum.ENTERED
BORROWED = SettledDayBasisEnum.BORROWED

TODAY = date(2026, 9, 30)
EARLY = date(2026, 9, 1)
LATE = date(2026, 9, 3)

CHECKING = 1
SAVINGS = 2


def _day(day, basis):
    """Return a :class:`SettleDay` -- shorthand, so each case reads as its pair."""
    return SettleDay(day=day, basis=basis)


class TestIsEvidence:
    """Only ``borrowed`` is not evidence (seam S1: ONE named predicate)."""

    @pytest.mark.parametrize("basis, expected", [
        (OBSERVED, True),
        (ASSERTED, True),
        (ENTERED, True),
        (BORROWED, False),
    ])
    def test_only_a_borrowed_day_is_not_evidence(self, basis, expected):
        """The three members a door can state are evidence; the derived one is not."""
        assert is_evidence(_day(EARLY, basis)) is expected

    def test_every_member_is_classified_above(self):
        """A fifth member fails here until someone decides which side of the line it is on."""
        assert set(SettledDayBasisEnum) == {OBSERVED, ASSERTED, ENTERED, BORROWED}


class TestSideDay:
    """What a door STATES: a day on one account, known one way."""

    def test_a_statement_of_nothing_is_refused(self):
        """A door stating no day passes no SideDay; wrapping ``None`` is a caller bug."""
        with pytest.raises(ValueError, match="cannot wrap None"):
            SideDay(CHECKING, None)

    def test_a_borrowed_day_cannot_be_stated(self):
        """A borrowed day is DERIVED; no door may hand one in (build note 3)."""
        with pytest.raises(ValueError, match="derived from the other side"):
            SideDay(CHECKING, _day(EARLY, BORROWED))

    @pytest.mark.parametrize("basis", [OBSERVED, ASSERTED, ENTERED])
    def test_evidence_is_stated(self, basis):
        """Each evidence member is a legal statement."""
        assert SideDay(CHECKING, _day(EARLY, basis)).day == _day(EARLY, basis)


class TestStatedBySide:
    """The doors state by ACCOUNT; the writer works by SIDE."""

    def test_nothing_stated_is_no_days(self):
        """An empty statement maps to the pair that states nothing."""
        assert stated_by_side((), CHECKING, SAVINGS) == NO_DAYS

    def test_the_source_account_is_the_expense_side(self):
        """``from`` is the side the money leaves; ``to`` the side it reaches."""
        stated = stated_by_side(
            (
                SideDay(SAVINGS, _day(LATE, ENTERED)),
                SideDay(CHECKING, _day(EARLY, OBSERVED)),
            ),
            CHECKING, SAVINGS,
        )
        assert stated == PairDays(
            expense=_day(EARLY, OBSERVED), income=_day(LATE, ENTERED),
        )

    def test_an_account_on_neither_side_is_refused_by_name(self):
        """A door pairing a transfer with the wrong account is a programming error."""
        with pytest.raises(ValueError, match="account 7, which is neither side"):
            stated_by_side((SideDay(7, _day(EARLY, ENTERED)),), CHECKING, SAVINGS)

    def test_two_days_for_one_side_are_refused(self):
        """A side has one day."""
        with pytest.raises(ValueError, match="Two days were stated for account 1"):
            stated_by_side(
                (
                    SideDay(CHECKING, _day(EARLY, ENTERED)),
                    SideDay(CHECKING, _day(LATE, OBSERVED)),
                ),
                CHECKING, SAVINGS,
            )


class TestBorrowedDay:
    """The ONE derivation of a borrowed day (seam S6)."""

    def test_a_side_borrows_its_lenders_day(self):
        """The other side's own day, whatever its basis, labelled ``borrowed``."""
        assert borrowed_day(_day(EARLY, OBSERVED), TODAY) == _day(EARLY, BORROWED)

    def test_with_no_lender_it_borrows_the_fallback(self):
        """No evidence on either side: the day the pair already shares, or the press."""
        assert borrowed_day(None, TODAY) == _day(TODAY, BORROWED)


class TestPairFallbackDay:
    """The day a pair with no evidence shares: the first recorded, else today."""

    def test_the_first_recorded_day_in_the_given_order_wins(self):
        """The caller orders the legs; the first one holding a day answers."""
        assert pair_fallback_day(
            (None, _day(LATE, BORROWED), _day(EARLY, BORROWED)), TODAY,
        ) == LATE

    def test_with_nothing_recorded_it_is_today(self):
        """A Paid press on a pair that never held a day borrows the press day."""
        assert pair_fallback_day((None, None), TODAY) == TODAY


class TestResolvePairDays:
    """Ruling R-BAL142, case by case: what each side holds once an act lands."""

    def test_a_paid_press_leaves_both_sides_borrowing_the_press_day(self):
        """Nothing stated, nothing held: both borrow today (declared change 1)."""
        assert resolve_pair_days(NO_DAYS, NO_DAYS, TODAY) == PairDays(
            expense=_day(TODAY, BORROWED), income=_day(TODAY, BORROWED),
        )

    def test_a_statement_on_one_side_is_lent_to_the_other(self):
        """The bank showed Checking: Savings borrows Checking's day (declared change 2)."""
        assert resolve_pair_days(
            PairDays(expense=_day(TODAY, BORROWED), income=_day(TODAY, BORROWED)),
            PairDays(expense=_day(EARLY, OBSERVED), income=None),
            TODAY,
        ) == PairDays(expense=_day(EARLY, OBSERVED), income=_day(EARLY, BORROWED))

    def test_the_income_side_can_be_the_lender(self):
        """Nothing makes the source side special: a statement on the far account lends too."""
        assert resolve_pair_days(
            NO_DAYS,
            PairDays(expense=None, income=_day(LATE, ENTERED)),
            TODAY,
        ) == PairDays(expense=_day(LATE, BORROWED), income=_day(LATE, ENTERED))

    def test_a_borrowing_side_follows_its_lenders_correction(self):
        """Checking corrected to LATE: Savings, which had only borrowed, moves with it."""
        assert resolve_pair_days(
            PairDays(expense=_day(EARLY, ENTERED), income=_day(EARLY, BORROWED)),
            PairDays(expense=_day(LATE, ENTERED), income=None),
            TODAY,
        ) == PairDays(expense=_day(LATE, ENTERED), income=_day(LATE, BORROWED))

    def test_a_side_with_its_own_day_does_not_follow(self):
        """Savings holds its own day: correcting Checking leaves it where it is."""
        assert resolve_pair_days(
            PairDays(expense=_day(EARLY, ENTERED), income=_day(EARLY, OBSERVED)),
            PairDays(expense=_day(LATE, ENTERED), income=None),
            TODAY,
        ) == PairDays(expense=_day(LATE, ENTERED), income=_day(EARLY, OBSERVED))

    def test_a_statement_of_nothing_moves_no_side_with_evidence(self):
        """Finding N-304: a figure correction states no day, and no evidenced side moves."""
        current = PairDays(expense=_day(EARLY, OBSERVED), income=_day(LATE, ENTERED))
        assert resolve_pair_days(current, NO_DAYS, TODAY) == current

    def test_a_stated_day_replaces_the_sides_own(self):
        """A stated day ALWAYS applies to its side; the verb decides which reach here."""
        assert resolve_pair_days(
            PairDays(expense=_day(EARLY, OBSERVED), income=_day(EARLY, BORROWED)),
            PairDays(expense=_day(LATE, ENTERED), income=None),
            TODAY,
        ) == PairDays(expense=_day(LATE, ENTERED), income=_day(LATE, BORROWED))

    def test_nothing_orders_the_two_sides_days(self):
        """Seam S4: the money may reach Savings before it leaves Checking's statement."""
        assert resolve_pair_days(
            NO_DAYS,
            PairDays(expense=_day(LATE, OBSERVED), income=_day(EARLY, ENTERED)),
            TODAY,
        ) == PairDays(expense=_day(LATE, OBSERVED), income=_day(EARLY, ENTERED))

    def test_a_side_holding_no_day_borrows_its_siblings_evidence(self):
        """A side holding no current day (a moved side) borrows its sibling's evidence."""
        assert resolve_pair_days(
            PairDays(expense=_day(EARLY, OBSERVED), income=None),
            NO_DAYS,
            TODAY,
        ) == PairDays(expense=_day(EARLY, OBSERVED), income=_day(EARLY, BORROWED))

    def test_two_borrowing_sides_share_the_fallback(self):
        """Neither side has evidence: both take the fallback, never two guesses."""
        assert resolve_pair_days(
            PairDays(expense=_day(EARLY, BORROWED), income=_day(EARLY, BORROWED)),
            NO_DAYS,
            EARLY,
        ) == PairDays(expense=_day(EARLY, BORROWED), income=_day(EARLY, BORROWED))
