"""
Shekel Budget App -- Statement Import Models (budget schema)

What a BANK said, recorded as fact.  Four tables, one subject (plan step
``bank_import:X-f6a``, ruling **R-FP**; the fourth at ``bank_import:X-f6b-1``,
ruling **R-BI10**):

  * :class:`AccountExternalIdentity` -- which account at a SOURCE is which
    account here.  The one-time mapping R-FP calls "a fact, not a guess".
  * :class:`StatementImport` -- one act of importing, and the window it
    declares it answers for.
  * :class:`BankStatementLine` -- one line the bank showed: the fact every
    source agrees on.
  * :class:`StatementLineSighting` -- what ONE import said about ONE line.
    A line is held by the sightings of the imports that showed it, and lives
    while any sighting does.

**None of them moves a figure, and that is the leaf boundary rather than a
coincidence.**  Recording what a statement said is separable from deciding
which of the app's own rows it explains, so this leaf lands with no matcher, no
``settled_on`` correction and no clearing link -- the same discipline that made
``X-au-c1`` and ``X-f3a-1`` provably balance-neutral.  What the recorded lines
are FOR is the leaves after it: the match and its review (``X-f6a-2``), the
purchase a bank line becomes (``X-f6a-3b``), the walked-statement silence that
makes an unshown line NOT CLEARED (``balance:X-f3a-2``), and the re-openable
recorded difference at the cutover (``balance:X-f3c``).

Sign convention, stated once: :attr:`BankStatementLine.amount` is SIGNED and
positive means money ENTERING the account, matching
``cash_ledger.movement_cash_leg`` exactly so a later match compares two figures
that already agree about direction.  Both of the developer's sources use that
convention natively (OFX ``TRNAMT``, and the CSV's Credit / Debit pair), so no
adapter has to invert anything.
"""

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import column_property

from app.extensions import db
from app.models._statement_import_table_args import (
    account_external_identity_table_args,
    bank_statement_line_table_args,
    statement_import_table_args,
    statement_line_sighting_table_args,
)
from app.models.merchant import Merchant
from app.models.mixins import (
    AccountScopedMixin,
    CreatedAtMixin,
    UserScopedMixin,
)


class AccountExternalIdentity(AccountScopedMixin, UserScopedMixin,
                              CreatedAtMixin, db.Model):
    """Which account at a SOURCE is this Shekel account.

    Ruling **R-FP**: the importer "needs a one-time account mapping -- the
    export's ``ACCTID`` to the Shekel account -- and that mapping is a fact, not
    a guess."  This is where the fact lives.

    **It is RECORDED by the user's own choice and then CHECKED, never
    inferred.**  On the first import the user says which account a file is for
    and the file's own identity is written here; on every import after it, the
    file's identity is compared against the recorded one and a disagreement
    REFUSES the import.  Inferring the account from the file instead would make
    a mis-typed export silently post one account's statement onto another --
    and the two sources cannot even be compared for equality, because SECU's
    CSV masks the account number (``******3820``) where its OFX spells it out
    (``40943820``).

    Columns:
        account_id  -- the Shekel account (from :class:`AccountScopedMixin`).
        user_id     -- its owner (from :class:`UserScopedMixin`), held equal to
                       the account's by ``fk_account_external_identities_owner``
                       so it is a co-located key rather than a copy.  It exists
                       because UNIQUENESS IS PER OWNER: see below.
        source_id   -- the adapter the identity was read by
                       (``ref.statement_sources``).
        external_account_id -- what that source calls the account.

    **The key is per SOURCE, not per institution, and that is deliberate
    honesty rather than a limitation accepted.**  SECU's CSV and its OFX are
    two adapters over one real account, and they would produce two rows here.
    Keying on the institution instead would require deciding that
    ``******3820`` and ``40943820`` name the same account -- which is a guess,
    which is exactly what this table exists not to make.  Two rows pointing at
    one ``account_id`` is the truthful record: each says what its own source
    calls this account.
    """

    __tablename__ = "account_external_identities"
    __table_args__ = account_external_identity_table_args

    id = db.Column(db.Integer, primary_key=True)
    source_id = db.Column(
        db.Integer,
        db.ForeignKey("ref.statement_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    external_account_id = db.Column(db.String(64), nullable=False)

    source = db.relationship("StatementSource", lazy="joined")

    def __repr__(self):
        return (
            f"<AccountExternalIdentity account={self.account_id} "
            f"external={self.external_account_id}>"
        )


class StatementImport(AccountScopedMixin, UserScopedMixin, CreatedAtMixin,
                      db.Model):
    """One act of importing a statement, and the window it DECLARES.

    The provenance every sighting points back to: who imported what, when,
    from which file, and which days the source said it was answering for.
    **A line is not owned by an import** (plan step ``bank_import:X-f6b-1``,
    ruling **R-BI10**): it is held by the SIGHTINGS of every import that
    showed it, so re-importing an overlapping span records a second sighting
    of each line it already holds and a second import whose window now also
    vouches for those days.

    Columns:
        account_id   -- the account the statement is for
                        (:class:`AccountScopedMixin`).
        user_id      -- who performed the import (:class:`UserScopedMixin`).
        source_id    -- the adapter that parsed the file.
        file_name    -- the uploaded file's own name, for the user to recognise
                        the import by.  Provenance only; nothing keys on it.
                        A feed sync's name is a label naming its window.
        file_digest  -- SHA-256 of the uploaded BYTES.  Also provenance: it
                        answers "is this the same file I imported before"
                        exactly, where the name cannot.  Deliberately NOT
                        unique -- re-uploading an identical file is a legal and
                        harmless act that records nothing new, and refusing it
                        would trade a truthful no-op for an error message.
        declared_start / declared_end -- the window the SOURCE declares this
                        import answers for (ruling **R-BAL71**): a CSV export
                        declares its first..last line day, because the file
                        states no range; a feed sync declares the window it
                        requested, so a quiet day inside it is covered and a
                        zero-line sync still covers what it asked for.  This
                        is what coverage reads
                        (``statement_import._balance.covered_runs``) and what
                        bounds a placed level (``budget.level_lies_within_file``).
                        *They were ``period_start`` / ``period_end`` -- the
                        line extremes, stored -- until X-f6b-1; for every
                        import recorded before it the extremes ARE what a CSV
                        declares, so the rename moved no value.*
        stated_balance / stated_balance_on -- what the file's OWN header claims
                        the account held, and the day it names.  A CLAIM, kept
                        verbatim and never rewritten.  Both-or-neither,
                        enforced by
                        ``ck_statement_imports_stated_balance_paired``.

    **``line_count`` and ``recorded_count`` were DELETED at X-f6b-1** (ruling
    **R-IY**: a derivable column is deleted, not maintained).  How many lines
    a file held is how many sightings this import wrote; how many were new is
    how many of those lines it was the FIRST to sight
    (:meth:`StatementLineSighting.first_import_of_each_line`); and the
    receipt still says both, computed by the door that knows them.  A stored
    count beside the rows it counts was a cache with no reconciler, and
    ``ck_statement_imports_line_count_positive`` went with it because a
    zero-line sync that states a balance is an observation, not a parse that
    found nothing.

    **The stated day is NOT the day the figure is for, and that is measured
    rather than defensive.**  SECU writes the balance as of the EXPORT INSTANT
    and labels it with the export's own day.  On the developer's 2026-08-21
    export the header reads ``Balance as of 08/21/2026,2501.310000`` while the
    file's last line is 08-18 and ``2501.31`` is 08-18's closing; on the
    2026-08-16 export it reads ``$4,747.63``, which is 2026-08-13's closing,
    over a file listing two 2026-08-14 lines worth ``-$1,006.72``.  The claim
    and the day it is FOR are therefore two facts, so the file's own words stay
    here and the solved day is a LEVEL.

    **What the import made of the claim is a row in the LEVEL RELATION, not
    two columns here** (plan step ``balance:X-bj-1``, rulings **R-IS** and
    **R-JN**).  ``balance_effective_on`` and ``balance_evidence_id`` lived on
    this row from ``bank_import:X-f6e-1`` until that step, and a release
    nulled them by UPDATE.  The bank's placement is an observation of the same
    quantity the owner's true-ups observe -- the account's balance at the
    close of a day -- so it is a :class:`~app.models.account.AccountAnchorHistory`
    row naming this import, its amount locked to :attr:`stated_balance` by
    ``fk_anchor_history_statement_import_claim`` over
    ``uq_statement_imports_id_stated_balance``, and its withdrawal an
    appended :class:`~app.models.anchor_release.AnchorRelease`.  An import
    whose header cannot be placed owns no level; one whose placement was
    released owns a level and a release.  The three CHECKs that paired and
    bounded the two columns went with them: the pairing is the level row's
    NOT NULL shape and its keys, and the bound (the solved day lies inside the
    declared window) is ``budget.level_lies_within_file``
    (:mod:`app.level_infrastructure`), attached to BOTH tables so neither the
    level nor this row's window can move outside the other.

    **``opening_balance`` and ``closing_balance`` were DROPPED at X-f6e-1**,
    and dropping them is the point rather than a tidy-up: ``closing`` is
    ``opening + Sigma(lines)`` and ``opening`` is
    ``stated - Sigma(lines up to the effective day)``, so both were derived
    values stored beside their own source with nothing reconciling the three --
    the root cause several of this project's arcs exist to remove.  What is
    stored is the claim; the observation and how firmly it is held are the
    level row's; every balance derives.
    """

    __tablename__ = "statement_imports"
    __table_args__ = statement_import_table_args

    id = db.Column(db.Integer, primary_key=True)
    source_id = db.Column(
        db.Integer,
        db.ForeignKey("ref.statement_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    file_name = db.Column(db.String(255), nullable=False)
    file_digest = db.Column(db.String(64), nullable=False)
    declared_start = db.Column(db.Date, nullable=False)
    declared_end = db.Column(db.Date, nullable=False)
    # NULLABLE, because a source may state no balance at all -- and then this
    # import determines no opening and the three columns below are NULL with
    # it.  Every SECU export the developer holds states one.
    stated_balance = db.Column(db.Numeric(12, 2))
    stated_balance_on = db.Column(db.Date)

    source = db.relationship("StatementSource", lazy="joined")
    # NO relationship to the level this import placed, to the releases naming
    # it, or to the sightings it wrote, deliberately: the first two tables
    # are append-only, and a relationship would let the unit of work emit
    # the SET NULL / DELETE the triggers refuse on the way to deleting this
    # row.  The database's own CASCADE and ``SET NULL
    # (released_by_import_id)`` are the disposal path -- the sightings go by
    # ``fk_statement_line_sightings_import_account``, and a line they were the
    # last to hold goes by ``budget.remove_line_left_unsighted``
    # (:mod:`app.sighting_infrastructure`) -- and the readers that want a
    # level join for it (``statement_import._balance.bank_levels``).

    #: The columns that tell two acts on one account apart, oldest first: the
    #: instant the import ran, then its id.  The id is load-bearing rather
    #: than decorative: ``created_at`` defaults to ``now()``, which in
    #: PostgreSQL is the TRANSACTION's start time, so two imports written in
    #: one transaction carry the identical instant and the id is the only
    #: thing that orders them.
    #:
    #: **ONE spelling, because four readers sort by it** (plan step
    #: ``bank_import:X-f6b-1``): the imports table and the hero's *last
    #: import* line read it newest-first, and the sighting relation reads it
    #: to say which import FIRST sighted a line
    #: (:meth:`StatementLineSighting.first_import_of_each_line`) and which
    #: sighted it LATEST (:attr:`BankStatementLine.current`).  A fifth
    #: spelling of one order is how two surfaces come to call different
    #: imports the newest.  Stated as names so :meth:`act_order` (a query)
    #: and :attr:`act_key` (a loaded row) read the one list.
    _ACT_ORDER = ("created_at", "id")

    @classmethod
    def act_order(cls) -> tuple:
        """Return the ``ORDER BY`` columns for the act order, oldest first.

        Returns:
            ``(StatementImport.created_at, StatementImport.id)``; a reader
            wanting newest-first applies ``.desc()`` to each.
        """
        return tuple(getattr(cls, name) for name in cls._ACT_ORDER)

    @property
    def act_key(self) -> tuple:
        """Return this act's place in the act order, as a sort key.

        Returns:
            ``(created_at, id)`` off this row.
        """
        return tuple(getattr(self, name) for name in self._ACT_ORDER)

    def __repr__(self):
        return (
            f"<StatementImport account={self.account_id} "
            f"{self.declared_start}..{self.declared_end}>"
        )


class BankStatementLine(AccountScopedMixin, db.Model):
    """One line a bank showed, recorded as the bank stated it.

    The app's own record of a line it did not author.  Nothing here is derived
    from a Shekel row and nothing here is edited afterwards: a statement line is
    an OBSERVATION, and the whole point of ruling **R-FL** was that an
    observation is what the app was missing.

    **A line is the BANK's fact, and what each source CALLED it is that
    source's SIGHTING** (plan step ``bank_import:X-f6b-1``, ruling
    **R-BI10**).  This row holds what every source agrees on -- the account,
    the day, the amount, the ordinal that tells two same-day same-amount lines
    apart -- and one :class:`StatementLineSighting` per import that showed it
    holds that source's wording, id, stated transaction day, running balance,
    category and the MERCHANT its word names.  A line lives while any
    sighting does
    (``budget.remove_line_left_unsighted``, :mod:`app.sighting_infrastructure`),
    which is what lets a second source start recording over the first's
    span without the two disagreeing about a line neither authored.  Until
    that step the wording lived HERE, so a different wording on a known
    day+amount was read as the bank RESTATING a line and refused the whole
    file (finding **N-303**).

    Columns:
        account_id   -- the account the line belongs to
                        (:class:`AccountScopedMixin`, since X-f6b-1: it
                        reached the account through its import's composite
                        key before, and the import no longer owns it); every
                        sighting's two composite keys hold the sighting's
                        account equal to this one and to its import's.
        posted_on    -- the day the bank posted it.  **This is the fact the
                        whole arc exists to obtain**: measured against the
                        developer's export, only 33 of 110 matched movements
                        carried the day the bank posted them (finding
                        **N-173**).
        amount       -- signed, positive INTO the account (see the module
                        docstring).
        sequence_in_group -- the ordinal that completes the identity key.

    **The merchant a rule fires on is a READ over the sightings, and the key
    is stored ONCE, on the sighting** (plan step ``bank_import:X-f6b-1b``,
    ruling **R-BI16**, which amends R-BI10's "stays on the line" clause).
    :attr:`merchant_id` and :attr:`merchant_name` are what the EARLIEST
    surviving sighting that names a merchant says
    (:attr:`StatementLineSighting.merchant_id`, by
    :meth:`StatementImport.act_order`), so the merchant a
    :class:`~app.models.merchant_rule.MerchantRule` is stated against does not
    move when a later source shows a different word, and a deleted import
    takes its own word with it and leaves nothing to repair.  *The key lived
    HERE from plan step ``bank_import:X-gd-1`` (when the merchant became a
    row) until X-f6b-1b: minted from the first sighting that named a word,
    filled from a later one only while NULL -- a derived value stored beside
    its source with no reconciler, so a line kept a key that a since-deleted
    import had minted (finding **BI-504**).*

    **Both reads are ONE SQL producer each, and nothing else derives them**
    (``CLAUDE.md`` rule 14; R-BI16's "one SQL producer, read-only" option):
    a ``column_property`` built by :func:`_stated_by_the_naming_sighting`,
    correlated to this row explicitly, so ``line.merchant_id`` on a loaded
    row and ``BankStatementLine.merchant_id`` in a ``filter``, ``group_by``
    or ``distinct`` are the same subquery -- a reader in either dialect
    reaches the one walk, and there is no Python re-derivation to agree
    with it.  The ``hybrid_property`` over each is the SEAL: a
    ``column_property`` accepts an assignment silently (measured 2026-09-18
    on SQLAlchemy 2.0: the constructor kwarg and ``row.attr = x`` both land
    in the instance and vanish at the next load), where the hybrid without a
    setter raises ``AttributeError`` on both, so no writer can put a key on
    a line.  **A loaded row's answer is as of its load**: the record door
    expires :data:`READS_OVER_SIGHTINGS` on every held line after it stages
    this import's sightings, and a locked read reloads them
    (``statement_match._resolve.locked_for_write`` composes
    ``populate_existing()``), which is the same rule the eager
    :attr:`sightings` collection already lived under.

    **The facts a SOURCE states about a line are its sighting's**, read
    through :attr:`sightings` and exposed here once each so a reader holding a
    line does not have to know which sighting to ask: :attr:`description`,
    :attr:`source_category` and :attr:`running_balance` are the sighting of
    the LATEST import (:attr:`current`) -- what the bank calls the line now --
    and :attr:`transaction_on` is the EARLIEST day any sighting states,
    because that is the day a match writes onto a purchase's ``purchased_on``
    (ruling **R-FW**) and money cannot be spent after the earliest day a
    source says it was.  A source's own id (``external_id``) is exposed on no
    line property: it is corroboration this source's next import pairs on
    (``_record``), never a fact about the line.

    **A line's stored IDENTITY is ``(account_id, posted_on, amount,
    sequence_in_group)``**, and the ordinal is what makes that key total.  Two
    genuinely distinct charges can share a day and an amount -- the same coffee
    twice -- and a key without the ordinal would reject the second as a
    duplicate, which is silent money loss on exactly the shape a duplicate
    detector is supposed to protect.

    **The ordinal is a SURROGATE this app mints, and no re-import compares
    against it** (plan step ``bank_import:X-f6a-4``).  Three of the key's four
    terms are facts the bank stated; this one is not, and the write door used
    to compare an incoming line against whatever sat at its ordinal -- treating
    an app-assigned number as though the bank had supplied it, which is a
    derived value stored beside its source with nothing reconciling the two.
    Measured against the shipped code 2026-08-20, that refused a whole file on
    two events that were not restatements at all: two same-day same-amount
    lines re-ordered between exports, and a genuinely NEW line the bank
    INSERTED ahead of a recorded one.  A re-import now reconciles a
    ``(posted_on, amount)`` GROUP as a set -- by this source's own id, then by
    the wording this source wrote, then by count against the lines another
    source showed (``statement_import._plan._reconcile``) -- and mints an
    ordinal only for a line it has decided is new
    (:func:`app.services.statement_import.fresh_ordinals`).  What this key
    still guarantees is that every recorded line has a distinct, stable
    address, which is the whole of what a surrogate owes.

    **The merchant is a FACT the adapter states, not a token a reader parses,
    and the NULL is the source saying it names none** (plan step
    ``bank_import:X-f6a-3d``).  It was
    ``statement_match._offers.merchant_of(description)``, read at render time,
    and that was right for what it fed: a form's name box, where a wrong parse
    costs a badly-named row.  Keying a RULE on it is a stronger claim than a
    display default can carry, in one specific way -- that reader is TOTAL, so
    a source with no merchant token falls back to the whole description, and
    SECU's own OFX truncates 326 of its 361 descriptions to exactly 32
    characters.  Every one of those would key one rule, which would then fire
    on every merchant behind them.  A NULL keys nothing, so a source that
    cannot name a merchant offers no rule rather than a wrong one -- the same
    direction a missing fact has to fail in that :attr:`transaction_on` already
    fails in.
    **It is read from the source's own merchant FIELD** (for SECU's CSV, the
    parenthesised trailing token of the Description CELL) rather than from
    the description, which is the ``Description | Memo`` join -- so a user's
    own memo ending in parentheses cannot become the key a rule matches on.
    That bound is structural rather than guarded, exactly as
    ``_secu_csv._stated_transaction_day``'s is.

    **There is deliberately no ``transaction_on <= posted_on`` CHECK** on a
    sighting.  The obvious constraint is false on real data: 2 of 361 lines
    in the developer's own SECU export carry an OFX ``DTUSER`` one day AFTER
    their ``DTPOSTED`` (both ACH deposits, 2026-02-24 and 2026-03-18).  A
    constraint that a real statement violates would make the truth
    unimportable.
    **What depends on that day being the earlier one is therefore a READER's
    guard, not the schema's** (plan step ``bank_import:X-f6a-3a``): a match
    corrects a purchase's ``purchased_on`` to this day, and
    ``entry_service.update_entry`` refuses the pair a later one would make --
    so the proposer declines the pairing rather than the table refusing the
    line.
    """

    __tablename__ = "bank_statement_lines"
    __table_args__ = bank_statement_line_table_args

    id = db.Column(db.Integer, primary_key=True)
    # ``account_id`` is the mixin's: a direct CASCADE key since X-f6b-1.  The
    # line reached the account through ``fk_bank_statement_lines_import_
    # account`` while an import owned it; a line held by sightings has no
    # single import to reach through, and a row whose account is a bare
    # integer until its first sighting arrives is a row a writer can put on
    # the wrong account.
    posted_on = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    # NO server default, deliberately.  A default on a component of the
    # IDENTITY key would let a future writer that forgets to compute the
    # ordinal write a plausible row instead of failing.
    sequence_in_group = db.Column(db.SmallInteger, nullable=False)

    # **Eager** (plan step ``bank_import:X-f6b-1``): every reader that has a
    # line wants what the bank CALLED it, and every one of those facts is a
    # sighting's -- the review screen renders 91 lines at once, and a lazy
    # load here is the N+1 finding **N-309** already paid for.
    # ``joined`` rather than ``selectinload`` so a reader that loads N lines still
    # issues ONE statement -- the property
    # ``test_reading_every_lines_merchant_is_ONE_statement`` grades -- and a
    # list bounded by ``LIMIT`` is wrapped in a subquery by the ORM so the
    # bound counts LINES, not the joined rows.  Writable (not viewonly) so a
    # fixture may append to it; the app's one writer stages sightings by
    # their ids (``statement_import._record``).  ``delete, delete-orphan``
    # with ``passive_deletes`` so the unit of work agrees with the database
    # about disposal -- a sighting goes with its line -- rather than trying
    # to null a loaded child's NOT NULL key on the way; no door in ``app/``
    # deletes a line through the ORM (the trigger and the cascades do), so
    # this is what a fixture or a psql-less repair gets.
    sightings = db.relationship(
        "StatementLineSighting", back_populates="line",
        foreign_keys="[StatementLineSighting.line_id, "
                     "StatementLineSighting.account_id]",
        lazy="joined", cascade="all, delete-orphan", passive_deletes=True,
    )

    #: The attributes a loaded line derives from its sightings, which a
    #: writer that stages a new sighting of it must expire: the collection
    #: itself, and the two projections of the naming sighting.  ONE spelling
    #: (``CLAUDE.md`` rule 14), because the record door and the test builder
    #: each expire exactly this set and neither may know the other's list.
    READS_OVER_SIGHTINGS = (
        "sightings",
        "_BankStatementLine__named_merchant_id",
        "_BankStatementLine__named_merchant_name",
    )

    @hybrid_property
    def merchant_id(self):
        """Return the merchant a rule on this line fires on, as its row id, or ``None``.

        What the EARLIEST surviving sighting that names a merchant says
        (ruling **R-BI16**; the class docstring says why that sighting and
        not the latest).  ``None`` when no sighting names one, which is every
        source saying it names none -- the direction a missing fact has to
        fail in, because a NULL keys no rule.

        **A READ-ONLY projection, and the seal is the whole point of the
        hybrid**: there is no setter, so ``line.merchant_id = x`` and
        ``BankStatementLine(merchant_id=x)`` both raise ``AttributeError``
        rather than land a key on a line.  At class level it is the
        ``column_property``'s own expression, so ``filter``, ``group_by``,
        ``distinct`` and ``isnot(None)`` all read the one subquery.

        Returns:
            The merchant row's id, or ``None``.
        """
        return self.__named_merchant_id

    @merchant_id.inplace.expression
    @classmethod
    def _merchant_id_expression(cls):
        """Return the SQL form of :attr:`merchant_id`: the sealed ``column_property``."""
        return cls.__named_merchant_id

    @hybrid_property
    def merchant_name(self):
        """Return what this line's merchant is CALLED, or ``None``.

        The label half of the fact :attr:`merchant_id` is the key half of,
        read by the SAME producer (:func:`_stated_by_the_naming_sighting`,
        projecting :attr:`~app.models.merchant.Merchant.name`), so a caller
        holding this row does not have to know that a merchant is a row to
        print its name and the two halves cannot come from two sightings.
        ``None`` exactly when :attr:`merchant_id` is.  Read-only, for
        :attr:`merchant_id`'s reason.

        Returns:
            The merchant row's name, or ``None``.
        """
        return self.__named_merchant_name

    @merchant_name.inplace.expression
    @classmethod
    def _merchant_name_expression(cls):
        """Return the SQL form of :attr:`merchant_name`: the sealed ``column_property``."""
        return cls.__named_merchant_name

    @property
    def current(self) -> "StatementLineSighting":
        """Return the sighting of the LATEST import that showed this line.

        What the bank calls the line NOW.  Latest by
        :attr:`StatementImport.act_key` -- the import's instant, then its
        id -- and never by the sighting's own id, which is a surrogate the
        sequence hands out in write order and which a backfill wrote in line
        order.  A line always has one: ``budget.remove_line_left_unsighted``
        deletes a line whose last sighting goes, so a loaded line with an
        empty collection is a row this session staged and has not flushed a
        sighting for yet, and reading a source's fact off it is a caller's
        defect rather than a state to answer for.
        """
        return max(
            self.sightings,
            key=lambda sighting: sighting.statement_import.act_key,
        )

    @property
    def description(self) -> str:
        """Return what the bank calls this line, per its latest sighting."""
        return self.current.description

    @property
    def source_category(self) -> "str | None":
        """Return what the latest source filed this line under, or ``None``."""
        return self.current.source_category

    @property
    def running_balance(self) -> "Decimal | None":
        """Return the balance the latest source stated after this line, or ``None``."""
        return self.current.running_balance

    @property
    def transaction_on(self) -> "date | None":
        """Return the EARLIEST day any source says this line was made, or ``None``.

        The day a match writes onto a matched purchase's ``purchased_on``
        (ruling **R-FW**), so it is the tightest bound the sightings support:
        money cannot be spent after the earliest day a source states it was.
        ``None`` when no sighting states one, which is every source saying it
        names no separate transaction day.
        """
        stated = [
            sighting.transaction_on for sighting in self.sightings
            if sighting.transaction_on is not None
        ]
        return min(stated) if stated else None

    def __repr__(self):
        return (
            f"<BankStatementLine {self.posted_on} {self.amount} "
            f"x{self.sequence_in_group} ({self.id})>"
        )


class StatementLineSighting(db.Model):
    """What ONE import said about ONE bank line.

    Plan step ``bank_import:X-f6b-1``, ruling **R-BI10**: *a bank line is held
    by the sightings of the imports that showed it.*  The line is the bank's
    fact -- account, day, amount, ordinal; this row is one source's account of
    it: the wording that source wrote, the id it assigned, the day it says the
    money was spent, the balance it stated after the line, the category it
    filed the line under and the merchant its word names.  Two sources
    showing one line write two rows here and one line there, and the app
    stops calling a second wording a restatement.

    Columns:
        account_id   -- the line's account and the import's, held equal to
                        BOTH by the two composite keys below, so a sighting
                        cannot pair a line with another account's import.
        line_id      -- the :class:`BankStatementLine`.
        import_id    -- the :class:`StatementImport` that showed it.
        description  -- what this source called the line, verbatim.
        merchant_id  -- the :class:`~app.models.merchant.Merchant` this
                        source's merchant word names, or ``None`` where it
                        names none: the CSV's parenthesised token, the feed's
                        ``payee``, resolved to the account's row for that
                        word by ``statement_import._merchants
                        .resolve_merchants`` in the same pass that writes
                        this row (plan step ``bank_import:X-f6b-1b``, ruling
                        **R-BI16**).  **The KEY a rule fires on lives HERE
                        and nowhere else**: the line's answer is a read over
                        its sightings (:attr:`BankStatementLine.merchant_id`).
                        The word itself lives once, on the merchant row --
                        ``merchants.name`` is the source's word verbatim,
                        written by one path and never edited -- so this row
                        carried the word as a string from X-f6b-1 until
                        X-f6b-1b and carries the key now (ruling **R-BI17**:
                        the word column was two homes for one fact).  Held
                        to THIS ACCOUNT's merchants by
                        ``fk_statement_line_sightings_merchant_account``,
                        composite for the reason the line's key was
                        (``bank_import:X-gd-1``): *is this merchant on this
                        account* is never a reader's check.  ``NO ACTION``,
                        measured rather than reasoned about on the line's key
                        (:class:`~app.models.merchant.Merchant`): a merchant a
                        sighting names cannot be deleted from under it, and
                        an account's deletion still succeeds because every
                        cascade of that one statement completes before the
                        check runs.
        transaction_on -- the day this source STATED the transaction itself
                        happened, or ``None`` where it states none.  **The
                        NULL is a fact and not a gap** (plan step
                        ``bank_import:X-f6a-3a``): a source that does not
                        distinguish the two days says so here rather than
                        restating the clearing day.
        external_id  -- this source's own id for the line (an OFX ``FITID``,
                        the feed's ``id``), or ``None``.  CORROBORATION that
                        this source's next import pairs on first
                        (``statement_import._plan._reconcile``), never the
                        line's identity: a source that has one still cannot
                        claim it on two lines of one account -- the door
                        refuses a file restating a held id on another day or
                        amount (``_plan._refuse_moved_ids``) -- and one that
                        has none is not thereby unidentifiable.
        running_balance -- the balance this source stated after the line, or
                        ``None`` where the export carries none.  A prefix sum
                        over the source's LISTING order, which is why two
                        sightings of one line may legitimately disagree
                        (``_plan._refuse_restatement``); what verifies an
                        import against itself is the chain inside one file
                        (``statement_import.verify_running_balance``).
        source_category -- the source's OWN category string, kept as
                        provenance.  It is the bank's opinion about a
                        merchant, not a Shekel category; the one thing that
                        reads it is ``statement_match._vocabulary``, in SQL,
                        against that adapter's own vocabulary
                        (**bank_import:R-GJ**).

    **One row per ``(line, import)``**, which is the whole of what a re-import
    means: an import that shows a line the app already holds records that it
    showed it, and a line is *already known* to an import exactly when the
    import's sighting of it is not the line's first.  The two derived reads
    every counter needs -- which import FIRST sighted a line, and which lines
    ONE import alone holds -- are :meth:`first_import_of_each_line` and
    :meth:`sole_import_of_each_line`, stated once each here because both
    service packages read them and neither may import the other.

    **Audit-logged like its siblings, and NOT append-only**: a sighting is
    deleted with its import, which rulings **R-GG(e)** and **R-BAL51**
    already permit for a level.  When the LAST sighting of a line goes, the
    line goes with it (``budget.remove_line_left_unsighted``,
    :mod:`app.sighting_infrastructure`) -- so an import's deletion removes
    exactly the lines no other import vouches for, and a line cannot be
    STRANDED by a delete.  (It can still be inserted with no sighting, as
    the record door does for the instant between staging a fresh line and
    staging its sighting; what the trigger makes impossible is a line
    outliving its last sighting.)
    """

    __tablename__ = "statement_line_sightings"
    __table_args__ = statement_line_sighting_table_args

    id = db.Column(db.Integer, primary_key=True)
    # No direct FK: the two composite keys above reach ``budget.accounts``
    # through the line and through the import, which is what holds the three
    # accounts equal.
    account_id = db.Column(db.Integer, nullable=False)
    line_id = db.Column(db.Integer, nullable=False)
    import_id = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(200), nullable=False)
    # NULLABLE, and the NULL means "this source names no merchant" rather
    # than "unknown" -- see :class:`BankStatementLine` for why that direction
    # is the safe one on the fact a rule matches against.  No direct
    # single-column key: the merchant is reached through the composite above,
    # which also holds the ACCOUNT equal.
    merchant_id = db.Column(db.Integer)
    transaction_on = db.Column(db.Date)
    external_id = db.Column(db.String(64))
    running_balance = db.Column(db.Numeric(12, 2))
    source_category = db.Column(db.String(100))

    line = db.relationship(
        "BankStatementLine", back_populates="sightings",
        foreign_keys=[line_id, account_id],
    )
    # **Eager and VIEWONLY**: every reader that holds a sighting asks which
    # import it belongs to -- to order the sightings of one line
    # (:attr:`BankStatementLine.current`) and to know which SOURCE showed it
    # (``_plan._reconcile`` pairs within a source) -- so a lazy load here is
    # one statement per sighting on every list surface (finding **N-309**);
    # and viewonly because :attr:`line` already persists ``account_id`` and
    # two relationships writing one column would contend.  No relationship
    # onto the merchant row, deliberately: nothing holding a sighting asks
    # its merchant's name -- the line's two projections read it in SQL
    # (:func:`_stated_by_the_naming_sighting`).
    statement_import = db.relationship(
        "StatementImport", foreign_keys=[import_id, account_id],
        lazy="joined", viewonly=True,
    )

    @classmethod
    def of_its_line(cls):
        """Return the join clause onto the sighting's line, both key columns.

        ONE spelling of the composite join every reader that walks line ->
        sighting makes, so the account equality travels IN the join rather
        than being remembered per reader.

        Returns:
            The ``AND`` of the two column equalities.
        """
        return and_(
            cls.line_id == BankStatementLine.id,
            cls.account_id == BankStatementLine.account_id,
        )

    @classmethod
    def of_its_import(cls):
        """Return the join clause onto the sighting's import, both key columns.

        The sighting -> import half of :meth:`of_its_line`'s rule.

        Returns:
            The ``AND`` of the two column equalities.
        """
        return and_(
            cls.import_id == StatementImport.id,
            cls.account_id == StatementImport.account_id,
        )

    @classmethod
    def first_import_of_each_line(cls, account_id: int):
        """Return which import FIRST sighted each of *account_id*'s lines.

        The fact ``bank_statement_lines.import_id`` stored until X-f6b-1 --
        *the import that first recorded this line* -- as a derivation: for
        each line, the sighting whose import is earliest by
        :meth:`StatementImport.act_order`.  What *recorded N new line(s)*
        counts, what the rule pass files
        (``statement_match._filing._fresh_line_ids``) and what the hero's
        *last import* line reports.

        Args:
            account_id: The account whose lines to read.

        Returns:
            A subquery with columns ``line_id`` and ``import_id``, one row per
            sighted line of the account.  A reader joins it on ``line_id`` and
            filters ``import_id``.
        """
        return (
            select(cls.line_id, cls.import_id)
            .join(StatementImport, cls.of_its_import())
            .where(cls.account_id == account_id)
            .ext(distinct_on(cls.line_id))
            .order_by(cls.line_id, *StatementImport.act_order())
            .subquery()
        )

    @classmethod
    def sole_import_of_each_line(cls, account_id: int):
        """Return which lines exactly ONE import holds, and which import.

        The lines that GO when that import is deleted -- and therefore the
        matches, skips and merchants its deletion takes with it
        (``statement_import._reads``).  A line another import also sighted
        stays, so it is absent here.

        Args:
            account_id: The account whose lines to read.

        Returns:
            A subquery with columns ``line_id`` and ``import_id``, one row per
            line with a single sighting.
        """
        return (
            select(cls.line_id, db.func.min(cls.import_id).label("import_id"))
            .where(cls.account_id == account_id)
            .group_by(cls.line_id)
            .having(db.func.count(cls.id) == 1)
            .subquery()
        )

    @classmethod
    def counts_by_import(cls, account_id: int):
        """Return, per import, how many lines it sighted and how many it sighted FIRST.

        The two figures ``statement_imports.line_count`` and
        ``recorded_count`` stored until X-f6b-1, as ONE aggregate over the
        sightings: what the imports table prints per row, what the hero's
        *last import* line prints for the newest, and what a receipt
        computes for the act it just performed.  The FIRST half reads
        :meth:`first_import_of_each_line`, so the three surfaces cannot
        count "new" three ways.

        Args:
            account_id: The account whose imports to count over.

        Returns:
            A ``SELECT`` with columns ``import_id``, ``sighted`` and
            ``first``, one row per import that wrote a sighting; a caller
            executes it and reads an absent import as ``(0, 0)``.
        """
        first = cls.first_import_of_each_line(account_id)
        return (
            select(
                cls.import_id,
                db.func.count(cls.id).label("sighted"),
                db.func.count(cls.id)
                .filter(first.c.import_id == cls.import_id)
                .label("first"),
            )
            .join(first, first.c.line_id == cls.line_id)
            .where(cls.account_id == account_id)
            .group_by(cls.import_id)
        )

    def __repr__(self):
        return (
            f"<StatementLineSighting line={self.line_id} "
            f"import={self.import_id} '{self.description[:24]}'>"
        )


def _stated_by_the_naming_sighting(column):
    """Return *column* as the EARLIEST sighting naming a merchant states it, per line.

    **THE one producer of a line's merchant** (plan step
    ``bank_import:X-f6b-1b``, ruling **R-BI16**): a scalar subquery over the
    line's sightings that name a merchant, joined to their imports for the
    act order and to the merchant row for its name, taking the first by
    :meth:`StatementImport.act_order`.  Both of
    :class:`BankStatementLine`'s projections are built here, so the key and
    the label a reader gets off one row come from the SAME sighting by
    construction rather than by two derivations agreeing.

    **Correlated to the line EXPLICITLY, and that is load-bearing.**  A
    scalar subquery auto-correlates every table the enclosing statement also
    selects from, and one reader encloses this in a statement that joins
    the sightings and the imports itself
    (``statement_match._vocabulary.account_payment_merchants`` files a line
    under a category by ANY of its sightings).  Measured 2026-09-18 on that
    reader, four ways: written with an IMPLICIT ``FROM`` (every table named
    only in the ``WHERE``) and left to auto-correlate, the inner sightings
    ARE the outer joined row and the set holds the filing sighting's
    merchant instead of the line's -- the silent wrong answer; the explicit
    ``select_from(...).join(...)`` chain below happens to survive
    auto-correlation, because a ``JOIN`` is one ``FROM`` element the outer
    statement does not hold; and ``correlate(BankStatementLine)`` is correct
    under BOTH forms, because it names the one table that IS the enclosing
    row and keeps every other in the subquery's own ``FROM``, where it
    shadows the outer one.  So the correlate is the guarantee and the join
    chain is the shape; a rewrite that drops both is what
    ``test_the_set_holds_the_LINES_merchant_not_the_filing_sightings``
    (``tests/test_services/test_statement_match/test_bars.py``) fails on.
    The composite joins and the order are the model's own spellings
    (:meth:`StatementLineSighting.of_its_line`, :meth:`~StatementLineSighting
    .of_its_import`, :meth:`StatementImport.act_order`), not a second one.

    Args:
        column: The column to project off the naming sighting's row or its
            merchant's -- :attr:`StatementLineSighting.merchant_id` or
            :attr:`~app.models.merchant.Merchant.name`.

    Returns:
        The scalar subquery, ``NULL`` for a line no sighting names a merchant
        on.
    """
    return (
        select(column)
        .select_from(StatementLineSighting)
        .join(StatementImport, StatementLineSighting.of_its_import())
        .join(
            Merchant,
            and_(
                StatementLineSighting.merchant_id == Merchant.id,
                StatementLineSighting.account_id == Merchant.account_id,
            ),
        )
        .where(
            StatementLineSighting.of_its_line(),
            StatementLineSighting.merchant_id.isnot(None),
        )
        .order_by(*StatementImport.act_order())
        .limit(1)
        .correlate(BankStatementLine)
        .scalar_subquery()
    )


# **The two projections, mapped after both classes exist**, because the
# producer names the sighting relation and the merchant row and a class body
# cannot name a class declared below it (SQLAlchemy's documented "append a
# column_property to a mapped class" form).  The names are DOUBLE-underscored
# and spelled here in their mangled form, and that is the seal rather than a
# style (the pattern ``Transaction.__estimated_amount`` set): a
# ``column_property`` accepts an assignment silently, so the guessable
# spellings -- ``row._named_merchant_id``, ``row.named_merchant_id`` -- bind
# plain instance attributes that reach no mapped state and the next read
# exposes, while the public :attr:`BankStatementLine.merchant_id` refuses a
# write outright.
# Pylint: ``protected-access`` -- the mangled attribute is this class's own,
# assigned at module scope only because the producer must be built after
# ``StatementLineSighting`` and ``Merchant`` are declared; no other module
# reaches it, and the hybrids above are its only readers.
BankStatementLine._BankStatementLine__named_merchant_id = column_property(  # pylint: disable=protected-access
    _stated_by_the_naming_sighting(StatementLineSighting.merchant_id),
)
# Pylint: ``protected-access`` -- as above, for the label projection.
BankStatementLine._BankStatementLine__named_merchant_name = column_property(  # pylint: disable=protected-access
    _stated_by_the_naming_sighting(Merchant.name),
)
