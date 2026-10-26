"""Tests for ``posting_service`` (Build-Order Steps 2 and 3).

``posting_service`` is the sole go-forward writer of the double-entry posting
ledger.  Step 2 posts settled **transfers**
(:func:`~app.services.posting_service.sync_transfer_postings`); Step 3 (Commit
4) adds settled ordinary **cash transactions**
(:func:`~app.services.posting_service.sync_transaction_postings`).  Both
reconcile a source's net posted ledger effect to a target by emitting one
balanced delta journal entry, idempotently.
:func:`~app.services.posting_service.account_posting_total`,
:func:`~app.services.posting_service.settled_transfer_effect`, and
:func:`~app.services.posting_service.posted_purchase_effect` are the
reconciliation helpers the oracle consumes (the transaction source's own,
``settled_transaction_effect``, went at plan step ``balance:X-bi-4a``).

The transfer tests pin the load-bearing properties with hand-computed
arithmetic.  **A settled transfer is TWO entries since plan step
``balance:X-bi-6-3``** (rulings **R-BAL45** and **R-BAL101**): one per side's
covering movement, on that movement's own day, each against the owner's
Transfers-in-transit account -- ``{from -amount, transit +amount}`` and
``{to +amount, transit -amount}`` -- so every figure a real account's ledger
carries in this package's transfer cases is what it was under the one-entry
shape, and the transit account nets to zero per transfer.  The cases that read "the transfer's entry" were
re-expressed for the pair when the shape moved (each docstring says how);
every per-real-account figure is unchanged:

  * **Sign + balance** -- a settle posts ``-amount`` on the from-account's
    ledger and ``+amount`` on the to-account's, each against transit, each
    entry summing to zero; the rule is class-independent (asset->asset AND
    asset->liability).
  * **Effective amount, not transfer amount** -- a settled transfer's
    stored figure (``Transfer.settled_figure``, which each side's covering
    movement posts) overrides the nominal transfer amount (the value the
    balance calculator and the oracle use).
  * **Idempotency** -- a repeat sync computes ``delta = 0`` and writes
    nothing.
  * **Reversal reads the ledger** -- a revert / delete negates exactly what
    was posted, not the (possibly-edited) transfer amount; a revert ->
    edit-amount -> re-settle posts the new amount.
  * **Reconciliation** -- the per-account posting total equals the
    settled transfer effect, and both net to zero after a reversal.
  * **Fail loud** -- a ``None`` scenario, a missing ledger-account pairing,
    and an unbalanced set of legs each raise :class:`PostingError`.

The transfer states are built through ``transfer_service`` (the sole transfer
writer, via the ``create_settled_transfer`` helper), so every transfer and
its sides' records are exactly what production writes.  Commit 5 wires
``posting_service`` into that service, so settling a transfer already
auto-posts its ledger entry; ``test_a_settled_transfer.py``'s settle classes
(``TestSyncSettlePostsBalancedEntry``, ``TestSyncSettleEntryDate``) read that
auto-posted entry back, while its ``TestSyncIdempotency`` and
``TestSyncReversal`` still invoke ``posting_service`` directly to prove a
re-sync no-ops or reverses.

The Step-3 transaction tests (the ``TestTransaction*`` and ``TestEnvelope*``
classes of ``test_a_settled_transaction.py`` and ``test_fail_loud.py``'s
``TestTransactionFailLoud``) cover the plan's one cash-effect formula --
``effective - Sigma(credit entries)``, signed ``+`` income / ``-`` expense --
with hand-computed worked examples (plain income/expense, the debit-only
envelope effect, the all-credit no-op), the correct-by-construction reconcile
(idempotency; reversal; the 2.8 CRITICAL revert -> recategorize -> re-settle,
proven at the service layer), counter-leg routing into the per-category /
Uncategorized-fallback account, and fail-loud.  The doors that write a
transaction call ``sync_transaction_postings`` themselves -- the settle verb
(``transaction_service.settle_transaction``) and every other door that settles
or edits a row (``routes/transactions/mutations.py``, ``credit_workflow`` and
``carry_forward_service`` among them) -- but these tests build settled rows
through the test helpers and the status seam's settle arm, neither of which
posts, and invoke it directly.

**Absolute totals since Build-Order Step 5 (C6).**  ``create_account`` posts
each fixture account's OPENING anchor correction at create time (the seed
Checking's $1000.00, the ledger-suite Savings' $100.00 sentinel), and the
effect-time self-heal at the sync tails keeps those corrections current, so
``account_posting_total`` is an ABSOLUTE balance: ``latest anchor +
post-assertion settled effects``.  A settle dated today (the helper default)
rides on top of the opening; a settle dated in
the 2024 bootstrap period is attributed BEFORE the origination assertion and
is absorbed into the opening delta instead (the total returns to the anchor).
The ``settled_*_effect`` source-table readers are unchanged.
"""
