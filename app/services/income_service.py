"""
Shekel Budget App -- Income Service (F-20 / MED-06 / F-032).

Single source of truth for what a salary profile PAYS: the paycheck engine's
own :class:`~app.services.paycheck_calculator.PaycheckBreakdown`, per period,
never an off-engine recompute: the ``Decimal(str(profile.annual_salary)) / <a
paycheck count>`` one silently dropped any applicable
:class:`~app.models.salary_raise.SalaryRaise` row pre-Commit-17, and the
column it read is gone since plan step salary:X-av-3a.
:class:`ProfilePaychecks` is the ONE spelling of a profile's projection (ledger
row **N-443**, plan step **salary:R14-a**) and :class:`SalaryPricing` is what
prices a generated row from it.

**It became a FUNCTION of the payday rather than a LIST at plan step
salary:S3-d**, and that is what deletes the horizon question rather than
answering it.  ``project_profile(profile, calendar)`` returned a list over the
owner's SAVED window, so past the last saved payday there was no paycheck and
:class:`~app.services.investment_projection.AccountPayrollFeed` INVENTED one
by holding a figure -- six rules over that fold, each measured wrong
(**N-541**, ruling **R-SAL10**).

**The hold is GONE since plan step salary:S3-e-2** (ruling **R-SAL15**).
S3-d built the producer that can price a payday past the horizon and moved
every consumer onto it while every one of them still asked for
``calendar.saved()``; S3-e-2 is where the ask widened.  The feed holds two
resolvers over :class:`ProfilePaychecks` now and prices whichever period a
consumer hands it, so nothing states a horizon and nothing holds.
``TestThePricerAnswersPastTheSavedHORIZON`` is what that deletion rested on,
and it is kept: it is the one place the capability is graded on its own.

Widening the list needed somebody to decide HOW LONG, and
one render asks two lengths of it: the balance seam wants the saved window, the
40-year chart wants forty years, and they share one memo.  Nothing decides,
because a paycheck does not depend on any other paycheck --
:func:`~app.services.paycheck_calculator.calculate_paycheck` rebuilds its own
year's month positions and both cumulatives off ``basis.calendar``, which runs
forward past the horizon at the owner's cadence -- so the series is a MAP from
payday to paycheck, defined wherever the rhythm reaches, and a caller asks for
the paydays it actually reads.

**One pricer per profile PER RAISE SET per read pass**, :class:`PaycheckPricing`,
reached through :meth:`~app.services.balance_at.BalanceContext.paychecks` --
per profile alone until plan step salary:S3-f-1, when the raise set became an
input of the engine's basis.  It replaced
the raw ``BalanceContext.payroll_breakdowns`` dict and the ``breakdowns=None``
argument on :func:`~app.services.projection_inputs.load_payroll_feeds` that
meant "no memo at all" -- the hole
``TestOnePaycheckProjectionPerProfilePerRender`` was written to watch, and which
that test's own docstring records two of four callers falling through.  A
consumer is handed the pricer now, so there is nothing left to forget to pass.

**ONE source of a paycheck per read pass since plan step salary:C12.**  Two
survived S3-d -- the pass held one and :class:`SalaryPricing` derived a
second, because the amount basis was built from two ids and had nothing to
hand it (ledger row **P63**) -- and C12 closed it by making the basis TAKE
the pass's pricer.  What it cost, and the interim a pass-less producer still
uses, is stated once at :class:`PaycheckPricing`; the runtime gate
``TestOnePaycheckProjectionPerProfilePerRender`` keeps its subject and counts
:class:`ProfilePaychecks` constructions so a second source cannot return.

**A SCALAR producer stood beside them until plan step salary:R14-b, and it is
DELETED rather than re-pointed.**  ``get_current_gross_biweekly(user_id,
calendar)`` answered "the current per-period gross" for the savings,
retirement and investment feeds, and the shape was the defect: one figure,
read at ONE moment, standing in for a series that every raise moves.  It
carried two more that fell out of that shape and that ``recurrence:R-F16``'s
adversarial review had to REVERT a fix over -- it chose the owner's profile
with an unordered ``.first()`` (a measured **39%** swing on a two-job owner,
flipping between renders with no data change) and it answered ``$0.00``
whenever no pay period covered its clock, which silently deleted a whole
contribution plan at onboarding and after a horizon lapse.

Ruling **R-SAL2** answers all three at once and none of the answers is a
scalar: the PERIOD is the clock, the gross is that period's own off the
engine's breakdown, and the profile is NAMED (by the deduction, or by
``budget.investment_params.salary_profile_id`` -- **R-SAL5**) rather than
searched.  Its consumers read
:class:`~app.services.investment_projection.AccountPayrollFeed` now, which
:func:`app.services.projection_inputs.load_payroll_feeds` folds out of
:class:`ProfilePaychecks`.  This module's own docstring listed those consumers
for as long as it had them; the list is gone with the function.

Boundary discipline (``CLAUDE.md``: "services are isolated from Flask"):
this module imports no Flask symbol.  All inputs are plain data (a user id, a
scenario id, an ORM profile, a calendar or a callable that answers one).
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from functools import partial

from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.services import paycheck_calculator
from app.services.payroll_basis import PayrollBasis
from app.services.pay_calendar import PayCalendar, calendar_for
from app.services.salary_raises import terms_of
from app.services.tax_config_service import configs_by_year, profile_tax_series


class ProfilePaychecks:
    """What ONE salary profile pays, asked a PAYDAY at a time and remembered.

    **The ONE spelling of a profile's projection, and closing that it was
    THREE is ledger row N-443** (plan step **salary:R14-a**).  The pair it
    replaced --
    ``tax_config_service.load_tax_configs_for_periods`` over the calendar's
    saved window (the door plan step salary:S3-d replaced with
    :func:`~app.services.tax_config_service.configs_by_year`, which takes
    YEARS rather than a window), then
    :func:`paycheck_calculator.project_salary` on a
    :class:`~app.services.payroll_basis.PayrollBasis` with the profile's
    calibration -- was written out longhand in three places over the same
    calendar: :class:`SalaryPricing` below, ``routes/salary/views.py`` and
    ``routes/salary/cockpit.py``.

    **The three could not simply call each other, and that is why moving the
    leaf was the remedy rather than picking one.**  The two route sites keep
    the whole :class:`~app.services.paycheck_calculator.PaycheckBreakdown` --
    they render the anatomy, the chart and the ledger table -- while the
    derivation kept only ``net_pay``, so the shared leaf had to be the
    BREAKDOWN and no existing caller produced one.  Ruling **R-IZ** names
    exactly this shape: a second walk is a cache with no column, agreement is
    not the test, and where a layer puts the shared leaf out of reach the
    remedy is to MOVE THE LEAF.

    **It reads the OWNER off the profile** rather than taking a user id,
    because the two must agree and a caller holding ``current_user.id`` beside
    a profile is a second chance to get it wrong.

    **The tax series is sliced ONCE, here, and every year after is arithmetic**
    (:func:`~app.services.tax_config_service.profile_tax_series` slices the
    law, :mod:`app.tax_law`, to the profile's filing status and state;
    :func:`~app.services.tax_config_service.configs_by_year` is the pure pick).
    It was three queries of a per-user copy of the law until plan step
    salary:X-at-1, which is why it was built here once rather than per payday;
    it issues no query now, and :meth:`at` still answers a payday nobody knew
    would be asked for without slicing the law again.

    **This value issues no query of its own since plan step salary:X-at-1
    (the tax series was all of it, at construction); the PROFILE's reads need
    not happen at construction either, and an adversarial review of plan step
    salary:S3-d corrected a sentence here that claimed otherwise.**
    ``SalaryProfile.raises``, ``.lines`` and ``.pay_entries`` are
    ``lazy="select"``.  The deductions and the pay entries are read by
    :func:`~app.services.paycheck_calculator.calculate_paycheck` on the FIRST
    paycheck priced; the raises are read one call earlier since plan step
    salary:S3-f-1, by :meth:`PaycheckPricing.for_profile` canonicalising the
    raise set for its memo key, and the engine reads only that value.  Whether
    any of those reads issues a SELECT depends on how the caller loaded the
    profile, not on this class.
    :func:`~app.services.projection_inputs.load_payroll_feeds` eager-loads
    them (``subqueryload`` on all three); :meth:`SalaryPricing._profile_by_template`
    does not, and that is a pre-existing property of the amount model's
    profile lookup rather than something this step introduced.  It is
    reported, not fixed here: adding eager options to that query changes what
    the amount model reads on every pass that prices a row, which is
    ``balance:X-i1``'s tier and not a producer's to decide.

    **Past the saved schedule it keeps answering, and that is the point of
    plan step salary:S3-d.**  The engine's four calendar judgements -- the
    month position both deduction cadences read, the FICA wage-base cumulative
    and a deduction's annual-cap cumulative -- all come off ``basis.calendar``
    since plan step **balance:X-bh-1**, and that rhythm projects forward at the
    owner's cadence to :data:`~app.utils.dates.CALENDAR_DATE_MAX`
    (:mod:`app.services.pay_calendar._rhythm`).  So a projected payday is
    priced by the same code and the same rules as a saved one; what it lacks is
    a ``budget.pay_periods`` row, which is exactly what
    :attr:`~app.services.paycheck_calculator.PeriodInfo.period_id` being
    ``None`` says.
    """

    def __init__(
        self, profile, calendar: PayCalendar, raise_terms=None,
    ) -> None:
        """Pin the profile to its owner's calendar and slice the tax law for it.

        Args:
            profile: The :class:`~app.models.salary_profile.SalaryProfile` to
                price.  Read for its owner and everything the engine prices
                a paycheck from -- its pay stubs among them since plan step
                salary:S11-c-2c, which replaced its calibration.
            calendar: The owner's
                :class:`~app.services.pay_calendar.PayCalendar` -- the payday
                set every calendar question the engine asks is answered from,
                and the cadence it divides the salary by.  Must belong to the
                SAME owner as *profile* (see the raise below).
            raise_terms: The raise set to price under, as a tuple of
                :class:`~app.services.salary_raises.RaiseTerms`, or ``None``
                for the rows' own terms -- handed straight to
                :attr:`~app.services.payroll_basis.PayrollBasis.raise_terms`
                (plan step salary:S3-f-1, ruling **R-SAL20**).  A pricer is
                one profile under ONE raise set: the per-payday memo below is
                keyed by payday alone, so a set that differs is a different
                pricer, which :meth:`PaycheckPricing.for_profile` keys on.

        Raises:
            UnsupportedStateError: The tax law does not list *profile*'s state
                (:func:`~app.services.tax_config_service.profile_tax_series`,
                plan step salary:X-at-3): the profile is refused rather than
                priced at $0.00 state tax.
            ValueError: *profile* and *calendar* belong to different owners.
                **The mispairing is SILENT without this**, which is why it is
                refused here rather than described: the profile would be
                priced on one owner's pay while every payday came from the
                other's schedule, and the engine's own cross-owner guard
                cannot fire on it -- :func:`~app.services.paycheck_calculator
                ._month_ordinal` refuses a payday the calendar cannot place,
                and the calendar places its OWN paydays perfectly well.  The
                result is a plausible wrong paycheck.
                :attr:`~app.services.pay_calendar.PayCalendar.user_id` exists
                for exactly this test and says so in its own docstring; plan
                step **salary:S3-d** is the first moment one value holds both
                halves, which is the cheapest this check will ever be.
        """
        if profile.user_id != calendar.user_id:
            raise ValueError(
                f"salary profile {getattr(profile, 'id', '?')} belongs to user "
                f"{profile.user_id} and the pay calendar to user "
                f"{calendar.user_id}: a paycheck is priced against the "
                f"calendar its own owner is paid on, and pairing the two "
                f"answers a plausible figure off the wrong schedule."
            )
        self._basis = PayrollBasis(profile, calendar, raise_terms)
        self._series = profile_tax_series(profile)
        self._by_payday: "dict[date, paycheck_calculator.PaycheckBreakdown]" = {}

    def over(
        self, periods,
    ) -> list[paycheck_calculator.PaycheckBreakdown]:
        """Return this profile's paycheck for each of *periods*, in that order.

        Prices only the paydays it has not priced already, so two callers
        asking overlapping spans in one pass pay for the union and never for
        the overlap.  That the subset is priced identically to the whole is
        :func:`paycheck_calculator.project_salary`'s own stated contract since
        plan step **balance:X-bh-1**: "this list may be any subset in any order
        and every breakdown is the same as it would be alone", because the
        engine's month and year context comes off ``basis.calendar`` rather
        than off the list it is handed.

        **The priced breakdowns are filed under their OWN payday**, read off
        :attr:`~app.services.paycheck_calculator.PeriodInfo.payday`, rather
        than zipped against *periods* by position.  That is WEAKER than it
        first reads and saying so is the point: it replaces a pairing by
        POSITION -- which would truncate silently if the two sequences ever
        differed in length -- with a pairing by KEY, so what survives is the
        narrower requirement that ``DerivedPeriod.start_date`` and
        ``PeriodInfo.payday`` are the same day.  The engine copies one to the
        other in one statement, so that is a key rather than the two-producer
        contract ``CLAUDE.md`` rule 14 names; it is not nothing.

        **The memo is keyed by PAYDAY alone, so it cannot tell a saved period
        from a projected one that opens on the same day.**  Unreachable: a
        calendar built by :func:`~app.services.pay_calendar.calendar_for`
        reads saved rows only, and every projected payday it yields falls
        strictly after the last saved one, so the two sets are disjoint by
        construction.  It is stated because this method's own signature
        invites "saved, projected, or both" and a caller assembling such a
        list by hand is the one input that could reach it.

        Args:
            periods: The :class:`~app.services.pay_calendar.DerivedPeriod`
                values to price -- saved, projected, or both, in any order.
                Only ``start_date`` and the fields the engine reads are used.
                Materialised on entry, so a generator serves: this walks it
                twice, and the second walk of a spent iterator would answer an
                empty list rather than raising.

        Returns:
            ``list[PaycheckBreakdown]``, one per element of *periods* and in
            its order, so a caller may zip the two.
        """
        periods = tuple(periods)
        # DEDUPED BY PAYDAY, not merely filtered, and the key matters: the
        # memo makes a REPEAT ask free, but two periods sharing a payday
        # INSIDE one call would both miss it and both be priced -- so "a
        # payday is priced once" would hold on the memo-hit path and not on
        # the first one.  Deduping the PERIODS would not do it, because two
        # ``DerivedPeriod`` values can differ (in ``period_id``, say) while
        # naming the same day, and the day is what this memo is keyed by.
        # Insertion order is preserved, so the engine is handed the paydays in
        # the order the caller first named them.
        wanted: "dict[date, object]" = {}
        for period in periods:
            if period.start_date not in self._by_payday:
                wanted.setdefault(period.start_date, period)
        unpriced = list(wanted.values())
        if unpriced:
            self._by_payday.update(
                (breakdown.period.payday, breakdown)
                for breakdown in paycheck_calculator.project_salary(
                    self._basis, unpriced,
                    # Tax configs resolve PER period year (DH-#30), the same
                    # per-year resolution the recurrence engine uses to
                    # GENERATE the stored amount, so the live figure and the
                    # generated one cannot disagree for want of a bracket set
                    # -- and per pay stub year, each stub priced on its own
                    # payday's law (ruling R-SAL77, plan step salary:S11-c-2c).
                    configs_by_year=configs_by_year(
                        self._series,
                        paycheck_calculator.tax_years_for(self._basis, unpriced),
                    ),
                )
            )
        return [self._by_payday[period.start_date] for period in periods]

    def at(self, period) -> paycheck_calculator.PaycheckBreakdown:
        """Return this profile's paycheck for ONE period.

        Args:
            period: The :class:`~app.services.pay_calendar.DerivedPeriod` to
                price.

        Returns:
            That payday's :class:`~app.services.paycheck_calculator
            .PaycheckBreakdown`.
        """
        return self.over([period])[0]


class PaycheckPricing:
    """The read pass's ONE :class:`ProfilePaychecks` per profile per raise set.

    **Plan step salary:S3-d.**  Within ONE of these, a payday is priced once
    by construction: :meth:`for_profile` memoizes per profile and raise set
    (per profile alone until plan step salary:S3-f-1) and
    :class:`ProfilePaychecks` memoizes per payday, so two consumers asking
    overlapping spans pay for the union and never for the overlap.  A consumer
    is HANDED one -- there is no ``breakdowns=None`` to forget, which is what
    let two of four callers bypass the dict this replaced.

    **A read pass holds ONE of these and every producer under it reads that
    one, the amount model included** (plan step **salary:C12**, closing ledger
    row **P63**).  Until C12, :class:`SalaryPricing` built a SECOND one,
    because ``cash_ledger.amount_basis`` took an owner and a scenario and had
    nothing to hand it -- so a render that read a salary ROW and a payroll
    FEED derived the calendar twice and priced the overlapping paydays twice
    (measured on the developer's data 2026-09-12: 2 calendars, 2 pricers, 115
    engine runs for one grid-shaped read).  The basis takes the pass's pricer
    now (:meth:`~app.services.balance_at.BalanceContext.amounts` hands
    :meth:`~app.services.balance_at.BalanceContext.paychecks`), and
    ``TestOnePaycheckProjectionPerProfilePerRender`` counts
    :class:`ProfilePaychecks` constructions to keep a second from reappearing.

    **It takes the calendar as a SOURCE and derives it once, on the first
    read.**  Two production doors hand one in and neither wants it derived
    early: the read pass hands its own memo
    (:meth:`~app.services.balance_at.BalanceContext.calendar`, through
    :func:`paycheck_pricing`), so building the pass's basis derives nothing
    and a pass that folds no cash and prices no paycheck -- the loan arm, the
    bank-agreement fragment -- pays for no calendar and cannot be refused
    one; a producer holding NO pass hands
    :func:`~app.services.pay_calendar.calendar_for` bound to its owner
    (:func:`derived_paycheck_pricing`), which is the laziness
    :class:`SalaryPricing` had when it derived its own.  (A test holding a
    calendar already hands ``lambda: calendar`` through
    ``tests/_test_helpers.pricing_over``.)  The owner is pinned BESIDE the
    source, because the amount model scopes its profile lookup by owner
    before any paycheck is priced and must not derive a calendar to learn it;
    the two are one value at both doors, and the first derivation refuses a
    source that answers another owner's.

    **No site prices a single period outside a pricer since plan step
    salary:C12-b**: six once called
    :func:`~app.services.paycheck_calculator.calculate_paycheck` directly,
    each resolving its own tax configs, and the two that dropped the
    profile's calibration that way -- ``/retirement`` (S3-f-2a) and
    ``/savings`` (C12-b) -- each published a different paycheck from every
    other page until they were folded in.  The census that enumerated them,
    in exactly one place -- ``tests/test_arch/
    test_the_calendar_wide_projection_has_one_spelling.py`` -- is kept as an
    EMPTY-MAP guard, and says there what it can and cannot see.
    """

    def __init__(
        self, user_id: int, calendar_source: Callable[[], PayCalendar],
    ) -> None:
        """Pin the owner and where their calendar comes from; derive nothing.

        Reached through the named constructors below, never directly, so the
        owner and the source are always one value's two halves.

        Args:
            user_id: The owner every profile in this pricer is priced for.
            calendar_source: A zero-argument callable answering that owner's
                :class:`~app.services.pay_calendar.PayCalendar`, called on
                reads of :attr:`calendar` until it answers once and released
                then, so it cannot be called again.
        """
        self._user_id = user_id
        self._calendar_source: "Callable[[], PayCalendar] | None" = calendar_source
        self._calendar: "PayCalendar | None" = None
        # Keyed by ``(profile id, canonical raise terms)``: a profile's
        # paychecks are a function of the profile, the calendar AND the raise
        # set they are priced under (plan step salary:S3-f-1), and every
        # spelling of one set -- the rows, ``None``, values -- is ONE key.
        self._by_profile: "dict[tuple, ProfilePaychecks]" = {}

    @property
    def calendar(self) -> PayCalendar:
        """The pass's pay calendar, so a consumer holding this needs no second.

        Published because :func:`~app.services.projection_inputs
        .load_payroll_feeds` needs the OWNER as well as the prices -- it
        needed the payday set too until plan step salary:S3-e-2 stopped it
        pricing anything up front -- and taking the two separately is how
        one owner's calendar comes to be paired with another's paychecks.

        **Derived ONCE, on the first read that succeeds, from the source the
        constructor pinned** (plan step salary:C12) -- so a pricer that is
        built and never asked for a paycheck costs no calendar, whichever
        source it holds.  The source is released the moment it answers, which
        makes "once" structural rather than prose and cuts the reference
        cycle a pass's bound method otherwise closes; a source that RAISED
        is kept and asked again on the next read, exactly as the pass's own
        calendar memo behaves.

        Returns:
            The :class:`~app.services.pay_calendar.PayCalendar` this pricer
            answers against.

        Raises:
            ValueError: The source answered another owner's calendar.  The
                owner is pinned beside the source so the amount model can
                scope a profile lookup without deriving; this is where the
                pair is checked, once, at the only moment both halves exist.
            PayCalendarError: From the source, on a read before it has
                answered -- :func:`~app.services.pay_calendar.calendar_for`'s
                refusals for the pass-less door, the pass's own for the pass.
        """
        if self._calendar is None:
            calendar = self._calendar_source()
            if calendar.user_id != self._user_id:
                raise ValueError(
                    f"paycheck pricer for user {self._user_id} was handed a "
                    f"calendar belonging to user {calendar.user_id}: the "
                    f"owner a pricer prices for and the calendar it prices "
                    f"against must be one owner."
                )
            self._calendar = calendar
            self._calendar_source = None
        return self._calendar

    @property
    def user_id(self) -> int:
        """The owner this pricer prices for, answered without deriving.

        **The amount model's owner since plan step salary:C12**:
        :class:`SalaryPricing` scopes its profile lookup by this rather than
        by a ``user_id`` handed in beside the pricer, so an (owner, pricer)
        mismatch is unrepresentable rather than unchecked -- the same reason
        :class:`ProfilePaychecks` reads the owner off the profile.  It is the
        pinned id and not ``self.calendar.user_id``, which is what keeps that
        lookup a single indexed query for a row on a template no profile
        names: the calendar is derived only when a paycheck is priced.

        Returns:
            The owner's id.
        """
        return self._user_id

    def for_profile(self, profile, raise_terms=None) -> ProfilePaychecks:
        """Return this pass's :class:`ProfilePaychecks` for *profile*.

        **Keyed on the raise set as well as the profile since plan step
        salary:S3-f-1** (ruling **R-SAL20**), because that is what a
        profile's paychecks are a function of.  The ``/retirement`` picture
        asks for the set its plan point believes on every render since plan
        step salary:S3-f-2b -- the rows' own terms at the stored plan, which
        is this memo's hit, and a probed set's on the rail's what-if, which
        gets a pricer of its own memoized under them, so ten probes at one
        raise set share one pricer and never a payday priced under another
        set; the salary pages and the balance seam still ask for the rows.

        **The key is CANONICAL, and it has to be.**  Whatever a caller spells
        the set with -- ``None``, the rows, a tuple of
        :class:`~app.services.salary_raises.RaiseTerms`, a probe carrying the
        stored end year unchanged -- it is converted through
        :func:`~app.services.salary_raises.terms_of` first, so two sets that
        would price every payday identically are one key and one pricer.  An
        adversarial review of this step measured that keying on the caller's
        objects admitted three spellings of the STORED set, which is the
        two-derivations-of-one-figure shape
        :class:`~app.services.retirement_plan.PlanPoint` exists to refuse
        (ledger row **P57**) one tier up.  The cost is that the rows are read
        HERE rather than at the first paycheck: for a profile loaded without
        its ``raises`` that is the same SELECT a call earlier, in the same
        request.

        Args:
            profile: The :class:`~app.models.salary_profile.SalaryProfile`.
                Its ``id`` is half the memo key and its ``user_id`` must be
                the owner the calendar belongs to.
            raise_terms: The raise set to price under -- anything
                :func:`~app.services.salary_raises.terms_of` accepts -- or
                ``None`` for the profile's rows.

        Returns:
            The profile's pricer under that raise set -- the same object every
            time within one pass, so nothing it has priced is priced again.

        Raises:
            UnsupportedStateError: The tax law does not list *profile*'s state
                (:class:`ProfilePaychecks`; plan step salary:X-at-3).
        """
        terms = terms_of(profile.raises if raise_terms is None else raise_terms)
        key = (profile.id, terms)
        if key not in self._by_profile:
            self._by_profile[key] = ProfilePaychecks(
                profile, self.calendar, terms,
            )
        return self._by_profile[key]


def paycheck_pricing(
    user_id: int, calendar_source: Callable[[], PayCalendar],
) -> PaycheckPricing:
    """Return a pricer that derives *user_id*'s calendar from *calendar_source*.

    The named constructor, so no consumer reaches for the class directly,
    and the read pass's door (plan step salary:C12):
    :meth:`~app.services.balance_at.BalanceContext.paychecks` hands its own
    calendar memo, so the pass's pricer -- and the amount basis built over it
    -- derives nothing until a paycheck is priced, and a pass that folds no
    cash never derives a calendar it never reads.  The pass-less door below
    is this with :func:`~app.services.pay_calendar.calendar_for` as the
    source.  **It took a calendar in HAND until the second adversarial review
    of C12-a**, when that form was left with test callers alone -- the shape
    ruling P54 names -- and moved to ``tests/_test_helpers.pricing_over``.

    Resolves nothing ITSELF, so a holder that prices no paycheck pays nothing
    for holding one.  The first query lands at
    :meth:`PaycheckPricing.for_profile`, which derives the calendar from its
    source on first use, reads that profile's raises when no set is given (a
    SELECT when they are not loaded) and builds a :class:`ProfilePaychecks`,
    whose tax series is sliced from the law without a query; nothing is issued
    before a profile is named.

    Args:
        user_id: The owner whose paychecks may be priced.
        calendar_source: Answers that owner's calendar; called until it
            answers once, never after.

    Returns:
        The empty pricer, its calendar not yet derived.
    """
    return PaycheckPricing(user_id, calendar_source)


def derived_paycheck_pricing(user_id: int) -> PaycheckPricing:
    """Return a pricer for a producer that holds NO read pass.

    **The interim that goes when every producer holds its pass, stated as
    such.**  Twelve call sites build an amount basis from an owner and a
    scenario with no :class:`~app.services.balance_at.BalanceContext` in reach
    -- the two settle doors, the row re-render helpers, the two
    template-conflict choosers, the companion, reconcile and statement-match
    scopes, the spending report, the credit workflow and the profile archive
    (greppable as ``cash_ledger.derived_amount_basis``; ledger row
    **BAL-491**, owned by balance:X-i6) -- and until each takes its pass,
    this is the pricer their basis is built over: :func:`paycheck_pricing` with
    :func:`~app.services.pay_calendar.calendar_for` bound to the owner.  It
    derives the calendar on the first paycheck it prices and never before,
    which is exactly the laziness :class:`SalaryPricing` had when it derived
    its own and the two reasons it had it: a settle of a non-salary row
    derives nothing and issues no calendar query, and an owner with no
    ``budget.pay_schedule`` row -- whom ``calendar_for`` REFUSES -- is refused
    only when a paycheck is actually asked for.  A caller holding a pass reads
    ``ctx.paychecks()`` and has no business here; each site moved onto its
    pass deletes one call, and this constructor goes with the last.

    Args:
        user_id: The owner whose paychecks may be priced.

    Returns:
        The empty pricer, its calendar not yet derived.
    """
    return paycheck_pricing(user_id, partial(calendar_for, user_id))


class SalaryPricing:
    """What the owner's active salary profiles pay, resolved per profile ASKED.

    **The DERIVATION half of the salary amount rule, split from its per-row
    lookup at plan step X-au-c2b, and the one producer of a ROW's amount since
    plan step X-au-d** (finding **N-443** is the two other spellings of the
    projection itself).  What rows a caller happens to have loaded reaches
    none of it: this is pinned to a scenario and a PRICER -- whose owner is
    the owner -- which is what lets one read pass resolve it ONCE however
    many row sets ask (:class:`~app.services.cash_ledger.AmountBasis`).

    **It holds a PRICER since plan step salary:S3-d, where it held a whole
    projection**, and the change is what a row's figure costs.  It memoized
    ``{profile_id: {period_id: PaycheckBreakdown}}`` over the owner's WHOLE
    saved window, so a single row's live amount ran the engine over every
    payday the owner has; it resolves the ONE period the row names now.

    **It TAKES the read pass's pricer since plan step salary:C12**, where it
    derived a second one -- ledger row **P63**, closed there and argued once,
    at :class:`PaycheckPricing`.  The owner is read off that pricer rather
    than handed in beside it, so the pair cannot disagree.

    It was a ``{transaction_id: Decimal}`` map built per row set until that step,
    and the two consequences are why this type exists.  A request that loaded two
    row sets ran the paycheck engine twice (findings **N-268**, **N-269**), and a
    row outside the set it was built over had no answer -- which forced the basis
    to carry the set as a membership guard so the miss could be REFUSED rather
    than read as "this row has no live figure".  Keyed on the definition and the
    period instead, there is no membership question left to get wrong: the pair
    IS the paycheck's identity.

    **Laziness is in TWO stages, and an adversarial review of this step's own
    build is why.**  The first draft resolved one map for every active profile
    on first read, gated only by ``is_income and template_id is not None`` -- a
    test that cannot tell a paycheck template from any other recurring income.
    Both halves of that were a regression against the row-set producer it
    replaced, which filtered its profile query by the CANDIDATE rows' templates
    and returned ``{}`` without projecting anything:

      * a recurring *Interest* or *Dividend* income row -- templated, projected,
        not a paycheck -- forced the whole paycheck engine, where the old path
        paid one indexed query and stopped; and
      * an owner with TWO active profiles paid ``project_salary`` twice on a
        pass whose rows named one of them.

    So the PROFILE LOOKUP is memoized here: asking about a template no profile
    names costs one indexed query and no engine run, and a profile is priced
    only when a row actually asks about it.  The pricing behind it lives for
    the pass, so the sharing this class exists for is unchanged.
    """

    def __init__(self, scenario_id: int, paychecks: PaycheckPricing) -> None:
        """Pin the scenario and the pricer; resolve nothing yet.

        Args:
            scenario_id: The scenario to resolve profiles against.
            paychecks: The pricer a row's paycheck is read from -- the read
                pass's, or the one a pass-less producer builds
                (:func:`derived_paycheck_pricing`).  Its ``user_id`` is the
                owner whose active profiles price these rows, answered
                without deriving a calendar.
        """
        self._scenario_id = scenario_id
        self._paychecks = paychecks
        self._profiles: "dict[int, SalaryProfile] | None" = None

    def paycheck_on(
        self, template_id: int, period,
    ) -> "paycheck_calculator.PaycheckBreakdown | None":
        """Return the paycheck the profile driving *template_id* pays on *period*.

        **The one walk from a recurring definition to its paycheck** (plan step
        salary:X-av-2, ruling **R-SAL71**): :meth:`net_for` reads a ROW's
        figure through it, and the Recurring surface's salary row reads
        TODAY's paycheck through it
        (``obligations_aggregator.todays_price``) -- the net AND the
        rhythm it was priced at
        (:attr:`~app.services.paycheck_calculator.PeriodInfo.cadence`), which a
        monthly figure must convert with.  The whole breakdown rather than a
        net because that pair is what the second reader needs, and one door
        answering both keeps them one pricing of one paycheck.

        Args:
            template_id: The recurring definition.
            period: The :class:`~app.services.pay_calendar.DerivedPeriod` to
                price -- saved, or projected past the horizon; the pricer
                prices either.

        Returns:
            The :class:`~app.services.paycheck_calculator.PaycheckBreakdown`,
            or ``None`` when no ACTIVE profile in this scenario names that
            template.

        Raises:
            UnsupportedStateError: The profile is in a state the tax law does
                not list (:meth:`PaycheckPricing.for_profile`; plan step
                salary:X-at-3).
        """
        profile = self._profile_by_template().get(template_id)
        if profile is None:
            return None
        return self._paychecks.for_profile(profile).at(period)

    def net_for(
        self, template_id: int, pay_period_id: int,
    ) -> Decimal | None:
        """Return what the profile driving *template_id* pays for that period.

        Args:
            template_id: The recurring definition the row was generated from.
            pay_period_id: The period the row is funded in.

        Returns:
            The live net pay, or ``None`` when no ACTIVE profile in this
            scenario names that template, or when that period is not one the
            owner's saved calendar holds.  Both are the refusals amount rule 2
            raises rather than substituting a stored figure.

        Raises:
            UnsupportedStateError: The profile is in a state the tax law does
                not list (:meth:`paycheck_on`; plan step salary:X-at-3).
        """
        # The profile is asked FIRST, before the calendar, so a row on a
        # template no profile names derives nothing (the laziness this class
        # docstring argues); :meth:`paycheck_on` then prices it.
        if self._profile_by_template().get(template_id) is None:
            return None
        paychecks = self._paychecks
        # The period is resolved by ID off the owner's calendar, and that is
        # amount rule 2's second refusal stated where it belongs: a row names
        # a ``budget.pay_periods`` row, so a ``pay_period_id`` the calendar
        # does not hold has no paycheck to price rather than a paycheck worth
        # nothing.  It replaced a membership test against a projected MAP at
        # plan step salary:S3-d, which answered the same question by pricing
        # the owner's whole window first.
        period = paychecks.calendar.period_by_id(pay_period_id)
        if period is None:
            return None
        return self.paycheck_on(template_id, period).earnings.net_pay

    def _profile_by_template(self) -> "dict[int, SalaryProfile]":
        """Return ``{template_id: profile}`` for this owner and scenario.

        One indexed query, memoized -- the CHEAP stage, so a row on a template
        no profile names is answered without projecting anything.

        **One profile per template in a scenario is the TABLE's rule since plan
        step salary:X-av-1** (``uq_salary_profiles_scenario_template``, ruling
        **R-SAL63** as scoped by **R-SAL69**, closing finding **N-294**), and
        this query reads ONE scenario, so no key here can have two profiles
        behind it.  Until then two active profiles naming one template were
        storable and this map kept whichever the query returned last; an
        ``ORDER BY id`` made that the same profile on every request without
        deciding which should price the row, and it is deleted with the state
        it ordered.  A profile whose template was hard-deleted (``SET NULL``)
        is left out: it prices no row, and several may share that NULL.

        Returns:
            ``{template_id: SalaryProfile}``; empty for an owner with no active
            profile on a template in this scenario.
        """
        if self._profiles is None:
            profiles = (
                db.session.query(SalaryProfile)
                .filter(
                    SalaryProfile.user_id == self._paychecks.user_id,
                    SalaryProfile.scenario_id == self._scenario_id,
                    SalaryProfile.is_active.is_(True),
                    SalaryProfile.template_id.isnot(None),
                )
                .all()
            )
            self._profiles = {p.template_id: p for p in profiles}
        return self._profiles


def salary_pricing(scenario_id: int, paychecks: PaycheckPricing) -> SalaryPricing:
    """Return the read pass's :class:`SalaryPricing` for a scenario and pricer.

    The named constructor the amount model calls, so no caller reaches for the
    class directly and the two pins are always supplied together.  Resolves
    nothing: every stage behind it is lazy, so a pass that prices no paycheck
    issues no query -- and a DERIVED pricer derives no calendar either, so
    that holds for a pass-less producer too.

    Args:
        scenario_id: The scenario to resolve profiles against.
        paychecks: The pricer the rows' paychecks are read from; its owner is
            the owner whose profiles price them.

    Returns:
        The unresolved :class:`SalaryPricing` handle.
    """
    return SalaryPricing(scenario_id, paychecks)


def salary_net_for(txn, pricing: SalaryPricing) -> Decimal | None:
    """Return what the salary profile pays for *txn*'s period, or ``None``.

    **The PRICING lookup -- amount rule 2's whole body** (ruling **R-FI**), and
    since plan step **X-au-d** the only producer of a ROW's amount.  It was
    split from a read-time repair (``live_projected_net``) at plan step
    X-au-c2b and stood beside it until that cutover declared salary rows
    DERIVED, at which point there was no stored figure left for a repair to
    supersede and the repair was deleted rather than left as a second walk to
    one answer (``CLAUDE.md`` rule 14, rulings **R-IZ** / **R-JA**).

    *It became the only spelling of the CALENDAR-WIDE projection at plan
    step* **salary:R14-a**, *which is finding* **N-443** *closed.*  The
    qualifier was load bearing and a second adversarial review put it back:
    ``tax_withholding_service`` and ``tax_report_service`` ran
    ``project_salary`` themselves over a single tax YEAR with
    ``tax_configs=``, a different question and not a fourth spelling.  They
    moved onto this pricer at plan step **salary:S11-c-2c**, when a pay stub
    began pricing on its own payday's year (ruling **R-SAL77**) and a year
    slice could need two years' law, so no ``app/`` site calls
    ``project_salary`` but this one -- and the arch test's census has counted
    every call of it since that step, so a new single-year caller fails it
    as a second spelling does.
    ``routes/salary/views`` and ``routes/salary/cockpit`` each built the same
    ``project_salary`` call over the same calendar, because they render the
    whole breakdown rather than the net -- so the shared leaf had to BE the
    breakdown, which is what :class:`ProfilePaychecks` holds and what
    :meth:`ProfilePaychecks.over` answers.  All three read it, and
    ``tests/test_arch/
    test_the_calendar_wide_projection_has_one_spelling.py`` is what keeps a
    fourth from appearing.

    It asks only what a paycheck IS worth, so it reads nothing about whether
    the row still counts: not ``is_projected``, not ``is_override``, not
    ``is_deleted``.  That is finding **N-262**'s rule applied one tier down --
    those three say whether a row COUNTS and who last touched it, never who
    prices it -- and it is why a Cancelled paycheck resolves like any other
    instead of refusing for a reason that has nothing to do with pricing.  The
    repair that DID read those three is what X-au-d removed, so the split it
    was written to protect is now simply the shape of the module.

    ``is_income`` IS read, and it is a pricing fact rather than a status one: a
    salary profile states a NET PAY, so an expense row on a salary-linked
    template has no figure here to find.  ``amount_rule`` still places such a
    row under rule 2 (it classifies by the definition, which is salary-linked),
    so this returning ``None`` is what turns it into that rule's refusal.

    Args:
        txn: The row being priced.  ``is_income``, ``template_id`` and
            ``pay_period_id`` are read.
        pricing: The read pass's :class:`SalaryPricing`.

    Returns:
        The live net pay for the row's period, or ``None`` when no active
        profile in this scenario names its template, when the projection does
        not cover its period, or when it is not an income row.

    Raises:
        UnsupportedStateError: The profile is in a state the tax law does not
            list (:meth:`SalaryPricing.net_for`; plan step salary:X-at-3).
    """
    if not txn.is_income or txn.template_id is None:
        return None
    return pricing.net_for(txn.template_id, txn.pay_period_id)
