"""
Shekel Budget App -- Paycheck Calculator Service

Core Phase 2 service: calculates net biweekly paycheck amounts from a
salary profile including raises, deductions, and taxes.

All functions are pure (no DB access) -- data is passed in as arguments.

A pay period here is a :class:`~app.services.pay_calendar.DerivedPeriod`, not a
``budget.pay_periods`` ORM row (pay-calendar plan step **C2-f2d-3**, whose
as-built record carries the census).  Only ``start_date`` and the period's
IDENTITY are read; ``end_date`` / ``period_index`` -- the columns plan step
**C4-c** dropped -- never were.  See :class:`PeriodInfo` for why that identity is
never ``None``.

The package -- one private leaf per verb
----------------------------------------

**A package since plan step salary:C12** (ledger row **P64**), in one-way
import order: :mod:`._breakdown` (the six value types a priced paycheck IS),
:mod:`._lines` (the one pass that prices a line of any kind, with its
cadence, cap and escalation rules, and the one producer of the gross),
:mod:`._calendar_questions` (the month-position and year-to-date reads the
next section names; the year-to-date replays the gross producer),
:mod:`._withholding` (the tax formulas), :mod:`._stubs` (the four taxes,
priced from the paycheck's pay stub through the formulas, since plan step
salary:S11-c-2c) and :mod:`._pricing` (the two public entries, which compose
the rest).  The
engine sat at EXACTLY 1000 of pylint's 1000-line ceiling as one module on
2026-08-17, ``recurrence:R-F16`` bought 127 lines
of room two days later, and the steps since had spent all but three of them
by 2026-09-12 -- so each edit shaved prose to add a line, which is how a
measured claim gets deleted for length.  A package is the answer
``pay_calendar``, ``cash_ledger`` and ``transaction_service`` had already
given the same measurement.  Every public name is re-exported below, so no
caller's import path moved; the private helpers a unit test reaches are
imported from their leaf, as ``pay_calendar``'s are.

The four calendar questions -- asked of the CALENDAR
----------------------------------------------------

Pricing a paycheck needs four facts that are not about the paycheck itself but
about where its payday SITS among this owner's other paydays:

* whether it is the THIRD payday of its calendar month -- the cockpit's
  badge, and until plan step salary:R15-b the one a 24-per-year deduction
  skipped (each line's cadence is its own recurrence rule now, read through
  the occurrence walk by ``PayrollBasis.line_applies_on``);
* whether it is the FIRST, which was likewise the only payday a 12-per-year
  deduction was taken on;
* the Social Security and Medicare wages this owner has already been paid
  this calendar year -- the gross less the all-payroll-taxes lines, since
  plan step salary:S11-e -- which drive the Social Security wage-base cap and
  the Medicare surtax threshold; and
* how much of a capped deduction has already been taken this calendar year.

**All four are answered from the owner's** :class:`~app.services.pay_calendar.PayCalendar`,
which :class:`~app.services.payroll_basis.PayrollBasis` carries -- through
exactly two producers,
:func:`~app.services.pay_calendar.paydays_in_month_through` and
:func:`~app.services.pay_calendar.paydays_in_year_before`.  **Until plan step
balance:X-bh-1 they were answered from an ``all_periods`` SEQUENCE the caller
supplied**, and the four had four separate scans of it.  The type was
``Sequence[DerivedPeriod]``, so a window, a year slice and a one-to-three
period sample all satisfied it while under-counting every one of the four:
measured at ``$502.45`` on one stored salary row when a schedule extend handed
the engine only its newly created periods (ledger row **D25**).  A calendar can
be built only from a COMPLETE payday set, so that argument is now
unrepresentable rather than refused in prose -- finding **N-390**'s first half.

**The SECOND half of N-390 closed at plan step balance:X-bh-2** (ruling
**balance:R-IA**, amended 2026-08-31): both producers project the rhythm
BACKWARD below the schedule's opening payday as well as forward past its
horizon, bounded by ``budget.pay_schedule.history_opens_on`` -- a stored fact
the registration form and the pay-periods settings section ask for, because the
app knows the CADENCE and cannot derive when a job began.  Until then a month
or a calendar year the record opened INSIDE was counted from the first RECORDED
payday: the owner's 2026 year-to-date gross for 2026-05-21 read ``$14,103.84``
-- four recorded paydays at ``$3,525.96`` -- against the ``$31,733.64`` of the
NINE he was really paid, which is what it reads once he states his opening.

**An owner who has stated nothing keeps the old reading, and that is the
amendment.**  ``NULL`` means NOT STATED, so the backward half answers nothing
for every owner nobody has asked.  Why that is the right default rather than
"back to ``CALENDAR_DATE_MIN``" is argued where the fact lives --
``budget.pay_schedule.history_opens_on``'s own column comment -- and turns on
DIRECTION: over-counting a year-to-date retires the FICA wage base and exhausts
an ``annual_cap`` early, understating the deduction and the tax and so
OVERSTATING net.  An application that budgets should guess poor.

*Stated further back than his own opening it reads TEN, not nine*, because the
rhythm steps from 2026-03-26 onto 2026-01-01 and that paycheck was really paid
**2025-12-31**, New Year's Day being a holiday (developer, 2026-08-30).  All 63
of his saved gaps are exactly 14 days, so the app models no shift at all: a
cadence projection can be wrong at exactly the year boundary a calendar-year
cumulative turns on.  That is ledger row **N-398** rather than a silence.

It moves ``$0.00`` on this owner's data either way -- measured 2026-08-30 by
pricing all 63 of his paychecks on both trees -- because he has no 12-per-year
deduction, no ``annual_cap``, and ``$91,675`` against a ``$184,500`` wage base.
The counts underneath DO move once he states his opening: 2026-03-26 goes from
his month's first paycheck to its second, and the 2026 year-to-date at
2026-12-31 from 20 paydays to 26.  With one deduction set to 12-per-year and
one capped at ``$1,200``, the same harness moved net **UP by ``$1,190.54``**
across four paychecks -- up, because both mechanisms REMOVE a deduction.  That
is what the ``$0.00`` is a property of, and what it is not.  *Since plan step
salary:R15-b a line's cadence is a recurrence rule that starts on the RECORDED
opening and is never projected backward, so under a stated opening the
12-per-year half of that figure no longer moves the same way -- the
migration's docstring (``542c61e48ee8``) states both directions of that
divergence; the capped half is unchanged.*

The per-paycheck gross -- a RATE, not a share of a year
-------------------------------------------------------

``base_biweekly`` is what the profile's PAY LIST pays on the payday since plan
step **salary:X-av-3a** (ruling **R-SAL59**): the entry the payday is priced
from, raised by each forecast raise landing after it, each step rounded at the
cent (**R-SAL60**).  Until then it was the (post-raise) annual salary divided
by the owner's PAYCHECK COUNT and rounded once, through
``payroll_basis.gross_per_paycheck``, which that step deleted with the
division.  The engine reaches it through ONE read,
:meth:`~app.services.payroll_basis.PayrollBasis.base_pay_on`, for the paycheck
and for both year-to-date replays (plan step **salary:X-av-2**; they spelled
it three times until then).
*It was ``gross_biweekly`` until plan step salary:R18-b (ruling R-SAL38),
when the gross became the base plus the paycheck's TAXABLE EARNING lines --
an employer allowance with a cadence, priced through the same pass as a
deduction (``_lines``) -- and the net gained the AFTER-TAX earning lines;
every percentage line is a percentage of the BASE, so what this section
argues about the rate is unchanged by a line joining it.*

The paycheck count is derived from the owner's pay cadence and from nothing
else since plan step **R-F16**; :class:`~app.services.payroll_basis.PayrollBasis`
carries what the second stored count cost (finding **F-16**).  **It is the
count of the rhythm in force ON THE PAYDAY since plan step salary:X-av-2**
(ruling **R-SAL66**; ledger row **SAL-569**), read through
:func:`~app.services.pay_calendar.cadence_on` -- the era covering the payday.
It was the LATEST era's for every payday, so a paycheck paid under an earlier
rhythm was divided, and annualised for withholding, by a count it was never
paid at.  An owner with one era is unchanged by construction.

Two properties follow, and they are the point:

* Every paycheck in one salary segment pays the SAME figure.
* The figure is a function of the salary and the cadence ALONE.  No period and
  no period LIST reach it, so nothing a schedule extend can do will move it.

**The second property is the whole argument, and the first is NOT evidence for
this fork** -- an adversarial review of the design corrected a first draft that
made it one.  A flat per-paycheck figure is what a real stub shows, and the
owner's does show one; but the superseded rule is ALSO flat whenever the annual
salary divides evenly, so "real stubs are flat" argues for correcting the
salary input (plan step **X-av**, finding **N-391**) and not for deleting the
residue distribution.  What decides THIS fork is that the residue had to be
apportioned, apportioning it needed an ordinal, and the only ordinal available
was a count of rows that happen to exist.

**This replaced a residue-distribution contract at plan step balance:X-aw**
(ruling **balance:R-HW**, 2026-08-29), superseding audit MED-05 / PA-07 --
which had itself superseded F-127's "accepted simplification" -- and closing
finding **N-239**.  MED-05 spread the annual quantisation residue over the
periods of a calendar year so the year summed to the annual salary exactly.
Deciding WHICH paychecks got the extra cent required knowing where a period sat
among its year's paychecks, and the only thing the engine had to count was the
``budget.pay_periods`` rows that happened to exist -- so filling 2028 from 16
rows to 26 moved six already-settled paychecks by a cent each.  N-239 is now
unrepresentable rather than guarded: there is no group, no ordinal, no
partial-context fallback and no list.

**What that gives up, stated because it is a real cost**: a calendar year's
grosses no longer sum to the annual salary exactly.  The bound is half a cent
per paycheck -- ``0.005 x periods_per_year``, so ``$0.13`` at a biweekly
cadence and ``$1.83`` at the daily one ``budget.pay_schedule`` legally admits.
On the owner's own salary, ``26 x $3,525.96 = $91,674.96``, four cents under.

*Two figures that belong to this paragraph are deliberately NOT here, because
an adversarial review found the first draft conflating them.*  ``-$0.03``
(2026), ``-$0.05`` (2027) and ``+$0.10`` (2028) are what the owner's schedule
moves BY, measured 2026-08-30 as this rule's year total minus the superseded
rule's -- they are not the distance from the annual salary, which for 2026 is
``+$5,006.84`` because a July raise splits the year and it holds 27 paydays.

Giving the identity up is the honest answer rather than a regression, because
the identity MED-05 enforced is not one payroll honours.  The employer's flat ``$3,526.00`` sums to
``$91,676.00`` over 26 paychecks against the ``$91,675.00`` the profile holds
-- and that stub is dated inside a 27-payday year while reading ``annual / 26``
rather than ``annual / 27``, so this employer demonstrably does NOT re-divide in
such a year.  Roughly one calendar year in eleven holds 27 biweekly paydays and
simply pays 27 of them.  **2026 is one on this owner's payday phase** -- 2026-01-01
through 2026-12-31 -- and driving MED-05's rule over it at a FLAT
``$91,675.00`` (no mid-year raise, so the whole year is one reconciliation
group) pays ``$95,200.96``: a full extra paycheck above the salary its own
docstring claimed the year would equal.

**The STORED input is a dated per-paycheck gross since plan step
salary:X-av-3a, with the annual derived** (ruling R-HW, then **R-SAL59**).  The
contract stated here -- a constant rate per paycheck, independent of the
schedule -- is what survived that flip unchanged; only the input improved.  The
yearly figure is now the rate times the paychecks a year, so the "four cents
under" above is gone by construction for the figure the app shows, and a
27-payday year simply pays 27 paychecks, as this employer does.
"""

from ._breakdown import (
    DeductionBreakdown,
    Earnings,
    PaycheckBreakdown,
    PeriodInfo,
    PricedLine,
    TaxLines,
    waterfall_fica_wages,
    waterfall_gross,
    waterfall_net,
    waterfall_taxable,
)
from ._pricing import calculate_paycheck, project_salary
from ._stubs import StubTotals, one_offs_change_taxes, stub_totals, tax_years_for

__all__ = [
    "DeductionBreakdown",
    "Earnings",
    "PaycheckBreakdown",
    "PeriodInfo",
    "PricedLine",
    "StubTotals",
    "TaxLines",
    "calculate_paycheck",
    "project_salary",
    "one_offs_change_taxes",
    "stub_totals",
    "tax_years_for",
    "waterfall_fica_wages",
    "waterfall_gross",
    "waterfall_net",
    "waterfall_taxable",
]
