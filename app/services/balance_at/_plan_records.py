"""Balance-at-T seam -- the two RECORDS a loan's forward plan is made of.

Split out of :mod:`._plan` at plan step **R16-b-2**, when that module passed
pylint's 1000-line ceiling for the second time in its life (the first split,
at R16-a, took the FOLD out into :mod:`._plan_fold`).  A ceiling crossed is a
statement that a module holds more than one subject; the subjects left in
``_plan`` were the VALUES the plan is made of, the ASSEMBLY that builds them
and -- new at R16-b-2 -- the DEFINITION WALK that sums every recurring
transfer into a loan.  The values go here, the walk to
:mod:`._plan_definitions`, and ``_plan`` keeps the assembly, the contract-only
estimate and the charge calendar.  Both siblings import this and nothing
imports them back, so the seam's internal DAG keeps its shape.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.loan_ledger import LoanCalendar


@dataclass(frozen=True)
class PlannedPayment:
    """One forward payment a loan is projected to make -- a RECORD, not a balance.

    The CASH half of :func:`loan_plan`.  It carries the cash a payment will move
    and the two dates the projection keys on -- but no rate, no escrow and no
    balance.  What a period CHARGES is :class:`AccrualCharge`; the balance is the
    FOLD of the two -- the loan's timeline replayed
    (:mod:`._loan_stream`, plan step recurrence:R16-c-1) -- never stored on a
    record.

    **It stopped carrying its own rate and escrow at plan step R16-a**, and the
    reason is the defect that step exists to close.  While a payment carried the
    charge it was to be split against, the fold charged one month of interest per
    payment RECORD -- so the payment count was the clock, and a loan paid twice as
    fast modelled the identical interest (measured on a production clone: 30
    payments of ``$531.94`` fourteen days apart and 30 a month apart both charge
    ``$1,096.34``, split for split).  A rate and an escrow belong to a period of
    TIME; a cash figure and its dates belong to a payment; and the two are now
    separate values.

    Attributes:
        due_date: The installment this payment satisfies (contract time) --
            the row's own ``due_date``, or the date the row generation would
            write for an estimated occurrence would carry
            (:func:`~app.services.recurrence.compute_due_date`).  Orders the
            split walk against the contract's charges and names the installment
            interval the contract-only arm's de-dup reads, so a late or clamped
            settlement never re-splits an installment (ruling R-A).
        effective_date: When the paydown becomes VISIBLE to a balance read --
            ``max(due_date, as_of + 1d)`` (ruling D1: a plan cannot have already
            happened).  For a normal future installment this is its due date; for
            an overdue-but-still-projected one it is tomorrow.
        cash: The cash this payment moves -- what the amount model resolves for
            a PLANNED row, or what it would resolve for the row an ESTIMATED
            occurrence has not written yet
            (:func:`~app.services.cash_ledger.definition_cash`), or the
            contract's own installment for a loan with no definition.
            Escrow-INCLUSIVE as the owner pays it, which is why the charge it
            clears is backed out of principal rather than added to it.
        is_estimated: ``True`` for an occurrence or contractual installment
            no row answers, ``False`` for a real projected-shadow record --
            carried for display / debugging; the fold treats both alike.
    """

    due_date: date
    effective_date: date
    cash: Decimal
    is_estimated: bool


@dataclass(frozen=True)
class LoanForwardPlan:
    """A loan's forward model: what it will PAY, and the terms it is CHARGED on.

    :func:`loan_plan`'s whole answer.  The shape plan step **R16-a** gave it --
    the charges independent of the payments -- holds, and plan step
    recurrence:R16-c-2 took it one step further: the plan carries the loan's
    contract TERMS rather than a charge list of its own (ruling **R-R100**), and
    the leaf charges the whole timeline from them, recorded facts and plan
    alike (:func:`~app.services.loan_ledger.with_contract_charges`).  Charges
    come from the loan's own note and the passage of time, payments from
    whatever the owner's recurring definitions say, so a payment cadence is a
    non-question: a definition emits payments on its own dates and the charges
    do not move.

    Attributes:
        payments: The forward payment records, PLANNED then ESTIMATED, ascending
            by ``(effective_date, due_date)``.  Empty for an account that is not
            a configured loan.
        calendar: The loan's :class:`~app.services.loan_ledger.LoanCalendar` --
            its origination, due day, rate periods and escrow lines -- which
            :func:`~._loan_stream.merged_stream` charges the timeline from.
            ``None`` for an account that is not a configured loan, whose
            timeline is its (empty) recorded stream.
    """

    payments: list[PlannedPayment]
    calendar: LoanCalendar | None


__all__ = ["LoanForwardPlan", "PlannedPayment"]
