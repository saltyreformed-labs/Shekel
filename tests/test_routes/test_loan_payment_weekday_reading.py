"""A loan's standing payment is read by the loan's contract day, never by a weekday.

Plan step ``recurrence:R8-c``, ruling **R-R96**: the standing payment's
first occurrence is the loan's first contractual installment, on
``payment_day``, so how that date is READ is the loan's too.  Two doors:

* **the edit** renders the "Starts on" row locked, the "repeating on" select
  disabled with it, so a browser posts neither; a crafted ``repeats_on``
  states the opening bound as surely as a crafted date and is refused with
  ``LOAN_PAYMENT_BOUND_IS_DERIVED`` (``refuse_recurrence_update``).
* **the create** into a loan holding no payment derives the start
  (``settle_loan_start``), and a reading posted beside it -- a crafted one
  beside the locked row, or one a script-less browser posts beside a typed
  date -- read a date the rule will not carry, so it is replaced with the
  contract day's reading: none.

Today is frozen on 2026-03-01, before the loan's 2026-04-15 origination; its
payment day is the 1st, so the first installment is Fri 2026-05-01 -- the
first Friday of May, which a carried ``from_start`` would FIT, so a door that
kept the posted reading would store it rather than be refused.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.enums import AcctTypeEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.category import Category
from app.models.transfer_template import TransferTemplate
from app.routes._recurrence_form_refusals import LOAN_PAYMENT_BOUND_IS_DERIVED
from tests._test_helpers import (
    cadence_payload,
    create_loan_account,
    freeze_today,
    make_loan_payment_template,
)
from tests.test_routes._repeats_on_page import form_as_posted, repeats_on_control

#: The day every request is made on, before the loan originates.
_TODAY = date(2026, 3, 1)

#: The loan's origination, and its first installment on payment day 1.
_ORIGINATION = date(2026, 4, 15)
_FIRST_INSTALLMENT = date(2026, 5, 1)

#: The name the create POST gives its template.
_NAME = "R8-c payment"


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    """Freeze today ahead of the loan's origination."""
    freeze_today(monkeypatch, _TODAY)


def _mortgage(seed_user):
    """Return a mortgage originating after today, payment day 1."""
    return create_loan_account(
        seed_user, db.session, name="Mortgage",
        principal=Decimal("200000.00"), rate=Decimal("0.05000"),
        term=360, origination_date=_ORIGINATION, payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )


def _flashes(auth_client):
    """Return the unconsumed flash messages left by an unfollowed redirect."""
    with auth_client.session_transaction() as sess:
        return [message for _category, message in sess.get("_flashes", [])]


def _saved_name(template_id):
    """Return a template's stored name, as the request left it."""
    db.session.expire_all()
    saved = db.session.get(TransferTemplate, template_id)
    return saved.name


def _rule_columns(template_id):
    """Return the stored ``(starts_on, nominal_day, weekday_reading)`` of a template's rule."""
    db.session.expire_all()
    rule = db.session.get(TransferTemplate, template_id).recurrence_rule
    return rule.starts_on, rule.nominal_day, rule.weekday_reading


@pytest.mark.usefixtures("seed_periods")
class TestTheStandingPaymentsEdit:
    """A crafted reading on the standing payment's edit is refused, as a crafted date is."""

    def test_the_rendered_form_posts_no_reading(self, auth_client, seed_user):
        """The precondition that makes a posted reading crafted: the select is disabled."""
        standing = make_loan_payment_template(db.session, seed_user, _mortgage(seed_user))
        db.session.commit()

        page = auth_client.get(f"/transfers/{standing.id}/edit").data.decode()

        assert "disabled" in repeats_on_control(page).selects[0]
        posted = form_as_posted(
            auth_client, f"/transfers/{standing.id}/edit",
            f"/transfers/{standing.id}",
        )
        assert "repeats_on" not in posted
        assert "starts_on" not in posted

    def test_the_same_rename_without_the_reading_saves(self, auth_client, seed_user):
        """The control: the form as the browser posts it, renamed, is accepted."""
        standing = make_loan_payment_template(db.session, seed_user, _mortgage(seed_user))
        db.session.commit()

        resp = auth_client.post(
            f"/transfers/{standing.id}",
            data=form_as_posted(
                auth_client, f"/transfers/{standing.id}/edit",
                f"/transfers/{standing.id}", name="Mortgage, renamed",
            ),
        )

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/transfers"), _flashes(auth_client)
        assert _saved_name(standing.id) == "Mortgage, renamed"
        assert _rule_columns(standing.id) == (_FIRST_INSTALLMENT, None, None)

    def test_a_crafted_reading_is_refused_and_nothing_saves(
        self, auth_client, seed_user,
    ):
        """The rename beside it does not save: the refusal is the whole submission's.

        The form as the browser posts it, renamed, with ``repeats_on`` added
        by hand -- the one difference from the case above.
        """
        standing = make_loan_payment_template(db.session, seed_user, _mortgage(seed_user))
        db.session.commit()
        stored = _rule_columns(standing.id)
        stored_name = _saved_name(standing.id)
        assert stored == (_FIRST_INSTALLMENT, None, None)
        payload = form_as_posted(
            auth_client, f"/transfers/{standing.id}/edit",
            f"/transfers/{standing.id}", name="Mortgage, renamed",
        )
        payload.add("repeats_on", "from_start")

        resp = auth_client.post(f"/transfers/{standing.id}", data=payload)

        assert resp.status_code == 302
        assert LOAN_PAYMENT_BOUND_IS_DERIVED in _flashes(auth_client)
        assert _saved_name(standing.id) == stored_name
        assert _rule_columns(standing.id) == stored


def _debt_category(seed_user):
    """Return a committed category for the create POST."""
    category = Category(
        user_id=seed_user["user"].id, group_name="Debt", item_name="Loan",
    )
    db.session.add(category)
    db.session.commit()
    return category


@pytest.mark.usefixtures("seed_periods")
class TestTheCreateIntoAPaymentLessLoan:
    """A reading posted beside the derived start is replaced with the contract day's."""

    @pytest.mark.parametrize(
        "starts_on",
        [
            pytest.param(None, id="beside the locked row"),
            # Tue 2026-03-17, the third Tuesday of March (3, 10, 17): a
            # reading the typed date allows, so the schema passes it.
            pytest.param(date(2026, 3, 17), id="beside a typed third Tuesday"),
        ],
    )
    def test_the_rule_stores_no_reading(self, auth_client, seed_user, starts_on):
        """Stored on the first installment, read by its day of the month."""
        loan = _mortgage(seed_user)
        category = _debt_category(seed_user)

        resp = auth_client.post("/transfers", data={
            "name": _NAME,
            "from_account_id": str(seed_user["account"].id),
            "to_account_id": str(loan.id),
            "amount": "1200.00",
            "category_id": str(category.id),
            **cadence_payload(
                unit=RecurrenceUnitEnum.MONTH, starts_on=starts_on,
                states_a_start=starts_on is not None,
            ),
            "repeats_on": "from_start",
        })

        assert resp.status_code == 302, _flashes(auth_client)
        assert resp.headers["Location"].endswith("/transfers"), _flashes(auth_client)
        template = db.session.query(TransferTemplate).filter_by(
            to_account_id=loan.id, name=_NAME,
        ).one()
        assert _rule_columns(template.id) == (_FIRST_INSTALLMENT, None, None)
