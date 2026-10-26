"""Account route tests: account type ownership across users, and the blueprint's re-export."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType

from ._helpers import _login_as


class TestAccountTypeMultiTenantOwnership:
    """Multi-tenant guard for ``ref.account_types`` (C-28 / F-044).

    Every test in this class exercises the per-user namespace policy:

      * Built-in types (``user_id IS NULL``) are seeded by
        ``app.ref_seeds.seed_reference_data`` and are read-only to every
        owner.
      * Owner-scoped types carry ``user_id = <creator>``.  Only the
        creator may rename or delete them; other owners do not see
        them in any listing and cannot reference them by ID through
        a forged form post.
      * Two different owners may each carry their own custom type
        with the same name; an owner may shadow a seeded built-in
        with their own copy.

    The route response for "type belongs to another owner" is the
    same as for "type does not exist" so the response cannot be used
    to enumerate other owners' catalogues.

    Two-owner scenarios use ``_login_as`` rather than the
    ``second_auth_client`` fixture so each client gets its own clean
    cookie jar.  The second_auth_client fixture interacts oddly with
    auth_client's cookies in the same test session (documented in
    ``test_fixture_validation.py``).
    """

    def test_create_type_persists_owner_user_id(
        self, app, auth_client, seed_user,
    ):
        """A new custom type carries ``user_id = current_user.id``.

        End-to-end check that the route layer's
        ``AccountType(user_id=current_user.id, **data)`` is in fact
        the path the form post takes.  Without this assertion a
        regression that dropped ``user_id`` on insert would silently
        re-introduce a global type and bypass the multi-tenant guard
        on every subsequent rename/delete attempt.
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            response = auth_client.post(
                "/accounts/types",
                data={"name": "OwnerScopedType", "category_id": asset_id},
                follow_redirects=True,
            )
            assert response.status_code == 200

            row = (
                db.session.query(AccountType)
                .filter_by(name="OwnerScopedType")
                .one()
            )
            assert row.user_id == seed_user["user"].id, (
                "create route must stamp user_id from current_user"
            )

    def test_owner_b_cannot_rename_owner_a_custom_type(
        self, app, db, seed_user, second_user,
    ):
        """A cross-owner rename returns 404, identical to a missing row.

        Owner A creates a custom type; Owner B (logged in via a
        fresh test_client) attempts to rename it via the route.
        The 404 response is identical to attempting to rename a
        non-existent type so Owner B cannot use the response to
        discover the existence of Owner A's catalogue.
        """
        with app.app_context():
            owner_a_type = AccountType(
                name="A_CustomType",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                user_id=seed_user["user"].id,
            )
            db.session.add(owner_a_type)
            db.session.commit()
            type_id = owner_a_type.id

        owner_b_client = _login_as(app, "other@shekel.local", "otherpass")

        # Owner B attempts the rename.
        response = owner_b_client.post(
            f"/accounts/types/{type_id}",
            data={"name": "Hijacked"},
            follow_redirects=True,
        )

        assert response.status_code == 404
        # Same response shape as a non-existent ID -- no leak.
        ghost_response = owner_b_client.post(
            "/accounts/types/9999999",
            data={"name": "Hijacked"},
            follow_redirects=True,
        )
        assert ghost_response.status_code == 404

        # The original row is unchanged.
        with app.app_context():
            unchanged = db.session.get(AccountType, type_id)
            assert unchanged.name == "A_CustomType"
            assert unchanged.user_id == seed_user["user"].id

    def test_owner_b_cannot_delete_owner_a_custom_type(
        self, app, db, seed_user, second_user,
    ):
        """A cross-owner delete returns the same flash as a missing row.

        The companion to the rename test: confirms the ownership
        guard fires on the delete path too.  Without the guard
        Owner B could enumerate Owner A's IDs by repeated deletes
        and watching for the type-in-use vs not-found responses.
        """
        with app.app_context():
            owner_a_type = AccountType(
                name="A_DeleteTarget",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                user_id=seed_user["user"].id,
            )
            db.session.add(owner_a_type)
            db.session.commit()
            type_id = owner_a_type.id

        owner_b_client = _login_as(app, "other@shekel.local", "otherpass")

        response = owner_b_client.post(
            f"/accounts/types/{type_id}/delete",
            follow_redirects=True,
        )

        assert response.status_code == 404

        with app.app_context():
            assert db.session.get(AccountType, type_id) is not None

    def test_owner_cannot_rename_seeded_builtin(
        self, app, auth_client, seed_user,
    ):
        """A seeded built-in (``user_id IS NULL``) is read-only.

        The route's ownership guard treats the seed-time NULL the
        same as another user's ID: ``account_type.user_id !=
        current_user.id`` is True for both.  The route returns 404
        and the row's name does not change.
        """
        with app.app_context():
            checking = (
                db.session.query(AccountType)
                .filter_by(name="Checking", user_id=None)
                .one()
            )

            response = auth_client.post(
                f"/accounts/types/{checking.id}",
                data={"name": "RenamedSeed"},
                follow_redirects=True,
            )

            assert response.status_code == 404

            db.session.expire(checking)
            db.session.refresh(checking)
            assert checking.name == "Checking"
            assert checking.user_id is None

    def test_owner_cannot_delete_seeded_builtin(
        self, app, auth_client, seed_user,
    ):
        """A seeded built-in cannot be deleted through the route.

        Mirrors the rename test for the delete path.  The seeded
        catalogue must remain stable so the ``ref_cache`` enum-to-id
        contract holds across application restarts; allowing owners
        to delete a built-in would silently break every consumer
        that resolves ``AcctTypeEnum.CHECKING``.
        """
        with app.app_context():
            checking = (
                db.session.query(AccountType)
                .filter_by(name="Checking", user_id=None)
                .one()
            )

            response = auth_client.post(
                f"/accounts/types/{checking.id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 404

            assert db.session.get(AccountType, checking.id) is not None

    def test_two_owners_can_share_custom_name(
        self, app, db, seed_user, second_user,
    ):
        """Owner A and Owner B may each carry a custom "Crypto".

        The two custom rows are distinct (different ``id`` and
        ``user_id``); each owner sees only their own.  This is the
        core multi-tenant promise the partial unique index
        ``uq_account_types_user_name`` (``UNIQUE (user_id, name)
        WHERE user_id IS NOT NULL``) enforces -- the legacy global
        UNIQUE on ``name`` would have rejected the second row.

        Owner A's row is created through the ORM directly while
        Owner B's row goes through the route; only one fresh client
        is logged in to side-step the double-login fixture quirk
        documented at the top of this class.
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            # Owner A's row -- direct ORM insert.
            a_row = AccountType(
                name="Crypto",
                category_id=asset_id,
                user_id=seed_user["user"].id,
            )
            db.session.add(a_row)
            db.session.commit()

        owner_b_client = _login_as(app, "other@shekel.local", "otherpass")

        # Owner B creates "Crypto" through the route -- distinct row.
        resp_b = owner_b_client.post(
            "/accounts/types",
            data={"name": "Crypto", "category_id": asset_id},
            follow_redirects=True,
        )
        assert resp_b.status_code == 200
        assert b"created" in resp_b.data

        with app.app_context():
            rows = (
                db.session.query(AccountType)
                .filter_by(name="Crypto")
                .order_by(AccountType.id)
                .all()
            )
            owners = {r.user_id for r in rows}
            assert owners == {
                seed_user["user"].id, second_user["user"].id,
            }
            assert len(rows) == 2

    def test_owner_can_create_per_user_copy_of_seed_name(
        self, app, auth_client, seed_user,
    ):
        """An owner may create a custom type whose name shadows a built-in.

        Per the C-28 acceptance criteria.  The two rows coexist:
        ``Checking`` with ``user_id IS NULL`` (built-in) and
        ``Checking`` with ``user_id = seed_user.id`` (custom).
        The seeded-name partial index restricts only the ``user_id
        IS NULL`` namespace; the user-name partial index restricts
        only the ``user_id IS NOT NULL`` namespace; the predicates
        are disjoint so both rows pass.
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)

        response = auth_client.post(
            "/accounts/types",
            data={"name": "Checking", "category_id": asset_id},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"created" in response.data

        with app.app_context():
            rows = (
                db.session.query(AccountType)
                .filter_by(name="Checking")
                .order_by(AccountType.user_id.asc().nullsfirst())
                .all()
            )
            assert len(rows) == 2
            seeded, custom = rows
            assert seeded.user_id is None
            assert custom.user_id == seed_user["user"].id

    def test_settings_listing_excludes_other_owners_custom_types(
        self, app, db, seed_user, second_user,
    ):
        """The settings page shows seeded + own; other owners' types are hidden.

        Owner B's "B_Secret" type is inserted via the ORM (same
        rationale as ``test_two_owners_can_share_custom_name``);
        Owner A logs in via a fresh client and the page body must
        not contain the string "B_Secret".  Owner A's own page
        still includes every seeded built-in (sanity check that
        the filter is OR, not AND).
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            secret = AccountType(
                name="B_Secret",
                category_id=asset_id,
                user_id=second_user["user"].id,
            )
            db.session.add(secret)
            db.session.commit()

        owner_a_client = _login_as(app, "test@shekel.local", "testpass")

        # Owner A loads settings -- must not see "B_Secret".
        resp_a = owner_a_client.get("/settings?section=account-types")
        assert resp_a.status_code == 200
        body = resp_a.data.decode()
        assert "B_Secret" not in body
        # Sanity: built-ins are still present for Owner A.
        assert "Checking" in body

    def test_settings_listing_includes_own_custom_types(
        self, app, auth_client, seed_user,
    ):
        """Owners see their own custom types alongside the seeded built-ins."""
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            owned = AccountType(
                name="OwnVisibleType",
                category_id=asset_id,
                user_id=seed_user["user"].id,
            )
            db.session.add(owned)
            db.session.commit()

        resp = auth_client.get("/settings?section=account-types")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "OwnVisibleType" in body
        # A built-in is still rendered.
        assert "Checking" in body

    def test_account_form_dropdown_excludes_other_owners_types(
        self, app, db, seed_user, second_user,
    ):
        """The /accounts/new dropdown shows seeded + own only.

        A leak in the dropdown would let Owner A select Owner B's
        custom type by name, and a successful POST would create
        a cross-owner FK -- exactly the IDOR the route-layer
        ``_account_type_is_visible`` guard is meant to close.
        Owner B's type is inserted via the ORM to avoid the
        double-login fixture quirk.
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            trap = AccountType(
                name="B_DropdownTrap",
                category_id=asset_id,
                user_id=second_user["user"].id,
            )
            db.session.add(trap)
            db.session.commit()

        owner_a_client = _login_as(app, "test@shekel.local", "testpass")
        resp = owner_a_client.get("/accounts/new")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "B_DropdownTrap" not in body
        # Sanity: built-ins remain available.
        assert "Checking" in body

    def test_create_account_with_other_owner_type_id_rejected(
        self, app, db, seed_user, second_user,
    ):
        """A forged ``account_type_id`` referencing another owner's type is rejected.

        Closes the IDOR that C-28 itself opens.  The dropdown
        already excludes the foreign type, but a hand-crafted POST
        that passes the FK by ID must also fail.  The response is
        an "Invalid account type." flash on the new-account form
        and no row is inserted into ``budget.accounts``.  Owner B's
        type is inserted via the ORM to side-step the double-login
        fixture quirk.
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            foreign_type = AccountType(
                name="B_OnlyMine",
                category_id=asset_id,
                user_id=second_user["user"].id,
            )
            db.session.add(foreign_type)
            db.session.commit()
            foreign_id = foreign_type.id

            before_count = (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id)
                .count()
            )

        owner_a_client = _login_as(app, "test@shekel.local", "testpass")
        response = owner_a_client.post(
            "/accounts",
            data={
                "name": "ForgedAccount",
                "account_type_id": foreign_id,
                "anchor_balance": "0",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Invalid account type." in response.data

        with app.app_context():
            after_count = (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id)
                .count()
            )
            assert after_count == before_count, (
                "no account row should have been created on rejected post"
            )
            # And no account row anywhere references the foreign type
            # under Owner A.
            forged = (
                db.session.query(Account)
                .filter_by(
                    user_id=seed_user["user"].id,
                    account_type_id=foreign_id,
                )
                .first()
            )
            assert forged is None

    def test_update_account_with_other_owner_type_id_rejected(
        self, app, db, seed_user, second_user,
    ):
        """An update that re-parents to another owner's type is rejected.

        Mirror of the create test for the update path.  Without the
        ``_account_type_is_visible`` guard a malicious POST against
        ``/accounts/<id>`` could change ``account_type_id`` to
        another owner's type and bypass the dropdown filter entirely.
        Owner B's type is inserted via the ORM to side-step the
        double-login fixture quirk.
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            foreign_type = AccountType(
                name="B_NotOurs",
                category_id=asset_id,
                user_id=second_user["user"].id,
            )
            db.session.add(foreign_type)
            db.session.commit()
            foreign_id = foreign_type.id
            account_id = seed_user["account"].id
            original_type_id = seed_user["account"].account_type_id
            account_name = seed_user["account"].name

        owner_a_client = _login_as(app, "test@shekel.local", "testpass")
        response = owner_a_client.post(
            f"/accounts/{account_id}",
            data={
                "name": account_name,
                "account_type_id": foreign_id,
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Invalid account type." in response.data

        with app.app_context():
            account = db.session.get(Account, account_id)
            assert account.account_type_id == original_type_id, (
                "account_type_id must remain pinned to the original type"
            )

    def test_audit_trigger_logs_account_type_mutations(
        self, app, auth_client, seed_user,
    ):
        """Mutations on ``ref.account_types`` land in ``system.audit_log``.

        Commit C-28 added ``("ref", "account_types")`` to
        ``AUDITED_TABLES``.  This test fires an INSERT through the
        route, then an UPDATE, then a DELETE, and asserts each
        operation produced a matching row in the forensic table
        with the calling user's ID populated -- closing the
        forensic-trail gap that pre-C-28 left for owner-driven
        type churn.
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            user_id = seed_user["user"].id

            before_count = db.session.execute(text(
                "SELECT count(*) FROM system.audit_log "
                "WHERE table_schema = 'ref' AND table_name = 'account_types'"
            )).scalar()

        # INSERT
        resp_create = auth_client.post(
            "/accounts/types",
            data={"name": "AuditedType", "category_id": asset_id},
            follow_redirects=True,
        )
        assert resp_create.status_code == 200

        with app.app_context():
            row = (
                db.session.query(AccountType)
                .filter_by(name="AuditedType", user_id=user_id)
                .one()
            )
            type_id = row.id

        # UPDATE (rename)
        resp_update = auth_client.post(
            f"/accounts/types/{type_id}",
            data={"name": "AuditedTypeRenamed"},
            follow_redirects=True,
        )
        assert resp_update.status_code == 200

        # DELETE
        resp_delete = auth_client.post(
            f"/accounts/types/{type_id}/delete",
            follow_redirects=True,
        )
        assert resp_delete.status_code == 200

        with app.app_context():
            rows = db.session.execute(text(
                "SELECT operation, user_id "
                "FROM system.audit_log "
                "WHERE table_schema = 'ref' AND table_name = 'account_types' "
                "  AND row_id = :row_id "
                "ORDER BY id"
            ), {"row_id": type_id}).fetchall()

            ops = [r[0] for r in rows]
            assert "INSERT" in ops
            assert "UPDATE" in ops
            assert "DELETE" in ops

            for op_name, audit_user in rows:
                assert audit_user == user_id, (
                    f"{op_name} audit row missing user_id "
                    f"(expected {user_id}, got {audit_user})"
                )

            after_count = db.session.execute(text(
                "SELECT count(*) FROM system.audit_log "
                "WHERE table_schema = 'ref' AND table_name = 'account_types'"
            )).scalar()
            assert after_count >= before_count + 3, (
                "expected at least three new audit rows "
                f"(insert + update + delete), gained {after_count - before_count}"
            )

    def test_legacy_global_unique_replaced_by_partial_indexes(
        self, app, db,
    ):
        """The migration-time partial unique indexes are present and active.

        Storage-tier sanity check that complements the route-tier
        tests above.  An INSERT of a duplicate per-user row raises
        IntegrityError naming ``uq_account_types_user_name``; an
        INSERT of a duplicate seeded row raises IntegrityError
        naming ``uq_account_types_seeded_name``.  The legacy
        ``account_types_name_key`` UNIQUE constraint must NOT be
        present, otherwise per-user copies of seeded names would
        be rejected at insert time.
        """
        with app.app_context():
            indexes = {
                row[0]
                for row in db.session.execute(text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = 'ref' AND tablename = 'account_types'"
                ))
            }
            assert "uq_account_types_seeded_name" in indexes
            assert "uq_account_types_user_name" in indexes
            assert "ix_account_types_user_id" in indexes

            unique_constraints = {
                row[0]
                for row in db.session.execute(text(
                    "SELECT constraint_name "
                    "FROM information_schema.table_constraints "
                    "WHERE table_schema = 'ref' "
                    "  AND table_name = 'account_types' "
                    "  AND constraint_type = 'UNIQUE'"
                ))
            }
            # The legacy global UNIQUE(name) must be gone -- otherwise
            # the per-user-copy contract would be impossible.
            assert "account_types_name_key" not in unique_constraints

    def test_seeded_partial_index_blocks_duplicate_seed(
        self, app, db,
    ):
        """Two seeded rows with the same name violate the seeded partial index.

        Defensive coverage for the seed script: a future change that
        accidentally inserts a duplicate seed row (no user_id) must
        be caught by the storage tier rather than producing a
        silently-corrupt cache where ``ref_cache`` resolves an enum
        member to one ID on one boot and a different ID on the next.
        """
        with app.app_context():
            asset_cat_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            duplicate = AccountType(
                name="Checking",  # Already seeded with user_id IS NULL
                category_id=asset_cat_id,
                user_id=None,
            )
            db.session.add(duplicate)
            with pytest.raises(IntegrityError):
                db.session.flush()
            db.session.rollback()

    def test_user_partial_index_blocks_same_user_duplicate(
        self, app, db, seed_user,
    ):
        """Two custom rows with the same (user_id, name) violate the user partial index.

        Defensive coverage for the route-layer per-user duplicate
        check.  If the route's pre-flight is bypassed (concurrent
        request, future code change) the partial unique index is
        the last line of defence and surfaces an IntegrityError on
        flush instead of silently committing two rows.
        """
        with app.app_context():
            asset_cat_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            first = AccountType(
                name="DupName",
                category_id=asset_cat_id,
                user_id=seed_user["user"].id,
            )
            second = AccountType(
                name="DupName",
                category_id=asset_cat_id,
                user_id=seed_user["user"].id,
            )
            db.session.add_all([first, second])
            with pytest.raises(IntegrityError):
                db.session.flush()
            db.session.rollback()

    def test_user_partial_index_allows_cross_user_duplicate(
        self, app, db, seed_user, seed_second_user,
    ):
        """Two custom rows with the same name but different user_id coexist.

        Direct ORM insert path -- bypasses the route to assert the
        storage tier is the correct shape.  Two owners must each be
        able to carry a custom type called "Shared".
        """
        with app.app_context():
            asset_cat_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            for_a = AccountType(
                name="Shared",
                category_id=asset_cat_id,
                user_id=seed_user["user"].id,
            )
            for_b = AccountType(
                name="Shared",
                category_id=asset_cat_id,
                user_id=seed_second_user["user"].id,
            )
            db.session.add_all([for_a, for_b])
            db.session.flush()  # No IntegrityError.
            db.session.commit()

            rows = (
                db.session.query(AccountType)
                .filter_by(name="Shared")
                .order_by(AccountType.user_id)
                .all()
            )
            assert len(rows) == 2
            assert {r.user_id for r in rows} == {
                seed_user["user"].id, seed_second_user["user"].id,
            }

    def test_audit_table_is_registered(self):
        """``ref.account_types`` is in ``AUDITED_TABLES``.

        Registry-level check: pre-C-28 the table was excluded on the
        "ref schema is read-only" rationale; post-C-28 the rule's
        premise no longer holds for this specific table and the
        registry must reflect that so the entrypoint trigger-count
        health check refuses to start a deployment whose triggers
        do not match.
        """
        from app.audit_infrastructure import AUDITED_TABLES  # pylint: disable=import-outside-toplevel
        assert ("ref", "account_types") in AUDITED_TABLES


class TestAccountsBlueprintReExport:
    """C1-2: pin the F-25 ``accounts_bp`` re-export contract.

    Post-F-25 the blueprint declaration lives in
    :mod:`app.routes.accounts._bp` so the package <-> submodule
    import round-trip no longer trips pylint's ``R0401`` cyclic-
    import detector.  ``app/__init__.py`` and any other consumer
    that historically did ``from app.routes.accounts import
    accounts_bp`` continues to resolve because the package init
    re-exports the symbol.  A future cleanup that drops the
    re-export would silently break the factory-time blueprint
    registration; this test pins the contract so the regression
    surfaces at the unit-test layer rather than at app boot.
    """

    def test_package_reexports_same_blueprint_instance(self):
        """The package-level ``accounts_bp`` is the leaf-module instance.

        ``is`` rather than ``==`` -- a copy of the blueprint would
        register a parallel set of routes against the app and the
        URL surface would silently diverge.
        """
        from app.routes.accounts import accounts_bp as package_bp  # pylint: disable=import-outside-toplevel
        from app.routes.accounts._bp import accounts_bp as leaf_bp  # pylint: disable=import-outside-toplevel
        assert package_bp is leaf_bp
