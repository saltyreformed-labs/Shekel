"""How a salary RAISE changes what a paycheck pays, and when it is an event.

Split out of :mod:`app.services.paycheck_calculator` at plan step **R-F16**,
which is the step that pushed that module past its 1000-line ceiling.  The
split is by CONCERN rather than by line count: applying raises is a rule about
``salary.salary_raises`` rows that two unrelated engines consume -- the
paycheck pipeline (:func:`~app.services.paycheck_calculator.calculate_paycheck`
/ ``project_salary``) and, until plan step salary:X-av-3a, the pension salary
projection, which reads the paycheck pipeline's own walk since.  It computes
no paycheck and reads no cadence -- a flat raise's paychecks a year is its
caller's argument -- so it never belonged to the engine's own file.

Pure: plain inputs, plain outputs, no Flask, no ORM, no clock, no database.

**A raise compounds on a PER-PAYCHECK pay since plan step salary:X-av-3a**
(rulings **R-SAL59**, **R-SAL60** and **R-SAL65**).  ``apply_raises`` walked
an ANNUAL salary through every application and rounded once at the end; the
salary's stored fact is what one paycheck pays now
(:class:`~app.models.salary_pay_entry.SalaryPayEntry`), so the walk lives in
:meth:`~app.services.payroll_basis.PayrollBasis.base_pay_on`, which reads the
two halves stated here: WHICH applications land between two days
(:func:`applications_between`) and what ONE application does to a paycheck's
pay (:func:`raise_pay`), each rounding to the cent the way a stub prints it.
The pension projection reads the same walk, so there is one.

**Every caller passes real ``SalaryRaise`` rows as of plan step salary:S3-c,
and the ENGINE passes :class:`RaiseTerms` values as of salary:S3-f-1.**  The
``raises`` argument was duck-typed so the pension projector could extrapolate
over FABRICATED values (deep-hunt #83) -- it built a ``TerminatedRaise`` per
row to carry a terminal year invented from
``auth.user_settings.merit_raise_horizon_years``.  Ruling **R-SAL11** made
that year a stored column on the row, so the fabrication, the value class it
produced and the setting it read are all deleted; the reads below are plain
attribute access rather than defended lookups.  The duck typing itself is
KEPT, and ruling **R-SAL20** is why: :attr:`~app.services.payroll_basis
.PayrollBasis.raises` is the profile's rows' terms unless a caller supplies
other terms, and a what-if over one raise's end year (plan step
**salary:S3-f**) arrives as :class:`RaiseTerms` carrying a row's terms with
that one changed.  That is an INPUT the owner typed for one request, with the
row still the one home of the stored fact -- not a second home computed from a
global on every render, which is what S3-c deleted.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.utils.money import round_money

#: The two answers to "how long is this recurring raise believed?" (plan step
#: **salary:S3-c**, ruling **R-SAL13**).  A MODE rather than a bare year,
#: because a form cannot tell an unanswered number input from a deliberate "no
#: end year", and the developer ruled on 2026-09-05 that a recurring raise is
#: ASKED rather than defaulted -- a default is a global belief in per-raise
#: clothing, which is the shape the deleted ``merit_raise_horizon_years`` had.
#: The mode is AUTHORITATIVE: "no end year" resolves to ``None`` whatever the
#: year box holds, so the two controls cannot disagree and no rule has to hold
#: them in step (rule 14: delete a home rather than keep two in step).
#:
#: **They live here, in the SERVICE tier, since plan step salary:S3-f-2b**,
#: where they were private constants of the salary schema.  Two doors answer
#: the question now -- the salary form (``RaiseCreateSchema``) and the
#: ``/retirement`` rail's per-raise probe -- and the rule that grades an
#: answer needs the ROW's effective year, which only the service holding the
#: rows has.  The schemas import the vocabulary from here; no service imports a
#: schema (the direction seven schema modules already take).
RAISE_END_MODE_YEAR = "year"
RAISE_END_MODE_NONE = "none"
RAISE_END_MODES = (RAISE_END_MODE_YEAR, RAISE_END_MODE_NONE)

#: The window a raise's YEAR -- its start's or its terminal one -- may fall
#: in: the ONE home of the numbers ``ck_salary_raises_valid_effective_date``
#: (as the days of those years, since plan step salary plan#3048 dated the
#: start) and ``ck_salary_raises_valid_terminal_year`` bound the columns by.
#: The schemas build their per-control ``Range`` from these (the early message
#: on the control); :func:`end_year_of` states the ceiling as a clause of the rule,
#: because ruling **R-SAL22** spells the rule as *not before the effective year
#: and not past 2100* and a service door resolving through the rule alone must
#: refuse what the column would.
RAISE_YEAR_MIN = 2000
RAISE_YEAR_MAX = 2100


class EndYearError(ValueError):
    """A recurring raise's end-year answer breaks :func:`end_year_of`'s rule.

    Carries WHICH half of the answer is wrong so a form can render the message
    on the right control: the salary schema maps ``"mode"`` to its
    ``raise_end_mode`` field and ``"year"`` to ``terminal_year``; the rail's
    probe reports it against the raise's row.

    Attributes:
        field: ``"mode"`` or ``"year"``.
        message: The sentence the owner reads.
    """

    def __init__(self, field: str, message: str) -> None:
        """Record which half failed and why.

        Args:
            field: ``"mode"`` or ``"year"``.
            message: The sentence the owner reads.
        """
        super().__init__(message)
        self.field = field
        self.message = message


def end_year_of(mode: "str | None", year: "int | None", effective_year: int) -> "int | None":
    """Resolve a recurring raise's end-year answer through the ONE rule.

    **The one statement of what a valid answer is** (ruling **R-SAL22**: ONE
    end-year rule, spelled once and shared).  Until plan step salary:S3-f-2b it
    lived inside ``RaiseCreateSchema.validate_end_year`` against the payload's
    own effective year; the ``/retirement`` rail's per-raise probe asks the
    same question of the STORED row, and S3-f-3's Save will ask it a third
    time, so the rule moved to where all three can reach it.  It mirrors the
    two CHECKs that bound the column and the ruling that the answer is asked:

    * the mode must be answered (``R-SAL13``: an unanswered end year would
      silently mean *indefinitely*, so it is REFUSED rather than believed);
    * under :data:`RAISE_END_MODE_NONE` the year box is IGNORED -- the mode is
      authoritative, so there is no value left to contradict anything;
    * under :data:`RAISE_END_MODE_YEAR` a year is required, it cannot precede
      the raise's effective year
      (``ck_salary_raises_terminal_year_not_before_effective``), and it cannot
      pass :data:`RAISE_YEAR_MAX` (``ck_salary_raises_valid_terminal_year``).

    The schemas ALSO state the window as a ``Range`` on their year controls,
    built from the same two constants, so the owner reads the refusal on the
    control before this runs; on a schema path the ceiling clause here cannot
    fire, and it is kept because the rule is the rule wherever it is asked
    (an adversarial review of this step: a door resolving through this
    function alone would otherwise store 2101 and die on the CHECK).  Whether
    the raise is recurring at all is the caller's question: a one-time raise
    has no end-year answer to resolve
    (``ck_salary_raises_terminal_year_only_on_a_recurring_raise``).

    Args:
        mode: The answer's kind -- one of :data:`RAISE_END_MODES`, or anything
            else (``None`` included) for *unanswered*.
        year: The year box's value, or ``None`` for empty.
        effective_year: The year the raise first applies -- the payload's on
            the salary form, the ROW's on the rail.

    Returns:
        The resolved terminal year: ``None`` for *no end year*, else *year*.

    Raises:
        EndYearError: The mode is unanswered, the year is missing where the
            mode demands one, the year precedes *effective_year*, or the year
            is past :data:`RAISE_YEAR_MAX`.
    """
    if mode not in RAISE_END_MODES:
        raise EndYearError(
            "mode",
            "Say how long this recurring raise is believed: pick an end "
            "year, or say it has none.",
        )
    if mode == RAISE_END_MODE_NONE:
        return None
    if year is None:
        raise EndYearError(
            "year", "Enter the last year this raise is believed to happen.",
        )
    if year < effective_year:
        raise EndYearError(
            "year",
            f"A raise cannot end before it starts: it takes effect in "
            f"{effective_year}.",
        )
    if year > RAISE_YEAR_MAX:
        raise EndYearError(
            "year", f"A raise cannot be believed past {RAISE_YEAR_MAX}.",
        )
    return year


@dataclass(frozen=True)
class RaiseTerms:
    """The terms of ONE raise, exactly as the two walks below read them.

    **A VALUE, and the CONTRACT** (plan step salary:S3-f-1, ruling
    **R-SAL20**).  The paycheck engine prices every paycheck from a tuple of
    these -- :attr:`~app.services.payroll_basis.PayrollBasis.raises` converts
    the profile's rows through :meth:`of` -- so a walk that starts reading an
    attribute this class does not carry fails on the first paycheck it prices
    rather than pricing rows correctly and values wrongly.  That is what makes
    the field list here the ONE statement of what a raise contributes to a
    paycheck, and what lets :meth:`~app.services.income_service
    .PaycheckPricing.for_profile` key its memo on the tuple ITSELF: two raise
    sets that would price every payday identically are equal here, whatever
    objects a caller spelled them with, so one pricer serves both.  A memo key
    must be canonical for the reason
    :class:`~app.services.retirement_plan.PlanPoint` records -- two spellings
    of one plan are two derivations of one figure -- and an adversarial review
    of this step measured that a key over the caller's objects admitted three
    spellings of the stored set.

    Frozen and hashable by value.  ``percentage`` and ``flat_amount`` are
    carried as the ``Decimal`` the column holds (``Decimal("0.0500")`` and
    ``Decimal("0.05")`` are equal and hash alike); ``raise_type_name`` is the
    type's display name, which :func:`get_raise_event` composes a label from
    and which is the only thing the engine reads of a raise's type.

    Attributes:
        effective_date: The day the raise starts (ruling **plan#3090**): its
            first application, and for a recurring raise the month and day
            it repeats on every later year it is believed.  A year and a
            month until plan step salary plan#3048, which the walk landed on
            the month's 1st.
        is_recurring: Whether it applies every year from then on.
        percentage: The fractional raise, or ``None`` for a flat one.
        flat_amount: The annual dollar raise, or ``None`` for a percentage.
        terminal_year: The last year it is believed, ``None`` for no end.
        raise_type_name: The display name of its type (``"merit"``, ...).
    """

    effective_date: date
    is_recurring: bool
    percentage: "Decimal | None"
    flat_amount: "Decimal | None"
    terminal_year: "int | None"
    raise_type_name: str

    @classmethod
    def of(cls, raise_obj) -> "RaiseTerms":
        """The terms of *raise_obj* -- a row, a value, or any raise-shaped object.

        Args:
            raise_obj: Anything exposing the six attributes above.  A
                :class:`~app.models.salary_raise.SalaryRaise` row does (its
                ``raise_type_name`` is a property over the joined type); so
                does another :class:`RaiseTerms`, which converts to an equal
                value.

        Returns:
            The :class:`RaiseTerms`.
        """
        return cls(
            effective_date=raise_obj.effective_date,
            is_recurring=bool(raise_obj.is_recurring),
            percentage=raise_obj.percentage,
            flat_amount=raise_obj.flat_amount,
            terminal_year=raise_obj.terminal_year,
            raise_type_name=raise_obj.raise_type_name,
        )


def terms_of(raises) -> "tuple[RaiseTerms, ...]":
    """Every raise in *raises* as :class:`RaiseTerms`, in the order given.

    **The one canonical spelling of a raise set.**  Order is kept rather than
    sorted because it is part of the answer: :func:`get_raise_event` joins
    labels in iteration order, and :func:`applications_between` keeps input
    order between two applications on one date by one method.

    Args:
        raises: An iterable of raise-shaped objects (see :meth:`RaiseTerms.of`).

    Returns:
        The tuple of values; empty for an empty or falsy *raises*.
    """
    return tuple(RaiseTerms.of(raise_obj) for raise_obj in (raises or ()))


def applications_between(raises, after, through):
    """Return every raise APPLICATION landing after *after* and on or before *through*.

    **The one statement of which forecast raises a paycheck is priced under**
    (plan step salary:X-av-3a, ruling **R-SAL59**).  An application lands on
    its own DATE since plan step salary plan#3048 (ruling **plan#3090**): a
    raise's start date, and for a recurring raise the same month and day in
    each later year it is believed.  It landed on the 1st of its effective
    month until then, which is the same day for every raise that migration
    ``6740ce13e174`` dated, since each was dated on its month's 1st.  *after*
    is the payday of the pay entry the paycheck is priced from: an entry
    REPLACES every forecast raise due on or before its payday, because the
    pay it records already holds them.

    **APPLICATIONS are ordered by the date each one lands on**, not by the
    raise they belong to.  Raise application is non-commutative (a flat
    raise then a percentage is not a percentage then a flat raise), so the
    order is the answer: grouping each raise's whole run of yearly
    applications before the next raise began was a defect -- a recurring flat
    COLA contributed all of its additions up front and a recurring
    percentage then multiplied the lot, including dollars that percentage
    had not been earned on.  Within a single date a flat raise applies before
    a percentage one (M-01; deep-hunt #12 added the method tie-break), and
    two applications on one date by one method keep their input order.

    A raise applies if its start date is on or before *through* (a
    recurring raise once a year from then on), and it has not TERMINATED
    first (:func:`_is_believed_in`): a terminated raise still applies; it
    stops accruing further applications after its last believed year.

    Args:
        raises: An iterable of raise-shaped objects, each exposing
            ``effective_date``, ``is_recurring``, ``percentage``,
            ``flat_amount`` and ``terminal_year`` -- rows or
            :class:`RaiseTerms` values.
        after: The day the pay being raised was recorded from; an
            application landing on or before it is excluded.
        through: The last day an application may land on to be included.

    Returns:
        ``(date, method_rank, raise_obj)`` for each application, in the order
        they apply: by date, a flat raise before a percentage one on one
        date, then input order.
    """
    applications = sorted(
        _applications(raises or (), through), key=lambda a: a[:2],
    )
    return [a for a in applications if a[0] > after]


def raise_pay(pay, raise_obj, periods_per_year):
    """Return a paycheck's pay after ONE raise application, rounded to the cent.

    **Each raise makes a new cent-exact pay, the way a stub prints it**
    (ruling **R-SAL60**), and the next raise compounds on that printed
    figure.  A percentage raise multiplies the pay; a FLAT raise is stated in
    dollars a YEAR (ruling **R-SAL65**: the salary form labels the box "a
    year") and adds that amount divided by the paychecks a year of the rhythm
    the paycheck is paid at.

    Args:
        pay: The per-paycheck pay the raise applies to, cent-exact.
        raise_obj: The raise, exposing ``percentage`` and ``flat_amount``
            (exactly one is set: ``ck_salary_raises_one_method``).
        periods_per_year: The paychecks a year of the rhythm in force where
            the raise lands, an integral ``Decimal``.

    Returns:
        The raised pay, quantized to the cent (``ROUND_HALF_UP``).
    """
    if raise_obj.percentage:
        return round_money(pay * (1 + Decimal(str(raise_obj.percentage))))
    return round_money(
        pay + Decimal(str(raise_obj.flat_amount)) / periods_per_year,
    )


def _is_believed_in(raise_obj, year):
    """Whether *raise_obj* is believed to happen in *year*.

    **The one place this project answers that question.**  It was answered
    twice until an adversarial review of plan step salary:S3-c -- once as a
    clamp inside :func:`_applications` and once as a comparison inside
    :func:`get_raise_event`, sixty lines apart, agreeing because they were
    read together rather than because anything held them to each other.  Two
    spellings of one rule are two spellings whether or not they agree today
    (CLAUDE.md rule 14), and the money either side of a disagreement is a
    projected paycheck against the banner announcing it.

    Args:
        raise_obj: A raise exposing ``effective_date`` and ``terminal_year``.
        year: The calendar year being asked about.

    Returns:
        ``True`` when *year* falls on or after the raise's effective year and
        on or before the last year it is believed -- ``terminal_year`` of
        ``None`` meaning there is no last year.
    """
    if year < raise_obj.effective_date.year:
        return False
    terminal_year = raise_obj.terminal_year
    return terminal_year is None or year <= terminal_year


def _applications(raises, through):
    """Yield one entry per raise APPLICATION landing on or before *through*.

    The unit :func:`applications_between` orders by.  A recurring raise contributes
    one entry per year from its start through the last year whose landing
    day *through* has reached; a one-time raise contributes at most one.
    The counts are exactly those the per-raise loop this replaced produced --
    what changed is that they are interleaved by DATE rather than grouped by
    raise, and since plan step salary plan#3048 that each lands on its own
    month and day (ruling **plan#3090**) rather than on its month's 1st.  A
    recurring raise never starts on February 29
    (``ck_salary_raises_recurring_not_on_february_29``), so every year it
    repeats in has its day.

    Args:
        raises: The raise objects, as :func:`applications_between` documents them.
        through: The last day an application may land on.

    Yields:
        ``(landing date, method_rank, raise_obj)`` -- *method_rank* is 0 for
        a flat raise and 1 for a percentage one, which is how the
        documented flat-before-percentage order survives inside a single
        date (M-01 / deep-hunt #12).  A raise is exactly one method
        (``ck_salary_raises_one_method``) with a positive amount, so a
        truthy ``flat_amount`` uniquely marks the flat leg.
    """
    for raise_obj in raises:
        starts = raise_obj.effective_date
        method_rank = 0 if raise_obj.flat_amount else 1

        if raise_obj.is_recurring:
            # Recurring raises compound each year on their start's month and
            # day.  Whether each of those years is still BELIEVED is
            # :func:`_is_believed_in`'s question, asked here rather than
            # answered a second time.
            for year in range(starts.year, through.year + 1):
                landed = starts.replace(year=year)
                if landed <= through and _is_believed_in(raise_obj, year):
                    yield landed, method_rank, raise_obj
        elif starts <= through and _is_believed_in(raise_obj, starts.year):
            # One-time raise: it lands once, on its own start date, and
            # only if that date falls within the years it is believed for.
            yield starts, method_rank, raise_obj


def get_raise_event(raises, period, replaced_through=None):
    """Return a description of any raise event occurring in this period.

    Public because the paycheck engine's basis composes each paycheck's
    banner from it (:meth:`~app.services.payroll_basis.PayrollBasis
    .pay_event_on`).  Pure over *raises* and
    ``period.start_date`` -- no breakdown, no DB, no ``float``.

    **It takes the RAISE SET rather than the profile since plan step
    salary:S3-f-1** (ruling **R-SAL20**), for the same reason
    :func:`applications_between` does: the engine badges the event of the set it
    PRICED, which is its basis's and not necessarily the profile's rows, and a
    banner announcing a raise the paycheck beside it was not priced under is
    the two-walks-disagreeing defect the paragraph below records.  Its one
    caller is :meth:`~app.services.payroll_basis.PayrollBasis.pay_event_on`
    since plan step salary:X-av-3a; the cockpit route passed
    ``profile.raises`` here directly until then, and reads the engine's own
    banner now.

    **It badges only an application the paycheck is PRICED under since plan
    step salary:X-av-3a** (ruling **R-SAL84**): a forecast raise due on or
    before the payday of the pay entry a paycheck is priced from is replaced
    by that entry (:func:`applications_between`), so its banner would announce
    money that never arrives.  *replaced_through* is that entry's payday.

    Args:
        raises: The raise set -- each exposing what
            :func:`applications_between` documents PLUS ``raise_type_name``,
            which is what :class:`RaiseTerms` carries and what a
            :class:`~app.models.salary_raise.SalaryRaise` row exposes as a
            property; a falsy/empty set badges nothing.
        period: The pay period, read for ``start_date`` alone.
        replaced_through: The payday of the pay entry the period's paycheck
            is priced from; an application landing on or before it badges
            nothing.  ``None`` badges every application of the month.

    Returns:
        The comma-joined event labels for *period*, or ``""``.

    **It honours ``terminal_year`` as of plan step salary:S3-c**, which is an
    obligation that step INHERITED rather than created: plan step salary:S3-a
    added the field and named this function as the walk that did not know
    about it, so a recurring raise went on badging an event every year after
    its last believed one -- a banner on a paycheck whose gross the same field
    had already stopped moving.  **Both walks go through
    :func:`_is_believed_in`**, so they agree by construction rather than by
    being read together -- a draft of this paragraph claimed the weaker thing,
    and an adversarial review of this step took that admission as the finding
    it was.
    """
    if not raises:
        return ""

    period_year = period.start_date.year
    period_month = period.start_date.month
    events = []

    for raise_obj in raises:
        eff_month = raise_obj.effective_date.month
        eff_year = raise_obj.effective_date.year

        is_match = False
        if (raise_obj.is_recurring and period_month == eff_month
                and _is_believed_in(raise_obj, period_year)):
            # A recurring raise recurs at eff_month every year from
            # eff_year onward and stops after the last year it is believed,
            # matching _applications' gate at BOTH ends -- so it
            # must not badge an event in a calendar year before it takes
            # effect (deep-hunt #13) or after it is over (salary:S3-c).
            is_match = True
        elif eff_year == period_year and eff_month == period_month:
            # The one-time arm, which a terminated recurring raise cannot
            # fall through into: reaching here needs ``period_year ==
            # eff_year``, and ``ck_salary_raises_terminal_year_not_before_
            # effective`` guarantees a raise is still believed in its own
            # effective year.  A one-time raise carries no terminal year at
            # all (``ck_salary_raises_terminal_year_only_on_a_recurring_
            # raise``), so there is nothing to test here.
            is_match = True

        if is_match and replaced_through is not None:
            # The application this match announces lands on the raise's own
            # month and day in the period's year (a one-time raise's own
            # year is that year).
            is_match = raise_obj.effective_date.replace(year=period_year) > replaced_through
        if is_match:
            # The type's display name, ONE attribute on rows and values alike.
            # A ``raise_type is None`` arm stood here; a row's property now
            # resolves the name from ``raise_type_id`` through the ref cache,
            # which answers a never-flushed row too (the relationship does
            # not), so the fallback label "raise" that arm produced for such a
            # row is gone with it.
            raise_type = raise_obj.raise_type_name
            if raise_obj.percentage:
                pct = Decimal(str(raise_obj.percentage)) * 100
                events.append(f"{raise_type.upper()} +{pct}%")
            else:
                events.append(f"{raise_type.upper()} +${raise_obj.flat_amount:,.2f}")

    return ", ".join(events)

__all__ = [
    "EndYearError",
    "RAISE_END_MODES",
    "RAISE_END_MODE_NONE",
    "RAISE_END_MODE_YEAR",
    "RAISE_YEAR_MAX",
    "RAISE_YEAR_MIN",
    "RaiseTerms",
    "applications_between",
    "end_year_of",
    "get_raise_event",
    "raise_pay",
    "terms_of",
]
