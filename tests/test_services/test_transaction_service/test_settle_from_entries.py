"""Settling from entries: each kind's status and figure, the day, its preconditions and session."""

from datetime import datetime

from app.utils.dates import display_today
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.models.ref import Status
from app.models.transaction import Transaction
from app.services import status_seam, transaction_service
from app.services.row_valuation import settled_figure
from tests._test_helpers import generate_row_of, make_expense_template

from ._helpers import _make_entry, _make_template, _plan_of


# ── Happy-Path Tests ─────────────────────────────────────────────────


class TestSettleFromEntriesExpense:
    """Settling expense transactions produces DONE status and entry-sum actual."""

    def test_expense_with_entries_sets_done_and_sum(
        self, app, db, seed_user, seed_periods,
    ):
        """Expense + multiple entries: status=DONE, actual=sum, day recorded.

        Setup: $150 + $250 = $400 of debit entries against a $500 envelope.
        Expected: status_id == DONE, actual_amount == 400.00, day recorded.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            user_id = seed_user["user"].id

            _make_entry(txn.id, user_id, "150.00", "Kroger")
            _make_entry(txn.id, user_id, "250.00", "Target")
            db.session.flush()

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            db.session.refresh(txn)
            # 150 + 250 = 400
            assert settled_figure(txn) == Decimal("400.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert txn.settled_on is not None

    def test_expense_includes_credit_entries(
        self, app, db, seed_user, seed_periods,
    ):
        """Both debit and credit entries contribute to actual_amount.

        Per ``purchases_total`` semantics, credit entries
        count toward total spending for analytics; the credit/checking
        impact split is handled separately by the CC payback workflow.

        Setup: $300 debit + $100 credit = $400.
        Expected: actual_amount == 400.00.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            user_id = seed_user["user"].id

            _make_entry(txn.id, user_id, "300.00", "Kroger")
            _make_entry(
                txn.id, user_id, "100.00", "Amazon", is_credit=True,
            )
            db.session.flush()

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            db.session.refresh(txn)
            # 300 + 100 = 400
            assert settled_figure(txn) == Decimal("400.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_expense_zero_entries_settles_at_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """Empty entry list settles at actual=0 (carry-forward rollover case).

        The carry-forward envelope branch invokes the helper on
        envelope rows that may have no entries; the contract is
        ``actual_amount == Decimal("0")``, NOT a fallback to
        ``estimated_amount``.  This frees the full estimated amount
        to roll into the next period's canonical row.
        """
        with app.app_context():
            template = _make_template(seed_user, amount="100.00")
            txn = generate_row_of(template, seed_periods[0])

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            db.session.refresh(txn)
            assert settled_figure(txn) == Decimal("0.00")
            # The PLAN is untouched: still the definition's, still $100.00.
            assert txn.estimated_amount is None
            assert _plan_of(txn) == Decimal("100.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert txn.settled_on is not None

    def test_expense_overspend_records_full_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """Overspending records the full entry sum without clamping.

        If the wife spends $120 against a $100 envelope, the actual
        reflects the truth ($120), not the cap ($100).  The
        carry-forward helper uses this to compute leftover =
        max(0, estimated - actual) elsewhere; clamping here would
        hide the overspend signal from analytics.

        Setup: $80 + $40 = $120 against a $100 estimate.
        Expected: actual_amount == 120.00, estimated stays 100.00.
        """
        with app.app_context():
            template = _make_template(
                seed_user, amount="100.00",
            )
            txn = generate_row_of(template, seed_periods[0])
            user_id = seed_user["user"].id

            _make_entry(txn.id, user_id, "80.00", "Kroger")
            _make_entry(txn.id, user_id, "40.00", "Target")
            db.session.flush()

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            db.session.refresh(txn)
            # 80 + 40 = 120 -- exceeds the 100 estimate, intentionally.
            assert settled_figure(txn) == Decimal("120.00")
            assert txn.estimated_amount is None
            assert _plan_of(txn) == Decimal("100.00")


class TestSettleFromEntriesIncome:
    """Settling income transactions produces RECEIVED status.

    Phase 2 of the carry-forward aftermath plan (committed in
    ``feat(template): rename ... reject envelope semantics on income``)
    rejects ``is_envelope=True`` on income templates at the schema
    layer, so this branch is not reachable via the normal template
    create/update flow.  The branch must remain correct because
    direct DB writes can still produce the combination, and the
    helper is the lowest-level mutation point -- its behavior
    documents the contract regardless of how the row got into this
    state.
    """

    def test_income_with_entries_sets_received(
        self, app, db, seed_user, seed_periods,
    ):
        """Income + entries: status=RECEIVED, actual=sum, day recorded."""
        with app.app_context():
            template = _make_template(
                seed_user, income=True, amount="2500.00",
            )
            txn = generate_row_of(template, seed_periods[0])
            user_id = seed_user["user"].id

            _make_entry(txn.id, user_id, "1000.00", "Direct deposit 1")
            _make_entry(txn.id, user_id, "1500.00", "Direct deposit 2")
            db.session.flush()

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            db.session.refresh(txn)
            # 1000 + 1500 = 2500
            assert settled_figure(txn) == Decimal("2500.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.RECEIVED)
            assert txn.settled_on is not None


# ── Settle-day handling ──────────────────────────────────────────────


class TestSettleFromEntriesSettleDay:
    """The helper records the settle day through the seam, and takes no knob.

    **It accepted an explicit ``paid_at`` until plan step X-f1, and the
    parameter was DEAD** (ruling R-EC).  Its docstring named the carry-forward
    envelope branch as the caller that supplied one;
    ``carry_forward_service._execute`` calls it with no such argument and always
    has, and an AST sweep of ``app/`` found ZERO call sites passing it.  The
    test that pinned it is deleted with it rather than renamed: a test for a
    knob nothing turns is a test for speculative flexibility, which the coding
    standards forbid outright.  A caller that genuinely means "this settled on
    another day" corrects it afterwards, through the edit door ruling R-ED
    builds.
    """

    def test_the_settle_day_is_the_users_today(
        self, app, db, seed_user, seed_periods,
    ):
        """Settling from entries records the user's civil day, via the seam.

        A ``date``, not an instant, and not the process's UTC day: the helper
        does no dating of its own -- it hands the status change to
        ``status_seam.apply_status_change``, whose stamp is ``display_today()``.
        The zone rule itself is pinned at the seam
        (``test_status_seam.py``); this pins that the envelope path really goes
        through it rather than reaching for a clock of its own, which it did
        (``db.func.now()``, a fourth database-clock reach) before the seam
        absorbed it.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            user_id = seed_user["user"].id
            _make_entry(txn.id, user_id, "10.00", "Test")
            db.session.flush()

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            db.session.refresh(txn)
            assert txn.settled_on == display_today()
            assert not isinstance(txn.settled_on, datetime)


# ── Precondition Tests ───────────────────────────────────────────────


class TestSettleFromEntriesPreconditions:
    """Each documented precondition raises ValidationError on violation.

    The helper is the lowest-level "settle a tracked row" mutation,
    used by both manual mark-done and the carry-forward batch.  A
    permissive helper would silently corrupt state when fed a
    soft-deleted row, an immutable status, or a non-envelope template;
    defensive validation keeps the bug
    surface narrow.  Each test below confirms one rule.
    """

    def test_rejects_soft_deleted_transaction(
        self, app, db, seed_user, seed_periods,
    ):
        """Soft-deleted transactions cannot be resurrected via settle."""
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            txn.is_deleted = True
            db.session.commit()
            txn_id = txn.id

            with pytest.raises(ValidationError) as exc_info:
                transaction_service.settle_from_entries(txn)
            # The seam's own sentence, naming the row (ruling R-CC98).
            assert "was deleted: a payment cannot be recorded under it" in (
                str(exc_info.value)
            )

            # No state change should have leaked through -- the helper
            # raised before any mutation, so a rollback is just hygiene.
            db.session.rollback()
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert status_seam.recorded_settlement(reloaded) is None
            assert reloaded.status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            )
            assert reloaded.is_deleted is True

    def test_rejects_non_envelope_template(
        self, app, db, seed_user, seed_periods,
    ):
        """Templates with is_envelope=False are not entry-tracked."""
        with app.app_context():
            # A plain $1,200.00 Rent bill: the shared builder's own default.
            template = make_expense_template(db.session, seed_user)
            txn = generate_row_of(template, seed_periods[0])

            with pytest.raises(ValidationError) as exc_info:
                transaction_service.settle_from_entries(txn)
            assert "envelope-tracked" in str(exc_info.value)

    def test_rejects_already_settled_status(
        self, app, db, seed_user, seed_periods,
    ):
        """A second call on a Paid transaction raises (no idempotent re-settle).

        The Paid status is immutable per the ``is_immutable`` flag on
        ``ref.statuses``.  Attempting to re-settle would update
        the settle day and possibly ``actual_amount`` on a finalised
        row, which is meaningless and indicates a caller bug.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            user_id = seed_user["user"].id
            _make_entry(txn.id, user_id, "100.00", "Kroger")
            db.session.flush()

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            # Second call should refuse.
            db.session.refresh(txn)
            with pytest.raises(ValidationError) as exc_info:
                transaction_service.settle_from_entries(txn)
            assert "immutable" in str(exc_info.value).lower()

    def test_rejects_cancelled_transaction(
        self, app, db, seed_user, seed_periods,
    ):
        """Cancelled transactions stay cancelled.

        ``CANCELLED`` is immutable so the user's deliberate
        cancellation is preserved.  Settling would silently override
        the cancel decision -- a financial bug masquerading as a
        no-op.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            cancelled_status = (
                db.session.query(Status).filter_by(name="Cancelled").one()
            )
            txn.status_id = cancelled_status.id
            db.session.flush()

            with pytest.raises(ValidationError) as exc_info:
                transaction_service.settle_from_entries(txn)
            assert "immutable" in str(exc_info.value).lower()


# ── Caller-Owned Session Lifecycle ───────────────────────────────────


class TestSettleFromEntriesSessionContract:
    """Verify the helper does not flush or commit on its own.

    The carry-forward batch needs the helper to participate in a
    single atomic transaction; if the helper auto-committed, a
    failure later in the batch would leave a half-applied state.
    """

    def test_does_not_commit_on_success(
        self, app, db, seed_user, seed_periods,
    ):
        """Mutations are visible only after the caller commits."""
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            user_id = seed_user["user"].id
            _make_entry(txn.id, user_id, "75.00", "Pharmacy")
            # Persist setup so a rollback after the helper call only
            # reverts the helper's own mutations, not the test fixtures.
            db.session.commit()
            txn_id = txn.id

            transaction_service.settle_from_entries(txn)

            # Attribute mutations are visible on the in-memory object.
            assert settled_figure(txn) == Decimal("75.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            # A rollback should reverse the helper's writes -- proving
            # the helper itself never committed or flushed.
            db.session.rollback()

            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded is not None
            assert status_seam.recorded_settlement(reloaded) is None
            assert reloaded.status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            )
