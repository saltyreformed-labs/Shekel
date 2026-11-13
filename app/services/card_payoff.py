"""
Shekel Budget App -- a card's one payment, made with the card

Plan step **credit_card:CC-6**, task plan#2933 (rulings plan#2888, plan#2926,
plan#2669): **every card has one payment**, a payoff row each payday from the
owner's primary account covering everything on the card, made by the door that
brings the card into being.  The amount is the card's to work out
(:attr:`~app.enums.CardPaymentModeEnum.CARD_BALANCE`, which reads no terms, so
a card with none works): the rows store no figure, and a row reads $0.00 when
nothing is owed.

**It stops and starts with the card** (task plan#3697): the account's archive
and unarchive doors (:mod:`app.routes.accounts.lifecycle`) archive and restore
it through the transfers' own acts, and an archived account re-typed into a
card has the payment made here archived at once.

**Two calls, because the refusals come before any write.**  :func:`plan_payoff`
answers who pays and what the payment is called, or refuses with a sentence,
while nothing is written yet: a card the owner cannot pay from anywhere is not
made at all, and the database's own refusals (``uq_transfer_templates_user_name``,
the from-side rule ``transfer_service`` holds) are never what an owner meets.
:func:`make_payoff` then writes the payment for a card that exists.  The doors
are account creation and a re-type into a card; both call these two and nothing
else, so the two cannot come to disagree about what a card's payment is.

**The payer is the primary**, the account the grid opens on
(:func:`app.services.account_resolver.resolve_grid_account` with no override,
the bill form's default payer for the same reason, plan#2931): the caller
resolves it before the card's write and hands it in, as the read pass is the
caller's.  It must be able to pay a card, so a card is refused rather than
replaced by a guess, and so is the account a re-type would make a card while
the grid opens on it: the owner picks the grid's account, not the re-type.

**It starts in the current paycheck** (plan#2669): the repeat starts on the
pass's day, and a pay-period repeat normalises that onto the paycheck holding
it, so its first occurrence is that paycheck's payday.  No row lands on or
before an account's books opening (rulings R-PC85, R-PC86), so the first row
follows from when the card's books open.  A card added today opens today, so
its first row is the next payday: measured through the create door, a card
added on Nov 13 owing $500.00 in the paycheck that began Nov 9 is paid
$500.00 on Nov 23, then $0.00 (``test_the_card_payoff``).  An account re-typed
into a card is paid from the current paycheck's payday when its books opened
before that payday, as a payment set up by hand is, and from the next when they
opened later.

Boundary discipline: no Flask import; the caller owns the transaction.
"""

from dataclasses import dataclass

from app import ref_cache
from app.enums import CardPaymentModeEnum, RecurrenceUnitEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import Account
from app.models.card_payment_settings import CardPaymentSettings
from app.models.transfer_template import TransferTemplate
from app.services import transfer_recurrence
from app.services.account_projection import is_revolving
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.recurrence import RecurrenceSpec, author_rule

#: The payment's name: plain words with the card's own name in them, never an
#: id (plan#2933).
PAYOFF_NAME = "Pay off {card}"

#: The refusal when the owner has no account the grid can open on.
NO_ACCOUNT_TO_PAY_FROM = (
    "'{card}' needs an account to pay it from: a card's payment comes out of "
    "a checking or savings account each payday, and you have no active one. "
    "Add one first."
)

#: The refusal when the primary is a card.
PRIMARY_CANNOT_PAY_A_CARD = (
    "'{card}' needs an account to pay it from: a card's payment comes out of "
    "the account the grid opens on, and that is '{primary}', which is a card. "
    "Choose a checking or savings account as the grid's account in Settings "
    "first."
)

#: The refusal when a re-type would make the primary itself a card.
PRIMARY_CANNOT_BECOME_A_CARD = (
    "'{card}' cannot become a credit card while the grid opens on it: a "
    "card's payment comes out of the account the grid opens on each payday. "
    "Choose another checking or savings account as the grid's account in "
    "Settings first."
)

#: The refusal when the payment's name is taken by another recurring transfer.
PAYOFF_NAME_TAKEN = (
    "'{card}' pays off through a recurring transfer named '{name}', and one "
    "of that name already exists. Rename that transfer first."
)


@dataclass(frozen=True)
class Payoff:
    """What a card's payment will be, settled before anything is written.

    Attributes:
        payer: The account the payment comes out of, the owner's primary.
        name: The payment's name, :data:`PAYOFF_NAME` for the card.
    """

    payer: Account
    name: str


def card_payment_on(card_id: int) -> CardPaymentSettings | None:
    """Return the worked-out payment naming *card_id*, or ``None``.

    At most one can (``uq_card_payment_settings_card_account_id``), and an
    archived one still does: its settings row stays.

    Args:
        card_id: ``budget.accounts.id`` of the card, owner-checked by the
            caller.

    Returns:
        The :class:`CardPaymentSettings` row, or ``None`` when no payment
        names the account.
    """
    return (
        db.session.query(CardPaymentSettings)
        .filter_by(card_account_id=card_id)
        .one_or_none()
    )


def plan_payoff(
    user_id: int,
    card_name: str,
    primary: Account | None,
    *,
    card_id: int | None = None,
) -> Payoff:
    """Return who pays a card and what its payment is called, or refuse.

    Asked before the card's own write, so a refusal leaves nothing behind.

    Args:
        user_id: The card's owner.
        card_name: The card's name, as it will be (a create) or is (a re-type).
        primary: The owner's primary account, resolved by the caller; ``None``
            when the owner has none.
        card_id: The account becoming a card, for a re-type; ``None`` for a
            card not yet made.  The primary may be that account, which a
            re-type is about to make a card.

    Returns:
        The :class:`Payoff` :func:`make_payoff` writes.

    Raises:
        ValidationError: When there is no primary, when the primary is the
            account becoming a card or is a card, or when the payment's name
            is already a recurring transfer's.
    """
    if primary is None:
        raise ValidationError(NO_ACCOUNT_TO_PAY_FROM.format(card=card_name))
    if primary.id == card_id:
        raise ValidationError(PRIMARY_CANNOT_BECOME_A_CARD.format(card=card_name))
    if is_revolving(primary):
        raise ValidationError(PRIMARY_CANNOT_PAY_A_CARD.format(
            card=card_name, primary=primary.name,
        ))
    name = PAYOFF_NAME.format(card=card_name)
    taken = db.session.query(
        db.session.query(TransferTemplate)
        .filter_by(user_id=user_id, name=name)
        .exists()
    ).scalar()
    if taken:
        raise ValidationError(PAYOFF_NAME_TAKEN.format(card=card_name, name=name))
    return Payoff(payer=primary, name=name)


def make_payoff(
    card: Account, payoff: Payoff, ctx: BalanceContext,
) -> TransferTemplate:
    """Write *card*'s payment: its definition, its settings, its repeat, its rows.

    Each payday from the paycheck holding the pass's day (plan#2669), the
    whole balance worked out, no figure stated (plan#2667).  Built in the
    order the doors build a definition: the template and its settings, then
    the repeat authored onto it, then the rows ``transfer_recurrence`` writes
    through ``transfer_service``.

    Args:
        card: The card, flushed, of a revolving type.
        payoff: What :func:`plan_payoff` settled for it.
        ctx: The caller's read pass for the card's owner, built after the card
            exists.

    Returns:
        The flushed payment :class:`TransferTemplate`.

    Raises:
        ValidationError: From ``transfer_service`` when a row cannot be made.
        NotFoundError: From ``transfer_service`` when an account it names is
            gone.
    """
    template = TransferTemplate(
        user_id=card.user_id,
        from_account_id=payoff.payer.id,
        to_account_id=card.id,
        name=payoff.name,
    )
    template.card_payment_settings = CardPaymentSettings(
        user_id=card.user_id,
        mode_id=ref_cache.card_payment_mode_id(CardPaymentModeEnum.CARD_BALANCE),
    )
    db.session.add(template)
    db.session.flush()
    author_rule(
        RecurrenceSpec(
            user_id=card.user_id,
            unit=RecurrenceUnitEnum.PERIOD,
            starts_on=ctx.as_of,
        ),
        ctx.calendar(),
        template,
    )
    transfer_recurrence.generate_for_template(
        template, GenerationSchedule.for_pass(ctx), ctx.scenario_id,
    )
    return template
