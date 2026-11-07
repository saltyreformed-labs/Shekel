"""
Shekel Budget App -- today, for a row on a card, and the row a move re-dated

**Ruling plan#2965**: a row on a card with no date typed takes today's date once
its paycheck has started.  **Ruling plan#2976**: a row moved on a card keeps or
takes its date by today.  :mod:`app.services.one_off` states both rules and reads no
clock, so each door that adds or moves a row resolves the day here: the grid's two
create doors (``routes/transactions/create.py``), the transfer form's one-time
transfer (``routes/transfers/_instances.py``), the popover's move
(``routes/transactions/_field_updates.py``), the transfer door
(``transfer_service._placed``) and Carry Forward (``carry_forward_service``).  It is
a service because the last two are.

**Ruling plan#2991**: a repeating row on a card moves by the same rule, except a
worked-out payment, whose date is what prices it (ruling plan#2671).
:func:`card_today_of_repeating_row` is which rows that is.  Such a move writes the
row's date and never the occurrence it answers, and it raises the row's hold
(``is_override``), which is what keeps every pass off that date.  Nothing stored says
"moved", so :func:`date_left_by_a_move` tells a moved row from a typed-over one by where
the row sits and what it is dated.  It opens the row's date box and decides what the
conflict chooser's "use" hands back.  It lives below :mod:`app.services.one_off` because
the engine's chooser asks it, and ``one_off`` imports the engine.
"""

from datetime import date

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.account_projection import is_revolving
from app.services.pay_calendar import PayCalendar, calendar_for
from app.services.recurrence import compute_due_date, period_of_occurrence
from app.utils.dates import display_today


def today_on_a_card(account: Account, *, is_envelope: bool = False) -> date | None:
    """Return the owner's day when *account* is a card that counts the row, else ``None``.

    What a door hands :mod:`app.services.one_off` as its ``card_today``, so that "is
    this row on a card" is asked one way at every door.

    Args:
        account: The account the row is on, or a transfer's destination, with its
            ``account_type`` loaded (:func:`~app.services.account_projection.is_revolving`).
        is_envelope: Whether the row is an envelope.  No card counts from an envelope's
            date (``balance_at._cash_plan._day_it_counts_from``), so it takes the rule
            a row off a card takes.

    Returns:
        :func:`~app.utils.dates.display_today` for a row a card counts from its date,
        ``None`` for any other.
    """
    return display_today() if is_revolving(account) and not is_envelope else None


def card_today_of_repeating_row(
    row: Transaction | Transfer, *, account: Account | None = None,
) -> date | None:
    """Return the owner's day when a move re-dates repeating *row*, else ``None``.

    **Ruling plan#2991**: a repeating bill or a standing transfer on a card.  ``None``
    for a row with no rule, off a card, an envelope (:func:`today_on_a_card`), and a
    worked-out payment, whose date is what prices it (ruling plan#2671).

    Args:
        row: The row.
        account: For a transfer, the account the save leaves it paying into, when an
            endpoint move in the same save changes it; ``None`` reads the row's own.

    Returns:
        The owner's day, or ``None`` when a move leaves this row's date alone.
    """
    if not row.recurs:
        return None
    if isinstance(row, Transfer):
        if row.template.card_payment_settings is not None:
            return None
        return today_on_a_card(row.to_account if account is None else account)
    return today_on_a_card(row.account, is_envelope=row.tracks_purchases)


def date_left_by_a_move(row: Transaction | Transfer, calendar: PayCalendar) -> bool:
    """Whether held, repeating *row* was moved rather than only typed over.

    **Only a held row can be**, because a move raises the hold and a row with none is the
    rule's: its next pass dates it, whatever its paycheck.  Read by position alone this
    froze every row of a rule whose funding was edited.  Among held rows, read from what
    is stored, since a move writes no marker and the hold is also a typed-over row's:

    * **It sits outside the paycheck its occurrence falls in.**  Its date may be the
      rule's, as a bought bill carried forward keeps it.
    * **It sits in that paycheck on another date**: moved away and back with a date that
      had passed, which stays.

    **What it cannot tell.**  A row typed over and never moved reads ``True`` once its
    rule's funding is edited, because the paycheck the rule names moved out from under
    it.  Its date box opens, which it need not use, and "use" hands back its figure and
    leaves its date (:attr:`~app.services.definition_unarchive.UseRestore.keep_their_hold`).

    ``False`` for a row answering no occurrence, which has nothing to compare, and a row
    :func:`card_today_of_repeating_row` leaves alone.

    Args:
        row: The row, its ``template`` and rule loaded or loadable.
        calendar: The owner's calendar.

    Returns:
        ``True`` when the row's date is the owner's to type.
    """
    if (
        not row.is_override
        or row.occurs_on is None
        or card_today_of_repeating_row(row) is None
    ):
        return False
    rule = row.template.recurrence_rule
    own = period_of_occurrence(rule, row.occurs_on, calendar)
    if own is None or row.pay_period_id != own.period_id:
        return True
    return row.due_date != compute_due_date(rule, row.occurs_on, own)


def a_save_may_date(row: Transaction | Transfer, period_id_after: int) -> bool:
    """Whether a save leaving repeating *row* in *period_id_after* may state its date.

    The two PATCH doors' backstop for the box their popovers render.  **Ruling
    plan#2991**: true where the save moves the row, because a date typed beside a move
    stands, and where an earlier move left its date.

    Args:
        row: A repeating row, before any write.
        period_id_after: The paycheck the save leaves it in.

    Returns:
        ``True`` when the save's date is the owner's to state.
    """
    if card_today_of_repeating_row(row) is None:
        return False
    if period_id_after != row.pay_period_id:
        return True
    return date_left_by_a_move(row, calendar_for(row.user_id))
