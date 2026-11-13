"""
Shekel Budget App -- What a LOAN Destination Decides at the Transfer Form's Doors

What a LOAN destination decides about a recurring transfer's validity bounds,
at BOTH of the generic transfer form's doors, and the form's affordance for
it:

* the CREATE door (:func:`settle_first_occurrence`; plan step R7c-b, rulings
  **R-R60** and **R-R74** at plan step R7d-f-3, re-cut under **R-R81** at
  plan step R7d-g-2): where the definition WOULD be the destination loan's
  standing payment -- no active recurring transfer into the loan is older
  than it, the seam's own reading -- its first occurrence is the loan's
  first contractual installment, derived into the submission, and a stated stop
  is refused; otherwise this is a SECOND transfer, whose start is its
  OWNER's -- required, and refused at or before the loan's origination --
  and whose stop is its owner's;
* the UPDATE door (:func:`settle_destination_for_update`; plan step R7d-f-4,
  rulings **R-R76** and **R-R77**): the same two branches for the two edits
  that make a definition a recurring transfer into a loan, and a loan
  payment cannot be pointed at another account at all
  (:data:`LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION`);
* the form's lock affordance (:class:`LoanDestinationLocks`,
  :func:`loan_destination_locks` for the create form and, since plan step
  R7d-f-5, :func:`loan_destination_locks_for_edit` for the edit form; ruling
  **R-R79**): the ONE set of destination ids the transfer form ships so
  ``recurrence_form.js`` can lock both bound rows the moment a loan the
  definition would be standing in is chosen, computed from the producer the
  doors read so the browser decides no domain fact, and whether the
  destination may change at all;
* and, one module over, the entry helper every door calls once its write
  is flushed (:mod:`app.routes._standing_payment`; plan step R7d-g-2,
  ruling **R-R85**): the loan's standing payment -- whichever definition the
  seam names NOW -- has its start brought onto the loan's contract and its
  rows brought along.

The two doors share one derivation of the first occurrence
(:func:`settle_loan_start`), one reading of the standing identity
(:func:`~app.routes._recurrence_form_refusals.would_be_standing_payment`)
and one refusal per branch (:func:`_refuse_stop_on_a_new_loan_payment`,
:func:`_refuse_start_before_origination`), so an edit cannot author what a
create refuses (plan ledger row **REC-521**).

**Until plan step R7d-g-2 the doors derived the start for EVERY loan
destination** (plan ledger row **D50**).  Measured on a production clone
2026-09-13: a ``$50``/mo sweep into the Van created that day was written
with the Van's 2023-03-22 start, and the create path generated five past
sweeps (2026-04-22 .. 08-22), ``$250.00`` that never happened, which the
summed tier priced into the Van's payoff.  Under **R-R81** the standing
payment's start is the contract's and a second transfer's is its owner's;
the derive-then-discard shape is gone with the branch that produced it.

**Split out of :mod:`app.routes._transfer_creation_helpers` at plan step
R7d-f-5** (developer ruling **R-R78**, 2026-09-12), where it had accumulated
since R7c-b beside the four transfer-creation helpers until that module stood
at pylint's 1,000-line cap with this leaf's per-edit producer still to land --
the shape ruling **R-R75** took for ``balance_at/_context.py``: a pure move
graded by AST, then the step.  Route-layer module (leading underscore =
route-internal) rather than a service because every door here consumes Flask
globals (``flash``, ``current_user``, the redirect through
:class:`~app.routes._redirect_target.RedirectTarget`);
``CLAUDE.md::Architecture`` keeps services isolated from Flask.  Nothing here
creates or mutates transfer shadow transactions -- the doors settle the
submission and refuse, and the writes stay the route's -- so the transfer
invariants are unaffected.

**The doors settle a SUBMISSION, not the payload, since plan step R7e**
(finding **D36**): each returns the
:class:`~app.services.recurrence.RecurrenceSubmission` it was handed with what
it derives stated in it -- the loan's first occurrence and its reading, the
completed cadence of a partial move, the unbounded stop -- where it wrote
those into the payload as keys the next step tested for.
"""
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from flask import Response, flash
from flask_login import current_user

from app.enums import RecurrenceUnitEnum
from app.models.loan_params import LoanParams
from app.models.recurrence_rule import RecurrenceRule
from app.models.transfer_template import TransferTemplate
from app.routes._recurrence_form_refusals import (
    LOAN_PAYMENT_BOUND_IS_DERIVED,
    is_loan_payment_or_standing,
    would_be_standing_payment,
)
from app.routes._redirect_target import RedirectTarget
from app.schemas.validation import (
    RECURRENCE_NEEDS_A_START,
    RECURRENCE_STARTS_ON_KEY,
)
from app.services import loan_loaders, loan_recurrence_sync
from app.services.balance_at import BalanceContext
from app.services.recurrence import (
    NEVER_ENDS,
    UNREADABLE_CADENCE_MESSAGE,
    UNSET,
    EndBound,
    MonthReading,
    RecurrenceSubmission,
    SubmittedCadence,
    Unset,
    stored_cadence,
)


LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION: str = (
    "A loan payment pays the loan it was set up for, so it cannot be pointed "
    "at another account. Archive it to stop paying this loan, or set up a new "
    "transfer to pay another one."
)
"""Refusal shown when an edit would point a loan's STANDING payment elsewhere.

**Ruling R-R76** (developer 2026-09-12, taken at plan step R7d-f-4): a loan
payment cannot be moved onto a different account.  The twin of
:data:`~app.routes._recurrence_form_refusals.LOAN_PAYMENT_CANNOT_BE_ONE_TIME`,
and it covers the SAME UNION that refusal covers (the developer ruling of
2026-08-14 recorded on it): the loan's STANDING payment -- its oldest active
recurring transfer, the identity
:func:`~app.services.balance_at.is_standing_loan_payment` names and the edit
form locks both bound rows on -- OR a definition carrying a loan-payment
SETTINGS row (:func:`~app.routes._recurrence_form_refusals.is_loan_payment`),
which the loan dashboard's own door writes for a second payment too.  The
harm is the twin's one field over.  For the standing payment, a rule's
existence is how ``recurring_transfer_query`` FINDS a loan's payment, so
re-pointing the rule's owner leaves the loan amortizing with nothing
projecting a payment against it, exactly as deleting the rule does -- and the
moved row would carry the old loan's cached payoff (ruling **R-R56**) into a
column its new destination reads as the owner's word.  For a settings-carrying
payment the amount model prices the definition off the loan it pays into
(``cash_ledger._amount_source._loan_payment_cash``: a DERIVE-mode payment is
P&I plus escrow, which a savings destination cannot answer), so the moved row
would refuse on every read of its cash.  An adversarial review of this step
found the second half missing from the first cut, which asked the identity
alone.  The two real intents each have a door: archive it to stop paying this
loan, or create a payment for the other one.

Enforced in :func:`settle_destination_for_update`, and what it buys
structurally is stated there: every destination move that door settles is a
move of a NON-standing definition.
"""


SECOND_TRANSFER_STARTS_AFTER_ORIGINATION: str = (
    "'{loan}' originates on {origination}, so a transfer into it cannot start "
    "on or before that date. Choose a start after {origination}."
)
"""Refusal shown when a SECOND transfer into a loan would start before the loan.

**Ruling R-R81** (developer 2026-09-13, taken at plan step R7d-g): a
recurring transfer into a loan that already holds a payment carries its
OWNER's start, never the contract's -- and the one thing the loan still says
about that start is that it cannot precede the loan.  The boundary is the
transfer service's own (ruling **R-C**,
:func:`~app.services.loan_loaders.precedes_origination`: a payment due at or
before origination is ERASED by the fold while the cash side still debits
it), asked here of the date the owner typed so the door refuses with a
sentence rather than generation refusing the first row with the service's.
A payment due after origination but before the first contractual installment
is allowed, as the service allows it: an early extra payment is legitimate.

Formatted with the loan's name and its origination date.
"""


def settle_first_occurrence(
    submission: RecurrenceSubmission,
    *,
    to_account_id: int,
    redirect: RedirectTarget,
) -> RecurrenceSubmission | Response:
    """Settle a loan destination's first occurrence and stop, or refuse the start.

    **The CREATE-form half of the rule the EDIT form states as
    ``LOAN_PAYMENT_BOUND_IS_DERIVED``** (plan step R7c-b, developer ruling
    2026-08-15).  A loan's standing recurring payment's first occurrence is
    the loan's first contractual installment; the app writes it, so the
    form's control is locked and posts nothing, and this is what fills the
    gap the lock leaves.

    **It DERIVES before the rule is built rather than after**, which is the
    whole of what that ruling changed.  Before this, the generic transfer
    form authored a rule from the date the USER typed and had it silently
    replaced a few lines on -- the shape
    :func:`~app.services.loan_recurrence_sync.loan_cadence_start`'s own
    docstring records as worse than duplication.  The create path's later
    ``bind_rule_to_loan`` call, a no-op for the standing payment and a silent
    overwrite for a second transfer, went at plan step R7d-g-2 (ruling
    **R-R85**).

    **The refusal is here rather than in the schema, for the reason the update
    path's is in the route**: whether a start is required depends on the
    DESTINATION -- a loan the definition would be standing in derives one,
    anything else must state one -- and a schema never learns which accounts
    are loans.  So
    ``TransferTemplateCreateSchema`` carries
    ``recurrence_start_is_required = False`` and this states the rule with the
    schema's own message, exactly as
    ``_recurrence_form_helpers.resolve_recurrence_rule_for_update`` does.

    **Two branches for a loan destination, and WHICH is the loan's answer**
    (ruling **R-R81**, plan step R7d-g-2; the identity is
    :func:`~app.routes._recurrence_form_refusals.would_be_standing_payment`,
    which for a definition that does not exist yet reads "the loan holds no
    active payment"):

    * a loan holding NO active payment makes the definition being created
      its standing payment the moment it exists -- the oldest active transfer
      into the loan, the identity
      :func:`~app.services.balance_at.is_standing_loan_payment` names and the
      edit form locks both rows on.  :func:`settle_loan_start` writes the
      derived first occurrence, whatever the payload said, and
      :func:`_refuse_stop_on_a_new_loan_payment` refuses a stated stop
      (ruling **R-R60**): the loan's own payment carries no authored stop
      (ruling **R-R59**), and until plan step R7d-g-1 the column it would
      have landed in was the chokepoints' cache (plan ledger row **N-512**).
    * a loan that already holds one makes this a SECOND transfer into it,
      whose start is its OWNER's -- required like a savings transfer's, and
      refused at or before the loan's origination
      (:func:`_refuse_start_before_origination`) -- and whose stop is its
      owner's, graded against that start by the schema.  Until this step the
      door derived its start too, and the create path generated occurrences
      back to the loan's first installment (``$250.00`` on the clone, this
      module's docstring).

    A submission naming NO cadence authors no rule and is left alone: "does
    not repeat" needs no first occurrence.

    Args:
        submission: What the create form stated.
        to_account_id: The destination, which must already be
            ownership-checked -- this reads its loan parameters, so an
            unchecked id would be an IDOR.
        redirect: Where to send the user when the submission is refused.

    Returns:
        * The :class:`~app.services.recurrence.RecurrenceSubmission` the
          create authors from: *submission* with the loan's derived first
          occurrence stated in it on the standing branch, else as given --
          stating a first occurrence, or naming no cadence at all.
        * :class:`Response` -- the refusal redirect, returned verbatim -- an
          unstated first occurrence where the owner must state one, a stop
          stated for the standing payment, or a second transfer's start at
          or before its loan's origination.
    """
    cadence = submission.named_cadence
    if cadence is None:
        return submission
    params = loan_loaders.load_loan_params(to_account_id)
    if params is not None and would_be_standing_payment(
        to_account_id, current_user.id,
    ):
        settled = settle_loan_start(submission, params=params, unit=cadence.unit)
        # A create states one bound for both rules: what it submits is what
        # it writes (an unmentioned one authors the unbounded rule).
        refusal = _refuse_stop_on_a_new_loan_payment(
            settled.end_bound, redirect=redirect,
        )
        return settled if refusal is None else refusal
    if submission.starts_on is UNSET:
        for message in RECURRENCE_NEEDS_A_START[RECURRENCE_STARTS_ON_KEY]:
            flash(message, "danger")
        return redirect.to_response()
    if params is None:
        return submission
    refusal = _refuse_start_before_origination(
        params, submission.starts_on, redirect=redirect,
    )
    return submission if refusal is None else refusal


def settle_loan_start(
    submission: RecurrenceSubmission,
    *,
    params: LoanParams,
    unit: RecurrenceUnitEnum,
) -> RecurrenceSubmission:
    """Return *submission* with a loan destination's derived first occurrence stated.

    The one derivation both doors run for the recurring transfer that BECOMES
    a loan's standing payment (plan step R7c-b at create, R7d-f-4 at update;
    since plan step R7d-g-2 for a loan holding no active payment alone,
    ruling **R-R81**): the first occurrence is
    the loan's first contractual installment, read through the one producer
    :func:`~app.services.loan_recurrence_sync.loan_cadence_start`, and written
    into the submission BEFORE the rule is built so nothing is authored that
    a sync then replaces.  Whatever the submission said about the start is
    replaced.  The create form locks the control and posts nothing; a posted
    date -- a browser with the script off, a crafted POST, an edit form whose
    row is still open -- is exactly the input the door was written to stop
    being authored, and no worse an outcome than a discarded input.

    Args:
        submission: What the form stated (or a partial move's completion of
            it).  NOT mutated: the value is frozen, and a replacement is
            returned.
        params: The destination loan's terms, loaded by the caller -- each
            door loads them ONCE, after the destination is ownership-checked
            (an unchecked read would be an IDOR), and decides on the answer
            whether it has a loan destination at all before anything else is
            settled.  The load stays the caller's so that decision precedes
            every other settle of the submission (an adversarial review of plan step
            R7d-f-4 found the update door completing a partial submission's
            cadence before it knew the destination was a loan).
        unit: The cadence unit the derivation keys the nominal day on.

    Returns:
        *submission* with its start STATED as the loan's first contractual
        installment, read by the contract's day (ruling **R-R96**) -- so a
        weekday reading posted beside a date this replaces is replaced with it
        (plan step recurrence:R8-c): it read a date the rule will not carry.
        Stated rather than left unmentioned because both update branches read
        it as stated: the authoring branch as the start it builds from, the
        re-point branch as a start that replaces the stored one.
    """
    cadence_start = loan_recurrence_sync.loan_cadence_start(unit, params)
    return replace(
        submission,
        starts_on=cadence_start.starts_on,
        reading=MonthReading(nominal_day=cadence_start.nominal_day),
    )


def _refuse_stop_on_a_new_loan_payment(
    bound: EndBound | Unset, *, redirect: RedirectTarget,
) -> Response | None:
    """Refuse a stated stop for the definition that becomes its loan's payment.

    **The create door's half of ``LOAN_PAYMENT_BOUND_IS_DERIVED``** (plan step
    R7d-f-3, ruling **R-R60**, developer 2026-09-05), and since plan step
    R7d-f-4 the update door's too for the two edits that make a definition a
    loan's payment (:func:`settle_destination_for_update`).  The edit door
    refuses a stated bound for the loan's standing payment; neither door can
    ask that identity of a definition that is not that loan's recurring
    transfer yet, so both ask the same producer the other way round -- a
    loan holding no active payment makes this definition its payment -- and
    since plan step R7d-g-2 (ruling **R-R81**) that question is the BRANCH's:
    this runs only on the standing branch, so it asks nothing and refuses
    every real stop it is handed.

    **What "stated" means HERE is a real stop -- a date or a count -- and not
    the key's presence**, which is the one place these doors read the wire
    differently from the standing payment's (developer ruling 2026-09-12,
    taken at R7d-f-3; ruling **R-R77** extends it to the update door).  The
    standing payment's locked control is server-disabled, so any key it
    receives is a crafted POST; the create form's server render cannot know
    the destination, so it emits the "Ends" select ENABLED with "Never"
    preselected, and an ordinary browser posts ``never`` unless the script's
    lock ran -- and an edit form whose definition is not the standing payment
    yet renders the control enabled for the same reason.  Refusing on
    presence would therefore make the script the control -- the thing R-R60
    demotes to an affordance -- and refuse an owner who chose nothing on a
    page rendered before the loan lost its payment.  A stated "Never" and an
    absent key author the same row here (a create reads an unmentioned bound
    as unbounded, :meth:`~app.services.recurrence.RecurrenceSubmission.authored`,
    and the update door states ``NEVER_ENDS``
    for it under R-R77), the unbounded rule the loan's payment carries, so
    accepting both refuses nothing real.  The reason the edit door reads
    presence for the standing payment -- a posted START could be a stale echo
    of a derived date -- has no counterpart for a stop whose derived value is
    the constant ``NEVER_ENDS``.

    The inverted-window twin that stood beside this until plan step R7d-g-2
    (``_refuse_bound_before_derived_start``) went with the derive-for-every-
    loan branch: on this branch the written stop is ``NEVER_ENDS`` or
    refused, and a second transfer's pair is graded by the schema against
    the owner's own start.

    Args:
        bound: The closing bound THIS SUBMISSION states
            (:data:`~app.services.recurrence.UNSET` when it stated nothing;
            ``NEVER_ENDS`` when the form said "Never").  Never a stored bound:
            what an update leaves alone is not something its owner stated
            here (ruling **R-R77**).
        redirect: Where to send the user when the submission is refused.

    Returns:
        * ``None`` -- no real stop was stated.
        * :class:`Response` -- the refusal redirect, returned verbatim.
    """
    if bound is UNSET or bound == NEVER_ENDS:
        return None
    flash(LOAN_PAYMENT_BOUND_IS_DERIVED, "danger")
    return redirect.to_response()


def _refuse_start_before_origination(
    params: LoanParams, starts_on: date, *, redirect: RedirectTarget,
) -> Response | None:
    """Refuse a SECOND transfer's owner-typed start at or before its loan's origination.

    The one rule the loan states about a start that is the owner's (ruling
    **R-R81**, plan step R7d-g-2), through the boundary the transfer service
    already refuses a written row on
    (:func:`~app.services.loan_loaders.precedes_origination`, ruling
    **R-C**): asked of the typed date so the door refuses it with
    :data:`SECOND_TRANSFER_STARTS_AFTER_ORIGINATION` rather than generation
    refusing the first row with the service's sentence after the definition
    is flushed.  The service's guard stays the floor for the row the walk
    actually dates (an every-paycheck rule normalises its start onto a
    payday, which is the row's date and not this one).

    Args:
        params: The destination loan's terms, loaded by the caller after the
            destination was ownership-checked.  Its ``account`` names the
            loan in the sentence.
        starts_on: The first occurrence the owner stated -- the submitted
            one, or the stored one an update leaves alone.
        redirect: Where to send the user when the submission is refused.

    Returns:
        * ``None`` -- the start falls after the loan's origination.
        * :class:`Response` -- the refusal redirect, returned verbatim.
    """
    if not loan_loaders.precedes_origination(params, starts_on):
        return None
    flash(
        SECOND_TRANSFER_STARTS_AFTER_ORIGINATION.format(
            loan=params.account.name,
            origination=params.origination_date.strftime("%b %-d, %Y"),
        ),
        "danger",
    )
    return redirect.to_response()


def settle_destination_for_update(
    template: TransferTemplate,
    data: dict[str, Any],
    submission: RecurrenceSubmission,
    *,
    pass_ctx: BalanceContext,
    redirect: RedirectTarget,
) -> RecurrenceSubmission | Response:
    """Settle what an UPDATE's destination decides about the rule's two bounds.

    **The update door's twin of :func:`settle_first_occurrence`** (plan step
    R7d-f-4, plan ledger row **REC-521**).  The create door settles a loan
    destination's two bounds; the update door enforced none of it, in two
    shapes, because its refusals judge the STORED definition: a "does not
    repeat" transfer into a payment-less loan given a cadence (no rule, so
    :func:`~app.services.balance_at.is_standing_loan_payment` answered
    ``False`` and the authoring branch wrote the owner's typed start and
    stop), and a recurring transfer into savings MOVED onto a payment-less
    loan (the identity was judged against savings, then the field loop moved
    the column).  Either way the row was the loan's standing payment with an
    owner's word in both bound columns.  This runs BEFORE
    ``resolve_recurrence_rule_for_update`` and settles the definition the
    edit LEAVES rather than the one it found, through the producers the
    create door reads (:func:`settle_loan_start`,
    :func:`_refuse_stop_on_a_new_loan_payment`,
    :func:`_refuse_start_before_origination`,
    :func:`~app.routes._recurrence_form_refusals.would_be_standing_payment`),
    so an edit cannot author what a create refuses.

    Three rules, in the one order they can be asked in:

    1. **A loan payment cannot be pointed at another account** (ruling
       **R-R76**, :data:`LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION`): the loan's
       STANDING payment -- or, since plan step R7d-g-2, an archived transfer
       that becomes it on unarchive (ruling **R-R86**) -- or a definition
       carrying loan-payment settings: the union its twin refusal covers,
       spelled once for both and for the form's affordance as
       :func:`~app.routes._recurrence_form_refusals.is_loan_payment_or_standing`
       (plan step R7d-f-5).  Asked first, and of the stored
       definition, because it is what makes every move settled below a move
       of a definition whose bounds are its OWNER's: the bounds rule 2
       states cannot trip the refusal the edit door keeps for the standing
       payment (``refuse_recurrence_update``'s
       ``LOAN_PAYMENT_BOUND_IS_DERIVED`` arm asks the submission's
       ``states_a_bound``, and rule 2 states the start, its reading and the
       stop in it).
    2. **The standing branch, where the definition WOULD be the standing
       payment of the loan the edit leaves it paying into** -- no active
       recurring transfer into that loan is OLDER than it, the seam's own
       reading (ruling **R-R81**; the developer chose the seam's reading over
       emptiness after this leaf's adversarial review, because an older
       definition moved onto a paid loan IS the seam's standing payment the
       moment it lands there, and a door that wrote its owner's start had
       the sync overwrite it in the same request): for the two
       edits that make the definition a recurring transfer into it -- adding
       a cadence to a definition that had none, or moving one that repeats
       onto it -- the first occurrence is DERIVED, whatever the payload said,
       exactly as at create.  Stated in the submission, where both update
       branches read it: the authoring branch as the start it builds from,
       the re-point branch as a stated start that replaces the stored date.
       A partial submission that moves a recurring definition without
       restating its cadence is COMPLETED from the stored row
       (:func:`~app.services.recurrence.stored_cadence`) so the re-point
       branch runs and carries the derived start: "an unmentioned cadence
       leaves the stored one alone" and "restate the stored cadence" are one
       request, and completing it is what keeps the derivation on one branch
       rather than giving this function a rule writer of its own.  A cadence the
       application cannot read cannot be completed, so that submission is
       refused with the form's own repair sentence
       (:data:`~app.services.recurrence.UNREADABLE_CADENCE_MESSAGE`).  Which
       cadence is :func:`_complete_the_cadence`'s, asked only once the
       destination is known to be such a loan (an adversarial review of plan
       step R7d-f-4 found the first cut completing a partial move onto
       SAVINGS).  The closing bound is the create door's, one door over
       (ruling **R-R77**, developer 2026-09-12): a REAL stop this submission
       states -- a date or a count -- is refused, and where it states "Never"
       or NOTHING the write states the unbounded rule -- even where the moved
       definition stored a real stop.  That stop was the owner's word about a
       savings transfer, not about the loan, and the loan's own payment
       carries no stop (ruling **R-R59**); and a locked "Ends" row posts
       nothing, so "nothing" has to mean the unbounded rule for the
       affordance plan step R7d-f-5 adds to this form to be lockable at all.
    3. **The second-transfer branch, where that loan already holds an
       OLDER payment** (ruling **R-R81**, plan step R7d-g-2; until then this
       derived the start too, plan ledger row **D50**): nothing is derived.
       The start the edit leaves -- the submitted one, or the stored one an
       unmentioned start leaves alone -- is the OWNER's and is refused at or before
       the loan's origination (:func:`_refuse_start_before_origination`); an
       authoring edit that states none is refused one step on, by the
       recurrence step's own "needs a start" sentence, exactly as for a
       savings destination.  The stop is the owner's on the update door's
       ordinary reading: a stated one replaces, an unmentioned one leaves the
       stored one alone, and the schema has graded a submitted pair.

    Args:
        template: The definition being edited, owner-checked by the route.
            Read for its stored destination, rule, settings row and active
            flag; NOT mutated -- the field loop moves the column afterwards.
        data: The validated partial payload, read for the destination the
            edit leaves (``to_account_id``) and NOT mutated.  Every FK in it
            must already be ownership-checked -- this reads the destination's
            loan parameters, so an unchecked id would be an IDOR.
        submission: What the edit form stated about the recurrence, which
            the route lifted out of *data* before this.
        pass_ctx: The read pass the route built BEFORE any write.  Rule 1's
            identity is read off its loan-resolution memo, which the refusals
            after this read again for free.
        redirect: Where a refusal sends the user (the edit form).

    Returns:
        The :class:`~app.services.recurrence.RecurrenceSubmission` the
        recurrence step applies -- *submission* as given, or, on rule 2's
        branch, with the derived first occurrence, the completed cadence of a
        partial move and the unbounded stop (``NEVER_ENDS``, where the
        submission stated "Never" or nothing) stated in it -- or the refusal
        redirect the caller returns verbatim.
    """
    moving = (
        "to_account_id" in data
        and data["to_account_id"] != template.to_account_id
    )
    if moving and is_loan_payment_or_standing(template, pass_ctx):
        flash(LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION, "danger")
        return redirect.to_response()
    # The two edits this door settles: a cadence added to a definition that
    # had none, or a definition that repeats moved.  Neither, and the edit is
    # an ordinary one (a rename, an amount, a re-pointed cadence that stays
    # put) -- cheapest disqualifier first, so nothing below runs for it.
    rule = template.recurrence_rule
    if rule is not None and not moving:
        return submission
    # The DESTINATION is settled before the cadence is: whether it is a loan,
    # and whether that loan holds a payment, is what decides that anything at
    # all is derived, and the partial submission rule 2 completes must be
    # completed for the standing branch alone.  Read here, once, off the
    # destination the ownership check has already passed.
    to_account_id = data.get("to_account_id", template.to_account_id)
    params = loan_loaders.load_loan_params(to_account_id)
    if params is None:
        return submission
    if would_be_standing_payment(
        to_account_id, current_user.id, template_id=template.id,
    ):
        return _settle_standing_payment(
            rule, submission, params=params, redirect=redirect,
        )
    refusal = _refuse_second_transfers_start(
        rule, submission, params=params, redirect=redirect,
    )
    return submission if refusal is None else refusal


def _settle_standing_payment(
    rule: RecurrenceRule | None,
    submission: RecurrenceSubmission,
    *,
    params: LoanParams,
    redirect: RedirectTarget,
) -> RecurrenceSubmission | Response:
    """Rule 2 of :func:`settle_destination_for_update`: the standing branch.

    Reached once that door knows the edit is one it settles and that the
    definition would be the standing payment of the configured loan the edit
    leaves it paying into.
    States the first occurrence for the cadence the definition repeats on
    afterwards, refuses a real stated stop, and hands the unbounded rule to
    the recurrence step as the stop the write states.

    Args:
        rule: The definition's stored rule, or ``None`` when the edit is the
            one that authors it.
        submission: What the edit form stated.
        params: The destination loan's terms, loaded by the caller.
        redirect: Where a refusal sends the user.

    Returns:
        The settled submission, or the refusal redirect, as
        :func:`settle_destination_for_update` documents them.
    """
    completed = _complete_the_cadence(rule, submission, redirect=redirect)
    if isinstance(completed, Response):
        return completed
    cadence = completed.named_cadence
    if cadence is None:
        # Nothing repeats after this edit: nothing to derive, no stop to judge.
        return completed
    settled = settle_loan_start(completed, params=params, unit=cadence.unit)
    refusal = _refuse_stop_on_a_new_loan_payment(
        settled.end_bound, redirect=redirect,
    )
    if refusal is not None:
        return refusal
    # Handed to the recurrence step as a STATED bound: an unmentioned bound
    # means "leave the stored one alone" one step on, and the stored one is
    # not the loan's payment's to keep.
    return replace(settled, end_bound=NEVER_ENDS)


def _refuse_second_transfers_start(
    rule: RecurrenceRule | None,
    submission: RecurrenceSubmission,
    *,
    params: LoanParams,
    redirect: RedirectTarget,
) -> Response | None:
    """Rule 3 of :func:`settle_destination_for_update`: the second-transfer branch.

    Reached once that door knows the destination the edit leaves is a
    configured loan that already holds an older payment.  Nothing is derived;
    the
    one thing graded is the start the edit LEAVES against the loan's
    origination.

    Args:
        rule: The definition's stored rule, or ``None`` when it does not
            repeat yet.
        submission: What the edit form stated.
        params: The destination loan's terms, loaded by the caller.
        redirect: Where a refusal sends the user.

    Returns:
        The refusal redirect, or ``None`` when the edit may proceed -- which
        includes an authoring edit that states no start (the recurrence step
        refuses that with its own sentence) and an edit after which the
        definition does not repeat (nothing to grade).
    """
    # Whether it repeats after this edit: the submitted cadence where the form
    # mentioned one (``None`` is "Does not repeat"), else the stored rule's.
    if submission.mentions_the_cadence:
        repeats = submission.named_cadence is not None
    else:
        repeats = rule is not None
    if not repeats:
        return None
    # The start the edit LEAVES, through the submission's one merge rule.
    starts_on = submission.starts_on_over(
        rule.starts_on if rule is not None else None,
    )
    if starts_on is None:
        return None
    return _refuse_start_before_origination(params, starts_on, redirect=redirect)


def _complete_the_cadence(
    rule: RecurrenceRule | None,
    submission: RecurrenceSubmission,
    *,
    redirect: RedirectTarget,
) -> RecurrenceSubmission | Response:
    """Return *submission* naming the cadence the definition repeats on after this edit.

    Rule 2's first half (:func:`settle_destination_for_update`), asked once
    the caller knows the edit is one of the two it settles AND the
    destination is a loan: on WHICH cadence the definition repeats afterwards.
    The submitted one; or, for a partial submission that moves a repeating
    definition without restating its cadence, the stored one it leaves alone,
    which is COMPLETED into the submission so the re-point branch runs and
    carries the derived start -- unit, interval and placement, with the
    per-month ceiling left unmentioned, so the stored ceiling rides through
    the merge as it would beside any restated cadence.  A cleared cadence is
    "Does not repeat": nothing repeats after that edit, so there is nothing to
    derive and no stop to judge (whether the clear itself is allowed is
    ``refuse_recurrence_update``'s first rule); and a one-time transfer moved
    by a partial submission still does not repeat.  It was
    ``_cadence_unit_to_settle`` until plan step R7e, which wrote the stored
    cadence's three keys into the payload and returned the unit beside a
    refusal.

    Args:
        rule: The definition's stored rule, or ``None`` when it does not
            repeat yet.
        submission: What the edit form stated.
        redirect: Where the one refusal here -- a stored cadence the
            application cannot read, so cannot complete -- sends the user.

    Returns:
        The submission the loan branch settles: *submission* as given (its
        own cadence, a cleared one, or none for a definition that does not
        repeat), or completed with the stored cadence -- or the
        unreadable-cadence refusal redirect.
    """
    if submission.mentions_the_cadence or rule is None:
        return submission
    reading = stored_cadence(rule)
    if reading is None:
        flash(UNREADABLE_CADENCE_MESSAGE, "danger")
        return redirect.to_response()
    return replace(
        submission,
        cadence=SubmittedCadence(
            unit=reading.cadence.unit,
            interval_n=reading.cadence.interval_n,
            placement=reading.placement,
        ),
    )


@dataclass(frozen=True)
class LoanDestinationLocks:
    """What the transfer form's script may lock, and what it may not touch.

    The browser's half of the rules the two doors enforce, emitted by the
    server from the same producer the doors read so the script computes no
    domain fact -- it tests membership and nothing else.  Held as one value
    rather than two context keys so the parts cannot be passed apart, and
    with the invariant below enforced rather than documented.

    **Emitted by BOTH transfer forms since plan step R7d-f-5** (ruling
    **R-R79**).  A CREATE form's server render cannot lock either bound row --
    the destination is chosen in the form -- which is why the set rides to
    the browser at all.  An EDIT form knows its template: it locks both rows
    server-side where the definition's bounds are the loan's
    (:class:`~app.routes._recurrence_form_render.RecurrenceStart`,
    :class:`~app.routes._recurrence_form_render.RecurrenceEnd`), and it
    emits this set COMPUTED FOR THAT EDIT
    (:func:`loan_destination_locks_for_edit`), because the update door derives
    the same bounds for the two edits that make a definition a loan's
    standing payment (:func:`settle_destination_for_update`) and the
    form otherwise invited a start the save replaces and a stop the save
    refuses.  The server answers the STORED identity, the script answers the
    CHOSEN destination against a set the server graded, and the script never
    re-enables a control the server locked (it reads ``disabled`` once at
    load and leaves that flag alone) -- the two never answer one question.

    **ONE set since plan step R7d-g-2** (ruling **R-R81**), where it carried
    two: every configured loan for the "Starts on" row and the payment-less
    subset for "Ends".  The doors derived the start for EVERY loan destination
    then (plan ledger row **D50**); a second transfer's start is its owner's
    now, so the two rows lock on the same membership and the wider set named
    a lock the door no longer applies.

    Attributes:
        derived_for: The destinations a transfer into which BECOMES the
            loan's standing payment: the owner's configured loans holding no
            active recurring payment older than the definition the form is
            for -- none at all, on a create form
            (:func:`~app.routes._recurrence_form_refusals.would_be_standing_payment`).
            Both bound rows lock the moment one is chosen -- the first
            occurrence is derived from the loan's contract
            (:func:`settle_loan_start`) and the loan's own payment carries no
            authored stop (ruling **R-R59**), so the doors refuse a stop
            stated anyway (rulings **R-R60**, **R-R77**).  A loan already
            holding an older payment is NOT here: a second transfer's start
            and stop are its owner's.
        pinned_reason: ``None`` where the destination is the owner's to
            change.  On an edit form of a definition ruling **R-R76** pins to
            its loan (:func:`~app.routes._recurrence_form_refusals.is_loan_payment_or_standing`),
            the refusal's own sentence
            (:data:`LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION`): the form renders
            the destination control DISABLED with it as the help text, so the
            owner meets the rule before the save rather than as its flash.  A
            pinned definition has no destination choice to grade, so the set
            is EMPTY -- :meth:`__post_init__` refuses a pinned value carrying
            one, because a list claiming "choosing this loan derives the
            bounds" for a choice the door REFUSES would be the affordance and
            the door answering one question two ways.  A disabled control
            posts nothing; ``to_account_id`` is optional on the update schema
            and an absent key is "not moving", so the form still submits and
            the door still refuses a crafted move.
    """

    derived_for: tuple[int, ...]
    pinned_reason: str | None = None

    def __post_init__(self) -> None:
        """Refuse a value whose parts disagree.

        Enforced for the reason
        :class:`~app.routes._recurrence_form_render.RecurrenceEnd` enforces
        its own: this project has been burned by an invariant the generated
        ``__init__`` did not enforce.

        Raises:
            ValueError: A pinned value carries a non-empty set.
        """
        if self.pinned_reason is not None and self.derived_for:
            raise ValueError(
                f"LoanDestinationLocks is pinned ({self.pinned_reason!r}) yet "
                f"names destinations that would derive the bounds "
                f"({self.derived_for!r}): every move of a pinned definition is "
                f"refused, not derived, so the pair disagrees with itself."
            )


def loan_destination_locks(user_id: int) -> LoanDestinationLocks:
    """Return which of *user_id*'s loan destinations lock the bound rows on a CREATE form.

    The payment-less loans derive both bounds (a definition that does not
    exist yet is younger than every active one); nothing is pinned, because
    it has no destination to be pinned to yet.
    The edit form's producer is :func:`loan_destination_locks_for_edit`; both
    read :func:`_loan_destination_lock_set`, which is where the set is
    spelled.

    Args:
        user_id: The owner rendering the create form.

    Returns:
        The :class:`LoanDestinationLocks`, ascending by account id (the
        loan-id loader's order, preserved).
    """
    return _loan_destination_lock_set(user_id, template_id=None)


def loan_destination_locks_for_edit(
    template: TransferTemplate, pass_ctx: BalanceContext,
) -> LoanDestinationLocks:
    """Return what the EDIT form of *template* may lock, and whether it may move.

    The set :func:`settle_destination_for_update` would act on for THIS
    definition, decided in that door's own order so a reader can check the
    two against each other line by line (plan step R7d-f-5, ruling
    **R-R79**):

    1. A definition ruling **R-R76** pins to its loan -- the standing payment,
       an archived transfer that becomes it on unarchive (ruling **R-R86**),
       or one carrying loan-payment settings -- may not move at all, so it
       has no destination choice to grade: the set is empty and
       ``pinned_reason`` carries the refusal's sentence for the disabled
       control (the door's rule 1).
    2. Every other definition may be moved onto, or given a cadence into,
       any loan the owner holds; the door derives both bounds for exactly
       the loans THIS definition would be standing in -- those holding no
       active payment older than it (its rule 2) -- and leaves the owner's
       word alone for the rest (its rule 3).  The stored destination of a
       definition that repeats needs no carve-out (plan step R7d-f-5 had
       one): an active recurring transfer into a loan that is not its
       standing payment is younger than the one that is.

    Args:
        template: The definition being edited, owner-checked by the route.
            Read for its rule, its settings row and its active flag.
        pass_ctx: The read pass the route built for the render, whose
            loan-resolution memo answers the standing identity -- the same
            read :func:`~app.routes._recurrence_form_render.edit_form_recurrence_state`
            makes, so the second is free -- and whose ``user_id`` is the owner
            (a producer below the route takes the pass and no separate id,
            the 2026-08-16 ruling).

    Returns:
        The :class:`LoanDestinationLocks` for this edit.
    """
    if is_loan_payment_or_standing(template, pass_ctx):
        return LoanDestinationLocks(
            derived_for=(), pinned_reason=LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION,
        )
    return _loan_destination_lock_set(pass_ctx.user_id, template_id=template.id)


def _loan_destination_lock_set(
    user_id: int, *, template_id: int | None,
) -> LoanDestinationLocks:
    """Return the loans of *user_id* the definition would be standing in, unpinned.

    One lookup per configured loan, through the producer that states the
    "active recurring transfer into this account" filter once
    (:func:`~app.services.recurring_transfer_query.active_recurring_transfer_templates`)
    rather than a second query spelling the same filter without the account
    clause.  The owner's loans are a handful; the cost is one query each on
    a form GET, carrying that producer's two eager loads (the settings row
    and the price series) for a boolean -- the price of one spelling.

    Args:
        user_id: The owner rendering the form.
        template_id: The definition the form is for, or ``None`` on a create
            form -- what the identity is read against.

    Returns:
        The :class:`LoanDestinationLocks`, unpinned, ascending by account id
        (the loan-id loader's order, preserved).
    """
    return LoanDestinationLocks(
        derived_for=tuple(
            account_id
            for account_id in loan_loaders.load_loan_account_ids_for_user(user_id)
            if would_be_standing_payment(account_id, user_id, template_id=template_id)
        ),
    )


__all__ = [
    "LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION",
    "SECOND_TRANSFER_STARTS_AFTER_ORIGINATION",
    "LoanDestinationLocks",
    "loan_destination_locks",
    "loan_destination_locks_for_edit",
    "settle_destination_for_update",
    "settle_first_occurrence",
    "settle_loan_start",
]
