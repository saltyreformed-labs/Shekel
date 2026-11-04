"""Account route tests: what an account's history refuses of a type change, and what
an allowed one re-reads.
"""

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.services.ledger_report_service import compute_balance_sheet
from app.utils.dates import display_today
from tests._ledger_parity import assert_ledger_parity
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    create_settled_cash_transaction,
)
from app.models.ref import AccountType
from app.services import account_service


def _sheet_line(section, label):
    """Return the amount of the one line of a statement section with *label*."""
    amounts = [line.amount for line in section.lines if line.label == label]
    assert len(amounts) == 1, f"{label!r} in {[l.label for l in section.lines]}"
    return amounts[0]


def _mistaken_card(seed_user):
    """Return a card set up as a Savings account: -$500.00 recorded, $70.00 spent.

    Ruling plan#2785's example, with a settled purchase on it so its history is
    both a correction and a movement: it reads ``-500.00 - 70.00 = -570.00``.
    """
    account = create_account_of_type(
        seed_user, db.session, "Savings", "Mistaken Card",
        anchor_balance=Decimal("-500.00"),
        observed_on=display_today() - timedelta(days=1),
    )
    create_settled_cash_transaction(
        seed_user, db.session, seed_user["bootstrap_period"], Decimal("70.00"),
        account=account, category=seed_user["categories"]["Groceries"],
        settled_on=display_today(),
    )
    db.session.commit()
    return account


def _assert_it_moved_to_liabilities(seed_user, equity_before):
    """Assert the mistaken card reads $570.00 owed, equity and the tie-out unchanged.

    Assets fall from ``1,000.00 - 570.00 = 430.00`` to the seeded Checking's
    ``1,000.00``, and Liabilities rise from nothing to ``570.00``, so
    ``1,000.00 = 570.00 + 430.00`` still holds with the same equity.
    """
    after = compute_balance_sheet(seed_user["user"].id, display_today())
    assert [line.label for line in after.assets.lines] == ["Checking"]
    assert after.assets.total == Decimal("1000.00")
    assert _sheet_line(after.liabilities, "Mistaken Card") == Decimal("570.00")
    assert after.equity.total == equity_before
    assert after.tie_out.in_balance is True
    assert_ledger_parity(db.session, seed_user["scenario"].id)


class TestTypeChangeBoundaryGuard:
    """What an account's history refuses of a type change (ruling plan#2785).

    Across a loan type and a non-loan type, re-typing an account or editing a
    custom type in place is refused while the account holds a line of the
    worked-out ledger: the kind decides which records its history is worked out
    from, so the crossing would drop its lines.  Across an asset type and a
    liability type nothing is refused, history or not: a line is classed by its
    account's current type, as it is labeled by its current name.  Either crossing
    on an account holding no line passes.  An allowed crossing re-classes the
    stored linked row and re-syncs the corrections.
    """

    def test_retype_with_history_across_class_boundary_is_allowed(
        self, app, auth_client, seed_user,
    ):
        """Re-typing the mistaken Savings to Credit Card moves it, equity unchanged.

        It read ``-570.00`` under Assets and reads ``570.00`` owed under
        Liabilities on every past balance sheet (ruling plan#2785's worked example),
        and the stored linked row follows its class.
        """
        # Pylint: ``import-outside-toplevel`` -- localized to the one test
        # that needs these helpers, matching the file's convention.
        # pylint: disable=import-outside-toplevel
        from app.enums import LedgerAccountClassEnum
        from tests._test_helpers import linked_ledger_account

        with app.app_context():
            account = _mistaken_card(seed_user)
            account_id = account.id
            cc_type = (
                db.session.query(AccountType)
                .filter_by(name="Credit Card", user_id=None)
                .one()
            )
            before = compute_balance_sheet(seed_user["user"].id, display_today())
            assert _sheet_line(before.assets, "Mistaken Card") == Decimal("-570.00")
            assert before.assets.total == Decimal("430.00")

            response = auth_client.post(
                f"/accounts/{account_id}",
                data={"account_type_id": str(cc_type.id)},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data
            db.session.expire_all()
            assert db.session.get(Account, account_id).account_type_id == cc_type.id
            assert linked_ledger_account(
                db.session, account_id,
            ).class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.LIABILITY,
            )
            _assert_it_moved_to_liabilities(seed_user, before.equity.total)

    def test_type_edit_with_history_across_class_boundary_is_allowed(
        self, app, auth_client, seed_user,
    ):
        """Editing the mistaken account's custom type to a Liability moves it too.

        The other door to the same crossing, with the same figures: no
        ``account_type_id`` changes, only the category of the type it is on.
        """
        # Pylint: ``import-outside-toplevel`` -- localized to the one test
        # that needs these helpers, matching the file's convention.
        # pylint: disable=import-outside-toplevel
        from app.enums import LedgerAccountClassEnum
        from tests._test_helpers import linked_ledger_account

        with app.app_context():
            custom_type = AccountType(
                name="side_pocket",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                user_id=seed_user["user"].id,
            )
            db.session.add(custom_type)
            db.session.commit()
            account = _mistaken_card(seed_user)
            account_id = account.id
            account.account_type_id = custom_type.id
            db.session.commit()
            before = compute_balance_sheet(seed_user["user"].id, display_today())
            assert _sheet_line(before.assets, "Mistaken Card") == Decimal("-570.00")

            response = auth_client.post(
                f"/accounts/types/{custom_type.id}",
                data={
                    "category_id": str(ref_cache.acct_category_id(
                        AcctCategoryEnum.LIABILITY,
                    )),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data
            db.session.expire_all()
            assert linked_ledger_account(
                db.session, account_id,
            ).class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.LIABILITY,
            )
            _assert_it_moved_to_liabilities(seed_user, before.equity.total)

    def test_retype_holding_a_line_to_a_loan_type_refused(
        self, app, auth_client, seed_user,
    ):
        """The seed Checking's $1000.00 opening refuses a re-type to Mortgage.

        Worked out from a loan's terms, of which it has none, it would drop that
        opening from every statement.
        """
        with app.app_context():
            checking = seed_user["account"]
            old_type_id = checking.account_type_id
            mortgage_type = (
                db.session.query(AccountType)
                .filter_by(name="Mortgage", user_id=None)
                .one()
            )

            response = auth_client.post(
                f"/accounts/{checking.id}",
                data={"account_type_id": str(mortgage_type.id)},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"between a loan type and a non-loan type" in response.data
            assert db.session.get(Account, checking.id).account_type_id == old_type_id

    def test_retype_of_a_loan_holding_its_opening_refused(
        self, app, auth_client, seed_user,
    ):
        """A configured loan's opening refuses a re-type to a non-loan type.

        The other direction across the boundary: the opening comes from its
        terms, which a Savings account's walk does not read.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Configured Loan",
                principal=Decimal("20000.00"), rate=Decimal("0.05000"),
            )
            db.session.commit()
            loan_id, old_type_id = loan.id, loan.account_type_id
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings", user_id=None)
                .one()
            )

            response = auth_client.post(
                f"/accounts/{loan_id}",
                data={"account_type_id": str(savings_type.id)},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"between a loan type and a non-loan type" in response.data
            assert db.session.get(Account, loan_id).account_type_id == old_type_id

    def test_retype_holding_no_line_to_a_loan_type_is_allowed(
        self, app, auth_client, seed_user,
    ):
        """The refusal's negative control: a $0.00 opening books no line, and passes.

        The seed Checking's lines stand beside it and are not this account's.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Savings", "Empty Savings",
                anchor_balance=Decimal("0.00"),
            )
            db.session.commit()
            account_id = account.id
            mortgage_type = (
                db.session.query(AccountType)
                .filter_by(name="Mortgage", user_id=None)
                .one()
            )

            response = auth_client.post(
                f"/accounts/{account_id}",
                data={"account_type_id": str(mortgage_type.id)},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data
            assert db.session.get(Account, account_id).account_type_id == mortgage_type.id

    def test_retype_unposted_account_reclasses_linked_row(
        self, app, auth_client, seed_user,
    ):
        """A $0-anchor account re-types across the boundary and re-classes.

        A zero opening books nothing, so the ledger is empty and the
        crossing is allowed; the route re-snapshots the empty linked row's
        class to the new category's (Asset -> Liability) so future postings
        land in the right balance-sheet section.
        """
        # Pylint: ``import-outside-toplevel`` -- localized to the one test
        # that needs these helpers, matching the file's convention.
        # pylint: disable=import-outside-toplevel
        from app.enums import LedgerAccountClassEnum
        from tests._test_helpers import linked_ledger_account

        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings", user_id=None)
                .one()
            )
            cc_type = (
                db.session.query(AccountType)
                .filter_by(name="Credit Card", user_id=None)
                .one()
            )
            account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Reclass Target",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.commit()
            linked = linked_ledger_account(db.session, account.id)
            assert linked.class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.ASSET,
            )

            response = auth_client.post(
                f"/accounts/{account.id}",
                data={"account_type_id": str(cc_type.id)},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data
            # Re-fetch: the request's session lifecycle detached the objects.
            reloaded = db.session.get(Account, account.id)
            assert reloaded.account_type_id == cc_type.id
            relinked = linked_ledger_account(db.session, account.id)
            assert relinked.class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.LIABILITY,
            )

    def test_type_edit_amortization_flip_refused_holding_a_line(
        self, app, auth_client, seed_user,
    ):
        """Flipping ``has_amortization`` on a type whose account holds a line refuses.

        The second crossing vector (C4 adversarial review M2): the custom
        Liability type's account holds its opening's lines, and flipping the
        flag in place would move the account across the loan boundary with no
        ``account_type_id`` change to guard.
        """
        with app.app_context():
            liability_id = ref_cache.acct_category_id(
                AcctCategoryEnum.LIABILITY,
            )
            custom_type = AccountType(
                name="store_card",
                category_id=liability_id,
                user_id=seed_user["user"].id,
            )
            db.session.add(custom_type)
            db.session.commit()
            account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=custom_type.id,
                    name="Posted Store Card",
                    anchor_balance=Decimal("-250.00"),
                ),
            )
            db.session.commit()

            response = auth_client.post(
                f"/accounts/types/{custom_type.id}",
                data={
                    "has_amortization": "true",
                    "category_id": str(liability_id),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"between a loan type and a non-loan type" in response.data
            db.session.refresh(custom_type)
            assert custom_type.has_amortization is False

    def test_type_edit_amortization_flip_allowed_holding_no_line(
        self, app, auth_client, seed_user,
    ):
        """The same flip's negative control: a $0.00 opening books no line, and passes."""
        with app.app_context():
            liability_id = ref_cache.acct_category_id(
                AcctCategoryEnum.LIABILITY,
            )
            custom_type = AccountType(
                name="store_card",
                category_id=liability_id,
                user_id=seed_user["user"].id,
            )
            db.session.add(custom_type)
            db.session.commit()
            account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=custom_type.id,
                    name="Empty Store Card",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.commit()

            response = auth_client.post(
                f"/accounts/types/{custom_type.id}",
                data={
                    "has_amortization": "true",
                    "category_id": str(liability_id),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data
            db.session.refresh(custom_type)
            assert custom_type.has_amortization is True

    def test_type_edit_category_flip_reclasses_unposted_accounts(
        self, app, auth_client, seed_user,
    ):
        """A category flip on a type whose accounts are unposted re-classes them.

        The type's only account has a $0 anchor (empty ledger), so the
        Asset -> Liability category flip is allowed and the account's empty
        linked row re-snapshots to the Liability class.
        """
        # Pylint: ``import-outside-toplevel`` -- localized to the one test
        # that needs these helpers, matching the file's convention.
        # pylint: disable=import-outside-toplevel
        from app.enums import LedgerAccountClassEnum
        from tests._test_helpers import linked_ledger_account

        with app.app_context():
            custom_type = AccountType(
                name="side_pocket",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                user_id=seed_user["user"].id,
            )
            db.session.add(custom_type)
            db.session.commit()
            account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=custom_type.id,
                    name="Unposted Pocket",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.commit()

            response = auth_client.post(
                f"/accounts/types/{custom_type.id}",
                data={
                    "category_id": str(ref_cache.acct_category_id(
                        AcctCategoryEnum.LIABILITY,
                    )),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data
            linked = linked_ledger_account(db.session, account.id)
            assert linked.class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.LIABILITY,
            )

    def test_update_account_anchor_edit_books_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The account EDIT door asserts no balance (plan step X-f1e, N-195).

        The inverse of the test this replaces, which asserted that POSTing an
        ``anchor_balance`` here booked a true-up correction and moved the
        Checking total from $1000.00 to $1500.00.  That door was the app's
        SECOND balance-assertion surface and it answered the same submission
        differently from the first, so it was deleted rather than aligned.

        Three things are checked because the deletion has three layers and any
        one of them alone would be a fence: the schema DISCARDS the field, so a
        forged submission cannot reach a writer; no assertion row is appended;
        and the posted ledger does not move.  The rest of the edit still
        applies, which is what says the route was narrowed rather than broken.
        """
        # Pylint: ``import-outside-toplevel`` -- localized to the one test
        # that needs this reader, matching the file's convention.
        # pylint: disable=import-outside-toplevel
        from app.services import posting_service

        with app.app_context():
            checking = seed_user["account"]
            checking_id = checking.id
            scenario_id = seed_user["scenario"].id
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            assertions_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=checking_id).count()
            )
            assert posting_service.account_posting_total(
                checking_id, scenario_id,
            ) == Decimal("1000.00")

            response = auth_client.post(
                f"/accounts/{checking_id}",
                data={
                    "name": "Renamed Checking",
                    "account_type_id": str(checking_type.id),
                    "anchor_balance": "1500.00",
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data
            db.session.expire_all()
            # The balance the form submitted reached nothing.
            assert posting_service.account_posting_total(
                checking_id, scenario_id,
            ) == Decimal("1000.00")
            assert (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=checking_id).count()
            ) == assertions_before
            # ...and the edit this door DOES own still landed.
            assert db.session.get(Account, checking_id).name == "Renamed Checking"
