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

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.transfer import Transfer
from app.services import loan_loaders
from app.services._posting_reconcile import account_owner_id
from app.services.scenario_resolver import get_baseline_scenario
from app.services.user_write_lock import lock_every_user_writes
from app.utils.db_errors import is_unique_violation

from app.services.loan_ledger import walk_loan_ledger

from ._corrections import reconcile_loan_corrections


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
    It touches ONLY the loan's own ledgers (linked, interest, escrow, refund,
    opening-equity).

    **It checks nothing against the walk any more** (plan task plan#3093).
    From plan step E1a until that task a checked-projection assert compared
    the linked ledger's per-date nets, the payments' cash legs included, with
    the walk and refused the write when they differed, and a lineage pass
    re-dated a payment's stale cash entry first so the assert could hold on
    real data.  The cash legs are the transfer syncs', which plan#2914 deletes
    with every other door into the store, so the assert went first, with the
    pass that served it.  The stored ledger it graded is read by no screen;
    the worked-out ledger the screens read is graded against the fold by the
    suite (``tests/_ledger_reads.assert_fold_matches_postings_every_day``).

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

    **A loan with no account row reconciles nothing**: it has no
    :class:`~app.models.loan_params.LoanParams`, so its walk is empty.  Until
    plan task plan#3093 it raised ``PostingError`` instead, from the
    linked-ledger resolution the checked-projection assert shared, which
    went with the assert.

    Args:
        loan_account_id: The loan whose full ledger to reconcile.
        scenario_id: The budget scenario to reconcile within.

    Raises:
        PostingError: A loan account with no linked ledger account, when a
            correction books on it
            (:func:`._corrections.reconcile_loan_corrections`).
    """
    walk = walk_loan_ledger(loan_account_id, scenario_id)
    reconcile_loan_corrections(loan_account_id, scenario_id, walk)


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
