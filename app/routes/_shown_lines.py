"""
Shekel Budget App -- What a page NAMED before the press

One translation, made identically by every door a page with ONE set of
withdrawal captions posts to: the transaction popover's Save, Paid / Received and
Delete (``routes/transactions/mutations.py``), its Credit and Undo CC
(``routes/transactions/credit.py``), the transfer
popover's Save and Paid (``routes/transfers/mutations.py``), the purchase
list's X and edit form (``routes/entries.py``), and carry-forward's Confirm,
one set for its whole batch (``routes/transactions/carry_forward.py``, leaf
5c-2c-2).  The reconcile panel posts one
set PER ROW (``shown_lines-<row id>``) and reads them itself
(``routes.accounts.reconcile._submitted_shown_lines``, plan step
``credit_card:CC-5-4a-5c-1``), and since leaf 5c-2c-1 one value PER SHARED
MATCH beside them -- a match naming the kept payments of several rows,
warned under each, posting ``"<line ids>;<row ids>"`` under
``reconcile_service.SharedMatch.FIELD`` (``shared_lines``, read by
``routes.accounts.reconcile._submitted_shared_lines``, ruling **R-CC135**).
It reads them itself because the save names only the lines under the rows the
owner ticked, and a shared match's lines only when all of its rows were
ticked (``reconcile_service.record_reconciliation``,
``NamedLines.for_ticks``).

**The act that takes a movement off the books asks what the owner was SHOWN**
(plan step ``credit_card:CC-5-4a-5``, rulings **R-CC81** / **R-CC127**): the
bank lines the page's withdrawal captions named, or the ruling that lets a
button stay silent.  The popover posts those lines back as ``shown_lines`` --
EMPTY when its captions name none, and that emptiness is itself a statement:
*"A button with no warning sends nothing"*.  A request WITHOUT the field came
from a surface that renders no caption at all, and what that means is the
DOOR's to say: Mark Paid's is silence under ruling **R-CC56** for the owner's
one-click (the grid's cell and its phone card) and, for a companion's, a press
that may free no line (ruling **R-CC130**,
``routes.transactions._press._mark_paid_posted``); every other door's is that
it named nothing.

**At the transaction and transfer doors only a full-edit popover posts the
field, so there its presence also says WHERE a refusal is answered**: a
popover's out-of-date press redraws the popover (ruling **R-CC128**), and any
other surface's refusal is its ordinary error.  The purchase list's doors
post it from the list itself and read only :attr:`Posted.shown`: each answers
a refusal by drawing that list again as it is now
(``routes.entries._refused_entry_response``), but for a purchase or row that
is gone, which is "not found".

Pure: no Flask import, no session.  A route-tier helper because the facts it
reads are about the FORM, the shape of :mod:`app.routes._typed_figure`.
"""

from typing import NamedTuple

from app.services.match_press import Shown, Silent

#: The form field a full-edit popover posts its captions' bank lines under,
#: declared on every schema those presses load
#: (:class:`~app.schemas.validation._helpers.ShownIds`).
SHOWN_LINES_FIELD = "shown_lines"


class Posted(NamedTuple):
    """What a press's request said about the page it was made from.

    Attributes:
        shown: What the removal act is told the page named
            (:class:`~app.services.match_press.Shown`), or what lets the
            door stay silent.
        from_popover: Whether the request posted the field -- which, of the
            transaction and transfer doors' surfaces, only a full-edit popover
            renders -- so a refusal as out of date redraws that popover
            (ruling **R-CC128**).  The purchase list's doors do not read it.
    """

    shown: Shown | Silent
    from_popover: bool


def read_posted(data: dict, *, absent: Shown | Silent) -> Posted:
    """Return what the press's page named, taking the field out of *data*.

    Args:
        data: The schema-loaded payload.  The field is REMOVED when present,
            because it names no column: the transaction PATCH ``setattr``s
            every key it does not recognise, and the transfer door hands its
            payload to the service as keywords.
        absent: What the door means when its request carries no field --
            :data:`~app.services.match_press.MARK_PAID` at Mark Paid,
            :data:`~app.services.match_press.NOTHING_SHOWN` elsewhere.

    Returns:
        The :class:`Posted`: ``Shown`` over the posted ids -- none, for the
        empty value ``ShownIds`` loads as ``None`` -- from a page that posts
        the field, or *absent* from any other surface.
    """
    if SHOWN_LINES_FIELD not in data:
        return Posted(shown=absent, from_popover=False)
    return Posted(
        shown=Shown(data.pop(SHOWN_LINES_FIELD) or frozenset()),
        from_popover=True,
    )
