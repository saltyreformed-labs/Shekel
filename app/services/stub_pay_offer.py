"""Shekel Budget App -- the Pay Stub page's pay offer (plan step salary:X-av-4).

What the page offers when a saved stub's base pay differs from the base the
app pays that payday.

Ruling **R-SAL61**: "When a stub's base differs from your pay, the stub
screen offers 'Use this as my pay from this payday', which adds an entry", as
the three rulings asked at this step settle it:

* **plan#3959** -- no button on a paycheck a forecast raise starts inside
  (:meth:`~app.services.payroll_basis.PayrollBasis.raises_inside`): its
  stub's base mixes the pay before and after the raise, and pay recorded from
  its payday would take the raise a second time.  The page says so and points
  to the first later payday paid entirely at the new pay.  Made-up: $2,000.00
  a paycheck and 5% from Wednesday 03-11; 03-27 pays 03-08..03-21, two of its
  ten weekdays before the raise, so its stub's base is 2,000.00 x 2/10 +
  2,100.00 x 8/10 = $2,080.00; recorded from 03-27 it would price every
  paycheck from 04-10 at 2,080.00 x 1.05 = $2,184.00, where the new pay is
  $2,100.00;
* **plan#3962** -- on a payday a pay amount already starts on, the button
  FIXES that amount, since a Record there is refused;
* **plan#3965** -- no check of the stub's own figures beyond the confirm,
  which names the old figure, the new one and the paychecks that move.

Otherwise the button is a Record, offered only where the Record door would
take it (:func:`~app.services.pay_list_service.record_refusal`).  The write
is the pay list's own (:func:`~app.services.pay_list_service.record_entry` or
:func:`~app.services.pay_list_service.fix_entry`): this module decides only
what is offered.  The door asks it again when the button is pressed and
writes nothing where the stub, the offer's kind, or the version of the amount
a Fix corrects has changed since the page rendered.  A Record's reach is not
pinned: an amount another tab records later than the stub's payday meanwhile
narrows the paychecks the Record moves, and the figure written is still the
stub's base.

Flask-free and read-only: plain values and ORM rows in, a value out.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from app.models.salary_pay_entry import SalaryPayEntry
from app.models.salary_profile import SalaryProfile
from app.services import pay_list_service, pay_stub_service
from app.services.pay_banner import ForecastRaise
from app.services.pay_calendar import PayCalendar, span_starting_on_or_after
from app.services.pay_list_service import PayReach
from app.services.payroll_basis import PayrollBasis
from app.services.salary_paydays import paycheck_on

if TYPE_CHECKING:
    from app.services.balance_at import BalanceContext
    from app.services.salary_raises import RaiseTerms


@dataclass(frozen=True)
class PayOffer:
    """The button: a stub's base pay as the pay from its payday on.

    Attributes:
        payday: The stub's payday, which the amount is from.
        pays_now: What the app pays that payday now: the paycheck's base, the
            figure the page sets beside the stub's.
        stub_base: The stub's base pay, the figure the button writes.
        reach: The paychecks the write moves.
        entry: The amount the payday already holds, which the button fixes
            (ruling **plan#3962**); ``None`` for a Record.
    """

    payday: date
    pays_now: Decimal
    stub_base: Decimal
    reach: PayReach
    entry: SalaryPayEntry | None

    #: Whether the page renders a button: the template's one test of which
    #: of the two answers it holds.
    button: ClassVar[bool] = True

    @property
    def old_figure(self) -> Decimal:
        """Return the figure the confirm says the write replaces.

        A Fix replaces the amount the payday holds, as the pay list lists it;
        a Record replaces what the app pays that payday.
        """
        return self.entry.amount if self.entry is not None else self.pays_now

    @property
    def label(self) -> str:
        """Return the button's words.

        "Use $X as my pay from P" for a Record; "Fix your pay from P: $old
        to $new" for a Fix (ruling **plan#3962**).
        """
        payday = self.payday.isoformat()
        if self.entry is None:
            return f"Use {_dollars(self.stub_base)} as my pay from {payday}"
        return (
            f"Fix your pay from {payday}: {_dollars(self.old_figure)} to "
            f"{_dollars(self.stub_base)}"
        )

    @property
    def confirm(self) -> str:
        """Return the question the button asks before it writes (ruling **plan#3965**).

        It names the old figure, the new one and the paychecks that move
        (:func:`_moves`), the check ruling plan#3965 chose over holding the
        button back.  Made-up: "$2,000.00 to $2,100.00, every paycheck from
        2026-01-02 on, and every one before 2026-01-02" is what the owner
        reads before a stub typed with a $100.00 allowance folded into its
        base reprices every paycheck.
        """
        payday = self.payday.isoformat()
        question = (
            f"Use {_dollars(self.stub_base)} as your pay from {payday}?"
            if self.entry is None else f"Fix your pay from {payday}?"
        )
        return (
            f"{question} {_dollars(self.old_figure)} to {_dollars(self.stub_base)}, "
            f"{_moves(self.reach)}."
        )


@dataclass(frozen=True)
class NoPayOffer:
    """Why the page offers no button, though the stub's base differs from the app's.

    Attributes:
        reason: The sentence the page shows.
        link_payday: The payday whose stub the page links to: the first later
            payday paid entirely at the new pay, when a raise starts inside
            this paycheck (ruling **plan#3959**) and the stub flow takes that
            payday today; else ``None``, and the reason names it as one to use
            once it is paid (:func:`_raise_inside`).
    """

    reason: str
    link_payday: date | None = None

    #: Whether the page renders a button (see :attr:`PayOffer.button`).
    button: ClassVar[bool] = False


def offer(
    profile: SalaryProfile, ctx: "BalanceContext", payday: date,
    stub_base: Decimal, today: date,
) -> PayOffer | NoPayOffer | None:
    """Return what the Pay Stub page offers for a stub's base pay on *payday*.

    In this order, each answer the first that applies:

    1. ``None`` -- the app holds no paycheck on *payday* (the page already
       says there is nothing to compare), or it pays *stub_base* there.
    2. :class:`NoPayOffer` -- a forecast raise starts inside the paycheck
       (ruling **plan#3959**), whether or not the payday holds an amount: a
       Fix to a blended base is as wrong as a Record of one.
    3. :class:`PayOffer` fixing the amount the payday holds (ruling
       **plan#3962**).
    4. :class:`NoPayOffer` with the Record door's own refusal, when it would
       refuse the payday (one later than the next payday, say, after the pay
       schedule moved).
    5. :class:`PayOffer` recording a new amount.

    The app's figure is the paycheck's base off the walk the read pass prices
    through (:attr:`~app.services.income_service.ProfilePaychecks.basis`), the
    figure the page's gap is set against, so the offer and the gap beside it
    read one pricing.

    Args:
        profile: The stub's owned profile, its pay list loaded or loadable.
        ctx: The route's :class:`~app.services.balance_at.BalanceContext`.
        payday: The stub's payday.
        stub_base: The stub's base pay.
        today: The owner's civil today.

    Returns:
        The offer, the reason there is none, or ``None``.
    """
    calendar = ctx.calendar()
    if paycheck_on(calendar, payday) is None:
        return None
    basis = ctx.paychecks().for_profile(profile).basis
    pays_now = basis.base_pay_on(payday).per_paycheck
    if stub_base == pays_now:
        return None
    inside = basis.raises_inside(payday)
    if inside:
        return _raise_inside(basis, ctx, payday, inside, today)
    held = pay_list_service.entry_on(profile.id, payday)
    if held is None:
        refusal = pay_list_service.record_refusal(profile, ctx, payday, today)
        if refusal is not None:
            return NoPayOffer(refusal)
    return PayOffer(
        payday, pays_now, stub_base, pay_list_service.reach_of(profile, payday), held,
    )


def _dollars(amount: Decimal) -> str:
    """Word a pay figure as the page prints one: ``$2,061.50``.

    Every figure here is above zero (the schema's bound on a pay amount and
    a stub's base, and the columns' CHECKs), so there is no sign to word.
    """
    return f"${amount:,.2f}"


def _moves(reach: PayReach) -> str:
    """Word the paychecks a pay amount from *reach*'s payday prices.

    Made-up: amounts from 01-02 and 06-05.  One from 03-13 moves "every
    paycheck from 2026-03-13 to the one before 2026-06-05, where your next
    pay amount starts"; one from 06-05, "every paycheck from 2026-06-05 on";
    the 01-02 amount, as the first, "every paycheck from 2026-01-02 to the
    one before 2026-06-05, where your next pay amount starts, and every one
    before 2026-01-02".

    Args:
        reach: The paychecks, as :func:`~app.services.pay_list_service.reach_of`
            answers them.

    Returns:
        The phrase, lower-case, without a full stop.
    """
    start = reach.payday.isoformat()
    if reach.until is None:
        moved = f"every paycheck from {start} on"
    else:
        moved = (
            f"every paycheck from {start} to the one before "
            f"{reach.until.isoformat()}, where your next pay amount starts"
        )
    if reach.and_before:
        moved += f", and every one before {start}"
    return moved


def _raise_inside(
    basis: PayrollBasis, ctx: "BalanceContext", payday: date,
    inside: "tuple[tuple[date, RaiseTerms], ...]", today: date,
) -> NoPayOffer:
    """Say why a paycheck a raise starts inside offers no button, and where to look instead.

    Each raise is named in the banner's own words
    (:attr:`~app.services.pay_banner.ForecastRaise.label`) with the day it
    starts.  The sentence holds whether or not a weekday of the paycheck
    falls before the raise: a raise starting the Monday after the pay period
    opens on a Sunday leaves every weekday at the new pay, and pay recorded
    from that payday would still take the raise again.

    The first later payday paid entirely at the new pay is linked only when
    the stub flow takes it today (:func:`~app.services.pay_stub_service
    .payday_refusal`, that flow's one rule): a raise starting inside the next
    payday's paycheck points past the next payday, which the flow refuses as
    not paid yet until that payday has passed.  Made-up: on 03-20, with
    03-27 the next payday, a raise from 03-11 inside 03-27's paycheck
    (03-08..03-21) names 04-10 as a stub to use once that paycheck is paid,
    with no link; on 03-28 the same page links it.

    Args:
        basis: The read pass's walk for the profile.
        ctx: The route's pass, read for the owner's calendar.
        payday: The stub's payday.
        inside: The raises starting inside its paycheck, as
            :meth:`~app.services.payroll_basis.PayrollBasis.raises_inside`
            answers them.
        today: The owner's civil today.

    Returns:
        The :class:`NoPayOffer`.
    """
    raises = " and ".join(
        f"your {ForecastRaise(terms).label} raise from {landed.isoformat()}"
        for landed, terms in inside
    )
    verb = "starts" if len(inside) == 1 else "start"
    reason = (
        f"{raises[0].upper()}{raises[1:]} {verb} during the pay period this "
        f"paycheck covers, so this stub's base pay can't be used as your pay "
        f"from this payday: the app would add the raise to it a second time."
    )
    whole = _first_whole_payday(basis, ctx.calendar(), payday)
    if whole is None:
        return NoPayOffer(reason)
    if pay_stub_service.payday_refusal(ctx, whole, today) is not None:
        return NoPayOffer(
            f"{reason}  Once your {whole.isoformat()} paycheck is paid, use its "
            f"stub: the first paycheck paid entirely at the new pay."
        )
    return NoPayOffer(
        f"{reason}  Use your {whole.isoformat()} stub, the first paycheck paid "
        f"entirely at the new pay.",
        whole,
    )


def _first_whole_payday(
    basis: PayrollBasis, calendar: PayCalendar, payday: date,
) -> date | None:
    """Return the first payday after *payday* whose paycheck no raise starts inside.

    That paycheck's pay period lies wholly after every raise starting inside
    *payday*'s, so it is paid entirely at the new pay, and its stub's base is
    a pay to record (ruling **plan#3959**).

    **At most one more payday than there are raises is asked**: each raise
    starts at most once a year, so of the next *n + 1* paychecks one is free
    of all *n* raises whenever those paychecks fall within a year, which every
    rhythm paying at least that often does.  Past that the answer is
    ``None``, and the page names no payday rather than walking on.

    Args:
        basis: The read pass's walk for the profile.
        calendar: The owner's calendar, which projects past its saved paydays.
        payday: The paycheck a raise starts inside.

    Returns:
        The payday, or ``None``.
    """
    day = payday
    for _ in range(len(basis.raises) + 1):
        following = span_starting_on_or_after(calendar, day + timedelta(days=1))
        if following is None:
            return None
        day = following.start_date
        if not basis.raises_inside(day):
            return day
    return None


__all__ = ["NoPayOffer", "PayOffer", "offer"]
