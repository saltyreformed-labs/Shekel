"""The CONTRACT's cash: each installment a loan still owes, paid as its lender bills it.

Plan step **recurrence:R16-f** (ruling **R-R53**).  A loan's contract line --
what it is charged and pays if every remaining installment is paid as the
contract bills it -- is the ONE replay
(:func:`.._replay.replay_loan_events`) run over the loan's recorded facts
with a third cash source behind them: these projections, beside the plan's
(:attr:`.._replay.LoanEventStream.projections`) and the settled payments.
So the contractual, the planned and the settled views are one fold with
three cash sources, and the contract's figures cannot part from the
balance and the history the same facts give.  Until this step the contract
line was a walk of its own (``amortization_engine.project_forward``), with
its own seed, its own extra clamp and its own closing rule.

Each projection is one installment the record has not reached
(:func:`~app.services.installment_calendar.installment_after` the record's
last event, ruling plan#3382) through the contract's last
(:func:`~app.services.installment_calendar.final_installment_date`):

* its cash is the installment's level P&I and its escrow, read off the
  installment's own charge (:func:`.._charges.contract_charges`, the one
  statement of an installment's rate period and escrow), and the term's last
  is a CLOSING payment (:class:`.._replay.ClosingCash`), which the replay
  resolves to whatever closes the loan when it walks it;
* it is dated at its installment in contract time, and counts from its due
  date or the day after the read, whichever is later
  (:func:`.._visible.projection_visible_on`): an installment due before the
  read that nothing paid is billed the day after the read and splits as its
  installment would, as the plan treats a recurring payment's missed
  occurrence (rulings plan#3983, plan#4029).

Pure: plain data in, plain values out.  No I/O, no clock, no Flask.  All money
is :class:`~decimal.Decimal`.
"""

from datetime import date

from app.services.installment_calendar import (
    final_installment_date,
    installment_after,
)
from app.utils.money import round_money

from ._charges import LoanCalendar, contract_charges
from ._replay import (
    ClosingCash,
    LoanCashEvent,
    LoanEventStream,
    latest_recorded_on,
)
from ._visible import projection_visible_on


def contract_projections(
    facts: LoanEventStream, calendar: LoanCalendar, as_of: date,
) -> list[LoanCashEvent]:
    """Return one projection per installment the contract still bills after *facts*.

    The contract's remaining installments as cash events, for the seam to
    append to the facts and replay: every installment from the first due
    strictly after the record's last event
    (:func:`.._replay.latest_recorded_on`: its latest payment's own due date
    or its latest assertion's date) through the term's last.  None is dated
    on or before that event, so the replay never pushes one past a recorded
    fact (:func:`.._replay.projection_boundary`).  A record already past the
    term leaves none.

    Each one's cash is ``round_money(period_pi + escrow)`` of its own
    installment, as its charge states them, save the term's last, which
    closes the loan (:attr:`.._replay.ClosingCash.WHAT_CLOSES_THE_LOAN`).
    Each is visible from ``max(due, as_of + 1d)``
    (:func:`.._visible.projection_visible_on`), so a missed installment --
    one due before the read, which no record reaches because every
    installment here is after the record's last event -- is billed the day
    after the read at its own installment.  Made-up figures (ruling
    plan#4029's): $3,000.00 at 5% over three months from Jan 15, nothing
    paid, read Mar 20: $1,008.34 due Feb 15 and Mar 15, both visible Mar 21,
    and the closing payment due Apr 15.

    The charges are read from origination, as the stream's own composer
    charges it (:func:`.._replay.with_contract_charges`), so each
    installment's figures come off the one statement of them rather than a
    second lookup of its rate period and escrow.

    Args:
        facts: The loan's recorded stream; its latest recorded fact -- the
            opening assertion at the least, which every production stream
            carries -- is where the contract's remaining installments begin.
        calendar: The loan's contract terms (:class:`.._charges.LoanCalendar`),
            the ones *facts* was charged on.
        as_of: The read's as-of, the day before which no projection counts.

    Returns:
        The projections in installment order, each carrying the installment's
        :class:`.._charges.AccrualCharge` as its ``source``.
    """
    first = installment_after(
        calendar.origination_date, calendar.payment_day,
        latest_recorded_on(facts),
    )
    last = final_installment_date(
        calendar.origination_date, calendar.payment_day, calendar.term_months,
    )
    return [
        LoanCashEvent(
            on_date=charge.on_date,
            cash=(
                ClosingCash.WHAT_CLOSES_THE_LOAN if charge.on_date == last
                else round_money(charge.period.period_pi + charge.escrow)
            ),
            source=charge,
            visible_on=projection_visible_on(charge.on_date, as_of),
        )
        for charge in contract_charges(calendar, last)
        if charge.on_date >= first
    ]
