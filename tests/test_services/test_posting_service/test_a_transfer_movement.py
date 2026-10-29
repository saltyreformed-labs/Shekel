"""A transfer's movements post through the one movement writer, booked under the transfer's leg."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry
from app.models.transaction_entry import TransactionEntry
from app.services import posting_service
from app.services._posting_write import _emit_balanced_entry, _PostingLeg
from app.utils.dates import display_today
from tests._test_helpers import (
    create_settled_transfer,
    refused_by_database_rule,
)

from ._helpers import (
    _ledger_id,
    _entries_for_transfer,
    _transit_ledger_id,
    _covering_movement_of_side,
    _scenario_id,
)


class TestAMovementReconcileReadsOnlyItsOwnSource:
    """A transfer movement's reconcile reads only its own source kind.

    The class's shadow case went at plan step ``balance:X-bi-6-4d-2``
    (ruling **R-BAL388**): a transfer is one row there (ruling
    **R-BAL258**), so the shadow the row door was handed, the income twin,
    cannot be stored.  That refusal is graded by
    ``test_the_declaration.py``'s
    ``test_the_transfer_owns_its_figure_and_neither_twin_can_be_stored`` and
    four loan cases (``test_the_database_refuses_a_twin`` plants the expense
    twin), and the teardown the case read is graded by
    ``test_a_settled_transfer.py``'s ``TestSyncReversal``.
    """

    @pytest.mark.usefixtures("db")
    def test_a_movement_reconcile_reads_only_its_own_source_kind(
        self, app, seed_user, savings,
    ):
        """An entry linking the same movement under ANOTHER source is untouched.

        The hazard rulings **R-BAL100** and **R-BAL101** arm together: a loan
        payment's SPLIT correction links the loan-side movement's
        ``transaction_entry_id`` under the ``loan_payment`` source, beside the
        movement's own cash leg under ``transfer_movement``.  A reconcile that
        read the posted side by LINK alone would sum the split's legs into the
        cash leg's, compute a delta against the cash target, and REVERSE the
        split.  Every typed reconcile filters on its source kind as well as its
        link (``_posting_write.emit_typed_source_deltas``), so it must not.

        Planted by hand -- a balanced ``loan_payment`` entry of $30.00 linked
        to the to-side's record (off the transfer by its side link since plan
        step balance:X-bi-6-4d-2, the income shadow's covering movement until
        then; ruling R-BAL167 class 4), between the transit row and the
        Savings row -- then the pair re-synced: the sync writes nothing
        (the cash leg is at target and the split is not its concern) and the
        family holds exactly the three entries planted.  Mutation, observed
        to FIRE on 2026-09-21: with the kind term dropped from the filter the
        sync emitted a fourth entry reversing the $30.00 (``== []`` failed).
        The planted legs themselves are append-only and so never asserted --
        a claim the ledger cannot falsify grades nothing.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            movement = _covering_movement_of_side(transfer.id, savings.id)
            savings_ledger = _ledger_id(savings)
            transit_ledger = _transit_ledger_id(seed_user)
            planted = JournalEntry(
                user_id=seed_user["user"].id,
                scenario_id=_scenario_id(seed_user),
                pay_period_id=seed_user["bootstrap_period"].id,
                entry_date=movement.settled_on,
                source_kind_id=ref_cache.posting_source_id(
                    PostingSourceEnum.LOAN_PAYMENT,
                ),
                transaction_entry_id=movement.id,
                description="planted split beside the cash leg",
            )
            _emit_balanced_entry(planted, [
                _PostingLeg(
                    savings_ledger, Decimal("-30.00"),
                    ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
                ),
                _PostingLeg(
                    transit_ledger, Decimal("30.00"),
                    ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
                ),
            ])
            _db.session.commit()

            assert posting_service.sync_transfer_postings(transfer) == []
            _db.session.commit()

            # The movement's own family: the cash leg, the planted entry, and
            # nothing the sync added.
            assert len(_entries_for_transfer(transfer.id)) == 3


class TestTheWriterBooksATransferMovementUnderItsLeg:
    """Leaf ``balance:X-bi-6-4a``: a transfer movement's parent is its LEG, never its shadow.

    Every door hands the writer the parent
    ``transfer_legs.movement_parent`` resolves, so a transfer movement's
    period, contributing gate and owner are its TRANSFER's -- the ones the
    cash fold has read since the leaf's first half (ruling **R-BAL106**).
    Each case wrote a drift around the service and asked the pair's door.
    Since plan step ``balance:X-bi-6-4d-2`` the transfer drifts are refused
    at COMMIT and asked inside the save (ruling **R-BAL167** class 2), and a
    transfer is one row (ruling **R-BAL258**), so the cases that staged a
    shadow went with the shadows.  The period case, a shadow moved to
    another period (ruling **R-BAL380**): the period read it kept is graded
    by ``test_the_oracle_and_attribution.py``'s revert-and-move case.  The
    row teardown on a shadow and the movement under a hidden one (ruling
    **R-BAL388**): the refusal each staged is graded per shape, the income
    twin by ``test_the_declaration.py``'s
    ``test_the_transfer_owns_its_figure_and_neither_twin_can_be_stored`` and
    a hidden twin by ``test_c21_state_machine_integration.py``'s
    ``test_a_hidden_twin_is_refused`` (ruling **R-BAL398**); the money each
    read is the settle's, graded by ``test_a_settled_transfer.py``'s
    ``TestSyncSettlePostsBalancedEntry``, and the re-sync at target by its
    ``TestSyncIdempotency``.
    """

    @pytest.mark.usefixtures("db")
    def test_a_transfer_deleted_around_the_service_holds_nothing(
        self, app, seed_user, savings,
    ):
        """A paid transfer hidden around the service is REFUSED at commit.

        ``$100.00`` Checking -> Savings settled; the TRANSFER row is flagged
        deleted by SQL while both its sides hold their records (Transfer
        Invariant 4 drift; until plan step ``balance:X-bi-6-4d-2`` its two
        shadows stayed live beside it).
        Until plan step ``balance:X-bi-6-4d-2`` the state committed
        and this asked the writer about it: the pair's door reversed both
        legs (Checking back on its ``$1,000.00`` opening, Savings on its
        ``$100.00``) and the cash fold named no fact, both gating on the
        transfer.  Since that step the payments hang off the transfer and the
        deleted-row rule's transfer arm refuses a transfer hidden holding one,
        so the drift cannot be stored (ruling **R-BAL167** class 2; the
        readers' Transfer-level live filters went with it, finding
        **BAL-534**).  Nothing is hidden and both payments stay.
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_on=display_today(),
            )
            _db.session.commit()
            _db.session.execute(
                _db.text(
                    "UPDATE budget.transfers SET is_deleted = TRUE WHERE id = :id"
                ),
                {"id": transfer.id},
            )

            with refused_by_database_rule(
                "was deleted while it still holds a recorded payment",
            ) as caught:
                _db.session.commit()
            _db.session.rollback()

            assert f"transfer {transfer.id} " in str(caught.value)
            _db.session.expire_all()
            assert transfer.is_deleted is False
            assert _db.session.query(TransactionEntry).filter(
                (TransactionEntry.expense_transfer_id == transfer.id)
                | (TransactionEntry.income_transfer_id == transfer.id),
            ).count() == 2

    @pytest.mark.usefixtures("db")
    def test_the_oracle_counts_a_dated_leg_whatever_the_transfers_status(
        self, app, seed_user, savings,
    ):
        """The oracle states the writer's rule: a dated leg of a contributing transfer.

        ``$100.00`` Checking -> Savings settled; the TRANSFER's status alone
        is rewritten to Projected by SQL (a status drift: both movements stay
        dated).  The writer keeps both legs posted -- a movement posts iff it
        is dated under a contributing parent (ruling **R-BAL101**) -- and
        ``settled_transfer_effect`` agrees on both accounts: Checking
        ``900.00`` = ``1,000.00`` + ``-100.00``, Savings ``200.00`` =
        ``100.00`` + ``100.00``.  Before the leaf the oracle filtered on the
        settled STATUS and answered ``0.00`` on both, grading a correct
        ledger as ``$100.00`` off.

        **The drift is STAGED inside the save since plan step
        ``balance:X-bi-6-4d-2``** (ruling **R-BAL167** class 2, the shape of
        :meth:`test_a_transfer_deleted_around_the_service_holds_nothing`): a
        record is dated only while its transfer is settled (the band rule),
        so the status rewrite is refused at COMMIT.  The writer and the
        oracle are asked inside the save, the commit is then refused, and
        the transfer stays Paid with both records dated.
        """
        with app.app_context():
            checking = seed_user["account"]
            scenario_id = _scenario_id(seed_user)
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_on=display_today(),
            )
            _db.session.commit()
            _db.session.execute(
                _db.text(
                    "UPDATE budget.transfers SET status_id = :s WHERE id = :id"
                ),
                {"s": ref_cache.status_id(StatusEnum.PROJECTED), "id": transfer.id},
            )
            _db.session.expire_all()

            assert posting_service.sync_transfer_postings(transfer) == []

            for account, opening, effect in (
                (checking, Decimal("1000.00"), Decimal("-100.00")),
                (savings, Decimal("100.00"), Decimal("100.00")),
            ):
                assert posting_service.settled_transfer_effect(
                    account.id, scenario_id,
                ) == effect
                assert posting_service.account_posting_total(
                    account.id, scenario_id,
                ) == opening + effect

            with refused_by_database_rule(
                "is not settled but 2 of its payment records are dated",
            ) as caught:
                _db.session.commit()
            _db.session.rollback()

            assert f"transfer {transfer.id} " in str(caught.value)
            _db.session.expire_all()
            assert transfer.status.is_settled
            assert _db.session.query(TransactionEntry).filter(
                (TransactionEntry.expense_transfer_id == transfer.id)
                | (TransactionEntry.income_transfer_id == transfer.id),
                TransactionEntry.settled_on.isnot(None),
            ).count() == 2
