"""Tests for the ``LedgerAccount`` model (Build-Order Step 2, Commit 2;
the category chart-of-accounts columns added Step 3, Commit 2).

``budget.ledger_accounts`` is the chart of accounts for the double-entry
posting ledger.  These tests pin the storage-tier invariants the model and
its migration jointly guarantee:

  * the partial unique index permits exactly one *linked* ledger account
    per real account, while allowing many *unlinked* rows (NULL
    ``account_id``);
  * the ``ck_ledger_accounts_name_present`` CHECK refuses a row that
    carries neither a ``name`` nor an ``account_id`` (the display rule
    COALESCE(account.name, ledger_account.name) could otherwise resolve to
    NULL) -- including a category row whose only other link is
    ``category_id``;
  * a linked row's display label derives from the live ``account.name``,
    even after a rename;
  * the ``account_id`` CASCADE disposes of the ledger account when an
    empty account is deleted, while the ``class_id`` RESTRICT refuses to
    drop a referenced accounting class;
  * (Step 3) ``uq_ledger_accounts_category`` keys one category ledger
    account per (owner, category, class) and ``uq_ledger_accounts_owner_bucket``
    one *fallback* per (owner, class) (keyed ``WHERE is_owner_bucket``); those
    plus ``uq_ledger_accounts_account_kind`` constrain the linked / category /
    fallback kinds, while deleted-category *orphans* (``is_owner_bucket`` False,
    NULL/NULL) carry no unique and coexist freely;
  * (Step 3) ``ck_ledger_accounts_account_or_category_null`` forbids a row
    setting BOTH ``account_id`` and ``category_id``, and
    ``ck_ledger_accounts_owner_bucket_shape`` forbids ``is_owner_bucket`` on
    anything but the NULL/NULL shape (so the flag stays a true discriminator);
  * (Step 3) ``category_id`` is SET NULL on a category delete -- the
    posted-to ledger account survives as an orphan with its ``name``
    snapshot intact, coexisting with the fallback rather than colliding with
    it (the H1 regression the ``is_owner_bucket`` flag closes);
  * the table is registered for auditing and its trigger fires.
"""
