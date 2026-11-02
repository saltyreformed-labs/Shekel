"""Account anchor-posting orchestration: per-scenario sync, all-scenarios, per-user.

The entry points that drive a non-loan account's anchor reconcile -- one walk
(:func:`app.services.ledger_lines.walk_account_ledger`) feeding one reconcile
(:func:`._anchors.reconcile_account_anchor_corrections`) per (account,
scenario):

* :func:`sync_account_anchor_postings` -- one scenario.
* :func:`sync_account_anchor_postings_all_scenarios` -- the owner's baseline
  scenario PLUS every scenario holding an entry that posts on any of the
  account's chart rows.  Anchor history lives on the ACCOUNT, not a scenario, so an
  account-global event (create, true-up, direct anchor edit) re-bases the
  corrections in every scenario at once -- the cash analogue of the loan
  rule; R8 owns the residual multi-scenario policy.
* :func:`resync_user_account_anchor_postings` -- every non-loan account one
  user owns, for the single-user chokepoints (the pay-period reset, whose
  CASCade disposed the user's correction entries with the wiped periods, and
  the ``create_baseline`` recovery path, so openings are not silently
  stranded for a user who lacked a baseline at account-create time).
* :func:`backfill_all_account_anchor_postings` -- the one-time, deploy-wide
  historical sweep over every non-loan account across all owners (C7),
  reusing the identical go-forward sync so backfill == go-forward by
  construction.
* :func:`self_heal_anchor_corrections` -- the effect-time self-heal the
  tails of ``posting_service.sync_transfer_postings`` /
  ``sync_transaction_postings`` invoke after emitting source delta entries:
  a source whose posted effect changed at-or-before an account's latest
  anchor assertion moved that anchor's walked ``ledger_before``, so the
  stale correction is re-derived in the same transaction.

An amortizing loan is a documented NO-OP at every entry point (never an
error: the chokepoints legitimately iterate all of a user's accounts): loans
book their anchor corrections through
:mod:`app.services.loan_posting_service` onto their per-loan
``equity_opening`` account, and the two correction families must stay
disjoint or a loan's balance would double-book.  The per-user enumerator
excludes loans structurally (``has_amortization IS FALSE`` -- exactly the
column ``classify_account`` branches on).

Idempotent and self-healing via reconcile-to-target.  Flushes but never
commits -- the caller owns the transaction boundary.
"""

import logging
from collections.abc import Iterable

from app import ref_cache
from app.enums import PostingSourceEnum
from app.extensions import db
from app.models.account import Account
from app.models.journal_entry import JournalEntry, Posting
from app.models.ref import AccountType
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services._posting_reconcile import account_chart_row_ids
from app.services.cash_ledger import reconciled_through
from app.services.scenario_resolver import get_baseline_scenario
from app.services.ledger_lines import walk_account_ledger
from app.services.user_write_lock import lock_every_user_writes

from ._anchors import reconcile_account_anchor_corrections

logger = logging.getLogger(__name__)


def _load_non_amortizing_account(account_id: int) -> Account | None:
    """Return the account when it is a real, non-loan row; else ``None``.

    The shared entry-point guard: a missing account (deleted between the
    caller's read and the sync) and an amortizing loan (the loan posting
    package's responsibility -- see the module docstring) are both "nothing
    to sync here", never an error, because the lifecycle chokepoints
    legitimately call the sync for every account a user touches.

    Args:
        account_id: The account to load and classify.

    Returns:
        The :class:`~app.models.account.Account` (its ``account_type``
        eager-loaded via the relationship's ``lazy="joined"``), or ``None``
        when absent or amortizing.
    """
    account = db.session.query(Account).filter_by(id=account_id).first()
    if account is None:
        return None
    if classify_account(account) is AccountProjectionKind.AMORTIZING:
        return None
    return account


def sync_account_anchor_postings(account_id: int, scenario_id: int) -> None:
    """Reconcile one non-loan account's anchor corrections in one scenario.

    The per-scenario chokepoint: walks the account's anchor assertions
    against the scenario's posted sources
    (:func:`app.services.ledger_lines.walk_account_ledger`) and reconciles the resulting
    opening / true-up corrections to the ledger
    (:func:`._anchors.reconcile_account_anchor_corrections`).

    Idempotent and self-healing: a re-run at the same state writes nothing.
    Touches ONLY the account's own chart rows.  A
    missing account or an amortizing loan is a no-op (see
    :func:`_load_non_amortizing_account`).  Flushes but does not commit
    (the caller owns the transaction).

    **Runs under the owner's write lock wherever another transaction can
    reach the owner's ledger**: a signed-in request's command transaction
    takes it before its view reads anything of the owner's (plan step
    ``balance:X-bn``, :mod:`app.db_transaction`), and the deploy reconciles
    take every owner's at their start.  Registration (``/register``,
    ``scripts/seed_user.py``) reaches this through
    ``account_service.create_account`` holding none: its user is not
    committed yet, so no other transaction can reach it.  Everything below
    this line is a read-modify-write -- read what is posted, subtract it
    from what the account's facts say, write the difference -- and two of
    them interleaved both compute their delta against the same posted state,
    so the second silently under-posts by the first's amount.  *From plan
    step X-f1c3c until ``balance:X-bn`` the lock was taken HERE, at the one
    per-(account, scenario) chokepoint every door funnels through; ruling
    R-CC115 deleted the acquisition when the lock moved ahead of every
    read.*  See :mod:`app.services.user_write_lock` for the reproduction and
    for why the lock is per USER rather than per account.

    Args:
        account_id: The non-loan account whose corrections to reconcile.
        scenario_id: The budget scenario to reconcile within.
    """
    account = _load_non_amortizing_account(account_id)
    if account is None:
        return
    reconcile_account_anchor_corrections(
        account, scenario_id, walk_account_ledger(account_id, scenario_id),
    )


def _scenarios_with_account_postings(account_id: int) -> set[int]:
    """Return the scenarios holding an entry that posts on one account's rows.

    The distinct ``scenario_id`` set over the journal entries with a leg on
    ANY of the account's own chart rows
    (:func:`app.services._posting_reconcile.account_chart_row_ids`) -- source
    entries AND already-posted corrections alike, so an account-global change
    re-bases (or reverses) corrections in every scenario the account's ledger
    is live in, and a stale correction in a scenario whose sources have all
    reverted is still revisited.

    **The account's ROWS, not its linked row**, for the reason plan step X-f3d
    widened the posted-leg reader: since ruling R-FO a correction's delta can
    move only the COUNTER side, and such an entry carries no linked leg at all.
    That cannot strand a scenario today -- a counter-only delta implies an
    original correction with a linked leg in the same scenario -- but the
    premise is one this package no longer wants to depend on, and widening it
    can only ADD a scenario, where the sync is idempotent.

    Args:
        account_id: The real account whose live scenarios to find.

    Returns:
        The distinct scenario ids (unordered; the caller sorts).
    """
    rows = (
        db.session.query(JournalEntry.scenario_id)
        .join(Posting, Posting.journal_entry_id == JournalEntry.id)
        .filter(Posting.ledger_account_id.in_(
            account_chart_row_ids(account_id),
        ))
        .distinct()
        .all()
    )
    return {row[0] for row in rows}


def sync_account_anchor_postings_all_scenarios(account_id: int) -> None:
    """Reconcile a non-loan account's anchor corrections across EVERY scenario.

    The account-GLOBAL chokepoint entry point (account create, anchor
    true-up, the direct anchor edit): anchor history lives on the account,
    not a scenario, so such a change re-bases the corrections in every
    scenario the account's ledger is live in.  Loops
    :func:`sync_account_anchor_postings` over the union of:

    * every scenario holding an entry that posts on any of the account's own
      chart rows (:func:`_scenarios_with_account_postings`), and
    * the owner's BASELINE scenario -- so a fresh account with no posted
      activity still gets its opening posted.  Corrections are per-scenario
      (postings are scenario-scoped); today only the baseline exists, so
      this is one entry in practice, but it is forward-compatible with
      scenario clone.

    A baseline-less owner with no posted activity syncs nothing; the skip is
    logged loudly because production users get a baseline at registration --
    only test fixtures (and the pre-``create_baseline`` recovery state) lack
    one, and a silently stranded opening would surface as a trial-balance
    gap much later.  A missing account or an amortizing loan is a quiet
    no-op (see :func:`_load_non_amortizing_account`).  Flushes but does not
    commit (the caller owns the transaction).

    **Runs under the owner's write lock wherever
    :func:`sync_account_anchor_postings` does, whose docstring says who
    takes it** (plan step ``balance:X-bn``; registration, whose user is not
    committed yet, takes none).  That matters here beyond the per-scenario
    sync it loops: the SCENARIO SET below is itself a read this function
    then acts on, and a scenario that became live between that read and the
    loop would otherwise be missed.  *From plan step X-f1c3c until
    ``balance:X-bn`` this function and the one it loops each took the lock
    themselves.*

    Args:
        account_id: The non-loan account whose corrections to reconcile
            across every scenario its ledger is live in.
    """
    account = _load_non_amortizing_account(account_id)
    if account is None:
        return
    scenario_ids = _scenarios_with_account_postings(account_id)
    baseline = get_baseline_scenario(account.user_id)
    if baseline is not None:
        scenario_ids.add(baseline.id)
    elif not scenario_ids:
        logger.warning(
            "Account %d (user %d) has no baseline scenario and no posted "
            "ledger activity; skipping its anchor-correction sync (the "
            "opening will post when a baseline exists -- see "
            "resync_user_account_anchor_postings).",
            account_id, account.user_id,
        )
        return
    for scenario_id in sorted(scenario_ids):
        sync_account_anchor_postings(account_id, scenario_id)


def self_heal_anchor_corrections(
    account_ids: Iterable[int],
    scenario_id: int,
    delta_entries: list[JournalEntry],
) -> None:
    """Resync the accounts whose anchor corrections *delta_entries* may have staled.

    The effect-time self-heal (Build-Order Step 5, C6): the tails of
    ``posting_service.sync_transfer_postings`` /
    ``sync_transaction_postings`` call this after emitting source delta
    entries, and ``posting_service.re_check_held_anchor_corrections`` calls it
    ONCE per scenario after a batch: ``resync_all_cash_postings`` after
    re-booking every source (ruling **R-BAL103**), and the one removal act
    after deleting the movements it reversed (ruling **plan#2564**).

    **What it decides is whether the reconcile can be SKIPPED, and that is
    the honest way round.**  :func:`sync_account_anchor_postings` is
    idempotent and reconciles to target, so running it after every emission
    is always correct and only ever costs a walk; everything below is the
    proof that a particular walk would write nothing.  Stating it as a skip
    rather than as a fire is not a style choice -- a fire-predicate that
    misses a case silently leaves the ledger wrong, which is exactly what
    happened while this function tested only the first of the two conditions
    below (see the second one).

    A walk writes nothing when BOTH hold:

    1. **The change rides on top of every assertion.**  A source change
       attributed at-or-before an account's latest anchor assertion moves
       that anchor's walked ``ledger_before``, so its posted correction is
       stale until re-derived; a change attributed after every assertion
       adds to the ledger without moving any correction.  The test asks the
       account's own boundary
       (:meth:`app.services.cash_ledger.ReconciledThrough.covers`) about the
       earliest emitted ``entry_date``.

       **This is the last cash caller of that boundary and it stayed
       deliberately** (plan step X-f3a-1, ruling **R-FL**).  The step moved
       every consumer that asks *which statement cleared this LINE* onto
       :class:`~app.services.cash_ledger.StatementCoverage`; this one asks a
       different question -- *could this emission have moved a posted
       correction* -- of a set of JOURNAL ENTRY dates, and a journal entry is
       not a line that clears.  The plan's own list of five consumers named it,
       and tracing it made the difference visible.

       **It remains SOUND, and the reason is an invariant rather than luck.**
       Every emitted ``entry_date`` is a source's ``settled_on`` (the
       movement's own, for every movement-sourced leg -- a transfer's two
       included since plan step ``balance:X-bi-6-3``), and a
       linked source's clearing statement must close on the day the DATE rule
       picks -- ``StatementCoverage._recorded_anchor_id`` refuses any other,
       and every door that moves a settle day releases the link.  So a source
       cleared by some assertion is always dated on or before the latest one,
       which is exactly what ``covers`` tests: the predicate cannot skip a
       change that moves a correction.  Were the link ever freed from the day
       (which the cutover, X-f3c, is what does), this arm would have to ask the
       coverage instead.

       A settle-side entry is
       dated at the source's CURRENT
       attribution civil date, and a reversal entry inherits the latest date
       it reverses (the R2 rule) -- the OLD attribution's civil date -- so
       both sides of every lifecycle delta are covered, including the revert
       of an early-settled future-period source whose CURRENT attribution
       (its period start) sits after the anchor while the reversed effect
       preceded it.  Testing ``<=`` rather than ``<`` over-fires for a
       same-day change, where the resync is an idempotent no-op walk -- the
       safe direction, since this is a SKIP predicate.
    2. **The corrections are already POSTED in this scenario.**  "Riding on
       top" says a posted correction does not MOVE; it says nothing about
       one that was never written.  A scenario becomes live for an account
       the moment an entry first lands on its linked ledger there, and the
       account-global sync
       (:func:`sync_account_anchor_postings_all_scenarios`) only visits
       scenarios that were ALREADY live -- so without this arm the very
       emission that makes a scenario live is the one that skips minting its
       opening, and the account's ledger in that scenario reads its activity
       alone.  Worked: a $1,000.00 account opened in January, a fresh
       scenario, one $70.00 expense settled in March -> that scenario's
       linked ledger summed to ``-$70.00`` instead of ``$930.00``, and the
       trial balance closed only because the missing opening and its equity
       twin were both absent.  Latent rather than live today: production
       creates baseline scenarios ONLY (``auth_service`` at registration,
       ``baseline_service`` at recovery), and the baseline always has its
       corrections from account-create time -- so this cannot fire until a
       scenario-clone / what-if feature ships, which is precisely when it
       would have shipped wrong.

    The two are ordered cheapest-first and short-circuit: the common settle
    (dated after the anchor, in a scenario that already carries its
    corrections) pays one indexed EXISTS and no walk.

    An account with no anchor history never fires; an amortizing loan
    passes the checks (loans carry anchor history rows too) and is then
    structurally skipped by :func:`sync_account_anchor_postings`.
    Flushes but does not commit (the caller owns the transaction).

    Args:
        account_ids: The real accounts the emitted deltas' LINKED legs can
            touch (the transfer's two endpoints / the transaction's cash
            account -- both immutable on their source rows).
        scenario_id: The scenario the deltas were emitted in (postings are
            scenario-scoped, so only that scenario's corrections can have
            moved).
        delta_entries: The just-emitted delta
            :class:`~app.models.journal_entry.JournalEntry` list (empty ->
            no-op).
    """
    if not delta_entries:
        return
    # **The owner's write lock covers the SKIP DECISION, not just the
    # reconcile it guards** (plan step X-f1c3c, finding N-193).  Nothing here
    # takes it since plan step ``balance:X-bn`` (it used to be taken on the
    # line below this comment); every caller already holds it: a signed-in
    # request's command transaction takes it before its view reads anything
    # of the owner's (:mod:`app.db_transaction`), and the deploy reconciles
    # take every owner's at their start.  Both predicates below are READS --
    # the account's coverage boundary and an EXISTS over its posted corrections
    # -- and their answer decides whether the locked reconcile is entered AT
    # ALL.  A skip taken against a stale read is permanent: nothing re-derives
    # it later.  Worked, with the lock one level down instead of here: a $70.00
    # settle dated after the account's latest assertion correctly skips, while a
    # concurrent true-up walks a ledger that cannot yet see that $70.00 and
    # posts its correction $70.00 short -- both commit, and the linked ledger
    # sits $70.00 under its own resolved assertion forever.  With the lock
    # taken before either request reads anything of the owner's, the loser
    # blocks before its first read, then reads a boundary that already covers
    # its own entry, and fires.
    earliest = min(entry.entry_date for entry in delta_entries)
    for account_id in sorted(set(account_ids)):
        # ONE statement of "the account's coverage boundary", asked through the
        # rule's ONE implementation rather than re-spelled as a ``<=`` here.
        # This module had its own copy of both; a second copy of this question
        # is what carried a silent timezone-sign dependency until finding
        # N-133 / F4, and it is the site a lint-based fence could never have
        # seen, because both of its operands were bare locals.
        #
        # **It shared that boundary with the entry reservation and the panel
        # until plan step X-f3a-1**, which moved both onto the recorded clearing
        # fact.  This is the last cash caller, and the docstring above says why
        # it stayed and what would make it have to move.
        boundary = reconciled_through(account_id)
        if boundary.observed_day is None:
            continue
        if boundary.covers(earliest) or not _has_posted_anchor_correction(
            account_id, scenario_id,
        ):
            sync_account_anchor_postings(account_id, scenario_id)


def _has_posted_anchor_correction(account_id: int, scenario_id: int) -> bool:
    """Return whether one account carries a posted anchor correction in a scenario.

    The second half of :func:`self_heal_anchor_corrections`' skip
    precondition: an EXISTS over the account-correction journal entries
    (``account_opening`` / ``account_trueup``) touching ANY of the account's
    own chart rows in *scenario_id* -- the same scoping
    :func:`app.services._posting_reconcile.posted_correction_legs` uses to read
    the amounts, through the same helper, asked here as a cheaper existence
    question because the caller only needs to know whether the scenario has
    been opened at all.  Sharing the scope is what keeps a SKIP decision from
    resting on a narrower view of the ledger than the reconcile it guards.

    **A ``$0`` opening legitimately posts NOTHING**, so this reads ``False``
    for such an account forever and its settles each pay a walk that writes
    nothing.  That is the correct trade: the alternative is a predicate that
    distinguishes "no correction because none is due" from "no correction
    because the scenario is new", and the only thing that knows the
    difference is the walk itself.

    Args:
        account_id: The non-loan account to test.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``True`` when at least one anchor correction is posted for the
        account in that scenario; ``False`` when the account has no chart rows
        at all (nothing can be posted yet) or none is posted.
    """
    correction_sources = [
        ref_cache.posting_source_id(source)
        for source in (
            PostingSourceEnum.ACCOUNT_OPENING,
            PostingSourceEnum.ACCOUNT_TRUEUP,
        )
    ]
    entry_ids = (
        db.session.query(Posting.journal_entry_id)
        .filter(Posting.ledger_account_id.in_(
            account_chart_row_ids(account_id),
        ))
    )
    return db.session.query(
        db.session.query(JournalEntry.id)
        .filter(
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.source_kind_id.in_(correction_sources),
            JournalEntry.id.in_(entry_ids),
        )
        .exists()
    ).scalar()


def _non_loan_accounts_id_query():
    """Return the base query for non-loan account ids, ascending.

    The one query definition the all-owners sweep and the per-user resync
    both build on: every account whose type is not amortizing
    (``has_amortization IS FALSE`` -- exactly the column ``classify_account``
    maps to ``AMORTIZING``, so this SQL filter and the per-account guard can
    never disagree).  Deliberately NOT filtered by ``is_active``: an archived
    account's posted corrections must keep reconciling (archiving disables new
    activity, it does not erase posted facts).  The two enumerators differ only
    in whether they add a ``user_id`` scope, so keeping the join / filter /
    order in one builder is what keeps them from drifting.

    Returns:
        A SQLAlchemy ``Query`` yielding ``(account_id,)`` rows for every
        non-loan account, ascending by id -- NOT executed; callers add a
        ``user_id`` filter (or none) and call ``.all()``.
    """
    return (
        db.session.query(Account.id)
        .join(AccountType, Account.account_type_id == AccountType.id)
        .filter(AccountType.has_amortization.is_(False))
        .order_by(Account.id)
    )


def _all_non_loan_account_ids() -> list[int]:
    """Return every non-loan account id, ascending (all owners).

    The all-owners enumerator behind the deploy-wide backfill
    (:func:`backfill_all_account_anchor_postings`), the cash analogue of
    ``loan_loaders.load_all_loan_account_ids``.  Deliberately NOT user-scoped:
    it is a system / deploy-time sweep over every owner's non-loan accounts
    (like the Step-2 / Step-3 settled-row backfills), and each posted
    correction still carries its own owner (the account's history row and
    scenario), so no row is mis-attributed.

    Returns:
        The non-loan account ids, ascending (already distinct -- ``Account.id``
        is the primary key); empty on an account-free database.
    """
    return [account_id for (account_id,) in _non_loan_accounts_id_query().all()]


def _non_loan_account_ids_for_user(user_id: int) -> list[int]:
    """Return every non-loan account id one user owns, ascending.

    The per-user counterpart to :func:`_all_non_loan_account_ids`, scoping the
    shared base query (:func:`_non_loan_accounts_id_query`) to *user_id*.  The
    loan mirror is ``loan_loaders.load_loan_account_ids_for_user``.

    Args:
        user_id: The owning user whose non-loan accounts to enumerate.

    Returns:
        The account ids, ascending.
    """
    return [
        account_id
        for (account_id,) in _non_loan_accounts_id_query()
        .filter(Account.user_id == user_id)
        .all()
    ]


def _reconcile_account_ids(account_ids: list[int]) -> list[int]:
    """Reconcile the given non-loan accounts across all their scenarios; return them.

    The shared body of the deploy-wide backfill
    (:func:`backfill_all_account_anchor_postings`) and the per-user resync
    (:func:`resync_user_account_anchor_postings`), mirroring
    ``loan_posting_service._reconcile_loan_account_ids``: each id is reconciled
    through :func:`sync_account_anchor_postings_all_scenarios`, the identical
    go-forward sync (so a reconciled correction is identical to a go-forward one
    by construction -- there is no second implementation that could drift).  The
    two public functions differ ONLY in their enumerator (all accounts vs one
    user's), which stays with each of them; this holds the loop itself in one
    place.

    Flushes but does NOT commit -- the caller owns the transaction boundary.

    Args:
        account_ids: The non-loan account ids to reconcile.

    Returns:
        ``account_ids`` unchanged, for the callers' return contract.
    """
    for account_id in account_ids:
        sync_account_anchor_postings_all_scenarios(account_id)
    return account_ids


def backfill_all_account_anchor_postings() -> list[int]:
    """Reconcile every non-loan account's anchor corrections (deploy backfill).

    The one-time, production-wide historical backfill (Build-Order Step 5, C7):
    for every non-loan account across all owners
    (:func:`_all_non_loan_account_ids`), reconcile its opening / true-up anchor
    corrections via :func:`sync_account_anchor_postings_all_scenarios`.  This
    posts the corrections for any account / anchor asserted BEFORE the C6
    go-forward wiring shipped (which therefore carries none), so every non-loan
    linked ledger sums to an ABSOLUTE balance on real historical data and the
    trial balance closes app-wide.

    Reuses the SAME per-account sync the go-forward chokepoints call
    (:func:`_reconcile_account_ids`), so a backfilled correction is identical
    to the go-forward one by construction -- there is no second implementation
    that could drift.  Idempotent and self-healing via reconcile-to-target: an
    account already carrying its go-forward corrections is already at target, so
    nothing is re-posted -- the backfill never double-posts, and a re-run at the
    same state writes nothing.  A $0-anchor account books nothing (no entry, no
    counter row), staying hard-deletable.

    Flushes but does NOT commit -- the caller owns the transaction boundary: the
    deploy (``scripts.init_database.initialise_database``, whose ONE commit
    covers the migrations and all three deploy hooks, and which initialises
    ``ref_cache`` first because the migration host does not; plan step
    ``balance:X-cv``), the backfill suite, or the reconciliation oracle.

    **The one transaction in the app that reconciles more than one OWNER**, so
    it is also the only one that takes more than one per-user write lock -- and
    it takes them all up front, ascending by user id
    (:func:`app.services.user_write_lock.lock_every_user_writes`), before the
    first account is visited.  The loop below walks accounts ascending by
    ACCOUNT id, which visits owners in no particular order, so two concurrent
    sweeps could otherwise take the same two keys in opposite orders and
    deadlock.  Pre-taking them makes the acquisition order a property of this
    function rather than of the account table's contents.

    Returns:
        The non-loan account ids reconciled, ascending -- for the deploy log and
        test introspection (empty on an account-free database).
    """
    lock_every_user_writes()
    return _reconcile_account_ids(_all_non_loan_account_ids())


def resync_user_account_anchor_postings(user_id: int) -> list[int]:
    """Reconcile every non-loan account a SINGLE user owns, across scenarios.

    The per-user chokepoint, mirroring
    ``loan_posting_service.resync_user_loan_postings``: iterates the user's
    non-loan accounts (:func:`_non_loan_account_ids_for_user`) and reconciles
    each through :func:`_reconcile_account_ids` -- the identical go-forward
    sync the deploy backfill uses, so a re-synced correction is identical to a
    go-forward one by construction.

    Three callers need it: ``pay_period_admin.reset_pay_periods`` (the wipe
    CASCADE-disposed the user's correction entries with their periods, so this
    re-derives the corrections onto the rebuilt schedule) and
    ``pay_period_admin.remove_earlier_pay_periods`` (plan step
    ``pay_calendar:C21``, ruling **R-PC114**: its delete takes the corrections
    the ledger had filed in the removed paychecks), both through
    ``pay_period_admin._refile_ledger``; and ``routes.grid.create_baseline``
    (the recovery path for baseline-less users, so openings skipped at
    account-create time are not silently stranded).  Scoped to one user
    because all three are single-user operations.

    **The reset half no longer fabricates anything to re-derive FROM**, and
    that is ruling R-EO (plan step X-f1c3c).  It used to run a
    ``_reanchor_accounts`` pass that staged one fresh history row per account,
    because an assertion's ``pay_period_id`` was an ``ON DELETE CASCADE`` FK
    and the wipe took the real assertions with the periods -- measured on
    production, a reset destroyed 69 balance observations and wrote 9
    fabricated replacements.  The assertion carries no period now, so the wipe
    cannot reach it, and this function re-derives the corrections from the
    observations that were always there.

    Idempotent and self-healing via reconcile-to-target.  Flushes but does
    NOT commit -- the caller owns the transaction boundary.

    Args:
        user_id: The owning user whose non-loan accounts to reconcile.

    Returns:
        The non-loan account ids reconciled, ascending (empty when the user
        has none).
    """
    return _reconcile_account_ids(_non_loan_account_ids_for_user(user_id))
