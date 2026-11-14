"""Balance-at-T seam -- the cash fold's ESTIMATED tier, past the last saved paycheck.

Plan step ``pay_calendar:C9``, task plan#3941.  Past the owner's last saved
paycheck no row exists for a generate pass to have written, so the PLANNED
tier (:func:`~._cash_fold._cash_plan`) ends there and, until this tier, every
account's balance froze on that day while the loans it paid kept amortizing.
Three rulings say what replaces the freeze:

* **plan#3930**: past the last saved paycheck, money comes from the RECURRING
  ITEMS, each occurrence priced the way its row would be -- a paycheck through
  its salary profile, a bill at its stated amount, a loan payment stopping
  when the loan is paid off.  Worked out on each read; nothing is saved.
* **plan#3932**: only paychecks AFTER the last saved one are estimated
  (:attr:`~._definition_walk.Paychecks.PROJECTED`).  Inside the saved
  schedule the saved rows are the plan, so every balance step there stays
  explained by a row.  A loan payment's paying side follows the LOAN's own
  figures, so the loan and the account paying it agree.
* **plan#4036**: the estimate runs as far as each read asks.  No date is
  refused and none freezes, and no page states a range up front: a reader of
  the fold names the last day it samples
  (:meth:`~._cash_fold.AssembledCashFold.steps_through`) and the tier is
  worked out through that day.

**What an occurrence is, and what it is worth, are each asked of the code
generation asks**, so a rolling top-up that saves a projected paycheck and
populates it moves no balance on any date.  WHICH occurrences: the seam's one
definition walk (:func:`~._definition_walk.unwritten_occurrences`), the place,
claim and date steps generation takes.  WHAT each is worth: the amount model's
own arms, :func:`~app.services.cash_ledger.transaction_definition_cash` for a
transaction template and :func:`~app.services.cash_ledger.definition_cash`
for a transfer template.  WHEN it lands: the day its row would land, the
paycheck's :meth:`~app.services.pay_calendar.DerivedPeriod.attribution_day`
of its due date clamped up to ``as_of + 1`` (ruling R-G), the rule
:func:`~._cash_fold._cash_plan` applies to the row.

**A loan payment is the one definition this tier does not price.**  The loan's
forward plan already walks every recurring transfer paying it
(:func:`~._plan_definitions.estimated_from_definitions`) and prices each
occurrence through the same :func:`~app.services.cash_ledger.definition_cash`;
the paying account reads those payments back off that plan
(:attr:`~._plan_records.PlannedPayment.unwritten`), so the loan and its payer
read one figure from one walk.  Of them it debits the occurrences generation
would write: on a projected paycheck, and admitted by the stop the loan's
closing date states (:func:`~app.services.recurrence.stop_on_closing_date`,
the rule ``loan_recurrence_sync.loan_payment_window`` hands generation).  So
it stops at the payoff, where the loan's own plan carries later occurrences
as excess.

**One walk per definition per read pass** (:attr:`~._context.BalanceContext
.estimates`): a transfer's two accounts each read the one priced walk, so
their legs cannot disagree, and a later reader asking past the farthest day
walked so far re-walks that definition once to the new day.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.  Seam-PRIVATE.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services.cash_ledger import (
    DefinitionRow,
    TransactionDefinitionRow,
    definition_cash,
    transaction_definition_cash,
)
from app.services.recurrence import stop_on_closing_date
from app.services.recurring_transfer_query import (
    active_recurring_transfer_templates_on,
    destination_account,
)

from ._context import BalanceContext
from ._definition_walk import (
    Paychecks,
    UnwrittenOccurrence,
    unwritten_occurrences,
)
from ._loan_figures import loan_closing_date
from ._plan import memoized_plan
from ._resolution import resolved_loan

_ZERO_MONEY = Decimal("0.00")
# Ruling R-G's clamp floor, the rule the PLANNED tier's ``_ONE_DAY`` states
# for a row: an occurrence cannot have already happened.
_ONE_DAY = timedelta(days=1)


class PricedOccurrence(NamedTuple):
    """One occurrence a definition owes past the last saved paycheck, and its worth.

    Attributes:
        unwritten: The occurrence, the paycheck its row would live in, and
            the day its row would be due.
        cash: What its row would be worth, as a MAGNITUDE: the account it
            moves money out of subtracts it and the one it moves money into
            adds it.
    """

    unwritten: UnwrittenOccurrence
    cash: Decimal


@dataclass(frozen=True)
class PricedWalk:
    """One definition's priced occurrences on projected paychecks, through a day.

    The value :attr:`~._context.BalanceContext.estimates` holds per definition.

    Attributes:
        through: The last OCCURRENCE day the walk covered.
        occurrences: Every occurrence through it on a projected paycheck that
            no row answers, ascending, each priced.
    """

    through: date
    occurrences: "tuple[PricedOccurrence, ...]"


def priced_occurrences(
    template: "TransactionTemplate | TransferTemplate",
    ctx: BalanceContext,
    *,
    through: date,
) -> list[PricedOccurrence]:
    """Return *template*'s occurrences on projected paychecks through *through*, priced.

    Walked and priced at most once per read pass for the farthest day asked:
    a request inside a walk already held reads it, and a request past it
    walks the definition again to the new day and replaces it.  A walk is a
    prefix of any longer one (an occurrence's placement, claim and due date
    do not depend on how far the walk runs), so the occurrences through
    *through* are the held walk's, cut at that day.

    Args:
        template: A recurring transaction or transfer template, not a loan
            payment (see :func:`_loan_payments`).
        ctx: The read pass: its calendar places the occurrences, its scenario
            scopes the rows that answer them, its amount basis prices them.
        through: The last occurrence day to cover.

    Returns:
        The priced occurrences, ascending by occurrence.

    Raises:
        AmountUnresolvable: When the definition states no price for an
            occurrence -- exactly where its written row would refuse.
    """
    key = (type(template), template.id)
    held = ctx.estimates.get(key)
    if held is None or held.through < through:
        held = PricedWalk(
            through=through,
            occurrences=tuple(
                PricedOccurrence(unwritten, _price(template, unwritten, ctx))
                for unwritten in unwritten_occurrences(
                    template, ctx,
                    through=through, paychecks=Paychecks.PROJECTED,
                )
            ),
        )
        ctx.estimates[key] = held
    return [
        priced for priced in held.occurrences
        if priced.unwritten.occurrence <= through
    ]


def _price(
    template: "TransactionTemplate | TransferTemplate",
    unwritten: UnwrittenOccurrence,
    ctx: BalanceContext,
) -> Decimal:
    """Return what the row *unwritten* would become is worth, as a magnitude.

    The arm a written row of the same kind is priced through: a transaction
    template's through rule 2 or rule 3
    (:func:`~app.services.cash_ledger.transaction_definition_cash`), a
    transfer template's through rule 3 or rule 4
    (:func:`~app.services.cash_ledger.definition_cash`).

    Args:
        template: The definition.
        unwritten: The occurrence, placed and dated.
        ctx: The read pass whose amount basis prices it.

    Returns:
        The occurrence's cash.

    Raises:
        AmountUnresolvable: When the definition states no price for it.
    """
    subject = (
        f"Template {template.id}'s occurrence "
        f"{unwritten.occurrence.isoformat()}"
    )
    if isinstance(template, TransactionTemplate):
        return transaction_definition_cash(
            TransactionDefinitionRow(
                template=template,
                due_date=unwritten.due_date,
                period=unwritten.period,
            ),
            ctx.amounts(),
            subject=subject,
        )
    return definition_cash(
        DefinitionRow(
            template=template,
            due_date=unwritten.due_date,
            period_start=unwritten.period.start_date,
            to_account_id=template.to_account_id,
        ),
        ctx.amounts(),
        subject=subject,
    )


def _loan_payments(
    template: TransferTemplate,
    loan: Account,
    ctx: BalanceContext,
    *,
    through: date,
) -> list[PricedOccurrence]:
    """Return the loan payment *template*'s estimated occurrences, read off the loan's plan.

    The loan's forward plan holds every occurrence of every definition paying
    it, priced (:attr:`~._plan_records.PlannedPayment.unwritten`), so this
    reads the figure the loan folds rather than pricing the occurrence again
    (ruling plan#3932).  It keeps the ones generation would write and the
    cash tier estimates: on a PROJECTED paycheck, through *through*, and
    admitted by the stop the loan's closing date states, which is the stop
    generation applies (``loan_recurrence_sync.loan_payment_window``).

    The plan drops an occurrence due at or before the loan's latest balance
    assertion (ruling R-R72) and walks no further than its own window
    (``_plan.loan_plan``: past the contract's extension, and five years past
    the read); this follows the loan in both, so the account paying it is
    never debited for a payment the loan does not receive.

    Args:
        template: A recurring transfer template paying into *loan*.
        loan: Its destination, a configured loan.
        ctx: The read pass.
        through: The last occurrence day to cover.

    Returns:
        The priced occurrences, ascending by occurrence.
    """
    resolved = ctx.resolved_recurrence_of(template.recurrence_rule)
    if resolved is None:
        # An owner with no pay periods: the loan plan walked nothing either.
        return []
    stop = stop_on_closing_date(loan_closing_date(loan, ctx), resolved.starts_on)
    return [
        PricedOccurrence(payment.unwritten, payment.cash)
        for payment in memoized_plan(loan, ctx).payments
        if payment.unwritten is not None
        and payment.unwritten.template_id == template.id
        and payment.unwritten.period.is_projected
        and payment.unwritten.occurrence <= through
        and stop.admits(payment.unwritten.occurrence)
    ]


def _recurring_transaction_templates(
    account_id: int, user_id: int,
) -> list[TransactionTemplate]:
    """Return *account_id*'s active recurring transaction templates, oldest first.

    The definitions a generate pass writes rows on the account from: active
    (``is_active``; an archived one names no occurrence) and carrying a
    recurrence rule.  The price series is eager-loaded because every
    occurrence of a stated-price template reads it.

    Args:
        account_id: The account the templates' rows live on.
        user_id: The pass's owner, scoping the query.

    Returns:
        The templates, ascending by id.
    """
    return (
        db.session.query(TransactionTemplate)
        .options(joinedload(TransactionTemplate.amount_versions))
        .filter(
            TransactionTemplate.user_id == user_id,
            TransactionTemplate.account_id == account_id,
            TransactionTemplate.is_active.is_(True),
            TransactionTemplate.recurrence_rule.has(),
        )
        .order_by(TransactionTemplate.id)
        .all()
    )


class EstimatedCashTier:
    """One account's ESTIMATED tier, worked out as far as a reader asks.

    Held by the account's :class:`~._cash_fold.AssembledCashFold`, which asks
    it for the days through the last one a reader samples.  The account's
    definitions are loaded on the first ask that reaches past the last saved
    paycheck and kept for the pass, so a read that stays inside the saved
    schedule issues no query for them.

    Attributes:
        account: The account the tier is for.
        ctx: The read pass the fold was assembled in.
    """

    def __init__(self, account: Account, ctx: BalanceContext) -> None:
        """Bind the tier to its account and read pass.

        Args:
            account: The account, already bound to *ctx*'s owner by the
                fold's own memo.
            ctx: The read pass.
        """
        self.account = account
        self.ctx = ctx
        self._definitions: (
            "tuple[list[TransactionTemplate], list[TransferTemplate]] | None"
        ) = None

    def day_nets_through(self, last_day: date) -> "dict[date, Decimal]":
        """Return ``{day: net}`` for every estimated occurrence landing by *last_day*.

        Empty, with nothing walked, when *last_day* is at or before both the
        last saved paycheck's end and the pass's ``as_of``: an estimate sits
        on a projected paycheck, so it lands no earlier than that paycheck's
        first day, and no earlier than ``as_of + 1`` (ruling R-G).

        **The walk reaches the END of the paycheck holding *last_day*.**  An
        occurrence is placed on the paycheck containing it or the first one
        starting on or after it, so its paycheck ends on or after it; its row
        lands inside that paycheck or later.  A row landing by *last_day* is
        therefore on the paycheck holding *last_day* or an earlier one, and
        its occurrence falls on or before that paycheck's end.

        Args:
            last_day: The last day a reader of the fold samples.

        Returns:
            One entry per landing day carrying at least one estimated
            occurrence, signed for this account (money in positive), and
            ``{}`` when none lands by *last_day*.

        Raises:
            AmountUnresolvable: When a definition states no price for an
                occurrence -- exactly where its written row would refuse.
        """
        calendar = self.ctx.calendar()
        horizon = calendar.horizon()
        if horizon is None or last_day <= max(horizon, self.ctx.as_of):
            return {}
        through = calendar.span_containing(last_day).end_date
        not_before = self.ctx.as_of + _ONE_DAY
        nets: "dict[date, Decimal]" = {}
        for signed, unwritten in self._signed_occurrences(through):
            lands_on = max(
                unwritten.period.attribution_day(unwritten.due_date),
                not_before,
            )
            if lands_on <= last_day:
                nets[lands_on] = nets.get(lands_on, _ZERO_MONEY) + signed
        return nets

    def _signed_occurrences(
        self, through: date,
    ) -> "list[tuple[Decimal, UnwrittenOccurrence]]":
        """Return every occurrence through *through* the account's definitions owe, signed.

        Args:
            through: The last occurrence day to cover.

        Returns:
            ``(signed cash, occurrence)`` per occurrence: a transaction
            template's added when it is income and subtracted otherwise, a
            transfer template's subtracted on the side it leaves and added on
            the side it enters.
        """
        transactions, transfers = self._loaded_definitions()
        income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        signed: "list[tuple[Decimal, UnwrittenOccurrence]]" = []
        for template in transactions:
            direction = 1 if template.transaction_type_id == income_type_id else -1
            signed.extend(
                (direction * priced.cash, priced.unwritten)
                for priced in priced_occurrences(
                    template, self.ctx, through=through,
                )
            )
        for template in transfers:
            direction = -1 if template.from_account_id == self.account.id else 1
            signed.extend(
                (direction * priced.cash, priced.unwritten)
                for priced in self._transfer_occurrences(template, through)
            )
        return signed

    def _transfer_occurrences(
        self, template: TransferTemplate, through: date,
    ) -> list[PricedOccurrence]:
        """Return a transfer template's priced occurrences, from the loan when it pays one.

        A template paying a configured loan is one of that loan's definitions
        (ruling **R-R35**: every recurring transfer into a loan pays it), and
        its occurrences are read off the loan's plan; any other is walked and
        priced here.

        Args:
            template: The recurring transfer template.
            through: The last occurrence day to cover.

        Returns:
            The priced occurrences, ascending by occurrence.
        """
        destination = destination_account(template)
        if destination is not None and resolved_loan(destination, self.ctx) is not None:
            return _loan_payments(template, destination, self.ctx, through=through)
        return priced_occurrences(template, self.ctx, through=through)

    def _loaded_definitions(
        self,
    ) -> "tuple[list[TransactionTemplate], list[TransferTemplate]]":
        """Return the account's recurring definitions, loading them on the first ask.

        Returns:
            ``(transaction templates on the account, transfer templates from
            or to it)``, each active, recurring and ascending by id.
        """
        if self._definitions is None:
            self._definitions = (
                _recurring_transaction_templates(
                    self.account.id, self.ctx.user_id,
                ),
                active_recurring_transfer_templates_on(
                    self.account.id, self.ctx.user_id,
                ),
            )
        return self._definitions


__all__ = [
    "EstimatedCashTier",
    "PricedOccurrence",
    "PricedWalk",
    "priced_occurrences",
]
