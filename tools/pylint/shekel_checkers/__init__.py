"""Project-specific pylint checkers for the Shekel budget app.

These AST checkers encode financial-correctness rules from
``docs/coding-standards.md`` that generic pylint does not cover, so the rules
are enforced by a deterministic tool instead of relying on a reviewer (human or
LLM) to remember them. They are loaded through ``.pylintrc`` (``load-plugins``),
so every pylint invocation -- the per-edit hook, the Stop-hook full run, CI, and
pre-commit -- applies them automatically.

This package is the plugin: pylint imports it and calls :func:`register`, which
registers every checker below. Each rule lives in its own module (one checker
per module, the shared AST primitives in ``_common``) so no single file grows
past pylint's module-size cap and each rule is read and tested in isolation.

Rules implemented:

* ``shekel-decimal-from-float`` (W9901, :mod:`.money`): flags ``Decimal``
  constructed from a float, which inherits binary float imprecision. Monetary
  values must be built from strings.
* ``shekel-refname-compare`` (W9902, :mod:`.refname`): flags comparing a
  ``.name`` attribute against a string literal. Reference-table name columns are
  for display only; logic must key off IDs or enums.
* ``shekel-disable-rationale`` (W9903, :mod:`.disable_rationale`): flags any
  ``# pylint: disable=`` that lacks a standard ``Pylint:`` why-comment in the
  mandated location (the docstring for a def/class-scoped directive, a comment
  immediately above otherwise). Keeps every suppression auditable with one grep.
* ``shekel-bare-money-quantize`` (W9904, :mod:`.money`): flags a ``.quantize()``
  of a cents quantum (``Decimal("0.01")`` / ``CENTS`` / ``TWO_PLACES``) with no
  explicit rounding mode, which silently uses Python's default
  ``ROUND_HALF_EVEN`` (banker's). ``app/utils/money.py`` mandates monetary
  rounding go through ``round_money`` (``ROUND_HALF_UP``); this locks the rule
  that the financial_calculations audit's E-26 / HIGH-04 remediation
  established.
* ``shekel-unclassified-fenced-export`` (W9909, :mod:`.balance_seam`): the
  fail-closed classification of new public exports in the PUBLIC
  balance-ingredient packages W9910 cannot protect (``loan_ledger``,
  ``loan_posting_service``, ``cash_ledger``, ``loan_resolver``,
  ``loan_payment_service``, ``account_projection``, plus
  the seam-private ``balance_at._context`` and ``balance_at._recurrence_memos``
  (its mixin), whose publicly re-exported ``BalanceContext`` W9910 cannot see
  methods on). Flags a PUBLIC top-level
  function or public method there classified as NEITHER a producer nor a
  deliberate non-producer. The rulings are keyed BY MODULE, so a name ruled
  harmless in one cannot exempt a same-named function added to another. A
  name-keyed fence fails OPEN -- a new function inside a covered module was
  unguarded until someone remembered to list it, and that shipped twice
  (``investment_base_balance_map``, then ``loan_owed_at_dates``, which a consumer
  read past the seam for a month with the fence silent). This inverts the
  default: an unclassified public function fails AT ITS DEFINITION, in the same
  per-edit hook run that created it, so a producer can no longer become a hole by
  omission. Resolve it by deciding what the function IS -- a balance-at-T
  belongs INSIDE ``balance_at`` as a private submodule; anything else goes in
  the module's non-producer set with a comment saying why. If in doubt, it is
  a producer.
* ``shekel-transaction-status-bypass`` (W9907, :mod:`.status_bypass`): flags
  writing ``status_id`` outside the transaction status seam. Every non-transfer
  ``Transaction.status_id`` change must funnel through
  ``status_seam.apply_status_change`` (the seam that verifies the transition,
  maintains ``paid_at``, and refreshes the eager ``status`` relationship) so the
  settled-state boundary is uniform and a confirmed settle can never be emitted
  twice or skipped (Build-Order Step 3's named highest risk). Only
  ``app.services.status_seam`` (the seam) and ``transfer_service`` (which mirrors
  ``status_id`` onto a transfer's two shadow ``Transaction`` rows, which a
  syntactic checker cannot tell from a real transaction) may write it. Four
  write forms are matched: direct assignment, the literal
  ``setattr(x, "status_id", ...)`` form, a ``status_id`` key or keyword in a
  bulk ``.update(...)`` / ``.values(...)`` call, and a ``Transaction(...)`` /
  ``Transfer(...)`` constructor ``status_id=`` kwarg whose value is not
  recognizably born-Projected (the 2026-07-02 adversarial review's H3:
  born-Projected was a convention three layers deep, not a machine rule). The
  status analog of the balance seam's own call fence, which was deleted at plan
  step E1e once structure made it unnecessary -- the status seam has no such
  structural boundary, so this name fence stays.
* ``shekel-ledger-model-bypass`` (W9908, :mod:`.ledger_model_fence`): flags any
  module OUTSIDE the posting-ledger allowlist that IMPORTS a posted-ledger row
  model (``Posting`` / ``JournalEntry`` / ``LedgerAccount``). The append-only
  ledger is written only through ``posting_service._emit_balanced_entry`` and
  read only through the sanctioned reader packages; a module holding a model
  class -- or its defining module -- can query or mutate a row outside those
  seams, the bypass class Build-Order Steps 2-5 built the seams to prevent
  (``docs/audits/balance_architecture/``). The fence binds on the NAME axis
  (importing ``Posting`` / ``JournalEntry`` / ``LedgerAccount`` is flagged
  wherever it comes from -- the ``from app.models import Posting`` F-1 re-export,
  the defining submodule, a relative path, an alias) and the MODULE axis
  (binding a defining submodule via ``from app.models.journal_entry import
  <anything>``, ``from app.models import journal_entry``, or ``import
  app.models.ledger_account``, reached as ``<module>.Posting``). The import
  analog of the W9907 write fence (and of the balance-seam read fence deleted
  at plan step E1e).
* ``shekel-private-module-import`` (W9910, :mod:`.package_privacy`): the
  balance arc's Phase D gate (step D-gate) -- a package's private modules are
  private. A module outside package ``P`` may not import ``P._x``, nor any
  name from it, in any spelling: ``from P._x import name`` (the form the stock
  ``import-private-name`` extension is fail-open for, finding N-26),
  ``from P import _x``, or ``import P._x``, aliased or not, relative or
  absolute, TYPE_CHECKING included. Name-INDEPENDENT and fail-closed (no
  allowlist, no producer list), it is the structural boundary that let the
  balance name fences DELETE instead of being maintained forever -- at plan
  step D3 down to one call surface, and at plan step E1e down to the W9909
  classification alone, once the last public producer outside the seam was
  itself deleted.

Deliberately NOT implemented as a checker: a blanket ``float()`` ban. The
codebase's real ``float()`` call sites are all legitimate (config timeouts that
are genuinely floats, and documented Decimal-to-float boundaries for Chart.js
JSON serialization). A static rule cannot distinguish a precision-losing
calculation from an end-of-pipeline serialization boundary without false
positives, so that judgment lives in the code-reviewer subagent instead.
"""

from .balance_seam import (
    ShekelBalanceSeamChecker,
    _is_public_export_surface,
)
# W9909's answer table and its scope sets, split out of ``balance_seam`` at
# plan step ``balance:X-f3c-2b-2a``: that module stood at EXACTLY pylint's
# 1000-line ceiling, and a FAIL-CLOSED registry that cannot grow refuses the
# next honest edit rather than the next unsafe one.
from ._fence_rulings import (
    _CASH_LEDGER_MODULES,
    _FENCED_MODULE_RULINGS,
    _KIND_CLASSIFIER_MODULES,
    _LEDGER_LINES_MODULES,
    _LOAN_LEDGER_DEFINING_MODULES,
    _LOAN_PAYMENT_SEAM_MODULES,
    _LOAN_RESOLVER_ENGINE_MODULES,
    _RECURRING_DEFINITION_MODULES,
    _ROW_VALUATION_MODULES,
    _SEAM_PRIVATE_CONTEXT_MODULES,
)
from .disable_rationale import ShekelDisableRationaleChecker
from .ledger_model_fence import (
    _LEDGER_LEAF_MODULE_NAMES,
    _LEDGER_MODEL_ALLOWLIST,
    _LEDGER_MODEL_MODULES,
    _LEDGER_MODEL_NAMES,
    ShekelLedgerModelFenceChecker,
)
from .money import ShekelMoneyChecker
from .package_privacy import ShekelPackagePrivacyChecker
from .refname import ShekelRefNameChecker
from .status_bypass import _STATUS_SEAM_MODULES, ShekelTransactionStatusBypassChecker

# Re-exported so the plugin's import surface is identical to the pre-split
# single module: ``.pylintrc`` names the package in ``load-plugins`` and calls
# ``register``; the checker unit tests import the seven checker classes and
# the module scope sets straight ``from shekel_checkers``.  The underscore-
# prefixed sets are internal-but-tested, listed here so re-export is explicit
# rather than an unused-import.
__all__ = [
    "_CASH_LEDGER_MODULES",
    "_FENCED_MODULE_RULINGS",
    "_KIND_CLASSIFIER_MODULES",
    "_LEDGER_LEAF_MODULE_NAMES",
    "_LEDGER_LINES_MODULES",
    "_LEDGER_MODEL_ALLOWLIST",
    "_LEDGER_MODEL_MODULES",
    "_LEDGER_MODEL_NAMES",
    "_LOAN_LEDGER_DEFINING_MODULES",
    "_LOAN_PAYMENT_SEAM_MODULES",
    "_LOAN_RESOLVER_ENGINE_MODULES",
    "_RECURRING_DEFINITION_MODULES",
    "_ROW_VALUATION_MODULES",
    "_SEAM_PRIVATE_CONTEXT_MODULES",
    "_is_public_export_surface",
    "_STATUS_SEAM_MODULES",
    "ShekelBalanceSeamChecker",
    "ShekelDisableRationaleChecker",
    "ShekelLedgerModelFenceChecker",
    "ShekelMoneyChecker",
    "ShekelPackagePrivacyChecker",
    "ShekelRefNameChecker",
    "ShekelTransactionStatusBypassChecker",
    "register",
]


def register(linter) -> None:
    """Register the Shekel checkers with the pylint ``linter`` (plugin entry point).

    Called by pylint when this package is named in ``.pylintrc``'s
    ``load-plugins``. ``linter`` is the active PyLinter instance.
    """
    linter.register_checker(ShekelMoneyChecker(linter))
    linter.register_checker(ShekelRefNameChecker(linter))
    linter.register_checker(ShekelDisableRationaleChecker(linter))
    linter.register_checker(ShekelBalanceSeamChecker(linter))
    linter.register_checker(ShekelTransactionStatusBypassChecker(linter))
    linter.register_checker(ShekelLedgerModelFenceChecker(linter))
    linter.register_checker(ShekelPackagePrivacyChecker(linter))
