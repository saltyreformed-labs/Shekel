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

**It switches to a payment scheduled with the card company and back**
(task plan#2679; rulings plan#2888, plan#2889): the same payment, its
settings' mode changed and its repeat rewritten when the repeat's kind changes
(:func:`switch_payment`), so the one-per-card key that held the payoff row's
place holds the scheduled payment's.  A card has one of four payments
(:data:`PAYMENT_CHOICES`): payoff rows, or the statement balance, the minimum
or everything on the card on each statement's due date.  The rows follow
through the maintain pass, which is the door's to run, because a row the
owner typed over is asked about first.

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
from app.services.card_terms import is_configured_card
from app.services.generation_schedule import GenerationSchedule
from app.services.recurrence import (
    RecurrenceSpec,
    author_rule,
    follows_card_due_dates,
    reauthor_rule,
    recurrence_spec,
)

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

#: The refusal of a scheduled payment on a card with no terms: its dates are
#: the statements' due dates, which the terms decide.
SCHEDULED_NEEDS_TERMS = (
    "'{card}' has no terms yet, so it has no statement due dates for a "
    "scheduled payment to fall on. Save the card's terms first."
)

#: The refusal of a change to an archived card's payment, archived with it
#: (task plan#3697).
ARCHIVED_CARD_PAYMENT = (
    "'{card}' is archived, and its payment with it. Restore the card to "
    "change its payment."
)


@dataclass(frozen=True)
class PaymentShape:
    """One payment a card can have: how much, and on which repeat.

    Attributes:
        mode: The amount the card works out.
        unit: The repeat: each payday (``PERIOD``) or each statement's due
            date (``DUE_DATE``), always every 1.
    """

    mode: CardPaymentModeEnum
    unit: RecurrenceUnitEnum

    @property
    def is_scheduled(self) -> bool:
        """Return whether this is a payment scheduled with the card company.

        Returns:
            ``True`` when it falls on each statement's due date.
        """
        return follows_card_due_dates(self.unit)


#: Payoff rows: everything on the card, each payday (rulings plan#2888,
#: plan#2910), the payment a card is made with.
PAYOFF_ROWS = PaymentShape(CardPaymentModeEnum.CARD_BALANCE, RecurrenceUnitEnum.PERIOD)

#: The four payments a card can have, by the token the card's page posts:
#: payoff rows, or one of the three amounts on each statement's due date, as
#: the owner scheduled it with the card company (rulings plan#2888,
#: plan#2889).
PAYMENT_CHOICES: dict[str, PaymentShape] = {
    "payoff": PAYOFF_ROWS,
    "statement": PaymentShape(
        CardPaymentModeEnum.STATEMENT_BALANCE, RecurrenceUnitEnum.DUE_DATE,
    ),
    "minimum": PaymentShape(
        CardPaymentModeEnum.MINIMUM_PAYMENT, RecurrenceUnitEnum.DUE_DATE,
    ),
    "everything": PaymentShape(
        CardPaymentModeEnum.CARD_BALANCE, RecurrenceUnitEnum.DUE_DATE,
    ),
}


@dataclass(frozen=True)
class RepeatChange:
    """A switch that rewrote the payment's repeat, as the repeats either side.

    Attributes:
        before: The repeat the payment had, or ``None`` when it had none.
        after: The repeat it has now, as stored.
    """

    before: RecurrenceSpec | None
    after: RecurrenceSpec


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


def card_ids_with_a_payment(accounts: list[Account]) -> frozenset[int]:
    """Return the ids of *accounts* that are cards with a payment of their own.

    What the transfer forms say beside a destination (task plan#4046): a
    transfer into such a card is an extra payment, which the card's own
    payment works out what is left after (ruling R-CC22).  Read off the
    settings rows, the fact the sentence states, in one query; every active
    card has one (task plan#2933), and an archived card's payment keeps its
    row (:func:`card_payment_on`).

    Args:
        accounts: The accounts a form offers, the owner's.

    Returns:
        The ids of those a card payment names.
    """
    return frozenset(
        card_id for (card_id,) in db.session.query(
            CardPaymentSettings.card_account_id,
        ).filter(
            CardPaymentSettings.card_account_id.in_(
                [account.id for account in accounts],
            ),
        )
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
        mode_id=ref_cache.card_payment_mode_id(PAYOFF_ROWS.mode),
    )
    db.session.add(template)
    db.session.flush()
    author_rule(
        _repeat_from_this_paycheck(card.user_id, PAYOFF_ROWS.unit, ctx),
        ctx.calendar(),
        template,
    )
    transfer_recurrence.generate_for_template(
        template, GenerationSchedule.for_pass(ctx), ctx.scenario_id,
    )
    return template


def _repeat_from_this_paycheck(
    user_id: int, unit: RecurrenceUnitEnum, ctx: BalanceContext,
) -> RecurrenceSpec:
    """Return a card payment's repeat, starting in the current paycheck.

    Ruling plan#2669: a passed payday or due date in the paycheck holding the
    pass's day is placed now.  A payday repeat states the day itself, which
    the repeat normalises onto that paycheck's payday.  A due-date repeat's
    first date is a bound, the first day it covers (``recurrence``'s
    ``_resolution`` says why), so it states that paycheck's first day, and a
    statement due earlier in the paycheck is paid now rather than a month
    later.  A day in no paycheck (past the saved schedule) covers from the day
    itself, as the payday repeat's own normalisation would find no paycheck
    to move it onto either.

    Args:
        user_id: The payment's owner.
        unit: Each payday or each statement's due date.
        ctx: The read pass, its day and calendar the owner's.

    Returns:
        The :class:`RecurrenceSpec` to author, every 1 and never ending.
    """
    starts_on = ctx.as_of
    if follows_card_due_dates(unit):
        period = ctx.calendar().period_containing(ctx.as_of)
        if period is not None:
            starts_on = period.start_date
    return RecurrenceSpec(user_id=user_id, unit=unit, starts_on=starts_on)


def current_choice(settings: CardPaymentSettings) -> str | None:
    """Return the :data:`PAYMENT_CHOICES` token of the payment *settings* names.

    Its mode and its repeat's kind together; ``None`` for a payment whose
    repeat is neither each payday nor each due date, or that has none.  No
    door makes one: the card's page is the only door that sets the repeat of
    a card's payment.

    Args:
        settings: The card's payment settings, its definition loaded.

    Returns:
        The token, or ``None``.
    """
    rule = settings.template.recurrence_rule
    if rule is None:
        return None
    shape = PaymentShape(
        ref_cache.card_payment_mode_member(settings.mode_id),
        recurrence_spec(rule).unit,
    )
    return next(
        (token for token, choice in PAYMENT_CHOICES.items() if choice == shape),
        None,
    )


def switch_refusal(card: Account, shape: PaymentShape) -> str | None:
    """Return why *card*'s payment cannot become *shape*, or ``None``.

    Asked before :func:`switch_payment` writes anything, so a refusal is a
    sentence rather than the database's.

    Args:
        card: The card, owner-checked and revolving.
        shape: The payment the owner chose.

    Returns:
        :data:`ARCHIVED_CARD_PAYMENT` for an archived card, whose payment is
        archived with it; :data:`SCHEDULED_NEEDS_TERMS` for a scheduled
        payment on a dormant card (its dates are the terms' due dates, and its
        statement and minimum amounts read the terms too); else ``None``.
    """
    if not card.is_active:
        return ARCHIVED_CARD_PAYMENT.format(card=card.name)
    if shape.is_scheduled and not is_configured_card(card):
        return SCHEDULED_NEEDS_TERMS.format(card=card.name)
    return None


def switch_payment(
    settings: CardPaymentSettings, shape: PaymentShape, ctx: BalanceContext,
) -> RepeatChange | None:
    """Make the card's payment *shape*: its mode, and its repeat when its kind changes.

    The rows are not touched here: they follow through the maintain pass,
    which the door runs so that rows the owner typed over can be asked about
    first.  A change of amount alone moves no row, because no row stores a
    worked-out figure (ruling plan#2667), so it needs no pass.

    The repeat is re-authored in place (``recurrence.reauthor_rule``), so the
    rule keeps its id and every row its lineage.  *ctx* may be the pass the
    door goes on to regenerate with: it memoizes a rule's resolution by what
    the rule says, never by its id
    (``balance_at._recurrence_memos.resolved_recurrence_of``), so a pass built
    before this write reads the rewritten repeat.

    Args:
        settings: The card's payment settings, its definition loaded.
        shape: The payment the owner chose, which :func:`switch_refusal`
            accepted.
        ctx: The read pass for the card's owner.

    Returns:
        The :class:`RepeatChange` when the repeat was rewritten, or ``None``
        when only the amount changed.
    """
    settings.mode_id = ref_cache.card_payment_mode_id(shape.mode)
    template = settings.template
    rule = template.recurrence_rule
    before = None if rule is None else recurrence_spec(rule)
    if before is not None and before.unit is shape.unit:
        return None
    spec = _repeat_from_this_paycheck(template.user_id, shape.unit, ctx)
    if rule is None:
        rule = author_rule(spec, ctx.calendar(), template)
    else:
        reauthor_rule(rule, spec, ctx.calendar())
    return RepeatChange(before=before, after=recurrence_spec(rule))
