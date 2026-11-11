"""
Shekel Budget App -- Pay Era Writer

**The ONE place in ``app/`` that changes ``budget.pay_eras``** (plan step
``pay_calendar:C17-a``, ruling **R-PC58**).  An era is one row per *how I have
been paid since* -- the rhythm a span of paydays runs on, kept when the next
span starts on a different one -- and every batch that records a payday
reaches this table through :func:`mint_era`, :func:`retire_eras` and, for
the two changes that move the record's FIRST payday,
:func:`rephase_earliest_era` (plan steps ``pay_calendar:C18-b`` and ``C21``),
called from ``pay_period_write._apply`` and from nothing else.  The ERA RULE that
decides what a batch does here is stated in the same module, in three
parts: :func:`eras_describing` says which eras a batch leaves standing,
:func:`era_to_mint` says whether it states a new one, and
:func:`era_to_move` says where a removal of the record's first paydays
moves the earliest one (plan step ``pay_calendar:C21``).

**Why a module of its own.**  :mod:`app.services.pay_schedule_service` reads
the eras (as :class:`~app.services.pay_schedule_service.ScheduleFacts`) and
writes the schedule row; this module writes the eras.  That is the reader /
writer split ``pay_period_service`` / ``pay_period_write`` already draws for
the paydays (plan step C3-b), and it was made when the era doors took that
service past pylint's 1,000-line ceiling -- a split rather than a trim, on
the terms ledger row **PC-498** records.  The two refusals both writers ask
(the cadence bound, the cadence-convention pairing) stay in the service beside
the column bounds they state, and this module imports them.

**A caller holding a loaded ``PaySchedule`` must re-read it after any of
these doors**: ``PaySchedule.eras`` is view-only, so an insert reaches no loaded
collection, the bulk delete synchronises nothing, and a joined load does not
replace a collection the identity map already holds (measured 2026-09-11).
``pay_period_write._apply`` expires the session after it; any other caller
expires the row (``db.session.expire``) before it reads the eras again.

Flask-isolated -- takes and returns plain data, never imports ``request`` /
``session``.  Flushes so callers see assigned ids, but never commits: the
route layer owns the transaction.
"""

from dataclasses import dataclass
from datetime import date

from app import ref_cache
from app.exceptions import PayPeriodRemovalRefused, ValidationError
from app.extensions import db
from app.models.pay_era import PayEra
from app.services import pay_schedule_service
from app.services.pay_calendar import (
    cadence_steps_to,
    first_payday_of,
    nominal_payday,
    opening_rephase,
    validate_eras,
)
from app.services.pay_rhythm import (
    Era,
    FixedDays,
    Monthly,
    Rhythm,
    SemiMonthly,
    era_covering,
)


def _fixed_days_columns(_effective_from: date, cadence: FixedDays) -> dict:
    """Return the parameter columns a fixed-days era stores: its day count.

    The phase is taken for the table's one signature and read for nothing:
    a fixed-days grid stores no fact beyond the day count.

    Args:
        cadence: The value.

    Returns:
        ``{"cadence_days": days}``.
    """
    return {"cadence_days": cadence.days}


def _nominal_day_of(effective_from: date, day: int) -> "int | None":
    """Return what ``nominal_day`` records for a meant *day* on *effective_from*.

    The day the era MEANS when the anchor's month could not hold it, else
    ``None`` -- ``recurrence:R-R3``'s rule, on this table's anchor: the
    column is present exactly when the date lost the intent, so absence has
    one meaning and ``ck_pay_eras_nominal_day`` can tie presence to the
    clamp.  A meant day BELOW the anchor's own day is not a clamp but an
    anchor off its grid, which :func:`reject_phase_off_grid` refuses before
    this is asked for a minted era, and :func:`_on_grid` -- the stronger
    question, which implies it -- before a moved phase's columns are.

    Args:
        effective_from: The era's phase, a day on its grid.
        day: The day of the month the era means at that anchor.

    Returns:
        *day* when it exceeds the anchor's day, else ``None``.
    """
    return day if day > effective_from.day else None


def _monthly_columns(effective_from: date, cadence: Monthly) -> dict:
    """Return the parameter columns a monthly era stores: ``nominal_day`` alone.

    Args:
        effective_from: The era's phase.
        cadence: The value.

    Returns:
        ``{"nominal_day": ...}``, ``None`` when the phase carries the day.
    """
    return {"nominal_day": _nominal_day_of(effective_from, cadence.day)}


def _semi_monthly_columns(effective_from: date, cadence: SemiMonthly) -> dict:
    """Return the parameter columns a semi-monthly era stores.

    The anchor stands for ONE member of the pair
    (:meth:`~app.services.pay_rhythm.SemiMonthly.member_of`, the value's one
    reading of its position, which the grid numbers half-months from too)
    and ``other_day`` is the member it does not stand for, with
    ``nominal_day`` recording the meant member only when the anchor's month
    clamped it.

    Args:
        effective_from: The era's phase.
        cadence: The value.

    Returns:
        ``{"nominal_day": ..., "other_day": ...}``.
    """
    meant_member = cadence.member_of(effective_from)
    meant, other = cadence.days[meant_member], cadence.days[1 - meant_member]
    return {
        "nominal_day": _nominal_day_of(effective_from, meant),
        "other_day": other,
    }


#: The parameter columns each cadence KIND stores, keyed by the value's class
#: (plan step ``C17-d-2``, ruling **R-PC80**): the ONE place a value becomes
#: its columns, as :func:`~app.services.pay_schedule_service._era_of` is the
#: one place the columns become a value.  A kind absent here is refused by
#: the lookup rather than written as the wrong one.
_COLUMNS_OF = {
    FixedDays: _fixed_days_columns,
    Monthly: _monthly_columns,
    SemiMonthly: _semi_monthly_columns,
}


def reject_phase_off_grid(effective_from: date, cadence) -> None:
    """Refuse an era whose first payday is not on its own cadence's grid.

    **The one refusal a day-of-month kind adds to the write door** (plan
    step ``C17-d-2``).  An era's ``effective_from`` is its first NOMINAL
    payday, so its grid passes through it by definition -- which a
    fixed-days grid does for any day, and a monthly or semi-monthly grid
    does only for its stated day(s): ``Monthly(15)`` from the 10th, or a
    1st/15th era from the 3rd, names a rhythm and a first payday that
    contradict each other.  The predicate is the grid's own round trip at
    step zero, dispatched on the kind, so nothing here restates what a
    month grid passes through.

    **Asked at every write, and the storage cannot catch what the write's
    ask refuses.**  ``pay_period_write.record_paydays`` asks it in its
    precondition block, before ``pay_period_batch.requested_paydays`` spaces
    the batch from the stated day -- on an off-grid anchor that batch's
    first element would not be the day the owner stated.  :func:`mint_era`
    asks it again immediately before the write, as it asks the cadence
    bound and the pairing, so no door can persist the state.
    :func:`rephase_earliest_era` asks the STRONGER question instead --
    whether the phase is a day of the earliest era's own grid
    (:func:`_on_grid`) -- which implies this one for every kind and which
    this function cannot ask: a fixed-days grid anchored at ANY day passes
    through it.  The CHECKs
    see only half of it: a meant day ABOVE the anchor's would be written
    as ``nominal_day`` and refused as not a clamp, but ``Monthly(5)`` from
    the 10th writes ``nominal_day = NULL`` and is storable as "monthly on
    the 10th" -- a wrong rhythm rather than an error, which is why the
    refusal is the writer's and not the schema's.

    Args:
        effective_from: The stated first nominal payday.
        cadence: The stated cadence, already bounded by
            :func:`~app.services.pay_schedule_service.reject_out_of_range_cadence`.

    Raises:
        ValidationError: The grid does not pass through *effective_from*.
            The message names the grid's paydays either side of the typed
            day -- the grid's own contract, ``payday(steps_to(d)) <= d <
            payday(steps_to(d) + 1)``, so a 1st/15th owner who typed the
            3rd is told the 1st AND the 15th -- so a form can render it
            against the payday control.
    """
    if nominal_payday(effective_from, cadence, 0) != effective_from:
        before = cadence_steps_to(effective_from, cadence, effective_from)
        raise ValidationError(
            f"A first payday of {effective_from.isoformat()} is not a day "
            f"you are paid on when paid {cadence.phrase}; on that rhythm the "
            f"paydays either side of it are "
            f"{nominal_payday(effective_from, cadence, before).isoformat()} "
            f"and "
            f"{nominal_payday(effective_from, cadence, before + 1).isoformat()}."
            f"  Enter one of those, or state the rhythm that pays on "
            f"{effective_from.isoformat()}."
        )


def _on_grid(era: Era, day: date) -> bool:
    """Return whether *day* is a NOMINAL day on *era*'s own grid.

    The grid's own round trip: the grid day
    :func:`~app.services.pay_calendar.cadence_steps_to` names for *day* is
    *day* itself.  ONE spelling for the two questions this module asks of an
    era's grid -- whether a batch's first payday continues the era covering
    it (:func:`era_to_mint`, whose docstring carries why the test is the
    round trip rather than a modulo) and whether a phase move keeps the
    earliest era on the grid it had (:func:`rephase_earliest_era`).
    :func:`reject_phase_off_grid` is a different question -- whether a grid
    anchored AT a day passes through it, which a fixed-days grid does for
    every day -- and cannot stand in for this one.

    Args:
        era: The era whose grid is asked.
        day: A nominal day.

    Returns:
        ``True`` when *era*'s grid passes through *day*.
    """
    cadence = era.rhythm.cadence
    return day == nominal_payday(
        era.effective_from, cadence,
        cadence_steps_to(era.effective_from, cadence, day),
    )


def mint_era(user_id: int, era: Era) -> PayEra:
    """Record that the owner has been paid on *era*'s rhythm since its day.

    **The ONE door that INSERTS into ``budget.pay_eras``** (plan step
    ``C17-a``, ruling **R-PC58**).  The table's one writer is this MODULE,
    in three functions: this one inserts an era, :func:`rephase_earliest_era`
    moves the earliest era's phase along its own grid in place, and
    :func:`retire_eras` deletes.  Called by ``pay_period_write.record_paydays`` when a batch
    states a rhythm the era covering its first payday does not already hold
    -- a first schedule, a cadence or convention changed going forward, or a
    phase off the covering grid -- and by nothing else: a batch that continues
    an era mints nothing, which is what closed the read-path re-judging
    ledger row **N-494** recorded, and the batch that records BELOW the
    record moves the earliest era's phase in place
    (:func:`rephase_earliest_era`) rather than minting it again.

    **The refusals live HERE, at the column's writer** (plan step X-ad-a's
    placement, carried over).  The cadence bound, the cadence-convention
    pairing and -- since plan step ``C17-d-2`` -- the phase's place on its
    own grid (:func:`reject_phase_off_grid`) are asked immediately before
    the write, so no door can persist what ``ck_pay_eras_cadence_range``,
    the collision floor or the month kinds' CHECKs refuse; every caller that
    takes the values from a form asks the same functions earlier so the
    refusal lands on the control the owner chose.

    **It writes the era as ONE row, and the pairing is why rather than
    tidiness.**  ``shift_id`` is legal only on a cadence longer than the
    longest run of closed days
    (:func:`~app.services.pay_schedule_service.reject_shift_on_short_cadence`),
    so the two columns carry a joint rule; written by two doors in sequence the
    row would pass through an intermediate state that is not the one the
    request means.  One statement judged against the state the operation
    LEAVES BEHIND has no such hole -- the same principle
    ``pay_period_write._PaydayChange`` exists for one module over.

    **The owner's ``budget.pay_schedule`` row must already exist**
    (``fk_pay_eras_schedule``): ``pay_period_write._apply`` calls
    :func:`~app.services.pay_schedule_service.ensure_schedule_row` first, and
    no other caller exists.

    Args:
        user_id: The owning user's id.
        era: The :class:`~app.services.pay_rhythm.Era` to persist.  Its
            ``effective_from`` must not equal an existing era's
            (``uq_pay_eras_user_effective_from``).  The writer holds that
            two ways before calling here: it retires every era whose first
            payday falls after the last surviving payday, and it bounds a
            minting batch at its era's first payday (``pay_period_batch``'s
            floor, since plan step ``C17-b-2``) -- so an era restated from a
            day the record already holds is REFUSED at the door rather than
            colliding on the key.  *Until then this sentence claimed the
            writer retired every era "taking effect on or after" the mint's
            day, which it never did; a rebuild from an existing era's day
            with a changed convention and every lower payday held reached
            the key as an IntegrityError.*  **That floor argument needs the
            record to stand on the earliest era's first grid step or
            above**, which is why the earlier door moves the phase down
            (:func:`rephase_earliest_era`, **R-PC105**): with only earlier
            paychecks kept below an unmoved phase, the floor IS that phase,
            and a rebuild from it with a new rhythm reached this key.  The
            removal of the first paydays moves it UP (**R-PC110**), and not
            for this argument: a record standing ABOVE the phase already
            satisfies it (the migrated era's shape).  It moves so the phase
            keeps naming the first paycheck the owner holds, and so a
            removal undoing an add leaves the era as the add found it.

    Returns:
        The new :class:`~app.models.pay_era.PayEra` row, flushed.

    Raises:
        ValidationError: The cadence's parameters fall outside its kind's
            bounds
            (:func:`~app.services.pay_schedule_service.reject_out_of_range_cadence`),
            the pair is one no calendar can derive
            (:func:`~app.services.pay_schedule_service.reject_shift_on_short_cadence`),
            or the era's first payday is off its own grid
            (:func:`reject_phase_off_grid`).  A 400 rather than a 500:
            every door in front of this one takes the values from a form.
    """
    cadence = era.rhythm.cadence
    pay_schedule_service.reject_out_of_range_cadence(cadence)
    pay_schedule_service.reject_shift_on_short_cadence(era.rhythm)
    reject_phase_off_grid(era.effective_from, cadence)
    # The one place a rhythm's convention becomes an id and its cadence
    # becomes its parameter columns, which is the storage boundary and
    # nowhere else -- ``recurrence._authoring`` resolves the same vocabulary
    # at the same moment for the same reason.
    row = PayEra(
        user_id=user_id,
        effective_from=era.effective_from,
        shift_id=ref_cache.business_day_shift_id(era.rhythm.shift),
        **_COLUMNS_OF[type(cadence)](era.effective_from, cadence),
    )
    db.session.add(row)
    db.session.flush()
    return row


@dataclass(frozen=True)
class EarliestRephase:
    """A change's era write when it moves the EARLIEST era's phase in place.

    The other value ``pay_period_write._PaydayChange.era`` can hold beside an
    :class:`~app.services.pay_rhythm.Era` to MINT, so a batch that does one
    cannot also do the other: the change carries ONE era write or none, and
    the type says which (review 2 of plan step ``pay_calendar:C18-b``).

    **It carries EVERY era as read, not the earliest alone, since plan step
    ``pay_calendar:C21``.**  A move DOWN can collide with nothing, so the
    earliest era was all the writer needed; a move UP (ruling **R-PC110**)
    can reach the next era's day, pass it, or leave the earliest era paying
    nothing before it, and the writer can judge that only against the eras
    after it -- which it must not read a second time (rule 14).

    Attributes:
        eras: The owner's eras AS THE DOOR READ THEM, in the same operation
            and under the same lock, ``effective_from`` ascending.  The
            first is the era the move keeps the rhythm of and the row it
            moves; the rest are what the moved phase is judged against.
        phase: The earliest era's new ``effective_from``: a NOMINAL day on
            its own grid -- below the current one when paydays are recorded
            below the record, above it when the record's first paydays are
            retired.
    """

    eras: "tuple[Era, ...]"
    phase: date


def rephase_earliest_era(user_id: int, rephase: EarliestRephase) -> None:
    """Move the owner's EARLIEST era's phase along its own grid, its rhythm untouched.

    **The era write of the two changes that move the record's first
    payday.**  "Add earlier paychecks" records paydays below the record
    (plan step ``pay_calendar:C18-b``, ruling **R-PC105**), and the era that
    pays them is the earliest one (**R-PC66**), so its phase moves DOWN to
    the grid day of the earliest new payday (``pay_calendar.earlier_paydays``
    computes it).  "Remove earlier paychecks" retires the first paydays
    (plan step ``C21``, ruling **R-PC110**), and the phase moves UP to the
    grid day of the payday left first (``pay_calendar.opening_rephase``).
    Either way the same rhythm on the same grid, so every payday it plans
    is unchanged, and the record's first payday stands for the era's first
    grid step again.  Left below the
    phase, a regenerate keeping only earlier paychecks could restate a
    rhythm from the old phase (a second era on
    ``uq_pay_eras_user_effective_from``) or from inside the next paycheck
    (an era ``pay_calendar._derive.validate_eras`` refuses on every read);
    left under a record whose first paydays are gone, the era's "since"
    would name a paycheck the owner no longer holds.

    **An UPDATE of one row rather than a retire and a mint, and review 1 of
    C18-b is why.**  :func:`mint_era` re-asks the cadence bound and the
    cadence-convention pairing, which judges a rhythm the door never
    stated: an owner whose stored pairing a later holiday-set change made
    illegal (ledger row **N-493**) was refused by a door that states no
    rhythm -- the principle that closed **N-494** -- and refused after the
    retire's DELETE had run.  A phase move changes the row's
    ``effective_from`` and the parameter columns read against it (a clamped
    month day's ``nominal_day``, which member of a semi-monthly pair the
    anchor stands for) and nothing else.  The rhythm those columns are
    written from is the stored one the door read (``rephase.eras[0]``), so
    ``cadence_days`` is written back with the value the row already holds.

    **It moves the phase to a day on the SAME grid that leaves a sequence
    the calendar can derive, and refuses anything else** (reviews 2 and 3
    of C18-b; plan step C21).  SAME GRID: "every payday it plans is
    unchanged" holds only for a phase the era's grid already passes through
    (:func:`_on_grid`).  A phase off it -- the new first payday's DISPLACED
    cash day under ``prior`` or ``next`` rather than its nominal day --
    re-phases the grid and moves every planned payday for good, and
    :func:`reject_phase_off_grid` cannot see that for a fixed-days era; the
    same-grid question implies it for every kind, so it is not asked beside
    it.  A DERIVABLE SEQUENCE: moved down, the earliest era cannot reach a
    later era's day, pass it, or be left paying nothing (ruling
    **R-PC75**); moved UP it can do all three, and those are three of the
    states ``pay_calendar.validate_eras`` refuses.  The move is asked of
    that validator over the sequence it would leave -- the calendar's one
    statement of which sequences derive, rather than a second spelling of
    three of its clauses here -- so it refuses a move up past what the
    earliest era can pay, and admits every move down as it did.  The
    column writer holds both refusals, so neither rests on the caller.

    **Why the two refusals raise different errors.**  The grid refusal is a
    ``ValidationError`` because C18-b made it one and tests name its text.
    The sequence refusal is the validator's own ``PayCalendarError``, and it
    is unreachable from both doors: the earlier door's phase is below the
    current one, and the removal door moves the phase only onto a paycheck
    the earliest era pays (``pay_calendar.opening_rephase`` answers
    ``None`` otherwise and the door refuses first, ruling **R-PC110**).  A
    caller that reaches it built the move by hand, and the recovery page
    that error reaches is the loud answer for that.

    **What it does NOT check, and why nothing needs to** (review 4 of C18-b;
    plan step ``balance:X-bn``).  The UPDATE is keyed on the phase the door
    read, and nothing checks that it moved a row, because no other write can
    move that phase in between: every era writer runs in a signed-in
    request's command transaction, which takes the owner's write lock before
    it reads any of the owner's data (:mod:`app.db_transaction`) -- the
    first-schedule generate route included, which ledger row **P71** recorded
    as taking none -- or in ``registration_service.register_user``'s (from
    ``/register`` and ``scripts/seed_user.py``), whose new user no other
    transaction can see until it commits.  Two migrations write the table
    too -- ``6fc77e86d76f`` inserts the eras and ``3ec5291ca4e2`` updates
    them -- and the deploy runs them (``scripts/init_database.py``, in the
    migrate service) while no web server serves: a migration arrives only
    with a new image, and an ``up`` that moves it stops the web server's old
    container before the deploy starts and starts the new one after it
    (:mod:`app.services.user_write_lock` states that order and what would
    break it; ruling plan#3534).
    *Until that step a first-schedule
    generate that read an empty record before a first schedule committed,
    and retired every era after this door's read, would have left the UPDATE
    matching nothing: the earlier door's paydays below an unmoved phase --
    the state ruling R-PC105 exists to prevent -- or the removal's record
    above one, a legal shape that has lost only R-PC110's "since".  The root
    was P71's missing lock, and a row count here would have routed around it
    rather than closed it.*

    Args:
        user_id: The owning user's id.  They hold at least one era -- the
            door that calls this has read their calendar.
        rephase: The eras as read, and the earliest one's new phase
            (:class:`EarliestRephase`).

    Raises:
        ValidationError: The phase is not a day of the earliest era's own
            grid (:func:`_on_grid`).  Both doors hand a nominal grid day, so
            neither reaches it; it is the column writer's own precondition,
            asked as :func:`mint_era` asks its own.
        PayCalendarError: The moved era would leave a sequence
            ``pay_calendar.validate_eras`` refuses -- a phase on or past the
            next era's, or one that leaves the earliest era no planned
            payday before it.  Unreachable from both doors (above).
    """
    eras, phase = rephase.eras, rephase.phase
    earliest = eras[0]
    cadence = earliest.rhythm.cadence
    if not _on_grid(earliest, phase):
        raise ValidationError(
            f"The earliest pay era cannot move from "
            f"{earliest.effective_from.isoformat()} to {phase.isoformat()}: "
            f"that day is not on the era's grid, so every payday it plans "
            f"would move with it."
        )
    validate_eras((Era(effective_from=phase, rhythm=earliest.rhythm), *eras[1:]))
    db.session.query(PayEra).filter(
        PayEra.user_id == user_id,
        PayEra.effective_from == earliest.effective_from,
    ).update(
        {"effective_from": phase, **_COLUMNS_OF[type(cadence)](phase, cadence)},
        synchronize_session=False,
    )


def eras_describing(
    stored: "pay_schedule_service.ScheduleFacts | None",
    surviving_paydays: "set[date]",
) -> "tuple[Era, ...]":
    """Return the eras a batch leaves standing: those with a surviving payday.

    The era rule's first half, beside its second (:func:`era_to_mint`).  An
    era pays from its FIRST PAYDAY
    (:func:`~app.services.pay_calendar.first_payday_of`, its
    ``effective_from`` displaced under its own convention), so one whose
    first payday falls after the last surviving payday describes nothing the
    batch keeps -- the earliest era excepted, which runs backward; the
    batch's new paydays all fall past that day (``pay_period_batch``'s floor)
    and take their era from the mint decision.
    ``retire_eras`` is handed this set, so what is judged against and what
    survives are one set.

    **The bound is the era's first PAYDAY and not its nominal
    ``effective_from``, since plan step ``C17-b-2``** (ledger row
    **PC-510**).  An era minted on a nominal closed day under ``prior`` pays
    its first paycheck BEFORE its own ``effective_from``, so compared on the
    nominal day it described no surviving payday the moment that paycheck
    was its only one, and the next batch retired it and re-minted it a
    cadence late; under ``next`` the mirror kept an era whose only paycheck
    a truncate had removed, beside a new era paying the same day.  The
    readers place a payday in cash days (:func:`~app.services.pay_calendar.era_index_at`),
    and the writer now agrees with them.

    **The EARLIEST era stands whenever any payday does, since plan step
    ``C17-c-2a``** (its adversarial review), and that is the readers' rule
    too: ruling **R-PC66** has the earliest era run BACKWARD below the
    record, so a surviving payday before its first payday is one it
    describes, and :func:`~app.services.pay_calendar.era_index_at` places
    such a day in it.  The state is a migrated one: ``C17-a``'s backfill
    minted an owner's era at their opening payday less the anchor gap under
    the convention the row held THEN, and an owner who opened on a closed
    day under ``none`` and later corrected to ``next`` holds an era whose
    first payday falls two days after their first record.  Truncated to
    that opening period they stood with NO era on the first-payday test:
    the next batch retired their only era and re-minted it from the day it
    continued, and the ceiling (``pay_period_batch.reject_skipped_paycheck``)
    had no plan to read.  They continue their era now, as every other owner.

    Args:
        stored: The owner's schedule facts before the batch, or ``None``.
        surviving_paydays: The paydays the batch leaves standing.

    Returns:
        The surviving eras, ``effective_from`` ascending; empty when nothing
        survives or the owner holds no era, NON-EMPTY otherwise.
    """
    if stored is None or not surviving_paydays:
        return ()
    latest = max(surviving_paydays)
    return tuple(
        era for index, era in enumerate(stored.eras)
        if index == 0 or first_payday_of(era) <= latest
    )


def era_to_mint(
    eras: "tuple[Era, ...]",
    first_payday: date,
    rhythm: Rhythm,
) -> "Era | None":
    """Return the era a batch states, or ``None`` when it continues one.

    **The era rule's decision, and the whole of it** (plan step
    ``pay_calendar:C17-a``).  Asked by ``pay_period_write.record_paydays``
    of the facts it read for the floor, and answered HERE because whether a
    rhythm is a new era is the era's own question, beside the door that mints
    one.  A batch mints an era at its first payday when

    1. the owner holds no era -- a first schedule;
    2. the era covering that day runs on a DIFFERENT rhythm -- a cadence or a
       convention corrected going forward, which is the state ledger row
       **N-492** records the old single row could only overwrite; or
    3. the day is OFF the covering era's grid -- a phase corrected going
       forward, which ``regenerate`` expresses by a corrected first payday at
       the same cadence.

    A batch whose first payday sits on the covering era's grid at that era's
    rhythm CONTINUES it and mints nothing.  **Extend and the rolling top-up
    do not reach this question at all since plan step ``C17-c-2b``**: they
    state no rhythm, so they go through
    ``pay_period_write.continue_paydays``, which records the paydays the
    stored eras already plan and mints and retires nothing.  *Until then the
    extend door restated the LATEST era from the horizon as a stated batch,
    and an owner truncated below that era's day had it retired and re-minted
    from the day the extend continued (ledger row **PC-509**); the door that
    replaced it materialises the plan of the era COVERING the record.*

    **The grid test is the GRID's own round trip on the NOMINAL day, which
    is what *first_payday* is** (the writer's own reading of it): a day is
    on the covering era's grid exactly when
    :func:`~app.services.pay_calendar.nominal_payday` at
    :func:`~app.services.pay_calendar.cadence_steps_to`'s answer lands back
    on it.  Until plan step ``C17-d-1`` this was spelled here a second time
    as ``(first_payday - effective_from).days % cadence_days == 0`` -- the
    same arithmetic under another name, and one that only a fixed-days grid
    can be asked in.  Asking the grid is what let the day-of-month kinds
    inherit the test at ``C17-d-2`` with nothing written here.  The two
    spellings agree on every integer input (Python's ``//`` and ``%`` share
    one divmod identity, brute-forced over a million pairs at the review);
    what differs is the edge of the ``date`` type itself -- a first payday
    within a cadence of ``date.min`` overflows the grid's ``timedelta`` add
    where the modulo answered ``False`` -- which no door reaches, every
    payday field being bounded at ``CALENDAR_DATE_MIN``.

    Args:
        eras: The eras the batch LEAVES STANDING, ``effective_from``
            ascending -- the owner's eras less those the batch supersedes,
            which ``pay_period_write.record_paydays`` computes from the
            surviving paydays.  Empty for a first schedule, and for a batch
            that leaves no payday standing.
        first_payday: The batch's first nominal payday.
        rhythm: The rhythm the batch states.

    Returns:
        The :class:`~app.services.pay_rhythm.Era` to mint, or ``None``.
    """
    if not eras:
        return Era(effective_from=first_payday, rhythm=rhythm)
    covering = era_covering(eras, first_payday)
    on_grid = _on_grid(covering, first_payday)
    if covering.rhythm == rhythm and on_grid:
        return None
    return Era(effective_from=first_payday, rhythm=rhythm)


def era_to_move(
    eras: "tuple[Era, ...]",
    paydays: "list[date]",
    opening: date,
) -> EarliestRephase:
    """Return the earliest era's move when a removal leaves *opening* first.

    **The era rule's third half** (plan step ``pay_calendar:C21``, ruling
    **R-PC110**), beside :func:`eras_describing` and :func:`era_to_mint`:
    what a change that RECORDS NOTHING but takes the record's first paydays
    does here.  ``pay_period_write.retire_paydays`` asks it before any
    statement, of the eras it read, only for such a removal.

    **It moves the earliest era's phase UP to the payday left first**:
    :func:`~app.services.pay_calendar.opening_rephase` matches *opening* to
    the paycheck it stands for and hands back the era re-phased onto that
    paycheck's nominal grid day -- the move ``pay_period_write.prepend_paydays``
    makes down, in reverse, so a removal undoing an add leaves the era as the
    add found it, and the era's "since" keeps naming the first paycheck the
    owner holds.  No era is minted or retired: the ruling's option text says
    the saved rhythms never change but for this start.

    **It REFUSES a removal that takes every payday the earliest era pays**
    -- the ruled message, naming the last of them and the era's rhythm.
    That era alone runs backward below the record (ruling **R-PC66**), so
    left with none of the record's paydays it would place the new opening
    on its own grid past its seam and count below it a payday the seam
    replaced (**R-PC75**); retiring it instead would re-describe every
    payday below the record on the next era's rhythm, the option the ruling
    refused.  "Keep one" also refuses removing every paycheck, which no
    door sends: the one that retires from the start keeps the paycheck it
    is told to start from.

    Args:
        eras: The owner's eras as read, ``effective_from`` ascending.
        paydays: Every recorded payday BEFORE the removal, ascending -- the
            refusal names the latest the earliest era pays.  The record's
            first is one of them for every state a door writes (each leaves
            it standing for that era's first grid step, or above it for the
            C17-a migrated era); a hand-built record whose first payday sits
            at an exact half-cadence tie with a later era's first would match
            the later one and leave the list empty, and no door writes that.
        opening: The payday the removal leaves first.

    Returns:
        The :class:`EarliestRephase` onto *opening*'s paycheck, carrying the
        eras as read.

    Raises:
        PayPeriodRemovalRefused: A later era pays *opening*.
    """
    moved = opening_rephase(eras, opening)
    if moved is None:
        # Which era pays a record is ONE rule, the producer that refused the
        # opening, so the payday named is placed by it too.
        earliest_pays = [
            payday for payday in paydays
            if opening_rephase(eras, payday) is not None
        ]
        raise PayPeriodRemovalRefused(
            f"Keep at least {earliest_pays[-1].isoformat()}, your last "
            f"paycheck paid {eras[0].rhythm.cadence.phrase}. Removing it "
            f"would erase that pay rhythm."
        )
    return EarliestRephase(eras=eras, phase=moved.effective_from)


def retire_eras(user_id: int, standing: "tuple[date, ...]") -> int:
    """Delete every era of the owner's but those *standing* names.

    **The ONE door that removes from ``budget.pay_eras``**, and its caller is
    ``pay_period_write._apply`` alone.  A recording batch SUPERSEDES every
    era whose first payday falls after the last payday it leaves standing
    (:func:`eras_describing` -- none of them describes a payday that stands,
    and every payday the batch records falls past that day) and a batch that
    leaves NO payday standing (``reset``) leaves no era either, since an era
    is *how I have been paid since* and nobody is.  It takes the STANDING
    set rather than a boundary day, so the decision is made once, in cash
    days, by the function that judges the mint against it.

    An era is never retired by a batch that records nothing: truncating a
    schedule's tail shortens the record and leaves the owner's declared
    rhythm as it was, so the next extend continues the plan they stated --
    the era covering the record first, then the later era from its own
    first payday (ruling **R-PC75**), and mints nothing on the way.

    Args:
        user_id: The owning user's id.
        standing: The ``effective_from`` of every era to KEEP; empty retires
            every era the owner holds.

    Returns:
        How many rows were deleted.
    """
    query = db.session.query(PayEra).filter(PayEra.user_id == user_id)
    if standing:
        query = query.filter(PayEra.effective_from.notin_(standing))
    return query.delete(synchronize_session=False)
