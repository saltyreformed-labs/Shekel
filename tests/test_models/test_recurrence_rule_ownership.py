"""A recurrence rule is owned by its definition, and the schema says so.

Plan step **recurrence:R-F6**, finding **F-6**: *a hard-deleted template leaves
its recurrence rule behind forever.*

**These are the cases the whole step exists for, and none of them existed
before it.**  The suite was green while three orphaned rules sat on production,
because every test asserted what the application DID with a rule and none
asserted what happened to the rule when its definition went.  The four
properties below are the ones the schema now carries:

1. **The leak is closed at the door that opened it** -- hard-deleting a
   definition through its ROUTE disposes of its rule, for both kinds.  This is
   F-6 itself, driven end to end rather than at the model.
2. **The disposal is RECORDED.**  ``budget.recurrence_rules`` is in
   ``audit_infrastructure.AUDITED_TABLES`` and a cascaded DELETE fires
   row-level triggers, so a rule that goes leaves a row in
   ``system.audit_log`` carrying the whole thing it was.  In a budgeting app a
   silent destruction is the defect even when the destruction is correct.
3. **An orphan is INEXPRESSIBLE**, not merely absent: ``ck_recurrence_rules_one_owner``
   refuses a rule with no definition and a rule with two.
4. **1:1 is structural.**  ``uq_recurrence_rules_transaction_template_id`` and
   its transfer twin refuse a second rule on one definition, which is what the
   runtime census ``_rule_is_exclusively_owned`` used to ask on every clear.

Every one is a FIRING CONTROL: remove the constraint it names and the case
fails.  That is what makes them different from asserting a state that was
already true -- ``docs/plans/verification.md``'s standard.
"""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import BusinessDayShiftEnum, PeriodPlacementEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    RecurrenceResolutionError,
    RecurrenceSpec,
    author_rule,
)
from tests._test_helpers import (
    bare_expense_template,
    create_savings_account,
    make_every_period_rule,
)


def _audited_deletes(rule_id):
    """Return how many audit rows record *rule_id*'s deletion.

    Args:
        rule_id: The rule to look for.

    Returns:
        int: The number of ``DELETE`` rows in ``system.audit_log`` whose
        ``old_data`` carries that id.
    """
    return db.session.execute(text("""
        SELECT count(*) FROM system.audit_log
         WHERE table_schema = 'budget'
           AND table_name = 'recurrence_rules'
           AND operation = 'DELETE'
           AND (old_data->>'id')::int = :rule_id
    """), {"rule_id": rule_id}).scalar_one()


class TestHardDeletingADefinitionDisposesOfItsRule:
    """Finding **F-6**, driven through the two routes that leaked.

    Both routes call ``db.session.delete(template)`` and neither says anything
    about the rule -- deliberately, because the disposal is the DATABASE'S now
    (``ON DELETE CASCADE`` on each arm of the owning arc).  That is the whole
    difference between this and patching the routes: a third deletion door
    added later inherits the behaviour instead of having to remember it.
    """

    def test_a_hard_deleted_transaction_template_takes_its_rule(
        self, app, auth_client, seed_user,
    ):
        """The transaction-template route, which is where F-6 was measured.

        Args:
            app: The application fixture.
            auth_client: The signed-in client.
            seed_user: The owner fixture.
        """
        with app.app_context():
            template = bare_expense_template(db.session, seed_user)
            rule_id = make_every_period_rule(db.session, template).id
            template_id = template.id
            db.session.commit()

            resp = auth_client.post(
                f"/templates/{template_id}/hard-delete", follow_redirects=True,
            )

            assert resp.status_code == 200
            db.session.expire_all()
            assert db.session.get(TransactionTemplate, template_id) is None
            assert db.session.get(RecurrenceRule, rule_id) is None, (
                "the definition went and its rule stayed -- finding F-6, which "
                "ck_recurrence_rules_* and the CASCADE exist to make impossible"
            )

    def test_a_hard_deleted_transfer_template_takes_its_rule(
        self, app, auth_client, seed_user,
    ):
        """The transfer-template route, the same shape one table over.

        Asserted separately rather than parametrized: the two routes are
        different code with different history guards, and the arc has two arms
        precisely because a fix that reached one would not reach the other.

        Args:
            app: The application fixture.
            auth_client: The signed-in client.
            seed_user: The owner fixture.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Arc Savings", Decimal("0.00"),
            )
            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Sweep",
            )
            db.session.add(template)
            db.session.flush()
            rule_id = make_every_period_rule(db.session, template).id
            template_id = template.id
            db.session.commit()

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete", follow_redirects=True,
            )

            assert resp.status_code == 200
            db.session.expire_all()
            assert db.session.get(TransferTemplate, template_id) is None
            assert db.session.get(RecurrenceRule, rule_id) is None, (
                "the transfer arm leaks where the transaction arm does not"
            )

    def test_a_delete_that_BYPASSES_the_orm_still_takes_the_rule(
        self, app, db, seed_user,
    ):
        """The DATABASE's cascade, which the two route cases above never reach.

        **Measured while writing this file**: with
        ``fk_recurrence_rules_transaction_template_id`` weakened to
        ``ON DELETE RESTRICT``, both route cases still PASSED -- SQLAlchemy's
        ``delete-orphan`` on ``TransactionTemplate.recurrence_rule`` removes
        the rule first, so the referential cascade never has to act and a test
        driven through the ORM cannot see whether it exists.

        That matters because the ORM cascade is not what closes finding
        **F-6**: it holds for the code paths that go through this mapper, which
        is exactly the "every door must remember" property the step replaced.
        What holds for a bulk statement, a restore, a hand repair or a door
        written later is the FK, and this is the only case that exercises it --
        a raw DELETE with no mapper involved.

        Args:
            app: The application fixture.
            db: The session fixture.
            seed_user: The owner fixture.
        """
        with app.app_context():
            template = bare_expense_template(db.session, seed_user)
            rule_id = make_every_period_rule(db.session, template).id
            template_id = template.id
            db.session.commit()

            db.session.execute(
                text("DELETE FROM budget.transaction_templates WHERE id = :t"),
                {"t": template_id},
            )
            db.session.commit()

            db.session.expire_all()
            assert db.session.get(RecurrenceRule, rule_id) is None, (
                "a definition deleted around the ORM left its rule behind, "
                "which is finding F-6 for every writer that is not this mapper"
            )
            assert _audited_deletes(rule_id) == 1, (
                "and the database's own cascade must still be audited"
            )

    def test_the_cascaded_disposal_is_recorded_in_the_audit_log(
        self, app, auth_client, seed_user,
    ):
        """A rule destroyed by the DATABASE is still destroyed on the record.

        The property that makes the CASCADE acceptable in a budgeting app:
        Postgres executes a referential cascade as an internal DELETE that
        fires the child's row-level triggers, so
        ``system.audit_trigger_func`` still writes the whole row to
        ``old_data``.  Without this the step would have traded a leak for a
        silent destruction -- and it is the only reason the migration's
        downgrade can offer a by-hand recovery for the three rows it deletes.

        Args:
            app: The application fixture.
            auth_client: The signed-in client.
            seed_user: The owner fixture.
        """
        with app.app_context():
            template = bare_expense_template(db.session, seed_user)
            rule_id = make_every_period_rule(db.session, template).id
            template_id = template.id
            db.session.commit()
            assert _audited_deletes(rule_id) == 0, "nothing deleted it yet"

            auth_client.post(
                f"/templates/{template_id}/hard-delete", follow_redirects=True,
            )

            assert _audited_deletes(rule_id) == 1, (
                "the cascade destroyed a rule and system.audit_log does not "
                "record it, so the disposal is unauditable"
            )


class TestTheOwningArcRefusesWhatItIsFor:
    """``ck_recurrence_rules_one_owner`` and the two unique indexes, firing.

    Driven by raw construction rather than through the write door, and that is
    the point: the door cannot express any of these states, so a test that went
    through it would prove only that the door is closed.  What these grade is
    the STORAGE tier -- the writer with no mapper, the restore, the hand edit.
    """

    @staticmethod
    def _rule(**columns):
        """Return an unflushed rule carrying a storable cadence.

        Args:
            **columns: The owning-arc columns to state.

        Returns:
            The unsaved :class:`~app.models.recurrence_rule.RecurrenceRule`.
        """
        return RecurrenceRule(
            interval_n=1,
            unit_id=ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH),
            placement_id=ref_cache.period_placement_id(
                PeriodPlacementEnum.CONTAINING_DATE,
            ),
            shift_id=ref_cache.business_day_shift_id(
                BusinessDayShiftEnum.NONE,
            ),
            starts_on=date(2026, 1, 15),
            **columns,
        )

    def test_a_rule_owned_by_nothing_is_refused(self, app, db, seed_user):
        """The orphan itself: finding F-6's row, now unstorable.

        Args:
            app: The application fixture.
            db: The session fixture.
            seed_user: The owner fixture (unused; the row names no owner).
        """
        with app.app_context():
            db.session.add(self._rule())
            with pytest.raises(IntegrityError) as caught:
                db.session.flush()
            assert "ck_recurrence_rules_one_owner" in str(caught.value)
            db.session.rollback()

    def test_a_rule_owned_by_BOTH_kinds_is_refused(self, app, db, seed_user):
        """The other half of the arc: exactly one, never two.

        Args:
            app: The application fixture.
            db: The session fixture.
            seed_user: The owner fixture.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Both Arms", Decimal("0.00"),
            )
            transfer = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Two Owners",
            )
            db.session.add(transfer)
            expense = bare_expense_template(db.session, seed_user)
            db.session.flush()

            db.session.add(self._rule(
                transaction_template_id=expense.id,
                transfer_template_id=transfer.id,
            ))
            with pytest.raises(IntegrityError) as caught:
                db.session.flush()
            assert "ck_recurrence_rules_one_owner" in str(caught.value)
            db.session.rollback()

    def test_a_second_rule_on_one_definition_is_refused(
        self, app, db, seed_user,
    ):
        """1:1, which the deleted runtime census used to ask on every clear.

        ``_rule_is_exclusively_owned`` counted the templates referencing a rule
        before daring to delete it, because with the FK on the other side two
        definitions COULD name one row and deleting it would have stripped a
        second definition's cadence silently.  This is that question, answered
        by an index.

        Args:
            app: The application fixture.
            db: The session fixture.
            seed_user: The owner fixture.
        """
        with app.app_context():
            template = bare_expense_template(db.session, seed_user)
            make_every_period_rule(db.session, template)
            db.session.flush()

            db.session.add(self._rule(transaction_template_id=template.id))
            with pytest.raises(IntegrityError) as caught:
                db.session.flush()
            assert "uq_recurrence_rules_transaction_template_id" in str(
                caught.value,
            )
            db.session.rollback()

    def test_a_second_rule_on_one_TRANSFER_definition_is_refused(
        self, app, db, seed_user,
    ):
        """The transfer arm's own index, which the transaction arm's does not cover.

        Args:
            app: The application fixture.
            db: The session fixture.
            seed_user: The owner fixture.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Twin Rules", Decimal("0.00"),
            )
            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="One Cadence Only",
            )
            db.session.add(template)
            db.session.flush()
            make_every_period_rule(db.session, template)
            db.session.flush()

            db.session.add(self._rule(transfer_template_id=template.id))
            with pytest.raises(IntegrityError) as caught:
                db.session.flush()
            assert "uq_recurrence_rules_transfer_template_id" in str(
                caught.value,
            )
            db.session.rollback()


class TestTheOwnerIsTheOneStatementOfWhoOwnsARule:
    """``RecurrenceRule.user_id`` reads through, so the pair cannot disagree.

    The column was dropped because with an owner it is a stored copy of the
    definition's own, and nothing kept the two in step -- one runtime
    comparison in one route helper was the whole of what noticed.
    """

    def test_the_owner_answers_for_both_arms(self, app, db, seed_user):
        """Either arm reports the same owner the definition carries.

        Args:
            app: The application fixture.
            db: The session fixture.
            seed_user: The owner fixture.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Reads Through", Decimal("0.00"),
            )
            transfer = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Reads Through",
            )
            db.session.add(transfer)
            db.session.flush()
            expense = bare_expense_template(db.session, seed_user)

            for owner in (expense, transfer):
                rule = make_every_period_rule(db.session, owner)
                assert rule.user_id == owner.user_id
                assert rule.user_id == seed_user["user"].id

    def test_a_rule_with_no_owner_says_so_rather_than_guessing(self, app):
        """An owner-less rule names the case instead of raising on ``None``.

        Unreachable through either write door -- both take an owner -- and
        unreachable for a row, which ``ck_recurrence_rules_one_owner`` refuses
        without one.  What it catches is a rule CONSTRUCTED directly, which is
        a fixture bypassing the seam; the message says which.

        Args:
            app: The application fixture.
        """
        with app.app_context():
            with pytest.raises(ValueError, match="no owning definition"):
                _ = RecurrenceRule().user_id


class TestTheDoorRefusesAnOwnerTheSpecDoesNotName:
    """``author_rule`` will not write a rule onto another owner's definition.

    **This is the only test that makes the refusal fire**, and it exists
    because an adversarial review measured that nothing did: the refusal was
    graded only by an AST census over ``_authoring.py``'s source
    (``test_recurrence_authoring::test_every_spec_field_reaches_the_row``),
    which fails if the block is DELETED and passes if it is made unreachable.
    A guard graded by whether a name is mentioned is not graded.

    Unreachable from all five production call sites today -- each derives the
    calendar from the same owner it passes -- and kept for the reason
    ``_resolution._require_owner_match`` states about the pairing it checks:
    consistent today, enforced by nothing, and the failure it prevents is a
    plausible WRONG DATE rather than an error.  A rule resolved against one
    owner's paydays and written onto another's definition is priced on the
    wrong schedule with nothing on screen saying so.
    """

    def test_authoring_for_one_owner_onto_another_owners_definition_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """The spec names one user, the definition belongs to another.

        Args:
            app: The application fixture.
            db: The session fixture.
            seed_user: The owner the spec names.
            seed_second_user: The owner the definition belongs to.
        """
        with app.app_context():
            theirs = bare_expense_template(db.session, seed_second_user)
            spec = RecurrenceSpec(
                user_id=seed_user["user"].id,
                unit=RecurrenceUnitEnum.MONTH,
                starts_on=date(2026, 1, 15),
            )

            with pytest.raises(
                RecurrenceResolutionError, match="cannot be written onto",
            ):
                author_rule(spec, calendar_for(seed_user["user"].id), theirs)

            db.session.rollback()

    def test_the_matching_owner_is_admitted(self, app, db, seed_user):
        """The control: the refusal is about the PAIR, not about authoring.

        Without it the case above would pass against a door that refused every
        write, which is the shape a guard with no control decays into.

        Args:
            app: The application fixture.
            db: The session fixture.
            seed_user: The owner of both the spec and the definition.
        """
        with app.app_context():
            mine = bare_expense_template(db.session, seed_user)
            spec = RecurrenceSpec(
                user_id=seed_user["user"].id,
                unit=RecurrenceUnitEnum.MONTH,
                starts_on=date(2026, 1, 15),
            )

            rule = author_rule(
                spec, calendar_for(seed_user["user"].id), mine,
            )

            assert rule.transaction_template_id == mine.id
            assert rule.user_id == seed_user["user"].id


class TestTheStorageTierIsWhatHoldsTheArc:
    """The constraints exist under the names the model and migration state.

    A CHECK the model declares and the database does not carry is a rule that
    holds in exactly one place, and it is the ORM -- which every raw writer
    walks past.  Read from ``pg_constraint`` / ``pg_indexes`` rather than from
    the model, so the two cannot agree with each other and be wrong together.
    """

    def test_the_arc_check_and_every_cascade_is_on_the_table(self, app, db):
        """The CHECK is present and all three FKs cascade.

        Three arms since plan step salary:R15-b (ruling R-SAL32): the two
        template kinds and the payroll deduction.  Exactly one of three is the
        sum of the three set-flags equalling 1, which is what the two-arm
        ``<>`` XOR said for two.

        Args:
            app: The application fixture.
            db: The session fixture.
        """
        with app.app_context():
            rows = dict(db.session.execute(text("""
                SELECT conname, pg_get_constraintdef(oid)
                  FROM pg_constraint
                 WHERE conrelid = 'budget.recurrence_rules'::regclass
                   AND conname IN (
                       'ck_recurrence_rules_one_owner',
                       'fk_recurrence_rules_transaction_template_id',
                       'fk_recurrence_rules_transfer_template_id',
                       'fk_recurrence_rules_paycheck_line_id')
            """)).all())

            assert set(rows) == {
                "ck_recurrence_rules_one_owner",
                "fk_recurrence_rules_transaction_template_id",
                "fk_recurrence_rules_transfer_template_id",
                "fk_recurrence_rules_paycheck_line_id",
            }
            check = rows["ck_recurrence_rules_one_owner"]
            for arm in (
                "transaction_template_id", "transfer_template_id", "paycheck_line_id",
            ):
                assert f"({arm} IS NOT NULL)" in check, f"the arc does not count {arm}"
            assert "= 1" in check, (
                "the arc must be EXACTLY-one-of-three: both-set, all-set and "
                "none-set are equally refused"
            )
            for arm in (
                "transaction_template_id", "transfer_template_id", "paycheck_line_id",
            ):
                definition = rows[f"fk_recurrence_rules_{arm}"]
                assert "ON DELETE CASCADE" in definition, (
                    f"the {arm} arm does not cascade, so finding F-6 is open "
                    f"again on that arm"
                )

    def test_both_arms_carry_a_partial_unique_index(self, app, db):
        """1:1 per arm, indexed only where the arm is populated.

        Args:
            app: The application fixture.
            db: The session fixture.
        """
        with app.app_context():
            indexes = dict(db.session.execute(text("""
                SELECT indexname, indexdef FROM pg_indexes
                 WHERE schemaname = 'budget'
                   AND tablename = 'recurrence_rules'
                   AND indexname LIKE 'uq_recurrence_rules_%'
            """)).all())

            assert set(indexes) == {
                "uq_recurrence_rules_transaction_template_id",
                "uq_recurrence_rules_transfer_template_id",
                "uq_recurrence_rules_paycheck_line_id",
            }
            for name, definition in indexes.items():
                assert "UNIQUE" in definition, f"{name} does not constrain"
                assert "WHERE" in definition, (
                    f"{name} is not PARTIAL, so it indexes the half of the "
                    f"table carrying NULL in that arm for no reader"
                )

    def test_neither_template_still_carries_a_rule_column(self, app, db):
        """The old direction is GONE, not merely unused.

        A column left in place is one a later reader can start using, which is
        how the two-representations defects this arc keeps closing begin.

        Args:
            app: The application fixture.
            db: The session fixture.
        """
        with app.app_context():
            survivors = db.session.execute(text("""
                SELECT table_name FROM information_schema.columns
                 WHERE table_schema = 'budget'
                   AND column_name = 'recurrence_rule_id'
                   AND table_name IN (
                       'transaction_templates', 'transfer_templates')
            """)).scalars().all()

            assert survivors == [], (
                f"{survivors} still carry recurrence_rule_id, so a rule can "
                f"be named from both directions at once"
            )

    def test_the_rule_carries_no_user_id_column(self, app, db):
        """The copied owner is gone, so it cannot disagree with the real one.

        Args:
            app: The application fixture.
            db: The session fixture.
        """
        with app.app_context():
            found = db.session.execute(text("""
                SELECT count(*) FROM information_schema.columns
                 WHERE table_schema = 'budget'
                   AND table_name = 'recurrence_rules'
                   AND column_name = 'user_id'
            """)).scalar_one()

            assert found == 0, (
                "budget.recurrence_rules.user_id is back: a second statement "
                "of an owner the definition already carries"
            )
