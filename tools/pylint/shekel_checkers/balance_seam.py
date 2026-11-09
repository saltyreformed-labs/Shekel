"""Balance-seam fence residue: W9909, the fail-closed completeness check.

**The name-keyed CALL fence is gone.**  Plan step D3 cut it to one surface and
plan step E1e deleted that surface with its subject: structure does the whole
job now.  Every balance producer is a PRIVATE submodule of
``app.services.balance_at`` (plan steps D1/D-ctx/D-fold), the package-privacy
gate W9910 (``shekel-private-module-import``) plus stock ``protected-access``
make reaching one from outside the seam a hard failure in every import
spelling, and -- since E1e -- **no public balance producer exists outside the
seam at all**, so there is nothing left for a call allowlist to guard.  That is
also why the deletion cedes no coverage: measured on this tree, the two
spellings a consumer would actually write for the deleted posting readers now
rate E0611 (``no-name-in-module``) and E1101 (``no-member``), which are
hard-gated by ``--fail-on=E`` -- a stronger gate than the W9906 warning they
replace.

Named non-goal, shared with W9910: dynamic reflection (``importlib`` /
``getattr`` chains) is outside every static gate's sight -- a deliberate evader
was always one rename past the name fence too, so nothing is ceded there
either.

What remains is ONE check:

* **W9909, the completeness registry on the PUBLIC ingredient packages**
  (:data:`_FENCED_MODULE_RULINGS`).  "Is this new public function a balance
  producer?" is a human judgment no AST rule can decide, and these packages
  hold every ingredient of one (facts, splits, sums, schedules) OUTSIDE
  W9910's protection -- measured four times (N-28/N-31): a public balance-at-T
  born in one rates 10.00/10 with every other gate green.  The registry forces
  that judgment loudly at definition time.  Its rot direction is SAFE: a stale
  classification entry names a dead function (inert, and the reverse-staleness
  meta-test flags it); it can never permit a bypass.  Since E1e every scoped
  package's PRODUCER set is empty, which states the invariant as data: a new
  public name in one of them is either a ruled NON-producer (with its why) or
  it belongs inside ``balance_at`` -- there is no third classification.
  Resolves only if the read seam, write cluster, and shared leaves ever move
  under one private super-package boundary (recorded in the plan as a post-E1
  option).
"""

from astroid import nodes

from pylint.checkers import BaseChecker

# The balance-producer deny list and its one-package allowlist are GONE (plan
# step D3).  Every producer is a private ``balance_at`` submodule, so W9910
# already flags every import spelling from outside the seam and W0212 the
# attribute-access residual -- a name list here could only duplicate (and rot
# behind) the structural gate.  The seam-private W9909 rulings the moved
# producers carried (finding N-31's "travel") died with it, for the same
# reason: a producer born inside a private module is unreachable until someone
# deliberately re-exports it on the seam's public ``__init__``, which is the
# reviewed, one-place act the fence always wanted.
#
# The two genesis loan-ledger balance readers -- W9906's last surface, and the
# whole message with them -- are GONE at plan step E1e, deleted rather than
# fenced.  They answered balance-at-T from the WRITE cluster's own postings
# table, which is why they could not simply move inside the read seam; but by
# E1d-b they had no caller in ``app/`` at all (the resolver's confirmed slice
# seeds from the WALK, and ``confirmed_loan_view`` -- the injection seam the
# allowlist existed for -- was deleted), leaving only the reconciliation
# oracle's window onto the postings.  A window belongs on the ORACLE's side, so
# it moved to ``tests/_test_helpers.py`` and the production functions were
# deleted: no producer, no re-export, nothing to allowlist.  The loan FOLD had
# already left for the same structural reason at plan step D-fold
# (``fold_loan_balances`` / ``fold_from_walk`` into ``balance_at._fold``; the
# forward twin it had there, ``_plan_fold.fold_forward``, merged into the one
# timeline at recurrence:R16-c-1), and ``walk_loan_ledger``
# came off to the leaf's NON-producer ruling because the walk yields only FACTS
# once the fold is elsewhere (see the ``loan_ledger`` ruling below).

# The loan RESOLVER call-fence and the context-memo handle fence are GONE (plan
# step D3), because plan step D2a removed their reason at the ROOT:
# ``LoanState.current_balance`` -- the balance-at-today that made a resolver
# bundle a leak -- is DELETED, so ``resolve_loan_bundle`` / ``resolve_loan_seeded``
# hand back schedule detail of the same sanctioned class ``debt_schedule_rows``
# exposed (deleted with its last caller at recurrence:R16-c-2).  Plan step
# E1d-a then moved that whole read INSIDE the seam
# as the private ``balance_at._resolution``, re-exported nowhere, so W9910 is the
# only gate it needs and its completeness scope deleted with it (its
# ``resolve_loan_seeded`` half folded away at E1d-b); the context memo went with
# it, leaving ``BalanceContext.loan_walk``, which hands back the leaf's
# public FACTS (``walk_loan_ledger`` is a ruled NON-producer).  A consumer that
# wants a loan's balance still has exactly one way to get it --
# ``balance_at.balance_at`` -- but by construction now, not by list.

# ── Fail-closed completeness (W9909) ────────────────────────────────
#
# The rulings themselves -- every scope set and every classification with
# its why -- live in :mod:`._fence_rulings`, which is DATA this module
# looks up.  They were inline until plan step ``balance:X-f3c-2b-2a``,
# when this file stood at EXACTLY pylint's 1000-line ceiling and a
# FAIL-CLOSED registry had five names to add: a gate whose answer table
# cannot grow refuses the next honest edit rather than the next unsafe
# one.  The checker changes when the RULE changes; the registry changes
# whenever a scoped package gains a public name, which is far more often.
#
# Only what this module READS.  The eight scope sets moved with the table and
# are imported by ``shekel_checkers/__init__.py`` from their new home rather
# than re-exported through here, so the split leaves no pass-through name whose
# only job is to make a move invisible.
from ._fence_rulings import _FENCED_MODULE_RULINGS




def _is_public_export_surface(node: nodes.FunctionDef) -> bool:
    """Return whether ``node`` is a PUBLIC name a consumer outside the module can call.

    A name is reachable from outside when it is public AND every scope enclosing
    it is a public CLASS, all the way up to the Module.  That admits, and the
    second of these is the shape the original check missed:

    * a public TOP-LEVEL function, and
    * a public METHOD of a public class (including a method of a public class
      nested in a public class) -- reachable as ``SomeClass(...).method(...)``,
      which is exactly how ``BalanceContext.loan`` handed routes a ``ResolvedLoan``
      with the fence silent.

    Excluded: any private name (leading underscore) at any level -- a private
    class's methods are unreachable because a consumer cannot name the class -- and
    anything nested inside a FUNCTION, which is unreachable from outside. Dunders
    fall out of the same underscore test: ``__post_init__`` is a lifecycle hook,
    not an export. ``@property`` / ``@classmethod`` / ``@staticmethod`` / ``async``
    are all ordinary FunctionDefs here and are all covered.

    **The walk is up the ANCESTOR CHAIN, not a fixed one- or two-level test.**  A
    two-level test (``parent is a ClassDef whose parent is the Module``) silently
    drops a method of a nested class, and a bare ``parent is Module`` test drops a
    top-level function defined inside an ``if`` or ``try``.  Neither shape exists
    in a fenced module today -- and "it cannot happen today" is precisely the
    reasoning that produced both of the holes this checker exists to close.

    Args:
        node: The function-definition node under inspection.

    Returns:
        ``True`` when the name is part of the module's public export surface.
    """
    if node.name.startswith("_"):
        return False
    scope = node.parent
    while scope is not None and not isinstance(scope, nodes.Module):
        # A FunctionDef ancestor makes it a nested (unreachable) def; a private
        # class ancestor makes it unnameable.  Statement wrappers (If / Try /
        # With) are not scopes -- step through them.
        if isinstance(scope, nodes.FunctionDef):
            return False
        if isinstance(scope, nodes.ClassDef) and scope.name.startswith("_"):
            return False
        scope = scope.parent
    return scope is not None


def _fenced_module_ruling(
    node: nodes.NodeNG,
) -> "tuple[frozenset[str], frozenset[str]] | None":
    """Return the (producers, non-producers) ruling for ``node``'s module.

    Matches the enclosing module's fully-qualified name against
    :data:`_FENCED_MODULE_RULINGS` exactly, or as a package prefix -- so a fenced
    module split into a package (or a package's submodules, e.g.
    ``loan_posting_service._corrections``) stays scoped.  Returns ``None`` when the
    module defines no fenced producer, in which case the completeness check does
    not apply to it.

    Args:
        node: The function-definition node whose enclosing module is resolved.

    Returns:
        The module's ``(producers, non_producers)`` pair, or ``None``.
    """
    name = node.root().name or ""
    for module, ruling in _FENCED_MODULE_RULINGS.items():
        if name == module or name.startswith(module + "."):
            return ruling
    return None


class ShekelBalanceSeamChecker(BaseChecker):
    """The balance-seam fence's honest residue: fail-closed classification only.

    The structural rules own the old fence's whole job now: every balance
    producer is a private ``app.services.balance_at`` submodule, unreachable
    from outside the seam under W9910 (``shekel-private-module-import``) in
    every import spelling, so consumers obtain balances through the seam's
    public entries by construction.  The name-keyed CALL fence W9906 is DELETED
    (plan step E1e) along with its last subject -- the two genesis posting
    readers, which had no ``app/`` caller left and whose oracle window moved to
    the test suite.  What this checker still enforces is the one judgment no AST
    rule can make: the fail-closed classification of new public exports in the
    balance-ingredient packages W9910 cannot protect (W9909; see the module
    docstring for the scope and its measured basis).
    """

    name = "shekel-balance-seam"
    msgs = {
        "W9909": (
            "Public function '%s' in a fenced module is unclassified; add it to "
            "the producer set (it answers balance-at-T) or the non-producer set "
            "(it does not) in shekel_checkers/balance_seam.py",
            "shekel-unclassified-fenced-export",
            "A name-keyed fence fails OPEN: a new function defined inside a "
            "covered module is unguarded by default until someone remembers to "
            "list it. That shipped twice (investment_base_balance_map, "
            "loan_owed_at_dates -- each read past the seam with the fence "
            "silent) and was re-measured four more times during Phase D "
            "(findings N-28 / N-31). This check inverts the default for the "
            "PUBLIC balance-ingredient packages the package-privacy gate W9910 "
            "cannot protect (the loan_ledger and loan_posting_service "
            "packages, the cash_ledger leaf and the row_valuation tier below "
            "it, the pure loan_resolver tier, and "
            "the account_projection classifier): every public top-level "
            "function or public method "
            "there must be explicitly classified as a producer or a "
            "non-producer, so an unclassified one fails AT ITS DEFINITION "
            "rather than silently becoming a hole a consumer can reach "
            "through. Resolve it by deciding what the function IS: if it "
            "answers 'what is account A's balance at time T' it belongs "
            "INSIDE app.services.balance_at as a private submodule (a public "
            "balance producer outside the seam is not a thing); if it answers "
            "something else -- a loader, a classifier, a reducer over "
            "balances the caller already holds, interest earned, schedule "
            "rows -- add it to its module's non-producer set with a comment "
            "saying why. If in doubt, it is a producer: a false negative is "
            "the dangerous mode for a fence.",
        ),
    }

    def visit_functiondef(self, node: nodes.FunctionDef) -> None:
        """Flag a public function OR METHOD in a fenced module classified as neither.

        The fail-closed half of the fence.  ``node`` is every function
        definition; it is considered when it is PUBLIC (non-underscore) and part
        of a fenced module's reachable export surface -- see
        :func:`_is_public_export_surface` -- and it is reported unless it appears
        in THAT MODULE'S producer set or its deliberate non-producer set.

        **METHODS are classified, not skipped, and that is this checker's second
        design fix.**  The original check returned early for anything whose parent
        was not the Module, on the reasoning that "a fence is about the module's
        public export surface".  A public method of a public class IS that surface:
        ``BalanceContext.loan`` was a public method that handed any caller a whole
        ``ResolvedLoan`` -- ``state.current_balance``, a balance-at-today, one
        attribute read away -- and because methods were never classified, the
        checker could not see it.  That is the exact fail-open shape W9909 was
        written to close, one level down, and it shipped anyway.  A consumer can
        reach ``SomeClass().method()`` every bit as easily as ``some_function()``;
        the fence must see both.

        Nested functions (a def inside a def) are still skipped: they are
        unreachable from outside, so they are not an export surface.  The
        ``balance_at`` seam package carries no ruling -- its public functions ARE
        the entries consumers call.
        """
        if not _is_public_export_surface(node):
            return
        ruling = _fenced_module_ruling(node)
        if ruling is None:
            return
        producers, non_producers = ruling
        if node.name in producers or node.name in non_producers:
            return
        self.add_message(
            "shekel-unclassified-fenced-export", node=node, args=(node.name,),
        )

    # An ``async def`` is a distinct astroid node with its own visit hook, and a
    # fence that only saw ``def`` would be trivially evaded by writing the
    # producer as a coroutine.  There are no async defs in app/ today, so this is
    # a closed door rather than a live path -- which is exactly the point.
    visit_asyncfunctiondef = visit_functiondef
