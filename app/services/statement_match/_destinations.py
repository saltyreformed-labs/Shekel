"""What budget line a bank line could BECOME a purchase against.

**Split out of :mod:`._candidates` at plan step ``balance:X-bi-7b``**, when
that module crossed this project's 1,000-line bound (ruling **balance:R-IR**:
the session that breaks a module splits it).  The seam is the one its own
docstring already drew -- two questions about two acts.  :mod:`._candidates`
answers *what has this account recorded that a statement could be showing*
(:func:`~._candidates.candidates_for`), and this answers *what budget line
could a statement line BECOME a purchase against*
(:func:`destinations_for`), which is ruling **R-FS**'s third shape.  Nothing
changed on the way across except one field the step's first leaf added
(``PurchaseDestination.recurs``) and its third leaf re-keyed
(``is_placed``, ruling **R-BAL24**).

**One scope, shared by the screen that offers and the door that writes**: a
row this does not return cannot be reached by crafting a request, and a row
it does return cannot be refused by the write door for being out of scope --
the property ``reconcile_service`` is built on and
:func:`~._candidates.matched_subjects` narrows per act.

Services-boundary discipline (``CLAUDE.md`` Architecture): reads only, plain
data in, frozen dataclasses out, no Flask import, no clock read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models.transaction import Transaction
from app.utils.balance_predicates import balance_contributing_clause

from ._creations import PurchaseDestination

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from collections.abc import Sequence

    from app.services.pay_calendar import PayCalendar


def destinations_for(
    account_id: int, calendar: "PayCalendar",
) -> "list[PurchaseDestination]":
    """Return every budget line a bank line could become a purchase against.

    **ONE scope, shared by the screen that offers a destination and the door
    that writes into it** (:func:`~._container._existing_envelope`), which is the
    property :func:`~._resolve.resolve_rows` rests on: a row this does not return
    cannot be reached by crafting a request, and a row it does return cannot be
    refused by the write door.  Every clause below is one of those doors'.

    **It lived beside :func:`~._candidates.candidates_for` because it is the
    same kind of answer about the other act** (plan step
    ``bank_import:X-f6a-3c-2``), and because a review pass derives both
    together and threads them: it was in ``_reads`` while that module was the
    only caller, and the write doors reached across for it.  It has its own
    module since plan step ``balance:X-bi-7b`` (the module docstring says
    why); the pass still derives the two together.

    Scope, and what each clause is:

    * on THIS account, and its pay period is one the OWNER'S CALENDAR holds --
      a statement is one bank's record of one account.  Ownership through the
      paycheck; ``C13-b`` REFUSED ``Transaction.user_id``.  **The ids come
      from the calendar
      rather than from a correlated subquery on ``pay_periods.user_id``, and
      that is what makes the span lookup below total** (pay-calendar plan step
      C4-a-4): the scan filters on
      :meth:`~app.services.pay_calendar.PayCalendar.saved_by_id`'s own keys and
      then indexes that same mapping, so a row it cannot place is
      unconstructible rather than skipped.  It is the clause
      :func:`~._candidates._transaction_candidates` already carries, for its stated reason --
      inside a COMMAND the two reads are separate snapshots under READ
      COMMITTED, so a concurrent payday INSERT between them is expressible, and
      scoping by the calendar's own ids means the query simply does not ask
      about a period the calendar has not got;
    * it TRACKS PURCHASES -- ``entry_service.create_entry`` refuses a parent
      that does not, and a purchase needs a container that can hold more than
      one;
    * it is not a TRANSFER and not INCOME -- both are ``create_entry``
      refusals: a transfer's legs are the transfer service's, and money coming
      in is not a purchase;
    * it CONTRIBUTES to a balance and is not soft-deleted
      (:func:`~app.utils.balance_predicates.balance_contributing_clause`) -- a
      Credit or Cancelled row records no cash, so a purchase filed under one
      would post nothing (ruling **R-FM**);
    * if it has SETTLED, it holds NO covering movement: its recorded figure
      IS its purchases (or nothing, ruling **R-BAL82**).  **This is the money
      clause** (:func:`~app.services.entry_service._doors
      ._reject_settled_addition`, the same predicate over the same fact since
      plan step ``balance:X-bi-4b-1``; both read the row's ``settled_basis_id``
      through ``X-bi-4a``): under no movement a new purchase raises what the
      row cost by exactly its own amount and is a movement of its own, so it
      is recorded; under a covering movement the purchase's movement would
      post beside the one that already carries the close (ruling **R-BAL80**)
      -- measured on a production clone through ``X-bi-3e``, when the row's
      own leg subtracted it from a gross that never held it: `-163.95` became
      `+203.67` while the anchor true-up moved `$0.00`.  The rows' ``entries``
      travel with the scan for it (``selectinload``), as
      :func:`~._candidates._transaction_candidates` loads them: a predicate in
      the comprehension must not cost a query per row.

    **A SIXTH clause stood here until plan step balance:X-am** (ruling
    **balance:R-HA**): the row must not be ARCHIVED, because an archived row's
    purchases were history (finding **N-229**) and
    :func:`~._candidates._purchase_candidates` declined to offer one.  The terminal
    ``Settled`` status is deleted, so both arms dropped the clause in one
    commit and still agree on what they offer.

    **What a pass's own acts change is re-asked, not baked in** (plan step
    ``bank_import:X-f6a-3c-2``): :func:`current_destinations` asks the same
    clauses of each row as it stands NOW, for the screen and for
    :func:`~._container._existing_envelope`.  Measured on the developer's own
    statement, 4 envelopes (2225, 2228, 2389, 2581) are both named by a
    proposal and offered as a destination, so **15 of the 91 creatable lines
    aim at an envelope an earlier item in the same pass settles** -- and a
    match settling an empty envelope writes a COVERING MOVEMENT, which the
    money clause then refuses, with the sentence about the envelope being
    gone rather than one from a tier deeper.  **Whether a match NAMES the row
    is not a clause, and was until plan step ``credit_card:CC-5-4a-5``** (leaf
    5c-2b, finding **CC-385**): an act naming an envelope's payment while the
    envelope is Projected names a payment the revert kept UN-DATED, which
    counts nothing, so a purchase filed beside it counts its own money once
    -- ruling **R-CC141** (developer 2026-10-04, *"The user should be allowed
    to add purchases from various sources to an envelope. The envelope is
    typically the sum of the purchases."*), and an act naming a SETTLED
    envelope's dated payment is the money clause's.

    **The clause that hid an already-matched envelope is DELETED, and two
    rulings amend the one that kept it.**  Finding **N-317** said it was
    wider than the money needs, and ruling **bank_import:R-FY** (developer
    2026-08-19) kept it WHOLE -- a money guard is not narrowed for a `$0.00`
    benefit -- and retired N-317 the next day as a decision rather than work
    owed.  Its one remaining reach was the Projected envelope above, whose
    matched lump the revert keeps UN-DATED and counting nothing, so rulings
    **R-CC141** (filing into it is allowed) and **R-CC143** (developer
    2026-10-05, "Allow it, change test": the match saves and the purchase
    counts once) amend R-FY's "stays whole" for it, and leaf 5c-2c-1's
    ruling **R-CC144** amends the clause of R-FY that kept the accept door's
    twin guard.  What the deleted clause once guarded beside that -- a
    Projected envelope holding no entries, matched -- was already
    unreachable through it: a match SETTLES the envelope it names, and a
    zero-entry settle at the bank's figure writes a COVERING MOVEMENT, which
    the money clause above refuses.

    Args:
        account_id: The cash account the statement is for.
        calendar: The owner's
            :class:`~app.services.pay_calendar.PayCalendar`, built by the read
            pass.  **It IS the ownership scope**, which is why no ``owner_id``
            sits beside it -- the rule :func:`~._candidates.candidates_for` states for its
            own signature, applied here at pay-calendar plan step C4-a-4: the
            periods it carries are exactly that owner's, so a second parameter
            naming the owner would be a second statement of whose rows may be
            offered and the two could disagree.  It is also where each offered
            row's SPAN comes from, DERIVED, where this producer read
            ``txn.pay_period.end_date`` -- a stored copy of a derivable fact
            that plan step ``pay_calendar:C4-c`` dropped.

    Returns:
        One :class:`~._creations.PurchaseDestination` per offerable row, oldest
        pay period first and then by name -- a deterministic order, so the
        chooser a screen shows does not depend on what the planner returned.
        **Ordered by the paycheck's own PAYDAY rather than by its id**, which
        is what "oldest" means: the two agree on every schedule written
        forward, and plan step ``pay_calendar:C6`` inserts a payday
        MID-SCHEDULE by design, which would give the newest row the newest id
        in the middle of the sequence.
    """
    # The owner's SAVED paychecks, keyed the way a row names one.  This ONE
    # mapping is both halves of the answer -- the scan's ownership scope on the
    # line below, and the span every offered row is labelled by -- so the two
    # cannot describe different schedules and the lookup cannot miss.
    spans = calendar.saved_by_id()
    rows = (
        db.session.query(Transaction)
        .options(
            # ``tracks_purchases`` below reads ``template.is_envelope`` for
            # every template-generated row, so the template travels with the
            # scan for the same reason ``_transaction_candidates`` loads it:
            # a predicate in the comprehension must not cost a query per row.
            # **``Transaction.pay_period`` is NOT loaded beside it** since
            # pay-calendar plan step C4-a-4: the relationship was here to read
            # the period's stored span, and the span now comes off ``spans``.
            joinedload(Transaction.template),
            # The money clause reads ``covering_movements`` off every settled
            # row (plan step balance:X-bi-4b-1), for the same reason.
            selectinload(Transaction.entries),
        )
        .filter(
            Transaction.account_id == account_id,
            balance_contributing_clause(),
            Transaction.pay_period_id.in_(spans.keys()),
        )
        .all()
    )
    offered = [
        PurchaseDestination(
            transaction_id=txn.id,
            name=txn.name,
            category_id=txn.category_id,
            # Indexed rather than searched, and a ``KeyError`` here is
            # unconstructible: the filter above IS this mapping's key set.
            period=spans[txn.pay_period_id],
            is_settled=txn.status.is_settled,
            # The row's identity ACROSS periods, which is what a merchant
            # rule names (plan step X-f6a-3d) -- for a placed row of a
            # rule-less definition as much as for a generated one since leaf
            # 7b-3 of balance:X-bi-7b (ruling R-BAL24) -- and whether it IS
            # such a placed row, which a new-envelope answer's first firing
            # converges on by name.  The rule rides on the template's joined
            # load above, so this costs no query per row.
            template_id=txn.template_id,
            is_placed=txn.is_placed,
        )
        for txn in rows
        if takes_a_purchase(txn)
    ]
    offered.sort(key=lambda d: (d.period.start_date, d.label))
    return offered


def takes_a_purchase(txn: Transaction) -> bool:
    """Return whether a bank line may become a purchase under *txn* as it stands.

    The Python clauses of :func:`destinations_for`'s scope -- it tracks
    purchases, it is not income, and a settled row holds no covering movement
    (the money clause) -- in ONE place, for the producer and for
    :func:`current_destinations`' re-ask.

    Args:
        txn: The row, with ``entries`` and ``template`` loaded.

    Returns:
        ``True`` when a purchase may be filed under it.
    """
    return (
        txn.tracks_purchases
        and not txn.is_income
        and (not txn.status.is_settled or not txn.covering_movements)
    )


def current_destinations(
    destinations: "Sequence[PurchaseDestination]",
) -> "list[PurchaseDestination]":
    """Return the *destinations* whose row, as it stands NOW, still takes a purchase.

    A pass derives :func:`destinations_for` once, and its own acts move rows
    under it: a match settling an empty envelope writes a covering movement,
    which the money clause refuses.  So the screen and the write door
    (:func:`~._container._existing_envelope`) narrow the pass's set by
    re-asking, in one statement and flushed state included, every clause of
    the scope that the row's own state decides -- the shape
    :func:`~._valuation.repriced` gives a row's price, which is total where
    an enumeration of the acts that move a row would not be.

    Args:
        destinations: The pass's derived destination set.  A SEQUENCE, because
            :class:`~._scope.ReviewScope` holds a tuple.

    Returns:
        The destinations still offerable, in *destinations*' own order.  No
        query when there are none.
    """
    if not destinations:
        return []
    rows = (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.template),
            selectinload(Transaction.entries),
        )
        .filter(
            Transaction.id.in_(
                {destination.transaction_id for destination in destinations},
            ),
            balance_contributing_clause(),
        )
        .all()
    )
    taking = {txn.id for txn in rows if takes_a_purchase(txn)}
    return [
        destination for destination in destinations
        if destination.transaction_id in taking
    ]
