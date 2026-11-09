"""The resolver reads no posted ledger: its import closure and its call qualifiers, against the
ledger fence's allowlist.
"""

from __future__ import annotations

import ast
import inspect
import os

import app.services

from ._helpers import _resolver_balance


# ---------------------------------------------------------------------------
# 6b. The resolver stays ledger-free -- the parallel run is not a tautology
# ---------------------------------------------------------------------------
#
# The parallel run (Sections 1 / 7) is honest ONLY because the un-seeded resolver
# derives its balance from the schedule and the transaction source, never from the
# posted ledger it is meant to cross-check.  Review finding M4: that independence
# was convention-only -- a future refactor letting a resolver input loader consult
# the ledger would silently collapse the parallel run to a tautology and nothing
# would fail.  These guards make it mechanical.
#
# **INDEPENDENCE WAS GRADED AS A VALUE from plan step balance:X-au-g-2c-1 until
# plan#2914**: ``test_perturbing_the_posted_ledger_does_not_move_the_reference``
# moved the STORED ledger and asserted ``_resolver_balance`` did not, which is
# sound against every route a static check cannot see: raw SQL, an ORM backref
# traversed off a model, ``importlib``, ``getattr``, or a ledger model module
# simply being RENAMED.  It went with the suite's grading of the store, since a
# forged stored leg is a state only the store can hold; it never moved the
# worked-out read the parallel run now compares against.
#
# **The static check is defence in depth, and it is now NARROW because a
# stronger gate already owns most of it.**  The production checker W9908
# (``shekel-ledger-model-bypass``, a hard ``--fail-on`` in CI and in the
# per-edit hook) forbids importing a posted-ledger model outside a 12-entry
# allowlist, on the module axis AND on the rename-immune NAME axis.  For every
# module outside that allowlist a ledger import is therefore already
# impossible, and restating it here bought nothing.  What is left is the
# question W9908 cannot ask: **does the resolver reference reach a ledger SEAM
# at all?**  The check below asserts exactly that, and it READS the checker's
# own constant rather than copying it, so the two cannot drift.
#
# **Re-measured 2026-09-09 (plan step balance:X-bl-2b), and the figure this
# paragraph used to quote was not one this code produces.**  It read "of the
# reference's 91-module import closure, 21 are inside W9908's allowlist and
# every one of those is ``app.models.*``".  ZERO modules of the closure are
# allowlist members, then or now: the allowlist holds the bare name
# ``app.models`` (the package) and this check tests literal membership, so
# ``app.models.account`` is not in it.  21 was the count of ``app.models.*``
# modules -- 22 on the pre-step closure, 14 on this one -- which is a different
# set.  The ``app.models`` exclusion below is therefore inert TODAY and kept
# deliberately: the allowlist is read from the checker at run time, so a model
# module added to it would land here, and a model legitimately references
# sibling models.  What gives this check its teeth is not the exclusion but
# ``test_the_closure_is_transitive_and_the_check_bites``, which plants a real
# seam two hops out and asserts the closure reaches it.
#
# **Three hand-maintained lists were deleted to get here and none was
# replaced**: a 12-token module-name DENYLIST (``_LEDGER_IMPORT_TOKENS``), a
# COVERAGE GUARD that existed only to keep the denylist complete, and an
# ALLOWLIST (``_MOVED_FENCED_MODULES``) re-adding modules that had moved out of
# a fenced package.  Each approximated one property and each had its own way to
# go stale; the per-module scan they served was also UNSOUND in the direction
# that matters, since a fenced module calling an unfenced neighbour that read
# the ledger passed.  A first draft of this step replaced them with a
# whole-closure ledger-model check that carried its own two-name constant --
# and an adversarial review showed that RENAMING ``app/models/journal_entry.py``
# made it vacuous with all four of its controls still green.  Reading W9908's
# allowlist instead is what removes the last copied list.
#
# **What a closure CANNOT see, stated because a guard that overstates itself is
# worse than one that does not exist.**  Import reachability over-approximates
# IMPORTS; it is orthogonal to value flow.  A module in the closure can reach a
# posted-ledger value with no import edge at all -- ``cash_ledger._books``
# already issues raw SQL through ``db.session.execute(text(...))`` and argues
# for it in its own docstring, and ``app.models.account`` is in the closure, so
# a ``backref`` on ``ledger_account`` would put every posting one attribute
# away.  Neither exists today; both are why a VALUE control is the stronger
# one and this is not.
#
# **The reference's dependency was far larger than its arithmetic, and plan
# step balance:X-bl-2b closed that** (finding **N-432**).
# ``_replay_from_anchor`` reads exactly three dates off each payment --
# ``period_start``, ``due_date``, ``settled_on`` -- and NO amount, yet this
# reference assembled its feed through ``load_loan_context``, which prices
# every row: **101 modules at dev ``2625963a``, 46 here** (102 against 46 on
# this tree, so the amount-free feed removes 56 like-for-like; the metric is
# the one stated in ``app.services.loan_ledger._installments``).  The feed is
# ``loan_ledger.payment_installments`` now, so a pricing refusal cannot break a
# control that reads no figure.  The narrow check below was unaffected either
# way; the finding was about the dependency, not about the check.

#: The modules the resolver reference is assembled from -- the closure's roots.
#:
#: VERIFIED against ``_resolver_balance``'s own body rather than maintained
#: beside it (``test_the_roots_cover_what_the_reference_calls``).  A
#: hand-maintained root list is the same hazard ``_MOVED_FENCED_MODULES`` was:
#: plan step X-au-g-2c-1 itself gave ``_resolver_balance`` a new producer and
#: nothing here noticed until an adversarial review did.
_RESOLVER_REFERENCE_ROOTS = (
    "app.services.amortization_engine",
    "app.services.loan_ledger",
    "app.services.loan_loaders",
    "app.services.loan_resolver",
)


#: Where the production ledger-model fence keeps its allowlist.
_LEDGER_FENCE_SOURCE = "tools/pylint/shekel_checkers/ledger_model_fence.py"


def _repo_root() -> str:
    """Return the repository root, derived from ``app.services``' own location."""
    return os.path.dirname(os.path.dirname(os.path.dirname(app.services.__file__)))


def _ledger_seam_allowlist() -> frozenset:
    """Return W9908's ``_LEDGER_MODEL_ALLOWLIST``, read from the checker's source.

    **Read, never copied.**  A second copy of that set here would be a list to
    keep in step by hand -- exactly what this rewrite deleted three of, and
    exactly how the first draft of it went vacuous under a module rename.
    Parsed out of the source with ``ast`` rather than imported, so this test
    needs neither ``pylint`` nor ``astroid`` on its path and cannot be
    perturbed by plugin load order.

    Returns:
        The fully-qualified module names allowed to import a posted-ledger
        model.

    Raises:
        AssertionError: When the constant is absent, or is no longer a
            ``frozenset({...})`` of string literals, or parses EMPTY.  Loud in
            every case, because a silently empty allowlist would make the check
            below pass over anything.
    """
    path = os.path.join(_repo_root(), _LEDGER_FENCE_SOURCE)
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name)
            and target.id == "_LEDGER_MODEL_ALLOWLIST"
            for target in node.targets
        ):
            continue
        call = node.value
        assert isinstance(call, ast.Call) and call.args, (
            "W9908's _LEDGER_MODEL_ALLOWLIST is no longer frozenset({...}); "
            "this reader must be updated with it"
        )
        names = frozenset(ast.literal_eval(call.args[0]))
        assert names, (
            "W9908's allowlist parsed EMPTY -- this check would pass over "
            "anything"
        )
        return names
    raise AssertionError(
        f"_LEDGER_MODEL_ALLOWLIST not found in {_LEDGER_FENCE_SOURCE} -- the "
        "production ledger-model fence moved or was renamed"
    )


def _module_sources() -> "dict[str, str]":
    """Return ``{dotted module name: source}`` for every module under ``app/``.

    Read off disk with no import, so building the graph has no side effect and
    cannot be perturbed by what a previous test imported.  A package's
    ``__init__.py`` is keyed by the package name itself, which is what makes a
    ``from app.services import loan_loaders`` edge resolve to the package.
    """
    root = os.path.join(_repo_root(), "app")
    sources: dict[str, str] = {}
    for dirpath, _dirs, files in os.walk(root):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            path = os.path.join(dirpath, fname)
            name = os.path.relpath(path, _repo_root())[:-3].replace(os.sep, ".")
            if name.endswith(".__init__"):
                name = name[: -len(".__init__")]
            with open(path, encoding="utf-8") as handle:
                sources[name] = handle.read()
    return sources


def _import_edges(sources: "dict[str, str]") -> "dict[str, set]":
    """Return ``{module: modules it imports}`` over *sources*.

    ``ast.walk`` visits nested nodes, so a lazy in-function import is an edge
    exactly like a top-level one -- which it must be: every deferred import in
    this codebase exists to break a load-order cycle, not to avoid running the
    target, and the target is reached at call time all the same.

    A ``from X import a, b`` node resolves to ``X.a`` / ``X.b`` where those are
    modules, and to ``X`` where they are names inside it.  Preferring the
    LONGEST match is what keeps ``from app import ref_cache`` from resolving to
    the ``app`` package -- whose ``__init__`` is the application factory and
    imports the whole tree, which would make every closure the entire codebase
    and this check meaningless.

    Args:
        sources: ``{module: source}``, from :func:`_module_sources` or a copy of
            it with a mutation spliced in.  Taken as an argument so a control
            can grade this builder against a hypothetical tree without writing
            to disk.

    Returns:
        ``{module: set of modules it imports}``.
    """
    packages = {name.rsplit(".", 1)[0] for name in sources if "." in name}
    edges: dict[str, set] = {}
    for name, source in sources.items():
        found: set = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    base = name if name in packages else name.rsplit(".", 1)[0]
                    for _ in range(node.level - 1):
                        base = base.rsplit(".", 1)[0]
                    module = f"{base}.{node.module}" if node.module else base
                else:
                    module = node.module or ""
                leaves = {
                    f"{module}.{alias.name}" for alias in node.names
                    if f"{module}.{alias.name}" in sources
                }
                found |= leaves
                # The package itself is an edge only when at least one imported
                # name is NOT a module of it -- i.e. a symbol re-exported from
                # its ``__init__``, which really does run that file.
                if module in sources and len(leaves) < len(node.names):
                    found.add(module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    while parts:
                        candidate = ".".join(parts)
                        if candidate in sources:
                            found.add(candidate)
                            break
                        parts.pop()
        edges[name] = found
    return edges


def _import_closure(roots, sources=None) -> "dict[str, list[str]]":
    """Return every module reachable from *roots*, each with the path that reached it.

    The path is what makes a failure actionable: the offending module is rarely
    the one to fix, and "``loan_payment_service._context`` -> ``cash_ledger`` ->
    ``X`` -> ``app.models.journal_entry``" names the edge that must go.
    Breadth-first, so the path recorded is a shortest one.

    Args:
        roots: Dotted module names to start from.  A package root seeds EVERY
            module inside it, not just its ``__init__`` -- the same
            whole-package scope the per-module fence had, kept deliberately: a
            leaf nothing currently imports is still a leaf of a package the
            reference is assembled from, and wiring the ledger into it would be
            one call site away from live.
        sources: ``{module: source}`` to build over.  Defaults to the real tree;
            a control passes a MUTATED copy so the closure itself can be graded
            against a hypothetical, rather than only the per-module detector it
            used to splice a string into.

    Returns:
        ``{module: [root, ..., module]}``, including the roots themselves.
    """
    if sources is None:
        sources = _module_sources()
    edges = _import_edges(sources)
    paths: dict[str, list[str]] = {}
    queue: list = []
    for root in roots:
        for name in sources:
            if name == root or name.startswith(f"{root}."):
                paths[name] = [name]
                queue.append(name)
    while queue:
        current = queue.pop(0)
        for target in sorted(edges.get(current, ())):
            if target not in paths:
                paths[target] = paths[current] + [target]
                queue.append(target)
    return paths


def _reference_call_qualifiers() -> set:
    """Return the module qualifiers :func:`_resolver_balance` calls through.

    Parses ``_resolver_balance``'s own source (:func:`inspect.getsource`) and
    walks its body for every ``<name>.<attr>(...)`` call, so "what the
    reference is assembled from" is read off the reference rather than
    remembered beside it.

    Returns:
        The bare qualifier names (``loan_loaders``, ``loan_resolver``, ...).
    """
    tree = ast.parse(inspect.getsource(_resolver_balance))
    body = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_resolver_balance"
    )
    return {
        node.func.value.id
        for node in ast.walk(body)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }


class TestResolverIsLedgerFree:
    """The resolver reference reads NONE of the posted ledger -- mechanically.

    **The VALUE control that perturbed the stored ledger went at plan#2914**
    with the suite's grading of the store (the block above
    :data:`_RESOLVER_REFERENCE_ROOTS` says what it covered).  The runtime
    backstop beside it, which fenced the posted-ledger readers off by
    patching them on ``posting_service`` and re-read the resolver, went at
    plan task plan#3132: of its three readers two read the records and moved
    into the tests (``tests/_records_oracle.py``), where the resolver cannot
    call them, and the third read the store, which no door writes.

    The static checks are deliberately narrow: the
    reference reaches no ledger SEAM (the question the production W9908 fence
    cannot ask), and its roots are read off ``_resolver_balance`` itself rather
    than maintained beside it.  See the block above
    :data:`_RESOLVER_REFERENCE_ROOTS` for why three hand-maintained lists were
    deleted to get here, and for what a closure cannot see.
    """

    def test_the_reference_reaches_no_ledger_seam(self):
        """No module the reference can reach may import a posted-ledger model.

        The static half, and it is one line of logic because W9908 owns the
        rest: outside that checker's allowlist a ledger-model import is already
        a hard CI failure, so the only modules in the reference's closure that
        COULD hold one are the allowlisted ones.  Of those, ``app.models.*`` is
        allowlisted on purpose (a model references sibling models); anything
        else is a ledger SEAM and has no business on this path.

        The allowlist is READ from the checker's source, never copied, so this
        cannot drift from the gate it defers to -- and cannot be made vacuous by
        renaming a model module, which is how the first draft of this check
        failed an adversarial review.
        """
        closure = _import_closure(_RESOLVER_REFERENCE_ROOTS)
        seams = {
            module: " -> ".join(closure[module])
            for module in closure
            if module in _ledger_seam_allowlist()
            and module != "app.models"
            and not module.startswith("app.models.")
        }
        assert not seams, (
            f"the resolver reference reaches a posted-ledger seam: {seams} -- "
            f"the parallel run would become a tautology (review M4 / M-1)"
        )

    def test_the_roots_cover_what_the_reference_calls(self):
        """Every module ``_resolver_balance`` calls through is one of the roots.

        **The control that would have caught this step's own drift.**  A
        hand-maintained root list goes stale exactly as ``_MOVED_FENCED_MODULES``
        did, and plan step X-au-g-2c-1 drifted it in the same commit that
        rewrote the fence: ``_resolver_balance`` gained
        ``amount_basis_for_scenario``, a helper that runs a query and builds an
        amount basis, and no root moved.  Reading the qualifiers off the
        function's own AST is what ties the two together.

        A qualifier that resolves into ``tests`` rather than ``app`` is named
        explicitly rather than ignored: the closure walks ``app/`` only, so a
        producer living in the suite is outside every static check here and its
        independence rests on the VALUE control above.
        """
        qualifiers = _reference_call_qualifiers()
        assert qualifiers, "no calls found -- this control is reading nothing"
        from_app = {q for q in qualifiers if f"app.services.{q}" in _module_sources()}
        uncovered = sorted(
            q for q in from_app
            if f"app.services.{q}" not in _RESOLVER_REFERENCE_ROOTS
        )
        assert not uncovered, (
            f"_resolver_balance calls through {uncovered}, which "
            f"_RESOLVER_REFERENCE_ROOTS does not name -- the closure is not "
            f"the reference's own dependency set"
        )
        # The suite-side producers, named so their absence from the static
        # checks is a stated fact rather than an oversight.
        assert from_app == {
            "amortization_engine", "loan_ledger", "loan_loaders", "loan_resolver",
        }

    def test_the_closure_is_transitive_and_the_check_bites(self):
        """A ledger SEAM import two hops from the roots is caught, by the closure.

        **This mutates the GRAPH, not a string.**  An earlier draft spliced
        source into ``_imports_a_ledger_model`` and asserted the detector fired,
        which grades the detector and never the closure -- so a closure that had
        gone vacuous still passed it.  ``_import_closure`` takes its ``sources``
        now, so the real builder runs against a hypothetical tree.

        The plant is two edges out (no module of the roots names it directly)
        and the assertion is that the closure REACHES the seam and reports the
        chain.

        **The plant MOVED at plan step balance:X-bl-2b, and the old one failing
        is what said so.**  It was ``cash_ledger._amounts``, reached only
        through ``loan_payment_service`` -- the pricing tier that step took OFF
        this reference.  Once the reference stopped loading it the module left
        the closure entirely and the guard above fired, refusing to grade a
        plant it could no longer reach rather than passing vacuously.  This one
        is inside the reference's own tier: ``loan_loaders._terms`` ->
        ``ref_cache`` -> ``ref_cache._accessors``.
        """
        sources = _module_sources()
        planted = "app.ref_cache._accessors"
        assert planted in sources
        clean = _import_closure(_RESOLVER_REFERENCE_ROOTS, sources)
        assert planted in clean and len(clean[planted]) > 2, (
            f"the transitivity control is measuring nothing: {clean.get(planted)}"
        )
        assert "app.services.posting_service" not in clean

        mutated = dict(sources)
        mutated[planted] += "\nfrom app.services import posting_service\n"
        after = _import_closure(_RESOLVER_REFERENCE_ROOTS, mutated)
        assert "app.services.posting_service" in after, (
            "the closure did not follow an import two hops from the roots"
        )
        assert "app.services.posting_service" in _ledger_seam_allowlist(), (
            "posting_service is not a ledger seam by W9908's own allowlist, so "
            "this control plants something the check would not object to"
        )

    def test_the_graph_does_not_collapse_through_the_app_package(self):
        """``from app import x`` resolves to ``app.x``, never to the ``app`` package.

        The one way this check could go vacuous in the opposite direction.
        ``app/__init__.py`` is the application factory and imports the whole
        tree, so resolving ``from app import ref_cache`` to ``app`` would put
        every module in every closure and the check would fail on everything,
        naming a path that explains nothing.  A first draft of this graph did
        exactly that: it reported a 282-module closure reaching ``balance_at``
        through ``app`` -> ``app.jinja_filters``, which is not an edge the
        resolver stack has.
        """
        sources = _module_sources()
        edges = _import_edges(sources)
        # **``_amount_rule`` since plan step X-au-f**, not ``_amount_source``:
        # the CLASSIFICATION tier split into its own module when the resolver
        # passed ``max-module-lines``, and ``_declared_relation`` -- the one
        # reader of ``ref_cache`` on this path, which turns a stored
        # ``amount_source_id`` back into the member the rules are written
        # against -- went with it.  Re-pointed rather than deleted: the claim is
        # that the resolver reads reference data BY ID, and that claim is now
        # about the leaf that does it.
        assert "app.ref_cache" in edges["app.services.cash_ledger._amount_rule"]
        assert "app" not in edges["app.services.cash_ledger._amount_rule"]
        assert "app" not in edges["app.services.cash_ledger._amount_source"]
        closure = _import_closure(_RESOLVER_REFERENCE_ROOTS, sources)
        assert "app" not in closure
        assert len(closure) < len(sources) / 2, (
            f"the closure holds {len(closure)} of {len(sources)} modules -- it "
            f"has collapsed through a re-export hub and grades nothing"
        )
