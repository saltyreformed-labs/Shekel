"""
Shekel Budget App -- Shared Transaction-Cell Render Helper

The cross-blueprint home for rendering a transaction's grid cell.
Every HTMX response that re-renders a cell -- the transaction CRUD and
status routes, and the entries CRUD routes' out-of-band cell refresh --
must ship the same context (notably ``entry_sums`` and ``budgets``, which drive
the amount display and the envelope progress), so the render has exactly one
definition with a public name instead of a module-private helper imported across
blueprint packages.  Follows the package-level shared-helper convention
of ``app/routes/_commit_helpers.py``.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, NamedTuple
from urllib.parse import parse_qsl, urlsplit

from flask import render_template, request
from flask_login import current_user
from werkzeug.datastructures import MultiDict

from app.enums import SettledDayBasisEnum
from app.exceptions import NotFoundError
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.cash_ledger import (
    derived_amount_basis,
    amounts_by_id,
    leg_amounts_by_key,
    resolve_transfer_amount,
    settled_amounts_by_key,
)
from app.services.account_resolver import (
    resolve_owner_and_view,
    resolve_owner_cash_flow_set,
)
from app.services.cash_flow_set import CashFlowSet
from app.services.entry_service import build_entry_sums_dict
from app.services.settle_day import SettleDay, is_evidence, recorded_settle_day
from app.services import grid_view_service
from app.services.grid_view_service import due_captions_by_key
from app.services.transaction_service import (
    leg_retained_amounts_by_key,
    retained_settle_amounts_by_id,
)
from app.services.transfer_legs import (
    TransferLeg,
    grid_transfer_leg,
    leg_of,
)
from app.utils.dates import display_today
from app.utils.digit_strings import integer_arg


@dataclass(frozen=True)
class RenderAmounts:
    """The THREE amount maps every surface showing a row's figure must publish.

    A row is a PLAN until its money moves and a RECORD of what moved once it has
    (plan step **X-au-c3**), and a screen shows the record where there is one and
    the plan otherwise.  All three are needed to render one cell, so they are
    resolved by ONE call and travel together -- a caller cannot take the budget
    and forget the settlement.

    **That pairing is a defect fixed rather than a convenience.**  An adversarial
    review of plan step X-au-c2b found the display rule written twice and
    differently, because each surface assembled its own context: the grid laid
    the seam's override map over the resolved one while every HTMX fragment
    published the resolved map ALONE under the same key, so one row showed two
    figures on two surfaces the same click opened.  Two independently-passed maps
    are the same shape of mistake one map further on.

    **The third map is the developer's 2026-08-17 rule that whatever will be
    booked against the account is shown wherever the row is shown.**  A row
    reverted out of the settled band KEEPS what it recorded and a re-settle
    honours it, so its plan is what the balance counts and its retained figure is
    what a tick books.  Those are two different numbers about one row, and the
    second was visible on no surface but the reconcile panel -- the grid and the
    full-edit popover both showed a ``$500.00`` plan for a row that would book
    ``$245.32``.  It rides in this dataclass for the same reason the second one
    does: so no surface can publish two of the three.

    Attributes:
        budgets: ``{transaction_id: what the row's amount IS}`` --
            :func:`~app.services.cash_ledger.amounts_by_id`.
        settled: ``{transaction_id: what its money DID}``, ``None`` per row that
            has not settled --
            :func:`~app.services.cash_ledger.settled_amounts_by_key`, the SAME
            map the grid counts from.  It read a TOTAL twin
            (``recorded_amounts_by_id``) through ``X-bi-4a``, whose one
            difference was ``None`` for a settled row that RECORDED NOTHING --
            a legacy shape (finding **N-181**) this fragment's Actual box
            existed to repair, and one the counting map refused; a settled
            row's record is the sum of its entries since plan step
            ``balance:X-bi-4b-1`` (ruling **R-BAL80**), so such a row is the
            ``$0.00`` record (ruling **R-BAL82**), the box shows ``0.00``, and
            typing the real figure is still the repair.
        retained: ``{transaction_id: what a tick WOULD book}``, ``None`` per row
            where that is already on screen --
            :func:`~app.services.transaction_service.retained_settle_amounts_by_id`.
    """

    budgets: dict[int, Decimal]
    settled: "dict[int, Decimal | None]"
    retained: "dict[int, Decimal | None]"


def fragment_amounts(txn: Transaction) -> RenderAmounts:
    """Return all three amount maps for ONE fragment's row.

    The single-row door onto the amount model (plan step X-au-c2b), for the HTMX
    fragments that re-render one cell or one card.  Every template that shows a
    row's amount reads this map rather than ``txn.estimated_amount``, because
    under the amount model a derived row stores nothing in that column and the
    cell would render an empty string where a figure belongs.

    **It answers by the same rule the pages do** --
    :func:`~app.services.cash_ledger.amounts_by_id`, the resolved amount
    superseded by a live recompute.  An adversarial review found that rule
    written twice and differently: the grid merged the seam's override map over
    its resolved one while every fragment published the resolved map ALONE under
    the same context key, so a drifted salary row showed its live net on the
    grid and its stale column in the quick-edit box the same click opened -- and
    that box is what a save posts back from.

    **It takes ONE row, and the signature is the guard.**  It took ``*rows`` and
    pinned the basis off ``rows[0]``, which needed a paragraph about cross-owner
    sets to be safe; every fragment renders exactly one row, so taking one row
    deletes the question rather than documenting it.  A batch surface takes the
    READ PASS's basis instead
    (:meth:`~app.services.balance_at.BalanceContext.amounts`).

    **The SETTLEMENT and RETAINED maps join it at plan step X-au-c3**, and it
    is the same argument one step on: a row that has settled shows what it
    RECORDED, and a row that was reverted shows what a re-settle will book, so a
    surface publishing the budget alone would show the plan for a row whose
    money has already moved or is about to move at a different figure.  One call
    answers all three so no surface can take one and forget the others.

    Args:
        txn: The row the fragment renders.

    Returns:
        A :class:`RenderAmounts` whose three maps hold one entry each, keyed
        for the templates and the entry builders, which all index a map.
    """
    # **A SETTLED salary row now costs a projection here, and it did not
    # before** (plan step balance:X-au-d, named by that step's adversarial
    # review rather than found later).  Such a row used to resolve through
    # amount rule 1 -- a column read -- and the read-time repair returned early
    # on ``is_projected``; it DECLARES its definition now, so rendering one
    # fragment runs ``paycheck_calculator.project_salary`` over the owner's
    # whole pay-period set.  Measured on the 2026-09-02 production clone: 8
    # statements, and one basis, for one row.
    #
    # It is not threaded from a read pass because a FRAGMENT has none: this is
    # the one-row render path, reached by an HTMX swap that loaded nothing else.
    # A pass-shaped caller (the grid, the fold, the reconcile panel) already
    # builds one basis for everything it loaded, which is what findings N-228 /
    # N-268 / N-309 are about; this is the shape those findings do not cover
    # and the cost is one derivation rather than one per row.
    basis = derived_amount_basis(txn.account.user_id, txn.scenario_id)
    return RenderAmounts(
        budgets=amounts_by_id([txn], basis),
        settled=settled_amounts_by_key([txn]),
        retained=retained_settle_amounts_by_id([txn]),
    )


def transfer_budgets(xfer: Transfer) -> "dict[int, Decimal]":
    """Return ``{transfer_id: what this transfer's amount IS}``, one entry.

    The transfer twin of :attr:`RenderAmounts.budgets`, and the ONE producer
    every surface showing a transfer's figure reads (plan step **X-au-f**).
    The three transfer fragments -- the cell, the quick edit and the full-edit
    popover -- took ``xfer.amount`` straight off the parent until this step,
    which was correct exactly while a generated transfer STORED its figure.
    That column empties for a generated transfer at this step's cutover leaf,
    at which point a template reading it renders Jinja's ``None`` into a money
    box and posts the literal string ``"None"`` back through
    ``TransferUpdateSchema`` (which refuses it).  Asking the amount model
    instead is the same move ``routes/transactions/forms`` already made for the
    transaction popover.

    **It is a MAP rather than a scalar for the reason
    :func:`fragment_amounts` states**: two of the three fragments POST this
    figure back into a money box, and a missing scalar renders ``value=""`` in
    silence where an absent map raises.  **What it does NOT buy is protection
    from the WRONG map**, and an adversarial review is why that is written
    down: ``budgets`` is one context key over two id spaces that are not
    disjoint, so a transaction-keyed map handed to a transfer fragment answers
    a HIT rather than a ``KeyError``.  No path does that today -- the one route
    that could, ``transactions/forms.get_full_edit``, has rendered no transfer
    form since leaf ``X-bi-6-1`` gave a transfer's popover the transfer's own
    door -- and the guarantee is that a MISSING map is loud, not that a
    mismatched one is.

    **It is SEPARATE from :func:`transfer_settlement_amounts` rather than a
    third field on it, and the split is by what each surface renders.**  The
    quick edit shows a PLAN and nothing else, while that producer answers
    the two settlement figures -- so bundling them would hand the quick-edit
    swap two figures it does not display.  Until plan step
    ``balance:X-bi-6-4d-2`` that producer also took the legs' records (its
    caller's ``covering_movements_by_leg`` load), a load the quick edit would
    have paid for too; both figures are the transfer's since, and it takes
    none.
    The popover shows all three and calls both; so does the transfers page's
    CELL since leaf ``X-bi-6-1`` closed finding **N-303** (it showed the plan
    over a figure just corrected, because it was handed neither map).  *A
    first draft of this paragraph cited finding N-296 for that load's cost and an
    adversarial review opened the row: N-296 is a per-DEFINITION eager load in
    BATCH callers, whose remedy is `pricing_load_options` and whose step is
    `X-bm`.  It says nothing about this.  The argument above needs no
    citation.*

    **SINGLE-ROW reads only.**  Plan step X-au-f-2 gave a loan payment's PARENT
    its producer (ruling **R-BAL10**), so this now builds a read pass and one
    call per row is what an N+1 would look like.  A transfer that owns its
    figure or reads its definition's series still costs no query -- the basis
    resolves nothing until a rule asks it -- and only a DERIVE-mode loan
    payment reaches the loan.  The one batch surface -- the grid, which since leaf ``X-bi-6-1``
    draws a transfer's LEGS off the parent -- prices them through
    :func:`~app.services.cash_ledger.leg_amounts_by_key` with the page's
    own basis (``routes/grid/_items.build_amount_maps``), the same resolver
    this calls; every parent-transfer render site here is a one-row HTMX
    swap.

    **The basis is BUILT here rather than threaded, exactly as the transaction
    twin builds one** (:func:`fragment_amounts`, whose comment carries the
    argument): a FRAGMENT has no read pass to take one from -- it is reached by
    an HTMX swap that loaded nothing else -- and a pass-shaped caller builds
    one basis for everything it loaded instead.  It is pinned off the
    transfer's OWN columns, which is what makes it right for the row being
    priced whichever site called.

    Args:
        xfer: The transfer the fragment renders.  It states no ownership rule of
            its own and needs none: it reads ``user_id`` and ``scenario_id``
            only to pin the basis to the transfer's own columns and, on the
            OWN arm, issues no query at all, so there is nothing here to
            scope.  Every caller is a transfers-blueprint render (the quick
            edit, the full-edit popover, :func:`render_transfer_cell`) handed
            a transfer its door loaded for the owner (``_get_owned_transfer``,
            the ad-hoc create's own owner, or its dedupe query's
            ``user_id``).  The one caller that established ownership
            TRANSITIVELY, ``routes/transactions/forms.get_full_edit`` through
            the transfer's shadow, rendered the transfer form until leaf
            ``X-bi-6-1``; an adversarial review had measured that difference.

    Returns:
        ``{xfer.id: Decimal}``, holding exactly one entry.

    Raises:
        AmountUnresolvable: From the resolver, for a transfer whose rule cannot
            answer.  A refusal is never a fallback (see
            :mod:`app.services.cash_ledger._amount_source`).
    """
    return {
        xfer.id: resolve_transfer_amount(
            xfer, derived_amount_basis(xfer.user_id, xfer.scenario_id),
        ),
    }


@dataclass(frozen=True)
class TransferSettlementAmounts:
    """The TWO settlement maps the transfer full-edit popover must publish.

    The transfer half of :class:`RenderAmounts`, and deliberately only two of
    its three: the PLAN is :func:`transfer_budgets`, a producer of its own
    because the fragment that shows a plan and no record (the quick edit)
    must not pay for a record it does not render.  What a settled transfer's
    money moved is ONE figure on the transfer itself (plan step
    ``balance:X-bi-6-4d-2``: :attr:`~app.models.transfer.Transfer.settled_figure`),
    and both maps below read it through a leg, with the producers the grid
    prices a leg's cell by (:func:`transfer_settlement_amounts`).

    **Both are keyed by the TRANSFER's id, not the leg's ``cell_key``.**  The
    template's subject is the transfer, and a map keyed by something the
    template does not have would be a second lookup for it to get wrong.  The
    re-key is safe because both legs read the transfer's one figure.

    **They are maps rather than scalars for the reason ``fragment_amounts``
    states**: a missing scalar renders ``value=""`` in silence while a missing
    map raises, and this form is a surface where an empty figure would be POSTED
    BACK as the transfer's settled figure.

    Attributes:
        settled: ``{transfer_id: what the transfer's money DID}``, ``None``
            when the transfer has not settled.
        retained: ``{transfer_id: what a re-settle WOULD re-book}``, ``None``
            when that is already on screen.
    """

    settled: "dict[int, Decimal | None]"
    retained: "dict[int, Decimal | None]"


def transfer_settlement_amounts(
    xfer: Transfer, user_id: int,
) -> TransferSettlementAmounts:
    """Return the pair's recorded and retained figures, keyed by transfer id.

    The transfer twin of :func:`fragment_amounts`, for the two surfaces that
    need it: the full-edit popover, which since plan step X-au-c3 carries an
    Actual box (what the bank took, prefilled from the transfer's settled
    figure) and the re-book notice (what a re-settle would honour), and the
    transfers page's cell (:func:`render_transfer_cell`).  Both call this
    rather than assembling it -- the popover, opened from the transfers page
    or from a grid leg's cell, and that page's cell -- because a rule
    written at each is how one click shows a different figure from another.

    **It asks the producers the grid prices a leg's cell by** (leaf
    ``X-bi-6-1``, ruling **R-BAL87**; rule 14):
    :func:`~app.services.cash_ledger.settled_amounts_by_key` and
    :func:`~app.services.transaction_service.leg_retained_amounts_by_key` over
    ONE leg built by :func:`~app.services.transfer_legs.leg_of`, so what
    this popover promises, what the cell beside it shows and what a tick
    books are one walk.  Until that leaf it loaded
    the shadow pair and read the expense SHADOW's entries through the row
    producers -- a second walk over the same record that agreed with the
    cell's only while Transfer Invariant 3 held, which an adversarial review
    named.  The settled producer reads the transfer's figure (a leg is
    valued by its parent, ``row_valuation.settled_figure``, ruling
    **plan#1983**) and the retained one the seam's
    own reads (``recorded_leg_settlement``, ``honoured_figure``): since plan
    step ``balance:X-bi-6-4d-2`` either leg reads the transfer's ONE settled
    figure.

    **The EXPENSE leg answers**, which is the leg
    ``transfer_service._settle.settle`` resolves its figures from and the leg
    the correction door compares a typed figure against
    (``transfer_service._update``).  Either would answer the same -- both
    read the transfer's one figure -- and naming one means the
    choice is not made twice.  (A DAY is per side since plan step
    ``balance:X-bi-6-4c-3``; :func:`transfer_side_boxes` draws each.)

    **It takes no records** (plan step ``balance:X-bi-6-4d-2``): neither
    producer reads a leg's record -- both figures are the transfer's -- so
    the leg built here carries none, and neither caller -- the popover nor
    the transfers page's cell (:func:`render_transfer_cell`) -- loads the
    records for it.  The popover's own ONE load of them (ledger row
    **BAL-530**, ``routes/transfers/forms.render_full_edit``) is read by its
    day boxes and its withdrawal caption alone.

    **It answers for a SOFT-DELETED transfer**, where the pair loader it used
    to call refused one: the transfers page's cell reaches here on the stale
    response after a rival's delete won (``_stale_transfer_response``), and
    nothing here refuses one.  The popover door refuses a deleted transfer
    itself (``transfers.get_full_edit``).

    Args:
        xfer: The transfer the popover is rendering.
        user_id: The owner, a defense-in-depth ownership check the loader used
            to make and this keeps: a transfer that is not *user_id*'s is
            refused as not found.

    Returns:
        A :class:`TransferSettlementAmounts` whose two maps hold one entry each.

    Raises:
        NotFoundError: If *xfer* is not *user_id*'s.
    """
    if xfer.user_id != user_id:
        raise NotFoundError(f"Transfer {xfer.id} not found.")
    leg = leg_of(xfer, xfer.from_account_id)
    return TransferSettlementAmounts(
        settled={xfer.id: settled_amounts_by_key([leg])[leg.cell_key]},
        retained={xfer.id: leg_retained_amounts_by_key([leg])[leg.cell_key]},
    )


def settles_at_nothing(budget: Decimal, retained: "Decimal | None") -> bool:
    """Return whether a settle stating no figure would record ``$0.00``.

    Read off the two maps the bill popover already publishes, by the
    contract :class:`RenderAmounts` states: ``retained`` is what a re-settle
    would RE-BOOK, ``None`` where the plan on screen is what it books -- the
    two arms of the settle verb's figure, a retained correction outranking
    the resolved plan (``transaction_service.settle_amount``).  For a row
    that settles from its PURCHASES neither map is what it books; the popover
    asks that arm first (``amount_correctable``).

    **Why the popover asks it** (plan step ``credit_card:CC-5-4a-5``): a
    ``$0.00`` record moves nothing, so the status seam takes the kept
    payment off the books and withdraws every match naming it.  Paid on such
    a reverted row -- an estimate set to ``$0.00`` over a kept payment --
    withdraws exactly as a ``$0.00`` typed in Actual does, and says so first
    by the same ruling (**R-CC56**); without the caption, a page that named
    no line would be refused at every press (ruling **R-CC127**).  The
    transfer popover does not ask: a stated transfer price is positive by its
    CHECKs, and a derived loan payment is ``$0.00`` only where its level P&I,
    priced from the loan's original principal, rounds to zero with no escrow
    or extra (``$1.00`` at 0% over 360 months).  On such a loan whose kept
    payment was booked above zero and matched, the popover's Paid is refused
    as out of date and the grid leg's one-click withdraws silently under
    **R-CC56**.

    Args:
        budget: The row's PLAN -- ``budgets[id]``.
        retained: ``retained[id]``.

    Returns:
        ``True`` when the figure such a settle books is zero.
    """
    return (budget if retained is None else retained) == 0


#: How an EVIDENCED side's day is known, in the popover's words (ruling
#: **R-BAL142**'s own wording: the bank showed it, you reconciled it, you typed
#: it).  A ``borrowed`` side has no entry: its caption names what it borrows.
_KNOWN_AS = {
    SettledDayBasisEnum.OBSERVED: "bank-shown",
    SettledDayBasisEnum.ASSERTED: "reconciled",
    SettledDayBasisEnum.ENTERED: "you typed it",
}


@dataclass(frozen=True)
class TransferSideBox:
    """One side's date box on the transfer popover (ruling **R-BAL108**).

    Attributes:
        field: The form field the box posts (``TransferUpdateSchema``):
            ``settled_on_from`` for the side the money leaves,
            ``settled_on_to`` for the side it arrives at.
        account_id: The account the side is on.
        account_name: Its display name.
        prefill: What the box is prefilled with and what the PATCH grades its
            submission against: the side's OWN day when it has evidence, else
            ``None`` -- a borrowed side's box renders EMPTY (ruling
            **R-BAL164**), so any day typed there, the borrowed one included,
            is that side's own.
        caption: How the side's day is known, in plain words, or ``None`` for
            a side holding no day (a ``$0.00`` close records no movement, so
            nothing moved on any day).
    """

    field: str
    account_id: int
    account_name: str
    prefill: SettleDay | None
    caption: "str | None"


def transfer_side_boxes(
    xfer: Transfer, records: dict,
) -> "tuple[TransferSideBox, TransferSideBox]":
    """Return the popover's two date boxes, from-side then to-side.

    Each side's day is read off its leg's RECORD -- the payment record the
    status seam's Transfer arm writes for that side (plan step
    ``balance:X-bi-6-4d-2``) -- in the caller's ONE load (ledger row
    **BAL-530**).  The ONE producer both the popover's render
    and the PATCH's grading read, so what a box was prefilled with and what
    its submission is graded against cannot part.

    Args:
        xfer: The transfer.
        records: ``covering_movements_by_leg([xfer.id])``.

    Returns:
        The two boxes.
    """
    days = {}
    for account_id in (xfer.from_account_id, xfer.to_account_id):
        record = records.get((xfer.id, account_id))
        days[account_id] = (
            None if record is None else recorded_settle_day(record)
        )
    boxes = []
    for field, account, other in (
        ("settled_on_from", xfer.from_account, xfer.to_account),
        ("settled_on_to", xfer.to_account, xfer.from_account),
    ):
        day, other_day = days[account.id], days[other.id]
        if day is None:
            prefill, caption = None, None
        elif is_evidence(day):
            prefill, caption = day, _KNOWN_AS[day.basis]
        elif other_day is not None and is_evidence(other_day):
            prefill = None
            caption = f"same as {other.name}, {day.day:%m-%d} (a guess)"
        else:
            prefill, caption = None, f"{day.day:%m-%d} (a guess)"
        boxes.append(TransferSideBox(
            field=field, account_id=account.id, account_name=account.name,
            prefill=prefill, caption=caption,
        ))
    return boxes[0], boxes[1]


def _page_account_override() -> int | None:
    """Return the ``account_id`` of the PAGE this fragment will be swapped into.

    htmx sends the browser's current URL with every request it makes
    (``HX-Current-URL``, on ``htmx.ajax`` and on the popover forms it
    ``process``es too), and the grid's balance line is a function of that
    URL: ``/grid?account_id=<card>`` puts the card on the line.  A fragment
    that re-draws one cell of that page must read the same override the page
    read, so it takes it from the header the way the page takes it from
    ``request.args`` -- through the same werkzeug ``type=`` coercion,
    :func:`~app.utils.digit_strings.integer_arg`, so the two cannot read one
    value two ways -- and answers ``None`` when the header is absent, carries
    no ``account_id``, or carries one that is not an integer a PostgreSQL
    ``integer`` can hold.  A request with no such header (a non-htmx caller, a test
    client) therefore reads the primary, as every fragment resolved before
    plan step ``credit_card:CC-4-2``.

    **The header is the requester's own and it moves NO write**: it feeds
    :func:`~app.services.account_resolver.resolve_cash_flow_set`, whose
    admission test refuses any account that is not the requester's, and a
    refused, foreign, junk or absent value all render the same primary --
    there is no oracle in it.  A malformed URL (``urlsplit`` raises on a bad
    netloc) reads ``None`` for the same reason a junk id does: this runs
    AFTER the commit, and a render must not turn a persisted write into a
    500.

    **What makes reading ``account_id`` off ANY host page safe is a census,
    and the census is this paragraph** (the re-review of CC-4-2 asked for
    it; re-taken at CC-4-3): the fragments that reach
    :func:`fragment_balance_line` are issued only from ``/grid`` (where
    ``?account_id=`` IS the balance line) and from ``/companion/*`` (no
    ``account_id``, and a companion is answered ``None`` before the header is
    read).  ``/analytics/calendar`` reads an ``account_id`` with the SAME
    meaning since plan step CC-4-3 -- the balance line within the set -- and
    issues no such fragment; the dashboard reads the set with no
    ``account_id`` of its own and issues no cell fragment (its two fragments,
    the anchor form and the pulse section, carry no id on the PAGE's URL;
    the anchor form's rides on the FRAGMENT's);
    every ``accounts.*`` page carries its id as a PATH parameter, so its
    query is empty.  A page that later gains both a cell fragment and an
    ``account_id`` with another meaning must either name the balance line's
    meaning for that parameter or scope this read to the grid's path; it is
    a deliberate decision, not a free ride.

    Returns:
        The page's ``account_id`` query value, or ``None``.
    """
    current_url = request.headers.get("HX-Current-URL")
    if not current_url:
        return None
    try:
        query = urlsplit(current_url).query
    except ValueError:
        return None
    return MultiDict(parse_qsl(query)).get("account_id", type=integer_arg)


def fragment_balance_line(owner_id: int) -> Account | None:
    """Return the BALANCE LINE a one-row fragment draws its row against.

    The account whose balance the PAGE renders (ruling **R-CC16**, plan step
    ``credit_card:CC-4-1``): the owner's cash-flow set's balance, resolved by
    the ONE call the grid page makes,
    :func:`~app.services.account_resolver.resolve_cash_flow_set`, with the
    page's own ``?account_id=`` override read off htmx's ``HX-Current-URL``
    (:func:`_page_account_override`).  Stated ONCE for the three fragment
    renders that need it -- the transfer cell's direction arrow, the
    transaction cell's account chip and the mobile card's (plan step
    ``credit_card:CC-4-2``) -- so a re-drawn cell cannot name a different
    balance line from the page around it.  A row on another account than this
    one carries the chip; a transfer with this account at one end draws its
    arrow.

    **It was ``resolve_grid_account`` with NO override until CC-4-2's
    adversarial review** (the transfer cell's spelling since plan step
    X-au-f-1): two spellings of one value that agreed only while the page
    carried no override, so on the card's balance line every click re-drew
    its cell from checking's side -- the chip appearing on the card's own row
    and vanishing from checking's -- until a reload.  Developer ruling
    2026-09-18 (CC-4-2's review, M1): read the page URL, one producer.

    **It is ``None`` for anyone but the row's OWNER**, by the owner's id
    rather than by what the requester happens to hold.  A companion reads the
    owner's plan through :func:`~app.services.companion_service` with no
    balance line at all -- their page renders no balance and no chip -- and
    the fragment a companion's Mark Paid swaps in must say the same.  Today a
    companion also owns no account, so the resolver would answer ``None`` for
    them anyway; the explicit test is what makes that the RULE rather than a
    property of the current population.

    **It is the balance-line half of :func:`fragment_cash_flow`** since plan
    step ``credit_card:CC-5-2``; a fragment that also draws an envelope's
    add-purchase picker reads both halves from that one walk.

    Args:
        owner_id: ``auth.users.id`` of the row or transfer being rendered.

    Returns:
        The balance line of the page the fragment lands on when the requester
        IS the owner, else ``None`` (which every template reads as "no
        balance line").  ``None`` too for an owner with no grid-eligible
        account, as the page's own context is.
    """
    return fragment_cash_flow(owner_id).balance_line


class FragmentCashFlow(NamedTuple):
    """What a one-row fragment resolves about the owner's cash-flow set, once.

    Attributes:
        purchases: The OWNER's set with no override -- the accounts a purchase
            under the row may name
            (:func:`~app.services.cash_flow_set.purchase_accounts`, ruling
            **R-CC37**) -- or ``None`` for an owner with no grid-eligible
            account.  Resolved for the row's owner whoever the requester is: a
            companion records the owner's purchase (ruling **R-CC11**).
        balance_line: :func:`fragment_balance_line`'s answer -- the page's
            balance line for the owner, ``None`` for anyone else.
    """

    purchases: CashFlowSet | None
    balance_line: Account | None


def fragment_cash_flow(owner_id: int) -> FragmentCashFlow:
    """Resolve a one-row fragment's two cash-flow answers from ONE walk.

    The mobile card draws an account chip (against the page's balance line)
    AND an add-purchase picker (from the owner's set), and the HTMX entry
    list draws the picker alone; each question is one resolve of the same
    set, so a fragment that asked them apart paid the primary chain and the
    cards query twice (the review of plan step ``credit_card:CC-5-2``).  For
    the OWNER both come off
    :func:`~app.services.account_resolver.resolve_owner_and_view` with the
    page's override; for anyone else the balance line is ``None`` by
    :func:`fragment_balance_line`'s rule and the picker is the owner's set by
    :func:`~app.services.account_resolver.resolve_owner_cash_flow_set`.

    Args:
        owner_id: ``auth.users.id`` of the row being rendered.

    Returns:
        The :class:`FragmentCashFlow` for this request.
    """
    if current_user.id != owner_id:
        return FragmentCashFlow(
            purchases=resolve_owner_cash_flow_set(owner_id), balance_line=None,
        )
    sets = resolve_owner_and_view(
        current_user.id, current_user.settings, _page_account_override(),
    )
    return FragmentCashFlow(
        purchases=sets.owner,
        balance_line=sets.view.balance if sets.view is not None else None,
    )


def render_transfer_cell(xfer: Transfer, **extra: Any) -> str:
    """Render a parent transfer's grid cell with the context it must carry.

    **The transfer twin of :func:`render_transaction_cell`, and this module's
    own opening paragraph is what it satisfies**: every HTMX response that
    re-renders a cell must ship the same context, *so the render has exactly one
    definition with a public name*.  The transaction cell had that; the transfer
    cell had SIX hand-assembled ``render_template`` calls across three modules
    and two blueprints, each repeating ``xfer``, a ``resolve_grid_account``
    lookup, and -- once plan step **X-au-f-1** made the figure a resolved one --
    a ``budgets`` map.

    **It was extracted at the moment the required context GREW, which is the
    only moment the omission is cheap.**  A seventh site added later without
    ``budgets`` is not a blank cell: Jinja raises on the subscript, so it is a
    500 on a live money surface, and on five of the six sites that surface is
    already an ERROR path -- a 409 conflict fragment or a designed 4xx -- where a
    500 replaces the message the user needed.  Naming the render once removes
    the way to forget.

    The ACCOUNT is resolved here rather than passed, because all six callers
    resolved the same thing from the same two arguments; it decides which
    direction arrow the cell draws.  Since plan step ``credit_card:CC-4-2`` the
    resolution is :func:`fragment_balance_line`'s -- the page's own, override
    included -- shared with the transaction cell and the mobile card, so the
    three fragments cannot name the balance line three ways, nor one way the
    page does not.

    **It publishes what the pair RECORDED and what a re-settle would RE-BOOK
    beside the plan** (leaf ``X-bi-6-1``, closing finding **N-303**): the
    cell was handed ``budgets`` alone and so painted the plan over a figure
    the same click's popover had just corrected -- the "one row, two figures
    on two surfaces" shape :class:`RenderAmounts` exists to prevent.  The
    two maps come from :func:`transfer_settlement_amounts`, the popover's own
    producer, so the cell and the popover cannot disagree about the pair.

    Args:
        xfer: The transfer to render.  Owner-established by the caller -- see
            :func:`transfer_budgets`, which states what that does and does not
            guarantee.
        **extra: Forwarded to ``render_template`` -- ``wrap_div=True``,
            ``conflict=True``, ``error=<message>``, the flags each caller adds
            on top of the shared context.

    Returns:
        Rendered HTML string.

    Raises:
        AmountUnresolvable: From :func:`transfer_budgets`, for a transfer whose
            rule cannot answer.  Unreachable while every transfer owns its
            figure; the leaves after this one are what give it a population.
    """
    amounts = transfer_settlement_amounts(xfer, current_user.id)
    return render_template(
        "transfers/_transfer_cell.html",
        xfer=xfer,
        account=fragment_balance_line(xfer.user_id),
        budgets=transfer_budgets(xfer),
        settled=amounts.settled,
        retained=amounts.retained,
        **extra,
    )


def render_transaction_cell(txn: Transaction, **extra: Any) -> str:
    """Render the transaction cell template with its amount and entry context.

    Wraps render_template so every HTMX cell response includes the three
    amount maps the display reads (:class:`RenderAmounts`), the ``entry_sums``
    dict the progress indicator on tracked transactions needs, and the PAYDAY
    the due-date caption is compared against.

    **The caption is DECIDED by the route and drawn by the template**
    (pay-calendar plan step C4-a-1).  The partial computed it itself, as
    ``t.due_date != t.pay_period.start_date`` -- a lazy relationship load
    issued from inside the render, once per distinct paycheck on a page
    drawing N cells, and a template cannot be given a query budget.  Both
    surfaces that draw this cell now call the same producer
    (:func:`~app.services.grid_view_service.due_captions_by_key`), so a row
    cannot caption one way on the grid and another in the fragment the same
    click swaps in -- the rule :class:`RenderAmounts` above states for the
    three amount maps, applied to the fourth thing a cell draws.

    **The payday it needs is now this function's OWN load, and the count did
    not change** (plan step ``pay_calendar:C13-b``).  This paragraph used to
    say the read cost nothing here because every caller had already proved
    ownership through ``txn.pay_period.user_id``
    (``transactions/_helpers._get_owned_transaction`` and its siblings), so
    the relationship was loaded before this ran.  Those doors read
    ``txn.user_id`` now and hydrate nothing, so the reason is gone -- and the
    load is not: it MOVED here, as a lazy load, and that is the honest
    statement rather than either "still free" or "one statement worse".

    **Re-measured 2026-09-03 on ``GET /transactions/<id>/cell``, both
    spellings of the ownership door, same request:** 12 statements and 1
    ``PayPeriod`` hydration EITHER SIDE.  The only difference is WHERE the
    ``budget.pay_periods`` SELECT sits in the sequence -- fifth (the ownership
    walk) before, sixth (this due caption) after.  The 2026-08-27 measurement
    this paragraph quoted said 12 as well, so the number is unchanged and its
    reason is not.

    Deriving the owner's calendar here instead was built and rejected on the
    2026-08-27 measurement -- it ADDED two statements on top of the load the
    ownership check had already paid for, and bought only a re-check of an
    ownership fact the route had just established.  **The arithmetic behind
    that rejection has shifted and the conclusion has not**: it is now one
    lazy load against a two-query derivation, so the derivation is still the
    more expensive of the two.  ``start_date`` is the one column
    ``budget.pay_periods`` actually stores, carried through
    :func:`~app.services.pay_calendar.derive_periods` untouched, so it is the
    same date the grid's derived window publishes.

    **The BALANCE LINE is the fifth thing the cell draws against** (plan step
    ``credit_card:CC-4-2``, ruling **R-CC16**): a row on another account
    than the balance line's carries an account chip, and since plan step
    ``credit_card:CC-5-2`` so does a row whose MOVEMENTS are -- the
    ``account_chips`` macro decides off ``t.account_id`` and each of
    ``t.entries``' against the ``account`` published here, the way the grid
    page's row macro publishes its own.  Resolved through
    :func:`fragment_balance_line`, the same call the transfer cell's arrow
    reads, and PUBLISHED even when it resolves ``None``: the partial reads it
    with ``is not none``, so a surface that forgets the key raises where a
    ``None`` draws no chip.  The chips' labels cost this fragment nothing
    beyond what it already paid: ``Transaction.account`` is
    ``lazy="joined"`` on the model, the family is loaded by
    :func:`fragment_amounts`'s settled-record read (17 statements for a
    bill's cell and 18 for an envelope's, one of them the family, on the
    tree before CC-5-2 and after it), and a movement's account is an
    identity-map hit for a member of the owner's set.

    Args:
        txn: The Transaction object to render.
        **extra: Additional keyword arguments forwarded to
            render_template (e.g. ``wrap_div=True``, ``wrap_oob=True``,
            ``conflict=True``).

    Returns:
        Rendered HTML string.
    """
    amounts = fragment_amounts(txn)
    return render_template(
        "grid/_transaction_cell.html",
        txn=txn,
        account=fragment_balance_line(txn.user_id),
        budgets=amounts.budgets,
        settled=amounts.settled,
        retained=amounts.retained,
        entry_sums=build_entry_sums_dict([txn], amounts.budgets),
        due_captions=due_captions_by_key(
            [txn], {txn.pay_period_id: txn.pay_period.start_date},
        ),
        **extra,
    )


@dataclass(frozen=True)
class LegRenderAmounts:
    """The three per-cell maps a transfer LEG's fragment must publish.

    :class:`RenderAmounts`' twin for a leg (leaf ``X-bi-6-1``, ruling
    **R-BAL87**), keyed by the leg's ``cell_key`` exactly as the grid page
    keys its own maps (``routes/grid/_items.build_amount_maps``), so the
    fragment an HTMX swap returns reads the same three keys the page did.

    Attributes:
        budgets: ``{cell_key: what the leg's amount IS}``.
        settled: ``{cell_key: what its money DID}``, ``None`` until it has.
        retained: ``{cell_key: what a tick WOULD book}``, ``None`` otherwise.
    """

    budgets: dict
    settled: dict
    retained: dict


def leg_fragment_amounts(leg: TransferLeg) -> LegRenderAmounts:
    """Return the three maps for ONE transfer leg's fragment.

    The leg twin of :func:`fragment_amounts`: the same three producers the
    grid page asks for its legs, over one leg, with a basis built off the
    parent's own columns for the reason :func:`transfer_budgets` gives (a
    fragment has no read pass to take one from).

    Args:
        leg: The leg being rendered, its record loaded.

    Returns:
        The :class:`LegRenderAmounts`.

    Raises:
        AmountUnresolvable: From the resolver, for a transfer whose rule
            cannot answer.
    """
    xfer = leg.transfer
    return LegRenderAmounts(
        budgets=leg_amounts_by_key(
            [leg], derived_amount_basis(xfer.user_id, xfer.scenario_id),
        ),
        settled=settled_amounts_by_key([leg]),
        retained=leg_retained_amounts_by_key([leg]),
    )


def render_transfer_leg_cell(xfer: Transfer, account_id: int, **extra: Any) -> str:
    """Render a transfer LEG's grid cell with the context it must carry.

    The leg twin of :func:`render_transaction_cell` (leaf ``X-bi-6-1``,
    ruling **R-BAL87**): what every transfer door returns when the request
    came from a leg's grid cell (``leg_account_id`` on the form), drawn by
    the SAME partial the page draws it with, ``grid/_transaction_cell.html``,
    which tells a leg from a row by the ``transfer_leg`` test.  The leg is
    re-read here -- :func:`~app.services.transfer_legs.grid_transfer_leg`,
    record included -- rather than handed in, because every caller has a
    transfer and an account id and the leg's record may have just changed
    (a settle wrote it).  ``entry_sums`` is empty by construction: a leg
    holds no purchases.

    Args:
        xfer: The parent transfer, owner-established by the caller.
        account_id: The account the leg is on -- one of the parent's two
            endpoints (:func:`~app.services.transfer_legs.leg_of` refuses
            any other).
        **extra: Forwarded to ``render_template`` -- ``wrap_div=True``,
            ``conflict=True``, ``error=<message>``.

    Returns:
        Rendered HTML string.

    Raises:
        ValueError: When *account_id* is neither endpoint (from ``leg_of``).
        AmountUnresolvable: From the resolver.
    """
    leg = grid_transfer_leg(xfer, account_id)
    amounts = leg_fragment_amounts(leg)
    return render_template(
        "grid/_transaction_cell.html",
        txn=leg,
        account=fragment_balance_line(xfer.user_id),
        budgets=amounts.budgets,
        settled=amounts.settled,
        retained=amounts.retained,
        entry_sums={},
        due_captions=due_captions_by_key(
            [leg], {leg.pay_period_id: leg.pay_period.start_date},
        ),
        **extra,
    )


def render_transfer_leg_card(
    xfer: Transfer, account_id: int, *, card_prefix: str, error: str | None = None,
) -> str:
    """Render a transfer LEG's mobile card for an HTMX outerHTML swap.

    The leg twin of ``routes/transactions/_helpers._render_mobile_card``,
    for the mobile action bar's Mark Paid on a leg (``render=mobile_card``
    on the transfer door's form).  One card through the shared
    ``render_one_card`` macro (``grid/_mobile_card_single.html``), so the
    route and the page share one card producer.  ``can_edit`` is always
    ``True``: a leg renders on the OWNER's grid alone -- a transfer names
    no definition, so no companion sees one
    (:attr:`~app.models.transaction.Transaction.visible_to_companion`'s
    rule, ruling **R-BAL73**).

    A leg whose parent is Cancelled yields no row key (the card lists filter
    cancelled items out), so a rejected action on a stale card of one swaps
    in the banner-only wrapper that keeps the card's id and says why, the
    shape the transaction twin takes; a SUCCESS never lands there, because
    a just-settled transfer is neither cancelled nor deleted.

    Args:
        xfer: The parent transfer, owner-established by the caller.
        account_id: The account the leg is on.
        card_prefix: The per-tab namespace the card was rendered under.
        error: A rejection message for the danger banner, or ``None``.

    Returns:
        Rendered HTML string.

    Raises:
        ValueError: When *account_id* is neither endpoint (from ``leg_of``).
        AmountUnresolvable: From the resolver.
    """
    leg = grid_transfer_leg(xfer, account_id)
    categories = (
        db.session.query(Category)
        .filter_by(user_id=xfer.user_id)
        .order_by(Category.group_name, Category.item_name)
        .all()
    )
    row_keys = grid_view_service.build_row_keys(
        [leg], categories, is_income_section=leg.is_income,
    )
    if not row_keys:
        return render_template(
            "grid/_mobile_card_error.html",
            card_id=grid_view_service.card_dom_id(leg, card_prefix),
            error=error,
        )
    amounts = leg_fragment_amounts(leg)
    return render_template(
        "grid/_mobile_card_single.html",
        rk=row_keys[0],
        txn=leg,
        budgets=amounts.budgets,
        settled=amounts.settled,
        retained=amounts.retained,
        entry_sums={},
        entry_lists={},
        can_edit=True,
        id_prefix=card_prefix,
        account=fragment_balance_line(xfer.user_id),
        today=display_today(),
        error=error,
    )
