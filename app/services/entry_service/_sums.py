"""What a SET of purchases adds up to, and the contexts a screen renders.

``entry_service``'s derivation half: the pure per-set reductions (the debit /
credit split, the remaining budget, the settled actual, the percent complete)
and the three builders that assemble the whole context an envelope's cell or
entry list renders from.  One builder is not pure since plan step
``credit_card:CC-5-4a-5``: :func:`build_entry_lists_dict` asks
:mod:`._removals` once per screen what each purchase's X would free, a read
of the statement matches.

**The in-period CHECK is no longer here**, and it left at pay-calendar plan
step C4-a-3 (ruling **R-PC31**).  It was ``check_purchase_date_in_period``,
which reached through ``transaction.pay_period`` to compare a purchase against
the STORED ``end_date`` plan step **C4-c** dropped; the rule is now
:meth:`app.services.pay_calendar.DerivedPeriod.covers`, on the value that
DERIVES the span, so the containment test this module ran and the one every
period search runs are one spelling rather than two that agreed by hand.

**Nothing here writes**, which is why it is the leaf: the write doors
(:mod:`._doors`) read the reductions from here and nothing here reads them, so
the arrow runs one way and neither half can grow a cycle through the other.
(:mod:`._removals` -- read here, never reading ``_doors`` -- keeps the arrow
one way.)

**The sum of ALL of a row's purchases is NOT here**, and it left at plan step
X-au-c3.  It was ``compute_actual_from_entries``, named for a column that step
removed; it is now :func:`app.services.row_valuation.purchases_total`, in the
module both the cash and loan tiers can reach -- because the settlement record's
own accessor needs it, and a module under both tiers cannot import this one.

Architecture:
  - No Flask imports.
  - All monetary arithmetic uses Decimal.
"""

from decimal import Decimal

from app.models.account import Account
from app.models.transaction_entry import TransactionEntry
from app.services.cash_flow_set import CashFlowSet, purchase_accounts
from app.services.entry_service._removals import (
    PurchaseControls,
    purchase_controls,
)
from app.services.pay_calendar import DerivedPeriod
from app.utils.entry_partition import partition_entries
from app.utils.money import percent_complete


#: What the two HAND-ENTRY doors call the two directions a purchase can have.
#: Values, not display text: the add and edit forms post one of these and
#: :func:`purchase_amount` is what turns the pair into a stored figure.
CHARGE = "charge"
REFUND = "refund"


def purchase_amount(magnitude: Decimal, *, records_a_refund: bool) -> Decimal:
    """Return the figure a purchase STORES, from a magnitude and a direction.

    **The one composition of a purchase's sign from what a human stated**
    (developer ruling **bank_import:R-IK**, 2026-09-01, plan step
    ``bank_import:X-gj-2b-3``).  Ruling
    **bank_import:R-II** made a merchant credit a NEGATIVE purchase and moved
    positivity off the table onto the hand-entry door -- and the branch that
    did it then deleted the bound from the EDIT door too, because that form
    resubmits its amount box even when the owner only changed a date, so a
    stored refund could not be re-described while the box refused a negative.

    That left a typed ``-45.00`` booking a refund in silence: the projection
    moved by twice the figure, nothing on any screen said a refund had been
    recorded, and the identical keystroke on the ADD form was a 422.  **The
    remedy is that neither form can express a sign at all**: both bound the
    amount at ``>= 0.01`` and both carry a Charge/Refund control, and this is
    where the two become a figure.  A minus is not refused, it is
    unrepresentable -- which is what makes the bound structural rather than a
    fence somebody has to keep.

    **The bank-import door does NOT come through here**, and that is not an
    omission: a bank line arrives already signed, so
    ``statement_match._create._born_purchase`` converts it with
    ``-line.amount`` -- one expression, total over both directions.  Two
    different inputs, two conversions, each stated once.

    Args:
        magnitude: What the purchase cost, as a POSITIVE figure.  The schemas
            bound it at ``>= 0.01``; this asserts nothing and would happily
            negate a negative, because the bound belongs on the door that has
            an HTTP status to answer with.
        records_a_refund: Whether the owner said this money came BACK.

    Returns:
        The stored figure: *magnitude* negated for a refund, unchanged for a
        charge.
    """
    return -magnitude if records_a_refund else magnitude


def credit_total_moves(entry: TransactionEntry, valid_updates: dict) -> bool:
    """Return whether applying *valid_updates* moves the envelope's credit total.

    Finding **N-323**: the payback sync refuses a settled payback's re-derive
    only for a write that moves the total.  Asked as the entry's own
    CONTRIBUTION to that sum before and after, rather than as a case analysis
    over which fields were submitted: the sum counts ``amount`` where
    ``is_credit``, so an entry contributes its amount or nothing, and
    comparing the two is exact for every combination at once.  A field-name
    test is what the first draft used -- "amount or is_credit was submitted"
    -- and it still refused a DEBIT row's amount edit, which cannot reach the
    credit sum at all.  Split out of ``_doors.update_entry`` at plan step
    ``credit_card:CC-5-4a-5``, whose ``shown`` argument (``press`` since leaf
    5c-2b) took that function past pylint's local-variable ceiling; a
    reduction over one purchase, so it lives with the others.

    Args:
        entry: The purchase as stored, BEFORE the update is applied.
        valid_updates: The submission, already narrowed to updatable fields.

    Returns:
        ``True`` when the entry's contribution to the credit sum changes.
    """
    credit_before = entry.amount if entry.is_credit else Decimal("0")
    amount_after = (
        valid_updates["figure"].amount if "figure" in valid_updates
        else entry.amount
    )
    credit_after = (
        amount_after
        if valid_updates.get("is_credit", entry.is_credit) else Decimal("0")
    )
    return credit_before != credit_after


def compute_entry_sums(
    entries: list[TransactionEntry],
) -> tuple[Decimal, Decimal]:
    """Compute (sum_debit, sum_credit) from a list of entries.

    Pure function -- no database access.

    Args:
        entries: List of TransactionEntry objects.

    Returns:
        Tuple of (sum_debit, sum_credit) as Decimals.
    """
    debit_entries, credit_entries = partition_entries(entries)
    sum_debit = sum((e.amount for e in debit_entries), Decimal("0"))
    sum_credit = sum((e.amount for e in credit_entries), Decimal("0"))
    return sum_debit, sum_credit


def build_entry_sums_dict(
    transactions: list,
    budgets: dict[int, Decimal],
) -> dict[int, dict]:
    """Build a {txn_id: sums_dict} mapping for transactions with purchases.

    Used by grid routes and HTMX cell-render endpoints to pre-compute
    entry aggregates for the cell template.  Only transactions with
    non-empty purchases are included in the result.

    **Over the row's PURCHASES, never its whole family** (plan step
    ``balance:X-bi-3e-2``, ruling **R-BAL68**): the status seam's covering
    movement -- the payment row a manual-branch close writes -- is an entry
    too, and it is not something the owner spent.  Read over ``entries``,
    a settled envelope closed at the door with no purchases showed its own
    close as one purchase (8 such rows on production, `$794.79`), and a
    reverted one would show the withdrawn close under a Projected row.

    The dict carries ``budget``, ``remaining`` and ``over_budget`` so the
    grid cell template renders without inline Jinja arithmetic
    (E-16 / MED-04).  ``remaining`` is computed via
    :func:`compute_remaining` (the E-21 declared base minus the sum of all
    entries), so the cell's over-budget styling is driven by the same single
    rule that the dashboard bill row sees via ``bill.entry_remaining``.

    **The base arrives as an ARGUMENT** (plan step X-au-c2b).  It was read here
    as ``txn.estimated_amount``, the COLUMN -- and under the amount model a
    derived row stores none, so that read would meet a ``None`` in a
    subtraction on a live screen at the first per-kind cutover.  The caller
    resolves its whole row set ONCE through
    :func:`~app.services.cash_ledger.amounts_by_key` and hands the map down, so
    the cell's numerator, its denominator and the balance row beside it are one
    figure rather than three reads that agree by luck.

    Pure function -- no database access beyond what was already loaded
    on the Transaction objects (expects entries to be accessible, either
    via eager load or lazy access).

    Args:
        transactions: List of Transaction objects with entries accessible.
        budgets: ``{transaction_id: Decimal}`` covering every row in
            *transactions*, from the caller's one
            :func:`~app.services.cash_ledger.amounts_by_key` call.  Indexed with
            ``[]``: a row missing from it is a caller that priced a different
            set, and a default here would be a fabricated budget.

    Returns:
        dict mapping transaction ID to {"debit": Decimal, "credit": Decimal,
        "total": Decimal, "count": int, "budget": Decimal,
        "remaining": Decimal, "over_budget": bool, "pct": Decimal}.  Empty dict
        if no transactions have entries.  ``pct`` is the entries-to-estimate ratio clamped
        to [0, 100] via :func:`pct_complete`; it drives the mobile
        progress-bar's ``data-progress-pct`` attribute on the unified
        ``render_row_card`` macro per mobile-first v3 plan Commit 13.
    """
    result: dict[int, dict] = {}
    for txn in transactions:
        purchases = txn.purchases
        if purchases:
            debit, credit = compute_entry_sums(purchases)
            total = debit + credit
            budget = budgets[txn.id]
            remaining = compute_remaining(budget, purchases)
            result[txn.id] = {
                "debit": debit,
                "credit": credit,
                "total": total,
                "count": len(purchases),
                "budget": budget,
                "remaining": remaining,
                "over_budget": remaining < Decimal("0"),
                "pct": pct_complete(total, budget),
            }
    return result


def build_entry_lists_dict(
    transactions: list,
    budgets: dict[int, Decimal],
    periods: "dict[int, DerivedPeriod]",
    cash_flow: CashFlowSet | None,
    viewer_id: int,
) -> dict[int, dict]:
    """Build a {txn_id: entry_list_data} mapping for envelope transactions.

    Pre-computes the entry-list rendering inputs that
    ``_render_entry_list`` in ``app/routes/entries.py`` produces per
    HTMX request, so the mobile grid macro can render entries inline
    on the initial grid response instead of lazy-loading them one
    request per envelope card.  With 6 visible pay periods and ~10
    envelope templates each, the lazy-load fan-out is ~60 parallel
    GETs on the entries endpoint, which exceeds the
    ``RATELIMIT_DEFAULT`` ceiling of ``30 per minute`` and leaves the
    over-limit cards stuck on the loading spinner.  Server-side
    rendering eliminates the fan-out entirely.

    Only purchase-tracking rows (``txn.tracks_purchases`` -- a row whose
    definition has ``is_envelope`` set) get an entry, matching the macro's
    guard for
    whether to render the inline entries section.  Non-tracking
    transactions are silently skipped.

    Expects ``entries`` and ``template`` eager-loaded on the
    Transaction objects.  **It was PURE from plan step X-f3b** (ruling
    **R-FM** made the posted indicator a question about the PURCHASE, which
    dropped the clearing rule's per-account read) **until plan step
    ``credit_card:CC-5-4a-5``**, which gives every purchase's X the bank lines
    it would leave unexplained (ruling **R-CC80**): that is a question about
    the statement matches, asked here ONCE for every envelope the screen draws
    (:func:`~app.services.entry_service._removals.purchase_controls` -- a
    payback query, a match-member query, and the act and line loads only
    where a match names a purchase), never once per envelope.

    **The PAYCHECK SPANS arrive as an argument** (pay-calendar plan step
    C4-a-3, ruling **R-PC34**), for the reason *budgets* does in
    :func:`build_entry_sums_dict`.  The
    out-of-period warning read ``txn.pay_period.end_date`` through the ORM
    relationship -- a stored column plan step **C4-c** dropped, and a lazy load
    per distinct paycheck issued from inside a render.  Each caller already
    holds the spans it needs and holds them by a different route: ``/grid``
    from the DERIVED window its own query scoped ``pay_period_id IN (...)``
    to, the companion page from the ONE period its query filtered on, and the
    two single-row fragments from the calendar they derive for their row.
    Deriving a second answer here would be the redundant read this arc exists
    to remove -- the argument
    :func:`~app.services.grid_view_service.due_captions_by_key` makes for the
    same shape one step earlier.

    **The OWNER's cash-flow set arrives as an argument too** (plan step
    ``credit_card:CC-5-2``), for the add-purchase form's account picker: each
    row's choice is :func:`~app.services.cash_flow_set.purchase_accounts` over
    it, the one spelling the purchase door tests against (ruling **R-CC37**).
    An argument and NOT a default, so that a caller which forgets it fails
    loudly rather than rendering a form with no picker on the initial grid
    while the HTMX refresh renders one -- the exact two-callers shape
    :func:`entry_list_view` exists to make unrepresentable.

    Args:
        transactions: List of Transaction objects with ``entries`` and
            ``template`` accessible.
        budgets: ``{transaction_id: Decimal}`` covering every row in
            *transactions* (see :func:`build_entry_sums_dict`), forwarded to
            :func:`entry_list_view`.
        periods: ``{budget.pay_periods.id: DerivedPeriod}`` covering every
            paycheck the *transactions* are filed in.  Indexed with ``[]`` for
            *budgets*' reason: a row whose paycheck is missing means the caller
            built this map from a different row set than it is rendering, and a
            silent skip would drop an out-of-period warning the screen owes.
        cash_flow: The rows' OWNER's cash-flow set, resolved with NO override
            (:func:`~app.services.account_resolver.resolve_cash_flow_set`), or
            ``None`` for an owner with no grid-eligible account.  The owner's,
            never the requester's: a companion's page renders the owner's
            picker (ruling **R-CC11**).  Never the page's overridden set: an
            override outside the set collapses it to one account, which is a
            fact about the view and not about what a purchase may name.
        viewer_id: The SESSION's user -- the rows' owner on the grid and the
            phone card, a companion on the companion page -- so only the
            owner's lists name the owner's bank lines (rulings **R-CC130** /
            **R-CC132**).  An argument and not a default, for *cash_flow*'s
            reason: a caller that forgot it must fail rather than draw the
            owner's statement for whoever is looking.

    Returns:
        dict mapping envelope transaction ID to one
        :func:`entry_list_view` -- the WHOLE context
        ``grid/_transaction_entries.html`` renders an envelope from.

        Empty dict when no transaction in the input has an envelope
        template.

    Raises:
        KeyError: A row is filed in a paycheck *periods* does not cover, or
            priced by a *budgets* that does not hold it.  **The two subscripts
            are written on separate lines below so a traceback says WHICH**:
            they key on different id spaces -- ``txn.id`` against
            ``txn.pay_period_id`` -- and a ``KeyError`` carrying a bare int
            from one physical line cannot be attributed to either map, let
            alone to a table.  Named by adversarial review, 2026-08-31.
    """
    controls = purchase_controls(transactions, viewer_id)
    return {
        txn.id: entry_list_view(
            txn.purchases,
            budgets[txn.id],
            periods[txn.pay_period_id],
            purchase_accounts(cash_flow, txn),
            controls[txn.id],
        )
        for txn in transactions
        if txn.tracks_purchases
    }


def entry_list_view(
    entries: list[TransactionEntry],
    budget: Decimal,
    period: "DerivedPeriod",
    accounts: tuple[Account, ...],
    controls: PurchaseControls,
) -> dict:
    """Return the WHOLE derived context one envelope's entry list renders from.

    **The ONE producer of that context, and it exists because a caller-built
    one was measured wrong.**  ``grid/_transaction_entries.html`` was assembled
    independently by the HTMX refresh (``routes/entries.py``) and by the grid
    macro (via :func:`build_entry_lists_dict`), and only the refresh supplied
    the indicator's key -- so on every INITIAL render of the grid, the mobile
    card, the companion view and the full-edit popover, the template asked a
    Jinja ``Undefined``, which answers ``False`` silently.  Every already-posted
    purchase read *"Not yet seen on a statement, so the budget is still held
    back"* while the projection had already released its reservation: the screen
    contradicted the number beside it, on 9 of 9 such purchases on the
    2026-08-13 production clone (`$640.70`).

    Two callers assembling one template's context is the shape; a forgotten key
    was the instance.  Returning the whole context from one place makes the
    omission unrepresentable at the service tier -- the route splats this
    mapping rather than naming its keys.

    **The indicator asks the PURCHASE since plan step X-f3b, not the account**
    (ruling **R-FM**).  It asked
    :meth:`~app.services.cash_ledger.StatementCoverage.is_cleared`, so the
    tooltip had a THIRD state -- a purchase whose posting day is recorded but
    which no assertion covers yet -- captioned *"the budget is still held
    back"*.  That sentence is now false.  The screen therefore asks the question
    the reservation asks, has this purchase posted, and the two cannot disagree
    because one fact is behind both.  It also drops a ``coverage_for`` read per
    rendered ACCOUNT from every grid render.

    **It no longer takes the TRANSACTION at all** (pay-calendar plan step
    C4-a-3).  The row was read for exactly one thing -- ``txn.pay_period``, to
    bound the out-of-period warning against the STORED ``end_date`` plan step
    **C4-c** dropped -- and every other input was already an argument, so once
    the span became one too the parameter had nothing left to answer.

    **That is a third argument a caller must pair correctly, and the honest
    statement is where the pairing is MADE rather than that it cannot go
    wrong.**  It went the other way for one value: the period used to be
    derived from *txn* here, so a period belonging to another row was not
    expressible, and now it is -- the same exposure *entries* and *budget*
    already carried.  What holds it is that ``app/`` has exactly TWO callers
    and each derives the period from the ROW's own ``pay_period_id``:
    :func:`build_entry_lists_dict` indexes its map with it, and
    ``routes.entries._render_entry_list`` resolves it through
    :meth:`~app.services.pay_calendar.PayCalendar.require_period`.  A guard
    here would be a fence over a shape neither caller can produce, which is
    what the arc is removing rather than adding.

    Args:
        entries: The transaction's PURCHASES
            (:attr:`~app.models.transaction.Transaction.purchases`, never the
            family: the seam's covering movement is not a purchase, ruling
            **R-BAL68**), already loaded and ordered by ``purchased_on``.
            Taken as an argument because the callers load them differently
            -- the route through the owner-scoped
            :func:`get_entries_for_transaction`, the grid off an eager-loaded
            relationship -- and neither may lose its scoping to share this
            derivation.
        budget: What the row's amount RESOLVES to -- the E-21 declared base the
            remaining figure is computed against
            (:func:`~app.services.cash_ledger.amounts_by_key`).  An argument
            rather than a read of ``txn.estimated_amount`` since plan step
            X-au-c2b: a derived row stores no figure in that column.
        period: The paycheck the entries' row is FILED in, as the owner's pay
            calendar DERIVES it.  It bounds the out-of-period warning below,
            and it is a :class:`~app.services.pay_calendar.DerivedPeriod`
            rather than an ORM ``PayPeriod`` because a period's span is the
            derivation over the owner's paydays, not the column the table
            stores beside them.
        accounts: The accounts a NEW purchase under this row may name, in
            picker order with the row's own first --
            :func:`~app.services.cash_flow_set.purchase_accounts` over the
            OWNER's cash-flow set (plan step ``credit_card:CC-5-2``, ruling
            **R-CC37**).  The purchase door admits exactly these, so the form
            offers what the door takes.  A fourth argument the callers pair
            the way they pair *period*: :func:`build_entry_lists_dict`
            derives it per row from the one set it is handed, and
            ``routes.entries._render_entry_list`` from the row's owner.
        controls: The list's
            :class:`~app.services.entry_service._removals.PurchaseControls`
            (:func:`~app.services.entry_service._removals.purchase_controls`):
            what removing each purchase would withdraw, covering at least
            *entries* -- the grid's one read over every envelope, or the
            fragment's over its one row -- and whether the owner is looking.
            Its removals are indexed with ``[]`` for *budgets*' reason: a
            purchase missing from them is a caller that read a different set,
            and an X drawn with no caption over a matched purchase is the
            silent withdrawal ruling **R-CC80** forbids.

    Returns:
        The seven keys the template consumes:

          - ``entries``: the list as given.
          - ``accounts``: the tuple as given.  The template renders its
            ``<select name="account_id">`` only when it holds more than one
            (ruling **R-CC34**: with one choice there is no control, and the
            door's ``None`` arm files the purchase on the row's account) --
            so every form of an owner with no card is byte-identical to
            before the picker existed.
          - ``remaining`` (Decimal): the row's resolved budget minus the sum
            of all entries (debit + credit), via :func:`compute_remaining`.
          - ``out_of_period_ids`` (set[int]): entry IDs whose ``purchased_on``
            falls outside *period*, surfacing the OP-4 date-awareness warning.
            Informational: nothing refuses such a purchase, because a
            late-posting one legitimately falls outside its row's paycheck.
            It asks ``purchased_on`` and never ``settled_on``, which is the
            distinction the column split exists for -- "is this purchase
            budgeted to the right pay period" is a BUDGET-clock question, and
            when the money reached the bank is a cash-clock fact belonging to
            the balance fold.
          - ``posted_ids`` (set[int]): entry IDs whose bank posting day is
            recorded, so their money has left the account and their envelope is
            no longer holding their budget back.  It is the SAME fact the
            reservation buckets on
            (``cash_ledger._amounts._entry_checking_impact``), decided HERE in
            Python; the template renders the answer and never re-derives it.
          - ``removals`` (dict[int, PurchaseRemoval]): each purchase's
            :class:`~app.services.entry_service._removals.PurchaseRemoval`
            -- what its X and its CC un-tick would leave unexplained on the
            statement screen, which the list names before the press and
            posts back with it (plan step ``credit_card:CC-5-4a-5``).
          - ``owner_viewing`` (bool): whether those controls may name the
            owner's bank lines (rulings **R-CC130** / **R-CC132**).
    """
    return {
        "removals": {
            entry.id: controls.removals[entry.id] for entry in entries
        },
        "owner_viewing": controls.owner_viewing,
        "entries": entries,
        "accounts": accounts,
        "remaining": compute_remaining(budget, entries),
        "out_of_period_ids": {
            e.id for e in entries if not period.covers(e.purchased_on)
        },
        "posted_ids": {
            e.id for e in entries if e.settled_on is not None
        },
    }


def compute_remaining(
    budget: Decimal,
    entries: list[TransactionEntry],
) -> Decimal:
    """Compute remaining budget: the declared base minus the sum of ALL entries.

    Uses the sum of ALL entries regardless of payment method (debit +
    credit) because the remaining balance represents budget consumption,
    not checking impact.  Negative values indicate overspending.

    **THE BASE IS A NET CASH TARGET, SO THIS IS UNBOUNDED IN BOTH DIRECTIONS**
    (developer ruling **bank_import:R-IK**, 2026-09-01, ruling **bank_import:R-II**, plan step
    ``bank_import:X-gj-2b-3``).  A merchant credit files as a NEGATIVE
    purchase, so ``sum(entries)`` can be negative and this can exceed the
    budget: an envelope budgeting `$100.00` that holds one `-$50.00` refund and
    nothing else answers `$150.00`, which is *`$150.00` of net spending may
    still be recorded against a `$100.00` plan*.  The alternative -- clamping
    at the budget -- was put to the developer with those numbers and refused,
    because it breaks the identity every surface that renders this depends on
    (``sum(entries) + remaining == budget``) and because
    ``cash_ledger._amounts._entry_checking_impact`` already reads the plan the
    same way: its own derivation says that treating a refund as spending
    nothing further *is the opposite of what a reservation is for*.

    **The COST is recorded rather than hidden.**  A refund of a purchase made
    in an EARLIER pay period lands in the period the bank posted it (the same
    developer's ruling of 2026-09-01, on the cash basis, because the
    alternative moves a figure the balance projection depends on), so it
    enlarges an envelope whose own purchases it did not reverse.  That is a
    known and accepted consequence, not a defect to re-report.

    Per E-21 (audit MED-03 / F-028 / F-056) the budget base for an
    entry-tracked bill row is the row's own AMOUNT unconditionally --
    never ``actual_amount`` and never status-dependent.  This is why
    the signature takes that base directly rather than the whole
    ``Transaction``: the base cannot be switched on at runtime;
    callers that want to display "remaining" against a different base
    are out of contract and must compute it themselves.

    **The parameter was named ``estimated_amount`` until plan step X-au-c2b**,
    and renaming it is the point rather than tidying: a parameter named after a
    COLUMN invites the next caller to pass that column, and under the amount
    model a derived row's is ``NULL``.  Every caller now passes what the row's
    amount RESOLVES to (:func:`~app.services.cash_ledger.amounts_by_key`), so
    the row's amount cell, its remaining and its over-budget flag share one
    declared base by construction.

    Pure function -- no database access.

    Args:
        budget: The transaction's resolved amount -- the E-21 declared base for
            the row's plan-vs-actual figures.
        entries: List of TransactionEntry objects.

    Returns:
        Decimal -- the remaining budget (negative means overspent).
    """
    total_spent = sum((e.amount for e in entries), Decimal("0"))
    return budget - total_spent


def pct_complete(total: Decimal, target: Decimal) -> Decimal:
    """Compute percent complete, clamped to [0, 100].

    Feeds the entry-tracking progress-bar widths on the companion
    transaction card (and any other surface that needs an entry
    aggregate as a percentage of its declared budget base).  Returns a
    Decimal so money math never crosses the Decimal/float boundary at
    the route layer (MED-04 / E-16): the companion route used to
    ``float(total / estimated * Decimal("100"))`` inline, which violated
    the "money math is service-layer Decimal, not route-layer float"
    standard.  Thin domain-named wrapper over
    :func:`app.utils.money.percent_complete` -- the single numeric
    contract the dashboard and companion progress surfaces both share.

    The two-decimal-place result is safe to render as-is in CSS width
    values: ``data-progress-pct="55.50"`` is parsed by
    ``app/static/js/progress_bar.js`` via ``parseFloat`` before being
    applied as an inline width, and CSS itself accepts the decimal
    notation in ``%`` values.

    Args:
        total: Sum of entries against the budgeted line.  **It can be
            NEGATIVE since ruling bank_import:R-II** -- an envelope whose
            refunds exceeded its purchases -- which makes
            :func:`~app.utils.money.percent_complete`'s negative-ratio arm
            reachable where it was not.  ``0`` is the right answer and the
            arm was censused and kept at plan step ``bank_import:X-gj-2b-3``:
            this drives a progress BAR's width, a bar cannot be negative, and
            the figure it sits beside is rendered unclamped.
        target: Budgeted estimated amount.  If <= 0 the function
            returns ``Decimal("0")`` rather than dividing by zero or
            producing a misleading negative percentage.

    Returns:
        Decimal in [0, 100] quantised to two decimal places when the
        guard does not fire; ``Decimal("0")`` when ``target <= 0``.
    """
    return percent_complete(total, target)
