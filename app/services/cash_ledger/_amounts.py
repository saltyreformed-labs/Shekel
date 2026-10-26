"""
Shekel Budget App -- Cash ledger: what ONE row is WORTH to checking.

The per-transaction valuation rules, and nothing that sums or folds them.  Given
a single row, these answer "how much of this hits the checking balance right
now?" -- the cash analog of the loan replay's
:class:`~app.services.loan_ledger.PaymentOutcome`, which answers the same
question for one loan payment (principal / interest / escrow).

**The VALUATION is here and the AMOUNT is next door.**  :mod:`._amount_source`
answers *what is this row's amount* -- the quantity the amount column holds or
would hold, by ruling R-FI's five rules.  This module answers *what is it
worth*, which composes that amount with an entered actual, an excluded status, a
soft delete and an envelope's purchases.  :func:`contributed_amount` is that
composition in one function and it took over from
``Transaction.effective_amount`` at plan step X-au-c2: a model property could not
resolve a derived amount -- it is a pure in-memory read, and a paycheck's
derivation needs the owner's whole pay-period set -- so the figure arrives as an
ARGUMENT a caller cannot forget.  :func:`contributions_by_id` is the batch a
reader with a row set uses, and
:func:`~app.services.row_valuation.settled_contribution` is the cheap accessor
for a reader whose rows have all SETTLED.  The BUDGET twin
of the first two is :func:`._amount_source.amounts_by_id`, which answers what a
row's amount IS rather than what it is worth (ruling E-21, plan step X-au-c2b).
*The cheap accessor had a budget twin too -- ``owned_amount`` -- and plan step
X-bu DELETED it: a public accessor answering "what is this row's plan" from the
``estimated_amount`` column is a second spelling of what
:func:`._amount_source.resolve_transaction_amount` answers, and the two parted
on a row a cutover had declared DERIVED (finding **BAL-462**).  Its body is now
rule 1's own arm.  The accessor was ``owned_contribution`` and carried the SAME
shape at its fall-through; plan step **X-bx** deleted that too (finding
**BAL-465**), and the two steps did not end the same way.  X-bu's reader wanted
a PLAN, so it was re-pointed at the resolver.  This one's readers ask what a
row's money DID, and a row that has not settled has done nothing -- so the
fall-through became a REFUSAL rather than a second producer, and the name
changed to state the precondition the seven readers had always relied on.*

**The arms that need no producer live one module DOWN, in
:mod:`app.services.row_valuation`** (plan step X-au-c2).  ``fixed_contribution``
(the status / soft-delete / settlement gate) and ``settled_contribution`` are
pure per-row reads.  The loan stack
needs the second of them and cannot name this package without raising pylint's
``cyclic-import``, because :mod:`._amount_source` reaches UP into
``loan_payment_service`` for rule 4's producer; that module's docstring carries
the measurement.  Of the two, only ``settled_contribution`` is re-exported from
this package (``__init__``'s ``__all__``); ``fixed_contribution`` is imported
here for the valuations below, an internal use rather than public surface.
*There were THREE until plan step X-bx.  ``own_figure`` -- the NULL refusal that
keeps the amount model total -- sat there because the accessor above spelled the
OWN column read at its fall-through; deleting that spelling left both remaining
callers inside :mod:`._amount_source`, so the leaf MOVED there and is private
(``_own_figure``).  A caller that means "this row's OWN stored figure" is now,
by construction, one of the amount model's own two arms.*  There is
still exactly ONE definition of each rule, which is the claim this module
exists to make.  What is genuinely inverted is that upward reach, and plan step
X-au-g owns unwinding it.

**THE READ-TIME REPAIR IS GONE, and its deletion is what this module's shape
now records.**  Three functions stood at the top of it -- ``live_override``,
``live_amounts`` and ``display_amounts_by_id`` -- and beneath them two
valuations, ``income_amount`` (which consumed the override) and
``_expense_amount`` (a one-line forward that existed for symmetry with it).
Every one of them was the same mechanism: a row stored a figure the app also
COMPUTED, so a lookup laid the computation over the column at read time and
wrote nothing back (finding **N-224**).  Plan step X-au-g-2c-2 deleted its LOAN
half by declaring every transfer shadow DERIVED; plan step **X-au-d** deleted
the SALARY half the same way.  With no stored figure left to supersede,
``display_amounts_by_id`` was ``amounts_by_id`` and ``income_amount`` was
``contribution_of`` -- two spellings of one walk, which ``CLAUDE.md`` rule 14
resolves by deleting a spelling rather than keeping them in step.  What the
callers ask now is the resolver directly.

Two rule families live here, split by the question they answer.

What a row is worth while it is still PROJECTED -- a reservation, money not yet
gone -- is :func:`contribution_of` for an ordinary row and
:func:`_entry_aware_amount` for one carrying purchases, the second being the
envelope reservation rather than a second amount rule.  What a still-projected
TRANSFER is worth to one of its two accounts is :func:`planned_leg_contribution`
over the parent's derived leg (plan step **X-bi-6a**, ruling **R-BAL13**):
the same figure rule 5 answered for the shadow row that leg replaced, read
from the parent without the shadow in between.

What money that really MOVED is worth is the other, and it is deliberately
neither of the above:

  * :func:`._cash_leg.movement_cash_leg` is a movement's whole figure in its
    parent's direction, signed by the parent's transaction type (ruling
    **R-BAL35**) -- what the cash WALK folds for every dated movement and the
    ledger WRITER books for it, through one function.  A settled ROW is
    worth nothing of its own since plan step ``balance:X-bi-4a`` (ruling
    **R-BAL80**): its money is its movements'.  The reservation above cannot
    reach a settled row (it filters to ``is_projected``), and an un-dated
    purchase under one is a movement in flight, a plan item beside the
    reservation rather than a leg of the row (ruling **R-BAL77**).
    ``_cash_leg.settled_cash_leg`` -- ``settled_contribution -
    Sigma(credit entries) - Sigma(dated purchases)`` -- outlived that step by
    one cut as the statement matcher's pricing of a settled row and is
    deleted (ruling **R-BAL81**: a settled row is worth what its covering
    movement moves, ``status_seam.covered_cash_leg``; ``bank_import``'s
    question, stated there), read by neither the walk nor the writer.

**Why they are one module (plan step D1c).**  They were split across two: the
override map's producer sat in the cash event sources while the four rules that
read its output sat inside ``balance_calculator``, a PRODUCER module, where
the fence had ruled all four explicit non-producers.  (The override map itself
is gone as of plan step X-au-d, above; the reason the valuations live together
is not.)  A producer module holding
the valuation rules is what stranded them (finding N-30) -- and
:func:`_entry_checking_impact` is the formula behind the grid-vs-savings
divergence (F-002 Pair C / E-25), which a sibling module documented itself as
"mirroring".  One home is what stops two producers agreeing by coincidence;
D1c deleted that mirror, so the formula now has a single caller and is private.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01): plain data
in, ``Decimal`` out; no Flask import, no writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# ``settled_contribution`` is imported for the package's public surface rather
# than for this module's own use: ``__init__`` re-exports it from here so a
# reader takes it from the same place as the valuations (see the module
# docstring).  Its budget twin ``owned_amount`` was re-exported beside it until
# plan step X-bu deleted the accessor.
# Pylint: ``unused-import`` -- the re-export IS the use; ``__init__`` names it.
from app.services.row_valuation import (  # pylint: disable=unused-import
    fixed_contribution,
    leg_fixed_contribution,
    settled_contribution,
)
from app.utils.balance_predicates import is_projected

from app.services.transfer_legs import TransferLeg

from ._amount_source import (
    AmountBasis,
    resolve_transaction_amount,
    resolve_transfer_amount,
)


@dataclass(frozen=True)
class ReconciledThrough:
    """The day through which an account's movements are inside a declared balance.

    Ruling R-DH (a): an assertion is the CLOSING balance for its civil day, so
    a movement dated at or before that day is inside it by definition.

    **It answers for the MODELLED side now, and that narrowing is plan step
    X-f3a-1's** (ruling **R-FL**).  This was "the ONE statement of the question
    this whole arc turns on", and for a CASH line it no longer is: whether the
    bank showed a transaction or a purchase is a RECORDED fact, asked through
    :class:`~._clearing.StatementCoverage`, because the developer's own bank
    exports measured the date comparison wrong on 70% of matched movements.
    What is left here is the case R-FL calls not an exception -- a payroll
    contribution (R-Z) and a modelled accrual (R-L / R-Y) are not lines anyone
    can tick, so the assertion legitimately outranks the model and the question
    really is "is this payday after the latest assertion" -- plus the posted
    self-heal's cost guard, which grades journal-entry DATES rather than lines
    and says so at its own site.

    An adversarial review once found a seventh statement of the rule hiding
    behind a loose date (``balance_at._asset_contributions``: a payroll
    contribution on a payday the assertion already covers is money the asserted
    balance contains, and modelling it again double counts); that consumer is
    one of the two this type now exists for.

    **What is NOT fenced, stated because a fence whose limits are unstated
    reads as stronger than it is.**  :attr:`~._events.CashAnchorFact.observed_on`
    and :attr:`~._events.CashSourceFact.settled_on` are still plain ``date``
    fields -- they have to be, to key a day, sort a stream and date a journal
    entry -- so ``x <= fact.observed_on`` compiles today in any new module.
    That is the shape a lint checker over the assertion-day vocabulary WOULD
    catch, and this type would not: the two fences are complementary rather
    than substitutes (``anchor_settle_partition.md`` Section 14.5).

    **It is a TYPE rather than a bare date because the mistake it prevents is
    the one that cost production ``$4,001.42``.**  The question had FOUR
    implementations when
    ``docs/audits/balance_architecture/archive/anchor_settle_partition.md`` was
    written, three of them comparing different things in different units, and
    the plan's answer was a pylint checker that would flag a fifth.  A checker
    cannot see through ``earliest <= latest``, which is the very site finding
    N-133 / F4 was about, so it would have fenced everything except the one
    with the history.  This class fences it structurally instead: with no
    ordering methods, ``settled_on <= reconciled_through`` raises
    ``TypeError`` rather than silently answering the question a fifth way.
    Asking it correctly and asking it wrongly stopped being the same keystroke.

    A caller that genuinely needs the raw civil day -- an SQL bound, a rendered
    caption -- reads :attr:`observed_day` and says so at the call site.  That
    is the deliberate escape hatch, and naming it is the point: reaching for it
    is visible in review, where a ``<=`` was not.

    **Why the day and not the instant, measured.**  Neither instant available
    is a fact about money: ``Transaction.paid_at`` WAS ``db.func.now()`` at the
    click (deleted at plan step X-f1) and an assertion's ``created_at`` is
    when it was typed.  So the
    instant partition asked "which button was pressed first" and answered a
    question about cash with it.  On production 2026-07-31 an ordinary session
    -- read the bank, enter ``$1,307.66``, tick off what cleared -- recorded
    three already-cleared payments in the NINE SECONDS after the assertion and
    subtracted ``$4,001.42`` a second time, rendering ``-$4,021.37`` against a
    true ``-$19.95``.  Across four months of that account, 65 of 139 settled
    rows (``$19,602.13`` gross) were classified by click order, and the
    correction the model was forced to plug at each assertion totalled
    ``$40,554.34`` gross / ``-$6,998.90`` net against ``$15,367.94`` /
    ``-$940.06`` under this rule.  The instant partition's own stated evidence
    was ONE 2026-07-25 pair (an anchor at 12:57 UTC, two expenses at 13:07,
    ``$108.15`` a date-keyed rule absorbs -- finding cash D1); scored over the
    whole account rather than that pair, the day rule books a SMALLER plug at
    that very assertion (``$39.27`` against ``$68.88``), because the rows it
    absorbs were overwhelmingly recorded late rather than cleared late.

    **The residual is stated rather than hidden.**  A payment that genuinely
    clears AFTER the balance was observed on the same day is absorbed anyway,
    and the projection reads high until the next assertion.  It is bounded
    (median ``$184.55`` a day against the ``$4,161.47`` the instant rule
    produced on 2026-07-31) and it self-corrects at the next assertion.

    **What removes it is an OBSERVATION, not a second derived date.**  With
    both real dates recorded, a movement made after the balance was read still
    carries the same civil day as one made before it, so no rule comparing two
    dates can tell them apart (``anchor_settle_partition.md`` Section 10.3).
    The guess ends only where the user says what their statement showed --
    which plan step S1-c built for PURCHASES
    (``reconcile_service.record_settled_days``) and which a bank import would do
    for settles.  Until then this is the best available guess on the settle
    side, and saying so is the honest form of it.

    Attributes:
        observed_day: The civil day the account's latest balance assertion is
            the closing balance for (``AccountAnchorHistory.observed_on``), or
            ``None`` when no balance has ever been declared for it.  Read it
            only to render or to bound a query; to ask whether a movement is
            inside the balance, call :meth:`covers`.
    """

    observed_day: date | None

    def covers(self, event_day: date | None) -> bool:
        """Return whether an event dated *event_day* is inside this balance.

        Ruling R-DH (a): an assertion is the closing balance for its civil day,
        so a movement dated at or before that day is inside it.

        **Total in both the argument and the boundary**, so it is a rule rather
        than one with a precondition each caller must remember.  A ``None``
        *event_day* is a purchase whose posting day has never been observed --
        still outstanding, whatever any balance says (ruling R-DH (d) as
        restated at plan step S1-c: the engine never guesses a posting day).  A
        ``None`` :attr:`observed_day` is an account that has never had a
        balance declared, so there is nothing for anything to be inside of.

        **Totality is what the remaining callers need, and the paragraph that
        used to be here described callers this type no longer has.**  It
        explained how a ``False`` behaved inside the two walks' ``while`` loops
        -- loops plan step X-f3a-1 deleted, along with every cash caller of this
        method.  What asks it now is the modelled contribution feed (R-Z), for
        which a ``None`` on either side means "not inside any declared balance",
        which is the answer wanted and needs no precondition remembered.

        Args:
            event_day: The civil day the money moved -- a settled row's or a
                purchase's ``settled_on`` -- or ``None`` when it has not been
                observed.

        Returns:
            True when the event is already inside the declared balance.
        """
        if event_day is None or self.observed_day is None:
            return False
        return event_day <= self.observed_day


def contributed_amount(txn, resolved: Decimal) -> Decimal:
    """Return what *txn* contributes to a balance, given its RESOLVED amount.

    **The replacement for ``Transaction.effective_amount``** (plan step
    X-au-c2), and the argument is the whole difference.  That property was a
    pure in-memory read with four arms, the last of which was "the stored
    ``estimated_amount``" -- and under the amount model a derived row stores
    none, so it could not answer at all (ruling **R-FI**).  It could not be
    taught to: resolving a paycheck needs the owner's whole pay-period set --
    four of the engine's judgements read it (**N-390**), the biweekly rounding
    residue that was this sentence's reason having been deleted at plan step
    balance:X-aw -- so no per-row property can hold the derivation.

    Taking the figure as an ARGUMENT is what a caller cannot forget.  A property
    is free to read and answers whatever it can; this signature does not exist
    without a resolved figure, and the only way to get one for a row set is
    :func:`contributions_by_id`, which builds the basis the resolver requires.

    Args:
        txn: The row being valued.
        resolved: What its amount resolves to
            (:func:`~._amount_source.resolve_transaction_amount`).

    Returns:
        ``0`` for a row that contributes nothing, what the row RECORDED as
        having moved once it has settled, else *resolved*.
    """
    fixed = fixed_contribution(txn)
    return resolved if fixed is None else fixed


def contributions_by_id(rows, basis: AmountBasis) -> dict[int, Decimal]:
    """Return ``{transaction_id: what the row contributes}`` for *rows*.

    **The BATCH valuation every reader that can see a still-projected row uses**
    (plan step X-au-c2).  It values each row through
    :func:`~app.services.row_valuation.fixed_contribution`, resolving only the
    rows that reach the amount model at all -- so the paycheck engine runs once
    per read pass rather than once per row (finding **N-228**).

    **It TAKES the basis rather than building one** (plan step X-au-c2b).  A
    basis is pinned to an owner and a scenario, not to a row set, so a caller
    that also needs the budget map (:func:`~._amount_source.amounts_by_id`)
    asks both against ONE -- which is how a single request stopped running the
    paycheck engine once per row set (findings **N-268**, **N-269**).  A caller with a
    read pass passes ``ctx.amounts()``; one without builds its own with
    :func:`~._amount_basis.derived_amount_basis`, the interim that goes site
    by site as each producer takes its pass.

    That ordering is the point rather than an optimisation: an excluded row is
    worth ``$0.00`` and its derived amount has no producer to answer it, so a
    valuation that resolved first and gated afterwards would 500 on a row nobody
    is counting.

    Every id in *rows* appears in the result, so a caller indexes it with ``[]``
    and a row it forgot to price raises a ``KeyError`` where it is read.  There
    is deliberately no ``.get(id, default)`` shape: a default here is a
    fabricated figure in a money path.

    Args:
        rows: The loaded rows to value.  They may span accounts -- the basis is
            keyed on the owner, not on one account -- but every one must belong
            to *basis*'s owner and scenario.
        basis: The read pass's :class:`~._amount_source.AmountBasis`.

    Returns:
        ``{transaction_id: Decimal}`` covering every row.

    Raises:
        AmountUnresolvable: From the resolver, for a row whose rule cannot
            answer.  A refusal is never a fallback (see
            :mod:`app.services.cash_ledger._amount_source`).
        UnsupportedStateError: From the resolver, for a salary-linked row
            whose profile is in a state the tax law does not list (plan step
            salary:X-at-3).
    """
    return {row.id: contribution_of(row, basis) for row in rows}


def contribution_of(txn, basis: AmountBasis) -> Decimal:
    """Value one row against *basis*, resolving it only if it needs resolving.

    The per-row body of :func:`contributions_by_id`, split out so the gate and
    the resolve are one expression rather than a comprehension with a
    conditional in it.

    Args:
        txn: The row being valued.
        basis: The basis :func:`contributions_by_id` built over its whole set.

    Returns:
        What the row contributes.
    """
    fixed = fixed_contribution(txn)
    if fixed is not None:
        return fixed
    return resolve_transaction_amount(txn, basis)


def leg_contributions_by_key(
    legs, basis: AmountBasis,
) -> dict[tuple[int, int], Decimal]:
    """Return ``{leg.cell_key: what the leg contributes}`` for transfer legs.

    :func:`contributions_by_id`'s twin for a display reader's transfer legs
    (leaf ``X-bi-6-1b``), keyed by
    :attr:`~app.services.transfer_legs.TransferLeg.cell_key` so the one
    ``contributions`` map the dashboard's bills and the calendar's day cells
    index holds rows and legs side by side.  One :func:`leg_contribution_of`
    per leg, exactly as its sibling is one :func:`contribution_of` per row;
    every key in *legs* appears, so a caller indexes it with ``[]``.

    Args:
        legs: The :class:`~app.services.transfer_legs.TransferLeg` values a
            surface is about to render, records loaded, their parents loaded
            with :func:`~app.utils.amount_relationships.transfer_pricing_load_options`.
        basis: The read pass's :class:`~._amount_source.AmountBasis`.

    Returns:
        ``{(transfer_id, account_id): Decimal}`` covering every leg.

    Raises:
        AmountUnresolvable: From the resolver, for a parent whose rule cannot
            answer.  A refusal is never a fallback.
    """
    return {leg.cell_key: leg_contribution_of(leg, basis) for leg in legs}


def leg_contribution_of(leg: TransferLeg, basis: AmountBasis) -> Decimal:
    """Value one leg against *basis*, resolving its parent only if it must.

    :func:`contribution_of`'s twin over a transfer leg (leaf ``X-bi-6-1b``),
    the same two steps in the same order: what the leg is worth WITHOUT a
    producer -- ``0`` for a parent that does not contribute, the figure its
    transfer stores once it has settled
    (:func:`~app.services.row_valuation.leg_fixed_contribution`) -- else the
    parent's resolved amount through :func:`planned_leg_contribution`, the
    ONE plan-half producer the cash fold prices a leg with.  A settled
    display leg therefore reads its transfer's stored figure, where the
    fold's legs are never settled; on a still-planned leg the two answers
    are one call.

    Args:
        leg: The :class:`~app.services.transfer_legs.TransferLeg`.
        basis: The read pass's :class:`~._amount_source.AmountBasis`.

    Returns:
        What the leg contributes to the account it is on, as a magnitude;
        ``leg.is_income`` says which way it moves the account.
    """
    fixed = leg_fixed_contribution(leg)
    if fixed is not None:
        return fixed
    return planned_leg_contribution(leg, basis)


def planned_leg_contribution(
    leg: TransferLeg, basis: AmountBasis,
) -> Decimal:
    """Return what one leg of a still-projected transfer is worth to its account.

    The plan-half valuation of a transfer (plan step **X-bi-6a**, ruling
    **R-BAL13**), and the ONE spelling of it: the cash fold's reduction
    (:func:`._flows.sum_projected`), the loan forward plan's PLANNED tier, the
    amortization feed and both investment contribution feeds all price a leg
    here.  It is the parent's resolved amount -- rule 1, rule 3 or rule 4 by
    :func:`~._amount_source.resolve_transfer_amount` -- which is exactly what
    amount rule 5 answered for the shadow row the leg replaced, with the
    shadow taken out of the walk.

    **It composes NOTHING, and the difference from :func:`contribution_of` is
    the whole design.**  A transaction's worth composes its amount with a
    settlement record, an excluded status and an envelope's purchases; a leg
    has none of those.  It is a PLAN by construction -- the loader admits only
    live still-Projected parents -- so there is no record to prefer, and a
    transfer carries no purchases, so there is no reservation to hold back.
    What a SETTLED transfer moved is the one figure it stores
    (:attr:`~app.models.transfer.Transfer.settled_figure`): the cash fold
    books it per side on that side's dated record
    (:func:`._cash_leg.movement_cash_leg`), and a display leg reads it
    through :func:`~app.services.row_valuation.leg_fixed_contribution` --
    never here.

    Args:
        leg: The :class:`~app.services.transfer_legs.TransferLeg`.
        basis: The read pass's :class:`~._amount_source.AmountBasis`; only a
            derive-mode loan payment's parent reads it.

    Returns:
        The parent transfer's resolved amount, as a magnitude; the leg's
        ``is_income`` says which way it moves the account.

    Raises:
        AmountUnresolvable: When the parent's own rule cannot answer.  A
            refusal is never a fallback (see :mod:`._amount_source`).
    """
    return resolve_transfer_amount(leg.transfer, basis)


def _entry_checking_impact(entries, estimated_amount: Decimal) -> Decimal:
    """An envelope's UNSPENT BUDGET: what its plan still holds back.

    The core of the entry-aware reduction, with exactly ONE caller:
    :func:`_entry_aware_amount` below, which owns the empty-entries
    short-circuit.

    **It is private, and D1c is what made that the honest answer.**  It was
    public for one documented reason -- ``balance_resolver`` held a second copy
    of the reduction (``_entry_aware_amount_dated``) and reached in here for the
    shared bucketing so the two paths "could not drift between the two balance
    paths".  D1c deleted that copy, so there is no second path to keep in step,
    and a public name justified by a caller that no longer exists is the
    stale-rationale shape finding N-30 is about.  Being private also retires its
    W9909 ruling -- structure doing what a fence entry was doing, which is the
    whole point of Phase D.

    One expression::

        impact = max(estimated_amount - sum(entries), 0)

    -- the budget less EVERY purchase recorded against it, debit or card,
    posted or not, floored at zero.  A purchase already recorded is not
    budget still to spend, whichever account its money leaves through and
    whether or not the bank has been seen to take it.

    **This was a three-bucket reservation through plan step ``X-bi-3e``, and
    ruling R-BAL77 is what reduced it** (plan step ``balance:X-bi-4a``).  It
    read ``max(E - P - C, U)`` -- the budget less the POSTED debits (already
    cash movements of their own, ruling **R-FM**) and the card purchases
    (which leave through a CC Payback sibling), floored at the UNPOSTED
    debits, so an envelope could never reserve less than the purchases nobody
    had yet seen leave.  That floor was the projection's only home for an
    un-dated purchase, and it lived inside the PROJECTED row's valuation --
    which is why a purchase under a CLOSED envelope had to be booked by the
    row's own leg on the close day instead, a second home for the same
    dollars.  The identity this docstring already carried is what split
    them::

        max(E - P - C, U)  ==  U + max(E - P - C - U, 0)
                           ==  U + max(E - sum(entries), 0)

    -- exact for EVERY sign of ``U`` (a refund is a negative purchase, ruling
    **bank_import:R-II**), checked exhaustively over the four terms.  The
    first term, ``U``, is now the account's movements IN FLIGHT
    (:func:`~._events.in_flight_movements`), one plan item per un-dated
    purchase whatever its parent's status; the second is this function.  So
    an envelope budgeting `$100.00` with a `$120.00` posted purchase and a
    `$40.00` refund not yet posted holds back ``max(100 - 80, 0) = 20.00``
    here and ``-40.00`` in flight: the refund arrives (`+40.00`) and `$20.00`
    of the budget is still expected to leave -- the same ``-20.00`` the
    three-bucket form answered, in two items that each say what they are.

    **What this does NOT ask, and why**: whether a purchase has POSTED.  That
    question moved out of here twice before it left entirely -- a stored
    ``is_cleared`` boolean written by a bulk UPDATE at every true-up, then a
    derivation against the account's latest asserted day (ruling R-DH (d)),
    then the RECORDED statement (ruling R-FL) -- and ruling R-FM made a
    posted purchase a cash movement of its own in the walk, on its own day,
    where :class:`~._clearing.StatementCoverage` asks which statement cleared
    it.  A purchase not yet posted is a movement in flight, held by the plan
    beside this.  So this reduction asks one fact about the row in front of
    it -- what has been recorded against its budget -- and no fact about the
    account or the bank.

    This function sees whatever entry set it is handed and applies the
    reduction to all of it.  Short-circuiting an empty set belongs to the
    caller, and there is exactly one, so that decision is made once rather than
    kept in step across two paths.

    Args:
        entries: An iterable of entry rows, each exposing ``amount``
            (Decimal).  The caller is responsible for short-circuiting an
            empty sequence before calling.
        estimated_amount: Decimal -- the transaction's budgeted amount,
            the ceiling the recorded purchases reduce.

    Returns:
        Decimal -- the budget this envelope still holds back from the account,
        never negative.
    """
    spent = sum((entry.amount for entry in entries), Decimal("0"))
    return max(estimated_amount - spent, Decimal("0"))


def _entry_aware_amount(txn, basis: AmountBasis) -> Decimal:
    """Compute the checking-balance impact for a single expense transaction.

    For projected expenses with entries (loaded eagerly or lazy-loaded on
    demand), the row is worth its UNSPENT BUDGET -- the plan less every
    purchase recorded against it, floored at zero
    (:func:`_entry_checking_impact`)::

        checking_impact = max(estimated_amount - sum(purchases), 0)

    Semantics (plan step ``balance:X-bi-4a``, ruling **R-BAL77**):
      - A purchase already recorded is not budget still to spend, whichever
        account its money leaves through and whether or not the bank has
        been seen to take it, so every purchase reduces the reservation.
      - Where the purchase's money IS depends on one fact about the
        purchase: a DATED one is a cash movement of its own in the settled
        stream and the ledger, on its own day (ruling **R-FM**, plan step
        X-f3b); an UN-DATED one is a movement in flight, a plan item of its
        own that the projection holds from tomorrow
        (:func:`~._events.in_flight_movements`).  Neither is this row's to
        hold, so the row holds only what is unspent.
      - A card purchase reduces the reservation like any other -- the
        envelope's budget is spent -- and its money leaves later through
        its CC Payback sibling, which is why the settled stream and the
        in-flight tier both leave it out.

    Example (the user's grocery bug):
      est = 500, three debit purchases summing to 462.34, all confirmed
      against a statement whose balance the user then entered.
      checking_impact = max(500 - 462.34, 0) = 37.66, the remaining budget
      to hold back now that the ledger carries the first three purchases as
      movements of their own.

    **The three tiers always sum to what the row costs**, which is the
    property ruling R-FM turns on: the dated purchases are in the ledger at
    their own days, the un-dated ones are in flight, and this reservation
    holds the rest; an envelope's CLOSE books nothing of its own (its row
    leg was ``sum(entries) - credit - posted_debit`` through ``X-bi-3e``, the
    un-dated purchases on the close day, and ruling R-BAL77 reads those as
    in flight instead).  So recording a purchase and truing the anchor up by
    the same amount still cannot move the projected end balance (ruling R-DH
    (c)) -- and it stops depending on the anchor RESET dropping the balance
    by exactly what the reservation released, which is what finding
    **N-274** measured and what the cutover (X-f3c) removes.

    Seam removed (Commit 5 / CRIT-01 / F-009 / E-25): the pre-Commit-5
    implementation guarded the entry formula behind an
    eager-load presence check on the relationship (the ``entries``
    key in the SQLAlchemy instance dict), and returned
    ``txn.effective_amount`` whenever that check missed.  That
    silently degraded to the non-entries-aware value whenever the
    consuming query had not issued
    ``selectinload(Transaction.entries)``.  Symptom #1 ($160 on grid
    vs $114.29 on /savings for the same data) is exactly that seam in
    production: the grid eager-loaded entries and computed the
    reduction; /savings did not and got back ``estimated_amount``
    unchanged.  E-25's correction put the eager load inside the
    LOADER rather than leaving it to each consumer, and plan step X-g4b
    left that property where the fold reads: every row this rule is
    handed comes from
    :func:`app.services.cash_ledger._facts._unwindowed_contributing_rows`,
    which issues ``selectinload(Transaction.entries)`` for both halves
    of the event stream, so this function never sees an unloaded
    relationship from a routed caller.  The remaining
    ``getattr(txn, "purchases", ())`` access below covers two safe cases:

      * **Not-yet-routed ORM callers** (savings/accounts/calendar/
        year-end/investment/retirement, fixed in Commits 6-9): the
        SQLAlchemy descriptor lazy-loads the relationship.  The
        caller now gets the CORRECT entries-aware value with one
        extra SELECT per transaction (acceptable for the transition;
        the producer routing eliminates the extra query).
      * **Non-ORM test fakes** with no ``purchases`` attribute:
        ``getattr`` returns the default ``()``, the empty-entries
        early return fires, and the function returns
        ``effective_amount`` -- the same behavior pre-Commit-5 had
        for test fakes.

    **The reservation is over the row's PURCHASES, never its whole family**
    (plan step ``balance:X-bi-3e-2``, ruling **R-BAL68**).  A settled row's
    covering movement -- the payment row the status seam writes for the
    record -- sits in ``entries`` too, and since that step a revert KEEPS it
    un-dated under a Projected row; read as a purchase it would spend the
    budget with a close the owner withdrew: a `$100.00` envelope reverted
    after a typed `$120.00` close would reserve nothing, where a reverted
    row is worth its PLAN (developer, 2026-08-17).  :attr:`~app.models.
    transaction.Transaction.purchases` is the entries less that mark, so the
    reduction below sees what a person recorded and nothing the seam did;
    the in-flight tier leaves the mark out by the same reading.

    What is no longer possible: the same Projected envelope expense
    yielding two different values for two different consumers based
    purely on whether their query happened to ``selectinload``.

    **There is no as-of window, and the reason is what an ENTRY is (plan step
    X-c2c1, ruling R-M).**  This carried an optional date bound (E-27 / HIGH-02
    / W-277) that dropped entries dated after the reader's now, so a purchase
    that had not happened could not clear the reservation early.  Ruling R-M
    answered that at the SOURCE instead: an entry RECORDS a purchase that
    happened, so plan step X-c0 refuses a future purchase date at both write
    doors (:func:`app.services.entry_service._reject_future_purchase_date`)
    -- and a purchase that happened belongs in the reservation whatever date the
    reader is asking from.  What a row is WORTH is a function of the row, as
    :func:`._cash_leg.cash_leg_of` beside it already is; the reader's clock decides
    WHEN the row lands (ruling R-G's clamp, in the seam's fold), never what it
    is worth.

    Two measured facts, so the deletion is not read as merely tidy.  It moves
    nothing: no stored purchase is dated after any reader's now -- the write
    guard bounds every row at ``display_today()``, which is never after the UTC
    ``date.today()`` a :class:`~app.services.balance_at.BalanceContext` pins by
    default, and zero rows in either database carry a future date (0 of 74 and 0
    of 47, re-verified 2026-07-26).  And the only read it could ever have
    changed is a HISTORICAL one, whose plan is TODAY's still-Projected rows
    clamped forward rather than the plan as it stood then -- so windowing their
    entries was a partial as-of purity inside a tier that has none.

    **The R-M re-ruling of 2026-08-01 did not bring that window back, and plan
    step X-f3b is what keeps it out.**  ``settled_on`` -- the day the bank was
    seen to take the money -- was unbounded ABOVE while a purchase was not a
    cash movement, because a future day was then the conservative direction: the
    purchase stayed outstanding and the whole budget stayed reserved.  Ruling
    **R-FM** inverts that, and the inversion is exactly the shape
    ``status_seam.reject_future_settle_day`` refuses on a transaction: a future
    posting day would release the reservation NOW and book the money later,
    putting already-spent money back into today's projection.  So the bound
    moved to the write door
    (:func:`app.services.entry_service._reject_future_posting_day`) rather than
    a clock arriving here.  A purchase the bank has not taken yet leaves the day
    NULL, which is what that state has always meant.

    **The budget this holds back is the RESOLVED amount, not the stored column**
    (plan step X-au-c2).  ``estimated_amount`` is what an envelope's budget was
    read from until the amount model made it NULL for a derived row, and an
    envelope generated by a recurring definition is exactly such a row once the
    template cutover (X-au-e) lands -- so the ceiling the three buckets reduce is
    :func:`~._amount_source.resolve_transaction_amount`'s answer, which for a row
    that owns its figure IS that column.  It is the same figure
    :func:`contributed_amount` would return for the row absent entries, so the
    two arms of this function cannot come to price one row two ways.

    Args:
        txn: A Transaction object.  The ``entries`` relationship may
            be eager-loaded (canonical producer), unloaded
            (transitional caller; lazy-loads on demand), or absent
            (test fake).
        basis: The account's :class:`~._amount_source.AmountBasis` -- the amount
            basis every arm prices through.  It carried the account's
            :class:`~._clearing.StatementCoverage` beside it until plan step
            X-f3b, in a ``ProjectedBasis`` wrapper; the reservation is the
            fact that needed it, and it no longer asks (see
            :func:`_entry_checking_impact`), so the wrapper went with it.

    Returns:
        Decimal -- the amount this transaction contributes to checking
        balance.
    """
    # ``getattr`` with a default of ``()`` handles both unloaded ORM
    # relationships (the derivation lazy-loads ``entries`` via the session)
    # and non-ORM fakes (no attribute defined).  The empty-tuple default
    # passes the falsy check below, mirroring the original empty-list
    # short-circuit and keeping non-ORM tests stable.
    entries = getattr(txn, "purchases", ())
    # This check stays AHEAD of ``is_projected`` and that ordering is
    # load-bearing, not stylistic: ``is_projected`` resolves a ``ref_cache`` id,
    # which is work an entry-less row has no reason to pay for.
    #
    # **The reason it used to give was that ``is_projected`` raises on a non-ORM
    # fake, and plan step X-au-c3 voided it**: every valuation now asks the
    # STATUS whether a row is worth what it RECORDED or what it PLANS, so a row
    # carrying no ``status_id`` is one no valuation could ever see.  The
    # ordering is graded by a call-counting spy instead of by a missing
    # attribute, and that spy still fails when the two guards are swapped --
    # verified by making the swap.
    if not entries:
        return contribution_of(txn, basis)

    # Only apply the entry formula to projected transactions.
    # Settled, cancelled, and credit statuses are already handled
    # correctly by the valuation (zero for excluded statuses,
    # actual_amount for settled statuses).  Routed through the
    # centralized ``is_projected`` predicate (D6-09 / MED-02) so
    # this entry-formula gate cannot drift from the other
    # Projected-only filters in this package and in the balance
    # resolver.
    if not is_projected(txn):
        return contribution_of(txn, basis)

    # Hold back what is unspent.  The reservation formula lives once, in
    # ``_entry_checking_impact`` (E-27).
    return _entry_checking_impact(
        entries, resolve_transaction_amount(txn, basis),
    )
