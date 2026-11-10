"""
Shekel Budget App -- The rolling pay-period window.

The ONE opportunistic, non-destructive schedule writer, split out of
``pay_period_admin`` at plan step **C4** (ledger row **P31**).  That module
holds the four DESTRUCTIVE doors -- extend, truncate, regenerate, reset -- each
of which a user initiates and each of which can take money's paycheck away from
it.  This one is none of those things: nothing asks for it, ``/grid`` and
``/dashboard`` call it on every render, and all it may do is APPEND paydays the
owner's own stored cadence already implies.

**The split is a SHAPE rather than a size fix, and the size is how it was
found.**  ``pay_period_admin``'s own module docstring named its subject as "the
lock classifier and extend / truncate / regenerate" and did not mention the
top-up, which is the seam stated and then not drawn.  It stood at 991 of
pylint's 1,000-line ceiling, so plan step C4's first commit -- ONE reader moved
off a dropped column -- could not fit, and the room would have come from
deleting prose.  That is finding **P31** exactly: "answered the ceiling by
trimming prose rather than by having a shape".

Flask-isolated, like the module it left: it takes and returns plain data, never
imports ``request`` / ``session``, and never commits (the route owns the
transaction).  It DECIDES and delegates -- every row it adds goes through
``pay_period_admin.extend_pay_periods`` and thence ``pay_period_write``, the one
place in ``app/`` that changes ``budget.pay_periods``.  The dependency runs one
way: this module imports that one, and nothing there reaches back.

**It RETURNS the periods it appended, where it returned a count** (plan step
R7d-c-1, ruling **R-R38**).  The append leaves them EMPTY: the door it calls
no longer generates the recurring rows, because the read pass that generation
resolves in may only be opened above this layer, and a count is not something
a caller can populate.  So the two render routes that call this --
:func:`app.routes.grid.page.index` and :func:`app.routes.dashboard.page` --
hand what comes back to
:func:`app.routes._period_population.populate_new_periods` inside the same
``write_transaction`` block.  A caller that drops the return value creates
paydays with no rent, no paycheck and no recurring transfer in them, which is
why the routes' own tests assert the ROWS rather than the period count.

**Nothing here reads ``end_date`` or ``period_index``**, the two columns plan
step C4-c dropped -- and that stays a graded property rather than a
consequence, because
:class:`~app.services.pay_calendar.DerivedPeriod` still carries both names.
``test_pay_period_admin.TestTheDestructiveDoorsHoldNoDerivedColumn`` censuses
this module and ``pay_period_admin`` for either name on ANY receiver, so a
future edit cannot regrow the read on a derived value either.
"""

from datetime import date

from app.services import pay_period_admin, pay_schedule_service
from app.services.pay_calendar import calendar_at_schedule
from app.utils.dates import display_today


def top_up_rolling_window(user_id, as_of=None):
    """Generate periods to keep the rolling window N ahead of today.

    The on-request continuous-mode top-up, called from the grid and
    dashboard entry points (the only routes that consume future
    periods).  No scheduler exists, so the window is refilled lazily on
    page load.

    Cheap and idempotent.  When rolling is disabled (or the user has no
    schedule row) it does ZERO write work -- one tiny schedule read.
    Otherwise it counts the current-and-future periods (those whose DERIVED
    end falls on or after ``as_of``, which INCLUDES the period containing
    ``as_of``, so "keep N ahead" counts the current period as one of the N)
    and, only if short of the target, appends exactly the deficit via
    :func:`~app.services.pay_period_admin.extend_pay_periods`, which leaves
    the new periods EMPTY for the caller to populate (see the module
    docstring).

    **Every read here is taken under the owner's write lock**, which the
    caller's ``write_transaction`` block took when its transaction began
    (plan step ``balance:X-bn``, ruling **R-CC114**: a page load's write
    takes the lock like any save), so a second render racing this one waits
    for its commit and then counts a full window.  *Until that step this
    function counted first, took the lock only on a deficit, and then
    RE-READ the schedule and RE-COUNTED under it; ruling R-CC115 deleted the
    acquisition, and the second read went with the reason for it.*
    Correctness against a duplicate payday comes from
    ``UNIQUE(user_id, start_date)``; the lock is the UX layer that lets a
    racing loser cleanly create nothing instead of hitting that constraint as
    a 500.

    **It passes no cadence, and that is not a saving of one argument.**  It
    used to hand ``pay_period_admin.extend_pay_periods`` the schedule row's own
    ``cadence_days``, which is exactly what ``resolve_cadence`` answers for an
    owner who has a row -- and this function returns before the append unless
    one exists.  A redundant pass-through of a value the callee re-reads is a
    second place for the two to come apart; plan step C3-b deleted the
    parameter at every door (finding **P29**).

    Args:
        user_id: The owning user's id.
        as_of: Reference date for "current and future".  Defaults to the
            OWNER's civil day (``utils.dates.display_today``) rather than the
            process clock, ruled 2026-08-19 with the lock classifier's -- this
            counts the owner's remaining paychecks, so it is their day that
            decides how many there are.  Both live callers (``/grid`` and
            ``/dashboard``) pass none.

    Returns:
        The newly created :class:`~app.models.pay_period.PayPeriod` rows,
        flushed and EMPTY -- the caller populates them.  An empty list when
        rolling is disabled, the window is already full, or a concurrent
        top-up filled it first.
    """
    if as_of is None:
        as_of = display_today()

    schedule = pay_schedule_service.get_schedule(user_id)
    if schedule is None or not schedule.rolling_enabled:
        return []
    # An owner holding the row and no ERA has stated no rhythm (plan step
    # pay_calendar:C17-a), so there is nothing to continue; the same answer
    # a row-less owner gets, one tier down.
    facts = pay_schedule_service.ScheduleFacts.of(schedule)
    if facts is None:
        return []

    deficit = schedule.rolling_target_periods - _future_period_count(
        user_id, facts, as_of,
    )
    if deficit <= 0:
        return []

    # No handler.  This is an opportunistic write on a READ path -- ``/grid``
    # and ``/dashboard`` call it with no handler of their own -- so anything
    # raised here is a 500 on both of the app's main screens.
    #
    # The FORWARD-ONLY floor passes by construction: the batch continues the
    # stored cadence and every payday it records falls after the last existing
    # one.  That is the only refusal this comment can prove, and a first draft
    # of it claimed all of them -- caught by an adversarial review of the
    # coverage-rule deletion, which reached the 500 by running it.
    # **Plan step pay_calendar:C14-e-2 changed WHERE the batch continues FROM
    # -- the stored grid phase rather than the last recorded payday -- and the
    # sentence survives because the producer is asked against the last
    # paycheck's END, which is the floor's own subject.  An adversarial review
    # of that step found the first cut asking against the last recorded PAYDAY
    # instead, a whole cadence lower, which refused ordinary extends
    # permanently right here.  This is the comment that says it has been wrong
    # once before; it is now checked by
    # ``test_a_PIECEWISE_owner_whose_tail_was_truncated_can_still_extend``
    # rather than by a reader.**
    # **It STAYED true through plan step ``pay_calendar:C14-e-3``, and the
    # paragraph above predicted it would not** (ledger row
    # **pay_calendar:PC-497** fault 1, opened by ``C14-d``).  The hazard was
    # real: the floor is the last paycheck's DISPLACED end, and while
    # ``record_paydays`` recorded the NOMINAL grid day it was handed, a
    # forward convention put that day inside the paycheck the calendar derives
    # -- a refusal HERE, with no handler, on both of the app's main screens.
    # ``C14-e-3`` closed it at the writer instead: ``requested_paydays``
    # records each element displaced, so the day offered and the floor are one
    # value again.  Graded by
    # ``test_a_FORWARD_convention_is_ACCEPTED_and_that_CLOSES_PC_497``.
    # **The CADENCE refusal that used to reach this line is gone** (plan step
    # ``pay_calendar:C4-c``, closing ledger row **pay_calendar:P33**).  The
    # writer refused a stored cadence below 2 while a stored ``end_date`` had
    # to satisfy ``start < end``, and ``ck_pay_schedule_cadence_range`` admits
    # 1 -- so an owner holding that value met a 500 HERE, on both of the app's
    # main screens, permanently.  The column is dropped and a one-day cycle is
    # an ordinary schedule, so the extend below has nothing left to refuse it
    # for.  It was deliberately NOT swallowed while it existed: a schedule the
    # app could not render is not a refusal to shrug off, and an opportunistic
    # writer needing a swallow was the clearest evidence the rule was wrong.
    # (The coverage rule, deleted 2026-08-11, was the other refusal that
    # reached here, and it WAS swallowed with a WARNING.)
    # **Neither the floor nor the CEILING is asked of this batch at all
    # since plan step ``C17-c-2b``** (rulings R-PC75, R-PC78): the door
    # records a prefix of ``pay_calendar.planned_paydays_after``, the one
    # sequence both fences read, so the first day recorded IS the floor and
    # falls before the ceiling by construction.  Between ``C17-c-2a`` and
    # this step the ceiling COULD reach this line (ruling R-PC76's stated
    # transitional hazard): the extend door restated the LATEST era from the
    # horizon (ledger row PC-509), so an owner truncated below a later era
    # of LONGER cadence was offered a payday that skipped one of the kept
    # rhythm's, refused here with no handler on every render.  The two steps
    # deployed together so no owner met it.
    return pay_period_admin.extend_pay_periods(user_id, deficit)


def _future_period_count(
    user_id: int,
    facts: pay_schedule_service.ScheduleFacts,
    as_of: date,
) -> int:
    """Count the user's current-and-future periods (those not ended by *as_of*).

    Includes the period containing ``as_of``, so this is the count the rolling
    target is compared against: "keep N ahead" counts the current period as one
    of the N.  The rule is
    :meth:`~app.services.pay_calendar.PayCalendar.current_and_future`.

    **It DERIVES the ends rather than reading them** (plan step C4, finding
    **P70**): this was the module's last query naming a column plan step C4-c
    dropped -- ``PayPeriod.end_date >= as_of``, counted in SQL -- and no period
    end is named here at all.

    **It loads at the schedule facts the caller HOLDS rather than calling**
    :func:`~app.services.pay_calendar.calendar_for`, which would re-read the
    schedule row :func:`top_up_rolling_window` already has.  It takes the
    FACTS rather than the two columns, which is what plan step balance:X-bh-2
    made matter: a calendar needs both, and
    :meth:`~app.services.pay_schedule_service.ScheduleFacts.of` is the one
    place that says which columns those are -- so a third calendar fact
    reaches here without this signature moving.  Plain data rather than the
    ORM row, for this package's standing reason: the row carries the rolling
    configuration too, and a helper that took it could quietly start reading
    a field that is not a calendar fact at all.  It cannot take a
    read pass's calendar instead -- ``/grid`` and ``/dashboard`` run the top-up
    BEFORE they open one, deliberately, so that pass sees the rows this
    creates.

    **What that costs, stated rather than absorbed** (adversarial review,
    2026-08-25).  It is the same ONE query the ``COUNT(*)`` was, and the
    schedule read ledger rows **P68** and **P69** record is not doubled -- but
    the render now runs the DERIVATION twice, where the SQL count ran it not at
    all.  That second derivation is unavoidable while the top-up precedes the
    pass, so it is pinned rather than removed:
    ``test_a_rolling_owner_derives_twice_and_that_is_the_bound``, in
    ``tests/test_arch/test_one_read_pass_per_render/test_calendar_derivations.py``,
    holds it at exactly two.  Its cost is a payday set and N frozen dataclasses
    rather than one integer -- 62 rows on production, immaterial there and
    growing with an owner's history, which is the axis nothing bounds.

    Args:
        user_id: The owning user's id.
        facts: The caller's own resolved schedule facts.  ``cadence_days`` is
            read only for the LAST period's end, so a stale one can move
            exactly one period in or out of this count; ``history_opens_on``
            is not read by this question at all and travels only because a
            calendar carries both -- and dropping it here would build a
            calendar that lies about the owner's rhythm.
        as_of: The reference date.

    Returns:
        The number of periods whose DERIVED end is on or after ``as_of``.
    """
    return len(calendar_at_schedule(user_id, facts).current_and_future(as_of))
