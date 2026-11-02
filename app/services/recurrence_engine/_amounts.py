"""
Shekel Budget App -- Recurrence Engine: WHAT a generated row's definition says

:class:`DerivedRowFields` -- the single statement of which columns a generated
row takes from its template rather than from its owner -- and its producer.

**One statement of the derived columns is what lets a regeneration UPDATE a row
instead of destroying and rebuilding it** (plan step R10-a, ruling **R-R19**).
Before it, the create path listed these fields inline and no update path
existed, so "make this row match its template again" could only be spelled as
"throw it away and build another" -- which took the owner's purchases, notes
and flags with it (finding **N-292**).

**THIS MODULE PRICES NOTHING AT ALL, and that is plan step X-au-e.**  X-au-d
took the paycheck: this module carried ``_get_salary_profile`` and
``_get_transaction_amount``, the second running
``paycheck_calculator.calculate_paycheck`` over the owner's whole schedule and
storing the answer in ``estimated_amount``, which ruling **R-FI** calls a cache
of a derivation and finding **N-224** measured as an app holding two answers to
one question.  What that step LEFT was a fork -- a definition that states its
price handed its rows that figure to OWN -- and X-au-e deletes the fork rather
than one of its arms.  Every generated row is DERIVED now, priced by its
definition's own effective-dated series as of the row's OWN due date (amount
rule 3, ``template_amount_service.amount_as_of``).

**A generator that prices nothing cannot mis-price.**  That retires the class of
defect migration ``a3f8b1c40d92`` was written to repair -- the ``$502.45`` a
window-narrowed period list put into one stored salary row -- and it is what
dissolves two findings rather than fixing them:

* **N-247**, one date under two predicates: a template amount edit's sweep
  selects rows by their pay PERIOD's end while the series answers by a row's own
  DUE date, so an edit could rewrite a row whose due date preceded the date it
  stated.  A sweep that writes no figure decides nothing about money.
* **N-244**, the back-dated re-price: this module and the conflict chooser were
  its two writers of today's price onto a past row, and neither writes one now
  (``recurrence_engine._conflicts`` hands a row back to its definition instead
  of to a figure).
"""
from datetime import date
from typing import NamedTuple

from app.enums import AmountSourceEnum
from app.models.amount_ownership import AmountOwnership
from app.services.amount_ownership import derived_ownership
from app.services.recurrence import compute_due_date



class DerivedRowFields(NamedTuple):
    """What a template and a placed occurrence DERIVE on a generated transaction.

    **THE one statement of which columns a generated row takes from its
    DEFINITION rather than from its owner**, and the reason
    :func:`regenerate_for_template` no longer destroys the rows it maintains.

    Both write paths consume it: :func:`generate_for_template` splats it into a
    new ``Transaction``, and :func:`regenerate_for_template` assigns it onto an
    existing one.  So a column added here is written on a NEW row and kept
    current on an EXISTING one from the same edit -- which is the property that
    made delete-and-recreate look necessary in the first place.  Before plan
    step R10-a the create path listed these fields inline and no update path
    existed, so "make the row match the template again" could only be spelled
    as "throw the row away and build another".  That cost the owner every
    column a template does NOT derive: the purchases recorded against the row
    (``transaction_entries`` CASCADED from their parent until plan step
    ``credit_card:CC-5-4a-4``), its ``notes``, its
    ``is_envelope`` and ``companion_visible`` flags, its ``created_at``, and
    its own id -- finding **N-292**, measured at 3 purchase records worth
    ``$499.82`` on one live row, destroyed with no prompt by an edit as small
    as a rename.

    Every field here is derived and none is the owner's, which is what makes
    overwriting one on an existing row safe.  The three columns that decide
    whether the row is the RULE's at all -- ``is_override``, ``is_deleted`` and
    ``status_id`` -- are deliberately absent: they are the classification the
    caller applies BEFORE deciding to write, never something a write restates.

    **The amount is carried as OWNERSHIP rather than as a figure, and that
    settles what used to be a deliberate omission** (plan step **X-au-k**,
    closing finding **N-293**).  This class carried ``estimated_amount`` and
    NOT ``amount_source_id``, because the two were separately mapped columns
    that ``ck_transactions_amount_ownership`` pairs one-to-one: splatting a
    figure onto a row already DERIVED wrote one half of the pair and aborted
    the whole template edit at flush.  Carrying both fields would have fixed
    the abort and introduced a silent un-derive, so the field stayed out and
    the abort was accepted as the better failure.  Neither is expressible now:
    :class:`~app.models.amount_ownership.AmountOwnership` is ONE attribute, so
    this class states the row's whole ownership or none of it.

    **The splat can no longer hand a DERIVED row back to its owner, and that
    is structural rather than guarded** (plan step **X-au-e**, the condition
    finding **N-437** was closed under).  This field held one of two values
    while generation still priced: ``own`` over a scalar for a definition that
    stated its price, ``derived`` for one whose price was computed.  Only the
    second remains, so the value this class can carry is a CONSTANT and there
    is no arm left that writes a figure onto a row -- the question has no site
    to be asked at rather than an answer that happens to be right.

    Attributes:
        account_id: The account the row is EXPECTED to be paid from, from the
            template (ruling **R-CC16**).  Applicable to every row this pass
            may rewrite since plan step ``credit_card:CC-5-2`` (ruling
            **R-CC36**): a row holding purchases moves with its definition
            and its purchases STAY where their money moved, each on the
            account of its own (**R-BAL76**).  Through ``CC-5-1`` a row
            holding records was RETAINED on an account move instead, because
            ``fk_transaction_entries_parent_account`` dragged its purchases
            with it and invalidated any statement link they carried; that key
            is gone and every reader asks the movement's own account, so the
            arm has nothing left to protect (``_maintain._no_row_is_reattributed``).
        name: The template's name.  Also propagated to rows OUTSIDE this pass's
            reach by ``definition_edit.apply_fields``,
            which covers the historic and immutable rows a regeneration never
            touches.
        category_id: The template's category, or ``None``.
        transaction_type_id: Expense or income, from the template.
        amount_ownership: WHERE this row's amount comes from, as ruling
            **R-FI**'s one attribute rather than a figure.  Since plan step
            X-au-e it states ONE shape for every generated row -- ``derived``
            naming :attr:`~app.enums.AmountSourceEnum.TEMPLATE` -- so the row
            carries a declaration and no figure, and its definition's own
            effective-dated series prices it as of its due date.  It was a
            fork on ``template_amount_service.owns_its_amount`` until that
            step, and the arm that fork selected is what stored the copy the
            cutover deleted.
        due_date: Derived from the rule and the placed occurrence by
            :func:`compute_due_date`, which always answers one -- the
            occurrence itself for a calendar cadence (weeks, months, years)
            funded by the paycheck containing it, the funding paycheck's
            payday for a paycheck cadence or one funded from a later paycheck
            (plan step R5-a, rulings **R-R94** / **R-R95**; weeks since R8-b).
            It was annotated ``date | None`` until plan step **X-bv-2**, which
            binds ``ck_transactions_template_row_needs_due_date`` (a row of a
            definition is dated) and tightened the type to the fact the
            producer had always stated (ruling **R-BAL17**).
    """

    account_id: int
    name: str
    category_id: int | None
    transaction_type_id: int
    amount_ownership: AmountOwnership
    due_date: date




def _derive_row_fields(template, rule, occurrence, period):
    """Resolve what *template* derives on the row answering *occurrence*.

    The producer of :class:`DerivedRowFields` for a definition WITH a rule,
    so the create path and the maintain path cannot disagree about what a
    generated row's definition says -- see that class for why one statement
    of it is what lets a regeneration UPDATE a row instead of destroying and
    rebuilding it.  A definition with NO rule states the same five columns
    and no date through :func:`_derive_unruled_fields` (plan step
    ``balance:X-bi-7a``), which is the other way a row of a definition is
    brought into line.

    **It took a ``GenerationSchedule`` and looked *period* up on that value's
    calendar until pay-calendar plan step C2-f3c**, then the CALENDAR alone
    until plan step **X-au-d**.  Both parameters existed for one reason: the
    paycheck engine, which this module no longer runs.  A pricing pass needs
    the owner's whole schedule -- four of the engine's judgements read it, and
    narrowing it to a write window is what stored one salary row ``$502.45``
    low (migration ``a3f8b1c40d92``) -- so the requirement did not go away; it
    MOVED, to :meth:`app.services.income_service.SalaryPricing._breakdown_by_period`,
    which derives the calendar it projects over and is the amount model's one
    walk to a paycheck.  Nothing left here reads a period beyond the one it is
    dating a row in.

    Args:
        template: The :class:`~app.models.transaction_template.TransactionTemplate`
            being generated from.
        rule: The template's recurrence rule, already confirmed present by
            :func:`resolve_generation_plan` (``GenerationPlan.rule``).
        occurrence: The date the rule names for this row, straight off its
            ``PlannedOccurrence`` -- what the row is dated FROM since plan
            step R5-a (plan ledger row **D18**: it was dated from *period*
            alone, and two occurrences seated in one paycheck shared a date).
        period: The :class:`~app.services.pay_calendar.DerivedPeriod` this row
            lives in, straight off the same ``PlannedOccurrence``.

    Returns:
        The :class:`DerivedRowFields` for this (template, occurrence) pair.
    """
    return DerivedRowFields(
        account_id=template.account_id,
        name=template.name,
        category_id=template.category_id,
        transaction_type_id=template.transaction_type_id,
        amount_ownership=derived_ownership(AmountSourceEnum.TEMPLATE),
        due_date=compute_due_date(rule, occurrence, period),
    )


def unruled_row_fields(template, due_date):
    """Resolve what a RULE-LESS *template*'s definition says about a row due on *due_date*.

    FIVE of the six columns, exactly as ``transfer_recurrence._derive_unruled_fields``
    states them for the twin table (plan step ``balance:X-bi-7a``).  A
    definition with no recurrence places no occurrence, so it states no DUE
    DATE: the row's is the owner's to state under ruling **R-BAL22** (its
    placed paycheck's start unless they say otherwise), and it is taken as an
    argument rather than dropped from the tuple so there is still exactly one
    statement of what a definition says about a row.

    **Two callers, one statement** (plan step ``balance:X-bi-7b``).  The
    maintain twin :func:`_derive_unruled_fields` brings an EXISTING row back
    into line with its definition and reads the date off the row; the one-off
    producer ``one_off.place_row_of`` states a NEW row and knows the date
    before the row exists.  Written as one function so the row a one-off is
    born with and the row its definition's edit re-declares cannot come to
    differ in any of the five.

    Args:
        template: The rule-less
            :class:`~app.models.transaction_template.TransactionTemplate`.
        due_date: The day the row is due on, which is the day amount rule 3
            prices it (``ck_transactions_template_row_needs_due_date`` says a
            row of a definition always has one).

    Returns:
        The :class:`DerivedRowFields` this definition says about such a row.
    """
    return DerivedRowFields(
        account_id=template.account_id,
        name=template.name,
        category_id=template.category_id,
        transaction_type_id=template.transaction_type_id,
        amount_ownership=derived_ownership(AmountSourceEnum.TEMPLATE),
        due_date=due_date,
    )


def _derive_unruled_fields(template, row):
    """Resolve what a RULE-LESS *template*'s definition says about *row*.

    :func:`unruled_row_fields` read at the row's own due date -- the maintain
    twin's spelling, for a row that already exists.

    Args:
        template: The rule-less
            :class:`~app.models.transaction_template.TransactionTemplate`.
        row: The :class:`~app.models.transaction.Transaction` it holds,
            which supplies the one field the definition does not state.

    Returns:
        The :class:`DerivedRowFields` this definition says about *row*.
    """
    return unruled_row_fields(template, row.due_date)
