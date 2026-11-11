"""The PATCH handler's FIELD-APPLICATION step.

**Split out of :mod:`.mutations` at plan step ``balance:X-bi-7a``**, whose
re-key of the override flip pushed that module to ``max-module-lines``; the
cut is the one X-au-j made for :mod:`._gates`, one step later in the same
handler: the gates run BEFORE this step, and what follows it
(``apply_requested_status`` and the commit) stays with the handler, as the
posting reconcile between them did until plan task plan#3097.

**Since plan step ``balance:X-bi-7b`` (leaf 7b-2) this step forks on the
row's SHAPE.**  A one-off is a rule-less definition plus its placed row
(ruling **R-BAL20**), and the grid popover is its whole lifecycle
(**R-BAL23**): the name, category and flags it posts are the DEFINITION's
and land there through the one act the template edit form calls
(:mod:`app.services.definition_edit`, ruling **R-BAL36**); a typed figure
corrects the definition's price IN PLACE and never detaches the row
(**R-BAL29**, **R-BAL37**); a period move RE-PLACES the row on the target
paycheck's start unless the owner had stated a day (**R-BAL33**); and a
moved due date carries ``occurs_on`` with it (**R-BAL25**).  A RECURRING
definition's row keeps every act it had: a typed figure or a period move
detaches it (OWN, ``is_override``), because that is a statement about one
occurrence among many.  A link-less row -- a CC payback, the one shape this
door still reaches with no definition since the family's cutover -- keeps
its own branch, frozen.

Boundary discipline: this writes the loaded row from the request's
already-schema-loaded ``data``; it issues no query of its own beyond the
lazy loads the row's accessors make and the definition edit's own, and it
does not commit.
"""

from app.services import definition_delete, definition_edit, transaction_service
from app.services.amount_ownership import state_own_amount
from app.services.one_off import (
    another_row_answers,
    due_date_after_move,
    holds_a_row_in,
    restate_price,
    state_due_date,
)

from app.routes.transactions._helpers import _error_transaction_response


# The PATCH fields the status seam writes, so the generic ``setattr`` loop in
# :func:`_apply_regular_update` must NOT.  They are one fact -- a row is settled
# if and only if it carries the day its money moved -- and
# ``status_seam.apply_status_change`` writes them in a single statement after
# verifying the transition and expiring the ``status`` relationship.  A bare
# ``setattr`` would skip all of that, and for ``settled_on`` it would make the
# loop a SECOND writer of the column the seam is the single door to: exactly
# finding **N-183** (which ``update_transfer`` shipped and X-f1b closed), about
# to be re-run on the transaction side by this step's own edit door (finding
# **N-185**).  Naming the pair here is what keeps the loop from growing a third
# member silently.
#: The fields a SEAM owns, excluded from the field-application loop so it
#: cannot become a second writer of any of them.  ``settled_amount``
#: joined at plan step X-au-c3: it states WHAT MOVED, so a bare ``setattr``
#: could book money on a row that never settled and could do it beside a basis
#: saying something else.  It reaches the record only through
#: ``apply_requested_status``, which hands it to the settle verb or refuses it
#: with a designed 400 when no settle is happening.
#:
#: ``estimated_amount`` joined at plan step **X-au-k**, and it is the one
#: member this set no longer has to be TRUSTED about: the column is read-only
#: on the model now, so a ``setattr`` that reached it raises ``AttributeError``
#: rather than half-writing the amount-ownership pair.  It stays named here
#: because the loop must SKIP it deliberately rather than crash, and because a
#: reader asking which fields a seam owns should find all five in one place.
#:
#: ``tender_account_id`` joined at plan step ``credit_card:CC-5-3``: it names
#: WHICH ACCOUNT the money moved through -- the covering movement's, never a
#: column of the row -- so a ``setattr`` would land it nowhere the model
#: declares, and it reaches the record only through
#: ``apply_requested_status`` beside the figure it belongs with.
_SEAM_OWNED_FIELDS = frozenset({
    "status_id", "settled_on", "settled_amount", "estimated_amount",
    "tender_account_id",
})

#: The PATCH fields that are a placed row's DEFINITION's (ruling **R-BAL23**):
#: what the item is called, what it is, and its two flags.  Taken out of the
#: payload before the row loop for such a row and landed on the definition
#: through :func:`definition_edit.apply_fields`, so the row's copies of the
#: first two are written by the propagation that brings every row of the
#: definition into line, and the flags -- which the row reads off its
#: definition -- are written nowhere on the row at all.  DERIVED from
#: :data:`definition_edit.EDITABLE_FIELDS` rather than spelled beside it, so
#: a field this door pops is one that act ``apply_fields`` writes: the two it
#: leaves out are the account and the type, which the popover does not
#: offer.
_ITEM_FIELDS = definition_edit.EDITABLE_FIELDS - {
    "account_id", "transaction_type_id",
}


def _re_files_the_row(txn, data) -> bool:
    """Whether this PATCH moves a placed row to ANOTHER grid row.

    The budget grid groups a rule-less definition's rows by
    ``(category_id, name)`` (ruling **R-BAL34**), so a placed row renamed or
    re-categorised at the popover leaves the row it was drawn in -- which an
    in-place cell swap cannot express, exactly as a period move cannot.  The
    handler asks this BEFORE :func:`_apply_field_updates` lands the new
    values, and triggers ``gridRefresh`` when it answers ``True``.  Compared
    against the ROW's current values because those are the grid's key.

    Args:
        txn: The row being edited.
        data: The schema-loaded PATCH payload, before the field write.

    Returns:
        ``True`` for a placed row whose submitted name or category differs
        from what the grid currently files it under; ``False`` for every
        other row, whose grid row a PATCH does not move.
    """
    if not txn.is_placed:
        return False
    return (
        ("name" in data and data["name"] != txn.name)
        or ("category_id" in data and data["category_id"] != txn.category_id)
    )


def _place_the_date(
    txn, data, *, period_changed, target_period, source_start,
):
    """Act 2 of :func:`_apply_field_updates`: a placed row's date, guarded.

    The date is the owner's typed one, else the row's; a period move then
    RE-PLACES it through ``one_off.due_date_after_move`` (ruling
    **R-BAL33**), and a changed date is written through
    ``one_off.state_due_date`` (**R-BAL25**), the one writer that carries
    ``occurs_on`` with it.  The two refusals are the ones act 2 of
    :func:`_apply_field_updates` names, asked in the order the row would
    meet the index: the target paycheck first (``one_off.holds_a_row_in``),
    then the day (``one_off.another_row_answers``).

    Args:
        txn: The placed row being edited.
        data: The PATCH payload after act 0, read for ``due_date``.
        period_changed: Whether this payload moves the row.
        target_period: The paycheck the row moves to; ``None`` unless
            *period_changed*.
        source_start: The paycheck the row is leaving, its start; ``None``
            unless *period_changed*.

    Returns:
        A designed 400 response tuple, or ``None`` when the date was placed.
    """
    if period_changed and holds_a_row_in(
        txn.template_id, txn.scenario_id, target_period.period_id,
        except_row_id=txn.id,
    ):
        return _error_transaction_response(
            txn.id,
            "That paycheck already holds this item's row, and an item "
            "holds one row per paycheck. Record into that row instead.",
        )
    due = data.get("due_date", txn.due_date)
    if period_changed:
        due = due_date_after_move(
            due, source_start=source_start, target=target_period,
        )
    if due == txn.due_date:
        return None
    if another_row_answers(
        txn.template_id, txn.scenario_id, due, except_row_id=txn.id,
    ):
        return _error_transaction_response(
            txn.id,
            "Another row of this item is already due that day, so "
            "this one cannot be. Pick a different day.",
        )
    state_due_date(txn, due)
    return None


def _apply_field_updates(
    txn, data, *, amount_authored, period_changed, target_period,
):
    """Write the submitted fields onto *txn*, refusing what may not be written.

    The FIELD half of :func:`_apply_regular_update`, extracted so the handler
    keeps one exit per concern rather than growing a branch per rule.  EIGHT
    acts, numbered 0 to 7, and the order is load-bearing.  **This enumeration
    once said "three" and listed three while the body performed four** (found
    by X-au-j's adversarial review, in the one helper whose whole discipline
    is that its order matters), so every act is numbered below; leaf 7b-2 of
    ``X-bi-7b`` added the pop in act 0 and acts 2 and 4, and leaf 7b-3 split
    the figure act in two around act 4 (acts 3 and 5), each marked NEW.

    0. **The reads the later acts need, made BEFORE the loop.**  ``recurs``
       and ``is_placed`` may lazy-load the template, and a load after the
       loop autoflushes the moved period with the flag still False (act 5);
       the source paycheck's start is read off ``txn.pay_period`` while the
       foreign key still names it (the same column
       ``render_transaction_cell`` reads for the due caption, so the
       response's own load serves it from the identity map).  NEW: for a
       placed row the ITEM fields (:data:`_ITEM_FIELDS`) are taken out of
       ``data`` here, so the row loop never writes a definition's field
       onto the row (nor, until plan task plan#3097, did the handler's
       posting pre-filter see a ``category_id`` the propagation had already
       reconciled).
    1. the ``setattr`` loop over the ROW's fields.  ``status_id`` and
       ``settled_on`` are BOTH excluded and routed through the status verb
       instead: a bare ``setattr`` would assign the column but skip the
       transition check, the settle-day stamp/clear, and the
       status-relationship expire that ``status_seam.apply_status_change``
       owns -- and for ``settled_on`` it would make this loop a SECOND writer
       of a column the seam is the single door to (finding **N-185**, the
       re-run of N-183 on the transaction side).  A placed row's ``due_date``
       is excluded too: act 2 writes it.
    2. **NEW -- a placed row's DATE, through the one writer**
       (``one_off.state_due_date``, ruling **R-BAL25**: the row answers its
       own due date, so ``occurs_on`` moves with it).  The date is the
       owner's typed one, else the row's; a period move then RE-PLACES it
       (``one_off.due_date_after_move``, **R-BAL33**): the target
       paycheck's start unless the date was not the source paycheck's start,
       an owner-stated day read by position.  **Two refusals guard it**
       (leaf 7b-3, found by adversarial review at this door): a period move
       into a paycheck that already holds a row of the definition
       (``one_off.holds_a_row_in``, R-BAL24's one row per paycheck), and a
       day another row of the definition already answers
       (``one_off.another_row_answers``, the occurrence index's own
       predicate) -- each a designed 400 where the index alone gave a bare
       *Invalid reference*.  Before act 3 and act 4 because both read the
       row's date -- the restate corrects the version it reads, and the
       propagation re-declares the row at it.
    3. **NEW -- a placed row's typed figure, the ONLY-row half**
       (rulings **R-BAL21** / **R-BAL29**; the fork is **R-BAL43**'s):
       ``one_off.restate_price`` corrects the version the row's due date
       reads, in place, and declares the row priced by it with no flag --
       which is also what re-attaches a row the interim between leaves 7b-1
       and 7b-2 left OWN (**R-BAL37**).  BEFORE act 4, and an adversarial
       review of 7b-2 is why: the propagation there selects rows that are
       not overridden, so a residue row re-attached here is reached by it in
       the same request (its category follows); and the definition's amount
       and fields then flush together, so its optimistic-lock counter bumps
       once per edit rather than twice -- the same reason the template edit
       form states the amount BEFORE ``apply_fields``.
    4. **NEW -- the DEFINITION's fields** (:mod:`app.services.definition_edit`,
       rulings **R-BAL23** / **R-BAL36**): the item fields taken out at act 0
       are applied to ``txn.template`` -- a changed name reaches every row of
       the definition through the bulk rename -- and the rows it holds are
       brought into line, the row's own category and TEMPLATE ownership
       included (their postings were reconciled with them until plan task
       plan#3097).  The flags need no
       propagation: the row reads them off its definition.  A row the
       interim detached (``is_override``) and NOT re-priced in this request
       is skipped by that propagation, as any overridden row is (its category
       waits for its next typed figure, or the cutover).  **Here, BEFORE the
       status arm, because the flags govern the settle**: a save that unticks
       *Track individual purchases* beside Status = Paid settles the row on
       its own figure (act 6's own sentence), which needs the definition's
       flag written first.  A PATCH that REVERTS a finalised placed row and
       re-categorises it in one request -- the "revert and correct" edit
       ``state_machine.finalised_edit_rejection`` admits; the card disables
       the select on a locked row, any HTTP client posts the pair -- met the
       other side of that order until plan step ``balance:X-bi-7c``: the
       propagation ran while the row was still locked and skipped it, so
       the definition took the category and the row kept the old one, and
       the next settle posted to it.  Found by 7b-2's adversarial review and
       left as crafted-only; the suite's own posting-lifecycle cases posted
       the pair once their rows became placed, and the handler now applies a
       lock-lifting transition before this step (*unlock, edit, lock*,
       ruling **R-BAL58**; it reconciled the ledger after it too until plan
       task plan#3097), so the propagation finds the row projected.
    5. **NEW -- the figure's OTHER half.**  A placed row whose definition
       holds ANOTHER row (a bank-born envelope's, one per paycheck) takes
       the figure as its OWN with the flag beside it, one occurrence among
       many (**R-BAL43**), exactly as a RECURRING row's typed figure makes
       that row its OWN through ``amount_ownership.state_own_amount``
       (shipped at X-au-c2b, restated at X-au-k: a hand-priced row OWNS its
       figure, so storing it RELEASES the relation that priced it, one act
       over one attribute); a link-less row keeps the OWN write it always
       had.  **AFTER act 4**, and an adversarial review of 7b-3 is why: an
       OWN row is one the propagation skips, so a category typed beside the
       figure on a sibling's card reached the definition and every other
       row but not the row it was typed on.  A sibling that was ALREADY the
       owner's from an earlier figure is skipped by act 4 as any overridden
       row is -- the engine's rule, and reported at the leaf.
    6. ``is_override``, which sits with the field writes and ABOVE both the
       refusal below and the status work: act 7 FLUSHES, so a flag written
       after it is written after the UPDATE it belongs in -- which for a
       period move leaves the row inside the generation index's partial
       predicate (``is_override = FALSE``) while its period is already the
       new one.  **Plan step R17 narrowed what that can collide with but
       did not remove it**: the dated index is keyed
       ``(template, scenario, occurs_on)`` and a move of a RECURRING row
       does not touch ``occurs_on``, so a DATED row no longer collides with
       whatever the engine put in the target period; an UNDATED
       template-linked row is still keyed on its paycheck by
       ``idx_transactions_template_scenario_undated``, so there the ordering
       is exactly as load-bearing as it was.  (For a PLACED row act 4 has
       already flushed, and correctly: a move never flags such a row, and a
       figure flags it at act 5, before this.)  **The flag says ONE thing
       since plan step X-au-h: this row is the OWNER's, not the rule's** --
       and since leaf 7b-2 a MOVE makes it ONLY where the definition RECURS
       (**R-BAL28**'s stated end): a rule-less definition runs no pass to
       keep off the row, and a flag there would only hide the row from the
       propagation of act 4 and from a rule added later (**R-BAL25**).  Both
       acts were presence tests before -- the amount's is finding **N-248**,
       and the period's had the identical shape, because the popover renders
       a period dropdown on every row and posts it whether or not it was
       touched; ``period_changed`` is computed by the caller BEFORE the loop
       rewrites ``pay_period_id``.
    7. the derived-amount refusal, asked AFTER the loop so ``tracks_purchases``
       reads the RESULTING row: unchecking "Track individual purchases" in the
       same save legitimately gives the row its own amount back.  **It reads
       a LAZY relationship and therefore autoflushes**, which is why this
       whole helper is called from inside the caller's exception net rather
       than above it.

    **The refusal is ruling R-FF's**, the same sentence the reconcile panel
    already obeys ("a tick is correctable exactly when the settle verb takes its
    MANUAL branch"), applied to the second door that offers an amount box.  An
    envelope carrying purchases settles at ``sum(entries)``, so a figure typed
    beside it is not a correction the app can honour: it would be written here,
    overwritten by the settle, and never mentioned.  The popover no longer
    renders the input for such a row (``amount_correctable``), so this is the
    crafted-request and stale-form backstop.  Only a REAL figure is refused: an
    empty box loads as an explicit ``None`` (the field is ``allow_none``), which
    states no amount at all.  **The "Paid from" account takes the same
    refusal on the same row** (plan step ``credit_card:CC-5-3``): such a row
    records its money as its purchases, each on its own account, so there is
    no ONE payment to book elsewhere -- the settle verb's entries branch would
    ignore the tender as it ignores the figure, and the popover renders no
    picker for the row (the same ``amount_correctable`` gate).

    A refused call has already staged the ``setattr`` loop's mutations;
    ``_error_transaction_response`` rolls them back, exactly as the settle-day
    refusal in the caller already relies on.

    Args:
        txn: The Transaction being edited.
        data: The schema-loaded PATCH payload, with the rendered-figure
            companion already removed by the caller.  A placed row's item
            fields are REMOVED from it here (act 0).
        amount_authored: Whether a HUMAN typed the ``estimated_amount`` this
            payload carries (ruling **R-JR**).  Decides whether the row takes
            ownership of the figure (a recurring or link-less row) or its
            definition is restated (a placed row).
        period_changed: Whether this payload MOVES the row to another period.
            Passed in because the caller computes it before the loop below
            rewrites ``pay_period_id``, at which point it can no longer be
            asked.
        target_period: The paycheck the row moves to, as the owner's
            calendar derived it for the FK probe, or ``None`` when the
            payload names none.  Never ``None`` when *period_changed*.

    Returns:
        A designed 400 response tuple, or ``None`` when every field was
        written.
    """
    # Act 0.
    recurs = txn.recurs
    placed = txn.is_placed
    only_row = placed and definition_delete.is_last_row_of_its_definition(txn)
    source_start = (
        txn.pay_period.start_date if placed and period_changed else None
    )
    item = (
        {field: data.pop(field) for field in _ITEM_FIELDS if field in data}
        if placed else {}
    )

    # Act 1.
    for field, value in data.items():
        if field in _SEAM_OWNED_FIELDS or (placed and field == "due_date"):
            continue
        setattr(txn, field, value)

    # Act 2.
    if placed:
        refused = _place_the_date(
            txn, data, period_changed=period_changed,
            target_period=target_period, source_start=source_start,
        )
        if refused is not None:
            return refused

    # Act 3.  The value is never ``None`` here: ``estimated_amount`` is not
    # ``allow_none`` on any of the transaction schemas, so
    # ``_normalize_empty_inputs`` DROPS an empty box rather than loading it
    # as an explicit nothing.  **Gated on AUTHORSHIP rather than on the
    # field's PRESENCE since plan step X-au-h** (ruling **R-JR**): the
    # popover renders this box on every correctable row and an HTML form
    # posts every input it renders, so presence was true of a notes-only
    # save -- which took ownership of a figure nobody chose (finding
    # **N-248**).
    if amount_authored and only_row:
        restate_price(txn, data["estimated_amount"])

    # Act 4.
    if item:
        definition_edit.apply_fields(txn.template, item)
        definition_edit.propagate_to_unruled_rows(txn.template)

    # Act 5.
    if amount_authored and not only_row:
        state_own_amount(txn, data["estimated_amount"])
        if placed:
            txn.is_override = True

    # Act 6.
    if recurs and (amount_authored or period_changed):
        txn.is_override = True

    # Act 7.
    if (
        data.get("settled_amount") is not None
        and transaction_service.settles_from_entries(txn)
    ):
        return _error_transaction_response(
            txn.id,
            "This row's actual comes from the purchases recorded against it, "
            "so an amount typed here would be discarded. Record the purchase "
            "instead, or correct one that is already there.",
        )
    if (
        data.get("tender_account_id") is not None
        and transaction_service.settles_from_entries(txn)
    ):
        return _error_transaction_response(
            txn.id,
            "This row's money is recorded as its purchases, each on its own "
            "account, so a 'Paid from' account chosen here would be "
            "discarded. Pick the account on the purchase instead.",
        )
    return None
