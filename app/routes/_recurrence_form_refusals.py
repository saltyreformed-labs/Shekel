"""
Shekel Budget App -- Why a submitted recurrence change may be REFUSED

The refusal half of :mod:`app.routes._recurrence_form_helpers`, split out at
plan step R7c-b when that module met the 1,000-line cap for the fourth time.
The seam is the one the module had already grown: everything here answers "may
this submission be applied at all", and everything there APPLIES one.  Each
refusal is a rule the form also expresses as an affordance -- a disabled
control, an unset ``<select>`` -- and disabling is never the guard, because a
client may post whatever it likes.

Four rules, and :func:`refuse_recurrence_update` asks them in the one order
they can be asked in:

1. a LOAN PAYMENT may not be made one-time
   (:data:`LOAN_PAYMENT_CANNOT_BE_ONE_TIME`);
2. a definition whose validity window the app DERIVES may not have one stated
   for it (:data:`LOAN_PAYMENT_BOUND_IS_DERIVED`);
3. a rule the form could not DISPLAY may not be cleared by the empty
   submission that state produces
   (:data:`UNREPAIRED_CADENCE_CANNOT_BE_CLEARED`);
4. no edit may leave the rule stopping before it starts
   (:func:`refuse_inverted_window`).

A FIFTH is asked later, by each door once its edit is applied, because it
grades the state the save would LEAVE rather than the submission: no edit may
leave a still-projected row of the definition answering an occurrence its
books drop, or inside the books of the account it sits on
(:func:`refuse_stranding_save`, plan step ``pay_calendar:C18-a``).

**:class:`RecurrenceFormContext` is DEFINED here**, one layer below the
authoring helpers that also take it, and that is what keeps the split a
boundary rather than a pair of modules that need each other.  Until plan step
R7d-f the two refusal entries took the closing bound and the redirect as bare
arguments, because the context lived in the authoring module and importing it
here would have closed a cycle; the inverted-window refusal then came to need
a THIRD value of the caller's -- the read pass -- and three bare copies of a
parameter object's fields is the shape the object exists to remove.  So the
leaf moved (``CLAUDE.md`` rule 14's placement clause): the helpers import the
context from here, and every refusal reads it whole beside the pass.

Route-layer module rather than service because every refusal ``flash``es and
redirects (the latter via
:class:`~app.routes._redirect_target.RedirectTarget`);
``CLAUDE.md::Architecture`` keeps services isolated from Flask globals.  The
leading underscore marks the module as route-internal.
"""
from dataclasses import dataclass
from datetime import date
from typing import Any

from flask import Response, flash
from flask_login import current_user

from app.extensions import db
from app.routes._redirect_target import RedirectTarget
from app.schemas.validation import (
    RECURRENCE_REPEATS_ON_KEY,
    RECURRENCE_STARTS_ON_KEY,
    end_bound_before_start_message,
)
from app.services import loan_loaders, planned_rows_books
from app.services.cash_ledger import is_loan_payment_definition
from app.services.balance_at import (
    BalanceContext,
    is_standing_loan_payment,
)
from app.services.recurrence import (
    EndBound,
    end_bound_from_columns,
    stored_cadence,
)
from app.services.definition_unarchive import UnarchiveScope
from app.services.recurring_transfer_query import (
    active_recurring_transfer_templates,
)

LOAN_PAYMENT_CANNOT_BE_ONE_TIME: str = (
    "A loan payment repeats for the life of the loan, so it cannot be made "
    "one-time. Choose a different pattern to change how often it repeats, or "
    "archive it to stop paying."
)
"""Refusal shown when an edit tries to clear a loan payment's recurrence."""


UNREPAIRED_CADENCE_CANNOT_BE_CLEARED: str = (
    "This recurring definition uses a repeat pattern that is no longer "
    "available, so the form could not show you how often it repeats -- and an "
    "empty choice here would delete the schedule. Nothing was saved. Choose "
    "how often it repeats, then save."
)
"""Refusal shown when an edit would clear a rule the form could not display.

**The half of :data:`~app.services.recurrence.UNREADABLE_CADENCE_MESSAGE`'s
promise that has to live on the SERVER.**  That message tells the user "saving
it unchanged will be refused", and before plan step R7b-2 the picker kept that
promise by keeping the stored pattern as a trailing selected ``<option>``: the
save then carried an id the write door refused.  The two-axis controls carry no
pattern id, so they render UNSET -- which means the unit ``<select>``'s FIRST
entry is selected, and that entry is the empty "Does not repeat" one whose save
DELETES the rule and sweeps its future rows.

An unrepaired edit and a deliberate clear are therefore the same bytes on the
wire, and no hidden field can separate them -- a client may drop one.  The
server can, from two facts it already holds: the stored rule names a pattern
this application does not model, and the submission names no cadence.  A form
that could not offer this rule's cadence cannot have collected the user's
intent to remove it, so the empty submission is refused rather than acted on.
"""


LOAN_PAYMENT_BOUND_IS_DERIVED: str = (
    "A loan payment runs from the loan's first installment until it is paid "
    "off, so when it starts and stops is not something you set. Change the "
    "loan's terms to move either one, or archive the payment to stop it early."
)
"""Refusal shown when a submission states a bound the app DERIVES.

**The server half of a control the form renders disabled**, and it needs both
halves for the reason ``UNREPAIRED_CADENCE_CANNOT_BE_CLEARED`` does: disabling
is an affordance, and a client may post whatever it likes.

``loan_recurrence_sync`` owns BOTH of the loan's standing payment's validity
bounds, each in its own way since ruling **R-R29**: the OPENING bound is
WRITTEN -- the loan's first contractual installment, re-derived when the
loan's ``payment_day`` moves -- and the CLOSING bound is DERIVED, the loan's
payoff resolved through the composed door on every read and stored nowhere
(plan step R7d-g).  A start accepted here would be silently discarded by the
next such edit, which is worse than refusing it, and the OPENING half is
worse still: it is what keeps a payment from generating before the loan
originates, measured at $3,220.92 of phantom cash debits on a mortgage
closing one month out.  A stop accepted here would state a plan the loan
does not know about -- so the loan's own payment runs to the payoff and
archiving is the door to stop it early (ruling **R-R59**, developer
2026-09-05, taken at plan step R7d-f: the control stays locked).  Until R7d-g
it would also have been discarded by the next payoff-affecting edit, which
wrote the derived payoff over it.

**The CREATE door states the same rule for a definition that does not exist
yet** (plan step R7d-f-3, ruling **R-R60**):
``_loan_destination.settle_first_occurrence`` derives the opening
bound for any loan destination and flashes THIS sentence for a stop stated
where the loan holds no active payment -- the definition being created would
be that payment.  It reads "stated" as a real stop rather than as the key's
presence, because that form's server render cannot lock the control and emits
"Never" as its default; the reasoning is on
``_refuse_stop_on_a_new_loan_payment``.  **And the UPDATE door states it for
the two edits that make a definition a loan's recurring transfer** (plan step
R7d-f-4, plan ledger row **REC-521**): a cadence added to a one-time transfer
into a loan, or a repeating transfer moved onto one, through the same reading
(``_loan_destination.settle_destination_for_update``, rulings
**R-R76** and **R-R77**) -- ahead of :func:`refuse_recurrence_update`, whose
presence rule below is the standing payment's alone.

**Which definitions it fires for is :func:`bounds_are_the_loans` --
``balance_at.is_standing_loan_payment``, or its archived reading since plan
step R7d-g-2 -- not :func:`is_loan_payment`** (plan step R7b-4; read off the
pass since R7d-f).
Those are different questions and asking the second was a defect an
adversarial review of plan step R7b-3 found: a template can carry loan-payment
SETTINGS without being the loan's standing payment, and its form then locked a
control for a value nothing wrote.  See that predicate's docstring.
"""


@dataclass(frozen=True)
class RecurrenceFormContext:
    """Recurrence-form processing options shared across the F-24 helpers.

    A parameter object, not a single domain concept: it groups the three
    otherwise-independent knobs the helpers read so the verbatim-triplicated
    signature tail collapses to one argument (and ``resolve`` forwards it
    unchanged).

    Bundles the two inputs that
    :func:`~app.routes._recurrence_form_helpers.recurrence_spec_from_form`,
    :func:`~app.routes._recurrence_form_helpers.update_recurrence_rule_from_form`,
    :func:`~app.routes._recurrence_form_helpers.resolve_recurrence_rule_for_update`
    and the two refusal entries below share verbatim: the form's closing
    bound and the validation-error redirect target.  Collapsing the
    formerly-triplicated ``end_bound`` / ``redirect_endpoint`` /
    ``redirect_endpoint_kwargs`` signature tail into one object both removes
    the duplication and clears the per-helper ``too-many-arguments`` count.
    A third field, the transaction-vs-transfer ``include_due_day_of_month``
    flag, left with the column it guarded at plan step recurrence:R5-a
    (ruling **R-R96**).

    **Defined in THIS module since plan step R7d-f**, having lived in the
    authoring helpers: the refusals read two of its fields and could not
    import it across the one-way seam, so they took those two as bare
    arguments until the inverted-window door needed the read pass beside
    them.  See the module docstring.

    **It does NOT carry the read pass**, and the omission is deliberate: the
    create preamble builds this context with no stored definition to read and
    no pass in hand, and a field half the constructors leave empty is an
    optional the other half must remember to check.  The pass is the UPDATE
    path's own argument (``pass_ctx``), passed beside this object by the two
    entries that judge a stored definition.

    Attributes:
        end_bound: When the recurrence STOPS, as the ONE value the submission
            composed (:class:`~app.services.recurrence.EndBound`), or ``None``
            when the form STATED NOTHING about it.

            The two are different requests and the helpers act on them
            differently -- a stated bound REPLACES the rule's, an absent one
            leaves it alone -- which is the same present-versus-absent
            distinction ``recurrence_unit`` turns on, and it is what lets a
            form whose bound is derived (a loan payment) render the control
            disabled and have the save mean "not mine to state" rather than
            "ends never".  It carried the raw ``end_date`` until plan step
            R7b-3, where a date was the only bound a form could state and the
            distinction had nothing to express.
        redirect: Where to redirect on a recoverable validation failure
            (a start period that is not this user's).
    """

    end_bound: EndBound | None
    redirect: RedirectTarget


def refuse_inverted_window(
    template: Any,
    data: dict[str, Any],
    *,
    ctx: RecurrenceFormContext,
) -> Response | None:
    """Refuse an UPDATE that would leave the rule stopping before it starts.

    **The update door's half of a rule the schema can only half see** (plan
    step R7c-b).  ``require_end_bound_after_start`` compares the two values a
    SUBMISSION states; an update states at most one of them and keeps the
    stored other, so the pair the save would actually write is invisible to it.
    Two ordinary edits produce it and both were unhandled 500s:

    * clearing "Starts on" while setting an earlier "Ends on" -- the cleared
      date box drops the key, the stored start is kept, and the new bound lands
      below it;
    * moving "Starts on" PAST a stored ``end_date`` without touching the "Ends"
      control, which no reviewer listed and which the schema cannot see at all
      because the bound is not in the payload.

    **``ck_recurrence_rules_valid_window`` stands behind this since plan step
    R7d-g**, and this door is still the one that can REPORT the mistake: the
    constraint refuses the pair at the flush as an ``IntegrityError``, which
    is a 500 and not a sentence, so the door grades the same pair first and
    says so.  The CHECK was held back until R7d-g on a developer ruling
    (2026-08-15): the columns carried DERIVED loan-payment windows beside
    authored ones, and an empty derived window was a correct answer a
    constraint could not tell from a user's mistake.  Nothing derived is
    stored there now.  See
    :func:`~app.schemas.validation.end_bound_before_start_message`, which both
    doors word the refusal with.

    Reads the EFFECTIVE pair on both sides -- submitted where the form stated
    it, stored where it did not -- which is the same present-versus-absent rule
    :func:`update_recurrence_rule_from_form` applies when it writes them.  The
    stored half is the owner's word for EVERY definition (plan step R7d-g), a
    second recurring transfer into a loan included, so it is graded as
    stated.  Until R7d-g it was read through the composed door's cache arm
    (ruling **R-R56**), because the loan's standing payment stored the
    chokepoints' payoff there and a loan cleared before its first installment
    stored an INVERTED pair through the sync's own production door; that arm
    is deleted with the writers, and the stored pair of a standing payment
    cannot invert (its closing bound is NULL, or an owner's stop the
    loan-params door refuses to move the start past).

    The stored rule is NOT resolved here, deliberately.  The comparison needs
    the two bound columns and the start, none of which decodes the cadence,
    so a rule whose stored pattern the application no longer models still
    reaches its repair save without this door raising on the way -- the same
    property the render side states for its own read of the bound.

    Args:
        template: The template being updated.  A template with no rule is
            skipped: the create branch authors from a submission the schema has
            already compared.
        data: The validated payload, NOT mutated -- the delegated helper pops
            the recurrence keys afterwards and must still see them.
        ctx: The form context, read for the submitted closing bound (``None``
            when the form stated none) and for where a refusal sends the user.

    Returns:
        * ``None`` -- the window is well-formed, or nothing this edit states
          can invert it.
        * :class:`Response` -- the refusal redirect, returned verbatim.
    """
    rule = template.recurrence_rule
    if rule is None or data.get("recurrence_unit") is None:
        return None
    starts_on = (
        data[RECURRENCE_STARTS_ON_KEY] if RECURRENCE_STARTS_ON_KEY in data else rule.starts_on
    )
    bound = (
        ctx.end_bound if ctx.end_bound is not None
        else end_bound_from_columns(rule.end_date, rule.max_occurrences)
    )
    end_date = bound.columns().end_date
    if starts_on is None or end_date is None or end_date >= starts_on:
        return None
    flash(end_bound_before_start_message(end_date, starts_on), "danger")
    return ctx.redirect.to_response()


def is_loan_payment(template: Any) -> bool:
    """Return whether *template* is a recurring loan payment.

    Public since plan step R7b-3, which gave it a second caller: the transfer
    edit route asks it to decide whether the "Ends" control renders locked.

    **Not the only place the question is asked**, and an adversarial review
    corrected an earlier claim here that said so:
    ``cash_ledger._amount_source._is_loan_payment`` answers the same
    ``settings is not None`` question about a TRANSFER row.  Pre-existing, and
    a wider concern than this step -- what is fixed here is the claim.

    A :class:`~app.models.loan_payment_settings.LoanPaymentSettings` row is
    present "only for recurring loan payments" (decision B), and it carries the
    standing ``extra_principal`` that amount rule 4 prices into every row the
    definition generates and the seam's forward plan prices into every
    occurrence no row covers (``recurring_transfer_query.loan_payment_config``
    is the read; the loan-level ``loan_standing_extra`` that threaded one
    picked definition's extra into the resolver went at plan step R7d-g-3).

    **ONE reading of the settings-row test**: the amount model's
    :func:`~app.services.cash_ledger.is_loan_payment_definition`, which is
    how a written row is classified as a loan payment and how the balance
    seam's estimate classifies a definition whose row does not exist yet.  A
    third spelling here was what R16-b-2's adversarial review found; the
    kind-agnostic ``getattr`` lives there now, so a transaction template still
    answers ``False`` without a query.

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate`` -- or,
            since plan step salary:R15-c, the ``PaycheckLine`` whose
            update runs the same dispatcher; it carries no settings row and
            answers ``False`` the way a transaction template does.

    Returns:
        ``True`` when the template carries loan-payment settings.
    """
    return is_loan_payment_definition(template)


def would_be_standing_payment(
    account_id: int, user_id: int, *, template_id: int | None = None,
) -> bool:
    """Return whether a definition into loan *account_id* WOULD be its standing payment.

    **The door-time reading of the standing-payment identity, spelled once
    for its readers**: the two transfer-form doors' branch gate
    (``_loan_destination.settle_first_occurrence`` and
    ``settle_destination_for_update``, rulings **R-R60**, **R-R77** and, since
    plan step R7d-g-2, **R-R81**: the standing branch runs ONLY where this
    answers ``True``), the form's affordance
    (``_loan_destination.loan_destination_locks``) and the archived reading
    of the identity (:func:`bounds_are_the_loans`).

    **It answers the SEAM's own question**, not an approximation of it
    (developer 2026-09-13, after R7d-g-2's adversarial review).  The standing
    payment is the loan's OLDEST active recurring transfer -- the first of
    :func:`~app.services.recurring_transfer_query.active_recurring_transfer_templates`,
    the producer the read pass memoises for
    :func:`~app.services.balance_at.is_standing_loan_payment` -- so a
    definition would be standing once saved and active exactly when no
    active recurring transfer into the loan is OLDER than it.  A definition
    that does not exist yet (*template_id* ``None``) is younger than every
    active one, so it is standing only where the loan holds none.  The first
    cut asked emptiness alone, and emptiness disagrees with the seam in one
    shape: an ARCHIVED or MOVED definition older than the loan's live payment
    is the seam's standing payment the moment it is active there, so a door
    that read it as a second wrote the owner's start and the next sync
    overwrote it -- the derive-then-discard shape ruling **R-R81** rejects.
    This is a READ of the seam's tie-break (ordered by id, ruling **R-R35**),
    not a second spelling of it: the ordering is the producer's.

    Asked of the producer directly rather than off a pass: a create resolves
    no loan before it generates, so there is no memo to read and building a
    pass here would fold the loan for one boolean.

    Lived in ``_loan_destination`` as ``_loan_holds_no_active_payment``
    until plan step R7d-g-2, when the archived reading below needed it one
    module down (that module imports this one, never the reverse).

    Args:
        account_id: A CONFIGURED loan's account.  The callers have already
            established that (``load_loan_params`` for the doors, the loan-id
            loader for the affordance); asked of a savings account this would
            answer ``True`` and mean nothing.
        user_id: The owner, scoping the query as the producer requires.
        template_id: The definition's id where it exists -- an edit, an
            archived transfer -- or ``None`` for one being created.

    Returns:
        ``True`` when no active recurring transfer into the loan is older
        than the definition.
    """
    actives = active_recurring_transfer_templates(account_id, user_id)
    if not actives:
        return True
    return template_id is not None and actives[0].id > template_id


def bounds_are_the_loans(template: Any, pass_ctx: BalanceContext) -> bool:
    """Return whether *template*'s two validity bounds are the LOAN's to state.

    **The ONE predicate behind every per-bound rule the edit form applies**
    (plan step R7d-g-2, ruling **R-R86**), read by the form's two locks
    (:func:`~app.routes._recurrence_form_render.edit_form_recurrence_state`),
    the presence refusal (:func:`refuse_recurrence_update`'s
    ``LOAN_PAYMENT_BOUND_IS_DERIVED`` arm) and, through
    :func:`is_loan_payment_or_standing`, the pin that keeps a loan payment on
    its loan.  Two readings, one answer:

    * the definition IS the loan's standing payment -- its oldest active
      recurring transfer, off the pass's loan resolution
      (:func:`~app.services.balance_at.is_standing_loan_payment`); or
    * it is ARCHIVED, repeats, and pays into a configured loan holding no
      active payment OLDER than it, so it BECOMES the standing payment the
      moment it is unarchived (:func:`would_be_standing_payment`, the seam's
      own oldest-active reading).

    The second reading is plan ledger row **REC-522** closed structurally.
    The seam's identity reads the ACTIVE set, so an archived former payment
    answered ``False`` and its edit form unlocked both bound rows: the owner
    could type a start and a stop that rode onto the loan's standing payment
    when it was unarchived, with no sync to correct the start and the stop
    stored as an owner's word in the column the composed door reads.  With
    this reading the archived form renders exactly as the standing payment's
    does -- both rows locked, the destination pinned -- and posts nothing; a
    crafted submission stating a bound is refused; the stored bounds ride
    through every edit untouched; and the UNARCHIVE door is where the start
    is re-derived (ruling **R-R85**) and a stop the owner authored earlier is
    honoured (ruling **R-R82**).  Nothing but an owner's submission writes the
    closing-bound column, and the archived form cannot make one.  Rejected
    (developer 2026-09-13): running the update door's loan branch for the
    archived case -- derive and write the start, refuse a real stop, read
    "nothing" as the unbounded rule -- which erases a stored owner's stop on
    any edit of the archived transfer and, for a settings-carrying archived
    payment (pinned, so shipped no lock affordance), silently replaces the
    start its open row posts.

    Cheapest disqualifiers first on the archived arm: the destination FK
    (absent on a transaction template, which answers before its other
    columns are read), the rule and ``is_active`` columns are in hand, the
    loan-params load is one primary-key read, and the set query runs only for
    an archived recurring transfer into a configured loan.

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate``.  A
            transaction template pays into no account and answers ``False``
            on both readings (``getattr`` on the FK column keeps the archived
            arm kind-agnostic, as the seam's own reader is).
        pass_ctx: The read pass the route built, whose loan-resolution memo
            answers the standing identity.

    Returns:
        ``True`` when the app derives both of this definition's bounds.
    """
    if is_standing_loan_payment(template, pass_ctx):
        return True
    account_id = getattr(template, "to_account_id", None)
    if account_id is None or not template.recurs or template.is_active:
        return False
    if loan_loaders.load_loan_params(account_id) is None:
        return False
    return would_be_standing_payment(
        account_id, template.user_id, template_id=template.id,
    )


def is_loan_payment_or_standing(template: Any, pass_ctx: BalanceContext) -> bool:
    """Return whether *template* is a loan payment by EITHER identity.

    **The UNION two refusals cover, spelled once** (plan step R7d-f-5, ruling
    **R-R79**): a definition carrying loan-payment SETTINGS
    (:func:`is_loan_payment`), OR the one whose bounds are the loan's
    (:func:`bounds_are_the_loans`) -- the STANDING payment of the loan it pays
    into, its oldest active recurring transfer read off the pass's loan
    resolution (:func:`~app.services.balance_at.is_standing_loan_payment`),
    or since plan step R7d-g-2 an ARCHIVED transfer that becomes it on
    unarchive (ruling **R-R86**).
    Each half is the right question for one half of the harm the two refusals
    name.  ``is_loan_payment`` is the settings row's: the standing
    ``extra_principal`` it carries, and the amount model pricing the
    definition off the loan it pays into (a DERIVE-mode payment is P&I plus
    escrow, which no other destination can answer).  ``is_standing_loan_payment``
    is the loan's: a rule's existence is how ``recurring_transfer_query`` FINDS
    a loan's payment, so clearing the rule or re-pointing its owner leaves the
    loan amortizing with nothing projecting a payment against it.

    **Measured on a production clone 2026-08-14: neither live loan payment
    satisfies the first predicate** (transfer templates 2 "Mortgage" and 9
    "Van Payment" carry no settings row), so a refusal asking it alone left
    both of the developer's real loans clearable -- the developer ruling of
    that day made :data:`LOAN_PAYMENT_CANNOT_BE_ONE_TIME` ask the union, and
    ruling **R-R76** (plan step R7d-f-4) made the destination refusal ask the
    same one.  Three sites read it: that one-time refusal
    (:func:`refuse_recurrence_update`), the destination refusal
    (:func:`~app.routes._loan_destination.settle_destination_for_update`) and
    the edit form's lock affordance
    (:func:`~app.routes._loan_destination.loan_destination_locks_for_edit`),
    whose lists are EMPTY for a definition this names because every move of
    it is refused rather than derived.  Two inline spellings agreed until this
    function replaced them; a third would have been the place they parted.

    Cheapest half first: the settings row is a lazy one-to-one -- at most one
    primary-key query, and none once the identity map holds it -- where the
    standing identity resolves the loan (memoised on the pass, so a second
    read on the same pass is free).

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate``.  A
            transaction template answers ``False`` on both halves, and so
            does a ``PaycheckLine`` (plan step salary:R15-c), which has
            neither a settings row nor a destination.
        pass_ctx: The read pass the route built, whose loan-resolution memo
            answers the standing identity.

    Returns:
        ``True`` when either identity holds.
    """
    return is_loan_payment(template) or bounds_are_the_loans(template, pass_ctx)


def refuse_recurrence_update(
    template: Any,
    data: dict[str, Any],
    *,
    ctx: RecurrenceFormContext,
    pass_ctx: BalanceContext,
    recurrence_submitted: bool,
) -> Response | None:
    """Return why this update's recurrence may not be applied, or ``None``.

    Every refusal :func:`resolve_recurrence_rule_for_update` makes, asked
    together and BEFORE any of its three branches writes anything -- so the
    dispatcher holds the dispatch and this holds the rules.  Split out at plan
    step R7c-b, when the inverted-window door made a fourth and pushed the
    dispatcher past pylint's ``too-many-return-statements``; decomposing is
    this project's answer to that count, never a disable, and it is the same
    move ``transfers.templates._settle_create_references`` records one module
    over.

    The four, in the one order they can be asked in -- each later one assumes
    the earlier ones passed:

    1. a LOAN PAYMENT may not be made one-time;
    2. a definition whose validity window the app DERIVES may not have one
       stated for it;
    3. a rule the form could not DISPLAY may not be cleared by the empty
       submission that state produces;
    4. no edit may leave the rule stopping before it starts.

    Args:
        template: The template being updated.  Read only; not mutated.
        data: The validated payload.  Read for PRESENCE before the delegated
            helpers pop the recurrence keys; not mutated.
        ctx: The form context: the submitted closing bound (``None`` when the
            form stated none) and where a refusal sends the user.
        pass_ctx: The read pass.  Read for ONE fact, whether *template*'s
            bounds are the loan's (:func:`bounds_are_the_loans` -- the
            standing payment of the loan it pays into, or an archived
            transfer that becomes it on unarchive),
            which two of the four rules turn on -- and read only when one of
            them can fire, because answering it resolves the loan.  Built by
            the route BEFORE any write, as
            the 2026-08-16 ruling has it (a producer below the route takes the
            pass and never builds one); regeneration afterwards builds its own,
            as a writer must.
        recurrence_submitted: Whether ``recurrence_unit`` is present at all,
            read by the caller before this runs because the two of them share
            it and a second ``in`` test is a second statement of "did the form
            mention the recurrence".

    Returns:
        * ``None`` -- the update may proceed.
        * :class:`Response` -- the refusal redirect, returned verbatim.
    """
    clearing = (
        recurrence_submitted
        and data.get("recurrence_unit") is None
        and template.recurs
    )
    # The opening bound's row is the date AND how it is read: a "repeating
    # on" choice states the start as surely as the date does (plan step
    # recurrence:R8-c), and the loan's standing payment reads its date by the
    # loan's contract day (ruling R-R96), so a crafted reading is refused here
    # with a crafted date rather than ridden past on the date's absence.
    states_a_bound = (
        ctx.end_bound is not None
        or RECURRENCE_STARTS_ON_KEY in data
        or RECURRENCE_REPEATS_ON_KEY in data
    )
    # The standing identity -- is this the standing payment of the loan it
    # pays into -- is asked ONLY when a submission clears the recurrence or
    # states a bound: it is read off the pass's loan resolution, and on this
    # pre-write pass nothing else has resolved the loan yet, so asking it on
    # every edit would resolve the loan for one boolean on an amount-only
    # PATCH (an adversarial review of plan step R7d-f measured the first cut
    # doing exactly that).  Each rule below asks only when it can fire; where
    # both can, the pass's memo makes the second read free.  The
    # inverted-window door asks no identity at all: since plan step R7d-g a
    # stored bound is the owner's word for every definition.
    #
    # A loan payment may not be made one-time, and WHICH definitions that
    # covers is the UNION of two questions rather than either alone (developer
    # ruling 2026-08-14, taken on the measurement ``is_loan_payment_or_standing``
    # records): the plan step R7b-3 finding that first named the settings-row
    # predicate read it as too BROAD; it is too NARROW where it matters, and
    # the union is what makes the refusal cover the set the harm is measured
    # on without giving up the set it was written for.
    if clearing and is_loan_payment_or_standing(template, pass_ctx):
        flash(LOAN_PAYMENT_CANNOT_BE_ONE_TIME, "danger")
        return ctx.redirect.to_response()
    # The loan's standing payment's validity bounds are the app's -- the
    # opening one WRITTEN from the loan's first contractual installment, the
    # closing one DERIVED from its payoff with no authored stop (ruling
    # **R-R59**) -- so a submission stating EITHER is refused rather
    # than accepted and then discarded by the next payoff-affecting edit.  The
    # form renders both controls disabled, which is why this is reachable only
    # by a crafted POST -- and why it is checked anyway: disabling is the
    # affordance, the refusal is the rule.  See LOAN_PAYMENT_BOUND_IS_DERIVED.
    #
    # ONE guard over both bounds because ONE identity decides both (plan step
    # R7d-f split what each bound's rule MEANS without splitting the set it
    # applies to), and reading it off the pass is what keeps this refusal, the
    # form's two locks and the composed door on one producer.  Since plan
    # step R7d-g-2 that identity has an ARCHIVED reading (ruling **R-R86**,
    # ``bounds_are_the_loans``): a transfer that becomes the standing payment
    # on unarchive renders the same two locked rows, so a stated bound here
    # is the same crafted POST.
    #
    # **ABSENCE is the signal, for BOTH halves** (developer ruling
    # 2026-08-15).  Each locked control renders ``disabled`` and a disabled
    # control posts nothing, so a form that may not state a bound states
    # nothing about it -- one meaning per wire state, and the same one the
    # partial-update contract already gives an omitted key.
    #
    # Plan step R7c-b briefly made the opening control ``readonly`` instead,
    # because the schema requires a first occurrence beside any chosen cadence
    # and disabling it would have made a locked form unsubmittable.  A readonly
    # input DOES post, which broke this refusal both ways: renaming a loan
    # payment answered LOAN_PAYMENT_BOUND_IS_DERIVED and saved nothing, and
    # weakening the test to "refuse a DIFFERENT date" would have made
    # "not mine to state" and "I chose exactly the derived value" the same
    # bytes -- so a loan re-amortised between render and save would refuse an
    # innocent rename on a stale echo.  The schema rule moved to CREATE instead
    # (``RecurrenceFormFieldsMixin.validate_recurrence_states_a_start``), which
    # is where the money it guards actually is.
    if states_a_bound and bounds_are_the_loans(template, pass_ctx):
        flash(LOAN_PAYMENT_BOUND_IS_DERIVED, "danger")
        return ctx.redirect.to_response()
    # **The SAME question ``edit_form_cadence`` renders unset on** (plan step
    # R7c-b).  ``UNREPAIRED_CADENCE_CANNOT_BE_CLEARED`` is the server half of a
    # promise the form makes, so the two must be one predicate: a rule the form
    # could not display renders the unit ``<select>`` on its first entry, and
    # that entry is the empty "Does not repeat" whose save DELETES the rule.
    # This asked only about ``pattern_id`` while the form derived the unit and
    # the placement from it too, so any widening of one without the other would
    # have let an unchanged save destroy exactly the rule the repair path
    # exists to fix.  ``stored_cadence`` is that one predicate.
    if clearing and stored_cadence(template.recurrence_rule) is None:
        flash(UNREPAIRED_CADENCE_CANNOT_BE_CLEARED, "danger")
        return ctx.redirect.to_response()

    inverted = refuse_inverted_window(template, data, ctx=ctx)
    if inverted is not None:
        return inverted
    return None


@dataclass(frozen=True)
class StrandingCheck:
    """What the stranded-row refusal reads, gathered BEFORE an edit is applied.

    :func:`refuse_stranding_save` grades the state an edit would LEAVE, and
    for an ARCHIVED definition it also needs a fact about the state the edit
    REPLACES: the rows its unarchive would restore (rulings **R-PC93**,
    **R-PC95**), which read after the edit would already leave out every row
    the edit moves below the books.  One value, built by
    :meth:`before_the_edit` where each edit door captures its before-image,
    so no door can ask the refusal without having asked first.  Everything
    else the refusal reads is the state AFTER the edit, off a pass of its
    own (the round-8 review's L1).

    Attributes:
        restorable: The rows the definition's unarchive would restore as it
            stood
            (:func:`app.services.planned_rows_books.restorable_before_the_edit`);
            ``None`` for an active definition.
    """

    restorable: UnarchiveScope | None

    @classmethod
    def before_the_edit(
        cls, template: Any, pass_ctx: BalanceContext,
    ) -> "StrandingCheck":
        """Return the check for *template*, asked before any field of its edit lands.

        Args:
            template: The owner-checked definition, unedited.
            pass_ctx: The door's pre-write read pass.

        Returns:
            The :class:`StrandingCheck`.
        """
        return cls(
            planned_rows_books.restorable_before_the_edit(template, pass_ctx),
        )


def refuse_stranding_save(
    template: Any, check: StrandingCheck, redirect: RedirectTarget, *,
    kind: Any, effective_from: date,
) -> Response | None:
    """Refuse an edit whose SAVED state strands a still-projected row below the books.

    Rulings **R-PC90** / **R-PC91** (developer, 2026-09-22; plan step
    ``pay_calendar:C18-a``): a recurring definition's edit is refused when
    the state it would save leaves a still-projected row of that definition
    answering an occurrence its books drop, or sitting inside the books of
    the account it sits on (ruling **R-PC99**), WHATEVER field changed -- an
    account moved onto books that open later, the envelope box unticked, a
    funding switch -- because a maintain pass reaching a dropped row
    retires it (the save's own regeneration, for a paycheck ending on or
    after the edit's effective date; a later pass for an older one), and a
    row inside the books is counted twice.  The state the save leaves is
    read with that regeneration applied: a row it rewrites is asked where
    the rewrite moves it (*kind*'s ``preview_fn``, the round-7 review's M1).
    An ARCHIVED definition's hidden rows count, since its unarchive brings
    them back (ruling **R-PC93**) -- those it would bring back as the
    definition stood before the edit (:class:`StrandingCheck`, ruling
    **R-PC95**).  The predicate is :func:`app.services.planned_rows_books
    .definition_edit_refusal`'s; this is the door half both edit doors share
    (``routes/templates/crud.update_template``, and
    ``routes/transfers/templates._regenerate_and_commit_template``).

    **Asked once the edit is whole and before regeneration**, unlike the four
    rules above, because it reads what the save would LEAVE.  So the session
    holds the edit, and a refusal ROLLS IT BACK before it flashes.  **It
    reads a pass built HERE, after every write the door makes before
    regenerating** (the round-8 review's L1), exactly as the regeneration
    builds its own: the pre-write pass's loan caches are keyed by account
    alone and go stale once the edit or the standing payment's sync writes,
    so a preview read off it could decide over a state the save does not
    leave.  For a definition with a rule -- the only one this refusal walks
    -- nothing is written between this pass and the regeneration's.

    Args:
        template: The edited definition -- rule, amount and fields applied,
            not committed.
        check: The door's :class:`StrandingCheck`, built before the edit.
        redirect: The edit form to send the owner back to.
        kind: The door's
            :class:`~app.routes._recurrence_conflict_chooser.RecurrenceConflictKind`,
            whose engine regenerates the save next.
        effective_from: The edit's effective date, from which that
            regeneration maintains.

    Returns:
        The edit form with the refusal flashed and the edit rolled back, or
        ``None`` when the save strands nothing.
    """
    stranded = planned_rows_books.definition_edit_refusal(
        template, BalanceContext.build(current_user.id), check.restorable,
        planned_rows_books.SaveRegeneration(kind.preview_fn, effective_from),
    )
    if stranded is None:
        return None
    db.session.rollback()
    flash(stranded, "danger")
    return redirect.to_response()


__all__ = [
    "LOAN_PAYMENT_BOUND_IS_DERIVED",
    "LOAN_PAYMENT_CANNOT_BE_ONE_TIME",
    "UNREPAIRED_CADENCE_CANNOT_BE_CLEARED",
    "RecurrenceFormContext",
    "StrandingCheck",
    "bounds_are_the_loans",
    "is_loan_payment",
    "is_loan_payment_or_standing",
    "would_be_standing_payment",
    "refuse_inverted_window",
    "refuse_recurrence_update",
    "refuse_stranding_save",
]
