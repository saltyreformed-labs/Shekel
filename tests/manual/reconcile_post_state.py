"""What the reconcile's acts are, and the post-state they must leave: the half that only reads.

Plan step **balance:X-bk-2** (plan#1192), task plan#2416, split from :mod:`reconcile_rehearsal`
on its seam (the two stood at 1,085 lines together).  This half WRITES NOTHING: it reads the ACT
SHEET (``tests/manual/reconcile_act_sheet.py``, plan#2372) into the acts it names
(:func:`acts_of`), reads a copy before act 1 into a :class:`Snapshot`, and grades a copy after
the acts (:func:`post_state_problems`) -- each act's own arm, the untouched items against the
snapshot, and plan#1192's DONE TEST (:func:`_done_test_problems`).  The rehearsal calls it
after its own last act, and its ``--check`` on a copy the owner performed the acts on, so the
two modes grade one set of arms.  Pressing the app's doors is :mod:`reconcile_rehearsal`'s.

It runs inside whichever app the rehearsal built, production's ``app/`` (the rehearsal's own
docstring says why); every figure it states goes only to the rehearsal's output.
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.account import Account
from app.models.statement_import import (
    BankStatementLine,
    StatementImport,
    StatementLineSighting,
)
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import balance_at, outstanding_difference
from app.services.outstanding_difference import StretchKind

from reconcile_residue import ZERO, residue_of


#: The one act ruling **plan#2433** holds back, by its transfer: SECU's
#: 2026-04-22 Van Loan line, whose match to transfer 322 moves the payment
#: onto the day Van Loan's books open, which the accept door refuses and no
#: door can move (measured 2026-10-30).  Step plan#2436 performs it.
HELD_BACK = {322: "step plan#2436 (ruling plan#2433)"}


#: The kinds of the Reconcile page's row token that carry a MOVEMENT's id
#: (``kind:id:figure:revision``): an envelope's purchase line, and a row's or
#: a settled transfer's own payment.  A still-Projected transfer is offered as
#: its ``leg`` (measured 2026-10-30 on the copy's MATCH panes).
_MOVEMENT_TOKENS = frozenset({"purchase", "settlement"})


_LEG_TOKEN = "leg"


@dataclass(frozen=True)
class MatchAct:
    """One SECU line matched on the Reconcile page to what the sheet names.

    Attributes:
        title: The act, as the sheet prints it.
        line: The act sheet's line.
        row: The act sheet's row, or ``None`` for a transfer still Projected.
        transfer_id: The transfer, or ``None`` for a TIMING pair.
    """

    title: str
    line: dict
    row: "dict | None"
    transfer_id: "int | None"

    def offered_as(self, token: str) -> bool:
        """Return whether the page's row *token* is this act's row."""
        kind, ident = token.split(":")[:2]
        if self.row is None:
            return kind == _LEG_TOKEN and int(ident) == self.transfer_id
        return kind in _MOVEMENT_TOKENS and int(ident) == self.row["entry_id"]


class Snapshot(NamedTuple):
    """The copy before the first act: its ids, and what the acts must leave standing.

    What the acts CREATED -- the import's lines, the purchases the owner's
    rules filed, the matches -- is the difference against its ids, and
    ``--check`` of a copy the owner performed the acts on reads this file of
    the same dump rather than trusting anything an operator types (the
    account-10 rehearsal's snapshot, commit ``01ebee2b8``).  **It also holds
    the state two arms grade a performed copy against**, which no post-state
    knows: the untouched items' (:func:`_standing`) and each envelope's that
    an act removes a line from (:func:`envelope_state`), both in their JSON
    form (:func:`jsonable`) so the file and a live read compare equal.

    Attributes:
        transactions: Every ``budget.transactions`` id, deleted rows too.
        deleted: The ``transactions`` already soft-deleted.
        entries: Every ``budget.transaction_entries`` id.
        lines: Every ``budget.bank_statement_lines`` id.
        matches: Every ``budget.statement_matches`` id.
        standing: The untouched items' state, by :func:`_standing`'s keys.
        envelopes: Each delete act's envelope state, by its row's id.
        captured_at: The database's ``now()`` when it was taken.
    """

    transactions: frozenset
    deleted: frozenset
    entries: frozenset
    lines: frozenset
    matches: frozenset
    standing: dict
    envelopes: dict
    captured_at: datetime

    @classmethod
    def capture(cls, acts_sheet: dict, acts: tuple) -> "Snapshot":
        """Read this copy now, for the acts *acts_sheet* names (:func:`acts_of`)."""
        def ids(column, *where):
            return frozenset(db.session.execute(db.select(column).where(*where)).scalars())
        _, _, deletes, held = acts
        return cls(
            ids(Transaction.id), ids(Transaction.id, Transaction.is_deleted.is_(True)),
            ids(TransactionEntry.id), ids(BankStatementLine.id), ids(StatementMatch.id),
            jsonable(_standing(acts_sheet, held)),
            {str(row["transaction_id"]): jsonable(envelope_state(
                row["transaction_id"], deleted_lines(deletes)))
             for row in deletes},
            db.session.execute(db.select(db.func.now())).scalar_one(),
        )

    @classmethod
    def read(cls, path: Path) -> "Snapshot":
        """Read the snapshot a rehearsal wrote.

        Raises:
            SystemExit: When the file lacks a field this file writes: an older
                version wrote it, and it cannot grade the arms that read one.
        """
        held = json.loads(path.read_text(encoding="utf-8"))
        absent = sorted(set(cls._fields) - set(held))
        if absent:
            raise SystemExit(
                f"--snapshot {path} carries no {', '.join(absent)}: an older version of "
                "this file wrote it; rehearse again from this checkout"
            )
        return cls(
            *(frozenset(held[name]) for name in cls._fields[:5]),
            held["standing"], held["envelopes"],
            datetime.fromisoformat(held["captured_at"]),
        )

    def write(self, path: Path) -> None:
        """Write it for the check to read.

        Args:
            path: Where, from :func:`door_rehearsal.new_file`.
        """
        path.write_text(json.dumps({
            "transactions": sorted(self.transactions), "deleted": sorted(self.deleted),
            "entries": sorted(self.entries), "lines": sorted(self.lines),
            "matches": sorted(self.matches),
            "standing": self.standing, "envelopes": self.envelopes,
            "captured_at": self.captured_at.isoformat(),
        }) + "\n", encoding="utf-8")


def jsonable(value):
    """Return *value* as its JSON form: a day and a figure as text, a tuple as a list.

    A state read live and the same state read back from the snapshot file are
    compared in this form, so neither side's types decide the comparison.
    """
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, (date, Decimal)):
        return str(value)
    return value


def named(movement: dict) -> str:
    """Return a sheet row or line as the performance sheet names it."""
    if "posted_on" in movement:
        return (f"SECU line {movement['posted_on']} {movement['amount']} "
                f"\"{movement['description']}\"")
    # A transfer's side has no row of its own, only its line's name.
    parts = list(dict.fromkeys(part for part in (movement["row"], movement["line"]) if part))
    return f"\"{': '.join(parts)}\" {movement['day']} {movement['amount']}"


def acts_of(sheet: dict) -> "tuple[list[MatchAct], list[dict], list[dict], list[dict]]":
    """Return the sheet's acts: matches, cancels, deletes, and the held back.

    Raises:
        SystemExit: When :data:`HELD_BACK` names a transfer the sheet does not
            match, or the sheet matches a SETTLED transfer it does not hold
            back -- both mean this file and the sheet disagree about what
            ruling plan#2433 holds back, and neither is guessed.
    """
    matches = [
        MatchAct(f"Match {named(pair['line'])} to {named(pair['row'])}",
                 pair["line"], pair["row"], None)
        for pair in sheet["timing"]
    ]
    held = [pair for pair in sheet["transfer"] if pair["transfer_id"] in HELD_BACK]
    if {pair["transfer_id"] for pair in held} != set(HELD_BACK):
        raise SystemExit(f"the act sheet matches none of {sorted(HELD_BACK)}: is it the "
                         "sheet ruling plan#2433 was made on?")
    for pair in sheet["transfer"]:
        if pair in held:
            continue
        if pair["row"] is not None:
            raise SystemExit(f"the act sheet matches settled transfer {pair['transfer_id']}, "
                             "which no ruling has rehearsed; derive the sheet again or ask")
        matches.append(MatchAct(
            f"Match {named(pair['line'])} to transfer {pair['transfer_id']}",
            pair["line"], None, pair["transfer_id"],
        ))
    rows = [item["row"] for item in sheet["never_moved"]]
    cancels = [row for row in rows if not row["envelope_line"]]
    deletes = [row for row in rows if row["envelope_line"]]
    return matches, cancels, deletes, held


def line_id_of(account_id: int, line: dict) -> "int | None":
    """Return the recorded id of the sheet's *line*, or ``None`` when unrecorded.

    A line only the export held carries no id on the sheet; once the import
    records it, it is the one line of the account on its day and figure that a
    sighting describes as the sheet does.

    Raises:
        AssertionError: When more than one recorded line answers to it.
    """
    if line["line_id"] is not None:
        return line["line_id"]
    found = db.session.execute(
        db.select(BankStatementLine.id).distinct().join(
            StatementLineSighting, StatementLineSighting.line_id == BankStatementLine.id,
        ).where(
            BankStatementLine.account_id == account_id,
            BankStatementLine.posted_on == date.fromisoformat(line["posted_on"]),
            BankStatementLine.amount == Decimal(line["amount"]),
            StatementLineSighting.description == line["description"],
        )
    ).scalars().all()
    assert len(found) <= 1, f"{named(line)} answers to lines {found}"
    return found[0] if found else None


def match_of(line_id: int) -> "int | None":
    """Return the match line *line_id* is a member of, or ``None``."""
    return db.session.execute(
        db.select(StatementMatchMember.match_id).where(
            StatementMatchMember.bank_statement_line_id == line_id,
        )
    ).scalar_one_or_none()


def _line_match(account_id: int, line: dict) -> "int | None":
    """Return the match the sheet's *line* is a member of, or ``None``.

    ``None`` too while the line is unrecorded: a line only the export holds
    has no id until act 1 records it (:func:`line_id_of`), and nothing is
    matched to it before then.  So :func:`match_of` is never asked about a
    missing id, whose ``IS NULL`` answers every movement-side member and
    raised at :meth:`Snapshot.capture` (measured 2026-10-31).
    """
    line_id = line_id_of(account_id, line)
    return None if line_id is None else match_of(line_id)


def _live_row(txn_id: int) -> "Transaction | None":
    """Return row *txn_id*, or ``None`` when it is gone: deleted outright, or soft-deleted.

    The app deletes rows, softly or outright: the card's Delete, which the
    owner could press beside Cancel (``transaction_service._delete``), the
    recurrence engine's retire, a payback's revert.  So an arm answers a row's
    absence as a sentence rather than raising on it: the check raised
    ``AttributeError`` on a cancelled row deleted outright from a performed
    copy (measured 2026-10-31).
    """
    txn = db.session.get(Transaction, txn_id)
    return None if txn is None or txn.is_deleted else txn


def _entry_matched(entry_id: int) -> bool:
    """Return whether movement *entry_id* is a member of any match."""
    return db.session.execute(
        db.select(StatementMatchMember.id).where(
            StatementMatchMember.transaction_entry_id == entry_id,
        )
    ).first() is not None


def _status_is(txn_or_transfer, status: StatusEnum) -> bool:
    """Return whether a row or transfer holds *status*."""
    return txn_or_transfer.status_id == ref_cache.status_id(status)


def moved_markers(account_id: int, digest: str, acts: tuple) -> "list[str]":
    """Return every act whose PRE-STATE has moved on this copy, one sentence each.

    The markers a partly performed copy trips: the export already imported
    (by its digest, which the import stores), a timing row already matched or
    re-dated, a transfer no longer Projected, a row no longer Paid, an envelope
    line gone or matched.  Markers, not a proof of the whole pre-state.

    Returns:
        Empty on a copy no act has touched.
    """
    matches, cancels, deletes, _ = acts
    problems = [
        f"the SECU export is already imported (import {found})"
        for found in db.session.execute(db.select(StatementImport.id).where(
            StatementImport.account_id == account_id,
            StatementImport.file_digest == digest,
        )).scalars()
    ]
    for act in matches:
        if _line_match(account_id, act.line) is not None:
            problems.append(f"{named(act.line)} is already matched")
        if act.row is None:
            transfer = db.session.get(Transfer, act.transfer_id)
            if transfer is None or transfer.is_deleted or \
                    not _status_is(transfer, StatusEnum.PROJECTED):
                problems.append(f"transfer {act.transfer_id} is not a live Projected one")
            continue
        entry = db.session.get(TransactionEntry, act.row["entry_id"])
        if entry is None or _entry_matched(entry.id) or \
                entry.settled_on != date.fromisoformat(act.row["day"]):
            problems.append(f"movement {act.row['entry_id']} is gone, matched or re-dated")
    for row in cancels:
        txn = _live_row(row["transaction_id"])
        if txn is None or not _status_is(txn, StatusEnum.DONE):
            problems.append(f"row {row['transaction_id']} is not a live Paid row")
    for row in deletes:
        if db.session.get(TransactionEntry, row["entry_id"]) is None or \
                _entry_matched(row["entry_id"]):
            problems.append(f"envelope line {row['entry_id']} is gone or matched")
    return problems


def _standing(sheet: dict, held: "list[dict]") -> dict:
    """Return the state of every movement no act may touch: card items and the held back.

    A row by its movement: whether it exists, its day, its figure and whether
    it is matched; a line by its match (:func:`_line_match`), keyed by its
    place on the sheet, because a line only the export holds has no id before
    act 1 records it, and two such lines can share a day, a figure and a
    description.  Read before the first act and after the last, and the two
    must be equal.
    """
    account_id = sheet["account"]["id"]
    state = {}
    for index, pair in enumerate(held, 1):
        state[f"held-back act {index}'s {named(pair['line'])}"] = _line_match(
            account_id, pair["line"])
        if pair["row"] is not None:
            state[f"movement {pair['row']['entry_id']}"] = _movement_state(
                pair["row"]["entry_id"])
    for index, item in enumerate(sheet["card"], 1):
        if item["kind"] == "payment":
            state[f"card item {index}'s {named(item['line'])}"] = _line_match(
                account_id, item["line"])
        else:
            state[f"movement {item['row']['entry_id']}"] = _movement_state(
                item["row"]["entry_id"])
    return state


def _movement_state(entry_id: int) -> "tuple | None":
    """Return what an act could change about movement *entry_id*."""
    entry = db.session.get(TransactionEntry, entry_id)
    if entry is None:
        return None
    return (entry.settled_on, entry.amount, _entry_matched(entry_id))


def imports_of(account_id: int, digest: str) -> "list[StatementImport]":
    """Return every import of the export whose digest is *digest*, oldest first.

    More than one is the same file submitted again, which the import door
    records as an import of its own that adds no line (measured 2026-10-31 on
    a rehearsed copy: a second import, and "Nothing new: all 509 line(s) in
    this file were already recorded").
    """
    return list(db.session.execute(db.select(StatementImport).where(
        StatementImport.account_id == account_id, StatementImport.file_digest == digest,
    ).order_by(StatementImport.id)).scalars())


def import_problems(account_id: int, digest: str, books_last: date) -> "list[str]":
    """Return why act 1 has not landed: the export imported once, covering the books' last day."""
    found = imports_of(account_id, digest)
    if not found:
        return ["the import: SECU's export is not imported"]
    if len(found) > 1:
        return [f"the import: SECU's export is imported {len(found)} times "
                f"(imports {[each.id for each in found]}), where act 1 imports it once"]
    if not found[0].declared_start <= books_last <= found[0].declared_end:
        return [f"the import: import {found[0].id} does not cover the books' last day "
                f"{books_last}"]
    return []


def act_movements(account_id: int, act: MatchAct) -> "set[int]":
    """Return the movement(s) *act* matches: the sheet's row, or the transfer's side."""
    if act.row is not None:
        return {act.row["entry_id"]}
    return set(db.session.execute(db.select(TransactionEntry.id).where(
        TransactionEntry.account_id == account_id,
        (TransactionEntry.expense_transfer_id == act.transfer_id)
        | (TransactionEntry.income_transfer_id == act.transfer_id),
    )).scalars())


def match_problems(account_id: int, act: MatchAct) -> "list[str]":
    """Return why *act* has not landed: its line matched to its movement, on the line's day."""
    line_id = line_id_of(account_id, act.line)
    if line_id is None:
        return [f"{act.title}: SECU's line is not recorded"]
    match_id = match_of(line_id)
    if match_id is None:
        return [f"{act.title}: line {line_id} is not matched"]
    members = set(db.session.execute(db.select(StatementMatchMember.transaction_entry_id).where(
        StatementMatchMember.match_id == match_id,
        StatementMatchMember.transaction_entry_id.is_not(None),
    )).scalars())
    entry_ids = act_movements(account_id, act)
    if members != entry_ids or not entry_ids:
        return [f"{act.title}: line {line_id}'s match {match_id} holds {sorted(members)}, "
                f"not {sorted(entry_ids)}"]
    posted_on = db.session.get(BankStatementLine, line_id).posted_on
    astray = sorted(entry_id for entry_id in entry_ids
                    if db.session.get(TransactionEntry, entry_id).settled_on != posted_on)
    if astray:
        return [f"{act.title}: movement(s) {astray} not dated the bank's {posted_on}"]
    return []


def cancel_problems(txn_id: int) -> "list[str]":
    """Return why row *txn_id*'s cancel has not landed: Cancelled, holding no movement."""
    txn = _live_row(txn_id)
    if txn is None:
        return [f"the cancel of row {txn_id}: it is gone"]
    if not _status_is(txn, StatusEnum.CANCELLED):
        return [f"the cancel of row {txn_id}: it is not Cancelled"]
    moving = db.session.execute(db.select(TransactionEntry.id).where(
        TransactionEntry.transaction_id == txn_id, TransactionEntry.settled_on.is_not(None),
    )).scalars().all()
    if moving:
        return [f"the cancel of row {txn_id}: it still holds movement(s) {moving}"]
    return []


def deleted_lines(deletes: "list[dict]") -> "frozenset[int]":
    """Return every envelope line the sheet's delete acts remove."""
    return frozenset(row["entry_id"] for row in deletes)


def envelope_state(txn_id: int, gone: "frozenset[int]") -> dict:
    """Return what removing lines must leave standing in envelope *txn_id*.

    The envelope's status, its settle day with the day's basis and clearing
    link, and each of its lines but those the sheet deletes (*gone*) by its
    figure, days, basis, link and whether it is matched: the reopen and
    re-close of ruling plan#2439 pass through all of them, and the ruling was
    made on their ending exactly as they began.  The envelope's own state is
    ``None`` once it is gone (:func:`_live_row`).
    """
    txn = _live_row(txn_id)
    others = db.session.execute(db.select(TransactionEntry).where(
        TransactionEntry.transaction_id == txn_id, TransactionEntry.id.not_in(gone),
    ).order_by(TransactionEntry.id)).scalars()
    return {
        "row": None if txn is None else (
            txn.status_id, txn.settled_on, txn.settled_day_basis_id, txn.reconciled_by_id),
        "lines": {
            other.id: (other.amount, other.purchased_on, other.settled_on,
                       other.settled_day_basis_id, other.reconciled_by_id,
                       _entry_matched(other.id))
            for other in others
        },
    }


def delete_problems(
    txn_id: int, entry_id: int, gone: "frozenset[int]", before: dict,
) -> "list[str]":
    """Return why removing line *entry_id* has not landed, or moved what it may not.

    The line is gone; the envelope's own state is *before*'s, and so is each
    line *before* holds.  A line *before* does not hold is not graded here: the
    import files new lines into an open envelope (measured 2026-10-30: three
    into row 2402), and a snapshot taken before it cannot hold them.

    Args:
        txn_id: The envelope.
        entry_id: The line removed.
        gone: Every line the sheet deletes, left out of both states.
        before: :func:`envelope_state` in JSON form, read before the act.
    """
    problems = []
    if db.session.get(TransactionEntry, entry_id) is not None:
        problems.append(f"the delete of line {entry_id}: it is still in envelope {txn_id}")
    after = jsonable(envelope_state(txn_id, gone))
    if after["row"] is None:
        return problems + [f"the delete of line {entry_id}: envelope {txn_id} is gone"]
    if after["row"] != before["row"]:
        problems.append(f"the delete of line {entry_id}: envelope {txn_id} moved from "
                        f"{before['row']} to {after['row']}")
    moved = sorted(line for line, state in before["lines"].items()
                   if after["lines"].get(line) != state)
    if moved:
        problems.append(f"the delete of line {entry_id}: envelope {txn_id}'s line(s) "
                        f"{moved} moved")
    return problems


def left_residues(acts_sheet: dict, held: "list[dict]") -> "dict[date, Decimal]":
    """Return what each day is apart by once every act is performed: the acts left undone.

    The card items (ruling plan#2343) and the act ruling plan#2433 holds back
    are the only movements the sheet names that no act answers, so a day they
    sit on is apart by exactly their residue (:func:`reconcile_residue.residue_of`)
    and every other day the sheet classed agrees.  A day where they come to
    nothing is left out: it agrees.
    """
    rows: "dict[str, list[Decimal]]" = defaultdict(list)
    lines: "dict[str, list[Decimal]]" = defaultdict(list)
    for item in acts_sheet["card"]:
        if item["kind"] == "payment":
            lines[item["line"]["posted_on"]].append(Decimal(item["line"]["amount"]))
        else:
            rows[item["row"]["day"]].append(Decimal(item["row"]["amount"]))
    for pair in held:
        lines[pair["line"]["posted_on"]].append(Decimal(pair["line"]["amount"]))
        if pair["row"] is not None:
            rows[pair["row"]["day"]].append(Decimal(pair["row"]["amount"]))
    left = {
        date.fromisoformat(day): residue_of(rows[day], lines[day])
        for day in set(rows) | set(lines)
    }
    return {day: residue for day, residue in left.items() if residue != ZERO}


def _done_test_problems(account_id: int, acts_sheet: dict, held: "list[dict]") -> "list[str]":
    """Return why plan#1192's done test fails on this copy, one sentence a day.

    **The test, as rulings plan#2343 and plan#2433 amended it**: on the
    account's disagreement list
    (:func:`app.services.outstanding_difference.disagreement_list`), every
    books day still apart is apart by exactly what :func:`left_residues`
    leaves on it, and every other books day agrees.  **A day the list does
    not COMPARE is not a day that agrees**, so a books stretch with no
    statement yet, or one the comparison does not draw, fails it too.  The
    list is graded over the sheet's own books span, and a list whose span has
    moved since the sheet was derived -- a later balance recorded -- is
    refused rather than graded: its new days are no act's.
    """
    account = db.session.get(Account, account_id)
    listing = outstanding_difference.disagreement_list(
        account, balance_at.BalanceContext.build(account.user_id))
    if listing is None:
        return ["the done test: the account has no disagreement list"]
    span = (listing.books.first_day, listing.books.last_day)
    derived = tuple(date.fromisoformat(acts_sheet["books"][end])
                    for end in ("first_day", "last_day"))
    if span != derived:
        return [f"the done test: the books span is {span[0]}..{span[1]}, not the sheet's "
                f"{derived[0]}..{derived[1]}; derive the act sheet again"]
    problems = [
        f"the done test: the books days {stretch.verdict.first_day}.."
        f"{stretch.verdict.last_day} are not compared ({stretch.kind.value})"
        for stretch in listing.stretches
        if stretch.kind in (StretchKind.NO_STATEMENT, StretchKind.NOT_COMPARED)
    ]
    apart = {
        day.comparison.day: day.comparison.residue
        for stretch in listing.stretches if stretch.kind is StretchKind.DISAGREES
        for day in stretch.days
    }
    left = left_residues(acts_sheet, held)
    problems += [
        f"the done test: {day} is apart by {apart.get(day, ZERO)}, and the acts left "
        f"undone on it come to {left.get(day, ZERO)}"
        for day in sorted(set(apart) | set(left))
        if apart.get(day, ZERO) != left.get(day, ZERO)
    ]
    return problems


def post_state_problems(
    account_id: int, acts_sheet: dict, acts: tuple, snapshot: Snapshot, digest: str,
) -> "list[str]":
    """Grade every arm the post-state answers: the one set both modes call (rule 14).

    The rehearsal calls it after its last act and ``--check`` on a copy the
    owner performed the acts on, so the two cannot grade different things.
    Each act's own arm again -- an act asserted only as it landed grades a
    rehearsal and never a performance (the account-10 check's lesson, commit
    ``01ebee2b8``) -- then the untouched items against the snapshot, then the
    done test, which grades the money on every books day.

    Returns:
        Every failed arm's sentence; empty when the post-state holds.
    """
    matches, cancels, deletes, held = acts
    problems = import_problems(
        account_id, digest, date.fromisoformat(acts_sheet["books"]["last_day"]))
    for act in matches:
        problems += match_problems(account_id, act)
    for row in cancels:
        problems += cancel_problems(row["transaction_id"])
    gone = deleted_lines(deletes)
    for row in deletes:
        problems += delete_problems(
            row["transaction_id"], row["entry_id"], gone,
            snapshot.envelopes[str(row["transaction_id"])])
    standing, before = jsonable(_standing(acts_sheet, held)), snapshot.standing
    # A key one side holds and the other does not is a snapshot this act sheet
    # did not write, or one an older version of this file keyed otherwise; it
    # fails rather than letting the item go ungraded.
    problems += [
        f"no act may touch {key}, and it moved: {before[key]} -> {state}" if key in before
        else f"no act may touch {key}, and the snapshot does not hold it"
        for key, state in standing.items() if key not in before or before[key] != state
    ]
    problems += [f"the snapshot holds {key}, which the act sheet does not name"
                 for key in sorted(set(before) - set(standing))]
    return problems + _done_test_problems(account_id, acts_sheet, held)
