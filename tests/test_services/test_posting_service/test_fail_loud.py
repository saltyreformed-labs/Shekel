"""A broken invariant raises PostingError rather than posting silently, on the transfer arm and the
transaction arm.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.enums import PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry
from app.services import posting_service
from app.services._posting_write import _emit_balanced_entry, _PostingLeg, emit_typed_source_deltas
from app.services.posting_service import PostingError
from tests._test_helpers import (
    add_txn,
    create_settled_cash_transaction,
    create_settled_transfer,
    refused_by_database_rule,
)

from ._helpers import _ledger_id, _transit_ledger_id, _legs_by_ledger, _scenario_id, _ledger_total


# ---------------------------------------------------------------------------
# Fail loud
# ---------------------------------------------------------------------------


class TestFailLoud:
    """Broken invariants raise PostingError rather than posting silently."""

    @pytest.mark.parametrize(
        "linkage",
        [
            {},
            {"transaction_id": 1, "transaction_entry_id": 2},
            {"transfer_id": 1},
        ],
        ids=["no link", "two links", "a transfer link"],
    )
    def test_a_typed_source_names_exactly_one_typed_link(
        self, app, db, seed_user, linkage,
    ):
        """``emit_typed_source_deltas`` refuses any link set but one typed FK.

        Plan step X-bi-3b: a header carrying two links lands in NONE of the
        ledger report's buckets, and a ``transfer_id`` header carries the
        ``transfer`` kind and never a transaction's -- so the refusal fires
        before a target is read, whatever *linkage* the caller spelled.
        """
        with app.app_context():
            txn = create_settled_cash_transaction(
                seed_user, _db.session, seed_user["bootstrap_period"],
                Decimal("10.00"),
            )
            with pytest.raises(ValueError, match="exactly one of"):
                emit_typed_source_deltas(
                    txn, targets={}, source=PostingSourceEnum.TRANSACTION,
                    description="x", log_label="x", **linkage,
                )

    def test_account_posting_total_none_scenario_fails_loud(
        self, app, db, seed_user,
    ):
        """A None scenario in ``account_posting_total`` raises PostingError."""
        with app.app_context():
            with pytest.raises(PostingError, match="scenario_id"):
                posting_service.account_posting_total(
                    seed_user["account"].id, None,
                )

    def test_settled_transfer_effect_none_scenario_fails_loud(
        self, app, db, seed_user,
    ):
        """A None scenario in ``settled_transfer_effect`` raises PostingError."""
        with app.app_context():
            with pytest.raises(PostingError, match="scenario_id"):
                posting_service.settled_transfer_effect(
                    seed_user["account"].id, None,
                )

    def test_missing_ledger_account_fails_loud(
        self, app, db, seed_user, savings,
    ):
        """A transfer whose account has no ledger pairing raises PostingError.

        Removing the Savings account's linked ledger row (an impossible state
        in production -- every account is paired) makes the sync fail loudly
        rather than post a one-legged or silently-wrong entry.  The row's
        postings go with it (``account_postings.ledger_account_id`` CASCADE),
        so the to-side movement reads as unposted and the door tries to post
        it -- into a pairing that no longer exists.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            # Drop the to-account's ledger pairing via raw SQL.
            _db.session.execute(_db.text(
                "DELETE FROM budget.ledger_accounts WHERE account_id = :a"
            ), {"a": savings.id})
            _db.session.commit()

            # The pair's door reconciles from what is posted, so with the
            # Savings pairing gone the to-side movement's target cannot be
            # built; the refusal fires before anything is written.
            with pytest.raises(PostingError, match="ledger account"):
                posting_service.sync_transfer_postings(transfer)

    def test_a_half_pair_posts_its_half_into_transit(
        self, app, db, seed_user, savings,
    ):
        """A transfer holding only its from-side record posts that side, in transit.

        **A declared behaviour change of plan step ``balance:X-bi-6-3``
        (rulings R-BAL45, R-BAL101), re-expressed from
        ``test_settle_missing_income_shadow_fails_loud``.**  The one-entry
        writer read the INCOME shadow's record for the pair and REFUSED when
        it was missing; shape C has no pair to read -- each side's movement
        posts on its own, against transit -- so a pair with one side is the
        in-transit state: money left Checking and has not arrived.  The
        ledger writer neither refuses nor fabricates the missing side; the
        band rule polices the pair (below).

        Arithmetic: from a ledger the teardown brought to zero (both sides
        posted and reversed: transit 0.00), the to-side's record is removed
        and the sync runs: it emits ONE entry, {Checking -100, transit +100}, and
        nothing for the missing side; transit holds +100.00 -- the half in
        flight.  (The to-side's earlier entries survive as a net-zero pair
        whose links the raw delete SET-NULLed; the readers drop unlinked
        residue.)  The leaf's adversarial review found the first cut reading
        back the entry the SETTLE had written; this grades the sync's own.

        **The half pair is STAGED inside the save since plan step
        ``balance:X-bi-6-4d-2``** (ruling **R-BAL167** class 2): the to-side's
        record hangs off the TRANSFER since that step, and a settled transfer
        holding ONE side's record is refused at COMMIT (the band rule).  The
        record comes off by its side link, the sync runs and is read inside
        the save, and the commit is then refused.  The income shadow was
        deleted beside it until the same step made a transfer one row (ruling
        **R-BAL258**); no shadow is stored to delete.
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(checking)
            savings_ledger = _ledger_id(savings)
            transit_ledger = _transit_ledger_id(seed_user)
            posting_service.reverse_transfer_postings_before_delete(transfer)
            _db.session.commit()
            assert _ledger_total(transit_ledger) == Decimal("0.00")
            # Remove the to-side's record via raw SQL, around the service: a
            # record is no longer deleted with anything (R-CC54; rule-5
            # re-expression, developer-confirmed 2026-09-23), and it is the
            # TRANSFER's, by its side link, since plan step
            # balance:X-bi-6-4d-2 (the shadow's covering movement until
            # then).  STAGED: not committed.
            _db.session.execute(_db.text(
                "DELETE FROM budget.transaction_entries "
                "WHERE income_transfer_id = :t"
            ), {"t": transfer.id})
            _db.session.expire_all()

            [posted] = posting_service.sync_transfer_postings(transfer)

            assert _legs_by_ledger(posted.id) == {
                checking_ledger: Decimal("-100.00"),
                transit_ledger: Decimal("100.00"),
            }
            assert _ledger_total(transit_ledger) == Decimal("100.00")
            # The missing side posted nothing: Savings sits on its opening.
            assert posting_service.account_posting_total(
                savings.id, _scenario_id(seed_user),
            ) == Decimal("100.00")
            assert posting_service.sync_transfer_postings(transfer) == []

            # The band rule refuses the half pair at the commit (ruling
            # R-BAL167 class 2).
            with refused_by_database_rule(
                "is settled but its sides hold 1 dated and 0 un-dated",
            ) as caught:
                _db.session.commit()
            _db.session.rollback()
            assert f"transfer {transfer.id} " in str(caught.value)

    def test_emit_balanced_entry_rejects_single_leg(self, app, db, seed_user):
        """The builder refuses an entry with fewer than two legs.

        The service-side backstop for the ``COUNT(*) >= 2`` half of the
        deferred balanced-journal trigger: a one-legged entry fails loudly at
        the call site before any write.
        """
        with app.app_context():
            with pytest.raises(PostingError, match="at least 2 legs"):
                _emit_balanced_entry(
                    JournalEntry(),
                    [_PostingLeg(1, Decimal("100.00"), 1)],
                )

    def test_emit_balanced_entry_rejects_unbalanced_legs(
        self, app, db, seed_user,
    ):
        """The builder refuses legs that do not sum to zero.

        The service-side backstop for the ``SUM(amount) = 0`` half of the
        deferred balanced-journal trigger: an unbalanced pair (+100 / +50,
        summing to +150) fails loudly at the call site before any write.
        """
        with app.app_context():
            with pytest.raises(PostingError, match="sum to 0"):
                _emit_balanced_entry(
                    JournalEntry(),
                    [
                        _PostingLeg(1, Decimal("100.00"), 1),
                        _PostingLeg(2, Decimal("50.00"), 1),
                    ],
                )


class TestTransactionFailLoud:
    """Broken invariants raise PostingError rather than posting silently."""

    def test_missing_cash_ledger_fails_loud(self, app, db, seed_user):
        """A transaction whose account has no ledger pairing raises PostingError.

        Removing the Checking account's linked ledger row (an impossible state
        in production -- every account is paired) makes the settle sync fail
        loudly rather than post a one-legged or silently-wrong entry.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            _db.session.execute(_db.text(
                "DELETE FROM budget.ledger_accounts WHERE account_id = :a"
            ), {"a": seed_user["account"].id})
            _db.session.commit()

            with pytest.raises(PostingError, match="ledger account"):
                posting_service.sync_transaction_postings(txn)

    def test_posted_purchase_effect_none_scenario_fails_loud(
        self, app, db, seed_user,
    ):
        """A None scenario in ``posted_purchase_effect`` raises PostingError."""
        with app.app_context():
            with pytest.raises(PostingError, match="scenario_id"):
                posting_service.posted_purchase_effect(
                    seed_user["account"].id, None,
                )
