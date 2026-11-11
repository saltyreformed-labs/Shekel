"""
Shekel Budget App -- The Loan's STANDING PAYMENT After a Door's Write

Two route-layer helpers around the loan's standing payment -- the oldest
active recurring transfer into a loan, whose ``starts_on`` is a stored
DERIVED value (ruling **R-R29**: the loan's first contractual installment,
the cadence anchor a month-unit rule cannot fire without):

* :func:`sync_loan_payment_start_or_refuse` -- the ONE entry helper every
  door where a definition can BECOME the standing payment, or where the
  contract's first installment can move, calls once its write is flushed
  (plan step R7d-g-2, ruling **R-R85**): loan setup, the loan-params edit,
  archive, unarchive, hard-delete and the transfer update door.  It brings
  the standing payment's start onto the contract and its generated rows
  along, and refuses the door whole where the re-derived start would pass a
  stop the owner authored.
* :func:`regenerate_or_refuse` -- the transfer MAINTAIN pass with the one
  arm the shared chooser flow leaves to its callers, the transfer service's
  own refusal translated into a flash; shared by the entry helper and the
  transfer update door.

Split from :mod:`app.routes._loan_destination` at plan step R7d-g-2 the
moment the rows-follow arm landed there and pushed that module past
pylint's 1,000 lines: what a loan destination decides at the transfer form's
two DOORS is one subject, and what happens to the standing payment after ANY
door's write is another.  Route-layer module (leading underscore =
route-internal) rather than a service because both helpers ``flash`` and
redirect; ``CLAUDE.md::Architecture`` keeps services isolated from Flask.
Nothing here creates or mutates transfer shadow transactions directly: the
maintain pass reaches rows through ``transfer_service``, so the transfer
invariants are its guarantee.
"""
from datetime import date

from flask import Response, flash

from app.exceptions import (
    NotFoundError,
    ValidationError as ShekelValidationError,
)
from app.extensions import db
from app.models.transfer_template import TransferTemplate
from app.routes._recurrence_conflict_chooser import (
    PreEditTemplateState,
    regenerate_or_conflict_chooser,
)
from app.routes._redirect_target import RedirectTarget
from app.routes._transfer_creation_helpers import TRANSFER_TEMPLATE_KIND
from app.services import loan_recurrence_sync, template_amount_service
from app.utils.dates import display_today


def sync_loan_payment_start_or_refuse(
    account_id: int, *, redirect: RedirectTarget, rows_follow: bool = True,
) -> Response | None:
    """Bring *account_id*'s standing payment's start onto its contract, or refuse.

    **The ONE entry helper every door where a definition can BECOME a loan's
    standing payment calls after its write is flushed** (plan step R7d-g-2,
    ruling **R-R85**; it began life as ``routes/loan/params.py``'s private
    at plan step R7d-g-1).  Under ruling **R-R29** the standing payment's
    ``starts_on`` is a stored derived value, so every door that can change
    WHICH definition is standing, or WHAT the contract says, re-derives it:

    * loan setup, where an account already holding a recurring transfer
      becomes a loan (ruling **R-R81**);
    * the loan-params edit, where ``payment_day`` moves the first
      installment;
    * archive and hard-delete, where the next-oldest transfer into the loan
      is PROMOTED;
    * unarchive, where the restored transfer is standing once more -- or
      newly, if it is older than the live payment, since the seam names the
      oldest active one;
    * the transfer update door, where the standing payment's cadence UNIT
      moves (every paycheck to monthly keeps a payday as the monthly day
      otherwise).

    Each door calls this with the loan the definition pays into, and the
    producer (:func:`~app.services.loan_recurrence_sync.sync_loan_payment_start`)
    asks the seam who is standing NOW -- after the flush -- and writes for
    that one definition, idempotently: a door where nothing moved costs a
    lookup and writes nothing.  That is what closes plan ledger row **D35**'s
    residue: the contract is complete at every door rather than healed at
    the next params edit.  Rejected (developer 2026-09-13): binding the
    restored rule at unarchive only where the loan holds no OTHER active
    payment, which leaves the older-unarchived-beside-a-live-payment case
    with the seam naming a standing payment whose start is its owner's.

    **The moved definition's ROWS follow its rule** (developer 2026-09-13,
    after this leaf's adversarial review).  A rule whose start moved names
    different occurrences; rows left on the old ones are a second home for
    one derived fact, and at the unarchive door they were worse than stale:
    the restored rows sat on the owner's old dates while the plain
    fill-forward wrote the contract's beside them, two debits a month.  So
    where the producer reports a move, the transfer MAINTAIN pass runs for
    that definition from today (ruling **R-R19**'s pass, the update door's
    own: rows the rule still names are kept, missing ones created, rows it
    no longer names retired unless they carry the owner's records, which are
    retained and flashed).  No amount changes here, so the chooser never
    renders.  The transfer update door and the unarchive door pass
    ``rows_follow=False``: each runs that pass itself for the definition in
    hand afterwards -- the update door for the edited one, the only
    definition the sync can move there (the standing payment cannot move
    loans, R-R76; one moved INTO a loan had its start derived by the door),
    and the unarchive door for the restored one whether or not it moved --
    and a second pass ahead of the update door's would flash a retained
    notice its chooser path then discards.

    **The one refusal.**  The stored closing bound is its owner's word
    (ruling **R-R82** honours one authored before the definition became the
    standing payment), and ``ck_recurrence_rules_valid_window`` refuses the
    pair a re-derived start moving past it would leave.  The producer
    refuses that pair before writing, naming the transfer; this rolls the
    door's pending write back, flashes the sentence and redirects, so the
    door is refused WHOLE rather than surfacing an ``IntegrityError`` from
    the flush.  The remedy is the owner's: archive that transfer, or set it
    up again without an end date.  The maintain pass's own refusals -- the
    transfer service's -- are translated the same way; a standing payment's
    rows start at the first installment, after origination by construction,
    so none is expected, and an unhandled one would be a 500 on a clean
    action.

    Args:
        account_id: The loan account the definition pays into, owner-checked
            by the route.  Any account may be passed: the producer returns
            before reading anything for one that is not a configured loan
            or holds no recurring transfer.
        redirect: Where a refusal sends the user.
        rows_follow: Whether a moved definition's rows are maintained here.
            ``True`` at every door but the transfer update door and the
            unarchive door, whose own passes follow.

    Returns:
        ``None`` when the start is in step (or nothing needed writing), else
        the refusal redirect with the pending write rolled back and the
        sentence flashed.
    """
    try:
        moved = loan_recurrence_sync.sync_loan_payment_start(account_id)
    except ShekelValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect.to_response()
    if moved is None or not rows_follow:
        return None
    return regenerate_or_refuse(
        moved,
        PreEditTemplateState(
            series=template_amount_service.series_image(moved),
            had_recurrence_rule=True,
            # A transfer names two accounts; no account question is asked.
            account_id=None,
        ),
        display_today(),
        redirect=redirect,
    )


def regenerate_or_refuse(
    template: TransferTemplate,
    before: PreEditTemplateState,
    effective_from: date,
    *,
    redirect: RedirectTarget,
) -> Response | None:
    """Run the transfer maintain pass for *template*, translating a service refusal.

    :func:`~app.routes._recurrence_conflict_chooser.regenerate_or_conflict_chooser`
    over the transfer kind, with the one arm that function leaves to its
    callers: the transfer service refuses a row it will not write -- a
    payment dated at or before its loan's origination (ruling **R-C**), a
    transfer out of a loan -- and raises out of the pass.  The create path
    catches that at materialisation and rolls the pending create back
    (``transfers/_instances._rollback_and_refuse``); until plan step
    R7d-g-2 the update door did not, and ruling **R-R81** made the state
    reachable there: a second transfer's typed start is graded against the
    origination at the door, but an every-paycheck rule dates its rows at
    the START of their paychecks (``compute_due_date``, ruling R-R95), so a
    start typed the day after origination can put the first row on or before
    it.
    The service stays the one floor for the row's date; this turns its
    refusal into the edit form's flash (developer 2026-09-13, after this
    leaf's adversarial review).  Shared with the entry helper above, whose
    maintain pass runs the same service.

    Args:
        template: The transfer template whose rows follow its definition.
        before: The template's pre-edit state, as the chooser reads it.
        effective_from: The date the pass maintains from.
        redirect: Where a refusal sends the user.

    Returns:
        The chooser response, the refusal redirect (rolled back, flashed), or
        ``None`` when the pass ran through.
    """
    try:
        return regenerate_or_conflict_chooser(
            template, before, effective_from, TRANSFER_TEMPLATE_KIND,
        )
    except (NotFoundError, ShekelValidationError) as exc:
        db.session.rollback()
        flash(f"Could not update transfer: {exc}", "danger")
        return redirect.to_response()


__all__ = [
    "regenerate_or_refuse",
    "sync_loan_payment_start_or_refuse",
]
