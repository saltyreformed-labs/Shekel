"""Tests for the centralized monetary rounding boundary (E-26, HIGH-04).

The audit (08_findings.md HIGH-04) identified 24 bare
``.quantize(Decimal("0.01"))`` sites in ``app/`` that silently fall back
to Python's default ``ROUND_HALF_EVEN`` (banker's), against the
``ROUND_HALF_UP`` convention every hand-computed financial test in this
project assumes. ``app/utils/money.py`` introduces the single boundary
helper E-26 requires. This file pins the helper's behavior at the
boundaries that matter:

- The half-up / banker's divergence at the ``Decimal("2.345")`` boundary
  (the canonical case where the two rounding modes produce different
  cents: half-up -> 2.35, banker's -> 2.34).
- Negative-number half-up semantics (away from zero on ties).
- Long-precision inputs typical of pre-quantize loan/interest math.
- ``ROUND_CEILING`` sanctioned variant for the savings-goal case (named
  at the call site to make the deviation from default rounding
  explicit).
- ``TypeError`` on ``float`` so callers cannot bypass the Decimal
  contract by accident.

Test IDs C1-1..C1-10 trace to remediation_plan.md Section 9 "Commit 1"
subsection E.
"""
from decimal import Decimal

import pytest

from app.utils.money import (
    MONEY_COLUMN_MAX,
    MoneyTextError,
    money_from_text,
    round_money,
    round_money_ceiling,
    round_money_floor,
)


class TestRoundMoney:
    """Hand-computed golden-cents pins for ``round_money`` (ROUND_HALF_UP)."""

    def test_round_money_half_up_boundary(self):
        """C1-1: 2.345 -> 2.35 under HALF_UP.

        Half-up: ties round away from zero, so 2.345 -> 2.35. Banker's
        (ROUND_HALF_EVEN, Python's Decimal default) would round to the
        nearest even and produce 2.34. The whole point of E-26 is that
        this boundary case must never silently return 2.34.
        """
        assert round_money(Decimal("2.345")) == Decimal("2.35")

    def test_round_money_half_up_even_digit(self):
        """C1-2: 2.355 -> 2.36 under HALF_UP.

        Half-up: 2.355 ties at the cent boundary, rounds away from
        zero to 2.36. Documents the third-decimal-5 case symmetric
        to C1-1.
        """
        assert round_money(Decimal("2.355")) == Decimal("2.36")

    def test_round_money_negative_half_up(self):
        """C1-3: -2.345 -> -2.35 under HALF_UP (ties away from zero).

        ROUND_HALF_UP rounds ties away from zero, so the negative
        half-cent boundary goes to -2.35, not -2.34. Pins the sign
        behavior callers must rely on.
        """
        assert round_money(Decimal("-2.345")) == Decimal("-2.35")

    def test_round_money_already_two_places(self):
        """C1-4: 100.00 -> 100.00 (idempotent at the boundary)."""
        assert round_money(Decimal("100.00")) == Decimal("100.00")

    def test_round_money_long_precision(self):
        """C1-5: 1234.5650001 -> 1234.57.

        Past the half-cent boundary the input is strictly greater
        than 1234.565, so HALF_UP (and any reasonable rounding mode)
        rounds up to 1234.57. Documents the typical pre-quantize
        loan/interest intermediate precision.
        """
        assert round_money(Decimal("1234.5650001")) == Decimal("1234.57")

    def test_round_money_zero(self):
        """C1-6: 0 -> 0.00 (quantization expands to two places)."""
        assert round_money(Decimal("0")) == Decimal("0.00")

    def test_round_money_rejects_float(self):
        """C1-7: round_money(2.345) raises TypeError.

        Accepting a float here would let an upstream
        ``Decimal(float_value)`` -- or worse, a raw float -- bypass
        the Decimal-from-string contract and re-introduce the float
        imprecision the helper exists to eliminate. The boundary
        must refuse loud.
        """
        with pytest.raises(TypeError, match="round_money expects Decimal"):
            round_money(2.345)


class TestRoundMoneyCeiling:
    """Hand-computed pins for the sanctioned ``round_money_ceiling`` variant."""

    def test_round_money_ceiling_rounds_up(self):
        """C1-8: 2.341 -> 2.35 under ROUND_CEILING.

        Ceiling rounds toward positive infinity, so any fractional
        cent past 2.34 lifts to 2.35 -- the savings-goal monthly-
        contribution invariant (never under-fund by a sub-cent).
        """
        assert round_money_ceiling(Decimal("2.341")) == Decimal("2.35")

    def test_round_money_ceiling_exact(self):
        """C1-9: 2.340 -> 2.34 (exact two-place input is idempotent)."""
        assert round_money_ceiling(Decimal("2.340")) == Decimal("2.34")

    def test_round_money_ceiling_rejects_float(self):
        """C1-10: round_money_ceiling(2.34) raises TypeError.

        Same Decimal-only contract as ``round_money``; documented
        per-variant so a future caller cannot assume one helper is
        looser than the other.
        """
        with pytest.raises(TypeError, match="round_money_ceiling expects Decimal"):
            round_money_ceiling(2.34)


class TestRoundMoneyFloor:
    """Hand-computed pins for the sanctioned ``round_money_floor`` variant.

    The largest-remainder cent-allocation base (escrow display rows,
    deep-hunt #17): every row starts from its floor so the leftover
    cents can be distributed without any row overshooting its exact
    value by more than a cent.
    """

    def test_round_money_floor_rounds_down(self):
        """2.349 -> 2.34 under ROUND_FLOOR.

        Floor drops any fractional cent, even .009 -- the allocation
        hands the dropped cents back explicitly, never implicitly.
        """
        assert round_money_floor(Decimal("2.349")) == Decimal("2.34")

    def test_round_money_floor_exact(self):
        """2.340 -> 2.34 (exact two-place input is idempotent)."""
        assert round_money_floor(Decimal("2.340")) == Decimal("2.34")

    def test_round_money_floor_negative_goes_down(self):
        """-2.341 -> -2.35: floor moves toward negative infinity.

        Distinguishes ROUND_FLOOR from truncation (ROUND_DOWN would
        give -2.34); pinned so the allocation's remainder arithmetic
        (exact - base >= 0) holds for negative amounts too.
        """
        assert round_money_floor(Decimal("-2.341")) == Decimal("-2.35")

    def test_round_money_floor_rejects_float(self):
        """round_money_floor(2.34) raises TypeError.

        Same Decimal-only contract as ``round_money``; documented
        per-variant so a future caller cannot assume one helper is
        looser than the other.
        """
        with pytest.raises(TypeError, match="round_money_floor expects Decimal"):
            round_money_floor(2.34)


class TestMoneyFromText:
    """The ONE walk from a figure a source stated as text to recorded cents.

    Plan step ``bank_import:X-f6b-2``: the CSV adapter, the bank feed's
    listing and the feed's reader each spelled it, and they disagreed (the
    listing accepted a number and a quiet ``NaN``).  Every arm here was a
    measured hole on one of them.
    """

    def test_a_decimal_string_becomes_cents_rounded_half_up(self):
        """Built from the string and rounded through the one rule:
        ``-165.220000`` is SECU's own OFX precision, and ``2.345`` is the
        half-cent that ``ROUND_HALF_EVEN`` would take down to ``2.34``."""
        assert money_from_text("386.05") == Decimal("386.05")
        assert money_from_text("-165.220000") == Decimal("-165.22")
        assert money_from_text("2.345") == Decimal("2.35")
        assert money_from_text("0.00") == Decimal("0.00")

    def test_the_widest_figure_a_money_column_holds_is_recorded(self):
        """``Numeric(12, 2)`` holds ten integer digits: ``9999999999.99``
        either side of zero is the edge, and it is admitted."""
        assert money_from_text("9999999999.99") == MONEY_COLUMN_MAX
        assert money_from_text("-9999999999.99") == -MONEY_COLUMN_MAX

    def test_every_refusal_names_its_reason(self):
        """Not a string, not a number, not a real number, too large: each
        its own reason, which the caller's sentence carries.  The column
        edge is graded from both sides: one cent past it, and a half-cent
        that only ROUNDS past it (``9999999999.995`` is ``10000000000.00``
        to the cent), so the bound is checked on the figure recorded."""
        cases = {
            386.05: "not a decimal string",
            386: "not a decimal string",
            None: "not a decimal string",
            "abc": "not an amount",
            "": "not an amount",
            "NaN": "not a real number",
            "sNaN": "not a real number",
            "Infinity": "not a real number",
            "-Infinity": "not a real number",
            "1E+30": "too large to record",
            "10000000000.00": "too large to record",
            "-10000000000.00": "too large to record",
            "9999999999.995": "too large to record",
        }
        for text, reason in cases.items():
            with pytest.raises(MoneyTextError) as caught:
                money_from_text(text)
            assert caught.value.reason == reason, text
            assert repr(text) in str(caught.value), text

    def test_an_exponent_the_constructor_itself_refuses_is_not_an_amount(self):
        """``Decimal("1E+9999999999999999999")`` raises on construction
        (past the context's exponent limit), so it is refused by the same
        arm as text that is no number at all, and with the same class."""
        with pytest.raises(MoneyTextError) as caught:
            money_from_text("1E+9999999999999999999")
        assert caught.value.reason == "not an amount"
