"""The salary regeneration is a service that takes the pass and returns what it kept.

Plan step **salary:S3-f-3**, ruling **R-SAL24**: the walk every salary write is
followed by moved below the route layer so the ``/retirement`` rail's end-year
Save could reach it.  What these cases grade is the CONTRACT the move changed
-- the two things the route-tier original did with Flask that the service now
does with plain data:

* it takes the request's :class:`~app.services.balance_at.BalanceContext`
  rather than building one from ``current_user`` (a producer below the route
  takes the pass; only a route builds one), so the owner it prices for is the
  pass's;
* it RETURNS the ids of the rows the regeneration declined to touch rather
  than flashing them, so each route reports them in its own voice -- and a
  retained row that was dropped on the way out would be a silent no-op
  (plan step R10-a).

What it does with a template-backed profile end to end -- the regenerated
rows -- is graded where the doors are: ``tests/test_routes/test_salary/`` for
the salary page and
``tests/test_routes/test_retirement.py::TestTheRailSavesARaisesEndYear`` for
the rail.  Both observed the template's amount move until plan step X-bp,
which deleted the re-statement (:meth:`TestTheServiceTakesThePassAndReturnsWhatItKept.test_it_prices_no_paycheck_and_states_no_amount`);
they read the paycheck a fresh pass prices instead.
"""

from decimal import Decimal

import pytest
from sqlalchemy.exc import DataError

from app.exceptions import RecurrenceConflict
from app.extensions import db
from app.services import salary_regeneration, template_amount_service
from app.services.balance_at import BalanceContext
from tests._test_helpers import make_salary_profile


def _linked_profile(seed_user, db_session):
    """An active profile priced through a template with a cadence."""
    # pylint: disable=import-outside-toplevel  -- the test-helper module's
    # own convention, so this file's import block stays the service's.
    from app import ref_cache
    from app.enums import TxnTypeEnum
    from app.models.transaction_template import TransactionTemplate
    from tests._test_helpers import make_every_period_rule

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=next(iter(seed_user["categories"].values())).id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
        name="Paycheck",
    )
    db_session.add(template)
    db_session.flush()
    make_every_period_rule(db_session, template)
    profile = make_salary_profile(seed_user, db_session)
    profile.template_id = template.id
    db_session.flush()
    return profile


class TestTheServiceTakesThePassAndReturnsWhatItKept:
    """The contract the move from the route tier changed (R-SAL24)."""

    def test_the_retained_ids_come_back_rather_than_being_flashed(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """A conflict's retained rows are the return value, in full.

        The override / soft-delete halves of a ``RecurrenceConflict`` are
        deliberately absorbed -- a salary regeneration preserves them and
        there is nothing to decide -- but the RETAINED half is the owner's to
        hear, so every id is handed back for the route to flash.
        """
        with app.app_context():
            profile = _linked_profile(seed_user, db.session)
            db.session.commit()
            seen = {}

            def _conflict(template, schedule, scenario_id, *, effective_from):
                seen["template"] = template
                raise RecurrenceConflict(
                    overridden=[1], deleted=[2], retained=[7, 9],
                )

            monkeypatch.setattr(
                salary_regeneration.recurrence_engine,
                "regenerate_for_template", _conflict,
            )
            ctx = BalanceContext.build(seed_user["user"].id)

            retained = salary_regeneration.regenerate_salary_transactions(ctx, profile)

            assert retained == [7, 9]
            assert seen["template"] is profile.template

    def test_a_clean_regeneration_returns_nothing_kept(
        self, app, db, seed_user, seed_periods_today,
    ):
        """No conflict, no retained rows: an empty list, never ``None``.

        The route hands the answer straight to ``flash_retained_notice``,
        which flashes nothing for an empty list -- a ``None`` would be a
        second spelling of "nothing kept" for every caller to test for.
        """
        with app.app_context():
            profile = _linked_profile(seed_user, db.session)
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            retained = salary_regeneration.regenerate_salary_transactions(ctx, profile)

            assert retained == []

    def test_it_prices_no_paycheck_and_states_no_amount(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """The regeneration redraws rows and writes no figure (plan step X-bp).

        It re-stated the template's ``default_amount`` at today's net until
        X-bp, a copy nothing read once the Recurring and savings pages, the
        edit page and the archive priced today's paycheck themselves, and the
        same step dropped the column.  So the pass's pricer is never asked
        for and no price version is written: a salary profile's template
        states no amount.
        """
        with app.app_context():
            profile = _linked_profile(seed_user, db.session)
            db.session.commit()

            def _never(*_args, **_kwargs):
                raise AssertionError("the regeneration priced a paycheck")

            monkeypatch.setattr(BalanceContext, "paychecks", _never)
            ctx = BalanceContext.build(seed_user["user"].id)

            assert salary_regeneration.regenerate_salary_transactions(ctx, profile) == []
            db.session.flush()
            assert template_amount_service.amount_versions(profile.template) == []

    def test_a_profile_with_no_template_is_left_alone(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """No template, no rows to regenerate: nothing priced, nothing called."""
        with app.app_context():
            profile = make_salary_profile(seed_user, db.session)
            db.session.commit()
            assert profile.template is None

            def _never(*_args, **_kwargs):
                raise AssertionError("the engine was called for a template-less profile")

            monkeypatch.setattr(
                salary_regeneration.recurrence_engine, "regenerate_for_template", _never,
            )
            # The paycheck door is the PASS's pricer since plan step
            # salary:C12 (it was a direct ``calculate_paycheck`` on this
            # module), so the pricer is what must never be asked for.
            monkeypatch.setattr(BalanceContext, "paychecks", _never)
            ctx = BalanceContext.build(seed_user["user"].id)

            assert salary_regeneration.regenerate_salary_transactions(ctx, profile) == []

    def test_a_database_error_is_logged_with_the_profile_and_re_raised(
        self, app, db, seed_user, seed_periods_today, monkeypatch, caplog,
    ):
        """The narrow catch is a logging hook, not a swallow (C-46 / F-145)."""
        with app.app_context():
            profile = _linked_profile(seed_user, db.session)
            db.session.commit()

            def _data_error(*_args, **_kwargs):
                raise DataError("stmt", None, Exception("simulated"))

            monkeypatch.setattr(
                salary_regeneration.recurrence_engine,
                "regenerate_for_template", _data_error,
            )
            ctx = BalanceContext.build(seed_user["user"].id)

            with pytest.raises(DataError):
                salary_regeneration.regenerate_salary_transactions(ctx, profile)

            assert any(
                f"profile {profile.id}" in record.getMessage()
                for record in caplog.records
            ), "the failure was re-raised without the profile id being logged"
