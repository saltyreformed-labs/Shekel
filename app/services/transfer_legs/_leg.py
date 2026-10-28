"""
Shekel Budget App -- A transfer LEG: the value, its label and its identity.

The half of :mod:`app.services.transfer_legs` that holds the VALUE: the
:class:`TransferLeg` every reader holds a transfer's side as (reading its
:attr:`~TransferLeg.record` off the transfer's list for that side,
:func:`side_movements`, ruling **plan#1984**), the one composition of a leg's
LABEL (:func:`leg_label`), the one statement of a leg's IDENTITY beside a plan
row's (:func:`cell_key`, :func:`key_order`, :func:`is_leg_key`, ruling
**R-BAL87**), the one answer to which row PRICES an item (:func:`parent_of`,
ruling **plan#1983**), and the one construction of a leg from its parent
(:func:`transfer_side_leg`, :func:`leg_of` from an account, and
:func:`side_legs` for both sides).
The package docstring carries the argument.  The functions these definitions
name by bare name (:func:`planned_transfer_legs`,
:func:`recorded_transfer_legs`, :func:`preload_side_records`,
:func:`grid_transfer_legs`, :func:`movement_parent`, ``_leg_is_income``) live
in :mod:`._records`, beside the join.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.ref import Status
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.utils.dates import days_paid_before_due


#: The two label prefixes :func:`leg_label` composes with.  Published so the
#: one reader that PARSES a label back out (``grid_view_service
#: ._short_display_name``, the grid's row label) strips exactly what was
#: composed rather than a literal of its own -- the coupling
#: ``transfer_service._create.shadow_names`` reported and left for this leaf
#: (that writer went with the twin rows' mirrors at plan step
#: ``balance:X-bi-6-4d-2``'s leaf 3b).
TRANSFER_TO_PREFIX = "Transfer to "
TRANSFER_FROM_PREFIX = "Transfer from "


def leg_label(from_account: Account, to_account: Account) -> tuple[str, str]:
    """Return the ``(expense, income)`` leg labels for two endpoints.

    ONE composition: "Transfer to <to-account>" is what the from-side shows
    (its money leaves for that account), "Transfer from <from-account>" what
    the to-side shows.  :attr:`TransferLeg.name` reads it off the parent's
    endpoints at render time, and nothing stores it:
    ``transfer_service._create.shadow_names`` wrote the same pair onto the two
    shadow rows until plan step ``balance:X-bi-6-4d-2`` deleted them, and its
    leaf 3b deleted that writer.

    Args:
        from_account: The source account, the expense leg's.
        to_account: The destination account, the income leg's.

    Returns:
        ``(expense_label, income_label)``.
    """
    return (
        f"{TRANSFER_TO_PREFIX}{to_account.name}",
        f"{TRANSFER_FROM_PREFIX}{from_account.name}",
    )


def side_movements_key(*, is_income: bool) -> str:
    """Return the name of the transfer's list that holds one side's records.

    ``income_movements`` for the to-side, ``expense_movements`` for the
    from-side (plan step ``balance:X-bi-6-4d-2``, ruling **R-BAL88**): the
    ONE statement of which relationship is a side's.  :func:`side_movements`
    reads the list by it, and :mod:`._records`' loaders fill a list they
    pre-load under it (``set_committed_value``), so no reader names the
    relationship a second way.  Read off the mapped attribute rather than
    spelled as a literal, so a renamed relationship fails at import.

    Args:
        is_income: ``True`` for the to-side, ``False`` for the from-side.

    Returns:
        The relationship's attribute name.
    """
    return (
        Transfer.income_movements if is_income else Transfer.expense_movements
    ).key


def side_movements(
    transfer: Transfer, *, is_income: bool,
) -> list[TransactionEntry]:
    """Return *transfer*'s list of one side's payment records: the ORM collection itself.

    Where a side's record is STORED (plan step ``balance:X-bi-6-4d-2``): a
    transfer holds each side's record in its own list,
    ``Transfer.expense_movements`` / ``income_movements``, at most one each
    (``uq_transaction_entries_one_expense_side_record`` /
    ``..._one_income_side_record``), exactly as a plan row holds its payment
    in ``Transaction.entries``.  Its readers: :attr:`TransferLeg.record`, the
    one removal act's list (``_records.parent_entries``) and the status
    seam's write of a new record (``status_seam._side._cover_side``), so the
    list a record is appended to, taken out of and read from is one list.

    Reading it LOADS the collection when nothing has (``lazy="select"``,
    which may autoflush), as reading a row's ``entries`` does; a loader that
    reads many sides' records pre-loads them first
    (:func:`~._records.preload_side_records`).

    Args:
        transfer: The parent transfer.
        is_income: ``True`` for the to-side, ``False`` for the from-side.

    Returns:
        The loaded collection, not a copy: an append or a removal is the
        transfer's own.
    """
    return getattr(transfer, side_movements_key(is_income=is_income))


@dataclass(frozen=True)
class TransferLeg:  # pylint: disable=too-many-public-methods
    """One side of a transfer, as the account on that side sees it.

    Pylint: ``too-many-public-methods`` (24/20) -- each answers a question a
    plan ROW answers (the grid's, the dashboard's, the fold's, since leaf
    ``X-bi-6-4a`` the ledger writer's ``user_id``, since leaf
    ``X-bi-6-4c-1`` the statement register's ``covering_movements``, since
    ruling **plan#1983** the fragment renderers' ``purchases``, and since
    ruling **plan#1984** the side's :attr:`record`, read where it was stored),
    derived from the parent, its endpoints or the side's list on the parent,
    so every reader asks a row and a leg the same question.  Fewer would put a
    shape branch back at each reader's site, the thing :attr:`tracks_purchases`
    says this class exists to stop.

    The value every plan reader folds in place of the shadow row it used to
    load, and since leaf ``X-bi-6-1`` the value the grid draws in a
    transfer's cell.  It is a PROJECTION of the parent, so the parent is the
    only thing it stores: the two derived fields say which account is looking
    and from which side, and everything else is read off the row.  The
    display-facing properties below (``name``, ``status``, ``category`` and
    their kin) are the parent's columns read through the leg, so a template
    that draws a row and a leg asks each the same question; a leg that CARRIED
    any of them would be a shadow row in memory.  So does its :attr:`record`,
    since ruling **plan#1984**: it was a field a loader filled until then,
    ``None`` both for a side holding none and for a side whose loader never
    asked (finding **BAL-649**).

    Attributes:
        transfer: The parent :class:`~app.models.transfer.Transfer`.  Its
            ``status_id``, ``pay_period_id``, ``due_date``, ``scenario_id`` and
            ``is_deleted`` are the leg's; its amount is what
            :func:`app.services.cash_ledger.resolve_transfer_amount` answers.
        account_id: The account this leg is on -- the parent's
            ``from_account_id`` or ``to_account_id``.
        is_income: ``True`` on the to-side (money arrives), ``False`` on the
            from-side (money leaves).  The split the two shadows' transaction
            TYPES carried until plan step ``balance:X-bi-6-4d-2`` deleted
            them, stated here from the columns that decide it.
    """

    transfer: Transfer
    account_id: int
    is_income: bool

    @property
    def record(self) -> TransactionEntry | None:
        """The side's COVERING MOVEMENT, read off its transfer's list for this side, or ``None``.

        Where and when this side's money moved -- its account and its day,
        never a figure (the transfer stores that once,
        :attr:`~app.models.transfer.Transfer.settled_figure`) -- written by
        the status seam at the settle, dated when the money moved and kept
        un-dated across a revert (ruling **R-BAL61**).  ``None`` when the side
        holds none: one never settled, or closed at ``$0.00`` (ruling
        **R-BAL82**).

        **Read where it is stored and stored nowhere else** (ruling
        **plan#1984**, finding **BAL-649**): the transfer's list for this side
        (:func:`side_movements`), as ``Transaction.covering_movements`` reads
        a row's ``entries``.  Until then it was a field the loader filled, so
        ``None`` meant both "holds none" and "the loader did not ask", and a
        loader that forgot to ask handed every reader a side with no payment.
        Now "not asked" is not a state a leg can hold: a read finds the list a
        loader pre-loaded (:func:`~._records.preload_side_records`, or the
        movement a leg was built from, ``_records._hold_side_record``) or
        loads it, so a forgotten pre-load costs a query, never a wrong
        answer.  And it is read live: a record the status seam appends, or the
        one removal act takes out, is what the next read sees, with no value
        built before the act to go stale.

        Raises:
            ValueError: When the side's list holds more than one record --
                unstorable under ``uq_transaction_entries_one_*_side_record``,
                so reaching it means a second was written around the status
                seam (``status_seam.covering_movement_of`` refuses a row's
                second the same way).
        """
        movements = side_movements(self.transfer, is_income=self.is_income)
        if not movements:
            return None
        record, *extra = movements
        if extra:
            raise ValueError(
                f"Transfer {self.transfer.id}'s "
                f"{'to' if self.is_income else 'from'}-side holds "
                f"{len(movements)} payment records; a settle writes exactly "
                "one, so a second can only have reached the table around the "
                "status seam."
            )
        return record

    @property
    def is_expense(self) -> bool:
        """``True`` on the from-side: the reduction's other arm."""
        return not self.is_income

    @property
    def cell_key(self) -> tuple[int, int]:
        """The leg's identity on a grid: ``(transfer id, account id)``.

        The key every per-cell map the grid publishes (``budgets``,
        ``settled``, ``retained``, ``due_captions``) holds a leg under, beside
        the plain rows' own ``id`` -- a tuple cannot collide with an ``int``,
        so one map serves both shapes.  Ruling **R-BAL87**.
        """
        return (self.transfer.id, self.account_id)

    @property
    def account(self) -> Account:
        """The account this leg is on, as a row: the parent's endpoint."""
        return (
            self.transfer.to_account if self.is_income
            else self.transfer.from_account
        )

    @property
    def name(self) -> str:
        """The leg's label, composed from the endpoints' CURRENT names."""
        expense, income = leg_label(
            self.transfer.from_account, self.transfer.to_account,
        )
        return income if self.is_income else expense

    @property
    def status_id(self) -> int:
        """The parent's status: a transfer's status lives in ONE row."""
        return self.transfer.status_id

    @property
    def status(self) -> Status:
        """The parent's :class:`~app.models.ref.Status` row."""
        return self.transfer.status

    @property
    def category_id(self) -> int | None:
        """The parent's category, which is where the grid files the leg."""
        return self.transfer.category_id

    @property
    def category(self) -> Category | None:
        """The parent's :class:`~app.models.category.Category` row."""
        return self.transfer.category

    @property
    def scenario_id(self) -> int:
        """The parent's scenario."""
        return self.transfer.scenario_id

    @property
    def user_id(self) -> int:
        """The parent's owner: whose ledger a leg's money is booked in.

        The ledger writer's header owner and transit account
        (``_posting_write.emit_typed_source_deltas``,
        ``_posting_purchases._purchase_target``), read off the transfer, the
        one home a transfer's owner has.
        """
        return self.transfer.user_id

    @property
    def is_deleted(self) -> bool:
        """The parent's soft-delete flag: a leg cannot be deleted alone."""
        return self.transfer.is_deleted

    @property
    def is_override(self) -> bool:
        """The parent's override flag (the pencil marker on a grid cell)."""
        return self.transfer.is_override

    @property
    def notes(self) -> str | None:
        """The parent's notes: a leg has none of its own."""
        return self.transfer.notes

    @property
    def template_id(self) -> None:
        """``None``: a leg names no transaction definition.

        The grid's row-key contract (``grid_view_service.build_row_keys``)
        asks every item it files whether a recurring definition generated it;
        a transfer's rule is its ``transfer_template_id``, which is not a row
        key on the transaction grid, so a leg answers as a one-off does.
        """
        return None

    @property
    def recurs(self) -> bool:
        """``False``: see :attr:`template_id`."""
        return False


    @property
    def pay_period_id(self) -> int:
        """The period the parent is filed in -- the leg's budget column."""
        return self.transfer.pay_period_id

    @property
    def pay_period(self) -> PayPeriod:
        """The parent's :class:`~app.models.pay_period.PayPeriod` row."""
        return self.transfer.pay_period

    @property
    def due_date(self) -> date | None:
        """The parent's due date, or ``None`` for an undated transfer."""
        return self.transfer.due_date

    @property
    def tracks_purchases(self) -> bool:
        """``False``: a transfer's leg is not an envelope.

        The question the dashboard's bill surfaces ask of every item they
        price (``dashboard_service._bills``, ``_pulse._row_still_due``), stated
        here from what a leg IS rather than answered by a branch at each of
        those sites (leaf ``X-bi-6-1b``).  A shadow row answered the same
        ``False`` through ``Transaction.tracks_purchases``' link-less arm
        (ruling **R-BAL73**).  A reader that draws a purchase LIST asks this
        first; the one that sums whatever purchases an item holds asks
        :attr:`purchases`.
        """
        return False

    @property
    def purchases(self) -> tuple[()]:
        """``()``: a leg holds no purchase.

        What ``Transaction.purchases`` answers for a plan row -- what a person
        recorded against it, less the status seam's covering movement --
        asked of a leg, which has nothing recorded against it but its
        :attr:`record`.  It is asked because the entry-sums builder
        (``entry_service.build_entry_sums_dict``) sums whatever purchases an
        item holds without asking :attr:`tracks_purchases`, so one cell
        renderer and one card renderer draw a row and a leg through the one
        call (``routes._render_helpers.render_transaction_cell``,
        ``routes._mobile_card.render_mobile_card``, ruling **plan#1983**),
        where each leg renderer passed an empty map by hand until then.
        """
        return ()

    @property
    def covering_movements(self) -> tuple[TransactionEntry, ...]:
        """The leg's covering movement as a row answers the question: ``(record,)`` or ``()``.

        What ``Transaction.covering_movements`` answers for a plan row --
        the status seam's movements, dated or kept un-dated across a revert
        (ruling **R-BAL61**) -- asked of a leg, whose one possible movement
        is its :attr:`record` (a leg holds no purchase, :attr:`tracks_purchases`).
        So ``status_seam.covered_cash_leg`` values a row's payment and a leg's
        through ONE producer with no shape branch (leaf ``X-bi-6-4c-1``: the
        statement register values every member by what the movement's
        parent moves on the act's account).  Empty for a leg holding no
        movement (one never settled, or closed at ``$0.00``).
        """
        return () if self.record is None else (self.record,)

    @property
    def settled_on(self) -> date | None:
        """The civil day this leg's money moved, or ``None``.

        Read off the leg's RECORD -- its covering movement's ``settled_on``
        (a record says where and when its side's money moved; the transfer
        stores how much) -- and never off the parent: a leg whose money moved
        carries a dated movement (ruling **R-BAL79**), a reverted one a
        movement kept un-dated, a planned one none.  What
        ``Transaction.settled_on`` states as a column for a row, for the
        readers that ask both shapes when a bill was paid
        (``spending_analysis.payment_timeliness_from_txns``).

        **One settled state has NO day here: the $0.00 close** (ruling
        **R-BAL82**, a close with no movement), which a transfer reaches
        through its popover's ``settled_amount`` of ``0.00``.  That is the
        standing answer, not the interval's (ruling **R-BAL90**): nothing was
        paid on any day, a transfer stores no day of its own, and the
        timeliness metric asks the RECORD of a row and a leg alike since leaf
        ``X-bi-6-4a`` (``spending_analysis._holds_a_record``), so a
        $0.00-closed row stops counting as a timed bill too.
        """
        if self.record is None:
            return None
        return self.record.settled_on

    @property
    def days_paid_before_due(self) -> int | None:
        """Days between the due date and the day the money moved, or ``None``.

        :func:`app.utils.dates.days_paid_before_due` over this leg's two
        days -- the ONE arithmetic ``Transaction.days_paid_before_due`` also
        calls.
        """
        return days_paid_before_due(self.due_date, self.settled_on)


#: A plan item as the display readers hold one: a plan row, or one side of a
#: transfer read off its parent.  The grid's window, the dashboard's bills,
#: the calendar's day cells and the Spending report's settled spend are each a
#: list of these since leaves ``X-bi-6-1`` and ``X-bi-6-1b``.
PlanItem = Transaction | TransferLeg

#: A plan item's PARENT: the row that stores its money facts once.  A plan row
#: is its own parent; a transfer leg's is its transfer (:func:`parent_of`).
#: Every pricing rule is a question about a parent (ruling **plan#1983**,
#: "price the parent, draw the side"), so both legs of one transfer are
#: valued by the one stored row and cannot show two figures.
PlanParent = Transaction | Transfer


def parent_of(item: PlanItem) -> PlanParent:
    """Return the parent whose money facts price *item*.

    **The ONE place a row and a leg are told apart for PRICING** (ruling
    **plan#1983**), beside :func:`cell_key`, the one place they are told
    apart for identity.  A page keys a map by the side and values it by the
    parent, ``{cell_key(item): value(parent_of(item))}``: a leg is a
    projection of its transfer that stores no money, and every one of its
    pricing questions (the plan amount, the recorded figure, the retained
    record) is a question about the transfer.  A plan row stores its own
    money facts, so it is its own parent.

    Args:
        item: A row or a leg.

    Returns:
        *item* for a row, its :attr:`TransferLeg.transfer` for a leg.
    """
    if isinstance(item, TransferLeg):
        return item.transfer
    return item


def is_leg_key(key) -> bool:
    """Return ``True`` iff *key* is a transfer leg's :func:`cell_key`.

    The ONE test of a key's shape (finding **BAL-552**): a leg's key is the
    ``(transfer id, account id)`` pair and a row's its ``int`` id, so a reader
    holding only a key -- :func:`key_order`, the reconcile panel's tick form
    (``reconcile_service.TickForm.of``) -- asks this rather than spelling the
    tuple test again.

    Args:
        key: A :func:`cell_key` value.

    Returns:
        ``True`` for a leg's pair, ``False`` for a row's id.
    """
    return isinstance(key, tuple)


def cell_key(item: PlanItem):
    """Return the key a surface publishes *item*'s per-item facts under.

    **The ONE place a row and a leg are told apart for identity** (leaf
    ``X-bi-6-1``, ruling **R-BAL87**): a plan row is keyed by its ``id`` and
    a transfer leg by :attr:`TransferLeg.cell_key`, the ``(transfer id,
    account id)`` pair.  An ``int`` and a tuple cannot collide, so a page's
    ``budgets`` / ``settled`` / ``retained`` / ``due_captions`` maps, the
    dashboard's ``contributions`` and the calendar's hold both shapes in one
    dict and every reader subscripts them with the same expression -- the
    Jinja global of the same name (``app.jinja_filters``) is this function.
    It lived in ``grid_view_service`` until leaf ``X-bi-6-1b`` gave it three
    readers below the grid; the leaf both shapes are defined against is its
    home.

    Args:
        item: A row or a leg.

    Returns:
        ``item.id`` for a row, ``item.cell_key`` for a leg.
    """
    if isinstance(item, TransferLeg):
        return item.cell_key
    return item.id


def key_order(key) -> tuple[int, int, int]:
    """Return a TOTAL order over :func:`cell_key` values, for a mixed sort.

    A row's key is an ``int`` and a leg's a pair, and Python refuses to
    compare the two, so a reader that ranks rows and legs together on their
    identity -- the Spending report's surprises, whose cap follows a rank
    that must be a function of the data (finding **P74**) -- orders by this:
    every row before every leg, rows by id, legs by ``(transfer id, account
    id)``.  The ONE spelling of that order (leaf ``X-bi-6-1b``).

    Args:
        key: A :func:`cell_key` value.

    Returns:
        A three-int tuple that sorts as stated.
    """
    if is_leg_key(key):
        return (1, key[0], key[1])
    return (0, key, 0)


def expense_legs(legs: Iterable[TransferLeg]) -> list[TransferLeg]:
    """Return the legs on which money LEAVES: the from-side of each transfer.

    The ONE spelling of "a transfer's spending half" (leaf ``X-bi-6-1b``):
    the dashboard's bills and the Spending report each draw a transfer only
    from the side its money leaves -- an obligation the paycheck owes, money
    that went -- as the transfer-out shadow row's expense TYPE selected it
    before them; the income leg on the other endpoint is neither.

    Args:
        legs: The legs a set drew, either side.

    Returns:
        Those with :attr:`TransferLeg.is_expense`, in the order given.
    """
    return [leg for leg in legs if leg.is_expense]


def leg_of(transfer: Transfer, account_id: int) -> TransferLeg:
    """Return *transfer*'s leg on *account_id*, refusing an account on neither side.

    A leg from an ACCOUNT asking for it (the other doors, from a SIDE, are
    :func:`transfer_side_leg` itself and, from a movement's link,
    :func:`recorded_transfer_legs`); every one builds through
    :func:`transfer_side_leg`.  ``ck_transfers_different_accounts`` makes the
    two sides distinct, so an account is on at most one of them and the
    answer is never ambiguous.

    Args:
        transfer: The parent transfer.
        account_id: The account asking for its leg.

    Returns:
        The :class:`TransferLeg` on that side.

    Raises:
        ValueError: When *account_id* is neither side of *transfer*.  A
            reader that asks for a leg an account is not on has paired a
            transfer with the wrong account, and a wrong answer here is money
            on the wrong balance.
    """
    if account_id == transfer.to_account_id:
        return transfer_side_leg(transfer, is_income=True)
    if account_id == transfer.from_account_id:
        return transfer_side_leg(transfer, is_income=False)
    raise ValueError(
        f"account {account_id} is on neither side of transfer {transfer.id} "
        f"(from {transfer.from_account_id} to {transfer.to_account_id}): "
        "there is no leg of it for that account to fold"
    )


def transfer_side_leg(transfer: Transfer, *, is_income: bool) -> TransferLeg:
    """Return *transfer*'s leg on one SIDE, its account that side's endpoint.

    **The one construction of a** :class:`TransferLeg` (leaf ``X-bi-6-4a``).
    Its callers reach a side three ways, because they start from three
    different facts: :func:`leg_of` from an ACCOUNT asking for its leg (the
    grid, the fold's plan half), :func:`recorded_transfer_legs` and
    :func:`movement_parent` from a MOVEMENT whose link names its side
    (:func:`_leg_is_income`), and the transfer service from the side ITSELF
    (``_settle.record_leg_clearing`` since leaf ``X-bi-6-4d-1``, and the
    status applier and the endpoint move through :func:`side_legs`, the
    pair -- through :func:`~._records.transfer_sides`, which loads both
    sides' records first, wherever the act reads them, since ruling
    **plan#2136**).  Either way
    the account is the endpoint on that side, so
    ``ck_transfers_different_accounts`` keeps the pair ``(transfer,
    account)`` a leg's identity (:attr:`TransferLeg.cell_key`).

    **Keyed by the SIDE and never by an account**, which is what makes it
    safe inside an act that moves an endpoint: ``_endpoints._apply_endpoint_move``
    assigns the transfer's account RELATIONSHIP, and ``from_account_id`` reads
    the old account until a flush, so a read keyed by that column found the
    record only when some lazy load happened to autoflush first (leaf
    ``X-bi-6-4d-1``'s adversarial review measured the miss).  The side's
    record is read off the side's own list (:attr:`TransferLeg.record`,
    ruling **plan#1984**), so no read of it names an account.  It was a
    loader in ``_records`` running its own query per side, beside a private
    twin of this construction, until that ruling made the record a read of
    the transfer's list: nothing was left to it but this.

    Args:
        transfer: The parent transfer.
        is_income: ``True`` for the to-side, ``False`` for the from-side.

    Returns:
        The :class:`TransferLeg`.
    """
    return TransferLeg(
        transfer=transfer,
        account_id=(
            transfer.to_account_id if is_income else transfer.from_account_id
        ),
        is_income=is_income,
    )


def side_legs(transfer: Transfer) -> tuple[TransferLeg, TransferLeg]:
    """Return *transfer*'s ``(expense, income)`` legs, loading nothing.

    The pair as values, each built by :func:`transfer_side_leg`: a leg reads
    its record off its transfer's list for its side only when asked
    (:attr:`TransferLeg.record`), so building the pair costs no query.
    :func:`~._records.transfer_sides` is this pair with both lists loaded in
    ONE statement, for an act that reads them; the status writer builds this
    one for a move that reads none (ruling **plan#2136**,
    ``transfer_service._status.apply_status_to_all_three``).

    Args:
        transfer: The parent transfer.

    Returns:
        ``(expense leg, income leg)``.
    """
    return (
        transfer_side_leg(transfer, is_income=False),
        transfer_side_leg(transfer, is_income=True),
    )
