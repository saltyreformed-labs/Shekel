"""Entrypoint step 3 is ONE transaction, committed once (plan step balance:X-cv).

``scripts/init_database.py`` used to commit the migrations in
``migrations/env.py``'s own transaction BEFORE the deploy's three ledger hooks
ran, so a hook that refused left a stamp the previous image could not resolve
and ``deploy/shekel-deploy.sh`` refused to re-pin it -- a manual dump restore
(ruling **R-BAL105**).  Now ``initialise_database`` opens a connection and a
transaction of its own, runs the migrations on it and everything else through a
session JOINED to it, and its commit is the only one (rulings **R-BAL113**,
**R-BAL118**).  These tests grade that from OUTSIDE the transaction: every
observation is read over a SEPARATE connection, which under READ COMMITTED sees
only what was committed, so a flush can never pass for a commit here.

**A real pending migration, not a stand-in.**  The per-test database is
already at head, so a no-op upgrade would move no stamp and prove nothing.
The ``pending_migration`` fixture builds a version directory holding every
real migration (symlinked) plus one probe revision on top of the real head
that creates ``budget.xcv_probe``, and points the one migration runner's
Alembic config at it (:mod:`app.migration_runner`, ruling **R-BAL114**) -- so
the stamp and a DDL change are really pending when the deploy runs.

**A real session write.**  The deploy's session steps are the reference seed
and ``ref_cache``'s load since plan task plan#3077 deleted the three ledger
hooks, whose loan correction these tests watched until then.  The
``unseeded_ref_row`` fixture deletes, committed, one ref row the per-test
database does not reference, so the real seed must INSERT it again: "every
write unmoved" is asserted of a write the seed made, not of one the test
forged.
"""
from __future__ import annotations

import logging
import pathlib
import re
import textwrap

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import event, text
from sqlalchemy.exc import InvalidRequestError

from app import migration_runner, ref_cache
from app.audit_infrastructure import (
    EXPECTED_TRIGGER_COUNT,
    require_audit_triggers,
)
from app.enums import StatementSourceEnum
from app.models.category import Category
from app.ref_seeds import ACCT_TYPE_SEEDS
from scripts.build_test_db_image import template_checks
from tests._test_helpers import (
    load_init_database_module,
    refused_by_database_rule,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_VERSIONS_DIR = _REPO_ROOT / "migrations" / "versions"
_INIT_DB = load_init_database_module()

_PROBE_REVISION = "xcvprobe0001"
_PROBE_MIGRATION = textwrap.dedent('''\
    """X-cv probe: a pending migration the one-transaction tests apply."""
    from alembic import op

    revision = "{revision}"
    down_revision = "{down_revision}"
    branch_labels = None
    depends_on = None


    def upgrade():
        """Create the probe table, then run the statements a test adds."""
        op.execute("CREATE TABLE budget.xcv_probe (id integer)")
        for statement in {statements!r}:
            op.execute(statement)


    def downgrade():
        """Drop the probe table and the refusing rule's function."""
        op.execute("DROP TABLE budget.xcv_probe")
        op.execute("DROP FUNCTION IF EXISTS budget.xcv_probe_refuse()")
''')

#: A rule the probe installs and then breaks: a DEFERRED constraint trigger on
#: the probe table that refuses every row, so the refusal is raised BY the
#: deploy's one commit, after every step has run, rather than by any statement
#: before it -- the shape of a migration's data write that a deferred rule of
#: the schema refuses (the books-boundary, side-band and deleted-row rules are
#: all deferred).
_REFUSED_AT_COMMIT = (
    "CREATE FUNCTION budget.xcv_probe_refuse() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ BEGIN "
    "RAISE EXCEPTION 'xcv probe: refused at commit'; END $$",
    "CREATE CONSTRAINT TRIGGER xcv_probe_refuse AFTER INSERT "
    "ON budget.xcv_probe DEFERRABLE INITIALLY DEFERRED "
    "FOR EACH ROW EXECUTE FUNCTION budget.xcv_probe_refuse()",
    "INSERT INTO budget.xcv_probe VALUES (1)",
)


def _real_head() -> str:
    """Return the head revision of the repository's own migration chain."""
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


def _install_probe(tmp_path, monkeypatch, statements: tuple[str, ...]) -> str:
    """Put one real, pending migration on top of the head; return that head.

    Every real version file is symlinked into a temporary directory beside a
    probe revision whose ``down_revision`` is the real head, and the migration
    runner's ``Config`` is wrapped so its ``version_locations`` reads that
    directory.  Everything else about the config -- ``alembic.ini``,
    ``env.py``, the shared connection -- is the runner's own.

    Args:
        tmp_path: pytest's per-test directory.
        monkeypatch: pytest's monkeypatch fixture.
        statements: SQL the probe's upgrade runs after creating its table.
    """
    head = _real_head()
    versions = tmp_path / "versions"
    versions.mkdir()
    for migration in _VERSIONS_DIR.glob("*.py"):
        (versions / migration.name).symlink_to(migration)
    (versions / f"{_PROBE_REVISION}_probe.py").write_text(
        _PROBE_MIGRATION.format(
            revision=_PROBE_REVISION, down_revision=head, statements=statements,
        ),
        encoding="utf-8",
    )
    real_config = migration_runner.Config

    def _config_reading_the_probe(path):
        """Build the runner's config, reading versions from the probe dir."""
        config = real_config(path)
        config.set_main_option("version_locations", str(versions))
        return config

    monkeypatch.setattr(migration_runner, "Config", _config_reading_the_probe)
    return head


@pytest.fixture(name="pending_migration")
def _pending_migration(tmp_path, monkeypatch) -> str:
    """Put one real, pending migration on top of the head; return that head."""
    return _install_probe(tmp_path, monkeypatch, ())


@pytest.fixture(name="migration_refused_at_commit")
def _migration_refused_at_commit(tmp_path, monkeypatch) -> str:
    """A pending migration whose write a deferred rule refuses at COMMIT; return the head."""
    return _install_probe(tmp_path, monkeypatch, _REFUSED_AT_COMMIT)


@pytest.fixture(name="unseeded_ref_row")
def _unseeded_ref_row(app, db) -> None:
    """Delete, committed, one ref row nothing in the per-test database references.

    ``ref.statement_sources`` 'secu_checking_csv' backs an enum member
    ``ref_cache`` requires, and no statement import or external identity exists
    here to hold it, so the deploy's reference seed must put it back -- and
    must do so BEFORE the cache is read, or the strict ``ref_cache.init``
    refuses the table.
    """
    db.session.execute(text(
        "DELETE FROM ref.statement_sources WHERE name = 'secu_checking_csv'"
    ))
    db.session.commit()


def _committed(db) -> dict:
    """Read what is COMMITTED, over a connection of its own.

    No tax row is read: the deploy copied the tax law into each user's rows
    until plan step salary:X-at-1 deleted that seed, and X-at-2 deleted the
    tables.

    Args:
        db: The Flask-SQLAlchemy handle.

    Returns:
        ``stamp`` (the ``alembic_version`` row), ``probe`` (whether the probe
        migration's table exists) and ``ref_row`` (how many copies of the row
        ``unseeded_ref_row`` deletes are there: the seed's write, ``1`` once
        it is committed).
    """
    with db.engine.connect() as conn:
        return {
            "stamp": conn.execute(text(
                "SELECT version_num FROM public.alembic_version"
            )).scalar_one(),
            "probe": conn.execute(text(
                "SELECT to_regclass('budget.xcv_probe') IS NOT NULL"
            )).scalar_one(),
            "ref_row": conn.execute(text(
                "SELECT count(*) FROM ref.statement_sources "
                "WHERE name = 'secu_checking_csv'"
            )).scalar_one(),
        }


class TestARefusalCommitsNothing:
    """A refusal raised by the one commit leaves the stamp AND every write where they were."""

    def test_a_refusal_at_the_one_commit_rolls_everything_back(
        self, app, db, migration_refused_at_commit, unseeded_ref_row, capsys,
    ):
        """A deferred rule refuses the migration's write at COMMIT: nothing commits.

        The rule is DEFERRED, so nothing refuses until COMMIT -- which is the
        commit that also carries the migration's stamp and the seed's row, so
        PostgreSQL rolls all three back with the refused write.  It was the
        balanced-journal trigger refusing a ledger hook's one-legged entry
        until plan task plan#3077 deleted the hooks.
        """
        with refused_by_database_rule(r"xcv probe: refused at commit"):
            _INIT_DB.initialise_database()
        db.session.rollback()
        # Raised BY the one commit: the line printed after it never ran, and
        # the sequence before it did.
        printed = capsys.readouterr().out
        assert "Audit trigger health OK" in printed
        assert "Database initialised" not in printed

        assert _committed(db) == {
            "stamp": migration_refused_at_commit, "probe": False, "ref_row": 0,
        }


class TestACleanRunCommitsOnce:
    """With no refusal, everything the deploy wrote commits together."""

    def test_a_write_the_last_session_step_left_unflushed_is_committed(
        self, app, db, seed_user, pending_migration, monkeypatch,
    ):
        """The deploy flushes the session before committing its connection.

        The deploy commits the CONNECTION, never the session, so the implicit
        flush a session ``commit()`` performs is not there: a row a step only
        ``add()``-ed would be discarded when the session closes, silently.  The
        last session step, ``ref_cache``'s load, adds one here after its own
        reads and returns without flushing; nothing after it reads through the
        session (the audit-trigger count runs on the connection), so no
        autoflush saves it either.
        """
        user_id = seed_user["user"].id
        real_init = ref_cache.init

        def _load_then_add_without_flushing(session):
            """Load the cache, then stage one category row, leaving it unflushed."""
            real_init(session)
            db.session.add(Category(
                user_id=user_id, group_name="X-cv", item_name="Unflushed",
            ))

        monkeypatch.setattr(ref_cache, "init", _load_then_add_without_flushing)

        _INIT_DB.initialise_database()

        with db.engine.connect() as conn:
            assert conn.execute(text(
                "SELECT count(*) FROM budget.categories "
                "WHERE user_id = :u AND item_name = 'Unflushed'"
            ), {"u": user_id}).scalar_one() == 1


class TestTheSeedsAndTheCheckAreInsideTheOneTransaction:
    """Entrypoint steps 4, 6 and 7 run inside step 3 (ruling R-BAL122).

    The reference seed, the tax seed and the audit-trigger check used to run
    after ``init_database.py`` had committed, so a failure in one of them left
    the release's stamp behind a dead container: the case ``shekel-deploy``
    cannot re-pin.  Now they commit with the migration or roll back with it --
    all but the tax seed (step 6), which plan step salary:X-at-1 deleted: the
    tax law lives in the code, and no deploy copies it.
    """

    def test_a_clean_run_commits_the_seed_with_the_migration(
        self, app, db, pending_migration, unseeded_ref_row,
    ):
        """The deleted ref row commits with the stamp.

        It also pins ruling R-BAL122's ORDER on an existing database: the ref
        row is missing when the deploy starts, so a cache read before the seed
        would refuse ``StatementSource.SECU_CHECKING_CSV`` and fail the deploy.
        """
        assert _committed(db) == {
            "stamp": pending_migration, "probe": False, "ref_row": 0,
        }

        _INIT_DB.initialise_database()

        assert _committed(db) == {
            "stamp": _PROBE_REVISION, "probe": True, "ref_row": 1,
        }
        # The deploy loaded ref_cache itself, from the seeded rows: the
        # re-inserted row has a NEW id, which only a load after the seed can
        # know (conftest's per-test load read the deleted row's id).
        with db.engine.connect() as conn:
            new_id = conn.execute(text(
                "SELECT id FROM ref.statement_sources "
                "WHERE name = 'secu_checking_csv'"
            )).scalar_one()
        assert ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV,
        ) == new_id

    def test_a_missing_audit_trigger_rolls_everything_back(
        self, app, db, pending_migration, unseeded_ref_row,
    ):
        """One audit trigger is gone: the check refuses and nothing commits.

        Not the migration and not the seed's rows: before X-cv's leaf 2 this
        check was entrypoint step 7, run after step 3 had committed both.
        """
        db.session.execute(text(
            "DROP TRIGGER audit_transactions ON budget.transactions"
        ))
        db.session.commit()

        with pytest.raises(
            RuntimeError,
            match=(
                rf"Audit trigger check failed: {EXPECTED_TRIGGER_COUNT - 1} "
                rf"audit trigger\(s\), at least {EXPECTED_TRIGGER_COUNT}"
            ),
        ):
            _INIT_DB.initialise_database()
        db.session.rollback()

        assert _committed(db) == {
            "stamp": pending_migration, "probe": False, "ref_row": 0,
        }

    def test_a_surplus_audit_trigger_is_not_refused(
        self, app, db, pending_migration, unseeded_ref_row,
    ):
        """AT LEAST the expected count, as step 7 asked: an extra trigger passes.

        A missing trigger means writes that leave no audit row; a surplus one
        (a trigger this image's list does not name) is not what the check is
        for, and refusing it would fail a deploy over a harmless row.
        """
        db.session.execute(text(
            "CREATE TRIGGER audit_xcv_surplus AFTER INSERT ON budget.categories "
            "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
        ))
        db.session.commit()

        _INIT_DB.initialise_database()

        assert _committed(db) == {
            "stamp": _PROBE_REVISION, "probe": True, "ref_row": 1,
        }

    def test_the_refusal_names_each_missing_table_and_re_creates_it(
        self, app, db,
    ):
        """The refusal names every missing table, and its repair text repairs it.

        Ruling R-BAL129 (X-cv leaf 2b): the X-cv rehearsal dropped one trigger;
        the release refused, the script re-pinned the previous image, which
        refused the same database, and neither log named the table.  Two
        triggers dropped, and a surplus one added that carries the name of one
        of them on the OTHER dropped table (``audit_transactions`` on
        ``budget.categories``), leaves the count one short: the refusal must
        still name BOTH tables, by the trigger's own name on its own table --
        neither any ``audit_`` trigger on the table nor the name alone
        anywhere, and not inferred from the count.  The repair text is then
        run verbatim, as one string, the way ``psql -c`` sends a pasted line,
        and every resulting definition is compared with a fixed string rather
        than with a trigger the code under test built.
        """
        db.session.execute(text(
            "DROP TRIGGER audit_transactions ON budget.transactions"
        ))
        db.session.execute(text(
            "DROP TRIGGER audit_categories ON budget.categories"
        ))
        db.session.execute(text(
            "CREATE TRIGGER audit_transactions AFTER INSERT "
            "ON budget.categories "
            "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
        ))
        db.session.commit()

        with db.engine.connect() as conn:
            with pytest.raises(RuntimeError) as refused:
                require_audit_triggers(conn)
        message = str(refused.value)
        assert "Missing: budget.categories, budget.transactions (" in message
        repair = re.search(
            r"re-create it as the database owner: (.*?)  If instead", message,
        ).group(1)

        with db.engine.begin() as conn:
            conn.exec_driver_sql(repair)
        with db.engine.connect() as conn:
            assert require_audit_triggers(conn) == EXPECTED_TRIGGER_COUNT + 1
            assert conn.execute(text(
                "SELECT pg_get_triggerdef(oid) FROM pg_trigger "
                "WHERE tgname IN ('audit_categories', 'audit_transactions') "
                "ORDER BY 1"
            )).scalars().all() == [
                "CREATE TRIGGER audit_categories AFTER INSERT OR DELETE OR "
                "UPDATE ON budget.categories FOR EACH ROW EXECUTE FUNCTION "
                "system.audit_trigger_func()",
                "CREATE TRIGGER audit_transactions AFTER INSERT ON "
                "budget.categories FOR EACH ROW EXECUTE FUNCTION "
                "system.audit_trigger_func()",
                "CREATE TRIGGER audit_transactions AFTER INSERT OR DELETE OR "
                "UPDATE ON budget.transactions FOR EACH ROW EXECUTE FUNCTION "
                "system.audit_trigger_func()",
            ]



def _with_stray_calls(monkeypatch, db, step, *, before=None, after=None):
    """Run a real step of the deploy between stray session calls.

    Args:
        monkeypatch: pytest's monkeypatch fixture.
        db: The Flask-SQLAlchemy handle whose session the steps use.
        step: ``(module, function name)`` of the step's real function: the
            reference seed, which ``ref_cache``'s load follows on the session,
            or that load, the LAST step that uses the session (ruling
            R-BAL122's order; only the audit-trigger count, on the connection,
            follows).
        before: ``"commit"`` or ``"rollback"``, called on ``db.session`` before
            the real step runs; ``None`` for no call.
        after: The same, called after it.
    """
    module, name = step
    real_step = getattr(module, name)

    def _stray_calls_around_the_step(*args, **kwargs):
        """Call the stray session method(s) around the real step."""
        if before is not None:
            getattr(db.session, before)()
        result = real_step(*args, **kwargs)
        if after is not None:
            getattr(db.session, after)()
        return result

    monkeypatch.setattr(module, name, _stray_calls_around_the_step)


_THE_SEED = (_INIT_DB, "seed_reference_data")
_LAST_SESSION_STEP = (ref_cache, "init")


class TestNothingInsideTheSequenceCanCommit:
    """A stray commit or rollback inside the deploy saves nothing early or partially.

    Rulings **R-BAL113** (the session joins the deploy's transaction, so a
    session commit does not reach it) and **R-BAL118** (the session holds only
    that one transaction, so a stray call is followed by a refusal, never by a
    transaction of the session's own).  Before X-cv's leaf 1b a stray COMMIT
    here committed the migrations on the spot, and a rollback followed by a
    commit committed the later steps' writes WITHOUT the migrations.  The
    stray calls sat around the ledger hooks until plan task plan#3077 deleted
    them; the seed and ``ref_cache``'s load are the session's steps since.
    """

    def test_a_stray_rollback_saves_nothing(
        self, app, db, pending_migration, unseeded_ref_row, monkeypatch,
    ):
        """The seed rolls back first: its first query refuses.

        ``ref_cache`` rolls back on a missing ref table, and a rollback ends the
        deploy's transaction, the migration with it.  The session may not then
        begin a transaction of its own for the seed to write into.
        """
        _with_stray_calls(monkeypatch, db, _THE_SEED, before="rollback")
        with pytest.raises(InvalidRequestError) as refused:
            _INIT_DB.initialise_database()

        assert _committed(db) == {
            "stamp": pending_migration, "probe": False, "ref_row": 0,
        }
        assert "Autobegin is disabled" in str(refused.value)
        db.session.rollback()

    def test_a_stray_commit_mid_sequence_saves_nothing(
        self, app, db, pending_migration, unseeded_ref_row, monkeypatch,
    ):
        """The seed commits its rows: ``ref_cache``'s first query refuses.

        The commit is not passed to the deploy's transaction, so neither the
        migration nor the row the seed flushed is committed by it, and the
        deploy then fails with nothing saved.
        """
        _with_stray_calls(monkeypatch, db, _THE_SEED, after="commit")
        with pytest.raises(InvalidRequestError) as refused:
            _INIT_DB.initialise_database()

        assert _committed(db) == {
            "stamp": pending_migration, "probe": False, "ref_row": 0,
        }
        assert "Autobegin is disabled" in str(refused.value)
        db.session.rollback()

    def test_a_rollback_then_a_commit_saves_nothing(
        self, app, db, pending_migration, unseeded_ref_row, monkeypatch,
    ):
        """The escape ruling R-BAL118 closed: roll back, write, commit.

        Measured before the ruling: a session allowed to begin a transaction of
        its own after the rollback wrote a ledger hook's correction into it and
        the commit saved it, WITHOUT the migration the rollback undid.
        """
        _with_stray_calls(
            monkeypatch, db, _THE_SEED, before="rollback", after="commit",
        )
        with pytest.raises(InvalidRequestError) as refused:
            _INIT_DB.initialise_database()

        assert _committed(db) == {
            "stamp": pending_migration, "probe": False, "ref_row": 0,
        }
        assert "Autobegin is disabled" in str(refused.value)
        db.session.rollback()

    def test_a_rollback_after_the_last_statement_fails_the_one_commit(
        self, app, db, pending_migration, unseeded_ref_row, monkeypatch,
    ):
        """The last session step rolls back after its work: the deploy's commit refuses.

        That step is ``ref_cache``'s load since plan task plan#3077 deleted the
        three ledger hooks (ruling plan#2516, option A), the third of which it
        was until then; between ruling R-BAL122 and plan step salary:X-at-1 it
        was the tax seed.  Each move re-expressed the case on the new last
        step, the claim unchanged.
        """
        _with_stray_calls(monkeypatch, db, _LAST_SESSION_STEP, after="rollback")
        with pytest.raises(InvalidRequestError) as refused:
            _INIT_DB.initialise_database()

        assert _committed(db) == {
            "stamp": pending_migration, "probe": False, "ref_row": 0,
        }
        assert "transaction is inactive" in str(refused.value)
        db.session.rollback()

    def test_a_commit_after_the_last_statement_is_harmless(
        self, app, db, pending_migration, unseeded_ref_row, monkeypatch,
    ):
        """The last session step commits after its work: everything commits, once.

        ``ref_cache``'s load, as in the case above.
        """
        _with_stray_calls(monkeypatch, db, _LAST_SESSION_STEP, after="commit")

        _INIT_DB.initialise_database()

        assert _committed(db) == {
            "stamp": _PROBE_REVISION, "probe": True, "ref_row": 1,
        }


class TestTheRunnerCommitsNothing:
    """``app.migration_runner`` never commits, whatever connection it is handed."""

    def test_a_connection_outside_a_transaction_is_not_committed(
        self, app, db, pending_migration, unseeded_ref_row,
    ):
        """Handed a connection with no transaction, the runner opens one it never commits.

        Alembic begins and COMMITS a transaction of its own on a connection that
        has none, which is how a deploy whose transaction a stray rollback had
        already ended would have committed its migrations on the spot.  The
        connection closes uncommitted here, so the migration must be gone.
        """
        with db.engine.connect() as connection:
            migration_runner.upgrade_to_head(connection)

        assert _committed(db) == {
            "stamp": pending_migration, "probe": False, "ref_row": 0,
        }


class TestTheDeployPutsTheCallersSessionBack:
    """The deploy borrows the app context's session slot and returns it untouched."""

    def test_the_session_held_before_is_the_session_held_after(
        self, app, db, seed_user,
    ):
        """A caller's session, and its uncommitted work, survive the deploy.

        The staged row is neither committed by the deploy's commit nor
        discarded by it: still pending in the same session afterwards, and
        invisible to a connection of its own.
        """
        user_id = seed_user["user"].id
        pending = Category(user_id=user_id, group_name="X-cv", item_name="Held")
        db.session.add(pending)
        held = db.session()

        _INIT_DB.initialise_database()

        assert db.session() is held
        assert pending in held.new
        with db.engine.connect() as conn:
            assert conn.execute(text(
                "SELECT count(*) FROM budget.categories "
                "WHERE user_id = :u AND item_name = 'Held'"
            ), {"u": user_id}).scalar_one() == 0
        db.session.rollback()


class TestEveryStatementRunsOnTheDeploysConnection:
    """The migrations, the reference seed and ``ref_cache`` share ONE connection."""

    def test_one_backend_serves_the_migration_and_the_session_steps(
        self, app, db, pending_migration, unseeded_ref_row,
    ):
        """Every statement the deploy issues reaches the same PostgreSQL backend.

        Flask-SQLAlchemy's session ignores a session-level bind, so a deploy
        that only configured one would run the seed and the cache on a pooled
        connection of their own, outside the transaction the migration ran in.
        The backend of every statement is recorded, and the record must hold
        the probe migration's DDL and the seed's write, so it cannot pass by
        having seen neither.  The write watched was a ledger hook's until plan
        task plan#3077 deleted the hooks.
        """
        statements = []

        def _record(conn, _cursor, statement, *_args):
            """Note which backend ran *statement*."""
            statements.append((
                conn.connection.dbapi_connection.info.backend_pid, statement,
            ))

        event.listen(db.engine, "before_cursor_execute", _record)
        try:
            _INIT_DB.initialise_database()
        finally:
            event.remove(db.engine, "before_cursor_execute", _record)

        assert any("xcv_probe" in sql for _pid, sql in statements)
        assert any(
            sql.lstrip().upper().startswith("INSERT INTO REF.STATEMENT_SOURCES")
            for _pid, sql in statements
        )
        assert len({pid for pid, _sql in statements}) == 1
        assert _committed(db)["ref_row"] == 1


def _empty_the_database(db) -> None:
    """Make the per-test database FRESH: no application schema, no stamp.

    Drops the five application schemas and the stamp, then replays
    ``scripts/init_db.sql`` -- entrypoint step 2, which re-creates the empty
    schemas and an empty ``public.alembic_version`` -- so the database is
    exactly what step 3 meets on a first boot.  The per-test database is
    dropped after every test, so nothing outlives this.
    """
    db.session.execute(text(
        "DROP SCHEMA ref, auth, budget, salary, system CASCADE"
    ))
    db.session.execute(text("DROP TABLE public.alembic_version"))
    db.session.execute(text(
        (_REPO_ROOT / "scripts" / "init_db.sql").read_text(encoding="utf-8")
    ))
    db.session.commit()


def _fresh_state(db) -> dict:
    """Read what a fresh build has COMMITTED, over a connection of its own."""
    with db.engine.connect() as conn:
        return {
            "users_table": conn.execute(text(
                "SELECT to_regclass('auth.users') IS NOT NULL"
            )).scalar_one(),
            "stamps": conn.execute(text(
                "SELECT array_agg(version_num) FROM public.alembic_version"
            )).scalar_one(),
        }


def _committed_builtin_account_types(db) -> int:
    """Count, over a connection of its own, the COMMITTED built-in account types."""
    with db.engine.connect() as conn:
        return conn.execute(text(
            "SELECT count(*) FROM ref.account_types WHERE user_id IS NULL"
        )).scalar_one()


class TestAFreshBuildIsOneTransaction:
    """The first-boot branch commits its schema and its stamp together, or neither."""

    def test_a_failure_before_the_stamp_leaves_the_database_empty(
        self, app, db, monkeypatch,
    ):
        """The stamp fails after every table was created: no table survives.

        Before X-cv each block committed as it went, so this failure left
        ``auth.users`` behind -- a database the next boot reads as "existing"
        and tries to migrate from an empty stamp.
        """
        _empty_the_database(db)

        def _fail(*_args, **_kwargs):
            """Stand in for a stamp that fails."""
            raise RuntimeError("forged stamp failure")

        monkeypatch.setattr(_INIT_DB, "stamp_head", _fail)
        with pytest.raises(RuntimeError, match="forged stamp failure"):
            _INIT_DB.initialise_database()

        assert _fresh_state(db) == {"users_table": False, "stamps": None}
        db.session.rollback()

    def test_a_failure_after_the_seed_leaves_the_database_empty(
        self, app, db, monkeypatch,
    ):
        """The audit-trigger check refuses after the reference seed ran: nothing survives."""
        _empty_the_database(db)

        def _refuse(_connection):
            """Stand in for an audit-trigger check that refuses."""
            raise RuntimeError("forged audit-trigger refusal")

        monkeypatch.setattr(_INIT_DB, "require_audit_triggers", _refuse)
        with pytest.raises(RuntimeError, match="forged audit-trigger refusal"):
            _INIT_DB.initialise_database()

        assert _fresh_state(db) == {"users_table": False, "stamps": None}
        db.session.rollback()

    def test_a_clean_build_commits_the_schema_and_the_stamp_together(
        self, app, db,
    ):
        """The tables exist and the stamp names the head, both committed."""
        _empty_the_database(db)

        _INIT_DB.initialise_database()

        assert _fresh_state(db) == {
            "users_table": True, "stamps": [_real_head()],
        }

    def test_a_clean_build_commits_the_reference_rows_too(self, app, db):
        """The reference rows are committed with the schema and the stamp.

        Finding BAL-536: before ruling R-BAL122 this build committed EMPTY ref
        tables, and entrypoint step 4's cache read refused them, so no new
        production install could boot.  The strict ``ref_cache.init`` now runs
        inside the build, after the seed, and the build returning at all means
        it passed.
        """
        _empty_the_database(db)

        _INIT_DB.initialise_database()

        assert _committed_builtin_account_types(db) == len(ACCT_TYPE_SEEDS)

    def test_a_clean_build_installs_every_family_the_image_is_verified_for(
        self, app, db,
    ):
        """A first boot installs each trigger family the test image is verified for.

        The tests above read ``auth.users``, the stamp and the account types,
        so the pay-stub or the sighting block deleted from
        ``init_fresh_database`` left them green while a new install lost that
        rule -- a pay stub that can be deleted, a bank line that outlives its
        last sighting (review L1 of the ``salary:S11-a`` carry-merge).  The
        checks are the image verifier's own
        (:func:`scripts.build_test_db_image.template_checks`): one list, so a
        family added there is graded here with nothing to remember.  NOT
        counted, here or in the image: the balanced-journal trigger
        (``apply_posting_infrastructure``) and the books-boundary triggers
        (``apply_opening_infrastructure``), whose modules export no constant
        naming their triggers (finding BAL-542).
        """
        _empty_the_database(db)

        _INIT_DB.initialise_database()

        checks = template_checks()
        with db.engine.connect() as conn:
            found = {
                label: conn.execute(text(sql)).scalar_one()
                for label, sql, _expected in checks
            }
        assert found == {label: expected for label, _sql, expected in checks}


class TestTheDeployKeepsTheAppsLogging:
    """Migrating on the deploy's connection leaves the app's logging alone."""

    def test_migrating_on_the_shared_connection_leaves_app_loggers_enabled(
        self, app, db,
    ):
        """``env.py`` skips alembic.ini's ``fileConfig`` when handed a connection.

        ``fileConfig`` disables every logger that exists when it runs, so before
        X-cv every ``app.*`` logger in the deploy host went silent once the
        migrations had run -- the cash resync's skipped-transfer warning among
        them, until plan task plan#3077 deleted that resync.  The root logger's
        handlers are asserted too: ``fileConfig``
        replaces them with alembic.ini's WARNING console.
        """
        app_logger = logging.getLogger("app.services.xcv_probe")
        root_handlers = list(logging.getLogger().handlers)

        with db.engine.connect() as connection:
            connection.begin()
            _INIT_DB.migrate_existing_database(connection)

        assert app_logger.disabled is False
        assert logging.getLogger().handlers == root_handlers
