"""Whose movements the cash fold and the worked-out ledger read: the one scope.

A DATED movement is read by two readers.  The cash fold reads one account's
at a time (:func:`~._events.settled_cash_facts`, beside its un-dated twin
:func:`~._events.in_flight_movements`); the worked-out ledger reads every
account one owner holds at once (:func:`owner_dated_movements`, plan task
plan#2526).  This module is the clause set both narrow by, stated once so
neither can come to admit a movement the other does not: the plan-row arm's
clauses (:func:`_movements_of`) and the transfer arm's (:func:`_dated_arms`),
each taken over the caller's SCOPE -- one account's (:func:`_on_account`) or
one owner's.

They were ``_events``' own, stated over an ``account_id``, until that task
took them over an owner and moved them here: the event module stood 11 lines
under the 1,000-line ceiling, and WHOSE movements is the seam the ceiling was
measuring.

Reads only: no write, no flush.
"""

from __future__ import annotations

from sqlalchemy.orm import contains_eager

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services.transfer_legs import (
    PlanItem,
    TransferLeg,
    recorded_transfer_legs,
)
from app.utils.balance_predicates import balance_contributing_clause


def _on_account(account_id: int):
    """Return the scope clause of one account's movements: the fold's.

    **The MOVEMENT's own account, never its parent's** (ruling **R-BAL75**):
    a movement is counted where its money moved, and its plan row's
    ``account_id`` is where the row was EXPECTED to be paid from.  The two
    were held equal by ``fk_transaction_entries_parent_account`` until plan
    step ``credit_card:CC-5-1`` dropped it (ruling **R-BAL76**) ahead of the
    card's doors, which write the first movements whose account is not their
    parent's; the posted ledger already attributes by the movement
    (``ledger_lines.purchase_target``), so the fold, the ledger and the
    anchor self-heal read one predicate rather than agreeing by a key.

    Args:
        account_id: The account the movements are ON.

    Returns:
        The clause over ``TransactionEntry``.
    """
    return TransactionEntry.account_id == account_id


def _movements_of(scope, scenario_id: int, *narrowing):
    """Return the PLAN-ROW movements in *scope* WITH their parents, scoped once.

    The ONE statement of what the plan-row movement stream is scoped by --
    shared by the SETTLED tier (:func:`~._events.settled_cash_facts`, the
    dated movements, through :func:`_dated_arms`) and the IN-FLIGHT tier
    (:func:`~._events.in_flight_movements`, the
    un-dated ones) so the two are a PARTITION of the same set rather than two
    filters that could disagree about which movements exist at all (plan step
    ``balance:X-bi-4a``).  Four clauses, each load-bearing:

    * *scope* -- WHOSE movements: one account's for the fold
      (:func:`_on_account`), or every account one owner holds for the
      worked-out ledger (:func:`owner_dated_movements`, plan task
      plan#2526), so the fold and the ledger narrow a movement by every
      other clause here alike.  It was an ``account_id`` argument, the
      fold's alone, until that task.
    * the parent's ``scenario_id`` -- a movement's scenario is its plan row's,
      read through ``transaction_id`` and never copied (ruling **R-BAL35**).
    * the parent is BALANCE-CONTRIBUTING -- the shared
      :func:`~app.utils.balance_predicates.balance_contributing_clause`, so a
      soft-deleted or Credit / Cancelled parent's movements are worth nothing
      here, as they post nothing (``ledger_lines.purchase_posts``).
    * the parent is a PLAN ROW, by the join on ``transaction_id``: a
      transfer side's movement names no row (plan step
      ``balance:X-bi-6-4d-2``, ruling **R-BAL88**) and is read with its
      transfer by :func:`_dated_arms`' other arm.

    Deliberately NOT narrowed by the parent's STATUS: a dated purchase
    against a still-Projected envelope has left the bank exactly as one
    against a closed envelope has, and an un-dated purchase against a closed
    envelope is in flight exactly as one against an open envelope is
    (ruling **R-BAL77**).

    The parent rides along through ``contains_eager`` on the join that
    already scopes the query, so no movement costs a second SELECT for its
    direction, budget column or type.  (It was ``movements_with_parents``,
    public, from plan step ``balance:X-bi-6-3`` -- the posting writer's
    transfer-family loader shared the join -- until leaf ``X-bi-6-4a`` moved
    that loader onto ``transfer_legs``' join, where a transfer movement's
    parent is its LEG, not its shadow row.)

    Args:
        scope: The clause over ``TransactionEntry`` naming whose movements:
            :func:`_on_account`'s, or the owner's.
        scenario_id: The budget scenario the parent rows live in.
        *narrowing: The tier's own clauses over ``TransactionEntry``.

    Returns:
        The matching ``TransactionEntry`` rows, each with ``.transaction``
        populated, unordered.
    """
    return (
        db.session.query(TransactionEntry)
        .join(Transaction, TransactionEntry.transaction_id == Transaction.id)
        .options(contains_eager(TransactionEntry.transaction))
        .filter(
            scope,
            Transaction.scenario_id == scenario_id,
            balance_contributing_clause(),
            TransactionEntry.is_credit.is_(False),
            *narrowing,
        )
        .all()
    )


def _dated_arms(
    scope, scenario_id: int,
) -> tuple[list[TransactionEntry], list[TransferLeg]]:
    """Return the DATED movements in *scope*, a plan row's and a transfer leg's.

    **Two arms** (leaf ``X-bi-6-4a``, ruling **R-BAL106**): a plan row's
    movements (:func:`_movements_of`), and each transfer LEG's
    (:func:`app.services.transfer_legs.recorded_transfer_legs`) under the
    same scope stated over the TRANSFER, its period, side and figure read
    there.  **The transfer arm states no contributing gate in
    SQL** (finding **BAL-534**, deleted at plan step ``balance:X-bi-6-4d-2``
    rather than graded): a DATED side record cannot sit under a deleted
    transfer (``app.deleted_row_infrastructure``'s transfer arm) nor under
    one that is not Paid or Received (``app.side_band_infrastructure``), so
    the filter selected nothing the dated narrowing did not; the walk's
    :func:`~._cash_leg.movement_cash_leg` still asks the leg's gate, and the
    worked-out ledger's ``ledger_lines.purchase_posts`` asks it too.

    **Three narrowings, each load-bearing** (:func:`_movements_of` holds the
    scope, scenario and contributing gate):

    * ``settled_on IS NOT NULL`` -- the trigger itself (ruling R-FM as
      refined by **R-FR**): the day the bank was seen to take the money.
      Whether a STATEMENT was recorded as showing it is
      :class:`~._clearing.StatementCoverage`'s separate question.
    * ``is_credit IS FALSE`` -- a card purchase never touches this account;
      it leaves later through its own CC Payback sibling (the card arc
      retires the flag at CC-7, when a card purchase is a movement on the
      card).
    * the parent is BALANCE-CONTRIBUTING -- a soft-deleted or Credit /
      Cancelled parent's movements are worth nothing, as they post nothing.

    These were :func:`~._events.settled_cash_facts`' own two queries until
    plan task plan#2526 gave the worked-out ledger the same two.

    Args:
        scope: The clause over ``TransactionEntry`` naming whose movements
            (:func:`_movements_of`).
        scenario_id: The budget scenario the parents live in.

    Returns:
        ``(plan-row movements, transfer legs)``: the first each with its
        ``.transaction`` populated and unordered, the second in movement-id
        order, each leg holding its movement as its record.
    """
    return (
        _movements_of(scope, scenario_id, TransactionEntry.settled_on.isnot(None)),
        recorded_transfer_legs(
            scope,
            Transfer.scenario_id == scenario_id,
            TransactionEntry.is_credit.is_(False),
            TransactionEntry.settled_on.isnot(None),
        ),
    )


def owner_dated_movements(
    owner_id: int, scenario_id: int,
) -> list[tuple[TransactionEntry, PlanItem]]:
    """Return every DATED movement one owner holds in a scenario, with its parent.

    The worked-out ledger's movement load (plan task plan#2526): the very
    movements :func:`~._events.settled_cash_facts` folds, over every account
    the owner holds rather than one -- the same two queries
    (:func:`_dated_arms`) under an owner's scope, so the fold and the
    ledger admit a movement by one clause set.  TWO statements whatever the
    number of movements, transfers or accounts: the records a report needs
    are read in a fixed number of statements, never one transfer at a time
    (finding plan#2147's requirement on the computed ledger, ruling
    plan#2136).

    **The scope is the movement's own ``owner_id``, never a parent's
    ``user_id``**: a transfer side's record names no plan row, so a row's
    owner cannot scope it, and the movement's column is held to its row's
    owner and to its account's (``fk_transaction_entries_owner_transaction``,
    ``fk_transaction_entries_owner_account``) and, for a side record, to its
    transfer's by the side keys' chain (ruling **R-BAL107**).

    The transfer arm states no contributing gate in SQL (:func:`_dated_arms`),
    so a reader that books lines asks the gate of each parent, as the
    worked-out ledger does through ``ledger_lines.purchase_posts``.

    Args:
        owner_id: The owner whose movements to load.
        scenario_id: The budget scenario their parents live in.

    Returns:
        ``(movement, parent)`` pairs ascending by movement id: the parent is
        the movement's plan row, or the
        :class:`~app.services.transfer_legs.TransferLeg` whose record it is.
    """
    rows, legs = _dated_arms(TransactionEntry.owner_id == owner_id, scenario_id)
    pairs: list[tuple[TransactionEntry, PlanItem]] = [
        (entry, entry.transaction) for entry in rows
    ]
    pairs.extend((leg.record, leg) for leg in legs)
    pairs.sort(key=lambda pair: pair[0].id)
    return pairs
