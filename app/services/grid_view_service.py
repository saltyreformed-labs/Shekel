"""
Shekel Budget App -- Grid View Service

Pure template-data producer for the budget grid.  Builds the sorted
row-key sequence (one entry per logical line item: a template-linked
group or a standalone name) and the
``(category_id, template_id, txn_name, period_id) -> [item]``
matching dict that drives every cell render, where an item is a
:class:`~app.models.transaction.Transaction` row or, since leaf
``X-bi-6-1`` (ruling **R-BAL87**), a :class:`~app.services.transfer_legs
.TransferLeg` -- one side of a transfer, drawn from the parent rather than
from a shadow row.  Every predicate here asks an item the questions both
shapes answer (``category_id``, ``status_id``, ``is_income``, ``recurs``,
``name``, ``pay_period_id``, ``due_date``); :func:`cell_key` is the one
place the two identities are told apart.

Single source of truth shared by:

  * the owner-facing grid (``app/routes/grid/page.py::index``), which
    consumes the dict from the desktop ``render_row_cells`` and the
    mobile ``render_row_card`` macros in
    ``app/templates/grid/_grid_row_macros.html``;
  * the companion view (``app/routes/companion.py::index``), which
    renders the shared ``grid/_mobile_this_period.html`` partial via
    the same macros (mobile-first v3 plan Commit 13 / D-B).

Architecture (per CLAUDE.md "Architecture" and
``docs/coding-standards.md``):

  * No Flask imports.  Takes plain SQLAlchemy collections and the
    ``RowKey`` shape; returns plain ``list[RowKey]`` and ``dict``.  One
    read of the session: :func:`drawn_status` takes the status row it draws
    by primary key, from the identity map once loaded (plan task plan#3054).
    :func:`draws_blank` asks a transfer's definition for its card-payment
    settings, which pricing the row has already loaded: ``GET /grid`` issued
    60 statements with it and without it (measured 2026-11-13, plan task
    plan#3698).
  * No monetary arithmetic.  Status / category / income-vs-expense
    filtering only; every displayed dollar amount still flows through
    ``balance_resolver`` per mobile-first v3 plan Section 1 rule 2.
  * Cancelled-status filtering is routed through
    :func:`app.utils.balance_predicates.is_cancelled` so the Python
    producer and the Jinja templates' Cancelled-status guards share
    one cached-ID definition (E-15 / MED-02).
"""

from collections import namedtuple
from collections.abc import Iterable
from datetime import date
from decimal import Decimal


from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.category import Category
from app.models.ref import Status
from app.services.cash_ledger import is_card_payment_definition
from app.services.pay_calendar import DerivedPeriod
from app.services.status_seam import drawn_status_id
from app.services.transfer_legs import (
    TRANSFER_FROM_PREFIX,
    TRANSFER_TO_PREFIX,
    PlanItem,
    TransferLeg,
    cell_key,
)
from app.utils.balance_predicates import is_cancelled

#: A grid item is a :data:`~app.services.transfer_legs.PlanItem`: a plan
#: row, or one side of a transfer read off its parent.  Named here for the
#: signatures below; :func:`~app.services.transfer_legs.cell_key` -- the
#: one place the two are told apart for identity -- moved down to that leaf
#: at leaf ``X-bi-6-1b`` and is imported above for the Jinja global that
#: still names it here.
GridItem = PlanItem


# Lightweight struct for a single row in the budget grid.  Rows of a
# RECURRING definition collapse to one row per (category, template)
# regardless of per-instance name drift; every other row -- a one-off's
# (a rule-less definition's, ruling **R-BAL34**), a transfer shadow, a CC
# payback -- collapses to one row per (category,
# name), so ``template_id`` here is set exactly when the row is a
# recurring definition's.
RowKey = namedtuple("RowKey", [
    "category_id",    # int -- FK to budget.categories
    "template_id",    # int or None -- the RECURRING definition, else None
    "txn_name",       # str -- row label (template name or the txn name)
    "group_name",     # str -- category group for section headers
    "item_name",      # str -- category item (used for sort tiebreaker)
    "display_name",   # str -- label shown in the row <th>
    "category",       # Category -- full ORM object for empty-cell rendering
])


def _short_display_name(name: str) -> str:
    """Strip redundant prefixes from item names for row headers.

    A transfer's leg is labelled "Transfer to X" / "Transfer from X"
    (:func:`~app.services.transfer_legs.leg_label`, whose two prefixes are
    read here rather than re-spelled) and a credit payback "CC Payback: X".
    The grid cell already shows a transfer icon or CC badge, so the prefix
    is visual noise in the row label.  Strip it to show only the meaningful
    part.
    """
    lower = name.lower()
    if lower.startswith(TRANSFER_TO_PREFIX.lower()):
        return name[len(TRANSFER_TO_PREFIX):]
    if lower.startswith(TRANSFER_FROM_PREFIX.lower()):
        return name[len(TRANSFER_FROM_PREFIX):]
    if lower.startswith("cc payback: "):
        return name[len("CC Payback: "):]
    return name


def leg_dom_id(transfer_id: int, account_id: int) -> str:
    """Return the DOM id of a transfer leg's grid cell wrapper.

    ``xfer-leg-<transfer id>-<account id>``: the leg's :func:`cell_key`
    spelled as an element id, so an HTMX response from a transfer door can
    target the cell that asked (``routes/transfers``, which holds the
    transfer and the leg's account and no item).  The row's twin is
    ``txn-cell-<id>``; :func:`cell_dom_id` composes either from an item, and
    every template reads one of the two rather than spelling the id.

    Args:
        transfer_id: The parent's ``budget.transfers.id``.
        account_id: The account the leg is on.

    Returns:
        The wrapper id.
    """
    return f"xfer-leg-{transfer_id}-{account_id}"


def cell_dom_id(item: GridItem) -> str:
    """Return the DOM id of *item*'s desktop grid cell wrapper.

    ``txn-cell-<id>`` for a row (the id every HTMX cell swap has always
    targeted) and :func:`leg_dom_id` for a leg.  A Jinja global of the same
    name, so the row macro, the cell partial and the doors' ``hx-target``
    values compose it in one place and the JavaScript reads it off the
    cell's ``data-cell`` attribute rather than composing a second spelling.

    Args:
        item: A row or a leg.

    Returns:
        The wrapper id.
    """
    if isinstance(item, TransferLeg):
        return leg_dom_id(item.transfer.id, item.account_id)
    return f"txn-cell-{item.id}"


def card_dom_id(item: GridItem, prefix: str = "") -> str:
    """Return the DOM id of *item*'s mobile card wrapper.

    ``card-<prefix->-<id>`` for a row and ``card-<prefix->xfer-<transfer
    id>-<account id>`` for a leg, where an empty *prefix* omits its segment
    -- the per-tab namespace ``render_one_card`` has always applied so one
    item can render in two visible tabs.  Composed here so the card macro,
    the action bar's ``hx-target`` and the single-card response agree.

    Args:
        item: A row or a leg.
        prefix: The tab namespace (``"tp"`` for This Period), or ``""``.

    Returns:
        The wrapper id.
    """
    if isinstance(item, TransferLeg):
        namespace = f"{prefix}-" if prefix else ""
        return f"card-{namespace}xfer-{item.transfer.id}-{item.account_id}"
    return row_card_dom_id(item.id, prefix)


def drawn_status(item: GridItem) -> Status | None:
    """Return the status row *item* is DRAWN as: what the grid shows of it.

    ``status_seam.drawn_status_id``'s answer as the ``ref.statuses`` row the
    templates read (plan task plan#3054), a Jinja global of the same name: the
    cell's chip and badge, the mobile card's and the Plan row's badge, the
    status's name in their labels and on the popover's context line, and the
    popover's Credit / Undo CC choice and its Status dropdown's selection.  A
    row put on the card is stored Paid and drawn Credit, so all of them show
    Credit, CC and Undo CC on it, and none a Paid tick; every other item is
    drawn as it is stored.  A transfer LEG is its transfer's status: Credit
    is a row's alone (``state_machine``'s transfer map has none).

    The one session read here: the status row by primary key.  A row's own
    status is joined onto it when it loads, so the identity map answers
    without a query; the Credit row is fetched once per session, at the first
    row on a card.

    Args:
        item: A row or a leg, with a row's ``entries`` loaded or loadable.

    Returns:
        The :class:`~app.models.ref.Status` to draw; ``None`` only for a
        status id no ``ref.statuses`` row holds.

    Raises:
        ValueError: From ``status_seam.drawn_status_id``, on a row holding
            two covering movements.
    """
    if isinstance(item, TransferLeg):
        return item.status
    return db.session.get(Status, drawn_status_id(item))


def draws_blank(
    item: GridItem, shown: Decimal, will_book: Decimal | None,
) -> bool:
    """Return whether *item*'s cell draws no figure: a card's payoff row with nothing to pay.

    Every card has one payment, a payoff row each payday made with the card,
    blank when nothing is owed (the card's amendment of 2026-11-05, plan task
    plan#3698).  So a paycheck where the card owes nothing draws no ``$0``,
    where every paycheck drew one before.  True for a transfer side, Projected,
    whose definition is the card's worked-out payment
    (:func:`~app.services.cash_ledger.is_card_payment_definition`), showing
    ``$0.00`` with nothing a tick would book instead.  The grid cell, the
    mobile card and the Plan row ask it, each drawing the figure a screen
    reader still reads and the click the cell still takes.

    False for every other item, a zero included: a ``$0.00`` bill still prints
    its zero (finding N-344), a ``$0.00`` payoff marked Paid is a record
    of paying nothing, and one whose tick would book a figure kept from a
    reverted close (*will_book*) has that figure to show.

    Args:
        item: A row or a leg.
        shown: The figure the cell shows: what it recorded, else its plan.
        will_book: What a tick would book where that is neither, else
            ``None`` (the cell's ``retained`` map).

    Returns:
        ``True`` when the cell draws no figure.
    """
    return (
        isinstance(item, TransferLeg)
        and item.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
        and shown == Decimal("0.00")
        and will_book is None
        and is_card_payment_definition(item.transfer.template)
    )


def row_card_dom_id(row_id: int, prefix: str = "") -> str:
    """Return the DOM id of a ROW's mobile card wrapper, from its id alone.

    :func:`card_dom_id`'s row arm, the way :func:`leg_dom_id` is
    :func:`cell_dom_id`'s leg arm: a caller holding no item can still target
    the card that asked.  That caller is the Mark Paid route answering a row
    that is GONE (plan step ``credit_card:CC-5-4a-4``, rulings **R-CC101** and
    **R-CC104**): a one-off row's delete removes it from the table, so only
    the id in the URL is left to name the card the refusal swaps into.

    Args:
        row_id: The row's ``budget.transactions.id``.
        prefix: The tab namespace (``"tp"`` for This Period), or ``""``.

    Returns:
        The wrapper id: ``card-<prefix->-<id>``, the empty prefix omitting
        its segment.
    """
    namespace = f"{prefix}-" if prefix else ""
    return f"card-{namespace}{row_id}"


def build_row_keys(
    transactions: Iterable[GridItem],
    categories: Iterable[Category],
    is_income_section: bool,
) -> list[RowKey]:
    """Build a deterministic, sorted list of RowKeys for the grid.

    Scans the supplied transactions and collects one row per logical
    line item.  Rows of a RECURRING definition dedupe by
    (category_id, template_id) and take their label from the current
    template name -- this keeps historic instances whose stored ``name``
    predates a template rename from splitting into a second row.
    Every other row dedupes by (category_id, name) and labels itself with
    the instance name.  **The fork is ``recurs``, not the link** (ruling
    **R-BAL34**, plan step ``balance:X-bi-7b``): a one-off carries a
    definition since that family's first leaf, and keyed on the link two
    same-named one-offs -- one grid row with two cells before -- became
    two rows both labelled by the name.  Display is cockpit grammar and
    identity is the correctness layer; the grid keeps the grouping the
    owner had, so a repeated informal one-off stays one row, and a
    one-off renamed at the popover moves to a new row as a link-less
    rename did.  Results are sorted by (group_name, item_name, txn_name)
    for stable alphabetical ordering within each category group.

    The caller controls scope: passing only visible-window transactions
    produces the default compact view (rows only for items active in
    the visible periods), while passing the full projection yields the
    show-all view.  Either way, cell matching in the template is
    unaffected -- it still walks ``txn_by_period`` per visible period.

    Args:
        transactions: iterable of grid items -- Transaction rows, or
            :class:`~app.services.transfer_legs.TransferLeg` values (which
            answer ``recurs`` ``False`` and file under their parent's
            category and label) -- to consider for row-key generation.
            Transactions with a non-null ``template_id`` must have their
            ``template`` relationship loaded (the grid route does this via
            ``selectinload``; the companion route does it via the join in
            ``companion_service.get_visible_transactions``) to avoid
            per-row lazy fetches; the rule ``recurs`` reads rides on the
            template's own joined load.
        categories: list of Category objects, already ordered by
            (group_name, item_name).  Used to map category_id -> Category
            for sort keys and for the empty-cell template.
        is_income_section: bool -- True to collect income transactions,
            False for expense transactions.

    Returns:
        list[RowKey] -- one entry per unique transaction row, sorted by
        (group_name, item_name, txn_name).  Deterministic across calls
        with the same data.
    """
    # Index categories by ID for O(1) lookup.
    cat_by_id = {c.id: c for c in categories}

    # Collect unique row keys.  For a recurring definition's rows the key
    # carries template_id (name omitted); for every other row the key
    # carries the instance name (template_id omitted).
    seen = set()
    row_keys: list[RowKey] = []

    for txn in transactions:
        # Skip deleted and cancelled transactions.  Routed through
        # the centralized ``is_cancelled`` predicate (D6-09 /
        # MED-02) so the Python row-key collector and the Jinja
        # ``!= STATUS_CANCELLED`` row guards in ``grid.html`` /
        # ``_mobile_grid.html`` share one definition of the rule.
        if txn.is_deleted or is_cancelled(txn):
            continue

        # Filter by income/expense.
        if is_income_section and not txn.is_income:
            continue
        if not is_income_section and not txn.is_expense:
            continue

        # Look up the category.  Items may have category_id=NULL (a
        # transfer's leg when the user's default "Transfers:
        # Incoming/Outgoing" categories are missing).  These must still
        # appear in the grid -- use a fallback group.
        cat = cat_by_id.get(txn.category_id)
        group_name = cat.group_name if cat else "Uncategorized"
        item_name = cat.item_name if cat else ""

        if txn.recurs:
            # A recurring definition's row: collapse all instances into
            # one row labelled with the definition's current name.  A row
            # that ``recurs`` has loaded its template, so there is no
            # fallback to the instance name here.
            label = txn.template.name
            key = (txn.category_id, txn.template_id, None)
        else:
            label = txn.name
            key = (txn.category_id, None, txn.name)

        if key not in seen:
            seen.add(key)
            row_keys.append(RowKey(
                category_id=txn.category_id,
                template_id=key[1],
                txn_name=label,
                group_name=group_name,
                item_name=item_name,
                display_name=_short_display_name(label),
                category=cat,
            ))

    # Sort by (group_name, item_name, txn_name) for deterministic ordering.
    row_keys.sort(key=lambda rk: (rk.group_name, rk.item_name, rk.txn_name))

    return row_keys


def _match_row_in_period(
    rk: RowKey,
    period: DerivedPeriod,
    txn_by_period: dict[int, list[GridItem]],
    is_income_section: bool,
) -> list[GridItem]:
    """Return the transactions matching ``rk`` in ``period``.

    Inner half of :func:`build_matched_by_row_period`, lifted to its
    own function so the outer cross-product loop stays flat (pylint
    ``too-many-nested-blocks``) and so the per-cell predicate is
    individually testable without instantiating the full row-key set.
    See :func:`build_matched_by_row_period` for the predicate
    semantics.

    **The two arms exclude each other's rows** (ruling **R-BAL34**): a
    recurring definition's row is matched by its template alone, and a
    name row is matched by name among the rows that do NOT recur.  The
    predicate used to compare by NAME whenever EITHER side was link-less,
    so a one-off sharing ``(category_id, name)`` with a recurring
    definition was drawn into that definition's row AND its own, and the
    definition's generated rows into the one-off's -- six cells on the
    2026-09-12 production restore (``from_scratch_architecture.md`` 10.4,
    trace 5: one-off 2584 Mother's Day against template 24, one-off 2581
    Homeschool Curriculum against template 21).
    """
    matched: list[GridItem] = []
    for txn in txn_by_period.get(period.period_id, []):
        if txn.category_id != rk.category_id:
            continue
        if is_income_section and not txn.is_income:
            continue
        if not is_income_section and not txn.is_expense:
            continue
        if txn.is_deleted or is_cancelled(txn):
            continue
        if rk.template_id is not None:
            if txn.template_id != rk.template_id:
                continue
        elif txn.recurs or txn.name != rk.txn_name:
            continue
        matched.append(txn)
    return matched


def build_matched_by_row_period(
    income_row_keys: list[RowKey],
    expense_row_keys: list[RowKey],
    periods: Iterable[DerivedPeriod],
    transactions: Iterable[GridItem],
) -> dict[tuple[int, int | None, str, int], list[GridItem]]:
    """Pre-compute the (row_key, period) -> matched transactions dict.

    Single source of truth for the grid's matching predicate: for each
    row key and each visible period, find the transactions that belong
    in that cell.  The Jinja grid templates (``grid.html`` and
    ``_mobile_grid.html``) previously hand-rolled this match in four
    duplicated blocks; the macros introduced in mobile-first v3 plan
    Commit 1 and the templates wired in Commits 3 and 4 consume this
    dict instead, so the predicate is defined once.

    Predicate (mirrors the Jinja loops at ``grid.html`` lines 158-173
    and 234-246 text-for-text):

    1. ``txn.category_id == rk.category_id``.
    2. Income section -> ``txn.is_income``; expense section ->
       ``txn.is_expense``.  Row keys are already partitioned by
       income/expense at ``build_row_keys`` time, but this redundant
       per-txn guard preserves the Jinja predicate verbatim.
    3. ``not txn.is_deleted``.
    4. ``not is_cancelled(txn)`` -- routed through the centralized
       ``is_cancelled`` helper so the Python producer and the Jinja
       templates' Cancelled-status guard share the same cached-ID
       source per E-15 / MED-02.
    5. A row key carrying a ``template_id`` (a recurring definition's
       row) matches by template id; a name row matches by name among the
       rows that do not recur (ruling **R-BAL34**; see
       :func:`_match_row_in_period`).

    Args:
        income_row_keys: row keys for the income section, in
            row-render order.
        expense_row_keys: row keys for the expense section, in
            row-render order.
        periods: iterable of :class:`~app.services.pay_calendar.DerivedPeriod`
            values -- the visible cells to render.  **The owner's own
            calendar answers them since plan step C2-f2b**, where they were
            ORM ``PayPeriod`` rows read out of the table by
            ``pay_period_service``; only ``period_id`` is read, and it is
            the same ``budget.pay_periods.id`` the transactions carry.  A
            PROJECTED period (``period_id`` ``None``) matches nothing here,
            which is correct rather than incidental: no transaction can
            point at a period that has no row.
        transactions: iterable of grid items -- Transaction rows or transfer
            legs (already filtered for user / account / scenario /
            soft-delete).  The function indexes these by ``pay_period_id``
            internally so the caller does not need to pre-group.

    Returns:
        ``dict[(category_id, template_id, txn_name, period_id),
        list[GridItem]]``.  Keys are 4-tuples uniquely identifying
        the (row, period) cell; values are non-empty lists of items in
        insertion order.  Cells with no matched items are omitted (the
        macro callers default to ``[]`` via ``dict.get``).
    """
    # Group items by pay_period_id once so the inner predicate
    # only iterates the period-relevant subset.  Mirrors the
    # ``txn_by_period`` produced by the grid route in v1.
    txn_by_period: dict[int, list[GridItem]] = {}
    for txn in transactions:
        txn_by_period.setdefault(txn.pay_period_id, []).append(txn)

    matched_by_row_period: dict[
        tuple[int, int | None, str, int], list[GridItem]
    ] = {}
    for row_keys, is_income_section in (
        (income_row_keys, True),
        (expense_row_keys, False),
    ):
        for rk in row_keys:
            for period in periods:
                matched = _match_row_in_period(
                    rk, period, txn_by_period, is_income_section,
                )
                if matched:
                    matched_by_row_period[(
                        rk.category_id,
                        rk.template_id,
                        rk.txn_name,
                        period.period_id,
                    )] = matched
    return matched_by_row_period


def due_captions_by_key(
    transactions: "Iterable[GridItem]",
    payday_by_period_id: "dict[int, date]",
) -> "dict":
    """Return ``{cell_key(item): the due date to caption, or None}``.

    **The grid cell's due-date caption, decided in PYTHON and indexed by the
    template** (pay-calendar plan step C4-a-1).  A row carries a ``due_date``
    only when the money is owed on a day other than the paycheck's own; where
    the two coincide the date says nothing and the cell stays quiet.  That is
    one rule, and before this it was written in Jinja as
    ``t.due_date != t.pay_period.start_date`` -- a comparison that reached
    through an ORM relationship, so a page drawing N cells issued a lazy load
    per distinct paycheck FROM INSIDE THE RENDER, and a template cannot be
    given a query budget.

    **Both surfaces that draw that cell call this**, which is what makes the
    caption the same on a page and in the HTMX fragment the same click swaps
    in: ``routes/grid/page`` for the grid and
    ``routes/_render_helpers.render_transaction_cell`` for the fragment.  It is
    the rule :class:`~app.routes._render_helpers.RenderAmounts` states for the
    three amount maps, applied to the fourth thing a cell draws.

    **Keyed by :func:`cell_key` and INDEXED rather than guarded**, on this
    template's standing rule: Jinja's default ``Undefined`` compares unequal to
    a date without raising, so a caption read as a bare variable renders on
    EVERY dated row when a surface forgets to publish it -- silently.  A map
    subscript raises instead, which is why ``budgets`` is a map and why this
    is one.  It was keyed by transaction id alone until leaf ``X-bi-6-1``
    drew a transfer's legs beside the rows.

    Args:
        transactions: The items being drawn -- rows or legs.  Each must
            carry a ``pay_period_id`` present in *payday_by_period_id*.
        payday_by_period_id: ``{budget.pay_periods.id: the day it opened}`` for
            every paycheck those rows are filed in.  Taken rather than derived
            because each caller already holds it for free and by a different
            route -- the grid from the DERIVED window whose columns it is
            drawing, the fragment from the ``pay_period`` its own ownership
            check has already loaded -- and deriving a second answer here would
            be the redundant read this arc exists to remove.  A ``start_date``
            is the one fact ``budget.pay_periods`` stores, carried through
            :func:`~app.services.pay_calendar.derive_periods` untouched, so the
            two routes cannot disagree.

    Returns:
        One entry per item: its ``due_date`` where that differs from its
        paycheck's payday, else ``None``.

    Raises:
        KeyError: A row is filed in a paycheck *payday_by_period_id* does not
            cover.  Loud rather than skipped: a missing entry means the caller
            built its map from a different row set than it is drawing, and a
            quiet ``None`` would draw a cell that silently omits a caption it
            owes.
    """
    return {
        cell_key(txn): (
            txn.due_date
            if txn.due_date is not None
            and txn.due_date != payday_by_period_id[txn.pay_period_id]
            else None
        )
        for txn in transactions
    }
