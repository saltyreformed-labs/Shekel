"""
Shekel Budget App -- The recurrence write door

Every recurrence rule in the application is written here.  Before this module
there were nine places that could write one: six that constructed a rule and
three that mutated one in place (the edit form's update path,
``loan_recurrence_sync._sync_loan_cadence``, and
``pay_period_admin``'s schedule-rebuild re-pointer, itself deleted at plan
step R7b-4), each setting the columns it
happened to care about.

The shape that leaves nothing for a writer to get half-right:

* a caller states what it AUTHORS
  (:class:`~app.services.recurrence.RecurrenceSpec` -- a cadence since plan
  step R7b, never a closed-set pattern id, and never a column);
* :func:`_author` writes that whole spec, and it is the only function in the
  application that assigns a column of ``budget.recurrence_rules``;
* every value derived on write is taken from the same
  :func:`~app.services.recurrence.resolve` call that validates the spec --
  never from the payload, which is what closes defect D1.

**Since plan step R7c-c there is ONE representation and this function writes
it.**  The rule states its recurrence in ``interval_n`` / ``unit_id`` /
``placement_id`` / ``shift_id`` / ``starts_on`` / ``nominal_day`` (with
``max_per_month`` since plan step salary:R15-a and ``weekday_reading`` since
plan step recurrence:R8-c), and that is
the whole table apart from the closing bound's exclusive arc and the unit's
carried day-of-month flag (``due_day_of_month`` was the other exception until
plan step recurrence:R5-a dropped it, ruling **R-R96**).  The closed set's storage
encoding -- ``pattern_id``,
``day_of_month``, ``month_of_year``, ``start_date``, ``start_period_id``,
``offset_periods``, and ``interval_n``'s encoded value -- was derived here and
is dropped; two representations with one producer became one representation
with no producer to keep honest.

**``interval_n`` says what the cadence says from that step**, which it did not
before: ``encode_cadence`` wrote ``1`` for every pattern whose interval was
baked into its NAME, so a Quarterly rule stored ``(interval_n = 1, unit_id =
month)`` -- MONTHLY at face value, 12 occurrences a year where 4 were owed --
and the read door had to recover the ``3`` through the pattern.  R7c-c's
migration re-points the column on the four live rules that carried the
encoding.

**The stored first occurrence no longer LAGS anything, which is what changed at
this step.**  While ``starts_on`` was derived from the closed-set columns plus
the owner's schedule, a schedule rebuilt with no rule written moved the
derivation and left the column -- so R7c-b re-ran the backfill before switching
the readers.  From here the column is AUTHORED: the form collects it, the loan
sync writes it from the loan's own contract, and neither has an input left to
lag.  The one derivation that remains is the pay-period NORMALISATION, and it
runs on every write.

**A partial change is expressed as a whole one.**  The two in-place writers do
not set a field; they read the rule's authored state back with
:func:`~app.services.recurrence.recurrence_spec` -- the READ door's, in
``_reading`` -- change the one fact they own with ``dataclasses.replace``, and
re-author.  So "the loan's payment day moved" is stated as a new spec, and every
encoded column is re-derived from it in the same call rather than left holding
what a previous cadence implied.

Flask-isolated (plain values in, no ``request`` / ``session`` reads) and it
never commits: writes flush into the caller's transaction, which owns the
boundary.
"""
from app import ref_cache
from app.extensions import db
from app.models.paycheck_line import PaycheckLine
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services.pay_calendar import PayCalendar
from app.services.recurrence._frequency import (
    RecurrenceResolutionError,
    has_day_of_month_coordinate,
)
from app.services.recurrence._resolution import resolve
from app.services.recurrence._spec import RecurrenceSpec

class EmptyAuthoredWindowError(RecurrenceResolutionError):
    """The spec's date stop precedes the first occurrence the write door would store.

    **The one refusal that lives at the WRITE door rather than in ``resolve``**
    (plan step R7d-g), and where it lives is the point.  ``_author`` stores
    ``resolve(spec).starts_on`` -- the NORMALISED first occurrence, which for a
    paycheck-space cadence is the payday of the paycheck that hosts the
    authored date, lifted to the owner's FIRST payday when the authored date
    precedes every paycheck -- beside the RAW closing bound the spec states.
    Both authoring doors grade the stop against the AUTHORED start, so an
    every-paycheck rule authored entirely before the schedule opens passes
    them (``2025-06-01`` to ``2025-12-01`` on a schedule opening ``2026-01-02``)
    and would reach the table as ``[2026-01-02, 2025-12-01]``: the inverted
    pair ``ck_recurrence_rules_valid_window`` refuses, as an unhandled
    ``IntegrityError``.  Refusing HERE, off the value that would be stored, is
    what makes the constraint true by construction: one writer, one comparison
    over the stored pair, and the doors translate this into their own sentence
    (``end_bound_before_start_message``, worded off the same two dates).

    NOT in ``resolve``, deliberately: readers resolve stored rules on every
    pass, and a stored pair the constraint admits can re-normalise past its
    stop after a pay-schedule rebuild moves the opening payday (plan ledger
    row **D39**'s shape) -- an EMPTY window the walk answers with zero
    occurrences, which a refusal on read would turn into a 500 on every
    surface.  A write is where a user can be told; a read is not.

    Attributes:
        starts_on: The first occurrence the row would have stored.
        end_date: The stop the spec states, which precedes it.
    """

    def __init__(self, *, starts_on, end_date):
        self.starts_on = starts_on
        self.end_date = end_date
        super().__init__(
            f"recurrence stops on {end_date} before its first occurrence "
            f"{starts_on}: the pair would violate "
            f"ck_recurrence_rules_valid_window, so it is refused before the "
            f"write rather than by the flush"
        )


#: What a recurrence rule may belong to: the two recurring-definition kinds
#: and, since plan step salary:R15-b (ruling **R-SAL32**), a payroll
#: deduction -- the three arms of ``budget.recurrence_rules``' owning arc.
#: Named rather than spelled inline because :func:`author_rule` is the one
#: door that binds an owner; the third kind was exactly the edit this comment
#: promised: one line here plus the column and the arm it needs.
RecurrenceOwner = TransactionTemplate | TransferTemplate | PaycheckLine


def _author(
    rule: RecurrenceRule, spec: RecurrenceSpec, calendar: PayCalendar,
) -> None:
    """Write *spec* onto *rule*, every column of it.

    The ONE place a recurrence rule's columns are assigned.  It writes the
    authored spec -- the columns that state the recurrence, plus the closing
    bound's exclusive arc -- taking every
    value from the same ``resolve`` call that validates it, which is also where
    the pay-period normalisation and the canonical cadence are decided.

    **There is nothing left to ENCODE since plan step R7c-c.**  The table stated
    its cadence with a closed set of pattern names until then, so this function
    wrote two representations of one fact: the authored columns, and
    ``pattern_id`` / ``interval_n`` / ``day_of_month`` derived from them.  All
    three of the second set are dropped, and what remains is a straight write of
    what the caller stated -- which is what makes "a stored authored value is a
    fact" true of every column on the row rather than of five of eight.  The
    one column nobody states is the unit's carried flag (plan step plan#2302),
    and its key holds it to the unit rather than this function's word.

    **The two-axis columns are AUTHORED from plan step R7c-b**, which is what
    retires the "a stored derivation is a cache" objection ruling R2d raised
    against writing them at all.  ``starts_on`` is what the form collects and
    what the loan sync writes from a contract; there is no input left for it to
    lag behind.  The window in which it could drift was the window in which
    nothing read it, and that window closed with R7c-b's re-backfill.

    **A day-less LOAN PAYMENT is the one value still measured against the
    schedule**, and it is narrow.  Its date is the loan's first contractual
    installment; when the rule bills by PAYCHECK rather than on a day of the
    month, the stored value is the payday of the paycheck that installment falls
    in, so a schedule rebuilt under it moves what the column would be re-derived
    as -- and the installment date is not recoverable from the row, which can
    put the rule's opening bound up to a pay period before the loan exists.
    Both of the developer's live loan payments fire on a day of the month,
    where the installment IS the occurrence and the value is contract-derived.
    Plan ledger row **D39** tracks the day-less shape, and owns it at plan step
    R5.  (It was **D6** until plan step R7c-c, which CLOSED that row: D6 said
    folding the opening bound into the anchor was lossy, and ruling **R-R16**
    answers that -- there is no fold, the first occurrence IS the bound -- so
    the residual was re-measured under an id of its own rather than carried
    under a wording that no longer described it.)

    **Resolved BEFORE the write, and ONE call does every job.**  A recurrence
    that cannot be resolved must not reach the table, and ``resolve`` is where
    every such refusal already lives -- an owner mismatch, a non-positive
    interval, a due day outside its column's domain, a first occurrence outside
    the calendar this application reaches, a pay-period cadence against an empty
    schedule.  Re-checking those here would be a second copy of one judgement.
    Taking the normalised date, the canonical cadence and the nominal day from
    that one result rather than deriving each again is the other half: several
    calls could not disagree today, but they would be several producers of one
    value, which is the shape this step exists to remove.

    **A refusal ran here BEFORE ``resolve`` until plan step R7c-c, and where it
    went is worth stating.**  ``encode_cadence`` was a pure table lookup asking
    "has this cadence anywhere to be written at all", and running it first was
    measured by an adversarial review of plan step R7b-2: resolving first meant
    doing arbitrary month arithmetic on a cadence about to be refused anyway,
    and ``(10000, YEAR)`` reached ``_months.clamped_day``, which builds a
    ``date`` from a month ordinal, raising ``ValueError: year must be in
    1..9999`` -- outside this package's error hierarchy, so the recurrence
    preview's handler did not catch it and a signed-in GET was an unhandled 500.
    R7c-c makes every cadence storable, so that lookup has nothing left to
    refuse -- and the hazard it incidentally covered is now closed at its own
    root instead, in ``_months.walk_months``, which stops at the last month the
    application's calendar reaches rather than walking off the end of ``date``.
    A cadence whose second occurrence lies past that point fires once.

    Args:
        rule: The rule to write, new or existing.
        spec: Its complete authored state.
        calendar: The owner's pay-period schedule.

    Raises:
        RecurrenceResolutionError: When *spec* cannot be resolved against
            *calendar* -- see :func:`~app.services.recurrence.resolve`.

            **``RecurrenceGenerationError`` left this list at plan step
            R7c-b**, and it left because the function that raised it did.  The
            pay-period normalisation is ``resolve``'s now, so an empty schedule
            is refused in the resolution hierarchy rather than in the
            generation one -- one door, one error class for a caller to catch
            around.
        EmptyAuthoredWindowError: The stop *spec* states precedes the first
            occurrence this call would store (a subclass of the above, so a
            caller catching the base catches it too).  Nothing is written.
    """
    resolved = resolve(spec, calendar)
    # The pair this call would STORE, graded before the first assignment
    # below so a refusal leaves the row exactly as it was: see
    # :class:`EmptyAuthoredWindowError` for why this is the one comparison and
    # why it is here and not in ``resolve``.
    stop = spec.end_bound.columns().end_date
    if stop is not None and stop < resolved.starts_on:
        raise EmptyAuthoredWindowError(starts_on=resolved.starts_on, end_date=stop)

    # **The owner is NOT written here, since plan step R-F6**, and it is the
    # one authored-looking value this function does not touch: a rule's owner
    # is the definition holding it (``rule.transaction_template`` /
    # ``rule.transfer_template``), and ``RecurrenceRule.user_id`` reads through
    # to that definition's.  ``spec.user_id`` states which owner the caller
    # BELIEVES it is authoring for, and its whole job is the pairing check in
    # ``resolve`` above -- a spec resolved against somebody else's pay calendar
    # produces a plausible wrong date rather than an error.  Writing it onto
    # the row would put a second copy of the owner beside the first, which is
    # what that step deleted.
    # ---- what the rule AUTHORS -------------------------------------------
    #
    # **The WHOLE table since plan step R7c-c**: six columns, every one of them
    # a value a caller states, and no encoding beside them -- seven since plan
    # step salary:R15-a added the per-month ceiling, eight since plan step
    # recurrence:R8-c added the weekday reading, with the carried unit flag
    # below the one column no caller states since plan#2302.  They are written
    # from the ONE ``resolve`` call above, whose cadence has already been
    # through
    # :func:`~app.services.recurrence._frequency.canonical_cadence` -- so
    # ``interval_n`` and ``unit_id`` are the
    # canonical spelling of the cadence rather than whatever the caller happened
    # to type, and every other column is derived from that same pair.
    #
    # ``starts_on`` is ``resolved``'s rather than ``spec``'s, and the difference
    # is the pay-period NORMALISATION: a caller may author any date for a
    # paycheck-space cadence and ``resolve`` answers the payday of the paycheck
    # that hosts it, so what reaches the column is always a real occurrence
    # (ruling **R-R16**).  For every other unit the two are the same value.
    rule.interval_n = resolved.interval_n
    rule.unit_id = ref_cache.recurrence_unit_id(resolved.unit)
    # The unit's flag, carried so the row's CHECKs can read it (plan step
    # plan#2302, ruling plan#2748): off the same resolved unit, because
    # ``fk_recurrence_rules_unit_day_of_month`` refuses a pair that disagrees.
    rule.unit_has_day_of_month = has_day_of_month_coordinate(resolved.unit)
    rule.placement_id = ref_cache.period_placement_id(resolved.placement)
    rule.shift_id = ref_cache.business_day_shift_id(resolved.shift)
    rule.starts_on = resolved.starts_on
    rule.nominal_day = resolved.nominal_day
    # How ``starts_on`` is read (plan step recurrence:R8-c): the member's
    # value, the literal ``ck_recurrence_rules_weekday_reading`` names, and
    # ``None`` for a rule read by its day of the month.  Off the resolved
    # value like the day beside it, so the reading the walk takes and the
    # reading the row holds are one.
    rule.weekday_reading = (
        None if resolved.weekday_reading is None
        else resolved.weekday_reading.value
    )
    # The cadence's third value (plan step salary:R15-a, ruling R-SAL29):
    # off the resolved value like the two beside it, so the ceiling the walk
    # applies and the ceiling the row holds come from one canonicalisation.
    rule.max_per_month = resolved.max_per_month
    # The closing bound is ONE authored value and TWO columns under an
    # exclusive arc (``ck_recurrence_rules_single_end_bound``), so it is split
    # here and rejoined at the read door -- the only two places the pair is
    # ever seen apart.  Assigning them from one ``columns()`` call rather than
    # from two accessors is what makes "never both" a property of this line
    # rather than of the value's two readers agreeing (plan step R7b-3).
    end_columns = spec.end_bound.columns()
    rule.end_date = end_columns.end_date
    rule.max_occurrences = end_columns.max_occurrences


def build_transient_rule(
    spec: RecurrenceSpec, calendar: PayCalendar,
) -> RecurrenceRule:
    """Build a resolved rule on an UNSAVED owner, adding neither to the session.

    For the read-only caller that needs a real rule OBJECT -- one carrying the
    columns a saved rule would -- without writing a row.

    **Its production caller left at plan step R-F6 and its remaining callers
    are all tests.**  The recurrence PREVIEW was the production one and no
    longer builds a row at all: it resolves the submitted spec directly, which
    is the spec-to-row-to-spec round-trip that step removed.  What still needs
    a rule OBJECT is the frozen baseline oracle
    (``tests/oracles/recurrence_baseline``), because
    ``recurrence.compute_due_date`` takes one, plus the test helpers
    and route-helper cases that hand a resolved rule to a producer without
    persisting it.  Plan step **R5-b** re-examines this entry point with
    ``compute_due_date``'s last caller rather than removing it ahead of it.

    **It BUILDS the owner rather than taking one, and an adversarial review of
    this step is why.**  A rule belongs to exactly one definition -- the schema
    says so for a ROW (``ck_recurrence_rules_one_owner``) and this says so for
    an OBJECT, so :attr:`RecurrenceRule.user_id`, which reads through to the
    owner, has an answer for every rule the application constructs.  The first
    shape took the owner as an ARGUMENT, and that was measured writing a row:
    handed an already-flushed template, ``owner.recurrence_rule = rule`` puts
    the "transient" rule inside the ``save-update`` cascade and the next flush
    INSERTs it -- something the pre-R-F6 version could not do under any
    argument.  Constructing the owner here is what makes "adds nothing to the
    session" a property of the function rather than of its callers: the
    template is created unsaved, referenced by nothing else, and discarded with
    the rule.

    The TRANSACTION arm is used because a transient rule's arm is read by
    nothing -- ``user_id`` is the only thing anyone asks an unsaved rule, and
    both arms answer it identically.  A caller that needed a specific kind
    would need a saved one.

    Args:
        spec: What to author.  Its ``user_id`` is the owner the built
            definition names, and therefore what ``rule.user_id`` reports.
        calendar: The owner's pay-period schedule.

    Returns:
        The unsaved, fully authored :class:`RecurrenceRule`.

    Raises:
        RecurrenceResolutionError: When the spec cannot be resolved -- see
            :func:`~app.services.recurrence.resolve`.
    """
    rule = RecurrenceRule()
    _author(rule, spec, calendar)
    TransactionTemplate(user_id=spec.user_id).recurrence_rule = rule
    return rule


def author_rule(
    spec: RecurrenceSpec, calendar: PayCalendar, owner: RecurrenceOwner,
) -> RecurrenceRule:
    """Create a recurrence rule ON its definition, and flush it.

    **The owner is an ARGUMENT since plan step R-F6, and that is the whole of
    what changed.**  A rule used to be authored free-standing and flushed for
    its id, which the caller then wrote onto the definition's
    ``recurrence_rule_id`` -- so the link was the CALLER'S job, and a caller
    that forgot it (or a route that deleted the definition afterwards) left a
    rule belonging to nothing.  Finding **F-6** is three such rows.  The link
    is made here now, and the schema refuses a rule without one
    (``ck_recurrence_rules_one_owner``), so there is no order of operations
    left for a caller to get wrong.

    Assigned through ``owner.recurrence_rule`` rather than by picking the arc's
    arm here: both definition kinds spell the relationship that way, so this
    stays kind-agnostic and SQLAlchemy writes whichever FK column the owner's
    own mapper names.  Same reason
    :func:`app.services.template_amount_service.set_amount` takes the template
    rather than a column for the other satellite of these two parents.

    **The OWNER decides whose rule this is, and the spec's own ``user_id`` is
    CHECKED against it rather than overwritten.**  Overwriting was the first
    shape and it was measured wrong the same day, by
    ``test_recurrence_authoring.TestTheAuthoredSurfaceIsWholeAndClosed
    ::test_every_spec_field_reaches_the_row``: a field a caller may state and
    the door silently discards is a field that lies about what it does, which
    is exactly the property that census exists to refuse.  So the pairing is
    stated in three places and all three must agree -- the spec, the calendar
    and the definition -- and the two comparisons that close the triangle are
    this one and ``resolve``'s own ``_require_owner_match``.

    It is not a fence standing in for a constraint, which is what plan step
    R-F6 removes elsewhere: no schema can hold it.  The rule's owner IS the
    definition (``ck_recurrence_rules_one_owner`` makes that structural), and
    what this refuses is the caller having RESOLVED the cadence against a
    different owner's paydays -- a fact about a computation that has already
    happened by the time any row exists.

    Still flushed, and now for a different reason: not to hand the caller an
    id, but so that a spec the database refuses -- a cadence past a CHECK, a
    definition that already carries a rule -- surfaces inside the caller's own
    request rather than at an unrelated commit later.

    Args:
        spec: What to author.  Its ``user_id`` must name *owner*'s owner.
        calendar: The owner's pay-period schedule.  Refused by ``resolve`` when
            it is not the OWNER'S -- a pay-period cadence's first occurrence
            and its phase are both measured against it, so the mismatched pair
            would produce a plausible wrong date rather than an error.
        owner: The ``TransactionTemplate``, ``TransferTemplate`` or (since
            plan step salary:R15-b) ``PaycheckLine`` this rule belongs
            to.  Mutated: its ``recurrence_rule`` is set to the new rule.  It
            need not be flushed -- SQLAlchemy orders the parent's INSERT
            before the rule's.

    Returns:
        The flushed :class:`RecurrenceRule`.

    Raises:
        RecurrenceResolutionError: When *spec* names a different owner than
            *owner* does, or when it cannot be resolved against *owner*'s
            schedule -- see :func:`~app.services.recurrence.resolve`.
    """
    if spec.user_id != owner.user_id:
        raise RecurrenceResolutionError(
            f"a recurrence authored for user {spec.user_id} cannot be written "
            f"onto a definition owned by user {owner.user_id}.  The rule would "
            f"belong to the definition's owner while its first occurrence and "
            f"phase were resolved against the other's paydays, which is a "
            f"plausible wrong date rather than an error."
        )
    rule = RecurrenceRule()
    _author(rule, spec, calendar)
    owner.recurrence_rule = rule
    db.session.add(rule)
    db.session.flush()
    return rule


def reauthor_rule(
    rule: RecurrenceRule, spec: RecurrenceSpec, calendar: PayCalendar,
) -> None:
    """Replace an existing rule's entire authored state, in place.

    The rule keeps its primary key and its owning arc -- and therefore every
    generated row's lineage -- while every column it defines is re-derived from
    *spec*.  It cannot change owner: the arc is not part of the authored state
    and nothing in a spec names a definition.

    Args:
        rule: The rule to re-author.
        spec: Its complete new authored state.
        calendar: The owner's pay-period schedule.

    Raises:
        RecurrenceResolutionError: When the spec cannot be resolved -- see
            :func:`~app.services.recurrence.resolve`.
    """
    _author(rule, spec, calendar)
