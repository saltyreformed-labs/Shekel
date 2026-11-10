"""
Shekel Budget App -- a salary's PAY LIST doors (plan step salary:X-av-3a).

What one paycheck pays, from a dated payday on, is the salary's stored fact
(:class:`~app.models.salary_pay_entry.SalaryPayEntry`, rulings **R-SAL59** and
**R-SAL61**), and it is written through here and nothing else: the create
form's FIRST entry (:func:`start_pay_list`), the salary page's Record a pay
change (:func:`record_entry`, plan step salary:X-av-3b) and its Fix
(:func:`fix_entry`).  The first entry IS a Record on an empty list, so the
create door writes through :func:`record_entry` too: one act, one set of
refusals.  Remove (:func:`remove_entry`, plan step salary:X-av-3b) takes any
entry but the only one (ruling **R-SAL68**, "Remove, except the last one").

**What the doors refuse, and whose rule each refusal is:**

* a day that is not a payday the app holds or projects a paycheck for, or
  one later than the owner's next payday -- the stub door's own rule
  (**R-SAL49**, **R-SAL48**), asked through
  :func:`~app.services.salary_paydays.payday_refusal_for_door` so the rule
  has one home (ruling **R-SAL90**, "Up to next payday": an entry is pay
  received, and a mistyped year would otherwise replace every forecast raise
  before it), in this door's own words, "Pay can be recorded up to your next
  payday, ..." (ruling **R-SAL93**, "Each door names its own", which amends
  R-SAL90); asked of a Fix only when it CHANGES the payday, as the stub door
  asks it (**R-SAL53**), so an entry whose payday later left the pay record
  stays fixable in place;
* a Record on, or a Fix moving an entry onto, a payday another of the
  profile's entries holds: one entry per payday
  (``uq_pay_entries_profile_payday``), and neither door overwrites an entry
  unseen.  One sentence for both, :func:`_refuse_held_payday`;
* a Remove of a profile's only entry: every paycheck is priced from the pay
  list, so a profile holds at least one entry (**R-SAL68**).

The amount's bounds are the schema's (``SalaryPayEntryRecordSchema`` and the
Fix schema built on it, ``SalaryProfileCreateSchema``; one home,
``schemas.validation._helpers``).  Every write prices the paychecks the entry
covers: the Record and Fix routes re-price them with
:func:`~app.services.salary_regeneration.regenerate_salary_transactions`, and
the create route generates the new profile's paychecks from its new template
(:func:`~app.services.recurrence_engine.generate_for_template`).

Flask-free: ORM rows and plain values in, the row out.  Never commits -- the
route owns the unit of work.  :func:`start_pay_list` flushes, so its caller
prices the new entry in the same unit of work; :func:`record_entry`,
:func:`fix_entry` and :func:`remove_entry` only STAGE their change, so the
flush that can lose a version race or meet ``uq_pay_entries_profile_payday``
runs inside the route's guard
(:func:`~app.routes._commit_helpers.regenerate_commit_or_report`) and is
reported there, never as a 500.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from app.exceptions import ValidationError
from app.extensions import db
from app.models.salary_pay_entry import SalaryPayEntry
from app.models.salary_profile import SalaryProfile
from app.services.payroll_basis import PayrollBasis
from app.services.salary_paydays import payday_refusal_for_door

if TYPE_CHECKING:
    from app.services.balance_at import BalanceContext


@dataclass(frozen=True)
class PayRow:
    """One pay entry as the salary page lists it (ruling **R-SAL61**).

    Attributes:
        entry: The :class:`~app.models.salary_pay_entry.SalaryPayEntry`, for
            its id, payday, amount and version counter.
        paychecks_a_year: The paychecks a year of the rhythm in force on its
            payday (ruling **R-SAL66**).
        yearly: The yearly figure of the entry's paycheck: its pay RATE
            times that count, shown and never stored (ruling **R-SAL59**).
            The rate is the pay in force on the last day the paycheck pays
            for, which is the entry's amount unless a forecast raise lands
            inside that paycheck's pay period after the entry opens (task
            plan#3100); never the paycheck's base, part of a raise times the
            count.
    """

    entry: SalaryPayEntry
    paychecks_a_year: int
    yearly: Decimal


def pay_rows(basis: PayrollBasis) -> list[PayRow]:
    """Return the profile's pay list as the salary page shows it, payday ascending.

    Each entry's yearly figure is :meth:`~app.services.payroll_basis
    .PayrollBasis.base_pay_on` its own payday -- the ONE producer of a
    paycheck's yearly figure (``BasePay.annual``, rule 14), its pay rate
    times the paychecks a year in force that day.  The rate walks from the
    entry to the last day its paycheck pays for: the entry's amount, unless a
    forecast raise lands in between (see :class:`PayRow`).

    Args:
        basis: The profile's :class:`~app.services.payroll_basis.PayrollBasis`.

    Returns:
        One :class:`PayRow` per entry.
    """
    rows = []
    for entry in sorted(basis.profile.pay_entries, key=lambda e: e.payday):
        base = basis.base_pay_on(entry.payday)
        rows.append(PayRow(entry, int(base.periods_per_year), base.annual))
    return rows


def _refuse_payday(ctx: "BalanceContext", payday: date, today: date) -> None:
    """Raise when *payday* is not a payday, or is later than the owner's next one.

    Args:
        ctx: The route's pass, read for the owner's calendar.
        payday: The day an entry would take effect on.
        today: The owner's civil today, as the stub door is handed it.

    Raises:
        ValidationError: With :func:`~app.services.salary_paydays
            .payday_refusal_for_door`'s message in this door's words
            (ruling **R-SAL93**).
    """
    refusal = payday_refusal_for_door(
        ctx, payday, today, door_words="Pay can be recorded",
    )
    if refusal is not None:
        raise ValidationError(refusal)


def _refuse_held_payday(
    profile_id: int, payday: date, moving_entry_id: int | None,
) -> None:
    """Raise when another of the profile's entries already holds *payday*.

    One entry per payday (``uq_pay_entries_profile_payday``), refused in words
    before the key could refuse it: a Record on a held payday, or a Fix moving
    an entry onto one, would replace pay the owner can see with pay typed
    unseen.  The fix is to Fix that entry, which is what the words say.

    Args:
        profile_id: The pay list's profile.
        payday: The payday the door would write an entry on.
        moving_entry_id: The entry a Fix is moving, which does not hold
            *payday* against itself; ``None`` for a Record, which moves none.

    Raises:
        ValidationError: Another entry holds *payday*.
    """
    query = db.session.query(SalaryPayEntry.id).filter(
        SalaryPayEntry.salary_profile_id == profile_id,
        SalaryPayEntry.payday == payday,
    )
    if moving_entry_id is not None:
        query = query.filter(SalaryPayEntry.id != moving_entry_id)
    if query.first() is not None:
        raise ValidationError(
            f"Your pay from {payday.isoformat()} is already recorded; "
            f"fix that entry instead."
        )


def record_entry(
    profile: SalaryProfile, ctx: "BalanceContext", amount: Decimal,
    payday: date, today: date,
) -> SalaryPayEntry:
    """Record a pay change: *amount* a paycheck from *payday* on (ruling **R-SAL61**).

    "'Record a pay change' adds one from a payday you pick (like the stub
    screen)."  The new entry prices its own paycheck and every one after it up
    to the next entry's; a forecast raise landing on or before the first day
    its paycheck pays for is taken to be in *amount* already (ruling
    **plan#3091**), and a later one still compounds on it (**R-SAL59**).  Of
    a profile's first entry, every paycheck before *payday* is priced from it
    too (R-SAL59: "Paychecks before the first entry use it").

    The ONE door an entry is added through: the create door's first entry
    (:func:`start_pay_list`), the salary page's Record, and the stub screen's
    button plan step salary:X-av-4 adds.

    Args:
        profile: The profile, its ownership checked by the route.
        ctx: The route's pass (its owner's calendar).
        amount: What one paycheck pays, above zero (the schema's bound).
        payday: The payday it pays it from.
        today: The owner's civil today.

    Returns:
        The entry, added to the profile's pay list and STAGED, not flushed:
        the INSERT runs at the caller's guarded flush (module docstring),
        where a concurrent Record of the same payday meets the unique key.

    Raises:
        ValidationError: *payday* is not a payday, is later than the owner's
            next one, or another of the profile's entries holds it.
    """
    _refuse_payday(ctx, payday, today)
    _refuse_held_payday(profile.id, payday, None)
    entry = SalaryPayEntry(payday=payday, amount=amount)
    # Through the relationship, so the profile's loaded pay list holds the
    # entry before the door prices it in the same unit of work.
    profile.pay_entries.append(entry)
    return entry


def start_pay_list(
    profile: SalaryProfile, ctx: "BalanceContext", amount: Decimal,
    payday: date, today: date,
) -> SalaryPayEntry:
    """Write a new profile's FIRST pay entry: *amount* a paycheck from *payday* on.

    :func:`record_entry` on the new profile's empty list, then flushed, so the
    create door generates its paychecks from the entry in the same unit of
    work.

    Args:
        profile: The new, flushed profile.
        ctx: The route's pass (its owner's calendar).
        amount: What one paycheck pays, above zero (the schema's bound).
        payday: The payday it pays it from.
        today: The owner's civil today.

    Returns:
        The entry, flushed.

    Raises:
        ValidationError: *payday* is not a payday, or is later than the
            owner's next one.
    """
    entry = record_entry(profile, ctx, amount, payday, today)
    db.session.flush()
    return entry


def fix_entry(
    entry: SalaryPayEntry, ctx: "BalanceContext", amount: Decimal,
    payday: date, today: date,
) -> SalaryPayEntry:
    """Correct one pay entry's amount, its payday, or both (ruling **R-SAL61**, "Fix").

    A correction moves only the paychecks the entry covers -- from its payday
    to the next entry's, and before it when it is the first -- which is the
    difference between a fix and a raise the dated list exists to keep.

    Args:
        entry: The entry, its ownership and version already checked by the
            route.
        ctx: The route's pass (its owner's calendar).
        amount: The corrected amount, above zero.
        payday: The corrected payday.
        today: The owner's civil today.

    Returns:
        The entry, its change staged and NOT flushed: the version-pinned
        UPDATE runs at the caller's guarded flush (module docstring), where a
        concurrent Fix surfaces as ``StaleDataError`` and a concurrent move
        onto the same payday as the unique key's ``IntegrityError``.

    Raises:
        ValidationError: A CHANGED payday that is not a payday, is later
            than the owner's next one, or another of the profile's entries
            holds.
    """
    if payday != entry.payday:
        _refuse_payday(ctx, payday, today)
        _refuse_held_payday(entry.salary_profile_id, payday, entry.id)
    entry.amount = amount
    entry.payday = payday
    return entry


def remove_entry(entry: SalaryPayEntry) -> None:
    """Remove one pay entry, never the profile's only one (ruling **R-SAL68**).

    "Remove, except the last one."  The paychecks the entry covered are then
    priced from the entry before it -- or, when it was the first, from the
    next one, which then covers the paychecks before it too (ruling
    **R-SAL59**: "Paychecks before the first entry use it") -- with every
    forecast raise landing after that entry's paycheck opens.

    Args:
        entry: The entry, its ownership and version already checked by the
            route.

    Returns:
        None.  The entry leaves its profile's loaded pay list, so the
        regeneration the route runs next prices without it, and its DELETE is
        STAGED (the list's ``delete-orphan`` cascade), version-pinned at the
        caller's guarded flush, where a concurrent Fix or Remove of it
        surfaces as ``StaleDataError``.

    Raises:
        ValidationError: *entry* is its profile's only entry.
    """
    profile = entry.salary_profile
    if len(profile.pay_entries) == 1:
        raise ValidationError(
            f"Your pay from {entry.payday.isoformat()} is your only pay "
            f"entry, and every paycheck is priced from it, so it can't be "
            f"removed.  Fix it instead."
        )
    profile.pay_entries.remove(entry)


__all__ = [
    "PayRow", "fix_entry", "pay_rows", "record_entry", "remove_entry",
    "start_pay_list",
]
