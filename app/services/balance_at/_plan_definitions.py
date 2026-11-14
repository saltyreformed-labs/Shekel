"""Balance-at-T seam -- the loan's ESTIMATED tier, priced from its definitions.

Plan step **R16-b-2**.  What a generate pass over the owner's whole schedule
would write for every recurring transfer into a loan, priced as those rows
would be priced -- the summing leaf of the R16 arc, split out of :mod:`._plan`
when that module crossed pylint's 1000-line ceiling (see
:mod:`._plan_records` for the split and its reasons).  :mod:`._plan` calls
:func:`estimated_from_definitions` from its assembly and owns the contract-only
estimate, the charge calendar and the read pass's memo; this module prices
each occurrence a definition still owes the loan and imports nothing from
``_plan``.  WHICH occurrences those are -- placed, answered by no row, dated --
is the seam's one definition walk (:mod:`._definition_walk`, plan step
``pay_calendar:C9``), which the cash tier walks too; this module held those
steps for the loan alone until that step.

The rulings it applies are stated on the function: **R-R64** (an occurrence
no row in any state answers is priced, past or future, bounded to the
schedule: the walk's :attr:`~._definition_walk.Paychecks.SAVED_OR_PROJECTED`),
**R-R65** (the authored closing alone; the derived stop is the fold's own
output), **R-R66** (``budget.transfers`` read for identity only), **R-R67**
(the amount model's own arm) and **R-R69** (dated as the row would be).  The
occurrences it prices are bounded BELOW twice, by two different facts.  The
walk drops every occurrence whose row would land on or before the books of an
account the definition moves money in (rulings **R-PC85**, **R-PC86**), as it
does for generation.  And the caller drops an occurrence due at or before the
loan's latest assertion -- its origination at the earliest, which is ruling
R-C's refusal -- in :func:`._plan.loan_plan`, on the one post-anchor predicate
(ruling **R-R72**).  An adversarial review found a definition whose unlocked
start predated its loan paying for months the loan did not exist; a second
predicate in the walk for the same boundary was measured redundant by the
review after it.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.  Seam-PRIVATE.
"""

from datetime import date

from app.models.account import Account
from app.services.cash_ledger import DefinitionRow, definition_cash

from ._context import BalanceContext
from ._definition_walk import Paychecks, unwritten_occurrences
from ._plan_records import _ONE_DAY, PlannedPayment
from ._resolution import ResolvedLoan


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

    1. walk it through the seam's one definition walk
       (:func:`~._definition_walk.unwritten_occurrences`) on every paycheck,
       saved or projected (:attr:`~._definition_walk.Paychecks.SAVED_OR_PROJECTED`):
       its occurrences under its AUTHORED closing alone, each placed on the
       paycheck its row would live in, less any a row of the definition
       answers in any state, each dated as generation would date its row
       (ruling **R-R69**).  The authored closing because the composed door's
       derived stop is a loan's closing date, which is THIS fold's output, and
       a fold reading its own answer is the fixed point ruling **R-R65**
       refused to scaffold; so every definition's authored end date is
       honoured (ruling **R-R37**) and a payment with none runs to the fold's
       own zero crossing;
    2. price each occurrence through the amount model's own arm
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
        for unwritten in unwritten_occurrences(
            template, ctx,
            through=through, paychecks=Paychecks.SAVED_OR_PROJECTED,
        ):
            cash = definition_cash(
                DefinitionRow(
                    template=template,
                    due_date=unwritten.due_date,
                    period_start=unwritten.period.start_date,
                    to_account_id=account.id,
                ),
                basis,
                subject=(
                    f"Template {template.id}'s occurrence "
                    f"{unwritten.occurrence.isoformat()}"
                ),
            )
            estimated.append(PlannedPayment(
                due_date=unwritten.due_date,
                effective_date=max(unwritten.due_date, clamp_floor),
                cash=cash,
                is_estimated=True,
            ))
    return estimated


__all__ = ["estimated_from_definitions"]
