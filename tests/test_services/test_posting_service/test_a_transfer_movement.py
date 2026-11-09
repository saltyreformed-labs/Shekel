"""A transfer's movements post through the one movement writer, booked under the transfer's leg."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db as _db
from app.models.transaction_entry import TransactionEntry
from app.services import posting_service
from app.utils.dates import display_today
from tests._ledger_reads import account_net
from tests._test_helpers import (
    create_settled_transfer,
    refused_by_database_rule,
)

from ._helpers import _settled_and_posted, _scenario_id


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
            transfer = _settled_and_posted(
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
                assert account_net(
                    _db.session, account.id, scenario_id,
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
