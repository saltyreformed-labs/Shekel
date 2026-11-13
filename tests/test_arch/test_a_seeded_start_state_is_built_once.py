"""Architecture test: what a NAMED SEEDED START STATE guarantees.

Plan step **balance:X-be-2**, finding **N-387**.  The suite could express two
start states -- empty, or "build it yourself in a fixture" -- and nothing could
say *start from a prepared world*.  So a module whose every test needed the
same world rebuilt it per test: measured on the ``url_map`` sweep, 302 ms of
setup to make 22 ms of requests, 236 times over, 93% of that file.

``@pytest.mark.seeded_start_state("name")`` says it instead.  The world is
built ONCE per xdist worker and frozen into a snapshot database; every test
that declares it still gets its own private clone of that snapshot.

**The two halves are tested apart because only one of them is new.**  That a
test starts from the world is the FEATURE.  That the test still cannot see
another test's writes is the CONTRACT the suite already had, and it is the half
a shared-database design would have traded away -- so it is graded here
directly, by two tests that each refuse to find the other's row.

**Why an ORDER-FREE isolation control.**  Both isolation arms below assert
absence and then write, rather than one writing and a later one checking: a
check-after-write pair proves nothing when the checker happens to run first,
and under xdist "first" is not a property this file controls.  Written this
way, whichever runs second is the one that fails if the copies are shared, so
the control cannot pass by scheduling luck.
"""

from decimal import Decimal

import pytest

from sqlalchemy import text

from app.models.category import Category
from app.models.user import User
from tests import _seeded_start_states as seeded_states
from tests import _worker_database as worker_database
from tests._seed_owner_fixtures import SEED_USER_EMAIL, build_seed_user
from tests._seeded_start_states import (
    _build_seeded_snapshot,
    register_seeded_state,
)


#: How many times this file's world has been BUILT in this process, and how
#: many tests have DECLARED it.  Both are needed: "built once" is vacuously
#: true for whichever declaring test runs first, so the arm below asserts one
#: build against at least two declarations rather than against a count of one.
_BUILDS = []
_DECLARERS = set()


def _build_arch_probe_world(db):
    """A minimal world: the seeded owner and nothing else.

    Args:
        db: The Flask-SQLAlchemy extension to write through.

    Returns:
        dict with the owner's ``user_id`` and default ``account_id``.
    """
    _BUILDS.append(1)
    seed_user = build_seed_user(db)
    return {
        "user_id": seed_user["user"].id,
        "account_id": seed_user["account"].id,
    }


def _build_arch_second_world(db):
    """A SECOND world, so the two-worlds-on-one-worker case has an owner.

    The seam's whole argument against a wider fixture scope is that one
    database cannot hold two modules' states at once.  A snapshot per world is
    the answer, and nothing graded it until this existed.

    Args:
        db: The Flask-SQLAlchemy extension to write through.

    Returns:
        dict carrying the owner's display name, which is what tells the two
        worlds apart from inside a test.
    """
    seed_user = build_seed_user(db)
    user = seed_user["user"]
    user.display_name = "Second World Owner"
    db.session.commit()
    return {"user_id": user.id, "display_name": user.display_name}


register_seeded_state("arch_probe", _build_arch_probe_world)
register_seeded_state("arch_probe_2", _build_arch_second_world)


#: The marker row both isolation arms write.  One name, because the point is
#: that each arm finds the table WITHOUT it.
_ISOLATION_MARKER = "x-be-2 isolation marker"


def _write_isolation_marker(db, user_id):
    """Write the marker category this world's isolation arms look for."""
    db.session.add(Category(
        user_id=user_id, group_name="Probe", item_name=_ISOLATION_MARKER,
    ))
    db.session.commit()


@pytest.mark.xdist_group("x_be_2_seeded_start_state")
@pytest.mark.seeded_start_state("arch_probe")
class TestADeclaredWorldIsPresentAndPrivate:
    """A declared world arrives built, and each test's copy is its own.

    Pinned to one xdist worker so the build-count arm below is measuring one
    process.  Without that, two arms on two workers each legitimately build
    once and the arm could never see a rebuild it was meant to catch.
    """

    def test_the_world_is_there_without_the_test_building_it(
        self, db, seeded_world,
    ):
        """The owner exists, and the test asked for no fixture that made one."""
        user = db.session.get(User, seeded_world["user_id"])
        assert user is not None, (
            "a test declaring a world started without it; the db fixture "
            "cloned the empty template instead of the world's snapshot"
        )
        assert user.email == SEED_USER_EMAIL

    @pytest.fixture(autouse=True)
    def _record_this_declarer(self, request):
        """Count every test in this class that declares the world.

        An autouse fixture rather than a line in each test, so a test added
        later is counted without anyone remembering to.
        """
        _DECLARERS.add(request.node.nodeid)

    def test_the_worlds_own_audit_rows_are_not_a_tests_to_see(
        self, db, seeded_world,
    ):
        """``system.audit_log`` starts empty, exactly as the template does.

        Seeding an owner fires the audit triggers.  Shipping those rows inside
        the world would silently change the meaning of every ``audit_log``
        count in any test that later adopts one, so the build truncates the log
        for the same reason ``scripts/build_test_template.py`` does.
        """
        count = db.session.execute(
            text("SELECT count(*) FROM system.audit_log")
        ).scalar()
        assert count == 0, (
            f"the world shipped {count} audit rows; a test adopting it would "
            f"be counting the SEED's writes as its own"
        )

    def test_isolation_holds_arm_one(self, db, seeded_world):
        """This copy has no marker row -- then it writes one.

        Paired with :meth:`test_isolation_holds_arm_two`; see the module
        docstring for why both assert-then-write rather than one writing for
        the other to find.
        """
        assert db.session.query(Category).filter_by(
            item_name=_ISOLATION_MARKER,
        ).count() == 0, (
            "another test's write is visible here, so the tests declaring "
            "this world are sharing one database rather than each holding a "
            "private clone of the snapshot"
        )
        _write_isolation_marker(db, seeded_world["user_id"])

    def test_isolation_holds_arm_two(self, db, seeded_world):
        """This copy has no marker row -- then it writes one.

        The twin of :meth:`test_isolation_holds_arm_one`.
        """
        assert db.session.query(Category).filter_by(
            item_name=_ISOLATION_MARKER,
        ).count() == 0, (
            "another test's write is visible here, so the tests declaring "
            "this world are sharing one database rather than each holding a "
            "private clone of the snapshot"
        )
        _write_isolation_marker(db, seeded_world["user_id"])

    def test_zz_it_was_built_once_for_all_of_them(self, db, seeded_world):
        """The builder ran ONCE on this worker, for every test above.

        **This is the entire claim of the step**; every other arm here would
        pass equally against a fixture that rebuilt the world each time.

        It asserts one build against MORE THAN ONE declaring test, because
        ``builds == 1`` alone is vacuously true for whichever declaring test
        runs first -- on its own it would wave through a rebuild-per-test
        regression whenever it happened to be scheduled first.  The declarer
        count comes from an autouse fixture on the class, so it counts tests
        added later too.

        Named ``zz`` and placed last so it runs after its siblings: pytest
        runs a class's methods in definition order, ``--dist=loadgroup`` plus
        this class's ``xdist_group`` keeps them in one process, and
        ``pytest-randomly`` is not installed.  If that ever stops holding, the
        declarer assertion FAILS rather than passing vacuously, which is the
        safe direction for an arm whose whole job is to notice a rebuild.
        """
        assert len(_DECLARERS) > 1, (
            f"only {len(_DECLARERS)} test declared this world before the "
            f"build count was judged, so 'built once' says nothing -- one "
            f"build for one declaring test is what a rebuild-per-test "
            f"regression also produces. Declarers seen: {sorted(_DECLARERS)}"
        )
        assert len(_BUILDS) == 1, (
            f"the world was built {len(_BUILDS)} times on this worker for "
            f"{len(_DECLARERS)} declaring tests. Once is the point: a rebuild "
            f"per test is the cost this seam removes"
        )


class TestAnUndeclaredTestIsUntouched:
    """A test that declares no world still starts from an empty database."""

    def test_no_world_leaks_into_a_test_that_did_not_ask(self, db):
        """The 11,700 tests that have not adopted this seam are unaffected.

        The world's owner is written by a builder this file registered at
        import, so it exists in this PROCESS; what must not exist is the row,
        in a database this test was given.
        """
        assert db.session.query(User).count() == 0, (
            "a seeded world reached a test that declared none -- the db "
            "fixture is not defaulting to the empty template"
        )

    def test_asking_for_a_world_without_declaring_one_says_so(
        self, request, db,
    ):
        """``seeded_world`` REFUSES rather than handing back an empty dict.

        Asks for the fixture itself, which is the behaviour: an earlier draft
        called the private helper and asserted its return value, so the
        ``LookupError`` this grades was dead code and mutating the fixture to
        ``return ids or {}`` left the arm green.
        """
        with pytest.raises(LookupError, match="without declaring one"):
            request.getfixturevalue("seeded_world")

    def test_a_marker_naming_no_world_says_which_test_forgot(self, request, db):
        """The marker written with no argument names the test, not IndexError.

        Applied here by hand rather than as a decorator: the point is the
        refusal, and a class-level marker would change what every other test
        in it starts from.
        """
        request.node.add_marker(pytest.mark.seeded_start_state)
        with pytest.raises(ValueError, match="no world named"):
            seeded_states.declared_seeded_state(request)


@pytest.mark.xdist_group("x_be_2_seeded_start_state")
@pytest.mark.seeded_start_state("arch_probe_2")
class TestTwoWorldsCoexistOnOneWorker:
    """A second world on the same worker is a second snapshot, not a clash.

    **The case the whole design exists for.**  The seam's argument against a
    module- or class-scoped fixture is that one database cannot hold two
    modules' states while xdist interleaves them; a snapshot per world is the
    answer, and nothing graded that until this class existed -- the cache is a
    dict keyed by name and only one key was ever asserted on.

    Pinned to the same worker as the first world's class so both are built in
    one process, which is what makes "coexist" the thing being measured.
    """

    def test_this_world_is_the_one_declared_not_the_other(
        self, db, seeded_world,
    ):
        """The owner here is the SECOND world's, though both are built here."""
        user = db.session.get(User, seeded_world["user_id"])
        assert user.display_name == "Second World Owner", (
            f"a test declaring 'arch_probe_2' was given a database holding "
            f"{user.display_name!r} -- the two worlds are sharing one snapshot"
        )
        assert seeded_world["display_name"] == "Second World Owner"

    def test_the_first_worlds_marker_row_is_not_here(self, db, seeded_world):
        """Neither world inherits what tests of the other wrote.

        The first world's isolation arms each commit a marker category. A
        snapshot shared between the two worlds would carry it here.
        """
        assert db.session.query(Category).filter_by(
            item_name=_ISOLATION_MARKER,
        ).count() == 0


@pytest.mark.xdist_group("x_be_2_seeded_start_state")
@pytest.mark.seeded_start_state("arch_probe")
class TestTheFirstWorldSurvivesTheSecondBeingBuilt:
    """Declaring world one AFTER world two was built still gives world one.

    **The arm that catches two worlds sharing one snapshot database**, which
    the class above cannot: with a single shared snapshot each world's tests
    still pass, because every class runs to completion before the next world
    is built and overwrites it.  Only a test that declares the FIRST world
    after the second exists can see the collision -- measured, by mutating
    the snapshot name to a constant, which left all sixteen arms green until
    this class existed.

    Placed after ``TestTwoWorldsCoexistOnOneWorker`` and pinned to the same
    worker, which is what makes "after" true.
    """

    def test_the_first_world_is_still_itself(self, db, seeded_world):
        """World one's owner, not the one world two renamed."""
        user = db.session.get(User, seeded_world["user_id"])
        assert user.display_name == "Test User", (
            f"a test declaring 'arch_probe' was given {user.display_name!r} "
            f"-- the second world's build overwrote this world's snapshot, so "
            f"the two are sharing one database"
        )


class TestTheSeamRefusesWhatItCannotDeliver:
    """The registry's own preconditions, which fire at import time."""

    @pytest.fixture()
    def restore_registry(self):
        """Undo anything a test in this class registers or builds.

        Both dicts, not only the builders: a test that registered a world and
        then caused it to be built would leave a snapshot name cached under a
        builder this fixture had already removed.
        """
        builders = dict(seeded_states._SEEDED_STATE_BUILDERS)
        snapshots = dict(seeded_states._SEEDED_STATE_SNAPSHOTS)
        yield
        seeded_states._SEEDED_STATE_BUILDERS.clear()
        seeded_states._SEEDED_STATE_BUILDERS.update(builders)
        seeded_states._SEEDED_STATE_SNAPSHOTS.clear()
        seeded_states._SEEDED_STATE_SNAPSHOTS.update(snapshots)

    def test_two_worlds_may_not_share_one_name(self, restore_registry):
        """One name is one world: two would share one snapshot database."""
        register_seeded_state("x_be_2_dupe", lambda db: {})
        with pytest.raises(ValueError, match="already registered"):
            register_seeded_state("x_be_2_dupe", lambda db: {})

    def test_registering_the_same_builder_twice_is_not_a_conflict(
        self, restore_registry,
    ):
        """Re-import must not raise: a module can be imported more than once."""
        def builder(db):
            return {}

        register_seeded_state("x_be_2_same", builder)
        register_seeded_state("x_be_2_same", builder)

    def test_a_name_too_long_to_survive_postgres_is_refused(
        self, restore_registry,
    ):
        """PostgreSQL truncates at 63 bytes, and two truncated names collide.

        Refused at REGISTRATION, which runs on every session, rather than at
        first use -- which would fire only on the run that happened to use both
        worlds, and only after one had already served the other's tests.
        """
        with pytest.raises(ValueError, match="truncates"):
            register_seeded_state("w" * 64, lambda db: {})

    def test_an_unregistered_world_names_the_ones_that_exist(self, app):
        """A typo in a marker fails with the list, before it touches a database."""
        with pytest.raises(KeyError, match="no seeded start state"):
            _build_seeded_snapshot("x_be_2_never_registered", app)

    def test_a_builder_returning_rows_instead_of_ids_is_refused(self):
        """A world's dict must be flat scalars, and that is ENFORCED.

        The dict is built once and shallow-copied to each declaring test, so a
        nested container would be ONE object shared by tests that each believe
        they hold their own -- and a test mutating it would silently edit what
        every later test sees. The refusal is what makes the shallow copy
        sufficient rather than merely usual.
        """
        with pytest.raises(RuntimeError, match="non-scalar values"):
            seeded_states._plain_data_or_raise("w", {"rows": [1, 2]})
        with pytest.raises(RuntimeError, match="not a dict"):
            seeded_states._plain_data_or_raise("w", [1, 2])
        # Scalars a builder legitimately returns are accepted unchanged.
        flat = {"a": 1, "b": "x", "c": None, "d": Decimal("1.00")}
        assert seeded_states._plain_data_or_raise("w", flat) is flat

    def test_the_refusal_is_WIRED_into_the_build(
        self, app, db, restore_registry,
    ):
        """A real build of a bad world is refused, not just the checker.

        The arm above grades the predicate; this grades that the predicate is
        CALLED.  Deleting the call from ``_build_seeded_snapshot`` left every
        other arm in this file green, which is the whole reason this exists.

        **This test releases the engine first, and must.**  A build drops the
        worker database ``WITH (FORCE)``, which severs the connection THIS
        test is holding -- leaving the ``db`` fixture's teardown to roll back
        a dead connection and report an ERROR after a passing test.  Releasing
        first is what the ``db`` fixture itself does before its own drop, for
        the same reason.  The build re-clones, so the database this test is
        torn down against exists again by the time it returns.
        """
        def rows_not_ids(_db):
            return {"accounts": [1, 2, 3]}

        register_seeded_state("x_be_2_wired", rows_not_ids)
        db.session.remove()
        db.engine.dispose()
        with pytest.raises(RuntimeError, match="non-scalar values"):
            _build_seeded_snapshot("x_be_2_wired", app)


class TestASnapshotDoesNotOutliveItsSession:
    """The load-bearing half of "never persisted between runs".

    A world holds rows dated from ``display_today()``, so a snapshot that
    survived its session would be a start state no code describes -- finding
    **N-385**'s shape. The code that prevents it had no test.
    """

    def test_session_finish_drops_the_snapshots_it_recorded(self, db):
        """``_drop_seeded_snapshots`` really drops, and forgets.

        Operates on a THROWAWAY database registered into the cache under a
        name no test declares, so the real worlds this worker built are left
        alone; the cache is restored either way.
        """
        throwaway = f"{worker_database._WORKER_DB_NAME}__world_x_be_2_probe"
        worker_database._clone_worker_database(
            throwaway, worker_database._WORKER_ADMIN_URL,
        )
        assert worker_database.database_exists(throwaway)

        real = dict(seeded_states._SEEDED_STATE_SNAPSHOTS)
        seeded_states._SEEDED_STATE_SNAPSHOTS.clear()
        seeded_states._SEEDED_STATE_SNAPSHOTS["x_be_2_probe"] = (
            throwaway, {},
        )
        try:
            seeded_states._drop_seeded_snapshots()
            assert not worker_database.database_exists(throwaway), (
                "session finish left a world snapshot on the cluster; its "
                "rows are dated from a session that has ended"
            )
            assert not seeded_states._SEEDED_STATE_SNAPSHOTS, (
                "the cache still names a snapshot that no longer exists, so "
                "a later build would clone from a database that is gone"
            )
        finally:
            seeded_states._SEEDED_STATE_SNAPSHOTS.update(real)
            worker_database._drop_worker_database(
                throwaway, worker_database._WORKER_ADMIN_URL,
            )
