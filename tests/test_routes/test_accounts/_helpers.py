"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_accounts.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import text

from app.extensions import db
from tests._test_helpers import (
    figure_source_columns,
    generate_row_of,
    make_expense_template,
    open_owner_calendar,
    settle_day_columns,
)
from app.models.user import User, UserSettings
from app.models.ref import AccountType
from app.services.auth_service import hash_password


#: The out-of-band swap that carries the balance acknowledgement into
#: ``base.html``'s single mount.  Spelled once because three tests slice the
#: response on it: the swap STYLE is load-bearing, not incidental -- an
#: outerHTML swap of the mount destroys a still-visible predecessor, which is
#: finding N-206, so a test that matched only the mount NAME would keep passing
#: through the regression this constant exists to catch.
_ACK_SWAP = 'hx-swap-oob="beforeend:#anchor-ack-mount"'


def _create_other_user_account():
    """Create a second user with their own account.

    Pay periods are generated before the account so the canonical
    account factory (E-19, Commit 3) can resolve / receive an anchor
    period without raising ``ValidationError``.

    Returns:
        dict with keys: user, account.
    """
    from app.services import account_service  # pylint: disable=import-outside-toplevel

    other_user = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
    )
    db.session.add(other_user)
    db.session.flush()

    settings = UserSettings(user_id=other_user.id)
    db.session.add(settings)

    # The factory needs at least one period to anchor against.  Dated far
    # before any test's typical 2026 range so it does not collide with periods
    # generated later by the test body.
    # Through the writer that owns the table (plan step pay_calendar:C4-b-1).
    open_owner_calendar(other_user.id, date(2024, 1, 5))

    checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=other_user.id,
            account_type_id=checking_type.id,
            name="Other Checking",
            anchor_balance=Decimal("500.00"),
        ),
    )
    db.session.commit()

    return {"user": other_user, "account": account}


class _TestTheReconcileRouteMembers:
    """The reconcile step's two doors -- plan step S1-c, ruling R-DH (d) / 12.5.

    **This class covered a DELETED function.**  It graded
    ``entry_service.clear_entries_for_anchor_true_up``: a bulk ``UPDATE`` that
    flipped ``is_cleared = TRUE`` on every past-dated entry of a projected
    parent, fired as a side effect of every checking anchor true-up.  Whether a
    purchase counted as reconciled was therefore decided by the order two
    buttons were pressed -- record then true up and it cleared, true up then
    record and it never did -- which is 14 of the developer's 53 same-day
    entries.  The flag, the bulk update and its two log events are gone.

    What replaced it is an ASKED question rather than a guessed one: after a
    successful true-up the app lists the purchases it still thinks are
    outstanding and the user ticks the ones their statement shows
    (``GET`` / ``POST /accounts/<id>/reconcile``).  A tick stamps
    ``settled_on`` with the assertion's own ``observed_on`` -- an upper bound on
    the true posting day, and the only bound the reconciliation predicate
    consumes, so no answer changes by sharpening it.

    The per-account scoping case SURVIVES the re-ruling because the invariant
    it names is unchanged and still real: reconciling account A must not touch
    account B's purchases.  The exhaustive scoping matrix (another user, a
    credit purchase, a settled parent, an already-recorded entry) is graded at
    the service in ``test_services/test_reconcile_service/test_the_outstanding_set.py``; what is
    graded here is the ROUTE -- that it resolves the assertion day itself, refuses a
    non-owner, and commits.
    """

    def _make_grocery_txn_with_entries(
        self, seed_user, seed_periods_today, entries, account=None,
        name="Groceries",
    ):
        """Create a tracked grocery transaction with the given entries.

        Args:
            seed_user: seed_user fixture dict.
            seed_periods_today: list of PayPeriods.
            entries: list of ``(amount, purchased_on, is_credit, settled_on)``
                tuples.  ``settled_on`` is the day the bank was seen to take
                the purchase, or ``None`` for one not yet seen -- the fourth
                element was a ``is_cleared`` bool until plan step S1-c.
            account: the Account the template and transaction belong
                to.  Defaults to the seed_user's primary checking
                account; pass a second account to exercise the
                per-account scope of the reconcile.
            name: the envelope's name, on both the template and the row.
                Defaults to "Groceries"; pass a second name to exercise the
                per-envelope GROUPING (plan step X-f2-c1), which needs two
                distinguishable blocks in one panel.

        Returns:
            The Transaction object -- the engine's row of a priced envelope
            definition on *account* (:func:`generate_row_of`, plan step
            balance:X-cf-4).
        """
        from app.models.transaction_entry import TransactionEntry

        template = make_expense_template(
            db.session, seed_user, amount="500.00", name=name,
            category_key="Groceries", is_envelope=True, account=account,
        )
        txn = generate_row_of(template, seed_periods_today[0])

        for amount, purchased_on, is_credit, settled_on in entries:
            db.session.add(TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=seed_user["user"].id,
                amount=Decimal(amount),
                description="Test purchase",
                purchased_on=purchased_on,
                is_credit=is_credit,
                **settle_day_columns(settled_on),
            ))
        db.session.commit()
        return txn

    @staticmethod
    def _true_up(auth_client, account_id, balance, observed_on=None, revert=None):
        """Assert *balance* through the real PATCH route.

        The reconcile route reads the account's LATEST asserted day and stamps
        it, so the fixtures true up first rather than hand-writing a history
        row: that is the production sequence (read your bank balance, enter it,
        then tick off what it contained) and it is the sequence whose ORDER the
        retired flag got wrong.

        Args:
            auth_client: the authenticated client.
            account_id: the account to assert about.
            balance: the balance string to submit.
            observed_on: an optional civil day to submit as the statement date
                (plan step X-f1c4c).  ``None`` submits the field not at all,
                which the write door reads as the user's today.
            revert: an optional surface token naming which of the five openers
                the editor was opened from.  ``None`` is the grid default.  It
                exists because the success response BRANCHES on it, and a
                caller that never passes one grades only the fall-through.
        """
        data = {"anchor_balance": balance}
        if observed_on is not None:
            data["observed_on"] = observed_on.isoformat()
        url = f"/accounts/{account_id}/true-up"
        if revert is not None:
            url = f"{url}?revert={revert}"
        response = auth_client.patch(url, data=data)
        assert response.status_code == 200
        return response

    @staticmethod
    def _entries_of(txn_id):
        """Return a transaction's entries, freshest read, ordered by id."""
        from app.models.transaction_entry import TransactionEntry

        db.session.expire_all()
        return (
            db.session.query(TransactionEntry)
            .filter_by(transaction_id=txn_id)
            .order_by(TransactionEntry.id)
            .all()
        )


# ── Commit 7: canonical entries-aware producer routing ─────────────
#
# Pre-Commit-7 the /accounts checking detail built its own transaction
# query without ``selectinload(Transaction.entries)`` (same shape as
# the savings tile pre-Commit-6) and forked on the NULL-anchor case
# differently from the grid.  The silent-degrade seam in
# ``cash_ledger._amounts._entry_aware_amount`` (closed at the math layer
# by Commit 5, structurally closed by the canonical producer in Commit
# 5) yielded $160.00 on the grid and $114.29 on /accounts for the
# audit's symptom #1 / #5 tuple.  Commit 7 routes the checking detail
# through the balance-at seam and resolves the anchor via
# the dated ``AccountAnchorHistory`` SoT, so both divergence axes
# (entries seam, NULL-anchor fork) close.  The NULL-anchor fork is
# dead post-Commit-3 and was deleted rather than left unreachable.


def _override_account_anchor(db_session, account, pay_period, anchor_balance):
    """Replace ``account``'s current anchor with the given balance + period.

    Appends a fresh :class:`AccountAnchorHistory` row (latest-wins by
    ``created_at``) and syncs the cache columns so the resolver's
    cache-reconciliation log does NOT fire.  Required because the
    ``seed_user`` factory writes an origination anchor of $1,000
    against the seed_periods anchor period; the Commit-7 symptom
    reproductions need $614.29 anchored to today's period.

    Mirrors ``tests/test_services/test_savings_dashboard_service/_helpers.py``'s
    ``_override_anchor`` (Commit 6).  Kept private to this module so
    later moves to a shared fixture remain a refactor rather than a
    contract change.
    """
    from tests._test_helpers import override_anchor  # pylint: disable=import-outside-toplevel

    override_anchor(
        db_session, account, pay_period, anchor_balance,
    )
    db_session.commit()


#: The civil day :func:`_add_cleared_debit_entry` buys and settles on.  Named
#: because an account's BOOKS have to precede it (ruling **R-HG**, plan step
#: X-f3c-2b) and two literals that must agree are two a caller can split -- it
#: was spelled twice inside the helper below and nowhere else.
_CLEARED_PURCHASE_DAY = date(2026, 1, 15)


def _add_cleared_debit_entry(db_session, *, txn, user_id, amount):
    """Add a CLEARED, DEBIT :class:`TransactionEntry` to ``txn``.

    These are the entries that produce the F-009 / CRIT-01 silent-
    degrade gap: pre-Commit-5 the entry-aware reduction would not run
    on consumers that did not eager-load entries, so the cleared
    amount (already in the anchor) would be implicitly subtracted a
    second time off the projection.
    """
    from app.models.transaction_entry import TransactionEntry  # pylint: disable=import-outside-toplevel

    db_session.add(TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=user_id,
        amount=amount,
        description="Cleared purchase",
        purchased_on=_CLEARED_PURCHASE_DAY,
        is_credit=False,
        **settle_day_columns(_CLEARED_PURCHASE_DAY),
    ))
    db_session.flush()


# ── Cash Detail: merged-page context contract (Fable 5 rebuild) ─────


def _capture_cash_detail_context(app, auth_client, account_id, *, setup=False):
    """Return the exact context ``cash_detail`` handed ``render_template``.

    Uses Flask's ``template_rendered`` signal so the test reads the route's
    context contract (the Decimals, the horizons list, the chart JSON,
    ``anchor_as_of``) WITHOUT parsing HTML -- the established chart / net-
    worth route-test pattern (see
    ``tests/test_routes/test_savings/test_the_dashboard.py::_capture_dashboard_context``).
    Asserts the cash-detail template rendered (a 200 page), so the helper
    fails loud on a redirect / 404 rather than reading a wrong context.
    """
    # Pylint: import-outside-toplevel -- deferred import is the file-wide
    # test convention.
    from flask import template_rendered  # pylint: disable=import-outside-toplevel

    recorded = []

    def _record(sender, template, context, **extra):
        recorded.append((template, context))

    template_rendered.connect(_record, app)
    try:
        url = f"/accounts/{account_id}/details"
        if setup:
            url += "?setup=1"
        response = auth_client.get(url)
    finally:
        template_rendered.disconnect(_record, app)

    assert response.status_code == 200, (
        f"GET {url} returned {response.status_code}; expected 200"
    )
    records = [
        c for t, c in recorded if t.name == "accounts/cash_detail.html"
    ]
    assert records, (
        "GET did not render accounts/cash_detail.html; rendered: "
        f"{[t.name for t, _ in recorded]!r}"
    )
    return records[0]


# ── Optimistic Locking (commit C-17 / F-009) ────────────────────────


def _bump_account_version_outside_session(account_id):
    """Simulate a concurrent commit by bumping ``version_id`` directly.

    Uses a fresh DB connection (NOT the test session) so the in-memory
    identity map of the calling session is unaffected.  After this
    helper returns, any object the caller previously loaded for
    ``account_id`` retains its old in-memory ``version_id`` while the
    database row carries the bumped value -- exactly the state a
    concurrent request from another browser tab would produce.

    The connection commit is essential: without it the UPDATE would
    sit in an open transaction and ``READ COMMITTED`` MVCC would
    hide the bump from the test session.
    """
    with db.engine.connect() as conn:
        conn.execute(
            text(
                "UPDATE budget.accounts "
                "SET version_id = version_id + 1 "
                "WHERE id = :id"
            ),
            {"id": account_id},
        )
        conn.commit()


    # ``test_true_up_cash_revert_skips_as_of_oob`` was DELETED at plan step
    # X-f1e3.  It is the ``cash-hero`` case of
    # ``TestTrueUp.test_the_as_of_snippet_goes_to_the_grid_and_only_the_grid``,
    # which grades all five openers in one parametrization -- keeping a third
    # copy is the duplication that parametrization removed.  Its second
    # assertion had also become false in principle: it read
    # ``'hx-swap-oob="true"' not in body``, meaning "this surface receives no
    # out-of-band fragment at all", and BOTH the reconcile prompt and the
    # back-dated acknowledgement now ride out-of-band on all five surfaces by
    # design.  It passed only because this fixture has nothing outstanding and
    # submits no ``observed_on``; the first purchase added to it would have
    # failed here and read as a regression in the new feature.


# ── Multi-Tenant Account Type Ownership (commit C-28 / F-044) ─────


def _login_as(app, email, password):
    """Build a fresh ``test_client`` and log it in as the given user.

    Wrapper around the well-known ``auth_client`` /
    ``second_auth_client`` cookie-interaction work-around documented
    in ``tests/test_integration/test_fixture_validation.py`` and
    re-applied in ``tests/test_routes/test_security_event_banner.py``.
    Each call returns an isolated client whose cookie jar is not
    cross-contaminated by any other client built earlier in the same
    test, which is the only reliable way to stage a two-owner
    interaction without one client's session leaking into the other.
    """
    client = app.test_client()
    resp = client.post("/login", data={"email": email, "password": password})
    assert resp.status_code == 302, (
        f"login as {email} failed; got status {resp.status_code}"
    )
    return client
