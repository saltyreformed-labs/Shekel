"""
Shekel Budget App -- Transaction Service: what a DOOR's requested status means

The route layer's ONE status entry point.  A door states the status the USER
asked for; :func:`apply_requested_status` decides what applying it means --
dispatching a settle to the verb, and reconciling the posted ledger either way.
What a REVERT undoes is the status seam's, in one statement with the status
itself (plan step X-au-c3): leaving the settled band releases the whole
settlement record.

**It exists because a ROUTE was making that decision, and making it wrong**
(finding **N-219**, plan step X-ap): the transaction PATCH handler called the
status seam directly, so marking a row Paid from the full-edit popover flipped
the status without asking what the row was WORTH.

Flask-isolated: plain data and ORM rows in, mutations applied in place, no
``request`` / ``session`` imports, no commit.
"""

from dataclasses import dataclass, replace

from app.exceptions import ValidationError
from app.services import posting_service
from app.services.match_press import Press
from app.models.transaction import Transaction
from app.services.movement_account import admitted_movement_account_id
from app.services.row_valuation import settled_figure
from app.services.settle_day import SettleDay
from app.services.stated_figure import StatedFigure
from app.services.status_seam import (
    Settlement,
    apply_status_change,
    correction_record,
    figure_for_status,
    recorded_settlement,
    tender_account_id_of,
    tender_for_status,
)
from app.services.transaction_service._row_rules import settles_from_entries
from app.services.transaction_service._settle import settle_transaction
from app.services.transaction_service._status_rules import (
    reject_mismatched_settled_status,
)
from app.utils.balance_predicates import enters_settled_band


@dataclass(frozen=True)
class StatedRecord:
    """What a DOOR states the settlement record IS: its figure and its tender.

    The two facts of a row's record a submission can state or correct -- what
    moved and the account it moved through -- which the settle arm hands the
    verb and the identity arm turns into one corrected record
    (:func:`_correction_for_status`).  One value because the two always
    travel together through :func:`apply_requested_status`; it was two
    keywords until plan step ``credit_card:CC-5-4a-5`` gave that door a
    sixth argument (the parameter object a public function over the limit
    takes).  The DAY is not in it: it is the assertion, not the record.

    Attributes:
        figure: The figure the door STATED and who wrote it
            (:class:`~app.services.stated_figure.StatedFigure`): ``typed``
            from the popover, ``observed`` from the statement matcher's
            transaction arm (plan step **X-bi-3e-1**, ruling **R-BAL61**).
            Read by the SETTLE arm, which decides whether it is a correction
            to record, and by the identity arm, which records it as one on a
            row staying settled; ``None`` means nobody stated one, and the
            settle records what it resolved instead.  Every other status
            change ignores it, because a figure records what MOVED and
            nothing else here moves money.
        tender_account_id: The account the door named as the one the money
            MOVED THROUGH (plan step ``credit_card:CC-5-3``): the popover's
            "Paid from" picker, posted on every Save and preselected from
            what the row records -- the one door that names one since plan
            step ``credit_card:CC-5-4a-5`` (leaf 5c-2a, ruling **R-CC137**),
            when the statement matcher stopped naming its own account.
            Read by the SETTLE arm, which hands it to the verb, and by the
            identity arm, which re-points a settled row's covering movement
            when it names an account other than the recorded one; ``None``
            means nobody named one.
    """

    figure: StatedFigure | None = None
    tender_account_id: int | None = None


#: A door that states nothing about the record -- a revert, a cancel, a
#: status the door asked for with no figure and no tender.
NOTHING_STATED = StatedRecord()


def apply_requested_status(
    txn: Transaction,
    new_status_id: int,
    *,
    settle_day: SettleDay | None = None,
    stated: StatedRecord = NOTHING_STATED,
    press: Press | None = None,
) -> None:
    """Apply the status a DOOR requested, and reconcile the ledger to it.

    **The route layer's ONE status entry point, and the reason it exists is
    that a route was deciding what a status change MEANS.**  The transaction
    PATCH handler and the cancel handler each called
    ``status_seam.apply_status_change`` directly -- the MECHANICS primitive,
    which verifies the transition, assigns the column and maintains the settle
    day, and deliberately does nothing else.  For "the user cancelled this row"
    the mechanics ARE the whole act.  For "the user marked this row Paid" they
    are not: settling also decides what the row is WORTH
    (:func:`._settle.settle_transaction`), and a door that flips the status without
    asking books whatever figure the row happened to be carrying.  That is
    finding **N-219** on the transaction PATCH door, and the shape of it is a
    ROUTE holding a money rule -- this arc's own root cause 1.

    So the routes stop choosing.  A door states the status the USER asked for
    and this decides what applying it means.  After plan step X-ap the only
    ``app/`` callers of the seam are this function, :func:`._settle.settle_transaction`,
    :func:`._settle.settle_from_entries`, ``credit_workflow`` (Credit and its revert,
    neither of them settled statuses) and ``transfer_service._status`` (a transfer,
    one row, which settles through ``transfer_service`` because every write of a
    transfer does: the transfer paragraph of ``CLAUDE.md``'s Architecture) -- so
    a FOURTH transaction settle door cannot be opened by reaching for the obvious
    primitive, which is how the third one was.

    **The ledger reconcile is here rather than at each door** for the reason
    :func:`._settle.settle_transaction` states for its own: every status change moves
    the row's posted effect (a settle posts it, a cancel reverses it), and a
    door that forgets posts nothing while reporting success.  It is reconciled
    LAST, after the caller's own field writes, so it reads the final amount and
    category rather than the pre-edit ones -- the discipline
    ``transfer_service.update_transfer`` documents.  A caller that edits
    posting-relevant fields WITHOUT changing the status still owes its own
    reconcile; there is no status change for this function to hang one on.
    So does a caller that runs this BEFORE its field writes -- the PATCH
    handler's unlock order (ruling **R-BAL58**: a transition that lifts a
    finalised row's lock is applied first, so the edits land on an unlocked
    row) -- because the reconcile here read the pre-edit row, and a moved
    period or category re-files an envelope's purchase legs only through a
    reconcile that reads the final one.

    Does NOT flush or commit -- the caller owns the session boundary.

    Args:
        txn: The transaction whose status the door is changing.  Must be a
            REGULAR row: a transfer's status is the transfer's own
            (``transfer_service.update_transfer``), and no row names a
            transfer since plan step ``balance:X-bi-6-5`` dropped the column
            (``balance:X-bi-6-4d-2`` deleted the shadows).
        new_status_id: The ``ref.statuses.id`` the door asked for -- the
            SUBMITTED status when the form carried one, else the row's own (an
            edit that changes only the settle day is an identity transition).
        settle_day: The civil day the money moved and HOW that day is known
            (:class:`app.services.settle_day.SettleDay`), when the door knows
            it, after the door's own
            :func:`app.services.status_seam.settle_day_for_status` reading of
            the submission -- which is what stamps the ``entered`` basis on a
            day that came out of a date box.  ``None`` leaves the seam's rule in
            force.
        stated: The figure and the tender the door states for the record
            (:class:`StatedRecord`, whose attributes say how each arm reads
            them); :data:`NOTHING_STATED` when it states neither.
        press: The save's :class:`~app.services.match_press.Press` (ruling **R-CC135**), or
            ``None`` when its door named nothing; handed to whichever arm runs, and
            asked by the act that takes the payment out of its matches
            (``status_seam.apply_status_change``, ruling **R-CC127**).

    Raises:
        ValidationError: From an illegal transition, the seam's settle-day
            refusals, or the tender gate.  A 400 at the route.
        NotFoundError: When *stated*'s tender names no account of the row's
            owner.  A 404 at the route.
        PostingError: From the reconcile, on a broken ledger invariant.
            Deliberately NOT a sibling of ``ValidationError`` -- it must fail
            loud rather than render as a designed refusal.
    """
    # THE DISPATCH, and it is the whole of finding **N-219**'s fix.  Moving a
    # row INTO the settled band is a SETTLE, which decides an amount before it
    # decides a status; every other status change is the mechanics plus
    # whatever the caller stated about what moved.  The verb reconciles the
    # ledger itself, so this arm returns rather than falling through to a
    # second reconcile of the same row.
    if enters_settled_band(txn, new_status_id):
        reject_mismatched_settled_status(txn, new_status_id)
        settle_transaction(
            txn, submitted=stated.figure, settle_day=settle_day,
            tender_account_id=stated.tender_account_id, press=press,
        )
        return
    # Everything else is ONE seam pass carrying every fact the door was given:
    # the status, the day, and what the row records as having moved.
    #
    # **It was two passes with an early return between them, and that dropped
    # status changes on the floor** -- measured, not reasoned: a row moving
    # Paid -> Settled (the archive) while carrying a corrected figure recorded
    # the figure, returned, and left the row Paid, answering 200.  The archive
    # WAS offered by the popover's own Status dropdown beside the Actual box,
    # so a user correcting a figure on the way to filing the row away silently
    # got only half of what they asked for.  (That status is deleted at plan
    # step **balance:X-am**; the measurement is quoted as it was taken.  What it
    # established is about the DOOR -- two independent facts in one request are
    # not alternatives -- and the popover still posts a status beside the Actual
    # box on every Save, so the composition it forced is still live.)
    #
    # The revert direction failed the same
    # way one step further out: a service caller reverting a settled row while
    # naming a figure recorded it, posted the ledger difference, and never
    # reverted -- booking money for a row it had just been told had not moved.
    #
    # The cause was treating a CORRECTION and a STATUS CHANGE as alternatives.
    # They are independent facts, and the seam already takes both in one call
    # -- which is what makes "a settled row states what moved" its property
    # rather than a convention each door keeps.  Composing them also collapses
    # what used to be two ledger reconciles into the one this function always
    # promised.
    #
    # **A figure on a row STAYING in the settled band is a CORRECTION to what
    # it recorded**, and it is applied rather than refused (developer ruling,
    # 2026-08-17).  The estimate and the actual are two different facts about a
    # row and get two different boxes: editing the estimate is a budget
    # decision and touches nothing that moved, and editing the actual states
    # what the bank really took.  Correcting a figure therefore does not
    # require reverting the row -- which matters beyond convenience, because
    # revert-then-re-settle was the ONLY path and it silently re-booked a
    # retained correction over a re-planned amount.
    #
    # This function used to own the other half of the revert too, clearing an
    # envelope's derived ``actual_amount`` through ``_release_derived_actual``
    # while deliberately sparing a bill's correction, because the two shared a
    # column and only a predicate could tell them apart.  They no longer share
    # one, and the predicate went with the sharing: nothing here is released BY
    # KIND, because nothing here is released at all.
    settlement = _correction_for_status(
        txn, new_status_id, stated.figure, stated.tender_account_id,
    )
    apply_status_change(
        txn, new_status_id, settle_day=settle_day, settlement=settlement,
        press=press,
    )
    posting_service.sync_transaction_postings(txn)


def _correction_for_status(
    txn: Transaction,
    new_status_id: int,
    submitted: StatedFigure | None,
    tender_account_id: int | None,
) -> Settlement | None:
    """Return the record a submitted figure or tender makes on *txn*, or ``None``.

    **The Actual box's write rule for a plain row** (developer ruling,
    2026-08-17): a settled row's figure is an observation about the bank, and
    an observation gets corrected when the statement disagrees.  Its sibling
    for the other half of the same assertion is the transfer's day correction
    (a stated side's day through
    :func:`app.services.transfer_service._status.apply_status_to_all_three`),
    which corrects the DAY on the identical argument.  **And the "Paid from"
    picker's, since plan step ``credit_card:CC-5-3``**: which account the
    money moved through is the record's third fact, observed the same way
    and corrected in place on the same argument -- a tender naming an account
    other than the recorded one re-points the covering movement (the seam's
    ``_re_point``), and rides the same record the figure does, so the two are
    written in ONE seam pass exactly as the status and the figure are.

    **It resolves rather than writes**, and that is what lets the caller hand
    the status and the record to the seam in ONE pass.  An earlier shape wrote
    the record itself and returned, so a status change arriving in the same
    request was never applied at all -- see :func:`apply_requested_status` for
    the two measured failures.

    **Every refusal is asked BEFORE the caller's seam call**, so a refused
    request leaves the row untouched.  That ordering is the reason this is a
    separate function rather than three guards inlined among the writes.

    The echo rule and the ``corrected`` basis are
    :func:`app.services.status_seam.correction_record`'s, stated once for both
    tables; the tender's echo rule is
    :func:`app.services.status_seam.tender_for_status`'s; the refusals below
    are this table's.

    Args:
        txn: The row the figure arrived for.
        new_status_id: The ``ref.statuses.id`` the row is moving to -- the
            SUBMITTED status when the form carried one, else the row's own.
        submitted: The figure the door stated and who wrote it, or ``None``
            when nobody stated one.
        tender_account_id: The account the door named, or ``None`` when it
            named none.

    Returns:
        A :class:`~app.services.status_seam.Settlement` -- ``corrected`` and
        carrying the stated source when a figure arrived, else the row's own
        record -- with the re-pointed tender when one arrived; or ``None``
        when nothing arrived or everything that did is an echo of what the
        row already records.

    Raises:
        ValidationError: When the status settles nothing (propagated from
            :func:`~app.services.status_seam.reject_figure_without_settled_status`
            / :func:`~app.services.status_seam.reject_tender_without_settled_status`),
            when the row takes its figure from its own purchases, when a
            tender arrives for a row recording no payment movement, or from
            the tender gate.
        NotFoundError: When *tender_account_id* names no account of the row's
            owner (the gate's 404).
    """
    # The SUBMISSION's own reading, echo-aware: a figure counts where the row
    # settles or stays settled, an untouched box on the way OUT of the band is
    # dropped (ruling **R-EG**), and a figure the user CHANGED beside a revert
    # is refused rather than discarded.  It lives at the door rather than at the
    # route because only here is the row in hand, and the comparison is against
    # what the row RECORDS -- which is what the box was prefilled from.  The
    # tender's reading is the same shape against the same record.  Read ONCE:
    # the echo rule below compares against the same figure.
    recorded = settled_figure(txn)
    figure = figure_for_status(txn, new_status_id, submitted, recorded)
    tender = tender_for_status(
        txn, new_status_id, tender_account_id, tender_account_id_of(txn),
    )
    if figure is None and tender is None:
        return None
    # **The door owns its own precondition**, which is the rule
    # :func:`._row_rules.reject_unsettleable` states for the settle verbs: an
    # envelope's figure IS the sum of its purchases (ruling **R-FF**), so a
    # typed one would be written and then contradicted by the row's own
    # children -- and its purchases each carry their own account, so there is
    # no ONE tender to re-point either.  The PATCH handler refuses both first
    # (the figure with a message naming the purchase list, the account with
    # one naming the purchase's own account); this is the service-tier
    # backstop, so a caller that skips the route cannot write a ``corrected``
    # record onto a row whose figure is derived.
    if settles_from_entries(txn):
        raise ValidationError(
            f"Transaction {txn.id} takes its figure from the purchases "
            "recorded against it, so it has no separate actual to correct "
            "and no single account its money moved through. Record the "
            "purchase, or correct one that is already there.",
        )
    record = (
        correction_record(recorded, figure) if figure is not None else None
    )
    if tender is None:
        return record
    tender = admitted_movement_account_id(txn, tender, movement="payment")
    if record is None:
        # A tender alone: the row's own record, whole, re-pointed.  A settled
        # row records a figure and its source on its covering movement, or --
        # a close of nothing (ruling **R-BAL82**) -- nothing at all, and a
        # record with nothing to book names no tender (``Settlement`` refuses
        # it); a crafted submission naming one for such a row meets this
        # sentence rather than that constructor's ``ValueError``.
        record = recorded_settlement(txn)
        if record is None or record.source is None:
            raise ValidationError(
                f"Transaction {txn.id} records no payment movement, so there "
                "is nothing to move to another account: it settled at "
                "$0.00. Type the amount that moved to record a payment."
            )
    return replace(record, account_id=tender)
