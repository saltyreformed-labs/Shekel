"""
Shekel Budget App -- C-46 / F-145 Exception Narrowing Tests

Verifies that the post-C-46 narrowing of broad ``except Exception:``
blocks in salary, retirement, and investment routes correctly:

  1. catches SQLAlchemyError-family exceptions (DataError, FK
     violations, etc.) so the user-facing flash + redirect and the
     ``db.session.rollback()`` still run for every previously-
     swallowed DB-tier failure,
  2. allows non-SQLAlchemy exceptions (RuntimeError, TypeError,
     decimal arithmetic) to propagate to the Flask 500 handler
     instead of being silently swallowed as a generic flash.

For each previously-broad ``except Exception:`` site, one test
patches the relevant DB write to raise a ``DataError`` and asserts
the narrowed handler runs.  A representative test patches a write
to raise a ``RuntimeError`` and asserts the exception propagates
through the test client (proving the narrow catch does not swallow
programming bugs).

The retirement and investment routes use ``except InvalidOperation``
to narrow a ``Decimal(str) / Decimal("100")`` pre-validation step;
those handlers are covered by the existing
``test_update_settings_invalid_swr`` family in ``test_retirement.py``
plus a new test for ``investment._convert_percentage_inputs``.

Cross-references: F-145 (Low), C-46.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy.exc import DataError

from app.extensions import db
from app.models.investment_params import InvestmentParams
from app.models.paycheck_line import PaycheckLine
from app.models.ref import (
    AccountType, CalcMethod, PaycheckLineKind, FilingStatus, RaiseType,
)
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.services import account_service
from tests._stub_period import stub_period_on
from tests._test_helpers import freeze_today
from tests.test_routes.test_salary._helpers import _create_profile


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze today inside the seeded period range.

    Matches the same freeze used by ``test_salary/conftest.py`` so the
    ``_create_profile`` helper and ``seed_periods`` fixture cooperate
    deterministically regardless of wall-clock date.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))


def _make_data_error():
    """Construct a representative ``DataError`` for use as side_effect.

    The instance's class hierarchy matters (must be a
    ``SQLAlchemyError`` subclass to land in the narrowed catch);
    the statement, params, and orig fields are not inspected by the
    route handlers under test.
    """
    return DataError(
        statement="(C-46 narrow-catch regression test)",
        params=None,
        orig=Exception("simulated NUMERIC overflow"),
    )


# ── salary.py: routes wrapping db.session.commit() ─────────────────


class TestSalaryNarrowCatch:
    """Verify each ``except SQLAlchemyError`` block in salary.py.

    Each test patches ``db.session.commit`` to raise a ``DataError``
    so the narrowed catch fires.  The previously-broad
    ``except Exception:`` would have caught this too; the regression
    here is that the post-narrow handler still rolls back the
    session, flashes the user-facing message, and redirects to a
    safe page (and -- crucially -- that non-SQLAlchemy exceptions
    are no longer swept under the same generic flash).
    """

    def test_create_profile_data_error_handled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``DataError`` on ``create_profile`` commit triggers narrow catch."""
        with app.app_context():
            filing_status = db.session.query(FilingStatus).filter_by(
                name="single",
            ).one()

            with patch.object(
                db.session, "commit", side_effect=_make_data_error(),
            ):
                resp = auth_client.post(
                    "/salary",
                    data={
                        "name": "New Profile",
                        "pay_amount": "2884.62",  # $75,000.00 a year / 26
                        "pay_payday": "2026-01-02",  # the first period's payday
                        **stub_period_on("2026-01-02"),
                        "filing_status_id": filing_status.id,
                        "state_code": "NC",
                    },
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert "/salary/new" in resp.headers["Location"], (
                "create_profile must redirect to /salary/new on DB-tier "
                "failure so the user can retry with the same form."
            )

            # Rollback verified: no profile persisted under the
            # would-be name.
            db.session.expire_all()
            persisted = db.session.query(SalaryProfile).filter_by(
                user_id=seed_user["user"].id, name="New Profile",
            ).all()
            assert persisted == []

    def test_update_profile_data_error_handled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``DataError`` on ``update_profile`` commit triggers narrow catch."""
        with app.app_context():
            profile = _create_profile(seed_user)
            # The update takes no salary since plan step salary:X-av-3a, so
            # the rollback's witness is the name the POST changes.
            original_name = profile.name
            filing_status = db.session.query(FilingStatus).filter_by(
                name="single",
            ).one()

            with patch.object(
                db.session, "commit", side_effect=_make_data_error(),
            ):
                resp = auth_client.post(
                    f"/salary/{profile.id}",
                    data={
                        "name": "Renamed Job",
                        "filing_status_id": filing_status.id,
                        "state_code": "NC",
                    },
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert f"/salary/{profile.id}/edit" in resp.headers["Location"]

            # Rollback verified: the name unchanged.
            db.session.expire_all()
            refreshed = db.session.get(SalaryProfile, profile.id)
            assert refreshed.name == original_name

    def test_add_raise_data_error_handled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``DataError`` (non-Integrity) on ``add_raise`` commit triggers narrow catch.

        The ``except IntegrityError`` branch above the narrowed
        ``except SQLAlchemyError`` only handles ``IntegrityError``;
        ``DataError`` is a sibling subclass and must fall through to
        the narrow catch.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(
                name="merit",
            ).one()

            with patch.object(
                db.session, "commit", side_effect=_make_data_error(),
            ):
                resp = auth_client.post(
                    f"/salary/{profile.id}/raises",
                    data={
                        "raise_type_id": raise_type.id,
                        "effective_date": "2026-07-01",
                        "percentage": "3",
                    },
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert f"/salary/{profile.id}/edit" in resp.headers["Location"]

            # Rollback verified: no raise persisted.
            db.session.expire_all()
            persisted = db.session.query(SalaryRaise).filter_by(
                salary_profile_id=profile.id,
            ).all()
            assert persisted == []

    def test_delete_raise_data_error_handled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``DataError`` on ``delete_raise`` commit triggers narrow catch."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(
                name="merit",
            ).one()

            salary_raise = SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=raise_type.id,
                effective_date=date(2026, 6, 1),
                percentage=Decimal("0.0300"),
            )
            db.session.add(salary_raise)
            db.session.commit()
            raise_id = salary_raise.id

            with patch.object(
                db.session, "commit", side_effect=_make_data_error(),
            ):
                resp = auth_client.post(
                    f"/salary/raises/{raise_id}/delete",
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert f"/salary/{profile.id}/edit" in resp.headers["Location"]

            # Rollback verified: raise still present.
            db.session.expire_all()
            still_there = db.session.get(SalaryRaise, raise_id)
            assert still_there is not None

    def test_update_raise_data_error_handled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``DataError`` (non-Integrity) on ``update_raise`` commit triggers narrow catch."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(
                name="merit",
            ).one()
            cola_type = db.session.query(RaiseType).filter_by(
                name="cola",
            ).one()

            salary_raise = SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=raise_type.id,
                effective_date=date(2026, 6, 1),
                percentage=Decimal("0.0300"),
            )
            db.session.add(salary_raise)
            db.session.commit()
            raise_id = salary_raise.id
            original_pct = salary_raise.percentage

            with patch.object(
                db.session, "commit", side_effect=_make_data_error(),
            ):
                resp = auth_client.post(
                    f"/salary/raises/{raise_id}/edit",
                    data={
                        "raise_type_id": cola_type.id,
                        "effective_date": "2026-07-01",
                        "percentage": "5",
                    },
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert f"/salary/{profile.id}/edit" in resp.headers["Location"]

            # Rollback verified: raise unchanged.
            db.session.expire_all()
            refreshed = db.session.get(SalaryRaise, raise_id)
            assert refreshed.raise_type_id == raise_type.id
            assert refreshed.percentage == original_pct

    def test_add_deduction_data_error_handled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``DataError`` (non-Integrity) on ``add_line`` commit triggers narrow catch."""
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(
                name="pre_tax_deduction",
            ).one()
            flat_method = db.session.query(CalcMethod).filter_by(
                name="flat",
            ).one()

            with patch.object(
                db.session, "commit", side_effect=_make_data_error(),
            ):
                resp = auth_client.post(
                    f"/salary/{profile.id}/lines",
                    data={
                        "name": "401k",
                        "paycheck_line_kind_id": pre_tax.id,
                        "calc_method_id": flat_method.id,
                        "amount": "200.00",
                    },
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert f"/salary/{profile.id}/edit" in resp.headers["Location"]

            # Rollback verified: no deduction persisted.
            db.session.expire_all()
            persisted = db.session.query(PaycheckLine).filter_by(
                salary_profile_id=profile.id,
            ).all()
            assert persisted == []

    def test_delete_deduction_data_error_handled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``DataError`` on ``delete_line`` commit triggers narrow catch."""
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(
                name="pre_tax_deduction",
            ).one()
            flat_method = db.session.query(CalcMethod).filter_by(
                name="flat",
            ).one()

            deduction = PaycheckLine(
                salary_profile_id=profile.id,
                paycheck_line_kind_id=pre_tax.id,
                calc_method_id=flat_method.id,
                name="401k",
                amount=Decimal("200.0000"),
            )
            db.session.add(deduction)
            db.session.commit()
            ded_id = deduction.id

            with patch.object(
                db.session, "commit", side_effect=_make_data_error(),
            ):
                resp = auth_client.post(
                    f"/salary/lines/{ded_id}/delete",
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert f"/salary/{profile.id}/edit" in resp.headers["Location"]

            # Rollback verified: deduction still present.
            db.session.expire_all()
            still_there = db.session.get(PaycheckLine, ded_id)
            assert still_there is not None

    def test_update_deduction_data_error_handled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``DataError`` (non-Integrity) on ``update_line`` commit triggers narrow catch."""
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(
                name="pre_tax_deduction",
            ).one()
            flat_method = db.session.query(CalcMethod).filter_by(
                name="flat",
            ).one()

            deduction = PaycheckLine(
                salary_profile_id=profile.id,
                paycheck_line_kind_id=pre_tax.id,
                calc_method_id=flat_method.id,
                name="401k",
                amount=Decimal("200.0000"),
            )
            db.session.add(deduction)
            db.session.commit()
            ded_id = deduction.id
            original_amount = deduction.amount

            with patch.object(
                db.session, "commit", side_effect=_make_data_error(),
            ):
                resp = auth_client.post(
                    f"/salary/lines/{ded_id}/edit",
                    data={
                        "name": "401k",
                        "paycheck_line_kind_id": pre_tax.id,
                        "calc_method_id": flat_method.id,
                        "amount": "500.00",
                    },
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert f"/salary/{profile.id}/edit" in resp.headers["Location"]

            # Rollback verified: amount unchanged.
            db.session.expire_all()
            refreshed = db.session.get(PaycheckLine, ded_id)
            assert refreshed.amount == original_amount


# ── salary.py: private helper ──────────────────────────────────────


class TestRegenerateHelperNarrowCatch:
    """Verify the salary regeneration's narrowed catch.

    The walk logs ``SQLAlchemyError`` raised by
    ``recurrence_engine.regenerate_for_template`` (so operators see
    profile-id context) and re-raises so the calling route catches
    it.  Non-SQLAlchemy exceptions propagate without that
    log entry but the route handler still records them.  **It lives in
    ``app.services.salary_regeneration`` since plan step salary:S3-f-3**
    (ruling **R-SAL24**); the route package's ``_regenerate_salary_transactions``
    is the adapter that hands it the door's pass and flashes what it kept
    (it opened the pass itself until task plan#3106), and the patch below
    targets the engine call inside the service's ``try``.  A lost version
    race leaves the service by its own arm since task plan#3126, logged at
    INFO and never as an error, for the route's conflict handling:
    ``tests/test_services/test_salary_regeneration.py`` grades that arm, and
    ``test_the_rails_lost_race`` the rail's race.
    """

    def test_helper_logs_sqlalchemy_error_with_profile_id(
        self, app, auth_client, seed_user, seed_periods, caplog,
    ):
        """Helper logs the profile-id when regenerate raises SQLAlchemyError.

        Patches ``recurrence_engine.regenerate_for_template`` (one of
        the two calls inside the service's try block that reach the
        database: ``GenerationSchedule.for_pass`` derives the pass's
        calendar there when the door has not read it already, as this
        POST, which carries no pay-period pair, has not) to raise a
        ``DataError``, then drives the service through
        ``update_profile``.  The service's ``logger.exception`` must
        emit a record whose message includes the profile id, and the
        outer route's ``except SQLAlchemyError`` block then converts
        the re-raised error into the standard flash + redirect.
        """
        import logging
        with app.app_context():
            profile = _create_profile(seed_user)
            filing_status = db.session.query(FilingStatus).filter_by(
                name="single",
            ).one()

            with caplog.at_level(
                logging.ERROR, logger="app.services.salary_regeneration",
            ):
                with patch(
                    "app.services.salary_regeneration.recurrence_engine."
                    "regenerate_for_template",
                    side_effect=_make_data_error(),
                ):
                    resp = auth_client.post(
                        f"/salary/{profile.id}",
                        data={
                            "name": "Day Job",
                            "filing_status_id": filing_status.id,
                            "state_code": "NC",
                        },
                        follow_redirects=False,
                    )

            assert resp.status_code == 302
            assert f"/salary/{profile.id}/edit" in resp.headers["Location"]

            # Helper logged with profile id.
            helper_logs = [
                rec for rec in caplog.records
                if "Failed to regenerate salary transactions" in rec.getMessage()
                and str(profile.id) in rec.getMessage()
            ]
            assert len(helper_logs) >= 1, (
                "The helper's ``except SQLAlchemyError`` block must emit "
                "a ``logger.exception`` record naming the profile id so "
                "operators can correlate regeneration failures with the "
                "salary profile under maintenance."
            )


# ── salary.py: propagation guarantee for non-SQLAlchemy errors ─────


class TestNonSqlAlchemyErrorPropagates:
    """The narrowed catch must NOT swallow programming bugs.

    Pre-C-46 the broad ``except Exception:`` would have caught any
    runtime error and presented the same generic flash.  Post-C-46
    the narrow ``except SQLAlchemyError:`` lets non-SQLAlchemy
    exceptions propagate so they surface as 500s (test client raises
    in TESTING mode) instead of hiding behind a "Failed to ...
    Please try again." flash that an operator would never see in the
    logs as anything other than a routine user error.
    """

    def test_create_profile_runtime_error_propagates(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``RuntimeError`` from commit is no longer swallowed."""
        with app.app_context():
            filing_status = db.session.query(FilingStatus).filter_by(
                name="single",
            ).one()

            with patch.object(
                db.session, "commit",
                side_effect=RuntimeError("simulated programming bug"),
            ):
                with pytest.raises(RuntimeError, match="programming bug"):
                    auth_client.post(
                        "/salary",
                        data={
                            "name": "New Profile",
                            "pay_amount": "2884.62",  # $75,000.00 a year / 26
                            "pay_payday": "2026-01-02",  # the first period's payday
                            **stub_period_on("2026-01-02"),
                            "filing_status_id": filing_status.id,
                            "state_code": "NC",
                        },
                        follow_redirects=False,
                    )


# ── investment.py: ``except InvalidOperation`` pre-processing ──────


class TestInvestmentInvalidOperationCatch:
    """Verify ``_convert_percentage_inputs`` narrow catch.

    The helper converts percentage form inputs (e.g. ``"7"`` -> ``"0.07"``)
    before Marshmallow validation runs.  Non-numeric input raises
    ``decimal.InvalidOperation``; the narrow catch leaves the raw
    value in place so Marshmallow's "Not a valid number." message
    re-renders the form rather than silently swallowing the error.
    """

    def test_update_params_non_numeric_return_passes_to_validation(
        self, app, auth_client, seed_user,
    ):
        """Non-numeric ``assumed_annual_return`` reaches Marshmallow as-is."""
        with app.app_context():
            retirement_type = db.session.query(AccountType).filter_by(
                name="401(k)",
            ).first()
            assert retirement_type is not None, (
                "Reference data must seed a 401(k) account type for "
                "investment routes to exercise their narrow catch."
            )

            from app.models.account import Account
            account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=retirement_type.id,
                    name="My 401k",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(account)
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/{account.id}/investment/params",
                data={
                    "assumed_annual_return": "abc",
                    "annual_contribution_limit": "23000",
                    "contribution_limit_year": "2026",
                    # #38: employer type omitted -- the create schema
                    # defaults it to NONE; this test exercises the
                    # ``assumed_annual_return="abc"`` validation reject.
                },
                follow_redirects=False,
            )

            # Marshmallow rejects "abc" with the validation error
            # flash.  The route redirects to the dashboard.
            assert resp.status_code == 302
            assert f"/accounts/{account.id}/investment" in resp.headers["Location"]

            # No InvestmentParams row persisted.
            db.session.expire_all()
            persisted = db.session.query(InvestmentParams).filter_by(
                account_id=account.id,
            ).all()
            assert persisted == []
