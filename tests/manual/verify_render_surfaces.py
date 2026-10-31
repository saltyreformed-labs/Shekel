"""GET every authenticated surface against a real database, for a HEAD diff.

**The fourth harness, and it is here because the other three are blind to the
same thing.**  ``verify_balance_baseline.py`` reads the balance seam,
``verify_savings_producers.py`` reads the producer packages above it, and
``verify_anchor_surfaces.py`` reads the anchor-derived figures either misses.
All three call PRODUCERS.  None of them renders a ROUTE, so none can see a
change that leaves every figure identical and still breaks the page -- a
template reading a key a view stopped publishing, a partial handed a value of
the wrong shape, a route whose own precondition now raises.

It answers "does every page still answer, and with a body of the same size",
never "is the figure right".  A byte count is a coarse instrument on purpose:
it is a REGRESSION check, and the figures are the other three harnesses' job.
Two identical byte counts can both be wrong.

Run it before a change and after, and diff the two JSON files::

    DATABASE_URL=postgresql://.../shekel \\
        python tests/manual/verify_render_surfaces.py /tmp/before.json
    # ... make the change ...
    DATABASE_URL=postgresql://.../shekel \\
        python tests/manual/verify_render_surfaces.py /tmp/after.json
    diff /tmp/before.json /tmp/after.json

A ``404`` is recorded rather than treated as a failure: most per-kind account
routes 404 for an account of another kind by design, so the DIFF is the signal
and an absolute status is not.

It drives the FIRST user in the database, which on a production clone is the
real owner; a companion role holds no accounts and would measure nothing.
"""

import json
import pathlib
import sys
from datetime import datetime, timezone

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so the repository root is added explicitly -- the same line every
# harness in this directory carries.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these imports must FOLLOW the
# ``sys.path`` line above, which is what makes this checkout's ``app`` the one
# imported at all.
# pylint: disable=wrong-import-position
from app import create_app  # noqa: E402
from app.extensions import db, login_manager  # noqa: E402
from app.models.account import Account  # noqa: E402
from app.models.user import User  # noqa: E402
from app.utils.session_helpers import login_session_stamps  # noqa: E402
# pylint: enable=wrong-import-position

#: The whole-page and self-refresh surfaces that read a per-period balance.
ROUTES = [
    "/grid",
    "/grid/balance-row",
    "/grid/subtotal-rows",
    "/grid/this-period-summary",
    "/dashboard",
    "/savings",
    "/accounts",
    "/retirement",
    "/analytics",
]

#: The per-account surfaces, formatted with each account id in turn.  A route
#: that does not apply to an account's kind answers 404 by design.
ACCOUNT_ROUTES = [
    "/accounts/{}/details",
    "/accounts/{}/details/band",
    "/accounts/{}/details/balance-hero",
    "/accounts/{}/checking",
    "/accounts/{}/interest",
    "/accounts/{}/investment",
    "/accounts/{}/investment/growth-chart",
    "/accounts/{}/loan",
    "/accounts/{}/property",
    "/accounts/{}/balance-history",
    "/accounts/{}/reconcile",
]


def _probe(client, route):
    """Return the recorded shape of one GET.

    Args:
        client: The Flask test client, already carrying a session.
        route: The path to request.

    Returns:
        ``{"status": int, "bytes": int, "location": str | None}``.
    """
    response = client.get(route, follow_redirects=False)
    return {
        "status": response.status_code,
        "bytes": len(response.get_data()),
        "location": response.headers.get("Location"),
    }


def main(out_path):
    """Write the snapshot for every route to *out_path*.

    Args:
        out_path: Destination JSON path.
    """
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    # The probe FORGES a session rather than posting the login form, because it
    # has no password for the database it is pointed at, and strong protection
    # refuses a session whose identifier was not minted inside a request.
    # Disabling it is scoped to this process and touches nothing measured here:
    # every route below is a GET whose RENDER is the subject.
    login_manager.session_protection = None
    with app.app_context():
        user = db.session.query(User).order_by(User.id).first()
        if user is None:
            raise SystemExit("no user in this database; nothing to render")
        user_id = user.id
        account_ids = [
            row.id for row in
            db.session.query(Account).filter_by(user_id=user_id)
            .order_by(Account.id).all()
        ]
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        session["_id"] = None
        # The stamps a sign-in writes, the user's among them: ``load_user``
        # refuses a session without them (plan#2173).
        session.update(login_session_stamps(
            datetime.now(timezone.utc), user_id,
        ))
    snapshot = {route: _probe(client, route) for route in ROUTES}
    for account_id in account_ids:
        for template in ACCOUNT_ROUTES:
            route = template.format(account_id)
            snapshot[route] = _probe(client, route)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, indent=2, sort_keys=True)
    server_errors = {
        route: shape for route, shape in snapshot.items()
        if shape["status"] >= 500
    }
    print(
        f"wrote {out_path}: {len(snapshot)} routes, "
        f"{len(server_errors)} server errors"
    )
    for route, shape in sorted(server_errors.items()):
        print(f"  {shape['status']} {route}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(
            "usage: verify_render_surfaces.py OUT.json "
            "(DATABASE_URL selects the database)"
        )
    main(sys.argv[1])
