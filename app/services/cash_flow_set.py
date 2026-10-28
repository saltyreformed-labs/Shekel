"""
Shekel Budget App -- the owner's CASH-FLOW SET: checking and its cards.

The value that answers "whose plan items are this paycheck's rows" (developer
ruling ``credit_card:R-CC16``, 2026-09-18; design
``docs/design/credit_card_from_scratch.md`` 3.3, plan step CC-4-1).  A plan
item's ``account_id`` is the ONE account its money is expected to move through
-- the phone bill that is always paid by card is a row ON the card -- so every
reader of a paycheck's plan items (the budget grid, the dashboard's upcoming
bills, the spending report, the calendar) reads the owner's cash-flow accounts
as ONE SET rather than one account.  The set is the owner's primary grid
account plus their active revolving accounts; loans, investments and savings
stay out.  ``account_resolver.is_cash_flow_account`` is only "not amortizing"
and admits the IRAs and the savings accounts, which is why the set is a
predicate of its own rather than that one.

**A balance line is still ONE account's.**  The grid's Projected End Balance,
the calendar's end-of-day line, the dashboard's hero: each is the balance of
:attr:`CashFlowSet.balance`, a member of the set (the primary by default; an
override within the set as the grid has always allowed).  The rows are the
set's.  An override naming an account OUTSIDE the set -- a savings account --
keeps that account's single-account view, exactly as before this step: the set
is then that one account.

**A transfer between two members shows ONCE, from the balance line's side**
(developer ruling ``credit_card:R-CC23``, 2026-09-18).  Every transfer is one
row with a leg on each endpoint (two shadow rows, one per endpoint, until plan
step ``balance:X-bi-6-4d-2``), and a single-account reader sees one of them by
construction.  Widening to the set would draw BOTH legs of a checking -> card
payment, and the card's INCOME leg would render as a paycheck income row with
its figure in Total Income -- the owner's own transfer counted as income.  So
the far leg -- a leg on a non-balance member whose transfer's OTHER endpoint
is also a member -- is not a paycheck row.  A transfer with only
one endpoint in the set (savings -> card) still shows from that endpoint, as a
checking -> savings transfer always has.  Ruled against: showing both sides
(Total Income carries the transfer); hiding EVERY transfer row on a non-balance
member (hides a real planned act rather than a second view of one).  Worked on
the ruling's example -- phone ``$45`` on the card, grocery ``$500`` on checking,
a ``$165`` checking -> card payment in the same paycheck: Total Income ``$0``,
Total Expenses ``$710``, Net Cash Flow ``-$710``, "On other accounts" ``+$45``,
checking's balance moves ``-$665``.

The rule's subject -- the transfers with both endpoints in the set -- is
spelled TWICE, over the same two endpoint columns under the same membership
test: the query :func:`_intra_set_transfers`, which the balance seam's
composed subtotal reads (:func:`far_legs_of`), and :func:`leg_accounts_shown`'s
in-memory test of a transfer its caller already holds, which decides the LEGS
every display reader draws (a reader draws a transfer's legs from the parent
rather than loading its shadow rows, and asks this module which of its two
sides the set shows).  While the two stay one test, the cells a reader draws
and the subtotal the seam sums agree about which rows are the paycheck's.
Every display reader's rows are the set's OWN (:func:`own_rows_clause`) --
every member's plan rows, less every shadow until plan step
``balance:X-bi-6-4d-2`` deleted them -- beside the LEGS of every
transfer the set touches (:func:`touched_transfers_clause`, then
:func:`set_transfer_legs`), the two halves one :class:`PlanItems` carries:
the grid since leaf ``X-bi-6-1``, the dashboard's bills, the calendar and the
Spending report since leaf ``X-bi-6-1b``.  **The ROW spelling of the rule --
``paycheck_rows_clause``, every member's rows less the far-leg shadow through
``far_leg_clause`` -- was DELETED at ``X-bi-6-1b`` with its last reader**: a
fence with no reader is deleted, not kept.  :func:`far_legs_of` read the
SETTLED far-leg shadows for the balance seam's subtotal until leaf
``X-bi-6-4a``, when the fold's facts came to name their transfer (ruling
**R-BAL106**) and that read went: both halves are excluded by transfer id.

Services boundary (``CLAUDE.md``): no Flask symbol.  The clauses are built,
never executed, here; the queries this module DOES run are
:func:`set_transfer_legs_in_periods` (the readers' period-windowed transfer
load, with the record load :func:`set_transfer_legs` reaches through
``transfer_legs``) and :func:`far_legs_of`'s one for the seam.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import NamedTuple

from sqlalchemy import or_

from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.transfer_legs import (
    PlanItem,
    TransferLeg,
    grid_transfer_legs,
)
from app.utils.amount_relationships import transfer_pricing_load_options


@dataclass(frozen=True)
class CashFlowSet:
    """The owner's cash-flow accounts, and which one's balance line renders.

    Built by :func:`app.services.account_resolver.resolve_cash_flow_set`; read
    by every plan-item reader and by the balance seam's grid view.  Frozen and
    total: :attr:`balance` is always a member of :attr:`members`, which is
    never empty.

    Attributes:
        balance: The account whose BALANCE the surface renders -- the primary
            grid account by default, or the override the request named.
        members: Every account whose plan items are the paycheck's rows, in
            picker order: the PRIMARY first, then the owner's active cards by
            ``sort_order`` then ``id``.  The balance account is a member and
            not necessarily the first -- an override naming a card puts the
            card on the line and leaves the order alone.  ONE account when
            the owner has no active card, or when the balance line is an
            account outside the set.
    """

    balance: Account
    members: tuple[Account, ...]

    def __post_init__(self):
        """Refuse a balance line that is not a member: the type's one invariant."""
        if not any(m.id == self.balance.id for m in self.members):
            raise ValueError(
                "the balance account must be a member of the set: "
                f"balance={self.balance.id} members="
                f"{[m.id for m in self.members]}"
            )

    @property
    def member_ids(self) -> tuple[int, ...]:
        """The members' ``budget.accounts.id`` values, in member order."""
        return tuple(m.id for m in self.members)

    @property
    def others(self) -> tuple[Account, ...]:
        """Every member but the balance account, in member order."""
        return tuple(m for m in self.members if m.id != self.balance.id)

    def seen_from(self, balance: Account) -> "CashFlowSet":
        """The VIEW of this set behind *balance*'s line: the ``?account_id=`` rule.

        The one rule of the grid's override, stated on the set it applies to
        (plan step ``credit_card:CC-5-2``; the walk lived inline in the
        resolver's private builder, then named ``_cash_flow_set``, since
        CC-4-1): a member keeps the
        set's rows behind its own balance line, and an account OUTSIDE the
        set is that account's single-account view, exactly as before the set
        existed.  A pure derivation, so the OWNER's set is resolved once per
        request and the view it renders is read off it -- the alternative,
        a second resolve for the accounts a purchase may name beside the
        one for the rows the page shows, would run the primary chain and
        the cards query twice per grid render for one answer.

        Args:
            balance: An account the caller has ADMITTED as the balance line
                (:func:`~app.services.account_resolver.resolve_cash_flow_set`
                runs the admission test); never an unadmitted row.

        Returns:
            This set behind *balance*, or :meth:`single` of *balance*.
        """
        if balance.id in self.member_ids:
            return CashFlowSet(balance=balance, members=self.members)
        return CashFlowSet.single(balance)

    @classmethod
    def single(cls, account: Account) -> "CashFlowSet":
        """The set of ONE account: its own rows behind its own balance line.

        What every single-account reader of the grid view asks for -- the
        cross-page equality tests, the anchor surfaces, the balance baseline
        harness -- and the shape the resolver answers for an owner with no
        card or for a balance line outside the set.  A legitimate set, not a
        compatibility shim: ``elsewhere`` is ``0.00`` in every column and the
        subtotals are the account's own.
        """
        return cls(balance=account, members=(account,))


def purchase_accounts(
    cash_flow: CashFlowSet | None, txn: Transaction,
) -> tuple[Account, ...]:
    """Return the accounts a purchase under *txn* may name, in picker order.

    **The ONE spelling of "which accounts may this row's purchase have moved
    through"** (developer rulings ``credit_card:R-CC37``, 2026-09-20, and
    **R-CC39**, 2026-09-21, which confirms it for EVERY row -- a Savings
    envelope offers Savings, Checking and the cards -- and amends R-CC34's
    narrower parenthetical; plan step CC-5-2): the row's own account, then
    every member of the ROW OWNER's cash-flow set that is not it, in the
    set's own order.  The purchase door
    (``entry_service._doors._purchase_account_id``) admits exactly this
    tuple's accounts and the add-purchase form renders its picker from it, so
    the door and the form cannot part: a swipe on a 401(k), an IRA, a house, a
    loan or a savings account is unwritable rather than merely unoffered,
    while a row that LIVES on such an account keeps its purchases there --
    its own account is always the first member.

    *cash_flow* is the OWNER's set with NO override
    (:func:`~app.services.account_resolver.resolve_cash_flow_set` with
    ``override_account_id=None``), never the page's.  A ``?account_id=``
    override naming an account outside the set collapses the page's set to
    that one account (the single-account view above), which is a fact about
    the VIEW; what a purchase may name is a fact about the owner, and reading
    the view's set here would offer the picker on one page and not another
    for the same row.  ``None`` -- an owner with no grid-eligible account at
    all -- leaves the row's own account as the one choice.

    The first member is the form's default: a purchase with no account named
    is on its row's account (the door's ``None`` arm).

    Args:
        cash_flow: The row OWNER's cash-flow set, resolved with no override,
            or ``None``.
        txn: The plan row the purchase is recorded against.  Its ``account``
            is a joined load, so this reads no query.

    Returns:
        The row's own account first, then the set's other members in member
        order; never empty.
    """
    if cash_flow is None:
        return (txn.account,)
    return (
        txn.account,
        *[m for m in cash_flow.members if m.id != txn.account_id],
    )


def _intra_set_transfers(cash_flow: CashFlowSet):
    """Return the query of transfer ids with BOTH endpoints in the set.

    The ONE spelling of the rule's subject.  Executed by :func:`far_legs_of`
    (and, until leaf ``X-bi-6-1b`` deleted the row spelling, a subquery of
    ``far_leg_clause``); a member's endpoints are the owner's accounts, so it
    can name no other owner's transfer.

    Args:
        cash_flow: The set.

    Returns:
        A query of ``budget.transfers.id`` -- built, not executed.
    """
    member_ids = cash_flow.member_ids
    return (
        db.session.query(Transfer.id)
        .filter(
            Transfer.from_account_id.in_(member_ids),
            Transfer.to_account_id.in_(member_ids),
        )
    )


def own_rows_clause(cash_flow: CashFlowSet):
    """Return the clause selecting the set's OWN plan rows: every member's.

    ``Transaction.account_id IN members``: every member's rows, for a reader
    that draws a transfer's legs from the parent (leaf ``X-bi-6-1``, ruling
    **R-BAL87**) -- every display reader since leaf ``X-bi-6-1b``.  Until plan
    step ``balance:X-bi-6-4d-2`` deleted the shadow rows, a ``transfer_id IS
    NULL`` term kept every shadow from loading beside the legs: the clause it
    replaced (``paycheck_rows_clause``) dropped only the FAR leg and kept the
    near one as a row, and this dropped both, because the near leg is drawn as
    a :class:`~app.services.transfer_legs.TransferLeg` by
    :func:`leg_accounts_shown`'s rule.  No transaction row names a transfer
    since, so every member row is an own row.

    Args:
        cash_flow: The set.

    Returns:
        A SQLAlchemy boolean clause over :class:`Transaction`.
    """
    return Transaction.account_id.in_(cash_flow.member_ids)


def leg_accounts_shown(cash_flow: CashFlowSet, transfer: Transfer) -> tuple[int, ...]:
    """Return the accounts whose leg of *transfer* the set draws (ruling **R-CC23**).

    The ONE reader-facing spelling of the rule (the row spelling,
    ``far_leg_clause``, went at leaf ``X-bi-6-1b``): a
    transfer with BOTH endpoints in the set shows once, from the balance
    line's side; one with a single endpoint in the set shows from that
    endpoint; one touching no member shows nowhere.  Stated over the two
    endpoint columns rather than by executing :func:`_intra_set_transfers`,
    because a caller holds the transfer already and the membership test is
    the same predicate that query filters on.

    **The fourth arm is the rule's own gap, reproduced rather than decided
    here**: both endpoints in the set and the balance line on NEITHER -- a
    checking -> card B payment seen from card A's balance line, members
    ``(checking, A, B)``.  The row spelling dropped BOTH shadows there
    (each was a member row that is not the balance account, of an intra-set
    transfer), so the payment was drawn nowhere on A's grid, and
    :func:`far_legs_of` keeps both out of the subtotal the same way.  This
    answers ``()`` for exactly that case, which is what the rows did; whether
    R-CC23 MEANS "nowhere" there is an open question for the developer,
    recorded at leaf ``balance:X-bi-6-1`` (its adversarial review found the
    first cut returning the balance account here, which
    :func:`~app.services.transfer_legs.leg_of` refuses as a leg the transfer
    has no side on -- a 500 on the grid).

    Args:
        cash_flow: The set.
        transfer: A transfer the caller loaded.

    Returns:
        The account ids to draw a leg for: one (the usual case), or none.
        Never two -- both sides in the set is the far-leg case, and a set
        cannot hold an endpoint twice (``ck_transfers_different_accounts``).
    """
    members = set(cash_flow.member_ids)
    on_from = transfer.from_account_id in members
    on_to = transfer.to_account_id in members
    if on_from and on_to:
        balance_id = cash_flow.balance.id
        if balance_id in (transfer.from_account_id, transfer.to_account_id):
            return (balance_id,)
        return ()
    if on_from:
        return (transfer.from_account_id,)
    if on_to:
        return (transfer.to_account_id,)
    return ()


def touched_transfers_clause(cash_flow: CashFlowSet):
    """Return the clause selecting every transfer the set TOUCHES.

    ``Transfer.from_account_id IN members OR Transfer.to_account_id IN
    members``: the transfer twin of :func:`own_rows_clause` (leaf
    ``X-bi-6-1b``), and the ONE spelling of which parents a reader of the
    set's plan items loads before :func:`leg_accounts_shown` decides which
    side -- if any -- it draws.  Every display reader appends THIS to its
    transfer query beside :func:`own_rows_clause` on its row query, exactly
    as each appends its own window and status filters to both; the grid's
    loader spelled it inline until this leaf gave it three more readers.

    Args:
        cash_flow: The set.

    Returns:
        A SQLAlchemy boolean clause over :class:`Transfer`.
    """
    member_ids = cash_flow.member_ids
    return or_(
        Transfer.from_account_id.in_(member_ids),
        Transfer.to_account_id.in_(member_ids),
    )


def set_transfer_legs(
    cash_flow: CashFlowSet, transfers: Iterable[Transfer],
) -> list[TransferLeg]:
    """Return the legs the set draws for *transfers*, their records not loaded.

    :func:`~app.services.transfer_legs.grid_transfer_legs` under the set's
    own rule: one leg per account :func:`leg_accounts_shown` names for each
    transfer.  The leaf's loader takes the rule as a function so that it
    states no set of its own; this is where the set supplies it, ONCE, for
    every reader (leaf ``X-bi-6-1b``) -- the period-windowed ones through
    :func:`set_transfer_legs_in_periods`, the rest with their own query.  A
    reader that reads the legs' records pre-loads them itself (the Spending
    report's settled items, ruling **plan#2136**); the grid, the dashboard and
    the calendar read none.

    Args:
        cash_flow: The set.
        transfers: The parents a reader loaded for its window, with the
            relationships its render will read.

    Returns:
        The legs, in transfer order then side order; ``[]`` for none.
    """
    return grid_transfer_legs(
        transfers, lambda transfer: leg_accounts_shown(cash_flow, transfer),
    )


def set_transfer_legs_in_periods(
    cash_flow: CashFlowSet, scenario_id: int, period_ids, *filters,
) -> list[TransferLeg]:
    """Load the legs of every live transfer the set touches in *period_ids*.

    The ONE query behind every reader windowed by PERIOD MEMBERSHIP -- the
    grid window, the dashboard's bills, the calendar and the Spending
    report's pay-period arm (leaf ``X-bi-6-1b``): the parents through
    :func:`touched_transfers_clause`, in *scenario_id*, filed in
    *period_ids*, live, narrowed by whatever *filters* the reader's own
    question adds (``is_projected_clause(Transfer)`` for a bill,
    ``balance_contributing_clause(Transfer)`` for a calendar cell, the
    settled statuses for spent money; nothing for the grid), then
    :func:`set_transfer_legs` over them.  Each parent carries
    :func:`~app.utils.amount_relationships.transfer_pricing_load_options`
    (the chain a leg's price walks, and the period a bill reads); the
    endpoints, status and category are ``lazy="joined"`` on the model.  A
    reader windowed some other way -- the Spending report's calendar span,
    selected by attribution day -- builds its own query and calls
    :func:`set_transfer_legs` directly.

    Args:
        cash_flow: The set.
        scenario_id: The budget scenario the transfers live in.
        period_ids: The pay period ids the window holds; empty loads none.
        *filters: Further SQLAlchemy clauses over :class:`Transfer`.

    Returns:
        The legs, in transfer id order then side order; ``[]`` for none.
    """
    if not period_ids:
        return []
    transfers = (
        db.session.query(Transfer)
        .options(*transfer_pricing_load_options())
        .filter(
            touched_transfers_clause(cash_flow),
            Transfer.scenario_id == scenario_id,
            Transfer.pay_period_id.in_(period_ids),
            Transfer.is_deleted.is_(False),
            *filters,
        )
        .order_by(Transfer.id)
        .all()
    )
    return set_transfer_legs(cash_flow, transfers)


class PlanItems(NamedTuple):
    """What a reader of the set's plan items draws: its own rows and the legs.

    The one shape the grid window (``routes/grid/_items.load_grid_items``),
    the dashboard's unpaid bills, the calendar's period rows and the Spending
    report's settled expenses each answer (leaves ``X-bi-6-1`` and
    ``X-bi-6-1b``): the rows :func:`own_rows_clause` selected and the legs
    :func:`set_transfer_legs` drew, kept apart because the per-item maps are
    built by a row producer and its leg twin, and together because every
    row-key, match and fold over them takes one list.

    Attributes:
        rows: The plan rows, :class:`~app.models.transaction.Transaction`.
        legs: One :class:`~app.services.transfer_legs.TransferLeg` per
            transfer the set shows, from the side it shows.
        items: ``rows + legs``, the list every reader that treats the two
            alike iterates.
    """

    rows: list[Transaction]
    legs: list[TransferLeg]
    items: list[PlanItem]

    @classmethod
    def of(cls, rows: list[Transaction], legs: list[TransferLeg]) -> "PlanItems":
        """Build the value with ``items`` derived, so it cannot disagree."""
        return cls(rows=rows, legs=legs, items=[*rows, *legs])


@dataclass(frozen=True)
class FarLegs:
    """The far legs of the set's intra-set transfers, by their transfer.

    What the balance seam needs to keep its composed subtotal in step with the
    items the readers draw (:func:`leg_accounts_shown`).  A member's budget legs
    (:func:`~app.services.balance_at._cash_periods._budget_legs`) hold a
    transfer two ways, and both are excluded by the TRANSFER's id: for a leg
    on a NON-balance member, "far" is exactly "its transfer has both endpoints
    in the set", so :attr:`transfer_ids` is every intra-set transfer, read off
    ``budget.transfers`` alone.

    * a STILL-PROJECTED transfer is a
      :class:`~app.services.transfer_legs.TransferLeg` derived from its
      parent (plan step X-bi-6a);
    * a SETTLED one is a :class:`~app.services.cash_ledger.CashSourceFact`
      whose :attr:`~app.services.cash_ledger.CashSourceFact.transfer_id` names
      it (leaf ``X-bi-6-4a``, ruling **R-BAL106**).  It was keyed by the
      SHADOW's ``transaction_id`` until then, read off the settled shadow
      rows by a second query; a fact that names its transfer needs neither,
      and a leg's money excluded by its transfer was excluded whatever its
      shadow's status said.

    **No shadow row is read** (Transfer Invariant 5: "no balance reader reads
    a projected shadow row"), so nothing here moved when plan step
    ``balance:X-bi-6-4d-2`` deleted the shadows.

    Attributes:
        transfer_ids: Every intra-set transfer's ``budget.transfers.id``.
    """

    transfer_ids: frozenset[int]

    @classmethod
    def none(cls) -> "FarLegs":
        """The empty answer, for a set with nothing to exclude."""
        return cls(transfer_ids=frozenset())


def far_legs_of(cash_flow: CashFlowSet) -> FarLegs:
    """Return the set's far legs -- one query, for the balance seam.

    Issues nothing for a one-member set: it has no other member to hold a far
    leg, so the answer is empty by construction rather than by a round trip.
    The query filters on neither scenario nor soft-deletion, on purpose: the
    seam's walk and plan are scenario-scoped and read live rows only, so an
    id here that names a row they never hold excludes nothing, and a filter
    would be a second statement of "which rows are live" beside theirs.

    Args:
        cash_flow: The set.

    Returns:
        The :class:`FarLegs`.
    """
    if not cash_flow.others:
        return FarLegs.none()
    return FarLegs(
        transfer_ids=frozenset(
            row.id for row in _intra_set_transfers(cash_flow).all()
        ),
    )
