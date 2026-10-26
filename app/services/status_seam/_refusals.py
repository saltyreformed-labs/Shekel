"""
Shekel Budget App -- Status Seam: the refusals

The invariants of a settlement stated as GUARDS, so every caller of the seam
inherits them rather than each door remembering one.  They are gathered here
because they are one subject -- what a row may and may not assert about its own
money -- and because :func:`._seam.apply_status_change` runs five of them
ahead of any mutation, so a refused call leaves the row untouched; the two
form readings beside it (``figure_for_status``, ``tender_for_status``) run
the other two for the edit doors, ahead of the seam.

Split out of the single ``status_seam`` module at plan step **X-au-c3**; see
:mod:`._record` for the ground the split was made on.

Five of the seven are this project's answer to a rule that cannot be a CHECK
constraint, and each says so in its own docstring: the settled-status
questions need ``ref.statuses.is_settled``, which a constraint on
``budget.transactions`` cannot see, and the ref convention keeps status ids out
of a schema; the stated-figure-over-purchases question is a count over
another table (:func:`reject_stated_figure_over_purchases`).  One is the
WORDS of a trigger: :func:`reject_settlement_on_a_deleted_row` says what
:mod:`app.deleted_row_infrastructure` refuses.  (An eighth,
``reject_settle_day_without_a_record``, was the one that MIRRORED a CHECK --
``ck_transactions_settle_day_needs_a_record`` said in words -- and went with
that CHECK and the row's figure columns at plan step ``balance:X-bi-4b-2``.)

Pure but for one read: each reads columns and the settled-status predicate,
raises or returns.  No mutation, no Flask, and one session read, in the
deleted-row refusal's words -- whether the row's recurring item is archived
(:meth:`app.utils.hidden_row.HiddenRow.of`, ruling **R-CC107**), asked only of
a row it is refusing.
"""

from datetime import date
from typing import Optional, Union

from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.settle_day import SettleDay
from app.services.status_seam._record import Settlement
from app.utils.balance_predicates import settled_status_ids
from app.utils.dates import display_today
from app.utils.hidden_row import HiddenRow


#: The rows this seam accepts.  ``Transfer`` carries no ``settled_on`` column --
#: each side's day lives on that side's payment record, the Transfer arm's
#: (:mod:`._side`) -- so the dating half of the mechanics is skipped for one of
#: the two.  The
#: branch is on the MODEL, never on ``hasattr``: a probe would silently skip the
#: maintenance for any future row that merely spelled the column differently,
#: and this arc has already paid for a ``hasattr``-shaped test -- plan step
#: X-aa's, whose lesson is Section 8's "``hasattr`` on a dataclass is not a
#: test".  (An earlier draft cited ruling R-CQ for that; R-CQ is the classifier
#: RENAME and carries no such lesson.)
StatusBearingRow = Union[Transaction, Transfer]

#: **It lives in this leaf rather than beside the mechanics that read it**, and
#: the reason is a dangling annotation a neutral review measured (2026-08-18):
#: ``reject_figure_without_settled_status`` annotates its row with this name,
#: and ``_seam`` imports ``_refusals`` -- so declaring it there left the string
#: annotation unresolvable, raising ``NameError`` under ``get_type_hints`` while
#: pylint saw nothing (it does not resolve names inside string annotations).
#: The lowest module that needs a shared name is where it belongs.


def reject_settle_day_without_settled_status(
    status_id: int, settle_day: Optional[SettleDay],
) -> None:
    """Refuse a settle day supplied for a status that is not settled.

    **One half of the settled-iff-dated invariant, stated once** (plan step
    X-f1, finding **N-183**).  A row carries the civil day its money moved if
    and only if it is in a settled status (Paid or Received), so a day
    handed in beside a Projected / Credit / Cancelled status is not a value to
    store -- it is a request to record a payment that has not happened.

    It is a module-level function rather than an inline check inside
    :func:`apply_status_change` because a transfer's sides are written by the
    seam's Transfer arm, not by :func:`apply_status_change`, so that arm and
    the transfer service ask it too: ``transfer_service._status
    .reject_stated_days_without_settle`` asks it of each side's stated day, in
    the update before its first write and in the status writer before either
    side is written, and the arm asks it again of each side's day it is
    handed, before its first write.  (The
    create asked it as well, of the days a transfer created settled stated,
    until ruling **R-BAL379** deleted that arm of the create.)  Several
    moments, one rule -- the alternative is the same sentence written at each,
    which is this arc's own root cause 1.

    Args:
        status_id: The ``ref.statuses.id`` the row is (or would be) in.
        settle_day: The day supplied beside it and how it is known
            (:class:`app.services.settle_day.SettleDay`), or ``None`` when the
            caller supplied none.  ``None`` is always accepted -- it means "no
            day was offered", which is legal for either kind of status.

    Raises:
        ValidationError: When *settle_day* is not ``None`` and *status_id* is
            not one of :func:`~app.utils.balance_predicates.settled_status_ids`.

            **A ``ValidationError`` (a 400) rather than a programming error,
            and NO form reaches it.**  The class was picked for plan step
            **X-f1c**'s settle-day field on the full-edit doors, and both
            doors read a submitted day through
            :func:`~app.services.status_seam.settle_day_for_status`, which
            drops a day beside a status that settles nothing (ruling
            **R-EG**) -- so only a service caller reaches this, for which a
            400 is generous.
    """
    if settle_day is None:
        return
    if status_id in settled_status_ids():
        return
    raise ValidationError(
        f"A settle day ({settle_day.day.isoformat()}) was supplied for status "
        f"{status_id}, which is not a settled status.  A row records the day "
        "its money moved only while it is settled (Paid or Received); "
        "mark it settled to give it a day, or clear the day to "
        "leave it projected."
    )


def reject_figure_without_settled_status(
    row: StatusBearingRow, new_status_id: int,
) -> None:
    """Refuse a submitted FIGURE for a status that settles nothing.

    **The ONE statement of that refusal for both tables**, and it is here
    because it had two (plan step X-au-c3).  ``transaction_service._door`` and
    ``transfer_service._update`` each spelled the same sentence in their own
    words, which is this arc's own root cause 1 -- two spellings of one money
    rule -- and the two would drift the first time either message was improved.

    **It is a refusal rather than a drop, and the distinction is a user's typed
    number.**  Both update schemas set ``Meta.unknown = EXCLUDE``, so a figure
    this tier discarded would vanish with no message.  A FORM never reaches it:
    :func:`~app.services.status_seam.figure_for_status` drops an echoed box on the way OUT of the
    settled band, which is ruling **R-EG**'s argument applied to the figure, so
    what lands here is a caller asserting both facts on purpose.

    The noun comes from the MODEL class, never from a caller-supplied label --
    the reason :data:`~app.services.status_seam.StatusBearingRow` gives for the
    dating branch it makes the same way.

    Args:
        row: The row the figure arrived for.  The CALLER establishes that a
            figure did arrive -- this asks only whether the status can hold one.
        new_status_id: The ``ref.statuses.id`` the row is moving to, which for a
            figure-only edit is the row's own (an identity transition).

    Raises:
        ValidationError: When *new_status_id* is not a settled status.  A 400 at
            either route.
    """
    if new_status_id in settled_status_ids():
        return
    noun = "Transfer" if isinstance(row, Transfer) else "Transaction"
    raise ValidationError(
        f"{noun} {row.id} is not settling, so a figure has nothing to record: "
        "an amount here states what MOVED. Mark it paid to record what left "
        "the account, or change its own amount to re-price the plan."
    )


def reject_tender_without_settled_status(
    row: Transaction, new_status_id: int,
) -> None:
    """Refuse a submitted TENDER for a status that settles nothing.

    :func:`reject_figure_without_settled_status`'s twin for the record's third
    fact (plan step ``credit_card:CC-5-3``): the account a payment moved
    through is a statement about money that MOVED, so beside a status under
    which nothing moves it has nothing to record.  A FORM never reaches it --
    :func:`~app.services.status_seam.tender_for_status` drops an untouched
    picker on the way OUT of the settled band, ruling **R-EG**'s argument
    applied to the tender -- so what lands here is a submission asserting
    both facts on purpose: "it was paid from the card" and "it did not
    move".  Refused rather than dropped, for the reason the figure's twin
    gives: a silently discarded field is how a user's stated fact vanishes.

    A ``Transaction`` alone: a transfer's money moves on its legs, and the
    transfer service names each leg's account itself.

    Args:
        row: The row the tender arrived for.  The CALLER establishes that a
            tender did arrive -- this asks only whether the status can hold
            one.
        new_status_id: The ``ref.statuses.id`` the row is moving to, which
            for a tender-only edit is the row's own (an identity transition).

    Raises:
        ValidationError: When *new_status_id* is not a settled status.  A 400
            at the route.
    """
    if new_status_id in settled_status_ids():
        return
    raise ValidationError(
        f"Transaction {row.id} is not settling, so a 'Paid from' account has "
        "nothing to record: it states which account the money MOVED through. "
        "Mark it paid to record the payment, or set it to Projected and leave "
        "the account as it was."
    )


def reject_settlement_without_settled_status(
    status_id: int, settlement: Optional[Settlement],
) -> None:
    """Refuse a settlement record offered for a status that is not settled.

    The settlement twin of :func:`reject_settle_day_without_settled_status`, and
    it exists for the same reason on the same rule: a row records what moved if
    and only if it is in a settled status, so a record handed in beside a
    Projected / Credit / Cancelled status is not a value to store -- it is a
    request to book money that has not moved.

    ``None`` is always accepted here and means "no record was offered", which is
    legal for either kind of status; :func:`apply_status_change` is what refuses
    the other direction -- a row ENTERING the settled band with no record.

    Args:
        status_id: The ``ref.statuses.id`` the row is moving to.
        settlement: The record offered beside it, or ``None``.

    Raises:
        ValidationError: When *settlement* is not ``None`` and *status_id* is
            not one of :func:`~app.utils.balance_predicates.settled_status_ids`.
    """
    if settlement is None:
        return
    if status_id in settled_status_ids():
        return
    raise ValidationError(
        f"A settlement record was supplied for status {status_id}, which is "
        "not a settled status.  A row records what moved only while it is "
        "settled (Paid or Received); mark it settled to record a "
        "figure, or leave it projected, which records nothing."
    )


def reject_stated_figure_over_purchases(
    row: Transaction, settlement: Optional[Settlement],
) -> None:
    """Refuse a STATED figure on a row whose purchases already state one.

    **The purchases ARE the figure** (plan step ``balance:X-bi-4a``, ruling
    **R-BAL78**).  A row holding purchases records its money AS those
    purchases -- each a movement of its own, dated or in flight -- and a
    figure typed over them would be a second statement of overlapping money:
    the seam would mirror it as a covering movement beside the purchases,
    and the fold, which reads movements alone (ruling **R-BAL80**), would
    count the typed movement AND the purchases.  Worked: `$389.75` of dated
    purchases under a `$500.00` typed close would read `-$889.75` where the
    row-leg fold of ``X-bi-3e`` netted the pair to `-$500.00`.  So the state
    is refused at the seam, where every door that hands over a record
    arrives, rather than admitted and reconciled: to change what such a row
    cost, correct or add a purchase.  The one door that could reach it --
    *Track individual purchases* unticked on a settled envelope, then the
    Actual box -- now meets this sentence; 0 such rows existed on production
    when the refusal went in (measured 2026-09-18 on the 12:34 restore).

    A record that STATES a figure is one whose ``source`` is not ``None``
    (``resolved``, ``typed`` or ``observed``); a ``purchases`` record states
    none and is what such a row settles on.

    Args:
        row: The row being written.
        settlement: The record this call writes, or ``None``.

    Raises:
        ValidationError: When *settlement* states a figure and *row* holds
            purchases (:attr:`~app.models.transaction.Transaction.purchases`,
            the entries less the seam's own covering mark).  A 400: reachable
            from the Actual box, and the message is the remedy.
    """
    if settlement is None or settlement.source is None:
        return
    if not row.purchases:
        return
    raise ValidationError(
        f"Transaction {row.id} records its money as purchases, so a figure "
        "cannot be stated over them: what it cost is what its purchases say. "
        "To change the total, correct or add a purchase."
    )


def reject_settlement_on_a_deleted_row(
    row: StatusBearingRow, settlement: Optional[Settlement],
) -> None:
    """Refuse a settlement record on a deleted row: a deleted row takes no money.

    Plan step ``credit_card:CC-5-4a-4``, ruling **R-CC89** (developer
    2026-09-23: *"The two code paths that write money under a row refuse first,
    with a readable sentence."*).  A record is what the seam's covering writer
    (:func:`._covering.sync_covering_movement`) turns into a payment record
    under the row, so refusing the record here is refusing that write, ahead of
    any mutation.  The other path is ``entry_service.create_entry``'s refusal of
    a purchase.  Measured by the step's third review: the popover's Actual
    correction on a deleted Paid $120.00 Hotel reached the covering writer
    through ``transaction_service.apply_requested_status``'s correction arm,
    which the settle verbs' ``reject_unsettleable`` does not guard, and wrote a
    dated $125.00 payment record under the hidden row.

    **The words of a trigger**: :mod:`app.deleted_row_infrastructure` refuses
    the same write in the database, for a writer that never reaches this door.
    A record of ``None`` writes nothing and passes: a revert of a deleted row,
    and a settle-day correction, move no money.

    **A ``Transfer`` is asked too: the words of the database's transfer
    arm.**  Since plan step ``balance:X-bi-6-4d-2`` a transfer's money is
    recorded on its two sides' records, hung off the transfer itself, and the
    status seam's Transfer arm (``_side.sync_side_records``) asks this before
    it writes one; :mod:`app.deleted_row_infrastructure`'s transfer arm
    refuses the same arrival in the database.  Either row is told "was
    archived" where its recurring item is archived and "was deleted"
    otherwise, whichever act hid it (ruling **R-CC107**), by its name
    (:meth:`~app.utils.hidden_row.HiddenRow.of`, which names a transfer by
    its from-side's leg label, never the nullable ``Transfer.name``, finding
    **BAL-547**).
    The arm's door refuses a deleted transfer as "not found" first
    (``transfer_service._validation._get_transfer_or_raise``), so only a
    service caller that skipped it reaches this.  (A deleted SHADOW, which
    this sentence named until then -- finding **BAL-550** -- no longer
    reaches the seam at all: no transaction row names a transfer,
    ``ck_transactions_names_no_transfer``.)

    Args:
        row: The row being written.
        settlement: The record this call writes, or ``None``.

    Raises:
        ValidationError: When *settlement* is not ``None`` and *row* is
            soft-deleted.  A 400.  The route's ownership door answers a
            deleted row "not found", and since plan step ``balance:X-bn`` no
            Delete can commit between that door's read and this one (both
            follow the request's owner lock), so only a service caller that
            skipped the door reaches it.
    """
    if settlement is None or not row.is_deleted:
        return
    raise ValidationError(deleted_row_payment_refusal(HiddenRow.of(row)))


def deleted_row_payment_refusal(gone: HiddenRow) -> str:
    """Return the sentence a payment on a hidden row is refused with.

    **One sentence for the three places that refuse it** -- this seam's
    :func:`reject_settlement_on_a_deleted_row`, the settle verbs'
    ``transaction_service`` ``reject_unsettleable`` (what a service caller's
    Mark Paid on a deleted row meets), and the Mark
    Paid route, which shows it on the cell or the card for a row that is gone
    (rulings **R-CC101**, **R-CC104**) -- so the owner reads one answer
    whichever of them refused.  It names the row and never its id (ruling
    **R-CC98**, developer 2026-09-23: *"never show a user a system ID. A user
    will not know what that is and only be confused. Use the name of the
    transaction"*), in the words ruling R-CC96 quotes for the purchase door's
    twin: *"Groceries was deleted: a purchase cannot be recorded under it"*
    -- or "was archived" where the row's recurring item is (ruling
    **R-CC107**, developer 2026-09-24: *"Gym was archived: a payment cannot
    be recorded under it.  Reload the page."*).

    It takes a :class:`~app.utils.hidden_row.HiddenRow` rather than the row
    because a one-off row's delete removes the row from the table: the route
    read the name while the row was live, and after the delete there is no row
    left to hand over.

    Args:
        gone: The hidden row's name, and whether its recurring item is
            archived.

    Returns:
        The refusal, naming the row.
    """
    return (
        f"{gone.name} {gone.went}: a payment cannot be recorded under it.  "
        "Reload the page."
    )


def day_is_in_the_future(day: date) -> bool:
    """Return whether *day* is later than the user's today (ruling **R-EJ**).

    **The PREDICATE behind :func:`reject_future_settle_day`, published so a
    SCREEN can ask it.**  A door that raises answers *may I do this*, and a
    control that must not be rendered needs *would this be refused* -- and the
    project's own rule is that a screen may not offer a control whose
    submission can never succeed, a shape the statement-match package has now
    closed six times.  Before this, the only way to ask was a second
    ``day > display_today()`` comparison at the call site, which is one money
    rule spelled twice.

    Added at plan step ``bank_import:X-gf-1`` for
    :func:`app.services.statement_match._leftovers._recordable_inflows`, whose
    card would otherwise render a tick for a bank line the bank dates in the
    future -- and whose door wrote the row before this refusal fired.  Found by
    adversarial financial review 2026-08-27.

    Args:
        day: The civil day to test.

    Returns:
        Whether it is after the user's today
        (:func:`~app.utils.dates.display_today`), which is the display-timezone
        clock every settle door already reads.
    """
    return day > display_today()


def reject_future_settle_day(settle_day: Optional[SettleDay]) -> None:
    """Refuse a settle day that has not happened yet (ruling **R-EJ**).

    A row carries a settle day if and only if it is settled, and settled means
    the money HAS moved -- so a day in the future is not a fact about money, it
    is a forecast in a fact column.  ``Transaction``'s class docstring specified
    this rule before any door could reach it: *"a 'not in the future' rule is
    not expressible in a CHECK (it is not immutable) and lives at the write door
    instead, exactly as ruling R-M's purchase-date guard does for an entry."*
    Plan step X-f1c is that write door, and the first one where a USER supplies
    the day.

    **What it costs to omit, MEASURED end to end through the live routes** (two
    independent derivations, one number -- the step's own trace and a neutral
    adversarial review).  A settled source counts from its own ``settled_on``
    (``cash_ledger.dated_deltas``), and
    :func:`app.services.balance_at._assertions.assertion_corrections` absorbs one into an
    assertion only when the assertion is dated ON OR AFTER it -- so a
    future-dated settle rides on top of every assertion until that day arrives.
    On a ``$1,000`` anchor a ``$100`` expense settled three days ago reads
    ``$900``; PATCH its day forward and the route answers ``200`` with the
    balance back at ``$1,000``.  **Already-spent money, back in the projection.**

    **And it is the LIKELY input, not an exotic one.**  The correction box tells
    the user to correct the day against their statement, and a statement's most
    common disagreement is a PENDING item carrying a FUTURE posting date.

    **The opposite rule on ``TransactionEntry.settled_on`` is not a
    contradiction.**  A future ENTRY posting day is the CONSERVATIVE direction --
    no assertion closes over it, so the debit stays reserved and the balance
    stays low -- so that door bounds only from below and says so.  A future
    ``Transaction.settled_on`` points the other way: it takes settled money OUT
    of the balance.  The two fields' rationales do not transfer.

    **The recorded clearing fact does not undo that** (plan step X-f3a-1, ruling
    **R-FL**), and an adversarial review was right to ask: a LINKED purchase is
    cleared whatever its day says, so a linked entry moved to a future day would
    release its reservation and put already-reserved money back in the
    projection -- the very failure this refusal exists to prevent, arriving
    through the exempt door.  It cannot happen, because
    ``entry_service.update_entry`` RELEASES the link whenever the posting day
    moves: a future-dated entry is therefore always unlinked, and the day rule
    answers it exactly as this paragraph describes.

    It lives here, beside :func:`reject_settle_day_without_settled_status` and
    the ``datetime`` refusal, for the reason those are here: ONE door, every
    write path, a value the seam does not accept rather than a check each caller
    has to remember.  Both date inputs also carry ``max`` = today, so the browser
    refuses first and this is the backstop -- the same layering
    ``accounts/form.html`` uses for an anchor's ``observed_on``.

    **The clock is the USER's** (ruling R-DH (b)).  ``display_today()``, never
    ``date.today()``: the process's UTC day is already tomorrow at 8pm Eastern,
    so the server's clock would refuse a settle the user is making right now.

    Args:
        settle_day: The candidate day and how it is known
            (:class:`app.services.settle_day.SettleDay`), or ``None`` when none
            was supplied.  ``None`` is always accepted -- it means "derive the
            day from the status", which is the everyday path.  The BASIS is not
            read: no provenance makes a day that has not happened a fact, and a
            bank line dated tomorrow is a pending item rather than a posting.

    Raises:
        ValidationError: When *settle_day*'s day is later than the user's today.  A
            400 rather than a programming error, because plan step X-f1c makes
            it reachable by an ordinary user typing in the correction box; the
            route layer renders it as a designed error fragment.
    """
    if settle_day is None:
        return
    if not day_is_in_the_future(settle_day.day):
        return
    today = display_today()
    raise ValidationError(
        f"A settle day of {settle_day.day.isoformat()} has not happened yet "
        f"(today is {today.isoformat()}).  A row records the day its money "
        "moved, so the day cannot be in the future -- if the payment is "
        "scheduled rather than made, leave it Projected."
    )
