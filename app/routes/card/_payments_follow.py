"""
Shekel Budget App -- Card route package: the unpaid payments follow their dates.

Plan step **credit_card:CC-6**, its task plan#2675 (ruling **plan#2671**: "When
you change the card's terms, the unpaid payments move to the new dates.  If you
had typed over one of them, the app asks you first.").  A repeat on each
statement's due date stores no day, so a change of terms moves its WALK on the
next read pass with nothing to keep in step
(:mod:`app.services.balance_at._definition_due_dates`); what is left is the rows
that walk already wrote.  Both terms doors, the save and the remove, call
:func:`follow_the_terms_or_ask` with their write pending.

**The payment's own door asks the same question** (task plan#2679): switching
a card's payment between payoff rows and a payment scheduled with the card
company rewrites its repeat, from each payday to each due date or back, and the
rows it wrote follow the same way (:func:`follow_the_switch_or_ask`).  One
helper serves both doors, because the question is the same one: a change took
some payments' dates away, and the owner typed over some of those payments.
The two differ only in which dates were taken (:class:`MovedDates`) and in the
words the chooser page uses for where the payments go (:class:`_Framing`).

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
one outcome would decide nothing (``accounts/card_payments_chooser.html``).

Route-layer module (leading underscore = route-internal) because it consumes
``flash``, ``render_template`` and ``request``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

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
from app.services.card_payoff import RepeatChange
from app.services.card_statement import DueDateSchedule
from app.services.generation_schedule import GenerationSchedule
from app.services.recurrence import (
    RecurrenceSpec,
    follows_card_due_dates,
    occurrences,
)
from app.services.recurrence_engine import definition_recurs
from app.services.recurring_transfer_query import due_date_repeats_into
from app.utils.dates import display_today

#: The chooser page framing a change of a card's payment dates, for both doors.
_PAYMENTS_CHOOSER_PAGE = "accounts/card_payments_chooser.html"


@dataclass(frozen=True)
class _Framing:
    """What the chooser page calls the change and the dates the payments go to.

    Attributes:
        page_title: The page's title.
        new_dates: Where the payments go, in the plural ("the new due dates").
        new_date: The one date a payment goes to ("the new due date").
    """

    page_title: str
    new_dates: str
    new_date: str


#: A change of the card's terms.
_TERMS_FRAMING = _Framing(
    "Confirm change of card terms", "the new due dates", "the new due date",
)

#: A switch to a payment scheduled with the card company.
_SCHEDULED_FRAMING = _Framing(
    "Confirm change of card payment", "each statement's due date",
    "the due date",
)

#: A switch back to payoff rows.
_PAYOFF_FRAMING = _Framing(
    "Confirm change of card payment", "each payday", "the payday",
)


@dataclass(frozen=True)
class PaymentsDoor:
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
class MovedDates:
    """A change of a payment's dates, as whether a day was a date either side.

    Attributes:
        before: Whether a day was one of the payment's dates before the
            door's write: a due date under the old terms, or an occurrence of
            the repeat a switch replaced.
        after: Whether it is one with the write pending.
    """

    before: Callable[[date], bool]
    after: Callable[[date], bool]

    def moved(self, row: Transfer) -> bool:
        """Return whether this change took the date *row* answers away.

        Args:
            row: A row a pass held back.  One answering no occurrence is on
                no date, so nothing moved it.

        Returns:
            ``True`` when its date was one of the payment's dates before and
            is not now.  A row an earlier "keep" left on a date that was
            already no longer the payment's is not asked about again, and nor
            is one on a date both sides name.
        """
        return (
            row.occurs_on is not None
            and self.before(row.occurs_on)
            and not self.after(row.occurs_on)
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
    conflict: RecurrenceConflict, dates: MovedDates,
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


@dataclass(frozen=True)
class _Change:
    """A door's change of a payment's dates: which were taken, and in what words.

    Attributes:
        dates: Which dates the change took away (:class:`MovedDates`).
        framing: What the chooser page calls the change (:class:`_Framing`).
    """

    dates: MovedDates
    framing: _Framing


def _maintain_every_repeat(
    repeats: list[TransferTemplate], change: _Change, door: PaymentsDoor,
    ctx: BalanceContext,
) -> str | None:
    """Run each definition's pass, then ask, or apply the owner's answers.

    Args:
        repeats: The definitions whose dates the door's write moved.
        change: The change.
        door: The door whose write is pending.
        ctx: The read pass built with that write pending.

    Returns:
        The chooser page, its pending write rolled back, or ``None`` when the
        caller should commit.
    """
    decisions = parse_conflict_decisions(request.form)
    held = {template.id: _maintained(template, ctx) for template in repeats}
    asked = {
        template_id: _rows_on_moved_dates(conflict, change.dates)
        for template_id, conflict in held.items()
        if conflict is not None
    }
    if decisions is None and any(
        conflict.overridden or conflict.deleted for conflict in asked.values()
    ):
        page = render_chooser_page(
            _PAYMENTS_CHOOSER_PAGE,
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
            page_title=change.framing.page_title,
            new_dates=change.framing.new_dates,
            new_date=change.framing.new_date,
            # Every payment listed carries the page's one question, its date;
            # neither door asks anything about an account.
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


def _follow_or_ask(
    repeats: list[TransferTemplate], change: _Change, door: PaymentsDoor,
    ctx: BalanceContext,
) -> Response | str | None:
    """Run :func:`_maintain_every_repeat`, refusing a row the pass cannot write.

    Args:
        repeats: The definitions whose dates the door's write moved.
        change: The change.
        door: The door whose write is pending.
        ctx: The read pass, its baseline scenario present.

    Returns:
        The chooser page or a refusal's redirect, each with the write rolled
        back; or ``None`` when the caller should commit.
    """
    try:
        return _maintain_every_repeat(repeats, change, door, ctx)
    except (NotFoundError, ShekelValidationError) as exc:
        db.session.rollback()
        flash(f"Could not move this card's payments: {exc}", "danger")
        return door.back.to_response()


def follow_the_terms_or_ask(
    account: Account, before: DueDateSchedule | None, door: PaymentsDoor,
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
        door: The door (:class:`PaymentsDoor`).

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
    return _follow_or_ask(
        repeats,
        _Change(MovedDates(before.falls_due_on, after.falls_due_on), _TERMS_FRAMING),
        door,
        ctx,
    )


def _fires_on(
    spec: RecurrenceSpec | None, template: TransferTemplate, ctx: BalanceContext,
) -> Callable[[date], bool]:
    """Return whether a day is an occurrence of *spec* on *template*.

    Asked of the repeat's own walk (``recurrence.occurrences``) through the
    day, resolved by the pass as generation resolves it, so "one of the
    payment's dates" has the one spelling the rows were written from.

    Args:
        spec: The repeat, or ``None`` for none, which names no day.
        template: The definition it belongs to, for its books and its card's
            due dates.
        ctx: The read pass.

    Returns:
        The test.  It names no day when the owner has no pay periods, as the
        resolution then answers nothing.
    """
    resolved = None if spec is None else ctx.resolved_for(spec, template)
    if resolved is None:
        return lambda _day: False
    calendar = ctx.calendar()
    return lambda day: day in occurrences(resolved, calendar, through=day)


def follow_the_switch_or_ask(
    template: TransferTemplate, change: RepeatChange, door: PaymentsDoor,
    ctx: BalanceContext,
) -> Response | str | None:
    """Move the card's unpaid payments onto the repeat a switch just wrote.

    Called by the payment door (:mod:`app.routes.card.payment`) with
    :func:`app.services.card_payoff.switch_payment`'s write made and
    uncommitted, when it rewrote the repeat, so a refusal or a question rolls
    the switch back with everything else.  The pass is the door's own, built
    before its write: it memoizes a rule's resolution by what the rule says
    (``switch_payment``'s docstring), so it walks the rewritten repeat.

    Args:
        template: The card's payment.
        change: The repeats either side of the switch.
        door: The door (:class:`PaymentsDoor`).
        ctx: The door's read pass.

    Returns:
        The chooser page or a refusal's redirect, each with the write rolled
        back; or ``None`` when the caller should commit.
    """
    # No baseline scenario: no pass can run, as at every door that
    # regenerates; and a payment that does not recur (archived) has no rows
    # for one to write, as at the terms door.
    if ctx.scenario is None or not definition_recurs(template):
        return None
    framing = (
        _SCHEDULED_FRAMING if follows_card_due_dates(change.after.unit)
        else _PAYOFF_FRAMING
    )
    dates = MovedDates(
        _fires_on(change.before, template, ctx),
        _fires_on(change.after, template, ctx),
    )
    return _follow_or_ask([template], _Change(dates, framing), door, ctx)
