"""The deploy resync skips a transfer that cannot post whole, and re-checks the anchor
corrections once after every source is re-booked.

It re-booked the legacy one-entry transfer shape too (ruling **R-BAL98**), until plan step
``balance:X-bi-6-5``'s second leaf (card plan#1845) deleted that shape, its refusal (ruling
**R-BAL104**) and the four cases that graded them (ruling plan#1890).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import PostingSourceEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.services import anchor_service, posting_service
from tests._stored_ledger import stored_ledger_net
from tests._test_helpers import create_settled_cash_transaction

from ._helpers import _ledger_id


# ---------------------------------------------------------------------------
# The deploy resync: a whole skip, and one anchor re-check (R-BAL103)
# ---------------------------------------------------------------------------


class TestTheDeployResync:
    """``resync_all_cash_postings`` re-books each source through its door.

    Plan step ``balance:X-bi-6-3`` built both properties graded here: a
    transfer the pair's door cannot post is skipped WHOLE under a savepoint,
    and every source is re-booked before the anchor corrections are
    re-checked once (ruling **R-BAL103**).  The real resync runs over a
    ledger forged behind its back by raw SQL.
    """

    def test_the_one_re_check_corrects_what_a_row_re_date_moved(
        self, app, db, seed_user,
    ):
        """The re-check after the loop WRITES when a re-dated row crosses an assertion.

        The row arm's twin of the case above (its hold is separate, so it
        has its own control).  Arithmetic: Checking opens at $1,000.00; a
        $30.00 row settles on S; Checking is trued up to $1,000.00 as of S-1
        (the row after it, correction $0.00).  The row's settle day is then
        moved to S-1 behind the ledger's back (the stale-day forge of the
        R-DH hook test), so the assertion now covers it.  The resync re-dates
        the row -- (1, 0) -- and the one re-check books the correction the
        move owes: exactly ONE new anchor-correction entry, Checking +30.00,
        and the ledger reads the asserted $1,000.00.  Mutation: the row
        arm's hold removed -> the ledger stays at $970.00.
        """
        with app.app_context():
            checking = seed_user["account"]
            row = create_settled_cash_transaction(
                seed_user, _db.session, seed_user["bootstrap_period"],
                Decimal("30.00"), account=checking, name="re-dated row",
            )
            _db.session.commit()
            [movement] = row.entries
            earlier = movement.settled_on - timedelta(days=1)
            anchor_service.apply_anchor_true_up(
                account=checking, new_balance=Decimal("1000.00"),
                observed_on=earlier,
            )
            checking_ledger = _ledger_id(checking)
            assert stored_ledger_net(
                _db.session, checking_ledger, seed_user["scenario"].id,
            ) == Decimal("970.00")
            _db.session.execute(
                _db.text(
                    "UPDATE budget.transactions SET settled_on = :day "
                    "WHERE id = :id"
                ),
                {"day": earlier, "id": row.id},
            )
            _db.session.execute(
                _db.text(
                    "UPDATE budget.transaction_entries "
                    "SET settled_on = :day, purchased_on = :day "
                    "WHERE transaction_id = :id"
                ),
                {"day": earlier, "id": row.id},
            )
            _db.session.commit()
            newest = _db.session.query(_db.func.max(JournalEntry.id)).scalar()

            assert posting_service.resync_all_cash_postings() == (1, 0)
            _db.session.commit()

            assert _new_anchor_correction_legs(newest, checking_ledger) == [
                Decimal("30.00"),
            ]
            assert stored_ledger_net(
                _db.session, checking_ledger, seed_user["scenario"].id,
            ) == Decimal("1000.00")


def _new_anchor_correction_legs(after_entry_id, ledger_id):
    """Return the *ledger_id* legs of anchor corrections written after an entry."""
    return [
        amount for (amount,) in _db.session.query(Posting.amount)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            JournalEntry.id > after_entry_id,
            Posting.ledger_account_id == ledger_id,
            JournalEntry.source_kind_id.in_([
                ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING),
                ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_TRUEUP),
            ]),
        )
        .order_by(JournalEntry.id)
    ]
