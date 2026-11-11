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
member (hides a real planned act rather than a second view of one).

**The set decides which ROWS a screen draws, not what its totals count**
(developer ruling plan#2926, 2026-11-05, which amends ``R-CC16`` and
``R-CC23`` for the grid's totals; plan task plan#2932).  The grid's Total
Income, Total Expenses and Net Cash Flow are the balance account's own -- what
leaves it and what comes in -- so a bill on the card is drawn on the card's
row, with its chip, and counted once, by the card's payment out of checking.
From plan step CC-4-1 until that task the balance seam summed every member's
rows into the grid's totals and reconciled them with the one account's balance
through an "On other accounts" row, reading this module's far legs to keep the
card's half of a checking -> card payment out; on the ruling's example above
that read Total Expenses ``$710`` with ``+$45`` on other accounts.  Read off
checking alone it is ``$665``, the ``$500`` grocery and the ``$165`` payment,
and checking's balance moves by exactly that.  The far-leg query, the
``others`` view of the set and the seam's composed subtotal went together,
having no reader left.  The dashboard's still-due totals follow the same rule
(ruling plan#3524, plan task plan#3528), and so do the analytics calendar's
day, month and year totals (ruling plan#3526, plan task plan#3529), through
:meth:`CashFlowSet.other_member`, the one test of which drawn items a screen's
totals leave out.

The rule's subject -- the transfers with both endpoints in the set -- is
spelled ONCE, by :func:`leg_accounts_shown`'s test of a transfer its caller
already holds, which decides the LEGS every display reader draws (a reader
draws a transfer's legs from the parent rather than loading its shadow rows,
and asks this module which of its two sides the set shows).  It was spelled a
second time, as a query the balance seam's composed subtotal read, until plan
task plan#2932 deleted that subtotal.  Every display reader's rows are the
set's OWN (:func:`own_rows_clause`) --
every member's plan rows, less every shadow until plan step
``balance:X-bi-6-4d-2`` deleted them -- beside the LEGS of every
transfer the set touches (:func:`touched_transfers_clause`, then
:func:`set_transfer_legs`), one list since plan step **plan#2130** and two
halves of one value (``PlanItems``) until it: the grid since leaf
``X-bi-6-1``, the dashboard's bills, the calendar and the Spending report
since leaf ``X-bi-6-1b``.  **The ROW spelling of the rule --
``paycheck_rows_clause``, every member's rows less the far-leg shadow through
``far_leg_clause`` -- was DELETED at ``X-bi-6-1b`` with its last reader**: a
fence with no reader is deleted, not kept.

**A screen states its scope ONCE** (plan step **plan#2130**): it fills in a
:class:`PlanScope` -- the set, the scenario, a :class:`Paychecks` or
:class:`AttributionSpan` window, a :class:`StatusBand`, expenses only -- and
:func:`load_plan_items` turns every part into a clause for both parent tables
in one place and answers one list, the rows by id then the sides.

Services boundary (``CLAUDE.md``): no Flask symbol.  The clauses are built,
never executed, here; the queries this module DOES run are
:func:`load_plan_items`'s two (with the record pre-load a screen that reads
records asks for).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import TypeVar

from sqlalchemy import or_
from sqlalchemy.orm import Query
from sqlalchemy.orm.interfaces import LoaderOption
from sqlalchemy.sql.expression import ColumnElement

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.transfer_legs import (
    PlanItem,
    PlanParent,
    TransferLeg,
    expense_legs,
    grid_transfer_legs,
    preload_side_records,
)
from app.utils.amount_relationships import transfer_pricing_load_options
from app.utils.balance_predicates import (
    balance_contributing_clause,
    is_projected_clause,
    settled_status_ids,
)


@dataclass(frozen=True)
class CashFlowSet:
    """The owner's cash-flow accounts, and which one's balance line renders.

    Built by :func:`app.services.account_resolver.resolve_cash_flow_set`; read
    by every plan-item reader; the balance seam reads only its
    :attr:`balance`, through the route (plan task plan#2932).  Frozen and
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

    def other_member(self, item: PlanItem) -> Account | None:
        """Return the member *item* is on, or ``None`` when it is the balance account.

        The ONE test of whether a screen's totals count an item it draws
        (developer rulings plan#2926, plan#3524 and plan#3526; plan tasks
        plan#2932, plan#3528 and plan#3529).  Its readers are the
        dashboard's still due and the analytics calendar's folds; the grid
        needs none, its totals being the balance seam's own column for the
        one account rather than a sum of the items it draws.  A screen draws
        every member's items behind one account's balance line, and its
        totals are that account's own, so an item on
        another member is drawn, marked with that member's name, and not
        added in.  A purchase on the card is then counted once, by the card's
        payment out of checking.  A leg is on the account of the side the set
        drew (:func:`leg_accounts_shown`), so a checking -> card payment seen
        from checking's line is checking's.

        Args:
            item: A row or a leg this set's loader selected
                (:func:`load_plan_items`), and so on a member.

        Returns:
            ``None`` for an item on :attr:`balance`; else the member it is on.

        Raises:
            ValueError: *item* is on no member -- an item some other loader
                selected, whose totals this set cannot answer for.
        """
        if item.account_id == self.balance.id:
            return None
        for member in self.members:
            if member.id == item.account_id:
                return member
        raise ValueError(
            f"the item is on account {item.account_id}, which is not a member "
            f"of the set: members={list(self.member_ids)}"
        )

    @classmethod
    def single(cls, account: Account) -> "CashFlowSet":
        """The set of ONE account: its own rows behind its own balance line.

        The shape the resolver answers for an owner with no card or for a
        balance line outside the set, and what :meth:`seen_from` answers for
        the latter.  A legitimate set, not a compatibility shim: its rows are
        that account's own.
        """
        return cls(balance=account, members=(account,))


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
    endpoint columns of a transfer the caller already holds; it is the
    rule's only spelling since plan task plan#2932 deleted the query the
    balance seam's composed subtotal read.

    **The fourth arm is the rule's own gap, reproduced rather than decided
    here**: both endpoints in the set and the balance line on NEITHER -- a
    checking -> card B payment seen from card A's balance line, members
    ``(checking, A, B)``.  The row spelling dropped BOTH shadows there
    (each was a member row that is not the balance account, of an intra-set
    transfer), so the payment was drawn nowhere on A's grid.  This
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
    side -- if any -- it draws.  :func:`load_plan_items` puts THIS on its
    transfer query beside :func:`own_rows_clause` on its row query, with the
    screen's window and status band on both (plan step **plan#2130**; every
    display reader appended the two clauses and its own filters itself until
    then); the grid's loader spelled it inline until leaf ``X-bi-6-1b`` gave
    it three more readers.

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
    every reader (leaf ``X-bi-6-1b``), through :func:`load_plan_items` since
    plan step **plan#2130**.  A reader that reads the legs' records asks that
    loader to pre-load them (``side_records``: the Spending report's settled
    items, ruling **plan#2136**); the grid, the dashboard and the calendar
    read none.

    Args:
        cash_flow: The set.
        transfers: The parents the loader selected for a screen's scope, with
            the relationships its render will read.

    Returns:
        The legs, in transfer order then side order; ``[]`` for none.
    """
    return grid_transfer_legs(
        transfers, lambda transfer: leg_accounts_shown(cash_flow, transfer),
    )


#: The parent table a scope's query is rooted at, ``Transaction`` or
#: ``Transfer``: a query narrowed here comes back rooted at the same one.
_Parent = TypeVar("_Parent", bound=PlanParent)


class StatusBand(Enum):
    """Which statuses a screen draws its plan items from.

    One part of a :class:`PlanScope`, and each band is ONE predicate over
    either parent table, ``Transaction`` or ``Transfer``, through the clause
    :mod:`app.utils.balance_predicates` already states for it -- so a screen's
    bill rows and its transfer sides are drawn from the same statuses because
    there is one statement of them, not two that agree.

    * :attr:`EVERY` -- no status filter: the grid draws every row and side,
      cancelled and credit included, and its cells decide what they show.
    * :attr:`PROJECTED` -- still planned: the dashboard's unpaid bills.
    * :attr:`SETTLED` -- paid or received: the Spending report's spent money.
    * :attr:`BALANCE_CONTRIBUTING` -- projected or settled, never credit or
      cancelled: the calendar's day cells (the F-3 Choice-2 predicate, wider
      than the grid subtotal's on purpose).
    """

    EVERY = "every"
    PROJECTED = "projected"
    SETTLED = "settled"
    BALANCE_CONTRIBUTING = "balance_contributing"

    def clauses(self, model: type[PlanParent]) -> tuple[ColumnElement[bool], ...]:
        """Return the band's filters over *model*.

        Args:
            model: ``Transaction`` or ``Transfer``; both carry ``status_id``
                and ``is_deleted`` under the same names.

        Returns:
            A tuple of SQLAlchemy clauses over *model*; empty for
            :attr:`EVERY`.
        """
        if self is StatusBand.PROJECTED:
            return (is_projected_clause(model),)
        if self is StatusBand.SETTLED:
            return (model.status_id.in_(settled_status_ids()),)
        if self is StatusBand.BALANCE_CONTRIBUTING:
            return (balance_contributing_clause(model),)
        return ()


@dataclass(frozen=True)
class Paychecks:
    """A window of pay periods: the items FILED under any of them.

    The grid's, the dashboard's, the calendar's and the Spending report's
    pay-period window: selected by period membership, ``pay_period_id IN
    (...)``, on either parent table.

    Attributes:
        period_ids: The ``budget.pay_periods.id`` values the window holds.
    """

    period_ids: tuple[int, ...]

    @property
    def selects_nothing(self) -> bool:
        """``True`` for no periods: the loader answers ``[]`` with no statement."""
        return not self.period_ids

    def narrow(self, query: Query[_Parent], model: type[_Parent]) -> Query[_Parent]:
        """Return *query* over *model* narrowed to the window.

        Args:
            query: A query rooted at *model*.
            model: ``Transaction`` or ``Transfer``.

        Returns:
            The narrowed query.
        """
        return query.filter(model.pay_period_id.in_(self.period_ids))


@dataclass(frozen=True)
class AttributionSpan:
    """A calendar span: the items ATTRIBUTED to a day inside it.

    The Spending report's calendar window: an item belongs to the span when
    ``COALESCE(due_date, its pay period's start)`` falls in ``[first_day,
    last_day]``, across all the owner's periods, so a bill due in month M is
    M's even when its funding period does not overlap M.  The period is
    JOINED for its start and not loaded.

    Attributes:
        first_day: The span's first calendar day (inclusive).
        last_day: The span's last calendar day (inclusive).
    """

    first_day: date
    last_day: date

    @property
    def selects_nothing(self) -> bool:
        """``False``: which days an item is attributed to only the database answers."""
        return False

    def narrow(self, query: Query[_Parent], model: type[_Parent]) -> Query[_Parent]:
        """Return *query* over *model* narrowed to the items attributed to the span.

        **The joined period must be the item's OWN owner's**, which is a fact
        the database holds for a row (``fk_transactions_owner_period``) and
        not for a transfer, whose period key is a plain one.  Stated against
        the item's ``user_id`` -- a member's own owner, held equal to every
        endpoint's by the transfer's composite account keys -- it selects
        what the report's ``PayPeriod.user_id == <the owner>`` did, without a
        second statement of who the owner is.

        Args:
            query: A query rooted at *model*.
            model: ``Transaction`` or ``Transfer``; both carry ``due_date``,
                ``pay_period_id`` and ``user_id``.

        Returns:
            The narrowed query, ``budget.pay_periods`` joined.
        """
        attribution_day = db.func.coalesce(model.due_date, PayPeriod.start_date)
        return (
            query.join(PayPeriod, model.pay_period_id == PayPeriod.id)
            .filter(
                PayPeriod.user_id == model.user_id,
                attribution_day >= self.first_day,
                attribution_day <= self.last_day,
            )
        )


@dataclass(frozen=True)
class PlanScope:
    """WHICH plan items a screen draws: its scope, stated once for both tables.

    The one value a reader of the set's plan items fills in (plan step
    **plan#2130**, ruling **plan#2131**): the grid, the dashboard's bills, the
    calendar and the Spending report each state their scope here and call
    :func:`load_plan_items`, which turns every part into a clause for each
    parent table in one place.  Until that step each wrote two queries, one
    over ``budget.transactions`` and one over ``budget.transfers``, and
    spelled every part twice -- five times over.

    Attributes:
        cash_flow: Whose items: the set's own rows
            (:func:`own_rows_clause`) and the legs of every transfer it
            touches (:func:`touched_transfers_clause`), drawn from the side
            :func:`leg_accounts_shown` names.
        scenario_id: The budget scenario the items live in.
        window: :class:`Paychecks` or :class:`AttributionSpan`.
        band: The :class:`StatusBand` the items are drawn from.
        expenses_only: ``True`` keeps the expense rows and the sides money
            LEAVES by (:func:`~app.services.transfer_legs.expense_legs`): a
            bill, or money spent.
    """

    cash_flow: CashFlowSet
    scenario_id: int
    window: Paychecks | AttributionSpan
    band: StatusBand
    expenses_only: bool


def load_plan_items(
    scope: PlanScope, row_loads: tuple[LoaderOption, ...], *,
    side_records: bool = False,
) -> list[PlanItem]:
    """Load the plan items *scope* selects: bill rows, then transfer sides.

    **The ONE loader of a screen's plan items** (plan step **plan#2130**).
    Each part of the scope becomes a clause on both parent tables here --
    the scenario, the window, live (not deleted) and the status band through
    :func:`_scoped`; the set through :func:`own_rows_clause` and
    :func:`touched_transfers_clause`; expenses only as the rows'
    ``transaction_type_id`` and the sides' :func:`~app.services.transfer_legs
    .expense_legs` -- so a screen's rows and its transfers cannot disagree on
    scope.  Were every bill a transfer, the row query would go, and with it
    each screen's *row_loads*, the one argument a screen passes that is a
    row's alone; no screen's :class:`PlanScope` would change.

    **The list is in one order, a function of the data**: rows by id, then
    the sides in transfer id then side order -- exactly
    :func:`~app.services.transfer_legs.key_order` (finding **plan#2186**).
    The row query carried no ``ORDER BY`` before, so a grid cell holding two
    rows drew them in whatever order the planner returned, which two copies of
    one state showed reversed (the X-bi-6-5 kit, 2026-10-29).

    One statement for the rows and one for the transfers, each with what its
    loader options select in (a transfer's pricing chain reads its period);
    none at all for a window that :attr:`~Paychecks.selects_nothing`; one
    more for *side_records*.

    Args:
        scope: The screen's :class:`PlanScope`.
        row_loads: The loader options for what the screen's readers read off
            a ROW (each screen keeps its own).  A transfer carries
            :func:`~app.utils.amount_relationships
            .transfer_pricing_load_options` for every screen: the chain a
            side's price walks, and the period a bill reads; its endpoints,
            status and category are ``lazy="joined"`` on the model.
        side_records: ``True`` for a screen whose readers read a side's
            RECORD (the Spending report's timeliness): every side's record is
            pre-loaded in one statement
            (:func:`~app.services.transfer_legs.preload_side_records`, ruling
            **plan#2136**: called by a reader that reads records, and by no
            other).

    Returns:
        The rows, then the sides: one list of
        :data:`~app.services.transfer_legs.PlanItem`.
    """
    if scope.window.selects_nothing:
        return []
    rows = _scoped(scope, Transaction).options(*row_loads).filter(
        own_rows_clause(scope.cash_flow),
    )
    if scope.expenses_only:
        rows = rows.filter(
            Transaction.transaction_type_id
            == ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        )
    transfers = (
        _scoped(scope, Transfer)
        .options(*transfer_pricing_load_options())
        .filter(touched_transfers_clause(scope.cash_flow))
    )
    loaded_rows = rows.order_by(Transaction.id).all()
    sides = set_transfer_legs(
        scope.cash_flow, transfers.order_by(Transfer.id).all(),
    )
    if scope.expenses_only:
        sides = expense_legs(sides)
    if side_records:
        preload_side_records(side.transfer for side in sides)
    return [*loaded_rows, *sides]


def _scoped(scope: PlanScope, model: type[_Parent]) -> Query[_Parent]:
    """Return the query of *model*'s live rows in *scope*'s scenario, window and band.

    The parts of a scope both tables state alike, stated once; the set and
    expenses only differ by table and :func:`load_plan_items` adds them.

    Args:
        scope: The screen's :class:`PlanScope`.
        model: ``Transaction`` or ``Transfer``.

    Returns:
        A query rooted at *model* -- built, not executed.
    """
    return scope.window.narrow(db.session.query(model), model).filter(
        model.scenario_id == scope.scenario_id,
        model.is_deleted.is_(False),
        *scope.band.clauses(model),
    )
