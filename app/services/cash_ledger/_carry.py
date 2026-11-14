"""
Shekel Budget App -- Cash ledger: an envelope's UNSPENT BUDGET.

Carry Forward Unpaid splits one envelope's budget across two paychecks: the
source settles at what it spent and the rest rolls into the next paycheck's row
of the same definition (plan step ``balance:X-ax``, ruling **plan#3973**).  The
rollover is RECORDED as a link on the source (``Transaction.rolled_into_id``,
plan#3985) and its amount is DERIVED from the source every time it is read,
never stored, so a purchase added, removed or re-costed on the source later
moves the target's figure with nothing to keep in step (``CLAUDE.md`` rule 14).

**What lives here is the one statement of an envelope's unspent budget**
(:func:`unspent_budget`).  It is what a still-projected envelope holds back from
its account (:func:`._amounts._entry_aware_amount`) and what a settled one
carries forward (:func:`._amount_source.resolve_transaction_amount`), one
quantity read at two moments.  It was ``_amounts._entry_checking_impact`` and
two copies in ``carry_forward_service`` (the execute path and the preview)
until this task; three spellings that agreed are still three spellings.  It is
a module of its own because :mod:`._amount_source` is the rule dispatch, near
its line cap, and a carry is not a fifth rule but a composition over them.

**Where a carry's inputs come from is the row, not the pass** (ruling
**plan#4052**).  The sources carried into a row are a relation of that row
(``Transaction.carried_from``), loaded by
:func:`~app.utils.amount_relationships.pricing_load_options` with its
definition and price history, so the carried amount needs no per-request cache
and no cache can disagree with a link or purchase written in the same request.
A first build held the owner's links in a per-request snapshot on the
:class:`~._amount_basis.AmountBasis`; the developer ruled it a fence.

Boundary discipline (``CLAUDE.md`` Architecture / B6-01): plain data and ORM
rows in, ``Decimal`` out; no Flask import, no writes.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from app.services.row_valuation import purchases_total


def unspent_budget(budget: Decimal, purchases: Iterable) -> Decimal:
    """Return an envelope's UNSPENT BUDGET: its budget less every purchase, floored at zero.

    One expression::

        unspent = max(budget - sum(purchases), 0)

    Read at two moments of one envelope.  While it is Projected, it is what the
    envelope still holds back from its account
    (:func:`._amounts._entry_aware_amount`); once it has settled and a carry
    linked it forward, it is what rolls into the next paycheck's row
    (:func:`._amount_source.resolve_transaction_amount`).  Every purchase
    counts, debit or card, posted or not: a purchase already recorded is not
    budget still to spend, whichever account its money leaves through (ruling
    **R-BAL77**, plan step ``balance:X-bi-4a``).

    **The reservation was three buckets through plan step ``X-bi-3e``, and
    ruling R-BAL77 is what reduced it** (plan step ``balance:X-bi-4a``).  It
    read ``max(E - P - C, U)`` -- the budget less the POSTED debits (already
    cash movements of their own, ruling **R-FM**) and the card purchases
    (which leave through a CC Payback sibling), floored at the UNPOSTED
    debits, so an envelope could never reserve less than the purchases nobody
    had yet seen leave.  That floor was the projection's only home for an
    un-dated purchase, and it lived inside the PROJECTED row's valuation --
    which is why a purchase under a CLOSED envelope had to be booked by the
    row's own leg on the close day instead, a second home for the same
    dollars.  The identity this docstring already carried is what split
    them::

        max(E - P - C, U)  ==  U + max(E - P - C - U, 0)
                           ==  U + max(E - sum(entries), 0)

    -- exact for EVERY sign of ``U`` (a refund is a negative purchase, ruling
    **bank_import:R-II**), checked exhaustively over the four terms.  The
    first term, ``U``, is now the account's movements IN FLIGHT
    (:func:`~._events.in_flight_movements`), one plan item per un-dated
    purchase whatever its parent's status; the second is this function.  So
    an envelope budgeting `$100.00` with a `$120.00` posted purchase and a
    `$40.00` refund not yet posted holds back ``max(100 - 80, 0) = 20.00``
    here and ``-40.00`` in flight: the refund arrives (`+40.00`) and `$20.00`
    of the budget is still expected to leave -- the same ``-20.00`` the
    three-bucket form answered, in two items that each say what they are.

    **What this does NOT ask, and why**: whether a purchase has POSTED.  That
    question moved out of here twice before it left entirely -- a stored
    ``is_cleared`` boolean written by a bulk UPDATE at every true-up, then a
    derivation against the account's latest asserted day (ruling R-DH (d)),
    then the RECORDED statement (ruling R-FL) -- and ruling R-FM made a
    posted purchase a cash movement of its own in the walk, on its own day,
    where :class:`~._clearing.StatementCoverage` asks which statement cleared
    it.  A purchase not yet posted is a movement in flight, held by the plan
    beside this.  So this reduction asks one fact about the row in front of
    it -- what has been recorded against its budget -- and no fact about the
    account or the bank.

    **The floor is against an OVERSPENT envelope and never against a refunded
    one** (developer ruling **bank_import:R-IK**, 2026-09-01).  A merchant
    credit is a NEGATIVE purchase (ruling **bank_import:R-II**), so the sum can
    be negative and the unspent budget can EXCEED the budget: an envelope
    budgeting `$100.00` holding one `-$50.00` refund carries `$150.00` forward
    and settles at `-$50.00`, which together conserve `$100.00` of plan across
    the two paychecks.  It is what ``entry_service.compute_remaining`` answers
    for the same row on screen; capping it at the budget was put to the
    developer with those numbers and refused.  An envelope budgeting `$100.00`
    that spent `$130.00` carries `$0.00`, never `-$30.00`.

    **The purchases, never the family** (ruling **R-BAL68**): a caller hands
    ``Transaction.purchases``, which leaves out the settle's covering movement,
    so a reverted manual close's kept movement is not spend and the whole
    budget rolls.

    Args:
        budget: The envelope's budget: its resolved amount, carried money
            included (:func:`._amount_source.resolve_transaction_amount`).
        purchases: The envelope's purchases (``Transaction.purchases``), each
            exposing ``amount``; empty for none.

    Returns:
        The unspent budget, never negative.
    """
    return max(budget - purchases_total(purchases), Decimal("0"))
