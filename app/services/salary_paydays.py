"""Shekel Budget App -- which days a salary door takes: ONE rule, each door's own words.

Two doors take a payday: the pay stub entry door (:mod:`app.services
.pay_stub_service`, rulings **R-SAL49** and **R-SAL48**) and the pay list's
doors (:mod:`app.services.pay_list_service` -- the create form's first entry
and Fix today, Record at plan step salary:X-av-3b -- ruling **R-SAL90**, which
gave them the same rule).  The rule lives HERE, beside neither door, because
it is both's: it lived in the stub door's module until plan step
**salary:S11-c-2a**, and the pay list imported a stub module for a rule about
paydays.  What each door SAYS is its own (ruling **R-SAL93**, "Each door names
its own"), passed in as :func:`payday_refusal_for_door`'s ``door_words``.
The day the create form offers its first entry, :func:`first_entry_payday`,
lives here beside the rule it must satisfy (task plan#3170).

Flask-free, and no query of its own: everything is asked of the owner's
:class:`~app.services.pay_calendar.PayCalendar`, which the caller's
:class:`~app.services.balance_at.BalanceContext` derives (loading it on its
first use).
"""

from datetime import date
from typing import TYPE_CHECKING

from app.services.pay_calendar import PayCalendar, span_starting_on_or_after

if TYPE_CHECKING:
    from app.services.balance_at import BalanceContext
    from app.services.pay_calendar import DerivedPeriod


def payday_refusal_for_door(
    ctx: "BalanceContext", day: date, today: date, *, door_words: str,
) -> str | None:
    """Return why a salary door cannot take *day*, or ``None`` when it can.

    **The one statement of which days a salary door takes** (rulings
    **R-SAL49** and **R-SAL48**, the stub door's; ruling **R-SAL90** gave the
    pay list's doors the same rule): the day must open a paycheck the app
    holds or projects -- the calendar's span covering it STARTS on it -- and
    must not be later than the owner's next payday (the first span opening on
    or after *today*).  So on 2026-09-23 the 2026-09-24 payday is accepted
    and the 2026-10-08 one is refused until 2026-09-24 has passed.

    The rule is one; the words are each door's (ruling **R-SAL93**, "Each
    door names its own", which amends R-SAL90's "and message"): the door says
    what it takes, so the pay form never speaks of a stub.

    Args:
        ctx: The route's :class:`~app.services.balance_at.BalanceContext`.
        day: The date the door would take.
        today: The owner's civil today (the display timezone's).
        door_words: What the door takes, as the refusal's second sentence
            opens: ``"A stub can be entered"``, ``"Pay can be recorded"``.

    Returns:
        The message to show, or ``None``.
    """
    calendar = ctx.calendar()
    refusal = not_a_payday(calendar, day)
    if refusal is not None:
        return refusal
    upcoming = span_starting_on_or_after(calendar, today)
    if upcoming is not None and day > upcoming.start_date:
        return (
            f"{day.isoformat()} has not been paid yet.  {door_words} up to your "
            f"next payday, {upcoming.start_date.isoformat()}."
        )
    return None


def not_a_payday(calendar: PayCalendar, day: date) -> str | None:
    """Return why *day* is not a payday the app holds or projects, or ``None``.

    **The one statement of "is this day a payday" for the salary doors that
    take one** (ruling **R-SAL49** for a stub; plan step salary:X-av-3a's pay
    list asks it too, rulings **R-SAL61** and **R-SAL90**): the calendar's
    span covering *day* STARTS on it (:func:`paycheck_on`).  A day below the
    record is refused as the record's, since the calendar holds no paycheck
    there.

    Args:
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.
        day: The day asked about.

    Returns:
        The message to show, or ``None`` for a payday.
    """
    if paycheck_on(calendar, day) is not None:
        return None
    opening = calendar.opening_bound()
    if opening is not None and day < opening:
        return (
            f"The app holds no paycheck on {day.isoformat()}: your pay "
            f"record starts {opening.isoformat()}."
        )
    return f"{day.isoformat()} is not one of your paydays."


def paycheck_on(calendar: PayCalendar, day: date) -> "DerivedPeriod | None":
    """Return the paycheck period that opens on *day*, or ``None``.

    Saved or projected forward (``span_containing``); nothing below the
    record, where the calendar holds no paycheck.

    Args:
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.
        day: The day asked about.

    Returns:
        The period opening on *day*, or ``None``.
    """
    span = calendar.span_containing(day)
    if span is None or span.start_date != day:
        return None
    return span


def first_entry_payday(calendar: PayCalendar, today: date) -> date | None:
    """Return the payday the create form's first pay entry defaults to, or ``None``.

    **The owner's current payday** (ruling **R-SAL90**, "defaults to your
    current one"): the paycheck covering *today*, saved or projected past the
    saved schedule, because the pay the owner types is what they are paid
    now.  **Before the first payday, the first payday**: no pay has come yet,
    the first is the one the typed pay will be, and it is the owner's next
    payday, the latest the doors take.  Either way a day
    :func:`payday_refusal_for_door` takes: it opens a paycheck the app holds
    or projects and is no later than the next payday.

    ``span_containing`` answers ``None`` only for an empty calendar or a day
    before :meth:`~app.services.pay_calendar.PayCalendar.opening_bound`, so
    the opening covers both: the first payday, or ``None`` for an owner
    holding no payday, whom the form points to the pay-period settings.

    *Until task plan#3170 the form read the SAVED period covering today*
    (``period_containing``), *and left the box empty past the saved
    schedule's last paycheck, where the doors take the projected one; and
    empty before the first payday.*

    Args:
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.
        today: The owner's civil today, the day the doors' rule reads.

    Returns:
        The payday, or ``None`` when the calendar holds none.
    """
    current = calendar.span_containing(today)
    if current is not None:
        return current.start_date
    return calendar.opening_bound()


__all__ = [
    "first_entry_payday", "not_a_payday", "paycheck_on", "payday_refusal_for_door",
]
