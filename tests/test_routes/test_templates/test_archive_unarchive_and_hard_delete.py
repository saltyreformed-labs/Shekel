"""Template route tests: archiving a definition and its projected rows, bringing it back, and
deleting it for good, which a definition holding a settled row or named by a merchant rule is
archived instead, and moving one a merchant rule names, which is refused.
"""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.account import Account
from app.models.ref import Status
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.utils.archive_helpers import HeldMovements
from app.services import status_seam, transaction_service
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.row_valuation import settled_figure
from tests._test_helpers import (
    all_periods,
    constraint_name_from,
    generate_row_of,
    make_income_template,
    settlement_if_settling,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD

from ._helpers import _create_template, _create_other_user_with_template


# ── Archive Tests ────────────────────────────────────────────────────


class TestTemplateArchive:
    """Tests for POST /templates/<id>/archive."""

    def test_archive_and_soft_deletes(self, app, auth_client, seed_user, seed_periods_today):
        """POST /templates/<id>/archive archives template and soft-deletes projected txns."""
        with app.app_context():
            template = _create_template(seed_user, cadence=EVERY_PERIOD)

            # Generate projected transactions.
            from app.services import recurrence_engine
            scenario = seed_user["scenario"]
            periods = all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in periods},
            ), scenario.id)
            db.session.commit()

            txn_count = db.session.query(Transaction).filter_by(
                template_id=template.id, is_deleted=False,
            ).count()
            # every_period pattern generates 1 transaction per period; 10 seeded periods.
            assert txn_count == 10

            resp = auth_client.post(
                f"/templates/{template.id}/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived" in resp.data

            db.session.refresh(template)
            assert template.is_active is False

            # All projected transactions should be soft-deleted.
            remaining = db.session.query(Transaction).filter_by(
                template_id=template.id, is_deleted=False,
            ).count()
            assert remaining == 0

    def test_archive_template_idor(self, app, auth_client, seed_user):
        """POST /templates/<id>/archive for another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()

            resp = auth_client.post(
                f"/templates/{other['template'].id}/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 404

            # Verify template still active.
            db.session.refresh(other["template"])
            assert other["template"].is_active is True

    def test_archive_nonexistent_template(self, app, auth_client, seed_user):
        """POST /templates/999999/archive for missing template returns 404 (security)."""
        with app.app_context():
            resp = auth_client.post(
                "/templates/999999/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 404


# ── Unarchive Tests ──────────────────────────────────────────────────


class TestTemplateUnarchive:
    """Tests for POST /templates/<id>/unarchive."""

    def test_unarchive_restores_transactions(self, app, auth_client, seed_user, seed_periods_today):
        """POST /templates/<id>/unarchive restores soft-deleted txns."""
        with app.app_context():
            template = _create_template(seed_user, cadence=EVERY_PERIOD)

            # Generate and then delete.
            from app.services import recurrence_engine
            scenario = seed_user["scenario"]
            periods = all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in periods},
            ), scenario.id)
            db.session.commit()

            # Archive via the archive route.
            auth_client.post(f"/templates/{template.id}/archive")

            db.session.refresh(template)
            assert template.is_active is False

            # Now unarchive.
            resp = auth_client.post(
                f"/templates/{template.id}/unarchive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"unarchived" in resp.data

            db.session.refresh(template)
            assert template.is_active is True

            # Transactions should be restored: every_period generates 1 per period,
            # seed_periods_today creates 10 biweekly periods
            active_txns = db.session.query(Transaction).filter_by(
                template_id=template.id, is_deleted=False,
            ).count()
            assert active_txns == 10

    def test_unarchive_template_idor(self, app, auth_client, seed_user):
        """POST /templates/<id>/unarchive for another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()

            resp = auth_client.post(
                f"/templates/{other['template'].id}/unarchive",
                follow_redirects=True,
            )
            assert resp.status_code == 404


# ── Hard Delete Tests (5A.5-2) ─────────────────────────────────────


class TestTemplateHardDelete:
    """Tests for POST /templates/<id>/hard-delete (permanent deletion)."""

    def test_hard_delete_template_no_history(self, app, auth_client, seed_user, seed_periods_today):
        """C-5A.5-11: Template with only Projected txns is permanently deleted."""
        with app.app_context():
            template = _create_template(seed_user, cadence=EVERY_PERIOD)

            # Generate projected transactions.
            from app.services import recurrence_engine
            scenario = seed_user["scenario"]
            periods = all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in periods},
            ), scenario.id)
            db.session.commit()

            template_id = template.id
            txn_count = db.session.query(Transaction).filter_by(
                template_id=template_id,
            ).count()
            assert txn_count == 10

            resp = auth_client.post(
                f"/templates/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data

            # Template is gone.
            assert db.session.get(TransactionTemplate, template_id) is None

            # All linked transactions are gone.
            remaining = db.session.query(Transaction).filter_by(
                template_id=template_id,
            ).count()
            assert remaining == 0

    def test_hard_delete_template_no_transactions(self, app, auth_client, seed_user):
        """C-5A.5-11b: Template with zero transactions is permanently deleted."""
        with app.app_context():
            template = _create_template(seed_user)
            template_id = template.id

            resp = auth_client.post(
                f"/templates/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data
            assert db.session.get(TransactionTemplate, template_id) is None

    def test_MOVING_a_template_a_merchant_rule_names_is_refused(
        self, app, auth_client, seed_user,
    ):
        """A designed refusal where there was an unhandled 500.

        Plan step ``bank_import:X-gd-2``.
        ``fk_merchant_rules_template_account`` is composite over
        ``(template_id, account_id)`` with no ``ON UPDATE``, so moving a
        template between accounts orphans every rule naming it and PostgreSQL
        raises -- an IntegrityError ``commit_or_handle_stale`` does not catch
        and no error handler renders.  Verified against the developer's own
        data 2026-08-26: ``UPDATE budget.transaction_templates SET account_id
        = 10 WHERE id = 19`` is refused by that constraint.

        **The 500 predates this step and the UNRECOVERABILITY does not**: the
        owner's way out used to be withdrawing the rule, and ruling R-GS
        removed the withdrawal.  Found by an adversarial security review.
        """
        from app.models.merchant import (  # pylint: disable=import-outside-toplevel
            Merchant,
        )
        from app.models.merchant_rule import (  # pylint: disable=import-outside-toplevel
            MerchantRule,
        )

        with app.app_context():
            template = _create_template(seed_user)
            template_id = template.id
            origin = template.account_id
            elsewhere = Account(
                user_id=seed_user["user"].id,
                name="Second Checking",
                account_type_id=seed_user["account"].account_type_id,
            )
            db.session.add(elsewhere)
            db.session.flush()
            merchant = Merchant(account_id=origin, name="Food Lion")
            db.session.add(merchant)
            db.session.flush()
            db.session.add(MerchantRule(
                user_id=seed_user["user"].id,
                account_id=origin,
                merchant_id=merchant.id,
                template_id=template_id,
                never_a_purchase=False,
            ))
            db.session.commit()

            resp = auth_client.post(
                f"/templates/{template_id}",
                data={"account_id": str(elsewhere.id)},
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"cannot be moved to another" in resp.data
            db.session.expire_all()
            assert db.session.get(
                TransactionTemplate, template_id,
            ).account_id == origin

    def test_MOVING_a_template_NO_rule_names_still_works(
        self, app, auth_client, seed_user,
    ):
        """The firing control: the guard refuses a move, it does not forbid one.

        Without it the case above would be satisfied by a door that refused
        every account change, which is an ordinary edit the form offers.
        """
        with app.app_context():
            template = _create_template(seed_user)
            template_id = template.id
            elsewhere = Account(
                user_id=seed_user["user"].id,
                name="Second Checking",
                account_type_id=seed_user["account"].account_type_id,
            )
            db.session.add(elsewhere)
            db.session.commit()
            target = elsewhere.id

            resp = auth_client.post(
                f"/templates/{template_id}",
                data={"account_id": str(target)},
                follow_redirects=True,
            )

            assert resp.status_code == 200
            db.session.expire_all()
            assert db.session.get(
                TransactionTemplate, template_id,
            ).account_id == target

    def test_hard_delete_template_a_MERCHANT_RULE_names_is_archived(
        self, app, auth_client, seed_user,
    ):
        """A stated answer is not collateral of deleting the row it names.

        Plan step ``bank_import:X-gd-2``.  ``fk_merchant_rules_template_account``
        is ON DELETE CASCADE, and this door gated only on settled TRANSACTIONS
        -- so permanently deleting a template destroyed every standing merchant
        rule filing into it, under a flash that mentioned only the template.
        Ruling **R-GS** is what makes it matter rather than untidy: a rule row
        is never un-stated by its owner, so a silent cascade was the only way
        one could vanish at all.

        Measured on the developer's dev database 2026-08-26: 16 of 29 rules
        name a template, and template 19 (`Clothes`) carried a rule and ZERO
        settled transactions -- so the permanent arm was live on it.  Found by
        three adversarial reviews the same day; the category twin of this door
        was already closed and this one was not.
        """
        from app.models.merchant import (  # pylint: disable=import-outside-toplevel
            Merchant,
        )
        from app.models.merchant_rule import (  # pylint: disable=import-outside-toplevel
            MerchantRule,
        )

        with app.app_context():
            template = _create_template(seed_user)
            template_id = template.id
            merchant = Merchant(
                account_id=seed_user["account"].id, name="Food Lion",
            )
            db.session.add(merchant)
            db.session.flush()
            rule = MerchantRule(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                merchant_id=merchant.id,
                template_id=template_id,
                never_a_purchase=False,
            )
            db.session.add(rule)
            db.session.commit()
            rule_id = rule.id

            resp = auth_client.post(
                f"/templates/{template_id}/hard-delete",
                follow_redirects=True,
            )

            assert resp.status_code == 200
            # The sentence names the REASON.  Saying "has payment history" for
            # a template whose only usage is a rule is the screens-stating-what-
            # is-false defect this arc keeps closing.
            assert b"where a merchant" in resp.data
            assert b"payment history" not in resp.data
            surviving = db.session.get(TransactionTemplate, template_id)
            assert surviving is not None
            assert surviving.is_active is False
            assert db.session.get(MerchantRule, rule_id) is not None

    def test_hard_delete_template_with_history(self, app, auth_client, seed_user, seed_periods_today):
        """C-5A.5-12: Template with Paid txn is blocked and archived instead."""
        with app.app_context():
            template = _create_template(seed_user, cadence=EVERY_PERIOD)

            from app.services import recurrence_engine
            scenario = seed_user["scenario"]
            periods_list = all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in periods_list},
            ), scenario.id)
            db.session.commit()

            # Mark one transaction as Paid.
            paid_status = db.session.query(Status).filter_by(name="Paid").one()
            txn = db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).first()
            # Through the real seam, which writes the whole settlement record
            # in one act -- the day, the figure and how it is known (plan step
            # X-au-c3).  A bare status assign leaves a state the record's own
            # CHECKs refuse.
            status_seam.apply_status_change(
                txn, paid_status.id,
                settlement=settlement_if_settling(txn, paid_status.id),
            )
            db.session.commit()

            resp = auth_client.post(
                f"/templates/{template.id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"has payment history" in resp.data
            assert b"archived instead" in resp.data

            # Template still exists but is archived.
            db.session.refresh(template)
            assert template.is_active is False

            # The Paid transaction is untouched.
            db.session.refresh(txn)
            assert txn.status_id == paid_status.id
            assert txn.is_deleted is False

            # Projected transactions are soft-deleted.
            projected_status = db.session.query(Status).filter_by(name="Projected").one()
            projected_remaining = db.session.query(Transaction).filter(
                Transaction.template_id == template.id,
                Transaction.status_id == projected_status.id,
                Transaction.is_deleted.is_(False),
            ).count()
            assert projected_remaining == 0

    def test_hard_delete_template_with_history_already_archived(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C-5A.5-12b: Already-archived template with Paid history stays archived without re-archiving."""
        with app.app_context():
            template = _create_template(seed_user, cadence=EVERY_PERIOD)

            from app.services import recurrence_engine
            scenario = seed_user["scenario"]
            periods_list = all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in periods_list},
            ), scenario.id)
            db.session.commit()

            # Mark one transaction as Paid.
            paid_status = db.session.query(Status).filter_by(name="Paid").one()
            txn = db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).first()
            # Through the real seam, which writes the whole settlement record
            # in one act -- the day, the figure and how it is known (plan step
            # X-au-c3).  A bare status assign leaves a state the record's own
            # CHECKs refuse.
            status_seam.apply_status_change(
                txn, paid_status.id,
                settlement=settlement_if_settling(txn, paid_status.id),
            )

            # Pre-archive the template.
            template.is_active = False
            db.session.commit()

            resp = auth_client.post(
                f"/templates/{template.id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"has payment history" in resp.data

            # Template still exists, still archived.
            db.session.refresh(template)
            assert template.is_active is False

    def test_hard_delete_template_already_archived(self, app, auth_client, seed_user, seed_periods_today):
        """C-5A.5-13: Pre-archived template with no history is permanently deleted."""
        with app.app_context():
            template = _create_template(seed_user, cadence=EVERY_PERIOD)

            from app.services import recurrence_engine
            scenario = seed_user["scenario"]
            periods_list = all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in periods_list},
            ), scenario.id)
            db.session.commit()

            # Pre-archive via route (soft-deletes projected txns).
            auth_client.post(f"/templates/{template.id}/archive")
            db.session.refresh(template)
            assert template.is_active is False

            template_id = template.id

            resp = auth_client.post(
                f"/templates/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data

            # Template and all transactions are gone.
            assert db.session.get(TransactionTemplate, template_id) is None
            remaining = db.session.query(Transaction).filter_by(
                template_id=template_id,
            ).count()
            assert remaining == 0

    def test_hard_delete_template_idor(self, app, auth_client, seed_user):
        """C-5A.5-14: Hard-deleting another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()
            other_id = other["template"].id

            resp = auth_client.post(
                f"/templates/{other_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 404

            # Other user's template still exists.
            assert db.session.get(TransactionTemplate, other_id) is not None

    def test_hard_delete_template_received_income_blocked(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C21-1: An income template with a RECEIVED paycheck is archived, not deleted.

        End-to-end proof of the CRIT-05 fix.  Pre-fix:
        ``template_has_paid_history`` enumerated ``[DONE, SETTLED]``
        by ID and silently omitted ``RECEIVED`` -- the status every
        income paycheck is given on mark-done
        (``transactions.py:534-535``: ``if txn.is_income: status_id =
        ref_cache.status_id(StatusEnum.RECEIVED)``).  The guard fell
        through and the route at ``templates.py:615-618`` then
        permanently destroyed the paycheck while flashing "permanently
        deleted."  Post-fix the predicate filters on
        ``Status.is_settled`` (RECEIVED carries
        ``is_settled=True``), the guard fires correctly, and the
        archive-fallback runs.  Assertions cover all three observable
        post-conditions: template archived (not deleted), RECEIVED
        paycheck still present and untouched, and the flash text
        accurately says "archived instead" rather than the misleading
        "permanently deleted."
        """
        with app.app_context():
            received_status = db.session.query(Status).filter_by(name="Received").one()

            # The definition's own row, settled the way Mark Paid settles an
            # income row -- RECEIVED, its record at the plan (plan step
            # balance:X-cf-4).
            template = make_income_template(
                db.session, seed_user, amount="2000.00",
                name="Biweekly Paycheck", category_key="Salary",
            )
            paycheck = generate_row_of(template, seed_periods_today[0])
            transaction_service.settle_transaction(paycheck)
            db.session.commit()

            template_id = template.id
            paycheck_id = paycheck.id

            resp = auth_client.post(
                f"/templates/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived instead" in resp.data
            # C21-8: the success "permanently deleted" flash must NOT
            # have fired.  The archive-fallback's own message
            # legitimately contains the substring "permanently deleted"
            # ("cannot be permanently deleted"), so check the literal
            # success-flash format instead.  Pre-fix the route would
            # have rendered exactly this success string while destroying
            # the paycheck.
            assert (
                b"Recurring transaction 'Biweekly Paycheck' permanently deleted"
                not in resp.data
            )

            # Template archived, not deleted.
            db.session.refresh(template)
            assert template.is_active is False
            assert db.session.get(TransactionTemplate, template_id) is not None

            # RECEIVED paycheck preserved with original amount and status.
            refreshed = db.session.get(Transaction, paycheck_id)
            assert refreshed is not None
            assert refreshed.status_id == received_status.id
            assert refreshed.is_deleted is False
            # Hand-verified: original actual_amount of $2000.00 is intact
            # (Decimal from string per coding standards).
            assert settled_figure(refreshed) == Decimal("2000.00")

    def test_hard_delete_template_bulk_delete_cannot_take_a_settled_row(
        self, app, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """C21-5: Even if the predicate is bypassed, a settled row is not destroyed.

        Defense in depth (CRIT-05 / E-22): the predicate fix above
        catches every settled status, but the destructive route is
        constrained additionally to ``Status.is_settled = False`` rows
        so a future regression of the predicate, a race window between
        the guard and the delete, or a different caller that bypasses
        the guard cannot physically destroy settled financial history.
        This test forces the bypass scenario by monkey-patching
        ``template_has_paid_history`` to return False even when a
        RECEIVED row exists, then asserts the post-condition.

        **The post-condition is the DATABASE's since the one-definition
        cutover** (plan step ``balance:X-bi-7d-2``, ruling **R-BAL20**).
        ``Transaction.template_id`` was ``ON DELETE SET NULL``, so the
        surviving RECEIVED row lost its link and stood as a zero-link row
        with its money intact; ``ck_transactions_one_pricing_link`` reads
        ``= 1`` and the key is ``RESTRICT`` now, so the definition cannot go
        while a row names it: the ORM nulls the survivor's link ahead of the
        parent delete, the CHECK refuses that statement, and the WHOLE act
        rolls back -- the settled row keeps its definition, the Projected
        row the bulk statement had deleted comes back, and the definition
        stands.  A raw ``IntegrityError`` rather than a designed refusal,
        because the guard this test bypasses is the designed one (the
        ``hard_delete_template`` docstring).
        """
        with app.app_context():
            # Mix: one RECEIVED + one Projected row of the same income
            # definition -- the engine's rows in two paychecks, the first
            # settled the way Mark Paid settles it (plan step balance:X-cf-4).
            received_status = db.session.query(Status).filter_by(name="Received").one()

            template = make_income_template(
                db.session, seed_user, amount="1500.00",
                name="Paycheck Bypass Scenario", category_key="Salary",
            )
            received_paycheck = generate_row_of(template, seed_periods_today[0])
            transaction_service.settle_transaction(received_paycheck)
            projected_paycheck = generate_row_of(template, seed_periods_today[1])
            db.session.commit()

            template_id = template.id
            received_id = received_paycheck.id
            projected_id = projected_paycheck.id

            # Force the bypass: predicate lies and says "no history."
            monkeypatch.setattr(
                "app.routes.templates.crud.archive_helpers.template_has_paid_history",
                lambda _template_id: False,
            )
            # ...and so does plan step credit_card:CC-5-4a-4's held-movements
            # check, which the RECEIVED row's own payment would otherwise
            # answer first (archiving before the database is reached).  Both
            # switched off, so the database's refusal is still what this
            # grades -- rule-5 re-expression, developer-confirmed 2026-09-23.
            monkeypatch.setattr(
                "app.routes.templates.crud.archive_helpers.template_holding_movements",
                lambda _template_id: HeldMovements(
                    payment=False, purchase=False, live_rows=0,
                ),
            )

            with pytest.raises(IntegrityError) as exc:
                auth_client.post(
                    f"/templates/{template_id}/hard-delete",
                    follow_redirects=True,
                )
            assert constraint_name_from(exc.value) == "ck_transactions_one_pricing_link"
            db.session.rollback()
            db.session.expire_all()

            # Settled row SURVIVES, its definition and its money intact.
            surviving = db.session.get(Transaction, received_id)
            assert surviving is not None
            assert surviving.template_id == template_id
            assert surviving.status_id == received_status.id
            assert surviving.is_deleted is False
            assert settled_figure(surviving) == Decimal("1500.00")

            # The act rolled back WHOLE: the Projected row and the
            # definition stand too.
            assert db.session.get(Transaction, projected_id) is not None
            assert db.session.get(TransactionTemplate, template_id) is not None

    def test_list_separates_active_and_archived(self, app, auth_client, seed_user):
        """C-5A.5-15: List page shows active and archived in separate sections."""
        with app.app_context():
            _create_template(
                seed_user, name="Active One", amount="100.00", cadence=EVERY_PERIOD,
            )
            _create_template(
                seed_user, name="Active Two", amount="200.00", cadence=EVERY_PERIOD,
            )
            archived = _create_template(
                seed_user, name="Archived One", amount="300.00", cadence=EVERY_PERIOD,
            )
            archived.is_active = False
            db.session.commit()

            resp = auth_client.get("/templates")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Active templates appear in the main table.
            assert "Active One" in html
            assert "Active Two" in html

            # Archived section exists with count indicator.
            assert "Archived (1)" in html
            assert "Archived One" in html

    def test_archive_label_in_flash(self, app, auth_client, seed_user, seed_periods_today):
        """C-5A.5-16: Archive flash message says 'archived' not 'deactivated'."""
        with app.app_context():
            template = _create_template(seed_user, cadence=EVERY_PERIOD)

            from app.services import recurrence_engine
            scenario = seed_user["scenario"]
            periods_list = all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in periods_list},
            ), scenario.id)
            db.session.commit()

            resp = auth_client.post(
                f"/templates/{template.id}/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived" in resp.data
            # Must NOT contain the old terminology.
            assert b"deactivated" not in resp.data
