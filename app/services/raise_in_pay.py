"""Which of a raise's years recorded pay already holds, and whether any reaches a paycheck.

Task plan#3169 (plan step salary:X-av-3b), ruling **R-SAL95** ("Refuse it,
label held") as ruling **plan#3091** moves the day it compares on: recorded pay
includes only the raises dated on or before the FIRST DAY its paycheck pays
for.  :meth:`~app.services.payroll_basis.PayrollBasis.raise_in_pay` returns a
:class:`RaiseInPay`, and every reader reads it, so the raise doors' refusals
and the Raises card's label cannot disagree about one raise:

* **the raise doors refuse a raise no paycheck would get**, in the words of
  ruling **plan#3374** (:attr:`RaiseInPay.refusal`);
* **the Raises card marks each year a recorded entry holds**, ``In your pay
  from <payday>`` for a one-time raise and ``2026 in your pay from
  2026-09-24`` for a yearly one (ruling **plan#3375**:
  :attr:`RaiseInPay.labels`).  A yearly raise is saved while any year still
  reaches pay, and so is a saved raise a later entry comes to hold (a Record
  or a Fix): it stays, labelled;
* **the Retirement page's end-year Save refuses an end year that leaves a
  yearly raise reaching no paycheck**, in that page's own words (task
  plan#3500, ruling **plan#3492**: :attr:`RaiseInPay.end_year_refusal`).  One
  rule for every door that writes a raise; only the words differ, because
  that Save edits nothing but the end year.

**Which application reaches pay and which entry holds it are the walk's own
rules**, and :meth:`~app.services.payroll_basis.PayrollBasis.raise_in_pay`
states them where the walk is.  An application is HELD by the first entry
whose paycheck opens on or after the day it lands.  It REACHES pay when it
lands after the day the entry in force then opens and some paycheck PAYS a day
of it before the entry holding it opens: a weekday of its pay period, the days
a paycheck's base is priced over (ruling **plan#3089**), as a paycheck's banner
reads "pays a day of it" (ruling **plan#3092**).  Made-up: biweekly paydays,
each paycheck paying the Saturday-to-Friday period that ends six days before
it, so an entry from Thursday 2027-03-25 opens on Saturday 03-06:

* a raise dated 03-06 or earlier is held by the entry and reaches no paycheck
  priced from it.  With 03-25 the only entry it is refused: "A raise starting
  by 2027-03-06 is already in your pay from 2027-03-25. Start it after
  2027-03-06, or fix your pay."
* a raise dated 03-10 reaches 03-25's paycheck from 03-10 on and every
  paycheck after it: saved, no label;
* with an earlier entry from 02-25 as well, a raise dated Monday 03-01 reaches
  the last weekdays of 03-11's paycheck (02-20..03-05) and is then held by
  03-25's entry: saved, labelled ``In your pay from 2027-03-25``.

**A weekend day is not a day paid**: where a job's periods end on a Saturday
(five days before a Thursday payday), entries from 02-25 and 03-25 open on
Sundays 02-07 and 03-07, and a raise dated Saturday 03-06 lands after the
first opens but on the last day of 03-11's paycheck, which pays no weekend
day, and the entry opening the next day holds it.  No paycheck's pay is priced
under it, so it is refused, naming 03-25's entry and 03-07.

Pure: values and the words they are shown in, no Flask, no database.
"""
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class HeldYears:
    """The years of one raise that one pay entry's recorded pay holds.

    A run of the raise's applications, each landing on or before the day the
    entry's paycheck opens and after the day the entry before it opens, so a
    yearly raise's held years are unbroken within one entry.

    Attributes:
        first_year: The year of the earliest application the entry holds.
        last_year: The year of the latest; equal to :attr:`first_year` for one.
        opens: The first day the entry's paycheck pays for: recorded pay
            includes every raise dated on or before it (ruling **plan#3091**).
        payday: The entry's payday, the day the Pay card lists it by.
    """

    first_year: int
    last_year: int
    opens: date
    payday: date

    @property
    def years(self) -> str:
        """Return the held years as the card words them: ``2026``, or ``2024-2026``."""
        if self.first_year == self.last_year:
            return str(self.first_year)
        return f"{self.first_year}-{self.last_year}"


@dataclass(frozen=True)
class RaiseInPay:
    """What recorded pay already holds of one raise, and whether any of it reaches a paycheck.

    Attributes:
        is_recurring: Whether the raise repeats every year, which is whether
            its label names the years.
        held: Each entry's run of the raise's applications it holds, in date
            order; empty for a raise every application of which lands after
            the last entry opens.
        reaches_pay: Whether some application lands after the day the entry
            in force then opens and some paycheck pays a day of it before the
            entry holding it opens, a day its pay is priced over: its pay
            period's weekdays (ruling **plan#3089**), or the last day of a
            period holding none.  So some paycheck's pay is priced under it.
            Every raise has at least one application (a recurring raise's end
            year is never before its start's), so when this is ``False``
            :attr:`held` holds every one of them.
    """

    is_recurring: bool
    held: tuple[HeldYears, ...]
    reaches_pay: bool

    @property
    def labels(self) -> tuple[str, ...]:
        """Return the Raises card's line for each entry holding part of the raise (plan#3375).

        ``In your pay from 2026-09-24`` for a one-time raise, which only one
        entry can hold; for a yearly raise each run of years with its entry,
        ``2026 in your pay from 2026-09-24``.
        """
        if not self.is_recurring:
            return tuple(f"In your pay from {held.payday.isoformat()}" for held in self.held)
        return tuple(
            f"{held.years} in your pay from {held.payday.isoformat()}" for held in self.held
        )

    @property
    def refusal(self) -> "str | None":
        """Return why the raise doors refuse the raise, or ``None`` when a paycheck gets it.

        Ruling **plan#3374**, the shorter words, naming the entry holding the
        raise's LAST application: a raise starting after that entry opens
        reaches its paycheck, so the sentence's advice is one the doors accept.
        """
        if self.reaches_pay:
            return None
        holder = self.held[-1]
        opens = holder.opens.isoformat()
        return (
            f"A raise starting by {opens} is already in your pay from "
            f"{holder.payday.isoformat()}. Start it after {opens}, or fix your pay."
        )

    @property
    def end_year_refusal(self) -> "str | None":
        """Return why the Retirement page's end-year Save refuses the raise, or ``None``.

        Ruling **plan#3492**, the words fitted to that page, whose Save edits
        only a yearly raise's end year: the year saved, and the payday of the
        entry holding the raise's LAST application, as :attr:`refusal` names
        it.  The year is that entry's last held year, which IS the year saved:
        when no application reaches pay every one is held, and a yearly raise
        lands once in every year it is believed (never on February 29,
        ``ck_salary_raises_recurring_not_on_february_29``), so its last lands
        in its end year.  ``None`` when a paycheck gets the raise, which
        always holds for a raise believed with no end year: a later year
        lands after the last entry opens.
        """
        if self.reaches_pay:
            return None
        holder = self.held[-1]
        return (
            f"Every year of this raise through {holder.last_year} is already in "
            f"your pay from {holder.payday.isoformat()}, so it changes no "
            "paycheck. Remove it on the salary page instead."
        )


__all__ = ["HeldYears", "RaiseInPay"]
