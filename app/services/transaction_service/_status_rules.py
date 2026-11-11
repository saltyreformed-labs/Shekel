"""
Shekel Budget App -- Transaction Service: the TYPE rule

Which settled status a row of a given TYPE takes -- income is Received, an
expense is Paid -- and the two things that follow from it: the refusal when a
door asks for the other one, and the narrowed set a dropdown may OFFER.

**The dropdown speaks the status a row is DRAWN as** (plan task plan#3054),
so this module also holds what a status POSTED from it means
(:func:`requested_status_id`): a row put on the card is stored Paid and drawn
Credit (``status_seam.drawn_status_id``), the dropdown selects Credit on it,
and an untouched Save posts Credit back.  What it offers and what it posts are
read through the same answer, so they cannot part, and a refusal of what it
posts is said in that status too (:func:`verify_requested_status`, plan task
plan#3057).

**It is a display convention with an enforced consequence**, which is why it is
a leaf of its own rather than a line inside the settle.  Every reader of the
settled band consumes ``settled_status_ids()`` as a SET and cannot tell Paid
from Received; the state machine is keyed on the STATUS and never sees
``transaction_type_id``, so it admits both from Projected.  This module is the
only place that holds the row's type and the status rule together.

Flask-isolated: plain data and ORM rows in, values out, no ``request`` /
``session`` imports, no flush, no commit.
"""

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.services.account_projection import is_revolving
from app.services.state_machine import allowed_transitions, verify_transition
from app.services.status_seam import drawn_status_id, settled_on_a_card
from app.utils.balance_predicates import (
    enters_credit,
    is_identity_move,
    is_projected,
)


def settled_status_member(txn: Transaction) -> StatusEnum:
    """Return the settled STATUS a row of this TYPE takes, as the enum member.

    The type rule, stated ONCE.  :func:`settled_status_id` resolves it to a
    ``ref.statuses.id`` for the writers and
    :func:`reject_mismatched_settled_status` reads its ``value`` for the human
    message -- which used to be a third spelling, ``"Received" if txn.is_income
    else "Paid"``, sitting two lines below the resolution it was restating.  A
    message derived from a second copy of a rule tells the user something the
    code may have stopped doing.

    Args:
        txn: The transaction about to settle.  Read for ``is_income`` only.

    Returns:
        ``StatusEnum.RECEIVED`` for income, ``StatusEnum.DONE`` otherwise.  The
        member's ``value`` is the DISPLAY name ("Received" / "Paid"), which is
        what makes one expression answer both needs.
    """
    return StatusEnum.RECEIVED if txn.is_income else StatusEnum.DONE


def settled_status_id(txn: Transaction) -> int:
    """Return the settled status a row of this TYPE takes.

    Income is Received and everything else is Paid.  It is a display
    convention rather than a balance one -- every reader of the settled band
    consumes ``settled_status_ids()`` as a SET and cannot tell the members
    apart -- but it is a convention with TWO former spellings, which is one
    too many for a rule that decides a stored column.

    **Both spellings were live and they agreed by reading.**
    ``app/routes/transactions/mutations.py:mark_done`` picked the id and
    handed it down, and :func:`._settle.settle_from_entries` re-derived the same id
    from the same predicate, its own comment naming the route as the thing it
    "mirrors" -- Section 8's "two spellings that agree by reading are two
    answers until one is deleted", on a money-adjacent rule.  Ruling **R-FA**
    forced the question by giving the reconcile tick the same settle as the
    grid's Mark Paid: a third door would have made it three.

    A transfer SHADOW never reaches here.  Its settle goes through
    ``transfer_service.update_transfer``, which sets Paid on both legs
    because the income/expense split is meaningless for a pair whose whole
    point is that one leg is each.

    Args:
        txn: The transaction about to settle.  Read for ``is_income`` only.

    Returns:
        The ``ref.statuses.id`` for Received or Paid.
    """
    return ref_cache.status_id(settled_status_member(txn))


def _mismatched_settled_status_ids(txn: Transaction) -> frozenset[int]:
    """Return the TYPE-specific settled statuses *txn* does NOT take.

    One expression for the two things that need it, the refusal
    (:func:`reject_mismatched_settled_status`) and the dropdown's pre-hint
    (:func:`offerable_status_ids`).  Stated once so the enforced rule and the
    displayed rule cannot drift, which is the same reason
    :func:`._row_rules.settles_from_entries` is published.

    **It is the TYPE PAIR minus this row's, NOT the settled band minus this
    row's -- and since plan step X-am those two sets are EQUAL, which makes the
    distinction easier to lose and no less real.**  The band held a third
    member, ``Settled``, the archive, which was not type-specific: an expense
    reached it from Paid and an income row from Received.  A first draft
    subtracted the whole band, so ``Settled`` counted as "mismatched" for every
    row and :func:`offerable_status_ids` removed it from every dropdown --
    silently retiring the only control that offered the archive, while the state
    machine still called ``Paid -> Settled`` legal and the seam still preserved
    the settle day across it.  Caught by adversarial review, and two assertions
    written in the same step said the opposite ("the full-edit Status dropdown
    can still reach it"), which is what made it a mistake rather than a
    decision.

    **So the pair stays written out rather than collapsing to
    ``settled_status_ids()``, and the coincidence is the reason, not an
    argument against it.**  The question this asks is *which settled statuses
    belong to a TYPE OTHER THAN THIS ROW'S* -- and "is settled" is a different
    predicate that happens to select the same two rows today.  Reading the band
    here would re-arm the exact defect above the moment a settled status exists
    that no transaction type settles into, which is live territory: the
    credit_card arc's own plan makes ``Credit`` terminal and adds card states.
    A set that must not widen with the band cannot be derived from the band.

    A SET rather than an equality, and deliberately: an inline equality against
    a resolved status id is the D6-09 shape the project's own census test
    refuses outside ``balance_predicates``, and the set form is both honest
    about what is being asked and the one that composes with
    :func:`app.services.state_machine.allowed_transitions`.

    Args:
        txn: The row, read for ``is_income``.

    Returns:
        The ``ref.statuses.id`` values this row's TYPE forbids it settling
        into -- exactly one member, for either type.
    """
    type_specific = frozenset({
        ref_cache.status_id(StatusEnum.DONE),
        ref_cache.status_id(StatusEnum.RECEIVED),
    })
    return type_specific - {settled_status_id(txn)}


def reject_mismatched_settled_status(
    txn: Transaction, new_status_id: int,
) -> None:
    """Refuse a settled status that contradicts the row's TYPE.

    :func:`settled_status_id` is the rule -- income settles as Received, an
    expense as Paid -- and it is the verb's to apply, so a door that asks for
    the OTHER one is asking for something the domain does not have.  The
    alternative to refusing is silent substitution: the row lands in a status
    the user did not pick, with nothing said about it.

    **The state machine cannot make this judgement**, and that is why the rule
    is here.  Its transaction map admits Projected -> Paid AND Projected ->
    Received because it is keyed on the STATUS, not on the row: it never sees
    ``transaction_type_id``.  So the two facts have to meet somewhere, and this
    is the only place that holds both.

    **It refuses nothing that exists.**  Measured on production: 17 rows carry
    an income type in the Paid status and all 17 are transfer SHADOWS, whose
    settle goes through ``transfer_service`` and never reaches here (a transfer
    sets Paid on both legs deliberately -- the income/expense split is
    meaningless for a pair whose whole point is that one leg is each).  Every
    one of the 143 non-shadow settled rows agrees with its own type.

    The full-edit dropdown pre-hints the same rule
    (:func:`offerable_status_ids`), so reaching this refusal takes a crafted
    request or a stale form -- which is exactly the layering the route's own
    purchase-tracking guard uses, and the reason this is a designed 400 rather
    than a programming error.

    Args:
        txn: The row being settled, read for ``is_income``.
        new_status_id: The settled status the door asked for.

    Raises:
        ValidationError: When *new_status_id* is a settled status other than
            the one this row's type takes.
    """
    if new_status_id not in _mismatched_settled_status_ids(txn):
        return
    kind = "Income" if txn.is_income else "An expense"
    takes = settled_status_member(txn).value
    raise ValidationError(
        f"{kind} settles as {takes}.  Transaction {txn.id} was asked to "
        f"settle as status {new_status_id} instead."
    )


def offerable_status_ids(txn: Transaction) -> frozenset[int]:
    """Return the statuses a DOOR may offer for *txn* -- the dropdown's set.

    ``state_machine.allowed_transitions`` narrowed by this module's type rule,
    with the identity move offered as the status the row is drawn as (below).
    The state machine grades the STATUS and cannot see the row's type, so its
    answer for a Projected row contains both Paid and Received; exactly one of
    them is reachable for any given row, and offering the other is offering a
    control that 400s at :func:`reject_mismatched_settled_status`.

    It is the pre-hint half of one rule rather than a second rule, which is the
    shape the status dropdown was already built to (grid audit D2: "options the
    state machine would reject render disabled, so an illegal transition cannot
    be picked instead of failing as a 400 after Save").  The enforcement stays
    at the verb; this only decides what the user is shown.

    **There is deliberately NO exemption for a row already sitting in the
    mismatched status.**  A first draft carried one -- ``- {txn.status_id}``, so
    such a row could still re-submit its own status -- and it was UNREACHABLE:
    the only mismatched rows in existence are the 17 income-typed Paid transfer
    SHADOWS, and ``routes/transactions/forms`` branches a shadow to the transfer
    popover before this is called, while ``TransactionUpdateSchema`` carries no
    ``transaction_type_id`` for a PATCH to flip.  A guard whose only possible
    test cannot fail is not a guard (finding **N-184**), and this module's own
    seam deleted one for that reason at plan step X-f1c.

    **Staying put is offered as the status the row is DRAWN as** (plan task
    plan#3054).  A row on the card is stored Paid and drawn Credit, so its
    identity move is offered as Credit and Paid is not offered at all ("Such
    a row offers no Paid", as an old Credit row offers none); every other move
    is the stored status's, which is the move the door makes
    (:func:`requested_status_id` reads a posted Credit back as the stored
    Paid).  For every other row the drawn status is the stored one and the
    set is the state machine's unchanged.

    Args:
        txn: The row the dropdown is being rendered for.

    Returns:
        The legal successor ids, the identity drawn as the row is drawn, minus
        the settled status this row's type does not take.
    """
    drawn = drawn_status_id(txn)
    offered = frozenset(
        drawn if is_identity_move(txn, status_id) else status_id
        for status_id in allowed_transitions(txn)
    )
    return offered - _mismatched_settled_status_ids(txn)


def requested_status_id(txn: Transaction, posted_status_id: int) -> int:
    """Return the status a door applies for a status POSTED from the popover.

    The read-back half of :func:`offerable_status_ids` (plan task plan#3054):
    the dropdown offers staying put as the status the row is drawn as, so a
    posted status equal to that one means "as it is", the row's stored
    status.  On a row put on the card an untouched Save posts Credit, and the
    door applies Paid, an identity move, so the Actual box, "Money moved on"
    and Notes beside it correct the row on the card.  Read as Credit, the
    state machine refused every such Save (Paid moves only to Projected);
    admitting Paid -> Credit there instead would store status Credit holding
    the movement, which drops the $800 from the card's balance (review B of
    plan#2890).  Every other posted status is the move it names, graded by
    the state machine on the stored status (:func:`verify_requested_status`).

    Args:
        txn: The row the popover was drawn for, before any field is written.
        posted_status_id: The ``ref.statuses.id`` the Status dropdown posted.

    It is one translation, the inverse of the offer's: the drawn identity
    reads as the stored one, and every other id reads as itself.

    Returns:
        ``txn.status_id`` when *posted_status_id* is the status *txn* is drawn
        as, else *posted_status_id*.

    Raises:
        ValueError: From ``status_seam.drawn_status_id``, on a row holding two
            covering movements.
    """
    stored_for_drawn = {drawn_status_id(txn): txn.status_id}
    return stored_for_drawn.get(posted_status_id, posted_status_id)


def verify_requested_status(txn: Transaction, requested: int) -> None:
    """Refuse a move the popover's Save asks for that the row cannot make, in its drawn status.

    The state machine grades the move on the STORED status
    (``state_machine.verify_transition``), and its sentence names that status,
    so on a row put on the card it said "from Paid" in a cell labelled Credit
    (review L4 of plan#3054).  Two refusals are therefore said here in plain
    words, each with what to do instead (plan task plan#3057); every other
    keeps the state machine's sentence, on a row whose drawn status is its
    stored one:

    * **Credit, for a row not in it.**  No row enters the Credit status
      (``state_machine``, finding plan#2133): the Credit button puts a row on
      the card, and a row tracking its purchases puts each purchase there by
      its CC tick, so the popover draws this option disabled and what posts
      it is a crafted request or a stale page;
    * **any other move refused on a row on the card**, whose only moves are
      staying as it is and going back to Projected.

    The PATCH door's early check, for error precedence (``routes/transactions/
    _gates``); the status seam re-runs the state machine's own.

    Args:
        txn: The row the popover was drawn for, before any field is written.
        requested: What :func:`requested_status_id` read the posted status as.

    Raises:
        ValidationError: When the state machine refuses the move.  A 400 at
            the route.
        ValueError: From ``status_seam.settled_on_a_card``, on a row holding
            two covering movements.
    """
    try:
        verify_transition(txn, requested)
    except ValidationError as refusal:
        if enters_credit(txn, requested):
            raise ValidationError(
                f"Status cannot put {txn.name} on a credit card: "
                f"{_how_it_goes_on_a_card(txn)}."
            ) from refusal
        if settled_on_a_card(txn):
            raise ValidationError(
                f"{txn.name} is on the card, so its Status can only stay "
                "Credit or go back to Projected, which takes it off the card."
            ) from refusal
        raise


def _how_it_goes_on_a_card(txn: Transaction) -> str:
    """Return what puts *txn* on a credit card, for :func:`verify_requested_status`'s refusal.

    Said for the row's own shape, because the refusal renders in its grid
    cell and each shape has a different answer: income goes on no card; a
    row tracking its purchases puts each purchase there by its CC tick
    (Credit is per purchase); any other expense by its Credit button, which
    the popover offers on a Projected row only
    (``transaction_service.credit_offered``), so a paid or cancelled one is
    set back to Projected first.  A bill that lives on a card is put there
    by Paid with its card picked (plan task plan#3234, ruling plan#3225), and
    Credit there means another card, drawn only when there is one, so its
    sentence names Paid.

    Args:
        txn: The row a Save asked to put in Credit.

    Returns:
        The clause after "Status cannot put <row> on a credit card: ".
    """
    if txn.is_income:
        return "only an expense can go on a card"
    if txn.tracks_purchases:
        return (
            "it tracks its purchases, so each purchase goes on the card by its "
            "own CC tick"
        )
    lives_on = ""
    press = "press Credit"
    if is_revolving(txn.account):
        lives_on = f"it lives on {txn.account.name}, so "
        press = "press Paid"
    if is_projected(txn):
        return f"{lives_on}open it and {press} instead"
    return f"{lives_on}set it back to Projected, then open it and {press}"
