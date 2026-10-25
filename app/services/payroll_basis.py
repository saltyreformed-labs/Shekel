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
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from functools import cached_property
from typing import Any

from app.services.pay_calendar import (
    PayCadence,
    PayCalendar,
    cadence_on,
    first_payday_of,
)
from app.services.recurrence import (
    ResolvedRecurrence,
    projected_occurrence_placements,
    recurrence_spec,
    resolve,
)
from app.services.salary_raises import (
    RaiseTerms,
    applications_between,
    get_raise_event,
    raise_pay,
    terms_of,
)
from app.utils.money import round_money

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
class BasePay:
    """What the salary pays on one payday: the rate, and the rhythm it is paid at.

    Plan step **salary:X-av-2** (ruling **R-SAL66**; ledger row **SAL-569**),
    and since plan step **salary:X-av-3a** the RATE is the stored fact's
    (ruling **R-SAL59**).  The value :meth:`PayrollBasis.base_pay_on` returns,
    so the paycheck engine reads a payday's salary figures from ONE read: the
    per-paycheck rate every paycheck prices from, the yearly figure
    :class:`~app.services.paycheck_calculator.Earnings` reports, and the
    count Pub 15-T annualises the paycheck's wages by.  The count rides here
    rather than being asked again by the withholding because it is a fact of
    the payday, and two reads of it would be two places for a paycheck's
    annualiser and its yearly figure to part.

    **The yearly figure is a property of the other two, never a third field**
    (R-SAL59: "The yearly figure is pay x paychecks a year, shown, never
    stored").  Until X-av-3a it was the field and the rate its division,
    rounded; a pay list stores the rate, so the product is exact and the
    division is gone.

    **It carries the CADENCE, not only its count** (ruling **R-SAL70**): the
    engine hands it on as :attr:`~app.services.paycheck_calculator.PeriodInfo
    .cadence`, so a reader turning the paycheck into a monthly or yearly
    figure converts at the rhythm the paycheck was priced at, through
    :class:`~app.services.pay_calendar.PayCadence`'s own conversions, and
    never at a count read off the calendar a second time.

    Attributes:
        per_paycheck: What one paycheck pays on the payday, cent-exact: the
            pay entry it is priced from, carried across any change of rhythm
            and raised by every forecast raise landing after that entry
            (:meth:`PayrollBasis.base_pay_on`).
        cadence: The rhythm in force on the payday --
            :func:`~app.services.pay_calendar.cadence_on`'s answer.
    """

    per_paycheck: Decimal
    cadence: PayCadence

    @property
    def periods_per_year(self) -> Decimal:
        """Return how many paychecks a year :attr:`cadence` pays, an integral ``Decimal``."""
        return self.cadence.periods_per_year

    @property
    def annual(self) -> Decimal:
        """Return the yearly figure: :attr:`per_paycheck` times the paychecks a year, exact."""
        return self.per_paycheck * self.periods_per_year


@dataclass(frozen=True)
class RhythmChangeWithoutPay:
    """A change of rhythm no pay entry is recorded from, as the salary page names it.

    Plan step **salary:X-av-3a**, ruling **R-SAL82** ("Yearly pay carries,
    said"): where the owner's rhythm changes and no entry is recorded ON the
    new rhythm's first payday, that payday's pay is the yearly pay carried
    across the change -- the pay before it times the old count, over the new
    count -- and the salary page says so rather than believing the figure
    silently.

    Attributes:
        first_payday: The new rhythm's first payday.
        per_paycheck: What that payday is priced at.
        carried_annual: The yearly pay carried across: the pay just before
            the change times the paychecks a year it was paid at.
    """

    first_payday: date
    per_paycheck: Decimal
    carried_annual: Decimal


@dataclass(frozen=True)
class _Walked:
    """One payday's walk of the pay list: its :class:`BasePay`, and what it carried.

    Private to :class:`PayrollBasis`: the walk's answer plus the two facts
    only the rhythm notice and the pay-change banner read.

    Attributes:
        base: The payday's :class:`BasePay`.
        entry_payday: The payday of the entry the pay was walked from.
        carried: ``(day, yearly pay)`` of the LAST change of count the walk
            carried the pay across, or ``None`` when it crossed none.
    """

    base: BasePay
    entry_payday: date
    carried: "tuple[date, Decimal] | None"


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
    return round_money(BasePay(pay, paid_at).annual / to.periods_per_year)


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
        profile: The ``SalaryProfile`` -- read for its pay list, the
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
    def _pay_list(self) -> "tuple[tuple[date, Decimal], ...]":
        """The profile's pay entries as ``(payday, amount)``, payday ascending.

        Read once per basis, as :attr:`raises` is.  Every door keeps a profile
        holding at least one entry (the create form writes the first; no door
        removes the last), so an empty list is a writer's defect, refused
        here by name rather than answered with an ``IndexError`` below.

        Raises:
            ValueError: The profile holds no pay entry.
        """
        entries = tuple(sorted(
            (entry.payday, entry.amount) for entry in self.profile.pay_entries
        ))
        if not entries:
            raise ValueError(
                f"salary profile {getattr(self.profile, 'id', None)} holds no "
                "pay entry, so no paycheck of it can be priced"
            )
        return entries

    def _entry_for(self, payday: date) -> "tuple[date, Decimal]":
        """Return the entry *payday* is priced from: the latest on or before it, else the first.

        Ruling **R-SAL59**: "Paychecks before the first entry use it."
        """
        chosen = self._pay_list[0]
        for entry in self._pay_list:
            if entry[0] > payday:
                break
            chosen = entry
        return chosen

    @cached_property
    def _walked(self) -> "dict[date, _Walked]":
        """Every payday walked so far, keyed by payday: each is walked ONCE per basis.

        A paycheck asks its own payday twice -- its base pay and its banner --
        and the year-to-date replays ask every earlier payday of the year
        again for each paycheck they price, so without this a projection
        re-walks the same day many times for the same answer.  Sound for the
        reason :attr:`_pay_list` and :attr:`raises` are cached: a walk reads
        only those two and the calendar, all fixed for one basis.  Cached as
        :attr:`_line_cadences` is: a frozen dataclass admits
        ``cached_property`` because it writes the instance dict directly.
        """
        return {}

    def _walk_of(self, payday: date) -> _Walked:
        """Return *payday*'s :class:`_Walked`, walking it only the first time it is asked."""
        walked = self._walked.get(payday)
        if walked is None:
            walked = self._walked[payday] = self._walk(payday)
        return walked

    def _walk(self, payday: date) -> _Walked:
        """Walk the pay list to *payday*: the one derivation of what it pays.

        Rulings **R-SAL59**, **R-SAL60**, **R-SAL65** and **R-SAL82**, in
        date order from the entry the payday is priced from:

        * each change of rhythm (an era's first payday, ``first_payday_of``)
          carries the pay across at the yearly figure (:func:`_carry`);
        * each forecast raise landing after the entry's payday
          (:func:`~app.services.salary_raises.applications_between`) raises
          it by one cent-exact step
          (:func:`~app.services.salary_raises.raise_pay`), a flat raise
          spread over the count of the rhythm in force where it lands;
        * on one day a change of rhythm comes first, because that day's
          paycheck is paid at the new rhythm.

        The walk ends at :func:`~app.services.pay_calendar.cadence_on`'s
        rhythm for the payday, which is the rhythm the paycheck is placed and
        annualised at: a record paid a day early before a seam stands for the
        NEXT era's first payday and is carried to it, though that era's first
        payday is after it.  A payday BEFORE the first entry takes the entry
        carried to its own rhythm and no raise: every application after the
        entry lands after the payday too.

        Args:
            payday: The day the paycheck arrives.

        Returns:
            The :class:`_Walked` of the payday.
        """
        entry_payday, pay = self._entry_for(payday)
        paid_at = cadence_on(self.calendar, entry_payday)
        final = cadence_on(self.calendar, payday)
        carried = None
        events = [
            (first_payday_of(era), 0, 0, era)
            for era in self.calendar.eras[1:]
            if entry_payday < first_payday_of(era) <= payday
        ] + [
            (landed, 1, method_rank, raise_obj)
            for landed, method_rank, raise_obj in applications_between(
                self.raises, entry_payday, payday,
            )
        ]
        for landed, kind, _rank, subject in sorted(events, key=lambda e: e[:3]):
            if kind == 0:
                rhythm = PayCadence(subject.rhythm.cadence)
                if rhythm.periods_per_year != paid_at.periods_per_year:
                    carried = (landed, BasePay(pay, paid_at).annual)
                    pay = _carry(pay, paid_at, rhythm)
                paid_at = rhythm
            else:
                pay = raise_pay(pay, subject, paid_at.periods_per_year)
        if final.periods_per_year != paid_at.periods_per_year:
            carried = (payday, BasePay(pay, paid_at).annual)
            pay = _carry(pay, paid_at, final)
        return _Walked(BasePay(pay, final), entry_payday, carried)

    def base_pay_on(self, payday: date) -> BasePay:
        """Return what the salary pays on *payday*, with the rhythm it is paid at.

        **The one spelling of base pay for the engine** (plan step
        **salary:X-av-2**, ruling **R-SAL66**; ledger row **SAL-569**).  The
        paycheck, the FICA wage-base cumulative and a capped line's
        year-to-date each read it, as does the pension's salary path since
        plan step salary:X-av-3a.  One read of :attr:`raises`, so a supplied
        raise set reaches every reader.

        **It walks the PAY LIST since plan step salary:X-av-3a** (rulings
        **R-SAL59**, **R-SAL60**, **R-SAL65**, **R-SAL82**; :meth:`_walk`
        states the rules).  Until then it divided the post-raise annual
        salary by the payday's own count and rounded once; the stored fact
        is what one paycheck pays now, and each raise rounds as a stub
        prints it.

        **The rhythm is the one in force ON THE PAYDAY**, through
        :func:`~app.services.pay_calendar.cadence_on`, which reads the era
        whose planned payday the day stands for, in cash days.  It rides on
        to :attr:`~app.services.paycheck_calculator.PeriodInfo.cadence`
        (ruling **R-SAL70**), which is what every reader converting the
        paycheck to a month or a year converts with.

        **Total** for a calendar in hand (ruling **R-PC45**: a calendar
        carries at least one era) and a profile holding a pay entry.
        Resolved the first time each payday is asked (:meth:`_walk_of`
        remembers it for the basis) and never at construction, so building
        a basis still reads nothing.

        Args:
            payday: The day the paycheck arrives -- saved, projected, or below
                the record.  The raises read only its year and month; the
                rhythm reads the whole day, because an era takes effect on a
                day.

        Returns:
            The :class:`BasePay` of that payday.
        """
        return self._walk_of(payday).base

    def pay_event_on(self, period) -> str:
        """Return the banner of *period*'s paycheck: a recorded pay change, or its forecast raises.

        Ruling **R-SAL84** ("Banner the pay change"): the paycheck where a
        recorded pay entry begins shows ``PAY +$X`` -- its base pay against
        the paycheck before it -- and a forecast raise that entry replaces
        badges nothing (:func:`~app.services.salary_raises.get_raise_event`
        with the entry's payday).  An entry changing nothing badges nothing.

        **The FIRST entry badges no pay change**: every paycheck before it is
        priced FROM it (R-SAL59), so there is no earlier recorded pay to
        compare it with, and across a change of rhythm the comparison would
        read only the carry's rounding.  Made-up: a first entry of
        ``$4,333.33`` on the first payday of a 12-a-year rhythm, after a
        26-a-year one; the paycheck before it is that entry carried back,
        ``$4,333.33 x 12 / 26 = $2,000.00``, so the yearly comparison read
        ``$51,999.96`` against ``$52,000.00`` and badged a ``$0.04`` cut no
        one took (an adversarial review of this step).  Its payday badges
        nothing at all: a raise of its month lands on the 1st, on or before
        the payday, so the entry holds it (``get_raise_event`` with the
        entry's payday).

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
            period: The paycheck's pay period: its ``start_date`` is the
                payday, and it is handed on to ``get_raise_event``.  One
                argument, so the payday and the period cannot disagree.

        Returns:
            The comma-joined labels, or ``""``.
        """
        payday = period.start_date
        walked = self._walk_of(payday)
        if walked.entry_payday == payday and payday != self._pay_list[0][0]:
            previous = self.calendar.span_containing(payday - timedelta(days=1))
            if previous is None:
                return ""
            before = self.base_pay_on(previous.start_date)
            if before.periods_per_year == walked.base.periods_per_year:
                change = walked.base.per_paycheck - before.per_paycheck
                unit = ""
            else:
                change = walked.base.annual - before.annual
                unit = " a year"
            if not change:
                return ""
            sign = "+" if change > 0 else "-"
            return f"PAY {sign}${abs(change):,.2f}{unit}"
        return get_raise_event(self.raises, period, walked.entry_payday)

    def rhythm_changes_without_pay(self) -> "list[RhythmChangeWithoutPay]":
        """Return each change of rhythm whose first paycheck is priced by carrying the yearly pay.

        Ruling **R-SAL82**: the salary page names a change of rhythm no pay is
        recorded from.  A change keeping the count (a new phase at the same
        rhythm) carries nothing and is not named.

        Returns:
            One :class:`RhythmChangeWithoutPay` per such change, oldest
            first; empty for an owner with one rhythm.
        """
        changes = []
        for era in self.calendar.eras[1:]:
            first = first_payday_of(era)
            walked = self._walk_of(first)
            if walked.carried is not None and walked.carried[0] == first:
                changes.append(RhythmChangeWithoutPay(
                    first, walked.base.per_paycheck, walked.carried[1],
                ))
        return changes


__all__ = ["BasePay", "PayrollBasis", "RhythmChangeWithoutPay"]
