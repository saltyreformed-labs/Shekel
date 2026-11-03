"""
Shekel Budget App -- a closing date never precedes the first occurrence (recurrence:R7d-g)

One fact on ``budget.recurrence_rules`` (finding **D35**, ruling **R-R80**,
migration ``bf50951a3599``)::

    ck_recurrence_rules_valid_window
        end_date IS NULL OR end_date >= starts_on

Drafted for plan step R7c-b and HELD BACK on a developer ruling (2026-08-15)
because the two columns then held two KINDS of fact -- what an owner authors,
and the derived payoff ten chokepoints wrote for a loan payment, where a loan
cleared before its first installment inverts the pair honestly.  R7d-g deleted
the writers, so every stored pair is an owner's word and both authoring doors
already refuse an inverted one; the constraint makes the state unrepresentable
rather than merely unproduced.

**Three classes.**  The CHECK itself, exercised in every direction a writer
could reach it from -- an INSERT with the pair inverted, an UPDATE moving the
stop below the start, an UPDATE moving the start past the stop -- and the two
accepted shapes on the boundary (equal dates; a count bound, which the CHECK
does not name).  Every row is built BARE, for the reason
``test_template_row_needs_due_date`` gives: the write door and the two form
refusals exist to make the refused states unreachable, so a control routed
through one would grade the door and never the constraint.

Then the MIGRATION, driven over this test's private clone: its ``upgrade``
NULLs exactly the rows ruling **R-R80** names -- the standing payment of a
configured loan and an ARCHIVED transfer into one -- and leaves a second
active transfer's owner-authored stop and a count bound alone; its
``downgrade`` drops the CHECK and restores nothing.  The refusal the upgrade
makes on a row step 1 does not clear is FIRED here rather than left as the
one path a green suite never runs: a second transfer's inverted pair (planted
with the CHECK down) fails the ``ALTER TABLE`` whole and the rollback takes
step 1's NULLs back with it.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
import sqlalchemy
from alembic import op
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.transfer_template import TransferTemplate
from app.services.balance_at import BalanceContext, is_standing_loan_payment
from tests._test_helpers import (
    create_loan_account,
    create_savings_account,
    load_migration_module,
    make_cadence_rule,
    make_loan_payment_template,
    make_transfer_template,
    state_template_price,
)
from tests.oracles.recurrence_baseline import MONTHLY

_CONSTRAINT = "ck_recurrence_rules_valid_window"
_MIGRATION = load_migration_module(
    "bf50951a3599_the_closing_bound_is_the_owners_word.py",
)


def _constraint_is_bound():
    """Return whether the CHECK exists on ``budget.recurrence_rules`` right now."""
    return bool(db.session.execute(sqlalchemy.text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM pg_constraint c"
        "  JOIN pg_class t ON t.oid = c.conrelid"
        "  JOIN pg_namespace n ON n.oid = t.relnamespace"
        "  WHERE n.nspname = 'budget' AND t.relname = 'recurrence_rules'"
        "    AND c.contype = 'c' AND c.conname = :name"
        ")"
    ), {"name": _CONSTRAINT}).scalar())


def _run(step):
    """Drive one of the migration's steps over this test's own connection.

    The idiom ``test_template_row_needs_due_date`` established: Alembic's
    ``op`` proxy needs an operations context, and ``op.get_bind`` is patched so
    a step that asks for the bind gets the session's connection rather than a
    second one.  The caller commits or rolls back.
    """
    connection = db.session.connection()
    ctx = MigrationContext.configure(connection=connection)
    with Operations.context(ctx):
        with patch.object(op, "get_bind", return_value=connection):
            step()


def _flush_refused():
    """Flush, asserting the database refuses it naming the window CHECK."""
    with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
        db.session.flush()
    assert _CONSTRAINT in str(exc.value)
    db.session.rollback()


def _monthly_rule(template, starts_on, **columns):
    """Return an UNFLUSHED monthly rule on *template* starting *starts_on*.

    Through the fixture's own write door for the cadence, then the closing
    bound columns are set BARE (the door refuses an inverted pair, which is
    the thing under test).  The caller flushes or commits.
    """
    rule = make_cadence_rule(template, MONTHLY, starts_on=starts_on)
    for column, value in columns.items():
        setattr(rule, column, value)
    return rule


def _set_columns(rule_id, **columns):
    """Set closing-bound columns on a rule by id with one UPDATE, flushed."""
    rule = db.session.get(RecurrenceRule, rule_id)
    for column, value in columns.items():
        setattr(rule, column, value)
    db.session.flush()


class TestTheCheckRefusesAnInvertedPair:
    """The constraint, in every direction a writer could reach it from."""

    def test_an_insert_with_the_stop_below_the_start_is_refused(
        self, app, db, seed_user,
    ):
        """INSERT direction."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Window savings", Decimal("0.00"),
            )
            template = make_transfer_template(db.session, seed_user, savings)
            db.session.commit()
            template.recurrence_rule = None
            db.session.flush()

            _monthly_rule(
                template, date(2026, 5, 15), end_date=date(2026, 5, 14),
            )
            _flush_refused()

    def test_an_update_moving_the_stop_below_the_start_is_refused(
        self, app, db, seed_user,
    ):
        """UPDATE direction, the stop side: the form's inverted-window shape."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Window savings", Decimal("0.00"),
            )
            template = make_transfer_template(db.session, seed_user, savings)
            db.session.commit()
            template.recurrence_rule = None
            db.session.flush()
            rule = _monthly_rule(
                template, date(2026, 5, 15), end_date=date(2026, 12, 15),
            )
            db.session.commit()

            rule.end_date = date(2026, 5, 14)
            _flush_refused()

    def test_an_update_moving_the_start_past_the_stop_is_refused(
        self, app, db, seed_user,
    ):
        """UPDATE direction, the start side: the loan-params edit's shape.

        The one pair a loan edit could reach, which
        ``loan_recurrence_sync._sync_loan_cadence`` refuses before it writes;
        this is the backstop that refusal stands in front of.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Window savings", Decimal("0.00"),
            )
            template = make_transfer_template(db.session, seed_user, savings)
            db.session.commit()
            template.recurrence_rule = None
            db.session.flush()
            rule = _monthly_rule(
                template, date(2026, 5, 15), end_date=date(2026, 6, 15),
            )
            db.session.commit()

            rule.starts_on = date(2026, 6, 16)
            _flush_refused()

    def test_a_stop_ON_the_start_is_admitted(self, app, db, seed_user):
        """The boundary: ``end_date = starts_on`` names exactly one occurrence."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Window savings", Decimal("0.00"),
            )
            template = make_transfer_template(db.session, seed_user, savings)
            db.session.commit()
            template.recurrence_rule = None
            db.session.flush()
            rule = _monthly_rule(
                template, date(2026, 5, 15), end_date=date(2026, 5, 15),
            )
            db.session.commit()
            assert rule.id is not None

    def test_a_count_bound_is_not_graded(self, app, db, seed_user):
        """A COUNT cannot invert against a date, and the CHECK does not name it."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Window savings", Decimal("0.00"),
            )
            template = make_transfer_template(db.session, seed_user, savings)
            db.session.commit()
            template.recurrence_rule = None
            db.session.flush()
            rule = _monthly_rule(template, date(2026, 5, 15), max_occurrences=1)
            db.session.commit()
            assert rule.id is not None


def _loan_with_three_definitions(seed_user):
    """Return ``(standing, second, archived)``: three recurring transfers into one loan.

    The R-R80 predicate's three classes on one loan, each holding a stored
    stop the CHECK admits: the STANDING payment (oldest active; the sync
    wrote its column), a SECOND active transfer (its owner's word), and an
    ARCHIVED one (a former payment whose column the sync wrote while it was
    active).  Built bare enough that the names are unique per owner.
    """
    loan = create_loan_account(
        seed_user, db.session, name="Predicate Loan",
        principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
        origination_date=date(2026, 1, 1),
    )
    standing = make_loan_payment_template(db.session, seed_user, loan)
    standing.name = "Standing payment"
    db.session.flush()
    second = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=loan.id,
        name="Second transfer",
    )
    archived = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=loan.id,
        name="Archived payment",
        is_active=False,
    )
    db.session.add_all([second, archived])
    db.session.flush()
    state_template_price(second, Decimal("50.00"))
    state_template_price(archived, Decimal("500.00"))
    make_cadence_rule(second, MONTHLY, fires_on_day=1)
    make_cadence_rule(archived, MONTHLY, fires_on_day=1)
    db.session.commit()
    return standing, second, archived


class TestTheMigrationRoundTrips:
    """``bf50951a3599``'s two steps, driven over this test's own clone."""

    def test_the_upgrade_nulls_the_ruled_set_and_binds_and_the_downgrade_drops_only_the_check(
        self, app, db, seed_user, seed_periods,
    ):
        """Down, plant one row per R-R80 class, up, and grade what each holds.

        The standing payment's and the archived transfer's stops go; the
        second transfer's owner-authored stop and a count bound stay.  The
        predicate is graded on the ROWS rather than on the rowcount alone:
        a mutation widening it to every transfer into a loan erases the
        second's stop, one narrowing it to active rows leaves the archived
        cache, and each reads red here.
        """
        with app.app_context():
            assert _constraint_is_bound()
            _run(_MIGRATION.downgrade)
            db.session.commit()
            assert not _constraint_is_bound()

            standing, second, archived = _loan_with_three_definitions(seed_user)
            assert is_standing_loan_payment(
                standing, BalanceContext.build(seed_user["user"].id),
            ), "precondition: the standing payment is the one the sync wrote for"
            _set_columns(standing.recurrence_rule.id, end_date=date(2028, 1, 1))
            _set_columns(second.recurrence_rule.id, end_date=date(2027, 6, 1))
            _set_columns(archived.recurrence_rule.id, end_date=date(2027, 9, 1))
            db.session.commit()
            # A second loan whose standing payment carries a COUNT: not a
            # cache (the writers never wrote one), so it stays.
            counted_loan = create_loan_account(
                seed_user, db.session, name="Counted Loan",
                principal=Decimal("6000.00"), rate=Decimal("0.05000"), term=12,
                origination_date=date(2026, 1, 1),
            )
            counted = make_loan_payment_template(
                db.session, seed_user, counted_loan,
            )
            counted.name = "Counted payment"
            db.session.flush()
            _set_columns(counted.recurrence_rule.id, max_occurrences=6)
            db.session.commit()

            _run(_MIGRATION.upgrade)
            db.session.commit()
            db.session.expire_all()

            assert _constraint_is_bound()
            assert standing.recurrence_rule.end_date is None, (
                "the standing payment's cache survived the migration"
            )
            assert archived.recurrence_rule.end_date is None, (
                "the archived transfer's cache survived the migration"
            )
            assert second.recurrence_rule.end_date == date(2027, 6, 1), (
                "a second transfer's owner-authored stop was erased"
            )
            assert counted.recurrence_rule.max_occurrences == 6
            assert counted.recurrence_rule.end_date is None

            _run(_MIGRATION.downgrade)
            db.session.commit()
            db.session.expire_all()
            assert not _constraint_is_bound()
            assert standing.recurrence_rule.end_date is None, (
                "the downgrade restored a cache it cannot know"
            )
            assert second.recurrence_rule.end_date == date(2027, 6, 1)

            _run(_MIGRATION.upgrade)
            db.session.commit()
            assert _constraint_is_bound()

    def test_the_upgrade_refuses_a_pair_its_first_step_does_not_clear(
        self, app, db, seed_user, seed_periods,
    ):
        """A second transfer's inverted pair fails the ALTER TABLE whole.

        Planted with the CHECK down, on the one class step 1 leaves alone.
        The upgrade's UPDATE runs first and NULLs the standing payment's
        stop; the ALTER TABLE then refuses, and the rollback must take that
        NULL back with it -- a half-applied migration would be the standing
        payment's cache gone with no CHECK to show for it.
        """
        with app.app_context():
            _run(_MIGRATION.downgrade)
            db.session.commit()
            assert not _constraint_is_bound()

            standing, second, _archived = _loan_with_three_definitions(seed_user)
            _set_columns(standing.recurrence_rule.id, end_date=date(2028, 1, 1))
            _set_columns(
                second.recurrence_rule.id,
                end_date=second.recurrence_rule.starts_on - date.resolution,
            )
            db.session.commit()

            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                _run(_MIGRATION.upgrade)
            assert _CONSTRAINT in str(exc.value)
            db.session.rollback()
            db.session.expire_all()
            assert not _constraint_is_bound(), (
                "a refused upgrade must leave the constraint unbound"
            )
            assert standing.recurrence_rule.end_date == date(2028, 1, 1), (
                "a refused upgrade must take its first step's NULLs back"
            )

            # Repair the planted pair the way the owner would, and the
            # upgrade binds.
            _set_columns(second.recurrence_rule.id, end_date=None)
            db.session.commit()
            _run(_MIGRATION.upgrade)
            db.session.commit()
            assert _constraint_is_bound()
