"""
Shekel Budget App -- today, for a row on a card

**Ruling plan#2965**: a row on a card with no date typed takes today's date once
its paycheck has started.  **Ruling plan#2976**: a row moved on a card keeps or
takes its date by today.  :mod:`app.services.one_off` states both rules and reads no
clock, so each door that adds or moves a row resolves the day here: the grid's two
create doors (``routes/transactions/create.py``), the transfer form's one-time
transfer (``routes/transfers/_instances.py``), the popover's move
(``routes/transactions/_field_updates.py``), the transfer door
(``transfer_service._placed``) and Carry Forward (``carry_forward_service``).  It is
a service because the last two are.
"""

from datetime import date

from app.models.account import Account
from app.services.account_projection import is_revolving
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
