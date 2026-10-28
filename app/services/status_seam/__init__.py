"""
Shekel Budget App -- Status Seam

The single status-mechanics primitive for BOTH status-bearing rows.
``apply_status_change`` is the ONE place a ``Transaction.status_id`` or a
``Transfer.status_id`` is assigned.  Every status-changing path -- the manual
``mark_done`` branch, the inline PATCH, ``cancel``, ``mark_as_credit`` /
``unmark_credit``, the envelope ``settle_from_entries``, and the transfer
service's one status writer for a transfer -- routes through it so the status
mechanics are uniform and impossible to skip.

**It became the only seam at plan step X-aj1** (ruling **R-DN**,
``docs/audits/balance_architecture/README.md``).  ``transfer_service`` used to
carry a SECOND implementation of this same seam for a transfer's rows, and that
duplication is why the ``shekel-transaction-status-bypass`` checker (W9907)
needed a two-module allowlist at all.  **Merging the seams does not by itself
shrink that allowlist**, and saying so here matters because the obvious
inference is wrong: ``transfer_service`` still writes a status through a
CONSTRUCTOR (``create_transfer``'s; the shadow constructor went with the
shadows at plan step X-bi-6-4d-2), which W9907's
born-Projected rule refuses, so its entry survives until plan step X-aj2 replaces
the write door.  What the merge did remove is the duplicate ATTRIBUTE writes --
and three defects the duplicate had and this one did not:

* it stamped the settle instant UNCONDITIONALLY on entering a settled status
  rather than preserving an existing one, so an identity re-submit of an
  unchanged status re-dated a settled transfer -- and since plan step E1a that
  day IS the posted ``entry_date``, so it moved the money (finding **N-146**).
  **The seam alone did not close that class**: plan step X-f1b0 found both
  mark-done routes passing an explicit instant that overrode this seam's
  preservation, re-dating a replayed settle by however long ago it happened
  (finding **N-178**), and removed both;
* it never expired the ``status`` relationship, though both models declare it
  ``lazy="joined"`` -- latent rather than live, since every route commits before
  rendering, but true by accident rather than by construction;
* it mirrored a drifted shadow's status with no transition check at all, which
  ruling **R-DO** replaced with a refusal.

**It is a PACKAGE since plan step X-au-c3, and the leaves are the three
subjects**: :mod:`._seam` (the mechanics -- ``apply_status_change`` and the two
form-submission readings), :mod:`._record` (WHAT a settle records: the
``Settlement`` value and the reads over it), and :mod:`._refusals` (the
invariants stated as guards); :mod:`._covering` writes a row's covering
movement and, since plan step ``balance:X-bi-6-4d-2``, :mod:`._side` a
transfer side's record (the Transfer arm).  The split is the one ``transfer_service`` and
``cash_ledger`` already made at the same 1000-line ceiling, and by the same
rule: BY RESPONSIBILITY, not by line count.  **The public surface is this module
and ``__all__`` is it** -- a leaf is private, and a caller outside the package
depends on the names below, so no import site changed when the split landed.

**The W9907 allowlist narrowed WITH the split**, to ``_seam`` alone.  Prefix
matching would otherwise have exempted every leaf here from the status-write
fence without anybody deciding it should be -- the exact widening that happened
to ``transfer_service`` at plan step X-f2-c3.  None of the other leaves
writes ``status_id`` at all.

Architecture:
  - A LOW-LEVEL primitive: it depends on the state machine, the
    settled-status predicate, the session, the models and LEAF services --
    the settle-day, cash-ledger, pay-period and transfer-leg readers
    (``settle_day``, ``cash_ledger``, ``pay_period_service``,
    ``transfer_legs``), the books grade (``planned_rows_books``), the figure
    and press values (``stated_figure``, ``match_press``), the match
    withdrawal (``match_withdrawal``) and the one act that takes a movement
    off the books (``movement_removal``, which reaches the ledger writer and
    ``match_withdrawal``) -- never on the higher-level services
    that call it (``transaction_service``,
    ``credit_workflow``, ``transfer_service``, the route layer, and the loan /
    paycheck settle paths).  Living below its callers is what keeps it free of
    the ``transaction_service <- entry_service <- entry_credit_workflow <-
    credit_workflow`` import cycle: were the seam in ``transaction_service``
    (which imports ``entry_service``), ``credit_workflow`` could not import it
    without closing that cycle.
  - **It also owns the DOOR-SIDE reading of a submitted settle day**
    (:func:`settle_day_for_status`, plan step X-f1c / ruling R-EG), which is
    form-submission policy rather than a primitive.  It lives here anyway, and
    the narrower "primitive only" claim this paragraph used to make alone was
    corrected by a neutral review: the rule has two route doors (the
    transaction PATCH and the transfer PATCH) in two route packages, so the
    alternatives were a shared route helper -- a cross-package private
    import, which W9910 fences -- or two spellings of one rule, which is this
    arc's own root cause 1.  It
    sits beside the refusal it defers to
    (:func:`reject_settle_day_without_settled_status`) so the forgiving door
    rule and the fail-loud service rule are read together.
  - The function is not pure: it reads the settled-status predicate, the
    settle-day echo rule (``settle_day.submitted_settle_day``) and, by one
    query, the budget's earliest recordable day
    (:func:`app.services.pay_period_service.earliest_recordable_day`),
    refusing a forwarded day before it with a ``ValidationError``.
    ``pay_period_service`` imports no other service, so the read adds no
    caller of the seam to what the seam imports.
  - No Flask imports.  Mutates the passed row in place and does NOT commit
    -- the caller owns the session boundary; it flushes explicitly only in
    the covering movement's own teardown and re-point
    (:func:`apply_status_change`'s docstring says which) and in a side
    record's own teardown and endpoint move (:mod:`._side`).
"""

from app.services.status_seam._record import (
    Settlement,
    correction_record,
    covering_movement_of,
    honoured_correction,
    honoured_figure,
    recorded_settlement,
    tender_account_id_of,
)
from app.services.status_seam._refusals import (
    StatusBearingRow,
    day_is_in_the_future,
    deleted_row_payment_refusal,
    reject_figure_without_settled_status,
    reject_future_settle_day,
    reject_settle_day_without_settled_status,
    reject_settlement_without_settled_status,
    reject_stated_figure_over_purchases,
    reject_tender_without_settled_status,
)
from app.services.status_seam._covering import (
    covered_cash_leg,
    covering_clause,
    holds_no_purchase_clause,
    payment_recorded_elsewhere_clause,
    record_clearing,
)
from app.services.status_seam._side import (
    free_moving_side_record,
    land_moved_side_record,
    record_side_clearing,
    release_side_figure,
    sync_side_records,
)
from app.services.status_seam._seam import (
    apply_status_change,
    settle_day_for_status,
    figure_for_status,
    tender_for_status,
)

__all__ = [
    "Settlement",
    "StatusBearingRow",
    "apply_status_change",
    "covered_cash_leg",
    "covering_clause",
    "holds_no_purchase_clause",
    "payment_recorded_elsewhere_clause",
    "record_clearing",
    "free_moving_side_record",
    "land_moved_side_record",
    "record_side_clearing",
    "release_side_figure",
    "sync_side_records",
    "figure_for_status",
    "tender_for_status",
    "correction_record",
    "covering_movement_of",
    "honoured_correction",
    "honoured_figure",
    "recorded_settlement",
    "tender_account_id_of",
    "deleted_row_payment_refusal",
    "reject_figure_without_settled_status",
    "reject_tender_without_settled_status",
    "day_is_in_the_future",
    "reject_future_settle_day",
    "reject_settle_day_without_settled_status",
    "reject_settlement_without_settled_status",
    "reject_stated_figure_over_purchases",
    "settle_day_for_status",
]
