"""The disable-rationale checker."""

import astroid
from pylint.testutils import CheckerTestCase, MessageTest

from shekel_checkers import ShekelDisableRationaleChecker


class TestShekelDisableRationaleChecker(CheckerTestCase):
    """``shekel-disable-rationale``: every disable needs a standard ``Pylint:`` note.

    Exercised through ``process_module`` against whole-module sources parsed with
    ``astroid.parse`` (whose ``stream()`` yields the source the raw checker
    tokenizes). Each ``def``/``class``-scoped case (rationale in the docstring) is
    paired with a statement-scoped case (rationale in a comment immediately above),
    and every positive (flagged) case is paired with the conforming form that must
    NOT fire -- a checker that over-fires would itself become disable noise.
    """

    CHECKER_CLASS = ShekelDisableRationaleChecker

    def test_allows_def_with_docstring_rationale(self) -> None:
        """A def-line disable justified in the docstring naming every rule passes."""
        module = astroid.parse(
            'def f(a, b, c, d, e, f):  '
            '# pylint: disable=too-many-arguments,too-many-positional-arguments\n'
            '    """Do a thing.\n'
            "\n"
            "    Pylint: ``too-many-arguments`` (6/5) / "
            "``too-many-positional-arguments`` (6/5) -- irreducible inputs.\n"
            '    """\n'
            "    return a\n"
        )
        with self.assertNoMessages():
            self.checker.process_module(module)

    def test_flags_def_without_marker(self) -> None:
        """A def-line disable whose docstring lacks the ``Pylint:`` marker is flagged."""
        module = astroid.parse(
            "def f(a, b, c, d, e, f):  # pylint: disable=too-many-arguments\n"
            '    """Do a thing with no rationale for the disable."""\n'
            "    return a\n"
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-disable-rationale",
                line=1,
                args=("too-many-arguments", "in the docstring"),
            ),
            ignore_position=True,
        ):
            self.checker.process_module(module)

    def test_flags_def_missing_one_rule_name(self) -> None:
        """A multi-rule disable must name EVERY rule in the docstring, not just one."""
        module = astroid.parse(
            "def f(a, b, c, d, e, f):  "
            "# pylint: disable=too-many-arguments,too-many-positional-arguments\n"
            '    """Do a thing.\n'
            "\n"
            "    Pylint: ``too-many-arguments`` (6/5) -- only one rule named.\n"
            '    """\n'
            "    return a\n"
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-disable-rationale",
                line=1,
                args=(
                    "too-many-arguments, too-many-positional-arguments",
                    "in the docstring",
                ),
            ),
            ignore_position=True,
        ):
            self.checker.process_module(module)

    def test_allows_class_with_docstring_rationale(self) -> None:
        """A class-line disable justified in the docstring passes."""
        module = astroid.parse(
            "class Bag:  # pylint: disable=too-many-instance-attributes\n"
            '    """A flat record.\n'
            "\n"
            "    Pylint: ``too-many-instance-attributes`` (8/7) -- flat aggregate.\n"
            '    """\n'
            "\n"
            "    x = 1\n"
        )
        with self.assertNoMessages():
            self.checker.process_module(module)

    def test_allows_decorated_def_disable(self) -> None:
        """The directive sits on the ``def`` line, not the decorator -- fromlineno maps it."""
        module = astroid.parse(
            "import functools\n"
            "@functools.cache\n"
            "def f():  # pylint: disable=too-many-return-statements\n"
            '    """Do a thing.\n'
            "\n"
            "    Pylint: ``too-many-return-statements`` (7/6) -- distinct exits.\n"
            '    """\n'
            "    return 1\n"
        )
        with self.assertNoMessages():
            self.checker.process_module(module)

    def test_allows_statement_with_comment_above(self) -> None:
        """A statement-scoped disable with a ``# Pylint:`` comment immediately above passes."""
        module = astroid.parse(
            "def h():\n"
            '    """Do a thing."""\n'
            "    # Pylint: ``invalid-name`` -- a single-letter loop alias reads clearer.\n"
            "    X = 1  # pylint: disable=invalid-name\n"
            "    return X\n"
        )
        with self.assertNoMessages():
            self.checker.process_module(module)

    def test_allows_standalone_disable_comment_with_rationale_above(self) -> None:
        """The deferred-import pattern: rationale above a standalone disable line."""
        module = astroid.parse(
            "def imp():\n"
            '    """Do a thing."""\n'
            "    # Pylint: ``import-outside-toplevel`` -- deferred to break a cycle.\n"
            "    # pylint: disable=import-outside-toplevel\n"
            "    import os\n"
            "    return os\n"
        )
        with self.assertNoMessages():
            self.checker.process_module(module)

    def test_flags_statement_without_comment_above(self) -> None:
        """A statement-scoped disable with no comment above is flagged."""
        module = astroid.parse(
            "def h():\n"
            '    """Do a thing."""\n'
            "    X = 1  # pylint: disable=invalid-name\n"
            "    return X\n"
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-disable-rationale",
                line=3,
                args=("invalid-name", "in a comment immediately above"),
            ),
            ignore_position=True,
        ):
            self.checker.process_module(module)

    def test_flags_statement_comment_separated_by_blank_line(self) -> None:
        """A rationale separated from the directive by a blank line does not count."""
        module = astroid.parse(
            "def h():\n"
            '    """Do a thing."""\n'
            "    # Pylint: ``invalid-name`` -- reason that floats away.\n"
            "\n"
            "    X = 1  # pylint: disable=invalid-name\n"
            "    return X\n"
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-disable-rationale",
                line=5,
                args=("invalid-name", "in a comment immediately above"),
            ),
            ignore_position=True,
        ):
            self.checker.process_module(module)

    def test_ignores_disable_text_inside_string_literal(self) -> None:
        """``# pylint: disable=`` inside a string is not a directive (no false positive)."""
        module = astroid.parse('S = "# pylint: disable=too-many-arguments"\n')
        with self.assertNoMessages():
            self.checker.process_module(module)

    def test_ignores_enable_directive(self) -> None:
        """``# pylint: enable=`` is not a suppression and needs no rationale."""
        module = astroid.parse("X = 1  # pylint: enable=too-many-arguments\n")
        with self.assertNoMessages():
            self.checker.process_module(module)

    def test_flags_combined_noqa_disable_without_rationale(self) -> None:
        """A ``# noqa: ...  pylint: disable=`` combined comment cannot evade the gate.

        Pylint honors the directive anywhere in the comment, so the checker
        must too: the historical combined trailing form used to slip past the
        old ``#\\s*pylint:`` regex entirely, leaving an undocumented
        suppression invisible to the rationale audit.
        """
        module = astroid.parse(
            "import sys\n"
            "sys.path.insert(0, '.')\n"
            "import os  # noqa: E402  pylint: disable=wrong-import-position\n"
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-disable-rationale",
                line=3,
                args=("wrong-import-position", "in a comment immediately above"),
            ),
            ignore_position=True,
        ):
            self.checker.process_module(module)

    def test_allows_combined_noqa_disable_with_rationale_above(self) -> None:
        """The combined form passes once the standard rationale sits above it."""
        module = astroid.parse(
            "import sys\n"
            "sys.path.insert(0, '.')\n"
            "# Pylint: ``wrong-import-position`` -- the bootstrap must precede it.\n"
            "import os  # noqa: E402  pylint: disable=wrong-import-position\n"
        )
        with self.assertNoMessages():
            self.checker.process_module(module)
