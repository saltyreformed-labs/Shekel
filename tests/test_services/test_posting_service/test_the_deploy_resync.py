"""The deploy resync skips a transfer that cannot post whole, and re-checks the anchor
corrections once after every source is re-booked.

It re-booked the legacy one-entry transfer shape too (ruling **R-BAL98**), until plan step
``balance:X-bi-6-5``'s second leaf (card plan#1845) deleted that shape, its refusal (ruling
**R-BAL104**) and the four cases that graded them (ruling plan#1890).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import PostingSourceEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.services import anchor_service, posting_service
from tests._test_helpers import (
    create_account_of_type,
    create_settled_cash_transaction,
    create_settled_transfer,
)

from ._helpers import (
    _ledger_id,
    _entries_for_transfer,
    _transit_ledger_id,
    _covering_movement_of_side,
    _entries_for_transaction,
    _ledger_total,
)


# ---------------------------------------------------------------------------
# The deploy resync: a whole skip, and one anchor re-check (R-BAL103)
# ---------------------------------------------------------------------------


def _real_account_nets_by_day(*ledger_ids):
    """Return ``{(ledger_account_id, entry_date): net}`` over the given ledgers.

    The grade ruling **R-BAL98** named for a re-book that changed the
    ledger's SHAPE, kept for a batch re-book: every real account's net per
    day exactly as it was.  An independent re-grouping of the legs by the
    entry's date and the leg's ledger account, zero-net keys dropped, never
    the service's own readers.
    """
    rows = (
        _db.session.query(
            Posting.ledger_account_id, JournalEntry.entry_date,
            _db.func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(Posting.ledger_account_id.in_(ledger_ids))
        .group_by(Posting.ledger_account_id, JournalEntry.entry_date)
        .all()
    )
    return {
        (ledger_id, day): net for ledger_id, day, net in rows if net != 0
    }


class TestTheDeployResync:
    """``resync_all_cash_postings`` re-books each source through its door.

    Plan step ``balance:X-bi-6-3`` built both properties graded here: a
    transfer the pair's door cannot post is skipped WHOLE under a savepoint,
    and every source is re-booked before the anchor corrections are
    re-checked once (ruling **R-BAL103**).  The real resync runs over a
    ledger forged behind its back by raw SQL.
    """

    def test_a_refused_transfer_is_skipped_whole(
        self, app, db, seed_user, savings, caplog,
    ):
        """A transfer one side of which cannot post is skipped WHOLE, and logged.

        The 2026-08-17 ruling: a family that cannot post is skipped and
        reported, never allowed to make the deploy unbootable, and the
        per-transfer SAVEPOINT (the adversarial review of plan step
        ``balance:X-bi-6-3``'s first leaf, finding 1) decides what the deploy
        commits, because the pair's door writes one side before it reaches
        the other.  Arithmetic: a $100.00 Checking -> Savings transfer settled
        go-forward (two movement entries); the entry of the
        movement the door reaches FIRST (the lower id) deleted by raw SQL, so
        the door has a re-post to write, and the SECOND movement re-pointed
        onto an account whose ledger pairing is removed, so the door is
        refused after that write.  The resync returns (0, 0) and logs the
        skip naming the transfer and why (the door's own message, naming the
        account whose pairing is missing); the family still holds exactly
        the second movement's original entry (the first's re-post rolled
        back).
        Mutations: a refusal on ANY skip raises here; without the savepoint
        the first movement's re-post survives the refusal.

        It was named for the legacy net it did not hold, beside a case that
        skipped a transfer still in the legacy one-entry shape and so also
        graded ruling **R-BAL104**'s refusal, until ruling plan#1890 deleted
        that case with the shape and the refusal (card plan#1845).
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            first_id, second_id = sorted(
                _covering_movement_of_side(transfer.id, account.id).id
                for account in (checking, savings)
            )
            [second_entry_id] = [
                entry.id for entry in _entries_for_transfer(transfer.id)
                if entry.transaction_entry_id == second_id
            ]
            _db.session.execute(_db.text(
                "DELETE FROM budget.journal_entries "
                "WHERE transaction_entry_id = :m"
            ), {"m": first_id})
            unpaired = create_account_of_type(
                seed_user, _db.session, "Savings", "Unpaired Savings",
            )
            _db.session.commit()
            _db.session.execute(_db.text(
                "DELETE FROM budget.ledger_accounts WHERE account_id = :a"
            ), {"a": unpaired.id})
            # The second record is the to-side's; it is carried onto the
            # unpaired account by re-pointing the transfer's to-account, the
            # key's ON UPDATE CASCADE moving it in the same statement (ruling
            # R-BAL167 class 2, plan step balance:X-bi-6-4d-2: a side's record
            # is on its side's endpoint).
            assert second_id == _covering_movement_of_side(transfer.id, savings.id).id
            _db.session.execute(_db.text(
                "UPDATE budget.transfers SET to_account_id = :u WHERE id = :t"
            ), {"u": unpaired.id, "t": transfer.id})
            _db.session.commit()
            _db.session.expire_all()

            with caplog.at_level(
                logging.WARNING, logger="app.services.posting_service",
            ):
                assert posting_service.resync_all_cash_postings() == (0, 0)

            assert (
                f"skipped 1 transfer(s) whose family could not be posted: "
                f"[{transfer.id}].  Why: transfer {transfer.id}: No ledger "
                f"account is linked to account {unpaired.id};"
            ) in caplog.text
            assert [
                entry.id for entry in _entries_for_transfer(transfer.id)
            ] == [second_entry_id]

    def test_the_anchors_are_re_checked_once_after_every_source_is_re_booked(
        self, app, db, seed_user, savings,
    ):
        """A batch re-book under a true-up writes NO anchor correction.

        Ruling **R-BAL103** (developer, 2026-09-22): the deploy resync
        re-books EVERY source first, then re-checks each touched account's
        anchor corrections ONCE.  Arithmetic: Checking opens at $1,000.00; a
        $30.00 cash row and two Checking -> Savings transfers ($100.00 and
        $50.00, distinct amounts for the ad-hoc dedupe key) settle on day D,
        so Checking's ledger reads $820.00, and Checking is trued up to
        $820.00 as of D -- an assertion covering all three, its correction
        $0.00.  Every entry of all three is then deleted behind the ledger's
        back, so each source's money waits its turn: the account walk reads
        only what is posted.  The resync reports (1, 2); per (real account,
        day) the nets are restored exactly, transit nets 0.00, and NOT ONE
        anchor correction entry is written.  Mutations, each observed firing
        (+2 entries): the transfer arm re-checking inside its loop -- the
        first transfer's walk cannot see the $50.00 transfer not yet
        re-posted and books a -$50.00 true-up the second's re-check
        reverses; the row arm re-checking inside its loop -- the row's walk
        runs before either transfer is re-posted and books a -$150.00 true-up
        the one re-check reverses.

        **Re-expressed under ruling plan#1890** (card plan#1845): the money
        waiting its turn was the legacy one-entry shape, which the walk did
        not read, until plan step ``balance:X-bi-6-5``'s second leaf deleted
        that shape; a source with no entries is waiting money the resync
        still re-posts.  The figures are unchanged.
        """
        with app.app_context():
            checking = seed_user["account"]
            period = seed_user["bootstrap_period"]
            transfers = [
                create_settled_transfer(
                    seed_user, _db.session, checking, savings, period,
                    amount=amount,
                )
                for amount in (Decimal("100.00"), Decimal("50.00"))
            ]
            _db.session.commit()
            day = _covering_movement_of_side(
                transfers[0].id, checking.id,
            ).settled_on
            assert _covering_movement_of_side(
                transfers[1].id, checking.id,
            ).settled_on == day
            row = create_settled_cash_transaction(
                seed_user, _db.session, period, Decimal("30.00"),
                account=checking, name="waiting row", settled_on=day,
            )
            _db.session.commit()
            checking_ledger = _ledger_id(checking)
            savings_ledger = _ledger_id(savings)
            assert _ledger_total(checking_ledger) == Decimal("820.00")
            anchor_service.apply_anchor_true_up(
                account=checking, new_balance=Decimal("820.00"),
                observed_on=day,
            )
            _db.session.commit()
            before = _real_account_nets_by_day(checking_ledger, savings_ledger)
            assert before[(checking_ledger, day)] == Decimal("-180.00")

            for xfer in transfers:
                _delete_behind_the_ledgers_back(_entries_for_transfer(xfer.id))
            _delete_behind_the_ledgers_back(_entries_for_transaction(row.id))
            _db.session.commit()
            corrections = _anchor_correction_entry_count()
            assert _db.session.query(JournalEntry).filter(
                JournalEntry.source_kind_id.in_([
                    ref_cache.posting_source_id(PostingSourceEnum.PURCHASE),
                    ref_cache.posting_source_id(
                        PostingSourceEnum.TRANSFER_MOVEMENT,
                    ),
                ]),
            ).count() == 0

            assert posting_service.resync_all_cash_postings() == (1, 2)
            _db.session.commit()

            assert _anchor_correction_entry_count() == corrections
            assert _real_account_nets_by_day(
                checking_ledger, savings_ledger,
            ) == before
            assert _ledger_total(_transit_ledger_id(seed_user)) == Decimal("0.00")
            assert _ledger_total(checking_ledger) == Decimal("820.00")
            assert posting_service.resync_all_cash_postings() == (0, 0)

    def test_the_one_re_check_corrects_what_the_transfer_re_book_moved(
        self, app, db, seed_user, savings,
    ):
        """The re-check after the loop WRITES when a re-booked transfer moves a correction.

        The case above proves the re-check writes nothing extra; this one
        proves it runs (ruling **R-BAL103** refused "no re-check in the
        resync").  Arithmetic: Checking opens at $1,000.00; a $100.00
        transfer to Savings settles on D and its entries are deleted behind
        the ledger's back, so the account walk cannot see it; Checking is
        then trued up to $900.00 as of D, so the walk books a -$100.00
        correction for money it cannot see and Checking's ledger reads
        $900.00 with the transfer missing.  The resync re-posts the transfer
        (the -$100.00 visible again, the ledger at $800.00) and the one
        re-check reverses the correction: exactly ONE new anchor-correction
        entry, Checking +100.00, and the ledger reads $900.00.  Mutation: the
        transfer arm's hold removed -> the ledger stays at $800.00.

        **Re-expressed under ruling plan#1890** (card plan#1845), as the
        case above is: the transfer's money waited in the legacy one-entry
        shape, which kept its -$100.00 in the ledger (reading $800.00 before
        the resync) while the walk could not see it.  A deleted entry is out
        of the ledger too, so that one setup figure reads $900.00; every
        figure the resync is graded by is unchanged.
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            day = _covering_movement_of_side(transfer.id, checking.id).settled_on
            _delete_behind_the_ledgers_back(_entries_for_transfer(transfer.id))
            _db.session.commit()
            assert _entries_for_transfer(transfer.id) == []
            anchor_service.apply_anchor_true_up(
                account=checking, new_balance=Decimal("900.00"),
                observed_on=day,
            )
            checking_ledger = _ledger_id(checking)
            assert _ledger_total(checking_ledger) == Decimal("900.00")
            newest = _db.session.query(_db.func.max(JournalEntry.id)).scalar()

            assert posting_service.resync_all_cash_postings() == (0, 1)
            _db.session.commit()

            assert _new_anchor_correction_legs(newest, checking_ledger) == [
                Decimal("100.00"),
            ]
            assert _ledger_total(checking_ledger) == Decimal("900.00")

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
            assert _ledger_total(checking_ledger) == Decimal("970.00")
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
            assert _ledger_total(checking_ledger) == Decimal("1000.00")


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


def _delete_behind_the_ledgers_back(entries):
    """Delete *entries* by raw SQL, so their source's money waits for the resync.

    The ORM's append-only guard is about the app's own writes, so a forge
    goes by raw SQL, as every forge in this module does.  The account walk
    reads only what is posted, so a source whose entries are gone is money
    waiting its turn until the resync re-posts it (ruling **R-BAL103**).
    """
    _db.session.execute(
        _db.text("DELETE FROM budget.journal_entries WHERE id = ANY(:ids)"),
        {"ids": [entry.id for entry in entries]},
    )
    _db.session.expire_all()


def _anchor_correction_entry_count():
    """Return how many account opening / true-up journal entries exist."""
    return _db.session.query(JournalEntry).filter(
        JournalEntry.source_kind_id.in_([
            ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING),
            ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_TRUEUP),
        ]),
    ).count()
