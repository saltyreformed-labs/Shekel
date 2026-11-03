"""
Shekel Budget App -- Paycheck engine: the questions asked of the CALENDAR.

The three reads behind the "four calendar questions" the package docstring
names -- a payday's position among its month's paydays
(:func:`_month_ordinal`, the third-paycheck badge's read; the two deduction
cadences read it too until plan step salary:R15-b moved each line's cadence
onto its own recurrence rule), whether that position is the month's third or
later (:func:`_is_third_paycheck`), and the
Social Security and Medicare wages this owner has already been paid this
calendar year (:func:`_get_cumulative_wages`, the FICA wage-base cumulative).  The fourth
question, a capped deduction's own year-to-date, is
:func:`~._lines._cumulative_line_before`, which walks the same
producer this leaf does and so lives beside the lines it caps.

All three answer from :class:`~app.services.pay_calendar.PayCalendar` through
exactly two producers, :func:`~app.services.pay_calendar
.paydays_in_month_through` and :func:`~app.services.pay_calendar
.paydays_in_year_before`; the package docstring carries what answering them
from a caller-supplied SEQUENCE cost (plan step **balance:X-bh-1**, ledger row
**D25**).

Split out of the one-module engine at plan step **salary:C12** (ledger row
**P64**).  This leaf imports the calendar module below the engine -- the
basis module too until plan step salary:X-av-2, when its year-to-date replay
began reading base pay off the basis it is handed rather than dividing for
itself -- and, since plan step salary:R18-b, :mod:`._lines` -- whose
:func:`~._lines.priced_gross` is the one producer of a payday's gross, and
since plan step salary:S11-e whose :func:`~._lines.priced_kind` is the one
per-kind pass, which prices the lines that leave its Social Security and
Medicare wages for the paycheck and for each payday the wage cumulative
here replays; :mod:`._lines` imports nothing of the package above
:mod:`._breakdown`, so there is no cycle.
"""

from app.enums import PaycheckLineKindEnum
from app.services.pay_calendar import (
    PayCalendarError,
    paydays_in_month_through,
    paydays_in_year_before,
)
from app.utils.money import ZERO

from ._breakdown import pre_fica_total, waterfall_fica_wages
from ._lines import _LineContext, priced_gross, priced_kind


def _month_ordinal(calendar, payday):
    """Return this payday's 1-based position among the paydays of its month.

    The ONE calendar read behind both month-position judgements.  With biweekly
    pay most months hold two paydays and twice a year one holds three, so the
    ordinal is 1, 2 or 3 -- but it is derived rather than assumed, because
    the owner's cadence (a ``budget.pay_eras`` row's since plan step
    ``pay_calendar:C17-a``) is user-stated, a day count 1..365 or a day of
    the month, and a daily-paid owner's month holds about thirty.

    It reads the CALENDAR, so the answer is a property of the owner's whole
    schedule rather than of whichever periods a caller was holding: see the
    package docstring's "The four calendar questions" for what the second
    shape cost.

    **It takes a payday rather than a period**, which is what lets the
    annual-cap cumulative call it too: that walk holds bare ``date`` values
    off :func:`~app.services.pay_calendar.paydays_in_year_before`, so a
    period-keyed signature left it spelling the count itself -- two spellings
    of one rule in one file, under a docstring claiming to be the only one.
    An adversarial review of this step measured that; the signature is the
    fix.

    **It REFUSES a period this calendar cannot place**, and an adversarial
    review of this step is why: the count is over the CALENDAR's paydays, not
    over the argument, so a period paired with the wrong owner's calendar was
    answered silently -- measured at 0, 1 and 2 for three foreign paydays in
    one month, each a different wrong answer to the deduction cadence, and 0
    is the reading that skips a 12-per-year deduction and takes a 24-per-year
    one on a third paycheck.  :class:`~app.services.payroll_basis.PayrollBasis`
    makes a narrow payday SET unrepresentable; it cannot make a
    period/calendar MISMATCH unrepresentable,
    because the period arrives separately.  So the mismatch is refused where it
    is detectable rather than described in a docstring, which is the standing
    :func:`~app.services.pay_calendar._views.axis_window`'s own refusal has --
    no caller in ``app/`` reaches it, and it guards the value against one
    assembled by hand.

    Args:
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.
        payday: The day the paycheck arrives.  Must be one this owner is paid
            on -- saved, or projected forward at their cadence.

    Returns:
        The 1-based ordinal, never 0.

    Raises:
        PayCalendarError: This owner is not paid on *payday*, so there is no
            position in the month to answer with.  Plan step **balance:X-bh-2**
            NARROWED what reaches here and did not remove it: the rhythm runs
            below the opening payday for an owner who has STATED one, so a
            day on their own phase and at or above their stated opening is
            placed -- the day 2026-03-12 that used to raise for the developer
            prices as March's first paycheck once he states it.  What is left
            is a day OFF that phase, a day below the stated opening, and every
            day below the record for an owner who has stated nothing.
            **For a stated owner this is a WEAKER cross-owner guard and the
            cost is countable**: at a shared cadence one payday in
            ``cadence_days`` lands on their phase, so a mispairing that was
            refused unconditionally is refused about thirteen times in
            fourteen.  For an unstated owner -- which is every owner until
            they answer -- the old strictness is unchanged.
    """
    paydays = paydays_in_month_through(calendar, payday)
    if not paydays or paydays[-1] != payday:
        raise PayCalendarError(
            f"user {calendar.user_id} is not paid on {payday.isoformat()}, "
            f"so that paycheck has no position among the paydays of its "
            f"month and the deduction cadence cannot be answered.  Their "
            f"calendar holds {len(calendar.periods)} payday(s) and "
            f"{len(paydays)} in that month at or before it.  A paycheck is "
            f"priced against the calendar it belongs to; pairing one owner's "
            f"period with another's schedule, or naming a day off their "
            f"cadence or below the day their paychecks began, reaches here."
        )
    return len(paydays)


def _is_third_paycheck(month_ordinal):
    """Whether a payday at this month position is its month's THIRD or later.

    **The rule written once**, for the two consumers that ask it: the deduction
    cadence below (a 24-per-year deduction is not taken on it) and
    :attr:`~._breakdown.PeriodInfo.is_third_paycheck`, which the salary cockpit, the
    projection ledger and the paycheck-anatomy fragment all render.  Two
    spellings of ``>= 3`` would be two places for the boundary to move.

    ``>=`` rather than ``==`` because a cadence shorter than fourteen days puts
    a fourth, fifth or thirtieth payday in a month, and a 24-per-year deduction
    is taken on the first two of them and no more.

    Args:
        month_ordinal: The payday's 1-based position in its calendar month, as
            :func:`_month_ordinal` returns it.

    Returns:
        ``True`` when the payday is the month's third or later.
    """
    return month_ordinal >= 3


def _get_cumulative_wages(basis, payday):
    """Return the Social Security and Medicare wages paid this year before *payday*.

    What the FICA Social Security wage-base cap and the Medicare surtax
    threshold are measured against, for the paycheck priced and, since plan
    step **salary:S11-c-2c**, for the pay stub that prices it: the formulas
    price the stub's own paycheck at the year-to-date in force on ITS payday
    (:mod:`._stubs`).  **It takes a payday rather than a period** for the
    reason :func:`_month_ordinal` does: a stub is dated, not a period, and a
    stub whose payday later left the pay record (ruling **R-SAL53**) is
    still summed to.

    **The paydays come from ``basis.calendar``** since plan step
    **balance:X-bh-1**, through
    :func:`~app.services.pay_calendar.paydays_in_year_before`.  It was an
    ``all_periods`` sequence a caller supplied, and the year-scoping and the
    ordering were both done here -- a filter on the year, a sort, and a break
    -- where the producer now guarantees both.

    **It reads BELOW the schedule's opening payday since plan step
    balance:X-bh-2**, which closed ledger row **N-390** -- for an owner who has
    STATED when their paychecks began.  Such an owner is no longer summed from
    the record's boundary, so the wage-base cap is reached when their wages
    reach it rather than late: the 2026 total for 2026-05-21 goes from
    ``$14,103.84`` -- four recorded paydays -- to the nine the developer was
    really paid, once he states his own opening.  An owner who has stated
    nothing is summed from the record exactly as before, which is the ruling's
    2026-08-31 amendment and the conservative direction.

    **Each earlier payday's gross is base pay PLUS its taxable earning lines,
    since plan step salary:R18-b** (ruling **R-SAL38**): a taxable allowance
    paid on a March paycheck is in the year's wages from then on, so the
    wage-base cap is reached when the wages reach it.  The figure comes from
    :func:`~._lines.priced_gross`, the SAME producer
    :func:`~._pricing.calculate_paycheck` prices the live paycheck with, so
    the earlier grosses summed here match each period's ``gross_biweekly``
    by construction rather than by two expressions happening to agree
    (``CLAUDE.md`` rule 14, one walk).  Until R18-b the term was the base
    rate alone, which a taxable line would have left out of the cap.  **The
    base it adds to is** :meth:`~app.services.payroll_basis.PayrollBasis
    .base_pay_on`'s **since plan step salary:X-av-2**, the one the live
    paycheck reads, so an earlier payday under an earlier rhythm is summed
    at the pay it was paid at rather than at the latest rhythm's divisor.

    **Less its all-payroll-taxes lines, since plan step salary:S11-e** (ruling
    **plan#2639**; ledger row **SAL-566**): the wage base and the surtax
    threshold count Social Security and Medicare wages, which a Section 125
    premium leaves, so each earlier payday adds
    :func:`~._breakdown.waterfall_fica_wages` of its gross and the
    :func:`~._breakdown.pre_fica_total` of the all-payroll-taxes lines
    :func:`~._lines.priced_kind` prices -- the rule, the total and the pass
    the paycheck's own tax reads.  Until then the total summed the gross,
    counting those lines toward the base, so it was reached early (one cause
    of ledger row **SAL-595**, whose rest plan#2637 owns).

    Args:
        basis: The :class:`~app.services.payroll_basis.PayrollBasis` -- its
            salary, raise set and lines price each earlier paycheck and its
            calendar supplies the paydays.
        payday: The day being priced.  It bounds the sum, which is STRICTLY
            before it, and its year is the window.

    Returns:
        The summed wages, ``ZERO`` for the year's first paycheck.
    """
    cumulative = ZERO

    for earlier in paydays_in_year_before(basis.calendar, payday):
        base = basis.base_pay_on(earlier).per_paycheck
        ctx = _LineContext(basis, earlier, base)
        _taxable, gross = priced_gross(ctx)
        cumulative += waterfall_fica_wages(
            gross,
            pre_fica_total(priced_kind(ctx, PaycheckLineKindEnum.PRE_FICA_DEDUCTION)),
        )

    return cumulative
