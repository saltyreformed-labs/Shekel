"""
Shekel Budget App -- What an EDIT form's recurrence controls START ON

The render-side half of :mod:`app.routes._recurrence_form_helpers`, split out
at plan step R7b-4 when that module reached the 1,000-line cap for the second
time -- the same trigger that produced
:mod:`app.routes._recurrence_conflict_chooser` at plan step R2e-1, and the same
kind of seam: everything here answers a read-only question ABOUT a stored
definition, everything there acts on a SUBMITTED payload.

Two entry points, one per kind of form.  :func:`create_form_recurrence_state`
is the state a form for a definition that does not exist yet opens on, and
:func:`edit_form_recurrence_state` composes, for a stored definition, the
three questions the two template forms render a control for:

* :func:`edit_form_cadence` -- which ``(unit, interval, placement)`` triple the
  three cadence controls preselect, or ``None`` (with a flashed explanation)
  when the stored pattern is one this application no longer models;
* :func:`edit_form_starts_on` -- what the "Starts on" row holds: the date, the
  day it means on the one date shape that leaves it open, and whether the row
  is LOCKED because the app writes that bound;
* :func:`edit_form_end` -- what the "Ends" row holds: which of the control's
  three shapes is selected and its value when the bound is the owner's, and
  the described stop the row DISPLAYS when it is locked because the app
  derives it.

**Each bound's row carries its own lock, since plan step R7d-f.**  Both rows
lock on ONE identity -- the definition's bounds are the loan's
(:func:`~app.routes._recurrence_form_refusals.bounds_are_the_loans`): it is
the standing payment of the loan it pays into
(:func:`~app.services.balance_at.is_standing_loan_payment`), or since plan
step R7d-g-2 an archived transfer that becomes it on unarchive (ruling
**R-R86**)
-- but what the lock MEANS differs per bound, and a single ``bounds_are_derived``
flag said "the app writes both", a premise ruling **R-R29** made false.  The
opening bound IS written: ``starts_on`` is the loan's first contractual
installment and the sync re-writes it.  The closing bound is DERIVED: the
loan's payoff is resolved through the composed door on every read, the column
that used to be shown here is the chokepoints' cache of it (plan ledger row
**D35** measures that cache stale), and the loan's own payment has no authored
stop -- archiving is the door to stop it early (ruling **R-R59**, developer
2026-09-05).  So the locked "Ends" row renders the door's answer, worded by
the same describer the Recurring row uses (ruling **R-R58**), and never the
column.

**The form takes the READ PASS** (:class:`~app.services.balance_at.BalanceContext`)
for that reason: the derived stop is a fold over the loan in the owner's
baseline scenario, and the identity is read off the pass's memoised loan
resolution rather than re-queried (plan ledger row **N-511**).  A route builds
the pass and hands it down (the 2026-08-16 ruling).  An owner with no baseline
scenario is REFUSED the loan payment's form through the seam's own guard --
``BaselineMissingError`` to the single application-level handler, ruling
**R-R30** -- exactly as every other producer needing a scenario refuses; a
transaction template or a savings transfer, which no loan bounds, still
renders for such an owner because the not-a-loan answer is reached first.

**Functions rather than Jinja attribute walks, and that is the point of the
module.**  Each of these decides something from more than one stored fact --
which shape a bound is in, whether a pattern is modelled, whether a template is
the loan's standing payment -- and deciding any of them in a template would put
a second statement of the rule in a place no gate reads.  Both forms take the
composed :class:`RecurrenceFormState` rather than five context keys, so they
cannot come to disagree about what the same rule means -- and ``duplicate-code``
said so the moment plan step R7b-4 made the two render blocks identical.

Route-layer rather than service because :func:`edit_form_cadence` flashes;
``CLAUDE.md::Architecture`` keeps services isolated from Flask globals.  The
leading underscore marks the module as route-internal.
"""
from dataclasses import dataclass
from datetime import date
from typing import Any

from flask import flash

from app.routes._recurrence_form_refusals import bounds_are_the_loans
from app.schemas.validation import EFFECTIVE_DATE_MAX, EFFECTIVE_DATE_MIN
from app.services.balance_at import BalanceContext
from app.services.recurrence import (
    NEVER_ENDS,
    UNREADABLE_CADENCE_MESSAGE,
    AuthorableCadence,
    EndBound,
    PickerModel,
    ResolvedRecurrence,
    SelectedCadence,
    describe,
    end_bound_from_columns,
    modelled_unit,
    offerable_nominal_days,
    picker_model,
    selected_cadence,
    stored_cadence,
)
from app.services.recurring_definition import resolved_definition
from app.utils.dates import display_today


@dataclass(frozen=True)
class RecurrenceStart:
    """What the "Starts on" row of a recurrence form starts on.

    The two fields that state WHEN a rule first fires
    (:attr:`~app.models.recurrence_rule.RecurrenceRule.starts_on` and its
    0-or-1 ``nominal_day``), held as one value because the form renders them as
    one row and the table refuses them apart
    (``ck_recurrence_rules_nominal_day``).  Two context keys would let a route
    render a nominal day beside a date that never clamped it -- which the write
    door refuses, so it would be a 500 out of an ordinary edit.

    Attributes:
        starts_on: The date the definition first happens -- or ``None`` for a
            box that opens BLANK, which only the payroll-line form renders
            (plan step salary:R18-c): there blank means the owner's opening
            payday and the route derives it, so the template forms' default
            of today would be read as a stated start.
        nominal_day: The day it MEANS, when that date's own month was too short
            to hold it; ``None`` for every unambiguous date.
        day_choices: Which days *starts_on* leaves OPEN, from
            :func:`~app.services.recurrence.offerable_nominal_days` -- empty
            for every date that says its own day, which is all but a handful.
            It decides whether the "repeating on" control renders at all and
            which of its options are enabled, so the form is correct before any
            script runs and cannot offer a pair the write door refuses.
        locked: Whether the row renders READ-ONLY, because the app WRITES this
            bound: the definition is the standing payment of the loan it pays
            into, and ``starts_on`` is that loan's first contractual
            installment, re-written by ``loan_recurrence_sync`` on every
            payoff-affecting edit (plan step R7b-4; carried on the row itself
            since R7d-f).  A locked control is ``disabled`` and posts nothing,
            and the server refuses a crafted submission that states one anyway
            (:data:`~app.routes._recurrence_form_refusals.LOAN_PAYMENT_BOUND_IS_DERIVED`).
    """

    starts_on: date | None
    nominal_day: int | None
    day_choices: tuple[int, ...] = ()
    locked: bool = False


@dataclass(frozen=True)
class RecurrenceEnd:
    """What the "Ends" row of a recurrence form starts on.

    One value with two readings, because the row has two states and a form
    that carried a bound beside a lock flag had to know which of them the
    other made meaningless (plan step R7d-f).

    Attributes:
        selected: The bound the OWNER stated, as the stored columns hold it
            (:func:`~app.services.recurrence.end_bound_from_columns`), or the
            unbounded shape for a definition with no rule and for a create
            form.  What the OPEN control preselects: its shape picks the mode
            ``<option>``, its value fills the matching input.  ``None`` on a
            LOCKED row, and not merely unrendered: a locked row displays the
            composed door's phrase and offers no control, so the stored
            bound has no input to fill -- until plan step R7d-g that column
            was the chokepoints' cache of the payoff (ruling **R-R56**), and
            the first cut of R7d-f carried the cache here and leaked it into
            a hidden input.  A value the row does not hold cannot reach the
            page by any template's mistake, which is the invariant
            :meth:`__post_init__` refuses to let the two fields violate.
        locked: Whether the row renders READ-ONLY, because the app DERIVES
            this bound: the definition is the standing payment of the loan it
            pays into, and it stops when the loan does.  The loan's own payment
            takes no authored stop -- archiving is the door to stop it early
            (ruling **R-R59**) -- so the control is ``disabled``, posts
            nothing, and a crafted submission stating one is refused.
        stop_phrase: What a LOCKED row DISPLAYS (ruling **R-R58**): the
            definition's whole stop
            as :func:`~app.services.recurrence.describe` words it -- ``"until
            Feb 22, 2029"`` for a loan that pays off, ``"never runs"`` for one
            that closed before the payment's first installment, and the
            control's own "Never" label for one that never pays off at this
            payment -- the same phrase the Recurring row shows, from the same
            resolved value, so the form and the surface cannot name two stops.
            ``None`` on every open row, and on a locked row for which the
            composed door resolved nothing: an owner with no pay periods (a
            broken invariant -- registration bootstraps one) or a stored
            cadence the application no longer models (the repair path, where
            the cadence controls render unset above the warning).  The row
            then shows an empty box under its help text rather than a value
            nothing derived.
        owner_stop: Whether a LOCKED row's phrase names a stop the OWNER
            authored rather than the loan's payoff (plan step R7d-g, ruling
            **R-R82**): a definition that became the loan's standing payment
            with no submission -- promoted by the archiving of an older
            payment, unarchived, or already paying into an account that was
            then set up as a loan -- keeps the stop its owner typed, the door
            composes it, and the locked row displays it.  The help text
            beside the row says which of the two it is showing: the payoff
            moves when the loan does, an owner's stop does not, and the only
            door to an owner's stop on a locked row is the archive.  Always
            ``False`` on an open row, which :meth:`__post_init__` enforces.
    """

    selected: EndBound | None
    locked: bool = False
    stop_phrase: str | None = None
    owner_stop: bool = False

    def __post_init__(self) -> None:
        """Refuse a row whose two halves disagree.

        A locked row carries no owner's bound and an open row always carries
        one, so ``selected is None`` and ``locked`` are one fact stated twice
        and must agree.  A check rather than a docstring guarantee, for the
        reason :class:`~app.services.recurrence.RuleReading` records in its
        own: this project has been burned by an invariant the generated
        ``__init__`` did not enforce.

        Raises:
            ValueError: When a locked row carries a bound or an open row
                carries none.
        """
        if (self.selected is None) != self.locked:
            raise ValueError(
                f"an Ends row is locked={self.locked!r} with "
                f"selected={self.selected!r}: a locked row carries no owner's "
                f"bound and an open row always carries one, so the pair "
                f"disagrees with itself."
            )
        if self.owner_stop and not self.locked:
            raise ValueError(
                "an OPEN Ends row claims to display an owner's stop: the "
                "owner's stop on an open row is the control itself, and the "
                "flag exists only to word a LOCKED row's help text."
            )


@dataclass(frozen=True)
class RecurrenceFormState:
    """Everything the recurrence controls need in order to RENDER.

    One value rather than five context keys, and it is not tidiness: both
    template forms asked the same five questions in the same order, so
    ``duplicate-code`` reported the render blocks as identical the moment plan
    step R7b-4 added the fifth.  Passing one value is what makes the two forms
    unable to diverge -- which is the same defect class this step fixed on the
    lock predicate, where one caller asked a slightly broader question than the
    writer it was supposed to mirror.

    Attributes:
        picker: The offer sets every control chooses from
            (:func:`~app.services.recurrence.picker_model` over the set the
            form passes: all of them on the transfer form, and on the
            transaction form all but a card's due dates, which only a
            transfer into a card may repeat by, plan step credit_card:CC-6).
            Identical for every user of one form -- what a definition can be,
            not what this one is.
        selected_cadence: Which ``(unit, interval, placement)`` triple the
            three cadence controls preselect, or ``None`` on a create form AND
            for a rule naming a pattern this application no longer models.
        selected_start: What the "Starts on" row holds -- the date, the day it
            means, and whether the row is locked.
        selected_end: What the "Ends" row holds -- the owner's bound for an
            open row, the described stop for a locked one.
        starts_on_min: The earliest date the "Starts on" box offers, and
            starts_on_max the latest.  The SCHEMA's own window, carried here
            rather than written in the template so the browser hint and the
            refusal cannot state two different ranges.
        starts_on_max: See *starts_on_min*.
    """

    picker: PickerModel
    selected_cadence: SelectedCadence | None
    selected_start: RecurrenceStart
    selected_end: RecurrenceEnd
    starts_on_min: date = EFFECTIVE_DATE_MIN
    starts_on_max: date = EFFECTIVE_DATE_MAX


def create_form_default_starts_on() -> date:
    """Return the "Starts on" date a CREATE form opens with.

    **TODAY, on the app's own civil clock**, and it is TOTAL -- which is what
    plan step R7c-b needed of it.  ``starts_on`` is the rule's first occurrence
    and the column is ``NOT NULL``, so a create form has to open on a real date
    rather than on an empty box the user might leave empty.

    **Defaulting it at all is a money fix**, and the measurement is worth
    keeping (adversarial review of plan step R7b-4).  The control this replaced
    was a ``<select>`` of pay periods with NO empty option, so it always
    submitted and it preselected the current period; a date box defaulting to
    EMPTY is not the same request, because an empty opening bound resolved to
    the schedule's first payday and the create routes generate over every
    period the owner has, with no lower window bound.  Measured on the
    developer's schedule shape: 5 backdated rows, ``$10,000.00`` at a
    ``$2,000.00`` rent.

    **Today rather than the current paycheck's payday**, which is what the
    bound-shaped version answered.  The two are the same request for a
    pay-period cadence -- ``resolve`` normalises any date onto the paycheck
    that hosts it, and today's paycheck is the one containing today -- and for
    a MONTH or YEAR cadence today is the honest reading of a control that now
    says when the thing first HAPPENS.  Neither can backdate: today is inside
    the current pay period, so the earliest row either produces is this one.

    It also removes the ``None`` the payday version had to answer for an owner
    whose schedule does not cover today.  That state left the box empty, which
    is now a submission the schema refuses -- so a user in it could not create a
    recurring definition at all.

    Returns:
        The app's civil today (:func:`app.utils.dates.display_today`).
    """
    return display_today()


def create_form_recurrence_state(
    cadences: tuple[AuthorableCadence, ...],
) -> RecurrenceFormState:
    """Return the render state a CREATE form's recurrence controls open on.

    No cadence selected, neither bound locked, the unbounded CLOSING shape,
    and a "Starts on" row that is DEFAULTED rather than empty -- see
    :func:`create_form_default_starts_on` for why that difference is a money
    decision rather than a convenience.  Nothing is resolved here because
    there is no definition yet to resolve: a create form takes no read pass,
    and the loan-destination locks it applies as the user picks an account
    are the browser's affordance over the ONE set the transfer route emits
    (:class:`~app.routes._loan_destination.LoanDestinationLocks`: the loans
    holding no payment yet, which derive both bounds -- since plan step
    R7d-g-2, ruling **R-R81**; a loan already holding one takes a second
    transfer with its owner's bounds), with the derivation and the refusal
    the route's (``settle_first_occurrence``, rulings **R-R60** and
    2026-09-12's on what a stated stop is at create).

    Args:
        cadences: What this form offers: the set its own door refuses against
            (ruling plan#2591).

    Returns:
        The :class:`RecurrenceFormState`.
    """
    return RecurrenceFormState(
        picker=picker_model(cadences),
        selected_cadence=None,
        selected_start=RecurrenceStart(
            starts_on=create_form_default_starts_on(), nominal_day=None,
        ),
        selected_end=RecurrenceEnd(selected=NEVER_ENDS),
    )


def edit_form_recurrence_state(
    template: Any, ctx: BalanceContext, cadences: tuple[AuthorableCadence, ...],
) -> RecurrenceFormState:
    """Return the whole render state for *template*'s recurrence controls.

    The ONE call both edit routes make, so a control cannot start on one thing
    in the transaction form and another in the transfer form.

    **The identity is asked once, and the definition is resolved only for a
    LOCKED row with a readable cadence.**  Whether *template*'s bounds are
    the loan's -- it is the standing payment of the loan it pays into, or an
    archived transfer that becomes it on unarchive (ruling **R-R86**,
    :func:`~app.routes._recurrence_form_refusals.bounds_are_the_loans`) --
    decides both rows' locks; the composed
    door (:func:`~app.services.recurring_definition.resolved_definition`) is
    what a locked "Ends" row displays and nothing else on this form reads it,
    so an open row -- every transaction template, every savings transfer, a
    second transfer into a loan -- resolves nothing (an adversarial review of
    this step found the first cut resolving every readable definition, and
    refusing a baseline-less owner a second transfer's form over a value the
    page never showed).  A rule whose stored pattern the application no
    longer models is NOT resolved either, because resolving it raises and
    this page is where the user repairs it (the controls render unset above
    :data:`~app.services.recurrence.UNREADABLE_CADENCE_MESSAGE`).

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate`` being
            edited.  Owner-checked by the route; read, not mutated.
        ctx: The read pass the route built.  Its loan-resolution memo answers
            the identity, its calendar is what the cadence resolves against
            and its scenario scopes the fold behind a loan payment's derived
            stop.
        cadences: What this form offers, as at create.  It must hold what a
            definition this door accepted stores: a ``<select>`` whose selected
            value is absent from its options silently becomes "Does not
            repeat", which deletes the rule on an unchanged save.

    Returns:
        The :class:`RecurrenceFormState`.

    Raises:
        BaselineMissingError: *template*'s bounds are a configured loan's --
            its standing payment, or an archived transfer that becomes it on
            unarchive -- and the owner has no baseline scenario (ruling
            **R-R30**), raised by the seam's own guard on the way to the
            derived stop and answered by the application-level handler.  Any
            other definition renders for such an owner, a second transfer
            into a loan included: its row is open and reads no derived stop.
    """
    cadence = edit_form_cadence(template)
    locked = bounds_are_the_loans(template, ctx)
    resolved = (
        resolved_definition(template, ctx)
        if locked and cadence is not None else None
    )
    picker = picker_model(cadences)
    return RecurrenceFormState(
        picker=picker,
        selected_cadence=cadence,
        selected_start=edit_form_starts_on(template, locked=locked),
        selected_end=edit_form_end(template, resolved, picker, locked=locked),
    )


def edit_form_cadence(template: Any) -> SelectedCadence | None:
    """Return what an EDIT form's cadence controls start on, warning if unreadable.

    The render-side counterpart of the write door below, and the reason both
    edit routes call one function rather than each decoding the rule: the
    stored pattern and the set the application models can disagree, and until
    plan step R9 drops the table they DO -- the ``Once`` row survives its
    deleted enum member (ruling R-R11), and a hand-edited or migration-missed
    rule can name it.

    **Answering ``None`` is what makes the repair reachable.**  Before plan
    step R7b-2 the form met that state by keeping the stored pattern as a
    trailing ``<select>`` option, because a ``<select>`` whose selected value is
    absent from its options silently submits a DIFFERENT one -- on both forms
    the first entry, "Does not repeat", which deletes the rule and sweeps its
    future rows.  The two-axis controls carry no pattern id to preserve, so
    there is nothing to append: they render UNSET, and the warning says to
    choose a cadence.

    **Rendering unset does NOT make the empty submission safe, and an
    adversarial review of this step caught the gap.**  Unset means the unit
    ``<select>``'s first entry is the selected one, so saving the form
    unchanged posts exactly the "Does not repeat" that deletes the rule -- the
    same destruction the trailing option existed to prevent, reached a
    different way.  What keeps ``UNREADABLE_CADENCE_MESSAGE``'s promise is the
    server-side refusal in :func:`resolve_recurrence_rule_for_update` (see
    :data:`UNREPAIRED_CADENCE_CANNOT_BE_CLEARED`), which is the only layer
    holding both facts the disposition needs.

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate`` being
            edited -- or, since plan step salary:R15-c, a ``PaycheckLine``,
            whose row's edit prefill the salary page reads through this same
            function.  Read for ``recurrence_rule`` only; not mutated.

    Returns:
        The :class:`~app.services.recurrence.SelectedCadence` to preselect, or
        ``None`` when the template does not repeat OR when its rule names a
        unit or a placement this application does not model -- the last case
        having flashed the explanation.

    """
    rule = template.recurrence_rule
    if rule is None:
        return None
    # **The AUTHORED columns, not the closed set's encoding of them** (plan
    # step R7c-b).  This asked ``modelled_pattern(rule.pattern_id)`` and then
    # decoded the unit and the placement out of that pattern, which left the
    # edit form as the ONE reader the step did not move across: every other
    # reader takes them from ``unit_id`` / ``placement_id`` through
    # ``recurrence_spec``.  ``stored_cadence`` asks about both at once and
    # is the SAME predicate ``resolve_recurrence_rule_for_update`` refuses the
    # empty submission with -- which matters more than the tidiness, because
    # rendering unset and refusing the clear are two halves of one promise: a
    # rule readable by one and not the other would render "Does not repeat"
    # selected and then be deleted by an unchanged save.
    reading = stored_cadence(rule)
    if reading is None:
        flash(UNREADABLE_CADENCE_MESSAGE, "warning")
        return None
    return selected_cadence(reading)


def edit_form_starts_on(template: Any, *, locked: bool) -> RecurrenceStart:
    """Return what an EDIT form's "Starts on" controls start on.

    The OPENING half of :func:`edit_form_end`, and a function rather than a
    Jinja attribute walk for the same reason its sibling is one: both forms
    ask the question and a template that reached through
    ``template.recurrence_rule.starts_on`` would state the ``None``-guard
    twice.

    **It cannot fail on an unreadable rule**, exactly like its sibling: it
    reads two authored columns and no pattern, so a rule naming a pattern the
    application no longer models still renders its start correctly while the
    cadence controls render unset above the warning.

    **The stored date is what a LOCKED row shows too**, and that is the
    asymmetry with the "Ends" row stated rather than hidden: the opening bound
    stays STORED under ruling **R-R29** -- it is the cadence anchor a
    month-unit rule cannot fire without -- and the sync keeps it current
    where the loan's contract moves, so the column is the value (plan ledger
    row **D35** measured it behind on two live loans, phase-preservingly, and
    both read current on a production clone of 2026-09-13).

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate`` being
            edited.  Read for ``recurrence_rule`` only; not mutated.
        locked: Whether the app writes this definition's opening bound -- the
            definition is the standing payment of the loan it pays into.
            Decided once by the caller for both rows.

    Returns:
        The stored :class:`RecurrenceStart`, or the CREATE default when the
        definition has no rule -- a template that does not repeat opens its
        "Starts on" row the way a create form does, for the same reason
        :func:`create_form_default_starts_on` gives.
    """
    rule = template.recurrence_rule
    if rule is None:
        # Nothing has chosen a cadence yet, and whether a date is ambiguous is
        # a property of the ``(unit, date)`` pair rather than of the date
        # alone.  The script shows the control the moment a cadence that fires
        # on a day of the month is picked.  Never locked: a definition with no
        # rule is nobody's standing payment.
        return RecurrenceStart(
            starts_on=create_form_default_starts_on(), nominal_day=None,
        )
    # ``modelled_unit`` rather than a raising reader: this function's whole
    # contract is that it cannot fail on a rule the cadence controls could not
    # display (see the docstring), and a ``ref`` id the enum does not name is
    # the same class of state.  It answers no choices, which renders the
    # control hidden above the warning that tells the user to pick a cadence.
    unit = modelled_unit(rule.unit_id)
    return RecurrenceStart(
        starts_on=rule.starts_on,
        nominal_day=rule.nominal_day,
        day_choices=(
            () if unit is None
            else offerable_nominal_days(unit, rule.starts_on)
        ),
        locked=locked,
    )


def edit_form_end(
    template: Any,
    resolved: ResolvedRecurrence | None,
    picker: PickerModel,
    *,
    locked: bool,
) -> RecurrenceEnd:
    """Return what an EDIT form's "Ends" row starts on.

    The bound half of :func:`edit_form_cadence`, and the reason both edit
    routes call a function rather than reading the columns in Jinja: the
    control's three shapes are discriminated by which column is non-NULL
    (ruling R-R13's "absence is the discriminator", applied to the bound), and
    deciding that in a template would be a second statement of the shape set
    -- keyed on a name string, which this project rules out.

    **A LOCKED row shows the composed door's answer and never the column**
    (plan step R7d-f).  For the loan's standing payment the ``end_date`` column
    is the chokepoints' cache of the derived payoff, measurably behind on live
    data (plan ledger row **D35**: ``2029-01-22`` stored against ``2029-02-22``
    derived), and until this step the locked control displayed exactly that
    stale date under a sentence saying it came from the projected payoff.  The
    phrase is :func:`~app.services.recurrence.describe`'s, over the resolved
    value the door already narrowed -- ONE producer with the Recurring row's
    stop line, so the form cannot name a different date than the page that
    lists it.  Ruling **R-R30** decides the owner with no baseline scenario:
    the door refuses, and this function is never reached for them.

    **It cannot fail on an unreadable rule**, unlike :func:`edit_form_cadence`:
    the OPEN row reads the two bound columns and no cadence, so a rule naming a
    pattern the application no longer models still renders its stop correctly
    while the cadence controls render unset above the warning.  A form that
    lost the user's stop date because its cadence was unreadable would be the
    repair path destroying what it came to fix.  The locked row keeps that
    property by taking *resolved* from a caller that did not resolve such a
    rule: it shows no phrase rather than raising.

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate`` being
            edited.  Read for ``recurrence_rule`` only; not mutated.
        resolved: What the definition's recurrence MEANS through the composed
            door, or ``None`` when there was nothing to resolve -- an open
            row (the caller resolves nothing it will not display), no rule,
            an unreadable cadence, or an owner with no pay periods.  Read only
            for a locked row.
        picker: The form's offer sets, for the "Never" label a locked row
            shows when nothing derived stops the definition -- the same word
            the open control offers for an unbounded rule, taken from the one
            place it is spelled.
        locked: Whether the app derives this definition's closing bound -- the
            definition is the standing payment of the loan it pays into.
            Decided once by the caller for both rows.

    Returns:
        The :class:`RecurrenceEnd`.

    Raises:
        RecurrenceResolutionError: The row carries both bound columns, which
            ``ck_recurrence_rules_single_end_bound`` refuses in the table.
    """
    if locked:
        return RecurrenceEnd(
            selected=None,
            locked=True,
            stop_phrase=_locked_stop_phrase(resolved, picker),
            # The authored half the door composed, read off the same value
            # the phrase is worded from: a real stop here is the owner's
            # (ruling **R-R82**), and the help text must not call it the
            # payoff.  ``None`` resolved means nothing to display either way.
            owner_stop=(
                resolved is not None
                and resolved.closing.authored != NEVER_ENDS
            ),
        )
    rule = template.recurrence_rule
    return RecurrenceEnd(
        selected=(
            NEVER_ENDS if rule is None
            else end_bound_from_columns(rule.end_date, rule.max_occurrences)
        ),
    )


def _locked_stop_phrase(
    resolved: ResolvedRecurrence | None, picker: PickerModel,
) -> str | None:
    """Return the words a LOCKED "Ends" row displays for *resolved*.

    :func:`~app.services.recurrence.describe`'s stop phrase, which words the
    WHOLE closing -- for the loan's standing payment ordinarily the derived
    stop alone, its authored half being ``NEVER_ENDS`` (the doors refuse a
    stated one, rulings **R-R60** and **R-R77**): ``"until Feb 22, 2029"``
    for a loan that pays off, ``"never runs"`` for one that closed before the
    payment's first installment; and the EARLIER of the two where the
    standing payment carries a stop its owner authored before it became one
    (ruling **R-R82**, plan step R7d-g).  A loan that never
    pays off at this payment derives no stop and the describer answers
    ``None``, which on the Recurring row means "no second line"; a form
    control has to SAY something there, and what it says is the same "Never"
    its open twin offers, read off the picker so the word is spelled once.

    Args:
        resolved: The resolved definition, or ``None`` when the door resolved
            nothing (see :class:`RecurrenceEnd`).
        picker: The form's offer sets.

    Returns:
        The phrase, or ``None`` when nothing was resolved.
    """
    if resolved is None:
        return None
    phrase = describe(resolved).stops
    if phrase is not None:
        return phrase
    return next(
        option.label for option in picker.end_bounds
        if option.token == NEVER_ENDS.token
    )


__all__ = [
    "RecurrenceEnd",
    "RecurrenceFormState",
    "RecurrenceStart",
    "create_form_default_starts_on",
    "create_form_recurrence_state",
    "edit_form_cadence",
    "edit_form_end",
    "edit_form_recurrence_state",
    "edit_form_starts_on",
]
