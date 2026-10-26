"""
Shekel Budget App -- The two ACTS that move a row between R-FI's states.

Ruling **R-FI**'s two states as the two acts that put a row into them: a row
either STATES ITS OWN figure or it DECLARES the relation that prices it.

**It is the write half of :mod:`app.services.cash_ledger._amount_source`**,
which is the read half.  That module answers *where does this row's amount come
from* and never writes; this one moves a row between the two states and never
prices anything.  Splitting them by direction is why the read package can state
"no writes" as a boundary and mean it.

**What this module IS since plan step X-au-k, and what it stopped being.**  It
opened by calling itself *"the ONE writer of a row's amount-ownership pair"*,
and that was a CENSUS: the figure and ``amount_source_id`` were two
independently mapped columns, so any code could write one, the biconditional
CHECK caught the half-write only at FLUSH, and keeping callers away from that
failure meant re-counting the write sites every time a cutover grew the derived
population.  The claim is now a property of the MAPPING --
:attr:`app.models.transaction.Transaction.amount_ownership` is one attribute
over a value object that cannot hold the illegal shape -- so this module no
longer has to be the only writer to be safe.  What is left is the part a type
should not do: turning an :class:`~app.enums.AmountSourceEnum` member into the
``ref.amount_sources`` id that is its storage shape, and naming the two acts.

**The per-table dispatch is GONE, and that is the measure of the change.**  A
transaction stores an owned figure in ``estimated_amount`` and a transfer in
``amount``, so this module used to carry a ``{model: column name}`` registry
and a lookup to decide which column an act should write.  Both models now
expose the pair under the SAME attribute name, so there is nothing to dispatch
on: a THIRD table brought under the amount model needs no entry here, only the
composite on its own mapping.

Boundary discipline (``CLAUDE.md`` Architecture / B6-01): ORM rows in, nothing
out.  Mutates the rows it is given in place; issues no query, does not flush and
does not commit, so a caller's unit of work is unchanged.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import AmountSourceEnum
from app.models.amount_ownership import AmountOwnership


def state_own_amount(row, figure: Decimal) -> None:
    """Make *row* OWN *figure*: store it, and drop the relation that priced it.

    The act a door performs when a HUMAN authors a figure.  Rule 1 of ruling
    R-FI -- ``amount_source_id IS NULL`` beside a stored amount -- and the
    statement :func:`app.services.cash_ledger.amount_rule` reads back: *a row a
    human re-priced owns its figure because the write door CLEARS its source*.

    **Dropping the source is not a second write and cannot be forgotten.**
    ``amount_ownership`` is ONE attribute holding one of two shapes, so storing
    a figure IS releasing the relation; there is no second column left behind
    and no ordering between them.

    Idempotent: a row that already owns a figure is simply restated, which is
    what lets a door call this without first asking which state the row is in.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer` being re-priced.
        figure: The amount the row now states as its own.

    Raises:
        ValueError: When *figure* is ``None`` (from
            :meth:`~app.models.amount_ownership.AmountOwnership.own`).
    """
    row.amount_ownership = AmountOwnership.own(figure)


def derived_ownership(relation: AmountSourceEnum) -> AmountOwnership:
    """Return the ownership of a row PRICED BY *relation*, as a VALUE.

    **The enum-to-id translation, and the one statement of it** (plan step
    **X-au-d**).  :class:`~app.models.amount_ownership.AmountOwnership` stores
    the ``ref.amount_sources`` id because that is the storage shape and a
    model-layer type reads no cache; the vocabulary a caller writes in is the
    enum, and this is where the two meet.

    **It is a VALUE rather than an act because a born row states its ownership
    before it exists.**  :func:`declare_derived` below moves an existing row
    into this state and is written in terms of this function, so the two cannot
    come to translate the member differently.  The caller that needs the value
    is the recurrence engine: its
    :class:`~app.services.recurrence_engine._amounts.DerivedRowFields` carries
    a row's whole ownership as one field, splatted into a ``Transaction(...)``
    that has no row to mutate yet.

    Args:
        relation: Which :class:`~app.enums.AmountSourceEnum` relation prices
            the row.

    Returns:
        The :class:`~app.models.amount_ownership.AmountOwnership` to assign.

    Raises:
        KeyError: When *relation* is not a seeded member
            (``ref_cache.amount_source_id``).
    """
    return AmountOwnership.derived(ref_cache.amount_source_id(relation))


def declare_derived(row, relation: AmountSourceEnum) -> None:
    """Declare *row*'s amount DERIVED by *relation*, and empty its figure.

    The act a per-kind cutover performs when a row stops being priced by a
    stored copy: the row names the RELATION that prices it -- its recurring
    definition (a transfer shadow named its parent transfer until plan step
    ``balance:X-bi-6-4d-2`` deleted the shadows) -- and holds no figure at
    all, so a stale derived amount becomes unrepresentable rather than merely
    unlikely (ruling **R-FI**, plan step X-au-c1).

    **It names the RELATION and never the RULE**, which is ruling **R-FK**:
    whether a definition is salary-linked, or a parent transfer is a loan
    payment, is a property of the DEFINITION read live at price time, so a mode
    flip changes what the row is worth without rewriting the row.  That is why
    a transfer SHADOW took ``PARENT_TRANSFER`` whether or not the transfer was
    a loan payment, until plan step ``balance:X-bi-6-4d-2`` deleted the
    shadows, and why the loan-settings routes need no writer of their own.

    **The enum-to-id translation is :func:`derived_ownership`'s**, above, and
    this is the ACT over it: a caller holding a row assigns, a caller building
    one takes the value.  Splitting them that way is what lets the recurrence
    engine state a born row's ownership without a row to mutate, with one
    statement of what the member means (plan step **X-au-d**).

    Idempotent, and a caller leans on it: :func:`app.services.one_off.restate_price`
    declares a row that may never have left this state.  The transfer door
    leaned on it too, re-declaring both twin legs on every definition-driven
    amount write, until plan step ``balance:X-bi-6-4d-2`` deleted the twins.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer` being declared.
        relation: Which :class:`~app.enums.AmountSourceEnum` relation prices it.

    Raises:
        KeyError: When *relation* is not a seeded member
            (``ref_cache.amount_source_id``).
    """
    row.amount_ownership = derived_ownership(relation)


def owns_its_amount(row) -> bool:
    """Return whether *row* states its own figure rather than deriving it.

    The NULL test the ownership CHECK is written over, named once so a caller
    asks the question rather than restating the encoding.  ``True`` means rule
    1 prices this row; ``False`` means its ``amount_source_id`` names the
    relation that does.

    **It asks the SOURCE rather than the value object**, so a row whose
    ownership has not been stated at all -- both halves still empty, which is
    a row mid-construction and a state ``ck_transactions_amount_ownership``
    refuses to persist -- answers ``True`` here exactly as it did before plan
    step X-au-k, instead of raising on a ``None`` composite.

    **Nothing here reads ``is_override``, ``is_projected`` or ``is_deleted``**,
    which is finding **N-262**'s rule: those three say whether a row COUNTS and
    who last touched it, never who owns its figure.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer` to ask about.

    Returns:
        ``True`` when the row owns its amount.
    """
    return row.amount_source_id is None
