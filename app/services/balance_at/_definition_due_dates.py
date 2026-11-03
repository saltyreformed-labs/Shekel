"""WHERE A DUE-DATE REPEAT'S DATES COME FROM -- the card it pays into.

Plan step ``credit_card:CC-6``, its task plan#2787 (ruling plan#2671: a card
payment's dates are its repeat, and a new repeat is each statement's due
date, worked out from the card's dated terms).  The recurrence package walks
such a repeat over a :class:`~app.services.card_statement.DueDateSchedule`
it never loads (``recurrence._occurrence``): the pure package reads no
database, and a value without the schedule is refused there rather than
walked as a payment that never falls due.  This module answers where the
schedule comes from: :func:`definition_due_dates`, read beside the books
floor by :meth:`~app.services.balance_at.BalanceContext.resolved_for` and by
every other caller of the one composition
(:func:`~app.services.balance_at.resolved_with_books`), which lays it on
the resolved value.

**The dates are the DESTINATION's, read every pass and stored nowhere.**  A
due date is a day the card's terms decide, and the owner restates terms (a
mistyped due day corrected in place, or a new version from a day on), so a
copy of it on the rule would be a second home with no reconciler
(``CLAUDE.md`` rule 14) -- the ruling's own words were that nothing should
remember a day.  Read here on every pass, a terms change moves the walk on
the next one with nothing to keep in step (re-placing the rows a pass already
stored is plan#2675's).

**A due-date repeat whose destination is not a configured card is refused**,
with :class:`~app.services.recurrence.RecurrenceResolutionError` naming the
account: no destination at all (a transaction template, a payroll line's
rule), an account that is not revolving, or a card with no terms (dormant,
design 3.4, so it has no statement to fall due).  Each is a state the write
doors refuse (plan#2788), so here it is a broken invariant, and the read side
fails loudly on one rather than walking it as no dates -- the disposition the
resolver takes for a stored cadence it cannot model.

**Why a module of its own**: :mod:`._definition_books` answers which books
bound a definition, a fact about every account it moves money in; this
answers which days a repeat falls on, a fact about the one account it pays,
and only for one unit.  The composition that attaches both stays there.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
plain values out; no Flask symbol, no writes, no clock.
"""

from __future__ import annotations

from app.models.account import Account
from app.services.account_projection import is_revolving
from app.services.card_statement import DueDateSchedule
from app.services.card_terms import load_card_term_versions
from app.services.recurrence import (
    RecurrenceResolutionError,
    RecurrenceSpec,
    follows_card_due_dates,
)
from app.services.recurring_transfer_query import destination_account


def definition_due_dates(
    spec: RecurrenceSpec, definition: object | None,
    memo: "dict[int, DueDateSchedule]",
) -> DueDateSchedule | None:
    """Return the card's due dates *spec* walks, or ``None`` for any other repeat.

    Each card's terms are read once per *memo* -- the caller's, one per read
    pass -- so a render resolving several repeats into one card pays one
    read, and the value handed back for two of them is the same schedule.

    Args:
        spec: The authored recurrence.  Only its unit is read: every unit but
            the due date answers ``None`` without a query.
        definition: What moves the money: a transfer template, the form
            preview's unsaved definition, or anything else
            :func:`~app.services.recurring_transfer_query.destination_account`
            reads a ``to_account_id`` off -- assumed the pass owner's, as the
            books floor assumes its accounts are.  ``None`` for a payroll
            line's rule.
        memo: The pass's ``card account id -> schedule`` memo, filled here.
            A refusal is not memoised, so it fires on every call.

    Returns:
        The destination card's :class:`~app.services.card_statement.DueDateSchedule`
        for a repeat on each statement's due date; ``None`` for every other
        unit.

    Raises:
        RecurrenceResolutionError: A due-date repeat whose destination is not
            a configured card: none named, not revolving, or no terms.
    """
    if not follows_card_due_dates(spec.unit):
        return None
    card = destination_account(definition)
    if card is None:
        raise RecurrenceResolutionError(
            "A repeat on each statement's due date pays no account: only a "
            "transfer into a credit card repeats on its due dates, so there "
            "is no card whose due dates it could fall on."
        )
    if card.id not in memo:
        memo[card.id] = _card_due_dates(card)
    return memo[card.id]


def _card_due_dates(card: Account) -> DueDateSchedule:
    """Return *card*'s due-date schedule, refusing an account that is not a configured card.

    Args:
        card: The account a due-date repeat pays into.

    Returns:
        The :class:`~app.services.card_statement.DueDateSchedule` its terms
        versions decide.

    Raises:
        RecurrenceResolutionError: *card* is not revolving, or is a card with
            no terms.
    """
    if not is_revolving(card):
        raise RecurrenceResolutionError(
            f"A repeat on each statement's due date pays account {card.id}, "
            f"which is not a credit card: only a card has statements to fall "
            f"due."
        )
    versions = load_card_term_versions(card.id)
    if not versions:
        raise RecurrenceResolutionError(
            f"A repeat on each statement's due date pays card {card.id}, "
            f"which has no terms yet: its due dates are worked out from its "
            f"terms, so it has none to fall on until they are stated."
        )
    return DueDateSchedule.of(versions)
