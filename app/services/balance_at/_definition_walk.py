"""Balance-at-T seam -- the DEFINITION walk: a recurring definition's unwritten occurrences.

**What a generate pass would write for one recurring definition and has not**
(plan step ``pay_calendar:C9``, task plan#3939): the three steps between a
definition and the rows it still owes, each the one generation itself takes,
so the seam's estimate and a later pass cannot disagree about which
occurrences a row will answer or what day it will carry.

1. **Place** each occurrence the rule names on the paycheck its row would live
   in, saved or projected
   (:func:`~app.services.recurrence.projected_occurrence_placements`), under
   the rule's AUTHORED closing alone and above its definition's books (rulings
   **R-R65**, **R-PC85**, **R-PC86**).  An occurrence the schedule cannot place
   is dropped (ruling **R-R64**'s boundary).
2. **Drop** each occurrence a row of the definition already answers, in ANY
   state (:func:`~app.services._recurrence_common.occurrences_to_write`, the
   generate decision both engines take, over the definition's own table,
   :func:`~app.services._template_rows.row_selector_for`; ruling **R-R66**).
3. **Date** what is left as its row would be dated
   (:func:`~app.services.recurrence.compute_due_date`, ruling **R-R69**).

**It takes any recurring definition**, a transaction template or a transfer
template, and **its caller states which paychecks it walks**
(:class:`Paychecks`): the loan's estimated tier prices an unanswered occurrence
inside the saved schedule as well as past it (ruling **R-R64**), and the cash
tier only past it (ruling plan#3932), because inside the saved schedule the
saved rows are the plan and an in-schedule gap is in the past.  Pricing is the
caller's: a loan payment through ``definition_cash``
(:mod:`._plan_definitions`), and nothing here reads a figure off any row.

Extracted from :mod:`._plan_definitions`, which held these steps for the loan
alone (plan step **R16-b-2**), so the loan and the cash tier walk a definition
through one body rather than two copies (``CLAUDE.md`` rule 14).

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes.  Seam-PRIVATE.
"""

from datetime import date
from enum import Enum
from typing import NamedTuple

from app.services._recurrence_common import occurrences_to_write
from app.services._template_rows import row_selector_for
from app.services.pay_calendar import DerivedPeriod
from app.services.recurrence import (
    compute_due_date,
    projected_occurrence_placements,
)

from ._context import BalanceContext


class Paychecks(Enum):
    """Which paychecks a definition walk places unwritten occurrences on.

    Members:
        SAVED_OR_PROJECTED: Every paycheck, saved or projected.  The loan's
            estimated tier: an occurrence the saved schedule places that no
            row answers is "not generated yet" and is priced, past or future
            (ruling **R-R64**), so the loan's payoff cannot move when
            generation later writes that row.
        PROJECTED: Only paychecks past the owner's last saved one
            (:attr:`~app.services.pay_calendar.DerivedPeriod.is_projected`).
            The cash tier (ruling plan#3932): inside the saved schedule the
            saved rows are the plan, so every balance step there stays
            explained by a row, and a gap there is in the PAST (generation
            writes from about today forward), where an estimate would land
            all at once tomorrow under ruling R-G.
    """

    SAVED_OR_PROJECTED = "saved_or_projected"
    PROJECTED = "projected"

    def admit(self, period: DerivedPeriod) -> bool:
        """Return whether an occurrence placed on *period* is walked.

        Args:
            period: The saved or projected paycheck the occurrence's row would
                live in.

        Returns:
            ``True`` when this walk keeps an occurrence placed there.
        """
        return self is Paychecks.SAVED_OR_PROJECTED or period.is_projected


class UnwrittenOccurrence(NamedTuple):
    """One occurrence of a definition that no row answers, placed and dated.

    Attributes:
        template_id: The id of the definition it is an occurrence of, so a
            reader handed occurrences of several definitions can tell whose
            each is: the cash fold's estimate reads a loan payment's
            occurrences back off the loan's plan, which holds every
            definition paying the loan (ruling plan#3932).  Unique within one
            template kind; a reader mixing transaction and transfer templates
            keys by the kind as well.
        occurrence: The date the rule's cadence names, the ``occurs_on`` its
            row would carry.
        period: The saved or projected paycheck its row would live in.
        due_date: The date its row would be due on
            (:func:`~app.services.recurrence.compute_due_date`), derived once
            here so its pricing reads it rather than deriving it again.
    """

    template_id: int
    occurrence: date
    period: DerivedPeriod
    due_date: date


def unwritten_occurrences(
    template,
    ctx: BalanceContext,
    *,
    through: date,
    paychecks: Paychecks,
) -> list[UnwrittenOccurrence]:
    """Return *template*'s occurrences through *through* that no row answers, dated.

    The three steps the module docstring names, for one definition.  The rule
    is resolved through the pass's memo
    (:meth:`~._context.BalanceContext.resolved_recurrence_of`), which attaches
    the definition's books floor and carries the AUTHORED closing alone
    (``derived`` is ``None`` by construction).  Never the composed door
    (``recurring_definition.resolved_definition``): for a loan payment its
    derived stop is the loan's payoff, which the loan's own fold computes from
    this walk, and a fold reading its own answer is the fixed point ruling
    **R-R65** refused.  Every stored closing bound is its owner's word since
    plan step R7d-g (ruling **R-R56**'s arm went with the column's last
    writer), so there is no cache to read around.

    The walk floors an occurrence whose row would land on or before the books
    of an account the definition moves money in (rulings **R-PC85**,
    **R-PC86**), as it does for generation.  For a loan payment, an occurrence
    at or before the loan's latest assertion is left to the caller
    (``_plan.loan_plan``, ruling **R-R72**).

    **Every state of a row answers its occurrence** -- immutable, overridden,
    cancelled, soft-deleted -- exactly as the generate path counts them: a row
    the owner removed must not be resurrected by an estimate any more than by
    a pass.  A row answering no occurrence (``occurs_on`` NULL) claims its
    whole paycheck, which a projected paycheck (no ``period_id``) can never
    be.

    Args:
        template: The recurring definition, a
            :class:`~app.models.transaction_template.TransactionTemplate` or a
            :class:`~app.models.transfer_template.TransferTemplate`, carrying a
            rule.
        ctx: The read pass: its calendar places the occurrences, its scenario
            scopes the rows.
        through: The last day the walk covers.
        paychecks: Which paychecks the caller walks (:class:`Paychecks`).

    Returns:
        One :class:`UnwrittenOccurrence` per occurrence placed on an admitted
        paycheck that no row of *template* answers, ascending by occurrence;
        empty for an owner with no pay periods.

    Raises:
        TypeError: When *template* is neither kind of definition
            (:func:`~app.services._template_rows.row_selector_for`).
    """
    resolved = ctx.resolved_recurrence_of(template.recurrence_rule)
    if resolved is None:
        # An owner with no pay periods has nothing to place on; the seam
        # cannot reach here with one (the calendar seeds the pass), and the
        # resolver's ``None``, which the memo hands back unchanged, is
        # honoured rather than second-guessed.
        return []
    placed = [
        placement
        for placement in projected_occurrence_placements(
            resolved, ctx.calendar(), through=through,
        )
        if placement.period is not None and paychecks.admit(placement.period)
    ]
    rule = template.recurrence_rule
    return [
        UnwrittenOccurrence(
            template_id=template.id,
            occurrence=placement.occurrence,
            period=placement.period,
            due_date=compute_due_date(
                rule, placement.occurrence, placement.period,
            ),
        )
        for placement in occurrences_to_write(
            row_selector_for(template, ctx.scenario_id), placed,
        )
    ]


__all__ = ["Paychecks", "UnwrittenOccurrence", "unwritten_occurrences"]
