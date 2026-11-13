"""Unit tests for the Shekel project-specific pylint checkers.

Each checker is exercised against AST snippets built with ``astroid.extract_node``
and verified with pylint's ``CheckerTestCase`` harness. Every positive case
(the antipattern is flagged) is paired with the corresponding negative case (the
legitimate form is NOT flagged), because a checker that over-fires creates the
cargo-cult-disable noise the rules exist to prevent.

The package-privacy checker's physical-membership primitive,
``_importer_file_inside``, is tested DIRECTLY: its placeholder-path guards have
no other discriminating observer.  It is internal to the checker module rather
than a package re-export, so it is imported from its home,
``shekel_checkers.package_privacy``.
"""

import sys
from pathlib import Path

import astroid
import pytest
from astroid import nodes
from pylint.testutils import CheckerTestCase, MessageTest

from shekel_checkers import (
    _CASH_LEDGER_MODULES,
    _FENCED_MODULE_RULINGS,
    _LEDGER_LINES_MODULES,
    _LOAN_PAYMENT_SEAM_MODULES,
    _SEAM_PRIVATE_CONTEXT_MODULES,
    _LEDGER_LEAF_MODULE_NAMES,
    _LEDGER_MODEL_ALLOWLIST,
    _LEDGER_MODEL_MODULES,
    _KIND_CLASSIFIER_MODULES,
    _LEDGER_MODEL_NAMES,
    _RECURRING_DEFINITION_MODULES,
    _ROW_VALUATION_MODULES,
    _LOAN_LEDGER_DEFINING_MODULES,
    _LOAN_RESOLVER_ENGINE_MODULES,
    _STATUS_SEAM_MODULES,
    _is_public_export_surface,
    ShekelBalanceSeamChecker,
    ShekelDisableRationaleChecker,
    ShekelLedgerModelFenceChecker,
    ShekelMoneyChecker,
    ShekelPackagePrivacyChecker,
    ShekelRefNameChecker,
    ShekelTransactionStatusBypassChecker,
)
from shekel_checkers.package_privacy import _importer_file_inside

# repo root: this file is <root>/tools/pylint/tests/test_*.py
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _fenced_module_sources(module_name: str) -> list[Path]:
    """Return every source file a fenced *module_name* scope covers.

    A flat module (``app.services.account_projection``) is one file.  A PACKAGE
    (``app.services.loan_ledger``) is every ``.py`` file inside it --
    because the checker scopes a package by prefix, so a producer born in ANY
    submodule (``_walk``, ``_visible``, ``_events``) is in scope and must be
    classified.  Enumerating them here is what makes the registry-vs-reality
    test see the same surface the checker does.

    Reads the source with astroid rather than importing ``app``, keeping the
    checker's unit tests free of the Flask/SQLAlchemy import graph.
    """
    relative = Path(*module_name.split("."))
    flat = _REPO_ROOT / relative.with_suffix(".py")
    if flat.is_file():
        return [flat]
    package = _REPO_ROOT / relative
    assert package.is_dir(), (
        f"fenced module {module_name} is neither a module nor a package -- the "
        f"scope names something that does not exist"
    )
    return sorted(package.glob("*.py"))


def _public_export_names(tree: nodes.Module) -> list[str]:
    """Return every public name a consumer outside *tree*'s module can call.

    The registry-vs-reality mirror of the checker's own
    ``_is_public_export_surface``, and it MUST agree with it on what "the export
    surface" means -- otherwise this guard stops pinning the thing it claims to
    pin. That is not hypothetical: while both looked only at ``tree.body``, a
    public ``BalanceContext.loan`` handed routes a ``ResolvedLoan`` unseen by
    either. So rather than re-implement the rule (a second copy that can drift),
    this walks every FunctionDef in the tree and asks the CHECKER'S OWN predicate.

    Args:
        tree: The parsed module.

    Returns:
        The public export names, in source order.
    """
    return [
        node.name for node in tree.nodes_of_class(nodes.FunctionDef)
        if _is_public_export_surface(node)
    ]


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


class TestShekelBalanceSeamChecker(CheckerTestCase):
    """``shekel-unclassified-fenced-export``: the fence's fail-closed residue.

    Every screen must obtain an account's balance through
    ``app.services.balance_at``, and that is now STRUCTURAL, not name-keyed:
    plan step D1d moved every producer inside the seam as a private submodule
    (today ``_cash_fold`` / ``_asset_fold`` / ``_kernel``; ``_daily_series``
    went at plan step X-c2b3, the calendar's per-day line being the fold
    sampled at every day, and ``_cash_engine`` / ``_calculator`` /
    ``_investment`` / ``_interest`` at X-g4b, once TWO folds answered what
    six producers used to),
    W9910 forbids reaching one from outside the package in
    every import spelling, and plan step E1e DELETED the last public producer
    outside the seam (the two genesis posting readers) rather than fencing it.
    So the call-fence tests are gone with the call fence -- the W9910 suite owns
    every import spelling now, and the reader spellings a consumer would write
    are E0611 / E1101 under ``--fail-on=E``.

    What survives here is the one judgment no AST rule can make: whether a new
    PUBLIC name in a balance-ingredient package is a producer.  The rule keys
    off the ENCLOSING module (``node.root().name``), so each case is parsed
    inside a named module via :func:`astroid.parse` (``module_name=``) rather
    than the bare :func:`astroid.extract_node` the shape-only checkers use --
    that yields an empty module name.  Every flagged form is paired with the
    conforming form that must NOT fire, and register-bound loops assert the
    check covers EVERY scoped module and EVERY classified name.
    """

    CHECKER_CLASS = ShekelBalanceSeamChecker

    @staticmethod
    def _function_def(source: str, module_name: str) -> nodes.FunctionDef:
        """Return the FunctionDef for *source* parsed inside *module_name*.

        The enclosing module's name drives the fenced-surface scoping, so it is
        set explicitly.  The snippet defines exactly one top-level function, so
        the module body's first statement is the node under test.
        """
        module = astroid.parse(source, module_name=module_name)
        return module.body[0]

    def test_flags_unclassified_public_function_in_loan_resolver(self) -> None:
        """A NEW public function in the loan-resolver tier is flagged (B-12 closed).

        This is the regression test for the fence's fail-open default: exactly
        the shape of ``loan_owed_at_dates`` (born inside a covered tier, never
        listed, silently reachable) -- and the ``loan_resolver`` package was
        the findings ledger's "wholly unfenced producer tier" until plan step
        D3 scoped it.  It must fail the moment the function is DEFINED, in any
        submodule (the package key prefix-matches).
        """
        node = self._function_def(
            "def owed_at_some_dates(accounts, scenario_id):\n    return {}\n",
            "app.services.loan_resolver._state",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-unclassified-fenced-export",
                node=node,
                args=("owed_at_some_dates",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_functiondef(node)

    def test_flags_unclassified_public_function_in_ledger_reader(self) -> None:
        """The same completeness rule binds on a loan-ledger submodule."""
        node = self._function_def(
            "def confirmed_loan_balance_somewhere(loan_id, scenario_id):\n"
            "    return None\n",
            "app.services.loan_ledger._walk",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-unclassified-fenced-export",
                node=node,
                args=("confirmed_loan_balance_somewhere",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_functiondef(node)

    def test_allows_every_classified_name_in_its_fenced_surface(self) -> None:
        """EVERY classified name -- producer or non-producer -- is exempt.

        Register-bound over both fenced surfaces, so a name added to (or dropped
        from) either set is automatically covered. Asserts the completeness check
        never fires on a function the project has already ruled on.
        """
        for module_name, (producers, non_producers) in _FENCED_MODULE_RULINGS.items():
            for name in sorted(producers | non_producers):
                node = self._function_def(
                    f"def {name}(account, scenario):\n    return None\n",
                    module_name,
                )
                with self.assertNoMessages():
                    self.checker.visit_functiondef(node)

    def test_ignores_private_function_in_fenced_module(self) -> None:
        """A private (underscore) cluster function is not part of the export surface."""
        node = self._function_def(
            "def _account_interest_projection(account, scenario):\n    return {}\n",
            "app.services.balance_at._kernel",
        )
        with self.assertNoMessages():
            self.checker.visit_functiondef(node)

    def test_ignores_public_function_in_consumer_module(self) -> None:
        """A consumer's own public functions are not the fence's business.

        The name and module are a REAL pair (``_horizon.build_horizon``), not a
        plausible-looking one: this fixture named ``compute_net_worth_horizon``
        against ``_horizon`` until plan step X-q2, and that function lived in
        ``_orchestrator`` and has since been deleted -- a synthetic fixture
        drifts silently because nothing resolves it.
        """
        node = self._function_def(
            "def build_horizon(core, account_data):\n"
            "    return {}\n",
            "app.services.savings_dashboard_service._horizon",
        )
        with self.assertNoMessages():
            self.checker.visit_functiondef(node)

    def test_ignores_public_function_in_the_seam_package(self) -> None:
        """The seam's OWN public entries are what consumers call; never flagged.

        ``balance_at`` is deliberately outside every scoped surface: its public
        functions ARE the fence-compliant entry points, so "unclassified" is
        meaningless there. Uses a submodule name to also pin that the seam's
        package split does not drag its entries into the cluster scope.
        """
        node = self._function_def(
            "def liability_owed_at_dates(liabilities, scenario, dates, current):\n"
            "    return {}\n",
            "app.services.balance_at._liability",
        )
        with self.assertNoMessages():
            self.checker.visit_functiondef(node)

    def test_ignores_nested_function_in_fenced_module(self) -> None:
        """A nested def is not part of the module's export surface."""
        module = astroid.parse(
            "def generate_debt_schedules(accounts, scenario_id):\n"
            "    def owed_at_some_dates(dates):\n"
            "        return {}\n"
            "    return owed_at_some_dates\n",
            module_name="app.services.balance_at._kernel",
        )
        nested = module.body[0].body[0]
        with self.assertNoMessages():
            self.checker.visit_functiondef(nested)

    # ── The context's loan handle: the hole a public METHOD opened ──────

    @staticmethod
    def _method_def(source: str, module_name: str) -> nodes.FunctionDef:
        """Return the first METHOD of the first class in *source*.

        The method-level counterpart of :meth:`_function_def`. The snippet
        defines exactly one class with one method, so the class body's first
        statement is the node under test.
        """
        module = astroid.parse(source, module_name=module_name)
        return module.body[0].body[0]

    def test_flags_unclassified_public_method_in_fenced_module(self) -> None:
        """A NEW public METHOD in a fenced module, unclassified, is flagged.

        THE regression test for this checker's second fail-open hole.
        ``visit_functiondef`` used to return early for anything whose parent was
        not the Module, so a public method was never classified at all -- and
        ``BalanceContext.loan`` became exactly that: a public method handing any
        caller a ``ResolvedLoan`` whose then-extant ``state.current_balance`` was
        a balance-at-today one attribute read away. Measured before the fix: a
        route reading ``ctx.loan_state(account).current_balance`` rated 10.00/10.
        D3's adversarial review re-measured the same shape from the other side
        (a ``ctx.balance_now(account)`` folding the memoized walk rated
        10.00/10 with the ``_context`` ruling deleted), which is why that
        ruling is the seam-private scope D3 keeps (its mixin's module joined it
        at ruling R-BAL146): ``BalanceContext`` is
        publicly re-exported, and W9910 cannot see a method on an object a
        consumer holds.
        """
        node = self._method_def(
            "class BalanceContext:\n"
            "    def balance_now(self, account):\n"
            "        return fold_from_walk(self.loan_walk(account), [self.as_of])\n",
            "app.services.balance_at._context",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-unclassified-fenced-export",
                node=node,
                args=("balance_now",),
                line=2, col_offset=4,
                end_line=2, end_col_offset=19,
            ),
        ):
            self.checker.visit_functiondef(node)

    def test_ignores_private_method_in_fenced_module(self) -> None:
        """A private method is not reachable from outside; not an export."""
        node = self._method_def(
            "class BalanceContext:\n"
            "    def _memoize(self, account):\n"
            "        return None\n",
            "app.services.balance_at._context",
        )
        with self.assertNoMessages():
            self.checker.visit_functiondef(node)

    def test_ignores_public_method_of_private_class_in_fenced_module(self) -> None:
        """A public method of a PRIVATE class: a consumer cannot name the class."""
        node = self._method_def(
            "class _Memo:\n"
            "    def balance_right_now(self, account):\n"
            "        return None\n",
            "app.services.balance_at._context",
        )
        with self.assertNoMessages():
            self.checker.visit_functiondef(node)

    def test_flags_public_method_of_a_NESTED_public_class(self) -> None:
        """A method of a public class nested in a public class is still reachable.

        ``Outer.Inner().balance_right_now()`` is nameable from a consumer, so it is
        export surface. A fixed two-level parent test (``parent is a ClassDef whose
        parent is the Module``) drops it silently. No fenced module has this shape
        today -- and "it cannot happen today" is exactly the reasoning that
        produced both holes this checker exists to close, so the walk is up the
        whole ancestor chain and this pins it.
        """
        module = astroid.parse(
            "class Outer:\n"
            "    class Inner:\n"
            "        def balance_right_now(self, account):\n"
            "            return None\n",
            module_name="app.services.loan_resolver._state",
        )
        node = module.body[0].body[0].body[0]
        with self.assertAddsMessages(
            MessageTest(
                "shekel-unclassified-fenced-export",
                node=node,
                args=("balance_right_now",),
                line=3, col_offset=8,
                end_line=3, end_col_offset=29,
            ),
        ):
            self.checker.visit_functiondef(node)

    def test_flags_public_function_defined_under_a_module_level_if(self) -> None:
        """A top-level function inside an ``if`` / ``try`` is still an export.

        ``node.parent`` is the ``If``, not the Module, so a bare
        ``isinstance(parent, Module)`` test drops it -- and a conditional
        definition (a feature flag, a try/except import shim) is a perfectly
        ordinary way to define a public producer.
        """
        module = astroid.parse(
            "import os\n"
            "if os.environ.get('X'):\n"
            "    def balance_right_now(account):\n"
            "        return None\n",
            module_name="app.services.loan_resolver._state",
        )
        node = module.body[1].body[0]
        with self.assertAddsMessages(
            MessageTest(
                "shekel-unclassified-fenced-export",
                node=node,
                args=("balance_right_now",),
                line=3, col_offset=4,
                end_line=3, end_col_offset=25,
            ),
        ):
            self.checker.visit_functiondef(node)

    def test_flags_unclassified_export_in_every_hand_scoped_module(self) -> None:
        """Every W9909-scoped module really is covered -- named literally.

        Since plan step D3 the whole registry scope is hand-written constants
        sitting a few lines from the entries they cover -- the residue of
        PUBLIC balance-ingredient packages W9910 cannot protect:

        * ``_LOAN_LEDGER_DEFINING_MODULES`` (``loan_ledger`` and ``loan_walk_reads``:
          the walk and the page's reads of it; its writers went at plan#3133)
        * ``_CASH_LEDGER_MODULES`` (``cash_ledger``, D1a then D1c)
        * ``_ROW_VALUATION_MODULES`` (``row_valuation``, X-au-c2 -- the
          producer-free valuation arms, extracted BELOW ``cash_ledger`` so the
          loan stack can reach them without closing an import cycle, and
          scoped the day they moved so the extraction did not un-rule
          ``settled_contribution``, which plan step X-bx renamed from
          ``owned_contribution``)
        * ``_KIND_CLASSIFIER_MODULES`` (``account_projection``, D1b)
        * ``_LOAN_RESOLVER_ENGINE_MODULES`` (``loan_resolver``, D3 -- B-12's
          "wholly unfenced tier", closed)
        * ``_LOAN_PAYMENT_SEAM_MODULES`` (``loan_payment_service``, D3's
          review -- the one reader-allowlisted module outside the defining
          package)
        * ``_RECURRING_DEFINITION_MODULES`` (``recurring_transfer_query``,
          R7d-a -- the loan-payment SETTINGS reads moved here off
          ``loan_payment_service``, and the scope moved with them so a fenced
          module's contents could not leave the fence by changing address)
        * ``_SEAM_PRIVATE_CONTEXT_MODULES`` (``balance_at._context`` -- the
          seam-private ruling D3 keeps: ``BalanceContext`` is publicly
          re-exported, so a new public METHOD on it reaches every route with
          no ``__init__`` edit, and W9910 cannot see attribute access -- and,
          since ruling R-BAL146, ``balance_at._recurrence_memos``, the mixin
          it inherits three of those methods from)
        * ``_LEDGER_LINES_MODULES`` (``ledger_lines``, plan task plan#2526 --
          the worked-out ledger, which took every ledger target rule, two of
          them ruled under ``loan_posting_service``, and the read beside them)

        For those, a set-equality guard would be SELF-ATTESTING: delete the
        registry entry and empty the constant -- two adjacent edits in one
        file, which is exactly the shape of the change that would do it -- and
        the suite stays green.  Measured on an earlier tree: **150 passed**
        with ``account_projection`` dropped from both, and the balance-at-T
        probe that motivated its entry then rated 10.00/10 again.

        **So the module names below are written out LITERALLY, and that is the
        point.**  Binding this loop to the constants would reproduce the hole one
        level down: emptying a constant would make the loop vacuous instead of
        failing.  A behavioural pin that names its subject cannot go quiet.
        """
        for module_name in (
            "app.services.account_projection",
            "app.services.cash_ledger",
            "app.services.row_valuation",
            "app.services.loan_ledger",
            "app.services.loan_walk_reads",
            "app.services.loan_payment_service",
            "app.services.loan_resolver",
            "app.services.recurring_transfer_query",
            "app.services.balance_at._context",
            "app.services.balance_at._recurrence_memos",
            "app.services.ledger_lines",
        ):
            node = self._function_def(
                "def balance_on(account, target):\n    return None\n",
                module_name,
            )
            with self.assertAddsMessages(
                MessageTest(
                    "shekel-unclassified-fenced-export",
                    node=node,
                    args=("balance_on",),
                ),
                ignore_position=True,
            ):
                self.checker.visit_functiondef(node)

    def test_package_scope_covers_a_submodule_that_does_not_exist_yet(
        self,
    ) -> None:
        """A NEW submodule of a scoped PACKAGE is covered the day it is written.

        This is what plan step D1c bought by making ``cash_ledger`` a package
        instead of two flat modules plus five stranded functions, and it is
        worth a behavioural pin rather than trust in
        :func:`_fenced_module_ruling`'s prefix match.

        The hole it closes is Section 8's: *a fail-CLOSED gate is scoped by
        module identity, so creating a module is how you escape it.*  While the
        cash layer was ``cash_events`` + ``period_flows``, its scope was a
        hand-written LIST, and writing ``app/services/cash_amounts.py`` would
        have left that scope silently -- which is exactly how N-28 happened at
        D1a.  A package key is matched by PREFIX, so escaping now means leaving
        the package, a far more visible act than adding a file to it.

        Uses a submodule name that deliberately does NOT exist on disk: the
        checker keys on the enclosing module's NAME, so coverage must not
        depend on the file having been created yet.
        """
        node = self._function_def(
            "def balance_on(account, target):\n    return None\n",
            "app.services.cash_ledger._not_written_yet",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-unclassified-fenced-export",
                node=node,
                args=("balance_on",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_functiondef(node)

    def test_package_scope_stops_at_the_package_boundary(self) -> None:
        """Teeth for the prefix match: a look-alike SIBLING is not scoped.

        The guard above would pass for the wrong reason if the match were a
        bare string prefix, so this pins the trailing-dot half of
        :func:`_module_in_allowlist`'s convention: ``cash_ledger_helpers``
        starts with ``cash_ledger`` but is a different module, outside the
        package, and must not inherit its ruling.

        It also states the residual honestly -- a package scope does NOT cover
        a sibling created beside it.  That is the remaining escape, and it is
        deliberately louder than the one D1c closed: it requires leaving the
        leaf the cash rules live in.
        """
        node = self._function_def(
            "def balance_on(account, target):\n    return None\n",
            "app.services.cash_ledger_helpers",
        )
        with self.assertNoMessages():
            self.checker.visit_functiondef(node)

    def test_rulings_cover_every_producer_defining_module(self) -> None:
        """The ruling registry scopes EXACTLY the declared module scopes.

        The completeness check can only protect a module it scopes, so the scope
        itself needs a guard: if a package were added to a scope constant but
        not given a ruling (or the reverse), W9909 would silently stop covering
        it -- the fail-open hole one level up. Pins the registry's key set
        against the scope constants the D3 residue is built from.  (The
        LITERAL module-name pin lives in
        :meth:`test_flags_unclassified_export_in_every_hand_scoped_module`,
        so emptying a constant AND its entry together still fails.)
        """
        expected = (
            _LOAN_LEDGER_DEFINING_MODULES
            | _LOAN_RESOLVER_ENGINE_MODULES
            | _LOAN_PAYMENT_SEAM_MODULES
            | _SEAM_PRIVATE_CONTEXT_MODULES
            | _CASH_LEDGER_MODULES
            | _KIND_CLASSIFIER_MODULES
            | _ROW_VALUATION_MODULES
            | _RECURRING_DEFINITION_MODULES
            | _LEDGER_LINES_MODULES
        )
        assert set(_FENCED_MODULE_RULINGS) == expected

    def test_classification_sets_match_the_real_fenced_modules(self) -> None:
        """The sets EXACTLY partition the fenced modules' real public surface.

        The checker enforces completeness against whatever the sets say; this
        pins the sets against the actual source on disk, in both directions:

        * every public top-level function AND every public METHOD of a public
          class really defined in a fenced module is classified (no hole the
          checker itself could not see -- e.g. if a module were dropped from the
          scope), and
        * every classified name really exists (no stale entry silently
          un-fencing a name that was renamed).

        **The methods are walked, not just the top-level functions, and that is
        the point.**  This guard previously read only ``tree.body``, so it carried
        the identical blind spot the checker did: ``BalanceContext.loan`` was a
        public method handing routes a ``ResolvedLoan``, and neither the checker
        nor the guard that is supposed to pin the checker's coverage could see it.
        A guard that shares the bug it guards against is not a guard.

        Parses the source with astroid rather than importing ``app`` so the
        checker's unit tests stay free of the Flask/SQLAlchemy import graph.
        """
        for module, (producers, non_producers) in _FENCED_MODULE_RULINGS.items():
            classified = producers | non_producers
            defined: set[str] = set()
            for source_path in _fenced_module_sources(module):
                tree = astroid.parse(
                    source_path.read_text(encoding="utf-8"), module_name=module,
                )
                for name in _public_export_names(tree):
                    defined.add(name)
                    assert name in classified, (
                        f"{module}.{name} is an UNCLASSIFIED public export in a "
                        f"fenced module: add it to the producer set (it answers "
                        f"balance-at-T) or the non-producer set (it does not)"
                    )
            # Only the module's OWN non-producer rulings are pinned for staleness:
            # the producer set is shared across the cluster (a producer defined
            # in one member module is legitimately absent from another).
            stale = non_producers - defined
            assert not stale, (
                f"{module}: non-producer rulings for functions it no longer "
                f"defines: {sorted(stale)} -- a rename or deletion left a stale "
                f"entry, which would un-fence the name it was renamed to"
            )


class TestShekelTransactionStatusBypassChecker(CheckerTestCase):
    """``shekel-transaction-status-bypass``: status_id written only via the seam.

    Every non-transfer ``Transaction.status_id`` change must route through
    ``status_seam.apply_status_change``; only that module
    (``app.services.status_seam``) and ``transfer_service._create`` (which
    CONSTRUCTS a transfer and its two shadow rows) may write it.  Four write forms are
    fenced (H3/R3 of the 2026-07-02 review closed the last three): direct
    assignment, the literal ``setattr`` form, a ``status_id`` payload in a bulk
    ``.update()`` / ``.values()`` call, and a born-settled ``Transaction`` /
    ``Transfer`` constructor kwarg.  Like every module-scoped fence here the
    rule keys off the ENCLOSING module (``node.root().name``), so each snippet is
    parsed inside a named module via :func:`astroid.parse` (``module_name=``).
    Every flagged form is paired with the conforming form that must NOT fire, and
    register-bound loops assert every allowlisted module is exempt from every
    form.
    """

    CHECKER_CLASS = ShekelTransactionStatusBypassChecker

    @staticmethod
    def _status_assign(assign_source: str, module_name: str) -> nodes.AssignAttr:
        """Return the AssignAttr target of *assign_source* parsed in *module_name*.

        The enclosing module's name drives the allowlist check, so it is set
        explicitly.  The snippet is a single attribute assignment, so the module
        body's one statement is an ``Assign`` whose first target is the
        store-context ``AssignAttr`` the checker visits.
        """
        module = astroid.parse(f"{assign_source}\n", module_name=module_name)
        return module.body[0].targets[0]

    def test_flags_status_id_assignment_from_route(self) -> None:
        """A bare status_id assignment in a route module is flagged."""
        node = self._status_assign(
            "txn.status_id = new_status_id",
            "app.routes.transactions.mutations",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_assignattr(node)

    def test_flags_status_id_assignment_from_credit_workflow(self) -> None:
        """A status_id assignment in credit_workflow is flagged (must use the seam)."""
        node = self._status_assign(
            "txn.status_id = credit_id", "app.services.credit_workflow",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_assignattr(node)

    def test_allows_status_id_assignment_in_status_seam(self) -> None:
        """The seam's own module (status_seam) may assign status_id; not flagged."""
        node = self._status_assign(
            "txn.status_id = new_status_id", "app.services.status_seam._seam",
        )
        with self.assertNoMessages():
            self.checker.visit_assignattr(node)

    def test_allows_status_id_assignment_in_transfer_create_leaf(self) -> None:
        """The transfer CREATE leaf may assign status_id; not flagged.

        A name-based checker cannot distinguish a shadow ``Transaction``'s
        ``status_id`` from a real transaction's, so the leaf that constructs the
        pair is allowlisted alongside the seam (2.8b MEDIUM).
        """
        node = self._status_assign(
            "expense_shadow.status_id = new_status_id",
            "app.services.transfer_service._create",
        )
        with self.assertNoMessages():
            self.checker.visit_assignattr(node)

    def test_flags_status_id_assignment_in_a_sibling_transfer_leaf(self) -> None:
        """A transfer-service leaf that is NOT ``_create`` is still fenced.

        The regression guard for plan step X-f2-c3's package move.  The
        allowlist matches a package PREFIX, so an entry naming
        ``app.services.transfer_service`` would have exempted all eight leaves
        of the new package the moment it stopped being one module -- a fence
        widened by a refactor rather than by a decision.  Narrowing the entry to
        the ``_create`` leaf is what this asserts, from the failing direction:
        the status applier reaches the column through the seam and must have no
        exemption of its own.
        """
        node = self._status_assign(
            "expense_shadow.status_id = new_status_id",
            "app.services.transfer_service._status",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_assignattr(node)

    def test_allows_every_seam_module(self) -> None:
        """EVERY allowlisted module may assign status_id directly.

        Binds the test to :data:`_STATUS_SEAM_MODULES` itself, so narrowing the
        set would surface here rather than as a surprise W9907 on a seam module.
        """
        for module_name in sorted(_STATUS_SEAM_MODULES):
            node = self._status_assign(
                "txn.status_id = new_status_id", module_name,
            )
            with self.assertNoMessages():
                self.checker.visit_assignattr(node)

    def test_flags_a_SIBLING_leaf_of_the_seam_package(self) -> None:
        """A seam leaf that is not the allowlisted one is NOT exempt.

        The narrowing of plan step X-au-c3, stated as a test.  ``status_seam``
        became a package there, and the entry named the package until the same
        widening this file already records for ``transfer_service`` was noticed
        a second time: :func:`_module_in_allowlist` matches a package PREFIX, so
        naming the package exempts every leaf inside it from the status-write
        fence -- an allowlist widening nobody decided on.

        The entry names ``_seam``, the one leaf holding ``apply_status_change``.
        ``_record`` (the settlement value and its reads) and ``_refusals`` (the
        invariants as guards) assign nothing, so a ``status_id`` write appearing
        in either is a write that went around the seam and must be flagged.
        """
        for leaf in (
            "app.services.status_seam._record",
            "app.services.status_seam._refusals",
        ):
            node = self._status_assign("txn.status_id = new_status_id", leaf)
            with self.assertAddsMessages(
                MessageTest("shekel-transaction-status-bypass", node=node),
                ignore_position=True,
            ):
                self.checker.visit_assignattr(node)

    def test_allows_a_submodule_of_an_ALLOWLISTED_module(self) -> None:
        """The package-prefix match itself still holds, for the named entry.

        The control for the test above: prefix matching is not broken, it is
        merely pointed at a leaf.  A submodule of an allowlisted module stays
        exempt, which is what lets an entry survive a further split of the
        module it names.
        """
        node = self._status_assign(
            "txn.status_id = new_status_id",
            "app.services.status_seam._seam._mechanics",
        )
        with self.assertNoMessages():
            self.checker.visit_assignattr(node)

    def test_flags_same_basename_in_another_package(self) -> None:
        """A same-named module in another package is NOT exempted (no collision bypass).

        The allowlist keys off the FULL module path, so a hypothetical
        ``app/routes/status_seam.py`` is still flagged.
        """
        node = self._status_assign(
            "txn.status_id = x", "app.routes.status_seam",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_assignattr(node)

    def test_ignores_other_attribute_assignment(self) -> None:
        """Assigning a non-status_id attribute is not this checker's concern."""
        node = self._status_assign(
            "txn.estimated_amount = amount",
            "app.routes.transactions.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_assignattr(node)

    def test_ignores_filing_status_id_assignment(self) -> None:
        """filing_status_id (a different attribute on the tax tables) is not matched."""
        node = self._status_assign(
            "profile.filing_status_id = fs_id",
            "app.services.tax_config_service",
        )
        with self.assertNoMessages():
            self.checker.visit_assignattr(node)

    def test_ignores_status_id_read_in_assigned_value(self) -> None:
        """A status_id READ on the value side is never flagged; only the target counts.

        ``txn.notes = (other.status_id == 5)`` assigns ``notes`` (the
        ``AssignAttr`` the checker visits); the ``other.status_id`` on the value
        side is a load-context ``Attribute`` the visitor never receives, so no
        message fires -- the structural reason a ``status_id ==`` comparison is
        immune.
        """
        node = self._status_assign(
            "txn.notes = (other.status_id == 5)",
            "app.routes.transactions.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_assignattr(node)

    # ── the call-shaped write forms (H3/R3: ctor, setattr, bulk) ──────

    @staticmethod
    def _status_call(call_source: str, module_name: str) -> nodes.Call:
        """Return the Call node of *call_source* parsed in *module_name*.

        Mirrors the seam checker's ``_producer_call``: the snippet is a single
        statement (a plain expression or an assignment), so the module body's
        one statement carries the OUTER call under test as its ``value`` --
        exactly the node a real run dispatches to ``visit_call`` for the
        method-call forms (``.update`` / ``.values``), whose inner calls are
        separate nodes.
        """
        module = astroid.parse(f"{call_source}\n", module_name=module_name)
        return module.body[0].value

    def test_flags_born_settled_ctor(self) -> None:
        """Transaction(status_id=done_id) constructs a born-settled row; flagged.

        A born-settled row would carry NULL paid_at, skip verify_transition,
        and emit no ledger posting -- the H3 failure mode.
        """
        node = self._status_call(
            "txn = Transaction(status_id=done_id, name=name)",
            "app.services.some_import_service",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_ctor_with_settled_ref_cache_lookup(self) -> None:
        """A ref-cache lookup of a NON-Projected member in a ctor is flagged."""
        node = self._status_call(
            "txn = Transaction(status_id=ref_cache.status_id(StatusEnum.PAID))",
            "app.services.some_import_service",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_ctor_with_opaque_status_variable(self) -> None:
        """An unrecognizable ctor status value fails closed and is flagged.

        ``Transaction(status_id=some_status)`` cannot be statically proven
        Projected; the fence's dangerous mode is the false negative, so the
        author must spell the value in a canonical Projected form (the
        ref-cache PROJECTED lookup or a projected_id name) or settle through
        the seam after creation.
        """
        node = self._status_call(
            "txn = Transaction(status_id=some_status)",
            "app.routes.transactions.create",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_transfer_ctor_born_settled(self) -> None:
        """Transfer(status_id=done_id) outside the seam modules is flagged too."""
        node = self._status_call(
            "xfer = Transfer(status_id=done_id)",
            "app.routes.transfers.mutations",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_attribute_form_model_ctor(self) -> None:
        """models.Transaction(status_id=done_id) -- the qualified ctor -- is flagged."""
        node = self._status_call(
            "txn = models.Transaction(status_id=done_id)",
            "app.services.some_import_service",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_allows_ctor_born_projected_ref_cache_lookup(self) -> None:
        """Transaction(status_id=ref_cache.status_id(StatusEnum.PROJECTED)) is the rule; not flagged.

        The credit-workflow / carry-forward create form.
        """
        node = self._status_call(
            "payback = Transaction("
            "status_id=ref_cache.status_id(StatusEnum.PROJECTED), name=name)",
            "app.services.credit_workflow",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_ctor_born_projected_plan_attribute(self) -> None:
        """Transaction(status_id=plan.projected_id) -- the recurrence-engine form -- is not flagged."""
        node = self._status_call(
            "txn = Transaction(status_id=plan.projected_id)",
            "app.services.recurrence_engine",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_ctor_born_projected_bare_name(self) -> None:
        """A bare projected_id local as the ctor status is recognized; not flagged.

        (A Transfer constructed outside transfer_service would violate the
        transfer INVARIANTS -- but that is a different rule with its own
        guards, not the status fence's concern.)
        """
        node = self._status_call(
            "xfer = Transfer(status_id=projected_id)",
            "app.routes.transfers.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_ctor_splat_kwargs(self) -> None:
        """Transaction(**data) carries no named status kwarg; not flagged.

        The create-route form: statically invisible, governed by the
        documented convention (the schema omits status_id and the route
        assigns Projected into the dict unconditionally).
        """
        node = self._status_call(
            "txn = Transaction(**data)",
            "app.routes.transactions.create",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_ctor_without_status_kwarg(self) -> None:
        """A model ctor with no status_id kwarg is not this fence's concern."""
        node = self._status_call(
            "txn = Transaction(name=name, estimated_amount=amount)",
            "app.services.some_import_service",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_transfer_spec_with_any_status(self) -> None:
        """TransferSpec(status_id=...) is a service spec, not a model ctor; not flagged.

        The spec is consumed by transfer_service.create_transfer, which owns
        the actual model construction inside the allowlist.
        """
        node = self._status_call(
            "spec = transfer_service.TransferSpec(status_id=done_id)",
            "app.routes.transfers.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_service_call_with_status_kwarg(self) -> None:
        """update_transfer(..., status_id=...) is the sanctioned service path; not flagged."""
        node = self._status_call(
            "transfer_service.update_transfer("
            "xfer_id, user_id, status_id=cancelled_id)",
            "app.routes.transactions.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_born_settled_ctor_in_transfer_service(self) -> None:
        """transfer_service constructs shadows with the parent's status; exempt.

        Shadow rows are born with spec.status_id / the parent transfer's
        status -- not necessarily Projected -- and transfer_service is the
        sanctioned owner of that construction.
        """
        node = self._status_call(
            "shadow = Transaction(status_id=spec.status_id)",
            "app.services.transfer_service._create",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_flags_setattr_status_literal(self) -> None:
        """setattr(txn, \"status_id\", value) writes the column past the AssignAttr visitor; flagged."""
        node = self._status_call(
            'setattr(txn, "status_id", value)',
            "app.routes.transactions.mutations",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_allows_setattr_status_literal_in_seam(self) -> None:
        """The seam module may use any write form it owns; not flagged."""
        node = self._status_call(
            'setattr(txn, "status_id", new_status_id)',
            "app.services.status_seam._seam",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_setattr_other_literal_field(self) -> None:
        """setattr of a different literal field is not this fence's concern."""
        node = self._status_call(
            'setattr(txn, "notes", value)',
            "app.routes.transactions.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_setattr_dynamic_field_loop(self) -> None:
        """setattr(txn, field, value) -- the schema-loop form -- is not matched.

        Statically invisible: the mutations route's loop excludes status_id
        with a ``continue`` and routes it through the seam; that guard stays
        with review and the route tests, as documented on the checker.
        """
        node = self._status_call(
            "setattr(txn, field, value)",
            "app.routes.transactions.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_flags_query_update_with_status_string_key(self) -> None:
        """A bulk Query.update carrying a \"status_id\" key bypasses the seam; flagged."""
        node = self._status_call(
            "db.session.query(Transaction).update("
            '{"status_id": done_id}, synchronize_session="fetch")',
            "app.routes.templates",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_update_with_column_attribute_key(self) -> None:
        """The column-object key form ({Transaction.status_id: ...}) is flagged too."""
        node = self._status_call(
            "query.update({Transaction.status_id: done_id})",
            "app.services.some_import_service",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_values_with_status_keyword(self) -> None:
        """update(Transaction).values(status_id=...) -- the Core form -- is flagged."""
        node = self._status_call(
            "update(Transaction).values(status_id=done_id)",
            "app.services.some_import_service",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_update_with_dict_builder(self) -> None:
        """query.update(dict(status_id=...)) -- the dict() builder -- is flagged."""
        node = self._status_call(
            "query.update(dict(status_id=done_id))",
            "app.services.some_import_service",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_allows_update_without_status_key(self) -> None:
        """The in-tree bulk soft-delete form (is_deleted only) is not flagged."""
        node = self._status_call(
            'query.update({"is_deleted": True}, synchronize_session="fetch")',
            "app.routes.templates",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_update_with_filing_status_key(self) -> None:
        """filing_status_id (the tax tables) is a different key; not matched."""
        node = self._status_call(
            'query.update({"filing_status_id": fs_id})',
            "app.services.tax_config_service",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_dict_update_with_non_literal_payload(self) -> None:
        """context.update(extra) -- a payload passed by name -- is not statically visible.

        The in-tree plain-dict merge form; a bulk-write payload built away
        from the call is a documented residual, not a false positive.
        """
        node = self._status_call(
            "context.update(extra_context)",
            "app.routes.settings",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_every_seam_module_for_call_forms(self) -> None:
        """EVERY allowlisted module is exempt from the call-shaped forms too.

        The companion to test_allows_every_seam_module: binds the call-form
        exemption to :data:`_STATUS_SEAM_MODULES` itself, using the strictest
        form (a born-settled ctor).
        """
        for module_name in sorted(_STATUS_SEAM_MODULES):
            node = self._status_call(
                "txn = Transaction(status_id=done_id)", module_name,
            )
            with self.assertNoMessages():
                self.checker.visit_call(node)


class TestShekelLedgerModelFenceChecker(CheckerTestCase):
    """``shekel-ledger-model-bypass``: ledger models imported only by the models.

    Every stored-ledger row model (``Posting`` / ``JournalEntry`` /
    ``LedgerAccount``) may be imported only by the models layer
    (:data:`_LEDGER_MODEL_ALLOWLIST`; the posting-ledger write core and its
    readers until plan task plan#3133 deleted them); every other module reads
    the ledger worked out from the records instead. Like the W9907 status fence the
    rule keys off the ENCLOSING module (``node.root().name``), so each snippet is
    parsed inside a named module via :func:`astroid.parse` (``module_name=``).
    Both fence axes are exercised: the NAME axis (a model class imported from the
    package re-export -- F-1 -- the defining submodule, a relative path, or a
    laundering re-export) and the MODULE axis (a defining submodule bound by
    ``from app.models.journal_entry import ...``, by name off the package
    ``from app.models import journal_entry``, or by plain ``import`` via
    ``visit_import``). Each flagged form is paired with the conforming form that
    must NOT fire, and register-bound loops assert the fence covers EVERY fenced
    module/name and EVERY allowlisted module, so a narrowed allowlist or a
    dropped model surfaces here.
    """

    CHECKER_CLASS = ShekelLedgerModelFenceChecker

    @staticmethod
    def _importfrom_node(import_source: str, module_name: str) -> nodes.ImportFrom:
        """Return the ImportFrom node for *import_source* parsed in *module_name*.

        The enclosing module's name drives the allowlist check, so it is set
        explicitly; the snippet is a single ``from`` import, so the module body's
        one statement is the ``ImportFrom`` under test.
        """
        module = astroid.parse(f"{import_source}\n", module_name=module_name)
        return module.body[0]

    @staticmethod
    def _import_node(import_source: str, module_name: str) -> nodes.Import:
        """Return the Import node for *import_source* parsed in *module_name*.

        As :meth:`_importfrom_node`, but for a plain ``import ...`` statement,
        whose single body statement is the ``Import`` node the checker visits.
        """
        module = astroid.parse(f"{import_source}\n", module_name=module_name)
        return module.body[0]

    # ── the defining-submodule shape (from app.models.<mod> import ...) ──

    def test_flags_submodule_import_from_consumer(self) -> None:
        """``from app.models.journal_entry import Posting`` in a route is flagged."""
        node = self._importfrom_node(
            "from app.models.journal_entry import Posting",
            "app.routes.grid",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-ledger-model-bypass",
                node=node,
                args=("app.models.journal_entry",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_submodule_import_regardless_of_name(self) -> None:
        """A NON-model name from a fenced submodule is flagged too.

        The whole model-bearing submodule is fenced, so importing anything from
        it (here the immutability-error class) reaches ledger-internal territory
        and is reported -- the fence keys off the source module, not the name.
        """
        node = self._importfrom_node(
            "from app.models.journal_entry import JournalEntryImmutableError",
            "app.routes.grid",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-ledger-model-bypass",
                node=node,
                args=("app.models.journal_entry",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_every_fenced_submodule_from_consumer(self) -> None:
        """EVERY module in _LEDGER_MODEL_MODULES is flagged when imported from a consumer.

        Binds the fence to the fenced-module set itself, so a submodule added to
        (or dropped from) the frozenset is automatically covered.
        """
        for modname in sorted(_LEDGER_MODEL_MODULES):
            node = self._importfrom_node(
                f"from {modname} import SomeName", "app.routes.grid",
            )
            with self.assertAddsMessages(
                MessageTest(
                    "shekel-ledger-model-bypass", node=node, args=(modname,),
                ),
                ignore_position=True,
            ):
                self.checker.visit_importfrom(node)

    # ── the package re-export shape (from app.models import Posting), F-1 ──

    def test_flags_package_reexport_name_from_consumer(self) -> None:
        """``from app.models import Posting`` in a consumer is flagged (the F-1 shape).

        The class imported off the ``app.models`` package rather than its
        defining submodule -- the shape a module-path-only fence would miss.
        """
        node = self._importfrom_node(
            "from app.models import Posting", "app.routes.grid",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-ledger-model-bypass", node=node, args=("Posting",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_every_reexport_name_from_consumer(self) -> None:
        """EVERY name in _LEDGER_MODEL_NAMES is flagged as an app.models re-export.

        Binds the F-1 fence to the class-name set, so a model added to (or
        dropped from) the frozenset is automatically covered.
        """
        for name in sorted(_LEDGER_MODEL_NAMES):
            node = self._importfrom_node(
                f"from app.models import {name}", "app.routes.grid",
            )
            with self.assertAddsMessages(
                MessageTest(
                    "shekel-ledger-model-bypass", node=node, args=(name,),
                ),
                ignore_position=True,
            ):
                self.checker.visit_importfrom(node)

    def test_flags_multi_name_reexport_reports_each(self) -> None:
        """One ``from app.models import`` naming two ledger models reports both."""
        node = self._importfrom_node(
            "from app.models import Posting, JournalEntry", "app.routes.grid",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-ledger-model-bypass", node=node, args=("Posting",),
            ),
            MessageTest(
                "shekel-ledger-model-bypass", node=node, args=("JournalEntry",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_allows_non_ledger_name_from_app_models(self) -> None:
        """``from app.models import Transaction`` is NOT flagged (not a ledger model).

        The ``app.models`` package re-exports dozens of models; only the three
        ledger row-model names -- and the two ledger submodule leaf names --
        trigger, so an ordinary model import is free.
        """
        node = self._importfrom_node(
            "from app.models import Transaction", "app.routes.grid",
        )
        with self.assertNoMessages():
            self.checker.visit_importfrom(node)

    # ── the submodule-bound-by-name shape (from app.models import journal_entry) ──

    def test_flags_submodule_bound_by_name_from_package(self) -> None:
        """``from app.models import journal_entry`` in a consumer is flagged.

        The submodule bound BY NAME off the package (then reached as
        ``journal_entry.Posting``) -- the MODULE-axis twin of the F-1 class
        re-export, and the shape the loan oracle's own detector already treats as
        ledger-reaching. Missing it would leave the production fence weaker than
        the test-side one.
        """
        node = self._importfrom_node(
            "from app.models import journal_entry", "app.routes.grid",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-ledger-model-bypass",
                node=node,
                args=("journal_entry",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_every_leaf_module_name_from_package(self) -> None:
        """EVERY leaf submodule name is flagged as an app.models by-name import.

        Binds the MODULE-by-name fence to :data:`_LEDGER_LEAF_MODULE_NAMES`, so a
        submodule added to (or dropped from) the fenced-module set is covered.
        """
        for leaf in sorted(_LEDGER_LEAF_MODULE_NAMES):
            node = self._importfrom_node(
                f"from app.models import {leaf}", "app.routes.grid",
            )
            with self.assertAddsMessages(
                MessageTest(
                    "shekel-ledger-model-bypass", node=node, args=(leaf,),
                ),
                ignore_position=True,
            ):
                self.checker.visit_importfrom(node)

    # ── the NAME axis: immune to module-path (relative, laundering) ──

    def test_flags_relative_class_import_from_consumer(self) -> None:
        """A RELATIVE import of a model class is flagged (the NAME axis).

        ``from ..models.journal_entry import Posting`` resolves to the real model
        at runtime, but astroid's ``node.modname`` is the relative fragment
        (``models.journal_entry``), matching no absolute module path. Keying on
        the imported NAME catches it regardless of the path it came through.
        """
        node = self._importfrom_node(
            "from ..models.journal_entry import Posting",
            "app.services.savings_dashboard_service",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-ledger-model-bypass", node=node, args=("Posting",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_class_import_laundered_through_reexport(self) -> None:
        """Importing a model class from a NON-app.models re-export is flagged.

        A consumer cannot launder the model past the fence by importing it from
        some other module that happens to re-export it -- the NAME axis flags
        ``Posting`` wherever it is imported from.
        """
        node = self._importfrom_node(
            "from app.services.cash_ledger import Posting", "app.routes.grid",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-ledger-model-bypass", node=node, args=("Posting",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_allows_relative_non_ledger_import(self) -> None:
        """A relative import of a NON-ledger name is NOT flagged.

        The NAME axis fires only on the three distinctive ledger class names, so
        an ordinary relative import (the intra-package idiom) is free -- no false
        positive from broadening the fence to catch relative class imports.
        """
        node = self._importfrom_node(
            "from ..models.transaction import Transaction",
            "app.services.savings_dashboard_service",
        )
        with self.assertNoMessages():
            self.checker.visit_importfrom(node)

    def test_allows_non_ledger_submodule_from_consumer(self) -> None:
        """``from app.models.transaction import Transaction`` is NOT flagged.

        Only the two model-bearing ledger submodules are fenced; another model's
        submodule is imported freely.
        """
        node = self._importfrom_node(
            "from app.models.transaction import Transaction", "app.routes.grid",
        )
        with self.assertNoMessages():
            self.checker.visit_importfrom(node)

    # ── the plain-module-import shape (import app.models.journal_entry) ──

    def test_flags_plain_module_import_from_consumer(self) -> None:
        """``import app.models.ledger_account`` in a consumer is flagged.

        The evasion a ``from``-only fence would miss: the model is reached as
        ``app.models.ledger_account.LedgerAccount`` with no importable name for a
        later pass to catch, so the fence binds at the plain import.
        """
        node = self._import_node(
            "import app.models.ledger_account", "app.routes.grid",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-ledger-model-bypass",
                node=node,
                args=("app.models.ledger_account",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_import(node)

    def test_flags_every_fenced_submodule_plain_import(self) -> None:
        """EVERY fenced submodule is flagged as a plain ``import`` from a consumer."""
        for modname in sorted(_LEDGER_MODEL_MODULES):
            node = self._import_node(f"import {modname}", "app.routes.grid")
            with self.assertAddsMessages(
                MessageTest(
                    "shekel-ledger-model-bypass", node=node, args=(modname,),
                ),
                ignore_position=True,
            ):
                self.checker.visit_import(node)

    def test_allows_plain_import_of_non_ledger_module(self) -> None:
        """``import app.models.transaction`` is NOT flagged (not a ledger submodule)."""
        node = self._import_node(
            "import app.models.transaction", "app.routes.grid",
        )
        with self.assertNoMessages():
            self.checker.visit_import(node)

    def test_allows_bare_app_models_package_import(self) -> None:
        """``import app.models`` (the bare package) is NOT flagged.

        The accepted boundary of the fence, matching the plan's scope ("by module
        path app.models.journal_entry / app.models.ledger_account, or by name
        from app.models") and the loan oracle's own ledger-import detector: the
        fence targets the model-bearing submodules and the by-name re-export, not
        a bare-package import. Reaching a model as ``app.models.Posting`` after a
        bare-package import is not an idiom in the tree; fencing it would false-
        positive on every ordinary ``import app.models``.
        """
        node = self._import_node("import app.models", "app.routes.grid")
        with self.assertNoMessages():
            self.checker.visit_import(node)

    # ── full-path allowlist: fail-closed + no basename collision ──

    def test_flags_import_in_unresolvable_module_fails_closed(self) -> None:
        """An empty / unresolvable enclosing module fails closed: the import is flagged."""
        node = self._importfrom_node(
            "from app.models.journal_entry import Posting", "",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-ledger-model-bypass",
                node=node,
                args=("app.models.journal_entry",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_same_basename_in_another_package(self) -> None:
        """A same-BASENAME module in a non-allowlisted package is still flagged.

        The allowlist matches the FULL module path, so a hypothetical
        ``app/routes/models.py`` -- basename ``models``, which IS the
        allowlisted name ``app.models`` -- must NOT be exempted by
        the collision. This is the false-negative a basename-only match allows.
        """
        node = self._importfrom_node(
            "from app.models import Posting", "app.routes.models",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-ledger-model-bypass", node=node, args=("Posting",),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    # ── the allowlist exemptions (all three shapes) ──

    def test_allows_submodule_import_from_every_allowlisted_module(self) -> None:
        """Each allowlisted module may import a ledger model by its defining submodule.

        Binds the exemption to :data:`_LEDGER_MODEL_ALLOWLIST` itself, so
        narrowing the set surfaces here rather than as a surprise W9908 on a
        model module.
        """
        for module_name in sorted(_LEDGER_MODEL_ALLOWLIST):
            node = self._importfrom_node(
                "from app.models.journal_entry import Posting", module_name,
            )
            with self.assertNoMessages():
                self.checker.visit_importfrom(node)

    def test_allows_reexport_from_every_allowlisted_module(self) -> None:
        """Each allowlisted module may import a ledger model by the app.models re-export."""
        for module_name in sorted(_LEDGER_MODEL_ALLOWLIST):
            node = self._importfrom_node(
                "from app.models import Posting", module_name,
            )
            with self.assertNoMessages():
                self.checker.visit_importfrom(node)

    def test_allows_plain_import_from_every_allowlisted_module(self) -> None:
        """Each allowlisted module may import a fenced ledger submodule plainly."""
        for module_name in sorted(_LEDGER_MODEL_ALLOWLIST):
            node = self._import_node(
                "import app.models.journal_entry", module_name,
            )
            with self.assertNoMessages():
                self.checker.visit_import(node)

    def test_allows_import_from_allowlisted_package_submodule(self) -> None:
        """A submodule of an allowlisted PACKAGE stays inside the fence (prefix match).

        The models layer is allowlisted by prefix, so its submodules must
        remain exempt -- the package-prefix arm of :func:`_module_in_allowlist`.
        The loan / account posting packages and the chart resolver package
        were the examples until plan task plan#3133 deleted them, and the
        report package one until plan task plan#2771 moved the statements onto
        the worked-out ledger and the fence tightened past it
        (:meth:`test_flags_the_report_package_since_it_reads_lines`).
        """
        for module_name in (
            "app.models.ledger_account",
            "app.models.account",
            "app.models.transaction_entry",
        ):
            node = self._importfrom_node(
                "from app.models.journal_entry import Posting, JournalEntry",
                module_name,
            )
            with self.assertNoMessages():
                self.checker.visit_importfrom(node)

    def test_flags_the_report_package_since_it_reads_lines(self) -> None:
        """The statements' package is outside the fence (plan task plan#2771).

        It read the stored journal until X-ew's second leaf moved both
        statements onto the worked-out ledger, which reads no ledger model;
        the allowlist dropped it, so a ledger model imported there again is
        flagged, at the package and at the submodule that read the store.
        """
        for module_name in (
            "app.services.ledger_report_service",
            "app.services.ledger_report_service._attribution",
        ):
            node = self._importfrom_node(
                "from app.models.journal_entry import Posting", module_name,
            )
            with self.assertAddsMessages(
                MessageTest(
                    "shekel-ledger-model-bypass",
                    node=node,
                    args=("app.models.journal_entry",),
                ),
                ignore_position=True,
            ):
                self.checker.visit_importfrom(node)


# ── shekel-private-module-import (W9910) fixtures ──


@pytest.fixture(scope="module", name="privacy_fixture_root")
def _privacy_fixture_root(tmp_path_factory: pytest.TempPathFactory):
    """On-disk fixture packages for the resolution-dependent privacy tests.

    The W9910 rule is mostly pure string analysis, but two behaviors depend on
    astroid RESOLVING real modules: the ``from P import _x`` module-vs-name
    split, and the physical-membership second chance for namespace packages.
    Testing those against the live ``app`` tree would couple the checker tests
    to the application's import graph (and to ``app`` being importable at all),
    so a hermetic fixture tree is built once per module and put on ``sys.path``:

    * ``dgate_res_pkg`` -- a REGULAR package: ``__init__`` defines a private
      NAME, ``_engine`` is a private MODULE (which itself defines a private
      NAME, so the intra-package "private name out of a private sibling
      module" form has a real target to resolve), ``public_mod`` is a public
      module defining a private NAME.
    * ``dgate_res_ns`` -- a NAMESPACE package (no ``__init__.py``) holding a
      private module, mirroring ``scripts/_script_lib.py``, plus a private
      subPACKAGE (``_libpkg``) whose boundary a namespace sibling must NOT
      inherit membership of (the per-boundary rule).

    The fixture names are used ONLY as resolution targets, never as a parsed
    module's ``module_name`` -- :func:`astroid.parse` registers string-built
    modules in the manager cache under their given name (with the ``"<?>"``
    placeholder file), so reusing a name in both roles would let one test's
    fake module shadow another test's on-disk resolution, making outcomes
    order-dependent. The pure string-rule tests use the disjoint
    ``dgate_probe_*`` names, which never exist on disk and are never resolved.

    Yields:
        The root directory holding both fixture packages.
    """
    root = tmp_path_factory.mktemp("dgate_fixture")
    pkg = root / "dgate_res_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "_package_private_name = object()\n", encoding="utf-8",
    )
    (pkg / "_engine.py").write_text(
        "def build_balance_map():\n    return {}\n\n\n"
        "def _engine_private_helper():\n    return None\n",
        encoding="utf-8",
    )
    (pkg / "public_mod.py").write_text(
        "_module_private_name = object()\n", encoding="utf-8",
    )
    namespace = root / "dgate_res_ns"
    (namespace / "_libpkg").mkdir(parents=True)
    (namespace / "_ns_lib.py").write_text(
        "def helper():\n    return 1\n", encoding="utf-8",
    )
    (namespace / "_libpkg" / "__init__.py").write_text("", encoding="utf-8")
    (namespace / "_libpkg" / "_deep.py").write_text(
        "def deep_helper():\n    return 2\n", encoding="utf-8",
    )
    # Both probe files exist on disk: the physical-membership test only honors
    # REAL files (astroid's "<?>" placeholder must never suppress a finding),
    # so the outsider control must fail on DIRECTORY containment, not on a
    # missing file.
    (namespace / "sibling_tool.py").write_text(
        "from dgate_res_ns._ns_lib import helper\n", encoding="utf-8",
    )
    (root / "outsider.py").write_text(
        "from dgate_res_ns._ns_lib import helper\n", encoding="utf-8",
    )
    sys.path.insert(0, str(root))
    yield root
    sys.path.remove(str(root))


class TestShekelPackagePrivacyChecker(CheckerTestCase):
    """``shekel-private-module-import``: a package's private modules are private.

    The balance arc's D-gate (docs/audits/balance_architecture/README.md): a
    module outside package ``P`` may not import ``P._x``, nor any name from
    it, in any spelling -- including ``from P._x import name``, the form the
    stock ``import-private-name`` extension is fail-open for (finding N-26),
    and imports under ``if TYPE_CHECKING:`` (finding N-25's shape). The rule
    keys off the ENCLOSING module name (plus the physical-file second chance
    for namespace packages), so each case is parsed inside a named module via
    :func:`astroid.parse`. Every flagged form is paired with the conforming
    form that must NOT fire.
    """

    CHECKER_CLASS = ShekelPackagePrivacyChecker

    @staticmethod
    def _import_statement(
        source: str,
        module_name: str,
        path: "str | None" = None,
        is_package: bool = False,
    ) -> nodes.NodeNG:
        """Return the last statement of *source* parsed inside *module_name*.

        Args:
            source: Python source whose final statement is the import under test.
            module_name: Dotted name given to the enclosing module.
            path: Optional file path recorded on the module (drives the
                physical-membership test).
            is_package: Mark the module as a package ``__init__`` (drives
                relative-import resolution depth).

        Returns:
            The final top-level statement node (an Import or ImportFrom).
        """
        module = astroid.parse(source, module_name=module_name, path=path)
        if is_package:
            module.package = True
        return module.body[-1]

    # ── the N-26 hole and its siblings: every spelling is flagged ──

    def test_flags_from_private_module_import_name(self) -> None:
        """``from P._x import name`` from outside P is flagged -- the N-26 form.

        This exact spelling rates 10.00/10 under the stock
        ``import-private-name`` extension (measured on pylint 4.0.5), and it is
        the form the seam's private engine modules are exposed to.
        """
        node = self._import_statement(
            "from app.services.balance_at._kernel import build_account_balance_map",
            "app.routes.grid",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-private-module-import",
                node=node,
                args=(
                    "app.services.balance_at._kernel",
                    "app.services.balance_at",
                ),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_aliased_from_import(self) -> None:
        """Aliasing the imported name does not evade the fence."""
        node = self._import_statement(
            "from app.services.balance_at._kernel import "
            "build_account_balance_map as calc",
            "app.routes.grid",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-private-module-import",
                node=node,
                args=(
                    "app.services.balance_at._kernel",
                    "app.services.balance_at",
                ),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_from_package_import_private_module(
        self, privacy_fixture_root: Path,
    ) -> None:
        """``from P import _x`` where ``_x`` IS a module of P is flagged.

        The spelling is ambiguous between a private submodule (fenced) and a
        private name defined in P's ``__init__`` (out of scope); astroid
        resolution decides, and here ``_engine.py`` exists on disk.
        """
        assert privacy_fixture_root.is_dir()
        node = self._import_statement(
            "from dgate_res_pkg import _engine", "consumer_outside",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-private-module-import",
                node=node,
                args=("dgate_res_pkg._engine", "dgate_res_pkg"),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_plain_import(self) -> None:
        """``import P._x`` from outside P is flagged at the Import node."""
        node = self._import_statement(
            "import app.services.balance_at._kernel", "app.routes.grid",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-private-module-import",
                node=node,
                args=(
                    "app.services.balance_at._kernel",
                    "app.services.balance_at",
                ),
            ),
            ignore_position=True,
        ):
            self.checker.visit_import(node)

    def test_flags_aliased_plain_import_reports_each_name(self) -> None:
        """``import P._x as y, P._z`` reports EVERY violating dotted path."""
        node = self._import_statement(
            "import app.services.balance_at._fold as fold, "
            "app.services.balance_at._plan",
            "app.routes.grid",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-private-module-import",
                node=node,
                args=(
                    "app.services.balance_at._fold",
                    "app.services.balance_at",
                ),
            ),
            MessageTest(
                "shekel-private-module-import",
                node=node,
                args=(
                    "app.services.balance_at._plan",
                    "app.services.balance_at",
                ),
            ),
            ignore_position=True,
        ):
            self.checker.visit_import(node)

    def test_flags_type_checking_import(self) -> None:
        """An import under ``if TYPE_CHECKING:`` is NOT exempt (N-25's shape).

        A public signature typed from another package's private module is a
        boundary leak; the D-gate ruling explicitly forbids the exemption, and
        this test pins that the checker sees the nested ImportFrom like any
        other.
        """
        module = astroid.parse(
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from app.services.balance_at._context import BalanceContext\n",
            module_name="app.routes.grid",
        )
        node = module.body[-1].body[0]
        with self.assertAddsMessages(
            MessageTest(
                "shekel-private-module-import",
                node=node,
                args=(
                    "app.services.balance_at._context",
                    "app.services.balance_at",
                ),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_nested_private_subpackage_at_first_crossing(self) -> None:
        """A path through a private subPACKAGE reports the FIRST boundary crossed."""
        node = self._import_statement(
            "from dgate_probe_pkg._sub.leaf import VALUE", "consumer_outside",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-private-module-import",
                node=node,
                args=("dgate_probe_pkg._sub", "dgate_probe_pkg"),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_sibling_package_prefix_collision(self) -> None:
        """A package whose name merely EXTENDS the owner's is outside it.

        ``app.services.balance_at2`` must not inherit ``app.services.balance_at``'s
        membership -- the trailing-dot rule every fence in this plugin carries.
        """
        node = self._import_statement(
            "from app.services.balance_at._kernel import build_account_balance_map",
            "app.services.balance_at2.helper",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-private-module-import",
                node=node,
                args=(
                    "app.services.balance_at._kernel",
                    "app.services.balance_at",
                ),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_relative_import_crossing_packages(self) -> None:
        """A RELATIVE import that climbs into a sibling package's private is flagged.

        In ``app.services.other_pkg.helper`` (a module of the package
        ``app.services.other_pkg``), two leading dots name ``app.services``, so
        the clause resolves to ``app.services.balance_at._kernel`` -- a privacy
        crossing exactly like its absolute twin.
        """
        node = self._import_statement(
            "from ..balance_at._kernel import build_account_balance_map",
            "app.services.other_pkg.helper",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-private-module-import",
                node=node,
                args=(
                    "app.services.balance_at._kernel",
                    "app.services.balance_at",
                ),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_unknown_importer_fails_closed(self) -> None:
        """An importer with no module name is inside nothing: the import flags."""
        node = self._import_statement(
            "from app.services.balance_at._kernel import build_account_balance_map",
            "",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-private-module-import",
                node=node,
                args=(
                    "app.services.balance_at._kernel",
                    "app.services.balance_at",
                ),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_unresolvable_relative_fails_closed(self) -> None:
        """A relative import that cannot be resolved still flags its private target."""
        node = self._import_statement(
            "from ._engine import build_balance_map", "",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-private-module-import",
                node=node,
                args=("._engine", "<unresolvable>"),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_namespace_package_reach_from_outside(
        self, privacy_fixture_root: Path,
    ) -> None:
        """A file OUTSIDE a namespace package's directory may not import its private.

        The control for the physical-membership exemption below: same import,
        same owner, and the importing file EXISTS on disk -- but outside
        ``dgate_res_ns/`` -- so the suppression must fail on directory
        containment itself, and the crossing fires.
        """
        node = self._import_statement(
            "from dgate_res_ns._ns_lib import helper",
            "outsider",
            path=str(privacy_fixture_root / "outsider.py"),
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-private-module-import",
                node=node,
                args=("dgate_res_ns._ns_lib", "dgate_res_ns"),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_namespace_sibling_reaching_private_subpackage(
        self, privacy_fixture_root: Path,
    ) -> None:
        """Physical membership is PER BOUNDARY: a sibling is not inside ``_libpkg``.

        This step's adversarial review demonstrated the evasion on the first
        draft: a statement-wide physical suppression let a namespace sibling
        reach ``P._libpkg._deep`` silently where the regular-package twin
        flags. The sibling's file sits inside ``dgate_res_ns`` but NOT inside
        ``dgate_res_ns/_libpkg``, so the deeper boundary must still fire.
        """
        node = self._import_statement(
            "from dgate_res_ns._libpkg._deep import deep_helper",
            "sibling_tool",
            path=str(privacy_fixture_root / "dgate_res_ns" / "sibling_tool.py"),
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-private-module-import",
                node=node,
                args=("dgate_res_ns._libpkg._deep", "dgate_res_ns._libpkg"),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_namespace_sibling_binding_private_submodule(
        self, privacy_fixture_root: Path,
    ) -> None:
        """When the base crossing dissolves, the name scan must still run.

        ``from dgate_res_ns._libpkg import _deep`` from a namespace sibling:
        the base's ``_libpkg`` boundary is dissolved by physical membership of
        ``dgate_res_ns``, so the statement must FALL THROUGH to the
        ``from P import _x`` scan -- where ``_deep`` resolves as a module of a
        package the importer is NOT inside, and flags. The review's second
        demonstrated evasion (the early return skipped this scan).
        """
        node = self._import_statement(
            "from dgate_res_ns._libpkg import _deep",
            "sibling_tool",
            path=str(privacy_fixture_root / "dgate_res_ns" / "sibling_tool.py"),
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-private-module-import",
                node=node,
                args=("dgate_res_ns._libpkg._deep", "dgate_res_ns._libpkg"),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_private_name_from_unresolvable_package(self) -> None:
        """An unresolvable ``from`` base presumes the private target is a module.

        The ratified fail-closed direction of the module-vs-name split: when
        ``dgate_probe_missing`` resolves nowhere (not on disk, never parsed),
        the checker cannot prove ``_x`` is a mere name, so it must flag --
        inverting this presumption is exactly the N-26-style fail-open
        regression the review measured the first draft's tests blind to.
        """
        node = self._import_statement(
            "from dgate_probe_missing import _x", "consumer_outside",
        )
        with self.assertAddsMessages(
            MessageTest(
                "shekel-private-module-import",
                node=node,
                args=("dgate_probe_missing._x", "dgate_probe_missing"),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    def test_flags_unresolvable_relative_private_name(self) -> None:
        """The unresolvable fail-closed path also covers a private imported NAME.

        ``from . import _engine`` under an unknown importer: the base cannot
        be resolved and the private thing is the NAME, so the name arm of
        ``_flag_unresolvable`` must report it (deleting that loop previously
        kept the whole suite green -- this is its discriminating observer).
        """
        node = self._import_statement("from . import _engine", "")
        with self.assertAddsMessages(
            MessageTest(
                "shekel-private-module-import",
                node=node,
                args=("._engine", "<unresolvable>"),
            ),
            ignore_position=True,
        ):
            self.checker.visit_importfrom(node)

    # ── the conforming forms: intra-package use is the point of privacy ──

    def test_allows_intra_package_absolute_import(self) -> None:
        """A module inside the package may import its package's private module."""
        node = self._import_statement(
            "from dgate_probe_pkg._engine import build_balance_map",
            "dgate_probe_pkg.public_mod",
        )
        with self.assertNoMessages():
            self.checker.visit_importfrom(node)

    def test_allows_seam_submodules_importing_each_other(
        self, privacy_fixture_root: Path,
    ) -> None:
        """A package's private modules compose each other freely, both spellings.

        The production shape this exists for is
        ``app/services/balance_at/_plan.py`` --
        ``from ._memoize import _memoize_once, require_scenario`` (spelled
        against ``._context`` until recurrence:R7d-f-2 moved the two
        primitives) -- a private NAME imported out of a private SIBLING module
        of the importer's own package.  It is the one conforming form that reaches
        the checker's name scan: the ``from`` clause's boundary dissolves on
        dotted-name membership, the importer is not the base module itself,
        and the imported name IS private, so ``_names_a_module`` has to
        RESOLVE the base and find it is a module rather than a package.

        **It is spelled against the hermetic fixture tree, and that is finding
        N-45's repair** (balance plan step X-h).  It used to be spelled with
        the real ``app.services.balance_at`` names, which this directory's
        rootdir cannot import -- so the resolution failed, the checker
        fail-CLOSED as designed, and the assertion fired whenever this class
        ran alone (measured: 1 failed / 30 passed).  It read green in a
        whole-file run only because ``TestShekelBalanceSeamChecker`` parses a
        synthetic module under the real name ``app.services.balance_at._context``
        earlier in the file and warmed astroid's cache.  The plan's proposed
        fix -- give the synthetic importer a real ``path=`` -- was measured and
        does NOT work: ``_importer_file_inside`` has to resolve the same
        unresolvable name, so the message is emitted with or without the path.
        The fixture tree exists for precisely this class of test, and
        ``conftest._isolate_astroid_module_cache`` now stops any test being
        green on a sibling's cache.

        Both spellings are pinned because they reach the base through
        different code (``_absolute_base``'s relative branch resolves the dots
        against the importer's own name), and production uses the relative one.
        """
        assert privacy_fixture_root.is_dir()
        for source in (
            "from dgate_res_pkg._engine import _engine_private_helper",
            "from ._engine import _engine_private_helper",
        ):
            node = self._import_statement(source, "dgate_res_pkg.composing_mod")
            with self.assertNoMessages():
                self.checker.visit_importfrom(node)

    def test_allows_own_init_importing_private_module(self) -> None:
        """A package ``__init__`` may bind its own private submodules absolutely."""
        node = self._import_statement(
            "from dgate_probe_pkg import _engine", "dgate_probe_pkg",
        )
        with self.assertNoMessages():
            self.checker.visit_importfrom(node)

    def test_allows_subpackage_module_reaching_up(self) -> None:
        """A module nested deeper inside the package is still inside it."""
        node = self._import_statement(
            "from dgate_probe_pkg import _engine", "dgate_probe_pkg._sub.leaf",
        )
        with self.assertNoMessages():
            self.checker.visit_importfrom(node)

    def test_allows_relative_sibling_module(self) -> None:
        """``from . import _sibling`` is inherently intra-package."""
        node = self._import_statement(
            "from . import _engine", "dgate_probe_pkg.public_mod",
        )
        with self.assertNoMessages():
            self.checker.visit_importfrom(node)

    def test_allows_relative_import_from_package_init(self) -> None:
        """``from . import _x`` in a package ``__init__`` resolves to the package itself.

        Discriminates the ``Module.package`` branch of the relative resolver:
        without it the ``__init__`` would resolve one level too high and the
        import would be flagged as unresolvable.
        """
        node = self._import_statement(
            "from . import _engine", "dgate_probe_pkg", is_package=True,
        )
        with self.assertNoMessages():
            self.checker.visit_importfrom(node)

    def test_allows_relative_climb_within_package(self) -> None:
        """``from .._x import name`` stays legal while it stays inside the package."""
        node = self._import_statement(
            "from .._engine import build_balance_map", "dgate_probe_pkg._sub.leaf",
        )
        with self.assertNoMessages():
            self.checker.visit_importfrom(node)

    def test_allows_private_name_from_public_module(
        self, privacy_fixture_root: Path,
    ) -> None:
        """A private NAME defined in a public module is out of this rule's scope.

        ``_module_private_name`` is a name in ``public_mod.py``, not a module,
        so the module-vs-name resolution must NOT flag it -- package-private
        names are a convention this rule deliberately leaves alone (the checker
        docstring records the boundary; the measured tree relies on it).
        """
        assert privacy_fixture_root.is_dir()
        node = self._import_statement(
            "from dgate_res_pkg.public_mod import _module_private_name",
            "consumer_outside",
        )
        with self.assertNoMessages():
            self.checker.visit_importfrom(node)

    def test_allows_private_name_from_package_init(
        self, privacy_fixture_root: Path,
    ) -> None:
        """A private NAME defined in a package ``__init__`` is not a module either."""
        assert privacy_fixture_root.is_dir()
        node = self._import_statement(
            "from dgate_res_pkg import _package_private_name",
            "consumer_outside",
        )
        with self.assertNoMessages():
            self.checker.visit_importfrom(node)

    def test_allows_top_level_private_modules(self) -> None:
        """``__future__`` / ``_thread`` have no owning package: never flagged."""
        future_node = self._import_statement(
            "from __future__ import annotations", "app.routes.grid",
        )
        with self.assertNoMessages():
            self.checker.visit_importfrom(future_node)
        thread_node = self._import_statement(
            "import _thread", "app.routes.grid",
        )
        with self.assertNoMessages():
            self.checker.visit_import(thread_node)

    def test_allows_dunder_module_segment(self) -> None:
        """A dunder segment (``__main__``) is public by convention, not private."""
        node = self._import_statement(
            "import dgate_probe_pkg.__main__", "consumer_outside",
        )
        with self.assertNoMessages():
            self.checker.visit_import(node)

    def test_allows_namespace_package_sibling_by_file(
        self, privacy_fixture_root: Path,
    ) -> None:
        """A file INSIDE a namespace package's directory is a member of it.

        The ``scripts/`` case: pylint names ``scripts/rotate_sessions.py`` as
        the TOP-LEVEL module ``rotate_sessions`` (no ``__init__.py``), yet the
        file sits beside ``scripts/_script_lib.py`` and is exactly the sibling
        that private library exists for. Membership falls through to the
        physical-file test, which resolves the owner's directories through
        astroid.
        """
        node = self._import_statement(
            "from dgate_res_ns._ns_lib import helper",
            "sibling_tool",
            path=str(privacy_fixture_root / "dgate_res_ns" / "sibling_tool.py"),
        )
        with self.assertNoMessages():
            self.checker.visit_importfrom(node)

    def test_allows_namespace_sibling_plain_import(
        self, privacy_fixture_root: Path,
    ) -> None:
        """The physical-membership exemption covers the plain-import spelling too."""
        node = self._import_statement(
            "import dgate_res_ns._ns_lib",
            "sibling_tool",
            path=str(privacy_fixture_root / "dgate_res_ns" / "sibling_tool.py"),
        )
        with self.assertNoMessages():
            self.checker.visit_import(node)

    def test_file_membership_rejects_placeholder_paths(self) -> None:
        """A string-built module can never prove physical membership.

        The discriminating observer for the existence guards in
        ``_importer_file_inside``: a consumer AND an owner both string-built
        (astroid caches them by name with the ``"<?>"`` placeholder file).
        Without the guards, the owner's placeholder dirname resolves to the
        working directory, which CONTAINS the consumer's placeholder abspath
        -- the false suppression that silently passed eight flag tests during
        this step's own development. The premise (astroid returns the cached
        string-built module for a by-name resolution) is asserted first, so
        an astroid caching change surfaces here as a loud premise failure
        rather than a vacuous pass.
        """
        owner = astroid.parse("", module_name="dgate_poison_owner")
        consumer = astroid.parse(
            "from dgate_poison_owner import _x", module_name="dgate_consumer",
        )
        resolved = consumer.import_module(
            "dgate_poison_owner", relative_only=False,
        )
        assert resolved is owner
        assert _importer_file_inside(consumer, "dgate_poison_owner") is False

    def test_allows_beyond_top_level_without_private_target(self) -> None:
        """A relative import past the top level with nothing private stays silent.

        That malformation is pylint's own E0402 territory; this rule only
        speaks when something private is named.
        """
        node = self._import_statement(
            "from ...nowhere import something", "app.grid",
        )
        with self.assertNoMessages():
            self.checker.visit_importfrom(node)
