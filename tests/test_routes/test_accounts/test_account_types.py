"""Account route tests: account types, the validation and columns of their metadata, and negative
paths.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.services import account_service


class TestAccountTypes:
    """Tests for account type create, rename, and delete."""

    def test_create_account_type(self, app, auth_client, seed_user):
        """POST /accounts/types creates a new account type owned by the caller.

        After commit C-28 / F-044 every type the route inserts carries
        ``user_id = current_user.id``; built-ins remain
        ``user_id IS NULL`` and are seeded only by the ref-tables seed
        script.  The assertion on ``user_id`` ensures the multi-tenant
        ownership guard is wired through end-to-end.
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            response = auth_client.post(
                "/accounts/types",
                data={"name": "investment", "category_id": asset_id},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Account type &#39;investment&#39; created." in response.data

            acct_type = (
                db.session.query(AccountType).filter_by(name="investment").one()
            )
            assert acct_type.name == "investment"
            assert acct_type.category_id == asset_id
            assert acct_type.user_id == seed_user["user"].id

    def test_rename_account_type(self, app, auth_client, seed_user):
        """POST /accounts/types/<id> renames a type the caller owns.

        The custom type is created with ``user_id = seed_user.id`` so
        the C-28 ownership guard accepts the rename.  A type with
        ``user_id IS NULL`` (a seeded built-in) would be rejected --
        that path is exercised in
        ``TestAccountTypeMultiTenantOwnership.test_owner_cannot_rename_seeded_builtin``.
        """
        with app.app_context():
            # Create a type to rename owned by the current user.
            new_type = AccountType(
                name="rename_source",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                user_id=seed_user["user"].id,
            )
            db.session.add(new_type)
            db.session.commit()

            response = auth_client.post(
                f"/accounts/types/{new_type.id}",
                data={"name": "rename_target"},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data

            db.session.refresh(new_type)
            assert new_type.name == "rename_target"

    def test_update_account_type_clears_max_term(self, app, auth_client, seed_user):
        """An emptied max-term input clears the stored term limit.

        The nullable-field clear rule: ``max_term_months`` is
        allow_none on the update schema, so the empty submit loads as
        an explicit None (it used to be DROPPED, making the limit
        unremovable from the UI) and the route's setattr loop nulls
        the column.  The loan route's term check is truthiness-guarded
        (``if max_term:``), so a NULL limit means unlimited.
        """
        with app.app_context():
            custom_type = AccountType(
                name="capped_loan",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY),
                has_amortization=True,
                max_term_months=360,
                user_id=seed_user["user"].id,
            )
            db.session.add(custom_type)
            db.session.commit()

            response = auth_client.post(
                f"/accounts/types/{custom_type.id}",
                data={"max_term_months": ""},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data
            db.session.refresh(custom_type)
            assert custom_type.max_term_months is None

    def test_delete_unused_account_type(self, app, auth_client, seed_user):
        """POST /accounts/types/<id>/delete removes a type the caller owns.

        Owner-scoped deletion mirrors the rename path: the row must
        belong to ``current_user`` (commit C-28 / F-044).  This test
        covers the happy path; the cross-owner refusal is in the
        new multi-tenant test class.
        """
        with app.app_context():
            new_type = AccountType(
                name="crypto",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                user_id=seed_user["user"].id,
            )
            db.session.add(new_type)
            db.session.commit()
            type_id = new_type.id

            response = auth_client.post(
                f"/accounts/types/{type_id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"deleted" in response.data

            assert db.session.get(AccountType, type_id) is None

    def test_create_duplicate_within_own_namespace(self, app, auth_client, seed_user):
        """A second create with the same name inside the caller's namespace
        is rejected with the duplicate-name warning.

        Per the C-28 acceptance criteria a user MAY create a custom
        type that shadows a seeded built-in (per-user copy), but they
        may NOT create two custom types with the same name -- the
        partial unique index ``uq_account_types_user_name`` is the
        storage-tier backstop and the route surfaces the conflict
        with the same flash the legacy global-UNIQUE produced.
        """
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            # First create -- a per-user copy of a seeded name is allowed.
            first = auth_client.post(
                "/accounts/types",
                data={"name": "Checking", "category_id": asset_id},
                follow_redirects=True,
            )
            assert first.status_code == 200
            assert b"Account type &#39;Checking&#39; created." in first.data

            # Second create with the same name within the same owner's
            # namespace -- rejected.
            response = auth_client.post(
                "/accounts/types",
                data={"name": "Checking", "category_id": asset_id},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"An account type with that name already exists." in response.data

            # Exactly one user-owned "Checking" plus the seeded built-in.
            owned = (
                db.session.query(AccountType)
                .filter_by(name="Checking", user_id=seed_user["user"].id)
                .all()
            )
            assert len(owned) == 1

    def test_delete_account_type_in_use(self, app, auth_client, seed_user):
        """An in-use owner-scoped type cannot be deleted.

        Constructs a custom type owned by ``seed_user`` and a single
        account that references it, then confirms the delete refuses
        with the in-use warning and leaves the type in place so the
        FK relationship from ``budget.accounts`` does not dangle.
        """
        with app.app_context():
            in_use_type = AccountType(
                name="MyCustomType",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                user_id=seed_user["user"].id,
            )
            db.session.add(in_use_type)
            db.session.flush()

            using_account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=in_use_type.id,
                    name="Custom Account",
                    anchor_balance=Decimal("100.00"),
                ),
            )
            db.session.add(using_account)
            db.session.commit()

            response = auth_client.post(
                f"/accounts/types/{in_use_type.id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Cannot delete this account type" in response.data

            # Type should still exist.
            assert db.session.get(AccountType, in_use_type.id) is not None


# ── Account Type Metadata Validation ─────────────────────────────


class TestAccountTypeMetadataValidation:
    """Tests for cross-field validation on account type create/update schemas."""

    def test_create_account_type_with_category(self, app, auth_client, seed_user):
        """POST with category and flags creates a type with correct metadata."""
        with app.app_context():
            liability_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
            resp = auth_client.post(
                "/accounts/types",
                data={
                    "name": "Test Debt",
                    "category_id": liability_id,
                    "has_parameters": "true",
                    "has_amortization": "true",
                    "max_term_months": "240",
                    "icon_class": "bi-cash-coin",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"created" in resp.data

            acct_type = db.session.query(AccountType).filter_by(
                name="Test Debt",
            ).one()
            assert acct_type.category_id == liability_id
            assert acct_type.has_parameters is True
            assert acct_type.has_amortization is True
            assert acct_type.max_term_months == 240
            assert acct_type.icon_class == "bi-cash-coin"

    def test_create_account_type_invalid_flag_combo(
        self, app, auth_client, seed_user,
    ):
        """has_amortization=True with Asset category is rejected."""
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            resp = auth_client.post(
                "/accounts/types",
                data={
                    "name": "Bad Combo",
                    "category_id": asset_id,
                    "has_amortization": "true",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            # Validation error redirects with flash.
            assert b"correct the highlighted errors" in resp.data
            # Type should NOT have been created.
            assert db.session.query(AccountType).filter_by(
                name="Bad Combo",
            ).first() is None

    def test_create_account_type_mutual_exclusion(
        self, app, auth_client, seed_user,
    ):
        """has_amortization and has_interest together is rejected."""
        with app.app_context():
            liability_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
            resp = auth_client.post(
                "/accounts/types",
                data={
                    "name": "Bad Exclusive",
                    "category_id": liability_id,
                    "has_amortization": "true",
                    "has_interest": "true",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"correct the highlighted errors" in resp.data

    def test_max_term_without_amortization(self, app, auth_client, seed_user):
        """max_term_months without has_amortization is rejected."""
        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            resp = auth_client.post(
                "/accounts/types",
                data={
                    "name": "Bad Term",
                    "category_id": asset_id,
                    "max_term_months": "120",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"correct the highlighted errors" in resp.data

    def test_update_account_type_metadata(self, app, auth_client, seed_user):
        """POST update changes metadata fields on a user-owned type.

        The C-28 ownership guard requires ``user_id = seed_user.id``
        on the row before the update route accepts mutations; that
        column is set explicitly here so the test exercises the
        metadata-write path independent of the multi-tenant guard.
        """
        with app.app_context():
            new_type = AccountType(
                name="update_meta_test",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                user_id=seed_user["user"].id,
            )
            db.session.add(new_type)
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/types/{new_type.id}",
                data={
                    "name": "update_meta_test",
                    "has_parameters": "true",
                    "has_interest": "true",
                    "is_liquid": "true",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"updated" in resp.data

            db.session.refresh(new_type)
            assert new_type.has_parameters is True
            assert new_type.has_interest is True
            assert new_type.is_liquid is True

    def test_update_account_type_multidict_checkboxes(self, app, auth_client, seed_user):
        """Boolean flags resolve correctly from MultiDict form data.

        Browsers submit hidden-input + checkbox pairs as duplicate keys
        in a MultiDict.  Checked checkboxes send ('field', 'false') and
        ('field', 'true'); unchecked send only ('field', 'false').  The
        schema must take the last value so checked boxes resolve to True.
        Regression test for a bug where Flask's MultiDict.items() returned
        only the first value, making all booleans always False.
        """
        from werkzeug.datastructures import MultiDict

        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            new_type = AccountType(
                name="multidict_test",
                category_id=asset_id,
                user_id=seed_user["user"].id,
            )
            db.session.add(new_type)
            db.session.commit()

            # Simulate browser form with checked checkboxes (hidden + checkbox).
            resp = auth_client.post(
                f"/accounts/types/{new_type.id}",
                data=MultiDict([
                    ("name", "multidict_test"),
                    ("category_id", str(asset_id)),
                    ("has_parameters", "false"),
                    ("has_parameters", "true"),
                    ("has_amortization", "false"),
                    ("has_interest", "false"),
                    ("has_interest", "true"),
                    ("is_pretax", "false"),
                    ("is_liquid", "false"),
                    ("is_liquid", "true"),
                    ("icon_class", "bi-cash-stack"),
                    ("max_term_months", ""),
                ]),
                follow_redirects=True,
            )
            assert resp.status_code == 200

            db.session.refresh(new_type)
            # Checked checkboxes must resolve to True.
            assert new_type.has_parameters is True
            assert new_type.has_interest is True
            assert new_type.is_liquid is True
            # Unchecked checkboxes must resolve to False.
            assert new_type.has_amortization is False
            assert new_type.is_pretax is False

    def test_update_account_type_multidict_all_unchecked(self, app, auth_client, seed_user):
        """Unchecking all boolean flags via MultiDict sets them to False.

        Starts with all flags True, then submits a form where every
        checkbox is unchecked (only hidden 'false' values sent).
        """
        from werkzeug.datastructures import MultiDict

        with app.app_context():
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            new_type = AccountType(
                name="multidict_uncheck_test",
                category_id=asset_id,
                has_parameters=True,
                has_interest=True,
                is_liquid=True,
                user_id=seed_user["user"].id,
            )
            db.session.add(new_type)
            db.session.commit()

            # Simulate browser form with all checkboxes unchecked.
            resp = auth_client.post(
                f"/accounts/types/{new_type.id}",
                data=MultiDict([
                    ("name", "multidict_uncheck_test"),
                    ("category_id", str(asset_id)),
                    ("has_parameters", "false"),
                    ("has_amortization", "false"),
                    ("has_interest", "false"),
                    ("is_pretax", "false"),
                    ("is_liquid", "false"),
                    ("icon_class", "bi-cash-stack"),
                    ("max_term_months", ""),
                ]),
                follow_redirects=True,
            )
            assert resp.status_code == 200

            db.session.refresh(new_type)
            assert new_type.has_parameters is False
            assert new_type.has_interest is False
            assert new_type.is_liquid is False


# ── Account Type Metadata Columns ────────────────────────────────


class TestAccountTypeMetadataColumns:
    """Verify the has_interest, is_pretax, and is_liquid metadata columns
    are seeded correctly on ref.account_types."""

    def test_account_type_has_interest_column(self, app, seed_user):
        """HYSA and HSA have has_interest=True; other types do not."""
        with app.app_context():
            hysa = db.session.query(AccountType).filter_by(name="HYSA").one()
            hsa = db.session.query(AccountType).filter_by(name="HSA").one()
            checking = db.session.query(AccountType).filter_by(name="Checking").one()
            mortgage = db.session.query(AccountType).filter_by(name="Mortgage").one()

            assert hysa.has_interest is True
            assert hsa.has_interest is True
            assert checking.has_interest is False
            assert mortgage.has_interest is False

            # Column is non-nullable.
            col = AccountType.__table__.columns["has_interest"]
            assert col.nullable is False

    def test_account_type_is_pretax_column(self, app, seed_user):
        """401(k) and Traditional IRA have is_pretax=True; Roth types do not."""
        with app.app_context():
            k401 = db.session.query(AccountType).filter_by(name="401(k)").one()
            trad_ira = db.session.query(AccountType).filter_by(
                name="Traditional IRA",
            ).one()
            roth_401k = db.session.query(AccountType).filter_by(
                name="Roth 401(k)",
            ).one()
            roth_ira = db.session.query(AccountType).filter_by(name="Roth IRA").one()
            brokerage = db.session.query(AccountType).filter_by(
                name="Brokerage",
            ).one()

            assert k401.is_pretax is True
            assert trad_ira.is_pretax is True
            assert roth_401k.is_pretax is False
            assert roth_ira.is_pretax is False
            assert brokerage.is_pretax is False

            # Column is non-nullable.
            col = AccountType.__table__.columns["is_pretax"]
            assert col.nullable is False

    def test_account_type_is_liquid_column(self, app, seed_user):
        """Checking, Savings, HYSA, Money Market have is_liquid=True."""
        with app.app_context():
            checking = db.session.query(AccountType).filter_by(
                name="Checking",
            ).one()
            savings = db.session.query(AccountType).filter_by(name="Savings").one()
            hysa = db.session.query(AccountType).filter_by(name="HYSA").one()
            money_market = db.session.query(AccountType).filter_by(
                name="Money Market",
            ).one()
            cd = db.session.query(AccountType).filter_by(name="CD").one()
            hsa = db.session.query(AccountType).filter_by(name="HSA").one()
            credit_card = db.session.query(AccountType).filter_by(
                name="Credit Card",
            ).one()
            k401 = db.session.query(AccountType).filter_by(name="401(k)").one()

            assert checking.is_liquid is True
            assert savings.is_liquid is True
            assert hysa.is_liquid is True
            assert money_market.is_liquid is True
            assert cd.is_liquid is False
            assert hsa.is_liquid is False
            assert credit_card.is_liquid is False
            assert k401.is_liquid is False

            # Column is non-nullable.
            col = AccountType.__table__.columns["is_liquid"]
            assert col.nullable is False

    def test_hsa_has_parameters_true(self, app, seed_user):
        """HSA now has has_parameters=True (changed from False)."""
        with app.app_context():
            hsa = db.session.query(AccountType).filter_by(name="HSA").one()
            assert hsa.has_parameters is True
            assert hsa.has_interest is True


# ── Negative Paths ────────────────────────────────────────────────


class TestAccountNegativePaths:
    """Negative-path tests: nonexistent IDs, IDOR, idempotent ops, validation, XSS."""

    def test_edit_nonexistent_account(self, app, auth_client, seed_user):
        """GET /accounts/999999/edit for a nonexistent account returns 404 (security)."""
        with app.app_context():
            resp = auth_client.get("/accounts/999999/edit", follow_redirects=True)

            assert resp.status_code == 404

    def test_update_nonexistent_account(self, app, auth_client, seed_user):
        """POST /accounts/999999 for a nonexistent account returns 404 (security)."""
        with app.app_context():
            checking_type = db.session.query(AccountType).filter_by(name="Checking").one()

            resp = auth_client.post("/accounts/999999", data={
                "name": "Ghost",
                "account_type_id": checking_type.id,
            }, follow_redirects=True)

            assert resp.status_code == 404

    def test_archive_nonexistent_account(self, app, auth_client, seed_user):
        """POST /accounts/999999/archive for a nonexistent account returns 404 (security)."""
        with app.app_context():
            resp = auth_client.post(
                "/accounts/999999/archive", follow_redirects=True,
            )

            assert resp.status_code == 404

    def test_unarchive_other_users_account_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /accounts/<id>/unarchive for another user's archived account returns 404 (security)."""
        with app.app_context():
            # Re-query to ensure the object is in the current session.
            acct_id = second_user["account"].id
            other_acct = db.session.get(Account, acct_id)
            other_acct.is_active = False
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/{acct_id}/unarchive",
                follow_redirects=True,
            )

            assert resp.status_code == 404

            # Verify DB state unchanged: account still inactive.
            db.session.expire_all()
            refreshed = db.session.get(Account, acct_id)
            assert refreshed.is_active is False

    def test_archive_already_inactive_account(self, app, auth_client, seed_user):
        """POST /accounts/<id>/archive on an already-inactive account is idempotent."""
        with app.app_context():
            account_id = seed_user["account"].id

            # First archive via the route.
            resp1 = auth_client.post(
                f"/accounts/{account_id}/archive",
                follow_redirects=True,
            )
            assert resp1.status_code == 200
            assert b"archived" in resp1.data

            # Second archive -- account is already inactive.
            resp2 = auth_client.post(
                f"/accounts/{account_id}/archive",
                follow_redirects=True,
            )

            # Route does not guard against double-archive; it sets
            # is_active=False and commits. This is idempotent behavior.
            assert resp2.status_code == 200
            assert b"archived" in resp2.data

            db.session.expire_all()
            refreshed = db.session.get(Account, account_id)
            assert refreshed.is_active is False

    def test_unarchive_already_active_account(self, app, auth_client, seed_user):
        """POST /accounts/<id>/unarchive on an already-active account is idempotent."""
        with app.app_context():
            account_id = seed_user["account"].id

            # Account starts active (default from seed). Unarchive anyway.
            resp = auth_client.post(
                f"/accounts/{account_id}/unarchive",
                follow_redirects=True,
            )

            # Route does not guard against unarchiving an already-active
            # account; it sets is_active=True and commits.
            assert resp.status_code == 200
            assert b"unarchived" in resp.data

            db.session.expire_all()
            refreshed = db.session.get(Account, account_id)
            assert refreshed.is_active is True

    def test_create_account_missing_name(self, app, auth_client, seed_user):
        """POST /accounts with missing name field fails schema validation and creates no record."""
        with app.app_context():
            checking_type = db.session.query(AccountType).filter_by(name="Checking").one()

            resp = auth_client.post("/accounts", data={
                "account_type_id": checking_type.id,
                "anchor_balance": "500.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            # Verify no extra account was created (seed_user has exactly 1).
            count = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 1

    def test_create_account_xss_in_name(self, app, auth_client, seed_user):
        """POST /accounts with script tag in name is stored but Jinja2 auto-escapes on render."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()

            # Schema accepts the name (no character restrictions, 32 chars < 100 max).
            resp = auth_client.post("/accounts", data={
                "name": "<script>alert(1)</script>",
                "account_type_id": savings_type.id,
                "anchor_balance": "0",
            }, follow_redirects=True)

            assert resp.status_code == 200

            # Verify account was created in DB.
            acct = (
                db.session.query(Account)
                .filter_by(
                    user_id=seed_user["user"].id,
                    name="<script>alert(1)</script>",
                )
                .one()
            )
            assert acct is not None

            # Verify the XSS payload does not appear unescaped in the response.
            assert b"<script>alert(1)</script>" not in resp.data
            # Verify the escaped form is present (Jinja2 auto-escaping).
            assert b"&lt;script&gt;" in resp.data
