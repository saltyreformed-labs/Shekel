"""Lifecycle tests for the transfer -> posting-ledger wiring (Step 2, Commit 5).

Commit 5 wired :func:`~app.services.posting_service.sync_transfer_postings`
into the transfer service's mutation chokepoints (``update_transfer`` /
``delete_transfer`` / ``restore_transfer``), so settling, reverting,
cancelling, deleting, and restoring a transfer kept the append-only
double-entry ledger in step WITHOUT any caller touching ``posting_service``
directly, until plan task plan#3075 deleted those syncs: the ledger these
tests read is worked out from the records, so nothing follows a write.  These
tests drive transfers END TO END through ``transfer_service``
only (the way the mark-done / cancel / delete routes do) and assert the
resulting ledger state, read from the ledger worked out from the records (the
store's own entries are not graded here since plan step plan#2914).  **A
settled transfer books each side's covering movement against the owner's
Transfers-in-transit account since plan step ``balance:X-bi-6-3``** (rulings
**R-BAL45** and **R-BAL101**).  The truth table:

  * ``projected -> done``       books both sides (+effect);
  * ``done -> settled``         is an idempotent no-op (already at target);
  * ``done -> projected``       takes the effect back off;
  * ``projected -> cancelled``  books nothing (never settled);
  * soft-delete of a settled transfer takes its effect off, and restore
    brings back a plan, which books nothing;
  * hard-delete of a settled transfer takes its effect off with the row;
  * a double mark-done never double-books;
  * settling and setting the figure in ONE call posts the RECORDED figure
    (the reconcile runs after every kwarg is applied, NOT inside the
    status-change helper -- the placement that makes the grid shadow-edit
    path correct).

After each mutation the per-account reconciliation invariant is asserted in
its Build-Order Step 5 ABSOLUTE form: the account's worked-out net ``== opening
anchor + settled_transfer_effect``.  ``create_account`` posts each fixture
account's opening correction at create time (Checking $1000.00, the Savings
$100.00 sentinel), and every settle here is stamped at server-now -- AFTER the
origination assertion -- so the settled-shadow effect is exactly the
post-assertion effect riding on top of the opening.  All money is ``Decimal``
from strings, with the arithmetic shown per the testing standard.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; test bodies bind fixtures by name.
from __future__ import annotations

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db as _db
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import posting_service, transfer_service
from app.services.ledger_lines import linked_key, transit_key
from tests._ledger_reads import (
    account_net,
    ledger_net,
    period_nets_of_movements,
    transfer_family_movement_ids,
)
from tests._test_helpers import (
    create_account_of_type,
    typed,
)
from app.services import cash_ledger
from app.models.amount_ownership import AmountOwnership


# ---------------------------------------------------------------------------
# Helpers and fixtures
# ---------------------------------------------------------------------------


def _family_nets(transfer, scenario_id):
    """Return ``{chart key: net}`` over the lines of the transfer's two records.

    Both sides file in the transfer's one paycheck, and the unpacking says so.
    """
    (nets,) = period_nets_of_movements(
        transfer_family_movement_ids(transfer.id), scenario_id,
    ).values()
    return nets


def _create_projected_transfer(seed_user, from_account, to_account, amount):
    """Create a Projected ad-hoc transfer via the service (posts nothing yet).

    Routed through ``transfer_service.create_transfer`` -- the sole transfer
    writer -- so the two shadows obey every transfer invariant.  A Projected
    transfer never goes through the status seam and so posts no ledger
    entry until it is settled.
    """
    return transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=from_account.id,
            to_account_id=to_account.id,
            pay_period_id=seed_user["bootstrap_period"].id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(amount),
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            category_id=None,
        ),
    )


def _settle(transfer, user_id, **extra):
    """Settle a transfer (Projected -> Done) through the service chokepoint.

    Mirrors the ``transfers.mark_done`` route: status -> Done, stating no day,
    so both sides borrow the owner's today (plan step ``balance:X-bi-6-4c-3``,
    ruling **R-BAL163**; it stated today as ``entered``, what the press
    recorded, until then).  Extra kwargs (e.g. ``figure``) are forwarded so a
    test can settle and state what moved in one call.
    """
    transfer_service.update_transfer(
        transfer.id, user_id,
        status_id=ref_cache.status_id(StatusEnum.DONE),
        **extra,
    )


def _assert_reconciles(scenario_id, *accounts):
    """Assert ledger == settled-shadow effect for each account (the oracle).

    The per-account reconciliation invariant in its Step-5 ABSOLUTE form: the
    net of an account's posting legs equals its opening anchor correction
    (posted at create time; the origination row is each fixture account's
    only assertion) plus the net effect of its settled, non-deleted transfer
    shadows -- every settle in this suite is stamped at server-now, after the
    assertion instant, so the effect rides on top of the opening.  Asserted
    from independent producers so a divergence fails loudly.
    """
    for account in accounts:
        posted = account_net(_db.session, account.id, scenario_id)
        effect = posting_service.settled_transfer_effect(account.id, scenario_id)
        opening = cash_ledger.resolve_anchor(account).balance
        assert posted == opening + effect, (
            f"account {account.id}: ledger {posted} != opening {opening} "
            f"+ settled effect {effect}"
        )


@pytest.fixture()
def savings(app, db, seed_user):  # pylint: disable=unused-argument
    """A second (Savings) account so a transfer has a destination.

    Created in the ``db`` fixture's app context (no nested context) so the
    returned account stays bound to the live session, the pattern ``seed_user``
    uses.  ``create_account_of_type`` fires the Step-2 ledger-account sync
    hook, so the account already carries its paired ledger account.
    """
    acct = create_account_of_type(
        seed_user, _db.session, "Savings", "Lifecycle Savings",
    )
    _db.session.commit()
    return acct


# ---------------------------------------------------------------------------
# Settle: projected -> done posts one balanced entry
# ---------------------------------------------------------------------------


class TestSettlePostsEntry:
    """Settling a transfer through the service auto-posts its effect."""

    def test_mark_done_posts_one_balanced_entry(
        self, app, db, seed_user, savings,
    ):
        """projected -> done posts -100 / +100 via transit; the ledger reconciles.

        Arithmetic: settling a $100 Checking -> Savings transfer books each
        side (plan step ``balance:X-bi-6-3``): -100.00 on Checking's own line
        (money out, a credit) against +100.00 transit, and +100.00 on Savings'
        (money in, a debit) against -100.00 transit; transit nets 0.00.  The
        reconcile invariant holds: Savings nets 100.00 (opening) + 100.00
        (effect) = 200.00.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            transfer = _create_projected_transfer(
                seed_user, checking, savings, Decimal("100.00"),
            )
            _db.session.commit()

            _settle(transfer, user_id)
            _db.session.commit()

            assert _family_nets(transfer, scenario_id) == {
                linked_key(checking): Decimal("-100.00"),
                linked_key(savings): Decimal("100.00"),
                transit_key(): Decimal("0.00"),
            }
            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("200.00")
            _assert_reconciles(scenario_id, checking, savings)

    def test_done_to_settled_archive_is_noop(
        self, app, db, seed_user, savings,
    ):
        """done -> settled books nothing more (already at target).

        Arithmetic: the settle posted +100 to Savings (total 200.00 on the
        $100.00 opening); archiving Done -> Settled keeps both movements
        dated under contributing parents, so nothing moves: Savings still
        nets 200.00 and the ledger still reconciles.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            transfer = _create_projected_transfer(
                seed_user, seed_user["account"], savings, Decimal("100.00"),
            )
            _db.session.commit()
            _settle(transfer, user_id)
            _db.session.commit()

            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            _db.session.commit()

            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("200.00")
            _assert_reconciles(scenario_id, seed_user["account"], savings)


# ---------------------------------------------------------------------------
# Revert: done -> projected reverses to zero (append-only)
# ---------------------------------------------------------------------------


class TestRevertReverses:
    """Reverting a settled transfer takes its effect back off."""

    def test_revert_to_projected_reverses_to_zero(
        self, app, db, seed_user, savings,
    ):
        """done -> projected takes the effect off; Savings is back on its opening.

        Arithmetic: the settle posted +100 on Savings and -100 on Checking
        (each against transit); reverting UN-DATES both covering movements
        (ruling **R-BAL61**), so neither side books anything.  The reverted
        transfer's side records are un-dated, so it drops from
        settled_transfer_effect, and the Savings total lands back on its
        $100.00 opening.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            transfer = _create_projected_transfer(
                seed_user, checking, savings, Decimal("100.00"),
            )
            _db.session.commit()
            _settle(transfer, user_id)
            _db.session.commit()

            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            _db.session.commit()

            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("100.00")
            _assert_reconciles(scenario_id, checking, savings)


# ---------------------------------------------------------------------------
# Cancel: projected -> cancelled posts nothing
# ---------------------------------------------------------------------------


class TestCancelPostsNothing:
    """Cancelling a never-settled transfer books nothing."""

    def test_cancel_projected_posts_nothing(
        self, app, db, seed_user, savings,
    ):
        """projected -> cancelled books nothing (never settled).

        Arithmetic: a Projected transfer has no posted effect; cancelling keeps
        the target at 0 (is_settled False), delta 0, nothing written.  Both
        accounts stay on their openings (Savings 100.00) with a zero settled
        effect.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            transfer = _create_projected_transfer(
                seed_user, checking, savings, Decimal("100.00"),
            )
            _db.session.commit()

            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.CANCELLED),
            )
            _db.session.commit()

            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("100.00")
            _assert_reconciles(scenario_id, checking, savings)


# ---------------------------------------------------------------------------
# Delete + restore
# ---------------------------------------------------------------------------


class TestDeleteAndRestore:
    """Deleting a settled transfer reverses it; restoring brings back a plan."""

    def test_soft_delete_settled_reverses_then_restore_brings_back_a_plan(
        self, app, db, seed_user, savings,
    ):
        """Soft-delete takes a settled transfer off; restore brings back a plan.

        Arithmetic: settle +100; soft-delete takes it off (Savings back on its
        100.00 opening); restore books NOTHING (Savings 100.00).

        The restore re-posted +100 (Savings 200.00) until plan step
        ``balance:X-bi-6-4d-2``.  That step's soft delete takes both payment
        records off (ruling **credit_card:R-CC75**), and it sets the transfer
        back to Projected (ruling **R-BAL246**, "Back as a plan, now",
        developer 2026-10-09): what the restore brings back is a plan holding
        no record, which books nothing.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            transfer = _create_projected_transfer(
                seed_user, checking, savings, Decimal("100.00"),
            )
            _db.session.commit()
            _settle(transfer, user_id)
            _db.session.commit()

            # Soft-delete takes the settled effect off.
            transfer_service.delete_transfer(transfer.id, user_id, soft=True)
            _db.session.commit()
            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("100.00")
            # The reverted shadows are soft-deleted, so the effect is 0 too.
            _assert_reconciles(scenario_id, checking, savings)

            # Restore brings back a plan, which posts nothing.
            transfer_service.restore_transfer(transfer.id, user_id)
            _db.session.commit()
            assert transfer.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert _db.session.query(TransactionEntry).filter(
                (TransactionEntry.expense_transfer_id == transfer.id)
                | (TransactionEntry.income_transfer_id == transfer.id),
            ).count() == 0
            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("100.00")
            _assert_reconciles(scenario_id, checking, savings)

    def test_hard_delete_after_soft_delete_writes_no_more_entries(
        self, app, db, seed_user, savings,
    ):
        """Soft-delete then hard-delete: the second delete moves nothing more.

        The delete door is idempotent through ``allow_deleted=True``.
        Arithmetic: settle +100, soft-delete takes it off, hard-delete: the
        transfer row gone, Savings on its 100.00 opening, transit at 0.00.
        The soft delete takes both payment records off (ruling
        **credit_card:R-CC75**, plan step ``balance:X-bi-6-4d-2``), and the
        transfer then waits as a plan (ruling **R-BAL246**).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            transfer = _create_projected_transfer(
                seed_user, seed_user["account"], savings, Decimal("100.00"),
            )
            _db.session.commit()
            _settle(transfer, user_id)
            _db.session.commit()
            transfer_id = transfer.id

            transfer_service.delete_transfer(transfer_id, user_id, soft=True)
            _db.session.commit()

            transfer_service.delete_transfer(transfer_id, user_id, soft=False)
            _db.session.commit()

            assert _db.session.get(Transfer, transfer_id) is None
            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("100.00")
            assert ledger_net(_db.session, transit_key(), scenario_id) == 0

    def test_hard_delete_settled_reverses_and_pairs_survive_null_links(
        self, app, db, seed_user, savings,
    ):
        """Hard-delete of a settled transfer: the row goes, and its effect with it.

        Arithmetic: settle +100 on Savings; the hard delete removes the
        transfer row (the removal act deletes the sides' covering movements
        first; a side's key no longer cascades to them since plan step
        ``credit_card:CC-5-4a-4``), so Savings is back on its 100.00 opening.
        The name is the stored entries' survival, links nulled, which plan
        step plan#2914 took off the suite's grading;
        ``test_transfer_service/test_the_delete_door.py`` cites this case by
        it for the row being gone.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            transfer = _create_projected_transfer(
                seed_user, checking, savings, Decimal("100.00"),
            )
            _db.session.commit()
            _settle(transfer, user_id)
            _db.session.commit()
            transfer_id = transfer.id

            transfer_service.delete_transfer(transfer_id, user_id, soft=False)
            _db.session.commit()

            # The transfer row is gone.
            assert _db.session.get(Transfer, transfer_id) is None
            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("100.00")


# ---------------------------------------------------------------------------
# A $0.00 correction withdraws the movements and their legs
# ---------------------------------------------------------------------------


class TestZeroCorrectionWithdrawsTheLegs:
    """Correcting a settled transfer to $0.00 reverses both sides' legs."""

    def test_zero_figure_on_a_settled_transfer_reverses_both_sides(
        self, app, db, seed_user, savings,
    ):
        """A settled $100 transfer corrected to $0.00 nets to zero everywhere.

        The status seam WITHDRAWS a covering movement a ``$0.00`` record lands
        on (no movement can carry a zero figure), and reversed its leg first
        through the one removal act's ``posting_service.reverse_leaving_movements``
        (``reverse_purchase_postings_before_delete`` until plan task plan#2557)
        -- the reversal that RETURNED for a shadow's movement through ruling
        R-BAL45's interval and reached it from plan step
        ``balance:X-bi-6-3`` (ruling **R-BAL101**) until plan task plan#3097
        deleted the act's reversal.  Arithmetic: settle +100
        on Savings / -100 on Checking (via transit); the correction withdraws
        both movements; Savings back on its 100.00 opening, Checking on its
        1000.00, transit 0.00.

        The COMMIT grades half of "the pair holds no covering movement": the
        status-band rule refuses a settled transfer whose sides hold one dated
        record, so a correction that withdraws either side's movement alone
        fails there.  A correction that withdraws NEITHER, which the band rule
        admits, is graded by the records: the transfer holds no side record.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            transfer = _create_projected_transfer(
                seed_user, checking, savings, Decimal("100.00"),
            )
            _db.session.commit()
            _settle(transfer, user_id)
            _db.session.commit()

            transfer_service.update_transfer(
                transfer.id, user_id, figure=typed(Decimal("0.00")),
            )
            _db.session.commit()

            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("100.00")
            assert account_net(
                _db.session, checking.id, scenario_id,
            ) == Decimal("1000.00")
            assert ledger_net(_db.session, transit_key(), scenario_id) == 0
            assert transfer_family_movement_ids(transfer.id) == []
            _assert_reconciles(scenario_id, checking, savings)


# ---------------------------------------------------------------------------
# Idempotency: a double mark-done never double-posts
# ---------------------------------------------------------------------------


class TestDoubleMarkDoneIdempotent:
    """Marking a transfer done twice books it once."""

    def test_double_mark_done_does_not_double_post(
        self, app, db, seed_user, savings,
    ):
        """A second mark-done (done -> done) books nothing more.

        Arithmetic: the first mark-done posts +100 on Savings; the identity
        re-submit (done -> done is a legal idempotent transition) moves
        nothing, so Savings nets 100.00 + 100.00 = 200.00 once and the ledger
        reconciles.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            transfer = _create_projected_transfer(
                seed_user, seed_user["account"], savings, Decimal("100.00"),
            )
            _db.session.commit()

            _settle(transfer, user_id)
            _db.session.commit()
            _settle(transfer, user_id)
            _db.session.commit()

            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("200.00")
            _assert_reconciles(scenario_id, seed_user["account"], savings)


# ---------------------------------------------------------------------------
# The trap: settle + actual_amount in one call posts the ACTUAL
# ---------------------------------------------------------------------------


class TestSettleWithActualSameCall:
    """Settling and setting actual_amount in one call posts the actual amount."""

    def test_settle_with_actual_amount_in_one_call_posts_actual(
        self, app, db, seed_user, savings,
    ):
        """One call settling AND setting actual_amount posts the ACTUAL effect.

        A settle can state what moved (``figure``) in the same
        ``update_transfer`` call, and the settle (``_settle.settle``) records
        it on the transfer through ``apply_status_to_all_three``.  The reconcile runs at the END
        of ``update_transfer`` (NOT inside ``apply_status_to_all_three``), so
        it reads the transfer's FINAL settled figure.

        Arithmetic: nominal $100, settled figure $88.00 -> each side's
        covering movement records $88.00, so the postings are -88.00 on
        Checking and +88.00 on Savings (each against transit), NOT -100 /
        +100 (Savings total 100.00 opening + 88.00 = 188.00).  A reconcile
        placed before the figure was applied would wrongly post the $100
        estimate -- the regression this guards.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            transfer = _create_projected_transfer(
                seed_user, checking, savings, Decimal("100.00"),
            )
            _db.session.commit()

            # Settle and record the actual in ONE call (the trap).
            _settle(transfer, user_id, figure=typed(Decimal("88.00")))
            _db.session.commit()

            nets = _family_nets(transfer, scenario_id)
            assert nets[linked_key(checking)] == Decimal("-88.00")
            assert nets[linked_key(savings)] == Decimal("88.00")
            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("188.00")
            _assert_reconciles(scenario_id, checking, savings)
