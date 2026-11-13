"""The ledger model fence checker."""

import astroid
from astroid import nodes
from pylint.testutils import CheckerTestCase, MessageTest

from shekel_checkers import (
    _LEDGER_LEAF_MODULE_NAMES,
    _LEDGER_MODEL_ALLOWLIST,
    _LEDGER_MODEL_MODULES,
    _LEDGER_MODEL_NAMES,
    ShekelLedgerModelFenceChecker,
)


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
