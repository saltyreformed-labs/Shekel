"""The one door that reads an owner's pay calendar out of the database.

Plan step **C2-b1**.  Everything else in this package is pure -- a function of
values a caller supplies -- and that is load-bearing: it lets C1's harness drive
the derivation over production's real 61 paydays and over a generated sweep
without a database, and the two runs exercise the same code.  This module is the
one exception, named and isolated so the boundary is a FILE rather than a
convention, and the package's public surface hides it behind
:func:`calendar_for`.

**Nothing in ``app/`` called this until plan step C2-d**, which is the leaf
boundary C2-b1 shipped it behind -- the same technique C1 and C2-a used, so the
loader was proven on its own against a real schedule rather than inside a
commit that also moved every consumer.  **C2-d was that first consumer**: both
anchor-correction posting writers reach it through
:func:`app.services._posting_reconcile.filing_calendar_for`.  **C2-b2 then
brought the recurrence engine** -- its ten ``recurrence.calendar_for`` call
sites now name this function, and the second calendar type and second loader
they used were deleted, so ``calendar_for`` is one name under ``app.services``
again.  **C2-c brought the cash view and C2-e the projection axis**; both have
shipped, and this sentence listed them as "still to come" until plan step
C2-f2a corrected it.  What remains is ``C2-f``'s own leaves --
``pay_period_service``'s three surviving readers at every surface outside the
balance seam, which stopped importing that module at C2-f2a.

**Why it does NOT call ``pay_period_service.get_all_periods``**, which is the
obvious spelling and is the one this must avoid.  Plan step C2-f points that
module's six ``get_*`` readers AT this value; were the loader to read through it,
that step would close an import cycle, and the fix at that point would be to move
this module -- after ten call sites already name it.  Reading the table here
instead makes the dependency run one way for good: ``pay_period_service`` may
depend on the calendar, never the reverse.

**And why it reads only ``start_date``.**  The payday is the sole fact in the
row (``docs/plans/implementation_plan_pay_calendar.md`` section 1); ``end_date``
and ``period_index`` are derived here from it.  So the query is already written
against the schema plan step C4-c left behind: it dropped both columns and this
module needs no edit for it.  *It said C4 "does not touch this module" until
C4's FIRST commit, which ADDED :func:`calendar_at_schedule` here -- not because the
drop reached the query, but because the rolling top-up needed this read without
the cadence read in front of it.  The claim about the COLUMNS still holds; the
claim about the FILE did not.*

The schedule facts come from ``pay_schedule_service.resolve_schedule`` rather
than from a second query of ``budget.pay_schedule``: that function is the one
place "what are this owner's calendar facts" is answered, and a second copy
would be a second thing to keep in step.  *Until plan step C4-b-2 the reason
was stronger and worse -- that function carried an inferring FALLBACK for an
owner with no schedule row, so a copy would have been a second copy of plan
finding P8's circularity.  ``fk_pay_periods_schedule`` made that owner
unstorable and the fallback went with them, so what is left is ordinary
single-source discipline.*  It answers BOTH calendar facts in one read since
plan step **balance:X-bh-2** -- the cadence and ``history_opens_on``, the floor
the backward rhythm stops at -- so a calendar load is still the two queries it
always was rather than three.
"""

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.services import pay_schedule_service

from ._cadence import PayCadence
from ._calendar import PayCalendar
from ._eras import PayCalendarError


def schedule_for(user_id: int) -> pay_schedule_service.ScheduleFacts:
    """Return *user_id*'s schedule facts, REFUSING an owner who has none.

    Plan step **C4-d**, ruling **R-PC45**.  **The one refusal every public
    door here makes**, written once because it is one state with one
    consequence:
    the owner holds no ``budget.pay_schedule`` row, so nothing states how often
    they are paid, so every per-paycheck figure in the application is
    unanswerable for them.

    **Before this step the two doors disagreed about that owner**, and the
    disagreement is what the ruling closed.  :func:`cadence_for` refused them,
    as it had since plan step R7a-2a.  :func:`calendar_for` answered an EMPTY
    :class:`~._calendar.PayCalendar` whose ``cadence_days`` was ``None`` -- so
    the refusal did not vanish, it was DEFERRED to whichever method first read
    the cadence, which is why ``/savings`` showed a repair page for this owner
    while ``/grid`` and the account detail page each showed a blank render of
    their own.  Three answers to one state.  Refusing here makes it one, and
    the answer is the page ``app/error_handlers.py`` already renders for
    ``PayCalendarError``: "Pay Calendar Unavailable", carrying the repair.

    **The absent value it deleted is the subject of the whole step.**  That
    empty-with-no-cadence calendar was the only thing constructing
    ``cadence_days = None``, and that ``None`` travelled into
    :class:`~._calendar.PayCalendar`, :func:`~._derive.derive_periods` and
    three producers in :mod:`._views`, each of which had to say in prose that
    the state was legal only beside an empty payday set.  With this refusal in
    front of them, none of them can be handed it.

    **Not reachable by any live page, and that is measured rather than
    assumed** (2026-09-02).  Two places in ``app/`` construct a ``User``:
    ``registration_service.register_user``, which writes the schedule row AND real
    paydays since plan step balance:X-ad-a, and
    ``routes/settings.companion_create``, which writes neither.  Nothing in
    ``app/`` deletes a schedule row -- not truncate, not regenerate, not
    ``pay_period_admin.reset_pay_periods``, which passes through "a row and zero
    paydays" and leaves the row.  So the row-less owner is the COMPANION.  On
    production and on the dev clone: one owner with a schedule and 63 paydays,
    one companion with neither.

    **What keeps the companion away from this is the OWNER RESOLUTION and not
    the decorator, which is a correction an adversarial review of this step
    made** (2026-09-02).  A first draft of this paragraph said "every route
    that builds a calendar carries ``@require_owner``, which 404s a companion
    before this is reached".  An AST census of all 224 route functions measured
    that FALSE: three companion-reachable route groups build a calendar with
    no ownership decorator at all -- ``routes/companion`` (``index``,
    ``period_view``), ``routes/entries``' four entry-list doors, and
    ``transactions.mark_done``.  The conclusion survives, for a different and
    better reason: each of those resolves the owner from DATA
    (``User.data_owner_id``, ``txn.user_id`` -- ``txn.pay_period.user_id``
    until plan step ``pay_calendar:C13-b``) and so builds the
    LINKED OWNER's calendar, never the requester's -- and an owner who has a
    transaction or a linked companion has a schedule row.  **A new
    companion-reachable surface spelling ``calendar_for(current_user.id)``
    would 500 here**, where the pre-C4-d loader degraded to an empty calendar;
    the decorator would not stop it, because on these routes the decorator is
    not there.

    **PUBLIC since plan step ``C14-e-2``; it was ``_require_schedule``.**
    That step gave ``budget.pay_schedule`` a third calendar fact -- the
    NOMINAL grid's phase -- which the derivation does not read and the
    extend door did.  A door that holds the facts and the calendar together
    is what let that caller take BOTH from one read; the alternative was a
    second scalar query of a row the same request had just resolved, which
    is precisely the duplicate ``C14-e-1`` deleted (``resolve_shift``).
    Exporting the door that already existed keeps the refusal below in one
    place rather than restating its message at a second caller -- which
    since plan step ``C17-c-2b`` is ``pay_period_write.continue_paydays``,
    reading the eras it materialises the plan of through this door.

    Args:
        user_id: The owning user's id.

    Returns:
        The owner's
        :class:`~app.services.pay_schedule_service.ScheduleFacts`.

    Raises:
        PayCalendarError: The owner holds no ``budget.pay_schedule`` row, which
            since plan step C4-b-2 IMPLIES no pay periods either
            (``fk_pay_periods_schedule``) -- or, since plan step ``C17-a``, a
            row with no era, which states no rhythm to derive from.
    """
    facts = pay_schedule_service.resolve_schedule(user_id)
    if facts is None:
        raise PayCalendarError(
            f"user {user_id} has no pay calendar: they hold no "
            f"budget.pay_schedule row -- and since fk_pay_periods_schedule "
            f"that means no pay periods either -- or a row with no era, so "
            f"neither which paycheck covers a day nor how many paychecks they "
            f"receive in a year is answerable.  Since plan step X-ad-a "
            f"registration writes the row, the era and the paydays together, "
            f"so this is companion data or an owner before their first batch "
            f"rather than a state to default.  "
            f"Assuming biweekly would report a weekly-paid owner's "
            f"commitments at half their true monthly value."
        )
    return facts


def calendar_for(user_id: int) -> PayCalendar:
    """Return *user_id*'s whole pay calendar, derived from their paydays.

    A separate call rather than a lookup hidden inside each consumer, so a
    caller answering many questions loads the schedule ONCE and threads it --
    ``period_population.populate_periods_from_active_templates`` generates a
    batch of rules together, and a per-rule load there would be the same two
    queries repeated N times for one answer.

    Always the owner's COMPLETE payday set, which is
    :class:`~._calendar.PayCalendar`'s one uncheckable precondition (ledger rows
    **P14**, **P26**): a slice of paydays is indistinguishable from a short
    schedule, and deriving over one would give its last period a
    cadence-projected end and renumber every ``period_index`` from zero.  There
    is no ``first``/``count`` argument here and there must not be; a window is
    :meth:`~._calendar.PayCalendar.window`, which returns a
    :class:`~._calendar.PeriodWindow` and keeps the real ends.

    Args:
        user_id: The owning user.

    Returns:
        The frozen :class:`~._calendar.PayCalendar`.  **An EMPTY calendar is
        still a legal answer and now carries a real cadence** -- an owner
        holding a ``budget.pay_schedule`` row and zero paydays, which
        ``pay_period_admin.reset_pay_periods`` passes through and which
        ``balance:X-ad`` (ruling R-DB) made the resting state of a
        mid-setup owner.

        *This paragraph used to say an empty CADENCE-LESS calendar was
        load-bearing for the brand-new owner, citing that same ruling:
        "registration stops writing a bootstrap payday, so a brand-new owner
        holds none and reaches ``/templates`` on their first visit."  Plan step
        balance:X-ad-a SHIPPED (``2a4eb477``) and made the claim false --
        registration now asks for the real payday, cadence and horizon and
        writes ``num_periods`` paydays, so a brand-new owner holds a schedule
        row and a schedule.  The scheduled reason had arrived and been refuted;
        plan step C4-d re-measured it rather than inheriting it.*

    Raises:
        PayCalendarError: The owner holds no ``budget.pay_schedule`` row
            (:func:`schedule_for`, since plan step C4-d); or the owner has
            paydays and no resolvable cadence -- reachable only inside a
            COMMAND, and only if a concurrent truncate lands between the two
            reads below (see the comment there); or the rows cannot define a
            calendar, which for a duplicate payday
            ``uq_pay_periods_user_start`` already prevents.
    """
    # The SCHEDULE ROW is read first, deliberately, and the nesting is what
    # orders the two reads: Python evaluates this argument before the call it
    # feeds.  ``resolve_schedule`` answers both calendar facts from that one
    # read (plan step balance:X-bh-2), so widening the calendar to carry the
    # owner's history bound did not add a third query or a second ordering.
    #
    # **Whether the two reads can differ at all now depends on WHO is asking**
    # (plan step balance:X-i3, ruling `balance:R-GU`).  Inside a QUERY -- every render,
    # which is where the calendar is read most -- the request's transaction is
    # one ``REPEATABLE READ`` snapshot, so both reads see one state of the
    # database and no interleaving is expressible.  Inside a COMMAND they are
    # still separate snapshots: the write doors reach here through
    # ``_posting_reconcile.filing_calendar_for`` and through generation, and a
    # CLI script or a deploy reconcile has no mode at all.
    #
    # So the order below still decides who loses the race, and only a command
    # can now lose it: in this order the loser sees a cadence and fewer
    # paydays, which derives a shorter calendar, while the other order sees
    # paydays and no cadence, which REFUSES.  Narrowing toward the answerable
    # state is the right way to lose a race a lock would otherwise have to
    # prevent.  Since plan step C4-d the schedule read is ``schedule_for``
    # rather than the bare resolve, which is what makes "no schedule row" a
    # refusal here instead of an empty calendar carrying no cadence.
    return calendar_at_schedule(user_id, schedule_for(user_id))


def calendar_at_schedule(
    user_id: int, facts: pay_schedule_service.ScheduleFacts,
) -> PayCalendar:
    """Return *user_id*'s pay calendar from schedule facts the CALLER already holds.

    Plan step **C4**.  :func:`calendar_for`'s body, minus the read that
    resolves the schedule -- for a caller that has the owner's
    ``budget.pay_schedule`` row in hand and would otherwise pay for a second
    read of it.

    **It took a bare ``cadence_days`` and was named ``calendar_at_cadence``
    until plan step balance:X-bh-2**, which gave the calendar a second fact off
    the same row -- and a THIRD at plan step ``C14-e-1``, the payday
    convention, which joined the cadence inside
    :class:`~app.services.pay_rhythm.Rhythm` rather than arriving as a field of its own.  The
    door did not move for it, which is the property
    :class:`~app.services.pay_schedule_service.ScheduleFacts` was shaped to
    have.  A door named for one of the two facts it needs is the drift
    this package spends its docstrings preventing, so the parameter became the
    pair :class:`~app.services.pay_schedule_service.ScheduleFacts` and the name
    followed it.  Passing the pair rather than two arguments is what stops a
    caller supplying one owner's cadence beside another's history bound.

    **One caller today and it is not a convenience** (finding **P70**):
    ``pay_period_rolling._future_period_count`` counts the owner's remaining
    paychecks on the rolling top-up, which ``/grid`` and ``/dashboard`` run
    BEFORE they open their read pass -- deliberately, so that pass sees the
    rows the top-up creates.  So it can neither take a calendar off a pass nor
    call :func:`calendar_for` without re-reading a schedule row it has already
    read, and a redundant per-render schedule query is the defect ledger rows
    **P68** and **P69** record.  This door lets it pay for exactly the one
    query the ``COUNT(*)`` it replaced cost.

    **The payday read lives HERE rather than at that caller**, which is this
    module's whole reason for existing: it is the one place in the package that
    holds a session, so a second ``budget.pay_periods`` query written for a
    caller's convenience would be a second answer to "what are this owner's
    paydays" outside the boundary that makes the rest of the package pure.

    Args:
        user_id: The owning user.
        facts: The owner's ``budget.pay_schedule`` calendar facts, as the
            caller already resolved them.  **Their existence is the argument**
            since plan step C4-d: a caller holds these only by holding the row,
            so ``eras`` is non-empty with an ``int`` cadence each, and there
            is no absent-rhythm pairing for this door to admit or for
            :func:`~._derive.derive_periods` to refuse.
            ``history_opens_on`` is ``None`` for the owner who has stated
            nothing, which is its ordinary value.

    Returns:
        The frozen :class:`~._calendar.PayCalendar` over the owner's COMPLETE
        payday set -- :func:`calendar_for`'s own guarantee, for its reasons.

    Raises:
        PayCalendarError: The rows cannot define a calendar -- a duplicate
            payday, which ``uq_pay_periods_user_start`` already prevents, or
            an era whose cadence falls outside 1..365, which
            ``ck_pay_eras_cadence_range`` already prevents for a stored one.
            Both name a caller rather than a page.
    """
    paydays = (
        db.session.query(PayPeriod.id, PayPeriod.start_date)
        .filter(PayPeriod.user_id == user_id)
        .order_by(PayPeriod.start_date)
        .all()
    )
    return PayCalendar.from_paydays(
        paydays=paydays,
        eras=facts.eras,
        user_id=user_id,
        history_opens_on=facts.history_opens_on,
    )


def cadence_for(user_id: int) -> PayCadence:
    """Return how often *user_id* is paid, without reading their paydays.

    Plan step **R7a-2a**.  The door for a consumer that needs the CADENCE and
    nothing else -- the savings dashboard's monthly-equivalent floor, the DTI
    denominator, the retirement gap's pre-retirement income, an investment
    limit spread over the year.  One query against ``budget.pay_schedule``
    instead of :func:`calendar_for`'s two, and no payday set to build: on
    production that is 61 rows this never loads.

    A caller that ALREADY holds a :class:`~._calendar.PayCalendar` must use
    :attr:`~._calendar.PayCalendar.cadence` instead, which answers from the
    calendar it already has.  Both reach one derivation
    (:class:`~._cadence.PayCadence`), so the two doors cannot disagree; what
    they differ in is how much of the schedule the caller needed anyway.

    **Through :func:`schedule_for` rather than
    ``pay_schedule_service.resolve_cadence``, since plan step C4-d**, and it
    costs the same one query: that function IS ``resolve_schedule`` plus a
    ``.rhythm.cadence``, and ``resolve_schedule`` is the one read.  What changed
    is that the refusal for a row-less owner is written once, here and for
    :func:`calendar_for`, instead of once per door with two messages to keep in
    step.  ``resolve_cadence`` survives for the callers that want the SOFT
    answer -- ``routes/salary/profiles._paychecks_per_year`` renders a pointer
    where this raises, because a form must not 500 on the state it repairs.

    **Resolve it once per PRODUCER and thread it.**  The recurrence plan said
    "once per request" (archived in
    ``docs/plans/historical/recurrence_findings_as_built_2026-08-15.md``), and
    the honest statement is narrower, because two surfaces
    genuinely read it twice: the budget dashboard's tracks section runs both
    narrow savings producers and each resolves its own, and ``/retirement``
    calls ``load_gap_inputs`` from the gap producer and again from the lever
    solver.  Both ride on an already-recorded duplicate load (finding
    **N-115** for the dashboard core data), and collapsing either would mean
    resolving BEFORE those producers' early returns -- which is the defect
    ``_DashboardCoreData``'s docstring records.  The rule that matters is the
    one this door does enforce: never per ROW.  A monthly-equivalent
    conversion runs per recurring template, so a lookup inside that loop would
    be one query per row of a page that already has the answer.

    Args:
        user_id: The owning user.

    Returns:
        The owner's :class:`~._cadence.PayCadence`.

    Raises:
        PayCalendarError: The owner has no ``budget.pay_schedule`` row, which
            since plan step C4-b-2 IMPLIES no pay periods
            (``fk_pay_periods_schedule``).  **Refused from
            :func:`schedule_for` since plan step C4-d** (ruling
            **R-PC45**), which is the same refusal this door has made since
            plan step R7a-2a with the message and the argument moved to the one
            place :func:`calendar_for` shares it from.  Refused rather than
            defaulted for the reason it always gave: every monthly equivalent
            in the application is a function of this number, and assuming
            biweekly would report a weekly-paid owner's commitments at half
            their true value.
            **It is no longer reachable with a cadence OUT OF RANGE**, which is
            what closing ledger row **P35** means: the only source is the
            stored column, bounded to 1..365 by
            ``ck_pay_schedule_cadence_range``, so
            :func:`~._derive.validate_cadence` can no longer refuse what this
            resolves.
    """
    return PayCadence(schedule_for(user_id).rhythm.cadence)
