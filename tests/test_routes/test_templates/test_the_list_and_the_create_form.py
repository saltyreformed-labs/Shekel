"""Template route tests: the Recurring list, and the create form and its POST, with its cadence,
its opening bound, its validation and its ownership refusals.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.enums import RecurrenceUnitEnum
from app.extensions import db
from app.models.ref import TransactionType
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from tests._test_helpers import cadence_payload, create_loan_account
from tests.oracles.recurrence_baseline import EVERY_PERIOD

from ._helpers import _TODAYS_PERIOD_INDEX, _create_template, _create_other_user_with_template


# ── List Tests ───────────────────────────────────────────────────────


class TestTemplateList:
    """Tests for GET /templates."""

    def test_list_templates(self, app, auth_client, seed_user):
        """GET /templates renders the template list page."""
        with app.app_context():
            # A definition WITH a rule: the list shows those and only those
            # since plan step balance:X-bi-7b (R-BAL23); a rule-less one is a
            # one-off's, graded absent in ``TestTheListShowsDefinitionsWithARule``.
            _create_template(seed_user, name="Car Payment", cadence=EVERY_PERIOD)

            resp = auth_client.get("/templates")
            assert resp.status_code == 200
            assert b"Car Payment" in resp.data

    def test_list_templates_empty(self, app, auth_client, seed_user):
        """GET /templates renders correctly when user has no definitions."""
        with app.app_context():
            resp = auth_client.get("/templates")
            assert resp.status_code == 200
            assert b"No active recurring definitions" in resp.data
            assert b"Car Payment" not in resp.data


# ── Create Tests ─────────────────────────────────────────────────────


class TestTemplateCreate:
    """Tests for GET /templates/new and POST /templates."""

    def test_new_template_form(self, app, auth_client, seed_user, seed_periods_today):
        """GET /templates/new renders the creation form."""
        with app.app_context():
            resp = auth_client.get("/templates/new")
            assert resp.status_code == 200
            assert b"New Recurring Transaction" in resp.data
            assert b'name="name"' in resp.data
            assert b'name="amount"' in resp.data
            # The three cadence controls plan step R7b-2 put in place of the
            # single pattern <select>.  Both interval inputs carry the same
            # name, which is why only one of them is ever enabled.
            assert b'name="recurrence_unit"' in resp.data
            assert b'name="interval_n"' in resp.data
            assert b'name="recurrence_placement"' in resp.data

    def test_create_with_no_cadence_is_refused_with_the_payload_a_browser_posts(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An empty cadence is REFUSED, not read as "does not repeat" (R-BAL23).

        Plan step ``balance:X-bi-7b``: a transaction definition with no rule
        is a ONE-OFF, made at the Budget grid through the one-off producer,
        so the form no longer offers *Does not repeat* and the schema refuses
        the empty unit its placeholder posts
        (``TemplateCreateSchema.validate_a_cadence_is_chosen``).  The payload
        is browser-shaped -- every control the page renders, empty where the
        user touched nothing -- so this grades the wire the form actually
        holds, and what a hidden ``starts_on`` box still submits reaches the
        refusal too rather than a constructor.  No definition is minted.
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            category = seed_user["categories"]["Rent"]
            before = db.session.query(TransactionTemplate).count()

            resp = auth_client.post("/templates", data={
                "name": "Browser Shaped No Recurrence",
                "amount": "24.99",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                # The placeholder -- an empty unit, with every other
                # recurrence control posting the value it renders with.
                "recurrence_unit": "",
                "recurrence_placement": "",
                "interval_n": "1",
                "day_of_month": "",
                "month_of_year": "",
                "start_date": "",
                "recurrence_end_mode": "never",
                "end_date": "",
                "max_occurrences": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"needs a cadence" in resp.data
            assert b"Budget grid" in resp.data
            assert db.session.query(TransactionTemplate).count() == before
            assert db.session.query(TransactionTemplate).filter_by(
                name="Browser Shaped No Recurrence",
            ).count() == 0

    def test_create_with_a_starts_on_date_bounds_the_rule(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The positive twin: a stated "Starts on" reaches the rule.

        Without this the case above would pass against a route that dropped
        the key on EVERY branch rather than only the no-cadence one -- which
        would silently discard the opening bound the user typed.
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            category = seed_user["categories"]["Rent"]
            starts_on = seed_periods_today[1].start_date

            resp = auth_client.post("/templates", data={
                "name": "Browser Shaped With Start",
                "amount": "24.99",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(),
                "starts_on": starts_on.isoformat(),
                "recurrence_end_mode": "never",
                "end_date": "",
                "max_occurrences": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            template = (
                db.session.query(TransactionTemplate)
                .filter_by(name="Browser Shaped With Start")
                .one()
            )
            assert template.recurrence_rule is not None
            assert template.recurrence_rule.starts_on == starts_on

    def test_create_a_weekly_template_at_the_interval_ceiling_fires_once(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Every 2,147,483,647 weeks saves, and generates its first row alone.

        The column's ceiling is what the interval box admits, and until plan
        step R8-b made the unit authorable the WEEK walk's ``timedelta``
        stride raised ``OverflowError`` for it -- a 500 on save, which
        generates rows through the same walk the preview reads.  The second
        occurrence lies past the application's calendar, so the honest
        answer is one row, due on the first occurrence: the paycheck that
        covers it funds it (ruling R-R94).
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            category = seed_user["categories"]["Rent"]
            starts_on = seed_periods_today[4].start_date + timedelta(days=3)

            resp = auth_client.post("/templates", data={
                "name": "Once In A Long While",
                "amount": "24.99",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(
                    unit=RecurrenceUnitEnum.WEEK, interval_n=2_147_483_647,
                    starts_on=starts_on,
                ),
            }, follow_redirects=True)

            assert resp.status_code == 200
            template = (
                db.session.query(TransactionTemplate)
                .filter_by(name="Once In A Long While")
                .one()
            )
            assert template.recurrence_rule.interval_n == 2_147_483_647
            rows = db.session.query(Transaction).filter_by(
                template_id=template.id, is_deleted=False,
            ).all()
            assert [(row.occurs_on, row.due_date) for row in rows] == [
                (starts_on, starts_on),
            ]

    def test_create_template_with_no_unit_at_all_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An ABSENT unit is refused on a create exactly as an empty one is.

        A create states a whole definition, so there is no stored cadence for
        an omitted field to leave alone; the schema treats absent and empty
        as one statement (R-BAL23).  The update schema's narrower rule is
        graded in ``test_recurrence_clear``.
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "Internet Bill",
                "amount": "79.99",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"needs a cadence" in resp.data
            assert db.session.query(TransactionTemplate).filter_by(
                name="Internet Bill",
            ).count() == 0

    def test_create_template_with_recurrence(self, app, auth_client, seed_user, seed_periods_today):
        """POST /templates creates a template with recurrence and generates transactions."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "Rent Payment",
                "amount": "1500.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data

            template = db.session.query(TransactionTemplate).filter_by(
                name="Rent Payment"
            ).one()
            assert template.recurrence_rule is not None

            # An every-paycheck rule generates one transaction per period FROM
            # ITS FIRST OCCURRENCE ONWARD.  ``seed_periods_today`` creates 10
            # biweekly periods with today in index 4, and a create form
            # defaults the first occurrence to today -- so the rule fires in
            # periods 4..9 and NOT in the four that have already closed.
            #
            # It asserted 10 until plan step R7c-b, which is the defect that
            # step closed rather than a count that drifted: an unstated opening
            # bound meant "start with the schedule", and the create routes
            # generate with no lower window bound, so a $2,000.00 rent template
            # created today wrote backdated rows into pay periods the user had
            # already reconciled.  ``TestACreateDoesNotBackfillClosedPayPeriods``
            # states the property; this is the same fact seen from the count.
            expected = len(seed_periods_today) - _TODAYS_PERIOD_INDEX
            assert expected == 6
            txns = db.session.query(Transaction).filter_by(
                template_id=template.id
            ).all()
            assert len(txns) == expected
            # Each transaction maps to a distinct period, and none of them is
            # one that closed before the rule began.
            period_ids = {txn.pay_period_id for txn in txns}
            assert len(period_ids) == expected
            assert period_ids.isdisjoint({
                period.id
                for period in seed_periods_today[:_TODAYS_PERIOD_INDEX]
            })

    def test_create_template_validation_error(self, app, auth_client, seed_user):
        """POST /templates with missing required fields shows validation error."""
        with app.app_context():
            resp = auth_client.post("/templates", data={
                # Missing name, amount, category, type, account.
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

    def test_create_template_invalid_account(self, app, auth_client, seed_user):
        """POST /templates with another user's account is rejected."""
        with app.app_context():
            other = _create_other_user_with_template()
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "Sneaky Template",
                "amount": "100.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": other["account"].id,  # Other user's account.
                # A cadence, because a transaction template REQUIRES one since plan step
                # balance:X-bi-7b (R-BAL23); what this case grades is unchanged.
                **cadence_payload(),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid account" in resp.data

    def test_create_template_invalid_category(self, app, auth_client, seed_user):
        """POST /templates with another user's category is rejected."""
        with app.app_context():
            other = _create_other_user_with_template()
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            resp = auth_client.post("/templates", data={
                "name": "Sneaky Template",
                "amount": "100.00",
                "category_id": other["category"].id,  # Other user's category.
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                # A cadence, because a transaction template REQUIRES one since plan step
                # balance:X-bi-7b (R-BAL23); what this case grades is unchanged.
                **cadence_payload(),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid category" in resp.data

    def test_create_template_on_loan_account_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /templates targeting a loan account is refused; no template made.

        A template on a loan would have the recurrence engine generate raw
        transactions onto the loan account (``recurrence_engine`` copies
        ``template.account_id``) -- the N-11 shape the create routes already
        refuse.  The shared ``_validate_template_form`` gate closes this
        second source.  The negative control is
        ``test_create_template_no_recurrence``: the identical form on a
        checking account is accepted.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session,
                principal=Decimal("200000.00"), rate=Decimal("0.06"),
                origination_date=date(2026, 1, 1), name="Loan Template Target",
            )
            db.session.commit()
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "On A Loan",
                "amount": "300.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": loan.id,
                # A cadence, because a transaction template REQUIRES one since plan step
                # balance:X-bi-7b (R-BAL23); what this case grades is unchanged.
                **cadence_payload(),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"not a transaction sum" in resp.data
            assert db.session.query(TransactionTemplate).filter_by(
                name="On A Loan",
            ).count() == 0
