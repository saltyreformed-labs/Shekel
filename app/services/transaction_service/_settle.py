"""
Shekel Budget App -- Transaction Service: what SETTLING a row means

Ruling **R-FA**'s verb and everything it is made of: the branch predicate that
says whether a row's amount is DERIVED from its entries, the two preconditions
no settle door may skip, the valuation the panel displays and the verb books,
the predicate that tells a human's correction from the panel's own prefill, and
the envelope primitive underneath it all.

**Two settle entry points, and the difference is deliberate.**
:func:`settle_transaction` is what settling a row MEANS -- amount and status and
ledger together.  :func:`settle_from_entries` is the envelope PRIMITIVE
underneath it, and it stays public for ``carry_forward_service``, which settles
a BATCH and must reconcile the ledger after its ``no_autoflush`` block, so it
owns that act itself.

**The rule this module holds is that a door states an INTENT and the service
decides what it costs.**  Every public function here is one of those decisions,
and none of them is reachable from a template or a form field.

Architecture:
  - No Flask imports.  Receives ORM objects, mutates them, and
    raises domain exceptions on precondition violation.
  - All monetary arithmetic uses ``Decimal``.  The entry sum a settle books
    is ``row_valuation.purchases_total`` -- it was
    ``entry_service.compute_actual_from_entries`` until plan step X-au-c3
    deleted that helper along with the column it was named for.
  - Does NOT flush or commit -- the caller owns the transaction
    boundary so the helper can safely participate in larger atomic
    operations (e.g. the carry-forward batch in Phase 4).  The ledger
    reconcile :func:`settle_transaction` runs FLUSHES; it still does not
    commit.
"""

import logging
from decimal import Decimal

from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.services import posting_service
from app.services.cash_ledger import (
    AmountBasis,
    derived_amount_basis,
    contribution_of,
)
from app.services.match_press import Press
from app.services.movement_account import admitted_movement_account_id
from app.services.row_valuation import purchases_total
from app.services.settle_day import SettleDay
from app.services.stated_figure import StatedFigure
from app.services.status_seam import (
    Settlement,
    apply_status_change,
    honoured_correction,
    honoured_figure,
    recorded_leg_settlement,
    recorded_settlement,
    tender_account_id_of,
    tender_for_status,
)
from app.services.transaction_service._status_rules import settled_status_id
from app.utils.balance_predicates import is_identity_move, settled_status_ids
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSACTION_SETTLED_FROM_ENTRIES,
    log_event,
)

from ._row_rules import reject_unsettleable, settles_from_entries

logger = logging.getLogger(__name__)


def fixed_settle_amount(txn: Transaction) -> "Decimal | None":
    """Return what a settle books from the row's OWN RECORDS, or ``None``.

    **The pure prefix of :func:`settle_amount`**, and the sibling in shape of
    :func:`app.services.row_valuation.fixed_contribution`: the arms that answer
    before any producer is consulted, so a caller that only wants those arms
    does not run the paycheck engine to find out there was nothing to run it
    for.  ``None`` means neither arm applies and the row's amount must be
    RESOLVED, which is what :func:`settle_amount` goes on to do.

    Two arms, in this order:

      * a row whose figure comes from its PURCHASES books their sum
        (:func:`settles_from_entries`, ruling **R-FF**) -- and it takes
        precedence, because such a row honours no correction at all;
      * a row still holding a ``corrected`` record books THAT
        (:func:`~app.services.status_seam.honoured_correction`, plan step
        X-au-c3).  A revert keeps what moved, so a row the user reverted in
        order to edit re-books the figure they read off their statement.

    **It is published because a SCREEN needs the second arm** (developer,
    2026-08-17).  A reverted row is worth its PLAN -- that is what the balance
    counts and what the grid shows -- while a re-settle books its retained
    correction, so the two numbers genuinely differ and the second one was
    visible on no surface but the reconcile panel.  The developer's rule is that
    whatever will be booked against the account is shown wherever the row is
    shown; :func:`retained_settle_amounts_by_id` is that map, and it is built
    from THIS function rather than from a second reading of the same columns, so
    what a screen promises and what a tick books cannot drift.

    Pure and cheap: a status test and a read of the row's ``entries`` (its
    purchases for the first arm, its covering movement for the second, since
    plan step ``balance:X-bi-4b-1``).  No producer runs, so a whole grid costs
    no paycheck engine, and every batch caller loads the entries once.

    Args:
        txn: The row being priced.

    Returns:
        The figure the row's own records answer, or ``None`` when they do not.
    """
    if settles_from_entries(txn):
        # The PURCHASES, never the family (ruling R-BAL68): this prices the
        # row BEFORE the seam runs, and a reverted manual close's kept
        # movement is still on the row then -- the seam withdraws it when
        # the ``purchases`` record lands.
        return purchases_total(txn.purchases)
    return honoured_correction(txn)


def retained_settle_amounts_by_id(rows) -> "dict[int, Decimal | None]":
    """Return ``{transaction_id: the figure a re-settle would RE-BOOK}``.

    The display map for the one figure a screen could not otherwise show (plan
    step X-au-c3, developer 2026-08-17).  A row that has been reverted out of
    the settled band keeps what it recorded, and a re-settle honours it -- so
    the grid showing a ``$500.00`` plan while marking it paid books ``$245.32``
    is two answers to one money question, which is the defect the reconcile
    panel's own prefill was fixed for and which the grid and the full-edit
    popover still had.

    ``None`` for every row where no such gap exists: a row already SETTLED
    (:func:`app.services.row_valuation.settled_amounts_by_id` is that row's
    map, and the screen shows the recorded figure already), a row that settles
    from its purchases (its entries are on screen and are the figure), and a row
    holding no ``corrected`` record (its plan IS what a settle would book).  So
    a non-``None`` value means exactly "this row will book something other than
    what you can see", which is the only case worth drawing.

    **It asks :func:`~app.services.status_seam.honoured_correction` rather than
    :func:`fixed_settle_amount`, and the difference is a false caption on a
    large class of rows.**  That function's FIRST arm is the purchases sum, so
    delegating to it answered a figure for every unsettled ENVELOPE carrying a
    purchase -- a `$400` envelope with one `$25` entry came back `$25.00`, and
    every grid surface drew it under the words "the figure recorded before this
    row was set back to Projected".  That row has never settled and records
    nothing; the caption was false, and the cell already showed `25 / 400` two
    lines up.  A retained CORRECTION is the one thing this map is about, and
    ``honoured_correction`` is the arm that answers it (found by adversarial
    review, 2026-08-18; the paragraph above already said this and the code did
    not do it).

    Args:
        rows: The rows a surface is about to render.

    Returns:
        ``{transaction_id: Decimal | None}`` covering every row.
    """
    settled = settled_status_ids()
    return {
        row.id: (
            None if row.status_id in settled else honoured_correction(row)
        )
        for row in rows
    }


def leg_retained_amounts_by_key(legs) -> "dict[tuple[int, int], Decimal | None]":
    """Return ``{leg.cell_key: the figure a re-settle would RE-BOOK}`` for legs.

    :func:`retained_settle_amounts_by_id`'s twin for the grid's transfer legs
    (leaf ``X-bi-6-1``, ruling **R-BAL87**), keyed by
    :attr:`~app.services.transfer_legs.TransferLeg.cell_key`.  The same rule
    through the same seam: ``None`` for a leg whose parent is settled (its
    recorded figure is on screen already) and for one whose transfer stores
    no stated figure; else what its transfer's retained figure still states
    (:func:`~app.services.status_seam.honoured_figure` over
    :func:`~app.services.status_seam.recorded_leg_settlement`, the transfer's
    one figure since plan step ``balance:X-bi-6-4d-2``) -- the figure a
    person read off a statement before the transfer was reverted, which the
    transfer's re-settle honours (``transfer_service._settle``).

    Args:
        legs: The :class:`~app.services.transfer_legs.TransferLeg` values a
            surface is about to render, records loaded.

    Returns:
        ``{(transfer_id, account_id): Decimal | None}`` covering every leg.
    """
    settled = settled_status_ids()
    return {
        leg.cell_key: (
            None if leg.status_id in settled
            else honoured_figure(recorded_leg_settlement(leg))
        )
        for leg in legs
    }


def settle_amount(txn: Transaction, basis: AmountBasis) -> Decimal:
    """Return what settling *txn* would BOOK, absent a caller-supplied actual.

    **The ONE valuation act 1 of :func:`settle_transaction` uses**, published
    because the reconcile panel must show the figure a tick will book.  A panel
    that renders the row's own figure beside a verb that books something else is
    two answers to one money question, one screen apart -- and after plan step
    X-aq the verb genuinely books something else for a salary row.  The verb
    CALLS this rather than re-branching, so there is no shape in which the
    displayed figure and the booked one can drift.

    It is a PURE read: nothing here mutates, so the panel calls it per offered
    row and the verb calls it again at the settle.

    **The basis is the CALLER'S and this builds none** (plan step X-au-j,
    findings **N-295** / **N-309**).  It built its own per call until then, and
    both live callers are PASSES, so the paycheck engine ran over the owner's
    whole pay-period set once per offered row: N-309 measured **609
    salary-pricing and 609 loan-pricing constructions** over 825 candidates and
    `4.7 s` to render, which ``amount_basis``'s own docstring had already named
    as finding **N-228**.

    **It is REQUIRED rather than defaulted, and that is the structural half.**
    An optional basis leaves the expensive shape as what a caller gets by
    saying nothing, so the next pass to reach this function reintroduces N-228
    in silence -- which is how the cost regrew one tier up after plan step
    X-au-c2b closed it WITHIN a call (N-268 / N-269).  The pass's PayCalendar
    one package over is required for the same reason and says so the same way:
    *"a producer that rebuilt its caller's pass would be the copy this
    parameter exists to remove"*.  A caller holding no pass builds one and says
    so in a line.

    Args:
        txn: The row about to be offered or settled, still Projected.
        basis: The read pass's
            :class:`~app.services.cash_ledger.AmountBasis`.  It must be built
            for THIS row's owner and scenario;
            :func:`~app.services.cash_ledger.resolve_transaction_amount`
            REFUSES a foreign one rather than answering from another
            scenario's salary profiles and another scenario's loans.

    Returns:
        ``sum(entries)`` when :func:`settles_from_entries`, else what the row
        is WORTH against the amount model (``cash_ledger.contribution_of``).

    Raises:
        ValidationError: On a row no door may settle
            (:func:`reject_unsettleable`).  A deleted row's value is nothing,
            and answering for one here would publish a figure
            :func:`settle_transaction` refuses to book.

    **The MANUAL branch was two figures until plan step X-au-d**, resolved
    together by a ``_manual_branch_figures`` helper: what the settle books, and
    the "freshest" live recompute that superseded the row's cached column.  The
    second existed only because a salary row's ``estimated_amount`` was a cache
    of a derivation (finding **N-224**) and the settle was the one moment the
    arc reconciled it.  Such a row stores no figure now, so there is nothing to
    supersede and nothing to reconcile; the branch is the valuation alone, and
    ``contribution_of`` resolves that derivation directly.
    """
    reject_unsettleable(txn)
    fixed = fixed_settle_amount(txn)
    if fixed is not None:
        return fixed
    return contribution_of(txn, basis)


def _is_correction(
    txn: Transaction, submitted: "StatedFigure | None", booked: Decimal,
) -> bool:
    """Return whether *submitted* is a STATED figure this settle would BOOK.

    **The verb's own act-1b decision, and it is finding N-231's fix.**  Three
    doors may hand a settle a figure and only some of them are corrections: a
    row whose amount is DERIVED from its entries ignores one outright
    (:func:`settles_from_entries`), and a figure equal to what the row would
    book anyway is the panel's own prefill coming back untouched.

    **It was PUBLIC until the developer's 2026-08-17 ruling**, so the reconcile
    writer could ask what the settle would count.  It asked BEFORE the settle
    and re-resolved the row's amount to make its comparison, which meant one
    ticked row paid for the derivation twice and the count was a second answer
    to a question the write had already decided.  :func:`settle_transaction`
    RETURNS that answer now -- the shape ``transfer_service._settle.settle``
    already had -- so the rule has one caller and needs no door.

    **What it replaced was a reading of the COLUMN, and that over-reported by
    construction.**  ``reconcile_service`` counted rows whose ``actual_amount``
    changed -- but an envelope's close always writes that column
    (:func:`settle_from_entries` sets it to ``sum(entries)``), so every envelope
    tick incremented a count whose whole purpose is to tell a human's figure
    from a machine's.  Measured before the fix: a probe settling one envelope
    with no correction submitted logged ``corrected_count: 1``.  Ruling
    **R-FB**'s production measurement ("11 of 93 settled bills carry a
    hand-typed correction") is made of exactly this signal.

    **It is asked BEFORE the settle**, because :func:`settle_transaction`
    mutates the very figures it compares: the seam writes the settlement record
    and flips the status, and a row's own valuation answers from that record
    once it has settled (``row_valuation.fixed_contribution``).  The comparison
    is exact rather than approximate -- ``booked`` is :func:`settle_amount`'s
    own answer, which is the same expression the panel prefilled from.

    **The three-shape argument this paragraph used to make is GONE with its
    subject** (plan step X-au-d).  It reasoned about a cache refresh running
    between the resolve and the record, and about which side of that refresh
    the comparison stood on.  Nothing refreshes a cache here now, so there is
    one figure and one moment.

    Args:
        txn: The row about to settle, still in its pre-settle status.
        submitted: The figure a caller stated and who wrote it, or ``None``
            when nobody stated one.
        booked: What this settle would book absent a correction, resolved once
            by :func:`settle_amount` and threaded here rather than re-derived.

    Returns:
        True when the verb will RECORD *submitted* as a ``corrected``
        settlement.
    """
    return (
        submitted is not None
        and not settles_from_entries(txn)
        and submitted.amount != booked
    )


def settle_transaction(
    txn: Transaction,
    *,
    submitted: StatedFigure | None = None,
    settle_day: SettleDay | None = None,
    tender_account_id: int | None = None,
    press: Press | None = None,
) -> bool:
    """Settle one regular transaction -- what "the money moved" MEANS for a row.

    **Ruling R-FA's verb.**  The rule lived in a ROUTE branch
    (``mutations.py:_mark_done_regular``) until plan step X-f2-c2, which needs
    the reconcile panel's tick to settle a row the SAME way the grid's Mark
    Paid does.  The two alternatives R-FA rejected were the reconcile writer
    re-stating the amount rule -- this arc's own root cause 1, on a money rule
    -- and the tick calling the mark-done HTTP endpoint per row, which has no
    atomicity and no channel for a statement date.  So the rule left the route
    and both doors call it.

    Three acts, in this order and the order matters:

    1. **The amount, which is what the row is worth right now** (ruling
       **R-FE**, plan step X-aq).  An envelope-tracked row WITH entries settles
       at ``sum(entries)`` (:func:`settle_from_entries`), because its entries
       ARE the record of what it cost -- and its record stores NO figure at
       all, for that reason.  Everything else resolves ONCE
       (:func:`settle_amount`) and RECORDS what it booked.  A caller-supplied
       *submitted* figure -- a figure a HUMAN read off a statement -- is what
       the record states instead, and only if it differs from what the row
       would book anyway.
       **This act was TWO acts until plan step X-au-d**, and the deleted half
       is worth naming: a salary row's ``estimated_amount`` was a CACHE of a
       derivation (finding **N-224**) and this verb was the one moment the arc
       wrote it back, so act 1a refreshed the column before act 1b recorded the
       settle.  Such a row stores no figure now -- it DECLARES the definition
       that prices it -- so there is no cache to reconcile, no ordering between
       the refresh and the seam, and no way for the plan and the record to
       state one number twice.
       **The ``txn.purchases`` test is load-bearing** (and since plan step
       ``balance:X-bi-4a`` it is the whole predicate, ruling **R-BAL78**),
       and production says so: ``Kayla's Spending Money`` carries no
       purchases at all, so settling it from purchases unconditionally
       would book ``$0.00`` against its ``$100.00`` estimate.  **Why the
       rule is HERE and not at each door**: it decides money, three doors
       settle a row, and a door that picks its own
       figure is how one row comes to book two amounts depending on which
       control the user pressed.  **And why the plan and the record are two
       columns**: a machine's recompute and a human's correction are different
       facts, and until plan step X-au-c3 three subsystems read one column's
       NULL-ness to tell them apart; the record's SOURCE says it now (the
       covering movement's ``figure_source_id``, read through
       ``status_seam.recorded_settlement`` since plan step
       ``balance:X-bi-4b-1``).
    2. **The status**, through the single seam, so the transition is verified
       and the settle day stamped by the one door that owns both -- and, since
       plan step **X-bi-3a** (ruling **R-BAL39**), the COVERING MOVEMENT
       written: the seam mirrors a ``derived`` / ``corrected`` record as one
       ``transaction_entries`` row carrying the figure, who wrote it, the day
       and the link, so a bill's money is recorded the way an envelope's
       purchases already are (``status_seam._covering``).  Balance-neutral by
       ruling **R-FM**'s identity, which is why act 3 needs no new arm: the
       ledger reconcile walks ``txn.entries`` and posts the family at once.
    3. **The ledger**, reconciled LAST, so it reads the final amount rather
       than the estimate -- the discipline ``transfer_service.update_transfer``
       documents and the reason the reconcile is not at the status flip.

    **Why act 3 is inside this verb and not left to the caller.**  Every
    settle door must reconcile, and a door that forgets posts nothing while
    reporting success -- an argument a caller can get wrong is a defect, not a
    contract (Section 8).  ``carry_forward_service`` genuinely cannot use this
    verb: it settles a BATCH and must reconcile after its ``no_autoflush``
    block so ``_emit_balanced_entry``'s flush lands on the batch's index-safe
    final state, so it keeps calling :func:`settle_from_entries` directly and
    owns its own reconcile.  That is the layering: the primitive for a batch,
    this verb for a settle, and since plan step X-ap the batch is the ONLY
    non-caller -- the full-edit Status dropdown was the other one, and it was a
    defect rather than a layering choice.

    **It takes a settle DAY since plan step X-f2-c2's money commit**, which is
    the caller ruling **R-EC** was waiting for: the reconcile tick knows the day
    the statement covers, and stamping the user's today instead would date the
    money to when they got round to reconciling.  ``None`` leaves the seam's own
    rule in force -- the user's today on first entry to the settled band,
    preserved on re-entry -- which is what every other door means.

    Does NOT commit -- the caller owns the session boundary.

    **A verb owns its own preconditions** (:func:`reject_unsettleable`).  A
    first draft borrowed the refusal of a transfer's twin row from
    :func:`settle_from_entries`, a branch such a row never reached, and an
    adversarial review settled one leg of a pair through it, silently.  No
    row can be a twin since plan step ``balance:X-bi-6-4d-2`` deleted them
    (ruling **R-BAL166**) and plan step ``balance:X-bi-6-5`` the
    ``transfer_id`` column that named one.

    Args:
        txn: The transaction to settle.  A transfer is not one: it is one
            row of its own and settles through ``transfer_service``.
        submitted: What the row actually cost and WHO SAID SO, when the CALLER
            knows (:class:`~app.services.stated_figure.StatedFigure`; plan step
            **X-bi-3e-1**, ruling **R-BAL61**): ``typed`` from the reconcile
            panel's tick, the grid's Mark Paid and the popover (through
            :func:`._door.apply_requested_status`), ``observed`` from the
            statement matcher's transaction arm through the same door.
            ``None`` does NOT mean "keep the stored amount": it means "nobody
            stated one", and the settle then RECORDS what it resolved, on the
            ``derived`` basis with the ``resolved`` source.  Ruling **R-FB** is
            what gives the parameter its real callers: a BILL's tick may
            correct its amount, prefilled, and an envelope's close may not --
            the envelope branch ignores a stated figure outright, and the
            full-edit door refuses one on such a row rather than dropping it
            silently.  **It is named for what it IS rather than for a column**
            since plan step X-au-c3: it was ``actual_amount``, and the column
            of that name is gone -- a settled row records what moved on its
            covering movement, beside a ``figure_source_id`` that says whether
            this figure is why.
        settle_day: The civil day the money moved and HOW that day is known
            (:class:`app.services.settle_day.SettleDay`), when the CALLER knows
            it -- the reconcile tick's statement date on the ``asserted``
            basis, the matcher's bank day on ``observed``.  ``None`` derives it
            through the seam.  A day in the future or one supplied for a non-settled
            status is refused there (rulings **R-EJ** / finding **N-183**);
            the tick cannot reach either, because an assertion's own
            ``observed_on`` is already refused in the future by
            ``anchor_service``.
        tender_account_id: The account the money MOVED THROUGH -- the
            TENDER (plan step ``credit_card:CC-5-3``, ruling **R-CC15**: a
            bill charged to the card is settled with its covering movement
            on the card) -- when the CALLER names one: the "Paid from"
            picker through ``mark_done`` and the popover, the ONE door that
            moves a bill's payment between accounts (ruling **R-CC137**,
            developer 2026-10-04: *"A bill's payment then changes account
            only through 'Paid from'"*).  The two statement-driven doors
            name none.  They FORCED the statement's own account (ruling
            **R-CC15**) until plan step ``credit_card:CC-5-4a-5`` -- the
            reconcile panel's tick at leaf 5c-1 (ruling **R-CC126**), the
            matcher's transaction arm at leaf 5c-2a -- and each now offers a
            row that settles from its figure only where its payment, if it
            has one, is already on the statement's account, so the default
            below books it there (a row settling from its purchases takes
            the entries branch, which ignores a tender).  An
            ECHO of what the row records -- the popover's picker
            untouched, which always shows the recorded tender -- names
            nothing new and is dropped (``status_seam.tender_for_status``,
            the one echo rule, shared with the identity arm); anything else
            is gated here against the ROW's owner and the owner's cash-flow
            set by the one gate the purchase door uses
            (``movement_account.admitted_movement_account_id``), so a
            payment on a 401(k) or a savings account is unwritable and a
            foreign account is a 404, before any mutation.  ``None`` means
            nobody named one, and the seam books on its one default -- the
            kept record's account, else the row's own (ruling **R-CC42**:
            where the money moved is retained across a revert as a stated
            figure is).  **The entries branch ignores it**, exactly as it
            ignores *submitted*: an envelope's purchases are its record and
            each carries its own account; the popover renders no picker on
            such a row and the PATCH door refuses one.
        press: The save's :class:`~app.services.match_press.Press` (ruling **R-CC135**), over
            the bank lines the door's page named or what lets it stay silent
            (plan step ``credit_card:CC-5-4a-5``, rulings **R-CC81** /
            **R-CC127**): a settle that takes the row's kept payment off the
            books (a ``$0.00`` figure, its purchases) or re-points it frees a
            match, and the act refuses a line the page did not name.  The
            default, ``None``, says the page named none.

    Returns:
        Whether this settle booked a HUMAN's figure -- what the reconcile
        writer counts (finding **N-231**).  **Answered by the act itself since
        the developer's 2026-08-17 ruling**, which is the shape
        ``transfer_service._settle.settle`` already had one table over: the
        caller used to ask :func:`_is_correction` separately, which re-resolved
        the row's amount to make its comparison, so one ticked row paid for the
        paycheck engine twice and the count could in principle disagree with
        the write.  Asked by the verb, the two cannot differ and the figure is
        resolved once.  ``False`` for the envelope branch, which books its own
        entries and honours no submitted figure at all.

    Raises:
        ValidationError: On a row naming a transfer (one the database refuses
            to store) or a soft-deleted row, from the
            envelope branch's remaining preconditions, from an illegal
            transition, from the seam's settle-day refusals, or from the
            tender gate (an archived account, or one outside the owner's
            cash-flow set).  All are 400s at the route.
        NotFoundError: When *tender_account_id* names no account of the ROW's
            owner (the security response rule: one answer for "not found"
            and "not yours").  A 404 at the route.
        PostingError: From act 3, on a broken ledger invariant.  Deliberately
            NOT a sibling of ``ValidationError`` -- it must fail loud rather
            than render as a designed refusal.
    """
    # Checked FIRST and before any mutation, so a refused call leaves the row
    # untouched -- the ordering ``status_seam.apply_status_change`` uses for
    # its own three refusals, and for the same reason.  Nothing it reads
    # flushes (two of the row's columns, and for a deleted row's sentence one
    # more read, all under ``no_autoflush``), so a call refused here writes
    # none of a caller's staged state.  It also precedes the identity no-op
    # below, which answered first for a row already Paid: a replayed Mark Paid
    # on a row another tab had deleted returned quietly and the grid redrew
    # the deleted row as a live Paid chip (plan step ``credit_card:CC-5-4a-4``,
    # its review 9, measured 2026-09-24).
    #
    # **Everything below decides from the row as the request's owner lock
    # left it** (plan step ``balance:X-bn``, ruling **R-CC106**): whether it
    # settles from its purchases, and at what figure.  Decided from a read
    # taken before a racing purchase committed, a Groceries envelope with
    # nothing spent settled at its $300.00 plan while the companion's $12.34
    # purchase landed beside it -- $312.34 recorded against a $300.00
    # envelope, measured 2026-09-23.  The request's transaction took its
    # owner's write lock before it read any of the owner's data
    # (:mod:`app.db_transaction`), so the companion's purchase either
    # committed before this read, and the row settles at the purchases, or
    # waits and meets the purchase door's settled-row refusal.  Until that
    # step the row's own lock did it here (rulings **R-CC96**, **R-CC99**
    # (a)), and re-read ``is_deleted`` and the movements under it.
    reject_unsettleable(txn)
    # The tender's reading and its gate, before any mutation for the same
    # reason and ahead of the identity no-op below: a bad REFERENCE is refused
    # whatever the row's state, so a replayed settle naming a foreign account
    # is a 404 and never a silent 200.  The reading first
    # (``status_seam.tender_for_status``, the one echo rule): an account equal
    # to what the row RECORDS names nothing new and is not gated -- the
    # popover's picker always shows the recorded tender, an ARCHIVED card
    # included (the review of this leaf: a since-archived card's echo met the
    # gate's archived refusal and the documented Paid button refused a
    # tender nobody changed), and R-CC42 keeps a kept record where it is.
    # The status handed to the reading is the one this verb settles INTO, so
    # its non-settled refusal cannot fire here.
    tender = tender_for_status(
        txn, settled_status_id(txn), tender_account_id,
        tender_account_id_of(txn),
    )
    if tender is not None:
        tender = admitted_movement_account_id(txn, tender, movement="payment")

    # **A row ALREADY in the status this settle would move it to is an
    # idempotent no-op**, which is the rule
    # ``transfer_service._update.settle_transfer`` has always stated one table
    # over and this verb did not.  Without it a replayed mark-done -- a double
    # click, a stale tab, a re-POST of an empty body -- re-derived the row and
    # OVERWROTE its record: measured, a settle booking a human's ``$90.00``
    # against a ``$500.00`` plan came back ``$90.00`` on the ``derived`` basis,
    # so the figure survived and the stored answer to "did a human correct this"
    # was destroyed.  That answer is the entire reason the record names its
    # writer (finding **N-241**, ruling **R-FH**; the covering movement's
    # ``figure_source_id`` now, the row's ``settled_basis_id`` through
    # ``X-bi-4a``), and the reconcile writer's correction count is read from
    # this verb's return, so the replay also un-counted a correction that had
    # really been made.
    #
    # **``Settlement.from_settle`` closed that measurement independently** by
    # honouring a RETAINED ``corrected`` record, so the manual branch would now
    # survive the replay without this line.  What it still uniquely protects is
    # the ENVELOPE replay: ``settle_from_entries`` refuses an immutable row by
    # precondition, so a double-clicked Mark Paid on an already-Paid envelope
    # would 400 where nothing is wrong and nothing needs doing.  That is the
    # case ``TestAReplayedSettleIsANoOp`` pins, and it is pinned because this
    # return had no firing control anywhere in the suite until then -- all
    # 9,611 tests passed with it disabled, measured 2026-08-17.
    #
    # **It tests the row's own settled status, NOT the settled BAND**, and the
    # difference is a refusal: a first version asked ``status_id in
    # settled_status_ids()`` and thereby swallowed ``Paid -> Received``, an
    # ILLEGAL transition ``state_machine.verify_transition`` exists to reject,
    # turning a designed 400 into a silent 200.  Only the identity move is
    # nothing to do; every other settled-to-settled move still owes the state
    # machine its answer.
    #
    # Settling again is not how a settled row is changed: the figure is
    # corrected by reverting and re-settling (the record survives the revert,
    # ``status_seam.apply_status_change``), and the DAY by
    # ``status_seam.settle_day_for_status`` (ruling **R-ED**).
    if is_identity_move(txn, settled_status_id(txn)):
        return False

    correction = None
    if settles_from_entries(txn):
        settle_from_entries(txn, settle_day=settle_day, press=press)
    else:
        # The correction DECISION.  The echo rule -- a figure equal to what the row
        # would book anyway is not a correction -- is :func:`_is_correction`'s,
        # asked HERE and nowhere else (finding **N-231**): the reconcile
        # writer's telemetry reads this verb's RETURN rather than asking the
        # predicate a second time, which is the developer's 2026-08-17 ruling
        # and what closed finding **N-258**.  Writing an echoed figure would
        # populate a column that is
        # NULL on every uncorrected row, destroying the only signal that says a
        # human typed one, which is what ruling R-FB's own production
        # measurement is made of ("11 of 93 settled bills carry a hand-typed
        # correction").  That half is load-bearing rather than tidy: the
        # reconcile panel PREFILLS its amount box, so an untouched tick submits
        # the figure the row would have booked anyway.
        #
        # **Four doors reach it with a figure**, and naming them replaces a
        # sentence this comment carried from plan step X-ap ("the panel is the
        # ONLY caller") that stopped being true when the popover's figure was
        # threaded through ``apply_requested_status`` (2026-08-17) and the
        # matcher's transaction arm through the same door: the reconcile
        # panel's tick, the grid's Mark Paid, the full-edit popover and the
        # matcher.  Every one states who wrote the figure with it (plan step
        # X-bi-3e-1).  A justification naming a caller that does not exist is
        # the defect ruling R-EC deleted a whole parameter for; it is corrected
        # here rather than left to read as coverage.
        #
        # ONE basis for the whole act (developer ruling, 2026-08-17): the
        # figure this settle books and the echo rule's comparison are two
        # questions about one row against one derivation, and building a basis
        # for each ran the paycheck engine twice per settle.  It was THREE
        # questions until plan step X-au-d, whose third was a cache refresh
        # this verb no longer performs -- a derived row holds no cache.
        basis = derived_amount_basis(txn.account.user_id, txn.scenario_id)
        # What this settle BOOKS, read from the same published rule the
        # reconcile panel prefills from, so the figure offered and the figure
        # recorded cannot differ.
        #
        # **The RETAINED correction is inside that rule, and asking for it a
        # second time here was a duplication plan step X-au-d introduced and
        # then removed.**  This read ``held = honoured_correction(txn)`` beside
        # ``booked = resolved if held is None else held`` -- necessary while
        # the branch resolved through ``_manual_branch_figures``, which did not
        # consult :func:`fixed_settle_amount`.  It does now, and that
        # function's second arm IS the retained correction (plan step
        # X-au-c3), so the conditional could only ever choose between a value
        # and itself.  One rule, asked once.
        booked = settle_amount(txn, basis)
        correction = submitted if _is_correction(txn, submitted, booked) else None
        # Acts 1 and 2 in ONE call: what moved, who wrote it, and the day.
        # The record is written by the seam rather than here so the covering
        # movement keeps the ONE writer ``settled_on`` has (finding **N-185**'s
        # rule applied to the record beside it) -- two writers of one money
        # fact in one request is the shape this arc removes.
        #
        # ``recorded_settlement`` is what the row STILL carries from a settle it
        # has since been reverted out of: a revert releases the assertion and
        # keeps what moved, so a user who reverted in order to edit has not lost
        # the figure they read off their statement.  ``from_settle`` honours a
        # retained ``corrected`` one, which is what makes that round trip
        # lossless rather than merely slower to destroy the figure.
        apply_status_change(
            txn, settled_status_id(txn), settle_day=settle_day,
            settlement=Settlement.from_settle(
                booked, correction, recorded_settlement(txn), tender=tender,
            ),
            press=press,
        )

    posting_service.sync_transaction_postings(txn)
    return correction is not None


def settle_from_entries(
    txn: Transaction, *, settle_day: SettleDay | None = None,
    press: Press | None = None,
) -> None:
    """Settle a tracked-envelope transaction at sum(entries).

    The envelope PRIMITIVE: the three writes an envelope's close needs --
    ``status_id``, the settle day, and the settlement RECORD -- as a single
    source of truth.  It is reached two ways, and the split is
    :func:`settle_transaction`'s docstring: every DOOR settles through that
    verb, which chooses this branch when the row holds purchases (ruling
    **R-BAL78**: whatever its definition's flag says), while
    ``carry_forward_service._execute`` calls this directly
    because it settles a batch and owes its ledger reconcile a different
    moment (see ``docs/carry-forward-aftermath-design.md`` Option F).

    **An EMPTY entry list settles at ``Decimal("0")`` here and at the row's own
    estimate through :func:`settle_transaction`, and that difference is RULED
    rather than incidental** (ruling **R-FJ**, finding **N-230**).  The two are
    answering different questions.  This primitive's caller,
    ``carry_forward_service``, has ALREADY relocated the unspent money -- it
    rolls ``estimated - sum(entries)`` into the next period's row and then
    settles the source at what was spent -- so booking the estimate here would
    count the same dollars twice.  A DOOR has relocated nothing: pressing Paid
    on an envelope says the budget is finished, and booking ``$0.00`` would
    record that no money left the account while marking the row Paid.  The
    discriminator is therefore the CALLER's act, not the row, which is why the
    rule cannot live in a shared branch and why :func:`settle_transaction` gates
    its entries branch on ``txn.purchases``.

    Production carries both signatures, which is how the difference was found:
    of 9 settled entry-less envelopes, 8 were booked at their estimate
    (`$794.79`) through a door and 1 -- ``Kayla's Spending Money``, `$157.60` --
    at `$0.00` through carry-forward.

    Effect on *txn* (in place):
      - **NO figure is stored at all** (plan step X-au-c3).  The record is
        ``Settlement(None, None)`` and the seam writes no covering movement
        for it, because the row's own entries ARE the record:
        ``row_valuation.settled_figure`` sums them on demand.  It USED to set
        ``actual_amount = sum(entries)``, and dropping that copy is what deleted
        ``entry_service``'s re-derivation of a settled envelope's figure -- with
        one copy there is nothing for a reconciler to keep in step, and a
        purchase corrected later moves the close by exactly its own difference.
        What the close BOOKS is still ``sum(e.amount for e in txn.purchases)``
        -- the row's purchases, never the covering movement a revert kept
        (ruling **R-BAL68**; the ``purchases`` record withdraws it) -- which is
        ``Decimal("0")`` when the row holds no purchase -- see the ruling
        above.
      - ``status_id`` is set to ``DONE`` for expense transactions and
        ``RECEIVED`` for income transactions, matching the display
        convention used by ``app/routes/transactions.py:mark_done``.
      - ``settled_on`` is *settle_day*'s day when the caller supplied one, else the
        status seam's own rule: the user's today on the first entry into a
        settled status, preserved on a re-settle.

    **The day parameter is BACK, and this time it has a caller** -- the
    reconcile panel's envelope close, which knows the statement's date and must
    not date the money to the day the user got round to reconciling.  Ruling
    **R-EC** deleted the previous one (``paid_at``) because NO call site passed
    it and its docstring named a caller that never had; the test rule 13 sets is
    a real caller, not a plausible one, and :func:`settle_transaction` now
    threads this from ``reconcile_service``.  ``carry_forward_service`` still
    passes nothing, which is correct: a carried-forward envelope settles today,
    not on some statement's day.

    **It took an explicit ``paid_at`` until plan step X-f1, and that parameter
    was DEAD** (ruling R-EC, rule 13).  Its docstring named the carry-forward
    envelope branch as the caller that supplied it;
    ``carry_forward_service._execute`` calls this with no such argument and
    always has.  Measured across ``app/``: zero call sites passed it.  A knob
    with no caller and a false justification is exactly the speculative
    flexibility the coding standards forbid, so it went rather than being
    renamed -- the seam owns the day, and a caller that genuinely means "this
    settled on a different day" corrects it on the row afterwards (ruling
    R-ED).

    The function does NOT flush or commit -- the caller owns the
    session boundary so the settlement can participate in a larger
    atomic operation (e.g. the carry-forward batch).

    Preconditions (defensively validated, not assumed):

      1. Neither a row naming a transfer (one the database refuses to store)
         nor a soft-deleted row (:func:`reject_unsettleable`).  Both rules
         are shared with the two other public settle surfaces rather than
         restated here -- this
         helper's own wording of the transfer rule was a SECOND spelling of
         it (finding **N-233**).
      2. ``txn.tracks_purchases`` is True -- the row is purchase-tracked
         through its definition's ``is_envelope`` flag.  Envelope semantics
         are the
         contract this helper relies on; calling on a non-tracked row is
         a programming error and surfaces as a ``ValidationError``.
      3. ``txn.status`` is mutable (``status.is_immutable`` is False).
         The only mutable status in the current schema is ``Projected``;
         settling a row that is already Paid, Received, Cancelled or
         Credit is meaningless and indicates a caller bug.

    Args:
        txn: The Transaction to settle.  Must be attached to the
            current SQLAlchemy session so the entries relationship
            resolves correctly.
        settle_day: The civil day the money moved and HOW that day is known
            (:class:`app.services.settle_day.SettleDay`), when the caller knows
            it.  ``None`` leaves the seam to derive it -- the owner's today on
            the ``entered`` basis.  Passed straight through -- every refusal
            that guards it (a future day, a day beside a non-settled status) is
            the seam's, so this helper adds no second opinion about a value it
            does not own.
        press: The save's press, or ``None`` when its door named nothing:
            a ``purchases`` record takes a kept payment off the
            books, and the act asks (ruling **R-CC127**;
            :func:`settle_transaction`).

    Raises:
        ValidationError: If any precondition is violated.  The error
            message names the txn ID and the specific violated rule
            so the route layer can surface an actionable message.

    Returns:
        None.  Mutations are applied in place; the caller is
        responsible for committing the surrounding transaction.
    """
    # Preconditions are checked cheap-first so an early bailout never
    # triggers an autoflush of pending mutations on the txn.  Column
    # checks come before relationship accesses (which lazy-load and
    # therefore autoflush) to keep the failure path side-effect-free.
    # The shared pair reads two columns (and, for a deleted row's sentence,
    # one more read), none of which flushes, so it belongs at the front.
    reject_unsettleable(txn)
    # Resolved purchase-tracking check: the DEFINITION's ``is_envelope``
    # (``tracks_purchases`` accesses the template exactly as the prior guard
    # did; a row naming no definition answers False off its own
    # ``template_id``, so the cheap-first / autoflush-safe ordering holds for
    # it).
    if not txn.tracks_purchases:
        raise ValidationError(
            f"Transaction {txn.id} is not envelope-tracked; "
            "settle_from_entries requires individual purchase tracking "
            "(the definition's is_envelope).",
        )
    # Guard against settling an already-finalised row.  ``status`` may
    # be unloaded if the caller passed a detached or freshly-constructed
    # transaction; treat the missing relationship as a precondition
    # violation rather than silently coercing it to mutable.
    if txn.status is None or txn.status.is_immutable:
        status_label = (
            txn.status.name if txn.status is not None else "<unset>"
        )
        raise ValidationError(
            f"Transaction {txn.id} has an immutable status "
            f"({status_label!r}); settle_from_entries requires a "
            "mutable status (Projected).",
        )

    new_status_id = settled_status_id(txn)

    # Route the status mechanics (transition check, status_id, the settlement
    # record, status expire) through the single seam so this helper cannot drift
    # from the manual mark-done / PATCH / credit paths.  The day is the caller's
    # when it has one -- the reconcile tick's statement date -- and otherwise
    # None, which is the seam's "stamp the user's today on entering the settled
    # status and preserve an existing one".
    #
    # The record STORES NO FIGURE (plan step X-au-c3): its basis is
    # ``purchases``, and the row's own entries are what state the amount.  That
    # is what deleted ``entry_service``'s re-derivation of a settled envelope's
    # figure -- with one copy there is nothing for a reconciler to keep in step,
    # and a purchase corrected next week moves the close by exactly its own
    # difference with no second write.
    apply_status_change(
        txn, new_status_id, settle_day=settle_day,
        settlement=Settlement(amount=None, source=None), press=press,
    )

    log_event(
        logger, logging.INFO,
        EVT_TRANSACTION_SETTLED_FROM_ENTRIES, BUSINESS,
        "Envelope transaction settled at sum(entries)",
        # The owner, read off the ROW (plan step ``pay_calendar:C13-b``).  It
        # was ``txn.pay_period.user_id``, and the note here said the
        # relationship was already loaded by the caller so the read triggered
        # no autoflush -- true, and true of a load this site no longer needs.
        # ``reconcile_service._rows.outstanding_rows``' eager
        # ``joinedload(Transaction.pay_period)`` was KEPT for this one line;
        # its docstring carries what removing it now costs and what has still
        # not been established.  It is a read that STAMPS a log event rather
        # than one that refuses, so it was never one of finding **P75**'s
        # nineteen -- that census excludes it by name.
        user_id=txn.user_id,
        transaction_id=txn.id,
        new_status_id=new_status_id,
        # What the close BOOKS, computed for the log rather than read back off
        # the row: a ``purchases`` settlement stores nothing to read.
        # ``purchases_total`` answers ``Decimal("0")`` for an empty entry list,
        # which is the carry-forward "no spend, full rollover" case.
        settled_amount=str(purchases_total(txn.purchases)),
        settled_on=txn.settled_on.isoformat(),
    )
