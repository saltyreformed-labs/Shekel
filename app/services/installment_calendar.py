"""A loan's INSTALLMENT CALENDAR: one rule, and every date question derived from it.

**The rule.**  A loan's installment number ``k`` (``k >= 1``) falls on its due
day in the ``k``-th month after the month it originated, clamped to that
month's end -- due the 31st: Jan 31, Feb 28, Mar 31.  The clamp is the
project's ONE month-day clamp, :func:`app.utils.dates.clamped_day` over
:func:`app.utils.dates.month_ordinal` (recurrence ruling **R-R3**, pay-calendar
ruling **R-PC79**), which the recurrence walk, the pay grid and the card
statement already land a meant day with.  A clamp re-applied to each month
afresh is what returns a 31st-of-the-month loan to the 31st after a February,
where stepping a month from a CLAMPED date keeps the clamp.

**Every answer here is worked out from that rule when it is asked, and none is
stored** (plan step recurrence:R16-c-2; the developer, ruling on the design:
*"I don't want any second copy or anything stored that can go stale especially
if it can be derived"*).  The list of a loan's installments enumerates it; the
installment a date falls in inverts it with month arithmetic rather than a
search over a list; the first installment is its first term; and the due date
on or after a pay period's start reads the same clamp from that month.  Until
that step the rule was written out by hand in the loan rate engine (the month
step and the due-date fallback) beside the one clamp; the payoff and refinance
calculators' own month step and clamp went at plan step recurrence:R25, which
also made the list, the lookup and the due date on or after a day readings of
the monthly-grid set beside the clamp
(:func:`app.utils.dates.grid_month_on_or_before` and its siblings, ruling
**R-R122**) that the card statement, the pay grid and the recurrence walk read
too; the first installment and the next month's due day are the clamp
itself.  The installment lookup
ruling **R-R104** needs was first built, inside that step, as a search over a
list rebuilt from origination for every payment priced: measured 2026-09-24 on
a production copy with a mortgage's payments switched to derive mode (rolled
back), pricing its forward plan's payments took 103 ms of a 162 ms build,
against 1.4 ms before, and the lookup worked out from the rule takes 1.5 ms.

**It sits BELOW every loan layer** -- it imports :mod:`app.utils.dates` and
nothing else -- so the rate engine, the loan loaders, the charge calendar
(:func:`app.services.loan_ledger.contract_charges`), the cash price
(:func:`app.services.cash_ledger._loan_installment._installment_cash`), the
forward plan and the loan page all reach the one rule from wherever they are.

Pure: no I/O, no clock, no Flask.
"""

from datetime import date

from app.utils.dates import (
    clamped_day,
    grid_month_on_or_after,
    grid_month_on_or_before,
    grid_months_within,
    month_ordinal,
)


def due_in_following_month(reference: date, payment_day: int) -> date:
    """Return the loan's due day in the month after *reference*'s month.

    The rule's one-month step: whatever day of its month *reference* is, the
    answer is *payment_day* in the NEXT calendar month, clamped to that
    month's end.  It seeds the loan's first installment from its origination
    (:func:`first_installment_date`) and steps to the installment after a
    date's own (:func:`installment_after`).  *It seeded the rate engine's
    replayed schedule from its latest row or anchor too, until plan step
    recurrence:R16-e deleted that replay (ruling plan#3382), and stepped the
    schedule-slot walk (``amortization_engine.schedule_dates``) past a
    contested month until plan task plan#3389 deleted that walk.*

    Args:
        reference: Any date; only its month is read.
        payment_day: The loan's contractual day-of-month due day, 1-31.

    Returns:
        *payment_day* in the month after *reference*'s, day-clamped.
    """
    return clamped_day(month_ordinal(reference) + 1, payment_day)


def first_installment_date(origination_date: date, payment_day: int) -> date:
    """Return the date of a loan's FIRST contractual installment.

    The project's single derivation of "when does this loan's first payment
    come due?", and the loan's own convention rather than a calendar guess:
    the ``payment_day`` of the month AFTER origination -- never a
    ``payment_day`` falling later in the origination month itself.
    Concretely, a loan originating 2026-04-15 with ``payment_day`` 20 first
    bills 2026-05-20, NOT 2026-04-20.  It is installment number 1 of the
    rule (:func:`installment_dates`).

    Deliberately NOT :func:`monthly_due_date`, which answers a different
    question (the first ``payment_day`` ON OR AFTER a date -- the installment
    a pay period contains) and would return that wrong 2026-04-20.

    Exposed because the recurrence bound needs it
    (:func:`app.services.loan_recurrence_sync.loan_cadence_start` makes it the
    rule's ``starts_on`` -- its FIRST OCCURRENCE since ruling R-R16 -- so no
    payment generates before the loan exists, plan step C9a), and since plan
    step recurrence:R25 the payoff-by-date and refinance calculators start
    their projections on it
    (:func:`app.services.amortization_engine.calculate_payoff_by_date`,
    ``routes.loan.calculators._project_refinance``) rather than on a month
    step of their own.  The
    alternative -- reading
    ``contractual_schedule_from_origination(...)[0].payment_date`` -- yields
    the identical date (pinned by test) but builds the loan's entire 360-row
    schedule and needs its rate feed to answer a question no rate can
    influence.

    It lived in :mod:`app.services.rate_period_engine` until plan step
    recurrence:R16-c-2 moved the rule here (the developer's ruling assigns
    the loan's copies of the clamp to the loan steps; pay-calendar step
    ``C20-b`` keeps the salary cockpit's and the recurrence row-dater's).

    Args:
        origination_date: The loan's immutable
            :attr:`~app.models.loan_params.LoanParams.origination_date`.
        payment_day: The loan's contractual day-of-month due day, 1-31.

    Returns:
        The first contractual installment's due date.
    """
    return due_in_following_month(origination_date, payment_day)


def monthly_due_date(period_start: date, payment_day: int) -> date:
    """Return the first *payment_day* on or after *period_start*.

    A loan payment is recorded against the pay period whose range contains
    its monthly due date, so for a payment carrying no stored due date the
    first ``payment_day`` at or after its period's start IS that due date
    (:func:`app.services.loan_loaders.installment_for`'s fallback).  The pay
    period start alone is too coarse for the anchor-boundary comparison ("did
    this payment come due after the balance was last verified?"): a true-up
    dated between a period's start and that period's due date would strand
    the payment in the gap.

    It is the grid's first day on or after *period_start*
    (:func:`app.utils.dates.grid_month_on_or_after`, the one set every
    monthly grid reads since plan step recurrence:R25): the rule's clamp in
    *period_start*'s own month, or the next month's when that day has already
    passed, so a ``payment_day`` of 31 resolves to Feb 28/29 in February.  It
    lived in :mod:`app.services.rate_period_engine` until plan step
    recurrence:R16-c-2 (see :func:`first_installment_date`).

    Args:
        period_start: The date to resolve from -- a payment's pay-period
            start.
        payment_day: The loan's contractual day-of-month due day, 1-31.

    Returns:
        The first date on or after *period_start* whose day is
        *payment_day* (day-clamped to the month's length).
    """
    return clamped_day(
        grid_month_on_or_after(period_start, payment_day), payment_day,
    )


def installment_dates(
    origination_date: date, payment_day: int, through: date,
) -> list[date]:
    """Return a loan's contractual installment dates, its first through *through*.

    The rule enumerated: installment ``k`` for ``k = 1, 2, ...`` -- number 1
    being :func:`first_installment_date` -- while it falls on or before
    *through*.  These are the dates a loan is CHARGED on
    -- every one of them from its first installment onward, whether or not a
    payment lands on it (:func:`app.services.loan_ledger.contract_charges`,
    rulings **R-R72**, **R-R89** and **R-R100**) -- and the grid the loan
    page's band chart and the plan's post-contractual extension step along.
    A caller that needs ONE installment asks :func:`installment_of` rather
    than searching this list.  The enumeration is the grid's own
    (:func:`app.utils.dates.grid_months_within`, plan step recurrence:R25):
    the months from the first installment's through the last on or before
    *through*, each re-clamped from the meant day.

    Args:
        origination_date: The loan's immutable
            :attr:`~app.models.loan_params.LoanParams.origination_date`.
        payment_day: The loan's contractual day-of-month due day, 1-31.
        through: The last day the sequence may reach; a date before the first
            installment yields an empty list.

    Returns:
        The installment dates, ascending, one per calendar month.
    """
    return [
        clamped_day(ordinal, payment_day)
        for ordinal in grid_months_within(
            payment_day,
            first_installment_date(origination_date, payment_day),
            through,
        )
    ]


def installment_of(
    origination_date: date, payment_day: int, day: date,
) -> date | None:
    """Return the installment whose INTERVAL *day* falls in, or ``None`` before the first.

    **Ruling R-R89's pairing** ("B: contract interval", plan step
    recurrence:R16-c-2): a loan payment belongs to the latest installment due
    ON or BEFORE it, so a payment due on the 10th of a loan due on the 22nd
    pays the previous month's installment, not the one twelve days later.
    Ruling **R-R104** ("Price on the interval") made it the ONE answer every
    tier asks: the payment's CASH is priced on this installment's rate period
    and escrow
    (:func:`app.services.cash_ledger._loan_installment._installment_cash`),
    and the forward plan's contract-only estimate counts an installment
    covered when a record falls in its interval
    (``app.services.balance_at._plan._estimated_from_contract``).

    **The rule inverted, not searched.**  The installment in *day*'s own
    month is the clamp there; when that has not yet fallen by *day*, the
    interval opened on the month before's -- the grid's latest day on or
    before *day* (:func:`app.utils.dates.grid_month_on_or_before`, plan step
    recurrence:R25).  Either way it is an installment only from number 1 on,
    :func:`first_installment_date`'s month.

    **The replay's pairing is the same answer by construction.**  The replay
    hands a payment whatever charge stands when it walks
    (:func:`app.services.loan_ledger.replay_loan_events`), and it charges
    every date :func:`installment_dates` lists -- the same rule -- with a
    charge sorting before a payment on its own date, so a payment walked on
    its own date faces exactly this installment's charge.  The one payment it
    differs for is an OVERDUE projection pushed past a later recorded fact,
    which R-R89 pairs with what stands at the push (the catch-up pays pure
    principal); its cash is priced on its own interval, as it would be had
    nothing pushed it.

    Args:
        origination_date: The loan's immutable
            :attr:`~app.models.loan_params.LoanParams.origination_date`.
        payment_day: The loan's contractual day-of-month due day, 1-31.
        day: The date to place -- a payment's due date in contract time
            (:func:`app.services.loan_loaders.installment_for`).

    Returns:
        The latest installment on or before *day*, or ``None`` when *day*
        precedes the first installment: ruling R-C's early extra, a payment
        after origination and before any installment falls, which no charge
        stands over.
    """
    ordinal = grid_month_on_or_before(day, payment_day)
    if ordinal < month_ordinal(first_installment_date(origination_date, payment_day)):
        return None
    return clamped_day(ordinal, payment_day)


def installment_after(
    origination_date: date, payment_day: int, day: date,
) -> date:
    """Return the first installment due strictly AFTER *day*.

    **The installment a loan's record has not reached yet** (ruling
    plan#3382, plan step recurrence:R16-e): handed the date of the record's
    last event -- a payment's own due date or an assertion's date, in
    contract time -- it names where the contract's remaining installments
    begin (:func:`app.services.loan_resolver.compute_payoff_scenarios`).  An
    installment due ON *day* is not after it, for the same reason an
    installment due on an assertion's date is inside the asserted balance
    (:func:`app.services.rate_period_engine.due_after_anchor`).

    The rule stepped, not searched: the installment whose interval *day*
    falls in (:func:`installment_of`), then the rule's one-month step
    (:func:`due_in_following_month`); for a *day* before the first
    installment -- the origination itself, or a balance asserted between it
    and the first due date -- the first installment
    (:func:`first_installment_date`).  Made-up figures, a loan from Jan 5
    due the 22nd: Feb 10 answers Feb 22, Mar 10 answers Mar 22, and Mar 22
    answers Apr 22.

    Args:
        origination_date: The loan's immutable
            :attr:`~app.models.loan_params.LoanParams.origination_date`.
        payment_day: The loan's contractual day-of-month due day, 1-31.
        day: The date to step past.

    Returns:
        The earliest installment due after *day*.
    """
    installment = installment_of(origination_date, payment_day, day)
    if installment is None:
        return first_installment_date(origination_date, payment_day)
    return due_in_following_month(installment, payment_day)


def installment_paid_by(
    origination_date: date, payment_day: int, due: date,
) -> date:
    """Return the day that NAMES a payment due on *due*: the installment it pays.

    **The one answer to "which installment does this payment pay"** (ruling
    **R-R104**) as a date every reader can name the payment by: the
    installment whose interval *due* falls in (:func:`installment_of`), or
    *due* itself for a payment due before the loan's first installment --
    ruling R-C's early extra, which pays no installment and keeps its own
    date.  For a payment due ON the contractual day it IS *due*; for one due
    off it, the latest installment before it -- a payment due Mar 10 on a loan
    due the 22nd pays, and is named by, Feb 22.

    Its readers are every tier that dates or names a payment by its
    installment: the cash price
    (:func:`app.services.cash_ledger._loan_installment._installment_cash`),
    the day a ``$0.00`` close is visible and booked on (ruling **R-R107**,
    :func:`app.services.loan_ledger.payment_visible_on`), the loan page's
    payment-history card (ruling **R-R108**,
    :func:`app.services.loan_posting_service.confirmed_loan_payment_history`)
    and the confirmed schedule row's date, number and escrow (ruling
    **R-R109**, :func:`app.services.balance_at.confirmed_view`).  The first
    two each spelled the early-extra fallback out beside
    :func:`installment_of` until plan step recurrence:R16-c-2 gave it this
    one home.  The forward plan's coverage test asks :func:`installment_of`
    directly, because an early extra covers no installment there.

    Args:
        origination_date: The loan's immutable
            :attr:`~app.models.loan_params.LoanParams.origination_date`.
        payment_day: The loan's contractual day-of-month due day, 1-31.
        due: The payment's own due date in contract time
            (:func:`app.services.loan_loaders.loan_payment_due_date`).

    Returns:
        The installment *due* falls in, or *due* when it precedes the first.
    """
    installment = installment_of(origination_date, payment_day, due)
    return due if installment is None else installment
