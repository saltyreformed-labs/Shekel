"""The side probe's boot (step X-cx's K1b-2): a side's app started as production's gunicorn
starts it -- ``run.py``'s ``create_app()``, the reference cache loaded eagerly -- on a copy, under
the pinned instant, and one query answered, so a rollback is known to come up on what the
release left (the K1b plan's item 1).

Run BY PATH under the side's venv, with the side's worktree as its directory, in the
environment :func:`tools.rehearsal.rows.side_environment` builds::

    <side venv>/bin/python <kit>/tools/rehearsal/side/boot.py <ISO 8601 instant>

``app`` IS THE SIDE'S.  Python made ``sys.path[0]`` this file's directory, which is the KIT's;
it becomes the worktree, as gunicorn puts its own directory first, before ``app`` is imported.
The clock is frozen as ``side/deploy.py`` freezes it (that module says what the pin reaches),
before the import, so ``app``'s import-time code reads it too.  It exits 0 once the query has
answered; a boot or a query that fails ends it non-zero, with its traceback.
"""
import datetime
import os
import sys

import time_machine

#: The query the boot proves its session with (the hand kit's, ``g1nomig.py``).
_QUERY = "SELECT count(*) FROM salary.pay_stubs"


def main() -> None:
    """Freeze the clock at ``sys.argv[1]``, then import the side's app, boot it and query once."""
    sys.path[0] = os.getcwd()
    with time_machine.travel(datetime.datetime.fromisoformat(sys.argv[1]), tick=False):
        # Pylint: ``import-outside-toplevel`` -- ``app`` must be the side's, which resolves only
        # once the line above has put its tree first on ``sys.path``, and its import-time code
        # must read the pinned clock.
        # pylint: disable=import-outside-toplevel
        from app import create_app
        from app.extensions import db
        flask_app = create_app()
        with flask_app.app_context():
            db.session.execute(db.text(_QUERY)).scalar_one()


if __name__ == "__main__":
    main()
