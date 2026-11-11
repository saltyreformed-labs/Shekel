"""The paycheck engine's owner-level input: a salary profile and its calendar.

Plan step **R-F16** (shipped at ``4258ce28``).
Its own module rather than a type inside
:mod:`app.services.paycheck_calculator`, which sits at the 1000-line ceiling
this step's own additions pushed it past -- and because nine modules outside
that one CONSTRUCT this value while only it computes a paycheck, so the input
has more consumers than the producer does.  Every one of the nine imports it
from HERE; an earlier draft of this sentence made the consumer argument while
all nine still reached the type through the engine, which is a claim its own
import graph refuted.

No Flask, no clock, no database access of its own.  What it reads off the
profile it is handed -- the raise rows, since plan step salary:R15-b each
deduction's recurrence rule, and since salary:X-av-3a the pay list -- it reads
as ATTRIBUTES the caller loaded (the engine's loader eager-loads all three;
``test_projection_inputs`` counts the
statements a walk issues and finds none), converting each to a value through
the reader that owns its vocabulary: :func:`~app.services.salary_raises
.terms_of` for a raise, :func:`~app.services.recurrence.recurrence_spec` for
a rule.  The profile is typed loosely on purpose (see the class).
"""
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from functools import cached_property
from typing import Any

from app.services.pay_calendar import (
    PaidDays,
    PayCadence,
    PayCalendar,
    cadence_on,
    days_paid_for,
    first_day_paid_at,
    first_payday_of,
    previous_payday,
)
from app.services.recurrence import (
    ResolvedRecurrence,
    projected_occurrence_placements,
    recurrence_spec,
    resolve,
)
from app.services.pay_banner import ForecastRaise, PayBanner, PayEvent, RecordedChange
from app.services.pay_rate import BasePay, PayRate
from app.services.salary_raises import (
    RaiseTerms,
    applications_between,
    raise_pay,
    terms_of,
)
from app.utils.business_days import SATURDAY
from app.utils.money import ZERO, round_money

#: How far past an ask a cadence is walked before it is asked again: every
#: walk reaches at least a year past the payday asked, and each later
#: extension also doubles the span walked so far, so a projection that asks
#: ascending paydays for twenty years re-walks about five times and never
#: walks past twice its horizon.  A walk to the application's last date was
#: the first shape and it cost 800 ms per basis on the developer's eleven
#: lines (measured 2026-09-13): past the saved schedule the ceiling arm
#: re-projects the month for every occurrence, so the walk must reach only
#: what is asked of it.  **The year past the ask is load-bearing, not
#: slack** (an adversarial review of this step): under ``CONTAINING_DATE`` an
#: occurrence up to a full period AFTER a payday places on that payday, so a
#: reach set exactly at the payday asked stops the walk before the
#: occurrence that admits it and caches the wrong ``False``.
_REACH_PAST_ASK = timedelta(days=366)

@dataclass
class _WalkedCadence:
    """One resolved cadence's admitted paydays, walked as far as it has been asked.

    Mutable, and private to :class:`PayrollBasis`: a memo, not a value.
    Every line that resolves to the same :class:`ResolvedRecurrence` shares
    one of these -- the developer's eleven 24-per-year lines are ONE walk --
    because the occurrence set is a function of the cadence and the calendar
    alone.

    Attributes:
        resolved: The cadence walked.
        through: The last day the walk has reached, or ``None`` before it is
            asked anything.
        admitted: Every payday the walk placed an occurrence on, through
            :attr:`through`.
    """

    resolved: ResolvedRecurrence
    through: date | None = None
    admitted: frozenset[date] = frozenset()

    def admits(self, calendar: PayCalendar, payday: date) -> bool:
        """Whether an occurrence lands on the paycheck of *payday*, walking further if needed.

        Refuses nothing: a payday this calendar cannot place is simply not in
        the set.  The engine's refusal of such a payday is
        :func:`~app.services.paycheck_calculator._calendar_questions
        ._month_ordinal`'s, read before any line is priced.
        """
        if self.through is None or payday > self.through:
            self._walk(calendar, payday)
        return payday in self.admitted

    def _walk(self, calendar: PayCalendar, payday: date) -> None:
        """Re-walk from the cadence's first occurrence to a year past *payday* at least.

        A later walk also doubles the span walked so far, so the reach grows
        geometrically with the asks; the set is REPLACED by the longer walk's,
        which is a superset of the shorter's (the same cadence over a longer
        window).
        """
        through = payday + _REACH_PAST_ASK
        if self.through is not None:
            through = max(through, self.through + (self.through - self.resolved.starts_on))
        self.admitted = frozenset(
            placement.period.start_date
            for placement in projected_occurrence_placements(
                self.resolved, calendar, through=through,
            )
            if placement.period is not None
        )
        self.through = through


@dataclass(frozen=True)
class RhythmChangeWithoutPay:
    """A change of rhythm no pay entry is recorded from, as the salary page names it.

    Plan step **salary:X-av-3a**, ruling **R-SAL82** ("Yearly pay carries,
    said"): where the owner's rhythm changes and no entry is recorded from the
    new rhythm's first paycheck, the pay from then is the yearly pay carried
    across the change -- the pay before it times the old count, over the new
    count -- and the salary page says so rather than believing the figure
    silently.

    Attributes:
        first_payday: The new rhythm's first payday.
        per_paycheck: The pay carried to the new rhythm, in force from the
            first day its first paycheck pays for.
        carried_annual: The yearly pay carried across: the pay just before
            the change times the paychecks a year it was paid at.
    """

    first_payday: date
    per_paycheck: Decimal
    carried_annual: Decimal


@dataclass(frozen=True)
class _Entry:
    """One pay entry as the walk reads it.

    Private to :class:`PayrollBasis`.

    Attributes:
        opens: The first day the entry's paycheck pays for: the entry is the
            pay in force from then (ruling **plan#3091**).
        payday: The entry's payday.
        amount: What one paycheck pays, as recorded.
    """

    opens: date
    payday: date
    amount: Decimal


@dataclass(frozen=True)
class _InForce:
    """The pay in force on one day, and what the walk to it read.

    Private to :class:`PayrollBasis`.

    Attributes:
        rate: The day's :class:`PayRate`.
        entry: The entry the pay was walked from.
        carried: ``(day, yearly pay)`` of the LAST change of count the walk
            carried the pay across, or ``None`` when it crossed none.
        raised: ``(landing day, raise)`` of every forecast application the
            walk raised the pay by, in the order it applied them: what a
            paycheck's banner names (:meth:`PayrollBasis.banner_on`).
    """

    rate: PayRate
    entry: _Entry
    carried: "tuple[date, Decimal] | None"
    raised: "tuple[tuple[date, RaiseTerms], ...]"


def _carry(pay: Decimal, paid_at: PayCadence, to: PayCadence) -> Decimal:
    """Return *pay* at rhythm *paid_at* carried to rhythm *to*, keeping its yearly pay.

    Ruling **R-SAL82**: at a change of rhythm with no pay recorded from it,
    the paycheck becomes the yearly pay (pay x paychecks a year) over the new
    count, rounded to the cent.

    Args:
        pay: The per-paycheck pay before the change, cent-exact.
        paid_at: The rhythm it was paid at.
        to: The rhythm it is carried to.

    Returns:
        The carried pay, quantized to the cent.
    """
    return round_money(PayRate(pay, paid_at).annual / to.periods_per_year)


def _at_rhythm(rate: PayRate, rhythm: PayCadence) -> PayRate:
    """Return *rate* paid at *rhythm*: carried across a change of count, else as it is.

    "A change of rhythm" is a change of COUNT, the test the walk's own carry
    turns on: a new phase paying as often keeps the pay.

    Args:
        rate: The rate to restate.
        rhythm: The rhythm to state it at.

    Returns:
        The :class:`PayRate` at *rhythm*.
    """
    if rate.periods_per_year == rhythm.periods_per_year:
        return PayRate(rate.per_paycheck, rhythm)
    return PayRate(_carry(rate.per_paycheck, rate.cadence, rhythm), rhythm)


def _priced_days(paid: PaidDays) -> "list[date]":
    """Return the days a paycheck's base is priced over: its pay period's weekdays.

    Ruling **plan#3089**: a paycheck pays its pay period's Monday-to-Friday
    days, a holiday or a vacation day included (the 07-16 stub paid 07-03, a
    holiday, at the new pay), each at the pay in force that day.  **A period
    holding no weekday is priced on its last day**, at the pay in force then:
    only a rhythm of one or two days can hold such a period, a weekend, and
    with no weekday to share the pay over it pays its rate, as a paycheck no
    change reaches does.  The pricing (:meth:`PayrollBasis.base_pay_on`) and
    the banner (:meth:`PayrollBasis.banner_on`) both read these days, so a
    raise is announced on the paycheck that first PAYS a day of it (task
    plan#3122).

    Args:
        paid: The paycheck's pay period.

    Returns:
        The days, ascending; never empty.
    """
    span = (paid.last - paid.first).days + 1
    weekdays = [
        day for day in (paid.first + timedelta(days=offset) for offset in range(span))
        if day.weekday() < SATURDAY
    ]
    return weekdays or [paid.last]


@dataclass(frozen=True)
class PayrollBasis:
    """One owner's salary contract bound to the calendar their paychecks arrive on.

    **It carries the whole :class:`~app.services.pay_calendar.PayCalendar`
    since plan step balance:X-bh-1, and that is the same fix applied a second
    time.**  It held a bare
    :class:`~app.services.pay_calendar.PayCadence` until then, while the engine
    took the owner's period SET as a separate argument -- so the paycheck count
    and the paydays it counted arrived by two routes with nothing holding them
    to one owner or one read.  Four of the engine's judgements read that
    argument (third-paycheck detection, the first-paycheck-of-month deduction
    cadence, the FICA wage-base cumulative and a deduction's annual cap), its
    type was ``Sequence[DerivedPeriod]``, and every window, year slice and
    one-period sample satisfied that type: passing one cost a stored salary row
    **$502.45** (ledger row **D25**).  A calendar is constructible only from a
    COMPLETE payday set, so the narrow context is now unrepresentable rather
    than forbidden by a docstring, and the cadence comes off the same
    derivation as the paydays.  That closed finding **N-390**'s first half;
    the second -- what the calendar can answer BELOW its opening payday --
    closed at plan step **balance:X-bh-2**, which gave the calendar the owner's
    stored ``history_opens_on`` and made its rhythm run in both directions.

    **The pair, as one value, is plan step R-F16's fix for finding F-16.**  The
    engine needs two facts to price a paycheck -- what the job pays a year, and
    how many paychecks that year holds -- and until this step they travelled
    separately: the salary profile carried its own ``pay_periods_per_year``
    column (a 12 / 24 / 26 / 52 dropdown) while
    ``budget.pay_schedule.cadence_days`` carried the payday rhythm, and no door
    validated one against the other.  Measured with the real engine on a
    ``$91,675`` salary, a profile saying 26 beside a 7-day cadence modelled
    ``$15,279.20`` of monthly gross against a true ``$7,639.60`` -- the year's
    paychecks summing to 200% of salary.  Only 5 of the 365 legal cadences had
    a dropdown value that could agree with them at all, so validating the pair
    was not an available remedy: the count had to become a derivation.

    Binding them makes the mismatched pair unrepresentable rather than merely
    discouraged -- the same argument the read-pass ruling makes for
    ``BalanceContext`` -- and there is now ONE derivation of the count,
    :attr:`~app.services.pay_calendar.PayCadence.periods_per_year`, which is
    also what every monthly-equivalent conversion in the application reads.
    A salary profile's paycheck recurs every pay period BY DEFINITION (it is
    what ``routes.salary.profiles._paycheck_template`` authors), so there was
    never a per-profile count for the dropped column to hold.

    **It NAMES the raise set it prices from since plan step salary:S3-f-1**
    (ruling **R-SAL20**), the way it names its calendar.  The engine read
    ``basis.profile.raises`` -- the ORM relationship -- at three sites, so the
    only raise set a paycheck could ever be priced under was the one stored,
    and a what-if over a raise's end year (the ``/retirement`` rail's per-raise
    probe, plan step **salary:S3-f**) had no input to arrive through.  The
    set is an INPUT of this value now: the profile's own rows' terms unless a
    caller supplies :attr:`raise_terms`, and every engine read goes through
    :attr:`raises`, which is always a tuple of
    :class:`~app.services.salary_raises.RaiseTerms` -- the VALUE whose fields
    are the engine's whole contract with a raise.  The stored plan is
    unchanged by construction: a basis built without terms prices exactly the
    figures its rows carry, read through the same walk.

    Attributes:
        profile: The ``SalaryProfile`` -- read for its pay list, the days
            its pay periods end before payday
            (``period_end_days_before_payday``, ruling **plan#3096**), the
            deductions and the W-4 inputs, and for the raises when no
            :attr:`raise_terms` are supplied.  Typed loosely because every
            consumer reads attributes rather than the ORM class, and because
            this module must not import a model (the engine below it is
            pure).  The test suite prices duck-typed profiles through the same
            door for that reason.
        calendar: The owner's whole
            :class:`~app.services.pay_calendar.PayCalendar` -- the payday set
            every calendar question the engine asks is answered from, and the
            eras whose rhythm :meth:`base_pay_on` prices each payday at.  ONE
            value because they are one fact: the cadence is a field of the
            calendar, so a paycheck cannot be priced at one rhythm and placed
            in a month counted at another.
        raise_terms: The raise set to price from INSTEAD of the profile's
            rows, as a tuple of :class:`~app.services.salary_raises.RaiseTerms`,
            or ``None`` for the rows' own terms -- the default, and what every
            direct constructor passes; the read pass's pricer
            (:meth:`~app.services.income_service.PaycheckPricing.for_profile`)
            passes the canonical tuple for every set, the stored one included.
            A ``None`` here does NOT stand for a stored number the way the
            plan point's rule forbids: it stands for the rows, which are the
            one home of the stored fact, and this value is an input rather
            than a memo key -- the pricer's memo is keyed on the tuple itself.
    """

    profile: Any
    calendar: PayCalendar
    raise_terms: "tuple[RaiseTerms, ...] | None" = None

    @cached_property
    def raises(self) -> "tuple[RaiseTerms, ...]":
        """The raise set the engine prices this basis from, as values.

        **The one read the engine makes of a raise set.**  Resolved on READ
        rather than at construction: ``SalaryProfile.raises`` is
        ``lazy="select"``, so touching it here would move a caller's SELECT
        from the first paycheck priced to the moment a basis is built, and a
        basis is built at nine sites that did not ask for that.  Cached
        because the engine asks it once per prior payday when it replays a
        year's cumulatives, and converting the rows each time would be work
        for the same answer; a frozen dataclass
        admits ``cached_property`` because it writes the instance dict
        directly rather than through the refused ``__setattr__``.

        **Both arms go through** :func:`~app.services.salary_raises.terms_of`,
        so "always a tuple of ``RaiseTerms``" is a property of this value and
        not of its callers' care: a supplied set spelled with rows or other
        raise-shaped objects is converted too, and the engine below never
        prices anything but the value.  A second adversarial review of this
        step found the first draft converting only the default arm.

        Returns:
            :attr:`raise_terms` canonicalised when a caller supplied one, else
            the profile's rows converted.
        """
        return terms_of(
            self.profile.raises if self.raise_terms is None else self.raise_terms
        )

    @cached_property
    def _line_cadences(self) -> "dict[Any, _WalkedCadence | None]":
        """Each deduction's cadence, resolved ONCE and walked as it is asked.

        The paycheck engine's one read of a deduction's FREQUENCY (plan step
        salary:R15-b, rulings **R-SAL3** and **R-SAL29**): a line's
        :attr:`~app.models.paycheck_line.PaycheckLine
        .recurrence_rule` is read through :func:`~app.services.recurrence
        .recurrence_spec`, resolved against THIS calendar, and its
        occurrences placed on saved and projected paychecks alike through
        :func:`~app.services.recurrence.projected_occurrence_placements` --
        the walk every recurring definition is generated by, so a 24-per-year
        line skipping a month's third paycheck and a bill skipping it are
        the same rule read the same way.  Lines that resolve to the SAME
        cadence share one :class:`_WalkedCadence`, and a walk reaches only as
        far as it is asked (:data:`_REACH_PAST_ASK`): the engine prices any payday
        the calendar can name, in no fixed order -- a projection walks
        forward, a capped line's year-to-date replays backward -- and the
        memo grows to meet it.

        ``None`` for a line with no rule: every paycheck, R-SAL3's NULL.  Keyed
        by the deduction object itself because the engine prices duck-typed
        deductions in its tests (a fake need carry no id), and read through
        ``getattr`` for the same reason the sibling optional columns are.

        Cached on the basis as :attr:`raises` is: one basis prices every
        payday of one profile at one raise set, and a frozen dataclass admits
        ``cached_property`` because it writes the instance dict directly.

        Returns:
            ``{deduction: its walked cadence, or None}`` for every deduction
            on the profile.
        """
        walks: dict[ResolvedRecurrence, _WalkedCadence] = {}
        cadences: dict[Any, _WalkedCadence | None] = {}
        for deduction in self.profile.lines:
            # ``getattr`` for the reason the engine reads ``annual_cap`` and
            # ``target_account_id`` that way: a deduction-like duck type (a
            # test fake) may omit the optional attribute.
            rule = getattr(deduction, "recurrence_rule", None)
            if rule is None:
                cadences[deduction] = None
                continue
            resolved = resolve(recurrence_spec(rule), self.calendar)
            cadences[deduction] = walks.setdefault(resolved, _WalkedCadence(resolved))
        return cadences

    def line_applies_on(self, line, payday: date) -> bool:
        """Whether *line* is taken on the paycheck of *payday*.

        ``deduction_applies_on`` until plan step salary:R18-b, when the
        earning kinds began asking it too (ruling **R-SAL38**); a line of any
        kind is placed by its rule the same way.

        Args:
            line: One of this profile's payroll lines.
            payday: The day the paycheck arrives -- a payday on this calendar,
                saved or projected.

        Returns:
            ``True`` when the line has no rule (every paycheck) or when its
            rule's walk placed an occurrence on this paycheck.
        """
        cadence = self._line_cadences[line]
        return cadence is None or cadence.admits(self.calendar, payday)

    @cached_property
    def _paid_days(self) -> "dict[date, PaidDays]":
        """Each payday's pay period, keyed by payday: each is asked ONCE per basis.

        :func:`~app.services.pay_calendar.days_paid_for` over the job's
        ``period_end_days_before_payday`` (ruling **plan#3096**).  Cached as
        :attr:`_line_cadences` is: a frozen dataclass admits
        ``cached_property`` because it writes the instance dict directly.
        """
        return {}

    def _paid_days_of(self, payday: date) -> PaidDays:
        """Return the days the paycheck paid on *payday* pays for, asking the calendar once."""
        paid = self._paid_days.get(payday)
        if paid is None:
            paid = self._paid_days[payday] = days_paid_for(
                self.calendar, payday, self.profile.period_end_days_before_payday,
            )
        return paid

    @cached_property
    def _pay_list(self) -> "tuple[_Entry, ...]":
        """The profile's pay entries, payday ascending, each with the day it opens.

        Read once per basis, as :attr:`raises` is.  Every door keeps a profile
        holding at least one entry (the create form writes the first; no door
        removes the last), so an empty list is a writer's defect, refused
        here by name rather than answered with an ``IndexError`` below.

        Raises:
            ValueError: The profile holds no pay entry.
        """
        entries = tuple(
            _Entry(self._paid_days_of(payday).first, payday, amount)
            for payday, amount in sorted(
                (entry.payday, entry.amount) for entry in self.profile.pay_entries
            )
        )
        if not entries:
            raise ValueError(
                f"salary profile {getattr(self.profile, 'id', None)} holds no "
                "pay entry, so no paycheck of it can be priced"
            )
        return entries

    def _entry_on(self, day: date) -> _Entry:
        """Return the entry in force on *day*: the latest opening on or before it, else the first.

        Ruling **R-SAL59** ("Paychecks before the first entry use it"), read by
        the first day each entry's paycheck pays for since ruling
        **plan#3091**.
        """
        chosen = self._pay_list[0]
        for entry in self._pay_list:
            if entry.opens > day:
                break
            chosen = entry
        return chosen

    @cached_property
    def _seams(self) -> "tuple[tuple[date, int], ...]":
        """``(first day, era index)`` of every era after the first, ascending.

        A change of rhythm takes effect from the first day its first paycheck
        pays for (:func:`~app.services.pay_calendar.first_day_paid_at`), so
        every day one paycheck pays for is paid at that paycheck's rhythm and
        a pay period never straddles one.
        """
        gap = self.profile.period_end_days_before_payday
        return tuple(
            (first_day_paid_at(self.calendar, index, gap), index)
            for index in range(1, len(self.calendar.eras))
        )

    def _rhythm_on(self, day: date) -> PayCadence:
        """Return the rhythm in force on *day*: the latest era whose first day is on or before it.

        The earliest era's before every later era's first day, below the record
        too, because that era alone runs backward (ruling **R-PC66**).
        """
        index = 0
        for first, era_index in self._seams:
            if first > day:
                break
            index = era_index
        return PayCadence(self.calendar.eras[index].rhythm.cadence)

    @cached_property
    def _in_force(self) -> "dict[date, _InForce]":
        """Every day walked so far, keyed by day: each is walked ONCE per basis.

        A paycheck asks each weekday it pays for and its last day, its banner
        asks the day before an entry opens, and the year-to-date replays ask
        every earlier payday of the year again for each paycheck they price.
        Sound for the reason :attr:`_pay_list` and :attr:`raises` are cached:
        a walk reads only those, the job's gap and the calendar, all fixed for
        one basis.
        """
        return {}

    def _in_force_on(self, day: date) -> _InForce:
        """Return *day*'s :class:`_InForce`, walking it only the first time it is asked."""
        in_force = self._in_force.get(day)
        if in_force is None:
            in_force = self._in_force[day] = self._walk(day)
        return in_force

    def _walk(self, day: date) -> _InForce:
        """Walk the pay list to *day*: the one derivation of the pay in force on a day.

        Rulings **R-SAL59**, **R-SAL60**, **R-SAL65**, **R-SAL82** and
        **plan#3091**, in date order from the entry in force on the day
        (:meth:`_entry_on`):

        * each change of rhythm after the entry opens and on or before the
          day (an era's first day, :attr:`_seams`) carries the pay across at
          the yearly figure (:func:`_carry`);
        * each forecast raise landing after the entry opens and on or before
          the day (:func:`~app.services.salary_raises.applications_between`)
          raises it by one cent-exact step
          (:func:`~app.services.salary_raises.raise_pay`), a flat raise
          spread over the count of the rhythm in force where it lands.  A
          raise landing on or before the day the entry opens is in the
          entry already (ruling **plan#3091**: recorded pay includes only
          raises dated by the first day its paycheck pays for);
        * on one day a change of rhythm comes first, because that day is
          paid at the new rhythm.

        A day BEFORE the first entry opens takes the entry carried to the
        day's rhythm and no raise: every application after the entry opens
        lands after the day too.

        Args:
            day: Any day.

        Returns:
            The :class:`_InForce` of the day.
        """
        entry = self._entry_on(day)
        pay = entry.amount
        paid_at = self._rhythm_on(entry.opens)
        final = self._rhythm_on(day)
        carried = None
        applications = applications_between(self.raises, entry.opens, day)
        events = [
            (first, 0, 0, index)
            for first, index in self._seams
            if entry.opens < first <= day
        ] + [
            (landed, 1, method_rank, raise_obj)
            for landed, method_rank, raise_obj in applications
        ]
        for landed, kind, _rank, subject in sorted(events, key=lambda e: e[:3]):
            if kind == 0:
                rhythm = PayCadence(self.calendar.eras[subject].rhythm.cadence)
                if rhythm.periods_per_year != paid_at.periods_per_year:
                    carried = (landed, PayRate(pay, paid_at).annual)
                    pay = _carry(pay, paid_at, rhythm)
                paid_at = rhythm
            else:
                pay = raise_pay(pay, subject, paid_at.periods_per_year)
        if final.periods_per_year != paid_at.periods_per_year:
            carried = (day, PayRate(pay, paid_at).annual)
            pay = _carry(pay, paid_at, final)
        return _InForce(
            PayRate(pay, final), entry, carried,
            tuple((landed, raise_obj) for landed, _rank, raise_obj in applications),
        )

    def pay_rate_on(self, day: date) -> PayRate:
        """Return the pay in force on *day*, at the rhythm in force that day.

        **The one derivation of a pay rate** (task plan#3100): the latest pay
        entry whose paycheck's first day is on or before *day* (else the
        first entry), raised by every forecast application landing after that
        first day and on or before *day*, across every change of rhythm, each
        step rounded to the cent as a stub prints it (:meth:`_walk` states
        the rules).  A paycheck's base reads it for each weekday it pays for
        and its rate for its last day (:meth:`base_pay_on`); the pension's
        yearly salary and the retirement dashboard's final paycheck read it
        for a December 1.

        Args:
            day: Any day: a day a paycheck pays for, or a day the pension
                evaluates a year at.

        Returns:
            The :class:`PayRate` in force on *day*.
        """
        return self._in_force_on(day).rate

    @cached_property
    def _base_pays(self) -> "dict[date, BasePay]":
        """Every payday priced so far, keyed by payday, as :attr:`_in_force` keys its days."""
        return {}

    def base_pay_on(self, payday: date) -> BasePay:
        """Return what the salary pays on *payday*: the paycheck's base, and its rate.

        **The one spelling of base pay for the engine** (plan step
        **salary:X-av-2**, ruling **R-SAL66**; ledger row **SAL-569**).  The
        paycheck, its percentage lines, the year-to-date wage and capped-line
        replays and the stub door's gap each read it; the pay list's yearly
        column reads its rate.  One read of :attr:`raises`, so a supplied
        raise set reaches every reader.

        **A paycheck pays the weekdays of its pay period, each at the pay in
        force that day** (ruling **plan#3089**, task plan#3100).  Its pay
        period is :func:`~app.services.pay_calendar.days_paid_for`'s; its
        weekdays (Monday to Friday, a holiday included, :func:`_priced_days`)
        are grouped by the pay in force on each (:meth:`pay_rate_on`) carried
        to the paycheck's own rhythm, each group's share of the weekdays is
        rounded to the cent as a stub prints each line, and the shares are
        summed.  A paycheck paid wholly at one pay pays that pay, since its
        one share is all of it; :class:`BasePay` works one that is not.

        **What it gives up is ruled three times**: the yearly figure is the
        rate times the paychecks a year rather than the salary typed
        (**R-SAL59**), each raise step rounds to the cent (**R-SAL60**), and
        each weekday share rounds to the cent as a stub line does
        (**plan#3089**), so a straddling paycheck can sit a cent off its
        exact blend.

        **The rhythm is the one in force ON THE PAYDAY**, through
        :func:`~app.services.pay_calendar.cadence_on`, which reads the era
        whose planned payday the day stands for, in cash days.  It rides on
        to :attr:`~app.services.paycheck_calculator.PeriodInfo.cadence`
        (ruling **R-SAL70**), which is what every reader converting the
        paycheck to a month or a year converts with.  Every day a paycheck
        pays for is paid at its rhythm already (:attr:`_seams`); the carry
        keeps the share's rhythm the paycheck's whatever the calendar holds.

        **A pay period holding no weekday pays its rate.**  Only a rhythm of
        one or two days can hold such a period, a weekend; with no weekday
        to share the pay over, the paycheck pays the pay in force on its last
        day, as a paycheck no change reaches does.

        **Total** for a calendar in hand (ruling **R-PC45**: a calendar
        carries at least one era) and a profile holding a pay entry.
        Resolved the first time each payday is asked and never at
        construction, so building a basis still reads nothing.

        Args:
            payday: The day the paycheck arrives -- saved, projected, or below
                the record.

        Returns:
            The :class:`BasePay` of that payday.
        """
        base = self._base_pays.get(payday)
        if base is None:
            base = self._base_pays[payday] = self._priced(payday)
        return base

    def _priced(self, payday: date) -> BasePay:
        """Price *payday*'s paycheck, as :meth:`base_pay_on` states the rule."""
        paid = self._paid_days_of(payday)
        rhythm = cadence_on(self.calendar, payday)
        rate = _at_rhythm(self.pay_rate_on(paid.last), rhythm)
        days = _priced_days(paid)
        shares = Counter(
            _at_rhythm(self.pay_rate_on(day), rhythm).per_paycheck for day in days
        )
        count = Decimal(len(days))
        return BasePay(
            sum((round_money(pay * days / count) for pay, days in shares.items()), ZERO),
            rate,
        )

    def banner_on(self, period) -> PayBanner:
        """Return *period*'s banner: its recorded pay change, and the raises it first pays.

        **Every change the paycheck is the first to show, each its own
        direction** (task plan#3168, finding plan#3118): the change a pay
        entry recorded from its payday makes, when it makes one, then each
        forecast raise the paycheck is the first to pay a day of, as
        :class:`~app.services.pay_banner.PayBanner` states them.  It answered
        with the recorded change alone until then, and with nothing for an
        entry changing nothing, so a raise landing inside the pay period of a
        paycheck where an entry is recorded was never announced.  Made-up:
        $2,000.00 a paycheck, 5% from Wednesday 2027-03-03, and $2,000.00
        re-recorded from 03-11, whose paycheck pays 02-20..03-05: the entry
        changes nothing and 03-11 pays 03-03..03-05 at $2,100.00, so it badges
        ``Merit +5%`` where it badged nothing, and no other paycheck badges
        the raise.  A banner was a display string until this task, its
        direction read back off its words (finding plan#1585).

        Ruling **R-SAL84** ("Banner the pay change") as ruling **plan#3091**
        amends it: the paycheck where a recorded pay entry begins shows ``Pay
        +$X`` -- the entry against the pay in force the day before the entry
        opens, PAY WITH PAY.  It compared the paycheck's base with the
        paycheck before it until then, which once a paycheck is priced
        weekday by weekday sets a whole raise against part of it.  Made-up:
        $2,000.00 a paycheck and 5% from Wednesday 2027-03-03, as
        :class:`BasePay` works it; the paycheck of 03-11 pays $2,030.00, and
        an entry of $2,100.00 recorded from 03-25, the first paycheck paid
        wholly at the raise, would have read ``Pay +$70.00`` for a change of
        nothing.  Pay with pay it is $2,100.00 against the $2,100.00 in force
        on 03-05, the day before the entry's paycheck opens, and badges
        nothing.  An entry changing nothing badges no change.

        **A forecast raise badges the first paycheck that PAYS a day of it**
        (ruling **plan#3092**, "where the pay first changes"; task plan#3122):
        the paycheck whose priced days (:func:`_priced_days`) hold the first
        priced day on or after the raise lands, and only where that paycheck
        is priced under it.  The banner names the applications the walk to
        the paycheck's last priced day applied (:meth:`_walk`, ``raised``)
        that land after the PREVIOUS paycheck's last priced day: the
        pricing's own days and its own read of the entry in force, so a
        banner cannot announce a raise the base beside it does not pay, nor
        miss one it does.  A raise dated on or before the day the paycheck's
        entry opens is inside the entry (ruling **plan#3091**), never
        applied, and is named only as the entry's own change.  Made-up: 5%
        from Wednesday 2027-03-03 badges 03-11, paying 02-20..03-05, and
        neither 02-25 nor 03-25; until task plan#3101 it badged every
        paycheck dated in March, 03-11 and 03-25.  Until task plan#3122 it
        named a raise landing anywhere in the pay period, so where periods
        run Sunday to Saturday a raise from a Saturday badged the paycheck
        ending that day, which pays no day of it, and not the next, which
        does.  **The banner follows the pay, not the rate**: that Saturday's
        paycheck's rate is the new pay (the pay in force on its last day,
        :meth:`base_pay_on`) while its base is wholly the old.

        **The FIRST entry badges no pay change**: every paycheck before it is
        priced FROM it (R-SAL59), so there is no earlier recorded pay to
        compare it with, and across a change of rhythm the comparison would
        read only the carry's rounding.  Made-up: a first entry of
        ``$4,333.33`` on the first payday of a 12-a-year rhythm, after a
        26-a-year one; the pay before it is that entry carried back,
        ``$4,333.33 x 12 / 26 = $2,000.00``, so the yearly comparison read
        ``$51,999.96`` against ``$52,000.00`` and badged a ``$0.04`` cut no
        one took (an adversarial review of plan step salary:X-av-3a).  Its
        payday badges no raise the entry holds either.

        **Across a change of rhythm it compares YEARLY pay**, labelled ``a
        year`` (ruling **R-SAL89**, "Yearly pay across a seam", amending
        R-SAL84): two paychecks paid at different counts are not
        comparable one to one, so ``$2,060.00`` biweekly followed by a
        recorded ``$1,030.00`` weekly is ``$53,560.00`` a year either side
        and badges nothing, where a per-paycheck comparison announced a
        ``$1,030.00`` cut.  "A change of rhythm" is a change of COUNT, the
        test :func:`_carry` also turns on: a new phase paying as often is
        compared per paycheck.

        Args:
            period: The paycheck's span on the calendar, read for its
                ``start_date``, the payday, alone.

        Returns:
            The :class:`~app.services.pay_banner.PayBanner`; empty for a
            paycheck that shows no change.
        """
        payday = period.start_date
        events: "list[PayEvent]" = [
            change
            for change in (
                self._recorded_change(entry)
                for entry in self._pay_list[1:] if entry.payday == payday
            )
            if change is not None
        ]
        last = _priced_days(self._paid_days_of(payday))[-1]
        before = _priced_days(
            self._paid_days_of(previous_payday(self.calendar, payday)),
        )[-1]
        events.extend(
            ForecastRaise(raise_obj)
            for landed, raise_obj in self._in_force_on(last).raised
            if landed > before
        )
        return PayBanner(tuple(events))

    def _recorded_change(self, entry: _Entry) -> "RecordedChange | None":
        """Return what *entry* changes: its pay against the pay before its paycheck opens.

        Pay with pay (ruling **plan#3091**), per paycheck, or a year across a
        change of count (ruling **R-SAL89**), as :meth:`banner_on` states.

        Args:
            entry: A pay entry after the first.

        Returns:
            The :class:`~app.services.pay_banner.RecordedChange`, or ``None``
            for an entry changing nothing.
        """
        after = self.pay_rate_on(entry.opens)
        before = self.pay_rate_on(entry.opens - timedelta(days=1))
        if before.periods_per_year == after.periods_per_year:
            change = RecordedChange(after.per_paycheck - before.per_paycheck, yearly=False)
        else:
            change = RecordedChange(after.annual - before.annual, yearly=True)
        return None if change.change == ZERO else change

    def rhythm_changes_without_pay(self) -> "list[RhythmChangeWithoutPay]":
        """Return each change of rhythm whose pay is priced by carrying the yearly pay.

        Ruling **R-SAL82**: the salary page names a change of rhythm no pay is
        recorded from.  A change keeping the count (a new phase at the same
        rhythm) carries nothing and is not named.  The change takes effect on
        the first day its first paycheck pays for (:attr:`_seams`), and it
        carries when no entry opens on that day.

        Returns:
            One :class:`RhythmChangeWithoutPay` per such change, oldest
            first; empty for an owner with one rhythm.
        """
        changes = []
        for first, index in self._seams:
            in_force = self._in_force_on(first)
            if in_force.carried is not None and in_force.carried[0] == first:
                changes.append(RhythmChangeWithoutPay(
                    first_payday_of(self.calendar.eras[index]),
                    in_force.rate.per_paycheck,
                    in_force.carried[1],
                ))
        return changes


__all__ = ["PayrollBasis", "RhythmChangeWithoutPay"]
