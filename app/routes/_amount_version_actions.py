"""
Shekel Budget App -- Amount-history route actions shared by both template kinds

One act, two forms.  The recurring-transaction edit page and the
recurring-transfer edit page each render the price history
:mod:`app.services.template_amount_service` writes, and each needs a control
that WITHDRAWS one entry -- the correction path for a price stamped against the
wrong date, which restating the amount cannot fix (a restatement writes a
version at the date it names and leaves the mis-dated one standing).

The two routes differ only in which model they look the template up on and where
they redirect afterwards, so the act itself lives here rather than twice: the
refusal wording, the stale-flush handling and the success flash are one
implementation, exactly as :mod:`app.routes._recurrence_form_helpers` holds the
steps those two update routes share.

Route-layer module (leading underscore = route-internal) rather than a service
because it consumes Flask ``flash`` / ``redirect`` / ``url_for``;
``CLAUDE.md::Architecture`` keeps services isolated from Flask globals.  The
service owns the RULE (which entry may be withdrawn); this owns the response.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from logging import Logger

from flask import Response, flash, redirect, url_for

from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.routes._commit_helpers import (
    STALE_ACTION_MESSAGE,
    StaleConflictContext,
    commit_or_handle_stale,
)
from app.routes._redirect_target import RedirectTarget
from app.services import template_amount_service
from app.services.balance_at import BalanceContext
from app.services.cash_ledger import is_card_payment_definition
from app.services.obligations_aggregator import todays_price


# Why a withdrawal is refused, stated as the repair rather than as the rule --
# and stated so that it is TRUE for all three reasons it fires, which an
# adversarial review found it was not: it named the earliest-entry rule even for
# an id that does not exist.  The service refuses the EARLIEST entry (every date
# before the series is priced from it), an entry whose removal would CHANGE the
# current amount (that is a price change, not a record correction), and an id
# this template does not own -- which is indistinguishable from a missing one, so
# the message leaks no existence either way.
_REFUSED_MESSAGE: str = (
    "That amount history entry could not be removed. The earliest entry stays, "
    "because every date before it is priced from it, and an entry that would "
    "change the current amount is changed from the Amount field instead. To "
    "re-date an entry, record the amount at the date you want first, then "
    "remove this one."
)


@dataclass(frozen=True)
class AmountVersionAction:
    """Where one kind's amount-history withdrawal reports back to.

    Bundled because the three fields are one cohesive concept -- the calling
    route's identity -- and passing them separately takes
    :func:`withdraw_amount_version` past pylint's argument threshold for no
    design gain.

    Attributes:
        logger: The calling route module's logger, so a stale-flush conflict is
            attributed to ``app.routes.templates`` or
            ``app.routes.transfers.templates`` exactly as its siblings are.
        edit_endpoint: The kind's edit-form endpoint, resolved with
            ``template_id`` for every redirect out of this action.
        noun: How the kind is named to the user in the stale-conflict flash
            ("recurring transaction" / "recurring transfer").
    """

    logger: Logger
    edit_endpoint: str
    noun: str


def withdraw_amount_version(
    template, version_id: int, action: AmountVersionAction,
) -> Response:
    """Withdraw one entry from *template*'s amount history and report back.

    Ownership is the CALLER's ``get_or_404`` on the template: the service looks
    the version up inside that template's own collection, so a ``version_id``
    belonging to another user's template is simply not found and its refusal is
    indistinguishable from "no such entry" (the security response rule -- 404
    for both not-found and not-yours, worn here as one refusal message for
    both).

    Args:
        template: The owned transaction or transfer template.
        version_id: The amount-version primary key to withdraw.
        action: The calling route's identity (:class:`AmountVersionAction`).

    Returns:
        A redirect back to the kind's edit form -- carrying the refusal, the
        stale-conflict warning, or the success flash.
    """
    template_id = template.id
    back = RedirectTarget(action.edit_endpoint, {"template_id": template_id})

    if not template_amount_service.delete_amount_version(template, version_id):
        flash(_REFUSED_MESSAGE, "warning")
        return back.to_response()

    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=action.logger,
        log_label="withdraw_amount_version",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(noun=action.noun),
        redirect=back,
    ))
    if conflict is not None:
        return conflict

    flash("Amount history entry removed.", "success")
    return redirect(url_for(action.edit_endpoint, template_id=template_id))


@dataclass(frozen=True)
class _WorkedOutWords:
    """What the app says about one kind of definition whose amount is worked out.

    Attributes:
        source: The line the edit form shows in place of the Amount box,
            naming what works the amount out (ruling plan#2694).
        refusal: The flash for an edit that types an amount anyway.
    """

    source: str
    refusal: str


#: The words for a card's worked-out payment, the one transfer template that
#: does not own its amount and does not follow a loan.
_CARD_WORKS_IT_OUT = _WorkedOutWords(
    source="Your card works out this payment from what it owes.",
    refusal=(
        "Your card works out this payment from what it owes, so its amount "
        "cannot be typed here. To pay a different amount once, type over that "
        "payment on the grid."
    ),
)

#: The words for each kind of worked-out definition, keyed by its model.  A
#: transaction template that does not own its amount is a salary profile's; a
#: transfer template that does not is a loan payment in derive mode, or a
#: card's payment (:func:`_worked_out_words`;
#: ``template_amount_service.owns_its_amount``).
_WORKED_OUT_WORDS: dict[type, _WorkedOutWords] = {
    TransactionTemplate: _WorkedOutWords(
        source="Your salary profile works out this amount.",
        refusal=(
            "Your salary profile works out this amount, so it cannot be "
            "typed here. Change the salary profile to change it."
        ),
    ),
    TransferTemplate: _WorkedOutWords(
        source="This payment follows the loan.",
        refusal=(
            "This payment follows the loan, so its amount cannot be typed "
            "here. It changes when the loan's payment does."
        ),
    ),
}


def _worked_out_words(template) -> _WorkedOutWords:
    """Return what the app says about *template*'s worked-out amount.

    Args:
        template: A definition that does not own its amount.

    Returns:
        Its :class:`_WorkedOutWords`.
    """
    if is_card_payment_definition(template):
        return _CARD_WORKS_IT_OUT
    return _WORKED_OUT_WORDS[type(template)]


@dataclass(frozen=True)
class EditFormAmount:
    """What a recurring definition's edit form shows for its amount.

    The ONE decision behind both edit forms' Amount row (plan step X-bp,
    ruling plan#2694, fixing finding plan#2689): a definition that owns its
    price shows the Amount box, and one whose amount is worked out shows a
    line naming where it comes from, with no box.  The box was shown for
    both until then, prefilled for a worked-out one from its stored copy --
    or, where it had once owned a price (a payment fixed before it tracked
    its loan, a paycheck whose salary profile was archived and brought
    back), from that dormant history -- and saving the page wrote the box
    into the copy the Recurring page and the savings page then counted.

    Attributes:
        today: What the definition costs today.  For a definition that owns
            its price, the Amount box's prefill: its history on the owner's
            day (``template_amount_service.amount_as_of`` at
            ``display_today()``), the STATED price, never a loan payment's
            standing extra added to it, because saving the box restates it;
            ``None`` only for an empty history, which no door writes.  For a
            worked-out one, the figure its rows are priced at on the read
            pass's day, the Recurring page's
            (``obligations_aggregator.todays_price``).
        worked_out_by: ``None`` for a definition that owns its price;
            otherwise the line naming what works the amount out, shown with
            *today* in place of the box.
    """

    today: Decimal | None
    worked_out_by: str | None


def edit_form_amount(
    template, ctx: BalanceContext, today: date,
) -> EditFormAmount:
    """Return what *template*'s edit form shows for its amount.

    **Each branch reads the day its figure answers to, and they are two
    days.**  The Amount box is read on *today*, the owner's day: a save
    that leaves "Amount effective from" blank restates the box on that day
    (both update routes default the date to ``display_today()``), and the
    history panel beside the box names Current and Scheduled by it.  Read
    on any other day, a box the owner leaves alone restates a price nobody
    stated.  It was read on the read pass's day (``BalanceContext.build``'s
    default, the process clock's ``date.today()``) from plan step X-bp's
    task plan#2698 until plan#2732: where the process day ran ahead of the
    owner's (CI at UTC, a process without the ``TZ`` pin), a price
    scheduled for the owner's tomorrow prefilled the box, and a plain
    rename restated it as of the owner's today, repricing today's rows.
    The worked-out line keeps the read pass, because its figure is the
    Recurring page's (ruling plan#2693), priced by the same pass.

    Args:
        template: The owner-checked transaction or transfer template.
        ctx: The form's read pass, which prices a worked-out definition.
        today: The owner's day, ``display_today()``, resolved once by the
            route for this box and the history panel beside it.

    Returns:
        The :class:`EditFormAmount`.

    Raises:
        AmountUnresolvable: A worked-out definition its rows' rule cannot
            price today, from :func:`~app.services.obligations_aggregator.todays_price`,
            exactly where the Recurring page would refuse it too.
    """
    if template_amount_service.owns_its_amount(template):
        return EditFormAmount(
            today=template_amount_service.amount_as_of(template, today),
            worked_out_by=None,
        )
    return EditFormAmount(
        today=todays_price(template, ctx).amount,
        worked_out_by=_worked_out_words(template).source,
    )


def refuse_a_typed_worked_out_amount(
    template, data: dict, back: RedirectTarget,
) -> Response | None:
    """Refuse an edit that types an amount for a definition that works one out.

    The edit form shows no Amount box for such a definition
    (:func:`edit_form_amount`), so only a crafted or stale submission
    carries one.  Refused rather than ignored: dropping the figure and
    saving the rest would tell the owner their figure was taken when
    nothing prices from it.  And refused HERE, ahead of the write door:
    ``template_amount_service.set_amount`` raises ``ValueError`` for such a
    definition, which neither update route catches, so a figure reaching it
    would answer the owner with a server error.

    Args:
        template: The owner-checked template being edited.
        data: The validated payload; ``amount`` is the form's Amount box.
        back: Where the refusal sends the owner (the edit form).

    Returns:
        ``None`` when the payload states no amount or the definition owns
        its price; else the redirect, with the reason flashed.
    """
    if "amount" not in data or template_amount_service.owns_its_amount(template):
        return None
    flash(_worked_out_words(template).refusal, "danger")
    return back.to_response()


__all__ = [
    "AmountVersionAction",
    "EditFormAmount",
    "edit_form_amount",
    "refuse_a_typed_worked_out_amount",
    "withdraw_amount_version",
]
