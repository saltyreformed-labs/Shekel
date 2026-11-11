"""
Shekel Budget App -- a /retirement rail Save that loses a version race (task plan#3126).

The rail's end-year Save builds its read pass before it writes, so the
regeneration's first flush, where the raise's version-pinned UPDATE runs, is
inside ``salary_regeneration``'s ``try``.  A lost race there is the route's
designed conflict: the rail re-rendered fresh at 409 with the conflict on the
row, logged by the route as a conflict at INFO.  It is not a failure of the
regeneration, so the service logs it at INFO too, with the table that lost,
and nothing is logged as an error.

``test_retirement.TestTheRailSavesARaisesEndYear``'s own race case replaces the
whole regeneration with a stub that raises, so it never reaches the service's
handlers.  The case here loses a real race inside the real walk: it bumps the
raise's version as the walk opens its schedule, after the rail has staged the
end year and before anything flushes it.  It lives in its own file because
``test_retirement.py`` is past 1,000 lines; the seeding and reading helpers
are that class's, imported from the helper class it inherits them from
(``_TestTheRailSavesARaisesEndYearMembers``), so no test class is collected
twice.
"""

import logging
from unittest.mock import patch

import sqlalchemy

from app.services.generation_schedule import GenerationSchedule
from tests.test_routes.test_retirement import (
    _TestTheRailSavesARaisesEndYearMembers as _SAVES,
)


def _a_race_lost_in_the_walk(session, raise_id: int):
    """A patch of ``GenerationSchedule.for_pass`` that loses *raise_id*'s race first.

    Another tab's save of the raise, planted by bumping its version with
    autoflush held off, lands as the walk opens its schedule: after the rail
    has staged its write and before anything flushes it.

    Args:
        session: The request's session (``db.session``).
        raise_id: The raise whose version moves.

    Returns:
        The ``patch.object`` context manager; entered, it is the mock, so its
        ``call_count`` says how often the walk opened a schedule.
    """
    real_for_pass = GenerationSchedule.for_pass

    def lose_the_race_then_schedule(ctx):
        with session.no_autoflush:
            session.execute(sqlalchemy.text(
                "UPDATE salary.salary_raises SET version_id = version_id + 1 "
                "WHERE id = :id"
            ), {"id": raise_id})
        return real_for_pass(ctx)

    return patch.object(
        GenerationSchedule, "for_pass", side_effect=lose_the_race_then_schedule,
    )


class TestTheRailsLostRace:
    """A version race lost inside the regeneration is the rail's 409, logged at INFO."""

    def test_a_raise_changed_inside_the_walk_is_a_409_and_no_error(
        self, auth_client, seed_user, db, seed_periods_today, caplog,
    ):
        """Another tab's save lands as the walk opens its schedule: 409, nothing saved.

        The rail stages the end year on the raise; the walk's first flush
        runs its UPDATE pinned to the version the rail read, which now
        matches no row, so ``StaleDataError`` is raised inside the
        regeneration's ``try``.  It reaches the route as itself (the service
        read ``profile.id`` off the rolled-back session in its handler until
        task plan#3106, and raised ``PendingRollbackError``, which the route
        answered as its designed 500).  The service logs it at INFO with the
        error's message, which names ``salary_raises`` as the table that lost,
        and nothing is logged at ERROR for a designed conflict (task
        plan#3126).
        """
        # Pylint: protected-access -- the helpers are the rail-save class's
        # own, shared rather than spelled a second time.
        # pylint: disable=protected-access
        raise_id, effective, _ = _SAVES._seed_saveable_raise(
            seed_user, db, seed_periods_today,
        )
        before = _SAVES._stored(db, raise_id)

        with caplog.at_level(logging.INFO, logger="app"), _a_race_lost_in_the_walk(
            db.session, raise_id,
        ) as for_pass:
            resp = _SAVES._save(auth_client, raise_id, "year", str(effective + 2))

        # The one call is the walk's: the rail opens no schedule elsewhere,
        # and the race ends the walk at its first flush.
        assert for_pass.call_count == 1
        assert resp.status_code == 409
        html = resp.data.decode()
        assert 'id="assumptions-panel"' in html
        row = _SAVES._row(html, raise_id)
        assert "This raise was changed by another action while you were editing." in row
        assert "Failed to save this end year." not in row
        assert _SAVES._stored(db, raise_id) == before, "the write survived the rollback"
        assert [
            record.getMessage() for record in caplog.records
            if record.levelno == logging.INFO
            and record.getMessage().startswith("Stale-data conflict")
        ] == [f"Stale-data conflict on update_settings raise ids=[{raise_id}]"], (
            "the route did not log its conflict once"
        )
        assert [
            "'salary_raises'" in record.getMessage() for record in caplog.records
            if record.levelno == logging.INFO
            and record.getMessage().startswith("Lost a version race")
        ] == [True], "the service did not log the raise's lost race once at INFO"
        assert not [
            record.getMessage() for record in caplog.records
            if record.levelno >= logging.ERROR
        ], "a designed conflict was logged as an error"
