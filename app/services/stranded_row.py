"""
Shekel Budget App -- A stranded row, as the books refusals name it

Plan step ``pay_calendar:C18-a``.  The value :mod:`app.services.planned_rows_books`
answers with when a save would strand an unpaid recurring row below the books
(rulings **R-PC88**, **R-PC90**, **R-PC91**, **R-PC93**, **R-PC99**), and the
sentences every refusal over one is built from: what the row is, what the save
would do to it, and what the owner does first.  The refusals' own wording,
and the argument for each question they ask, are that module's.

**A module of its own because ``planned_rows_books`` reached the 1,000-line
ceiling** (coding-standards ruling **balance:R-IR**: the change that crosses it
splits the file).  This is the move, verbatim: the two classes and their
docstrings, which hold no query and no walk -- only what a refusal SAYS about
a row already found -- so they read apart from the producers that find it.
:mod:`app.services.planned_rows_books` imports them back and keeps
:class:`StrandedRow` in its public face, the type its producers return.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain values in,
a sentence out; no Flask symbol, no writes, no clock.
"""

from dataclasses import dataclass
from datetime import date
from typing import NamedTuple

from app.services.definition_unarchive import books_named


class BooksHolding(NamedTuple):
    """Which books a stranded row is compared with (ruling R-PC99).

    Attributes:
        opened_on: Those books' opening day.
        holder: What they are read off, for :meth:`StrandedRow.books`: the
            ROW when the books of the account it sits on hold its own day --
            as an edit's regeneration rewrites it, where it does -- else its
            definition, whose walk drops it.
        sits_inside: Whether it sits on those books: ``False`` for a row its
            schedule would drop although it sits on another account -- one
            its definition left behind by an account move -- which is
            refused for the pass that would delete it, never described as
            sitting inside them.
    """

    opened_on: date
    holder: object
    sits_inside: bool


@dataclass(frozen=True)
class StrandedRow:
    """A still-projected recurring row a save would leave below the books.

    Attributes:
        name: What the refusal calls it: the row's own name for a live row,
            its DEFINITION's name for a hidden one (*is_hidden*), which the
            owner finds on the archived list and nowhere else.
        books_day: The day the books are compared with, for the placement
            the walk gives the row
            (:meth:`~app.services.recurrence.ResolvedRecurrence.books_day`).
        is_envelope: Whether *books_day* is the row's paycheck's last day
            (an envelope) rather than its due day, which is what the refusal
            has to say to be true.
        is_hidden: Whether the row is hidden by its definition's archive --
            one its unarchive would bring back (ruling **R-PC93**) -- which
            the owner cannot see, let alone mark paid, until they unarchive.
        holding: The books it is compared with (:class:`BooksHolding`), or
            ``None`` where no sentence names them -- read as sitting inside.
        dropped: Whether its schedule's walk drops its occurrence, so a
            later maintain pass would retire it: the "stop being planned"
            half of the refusal.
    """

    name: str
    books_day: date
    is_envelope: bool
    is_hidden: bool = False
    holding: BooksHolding | None = None
    dropped: bool = True

    def described(self) -> str:
        """Return the row as the refusals name it: its name and the day compared.

        Returns:
            ``"Rent" is still projected and due 2026-01-01`` for a bill, or
            ``"Groceries" is still projected in the paycheck ending
            2026-09-23`` for an envelope -- the day the owner has to move
            the row past, in the words ruling **R-PC91** gave it.  A hidden
            row reads ``"Rent" is archived and still holds an unpaid item due
            2026-01-01 that unarchiving would bring back``, R-PC93's words.
        """
        where = (
            f"in the paycheck ending {self.books_day.isoformat()}"
            if self.is_envelope
            else f"due {self.books_day.isoformat()}"
        )
        if self.is_hidden:
            return (
                f'"{self.name}" is archived and still holds an unpaid item '
                f"{where} that unarchiving would bring back"
            )
        if not self.is_envelope:
            where = f"and {where}"
        return f'"{self.name}" is still projected {where}'

    def remedy(self) -> str:
        """Return what the owner does first, the clause every refusal ends on.

        Returns:
            ``Mark it paid or cancel it first`` for a live row; for a hidden
            one, ``Unarchive "Rent", mark that item paid or cancel it`` --
            the one door that reaches a row the owner cannot see (ruling
            **R-PC93**), naming the definition so the sentence cannot be read
            as unarchiving the opening or the row.  Both are true for every
            row these refusals name (developer, 2026-09-23, round 9's M1):
            R-PC88's "or move it later" could never work, since every such
            row was made by a recurring schedule -- its due day is the
            schedule's, which the form refuses to change, and a move to a
            later paycheck keeps it, so the refusal came back unchanged -- and
            R-PC93's "or delete "Rent" for good" is refused for any definition
            with payment history (round 8's M-1).
        """
        if self.is_hidden:
            return f'Unarchive "{self.name}", mark that item paid or cancel it'
        return "Mark it paid or cancel it first"

    def consequence(self) -> str:
        """Return what the save would do to the row, the refusals' middle sentence.

        Returns:
            ``An opening is the balance at the END of its day, so that unpaid
            item would sit inside it`` -- ending ``and stop being planned``
            when its schedule drops it too -- for a row inside the books it
            sits on; for a row its schedule drops while it sits on another
            account (ruling **R-PC99**), ``Its schedule would stop producing
            that unpaid item, so the next pass to reach it would delete it
            without a word`` -- the developer's reason such a row still
            blocks the move; that pass is the save's own regeneration for a
            row in its paycheck window, a later one for a row before it.  No
            pass reaches a HIDDEN row (the round-7 review's L1): its sentence
            stops at its schedule.
        """
        if self.holding is not None and not self.holding.sits_inside:
            then = "" if self.is_hidden else (
                ", so the next pass to reach it would delete it without a word"
            )
            return f"Its schedule would stop producing that unpaid item{then}."
        tail = " and stop being planned" if self.dropped else ""
        return (
            "An opening is the balance at the END of its day, so that unpaid "
            f"item would sit inside it{tail}."
        )

    def books(self) -> str:
        """Return the accounts whose books it is compared with, in the possessive.

        Read only when a sentence names them
        (:func:`~app.services.definition_unarchive.books_named`), off
        :attr:`holding`'s holder: the accounts the row sits on, or its
        definition's.

        Returns:
            ``Checking's``, or ``Src's and Dst's``.
        """
        return books_named(self.holding.holder, self.holding.opened_on)
