"""Migration ``82f7b7ed4332``: a transfer is one row.

Plan step ``balance:X-bi-6-4d-2``, rulings **R-BAL166** (the twin rows are
deleted in the release that stops their upkeep), **R-BAL258** (the database
refuses a new one, "Database refuses") and **R-BAL260** (the downgrade restores
each twin from a kept copy, "Keep a copy").  The revision keeps a copy of every
twin in ``system.transfer_twin_purge``, deletes them, and adds
``ck_transactions_names_no_transfer``; it REFUSES, writing nothing, while a twin
could not go cleanly, a twin that is no copy of its transfer among them (its
status aside, which the app at ``e616adf7fe22`` writes on the transfer alone),
a twin filed under a category that is not its owner's, and a transfer filed
under one that is not its owner's, whose twins the downgrade would build under
it; its downgrade restores column for column every kept twin whose transfer was not
written since (re-reading what its transfer and its side's record say now),
rebuilds from its transfer (keeping its identity) every twin whose transfer
was, puts a kept value that no longer fits back as the transfer says (ruling
**R-BAL261**), builds two new twins for a transfer created since, and drops
the copy.  Every refusal is driven through the shipped ``upgrade``, so a
refusal it stopped asking fails here.

Driven against the test database, which is built at head, through the shipped
``upgrade`` / ``downgrade``.  Each case first steps down through
``c956c1d83108`` (``below_the_transfer_column_drop``), which drops the
transfer column this revision deletes twins from and restores them into, and
drops the copy (ruling plan#1851).  No twin exists there and no copy, so the
first downgrade in each case BUILDS two per transfer -- the pre-step shape the
upgrade meets.  The schema differs between the two revisions only by the CHECK
and the copy, so the app's doors run at either.  Every case ends at this
revision or below it, never back at head.
"""
