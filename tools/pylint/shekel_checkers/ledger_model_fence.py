"""Ledger-model import fence: ``shekel-ledger-model-bypass`` (W9908).

Flags any module OUTSIDE the posting-ledger service allowlist that IMPORTS a
posted-ledger row model -- ``Posting`` / ``JournalEntry`` / ``LedgerAccount``.
The posting ledger is append-only and balances through a deferred trigger
(``budget.assert_journal_entry_balanced``); every write goes through
``posting_service._emit_balanced_entry`` and every confirmed read through the
sanctioned reader packages (``posting_reads``, the loan / account posting
packages, the ledger report service). A consumer that holds one of these model
classes -- or the module that defines it -- can query the ledger, or
construct/mutate a row, OUTSIDE those seams: the exact bypass Build-Order
Steps 2-5 built the seams to prevent (``docs/audits/balance_architecture/``).
The fence is the import analog of the W9907 status-write fence (and of the
balance-producer read fence, which structure retired at plan step E1e): it binds
at the point a model (or its module) enters a module, where the name is still
visible, rather than at every downstream use.

It binds on two axes, so a module-path rename cannot slip a model class past it:

* by NAME -- importing ``Posting`` / ``JournalEntry`` / ``LedgerAccount`` by name
  is flagged wherever it is imported from (``from app.models import Posting`` --
  the F-1 package re-export; ``from app.models.journal_entry import Posting``;
  ``from ..models.journal_entry import Posting`` -- a relative path; an alias).
  The three class names are distinctive enough to carry no non-ledger collision
  (the precedent the balance-producer fence set for name-fencing);
* by MODULE -- binding a defining submodule reaches the model as
  ``<module>.Posting``, so ``from app.models.journal_entry import <anything>``
  (the whole ledger submodule is off-limits), ``from app.models import
  journal_entry`` (the submodule bound by name off the package), and the plain
  ``import app.models.journal_entry`` are each flagged.

Accepted boundaries (documented, not claimed closed): a bare ``import
app.models`` followed by attribute access ``app.models.Posting``, and a RELATIVE
submodule-by-name import ``from ..models import journal_entry``. Neither is an
idiom in the tree (cross-package imports are absolute; relative imports are
intra-package ``from ._sibling`` only), and reaching a model through either is
not written anywhere -- fencing them would risk false positives on ordinary
``import app.models`` for no real coverage. The NAME axis already catches the
relative CLASS import, which is the reachable relative shape.
"""

from astroid import nodes

from pylint.checkers import BaseChecker

from ._common import _module_in_allowlist

# The posted-ledger row models' defining submodules. Binding one of these
# reaches its model as ``<module>.Posting``, so the whole submodule is fenced:
# ``from app.models.journal_entry import <anything>`` and a plain ``import
# app.models.journal_entry`` both name it.
_LEDGER_MODEL_MODULES = frozenset({
    "app.models.journal_entry",
    "app.models.ledger_account",
})
# Their leaf names -- for the ``from app.models import journal_entry`` shape,
# which binds the submodule BY NAME off the package (then reached as
# ``journal_entry.Posting``). Derived from the module set so the two never drift.
_LEDGER_LEAF_MODULE_NAMES = frozenset(
    name.rsplit(".", 1)[-1] for name in _LEDGER_MODEL_MODULES
)
# The row-model CLASS names. Imported by name, a model class is fenced wherever
# it comes from (the package re-export -- the F-1 shape; the defining submodule;
# a relative path; an alias) -- the NAME axis, immune to a module-path rename.
_LEDGER_MODEL_NAMES = frozenset({"Posting", "JournalEntry", "LedgerAccount"})

# Modules allowed to import a ledger model directly: the posting-ledger write
# core (``posting_service`` + its ``_posting_write`` / ``_posting_reconcile`` /
# ``_posting_purchases`` leaves -- ``_posting_purchases`` added at plan step
# X-f3b, which made a PURCHASE a posting source of its own; a
# ``_posting_legacy`` leaf sat here from ``balance:X-bi-6-3`` leaf 3b until
# ``X-bi-6-5``'s second leaf, card plan#1845, deleted it), the readers
# (``posting_reads``, the
# loan / account posting packages, the ledger report package), the
# chart-of-accounts resolver
# (``ledger_account_service``), the pay-period LOCK CLASSIFIER whose
# ``LEDGER_POSTINGS`` gate sums a period's postings, and the archive
# guard that counts a linked account's postings. Fully-qualified names, matched
# exactly or as a package prefix (the three packages) via the fail-closed
# :func:`_module_in_allowlist` -- the same match/prefix/fail-closed contract the
# balance-producer fence relied on. ``app.models`` is listed as a package prefix ON PURPOSE:
# a model legitimately references sibling models (relationships, FK type hints),
# so the whole models layer governs its own internal imports; the fence keeps the
# service / route / util layers out. Grep-verified complete against every current
# importer under app/, so the fence ships at zero violations.
_LEDGER_MODEL_ALLOWLIST = frozenset({
    "app.models",
    "app.services.posting_service",
    "app.services.posting_reads",
    "app.services._posting_reconcile",
    "app.services._posting_purchases",
    "app.services._posting_write",
    "app.services.loan_posting_service",
    "app.services.account_posting_service",
    "app.services.ledger_account_service",
    "app.services.ledger_report_service",
    # ``app.services.pay_period_admin`` until plan step C3-a MOVED the lock
    # classifier out of it: the only ledger-model use in that module was
    # ``_period_ids_with_unbalanced_ledger``, so the entry moved with the
    # function rather than being duplicated, and the writers now hold no
    # ledger import at all.  The fence got tighter, not wider.
    "app.services.pay_period_locks",
    "app.utils.archive_helpers",
})


class ShekelLedgerModelFenceChecker(BaseChecker):
    """Forbid importing a posted-ledger model outside the posting-ledger seams.

    Every ledger write goes through ``posting_service._emit_balanced_entry`` and
    every confirmed read through the sanctioned reader packages; a module holding
    ``Posting`` / ``JournalEntry`` / ``LedgerAccount`` -- or the module that
    defines one -- can query or mutate the append-only ledger outside those
    seams, the bypass class Build-Order Steps 2-5 exist to prevent
    (docs/audits/balance_architecture/). This checker is the deterministic import
    fence for that class, the ledger analog of the W9907 status-write fence.
    Only the posting-ledger write core, its readers,
    and the utilities that legitimately hold a model class
    (:data:`_LEDGER_MODEL_ALLOWLIST`) may import a ledger model; every other
    module must reach the ledger through those services. The fence binds on the
    NAME axis (a model class is flagged wherever it is imported from -- immune to
    a module-path rename or a relative path) and the MODULE axis (binding a
    defining submodule, by ``from`` or plain ``import``), so no reachable import
    shape lands a model class in a consumer uncaught (the accepted-boundary
    residuals -- bare ``import app.models``, relative submodule-by-name -- are
    non-idioms named in the module docstring).
    """

    name = "shekel-ledger-model-fence"
    msgs = {
        "W9908": (
            "Posted-ledger model '%s' imported outside the posting-ledger "
            "allowlist; reach the ledger through its services instead",
            "shekel-ledger-model-bypass",
            "The posting ledger (Posting / JournalEntry / LedgerAccount) is "
            "append-only and balances through a deferred trigger. Every write "
            "must go through posting_service._emit_balanced_entry and every "
            "confirmed read through the sanctioned reader packages "
            "(posting_reads, the loan_posting_service / account_posting_service "
            "packages, ledger_report_service); a module that imports a ledger "
            "model -- or its defining module -- can query or construct/mutate a "
            "row outside those seams, the bypass class Build-Order Steps 2-5 "
            "built the seams to prevent (docs/audits/balance_architecture/). "
            "Only the posting-ledger write core, its readers, "
            "ledger_account_service, pay_period_admin, and archive_helpers may "
            "import a ledger model. The fence binds by NAME (importing Posting / "
            "JournalEntry / LedgerAccount is flagged wherever it comes from -- "
            "the app.models re-export F-1 shape, the defining submodule, a "
            "relative path, an alias) and by MODULE (binding a defining "
            "submodule via from app.models.journal_entry import <anything>, from "
            "app.models import journal_entry, or import app.models.ledger_account "
            "reaches the model as <module>.Posting). The status fence (W9907) "
            "guards the write seam and the balance seam is now structural "
            "(W9910); this guards the import that would let a module skip "
            "either.",
        ),
    }

    def visit_importfrom(self, node: nodes.ImportFrom) -> None:
        """Flag a ``from``-import that lands a ledger model in a non-allowlisted module.

        Three matches, in priority order (each name reported at most once):

        * MODULE axis -- ``node.modname`` is a defining submodule
          (``app.models.journal_entry`` / ``app.models.ledger_account``): the
          whole submodule is ledger-internal, so ANY name pulled from it is
          reported once (the submodule reach is the offense, not the name);
        * off the ``app.models`` package -- a model CLASS name (the F-1
          re-export) OR a defining submodule bound BY NAME
          (``from app.models import journal_entry``, then ``journal_entry.Posting``);
        * NAME axis -- a model CLASS name imported from ANY OTHER path (a
          relative import, a re-export elsewhere): keying on the name makes the
          fence immune to the module-path forms the first two branches match on.

        ``node.names`` holds ``(name, alias)`` pairs. An allowlisted enclosing
        module is exempt and returns first; an empty / unresolvable module name
        fails closed (not allowlisted, so checked).
        """
        if _module_in_allowlist(node, _LEDGER_MODEL_ALLOWLIST):
            return
        if node.modname in _LEDGER_MODEL_MODULES:
            self.add_message(
                "shekel-ledger-model-bypass", node=node, args=(node.modname,),
            )
            return
        if node.modname == "app.models":
            for name, _alias in node.names:
                if name in _LEDGER_MODEL_NAMES or name in _LEDGER_LEAF_MODULE_NAMES:
                    self.add_message(
                        "shekel-ledger-model-bypass", node=node, args=(name,),
                    )
            return
        for name, _alias in node.names:
            if name in _LEDGER_MODEL_NAMES:
                self.add_message(
                    "shekel-ledger-model-bypass", node=node, args=(name,),
                )

    def visit_import(self, node: nodes.Import) -> None:
        """Flag a plain ``import app.models.<ledger module>`` from a consumer.

        The MODULE-axis shape a ``from``-only fence would miss: ``import
        app.models.journal_entry`` binds the module, and the model is reached as
        ``app.models.journal_entry.Posting`` with no importable name for the
        ``from``-import branches to see. ``node.names`` holds ``(dotted_name,
        alias)`` pairs; a name that is one of the fenced ledger submodules is
        flagged (alias-agnostic). An allowlisted enclosing module returns first.
        """
        if _module_in_allowlist(node, _LEDGER_MODEL_ALLOWLIST):
            return
        for name, _alias in node.names:
            if name in _LEDGER_MODEL_MODULES:
                self.add_message(
                    "shekel-ledger-model-bypass", node=node, args=(name,),
                )
