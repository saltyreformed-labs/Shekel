"""A loan's payment INSTALLMENTS -- which contractual payment each row satisfies.

Plan step **balance:X-bl-2a** (finding **N-432**).  A loan payment has a RECORD
half and a PLAN half, and until this module they arrived only fused: the sole
producer of "which installment each of this loan's payments satisfies, and when
its cash moved" was
:func:`app.services.loan_payment_service.get_payment_history`, which also PRICES
every row through the amount model.  A consumer that needs the DATES therefore
loaded the whole pricing tier and inherited its refusals, so an
``AmountUnresolvable`` broke readers that never look at a figure.  This module
answers the date question alone.

**The closure figures, dated because a measurement quoted as a REASON decays
invisibly -- and every one of these has decayed at least once.**  Assembling the
feed through ``get_payment_history`` reaches **98** modules at ``e74f7d6d``,
**101** at ``2625963a`` (X-bl-2a) and **102** here; the replay's own tier is
**42**, **45** and **46** across those same three trees; ``rate_period_engine``
alone is **6** and **7**, with zero models and zero ``db``.  Two package edits
moved every one of them: splitting ``loan_loaders`` into a package added three
modules to any closure containing it (X-bl-2a), and ``amortization_engine
._dates`` added one (X-bl-2b).  **The gap is CLOSED at X-bl-2b**, which moved
the consumer -- the reconciliation oracle's reference and five other suite
copies of the un-seeded replay -- onto :func:`payment_installments`: **101 at
``2625963a``, 46 here**, and the pricing route on this tree is 102, so the
amount-free feed removes **56** modules like-for-like.

**It is also where a loan's settled history stops having TWO producers.**
:func:`app.services.loan_loaders.settled_income_legs` calls itself the
project's single "which payments are settled, and in what order" derivation;
``get_payment_history`` was not among its consumers and asked the question again
as ``txn.status.is_settled``.  The two agreed -- ``settled_status_ids()`` is
derived from that very column, and ``TestSettledStatusIds`` pins the parity --
but agreement is not the test (``CLAUDE.md`` rule 14).  :func:`payment_installments`
reads the settled set and the projected set, so a row's settled-ness is decided
by WHICH SET it came from and is never re-derived here.

**The SCHEDULE SLOT is deliberately not here.**  The ``due_date`` a payment
satisfies is a fact (:func:`app.services.loan_loaders.loan_payment_due_date`);
the monthly schedule row it consumes is not, because biweekly pay periods
sometimes put two payments in one due month and a monthly engine that summed
them would double-count that month.  That assignment is
:func:`app.services.amortization_engine.schedule_dates`, which INVENTS a date for
the loser of a collision -- which is why the replay keys its RATE on the
pay-period start instead
(:func:`app.services.rate_period_engine.replay_schedule`, finding **N-36**).  It
lives in the pure engine and not here so BOTH feeds reach it without a loader in
scope: hosting it HERE put ``loan_payment_service._engine_prep`` -- pure
arithmetic -- on a **46-module** import closure against the **15** it had at
``e74f7d6d`` (**16** here, ``_dates`` included), which is the very defect these
steps exist to remove,
rebuilt one module over.  An installment carries facts; the slot is a derivation
over the whole feed.

*Every closure figure in this step is the same metric, stated because they keep
decaying: modules under ``app/`` reachable by import from the named roots,
counting the roots and excluding the ``app`` package itself (its ``__init__`` is
the application factory and reaches everything).  A package root seeds every
module inside it.  Measured 2026-09-09 on ``e74f7d6d``, on ``2625963a`` and on
this tree, by the reconciliation oracle's own closure walker
(``test_posting_ledger_loan_reconciliation/test_the_resolver_is_ledger_free.py``), which
is the code the check reads -- not a second implementation of the metric.*

Pure except for :func:`payment_installments`, which reaches the loan loaders for
its rows exactly as :func:`~app.services.loan_ledger.walk_loan_ledger` does.
"""

from dataclasses import dataclass
from datetime import date

from app.models.loan_params import LoanParams
from app.services.amortization_engine import PaymentDates
from app.services.loan_loaders import income_legs, loan_payment_due_date
from app.services.transfer_legs import TransferLeg

from ._visible import payment_visible_on


@dataclass(frozen=True)
class PaymentInstallment:
    """One loan payment's THREE dates and the row they were read off.

    The RECORD half of a loan payment paired with its source row, so a consumer
    that needs the chronology does not load the tier that prices it.  See the
    module docstring for what that tier costs.

    **It COMPOSES the dates rather than restating them** (plan step
    **balance:X-bl-2b**).  :class:`~app.services.amortization_engine.PaymentDates`
    is the one home for a payment's three dates, and
    :class:`~app.services.amortization_engine.PaymentRecord` -- this value's
    priced sibling -- composes the same type, so handing the replay an
    amount-free feed is an attribute read rather than a projection that could
    drift from the priced one.  The three fields lived here directly until that
    step, which is how the FUNDING basis came to carry two names (``period_start``
    here, ``payment_date`` there) for one fact.

    All three dates are read through the derivation that already owns each, so
    this value introduces none of its own: the funding period off the parent
    transfer's :class:`~app.models.pay_period.PayPeriod`, the due date through
    :func:`app.services.loan_loaders.loan_payment_due_date`, the cash day through
    :func:`._visible.payment_visible_on`.  The ``due_date`` here is always the
    payment's OWN due date, never the slot
    :func:`~app.services.amortization_engine.schedule_dates` may invent for it;
    a consumer that needs slots applies
    :func:`~app.services.amortization_engine.slotted_dates` to the feed.

    Attributes:
        source: What this installment was read off: the
            :class:`~app.services.transfer_legs.TransferLeg` of its parent
            transfer, settled or projected alike (plan step
            **balance:X-bi-6-4b**; a settled payment was its loan-side income
            SHADOW row from X-bi-6a until then, and a projected one has been a
            leg since X-bi-6a, ruling **R-BAL13**).  A settled leg carries its
            covering movement as its ``record``.  Carried so a caller that
            ALSO needs the source -- pricing it -- takes it from here rather
            than issuing a second query, the same reason
            :class:`~app.services.loan_ledger.PaymentOutcome` carries one on
            its event.  Whether it has happened is ``dates``' to say, never
            the source's shape.
        dates: The payment's
            :class:`~app.services.amortization_engine.PaymentDates` -- its
            funding period, its own due date, and the day its cash
            moved.  ``settled_on`` is non-``None`` exactly for a row from the
            settled set, which is what makes "has this happened?" a property of
            the query rather than of a second reading of the status column.
    """

    source: TransferLeg
    dates: PaymentDates


def payment_installments(
    account_id: int, scenario_id: int, params: LoanParams, *,
    options: tuple, leg_options: tuple,
) -> list[PaymentInstallment]:
    """Return a loan's payments as their DATES alone, in payment order.

    The project's single "which installment does each of this loan's payments
    satisfy, and has its cash moved?" derivation, and the amount-free half of
    :func:`app.services.loan_payment_service.get_payment_history`, which is built
    on it.  A consumer that needs only the chronology -- the schedule replay's
    feed is the whole of it -- takes this and never loads the amount model.
    A caller feeding the replay applies
    :func:`app.services.amortization_engine.slotted_dates` to
    ``[installment.dates for installment in ...]`` first, which is the same
    collision assignment the priced feed's
    :func:`~app.services.loan_payment_service.prepare_payments_for_engine`
    applies.

    **Settled-ness is read off the PARTITION, not derived here.**
    :func:`app.services.loan_loaders.income_legs` is the app's one answer to
    "which of this loan's payments have happened", and this asks it: a row
    carries a cash day iff it arrived in that producer's ``settled`` half.  This
    module states no rule that could disagree with the fold's, because it states
    no rule at all.  ``get_payment_history`` re-read ``txn.status.is_settled``
    until plan step **balance:X-bl-2a**, which was a second expression of one
    fact held true by a pinned parity.

    **The order is ``(pay_period.start_date, id)``, and the tie-break is new.**
    It is the producer's order, applied to the two halves merged.
    ``get_payment_history`` ordered by ``PayPeriod.start_date`` in SQL with no
    tie-break, leaving two payments in ONE pay period in whatever order the
    database returned them.  The order reaches exactly one decision --
    :func:`~app.services.amortization_engine.schedule_dates`, where it settles
    which of two payments colliding on a due month keeps it -- so the change
    reaches only payments sharing a pay period, and it replaces an arbitrary
    answer with a stable one.  *It is NOT confined to a same-DUE-month pair: in a
    cascade, two same-period payments whose due months differ can still swap
    slots when an earlier payment has taken the first of them.  Neither live loan
    has a collision at all (0 on 58 shadows, 2026-09-09), so nothing moves
    today.*

    **The tie-break is the PARENT transfer's id since plan step
    balance:X-bi-6a**, when the two halves stopped sharing a row id space (a
    settled payment was a shadow row, a projected one a leg), and since plan
    step balance:X-bi-6-4b both halves ARE legs, so the key is each leg's
    ``transfer.id`` with no mapping at all.  The parent's id orders payments
    as the shadow's id did wherever a transfer's shadows were written with it
    (0 inversions measured on the 2026-09-23 21:17 production dump).  One key
    over both halves rather than a rule for which half goes first.

    Args:
        account_id: The loan account whose payments to read.
        scenario_id: The budget scenario to scope to.
        params: The loan's :class:`~app.models.loan_params.LoanParams`, the
            stored home of the two facts that place a payment on its
            installment grid: ``payment_day`` reconstructs the due date of a
            payment that stores none, and with ``origination_date`` it dates a
            ``$0.00`` close by its interval's installment (rulings
            **R-BAL139**, **R-R107**, :func:`._visible.payment_visible_on`).
            Taken whole since R-R107 rather than as two values, so this
            function's callers hand it one loan's row, never a due day and an
            origination read from two places.
        options: The loader options for every relationship the CALLER will
            traverse on the SETTLED legs' parents, rooted at
            :class:`~app.models.transfer.Transfer`.  ``()`` for a caller that
            reads only the dates -- the schedule replay's reference -- and,
            since the settled half is valued from its RECORD, ``()`` for
            ``get_payment_history`` too: nothing on that valuation's path
            walks a relationship.  THIS function's own reads need only the
            pay period, and the producer loads that itself.
        leg_options: The same statement for the PROJECTED legs' parents,
            rooted at :class:`~app.models.transfer.Transfer`: ``()`` for the
            dates alone, ``transfer_pricing_load_options()`` for a caller that
            prices them, which is what ``get_payment_history`` does.

    Returns:
        Every non-excluded income payment on the account as a
        :class:`PaymentInstallment`, ascending by ``(pay_period.start_date,
        parent transfer id)``; ``[]`` when the loan has no payment history.

    Raises:
        UndatedSettleError: When a settled payment's record carries no
            ``settled_on`` -- raised by :func:`._visible.payment_visible_on`,
            because dating a settled payment by a fallback would put real money
            on a day nothing recorded.
        ValueError: Propagated from
            :func:`app.services.loan_loaders.income_legs` for a transfer
            whose status it cannot place.  Named here because this function widened
            that refusal's reach: the partition is now also behind
            ``walk_loan_ledger``, ``confirmed_shadows_through``, the posting
            reader and the escrow forward-only guard, so a broken status seed
            is loud on every loan surface rather than on one.
    """
    payments = income_legs(
        account_id, scenario_id, options=options, leg_options=leg_options,
    )
    # Each half arrives in its own order; the merge key is the PARENT's id,
    # which every leg carries (see the docstring).
    dated: list[tuple[TransferLeg, date | None]] = [
        (
            leg,
            payment_visible_on(
                leg, params.origination_date, params.payment_day,
            ),
        )
        for leg in payments.settled
    ]
    dated += [(leg, None) for leg in payments.projected]
    dated.sort(
        key=lambda entry: (entry[0].pay_period.start_date, entry[0].transfer.id),
    )
    return [
        PaymentInstallment(
            source=source,
            dates=PaymentDates(
                period_start=source.pay_period.start_date,
                due_date=loan_payment_due_date(source, params.payment_day),
                settled_on=settled_on,
            ),
        )
        for source, settled_on in dated
    ]
