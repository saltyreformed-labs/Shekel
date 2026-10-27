"""
Shekel Budget App -- Cash ledger: WHERE one row's amount comes from.

The TOTAL dispatch behind ruling **R-FI** (plan step **X-au-b**): a row's amount
is either its OWN -- a human authored the figure, or the money already moved --
or it is DERIVED, and a derived amount is not stored at all.  Four rules, one
per source, each delegating to the producer that already answers it.  Given a
row, :func:`resolve_transaction_amount` answers what its amount column holds or
would hold, and REFUSES rather than falling back for a row it cannot place.

**This is the sibling of :mod:`._amounts`, not a second copy of it, and the two
answer different questions.**  This module answers *what is this row's amount* --
the quantity ``budget.transactions.estimated_amount`` and
``budget.transfers.amount`` carry, and the one plan step X-au-c1 made NULLABLE.
:mod:`._amounts` answers *what is this row worth to checking*, which composes
that amount with an entered actual, an excluded status and an envelope's
purchases.  Splitting them by question is what the package already does
(:mod:`app.services.cash_ledger` docstring's table).  **It is a separate module
rather than more of ``_amounts``, and that was a developer decision**
(2026-08-12): the two questions are different tiers, and ``_amounts`` was 690
lines against a live ``too-many-lines`` ceiling of 1000.  *Re-derived at plan
step X-bx, because a figure quoted as a REASON decays invisibly: ``_amounts``
reads 664.  The clause that followed -- "a ceiling nothing in ``app/`` exceeds"
-- is DELETED rather than tensed.  It is still true and it implied room that is
gone: three modules now sit exactly ON 1000 and two more at 999, so the ceiling
binds this tree generally and a module needing space must SPLIT, never shave.*
A module INSIDE this package rather than a new top-level service,
because the W9909 completeness fence is keyed on the package and prefix-matched,
so a sibling module is scoped the day it is written -- where a new top-level
module would escape it, which is finding N-28's shape.

**The four rules, and the DECLARATION that picks between them.**  They are NOT a
partition over a row's links -- ruling R-FI refuted that discriminator by
tracing -- because two of them are SUBSETS of two others:

  1. **OWN** -- the row states its own figure, and SAYS SO by carrying
     ``amount_source_id IS NULL``.  An ad-hoc row, a CC payback, and a row a
     human re-priced.  **A settled row is NOT automatically one of them**: plan
     step X-au-c3 records what MOVED in its own columns and writes no plan
     column at all, so a settled row's plan keeps whatever ownership it had.
  2. **SALARY** -- a paycheck, priced by the salary profile driving its
     template (``income_service.salary_net_for`` over the pass's
     ``SalaryPricing``).  A SUBSET of rule 3: ``SalaryProfile.template_id``
     names an ordinary transaction template.
  3. **TEMPLATE** -- an ordinary recurring row, priced by its definition's
     effective-dated series as of the row's OWN due date
     (``template_amount_service.amount_as_of``, plan step X-au-a).
  4. **LOAN_PAYMENT** -- a loan payment's parent TRANSFER, priced by the loan
     (:class:`._loan_pricing.LoanPricing`, a module of this package since plan
     step X-au-g-2a).  A SUBSET of rule 3: a loan payment's definition is a
     transfer template.  **It was a rule about the SHADOW until plan step
     X-au-f-2** (ruling **R-BAL10**), where it moved onto the transfer and
     took the standing ``extra_principal`` with it.

A fifth rule, **TRANSFER**, priced a transfer's two shadow rows by their
parent (a row declaring ``parent_transfer``).  Plan step
``balance:X-bi-6-4d-2`` deleted the shadows (ruling **R-BAL166**), and plan
step ``balance:X-bi-6-5`` deleted the rule, its refusal of a row with no
parent to read, and the relation (finding **BAL-642**).  A leg reads its
parent through :func:`resolve_transfer_amount`.

**WHICH rule owns a row is next door** (:mod:`._amount_rule`, split out at plan
step X-au-f-2 when this module passed ``max-module-lines``).  That leaf holds
the :class:`AmountRule` enum, both classifiers and their refinement tables; this
one holds what each rule ANSWERS.  The seam is *ownership is DECLARED and the
refinement is READ* -- plan step X-au-c2's design, finding **N-262** -- and the
argument for it is written once, there.  **FOUR RULES, TWO ROW TABLES, and
neither table takes all four**: a TRANSACTION takes rules 1, 2 and 3
(:func:`resolve_transaction_amount`), a TRANSFER takes rules 1, 3 and 4
(:func:`resolve_transfer_amount`).  Each has its own total dispatch below, and
the suite grades their UNION against the enum.

**What the column read buys is a resolver whose answer cannot contradict the
CHECK.**  Until plan step X-au-c2 the OWN arm was INFERRED from ``is_override``
and from having left Projected, neither of which
``ck_transactions_amount_ownership`` can see -- so four live doors could write a
row the schema admits and this dispatch refuses: a pay-period move alone sets the
flag (``routes/transactions/mutations.py:251``), carry-forward sets it in a bulk
``query.update`` no ORM validator sees (``carry_forward_service/_execute.py:157``),
and Credit and Cancelled leave Projected WITHOUT entering the settled band, so no
freeze ever fires.  Production carries 7 Cancelled and 2 Credit template-linked
rows and ``routes/grid/_items.py``'s ``load_grid_items`` loads every one
of them with no status predicate,
so the first bucket to derive would have taken out the whole screen.  Asking the
column instead makes the two agree by construction: the state the CHECK pairs a
figure with is exactly the state this dispatch answers from that figure.

X-au-c1 backfilled no declaration at all, so every row was OWN and this resolver
answered its stored column through ONE arm.  That is what made X-au-c2's
fifteen-module reader refactor byte-identical by construction rather than by
measurement -- before it, a Projected template-linked row priced from the SERIES
and agreed with its column only because X-au-b measured ``$0.00`` drift.  **The
per-kind cutovers are what stamp a relation as each bucket stops being priced**:
X-au-g-2c-2 declared every transfer SHADOW (350 rows on production, 2026-09-01;
plan step ``balance:X-bi-6-4d-2`` deleted the shadows), **X-au-d** every
non-override SALARY row (59 rows, 2026-09-02), **X-au-e** the non-override
TEMPLATE rows and **X-au-f** a generated transfer's own amount.  *The sentence
this replaces still said every production row was OWN; it went stale at the
first of those and is corrected here rather than at the step that made it false
a second time.*  A CC payback is the kind carrying NEITHER link while its
amount is derived (``credit_workflow.create_cc_payback_transaction`` copies the
source row's figure, ``entry_credit_workflow.sync_entry_payback`` re-states it as
the sum of the source's credit entries), so it places as OWN here and needs a
relation of its own to stop -- finding **N-243**, plan step X-au-i.

**A refusal is a refusal, never a fallback.**  Where a derived rule's producer
cannot answer -- an EMPTY series, no live net for the row's period, a loan
whose basis will not resolve, a missing parent
-- :class:`~app.exceptions.AmountUnresolvable` is raised naming the row and the
rule.  Falling back to the stored column would publish exactly the stale figure
this arc exists to delete, and once a per-kind cutover (plan steps
X-au-d..X-au-i) declares that row's relation its column is NULL, so the fallback
would be a ``None`` in a money path.  Zero rows on
production take any refusal arm (measured 2026-08-12 over all 997), so each one
carries a seeded control instead.

**The DERIVATION tier is separate, and finding N-228 is why.**  The salary
derivation runs the paycheck engine over the owner's WHOLE pay-period set, so
asking it per row is quadratic work and was already measured as a defect.
:func:`amount_basis` holds each live derivation ONCE and hands the resolver
both; the rules read the derivation their own kind owns, so which rule applies
is never decided by which map a row appears in.  That distinction is the refuted
discriminator one level down.  It is keyed on a ``(user_id, scenario_id)`` pair
rather than on an ``Account`` -- it only ever read ``account.user_id`` -- so a
CROSS-ACCOUNT reader builds one basis for everything it loaded instead of
grouping its rows by account first (plan step X-au-c2).

**A basis is pinned to an OWNER and a SCENARIO, not to a row set** (plan step
X-au-c2b), and that is what makes "one pricing pass per read pass" structural
rather than a convention each surface remembers.  Everything expensive here is
scoped that way already -- the paycheck engine by owner, a loan's P&I and escrow
history by loan -- so storing per-row ANSWERS made a pass row-set-shaped for no
reason and cost a re-derivation every time a request loaded a second row set
(findings **N-268**, **N-269**).  A read pass carries its own through
``balance_at.BalanceContext.amounts``; a write door that prices one row builds
one and pays only for the rules that row reaches.

Boundary discipline (``CLAUDE.md`` Architecture / B6-01): plain data and ORM
rows in, ``Decimal`` out; no Flask import, no writes.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from app.exceptions import AmountUnresolvable

from ._amount_basis import AmountBasis
from ._amount_rule import AmountRule, amount_rule, transfer_amount_rule
from ._definition_cash import (
    DefinitionRow,
    _loan_payment_cash_from,
    _stated_amount,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    # Named for the annotation alone.  A runtime import would put the paycheck
    # / tax stack on this module's load path, which is the cycle the salary
    # rule's call site defers to avoid (finding N-267).  The LOAN half was
    # named here for the same reason until plan step X-au-g-2a; rule 4's
    # producer is a module of THIS package now, so nothing about it needs
    # deferring and there is no import to keep.
    from app.services.income_service import SalaryPricing


def resolve_transaction_amount(txn, basis: AmountBasis) -> Decimal:
    """Return what *txn*'s amount is, by the rule that owns it.

    The resolver plan step X-au-b exists to build.  It is TOTAL over rows -- one
    of the three rules a transaction takes applies to every one -- and total in
    its answer: it returns a
    ``Decimal`` or it raises, never ``None`` and never a plausible substitute.

    Proven equal to the answer the app gives today for **every one of the 997
    rows** on the 2026-08-12 production clone
    (``tests/manual/verify_amount_resolver.py``).  "The answer the app gives
    today" is not single-valued -- ``routes/grid/page.py``'s ``index`` reads the override map
    while ``dashboard_service.py:279`` reads the raw column, which IS finding
    **N-224** -- so the oracle grades the OVERRIDE-MAP answer, the one every
    balance surface folds.

    **It was UNWIRED at plan step X-au-b**: nothing in ``app/`` called it
    until plan step X-au-c2 routed the readers through it, and X-au-c3 made a
    settled row RECORD what moved.

    **A caller resolving many rows should apply
    :func:`~app.utils.amount_relationships.pricing_load_options`**, re-exported
    from this package so a caller asks the amount model for its own load.  It
    was a paragraph naming seven relationships that every routed loader had to
    remember; plan step X-au-g-2c-2 made it a function, because
    that step is what made the ``Transaction.transfer`` chain load-bearing --
    a transfer shadow was DERIVED from then, so a surface that loaded
    projected shadows walked to a parent per row without it.  None loads one
    now: the grid prices a transfer's legs since leaf ``X-bi-6-1``
    (:func:`leg_amounts_by_key`), the cash fold and the loan payment feed read
    projected transfers as legs of the PARENT and take
    :func:`~app.utils.amount_relationships.transfer_pricing_load_options`
    instead, and plan step ``balance:X-bi-6-4d-2`` deleted the shadows (ruling
    **R-BAL166**).  It lives one tier
    DOWN because ``loan_loaders`` needs it and this package imports
    ``loan_loaders``; that module states the argument.

    **It no longer asks whether *txn* was in the basis's row set** (plan step
    X-au-c2b).  A basis holds DERIVATIONS pinned to an owner and a scenario, not
    answers pinned to a row set, so every row of that owner and scenario is
    resolvable against it and the membership refusal this used to raise has no
    state left to describe.

    **What it asks INSTEAD is the pin the caller can still get wrong**, and the
    substitution is the point rather than a smaller guard.  ``priced_ids``
    caught "this row was not in the set I built over"; it could not catch "this
    basis belongs to another scenario", which is the mistake that actually
    changes a figure -- ``LoanPricing`` resolves a loan against ITS scenario's
    payment history, so a foreign basis answers a different ``monthly_payment``
    with no error at all.  A row states its own ``scenario_id`` in a column, so
    the check is free and TOTAL where the membership set was neither.

    An adversarial review of this step's own build is what put it here: the
    control that replaced the deleted membership test proved the SAFE direction
    (a basis answers for a row it was not built over) and left the unsafe one --
    a silently different number -- with nothing asserting it.

    Args:
        txn: The :class:`~app.models.transaction.Transaction` to price.  It must
            belong to *basis*'s scenario.
        basis: The read pass's :class:`AmountBasis` (:func:`amount_basis`).

    Returns:
        The row's amount as a ``Decimal``.

    Raises:
        AmountUnresolvable: When *txn* belongs to another scenario than *basis*,
            or when the rule that owns this row cannot answer for it.  See the
            module docstring: a refusal is never a fallback.

            **No OTHER exception this arc defines reaches a caller here**
            -- ``amount_rule`` can still raise ``KeyError`` for a ref member
            added without a rule beside it, which is documented at that
            dispatch -- **and a second clause here said otherwise until plan
            step X-au-g-2c-1 re-took it.**  That clause named
            ``UndatedSettleError``, "propagated from the DERIVE-mode loan arm,
            whose producer loads the loan's payment history" -- true when it was
            written and false since plan step **X-au-g-1** deleted that load.
            The derive arm reaches ``_loan_installment._installment_cash``,
            which derives an installment date (``loan_loaders.installment_for``,
            total: a stored ``due_date`` or one computed from the period start)
            and reads a rate period and an escrow
            version on it.  No settle day is consulted on any of the four
            rules' paths.  A stale ``Raises:`` is the quietest kind of false
            claim: nothing executes it, so nothing contradicts it.

        UnsupportedStateError: Another arc's refusal, through the salary rule's
            pricer (plan step salary:X-at-3): a salary-linked row whose
            profile is in a state the tax law does not list.  It is a refusal
            like the one above and is answered app-wide by its own page
            (:mod:`app.error_handlers`); ``salary_profile_service
            .archive_profile``, which catches :class:`AmountUnresolvable`
            alone, lets it through, so such a profile cannot be archived
            while a row it prices still derives its figure from the profile
            (the usual case: a template whose rows fall in the saved
            calendar) until its state is one the law lists.
    """
    if txn.scenario_id != basis.scenario_id:
        raise AmountUnresolvable(
            f"Transaction {txn.id} is in scenario {txn.scenario_id} and this "
            f"AmountBasis prices scenario {basis.scenario_id}. Resolving it "
            "here would price it from another scenario's salary profiles and "
            "another scenario's loans -- a different figure, with nothing to "
            "say so. Build the basis for the scenario you are pricing."
        )
    return _RULE_ANSWERS[amount_rule(txn)](txn, basis)


def amounts_by_id(rows, basis: AmountBasis) -> dict[int, Decimal]:
    """Return ``{transaction_id: what the row's amount IS}`` for *rows*.

    **The batch every reader that takes a row's BUDGET uses** (plan step
    X-au-c2b), and the sibling of
    :func:`~._amounts.contributions_by_id` rather than a second spelling of it.
    The two answer different questions and a reader wants one or the other:

      * a CONTRIBUTION is what the row is worth to a balance -- ``0`` for an
        excluded row, what the row RECORDED as having moved once it has settled
        (``row_valuation.settled_figure``), else the resolved amount;
      * an AMOUNT is what the row's budget IS, unconditionally.

    **Ruling E-21 is why the second exists.**  An entry-tracked bill row's
    budget base is ``estimated_amount`` -- never what the row recorded as
    having moved, never status-dependent -- so the row's three figures (the amount cell, the
    remaining, the over-budget flag) all answer one question.  A contribution
    would break both halves of that: it answers ``$0.00`` for a Cancelled
    envelope, whose budget is still its budget, and it answers the entered
    actual for a settled one, which would silently re-base a variance on the
    number it is being compared against.

    **There is deliberately NO status gate above the resolve**, which is the one
    place this differs in shape from ``contributions_by_id``.  That batch gates
    first because an excluded row is worth ``$0.00`` and has no producer to
    answer it; here the excluded row's budget is exactly what is being asked
    for, so the resolver is asked for every row.  A per-kind cutover
    (X-au-d..X-au-i) that declares a row derived owes that row a producer that
    answers whatever its status -- and rule 2 and rule 4 stopped reading status
    at this same step precisely so they can.

    Every id in *rows* appears in the result, so a caller indexes it with ``[]``
    and a row it forgot to price raises a ``KeyError`` where it is read.  There
    is deliberately no ``.get(id, default)`` shape: a default here is a
    fabricated figure in a money path.

    Args:
        rows: The loaded rows to price.  They may span accounts -- the basis is
            keyed on the owner, not on one account -- but every one must belong
            to *basis*'s owner and scenario.
        basis: The read pass's :class:`AmountBasis`.

    Returns:
        ``{transaction_id: Decimal}`` covering every row.

    Raises:
        AmountUnresolvable: From the resolver, for a row whose rule cannot
            answer.  A refusal is never a fallback (see the module docstring).
        UnsupportedStateError: From the resolver, for a salary-linked row
            whose profile is in a state the tax law does not list (plan step
            salary:X-at-3).
    """
    return {row.id: resolve_transaction_amount(row, basis) for row in rows}


def leg_amounts_by_key(legs, basis: AmountBasis) -> dict[tuple[int, int], Decimal]:
    """Return ``{leg.cell_key: what the leg's amount IS}`` for transfer legs.

    :func:`amounts_by_id`'s twin for the grid's transfer legs (leaf
    ``X-bi-6-1``, ruling **R-BAL87**): a leg's budget is its parent's, priced
    by :func:`resolve_transfer_amount` -- the ONE producer ruling **R-BAL10**
    put a transfer's amount on, and what ``routes._render_helpers
    .transfer_budgets`` asks for one transfer -- so a leg's cell and the
    transfer popover the same click opens cannot show two figures.  Keyed by
    :attr:`~app.services.transfer_legs.TransferLeg.cell_key`, a tuple, so the
    page's one ``budgets`` map holds rows (by ``id``) and legs together.
    Both legs of one transfer resolve to one figure; the resolver is asked
    once per leg rather than once per parent because the cost is a lookup on
    every arm but a derive-mode loan payment's, and a cache here would be a
    second home for the answer.

    Args:
        legs: The :class:`~app.services.transfer_legs.TransferLeg` values a
            surface is about to render, their parents loaded with
            :func:`~app.utils.amount_relationships.transfer_pricing_load_options`.
        basis: The read pass's :class:`AmountBasis`.

    Returns:
        ``{(transfer_id, account_id): Decimal}`` covering every leg.

    Raises:
        AmountUnresolvable: From the resolver, for a transfer whose rule cannot
            answer.  A refusal is never a fallback (see the module docstring).
    """
    return {
        leg.cell_key: resolve_transfer_amount(leg.transfer, basis) for leg in legs
    }


def resolve_transfer_amount(xfer, basis: AmountBasis) -> Decimal:
    """Return what a parent TRANSFER's amount is, by the rule that owns it.

    ``budget.transfers.amount`` is the second column ruling R-FI's CHECK covers,
    so its rules belong here beside the transaction's rather than in a module of
    their own.  Three of the four can apply: a transfer owns its figure, its
    definition's series states it as of the transfer's own due date, or -- since
    plan step X-au-f-2 -- it is a LOAN PAYMENT and the loan says what it costs.
    Which of the three is :func:`transfer_amount_rule`, asking the
    ``amount_source_id`` column and then the definition, never an inference from
    ``is_override``, from having left Projected, or from carrying a template
    (plan step X-au-c2, finding **N-262**).  An AD-HOC transfer is structurally
    in the first state: ``ck_transfers_adhoc_owns_amount`` refuses a declaration
    on one, because nobody generated it and no definition states its price.

    **A LOAN PAYMENT'S CASH IS ANSWERED HERE, AND THAT IS RULING R-BAL10**
    (plan step X-au-f-2, closing finding **N-263**).  This function took no
    basis and had no loan arm: a derive-mode payment's parent reached the series
    arm and was REFUSED by ``template_amount_service.owns_its_amount``, which
    was correct while the parent still stored a figure and unpriceable the
    moment the cutover emptied that column (``X-au-f-3``, absorbed into
    ``X-au-f-2``).  The occurrence is ONE economic
    event, held as three rows until plan step ``balance:X-bi-6-4d-2`` deleted
    its two shadows (ruling **R-BAL166**) and as one since; the two legs are
    its PROJECTION, so the value's home is the parent and each leg reads it
    through this function (the grid's :func:`leg_amounts_by_key` and the cash
    fold's :func:`~._amounts.planned_leg_contribution` among its callers).

    **The OWN arm is asked FIRST, and on a loan payment that is the whole of
    R-JM.**  A transfer carrying a figure owns it whatever its template says, so
    an owner who typed ``$1,325.00`` on a derive-mode payment gets
    ``$1,325.00`` at the parent and at both legs -- the contract does not
    overrule them and the standing extra is not added on top of what they
    typed, because the figure they typed IS the cash that leaves the bank
    (**R-IO**, **R-IW**).  Before this step the legs answered the CONTRACT
    while the parent answered the owner, which is the ``$174.10`` divergence
    R-JM was ruled to make unrepresentable.

    **It takes the read pass's basis and states NO scenario pin, which is where
    it differs from its transaction twin.**  That twin refuses a foreign basis
    because rule 2 resolves against ``basis.salary``, an owner-and-scenario
    derivation, so a foreign basis answers a different figure with nothing to
    say so.  Nothing on THIS side is scoped that way: the only derivation a
    transfer reaches is ``basis.loans``, and a loan's terms are neither
    owner-scoped nor scenario-scoped -- ``LoanPricing`` lost its scenario
    argument at plan step X-au-g-2c-2 precisely because the parameter only ever
    expressed a mistake.  A pin check here could not change an answer, and a
    guard that cannot fire is a fence rather than a control.  The day a
    scenario-scoped producer prices a transfer, the check arrives with it.

    Args:
        xfer: The :class:`~app.models.transfer.Transfer` to price.
        basis: The read pass's :class:`AmountBasis` (:func:`amount_basis`).
            Only rule 4's DERIVE arm reads it, so a transfer that owns its
            figure or reads a series is answered off the row already loaded.

    Returns:
        The transfer's amount as a ``Decimal``.

    Raises:
        AmountUnresolvable: When the transfer declares a relation that cannot
            price a transfer, when it is priced by its definition and that
            definition states no price for its due date, or when it is a
            derive-mode loan payment whose loan will not resolve.
    """
    return _TRANSFER_RULE_ANSWERS[transfer_amount_rule(xfer)](xfer, basis)


def _own_figure(amount, kind: str, row_id: int) -> Decimal:
    """Return a row's OWN stored figure, refusing a row that carries none.

    The OWN arm shared by rule 1 (:func:`_own_answer`) and by
    :func:`resolve_transfer_amount`, exactly as :func:`_stated_amount` below is
    the shared SERIES arm -- one per column the two tables carry, so neither
    pair can come to disagree about what "the row's own figure" means.

    The refusal in it is the amount model's TOTALITY contract rather than
    defensive padding: a resolver that can answer ``None`` for a row is not
    total, and every other rule beside it raises rather than returning one.  It
    is unreachable on the DATA -- no row that owns its amount has a NULL column
    -- and what keeps it that way is ``ck_transactions_amount_ownership`` (plan
    step X-au-c1): a row that owns its amount must store one.  A row that reaches
    here with no figure has that CHECK broken, and substituting a zero would
    remove real money from a balance in silence.

    **IT LIVED IN :mod:`app.services.row_valuation` UNTIL PLAN STEP X-bx, AND
    IT MOVED HERE BECAUSE THAT IS WHERE ITS CALLERS ENDED UP.**  Two steps took
    it there, and this is the one home for both, because they are one story
    about one leaf.  It was public in that producer-free module to serve two
    accessors that answered "what is this row's plan" from the
    ``estimated_amount`` column, each a SECOND spelling of the question this
    module answers: X-bu deleted ``owned_amount``, whose refusal parted from the
    resolver on a row a cutover had declared DERIVED and returned 500 from
    ``/analytics/spending`` on production-shaped data (**BAL-462**), and X-bx
    deleted the fall-through of ``owned_contribution`` -- now
    :func:`~app.services.row_valuation.settled_contribution`, renamed for the
    assertion that survived (**BAL-465**).  That left both remaining callers
    inside this file, so the leaf follows them and is PRIVATE -- which also
    takes a public name out of the balance fence rather than moving it to a
    second ruling (``shekel_checkers/_fence_rulings``).  X-bu's entry in
    :func:`_own_answer` said this refusal was not unrepresentable, only
    unexposed; what X-bx made unrepresentable is a public one-token answer to
    the plan question anywhere below this module.  What it still refuses is a
    broken CHECK, never a reader.

    Args:
        amount: The row's stored amount column.
        kind: ``"transaction"`` or ``"transfer"``, for the refusal message.
        row_id: The row's id, named in the refusal.

    Returns:
        The stored figure.

    Raises:
        AmountUnresolvable: When the row owns its amount and stores none.
    """
    if amount is None:
        raise AmountUnresolvable(
            f"{kind.capitalize()} {row_id} owns its amount and carries none. "
            "A row whose amount is its OWN must store it -- that pairing is "
            "ck_transactions_amount_ownership -- so this row was written "
            "around the CHECK. There is deliberately no substitute figure: "
            "answering zero would take real money out of a balance without "
            "saying so."
        )
    return amount


def _own_answer(txn, _basis: AmountBasis) -> Decimal:
    """Rule 1: the row states its own figure.

    Takes the basis it does not read, because every rule answers through ONE
    signature -- which is what lets the dispatch below be a mapping keyed on the
    rule rather than a special case per rule.

    **NO READER OUTSIDE THE AMOUNT MODEL ASKS THE PLAN COLUMN**, and plan steps
    X-bu and X-bx are jointly what made that true; :func:`_own_figure` carries
    the account of how, because it is the leaf both steps were about.  What
    belongs here is the consequence: this arm is the ONE place a transaction's
    plan column is read, :func:`resolve_transfer_amount` above is the one place
    the transfer column is, and the two spell it identically so the tables read
    alike.  X-bu's entry here recorded the arm as one of TWO copies with nothing
    holding them in step; X-bx deleted the other rather than re-pointing it, so
    the caveat is discharged rather than merely restated.

    Args:
        txn: The transaction being priced.

    Returns:
        The row's stored ``estimated_amount``.

    Raises:
        AmountUnresolvable: When the row owns its amount and carries none, which
            is ``ck_transactions_amount_ownership`` broken rather than a state
            the model represents.  See :func:`_own_figure`.
    """
    return _own_figure(txn.estimated_amount, "transaction", txn.id)


def _salary_answer(txn, basis: AmountBasis) -> Decimal:
    """Rule 2: a paycheck is worth what its salary profile pays for that period.

    Delegates to :func:`app.services.income_service.salary_net_for` over the
    pass's :class:`~app.services.income_service.SalaryPricing`, which since
    plan step **X-au-d** is the only producer of a ROW's amount: generation
    used to write a second copy of this derivation into ``estimated_amount``
    and it now declares the row instead.

    *That is the narrow claim and it is the true one.*  An adversarial review
    of X-au-d refuted the wider one this paragraph used to make -- that it is
    the only producer of the FIGURE -- because ``routes/salary/views`` and
    ``routes/salary/cockpit`` each built the same ``project_salary`` call over
    the same calendar to render their own breakdowns, so the derivation was
    written three times.  **That was finding N-443 and plan step salary:R14-a
    closed it**: all three now call
    :class:`app.services.income_service.ProfilePaychecks`.  The wider claim
    is still NOT true and a second adversarial review caught this sentence
    making it: ``tax_withholding_service`` and ``tax_report_service``
    derive breakdowns of their own over a single tax YEAR.  What R14-a
    made single is the CALENDAR-WIDE projection; what this function owns
    is still only the narrow claim -- what a ROW's amount is.

    **The refusal narrowed at that step and is stated as it now is.**  It
    fired where the app held two answers: the read-time producer scoped its
    profile lookup by SCENARIO while generation's own took the first active
    profile whatever its scenario, so a template driven by profiles in two
    scenarios was priced by one at write time and by another -- or by none --
    at read time.  There is no write-time resolution left to disagree with.
    What still refuses: no ACTIVE profile in the ROW's scenario names its
    template, the profile's projection does not cover the row's pay period, or
    the row is an EXPENSE on a salary-linked template
    (``salary_net_for`` takes income only).  Zero such rows on the 2026-08-12
    production clone and zero on the 2026-09-02 one.

    **It reads no STATUS, and that is plan step X-au-c2b's split.**  The map it
    used to index was built by a read-time repair, which filtered to Projected
    non-overridden rows -- so a Cancelled or hand-priced paycheck was refused
    here for a reason that has nothing to do with what a paycheck is worth.
    Pricing asks the definition; whether a row still counts is finding
    **N-262**'s separate question, answered above this rule by
    ``row_valuation.fixed_contribution``.  *That repair is deleted as of plan
    step X-au-d, so the split it protected is now simply the shape of the
    model: there is one producer and it reads no status.*

    **A HAND-PRICED paycheck never reaches this rule**, and that is what makes
    the status-blindness safe rather than merely tidy.  The edit doors state a
    typed figure through ``amount_ownership.state_own_amount``, which clears
    the declaration -- so such a row is OWN and rule 1 answers it.  ``is_override``
    decides nothing here (finding **N-262**): a row whose PERIOD alone was
    moved carries that flag and stays derived, which is why moving a paycheck
    re-prices it for the paycheck it was moved into.

    Args:
        txn: The salary income row being priced.
        basis: The read pass's basis; its ``salary`` derivation holds the
            answer, keyed on the row's template and pay period.

    Returns:
        The live net pay for the row's period.

    Raises:
        AmountUnresolvable: When the live recompute has no answer for this row.
        UnsupportedStateError: The row's profile is in a state the tax law
            does not list (plan step salary:X-at-3), from the pricer.
    """
    # Pylint: ``import-outside-toplevel`` -- the paycheck / tax stack stays off
    # this module's load path, the same reason ``amount_basis`` imports it at
    # call time (finding N-267).
    # pylint: disable=import-outside-toplevel
    from app.services.income_service import salary_net_for
    net = salary_net_for(txn, basis.salary)
    if net is None:
        raise AmountUnresolvable(
            f"Transaction {txn.id} is priced by a salary profile and the live "
            "recompute answered nothing for it. Either no ACTIVE profile names "
            "its template in this row's scenario -- generation resolves the "
            "profile without scoping by scenario and the recompute scopes by "
            "it, so the two can disagree -- or the profile's projection does "
            "not cover this row's pay period, or the row is an expense on a "
            "salary template. The stored figure is not a fallback: it is the "
            "cache this rule exists to stop reading."
        )
    return net


def _template_answer(txn, _basis: AmountBasis) -> Decimal:
    """Rule 3: a recurring row is worth what its definition states on its due date.

    Takes the basis it does not read: the price series is a STORED fact
    resolved per row, not a live recompute that has to be batched.

    Args:
        txn: The template-generated row being priced.

    Returns:
        The definition's stated amount as of the row's due date.

    Raises:
        AmountUnresolvable: See :func:`_stated_amount`.
    """
    return _stated_amount(txn.template, txn.due_date, f"Transaction {txn.id}")


def _transfer_own_answer(xfer, _basis: AmountBasis) -> Decimal:
    """Rule 1, on a transfer: the row states its own figure.

    The transfer entry of the same arm :func:`_own_answer` is for a
    transaction, spelling it identically over the other table's column so the
    two cannot come to disagree about what "the row's own figure" means
    (:func:`_own_figure` carries the argument).

    Takes the basis it does not read, because every rule answers through ONE
    signature -- which is what lets the transfer dispatch be a mapping keyed on
    the rule rather than three special cases.

    Args:
        xfer: The transfer being priced.

    Returns:
        The transfer's stored figure.

    Raises:
        AmountUnresolvable: See :func:`_own_figure`.
    """
    return _own_figure(xfer.amount, "transfer", xfer.id)


def _transfer_template_answer(xfer, _basis: AmountBasis) -> Decimal:
    """Rule 3, on a transfer: its definition states the price on its due date.

    The transfer entry of :func:`_template_answer`'s arm, through the same
    shared producer (:func:`_stated_amount`), so a transfer and a transaction
    generated by definitions of the two kinds resolve their series the same
    way.

    Takes the basis it does not read: a price series is a STORED fact resolved
    per row, not a live recompute that has to be batched.

    Args:
        xfer: The template-generated transfer being priced.

    Returns:
        The definition's stated amount as of the transfer's due date.

    Raises:
        AmountUnresolvable: See :func:`_stated_amount`.
    """
    return _stated_amount(xfer.template, xfer.due_date, f"Transfer {xfer.id}")


def _loan_payment_cash(xfer, basis: AmountBasis) -> Decimal:
    """Rule 4: a loan payment's transfer is worth what the loan says it costs.

    **THE CASH THAT LEAVES THE BANK, stated ONCE** (ruling **R-BAL10**, plan
    step X-au-f-2).  Two arms, one per MODE, and the mode is read off the
    settings row (``recurring_transfer_query.loan_payment_config``) rather than
    inferred from which map the row turned up in:

    * **derive mode** -- the cash is P&I plus the escrow in effect on the
      installment's own DUE date plus any standing extra, which
      ``LoanPricing.derive_cash`` computes from the loan's own resolution.  A
      ``None`` there means the loan would not resolve, and that REFUSES;
    * **manual mode** -- the operator owns the base cash, which is a STATED
      amount, so it is the definition's series (rule 3's arm, shared) plus the
      standing extra.

    **The standing extra is part of BOTH arms and lives on neither leg.**  It
    was added at the SHADOW before this step, which, once every leg reads its
    parent's figure (the read half of ruling **R-JM**, structural since plan
    step ``X-bi-6-1``), would give an owner who typed ``$1,325.00`` legs worth
    ``$1,425.00`` (manual) or ``$1,610.95`` (derive) -- the ``$174.10`` shape
    R-JM makes unrepresentable, and the reason R-BAL10 rejected keeping the
    base at the parent and the extra at the leg.  Worked, on P&I ``$1,400.00`` + escrow
    ``$110.95`` + extra ``$100.00``: parent and both legs ``$1,610.95``.

    **ONE ``round_money`` boundary per arm, over the WHOLE sum** (ruling
    **E-26**).  The derive arm sums three terms and rounds once inside
    :func:`._loan_installment._installment_cash`; the manual arm sums two and
    rounds here.  Composing either as a round of a round double-rounds, which
    is how a cutover advertised as byte-identical parts from its predecessor by
    a cent.

    **The derive arm reads no STATUS, and that is plan step X-au-c2b's split.**
    The map it used to index was built by the read-time repair, which filtered to
    Projected non-overridden shadows -- so a Cancelled or hand-priced loan
    payment was refused here as though its LOAN would not resolve, which is a
    different and alarming statement.  Whether a row still counts is finding
    **N-262**'s separate question, answered above this rule rather than inside
    it.

    **The mode is read rather than inferred, and an adversarial review is why.**
    The first draft answered "the live map when it has an entry, else the
    parent's series", which made a manual payment resolve TWO different ways:
    ``LoanPricing.live_cash`` priced a manual payment from
    ``shadow.estimated_amount + extra``, so a payment with a standing extra was
    answered from the stored column while the same payment without one was
    answered from its series.  On a shadow whose cache had drifted from its
    definition the two disagreed -- ``$1,400.00`` against ``$1,450.00`` on the
    review's reproduction -- and the arm that won was the one reading the column
    ruling R-FI exists to stop reading.  Neither arm reads a stored figure now.

    **The derive arm reads no wall clock, and plan step X-au-g-2b is what
    closed the last read.**  :class:`._loan_pricing.LoanPricing` pinned
    ``date.today()`` when the basis was built and resolved every payment's P&I
    against it -- finding **N-40** -- while the escrow beside it in the same
    sum already resolved on the payment's own due date.  Ruling **R-IJ** put
    both on the installment (as ruling D5 had put the escrow), so the
    derivation takes no date and the whole package makes no clock call --
    an AST census over all thirteen modules, asserted by
    ``test_amount_source.TestTheAmountModelReadsNoClock``.
    Dormant on production (``budget.loan_payment_settings`` is empty), so this
    rule prices ``$0.00`` there and is graded only on a seeded loan.

    Args:
        xfer: The loan-payment transfer being priced.  Its ``due_date`` and its
            ``pay_period`` date the installment -- the parent's columns, which
            every leg reads rather than holds
            (:class:`~app.services.transfer_legs.TransferLeg`'s ``due_date``
            and ``pay_period``).
        basis: The read pass's basis; its ``loans`` derivation resolves the
            destination loan and holds its escrow history.

    Returns:
        The transfer's live cash.

    Raises:
        AmountUnresolvable: When a DERIVE-mode payment's loan will not resolve,
            or when a MANUAL payment's definition states no price.
    """
    return _loan_payment_cash_from(
        DefinitionRow(
            template=xfer.template,
            due_date=xfer.due_date,
            period_start=xfer.pay_period.start_date,
            to_account_id=xfer.to_account_id,
        ),
        basis, subject=f"Transfer {xfer.id}",
    )


# The TOTAL dispatch: one answer per rule, keyed by the rule itself.  A mapping
# rather than a chain of ``if``s so that adding a member to :class:`AmountRule`
# without an answer for it raises at the lookup instead of falling through to
# whichever branch happens to be last -- which is how a three-rule dispatch
# silently becomes a two-rule one.  ``tests/test_services/test_amount_source.py``
# grades the table against the enum, so the completeness is a predicate rather
# than a comment.
_RULE_ANSWERS = {
    AmountRule.OWN: _own_answer,
    AmountRule.SALARY: _salary_answer,
    AmountRule.TEMPLATE: _template_answer,
}

# The same table for the OTHER row table, and the two are what make the four
# rules total across both.  A transfer takes rule 1, rule 3 or rule 4; a
# transaction takes any but rule 4, which stopped being a shadow rule at plan
# step X-au-f-2 (ruling **R-BAL10**).  ``tests/test_services/test_amount_source.py``
# grades the UNION of the two against the enum -- so a rule with an answer in
# neither table is caught -- and each table's own key set besides, so a member
# added to :class:`AmountRule` has to arrive with a decision about BOTH rather
# than defaulting into one.
_TRANSFER_RULE_ANSWERS = {
    AmountRule.OWN: _transfer_own_answer,
    AmountRule.TEMPLATE: _transfer_template_answer,
    AmountRule.LOAN_PAYMENT: _loan_payment_cash,
}
