"""
Shekel Budget App -- A leg's RECORD: the one join to its side's payment.

The half of :mod:`app.services.transfer_legs` that reaches a leg's RECORD --
the payment a transfer side holds, filed under the TRANSFER by one of the
entry's two side links since plan step ``balance:X-bi-6-4d-2`` (ruling
**R-BAL88**).  :func:`_side_records` is the ONE join's root,
:func:`_movement_link` its correlated link, :func:`_links_any` the same link
over transfer ids, and :func:`_leg_transfer_id` and :func:`_leg_is_income` say
which transfer and which side.  Every loader in this package that asks the
join lives here -- the plan half's :func:`planned_transfer_legs` and the
reconcile panel's :func:`offerable_transfer_legs` (both through
:func:`dated_leg_exists_clause`), the settled half's
:func:`transfer_movement_rows` / :func:`recorded_transfer_legs`, the posting
writer's :func:`transfer_family_movements`, the grid's
:func:`covering_movements_by_leg` / :func:`grid_transfer_leg` /
:func:`grid_transfer_legs`, the transfer service's :func:`transfer_side_leg`,
the recurrence engine's :func:`transfers_holding_records`, and every door's
"this transfer holds a payment" (:func:`transfer_holds_a_movement` /
:func:`held_transfer_entries`) -- beside :func:`movement_parent`, the join's
Python twin over one loaded movement, and :func:`parent_entries`, the list
that movement's parent loaded it into.  Plan step ``balance:X-bi-6-4d-2``
moved the join off the shadows HERE, once, for every reader built on it.

**"The module docstring" in the definitions below means the PACKAGE's**
(:mod:`app.services.transfer_legs`): they were written when this was one
module.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, or_
from sqlalchemy.orm import Query, joinedload
from sqlalchemy.sql.expression import ColumnElement, Exists

from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services.transfer_legs._leg import (
    PlanItem,
    TransferLeg,
    _leg_on_side,
    leg_of,
)
from app.utils.balance_predicates import is_projected_clause


def planned_transfer_legs(
    account_id: int, scenario_id: int, *, options: tuple,
) -> list[TransferLeg]:
    """Return every still-planned leg of a projected transfer *account_id* is on.

    The plan-half loader behind the cash fold's plan
    (:func:`app.services.cash_ledger.planned_cash_rows`) and the loan
    partition's projected half
    (:func:`app.services.loan_loaders.projected_income_legs`): one statement
    against ``budget.transfers``, scoped by account and scenario, narrowed to
    live still-Projected parents through the SAME shared builder the shadow
    loaders narrow with (:func:`~app.utils.balance_predicates.is_projected_clause`),
    so the plan half and the record half cannot disagree about which status is
    "still planned".

    **It takes no period window, for the reason the cash plan loader does not**
    (``cash_ledger._facts.planned_cash_rows``): a fold over a windowed plan is a
    fold over a different account, and an argument a caller can get wrong is a
    defect rather than a contract.

    **The caller states the loads that cost a round trip** (the rule plan step
    ``balance:X-bl-2a`` gave the shadow loaders): a reader that prices the legs
    passes :func:`~app.utils.amount_relationships.transfer_pricing_load_options`,
    one that reads dates alone passes ``()``.  Required rather than defaulted,
    so a new caller decides instead of inheriting a guess.

    Args:
        account_id: The account whose legs to load -- either side.
        scenario_id: The budget scenario the transfers live in.
        options: The loader options for every relationship the caller will
            traverse on the parents, rooted at
            :class:`~app.models.transfer.Transfer`.

    Returns:
        One :class:`TransferLeg` per matching transfer whose leg on this
        account is not yet a dated movement, unordered and with no
        :attr:`~TransferLeg.record`; ``[]`` for an account no still-projected
        transfer touches.
    """
    return _still_planned_legs(
        account_id, options, Transfer.scenario_id == scenario_id,
    )


def offerable_transfer_legs(
    account_id: int,
    owner_id: int,
    period_ids,
    *,
    options: tuple,
    transfer_ids=None,
) -> list[TransferLeg]:
    """Return the still-planned legs on *account_id* a statement could settle.

    The reconcile panel's loader (leaf ``X-bi-6-4c-2``), and statement
    match's LEG arm's since leaf ``X-bi-6-4c-1``: the owner's live,
    still-Projected transfers on this account, either side, filed in one of
    *period_ids*, each as the :class:`TransferLeg` on this account -- emitted
    exactly while that side's own DATED movement does not exist, which is
    ruling **R-BAL79**'s rule and the same test :func:`planned_transfer_legs`
    applies (the two share :func:`_still_planned_legs`).  It is a SECOND
    loader rather than that one with an argument, because its contract is a
    different one: an offer screen's WINDOW is its contract (the periods that
    had started by the statement's day), where a fold over a windowed plan is
    a fold over a different account; and it is scoped by OWNER and not by
    SCENARIO, because the panel deliberately takes no scenario
    (``reconcile_service._rows.outstanding_scope`` carries the deferral).

    The PARENT decides (ruling **R-JM**, a transfer leg reads its parent): a
    parent that is not Projected is not emitted, and a Projected parent's side
    is emitted while that side's own DATED record does not exist.  Since plan
    step ``balance:X-bi-6-4d-2`` the leg is priced and ticked off the parent
    and the side's record alone (``transfer_service.leg_settle_amount``,
    ``record_leg_clearing``), so no shadow's status or soft-delete reaches the
    offer.  ``tests/test_services/test_reconcile_transfer_legs.py`` pins the
    parent-side direction and the dated-side rule here.

    Args:
        account_id: The account the statement is for -- either side.
        owner_id: The owner whose transfers may be offered.
        period_ids: The pay-period ids an offer may be filed in; an empty set
            admits nothing.
        options: The loader options for every relationship the caller will
            traverse on the parents, rooted at
            :class:`~app.models.transfer.Transfer` -- required for
            :func:`planned_transfer_legs`' reason.
        transfer_ids: The writer's narrowing -- the transfer ids a form
            posted.  ``None`` (the reader) means every transfer in scope; an id
            outside the scope simply does not come back.

    Returns:
        One :class:`TransferLeg` per matching transfer, unordered and with no
        :attr:`~TransferLeg.record`.
    """
    filters = [
        Transfer.user_id == owner_id,
        Transfer.pay_period_id.in_(period_ids),
    ]
    if transfer_ids is not None:
        filters.append(Transfer.id.in_(transfer_ids))
    return _still_planned_legs(account_id, options, *filters)


def _still_planned_legs(
    account_id: int, options: tuple, *filters,
) -> list[TransferLeg]:
    """Return the legs on *account_id* of live, still-Projected transfers, per side.

    The ONE statement of "this transfer's leg on this account is still
    planned" for :func:`planned_transfer_legs` and
    :func:`offerable_transfer_legs`: the parent is live and Projected (the
    shared :func:`~app.utils.balance_predicates.is_projected_clause`), the
    account is one of its endpoints, and that side's dated covering movement
    does not exist (ruling **R-BAL79**).  Each caller adds its own scope.

    Args:
        account_id: The account whose legs to load -- either side.
        options: The caller's loader options, rooted at ``Transfer``.
        *filters: The caller's further clauses over ``Transfer``.

    Returns:
        The legs, unordered, with no :attr:`~TransferLeg.record`.
    """
    # The leg's RECORD, when it exists: a dated covering movement on this
    # account (ruling **R-BAL79**).  A correlated EXISTS rather than a join,
    # so a transfer is one row here whatever its sides hold.
    dated_leg = dated_leg_exists_clause(
        TransactionEntry.account_id == account_id,
    )
    transfers = (
        db.session.query(Transfer)
        .options(*options)
        .filter(
            *filters,
            Transfer.is_deleted.is_(False),
            is_projected_clause(Transfer),
            or_(
                Transfer.from_account_id == account_id,
                Transfer.to_account_id == account_id,
            ),
            ~dated_leg,
        )
        .all()
    )
    return [leg_of(transfer, account_id) for transfer in transfers]


def _side_records() -> Query:
    """Return the query of every transfer side's RECORD: the ONE join's root.

    **A transfer's payment record hangs off the TRANSFER** (plan step
    ``balance:X-bi-6-4d-2``, ruling **R-BAL88**): a movement filed under one
    side names it by :func:`_movement_link`'s two side keys, and a movement
    filed under a plan row names that row and no transfer
    (``ck_transaction_entries_one_parent``).  Every reader of a leg's record
    narrows this -- the plan half's, the loan settled half's and the
    resync's :func:`dated_leg_exists_clause`, the grid's
    :func:`covering_movements_by_leg`, every settled-half reader through
    :func:`transfer_movement_rows`, the posting writer through
    :func:`transfer_family_movements`, and every door's "this transfer holds a
    payment" through :func:`transfer_holds_a_movement` /
    :func:`held_transfer_entries` -- so the join is stated once, here.

    **There is no record test and no covering test, because the schema
    answers both.**  Through ``X-bi-6-4c`` a movement hung off a shadow row,
    and three questions had to be asked of the join: is it covering (a
    shadow could in principle hold a purchase), is the shadow live (a shadow
    soft-deleted around the service left its movement no leg's), and which
    transfer.  A side link is a record by
    ``ck_transaction_entries_side_link_is_a_record`` (so ``_is_covering``,
    finding **BAL-551**'s second spelling of ``status_seam.covering_clause``,
    is deleted), and a linked record under a hidden transfer is unstorable by
    the deleted-row rule's transfer arm (``app/deleted_row_infrastructure``:
    one cannot arrive under a hidden transfer, and a transfer holding one
    cannot be hidden), so ``_leg_is_record`` is deleted too.  Each reader
    narrows by :func:`_movement_link` (rooted at ``Transfer``) or by a
    transfer id (:func:`_links_any`).

    Returns:
        A query rooted at :class:`~app.models.transaction_entry.TransactionEntry`
        -- built, not executed, and not yet narrowed to any transfer.
    """
    return db.session.query(TransactionEntry)


def _movement_link():
    """Return the SQL truth of "this movement is a side record of this transfer".

    The join's ON clause, correlated to ``Transfer``, stated once so no reader
    can name a different link: the from-side's key or the to-side's
    (``fk_transaction_entries_expense_side`` / ``..._income_side``, ruling
    **R-BAL88**).  An ``OR`` over the two columns rather than a
    ``coalesce``, so each arm reaches its side's index.
    """
    return or_(
        TransactionEntry.expense_transfer_id == Transfer.id,
        TransactionEntry.income_transfer_id == Transfer.id,
    )


def _links_any(transfer_ids) -> ColumnElement:
    """Return the SQL truth of "this movement is a side record of one of *transfer_ids*".

    :func:`_movement_link` for a reader holding transfer IDS rather than a
    correlated ``Transfer``, in the same per-side shape.
    """
    ids = list(transfer_ids)
    return or_(
        TransactionEntry.expense_transfer_id.in_(ids),
        TransactionEntry.income_transfer_id.in_(ids),
    )


def _leg_transfer_id():
    """Return the column naming a side record's TRANSFER.

    Whichever of the movement's two side links is set
    (``ck_transaction_entries_one_parent`` sets at most one), ``NULL`` for a
    movement filed under a plan row.
    """
    return func.coalesce(
        TransactionEntry.expense_transfer_id,
        TransactionEntry.income_transfer_id,
    )


def _leg_is_income():
    """Return the SQL truth of "this side record is its transfer's to-side".

    The SIDE is which link column is set (ruling **R-BAL88**:
    ``income_transfer_id`` keyed to the to-account, ``expense_transfer_id`` to
    the from-account), never inferred from the account.
    """
    return TransactionEntry.income_transfer_id.isnot(None)


def _side_of(movement: TransactionEntry) -> "tuple[Transfer, bool] | None":
    """Return ``(transfer, is_income)`` for a loaded side record, else ``None``.

    The Python twin of :func:`_leg_transfer_id` and :func:`_leg_is_income` over
    one loaded movement, for :func:`movement_parent` and :func:`parent_entries`.
    Read off the link COLUMNS, which the status seam sets at construction
    (``status_seam._side``), and the relationship beside each for the row.
    """
    if movement.income_transfer_id is not None:
        return movement.income_transfer, True
    if movement.expense_transfer_id is not None:
        return movement.expense_transfer, False
    return None


def transfer_movement_rows(*filters):
    """Return every transfer leg's covering movement WITH its transfer and side.

    **The settled half's ONE loader** (leaf ``X-bi-6-4a``, ruling
    **R-BAL106**): each row is ``(movement, transfer, is_income)`` --
    the :class:`~app.models.transaction_entry.TransactionEntry`, its parent
    :class:`~app.models.transfer.Transfer` and its side -- over
    :func:`_side_records`' join, narrowed by the caller's *filters*.  A reader
    takes a movement's period, scenario, status, soft-delete and direction
    from the TRANSFER and the SIDE, so the fold, the ledger oracle, the
    savings metric and every later reader of a paid transfer's money moved
    off the shadows in one place when ``X-bi-6-4d-2`` moved the join onto the
    side links.  A LOADER, not
    a producer: it selects and returns, and every figure is the caller's
    (``cash_ledger.movement_cash_leg`` for the fold, the oracle's own sign).

    Args:
        *filters: The caller's clauses over ``TransactionEntry`` and the joined
            ``Transfer`` -- the movement's account and day, the parent's
            scenario, status and soft-delete.

    Returns:
        An unexecuted ``Query`` of ``(TransactionEntry, Transfer, bool)``
        rows, ordered by the movement's id so a caller that emits in load
        order is deterministic.
    """
    return (
        _side_records()
        .join(Transfer, _movement_link())
        .add_entity(Transfer)
        .add_columns(_leg_is_income())
        .filter(*filters)
        .order_by(TransactionEntry.id)
    )


def recorded_transfer_legs(*filters) -> list[TransferLeg]:
    """Return a :class:`TransferLeg` per matching covering movement, record attached.

    :func:`transfer_movement_rows` as legs: the value the grid and the plan
    half already hold a transfer's side as, carrying its movement as
    :attr:`~TransferLeg.record` (leaf ``X-bi-6-4a``).  A leg is its parent,
    so everything :func:`app.services.cash_ledger.movement_cash_leg` asks of a
    movement's parent -- its type, soft-delete and status -- the leg answers
    off the transfer.

    Args:
        *filters: As :func:`transfer_movement_rows`.

    Returns:
        The legs in movement-id order; ``[]`` when none match.
    """
    return [
        _leg_on_side(transfer, is_income=is_income, record=movement)
        for movement, transfer, is_income in transfer_movement_rows(*filters)
    ]


def dated_leg_exists_clause(*filters):
    """Return the SQL form of "this transfer has a leg whose record is DATED".

    A correlated ``EXISTS`` over :func:`_side_records`, rooted at
    ``Transfer``: one of the transfer's sides has a record carrying a
    ``settled_on``.  Its three readers ask one question three ways -- the
    plan half's :func:`_still_planned_legs` (behind
    :func:`planned_transfer_legs` and :func:`offerable_transfer_legs`)
    narrows it to ONE account and negates it (that side's money moved, so
    its leg is no longer planned, ruling **R-BAL79**); the loan settled
    half (``loan_loaders._legs.settled_half_clause``) narrows it to the
    transfer's to-account and ORs it with "not Projected" (the payment into
    the loan has happened, ruling **R-BAL140**); and the posting writer's
    deploy resync asks it bare, ORed with "settled" (the transfer may hold
    a posted leg whatever its status, the totality argument of
    ``posting_service.resync_all_cash_postings``).  Stated once
    since leaf ``X-bi-6-4a``: the resync's copy lived in
    ``_posting_purchases`` and walked the shadow itself, and it read every
    entry under a live shadow where this reads the covering movements the
    writer's family holds (0 of 38 entries under a shadow were anything else
    on the 2026-09-22 17:06 production dump).

    Args:
        *filters: Further clauses over ``TransactionEntry``, e.g. the
            account.

    Returns:
        A SQLAlchemy ``EXISTS`` clause, correlated to ``Transfer``.
    """
    return (
        _side_records()
        .filter(
            _movement_link(),
            TransactionEntry.settled_on.isnot(None),
            *filters,
        )
        .with_entities(TransactionEntry.id)
        .correlate(Transfer)
        .exists()
    )


def transfer_holds_a_movement() -> Exists:
    """Return the SQL truth of "this transfer holds a payment or purchase".

    **The ONE spelling of the question for a TRANSFER** (leaf
    ``X-bi-6-4a-3``, rulings **R-BAL125** and **R-BAL157**): every door
    that must not remove a transfer holding money asks it -- the pay-period
    lock and the reset gate (``pay_period_locks.items_holding_a_movement``),
    "Remove earlier paychecks" through the same items, and the transfer
    archive and the recurring transfer's delete (``archive_helpers``, whose
    aggregate reads :func:`held_transfer_entries`, the same join).  Until
    that leaf each of those five doors walked the shadows itself, and so did
    the account's delete, which asks no such question since plan step
    ``X-bi-6-4d-2`` (``archive_helpers.account_has_history`` counts every
    live transfer from or to the account, holding money or not).  So
    ``X-bi-6-4d-2``, which moved a transfer's movements off the shadows
    onto the transfer, had one module to change, this one -- the join
    (:func:`_side_records`) and its link (:func:`_movement_link`).

    **Any record on either side, dated or kept un-dated**: the scope of the
    side keys it pre-empts, which refuse the transfer's delete for any
    record (``NO ACTION``, ruling **R-CC64**).

    Returns:
        A SQLAlchemy ``EXISTS`` clause, correlated to ``Transfer``.
    """
    return (
        _side_records()
        .filter(_movement_link())
        .with_entities(TransactionEntry.id)
        .correlate(Transfer)
        .exists()
    )


def held_transfer_entries(*filters: ColumnElement) -> Query:
    """Return every entry the transfers matching *filters* hold, each with its transfer.

    :func:`transfer_holds_a_movement` as rows rather than a test: the
    archive aggregate (``archive_helpers.transfers_holding_movements``)
    reads WHICH kind each held entry is and counts the transfers holding
    them, over the same scope -- any record on either side -- and since
    leaf ``X-bi-6-4d-1`` the transfer's delete (``transfer_service._delete``)
    hands the one removal act every record its transfer holds, the scope the
    side keys would refuse the delete for.  Unordered: the aggregate needs no
    order and the delete states its own.

    Args:
        *filters: Clauses over ``Transfer``.

    Returns:
        An unexecuted ``Query`` of
        :class:`~app.models.transaction_entry.TransactionEntry` with its
        transfer joined as :class:`~app.models.transfer.Transfer`.
    """
    return (
        _side_records()
        .join(Transfer, _movement_link())
        .filter(*filters)
    )


def movement_parent(movement: TransactionEntry) -> PlanItem:
    """Return what *movement* records money FOR: its plan row, or its transfer's LEG.

    **The ledger writer's ONE resolution of a loaded movement's parent**
    (leaf ``X-bi-6-4a``, its second half, ruling **R-BAL106**).  Every door
    that books a movement -- the pair door, a row's family reconcile, the
    teardowns and the one removal act -- asks this, and the writer takes
    what it answers as the parent that types the movement's legs: a plan
    row's category and owner, or for a transfer movement the LEG, whose
    period, owner, scenario, contributing gate and side are its TRANSFER's
    (:class:`TransferLeg`), never the shadow row's.  So the ledger and the
    cash fold read a transfer's money off the same parent.

    **Read off the movement's side links** (plan step ``balance:X-bi-6-4d-2``,
    :func:`_side_of`), the Python twin of :func:`_leg_transfer_id` and
    :func:`_leg_is_income`: a side record is its leg's record, always (the
    schema's two rules :func:`_side_records` names), so its leg carries it;
    any other movement is filed under its plan row.
    ``tests/test_services/test_transfer_legs/test_the_settled_half.py`` pins the twin against
    :func:`transfer_movement_rows`.

    Args:
        movement: A ``budget.transaction_entries`` row with its parent
            reachable -- its side's transfer, or its row.

    Returns:
        The movement's plan row, or the :class:`TransferLeg` it is booked
        under.
    """
    side = _side_of(movement)
    if side is None:
        return movement.transaction
    transfer, is_income = side
    return _leg_on_side(transfer, is_income=is_income, record=movement)


def movement_parent_loads() -> tuple:
    """Return the loader options :func:`movement_parent` reads, rooted at ``TransactionEntry``.

    For a reader that resolves MANY loaded movements' parents -- the
    statement register folds every act on an account (leaf
    ``X-bi-6-4c-1``), and the loan posting probe names the transfer of
    every stale movement it finds (``loan_posting_service._sync``, leaf
    ``X-bi-6-4d-1``) -- and must not lazy-load one transfer per member:
    the movement's plan row, or its side's transfer, whose endpoints and
    status ride it (``lazy="joined"``).  Published HERE rather than spelled
    by the reader because which relationships :func:`movement_parent` walks
    is this module's to name.  Chain it under the caller's path with
    ``Load.options``.

    Returns:
        A tuple of loader options.
    """
    return (
        joinedload(TransactionEntry.transaction),
        joinedload(TransactionEntry.expense_transfer),
        joinedload(TransactionEntry.income_transfer),
    )


def transfer_family_movements(
    transfer: Transfer,
) -> list[tuple[TransactionEntry, TransferLeg]]:
    """Return every record *transfer*'s two sides hold, each with its leg.

    The posting writer's pair door walks this
    (``posting_service.sync_transfer_postings`` and its teardown twin): each
    side's record, dated or kept un-dated, paired with the leg
    :func:`movement_parent` books it under.  Since plan step
    ``balance:X-bi-6-4d-2`` a transfer's family IS its sides' records -- a
    record under a hidden transfer is unstorable, so there is no movement
    that is no leg's record left to reverse -- and each one's transfer is
    *transfer*, an identity-map hit, so the walk reads no relationship by a
    query.  Moved here from ``posting_service._transfer_family_movements`` at
    leaf ``X-bi-6-4a``.

    Args:
        transfer: The transfer, loaded.

    Returns:
        ``(movement, leg)`` pairs, ascending by movement id so a run's
        entries are deterministic.
    """
    movements = (
        _side_records()
        .filter(_links_any((transfer.id,)))
        .order_by(TransactionEntry.id)
        .all()
    )
    return [(movement, movement_parent(movement)) for movement in movements]


def parent_entries(movement: TransactionEntry) -> list[TransactionEntry]:
    """Return the loaded list of movements *movement*'s parent holds, it among them.

    The list the one removal act takes a movement out of after deleting it
    (``movement_removal.remove_movements``, its step 3), so a reconcile that
    walks the parent's movements later in the same request -- the settle
    verbs', the entry door's re-derivation -- never meets one that is gone.
    **Asked here since leaf ``X-bi-6-4c-4``** because a transfer's payment
    has no row: since plan step ``balance:X-bi-6-4d-2`` its list is its
    side's collection on the TRANSFER (``Transfer.expense_movements`` /
    ``income_movements``, :func:`_side_of`), and a plan row's movement's is
    the row's ``entries``.  The act's contract is THAT collection, never a
    copy and never a union built over two of them: a list the parent did not
    load would take the movement out of itself and leave it in the parent's,
    which is the stale walk this act exists to prevent
    (``test_cc5_4a3_movement_removal``'s
    ``TestTheRemovedMovementLeavesItsParentsLoadedList``).  Both collections
    have the same shape (no delete cascade, ``passive_deletes="all"``), so
    the act's "deleted AND removed emits the ``DELETE`` alone" holds of a
    side's list as of a row's
    (``test_a_transfer_side_s_payment_hangs_off_the_transfer``).

    Reading it LOADS the collection (``lazy="select"``, which may autoflush),
    and that is the order the act needs: the list is read before the
    movement's ``DELETE`` is staged, so a lazy load cannot land the delete
    first and load a list the movement is no longer in.

    Args:
        movement: A ``budget.transaction_entries`` row with its parent
            reachable -- its side's transfer, or its row.

    Returns:
        The parent's loaded collection itself, not a copy -- the
        same object on every call while it stays loaded (an expire, such as
        a commit's, discards it, and the next read loads a new one).
    """
    side = _side_of(movement)
    if side is None:
        return movement.transaction.entries
    transfer, is_income = side
    return transfer.income_movements if is_income else transfer.expense_movements


def covering_movements_by_leg(
    transfer_ids: Iterable[int],
) -> dict[tuple[int, int], TransactionEntry]:
    """Return ``{(transfer id, account id): the leg's covering movement}``.

    The grid's one load of every leg's record for a window of transfers
    (leaf ``X-bi-6-1``): one statement over :func:`_side_records` narrowed to
    the transfers named, dated or not, so a reverted leg carries the record
    its revert kept (ruling **R-BAL61**) as a settled leg carries its dated
    one.  A record says where and when its side's money moved and holds no
    figure: the settled and retained figures the grid draws are the
    transfer's (:attr:`~app.models.transfer.Transfer.settled_figure`).  At
    most one per key: a side holds at most one record
    (``uq_transaction_entries_one_expense_side_record`` /
    ``..._one_income_side_record``), and a record's account IS its side's
    endpoint (the side keys), so the key is the leg's.

    Args:
        transfer_ids: The parents whose legs are being drawn.  Empty answers
            ``{}`` without a query.

    Returns:
        The map, holding a key only for a leg that has a record.
    """
    ids = list(transfer_ids)
    if not ids:
        return {}
    movements = (
        _side_records()
        .add_columns(_leg_transfer_id())
        .filter(_links_any(ids))
        .all()
    )
    return {
        (transfer_id, movement.account_id): movement
        for movement, transfer_id in movements
    }


def grid_transfer_leg(transfer: Transfer, account_id: int) -> TransferLeg:
    """Return ONE leg for a grid fragment, its record loaded.

    What a transfer door renders back into a leg's cell (leaf
    ``X-bi-6-1``): :func:`grid_transfer_legs` over one transfer and one
    side, stated separately so the fragment renderers name what they load.

    Args:
        transfer: The parent.
        account_id: The account the leg is on.

    Returns:
        The leg, with its covering movement when one exists.

    Raises:
        ValueError: When *account_id* is neither endpoint (from
            :func:`leg_of`).
    """
    return leg_of(
        transfer, account_id,
        record=covering_movements_by_leg([transfer.id]).get(
            (transfer.id, account_id),
        ),
    )


def transfer_side_leg(transfer: Transfer, *, is_income: bool) -> TransferLeg:
    """Return *transfer*'s leg on one SIDE with its record, keyed by the side alone.

    How the transfer service reaches a side's leg and record (leaf
    ``X-bi-6-4d-1``).  The act's expense leg
    (``transfer_service._validation.TransferRows.expense_leg``) is how the
    settle and the update read the retained correction a re-settle honours
    and the figure an echoed Actual box is compared against -- each off the
    leg's TRANSFER, the settled figure's one home since plan step
    ``balance:X-bi-6-4d-2``, never off the record, which holds no figure;
    they read the expense shadow's ``entries`` until that leaf.  The writers
    that need a side's record itself read it here too: both sides for the
    seam pass (``transfer_service._status._side_legs``) and for an endpoint
    move (``transfer_service._endpoints``), and the offered side for the
    statement link (``transfer_service._settle.record_leg_clearing``).

    **Keyed by the SIDE and never by an account**, which is what makes it
    safe inside an act that moves an endpoint: ``_endpoints._apply_endpoint_move``
    assigns the transfer's account RELATIONSHIP, and ``from_account_id`` reads
    the old account until a flush, so a read keyed by that column found the
    record only when some lazy load happened to autoflush first (the leaf's
    adversarial review measured the miss).  The side is its LINK column
    (ruling **R-BAL88**), so this read never names an account.

    Args:
        transfer: The parent.
        is_income: ``True`` for the to-side, ``False`` for the from-side.

    Returns:
        The leg, its record the side's covering movement -- dated, or kept
        un-dated across a revert -- or ``None`` when the side holds none.

    """
    link = (
        TransactionEntry.income_transfer_id if is_income
        else TransactionEntry.expense_transfer_id
    )
    # ``one_or_none`` and not ``first``: a side holds at most one record
    # (``uq_transaction_entries_one_*_side_record``), and a second would be a
    # refusal rather than a pick.
    record = _side_records().filter(link == transfer.id).one_or_none()
    return _leg_on_side(transfer, is_income=is_income, record=record)


def grid_transfer_legs(
    transfers: Iterable[Transfer], shown_accounts,
) -> list[TransferLeg]:
    """Return the legs a grid draws for *transfers*, records attached.

    The grid-window loader (leaf ``X-bi-6-1``, ruling **R-BAL87**): for each
    transfer, one :class:`TransferLeg` per account *shown_accounts* names for
    it, each carrying its covering movement from ONE
    :func:`covering_movements_by_leg` load.  Which accounts are shown is the
    caller's rule -- :func:`app.services.cash_flow_set.leg_accounts_shown`
    for the paycheck grid, where a transfer between two members shows once
    from the balance line's side (ruling **R-CC23**) -- taken as a function
    so this loader states no set of its own.

    Args:
        transfers: The parents in the window, loaded by the caller with the
            relationships its render will read (the pricing chain, the
            endpoints, the category).
        shown_accounts: ``transfer -> the account ids whose leg to draw``.

    Returns:
        The legs, in transfer order then side order; ``[]`` for no transfers.
    """
    transfers = list(transfers)
    records = covering_movements_by_leg(t.id for t in transfers)
    legs: list[TransferLeg] = []
    for transfer in transfers:
        for account_id in shown_accounts(transfer):
            legs.append(leg_of(
                transfer, account_id,
                record=records.get((transfer.id, account_id)),
            ))
    return legs


def transfers_holding_records(transfer_ids: Iterable[int]) -> set[int]:
    """Return which of *transfer_ids* hold a payment record on either side.

    The recurrence engine's question about a transfer's LEGS (leaf
    ``X-bi-6-4c-2``; ``transfer_recurrence._rows_holding_owner_records``
    carries why each arm is the owner's record): on either side, a record --
    dated or kept un-dated across a revert (ruling **R-BAL61**) -- which
    carries the statement link too when one stands.  Asked of every transfer
    in ONE statement, because a regeneration considers a template's whole
    future.

    **The shadow's own statement link is not asked since plan step
    ``balance:X-bi-6-4d-2``**: a statement links the side's RECORD
    (``transfer_service.record_leg_clearing``), and a side holding none -- a
    ``$0.00`` close -- keeps no link (ruling **R-BAL141**).

    Args:
        transfer_ids: The transfers to ask about.  Empty answers ``set()``
            without a query.

    Returns:
        The subset of *transfer_ids* holding a record on either side.
    """
    ids = list(transfer_ids)
    if not ids:
        return set()
    return {
        transfer_id
        for (transfer_id,) in _side_records()
        .with_entities(_leg_transfer_id())
        .filter(_links_any(ids))
        .distinct()
    }
