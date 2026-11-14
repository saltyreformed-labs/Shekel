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
  to the first later payday paid entirely at the new pay.  On the owner's own
  stubs: 07-16 pays 06-27..07-10, eight of its ten weekdays after a 3% raise
  from 07-01, base $3,610.56; recorded from 07-16 it would price every
  paycheck from 07-30 at 3,610.56 x 1.03 = $3,718.88, where the employer pays
  $3,631.70;
* **plan#3962** -- on a payday a pay amount already starts on, the button
  FIXES that amount, since a Record there is refused;
* **plan#3965** -- no check of the stub's own figures beyond the confirm,
  which names the old figure, the new one and the paychecks that move.

Otherwise the button is a Record, offered only where the Record door would
take it (:func:`~app.services.pay_list_service.record_refusal`).  The write
is the pay list's own (:func:`~app.services.pay_list_service.record_entry` or
:func:`~app.services.pay_list_service.fix_entry`): this module decides only
what is offered, and the door asks it again when the button is pressed, so a
page left open while the pay list changed cannot write what it showed.

Flask-free and read-only: plain values and ORM rows in, a value out.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from app.models.salary_pay_entry import SalaryPayEntry
from app.models.salary_profile import SalaryProfile
from app.services import pay_list_service
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


@dataclass(frozen=True)
class NoPayOffer:
    """Why the page offers no button, though the stub's base differs from the app's.

    Attributes:
        reason: The sentence the page shows.
        whole_payday: The first later payday paid entirely at the new pay, for
            the page's link to its stub, when a raise starts inside this
            paycheck (ruling **plan#3959**); else ``None``.
    """

    reason: str
    whole_payday: date | None = None

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
        return _raise_inside(basis, calendar, payday, inside)
    held = pay_list_service.entry_on(profile.id, payday)
    if held is None:
        refusal = pay_list_service.record_refusal(profile, ctx, payday, today)
        if refusal is not None:
            return NoPayOffer(refusal)
    return PayOffer(
        payday, pays_now, stub_base, pay_list_service.reach_of(profile, payday), held,
    )


def _raise_inside(
    basis: PayrollBasis, calendar: PayCalendar, payday: date,
    inside: "tuple[tuple[date, RaiseTerms], ...]",
) -> NoPayOffer:
    """Say why a paycheck a raise starts inside offers no button, and where to look instead.

    Each raise is named in the banner's own words
    (:attr:`~app.services.pay_banner.ForecastRaise.label`) with the day it
    starts.  The sentence holds whether or not a weekday of the paycheck
    falls before the raise: a raise starting the Monday after the pay period
    opens on a Sunday leaves every weekday at the new pay, and pay recorded
    from that payday would still take the raise again.

    Args:
        basis: The read pass's walk for the profile.
        calendar: The owner's calendar.
        payday: The stub's payday.
        inside: The raises starting inside its paycheck, as
            :meth:`~app.services.payroll_basis.PayrollBasis.raises_inside`
            answers them.

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
    whole = _first_whole_payday(basis, calendar, payday)
    if whole is not None:
        reason += (
            f"  Use your {whole.isoformat()} stub, the first paycheck paid "
            f"entirely at the new pay."
        )
    return NoPayOffer(reason, whole)


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
