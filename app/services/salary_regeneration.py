"""
Shekel Budget App -- What a change to a salary profile does to the rows it prices.

One act: :func:`regenerate_salary_transactions`.  A salary profile is the
producer behind every paycheck row its template generated, so any write to the
profile or its line items -- a raise added, edited, ended or removed, a
deduction changed, the profile re-priced -- is followed by the SAME move: the
projected paycheck rows from today are regenerated under the new terms.

**What that move keeps in step, stated exactly** (an adversarial review of
plan step salary:S3-f-3 corrected a draft that claimed more).  A projected
paycheck row stores NO figure since plan step balance:X-au-d: the engine writes
a declaration and the cash ledger's rule 2 prices the row at read time from
the profile's own rows, so a raise write with no regeneration at all moves
every projected paycheck the moment it commits.  What the regeneration keeps
current is the ROW SET, which a cadence or window change redraws.  Two doors
that write a raise leave the same thing behind, or they are two doors with two
outcomes (CLAUDE.md rule 14).

**It re-stated the template's ``default_amount`` at today's net too, until
plan step X-bp**, and by then the copy had one reader left: the profile's
archive, which opened the template's price series at it, so a raise whose
date had passed since the last salary save was missing from the series it
opened.  The archive prices today's paycheck instead since that step
(``routes/salary/profiles.delete_profile``), as the Recurring and savings
pages and the template's edit page already did
(``obligations_aggregator.todays_price``).  A salary profile's template owns
no stated amount, so the write door now refuses one
(``template_amount_service.set_amount``).

**It lived in the salary ROUTES package until plan step salary:S3-f-3**
(``routes/salary/_helpers._regenerate_salary_transactions``; ruling
**R-SAL24**), and moving it here is a correction rather than a size fix.  It
writes money rows (and priced money too, until plan step X-bp), which is the
reason
:mod:`app.services.salary_profile_service` gives for being a service and not
two lines in a route -- and S3-f-3 gave the ``/retirement`` assumptions rail a
second door that writes a raise's end year.  A door on another blueprint could
not reach a private module of the salary package (the
``shekel-private-module-import`` gate is exactly that fence), so the walk
every raise write is followed by either moved below both routes or was
spelled twice.  Rule 14 says once.

**Flask-free, which is what the move changed.**  The route-tier original built
its own read pass from ``current_user`` and FLASHED the rows the regeneration
declined to touch.  This takes the pass the route already opened
(a producer below the route takes the :class:`~app.services.balance_at
.BalanceContext` and drops ``user_id``; only a route builds one) and RETURNS
those row ids, so each route reports them in its own voice --
``flash_retained_notice`` for both today.  The day the regeneration runs from
is the pass's pinned ``as_of`` (ledger row **SAL-572**, plan step
salary:X-av-3a).  The one reordering: the pass is built BEFORE the template
guard below runs, where the route-tier original guarded first -- one scenario
query for a profile without a template, a state ``create_profile`` never
produces.  The route package's adapter built it until task plan#3106; each
door builds it now and the adapter takes it, so a door that read the
calendar before its write hands that same pass here.

**It prices nothing since plan step X-bp**, so the pass is read for its
owner's schedule, its baseline scenario and its day; the requirement it once
stated -- that the pass POSTDATE the write it follows, because the pass's
pricer memoizes per payday and the re-statement read today's paycheck from
it -- went with the re-statement.

Boundary discipline (``CLAUDE.md`` Architecture): ORM rows and a pass in, plain
ids out.  Flushes through the engine and does not commit -- the caller owns the
unit of work, because the regeneration is one optimistic-locked transaction
with the write that caused it.
"""

import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm.exc import StaleDataError

from app.exceptions import RecurrenceConflict
from app.models.salary_profile import SalaryProfile
from app.services import recurrence_engine
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule

logger = logging.getLogger(__name__)


def regenerate_salary_transactions(
    ctx: BalanceContext, profile: SalaryProfile,
) -> list[int]:
    """Regenerate the profile's paycheck rows from the pass's day.

    The one walk every salary write is followed by (module docstring).  The
    pass pins the owner, the day and the baseline scenario, and its calendar
    is the one the regeneration resolves against (plan steps R7d-c-1 and
    R4b-1; pay-calendar plan steps C2-f2d-3 and C2-f3c), so there is one value
    and no second read to reconcile it against.

    A profile with no template, or an owner with no baseline scenario, has no
    rows to regenerate and is left alone.

    Args:
        ctx: The request's read pass.
        profile: The :class:`~app.models.salary_profile.SalaryProfile` whose
            template prices the rows.

    Returns:
        The ids of the projected rows the pass LEFT ALONE because the owner
        has purchases or notes recorded against them (plan step R10-a) --
        empty when every row took the change.  The caller tells the owner;
        a retained row is a silent no-op unless it is said out loud.

    Raises:
        sqlalchemy.orm.exc.StaleDataError: Re-raised from the regeneration's
            flush, logged at INFO with its message and no traceback: a row
            this unit of work writes -- the write this walk follows, or a
            paycheck row the walk updates in place -- lost a version race,
            which the calling route reports in its own voice; it is not a
            failure of the regeneration (task plan#3126).
        sqlalchemy.exc.SQLAlchemyError: Any other, re-raised from the
            regeneration's flush after the profile id is logged (C-46 /
            F-145), so the route's own handler still reports it.
    """
    if not profile.template:
        return []
    if ctx.scenario is None:
        return []

    # The id the failure log names is read BEFORE the walk.  A flush that
    # fails inside it rolls the session back and expires the profile, and
    # reading ``profile.id`` in the handler below raised
    # ``PendingRollbackError`` in place of the error it was logging.  A door
    # whose pass is built after its write flushes in that pass's scenario
    # query, before this walk, so its write's own failures never reach here;
    # the ``/retirement`` rail's Save builds its pass first, and since task
    # plan#3106 the salary update and Fix doors hand over a pass built before
    # their writes too.  A lost version race was how it showed: the route
    # reported its generic failure rather than its conflict (measured
    # 2026-11-09, task plan#3106).  A race leaves by the ``StaleDataError``
    # arm since task plan#3126, which logs this id too; any other flush
    # failure reaches the handler below, which would meet the expired profile
    # if it read the id itself.
    profile_id = profile.id

    # The regeneration runs from the PASS's pinned day, read once (ledger row
    # SAL-572, plan step salary:X-av-3a).
    try:
        recurrence_engine.regenerate_for_template(
            profile.template, GenerationSchedule.for_pass(ctx), ctx.scenario_id,
            effective_from=ctx.as_of,
        )
    except RecurrenceConflict as e:
        logger.warning("Recurrence conflict during salary regeneration: %s", e)
        # The override / soft-delete halves of this conflict are deliberately
        # swallowed here -- a salary regeneration preserves them and there is
        # nothing for the operator to decide -- but a retained row means this
        # pass declined to apply the profile's change to a row carrying their
        # own records, which the CALLER must say out loud (plan step R10-a,
        # adversarial review): it is returned, never dropped.
        return list(e.retained)
    except StaleDataError as lost:
        # A lost version race is not a failure of the regeneration: another
        # save moved a row this unit of work writes -- the write this walk
        # follows, or a paycheck row the walk updates in place -- and the
        # calling route reports it in its own voice, a caller with a stale
        # arm as its designed conflict and one without as its generic
        # failure.  Caught by the arm below, as the SQLAlchemyError it also
        # is, every race lost inside the walk was an ERROR with a traceback
        # beside the route's report (task plan#3126).  It is logged here at
        # INFO, the level a conflict is logged at, because only its message
        # says WHICH row lost: a route's conflict line names the door's row,
        # so a paycheck row's race would leave no record of the table.
        logger.info(
            "Lost a version race regenerating salary transactions for profile %d: %s",
            profile_id, lost,
        )
        raise
    except SQLAlchemyError:
        # Narrow catch (C-46 / F-145): logging hook that re-raises.
        # SQLAlchemy errors from the regenerate flush get the
        # profile-id context here as well as in the calling route's
        # ``except SQLAlchemyError`` block.  Non-SQLAlchemy
        # exceptions still propagate to the caller without this
        # extra log line; the caller's logger.exception then
        # records the user-id + profile-id context.
        logger.exception("Failed to regenerate salary transactions for profile %d", profile_id)
        raise
    return []


__all__ = ["regenerate_salary_transactions"]
