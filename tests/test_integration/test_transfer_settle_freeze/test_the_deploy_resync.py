"""resync_all_cash_postings skips a transfer it cannot post and goes on, so one broken pair cannot
stop the deploy's resync.
"""

import logging

from app.extensions import db
from app.services import posting_service, transfer_service
from tests._test_helpers import create_account_of_type, transfer_side_record

from ._helpers import _plain_transfer


class TestOneBrokenPairCannotStopTheDeployResync:
    """``resync_all_cash_postings`` skips a transfer it cannot post, and goes on.

    **The refusal that made this necessary is correct, and its blast radius was
    not** (developer ruling, 2026-08-17).  The one-entry writer's figure read
    (deleted with that writer at plan step ``balance:X-bi-6-3``) gained a
    settled-status predicate at plan step X-au-c3 and raised ``PostingError``
    when the income shadow was not settled -- right for a single write path,
    where a caller asking what one transfer settled at must not be handed a
    fabricated figure.

    But this batch selects transfers by the PARENT's status and runs at
    container start (``scripts/init_database.py``) with no isolation, so one
    Transfer-Invariant-4 drift -- the exact corruption ``restore_transfer``
    exists to repair -- made the app unbootable for every user, and the operator
    could not reach the screen that would name the row.  Before the predicate it
    would have posted a figure and carried on.

    **That drift has no row to live on since plan step balance:X-bi-6-4d-2**: a
    transfer is one row, and no transaction can name it:
    ``ck_transactions_names_no_transfer`` refused a twin (ruling **R-BAL258**)
    until plan step balance:X-bi-6-5 dropped the column.  The batch's blast
    radius is still the subject, so the case below breaks a transfer the way
    one still CAN be broken -- its to-side's record carried onto an account
    whose ledger pairing is gone, ``test_posting_service.py``'s recipe --
    beside a healthy transfer the resync genuinely has to re-post.
    """

    @staticmethod
    def _movement_entry_count(xfer_id):
        """Return how many journal entries *xfer_id*'s two side records hold.

        A side's record hangs off the transfer by its side link since plan
        step balance:X-bi-6-4d-2, and a settled side posts ONE entry against
        transit (plan step ``balance:X-bi-6-3``), so a settled transfer at
        target holds two.
        """
        return db.session.execute(db.text(
            "SELECT count(*) FROM budget.journal_entries je"
            "  JOIN budget.transaction_entries te"
            "    ON te.id = je.transaction_entry_id"
            " WHERE te.expense_transfer_id = :t OR te.income_transfer_id = :t"
        ), {"t": xfer_id}).scalar()

    def test_it_skips_the_broken_transfer_and_still_posts_the_healthy_one(
        self, app, db, seed_user, seed_periods, caplog,
    ):
        """The resync refuses one transfer, re-posts the other, and returns.

        **Re-expressed at plan step balance:X-bi-6-4d-2** (ruling **R-BAL167**
        class 1, the substitution stated; it was
        ``test_it_skips_the_broken_pair_and_still_posts_the_healthy_one``, and
        at cp2c of that step briefly
        ``test_the_drift_it_skipped_is_refused_and_the_batch_still_completes``).
        Its drift was the INCOME twin's status moved out of the settled band
        under a settled parent, a row no transfer has now.  The broken
        transfer below is broken the way one still can be: its to-account
        re-pointed by raw SQL onto an account whose ``budget.ledger_accounts``
        pairing was deleted (the side's record follows on its key's ON UPDATE
        CASCADE), so the pair's door refuses it naming that account.  And
        where both transfers were already at target before -- so nothing told
        a resync that reached them from one that reached neither -- the
        healthy one's two entries are deleted, so the resync has a re-post to
        make and reports it.

        **The broken transfer is created FIRST**, and the resync walks
        transfers in id order, so the refusal comes before the reach: a batch
        that stopped at its first refusal would not reach the healthy one.

        Arithmetic: two plain transfers settled go-forward, ``$60.00`` to
        Savings (broken) and ``$45.00`` to Vacation (healthy), each holding
        two movement entries.  The first resync reports ``(0, 1)`` -- no
        transaction, one transfer re-posted -- logs exactly the broken one as
        skipped, and leaves the healthy one holding two entries again and the
        broken one the two it had.  A second resync reports ``(0, 0)``.

        Shown to FIRE (``cp2c-a``): a resync walking no transfer, one with the
        per-transfer ``except PostingError`` deleted, and one that stops at
        its first refusal each fail it.  What it cannot see is a savepoint
        rolling back only PART of a refused family's writes, because the
        broken transfer here writes nothing before its refusal; that half is
        ``test_posting_service.py``'s ``test_a_refused_transfer_holding_no_
        legacy_net_is_still_skipped``, the sibling where a skip still returns.
        ``test_a_refused_transfer_is_skipped_whole`` grades the same savepoint
        on a family holding a LEGACY net, and the resync RAISES there since
        ruling **R-BAL104**, so the batch does not complete in that case.
        """
        with app.app_context():
            broken = _plain_transfer(
                seed_user, seed_periods, amount="60.00",
                destination="Savings",
            )
            healthy = _plain_transfer(
                seed_user, seed_periods, amount="45.00",
                destination="Vacation",
            )
            db.session.commit()
            owner = seed_user["user"].id
            broken_id, healthy_id = broken.id, healthy.id
            # The premise of the ordering argument in the docstring.
            assert broken_id < healthy_id

            transfer_service.settle_transfer(broken_id, owner)
            transfer_service.settle_transfer(healthy_id, owner)
            db.session.commit()
            assert self._movement_entry_count(broken_id) == 2
            assert self._movement_entry_count(healthy_id) == 2

            # The healthy transfer needs a re-post: its two entries go.
            db.session.execute(db.text(
                "DELETE FROM budget.journal_entries WHERE id IN ("
                "  SELECT je.id FROM budget.journal_entries je"
                "  JOIN budget.transaction_entries te"
                "    ON te.id = je.transaction_entry_id"
                "  WHERE te.expense_transfer_id = :t"
                "     OR te.income_transfer_id = :t)"
            ), {"t": healthy_id})
            # The broken transfer: an account created (which pairs it), its
            # pairing removed, and the broken transfer's to-account re-pointed
            # onto it, its side's record moving with it on the key's cascade.
            unpaired = create_account_of_type(
                seed_user, db.session, "Savings", "Unpaired Savings",
            )
            db.session.commit()
            unpaired_id = unpaired.id
            db.session.execute(db.text(
                "DELETE FROM budget.ledger_accounts WHERE account_id = :a"
            ), {"a": unpaired_id})
            db.session.execute(db.text(
                "UPDATE budget.transfers SET to_account_id = :u WHERE id = :t"
            ), {"u": unpaired_id, "t": broken_id})
            db.session.commit()
            db.session.expire_all()
            assert self._movement_entry_count(healthy_id) == 0
            assert transfer_side_record(
                db.session, broken_id, unpaired_id,
            ) is not None

            with caplog.at_level(
                logging.WARNING, logger="app.services.posting_service",
            ):
                # The batch completes rather than raising, and it REACHED the
                # healthy transfer past the refused one: that is the claim.
                assert posting_service.resync_all_cash_postings() == (0, 1)
            db.session.commit()

            assert (
                f"skipped 1 transfer(s) whose family could not be posted: "
                f"[{broken_id}]."
            ) in caplog.text
            assert self._movement_entry_count(healthy_id) == 2
            assert self._movement_entry_count(broken_id) == 2

            # At target now, so a further pass re-posts nothing -- and still
            # skips the broken transfer rather than raising.
            assert posting_service.resync_all_cash_postings() == (0, 0)
