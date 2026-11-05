"""
Shekel Budget App -- the statement-import models' table arguments, as one value each.

**Born as a MOVE in the commit that also edited two of the four** (plan step
``bank_import:X-f6b-1b``, 2026-09-18, under ruling **balance:R-IR**:
*pylint's 1,000-line module ceiling stays, and the session that breaks a
module is the one that splits it*).  :mod:`app.models.statement_import` stood
at 1,132 lines once the sighting relation gained the merchant key and the line
gained its two sealed projections, so the four ``__table_args__`` tuples came
here on the seam :mod:`app.models._transaction_table_args` established:
``__table_args__`` is the one part of a model that is a VALUE rather than a
declaration, names no attribute the class exposes, is imported by no caller,
and is read by SQLAlchemy exactly once at class construction.  Every other
cut (the columns, the properties, the class docstrings) would have separated
things a reader needs side by side.

**What "moved verbatim" covers, and what it does not, stated against the
tree the commit started from** (``git show <parent>:app/models/statement_import.py``).
``account_external_identity_table_args`` and ``statement_import_table_args``
are byte-for-byte the tuples that stood in the class bodies: ``ast.dump`` of
each equals ``ast.dump`` of the expression it replaced -- identical in KIND,
in ORDER and in every keyword argument, so a drop, a swap, a reworded
predicate and a changed ``ondelete`` are all ruled out at once.
``bank_statement_line_table_args`` and ``statement_line_sighting_table_args``
carry X-f6b-1b's OWN schema edits in the same commit -- the line's
``fk_bank_statement_lines_merchant_account`` and
``idx_bank_statement_lines_account_merchant`` DROPPED (8 members to 6), the
sighting's ``fk_statement_line_sightings_merchant_account`` and
``idx_statement_line_sightings_account_merchant`` ADDED (8 to 10) -- and are
otherwise the same tuples; the AST equality for those two was measured
against the edited class bodies immediately before the move, so the MOVE
changed nothing and the schema change is the model's (rulings **R-BI16**,
**R-BI17**).  *A first draft of this paragraph claimed all four were
verbatim, measured against the working copy rather than the parent commit;
named by adversarial review 2026-09-18.*

The argument for each key, constraint and index stays WITH it, in the comments
below.  The models that read these values:
:class:`~app.models.statement_import.AccountExternalIdentity`,
:class:`~app.models.statement_import.StatementImport`,
:class:`~app.models.statement_import.BankStatementLine` and
:class:`~app.models.statement_import.StatementLineSighting`.
"""

from app.extensions import db


account_external_identity_table_args = (
    # One external account maps to at most ONE of THIS OWNER'S accounts.
    # The arm that makes importing the card's export into Checking
    # refusable by the DATABASE rather than by a reviewer noticing.
    #
    # **Scoped by owner, and that is not decoration.**  A GLOBAL key over
    # ``(source_id, external_account_id)`` is wrong on a low-entropy value:
    # this adapter's identifier is SECU's MASK (``******3820``), so two
    # owners at one credit union collide on the last four digits with
    # probability 1/10,000 per pair -- and the loser could never import
    # their own statements, while the refusal would disclose that some
    # other account in the system had claimed their number.  Per owner, the
    # only row you can collide with is your own, which is a fact you are
    # entitled to be told about.
    db.UniqueConstraint(
        "user_id", "source_id", "external_account_id",
        name="uq_account_external_identities_owner_source_account",
    ),
    # ...and one Shekel account has at most one identity per source, so
    # "what does this source call this account" has exactly one answer.
    db.UniqueConstraint(
        "account_id", "source_id",
        name="uq_account_external_identities_account_source",
    ),
    # This row's owner IS its account's, guaranteed rather than maintained
    # -- the construction ``fk_transactions_owner_account`` uses,
    # keyed onto ``uq_accounts_id_user``.  Without it ``user_id`` would be
    # a copy some writer has to keep in step, and the uniqueness above
    # would be scoped by a column that could be set wrong.
    db.ForeignKeyConstraint(
        ["account_id", "user_id"],
        ["budget.accounts.id", "budget.accounts.user_id"],
        name="fk_account_external_identities_owner",
        ondelete="CASCADE",
    ),
    {"schema": "budget"},
)


statement_import_table_args = (
    # The superkey a composite foreign key needs as its target, so
    # ``fk_statement_line_sightings_import_account`` can hold a
    # sighting's account equal to its import's.  It constrains nothing on
    # its own (``id`` is already the primary key).
    db.UniqueConstraint(
        "id", "account_id", name="uq_statement_imports_id_account",
    ),
    # The superkey a bank LEVEL's amount keys onto
    # (``fk_anchor_history_statement_import_claim``, plan step
    # ``balance:X-bj-1``).  It constrains nothing on its own (``id`` is the
    # primary key); it exists because PostgreSQL requires a UNIQUE over
    # exactly the referenced columns.  ``stated_balance`` is nullable, and
    # that is load-bearing: a NULL in a referenced column matches no
    # referencing row, so an import that states no balance can own no
    # level -- the rule ``ck_statement_imports_anchor_needs_a_claim`` used
    # to state in words, now a property of the key.
    db.UniqueConstraint(
        "id", "stated_balance",
        name="uq_statement_imports_id_stated_balance",
    ),
    db.CheckConstraint(
        "declared_end >= declared_start",
        name="ck_statement_imports_declared_ordered",
    ),
    # The file's CLAIM is one fact in two columns.  A figure without its
    # day asserts nothing about an account, and a day without a figure
    # asserts nothing at all.  What the import MADE of the claim -- the
    # day it is the balance for, and how firmly -- is a level row's
    # (plan step ``balance:X-bj-1``); the two CHECKs that paired those
    # columns here are that row's NOT NULL shape now, and the one that
    # bounded the solved day within the file is
    # ``budget.level_lies_within_file`` on both tables.  A date-range
    # export still records its claim and owns no level: the developer's
    # 2026-01-02..2026-03-31 file, pulled 2026-08-23, states `$2,459.60`
    # as of 08-23, 145 days past its last line and `$255.41` from the
    # `$2,715.01` its own 139 lines imply.
    db.CheckConstraint(
        "(stated_balance IS NULL) = (stated_balance_on IS NULL)",
        name="ck_statement_imports_stated_balance_paired",
    ),
    db.Index("idx_statement_imports_account", "account_id"),
    {"schema": "budget"},
)


bank_statement_line_table_args = (
    # The SUPERKEY ``statement_match_members`` and
    # ``statement_line_sightings`` name to prove their own ``account_id``
    # is this line's (plan step ``bank_import:X-f6a-2``).  It constrains
    # nothing -- ``id`` is already the primary key, so this key can reject
    # no row -- and exists only because PostgreSQL requires a UNIQUE over
    # exactly the referenced columns before a composite foreign key may
    # target them.
    db.UniqueConstraint(
        "id", "account_id", name="uq_bank_statement_lines_id_account",
    ),
    # THE IDENTITY.  Re-importing an overlapping span cannot duplicate a
    # line, structurally rather than by the importer remembering to check.
    db.UniqueConstraint(
        "account_id", "posted_on", "amount", "sequence_in_group",
        name="uq_bank_statement_lines_identity",
    ),
    db.CheckConstraint(
        "sequence_in_group >= 0",
        name="ck_bank_statement_lines_sequence_non_negative",
    ),
    # A statement line MOVES money, and its figure is a REAL number.
    # ``docs/coding-standards.md`` requires a CHECK on every financial
    # column; the adapter's refusal of a line stating no amount is the
    # Python half of the same rule.
    #
    # **The ``< 'NaN'`` term is the part that is not obvious, and a first
    # draft of this constraint got it wrong.**  PostgreSQL's ``numeric``
    # accepts ``NaN`` and orders it ABOVE every real number, so
    # ``NaN <> 0`` is TRUE and ``NaN = NaN`` is TRUE -- a plain non-zero
    # test admits it.  Since NaN sorts greatest, ``x < 'NaN'`` is true for
    # every real value and false for NaN itself, which is what makes a NaN
    # amount unrepresentable rather than merely unreached.  It matters
    # because a NaN amount compares equal to nothing (invisible to every
    # matcher), poisons ``SUM()`` over the account, and raises inside the
    # money display macro -- so the page 500s on every later load.  The
    # running balance carries the same term on its own table.
    db.CheckConstraint(
        "amount <> 0 AND amount < 'NaN'::numeric",
        name="ck_bank_statement_lines_amount_real_nonzero",
    ),
    # The walk reads a whole account in posted-day order.
    db.Index(
        "idx_bank_statement_lines_account_day",
        "account_id", "posted_on",
    ),
    {"schema": "budget"},
)


statement_line_sighting_table_args = (
    # THE identity of a sighting: one per import per line.  A re-import
    # of a file the app has already recorded writes one row per line here
    # and nothing on the line, and this key is what makes writing it twice
    # in one act impossible rather than checked.
    db.UniqueConstraint(
        "line_id", "import_id",
        name="uq_statement_line_sightings_line_import",
    ),
    # This sighting's account IS its line's, guaranteed rather than
    # maintained -- keyed onto ``uq_bank_statement_lines_id_account``.
    # CASCADE: a line that goes takes what was said about it.
    db.ForeignKeyConstraint(
        ["line_id", "account_id"],
        ["budget.bank_statement_lines.id",
         "budget.bank_statement_lines.account_id"],
        name="fk_statement_line_sightings_line_account",
        ondelete="CASCADE",
    ),
    # ...and its import's, keyed onto ``uq_statement_imports_id_account``.
    # CASCADE: deleting an import withdraws everything it said, and the
    # line survives exactly when another import still says something.
    db.ForeignKeyConstraint(
        ["import_id", "account_id"],
        ["budget.statement_imports.id",
         "budget.statement_imports.account_id"],
        name="fk_statement_line_sightings_import_account",
        ondelete="CASCADE",
    ),
    # The lookup the record door makes before it pairs: where THIS SOURCE
    # already holds each id the file states (``_plan._held_ids``).
    # Partial, because most adapters carry no external id.  **Not
    # unique, and the line-level index it replaces was**: a re-import
    # re-sights a line under the same id, which is two rows here and one
    # line there.  The rule that a source names ONE line per id is the
    # door's (``_plan._refuse_moved_ids``), pinned by its tests.
    db.Index(
        "idx_statement_line_sightings_account_external_id",
        "account_id", "external_id",
        postgresql_where=db.text("external_id IS NOT NULL"),
    ),
    # This sighting's merchant is one of THIS ACCOUNT's, structurally
    # (ruling **R-BI16**; the shape ``fk_bank_statement_lines_merchant_
    # account`` had on the line from ``bank_import:X-gd-1``).  ``MATCH
    # SIMPLE`` (PostgreSQL's default) is what lets it sit on a nullable
    # column -- a sighting whose ``merchant_id`` is NULL satisfies it
    # whatever ``account_id`` says, which is this source naming none.
    db.ForeignKeyConstraint(
        ["merchant_id", "account_id"],
        ["budget.merchants.id", "budget.merchants.account_id"],
        name="fk_statement_line_sightings_merchant_account",
    ),
    # Every per-import count reads this way: the sightings one import
    # wrote, the lines it alone holds, the merchants its sightings name.
    db.Index("idx_statement_line_sightings_import", "import_id"),
    # Every per-ACCOUNT derivation reads this way (the first and sole
    # import of each line, the counts, the record door's id lookup), and
    # the two composite keys index nothing of their own.  Trivial at one
    # CSV's 306 rows; the daily feed writes a row per line per sync.
    db.Index(
        "idx_statement_line_sightings_account_line",
        "account_id", "line_id",
    ),
    # Which merchants an account's sightings name, grouped: what a delete
    # would orphan (``statement_import._reads.orphan_merchants_by_import``)
    # and the downgrade's restore of the line's key.  Partial, because a
    # NULL merchant keys nothing and is never looked up by this column
    # (the shape ``idx_bank_statement_lines_account_merchant`` had).
    db.Index(
        "idx_statement_line_sightings_account_merchant",
        "account_id", "merchant_id",
        postgresql_where=db.text("merchant_id IS NOT NULL"),
    ),
    # The stated figure is a REAL number or absent, the same term the
    # line's own amount carries and for the same reason (see
    # ``ck_bank_statement_lines_amount_real_nonzero``).
    db.CheckConstraint(
        "running_balance IS NULL OR running_balance < 'NaN'::numeric",
        name="ck_statement_line_sightings_running_balance_real",
    ),
    {"schema": "budget"},
)
