"""Account route tests: the version id column and its lifecycle, and every door's refusal of a
stale form.
"""

from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.services import account_service, cash_ledger

from ._helpers import _bump_account_version_outside_session


class TestAccountVersionIdColumn:
    """Schema-level invariants for the optimistic-lock counter."""

    def test_the_anchor_cache_columns_are_gone(self, app):
        """``budget.accounts`` carries NO anchor cache columns (ruling R-EH).

        **Asserted rather than merely deleted**, for the reason the sibling
        lock one table over gives (``test_c43_ondelete_and_naming_convention
        .test_account_anchor_history_has_no_pay_period_fk``): a future
        migration re-adding ``current_anchor_balance`` /
        ``current_anchor_period_id`` would re-open a denormalized second
        balance -- the state whose divergence this arc spent a step deleting --
        with nothing objecting, and "the tests stopped mentioning it" is not
        something a reader can distinguish from an accident.

        The CHECK and the deferrable FK that existed only for those columns are
        asserted gone in the same pass, because each is independently
        re-addable and each carried its own cost: the CHECK was redundant with
        a NOT NULL, and the FK's deferrability existed solely so the pay-period
        reset could re-point the column mid-transaction.
        """
        with app.app_context():
            insp = inspect(db.engine)
            cols = {
                c["name"] for c in insp.get_columns("accounts", schema="budget")
            }
            assert "current_anchor_balance" not in cols
            assert "current_anchor_period_id" not in cols

            checks = {
                c["name"]
                for c in insp.get_check_constraints("accounts", schema="budget")
            }
            assert "ck_accounts_anchor_balance_present" not in checks

            fks = {
                fk.get("name")
                for fk in insp.get_foreign_keys("accounts", schema="budget")
            }
            assert "accounts_current_anchor_period_id_fkey" not in fks

    def test_version_id_column_present_and_not_null(self, app):
        """The live ``budget.accounts`` table carries a NOT NULL ``version_id``."""
        with app.app_context():
            insp = inspect(db.engine)
            cols = {
                c["name"]: c
                for c in insp.get_columns("accounts", schema="budget")
            }

            assert "version_id" in cols, (
                "Account.version_id column is missing from the live "
                "schema -- migration 861a48e11960 may not have run."
            )
            assert cols["version_id"]["nullable"] is False, (
                "Account.version_id must be NOT NULL or the optimistic "
                "lock silently fails on rows that have a NULL counter."
            )

    def test_version_id_check_constraint_present(self, app):
        """The CHECK constraint that pins ``version_id > 0`` is on the live table."""
        with app.app_context():
            insp = inspect(db.engine)
            checks = {
                c["name"]: c["sqltext"]
                for c in insp.get_check_constraints(
                    "accounts", schema="budget",
                )
            }

            assert "ck_accounts_version_id_positive" in checks, (
                "ck_accounts_version_id_positive missing -- the schema "
                "no longer matches the model declaration."
            )
            # PostgreSQL normalises the predicate; either form is valid.
            normalised = checks["ck_accounts_version_id_positive"].lower().replace(" ", "")
            assert "version_id>0" in normalised, (
                "CHECK constraint expression has changed; rerun the "
                "migration or update the model in lockstep."
            )

    def test_version_id_check_rejects_zero(self, app, db, seed_user):
        """Inserting a row with ``version_id = 0`` raises IntegrityError.

        The application never sets ``version_id`` directly; this test
        exercises the database-tier guard against a future raw-SQL
        path or a buggy migration that writes 0.
        """
        with app.app_context():
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            with pytest.raises(IntegrityError):
                db.session.execute(
                    text(
                        "INSERT INTO budget.accounts "
                        "(user_id, account_type_id, name, version_id) "
                        "VALUES (:u, :t, :n, 0)"
                    ),
                    {
                        "u": seed_user["user"].id,
                        "t": checking_type.id,
                        "n": "Bad Version",
                    },
                )
                db.session.flush()
            db.session.rollback()

    def test_mapper_declares_version_id_col(self, app):
        """``Account.__mapper_args__`` exposes the version counter to SQLAlchemy.

        Without this declaration SQLAlchemy emits ``UPDATE`` without
        the ``WHERE version_id = ?`` narrowing and the optimistic-lock
        contract collapses; the rest of the test class would still pass
        but production would silently regress.
        """
        with app.app_context():
            mapper = inspect(Account)
            assert mapper.version_id_col is not None, (
                "Account mapper has no version_id_col -- "
                "__mapper_args__ regression."
            )
            assert mapper.version_id_col.name == "version_id"


class TestAccountVersionIdLifecycle:
    """End-to-end behaviour of the ``version_id`` counter through ORM operations."""

    def test_new_account_starts_at_version_one(self, app, auth_client, seed_user):
        """Newly created accounts have ``version_id == 1``.

        ``server_default='1'`` on the column guarantees this for rows
        inserted via SQLAlchemy with no explicit ``version_id``.  The
        seed_user fixture path exercises this exact code path.
        """
        with app.app_context():
            acct = db.session.get(Account, seed_user["account"].id)
            assert acct.version_id == 1

    def test_seed_user_account_starts_at_version_one(self, app, seed_user):
        """The seed fixture's account has ``version_id == 1`` after creation."""
        with app.app_context():
            acct = db.session.get(Account, seed_user["account"].id)
            assert acct.version_id == 1, (
                f"seed_user fixture account should start at version 1, "
                f"got {acct.version_id}"
            )

    def test_version_does_not_increment_on_read(self, app, db, seed_user):
        """Pure SELECT operations leave ``version_id`` unchanged.

        The optimistic-lock contract increments only on UPDATE/DELETE;
        a regression here would inflate the counter on every page
        view and turn every form submit into a stale-form 409.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            initial_version = db.session.get(Account, acct_id).version_id

            for _ in range(5):
                _ = db.session.get(Account, acct_id).name
                db.session.expire_all()

            final_version = db.session.get(Account, acct_id).version_id
            assert final_version == initial_version

    def test_version_increments_on_update(self, app, db, seed_user):
        """Each ORM-emitted UPDATE bumps ``version_id`` by exactly one."""
        with app.app_context():
            acct_id = seed_user["account"].id
            v0 = db.session.get(Account, acct_id).version_id

            acct = db.session.get(Account, acct_id)
            acct.name = "Renamed Once"
            db.session.commit()
            v1 = db.session.get(Account, acct_id).version_id

            acct.name = "Renamed Twice"
            db.session.commit()
            v2 = db.session.get(Account, acct_id).version_id

            assert v1 == v0 + 1
            assert v2 == v1 + 1


class TestAccountConcurrentMutationStaleData:
    """SQLAlchemy ``StaleDataError`` is raised on truly concurrent races."""

    def test_concurrent_update_raises_stale_data_error(
        self, app, db, seed_user,
    ):
        """A race that bumps the version between load and commit raises StaleDataError.

        The simulated concurrent commit advances the row to version 2;
        the test session, still holding an in-memory copy at version
        1, attempts an UPDATE -- the version-pinned WHERE matches no
        rows and SQLAlchemy raises ``StaleDataError``.  This is the
        load-bearing invariant that makes the SQLAlchemy tier of the
        optimistic lock work.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            acct = db.session.get(Account, acct_id)
            assert acct.version_id == 1

            _bump_account_version_outside_session(acct_id)

            # ANY mutable column proves the version pin -- the point is that
            # the ORM narrows the UPDATE by ``version_id``, not which field is
            # written.  It used to write ``current_anchor_balance``, deleted at
            # plan step X-f1c3c (ruling R-EH); ``name`` is the column the
            # account-edit form actually writes and the one whose door still
            # carries this lock.
            acct.name = "Renamed Under A Stale Version"

            with pytest.raises(StaleDataError):
                db.session.commit()

            db.session.rollback()

            db.session.expire_all()
            persisted = db.session.get(Account, acct_id)
            assert persisted.name != "Renamed Under A Stale Version"
            assert persisted.version_id == 2

    def test_concurrent_delete_raises_stale_data_error(
        self, app, db, seed_user,
    ):
        """DELETE also enforces the version pin; concurrent bump blocks the delete."""
        with app.app_context():
            checking_type = (
                db.session.query(AccountType)
                .filter_by(name="Checking").one()
            )
            spare = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Spare",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(spare)
            db.session.commit()
            spare_id = spare.id

            _bump_account_version_outside_session(spare_id)

            db.session.delete(spare)
            with pytest.raises(StaleDataError):
                db.session.commit()
            db.session.rollback()

            persisted = db.session.get(Account, spare_id)
            assert persisted is not None, (
                "Stale-data DELETE must leave the row intact for the "
                "winner of the race to handle."
            )


class TestTrueUpIsAppendOnly:
    """``true_up`` (PATCH /accounts/<id>/true-up) records, it does not contend.

    **This class graded the C-17 optimistic lock until plan step X-f1c3c**, in
    four cases: a matching ``version_id`` succeeds and bumps the counter, a
    stale one answers 409 with the conflict cell, a flush-time
    ``StaleDataError`` answers the same 409, and an omitted version falls
    through to the database check.  Ruling R-EN deleted all of it, because the
    path stopped writing the row the lock guarded -- a true-up APPENDS an
    assertion, so there is nothing for a second tab to overwrite.  The four
    cases are replaced by the one that states the new contract, plus the
    concurrent case in ``test_race_conditions.TestConcurrentAnchorUpdate``
    (both requests 200, both assertions recorded).
    """

    def test_a_stale_form_still_records_its_assertion(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """A true-up submitted against a stale row succeeds and is recorded.

        The direct inversion of ``test_true_up_returns_409_on_stale_version``.
        The account row is advanced out from under the form (simulating another
        tab having edited the account), and the true-up is submitted anyway.
        It must be accepted: what the user is declaring is what their bank
        holds, and that statement does not become false because some other
        field on the account changed.

        **The payload carries the stale ``version_id``, and that is the whole
        point of the test.**  A first version of it advanced the row and then
        submitted no version at all, which graded a form that never had the
        pin rather than the one R-EN actually makes tolerable: a page cached
        BEFORE the deploy, whose form still renders the hidden field.  That
        payload reaches ``AnchorUpdateSchema``, which no longer declares
        ``version_id`` -- so this also pins the ``unknown = EXCLUDE``
        backward-compatibility the deletion rests on.  A schema that started
        rejecting unknown fields would 400 here.

        Non-vacuity: the version is genuinely advanced first (asserted), the
        submitted pin is asserted to be the STALE one, and the assertion is
        read back out of ``account_anchor_history`` rather than off the
        response, so a 200 that recorded nothing fails here.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            before = db.session.get(Account, acct_id).version_id
            _bump_account_version_outside_session(acct_id)
            db.session.expire_all()
            current = db.session.get(Account, acct_id).version_id
            assert current != before

            resp = auth_client.patch(
                f"/accounts/{acct_id}/true-up",
                data={
                    "anchor_balance": "4242.42",
                    # The pin the pre-deploy form still renders, and it is
                    # STALE -- the row has moved past it.  Before R-EN this
                    # exact payload answered 409.
                    "version_id": str(before),
                },
            )
            assert resp.status_code == 200, resp.data[:200]

            db.session.expire_all()
            account = db.session.get(Account, acct_id)
            assert cash_ledger.resolve_anchor(account).balance == Decimal(
                "4242.42",
            )


class TestUpdateAccountStaleForm:
    """``update_account`` (POST /accounts/<id>) optimistic locking on the full edit form."""

    def test_update_account_succeeds_with_matching_version(
        self, app, auth_client, seed_user,
    ):
        """A matching ``version_id`` on the edit form updates and bumps the counter."""
        with app.app_context():
            acct_id = seed_user["account"].id
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            v0 = db.session.get(Account, acct_id).version_id

            response = auth_client.post(
                f"/accounts/{acct_id}",
                data={
                    "name": "Primary Checking",
                    "account_type_id": str(checking_type.id),
                    "version_id": str(v0),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Account &#39;Primary Checking&#39; updated." in response.data

            db.session.expire_all()
            acct = db.session.get(Account, acct_id)
            assert acct.name == "Primary Checking"
            assert acct.version_id == v0 + 1

    def test_update_account_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """A stale ``version_id`` redirects back to the edit form with a warning flash.

        The non-HTMX update_account path uses flash + redirect rather
        than a 409 partial because the surrounding UX is a full-page
        form, not a swap.  The behaviour invariant is the same: NO
        write occurs and the user is told the row changed.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            stale_version = db.session.get(Account, acct_id).version_id

            _bump_account_version_outside_session(acct_id)
            db.session.expire_all()
            name_before = db.session.get(Account, acct_id).name

            response = auth_client.post(
                f"/accounts/{acct_id}",
                data={
                    "name": "Should Not Apply",
                    "account_type_id": str(checking_type.id),
                    "version_id": str(stale_version),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            acct = db.session.get(Account, acct_id)
            assert acct.name == name_before, (
                "Stale-form on update_account must NOT mutate any field."
            )


class TestArchiveAndDeleteStaleData:
    """``archive_account`` / ``unarchive_account`` / ``hard_delete_account`` StaleDataError handling."""

    def test_archive_account_stale_data_redirects_with_warning(
        self, app, db, auth_client, seed_user,
    ):
        """A StaleDataError during archive surfaces as a flash + redirect.

        The contract: the user always receives a useful response,
        never a 500.  The account stays unchanged; the user reloads
        and retries.
        """
        from sqlalchemy import event  # pylint: disable=import-outside-toplevel

        with app.app_context():
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            spare = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Archive Target",
                    anchor_balance=Decimal("0.00"),
                ),
                is_active=True,
            )
            db.session.add(spare)
            db.session.commit()
            spare_id = spare.id

            fired = {"flag": False}

            def make_stale(mapper, connection, target):
                if fired["flag"] or target.id != spare_id:
                    return
                fired["flag"] = True
                _bump_account_version_outside_session(spare_id)

            event.listen(Account, "before_update", make_stale)
            try:
                response = auth_client.post(
                    f"/accounts/{spare_id}/archive",
                    follow_redirects=True,
                )
            finally:
                event.remove(Account, "before_update", make_stale)

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(Account, spare_id)
            assert persisted.is_active is True, (
                "StaleDataError on archive must NOT flip is_active."
            )

    def test_hard_delete_account_stale_data_redirects_with_warning(
        self, app, db, auth_client, seed_user,
    ):
        """A StaleDataError during hard-delete leaves the row intact.

        Unlike a normal delete, the row does NOT get removed when the
        version race goes against this request.  The user receives a
        warning flash and the row remains for the winner of the race.
        """
        from sqlalchemy import event  # pylint: disable=import-outside-toplevel

        with app.app_context():
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            spare = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Delete Target",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(spare)
            db.session.commit()
            spare_id = spare.id

            fired = {"flag": False}

            def make_stale(mapper, connection, target):
                if fired["flag"] or target.id != spare_id:
                    return
                fired["flag"] = True
                _bump_account_version_outside_session(spare_id)

            event.listen(Account, "before_delete", make_stale)
            try:
                response = auth_client.post(
                    f"/accounts/{spare_id}/hard-delete",
                    follow_redirects=True,
                )
            finally:
                event.remove(Account, "before_delete", make_stale)

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(Account, spare_id)
            assert persisted is not None, (
                "StaleDataError on hard-delete must leave the row in "
                "place for the winner of the race to handle."
            )


class TestAnchorTemplatesEmitVersionPin:
    """Which anchor-related forms carry a hidden ``version_id`` pin, and which must not.

    It read "templates that render anchor edit forms MUST include a hidden
    ``version_id`` input" until plan step X-f1c3c.  Ruling R-EN split that in
    two: the ANCHOR editor must not ship one (its handler stopped reading it
    when the true-up stopped writing the ``accounts`` row the counter guards),
    while the full ACCOUNT edit form still must (it writes real columns).  Both
    halves are graded here, which is what keeps the deletion from reading as an
    oversight.

    Three ``*_conflict_cell_retry_opener_*`` cases were DELETED here in the same
    step: they asserted that a 409 conflict cell's retry opener carried the
    right ``revert`` token, and there is no 409.  The revert-token routing they
    shared is still graded by the ``anchor_form`` and ``hx-patch`` cases below,
    per surface.
    """

    def test_anchor_form_ships_no_version_pin(
        self, app, auth_client, seed_user,
    ):
        """GET /accounts/<id>/anchor-form ships NO version_id pin (ruling R-EN).

        The direct inversion of ``test_grid_anchor_form_includes_version_pin``.
        The pin existed for the C-17 optimistic lock, which the true-up path
        lost at plan step X-f1c3c when it stopped writing the ``accounts`` row
        that ``version_id`` guards.  Shipping a pin the handler no longer reads
        would be a field the form lies about.

        Non-vacuity is the sibling below: the full ACCOUNT edit form must still
        ship one, because that door does write real columns.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            response = auth_client.get(f"/accounts/{acct_id}/anchor-form")

            assert response.status_code == 200
            assert 'name="version_id"' not in response.data.decode()

    def test_anchor_form_default_revert_is_grid_display(
        self, app, auth_client, seed_user,
    ):
        """Without ?revert, the editor reverts to the grid display cell.

        Fix C: the grid path passes no ``revert`` token, so Cancel /
        Escape keep their original target -- the ``accounts.anchor_display``
        GET endpoint -- byte-for-byte unchanged.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(f"/accounts/{acct_id}/anchor-form")
            assert response.status_code == 200
            body = response.data.decode()
            display_url = f"/accounts/{acct_id}/anchor-display"
            # Cancel hx-get and Escape data-revert-url both target the
            # grid display cell.
            assert f'hx-get="{display_url}"' in body
            assert f'data-revert-url="{display_url}"' in body
            assert "/dashboard/balance" not in body

    def test_anchor_form_dashboard_revert_targets_balance_section(
        self, app, auth_client, seed_user,
    ):
        """With ?revert=dashboard, the editor reverts to the balance card.

        Fix C: opened from the dashboard balance card, Cancel / Escape must
        restore THAT card (``dashboard.balance_section``) rather than
        stranding the dashboard on the grid's whole-dollar display cell.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(
                f"/accounts/{acct_id}/anchor-form?revert=dashboard"
            )
            assert response.status_code == 200
            body = response.data.decode()
            # Cancel hx-get and Escape data-revert-url both point at the
            # dashboard balance-section partial, not the grid display cell.
            assert 'hx-get="/dashboard/balance"' in body
            assert 'data-revert-url="/dashboard/balance"' in body
            assert f"/accounts/{acct_id}/anchor-display" not in body

    def test_grid_anchor_form_patch_url_carries_no_revert(
        self, app, auth_client, seed_user,
    ):
        """M1 (grid): the edit form's hx-patch carries no ``revert`` token.

        The grid path passes no ``revert`` query param, so the form's
        mutation URL must be the bare ``/true-up`` endpoint -- byte-for-byte
        unchanged from before the revert round-trip was added.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(f"/accounts/{acct_id}/anchor-form")
            assert response.status_code == 200
            body = response.data.decode()
            assert f'hx-patch="/accounts/{acct_id}/true-up"' in body
            assert "revert=dashboard" not in body

    def test_dashboard_anchor_form_patch_url_threads_revert(
        self, app, auth_client, seed_user,
    ):
        """M1 (dashboard): the edit form's hx-patch threads ``revert=dashboard``.

        Opened from the dashboard balance card, the form's mutation URL
        must carry the ``revert`` token so the success re-render lands on the
        dashboard card rather than stranding it on the grid display cell.
        The token also routed a 409 conflict cell's retry opener, until ruling
        R-EN deleted that response (plan step X-f1c3c).
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(
                f"/accounts/{acct_id}/anchor-form?revert=dashboard"
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert (
                f'hx-patch="/accounts/{acct_id}/true-up?revert=dashboard"'
                in body
            )

    def test_anchor_form_accounts_revert_targets_cockpit_balance(
        self, app, auth_client, seed_user,
    ):
        """With ?revert=accounts, the editor reverts to the cockpit card cell.

        Opened from the Net Worth Cockpit's per-card balance, Cancel /
        Escape must restore THAT card's cell (``savings.cockpit_balance``)
        rather than the grid display cell -- the multi-card analog of the
        dashboard ``revert=dashboard`` round-trip.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(
                f"/accounts/{acct_id}/anchor-form?revert=accounts"
            )
            assert response.status_code == 200
            body = response.data.decode()
            cell_url = f"/savings/cockpit/{acct_id}/balance"
            assert f'hx-get="{cell_url}"' in body
            assert f'data-revert-url="{cell_url}"' in body
            assert f"/accounts/{acct_id}/anchor-display" not in body

    def test_accounts_anchor_form_patch_url_threads_revert(
        self, app, auth_client, seed_user,
    ):
        """The cockpit edit form's hx-patch threads ``revert=accounts``.

        So the success re-render lands on the cockpit card rather than
        stranding it on the grid display cell.  The token also routed a 409
        conflict cell's retry opener, until ruling R-EN deleted that response
        (plan step X-f1c3c).
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(
                f"/accounts/{acct_id}/anchor-form?revert=accounts"
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert (
                f'hx-patch="/accounts/{acct_id}/true-up?revert=accounts"'
                in body
            )

    def test_account_edit_form_includes_version_pin(
        self, app, auth_client, seed_user,
    ):
        """GET /accounts/<id>/edit ships ``version_id`` so the POST round-trips."""
        with app.app_context():
            acct_id = seed_user["account"].id
            current_version = db.session.get(Account, acct_id).version_id

            response = auth_client.get(f"/accounts/{acct_id}/edit")

            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{current_version}"' in body

    def test_account_create_form_omits_version_pin(
        self, app, auth_client, seed_user,
    ):
        """The create form has no ``version_id`` -- there is no row to pin yet.

        Catching the regression of a copy-paste that puts an
        ``account.version_id`` reference into the create form would
        produce a Jinja UndefinedError because ``account`` is None
        on that path.
        """
        with app.app_context():
            response = auth_client.get("/accounts/new")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' not in body
