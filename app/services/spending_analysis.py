"""
Shekel Budget App -- Shared Spending-Analysis Primitives

The settled-expense primitives shared by every retrospective spending
surface: the Year-End spending section (:mod:`year_end_summary_service`)
and the unified Spending report (:mod:`spending_report_service`, whose
``_build_surprises`` is the settled-surprises kernel the retired Variance
tab once owned; the retired per-period Trends engine read spending through
here too until the D7 rebuild removed its last consumer).

Extracting them here means the "what counts as measured spending" rule is
defined once rather than re-implemented per surface (coding-standards rule
13; the T-P3 ``projection_inputs`` precedent, which closed a cross-file
``duplicate-code`` finding the same way):

* :func:`query_settled_expenses` -- the settled-expense load selected by
  a PERIOD SET (settled status, expense type, not deleted, scoped to the
  owner's cash-flow set / scenario / period set).  Its one caller is the Spending report's
  pay-period arm, which no route reaches today; the sibling below is what a
  live render runs.  The two share their filters, so a change to what
  "settled spending" selects is still a single edit.  **Each answers the
  set's OWN rows beside the EXPENSE LEG of every settled transfer the set
  touches, read off the parent in ``budget.transfers``** (leaf
  ``balance:X-bi-6-1b``, ruling **R-BAL86**): a
  :class:`~app.services.cash_flow_set.PlanItems`, where it was the rows
  with the settled transfer-out shadow among them.
* :func:`query_settled_expenses_in_span` -- the same filters selected
  by the attribution rule instead of a period set: COALESCE(due_date,
  owning period start) inside a calendar span, across ALL the user's pay
  periods, over both tables.  The Spending report's calendar windows read
  through it so a bill due in month M is attributed to M even when its
  funding period does not overlap M.
* :func:`resolved_actual_amount` -- the settled-surprises kernel's
  plan-at-entry vs recorded-at-settle rule, a question about an item's
  PARENT (ruling **plan#1983**): a settled parent is worth what it RECORDED
  as having moved; an unsettled one has recorded nothing, so it asks the
  amount model for its plan and shows zero variance BY CONSTRUCTION.  Since
  that ruling this module tells a row and a leg apart for no FIGURE: the
  routers ``recorded_spend`` and ``planned_spend`` (leaf
  ``balance:X-bi-6-1b``), which sent each shape to its own pair of
  producers, are deleted, and their callers ask
  :func:`~app.services.row_valuation.settled_contribution` and
  :func:`~app.services.cash_ledger.resolve_amount` of the item's
  :func:`~app.services.transfer_legs.parent_of`.
* :func:`signed_pct` -- the guarded "signed value as a percentage of a
  base" helper (``None`` when the base is zero), shared by the surprises
  figures and the Spending hero's vs-prior / vs-average chips.
* :func:`payment_timeliness_from_txns` -- the on-time / late / average
  days-before-due rule, given the already window-attributed settled
  expenses.  Its gate asks each item whether it RECORDS a payment
  (``_holds_a_record``, a row's entries or a leg's record, leaf
  ``balance:X-bi-6-4a``): a row-or-leg router for a predicate rather than a
  figure.

Pure-function module -- no Flask imports; the only side effects are the two
read queries, :func:`query_settled_expenses` and
:func:`query_settled_expenses_in_span`.
"""

import calendar as cal_mod
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import joinedload, selectinload

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.cash_flow_set import (
    CashFlowSet,
    PlanItems,
    own_rows_clause,
    set_transfer_legs,
    set_transfer_legs_in_periods,
    touched_transfers_clause,
)
from app.services.pay_calendar import DerivedPeriod
from app.services.cash_ledger import AmountBasis, resolve_amount
from app.services.row_valuation import settled_figure
from app.services.transfer_legs import (
    PlanItem,
    PlanParent,
    TransferLeg,
    expense_legs,
    preload_side_records,
)
from app.utils.amount_relationships import transfer_pricing_load_options
from app.utils.balance_predicates import settled_status_ids
from app.utils.dates import pay_period_range_label
from app.utils.money import CENTS, HUNDRED, ZERO

_WINDOW_TYPES = frozenset({"pay_period", "month", "year"})


def validate_window(
    window_type: str,
    period_id: int | None,
    month: int | None,
    year: int | None,
) -> None:
    """Validate a discriminated pay-period / month / year window selector.

    Shared by the analytics window-based report services (the Spending
    report and the confirmed-ledger Income Statement) so the required-field
    rule and its error messages live in one place rather than a copy per
    surface.

    Args:
        window_type: One of ``"pay_period"`` / ``"month"`` / ``"year"``.
        period_id: The pay period id (required for ``"pay_period"``).
        month: The calendar month 1-12 (required with ``year`` for
            ``"month"``).
        year: The calendar year (required for ``"month"`` and ``"year"``).

    Raises:
        ValueError: If ``window_type`` is unknown, or a required field for
            the type is missing.
    """
    if window_type not in _WINDOW_TYPES:
        raise ValueError(
            f"Invalid window_type {window_type!r}. Must be one of "
            f"{sorted(_WINDOW_TYPES)}."
        )
    if window_type == "pay_period" and period_id is None:
        raise ValueError(
            "period_id is required when window_type is 'pay_period'."
        )
    if window_type == "month" and (month is None or year is None):
        raise ValueError(
            "Both month and year are required when window_type is 'month'."
        )
    if window_type == "year" and year is None:
        raise ValueError("year is required when window_type is 'year'.")


def query_settled_expenses(
    scenario_id: int,
    period_ids: list[int],
    cash_flow: CashFlowSet,
) -> PlanItems:
    """Load the settled expense items across the cash-flow set over given periods.

    Filters: settled status only (Paid/Received -- so Cancelled and
    Credit, which are not settled, are excluded), expense type only, not
    deleted, the owner's cash-flow set, one scenario, and ``pay_period_id``
    in *period_ids* -- over the rows, and over the parents of the legs.

    **The items are the PAYCHECK's across the set -- checking and its cards
    -- not one account's** (developer ruling ``credit_card:R-CC16``, plan
    step CC-4-3), and since leaf ``balance:X-bi-6-1b`` (ruling **R-BAL86**)
    they are the set's OWN rows plus one LEG per settled transfer, read off
    the parent in ``budget.transfers`` rather than off a shadow row:
    :func:`~app.services.cash_flow_set.own_rows_clause` selects every
    member's rows and no shadow, and :func:`~app.services.cash_flow_set
    .set_transfer_legs_in_periods` draws each transfer the set touches from
    the side :func:`~app.services.cash_flow_set.leg_accounts_shown` names
    (ruling ``R-CC23``: once, from the balance line's side, when both
    endpoints are members), kept where it is the EXPENSE leg -- money that
    left, which is what spending is; the income leg on the far endpoint is
    not spend, as the income shadow's type never was.  A card payment is
    therefore one settled leg, on the balance account's side.  It was
    ``Transaction.account_id == account_id``, then the paycheck-rows clause
    that carried the settled transfer-out shadow in as a row.

    **It eager-loads ``category`` and NOT ``pay_period``**, and the second half
    of that changed at pay-calendar plan step **C2-f3d** -- in BOTH queries,
    for one reason: an AST and grep census over everything reachable from
    ``compute_spending_report`` found no read of ``txn.pay_period`` anywhere.
    The period IS the window on this path, so its identity is already resolved
    (:func:`._window._resolve_window`), and neither the breakdown, the hero nor
    ``settled_contribution`` asks a row which paycheck it sits in.  The load's
    stated reason was that a caller "attributes by period", which no caller
    does.

    **THAT CENSUS STOPPED BEING TOTAL on 2026-09-05, and it is recorded here
    rather than re-run because what changed was reachability, not this
    query.**  The surprises list prices a row through
    ``cash_ledger.resolve_transaction_amount`` as of 2026-09-05 (the
    ``/analytics/spending`` fix), and rule 4's arm --
    ``LoanPricing.derive_cash`` -> ``loan_loaders.installment_for`` -- reads a
    ``pay_period`` on every call, because that producer takes the period start
    eagerly.  **From plan step balance:X-au-f-2 it was the PARENT TRANSFER's
    period rather than the shadow's** (ruling R-BAL10 moved the answer to the
    parent), so the chain this query would have had to load was
    ``Transaction.transfer -> pay_period``; the count was unchanged and the
    relationship was not.  A row reached that arm only as a transfer's
    shadow, through rule 5: plan step ``balance:X-bi-6-4d-2`` deleted the
    shadows and plan step ``balance:X-bi-6-5`` the rule, so no row this query
    loads reaches it (a transaction's dispatch holds no rule 4); whether the
    census is total again is not re-measured here.  The removal is not
    re-litigated here, and whether this query should adopt
    ``amount_relationships.pricing_load_options`` -- which would re-add the
    ``pay_period`` load C2-f3d removed -- is finding **N-296**'s, whose census
    of unguarded batch pricing callers this reader joined.

    **What the removal actually costs, said conditionally because the two
    queries differ in whether they run.**  This one is reached only from the
    pay-period arm, which no route builds (see :func:`._window._series_windows`),
    so its ``joinedload`` executed on zero live renders; what it did there was
    hydrate a ``PayPeriod`` per window into the identity map, which is what hid
    the ``db.session.get`` the retired ordinal walk beside it ran.  The sibling
    is the one every ``/analytics/spending`` render runs twelve times, and its
    ``contains_eager`` went too -- **its JOIN is load-bearing and its
    hydration was not**, which an earlier draft of this paragraph conflated:
    the COALESCE attribution filter runs on the join, so the join stays and
    only the ``PayPeriod`` columns leave the SELECT.  Measured on a clone of
    production: the same 29 rows with the same ids, six fewer columns each.

    Args:
        scenario_id: The budget scenario to scope to (the caller's
            baseline).
        period_ids: The pay-period ids to include.  An empty list yields
            an empty result.
        cash_flow: The owner's cash-flow set, whose members' items to load.

    Returns:
        The :class:`~app.services.cash_flow_set.PlanItems`: the matching
        settled expense :class:`Transaction` rows and the expense legs of
        the matching settled transfers, records loaded.
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

    # Pylint: ``duplicate-code`` -- the settled-expense query core.  The
    # account / scenario / period / expense-type filter prefix coincides
    # with ``dashboard_service``'s expense query, but the two diverge on the
    # parts that matter (eager-loads and the settled-vs-projected status
    # gate), so a shared builder would need both as parameters and save no
    # logic (coding-standards rule 13).  One-sided ``duplicate-code``
    # disable, mirroring the journal_entry/transfer FK-block precedent.
    # pylint: disable=duplicate-code
    rows = (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.category),
            # ``resolved_actual_amount`` asks ``row_valuation.settled_figure``,
            # which sums EVERY settled row's entries rather than reading a
            # stored copy (plan step X-au-c3 for an envelope; balance:X-bi-4b-1
            # for every row, ruling R-BAL80).  Without this the
            # Spending report issues one SELECT per settled row where it
            # used to read a column; production carries 29 such rows.
            selectinload(Transaction.entries),
        )
        .filter(
            own_rows_clause(cash_flow),
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.is_deleted.is_(False),
            Transaction.transaction_type_id == expense_type_id,
            Transaction.status_id.in_(settled_status_ids()),
        )
        .all()
    )
    # pylint: enable=duplicate-code
    return _settled_items(rows, set_transfer_legs_in_periods(
        cash_flow, scenario_id, period_ids,
        Transfer.status_id.in_(settled_status_ids()),
    ))


def query_settled_expenses_in_span(
    scenario_id: int,
    cash_flow: CashFlowSet,
    user_id: int,
    first_day: date,
    last_day: date,
) -> PlanItems:
    """Load the settled expense items ATTRIBUTED to a calendar span.

    The same filters as :func:`query_settled_expenses` -- the cash-flow
    set's own rows and its settled transfers' expense legs, plan steps
    CC-4-3 and ``balance:X-bi-6-1b`` -- selected by the attribution rule
    instead of a period set: rows, and parents, whose ``COALESCE(due_date,
    owning period start)`` falls inside ``[first_day, last_day]``, across
    ALL the user's pay periods.  The former period-overlap pre-filter
    under-fetched at window boundaries: a settled bill due in month M whose
    funding period did not overlap M was attributed to NO month window at
    all (its own period's months excluded it by date; M never loaded its
    period).  Selecting by the attribution day itself makes every settled
    expense belong to exactly one calendar window, and the result no longer
    depends on which window is viewed.

    The COALESCE runs in SQL on the joined period row -- the same rule
    consumers previously applied in Python -- so the filter and the
    attribution stay one definition; the transfer query joins the same
    table on ``Transfer.pay_period_id`` and states the same COALESCE over
    the parent's ``due_date``, which is the leg's.  It builds its own
    query rather than calling :func:`~app.services.cash_flow_set
    .set_transfer_legs_in_periods`, because that loader windows by period
    MEMBERSHIP and this reader does not.

    Args:
        scenario_id: The budget scenario to scope to (the caller's
            baseline).
        cash_flow: The owner's cash-flow set, whose members' items to load.
        user_id: The owning user (scopes the pay-period join).
        first_day: The span's first calendar day (inclusive).
        last_day: The span's last calendar day (inclusive).

    Returns:
        The :class:`~app.services.cash_flow_set.PlanItems`: the matching
        settled expense :class:`Transaction` rows, with ``category``
        eager-loaded like the sibling query (``pay_period`` is JOINED and
        not loaded: the join carries the COALESCE filter above, and no
        consumer reads the relationship, plan step C2-f3d), and the expense
        legs of the matching settled transfers, records loaded.
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    attribution_day = db.func.coalesce(
        Transaction.due_date, PayPeriod.start_date,
    )
    rows = (
        db.session.query(Transaction)
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .options(
            joinedload(Transaction.category),
            # The sibling query's reason, and the same consumer: see
            # :func:`query_settled_expenses` above.
            selectinload(Transaction.entries),
        )
        .filter(
            own_rows_clause(cash_flow),
            Transaction.scenario_id == scenario_id,
            PayPeriod.user_id == user_id,
            Transaction.is_deleted.is_(False),
            Transaction.transaction_type_id == expense_type_id,
            Transaction.status_id.in_(settled_status_ids()),
            attribution_day >= first_day,
            attribution_day <= last_day,
        )
        .all()
    )
    transfer_attribution_day = db.func.coalesce(
        Transfer.due_date, PayPeriod.start_date,
    )
    transfers = (
        db.session.query(Transfer)
        .join(PayPeriod, Transfer.pay_period_id == PayPeriod.id)
        .options(*transfer_pricing_load_options())
        .filter(
            touched_transfers_clause(cash_flow),
            Transfer.scenario_id == scenario_id,
            PayPeriod.user_id == user_id,
            Transfer.is_deleted.is_(False),
            Transfer.status_id.in_(settled_status_ids()),
            transfer_attribution_day >= first_day,
            transfer_attribution_day <= last_day,
        )
        .order_by(Transfer.id)
        .all()
    )
    return _settled_items(rows, set_transfer_legs(cash_flow, transfers))


def _settled_items(rows: list[Transaction], legs: list[TransferLeg]) -> PlanItems:
    """Return *rows* and the expense half of *legs* as the window's items, every record loaded.

    Where the report's two settled loaders (:func:`query_settled_expenses`,
    :func:`query_settled_expenses_in_span`) meet.  The timeliness metric
    (:func:`payment_timeliness_from_txns`) reads every settled item's RECORD:
    a row's through its ``entries``, which each loader's query loads
    (``selectinload``), and a leg's off its transfer's side list, loaded here
    for every leg in ONE statement
    (``transfer_legs.preload_side_records``, ruling **plan#2136**), where it
    would otherwise cost a query per leg.  The grid's leg loader loaded them
    for every reader until that ruling, the grid, the dashboard and the
    calendar included, which read none.

    Args:
        rows: The window's settled expense rows, ``entries`` loaded.
        legs: The legs the set draws for the window's settled transfers,
            either side.

    Returns:
        The :class:`~app.services.cash_flow_set.PlanItems`: *rows*, then the
        EXPENSE legs (:func:`~app.services.transfer_legs.expense_legs`).
    """
    spent = expense_legs(legs)
    preload_side_records(leg.transfer for leg in spent)
    return PlanItems.of(rows, spent)


def resolved_actual_amount(parent: PlanParent, basis: AmountBasis) -> Decimal:
    """Return the 'actual' amount for an estimate-vs-actual comparison.

    The Variance/surprises kernel's rule, a question about a PARENT (ruling
    **plan#1983**: a caller holding a transfer leg asks its transfer,
    :func:`~app.services.transfer_legs.parent_of`): a settled parent is
    worth what it RECORDED as having moved
    (:func:`~app.services.row_valuation.settled_figure`), and an unsettled
    one has recorded nothing, so it reads back its own PLAN
    (:func:`~app.services.cash_ledger.resolve_amount`) and its individual
    variance is exactly zero.  A "surprise" is an item whose recorded figure
    differs from its plan.  It routed a row and a leg to their twin producers
    through ``planned_spend`` (leaf ``balance:X-bi-6-1b``) until that ruling.

    **Which rows can produce one changed at plan step X-au-c3, and it is a
    widening the surprises list wanted.**  The old rule read
    ``actual_amount``, which was populated only when a HUMAN had typed a
    correction -- so a settled row was a surprise only if somebody had retyped
    its figure, and a row whose settle booked something other than its estimate
    for any other reason (a salary row's live recompute, a loan payment's
    payment-date escrow) recorded nothing and reported a variance of zero.  A
    settled row now always records what moved, so the comparison finally answers
    the question the list is named for.  Whether the figure came from a human is
    a separate fact with its own home: the covering movement's
    ``figure_source_id`` (the row's ``settled_basis_id`` through ``X-bi-4a``).

    **THE FALL-THROUGH ASKS THE AMOUNT MODEL since plan step X-bu, and that is
    what makes the zero variance STRUCTURAL rather than an agreement between two
    producers.**  It read the row's own ``estimated_amount`` column -- through
    an ``owned_amount`` accessor from plan step X-au-c2b, and directly before
    that -- so an unsettled row whose plan a cutover had declared DERIVED
    carries no figure and this REFUSED, while the estimate half beside it in
    ``_build_surprises`` resolved. That is finding **BAL-462**'s failure mode
    exactly, on the unsettled population instead of the settled one: same
    exception, same page, same class of row. It was unreachable only because
    this kernel's one caller filters to settled rows in SQL -- a caller
    convention, which is what BAL-462 already paid for trusting.

    Both halves are now the SAME CALL on the same row and the same basis, so an
    unsettled row's delta is zero because there is nothing for it to be
    different from. **BAL-462's remedy said this reader "is correct and
    stays"**; two neutral adversarial reviews of X-bu measured the surviving
    refusal, and the developer superseded that clause on 2026-09-05 rather than
    close the finding with the obligation recorded in prose -- which is the very
    thing BAL-462 exists to name.

    **It takes the BASIS rather than building one**, the pattern every reader of
    a possibly-derived row follows: the plan may be a live derivation, so a
    basis is REQUIRED and not optional, and its one caller already holds the one
    it built for the estimate half. Passing a second basis would resolve the
    same row against a different pass.

    Args:
        parent: The plan row or transfer to resolve.  Its settlement record
            is read, and ``status`` is declared ``lazy="joined"`` on both
            models so nothing here needs an explicit load.
        basis: The read pass's
            :class:`~app.services.cash_ledger.AmountBasis`, built once by the
            caller.  REQUIRED rather than optional for the same reason
            ``_build_surprises`` states it: a row whose plan is derived cannot
            be priced without one, and an optional basis would put the refusal
            back one branch later.

    Returns:
        The comparison actual as a ``Decimal``.

    Raises:
        AmountUnresolvable: When the amount model cannot price an UNSETTLED
            row.  A settled row never raises since plan step
            ``balance:X-bi-4b-1``: its record is the sum of its entries.  That
            is not the derived-plan refusal this function used to carry, which
            plan step X-bu removed from here.
    """
    recorded = settled_figure(parent)
    if recorded is not None:
        return recorded
    return resolve_amount(parent, basis)


def calendar_window_bounds(
    window_type: str, year: int, month: int | None,
) -> tuple[date, date]:
    """Return the inclusive ``(first_day, last_day)`` of a calendar window.

    Shared by the analytics window services (the Spending report and the
    confirmed-ledger Income Statement) so the month / year date-span rule is
    defined once rather than re-derived per surface.

    Args:
        window_type: ``"month"`` (spans ``month`` within ``year``) or any
            other value, treated as the full ``year``.
        year: The calendar year.
        month: The calendar month 1-12 (required for a ``"month"`` window;
            ignored for a year window).

    Returns:
        The first and last calendar dates of the month or year.
    """
    if window_type == "month":
        last_dom = cal_mod.monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, last_dom)
    return date(year, 1, 1), date(year, 12, 31)


def window_label(
    window_type: str,
    month: "int | None",
    year: "int | None",
    period: DerivedPeriod | None,
) -> str:
    """Return the human label for an analytics window.

    The THIRD shared window rule, beside :func:`validate_window` and
    :func:`calendar_window_bounds`, and it is here for their reason: the
    Spending report and the confirmed-ledger Income Statement each render a
    window heading, and a rule defined per surface is a rule two surfaces can
    come to disagree about.

    **It was written TWICE, and finding that was pylint's** (plan step
    C2-f3a).  Ledger row **P47** recorded three copies of the pay-period
    REGISTER; collapsing those onto
    :func:`~app.utils.dates.pay_period_range_label` left the two enclosing
    ``_window_label`` functions structurally identical, and ``duplicate-code``
    said so.  They were the same three-arm dispatch over two window dataclasses
    that carry the same four fields -- ``StatementWindow`` and
    ``SpendingWindow`` -- and their month arms produced one string from two
    spellings (``date(...).strftime("%B")`` against an f-string format spec).
    So the register was the visible half of a whole duplicated function.

    **It takes the SCALARS rather than a window**, exactly as
    :func:`validate_window` does at the same two call sites, because the two
    dataclasses are per-surface types this module may not choose between --
    and in :func:`validate_window`'s ORDER, ``month`` before ``year``, so two
    functions called side by side at both sites cannot be read as taking their
    arguments in different orders.

    Args:
        window_type: ``"pay_period"``, ``"month"`` or ``"year"``.
        month: The calendar month 1-12, for a ``"month"`` window.
        year: The calendar year, for a ``"month"`` or ``"year"`` window.
        period: The resolved
            :class:`~app.services.pay_calendar.DerivedPeriod` for a
            ``"pay_period"`` window.  ``None`` for the calendar windows, and
            also when a pay-period window's id names none of the owner's
            periods -- which is what makes the empty string below a real
            answer rather than a fallback.

    Returns:
        ``"Feb 21 - Mar 06, 2026"`` (pay period), ``"January 2026"`` (month),
        ``"2026"`` (year), or ``""`` when a pay-period window resolved no
        period.
    """
    if window_type == "pay_period":
        if period is None:
            return ""
        return pay_period_range_label(period.start_date, period.end_date)
    if window_type == "month":
        return f"{date(year, month, 1):%B} {year}"
    return str(year)


def category_names(txn: PlanItem) -> tuple[str, str]:
    """Return the ``(group_name, item_name)`` labels for a row or a leg.

    Reads the item's category (a leg's is its parent's), falling back to
    ``("Uncategorized", "Uncategorized")`` for a row with no category.
    Shared by the year-end spending section and the unified Spending report
    so both bucket an uncategorized row under the same label.

    Args:
        txn: The row or transfer leg whose category labels to resolve.

    Returns:
        The ``(group_name, item_name)`` pair.
    """
    if txn.category is None:
        return ("Uncategorized", "Uncategorized")
    return (txn.category.group_name, txn.category.item_name)


def signed_pct(numerator: Decimal, base: Decimal) -> Decimal | None:
    """Return ``numerator / base`` as a signed 2-dp percent, or ``None``.

    **The base must be POSITIVE, and that is one rule stated HERE** (plan step
    ``bank_import:X-gj-2b-3``).  A zero base has no meaningful percentage, and
    a NEGATIVE one reports the wrong DIRECTION: spend rising from ``-50.00`` to
    ``100.00`` is a rise of ``150.00``, and ``150 / -50`` is ``-300%`` -- a
    fall of three hundred percent printed over a rise, on a chip an owner
    reads.  That is a fact about this OPERATION rather than about any caller,
    so it lives here.

    **It was ``base == ZERO`` here and ``baseline > ZERO`` at the caller**
    (``spending_report_service._types.Comparison.of``), added when ruling
    **bank_import:R-II** made a negative baseline reachable.  Two guards whose
    fail sets NEST -- the caller's strictly subsumed this one -- is the shape
    ``_income._load_line`` deleted eight files away in this same branch, and
    ``CreationBars.bar_for`` before it.  One guard now, and the caller has
    none.

    The percentage is rounded to two decimal places with ``ROUND_HALF_UP``
    (the project's money-rounding convention).

    Args:
        numerator: The signed quantity (a variance, or a vs-prior delta).
        base: The percentage base (an estimate, or a prior spend).

    Returns:
        ``(numerator / base) * 100`` quantized to 0.01, or ``None`` when
        ``base`` is not positive.
    """
    if base <= ZERO:
        return None
    return (numerator / base * HUNDRED).quantize(CENTS, rounding=ROUND_HALF_UP)


def payment_timeliness_from_txns(txns: list[PlanItem]) -> dict | None:
    """Compute on-time / late / average-days metrics over settled expenses.

    Examines the subset of *txns* that carry both ``settled_on`` and
    ``due_date`` (the only items whose timing is knowable -- an unsettled
    one has no settle day by construction; a transfer leg's is its covering
    movement's, leaf ``balance:X-bi-6-1b``).  A bill paid on or before its
    due date (``days_paid_before_due >= 0``) is on time; the average is
    signed (positive = paid early on average).  Routes through the
    ``days_paid_before_due`` property both shapes carry, which since plan
    step X-f1 subtracts two civil dates rather than converting an instant
    (ruling R-EC) -- one arithmetic, :func:`app.utils.dates
    .days_paid_before_due`.

    **An item that RECORDS nothing is not a timed bill, for a row as for a
    leg** (ruling **R-BAL90**, finding **BAL-528**, leaf
    ``balance:X-bi-6-4a``): a ``$0.00`` close holds no movement (ruling
    **R-BAL82**), nothing was paid on any day, so "days paid before due"
    has no subject.  A leg says so already -- its day IS its movement's and
    it has none -- while a ``$0.00``-closed ROW keeps a ``settled_on``
    column, so through ``X-bi-6-1b`` the row was timed and the leg was not:
    two shapes, two answers.  The gate reads the RECORD for both
    (:func:`_holds_a_record`), so they answer alike; a row with a record
    keeps its own ``settled_on`` as the day.  Dropping a $0.00 close takes
    one off the count and off whichever of on time / late it fell in, and
    shifts the unrounded average unless its days equal the rest's mean (the
    2-dp figure can round a small shift away); when it was the only timed
    item the answer becomes ``None``.  That is the one declared change (0
    such rows on the 2026-09-22 production dump).

    The caller (the Spending report's hero, the one caller) supplies the
    window's settled expenses already attributed, so this core owns only
    the paid-at/due-date gate and the counting.

    Args:
        txns: Settled expense items attributed to the window, a row's
            ``entries`` loaded (both loaders here load them).

    Returns:
        A dict with ``total_bills_paid``, ``paid_on_time``, ``paid_late``,
        and ``avg_days_before_due`` (a 2-dp ``Decimal``), or ``None`` when
        no item records a payment and carries both ``settled_on`` and
        ``due_date``.
    """
    applicable = [
        txn for txn in txns
        if txn.settled_on is not None and txn.due_date is not None
        and _holds_a_record(txn)
    ]
    if not applicable:
        return None

    paid_on_time = 0
    paid_late = 0
    total_days = 0
    for txn in applicable:
        days_before = txn.days_paid_before_due
        total_days += days_before
        if days_before >= 0:
            paid_on_time += 1
        else:
            paid_late += 1

    avg_days = (
        Decimal(str(total_days)) / Decimal(str(len(applicable)))
    ).quantize(CENTS, rounding=ROUND_HALF_UP)

    return {
        "total_bills_paid": len(applicable),
        "paid_on_time": paid_on_time,
        "paid_late": paid_late,
        "avg_days_before_due": avg_days,
    }


def _holds_a_record(item: PlanItem) -> bool:
    """Return whether *item* RECORDS a payment: a movement, not a ``$0.00`` close.

    The record of a settled item is its movements (ruling **R-BAL80**): a
    leg's is its covering movement (:attr:`~app.services.transfer_legs
    .TransferLeg.record`), a row's its family -- a bill's or a paycheck's
    covering movement, an envelope's purchases.  A ``$0.00`` close is a close
    with NO entries (ruling **R-BAL82**), which is the one settled item that
    answers ``False`` (leaf ``balance:X-bi-6-4a``, ruling **R-BAL90**).

    Args:
        item: A settled row, its ``entries`` loaded, or a leg.

    Returns:
        ``True`` when a movement records what the item paid.
    """
    if isinstance(item, TransferLeg):
        return item.record is not None
    return bool(item.entries)
