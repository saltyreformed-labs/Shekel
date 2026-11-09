"""Account route tests: what an account's history refuses of a type change, and what
an allowed one re-reads.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.services.ledger_report_service import (
    StatementWindow,
    compute_balance_sheet,
    compute_income_statement,
)
from app.services.pay_calendar import calendar_for
from app.utils.dates import display_today
from tests._ledger_reads import account_net
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    create_settled_cash_transaction,
)
from app.models.ref import AccountType
from app.services import account_service, anchor_service


def _sheet_line(section, label):
    """Return the amount of the one line of a statement section with *label*."""
    amounts = [line.amount for line in section.lines if line.label == label]
    assert len(amounts) == 1, f"{label!r} in {[l.label for l in section.lines]}"
    return amounts[0]


def _income_lines(seed_user):
    """Return the income statement's lines as ``{label: amount}``, for the true-up's year."""
    owner_id = seed_user["user"].id
    report = compute_income_statement(
        owner_id, calendar_for(owner_id),
        StatementWindow("year", year=(display_today() - timedelta(days=1)).year),
    )
    return {line.label: line.amount for line in report.income.lines}


def _trued_up_account(seed_user, type_name, name, amounts):
    """Return an account opened at ``amounts[0]`` two days ago, at ``amounts[1]`` yesterday."""
    opening, trued_to = amounts
    account = create_account_of_type(
        seed_user, db.session, type_name, name,
        anchor_balance=Decimal(opening),
        observed_on=display_today() - timedelta(days=2),
    )
    anchor_service.apply_anchor_true_up(
        account=account, new_balance=Decimal(trued_to),
        observed_on=display_today() - timedelta(days=1),
    )
    return account


def _mistaken_card(seed_user):
    """Return a card set up as a HYSA: -$500.00 recorded, then -$530.00, $70.00 spent.

    Ruling plan#2785's example on a type that earns interest (ruling plan#2880), so
    its history is an opening, a true-up and a settled purchase: it reads
    ``-530.00 - 70.00 = -600.00``.
    """
    account = _trued_up_account(
        seed_user, "HYSA", "Mistaken Card", ("-500.00", "-530.00"),
    )
    create_settled_cash_transaction(
        seed_user, db.session, seed_user["bootstrap_period"], Decimal("70.00"),
        account=account, category=seed_user["categories"]["Groceries"],
        settled_on=display_today(),
    )
    db.session.commit()
    return account


def _assert_it_reads_as_interest_bearing(seed_user):
    """Assert the mistaken card reads -$600.00 in Assets, its true-up as interest; return equity.

    Assets are ``1,000.00 - 600.00 = 400.00``, Retained Earnings the true-up and the
    purchase, ``-30.00 - 70.00 = -100.00``, and equity
    ``1,000.00 - 500.00 - 100.00 = 400.00``.
    """
    before = compute_balance_sheet(seed_user["user"].id, display_today())
    assert _sheet_line(before.assets, "Mistaken Card") == Decimal("-600.00")
    assert before.assets.total == Decimal("400.00")
    assert _income_lines(seed_user) == {
        "Mistaken Card -- Interest Income": Decimal("-30.00"),
    }
    assert _sheet_line(before.equity, "Mistaken Card -- Opening") == Decimal("-500.00")
    assert _sheet_line(before.equity, "Retained Earnings") == Decimal("-100.00")
    return before.equity.total


def _assert_it_moved_to_liabilities(seed_user, equity_before):
    """Assert the mistaken card reads $600.00 owed, equity and the tie-out unchanged.

    Assets rise from ``400.00`` to the seeded Checking's ``1,000.00``, and Liabilities
    from nothing to ``600.00``, so ``1,000.00 = 600.00 + 400.00`` still holds with the
    same equity.  Re-read under a type that earns no interest (ruling plan#2880), the
    true-up leaves the income statement for the opening,
    ``-500.00 - 30.00 = -530.00``, and Retained Earnings holds the purchase alone.
    """
    after = compute_balance_sheet(seed_user["user"].id, display_today())
    assert [line.label for line in after.assets.lines] == ["Checking"]
    assert after.assets.total == Decimal("1000.00")
    assert _sheet_line(after.liabilities, "Mistaken Card") == Decimal("600.00")
    assert not _income_lines(seed_user)
    assert _sheet_line(after.equity, "Mistaken Card -- Opening") == Decimal("-530.00")
    assert _sheet_line(after.equity, "Retained Earnings") == Decimal("-70.00")
    assert after.equity.total == equity_before
    assert after.tie_out.in_balance is True


def _assert_the_true_up_reads(seed_user, opening, interest):
    """Assert Rainy's opening, and its true-up as interest or, with ``None``, not.

    The seeded Checking earns nothing, so Retained Earnings is that interest alone.
    """
    sheet = compute_balance_sheet(seed_user["user"].id, display_today())
    assert _sheet_line(sheet.equity, "Rainy -- Opening") == Decimal(opening)
    assert _income_lines(seed_user) == (
        {} if interest is None
        else {"Rainy -- Interest Income": Decimal(interest)}
    )
    assert _sheet_line(sheet.equity, "Retained Earnings") == Decimal(interest or "0.00")
    assert sheet.tie_out.in_balance is True
    return sheet.equity.total


def _type_edit_form(account_type, **changed):
    """Return what an account type's edit form submits, an unticked flag as ``false``."""
    flags = ("has_parameters", "has_amortization", "has_interest", "is_pretax", "is_liquid")
    return {
        "name": account_type.name,
        "category_id": str(account_type.category_id),
        "icon_class": account_type.icon_class or "",
        "max_term_months": "",
        **{flag: str(getattr(account_type, flag)).lower() for flag in flags},
        **changed,
    }


class TestTypeChangeBoundaryGuard:
    """What an account's history refuses of a type change (ruling plan#2785).

    Across a loan type and a non-loan type, re-typing an account or editing a
    custom type in place is refused while the account holds a line of the
    worked-out ledger: the kind decides which records its history is worked out
    from, so the crossing would drop its lines.  Across an asset type and a
    liability type nothing is refused, history or not: a line is classed by its
    account's current type, as it is labeled by its current name.  Either crossing
    on an account holding no line passes.

    Any allowed change of an account's kind re-reads its past true-ups under the
    new type, past income included (ruling plan#2880): a re-type means "it was
    always this type".
    """

    def test_retype_with_history_across_class_boundary_is_allowed(
        self, app, auth_client, seed_user,
    ):
        """Re-typing the mistaken HYSA to Credit Card moves it, equity unchanged.

        It read ``-600.00`` under Assets and reads ``600.00`` owed under Liabilities
        on every past balance sheet (ruling plan#2785), its true-up moves from
        interest to its opening on every past statement (ruling plan#2880).
        """
        with app.app_context():
            account = _mistaken_card(seed_user)
            account_id = account.id
            cc_type = (
                db.session.query(AccountType)
                .filter_by(name="Credit Card", user_id=None)
                .one()
            )
            equity_before = _assert_it_reads_as_interest_bearing(seed_user)

            response = auth_client.post(
                f"/accounts/{account_id}",
                data={"account_type_id": str(cc_type.id)},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data
            db.session.expire_all()
            assert db.session.get(Account, account_id).account_type_id == cc_type.id
            _assert_it_moved_to_liabilities(seed_user, equity_before)

    def test_type_edit_with_history_across_class_boundary_is_allowed(
        self, app, auth_client, seed_user,
    ):
        """Editing the mistaken account's custom type to a Liability moves it too.

        The other door to the same crossing, with the same figures: no
        ``account_type_id`` changes, only the type it is on, whose "Interest" tick
        goes with the Asset category that allows it.
        """
        with app.app_context():
            custom_type = AccountType(
                name="side_pocket",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                user_id=seed_user["user"].id,
                has_interest=True,
            )
            db.session.add(custom_type)
            db.session.commit()
            account = _mistaken_card(seed_user)
            account.account_type_id = custom_type.id
            db.session.commit()
            equity_before = _assert_it_reads_as_interest_bearing(seed_user)

            response = auth_client.post(
                f"/accounts/types/{custom_type.id}",
                data=_type_edit_form(
                    custom_type,
                    category_id=str(ref_cache.acct_category_id(
                        AcctCategoryEnum.LIABILITY,
                    )),
                    has_interest="false",
                ),
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data
            db.session.expire_all()
            _assert_it_moved_to_liabilities(seed_user, equity_before)

    @pytest.mark.parametrize(
        "case",
        [
            # Ruling plan#2880's example: ``1,040.00 - 1,000.00 = 40.00`` of interest
            # becomes opening, ``1,000.00 + 40.00 = 1,040.00``.
            pytest.param(
                ("HYSA", ("1000.00", "1040.00"), "Credit Card",
                 ("1000.00", "40.00"), ("1040.00", None)),
                id="asset-to-liability",
            ),
            # The same $40.00 with no crossing, which no refusal ever guarded.
            pytest.param(
                ("HYSA", ("1000.00", "1040.00"), "Savings",
                 ("1000.00", "40.00"), ("1040.00", None)),
                id="asset-to-asset",
            ),
            # The reverse: ``-530.00 - (-500.00) = -30.00`` was opening, and is
            # interest once the type earns it.
            pytest.param(
                ("Credit Card", ("-500.00", "-530.00"), "HYSA",
                 ("-530.00", None), ("-500.00", "-30.00")),
                id="liability-to-asset",
            ),
        ],
    )
    def test_retype_re_reads_past_true_ups(self, app, auth_client, seed_user, case):
        """A re-type moves a true-up between interest and opening, equity unchanged."""
        from_type, amounts, to_type, reads_before, reads_after = case
        with app.app_context():
            account = _trued_up_account(seed_user, from_type, "Rainy", amounts)
            account_id = account.id
            to_type_id = (
                db.session.query(AccountType)
                .filter_by(name=to_type, user_id=None)
                .one()
            ).id
            equity_before = _assert_the_true_up_reads(seed_user, *reads_before)

            response = auth_client.post(
                f"/accounts/{account_id}",
                data={"account_type_id": str(to_type_id)},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data
            db.session.expire_all()
            assert _assert_the_true_up_reads(seed_user, *reads_after) == equity_before

    def test_type_edit_unticking_interest_re_reads_past_true_ups(
        self, app, auth_client, seed_user,
    ):
        """Unticking "Interest" on a custom type moves a past true-up at once.

        The worked-out ledger reads the account's kind as it stands, so the edit
        re-reads its past true-up with nothing re-synced.  *Until plan task
        plan#3076 the edit re-synced the stored ledger only across a loan or an
        asset-and-liability crossing, so the store held the old reading here
        until the account's next balance (finding plan#2883); no door writes the
        store now.*
        """
        with app.app_context():
            custom_type = AccountType(
                name="side_pocket",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                user_id=seed_user["user"].id,
                has_interest=True,
            )
            db.session.add(custom_type)
            db.session.commit()
            account = _trued_up_account(
                seed_user, "HYSA", "Rainy", ("1000.00", "1040.00"),
            )
            account.account_type_id = custom_type.id
            db.session.commit()
            equity_before = _assert_the_true_up_reads(seed_user, "1000.00", "40.00")

            response = auth_client.post(
                f"/accounts/types/{custom_type.id}",
                data=_type_edit_form(custom_type, has_interest="false"),
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"updated" in response.data
            db.session.expire_all()
            assert _assert_the_true_up_reads(seed_user, "1040.00", None) == equity_before

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

    @pytest.mark.parametrize(
        "openings", (("0.00", "-250.00"), ("-250.00", "0.00")),
        ids=("the-second-holds-it", "the-first-holds-it"),
    )
    def test_type_edit_amortization_flip_refused_when_any_account_holds_a_line(
        self, app, auth_client, seed_user, openings,
    ):
        """Either of a type's two accounts holding the line refuses the flip.

        One holds a $0.00 opening, which books nothing, and the other $250.00 owed.
        The type is one row, so it cannot cross for the one and not the other.
        """
        with app.app_context():
            custom_type = AccountType(
                name="store_card",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY),
                user_id=seed_user["user"].id,
            )
            db.session.add(custom_type)
            db.session.commit()
            for number, opening in enumerate(openings):
                account_service.create_account(
                    account_service.AccountSpec(
                        user_id=seed_user["user"].id,
                        account_type_id=custom_type.id,
                        name=f"Store Card {number}",
                        anchor_balance=Decimal(opening),
                    ),
                )
            db.session.commit()

            response = auth_client.post(
                f"/accounts/types/{custom_type.id}",
                data=_type_edit_form(custom_type, has_amortization="true"),
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
            assert account_net(
                db.session, checking_id, scenario_id,
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
            assert account_net(
                db.session, checking_id, scenario_id,
            ) == Decimal("1000.00")
            assert (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=checking_id).count()
            ) == assertions_before
            # ...and the edit this door DOES own still landed.
            assert db.session.get(Account, checking_id).name == "Renamed Checking"
