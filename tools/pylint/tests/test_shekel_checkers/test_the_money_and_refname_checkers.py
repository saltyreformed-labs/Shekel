"""The money checker and the reference-name comparison checker."""

import astroid
from pylint.testutils import CheckerTestCase, MessageTest

from shekel_checkers import ShekelMoneyChecker, ShekelRefNameChecker


class TestShekelMoneyChecker(CheckerTestCase):
    """The ``shekel-money`` checker: two monetary call rules.

    ``shekel-decimal-from-float`` -- Decimal must be built from strings, not
    floats; ``shekel-bare-money-quantize`` -- money must be rounded through
    ``round_money`` (explicit ROUND_HALF_UP), never a bare ``.quantize()`` that
    falls back to banker's rounding.
    """

    CHECKER_CLASS = ShekelMoneyChecker

    def test_flags_float_literal(self) -> None:
        """Decimal(0.1) loses precision and must be flagged."""
        node = astroid.extract_node("Decimal(0.1)")
        with self.assertAddsMessages(
            MessageTest("shekel-decimal-from-float", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_signed_float_literal(self) -> None:
        """Decimal(-0.1) hides the float behind a unary minus; still flagged."""
        node = astroid.extract_node("Decimal(-0.1)")
        with self.assertAddsMessages(
            MessageTest("shekel-decimal-from-float", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_float_builtin_argument(self) -> None:
        """Decimal(float(x)) launders a float through float(); still flagged."""
        node = astroid.extract_node("Decimal(float(x))")
        with self.assertAddsMessages(
            MessageTest("shekel-decimal-from-float", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_qualified_decimal_from_float(self) -> None:
        """decimal.Decimal(0.1) (attribute form) is flagged just like the bare call."""
        node = astroid.extract_node("decimal.Decimal(0.1)")
        with self.assertAddsMessages(
            MessageTest("shekel-decimal-from-float", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_allows_string_literal(self) -> None:
        """Decimal(\"0.1\") is exact and must NOT be flagged."""
        node = astroid.extract_node('Decimal("0.1")')
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_integer_literal(self) -> None:
        """Decimal(5) is exact; integer arguments are intentionally allowed."""
        node = astroid.extract_node("Decimal(5)")
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_variable_argument(self) -> None:
        """Decimal(x) cannot be statically proven float; not flagged (no false positive)."""
        node = astroid.extract_node("Decimal(x)")
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_ignores_non_decimal_call(self) -> None:
        """A float literal passed to some other callable is not this checker's concern."""
        node = astroid.extract_node("SomeWidget(0.1)")
        with self.assertNoMessages():
            self.checker.visit_call(node)

    # ── shekel-bare-money-quantize (W9904) ──────────────────────────

    def test_flags_bare_quantize_decimal_literal(self) -> None:
        """amount.quantize(Decimal(\"0.01\")) rounds money with banker's default; flagged."""
        node = astroid.extract_node('amount.quantize(Decimal("0.01"))')
        with self.assertAddsMessages(
            MessageTest("shekel-bare-money-quantize", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_bare_quantize_two_places_constant(self) -> None:
        """amount.quantize(TWO_PLACES) -- the named cents constant -- is flagged."""
        node = astroid.extract_node("amount.quantize(TWO_PLACES)")
        with self.assertAddsMessages(
            MessageTest("shekel-bare-money-quantize", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_bare_quantize_underscore_cents_constant(self) -> None:
        """amount.quantize(_TWO_PLACES) -- the private redeclaration form -- is flagged."""
        node = astroid.extract_node("total.quantize(_TWO_PLACES)")
        with self.assertAddsMessages(
            MessageTest("shekel-bare-money-quantize", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_allows_quantize_with_rounding_keyword(self) -> None:
        """quantize(CENTS, rounding=ROUND_HALF_UP) selects the mode explicitly; not flagged."""
        node = astroid.extract_node(
            "amount.quantize(CENTS, rounding=ROUND_HALF_UP)",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_quantize_with_positional_rounding(self) -> None:
        """quantize(TWO_PLACES, ROUND_HALF_UP) -- positional mode -- is not flagged."""
        node = astroid.extract_node("amount.quantize(TWO_PLACES, ROUND_HALF_UP)")
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_bare_quantize_non_cents_quantum(self) -> None:
        """A bare quantize of a one-decimal percentage is not money; not flagged."""
        node = astroid.extract_node('pct.quantize(Decimal("0.1"))')
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_bare_quantize_named_percentage_quantum(self) -> None:
        """A bare quantize of a non-cents named quantum (_PCT_QUANTUM) is not flagged."""
        node = astroid.extract_node("rate.quantize(_PCT_QUANTUM)")
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_round_money_helper_call(self) -> None:
        """round_money(x) is the sanctioned boundary helper, not a bare quantize; not flagged."""
        node = astroid.extract_node("round_money(amount)")
        with self.assertNoMessages():
            self.checker.visit_call(node)


class TestShekelRefNameChecker(CheckerTestCase):
    """``shekel-refname-compare``: logic must not compare .name to string literals."""

    CHECKER_CLASS = ShekelRefNameChecker

    def test_flags_name_equals_literal(self) -> None:
        """status.name == \"Projected\" is the reference-table antipattern."""
        node = astroid.extract_node('status.name == "Projected"')
        with self.assertAddsMessages(
            MessageTest("shekel-refname-compare", node=node),
            ignore_position=True,
        ):
            self.checker.visit_compare(node)

    def test_flags_reversed_operands(self) -> None:
        """\"Projected\" == status.name is the same smell with operands swapped."""
        node = astroid.extract_node('"Projected" == status.name')
        with self.assertAddsMessages(
            MessageTest("shekel-refname-compare", node=node),
            ignore_position=True,
        ):
            self.checker.visit_compare(node)

    def test_flags_nested_attribute(self) -> None:
        """txn.status.name == \"Projected\" still reads the display column."""
        node = astroid.extract_node('txn.status.name == "Projected"')
        with self.assertAddsMessages(
            MessageTest("shekel-refname-compare", node=node),
            ignore_position=True,
        ):
            self.checker.visit_compare(node)

    def test_flags_inequality(self) -> None:
        """status.name != \"Projected\" is flagged like equality."""
        node = astroid.extract_node('status.name != "Projected"')
        with self.assertAddsMessages(
            MessageTest("shekel-refname-compare", node=node),
            ignore_position=True,
        ):
            self.checker.visit_compare(node)

    def test_flags_membership_in_literal_tuple(self) -> None:
        """status.name in (\"done\", \"credit\") keys logic off display labels."""
        node = astroid.extract_node('status.name in ("done", "credit")')
        with self.assertAddsMessages(
            MessageTest("shekel-refname-compare", node=node),
            ignore_position=True,
        ):
            self.checker.visit_compare(node)

    def test_allows_name_equals_subscript(self) -> None:
        """AccountType.name == data[\"name\"] compares to user input, not a literal."""
        node = astroid.extract_node('AccountType.name == data["name"]')
        with self.assertNoMessages():
            self.checker.visit_compare(node)

    def test_allows_name_equals_variable(self) -> None:
        """status.name == expected (a variable) is a legitimate dynamic comparison."""
        node = astroid.extract_node("status.name == expected")
        with self.assertNoMessages():
            self.checker.visit_compare(node)

    def test_allows_id_comparison(self) -> None:
        """status_id == 3 keys off the ID column and is the correct pattern."""
        node = astroid.extract_node("status_id == 3")
        with self.assertNoMessages():
            self.checker.visit_compare(node)

    def test_allows_non_name_attribute(self) -> None:
        """request.method == \"POST\" reads .method, not the .name display column."""
        node = astroid.extract_node('request.method == "POST"')
        with self.assertNoMessages():
            self.checker.visit_compare(node)
