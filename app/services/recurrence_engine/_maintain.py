"""
Shekel Budget App -- Recurrence Engine: bringing generated rows back into line

:func:`regenerate_for_template` and the pass behind it.  Run when a template's
amount, schedule or fields change, it MAINTAINS the rows the definition already
generated: a row the rule still names is updated in place, a period the rule
names with no row gets one, and a row the rule no longer names is retired --
unless the owner has records against it, in which case the pass leaves it
exactly as it found it and asks.

**Until plan step R10-a this deleted every auto-generated row in the window and
generated replacements** (ruling **R-R19**, finding **N-292**).  That was safe
only while a generated row was a pure projection of ``(template, period)``, and
it has not been one for a long time: ``transaction_entries`` CASCADED from their
parent (until plan step ``credit_card:CC-5-4a-4``), so an edit as small as a
rename destroyed the PURCHASES recorded
against a part-spent envelope -- measured on a production clone at 3 records
worth ``$499.82``, taken with no prompt and an ``overridden_conflict_count`` of
0.  It also dropped ``notes``, ``is_envelope``, ``companion_visible``,
``actual_amount``, ``created_at`` and the row's own id.

The decision and the write are separate on purpose
(:func:`~app.services._recurrence_common.classify_maintain_work` reads,
:func:`_apply_maintain_work` writes), so what a regeneration decides can be
asserted without a database write.  **The decision itself is SHARED with the
transfer engine since plan step R10-b** and lives in
:mod:`app.services._recurrence_common`; what stays here is what is about this
table -- what a generated row's definition says, what "the owner's records"
means on it, and the write.
"""
import logging

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import posting_service
from app.services.recurrence_engine._generate import _selector
from app.services.recurrence_engine._amounts import (
    _derive_row_fields,
    _derive_unruled_fields,
)
from app.services.recurrence_engine._pass import (
    MaintainActs,
    PassReporting,
    RegenerationPreview,
    preview_regeneration,
    regenerate_definition,
)
from app.utils.log_events import EVT_RECURRENCE_REGENERATED

logger = logging.getLogger(__name__)



def regenerate_for_template(template, schedule, scenario_id, effective_from=None):
    """Bring a template's future rows into line with its current definition.

    Run when a template's amount, schedule or fields change.  **It MAINTAINS
    the rows the rule still names rather than destroying and rebuilding them**,
    which is plan step R10-a and ruling **R-R19**, closing finding **N-292**.

    Four outcomes, one per period the pass considers:

      1. the rule names the period and an auto-generated row is there -- the
         row is UPDATED in place from :class:`DerivedRowFields` and its
         postings reconciled;
      2. the rule names the period and nothing is there -- a row is created,
         exactly as :func:`generate_for_template` would;
      3. the rule NO LONGER names the period -- the row is removed if it is
         empty, and RETAINED as a conflict if the owner has records against it;
      4. the row answers NO occurrence at all (``occurs_on`` is NULL) -- it is
         left exactly as found and RETAINED as a conflict, because the
         definition has moved past a row it cannot place (finding
         **REC-516**).

    Overridden and soft-deleted rows are conflicts wherever they sit, as
    before; immutable rows are never touched.

    **Until plan step R10-a this deleted every auto-generated row in the window
    and generated replacements**, which was safe only while a generated row was
    a pure projection of ``(template, period)``.  It has not been one for a
    long time: ``transaction_entries`` CASCADED from their parent (until plan
    step ``credit_card:CC-5-4a-4``), so the sweep
    destroyed the PURCHASES recorded against a part-spent envelope -- measured
    on a production clone at 3 records worth ``$499.82``, taken silently by an
    edit as small as a rename, with ``overridden_conflict_count`` reporting 0.
    It also dropped ``notes``, ``is_envelope``, ``companion_visible``,
    ``actual_amount``, ``created_at`` and the row's own id, and the lost id is
    why plan step X-f3b had to reverse a row's ledger legs on the way out.  A
    row the rule still names is now never deleted, so none of that is reachable
    on this path and no future column has to be remembered either.

    **Maintaining is not a behaviour change for a row with nothing on it, and
    that is provable rather than hoped for.**  ``Projected`` is the ONLY
    non-immutable status in ``ref.statuses``, so this pass can reach nothing
    else, and an update writes exactly the values a recreation would have
    written -- measured at 504 of 505 live sweepable rows identical, the 505th
    being the one that keeps its purchases.

    **The pass and the rule share ONE bound and ONE domain, and since
    pay-calendar plan step C2-f3c both are structural rather than upheld.**
    They shared the bound before it too -- ``resolve_generation_plan`` resolved
    the ORM row before applying it, so both halves read the same STORED
    ``end_date`` -- and an adversarial review of C2-f3c corrected a draft of
    this paragraph that claimed otherwise.  What changed is that both now read
    the DERIVED end, off the same calendar, because plan step **C4-c** dropped the
    column they used to agree on.

    What ``regeneration_bound`` actually existed for was narrower: the sweep
    was SQL, and ``end_date >= NULL`` matches no row, so "no lower bound" had
    to become a concrete date before it could be compared against a column at
    all -- and the date it became had to be the WRITE WINDOW's opening, because
    a sweep bounded by the SCHEDULE's opening would have reached every
    non-override row from the owner's first payday forward while the rule was
    resolved only inside the window, retiring rows nothing would recreate.  A
    period-ID set taken from that same window
    (``_recurrence_common.rows_this_pass_may_maintain``) needs no translation
    for ``None`` and cannot exceed the window whatever the bound is, so both
    halves of that helper's job are gone and the helper with them.

    Args:
        template:       The updated TransactionTemplate.
        schedule:       The owner's
                        :class:`~app.services.generation_schedule.GenerationSchedule`.
        scenario_id:    The target scenario.
        effective_from: Date from which to maintain, or ``None`` for the whole
                        write window.  The row select and the rule take it
                        unchanged, and read it against the same derived end.

    Returns:
        List of newly created Transaction objects.  Rows this pass UPDATED are
        not in it, so the value keeps the meaning every caller already reads it
        with.

    Raises:
        RecurrenceConflict: When rows exist that this pass must not change
            unasked.  The caller should catch it, present the options, and call
            :func:`resolve_conflicts`.
    """
    return regenerate_definition(
        _PASS, template, schedule, scenario_id, effective_from,
    )


def preview_regeneration_for_template(
    template, schedule, scenario_id, effective_from=None,
) -> RegenerationPreview:
    """Return what :func:`regenerate_for_template` would do to existing rows, unwritten.

    The pass's own decision, read and not applied
    (:func:`~._pass.preview_regeneration`): the transactions it would bring
    into line, with the fields it would write onto each, and those it would
    delete.  Read by the edit door's stranded-row refusal before the save
    regenerates (plan step ``pay_calendar:C18-a``).

    Args:
        template: The edited TransactionTemplate.
        schedule: See :func:`regenerate_for_template`.
        scenario_id: The scenario the save would regenerate.
        effective_from: See :func:`regenerate_for_template`.

    Returns:
        The :class:`~._pass.RegenerationPreview`.
    """
    return preview_regeneration(
        _PASS, template, schedule, scenario_id, effective_from,
    )




def _rows_holding_owner_records(existing) -> set[int]:
    """Return the ids of rows carrying content their template did not put there.

    "The owner's own records" is what finding **N-292** is about: a generated
    row is not a pure projection of ``(template, period)``, and the columns
    below are the ones a regeneration cannot reconstruct.  Purchases are the
    costly one -- ``transaction_entries`` CASCADED from their parent (until plan
    step ``credit_card:CC-5-4a-4``), so the old
    delete-and-recreate sweep destroyed them, measured at 3 records worth
    ``$499.82`` on one live row.

    **The ENTRIES are asked in ONE query, not one per row.**  A regeneration
    considers every future row of a template -- 505 of them across the live
    templates on a production clone -- so reading ``row.entries`` in the
    classifier would issue a query per row on the hot path of every template
    edit.

    **The entries are the whole answer for money, and that is structural
    rather than upheld** (plan step ``balance:X-bi-4b-2``, rulings
    **R-BAL80**, **R-BAL82**).  A row's family of movements is every record
    of what its money did: a person's purchases, and the settle's own
    covering movement, which a revert un-dates and KEEPS (plan step
    X-bi-3e-2) -- so a row the owner settled at a figure and then set back
    to Projected, mutable to this sweep, still holds that movement, and the
    retained figure (a number read off a bank statement) is held back as a
    CONFLICT for the owner to resolve.  A ``$0.00`` close keeps no movement
    and is retained by nothing across a revert (R-BAL82), so a reverted
    close of nothing follows its template freely, as a row that never
    settled does.  The arm read the row's own ``settled_basis_id`` from
    plan step X-au-c3 until X-bi-4b-2 deleted that column: the same fact in
    two homes, and this predicate was one of the two readers keeping them
    in step.

    **A STATEMENT LINK counts too, and it needs no condition of its own** --
    which plan step R10-b measured, correcting what R10-a's own adversarial
    review concluded here.  ``ck_transactions_cleared_needs_settle_day`` says
    a link needs a settle day, and no row this pass can see carries one:
    ``Projected`` is the only status the pass reaches (the module docstring),
    and the seam releases the day and the link together on the way out of the
    settled band (``status_seam.apply_status_change``).  A row that carries a
    link is settled, and a settled row's money is its entries, held by the
    query above.  (Until plan step ``balance:X-bi-4b-2`` the chain ran one
    link further -- ``ck_transactions_settle_day_needs_a_record`` made a
    settle day imply a ``settled_basis_id`` -- and rested on that column's
    arm.)  R10-b's argument was about the ACCOUNT-MOVE half of the retention
    rule -- a link is scoped by account, so a linked row must not be moved --
    and that half is retired for this engine at plan step
    ``credit_card:CC-5-2`` (ruling **R-CC36**, :func:`_no_row_is_reattributed`):
    a purchase's link is scoped by the PURCHASE's own account, which its row
    moving never touches.  What this set still decides is the other half:
    a row the rule no longer names is retired when it carries nothing and
    RETAINED when it carries any of these.

    Args:
        existing: The rows this pass is considering.

    Returns:
        The subset of their ids that hold an entry -- a purchase or a retained
        covering movement -- or a note of their own.
    """
    ids = [row.id for row in existing]
    if not ids:
        return set()
    holding = {
        transaction_id
        for (transaction_id,) in db.session.query(
            TransactionEntry.transaction_id,
        ).filter(TransactionEntry.transaction_id.in_(ids)).distinct()
    }
    for row in existing:
        # ``notes`` is free text the owner typed and no writer derives; a
        # whitespace-only note is not a record worth blocking an edit over.
        if row.notes is not None and row.notes.strip():
            holding.add(row.id)
    return holding




def _no_row_is_reattributed(existing, template) -> "frozenset[int]":
    """Return the ids of rows an account move would re-file: NONE, for a transaction.

    The transaction engine's answer to the question
    :func:`_recurrence_common.classify_maintain_work` asks both engines
    (``reattributed``: which rows hold records the definition's account move
    would re-attribute), and the answer is the empty set since plan step
    ``credit_card:CC-5-2`` (ruling **R-CC36**).

    **Why it was ever non-empty, and why it is not now.**  Through plan step
    ``credit_card:CC-5-1`` the schema bound a purchase's account to its
    parent's (``fk_transaction_entries_parent_account``, ON UPDATE CASCADE),
    so moving a row dragged every purchase onto the new account and
    invalidated the account-scoped clearing link each carried
    (``fk_transaction_entries_reconciled_by``); a row holding records was
    therefore RETAINED where it was and reported (plan step R10-b).  Since
    ``CC-5-1`` a movement's account is its own (ruling **R-BAL76**; the key
    is dropped), and since ``CC-5-2`` every reader asks the movement's
    account rather than its row's (design 3.2's census).  So a moved row
    LEAVES its purchases where their money moved -- a `$60` card swipe stays
    on the card when its Groceries envelope moves from Checking to Second
    Checking, and each purchase's clearing link is scoped by the purchase's
    own account, which the move never touches.  The row's kept payment
    record (a reverted settle's un-dated covering movement) keeps its
    account when the row is next settled; only a NAMED tender moves it
    (ruling **R-CC42**, ``status_seam._covering._cover`` / ``_re_point``,
    which amended this ruling's re-point clause).  Nothing on a
    transaction row is re-filed by its account moving, so nothing is held
    back for it; a row holding records still RETAINS on the other shape (the
    rule no longer names its occurrence), which is
    :func:`_rows_holding_owner_records`' half and is unchanged.

    **The TRANSFER engine keeps its own non-empty answer**
    (``transfer_recurrence._rows_the_definition_reattributes``): its endpoint
    move carries both shadows AND their movements between accounts by hand
    (``transfer_service._endpoints._apply_endpoint_move``), so a settled
    leg's record WOULD be re-filed there.  The two engines differ here
    because their models do, which is exactly what :class:`MaintainActs`
    exists to name.

    Args:
        existing: The rows this pass is considering.  Unread.
        template: The TransactionTemplate.  Unread.

    Returns:
        The empty set.
    """
    del existing, template
    return frozenset()




def _apply_maintain_work(work, derived, template, scenario_id, projected_id):
    """Write one classified maintain pass, and reconcile what it moved.

    The only writer in the maintain path.  Order is load-bearing in one place:
    the ledger reconcile runs AFTER the flush, so each updated row's postings
    are reconciled against the amount and account the flush actually stored.

    Args:
        work: The :class:`~app.services._recurrence_common.MaintainWork`
            from :func:`~app.services._recurrence_common.classify_maintain_work`.
        derived: ``{pay_period_id: DerivedRowFields}`` for every period the
            rule names -- the single statement of what a generated row's
            definition says, consumed identically by the update and the create.
        template: The definition every written row is linked to.  Taken whole
            rather than as an id since plan step balance:X-au-d, because
            :class:`~app.services._recurrence_common.MaintainActs` gives both
            engines' writers one signature and a transfer's needs the template
            itself.  **Plan step ``pay_calendar:C13-a`` needs it whole for a
            second, independent reason**: a created row states its OWNER as
            well as its link, and the template is the one place both are
            known -- so passing the id would put one object's two fields in
            two parameters.  Either reason alone justifies the signature;
            removing one does not license reverting it.
        scenario_id: The scenario every created row is written into.
        projected_id: The ``Projected`` status id for created rows.  ``None``
            only when the rule was cleared, in which case *work.create_in* is
            empty and it is never read.

    Returns:
        ``(created, updated)`` -- the rows this pass added and the rows it
        brought into line.
    """
    # ``occurs_on`` is deliberately absent from this loop, and that is plan
    # step **R17**'s central claim: a row's OCCURRENCE is what it IS, not
    # something its definition re-derives per pass.  It is not in
    # ``DerivedRowFields``, so this splat cannot reach it, and a maintained row
    # therefore keeps the occurrence it was created for.  What a pass should do
    # when a rule EDIT moves the occurrence set out from under an existing row
    # is the question the skip-predicate leaf owns; baking the answer in here
    # would decide it silently.
    #
    # **The AMOUNT travels as one attribute since plan step X-au-k**, and this
    # loop is why that matters.  ``DerivedRowFields`` carries
    # ``amount_ownership``, so ``setattr`` here writes a row's whole ownership
    # or none of it -- where it used to write ``estimated_amount`` alone and
    # abort the entire template edit at flush against
    # ``ck_transactions_amount_ownership`` (finding **N-293**, closed there).
    #
    # **What it writes is a DECLARATION and never a figure** (plan step
    # X-au-e, the condition finding N-437 was closed under).  The field is
    # ``derived_ownership(AmountSourceEnum.TEMPLATE)``, one value rather than
    # a fork, so the splat cannot hand a row back to its owner -- there is no
    # arm here that states a price at all, which is the whole meaning of
    # "maintain" once a definition prices its own rows.  It carried
    # ``own(figure)`` unconditionally until X-au-k, then ``own`` OR ``derived``
    # on ``template_amount_service.owns_its_amount`` until X-au-e; a SILENT
    # hand-back was what the first would have become the moment a cutover
    # derived a row this pass can reach.
    #
    # The rows it can reach are narrower than the fetch:
    # ``_recurrence_common.classify_maintain_work`` routes an IMMUTABLE, an
    # OVERRIDDEN and a soft-DELETED row away from ``work.update`` before this
    # loop sees them, so a figure a human authored is never overwritten here.
    updated = []
    for row in work.update:
        for field, value in derived[row.occurs_on]._asdict().items():
            setattr(row, field, value)
        updated.append(row)

    created = []
    for create in work.create_in:
        txn = Transaction(
            **derived[create.occurs_on]._asdict(),
            # The OWNER sits here rather than in ``DerivedRowFields``, and the
            # loop above is exactly why (plan step ``pay_calendar:C13-a``): it
            # ``setattr``s every derived field onto an EXISTING row, and a row
            # does not change hands because its template was edited.  Same
            # argument as ``occurs_on``'s, stated at the top of this function.
            user_id=template.user_id,
            template_id=template.id,
            pay_period_id=create.period_id,
            occurs_on=create.occurs_on,
            scenario_id=scenario_id,
            status_id=projected_id,
            is_override=False,
            is_deleted=False,
        )
        db.session.add(txn)
        created.append(txn)

    # **This is now the ONLY path here that deletes**, and it is reached only
    # when the rule stopped naming the row's OCCURRENCE (plan step R17; it was
    # the row's PERIOD) AND the row carries nothing of the owner's -- no entry
    # at all (:func:`_rows_holding_owner_records`), so it has no posted leg to
    # reverse: a row's legs are its movements' since plan step
    # ``balance:X-bi-4a``, and a row still holding one could not be deleted
    # here, its key being NO ACTION (ruling **R-CC64**).  It reversed the
    # row's family first, a no-op for a row holding nothing, until plan task
    # plan#2557 deleted that door (ruling **plan#2564**: a movement's postings
    # are reversed by the one removal act that deletes it).
    for row in work.retire:
        db.session.delete(row)

    db.session.flush()

    # An updated row may have moved money: its amount can change with the
    # template's, and its account with it.  Reconciling to the STORED values is
    # what keeps the ledger equal to the rows without a second definition of
    # what those rows say.
    for row in updated:
        posting_service.sync_transaction_postings(row)
    return created, updated


def propagate_to_unruled_definition(template, rows) -> "list[int]":
    """Apply a RULE-LESS definition's edit to the rows it holds.

    **The maintain pass's decision, for the one shape that does not
    regenerate** -- the transaction twin of
    ``transfer_recurrence.propagate_to_unruled_template`` (plan step
    ``balance:X-bi-7a``, ruling **R-BAL20**).  A definition with no rule
    names no occurrence, so a regeneration would RETIRE every record-free row
    it holds (defect **D16**) and the route gates the sweep on "the
    definition IS or WAS recurring" instead; until this function existed its
    rows were reached by nothing but the bulk rename in
    ``definition_edit.apply_fields``, so a new
    category, account or type stayed on the Recurring page and never reached
    the grid.  Every row a rule-less definition holds is one of these: a
    one-off's placed rows (every one-off's since the family's cutover,
    ``X-bi-7d-2``) and the rows a CLEARED cadence left behind.

    **It RETAINS NOTHING since plan step ``credit_card:CC-5-2`` (ruling
    **R-CC36**), and so it decides nothing.**  Through ``CC-5-1`` it made the
    regular pass's one account-move refusal in the shared place
    (``_recurrence_common.classify_unruled_work``, over this engine's
    :func:`_rows_holding_owner_records` and the rows whose account the
    definition moved): a row holding purchases was RETAINED where it was,
    because the parent-account key dragged its purchases with it.  That key
    is gone and a moved row leaves its purchases where their money moved
    (:func:`_no_row_is_reattributed`), so the transaction twin has no arm
    left to decide -- and asking the classifier anyway would issue the
    owner-records query (one ``SELECT`` per rule-less edit) to intersect it
    with an empty set.  The transfer twin still decides there; the two
    engines differ here because their models do.
    Every row takes :func:`~._amounts._derive_unruled_fields` whole --
    the definition's account, name, category, type and TEMPLATE ownership,
    with the row's own due date -- through the same ``setattr`` splat
    :func:`_apply_maintain_work` uses, and its postings are reconciled
    after the flush for the same reason: an updated row may have moved
    money's category or account.  Ownership is written as a declaration and
    never a figure (plan step X-au-e).  The caller selects rows that are not
    overridden, so a figure the owner typed at the grid (OWN with the flag
    beside it) is never reached here; an OWN figure written WITHOUT the flag
    -- ``salary_profile_service``'s archive freeze -- is reached exactly as
    the regular pass reaches it, which is that pass's standing shape and not
    this twin's to decide.

    Args:
        template: The updated rule-less TransactionTemplate, its new field
            values already applied.
        rows: Its live, rule-owned rows -- Projected, not overridden and not
            soft-deleted, which the caller selects
            (``definition_edit.unruled_live_rows``).

    Returns:
        The ids this pass RETAINED, in the shape the transfer twin's route
        reports: EMPTY for a transaction definition since ruling **R-CC36**.
    """
    for row in rows:
        for field, value in _derive_unruled_fields(template, row)._asdict().items():
            setattr(row, field, value)
    db.session.flush()
    for row in rows:
        posting_service.sync_transaction_postings(row)
    return []


#: The acts a regeneration performs that are THIS engine's own -- what
#: :class:`~._pass.MaintainActs` names, and everything about a regeneration
#: that a transaction and a transfer do not share.  The body that performs them
#: is :func:`~._pass.regenerate_definition`, ONE function for both engines
#: since plan step balance:X-au-d -- which is where this module's own copy of
#: it went.
_PASS = MaintainActs(
    PassReporting(
        logger, "Blocked cross-user recurrence regeneration",
        EVT_RECURRENCE_REGENERATED, "Recurrence regenerated for template",
    ),
    _selector, _derive_row_fields, _rows_holding_owner_records,
    _no_row_is_reattributed, _apply_maintain_work,
)
