"""The balance sheet renders for an owner holding a correction and no pay schedule.

Finding plan#2875: the sheet folds each line of the worked-out ledger by its
key, day and amount and reads no paycheck, yet the read filed every opening and
true-up in a paycheck and asked the owner's pay calendar to do it.  So an owner
holding a correction and no pay schedule, which ``calendar_for`` refuses, got
the pay-calendar recovery handler (``app/error_handlers.py``), which answers a
safe-method htmx request with ``204`` and no body, where they had a sheet to
read.  No owner is in that state today (account creation refuses an owner with
no pay periods); this case builds it by stripping the seed owner's schedule.
"""

import re

import pytest

from app.services.pay_calendar import PayCalendarError, calendar_for
from tests._test_helpers import strip_owner_schedule

#: One statement row: its label cell, then its amount cell
#: (``analytics/_statement_macros.html``).
_ROW = re.compile(
    r"<td(?: class=\"fw-bold\")?>([^<]+)</td>\s*<td class=\"text-end[^\"]*\">([^<]+)</td>",
)


class TestAnOwnerWithNoPayScheduleGetsTheirSheet:
    """The finding's owner, rendered through the route the Statements pill loads."""

    def test_the_sheet_renders_the_opening_and_ties_out(
        self, app, auth_client, seed_user, db,
    ):
        """Checking opened at $1,000.00, no pay schedule: the sheet, not the recovery page.

        The seed owner's Checking opens at ``$1,000.00`` on a 2024 day, so an
        as-of-today sheet holds its opening: Checking ``$1,000.00`` in Assets
        against its equity twin "Checking -- Opening" ``$1,000.00``, and the
        tie-out closes (``1,000.00 == 0.00 + 1,000.00``).  The CONTROL: the
        owner really holds no schedule, so ``calendar_for`` refuses them.
        """
        with app.app_context():
            owner_id = seed_user["user"].id
            strip_owner_schedule(db.session, owner_id)
            db.session.commit()
            with pytest.raises(PayCalendarError):
                calendar_for(owner_id)

        resp = auth_client.get(
            "/analytics/balance-sheet", headers={"HX-Request": "true"},
        )

        assert resp.status_code == 200
        rows = dict(_ROW.findall(resp.data.decode()))
        assert rows["Checking"] == "$1,000.00"
        assert rows["Checking -- Opening"] == "$1,000.00"
        assert rows["Total Assets"] == "$1,000.00"
        assert "In balance" in resp.data.decode()
