"""Lifecycle tests for the transfer -> posting-ledger wiring (Step 2, Commit 5).

Commit 5 wires :func:`~app.services.posting_service.sync_transfer_postings`
into the transfer service's mutation chokepoints (``update_transfer`` /
``delete_transfer`` / ``restore_transfer``), so settling, reverting,
cancelling, deleting, and restoring a transfer keep the append-only
double-entry ledger in step WITHOUT any caller touching ``posting_service``
directly.  These tests drive transfers END TO END through ``transfer_service``
only (the way the mark-done / cancel / delete routes do) and assert the
resulting ledger state.  **A settled transfer is TWO entries since plan step
``balance:X-bi-6-3``** (rulings **R-BAL45** and **R-BAL101**): one per side's
covering movement, each against the owner's Transfers-in-transit account, so
every act below writes a PAIR of entries and every figure a real account
carries is what the one-entry shape gave it; the cases were re-expressed for
the pair when the shape moved (each docstring says how).  The truth table:

  * ``projected -> done``       posts two balanced entries (+effect);
  * ``done -> settled``         is an idempotent no-op (already at target);
  * ``done -> projected``       reverses them to net zero (append-only);
  * ``projected -> cancelled``  posts nothing (never settled);
  * soft-delete of a settled transfer reverses, and restore re-posts;
  * hard-delete of a settled transfer reverses, then the immutable pairs
    survive with their movement links SET NULL;
  * a double mark-done never double-posts;
  * settling and setting the figure in ONE call posts the RECORDED figure
    (the reconcile runs after every kwarg is applied, NOT inside the
    status-change helper -- the placement that makes the grid shadow-edit
    path correct).

After each mutation the per-account reconciliation invariant is asserted in
its Build-Order Step 5 ABSOLUTE form: ``account_posting_total == opening
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
from app.enums import PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import posting_service, transfer_service
from tests._test_helpers import (
    create_account_of_type,
    linked_ledger_account,
    transfer_family_journal_filter,
    transit_ledger_account,
    typed,
)
from app.services import cash_ledger
from app.models.amount_ownership import AmountOwnership


# ---------------------------------------------------------------------------
# Helpers and fixtures
# ---------------------------------------------------------------------------


def _ledger_id(account):
    """Return the LINKED ledger account id for *account* (never its twin)."""
    return linked_ledger_account(_db.session, account.id).id


def _entries_for_transfer(transfer_id):
    """Return every journal entry of the transfer's FAMILY, oldest first.

    The per-movement entries linked to the shadows' covering movements (plan
    step ``balance:X-bi-6-3``), plus anything still linked by ``transfer_id``
    (``transfer_family_journal_filter``).
    """
    return (
        _db.session.query(JournalEntry)
        .filter(transfer_family_journal_filter(transfer_id))
        .order_by(JournalEntry.id)
        .all()
    )


def _movement_sourced_entries(user_id):
    """Return the owner's TRANSFER-MOVEMENT-sourced journal entries, oldest first.

    Counted by SOURCE rather than through :func:`_entries_for_transfer`
    because a soft delete takes both payment records off (ruling
    **credit_card:R-CC75**, plan step ``balance:X-bi-6-4d-2``): the entries
    a record posted survive as immutable history with their link SET NULL,
    so the family filter, which follows the links, finds none of them.  Each
    case here owns one transfer, so its movement-sourced entries are that
    transfer's.
    """
    return (
        _db.session.query(JournalEntry)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.source_kind_id == ref_cache.posting_source_id(
                PostingSourceEnum.TRANSFER_MOVEMENT,
            ),
        )
        .order_by(JournalEntry.id)
        .all()
    )


def _transit_id(seed_user):
    """Return the owner's Transfers-in-transit ledger account id (a lookup)."""
    return transit_ledger_account(_db.session, seed_user["user"].id).id


def _real_legs(entries, *ledger_ids):
    """Return ``{ledger_account_id: net}`` over *entries*' legs on those ledgers.

    A transfer family's per-REAL-account net is what the one-entry shape's
    single entry used to state directly; over the pair it is the sum of each
    side's leg, and it is what every figure below is asserted on.
    """
    nets = {ledger_id: Decimal("0.00") for ledger_id in ledger_ids}
    for entry in entries:
        for ledger_id, amount in _legs_by_ledger(entry.id).items():
            if ledger_id in nets:
                nets[ledger_id] += amount
    return nets


def _legs_by_ledger(entry_id):
    """Return ``{ledger_account_id: amount}`` for one entry's legs."""
    return {
        leg.ledger_account_id: leg.amount
        for leg in _db.session.query(Posting)
        .filter_by(journal_entry_id=entry_id)
        .all()
    }


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
        posted = posting_service.account_posting_total(account.id, scenario_id)
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
    """Settling a transfer through the service auto-posts one balanced entry."""

    def test_mark_done_posts_one_balanced_entry(
        self, app, db, seed_user, savings,
    ):
        """projected -> done posts -100 / +100 via transit; the ledger reconciles.

        Arithmetic: settling a $100 Checking -> Savings transfer posts two
        entries (plan step ``balance:X-bi-6-3``): -100.00 on Checking's
        ledger (money out, a credit) against +100.00 transit, and +100.00 on
        Savings' (money in, a debit) against -100.00 transit; each sums to
        zero and transit nets 0.00.  The reconcile invariant holds:
        account_posting_total(Savings) == 100.00 (opening) + 100.00 (effect)
        = 200.00.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            transfer = _create_projected_transfer(
                seed_user, checking, savings, Decimal("100.00"),
            )
            _db.session.commit()
            # No entry before settling.
            assert _entries_for_transfer(transfer.id) == []

            _settle(transfer, user_id)
            _db.session.commit()

            entries = _entries_for_transfer(transfer.id)
            assert len(entries) == 2
            for entry in entries:
                assert sum(_legs_by_ledger(entry.id).values()) == Decimal("0.00")
            assert _real_legs(
                entries, _ledger_id(checking), _ledger_id(savings),
                _transit_id(seed_user),
            ) == {
                _ledger_id(checking): Decimal("-100.00"),
                _ledger_id(savings): Decimal("100.00"),
                _transit_id(seed_user): Decimal("0.00"),
            }
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("200.00")
            _assert_reconciles(scenario_id, checking, savings)

    def test_done_to_settled_archive_is_noop(
        self, app, db, seed_user, savings,
    ):
        """done -> settled posts no second entry (already at target).

        Arithmetic: the settle posted +100 to Savings (total 200.00 on the
        $100.00 opening); archiving Done -> Settled keeps both movements
        dated under contributing parents, so target == current on each,
        delta 0, no entry.  The ledger stays at the settle's two entries and
        still reconciles.
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

            assert len(_entries_for_transfer(transfer.id)) == 2
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("200.00")
            _assert_reconciles(scenario_id, seed_user["account"], savings)


# ---------------------------------------------------------------------------
# Revert: done -> projected reverses to zero (append-only)
# ---------------------------------------------------------------------------


class TestRevertReverses:
    """Reverting a settled transfer appends a balanced reversal."""

    def test_revert_to_projected_reverses_to_zero(
        self, app, db, seed_user, savings,
    ):
        """done -> projected appends the reversals; Savings nets to zero.

        Arithmetic: the settle posted +100 on Savings and -100 on Checking
        (each against transit); reverting UN-DATES both covering movements
        (ruling **R-BAL61**), so each side's target is 0 and the door posts
        the deltas: -100 on Savings against +100 transit, +100 on Checking
        against -100 transit.  Four entries survive (append-only -- the
        originals are never edited).  The reverted income shadow is no longer
        is_settled, so it drops from settled_transfer_effect, and the Savings
        total lands back on its $100.00 opening.
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

            entries = _entries_for_transfer(transfer.id)
            assert len(entries) == 4
            reversals = entries[2:]
            assert _real_legs(
                reversals, _ledger_id(checking), _ledger_id(savings),
                _transit_id(seed_user),
            ) == {
                _ledger_id(savings): Decimal("-100.00"),
                _ledger_id(checking): Decimal("100.00"),
                _transit_id(seed_user): Decimal("0.00"),
            }
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("100.00")
            _assert_reconciles(scenario_id, checking, savings)


# ---------------------------------------------------------------------------
# Cancel: projected -> cancelled posts nothing
# ---------------------------------------------------------------------------


class TestCancelPostsNothing:
    """Cancelling a never-settled transfer writes no ledger entry."""

    def test_cancel_projected_posts_nothing(
        self, app, db, seed_user, savings,
    ):
        """projected -> cancelled posts no entry (never settled).

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

            assert _entries_for_transfer(transfer.id) == []
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("100.00")
            _assert_reconciles(scenario_id, checking, savings)


# ---------------------------------------------------------------------------
# Delete + restore
# ---------------------------------------------------------------------------


class TestDeleteAndRestore:
    """Deleting a settled transfer reverses it; restoring brings back a plan."""

    def test_soft_delete_settled_reverses_then_restore_reposts(
        self, app, db, seed_user, savings,
    ):
        """Soft-delete reverses a settled transfer; restore brings back a plan.

        Arithmetic: settle +100 (2 entries, one per side); soft-delete
        reverses -100 through the teardown door (4 entries, Savings back on
        its 100.00 opening); restore re-posts NOTHING (still 4 entries,
        Savings 100.00).  Append-only throughout -- every correction is a new
        entry, none edited.

        The restore re-posted +100 (6 entries, Savings 200.00) until plan
        step ``balance:X-bi-6-4d-2``.  That step's soft delete takes both
        payment records off (ruling **credit_card:R-CC75**), so the four
        entries survive with their links nulled and are counted by source
        (:func:`_movement_sourced_entries`), and it sets the transfer back to
        Projected (ruling **R-BAL246**, "Back as a plan, now", developer
        2026-10-09): what the restore brings back is a plan holding no
        record, which posts nothing.  The name is kept as history.
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

            # Soft-delete reverses the posted effect.
            transfer_service.delete_transfer(transfer.id, user_id, soft=True)
            _db.session.commit()
            assert len(_movement_sourced_entries(user_id)) == 4
            assert _entries_for_transfer(transfer.id) == []
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
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
            assert len(_movement_sourced_entries(user_id)) == 4
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("100.00")
            _assert_reconciles(scenario_id, checking, savings)

    def test_hard_delete_of_a_soft_deleted_pair_finds_the_ledger_at_zero(
        self, app, db, seed_user, savings,
    ):
        """Soft-delete then hard-delete: the second teardown writes nothing.

        The delete door is idempotent through ``allow_deleted=True``, and the
        teardown it runs first must find a soft-deleted pair's postings
        already at zero rather than reverse them AGAIN -- which is why the
        teardown reads every shadow of the transfer, deleted or not (plan
        step ``balance:X-bi-6-3``, ruling **R-BAL101**), and reconciles each
        movement to an empty target.  Arithmetic: settle +100 (2 entries),
        soft-delete -100 (4 entries), hard-delete: 0 more, the transfer row
        gone, Savings on its 100.00 opening, transit at 0.00.

        The soft delete's four entries are counted by source
        (:func:`_movement_sourced_entries`) since plan step
        ``balance:X-bi-6-4d-2``, whose soft delete takes both payment records
        off (ruling **credit_card:R-CC75**) and so nulls the links the family
        filter follows; the transfer then waits as a plan (ruling
        **R-BAL246**).  The name is kept as history.
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
            transit_ledger = _transit_id(seed_user)

            transfer_service.delete_transfer(transfer_id, user_id, soft=True)
            _db.session.commit()
            assert len(_movement_sourced_entries(user_id)) == 4

            transfer_service.delete_transfer(transfer_id, user_id, soft=False)
            _db.session.commit()

            assert _db.session.get(Transfer, transfer_id) is None
            assert len(_movement_sourced_entries(user_id)) == 4
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("100.00")
            assert _db.session.query(
                _db.func.coalesce(_db.func.sum(Posting.amount), 0)
            ).filter(Posting.ledger_account_id == transit_ledger).scalar() == 0

    def test_hard_delete_settled_reverses_and_pairs_survive_null_links(
        self, app, db, seed_user, savings,
    ):
        """Hard-delete reverses, then the immutable pairs survive, links nulled.

        Arithmetic: settle +100 (2 entries, one per side); hard-delete first
        reverses -100 (2 more), then removes the transfer row -- the removal
        act deletes the shadows' covering movements first (a shadow's key no
        longer cascades to them since plan step ``credit_card:CC-5-4a-4``),
        the shadows CASCADE with the transfer, and
        ``journal_entries.transaction_entry_id`` is SET NULL on all four
        entries.  The immutable legs survive and the transfer row is gone.
        This is the append-only correction proven through a hard delete,
        re-expressed for the pair at plan step ``balance:X-bi-6-3`` (the
        entries linked ``transfer_id`` and nulled that).

        **The Savings LINKED ledger holds FIVE legs, and it held three until
        plan step X-f3c-2b.**  Its opening is now posted, reversed and
        re-posted, because ``create_account_of_type`` restates the account's
        books so a fixture can date a row before the account's own creation day
        -- which is production's own shape after the same act.  Those three net
        to the ``$100.00`` opening exactly as the single leg did, so the ledger
        still reads ``+$100.00`` in total.  Both counts are asserted: the whole
        ledger, which is what catches an amount-NEUTRAL pair of extra legs
        landing during the delete, and the TRANSFER-MOVEMENT-sourced pair
        alone, which is the append-only correction this case is named for.
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
            savings_ledger = _ledger_id(savings)

            transfer_service.delete_transfer(transfer_id, user_id, soft=False)
            _db.session.commit()

            # The transfer row is gone.
            assert _db.session.get(Transfer, transfer_id) is None
            # All four entries survive with their movement link nulled
            # (immutable history).  Filtered by the TRANSFER-MOVEMENT source
            # kind: the openings the fixtures posted are also
            # concrete-FK-less entries, but carry the account_opening source.
            assert _entries_for_transfer(transfer_id) == []
            surviving = (
                _db.session.query(JournalEntry)
                .filter(
                    JournalEntry.user_id == user_id,
                    JournalEntry.transaction_entry_id.is_(None),
                    JournalEntry.source_kind_id == ref_cache.posting_source_id(
                        PostingSourceEnum.TRANSFER_MOVEMENT,
                    ),
                )
                .all()
            )
            assert len(surviving) == 4
            # THE WHOLE LEDGER first, which is the reading a source filter
            # cannot give: five legs -- the opening posted, reversed and
            # re-posted by the factory's restatement, plus the settle and its
            # reversal -- netting to the $100.00 opening.  An amount-NEUTRAL
            # extra pair landing here during the delete moves this count and
            # nothing else, so dropping it for the filtered count below would
            # have been the cheaper repair rather than the stronger one.
            all_savings_legs = (
                _db.session.query(Posting)
                .filter_by(ledger_account_id=savings_ledger)
                .all()
            )
            assert len(all_savings_legs) == 5
            assert sum(
                leg.amount for leg in all_savings_legs
            ) == Decimal("100.00")

            # THEN the TRANSFER-MOVEMENT-sourced pair alone (+100 settle, -100
            # reversal), which is the append-only correction this case is named
            # for: it survives the parent's disposal and nets to zero.
            savings_legs = (
                _db.session.query(Posting)
                .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
                .filter(
                    Posting.ledger_account_id == savings_ledger,
                    JournalEntry.source_kind_id == ref_cache.posting_source_id(
                        PostingSourceEnum.TRANSFER_MOVEMENT,
                    ),
                )
                .all()
            )
            assert len(savings_legs) == 2
            assert sum(leg.amount for leg in savings_legs) == Decimal("0.00")
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
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
        on (no movement can carry a zero figure), and reverses its leg first
        through ``posting_service.reverse_purchase_postings_before_delete`` --
        the door that RETURNED for a shadow's movement through ruling
        R-BAL45's interval and reaches it since plan step
        ``balance:X-bi-6-3`` (ruling **R-BAL101**).  Arithmetic: settle +100
        on Savings / -100 on Checking (two entries via transit); the
        correction withdraws both movements and reverses both legs (four
        ``transfer_movement`` entries, their movement links SET NULL by the
        withdrawal, which is why they are read by source kind here); Savings
        back on its 100.00 opening, Checking on its 1000.00, transit 0.00,
        and the pair holds no covering movement.

        That last is graded by two reads before any count of the side links,
        and none is taken: the two counts this case took were reached by no
        app mutant (measured at plan step ``balance:X-bi-6-4d-2``'s
        vacuous-residue checkpoint, where they went).  The COMMIT is the
        first: the status-band rule refuses a settled transfer whose sides
        hold one dated record, so a correction that withdraws either side's
        movement alone fails there.  The movement-sourced entry count is the
        second: a correction that withdraws neither, which the band rule
        admits, leaves two entries, not four.
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
            assert len(_entries_for_transfer(transfer.id)) == 2

            transfer_service.update_transfer(
                transfer.id, user_id, figure=typed(Decimal("0.00")),
            )
            _db.session.commit()

            movement_sourced = (
                _db.session.query(JournalEntry)
                .filter(
                    JournalEntry.user_id == user_id,
                    JournalEntry.source_kind_id == ref_cache.posting_source_id(
                        PostingSourceEnum.TRANSFER_MOVEMENT,
                    ),
                )
                .all()
            )
            assert len(movement_sourced) == 4
            assert {e.transaction_entry_id for e in movement_sourced} == {None}
            assert _entries_for_transfer(transfer.id) == []
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("100.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            assert _db.session.query(
                _db.func.coalesce(_db.func.sum(Posting.amount), 0)
            ).filter(
                Posting.ledger_account_id == _transit_id(seed_user),
            ).scalar() == 0
            _assert_reconciles(scenario_id, checking, savings)


# ---------------------------------------------------------------------------
# Idempotency: a double mark-done never double-posts
# ---------------------------------------------------------------------------


class TestDoubleMarkDoneIdempotent:
    """Marking a transfer done twice posts exactly one entry."""

    def test_double_mark_done_does_not_double_post(
        self, app, db, seed_user, savings,
    ):
        """A second mark-done (done -> done) posts no second entry.

        Arithmetic: the first mark-done posts +100 (two entries, one per
        side); the identity re-submit (done -> done is a legal idempotent
        transition) sees current == target on each movement, delta 0, and
        writes nothing.  The service-level double-mark-done guard -- the
        settle's two entries, ledger reconciles.
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

            assert len(_entries_for_transfer(transfer.id)) == 2
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
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

        The grid shadow-edit path can send ``status_id=done`` AND
        ``actual_amount`` in a single ``update_transfer`` call, and the service
        applies ``actual_amount`` AFTER ``status_id``.  The reconcile runs at
        the END of ``update_transfer`` (NOT inside ``apply_status_to_all_three``), so
        it reads the FINAL income-shadow effective amount.

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

            entries = _entries_for_transfer(transfer.id)
            assert len(entries) == 2
            assert _real_legs(
                entries, _ledger_id(checking), _ledger_id(savings),
            ) == {
                _ledger_id(checking): Decimal("-88.00"),
                _ledger_id(savings): Decimal("88.00"),
            }
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("188.00")
            _assert_reconciles(scenario_id, checking, savings)
