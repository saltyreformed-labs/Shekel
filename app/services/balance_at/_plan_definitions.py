"""Balance-at-T seam -- the ESTIMATED tier's DEFINITION walk.

Plan step **R16-b-2**.  What a generate pass over the owner's whole schedule
would write for every recurring transfer into a loan, priced as those rows
would be priced -- the summing leaf of the R16 arc, split out of :mod:`._plan`
when that module crossed pylint's 1000-line ceiling (see
:mod:`._plan_records` for the split and its reasons).  :mod:`._plan` calls
:func:`estimated_from_definitions` from its assembly and owns the contract-only
estimate, the charge calendar and the read pass's memo; this module owns the
three steps between a definition and its estimated payments -- which
occurrences it names, which of them a row already answers, and what each
would cost -- and imports nothing from ``_plan``.

The rulings it applies are stated on the functions: **R-R64** (an occurrence
no row in any state answers is priced, past or future, bounded to the
schedule), **R-R65** (the authored closing alone; the derived stop is the
fold's own output), **R-R66** (``budget.transfers`` read for identity only),
**R-R67** (the amount model's own arm) and **R-R69** (dated as the row would
be).  The occurrences it prices are bounded BELOW twice, by two different
facts.  The walk drops every occurrence whose row would land on or before the
books of an account the definition moves money in (rulings **R-PC85**,
**R-PC86**), as it does for generation.  And the caller drops an occurrence
due at or before the loan's latest assertion -- its origination at the
earliest, which is ruling R-C's refusal -- in :func:`._plan.loan_plan`, on the
one post-anchor predicate (ruling **R-R72**).

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.  Seam-PRIVATE.
"""

from datetime import date

from app.models.account import Account
from app.models.transfer import Transfer
from app.services._recurrence_common import (
    OccurrenceClaims,
    TemplateRowSelector,
    rows_claiming,
)
from app.services.cash_ledger import DefinitionRow, definition_cash
from app.services.recurrence import (
    compute_due_date,
    projected_occurrence_placements,
)

from ._context import BalanceContext
from ._plan_records import _ONE_DAY, PlannedPayment
from ._resolution import ResolvedLoan


def _answered_by_rows(
    template, scenario_id: int, placements: list,
) -> OccurrenceClaims:
    """Return which of *placements* a row of *template* already answers, in ANY state.

    **The fold reads ``budget.transfers`` here for OCCURRENCE IDENTITY and for
    nothing else** (ruling **R-R66**, developer 2026-09-11).  A generated
    row's identity is the occurrence it answers -- ``(transfer_template_id,
    scenario_id, occurs_on)``, the key both unique indexes carry since plan
    step R17 -- and that column was deliberately NOT mirrored onto the shadow
    transactions the balance fold read money from until plan step
    ``balance:X-bi-6-4d-2`` deleted them.  So "has this occurrence been
    answered" could not be asked of ``budget.transactions`` at all, and
    asking it of the parent rows through the one claim rule generation applies
    (:class:`~app.services._recurrence_common.OccurrenceClaims`, over the same
    scoped fetch, :func:`~app.services._recurrence_common.rows_claiming`) is
    what makes the estimate and the generate pass agree by construction about
    which occurrences a row already holds.  No figure is read off any row
    here.

    **Every state counts** -- immutable, overridden, cancelled, soft-deleted
    -- exactly as the generate path has always counted them: a row the owner
    removed must not be resurrected by the estimate any more than by a pass.
    A row answering no occurrence (``occurs_on`` NULL: a hand-made or
    carried-forward row) claims its whole paycheck, which a projected period
    (``period_id`` ``None``) can never be.

    Args:
        template: The recurring transfer definition.
        scenario_id: The read pass's scenario.
        placements: The definition's placed occurrences
            (:class:`~app.services.recurrence.OccurrencePlacement`, each with
            a period), which is the duck type the claim reader takes.

    Returns:
        The :class:`~app.services._recurrence_common.OccurrenceClaims`.
    """
    selector = TemplateRowSelector(
        model=Transfer,
        template_fk_col=Transfer.transfer_template_id,
        template=template,
        scenario_id=scenario_id,
    )
    return OccurrenceClaims.over(rows_claiming(selector, placements))


def _unanswered_placements(
    template, ctx: BalanceContext, through: date,
) -> list[tuple]:
    """Return *template*'s placeable occurrences through *through* that no row answers.

    Steps 1-3 of :func:`estimated_from_definitions`, for one definition:
    resolve the rule through the pass's memo
    (:meth:`~._context.BalanceContext.resolved_recurrence_of`, which attaches
    the definition's books floor), narrow it to its AUTHORED closing (never
    the composed door -- see that function), place every occurrence on the
    paycheck its row would live in, drop the unplaceable (ruling **R-R64**'s
    boundary) and every occurrence whose row would land on or before the
    books of an account it moves money in (rulings **R-PC85**, **R-PC86**:
    the walk's own floor, so the estimate and a generate pass drop the same
    occurrences), and drop what a row of this definition already answers in
    any state (:func:`_answered_by_rows`).  An occurrence at or before the
    loan's origination -- which the write door refuses (ruling R-C), so a
    pass would not write it -- is left to the caller, which drops every
    payment at or before the loan's latest assertion, the origination being
    the earliest of those (ruling **R-R72**).  The books floor is a different
    fact -- where the app keeps books, not what the loan owed -- and drops
    such an occurrence first only when its row falls on or before the books'
    opening.  An adversarial
    review found a second definition whose unlocked start predated its loan
    paying for months the loan did not exist; a second predicate here for
    the same boundary was measured redundant by the review after it.

    Args:
        template: The recurring transfer definition, carrying a rule.
        ctx: The read pass -- its calendar places the occurrences, its
            scenario scopes the rows.
        through: The last day the walk covers.

    Returns:
        ``(placement, due)`` pairs -- each unanswered
        :class:`~app.services.recurrence.OccurrencePlacement` (with a period)
        and the date its row would carry -- ascending by occurrence.  The
        date is derived ONCE here and threaded to the pricing rather than
        derived again.
    """
    calendar = ctx.calendar()
    # The pass's memo: the composed door resolves this same rule to narrow it
    # by the stop THIS fold produces, and one pass resolves one rule once.
    resolved = ctx.resolved_recurrence_of(template.recurrence_rule)
    if resolved is None:
        # An owner with no pay periods has nothing to place on; the seam
        # cannot reach here with one (the calendar seeds the pass), and the
        # resolver's ``None``, which the memo hands back unchanged, is
        # honoured rather than second-guessed.
        return []
    # The memo's value carries the AUTHORED closing alone (``derived`` is
    # ``None`` by construction; the books floor it attaches bounds the walk
    # from BELOW and closes nothing), which since plan step R7d-g is exactly
    # what the rule's two bound columns hold: every stored closing bound is
    # its owner's word, so there is no cache to read around (ruling
    # **R-R56**'s arm went with the column's last writer) and nothing to
    # rebuild.  The derived stop is THIS fold's output.
    rule = template.recurrence_rule
    dated = [
        (
            placement,
            compute_due_date(rule, placement.occurrence, placement.period),
        )
        for placement in projected_occurrence_placements(
            resolved, calendar, through=through,
        )
        if placement.period is not None
    ]
    answered = _answered_by_rows(
        template, ctx.scenario_id, [placement for placement, _due in dated],
    )
    return [
        (placement, due) for placement, due in dated
        if not answered.blocks(placement)
    ]


def estimated_from_definitions(
    account: Account,
    resolved: ResolvedLoan,
    ctx: BalanceContext,
    as_of: date,
    through: date,
) -> list[PlannedPayment]:
    """Build the ESTIMATED tier from EVERY definition paying into *account*.

    **What a generate pass over the owner's whole schedule would write, priced
    as those rows would be priced** (plan step **R16-b-2**).  For each active
    recurring transfer into the loan
    (:attr:`~app.services.balance_at._resolution.ResolvedLoan.definitions`,
    loaded once per pass; ruling **R-R35**: every one of them is a payment
    against it):

    1. resolve its rule against the owner's calendar through the pass's memo,
       which attaches the definition's books floor (rulings **R-PC85**,
       **R-PC86**: no occurrence whose row would land on or before the books
       of an account it moves money in is walked), and walk it under its
       AUTHORED closing alone.  Never through the
       composed door: that door's derived stop is a loan's closing date,
       which is THIS fold's output, and a fold reading its own answer is the
       fixed point ruling **R-R65** refused to scaffold.  The authored half is
       the rule's own two columns, which hold nothing but an owner's word
       since plan step R7d-g (the chokepoints' cache of the payoff, which
       ruling **R-R56**'s arm read around, is NULLed and never written), so
       every definition's authored end date is honoured (ruling **R-R37**)
       and a payment with none runs to the fold's own zero crossing;
    2. place each occurrence on the paycheck its row would live in, saved or
       projected (:func:`~app.services.recurrence.projected_occurrence_placements`);
       an occurrence the schedule cannot place is dropped, which is ruling
       **R-R64**'s boundary;
    3. drop each occurrence a row of this definition already answers, in any
       state (:func:`_answered_by_rows`);
    4. date what is left as generation would date its row
       (:func:`~app.services.recurrence.compute_due_date`, ruling **R-R69**)
       and price it through the amount model's own arm
       (:func:`~app.services.cash_ledger.definition_cash`, ruling **R-R67**).

    Occurrences PAST as well as future are priced (ruling **R-R64**): an
    occurrence the schedule places that nothing answers is "not generated
    yet", and clamping its visible date to ``as_of + 1d`` is exactly what
    the PLANNED tier does with the overdue projected row generation would
    have written (ruling D1).

    The walk's window (*through*) is the caller's: :func:`._plan.loan_plan`
    hands the later of the post-contractual extension's end
    (``_plan._extension_dates``) and the same span past the read, so a
    matured loan still owing is walked past today.  A definition still
    firing past the window belongs to a loan the plan cannot retire, which
    :func:`._plan_fold.timeline_payoff_date` reports as ``None``.  An occurrence
    the loan's latest assertion subsumes (due at or before it) is priced
    here and dropped by the caller, on the one predicate its planned rows
    are dropped on (ruling **R-R72**).

    Args:
        account: The loan account.  Must belong to ``ctx.user_id``.
        resolved: The pass's resolution of the loan -- its definitions and
            its params.
        ctx: The read pass -- its calendar places the occurrences, its
            scenario scopes the rows, its basis prices the occurrences.
        as_of: The read pass's as-of -- the clamp floor is ``as_of + 1d``.
        through: The last day the walk covers.

    Returns:
        One :class:`PlannedPayment` per unanswered placeable occurrence of
        every definition (``is_estimated=True``), in walk order per
        definition; the caller sorts.

    Raises:
        AmountUnresolvable: When a definition states no price for an
            occurrence (an empty series, a derive-mode payment whose loan will
            not resolve) -- exactly where its written row would refuse.
    """
    basis = ctx.amounts()
    clamp_floor = as_of + _ONE_DAY
    estimated: list[PlannedPayment] = []
    for template in resolved.definitions:
        for placement, due in _unanswered_placements(template, ctx, through):
            cash = definition_cash(
                DefinitionRow(
                    template=template,
                    due_date=due,
                    period_start=placement.period.start_date,
                    to_account_id=account.id,
                ),
                basis,
                subject=(
                    f"Template {template.id}'s occurrence "
                    f"{placement.occurrence.isoformat()}"
                ),
            )
            estimated.append(PlannedPayment(
                due_date=due,
                effective_date=max(due, clamp_floor),
                cash=cash,
                is_estimated=True,
            ))
    return estimated


__all__ = ["estimated_from_definitions"]
