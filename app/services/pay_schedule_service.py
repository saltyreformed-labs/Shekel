"""
Shekel Budget App -- Pay Schedule Service

Reads and writes the per-user ``budget.pay_schedule`` row -- the owner-level
configuration a schedule cannot derive from its own rows -- and the
``budget.pay_eras`` rows that hang off it: one era per *how I have been paid
since*, carrying the cadence (whose KIND is which parameter columns the row
holds, ruling **R-PC80**), the phase and the payday convention (plan step
``pay_calendar:C17-a``, ruling **R-PC58**).

**The RHYTHM is an ERA's fact and the ROW holds what is the owner's**, and the
doors here are split on that line.  An era's ``cadence_days``, ``shift_id``
and ``effective_from`` are what a pay CALENDAR is derived from, and
:func:`resolve_schedule` answers every era in ONE read as
:class:`ScheduleFacts`, the cadence and the convention of each travelling as
the :class:`~app.services.pay_rhythm.Rhythm` the derivation displaces under
and the opening bound beside them.  ``rolling_enabled`` and
``rolling_target_periods`` configure the on-request top-up and are read off
the row itself by the caller that is about to write; ``history_opens_on``
bounds the earliest era's backward rhythm and is the owner's because only that
era has one.

*Until ``C17-a`` the row held one ``cadence_days``, one ``shift_id`` and one
``nominal_anchor``, and* ``upsert_schedule`` *rewrote all three on every batch
that recorded a payday -- so "correct my cadence going forward" silently
re-described every PAST payday too (ledger row **N-492**), and the extend path
re-judged the stored pair on every ``/grid`` render by handing it back through
that door (**N-494**).  That door is gone.*  **This module READS the eras and
writes the schedule row; :mod:`app.services.pay_era_write` writes the eras**
-- the reader / writer split ``pay_period_service`` / ``pay_period_write``
already draws for the paydays (plan step C3-b), made for the same reason: an
era is minted by ``mint_era`` when a batch states a rhythm the era covering
its first payday does not hold, has its phase moved down in place by
``rephase_earliest_era`` when a batch records below the record (plan step
``pay_calendar:C18-b``), and is retired by ``retire_eras`` when a later batch
supersedes it.  The two refusals every door that STATES a rhythm asks (the
cadence bound and the cadence-convention pairing) live HERE, beside the
column bounds they state.

**:class:`~app.services.pay_rhythm.Rhythm` and :class:`~app.services.pay_rhythm.Era`
are DECLARED in a pure leaf and imported here** (plan step ``C14-e``,
extended at ``C17-a``).  Each is a frozen value with no session behind it,
and this module holds a session, so declaring them here would put them out
of reach of the derivation that must read them.  Rule 14's remedy for a leaf
a layer has misplaced is to move the leaf, not to mint a second one; see that
module for the whole argument.

**It no longer owns the advisory lock that serializes the structural
pay-period mutations** (plan step X-f1c3c).  That lock moved, unchanged
in key and namespace value, to
:mod:`app.services.user_write_lock` -- because the posting-ledger
reconciles need the SAME lock, not a second one: a reconcile derives each
correction's pay period from the owner's calendar, so a concurrent
truncate can delete the period it is filing under.  Two locks would also
be the only way this app could deadlock.  See that module for the whole
argument.

Flask-isolated -- takes and returns plain data, never imports
``request`` / ``session``.  Flushes so callers see assigned ids, but
never commits: the route layer owns the transaction.
"""

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import BusinessDayShiftEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.pay_era import (
    CADENCE_DAYS_MAX,
    CADENCE_DAYS_MIN,
    DAY_OF_MONTH_MAX,
    DAY_OF_MONTH_MIN,
    SEMI_MONTHLY_LOWER_DAY_MAX,
    PayEra,
)
from app.models.pay_period import PayPeriod
from app.models.pay_schedule import PaySchedule
from app.services.pay_rhythm import Era, FixedDays, Monthly, Rhythm, SemiMonthly
from app.utils.business_days import shortest_collision_free_cadence
from app.utils.dates import CALENDAR_DATE_MAX, CALENDAR_DATE_MIN

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduleFacts:
    """The facts a pay CALENDAR is built from: the owner's eras, and the bound.

    Plan step **balance:X-bh-2**, reshaped at **pay_calendar:C17-a**.  One
    value rather than several return types because they arrive from one load
    and are read by one consumer -- a
    :class:`~app.services.pay_calendar.PayCalendar` needs them all, and
    resolving them separately would be several queries of the same rows per
    calendar load, which is exactly the redundant-schedule-read defect ledger
    rows **P68** and **P69** record.

    **It carries the ERAS, not a rhythm** (ruling **R-PC58**).  Until
    ``C17-a`` it carried the schedule row's one cadence and one convention as
    a :class:`~app.services.pay_rhythm.Rhythm` beside a ``nominal_anchor``;
    a pay schedule is a SEQUENCE OF ERAS now, and this value is that sequence
    with the owner's history bound beside it.  :attr:`rhythm` still answers --
    the LATEST era's -- for the reader of the CURRENT rhythm, the cadence
    door (:func:`~app.services.pay_calendar.cadence_for`); the extend door
    read it too until plan step ``C17-c-2b``, where it stopped stating a
    rhythm.  The calendar itself takes the whole sequence since plan step
    ``C17-b-2``, every reader asking the era covering its own day.

    **It is the facts OF AN OWNER WHO HOLDS AN ERA, and cannot say
    otherwise** (ruling **R-PC45**'s principle, one relation over).  The
    tuple is NON-EMPTY: an owner with a schedule row and no era has stated no
    rhythm, and :meth:`of` answers ``None`` for them rather than a value whose
    :attr:`rhythm` would raise.  "This owner has no calendar at all" is
    therefore one optional -- :func:`resolve_schedule` answering ``None`` --
    rather than a value carrying an absence a reader must remember to test.

    **It carries the calendar facts and NOT the rolling ones.**
    ``rolling_enabled`` and ``rolling_target_periods`` configure a WRITE
    (the on-request top-up); these describe the owner's rhythm, which is
    what a calendar derives from.  A caller that needs the rolling half wants
    the row itself (:func:`get_schedule`), because it is about to write.

    Attributes:
        eras: The owner's :class:`~app.services.pay_rhythm.Era` values,
            ``effective_from`` ascending and never empty.  Every half of
            every era is a STORED value and never an inferred one; the arm
            that inferred a cadence closed findings **P8** and **P35** on its
            way out (see :func:`resolve_schedule`).
        history_opens_on: How far back this owner's paychecks reach, or
            ``None`` for NOT STATED (ruling **balance:R-IA**, amended
            2026-08-31) -- an absence rather than a claim, and one the
            backward rhythm answers by counting only the record.  It is the
            one optional here because the COLUMN is nullable; there is NO
            fallback for it, since the first recorded payday is a record
            boundary rather than an answer.
    """

    eras: "tuple[Era, ...]"
    history_opens_on: date | None

    @property
    def latest_era(self) -> Era:
        """Return the era with the greatest ``effective_from``.

        The era the schedule's plan ENDS on, whose rhythm is the owner's
        current one (:attr:`rhythm`).  *Until plan step ``C17-c-2b`` every
        extend and rolling top-up recorded ITS grid; they record the plan of
        the era covering the record now, which is this one only once the
        record reaches it (ruling **R-PC75**).*

        Returns:
            The last of :attr:`eras`.
        """
        return self.eras[-1]

    @property
    def rhythm(self) -> Rhythm:
        """Return the LATEST era's cadence and convention.

        **The one rhythm every calendar reader took at plan step ``C17-a``**,
        where the schedule row's own pair used to be -- which is what held
        that leaf at ``$0.00``: a single-era owner (every owner the migration
        backfills) read back exactly the values the row held.  Since
        ``C17-b-2`` the calendar takes :attr:`eras` whole and each reader asks
        the era covering its own day; what is left here is the CURRENT
        rhythm, for the cadence door (extend read it too until plan step
        ``C17-c-2b``, where it stopped stating a rhythm at all).  For a
        PIECEWISE owner it is the rhythm their most recent STATING batch
        declared -- the writer retires every era past the last surviving
        payday and mints from the batch's day, so the latest era is either
        that batch's mint or the era it continued at the same rhythm.

        Returns:
            :attr:`latest_era`'s :class:`~app.services.pay_rhythm.Rhythm`.
        """
        return self.latest_era.rhythm

    @classmethod
    def of(cls, schedule: PaySchedule) -> "ScheduleFacts | None":
        """Return the calendar facts carried by an existing schedule *row*.

        For a caller that already holds the row -- the rolling top-up, which
        reads it to decide whether to write at all and must not pay for a
        second read (finding **P70**).  A classmethod rather than attribute
        reads at that caller so WHICH columns are the calendar facts is stated
        once: a fact added to the era joins the value here, and the top-up
        inherits it without its author remembering.

        Args:
            schedule: The owner's ``budget.pay_schedule`` row, with its
                ``eras`` collection loaded or loadable.

        Returns:
            Its :class:`ScheduleFacts`, or ``None`` when the row holds no era
            -- an owner who has stated no rhythm, for whom there is no
            calendar to derive.

        Raises:
            ValidationError: An era names a ``shift_id``
                ``ref.business_day_shifts`` does not hold.  Stated once, at
                the one place a stored id becomes a member.
        """
        eras = tuple(_era_of(row) for row in schedule.eras)
        if not eras:
            return None
        return cls(eras=eras, history_opens_on=schedule.history_opens_on)


def _cadence_of(row: PayEra):
    """Return the cadence VALUE a stored *row*'s parameter columns state.

    **The kind is which columns are present** (plan step ``C17-d-2``, ruling
    **R-PC80**), read here and nowhere else -- the inverse of
    ``pay_era_write._COLUMNS_OF``, which is the one place a value becomes
    its columns.  ``cadence_days`` present is a
    :class:`~app.services.pay_rhythm.FixedDays` era (``ck_pay_eras_one_kind``
    keeps both month columns NULL beside it); otherwise the day the era
    means is ``nominal_day`` when the first month clamped it and
    ``effective_from``'s own day when it did not, and ``other_day`` present
    makes it a :class:`~app.services.pay_rhythm.SemiMonthly` pair, absent a
    :class:`~app.services.pay_rhythm.Monthly` day.  Every branch reads a
    column the CHECKs bound, so no refusal is needed here: there is no
    storable row this cannot read as a legal era.

    Args:
        row: A ``budget.pay_eras`` row.

    Returns:
        The cadence value.
    """
    if row.cadence_days is not None:
        return FixedDays(row.cadence_days)
    meant = row.nominal_day if row.nominal_day is not None else row.effective_from.day
    if row.other_day is not None:
        return SemiMonthly((meant, row.other_day))
    return Monthly(meant)


def _era_of(row: PayEra) -> Era:
    """Return the :class:`~app.services.pay_rhythm.Era` a stored *row* states.

    The storage boundary in the READ direction: the shift id becomes its
    member here and nowhere else, which is IDs-for-logic as the project means
    it -- no ``name`` string is ever compared -- and the cadence's kind is
    read off which parameter columns the row carries (:func:`_cadence_of`),
    since plan step ``C17-d-2`` dropped ``kind_id`` (ruling **R-PC80**).

    Args:
        row: A ``budget.pay_eras`` row.

    Returns:
        The era as a value.

    Raises:
        ValidationError: The row names a shift this application does not
            model.  Refused rather than read as ``none``: a missing
            convention would silently un-displace every projected payday,
            which is a wrong date rather than an error.  The foreign key
            admits only seeded ids, so reaching this means a ``ref`` table
            was changed under the application.
    """
    shift = ref_cache.business_day_shift_member(row.shift_id)
    if shift is None:
        raise ValidationError(
            f"user {row.user_id}'s pay era from "
            f"{row.effective_from.isoformat()} names business-day shift "
            f"{row.shift_id}, which this application does not model.  "
            f"Refused rather than read as 'none': a missing "
            f"convention would silently un-displace every projected payday, "
            f"which is a wrong date rather than an error.  "
            f"fk_pay_eras_shift_id admits only seeded ids, so reaching this "
            f"means ref.business_day_shifts was changed under the application."
        )
    return Era(
        effective_from=row.effective_from,
        rhythm=Rhythm(cadence=_cadence_of(row), shift=shift),
    )


def get_schedule(user_id: int) -> PaySchedule | None:
    """Return the user's pay-schedule row with its eras, or ``None`` when absent.

    **Absent means one thing, since plan step C4-b-2**: this user has never
    recorded a payday.  ``fk_pay_periods_schedule`` holds a pay period's owner
    to having a row here, so no schedule row IMPLIES no pay periods.  *Not the
    converse, and an adversarial review caught a draft of this paragraph
    asserting the equivalence: an owner may hold this row and zero periods,
    which is the state ``pay_period_admin.reset_pay_periods`` passes through.*
    Absence used to mean a second thing as well -- a legacy user with periods
    that predated this table -- and carrying an answer for that state is what
    findings **P8** and **P35** cost.

    **The eras ride in the same statement** (plan step ``C17-a``).  The row's
    rhythm moved to ``budget.pay_eras``, and a caller holding this row wants
    the rhythm too -- the rolling top-up reads it on every ``/grid`` render --
    so the collection is joined rather than lazily fetched, keeping the
    schedule read at the one query it was (ledger rows **P68**, **P69**).

    Callers that want the CALENDAR facts rather than the row (because they are
    about to derive, not to write) use :func:`resolve_schedule`.

    Args:
        user_id: The owning user's id.

    Returns:
        The user's :class:`PaySchedule`, or ``None``.
    """
    return (
        db.session.query(PaySchedule)
        .options(joinedload(PaySchedule.eras))
        .filter_by(user_id=user_id)
        .first()
    )


def _reject_out_of_range_fixed_days(cadence: FixedDays) -> None:
    """Refuse a day count ``ck_pay_eras_cadence_range`` would refuse.

    Args:
        cadence: The candidate.

    Raises:
        ValidationError: The count is outside 1..365.
    """
    if not CADENCE_DAYS_MIN <= cadence.days <= CADENCE_DAYS_MAX:
        raise ValidationError(
            f"Days between paydays must be between {CADENCE_DAYS_MIN} and "
            f"{CADENCE_DAYS_MAX}; got {cadence.days}."
        )


def _reject_out_of_range_day(day: int) -> None:
    """Refuse a day of the month no month has.

    Args:
        day: The candidate.

    Raises:
        ValidationError: *day* is outside 1..31.
    """
    if not DAY_OF_MONTH_MIN <= day <= DAY_OF_MONTH_MAX:
        raise ValidationError(
            f"A payday must be a day of the month, {DAY_OF_MONTH_MIN} to "
            f"{DAY_OF_MONTH_MAX}; got {day}."
        )


def _reject_out_of_range_monthly(cadence: Monthly) -> None:
    """Refuse a monthly cadence whose day no month has.

    Args:
        cadence: The candidate.

    Raises:
        ValidationError: The day is outside 1..31.
    """
    _reject_out_of_range_day(cadence.day)


def _reject_out_of_range_semi_monthly(cadence: SemiMonthly) -> None:
    """Refuse a semi-monthly pair ``ck_pay_eras_other_day`` would refuse.

    Args:
        cadence: The candidate.

    Raises:
        ValidationError: A day is outside 1..31, the two days are equal, or
            the lower exceeds 27 (both would clamp onto 28 February).
    """
    lower, upper = cadence.days
    _reject_out_of_range_day(lower)
    _reject_out_of_range_day(upper)
    if lower == upper:
        raise ValidationError(
            f"Twice a month needs two different days; got {lower} twice."
        )
    if lower > SEMI_MONTHLY_LOWER_DAY_MAX:
        raise ValidationError(
            f"The earlier of your two paydays must be day "
            f"{SEMI_MONTHLY_LOWER_DAY_MAX} or before; got {lower} and "
            f"{upper}, which both fall on February's last day."
        )


#: Each kind's bound at the write door, keyed by the value's class -- the
#: door-side twin of ``pay_calendar._eras._PARAMETER_CHECKS``, raising the
#: form error where that raises the package's.  A kind absent here is
#: refused by the lookup rather than written unbounded.
_RANGE_REFUSALS = {
    FixedDays: _reject_out_of_range_fixed_days,
    Monthly: _reject_out_of_range_monthly,
    SemiMonthly: _reject_out_of_range_semi_monthly,
}


def reject_out_of_range_cadence(cadence) -> None:
    """Refuse a cadence whose parameters the era table's CHECKs would refuse.

    **One implementation of the bound, two callers, and the second is why it
    is a function** (plan step X-ad-a).
    :func:`~app.services.pay_era_write.mint_era` is the one door that writes
    a cadence a row did not already hold (``budget.pay_eras.cadence_days``
    since plan step ``C17-a``; the schedule row's until then) and asks this
    immediately before writing, so no door can persist a value the CHECK
    refuses; ``pay_era_write.rephase_earliest_era`` writes back the row's own
    value at a new phase.
    ``registration_service.register_user`` asks
    it EARLIER -- in its up-front validation block, before the ``User`` row is
    added to the session -- because a registration that refuses halfway leaves
    a partly-built owner in a session whose only protection is that nobody
    commits it.  Neither caller may hold its own copy of the bound: two copies
    of a range are two chances for the schema tier, the service tier and the
    column to disagree.

    **It takes the cadence VALUE since plan step ``C17-d-1`` and dispatches
    on its kind since ``C17-d-2``** (ruling **R-PC79**), so the day-of-month
    kinds bring their own bounds to this one door rather than to a second
    one beside it: a fixed-days count is ``ck_pay_eras_cadence_range``'s
    1..365; a day of the month is 1..31; a semi-monthly pair is two
    distinct such days with the lower at most 27
    (``ck_pay_eras_other_day``).  Each bound reads the model's constants,
    which the pure package mirrors and a test holds equal.

    Args:
        cadence: The candidate cadence.

    Raises:
        ValidationError: The parameters fall outside the kind's bounds.
            The message names the offending value and the bound, so a
            surface can render it verbatim.
    """
    _RANGE_REFUSALS[type(cadence)](cadence)


def reject_shift_on_short_cadence(rhythm: Rhythm) -> None:
    """Refuse a displacing convention a cadence is too short to carry.

    **The pairing is what is refused, and it is refused HERE rather than by a
    CHECK constraint** (plan step **C14-b**, ruling **R-PC59**,
    developer 2026-09-05).
    A convention that moves a payday off a closed day is not injective: two
    nominal paydays closer together than the longest run of consecutive closed
    days displace onto ONE day, and
    ``pay_calendar._derive.derive_periods`` refuses a repeated payday outright,
    so the owner's whole calendar would raise rather than render.

    **Two reasons, and an earlier draft of this docstring claimed three.**
    An adversarial review of 2026-09-05 struck the third and corrected the
    premise of the second; the count is restated here rather than left to
    decay.

    1. **The floor is DERIVED.**  It is the longest closed run plus one, proved
       and computed by
       :func:`~app.utils.business_days.shortest_collision_free_cadence`, and it
       moves with the holiday set -- which is not fixed
       (``business_days.JUNETEENTH_FIRST_YEAR`` records the set changing once
       inside the window this application admits).  A CHECK expression must be
       IMMUTABLE, so a constraint could only freeze a copy where nothing can
       recompute it.
    2. **A constraint cannot name a FIELD.**  It arrives as an
       ``IntegrityError`` carrying a constraint name, where a form needs the
       message on the control the owner chose -- which is what
       :func:`~app.schemas.validation._pay_rhythm.validate_derivable_rhythm`
       supplies, and the same reason plan step X-ad-a moved the cadence bound
       out from behind ``ck_pay_schedule_cadence_range``.

    *The struck reason was "which id means ``none`` is seed data, so a CHECK
    would hard-code a ``ref`` id".  True of the naive spelling and defeasible:
    a pinned id verified by a migration-time assertion, or NULL standing for
    "no convention", both dodge it.  Each has its own cost -- and the first two
    reasons stand on their own -- but a defeasible reason is not a structural
    one and stating it as such overclaimed.*

    **What this placement does NOT buy, and a CHECK would not have bought
    either.**  The refusal is asked on WRITE, so neither it nor a frozen
    constraint sees a STORED row that a later holiday change made illegal --
    ``ADD CONSTRAINT`` does scan existing rows, but only when some migration
    re-adds it.  Nothing reconciles ``budget.pay_schedule`` today.

    **A floor RISE no longer surfaces on a read path** (plan step
    ``C17-a``, closing ledger row **N-494**).  The continue path used to
    re-judge the STORED pair -- ``pay_period_admin.extend_pay_periods`` handed
    it back through the schedule row's upsert -- and the rolling top-up
    reaches that path from ``routes/grid`` and ``routes/dashboard`` with no
    ``except ValidationError``, so an owner whose stored pair a later holiday
    change made illegal would have met a 500 on the two screens they use
    most.  A batch that CONTINUES an era writes no rhythm now, so this is
    asked only where a rhythm is STATED.  What a stored pair made illegal
    still meets is ``pay_calendar.covering_projection``'s refusal, which
    reaches the "Pay Calendar Unavailable" page rather than a bare 500; that
    nothing reconciles ``budget.pay_eras`` against a moved holiday set is
    ledger row **N-493**, still open.

    ``none`` displaces nothing, so it is legal at every cadence and returns
    before the floor is computed -- which is also why the walk behind that
    floor is paid only by an owner who actually chooses a convention.

    Args:
        rhythm: The :class:`Rhythm` the operation will LEAVE BEHIND, which is
            not necessarily the stored one -- a request that shortens the
            cadence and switches the convention off in one submission leaves a
            legal pair, and judging either half against the stored other half
            would refuse it.

    Raises:
        ValidationError: The cadence is below the floor and the convention is
            not ``none``.  The message names the floor, the offending cadence
            and the consequence, so a surface can render it verbatim.
    """
    if rhythm.shift is BusinessDayShiftEnum.NONE:
        return
    floor = shortest_collision_free_cadence()
    # The floor is asked of the era's SHORTEST GAP (ruling R-PC79), which
    # for a fixed-days cadence is its day count and for a day-of-month kind
    # the value's own closed form: every theorem the package leans on rests
    # on a displacement being shorter than the gap between two grid days,
    # never on the gap being constant.
    if rhythm.cadence.shortest_gap < floor:
        raise ValidationError(
            f"Paydays must be at least {floor} days apart when payroll "
            f"moves a payday off a weekend or holiday; got "
            f"{rhythm.cadence.shortest_gap} (paid {rhythm.cadence.phrase}).  "
            f"A shorter gap would land two paychecks on one day."
        )


def reject_out_of_range_history_opening(history_opens_on: date | None) -> None:
    """Refuse an opening ``ck_pay_schedule_history_opens_range`` would refuse.

    :func:`reject_out_of_range_cadence`'s sibling, written for the same reason
    and asked by the same two kinds of caller: :func:`set_history_opening`, the
    column's one writer, asks it immediately before writing, and
    ``registration_service.register_user`` asks it in its up-front validation block,
    before the ``User`` row exists.  A value outside the CHECK reaches the
    database as an ``IntegrityError`` 500 rather than as something a form can
    render, and an HTML date input accepts a five-digit-year typo, so this is
    the ordinary path rather than a defensive one.

    ``None`` passes, and it is the column's ordinary value: it means the
    owner has not stated a history, which the rhythm answers by counting only
    the recorded paydays (ruling **balance:R-IA**, amended 2026-08-31).

    Args:
        history_opens_on: The candidate opening day, or ``None``.

    Raises:
        ValidationError: The day falls outside
            :data:`~app.utils.dates.CALENDAR_DATE_MIN` ..
            :data:`~app.utils.dates.CALENDAR_DATE_MAX`.  The message
            names the offending day and both bounds so a surface can render it
            verbatim.
    """
    if history_opens_on is None:
        return
    if not CALENDAR_DATE_MIN <= history_opens_on <= CALENDAR_DATE_MAX:
        raise ValidationError(
            f"The day your paychecks started must fall between "
            f"{CALENDAR_DATE_MIN.isoformat()} and "
            f"{CALENDAR_DATE_MAX.isoformat()}; got "
            f"{history_opens_on.isoformat()}."
        )


def record_below_history(
    first_payday: date | None, history_opens_on: date | None,
) -> bool:
    """Return whether a record opening on *first_payday* starts below the stated history.

    **The ONE predicate for "a payday before the day the owner said their
    paychecks started"** (ruling **pay_calendar:R-PC104**).  Two refusals
    ask it and word it for their own door: :func:`reject_history_opening_after_payday`
    (a history STATED after the record's first payday) and
    ``pay_period_batch.reject_payday_before_history`` ("Add earlier
    paychecks" reaching below the stated history, plan step
    ``pay_calendar:C18-b``).  One predicate is what the ruling's "the same
    rule" means: the two doors can never admit different sets.

    Args:
        first_payday: The record's first payday, or the one a door would make
            first; ``None`` for an owner with no paydays.
        history_opens_on: The stated day, or ``None`` for not stated.

    Returns:
        ``True`` when both are present and the history opens after the
        payday.  Equality is not below: a history ON the first payday counts
        nothing under the record, which is an ordinary statement.
    """
    return (
        first_payday is not None and history_opens_on is not None
        and history_opens_on > first_payday
    )


def reject_history_opening_after_payday(
    history_opens_on: date | None, opening_payday: date | None,
) -> None:
    """Refuse an opening later than the first payday it is a floor below.

    **One rule, asked of two different sources**, which is why it is a function
    rather than an inline test at either.  ``registration_service.register_user`` asks
    it of the payday the sign-up form STATES, up front, before the ``User`` row
    is added -- that module's standing property, and the reason its
    pay-calendar checks all sit in one block.  :func:`set_history_opening` asks
    it of the payday the schedule RECORDS, because by then there is a schedule
    to read.  Two spellings of "your paychecks cannot have begun after your
    first one" would be two chances for the two doors to admit different sets.
    The test itself is :func:`record_below_history`, which a third door asks.

    Equality passes, and it is the ordinary answer for one whole class of
    owner: a floor ON the opening payday means "count nothing below the
    record", which is what somebody whose first payday has not happened yet
    states (ruling ``pay_calendar:R-PC14`` calls that an ordinary state).

    Args:
        history_opens_on: The candidate opening day, or ``None`` -- which
            passes, being the absence of a claim rather than a claim.
        opening_payday: The first payday to measure against, or ``None`` for an
            owner with no paydays at all -- which also passes, there being no
            rhythm for a floor to contradict.

    Raises:
        ValidationError: *history_opens_on* falls after *opening_payday*.  The
            message names both days, so a surface can render it verbatim.
    """
    if record_below_history(opening_payday, history_opens_on):
        raise ValidationError(
            f"Your paychecks cannot have started on "
            f"{history_opens_on.isoformat()}: that is after your first "
            f"payday, {opening_payday.isoformat()}.  Enter that day or an "
            f"earlier one, or leave it blank."
        )


def set_history_opening(
    user_id: int, history_opens_on: date | None,
) -> PaySchedule:
    """Store how far back this owner's paychecks reach.

    Plan step **balance:X-bh-2** (ruling **balance:R-IA**).  The ONE writer of
    ``history_opens_on``, for the two doors that ask the question:
    registration, which asks it beside the payday and cadence it already asks
    for, and the pay-periods settings section, which is where an owner corrects
    it or states it for the first time -- every owner who registered before
    this column existed holds ``NULL``, and ``NULL`` is not a state a sign-up
    form can revisit.

    **It is a door of its own rather than a field of the era**
    (:func:`~app.services.pay_era_write.mint_era`), and the lifecycles are
    why.  An era is minted by
    ``pay_period_write.record_paydays`` whenever a batch states a rhythm --
    a first schedule, a cadence corrected going forward -- and when a job
    began is not a fact a batch of paydays states: only the EARLIEST era runs
    backward below the record, so a floor per era would be a column with one
    meaningful row, and threading it through the mint would either restate
    the owner's answer on every new era or add a "leave this one alone"
    argument, which is the conditional-write shape ``set_rolling`` already
    avoids by being separate.

    **``None`` is a real value to write, not a skip.**  Clearing the field is
    how an owner WITHDRAWS a statement -- after which the engine counts only
    their recorded paydays again -- so this door stores what it is given.

    A schedule row must already exist, exactly as :func:`set_rolling` requires:
    the value bounds a rhythm, and an owner with no row has no cadence for a
    rhythm to run at.  Registration satisfies that by writing its paydays --
    and with them the schedule row -- before it calls here.

    Args:
        user_id: The owning user's id.
        history_opens_on: The day the owner's paychecks began, or ``None``
            to state nothing, which counts only the record.

    Returns:
        The updated :class:`PaySchedule` row, flushed.

    Raises:
        ValidationError: The user has no schedule row, the day falls outside
            the window ``ck_pay_schedule_history_opens_range`` admits, or it
            falls after the owner's first recorded payday.
    """
    reject_out_of_range_history_opening(history_opens_on)
    schedule = get_schedule(user_id)
    if schedule is None:
        raise ValidationError(
            "Generate a pay-period schedule before saying when your "
            "paychecks started."
        )
    # The owner's own paydays, not a calendar: this asks for ONE day and the
    # derivation would build every period to answer it.  ``min`` rather than
    # the lowest ``period_index``, because the floor is measured against the
    # earliest payday and the two agree only while the index is in date order
    # -- which is a stored column plan step C4-c dropped.
    reject_history_opening_after_payday(
        history_opens_on,
        db.session.query(func.min(PayPeriod.start_date))
        .filter(PayPeriod.user_id == user_id)
        .scalar(),
    )
    schedule.history_opens_on = history_opens_on
    db.session.flush()
    return schedule


def ensure_schedule_row(user_id: int) -> None:
    """Create the user's ``budget.pay_schedule`` row if none exists.

    **The row's one creator since plan step ``C17-a``.**  The row used to be
    created by the rhythm upsert the first time a batch recorded a payday;
    the rhythm is an era's now, and what a first batch still needs is the
    owner-level row for ``fk_pay_periods_schedule`` and
    ``fk_pay_eras_schedule`` to target.  ``INSERT ... ON CONFLICT DO
    NOTHING`` on ``uq_pay_schedule_user``, so a concurrent first-generation
    double-submit can never raise an ``IntegrityError`` 500 and an existing
    row's rolling configuration, stated history and ``created_at`` are never
    disturbed.

    Args:
        user_id: The owning user's id.
    """
    db.session.execute(
        pg_insert(PaySchedule.__table__)
        .values(user_id=user_id)
        .on_conflict_do_nothing(constraint="uq_pay_schedule_user"),
    )


def set_rolling(user_id: int, enabled: bool, target_periods: int) -> PaySchedule:
    """Update the user's continuous-rolling-window configuration.

    The settings-page setter for the rolling window: it flips
    ``rolling_enabled`` and stores the target period count on the user's
    existing schedule row.  Cadence is deliberately NOT touched here --
    it is owned by generate / regenerate.

    A schedule row must already exist.  The rolling window keeps a count
    of periods generated ahead, and growing the schedule needs a stored
    cadence to extend at; a user with no row has never generated a
    schedule, so there is nothing to roll forward.  Every user who has
    generated periods has a row -- the first generation upserts one, the
    Phase-1 backfill created one for every pre-existing user, and since plan
    step C4-b-2 ``fk_pay_periods_schedule`` makes it structural rather than
    historical -- so this guard only rejects the genuinely-not-set-up case.

    Args:
        user_id: The owning user's id.
        enabled: Whether continuous top-up is on.
        target_periods: How many current-and-future periods to keep
            generated ahead (>= 1; the count INCLUDES the current
            period).  Bounded to 1..260 by the caller's schema and to
            > 0 by ``ck_pay_schedule_positive_target``.

    Returns:
        The updated :class:`PaySchedule` row, flushed.

    Raises:
        ValidationError: The user has no schedule row (they must generate
            a schedule first).
    """
    schedule = get_schedule(user_id)
    if schedule is None:
        raise ValidationError(
            "Generate a pay-period schedule before configuring the "
            "rolling window."
        )
    schedule.rolling_enabled = enabled
    schedule.rolling_target_periods = target_periods
    db.session.flush()
    return schedule


def resolve_schedule(user_id: int) -> "ScheduleFacts | None":
    """Resolve the facts a pay calendar is derived from, in ONE read.

    Plan step **balance:X-bh-2**.  :func:`resolve_cadence`'s body, widened to
    the pair -- because :func:`app.services.pay_calendar.calendar_for` needs
    both and asking for them separately would query one row twice per calendar
    load, which is the redundant per-render schedule read ledger rows **P68**
    and **P69** record.

    **It reads the stored row and nothing else, since plan step C4-b-2**, which
    closed findings **P8** and **P35** by making the state the deleted arm
    existed for UNREPRESENTABLE.  That arm inferred a row-less owner's cadence
    from their last period's length -- ``(end_date - start_date).days + 1`` --
    and it was wrong in two ways at once.  It was CIRCULAR: since plan step
    C3-b :func:`app.services.pay_period_write.record_paydays` derives that same
    last end FROM this answer, so it read back the value it had produced and
    could be neither right nor wrong.  And it was unbounded ABOVE, where the
    stored cadence was bounded to 1..365 by the column's CHECK (then
    ``budget.pay_schedule.cadence_days`` under ``ck_pay_schedule_cadence_range``;
    ``budget.pay_eras.cadence_days`` under ``ck_pay_eras_cadence_range`` since
    plan step ``C17-a``): a hand-written period spanning more than a year
    inferred a cadence ``app.services.pay_calendar`` refuses, and since plan
    step C2-c that raise reaches every balance page as a bare 500.

    What makes the arm unreachable is ``fk_pay_periods_schedule``, the key
    :class:`~app.models.pay_period.PayPeriod` carries: a pay period's owner has
    a ``budget.pay_schedule`` row or the row cannot exist.  So the arm is
    DELETED rather than left standing over a state no database can hold -- an
    unreachable branch is a claim nothing grades, and the next reader cannot
    tell it from a live one.

    **``None`` is still a real answer, and since plan step C4-d it is the
    WHOLE answer rather than a pair of them** (ruling **R-PC45**).  Before, it
    meant "no schedule row AND no period to infer from"; then C4-b-2 narrowed
    it to "no schedule row", which the key makes sufficient -- such an owner has
    no pay periods either.  What this step changed is where that absence is
    SPELT.  It used to be a ``ScheduleFacts`` with both fields ``None``, so the
    absence of a row and the absence of a stated history shared one encoding
    and a THIRD pair -- no cadence beside a stated opening -- was constructible
    and unrepresentable.  Now the absence is this function's own return: there
    is no row, so there are no facts, so there is no value.

    That is an ordinary owner: a companion account
    (``routes/settings.companion_create`` writes neither), or any user before
    registration records their first batch.  The extend path reads it as
    "generate your first schedule first".

    **The two doors above the calendar refuse it and this one does not**, which
    is the split C4-d makes explicit rather than leaves to chance.  This is the
    SOFT door: it answers "does this owner have a schedule" and a caller decides
    what that means, which is what ``routes/salary/profiles.new_profile`` needs
    (task plan#3170) -- a FORM must not 500 on the state it points the owner to
    the fix for.  The HARD
    doors are :func:`app.services.pay_calendar.calendar_for` and
    :func:`app.services.pay_calendar.cadence_for`, which answer or raise
    ``PayCalendarError``, because every figure they feed is a per-paycheck one.
    Before C4-d the two calendar doors disagreed about this owner -- the cadence
    door refused and the calendar door quietly answered an EMPTY calendar
    carrying no cadence -- and that split is why some screens showed a repair
    page for them and others showed a blank one.

    **Since plan step ``C17-a`` it answers the owner's ERAS rather than one
    rhythm** -- every ``budget.pay_eras`` row as an
    :class:`~app.services.pay_rhythm.Era`, plus ``history_opens_on`` -- and
    still in one statement, since :func:`get_schedule` joins the eras onto the
    row, which is the property :class:`ScheduleFacts` was shaped to have.

    **``None`` widened by one state at that step**: an owner holding a
    schedule row and NO era has stated no rhythm and gets no facts, exactly as
    an owner with no row does.  No door in ``app/`` produces that owner --
    every batch that records a payday mints an era when none covers it -- and
    the migration backfills one era for every owner holding a payday, so the
    state is reachable only for a row whose paydays were all removed before
    the migration ran.

    **``history_opens_on`` never had a fallback and that asymmetry was the
    point.**  Nothing in ``budget.pay_periods`` says when a job began -- the
    first recorded payday is a record boundary, not an answer -- so an owner who
    HAS a row and has stated nothing carries ``None`` there, and it reads as
    exactly that (ruling **balance:R-IA**, amended 2026-08-31).  It is the one
    optional on :class:`ScheduleFacts`, because its column is.

    Args:
        user_id: The owning user's id.

    Returns:
        The :class:`ScheduleFacts`, or ``None`` when the user has no
        ``budget.pay_schedule`` row -- which by ``fk_pay_periods_schedule`` is
        an owner with no pay periods either -- or a row holding no era.

    Raises:
        ValidationError: An era names a ``shift_id``
            ``ref.business_day_shifts`` does not hold
            (:meth:`ScheduleFacts.of`, since plan step ``C14-e-1``).  **This
            does not make the door hard**, and the distinction is the one the
            paragraph above draws: the SOFT answer is about an owner with no
            schedule, which a form repairs, where a stored id this application
            cannot name is a ``ref`` table edited under the app and is not a
            state any form repairs.  Named here because every caller of this
            door now inherits it, where before it belonged to one scalar
            reader.
    """
    schedule = get_schedule(user_id)
    if schedule is None:
        return None
    return ScheduleFacts.of(schedule)


def resolve_cadence(user_id: int) -> "FixedDays | Monthly | SemiMonthly | None":
    """Resolve the cadence to continue the user's schedule with.

    :func:`resolve_schedule`'s cadence half, for the callers that need only
    that -- the extend and rolling paths, the writer's own re-read, and
    :func:`app.services.pay_calendar.cadence_for`.  It is a forward rather than
    a second implementation: plan step **balance:X-bh-2** widened the read to a
    pair, and leaving this reading the row itself would have been two answers
    to "what cadence does this owner have" the moment one of them changed.

    **It is the SOFT door's cadence half**, and since plan step C4-d that is
    what distinguishes it from :func:`app.services.pay_calendar.cadence_for`
    rather than how much of the schedule each reads.  Both cost one query of
    one row; this one ANSWERS ``None`` for an owner with no schedule row and
    that one REFUSES them.  **No application code calls it since task
    plan#3170**: the new-profile form, its last caller, reads
    :func:`resolve_schedule` whole, because it derives the owner's calendar
    from the facts (ruling **R-SAL97**: the count in force on its default
    payday, not the latest rhythm's).  The callers the paragraph above names
    no longer call it either; step plan#3505 deletes it.

    Args:
        user_id: The owning user's id.

    **It answers the LATEST era's cadence since plan step ``C17-a``**, which
    for every owner the migration backfills is the value the schedule row
    held; a reader that wants a PAST day's cadence is ``C17-b``'s.

    Returns:
        The STORED cadence as a value of its kind (plan step ``C17-d-1``),
        or ``None`` when the user has no ``budget.pay_schedule`` row -- since
        plan step C4-b-2 the same statement as "no pay periods"
        (``fk_pay_periods_schedule``) -- or no era.  The extend path treats
        ``None`` as "generate your first schedule first".
    """
    facts = resolve_schedule(user_id)
    return None if facts is None else facts.rhythm.cadence
