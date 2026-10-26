"""
Shekel Budget App -- Transfer Service: what a MOVE means for a PLACED transfer

A one-time transfer is a rule-less definition plus the one transfer it PLACED
(:attr:`~app.models.transfer.Transfer.is_placed`, plan step ``balance:X-ci-1``).
The transaction side states what a move and a date mean for such a row in
:mod:`app.services.one_off` and applies it at its two doors, because a
transaction has no service door of its own; a transfer HAS one --
:func:`~app.services.transfer_service.update_transfer`, the door every mover
passes through (Transfer Invariant 4) -- so the rules are applied INSIDE it
(ruling **R-BAL96**): no door, present or future, can move a placed transfer
without re-placing it, and none can date one without its occurrence following.
The two rules themselves are the transaction twin's, stated once there and
reused as they are:

* **A period move RE-PLACES the transfer** (ruling **R-BAL93**, the twin of
  **R-BAL33**): moved to another paycheck it is due on that paycheck's start
  unless its date was not its old paycheck's start -- an owner-stated day,
  read by position -- which is :func:`one_off.due_date_after_move`, taking
  plain dates and the target :class:`~app.services.pay_calendar.DerivedPeriod`.
  For a one-time payment INTO A LOAN the due date is the installment it
  satisfies, so the re-placed date is graded by the loan-installment guard
  exactly as a hand-typed one is (it reads ``updates["due_date"]``, which
  :func:`re_place_and_grade_the_day` has written by then).
* **The transfer records its own due date as the occurrence it answers**
  (ruling **R-BAL94**, the twin of **R-BAL25**): ``occurs_on = due_date``,
  written at birth (``routes/transfers/_instances``) and by
  :func:`~._update._apply_remaining_fields`' one due-date arm whenever the
  date MOVES, so a date moved since this leaf leaves the two columns equal.
  A placed transfer can hold them apart until an edit next moves its date:
  a cleared cadence's survivor answers the occurrence its cadence named and
  keeps the due date its cadence last gave it, which need not be that day (a
  Monthly First rule's occurrence is the 1st, and its row is dated from the
  paycheck it defers onto); a cleared cadence's pre-R17 legacy row answers
  none (R-BAL94); and a one-time transfer born before this leaf answers
  none, even one whose date moved before it (that move wrote no
  ``occurs_on``), until an edit moves its date or ``X-ci-3`` backfills it.
  A one-time transfer answered NO occurrence until this leaf -- the
  undated, definition-linked shape finding **REC-516** names, whose last
  writer on this table the one-time branch was -- which is what
  ``recurrence:R19-b`` waited on for this table.
* **A typed figure RESTATES the definition's price in place, inside the
  door** (ruling **R-BAL92**; **R-BAL96** as amended 2026-09-21 after this
  leaf's adversarial review): the popover sends the figure a human typed as
  ``definition_price=`` -- an explicit act, distinct from ``amount_ownership``
  so that value keeps its one meaning (**R-BAL11**) -- and
  :func:`restate_definition_price` corrects the version the transfer's FINAL
  due date reads (after the re-placing, the twin of ``one_off.restate_price``),
  declares the transfer definition-priced and clears the flag (**R-BAL37**'s
  heal), all BEFORE the settle dispatch.  As first built the popover restated
  after the door had run, and a figure typed beside Status = Paid booked the
  pre-restate price on both legs, `$50.00` wrong on each account in the
  worked case: the settle is the door's, so the restate had to be too.

* **Two placed transfers of one definition never answer one day**
  (:func:`reject_a_day_another_row_answers`): the occurrence index
  ``idx_transfers_template_scenario_occurrence`` is unique on ``(template,
  scenario, occurs_on)`` over live rows, and a date write that would collide
  is refused as a designed :class:`~app.exceptions.ValidationError` -- a
  sentence the popover renders -- rather than met as an ``IntegrityError``
  the route answers with *Invalid reference*.  A cleared cadence's survivors
  are the rows this can happen to: several transfers, one definition, each
  answering the occurrence the cadence named.  It is the TRANSFER table's
  statement of the predicate ``one_off.another_row_answers`` states for the
  transaction table -- two indexes over two tables whose definition columns
  differ by name (``template_id`` / ``transfer_template_id``), so one body
  parameterised on the model would need the column registry plan step
  X-au-k deleted; two twins, one per index, is the honest shape.  **No
  per-paycheck refusal is twinned**: ``one_off.holds_a_row_in`` states
  **R-BAL24**'s one-row-per-paycheck rule for a bank-born envelope's
  definition, which no transfer definition is; the paycheck-keyed indexes on
  this table cover UNDATED rows (``..._undated``) and template-less rows
  (``uq_transfers_adhoc_dedupe``), neither of which a placed transfer is.

Flask-isolated like the rest of the package: ORM rows and plain data in,
``updates`` mutated in place, no flush or commit of its own.  Reading the
source paycheck is a lazy load, which may autoflush pending writes; every
caller runs this before the first write.
"""

from datetime import date

from app.enums import AmountSourceEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transfer import Transfer
from app.services import template_amount_service
from app.services.amount_ownership import derived_ownership
from app.services.one_off import due_date_after_move
from app.services.pay_calendar import DerivedPeriod


def re_place_and_grade_the_day(
    xfer: Transfer, updates: dict[str, object], target: DerivedPeriod | None,
    *, placed: bool,
) -> bool:
    """Re-place a placed transfer's date on a move and grade the day it is left with.

    The pre-write half of a placed transfer's update, asked by
    ``._update._apply_transfer_updates`` right after the endpoints and the
    target paycheck are resolved and BEFORE the loan-installment guard, so a
    one-time payment into a loan is graded on the installment the move
    leaves it with.  Two acts and one answer:

    * a period move RE-PLACES the due date (**R-BAL93**): a no-op unless
      *updates* moves *xfer* to ANOTHER paycheck -- the popover posts its
      period select on every save -- and the date re-placed is the owner's
      typed one when the same edit carries a ``due_date`` (a day typed beside
      the move is the owner's, the twin's rule), else the transfer's own; the
      source paycheck's start is read off the row's ``pay_period`` while the
      foreign key still names it;
    * whether the DATE MOVES -- the update leaves the transfer on a different
      day than it has -- is decided ONCE here and returned, because it is
      the one predicate under which the day is graded against the
      definition's siblings (:func:`reject_a_day_another_row_answers`) and,
      in the due-date arm, the occurrence follows the date (**R-BAL94**), so
      a moved date leaves the two equal.  A save that re-posts the day the transfer
      already has (the form posts every control it renders) moves nothing
      and re-keys nothing -- the twin's guard, which this leaf's adversarial
      review found missing from a first cut.

    Args:
        xfer: The transfer being updated, before any write.
        updates: The update kwargs, mutated in place: ``due_date`` is set (or
            replaced) with the re-placed date when the transfer moves.
        target: The paycheck the update leaves the transfer in, resolved off
            the owner's calendar by the caller; ``None`` when the update
            names none of the period-bearing fields.
        placed: Whether *xfer* is a rule-less definition's, read by the
            caller before any write.  A transfer that is not answers
            ``False`` with nothing done: a generated transfer's date is its
            definition's and its occurrence its cadence's.

    Returns:
        Whether this update moves the placed transfer's date.

    Raises:
        ValidationError: From :func:`reject_a_day_another_row_answers`.
    """
    if not placed:
        return False
    if target is not None and target.period_id != xfer.pay_period_id:
        updates["due_date"] = due_date_after_move(
            updates.get("due_date", xfer.due_date),
            source_start=xfer.pay_period.start_date,
            target=target,
        )
    date_moves = "due_date" in updates and updates["due_date"] != xfer.due_date
    if date_moves:
        reject_a_day_another_row_answers(xfer, updates["due_date"])
    return date_moves


def reject_a_day_another_row_answers(xfer: Transfer, day: date | None) -> None:
    """Refuse *day* for placed *xfer* when a sibling of its definition already answers it.

    The transfer occurrence index's own predicate, the twin of
    :func:`one_off.another_row_answers` over the transaction index (the
    module docstring says why they are two): unique on ``(template, scenario,
    occurs_on)`` over live rows, no status term, ``is_override`` not
    excluded.  Asked by :func:`re_place_and_grade_the_day` for a placed
    transfer whose due date this update CHANGES -- the one predicate under
    which the occurrence also follows the date, decided there so a moved date
    leaves the two equal; a save that leaves the date alone collides with
    nothing and is not asked.

    Args:
        xfer: The placed transfer, before any write.
        day: The due date the update leaves it with, which is the occurrence
            it will answer (**R-BAL94**); ``None`` (a clear, which the route
            refuses first and the CHECK constraint refuses last) collides
            with nothing here.

    Raises:
        ValidationError: Another live transfer of the same definition, in the
            same scenario, already answers *day*.
    """
    if day is None:
        return
    sibling = (
        db.session.query(Transfer.id)
        .filter(
            Transfer.transfer_template_id == xfer.transfer_template_id,
            Transfer.scenario_id == xfer.scenario_id,
            Transfer.occurs_on == day,
            Transfer.is_deleted.is_(False),
            Transfer.id != xfer.id,
        )
        .first()
    )
    if sibling is not None:
        raise ValidationError(
            "Another transfer of this item is already due that day, so this "
            "one cannot be. Pick a different day."
        )


def restate_definition_price(
    xfer: Transfer, updates: dict[str, object], *, placed: bool,
) -> None:
    """Perform a placed transfer's typed-figure act: restate, re-attach, unflag.

    A no-op when *updates* carries no ``definition_price``.  The transfer
    twin of ``one_off.restate_price``, as one act inside the door (ruling
    **R-BAL96** as amended): ``updates["definition_price"]`` is
    the figure a human typed on the popover of a one-time transfer, and it is
    a statement about the DEFINITION -- the one placed transfer IS the
    definition, so there is no "one occurrence among many" for the figure to
    be about.  The version the transfer's FINAL due date reads takes the
    figure (``template_amount_service.restate_in_effect``; the re-placing
    has already written that date into *updates* when the same save moves
    the transfer), the pair is declared priced by the definition (which
    ``_amount.apply_amount_ownership`` lands on the transfer, whose legs read
    it, re-attaching a transfer the pre-X-ci flip left OWN), and the flag comes
    off -- idempotent on a transfer never detached.  The ordering that makes
    this the door's act and not the popover's: it runs after every refusal
    and BEFORE the settle dispatch, so a figure typed beside Status = Paid is
    what the legs book.

    The key is READ and left in *updates* rather than popped, for the reason
    ``_apply_transfer_updates`` gives for ``amount_ownership``: the audit's
    ``fields_changed`` reads the keys, and a re-price that vanished from the
    audit trail is the defect a first revision of that block shipped.

    Args:
        xfer: The transfer being updated, before any write; its ``template``
            is the definition whose price is restated.
        updates: The update kwargs, mutated in place: ``amount_ownership``
            (the definition's) and ``is_override`` (``False``) are written
            beside ``definition_price``; ``due_date`` is read for the day.
        placed: Whether *xfer* is a rule-less definition's, read by the
            caller before any write.

    Raises:
        ValueError: *xfer* is not a placed transfer (a recurring definition's
            row is one occurrence among many, and restating its definition
            would re-price every occurrence -- the caller meant
            ``amount_ownership``); or the caller stated an ``amount_ownership``
            beside the figure, two answers to what prices the pair.  From
            ``restate_in_effect``: the definition holds no version, a state no
            producer of a rule-less transfer definition writes.
    """
    if "definition_price" not in updates:
        return
    if not placed:
        raise ValueError(
            "update_transfer was given definition_price on a transfer that is "
            "not a rule-less definition's: a recurring definition's transfer "
            "is one occurrence among many, and its typed figure is its OWN "
            "(amount_ownership), never a restatement of the definition."
        )
    if "amount_ownership" in updates:
        raise ValueError(
            "update_transfer was given definition_price beside amount_ownership: "
            "two statements of what prices this pair.  A one-time transfer's "
            "typed figure is the first alone (ruling R-BAL92); the second is "
            "for a transfer that owns its figure."
        )
    template_amount_service.restate_in_effect(
        xfer.template, updates["definition_price"],
        on=updates.get("due_date", xfer.due_date),
    )
    updates["amount_ownership"] = derived_ownership(AmountSourceEnum.TEMPLATE)
    updates["is_override"] = False
