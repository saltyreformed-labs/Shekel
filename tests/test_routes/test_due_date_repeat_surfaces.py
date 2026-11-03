"""
Shekel Budget App -- a due-date repeat's dates on every surface that walks it

Plan step **credit_card:CC-6**, its task plan#2787.  The read pass attaches a
card's due dates to a repeat on each statement's due date
(``BalanceContext.resolved_for``; the service half is
``tests/test_services/test_due_date_repeat_read_pass.py``).  Every reader
outside the pass's memo hands the same dates to the one composition
(``balance_at.resolved_with_books``), so a due-date repeat is walked with
its dates on every path.  Pinned here, through the doors an owner reaches:

* the recurrence form's live preview lists the paychecks covering the
  card's due dates, and refuses a due-date repeat into an account that is
  not a card with its muted line;
* the Recurring page dates the payment's next row on the card's next due
  date and describes it as such;
* the books refusals (``planned_rows_books``) walk the repeat with its
  dates: an opening restatement names the due-date row it would strand, an
  archived payment's restorable rows are read off its walk rather than
  every hidden row, and a paid row reverts above its books.

Each of the last three is a site that resolves outside the pass's memo, and
each would refuse a walk without the dates (plan#2673), so each case fails
if its site hands none.
"""

import logging
import re
from datetime import date, timedelta

import pytest

from app import ref_cache
from app.enums import RecurrenceUnitEnum, StatusEnum
from app.extensions import db
from app.models.transfer import Transfer
from app.services.pay_calendar import calendar_for
from app.services.planned_rows_books import first_row_an_opening_strands
from tests._test_helpers import cadence_payload, create_account_of_type
from tests.test_services.test_due_date_repeat_read_pass import (
    _DUE_ON_27,
    _JAN_2,
    _card,
    _generate,
    _payment,
)

def _preview(auth_client, seed_user, to_account):
    """GET the preview of a repeat on each due date into *to_account*, from Jan 2."""
    return auth_client.get(
        "/templates/preview-recurrence",
        query_string={
            **cadence_payload(unit=RecurrenceUnitEnum.DUE_DATE, starts_on=_JAN_2),
            "from_account_id": seed_user["account"].id,
            "to_account_id": to_account.id,
        },
    )


def _listed(period):
    """Return how the preview renders *period*'s payday."""
    return period.start_date.strftime("%b %d, %Y").encode()


def _rows(template):
    """The payment's live transfers, earliest due first."""
    return (
        db.session.query(Transfer)
        .filter_by(transfer_template_id=template.id, is_deleted=False)
        .order_by(Transfer.due_date)
        .all()
    )


def _generated(seed_user, card, name="Pay the Visa"):
    """A committed $200.00 payment into *card* on each due date, its rows generated.

    Generated over every saved paycheck (``seed_periods``): Jan 27, Feb 27,
    Mar 27 and Apr 27.
    """
    template = _payment(seed_user, card, name=name)
    _generate(seed_user, template)
    db.session.commit()
    return template


class TestThePreview:
    """The form's live preview walks the dates saving would."""

    def test_it_lists_the_paychecks_covering_the_cards_due_dates(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Jan 16, Feb 27, Mar 27 and Apr 24: the paychecks holding the due dates.

        And none of the six paychecks no due date falls in, so the list is
        the card's dates and not every paycheck.
        """
        with app.app_context():
            card = _card(seed_user)
            db.session.commit()

            resp = _preview(auth_client, seed_user, card)

            assert resp.status_code == 200
            for index in (1, 4, 6, 8):
                assert _listed(seed_periods[index]) in resp.data
            for index in (0, 2, 3, 5, 7, 9):
                assert _listed(seed_periods[index]) not in resp.data

    @pytest.mark.usefixtures("seed_periods")
    def test_into_an_account_that_is_not_a_card_it_previews_nothing(
        self, app, auth_client, seed_user, caplog,
    ):
        """The muted line, and the log names the account the refusal is about."""
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy day",
            )
            db.session.commit()
            caplog.set_level(logging.INFO)

            resp = _preview(auth_client, seed_user, savings)

            assert resp.status_code == 200
            assert b"No preview for this cadence" in resp.data
            assert (
                f"pays account {savings.id}, which is not a credit card"
            ) in caplog.text


class TestTheRecurringPage:
    """The Recurring page dates the payment's next row on the card's next due date."""

    def test_the_next_date_is_the_cards_next_due_date(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Due on the 15th: the next 15th on or after today, $200.00 a month.

        The page's read pass measures now by ``date.today()``
        (``BalanceContext.build``'s default), so the expected date is worked
        out from the same day: this month's 15th while it has not passed,
        else next month's.  Every due date is a deadline once a statement, so
        twelve a year and $200.00 a month.
        """
        with app.app_context():
            # Closing on the 20th and due on the 15th: the first 15th
            # strictly after each close is next month's, so every due date
            # is a 15th.
            card = _card(seed_user, close_day=20, due_day=15)
            _payment(seed_user, card, starts_on=seed_periods_today[0].start_date)
            db.session.commit()
            today = date.today()
            next_due = (
                today.replace(day=15) if today.day <= 15
                else (today.replace(day=1) + timedelta(days=31)).replace(day=15)
            )

            html = auth_client.get("/templates").data.decode()

            row = re.search(
                r'data-sort-name="pay the visa"[^>]*'
                r'data-sort-monthly="([^"]*)"[^>]*data-sort-next="([^"]*)"',
                html,
            )
            assert row is not None, "the payment's row is not on the page"
            assert row.groups() == ("200.00", next_due.isoformat())
            assert "On each statement&#39;s due date" in html[row.end():][:1500]


@pytest.mark.usefixtures("seed_periods")
class TestTheBooksRefusals:
    """Each books refusal resolving outside the pass walks the repeat with its dates."""

    def test_a_restatement_onto_a_due_date_names_its_row(self, app, seed_user):
        """The card's books opening Jan 27 strand the payment due Jan 27.

        A row dated ON an opening is inside it.  The day before strands
        nothing: no row sits on the card before Jan 27.
        """
        with app.app_context():
            card = _card(seed_user)
            _generated(seed_user, card)
            calendar = calendar_for(seed_user["user"].id)

            stranded = first_row_an_opening_strands(card.id, _DUE_ON_27[0], calendar)

            assert (stranded.name, stranded.books_day, stranded.is_hidden) == (
                "Pay the Visa", _DUE_ON_27[0], False,
            )
            assert first_row_an_opening_strands(
                card.id, _DUE_ON_27[0] - timedelta(days=1), calendar,
            ) is None

    def test_an_archived_payments_rows_are_read_off_its_walk(
        self, app, auth_client, seed_user, caplog,
    ):
        """Archived: its hidden Jan 27 row is named, from the walk, not every hidden row.

        The restatement reads what the archived payment's unarchive would
        restore off its walk over the books as they stand; a walk it cannot
        take falls back to counting every hidden row, with a warning.  So
        the warning's absence is the walk's.
        """
        with app.app_context():
            card = _card(seed_user)
            template = _generated(seed_user, card)
            assert auth_client.post(
                f"/transfers/{template.id}/archive",
            ).status_code == 302
            db.session.expire_all()
            assert not _rows(template), "precondition: the archive hid every row"
            caplog.set_level(logging.WARNING)

            stranded = first_row_an_opening_strands(
                card.id, _DUE_ON_27[0], calendar_for(seed_user["user"].id),
            )

            assert (stranded.name, stranded.books_day, stranded.is_hidden) == (
                "Pay the Visa", _DUE_ON_27[0], True,
            )
            assert "Counting every hidden row" not in caplog.text

    def test_a_paid_row_above_its_books_reverts(self, app, auth_client, seed_user):
        """Paid then set back to projected: allowed, its walk read with the dates.

        The books open before Jan 27, so neither question holds the row; the
        walk that asks the second needs the card's dates, and without them
        the revert would be refused as a schedule that cannot be read.
        """
        with app.app_context():
            card = _card(seed_user)
            first = _rows(_generated(seed_user, card))[0]
            assert auth_client.post(
                f"/transfers/instance/{first.id}/mark-done",
            ).status_code == 200
            projected = ref_cache.status_id(StatusEnum.PROJECTED)

            resp = auth_client.patch(
                f"/transfers/instance/{first.id}",
                data={"status_id": str(projected)},
            )

            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            assert db.session.get(Transfer, first.id).status_id == projected
