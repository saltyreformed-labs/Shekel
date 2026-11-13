"""Balance-at-T seam -- the doors to a card's worked-out payment price.

Plan step ``credit_card:CC-6``.  :mod:`._card_payment` holds the rule, which
:mod:`._cash_fold` imports to assemble a card's fold; these read the answer off
the pass's fold, so they import that module and it imports nothing of them.
Their own module for that one arrow.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes.
"""

from decimal import Decimal
from typing import TYPE_CHECKING

from app.exceptions import AmountUnresolvable
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate

from ._cash_fold import assembled_fold

if TYPE_CHECKING:
    from ._card_payment import NextCardPayment
    from ._context import BalanceContext


def card_payment_price(xfer: Transfer, ctx: "BalanceContext") -> Decimal:
    """Return what the card works out for one of its payment rows.

    What :meth:`~._context.BalanceContext.amounts` hands the amount model, and
    the one answer every reader of the row reads.

    **The scenario is refused here as well as in the amount model**, because
    only this function knows which scenario the fold is.  The amount model's
    check compares the row with the BASIS, and carry-forward's basis names the
    scenario it carries within while holding this deriver: a row of that
    scenario passes the check, and this pass would answer from its own rows.

    Args:
        xfer: The row: a transfer into the card, on any status.
        ctx: The read pass whose fold of the card answers.

    Returns:
        The row's price.

    Raises:
        ForeignAccountError: The card belongs to another owner.
        BaselineMissingError: This pass has no baseline scenario.
        AmountUnresolvable: The row is in another scenario than this pass, or
            see :meth:`~._card_payment.CardPayments.price_of`.
    """
    if xfer.scenario_id != ctx.scenario_id:
        raise AmountUnresolvable(
            f"Transfer {xfer.id} is in scenario {xfer.scenario_id} and this "
            f"read pass folds scenario {ctx.scenario_id}. A card works out its "
            "payment from one scenario's rows, so answering here would price "
            "it from another's."
        )
    return assembled_fold(xfer.to_account, ctx).card_payments.price_of(xfer)


def next_card_payment(
    template: TransferTemplate, ctx: "BalanceContext",
) -> "NextCardPayment | None":
    """Return a card's worked-out payment's next payment: its day and its figure.

    What the Recurring page shows for the definition, its Next column and its
    Amount from this one answer (plan tasks plan#3700 and plan#3894), and the
    figure the Archived drawer and the definition's edit form show.  **Its next
    rows, never a row imagined on the pass's day**: such a row reads ``0.00`` in
    statement mode from each due date until the next close, which says nothing
    about what the payment costs.  The next is the first day one of its rows
    still planned is dated on or after the pass's day
    (:meth:`~._card_payment.CardPayments.next_payment`), so an earlier row still
    unmarked does not hold the figure.

    Args:
        template: The definition, carrying card-payment settings.
        ctx: The read pass, whose day the next row is dated on or after.

    Returns:
        The :class:`~._card_payment.NextCardPayment`, or ``None`` with no row
        dated on or after the pass's day.

    Raises:
        ForeignAccountError: The card belongs to another owner.
        BaselineMissingError: This pass has no baseline scenario: a card's
            balance is folded over one scenario's rows.
        AmountUnresolvable: See
            :meth:`~._card_payment.CardPayments.price_of`.
    """
    return assembled_fold(
        template.to_account, ctx,
    ).card_payments.next_payment(template.id, ctx.as_of)
