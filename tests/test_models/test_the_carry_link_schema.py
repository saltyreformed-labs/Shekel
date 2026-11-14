"""A carry's link names a row of the same owner, definition and scenario, structurally.

Task **plan#3985** under plan step **balance:X-ax**, ruling **plan#3973**:
migration ``0ec92ce1634b`` gives ``budget.transactions`` a ``rolled_into_id``,
set on the SOURCE row, naming the row a carry-forward rolled its unspent budget
into.  Nothing in ``app/`` writes the column yet (plan#3989 does), so nothing
in the suite exercises these rules but this module.

**Every refusal here is a FIRING CONTROL** (``docs/plans/verification.md``
standard 4): it writes the state the schema refuses and asserts the refusal by
the constraint's NAME, read off the error's diagnostics
(:func:`~tests._test_helpers.constraint_name_from`), and each has a case that
the legal shape beside it is admitted, since a key that refuses every link
would pass the refusals just as well.  The link is the only axis a case varies:
the rows are the engine's (:func:`~tests._test_helpers.generate_row_of`), the
source is settled through the app's own settle path
(:func:`~tests._test_helpers.settle_cash_row`), and a row is built by hand only
where it is a state the app cannot write, which is what the key exists to
refuse.

What each refusal would cost if it were writable:

* **into another definition's, another scenario's or another owner's row**:
  the carried money would show up in a different envelope, in a what-if
  scenario's projection, or in someone else's budget;
* **on a row with no definition**: ``MATCH SIMPLE`` skips the composite key
  whenever ``template_id`` is NULL, so the link would be checked by nothing;
* **with no settle day**: a carry is the settle's own act, and a link left on a
  reverted row would keep carrying money its row no longer settled;
* **into itself**: a row would count its own unspent budget twice;
* **deleting a row a carry names**: the money would vanish from under its
  source; ``RESTRICT``, never ``CASCADE`` or ``SET NULL``.
"""
from __future__ import annotations

import pytest
import sqlalchemy.exc
from sqlalchemy import text

from app import ref_cache
from app.enums import StatusEnum
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.services import credit_workflow
from app.services.settle_day import record_settle_day
from tests._test_helpers import (
    an_entered_day,
    constraint_name_from,
    generate_row_of,
    load_migration_module,
    make_expense_template,
    run_migration_callable as _run,
    settle_cash_row,
)

#: This task's own revision, loaded so its SHIPPED callables are what runs.
_MIGRATION = load_migration_module(
    "0ec92ce1634b_a_carry_names_the_row_it_rolled_into.py",
)

_KEY = "fk_transactions_rolled_into"
_CONSTRAINTS = (
    "uq_transactions_id_user_template_scenario",
    _KEY,
    "ck_transactions_carry_names_a_definition",
    "ck_transactions_carry_needs_settle_day",
    "ck_transactions_carry_not_into_itself",
)
_INDEX = "idx_transactions_rolled_into"


def _installed(session) -> dict:
    """Return the subset of this revision's objects the database holds."""
    return {
        "column": session.execute(text(
            "SELECT count(*) FROM information_schema.columns "
            " WHERE table_schema = 'budget' AND table_name = 'transactions' "
            "   AND column_name = 'rolled_into_id'"
        )).scalar(),
        "constraints": session.execute(text(
            "SELECT count(*) FROM pg_constraint "
            " WHERE conrelid = 'budget.transactions'::regclass "
            "   AND conname = ANY(:names)"
        ), {"names": list(_CONSTRAINTS)}).scalar(),
        "index": session.execute(text(
            "SELECT indexdef FROM pg_indexes "
            " WHERE schemaname = 'budget' AND indexname = :name"
        ), {"name": _INDEX}).scalar(),
    }


_UPGRADED = {
    "column": 1,
    "constraints": len(_CONSTRAINTS),
    "index": (
        "CREATE INDEX idx_transactions_rolled_into ON budget.transactions "
        "USING btree (rolled_into_id) WHERE (rolled_into_id IS NOT NULL)"
    ),
}
_DOWNGRADED = {"column": 0, "constraints": 0, "index": None}


def _envelope(db_session, seed, name="Groceries"):
    """Return a `$500.00` every-paycheck envelope definition of *seed*'s."""
    return make_expense_template(
        db_session, seed, "500.00",
        name=name, category_key="Groceries", is_envelope=True,
    )


def _settled_source(db_session, seed, periods, template=None):
    """Return *template*'s row in the first period, settled as Paid.

    The settle is the app's own (the status seam, the covering movement, the
    postings), so the row is a source the carry-forward could have written.
    """
    template = template or _envelope(db_session, seed)
    source = generate_row_of(template, periods[0])
    settle_cash_row(source)
    db_session.flush()
    return source


def _bare_copy(row: Transaction, **overrides) -> Transaction:
    """Return an unflushed row that differs from *row* only in *overrides*.

    For the targets the app cannot write (a row of another owner's naming this
    definition, a what-if row the engine did not generate), so a refusal is
    the key's and never another rule's: every other column is the engine's
    row's.  Undated as an occurrence (``occurs_on`` NULL) unless overridden,
    so it never collides with *row* on the occurrence index.
    """
    fields = {
        "user_id": row.user_id,
        "template_id": row.template_id,
        "pay_period_id": row.pay_period_id,
        "scenario_id": row.scenario_id,
        "account_id": row.account_id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": row.name,
        "category_id": row.category_id,
        "transaction_type_id": row.transaction_type_id,
        "amount_ownership": row.amount_ownership,
        "due_date": row.due_date,
        "occurs_on": None,
    }
    fields.update(overrides)
    return Transaction(**fields)


def _refused_by(db_session, constraint: str) -> None:
    """Flush, expect an IntegrityError by *constraint*'s name, and roll back."""
    with pytest.raises(sqlalchemy.exc.IntegrityError) as excinfo:
        db_session.flush()
    assert constraint_name_from(excinfo.value) == constraint, excinfo.value
    db_session.rollback()


class TestTheLegalShapeIsAdmitted:
    """The controls: a carry the carry-forward will write is storable."""

    def test_a_settled_envelope_rolls_into_the_next_paychecks_row(
        self, db, seed_user, seed_periods,
    ):
        """Same owner, definition and scenario, a settled source: admitted.

        Read back through a fresh load, so the link is what the database
        stored and the relationship resolves it, not the session's memory.
        """
        template = _envelope(db.session, seed_user)
        source = _settled_source(db.session, seed_user, seed_periods, template)
        target = generate_row_of(template, seed_periods[1])
        source.rolled_into = target
        db.session.commit()
        source_id, target_id = source.id, target.id
        db.session.expire_all()

        reloaded = db.session.get(Transaction, source_id)
        assert reloaded.rolled_into_id == target_id
        assert reloaded.rolled_into.id == target_id

    def test_two_sources_may_roll_into_one_row(
        self, db, seed_user, seed_periods,
    ):
        """A row may receive several carries: the index is not unique.

        Two paychecks' envelopes carried into the third's, the shape a
        carry-forward run over two unpaid periods writes.
        """
        template = _envelope(db.session, seed_user)
        first = generate_row_of(template, seed_periods[0])
        second = generate_row_of(template, seed_periods[1])
        target = generate_row_of(template, seed_periods[2])
        settle_cash_row(first)
        settle_cash_row(second)
        first.rolled_into = target
        second.rolled_into = target
        db.session.commit()

        assert first.rolled_into_id == target.id
        assert second.rolled_into_id == target.id

    def test_a_target_written_in_the_same_flush_is_inserted_first(
        self, db, seed_user, seed_periods,
    ):
        """A new target and its source's link flush together.

        The carry-forward's CREATE arm writes the next paycheck's row and the
        link in one act (plan#3989), and the target has no id until it is
        inserted.  The mapped relationship is what orders the target's INSERT
        before the source's UPDATE.
        """
        template = _envelope(db.session, seed_user)
        source = _settled_source(db.session, seed_user, seed_periods, template)
        target = _bare_copy(
            source,
            pay_period_id=seed_periods[1].id,
            due_date=seed_periods[1].start_date,
        )
        source.rolled_into = target
        db.session.add(target)
        db.session.flush()

        assert target.id is not None
        assert source.rolled_into_id == target.id


class TestTheKeyHoldsTheTargetToTheSource:
    """``fk_transactions_rolled_into``: same owner, definition and scenario."""

    def test_another_definitions_row_is_refused(
        self, db, seed_user, seed_periods,
    ):
        """Groceries may not carry into the next paycheck's Gas envelope."""
        source = _settled_source(db.session, seed_user, seed_periods)
        other = generate_row_of(
            _envelope(db.session, seed_user, name="Gas"), seed_periods[1],
        )
        source.rolled_into_id = other.id
        _refused_by(db.session, _KEY)

    def test_another_scenarios_row_is_refused(
        self, db, seed_user, seed_periods,
    ):
        """A baseline envelope may not carry into a what-if scenario's row."""
        source = _settled_source(db.session, seed_user, seed_periods)
        what_if = Scenario(
            user_id=seed_user["user"].id, name="What if", is_baseline=False,
        )
        db.session.add(what_if)
        db.session.flush()
        other = _bare_copy(
            source, scenario_id=what_if.id,
            pay_period_id=seed_periods[1].id,
            due_date=seed_periods[1].start_date,
        )
        db.session.add(other)
        db.session.flush()
        source.rolled_into_id = other.id
        _refused_by(db.session, _KEY)

    def test_another_owners_row_of_the_same_definition_is_refused(
        self, db, seed_user, seed_periods, seed_second_user, seed_second_periods,
    ):
        """The owner is named in the key because the definition does not imply it.

        A row's ``template_id`` is a plain key, so another owner's row may name
        this owner's definition and baseline scenario: the schema holds it
        (built here by hand, since no door writes it).  A key over definition
        and scenario alone would admit the link; ``user_id`` is what refuses it.
        """
        source = _settled_source(db.session, seed_user, seed_periods)
        foreign = _bare_copy(
            source,
            user_id=seed_second_user["user"].id,
            account_id=seed_second_user["account"].id,
            pay_period_id=seed_second_periods[1].id,
            due_date=seed_second_periods[1].start_date,
        )
        db.session.add(foreign)
        db.session.flush()
        source.rolled_into_id = foreign.id
        _refused_by(db.session, _KEY)

    def test_deleting_a_row_a_carry_names_is_refused(
        self, db, seed_user, seed_periods,
    ):
        """``RESTRICT``: the target stays and so does the link.

        Through the ORM, the way a door deletes: the mapping carries no
        one-to-many that would null the source's link first, so the delete
        meets the key.  ``SET NULL`` would let the delete through and drop the
        carry; ``CASCADE`` would delete the source with it.
        """
        template = _envelope(db.session, seed_user)
        source = _settled_source(db.session, seed_user, seed_periods, template)
        target = generate_row_of(template, seed_periods[1])
        source.rolled_into = target
        db.session.commit()
        source_id, target_id = source.id, target.id

        db.session.delete(target)
        _refused_by(db.session, _KEY)
        assert db.session.get(Transaction, target_id) is not None
        assert db.session.get(Transaction, source_id).rolled_into_id == target_id

    def test_one_statement_deleting_a_source_with_its_target_is_admitted(
        self, db, seed_user, seed_periods,
    ):
        """``RESTRICT`` checks at the statement's end, not at each row.

        A door that deletes a definition's rows in one statement
        (``definition_delete``'s shape) takes a source and its target together,
        and nothing is left naming anything.  Measured rather than assumed:
        a key that checked each row as it went would refuse whichever of the
        two the statement reached first.  The source's settle day is written
        by the app's own pair writer rather than a settle, because a settle's
        covering movement would refuse the delete by its own key first, and
        that movement is not this test's subject.
        """
        template = _envelope(db.session, seed_user)
        source = generate_row_of(template, seed_periods[0])
        target = generate_row_of(template, seed_periods[1])
        record_settle_day(source, an_entered_day(seed_periods[0].start_date))
        source.rolled_into = target
        db.session.commit()

        deleted = db.session.execute(text(
            "DELETE FROM budget.transactions WHERE template_id = :template"
        ), {"template": template.id}).rowcount
        db.session.commit()

        assert deleted == 2


class TestTheChecks:
    """The three CHECKs, each the one a case breaks."""

    def test_a_link_on_a_row_with_no_definition_is_refused(
        self, db, seed_user, seed_periods,
    ):
        """The hole ``MATCH SIMPLE`` leaves is closed.

        A CC payback names no definition, so the composite key never looks at
        its link: without this CHECK it could name a row that does not exist.
        The payback is the app's own (Mark Credit), given a settle day so the
        settle-day CHECK is not the one that fires.
        """
        rent = make_expense_template(db.session, seed_user)
        charged = generate_row_of(rent, seed_periods[0])
        db.session.commit()
        payback = credit_workflow.mark_as_credit(
            charged.id, seed_user["user"].id,
        )
        assert payback.template_id is None
        record_settle_day(payback, an_entered_day(seed_periods[1].start_date))
        db.session.flush()
        no_such_row = db.session.execute(text(
            "SELECT max(id) + 1 FROM budget.transactions"
        )).scalar()
        payback.rolled_into_id = no_such_row
        _refused_by(db.session, "ck_transactions_carry_names_a_definition")

    def test_a_link_on_a_row_with_no_settle_day_is_refused(
        self, db, seed_user, seed_periods,
    ):
        """A Projected envelope carries nothing: the link needs the settle day."""
        template = _envelope(db.session, seed_user)
        source = generate_row_of(template, seed_periods[0])
        target = generate_row_of(template, seed_periods[1])
        assert source.settled_on is None
        source.rolled_into = target
        _refused_by(db.session, "ck_transactions_carry_needs_settle_day")

    def test_a_row_carried_into_itself_is_refused(
        self, db, seed_user, seed_periods,
    ):
        """The composite key admits a self-link; this CHECK is what refuses it."""
        source = _settled_source(db.session, seed_user, seed_periods)
        source.rolled_into_id = source.id
        _refused_by(db.session, "ck_transactions_carry_not_into_itself")


class TestTheRevisionRoundTrips:
    """``downgrade`` then ``upgrade``, and the downgrade's one refusal."""

    def test_downgrade_removes_exactly_what_upgrade_added(
        self, db, seed_user, seed_periods,
    ):
        """The column, the five constraints and the index go, and come back.

        Over a database holding a settled envelope and the next paycheck's
        row, unlinked: the rows survive both directions.
        """
        template = _envelope(db.session, seed_user)
        _settled_source(db.session, seed_user, seed_periods, template)
        generate_row_of(template, seed_periods[1])
        db.session.commit()
        before = db.session.execute(
            text("SELECT count(*) FROM budget.transactions"),
        ).scalar()

        assert _installed(db.session) == _UPGRADED
        _run(_MIGRATION.downgrade, db.session)
        assert _installed(db.session) == _DOWNGRADED
        _run(_MIGRATION.upgrade, db.session)
        assert _installed(db.session) == _UPGRADED
        assert db.session.execute(
            text("SELECT count(*) FROM budget.transactions"),
        ).scalar() == before

    def test_a_recorded_carry_refuses_the_downgrade_by_count(
        self, db, seed_user, seed_periods,
    ):
        """A link is reported, never dropped: it is the carry's only record.

        The count in the message is what makes this a test of the explicit
        refusal, and the schema reading UPGRADED after the rollback is what
        says nothing was dropped.
        """
        template = _envelope(db.session, seed_user)
        source = _settled_source(db.session, seed_user, seed_periods, template)
        source.rolled_into = generate_row_of(template, seed_periods[1])
        db.session.commit()

        with pytest.raises(RuntimeError) as excinfo:
            _run(_MIGRATION.downgrade, db.session)
        assert str(excinfo.value).startswith(
            "1 budget.transactions row(s) record the row their carry-forward "
            "rolled into (rolled_into_id)."
        )
        db.session.rollback()
        assert _installed(db.session) == _UPGRADED

    def test_the_same_downgrade_succeeds_once_the_link_is_cleared(
        self, db, seed_user, seed_periods,
    ):
        """THE CONTROL for the refusal above: same rows, the link cleared.

        Without it the refusal would pass just as well against a downgrade that
        could never run at all.
        """
        template = _envelope(db.session, seed_user)
        source = _settled_source(db.session, seed_user, seed_periods, template)
        source.rolled_into = generate_row_of(template, seed_periods[1])
        db.session.commit()
        source.rolled_into = None
        db.session.commit()

        _run(_MIGRATION.downgrade, db.session)
        assert _installed(db.session) == _DOWNGRADED
        _run(_MIGRATION.upgrade, db.session)
        assert _installed(db.session) == _UPGRADED
