"""Amount-history route tests: creating a definition opens its price series and leaves its version
counter where its INSERT put it, archiving its salary profile gives it one, and an edit records its
price at the date it states, refuses a wild date, and bumps the version counter once.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.utils.dates import display_today
from app.models.ref import TransactionType
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import template_amount_service as tas
from app.services.balance_at import BalanceContext
from app.services.obligations_aggregator import todays_price
from tests._test_helpers import cadence_payload, create_savings_account
from tests._definition_price import newest_price

from ._helpers import (
    _salary_linked_template,
    _template_with_history,
    _transfer_template,
)


# ── The create doors ─────────────────────────────────────────────────


def _geico_create_form(seed_user):
    """What the Recurring form posts to create Geico at $165.30."""
    return {
        "name": "Geico",
        "amount": "165.30",
        "category_id": seed_user["categories"]["Rent"].id,
        "transaction_type_id": db.session.query(TransactionType)
            .filter_by(name="Expense").one().id,
        "account_id": seed_user["account"].id,
        # A transaction template REQUIRES a cadence since plan step
        # balance:X-bi-7b (R-BAL23); the series is what these grade.
        **cadence_payload(),
    }


def _money_market_create_form(seed_user, savings, start_period):
    """What the transfer form posts to create a $250.00 contribution to *savings*."""
    return {
        "name": "Money Market Contribution",
        "amount": "250.00",
        "from_account_id": seed_user["account"].id,
        "to_account_id": savings.id,
        "category_id": seed_user["categories"]["Rent"].id,
        "start_period_id": start_period.id,
    }


def _updates_recorded_for(table_name, row_id):
    """Return how many UPDATEs of ``budget.<table_name>`` row *row_id* the audit log holds.

    The audit trigger writes one row per UPDATE statement the database runs,
    so this counts what was SENT, which a model attribute cannot: the counter
    an UPDATE carries is whatever the mapper assigned.
    """
    return db.session.execute(db.text(
        "SELECT count(*) FROM system.audit_log"
        " WHERE table_schema = 'budget' AND table_name = :table_name"
        " AND row_id = :row_id AND operation = 'UPDATE'"
    ), {"table_name": table_name, "row_id": row_id}).scalar_one()


class TestCreateOpensTheSeries:
    """A definition is born with its price recorded, not just stored."""

    def test_creating_a_recurring_transaction_opens_its_series(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /templates records the stated amount as of today.

        Without this the definition would have a column and no history, and the
        resolver plan step X-au-b builds would have nothing to answer from.
        """
        with app.app_context():
            resp = auth_client.post(
                "/templates", data=_geico_create_form(seed_user),
                follow_redirects=True,
            )
            assert resp.status_code == 200

            template = db.session.query(TransactionTemplate).filter_by(
                name="Geico",
            ).one()
            versions = tas.amount_versions(template)
            assert len(versions) == 1
            assert versions[0].amount == Decimal("165.30")
            assert versions[0].effective_date == display_today()

    def test_creating_a_recurring_transfer_opens_its_series(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /transfers records the stated amount as of today."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            db.session.commit()

            resp = auth_client.post(
                "/transfers",
                data=_money_market_create_form(
                    seed_user, savings, seed_periods_today[0],
                ),
                follow_redirects=True,
            )
            assert resp.status_code == 200

            template = db.session.query(TransferTemplate).filter_by(
                name="Money Market Contribution",
            ).one()
            versions = tas.amount_versions(template)
            assert len(versions) == 1
            assert versions[0].amount == Decimal("250.00")
            assert versions[0].effective_date == display_today()


class TestOpeningTheSeriesBumpsNoCounter:
    """A create leaves the definition's counter at 1 and sends no UPDATE of its row.

    The counter guards a price a form was shown, and a series with no version
    in the database has shown none.  Until plan#2734 the write door bumped it
    for any definition already INSERTed, and the three doors here flush the
    definition before they state its price, so each sent an UPDATE taking
    ``version_id`` from 1 to 2, which the audit log recorded.  (The investment
    and loan-payment doors state the price before their flush, so they never
    reached the bump.)
    """

    def test_the_recurring_transaction_form(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /templates: Geico at $165.30, counter 1, no UPDATE."""
        with app.app_context():
            resp = auth_client.post(
                "/templates", data=_geico_create_form(seed_user),
                follow_redirects=True,
            )
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.query(TransactionTemplate).filter_by(
                name="Geico",
            ).one()
            assert tas.amount_versions(template)[0].amount == Decimal("165.30")
            assert template.version_id == 1
            assert _updates_recorded_for("transaction_templates", template.id) == 0

    def test_the_recurring_transfer_form(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /transfers: a $250.00 contribution, counter 1, no UPDATE."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            db.session.commit()

            resp = auth_client.post(
                "/transfers",
                data=_money_market_create_form(
                    seed_user, savings, seed_periods_today[0],
                ),
                follow_redirects=True,
            )
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.query(TransferTemplate).filter_by(
                name="Money Market Contribution",
            ).one()
            assert tas.amount_versions(template)[0].amount == Decimal("250.00")
            assert template.version_id == 1
            assert _updates_recorded_for("transfer_templates", template.id) == 0

    def test_a_one_off_from_the_grid(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /transactions: a $162.25 one-off's definition, counter 1, no UPDATE.

        The modal and the grid cell share ``one_off.place_one_off``, which
        mints the definition this grades.
        """
        with app.app_context():
            resp = auth_client.post("/transactions", data={
                "name": "Kayla's Kindle",
                "estimated_amount": "162.25",
                "account_id": seed_user["account"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "pay_period_id": seed_periods_today[4].id,
                "transaction_type_id": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                "scenario_id": seed_user["scenario"].id,
            })
            assert resp.status_code == 201, resp.data[:400]

            db.session.expire_all()
            definition = db.session.query(Transaction).filter_by(
                name="Kayla's Kindle",
            ).one().template
            assert tas.amount_versions(definition)[0].amount == Decimal("162.25")
            assert definition.version_id == 1
            assert _updates_recorded_for(
                "transaction_templates", definition.id,
            ) == 0


class TestEligibilityCanBeGAINED:
    """A definition that becomes stated-amount must not be left with no series."""

    def test_archiving_a_salary_profile_opens_its_template_series(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The paycheck stops pricing the rows, so today's paycheck becomes the price.

        With no ACTIVE profile the template starts owning its amount at exactly
        that moment, so the archive opens its series at today's paycheck, as a
        pass priced it while the profile still did (plan step X-bp; it opened
        at the template's ``default_amount`` copy until then).  Found by
        adversarial review: before this, archiving the profile left an eligible
        template holding ZERO versions -- 58 rows on production's one salary
        template -- which is the empty-series gap plan step X-au-b's resolver is
        specified to refuse rather than fall back on.
        """
        with app.app_context():
            template, profile = _salary_linked_template(seed_user)
            tid, pid = template.id, profile.id

            assert tas.owns_its_amount(template) is False
            assert tas.amount_versions(template) == []
            paycheck = todays_price(
                template, BalanceContext.build(seed_user["user"].id),
            ).amount

            resp = auth_client.post(
                f"/salary/{pid}/delete", follow_redirects=True,
            )
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert tas.owns_its_amount(template) is True
            versions = tas.amount_versions(template)
            assert len(versions) == 1
            assert versions[0].amount == paycheck
            assert tas.amount_as_of(template, date(2026, 4, 1)) == paycheck


# ── The edit door ────────────────────────────────────────────────────


class TestEditRecordsThePrice:
    """The form's date is a stored fact, not a transient sweep bound."""

    def test_a_new_amount_at_a_date_appends_a_version(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Raising the premium from September leaves August on the old price.

        The defect ruling R-FI names is the opposite: with a bare scalar, this
        edit reprices every past projection too.
        """
        with app.app_context():
            template = _template_with_history(
                seed_user, [(date(2026, 4, 1), "178.00")],
            )

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Geico",
                "amount": "165.30",
                "effective_from": "2026-09-01",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, template.id)
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [
                (date(2026, 4, 1), Decimal("178.00")),
                (date(2026, 9, 1), Decimal("165.30")),
            ]
            assert tas.amount_as_of(template, date(2026, 8, 1)) == Decimal("178.00")

    def test_a_rename_records_no_price(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Re-submitting the same amount states no change, so nothing is written.

        Every edit form posts the amount back, so gating on "the form carried a
        figure" would litter the history with entries that say nothing.
        """
        with app.app_context():
            template = _template_with_history(
                seed_user, [(date(2026, 4, 1), "178.00")],
            )

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Geico Auto",
                "amount": "178.00",
                "effective_from": "2026-09-01",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, template.id)
            assert template.name == "Geico Auto"
            assert len(tas.amount_versions(template)) == 1

    def test_a_transfer_amount_change_appends_a_version(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The transfer form drives the same door: $500.00 -> $250.00.

        Production's ``Checking -> Fidelity Money Market`` contribution is
        exactly this shape, and it is the only transfer template whose rows
        record two prices.
        """
        with app.app_context():
            template = _transfer_template(seed_user, amount="500.00")

            resp = auth_client.post(f"/transfers/{template.id}", data={
                "name": "Money Market Contribution",
                "amount": "250.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": template.to_account_id,
                "category_id": seed_user["categories"]["Rent"].id,
                "effective_from": "2026-05-21",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransferTemplate, template.id)
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [
                (date(2020, 4, 9), Decimal("500.00")),
                (date(2026, 5, 21), Decimal("250.00")),
            ]


class TestTheOptimisticLockCounter:
    """One edit bumps the template's version counter exactly once."""

    def test_a_rename_plus_amount_submit_bumps_the_counter_once(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The stale-form guard rests on this, and nothing else pins it.

        The amount door suppresses autoflush, and the update route calls it
        BEFORE the field loop, precisely because a rename issues a bulk UPDATE
        that autoflushes whatever is dirty -- so stating the amount afterwards
        left a second dirty write for the commit and took ``version_id`` from 1
        to 3 on one submit.  An adversarial review noted that removing either
        guard would silently break the stale-form check with a green suite.
        """
        with app.app_context():
            template = _template_with_history(
                seed_user, [(date(2020, 4, 1), "178.00")],
            )
            tid = template.id
            before = template.version_id

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Geico Auto",
                "amount": "165.30",
                "version_id": str(before),
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert template.version_id == before + 1
            assert template.name == "Geico Auto"
            assert newest_price(template) == Decimal("165.30")

    def test_a_transfer_rename_plus_amount_submit_bumps_once_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The transfer route calls the same door before its own field loop."""
        with app.app_context():
            template = _transfer_template(seed_user, amount="500.00")
            tid = template.id
            before = template.version_id

            resp = auth_client.post(f"/transfers/{tid}", data={
                "name": "Money Market Savings",
                "amount": "250.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": template.to_account_id,
                "category_id": seed_user["categories"]["Rent"].id,
                "version_id": str(before),
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransferTemplate, tid)
            assert template.version_id == before + 1
            assert newest_price(template) == Decimal("250.00")


    def test_a_wild_effective_date_is_rejected_by_the_form(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A four-digit-year typo never reaches the series.

        Measured by an adversarial review before the bound existed: ``0202``
        was accepted, became the earliest version, and could not be withdrawn.
        The schema now refuses it and the edit is redirected back unapplied.
        """
        with app.app_context():
            template = _template_with_history(
                seed_user, [(date(2020, 4, 1), "178.00")],
            )
            tid = template.id

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Geico",
                "amount": "165.30",
                "effective_from": "0202-08-11",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [(date(2020, 4, 1), Decimal("178.00"))]
            assert newest_price(template) == Decimal("178.00")
