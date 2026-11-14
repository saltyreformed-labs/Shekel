"""
Shekel Budget App -- the card's page switches its one payment

Plan step **credit_card:CC-6**, its task plan#2679 (rulings **plan#2888**,
**plan#2889**, **plan#2669**): every card is made with payoff rows, everything
on the card each payday, and its page switches that one payment to a payment
scheduled with the card company, the statement balance, the minimum or
everything on the card on each statement's due date, and back.  Pinned
through what the page posts:

* a switch rewrites the mode and the repeat, and the rows follow from today's
  paycheck: payday rows retire and each due date gets a row, priced by the new
  amount; and back again, the paycheck's passed payday placed now;
* a change of amount alone moves no row;
* a payment the owner typed over on a date the switch takes away is asked about
  first, and asking saves nothing; "keep" leaves it and the next due date pays
  what is left, "move" drops it; one on a date both repeats name is not asked;
* the refusals: a dormant card cannot be scheduled, an archived card's payment
  cannot change, a card with no payment, another owner's card and a checking
  account are 404, and a token the page does not post is refused;
* the card's page says which payment the card has and offers the others.

**The calendar** is ``seed_periods``': ten paychecks from Fri Jan 2 2026,
paydays Jan 2, Jan 16, Jan 30, Feb 13, Feb 27, Mar 13, Mar 27, Apr 10, Apr 24
and May 8, the last ending May 21.  **The card** closes on the 5th and falls due
on the 27th from Dec 1 2025, so inside the horizon it falls due Jan 27, Feb 27,
Mar 27 and Apr 27; its $0.00 owed is observed Jan 2, so its books open Jan 1,
before every row.  **$800 goes on
it Jan 3.**  **Today is held at Jan 8**, inside the Jan 2 paycheck.
"""

from datetime import date
from decimal import Decimal
from html.parser import HTMLParser

import pytest

from app import ref_cache
from app.enums import CardPaymentModeEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.card_payment_settings import CardPaymentSettings
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import card_payoff
from app.services.card_terms import set_terms
from app.services.recurrence import recurrence_spec
from tests._test_helpers import create_account_of_type, freeze_today
from tests.test_routes.test_a_terms_change_moves_the_payments import (
    _DUE_ON_20_FORM,
    _apply,
    _deleted,
    _figure,
    _live_dates,
    _read_chooser,
    _row_on,
    _typed_over,
)
from tests.test_routes.test_card_payment_doors import _flashes
from tests.test_services.test_card_payment_price import _spend, _works_out
from tests.test_services.test_due_date_repeat_read_pass import (
    _DUE_ON_27,
    _TERMS_FROM,
    _terms,
)

#: Today, for every request and pass here but one class's: inside the Jan 2 paycheck.
_TODAY = date(2026, 1, 8)

#: The paydays inside the horizon.
_PAYDAYS = [
    date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30), date(2026, 2, 13),
    date(2026, 2, 27), date(2026, 3, 13), date(2026, 3, 27), date(2026, 4, 10),
    date(2026, 4, 24), date(2026, 5, 8),
]


@pytest.fixture(name="today")
def _today():
    """The day every request and pass here is held at; a class may restate it."""
    return _TODAY


@pytest.fixture(autouse=True)
def _frozen(monkeypatch, today):
    """Hold every clock the door and its passes read at *today*."""
    freeze_today(monkeypatch, today)


def _dormant_card(seed_user):
    """A Credit Card owing nothing on Jan 2, its books open Jan 1, no terms, flushed."""
    card = create_account_of_type(
        seed_user, db.session, "Credit Card", "Visa",
        anchor_balance=Decimal("0.00"), observed_on=date(2026, 1, 2),
    )
    db.session.flush()
    return card


def _card(seed_user):
    """:func:`_dormant_card`, closing on the 5th and due on the 27th, flushed."""
    card = _dormant_card(seed_user)
    set_terms(card.id, card.user_id, _TERMS_FROM, _terms(5, 27))
    db.session.flush()
    return card


def _with_its_payoff(seed_user, seed_periods, card):
    """*card*'s payoff rows each payday, and $800 on it Jan 3; the payment.

    Built as :func:`app.services.card_payoff.make_payoff` builds it: the
    whole balance worked out, each payday, no figure stored.
    """
    template = _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)
    _spend(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
    return template


def _switch(auth_client, card, token):
    """POST the card's payment form naming *token*, the redirect not followed."""
    return auth_client.post(
        f"/accounts/{card.id}/card/payment", data={"payment": token},
    )


def _payment_of(template):
    """*template*'s ``(mode, unit, starts_on)`` as stored, read fresh."""
    db.session.expire_all()
    template = db.session.get(TransferTemplate, template.id)
    spec = recurrence_spec(template.recurrence_rule)
    return (
        ref_cache.card_payment_mode_member(template.card_payment_settings.mode_id),
        spec.unit,
        spec.starts_on,
    )


def _price_on(template, day, seed_user):
    """What the row of *template* answering *day* is worth."""
    return _figure(_row_on(template, day).id, seed_user)


class TestTheSwitch:
    """The mode and the repeat change, and the rows follow from today's paycheck."""

    def test_the_statement_balance_is_paid_on_each_due_date(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Payday rows give way to one row per due date, priced at its statement.

        The repeat covers from Jan 2, the first day of the paycheck holding
        today (plan#2669), so every unpaid payday row from it on retires,
        Jan 2's included.  Jan 27 pays the statement closing Jan 5, valued at
        the end of Jan 4: the $800 bought Jan 3, so $800.00.  Feb 27 pays the
        statement closing Feb 5, which holds that $800 payment, assumed on
        time (plan#2889): $800.00 - $800.00 = $0.00.
        """
        with app.app_context():
            card = _card(seed_user)
            template = _with_its_payoff(seed_user, seed_periods, card)
            assert _live_dates(template) == _PAYDAYS

            resp = _switch(auth_client, card, "statement")

            assert resp.status_code == 302
            assert _flashes(auth_client) == ["Visa's payment is saved."]
            assert _payment_of(template) == (
                CardPaymentModeEnum.STATEMENT_BALANCE,
                RecurrenceUnitEnum.DUE_DATE,
                date(2026, 1, 2),
            )
            assert _live_dates(template) == _DUE_ON_27
            assert _price_on(template, date(2026, 1, 27), seed_user) == Decimal("800.00")
            assert _price_on(template, date(2026, 2, 27), seed_user) == Decimal("0.00")

    def test_back_to_payoff_rows_pays_each_payday_again(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Switched back, each payday has its row again, Jan 2's placed now.

        The repeat starts on today, which it moves onto the payday of the
        paycheck holding it, Jan 2 (plan#2669).  Jan 2 pays what the card
        owed at the end of Jan 1, $0.00; Jan 16 what it owed at the end of
        Jan 15, the $800 bought Jan 3: $800.00.
        """
        with app.app_context():
            card = _card(seed_user)
            template = _with_its_payoff(seed_user, seed_periods, card)
            _switch(auth_client, card, "statement")

            resp = _switch(auth_client, card, "payoff")

            assert resp.status_code == 302
            assert _payment_of(template) == (
                CardPaymentModeEnum.CARD_BALANCE,
                RecurrenceUnitEnum.PERIOD,
                date(2026, 1, 2),
            )
            assert _live_dates(template) == _PAYDAYS
            assert _price_on(template, date(2026, 1, 2), seed_user) == Decimal("0.00")
            assert _price_on(template, date(2026, 1, 16), seed_user) == Decimal("800.00")

    def test_a_change_of_amount_alone_moves_no_row(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Statement balance to minimum: the same rows, the minimum's figure.

        The Jan 5 statement is $800.00, and its minimum is the larger of 2% of
        it ($16.00) and the $25.00 floor: $25.00.
        """
        with app.app_context():
            card = _card(seed_user)
            template = _with_its_payoff(seed_user, seed_periods, card)
            _switch(auth_client, card, "statement")
            ids = {row.id for row in db.session.query(Transfer).filter_by(
                transfer_template_id=template.id, is_deleted=False,
            )}

            _switch(auth_client, card, "minimum")

            assert _payment_of(template)[:2] == (
                CardPaymentModeEnum.MINIMUM_PAYMENT, RecurrenceUnitEnum.DUE_DATE,
            )
            assert {row.id for row in db.session.query(Transfer).filter_by(
                transfer_template_id=template.id, is_deleted=False,
            )} == ids
            assert _price_on(template, date(2026, 1, 27), seed_user) == Decimal("25.00")

    def test_the_payment_it_already_has_changes_nothing(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Saving payoff rows on a card paid off each payday says so and writes nothing."""
        with app.app_context():
            card = _card(seed_user)
            template = _with_its_payoff(seed_user, seed_periods, card)
            version = template.version_id

            _switch(auth_client, card, "payoff")

            assert _flashes(auth_client) == ["Visa's payment is already that one."]
            db.session.expire_all()
            assert db.session.get(TransferTemplate, template.id).version_id == version
            assert _live_dates(template) == _PAYDAYS


class TestItStartsInTodaysPaycheck:
    """A date already passed in today's paycheck is placed now (ruling plan#2669)."""

    @pytest.fixture(name="today")
    def _jan_28(self):
        """Today is Jan 28, in the Jan 16 paycheck, the day after a due date."""
        return date(2026, 1, 28)

    def test_a_due_date_passed_this_paycheck_is_paid_now(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Switched on Jan 28, the Jan 27 statement is paid in the Jan 16 paycheck.

        The repeat covers from Jan 16, the first day of the paycheck holding
        Jan 28, so the statement due Jan 27 is paid now rather than a month
        later: its row is in the Jan 16 paycheck, $800.00, and checking pays it
        before the paycheck ends.
        """
        with app.app_context():
            card = _card(seed_user)
            template = _with_its_payoff(seed_user, seed_periods, card)

            _switch(auth_client, card, "statement")

            assert _payment_of(template)[2] == date(2026, 1, 16)
            row = _row_on(template, date(2026, 1, 27))
            assert row.pay_period_id == seed_periods[1].id
            assert _price_on(template, date(2026, 1, 27), seed_user) == Decimal("800.00")


class TestATypedOverPaymentIsAskedAbout:
    """A payment the owner typed over on a date the switch takes away is asked about first."""

    def test_asking_saves_nothing(self, app, auth_client, seed_user, seed_periods):
        """The chooser names the change and the row, and the payment stands as it was."""
        with app.app_context():
            card = _card(seed_user)
            template = _with_its_payoff(seed_user, seed_periods, card)
            typed = _typed_over(seed_user, template, date(2026, 1, 16))

            resp = _switch(auth_client, card, "statement")

            page = _read_chooser(resp)
            text = resp.get_data(as_text=True)
            assert "Confirm change of card payment" in text
            assert "You changed how Visa is paid." in text
            assert "Your unpaid payments move to each statement&#39;s due date." in text
            assert "Move to the due date" in text
            assert page.radio_names == {f"conflict_decision_{typed}"}
            assert _payment_of(template)[:2] == (
                CardPaymentModeEnum.CARD_BALANCE, RecurrenceUnitEnum.PERIOD,
            )
            assert _live_dates(template) == _PAYDAYS
            assert _figure(typed, seed_user) == Decimal("300.00")

    def test_keep_leaves_it_and_the_due_date_pays_the_rest(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Kept, the $300 stays on Jan 16 and Jan 27 pays what is left.

        The Jan 5 statement is $800.00; the $300 sent Jan 16 is after its
        close and on or before Jan 27, so it is not inside that figure and is
        subtracted (ruling R-CC22): $800.00 - $300.00 = $500.00.  The Feb 5
        statement holds both: $800.00 - $300.00 - $500.00 = $0.00.
        """
        with app.app_context():
            card = _card(seed_user)
            template = _with_its_payoff(seed_user, seed_periods, card)
            typed = _typed_over(seed_user, template, date(2026, 1, 16))
            page = _read_chooser(_switch(auth_client, card, "statement"))

            resp = _apply(auth_client, page, {f"conflict_decision_{typed}": "keep"})

            assert resp.status_code == 302
            assert _payment_of(template)[:2] == (
                CardPaymentModeEnum.STATEMENT_BALANCE, RecurrenceUnitEnum.DUE_DATE,
            )
            assert _live_dates(template) == sorted([date(2026, 1, 16), *_DUE_ON_27])
            assert _figure(typed, seed_user) == Decimal("300.00")
            assert _price_on(template, date(2026, 1, 27), seed_user) == Decimal("500.00")
            assert _price_on(template, date(2026, 2, 27), seed_user) == Decimal("0.00")

    def test_move_drops_it_and_the_due_date_pays_it_all(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Moved, the $300 is gone, and Jan 27 pays the whole $800.00 statement."""
        with app.app_context():
            card = _card(seed_user)
            template = _with_its_payoff(seed_user, seed_periods, card)
            typed = _typed_over(seed_user, template, date(2026, 1, 16))
            page = _read_chooser(_switch(auth_client, card, "statement"))

            _apply(auth_client, page, {f"conflict_decision_{typed}": "use"})

            assert _live_dates(template) == _DUE_ON_27
            assert _price_on(template, date(2026, 1, 27), seed_user) == Decimal("800.00")

    def test_one_on_a_date_both_repeats_name_is_not_asked(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Feb 27 is a payday and a due date: its typed $300 stays and nothing is asked."""
        with app.app_context():
            card = _card(seed_user)
            template = _with_its_payoff(seed_user, seed_periods, card)
            typed = _typed_over(seed_user, template, date(2026, 2, 27))

            resp = _switch(auth_client, card, "statement")

            assert resp.status_code == 302
            assert _live_dates(template) == _DUE_ON_27
            assert _row_on(template, date(2026, 2, 27)).id == typed
            assert _figure(typed, seed_user) == Decimal("300.00")

    def test_one_an_earlier_keep_left_off_the_dates_is_not_asked_again(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A typed Jan 27 kept when the terms moved the due day to the 20th is not asked about.

        Scheduled on the statement balance, the Jan 27 payment typed at $300
        is kept where it is when the terms move every due date to the 20th
        (the terms door's question).  Switched to payoff rows, Jan 27 is on
        neither repeat's dates, and the pass holds it back as typed over: it
        was not on the old repeat's dates, so nothing about it moved, and it
        stays with its $300 without a question.
        """
        with app.app_context():
            card = _card(seed_user)
            template = _with_its_payoff(seed_user, seed_periods, card)
            _switch(auth_client, card, "statement")
            typed = _typed_over(seed_user, template, date(2026, 1, 27))
            page = _read_chooser(auth_client.post(
                f"/accounts/{card.id}/card/terms", data=_DUE_ON_20_FORM,
            ))
            _apply(auth_client, page, {f"conflict_decision_{typed}": "keep"})
            assert date(2026, 1, 27) in _live_dates(template)

            resp = _switch(auth_client, card, "payoff")

            assert resp.status_code == 302
            assert _live_dates(template) == sorted([date(2026, 1, 27), *_PAYDAYS])
            assert _figure(typed, seed_user) == Decimal("300.00")

    def test_a_deleted_one_is_shown_and_offered_no_decision(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The deleted Jan 16 payment is listed as staying deleted, with no radio."""
        with app.app_context():
            card = _card(seed_user)
            template = _with_its_payoff(seed_user, seed_periods, card)
            _deleted(seed_user, template, date(2026, 1, 16))

            resp = _switch(auth_client, card, "statement")

            page = _read_chooser(resp)
            assert "Stays deleted" in resp.get_data(as_text=True)
            assert page.radio_names == set()
            _apply(auth_client, page)
            assert _live_dates(template) == _DUE_ON_27


class TestTheRefusals:
    """Each refusal is a sentence, or the gate's 404, and nothing is written."""

    def test_a_dormant_card_cannot_be_scheduled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A card with no terms has no due dates for a scheduled payment to fall on."""
        with app.app_context():
            card = _dormant_card(seed_user)
            template = _with_its_payoff(seed_user, seed_periods, card)

            _switch(auth_client, card, "everything")

            assert _flashes(auth_client) == [
                card_payoff.SCHEDULED_NEEDS_TERMS.format(card="Visa"),
            ]
            assert _payment_of(template)[:2] == (
                CardPaymentModeEnum.CARD_BALANCE, RecurrenceUnitEnum.PERIOD,
            )
            assert _live_dates(template) == _PAYDAYS

    def test_an_archived_cards_payment_cannot_change(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Archived through its door, the card refuses the switch with a sentence."""
        with app.app_context():
            card = _card(seed_user)
            template = _with_its_payoff(seed_user, seed_periods, card)
            assert auth_client.post(
                f"/accounts/{card.id}/archive",
            ).status_code == 302

            _switch(auth_client, card, "statement")

            # The archive's own flash is still unread before it.
            assert _flashes(auth_client)[1:] == [
                card_payoff.ARCHIVED_CARD_PAYMENT.format(card="Visa"),
            ]
            assert _payment_of(template)[:2] == (
                CardPaymentModeEnum.CARD_BALANCE, RecurrenceUnitEnum.PERIOD,
            )

    def test_a_card_with_no_payment_is_404(self, app, auth_client, seed_user):
        """No door makes a card without its payment, so there is nothing to switch."""
        with app.app_context():
            card = _card(seed_user)
            db.session.commit()

            assert _switch(auth_client, card, "statement").status_code == 404
            assert db.session.query(CardPaymentSettings).count() == 0

    def test_another_owners_card_is_404(
        self, app, seed_user, seed_periods, second_auth_client,
    ):
        """The owner's card switched by another user is not found, and stands."""
        with app.app_context():
            card = _card(seed_user)
            template = _with_its_payoff(seed_user, seed_periods, card)

            assert _switch(second_auth_client, card, "statement").status_code == 404
            assert _payment_of(template)[:2] == (
                CardPaymentModeEnum.CARD_BALANCE, RecurrenceUnitEnum.PERIOD,
            )

    def test_a_checking_account_is_404(self, app, auth_client, seed_user):
        """The door serves a card alone: checking's id is not one."""
        with app.app_context():
            assert _switch(
                auth_client, seed_user["account"], "statement",
            ).status_code == 404

    def test_a_token_the_page_does_not_post_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A crafted payment is refused by the form's schema and changes nothing."""
        with app.app_context():
            card = _card(seed_user)
            template = _with_its_payoff(seed_user, seed_periods, card)

            resp = _switch(auth_client, card, "fixed")

            assert resp.status_code == 302
            assert _flashes(auth_client)
            assert _payment_of(template)[:2] == (
                CardPaymentModeEnum.CARD_BALANCE, RecurrenceUnitEnum.PERIOD,
            )


class _PaymentRadios(HTMLParser):
    """The card's page's payment radios: each one's value, checked and disabled."""

    def __init__(self):
        """Start with no radio read."""
        super().__init__()
        self.radios = {}

    def handle_starttag(self, tag, attrs):
        """Record each ``payment`` radio as ``value: (checked, disabled)``."""
        named = dict(attrs)
        if tag == "input" and named.get("name") == "payment":
            self.radios[named["value"]] = ("checked" in named, "disabled" in named)


def _page(auth_client, card):
    """The card's page: its text and its payment radios."""
    resp = auth_client.get(f"/accounts/{card.id}/details")
    assert resp.status_code == 200
    radios = _PaymentRadios()
    text = resp.get_data(as_text=True)
    radios.feed(text)
    return " ".join(text.split()), radios.radios


class TestTheCardsPage:
    """The Payment card says which payment the card has and offers the others."""

    def test_payoff_rows_are_named_and_checked(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A card paid off each payday says so, from checking, and checks that choice."""
        with app.app_context():
            card = _card(seed_user)
            _with_its_payoff(seed_user, seed_periods, card)

            text, radios = _page(auth_client, card)

            assert (
                "Pay the Visa</span>, from Checking: everything on the card, each "
                "payday." in text
            )
            assert radios == {
                "payoff": (True, False), "statement": (False, False),
                "minimum": (False, False), "everything": (False, False),
            }
            assert "is an extra payment: this payment works out what is left after it" in text

    def test_a_scheduled_payment_is_named_and_checked(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Switched to the statement balance, the card says so and checks it."""
        with app.app_context():
            card = _card(seed_user)
            _with_its_payoff(seed_user, seed_periods, card)
            _switch(auth_client, card, "statement")

            text, radios = _page(auth_client, card)

            assert (
                "from Checking: the statement balance, on each statement's due "
                "date, as you scheduled it with the card company." in text
            )
            assert radios["statement"] == (True, False)
            assert [value for value, (checked, _) in radios.items() if checked] == [
                "statement",
            ]

    def test_a_dormant_card_offers_payoff_rows_alone(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """No terms: the three scheduled choices are drawn disabled, with the reason."""
        with app.app_context():
            card = _dormant_card(seed_user)
            _with_its_payoff(seed_user, seed_periods, card)

            text, radios = _page(auth_client, card)

            assert "Save the card's terms first" in text
            assert radios == {
                "payoff": (True, False), "statement": (False, True),
                "minimum": (False, True), "everything": (False, True),
            }

    def test_an_archived_card_draws_no_form(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Archived, the card says its payment is archived with it and offers nothing."""
        with app.app_context():
            card = _card(seed_user)
            _with_its_payoff(seed_user, seed_periods, card)
            auth_client.post(f"/accounts/{card.id}/archive")

            text, radios = _page(auth_client, card)

            assert "This card is archived, and its payment with it." in text
            assert not radios
