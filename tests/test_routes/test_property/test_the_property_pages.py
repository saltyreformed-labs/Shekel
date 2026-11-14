"""Creating a Property, its detail page and rate form, the loan's collateral link, and deletion."""

from decimal import Decimal

import pytest

from app.exceptions import RequiredRecordMissing
from app.models.account import Account
from app.models.asset_appreciation_params import AssetAppreciationParams
from app.models.investment_params import InvestmentParams
from app.models.ref import AccountType
from tests._test_helpers import create_loan_account

from ._helpers import _property_type_id, _make_property


class TestCreateProperty:
    """Creating a Property routes through the appreciation setup page."""

    def test_create_auto_creates_params_and_redirects(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """Property create seeds a zero-rate params row, redirects to setup."""
        with app.app_context():
            type_id = _property_type_id(db)

        resp = auth_client.post("/accounts", data={
            "name": "My House",
            "account_type_id": type_id,
            "anchor_balance": "400000.00",
        })
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "/property" in location
        assert "setup=1" in location

        with app.app_context():
            acct = (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, name="My House")
                .one()
            )
            params = (
                db.session.query(AssetAppreciationParams)
                .filter_by(account_id=acct.id)
                .first()
            )
            assert params is not None
            # E-12 zero sentinel until the user sets a real rate.
            assert params.annual_appreciation_rate == Decimal("0")
            # Regression: a Property must NOT be auto-created as an investment.
            assert (
                db.session.query(InvestmentParams)
                .filter_by(account_id=acct.id)
                .first()
                is None
            )


class TestPropertyDetailPage:
    """The detail page renders the equity figures and the rate form."""

    def test_detail_renders_equity(self, app, auth_client, db, seed_user, seed_periods_today):
        """GET the property page shows market value, equity, and LTV.

        Label strings match the Fable 5 band rebuild
        (docs/design/account_detail_audit.md, Surface 5 verdict +
        decisions 6-7): the hero label is "Equity" and the supporting
        chips are "Market value" / "Secured debt" / "Loan-to-value".
        """
        with app.app_context():
            acct = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
            )
            acct_id = acct.id

        resp = auth_client.get(f"/accounts/{acct_id}/property")
        assert resp.status_code == 200
        body = resp.data
        assert b"Equity" in body
        assert b"Market value" in body
        assert b"Secured debt" in body
        assert b"Loan-to-value" in body
        assert b"Secured by" in body
        # Market value renders (entered as 400000.00).
        assert b"400,000" in body

    def test_the_page_REFUSES_a_missing_params_row_rather_than_creating_one(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """The appreciation twin of the interest refusal (plan step X-i3).

        This page auto-created an ``asset_appreciation_params`` row on a GET
        until that step deleted the branch: a render that repairs data is a
        write inside a read, and it cost the page the one snapshot its equity
        and LTV figures are computed against.  The rule is held at the doors
        that set an account's kind instead
        (:mod:`app.services.account_params`).

        A manufactured zero-rate row renders on screen exactly like a rate the
        owner configured, which is why the refusal is the honest answer.
        """
        with app.app_context():
            acct = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
            )
            acct_id = acct.id
            db.session.query(AssetAppreciationParams).filter_by(
                account_id=acct_id,
            ).delete()
            db.session.commit()

        with pytest.raises(
            RequiredRecordMissing, match="asset_appreciation_params",
        ):
            auth_client.get(f"/accounts/{acct_id}/property")

        with app.app_context():
            assert db.session.query(AssetAppreciationParams).filter_by(
                account_id=acct_id,
            ).first() is None, "the render manufactured a row on its way out"

    def test_reclassing_INTO_a_property_type_seeds_its_params_row(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """The APPRECIATING arm of the shared seeder, at the re-class door.

        Its sibling in ``test_accounts/test_creation_redirects.py`` covers the
        INTEREST arm; this is the one the deleted ``property_detail``
        auto-create was standing in for, and without it only one of the
        seeder's three arms is exercised anywhere.
        """
        with app.app_context():
            checking = seed_user["account"]
            property_type = db.session.query(AccountType).filter_by(
                name="Property",
            ).one()
            assert db.session.query(AssetAppreciationParams).filter_by(
                account_id=checking.id,
            ).first() is None

            resp = auth_client.post(f"/accounts/{checking.id}", data={
                "name": checking.name,
                "account_type_id": property_type.id,
                "version_id": checking.version_id,
            }, follow_redirects=True)
            assert resp.status_code == 200

            params = db.session.query(AssetAppreciationParams).filter_by(
                account_id=checking.id,
            ).one()
            assert params.annual_appreciation_rate == Decimal("0")

    def test_update_appreciation_rate(self, app, auth_client, db, seed_user, seed_periods_today):
        """POSTing a percent rate stores the decimal fraction."""
        with app.app_context():
            acct = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.00000"),
            )
            acct_id = acct.id

        resp = auth_client.post(
            f"/accounts/{acct_id}/property/params",
            data={"appreciation_rate": "3.5"},
        )
        assert resp.status_code == 302
        with app.app_context():
            params = (
                db.session.query(AssetAppreciationParams)
                .filter_by(account_id=acct_id)
                .one()
            )
            # 3.5% entered -> stored as the 0.035 decimal fraction.
            assert params.annual_appreciation_rate == Decimal("0.03500")


class TestCollateralLinkRoute:
    """The loan-side "secured by" picker sets and clears the link."""

    def test_link_loan_to_property(self, app, auth_client, db, seed_user, seed_periods_today):
        """Linking a loan to a Property sets the FK and the secured_loans backref."""
        with app.app_context():
            prop = _make_property(db, seed_user, seed_periods_today)
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            prop_id, loan_id = prop.id, loan.id

        resp = auth_client.post(
            f"/accounts/{loan_id}/loan/collateral",
            data={"collateral_account_id": str(prop_id)},
        )
        assert resp.status_code == 302
        with app.app_context():
            loan = db.session.get(Account, loan_id)
            assert loan.collateral_account_id == prop_id
            prop = db.session.get(Account, prop_id)
            assert loan_id in [secured.id for secured in prop.secured_loans]

    def test_clear_link(self, app, auth_client, db, seed_user, seed_periods_today):
        """Submitting an empty value clears an existing link."""
        with app.app_context():
            prop = _make_property(db, seed_user, seed_periods_today)
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            loan.collateral_account_id = prop.id
            db.session.commit()
            loan_id = loan.id

        resp = auth_client.post(
            f"/accounts/{loan_id}/loan/collateral",
            data={"collateral_account_id": ""},
        )
        assert resp.status_code == 302
        with app.app_context():
            loan = db.session.get(Account, loan_id)
            assert loan.collateral_account_id is None

    def test_a_value_that_names_no_id_is_refused_and_the_link_survives(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """Plan step X-ae: a malformed submission is not a clear.

        Two things were wrong here at once.  A superscript two passes
        ``str.isdigit()`` and makes ``int()`` raise, so this exact request
        was an unhandled 500 (finding N-136); and the route's stated
        fallback for a bad value -- clear the link -- meant a forged field
        silently destroyed a real link under a "Secured-by link updated."
        flash.  "" is the picker's own blank option and still clears; a
        value the picker cannot emit now gets the same answer as an id
        naming no account, and the loan keeps what it had.
        """
        with app.app_context():
            prop = _make_property(db, seed_user, seed_periods_today)
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            loan.collateral_account_id = prop.id
            db.session.commit()
            prop_id, loan_id = prop.id, loan.id

        resp = auth_client.post(
            f"/accounts/{loan_id}/loan/collateral",
            data={"collateral_account_id": "\N{SUPERSCRIPT TWO}"},
            follow_redirects=True,
        )

        assert resp.status_code == 200
        assert b"Invalid linked account." in resp.data
        assert b"Secured-by link updated." not in resp.data
        with app.app_context():
            loan = db.session.get(Account, loan_id)
            assert loan.collateral_account_id == prop_id

    def test_a_whitespace_only_value_does_not_clear_the_link(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """A forged space is not the picker's blank option.

        Caught by adversarial review of the first build, which kept the
        route's original ``.strip()``.  ``str.strip`` removes UNICODE
        whitespace, so ``"\\xa0"``, ``"\\u3000"`` and ``" "`` all became
        ``""`` and took the clear path -- destroying a real link under a
        "Secured-by link updated." flash, which is verbatim the behaviour
        this route's docstring says is closed.  The ``<select>`` emits
        ``value=""`` and nothing else, so only ``""`` clears.
        """
        with app.app_context():
            prop = _make_property(db, seed_user, seed_periods_today)
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            loan.collateral_account_id = prop.id
            db.session.commit()
            prop_id, loan_id = prop.id, loan.id

        for blank in (" ", " ", "　"):
            # The premise: each of these would have looked empty after a strip.
            assert blank.strip() == ""
            resp = auth_client.post(
                f"/accounts/{loan_id}/loan/collateral",
                data={"collateral_account_id": blank},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Invalid linked account." in resp.data
            with app.app_context():
                loan = db.session.get(Account, loan_id)
                assert loan.collateral_account_id == prop_id, (
                    f"{blank!r} cleared the link"
                )

    def test_an_absent_field_does_not_clear_the_link(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """A POST with no ``collateral_account_id`` at all is not a clear.

        The second adversarial review's finding, one input over from the
        whitespace case: the route read the field with a ``""`` default, so
        an ABSENT field took the same clear path and destroyed a real link
        under a success flash.  The ``<select>`` is always submitted by a
        browser rendering this form, so an absent field is the same forged
        or truncated POST the guard refuses -- only a submitted ``""``, the
        picker's own blank option, clears.
        """
        with app.app_context():
            prop = _make_property(db, seed_user, seed_periods_today)
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            loan.collateral_account_id = prop.id
            db.session.commit()
            prop_id, loan_id = prop.id, loan.id

        resp = auth_client.post(
            f"/accounts/{loan_id}/loan/collateral",
            data={},
            follow_redirects=True,
        )

        assert resp.status_code == 200
        assert b"Invalid linked account." in resp.data
        assert b"Secured-by link updated." not in resp.data
        with app.app_context():
            loan = db.session.get(Account, loan_id)
            assert loan.collateral_account_id == prop_id

    def test_a_padded_id_is_refused_like_every_other_door(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """Four doors, ONE rule -- which a pre-normalizing door defeats.

        The first build stripped here and nowhere else, so ``" 2 "`` linked
        at this door while the reconcile and companion doors refused it.
        The shared rule is the deliverable of this step; a door that
        normalizes before applying it is not sharing it.
        """
        with app.app_context():
            prop = _make_property(db, seed_user, seed_periods_today)
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            db.session.commit()
            prop_id, loan_id = prop.id, loan.id

        resp = auth_client.post(
            f"/accounts/{loan_id}/loan/collateral",
            data={"collateral_account_id": f"  {prop_id}  "},
            follow_redirects=True,
        )

        assert resp.status_code == 200
        assert b"Invalid linked account." in resp.data
        with app.app_context():
            loan = db.session.get(Account, loan_id)
            assert loan.collateral_account_id is None

    def test_an_id_spelled_in_another_digit_script_is_refused(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """One account id has ONE spelling (X-ae's ASCII ruling).

        Eastern Arabic numerals pass ``isdigit()`` and convert cleanly, so a
        crash-only fix would have accepted this as the property's real id.
        The picker emits ``str(int)``; anything else names no id.
        """
        with app.app_context():
            prop = _make_property(db, seed_user, seed_periods_today)
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            db.session.commit()
            prop_id, loan_id = prop.id, loan.id

        eastern_arabic = str(prop_id).translate(
            str.maketrans("0123456789", "٠١٢٣٤"
                                        "٥٦٧٨٩"),
        )
        assert int(eastern_arabic) == prop_id

        resp = auth_client.post(
            f"/accounts/{loan_id}/loan/collateral",
            data={"collateral_account_id": eastern_arabic},
            follow_redirects=True,
        )

        assert resp.status_code == 200
        assert b"Invalid linked account." in resp.data
        with app.app_context():
            loan = db.session.get(Account, loan_id)
            assert loan.collateral_account_id is None


class TestPropertyDeletion:
    """Deleting a Property clears the link (SET NULL) and its params row."""

    def test_delete_property_nulls_loan_link(self, app, db, seed_user, seed_periods_today):
        """ON DELETE SET NULL keeps the loan alive with its link cleared."""
        with app.app_context():
            # No appreciation params row -> a plain ORM delete suffices to
            # exercise the DB-level SET NULL on the loan's FK.
            prop = _make_property(db, seed_user, seed_periods_today)
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            loan.collateral_account_id = prop.id
            db.session.commit()
            loan_id = loan.id

            db.session.delete(prop)
            db.session.commit()

            survived = db.session.get(Account, loan_id)
            assert survived is not None
            assert survived.collateral_account_id is None

    def test_hard_delete_property_cleans_up_params(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """Hard-deleting a Property removes its appreciation params row."""
        with app.app_context():
            # $0 anchor: a non-zero anchor posts its Step-5 opening and the
            # hard delete would archive instead (Guard 5); the subject here
            # is the params-row cleanup on a real delete.
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
                anchor_balance=Decimal("0.00"),
            )
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            loan.collateral_account_id = prop.id
            db.session.commit()
            prop_id, loan_id = prop.id, loan.id

        resp = auth_client.post(f"/accounts/{prop_id}/hard-delete")
        assert resp.status_code == 302
        with app.app_context():
            # The Property and its params row are gone; the loan survives with
            # its link nulled by the FK.
            assert db.session.get(Account, prop_id) is None
            assert (
                db.session.query(AssetAppreciationParams)
                .filter_by(account_id=prop_id)
                .first()
                is None
            )
            survived = db.session.get(Account, loan_id)
            assert survived is not None
            assert survived.collateral_account_id is None
