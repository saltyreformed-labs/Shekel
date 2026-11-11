"""
Shekel Budget App -- Card route package: the unpaid payments follow the terms.

Plan step **credit_card:CC-6**, its task plan#2675 (ruling **plan#2671**: "When
you change the card's terms, the unpaid payments move to the new dates.  If you
had typed over one of them, the app asks you first.").  A repeat on each
statement's due date stores no day, so a change of terms moves its WALK on the
next read pass with nothing to keep in step
(:mod:`app.services.balance_at._definition_due_dates`); what is left is the rows
that walk already wrote.  Both terms doors, the save and the remove, call
:func:`follow_the_terms_or_ask` with their write pending.

**Every row moves through the maintain pass**
(:func:`~app.services.transfer_recurrence.regenerate_for_template`), the one
writer of a definition's rows: a row answering what is still a due date is left
alone, one answering what no longer is retires, and each new date gets a row.  So a
moved payment is a new row on the new date rather than an old row re-dated
(ruling **recurrence:R-R49**: a row is never re-pointed at another occurrence),
and a row holding the owner's notes or a kept payment record is retained where
it is and reported, as at every other door that regenerates.

**Asked of the rows this change moved, and of no others.**  The pass reports
every typed-over or deleted row in its window as a conflict, whether or not its
date moved.  The question here is about a date, so a row is asked about only
where the terms before the change made its date a due date and the terms after
it do not (:func:`_rows_on_moved_dates`).  A row left on an old date by an
earlier "keep" was on no due date before this change, so it is not asked about
a second time; and a save that moves no date (a new credit limit) runs no pass
and asks nothing.

**"Move" finishes in a second pass**, because that is the pass that can reach
the row.  The first pass ran while the row was typed over, so the row was held
back, and it wrote the new date's payment beside it.  Handing the row back
(``transfer_recurrence.resolve_conflicts``) only clears the owner's hold, which
would leave two payments for one statement until some later edit.  Run again,
the pass finds a row of its own on a date the rule no longer names, and retires
it.  The new date's row is already there and is not written twice
(:class:`~app.services._recurrence_common.OccurrenceClaims`).

**"Keep" leaves the row where it is, and the new date still gets a row.**  That
is what the pass does with a held row at every door, the page says so, and the
later worked-out amounts subtract a kept payment from the payment after it
(ruling **R-CC22**).  A deleted row gets no decision at all: its date is not a
due date any more, so the new date gets a payment either way, and a choice with
one outcome would decide nothing (``accounts/card_terms_chooser.html``).

Route-layer module (leading underscore = route-internal) because it consumes
``flash``, ``render_template`` and ``request``.
"""

from dataclasses import dataclass

from flask import Response, flash, request, url_for

from app.exceptions import (
    NotFoundError,
    RecurrenceConflict,
    ValidationError as ShekelValidationError,
)
from app.extensions import db
from app.models.account import Account
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.routes._recurrence_conflict_chooser import (
    apply_conflict_decisions,
    build_conflict_choices,
    flash_retained_notice,
    parse_conflict_decisions,
    render_chooser_page,
)
from app.routes._redirect_target import RedirectTarget
from app.routes._transfer_creation_helpers import TRANSFER_TEMPLATE_KIND
from app.services import card_terms
from app.services.balance_at import BalanceContext
from app.services.card_statement import DueDateSchedule
from app.services.generation_schedule import GenerationSchedule
from app.services.recurrence_engine import definition_recurs
from app.services.recurring_transfer_query import due_date_repeats_into
from app.utils.dates import display_today

#: The chooser page framing a change of terms.
_TERMS_CHOOSER_PAGE = "accounts/card_terms_chooser.html"


@dataclass(frozen=True)
class TermsDoor:
    """The door whose pending write the payments are following.

    Attributes:
        change_sentence: What the owner just did, in their terms; the chooser
            page opens with it.
        action_url: Where the chooser's Apply posts: the door itself, which
            makes the same write again.
        back: Where a refusal, and the chooser's Cancel, return.
    """

    change_sentence: str
    action_url: str
    back: RedirectTarget


@dataclass(frozen=True)
class _MovedDates:
    """A change of a card's terms, as the two sets of due dates it replaced.

    Attributes:
        before: The due dates before the door's write.
        after: The due dates with it pending.
    """

    before: DueDateSchedule
    after: DueDateSchedule

    def moved(self, row: Transfer) -> bool:
        """Return whether this change took the date *row* answers off the due dates.

        Args:
            row: A row a pass held back.  One answering no occurrence is on
                no date, so nothing moved it.

        Returns:
            ``True`` when its date was a due date before and is not now.
        """
        return (
            row.occurs_on is not None
            and self.before.falls_due_on(row.occurs_on)
            and not self.after.falls_due_on(row.occurs_on)
        )


def _maintained(
    template: TransferTemplate, ctx: BalanceContext,
) -> RecurrenceConflict | None:
    """Run the maintain pass for *template*, returning what it held back.

    The row window opens at today's paycheck, as at the other door a rule's
    dates move without an edit of the rule
    (:func:`app.routes._standing_payment.sync_loan_payment_start_or_refuse`):
    a paycheck already over is history, and no terms change reaches into it.

    Args:
        template: A definition repeating on the card's due dates.
        ctx: The read pass built with the door's write pending.

    Returns:
        The pass's :class:`~app.exceptions.RecurrenceConflict`, or ``None``
        when it held nothing back.
    """
    try:
        TRANSFER_TEMPLATE_KIND.regenerate_fn(
            template, GenerationSchedule.for_pass(ctx), ctx.scenario_id,
            effective_from=display_today(),
        )
    except RecurrenceConflict as conflict:
        return conflict
    return None


def _rows_on_moved_dates(
    conflict: RecurrenceConflict, dates: _MovedDates,
) -> RecurrenceConflict:
    """Narrow what a pass held back to the rows this change's question is about.

    Args:
        conflict: What one definition's pass held back.
        dates: The change.

    Returns:
        The rows the change took off the due dates, as the conflict the
        chooser's helpers read, parted by whether each is deleted rather than
        by the pass's lists: a row typed over and then deleted is held as
        typed over, and it is a deleted payment to the owner.  Nothing
        retained: a retained row is reported, never asked about.
    """
    moved = [
        row for row in (
            db.session.get(Transfer, row_id)
            for row_id in (*conflict.overridden, *conflict.deleted)
        )
        if dates.moved(row)
    ]
    return RecurrenceConflict(
        overridden=[row.id for row in moved if not row.is_deleted],
        deleted=[row.id for row in moved if row.is_deleted],
    )


def _apply_decisions(
    template: TransferTemplate, asked: RecurrenceConflict,
    decisions: dict[int, str], ctx: BalanceContext,
) -> RecurrenceConflict | None:
    """Apply the owner's answers for one definition, and finish what they moved.

    Only the typed-over rows are answerable: the allow-list handed down leaves
    a deleted row out, so a crafted form cannot restore one.

    Args:
        template: The definition.
        asked: Its rows on moved dates (:func:`_rows_on_moved_dates`).
        decisions: The chooser's ``{row_id: "keep" | "use"}``.
        ctx: The door's read pass.

    Returns:
        What the second pass held back, the same way :func:`_maintained` does.
    """
    apply_conflict_decisions(
        kind=TRANSFER_TEMPLATE_KIND,
        conflict=RecurrenceConflict(overridden=asked.overridden),
        decisions=decisions,
        ctx=ctx,
    )
    return _maintained(template, ctx)


def _maintain_every_repeat(
    repeats: list[TransferTemplate], dates: _MovedDates, door: TermsDoor,
    ctx: BalanceContext,
) -> str | None:
    """Run each definition's pass, then ask, or apply the owner's answers.

    Args:
        repeats: The definitions following the card's due dates.
        dates: The change.
        door: The door whose write is pending.
        ctx: The read pass built with that write pending.

    Returns:
        The chooser page, its pending write rolled back, or ``None`` when the
        caller should commit.
    """
    decisions = parse_conflict_decisions(request.form)
    held = {template.id: _maintained(template, ctx) for template in repeats}
    asked = {
        template_id: _rows_on_moved_dates(conflict, dates)
        for template_id, conflict in held.items()
        if conflict is not None
    }
    if decisions is None and any(
        conflict.overridden or conflict.deleted for conflict in asked.values()
    ):
        page = render_chooser_page(
            _TERMS_CHOOSER_PAGE,
            request.form,
            choices=sorted(
                (
                    choice
                    for conflict in asked.values()
                    for choice in build_conflict_choices(
                        conflict, TRANSFER_TEMPLATE_KIND, ctx.calendar(),
                    )
                ),
                key=lambda choice: choice.due_date,
            ),
            change_sentence=door.change_sentence,
            # Every payment listed carries the page's one question, its due
            # date; a card's terms change asks nothing about an account.
            asks_follow=True,
            action_url=door.action_url,
            cancel_url=url_for(door.back.endpoint, **door.back.kwargs),
        )
        db.session.rollback()
        return page
    if decisions is not None:
        for template in repeats:
            if asked.get(template.id) is not None:
                held[template.id] = _apply_decisions(
                    template, asked[template.id], decisions, ctx,
                )
    flash_retained_notice([
        row_id
        for conflict in held.values() if conflict is not None
        for row_id in conflict.retained
    ])
    return None


def follow_the_terms_or_ask(
    account: Account, before: DueDateSchedule | None, door: TermsDoor,
) -> Response | str | None:
    """Move the card's unpaid payments to the due dates the door's write leaves.

    Called by both terms doors with their write made and uncommitted, so a
    refusal or a question rolls the change of terms back with everything else
    and nothing has moved.

    Args:
        account: The card the door has gated.
        before: The card's due dates before the write
            (:func:`app.services.card_terms.due_dates_of`); ``None`` for a
            dormant card, which no repeat can follow (its doors refuse one,
            plan#2788).
        door: The door (:class:`TermsDoor`).

    Returns:
        The chooser page or a refusal's redirect, each with the write rolled
        back; or ``None`` when the caller should commit.
    """
    after = card_terms.due_dates_of(
        card_terms.load_card_term_versions(account.id),
    )
    # Equal: this save moved no date (a new credit limit), so no pass runs and
    # nothing is asked.
    if before is None or after == before:
        return None
    repeats = [
        template for template in due_date_repeats_into(account.id)
        if definition_recurs(template)
    ]
    if not repeats:
        return None
    # Built here, after the door's write, because the pass reads the card's due
    # dates once and walks them: built before the write, it would walk the old
    # terms.
    ctx = BalanceContext.build(account.user_id)
    # No baseline scenario: no pass can run, as at every door that regenerates.
    if ctx.scenario is None:
        return None
    try:
        return _maintain_every_repeat(
            repeats, _MovedDates(before, after), door, ctx,
        )
    except (NotFoundError, ShekelValidationError) as exc:
        db.session.rollback()
        flash(f"Could not move this card's payments: {exc}", "danger")
        return door.back.to_response()
