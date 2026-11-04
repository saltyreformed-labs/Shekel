"""
Shekel Budget App -- Editing a transaction DEFINITION, and its rows following

**The ONE act that applies an edit to a ``budget.transaction_templates`` row
and brings the rows it names into line.**  A pure move (plan step
``balance:X-bi-7b``, leaf 7b-2) of two route-layer bodies into the service
tier, so that a SECOND door -- the grid popover, which lands a one-off's
name, category and flags on its definition under ruling **R-BAL23** -- can
call the same act rather than be written against the first door's body.  A
door written against another door's BODY inherits its write and none of its
refusals; :mod:`app.services.definition_delete` records that lesson for the
delete, and this module is its twin for the edit.

**Two halves, and only the second is a rule-less definition's.**

1. :func:`apply_fields` writes the allowlisted fields onto the definition
   and propagates a changed NAME onto EVERY row it names -- settled,
   overridden and soft-deleted rows included.  Moved whole from
   ``routes/templates/crud._apply_fields_and_propagate_rename``.
2. :func:`propagate_to_unruled_rows` is how a RULE-LESS definition's other
   fields -- account, category, type, the TEMPLATE ownership declaration --
   reach the rows it holds, which no pass regenerates (a regeneration with no
   rule names no occurrence and would RETIRE every record-free row, defect
   **D16**).  Which rows are the definition's to speak to is decided here
   (:func:`unruled_live_rows`, moved whole from
   ``routes/templates/_instances.non_repeating_live_rows``) and what to
   write is the engine's
   (``recurrence_engine.propagate_to_unruled_definition``), the split the
   transfer twin drew after an adversarial review of plan step R10-b
   measured a route applying a definition unconditionally.  The FLAGS need
   no propagation at all: ``Transaction.tracks_purchases`` and
   ``visible_to_companion`` read the definition for every row it names.

Boundary discipline (``CLAUDE.md`` Architecture): ORM rows and plain data
in, plain data out, no Flask import.  It MUTATES and does NOT commit -- the
caller owns the unit of work, and the FLASHING a door does with the retained
ids stays in the route (``routes/templates/_instances``).
"""

from app.extensions import db
from app.models.transaction import Transaction
from app.services import recurrence_engine
from app.utils.balance_predicates import is_projected_clause


# The definition's OWN fields an edit may write back via ``setattr``, scoped
# to exactly the keys ``TemplateUpdateSchema`` can deserialize.
#
# ``is_active`` and ``sort_order`` are deliberately absent: neither is a field
# on the Template schema chain, so the schema's ``unknown = EXCLUDE`` can never
# surface them here.  ``is_active`` is owned by the dedicated archive /
# unarchive routes, which pair the flag flip with the projected-row
# soft-delete an edit does not perform -- allowlisting it would invite a
# future schema field to silently archive a definition without that cleanup.
#
# The amount (the form's ``amount``) is absent for the same shape of reason since plan step
# X-au-a: the amount is no longer a bare column but a dated SERIES, and
# ``template_amount_service`` holds its two write doors (``set_amount``
# appends a version; ``restate_in_effect`` corrects the one a date reads,
# **R-BAL29**).  A setattr here would write an attribute no column holds
# (plan step X-bp dropped ``default_amount``), so the save would state no
# price and say nothing.
EDITABLE_FIELDS = frozenset({
    "name", "category_id", "transaction_type_id",
    "account_id", "is_envelope", "companion_visible",
})


def apply_fields(template, data) -> None:
    """Apply allowlisted field updates to *template*, propagating a rename.

    Writes every :data:`EDITABLE_FIELDS` key present in *data* onto
    *template*, then propagates a changed name to EVERY existing
    :class:`~app.models.transaction.Transaction` that names this definition
    -- including soft-deleted ones.

    The rename propagation is load-bearing: ``regenerate_for_template``
    only rewrites non-override rows on or after ``effective_from``, and
    :func:`propagate_to_unruled_rows` reaches only live ones, so historic
    rows, overrides and settled rows would otherwise keep the old label and
    desync every view that renders ``txn.name`` directly (calendar CSV
    export, calendar, companion card, edit form header).  Soft-deleted rows
    are renamed too, so a row later restored -- by the recurrence-conflict
    chooser's "use" action or by carry-forward -- surfaces with the current
    name rather than a stale one.  Neither partial unique generation index
    reads ``name``, so a bulk name update cannot trip a constraint.
    Definition ownership is verified by the caller, so ``template_id`` alone
    scopes the update to the current user.

    **The bulk UPDATE autoflushes whatever is dirty**, which is why the
    template edit door states the amount BEFORE calling this: stating it
    afterwards would leave a second dirty write for the commit and bump the
    optimistic-lock counter twice for one edit.

    Args:
        template: The :class:`~app.models.transaction_template.TransactionTemplate`
            being edited.
        data: The schema-loaded payload; keys outside
            :data:`EDITABLE_FIELDS` are ignored.
    """
    old_name = template.name
    for field, value in data.items():
        if field in EDITABLE_FIELDS:
            setattr(template, field, value)

    if template.name != old_name:
        db.session.query(Transaction).filter(
            Transaction.template_id == template.id,
        ).update({"name": template.name}, synchronize_session="fetch")


def unruled_live_rows(template) -> list[Transaction]:
    """Return the rows a RULE-LESS definition's edit may still rewrite.

    Projected, not hand-edited, not soft-deleted -- the same three conditions
    ``_recurrence_common.classify_maintain_work`` uses to decide which rows a
    recurring definition's regeneration may rewrite.  A settled row is
    immutable history and an overridden one is a deliberate per-instance
    change; neither follows the definition, here or there.  A one-off's
    ONLY row carries no flag once the popover restates its price in place
    (**R-BAL29**; a row the interim between leaves 7b-1 and 7b-2 detached
    is re-attached by its next typed figure, **R-BAL37**), so it is always
    selected; a row of a MANY-row definition the owner re-priced is the
    owner's (**R-BAL43**) and is skipped like any overridden row, so a
    later category edit on the definition does not reach it.

    Args:
        template: The rule-less
            :class:`~app.models.transaction_template.TransactionTemplate`.

    Returns:
        The matching :class:`~app.models.transaction.Transaction` rows,
        oldest first.  Normally exactly one -- a one-off's placed row -- but
        a bank-born envelope holds one per paycheck (**R-BAL24**) and a
        definition whose recurrence was CLEARED keeps whatever survived that
        sweep, and this must be correct for all three.
    """
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.template_id == template.id,
            is_projected_clause(Transaction),
            Transaction.is_override.is_(False),
            Transaction.is_deleted.is_(False),
        )
        .order_by(Transaction.id)
        .all()
    )


def propagate_to_unruled_rows(template) -> list[int]:
    """Push a RULE-LESS definition's edited fields onto the rows it holds.

    The counterpart of regeneration for the one shape that does not
    regenerate, and the reason a rule-less definition's category, account,
    type or ownership declaration reaches the grid at all: :func:`apply_fields`
    carries the NAME to every row of every definition, and nothing carried
    the rest.  The engine writes every row it is handed
    (``recurrence_engine.propagate_to_unruled_definition``; through plan step
    ``credit_card:CC-5-1`` it RETAINED a row whose account the definition
    moved and which held the owner's records, an arm ruling **R-CC36**
    retired -- a moved row leaves its purchases where their money moved) and
    answers the rows it left, empty for a transaction definition; the caller
    reports them.

    Args:
        template: The updated rule-less
            :class:`~app.models.transaction_template.TransactionTemplate`,
            its new field values already applied.

    Returns:
        The ids the pass RETAINED, for the door to report.
    """
    return recurrence_engine.propagate_to_unruled_definition(
        template, unruled_live_rows(template),
    )


__all__ = [
    "EDITABLE_FIELDS",
    "apply_fields",
    "propagate_to_unruled_rows",
    "unruled_live_rows",
]
