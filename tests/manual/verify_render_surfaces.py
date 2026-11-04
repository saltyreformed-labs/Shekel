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

**The two statements and the paycheck locks render here too** (plan task
plan#2773): the Statements pill's two partials, by an HTMX GET, over each
window kind and once per paycheck, and the pay-periods settings section, whose
chips show each paycheck's lock.  Their figures are
``verify_statement_baseline.py``'s; this one sees a statement page that stops
answering, which that one, calling the producers, cannot.

Run it before a change and after, and diff the two JSON files::

    DATABASE_URL=postgresql://.../shekel \\
        python tests/manual/verify_render_surfaces.py /tmp/before.json
    # ... make the change ...
    DATABASE_URL=postgresql://.../shekel \\
        python tests/manual/verify_render_surfaces.py /tmp/after.json
    diff /tmp/before.json /tmp/after.json

A ``404`` is recorded rather than treated as a failure: most per-kind account
routes 404 for an account of another kind by design, so the DIFF is the signal
and an absolute status is not.  A server error is the exception, and so is a
statement partial answering anything but ``200``: either is printed and the
exit status is 1, so a sweep that rendered nothing cannot pass as a diff of
two equal sizes (the snapshot is written either way).

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
from app.extensions import db, limiter, login_manager  # noqa: E402
from app.models.account import Account  # noqa: E402
from app.models.pay_period import PayPeriod  # noqa: E402
from app.models.user import User  # noqa: E402
from app.utils.session_helpers import login_session_stamps  # noqa: E402
# pylint: enable=wrong-import-position

#: The whole-page and self-refresh surfaces that read a per-period balance, and
#: the pay-periods settings section, whose chips render each paycheck's lock.
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
    "/settings?section=pay-periods",
]

#: The two statements, requested as the Statements pill requests them: an
#: HTMX GET, since a plain one answers the analytics shell and renders no
#: statement (D13).  The income statement's three window kinds at their
#: defaults (the current paycheck, month and year), and the balance sheet as
#: of today and as of the route's floor, before any ledger line: its empty
#: state.
STATEMENT_ROUTES = [
    "/analytics/income-statement",
    "/analytics/income-statement?window=month",
    "/analytics/income-statement?window=year",
    "/analytics/balance-sheet",
    "/analytics/balance-sheet?as_of=2000-01-01",
]

#: The statements once per paycheck, formatted with each pay period's id and
#: first day in turn: the income statement over that paycheck, and the balance
#: sheet as of its first day.
PERIOD_STATEMENT_ROUTES = [
    "/analytics/income-statement?window=pay_period&period_id={period_id}",
    "/analytics/balance-sheet?as_of={start_date}",
]

#: The header an HTMX request carries, and the prefix its snapshot key takes.
_HTMX = {"HX-Request": "true"}
_HTMX_KEY = "HX "

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


def _probe(client, route, headers=None):
    """Return the recorded shape of one GET.

    Args:
        client: The Flask test client, already carrying a session.
        route: The path to request.
        headers: Request headers, or ``None`` for none (:data:`_HTMX` for an
            HTMX partial).

    Returns:
        ``{"status": int, "bytes": int, "location": str | None}``.
    """
    response = client.get(route, headers=headers, follow_redirects=False)
    return {
        "status": response.status_code,
        "bytes": len(response.get_data()),
        "location": response.headers.get("Location"),
    }


def _statement_routes(periods):
    """Return every statement route to request: the fixed ones, then each paycheck's.

    Args:
        periods: The owner's pay periods, each ``{"period_id": int,
            "start_date": ISO date}``, by first day.
    """
    return STATEMENT_ROUTES + [
        template.format(**period)
        for period in periods for template in PERIOD_STATEMENT_ROUTES
    ]


def _unanswered(snapshot):
    """Return a line for each probe that did not answer, by route.

    A server error on any route, and a statement partial answering anything
    but 200: a partial that redirects or refuses rendered no statement, so its
    size measures nothing.

    Args:
        snapshot: Each route's recorded shape, by route.
    """
    return [
        f"  {shape['status']} {route}"
        for route, shape in sorted(snapshot.items())
        if shape["status"] >= 500
        or (route.startswith(_HTMX_KEY) and shape["status"] != 200)
    ]


def main(out_path):
    """Write the snapshot for every route to *out_path*; the exit status.

    Args:
        out_path: Destination JSON path.

    Returns:
        1 when a probe did not answer (:func:`_unanswered`, each printed),
        else 0.  The snapshot is written either way.
    """
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    # The probe FORGES a session rather than posting the login form, because it
    # has no password for the database it is pointed at, and strong protection
    # refuses a session whose identifier was not minted inside a request.
    # Disabling it is scoped to this process and touches nothing measured here:
    # every route below is a GET whose RENDER is the subject.
    login_manager.session_protection = None
    # The rate limiter is switched off on the same terms.  A sweep of every
    # page from one address is far past production's default limit (30 a
    # minute), and a production configuration whose store cannot be reached
    # fails closed: under the release kit's (``tools.rehearsal.rows``) every
    # GET answered a 500 before its view ran (plan task plan#2773's first
    # run, 259 of 259 on both sides, byte-identical).
    limiter.enabled = False
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
        periods = [
            {"period_id": row.id, "start_date": row.start_date.isoformat()}
            for row in db.session.query(PayPeriod).filter_by(user_id=user_id)
            .order_by(PayPeriod.start_date).all()
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
    for route in _statement_routes(periods):
        snapshot[_HTMX_KEY + route] = _probe(client, route, _HTMX)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, indent=2, sort_keys=True)
    unanswered = _unanswered(snapshot)
    print(
        f"wrote {out_path}: {len(snapshot)} routes, "
        f"{len(unanswered)} unanswered"
    )
    for line in unanswered:
        print(line)
    return 1 if unanswered else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(
            "usage: verify_render_surfaces.py OUT.json "
            "(DATABASE_URL selects the database)"
        )
    sys.exit(main(sys.argv[1]))
