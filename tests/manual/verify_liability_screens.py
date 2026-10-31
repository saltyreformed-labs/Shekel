"""Save the screens that show a liability figure, for a BYTE-for-byte HEAD diff.

Written at plan step ``credit_card:CC-5-5a`` (ruling **R-CC50**, amended by
**R-CC52**) to grade ``CC-5-5c``, the one-sign flip (ruling **R-CC47**), and the
liability doors of ``CC-5-5b`` before it: a configured loan's balance
moves from the owed sign to the held sign every other account already reports,
and every reader that shows what a liability OWES moves onto the one ``owed``
flip.  The ruling's own claim is "production's screens do not change", and this
file is what grades it.

**Why a fifth render harness.**  ``verify_render_surfaces.py`` records a status
and a BYTE COUNT per route, which is blind to exactly this change: a figure
whose sign flips from ``$176,719.77`` to ``-$176,719.77`` changes the length
by one byte and a class toggle can change it by none.  ``verify_savings_producers.py``
dumps the producers' RECORDS, and those are meant to change at CC-5-5c (a loan
projection's ``current_balance`` becomes the held figure), so its diff cannot
say whether a SCREEN moved.  This saves the rendered BODY of every surface, so
``diff -r`` names the exact line that moved.

**What it renders**, for the first user in the database (the real owner on a
production clone).  The list is a CENSUS, not a proof of completeness: the
routes found at CC-5-5a to render a liability or loan figure, widened by that
step's adversarial review.  A screen added later is invisible until it is
listed here.

* the whole-page and fragment GETs that show a liability figure -- ``/savings``
  and its cockpit fragment, every account's cockpit balance tile, ``/dashboard``,
  ``/accounts``, ``/debt-strategy``, the Taxes tab (a mortgage's interest) and
  the Balance Sheet (the posted ledger's Liabilities section) -- and the
  account CREATE form, whose "Opening Balance" is the first door plan step
  CC-5-5b changes;
* every per-account surface ``verify_render_surfaces.py`` probes, for every
  account (a route that does not apply to an account's kind answers 404 by
  design, recorded rather than treated as a failure), plus the loan page's
  balance hero and schedule, and the four forms that PRE-FILL a balance a
  write door then records -- the loan true-up form, the cash anchor form and
  display, and the edit page's books-opening card (the reviews' finding: a
  re-signed pre-fill is a wrong assertion one click away, and no figure-only
  harness sees it; CC-5-5b must move these for a LIABILITY and leave them
  byte-identical for an asset);
* the GRID of every liability with no amortization schedule (a card, a custom
  liability), the surface the anchor editor opens from beside the held balance
  its rows are summed in -- added at plan step CC-5-5b (ruling R-CC57);
* the read-only POST calculators that price a loan's owed balance -- the payoff
  calculator in both modes and the refinance comparison for every loan, and the
  debt strategy in both orders.  None of them writes: each validates its form
  and renders a result.

**Normalized** only where a render is not a function of the data: the CSRF
token (minted per session) is replaced with a fixed placeholder.  Anything else
that differs between two runs on one tree is a defect in this instrument, and
the procedure below checks for it.

**It records WHICH TREE it rendered**, in ``tree.json`` beside the bodies: the
imported ``app`` package's path, that checkout's ``git`` HEAD, whether its
``app/`` differs from HEAD, and a SHA-256 digest of every file under that
``app/`` as it sits on disk.  The script puts its OWN checkout first on
``sys.path``, so a ``PYTHONPATH`` pointing elsewhere is ignored, and a baseline
taken by running the wrong copy renders the AFTER tree on both sides --
``diff -r`` then reads byte-identical while grading nothing.  The DIGEST is
what catches that: a path or a HEAD can differ between two copies of the same
code, and the digest cannot.

**Procedure** (the clocks matter: the pages read the display day and the debt
strategy reads the server's ``date.today()``, which is UTC, so both sides must
run on the same display day AND the same UTC day -- avoid 20:00-24:00 EDT --
against the same database)::

    # 1. Baseline on the parent commit, from a worktree (never git checkout);
    #    run the BASE worktree's own copy (copy this file in if the parent
    #    predates it):
    git worktree add /tmp/wt-base <parent-sha>
    DATABASE_URL=postgresql://.../<clone> \\
        python /tmp/wt-base/tests/manual/verify_liability_screens.py /tmp/base
    # 2. A SECOND baseline run must be identical -- the instrument's own noise:
    ... verify_liability_screens.py /tmp/base2 && diff -r /tmp/base /tmp/base2
    # 3. The change under test, same days, same database:
    DATABASE_URL=... python tests/manual/verify_liability_screens.py /tmp/after
    diff -r -x tree.json /tmp/base /tmp/after
    # 4. Read both tree.json files: their ``app_digest`` values must DIFFER.

A development ``create_app()`` runs its idempotent startup DDL (``CREATE SCHEMA
IF NOT EXISTS``) against the database, so point it at a throwaway clone; what
"never writes" claims below is the calculator POSTs, not the app's startup.

It answers "did any screen move", never "is the figure right": two identical
screens can both be wrong.  The step's proof is its firing controls and its
hand-computed oracles; this is the regression check beside them.
"""

import hashlib
import json
import pathlib
import re
import subprocess
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
import app as app_package  # noqa: E402
from app import create_app  # noqa: E402
from app.extensions import db, login_manager  # noqa: E402
from app.models.account import Account  # noqa: E402
from app.models.loan_params import LoanParams  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.account_category import is_liability_account  # noqa: E402
from app.utils.session_helpers import login_session_stamps  # noqa: E402
# pylint: enable=wrong-import-position

#: The whole-page and fragment GETs that render a liability figure.
ROUTES = [
    "/savings",
    "/savings/cockpit",
    "/dashboard",
    "/accounts",
    "/debt-strategy",
    "/analytics/taxes",
    "/analytics/balance-sheet",
    "/accounts/new",
]

#: The GRID of every account that is a liability WITHOUT an amortization
#: schedule -- a card, a custom liability -- which is where the anchor editor
#: opens from beside the held balance its rows are summed in (plan step
#: credit_card:CC-5-5b, ruling R-CC57; the CC-5-5a tick review's L6).  A loan's
#: grid is refused (``resolve_grid_account``'s amortizing gate), so it is not
#: listed; production held no such account on 2026-09-22, so this adds no
#: response there and grades a clone that carries one.
LIABILITY_GRID_ROUTE = "/grid?account_id={}"

#: The per-account GETs, formatted with each account id in turn: the cockpit
#: tile, every surface ``verify_render_surfaces.py`` probes, the loan page's
#: balance hero and schedule, and the four forms that pre-fill a balance a
#: write door records.
ACCOUNT_ROUTES = [
    "/savings/cockpit/{}/balance",
    "/accounts/{}/details",
    "/accounts/{}/details/band",
    "/accounts/{}/details/balance-hero",
    "/accounts/{}/checking",
    "/accounts/{}/interest",
    "/accounts/{}/investment",
    "/accounts/{}/investment/growth-chart",
    "/accounts/{}/loan",
    "/accounts/{}/loan/balance-hero",
    "/accounts/{}/loan/schedule",
    "/accounts/{}/loan/anchor-form",
    "/accounts/{}/anchor-form",
    "/accounts/{}/anchor-display",
    "/accounts/{}/edit",
    "/accounts/{}/property",
    "/accounts/{}/balance-history",
    "/accounts/{}/reconcile",
]

#: The routes that render their figures only for an HTMX request -- a plain GET
#: is REDIRECTED to the page (or, for an analytics tab, answered with the empty
#: shell) -- so they are requested with ``HX-Request`` or they would measure
#: the redirect.  Matched against the route TEMPLATE (``{}`` for the id).
HTMX_ONLY = frozenset({
    "/savings/cockpit",
    "/savings/cockpit/{}/balance",
    "/accounts/{}/details/band",
    "/accounts/{}/details/balance-hero",
    "/accounts/{}/investment/growth-chart",
    "/accounts/{}/loan/balance-hero",
    "/accounts/{}/loan/anchor-form",
    "/analytics/taxes",
    "/analytics/balance-sheet",
})

#: The read-only loan calculators, posted for every configured loan.  The
#: figures are representative, not special: what is graded is that the same
#: inputs render the same page on both trees.
LOAN_POSTS = [
    ("/accounts/{}/loan/payoff", {"mode": "extra_payment", "extra_monthly": "200.00"}),
    ("/accounts/{}/loan/payoff", {"mode": "target_date", "target_date": "2030-01-01"}),
    ("/accounts/{}/loan/refinance", {
        "new_rate": "5.000", "new_term_months": "240", "closing_costs": "3000.00",
    }),
]

#: The debt strategy's two orders over the owner's loans.
DEBT_STRATEGY_POSTS = [
    ("/debt-strategy/calculate", {"strategy": "avalanche", "extra_monthly": "300.00"}),
    ("/debt-strategy/calculate", {"strategy": "snowball", "extra_monthly": "300.00"}),
]

#: The per-session CSRF token, in both places ``base.html`` renders it.
_CSRF = re.compile(r'(name="csrf_token" value="|name="csrf-token" content=")[^"]*"')

#: The header an HTMX request carries.
_HX = {"HX-Request": "true"}


def _headers(template):
    """Return the request headers for a route template.

    Args:
        template: The route, or its ``{}`` template for a per-account route.

    Returns:
        ``HX-Request`` for an HTMX-only route, else no headers.
    """
    return _HX if template in HTMX_ONLY else {}


def _normalize(body):
    """Replace the per-session CSRF token with a fixed placeholder.

    Args:
        body: The response body, decoded.

    Returns:
        The body with every CSRF token value replaced.
    """
    return _CSRF.sub(r'\1CSRF"', body)


def _file_name(method, route, form):
    """Return a stable file name for one request.

    Args:
        method: ``"GET"`` or ``"POST"``.
        route: The request path.
        form: The posted form, or ``None`` for a GET.

    Returns:
        A file name unique to the request.
    """
    stem = route.strip("/").replace("/", "__") or "root"
    if form is not None:
        stem += "__" + "_".join(f"{k}-{v}" for k, v in sorted(form.items()))
    return f"{method}__{stem}.html"


class _Snapshot:
    """One run's output directory and the index of every response saved in it."""

    def __init__(self, out_dir):
        """Hold *out_dir* and start an empty index.

        Args:
            out_dir: The output directory (already created).
        """
        self.out_dir = out_dir
        self.index = {}

    def save(self, method, route, response, form=None):
        """Write one response's normalized body and record its status.

        Args:
            method: ``"GET"`` or ``"POST"``.
            route: The request path.
            response: The test client's response.
            form: The posted form, or ``None`` for a GET.
        """
        name = _file_name(method, route, form)
        (self.out_dir / name).write_text(
            _normalize(response.get_data(as_text=True)), encoding="utf-8",
        )
        self.index[name] = {
            "route": route,
            "status": response.status_code,
            "location": response.headers.get("Location"),
        }

    def write_index(self):
        """Write ``index.json``: every saved file's route, status and location."""
        (self.out_dir / "index.json").write_text(
            json.dumps(self.index, indent=2, sort_keys=True), encoding="utf-8",
        )

    def server_errors(self):
        """Return the saved responses that answered 5xx.

        Returns:
            ``{file name: index entry}`` for every status of 500 or above.
        """
        return {
            name: entry for name, entry in self.index.items()
            if entry["status"] >= 500
        }


def _app_digest(package_dir):
    """Return a SHA-256 digest of every file under the ``app`` package on disk.

    Args:
        package_dir: The imported ``app`` package's directory.

    Returns:
        The hex digest over each file's relative path and bytes, in path order,
        skipping ``__pycache__`` -- so two checkouts whose ``app/`` holds the same
        code digest equal whatever their paths and HEADs.
    """
    digest = hashlib.sha256()
    files = sorted(
        path for path in package_dir.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    for path in files:
        digest.update(str(path.relative_to(package_dir)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _tree_identity():
    """Return which checkout's ``app`` this process imported, and its state.

    Returns:
        ``{"app_package", "app_digest", "git_head", "app_dirty"}`` for the
        checkout that holds the imported ``app`` package; ``app_dirty`` is
        whether its ``app/`` differs from HEAD (the script itself, copied into a
        base worktree, does not count).
    """
    package_dir = pathlib.Path(app_package.__file__).resolve().parent
    checkout = package_dir.parent
    head = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(checkout), "status", "--porcelain", "--", "app"],
        capture_output=True, text=True, check=True,
    ).stdout
    return {
        "app_package": str(package_dir),
        "app_digest": _app_digest(package_dir),
        "git_head": head,
        "app_dirty": bool(status.strip()),
    }


def _owner_ids(app):
    """Return the first user's id, their account ids and their loan ids.

    Args:
        app: The Flask application.

    Returns:
        ``(user_id, account_ids, loan_ids, grid_liability_ids)`` -- the loans
        being the accounts that carry a ``LoanParams`` row, and the grid
        liabilities the liabilities with no amortization schedule
        (:data:`LIABILITY_GRID_ROUTE`), each list ordered by id.
    """
    with app.app_context():
        user = db.session.query(User).order_by(User.id).first()
        if user is None:
            raise SystemExit("no user in this database; nothing to render")
        account_ids = [
            row.id for row in
            db.session.query(Account).filter_by(user_id=user.id)
            .order_by(Account.id).all()
        ]
        loan_ids = [
            row.account_id for row in
            db.session.query(LoanParams)
            .filter(LoanParams.account_id.in_(account_ids))
            .order_by(LoanParams.account_id).all()
        ]
        grid_liability_ids = [
            row.id for row in
            db.session.query(Account).filter_by(user_id=user.id)
            .order_by(Account.id).all()
            if is_liability_account(row)
            and not row.account_type.has_amortization
        ]
        return user.id, account_ids, loan_ids, grid_liability_ids


def _forged_client(app, user_id):
    """Return a test client already logged in as *user_id*.

    The probe FORGES a session rather than posting the login form, because it
    has no password for the database it is pointed at, and strong protection
    refuses a session whose identifier was not minted inside a request -- the
    same scoping ``verify_render_surfaces.py`` states for its own forge.

    Args:
        app: The Flask application (session protection already disabled).
        user_id: The user to log in as.

    Returns:
        The logged-in test client.
    """
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
    return client


def _render(client, snapshot, owned):
    """Request every listed surface and save each response into *snapshot*.

    Args:
        client: The logged-in test client.
        snapshot: The run's :class:`_Snapshot`.
        owned: ``(account_ids, loan_ids, grid_liability_ids)`` from
            :func:`_owner_ids`: every account (the per-account GETs), the
            configured loans (the calculator POSTs) and the liabilities with no
            schedule (their grids).
    """
    account_ids, loan_ids, grid_liability_ids = owned
    for route in ROUTES:
        snapshot.save("GET", route, client.get(route, headers=_headers(route)))
    for account_id in grid_liability_ids:
        route = LIABILITY_GRID_ROUTE.format(account_id)
        snapshot.save("GET", route, client.get(route))
    for account_id in account_ids:
        for template in ACCOUNT_ROUTES:
            route = template.format(account_id)
            snapshot.save(
                "GET", route, client.get(route, headers=_headers(template)),
            )
    # The calculators are HTMX forms: requested as the page's own hx-post is.
    for loan_id in loan_ids:
        for template, form in LOAN_POSTS:
            route = template.format(loan_id)
            snapshot.save(
                "POST", route, client.post(route, data=form, headers=_HX), form,
            )
    for route, form in DEBT_STRATEGY_POSTS:
        snapshot.save(
            "POST", route, client.post(route, data=form, headers=_HX), form,
        )


def main(out_path):
    """Render every listed liability screen into the directory *out_path*.

    Args:
        out_path: Destination directory; created, and must not already hold
            a snapshot (a stale file would survive into the diff).
    """
    out_dir = pathlib.Path(out_path)
    out_dir.mkdir(parents=True, exist_ok=False)
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    login_manager.session_protection = None
    user_id, account_ids, loan_ids, grid_liability_ids = _owner_ids(app)
    snapshot = _Snapshot(out_dir)
    _render(
        _forged_client(app, user_id), snapshot,
        (account_ids, loan_ids, grid_liability_ids),
    )
    snapshot.write_index()
    identity = _tree_identity()
    (out_dir / "tree.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True), encoding="utf-8",
    )
    server_errors = snapshot.server_errors()
    print(
        f"wrote {out_dir}: {len(snapshot.index)} responses "
        f"({len(account_ids)} accounts, {len(loan_ids)} loans, "
        f"{len(grid_liability_ids)} liability grids), "
        f"{len(server_errors)} server errors; rendered {identity['app_package']} "
        f"at {identity['git_head'][:10]}"
        f"{' (app/ dirty)' if identity['app_dirty'] else ''}, "
        f"app digest {identity['app_digest'][:12]}"
    )
    for name, entry in sorted(server_errors.items()):
        print(f"  {entry['status']} {entry['route']} ({name})")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(
            "usage: verify_liability_screens.py OUT_DIR "
            "(DATABASE_URL selects the database)"
        )
    main(sys.argv[1])
