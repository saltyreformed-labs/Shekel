"""
Shekel Budget App -- The account half of a bill's edit

Ruling **plan#3230** (developer 2026-11-10), task plan#3237: a save that moves a
bill to another account stops before moving a row changed by hand, and asks.
Josh's words: "I don't think the hand edited bills should move without
alerting the end user first. A hand edit indicates something different about
that particular bill."

A bill's edit moves the unpaid rows its regeneration maintains (paychecks
ending on or after the "Amount effective from" date,
``_recurrence_common.rows_this_pass_may_maintain``).  A row changed by hand
(``is_override``: a price typed over, a moved paycheck, a carry-forward) is the
regeneration's conflict and is left where it is, so this module states which
of those rows the conflict chooser asks about -- "Move to <the new account>" or
"Keep on <its account>" -- and which of them the move can be offered for.
:mod:`app.routes._recurrence_conflict_chooser` renders the question and acts
on the answers; the move itself is the engine's
(``recurrence_engine.resolve_conflicts(..., "move", ...)``).

**Split from the chooser module when that reached the 1,000-line ceiling**, at
the seam the question draws: which rows are asked, and where a move would put
each, are read off the definition, its rows and the books, and need nothing of
the page.  So it takes plain values and imports nothing of the chooser's.

A transfer's two accounts are not asked about (task plan#3237 leaves them
out): the transfer doors' before-image names no account, and this module then
answers ``None``.

Route-layer module (leading underscore = route-internal) beside the chooser it
serves; it consumes no Flask global.
"""
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import NamedTuple

from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services.balance_at import BalanceContext
from app.services.definition_unarchive import (
    books_named,
    rows_held_where_they_sit,
    sits_off_its_definitions_account,
)


class AskedAccount(NamedTuple):
    """What a bill's edit asks about one row's account (ruling plan#3230).

    Attributes:
        on_account: The name of the account the row sits on now -- the
            "Keep on <this>" side.
        books_open_on: When the move cannot be offered: the day the bill's
            new account's books open, on or after this row's own day, so
            moving it there would count it a second time (ruling
            **R-PC99**).  ``None`` when "Move to" is offered.
        books: Those books, in the possessive the books sentences use
            (:func:`~app.services.definition_unarchive.books_named`:
            ``Savings's``), or ``None`` when the move is offered.
    """

    on_account: str
    books_open_on: date | None
    books: str | None


class RowOnAccount(NamedTuple):
    """A row as a "Move to" would leave it: on *account_id*, still due on *due_date*.

    The rewrite :func:`~app.services.definition_unarchive.own_day_held`
    reads (its accounts and its due day), so the books question is asked of
    the row where the move puts it -- the shape a regeneration's own rewrite
    gives a row it moves (``RegenerationPreview.rewrites``).

    Attributes:
        account_id: The bill's new account.
        due_date: The row's own due date, which the move keeps.
    """

    account_id: int
    due_date: date | None


@dataclass(frozen=True)
class AccountQuestion:
    """What a bill's edit asks about its rows changed by hand (ruling plan#3230).

    Attributes:
        moved_to: The :class:`~app.models.account.Account` the edit moved the
            bill to.
        asked: ``{row id: AskedAccount}`` for every live row changed by hand
            that is not already on *moved_to* -- possibly none, when the edit
            moved the bill over no such row (the page still names the move
            when it asks the price, and a deleted row it brings back returns
            on *moved_to*).
    """

    moved_to: Account
    asked: "dict[int, AskedAccount]"

    def movable(self) -> frozenset[int]:
        """Return the ids of the asked rows the page offers to move.

        Returns:
            Every asked row whose day *moved_to*'s books do not hold.
        """
        return frozenset(
            row_id for row_id, asked in self.asked.items()
            if asked.books_open_on is None
        )


def account_question(
    template: TransactionTemplate | TransferTemplate,
    account_before: int | None, overridden: Sequence[int],
    model: type[Transaction] | type[Transfer], ctx: BalanceContext,
) -> AccountQuestion | None:
    """Return what a bill's edit asks about its rows' account, or ``None`` when it moved none.

    **The one statement of which rows the account question is asked of**,
    read by the page and by the Apply alike
    (``_recurrence_conflict_chooser.regenerate_or_conflict_chooser``), so the
    rows offered and the rows moved cannot come to differ.  Asked when the
    edit moved a TRANSACTION definition's account (*account_before* names
    one, and it is not the account the edit leaves), of every row the
    regeneration held back because it was changed by hand (*overridden*)
    that is live and not already on the new account
    (:func:`~app.services.definition_unarchive.sits_off_its_definitions_account`).
    A paid row is never a conflict, so it is never asked about and never
    moves; a soft-deleted row is on no balance, so it is not asked about
    either -- "use" brings one back on the bill's account, a row re-priced
    before it was deleted included (``recurrence_engine.resolve_conflicts``).

    **Each row is asked the books question where the move would put it**
    (ruling **R-PC99**): on or before the new account's books the row would
    sit inside its opening and be counted a second time, so the page offers
    no move for it and says why.  The same loader and predicate every other
    door bounding a planned row asks
    (:func:`~app.services.definition_unarchive.rows_held_where_they_sit`),
    over the row as moved (:class:`RowOnAccount`).

    Args:
        template: The edited definition, its fields applied.
        account_before: The account the definition named before the edit
            (``PreEditTemplateState.account_id``); ``None`` at a transfer
            door, which asks no account question.
        overridden: The ids the regeneration held back as changed by hand
            (``RecurrenceConflict.overridden``).
        model: The row model the ids name (``RecurrenceConflictKind.model``).
        ctx: The edit's read pass, whose calendar the regeneration resolved
            against.

    Returns:
        The :class:`AccountQuestion` -- its ``asked`` empty when no row changed
        by hand is left to ask about -- or ``None`` when the edit moved no
        transaction definition's account.
    """
    if account_before is None or template.account_id == account_before:
        return None
    rows = [
        row for row in (db.session.get(model, row_id) for row_id in overridden)
        if row is not None and not row.is_deleted
        and sits_off_its_definitions_account(row)
    ]
    moved = {
        row.id: RowOnAccount(template.account_id, row.due_date) for row in rows
    }
    held = rows_held_where_they_sit(
        template, (model.id.in_(list(moved)),), ctx.calendar(), {},
        rewrites=moved,
    ) if rows else {}
    return AccountQuestion(
        # Read by id: the edit set the column, and the relationship it loaded
        # before may still name the account it left.
        moved_to=db.session.get(Account, template.account_id),
        asked={
            row.id: AskedAccount(
                on_account=row.account.name,
                books_open_on=held[row.id][0].opened_on if row.id in held else None,
                books=(
                    books_named(moved[row.id], held[row.id][0].opened_on)
                    if row.id in held else None
                ),
            )
            for row in rows
        },
    )


__all__ = [
    "AccountQuestion",
    "AskedAccount",
    "RowOnAccount",
    "account_question",
]
