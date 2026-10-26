"""The deploy resync re-books a legacy one-entry transfer to shape C once, beside the anchor
corrections it must leave alone.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.services import anchor_service, posting_service, transfer_service
from app.services._posting_write import _emit_balanced_entry, _PostingLeg
from app.services.posting_service import PostingError
from tests._test_helpers import (
    create_account_of_type,
    create_settled_cash_transaction,
    create_settled_transfer,
)

from ._helpers import (
    _ledger_id,
    _entries_for_transfer,
    _transit_ledger_id,
    _side_entries,
    _covering_movement_of_side,
    _legs_by_ledger,
    _scenario_id,
    _entries_for_transaction,
    _ledger_total,
)


# ---------------------------------------------------------------------------
# The deploy resync re-books the legacy one-entry shape (R-BAL98)
# ---------------------------------------------------------------------------


def _real_account_nets_by_day(*ledger_ids):
    """Return ``{(ledger_account_id, entry_date): net}`` over the given ledgers.

    The grade ruling **R-BAL98** names: a re-book that changes the ledger's
    SHAPE must leave every real account's net per day exactly as it was.  An
    independent re-grouping of the legs by the entry's date and the leg's
    ledger account, zero-net keys dropped, never the service's own readers.
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


class TestTheDeployResyncReBooksTheLegacyShape:
    """``resync_all_cash_postings`` moves a one-entry transfer to shape C once.

    Ruling **R-BAL98** (plan step ``balance:X-bi-6-3``): every production
    transfer booked as ONE entry (19 on the 2026-09-22 17:06 dump) is
    re-booked by the deploy's first hook through the go-forward
    ``sync_transfer_postings``' re-book half -- the legacy
    ``transfer`` source reversed at its own date, the two per-movement
    entries posted against transit -- and graded by three equalities: every
    real account's net per day byte-identical, transit netting zero per
    transfer, the trial balance closing.  This is the CI half of that grade
    (the clone rehearsal is the other): a settled transfer is forged into the
    legacy shape exactly as production holds it, and the real resync runs.
    """

    def test_one_pass_re_books_and_a_second_pass_writes_nothing(
        self, app, db, seed_user, savings,
    ):
        """One resync: legacy reversed, both sides posted, nets unchanged, then 0.

        Arithmetic: a $100 Checking -> Savings transfer settled on day D.
        Forged legacy state: ONE ``transfer``-source entry dated D,
        ``{Checking -100, Savings +100}``, and no movement-linked entry --
        production's shape before this step.  Per (real account, day) before:
        Checking D -100.00, Savings D +100.00 (each on its opening, which
        sits on an earlier day).  The resync reports (0, 1): the legacy
        entry reversed at D (its source nets zero), two per-movement entries
        posted at D -- {Checking -100, transit +100} and {Savings +100,
        transit -100} -- so per (real account, day) after == before, transit
        nets 0.00, the trial balance is 0.00, and the family holds four
        entries.  A second resync reports (0, 0) and adds nothing.
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
            day = _covering_movement_of_side(transfer.id, checking.id).settled_on

            # Forge the legacy shape: drop the go-forward per-movement entries
            # (raw SQL, as every legacy forge in the suite: the ORM's
            # append-only guard is about the app's own writes) and book the
            # one entry the pre-6-3 writer wrote, dated at the settle day.
            # Each side's record hangs off the transfer by its side link since
            # plan step balance:X-bi-6-4d-2 (R-BAL167 class 4); this joined the
            # records under the transfer's shadows until then.
            _db.session.execute(_db.text(
                "DELETE FROM budget.journal_entries WHERE id IN ("
                "  SELECT je.id FROM budget.journal_entries je"
                "  JOIN budget.transaction_entries te"
                "    ON te.id = je.transaction_entry_id"
                "  WHERE te.expense_transfer_id = :t"
                "     OR te.income_transfer_id = :t)"
            ), {"t": transfer.id})
            legacy = JournalEntry(
                user_id=seed_user["user"].id,
                scenario_id=_scenario_id(seed_user),
                pay_period_id=seed_user["bootstrap_period"].id,
                entry_date=day,
                source_kind_id=ref_cache.posting_source_id(
                    PostingSourceEnum.TRANSFER,
                ),
                transfer_id=transfer.id,
                description="Transfer: Checking to Posting Savings",
            )
            transfer_kind = ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)
            _emit_balanced_entry(legacy, [
                _PostingLeg(checking_ledger, Decimal("-100.00"), transfer_kind),
                _PostingLeg(savings_ledger, Decimal("100.00"), transfer_kind),
            ])
            _db.session.commit()
            assert _ledger_total(transit_ledger) == Decimal("0.00")
            before = _real_account_nets_by_day(checking_ledger, savings_ledger)
            assert before[(checking_ledger, day)] == Decimal("-100.00")
            assert before[(savings_ledger, day)] == Decimal("100.00")

            assert posting_service.resync_all_cash_postings() == (0, 1)
            _db.session.commit()

            # The legacy source nets to zero at its own date.
            legacy_net = (
                _db.session.query(_db.func.coalesce(_db.func.sum(Posting.amount), 0))
                .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
                .filter(
                    JournalEntry.transfer_id == transfer.id,
                    JournalEntry.entry_date == day,
                    Posting.ledger_account_id == savings_ledger,
                )
                .scalar()
            )
            assert legacy_net == 0
            # Both sides posted against transit, at the same day.
            by_side = _side_entries(transfer.id)
            [from_entry] = by_side[checking.id]
            [to_entry] = by_side[savings.id]
            assert _legs_by_ledger(from_entry.id) == {
                checking_ledger: Decimal("-100.00"),
                transit_ledger: Decimal("100.00"),
            }
            assert _legs_by_ledger(to_entry.id) == {
                savings_ledger: Decimal("100.00"),
                transit_ledger: Decimal("-100.00"),
            }
            assert {from_entry.entry_date, to_entry.entry_date} == {day}
            # The three equalities.
            assert _real_account_nets_by_day(
                checking_ledger, savings_ledger,
            ) == before
            assert _ledger_total(transit_ledger) == Decimal("0.00")
            assert _db.session.query(
                _db.func.coalesce(_db.func.sum(Posting.amount), 0)
            ).scalar() == 0
            assert len(_entries_for_transfer(transfer.id)) == 4

            assert posting_service.resync_all_cash_postings() == (0, 0)
            assert len(_entries_for_transfer(transfer.id)) == 4

    def test_a_refused_transfer_is_skipped_whole(
        self, app, db, seed_user, savings,
    ):
        """A transfer one side of which cannot post leaves NOTHING half-booked.

        The leaf's adversarial review, finding 1: the pair's door writes in
        sequence (the legacy reversal, then each side), so a refusal on the
        second side would leave the first side and the reversal committed by
        the batch -- Checking debited into transit, nothing arriving, a trial
        balance that still closes.  The resync runs each transfer under a
        SAVEPOINT and rolls a refused one back whole.  Arithmetic: the legacy
        shape forged as in the case above (the entry intact, both legs), then
        the TO-side movement re-pointed onto an account whose ledger pairing
        has been removed (an impossible state, the fail-loud fixture's) -- so
        the door reverses the legacy entry, posts the from side, and only
        then is refused.  The resync skips the transfer whole; the legacy
        entry still stands un-reversed, no per-movement entry exists, transit
        is untouched.  Mutation: without the savepoint the reversal and the
        from-side entry survive the refusal and the legacy entry is netted
        away -- so since leaf 3b the resync no longer refuses and the case
        fails at the raise (DID NOT RAISE, observed); a savepoint rolling back
        only PART of the door's writes is what the assertions after the raise
        still catch.

        **Re-expressed under rule 5 at leaf 3b, the developer confirming it
        2026-09-22** (ruling **R-BAL104**): this skipped transfer keeps a
        NONZERO legacy posting the account walk cannot see, so the resync no
        longer returns (0, 0) -- it REFUSES after its loop, naming the
        transfer as holder and as skipped, and the deploy's commit never
        runs.  Every other assertion is unchanged and runs in the caller's
        transaction right after the raise.  A skip holding no legacy net
        still returns (the next case); a legacy net netting zero in total but
        not per day still refuses (the one after).
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
            day = _covering_movement_of_side(transfer.id, checking.id).settled_on
            # Each side's record hangs off the transfer by its side link since
            # plan step balance:X-bi-6-4d-2 (R-BAL167 class 4); this joined the
            # records under the transfer's shadows until then.
            _db.session.execute(_db.text(
                "DELETE FROM budget.journal_entries WHERE id IN ("
                "  SELECT je.id FROM budget.journal_entries je"
                "  JOIN budget.transaction_entries te"
                "    ON te.id = je.transaction_entry_id"
                "  WHERE te.expense_transfer_id = :t"
                "     OR te.income_transfer_id = :t)"
            ), {"t": transfer.id})
            legacy = JournalEntry(
                user_id=seed_user["user"].id,
                scenario_id=_scenario_id(seed_user),
                pay_period_id=seed_user["bootstrap_period"].id,
                entry_date=day,
                source_kind_id=ref_cache.posting_source_id(
                    PostingSourceEnum.TRANSFER,
                ),
                transfer_id=transfer.id,
                description="Transfer: Checking to Posting Savings",
            )
            transfer_kind = ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)
            _emit_balanced_entry(legacy, [
                _PostingLeg(checking_ledger, Decimal("-100.00"), transfer_kind),
                _PostingLeg(savings_ledger, Decimal("100.00"), transfer_kind),
            ])
            _db.session.commit()
            # An unpaired account for the to-side movement to sit on: created
            # (which pairs it), its pairing removed (no legs on it), and the
            # to-side movement re-pointed onto it by raw SQL -- the legacy
            # entry keeps both legs, so the refusal comes from the second
            # side's missing pairing and nowhere earlier.
            unpaired = create_account_of_type(
                seed_user, _db.session, "Savings", "Unpaired Savings",
            )
            _db.session.commit()
            _db.session.execute(_db.text(
                "DELETE FROM budget.ledger_accounts WHERE account_id = :a"
            ), {"a": unpaired.id})
            # A side's record is on its side's endpoint since plan step
            # balance:X-bi-6-4d-2 (``fk_transaction_entries_income_side``), so
            # it is carried there by re-pointing the transfer's to-account,
            # the key's ON UPDATE CASCADE moving it in the same statement; the
            # record alone was re-pointed until then, a write the key now
            # refuses (ruling R-BAL167 class 2).
            to_side = _covering_movement_of_side(transfer.id, savings.id)
            _db.session.execute(_db.text(
                "UPDATE budget.transfers SET to_account_id = :u WHERE id = :t"
            ), {"u": unpaired.id, "t": transfer.id})
            _db.session.commit()
            _db.session.expire_all()
            assert to_side.account_id == unpaired.id

            with pytest.raises(
                PostingError,
                match=(
                    rf"transfer\(s\) \[{transfer.id}\] still hold a nonzero "
                    rf"legacy .*\(skipped this pass: \[{transfer.id}\]\)"
                ),
            ):
                posting_service.resync_all_cash_postings()

            # Nothing half-booked: the legacy SOURCE still nets its whole
            # effect (un-reversed -- the entry's own legs are append-only, so
            # the net is what grades it), no per-movement entry exists,
            # transit is untouched.
            legacy_nets = {
                ledger_id: net
                for ledger_id, net in _db.session.query(
                    Posting.ledger_account_id, _db.func.sum(Posting.amount),
                )
                .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
                .filter(JournalEntry.transfer_id == transfer.id)
                .group_by(Posting.ledger_account_id)
                .all()
            }
            assert legacy_nets == {
                checking_ledger: Decimal("-100.00"),
                savings_ledger: Decimal("100.00"),
            }
            assert _db.session.query(JournalEntry).filter(
                JournalEntry.source_kind_id == ref_cache.posting_source_id(
                    PostingSourceEnum.TRANSFER_MOVEMENT,
                ),
            ).count() == 0
            assert _ledger_total(transit_ledger) == Decimal("0.00")
            assert _ledger_total(checking_ledger) == Decimal("900.00")

    def test_a_refused_transfer_holding_no_legacy_net_is_still_skipped(
        self, app, db, seed_user, savings, caplog,
    ):
        """A skip with NO legacy net still returns, skipped whole and logged.

        Ruling **R-BAL104** refuses the resync only for a transfer whose
        LEGACY source still nets nonzero; every other family keeps the
        2026-08-17 ruling -- one that cannot post is skipped and reported,
        never allowed to make the deploy unbootable -- and this is the case
        where the per-transfer SAVEPOINT still decides what the deploy
        commits.  Arithmetic: a $100.00 Checking -> Savings transfer settled
        go-forward (two movement entries, no legacy entry); the entry of the
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
            # unpaired account by re-pointing the transfer's to-account, as in
            # the case above (ruling R-BAL167 class 2, plan step
            # balance:X-bi-6-4d-2: a side's record is on its side's endpoint).
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

    def test_a_legacy_net_zero_in_total_but_not_per_day_still_refuses(
        self, app, db, seed_user, savings,
    ):
        """A skipped transfer's legacy residue refuses PER (period, day), not in total.

        Ruling **R-BAL104** refuses while a transfer holds a nonzero legacy
        posting "on any (period, day)", and the walk that cannot see it moves
        each DAY's balance: a settle / reversal pair straddling two days (the
        E1a review's H2 residue) nets zero in total and is still money on
        each day.  Arithmetic: a $100.00 Checking -> Savings transfer settled
        on D, its go-forward movement entries replaced by a straddling legacy
        pair -- {Checking -100, Savings +100} at D and {Checking +100,
        Savings -100} at D-8, $0.00 in total -- and its to-side movement
        re-pointed onto an unpaired account so the resync skips it.  The
        resync refuses naming the transfer and why it was skipped (the
        unpaired account); the pair still stands on both days (Checking
        -100.00 at D and +100.00 at D-8, Savings the opposite).  Mutation: a
        refusal that sums each transfer's legacy legs across days reads $0.00
        here and lets the resync finish.
        """
        with app.app_context():
            checking = seed_user["account"]
            user_id = seed_user["user"].id
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(checking)
            savings_ledger = _ledger_id(savings)
            day = _covering_movement_of_side(transfer.id, checking.id).settled_on
            earlier = day - timedelta(days=8)
            # By the records' side links since plan step balance:X-bi-6-4d-2
            # (R-BAL167 class 4), the records under the shadows until then.
            _db.session.execute(_db.text(
                "DELETE FROM budget.journal_entries "
                "WHERE transaction_entry_id IN ("
                "  SELECT te.id FROM budget.transaction_entries te"
                "  WHERE te.expense_transfer_id = :t"
                "     OR te.income_transfer_id = :t)"
            ), {"t": transfer.id})
            transfer_kind = ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)
            for entry_day, checking_leg in (
                (day, Decimal("-100.00")),
                (earlier, Decimal("100.00")),
            ):
                _emit_balanced_entry(
                    JournalEntry(
                        user_id=user_id,
                        scenario_id=_scenario_id(seed_user),
                        pay_period_id=seed_user["bootstrap_period"].id,
                        entry_date=entry_day,
                        source_kind_id=ref_cache.posting_source_id(
                            PostingSourceEnum.TRANSFER,
                        ),
                        transfer_id=transfer.id,
                        description="Transfer: Checking to Posting Savings",
                    ),
                    [
                        _PostingLeg(checking_ledger, checking_leg, transfer_kind),
                        _PostingLeg(savings_ledger, -checking_leg, transfer_kind),
                    ],
                )
            unpaired = create_account_of_type(
                seed_user, _db.session, "Savings", "Unpaired Savings",
            )
            _db.session.commit()
            _db.session.execute(_db.text(
                "DELETE FROM budget.ledger_accounts WHERE account_id = :a"
            ), {"a": unpaired.id})
            # A side's record is on its side's endpoint since plan step
            # balance:X-bi-6-4d-2 (``fk_transaction_entries_income_side``), so
            # it is carried there by re-pointing the transfer's to-account,
            # the key's ON UPDATE CASCADE moving it in the same statement; the
            # record alone was re-pointed until then, a write the key now
            # refuses (ruling R-BAL167 class 2).
            to_side = _covering_movement_of_side(transfer.id, savings.id)
            _db.session.execute(_db.text(
                "UPDATE budget.transfers SET to_account_id = :u WHERE id = :t"
            ), {"u": unpaired.id, "t": transfer.id})
            _db.session.commit()
            _db.session.expire_all()
            assert to_side.account_id == unpaired.id

            with pytest.raises(
                PostingError,
                match=(
                    rf"transfer\(s\) \[{transfer.id}\] still hold a nonzero "
                    rf"legacy .*\(skipped this pass: \[{transfer.id}\]\)\.  "
                    rf"Why: transfer {transfer.id}: No ledger account is "
                    rf"linked to account {unpaired.id};"
                ),
            ):
                posting_service.resync_all_cash_postings()

            legacy_by_day = {
                (ledger_id, entry_day): net
                for ledger_id, entry_day, net in _db.session.query(
                    Posting.ledger_account_id, JournalEntry.entry_date,
                    _db.func.sum(Posting.amount),
                )
                .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
                .filter(JournalEntry.transfer_id == transfer.id)
                .group_by(Posting.ledger_account_id, JournalEntry.entry_date)
                .all()
            }
            assert legacy_by_day == {
                (checking_ledger, day): Decimal("-100.00"),
                (savings_ledger, day): Decimal("100.00"),
                (checking_ledger, earlier): Decimal("100.00"),
                (savings_ledger, earlier): Decimal("-100.00"),
            }

    def test_a_reverted_transfers_legacy_residue_heals_too(
        self, app, db, seed_user, savings,
    ):
        """Cross-date legacy residue on a REVERTED transfer nets to zero per date.

        The leaf's adversarial review, finding 5: the loan lineage probe no
        longer reads the legacy source, and a reverted transfer is neither
        settled nor holds a dated movement, so the resync's transfer arm must
        reach it by its LEGACY ENTRIES or the E1a review's H2 residue (a
        settle / reversal pair straddling two dates, net zero in total and
        not per date) would be healed by nothing.  Arithmetic: settle then
        revert (the movement entries net zero), then forge the residue: a
        legacy entry at D {Checking -100, Savings +100} and its reversal at
        D-8 {Checking +100, Savings -100}.  Per (real account, day) before
        the forge is the clean state; the resync reports (0, 1) and restores
        it exactly -- both legacy keys net zero -- and a second pass is
        (0, 0).
        """
        with app.app_context():
            checking = seed_user["account"]
            user_id = seed_user["user"].id
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            day = _covering_movement_of_side(transfer.id, checking.id).settled_on
            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(checking)
            savings_ledger = _ledger_id(savings)
            clean = _real_account_nets_by_day(checking_ledger, savings_ledger)

            transfer_kind = ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)
            for entry_day, checking_leg in (
                (day, Decimal("-100.00")),
                (day - timedelta(days=8), Decimal("100.00")),
            ):
                residue = JournalEntry(
                    user_id=user_id,
                    scenario_id=_scenario_id(seed_user),
                    pay_period_id=seed_user["bootstrap_period"].id,
                    entry_date=entry_day,
                    source_kind_id=ref_cache.posting_source_id(
                        PostingSourceEnum.TRANSFER,
                    ),
                    transfer_id=transfer.id,
                    description="Transfer: Checking to Posting Savings",
                )
                _emit_balanced_entry(residue, [
                    _PostingLeg(checking_ledger, checking_leg, transfer_kind),
                    _PostingLeg(savings_ledger, -checking_leg, transfer_kind),
                ])
            _db.session.commit()
            assert _real_account_nets_by_day(
                checking_ledger, savings_ledger,
            ) != clean

            assert posting_service.resync_all_cash_postings() == (0, 1)
            _db.session.commit()

            assert _real_account_nets_by_day(
                checking_ledger, savings_ledger,
            ) == clean
            assert posting_service.resync_all_cash_postings() == (0, 0)

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
        $0.00.  All three are then forged into the legacy shape (the row's
        movement entry into one ``transaction``-source entry, each
        transfer's two into one ``transfer``-source entry; every real leg and
        day unchanged).  The resync reports (1, 2); per (real account, day)
        the nets are unchanged, transit nets 0.00, and NOT ONE anchor
        correction entry is written.  Mutations, each observed firing (+2
        entries): the transfer arm re-checking inside its loop -- the first
        transfer's walk cannot see the $50.00 legacy entry (the walk reads
        no ``transfer_id``-linked entry) and books a -$50.00 true-up the
        second's re-check reverses; the row arm re-checking inside its loop
        -- the row's walk runs while both transfers are legacy and books a
        -$150.00 true-up the one re-check reverses.
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
                account=checking, name="legacy row", settled_on=day,
            )
            _db.session.commit()
            checking_ledger = _ledger_id(checking)
            savings_ledger = _ledger_id(savings)
            assert _ledger_total(checking_ledger) == Decimal("820.00")
            anchor_service.apply_anchor_true_up(
                account=checking, new_balance=Decimal("820.00"),
                observed_on=day,
            )

            for xfer in transfers:
                _fold_into_legacy_entry(
                    _entries_for_transfer(xfer.id),
                    source=PostingSourceEnum.TRANSFER,
                    description="Transfer: Checking to Posting Savings",
                    transfer_id=xfer.id,
                )
            _fold_into_legacy_entry(
                _entries_for_transaction(row.id),
                source=PostingSourceEnum.TRANSACTION,
                description=row.name, transaction_id=row.id,
            )
            _db.session.commit()
            before = _real_account_nets_by_day(checking_ledger, savings_ledger)
            corrections = _anchor_correction_entry_count()
            assert before[(checking_ledger, day)] == Decimal("-180.00")
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
        transfer to Savings settles on D and is forged into the legacy shape,
        which the account walk does not read; Checking is then trued up to
        $900.00 as of D, so the walk books a -$100.00 correction for money it
        cannot see and Checking's ledger reads $800.00.  The resync re-books
        the transfer (the -$100.00 now visible as a movement) and the one
        re-check reverses the correction: exactly ONE new anchor-correction
        entry, Checking +100.00, and the ledger reads $900.00.  Mutation: the
        transfer arm's hold removed -> the ledger stays at $800.00.
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            day = _covering_movement_of_side(transfer.id, checking.id).settled_on
            _fold_into_legacy_entry(
                _entries_for_transfer(transfer.id),
                source=PostingSourceEnum.TRANSFER,
                description="Transfer: Checking to Posting Savings",
                transfer_id=transfer.id,
            )
            _db.session.commit()
            anchor_service.apply_anchor_true_up(
                account=checking, new_balance=Decimal("900.00"),
                observed_on=day,
            )
            checking_ledger = _ledger_id(checking)
            assert _ledger_total(checking_ledger) == Decimal("800.00")
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


def _fold_into_legacy_entry(entries, *, source, description, **link):
    """Replace *entries* by ONE entry of the legacy *source*, legs summed.

    The pre-``X-bi-6-3`` shape of a settled source: its movement entries'
    legs summed per ledger account (a transfer's transit legs net away), the
    entries deleted by raw SQL (the ORM's append-only guard is about the
    app's own writes), and one entry emitted at their shared day and period
    under *source*, linked by *link* (``transfer_id`` or ``transaction_id``).
    """
    [day] = {entry.entry_date for entry in entries}
    [period_id] = {entry.pay_period_id for entry in entries}
    legs: dict = {}
    for leg in _db.session.query(Posting).filter(
        Posting.journal_entry_id.in_([entry.id for entry in entries]),
    ):
        amount, _ = legs.get(leg.ledger_account_id, (Decimal("0.00"), None))
        legs[leg.ledger_account_id] = (amount + leg.amount, leg.posting_kind_id)
    [(user_id, scenario_id)] = {
        (entry.user_id, entry.scenario_id) for entry in entries
    }
    _db.session.execute(
        _db.text("DELETE FROM budget.journal_entries WHERE id = ANY(:ids)"),
        {"ids": [entry.id for entry in entries]},
    )
    _db.session.expire_all()
    _emit_balanced_entry(
        JournalEntry(
            user_id=user_id, scenario_id=scenario_id,
            pay_period_id=period_id, entry_date=day,
            source_kind_id=ref_cache.posting_source_id(source),
            description=description, **link,
        ),
        [
            _PostingLeg(ledger_id, amount, kind)
            for ledger_id, (amount, kind) in sorted(legs.items())
            if amount != 0
        ],
    )


def _anchor_correction_entry_count():
    """Return how many account opening / true-up journal entries exist."""
    return _db.session.query(JournalEntry).filter(
        JournalEntry.source_kind_id.in_([
            ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING),
            ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_TRUEUP),
        ]),
    ).count()
