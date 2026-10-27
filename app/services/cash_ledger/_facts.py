"""
Shekel Budget App -- Cash ledger: the FACTS (what happened to this account).

The stored, user-asserted, or recorded events a cash balance is folded from,
and nothing that folds them:

  * :class:`AnchorPoint` / :func:`resolve_anchor` -- the user's balance
    ASSERTION, read from the dated ``AccountAnchorHistory`` source of truth
    (E-19).  A stored fact, not a computed projection.
  * :func:`planned_cash_rows` -- the account's still-PROJECTED
    balance-contributing rows, unwindowed, plus one derived LEG per
    still-projected transfer the account is on (plan step X-bi-6a, ruling
    R-BAL13): the PLAN, which the seam's cash fold dates and values (ruling
    R-G) because a plan's effective date depends on the reader's as-of and
    this leaf reads no clock.

The loan analog is :mod:`app.services.loan_ledger._events`: both answer "what
happened, and in what order", never "what is the balance at T" -- which is the
:mod:`app.services.balance_at` seam's question.  What one of these rows is
WORTH lives beside this in :mod:`._amounts`; what a SET of them sums to lives
in :mod:`._flows`.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01).  Plain data
in, frozen dataclass out; no ``flask`` / ``request`` / ``session`` /
``current_app`` / ``render_template`` import.  ``log_event`` is from
``app.utils.log_events``, the project's Flask-free structured-logging helper.

Decimal discipline (``docs/coding-standards.md``).  :attr:`AnchorPoint.balance`
is constructed via ``Decimal(str(...))`` from the storage value.
``AccountAnchorHistory.anchor_balance`` is a ``Numeric(12,2)`` column, so the
SQLAlchemy adapter already returns ``Decimal`` -- but routing through ``str`` is
the project convention and is the cheap insurance against a future column-type
change silently coercing through float.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.transaction import Transaction
from app.services.transfer_legs import (
    TransferLeg,
    planned_transfer_legs,
)
from app.utils.amount_relationships import (
    transfer_pricing_load_options,
    valuation_load_options,
)
from app.utils.balance_predicates import (
    balance_contributing_clause,
    is_projected_clause,
    owner_declared_clause,
)

from ._amounts import ReconciledThrough
from ._events import InFlightMovement, in_flight_movements


@dataclass(frozen=True)
class AnchorPoint:
    """Immutable date-anchored anchor (E-19 single source of truth).

    Attributes:
        anchor_id: The ``budget.account_anchor_history`` row's own id -- the
            value a cleared line NAMES (``reconciled_by_id``, ruling **R-FL**,
            plan step X-f3a-1).  The reconcile panel needs the STATEMENT it is
            reconciling against, not merely the day it was true for: two
            assertions can share a day (production carries 3 such days on
            Checking), so a day cannot identify one.  Carried here rather than
            re-queried because this record is already the answer to "which
            assertion governs", and a second query for its id would be a second
            answer to that question with a write in between.
        balance: The real-money anchor balance as a ``Decimal``.  Zero is a
            legitimate value per E-12 and is preserved verbatim; consumers MUST
            NOT treat ``Decimal("0.00")`` as "missing".
        observed_on: The civil day the asserted balance was TRUE -- the
            stored ``AccountAnchorHistory.observed_on`` (ruling R-DH, plan
            step 2).  **This is the "as of" a caption means.**  It was the same
            day as ``created_at``'s until the column became user-supplied; now
            an account opened today and back-dated to 2026-01-01 is a balance
            that was true on Jan 1, and a surface captioning it "anchored Jul
            31" is naming the keystroke rather than the fact.  On a modelled
            account it is also the day the return starts accruing from
            (``balance_at._asset_fold``), so a "growth since" caption that
            reads ``created_at`` contradicts the figure beside it.
        created_at: The anchor event's RECORDING instant, aware-UTC.  For a
            surface that genuinely shows when the row was entered; a reader
            wanting the day the balance was true wants
            :attr:`observed_on`.

    **It carried an ``as_of_date`` until 2026-07-31, and deleting it is
    finding N-133 / F12's other half.**  That field was ``created_at``
    truncated to a UTC day, justified in its own docstring by matching the
    ``uq_anchor_history_account_period_balance_day`` index -- which now keys
    on the stored ``observed_on`` instead, so the justification was gone.  That
    index is itself deleted now (ruling R-EQ, plan step X-f1c4b): the duplicate
    rule is a write-door comparison against the governing assertion.
    No production code read it: the account-detail route deliberately reads
    ``created_at`` and says why (a UTC day renders a late-evening Eastern
    anchor on the wrong day), and its only reader in the repository was one
    test assertion.  A reader that wants the day an assertion was TRUE reads
    ``CashAnchorFact.observed_on``, which is a stored fact rather than a
    seventh derivation of "which day".
    """

    anchor_id: int
    balance: Decimal
    observed_on: date
    created_at: datetime


def _governing_row(
    account_id: int, on_or_before: date | None,
) -> AccountAnchorHistory | None:
    """Return the assertion that GOVERNS, optionally as of a civil day.

    The ONE query behind both :func:`resolve_anchor` (``on_or_before=None`` --
    which assertion governs now) and :func:`governing_anchor_on`
    (``on_or_before=D`` -- which governed on day D).  They are the same question
    with the same tie-breaks and only the horizon differs, so they are one
    implementation: two queries that "agree by reading" is the defect this
    package's own history is made of.

    **Over the OWNER's levels only, until the flip** (plan step
    ``balance:X-bj-1``, ruling **R-JN**).  The level relation holds the bank's
    statement placements too since that step, and what this answers is
    "which assertion RESETS the fold" -- the grid header, the reconcile panel
    and the write door's did-this-change compare all mean that -- so it
    composes :func:`~app.utils.balance_predicates.owner_declared_clause`,
    the one spelling of that narrowing, which ``X-f3c-5`` deletes with the
    reset.

    Args:
        account_id: The account whose assertions to search.
        on_or_before: The civil day to answer as of, or ``None`` for the
            account's latest assertion at any date.

    Returns:
        The governing :class:`AccountAnchorHistory` row, or ``None`` when the
        account has no assertion at or before the horizon.
    """
    query = db.session.query(AccountAnchorHistory).filter(
        AccountAnchorHistory.account_id == account_id,
        owner_declared_clause(),
    )
    if on_or_before is not None:
        query = query.filter(AccountAnchorHistory.observed_on <= on_or_before)
    return query.order_by(
        AccountAnchorHistory.observed_on.desc(),
        AccountAnchorHistory.created_at.desc(),
        AccountAnchorHistory.id.desc(),
    ).first()


def _anchor_point(row: AccountAnchorHistory) -> AnchorPoint:
    """Return the :class:`AnchorPoint` for one assertion row.

    The ONE construction, shared by every resolver here, so a field added to the
    record cannot reach some callers and not others -- which is exactly what
    ``anchor_id`` would have done at plan step X-f3a-1 had the two builders
    stayed hand-written.

    Args:
        row: The governing :class:`~app.models.account.AccountAnchorHistory`.

    Returns:
        Its :class:`AnchorPoint`.
    """
    return AnchorPoint(
        anchor_id=row.id,
        balance=Decimal(str(row.anchor_balance)),
        observed_on=row.observed_on,
        created_at=row.created_at,
    )


def governing_anchor(account_id: int) -> AnchorPoint | None:
    """Return the assertion that governs *account_id* now, or ``None``.

    The NON-RAISING twin of :func:`resolve_anchor`, and the difference is the
    caller rather than the question: a READER cannot proceed without an
    assertion and gets the ``RuntimeError``, while the reconcile panel renders
    an honest empty state for an account whose owner has never declared a
    balance ("there is nothing for an offer to be inside of").  Both go through
    :func:`_governing_row`, so they cannot disagree about which row governs.

    **The panel needs the ROW, not the day** (plan step X-f3a-1, ruling
    **R-FL**): a tick records which STATEMENT showed the money, two assertions
    can share a civil day, and it asked ``reconciled_through(...).observed_day``
    -- a ``MAX`` over the column -- which cannot name one.

    Args:
        account_id: The account whose governing assertion to resolve.

    Returns:
        The governing :class:`AnchorPoint`, or ``None`` for an account with no
        assertion at all -- fixture-only in production (migration
        ``cfb15e782f86`` plus ``account_service.create_account`` guarantee an
        opening row).
    """
    governing = _governing_row(account_id, on_or_before=None)
    return None if governing is None else _anchor_point(governing)


def governing_anchor_on(
    account_id: int, observed_on: date,
) -> AnchorPoint | None:
    """Return the assertion that governed ``account_id`` on ``observed_on``.

    **The WRITE doors' question** (ruling **R-EQ**, plan step X-f1c4b): before
    appending an assertion for a civil day, does it change what is already true
    on that day?  It differs from :func:`resolve_anchor` only when the day being
    asserted is not the newest one the account carries -- a BACK-DATED
    assertion, which is what plan step X-f1c4c makes reachable on the cash door
    and what the loan door has allowed since Commit 16.

    **Asking ``resolve_anchor`` instead is a measured defect, not a shortcut.**
    A submission for an earlier day compared against the LATEST assertion can
    never match, so a double-click on a back-dated correction appends a
    duplicate every time -- reproduced on the loan door by two independent
    reviews of X-f1c4b before this function existed.  A submission for day D can
    only change what is true at or after D, so D is the horizon the comparison
    belongs at.

    Args:
        account_id: The account being asserted about.
        observed_on: The civil day the submission asserts a balance for.

    Returns:
        The governing :class:`AnchorPoint`, or ``None`` when the account has no
        assertion at or before *observed_on* -- in which case the submission is
        necessarily new.  Unlike :func:`resolve_anchor` this does NOT raise on
        an account with no history: "nothing governs this day yet" is an honest
        answer to a writer, where it is a broken invariant to a reader.
    """
    governing = _governing_row(account_id, on_or_before=observed_on)
    if governing is None:
        return None
    return _anchor_point(governing)


def resolve_anchor(account: Account) -> AnchorPoint:
    """Return the canonical :class:`AnchorPoint` for ``account``.

    Reads the most recent ``AccountAnchorHistory`` row for the account as the
    dated source of truth (E-19).  **It is the ONE answer to "what balance has
    this account been asserted to hold", for every consumer** -- the grid
    header, the reconcile panel, the Property's market value, the archived
    drawer's last balance, and the write doors' did-this-change test all ask
    here (plan step X-f1c3a).

    **It used to be the second-best answer**, because ``accounts`` carried a
    denormalized copy of this same row and most readers took that instead.
    This function compared the two and logged ``EVT_ANCHOR_CACHE_RECONCILED``
    when they disagreed, letting the history row win without repairing the copy
    (finding cash D4).  Ruling **R-EH** deleted the columns, so there is no
    second answer to reconcile against and no detector to run: the divergence
    is not detected-and-logged, it is inexpressible.

    The Decimal balance is constructed via ``Decimal(str(...))`` to
    obey the project's "construct Decimal from strings" rule
    (``docs/coding-standards.md``) even though the storage column is
    already ``Numeric(12,2)``.

    Never returns ``None``: Commit 3 (migration ``cfb15e782f86`` plus
    the canonical ``account_service.create_account`` factory)
    guarantees every account row has a matching origination history
    row from the moment it exists, so the latest-row query always
    succeeds.  The defensive ``RuntimeError`` exists so that a future
    regression -- e.g. a code path that bypasses the factory by
    calling ``db.session.add(Account(...))`` directly -- fails loudly
    here rather than silently returning a wrong number to every
    downstream consumer.

    **It takes no ``scenario_id``, and dropping it is part of the same ruling.**
    The parameter never scoped the query -- ``AccountAnchorHistory`` carries no
    scenario column and accounts are not scenario-scoped at the storage tier --
    and its only remaining use was the reconciliation log payload deleted above.
    Keeping it for "API symmetry" with the row loaders beside it would have
    forced a ``BalanceContext`` into the write doors, none of which hold one: an
    argument a caller must fabricate is the shape this plan's Section 8 rules a
    defect rather than a contract.  (The account-edit validator was the other
    caller named here; plan step X-f1e deleted its read entirely.)

    Args:
        account: The :class:`~app.models.account.Account` to resolve.
            Must be attached to ``db.session`` (the history-row query
            reads via the session).

    Returns:
        :class:`AnchorPoint` -- the asserted balance, the day it was true, and
        the recording instant.  **It carried the anchor PERIOD until plan step
        X-f1c3b** (ruling R-EO), which deleted
        ``account_anchor_history.pay_period_id``: an assertion is a fact about
        a bank and is not filed under a budgeting artifact.  No reader in
        ``app/`` had ever taken that field.

    **"Latest" is the latest BUSINESS day, and the tie-breaks are the WALK's,
    key for key.**  The order here is ``(observed_on, created_at, id)``
    descending -- the exact reverse of
    :func:`app.services.cash_ledger.cash_anchor_facts` -- so the row this names
    as current is by construction the row the walk replays LAST.  Both halves
    are load-bearing.  Two assertions can share an instant (a same-second
    true-up, or any fixture that stamps both), and ``created_at DESC`` alone
    returns whichever row the plan happens to yield, so ``id`` breaks it.  And
    since plan step 2 made ``observed_on`` a user-supplied column, the
    RECORDING order and the BUSINESS order can differ outright: a balance
    asserted for an earlier day but recorded later is not the current one, and
    ordering on ``created_at`` first would have named it.  One question answered
    two ways is how the resolver and the fold come to disagree about which
    balance is authoritative.

    Raises:
        RuntimeError: When no ``AccountAnchorHistory`` row exists for
            the account.  Unreachable in production after Commit 3;
            see the function docstring above for the regression-trap
            rationale.
    """
    latest = _governing_row(account.id, on_or_before=None)
    if latest is None:
        raise RuntimeError(
            f"resolve_anchor: account id={account.id} has zero "
            "AccountAnchorHistory rows.  Commit 3 (migration "
            "cfb15e782f86 plus account_service.create_account) makes "
            "this state unreachable; investigate any code path that "
            "constructed the Account row without routing through the "
            "canonical factory."
        )

    return _anchor_point(latest)


def reconciled_through(account_id: int) -> ReconciledThrough:
    """Return the coverage boundary *account_id*'s latest assertion establishes.

    The boundary every "is this already inside the balance the user declared"
    question is asked through
    (:meth:`~app.services.cash_ledger.ReconciledThrough.covers`), for the
    callers that do NOT already hold a walk: the posting self-heal's skip
    predicate, the entry list's reconciled indicator, and the reconcile panel.
    One indexed lookup (``idx_anchor_history_account`` leads on
    ``account_id``), no rows materialised, no anchor resolution.

    **It is the SQL twin of
    :attr:`~app.services.cash_ledger.CashLedgerWalk.reconciled_through`, and
    the two exist for a reason rather than by accident.**  A caller holding the
    walk already has the answer in memory and must not pay a query for it; a
    caller rendering one template row must not walk an account to get it.  They
    are provably equal -- ``MAX`` over the same column against the last element
    of a list the loader orders ``(observed_on, created_at, id)`` ascending --
    and that equality is pinned by a test rather than assumed, because "two
    statements that happen to agree" is the exact shape this arc exists to
    remove.  A THIRD statement is not acceptable: the account posting sync
    grew one (``MAX(created_at)`` as an instant, compared against a civil date
    pushed through midnight UTC) and it carried a silent timezone-sign
    dependency for the whole time it lived (finding N-133 / F4).

    Args:
        account_id: The account whose coverage boundary to resolve.

    Returns:
        The account's :class:`~app.services.cash_ledger.ReconciledThrough`.
        Its ``observed_day`` is ``None`` for an account with no anchor history
        (fixture-only -- migration ``cfb15e782f86`` plus
        ``account_service.create_account`` guarantee production accounts one)
        or a missing account, and such a boundary reconciles nothing, which is
        the honest answer when no balance has ever been declared.
    """
    return ReconciledThrough(
        db.session.query(db.func.max(AccountAnchorHistory.observed_on))
        .filter(
            AccountAnchorHistory.account_id == account_id,
            # The owner's declarations bound clearing, not the bank's
            # placements: the same interim narrowing ``_governing_row``
            # composes, for the same reason (plan step ``balance:X-bj-1``).
            owner_declared_clause(),
        )
        .scalar()
    )


def planned_cash_rows(
    account_id: int, scenario_id: int,
) -> list[Transaction | TransferLeg | InFlightMovement]:
    """Return an account's plan: its projected rows, transfer legs and movements in flight.

    The PLAN half of the cash event stream, and the structural twin of
    :func:`app.services.cash_ledger.settled_cash_facts` beside it: that half
    is every DATED movement on the account, this half is everything the
    projection must still hold -- three kinds since plan step
    ``balance:X-bi-4a``, each a ruling:

    **A movement IN FLIGHT is a plan item** (ruling **R-BAL77**): a purchase
    recorded against a contributing envelope, open or closed, that the bank
    has not been seen to take
    (:func:`~app.services.cash_ledger._events.in_flight_movements`, the
    un-dated half of the ONE movement stream the settled twin reads the dated
    half of).  It is worth its own figure and lands no earlier than the day it
    happened; the Projected envelope it sits under reserves only its UNSPENT
    budget (:func:`~._amounts._entry_checking_impact`), so the two never
    hold the same dollars back twice.  Through ``X-bi-3e`` a Projected
    envelope's reservation carried its un-dated purchases inside one
    ``max`` and a CLOSED envelope's row leg booked them on the close day;
    the identity ``max(a, b) = b + max(a - b, 0)`` is what split the first
    into this item plus the unspent budget, and the ruling is what deleted
    the second.

    **A still-projected transfer's leg is DERIVED from the parent row, not read
    off its shadow** (ruling **R-BAL13**).  The two shadow ``Transaction`` rows
    the transfer service wrote beside each parent were that parent's
    projection onto its two accounts, and until this step this loader was
    kind-blind on ``transfer_id``: it loaded the shadow on this account and
    the valuation walked it to the parent (amount rule 5).  The row half of
    the plan then EXCLUDED shadows, until plan step ``balance:X-bi-6-4d-2``
    deleted them (ruling **R-BAL166**); no transaction row names a transfer
    since.  The leg half is
    :func:`app.services.transfer_legs.planned_transfer_legs` over
    ``budget.transfers``: one :class:`~app.services.transfer_legs.TransferLeg`
    per live still-projected transfer the account is on either side of, worth
    the parent's resolved amount -- **and only for a side whose DATED
    movement does not yet exist** (ruling **R-BAL79**, plan step
    ``balance:X-bi-4a``): the settled twin reads a settled leg as its side's
    covering movement, which hangs off the transfer by a side link since plan
    step ``balance:X-bi-6-4d-2``, so the movement, not the parent's status, is
    what decides which relation a leg is in, and a leg cannot be counted by
    both halves.  Where a shadow had drifted from its parent on period, due
    date, deletion or scenario the plan followed the PARENT, which is ruling
    **R-JA**'s direction; with the shadows gone the parent is the only row.

    * a DATED movement can be dated by this leaf -- its ``settled_on`` is a
      STORED fact and nothing derives it (plan step X-f1, ruling R-EC) -- so
      ``_events`` returns it valued and dated, as a
      :class:`~app.services.cash_ledger.CashSourceFact`;
    * a plan item cannot.  A projected row's effective date is
      ``max(its attribution date, as_of + 1 day)`` and a movement in flight's
      is ``max(the day it happened, as_of + 1 day)`` (ruling R-G: "a plan
      cannot have already happened"), each a function of the READER's as-of
      -- and this package reads no clock, deliberately (a walk that read one
      made the posted ledger a function of when the sync happened to run, the
      corruption shape plan step A3 removed from the loan side).

    So this returns the items THEMSELVES and the seam's cash fold owns the
    dating and the valuation, exactly as the loan plan's PLANNED tier lives in
    ``balance_at._plan`` rather than in ``loan_ledger`` (plan step C6a's ruling,
    restated for cash).  That is also why a row is returned as a plain row and
    not a ``CashPlannedFact``: a fact type here would have to carry either no
    date (a dataclass earning nothing over the row) or a clock-derived one (the
    thing the ruling forbids); a movement in flight carries the day it
    happened, which is a stored fact and not a derivation.

    **It takes no period window, for the same reason its settled twin does not.**
    An argument a caller can get wrong is a defect, not a contract (plan
    Section 8): the loan fold once TOOK the period list its visibility rule
    needed, and the grid passing a WINDOW moved a balance by $150,000.00 (plan
    step B1).  A fold over a windowed plan is a fold over a different account.

    The eligibility gate is the shared
    :func:`~app.utils.balance_predicates.balance_contributing_clause`
    (``is_deleted = FALSE AND status_id NOT IN (Credit, Cancelled)``) composed
    with :func:`~app.utils.balance_predicates.is_projected_clause` -- the SQL form
    of the very ``is_projected`` predicate
    :func:`~app.services.cash_ledger.sum_projected` re-applies when it values
    these rows, so the loader and the reduction cannot disagree about which rows
    are in the plan.  The status pair is redundant by construction (Projected is
    neither Credit nor Cancelled) and composed anyway, so this loader and its
    settled twin state "which rows exist at all" through one shared clause rather
    than two hand-written filters.

    Args:
        account_id: The account whose plan to load.
        scenario_id: The budget scenario the rows live in.

    Returns:
        Every still-Projected contributing row of the account's OWN, with
        ``entries`` populated, followed by one
        :class:`~app.services.transfer_legs.TransferLeg` per live
        still-projected transfer the account is on whose leg here is not yet
        a dated movement, its parent's pricing relationships populated,
        followed by one
        :class:`~app.services.cash_ledger._events.InFlightMovement` per
        un-dated purchase on the account.  Unordered: the fold groups them by
        day.
    """
    rows: list[Transaction | TransferLeg | InFlightMovement] = (
        _unwindowed_contributing_rows(
            account_id, scenario_id, is_projected_clause(Transaction),
        )
    )
    rows.extend(planned_transfer_legs(
        account_id, scenario_id, options=transfer_pricing_load_options(),
    ))
    rows.extend(in_flight_movements(account_id, scenario_id))
    return rows


def _unwindowed_contributing_rows(
    account_id: int, scenario_id: int, narrowing,
) -> list[Transaction]:
    """Return an account's contributing rows in one scenario, narrowed by the caller.

    The unwindowed ROW load behind the plan half of the cash event stream --
    :func:`planned_cash_rows` above (still-Projected, and since plan step
    X-bi-6a no transfer shadow).  **It was the one load behind BOTH halves
    through ``X-bi-3e``**, the settled half narrowing it to the settled
    statuses; since plan step ``balance:X-bi-4a`` the settled half reads no
    row at all -- it is every dated MOVEMENT on the account, loaded with its
    parent in one statement (``_events._movements_of``, ruling **R-BAL80**)
    -- so the four-relationship parent chain this loader's options carry no
    longer costs a fold anything on the settled side (ledger row
    **BAL-501**, closed here).  What partitions the contributing set now is
    the movement's DAY: dated movements are the actual, un-dated ones are in
    flight, and this load is the plan's still-Projected rows beside them.

    Kept where it is rather than folded into its one caller: the account /
    scenario scope, the contributing gate, and ``selectinload(entries)`` are
    individually load-bearing -- a missing ``selectinload(entries)`` is the
    seam that shipped two different balances for one row in CRIT-01 / F-009
    -- and stating them once beside the narrowing parameter keeps "which rows
    exist at all" a single clause rather than a filter written per caller.
    PRIVATE, and out of the W9909 registry for the same reason as before:
    an implementation detail of the loader, not a leaf surface.

    **``joinedload(pay_period)`` was a THIRD option here and pay-calendar plan
    step C4-a-1 deleted it, which is worth a paragraph because of what kept it
    alive.**  It was the fold's -- ``_cash_fold._cash_plan`` read
    ``txn.pay_period`` for its attribution clamp until that step moved the clamp
    onto the owner's derived calendar.  Once that reader left, the load had no
    consumer of its own at all: what it still did was warm SQLAlchemy's identity
    map for a reader in another package, ``grid/_transaction_cell.html``, which
    rendered ``t.pay_period.start_date`` from JINJA for its due-date caption.
    Measured 2026-08-27 on a six-period ``/grid`` render carrying six projected
    rows: 29 statements with the load and 35 without, of which
    ``budget.pay_periods`` reads went 4 to 8 -- so deleting it alone would have
    been a real regression, and keeping it would have written an accidental
    cross-package coupling down as a contract.  Both halves went instead: the
    two surfaces that draw that cell PUBLISH the payday it needs, and the load
    went with the reader.  **The lesson is the shape rather than the site**: an
    eager load whose stated reason has moved on is not dead weight, it is a
    query budget some other reader is silently spending.

    **The status narrowing is a clause PARAMETER, and stays in SQL.**  Loading the
    whole contributing set and partitioning in Python would be one query instead
    of two, and is rejected: the Projected half is roughly two years of forward
    projection, so a post-filter would eager-load entries for the whole horizon to
    keep the ~130 settled rows the walk wants -- and the settled half has a
    consumer (the walk, and at plan step X-d the posting writer) that never wants
    the plan at all.

    Args:
        account_id: The account whose rows to load.
        scenario_id: The budget scenario the rows live in.
        narrowing: The caller's narrowing as a SQLAlchemy boolean expression
            over ``Transaction``, built from the shared builders in
            :mod:`app.utils.balance_predicates`
            (:func:`~app.utils.balance_predicates.is_projected_clause` for
            the plan), never a status literal written at the call site.

    Returns:
        ``list[Transaction]`` -- the matching rows, unordered, with ``entries``
        populated.
    """
    return (
        db.session.query(Transaction)
        # What a CONTRIBUTION pass reads, stated by the valuation rather than
        # copied here (plan step X-au-g-2c-2): the pricing set a still-Projected
        # row is resolved through, plus its entries for the unspent-budget
        # reservation.  It is the valuation's own statement of what a
        # contribution pass MAY read, and narrowing it per caller is the
        # copied-clause shape this loader exists to avoid.
        .options(*valuation_load_options())
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            narrowing,
            balance_contributing_clause(),
        )
        .all()
    )
