"""
Shekel Budget App -- Reading a recurring DEFINITION (plan step R7d-d)

The one door that reads a recurring definition against a read pass, composing
what its RULE means with what its DESTINATION allows.

:mod:`app.services.recurrence` answers the first half and is pure: given a rule
and the owner's pay calendar it resolves the cadence, the first occurrence and
the bound the owner AUTHORED.  It cannot answer the second half, because
deciding whether something outside the rule stops the definition means folding
a loan's balance forward -- the ORM and the balance seam, neither of which that
package may import.  This module is where the two meet.

Why it is a door and not a helper
---------------------------------

**A definition can be stopped by something it did not author, and every
surface that asks "does this still fire" has to honour both stops.**  A
recurring transfer that pays a loan stops when the debt does.  Until plan step
R7d that fact reached the walk only as a CACHE: ten call sites WROTE the
loan's derived payoff into ``budget.recurrence_rules.end_date``, the authored
bound's own column, so one column held two facts and every reader was trusting
that some earlier write was recent enough (plan ledger row **D35**).  That is
``CLAUDE.md`` rule 14's stored-and-derived case, and the remedy is to delete a
home rather than keep two in step -- which plan step R7d-g did: the writers
are gone, the cache is NULLed (ruling **R-R80**) and
``ck_recurrence_rules_valid_window`` holds on every row.

Deleting it left five surfaces that each need the conjunction: generation,
this surface's next date, its cadence sentence, the ``/obligations`` and
``/savings`` monthly totals, and the recurrence form's preview.  **Written by
hand in five places that is five chances to drift**, and the drift would be
silent -- a forgotten narrowing admits occurrences against a debt that is gone
and nothing raises.

So the conjunction is not a step a reader performs.  It is a VALUE
(:class:`~app.services.recurrence.Closing`) carried by the resolved recurrence
this door returns, and the walk that already reads that value narrows without
gaining a parameter.  A reader that takes this door cannot reach the
un-narrowed answer by forgetting an argument.  A reader that goes ROUND the
door still can: ``resolve`` builds a value with no derived half, because the
pure package cannot fold a balance.  **Generation takes this door since plan
step R7d-c-2** (``recurrence_engine.resolve_generation_plan`` reads
:func:`read_definition` over the pass its schedule carries), which was the one
reader that MOVES MONEY, and the form's live preview takes it since plan step
R7d-f-2 (:func:`resolved_submission`, over the transient definition the
request composes); what still goes round it in ``app/`` (re-read 2026-09-13)
is two readers that ask no closing question at all -- the write
door's refusal of the unresolvable and the opening-bound comparison in
``loan_recurrence_sync._sync_loan_cadence`` -- and the balance seam's own
definition walk (``balance_at._definition_walk``, plan steps R16-b-2 and C9), which
must not take this door because the derived stop it would compose is that
fold's own output (ruling **R-R65**); it reads the pass's memoised resolution
of the rule under its authored closing alone (:mod:`app.services.recurrence._closing`
states the same census).

What it does NOT do
-------------------

It resolves; it decides no policy of its own.  ``None`` back from
:func:`~app.services.loan_recurrence_sync.loan_payment_window` means "no
derived source bounds this definition" -- a transaction template pays into no
account at all, and a transfer into a savings account has no derived stop --
and that is carried through as a :class:`~app.services.recurrence.Closing` with
no derived half rather than translated into some neutral shape.

**It applied ONE policy until plan step R7d-g, and that policy's subject is
gone.**  Ruling **R-R56** (developer, 2026-09-04) had it read a closing
bound the APP wrote as the cache it was, not as the owner's word: for the
definition :func:`~app.services.balance_at.is_standing_loan_payment` names,
the column held the ten chokepoints' cached payoff, and composed as authored
a cache EARLIER than the fresh derivation (plan ledger row **D35**'s measured
shape, ``2029-01-22`` stored against ``2029-02-22`` derived) would still have
bound.  So the door composed ``authored=NEVER_ENDS`` for that definition
through an arm the seam stated once (``authored_closing``) and three readers
shared.  The arm carried three limits, because the schema recorded who wrote
a bound nowhere: it answered "does the app write this bound" and not "did it
write the value there now", so an owner's word in that column -- written
before the create door refused one (ruling **R-R60**), on a second transfer
promoted by the archiving of the first, or on an archived transfer edited
and unarchived (plan ledger row **REC-522**) -- was read as the cache; an
ARCHIVED loan payment's cached column was read as its owner's bound in the
Archived drawer; and kept past the column's writers the arm would have read
those owners' words as the cache forever.  R7d-g deleted the writers, NULLed
the cache on the standing payment of every loan and on every archived
transfer into one (ruling **R-R80**), and deleted the arm with them:
**a stored closing bound is its owner's word, for every definition**, and it
is honoured when the definition becomes the standing payment with no
submission to refuse it at (ruling **R-R82**; the loan's own payment
carries no authored stop is a rule the submission doors enforce, rulings
**R-R60** and **R-R77**, and R7d-g-2 adds the archived edit door).

Flask-isolated (``CLAUDE.md`` Architecture): it takes a template and a read
pass and returns plain values, reads no ``request`` / ``session``, opens no
transaction and writes nothing.  **It takes the pass and never builds one** --
the 2026-08-16 ruling that a producer below the route does not call
``BalanceContext.build`` -- so the calendar a rule is resolved against and the
pass its derived stop is resolved in cannot be two values that disagree.
"""

from dataclasses import dataclass, replace

from app.services.loan_recurrence_sync import loan_payment_window
from app.services.balance_at import BalanceContext
from app.services.recurrence import (
    Closing,
    EndBound,
    RecurrenceOwner,
    RecurrenceSpec,
    ResolvedRecurrence,
    RuleReading,
)


@dataclass(frozen=True)
class UnsavedDefinition:
    """A recurring definition a form DESCRIBES and nothing stores yet.

    What the recurrence form's live preview reads through this door (plan
    step R7d-f-2, plan ledger row **REC-515**): the template being previewed
    may not exist -- the create form is showing what saving WOULD produce --
    so there is no row to hand :func:`resolved_definition`, and plan step
    R-F6 deleted the transient ROW the preview used to fabricate for exactly
    that reason.  What the destination-half of the composition actually reads
    off a definition is ONE column
    (:func:`~app.services.recurring_transfer_query.destination_account` reads
    ``to_account_id`` and nothing else), so this is that column and nothing
    else, and :func:`resolved_submission` pairs it with the
    :class:`~app.services.recurrence.RecurrenceSpec` the form states.

    **It carries no ``recurrence_rule`` and no ``id`` on purpose.**  The
    authored half of an unsaved definition's closing is the submission's own
    word by construction -- the form never reads the stored column.  Nothing
    else about a definition's identity reaches the derived stop.

    **It carries the SOURCE accounts too, since plan step
    ``pay_calendar:C18-a``** (ruling **R-PC85**): the other end of the
    composition is where the definition's books open, and that is every
    account it moves money in -- a transaction form's ``account_id``, a
    transfer form's ``from_account_id`` beside its ``to_account_id`` --
    named exactly as a template names them, so the read pass reads a stored
    template and this by one rule (``balance_at._definition_books``).  Without
    them the preview would list a date saving would not generate.

    Attributes:
        to_account_id: The destination account the form names, or ``None``
            for a transaction template (which pays into no account) and for a
            transfer form that has not stated one.  **Must be the pass
            owner's**: the route resolves the submitted id through the
            ownership gate before building this (the house rule -- 404 for
            missing and for foreign alike), and the pass refuses a foreign
            account a second time when it memoises the loan
            (``ForeignAccountError`` from ``_memoize_once``, plan step X-i4).
        account_id: The account a transaction form names, or ``None`` (a
            transfer form, or a transaction form with none chosen yet).  The
            owner's, by the same gate.
        from_account_id: The account a transfer form draws from, or ``None``.
            The owner's, by the same gate.
        is_envelope: Whether a transaction form's envelope box is ticked
            (ruling **R-PC89**): an envelope's row is compared with the books
            on its paycheck's LAST day, a bill's on its due day, so the box
            decides which dates saving would generate.  Named as the
            template names it, for the reason the accounts are.  ``False``
            on the transfer form, which has no box.
    """

    to_account_id: int | None
    account_id: int | None = None
    from_account_id: int | None = None
    is_envelope: bool = False


def resolved_rule_of(
    template: RecurrenceOwner, ctx: BalanceContext,
) -> ResolvedRecurrence | None:
    """Return what *template*'s RULE means on *ctx*, its books attached, or ``None``.

    The pass's memoised resolution of the rule
    (:meth:`~app.services.balance_at.BalanceContext.resolved_recurrence_of`)
    under its AUTHORED closing alone -- the half :func:`resolved_definition`
    narrows by the destination, and the whole of what the edit door's
    stranded-row refusal walks (``planned_rows_books.definition_edit_refusal``,
    plan step ``pay_calendar:C18-a``), which asks only what the books drop
    and needs no loan's stop to ask it.  One function for the two, because
    the None-guarded read was spelled in both and pylint's ``duplicate-code``
    measured it.

    ``getattr`` rather than attribute access, and NOT
    ``obligations_aggregator.template_rule``: that module reads THIS door
    since plan step R7d-e (its expired filter judges the composed closing),
    so importing it here would be a cycle one step out -- the same "move the
    leaf" problem plan step R7d-d solved one layer down, recreated one layer
    up.  The read is one ``getattr`` and the duck-typed contract is the
    recurrence package's own
    (:data:`~app.services.recurrence.RecurrenceOwner`).

    Args:
        template: The recurring definition; see :func:`resolved_definition`
            for the ownership contract.
        ctx: The read pass.

    Returns:
        The resolved value, or ``None`` when the definition does not repeat
        (no rule names it) or the owner has no pay periods.

    Raises:
        RecurrenceResolutionError: See :func:`resolved_definition`.
    """
    rule = getattr(template, "recurrence_rule", None)
    if rule is None:
        return None
    return ctx.resolved_recurrence_of(rule)


def resolved_definition(
    template: RecurrenceOwner, ctx: BalanceContext,
) -> ResolvedRecurrence | None:
    """Return what *template*'s recurrence MEANS, narrowed by its destination.

    The composed read: :func:`~app.services.recurrence.resolved_recurrence`
    for what the rule says, :func:`~app.services.loan_recurrence_sync.
    loan_payment_window` for what the destination allows, and the two held in
    one :class:`~app.services.recurrence.Closing` on the value returned.

    Exposed on its own beside :func:`read_definition` for the callers that
    want the cadence and not its rows -- the Recurring surface's archived
    drawer describes every archived definition and places none -- which is the
    same split :func:`~app.services.recurrence.resolved_recurrence` and
    :func:`~app.services.recurrence.read_rule` already keep one layer down.

    **The narrowing is applied in :func:`_narrowed` and only there**, so
    "what stops this definition" has one implementation for a stored
    definition and for an unsaved one (:func:`resolved_submission`).  The
    authored half is carried across from the value the pure resolver built
    rather than re-read off the rule: reading it twice would be a second
    spelling of the same column.  Until plan step R7d-g there was one
    exception (ruling **R-R56**): for the definition whose closing bound the
    app itself wrote, the stored bound was the chokepoints' cache of the
    derived payoff and was replaced by ``NEVER_ENDS``.  Nothing writes that
    cache now and the migration NULLed it, so the column is the owner's word
    for every definition and the exception is gone.

    Args:
        template: The recurring definition -- a ``TransactionTemplate`` or a
            ``TransferTemplate`` (:data:`~app.services.recurrence.
            RecurrenceOwner`), or any object exposing ``recurrence_rule`` and
            ``to_account_id``, which is what the test fixtures build.
            **Must belong to ``ctx.user_id``**: the caller owns the ownership
            check, as every seam entry this reaches states.  A cross-owner
            pairing is refused one call down by
            :func:`~app.services.recurrence.resolve`, which will not resolve a
            rule against another owner's calendar, and that refusal is reached
            FIRST here: the read pass refuses a foreign loan too
            (``ForeignAccountError`` from ``balance_at._memoize._memoize_once``,
            plan step X-i4), but only after loading the account, so resolving
            first is the cheaper refusal and the one whose exception names the
            rule.
        ctx: The read pass.  Its ``calendar()`` is the schedule the cadence
            resolves against and its ``as_of`` and scenario scope the fold, so
            both halves are measured in one pass.

    Returns:
        The :class:`~app.services.recurrence.ResolvedRecurrence`, or ``None``
        when the definition does not repeat (no rule names it) or the owner's
        schedule holds no pay periods -- the two ``None`` cases
        :func:`~app.services.recurrence.resolved_recurrence` already
        distinguishes for its callers, passed through unchanged.

    Raises:
        RecurrenceResolutionError: The rule cannot be resolved against the
            owner's schedule -- an unmodelled cadence, a domain violation, or
            a rule paired with another owner's pass.
        BaselineMissingError: The definition pays into a configured loan and
            *ctx* has no baseline scenario (ruling **R-R30**), from the seam's
            own ``require_scenario``.  A definition with no loan behind it
            still resolves for such an owner: the not-a-loan answer is reached
            before the scenario guard.
    """
    # The pass's memo, not a fresh resolution: the forward plan behind the
    # derived stop below walks this same rule to sum the definition's
    # occurrences (plan step R16-b-2), and one pass resolves one rule once.
    resolved = resolved_rule_of(template, ctx)
    if resolved is None:
        return None
    # The occurrence walk is deliberately NOT run first.  ``resolved_recurrence``
    # refuses a rule paired with another owner's calendar, so resolving before
    # the destination is asked about makes a cross-owner pairing raise the
    # rule's own refusal before any account is loaded (the pass would refuse
    # the foreign loan too, one load later).  The resolved value is then HANDED to
    # the resolver, whose EMPTY test needs the definition's first occurrence:
    # this is the one resolution of the rule on the pass (``CLAUDE.md`` rule
    # 14), where a first build had the resolver derive it again on its own.
    # The authored half is the rule's own two columns, as the pure resolver
    # read them: since plan step R7d-g every stored closing bound is its
    # owner's word, so there is no cache arm (ruling **R-R56**) between the
    # column and the composition any more.  Both entries of this door now
    # hand ``_narrowed`` the same thing.
    return _narrowed(
        template, resolved, authored=resolved.closing.authored, ctx=ctx,
    )


def resolved_submission(
    spec: RecurrenceSpec, definition: UnsavedDefinition, ctx: BalanceContext,
) -> ResolvedRecurrence | None:
    """Return what an UNSAVED definition would mean, narrowed by its destination.

    :func:`resolved_definition` for the recurrence form's live preview (plan
    step R7d-f-2, plan ledger row **REC-515**), which has a
    :class:`~app.services.recurrence.RecurrenceSpec` the request states and
    a destination the form names, and no row.  The preview walked the spec's
    own resolution until then, so a transfer into a loan previewed
    occurrences running past the loan's payoff whenever fewer than five
    remained -- on the one surface whose contract is "what saving would
    produce", and while every other reader of a loan payment's schedule had
    taken this door (**R-R34**'s census of the preview as a reader of the
    stored column was inexact: it read neither the column nor the derived
    stop).

    **The same narrowing, applied by the same code.**  Both entries hand
    :func:`_narrowed` a resolved value and what stops it, and since plan step
    R7d-g the authored half is read the same way on both: a stored
    definition's is its two columns, which hold only its owner's word; a
    submission's IS the owner's word -- the form's "Ends" controls, or
    :data:`~app.services.recurrence.NEVER_ENDS` when the row is locked and
    posts nothing, which is what the loan's standing payment posts -- so it
    is taken as stated.  (Until R7d-g a stored definition's went through
    ruling **R-R56**'s arm, because its column could hold the chokepoints'
    cache.)  **Resolved through the pass's**
    :meth:`~app.services.balance_at.BalanceContext.resolved_for` **since plan
    step ``pay_calendar:C18-a``**, the one composition that attaches where
    the definition's books open (ruling **R-PC85**), so the preview and the
    save it previews bound by one floor.  It called ``resolved_spec`` directly
    until then -- one producer either way, the memo adding nothing but a key;
    the floor is what the memo's method adds now.

    Args:
        spec: What the form states, unresolved.
        definition: The destination the form names, as an
            :class:`UnsavedDefinition`.  See its ownership contract.
        ctx: The read pass the ROUTE built (the 2026-08-16 ruling: a producer
            below the route never builds one).  Its calendar is what *spec*
            resolves against; its scenario and ``as_of`` scope the loan fold
            behind the derived stop.

    Returns:
        The :class:`~app.services.recurrence.ResolvedRecurrence` with both
        halves of its closing, or ``None`` when the owner's schedule holds no
        pay periods -- the one refusal
        :func:`~app.services.recurrence.resolved_spec` answers rather than
        raises, passed through so the preview can say so in its own words.

    Raises:
        RecurrenceResolutionError: *spec* cannot be resolved against the
            owner's schedule -- a non-positive interval, a day the date leaves
            no room for, a first occurrence outside the authored window, or a
            spec stating another owner's ``user_id``.
        BaselineMissingError: The destination is a configured loan and *ctx*
            has no baseline scenario (ruling **R-R30**); see
            :func:`resolved_definition`.
    """
    resolved = ctx.resolved_for(spec, definition)
    if resolved is None:
        return None
    return _narrowed(
        definition, resolved, authored=resolved.closing.authored, ctx=ctx,
    )


def _narrowed(
    definition: RecurrenceOwner | UnsavedDefinition,
    resolved: ResolvedRecurrence, *,
    authored: EndBound, ctx: BalanceContext,
) -> ResolvedRecurrence:
    """Return *resolved* with its closing composed from *authored* and the destination.

    **The narrowing is applied HERE and only here** -- the sentence
    :func:`resolved_definition` carried alone until plan step R7d-f-2 gave
    the door a second entry.  The derived half is
    :func:`~app.services.loan_recurrence_sync.loan_payment_window`'s answer
    about *definition*'s destination; the authored half is whatever the
    caller established it to be (see :func:`resolved_submission` for the one
    way the two entries differ).  ``replace`` rather than a mutation because
    :class:`~app.services.recurrence.ResolvedRecurrence` is frozen.

    Args:
        definition: What is being read -- a stored template or an
            :class:`UnsavedDefinition`; only its ``to_account_id`` is read,
            through :func:`~app.services.recurring_transfer_query
            .destination_account`.
        resolved: The rule's or spec's resolution, whose authored closing the
            caller has already read and may have replaced.
        authored: The closing bound the OWNER stated, as the caller reads it.
        ctx: The read pass.

    Returns:
        *resolved* with ``closing`` holding both halves.
    """
    return replace(
        resolved,
        closing=Closing(
            authored=authored,
            derived=loan_payment_window(definition, resolved, ctx),
        ),
    )


def read_definition(
    template: RecurrenceOwner, ctx: BalanceContext,
) -> RuleReading:
    """Read *template* against *ctx*, keeping the meaning and the placements.

    :func:`resolved_definition` plus the occurrence walk, which is the
    composition :func:`~app.services.recurrence.read_rule` makes one layer
    down -- and this is that function with the destination's own stop applied,
    so a caller takes this rather than performing the two steps itself.

    **The placements are walked under the composed closing**, because the walk
    reads it off the resolved value.  So a surface's "next date" and its
    cadence sentence are two readings of ONE narrowing and cannot come apart.
    They do not disagree today either -- ``recurring_view._build_section``
    already reads each rule once and derives both from that reading, and an
    adversarial review of this step corrected an earlier sentence here for
    claiming otherwise.  What this preserves is that property through a
    second stop being added, rather than repairing a disagreement that
    existed.

    **And they are walked ONCE per pass, since plan step R7d-f-2** (plan
    ledger row **N-513**): the walk is the pass's memo
    (:meth:`~app.services.balance_at.BalanceContext.placements_of`, keyed by
    the composed value), as the resolution has been since R16-b-2.  A
    ``/savings`` render reads a checking-to-goal transfer through this door
    in two sets and used to pay the walk twice; now the second read is memo
    hits end to end -- the resolution, the destination's loan state, the
    identity behind ruling R-R56, and the walk.  The reading also carries the
    horizon the walk reached (plan ledger row **N-514**), read off the same
    calendar the memo walked against, and the occurrences the definition's
    books drop, which its closing counts and no row answers (plan step
    ``pay_calendar:C18-a``, ruling **R-PC94**).

    Args:
        template: The recurring definition.  See :func:`resolved_definition`
            for the ownership contract.
        ctx: The read pass.

    Returns:
        The :class:`~app.services.recurrence.RuleReading`.  Its ``resolved`` is
        ``None`` with no placements for a definition that does not repeat or
        an owner with no pay periods, which is what
        :func:`~app.services.recurrence.read_rule` answers for the same two
        states.

    Raises:
        RecurrenceResolutionError: See :func:`resolved_definition`.
        RecurrenceGenerationError: The resolved value names something the
            occurrence engine cannot walk.  See
            :func:`~app.services.recurrence.rule_occurrences`.
        BaselineMissingError: See :func:`resolved_definition`.
    """
    resolved = resolved_definition(template, ctx)
    if resolved is None:
        return RuleReading(
            resolved=None, placements=(), horizon=ctx.calendar().horizon(),
        )
    walk = ctx.placements_of(resolved)
    return RuleReading(
        resolved=resolved,
        placements=walk.kept,
        horizon=ctx.calendar().horizon(),
        below_the_books=walk.below_the_books,
    )


__all__ = [
    "UnsavedDefinition",
    "read_definition",
    "resolved_definition",
    "resolved_rule_of",
    "resolved_submission",
]
