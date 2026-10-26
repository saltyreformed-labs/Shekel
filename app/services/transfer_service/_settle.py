"""
Shekel Budget App -- Transfer Service: what SETTLING a transfer means

The whole act, for all three rows at once: an auto-derived loan payment stops
deriving and OWNS what it is live worth, the status and the settle day land in
ONE seam pass, and a figure a human typed is told apart from the panel's own
prefill echoed back.

**The rule lives here rather than at each door, and that is ruling R-FA applied
to the transfer table.**  It lived in ONE route branch --
``routes/transactions/_shadow_mutations``'s ``_mark_done_shadow``, which called
``loan_payment_service.live_loan_payment_amount`` and handed the answer to
``update_transfer`` as an ``actual_amount`` -- and FOUR doors could move a
transfer into the settled band before this step added a fifth (the reconcile
panel's tick):

* the grid's shadow "Mark Paid" (``_mark_done_shadow``), which froze;
* the transfers page's "Mark Done" (``routes/transfers/mutations.mark_done``),
  which did not;
* the transfer full-edit Status dropdown (``_execute_transfer_update``), which
  did not;
* a transaction PATCH landing on a shadow (``_apply_shadow_update``), which did
  not.

That is finding **N-219**'s shape one table over -- a ROUTE holding a money
rule, so one control books a different figure from another for the same payment.

**WHICH COLUMN the freeze writes is deliberately NOT changed here, and the
reason is measured rather than conservative.**  The figure is the APP's
derivation, so ruling **R-FH** says it belongs in the row's OWN amount and
``actual_amount`` should hold a human's figure alone; finding **N-241** records
that it does not.  This leaf BUILT that move and then withdrew it, because two
adversarial reviews measured it unsafe on today's schema:

* ``cash_ledger._loan_installment._manual_shadow_amount`` derives a manual payment's cash
  as ``estimated_amount + extra`` and documents that column as *"always the
  generated base"*.  Writing the freeze there makes the derivation read its own
  output, so a settle / revert / settle cycle COMPOUNDS the standing extra --
  measured ``$1,599.10 -> $1,699.10 -> $1,799.10``.  *Both halves of that
  hazard are gone: plan step X-au-c3 moved the record off the plan, and plan
  step X-au-g-2c-2 deleted the producer that read the column;*
* what a row is worth prefers a human's ``actual_amount`` over its own amount,
  and nothing clears a leftover ``actual_amount`` when a transfer is reverted
  (finding **N-257**), so a freeze written to ``estimated_amount`` is silently
  OUTRANKED by it -- the panel offers the frozen figure and the settle books
  the stale one, ``$99.10`` apart on the reviewed measurement.

Both hazards are the same shape: the write is neither idempotent nor
authoritative while the schema has no way to say whether a row's amount is its
OWN or DERIVED.  Ruling **R-FI**'s ``amount_source`` column is exactly that
statement, and plan step **X-au-c** adds it -- so the column move is that step's,
and the developer ruled it there (2026-08-12).  What THIS leaf does instead is
put the write in ONE place, so moving it is a one-line change rather than a hunt
across four modules.

**What it cost on production was `$0.00`, and the reason is worth
stating**: ``budget.loan_payment_settings`` held ZERO rows, so
``loan_payment_config`` answered ``(False, 0.00)`` for every transfer template
and every shadow resolved to its parent's own figure -- which every shadow
already equalled (re-measured 2026-09-01 at stamp ``a4c6f1d92b73``: 0 of 350
differed).  The split would open the first time a loan payment transfer was
created through ``routes/loan/payment_transfer.py``, a live route.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Reads and mutates ORM rows; no flush, no commit.
  - All monetary arithmetic uses :class:`~decimal.Decimal`.
"""

from decimal import Decimal

from app.exceptions import ValidationError
from app.models.transfer import Transfer
from app.services.cash_ledger import (
    AmountBasis,
    derived_amount_basis,
    leg_contribution_of,
)
from app.services.match_press import Press
from app.services.stated_figure import StatedFigure
from app.services import status_seam
from app.services.status_seam import (
    Settlement,
    honoured_figure,
    recorded_leg_settlement,
)
from app.services.transfer_legs import TransferLeg, transfer_side_leg
from app.services.transfer_service._side_days import PairDays
from app.services.transfer_service._status import apply_status_to_all_three
from app.services.transfer_service._validation import TransferRows


def _reject_unsettleable(transfer: Transfer) -> None:
    """Refuse a transfer this module may not settle or price -- one rule, once.

    **The twin of ``transaction_service.reject_unsettleable``, and it exists
    for the reason that one does** (finding **N-233**): a verb owns its own
    preconditions.  **A soft-deleted transfer** must not be resurrected by a
    settle, nor priced for one: it values at ``Decimal("0")`` (the
    valuation's own gate), so settling it would book nothing while stamping
    the pair Paid.  ``_get_transfer_or_raise`` already refuses a deleted
    transfer at the settle's door, and the reconcile arm's offer scope
    excludes one -- but :func:`leg_settle_amount` is a public pure read with
    neither in front of it, and a figure this module publishes for a transfer
    it refuses to book is the shape plan step X-f2-c3 removed one table over.

    It was asked of the EXPENSE SHADOW until plan step
    ``balance:X-bi-6-4d-2``, together with "is this a transfer shadow at all":
    a shadow is no longer what this module prices, so the second rule has no
    subject, and a twin's soft-delete is no longer read.

    Args:
        transfer: The transfer to check.

    Raises:
        ValidationError: When *transfer* is soft-deleted.
    """
    if transfer.is_deleted:
        raise ValidationError(
            f"Transfer {transfer.id} is soft-deleted; a settle cannot "
            "resurrect a deleted transfer.",
        )


# ``frozen_amount`` stood here until plan step X-au-g-2c-2, and what deleted it
# is the state it repaired becoming unrepresentable rather than a rule changing.
# It published ``LoanPricing.live_cash`` under a name -- *the live payment-date
# figure a settle FREEZES* -- because a loan-payment shadow STORED a
# creation-time estimate that the settle had to supersede.  A transfer has no
# shadow now, and a transfer leg is priced by the amount model,
# so :func:`~app.services.cash_ledger.leg_contribution_of` answers the same number
# for a derive-mode payment (its installment's P&I + escrow + extra), for a
# manual one (its definition's price + extra) and for a plain transfer (its
# parent's), and there is nothing left to freeze OVER.
#
# **The freeze's ``is_projected`` guard went with it and is not missed**, which
# is worth stating because it was load-bearing: it made the capture ONE-SHOT,
# so a ``done -> done`` replay from a stale tab could not rewrite a recorded
# figure with a later derivation.  What holds that now is
# :func:`~app.services.row_valuation.leg_fixed_contribution`, asked FIRST inside
# :func:`~app.services.cash_ledger.leg_contribution_of` -- a settled leg answers
# from its transfer's settled figure and never reaches a producer -- plus
# :func:`settle` running only on the way INTO the settled band.  Two
# independent reasons
# where there was one.


def settle(
    rows: TransferRows,
    new_status_id: int,
    *,
    submitted: StatedFigure | None,
    stated: PairDays,
    press: Press | None = None,
) -> bool:
    """Settle a transfer -- both legs and the parent -- and say whose figure it booked.

    **The whole act, in one function, and that is what the four doors buy from
    it.**  A settle is not a status change with extras: it decides what the row
    is worth, records that the money moved, and dates it, and a door that does
    two of those three books a figure the third contradicts.

    Two acts, in this order, and the order is the rule:

    1. **The figure, decided but not yet written.**  What the expense LEG is
       worth (:func:`~app.services.cash_ledger.leg_contribution_of`, the
       parent's resolved amount) is asked ONCE, before anything moves -- after
       the status flip it would answer from the settled figure this act is
       about to write.  A RETAINED correction
       (:func:`~app.services.status_seam.honoured_figure` over the transfer's
       one settled figure, read through the expense leg) outranks that
       derivation, and a figure a HUMAN supplied NOW outranks
       both: it is compared against what the row would book anyway and is a
       CORRECTION only if it differs.  A figure somebody read off a statement is
       a fact; a derivation is an inference.
    2. **The status, each side's settle day and the RECORD, in ONE seam
       pass.**  A side's day is the caller's when it states one -- the
       reconcile tick's statement date for the leg on the statement's account
       -- so the pair is dated once rather than stamped with today and
       corrected afterwards.  That second write was this module's own defect:
       the settle went through the door ruling **R-ED** built for a user
       CORRECTING a day (``apply_settle_day_correction``, deleted at plan step
       ``balance:X-bi-6-4c-3``), so every tick wrote ``settled_on`` twice and
       the intermediate value was a day the money did not move.  The side
       nobody stated for borrows the stated side's day (ruling **R-BAL142**).
       The figure rides in the same call
       (``status_seam.Settlement``) and is stored once, on the transfer; each
       side's record takes its day and no figure (a ``$0.00`` close keeps
       none).

    **It was THREE acts until plan step X-au-c3**, the third being a separate
    write of the figure into ``actual_amount`` after the seam.  One call is what
    makes "a settled row states what moved" a property of the seam rather than a
    convention each settle verb keeps -- the seam REFUSES a row entering the
    settled band with no record -- and it is why a refused transition can no
    longer leave a money column written by a settle that did not happen.

    **An ECHO is not recorded as a correction.**  The reconcile panel PREFILLS
    its amount box, so an untouched tick posts the figure the row would book
    anyway; recording it as ``corrected`` would destroy the only stored signal
    that says a human read a number off a statement (ruling **R-FB**'s
    production measurement, "11 of 93 settled bills carry a hand-typed
    correction", is made of exactly that signal).  The figure is still RECORDED
    either way -- on the ``derived`` basis -- which is the difference from the
    world before this step, where an uncorrected settle recorded nothing and
    every reader fell back to the row's plan.

    **A settle never CLEARS the record.**  A ``figure`` beside a status that
    settles nothing is REFUSED unless it echoes what the pair records
    (``_update._grade_submitted_figure``), because a figure states what MOVED
    and an unsettled pair has moved nothing; one on a pair that stays settled
    is a CORRECTION, applied in place.  A revert KEEPS what moved (a ``$0.00``
    close's revert clears its ``0.00``, ruling **R-BAL365**) and the re-settle
    honours a stated figure (act 1 above), so the round trip is lossless.
    ``settle_transaction`` follows the same rule one table over, so the two
    settles cannot come to disagree.

    **The figure lands in the row's OWN settlement record, and that is what
    closed finding N-241.**  It went to ``actual_amount`` -- a column ruling
    **R-FH** reserves for a figure a HUMAN supplied -- so a machine-derived
    freeze written there manufactured a correction nobody made.  A record
    that says WHO wrote its figure (the transfer's
    ``settled_figure_source_id`` since plan step ``balance:X-bi-6-4d-2``; a
    covering movement's ``figure_source_id`` from ``balance:X-bi-4b-1``; the
    row's ``settled_basis_id`` through ``X-bi-4a``) leaves the freeze nothing to
    overwrite, and the column it was hiding in is gone.

    Mutates in place.  Does NOT flush, commit, or reconcile the posted ledger
    -- ``update_transfer``'s tail owns all three, so a settle and an ordinary
    edit reconcile through one statement.

    Args:
        rows: The transfer, at its pre-settle status.  The figure is resolved
            from the EXPENSE leg; either would answer the same (both price the
            one parent, and both read the transfer's one settled figure), and
            naming one means the choice is not made twice.
        new_status_id: The settled status the transfer moves to, as the DOOR
            asked for it.  Verified by
            :func:`~app.services.transfer_service._status.apply_status_to_all_three`.
        submitted: The figure a caller stated and who wrote it
            (:class:`~app.services.stated_figure.StatedFigure`; every caller
            today is a person's door, so ``typed``), or ``None`` when nobody
            stated one.
        stated: The days the caller states, by side
            (:class:`~app.services.transfer_service._side_days.PairDays`) --
            the reconcile tick's statement day on the ``asserted`` basis, the
            matcher's bank day on ``observed``, each for the side on the
            statement's account.  A settle entering the band admits every
            stated day.  Empty derives both: each side borrows the owner's
            today, as a Paid press does.
        press: The save's press, or ``None`` when its door named nothing:
            a ``$0.00`` figure takes each leg's kept payment off
            the books, and the act asks (ruling **R-CC127**;
            :func:`~._status.apply_status_to_all_three`).

    Returns:
        Whether this settle booked a figure the caller supplied NOW -- what the
        reconcile writer counts (finding **N-231**).  Answered by the act itself
        rather than by a predicate the caller asks separately, so the count and
        the write cannot disagree and the figure is resolved once per settle
        instead of once per asker.  ``False`` for a settle that honoured a
        RETAINED correction: nobody typed anything at this tick, and the count
        is of what this tick's user did.

    Raises:
        ValidationError: On a transfer this module may not settle
            (:func:`_reject_unsettleable`), or from the status seam's own
            transition and settle-day refusals.
    """
    _reject_unsettleable(rows.transfer)

    # Resolved ONCE, and everything below reads this answer rather than asking
    # again.  It was asked up to three times per ticked row before plan step
    # X-f2-c3 -- by the arm's correction predicate, by the dispatch's own
    # predicate, and by its fallback -- and each asking is a ``Transfer`` query
    # plus, for a derive-mode payment, a loan-basis resolve and an escrow load.
    basis = derived_amount_basis(
        rows.transfer.user_id, rows.transfer.scenario_id,
    )
    # Off the expense LEG -- its transfer, never its side's record -- never a
    # twin row (plan step ``balance:X-bi-6-4d-2``): a twin's status is no
    # longer kept, and a twin left saying Paid under a reverted transfer would
    # price at the sum of its empty entries, ``$0.00``, and a ``$0.00`` settle
    # takes the sides' records off the books.
    resolved = leg_contribution_of(rows.expense_leg, basis)
    # A RETAINED correction outranks the derivation, through the same published
    # rule :func:`leg_settle_amount` offers from, so the pair's offer and its
    # booking are one expression (plan step X-au-c3).  The record is the
    # transfer's one settled figure, read once for the act through the
    # expense leg (``rows.expense_leg``; ``recorded_leg_settlement`` reads
    # the leg's transfer) and carried into the settlement below.
    retained = recorded_leg_settlement(rows.expense_leg)
    held = honoured_figure(retained)
    booked = resolved if held is None else held
    correction = (
        submitted if submitted is not None and submitted.amount != booked
        else None
    )

    # ONE act: the status, each side's day, and what the transfer RECORDS as
    # having moved.  ``Settlement.from_settle`` states the "a human's figure
    # beats the derivation" rule once for both settle verbs, and the seam's
    # Transfer arm stores the figure once, on the transfer: each side's record
    # says where and when its money moved, not how much.
    #
    # **The figure goes to the row's OWN record, not to a column reserved for a
    # human, and that closes finding N-241.**  It was written to
    # ``actual_amount`` -- which ruling **R-FH** reserves for a figure somebody
    # read off a statement, and which three subsystems read the NULL-ness of as
    # meaning exactly that -- so every derive-mode loan settle manufactured a
    # correction nobody had made.  The two are different facts now, and which
    # one a figure is stands in the record's SOURCE (the transfer's
    # ``settled_figure_source_id``, plan step balance:X-bi-6-4d-2) rather than
    # being inferred from a column being populated.
    #
    # The pair's RETAINED record is read from the expense leg -- the same leg the
    # figures above come from, and for the same reason (both legs read the
    # transfer's one settled figure, so naming one means the choice is not
    # accidental).  A
    # revert releases the pair's assertion and keeps a figure above ``$0.00``,
    # so re-settling a transfer the user reverted in order to edit honours the
    # figure they read off their statement instead of re-deriving over it.
    apply_status_to_all_three(
        rows, new_status_id, stated=stated, press=press,
        settlement=Settlement.from_settle(booked, correction, retained),
    )

    # **``EVT_TRANSFER_AMOUNT_FROZEN`` WAS EMITTED HERE, AND IT IS DELETED
    # RATHER THAN RE-POINTED** (ruling **R-BAL12**, plan step X-au-f).  Its
    # predicate was ``booked != rows.transfer.amount`` -- the one money write no
    # operator asked for, fired when the app's own derivation decided the booked
    # figure and that figure was not the one the transfer stated.
    #
    # That column is EMPTY for a generated transfer now, so the comparison was
    # finding **N-451**: true on EVERY settle, including a plain
    # checking-to-savings transfer where no derivation decided anything.
    # Re-pointing it at the RESOLVED figure makes it fire on NONE -- under
    # ruling **R-BAL10** the booked figure IS what the transfer states, so the
    # two sides share one producer and the comparison is an identity.  An event
    # that cannot fire is a fence the design made unnecessary, so it went with
    # its predicate, its ``log_events`` registration and its integration test
    # rather than being kept as a green check that measures nothing.
    #
    # What it recorded is not lost: the record says whether a booked figure
    # was the app's own resolution or a human's correction (the transfer's
    # ``settled_figure_source_id`` since ``balance:X-bi-6-4d-2``; a covering
    # movement's ``figure_source_id`` before it, and the row's
    # ``settled_basis_id`` through ``X-bi-4a``), for every settle rather than
    # for the subset a predicate
    # happened to select (plan step X-au-c3).
    return correction is not None


def leg_settle_amount(leg: TransferLeg, basis: AmountBasis) -> Decimal:
    """Return what settling *leg*'s transfer would BOOK on the leg's account.

    **The transfer twin of ``transaction_service.settle_amount``, and it exists
    for the same reason**: the reconcile panel and the statement matcher offer
    LEGS, and must show the figure a tick will book.  :func:`settle` resolves
    its own figure through the same two rules, so the displayed figure and the
    booked one cannot drift:

    * a RETAINED correction outranks every derivation (plan step X-au-c3), read
      off the transfer's one settled figure (plan step
      ``balance:X-bi-6-4d-2``; until then off the offered side's record,
      re-read through ``transfer_legs.transfer_side_leg``) -- a draft honoured
      it only at the WRITE, so the panel offered the plan and the settle
      booked the human's figure;
    * else what the leg is worth
      (:func:`~app.services.cash_ledger.leg_contribution_of`): ``0`` for a
      parent that does not contribute, else the parent's resolved amount.

    It was asked of the leg's TWIN ROW through plan step
    ``balance:X-bi-6-4d-2`` (``settle_amount(shadow)``, through a
    ``_shadow_of`` that refused a corrupt twin pair); a twin is no longer
    read, so a leg is priced off its transfer, never its side's record.

    **The basis is the CALLER'S and this builds none** (plan step X-au-j,
    finding **N-295**): each caller's read pass builds one for the owner and
    prices every offered leg against it.

    A PURE read: nothing here mutates.

    Args:
        leg: The leg being offered, its parent still Projected.
        basis: The read pass's
            :class:`~app.services.cash_ledger.AmountBasis`, built for the
            leg's owner.

    Returns:
        A retained correction where one stands, else what the leg is worth.

    Raises:
        ValidationError: On a soft-deleted transfer
            (:func:`_reject_unsettleable`).
        AmountUnresolvable: From the amount model, for a parent whose rule
            cannot price it.  A refusal is never a fallback.
    """
    _reject_unsettleable(leg.transfer)
    held = honoured_figure(recorded_leg_settlement(leg))
    if held is not None:
        return held
    return leg_contribution_of(leg, basis)


def record_leg_clearing(leg: TransferLeg, anchor_id: int) -> None:
    """Record WHICH statement showed *leg* (ruling **R-FL**), on the side's record.

    The reconcile panel's tick, which holds legs, records the link AFTER the
    settle verb returns -- the verb is shared with the grid's Mark Paid, which
    no statement has shown.  The side's record is read as it stands now (the
    settle just wrote it) and linked by the status seam's Transfer arm
    (``status_seam.record_side_clearing``): per SIDE and never mirrored to the
    other side, whose account's statement nobody read in this act; a side
    closed at ``$0.00`` holds no record and keeps no link (ruling
    **R-BAL141**).  Until plan step ``balance:X-bi-6-4d-2`` it linked the
    side's twin row and that row's covering movement.

    Issues no flush and no commit -- the caller owns the session boundary.

    Args:
        leg: The leg on the account whose statement was read, resolved
            through that account's own offer scope
            (``reconcile_service._transfers``), so it is this owner's and on
            this account by construction.  Its transfer has just settled.
        anchor_id: The ``budget.account_anchor_history`` row the statement is.
    """
    status_seam.record_side_clearing(
        transfer_side_leg(leg.transfer, is_income=leg.is_income), anchor_id,
    )
