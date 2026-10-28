"""
Shekel Budget App -- A transfer LEG: the value, its label and its identity.

The half of :mod:`app.services.transfer_legs` whose code is blind to where a
movement hangs: the :class:`TransferLeg` value every reader holds a transfer's
side as (carrying the covering movement a loader hands it as its
:attr:`~TransferLeg.record`), the one composition of a leg's LABEL
(:func:`leg_label`), the one statement of a leg's IDENTITY beside a plan row's
(:func:`cell_key`, :func:`key_order`, ruling **R-BAL87**), and the one
construction of a leg from its parent (:func:`leg_of`, :func:`_leg_on_side`).
The package docstring carries the argument.  The functions these definitions
name by bare name (:func:`planned_transfer_legs`,
:func:`recorded_transfer_legs`, :func:`covering_movements_by_leg`,
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


@dataclass(frozen=True)
class TransferLeg:  # pylint: disable=too-many-public-methods
    """One side of a transfer, as the account on that side sees it.

    Pylint: ``too-many-public-methods`` (22/20) -- each answers a question a
    plan ROW answers (the grid's, the dashboard's, the fold's, since leaf
    ``X-bi-6-4a`` the ledger writer's ``user_id``, and since leaf
    ``X-bi-6-4c-1`` the statement register's ``covering_movements``), derived
    from the parent, its endpoints or the leg's record, so every reader asks
    a row and a leg the same question.  Fewer would put a shape branch back
    at each reader's site, the thing :attr:`tracks_purchases` says this class
    exists to stop.

    The value every plan reader folds in place of the shadow row it used to
    load, and since leaf ``X-bi-6-1`` the value the grid draws in a
    transfer's cell.  It is a PROJECTION of the parent, so the parent is the
    only thing it stores: the two derived fields say which account is looking
    and from which side, and everything else is read off the row.  The
    display-facing properties below (``name``, ``status``, ``category`` and
    their kin) are the parent's columns read through the leg, so a template
    that draws a row and a leg asks each the same question; a leg that CARRIED
    any of them would be a shadow row in memory.

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
        record: The leg's COVERING MOVEMENT
            (:class:`~app.models.transaction_entry.TransactionEntry`,
            ``covers_settlement``) when one exists: where and when this side's
            money moved -- its account and its day, never a figure (the
            transfer stores that once,
            :attr:`~app.models.transfer.Transfer.settled_figure`) -- written
            by the status seam at the settle, dated when the money moved and
            kept un-dated across a revert (ruling **R-BAL61**).
            ``None`` when the leg has none -- or when the loader did not ask:
            the fold's :func:`planned_transfer_legs` emits the legs of
            still-Projected transfers, whose sides hold no dated movement
            (the status-band rule, :mod:`app.side_band_infrastructure`), and
            never loads one, while the grid's
            :func:`grid_transfer_legs` loads every leg's through
            :func:`covering_movements_by_leg`.  :func:`movement_parent` gave
            a leg ``None`` for a movement that was NOT its leg's record (one
            under a DEAD shadow, or an entry under a live shadow that covered
            no settlement), which is how the ledger writer knew to post
            nothing for it, until plan step ``balance:X-bi-6-4d-2``: it reads
            a movement's side link now, and a side record is always its
            leg's record.
    """

    transfer: Transfer
    account_id: int
    is_income: bool
    record: TransactionEntry | None = None

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
        (ruling **R-BAL73**).  Every reader of a row's ``purchases`` asks
        this first, so a leg needs no ``purchases`` of its own.
        """
        return False

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
        movement (one never settled, or closed at ``$0.00``) or one whose
        loader did not ask (:attr:`record`).
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
    if isinstance(key, tuple):
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


def leg_of(
    transfer: Transfer, account_id: int, *,
    record: TransactionEntry | None = None,
) -> TransferLeg:
    """Return *transfer*'s leg on *account_id*, refusing an account on neither side.

    A leg from an ACCOUNT asking for it (the other door, from a movement's
    link, is :func:`recorded_transfer_legs`); both build through
    :func:`_leg_on_side`.  ``ck_transfers_different_accounts`` makes the two sides distinct,
    so an account is on at most one of them and the answer is never ambiguous.

    Args:
        transfer: The parent transfer.
        account_id: The account asking for its leg.
        record: The leg's covering movement, when the caller loaded one (the
            grid); the fold's callers pass none.  See
            :attr:`TransferLeg.record`.

    Returns:
        The :class:`TransferLeg` on that side.

    Raises:
        ValueError: When *account_id* is neither side of *transfer*.  A
            reader that asks for a leg an account is not on has paired a
            transfer with the wrong account, and a wrong answer here is money
            on the wrong balance.
    """
    if account_id == transfer.to_account_id:
        return _leg_on_side(transfer, is_income=True, record=record)
    if account_id == transfer.from_account_id:
        return _leg_on_side(transfer, is_income=False, record=record)
    raise ValueError(
        f"account {account_id} is on neither side of transfer {transfer.id} "
        f"(from {transfer.from_account_id} to {transfer.to_account_id}): "
        "there is no leg of it for that account to fold"
    )


def _leg_on_side(
    transfer: Transfer, *, is_income: bool, record: TransactionEntry | None,
) -> TransferLeg:
    """Return *transfer*'s leg on one SIDE, its account that side's endpoint.

    The one construction of a :class:`TransferLeg` (leaf ``X-bi-6-4a``).
    Its two callers reach a side two ways, because they start from two
    different facts: :func:`leg_of` from an ACCOUNT asking for its leg (the
    plan half, which has no movement to ask), and
    :func:`recorded_transfer_legs` from a MOVEMENT whose link names its side
    (:func:`_leg_is_income`).  Either way the account is the endpoint on
    that side, so ``ck_transfers_different_accounts`` keeps the pair
    ``(transfer, account)`` a leg's identity (:attr:`TransferLeg.cell_key`).

    Args:
        transfer: The parent transfer.
        is_income: ``True`` for the to-side, ``False`` for the from-side.
        record: The leg's covering movement, or ``None``.

    Returns:
        The :class:`TransferLeg`.
    """
    return TransferLeg(
        transfer=transfer,
        account_id=(
            transfer.to_account_id if is_income else transfer.from_account_id
        ),
        is_income=is_income,
        record=record,
    )
