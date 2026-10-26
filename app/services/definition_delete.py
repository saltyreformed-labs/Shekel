"""
Shekel Budget App -- Permanently deleting a transaction DEFINITION

**The ONE act that removes a ``budget.transaction_templates`` row and the
rows it still names**, shared by the two doors that perform it: the
definition's own hard-delete (``routes/templates/crud.hard_delete_template``)
and the account hard-delete, which disposes of the rule-less definitions its
guards let through (``routes/accounts/crud.hard_delete_account``, plan step
``balance:X-bi-7a``).  The second door was written against the first's body
rather than calling it, and an adversarial review of that step reproduced
the state ruling **R-JE** (finding **N-440**) exists to refuse: a settled,
soft-deleted, TEMPLATE-priced row left with ``template_id NULL`` -- priced by
nothing, unrestorable by the cutover's downgrade -- which ``transactions.
template_id``'s ``ON DELETE SET NULL`` manufactured until the family's cutover
(plan step ``balance:X-bi-7d-2``) made the key ``RESTRICT``: a definition
with a surviving row is now undeletable by the database, so the order below
(rows first, then the definition) is what a successful delete requires.  One
body, so the two doors cannot part again.

**What the act is, in order.**  Every NON-SETTLED row the definition names is
deleted first, then the definition goes through the session, so
``amount_versions``' delete-orphan and the rule's cascade run.  **None of
those rows holds a payment or a purchase** (plan step
``credit_card:CC-5-4a-4``, rulings **R-CC54**, **R-CC64**): a row holding one
is history, which the definition door asks
(:func:`~app.utils.archive_helpers.template_holding_movements`) and archives
on, the account door's history arm archives on for its rule-less
definitions' live rows (the database refusing a hidden one holding one,
rulings **R-CC89**, **R-CC92**), and the row door removes through
:mod:`app.services.movement_removal` before it gets here -- and the database
refuses the delete otherwise (``fk_transaction_entries_transaction_id``, NO
ACTION).  Until that step the movements CASCADED with their rows, and this act
reversed each purchase's postings first so their legs would not strand; a row
holds a posting only through a dated purchase, so with no movement in scope
there is no posting to reverse, and the loop is deleted rather than kept as a
guard for a state the key refuses.  The delete is restricted to non-settled rows via
``Status.is_settled`` (CRIT-05 / E-22) so a race-window mark-done cannot
destroy Paid / Received history; **the two definition doors have already
asked :func:`~app.utils.archive_helpers.template_has_paid_history`** --
soft-deleted settled rows included -- and refused, which is what makes the
restriction a backstop rather than a filter that leaves a survivor.  The
ROW door (below) does not ask it, and correctly: it reaches here only for a
definition whose LAST row is the one being deleted, so the only settled row
that could be its history is the one the owner is deliberately removing,
already deleted through that door's own reversal of its postings.

**A ONE-OFF's definition goes with its LAST row** (plan step
``balance:X-bi-7b``, ruling **R-BAL23**; the row's delete door is the third
caller): a rule-less definition with no row defines nothing (**R-BAL27**), so
``transaction_service.delete_transaction`` asks
:func:`is_last_row_of_its_definition` and disposes of the definition through
the same act.  A definition holding OTHER rows -- a bank-born envelope's
paycheck rows (**R-BAL24**), a cleared cadence's survivors, a soft-deleted
one-off (still the definition's, 10.8) -- keeps them and stays.

Boundary discipline (``CLAUDE.md`` Architecture): an ORM row in, no Flask
import.  It MUTATES and does NOT commit -- the caller owns the unit of work.
"""

from app.extensions import db
from app.models.ref import Status
from app.models.transaction import Transaction
from app.services import posting_service


def rows_holding_purchase_postings(*scope):
    """Return the template rows matching *scope* that hold ledger postings.

    **A PROJECTED row can hold postings since plan step X-f3b** (ruling
    **R-FM**): a purchase whose bank posting day the owner recorded books its
    own balanced cash leg whatever its envelope's status is.  Every bulk
    statement over a definition's rows was written under the opposite premise
    -- "a Projected row has no postings, so a bulk archive / restore / delete
    cannot touch the ledger" -- and that premise is what fell.

    The narrowing is what keeps the cost honest: a template generates ~50 rows
    over the forward horizon and at most a handful can ever hold a purchase, so
    this returns the empty list with ONE indexed read in the ordinary case and
    the callers loop over nothing.  Moved here whole from
    ``routes/templates/crud`` at plan step ``balance:X-bi-7a``, with the
    delete that used it.  **The unarchive's restore is its one caller since
    plan step ``credit_card:CC-5-4a-4``**: the archive and this module's
    delete no longer reach a row holding a movement (rulings **R-CC63**,
    **R-CC54**), and a row holds a posting only through one, so both loops
    were deleted.  The restore keeps it although it finds nothing since that
    step, because a definition's hidden row holds no movement, and the
    DATABASE holds that: a movement cannot arrive under a hidden row (ruling
    **R-CC89**), a row cannot be hidden while it holds one (**R-CC92**) --
    in either order of a race too, because the arrival check locks the row
    (**R-CC96**) -- and the release's migration refuses to inherit one
    (**R-CC82**) --
    :mod:`app.deleted_row_infrastructure`.  Kept as the restore's own
    statement of what it re-posts rather than an argument that nothing could.

    Args:
        *scope: The SQLAlchemy clauses selecting the rows the bulk statement is
            about to touch -- built by the caller, so this reader can never
            select a different set from the statement it guards.

    Returns:
        The matching :class:`~app.models.transaction.Transaction` rows.
    """
    return (
        db.session.query(Transaction)
        .filter(*scope, posting_service.posted_purchase_exists_clause())
        .all()
    )


def is_last_row_of_its_definition(txn) -> bool:
    """Return whether deleting *txn* would leave a RULE-LESS definition with no row.

    The predicate the row's delete door and its dialog share (plan step
    ``balance:X-bi-7b``): TRUE for a one-off's only row, FALSE for a row that
    is not PLACED (``Transaction.is_placed``: a recurring definition's rows
    are the RULE's, soft-deleted as tombstones; a link-less row has nothing
    to dispose of), and for a row of a rule-less definition that holds any
    OTHER row.  **Soft-deleted rows
    count as rows**: a soft-deleted one-off is still its definition's (10.8),
    and disposing of the definition under it would leave a TEMPLATE-priced
    tombstone with ``template_id NULL`` -- ruling **R-JE**'s state, one door
    over.

    One indexed read (``idx_transactions_template``; the partial undated
    index cannot serve a predicate that must see soft-deleted and dated
    rows), issued only for a rule-less definition's row.

    Args:
        txn: The :class:`~app.models.transaction.Transaction` being deleted.

    Returns:
        Whether *txn*'s definition has no row but *txn*.
    """
    if not txn.is_placed:
        return False
    another = (
        db.session.query(Transaction.id)
        .filter(
            Transaction.template_id == txn.template_id,
            Transaction.id != txn.id,
        )
        .first()
    )
    return another is None


def permanently_delete_definition(template) -> None:
    """Delete *template* and every non-settled row it names, in that order.

    The module docstring carries the order and why each step is where it is.
    The caller has refused on :func:`~app.utils.archive_helpers.template_has_paid_history`
    and on a row holding a movement (and, at the definition's own door, on a
    standing merchant rule) BEFORE reaching here; the settled-row restriction
    below is the backstop for a caller that has not, and the movement keys
    are the database's own.

    Args:
        template: The :class:`~app.models.transaction_template.TransactionTemplate`
            to remove.  Its rows are selected by id, so it need not be loaded
            with them.
    """
    settled_status_ids = db.session.query(Status.id).filter(
        Status.is_settled.is_(True)
    ).scalar_subquery()
    db.session.query(Transaction).filter(
        Transaction.template_id == template.id,
        Transaction.status_id.notin_(settled_status_ids),
    ).delete(synchronize_session="fetch")
    db.session.delete(template)
