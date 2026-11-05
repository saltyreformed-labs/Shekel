"""
Shekel Budget App -- Statement Import: the bank says what happened.

**The app's own records of when money moved are guesses, and this package is
where it stops guessing.**  Measured on the developer's own bank exports: of 110
movements matched to bank lines on exact amount, only 33 carried the day the app
had recorded, and 88 of 135 settled Checking rows share a click-minute with
another row -- a settle date is a bookkeeping-session artifact, not an
observation (finding **N-173**).

Plan step ``bank_import:X-f6a``, ruling **R-FP**.  This leaf RECORDS a
statement; it does not yet decide which of the app's own rows a line explains.
That separation is deliberate and it is what makes this commit gradeable by a
property rather than by inspection: no balance moves, because no balance input
is written.

The public surface, and what each piece is for:

* :class:`StatementLine` -- the ONE normalized line shape every adapter
  produces, so everything downstream of a parser is source-independent.
* :func:`supported_sources` / :func:`parse_statement` -- the adapter registry.
* :func:`group_key` / :func:`pair_by_statement` / :func:`fresh_ordinals` -- the
  identity rule, which needs no id of the source's own.  A line's STORED key is
  ``(account, posted_on, amount, ordinal)``; the ordinal is a SURROGATE this app
  mints, and what decides whether an incoming line is one the app already holds
  is the WORDING the bank stated, reconciled a group at a time (plan step
  ``bank_import:X-f6a-4``).
* :func:`verify_running_balance` -- the self-check a source with a running
  balance affords, and the reason the CSV was chosen over the OFX.
* :func:`plan_statement` -- that door's DECISION, every refusal included,
  returned as a :class:`StatementPlan` and read without performing it: what a
  file WOULD add, for a reader deriving the reconcile's acts on a copy before
  any of them (plan step ``balance:X-bk-2``, plan#1192).
* :func:`record_statement` -- the one write door, performing that plan and
  returning :class:`ImportOutcome`.  A placed figure is a LEVEL row naming the import
  (plan step ``balance:X-bj-1``), and :func:`bank_levels` is the one read of
  which levels stand and which a release has withdrawn.
* :func:`delete_import` -- the one UNDO door, returning
  :class:`ImportRemoval`, and the only thing in this package that destroys.
  It is what finding **N-302** says a refusal owes (plan step
  ``bank_import:X-f6a-4``): a restated line, or a first import that named the
  wrong Shekel account, used to end that account's ability to import for good.
  It removes what the BANK said, and a settle day an accepted match wrote is
  the app's own record and stays -- but it MOVES MONEY where the review had
  CREATED a row from one of those lines (ruling **R-GG**): the row goes with
  the line, and the receipt says how many and how much.  *It said
  BALANCE-NEUTRAL until plan step ``bank_import:X-gr`` (finding **BI-487**).*

**What the recorded lines are FOR is the steps after this one**, named here
because the schema was designed for all four rather than for the first: the
match and its review (``X-f6a-2``), a bank line that becomes a purchase
(``X-f6a-3b``), the walked-statement SILENCE that makes an unshown line NOT
CLEARED rather than unknown (``balance:X-f3a-2``), and the re-openable recorded
difference at the cash cutover (``balance:X-f3c``).
"""

from ._adapters import parse_statement, supported_sources
from ._anchor import (
    ImportedBalance,
    KnownOpening,
    recorded_opening_before,
    release_anchors_from,
    resolve_anchor,
    solve_effective_day,
    weaker_of,
)
from ._balance import (
    BankAnchor,
    BankBalances,
    Checkpoint,
    RecordedRun,
    bank_balance_on,
    bank_daily_movements,
    bank_levels,
    covered_runs,
    fold_bank_balances,
)
from ._integrity import (
    carries_running_balance,
    opening_balance,
    verify_running_balance,
)
from ._line import (
    GroupPairing,
    KeyedLine,
    ParsedStatement,
    StatementLine,
    fresh_ordinals,
    group_indexes,
    group_key,
    pair_by_statement,
)
from ._reads import (
    ImportRecord,
    ImportRemovalPreview,
    RecordedSpan,
    SourceOption,
    available_sources,
    import_history,
    recent_lines,
    recorded_span,
)
from ._plan import StatementPlan, plan_statement
from ._record import ImportOutcome, record_statement
from ._undo import ImportRemoval, delete_import

__all__ = [
    "BankAnchor",
    "BankBalances",
    "Checkpoint",
    "GroupPairing",
    "ImportOutcome",
    "ImportedBalance",
    "KnownOpening",
    "ImportRecord",
    "ImportRemovalPreview",
    "ImportRemoval",
    "KeyedLine",
    "RecordedRun",
    "RecordedSpan",
    "SourceOption",
    "StatementPlan",
    "StatementLine",
    "available_sources",
    "bank_balance_on",
    "bank_daily_movements",
    "bank_levels",
    "carries_running_balance",
    "covered_runs",
    "delete_import",
    "fold_bank_balances",
    "fresh_ordinals",
    "group_indexes",
    "group_key",
    "import_history",
    "opening_balance",
    "pair_by_statement",
    "plan_statement",
    "ParsedStatement",
    "parse_statement",
    "recent_lines",
    "record_statement",
    "recorded_opening_before",
    "recorded_span",
    "release_anchors_from",
    "resolve_anchor",
    "solve_effective_day",
    "supported_sources",
    "verify_running_balance",
    "weaker_of",
]
