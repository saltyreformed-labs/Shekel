"""Form payloads and calendar windows: a cadence as a schema or a browser states it, a payroll
basis and its pricing, windows of pay periods, and the dashboard's section context.
"""

from ._users_and_eras import eras_of


# ── Recurrence cadence payloads (plan step R7b-2) ─────────────────
#
# The two AXES a recurrence form authors, in one place.  Before R7b-2 a test
# wrote ``{"recurrence_pattern": str(monthly.id)}`` and every suite carried its
# own spelling of that; the form now states a unit, an interval and a
# placement, and plan step R7c changes the wire shape again when the authored
# columns land.  One producer is one edit then -- and it is also what stops a
# test authoring a cadence the application cannot store, because it states the
# axes and lets the encoder choose the pattern, exactly as the form does.
#
# TWO shapes, because the cadence crosses two boundaries and looks different at
# each.  A BROWSER posts ``ref`` ids spelled as strings; the Marshmallow field
# (``_helpers.RecurrenceUnitField`` / ``PeriodPlacementField``) deserializes
# each to its enum MEMBER, and that is what the route helpers in
# ``app.routes._recurrence_form_helpers`` receive.  A test that drives a route
# through the client wants the first; one that calls a helper directly wants
# the second, and handing it strings would test a payload no schema produces.
# :func:`cadence_payload` is built from :func:`validated_cadence` so the key
# names and the defaults are stated once for both.


def validated_cadence(
    unit=None, interval_n=1, placement=None, starts_on=None, nominal_day=None,
    states_a_start=True, max_per_month=None,
):
    """Return one cadence as a SCHEMA's fields deserialize it.

    The deserialized shape: enum members, a real ``int`` and a real ``date``,
    which the schema's ``@post_load`` consumes into the submission a route
    helper reads, as :func:`~tests.test_routes._recurrence_submission.submission_from`
    does (the route helpers read this dict's keys directly until plan step R7e).

    **``starts_on`` rides WITH the cadence since plan step R7c-b**, and it is
    not optional garnish: the rule's first occurrence is authored (ruling
    R-R16), ``budget.recurrence_rules.starts_on`` is ``NOT NULL``, and
    ``RecurrenceFormFieldsMixin.validate_recurrence_states_a_start`` refuses a
    submission that names a cadence without one.  A helper that produced the
    cadence alone would produce a payload no form can post.

    Args:
        unit: A :class:`~app.enums.RecurrenceUnitEnum` member.  Defaults to
            ``PERIOD``, the every-paycheck cadence most fixtures want.
        interval_n: How many units pass between occurrences.
        placement: A :class:`~app.enums.PeriodPlacementEnum` member.  Defaults
            to ``CONTAINING_DATE``, the placement every unit offers.
        starts_on: The rule's FIRST OCCURRENCE.  Defaults to whatever a create
            form opens on, taken from that form's own producer rather than
            restated here, so a fixture starts where a user would.
        nominal_day: The day the rule MEANS when *starts_on*'s month was too
            short to hold it (ruling R-R3), and ``None`` -- the ordinary case
            -- when the date holds the day.  Omitted from the payload entirely
            when ``None``, because the control that posts it renders only where
            the chosen date leaves the question open.
        states_a_start: ``False`` leaves ``starts_on`` OUT of the payload
            entirely, which is a different request from stating a date and is
            what a locked control produces -- a loan payment's, whose bound the
            app derives, renders ``disabled`` and so posts nothing.  An UPDATE
            reads the absence as "leave the stored one alone"; a CREATE refuses
            it.  Absence and presence being distinguishable is the whole point
            of the ruling of 2026-08-15, so a helper that could only produce
            one of them could not exercise it.
        max_per_month: The per-month ceiling (plan step salary:R15-a), or
            ``None`` to leave the key out -- what a DISABLED control posts.

    Returns:
        A dict of deserialized payload values, ready to splat into the ``data``
        a helper is called with.
    """
    # Imported inside the function rather than at module scope: this helpers
    # module is imported by tests that run before the Flask app, and therefore
    # the ref cache, exists.
    # Pylint: ``import-outside-toplevel`` is the point, not an oversight.
    # pylint: disable=import-outside-toplevel
    from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
    from app.routes._recurrence_form_render import create_form_default_starts_on
    from app.services.recurrence import MonthReading

    payload = {
        "recurrence_unit": (
            unit if unit is not None else RecurrenceUnitEnum.PERIOD
        ),
        "interval_n": interval_n,
        "recurrence_placement": (
            placement if placement is not None
            else PeriodPlacementEnum.CONTAINING_DATE
        ),
    }
    if states_a_start:
        payload["starts_on"] = (
            starts_on if starts_on is not None
            else create_form_default_starts_on()
        )
    if nominal_day is not None:
        payload["repeats_on"] = MonthReading(nominal_day=nominal_day)
    if max_per_month is not None:
        payload["max_per_month"] = max_per_month
    return payload


def end_bound_payload(bound=None):
    """Return the form keys that state one closing bound, as a BROWSER posts them.

    The bound half of :func:`cadence_payload`, for plan step R7b-3's "Ends"
    control.  The control is a mode ``<select>`` and two value inputs of which
    exactly one is ever ENABLED, so a real submission carries the mode plus at
    most one value -- and that is what this produces.  A helper that always
    sent both would exercise a payload the form cannot make.

    Args:
        bound: An :class:`~app.services.recurrence.EndBound`.  Defaults to the
            unbounded shape, which is what a create form starts on.

    Returns:
        A dict of form values -- strings, as an HTML form submits them.
    """
    # Pylint: ``import-outside-toplevel`` -- see :func:`validated_cadence`.
    from app.services.recurrence import (  # pylint: disable=import-outside-toplevel
        NEVER_ENDS,
    )

    stated = NEVER_ENDS if bound is None else bound
    payload = {"recurrence_end_mode": stated.token}
    if stated.end_date is not None:
        payload["end_date"] = stated.end_date.isoformat()
    if stated.max_occurrences is not None:
        payload["max_occurrences"] = str(stated.max_occurrences)
    return payload


def cadence_payload(
    unit=None, interval_n=1, placement=None, starts_on=None, nominal_day=None,
    states_a_start=True, max_per_month=None,
):
    """Return the form keys that author one cadence, as a BROWSER posts them.

    :func:`validated_cadence`'s wire form: each enum member resolved to its
    ``ref`` row id, every value a string.  Derived from that function rather
    than written beside it, so the key names and the defaults have one
    statement and plan step R7c moved both together.

    ``starts_on`` goes out ISO-formatted, which is what ``<input type="date">``
    posts and what ``fields.Date`` reads.

    Args:
        unit: A :class:`~app.enums.RecurrenceUnitEnum` member.  Defaults to
            ``PERIOD``.
        interval_n: How many units pass between occurrences.
        placement: A :class:`~app.enums.PeriodPlacementEnum` member.  Defaults
            to ``CONTAINING_DATE``.
        starts_on: The rule's first occurrence; see :func:`validated_cadence`.
        nominal_day: The clamped day the rule means; see
            :func:`validated_cadence`.  Absent from the returned dict when
            ``None``, matching the control's own conditional rendering.
        states_a_start: ``False`` omits ``starts_on``, which is what a locked
            (``disabled``) control posts; see :func:`validated_cadence`.
        max_per_month: The per-month ceiling (plan step salary:R15-a).  Absent
            from the returned dict when ``None``, which is what the control
            posts DISABLED beside a calendar-month unit; a test modelling an
            enabled, empty box adds ``"max_per_month": ""`` itself.

    Returns:
        A dict of form values -- strings, as an HTML form submits them -- ready
        to splat into a POST payload.
    """
    # Pylint: ``import-outside-toplevel`` -- see :func:`validated_cadence`.
    from app import ref_cache  # pylint: disable=import-outside-toplevel

    loaded = validated_cadence(
        unit, interval_n, placement, starts_on, nominal_day, states_a_start,
        max_per_month,
    )
    payload = {
        "recurrence_unit": str(
            ref_cache.recurrence_unit_id(loaded["recurrence_unit"]),
        ),
        "interval_n": str(loaded["interval_n"]),
        "recurrence_placement": str(
            ref_cache.period_placement_id(loaded["recurrence_placement"]),
        ),
    }
    if "starts_on" in loaded:
        payload["starts_on"] = loaded["starts_on"].isoformat()
    if "repeats_on" in loaded:
        payload["repeats_on"] = loaded["repeats_on"].token
    if "max_per_month" in loaded:
        payload["max_per_month"] = str(loaded["max_per_month"])
    return payload


def payroll_basis(profile, periods, cadence_days=14, history_opens_on=None):
    """Return the :class:`PayrollBasis` for *profile* over *periods*.

    **The ONE test-side door onto the paycheck engine's owner input**, added at
    plan step **R-F16** when ``salary.salary_profiles.pay_periods_per_year``
    was dropped and the count became a derivation off
    ``budget.pay_schedule.cadence_days``.  One shared helper rather than a
    ``for_test`` constructor on the production type or a defaulted ``cadence=``
    argument on the engine, which is the ruling ledger row **P54** set for the
    same question about ``BalanceContext``: a production API with only test
    callers is the speculative shape ``CLAUDE.md`` rule 13 forbids, and a
    defaulted cadence would let the engine assume biweekly for an owner who is
    not.

    The default matches the suite's fixtures and
    ``BaseConfig.DEFAULT_PAY_CADENCE_DAYS``, so a test that does not care about
    the rhythm says nothing and gets 26 paychecks a year.  A test that DOES
    care states its cadence and gets the count that follows -- 7 gives 52, 15
    gives 24, 365 gives 1.

    **It takes the owner's PERIODS since plan step balance:X-bh-1**, because
    the basis now carries the whole
    :class:`~app.services.pay_calendar.PayCalendar` rather than a bare cadence:
    the engine's four calendar judgements -- third-paycheck detection, the
    first-paycheck-of-month deduction cadence, the FICA wage-base cumulative
    and a deduction's annual cap -- count paydays off it, where they used to
    read an ``all_periods`` argument a caller supplied beside the basis.  A
    case passes here exactly what it used to pass there.

    **It is REQUIRED and has no default, which an adversarial review of this
    step is why.**  A defaulted empty *periods* builds a paydayless calendar
    that still answers the paycheck COUNT from *cadence_days*, so a case
    reading only a gross goes on passing -- while every month and year count
    over it is ZERO, which INVERTS two arms: a 12-per-year deduction stops
    applying where the old empty ``all_periods`` list made it apply
    vacuously, and a 24-per-year one starts applying on a third paycheck.
    Five oracles were measured still taking the default, agreeing with their
    producers only because no fixture behind them carries a deduction or
    reaches the wage base.  An argument a caller can silently omit is the
    same defect this step removed from the engine, one layer up.

    Args:
        profile: The ``SalaryProfile`` (or a duck-typed stand-in) to price.
            Its ``user_id`` owns the calendar built here, because a rule is
            refused against another owner's schedule
            (``_require_owner`` in ``app/services/recurrence/_resolution.py``),
            so a stand-in carries one too.
        periods: The owner's WHOLE schedule as
            :class:`~app.services.pay_calendar.DerivedPeriod` values, in any
            order.  Only their paydays are read; the calendar re-derives every
            end and ordinal, so a case may hand over the same values it prices.
        cadence_days: Days between the owner's paydays, 1..365.
        history_opens_on: How far back this owner's paychecks reach, or
            ``None`` -- **the DEFAULT, and it is the column's own** (plan step
            **balance:X-bh-2**).  ``budget.pay_schedule.history_opens_on`` is
            nullable and null means NOT STATED since ruling
            **balance:R-IA**'s 2026-08-31 amendment, so a case that says
            nothing here gets what an owner nobody asked gets: the engine
            counts *periods* and projects nothing below them.  **That makes
            the default the SAFE one** -- forgetting it cannot silently invent
            paychecks, which is the direction a defaulted argument should fail
            in.  A case about the BACKWARD rhythm states a day here, and a
            case about an owner who genuinely started at the record's opening
            states that opening.

    Returns:
        The :class:`~app.services.payroll_basis.PayrollBasis`.

    Raises:
        PayCalendarError: *cadence_days* falls outside 1..365, which is what
            ``ck_pay_schedule_cadence_range`` refuses in the database, or two
            of *periods* share a payday.
    """
    from app.services.payroll_basis import (  # pylint: disable=import-outside-toplevel
        PayrollBasis,
    )

    return PayrollBasis(profile, derived_calendar(
        [period.start_date for period in periods], cadence_days=cadence_days,
        user_id=profile.user_id, history_opens_on=history_opens_on,
    ))


def pricing_over(calendar):
    """Return a :class:`~app.services.income_service.PaycheckPricing` over *calendar*.

    **The test-side door for a calendar already in hand**, and ONE copy of it
    (ruling P54's shape: a production API with only test callers is the
    speculative shape ``CLAUDE.md`` rule 13 forbids, so the production
    constructor that took a calendar was moved here at plan step salary:C12,
    when its last production caller went).  Production holds two doors and
    both take a SOURCE -- the read pass hands its calendar memo, a pass-less
    producer hands ``calendar_for`` bound to the owner -- so a test that has
    derived a calendar for itself hands it back as the source, the owner read
    off the same value.  Every property the pricer has in production holds
    here: nothing is derived at construction, and the source answers once.

    Args:
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.

    Returns:
        The empty pricer.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app or ORM
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.income_service import paycheck_pricing

    return paycheck_pricing(calendar.user_id, lambda: calendar)


def derived_calendar(
    paydays, cadence_days=14, user_id=1, history_opens_on=None,
):
    """Return a :class:`PayCalendar` over *paydays*, derived rather than built.

    The calendar-shaped sibling of :func:`derived_window`, for a producer that
    takes the whole calendar rather than a slice of it -- which
    :meth:`app.services.income_service.SalaryPricing._breakdown_by_period` does,
    because the paycheck engine needs the owner's paycheck COUNT as well as
    their periods and both must come off one derivation (plan step **R-F16**).

    *This sentence named ``recurrence_engine._amounts`` until plan step
    balance:X-au-d, and a mechanical rename left it naming
    ``_generated_amount_ownership``, which takes a template and reads no
    calendar at all.  The requirement did not go away when generation stopped
    pricing -- it MOVED, to the derivation above.  Caught by an adversarial
    review of that step.*

    Args:
        paydays: The paydays opening each period, in any order.
        cadence_days: Days between paydays, 1..365.
        user_id: The owner the calendar belongs to.
        history_opens_on: How far back the owner's paychecks reach, or ``None``
            -- the default, which is the nullable column's own; see
            :func:`payroll_basis` for what saying nothing means.

    Returns:
        The :class:`~app.services.pay_calendar.PayCalendar`.
    """
    from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
        PayCalendar,
    )

    pairs = [(index + 1, payday) for index, payday in enumerate(sorted(paydays))]
    return PayCalendar.from_paydays(
        pairs,
        eras_of(pairs, cadence_days),
        user_id=user_id,
        history_opens_on=history_opens_on,
    )


def derived_window(paydays, cadence_days):
    """Return a :class:`PeriodWindow` over *paydays*, derived rather than built.

    The PURE test-side door onto the pay calendar, added at plan step **C2-e**,
    when ``growth_engine`` stopped accepting a hand-built list of period-shaped
    objects.  :func:`period_window` above is its database-backed sibling: use
    that one when the test has real ``budget.pay_periods`` rows, and this one
    for the unit tests that have no database at all.

    **It derives; it does not assemble.**  The window comes out of a real
    :class:`~app.services.pay_calendar.PayCalendar`, so its ends are the ones
    the derivation computes (each period ends the day before the next payday,
    and the last the day before the next payday of a grid phased on the
    FIRST payday at *cadence_days* -- :func:`eras_of`), its ordinals run in
    payday order, and its periods TILE.  A test therefore cannot hand the
    growth engine a shape production could not produce -- a gap between two
    periods, an ordinal out of date order, an end below its own start -- which
    is the whole reason the engine's parameter is a window rather than a list.

    Args:
        paydays: The paydays opening each period, in any order.  A test that
            wants a plain biweekly run passes ``[d, d + 14, d + 28, ...]``.
        cadence_days: Days between paydays, 1..365.  It sets the LAST period's
            end and nothing else, so a run of evenly spaced paydays should
            pass its own spacing; off it, the last end lands on the grid
            rather than one cadence past the last payday.

    Returns:
        The :class:`~app.services.pay_calendar.PeriodWindow` over every derived
        period, ``start_date`` ascending.

    Raises:
        PayCalendarError: Anything the derivation refuses -- a duplicate
            payday, a cadence outside 1..365, a payday that is not a plain
            ``date``.
    """
    from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
        PayCalendar,
        PeriodWindow,
    )

    pairs = [
        (index + 1, payday) for index, payday in enumerate(sorted(paydays))
    ]
    calendar = PayCalendar.from_paydays(
        pairs,
        eras_of(pairs, cadence_days),
        user_id=1,
        history_opens_on=None,
    )
    return PeriodWindow(periods=calendar.periods)


def biweekly_window(first_payday, count):
    """Return a :class:`PeriodWindow` of *count* 14-day periods from *first_payday*.

    The shorthand for the commonest shape in the unit tests -- an evenly
    spaced biweekly run -- over :func:`derived_window`, which it defers every
    guarantee to.

    Args:
        first_payday: The payday opening the first period.
        count: How many periods to derive.

    Returns:
        The :class:`~app.services.pay_calendar.PeriodWindow`.
    """
    from datetime import timedelta  # pylint: disable=import-outside-toplevel

    return derived_window(
        [first_payday + timedelta(days=14 * step) for step in range(count)],
        14,
    )


def window_head(window, count):
    """Return the first *count* periods of *window* as a window of their own.

    The test-side stand-in for slicing, which
    :meth:`~app.services.pay_calendar.PeriodWindow.__getitem__` REFUSES (plan
    step C2-e): no consumer in ``app/`` slices a window, and the branch that
    once allowed it returned ``window[::-1]`` silently re-sorted into payday
    order rather than reversed.  A leading run of a tiling tiles, so this
    cannot build a window the type would refuse -- the constructor still
    checks.

    Args:
        window: The :class:`~app.services.pay_calendar.PeriodWindow` to take
            from.
        count: How many periods to keep, from the window's own start.

    Returns:
        The leading :class:`~app.services.pay_calendar.PeriodWindow`.
    """
    from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
        PeriodWindow,
    )

    return PeriodWindow(periods=window.periods[:count])


def period_window(periods):
    """Return the :class:`PeriodWindow` the seam reports over for *periods*.

    The test-side door onto the pay calendar, added at plan step **C2-c**,
    when the balance seam stopped taking a list of ORM ``PayPeriod`` rows and
    started reading its reporting domain off the pay calendar
    (``app.services.balance_at.BalanceContext.reported_periods``).  A handful
    of seam producers still take a window explicitly -- the ones that take a
    ``scenario_id`` and an ``as_of`` rather than a context -- and this is how a
    test names the SUBSET of an owner's schedule it wants those to report.

    It derives the owner's whole calendar and then selects the requested
    periods out of it, rather than building period bounds from the ORM rows:
    a window carries the ends the WHOLE calendar computed, which is the
    property ledger row **P14** is about, and taking the ends off the stored
    columns here would let a test pass against bounds production no longer
    reads.

    Args:
        periods: The ``PayPeriod`` rows to report, in any order and all
            belonging to one user.  Must be non-empty -- an empty request has
            no owner to resolve a calendar for, and the seam entries take
            ``PeriodWindow(periods=())`` directly for that case.

    Returns:
        The :class:`~app.services.pay_calendar.PeriodWindow` over exactly those
        periods.

    Raises:
        PayCalendarError: The requested periods do not form an unbroken span,
            which the window type refuses (plan finding **P32**).  That is a
            real answer, not a helper limitation: a gapped column set renders
            a balance row that does not add up.
    """
    from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
        PeriodWindow,
        calendar_for,
    )

    wanted = {period.id for period in periods}
    calendar = calendar_for(next(iter(periods)).user_id)
    return PeriodWindow(
        periods=tuple(
            period for period in calendar.saved()
            if period.period_id in wanted
        ),
    )


def dashboard_section(user_id, as_of=None):
    """Return what ``dashboard.page`` resolves before it calls a producer.

    The test-side door onto the budget dashboard's producers, added at
    pay-calendar plan step **C2-f2e**, when
    ``dashboard_service.compute_pulse_section`` and
    ``compute_balance_section`` stopped taking a ``user_id`` and started taking
    a :class:`~app.services.dashboard_service.DashboardSection` -- the account,
    the settings and the read pass, resolved ONCE by the route.

    It performs the route's two steps and nothing else, deliberately: a helper
    that CONSTRUCTED a section from parts would let a test exercise a
    combination the route cannot produce (an account of one owner beside
    another's pass), which is exactly the "test infrastructure that bypasses
    the production door" shape ``docs/plans/lessons.md`` records.

    Args:
        user_id: The owner whose dashboard to resolve.
        as_of: The read pass's pinned day.  Defaults to the pass's own default
            (``date.today()``); supply one to pin a render to a day the test
            controls, which is the same knob every other calendar-sensitive
            fixture uses.

    Returns:
        The :class:`~app.services.dashboard_service.DashboardSection`, or
        ``None`` when the owner has no resolvable grid account -- the same
        ``None`` the route passes straight through to the producers.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dependency
    # avoidance as the factories in ``_accounts.py`` and ``_loans.py``.
    from app.services import dashboard_service
    from app.services.balance_at import BalanceContext

    return dashboard_service.resolve_section(
        BalanceContext.build(user_id, as_of=as_of),
    )
