"""
Shekel Budget App -- Cash ledger: the read pass's LOAN-PAYMENT derivation.

:class:`LoanPricing` is amount rule 4's producer: what one loan payment's
installment costs, with each destination loan resolved at most once per read
pass.  Lazy, so a pass that prices no loan payment issues no query.

Sits above :mod:`._loan_installment`, which owns the per-installment rules it
delegates to, and below :mod:`._amount_basis`, which holds ONE of these per
read pass.

**Moved here from ``loan_payment_service`` at plan step X-au-g-2a.**  The
argument for the move is written once, in :mod:`._loan_installment`.

**It is a MEMO now and no longer a repair, and plan step X-au-g-2c-2 is what
took the second half.**  It carried :meth:`live_cash` -- the read-time override
that SUPERSEDED a loan-payment shadow's stored figure on every balance and
display surface -- together with the scenario-wide ``{transfer_id: config}``
map that decided which shadows it fired for.  Ruling **R-FI** deletes a
read-time repair by making the state it repairs unrepresentable, and that is
what a shadow declaring ``PARENT_TRANSFER`` with no stored figure did (plan
step ``balance:X-bi-6-4d-2`` deleted the shadows): there was no stale copy
left to supersede, so the override had nothing to find and
:func:`._amount_source.resolve_transfer_amount` prices the row directly -- at
the PARENT since plan step X-au-f-2 (ruling **R-BAL10**), which is why nothing
below this line names a :class:`~app.models.transaction.Transaction` any more.

**Deleting that map took the package's ONLY ``budget.transfers`` query with
it.**  ``_load_live_payment_configs`` INNER-joined the scenario's transfers
through their template to ``loan_payment_settings`` to discover which of them
were loan payments -- a question the amount model now asks of the parent it was
handed, per row, off a relationship (ruling **R-FK**).  *This paragraph went on
to say these modules "make no statement against that table at all again", and
plan step X-bi-6a made that sentence false BY DESIGN: the plan loader
(:func:`._facts.planned_cash_rows`) derives a still-projected transfer's legs
from ``budget.transfers`` through :mod:`app.services.transfer_legs` (ruling
R-BAL13; Transfer Invariant 5 restated).  What this module still makes no
statement against is that table for a FIGURE of its own: a loan's price is its
terms, never its payment rows.*

**It takes no SCENARIO either, and that is the same deletion one level down.**
The scenario only ever scoped the config map; a loan's terms are not
scenario-scoped, which ``_loan_installment._resolve_loan_basis`` already
recorded when it lost its own scenario argument at plan step X-au-g-1 (*"the
parameter only ever existed to scope the payment rows this no longer reads"*);
the terms are the pass's :class:`~app.services.loan_ledger.LoanCalendars`
since plan step recurrence:R25 deleted that resolver.
The pin a caller can still get wrong -- pricing a row against another
scenario's basis -- is refused by
:func:`._amount_source.resolve_transaction_amount`, which asks the row's own
``scenario_id`` column and is TOTAL where the map's membership never was.
"""

from datetime import date
from decimal import Decimal

from app.services.loan_ledger import LoanCalendars
from ._loan_installment import _installment_cash


class LoanPricing:
    """Each destination loan's terms, resolved at most once per read pass.

    **The DERIVATION half of amount rule 4, split from its per-row lookup at
    plan step X-au-c2b.**  Everything expensive behind a loan payment's figure
    -- the destination loan's rate-period set, its contractual payment day and
    its escrow line history -- is scoped by the LOAN and by nothing about which
    rows a caller happens to have loaded.  Keyed that way, one read pass
    resolves each loan once however many row sets ask
    (:class:`~app.services.cash_ledger.AmountBasis`).

    It was two ``{transaction_id: Decimal}`` producers built per row set until
    that step -- ``live_loan_transfer_amounts`` for a display row set and
    ``live_loan_payment_amount`` for one settling shadow -- and the second
    function's own docstring said it *"mirrors live_loan_transfer_amounts'
    candidate filter ... so the settle capture fires for precisely the set the
    projected override covers"*.  Two implementations of one rule, kept in step
    by hand, is what this class collapsed; the cost of the split had been
    findings **N-268** and **N-269**, a request that priced two row sets paying
    the loan resolve twice.

    **The derivation is LAZY**, so a read pass whose rows hold no loan payment
    pays nothing at all: the per-loan resolve only runs for a loan a payment
    actually names.  That is the "no query when there are no candidates"
    property the row-set producers had, kept rather than traded away.

    **It holds no terms of its own since plan step recurrence:R25** (ruling
    **R-R105**, finding **REC-545**): each loan's terms are the read pass's
    :class:`~app.services.loan_ledger.LoanCalendars` -- the memo the pass's
    loan walk builds its charges from -- so the price and the charges read
    one calendar, loaded once for both.  (The loan resolver's bundle and the
    payoff calculator still load the terms on their own paths in the same
    pass: finding **REC-559**, owned by plan step recurrence:R16-f.)  It
    memoized a second bundle per loan here (``_LoanCashBasis`` and the escrow
    lines) until then.

    **IT READS NO CLOCK, and plan step X-au-g-2b is what deleted the one it
    used to read.**  It took an ``as_of`` and resolved each loan's rate-period
    P&I against it -- one figure per pass, applied to every installment the
    pass priced, which is finding **N-40**.  Ruling **R-IJ** put a loan's
    contractual terms on the INSTALLMENT they govern, as ruling D5 had already
    put a payment's escrow, so there is no pass-level date left to pin: the
    per-loan resolve (the pass's
    :class:`~app.services.loan_ledger.LoanCalendar` since plan step
    recurrence:R25) answers the loan's term SET, which no date parameterises,
    and each payment reads the period governing its own due date.  What a
    whole pass now shares is the derivation rather than an answer.

    **It states no ownership rule and reads no status, which is where it
    differs from the ``live_cash`` it replaced** (plan step X-au-g-2c-2).  That
    method answered ``None`` for an operator ``is_override``, an already-settled
    shadow, or a manual payment with no standing extra, because a read-time
    REPAIR is a question about which stored figure to supersede.  Pricing is
    not: whether a row COUNTS and who last touched it are finding **N-262**'s
    separate question, answered above this tier.
    """

    def __init__(self, terms: LoanCalendars) -> None:
        """Hold the pass's loan terms; resolve nothing until a payment asks.

        Args:
            terms: The read pass's
                :class:`~app.services.loan_ledger.LoanCalendars`.
        """
        self._terms = terms

    def derive_cash(
        self,
        due_date: "date | None",
        period_start: date,
        loan_account_id: int,
        extra_principal: Decimal,
    ) -> "Decimal | None":
        """Return a DERIVE-mode payment's cash: P&I + its installment's escrow + extra.

        **Amount rule 4's derive arm, and it reads no status** -- not
        ``is_projected``, not ``is_override``, not ``is_deleted``.  That is
        finding **N-262**'s rule one tier down: those three say whether a row
        COUNTS and who last touched it, never what prices it.

        **It takes the two DATING COLUMNS rather than the payment SHADOW, and
        plan step X-au-f-2 is why** (ruling **R-BAL10**).  The answer's home is
        the PARENT transfer now, which carries the same two facts and is not a
        :class:`~app.models.transaction.Transaction`; taking the values is what
        lets one producer answer for either row, and is the shape
        :func:`app.services.settle_day.settle_day_from_columns` already takes
        precisely so a transfer can answer it.  The old parameter carried a
        note that either LEG resolved the same figure; the leg is not asked at
        all now, so there is no whichever-is-passed question left.

        Args:
            due_date: The payment's own stored due date, or ``None``.
            period_start: The start date of the payment's pay period -- the
                installment fallback for a payment carrying no due date.
            loan_account_id: The destination loan to resolve.
            extra_principal: The recurring payment's standing extra principal
                (``0.00`` when none), from
                :func:`~app.services.recurring_transfer_query.loan_payment_config`.

        Returns:
            The derived cash, or ``None`` when the loan will not resolve -- an
            account carrying no ``LoanParams``, which rule 4 turns into a
            refusal rather than a fallback to a stored snapshot.
        """
        calendar = self._terms.loan_calendar_of(loan_account_id)
        if calendar is None:
            return None
        return _installment_cash(
            calendar, due_date, period_start, extra_principal,
        )


def loan_pricing(terms: LoanCalendars) -> LoanPricing:
    """Return a read pass's :class:`LoanPricing` over its loan terms.

    The named constructor the amount model calls, so no caller reaches for the
    class directly.  Resolves nothing: the derivation behind it is lazy, so a
    pass that prices no loan payment issues no query.

    It took an ``as_of`` until plan step X-au-g-2b and a ``scenario_id`` until
    X-au-g-2c-2, and the argument for the absence of both is on
    :class:`LoanPricing`: a loan's contractual terms resolve on the installment
    they govern (ruling **R-IJ**) and are not scenario-scoped, so a read pass
    has neither a date nor a scenario to hand this.

    Args:
        terms: The read pass's
            :class:`~app.services.loan_ledger.LoanCalendars`, shared with the
            pass's loan walk (plan step recurrence:R25).

    Returns:
        The unresolved :class:`LoanPricing` handle.
    """
    return LoanPricing(terms)
