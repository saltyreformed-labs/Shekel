"""The one forged sign-in of the scripts here, and its proof that the app lets it in.

**Why forged.**  A script measuring a copy of production has no password for the
account it renders as, so it writes a signed-in session into a test client's
cookie rather than posting the login form: the keys Flask-Login reads
(``_user_id``, ``_fresh``, ``_id``) and the stamps a sign-in writes
(:func:`~app.utils.session_helpers.login_session_stamps`), without which
``load_user`` refuses the session (plan#2173).  Its caller switches strong
session protection off first, since strong protection refuses a session whose
identifier was not minted inside a request.

**Why proven** (ruling plan#3804).  The app decides per request whether a session
gets past its sign-in rules, and a forged one can be turned away like any other.
Since every account must have two-factor (plan#3452), an account without it is
sent to ``/mfa/setup`` from every page (``app/login_gate.py``).  Measured
2026-11-13 (plan#3602) on a production copy whose owner had no two-factor, which
is how dev copies were made before that card: ``verify_grid_cells.py`` exited 0
reporting no cells and ``verify_liability_screens.py`` exited 0 reporting no
server errors, every page each saved being that redirect, so a before-and-after
diff on such a copy would read identical while measuring nothing.  So the forge
opens :data:`PROOF_PAGE` before it hands the client back, and a session the app
does not let in stops the script there, naming why and what to do.

It stops with :data:`~tests.manual.exit_status.NOTHING_MEASURED`, the status the
measurements here give for "nothing was measured", so no caller reads it as a
verdict of its own: ``verify_render_surfaces.py`` exits 1 for a page that did
not answer.

Every script here that forges a session calls :func:`signed_in_client`:
``door_rehearsal.Operator``, ``verify_grid_cells.py``,
``verify_liability_screens.py`` and ``verify_render_surfaces.py``.
"""

import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

from flask import Flask, url_for
from flask.testing import FlaskClient

from app.utils.session_helpers import login_session_stamps
from tests.manual.exit_status import NOTHING_MEASURED

#: The page the forge proves itself on: every role's own Security page (ruling
#: plan#3435), behind the sign-in and behind the two-factor hold, so it
#: answers 200 only to a session the app lets all the way in.
PROOF_PAGE = "/security"


def signed_in_client(app: Flask, user_id: int) -> FlaskClient:
    """Return a test client signed in as *user_id*, proven to reach a signed-in page.

    Args:
        app: The Flask application, its strong session protection off.
        user_id: The ``auth.users.id`` to sign in as.

    Returns:
        The test client, carrying the session.

    Raises:
        SystemExit: With :data:`~tests.manual.exit_status.NOTHING_MEASURED`,
            after printing why to stderr, when :data:`PROOF_PAGE` does not
            answer 200: the account is held at two-factor setup, the app
            refused the session, or the page answered something else.
    """
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        session["_id"] = None
        session.update(login_session_stamps(datetime.now(timezone.utc), user_id))
    answer = client.get(PROOF_PAGE)
    if answer.status_code == 200:
        return client
    print(_why_not_signed_in(app, user_id, answer), file=sys.stderr)
    raise SystemExit(NOTHING_MEASURED)


def _why_not_signed_in(app: Flask, user_id: int, answer) -> str:
    """Say why the forged session of *user_id* did not reach :data:`PROOF_PAGE`.

    Args:
        app: The Flask application.
        user_id: The user the session was forged for.
        answer: The response :data:`PROOF_PAGE` gave.

    Returns:
        The sentence, naming where the app sent the session and the fix.
    """
    with app.test_request_context():
        setup, login = url_for("auth.mfa_setup"), url_for("auth.login")
    sent_to = urlsplit(answer.headers.get("Location", "")).path
    if sent_to == setup:
        return (
            f"User {user_id} has no two-factor, so the app sends them to {setup} "
            "from every page, and every page this script opens would be that "
            "redirect.  On a dev copy, give the account the dev key first: "
            "python -m tools.dev_two_factor <its email>.  Nothing was measured."
        )
    if sent_to == login:
        return (
            f"The app refused the forged session of user {user_id}, sending it to "
            f"{login}: an unknown or deactivated user, or strong session "
            "protection left on (the forge's caller switches it off).  Nothing "
            "was measured."
        )
    return (
        f"{PROOF_PAGE} answered {answer.status_code} to the forged session of "
        f"user {user_id}, not the page.  Nothing was measured."
    )
