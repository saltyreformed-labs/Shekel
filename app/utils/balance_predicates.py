"""Centralized balance-contributing status predicate (E-15, MED-02).

MED-02 / D6-09 identified that the conceptual predicate "is this
transaction's amount contributing to a projected balance"
is hand-reproduced in 20+ sites across the codebase in three structurally
different forms:

- Python in-loop skips, e.g. ``if txn.status_id != projected_id``
  (``cash_ledger._flows`` / ``cash_ledger._amounts``, ``grid.py``,
  ``credit_workflow.py``; the three former ``balance_calculator.py`` sites moved
  to the cash ledger leaf at plan step D1c and that module now has none).
- SQLAlchemy filters, e.g. ``Status.excludes_from_balance.is_(False)``
  reproduced across ``year_end_summary_service.py``,
  ``savings_dashboard_service.py``, ``loan_payment_service.py``.
- Jinja conditionals against status IDs across the grid templates.

In addition the ``[CREDIT, CANCELLED]`` exclusion set is re-derived under a
different name (``_get_excluded_status_ids`` in
``year_end_summary_service.py``) plus reproduced inline.

Every site is individually ID-based and correct today (E-15 is satisfied
at each site -- never a name-string comparison), so no displayed number
is wrong now. The risk MED-02 flags is that a one-sided change to the
status-to-balance rule -- adding a new exclusion, or relaxing one --
would silently drift a balance because the rule lives in N places, not
one.

This module is the single source of truth for the predicate. It exposes:

- ``is_balance_contributing(txn) -> bool`` mirrors exactly
  the valuation's gate (``row_valuation.fixed_contribution``): soft-deleted contributes
  zero, ``excludes_from_balance`` statuses contribute zero, everything
  else contributes its effective amount. This is the Python predicate
  for in-memory iteration.
- ``is_projected(row) -> bool`` is the equality form ("is this row
  Projected?") used by the inline ``!= projected_id`` / ``== projected_id``
  sites. Pure status equality; does not consider ``is_deleted`` (callers
  that need the combined gate use ``is_balance_contributing``). It takes a
  ``Transaction`` OR a ``Transfer``, for the same reason
  ``is_projected_clause`` below is parameterised on the model class: both
  tables carry a ``status_id`` into ``ref.statuses``, and "is this row still
  Projected" is ONE question. The row form was Transaction-only until plan
  step X-au-b asked it of a transfer (``cash_ledger._amount_source``), where
  the alternative was a second spelling of the same comparison.
- ``balance_excluded_status_ids() -> frozenset[int]`` is the cached
  ``{Credit.id, Cancelled.id}`` set, derived from the same ``ref_cache``
  lookups as the clause builder so they can never disagree.
- ``balance_contributing_clause()`` is the SQLAlchemy boolean expression
  for ORM queries. It uses ``Transaction.status_id`` directly so callers
  do not need to ``.join(Status)``: the ID set comes from the same
  ``balance_excluded_status_ids()`` accessor the Python predicate
  consults, so the SQL filter and the Python loop classify any
  transaction identically.

Commits 5 and 10 of the financial-calculation remediation route the
canonical balance and period-subtotal producers through these helpers;
Commit 29 finishes routing the residual inline Python skips, the
remaining SQLAlchemy filters, and the Jinja-template predicates through
them, and extends this module with the per-status equality predicates
and SQL clause builders the residual sites consume:

- ``is_projected``, ``is_credit``, ``is_cancelled``, ``is_done`` --
  Python equality predicates for the four statuses the call sites
  branch on. ``is_projected`` was introduced in Commit 2; Commit 29
  adds the rest so every per-status equality check in business logic
  routes through one cached-ID source.
- ``is_projected_clause(model_class)`` -- the SQL form of
  ``is_projected``, parameterised on the model class so both
  ``Transaction`` queries (dashboard, entries, carry-forward,
  templates) and ``Transfer`` queries (transfer-template archive /
  unarchive / hard-delete) share one definition.

Per E-15 / CLAUDE.md rule 4 ("IDs for logic, strings for display only"),
every predicate in this module is implemented over the semantic boolean
columns on the status row (``excludes_from_balance``) or over cached
integer IDs from ``ref_cache``. The status display string is never
consulted; the C2-8 test asserts this property mechanically against the
module source.
"""
from collections.abc import Callable
from datetime import date

from sqlalchemy import and_

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import UndatedSettleError
from app.models.account import AccountAnchorHistory
from app.models.transaction import Transaction
from app.models.transfer import Transfer


def balance_excluded_status_ids() -> frozenset[int]:
    """Return the cached set of status IDs excluded from balance contribution.

    Per ``app/ref_seeds.py`` the rows with ``excludes_from_balance=True``
    are exactly ``Credit`` and ``Cancelled`` -- both represent a
    transaction whose dollar amount is settled elsewhere (paid by credit
    card, or cancelled outright) and therefore must contribute zero to
    the projected checking balance. The valuation
    already encodes this exclusion; the SQLAlchemy clause builder
    consumes this same set so the in-Python predicate and the ORM filter
    cannot disagree.

    Returns:
        A ``frozenset[int]`` of the ``ref.statuses.id`` values for
        ``StatusEnum.CREDIT`` and ``StatusEnum.CANCELLED``. ``frozenset``
        (not ``set``) so the value is hashable and immutable -- callers
        treat it as an inert lookup, never mutate it.

    Raises:
        RuntimeError: propagated from ``ref_cache.status_id`` if the
            reference cache has not been initialized. The cache is
            populated by ``create_app()`` after seeding; production and
            test paths both initialize it before any service runs.
    """
    return frozenset({
        ref_cache.status_id(StatusEnum.CREDIT),
        ref_cache.status_id(StatusEnum.CANCELLED),
    })


def settled_status_ids() -> frozenset[int]:
    """Return the cached set of status IDs that represent a settled transaction.

    Per ``app/ref_seeds.py`` the rows with ``is_settled=True`` are
    exactly ``Paid`` (``StatusEnum.DONE``) and ``Received`` -- the two
    statuses whose real-world money movement has completed.
    This is the ID-list counterpart of the semantic ``Status.is_settled``
    column: SQLAlchemy filters that need the set for a
    ``status_id.in_(...)`` clause consume this accessor, while the sibling
    sites that read ``txn.status.is_settled`` (the calendar, variance,
    savings-metric, and balance-calculator Python loops) consult the
    column directly. Both forms resolve to the same two rows by
    construction, so a "settled spending" total computed via the ID list
    can never disagree with one gated on the boolean column. The
    ``TestSettledStatusIds`` parity test derives the expected set from the
    ``is_settled`` column itself, so adding or removing a settled status
    in the seed matrix without updating this accessor fails that test.

    **The band held THREE members until plan step X-am** (ruling
    **balance:R-HA**, closing finding **N-177**).  The third was ``Settled``,
    the ARCHIVE, and every reader here consumed the SET rather than the member
    -- so the balance engine could not tell the three apart and the archive
    contributed nothing this accessor's callers could see.  What it DID have
    were two readers that told it apart, ``is_archived`` and
    ``not_archived_clause``, both of them guards over a state carrying zero
    rows; both are deleted with it.  Now that the band is exactly the two
    type-specific settle targets, ``transaction_service.settled_status_member``
    is TOTAL over it: every member of this set is the settled status of exactly
    one transaction type.

    Returns:
        A ``frozenset[int]`` of the ``ref.statuses.id`` values for
        ``StatusEnum.DONE`` and ``StatusEnum.RECEIVED``. ``frozenset``
        (not ``set``) so the value is hashable and immutable -- callers
        treat it as an inert lookup, never mutate it.

    Raises:
        RuntimeError: propagated from ``ref_cache.status_id`` if the
            reference cache has not been initialized. The cache is
            populated by ``create_app()`` after seeding; production and
            test paths both initialize it before any service runs.
    """
    return frozenset({
        ref_cache.status_id(StatusEnum.DONE),
        ref_cache.status_id(StatusEnum.RECEIVED),
    })


def is_identity_move(row, new_status_id: int) -> bool:
    """Return whether *row* is ALREADY in *new_status_id* -- nothing to do.

    **The predicate that tells a re-submit from a transition**, and it is the
    one question a settle verb must ask before it books anything.  A door
    reached twice -- an HTMX double click, a stale tab, a re-POST of an empty
    body -- asks for the status the row already holds, and answering that by
    re-running the settle re-derives a row whose money has already moved.

    Measured before this predicate existed: a settle that booked a human's
    ``$90.00`` correction against a ``$500.00`` plan, replayed with an empty
    body, came back ``$90.00`` on the ``derived`` basis -- the figure survived
    and the stored answer to "did a human correct this" did not
    (the row's ``settled_basis_id`` then, the movement's ``figure_source_id``
    now; finding **N-241**).

    **That measurement is now closed twice over, and what this UNIQUELY protects
    is narrower than it was.**  ``Settlement.from_settle`` honours a RETAINED
    ``corrected`` record, so a replayed MANUAL settle would re-book the same
    figure on the same basis even without this.  What still needs it is the
    ENVELOPE replay: ``transaction_service.settle_from_entries`` refuses an
    immutable row by precondition, so a double-clicked Mark Paid on an
    already-Paid envelope would answer 400 where the user has done nothing wrong
    and nothing needs doing.  That is the case
    ``TestAReplayedSettleIsANoOp.test_a_replayed_close_on_an_ENVELOPE_is_a_no_op_not_a_400``
    pins -- and it is pinned because the guard had NO firing control at all
    until then: the full 9,611-test suite passed with the early return disabled,
    measured 2026-08-17.

    **It is deliberately NARROWER than "already in the settled band"**, and the
    difference is a refusal.  Asking the band swallows ``Paid -> Received``,
    which is an ILLEGAL transition ``state_machine.verify_transition`` exists to
    reject (the maps are keyed on the STATUS and give ``done`` no edge to
    ``received``), and turns its designed 400 into a silent 200.  Only the
    identity move is nothing to do; every other move still owes the state
    machine an answer.  The sharper example used to be ``Settled -> Paid``,
    out of the terminal ARCHIVE plan step **X-am** deleted; the rule did not
    change, only the illegal move available to illustrate it.

    Polymorphic over both status-bearing models for the reason
    :func:`enters_settled_band` below states: the question is over ``status_id``
    against ``ref.statuses.id`` and nothing else.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer`, read for its CURRENT
            ``status_id``.
        new_status_id: The status a door is asking for.

    Returns:
        True when the row already holds that status.
    """
    return row.status_id == new_status_id


def enters_settled_band(row, new_status_id: int) -> bool:
    """Return whether moving *row* to *new_status_id* SETTLES it.

    **The predicate that tells a status ASSIGNMENT from a SETTLE.**  Settling
    is entering the band from outside it: Projected -> Paid or Projected ->
    Received, the only two the state machine admits inward (Credit and
    Cancelled reach the band through Projected, never directly).

    **Staying inside the band is NOT a settle**, and that half is load-bearing.
    ``Paid -> Paid`` is an idempotent re-submit -- an HTMX double click, a stale
    tab, a Save on a popover whose status box was not touched -- and routing it
    to a settle verb would ask an immutable row to re-price itself, which an
    envelope's settle refuses by precondition and a manual settle would answer
    by re-reading a projection the row left months ago.

    Identity is now the only LEGAL within-band move, which was not true until
    plan step **X-am**: ``Paid -> Settled`` was the other one, an ARCHIVE of a
    row whose money had already moved and whose amount was already a fact, and
    it needed this same half for the same reason.  Deleting that status
    narrowed the case without changing the rule, so nothing here loosened.

    **"Legal" is doing work in that sentence.**  ``Paid -> Received`` is a
    within-band move that is not the identity, and a door CAN submit it -- this
    predicate is asked before ``verify_transition`` refuses it
    (``transaction_service._door.apply_requested_status``,
    ``status_seam._seam.apply_status_change``).  It answers False here, which is
    correct: it does not settle.  The refusal is the state machine's, and
    :func:`is_identity_move` exists to be narrower than the band precisely so
    that move keeps owing one.

    **It lives HERE rather than on either service, and the move is plan step
    X-f2-c3's.**  It was ``transaction_service``'s, published so
    ``apply_requested_status`` could dispatch on it; that leaf gave
    ``transfer_service.update_transfer`` the same dispatch -- a transfer's
    settle rule became structural there -- and a TRANSFER asking the
    transaction service "is this a settle" reads as a dependency that is not
    real.  The question is over ``status_id`` and :func:`settled_status_ids`
    and nothing else, which is this module's subject, and it is polymorphic in
    exactly the way :func:`is_projected_clause` already is: ``Transaction`` and
    ``Transfer`` both carry a ``status_id`` FK against ``ref.statuses.id``.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer`, read for its CURRENT
            ``status_id``.
        new_status_id: The status a door is asking for.

    Returns:
        True when the move crosses INTO the settled band from outside it.
    """
    settled = settled_status_ids()
    return row.status_id not in settled and new_status_id in settled


def reverts_to_projected(row, new_status_id: int) -> bool:
    """Return whether moving *row* to *new_status_id* sets it BACK to Projected.

    **The predicate that tells a revert from a re-submit** (plan step
    ``pay_calendar:C18-a``, ruling **R-PC97**): a paid, received, credited or
    cancelled row asked back into Projected -- each of which the state machine
    admits, since no status is terminal -- and not a Projected row asked for
    Projected again, which is the identity move (:func:`is_identity_move`)
    and changes nothing.  Asked by
    ``planned_rows_books.reject_revert_below_the_books``, the refusal each row
    type's status door runs before it writes.  Polymorphic over both
    status-bearing models for the reason :func:`enters_settled_band` states.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer`, read for its CURRENT
            ``status_id``.
        new_status_id: The status a door is asking for.

    Returns:
        True when *row* is not Projected and the move would make it so.
    """
    return (
        new_status_id == ref_cache.status_id(StatusEnum.PROJECTED)
        and not is_projected(row)
    )


def delete_returns_to_plan(row, *, took_off: bool) -> bool:
    """Return whether the delete that took *row*'s payments off sets it back to Projected.

    **Ruling R-BAL246's one statement** (developer 2026-10-09, "Back as a
    plan, now"): a delete that takes a SETTLED item's payment records off the
    books (ruling **credit_card:R-CC75**) returns it to a plan, so a restore
    brings back a plan -- never a ``$0.00`` close, "the bank took nothing",
    which a loan reads as a missed installment and charges its interest.  A
    settled item that held nothing IS a ``$0.00`` close and keeps its
    status; an unsettled one keeps its status whatever came off -- a
    Projected item is a plan already, and a Cancelled one stays Cancelled
    even when kept payment records came off it.  Polymorphic over both
    status-bearing models for the reason :func:`enters_settled_band` states:
    the transfer delete asks it
    (``transfer_service._status.return_to_plan``), and the row delete is owed
    the same (ledger row **credit_card:CC-387**), each through its own kind's
    one status door.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer` being deleted, read for
            its CURRENT ``status_id``.
        took_off: Whether the delete took at least one payment record off.

    Returns:
        True when *row* is settled and the delete took a payment off.
    """
    return took_off and row.status_id in settled_status_ids()


#: Why a settled ROW can carry no day, and the repair: :func:`settled_day`'s
#: cause sentence for :func:`require_settled_day`.
_ROW_UNDATED_CAUSE = (
    "Every settled row is given one by status_seam.apply_status_change (the "
    "single status write door) and by migration a3f7c8e21b64's backfill, so "
    "this row was written by neither -- most likely a bulk query.update() on "
    "status_id, or a fixture constructing the row with a settled status "
    "directly.  Route the write through the seam"
)


def settled_day(transaction_id: int, settled_on: date | None) -> date:
    """Return the civil day a SETTLED transaction's money moved, or refuse.

    **The ONE accessor for "which day did this cash move", and it replaced
    ELEVEN derivations** (plan step X-f1, ruling R-EC).  Until then every
    consumer took ``transactions.paid_at`` -- the instant the user clicked --
    and converted it to a display-timezone civil day with the row's pay-period
    start as a fallback: the read fold, the posting walk's two loaders, the
    posting writer's two entry-date helpers, the loan payment writer, the loan
    fold's visibility rule, the confirmed-statement reader's two loaders, the
    loan interest tax-year basis, and ``Transaction.days_paid_before_due``.
    Fourteen sites over three helper layers, all computing one fact the row now
    stores.  Callers pass ``(id, row.settled_on)`` and get the day.

    It lives beside :func:`settled_status_ids` because it is that predicate's
    other half.  A row is settled if and only if it carries a settle day, and
    both facts are written by ONE statement -- ``status_seam.apply_status_change``
    is the single door that assigns ``status_id``, and it assigns
    ``settled_on`` in the same call.  That is why there is no CHECK constraint:
    the predicate lives in ``ref.statuses`` and a constraint cannot join, so the
    invariant is held structurally at the write door rather than declared at the
    storage tier.

    Its companion question -- "had this row's cash moved YET, at date D" -- is
    :func:`app.utils.dates.has_settled_by`, which lives one module over on a
    purity argument its callers force (see that function).  The two take
    opposite positions on a missing day, and each is right for its own caller.

    **A missing day is REFUSED, not defaulted, and that is the point of the
    function.**  The derivation this replaced fell back to the pay period's
    ``start_date`` whenever the instant was NULL, which was a guess the reader
    could not see; the day is now a stored fact, so its absence on a settled row
    means the invariant above is broken and no fallback is honest.  Silently
    dating such a row would put real money on a day nothing recorded, and
    silently DROPPING it would remove money from a balance without saying so.
    The condition is reachable rather than theoretical: a bulk
    ``query.update({status_id: paid})`` bypasses the ORM session entirely
    (finding N-65 measured 41 such sites in the suite), and a fixture that
    constructs ``Transaction(status_id=<paid>)`` directly never passes the seam.
    Both produce exactly this row, and both should fail where they are written.

    **Its refusal is :func:`require_settled_day`'s since leaf
    balance:X-bi-6-4d-1**, the one refusal of a missing settle day, which the
    loan walk reaches too for a settled transfer's loan-side record
    (:func:`app.services.loan_ledger.payment_visible_on`).  This binds a ROW's
    subject and cause to it, and the refusal's text is byte-identical to the
    one this function spelled itself until then.

    Args:
        transaction_id: The row's id, named in the refusal so a broken row is
            identifiable without re-querying.
        settled_on: The row's stored ``settled_on``, read either off the ORM
            attribute or out of a batched query tuple -- both shapes occur
            among the call sites, which is why this takes the VALUE rather than
            the row.

    Returns:
        The civil day the row's cash moved.

    Raises:
        UndatedSettleError: When *settled_on* is ``None``.  The caller is
            reading a row it believes is settled, so a missing day is a broken
            invariant rather than an empty value.
    """
    return require_settled_day(
        settled_on,
        subject=lambda: f"Transaction {transaction_id}",
        cause=_ROW_UNDATED_CAUSE,
    )


def require_settled_day(
    settled_on: date | None, *, subject: Callable[[], str], cause: str,
) -> date:
    """Return a settled holder's stored day, or REFUSE: the one refusal of a missing day.

    **One rule with two holders, stated once** (leaf balance:X-bi-6-4d-1,
    rule 14).  "A settled holder's missing settle day is refused, never
    defaulted" is ruling R-EC's (plan step X-f1), and two holders ask it: a
    ROW, whose day is its own ``settled_on`` (:func:`settled_day`, its
    binding), and a settled TRANSFER's loan-side payment, whose day is its
    covering movement's (:func:`app.services.loan_ledger.payment_visible_on`).
    Both run the same test, raise the same error, for the same reason; they
    differ only in WHAT is named and WHY it can happen, so those two are the
    arguments and the sentence frame is here.  Each caller has already decided
    its holder is settled; this answers only "the day, or refuse".

    **The subject is built only when refusing**, because naming a transfer's
    payment in words reads its relationships, and the loan walk asks this for
    every settled payment it folds.  The happy path calls nothing.

    Args:
        settled_on: The holder's stored day, ``None`` when it carries none.
        subject: Returns the words naming the holder, e.g.
            ``"Transaction 4242"``; called once, and only on the refusal.
        cause: Why such a holder can exist and how to repair it, ending
            without a full stop: the frame continues it with "; there is
            deliberately no fallback day ...".

    Returns:
        *settled_on*, when it is a day.

    Raises:
        UndatedSettleError: When *settled_on* is ``None``.
    """
    if settled_on is None:
        raise UndatedSettleError(
            f"{subject()} is in a settled status but carries no settled_on, "
            "so the day its money moved is unknown.  "
            f"{cause}; there is deliberately no fallback day, because "
            "inventing one would place real money on a day nothing recorded."
        )
    return settled_on


def status_contributes_to_balance(txn) -> bool:
    """Return True iff *txn*'s status alone permits balance contribution.

    The status-only half of :func:`is_balance_contributing`: returns
    ``False`` for an ``excludes_from_balance=True`` status
    (``Credit``, ``Cancelled``) and ``True`` for every other status,
    *without* consulting ``txn.is_deleted``.  Sized for callers that
    have already pre-filtered deleted rows upstream (the
    investment-projection Python iteration sites consume already-
    SQL-filtered shadow contribution transactions) and whose duck-
    typed test fakes therefore do not carry an ``is_deleted``
    attribute.

    ``is_balance_contributing`` is defined as
    ``not txn.is_deleted and status_contributes_to_balance(txn)`` so
    the two predicates can never disagree about the status-only half
    of the rule.

    Args:
        txn: any object with a ``status`` attribute that, when not
            ``None``, carries an ``excludes_from_balance`` boolean.
            ``Transaction`` and the ``FakeContribTransaction`` test
            duck-types both satisfy this; ``is_deleted`` is NOT
            consulted.

    Returns:
        ``True`` if the status row's ``excludes_from_balance`` is
        ``False`` (or the status is ``None`` -- treated as
        contributing, matching the
        valuation's own fallback); ``False`` if
        the status carries ``excludes_from_balance=True``.
    """
    return not (txn.status is not None and txn.status.excludes_from_balance)


def is_balance_contributing(txn: Transaction) -> bool:
    """Return True iff *txn* contributes its effective amount to a balance.

    Mirrors the gate in ``row_valuation.fixed_contribution`` exactly: a
    soft-deleted transaction contributes zero, a transaction whose
    status has ``excludes_from_balance=True`` (``Credit``, ``Cancelled``)
    contributes zero, everything else contributes its effective amount.
    The two predicates share one definition so the in-Python balance
    loop and any consumer that wants to ask "should this row's amount
    be summed" cannot drift apart from the valuation rule.

    Args:
        txn: a ``Transaction`` instance. Both ``is_deleted`` and the
            ``status`` relationship are expected to be loaded;
            ``Transaction.status`` is declared ``lazy="joined"`` so a
            standard ORM load satisfies this without explicit
            ``selectinload``.

    Returns:
        ``True`` if the transaction's contribution would be a
        non-excluded value (i.e. it participates in balance projection);
        ``False`` if either soft-deleted or carrying an
        ``excludes_from_balance`` status.

    Note:
        A ``txn`` with ``status is None`` is treated as contributing.
        This matches ``row_valuation.fixed_contribution``, which guards the
        exclusion behind ``if self.status and ...``: an unloaded or
        in-construction status is not evidence of exclusion, so the
        predicate defers to the valuation's own fallback
        behavior. Callers that need to assert a fully-loaded status
        should do so at their own boundary.
    """
    if txn.is_deleted:
        return False
    return status_contributes_to_balance(txn)


def is_projected(row: Transaction | Transfer) -> bool:
    """Return True iff *row*'s status is ``Projected``.

    Centralizes the inline ``status_id != ref_cache.status_id(
    StatusEnum.PROJECTED)`` and ``status_id == projected_id`` comparisons
    that recur across ``cash_ledger``, ``grid.py``, and
    ``credit_workflow.py``. The comparison is pure status equality and
    does not consider ``is_deleted`` -- callers that need the combined
    "live and balance-contributing" gate compose this predicate with
    ``is_balance_contributing``, or use ``is_balance_contributing``
    alone when they only need the exclusion set semantics.

    **It takes a ``Transaction`` OR a ``Transfer``**, which is the same
    generality :func:`is_projected_clause` has always had one tier down: both
    tables carry a ``status_id`` into ``ref.statuses``, and "is this row still
    Projected" is one question with one answer. It was annotated
    Transaction-only until plan step X-au-b needed it for a transfer
    (:func:`app.services.cash_ledger.resolve_transfer_amount`), where the only
    alternative was a second spelling of this comparison -- the drift this
    module exists to prevent.

    Args:
        row: a ``Transaction`` or ``Transfer`` with ``status_id`` populated.

    Returns:
        ``True`` if ``row.status_id`` equals the cached integer ID for
        ``StatusEnum.PROJECTED``; ``False`` for every other status,
        including ``Paid``, ``Received``, ``Credit`` and ``Cancelled``.

    Raises:
        RuntimeError: propagated from ``ref_cache.status_id`` if the
            reference cache has not been initialized.
    """
    return row.status_id == ref_cache.status_id(StatusEnum.PROJECTED)


def is_credit(txn: Transaction) -> bool:
    """Return True iff *txn*'s status is ``Credit``.

    Centralizes the inline ``status_id == credit_id`` /
    ``status_id != credit_id`` comparisons in
    ``credit_workflow.py`` (mark-as-credit idempotency check;
    unmark-credit precondition guard) and ``entry_service.py``
    (block entries on credit-status transactions). Pure status
    equality, does not consider ``is_deleted``.

    Args:
        txn: a ``Transaction`` instance with ``status_id`` populated.

    Returns:
        ``True`` if ``txn.status_id`` equals the cached integer ID for
        ``StatusEnum.CREDIT``; ``False`` for every other status.

    Raises:
        RuntimeError: propagated from ``ref_cache.status_id`` if the
            reference cache has not been initialized.
    """
    return txn.status_id == ref_cache.status_id(StatusEnum.CREDIT)


def is_cancelled(txn: Transaction) -> bool:
    """Return True iff *txn*'s status is ``Cancelled``.

    Centralizes the inline ``status_id == cancelled_id`` comparisons
    in ``app/routes/grid/`` (skip-cancelled row-key collection,
    mirroring the templates' ``!= STATUS_CANCELLED`` guards in
    ``grid.html``, ``_mobile_grid.html``) and ``entry_service.py``
    (block entries on cancelled-status transactions). Pure status
    equality, does not consider ``is_deleted``.

    Note that this predicate is intentionally narrower than
    ``is_balance_contributing``: a ``Credit`` transaction is excluded
    from balance contribution but is NOT cancelled, and the grid
    still renders the Credit row (with strike-through styling)
    whereas a Cancelled row is omitted from the row-key set.

    Args:
        txn: a ``Transaction`` instance with ``status_id`` populated.

    Returns:
        ``True`` if ``txn.status_id`` equals the cached integer ID for
        ``StatusEnum.CANCELLED``; ``False`` for every other status.

    Raises:
        RuntimeError: propagated from ``ref_cache.status_id`` if the
            reference cache has not been initialized.
    """
    return txn.status_id == ref_cache.status_id(StatusEnum.CANCELLED)


def is_done(txn: Transaction) -> bool:
    """Return True iff *txn*'s status is ``Paid`` (``StatusEnum.DONE``).

    Centralized the inline ``status_id == done_id`` comparison in
    ``entry_service``'s actual-recompute hook. Pure status equality, does
    not consider ``is_deleted``.

    **It has NO caller in ``app/`` as of plan step X-ap**, which merged that
    hook with its posting-reconcile sibling onto the settled BAND -- the two
    halves of one act were grading the same row differently (finding
    **N-229**). Reported for plan step **X-e**, whose subject is exactly the
    callerless public helper, rather than deleted inside a step scoped to the
    settle doors.

    Note on the name: ``StatusEnum.DONE`` is the enum member; the
    ref-table row carries display name "Paid" and ``is_settled=True``.
    The predicate is named ``is_done`` to match the enum identifier
    so a future renaming of the enum surfaces in this helper as well.

    Args:
        txn: a ``Transaction`` instance with ``status_id`` populated.

    Returns:
        ``True`` if ``txn.status_id`` equals the cached integer ID for
        ``StatusEnum.DONE``; ``False`` for every other status.

    Raises:
        RuntimeError: propagated from ``ref_cache.status_id`` if the
            reference cache has not been initialized.
    """
    return txn.status_id == ref_cache.status_id(StatusEnum.DONE)


def is_projected_clause(model_class):
    """Return a SQLAlchemy boolean clause matching ``Projected``.

    Centralizes the eleven SQLAlchemy filter sites that previously
    each bound ``projected_id = ref_cache.status_id(StatusEnum.
    PROJECTED)`` locally and wrote ``Model.status_id == projected_id``
    inline (D6-09 (ii); five in ``app/routes`` for the template /
    transfer archive workflow and the entries auto-clear, six in
    ``app/services`` for the dashboard / entries / carry-forward
    queries). After this commit those sites all read
    ``is_projected_clause(Transaction)`` /
    ``is_projected_clause(Transfer)`` so the rule "what does a
    Projected filter look like in SQL" is defined once.

    The clause is intentionally polymorphic over the model class
    because ``Transaction`` and ``Transfer`` both carry an FK named
    ``status_id`` against ``ref.statuses.id`` and the D6-09 (ii)
    register covers both. Other models with a different status
    column shape would need their own helper; passing one in here
    is a usage error caught by the missing-attribute ``AttributeError``
    at filter-build time.

    Args:
        model_class: ``app.models.transaction.Transaction`` or
            ``app.models.transfer.Transfer``. Any other class with a
            ``status_id`` column attribute also works (the helper is
            structurally typed) but the two listed are the only
            current callers.

    Returns:
        A SQLAlchemy boolean expression equivalent to
        ``model_class.status_id == <PROJECTED.id>`` suitable for
        ``query.filter(...)``.

    Raises:
        RuntimeError: propagated from ``ref_cache.status_id`` if the
            reference cache has not been initialized.
    """
    return model_class.status_id == ref_cache.status_id(StatusEnum.PROJECTED)


def balance_contributing_clause(model_class=Transaction):
    """Return a SQLAlchemy boolean clause matching ``is_balance_contributing``.

    The Python predicate and this ORM filter are generated from the
    same ``ref_cache``-backed accessors (``balance_excluded_status_ids``)
    so they classify any transaction identically -- the C2-6 parity
    test enforces this on a mixed-status seeded set. Callers compose
    the clause into any query over ``Transaction`` without needing to
    ``.join(Status)``: ``Transaction.status_id`` is the discriminator,
    and the excluded-ID set is the cached lookup.

    **Polymorphic over the model class since leaf ``balance:X-bi-6-1b``, the
    way :func:`is_projected_clause` has always been**: a display reader that
    draws a transfer as a LEG read off its parent loads the parents from
    ``budget.transfers`` under the SAME "still counts" rule it loads its rows
    under, and ``Transfer`` carries the same two columns.  The default keeps
    every ``Transaction`` caller unchanged.

    Args:
        model_class: ``app.models.transaction.Transaction`` (the default) or
            ``app.models.transfer.Transfer`` -- any class carrying
            ``is_deleted`` and a ``status_id`` against ``ref.statuses``.

    Returns:
        A SQLAlchemy ``and_`` clause equivalent to
        ``model_class.is_deleted IS FALSE AND
        model_class.status_id NOT IN (Credit.id, Cancelled.id)``.
        Suitable for ``query.filter(balance_contributing_clause())``
        on any select rooted at ``Transaction``, or
        ``balance_contributing_clause(Transfer)`` on one rooted at
        ``Transfer``.

    Raises:
        RuntimeError: propagated from ``balance_excluded_status_ids``
            if the reference cache has not been initialized.
    """
    return and_(
        model_class.is_deleted.is_(False),
        model_class.status_id.notin_(balance_excluded_status_ids()),
    )


def owner_declared_clause():
    """Return the SQL clause matching a level the OWNER declared.

    **The ONE spelling of the interim narrowing every cash-side reader of
    the level relation composes** (plan step ``balance:X-bj-1``, ruling
    **R-JN**).  Since that step ``budget.account_anchor_history`` holds the
    bank's statement placements beside the owner's true-ups, and until
    ``X-f3c-5`` stops an assertion resetting a plain account the cash fold
    must RESET at the owner's rows and at nothing else -- a bank's closing is
    an observation that moves no balance (ruling **R-IS**), and letting it
    reset the fold would move money at a step that moves none.  So the
    resolver (``cash_ledger._facts._governing_row``), the clearing boundary
    (``reconciled_through``), the replay (``cash_anchor_facts``) and the
    books bound (``earliest_assertion_day``) all filter through this clause
    rather than each writing ``statement_import_id IS NULL`` for itself.
    ``scripts/integrity_check.py``'s BA-01 spells the same predicate in raw
    SQL, the one second spelling, named there.

    **It is a FENCE with a named deleter.**  The flip deletes the reset and
    this clause with it; after it a level is a check, not a reset, and no
    cash reader has a reason to prefer one observer over the other.

    Returns:
        A SQLAlchemy boolean expression equivalent to
        ``AccountAnchorHistory.statement_import_id IS NULL``, suitable for
        ``query.filter(...)`` on any select rooted at that model.
    """
    return AccountAnchorHistory.statement_import_id.is_(None)
