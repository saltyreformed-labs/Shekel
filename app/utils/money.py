"""Centralized monetary rounding boundary and shared financial constants
(E-24, E-26; HIGH-04, HIGH-05).

Full-precision Decimal arithmetic everywhere in the codebase; rounding
happens once, here, at the display or persistence boundary. ``ROUND_HALF_UP``
is the only default -- it is the convention every hand-computed financial
test in this project assumes, and the convention every financial display
in the app implies. Python's Decimal default of ``ROUND_HALF_EVEN``
(banker's rounding) is a silent source of one-cent drift at half-cent
boundaries and must never be reached implicitly through a bare
``.quantize(Decimal("0.01"))``.

This module exposes three rounding helpers and the calendar-month
denominator every monthly figure divides by. ``round_money`` is the default
boundary rounding. ``round_money_ceiling`` is the explicitly-named sanctioned
variant for the savings-goal monthly contribution case, where under-
funding by a fraction of a cent must never round down. ``round_money_floor``
is the sanctioned variant for largest-remainder cent allocation, where a
set of display rows must sum exactly to their already-rounded total --
naming each exception at the call site makes the deviation auditable.

Both helpers reject ``float`` input with ``TypeError``. Construction of a
Decimal from a float (``Decimal(0.1)``) re-introduces the float
imprecision the helper exists to eliminate, so the helper refuses the
input at the boundary rather than silently rounding an already-imprecise
value. Callers must construct Decimal from strings (CLAUDE.md / coding
standards: "Construct Decimals from strings").

``MONTHS_PER_YEAR`` is the calendar-month denominator, and it belongs here
because 12 is a property of the calendar rather than of any owner.

**The one interest producer lives here as well** (plan step recurrence:R16-d):
:func:`accrued_interest` prices a window under the agreement's accrual
convention at full precision, and :func:`installment_interest` is that figure
rounded once, a loan's per-installment charge.  It sits at this leaf for the
reason the allocation below does: every walk that charges interest must be
able to reach it.

**The two halves of a loan payment's arithmetic BOTH live here, and plan step
X-au-g-2c-3a is why the second one moved.** A loan payment is one CHARGE
(:func:`installment_interest`, what time has cost) and one ALLOCATION
(:func:`apply_payment_cash`, how the cash covers it), and every walk over a
loan needs both. The charge has been shared from this leaf since E-24 (as
``accrue_monthly_interest`` until plan step recurrence:R16-d), which is what
makes every walk's interest "byte-identical by construction". The allocation
was NOT shared: it sat in
``loan_ledger._split``, whose import closure is 23 modules, ABOVE two of the
four walks that need it. ``amortization_engine`` (closure 2) and
``rate_period_engine`` (closure 3) structurally COULD NOT call it -- reaching
up is the cycle ``loan_ledger._split -> rate_period_engine ->
amortization_engine`` -- so each restated the rule inline, and a fourth
statement (``loan_payment_service._engine_prep``'s escrow floor) disagreed
with the other three. That was finding **N-409**: a short payment read as
exactly on schedule. The duplication was FORCED BY THE LAYERING rather than
chosen, which is why the remedy is a move and not a convention.

**Its partner ``PAY_PERIODS_PER_YEAR = Decimal("26")`` LEFT at the recurrence
arc's plan step R7a-2a, and it was never a constant.** It stood for how often
the owner is paid, which is the owner's pay era's cadence
(``budget.pay_eras`` since plan step ``pay_calendar:C17-a``;
``budget.pay_schedule.cadence_days`` until then) and is user-stated, a day
count 1..365 or a day of the month -- so every monthly-equivalent figure on
``/savings``, the Recurring surface and ``/retirement`` was wrong for anyone
not paid biweekly: a weekly-paid owner's ``$100`` per-paycheck bill reported
``$216.67`` a month against a true ``$433.33``. It is now DERIVED per owner by
:class:`app.services.pay_calendar.PayCadence`, which also owns the named unit
conversions the nine reader files used to spell inline. The E-24 / HIGH-05 rule
that produced this paragraph still stands and is stronger: the biweekly-to-
monthly factor has one home, and it is no longer a number this module can
state.
"""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

from app.enums import InterestAccrualConventionEnum
from app.utils.dates import month_ordinal

CENTS = Decimal("0.01")
ZERO = Decimal("0")
HUNDRED = Decimal("100")

MONTHS_PER_YEAR = Decimal("12")


#: The largest magnitude any of this app's money columns can hold.  Every one
#: of them is ``Numeric(12, 2)``, so ten integer digits and two decimal places,
#: and a figure at or above this is UNSTORABLE rather than merely large.
#:
#: **Named because a DERIVED figure can exceed it where an entered one cannot.**
#: A schema bound keeps a typed figure inside the domain (see
#: ``app.schemas.validation._helpers._NON_NEGATIVE_MONETARY``, and plan step
#: X-f2-c3 for what omitting one cost), but a door that SUMS stored columns is
#: bounded by the count of terms rather than by any one of them -- so the sum,
#: and anything derived from it, needs the domain stated somewhere it can be
#: compared against.  Reaching the database with a larger figure is
#: ``NumericValueOutOfRange`` (SQLSTATE 22003), which is an unhandled 500 and,
#: inside a batch, one that discards every item applied beside it.
MONEY_COLUMN_MAX: Decimal = Decimal("9999999999.99")


def round_money(value: Decimal) -> Decimal:
    """Round a monetary Decimal to cents using ``ROUND_HALF_UP``.

    The default boundary rounding for every displayed or persisted
    monetary amount in the app. Use this at the boundary; keep
    intermediate arithmetic at full Decimal precision.

    Args:
        value: a Decimal in full precision. ``float`` is rejected to
            prevent the float-imprecision leak the helper exists to
            eliminate; callers construct Decimal from strings.

    Returns:
        ``value`` quantized to ``Decimal("0.01")`` with
        ``rounding=ROUND_HALF_UP``. ``Decimal("2.345")`` becomes
        ``Decimal("2.35")``, never ``Decimal("2.34")`` (which is what
        Python's default ``ROUND_HALF_EVEN`` would produce).

    Raises:
        TypeError: if ``value`` is not a ``Decimal``. Specifically
            rejects ``float`` so a caller cannot bypass the Decimal
            contract by accident.
    """
    if not isinstance(value, Decimal):
        raise TypeError(
            f"round_money expects Decimal, got {type(value).__name__}: {value!r}"
        )
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def round_money_ceiling(value: Decimal) -> Decimal:
    """Round a monetary Decimal up to cents using ``ROUND_CEILING``.

    Sanctioned variant for cases where under-funding by a fraction of a
    cent must never round down -- specifically the savings-goal monthly
    contribution computation, which by design over-funds rather than
    under-funds the target. Naming the variant at the call site makes
    the deviation from default ``round_money`` explicit and auditable;
    callers must never reach a non-default rounding mode implicitly.

    Args:
        value: a Decimal in full precision. ``float`` is rejected for
            the same reason as ``round_money``.

    Returns:
        ``value`` quantized to ``Decimal("0.01")`` with
        ``rounding=ROUND_CEILING``. ``Decimal("2.341")`` becomes
        ``Decimal("2.35")``; an already-exact ``Decimal("2.340")``
        stays ``Decimal("2.34")``.

    Raises:
        TypeError: if ``value`` is not a ``Decimal``. Specifically
            rejects ``float``.
    """
    if not isinstance(value, Decimal):
        raise TypeError(
            f"round_money_ceiling expects Decimal, got {type(value).__name__}: {value!r}"
        )
    return value.quantize(CENTS, rounding=ROUND_CEILING)


def round_money_floor(value: Decimal) -> Decimal:
    """Round a monetary Decimal down to cents using ``ROUND_FLOOR``.

    Sanctioned variant for largest-remainder cent allocation (the
    escrow display rows, deep-hunt #17): each row starts from its
    floored value and the leftover cents -- the difference between the
    sum of floors and the sum-then-rounded total -- are handed out to
    the rows with the largest fractional remainders, so the rendered
    rows always add up to the stated total without changing the total
    itself. Naming the variant at the call site makes the deviation
    from default ``round_money`` explicit and auditable; callers must
    never reach a non-default rounding mode implicitly.

    Args:
        value: a Decimal in full precision. ``float`` is rejected for
            the same reason as ``round_money``.

    Returns:
        ``value`` quantized to ``Decimal("0.01")`` with
        ``rounding=ROUND_FLOOR``. ``Decimal("2.349")`` becomes
        ``Decimal("2.34")``; a negative ``Decimal("-2.341")`` becomes
        ``Decimal("-2.35")`` (floor moves toward negative infinity).

    Raises:
        TypeError: if ``value`` is not a ``Decimal``. Specifically
            rejects ``float``.
    """
    if not isinstance(value, Decimal):
        raise TypeError(
            f"round_money_floor expects Decimal, got {type(value).__name__}: {value!r}"
        )
    return value.quantize(CENTS, rounding=ROUND_FLOOR)


def percent_complete(total: Decimal, target: Decimal) -> Decimal:
    """Compute ``total`` as a percentage of ``target``, clamped to [0, 100].

    The single numeric contract behind every "percent funded" / progress-
    bar surface (the budget dashboard's savings-goal cards, the companion
    entry view).  Guards against division by zero and clamps the result so
    a render never receives a negative width or one exceeding 100%.

    Args:
        total: The amount accumulated so far (sum of entries / balance).
        target: The budgeted or goal amount.  When ``<= 0`` the function
            returns ``Decimal("0")`` rather than dividing by zero or
            producing a misleading negative percentage.

    Returns:
        A Decimal in ``[0, 100]`` quantized to two decimal places with
        ``ROUND_HALF_UP`` for the in-range case; the un-quantized
        ``Decimal("0")`` when ``target <= 0`` or the ratio is negative,
        and ``Decimal("100.00")`` when the ratio exceeds 100%.
    """
    if target <= ZERO:
        return ZERO
    pct = (total / target * HUNDRED).quantize(CENTS, rounding=ROUND_HALF_UP)
    if pct > HUNDRED:
        return Decimal("100.00")
    if pct < ZERO:
        return ZERO
    return pct


def _monthly_1_12(
    balance: Decimal, annual_rate: Decimal, start: date, end: date,
) -> Decimal:
    """Return ``MONTHLY_1_12``'s interest: a twelfth of the rate per calendar month spanned.

    The months are ``month_ordinal(end) - month_ordinal(start)``, so the
    window's DAYS never enter: ``[Jan 31, Feb 28)`` and ``[Feb 28, Mar 31)``
    are one month each, and a window opening and closing inside one month is
    none.  Associated ``balance * (annual_rate / 12) * months`` -- see
    :func:`accrued_interest` for why the brackets are load-bearing.

    Args:
        balance: The balance accruing (``> 0``; :func:`accrued_interest` has
            already answered a non-positive one).
        annual_rate: The annual rate as a decimal fraction (``> 0``).
        start: The window's first day.
        end: The window's EXCLUSIVE end (``> start``).

    Returns:
        The window's interest at full precision.
    """
    months = month_ordinal(end) - month_ordinal(start)
    return balance * (annual_rate / MONTHS_PER_YEAR) * months


#: Every convention's rule, keyed by its member: the dispatch IS the lookup, so
#: a member without a rule, or a value that is not a member at all (a stored
#: id, ``None``, the member's name string), is a ``KeyError`` at the first call
#: rather than a silent ``0`` -- the savings producer's unrecognised-frequency
#: arm (``interest_projection.accrued_interest``) is the shape this refuses.
#: One entry, because the list has one member (ruling plan#3354).
_ACCRUAL_RULES: dict[
    InterestAccrualConventionEnum,
    Callable[[Decimal, Decimal, date, date], Decimal],
] = {
    InterestAccrualConventionEnum.MONTHLY_1_12: _monthly_1_12,
}


def accrued_interest(
    balance: Decimal,
    annual_rate: Decimal,
    start: date,
    end: date,
    convention: InterestAccrualConventionEnum,
) -> Decimal:
    """Return the interest *balance* accrues over ``[start, end)`` under *convention*, UNROUNDED.

    **The ONE interest producer** (plan step recurrence:R16-d, finding **D52**;
    rulings plan#3348 and plan#3354).  How interest accrues is a fact of the
    agreement -- a loan's note states it, and the loan holds it as
    ``budget.loan_params.interest_accrual_convention_id`` -- so the rule is a
    member of :class:`~app.enums.InterestAccrualConventionEnum` passed in, never
    a formula a caller assumes.  Its one member today:

    * ``MONTHLY_1_12`` -- one twelfth of the annual rate for each calendar
      month the window spans (:func:`_monthly_1_12`), the US fixed-rate
      mortgage's convention and every loan's until its note says otherwise.

    **Total over its inputs**: a non-positive balance, rate or window (``end <=
    start``) accrues ``0``, so no caller guards it.  Every walk that charges a
    loan stops at a closed balance before asking, so that arm moves nothing for
    them; it is there so the question has an answer for every input.  The
    convention is looked up FIRST, so a non-member is refused even where the
    inputs would accrue nothing.

    **Full precision**, because a caller that sums windows must round once, at
    the end, rather than per window: the savings fold credits whole cents off a
    full-precision running total (``balance_at._asset_fold``, ruling R-X) and
    joins this producer at plan#3356.  A loan's charge rounds each installment,
    and that is :func:`installment_interest`, the one place the rounding is
    spelled.

    **The association is load-bearing**: ``balance * (annual_rate / 12)``, not
    ``(balance * annual_rate) / 12``.  The two are not one function -- measured
    2026-09-02 over 500,000 randomised draws they part once, ``$271,375.20`` at
    ``0.025`` giving ``$565.36`` against ``$565.37`` -- and the first is the
    one every loan has been charged by since E-24.  A month's window is
    therefore byte-identical, once rounded, to ``round_money(balance *
    (annual_rate / 12))``, the spelling ``accrue_monthly_interest`` held until
    this step; the multiplication by a month count of ``1`` is exact.

    Args:
        balance: The balance the interest accrues on.  ``float`` is not
            supported; construct Decimals from strings.
        annual_rate: The annual rate as a decimal fraction (``Decimal("0.06875")``
            for 6.875%).
        start: The window's first day.
        end: The window's EXCLUSIVE end.  An installment walk's window is
            :func:`app.services.installment_calendar.accrual_window`.
        convention: The agreement's
            :class:`~app.enums.InterestAccrualConventionEnum` member.  There is
            no default: a caller that forgets it is a ``TypeError``.

    Returns:
        The window's interest at full Decimal precision, or ``Decimal("0")``
        for a non-positive balance, rate or window.

    Raises:
        KeyError: if *convention* is not a member with a rule (a stored id, the
            member's name string, ``None``).
    """
    rule = _ACCRUAL_RULES[convention]
    if balance <= ZERO or annual_rate <= ZERO or end <= start:
        return ZERO
    return rule(balance, annual_rate, start, end)


def installment_interest(
    balance: Decimal,
    annual_rate: Decimal,
    start: date,
    end: date,
    convention: InterestAccrualConventionEnum,
) -> Decimal:
    """Return one installment's interest charge: :func:`accrued_interest` rounded ONCE to cents.

    A loan charges interest per installment in whole cents, so each walk that
    charges one -- the posted ledger's replay, the contractual schedule, the
    forward projection and the payoff strategy -- asks this rather than
    spelling ``round_money(accrued_interest(...))`` itself: one rounding of one
    producer, in one place.  :func:`round_money`'s ``ROUND_HALF_UP`` is the
    project's only rounding boundary, and the window rounds once, never per
    month inside it.

    Args:
        balance: The balance the interest accrues on.
        annual_rate: The annual rate as a decimal fraction.
        start: The window's first day.
        end: The window's EXCLUSIVE end, the installment's due date
            (:func:`app.services.installment_calendar.accrual_window`).
        convention: The loan's
            :class:`~app.enums.InterestAccrualConventionEnum` member.

    Returns:
        The installment's interest quantized to cents; ``Decimal("0.00")`` for
        a non-positive balance, rate or window.

    Raises:
        KeyError: as :func:`accrued_interest`.
    """
    return round_money(
        accrued_interest(balance, annual_rate, start, end, convention),
    )


@dataclass(frozen=True)
class PaymentCashSplit:
    """The four economic parts one payment's cash divides into, plus the balance after.

    The pure result of :func:`apply_payment_cash`, carrying NO source record --
    so the same arithmetic serves a settled shadow, a projected shadow, a
    synthesized contractual installment and a schedule row.

    Attributes:
        interest: The interest this payment cleared -- an Expense leg (``>= 0``).
            Echoed back from the charge the caller supplied; never re-derived
            here.
        escrow: The escrow this payment cleared, NO inflation -- an Expense leg
            (``>= 0``).  Echoed back from the charge the caller supplied.
        principal: The real debt paid down, ``cash - interest - escrow``, capped
            at the outstanding balance.  May be NEGATIVE (an underpayment that
            grows the balance) -- surfaced, never clamped (plan D5).
        excess: A payoff overpayment routed to a Refund Receivable (Asset) leg
            (``>= 0``): cash beyond what closes the loan (plan D4).
        balance_after: The outstanding balance after this payment
            (``balance_before - principal``).
    """

    interest: Decimal
    escrow: Decimal
    principal: Decimal
    excess: Decimal
    balance_after: Decimal


def apply_payment_cash(
    cash: Decimal,
    balance: Decimal,
    charged_interest: Decimal,
    charged_escrow: Decimal,
) -> PaymentCashSplit:
    """Allocate one payment's *cash* against charges ALREADY standing.

    **The ONE allocation rule in the application** (plan steps **R16-a** and
    **X-au-g-2c-3a**).  A loan payment's cash covers what the loan has charged
    -- the escrow it impounds and the interest it has accrued -- and only the
    remainder pays the debt down.  That rule is a function of the CASH and the
    CHARGES and of nothing else.  WHICH charges stand is a different question,
    answered by how much time has passed and at what rate.

    **Fused, the two made the payment COUNT the clock.**  Measured on a
    production clone: 30 payments of ``$531.94`` fourteen days apart charged the
    same 30 months of interest as 30 a month apart -- ``$1,096.34`` either way,
    split for split -- so the Van Loan paid off in half the time modelled the
    identical interest.
    With the accrual lifted out, a second payment inside one accrual period
    arrives here with ``charged_interest = 0.00`` and pays pure principal, which
    is what it does.

    **It lives at this leaf so that every walk can reach it**, which two of the
    four could not while it sat in ``loan_ledger`` -- see the module docstring.

    Two regimes (plan Section 6):

    * **Loan already closed** (``balance <= 0``): nothing is owed, so the entire
      cash is an overpayment routed to ``excess`` (a Refund) whatever the caller
      computed, and the balance does not move.  This keeps every post-payoff cash
      entry matched by a correction instead of a phantom paydown.
    * **Open loan**: ``principal = cash - charged_interest - charged_escrow``; a
      principal that would overrun the balance caps to it, the remainder going to
      ``excess``.

    Args:
        cash: The cash this payment moved (settled actual, live planned, or
            synthesized contractual).
        balance: The outstanding balance before this payment.
        charged_interest: The interest standing against the loan for this payment
            to clear.  ``0.00`` for a payment that follows another inside one
            accrual period.  Never re-derived here.
        charged_escrow: The escrow standing against the loan for this payment to
            clear -- ``0.00`` when the loan escrows nothing, or when an earlier
            payment in the same accrual period already covered it.

    Returns:
        The :class:`PaymentCashSplit` for this payment.
    """
    if balance <= 0:
        # Already paid off: a further payment is pure overpayment (refund), with no
        # interest and no escrow due, and the balance does not move.
        return PaymentCashSplit(
            interest=Decimal("0.00"),
            escrow=Decimal("0.00"),
            principal=Decimal("0.00"),
            excess=cash,
            balance_after=balance,
        )
    principal = cash - charged_interest - charged_escrow
    if principal > balance:
        # Payoff overpayment: principal caps at the remaining balance; the surplus
        # is a refund the lender owes back (plan D4), never absorbed into principal
        # or escrow.
        excess = principal - balance
        principal = balance
    else:
        excess = Decimal("0.00")
    return PaymentCashSplit(
        interest=charged_interest,
        escrow=charged_escrow,
        principal=principal,
        excess=excess,
        balance_after=balance - principal,
    )
