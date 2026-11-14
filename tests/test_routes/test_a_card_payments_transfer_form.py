"""
Shekel Budget App -- the recurring transfer form and a card's payment (CC-6)

Plan step **credit_card:CC-6**, task plan#4046.  A card's worked-out payment
pays its card only and repeats as its card's page sets it (task plan#2679), so
its edit form on the generic recurring transfer form:

* renders its destination DISABLED, with the update door's own sentence as its
  help (``CARD_PAYMENT_CANNOT_CHANGE_DESTINATION``), as a loan's pinned payment
  does;
* renders its repeat in the Recurring page's words and where it is set, with a
  link to the card's page, and NONE of the recurrence controls -- the form posts
  no recurrence key;
* and its update door refuses a submission stating any part of the repeat,
  echo or change, with ``CARD_PAYMENT_REPEAT_IS_SET_ON_ITS_CARD``, writing
  nothing; the form as rendered still saves.

A plain recurring transfer into a card is an extra payment, which the card's
own payment works out what is left after, and both transfer forms say so while
such a card is the destination; each card with a payment of its own carries
the fact on its option, for ``recurrence_form.js`` (driven in a browser by
``tests/manual/verify_recurrence_form.py``).

**The calendar** is ``seed_periods``': ten paychecks from Fri Jan 2 2026.
**The card** owes $0.00 observed Jan 2, so its books open Jan 1, and closes on
the 5th, due on the 27th.  **Today is held at Jan 8**, inside the Jan 2
paycheck.
"""

from datetime import date
from decimal import Decimal
from html.parser import HTMLParser

import pytest

from app import ref_cache
from app.enums import CardPaymentModeEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.routes.transfers._helpers import (
    CARD_PAYMENT_CANNOT_CHANGE_DESTINATION,
    CARD_PAYMENT_REPEAT_IS_SET_ON_ITS_CARD,
)
from app.schemas.validation import RECURRENCE_FORM_KEYS
from app.services.card_terms import set_terms
from app.services.recurrence import recurrence_spec
from tests._form_controls import enabled_form_controls
from tests._test_helpers import (
    create_account_of_type,
    create_savings_account,
    freeze_today,
    make_transfer_template,
)
from tests.test_routes.test_card_payment_doors import _flashes
from tests.test_services.test_card_payment_price import _works_out
from tests.test_services.test_due_date_repeat_read_pass import _TERMS_FROM, _terms

#: Today, for every request and pass here: inside the Jan 2 paycheck.
_TODAY = date(2026, 1, 8)

#: The extra-payment sentence's opening words, as the form renders them.
_EXTRA_PAYMENT = "A transfer into a card is an extra payment"


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    """Hold every clock the forms, the doors and their passes read at Jan 8."""
    freeze_today(monkeypatch, _TODAY)


class _PageReader(HTMLParser):
    """Every named control a page renders, and the elements this file reads.

    Attributes:
        names: Each ``<input>``, ``<select>`` and ``<textarea>`` name, disabled
            or not: what the page could post.
        tags: Each element carrying an ``id``, as its attributes.
        options: Each ``<option>`` of ``#to_account_id``, as its attributes.
        text: Each id'd element's text, its descendants' included.
    """

    def __init__(self):
        super().__init__()
        self.names: set[str] = set()
        self.tags: dict[str, dict[str, str | None]] = {}
        self.options: list[dict[str, str | None]] = []
        self.text: dict[str, list[str]] = {}
        self._open: list[str | None] = []
        self._in_destination = False

    def handle_starttag(self, tag, attrs):
        """Record a control's name, an id'd element, or a destination option."""
        attributes = dict(attrs)
        if tag in ("input", "select", "textarea") and attributes.get("name"):
            self.names.add(attributes["name"])
        if tag == "select":
            self._in_destination = attributes.get("id") == "to_account_id"
        if tag == "option" and self._in_destination:
            self.options.append(attributes)
        if tag in ("input", "option", "br"):
            return
        element_id = attributes.get("id")
        if element_id is not None:
            self.tags[element_id] = attributes
            self.text[element_id] = []
        self._open.append(element_id)

    def handle_endtag(self, tag):
        """Close the innermost open element."""
        if tag == "select":
            self._in_destination = False
        if tag in ("input", "option", "br") or not self._open:
            return
        self._open.pop()

    def handle_data(self, data):
        """Add *data* to the text of every id'd element it sits inside."""
        for element_id in self._open:
            if element_id is not None:
                self.text[element_id].append(data)

    def words(self, element_id):
        """Return the element's text, its whitespace collapsed."""
        return " ".join("".join(self.text[element_id]).split())


def _read(response):
    """Return the page *response* carries, read; asserts it rendered."""
    assert response.status_code == 200
    reader = _PageReader()
    reader.feed(response.get_data(as_text=True))
    return reader


def _card(seed_user):
    """A Credit Card owing nothing on Jan 2, closing on the 5th, due on the 27th, flushed."""
    card = create_account_of_type(
        seed_user, db.session, "Credit Card", "Visa",
        anchor_balance=Decimal("0.00"), observed_on=date(2026, 1, 2),
    )
    db.session.flush()
    set_terms(card.id, card.user_id, _TERMS_FROM, _terms(5, 27))
    db.session.flush()
    return card


def _savings(seed_user):
    """A Savings account holding nothing on Jan 2, flushed."""
    return create_savings_account(
        seed_user, db.session, "Savings", Decimal("0.00"),
        observed_on=date(2026, 1, 2),
    )


def _option_for(page, account):
    """Return *account*'s ``<option>`` in the destination select, as its attributes."""
    found = [o for o in page.options if o.get("value") == str(account.id)]
    assert len(found) == 1, page.options
    return found[0]


def _stored(template):
    """*template*'s repeat as stored, and its live rows' dates, read fresh."""
    db.session.expire_all()
    template = db.session.get(TransferTemplate, template.id)
    rows = db.session.query(Transfer).filter_by(
        transfer_template_id=template.id, is_deleted=False,
    ).all()
    return template.name, recurrence_spec(template.recurrence_rule), sorted(
        row.pay_period_id for row in rows
    )


@pytest.mark.usefixtures("seed_periods")
class TestTheCardPaymentsEditForm:
    """The payment's destination and repeat are shown, never offered."""

    def test_the_destination_is_disabled_with_the_doors_sentence(
        self, app, auth_client, seed_user,
    ):
        """The card is shown, disabled, and the help is the refusal's own words."""
        with app.app_context():
            template = _works_out(
                seed_user, _card(seed_user), CardPaymentModeEnum.CARD_BALANCE,
            )

            page = _read(auth_client.get(f"/transfers/{template.id}/edit"))

            destination = page.tags["to_account_id"]
            assert "disabled" in destination
            assert destination["aria-describedby"] == "to-account-help"
            assert page.words("to-account-help") == (
                CARD_PAYMENT_CANNOT_CHANGE_DESTINATION.format(payment="Pay the Visa")
            )

    def test_payoff_rows_show_their_repeat_and_no_control(
        self, app, auth_client, seed_user,
    ):
        """Each payday, in the Recurring page's words, where it is set, and nothing to post."""
        with app.app_context():
            card = _card(seed_user)
            template = _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)

            page = _read(auth_client.get(f"/transfers/{template.id}/edit"))

            assert page.words("card-payment-repeat") == "Every paycheck"
            assert page.words("card-payment-repeat-help") == (
                CARD_PAYMENT_REPEAT_IS_SET_ON_ITS_CARD.format(
                    payment="Pay the Visa", card="Visa",
                ) + " Open Visa's page"
            )
            assert f'href="/accounts/{card.id}/details#card-payment"' in (
                auth_client.get(f"/transfers/{template.id}/edit").get_data(as_text=True)
            )
            assert page.names & RECURRENCE_FORM_KEYS == set()
            assert "recurrence-fields" not in page.tags
            assert "to-account-extra-payment" not in page.tags

    def test_a_scheduled_payment_shows_its_due_dates(
        self, app, auth_client, seed_user,
    ):
        """A payment scheduled with the card company reads as its due dates."""
        with app.app_context():
            template = _works_out(
                seed_user, _card(seed_user), CardPaymentModeEnum.STATEMENT_BALANCE,
                on_due_dates=True,
            )

            page = _read(auth_client.get(f"/transfers/{template.id}/edit"))

            assert page.words("card-payment-repeat") == "On each statement's due date"
            assert page.names & RECURRENCE_FORM_KEYS == set()

    def test_the_form_as_rendered_saves(self, app, auth_client, seed_user):
        """What a browser posts, renamed, saves the name and leaves the repeat."""
        with app.app_context():
            template = _works_out(
                seed_user, _card(seed_user), CardPaymentModeEnum.CARD_BALANCE,
            )
            _name, repeat, rows = _stored(template)
            html = auth_client.get(f"/transfers/{template.id}/edit").get_data(as_text=True)
            posted = enabled_form_controls(html, f"/transfers/{template.id}")
            posted["name"] = ["Visa autopay"]

            response = auth_client.post(f"/transfers/{template.id}", data=posted)

            assert response.status_code == 302
            assert response.headers["Location"].endswith("/transfers")
            assert _stored(template) == ("Visa autopay", repeat, rows)


#: A submission stating a part of the payment's repeat, by what each states.
#: Each is one the update schema accepts, so the refusal met is the door's.
_REPEATS_STATED = {
    "the stored repeat, echoed": lambda periods: {
        "recurrence_unit": str(ref_cache.recurrence_unit_id(RecurrenceUnitEnum.PERIOD)),
        "interval_n": "1",
        "recurrence_placement": str(periods),
        "starts_on": "2026-01-02",
    },
    "each statement's due date": lambda periods: {
        "recurrence_unit": str(ref_cache.recurrence_unit_id(RecurrenceUnitEnum.DUE_DATE)),
        "interval_n": "1",
        "recurrence_placement": str(periods),
        "starts_on": "2026-01-02",
    },
    "does not repeat": lambda periods: {"recurrence_unit": ""},
    "a start alone": lambda periods: {"starts_on": "2026-01-16"},
    "a stop alone": lambda periods: {
        "recurrence_end_mode": "on_date", "end_date": "2026-03-31",
    },
}


def _placement(template):
    """The stored repeat's placement id, which an echo posts."""
    return template.recurrence_rule.placement_id


@pytest.mark.usefixtures("seed_periods")
class TestTheUpdateDoorRefusesARepeat:
    """Any part of a card payment's repeat stated here is refused, and nothing is written."""

    @pytest.mark.parametrize("stated", sorted(_REPEATS_STATED))
    def test_a_stated_repeat_is_refused(self, app, auth_client, seed_user, stated):
        """Refused to the edit form in the card's words; name, repeat and rows stand."""
        with app.app_context():
            template = _works_out(
                seed_user, _card(seed_user), CardPaymentModeEnum.CARD_BALANCE,
            )
            before = _stored(template)

            response = auth_client.post(f"/transfers/{template.id}", data={
                "name": "Visa autopay",
                "version_id": str(template.version_id),
                **_REPEATS_STATED[stated](_placement(template)),
            })

            assert response.status_code == 302
            assert response.headers["Location"].endswith(
                f"/transfers/{template.id}/edit",
            )
            assert _flashes(auth_client) == [
                CARD_PAYMENT_REPEAT_IS_SET_ON_ITS_CARD.format(
                    payment="Pay the Visa", card="Visa",
                ),
            ]
            assert _stored(template) == before

    def test_a_plain_transfer_into_the_card_takes_its_repeat(
        self, app, auth_client, seed_user,
    ):
        """The control: the same change on an extra payment into the card lands."""
        with app.app_context():
            card = _card(seed_user)
            _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)
            extra = make_transfer_template(db.session, seed_user, card, "100.00")
            db.session.commit()

            response = auth_client.post(f"/transfers/{extra.id}", data={
                "name": extra.name,
                "version_id": str(extra.version_id),
                **_REPEATS_STATED["each statement's due date"](_placement(extra)),
            })

            assert response.status_code == 302
            assert response.headers["Location"].endswith("/transfers")
            assert _stored(extra)[1].unit is RecurrenceUnitEnum.DUE_DATE


@pytest.mark.usefixtures("seed_periods")
class TestTheKeysTheDoorReads:
    """The refusal reads every control a recurring transfer's form can post."""

    def test_the_keys_are_the_forms_recurrence_controls(
        self, app, auth_client, seed_user,
    ):
        """A plain transfer's form renders exactly these beside its own fields."""
        with app.app_context():
            extra = make_transfer_template(
                db.session, seed_user, _savings(seed_user), "100.00",
            )
            db.session.commit()

            page = _read(auth_client.get(f"/transfers/{extra.id}/edit"))

            assert page.names - {
                "csrf_token", "version_id", "name", "amount", "effective_from",
                "from_account_id", "to_account_id", "category_id",
            } == RECURRENCE_FORM_KEYS
            assert RECURRENCE_FORM_KEYS == {
                "recurrence_unit", "interval_n", "recurrence_placement",
                "max_per_month", "starts_on", "repeats_on",
                "recurrence_end_mode", "end_date", "max_occurrences",
            }


@pytest.mark.usefixtures("seed_periods")
class TestTheExtraPaymentSentence:
    """A transfer into a card with its own payment says it is an extra payment."""

    def test_the_create_form_marks_the_cards_with_a_payment(
        self, app, auth_client, seed_user,
    ):
        """The card's option carries the fact; checking's does not; none is chosen yet."""
        with app.app_context():
            card = _card(seed_user)
            _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)

            page = _read(auth_client.get("/transfers/new"))

            assert "data-has-a-payment" in _option_for(page, card)
            assert "data-has-a-payment" not in _option_for(page, seed_user["account"])
            assert "d-none" in page.tags["to-account-extra-payment"]["class"].split()
            assert page.words("to-account-extra-payment").startswith(_EXTRA_PAYMENT)

    def test_the_create_form_opened_on_the_card_says_so(
        self, app, auth_client, seed_user,
    ):
        """Opened with the card as the destination, the sentence is shown."""
        with app.app_context():
            card = _card(seed_user)
            _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)

            page = _read(auth_client.get(f"/transfers/new?to_account={card.id}"))

            assert "d-none" not in page.tags["to-account-extra-payment"]["class"].split()

    def test_an_extra_payments_edit_form_says_so(self, app, auth_client, seed_user):
        """A plain transfer into the card: shown, and its controls are its owner's."""
        with app.app_context():
            card = _card(seed_user)
            _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)
            extra = make_transfer_template(db.session, seed_user, card, "100.00")
            db.session.commit()

            page = _read(auth_client.get(f"/transfers/{extra.id}/edit"))

            assert "d-none" not in page.tags["to-account-extra-payment"]["class"].split()
            assert "disabled" not in page.tags["to_account_id"]
            assert "card-payment-repeat" not in page.tags
            assert RECURRENCE_FORM_KEYS <= page.names

    def test_a_transfer_into_savings_does_not(self, app, auth_client, seed_user):
        """The control: into savings, the sentence is rendered hidden."""
        with app.app_context():
            card = _card(seed_user)
            _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)
            savings = make_transfer_template(
                db.session, seed_user, _savings(seed_user), "100.00",
            )
            db.session.commit()

            page = _read(auth_client.get(f"/transfers/{savings.id}/edit"))

            assert "d-none" in page.tags["to-account-extra-payment"]["class"].split()
            assert "data-has-a-payment" in _option_for(page, card)
