"""Lifecycle tests for a transaction's ledger lines through its doors (Step 3, Commit 6).

Commit 6 wired :func:`~app.services.posting_service.sync_transaction_postings`
into every ordinary-transaction effect boundary -- the mark-done / PATCH /
cancel / delete routes, the envelope entry-mutation service, the credit
payback-delete paths, and carry-forward -- and plan task plan#3097 took it out
of every one of them: no transaction door writes the stored ledger.  These
tests drive transactions END TO END through those handlers and assert the
ledger worked out from the records, which is what settling, editing,
reverting, deleting and carrying forward a transaction still move (plan step
plan#2914).

The load-bearing properties, each pinned by a test below (until plan task plan#3097
the store's sync held the first and third by running LAST and gated):

  * a manual ``actual_amount`` books the ACTUAL not the estimate, and a
    settle-and-recategorize PATCH books the NEW category (the 2.8b HIGH, both
    directions);
  * a revert-and-recategorize PATCH leaves neither the OLD category nor the
    new ``category_id`` holding the row (the 2.8 CRITICAL), at the route level;
  * a metadata-only edit moves nothing;
  * an envelope books its debit-only effect (``effective - Sigma(credit)``), and
    an entry mutation on a posted envelope moves its ledger figure;
  * every delete path (hard / soft, the settled-payback unmark, and a recursive
    payback chain) leaves the accounts where they were before the settle;
  * each envelope source that carry-forward settles books its confirmed effect.

After each mutation the per-account reconciliation invariant is asserted in
its Build-Order Step 5 ABSOLUTE form: the account's worked-out net ``== opening
anchor + settled_transfer_effect + posted_purchase_effect``.  The read books
the seed Checking's $1000.00 opening the day before its origination assertion, and every
settle in this suite is stamped at SERVER now (the seam's ``db.func.now()``,
including carry-forward's ``settle_from_entries`` default) -- after the
origination assertion -- so the settled effects ride on top of the opening
and the Checking totals below are ``1000.00 + effect``.  All money is
``Decimal`` from strings, with the arithmetic shown per the testing standard.
"""
