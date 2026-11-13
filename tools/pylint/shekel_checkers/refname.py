"""Reference-name comparison checker: ``shekel-refname`` (W9902).

Flags comparing a ``.name`` attribute against a string literal. Reference-table
``name`` columns are for display only (CLAUDE.md: "IDs for logic, strings for
display only"); logic must key off the ``*_id`` column or an enum constant.
"""

from astroid import nodes

from pylint.checkers import BaseChecker

from ._common import _is_string_const

# Comparison operators where a name-vs-string-literal comparison is the
# reference-table antipattern. ``<`` / ``>`` on a name column are not the
# documented smell and are left alone.
_EQUALITY_OPS = frozenset({"==", "!="})
_MEMBERSHIP_OPS = frozenset({"in", "not in"})
# Attribute name of the reference-table display column. IDs are the logic key;
# this column is display-only (CLAUDE.md: "IDs for logic, strings for display").
_DISPLAY_COLUMN = "name"


def _is_display_name_attr(node: nodes.NodeNG) -> bool:
    """Return True if ``node`` reads a ``.name`` attribute (e.g. ``status.name``).

    Matches the outermost attribute, so ``txn.status.name`` qualifies. Comparing
    such an attribute against a string literal is the reference-table antipattern;
    comparing it against a variable or column (``AccountType.name == data["name"]``)
    is legitimate and is not matched because the other operand is not a literal.
    """
    return isinstance(node, nodes.Attribute) and node.attrname == _DISPLAY_COLUMN


class ShekelRefNameChecker(BaseChecker):
    """Forbid logic that compares reference-table display names to string literals."""

    name = "shekel-refname"
    msgs = {
        "W9902": (
            "Comparison of a .name attribute against a string literal; "
            "key logic off IDs or enums, not display names",
            "shekel-refname-compare",
            "Reference-table 'name' columns are for display only (CLAUDE.md: "
            '"IDs for logic, strings for display only. NEVER compare against '
            'string name columns"). A literal comparison such as '
            "status.name == \"Projected\" silently breaks if the display label is "
            "renamed and bypasses the cached-enum model in app/enums/ / "
            "app/ref_cache/. Compare the *_id column or the enum constant. "
            "Comparing .name against a variable or column is not flagged.",
        ),
    }

    def visit_compare(self, node: nodes.Compare) -> None:
        """Flag ``<expr>.name`` compared to a string literal via ==, !=, in, not in.

        ``node`` is every comparison expression. astroid stores a comparison as a
        left operand plus a list of ``(operator, operand)`` pairs (chained
        comparisons such as ``a == b == c`` produce several pairs); each adjacent
        operand pair is inspected. Only string-literal operands trigger a report,
        so ``AccountType.name == data["name"]`` (a non-literal right side) passes.
        """
        operands = [node.left] + [operand for _, operand in node.ops]
        operators = [operator for operator, _ in node.ops]
        for index, operator in enumerate(operators):
            left = operands[index]
            right = operands[index + 1]
            if operator in _EQUALITY_OPS and self._is_name_literal_equality(left, right):
                self.add_message("shekel-refname-compare", node=node)
                return
            if operator in _MEMBERSHIP_OPS and self._is_name_literal_membership(left, right):
                self.add_message("shekel-refname-compare", node=node)
                return

    @staticmethod
    def _is_name_literal_equality(left: nodes.NodeNG, right: nodes.NodeNG) -> bool:
        """Return True if one side reads ``.name`` and the other is a string literal.

        Order-independent so both ``status.name == "X"`` and ``"X" == status.name``
        are caught.
        """
        return (_is_display_name_attr(left) and _is_string_const(right)) or (
            _is_display_name_attr(right) and _is_string_const(left)
        )

    @staticmethod
    def _is_name_literal_membership(left: nodes.NodeNG, right: nodes.NodeNG) -> bool:
        """Return True for ``<expr>.name in (<string literals>)`` and ``not in``.

        The membership form lists allowed display labels (``status.name in
        ("done", "credit")``); flagged when the left side reads ``.name`` and the
        right side is a literal collection containing at least one string.
        """
        if not _is_display_name_attr(left):
            return False
        if isinstance(right, (nodes.Tuple, nodes.List, nodes.Set)):
            return any(_is_string_const(element) for element in right.elts)
        return _is_string_const(right)
