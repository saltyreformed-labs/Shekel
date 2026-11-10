"""The statement must agree with itself before the app believes it.

Plan step **bank_import:X-f6a-1**.  A source carrying a per-line running balance
states the same account twice -- once as a sequence of amounts, once as a
sequence of balances -- and the two must agree.  That redundancy is the only
self-check the app has over a record it did not author.

**Every refusal here is a FIRING CONTROL** (``docs/plans/verification.md``
standard 4): the chain holds on 305 of 305 consecutive pairs of the developer's
real export, so nothing in ordinary use exercises the failure arm, and a test
that only asserted a good file passes would pass equally against a checker that
returned unconditionally.  Each test below plants the break and asserts the
refusal.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.statement_import import (
    StatementIntegrityError,
    StatementLine,
    StatementParseError,
    carries_running_balance,
    opening_balance,
    verify_running_balance,
)


def _line(day, amount, running, description="X"):
    """Return one line with a stated running balance."""
    return StatementLine(
        posted_on=day,
        transaction_on=day,
        amount=Decimal(amount),
        description=description,
        running_balance=None if running is None else Decimal(running),
    )


def _chain(start, moves):
    """Return lines whose balances follow from *start*, chronologically."""
    balance = Decimal(start)
    lines = []
    for index, amount in enumerate(moves):
        balance += Decimal(amount)
        lines.append(_line(date(2026, 3, 1 + index), amount, balance))
    return lines


class TestAGoodChainPasses:
    """The correct file must not be refused; a gate that refuses one is worse
    than no gate."""

    def test_a_consistent_chain_raises_nothing(self):
        """Three ordinary movements whose balances follow."""
        verify_running_balance(_chain("100.00", ["-25.00", "50.00", "-10.00"]))

    def test_a_single_line_has_no_pair_to_check(self):
        """The first line is unchecked by construction, and that is honest."""
        verify_running_balance([_line(date(2026, 3, 1), "-25.00", "75.00")])

    def test_an_empty_file_raises_nothing(self):
        """Total over the empty input."""
        verify_running_balance([])

    def test_a_source_with_no_running_balance_is_not_refused(self):
        """The column is an export option; its absence costs the check only."""
        lines = [
            _line(date(2026, 3, 1), "-25.00", None),
            _line(date(2026, 3, 2), "-10.00", None),
        ]

        verify_running_balance(lines)

        assert carries_running_balance(lines) is False


class TestABrokenChainIsRefused:
    """Each break is a different real defect, and all three land here."""

    def test_a_missing_line_is_caught(self):
        """The commonest failure: a partial export.

        Drop the middle movement and the surviving balances no longer follow,
        which is exactly what a re-export of a narrower span would look like
        if the importer stitched spans together naively.
        """
        lines = _chain("100.00", ["-25.00", "50.00", "-10.00"])
        del lines[1]

        with pytest.raises(StatementIntegrityError) as caught:
            verify_running_balance(lines)

        assert caught.value.break_count == 1

    def test_a_tampered_amount_is_caught(self):
        """An edited file: the amount no longer explains the balance move."""
        lines = _chain("100.00", ["-25.00", "50.00"])
        lines[1] = _line(lines[1].posted_on, "-999.00", lines[1].running_balance)

        with pytest.raises(StatementIntegrityError):
            verify_running_balance(lines)

    def test_lines_in_the_WRONG_ORDER_are_caught(self):
        """The chain is a prefix sum, so a newest-first file fails on it.

        This is the arm that makes the adapter's reversal load-bearing rather
        than cosmetic: an adapter that forgot to reverse would be caught here
        instead of silently assigning every ordinal and every date backwards.
        """
        lines = _chain("100.00", ["-25.00", "50.00", "-10.00"])
        lines.reverse()

        with pytest.raises(StatementIntegrityError):
            verify_running_balance(lines)

    def test_it_reports_how_many_broke_and_names_the_EARLIEST(self):
        """Two separated breaks, so the count and the choice both matter.

        ``break_count >= 1`` -- which is what this asserted first -- is
        ``result > 0``, and no test in this file produced more than one break,
        so a hardcoded count and ``breaks[-1]`` were both indistinguishable
        from correct.
        """
        lines = _chain(
            "100.00", ["-25.00", "50.00", "-10.00", "5.00", "-1.00", "2.00"],
        )
        # Shift a SUFFIX of the balances by a constant: that breaks exactly
        # the one pair at the seam and leaves every later pair consistent.
        # Shifting a single line instead breaks the pair on BOTH sides of it,
        # which is how the first version of this test asked for 2 and got 3.
        def _shift(index, by):
            for position in range(index, len(lines)):
                lines[position] = _line(
                    lines[position].posted_on, lines[position].amount,
                    lines[position].running_balance + Decimal(by),
                )

        _shift(2, "7.00")
        _shift(5, "13.00")

        with pytest.raises(StatementIntegrityError) as caught:
            verify_running_balance(lines)

        assert caught.value.break_count == 2
        assert str(lines[2].posted_on) in caught.value.first_break
        assert str(lines[5].posted_on) not in caught.value.first_break
        assert "Nothing was imported" in str(caught.value)

    def test_a_one_cent_break_is_caught(self):
        """The comparison is exact, because money is exact."""
        lines = _chain("100.00", ["-25.00", "50.00"])
        lines[1] = _line(
            lines[1].posted_on, lines[1].amount,
            lines[1].running_balance + Decimal("0.01"),
        )

        with pytest.raises(StatementIntegrityError):
            verify_running_balance(lines)


class TestAPartialBalanceColumnIsAParseFailure:
    """Some rows carrying a balance and some not is a broken READ, not a
    source that lacks the fact."""

    def test_a_mixture_is_refused(self):
        """Downgrading silently to "no self-check" would hide a bad parser."""
        lines = [
            _line(date(2026, 3, 1), "-25.00", "75.00"),
            _line(date(2026, 3, 2), "-10.00", None),
        ]

        with pytest.raises(StatementParseError, match="running balance on 1 of 2"):
            carries_running_balance(lines)

    def test_verify_surfaces_that_same_refusal(self):
        """The checker does not swallow it and report a clean chain."""
        lines = [
            _line(date(2026, 3, 1), "-25.00", "75.00"),
            _line(date(2026, 3, 2), "-10.00", None),
        ]

        with pytest.raises(StatementParseError):
            verify_running_balance(lines)


class TestTheOpeningIsDerivedFromTheLines:
    """Never from the file's own header, which was measured to lag."""

    def test_the_opening_is_the_first_balance_minus_its_own_move(self):
        """The balance before anything in the file happened."""
        lines = _chain("100.00", ["-25.00", "50.00"])

        assert opening_balance(lines) == Decimal("100.00")

    def test_it_is_none_without_a_running_balance(self):
        """A source that does not state balances does not get an invented one."""
        lines = [_line(date(2026, 3, 1), "-25.00", None)]

        assert opening_balance(lines) is None

    def test_it_is_none_for_an_empty_file(self):
        """Total rather than raising."""
        assert opening_balance([]) is None
