"""
Shekel Budget App -- today, for a row the owner adds to a card

**Ruling plan#2965**: a row on a card with no date typed takes today's date once
its paycheck has started.  :func:`app.services.one_off.due_date_for` states that
rule and reads no clock, because services take plain data
(``CLAUDE.md``, Architecture), so each door that adds a row resolves the day here:
the grid's two create doors (``routes/transactions/create.py``) and the transfer
form's one-time transfer (``routes/transfers/_instances.py``).
"""

from datetime import date

from app.models.account import Account
from app.services.account_projection import is_revolving
from app.utils.dates import display_today


def today_on_a_card(account: Account) -> date | None:
    """Return the owner's day when *account* is a card, else ``None``.

    What a door hands :func:`~app.services.one_off.due_date_for` as its
    ``card_today``, so that "is this row on a card" is asked one way at every door.

    Args:
        account: The account the row is on, or a transfer's destination, with its
            ``account_type`` loaded (:func:`~app.services.account_projection.is_revolving`).

    Returns:
        :func:`~app.utils.dates.display_today` on a card, ``None`` anywhere else.
    """
    return display_today() if is_revolving(account) else None
