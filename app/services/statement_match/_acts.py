"""How an accepted act is READ whole, and the two questions asked of one.

Split out of :mod:`._release` at plan step ``credit_card:CC-5-4a-1`` when
that module crossed the 1,000-line bound (ruling **balance:R-IR**: the
session that breaks a module splits it, by SUBJECT).  The seam is the one
:func:`acts_of`'s own docstring drew at plan step ``bank_import:X-f6f``: an
act is only readable with BOTH its relations and the row each of them names,
and TWO readers need exactly that -- the register's accepted list
(:mod:`._accepted_view`) and the undo (:mod:`._release`).  What lives here is
what both share: the loader (:func:`acts_of`, :data:`_WHOLE_ACT`), the
invariant it narrows on (:data:`NAMES_A_BANK_LINE`), and the one derivation
both ask of a loaded act -- which rows it NAMES (:func:`named_rows`).
Nothing here writes.

Services-boundary discipline (``CLAUDE.md`` Architecture): reads only, ORM
rows out for the two folds that hold them, no Flask import.
"""

from __future__ import annotations

from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.statement_match import (
    StatementMatch,
    StatementMatchCreation,
    StatementMatchMember,
)
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import transfer_legs

_WHOLE_ACT = (
    selectinload(StatementMatch.members).selectinload(
        StatementMatchMember.line,
    ),
    # An entry member's parent and ITS entries: a payment member is valued
    # as its row's payment on the act's account (``covered_cash_leg`` walks
    # the row's covering movements, plan step ``credit_card:CC-5-4a-1``),
    # and a fold over 221 acts must not lazy-load one row's entries per
    # member -- the shape the creations' transaction chain below takes.
    selectinload(StatementMatch.members).selectinload(
        StatementMatchMember.entry,
    ).joinedload(TransactionEntry.transaction).selectinload(
        Transaction.entries,
    ),
    # And what ``transfer_legs.movement_parent`` reads to resolve a payment
    # member's parent -- a transfer movement's transfer (leaf
    # ``balance:X-bi-6-4c-1``: the register values a transfer's payment
    # through its LEG, and :func:`named_rows` names it by that leg) --
    # published by ``transfer_legs`` because the chain walks the movement's
    # side links (plan step ``balance:X-bi-6-4d-2``; its shadow row until
    # then), which are that package's to name.  For a side's record the
    # transaction chain above loads nothing: it is filed under no row.
    selectinload(StatementMatch.members).selectinload(
        StatementMatchMember.entry,
    ).options(*transfer_legs.movement_parent_loads()),
    selectinload(StatementMatch.creations).selectinload(
        StatementMatchCreation.transaction,
    ).selectinload(Transaction.entries),
    selectinload(StatementMatch.creations).selectinload(
        StatementMatchCreation.entry,
    ).joinedload(TransactionEntry.transaction).selectinload(
        Transaction.entries,
    ),
)

#: An accepted act NAMES AT LEAST ONE BANK LINE, as a clause a query can carry.
#:
#: **The invariant, stated once where SQL can apply it** (plan step
#: ``bank_import:X-gj-1c``, finding **bank_import:N-389**).  It was asked in
#: two languages: :func:`acts_of` handed every act to
#: :func:`~._accepted_view.accepted_groups`, which skipped a lineless one in
#: Python, while :func:`~._accepted_view.accepted_counts` counted the table
#: and skipped nothing.  A caption is derived from the second and a tab from
#: the first, so one such act made the Reconcile screen's Explained caption
#: one higher than the tab could draw -- measured on a planted act 2026-08-31:
#: caption ``1``, rendered ``0``, withheld ``0``.
#:
#: **It narrows the LOADER rather than guarding the fold**, which is what
#: deletes the Python guard instead of adding a second one beside it: an act
#: with no line has no day (``max()`` over an empty side), no amount and no
#: wording, so it is not a card and never was -- and a reader that cannot
#: receive one needs no arm for it.  The three guarantees that make the state
#: unreachable in the first place are ``record_match``'s refusal at the one
#: writer, ``fk_statement_match_members_line_account`` no longer cascading,
#: and migration ``e4a7c0f13b92`` having deleted the acts that already held
#: none.  This is the fourth and the only one a reader can apply: a foreign
#: key cannot see an absence.
#:
#: **Reaching one is still an ALARM rather than a silence**, and skipping
#: silently was the original defect (two adversarial reviews, 2026-08-20): the
#: act goes on claiming its transactions in ``matched_subjects``, so those rows
#: can never be matched again and no release control exists to free them.
#: :func:`~._accepted_view.accepted_counts` counts the acts this clause
#: EXCLUDES in the same aggregate it counts the ones it admits, and logs them
#: at ERROR -- one query, on the page that reads both numbers.
NAMES_A_BANK_LINE = StatementMatch.members.any(
    StatementMatchMember.bank_statement_line_id.isnot(None),
)




def named_rows(
    match: StatementMatch,
) -> "tuple[set[int | tuple[int, int]], set[int]]":
    """Return the plan items and purchase ids this act NAMES.

    A creation in one of these sets is a SUBJECT -- what the act is about --
    and one in neither is a CONTAINER.  Derived from the members rather than
    stored, because they are the one statement of what an act names and a
    second copy could disagree with them.

    **A row is named through its PAYMENT** (plan steps
    ``credit_card:CC-5-4a-1`` / ``CC-5-4a-2``, rulings **R-CC43**,
    **R-CC45**): every member on the app's side names a movement, and a
    member naming a row's covering movement -- the residual a group minted,
    the bill the owner ticked -- names the row it is the payment OF, so the
    creation record for a minted residual still meets its member.  A
    PURCHASE member names the purchase and not its envelope (the envelope is
    a container, ruling **R-GG**); the ``covers_settlement`` mark is what
    tells the two entry members apart, read off the entry the loader joined
    (:data:`_WHOLE_ACT`), so a payment member is a row named and never a
    purchase named.  The same rule :func:`~._candidates.matched_subjects`
    applies in SQL to an owner's claims.  Public because
    :mod:`._accepted_view` asks the same question of the same act.

    **A payment names its PARENT by** ``transfer_legs.cell_key`` (plan step
    ``balance:X-bi-6-4d-2``, ruling **R-BAL87**'s one identity for a row and a
    leg): a row's payment names the row's ``id``, and a transfer side's
    record names its LEG, the ``(transfer id, account id)`` pair, resolved by
    ``transfer_legs.movement_parent`` off the side links :data:`_WHOLE_ACT`
    loads.  An ``int`` and a pair cannot collide, so its two readers need no
    branch.  The undo (``_release.planned_removals``) probes the set with a
    creation's row id, and no creation is a transfer's (a match never creates
    one), so a leg never matches.  The register (``_accepted_view``) compares
    the WHOLE set with the act's creations (``created_every_row``), and there
    a leg is what makes the answer right: an act naming a transfer's payment
    did not create everything it names.  **Until that step a transfer's
    payment added its movement's ``transaction_id``** -- its shadow row's id,
    and from the step ``None``, which both readers happened to answer right
    with as a member of a set of row ids.  The register names and values a
    transfer member through its LEG (``_accepted_view._accepted_row``), never
    through this set.

    Args:
        match: The act, with its members, their subjects and the members'
            parents (:data:`_WHOLE_ACT`) loaded.

    Returns:
        ``(items, purchase_ids)``: the plan items the act is about -- a row's
        ``id`` or a transfer leg's ``(transfer id, account id)``, named
        through their payment -- and the purchases it names.
    """
    items = set()
    purchases = set()
    for member in match.members:
        if member.transaction_entry_id is None:
            continue
        if member.entry.covers_settlement:
            items.add(transfer_legs.cell_key(
                transfer_legs.movement_parent(member.entry),
            ))
        else:
            purchases.add(member.transaction_entry_id)
    return items, purchases



def acts_of(
    owner_id: int, account_id: int, match_ids: "set[int] | None" = None,
    *, applied_by_rule: "bool | None" = None,
) -> "list[StatementMatch]":
    """Return match acts WHOLE, newest first, in one read.

    **The ONE loader, because an act is only readable with both of its
    relations** (plan step ``bank_import:X-f6f``): what it NAMES decides
    whether it still holds, and what it CREATED decides what an undo would take
    back.  Two callers need exactly that -- the register's accepted list and
    the import page's delete preview -- and they spelled the same query with the
    same two eager loads until pylint's ``duplicate-code`` said so.  A third
    relation added later would otherwise be loaded by one reader and lazily
    fetched per row by the other; it is stated once, in :data:`_WHOLE_ACT`.

    **It filters on the OWNER as well as the account**, which the write door
    :func:`~._release.release_match` already does.  The account implies the owner
    (``fk_statement_matches_owner``), so the second column can only ever be
    redundant -- and a reader feeding a destructive control's confirmation
    narrows by the same two columns the control itself does rather than by one
    of them.  Named by adversarial security review 2026-08-24.

    The ORDER is the panel's (newest first) and costs the other caller nothing,
    where an unordered read would have to be sorted twice.

    **It returns only acts that NAME A BANK LINE** (:data:`NAMES_A_BANK_LINE`,
    plan step ``bank_import:X-gj-1c``).  That is a narrowing rather than a
    filter over the result, and it is what makes an act with no day, no amount
    and no wording unreachable by every reader instead of skipped by one of
    them; :func:`~._accepted_view.accepted_counts` shares the clause, so a tab
    caption and the cards under it are one set.  Such an act is still an ALARM
    -- see that clause for where the ERROR is raised and why it is not silent.

    Args:
        owner_id: The user the route proved owns the account.
        account_id: The account whose acts to read.
        match_ids: The acts to consider, or ``None`` for all of this account's.
            **The import page passes a set**, because it renders at most 20
            imports and every act outside them is a row it will never show --
            an unbounded read of an account's every act is work a page that
            renders 20 imports never uses.

        applied_by_rule: Which half of the account's acts to load -- ``True``
            for the acts a standing rule performed (**R-GT**), ``False`` for
            the acts a person ticked, ``None`` for both.  **A second narrowing
            beside** *match_ids* **rather than a filter over the result**, for
            that parameter's own reason: an act the caller will never render
            is work it never uses.

    Returns:
        Its :class:`~app.models.statement_match.StatementMatch` rows that name
        a bank line, newest first, loaded WHOLE (:data:`_WHOLE_ACT`) -- both
        relations and the row each of them names, so a caller folding many
        acts issues no further statement and reaches no subject by id.
    """
    if match_ids is not None and not match_ids:
        return []
    query = (
        db.session.query(StatementMatch)
        .options(*_WHOLE_ACT)
        .filter(
            StatementMatch.account_id == account_id,
            StatementMatch.user_id == owner_id,
            # **The invariant, applied where a caption can share it**
            # (:data:`NAMES_A_BANK_LINE`, plan step ``bank_import:X-gj-1c``).
            # An act naming no bank line has no day, no amount and no wording,
            # so no reader of this function can render one; excluding it HERE
            # is what lets :func:`~._accepted_view.accepted_counts` count the
            # same set from one clause instead of a second spelling in Python.
            NAMES_A_BANK_LINE,
        )
    )
    if match_ids is not None:
        query = query.filter(StatementMatch.id.in_(match_ids))
    if applied_by_rule is not None:
        # **Narrowed in SQL, before anything is LOADED** (plan step
        # ``bank_import:X-gj-1a``).  The Reconcile screen renders the two
        # halves as two tabs (**R-GT**), and a caller filtering this
        # function's RESULT would load and price every act on the account to
        # render one half -- 221 of the developer's own to draw a tab holding
        # none of them, which is exactly the cost ruling **R-GX** split the
        # register off the review screen to stop paying.
        query = query.filter(
            StatementMatch.applied_by_rule.is_(applied_by_rule),
        )
    return query.order_by(
        StatementMatch.created_at.desc(), StatementMatch.id.desc(),
    ).all()
