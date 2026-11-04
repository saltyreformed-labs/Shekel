"""
Shekel Budget App -- only a transfer into a card repeats on statement due dates

Plan step **credit_card:CC-6**, its task plan#2788.  A repeat on each
statement's due date takes its dates from the card it pays, so the read pass
refuses to walk one paying no configured card
(``balance_at._definition_due_dates``).  These are the doors that keep that
refusal from being reached, and the form's offer of the repeat:

* an expense or income definition is refused at both its doors (a field error
  from its schemas), and a payroll line by its own;
* the transfer's create door and its update door refuse a repeat the save
  would LEAVE paying anything but a card with its terms stated, whichever of
  the repeat and the destination the edit changed;
* the account-type door refuses to leave such a repeat paying no card;
* the transfer form offers the repeat, marks which destinations can take it,
  and an edit form preselects a stored one, where a missing option would read
  as "Does not repeat" and delete the rule on an unchanged save.

Every payload here posts what the form posts for that state
(``_form_for``): the browser half, which options a script shows, is graded by
``tests/manual/verify_recurrence_form.py``.
"""

import json
import re
from datetime import date
from decimal import Decimal

import pytest
from marshmallow import ValidationError

from app import ref_cache
from app.enums import (
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
    TxnTypeEnum,
)
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.routes.transfers._helpers import A_DUE_DATE_REPEAT_PAYS_A_CARD
from app.schemas.validation import PaycheckLineCreateSchema
from app.schemas.validation.templates import ONLY_A_TRANSFER_REPEATS_ON_DUE_DATES
from app.services.card_terms import configured_card_ids, is_configured_card
from app.services.pay_calendar import calendar_for
from app.services.recurrence import RecurrenceSpec, author_rule
from app.utils.account_validation import DUE_DATE_REPEAT_KEEPS_ITS_CARD
from tests._test_helpers import (
    cadence_payload,
    create_account_of_type,
    state_template_price,
)
from tests.test_routes.test_card_payment_doors import _flashes, _store_credit_type
from tests.test_routes.test_salary.test_deduction_cadence import _line_form
from tests.test_services.test_due_date_repeat_read_pass import (
    _DUE_ON_27,
    _JAN_2,
    _card,
    _dormant_card,
    _payment,
)


def _due_dates_cadence():
    """The cadence keys the form posts for the due-date repeat from Jan 2."""
    return cadence_payload(unit=RecurrenceUnitEnum.DUE_DATE, starts_on=_JAN_2)


def _months_cadence():
    """The cadence keys the form posts for an every-month repeat from Jan 2."""
    return cadence_payload(unit=RecurrenceUnitEnum.MONTH, starts_on=_JAN_2)


def _form_for(seed_user, to_account, cadence, name="Pay it"):
    """Every control the transfer form posts for a repeating definition.

    A hidden pay-period box and the disabled day and ceiling controls post
    nothing beside a chosen cadence (``recurrence_form.js``), so they are
    absent here as they are from the browser's post.
    """
    return {
        "name": name,
        "amount": "200.00",
        "from_account_id": str(seed_user["account"].id),
        "to_account_id": str(to_account.id),
        "category_id": str(seed_user["categories"]["Rent"].id),
        "recurrence_end_mode": "never",
        **cadence,
    }


def _edit_form_for(template, seed_user, to_account, cadence):
    """What the edit form posts: the create form's controls and the version pin."""
    return {
        **_form_for(seed_user, to_account, cadence, name=template.name),
        "version_id": str(template.version_id),
    }


def _savings(seed_user, name="Rainy day"):
    """A savings account of *seed_user*'s, not a card."""
    return create_account_of_type(seed_user, db.session, "Savings", name)


def _monthly_transfer(seed_user, to_account, name="Monthly"):
    """A repeating transfer on an every-month rule, posted through its own door."""
    return _created(seed_user, to_account, name, monthly=True)


def _created(seed_user, to_account, name, *, monthly):
    """Return a transfer template built as the write door builds one.

    *monthly* gives it an every-month rule from Jan 2; without one it is a
    one-time definition.
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        name=name,
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, Decimal("200.00"))
    if monthly:
        author_rule(
            RecurrenceSpec(
                user_id=template.user_id,
                unit=RecurrenceUnitEnum.MONTH,
                starts_on=_JAN_2,
            ),
            calendar_for(template.user_id),
            template,
        )
    db.session.flush()
    return template


def _unit_of(template):
    """The stored rule's unit id, or ``None`` for a definition with no rule."""
    db.session.expire_all()
    rule = db.session.query(RecurrenceRule).filter_by(
        transfer_template_id=template.id,
    ).one_or_none()
    return None if rule is None else rule.unit_id


def _due_date_unit_id():
    """The ``ref.recurrence_units`` id of the due-date repeat."""
    return ref_cache.recurrence_unit_id(RecurrenceUnitEnum.DUE_DATE)


def _option_tag(body, select_id, value):
    """The opening ``<option>`` tag carrying *value* inside *select_id*."""
    select = re.search(
        rf'<select[^>]*id="{select_id}".*?</select>', body, re.DOTALL,
    ).group(0)
    match = re.search(rf'<option value="{value}"[^>]*>', select, re.DOTALL)
    return None if match is None else match.group(0)


class TestAnExpenseOrIncomeDefinitionIsRefused:
    """Its form never offers the repeat, so a crafted POST meets the schema."""

    def _template_form(self, seed_user, cadence):
        """The controls the transaction form posts, naming *cadence*."""
        return {
            "name": "Rent",
            "amount": "100.00",
            "category_id": str(seed_user["categories"]["Rent"].id),
            "transaction_type_id": str(ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)),
            "account_id": str(seed_user["account"].id),
            "recurrence_end_mode": "never",
            **cadence,
        }

    @pytest.mark.usefixtures("seed_periods")
    def test_the_create_door_refuses_and_writes_nothing(
        self, app, auth_client, seed_user,
    ):
        """No definition, and the sentence rather than the generic prompt."""
        with app.app_context():
            before = db.session.query(TransactionTemplate).count()

            response = auth_client.post(
                "/templates", data=self._template_form(seed_user, _due_dates_cadence()),
            )

            assert response.status_code == 302
            assert _flashes(auth_client) == [ONLY_A_TRANSFER_REPEATS_ON_DUE_DATES]
            assert db.session.query(TransactionTemplate).count() == before

    @pytest.mark.usefixtures("seed_periods")
    def test_the_update_door_refuses_and_leaves_the_rule_alone(
        self, app, auth_client, seed_user,
    ):
        """The stored monthly rule stands, its unit untouched."""
        with app.app_context():
            created = auth_client.post(
                "/templates", data=self._template_form(seed_user, _months_cadence()),
            )
            assert created.status_code == 302
            _flashes(auth_client)
            template = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id, name="Rent",
            ).one()
            rule = template.recurrence_rule
            months = ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH)
            with auth_client.session_transaction() as sess:
                sess.pop("_flashes", None)

            response = auth_client.post(
                f"/templates/{template.id}",
                data={
                    **self._template_form(seed_user, _due_dates_cadence()),
                    "version_id": str(template.version_id),
                },
            )

            assert response.status_code == 302
            assert _flashes(auth_client) == [ONLY_A_TRANSFER_REPEATS_ON_DUE_DATES]
            db.session.expire_all()
            assert rule.unit_id == months


class TestAPayrollLineIsRefused:
    """A line's rule picks paychecks, so its schema refuses the repeat too."""

    def test_the_line_schema_refuses_it_on_the_unit_field(
        self, app, seed_user, seed_periods,
    ):
        """A due-date repeat on a line would raise at the walk: no dates reach it."""
        with app.app_context():
            payload = _line_form("Benefit", amount="10.00", **_due_dates_cadence())
            with pytest.raises(ValidationError) as refused:
                PaycheckLineCreateSchema().load(payload)
            assert "recurrence_unit" in refused.value.normalized_messages()


@pytest.mark.usefixtures("seed_periods")
class TestTheCreateDoor:
    """A new transfer on due dates must pay a configured card."""

    def test_into_a_configured_card_it_generates_on_the_cards_dates(
        self, app, auth_client, seed_user,
    ):
        """The rule stores the unit and the rows fall on the 27th of each month."""
        with app.app_context():
            card = _card(seed_user)
            db.session.commit()

            response = auth_client.post(
                "/transfers", data=_form_for(seed_user, card, _due_dates_cadence()),
            )

            assert response.status_code == 302
            template = db.session.query(TransferTemplate).filter_by(
                user_id=seed_user["user"].id, name="Pay it",
            ).one()
            assert _unit_of(template) == _due_date_unit_id()
            rows = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).all()
            assert sorted(row.due_date for row in rows) == _DUE_ON_27

    @pytest.mark.parametrize("destination", ["savings", "dormant card", "loan"])
    def test_into_anything_else_it_refuses_and_writes_nothing(
        self, app, auth_client, seed_user, destination,
    ):
        """Savings, a card with no terms, and a loan.

        The loan is the case that ORDER decides: its own settling would read
        the unit as a cadence the contract can start, so the refusal runs first.
        """
        with app.app_context():
            if destination == "savings":
                account = _savings(seed_user)
            elif destination == "dormant card":
                account = _dormant_card(seed_user)
            else:
                account = create_account_of_type(
                    seed_user, db.session, "Mortgage", "House",
                )
            db.session.commit()
            before = db.session.query(TransferTemplate).count()
            rules_before = db.session.query(RecurrenceRule).count()

            response = auth_client.post(
                "/transfers", data=_form_for(seed_user, account, _due_dates_cadence()),
            )

            assert response.status_code == 302
            assert response.headers["Location"].endswith("/transfers/new")
            assert _flashes(auth_client) == [
                A_DUE_DATE_REPEAT_PAYS_A_CARD.format(account=account.name),
            ]
            assert db.session.query(TransferTemplate).count() == before
            assert db.session.query(RecurrenceRule).count() == rules_before


@pytest.mark.usefixtures("seed_periods")
class TestTheUpdateDoor:
    """The repeat and the destination the edit LEAVES, whichever it changed."""

    def test_moving_the_repeat_onto_savings_is_refused(
        self, app, auth_client, seed_user,
    ):
        """Nothing moves: the destination and the rule stay the card's."""
        with app.app_context():
            card = _card(seed_user)
            savings = _savings(seed_user)
            template = _payment(seed_user, card)
            db.session.commit()

            response = auth_client.post(
                f"/transfers/{template.id}",
                data=_edit_form_for(template, seed_user, savings, _due_dates_cadence()),
            )

            assert response.status_code == 302
            assert _flashes(auth_client) == [
                A_DUE_DATE_REPEAT_PAYS_A_CARD.format(account="Rainy day"),
            ]
            db.session.expire_all()
            assert template.to_account_id == card.id
            assert _unit_of(template) == _due_date_unit_id()

    def test_a_partial_move_that_restates_no_cadence_is_refused(
        self, app, auth_client, seed_user,
    ):
        """Absent leaves the stored unit alone, so the stored unit is graded."""
        with app.app_context():
            card = _card(seed_user)
            savings = _savings(seed_user)
            template = _payment(seed_user, card)
            db.session.commit()

            response = auth_client.post(
                f"/transfers/{template.id}",
                data={
                    "to_account_id": str(savings.id),
                    "version_id": str(template.version_id),
                },
            )

            assert response.status_code == 302
            assert _flashes(auth_client) == [
                A_DUE_DATE_REPEAT_PAYS_A_CARD.format(account="Rainy day"),
            ]
            db.session.expire_all()
            assert template.to_account_id == card.id

    def test_giving_a_savings_transfers_rule_the_repeat_is_refused(
        self, app, auth_client, seed_user,
    ):
        """The destination stays and the repeat is what changes."""
        with app.app_context():
            savings = _savings(seed_user)
            template = _monthly_transfer(seed_user, savings)
            months = ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH)
            db.session.commit()

            response = auth_client.post(
                f"/transfers/{template.id}",
                data=_edit_form_for(template, seed_user, savings, _due_dates_cadence()),
            )

            assert response.status_code == 302
            assert _flashes(auth_client) == [
                A_DUE_DATE_REPEAT_PAYS_A_CARD.format(account="Rainy day"),
            ]
            assert _unit_of(template) == months

    def test_giving_a_one_time_transfer_into_savings_the_repeat_is_refused(
        self, app, auth_client, seed_user,
    ):
        """The authoring branch of the door: no rule is written."""
        with app.app_context():
            savings = _savings(seed_user)
            template = _created(seed_user, savings, "One time", monthly=False)
            db.session.commit()

            response = auth_client.post(
                f"/transfers/{template.id}",
                data=_edit_form_for(template, seed_user, savings, _due_dates_cadence()),
            )

            assert response.status_code == 302
            assert _flashes(auth_client) == [
                A_DUE_DATE_REPEAT_PAYS_A_CARD.format(account="Rainy day"),
            ]
            assert _unit_of(template) is None

    def test_giving_a_one_time_transfer_into_a_card_the_repeat_goes_through(
        self, app, auth_client, seed_user,
    ):
        """The authoring branch accepts it, and its rows take the card's dates."""
        with app.app_context():
            card = _card(seed_user)
            template = _created(seed_user, card, "One time", monthly=False)
            db.session.commit()

            response = auth_client.post(
                f"/transfers/{template.id}",
                data=_edit_form_for(template, seed_user, card, _due_dates_cadence()),
            )

            assert response.status_code == 302
            assert _unit_of(template) == _due_date_unit_id()

    def test_moving_between_two_configured_cards_goes_through(
        self, app, auth_client, seed_user,
    ):
        """The dates follow the destination, so the second card's are used."""
        with app.app_context():
            visa = _card(seed_user)
            amex = _card(seed_user, "Amex", due_day=15)
            template = _payment(seed_user, visa)
            db.session.commit()

            response = auth_client.post(
                f"/transfers/{template.id}",
                data=_edit_form_for(template, seed_user, amex, _due_dates_cadence()),
            )

            assert response.status_code == 302
            assert response.headers["Location"].endswith("/transfers")
            db.session.expire_all()
            assert template.to_account_id == amex.id

    def test_moving_to_savings_while_leaving_the_repeat_goes_through(
        self, app, auth_client, seed_user,
    ):
        """The edit leaves a monthly transfer into savings, which pays no card."""
        with app.app_context():
            card = _card(seed_user)
            savings = _savings(seed_user)
            template = _payment(seed_user, card)
            db.session.commit()

            response = auth_client.post(
                f"/transfers/{template.id}",
                data=_edit_form_for(template, seed_user, savings, _months_cadence()),
            )

            assert response.status_code == 302
            assert response.headers["Location"].endswith("/transfers")
            db.session.expire_all()
            assert template.to_account_id == savings.id
            assert _unit_of(template) == ref_cache.recurrence_unit_id(
                RecurrenceUnitEnum.MONTH,
            )

    def test_an_unchanged_save_of_a_stored_repeat_goes_through(
        self, app, auth_client, seed_user,
    ):
        """The edit form's own post of a stored due-date repeat stores it again."""
        with app.app_context():
            card = _card(seed_user)
            template = _payment(seed_user, card)
            db.session.commit()

            response = auth_client.post(
                f"/transfers/{template.id}",
                data=_edit_form_for(template, seed_user, card, _due_dates_cadence()),
            )

            assert response.status_code == 302
            assert response.headers["Location"].endswith("/transfers")
            assert _unit_of(template) == _due_date_unit_id()

    def test_a_month_end_rule_can_move_to_the_repeat_with_no_stated_start(
        self, app, auth_client, seed_user,
    ):
        """A stored nominal day does not survive onto a unit with no day to mean.

        Apr 30 meaning the last day of the month: a partial post leaves the
        stored date in place, and carrying its day across would build the pair
        ``RecurrenceSpec`` refuses, a 500 rather than a save.
        """
        with app.app_context():
            card = _card(seed_user)
            template = _created(seed_user, card, "Month end", monthly=False)
            rule = author_rule(
                RecurrenceSpec(
                    user_id=template.user_id,
                    unit=RecurrenceUnitEnum.MONTH,
                    starts_on=date(2026, 4, 30),
                    nominal_day=31,
                ),
                calendar_for(template.user_id),
                template,
            )
            db.session.commit()
            assert rule.nominal_day == 31
            cadence = _due_dates_cadence()
            del cadence["starts_on"]

            response = auth_client.post(
                f"/transfers/{template.id}",
                data=_edit_form_for(template, seed_user, card, cadence),
            )

            assert response.status_code == 302
            db.session.expire_all()
            assert rule.unit_id == _due_date_unit_id()
            assert rule.nominal_day is None


@pytest.mark.usefixtures("seed_periods")
class TestTheAccountTypeDoor:
    """A card a due-date repeat names stays a card, settings row or none."""

    def test_re_typing_it_is_refused(self, app, auth_client, seed_user):
        """A plain fixed payment carries no settings row and still needs a card."""
        with app.app_context():
            card = _card(seed_user)
            _payment(seed_user, card)
            store_credit = _store_credit_type(seed_user)
            card_type = card.account_type_id

            response = auth_client.post(
                f"/accounts/{card.id}",
                data={"account_type_id": str(store_credit.id)},
            )

            assert response.status_code == 302
            assert _flashes(auth_client) == [DUE_DATE_REPEAT_KEEPS_ITS_CARD.format(
                card="Visa", payment="Pay the Visa",
            )]
            db.session.expire_all()
            assert card.account_type_id == card_type

    def test_an_archived_repeat_still_holds_it(self, app, auth_client, seed_user):
        """The read pass walks an archived repeat for the rows it would restore."""
        with app.app_context():
            card = _card(seed_user)
            template = _payment(seed_user, card)
            template.is_active = False
            store_credit = _store_credit_type(seed_user)

            response = auth_client.post(
                f"/accounts/{card.id}",
                data={"account_type_id": str(store_credit.id)},
            )

            assert _flashes(auth_client) == [DUE_DATE_REPEAT_KEEPS_ITS_CARD.format(
                card="Visa", payment="Pay the Visa",
            )]
            assert response.status_code == 302

    def test_a_card_only_a_monthly_transfer_pays_can_be_re_typed(
        self, app, auth_client, seed_user,
    ):
        """Any other repeat's dates are its own, so nothing holds the type."""
        with app.app_context():
            card = _card(seed_user)
            _monthly_transfer(seed_user, card)
            store_credit = _store_credit_type(seed_user)

            response = auth_client.post(
                f"/accounts/{card.id}",
                data={"account_type_id": str(store_credit.id)},
            )

            assert response.status_code == 302
            db.session.expire_all()
            assert card.account_type_id == store_credit.id


class TestAConfiguredCard:
    """The one predicate the doors, the gate and the form's offer read."""

    def test_it_is_a_card_with_terms_and_nothing_else(self, app, seed_user):
        """A dormant card and a savings account are not; a card with terms is."""
        with app.app_context():
            card = _card(seed_user)
            dormant = _dormant_card(seed_user, "Dormant")
            savings = _savings(seed_user)

            assert is_configured_card(card) is True
            assert is_configured_card(dormant) is False
            assert is_configured_card(savings) is False
            assert configured_card_ids([card, dormant, savings]) == {card.id}

    def test_terms_that_outlive_a_re_type_do_not_make_a_card(self, app, seed_user):
        """The rows outlive a re-type, so revolving is asked first."""
        with app.app_context():
            account = _card(seed_user)
            account.account_type_id = _store_credit_type(seed_user).id
            db.session.flush()
            db.session.expire(account)

            assert is_configured_card(account) is False


@pytest.mark.usefixtures("seed_periods")
class TestTheFormsOffer:
    """The transfer form offers it, and the transaction form never does."""

    def test_the_transfer_form_marks_only_a_configured_card(
        self, app, auth_client, seed_user,
    ):
        """A card with terms carries the fact; a dormant card and savings do not."""
        with app.app_context():
            card = _card(seed_user)
            dormant = _dormant_card(seed_user, "Dormant")
            savings = _savings(seed_user)
            db.session.commit()

            body = auth_client.get("/transfers/new").data.decode()

            assert "data-has-due-dates" in _option_tag(body, "to_account_id", card.id)
            for account in (dormant, savings, seed_user["account"]):
                assert "data-has-due-dates" not in _option_tag(
                    body, "to_account_id", account.id,
                )

    def test_the_transfer_form_offers_the_unit_with_its_two_facts(
        self, app, auth_client, seed_user,
    ):
        """The script is told it needs a card and holds the interval at 1."""
        with app.app_context():
            body = auth_client.get("/transfers/new").data.decode()

            due_dates = _due_date_unit_id()
            assert "statement due dates" in _option_tag_text(
                body, "recurrence_unit", due_dates,
            )
            offers = json.loads(
                re.search(r'data-cadence-options="([^"]*)"', body).group(1)
                .replace("&#34;", '"'),
            )
            for offer in offers:
                if offer["unit_id"] == due_dates:
                    assert offer["needs_a_card"] is True
                    assert offer["fixed_interval"] == 1
                    assert offer["placement_id"] == ref_cache.period_placement_id(
                        PeriodPlacementEnum.CONTAINING_DATE,
                    )
                else:
                    assert offer["needs_a_card"] is False
                    assert offer["fixed_interval"] is None

    def test_the_transaction_form_does_not_offer_it(
        self, app, auth_client, seed_user,
    ):
        """Neither the control nor the wire names it."""
        with app.app_context():
            body = auth_client.get("/templates/new").data.decode()

            assert _option_tag(body, "recurrence_unit", _due_date_unit_id()) is None
            assert "statement due date" not in body

    def test_an_edit_form_preselects_a_stored_repeat(
        self, app, auth_client, seed_user,
    ):
        """An option missing here would read as "Does not repeat" and delete the rule."""
        with app.app_context():
            card = _card(seed_user)
            template = _payment(seed_user, card)
            db.session.commit()

            body = auth_client.get(f"/transfers/{template.id}/edit").data.decode()

            assert "selected" in _option_tag(
                body, "recurrence_unit", _due_date_unit_id(),
            )
            assert "selected" in _option_tag(
                body, "recurrence_placement",
                ref_cache.period_placement_id(PeriodPlacementEnum.CONTAINING_DATE),
            )
            interval = re.search(r'<input[^>]*id="interval_n"[^>]*>', body, re.DOTALL)
            assert 'value="1"' in interval.group(0)


def _option_tag_text(body, select_id, value):
    """The whole ``<option>`` carrying *value* inside *select_id*, text included."""
    select = re.search(
        rf'<select[^>]*id="{select_id}".*?</select>', body, re.DOTALL,
    ).group(0)
    return re.search(
        rf'<option value="{value}"[^>]*>.*?</option>', select, re.DOTALL,
    ).group(0)
