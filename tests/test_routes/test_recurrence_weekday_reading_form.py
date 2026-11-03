"""The template form's "repeating on" control reads a date by its weekday.

Plan step ``recurrence:R8-c``, rulings plan#2668 and plan#2670.  The control
is ONE ``<select>`` (``repeats_on``) offering the same day of the month, the
clamped month-end days and, since this step, the weekday counted from the
month's start ("the third Tuesday") and the month's last such weekday.  A
weekday option's words depend on the date being typed, so it carries every
phrase it can show and the window saying which dates it reads;
``recurrence_form.js`` picks the phrase and enables the option, which
rendered HTML cannot see (``tests/manual/verify_recurrence_form.py``,
plan#2680, grades that half).

**What these cases post is what the template emits.**  The create case posts
the weekday token read off the rendered option, and every edit case posts the
rendered EDIT form whole (``_repeats_on_page.form_as_posted``), changing only what
the owner changed -- so an amount-only edit carries the date and the
preselected reading exactly as a browser does, and a preselection the page
got wrong would clear the reading on the next save.

The schedule is ``seed_periods``: ten biweekly paychecks from Fri 2026-01-02
to Thu 2026-05-21 (the preview's is two years of the same), today frozen on
Mon 2026-01-05 inside the first.  The third
Tuesdays of 2026, counted off a calendar: Jan 20, Feb 17, Mar 17, Apr 21, May
19, Jun 16, Jul 21, Aug 18.  Read by its day instead, a rule from Jan 20 fires
on the 20th of every month.
"""
import re
from datetime import date

import pytest

from app.enums import RecurrenceUnitEnum
from app.extensions import db
from app.models.ref import TransactionType
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.routes._recurrence_preview import NOTHING_TO_PREVIEW
from app.services.recurrence import weekday_reading_options
from tests._test_helpers import cadence_payload, freeze_today
from tests.test_routes._repeats_on_page import (
    form_as_posted,
    repeats_on_control,
    weekday_token,
)

#: The day every request here is made on: inside the schedule's first paycheck.
_TODAY = date(2026, 1, 5)

#: Tue 2026-01-20, the third Tuesday of January 2026 (6, 13, 20).
_THIRD_TUESDAY = date(2026, 1, 20)

#: The third Tuesdays the rule fires on inside the saved schedule.
_THIRD_TUESDAYS = [
    date(2026, 1, 20), date(2026, 2, 17), date(2026, 3, 17),
    date(2026, 4, 21), date(2026, 5, 19),
]

#: The name every created template carries, so "which template" is one query.
_NAME = "Book club dues"


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    """Freeze today inside the schedule's first paycheck."""
    freeze_today(monkeypatch, _TODAY)


def _create_through_the_form(auth_client, seed_user, starts_on, repeats_on):
    """POST /templates: a monthly rule from *starts_on*, read as *repeats_on*.

    Args:
        auth_client: The signed-in client.
        seed_user: The owner fixture.
        starts_on: The first occurrence.
        repeats_on: The "repeating on" token, read off the rendered form.

    Returns:
        The created template.
    """
    expense = db.session.query(TransactionType).filter_by(name="Expense").one()
    resp = auth_client.post("/templates", data={
        "name": _NAME,
        "default_amount": "25.00",
        "category_id": seed_user["categories"]["Rent"].id,
        "transaction_type_id": expense.id,
        "account_id": seed_user["account"].id,
        **cadence_payload(unit=RecurrenceUnitEnum.MONTH, starts_on=starts_on),
        "repeats_on": repeats_on,
    })
    assert resp.status_code == 302, resp.data.decode()
    assert resp.headers["Location"].endswith("/templates")
    return db.session.query(TransactionTemplate).filter_by(name=_NAME).one()


def _create_a_third_tuesday_rule(auth_client, seed_user):
    """Create the third-Tuesday rule through the create form, as the owner does."""
    page = auth_client.get("/templates/new").data.decode()
    return _create_through_the_form(
        auth_client, seed_user, _THIRD_TUESDAY,
        weekday_token(page, counts_from_end=False),
    )


def _due_dates(template):
    """Return the due dates of *template*'s live rows, ascending."""
    db.session.expire_all()
    return sorted(
        row.due_date
        for row in db.session.query(Transaction).filter_by(
            template_id=template.id, is_deleted=False,
        )
    )


def _rule_columns(template):
    """Return the stored ``(starts_on, nominal_day, weekday_reading)`` of *template*'s rule."""
    db.session.expire_all()
    rule = db.session.get(TransactionTemplate, template.id).recurrence_rule
    return rule.starts_on, rule.nominal_day, rule.weekday_reading


def _edit_form_as_posted(auth_client, template, **changes):
    """Return *template*'s rendered edit form as a browser posts it, with *changes*."""
    action = f"/templates/{template.id}"
    return form_as_posted(auth_client, f"{action}/edit", action, **changes)


@pytest.mark.usefixtures("seed_periods")
class TestTheControlRendersTheWeekdayOptions:
    """What the server renders, before any script runs."""

    def test_the_create_form_carries_both_weekday_options_and_their_windows(self, auth_client):
        """Both options, each with the window and every phrase the script picks from.

        The create form opens on no cadence, so the row is hidden and every
        option but the default is disabled until a cadence with a day of the
        month is chosen; the script enables what the typed date allows.
        """
        control = repeats_on_control(auth_client.get("/templates/new").data.decode())

        assert [option["value"] for option in control.options] == [
            "", "29", "30", "31", "from_start", "from_end",
        ]
        assert "d-none" in control.container_classes
        expected = {option.token: option for option in weekday_reading_options()}
        for token, window in (("from_start", ("false", "4")), ("from_end", ("true", "1"))):
            option = control.by_value[token]
            assert (
                option["data"]["counts-from-end"], option["data"]["last-week"],
            ) == window
            assert option["data"]["labels"] == expected[token].labels_json
            assert option["disabled"] and option["label"] == ""

    def test_the_edit_form_preselects_the_stored_reading_in_its_words(
        self, auth_client, seed_user,
    ):
        """Shown, enabled, selected and labelled for the stored date.

        Jan 20 is two weeks from January's end, so the last-weekday option
        stays disabled and unlabelled.
        """
        template = _create_a_third_tuesday_rule(auth_client, seed_user)

        control = repeats_on_control(
            auth_client.get(f"/templates/{template.id}/edit").data.decode(),
        )

        assert "d-none" not in control.container_classes
        assert "disabled" not in control.selects[0]
        chosen = control.by_value["from_start"]
        assert (chosen["selected"], chosen["disabled"], chosen["label"]) == (
            True, False, "the third Tuesday",
        )
        assert [
            option["value"] for option in control.options if option["selected"]
        ] == ["from_start"]
        other = control.by_value["from_end"]
        assert (other["disabled"], other["label"]) == (True, "")


@pytest.mark.usefixtures("seed_periods")
class TestTheFormStoresAndWalksTheReading:
    """Create and edit, posting what the template emits."""

    def test_a_third_tuesday_rule_created_through_the_form(
        self, auth_client, seed_user,
    ):
        """It stores ``from_start`` and its rows are due on the third Tuesdays.

        Read by its day, the same rule would be due on Feb 20, Mar 20, Apr 20
        and May 20.
        """
        template = _create_a_third_tuesday_rule(auth_client, seed_user)

        assert _rule_columns(template) == (_THIRD_TUESDAY, None, "from_start")
        assert _due_dates(template) == _THIRD_TUESDAYS

    def test_an_amount_only_edit_keeps_the_reading(
        self, auth_client, seed_user,
    ):
        """The whole form posted with the amount changed: the date and its reading ride.

        The browser posts the start and the preselected reading on every
        save, so a preselection the page got wrong would clear the reading
        here -- the rows would move to the 20th.
        """
        template = _create_a_third_tuesday_rule(auth_client, seed_user)

        resp = auth_client.post(
            f"/templates/{template.id}",
            data=_edit_form_as_posted(
                auth_client, template, default_amount="30.00",
            ),
        )

        assert resp.status_code == 302, resp.data.decode()
        assert _rule_columns(template) == (_THIRD_TUESDAY, None, "from_start")
        assert _due_dates(template) == _THIRD_TUESDAYS

    def test_moving_the_date_to_the_30th_with_the_control_reset_clears_it(
        self, auth_client, seed_user,
    ):
        """The 30th read by its day: Jan 30, Feb 28 (clamped), Mar 30, Apr 30.

        May 30 falls after the last saved paycheck (May 8-21), so it has no
        row.  The script resets the control when the typed date disallows
        the stored choice; this is what it then posts.
        """
        template = _create_a_third_tuesday_rule(auth_client, seed_user)

        resp = auth_client.post(
            f"/templates/{template.id}",
            data=_edit_form_as_posted(
                auth_client, template, starts_on="2026-01-30", repeats_on="",
            ),
        )

        assert resp.status_code == 302, resp.data.decode()
        assert _rule_columns(template) == (date(2026, 1, 30), None, None)
        assert _due_dates(template) == [
            date(2026, 1, 30), date(2026, 2, 28), date(2026, 3, 30),
            date(2026, 4, 30),
        ]

    def test_the_stored_reading_beside_a_moved_date_it_does_not_fit_is_a_field_error(
        self, auth_client, seed_user,
    ):
        """The trap the plan#2676 note named, refused in words rather than a 500.

        The date typed with no script to reset the control: Fri 2026-01-30
        is a fifth Friday, so it cannot be read from the start, and the
        sentence names what it CAN be read as.  Nothing is saved.
        """
        template = _create_a_third_tuesday_rule(auth_client, seed_user)

        resp = auth_client.post(
            f"/templates/{template.id}",
            data=_edit_form_as_posted(auth_client, template, starts_on="2026-01-30"),
            follow_redirects=True,
        )

        assert resp.status_code == 200
        assert (
            "Starting on Jan 30, 2026, this can repeat on the same day of the "
            "month or on the last Friday."
        ) in resp.data.decode()
        assert _rule_columns(template) == (_THIRD_TUESDAY, None, "from_start")
        assert _due_dates(template) == _THIRD_TUESDAYS


def _preview(auth_client, starts_on, repeats_on):
    """Return the preview fragment's period lines for a monthly rule.

    Args:
        auth_client: The signed-in client.
        starts_on: The first occurrence.
        repeats_on: The "repeating on" token.

    Returns:
        The fragment's text.
    """
    resp = auth_client.get(
        "/templates/preview-recurrence",
        query_string={
            **cadence_payload(unit=RecurrenceUnitEnum.MONTH, starts_on=starts_on),
            "repeats_on": repeats_on,
        },
    )
    assert resp.status_code == 200
    return resp.data.decode()


@pytest.mark.usefixtures("seed_periods_52")
class TestThePreviewReadsTheReading:
    """``/templates/preview-recurrence`` reads ``repeats_on`` through the schema's own reader.

    On ``seed_periods_52`` -- the same schedule two years long -- because the
    preview lists SAVED paychecks only, and no third Tuesday from January to
    May lands in a different paycheck from its day of the month.
    """

    def test_from_the_start_walks_the_third_tuesdays_paychecks(self, auth_client):
        """From Tue 2026-04-21: June's third Tuesday is in a different paycheck from June 21.

        Apr 21, May 19, Jun 16, Jul 21 and Aug 18 sit in the paychecks
        starting Apr 10, May 8, Jun 5, Jul 17 and Aug 14.  Read by its day,
        Jun 21 (a Sunday) is in the one starting Jun 19, and the other four
        are the same -- so the token is what moves June.
        """
        page = auth_client.get("/templates/new").data.decode()
        token = weekday_token(page, counts_from_end=False)

        weekday = re.findall(r"<li>(.*?)</li>", _preview(auth_client, date(2026, 4, 21), token))
        by_day = re.findall(r"<li>(.*?)</li>", _preview(auth_client, date(2026, 4, 21), ""))

        assert weekday == [
            "Apr 10, 2026 - Apr 23, 2026",
            "May 08, 2026 - May 21, 2026",
            "Jun 05, 2026 - Jun 18, 2026",
            "Jul 17, 2026 - Jul 30, 2026",
            "Aug 14, 2026 - Aug 27, 2026",
        ]
        assert by_day == [
            "Apr 10, 2026 - Apr 23, 2026",
            "May 08, 2026 - May 21, 2026",
            "Jun 19, 2026 - Jul 02, 2026",
            "Jul 17, 2026 - Jul 30, 2026",
            "Aug 14, 2026 - Aug 27, 2026",
        ]

    def test_an_unknown_token_answers_no_preview(self, auth_client):
        """A token no option posts previews nothing rather than the 17th."""
        fragment = _preview(auth_client, date(2026, 11, 17), "third_tuesday")

        assert NOTHING_TO_PREVIEW in fragment
        assert "<li>" not in fragment
