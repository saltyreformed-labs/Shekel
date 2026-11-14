"""The package privacy checker."""

import sys
from pathlib import Path

import astroid
import pytest
from astroid import nodes
from pylint.testutils import CheckerTestCase, MessageTest

from shekel_checkers import ShekelPackagePrivacyChecker
from shekel_checkers.package_privacy import _importer_file_inside


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

    @pytest.fixture(scope="class", name="privacy_fixture_root")
    @classmethod
    def _privacy_fixture_root(cls, tmp_path_factory: pytest.TempPathFactory):
        """On-disk fixture packages for the resolution-dependent privacy tests.

        The W9910 rule is mostly pure string analysis, but two behaviors depend on
        astroid RESOLVING real modules: the ``from P import _x`` module-vs-name
        split, and the physical-membership second chance for namespace packages.
        Testing those against the live ``app`` tree would couple the checker tests
        to the application's import graph (and to ``app`` being importable at all),
        so a hermetic fixture tree is built once for this class and put on
        ``sys.path``:

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
        whole-file run only because ``TestShekelBalanceSeamChecker`` (now
        ``test_the_balance_seam_checker.py``'s) parses a synthetic module under
        the real name ``app.services.balance_at._context`` earlier in the one
        file the two classes shared then, and warmed astroid's cache.  The plan's proposed
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
