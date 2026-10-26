"""
Shekel Budget App -- Pay-Period Template Population

Fills a set of pay periods with each active template's recurring rows --
transactions AND transfers -- in one pass.  This is the orchestrator the ROUTE
runs after the extend, regenerate, reset or rolling-top-up door has created
new, empty periods; until ruling **R-R38** those doors ran it themselves.

It lives in its own module rather than inside either recurrence engine
because it must call BOTH: the transaction engine
(``recurrence_engine.generate_for_template``) and the transfer engine
(``transfer_recurrence.generate_for_template``).  The transfer engine
already imports the transaction engine, so co-locating this orchestrator
with either one would create an import cycle; a neutral module that
imports both (and is imported by neither) keeps the graph acyclic.

**It TAKES the generate pass's read context and never builds one** (ruling
**R-R38**).  A first build of plan step R7d-c-1 had this module call
``BalanceContext.build`` itself, on the ground that a pass opened by a writer
AFTER its own write is the first one that can see the write.  The developer
refused that as a band-aid: the ROOT CAUSE is that ``extend_pay_periods``
performed a write and then a READ-DEPENDENT write in one call, so no caller
could get between them, and a module opening its own pass is what a caller
with no seam has to do.  The doors SPLIT instead -- each records its paydays
and returns, and the route opens the pass and calls this -- so the ordering
that matters (the pass is resolved AFTER the periods exist and BEFORE the rows
do) is the shape of the code rather than a paragraph, and
``pay_calendar:C11``'s layer predicate ("no module under ``app/services/**``
calls ``BalanceContext.build``") needs no carve-out for this module.

**What the stale-pass hazard actually was**, kept here because the rule that
answers it is now the call ORDER at the route and nothing in the type system
restates it.  A pass built BEFORE the pay-period write holds the pre-write
calendar -- which :meth:`~app.services.generation_schedule.GenerationSchedule.__post_init__`
refuses for ``for_period_ids``, because the new ids are not in it -- and the
pre-write LOAN, which nothing catches: since plan step R7d-c-2 a loan payment's
closing bound is a fold over the loan's forward plan, and a pass memoizes each
loan's resolution for its whole life.  Measured on a production clone
(2026-08-27): with a pass built first, deleting the Van Loan's 5 already-due
transfers moved its derived payoff ``2029-02-22`` -> ``2029-04-22`` on a FRESH
pass while the pre-write pass went on answering ``2029-02-22``, and nothing
raised -- the stale date came back as a figure.

Flask-isolated: takes and returns plain data, flushes via the engines,
never commits.
"""

from collections.abc import Iterable

from app.extensions import db
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import transfer_recurrence
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.recurrence_engine import generate_for_template


def populate_periods_from_active_templates(
    ctx: BalanceContext, period_ids: Iterable[int],
) -> int:
    """Generate recurring transactions AND transfers into a set of periods.

    The repopulation step extend, regenerate and reset run after creating
    new, empty periods.  ``pay_period_write.record_paydays`` creates blank
    periods and does NOT call the recurrence engine, so a freshly-appended
    period has none of its rent / paychecks / recurring transfers until this
    runs.  This re-runs BOTH engines -- transactions and transfers, so a new
    period never silently misses a recurring transfer -- over the specific
    *period_ids*, into the owner's baseline scenario (multi-scenario
    repopulation is reserved for later).

    Both engines' shared ``OccurrenceClaims`` skips any occurrence a
    template-linked row already answers, so this is safe to re-run: a retried
    extend / top-up creates nothing and cannot violate either partial unique
    generation index -- ``(template, scenario, occurs_on)`` for a row that
    answers an occurrence, ``(template, scenario, pay_period_id)`` for one that
    answers none (plan step **R17**).

    **This is the caller plan step R4b-1 was written for.**  *period_ids* is
    the newly created batch, and until R4b-1 it was handed to each engine as
    BOTH the schedule the rule was resolved against and the window to write
    into -- so every extend re-read every rule as though the owner's pay
    history began at the new batch.  It produced duplicate ``Monthly First``
    rows and stored a third paycheck $502.45 low on production; the
    measurements live in
    :class:`~app.services.generation_schedule.GenerationSchedule`.  The
    owner's calendar is derived ONCE, on *ctx*, and the batch states only the
    window.

    **ONE read pass serves every template** (plan step R7d-c-1), so the batch
    holds one derivation of the owner's calendar, one baseline-scenario
    resolution, and -- since plan step R7d-c-2 -- one answer per loan to "when
    does this stop", pinned at the state the pay-period write left.  A
    per-template pass would derive the owner's 62-payday calendar once per
    definition.

    **What that does NOT buy is order-independence, and a first draft of this
    paragraph claimed it.**  The claim was that a transfer this loop generates
    into a loan joins that loan's payment feed, so a pass rebuilt per template
    would answer the second definition's bound against rows the first had just
    written.  Measured on a production clone (2026-08-27, the Van Loan):
    deleting all 23 FUTURE transfers into it moved the derived payoff by zero
    days, and HALVING all 23 moved it by zero days -- because the ESTIMATED
    tier prices every uncovered future installment from the DEFINITION (plan
    step R7d-a), so a future row neither adds to the plan nor changes it.  What
    does move it is the PAST: dropping the 5 already-due transfers moved the
    payoff ``2029-02-22`` -> ``2029-04-22``.  This loop creates future rows
    only, so within one batch the order is not observable and sharing the pass
    is a cost and single-derivation argument rather than a correctness one.

    **The pass is TAKEN, and the caller owes it the ordering** -- built after
    the write that created *period_ids* -- which is ruling **R-R38** and the
    module docstring carries the argument.  The route layer states that order
    once, in :func:`app.routes._period_population.populate_new_periods`, and
    every door's caller reads it from there.

    **It takes IDS and forwards no boundary since pay-calendar plan step
    C2-f3c.**  It took ORM rows, read two things off them -- ``.id`` for the
    window and ``periods[0].start_date`` for a boundary -- and forwarded that
    boundary to each engine.  Both are gone, and neither was a behaviour
    change: the window is the batch, every period of the batch ENDS on or
    after the batch's own opening payday, so bounding placements by that date
    could never drop one.  The ``effective_from`` parameter that let a caller
    override it went with the boundary; no caller in ``app/`` or in the suite
    ever passed one, which is the speculative shape ``CLAUDE.md`` rule 13
    forbids.  A caller that genuinely needs a later bound calls the engines
    directly, as the unarchive paths already do.

    Args:
        ctx: The read pass this generation runs inside -- the owner, the
            pinned ``as_of``, the baseline scenario, and the memos every
            derivation on the pass shares.  It must have been built AFTER the
            write that created *period_ids*, or
            :meth:`~app.services.generation_schedule.GenerationSchedule.__post_init__`
            refuses the window.  An owner with no baseline scenario is a
            no-op: ``ctx.scenario`` (the nullable) rather than
            ``ctx.scenario_id`` (which raises), because that has always been
            this path's answer -- ruling R-R30 decides the FORM READ path and
            leaves this one alone.
        period_ids: The ``budget.pay_periods.id`` values to populate.  Must
            already be flushed, and must be this owner's -- both are what
            ``pay_period_write.record_paydays`` returns.  An empty set is a
            no-op.

    Returns:
        The number of template-linked records created (transactions plus
        transfers; a transfer is one row and counts once).
    """
    window = frozenset(period_ids)
    if not window or ctx.scenario is None:
        return 0

    schedule = GenerationSchedule.for_period_ids(ctx, window)

    created = 0
    txn_templates = (
        db.session.query(TransactionTemplate)
        .filter_by(user_id=ctx.user_id, is_active=True)
        .all()
    )
    for template in txn_templates:
        created += len(generate_for_template(
            template, schedule, ctx.scenario_id,
        ))

    transfer_templates = (
        db.session.query(TransferTemplate)
        .filter_by(user_id=ctx.user_id, is_active=True)
        .all()
    )
    for template in transfer_templates:
        created += len(transfer_recurrence.generate_for_template(
            template, schedule, ctx.scenario_id,
        ))

    return created
