"""Loan posting orchestration: unified per-scenario sync, all-scenarios, backfill.

The entry points that drive a loan's FULL genesis reconcile -- the opening /
true-up anchor corrections and the per-payment split corrections, ONE
reconcile since plan step ``balance:X-bi-6-3`` (:mod:`._corrections`, ruling
**R-BAL102**) -- off ONE running-balance walk
(:func:`app.services.loan_ledger.walk_loan_ledger`) per (loan, scenario), so the two halves share
the balance interest accrued on and no chokepoint walks the loan twice:

* :func:`sync_loan_postings` -- one scenario, one walk, one reconcile.
* :func:`sync_loan_postings_all_scenarios` -- every scenario a loan has payments
  in, PLUS the owner's baseline (so a payment-less new loan's opening still
  posts).  A balance true-up, a rate change, and a params edit all live on the
  loan ACCOUNT, not a scenario, so they re-base the confirmed split AND the
  anchor corrections in every scenario at once.
* :func:`sync_all_scenarios_or_duplicate` -- the same, wrapped for the two
  chokepoints that append a unique-constrained row and translate its duplicate.
* :func:`backfill_all_loan_postings` -- the one-time historical sweep, reusing
  the identical go-forward sync so backfill == go-forward by construction.

Flushes but never commits -- the caller owns the transaction boundary.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import loan_loaders
from app.services._posting_reconcile import account_owner_id
from app.services.row_valuation import settled_contribution
from app.services.posting_service import (
    PostingError,
    _ledger_account_for,
    sync_transfer_postings,
)
from app.services.scenario_resolver import get_baseline_scenario
from app.services.transfer_legs import (
    movement_parent,
    movement_parent_loads,
    parent_of,
)
from app.services.user_write_lock import lock_every_user_writes
from app.utils.db_errors import is_unique_violation
from app.utils.money import round_money

from app.services.loan_ledger import (
    LoanLedgerWalk,
    dated_deltas,
    walk_loan_ledger,
)

from ._corrections import reconcile_loan_corrections
from ._linked_ledger import _movement_nets_by_date, _visible_nets

_ZERO_MONEY = Decimal("0.00")


def scenarios_with_loan_payments(loan_account_id: int) -> list[int]:
    """Return the scenarios that carry a payment for a loan.

    The distinct ``scenario_id`` set over the live transfers INTO the loan --
    the scenarios whose split corrections a loan-GLOBAL change re-bases.  It
    read the loan's non-deleted income SHADOWS until plan step
    balance:X-bi-6-4b; every live transfer carried one live income shadow in
    its own scenario then (Transfer Invariants 1 and 3), so the two sets were
    one (measured equal on the 2026-09-23 21:17 production dump).  A balance
    true-up, a rate change, and a params edit all live on the loan ACCOUNT,
    not a scenario, so they move the
    confirmed-payment split in every scenario the loan has payments in;
    :func:`sync_loan_postings_all_scenarios` reconciles each in turn (adding the
    baseline so a payment-less loan is not skipped).  A projected-only scenario
    is harmlessly included -- its sync is a no-op, since only a settled payment
    posts a correction.

    Args:
        loan_account_id: The loan whose payment scenarios to enumerate.

    Returns:
        The distinct scenario ids, ascending.
    """
    rows = (
        db.session.query(Transfer.scenario_id)
        .filter(
            Transfer.to_account_id == loan_account_id,
            Transfer.is_deleted.is_(False),
        )
        .distinct()
        .all()
    )
    return sorted(row[0] for row in rows)


def sync_loan_postings(loan_account_id: int, scenario_id: int) -> None:
    """Reconcile a loan's FULL genesis ledger in one scenario, off ONE walk.

    The unified per-scenario chokepoint: walks the loan's anchors and confirmed
    payments ONCE (:func:`walk_loan_ledger`), then reconciles BOTH halves off
    that single walk in ONE pass -- the opening / true-up anchor corrections
    and the per-payment split corrections
    (:func:`._corrections.reconcile_loan_corrections`, one posted read and
    one delta loop over the three correction kinds since plan step
    ``balance:X-bi-6-3``, ruling **R-BAL102**).  Because a pre-true-up
    payment change moves a later true-up's ``owed_before`` (and every
    payment's split rides the same running balance), the two halves must
    reconcile TOGETHER off the same walk; splitting them would risk a stale
    true-up or a double walk (the two full-loan walks a pair of self-contained
    syncs would each cost).

    Idempotent and self-healing: a re-run at the same state writes nothing.
    The loan-side reconciles touch ONLY the loan's own ledgers (linked,
    interest, escrow, refund, opening-equity); the lineage-transfer pass
    (:func:`_reconcile_lineage_transfer_entries`, step E1a) may additionally
    RE-DATE a settled payment's Step-2 cash entry -- which touches Checking's
    ledger but moves no net anywhere: it corrects WHICH DAY the cash moved,
    never how much.  After both reconcile, the CHECKED-PROJECTION assert
    (:func:`_assert_checked_projection`, plan step E1a) verifies the linked
    ledger's per-date nets against the same walk and raises rather than
    letting a divergent ledger commit.

    **Takes no as-of, and reads no clock.**  It posts what the loan's facts say,
    every one of them; WHEN each fact becomes visible is the readers' decision,
    not this writer's (:func:`app.services.loan_ledger.walk_loan_ledger`).  So a re-run posts the
    same ledger tomorrow as today -- the property that makes these postings a
    re-derivable projection of the loan's data rather than a record of when a
    sync happened to run.  Flushes but does not commit (the caller owns the
    transaction).

    **Runs under the owner's write lock, which every caller already holds**:
    a signed-in request's command transaction takes it before its view reads
    anything of the owner's (plan step ``balance:X-bn``,
    :mod:`app.db_transaction`), and the deploy reconciles take every owner's
    at their start; plan step X-f1c3c took it here, before the walk, until
    then.  Registration never reaches this: it creates no loan.  Both
    reconciles below are read-modify-writes -- read what is posted, subtract
    it from what the walk says, write the difference -- and two of them
    interleaved both compute their delta against the same posted state.
    The loan half never had even the accidental serialisation the cash half
    lost at ruling R-EN: a loan true-up appends to
    :class:`~app.models.loan_anchor_event.LoanAnchorEvent` and UPDATEs no row,
    so nothing has serialised this since Commit 16.  R-EN cited the loan
    contract as the precedent for deleting the cash lock, which is how a
    defect became a rationale.  See :mod:`app.services.user_write_lock`.

    **A loan with no resolvable owner still FAILS LOUD, and an earlier version
    of this step made it a silent no-op.**  ``account_owner_id`` returns
    ``None`` only when the ``accounts`` row is absent (``user_id`` is NOT
    NULL), and at that point ``_ledger_account_for`` below raises
    ``PostingError`` for the missing chart-of-accounts pairing -- which is the
    disposition this codebase wants for a broken invariant.  *Until plan step
    ``balance:X-bn`` this function keyed a lock of its own on that owner and
    skipped the acquisition when there was none, leaving the raise to fire.*
    *The claim that this matched "the same
    disposition the reconciles below already have" was FALSE and was caught by
    a neutral adversarial review: the cash twin no-ops on a missing account by
    design, this one raises, and the two are not the same rule.*

    Args:
        loan_account_id: The loan whose full ledger to reconcile.
        scenario_id: The budget scenario to reconcile within.

    Raises:
        PostingError: When the reconciled ledger does not equal the fold of the
            loan's events (:func:`_assert_checked_projection`).
    """
    walk = walk_loan_ledger(loan_account_id, scenario_id)
    # ONE linked-ledger resolution per sync, shared by the lineage probe and
    # the assert (each used to resolve its own -- a redundant query).
    linked_ledger_id = _ledger_account_for(loan_account_id).id
    _reconcile_lineage_transfer_entries(linked_ledger_id, scenario_id, walk)
    reconcile_loan_corrections(loan_account_id, scenario_id, walk)
    _assert_checked_projection(
        loan_account_id, scenario_id, walk, linked_ledger_id,
    )


def _reconcile_lineage_transfer_entries(
    linked_ledger_id: int, scenario_id: int, walk: LoanLedgerWalk,
) -> None:
    """Reconcile the loan's stale-dated Step-2 cash entries, wherever they hide.

    The loan sync's third reconcile (plan step E1a), and the reason the
    checked-projection assert can hold on REAL data: the linked ledger's
    per-date nets include the TRANSFER-source cash legs, which this package
    does not write -- and the pre-E1a transfer reconcile was date-blind, so a
    historical revert / re-settle left net-zero-in-TOTAL pairs straddling two
    DATES (measured on the real Mortgage: +$2,410.95 at 2026-07-02 against
    its reversal dated 2026-06-18 -- the old latest-``entry_date`` reversal
    rule's residue).  The loan-side reconciles cannot touch those entries, so
    without this pass the assert would fire on residue no loan write can heal
    -- the C9a lesson (a guard that 500s its own route) in posting form.

    **The candidate set comes from the LEDGER, not the walk** (the step's
    adversarial review, H2): a REVERTED or SOFT-DELETED payment is outside
    the walk's settled set, but its pre-E1a cash entries can carry exactly
    the same cross-date residue -- so the probe reads every loan-side
    movement's non-zero per-date nets off the linked ledger
    (:func:`._linked_ledger._movement_nets_by_date`; a payment's cash leg is
    its loan-side covering movement's own entry since plan step
    ``balance:X-bi-6-3``, ruling **R-BAL45**) and compares each against what
    a clean ledger holds: for a settled walk payment, its full cash at its
    settle date (the SAME leaf clock the fold and the writer share -- the
    event's ``visible_on``, read once by the stream's builder through
    :func:`app.services.loan_ledger.payment_visible_on`), keyed by the
    payment leg's record, its covering movement; for any other movement,
    nothing (every date nets zero).  Only a movement that fails that comparison is re-synced, so
    the steady-state cost is the ONE probe query; each stale movement's
    TRANSFER runs :func:`app.services.posting_service.sync_transfer_postings`
    -- the one date-aware reconcile for those entries, which reads each
    movement's own state under its transfer LEG (a soft-deleted payment's
    legs are non-contributing, the transfer's gate read through the leg since
    leaf ``balance:X-bi-6-4a``, so they reverse) and brings the legacy
    one-entry source to zero besides.  A
    pre-guard legacy transfer OUT of the loan (the R6 KEEP arm) never matches
    its walk expectation and re-syncs as a no-op each pass -- bounded, and
    such a loan is already assert-blocked as an N-11-class F1 item.

    Note the honest scope widening: the loan sync may RE-DATE a payment's
    cash entry, which touches the CHECKING side of that entry -- moving no
    net anywhere, only correcting WHICH DAY the cash moved (the entry's own
    account-anchor self-heal runs inside the transfer sync).

    Args:
        linked_ledger_id: The loan's linked ledger account id (resolved once
            by :func:`sync_loan_postings`).
        scenario_id: The budget scenario the reconcile runs in.
        walk: The loan's walk (supplies each settled payment's expected
            settle date and cash).

    Raises:
        PostingError: From the transfer sync, for a broken chart-of-accounts
            pairing (fail-loud, same as every posting path).
    """
    posted = _movement_nets_by_date(linked_ledger_id, scenario_id)
    # Zero-filtered SYMMETRICALLY with the posted side (its zero-net dates are
    # dropped): a settled payment with a zero cash contribution -- a ``$0.00``
    # close, its transfer's settled figure ``0.00`` and no record on either
    # side -- posts nothing, so expecting ``{date: 0.00}``
    # would flag it stale and re-sync it (a no-op) on every pass forever.
    #
    # A loop rather than a comprehension because the cash is needed BOTH as the
    # value and by the zero filter, and the comprehension form read it twice per
    # split -- two valuations of one payment, which is the shape that lets a
    # filter and the figure it admits come to disagree.  Every source here is
    # a leg from ``loan_loaders.settled_income_legs`` (the walk's own
    # loader, plan step balance:X-bi-6-4b), so it has SETTLED and
    # ``settled_contribution`` of its parent answers the figure its transfer
    # stores --
    # and REFUSES a leg outside the settled half rather than pricing its
    # plan, so this loop's precondition is stated by the call it makes and
    # not only by the loader above it.  A leg with a non-zero cash holds its
    # RECORD, its covering movement, whose id is the key the posted side
    # groups by; a ``$0.00`` close holds none and is skipped by the zero
    # filter.  A leg is always
    # a transfer's, so the ``transfer_id is None`` skip the shadow needed is
    # gone.
    expected: dict[int, dict[date, Decimal]] = {}
    transfer_by_movement: dict[int, int] = {}
    for outcome in walk.settled_splits:
        leg = outcome.source
        cash = round_money(settled_contribution(parent_of(leg)))
        if cash == 0:
            continue
        expected[leg.record.id] = {outcome.visible_on: cash}
        transfer_by_movement[leg.record.id] = leg.transfer.id
    stale_ids = {
        movement_id
        for movement_id in set(posted) | set(expected)
        if posted.get(movement_id, {}) != expected.get(movement_id, {})
    }
    if not stale_ids:
        return
    # A posted movement the walk does not expect -- a reverted or soft-deleted
    # payment's, or one a legacy transfer OUT of the loan wrote -- names its
    # transfer through the ledger writer's ONE resolution of a movement's
    # parent (``transfer_legs.movement_parent``, plan step
    # balance:X-bi-6-4b), over one load of all of them with the loader
    # options that resolution publishes (``transfer_legs.movement_parent_loads``
    # since leaf balance:X-bi-6-4d-1, ledger row BAL-579: which relationships
    # the chain walks -- a movement's row, or its side's transfer -- is that
    # package's to name).  Every one resolves to a leg: only a transfer's
    # movement posts under the transfer-movement source, and a hard-deleted
    # movement's residue was excluded above.  It was NOT
    # ``transfer_movement_rows`` while a movement could sit under a DEAD
    # shadow, which that loader dropped and this probe had to name so its
    # postings reversed; a side record hangs off its transfer since plan
    # step balance:X-bi-6-4d-2, so no movement sits under a shadow now.
    unresolved = stale_ids - set(transfer_by_movement)
    if unresolved:
        transfer_by_movement.update(
            (movement.id, movement_parent(movement).transfer.id)
            for movement in db.session.query(TransactionEntry)
            .options(*movement_parent_loads())
            .filter(TransactionEntry.id.in_(unresolved))
        )
    transfers = (
        db.session.query(Transfer)
        .filter(Transfer.id.in_({transfer_by_movement[m] for m in stale_ids}))
        .order_by(Transfer.id)
        .all()
    )
    for xfer in transfers:
        sync_transfer_postings(xfer)


def _assert_checked_projection(
    loan_account_id: int,
    scenario_id: int,
    walk: LoanLedgerWalk,
    linked_ledger_id: int,
) -> None:
    """Assert the posted linked ledger equals the fold of the loan's events.

    **The E1a invariant: ``sum(postings) == fold(ACTUAL events)``, checked at
    WRITE time -- per visible date, not just in total.**  The walk's events,
    re-keyed by the day each counts from
    (:func:`app.services.loan_ledger.dated_deltas` -- the ONE statement of the
    clock, shared with the balance seam's fold), must match the linked
    ledger's per-``entry_date`` nets exactly: same dates, same amounts, in
    posting space (a posted net is the negated owed delta).  Anything else
    means the posted ledger is no longer a faithful projection of the loan's
    source facts, and every reader of the general ledger -- the statements,
    the reconciliation oracle, the payment-history table -- is looking at a
    lie.  Raising HERE, inside the write transaction, turns that lie into a
    rollback at the write that caused it instead of a silent divergence found
    months later (finding B-5's invariant, open since the archived audits).

    The comparison is per DATE because a right amount at a wrong date is
    still a wrong ledger: the fold counts each event from its ``entry_date``
    (step C2's one clock), so a stale-dated posting moves balances on every
    day between the two dates.  The reconciles this runs behind are
    date-aware for exactly that reason (finding N-13), so a legitimate edit
    -- a settled ``settled_on`` move, a params change, a true-up -- converges
    BEFORE this check reads the ledger back and can never trip it.

    The two known ways to fail it, named so the error is actionable:

    * a reconcile defect (a target computed or dated wrong) -- fix the
      reconcile, never this check;
    * a posting the walk cannot model -- a raw transaction typed onto the
      loan or a transfer out of it (the N-11 class, forbidden at the source
      since steps BG / R6).  Such a row is an F1-class DATA item for a human:
      the E1a ship gate swept production and found none, and the write
      guards refuse new ones, so one appearing here means a guard was
      bypassed.

    Skipped for an unconfigured loan (an empty walk: no ``LoanParams``, the
    N1 guard) -- the reconciles above posted nothing and there is no fact
    stream to check against.

    Args:
        loan_account_id: The loan whose projection to verify (names the loan
            in the failure message).
        scenario_id: The budget scenario the reconcile ran in.
        walk: The SAME walk the reconciles just posted from (one walk, both
            halves, then the check -- never a second walk that could differ).
        linked_ledger_id: The loan's linked ledger account id (resolved once
            by :func:`sync_loan_postings`, shared with the lineage probe).

    Raises:
        PostingError: When any visible date's posted net differs from the
            fold's delta -- the projection is broken and the write must not
            commit.
    """
    if not walk.anchor_corrections and not walk.payment_splits:
        return
    expected: dict[date, Decimal] = {}
    for visible_on, delta in dated_deltas(walk):
        # Posting space: the linked ledger stores the NEGATED owed delta
        # (debit-positive convention; owed = -(sum of postings)).
        expected[visible_on] = expected.get(visible_on, _ZERO_MONEY) - delta
    expected = {
        visible_on: net for visible_on, net in expected.items() if net != 0
    }
    posted = {
        entry_date: net
        for entry_date, net in _visible_nets(linked_ledger_id, scenario_id)
        if net != 0
    }
    if expected == posted:
        return
    mismatches = sorted(
        (
            on_date,
            expected.get(on_date, _ZERO_MONEY),
            posted.get(on_date, _ZERO_MONEY),
        )
        for on_date in set(expected) | set(posted)
        if expected.get(on_date, _ZERO_MONEY) != posted.get(on_date, _ZERO_MONEY)
    )
    detail = "; ".join(
        f"{on_date.isoformat()}: walk {want} vs posted {got}"
        for on_date, want, got in mismatches
    )
    raise PostingError(
        f"Loan account {loan_account_id} scenario {scenario_id}: the posted "
        f"linked ledger diverges from the fold of the loan's events at "
        f"{len(mismatches)} date(s) [{detail}].  Either a reconcile defect "
        f"(fix the reconcile) or a posting the walk cannot model (an N-11 "
        f"class row -- an F1-class data item for a human).  Refusing to "
        f"commit a ledger that no longer projects the loan's facts."
    )


def sync_loan_postings_all_scenarios(loan_account_id: int) -> None:
    """Reconcile a loan's full genesis ledger across EVERY scenario.

    The loan-GLOBAL chokepoint entry point (loan-params create / edit, a balance
    true-up, a rate change): the anchor and rate live on the loan account, not
    the scenario, so such a change re-bases the confirmed-payment split AND the
    anchor corrections in every scenario the loan is displayed in.  Loops
    :func:`sync_loan_postings` over the union of:

    * every scenario the loan has a payment in
      (:func:`scenarios_with_loan_payments`), and
    * the owner's BASELINE scenario -- so a payment-less loan (a brand-new loan
      at params-create, before any payment settles) still gets its opening
      posted, and so the baseline is never skipped.  The opening is per-scenario
      (postings are scenario-scoped); today only the baseline exists, so this is
      one entry in practice, but it is forward-compatible with scenario clone.

    A brand-new or unresolvable loan (no anchors) syncs nothing.  Idempotent and
    self-healing.  Flushes but does not commit (the caller owns the transaction).

    **Runs under the owner's write lock, which every caller already holds**
    (:func:`sync_loan_postings` says whose it is; plan step
    ``balance:X-bn``).  That matters here beyond the per-scenario sync it
    loops: the SCENARIO SET below is itself a read this function then acts
    on, and a scenario that became live between that read and the loop
    would otherwise be missed.  *From plan step X-f1c3c until ``balance:X-bn``
    this function and the one it loops each took the lock themselves, as the
    cash twin did.*

    Args:
        loan_account_id: The loan whose corrections to reconcile across every
            scenario it is displayed in.
    """
    owner_id = account_owner_id(loan_account_id)
    if owner_id is None:
        return
    scenario_ids = set(scenarios_with_loan_payments(loan_account_id))
    baseline = get_baseline_scenario(owner_id)
    if baseline is not None:
        scenario_ids.add(baseline.id)
    for scenario_id in sorted(scenario_ids):
        sync_loan_postings(loan_account_id, scenario_id)


def sync_all_scenarios_or_duplicate(
    loan_account_id: int, unique_index_name: str,
) -> bool:
    """Re-sync a loan across scenarios and flush, reporting a same-key duplicate.

    The body of the loan-GLOBAL chokepoint that appends a unique-constrained
    row and THEN re-syncs: the ARM rate change (which adds a
    :class:`~app.models.loan_features.RateHistory` row).

    **It had a second caller until plan step X-f1c4b** -- the balance true-up,
    which appended a :class:`~app.models.loan_anchor_event.LoanAnchorEvent` and
    leaned on ``uq_loan_anchor_events_acct_date_bal_day``.  Ruling **R-EQ**
    deleted that index because a content key cannot tell a transport retry from
    a deliberate re-assertion; the anchor door decides before it stages and calls
    :func:`sync_loan_postings_all_scenarios` directly.  **This helper is right
    for the caller it kept**: ``rate_history`` is an EDITABLE table whose
    ``uq_rate_history_account_effective_date`` states a real business rule (one
    rate per effective date, corrected by editing the row), not an idempotency
    guess.
    Runs :func:`sync_loan_postings_all_scenarios` (whose queries autoflush the
    caller's just-added row) then an explicit flush, so a same-key duplicate the
    pending row collides on surfaces HERE -- inside one ``try`` -- and is
    translated to a ``False`` return instead of leaking as a 500 at a later,
    unguarded commit.  The sync and the flush share the ``try`` precisely because
    the sync's autoflush is what triggers the pending row's INSERT.

    Flushes but does NOT commit (the caller owns the transaction and its
    outcome / response handling).  On a duplicate it rolls back -- discarding
    the duplicate row and the sync's work together -- exactly as the caller's
    prior idempotent commit did; the prior committed state stands.

    Args:
        loan_account_id: The loan whose corrections to reconcile across every
            scenario it is displayed in.
        unique_index_name: The unique index / constraint the caller's pending
            row can collide on -- ``uq_rate_history_account_effective_date``,
            the only one left since ruling R-EQ deleted the loan-anchor index
            (plan step X-f1c4b).

    Returns:
        ``True`` when the sync + flush succeeded (the caller should commit);
        ``False`` when the named unique index rejected the pending row -- an
        idempotent same-key duplicate, already rolled back.

    Raises:
        IntegrityError: For any ``IntegrityError`` NOT on *unique_index_name* --
            an unexpected constraint failure must surface, never be swallowed.
    """
    try:
        sync_loan_postings_all_scenarios(loan_account_id)
        db.session.flush()
        return True
    except IntegrityError as exc:
        db.session.rollback()
        if not is_unique_violation(exc, unique_index_name):
            raise
        return False


def _reconcile_loan_account_ids(loan_account_ids: list[int]) -> list[int]:
    """Reconcile the given loan accounts across all their scenarios; return them.

    The shared body of the deploy-wide backfill
    (:func:`backfill_all_loan_postings`) and the per-user reset re-sync
    (:func:`resync_user_loan_postings`): each id is reconciled through
    :func:`sync_loan_postings_all_scenarios`, the identical go-forward sync (so a
    reconciled correction is identical to a go-forward one by construction --
    there is no second implementation that could drift).  The two public
    functions differ ONLY in their enumerator (all loans vs one user's), which
    stays with each of them; this holds the loop itself in one place.

    Flushes but does NOT commit -- the caller owns the transaction boundary.

    Args:
        loan_account_ids: The loan account ids to reconcile.

    Returns:
        ``loan_account_ids`` unchanged, for the callers' return contract.
    """
    for loan_account_id in loan_account_ids:
        sync_loan_postings_all_scenarios(loan_account_id)
    return loan_account_ids


def backfill_all_loan_postings() -> list[int]:
    """Reconcile every loan's full genesis ledger across all scenarios (backfill).

    The one-time, production-wide historical backfill: for every configured loan
    account, across all owners
    (:func:`app.services.loan_loaders.load_all_loan_account_ids`),
    reconcile its opening, true-up, and confirmed-payment corrections via
    :func:`sync_loan_postings_all_scenarios`.  This posts the corrections for any
    loan / payment settled BEFORE the go-forward wiring shipped (which therefore
    carries none), so the ledger is complete on real historical data.

    Reuses the SAME per-loan sync the go-forward chokepoints call, so a
    backfilled correction is identical to the go-forward one by construction --
    there is no second implementation that could drift.  Idempotent and
    self-healing via reconcile-to-target: a loan already carrying its go-forward
    corrections is already at target, so nothing is re-posted -- the backfill
    never double-posts, and a re-run at the same state writes nothing.

    Flushes but does NOT commit -- the caller owns the transaction boundary: the
    deploy (``scripts.init_database.initialise_database``, whose ONE commit
    covers the migrations and all three deploy hooks, and which initialises
    ``ref_cache`` first because the migration host does not; plan step
    ``balance:X-cv``), the backfill suite, or the reconciliation oracle.

    **One of the two transactions in the app that reconcile more than one
    OWNER** (the cash twin ``backfill_all_account_anchor_postings`` is the
    other), so it takes every per-user write lock up front, ascending by user id
    (:func:`app.services.user_write_lock.lock_every_user_writes`), before the
    first loan is visited.  The loop below walks loans ascending by ACCOUNT id,
    which visits owners in no particular order; pre-taking the locks makes the
    acquisition order a property of this function rather than of the loan
    table's contents, so two concurrent sweeps cannot take the same two keys in
    opposite orders.

    Returns:
        The loan account ids reconciled, ascending -- for the deploy log and
        test introspection (empty on a loan-free database).
    """
    lock_every_user_writes()
    return _reconcile_loan_account_ids(loan_loaders.load_all_loan_account_ids())


def resync_user_loan_postings(user_id: int) -> list[int]:
    """Reconcile every loan a SINGLE user owns across all its scenarios.

    The per-user counterpart to :func:`backfill_all_loan_postings`: iterates only
    *user_id*'s configured loans
    (:func:`app.services.loan_loaders.load_loan_account_ids_for_user`) and
    reconciles each through :func:`sync_loan_postings_all_scenarios`, reusing the
    identical go-forward sync so a re-synced correction is identical to a
    go-forward one by construction -- there is no second implementation that could
    drift.

    Two callers, both through ``pay_period_admin._refile_ledger``.
    ``reset_pay_periods``: a full reset wipes the user's pay periods, and
    ``journal_entries.pay_period_id`` is ON DELETE CASCADE, so the wipe
    disposes this user's loan opening / true-up genesis entries (which exist
    independently of any settled transaction, so the reset's zero-settled gate
    does NOT keep them safe).  ``remove_earlier_pay_periods`` (plan step
    ``pay_calendar:C21``, ruling **R-PC114**): its delete disposes the genesis
    entries the ledger had filed in the removed paychecks -- an entry dated
    before the first paycheck files in the earliest one.  The loan's SOURCE
    facts survive either -- :class:`~app.models.loan_params.LoanParams` and its
    true-up :class:`~app.models.loan_anchor_event.LoanAnchorEvent` rows carry
    no ``pay_period_id`` -- so this re-derives and re-posts the genesis
    corrections onto the schedule that stands, attributed to its periods.
    Scoped to the one user because both are single-user operations: unlike
    the deploy backfill neither may reconcile other owners' loans inside its
    transaction.

    Idempotent and self-healing via reconcile-to-target.  Flushes but does NOT
    commit -- the caller owns the transaction boundary (the route's commit).

    Args:
        user_id: The owning user whose loans to reconcile.

    Returns:
        The loan account ids reconciled, ascending (empty when the user has no
        loan).
    """
    return _reconcile_loan_account_ids(
        loan_loaders.load_loan_account_ids_for_user(user_id)
    )
