"""
Shekel Budget App -- A loan's LOADED contract context: its rate history and escrow.

One account's rate changes and escrow, loaded once and shared by the two
readers that need them (:func:`load_loan_context`): the balance seam's loan
resolution (:func:`app.services.balance_at._resolution.resolve_loan_bundle`),
which hands the rate-change feed to the resolver, and the loan route's read
pass (``app/routes/loan/_helpers._load_route_context``), whose schedule and
escrow card read the escrow beside it.

**It held a loan's PAYMENT LIST until plan task plan#3388** (plan step
recurrence:R16-e, ruling **R-R93**).  ``LoanContext.payments`` was every
transfer into the loan priced through the amount model
(``get_payment_history``) and then prepared for the schedule replay
(``prepare_payments_for_engine``), whose escrow-subtraction FLOOR,
``amount - min(escrow, amount - contractual_pi)``, was a second allocation rule
beside the walk's ``app.utils.money.apply_payment_cash``: on a short payment it
reported exactly the contractual P&I and the shortfall vanished, in the
optimistic direction (finding **N-409**).  ``LoanContext.contractual_pi``
(``compute_contractual_pi``) was the threshold that floor keyed on.  The list
existed only to feed the replay -- walk 3 over a loan, which the same step
deleted first (plan task plan#3385) -- and nothing else read it: what a loan
PAID is its walk's (``loan_ledger.replay_loan_events``) and what it is planned
to pay is the balance seam's plan fold.  So the read pass's amount basis, which
priced the list, is no longer an input here, nor is the loan's ``LoanParams``,
which dated it.  What is left is the loan's contract facts, which no scenario
scopes.

It was a package of two leaves, one per verb, until the same task: the
preparation leaf held the floor and the contractual P&I it keyed on and nothing
else, so with both gone one leaf was left and the split went with them.

It reads the POSTED LEDGER nowhere (plan step E1d-b, which moved the loan
resolver's confirmed slice onto the balance seam's event walk) and imports
nothing from the seam, which imports it.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.services import escrow_calculator
from app.services.amortization_engine import RateChangeRecord
from app.services.loan_loaders import (
    _rate_change_records_from,
    load_escrow_lines,
    load_rate_history,
)


@dataclass
class LoanContext:
    """A loan's contract context -- its rate history and escrow -- loaded once.

    Loaded once per account by :func:`load_loan_context` and read by the
    balance seam's loan resolution and the loan route's read pass.  Every field
    is a CONTRACT fact of the loan, so none is scoped to a scenario: an owner
    with no baseline scenario loads exactly what an owner with one does, which
    is what keeps escrow and rate editing working for them (plan step C8e,
    ruling **R-BX**).

    Attributes:
        rate_changes: List of RateChangeRecord for the loan -- its
            origination row plus any ARM adjustments (DH-#56: every loan
            carries an origination row).  ``None`` only for a loan with
            no RateHistory rows at all, which the origination-row
            invariant forbids in production.
        escrow_components: The loan's escrow lines resolved to their in-effect
            version on today (:class:`~app.services.escrow_calculator.ResolvedEscrowLine`).
            The loan CARD's escrow set: what the owner pays this month.  It is
            NOT an installment's escrow -- every tier that prices one resolves
            its own on that installment's due date (ruling **R-IJ**) -- and the
            amortization schedule stopped reading it at plan step X-au-g-2b
            (finding **N-410**).
        monthly_escrow: The sum of ``escrow_components``: the card's monthly
            escrow figure, on the same footing and with the same limit.
        rate_history: RateHistory ORM objects for rate display.  Carries
            the origination row for every loan plus any ARM adjustments;
            the loan dashboard shows the table only for ARM loans.
        escrow_lines: The account's escrow LINES with their full version
            history (:class:`~app.models.escrow_line.EscrowLine`), the raw
            source ``escrow_components`` is resolved from.  Kept so the loan
            dashboard builds the escrow card's version drawer
            (:func:`app.services.escrow_calculator.build_escrow_card`) off the
            same load, rather than re-querying the lines.
    """

    rate_changes: list[RateChangeRecord] | None
    escrow_components: list  # list[ResolvedEscrowLine]
    monthly_escrow: Decimal
    rate_history: list = field(default_factory=list)  # list[RateHistory]
    escrow_lines: list = field(default_factory=list)  # list[EscrowLine]


def load_loan_context(account_id: int) -> LoanContext:
    """Load a loan account's contract context: its rate history and escrow.

    A pure data-loading function -- no Flask request/session imports.

    **It takes the account alone since plan task plan#3388**, which deleted the
    payment list it also loaded (see the module docstring).  The read pass's
    amount basis priced that list and the loan's ``LoanParams`` dated it, so
    with the list gone neither is read here.  The basis was also the one input
    an owner with no baseline scenario could not supply (they passed ``None``
    and loaded no payments); with no scenario-scoped input left, there is no
    degraded state to spell.

    Args:
        account_id: The loan account ID.

    Returns:
        The account's :class:`LoanContext`.
    """
    # Escrow.  ``escrow_components`` / ``monthly_escrow`` are the CARD's
    # resolution of the lines on today, and the distinction is ruling R-IJ's:
    # what the owner pays this month is a today question, what an installment
    # costs is not.  Every consumer that prices an installment takes
    # ``escrow_lines`` and resolves its own (``build_schedule_context`` per
    # confirmed row).  ``monthly_escrow`` equals
    # ``escrow_monthly_as_of(escrow_lines, today)`` by construction.
    escrow_lines = load_escrow_lines(account_id)
    escrow_components = escrow_calculator.resolve_active_lines(
        escrow_lines, date.today(),
    )
    monthly_escrow = escrow_calculator.calculate_monthly_escrow(
        escrow_components,
    )

    # Rate history for EVERY loan.  DH-#56 retired LoanParams.interest_rate,
    # so the loan's base / period-0 rate lives in its origination RateHistory
    # row; every loan carries one (create_params seeds it on setup; the DH-#56
    # migration backfilled pre-existing loans).  The load is therefore not
    # ARM-gated -- a fixed-rate loan resolves its single rate period from its
    # one origination row.  The raw rows are kept for the ``rate_history``
    # display field; the feed is the same rows mapped for the engine.
    rate_history_records = load_rate_history(account_id)
    rate_changes = _rate_change_records_from(rate_history_records)

    return LoanContext(
        rate_changes=rate_changes,
        escrow_components=escrow_components,
        monthly_escrow=monthly_escrow,
        rate_history=rate_history_records,
        escrow_lines=escrow_lines,
    )
