"""Account route tests: where creating an account redirects, the interest and investment dispatch,
and the wizard banner.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import CompoundingFrequencyEnum, EmployerContributionTypeEnum
from app.exceptions import RequiredRecordMissing
from app.extensions import db
from app.models.account import Account
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.models.ref import AccountType
from app.services import account_service


# ── Account Creation Redirect Tests ──────────────────────────────


class TestAccountCreationRedirects:
    """Tests for post-creation redirect routing.

    Parameterized account types redirect to their configuration pages
    with setup=1.  Non-parameterized types redirect to the unified
    accounts cockpit (savings.dashboard), the retired /accounts table's
    successor (Loop B P4).
    """

    def test_hysa_creation_redirects_to_detail(
        self, app, auth_client, seed_user,
    ):
        """HYSA creation redirects to the cash detail page (setup=1) and auto-creates params.

        The Fable 5 overhaul merged the interest detail page into
        ``accounts.cash_detail`` (URL ``/accounts/<id>/details``), so the
        post-create redirect for an interest-bearing account lands there
        with ``setup=1`` for the wizard banner.
        """
        with app.app_context():
            hysa_type = db.session.query(AccountType).filter_by(name="HYSA").one()

            resp = auth_client.post("/accounts", data={
                "name": "My HYSA",
                "account_type_id": hysa_type.id,
                "anchor_balance": "5000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/details" in location
            assert "setup=1" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="My HYSA",
            ).one()
            assert db.session.query(InterestParams).filter_by(
                account_id=acct.id
            ).first() is not None

    def test_mortgage_creation_redirects_to_dashboard(
        self, app, auth_client, seed_user,
    ):
        """Mortgage creation redirects to loan dashboard with setup=1."""
        with app.app_context():
            mortgage_type = db.session.query(AccountType).filter_by(
                name="Mortgage"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Home Mortgage",
                "account_type_id": mortgage_type.id,
                "anchor_balance": "250000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/loan" in location
            assert "setup=1" in location

    def test_auto_loan_creation_redirects_to_dashboard(
        self, app, auth_client, seed_user,
    ):
        """Auto loan creation redirects to loan dashboard with setup=1."""
        with app.app_context():
            auto_loan_type = db.session.query(AccountType).filter_by(
                name="Auto Loan"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Car Loan",
                "account_type_id": auto_loan_type.id,
                "anchor_balance": "20000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/loan" in location
            assert "setup=1" in location

    def test_401k_creation_redirects_to_investment_dashboard(
        self, app, auth_client, seed_user,
    ):
        """401(k) creation redirects to investment dashboard and auto-creates InvestmentParams."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Work 401k",
                "account_type_id": k401_type.id,
                "anchor_balance": "10000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/investment" in location
            assert "setup=1" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Work 401k",
            ).one()
            assert db.session.query(InvestmentParams).filter_by(
                account_id=acct.id
            ).first() is not None

    def test_roth_ira_creation_redirects_to_investment_dashboard(
        self, app, auth_client, seed_user,
    ):
        """Roth IRA creation routes to investment dashboard with InvestmentParams."""
        with app.app_context():
            roth_ira_type = db.session.query(AccountType).filter_by(
                name="Roth IRA"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "My Roth IRA",
                "account_type_id": roth_ira_type.id,
                "anchor_balance": "5000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/investment" in location
            assert "setup=1" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="My Roth IRA",
            ).one()
            assert db.session.query(InvestmentParams).filter_by(
                account_id=acct.id
            ).first() is not None

    def test_brokerage_creation_redirects_to_investment_dashboard(
        self, app, auth_client, seed_user,
    ):
        """Brokerage creation routes to investment dashboard with InvestmentParams."""
        with app.app_context():
            brokerage_type = db.session.query(AccountType).filter_by(
                name="Brokerage"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "My Brokerage",
                "account_type_id": brokerage_type.id,
                "anchor_balance": "1000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/investment" in location
            assert "setup=1" in location

    def test_checking_creation_redirects_to_cockpit(
        self, app, auth_client, seed_user,
    ):
        """Checking (non-parameterized) creation redirects to the cockpit, no setup param."""
        with app.app_context():
            checking_type = db.session.query(AccountType).filter_by(
                name="Checking"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Secondary Checking",
                "account_type_id": checking_type.id,
                "anchor_balance": "0",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert location.endswith("/savings")
            assert "setup" not in location

    def test_savings_creation_redirects_to_cockpit(
        self, app, auth_client, seed_user,
    ):
        """Plain savings account creation redirects to the cockpit."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Emergency Fund",
                "account_type_id": savings_type.id,
                "anchor_balance": "0",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert location.endswith("/savings")
            assert "setup" not in location

    def test_student_loan_creation_redirects_to_loan(
        self, app, auth_client, seed_user,
    ):
        """Student loan creation redirects to loan dashboard for setup.

        Student loans have has_amortization=True and are now served by
        the unified loan routes.  They must not be routed to the
        investment dashboard.
        """
        with app.app_context():
            sl_type = db.session.query(AccountType).filter_by(
                name="Student Loan"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Student Loan",
                "account_type_id": sl_type.id,
                "anchor_balance": "30000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "investment" not in location
            assert "/loan" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Student Loan",
            ).one()
            assert db.session.query(InvestmentParams).filter_by(
                account_id=acct.id
            ).first() is None

    def test_personal_loan_creation_no_investment_params(
        self, app, auth_client, seed_user,
    ):
        """Personal loan creation does NOT create InvestmentParams."""
        with app.app_context():
            pl_type = db.session.query(AccountType).filter_by(
                name="Personal Loan"
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "Personal Loan",
                "account_type_id": pl_type.id,
                "anchor_balance": "5000.00",
            })

            assert resp.status_code == 302
            assert "investment" not in resp.headers["Location"]

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Personal Loan",
            ).one()
            assert db.session.query(InvestmentParams).filter_by(
                account_id=acct.id
            ).first() is None

    def test_investment_params_not_duplicated(
        self, app, auth_client, seed_user,
    ):
        """Auto-creation of InvestmentParams produces exactly one record."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()

            auth_client.post("/accounts", data={
                "name": "Dupe Test 401k",
                "account_type_id": k401_type.id,
                "anchor_balance": "10000.00",
            })

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Dupe Test 401k",
            ).one()

            count = db.session.query(InvestmentParams).filter_by(
                account_id=acct.id
            ).count()
            assert count == 1

    def test_investment_params_defaults_are_reasonable(
        self, app, auth_client, seed_user,
    ):
        """Auto-created InvestmentParams have sensible default values."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()

            auth_client.post("/accounts", data={
                "name": "Default 401k",
                "account_type_id": k401_type.id,
                "anchor_balance": "0",
            })

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Default 401k",
            ).one()
            params = db.session.query(InvestmentParams).filter_by(
                account_id=acct.id,
            ).one()

            assert params.assumed_annual_return == Decimal("0.07000")
            assert params.employer_contribution_type_id == (
                ref_cache.employer_contribution_type_id(
                    EmployerContributionTypeEnum.NONE,
                )
            )
            assert params.assumed_annual_return >= 0


# ── Metadata-Driven Interest Dispatch ────────────────────────────


class TestInterestDispatch:
    """Verify that has_interest metadata flag drives auto-creation,
    redirect, and detail page access instead of hardcoded HYSA type ID."""

    def test_create_account_hsa_auto_creates_interest_params(
        self, app, auth_client, seed_user,
    ):
        """HSA has has_interest=True; creating one auto-creates InterestParams."""
        with app.app_context():
            hsa_type = db.session.query(AccountType).filter_by(name="HSA").one()
            assert hsa_type.has_interest is True

            resp = auth_client.post("/accounts", data={
                "name": "My HSA",
                "account_type_id": hsa_type.id,
                "anchor_balance": "1200.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/details" in location
            assert "setup=1" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="My HSA",
            ).one()
            params = db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first()
            assert params is not None, "InterestParams not auto-created for HSA"

    def test_create_account_money_market_with_interest(
        self, app, auth_client, seed_user, db,
    ):
        """Money Market with has_interest=True auto-creates InterestParams."""
        with app.app_context():
            mm_type = db.session.query(AccountType).filter_by(
                name="Money Market",
            ).one()
            mm_type.has_interest = True
            mm_type.has_parameters = True
            db.session.commit()

            resp = auth_client.post("/accounts", data={
                "name": "My MM",
                "account_type_id": mm_type.id,
                "anchor_balance": "3000.00",
            })

            assert resp.status_code == 302
            assert "/details" in resp.headers["Location"]

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="My MM",
            ).one()
            params = db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first()
            assert params is not None

    def test_interest_detail_accepts_any_interest_type(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """Cash detail page renders (with APY) for any has_interest=True type."""
        with app.app_context():
            hsa_type = db.session.query(AccountType).filter_by(name="HSA").one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hsa_type.id,
                    name="HSA Detail Test",
                    anchor_balance=500,
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InterestParams(
                account_id=acct.id, apy=Decimal("0.04500"),  # HIGH-06: apy NOT NULL, no server_default
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200
            # The interest-bearing variant shows the parameters card.
            assert b"APY" in resp.data

    def test_has_interest_true_but_no_params_row(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """Cash detail REFUSES a missing params row; it does not manufacture one.

        **This test asserted the opposite until plan step balance:X-i3**, which
        deleted the auto-create branch on the developer's ruling.  The old
        behaviour was a render repairing data: it wrote inside a read, which
        costs the page the one snapshot every figure on it is computed against,
        and it hid the door that should have written the row (the type-change
        gap the sibling test below now covers).

        A zero-rate row manufactured here renders on screen exactly like a rate
        the owner configured, so the refusal is the honest answer and the
        message names the repair.
        """
        with app.app_context():
            hsa_type = db.session.query(AccountType).filter_by(name="HSA").one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hsa_type.id,
                    name="HSA No Params",
                    anchor_balance=100,
                ),
            )
            db.session.add(acct)
            db.session.commit()

            # No InterestParams row exists yet.  ``account_service`` builds the
            # Account; the params row is the ACCOUNT ROUTE's, which is why this
            # state is reachable from the service and not from a browser.
            assert db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first() is None

            with pytest.raises(RequiredRecordMissing, match="interest_params"):
                auth_client.get(f"/accounts/{acct.id}/details")

            # And it manufactured NOTHING on the way out.
            assert db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first() is None

    def test_editing_the_TYPE_into_an_interest_kind_seeds_every_account_on_it(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """The THIRD door, and the one the step's own first draft missed.

        Plan step balance:X-i3.  ``POST /accounts/types/<id>`` may flip
        ``has_interest`` on an owner's OWN custom type, which changes the
        projection kind of every account already on it -- with no
        ``account_type_id`` change for the account-update door to see and no
        account row touched at all.  Deleting the detail page's auto-create
        without holding the rule here would have turned a legitimate settings
        edit into a 500 on the account's own page.

        Asserted through the RENDER, not just the row: the page is what the
        deleted repair existed to keep working.
        """
        with app.app_context():
            plain_type = AccountType(
                user_id=seed_user["user"].id,
                name="My Cash Pot",
                category_id=db.session.query(AccountType).filter_by(
                    name="Checking",
                ).one().category_id,
            )
            db.session.add(plain_type)
            db.session.flush()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=plain_type.id,
                    name="Cash Pot",
                    anchor_balance=Decimal("100.00"),
                ),
            )
            db.session.add(acct)
            db.session.commit()
            assert db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first() is None, "a plain custom type starts with no params row"

            resp = auth_client.post(f"/accounts/types/{plain_type.id}", data={
                "name": plain_type.name,
                "category_id": plain_type.category_id,
                "has_interest": "true",
            }, follow_redirects=True)
            assert resp.status_code == 200
            db.session.expire_all()
            # The precondition, asserted rather than assumed: a schema refusal
            # here flashes and redirects, which ``follow_redirects`` would also
            # render as a 200.
            assert db.session.get(AccountType, plain_type.id).has_interest

            params = db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).one()
            assert params.apy == Decimal("0")
            assert auth_client.get(
                f"/accounts/{acct.id}/details",
            ).status_code == 200

    def test_reclassing_into_an_interest_type_seeds_its_params_row(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """The gap the deleted auto-create was covering, closed at its door.

        Plan step balance:X-i3.  The seeder had exactly ONE caller -- account
        CREATION -- so an account re-classed into a parameterised kind carried
        no params row, and two detail pages each repaired it on a GET.
        ``update_account`` now seeds through the shared
        ``_type_params.ensure_type_params``, so the invariant holds at the door
        that establishes the kind.

        The FIRING half matters: without the seeding line this test fails at
        the render below, which is the state the auto-create used to hide.
        """
        with app.app_context():
            checking = seed_user["account"]
            hsa_type = db.session.query(AccountType).filter_by(name="HSA").one()
            assert db.session.query(InterestParams).filter_by(
                account_id=checking.id,
            ).first() is None, "a Checking account starts with no params row"

            resp = auth_client.post(f"/accounts/{checking.id}", data={
                "name": checking.name,
                "account_type_id": hsa_type.id,
                "version_id": checking.version_id,
            }, follow_redirects=True)
            assert resp.status_code == 200

            params = db.session.query(InterestParams).filter_by(
                account_id=checking.id,
            ).one()
            # The explicit zero sentinel the create door uses (E-12 / HIGH-06):
            # a missing rate is never projected as a server-default one.
            assert params.apy == Decimal("0")

            # And the page the missing row used to repair now renders.
            assert auth_client.get(
                f"/accounts/{checking.id}/details",
            ).status_code == 200


# ── Investment Dispatch (Metadata-Driven) ────────────────────────


class TestInvestmentDispatch:
    """Verify that investment/retirement auto-creation and redirect use
    metadata flags instead of hardcoded type ID frozensets."""

    def test_create_account_user_type_retirement_auto_creates_params(
        self, app, auth_client, seed_user, db,
    ):
        """A user-created Retirement type with has_parameters=True auto-creates
        InvestmentParams and redirects to the investment dashboard."""
        from app import ref_cache
        from app.enums import AcctCategoryEnum

        with app.app_context():
            custom_type = AccountType(
                name="TestSEPIRA",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT),
                has_parameters=True,
            )
            db.session.add(custom_type)
            db.session.commit()

            resp = auth_client.post("/accounts", data={
                "name": "My SEP IRA",
                "account_type_id": custom_type.id,
                "anchor_balance": "10000.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/investment" in location
            assert "setup=1" in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="My SEP IRA",
            ).one()
            params = db.session.query(InvestmentParams).filter_by(
                account_id=acct.id,
            ).first()
            assert params is not None, "InvestmentParams not auto-created"

    def test_has_parameters_false_no_auto_create(
        self, app, auth_client, seed_user,
    ):
        """An account type with has_parameters=False gets no params and
        redirects to the cockpit (savings.dashboard)."""
        with app.app_context():
            # Savings has has_parameters=False.
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            assert savings_type.has_parameters is False

            resp = auth_client.post("/accounts", data={
                "name": "Plain Savings",
                "account_type_id": savings_type.id,
                "anchor_balance": "500.00",
            })

            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert "/savings" in location
            assert "setup" not in location

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Plain Savings",
            ).one()
            assert db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).first() is None
            assert db.session.query(InvestmentParams).filter_by(
                account_id=acct.id,
            ).first() is None

    def test_529_plan_has_parameters_true_in_seed(self, app, seed_user):
        """529 Plan has has_parameters=True in seed data."""
        with app.app_context():
            plan_type = db.session.query(AccountType).filter_by(
                name="529 Plan",
            ).one()
            assert plan_type.has_parameters is True

    def test_create_account_529_auto_creates_investment_params(
        self, app, auth_client, seed_user,
    ):
        """529 Plan auto-creates InvestmentParams and redirects to investment dashboard."""
        with app.app_context():
            plan_type = db.session.query(AccountType).filter_by(
                name="529 Plan",
            ).one()

            resp = auth_client.post("/accounts", data={
                "name": "College Fund",
                "account_type_id": plan_type.id,
                "anchor_balance": "2000.00",
            })

            assert resp.status_code == 302
            assert "/investment" in resp.headers["Location"]

            acct = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="College Fund",
            ).one()
            assert db.session.query(InvestmentParams).filter_by(
                account_id=acct.id,
            ).first() is not None


# ── Wizard Banner Tests ──────────────────────────────────────────


class TestWizardBanner:
    """Tests for the setup wizard banner on parameter pages.

    The banner appears when ?setup=1 is in the query string, indicating
    the user just created the account and should review configuration.
    """

    def test_wizard_banner_shown_on_hysa_with_setup_param(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """HYSA detail page shows wizard banner when ?setup=1 is present."""
        with app.app_context():
            hysa_type = db.session.query(AccountType).filter_by(
                name="HYSA"
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hysa_type.id,
                    name="Banner HYSA",
                    anchor_balance=Decimal("5000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InterestParams(
                account_id=acct.id, apy=Decimal("0.04500"),  # HIGH-06: apy NOT NULL, no server_default
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details?setup=1")
            assert resp.status_code == 200
            assert b"Configure the settings below" in resp.data
            assert b"alert-dismissible" in resp.data

    def test_wizard_banner_hidden_on_hysa_without_setup_param(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """HYSA detail page does NOT show wizard banner without ?setup=1."""
        with app.app_context():
            hysa_type = db.session.query(AccountType).filter_by(
                name="HYSA"
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hysa_type.id,
                    name="No Banner HYSA",
                    anchor_balance=Decimal("5000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InterestParams(
                account_id=acct.id, apy=Decimal("0.04500"),  # HIGH-06: apy NOT NULL, no server_default
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200
            assert b"Configure the settings below" not in resp.data

    def test_wizard_banner_shown_on_investment_with_setup_param(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Investment dashboard shows wizard banner when ?setup=1 is present."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=k401_type.id,
                    name="Banner 401k",
                    anchor_balance=Decimal("10000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InvestmentParams(
                account_id=acct.id,
                employer_contribution_type_id=ref_cache.employer_contribution_type_id(
                    EmployerContributionTypeEnum.NONE,
                ),
            ))
            db.session.commit()

            resp = auth_client.get(
                f"/accounts/{acct.id}/investment?setup=1"
            )
            assert resp.status_code == 200
            assert b"Configure the settings below" in resp.data
            assert b"alert-dismissible" in resp.data

    def test_wizard_banner_hidden_on_investment_without_setup_param(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Investment dashboard does NOT show wizard banner without ?setup=1."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=k401_type.id,
                    name="No Banner 401k",
                    anchor_balance=Decimal("10000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InvestmentParams(
                account_id=acct.id,
                employer_contribution_type_id=ref_cache.employer_contribution_type_id(
                    EmployerContributionTypeEnum.NONE,
                ),
            ))
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/investment")
            assert resp.status_code == 200
            assert b"Configure the settings below" not in resp.data
