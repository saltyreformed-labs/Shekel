"""Pay-period generation's event, the recurrence engine's generate, regenerate and
conflict-resolution events (a salary profile's archive and the cross-user refusals included),
and carry forward's event with its breakdown counts.
"""

import logging
from datetime import date
from decimal import Decimal

from app.models.transaction_template import TransactionTemplate
from app.services import carry_forward_service, pay_period_write, recurrence_engine
from app import ref_cache
from app.enums import TxnTypeEnum
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.utils.log_events import (
    ACCESS,
    BUSINESS,
    EVT_ACCESS_DENIED_CROSS_USER,
    EVT_CARRY_FORWARD,
    EVT_CROSS_USER_BLOCKED,
    EVT_PAY_PERIODS_GENERATED,
    EVT_RECURRENCE_CONFLICTS_RESOLVED,
    EVT_RECURRENCE_GENERATED,
    EVT_RECURRENCE_REGENERATED,
    EVT_SALARY_ROWS_FROZEN,
    EVT_TRANSFER_RECURRENCE_REGENERATED,
)
from tests._test_helpers import (
    make_every_period_rule,
    one_off_row_of,
    record_paydays_across_a_hole,
    rhythm_of,
    start_test_pay_list,
)

from ._helpers import _LogCapture


# ── Pay periods ────────────────────────────────────────────────────


class TestPayPeriodServiceLogging:
    """``pay_period_service.generate_pay_periods`` emits ``pay_periods_generated``."""

    def test_generate_emits_event(self, app, db, bare_user):
        """Generating periods emits one event with user_id and count.

        A ``bare_user`` -- one with no opening payday -- so the batch is the
        writer's own first schedule; beside ``seed_user``'s 2024 opening
        payday a 2027 block is a hole the writer refuses (plan step
        ``pay_calendar:C17-c-2a``), and the tree helper that builds such a
        state emits nothing.
        """
        with app.app_context(), _LogCapture(
            "app.services.pay_period_write",
        ) as cap:
            created = pay_period_write.record_paydays(
                user_id=bare_user["user"].id,
                first_payday=date(2027, 1, 1),
                num_periods=3,
                rhythm=rhythm_of(14),
            )
            assert len(created) == 3

        record = cap.find(EVT_PAY_PERIODS_GENERATED)
        assert record is not None, (
            f"Did not emit {EVT_PAY_PERIODS_GENERATED}; "
            f"observed: {cap.event_summary()}"
        )
        assert record.levelno == logging.INFO
        assert record.category == BUSINESS
        assert record.user_id == bare_user["user"].id
        assert record.count == 3
        # The rhythm as its kind's PHRASE (ruling R-PC82, plan step
        # pay_calendar:C17-d-2): one field every kind can fill, where
        # ``cadence_days`` had nothing to say for a month kind.
        assert record.cadence == "every 14 days"
        assert record.start_date == "2027-01-01"


class TestRecurrenceEngineLogging:
    """recurrence_engine emits generate / regenerate / resolve events."""

    def test_generate_emits_event(self, app, db, seed_user, _recurrence_setup, seed_periods):
        """generate_for_template emits ``recurrence_generated`` with count."""
        with app.app_context(), _LogCapture(
            "app.services.recurrence_engine",
        ) as cap:
            created = recurrence_engine.generate_for_template(
                _recurrence_setup, GenerationSchedule.for_period_ids(
                    BalanceContext.build(_recurrence_setup.user_id), {p.id for p in seed_periods[:3]},
                ),
                seed_user["scenario"].id,
            )

        assert len(created) == 3
        record = cap.find(EVT_RECURRENCE_GENERATED)
        assert record is not None
        assert record.template_id == _recurrence_setup.id
        assert record.count == 3
        assert record.user_id == seed_user["user"].id

    def test_regenerate_emits_THIS_engines_event_and_not_the_other(
        self, app, db, seed_user, _recurrence_setup, seed_periods,
    ):
        """regenerate_for_template emits ``recurrence_regenerated``.

        **And not the transfer engine's**, which is the half plan step
        balance:X-au-d made worth asserting.  Both engines run ONE shared body
        (``recurrence_engine._pass.regenerate_definition``) and supply their own
        ``PassReporting`` -- a logger and three strings, constructed
        POSITIONALLY.  A swap of the five callables beside it is caught by
        arity; a swap of the two engines' reporting records is not, and would
        file each engine's regeneration under the other's audit event in
        silence.  The transfer engine's twin is
        ``test_transfer_recurrence``'s ``EVT_TRANSFER_RECURRENCE_REGENERATED``
        assertion, so the pair closes the swap in both directions.  Named by an
        adversarial review of that step.
        """
        with app.app_context():
            recurrence_engine.generate_for_template(
                _recurrence_setup, GenerationSchedule.for_period_ids(
                    BalanceContext.build(_recurrence_setup.user_id), {p.id for p in seed_periods[:3]},
                ),
                seed_user["scenario"].id,
            )
            db.session.commit()
            with _LogCapture("app.services.recurrence_engine") as cap:
                recurrence_engine.regenerate_for_template(
                    _recurrence_setup, GenerationSchedule.for_period_ids(
                        BalanceContext.build(_recurrence_setup.user_id), {p.id for p in seed_periods[:3]},
                    ),
                    seed_user["scenario"].id,
                )

        record = cap.find(EVT_RECURRENCE_REGENERATED)
        assert record is not None
        assert record.template_id == _recurrence_setup.id
        assert cap.find(EVT_TRANSFER_RECURRENCE_REGENERATED) is None, (
            "this engine must emit its OWN event: finding the transfer "
            "engine's here means the two PassReporting records are swapped"
        )

    def test_archiving_a_salary_profile_emits_its_frozen_count(
        self, app, db, seed_user, _recurrence_setup, seed_periods,
    ):  # pylint: disable=unused-argument
        """Archiving a salary profile emits ``salary_rows_frozen``.

        Plan step **balance:X-au-d**: the archive records what each row it
        priced was last worth, and the COUNT is what the audit trail carries.
        Asserted because a freeze that silently skipped a row would still leave
        the balance unmoved -- the skipped row keeps its declaration and
        re-prices later -- so the outcome alone cannot see it.  Named by an
        adversarial review of that step.
        """
        # pylint: disable=import-outside-toplevel  -- the salary models and the
        # archive service are not this module's subject and would put the
        # paycheck stack on every logging test's load path.
        from app.models.ref import FilingStatus
        # Pylint: ``import-outside-toplevel`` -- see above.
        from app.models.salary_profile import SalaryProfile
        # Pylint: ``import-outside-toplevel`` -- see above.
        from app.services import salary_profile_service

        with app.app_context():
            # The template is built HERE rather than taken from
            # ``_recurrence_setup``: ``is_salary_linked_template`` reads the
            # identity-mapped ``salary_profiles`` collection, so a template
            # loaded before its profile existed carries an empty one and would
            # make this case grade a stale relationship rather than the act.
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=next(iter(seed_user["categories"].values())).id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                name="X-au-d logging control",
                default_amount=Decimal("11.11"),
            )
            db.session.add(template)
            db.session.flush()
            salary = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=db.session.query(FilingStatus).first().id,
                template_id=template.id,
                name="X-au-d logging control",
                state_code="NC",
                is_active=True,
            )
            db.session.add(salary)
            start_test_pay_list(salary, Decimal("4000.00"))  # $104,000.00 a year / 26
            db.session.flush()
            make_every_period_rule(db.session, template)
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(seed_user["user"].id),
                    {p.id for p in seed_periods[:3]},
                ),
                seed_user["scenario"].id,
            )
            db.session.flush()
            profile = template.salary_profiles[0]

            with _LogCapture("app.services.salary_profile_service") as cap:
                frozen = salary_profile_service.archive_profile(profile)

            assert frozen == len(created) == 3
            record = cap.find(EVT_SALARY_ROWS_FROZEN)
            assert record is not None
            assert record.frozen_count == 3
            assert record.salary_profile_id == profile.id
            assert record.template_id == template.id

    def test_cross_user_blocked_emits_event(
        self, app, db, seed_user, _recurrence_setup, seed_periods,
        seed_second_user,
    ):
        """A scenario from another user emits ``cross_user_blocked``."""
        with app.app_context(), _LogCapture(
            "app.services.recurrence_engine",
        ) as cap:
            created = recurrence_engine.generate_for_template(
                _recurrence_setup, GenerationSchedule.for_period_ids(
                    BalanceContext.build(_recurrence_setup.user_id), {p.id for p in seed_periods[:1]},
                ),
                seed_second_user["scenario"].id,
            )

        assert created == []
        record = cap.find(EVT_CROSS_USER_BLOCKED)
        assert record is not None
        assert record.levelno == logging.WARNING
        assert record.template_user_id == seed_user["user"].id
        assert record.scenario_id == seed_second_user["scenario"].id

    def test_resolve_conflicts_keep_emits_event(self, app, db, seed_user):
        """resolve_conflicts(action='keep') emits the resolution event."""
        with app.app_context(), _LogCapture(
            "app.services.recurrence_engine",
        ) as cap:
            recurrence_engine.resolve_conflicts(
                [], "keep", BalanceContext.build(seed_user["user"].id),
            )

        record = cap.find(EVT_RECURRENCE_CONFLICTS_RESOLVED)
        assert record is not None
        assert record.action == "keep"

    def test_resolve_conflicts_cross_user_emits_access_event(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """A cross-user transaction id emits ``access_denied_cross_user``.

        The resolve helper silently skips the row but the audit trail
        must record the probe so SOC tooling sees it.
        """
        from app.models.ref import TransactionType  # noqa: WPS433
        expense_type = db.session.query(
            TransactionType,
        ).filter_by(name="Expense").one()

        # Build a transaction owned by the second user.
        # Years past the second owner's 2024 opening payday: a hole, written
        # through the tree's helper (plan step C17-c-2a, ruling R-PC67).
        s2_periods = record_paydays_across_a_hole(
            seed_second_user["user"].id, date(2027, 6, 1), 1, rhythm_of(14),
        )
        db.session.flush()

        s2_txn = one_off_row_of(
            s2_periods[0],
            name="Other Owner Txn",
            amount=Decimal("75.00"),
            user_id=s2_periods[0].user_id,
            account_id=seed_second_user["account"].id,
            scenario_id=seed_second_user["scenario"].id,
            transaction_type_id=expense_type.id,
            category_id=seed_second_user["categories"]["Rent"].id,
        )
        s2_txn.is_override = True
        db.session.commit()

        with app.app_context(), _LogCapture(
            "app.services.recurrence_engine",
        ) as cap:
            recurrence_engine.resolve_conflicts(
                [s2_txn.id], "update", BalanceContext.build(seed_user["user"].id),
            )

        record = cap.find(EVT_ACCESS_DENIED_CROSS_USER)
        assert record is not None
        assert record.category == ACCESS
        assert record.levelno == logging.WARNING
        assert record.user_id == seed_user["user"].id
        assert record.owner_id == seed_second_user["user"].id


# ── Carry forward ──────────────────────────────────────────────────


class TestCarryForwardLogging:
    """carry_forward_unpaid emits ``carry_forward`` with breakdown counts."""

    def test_carry_forward_emits_event(
        self, app, db, seed_user, seed_periods,
    ):
        """A successful carry-forward emits the structured event."""
        from app.models.ref import TransactionType  # noqa: WPS433
        expense_type = db.session.query(
            TransactionType,
        ).filter_by(name="Expense").one()

        # One projected one-off in period 0 -- the discrete partition.
        one_off_row_of(
            seed_periods[0],
            name="Ad-hoc Expense",
            amount=Decimal("50.00"),
            user_id=seed_periods[0].user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=expense_type.id,
            category_id=seed_user["categories"]["Groceries"].id,
        )
        db.session.commit()

        with app.app_context(), _LogCapture(
            "app.services.carry_forward_service",
        ) as cap:
            count = carry_forward_service.carry_forward_unpaid(
                source_period_id=seed_periods[0].id,
                target_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

        assert count == 1
        record = cap.find(EVT_CARRY_FORWARD)
        assert record is not None
        assert record.user_id == seed_user["user"].id
        assert record.count == 1
        assert record.from_period_id == seed_periods[0].id
        assert record.to_period_id == seed_periods[1].id
        assert record.discrete_count == 1
        assert record.envelope_count == 0
        assert record.transfer_count == 0
