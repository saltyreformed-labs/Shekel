"""REC-524: editing an ARCHIVED definition regenerates none of its rows.

Plan step **R7d-g-3** (``docs/plans/implementation_plan_recurrence_redesign.md``),
plan ledger row **REC-524**.  The archive door soft-deletes every projected
row a definition generated and takes it out of every active set; the edit
door is URL-only for an archived template (the Archived drawer offers
unarchive and hard-delete, no edit), but it is reachable, and until this step
nothing on the regeneration path read ``is_active``.  **The branch, traced
here rather than assumed** (the coordinator's review corrected an earlier
diagnosis): a soft-deleted row still CLAIMS its occurrence
(``_recurrence_common.OccurrenceClaims.over`` reads immutable, overridden and
soft-deleted rows alike), so an archived definition whose every occurrence has
a soft-deleted row regenerates nothing even without this step -- measured
below as the amount-edit case.  What revived rows is an occurrence NO row
answers: a definition archived before it ever generated (REC-524's ``0 -> 6``
measurement, on a hand-built template), and -- the shape a real archive
reaches -- every pay period populated AFTER the archive, because period
population skips an archived template and so leaves its later occurrences
unclaimed.  The maintain pass then CREATED a live row per unclaimed period the
rule named, the Recurring surface still listing the template as archived.

The fix is the engine's, not a door's: :func:`~app.services.recurrence_engine
.definition_recurs` is the ONE predicate for "does this definition recur",
and an archived definition does not, so :func:`resolve_generation_plan`
answers ``None`` for it exactly as it does for a definition with no rule.
Both template kinds and every door inherit that; the unarchive door flips
the flag before its pass and is unaffected.  The conflict chooser's
"still recurs" gate reads the same predicate, so an amount edit on an
archived template asks no question about rows it will not regenerate.

Each claim below carries its NEGATIVE CONTROL as a live mutation
(``monkeypatch``), because a green suite cannot otherwise tell a gate that
fired from one that measured nothing: with the archived arm patched out, the
rename revives the rows exactly as REC-524 measured.
"""

from decimal import Decimal

import pytest

from app.enums import RecurrenceUnitEnum
from app.extensions import db
from app.models.ref import AccountType, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import (
    account_service,
    recurrence_engine,
    transfer_recurrence,
)
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.recurrence_engine import _plan as engine_plan
from app.services.recurrence_engine import definition_recurs
from app.utils.balance_predicates import is_projected_clause
from tests._test_helpers import (
    all_periods,
    cadence_payload,
    make_cadence_rule,
    state_template_price,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD
from tests._definition_price import newest_price


# ── Fixtures ─────────────────────────────────────────────────────────


def _savings_account(seed_user):
    """Create a Savings destination for the transfer template."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _transfer_template_with_rows(seed_user, savings):
    """An every-paycheck transfer into *savings*, its rows generated, committed."""
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        category_id=seed_user["categories"]["Rent"].id,
        name="Sweep to savings",
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, Decimal("200.00"))
    make_cadence_rule(template, EVERY_PERIOD)
    transfer_recurrence.generate_for_template(
        template,
        GenerationSchedule.for_period_ids(
            BalanceContext.build(template.user_id),
            {p.id for p in all_periods(seed_user["user"].id)},
        ),
        seed_user["scenario"].id,
    )
    db.session.commit()
    return template


def _transaction_template_with_rows(seed_user):
    """An every-paycheck expense template, its rows generated, committed."""
    expense = db.session.query(TransactionType).filter_by(name="Expense").one()
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=expense.id,
        name="Streaming",
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, Decimal("15.99"))
    make_cadence_rule(template, EVERY_PERIOD)
    recurrence_engine.generate_for_template(
        template,
        GenerationSchedule.for_period_ids(
            BalanceContext.build(template.user_id),
            {p.id for p in all_periods(seed_user["user"].id)},
        ),
        seed_user["scenario"].id,
    )
    db.session.commit()
    return template


def _archived_transfer_never_generated(seed_user, savings):
    """An archived every-paycheck transfer into *savings* with NO row at all.

    The shape REC-524 was measured on, and the one a real archive reaches
    the day new pay periods are populated: period population skips an
    archived template, so its later occurrences have no row -- soft-deleted
    or otherwise -- to CLAIM them, and a regeneration that reads the rule
    alone CREATES one per period.  (A soft-deleted row claims its occurrence,
    which is why the archive-with-history fixture below cannot show the
    revival and the amount-edit case uses it for the chooser instead.)
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        category_id=seed_user["categories"]["Rent"].id,
        name="Sweep to savings",
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, Decimal("200.00"))
    make_cadence_rule(template, EVERY_PERIOD)
    template.is_active = False
    db.session.commit()
    return template


def _archived_transaction_never_generated(seed_user):
    """The transaction twin of :func:`_archived_transfer_never_generated`."""
    expense = db.session.query(TransactionType).filter_by(name="Expense").one()
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=expense.id,
        name="Streaming",
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template, Decimal("15.99"))
    make_cadence_rule(template, EVERY_PERIOD)
    template.is_active = False
    db.session.commit()
    return template


def _live_transfers(template_id):
    """The template's projected, non-deleted transfers."""
    return (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == template_id,
            is_projected_clause(Transfer),
            Transfer.is_deleted.is_(False),
        )
        .all()
    )


def _live_transactions(template_id):
    """The template's projected, non-deleted transactions."""
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.template_id == template_id,
            is_projected_clause(Transaction),
            Transaction.is_deleted.is_(False),
        )
        .all()
    )


def _reload(model, template_id):
    db.session.expire_all()
    return db.session.get(model, template_id)


def _transfer_update_payload(template, **overrides):
    """The transfer edit form's fields, the cadence restated, as a browser posts them."""
    payload = {
        "name": template.name,
        "amount": str(newest_price(template)),
        "from_account_id": str(template.from_account_id),
        "to_account_id": str(template.to_account_id),
        "version_id": str(template.version_id),
        # The cadence restated WITHOUT a start: the stored one rides through
        # ("absent keeps"), which is what a browser posts for an untouched
        # row -- and what keeps the rule on the seeded periods.
        **cadence_payload(unit=RecurrenceUnitEnum.PERIOD, states_a_start=False),
    }
    payload.update(overrides)
    return payload


def _disarm_the_archived_arm(monkeypatch):
    """NEGATIVE CONTROL: make the predicate read the rule alone, as before this step."""
    monkeypatch.setattr(
        engine_plan, "definition_recurs",
        lambda template: template.recurrence_rule is not None,
    )


# ── The predicate ────────────────────────────────────────────────────


@pytest.mark.usefixtures("seed_periods_today")
class TestDefinitionRecurs:
    """:func:`definition_recurs`: active AND ruled, nothing else."""

    def test_an_active_ruled_definition_recurs(self, app, seed_user):
        with app.app_context():
            template = _transfer_template_with_rows(
                seed_user, _savings_account(seed_user),
            )
            assert definition_recurs(template) is True

    def test_an_archived_definition_does_not(self, app, seed_user):
        with app.app_context():
            template = _transfer_template_with_rows(
                seed_user, _savings_account(seed_user),
            )
            template.is_active = False
            db.session.flush()
            assert definition_recurs(template) is False

    def test_a_rule_less_definition_does_not(self, app, seed_user):
        with app.app_context():
            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=_savings_account(seed_user).id,
                name="One-off",
            )
            db.session.add(template)
            db.session.flush()
            assert definition_recurs(template) is False

    def test_the_carry_forward_prediction_reads_the_same_answer(
        self, app, seed_user,
    ):
        """``rows_to_generate_in_period`` shares the plan resolver, so it inherits the arm.

        The carry-forward's preview and executor ask it whether the engine
        would generate a target row; for an archived envelope the answer is
        now "no", and the leftover is created as its own row rather than
        generated from a stopped definition.
        """
        with app.app_context():
            template = _transaction_template_with_rows(seed_user)
            ctx = BalanceContext.build(seed_user["user"].id)
            schedule = GenerationSchedule.for_pass(ctx)
            period_id = next(iter(schedule.write_period_ids))
            # Live: the engine names this period (a row already answers it,
            # so the prediction is empty for THAT reason; ask the plan).
            assert recurrence_engine.resolve_generation_plan(
                template, schedule, ctx.scenario_id, None, block_message="x",
            ) is not None
            template.is_active = False
            db.session.flush()
            assert recurrence_engine.rows_to_generate_in_period(
                template, period_id, ctx.scenario_id, schedule=schedule,
            ) == ()

    def test_the_plan_resolves_to_nothing_for_an_archived_definition(
        self, app, seed_user,
    ):
        """The engine's own reading: an archived definition names no occurrence."""
        with app.app_context():
            template = _transfer_template_with_rows(
                seed_user, _savings_account(seed_user),
            )
            ctx = BalanceContext.build(seed_user["user"].id)
            schedule = GenerationSchedule.for_pass(ctx)
            assert recurrence_engine.resolve_generation_plan(
                template, schedule, ctx.scenario_id, None, block_message="x",
            ) is not None
            template.is_active = False
            db.session.flush()
            assert recurrence_engine.resolve_generation_plan(
                template, schedule, ctx.scenario_id, None, block_message="x",
            ) is None


# ── The transfer kind ────────────────────────────────────────────────


@pytest.mark.usefixtures("seed_periods_today")
class TestAnArchivedTransferTemplatesEdit:
    """POST /transfers/<id> on an archived template writes the definition, no rows."""

    def _archived_with_history(self, auth_client, seed_user):
        """A transfer template whose rows the archive door soft-deleted."""
        template = _transfer_template_with_rows(
            seed_user, _savings_account(seed_user),
        )
        assert len(_live_transfers(template.id)) > 0, "precondition: rows exist"
        resp = auth_client.post(f"/transfers/{template.id}/archive")
        assert resp.status_code == 302
        template = _reload(TransferTemplate, template.id)
        assert template.is_active is False
        assert _live_transfers(template.id) == []
        return template

    def test_a_rename_revives_no_row(self, app, auth_client, seed_user):
        """REC-524's measurement, inverted: 0 live rows before, 0 after.

        On the shape it was measured on -- occurrences no row answers -- so
        the pass has something to create and creates nothing.
        """
        with app.app_context():
            template = _archived_transfer_never_generated(
                seed_user, _savings_account(seed_user),
            )
            assert _live_transfers(template.id) == []

            resp = auth_client.post(
                f"/transfers/{template.id}",
                data=_transfer_update_payload(template, name="Sweep, renamed"),
            )

            assert resp.status_code == 302, resp.data
            saved = _reload(TransferTemplate, template.id)
            assert saved.name == "Sweep, renamed"
            assert saved.is_active is False
            assert _live_transfers(saved.id) == []

    def test_negative_control_the_rename_revives_rows_without_the_archived_arm(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """With the predicate reading the rule alone, the rows appear (the defect)."""
        with app.app_context():
            template = _archived_transfer_never_generated(
                seed_user, _savings_account(seed_user),
            )
            _disarm_the_archived_arm(monkeypatch)

            resp = auth_client.post(
                f"/transfers/{template.id}",
                data=_transfer_update_payload(template, name="Sweep, renamed"),
            )

            assert resp.status_code == 302
            saved = _reload(TransferTemplate, template.id)
            assert saved.is_active is False
            assert len(_live_transfers(saved.id)) > 0, (
                "the control did not reproduce REC-524: the gate under test "
                "is not what stops the revival"
            )

    def test_an_amount_edit_asks_no_chooser_and_revives_no_row(
        self, app, auth_client, seed_user,
    ):
        """The archived rows are soft-deleted conflicts; the chooser stays silent.

        A definition that does not recur will regenerate nothing at the new
        amount, so the chooser has no question to ask -- the same reading it
        gives a CLEARED recurrence.  The edit saves and the list renders.
        """
        with app.app_context():
            template = self._archived_with_history(auth_client, seed_user)

            resp = auth_client.post(
                f"/transfers/{template.id}",
                data=_transfer_update_payload(template, amount="250.00"),
            )

            assert resp.status_code == 302, resp.data
            assert resp.headers["Location"].endswith("/transfers")
            saved = _reload(TransferTemplate, template.id)
            assert newest_price(saved) == Decimal("250.00")
            assert saved.is_active is False
            assert _live_transfers(saved.id) == []

    def test_unarchive_still_brings_the_rows_back(
        self, app, auth_client, seed_user,
    ):
        """The unarchive door flips the flag before its pass, so it recurs again."""
        with app.app_context():
            template = self._archived_with_history(auth_client, seed_user)

            resp = auth_client.post(f"/transfers/{template.id}/unarchive")

            assert resp.status_code == 302
            saved = _reload(TransferTemplate, template.id)
            assert saved.is_active is True
            assert len(_live_transfers(saved.id)) > 0


# ── The transaction kind ─────────────────────────────────────────────


@pytest.mark.usefixtures("seed_periods_today")
class TestAnArchivedTransactionTemplatesEdit:
    """POST /templates/<id> on an archived template writes the definition, no rows."""

    def test_a_rename_revives_no_row(self, app, auth_client, seed_user):
        with app.app_context():
            template = _archived_transaction_never_generated(seed_user)
            assert _live_transactions(template.id) == []

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Streaming, renamed",
                "amount": str(newest_price(template)),
                **cadence_payload(
                    unit=RecurrenceUnitEnum.PERIOD, states_a_start=False,
                ),
            })

            assert resp.status_code == 302, resp.data
            saved = _reload(TransactionTemplate, template.id)
            assert saved.name == "Streaming, renamed"
            assert saved.is_active is False
            assert _live_transactions(saved.id) == []

    def test_negative_control_the_rename_revives_rows_without_the_archived_arm(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        with app.app_context():
            template = _archived_transaction_never_generated(seed_user)
            _disarm_the_archived_arm(monkeypatch)

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Streaming, renamed",
                "amount": str(newest_price(template)),
                **cadence_payload(
                    unit=RecurrenceUnitEnum.PERIOD, states_a_start=False,
                ),
            })

            assert resp.status_code == 302, resp.data
            saved = _reload(TransactionTemplate, template.id)
            assert saved.is_active is False
            assert len(_live_transactions(saved.id)) > 0, (
                "the control did not reproduce REC-524 on the transaction kind"
            )


@pytest.mark.usefixtures("seed_periods_today")
class TestTheUpdateDoorCannotArchive:
    """REC-525: ``is_active`` posted to POST /transfers/<id> writes nothing."""

    def test_a_crafted_is_active_key_is_ignored(self, app, auth_client, seed_user):
        """A posted ``is_active`` writes nothing, and the allowlist no longer names it.

        Two pins, because the first cannot see the second: ``BaseSchema`` drops
        the unknown key BEFORE the allowlist is consulted, so the POST below
        passed with the dead entry present.  What REC-525 deleted is the
        entry itself, and the constant is read directly for that.
        """
        from app.routes.transfers.templates import _TEMPLATE_UPDATE_FIELDS  # pylint: disable=import-outside-toplevel

        assert "is_active" not in _TEMPLATE_UPDATE_FIELDS
        with app.app_context():
            template = _transfer_template_with_rows(
                seed_user, _savings_account(seed_user),
            )
            live_before = len(_live_transfers(template.id))
            assert template.is_active is True

            resp = auth_client.post(
                f"/transfers/{template.id}",
                data=_transfer_update_payload(
                    template, name="Renamed", is_active="false",
                ),
            )

            assert resp.status_code == 302, resp.data
            saved = _reload(TransferTemplate, template.id)
            assert saved.name == "Renamed"
            assert saved.is_active is True
            assert len(_live_transfers(saved.id)) == live_before
