"""
Shekel Budget App -- A bank source's own id for a bank line (budget schema)

One table, one subject (plan step plan#2232, rulings
**plan#2233**, **plan#3209** and **plan#3220**): the id a source gives a bank
transaction -- an OFX ``FITID``, a feed's transaction ``id`` -- and the ONE
line it names.

**A source names one line per id, and this table is what makes that a key
rather than a check.**  The rule lived on the line until plan step
``bank_import:X-f6b-1`` (``uq_bank_statement_lines_external_id``), which moved
the id onto each import's sighting, where it could not be unique -- a
re-import re-sights a line under the same id, which is two sighting rows and
one line -- so from then until this table the rule was the record door's
check alone (``statement_import._plan._refuse_moved_ids``).  A missed pairing
would have recorded one bank transaction as two lines, and Books vs bank sums
every line of a day.  The door still refuses each shape that would mean a
second line under a held id, with a sentence, before it writes; this key
refuses the state itself to any writer the door does not govern -- one
outside it, or two imports racing through it.

**The id stays with its LINE for as long as the line exists** (ruling
**plan#2233**, 2026-11-10): the row goes only when its line does
(``fk_line_source_ids_line_account``, CASCADE), never when the last import of
its own source stops showing the line.  Worked: a feed records a $42.17 coffee
on Oct 8 as ``TRN-881`` and a CSV later shows the same line; undoing the feed's
import leaves the line standing on the CSV's sighting, and this row with it,
so a later sync that sends ``TRN-881`` dated Oct 9 is refused rather than
recorded as a second $42.17 line.  Under the rule before this table -- an id
held only while a sighting from its own source carried it -- the sync would
have been recorded, and Books vs bank would show $84.34 for Oct 8-9 where
the bank moved $42.17.

**One home for the id** (``CLAUDE.md`` rule 14; ruling **plan#3220**):
``statement_line_sightings.external_id`` and its index were dropped in the
same revision, so what a source calls a line is stored once, here, and no
sighting carries a copy that must agree with it.
"""

from sqlalchemy import and_

from app.extensions import db
from app.models.statement_import import BankStatementLine


class LineSourceId(db.Model):
    """One source's id for one bank line.

    Columns:
        account_id  -- the line's account, held equal to it by
                       ``fk_line_source_ids_line_account``: a co-located key
                       rather than a copy a writer maintains, the construction
                       ``statement_line_sightings.account_id`` takes.  It
                       exists because the id is unique PER ACCOUNT: a source
                       numbers its transactions within an account, so the same
                       string on two accounts is two ids.
        source_id   -- the adapter whose id this is (``ref.statement_sources``).
                       Ids are per source: the same string from two adapters is
                       two ids, and a CSV's line and a feed's line for one
                       transaction may each carry their own.
        external_id -- what that source calls the line, verbatim.
        line_id     -- the :class:`~app.models.statement_import.BankStatementLine`
                       it names.

    **A line may hold more than one id from one source.**  The record door
    pairs an incoming line by this source's id first and by its wording
    after (ruling **R-BI10**), so a line this source showed under one id can
    be paired by wording to a later file's line carrying another.  What the
    key forbids is the converse -- one id naming two lines -- which is the
    double count.  A file stating, as two lines, two ids that one line
    already holds is refused by the door
    (``statement_import._plan._refuse_ids_sharing_a_line``), because it
    says the one line is two transactions.

    **No relationship to the line**, deliberately: the one reader joins it in
    a query (``_plan._held_ids``) through :meth:`of_its_line`, the one writer
    names the line by id (``statement_import._record``), and a
    relationship would be an abstraction with no reader (``CLAUDE.md`` rule
    13), the reasoning :class:`~app.models.statement_line_skip.StatementLineSkip`
    records for its own.
    """

    __tablename__ = "line_source_ids"
    __table_args__ = (
        # THE RULE: a source names ONE line per id, per account.  Total, not
        # partial, because every column of it is NOT NULL.  It is also the
        # index the record door's lookup reads (``_plan._held_ids``: this
        # account, this source, the ids the file states).
        db.UniqueConstraint(
            "account_id", "source_id", "external_id",
            name="uq_line_source_ids_account_source_external",
        ),
        # This id's account IS its line's, keyed onto
        # ``uq_bank_statement_lines_id_account``.  CASCADE, and that is ruling
        # **plan#2233**: the id goes when its line goes and at no other time.
        # A line goes when its last sighting does
        # (``budget.remove_line_left_unsighted``), so undoing every import
        # that showed a line frees its ids, and undoing only some of them does
        # not.
        db.ForeignKeyConstraint(
            ["line_id", "account_id"],
            ["budget.bank_statement_lines.id",
             "budget.bank_statement_lines.account_id"],
            name="fk_line_source_ids_line_account",
            ondelete="CASCADE",
        ),
        # RESTRICT, the project's rule for a ``ref`` key: a source that has
        # named a line cannot be removed from the catalogue from under it.
        db.ForeignKeyConstraint(
            ["source_id"], ["ref.statement_sources.id"],
            name="fk_line_source_ids_source",
            ondelete="RESTRICT",
        ),
        # The line's CASCADE looks its ids up by line, which the unique key
        # above (account, source, id) cannot serve.
        db.Index(
            "idx_line_source_ids_account_line", "account_id", "line_id",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # No direct single-column key onto the account: the composite above
    # reaches it through the line and holds the two equal, the shape
    # ``statement_line_sightings.account_id`` takes.
    account_id = db.Column(db.Integer, nullable=False)
    source_id = db.Column(db.Integer, nullable=False)
    external_id = db.Column(db.String(64), nullable=False)
    line_id = db.Column(db.Integer, nullable=False)

    @classmethod
    def of_its_line(cls):
        """Return the join clause onto the id's line, both key columns.

        The composite join spelled once, so the account equality travels in
        the join rather than being remembered by the reader -- the rule
        :meth:`~app.models.statement_import.StatementLineSighting.of_its_line`
        states for a sighting.

        Returns:
            The ``AND`` of the two column equalities.
        """
        return and_(
            cls.line_id == BankStatementLine.id,
            cls.account_id == BankStatementLine.account_id,
        )

    def __repr__(self):
        return (
            f"<LineSourceId source={self.source_id} '{self.external_id}' "
            f"line={self.line_id}>"
        )
