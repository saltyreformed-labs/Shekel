"""
Shekel Budget App -- Cash ledger: an envelope's UNSPENT BUDGET, and where a carry put it.

Carry Forward Unpaid splits one envelope's budget across two paychecks: the
source settles at what it spent and the rest rolls into the next paycheck's row
of the same definition (plan step ``balance:X-ax``, ruling **plan#3973**).  The
rollover is RECORDED as a link on the source (``Transaction.rolled_into_id``,
plan#3985) and its amount is DERIVED from the source every time it is read,
never stored, so a purchase added, removed or re-costed on the source later
moves the target's figure with nothing to keep in step (``CLAUDE.md`` rule 14).

Two things live here, and both are below :mod:`._amount_source`:

* :func:`unspent_budget`, the ONE statement of an envelope's unspent budget.
  It is what a still-projected envelope holds back from its account
  (:func:`._amounts._entry_aware_amount`) and what a settled one carries
  forward (:class:`Carries`), one quantity read at two moments.  It was
  ``_amounts._entry_checking_impact`` and two copies in
  ``carry_forward_service`` (the execute path and the preview) until this
  task; three spellings that agreed are still three spellings.
* :class:`Carries`, the read pass's carry links, loaded once per
  :class:`~._amount_basis.AmountBasis` and memoised per target, so a grid of
  carried rows costs one batch rather than a query per row.

**Why a module of its own rather than more of** :mod:`._amount_source`: that
module is the rule dispatch, near its line cap, and a carry is not a fifth
rule.  It is a COMPOSITION over the rules: a row's amount is its own amount, by
whichever rule owns it, plus the unspent budget of every source rolled into it
(:func:`._amount_source.resolve_transaction_amount`).  A row a carry landed in
keeps its definition's price; the carry sits on top.

**What the composition costs a pass, and when it can be stale.**  The first
price a pass asks loads every live carry link of the basis's owner and
scenario, with each source's purchases and pricing relations, in one batch
(:meth:`Carries.carried_into`).  A pass that prices nothing loads nothing.  The
memo is a snapshot as of that first ask: a door that writes a purchase on a
carried source, a link, or a source's price, and THEN prices the source's
target, builds its basis after the write.  The census of the doors that do is
in the commit that wrote this module.

Boundary discipline (``CLAUDE.md`` Architecture / B6-01): plain data and ORM
rows in, ``Decimal`` out; no Flask import, no writes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.services.row_valuation import purchases_total
from app.utils.amount_relationships import valuation_load_options


def unspent_budget(budget: Decimal, purchases: Iterable) -> Decimal:
    """Return an envelope's UNSPENT BUDGET: its budget less every purchase, floored at zero.

    One expression::

        unspent = max(budget - sum(purchases), 0)

    Read at two moments of one envelope.  While it is Projected, it is what the
    envelope still holds back from its account
    (:func:`._amounts._entry_aware_amount`); once it has settled and a carry
    linked it forward, it is what rolls into the next paycheck's row
    (:class:`Carries`).  Every purchase counts, debit or card, posted or not:
    a purchase already recorded is not budget still to spend, whichever
    account its money leaves through (ruling **R-BAL77**, plan step
    ``balance:X-bi-4a``).

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


class Carries:
    """One read pass's carry links, and what each target was carried, memoised.

    Pinned to an owner and a scenario, as every derivation an
    :class:`~._amount_basis.AmountBasis` holds is: a link joins rows of one
    owner, definition and scenario (``fk_transactions_rolled_into``), so the
    owner and scenario's links are every carry the basis can be asked about.

    Lazy: nothing is loaded until :meth:`carried_into` is first asked, and then
    everything is, once.  The memo holds what each TARGET was carried, never a
    row's whole amount: the rules' own answers stay unmemoised, so a price
    restated in a request reaches the next resolve as it always has.
    """

    def __init__(self, user_id: int, scenario_id: int) -> None:
        """Hold the pins; load nothing until a price asks.

        Args:
            user_id: The owner whose carries these are.
            scenario_id: The scenario they were carried in.
        """
        self._user_id = user_id
        self._scenario_id = scenario_id
        self._sources: "dict[int, tuple[Transaction, ...]] | None" = None
        self._carried: dict[int, Decimal] = {}

    def carried_into(
        self, target_id: "int | None", budget_of: Callable[[Transaction], Decimal],
    ) -> Decimal:
        """Return the money carried into row *target_id*: its sources' unspent budgets.

        ::

            carried(target) = sum(unspent_budget(budget_of(source), source.purchases)
                                  for each live source linked into target)

        ``budget_of`` is :func:`._amount_source.resolve_transaction_amount`
        over the same basis, handed in because that module composes this one
        and so sits above it.  A source's budget is its resolved amount, carried
        money included, so a CHAIN composes: a row carried into, settled and
        carried on passes forward what it received and did not spend.  The
        recursion ends because a link needs a settled source
        (``ck_transactions_carry_needs_settle_day``) and the carry-forward only
        rolls into a row that has not settled, so no chain returns to a row in
        it.

        **The source's budget is its CURRENT resolved amount**, decided under
        ruling **R-BAL207** at this card's design: a price restated from before
        the source's paycheck moves its carry, because freezing the budget at
        the carry would be a stored copy of a derived value.  A `$500.00`
        budget with `$57.96` spent carries `$442.04`; restated as `$450.00`, it
        carries `$392.04`.

        A deleted source carries nothing: its budget left the plan with it.  A
        row with no id yet (an unflushed one) has no link naming it, so it is
        carried nothing.

        Args:
            target_id: The id of the row being priced.
            budget_of: The resolver for a source's budget, always the same one
                for one :class:`Carries` (the memo assumes it).

        Returns:
            The carried amount, ``Decimal("0")`` for a row nothing rolled into.
        """
        if target_id not in self._carried:
            self._carried[target_id] = sum(
                (
                    unspent_budget(budget_of(source), source.purchases)
                    for source in self._sources_into(target_id)
                ),
                Decimal("0"),
            )
        return self._carried[target_id]

    def _sources_into(self, target_id: "int | None") -> "tuple[Transaction, ...]":
        """Return the live sources linked into *target_id*, loading every link once.

        One statement for the links, plus the selectin loads of their sources'
        purchases and pricing relations (``valuation_load_options``), whatever
        the number of carries or the length of a chain: the sources of a chain
        are themselves linked rows, so they are all in the one batch.

        Args:
            target_id: The id of the row being priced.

        Returns:
            Its live sources, empty when none rolled into it.
        """
        if self._sources is None:
            sources: dict[int, list[Transaction]] = {}
            for source in (
                db.session.query(Transaction)
                .filter(
                    Transaction.user_id == self._user_id,
                    Transaction.scenario_id == self._scenario_id,
                    Transaction.rolled_into_id.isnot(None),
                    Transaction.is_deleted.is_(False),
                )
                .options(*valuation_load_options())
                .order_by(Transaction.id)
            ):
                sources.setdefault(source.rolled_into_id, []).append(source)
            self._sources = {
                target: tuple(rows) for target, rows in sources.items()
            }
        return self._sources.get(target_id, ())
