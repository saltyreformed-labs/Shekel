"""
Shekel Budget App -- Transaction Service Tests

Tests for ``app.services.transaction_service`` -- the cross-cutting
helpers used by both the manual ``mark_done`` route and the
carry-forward envelope branch (Phase 4 of
``docs/carry-forward-aftermath-implementation-plan.md``).

Each test verifies exact Decimal values and explicit arithmetic so a
reviewer can recompute the expected values by hand.  Tests are
deliberately kept small and single-purpose so a regression at any
contract boundary surfaces with a precise failure message.
"""

from datetime import date, datetime, timedelta

from app.utils.dates import display_today
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    MovementFigureSourceEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.journal_entry import JournalEntry
from app.models.ref import Status
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import posting_service, status_seam, transaction_service
# The leaf, imported for ONE control that spies on a private name the package
# does not re-export -- see
# ``test_the_live_figure_is_resolved_BEFORE_the_status_flip`` for why patching
# the package attribute would grade nothing.
from app.services.transaction_service import _settle
from app.services.row_valuation import settled_contribution, settled_figure
from app.services.cash_ledger import (
    amounts_by_key,
    derived_amount_basis,
    resolve_transaction_amount,
)
from tests._test_helpers import NO_TAX_LAW
from tests._test_helpers import (
    typed,
    amount_basis_for,
    an_entered_day,
    family_journal_filter,
    figure_source_columns,
    generate_row_of,
    make_expense_template,
    make_income_template,
    net_posted_by_day,
    repriced_by_the_owner,
    settlement_if_settling,
    start_test_pay_list,
)
from app.models.amount_ownership import AmountOwnership
from app.services.amount_ownership import state_own_amount


# ── Helpers ──────────────────────────────────────────────────────────


def _make_entry(txn_id, user_id, amount, description, *,
                purchased_on=None, is_credit=False):
    """Create an entry directly via ORM (bypasses service validation).

    The transaction service operates on already-loaded entries, so
    these tests construct entries with raw ORM access to keep the
    setup focused on the helper's contract.
    """
    # The parent, resolved here rather than taken as an argument: this
    # helper's whole point is that a caller passes IDS.  The entry takes its
    # account (the parent's, because no door here says otherwise -- it is the
    # movement's OWN since plan step credit_card:CC-5-1) and its owner (the
    # parent's, which ``fk_transaction_entries_owner_transaction`` refuses to
    # see any other way).
    parent = db.session.get(Transaction, txn_id)
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn_id,
        account_id=parent.account_id,
        owner_id=parent.user_id,
        user_id=user_id,
        amount=Decimal(amount),
        description=description,
        purchased_on=purchased_on or date(2026, 1, 5),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def _make_template(
    seed_user, *, income=False, default_amount="500.00", is_envelope=True,
):
    """Create a PRICED, every-paycheck definition of the requested kind.

    The shared builders (:func:`make_expense_template` and its income twin),
    so the definition is one the engine can generate from and its rows are
    the engine's own (:func:`generate_row_of`, plan step balance:X-cf-3b).
    It was ``_make_envelope_template`` -- a hand-built, UNPRICED envelope
    template that half its callers then flipped to ``is_envelope = False`` --
    until that step; a case states the definition it means here instead.

    Args:
        seed_user: The seed user fixture dict.
        income: Build an income definition rather than an expense one.
            The income-side envelope branch is reachable only this way:
            Phase 2 rejects ``is_envelope=True`` on income at the schema
            layer, and the helper under test must stay correct for a row
            that reached that state by direct write.
        default_amount: The definition's stated price.
        is_envelope: Whether its rows track purchases.
    """
    builder = make_income_template if income else make_expense_template
    return builder(
        db.session, seed_user, amount=default_amount,
        name=f"Tracked {'Income' if income else 'Expense'}",
        category_key="Groceries", is_envelope=is_envelope,
    )


def _plan_of(txn):
    """Return what *txn* PLANS -- its amount as the amount model resolves it.

    A row of a definition stores no figure (plan step X-au-e), so "the plan is
    untouched" is a question to the resolver rather than to the
    ``estimated_amount`` column, which reads ``None`` on such a row before and
    after every settle.  A row the owner re-priced owns its figure and the
    column is that figure; the cases that grade a re-price read it there.
    """
    return resolve_transaction_amount(txn, amount_basis_for(txn))


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
            template = _make_template(seed_user, default_amount="100.00")
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
                seed_user, default_amount="100.00",
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
                seed_user, income=True, default_amount="2500.00",
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


class TestSettleTransactionTheVerb:
    """``settle_transaction`` -- ruling **R-FA**'s verb, graded as a SERVICE.

    **These exist because an adversarial review found the verb had no
    service-tier control at all.**  Every assertion about it reached it through
    ``POST /transactions/<id>/mark-done``, so its contract was graded only in
    the shape one HTTP door happens to call it in -- and the whole reason plan
    step X-f2-c2 extracted it is that a SECOND, non-HTTP caller arrives next
    leaf (the reconcile panel's tick) whose shape nothing pinned.
    """

    def test_a_soft_deleted_row_is_refused(
        self, app, seed_user, seed_periods,
    ):
        """A settle cannot resurrect a deleted row -- on EITHER branch.

        **Finding N-233.**  The envelope branch refused this from the
        beginning, with its own docstring giving the reason, and the MANUAL
        branch never did -- so the refusal was a property of which branch a row
        happened to take rather than of the verb.  It was REACHABLE:
        ``get_accessible_transaction`` did not filter ``is_deleted`` until plan
        step ``credit_card:CC-5-4a-4`` (ruling **R-CC89**), so
        ``POST /transactions/<id>/mark-done`` on a soft-deleted non-envelope row
        flipped it into the settled band and stamped a settle day, while
        ``effective_amount`` valued it at ``Decimal("0")`` -- a row that reads
        Paid on every surface and is worth nothing on all of them.  Production
        carries 102 soft-deleted rows, every one of them Projected.

        Graded on the MANUAL branch specifically, because that is the half that
        had no guard.
        """
        with app.app_context():
            template = _make_template(seed_user, is_envelope=False)
            txn = generate_row_of(template, seed_periods[0])
            txn.is_deleted = True
            db.session.flush()

            with pytest.raises(ValidationError) as exc:
                transaction_service.settle_transaction(txn)

            # The seam's own sentence, naming the row (ruling R-CC98).
            assert "was deleted: a payment cannot be recorded under it" in (
                str(exc.value)
            )
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert txn.settled_on is None

    def test_settle_amount_refuses_a_soft_deleted_row(
        self, app, seed_user, seed_periods,
    ):
        """The read refuses what the write refuses, for the same reason.

        A deleted row values at ``Decimal("0")`` through ``effective_amount``,
        so answering here would publish a figure ``settle_transaction`` will not
        book -- the argument ``reject_unsettleable``'s transfer-shadow half
        made while transfers had twin rows, applied to the rule that was
        missing beside it.
        """
        with app.app_context():
            template = _make_template(seed_user, is_envelope=False)
            txn = generate_row_of(template, seed_periods[0])
            txn.is_deleted = True
            db.session.flush()

            with pytest.raises(ValidationError) as exc:
                transaction_service.settle_amount(txn, amount_basis_for(txn))

            # The seam's own sentence, naming the row (ruling R-CC98).
            assert "was deleted: a payment cannot be recorded under it" in (
                str(exc.value)
            )

    def test_an_envelope_with_entries_ignores_a_supplied_actual(
        self, app, seed_user, seed_periods,
    ):
        """sum(entries) wins over a caller-supplied actual, and posts.

        The precedence half of act 1: an envelope's entries ARE the record of
        what it cost, so a caller offering a different figure is offering an
        opinion about a derived value.  $40 + $50 against a $500 estimate and a
        supplied $999.99 settles at $90.00.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            db.session.flush()
            _make_entry(txn.id, seed_user["user"].id,
                        Decimal("40.00"), "Walmart")
            _make_entry(txn.id, seed_user["user"].id,
                        Decimal("50.00"), "Sam's")
            db.session.flush()

            transaction_service.settle_transaction(
                txn, submitted=typed(Decimal("999.99")),
            )

            assert settled_figure(txn) == Decimal("90.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert txn.settled_on == display_today()

    def test_a_plain_row_honours_a_supplied_actual(
        self, app, seed_user, seed_periods,
    ):
        """A row with no entries books what the caller supplied.

        The other half of act 1, and the channel ruling **R-FB** gives a first
        real caller: a bill's tick may correct its amount.  $250.00 supplied
        against a $500.00 estimate books $250.00.
        """
        with app.app_context():
            template = _make_template(seed_user, is_envelope=False)
            txn = generate_row_of(template, seed_periods[0])
            db.session.flush()

            transaction_service.settle_transaction(
                txn, submitted=typed(Decimal("250.00")),
            )

            assert settled_figure(txn) == Decimal("250.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_a_plain_row_with_no_correction_records_what_it_booked(
        self, app, seed_user, seed_periods,
    ):
        """Nothing typed still RECORDS what moved -- the one-click path.

        **This asserted the opposite until plan step X-au-c3**: the figure
        column was left NULL, because it doubled as the "a human typed this"
        signal and writing the estimate into it would have manufactured a
        correction nobody made.  The two questions have two columns now, so a
        one-click settle records the figure it booked and says the basis is
        ``derived``.  The row's PLAN is untouched -- no settle writes a plan
        column -- and the figure it books is the same ``$500.00`` the old
        fall-back answered, so no balance moves.
        """
        with app.app_context():
            template = _make_template(seed_user, is_envelope=False)
            txn = generate_row_of(template, seed_periods[0])
            db.session.flush()

            transaction_service.settle_transaction(txn)

            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is the movement's ``figure_source_id``
            # now (a column of the row's own from X-au-c3 to X-bi-4b-2), so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            assert settled_figure(txn) == Decimal("500.00")
            assert txn.estimated_amount is None
            assert _plan_of(txn) == Decimal("500.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_an_illegal_transition_raises_and_leaves_the_row_alone(
        self, app, seed_user, seed_periods,
    ):
        """Settling an already-Cancelled row is refused by the state machine.

        The verb's ``ValidationError`` surface, which the route renders as a
        designed 400.  Graded here rather than only through HTTP so the next
        caller knows what it must catch.
        """
        with app.app_context():
            template = _make_template(seed_user, is_envelope=False)
            txn = generate_row_of(template, seed_periods[0])
            txn.status_id = ref_cache.status_id(StatusEnum.CANCELLED)
            db.session.flush()

            with pytest.raises(ValidationError):
                transaction_service.settle_transaction(txn)

            assert txn.settled_on is None
            assert txn.status_id == ref_cache.status_id(StatusEnum.CANCELLED)

    def test_income_takes_received_and_expense_takes_paid(
        self, app, seed_user, seed_periods,
    ):
        """``settled_status_id`` is the ONE spelling of the income/expense pick.

        It was written twice before plan step X-f2-c2 -- once in the mark-done
        route and once in ``settle_from_entries``, whose comment said it
        "mirrors" the route.  Both ends are graded here so a future third
        spelling has something to fail against.
        """
        with app.app_context():
            expense_tpl = _make_template(seed_user, is_envelope=False)
            expense = generate_row_of(expense_tpl, seed_periods[0])
            income_tpl = _make_template(
                seed_user, income=True, is_envelope=False,
            )
            income = generate_row_of(income_tpl, seed_periods[0])
            db.session.flush()

            assert transaction_service.settled_status_id(expense) == (
                ref_cache.status_id(StatusEnum.DONE)
            )
            assert transaction_service.settled_status_id(income) == (
                ref_cache.status_id(StatusEnum.RECEIVED)
            )

            transaction_service.settle_transaction(expense)
            transaction_service.settle_transaction(income)
            assert expense.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert income.status_id == ref_cache.status_id(StatusEnum.RECEIVED)


class TestASettleBooksTheFreshestFigure:
    """Plan step **X-aq**, ruling **R-FE**, finding **N-224**.

    **The defect these grade is two answers to one question.**
    ``transactions.estimated_amount`` was a CACHE of a derivation that a
    read-time repair recomputed without writing back -- so every balance
    surface showed the live figure while every settle door booked the stored
    one, and settling moved the projected end balance by the difference.  That
    is the exact invariant ruling R-DH (c) states and plan step X-f3 is
    ship-gated on.

    **Plan step X-au-d closed it at the source rather than at the settle**, and
    these cases are what that looks like from here.  A salary row DECLARES the
    definition that prices it and stores no figure, so there is no cache to
    book instead of and none to reconcile: what the settle books is what the
    amount model answers, and its RECORD (the covering movement, on the
    ``resolved`` source) is the only figure the row ends up holding.  A test that passed by
    reading the plan column would now have to report ``None``.

    The setup is the sibling suite's, so the expected net is not invented here:
    a ``$104,000`` profile over 26 periods with no tax configs seeded nets
    ``$4,000.00`` a period, which
    ``test_income_service.TestSalaryNetFor.test_recomputes_live_ignoring_stored_amount``
    pins independently.
    """

    @staticmethod
    def _salary_row(seed_user, period, *, repriced_to=None):
        """Return a Projected income row whose template IS a salary profile.

        The engine's own row (:func:`generate_row_of`, plan step
        balance:X-cf-3b): a paycheck nobody has re-priced DECLARES its
        definition and stores nothing, which is the shape the app produces
        after plan step X-au-d.  **The row's OWNERSHIP follows its override
        flag**: pass *repriced_to* for one a human re-priced, and it takes
        ownership the way the edit door does (:func:`repriced_by_the_owner`).
        Building the two acts independently would let a test assert against
        a state ``ck_transactions_amount_ownership`` and the write doors
        between them cannot produce.

        The definition is linked to the profile AFTER the shared builder
        states its price, so it holds a one-version series nothing reads:
        once linked, ``owns_its_amount`` is False and the row is priced by
        rule 2 whatever that series says (production's salary template holds
        no series at all; the difference is unread either way).
        """
        # Pylint: ``import-outside-toplevel`` -- the salary models are not
        # part of this module's subject and importing them at the top would put
        # the paycheck stack on every transaction-service test's load path.
        # pylint: disable=import-outside-toplevel
        from app.models.ref import FilingStatus
        from app.models.salary_profile import SalaryProfile

        filing = db.session.query(FilingStatus).first()
        profile = SalaryProfile(
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            filing_status_id=filing.id,
            name="X-aq Salary",
            state_code="NC",
            is_active=True,
        )
        db.session.add(profile)
        start_test_pay_list(profile, Decimal("4000.00"))  # $104,000.00 a year / 26
        db.session.flush()

        template = _make_template(
            seed_user, income=True, is_envelope=False,
        )
        # The RELATIONSHIP rather than the key, so ``template.salary_profiles``
        # -- which the shared builder's ``set_amount`` already loaded as empty
        # -- is current in this session without waiting for a commit.
        profile.template = template
        db.session.flush()

        txn = generate_row_of(template, period)
        if repriced_to is not None:
            repriced_by_the_owner(txn, repriced_to)
        return txn

    def test_a_declared_paycheck_settles_at_what_its_PROFILE_pays(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """The row books ``$4,000.00`` and its plan column stays empty.

        The headline of X-aq, restated at X-au-d.  Before X-aq this settle
        booked a stale ``$1.00`` while the grid cell beside it read
        ``$4,000.00`` -- ``$3,999.00`` of income deleted from the projection by
        pressing Mark Paid.  X-aq made the settle ask the same producer the
        grid asked; X-au-d deleted the stale figure, so there is no second
        answer left for either of them to prefer.

        **The settle writes NO plan column**, which is what the deleted
        reconciler used to do here.  A derived row has no cache to reconcile,
        so the only figure this act writes is the RECORD -- and the record says
        WHO wrote it (the movement's ``figure_source_id``), which is what keeps
        a machine's resolution distinguishable from a human's correction.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            txn = self._salary_row(seed_user, seed_periods[0])
            db.session.commit()
            assert txn.estimated_amount is None

            transaction_service.settle_transaction(txn)

            assert txn.estimated_amount is None, (
                "the settle must not hand a derived row back to its owner: "
                "that is finding N-437, and the plan stays the definition's"
            )
            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is the movement's ``figure_source_id``
            # now (a column of the row's own from X-au-c3 to X-bi-4b-2), so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            assert settled_figure(txn) == Decimal("4000.00")
            assert settled_contribution(txn) == Decimal("4000.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.RECEIVED)

    def test_a_supplied_actual_still_wins_over_the_live_figure(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """A figure a human typed beats every derivation.

        The precedence half of act 1 and the reason X-f2-c2's correctable box
        (ruling **R-FB**) is safe: the panel's prefilled amount is what the
        statement says, and a live recompute must not overwrite it.

        **The two facts stay SEPARABLE**, which is what the record's own basis
        column buys: the human's ``$3,912.44`` is what the row RECORDS, and the
        machine's ``$4,000.00`` is what its PLAN still resolves to -- from the
        definition, not from a column, so the plan is not something this settle
        wrote.  Under the shipped-then-withdrawn single-column version the
        resolution was invisible here, so nothing could tell a stale projection
        from an accurate one after the fact.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            txn = self._salary_row(seed_user, seed_periods[0])
            db.session.commit()

            transaction_service.settle_transaction(
                txn, submitted=typed(Decimal("3912.44")),
            )

            assert settled_figure(txn) == Decimal("3912.44")
            assert txn.estimated_amount is None
            assert settled_contribution(txn) == Decimal("3912.44")
            # The PLAN, asserted rather than described: it still resolves from
            # the definition, and it is a different number from the record.
            # An adversarial review of this step found the docstring claiming
            # this and nothing grading it -- ``estimated_amount is None`` says
            # the column is empty, not that the derivation still answers.
            assert amounts_by_key(
                [txn], derived_amount_basis(txn.account.user_id, txn.scenario_id),
            )[txn.id] == Decimal("4000.00")

    def test_an_overridden_row_is_not_re_derived(
        self, app, db, seed_user, seed_periods,
    ):
        """A paycheck a human re-priced settles at the figure they typed.

        **The REASON changed at plan step X-au-d** and the outcome did not.  It
        used to hold because the read-time repair dropped an ``is_override``
        row; it holds now because such a row OWNS its figure, so amount rule 1
        answers it and no salary producer is consulted at all (finding
        **N-262**).  The flag is set here because that is what the edit door
        does beside taking ownership, not because anything reads it to price.
        Owned ``$1,234.56`` settles at ``$1,234.56``.
        """
        with app.app_context():
            txn = self._salary_row(
                seed_user, seed_periods[0], repriced_to="1234.56",
            )
            db.session.commit()

            transaction_service.settle_transaction(txn)

            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is the movement's ``figure_source_id``
            # now (a column of the row's own from X-au-c3 to X-bi-4b-2), so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            assert settled_figure(txn) == Decimal("1234.56")
            assert settled_contribution(txn) == Decimal("1234.56")

    # ``test_an_agreeing_live_figure_leaves_the_column_null`` lived here until
    # plan step X-au-d, and its SUBJECT is deleted rather than the case being
    # weakened.  It graded the cache reconciler's own suppression rule -- that
    # a refresh writes nothing when the live figure already equals the stored
    # one -- so that ``actual_amount``'s NULL-ness stayed readable as "a human
    # typed this".  There is no reconciler and no cache: a derived row's plan
    # column is empty before and after every settle, which
    # ``test_a_declared_paycheck_settles_at_what_its_PROFILE_pays`` asserts
    # directly.  The signal that survived is the movement's
    # ``figure_source_id``, and the ``resolved``-versus-``typed`` pair above
    # is its control.

    def test_a_row_with_no_live_seam_is_untouched(
        self, app, db, seed_user, seed_periods,
    ):
        """An ordinary bill is not a candidate, so X-aq writes nothing.

        The blast-radius control.  ``live_projected_net`` wants an income row
        linked to an active profile and ``live_loan_transfer_amounts`` wants a
        transfer shadow, so an expense on a plain template matches neither and
        the settle is byte-identical to its pre-X-aq behaviour.
        """
        with app.app_context():
            template = _make_template(seed_user, is_envelope=False)
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()

            transaction_service.settle_transaction(txn)

            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is the movement's ``figure_source_id``
            # now (a column of the row's own from X-au-c3 to X-bi-4b-2), so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            assert settled_figure(txn) == Decimal("500.00")
            assert settled_contribution(txn) == Decimal("500.00")

    def test_an_envelope_with_entries_still_settles_at_its_entries(
        self, app, db, seed_user, seed_periods,
    ):
        """The envelope branch is untouched: entries beat every derivation.

        Act 1's ordering, re-graded from the X-aq side.  A row that is BOTH
        envelope-tracked with entries AND income keeps ``sum(entries)`` -- the
        freshest answer for an envelope is its own record of what it cost, not
        a recompute of what it was expected to be.
        """
        with app.app_context():
            template = _make_template(seed_user, income=True)
            txn = generate_row_of(template, seed_periods[0])
            db.session.flush()
            _make_entry(txn.id, seed_user["user"].id,
                        Decimal("12.34"), "Refund")
            db.session.commit()

            transaction_service.settle_transaction(txn)

            assert settled_figure(txn) == Decimal("12.34")

    # ``test_a_hand_typed_actual_is_NEVER_overwritten`` lived here until plan
    # step X-au-c3, and its state is unconstructible now rather than merely
    # untested.  It built a PROJECTED salary row carrying
    # ``actual_amount = 3880.15`` and proved a fourth guard on the settle's
    # cache refresh kept it from booking ``$4,000.00`` over it.  A figure
    # RECORDS a settle now (the covering movement the seam writes, which
    # ``reject_settlement_without_settled_status`` keeps off a row whose money
    # has not moved) -- and the door that
    # produced the state is gone with it: the full-edit form's Actual box was
    # deleted, so the only way a human's pre-settle figure reaches a row is
    # ``estimated_amount``, and stating one there is what TAKES OWNERSHIP of
    # the row (``amount_ownership.state_own_amount``, plan step X-au-d).  That
    # ownership is why no derived rule prices such a row, and
    # ``test_an_overridden_row_is_not_re_derived`` above is the control on it.
    # The refresh and its guards are all deleted with the cache they kept
    # true.

    def test_a_supplied_figure_equal_to_the_booked_one_writes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """The echo rule applies to a CALLER's figure too, not just a derived one.

        It was two rules before an adversarial review: the verb suppressed an
        echo of the LIVE figure and the reconcile writer separately suppressed
        an echo of the SUBMITTED one, so the same column meant different things
        depending on which door wrote it.  Plan step X-ap routes a form here
        that submits ``actual_amount`` on EVERY save, so the caller-supplied
        half is the half that matters next.

        `$500.00` supplied against a `$500.00` estimate leaves the column NULL.
        """
        with app.app_context():
            template = _make_template(seed_user, is_envelope=False)
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()

            transaction_service.settle_transaction(
                txn, submitted=typed(Decimal("500.00")),
            )

            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is the movement's ``figure_source_id``
            # now (a column of the row's own from X-au-c3 to X-bi-4b-2), so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            assert settled_figure(txn) == Decimal("500.00")
            assert settled_contribution(txn) == Decimal("500.00")

    def test_the_figure_is_resolved_BEFORE_the_status_flip(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """Order is load-bearing, and this is the control that says so.

        A row's valuation answers from its SETTLEMENT RECORD once it has
        settled (``row_valuation.fixed_contribution``), so a verb that resolved
        AFTER ``apply_status_change`` would be asking about the very record
        that call had just written -- circular, and it would book whatever the
        seam happened to store.  Grading it as an ORDER rather than as an
        outcome: the row is Projected at the moment the resolution is made.

        **The reason survived plan step X-au-d and its mechanism did not.**  It
        used to be that the read-time repair filtered to Projected rows, so
        asking late always answered "nothing fresher" and the settle silently
        booked the cache.  There is no repair and no cache; what makes the
        order load-bearing now is the settlement record itself.

        **The LIST is the control on the one-resolution rule too** (developer
        ruling, 2026-08-17).  It is asserted as a whole rather than by
        membership, so a settle that resolved the amount twice -- which is what
        the booked figure and the echo comparison each doing their own would
        produce -- fails here with ``[2, 2] != [2]``.  That is the measurement,
        not a claim about it.

        **The spy is installed on the LEAF that owns the name**, not on the
        package that re-exports the verb, and the difference is not cosmetic:
        ``settle_transaction`` resolves ``settle_amount`` as a global of its
        own module, so patching a package attribute would intercept nothing and
        this control would pass while grading nothing.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            txn = self._salary_row(seed_user, seed_periods[0])
            db.session.commit()

            seen_status = []
            real = _settle.settle_amount

            def _spy(row, basis):
                seen_status.append(row.status_id)
                return real(row, basis)

            _settle.settle_amount = _spy
            try:
                transaction_service.settle_transaction(txn)
            finally:
                _settle.settle_amount = real

            assert seen_status == [
                ref_cache.status_id(StatusEnum.PROJECTED),
            ]
            assert txn.estimated_amount is None
            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is the movement's ``figure_source_id``
            # now (a column of the row's own from X-au-c3 to X-bi-4b-2), so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            assert settled_figure(txn) == Decimal("4000.00")


class TestApplyRequestedStatusTheDoorVerb:
    """``apply_requested_status`` -- the route layer's ONE status entry point.

    **It exists because a ROUTE was deciding what a status change means.**  The
    transaction PATCH handler and the cancel handler each called
    ``status_seam.apply_status_change`` -- the MECHANICS primitive, which
    assigns the column and posts nothing -- so the PATCH door could flip a row
    into the settled band without ever asking what the row was worth (finding
    **N-219**).  These grade the verb's two acts: the status change, and the
    ledger reconcile that every status change owes.
    """

    @staticmethod
    def _plain_row(seed_user, period):
        """Return a Projected, NON-envelope $100.00 row: the verb's manual branch.

        The engine's row of a $100.00 definition (plan step balance:X-cf-3b);
        the figure is stated on the definition, which is what prices the row.
        """
        template = _make_template(
            seed_user, default_amount="100.00", is_envelope=False,
        )
        return generate_row_of(template, period)

    def test_a_status_change_reconciles_the_ledger(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled status posts the row's effect, in the same call.

        Act 2 is the point: the seam alone assigns a column and posts nothing,
        so a door that called it settled a row while the double-entry ledger
        stayed flat.  A $100.00 expense settles and exactly ONE journal entry
        appears against it, booking $100.00 out of the cash account.

        Shown to FIRE: deleting the reconcile leaves zero entries.
        """
        with app.app_context():
            txn = self._plain_row(seed_user, seed_periods[0])

            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.DONE),
            )

            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert txn.settled_on == display_today()
            # The row's FAMILY (plan step X-bi-3a): the $100.00 is posted under
            # the covering movement the seam wrote, the parent's leg is zero.
            entries = (
                db.session.query(JournalEntry)
                .filter(family_journal_filter(txn)).all()
            )
            assert len(entries) == 1
            # effective_amount == estimated_amount == 100.00, nothing credited,
            # so the cash account's movement effect is a 100.00 outflow.
            assert posting_service.posted_purchase_effect(
                seed_user["account"].id, seed_user["scenario"].id,
            ) == Decimal("-100.00")

    @staticmethod
    def _ledger_entry_ids(user_id):
        """Return the ids of every journal entry the owner's ledger holds, ascending.

        The ledger is append-only, so a write of any kind -- a leg for this
        row, its movement, or anything else -- adds an id, and the same list
        before and after a call is the whole of "it posted nothing".  These
        cases read the row's own ``transaction_id`` link until plan step
        ``balance:X-bi-6-5``'s second leaf (card plan#1845) deleted it; that
        read had found nothing since plan step ``balance:X-bi-4a``, when a
        row's money moved onto its movements, so it graded nothing.
        """
        return [
            entry_id for (entry_id,) in (
                db.session.query(JournalEntry.id)
                .filter(JournalEntry.user_id == user_id)
                .order_by(JournalEntry.id)
            )
        ]

    def test_a_cancel_leaves_the_ledger_flat(
        self, app, db, seed_user, seed_periods,
    ):
        """Cancelling a Projected row posts nothing, and that IS the reconcile.

        The non-settled arm.  A Projected row has never posted, so reconciling
        to Cancelled's empty target is an idempotent no-op -- the property the
        cancel handler used to spell out for itself and now inherits.  The
        settle day is cleared by the seam, because a cancelled row's money
        never moved.
        """
        with app.app_context():
            txn = self._plain_row(seed_user, seed_periods[0])
            ledger_before = self._ledger_entry_ids(seed_user["user"].id)

            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.CANCELLED),
            )

            assert txn.status_id == ref_cache.status_id(StatusEnum.CANCELLED)
            assert txn.settled_on is None
            assert self._ledger_entry_ids(seed_user["user"].id) == ledger_before

    def test_an_illegal_transition_raises_and_posts_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A refused transition leaves both the row and the ledger untouched.

        Cancelled -> Paid is not in the transaction workflow: a cancelled row
        must be reprojected first, so the audit trail records both moves.  The
        verb must refuse BEFORE it reconciles -- a reconcile run against a
        status the state machine rejected would post an effect the row is not
        entitled to.
        """
        with app.app_context():
            txn = self._plain_row(seed_user, seed_periods[0])
            ledger_before = self._ledger_entry_ids(seed_user["user"].id)
            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.CANCELLED),
            )

            with pytest.raises(ValidationError) as exc:
                transaction_service.apply_requested_status(
                    txn, ref_cache.status_id(StatusEnum.DONE),
                )

            assert "transition" in str(exc.value)
            assert txn.status_id == ref_cache.status_id(StatusEnum.CANCELLED)
            assert self._ledger_entry_ids(seed_user["user"].id) == ledger_before


class TestARevertKeepsWhatMovedAndReleasesTheAssertion:
    """The retention round trips, end to end through the real verbs.

    **The whole of plan step X-au-c3's third design**, and the arm the suite
    could not see before: a row carries THREE facts with THREE lifetimes -- the
    PLAN, WHAT MOVED, and the ASSERTION -- and no column belongs to two.  A
    revert releases the assertion (``settled_on``, ``reconciled_by_id``) and
    keeps what moved, so the revert / edit / re-settle round trip the full-edit
    popover INSTRUCTS the user to perform is lossless.

    Every case below drives ``status_seam.apply_status_change`` and
    ``transaction_service.settle_transaction`` -- the doors -- rather than
    assigning columns, because the claim under test is what those doors do.
    """

    @staticmethod
    def _settle(txn, *, submitted=None):
        """Settle *txn* through the verb, returning whether it booked a human's figure."""
        return transaction_service.settle_transaction(txn, submitted=submitted)

    @staticmethod
    def _revert(txn):
        """Put *txn* back to Projected through the ONE status door."""
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.PROJECTED),
        )

    def test_a_revert_keeps_the_figure_and_drops_the_day(
        self, app, db, seed_user, seed_periods,
    ):
        """The asymmetry, stated as an assertion instead of a comment.

        A first version of this step released all four columns together, under
        a CHECK that paired the day with the basis -- and it cost the user real
        data, because the popover tells them to revert in order to edit.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            self._settle(txn, submitted=typed(Decimal("245.32")))
            db.session.flush()
            assert txn.settled_on is not None
            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                Decimal("245.32"), MovementFigureSourceEnum.TYPED,
            )
            # A statement is recorded as having SHOWN this money, so the
            # release below has something to release.  Asserting
            # ``reconciled_by_id is None`` after a revert without this is
            # vacuous -- it passes on a build that never clears the link, which
            # is half of what "a revert releases the ASSERTION" claims (found
            # by adversarial review, 2026-08-17).
            anchor = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=txn.account_id)
                .order_by(AccountAnchorHistory.id.desc())
                .first()
            )
            assert anchor is not None, "the fixture account carries no anchor"
            txn.reconciled_by_id = anchor.id
            db.session.flush()
            assert txn.reconciled_by_id is not None

            self._revert(txn)
            db.session.flush()

            # The ASSERTION is withdrawn -- BOTH of its columns ...
            assert txn.settled_on is None
            assert txn.reconciled_by_id is None
            # ... and WHAT MOVED survives it, on the kept movement.
            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                Decimal("245.32"), MovementFigureSourceEnum.TYPED,
            )
            # But nothing counts it: the STATUS decides, not the record.
            assert settled_figure(txn) is None

    def test_revert_edit_the_plan_re_settle_books_the_HUMANS_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """The round trip the popover instructs, with an edit in the middle.

        The user reverts to change the PLAN, changes it, and marks the row paid
        again without typing an amount.  The re-settle must book the figure they
        read off their statement -- not the plan they just edited, and not a
        fresh derivation of it.  This is the case a ``derived``-only retention
        rule would get wrong in the direction that loses the user's number.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            self._settle(txn, submitted=typed(Decimal("245.32")))
            db.session.flush()

            self._revert(txn)
            state_own_amount(txn, Decimal("610.00"))
            db.session.flush()

            booked_a_human_figure = self._settle(txn)
            db.session.flush()

            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                Decimal("245.32"), MovementFigureSourceEnum.TYPED,
            )
            assert settled_figure(txn) == Decimal("245.32")
            # The plan edit stands, and is a different fact from what moved.
            assert txn.estimated_amount == Decimal("610.00")
            # Nobody typed anything at THIS tick, so the reconcile writer's
            # correction count does not count it (finding N-231).
            assert booked_a_human_figure is False

    def test_the_panel_OFFERS_exactly_what_the_re_settle_BOOKS(
        self, app, db, seed_user, seed_periods,
    ):
        """Finding C1, pinned: the offer and the booking are one expression.

        Measured before ``honoured_correction`` answered both: the panel offered
        a reverted row at its ``$500.00`` plan and the tick booked ``$245.32``.
        Worse than the drift, no input meant "book the plan" -- a submitted
        figure counts as a correction only when it DIFFERS from the offer, so
        typing ``$500.00`` was read as an echo of a figure never shown.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            self._settle(txn, submitted=typed(Decimal("245.32")))
            db.session.flush()
            self._revert(txn)
            db.session.flush()

            offered = transaction_service.settle_amount(txn, amount_basis_for(txn))
            assert offered == Decimal("245.32")

            self._settle(txn)
            db.session.flush()
            assert settled_figure(txn) == offered

    def test_a_retained_DERIVED_record_is_RE_DERIVED_not_reused(
        self, app, db, seed_user, seed_periods,
    ):
        """Only a HUMAN's figure is honoured; the app's own inference is redone.

        A ``resolved`` record is what the app resolved at a moment that has
        passed, and re-resolving is strictly better -- the plan may legitimately
        have been re-priced meanwhile.  **This is the case a mutant dropping
        ``retained.stated`` from ``Settlement.from_settle`` survives**: with a
        stated record the two arms agree, so only a retained RESOLVED record
        whose plan has since MOVED can tell them apart.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            self._settle(txn)
            db.session.flush()
            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                Decimal("500.00"), MovementFigureSourceEnum.RESOLVED,
            )

            self._revert(txn)
            state_own_amount(txn, Decimal("610.00"))
            db.session.flush()
            # Nothing is OFFERED from the retained record either -- the offer
            # and the booking are one expression, so both re-derive.
            assert transaction_service.settle_amount(
                txn, amount_basis_for(txn),
            ) == Decimal("610.00")

            self._settle(txn)
            db.session.flush()

            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                Decimal("610.00"), MovementFigureSourceEnum.RESOLVED,
            )

    def test_a_reverted_PURCHASES_row_re_sums_its_entries(
        self, app, db, seed_user, seed_periods,
    ):
        """An envelope retains a record that stores NO figure, and re-derives.

        ``purchases`` is the basis with nothing to retain: the row's own
        children state the figure, so a revert leaves the record naming a basis
        and no amount, and a re-settle re-sums whatever the entries now say.
        The re-sum is the point -- an envelope reverted in order to CHANGE its
        purchases must close at what it now holds.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            _make_entry(txn.id, seed_user["user"].id, "40.00", "Store A")
            db.session.flush()

            self._settle(txn)
            db.session.flush()
            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                None, None,
            )
            assert txn.covering_movements == []
            assert settled_figure(txn) == Decimal("40.00")

            self._revert(txn)
            db.session.flush()
            # The row records nothing of its own to survive the revert: its
            # entries are the record, and they stand (ruling **R-BAL82**).
            assert status_seam.recorded_settlement(txn) is None
            assert txn.covering_movements == []
            assert settled_figure(txn) is None

            _make_entry(txn.id, seed_user["user"].id, "12.50", "Store B")
            db.session.flush()
            # ``_make_entry`` inserts by parent ID rather than through the
            # relationship, so ``txn.entries`` is still the collection the
            # FIRST settle loaded.  Expiring it is what a second REQUEST does
            # for free, and the re-sum is the thing under test.
            db.session.expire(txn, ["entries"])
            self._settle(txn)
            db.session.flush()

            assert settled_figure(txn) == Decimal("52.50")
            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                None, None,
            )

    def test_a_reverted_row_that_is_then_CANCELLED_counts_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """The retained figure never leaks back through a non-settled status.

        Cancelled is ``excludes_from_balance``, so the row is worth ``0`` -- and
        the arm that says so sits ABOVE the settlement read in
        ``row_valuation.fixed_contribution``, which is what makes the order of
        those two arms load-bearing rather than incidental.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            self._settle(txn, submitted=typed(Decimal("245.32")))
            db.session.flush()
            self._revert(txn)
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.CANCELLED),
            )
            db.session.flush()

            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                Decimal("245.32"), MovementFigureSourceEnum.TYPED,
            )
            assert settled_figure(txn) is None
            assert settled_contribution(txn) == Decimal("0")


class TestAReplayedSettleIsANoOp:
    """``is_identity_move``'s firing control -- the guard the suite could not see.

    **Measured 2026-08-17: the FULL suite (9,611 tests) passed with this guard
    disabled.**  A guard whose removal no test can see is not a guard, and this
    class is what makes it one.

    The defect the guard was written for is now closed twice over --
    ``Settlement.from_settle`` honours a retained ``corrected`` record, so a
    replayed manual settle no longer destroys provenance by re-deriving.  What
    it still UNIQUELY protects is the ENVELOPE replay: ``settle_from_entries``
    has preconditions a settled row fails, so without the early return a
    double-clicked Mark Paid on an already-Paid envelope answers 400 where the
    user has done nothing wrong and nothing needs doing.
    """

    def test_a_replayed_close_on_an_ENVELOPE_is_a_no_op_not_a_400(
        self, app, db, seed_user, seed_periods,
    ):
        """The identity move this guard exists for, and the only one that needs it.

        Shown to FIRE: disabling the early return in ``settle_transaction``
        raises ``ValidationError`` here instead of answering ``False``.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            _make_entry(txn.id, seed_user["user"].id, "40.00", "Store A")
            db.session.flush()

            transaction_service.settle_transaction(txn)
            db.session.flush()
            recorded = settled_figure(txn)
            day = txn.settled_on

            # The double click / stale tab / re-POST.
            assert transaction_service.settle_transaction(txn) is False
            db.session.flush()

            assert settled_figure(txn) == recorded
            assert txn.settled_on == day

    def test_it_is_NARROWER_than_the_settled_band(
        self, app, db, seed_user, seed_periods,
    ):
        """``Paid -> Received`` still owes the state machine an answer.

        A first version of this guard asked ``status_id in settled_status_ids()``
        and thereby swallowed an ILLEGAL transition, turning a designed 400 into
        a silent 200.  Only the IDENTITY move is nothing to do.

        The specimen was ``Settled -> Paid`` until plan step **balance:X-am**
        deleted the archive.  ``Paid -> Received`` is now the ONLY non-identity
        move inside the settled band -- the band is exactly those two -- so it
        is the one case that can still tell a band test from an identity test.
        Without it this guard's narrowness would be unfalsifiable.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            _make_entry(txn.id, seed_user["user"].id, "40.00", "Store A")
            db.session.flush()
            # An INCOME row, so its type-correct settle target is Received and
            # the Paid it holds is the other member of the band.
            txn.transaction_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(
                    txn, ref_cache.status_id(StatusEnum.DONE),
                ),
            )
            db.session.flush()

            # The row holds Paid; its TYPE settles as Received, which is not
            # the status it holds -- so this is a transition, not a re-submit,
            # and the state machine refuses it.
            with pytest.raises(ValidationError):
                transaction_service.settle_transaction(txn)


class TestTheRetainedMapAnswersOnlyWhereTheGapIsREAL:
    """``retained_settle_amounts_by_id``'s status gate, which nothing graded.

    The map's contract is narrow and its whole value is the narrowness: a
    non-``None`` entry means "this row will book something other than what you
    can SEE", so a template draws a marker without deciding anything.  A settled
    row is already showing its recorded figure
    (``row_valuation.settled_amounts_by_key``), so a marker there would tell the
    user a settled row is about to book something -- which it is not.

    **Measured 2026-08-17 by an adversarial mutation pass**: dropping the
    settled-status gate left the whole suite green, so the badge would have
    appeared on every settled envelope and every settled corrected row with
    nothing to say so.
    """

    def test_a_SETTLED_row_is_not_in_the_map(
        self, app, db, seed_user, seed_periods,
    ):
        """The gate, from both sides in one case.

        The same row is asked twice -- once settled, once reverted -- so the
        test cannot pass by answering ``None`` for everything, which is the
        shape a one-sided assertion would admit.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            transaction_service.settle_transaction(
                txn, submitted=typed(Decimal("245.32")),
            )
            db.session.flush()

            settled_map = transaction_service.retained_settle_amounts_by_id(
                [txn],
            )
            assert settled_map[txn.id] is None, (
                "a settled row shows its recorded figure already; a 'will "
                "book' marker on one is a second answer to a settled question"
            )

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.flush()

            reverted_map = transaction_service.retained_settle_amounts_by_id(
                [txn],
            )
            assert reverted_map[txn.id] == Decimal("245.32")


class TestTheDoorAppliesTheStatusANDTheCorrection:
    """A status change and a figure correction are INDEPENDENT facts.

    **The door treated them as alternatives, and that dropped status changes on
    the floor** -- measured on 2026-08-18, not reasoned.  ``apply_requested_status``
    recorded a submitted figure and RETURNED, so a row moving Paid -> Settled
    while carrying a corrected Actual recorded the figure and stayed Paid,
    answering 200.  Both controls are on the same popover -- the Status dropdown
    sits beside the Actual box -- so a user correcting a figure on the way to
    filing the row away got half of what they asked for, silently.

    **The sharpest specimen is GONE and this says so rather than leaving the
    reader to notice.**  ``Paid -> Settled`` was a real status MOVE that a
    correction could ride, and plan step **balance:X-am** deleted the terminal
    ``Settled`` archive.  A settled row's only remaining status move is the
    REVERT, and a revert carrying a CHANGED figure is deliberately refused
    (below) rather than composed -- so the "move plus correction" case has no
    legal instance left and its test went with the status.

    What still grades the composition is the IDENTITY move below: the door is
    handed a status AND a figure in one call and must apply both, which is the
    property whose absence dropped status changes on the floor.  What is no
    longer graded is the same composition across a status BOUNDARY, and that is
    a real reduction -- recorded here, not absorbed.  The first status move
    added to a settled row's map owes this class a case.
    """

    #: A settle day that is NOT the user's today.  A settle stamps
    #: ``display_today()``, so on a today-dated row "the correction preserved
    #: the day" and "the seam re-stamped it" read the same and the assertion
    #: grades nothing -- finding **N-146**'s shape, which this arc has already
    #: paid for once.
    _SETTLED_DAY_OFFSET = timedelta(days=3)

    @classmethod
    def _settled_row(cls, seed_user, period):
        """Return a $100.00 row settled at its plan, on a PAST day, through the real verb.

        The engine's row of a $100.00 definition (plan step balance:X-cf-3b).
        """
        template = _make_template(
            seed_user, default_amount="100.00", is_envelope=False,
        )
        txn = generate_row_of(template, period)
        transaction_service.apply_requested_status(
            txn, ref_cache.status_id(StatusEnum.DONE),
            settle_day=an_entered_day(display_today() - cls._SETTLED_DAY_OFFSET),
        )
        return txn

    def test_a_correction_with_a_status_IN_HAND_does_BOTH(
        self, app, db, seed_user, seed_periods,
    ):
        """The identity case: the figure lands, the status stays, the ledger moves.

        The composition, and since plan step **balance:X-am** the only legal
        instance of it: the door is handed a status AND a figure in ONE call
        and must apply both.  It used to record the figure and RETURN, so the
        status half was dropped -- measured 2026-08-18 on ``Paid -> Settled``,
        the move that no longer exists.

        Hand arithmetic: the row settles at its $100.00 plan, so the cash
        account is $100.00 down.  The correction restates the figure as
        $87.10, so the settled effect becomes -$87.10 -- the difference
        re-posted, not a second full booking.

        The settle day is preserved, because a figure correction moves no day
        and ruling **R-FL** releases the clearing link on the DAY alone.
        """
        with app.app_context():
            txn = self._settled_row(seed_user, seed_periods[0])
            assert settled_figure(txn) == Decimal("100.00")
            day = txn.settled_on

            transaction_service.apply_requested_status(
                txn, txn.status_id,
                stated=transaction_service.StatedRecord(figure=typed(Decimal("87.10"))),
            )

            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert settled_figure(txn) == Decimal("87.10")
            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.TYPED
            )
            assert txn.settled_on == day, (
                "a figure correction moved the settle day"
            )
            # The POSTING LEDGER, read from ``budget.journal_entries``.
            # ``posting_service.settled_transaction_effect`` is NOT an oracle
            # here: it queries ``budget.transactions`` through
            # ``posting_reads.settled_figure_clause``, the query-tier twin of
            # ``row_valuation.settled_figure`` -- so asserting on it is the
            # line above restated in SQL, and deleting the reconcile from the
            # door leaves it green (measured by a neutral review, 2026-08-18).
            # The net per day is what separates "re-booked" from "booked twice".
            assert net_posted_by_day(
                family_journal_filter(txn),
            ) == {day: Decimal("87.10")}

    def test_a_REVERT_carrying_a_figure_is_refused_and_changes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """Two contradictory facts in one call is a programming error, not a save.

        A revert says the money did not move; a figure says this much did.  The
        door used to record the figure, post the ledger difference, and never
        revert -- booking money for a row it had just been told had not moved.
        It refuses now, BEFORE the seam runs, so the row is untouched.

        A FORM reaches this whenever the user CHANGED the box, which is the
        point: an untouched prefill is still dropped on the way out of the band
        (ruling **R-EG**, so the unlock path keeps working), and only a figure
        that differs from the record is refused.  ``figure_for_status`` draws
        that line, and until it did both were discarded.
        """
        with app.app_context():
            txn = self._settled_row(seed_user, seed_periods[0])
            # The LEDGER before, from the journal rather than from the row --
            # see the archive test for why the row-tier reader is not an oracle.
            # The row's FAMILY, its movements' entries: it read the row's own
            # ``transaction_id`` link until card plan#1845 deleted it, which
            # had found nothing since plan step ``balance:X-bi-4a``.
            ledger_before = net_posted_by_day(family_journal_filter(txn))
            assert ledger_before == {txn.settled_on: Decimal("100.00")}

            with pytest.raises(ValidationError) as exc:
                transaction_service.apply_requested_status(
                    txn, ref_cache.status_id(StatusEnum.PROJECTED),
                    stated=transaction_service.StatedRecord(figure=typed(Decimal("123.45"))),
                )

            assert "has nothing to record" in str(exc.value)
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert settled_figure(txn) == Decimal("100.00")
            assert net_posted_by_day(family_journal_filter(txn)) == ledger_before, (
                "a refused request posted to the ledger anyway"
            )

    def test_an_ECHO_leaves_the_derived_basis_standing(
        self, app, db, seed_user, seed_periods,
    ):
        """Re-posting the prefilled figure must not manufacture a correction.

        The echo rule's firing control at this tier: the movement's source is
        the only stored signal that a human read a number off a statement,
        and an untouched Save posts the box's contents back on every edit.
        """
        with app.app_context():
            txn = self._settled_row(seed_user, seed_periods[0])
            resolved = MovementFigureSourceEnum.RESOLVED
            assert status_seam.recorded_settlement(txn).source is resolved

            transaction_service.apply_requested_status(
                txn, txn.status_id,
                stated=transaction_service.StatedRecord(figure=typed(Decimal("100.00"))),
            )

            assert status_seam.recorded_settlement(txn).source is resolved


class TestTheRetainedMapAnswersOnlyARetainedCORRECTION:
    """``retained_settle_amounts_by_id`` draws the re-book badge, and it lied.

    The badge's caption is *"the figure recorded before this row was set back to
    Projected"*, so a non-``None`` value must mean exactly that.  The map
    delegated to ``fixed_settle_amount``, whose FIRST arm is the purchases sum
    -- so every unsettled ENVELOPE carrying a purchase came back with a figure
    and got the badge, on a row that has never settled and records nothing
    (neutral review, 2026-08-18).  The function's own docstring already said
    such a row must answer ``None``.
    """

    def test_a_never_settled_envelope_with_a_purchase_gets_no_badge(
        self, app, db, seed_user, seed_periods,
    ):
        """The defect, stated as the row that produced it.

        A `$400.00` envelope with one `$25.00` purchase, never settled.  The
        cell already renders `25 / 400`; the badge would append `$25.00` again
        under a sentence that is false for this row.
        """
        with app.app_context():
            template = _make_template(seed_user, default_amount="400.00")
            txn = generate_row_of(template, seed_periods[0])
            _make_entry(txn.id, seed_user["user"].id, "25.00", "Milk")
            db.session.flush()

            assert status_seam.recorded_settlement(txn) is None, "fixture: nothing recorded"
            assert transaction_service.retained_settle_amounts_by_id(
                [txn],
            ) == {txn.id: None}

    def test_a_reverted_row_holding_a_correction_DOES_get_one(
        self, app, db, seed_user, seed_periods,
    ):
        """The firing control, and the case the badge exists for.

        A revert releases the assertion and KEEPS what moved, so this row's plan
        is what the balance counts while a re-settle books the retained
        `$245.32` -- two numbers about one row, and the second is on no other
        surface.
        """
        with app.app_context():
            template = _make_template(seed_user, is_envelope=False)
            txn = generate_row_of(template, seed_periods[0])
            db.session.flush()
            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.DONE),
                stated=transaction_service.StatedRecord(figure=typed(Decimal("245.32"))),
            )
            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )

            assert transaction_service.retained_settle_amounts_by_id(
                [txn],
            ) == {txn.id: Decimal("245.32")}

    def test_a_reverted_row_holding_a_DERIVED_record_gets_none(
        self, app, db, seed_user, seed_periods,
    ):
        """The second control: only a HUMAN's figure outlives the revert.

        A ``derived`` record is the app's own inference about a moment that has
        passed, and a re-settle re-resolves it -- so the row's plan IS what a
        tick books and the badge would state a difference that does not exist.
        """
        with app.app_context():
            template = _make_template(seed_user, is_envelope=False)
            txn = generate_row_of(template, seed_periods[0])
            db.session.flush()
            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.DONE),
            )
            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )

            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            assert transaction_service.retained_settle_amounts_by_id(
                [txn],
            ) == {txn.id: None}


class TestTheDeleteDoorReconcilesItsOwnerArgument:
    """``delete_transaction`` checks the owner it is handed against the row.

    Finding **N-373**, landed at plan step **pay_calendar:C13-b**.  This door
    took an ``owner_id`` for its events and reconciled it against the row
    NOWHERE, while its sibling ``entry_service.delete_entry`` re-validated --
    so of the two doors ``statement_match._release._remove`` calls, one asked
    and one trusted.  Both live callers prove ownership first, so the finding
    measured ``$0.00`` and no reachable defect; what it names is a PERMISSION
    a third caller would inherit.

    It is ONE comparison because ``budget.transactions`` carries its owner as
    a COLUMN since plan step ``C13-a``.  Before that the predicate was a join
    walk through the paycheck, which is why the finding was sequenced behind
    the key rather than fixed when it was raised.

    The refusal is a ``NotFoundError`` naming only the row's id -- the same
    shape and the same silence as the sibling door, per the security response
    rule that "not yours" and "not found" read alike.
    """

    def test_a_foreign_owner_is_refused(
        self, app, seed_user, seed_periods, second_user,
    ):
        """A caller naming another user as the owner gets the 404 shape."""
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            db.session.flush()

            with pytest.raises(NotFoundError) as exc:
                transaction_service.delete_transaction(
                    txn, second_user["user"].id,
                )
            assert str(txn.id) in str(exc.value)
            assert not txn.is_deleted

    def test_the_owner_is_refused_BEFORE_the_deletion_rules(
        self, app, seed_user, seed_periods, second_user,
    ):
        """The ownership arm runs ahead of ``deletion_refusal``.

        Ordering is the half a status code cannot show: a foreign caller must
        be told NOTHING about the row, so the ownership refusal has to come
        before the rules that name WHY a row may not be deleted.  The row here
        is one ``deletion_refusal`` rejects by name -- a CC payback -- so the
        two arms give different exceptions and the order is observable: put
        ownership second and this call comes back as a ``ValidationError``
        whose sentence tells a stranger the row is a payback.

        The OWNER's own call is made too, and it must reach that
        ``ValidationError``: without it this case would also pass on a door
        that refused everything.
        """
        with app.app_context():
            template = _make_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            # Built directly: a payback carries no template, because
            # ``ck_transactions_one_pricing_link`` allows a row exactly one
            # pricing parent.
            payback = Transaction(
                user_id=seed_user["user"].id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Tracked Expense (payback)",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                amount_ownership=AmountOwnership.own(Decimal("500.00")),
                credit_payback_for_id=source.id,
            )
            db.session.add(payback)
            db.session.flush()

            with pytest.raises(NotFoundError) as exc:
                transaction_service.delete_transaction(
                    payback, second_user["user"].id,
                )
            assert "payback" not in str(exc.value).lower()

            with pytest.raises(ValidationError):
                transaction_service.delete_transaction(
                    payback, seed_user["user"].id,
                )
