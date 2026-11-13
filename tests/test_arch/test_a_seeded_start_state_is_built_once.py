"""Architecture test: what a NAMED SEEDED START STATE guarantees.

Plan step **balance:X-be-2**, finding **N-387**.  The suite could express two
start states -- empty, or "build it yourself in a fixture" -- and nothing could
say *start from a prepared world*.  So a module whose every test needed the
same world rebuilt it per test: measured on the ``url_map`` sweep, 302 ms of
setup to make 22 ms of requests, 236 times over, 93% of that file.

``@pytest.mark.seeded_start_state("name")`` says it instead.  Every world the
run's tests declare is built ONCE per xdist worker, in one pass, and frozen into
a snapshot database of its own; every test that declares one still gets its own
private clone of that snapshot.

**No test here counts builds across tests** (plan#2954, ruling plan#3767).
:func:`tests._seeded_start_states.built_worlds` is the only place a world is
built, it builds the list :func:`tests._seeded_start_states.declared_worlds`
names, which holds each world once, and it runs inside a session-scoped fixture.
The list and the pass are ours, and each is graded by one test below; that
pytest runs a session fixture once per process is the contract the ``app``
fixture already relies on.  This file once counted builds across the declaring
tests, which only meant something with those tests in one process, in order,
and so needed an ``xdist_group``.

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

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from sqlalchemy import text

from app.models.category import Category
from app.models.user import User
from tests import _app_fixtures as app_fixtures
from tests import _seeded_start_states as seeded_states
from tests import _worker_database as worker_database
from tests._seed_owner_fixtures import SEED_USER_EMAIL, build_seed_user
from tests._seeded_start_states import (
    _build_seeded_snapshot,
    register_seeded_state,
)


def _build_arch_probe_world(db):
    """A minimal world: the seeded owner and nothing else.

    Args:
        db: The Flask-SQLAlchemy extension to write through.

    Returns:
        dict with the owner's ``user_id`` and default ``account_id``.
    """
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


class _CollectedTest:
    """A stand-in for a collected test: its node id and the one marker it carries.

    What the marker reader and the build pass ask of a test, and nothing more,
    so a test of them needs no collection and adds no marker to a real node.
    """

    def __init__(self, nodeid, decorator=None):
        """Carry *decorator*'s mark, ``pytest.mark.seeded_start_state(...)``, or none."""
        self.nodeid = nodeid
        self._mark = None if decorator is None else decorator.mark

    def get_closest_marker(self, name):
        """The carried mark when *name* is its name, as a node answers."""
        if self._mark is not None and self._mark.name == name:
            return self._mark
        return None


@pytest.mark.seeded_start_state("arch_probe")
class TestADeclaredWorldIsPresentAndPrivate:
    """A declared world arrives built, and each test's copy is its own."""

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

    def test_this_world_is_the_one_declared_not_the_other(
        self, db, seeded_world,
    ):
        """World one's owner, not the one world two renamed.

        Both worlds are built in one pass before either's tests run, so if the
        two shared one snapshot database, the one built second would have
        overwritten the first: this test or its twin in
        :class:`TestTwoWorldsCoexistOnOneWorker` fails on every run.
        """
        user = db.session.get(User, seeded_world["user_id"])
        assert user.display_name == "Test User", (
            f"a test declaring 'arch_probe' was given {user.display_name!r} "
            f"-- the second world's build overwrote this world's snapshot, so "
            f"the two are sharing one database"
        )

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


class TestAnUndeclaredTestIsUntouched:
    """A test that declares no world still starts from an empty database."""

    def test_no_world_leaks_into_a_test_that_did_not_ask(self, db):
        """The 11,700 tests that have not adopted this seam are unaffected.

        The worlds' owners exist in their snapshots on this cluster once any
        declaring test has run on this worker; what must not exist is the row,
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

    def test_a_marker_naming_no_world_says_which_test_forgot(self):
        """The marker written with no argument names the test, not IndexError.

        Asked of a stand-in rather than of this test's own node: a marker added
        to a real node would be read by the build pass over every collected
        test, and would refuse every declaring test on this worker.
        """
        forgot = _CollectedTest(
            "tests/x.py::test_forgot", pytest.mark.seeded_start_state,
        )
        with pytest.raises(ValueError, match="test_forgot.*no world named"):
            seeded_states.declared_seeded_state(forgot)


@pytest.mark.seeded_start_state("arch_probe_2")
class TestTwoWorldsCoexistOnOneWorker:
    """A second world on the same worker is a second snapshot, not a clash.

    **The case the whole design exists for.**  The seam's argument against a
    module- or class-scoped fixture is that one database cannot hold two
    modules' states while xdist interleaves them; a snapshot per world is the
    answer.  Every worker that runs a declaring test builds both of this file's
    worlds in one pass, before either's tests run, so "coexist" holds on every
    worker this class runs on, and the first world's class checks the other
    direction.
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


@pytest.fixture()
def restore_registry():
    """Undo any world a test registers.

    The build pass reads the registry by name, so a world a test registered
    and left behind would be built by every later pass that named it.
    """
    builders = dict(seeded_states._SEEDED_STATE_BUILDERS)
    yield
    seeded_states._SEEDED_STATE_BUILDERS.clear()
    seeded_states._SEEDED_STATE_BUILDERS.update(builders)


@pytest.mark.usefixtures("restore_registry")
class TestTheSeamRefusesWhatItCannotDeliver:
    """The registry's own preconditions, which fire at import time."""

    def test_two_worlds_may_not_share_one_name(self):
        """One name is one world: two would share one snapshot database."""
        register_seeded_state("x_be_2_dupe", lambda db: {})
        with pytest.raises(ValueError, match="already registered"):
            register_seeded_state("x_be_2_dupe", lambda db: {})

    def test_registering_the_same_builder_twice_is_not_a_conflict(self):
        """Re-import must not raise: a module can be imported more than once."""
        def builder(db):
            return {}

        register_seeded_state("x_be_2_same", builder)
        register_seeded_state("x_be_2_same", builder)

    def test_a_name_too_long_to_survive_postgres_is_refused(self):
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

    def test_the_refusal_is_WIRED_into_the_build(self, app, db):
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


#: Two worlds the pass tests register, named so they sort ``_a`` before ``_z``.
_PASS_FIRST = "x_be_2_pass_a"
_PASS_SECOND = "x_be_2_pass_z"


def _register_counting_worlds(builds, second=None):
    """Register the pass tests' two worlds, each recording its name in *builds*.

    Args:
        builds: The list each build appends its world's name to.
        second: The second world's builder, when it must do more than record.

    Returns:
        The collected tests the pass reads: two declaring the first world, one
        the second, and one declaring none.
    """
    def recorded(name):
        def build(_db):
            builds.append(name)
            return {}
        return build

    register_seeded_state(_PASS_FIRST, recorded(_PASS_FIRST))
    register_seeded_state(_PASS_SECOND, second or recorded(_PASS_SECOND))
    return [
        _CollectedTest("t.py::a1", pytest.mark.seeded_start_state(_PASS_FIRST)),
        _CollectedTest("t.py::a2", pytest.mark.seeded_start_state(_PASS_FIRST)),
        _CollectedTest("t.py::z", pytest.mark.seeded_start_state(_PASS_SECOND)),
        _CollectedTest("t.py::none"),
    ]


@pytest.mark.usefixtures("restore_registry")
class TestTheOnePass:
    """The list of a run's worlds and the pass that builds it, the build's only caller.

    **The pass tests release the engine first, and must**, for the reason
    :meth:`TestTheSeamRefusesWhatItCannotDeliver.test_the_refusal_is_WIRED_into_the_build`
    gives: a build drops the worker database this test is holding.
    """

    def test_the_pass_runs_once_per_worker(self):
        """The fixture that runs the pass is session-scoped: once per worker process.

        The one word "built once" rests on, beside the list the next arm
        grades.  Read from the source because no test can watch a session's
        cache without depending on which tests ran before it in its process,
        and pytest keeps a fixture's scope only in private attributes.  Measured
        2026-11-13: with the fixture made function-scoped, every other arm in
        this file stayed green, the run merely slower.
        """
        tree = ast.parse(Path(app_fixtures.__file__).read_text(encoding="utf-8"))
        fixture = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_seeded_snapshots"
        )
        scopes = [
            keyword.value.value
            for decorator in fixture.decorator_list if isinstance(decorator, ast.Call)
            for keyword in decorator.keywords if keyword.arg == "scope"
        ]
        assert scopes == ["session"], (
            f"_seeded_snapshots is declared with scope {scopes}, not 'session': "
            f"the pass would build every declared world again for each test "
            f"that declares one"
        )

    def test_each_declared_world_is_named_once(self):
        """Two tests declaring one world name it once; a test declaring none, nothing."""
        items = _register_counting_worlds([])
        assert seeded_states.declared_worlds(items) == [_PASS_FIRST, _PASS_SECOND]

    def test_the_pass_builds_each_world_once_and_drops_every_snapshot(self, app, db):
        """Each declared world is built once, and no snapshot outlives the pass.

        A world holds rows dated from ``display_today()``, so a snapshot that
        outlived its session would be a start state no code describes --
        finding **N-385**'s shape.  The ``_seeded_snapshots`` fixture runs this
        pass, so its exit is the session's end.
        """
        builds = []
        items = _register_counting_worlds(builds)
        db.session.remove()
        db.engine.dispose()
        with seeded_states.built_worlds(items, app) as worlds:
            snapshots = [snapshot for snapshot, _ids in worlds.values()]
            assert builds == [_PASS_FIRST, _PASS_SECOND], (
                f"the pass built {builds} for two tests declaring {_PASS_FIRST} "
                f"and one declaring {_PASS_SECOND}: each world is built once"
            )
            assert sorted(worlds) == [_PASS_FIRST, _PASS_SECOND]
            assert all(map(worker_database.database_exists, snapshots))
        left = [name for name in snapshots if worker_database.database_exists(name)]
        assert not left, (
            f"the pass ended and left {left} on the cluster; their rows are "
            f"dated from a session that has ended"
        )

    def test_a_build_that_raises_leaves_no_snapshot(self, app, db):
        """The first world's snapshot is dropped when the second world's build raises."""
        def refuses(_db):
            raise RuntimeError("the second world's builder refuses")

        items = _register_counting_worlds([], second=refuses)
        db.session.remove()
        db.engine.dispose()
        with pytest.raises(RuntimeError, match="second world's builder refuses"):
            with seeded_states.built_worlds(items, app):
                pass
        first = seeded_states._seeded_snapshot_name(_PASS_FIRST)
        assert not worker_database.database_exists(first), (
            f"a pass whose second build raised left {first}, the first world's "
            f"snapshot, on the cluster with nothing left to drop it"
        )
