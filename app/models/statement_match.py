"""
Shekel Budget App -- Statement Match Models (budget schema)

WHICH of the app's own rows a recorded bank line IS.  Three tables, one subject
(plan step ``bank_import:X-f6a-2``, rulings **R-FS** and **R-FV**):

  * :class:`StatementMatch` -- one act of matching, reviewed and accepted by
    the owner.
  * :class:`StatementMatchMember` -- one thing that act names: a bank line or
    a movement.
  * :class:`StatementMatchCreation` -- one thing that act brought into
    EXISTENCE, which is not the same set (plan step ``bank_import:X-f6f``,
    ruling **R-GG**).

**The relation is IDENTITY, and identity is the fact this arc was missing.**
``transactions.reconciled_by_id`` (ruling **R-FL**) answers a different
question -- *which declared balance already contains this row* -- and that is
DERIVABLE from identity once a statement carries the line, where identity is
not derivable from absorption.  So an accepted match stores the identity and
nothing else, and ruling **R-FV** is that sentence: a match does not write
``reconciled_by_id``, it only causes the settle day to move, and the doors
already RELEASE the link when a day moves.  Plan step ``balance:X-f3c`` is
where ``StatementCoverage`` starts reading this relation instead, and
``balance:X-f4`` deletes the column it replaces.

**A group is why this is two tables rather than a join column.**  Ruling
**R-FS** measured the grain mismatch running in BOTH directions on the
developer's own accounts: one payroll deposit against three app rows, and one
envelope against N card swipes.  Neither direction fits a foreign key on
either row, and both are the SAME fact -- *this set of bank lines and this set
of app rows are one movement* -- so the act is the table and the things it
names are its members.

**What a match ASSERTS, and the one thing that is checked at the door rather
than by the schema.**  The signed sum of the member lines equals the signed sum
of the member rows: that is what makes the group a movement rather than a
guess.  It is a cross-row aggregate over two tables, so no ``CHECK`` can carry
it; :func:`app.services.statement_match.accept_match` refuses an unbalanced
group and its refusal names the difference.  That refusal is not a formality --
measured on the developer's own statement, 6 of 16 payroll deposits sit
`$0.05`-`$0.06` ABOVE what the app's rows sum to, which is finding **N-391**
seen from the outside.  *The direction was written the wrong way round until
plan step balance:X-aw re-measured it -- the BANK pays more, which is why the
owner has been hand-typing the net since 2026-07-02 -- and the finding was
**N-239** until that step split the horizon half off from this one.*

**Agreement is DERIVED, never stored, and a stale match is not a corrupt one.**
Nothing here records the day the match asserted: that is ``max(posted_on)`` over
the member lines, and each member row carries it in its own ``settled_on``.  A
user who later moves a day by hand puts the group out of agreement with the
bank, and the review screen's own
:class:`~app.services.statement_match.AcceptedGroup` carries an ``agrees`` flag
that SHOWS it rather than a release nobody can see -- the repair door finding
**N-302** says a refusal owes.
"""

from app.extensions import db
from app.models.mixins import (
    AccountScopedMixin,
    CreatedAtMixin,
    UserScopedMixin,
)


class StatementMatch(AccountScopedMixin, UserScopedMixin, CreatedAtMixin,
                     db.Model):
    """One accepted act of matching: these bank lines ARE these app rows.

    Ruling **R-FP**: *a match is a PROPOSAL, never a silent apply*.  A row here
    exists only because a human reviewed a proposal and accepted it, which is
    why the act is a table at all -- a derived correspondence would need no
    record of who agreed to it.

    Columns:
        account_id -- the account both sides belong to
            (:class:`~app.models.mixins.AccountScopedMixin`).  Every member is
            held to it by a composite key, so a group spanning two accounts is
            unrepresentable rather than untested: a match is a claim about ONE
            bank's record of ONE account.
        user_id -- who accepted it (:class:`~app.models.mixins.UserScopedMixin`),
            held equal to the account's owner by
            ``fk_statement_matches_owner`` so it is a co-located key rather
            than a copy.  The same construction
            ``fk_account_external_identities_owner`` uses.
        created_at -- when (:class:`~app.models.mixins.CreatedAtMixin`).  A
            match is never edited: correcting one is deleting it and matching
            again, which is why there is no ``updated_at``.
        applied_by_rule -- whether a STANDING RULE performed this act rather
            than the owner ticking it (ruling **R-GT**, plan step
            ``bank_import:X-gd-2``).  See below.

    **It stores no amount and no day**, and both absences are the rule rather
    than an omission.  The amount is the sum of the member lines and the day is
    their latest ``posted_on``; storing either would put a derived value beside
    its source with nothing reconciling them, which is the root cause three of
    this project's arcs exist to remove.

    **...and it stores WHO CONSENTED, because that one is not derivable**
    (ruling **R-GT**).  A standing rule is the owner's consent given once
    (ruling **R-GH**), so an act performed under one is still consented to --
    but it is a different fact from a tick, and the receipt an application owes
    (ruling **R-GI**) has to be able to say which.

    **WHICH rule is deliberately NOT stored**, and that is the same argument
    the two absences above make.  The matched line carries the account and the
    merchant, which is exactly ``budget.merchant_rules``' key, so a foreign key
    to the rule row would store a pointer the line already determines -- and it
    would force a choice none of whose arms is right: ``CASCADE`` deletes money
    records when a rule is restated away, ``SET NULL`` claims the owner ticked
    it, and ``RESTRICT`` refuses to change a rule that ever fired, which is the
    dead end finding **N-302** records one arc over.  What is left over -- that
    a rule acted rather than a person -- is not derivable from anything, because
    a person may tick a line whose merchant has a rule.  That is the whole of
    what this column holds.

    **NOT NULL with no default.**  The writer states it or the flush refuses
    (:func:`~app.services.statement_match._accept._record`).  A default of
    ``false`` would be correct for every row that exists today and would still
    be wrong, because the value it supplies is *the owner agreed to this*.

    **Every row is ``false`` until plan step ``bank_import:X-ge``**, which
    builds the door that applies a rule at import; measured 221 acts on the
    developer's dev database, all of them ticks.  The column ships one leaf
    ahead of that door on purpose, so the step that MOVES MONEY carries no
    schema change and the consent boundary can be reviewed before it does.
    """

    __tablename__ = "statement_matches"
    __table_args__ = (
        # The SUPERKEY the members name so their own ``account_id`` is this
        # act's.  It constrains nothing -- ``id`` is already the primary key --
        # and exists only because PostgreSQL requires a UNIQUE over exactly the
        # referenced columns before a composite foreign key may target them.
        # The same construction, for the same reason, as
        # ``uq_transactions_id_account``.
        db.UniqueConstraint(
            "id", "account_id", name="uq_statement_matches_id_account",
        ),
        # This act's owner IS its account's, guaranteed rather than maintained.
        db.ForeignKeyConstraint(
            ["account_id", "user_id"],
            ["budget.accounts.id", "budget.accounts.user_id"],
            name="fk_statement_matches_owner",
            ondelete="CASCADE",
        ),
        db.Index("idx_statement_matches_account", "account_id"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    applied_by_rule = db.Column(db.Boolean, nullable=False)

    members = db.relationship(
        "StatementMatchMember", back_populates="match",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    creations = db.relationship(
        "StatementMatchCreation", back_populates="match",
        cascade="all, delete-orphan", passive_deletes=True,
    )

    def __repr__(self):
        return f"<StatementMatch account={self.account_id} ({self.id})>"


class StatementMatchMember(db.Model):
    """One thing a match names -- a bank line or a movement.

    **An EXCLUSIVE ARC of two typed foreign keys**, the shape plan step
    ``balance:X-ai-s`` states for ``journal_entries`` and
    ``template_amount_versions`` already carries: exactly one of the two is
    set, and ``ck_statement_match_members_one_subject`` is what says so.  A
    single polymorphic ``(kind, id)`` pair would be a foreign key the database
    cannot check, on a table whose whole job is to say that two real rows are
    the same movement.

    **The app's side is always a MOVEMENT** (plan steps
    ``credit_card:CC-5-4a-1`` / ``CC-5-4a-2``, rulings **R-CC43** and
    **R-CC45**): a person's purchase, or the covering movement a settle wrote
    for a bill, a paycheck or a transfer leg -- which is what a settled row's
    money IS (ruling **R-BAL80**), on whichever account it moved through.
    The arc had a third arm, the ROW itself, until migration ``2eabfa596ee0``
    re-keyed every row member onto its row's covering movement and dropped
    the column: a row member held its act to the ROW's account, where a
    card-paid bill's money never was, and a definition's account move over a
    matched Projected row raised at that key (finding **CC-356**).  A row is
    a candidate only while it is Projected, and a match settles it through
    its own door and records the payment that settle wrote
    (:func:`~app.services.statement_match._accept._as_recorded`).

    Columns:
        match_id -- the act this membership belongs to.
        account_id -- the account, held equal to the act's by
            ``fk_statement_match_members_match_account`` and to the SUBJECT's
            by whichever of the two composite keys below applies.  That is
            what makes a match to another account's movement unwritable rather
            than merely unoffered -- the same reason
            ``fk_transactions_reconciled_by`` is composite (ruling **R-FL**).
        bank_statement_line_id -- the bank's line, when this member is one.
        transaction_entry_id -- the app's movement, when this member is one.

    **Whichever of the two the arc carries is reachable as :attr:`line` or
    :attr:`entry`**, joined on the composite key so the account equality
    travels IN the join.  A reader that holds a member of an
    act it has proved the owner's therefore cannot reach another account's row
    through one; see the relationships themselves for what that replaced.

    **What an act CREATED is a DIFFERENT relation and lives in
    :class:`StatementMatchCreation`** (plan step ``bank_import:X-f6f``,
    developer ruling **R-GG**).  It was a ``created_version_id`` column here
    until then, which worked while the only created subject was a group's
    residual -- a row the act also NAMES.  The create-a-purchase arm creates
    two things, a purchase it names and often the budget line that HOLDS the
    purchase, and a container is not a member: naming it would claim the same
    money twice and would break ``Sigma(lines) = Sigma(members)``, and no act
    can.  A row holding a purchase is never offered: while Projected it is
    worth ``$0.00`` to the matcher's offer
    (:func:`~app.services.statement_match._settle_price.settle_price`,
    ruling **R-BAL81**), and closed from its purchases it holds no payment to
    name.  A column on this table therefore had nowhere to put the one
    subject the undo most needed to reach.

    **Each subject belongs to at most ONE match, and that is structural.**  The
    two partial unique indexes below are what make "already matched" a
    question the database answers: without them a second review pass could
    explain one bank line twice, and the two acts would each look complete.

    **NEITHER key cascades** (plan step ``credit_card:CC-5-4a-4``, ruling
    **R-CC54**: "A match's key to its payment or purchase stops cascading,
    like its key to the bank line").  **The MOVEMENT key CASCADED until
    migration ``c4a4e7d1b9f2``**, on the argument that refusing would refuse
    an ordinary delete over a record the user cannot see from the row (the
    dead end finding **N-302** records one table over) -- and what that left
    was a door that could empty an act without the act knowing: the template,
    account and pay-period deletes destroyed a named movement by cascade and
    left the act holding its bank line alone, which read as explained forever
    until a predicate in the one read that decides stopped counting it
    (finding **CC-363**).  Both ends are closed now, so there is nothing for
    that predicate to catch and it is deleted: an ordinary delete never
    reaches this key, because the ONE act that takes a movement off the books
    (:mod:`app.services.movement_removal`) takes it out of every match first
    -- withdrawing an act left naming no app row and saying which line it
    freed, or leaving a group smaller for its ``agrees`` flag
    (:class:`~app.services.statement_match.AcceptedGroup`) to report -- and
    the doors that deleted rows as a side effect keep a row holding a
    movement instead.  So an act naming only lines is unrepresentable rather
    than filtered, and the key refusing is a defect's loud failure, never a
    user's dead end.

    **The BANK-LINE key has never cascaded, and it is where the reasoning
    started** (plan step ``bank_import:X-f6a-4``).  Losing app rows was
    VISIBLE; losing bank lines was not.  A match with no line left asserts nothing about
    a bank, so :func:`~app.services.statement_match._accepted_view
    .accepted_groups` could not render it and no release button could ever
    exist for it -- while ``matched_subjects`` reads the members directly and
    went on reporting its app rows as already matched, so those rows could
    never be offered or matched again.  MEASURED on a production clone
    2026-08-20: deleting one import took 361 lines and left the act standing
    with 0 line members and 1 app-row member.  Nothing reached that state
    before, because nothing deleted a line; X-f6a-4's repair door is what would
    have made it reachable, so the door RELEASES a match before it removes the
    lines and the database refuses to orphan one either way.  A whole-account
    delete is unaffected: its cascade removes the members with the match inside
    the same statement, which a ``NO ACTION`` check tolerates and ``RESTRICT``
    would not -- verified against a production clone before the constraint was
    changed.
    """

    __tablename__ = "statement_match_members"
    __table_args__ = (
        # THE EXCLUSIVE ARC: exactly one subject.  Summing the NULL tests is
        # the spelling ``ck_statement_match_creations_one_subject`` uses for
        # its own two columns, kept when the third went (migration
        # ``2eabfa596ee0``) so the two tables state the rule alike.
        db.CheckConstraint(
            "(bank_statement_line_id IS NOT NULL)::int "
            "+ (transaction_entry_id IS NOT NULL)::int = 1",
            name="ck_statement_match_members_one_subject",
        ),
        # This member's account IS its act's.
        db.ForeignKeyConstraint(
            ["match_id", "account_id"],
            ["budget.statement_matches.id", "budget.statement_matches.account_id"],
            name="fk_statement_match_members_match_account",
            ondelete="CASCADE",
        ),
        # ...and IS its subject's, for whichever of the two it carries.
        # ``MATCH SIMPLE`` (PostgreSQL's default) is what lets these two sit
        # beside one another: a member whose ``bank_statement_line_id`` is NULL
        # satisfies the line key whatever ``account_id`` says.
        # **No ``ondelete`` -- the default NO ACTION, deliberately.**  The
        # database refuses to remove a bank line while a match names it, so a
        # match that has lost its lines (invisible on the register that lists
        # accepted acts, and permanently blocking its app rows) is
        # unrepresentable rather than
        # merely unproduced.  NO ACTION rather than RESTRICT because the check
        # is then deferred to the end of the statement, which is what lets a
        # whole-account delete cascade the members away and the lines with them
        # in one go; RESTRICT is checked per row and would refuse it.  See the
        # class docstring for the measurement.
        db.ForeignKeyConstraint(
            ["bank_statement_line_id", "account_id"],
            ["budget.bank_statement_lines.id",
             "budget.bank_statement_lines.account_id"],
            name="fk_statement_match_members_line_account",
        ),
        # The movement key, NO ACTION for the line key's reasons (the class
        # docstring; ``CASCADE`` until migration ``c4a4e7d1b9f2``, ruling
        # **R-CC54**).
        db.ForeignKeyConstraint(
            ["transaction_entry_id", "account_id"],
            ["budget.transaction_entries.id",
             "budget.transaction_entries.account_id"],
            name="fk_statement_match_members_entry_account",
        ),
        # One subject, at most one match.  Partial, because one of the two
        # columns is NULL on every row and a NULL is not a claim.
        db.Index(
            "uq_statement_match_members_line", "bank_statement_line_id",
            unique=True,
            postgresql_where=db.text("bank_statement_line_id IS NOT NULL"),
        ),
        db.Index(
            "uq_statement_match_members_entry", "transaction_entry_id",
            unique=True,
            postgresql_where=db.text("transaction_entry_id IS NOT NULL"),
        ),
        # The reader loads a whole act at once.
        db.Index("idx_statement_match_members_match", "match_id"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # No direct single-column keys: every relationship above is reached through
    # a composite key that also holds the account equal.  Same shape, same
    # reason, as ``bank_statement_lines.account_id``.
    match_id = db.Column(db.Integer, nullable=False)
    account_id = db.Column(db.Integer, nullable=False)
    bank_statement_line_id = db.Column(db.Integer)
    transaction_entry_id = db.Column(db.Integer)

    match = db.relationship(
        "StatementMatch", back_populates="members",
        foreign_keys=[match_id, account_id],
    )
    # **THE SUBJECT, reached through the COMPOSITE key, which is what puts the
    # account in the JOIN rather than in a filter a reader has to remember**
    # (finding **bank_import:N-358**, plan step ``bank_import:X-gf-2``).  Each
    # of these emits
    # ``ON subject.id = member.<id> AND subject.account_id = member.account_id``
    # -- so a reader holding a member of an act it has proved the owner's
    # cannot reach another account's row through one, whatever it forgets.
    # The readers used to collect the ids and SELECT them back by primary key
    # alone; nothing leaked, because every id came from a scoped act, but that
    # is safety by DERIVATION over an open set of future callers, which is the
    # ground N-353 was refused on one function away.
    #
    # ``viewonly`` because the WRITER states the id columns
    # (:func:`~app.services.statement_match._accept._record`): these are a read
    # projection of a key the database already holds, and a second, writable
    # path to the same column pair is exactly the drift this arc keeps
    # removing.  It is also what keeps SQLAlchemy from trying to manage one
    # ``account_id`` through three overlapping relationships.
    line = db.relationship(
        "BankStatementLine",
        foreign_keys=[bank_statement_line_id, account_id],
        viewonly=True,
    )
    entry = db.relationship(
        "TransactionEntry",
        foreign_keys=[transaction_entry_id, account_id],
        viewonly=True,
    )

    def __repr__(self):
        subject = (
            f"line={self.bank_statement_line_id}"
            if self.bank_statement_line_id is not None
            else f"entry={self.transaction_entry_id}"
        )
        return f"<StatementMatchMember match={self.match_id} {subject}>"


class StatementMatchCreation(db.Model):
    """One app row a match act brought into EXISTENCE, and at which revision.

    Plan step ``bank_import:X-f6f``, developer ruling **R-GG** (2026-08-24).
    **What an act NAMES and what an act MAKES are two relations**, and
    conflating them is what left the create-a-purchase arm without an inverse
    (findings **N-333** and **N-340**).  A match's MEMBERS are the things it
    asserts are one movement, so their signed amounts must add up; the rows it
    CREATED are what an undo has to take back, and they are not the same set in
    either direction:

    * a group's residual is BOTH -- created, and named, so it closes the gap
      (ruling **R-FN**);
    * a purchase recorded from a bank line is BOTH;
    * the ENVELOPE that purchase went into, when the act minted one, is
      created and NOT named -- naming an envelope beside its own purchase
      counts the same money twice (ruling **R-FM**), and no act can: a row
      holding a purchase is never offered (:class:`StatementMatchMember`
      says why), so the accept door's refusal of that pairing was deleted as
      unreachable (plan step ``credit_card:CC-5-4a-5``, finding **CC-386**,
      ruling **R-CC144**).

    So the fact lived on ``statement_match_members.created_version_id`` while
    the residual was the only created subject, and had nowhere to put the
    container.  It is its own table now, and the member column is dropped.

    Columns:
        match_id -- the act that created this row.
        account_id -- the account, held equal to the act's by
            ``fk_statement_match_creations_match_account`` and to the SUBJECT's
            by whichever of the two composite keys applies.  The same
            construction, for the same reason, as
            :class:`StatementMatchMember`'s.
        transaction_id -- the budget row, when the subject is one.
        transaction_entry_id -- the purchase, when the subject is one.
        created_version_id -- the subject's ``version_id`` as this act left it.
            NOT NULL: a row here IS a creation, so there is no "already
            existed" state left for a NULL to mean.

    Whichever of the two the arc carries is reachable as :attr:`transaction` or
    :attr:`entry`, joined on the composite key exactly as
    :class:`StatementMatchMember`'s two are.

    **The revision is the whole predicate, and that is why it is a version
    rather than a flag.**  "Still has no category and still holds the figure we
    recorded and still has no purchases" is three guesses about which edits
    matter; a counter that moves on every ORM update is the fact itself.  It
    also covers what nothing else would: a row nothing edited cannot have grown
    a CC payback either, because ``mark_as_credit`` writes the source row's own
    status.

    **A SUBJECT and a CONTAINER are told apart by MEMBERSHIP, not by a column
    here.**  A creation whose subject is also a member of the same act is what
    the act is ABOUT, so an undo removes it and REFUSES where the owner has
    edited it since; a creation that is not a member is a container, so an undo
    removes it only when nothing is left in it and nothing has touched it, and
    otherwise leaves it standing without refusing.  Deriving that from the
    members is exact -- they are the one statement of what the act names -- and
    a stored copy of it would be a second answer to a question this schema
    already answers.

    **There is no BANK LINE arm, and its absence is the constraint.**  A match
    act cannot bring a line into existence: an import does that, and the line
    is what the act is ABOUT.  The old column needed a CHECK to say so; here it
    is unspellable.

    **A subject is created by at most ONE act**, which the two partial unique
    indexes make structural.  Two acts each claiming to have minted one row
    would each offer to remove it, and the second would find it gone.

    **The subject keys CASCADE, and unlike the MEMBER keys they still do**
    (plan step ``credit_card:CC-5-4a-4`` flipped the member's movement key to
    NO ACTION, ruling **R-CC54**).  A member is a CLAIM that a bank line is
    this movement, so losing one silently is a false statement about the
    bank; a creation is only a record of what an undo would take back, so a
    subject the owner deletes themselves -- through the one act for a
    movement, through the row's own door for an emptied container -- takes
    its creation record with it, and the undo has nothing to remove and
    nothing to refuse.  Refusing that delete because of a record the user
    cannot see from the row is the dead end finding **N-302** records one
    table over.
    """

    __tablename__ = "statement_match_creations"
    __table_args__ = (
        # THE EXCLUSIVE ARC: exactly one subject.  Summing the NULL tests is
        # the spelling ``ck_statement_match_members_one_subject`` uses.
        db.CheckConstraint(
            "(transaction_id IS NOT NULL)::int "
            "+ (transaction_entry_id IS NOT NULL)::int = 1",
            name="ck_statement_match_creations_one_subject",
        ),
        db.CheckConstraint(
            "created_version_id > 0",
            name="ck_statement_match_creations_version_positive",
        ),
        # This creation's account IS its act's.
        db.ForeignKeyConstraint(
            ["match_id", "account_id"],
            ["budget.statement_matches.id",
             "budget.statement_matches.account_id"],
            name="fk_statement_match_creations_match_account",
            ondelete="CASCADE",
        ),
        # ...and IS its subject's.  ``MATCH SIMPLE`` (PostgreSQL's default) is
        # what lets the two sit beside one another: a creation whose
        # ``transaction_id`` is NULL satisfies that key whatever
        # ``account_id`` says.
        db.ForeignKeyConstraint(
            ["transaction_id", "account_id"],
            ["budget.transactions.id", "budget.transactions.account_id"],
            name="fk_statement_match_creations_transaction_account",
            ondelete="CASCADE",
        ),
        db.ForeignKeyConstraint(
            ["transaction_entry_id", "account_id"],
            ["budget.transaction_entries.id",
             "budget.transaction_entries.account_id"],
            name="fk_statement_match_creations_entry_account",
            ondelete="CASCADE",
        ),
        # One subject, at most one act that made it.  Partial, because one of
        # the two columns is NULL on every row and a NULL is not a claim.
        db.Index(
            "uq_statement_match_creations_transaction", "transaction_id",
            unique=True,
            postgresql_where=db.text("transaction_id IS NOT NULL"),
        ),
        db.Index(
            "uq_statement_match_creations_entry", "transaction_entry_id",
            unique=True,
            postgresql_where=db.text("transaction_entry_id IS NOT NULL"),
        ),
        # The reader loads a whole act at once.
        db.Index("idx_statement_match_creations_match", "match_id"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # No direct single-column keys: every relationship above is reached through
    # a composite key that also holds the account equal.  Same shape, same
    # reason, as ``statement_match_members``'.
    match_id = db.Column(db.Integer, nullable=False)
    account_id = db.Column(db.Integer, nullable=False)
    transaction_id = db.Column(db.Integer)
    transaction_entry_id = db.Column(db.Integer)
    created_version_id = db.Column(db.Integer, nullable=False)

    match = db.relationship(
        "StatementMatch", back_populates="creations",
        foreign_keys=[match_id, account_id],
    )
    # The same two-column join, for the same reason, as
    # :class:`StatementMatchMember`'s two -- and here it does one more thing.
    # The undo reached each subject with ``db.session.get`` and paid 478
    # queries folding 230 acts, so a bulk reader had to WARM the identity map
    # first and then HOLD the result, SQLAlchemy's identity map being weak.  A
    # relationship is loaded by the same eager option as the rest of the act
    # and is held by the act itself, so neither the warm nor the holding is a
    # thing a caller can forget.
    transaction = db.relationship(
        "Transaction",
        foreign_keys=[transaction_id, account_id],
        viewonly=True,
    )
    entry = db.relationship(
        "TransactionEntry",
        foreign_keys=[transaction_entry_id, account_id],
        viewonly=True,
    )

    def __repr__(self):
        subject = (
            f"txn={self.transaction_id}"
            if self.transaction_id is not None
            else f"entry={self.transaction_entry_id}"
        )
        return (
            f"<StatementMatchCreation match={self.match_id} {subject} "
            f"v{self.created_version_id}>"
        )
