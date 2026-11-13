"""The balance seam's unclassified fenced export checker."""

import astroid
from astroid import nodes
from pylint.testutils import CheckerTestCase, MessageTest

from shekel_checkers import (
    _CASH_LEDGER_MODULES,
    _FENCED_MODULE_RULINGS,
    _LEDGER_LINES_MODULES,
    _LOAN_PAYMENT_SEAM_MODULES,
    _SEAM_PRIVATE_CONTEXT_MODULES,
    _KIND_CLASSIFIER_MODULES,
    _RECURRING_DEFINITION_MODULES,
    _ROW_VALUATION_MODULES,
    _LOAN_LEDGER_DEFINING_MODULES,
    _LOAN_RESOLVER_ENGINE_MODULES,
    ShekelBalanceSeamChecker,
)

from ._helpers import _fenced_module_sources, _public_export_names


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
