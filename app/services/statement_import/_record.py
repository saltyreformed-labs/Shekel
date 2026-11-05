"""The ONE door that records what a statement said.

Everything it can refuse, it refuses BEFORE it INSERTS a row: the file is
parsed, its running-balance chain verified, its account identity reconciled and
its lines compared against what is already recorded, and only then is anything
staged.  So a refused import writes no new line without depending on the
rollback.

**The claim is about the SESSION too, and it was not until plan step
``bank_import:X-gd-1``.**  The reconciliation walks group by group, and the
absorbing arm this door had then filled a recorded row's NULLs as it went --
so a refusal raised on group *k* left groups 1..*k*-1 dirty in the session and
it was the route's rollback that discarded them.  Nothing was lost by that,
because those writes only ever ADD a fact the file states, but "leaves the
database exactly as it was without depending on the rollback" was true of the
older per-line loop and was not true of that one (found by adversarial
financial review 2026-08-20).  :func:`~._plan._reconcile` now DECIDES the whole
partition and returns it; the writes happen beside the staging, after the
last refusal, so the session is untouched by a refused import.  The change was
forced -- a merchant is a row now, and a row cannot be resolved for a file that
is about to be refused -- and the older claim is what it restores.

**The DECISION is another module's** (:mod:`._plan`, plan step
``balance:X-bk-2``, plan#1192): every refusal, the pairing of each line
against what its source already sighted, and the balance's resolution,
returned as a :class:`~._plan.StatementPlan` before anything is staged -- the
order the opening paragraph states, made a module boundary.  This one
performs the plan.

**A re-import writes NOTHING onto a recorded line** (plan step
``bank_import:X-f6b-1b``, ruling **R-BI16**).  Every fact a source states
about a line is its sighting's, the merchant KEY included: the sighting
carries the merchant its word names, and the line's merchant is a read over
its sightings.  Until that step this door filled a held line's NULL key from
a later sighting -- an ``UPDATE`` on the line, and a key that outlived the
import that minted it (finding **BI-504**).  Now a re-import UPDATEs no
bank line: what it writes FOR a held line is a sighting, whose insert takes
``FOR KEY SHARE`` on the line and nothing stronger (the import row, the
merchant rows and, when the file states one, the level are the pass's other
inserts, none of them on a line).

**Nothing here moves a figure.**  Recording what a bank said is separable from
deciding which of the app's own rows it explains, and that separation is the
leaf boundary (plan step ``bank_import:X-f6a``): the match, the review and the
``settled_on`` correction are the leaf after this one.  A reader can therefore
grade this commit by a property rather than by inspection -- no balance moves,
because no balance input is written.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no ``flask`` / ``request`` / ``session`` /
``current_app`` import.  It FLUSHES and does not commit, matching
``entry_service``'s doors: the route owns the unit of work.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date

from app import ref_cache
from app.enums import StatementSourceEnum
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.statement_import import (
    BankStatementLine,
    StatementImport,
    StatementLineSighting,
)

from ._anchor import ImportedBalance, release_anchors_from
from ._identity import record_identity
from ._line import KeyedLine, StatementLine
from ._merchants import resolve_merchants
from ._plan import _Reconciled, plan_statement


@dataclass(frozen=True)
class ImportOutcome:
    """What one import act did.

    Attributes:
        import_id: The ``budget.statement_imports`` row recording the act.
        line_count: Lines the file held -- each now a sighting of this
            import's.  **Read from the ONE aggregate the imports table and
            the hero read** (:meth:`~app.models.statement_import
            .StatementLineSighting.counts_by_import`) after the sightings
            are flushed, and stored nowhere (plan step
            ``bank_import:X-f6b-1``, ruling **R-IY**); a receipt that
            counted the parsed lines itself would be a second spelling of
            the same figure, agreeing today and free to part.
        recorded_count: Lines this import was the FIRST to sight, from the
            same aggregate.
        declared_start: The first day the import DECLARED it answers for
            (``StatementImport.declared_start``).
        declared_end: The last.
        balance: What the file claimed the account held and what this import
            made of it, or ``None`` when the file states no balance.  The same
            value the row stores, so the receipt and the page say one thing.
        anchors_released: Other imports whose placed balance rested on days
            this import's fresh lines reach, and which
            :func:`~._anchor.release_anchors_from` therefore released.  **On
            the receipt since plan step ``bank_import:X-gr``** (finding
            **BI-488**): the door counted them and dropped the count, so an
            owner learned that an import had taken a checked balance away
            from an earlier one only from the imports table's badge flipping
            to *not placed*, and only if they looked.  Zero for an import
            that recorded no fresh line, because a line already recorded
            undercuts nothing.
    """

    import_id: int
    line_count: int
    recorded_count: int
    declared_start: date
    declared_end: date
    balance: ImportedBalance | None
    anchors_released: int

    @property
    def already_known(self) -> int:
        """Return how many of the file's lines were already recorded.

        DERIVED rather than stored, which is this project's own rule about
        derived values applied to its own return type: two fields that must sum
        to a third are two fields that can come to disagree.  It is named at
        all -- rather than left to the caller's subtraction -- because it is
        the number that makes idempotency VISIBLE, and a caller doing the
        arithmetic itself is a caller that can do it backwards.
        """
        return self.line_count - self.recorded_count


def _merchant_words(reconciled: _Reconciled) -> "set[str]":
    """Return every merchant word this pass will need a row for.

    **Both halves, TOTAL**: every sighting this pass writes carries the
    merchant its word names (ruling **R-BI16**), whether the line is fresh or
    held, so every word the file states needs a row.  *Until plan step
    ``bank_import:X-f6b-1b`` the held half asked only for the words filling a
    NULL key on the line*; a held line's word that the line already had a
    key for was never resolved, so a second source's different word for a
    known line had no row at all.

    Args:
        reconciled: What :func:`_reconcile` decided.

    Returns:
        The words, as a set.  A line naming none contributes nothing, which is
        the source saying it names none.
    """
    stated = [keyed.line for keyed in reconciled.fresh]
    stated.extend(line for line, _recorded in reconciled.held)
    return {line.merchant for line in stated if line.merchant}


def _sighting_of(
    account_id: int, import_id: int, line_id: int, line: StatementLine,
    merchants: "dict[str, int]",
) -> StatementLineSighting:
    """Return this import's sighting of one line, from what the source stated.

    THE one place a :class:`~._line.StatementLine`'s per-source facts become a
    row, so the fresh half and the held half of a pass cannot record them
    differently -- the merchant KEY among them (ruling **R-BI16**): the word
    the source named becomes this sighting's ``merchant_id`` here, for a
    fresh line and a held one alike.

    Args:
        account_id: The account being imported into.
        import_id: The import that is recording the sighting.
        line_id: The recorded line it is a sighting of.
        line: What the source stated.
        merchants: This pass's merchant rows by name
            (:func:`~._merchants.resolve_merchants`), TOTAL over every word
            the file states -- :func:`_merchant_words` is what puts them in
            it, so this indexes rather than defaulting.

    Returns:
        The unstaged row.
    """
    return StatementLineSighting(
        account_id=account_id,
        import_id=import_id,
        line_id=line_id,
        description=line.description,
        # ``None`` where the source names none, which keys no rule -- the
        # direction a missing fact has to fail in.
        merchant_id=merchants[line.merchant] if line.merchant else None,
        transaction_on=line.transaction_on,
        external_id=line.external_id,
        running_balance=line.running_balance,
        source_category=line.source_category,
    )


def _stage_lines(
    account_id: int, import_id: int, fresh: "list[KeyedLine]",
    merchants: "dict[str, int]",
) -> None:
    """Stage one :class:`BankStatementLine` and its sighting per fresh line.

    The line needs its id before the sighting can name it, so the lines are
    flushed once, together, between the two loops -- one round trip for the
    pass rather than one per line.  The line takes what every source agrees
    on and nothing else; the merchant the file names goes onto the sighting
    (ruling **R-BI16**).

    Args:
        account_id: The account being imported into.
        import_id: The import that is recording them.
        fresh: The lines to write.
        merchants: This pass's merchant rows by name, for the sightings
            (:func:`_sighting_of`).
    """
    rows = [
        BankStatementLine(
            account_id=account_id,
            posted_on=keyed.line.posted_on,
            amount=keyed.line.amount,
            sequence_in_group=keyed.sequence_in_group,
        )
        for keyed in fresh
    ]
    db.session.add_all(rows)
    db.session.flush()
    db.session.add_all(
        _sighting_of(account_id, import_id, row.id, keyed.line, merchants)
        for row, keyed in zip(rows, fresh)
    )


def _write_records(
    account_id: int, import_id: int, reconciled: _Reconciled,
) -> None:
    """Write everything this file adds to the record, in one merchant pass.

    **The writes that share one fact, kept together because of it** (plan step
    ``bank_import:X-gd-1``).  A merchant WORD becomes a merchant ROW here and
    nowhere else (:func:`~._merchants.resolve_merchants`), and every sighting
    this pass writes -- of a held line or a fresh one -- names its row
    (ruling **R-BI16**).  Splitting them would mean resolving the same words
    twice or threading a mapping across the door, and the callers are a few
    lines apart.

    **Every line the file states gains this import's sighting** (ruling
    **R-BI10**): the held half records what this source said about a line
    the app already holds, the fresh half records the line and what was said.

    **It runs after the last refusal**, which is what the module's opening
    claim rests on: this is the first statement that INSERTS, and a file about
    to be refused leaves nothing behind without depending on the rollback.

    One statement for the whole pass, not one per line: the developer's own
    year-to-date export is 361 lines naming 62 merchants.

    Args:
        account_id: The account being imported into.
        import_id: The import recording the pass.
        reconciled: What :func:`_reconcile` decided this file adds and holds.
    """
    merchants = resolve_merchants(account_id, _merchant_words(reconciled))
    db.session.add_all(
        _sighting_of(account_id, import_id, recorded.id, line, merchants)
        for line, recorded in reconciled.held
    )
    _stage_lines(account_id, import_id, reconciled.fresh, merchants)
    # The held lines' eager ``sightings`` collections and their two merchant
    # projections were loaded BEFORE the rows above existed, and a sighting
    # staged by its ids joins neither a loaded collection nor a loaded
    # subquery answer.  Expire exactly that set, so a reader of a held line's
    # wording, day or merchant in this same unit of work reads the row and
    # not the instance as it was.  ONE spelling of the set
    # (``BankStatementLine.READS_OVER_SIGHTINGS``), shared with the test
    # builder that stages a sighting the same way.
    for _line, recorded in reconciled.held:
        db.session.expire(recorded, BankStatementLine.READS_OVER_SIGHTINGS)


def record_statement(
    account_id: int,
    user_id: int,
    source: StatementSourceEnum,
    file_name: str,
    payload: bytes,
) -> ImportOutcome:
    """Record what a statement said, once.

    The whole import: its decision, every refusal included, is
    :func:`plan_statement`'s, and only after it returns does anything get
    staged -- this performs that plan.

    **Re-importing an overlapping span records a SIGHTING of every line it
    already holds and records the ACT** (ruling **R-BI10**).  The line stays
    one line; this import's window now also vouches for its days; and the
    receipt's ``recorded_count`` reports honestly that it added nothing.

    Args:
        account_id: The account the user chose.  The CALLER has already proven
            the requesting user owns it -- this door takes an id and does no
            ownership check, exactly as every other service door here does
            (``app/utils/auth_helpers.py`` is the route-side rule).
        user_id: Who performed the import.
        source: Which adapter reads the bytes.
        file_name: The uploaded file's own name, kept as provenance.
        payload: Its raw bytes.

    Returns:
        The :class:`ImportOutcome`.

    Raises:
        StatementParseError: The file is not the shape the adapter reads, or
            states one of its own ids twice.
        StatementIntegrityError: The file's running balances do not follow
            from its own lines.
        StatementBalanceUnexplained: The file states a balance that no day it
            covers reconciles with what is already known.
        StatementAccountMismatch: The file is for a different account.
        StatementLineIdMoved: This source restates a line's day or amount
            under an id it already holds.
        StatementLineConflict: This source restates a line's wording.
    """
    plan = plan_statement(account_id, user_id, source, payload)
    parsed, source_id = plan.parsed, plan.source_id
    reconciled, balance = plan.reconciled, plan.balance

    # Every refusal is now behind us, so this is the first write.
    if plan.identity_is_new:
        record_identity(
            account_id, user_id, source_id, parsed.external_account_id,
        )

    statement_import = StatementImport(
        account_id=account_id,
        user_id=user_id,
        source_id=source_id,
        file_name=file_name[:255],
        file_digest=hashlib.sha256(payload).hexdigest(),
        declared_start=parsed.declared_start,
        declared_end=parsed.declared_end,
        # The bank's OWN claim, verbatim.  The claim and the day it is FOR
        # are two facts (ruling **R-GF**): SECU writes the figure as of the
        # export INSTANT and labels it with the export's day, so on the
        # developer's 2026-08-16 file these two columns read 08-16 and the
        # level below reads 08-13.
        stated_balance=parsed.stated_balance,
        stated_balance_on=parsed.stated_balance_on,
    )
    db.session.add(statement_import)
    # The sightings carry the import's id in a composite key, so the import
    # row must exist before they are staged.
    db.session.flush()

    _write_records(account_id, statement_import.id, reconciled)
    # **What the import made of the claim is a LEVEL** (plan step
    # ``balance:X-bj-1``, ruling **R-IS**): the day the figure is the balance
    # for and how firmly it is held, as a row in the same relation the
    # owner's true-ups occupy, naming this import.  Its amount is the claim
    # -- the key ``fk_anchor_history_statement_import_claim`` would refuse
    # anything else -- and its evidence is what the solve above worked out.
    # Written after the lines because it is a conclusion drawn from them,
    # and inside the declared window by ``budget.level_lies_within_file``.
    if balance is not None and balance.is_anchored:
        db.session.add(AccountAnchorHistory(
            account_id=account_id,
            anchor_balance=balance.stated,
            observed_on=balance.effective_on,
            evidence_id=ref_cache.statement_balance_evidence_id(
                balance.evidence,
            ),
            statement_import_id=statement_import.id,
        ))
    # **Every anchor these fresh lines undercut is RELEASED**, and it happens
    # after the staging so the earliest fresh day is known.  An anchor solved
    # before a line at or before its own day was recorded was solved without
    # that line, and an adversarial review reproduced one storing a day two
    # days early under a *corroborated* badge because of it.  This import's
    # OWN anchor is not among them: it was solved against its own complete
    # line list, so it accounts for every line staged here.  **The count is
    # KEPT** (plan step ``bank_import:X-gr``, finding **BI-488**): it is what
    # tells the receipt that this file took a checked balance away from an
    # earlier import, which until then the owner learned only from the imports
    # table's badge.
    anchors_released = (
        release_anchors_from(
            account_id,
            min(keyed.line.posted_on for keyed in reconciled.fresh),
            statement_import.id,
        )
        if reconciled.fresh else 0
    )
    db.session.flush()

    # The receipt's two counts come from the aggregate every other surface
    # reads, so the receipt, the imports table and the hero cannot part.  A
    # file with no line wrote no sighting and reads ``(0, 0)``.
    counted = db.session.execute(
        StatementLineSighting.counts_by_import(account_id)
        .where(StatementLineSighting.import_id == statement_import.id),
    ).one_or_none()
    return ImportOutcome(
        import_id=statement_import.id,
        line_count=0 if counted is None else counted.sighted,
        recorded_count=0 if counted is None else counted.first,
        declared_start=parsed.declared_start,
        declared_end=parsed.declared_end,
        balance=balance,
        anchors_released=anchors_released,
    )
