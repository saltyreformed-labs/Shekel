"""Perform the reconcile's non-card acts through the app's own doors, on a production copy.

Plan step **balance:X-bk-2** (plan#1192), its task plan#2414, on rulings **plan#2343** (the card
days are plan#2348's), **plan#2359** (a Checking row on neither export never moved),
**plan#2426** (a row a Capital One charge could be is card spending) and **plan#2433** (the
April Van Loan match waits on step plan#2436).  The ACT SHEET
(``tests/manual/reconcile_act_sheet.py``, plan#2372) says what each act is; this performs every
one of them that is not the card step's, as the owner does, through the pages the owner
presses, in the order the doors force:

1. **The import** (:func:`_import`): SECU's export through the statements door, so an import
   covers the books' last day and the lines only the export held are recorded.  **That door
   also FILES**: the owner's standing rules turn the new swipe lines they answer for into
   purchases (ruling R-GH).  Measured on the 2026-10-30 copy: 5 of its 14 new lines, the two
   Smith's Nursery charges among them, so the never-moved Smith's farm line is replaced rather
   than left waiting; none of the five TIMING lines.  The sheet records what it filed.
2. **The matches**, on the Reconcile page (:func:`_match`): each TIMING pair, the row the page
   itself proposes for SECU's line once the import has recorded it, then each TRANSFER line
   but the one ruling plan#2433 holds back (:data:`HELD_BACK`).
3. **The never-moved rows** (:func:`_cancel`, :func:`_delete`): each row cancelled, which is
   TWO presses of its full edit, Status to Projected and then to Cancelled, because the cancel
   door takes only a Projected row and every one of these is Paid; and each envelope line
   deleted by its X.  **The line list renders no X under a Paid envelope**
   (``purchases_closed`` in ``grid/_transaction_entries.html``), and 6 of the sheet's 7 lines
   sit under one, so on ruling **plan#2439** such a line is removed as that list's own notice
   says: Status to Projected, the X, Status to Paid, then "Money moved on" set back to the day
   the envelope carried.  Measured 2026-10-31 on copies of the acts-1-16 restore: the round
   trip moves no other line, re-dates only the envelope's own settle day, and with the day
   typed back ends exactly where a DELETE of the line alone does.

The CARD items, and the act ruling plan#2433 holds back, are named on the sheet, and no act
touches them: their state is read before the first act and after the last
(:func:`_standing`), and a difference stops the run.

**IT WRITES.**  Before its first write it refuses the name ``shekel``
(:func:`door_rehearsal.connect`) and any copy on which an act's pre-state has moved, whatever
it is called (:func:`_moved`), naming every moved marker -- nothing here is idempotent, so a
partly performed copy is refused at act zero.  Then it writes a SNAPSHOT of the copy's ids
(:class:`Snapshot`), which plan#2416's read-only check of a copy the owner performed the acts
on reads, and after each act it asserts the act LANDED, reading the database rather than the
response: the Reconcile page answers a refused card with a 200 and a receipt saying so
(measured 2026-10-30 on the April Van Loan match).

**Every figure goes to the PERFORMANCE SHEET** (ruling **R-BAL249**), a file outside every
checkout (:func:`door_rehearsal.new_file`), saved even when a run stops; this file states
structure only -- ids, the one held-back act, the order.

**It runs PRODUCTION's ``app/``**, because the owner performs these acts on production: export
the deployed revision's ``app/`` outside every checkout and put it first on ``PYTHONPATH``.  This
file and :mod:`door_rehearsal` come from this checkout, and the act sheet is read as a FILE,
never through its module, which needs this branch's ``app/``.  ``SECRET_KEY`` may be any value:
the owner's session is forged (:class:`door_rehearsal.Operator`)::

    git archive <the deployed revision> app | tar -x -C <a folder outside every checkout>
    LC_ALL=C.UTF-8 SECRET_KEY=<any> DATABASE_URL=postgresql://.../<the copy> \\
        PYTHONPATH=<that folder> python tests/manual/reconcile_rehearsal.py \\
        --clone <the copy> --account <its id> \\
        --acts ~/projects/shekel-handoffs/<folder>/act_sheet.json \\
        --secu <SECU's CSV export> \\
        --sheet ~/projects/shekel-handoffs/<folder>/performance_sheet.md \\
        --snapshot ~/projects/shekel-handoffs/<folder>/snapshot.json
"""

import argparse
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

from flask import Flask

import app as app_package
from app import ref_cache
from app.enums import StatementSourceEnum, StatusEnum
from app.extensions import db
from app.models.account import Account
from app.models.statement_import import (
    BankStatementLine,
    StatementImport,
    StatementLineSighting,
)
from app.models.statement_match import (
    StatementMatch,
    StatementMatchCreation,
    StatementMatchMember,
)
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services.statement_match import Tab

from door_rehearsal import Operator, RefusedError, Sheet, connect, forms_in, new_file


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
    """Every id the copy held before the first act, and the database's clock.

    What the acts CREATED -- the import's lines, the purchases the owner's
    rules filed, the matches -- is the difference against this, and plan#2416's
    check of a copy the owner performed the acts on reads this file of the
    same dump rather than trusting any id an operator types (the account-10
    rehearsal's snapshot, commit ``01ebee2b8``).

    Attributes:
        transactions: Every ``budget.transactions`` id, deleted rows too.
        deleted: The ``transactions`` already soft-deleted.
        entries: Every ``budget.transaction_entries`` id.
        lines: Every ``budget.bank_statement_lines`` id.
        matches: Every ``budget.statement_matches`` id.
        captured_at: The database's ``now()`` when it was taken.
    """

    transactions: frozenset
    deleted: frozenset
    entries: frozenset
    lines: frozenset
    matches: frozenset
    captured_at: datetime

    @classmethod
    def capture(cls) -> "Snapshot":
        """Read this copy's ids now."""
        def ids(column, *where):
            return frozenset(db.session.execute(db.select(column).where(*where)).scalars())
        return cls(
            ids(Transaction.id), ids(Transaction.id, Transaction.is_deleted.is_(True)),
            ids(TransactionEntry.id), ids(BankStatementLine.id), ids(StatementMatch.id),
            db.session.execute(db.select(db.func.now())).scalar_one(),
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
            "captured_at": self.captured_at.isoformat(),
        }) + "\n", encoding="utf-8")


def _named(movement: dict) -> str:
    """Return a sheet row or line as the performance sheet names it."""
    if "posted_on" in movement:
        return (f"SECU line {movement['posted_on']} {movement['amount']} "
                f"\"{movement['description']}\"")
    # A transfer's side has no row of its own, only its line's name.
    parts = list(dict.fromkeys(part for part in (movement["row"], movement["line"]) if part))
    return f"\"{': '.join(parts)}\" {movement['day']} {movement['amount']}"


def _acts(sheet: dict) -> "tuple[list[MatchAct], list[dict], list[dict], list[dict]]":
    """Return the sheet's acts: matches, cancels, deletes, and the held back.

    Raises:
        SystemExit: When :data:`HELD_BACK` names a transfer the sheet does not
            match, or the sheet matches a SETTLED transfer it does not hold
            back -- both mean this file and the sheet disagree about what
            ruling plan#2433 holds back, and neither is guessed.
    """
    matches = [
        MatchAct(f"Match {_named(pair['line'])} to {_named(pair['row'])}",
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
            f"Match {_named(pair['line'])} to transfer {pair['transfer_id']}",
            pair["line"], None, pair["transfer_id"],
        ))
    rows = [item["row"] for item in sheet["never_moved"]]
    cancels = [row for row in rows if not row["envelope_line"]]
    deletes = [row for row in rows if row["envelope_line"]]
    return matches, cancels, deletes, held


def _line_id(account_id: int, line: dict) -> "int | None":
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
    assert len(found) <= 1, f"{_named(line)} answers to lines {found}"
    return found[0] if found else None


def _match_of(line_id: int) -> "int | None":
    """Return the match line *line_id* is a member of, or ``None``."""
    return db.session.execute(
        db.select(StatementMatchMember.match_id).where(
            StatementMatchMember.bank_statement_line_id == line_id,
        )
    ).scalar_one_or_none()


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


def _moved(account_id: int, digest: str, acts: tuple) -> "list[str]":
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
        line_id = _line_id(account_id, act.line)
        if line_id is not None and _match_of(line_id) is not None:
            problems.append(f"{_named(act.line)} is already matched")
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
        txn = db.session.get(Transaction, row["transaction_id"])
        if txn is None or txn.is_deleted or not _status_is(txn, StatusEnum.DONE):
            problems.append(f"row {row['transaction_id']} is not a live Paid row")
    for row in deletes:
        if db.session.get(TransactionEntry, row["entry_id"]) is None or \
                _entry_matched(row["entry_id"]):
            problems.append(f"envelope line {row['entry_id']} is gone or matched")
    return problems


def _standing(sheet: dict, held: "list[dict]") -> dict:
    """Return the state of every movement no act may touch: card items and the held back.

    A row by its movement: whether it exists, its day, its figure and whether
    it is matched; a line by its match.  Read before the first act and after
    the last, and the two must be equal.
    """
    state = {}
    for pair in held:
        state[f"line {pair['line']['line_id']}"] = _match_of(pair["line"]["line_id"])
        if pair["row"] is not None:
            state[f"movement {pair['row']['entry_id']}"] = _movement_state(
                pair["row"]["entry_id"])
    for item in sheet["card"]:
        if item["kind"] == "payment":
            state[f"line {item['line']['line_id']}"] = _match_of(item["line"]["line_id"])
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


@dataclass
class Run:
    """What every act needs: the app, the owner's session and the sheet.

    Attributes:
        app: The application, pointed at the copy.
        operator: The owner's session.
        account_id: The account reconciled.
        books_last: The books span's last day, which the import must cover.
        snapshot: The copy's ids before act 1, which tell what the acts made.
    """

    app: Flask
    operator: Operator
    account_id: int
    books_last: date
    snapshot: Snapshot

    @property
    def sheet(self) -> Sheet:
        """The performance sheet."""
        return self.operator.sheet

    def read(self, reader, *args):
        """Return *reader(*args)* from a fresh session, between two requests.

        No app context is held while the operator's requests run, so each one
        is its own unit of work and every read here sees what it committed.
        """
        with self.app.app_context():
            return reader(*args)


def _import(run: Run, number: int, secu: Path) -> None:
    """Act 1: record SECU's export through the statements door, and read what it did."""
    page = f"/accounts/{run.account_id}/statements"
    run.sheet.act(number, "Import SECU's export", f"{page} (Statements: Upload)")
    form = run.operator.form_posting_to(page, page)
    source = StatementSourceEnum.SECU_CHECKING_CSV.value
    run.sheet.typed("Export kind", source)
    run.sheet.typed("Statement file", secu.name)
    payload = {"statement_file": (io.BytesIO(secu.read_bytes()), secu.name)}
    run.operator.submit(form, typed={"source": source, **payload})
    for category, message in run.operator.flashes:
        run.sheet.say(f"- The app said ({category}): {message}")
    run.read(_imported, run, hashlib.sha256(secu.read_bytes()).hexdigest())


def _imported(run: Run, digest: str) -> None:
    """Assert the import landed and covers the books' last day; record what rules filed."""
    found = db.session.execute(db.select(StatementImport).where(
        StatementImport.account_id == run.account_id, StatementImport.file_digest == digest,
    )).scalar_one_or_none()
    assert found is not None, "the import recorded nothing"
    assert found.declared_start <= run.books_last <= found.declared_end, \
        f"import {found.id} does not cover the books' last day {run.books_last}"
    run.sheet.say(f"- LANDED: import {found.id}, {found.declared_start} to "
                  f"{found.declared_end}, covering the books' last day {run.books_last}")
    filed = db.session.execute(db.select(StatementMatch).where(
        StatementMatch.account_id == run.account_id,
        StatementMatch.applied_by_rule.is_(True),
        StatementMatch.id.not_in(run.snapshot.matches),
    ).order_by(StatementMatch.id)).scalars().all()
    run.sheet.say(f"- Your standing rules filed {len(filed)} new line(s) at import:")
    for match in filed:
        run.sheet.say(f"  - {_filed(match.id)}")


def _filed(match_id: int) -> str:
    """Return one rule filing as the sheet names it: the line and where it went."""
    line = db.session.get(BankStatementLine, db.session.execute(
        db.select(StatementMatchMember.bank_statement_line_id).where(
            StatementMatchMember.match_id == match_id,
            StatementMatchMember.bank_statement_line_id.is_not(None),
        )).scalar_one())
    into = db.session.execute(db.select(Transaction.name).join(
        TransactionEntry, TransactionEntry.transaction_id == Transaction.id,
    ).join(
        StatementMatchCreation,
        StatementMatchCreation.transaction_entry_id == TransactionEntry.id,
    ).where(StatementMatchCreation.match_id == match_id)).scalars().all()
    return (f"{line.posted_on} {line.amount} {_description(line.id)}, filed into "
            f"{', '.join(sorted(set(into))) or 'a new row'}")


def _description(line_id: int) -> str:
    """Return the first sighting's description of line *line_id*."""
    return db.session.execute(db.select(StatementLineSighting.description).where(
        StatementLineSighting.line_id == line_id,
    ).order_by(StatementLineSighting.import_id)).scalars().first()


def _card_form(run: Run, line_id: int) -> "tuple[dict, str]":
    """Return the Apply form holding line *line_id*'s card opened, and its page.

    The card is on whichever tab the page puts it; each is asked in turn, with
    the card's MATCH pane rendered in the document (``?open=``).

    Raises:
        AssertionError: When no tab renders an OK box for the line.
    """
    for tab in Tab:
        page = (f"/accounts/{run.account_id}/statements/reconcile"
                f"?tab={tab.value}&open={line_id}")
        response = run.operator.client.get(page)
        assert response.status_code == 200, f"GET {page} -> {response.status_code}"
        for form in forms_in(response.get_data(as_text=True)):
            if ("ok", str(line_id)) in form["unticked"]:
                return form, page
    raise AssertionError(f"no Reconcile tab renders a card for line {line_id}")


def _match(run: Run, number: int, act: MatchAct) -> None:
    """Match one SECU line to the sheet's row on the Reconcile page, and assert it landed.

    **The page's own proposal must BE the sheet's row**: a row the page offers
    beside it ticked is a different act, and the run stops rather than
    untick it.  Where the page proposes nothing the owner ticks the row in the
    MATCH pane, which must offer it.  Then OK on the card, and Apply.
    """
    line_id = run.read(_line_id, run.account_id, act.line)
    assert line_id is not None, f"{_named(act.line)} is not recorded"
    form, page = _card_form(run, line_id)
    run.sheet.act(number, act.title, f"{page} (Reconcile: the line's card, MATCH)")
    rows = f"rows-{line_id}"
    proposed = [value for name, value in form["controls"] if name == rows]
    wanted = [value for value in proposed if act.offered_as(value)] or \
        [value for name, value in form["unticked"] if name == rows and act.offered_as(value)]
    assert wanted, f"the MATCH pane for line {line_id} does not offer the sheet's row"
    stray = [value for value in proposed if not act.offered_as(value)]
    assert not stray, f"the page also proposes {stray} for line {line_id}"
    ticked = [("ok", str(line_id))]
    if wanted[0] in proposed:
        run.sheet.say(f"- The page proposes the row, ticked: {wanted[0]}")
    else:
        ticked.append((rows, wanted[0]))
        run.sheet.say(f"- TICK the row: {wanted[0]}")
    if ("match" not in [value for name, value in form["controls"] if name == f"verb-{line_id}"]):
        ticked.append((f"verb-{line_id}", "match"))
    run.sheet.say("- TICK OK on the card, then press Apply")
    run.operator.submit(form, ticked=tuple(ticked))
    run.read(_matched, run, act, line_id)


def _matched(run: Run, act: MatchAct, line_id: int) -> None:
    """Assert *line_id* is matched to the act's movement, now on the line's day."""
    match_id = _match_of(line_id)
    assert match_id is not None, f"line {line_id} is not matched: the page refused the card"
    members = set(db.session.execute(db.select(StatementMatchMember.transaction_entry_id).where(
        StatementMatchMember.match_id == match_id,
        StatementMatchMember.transaction_entry_id.is_not(None),
    )).scalars())
    if act.row is not None:
        entry_ids = {act.row["entry_id"]}
    else:
        entry_ids = set(db.session.execute(db.select(TransactionEntry.id).where(
            TransactionEntry.account_id == run.account_id,
            (TransactionEntry.expense_transfer_id == act.transfer_id)
            | (TransactionEntry.income_transfer_id == act.transfer_id),
        )).scalars())
    assert members == entry_ids and entry_ids, \
        f"line {line_id}'s match {match_id} holds {members}, not {entry_ids}"
    posted_on = db.session.get(BankStatementLine, line_id).posted_on
    for entry_id in entry_ids:
        settled_on = db.session.get(TransactionEntry, entry_id).settled_on
        assert settled_on == posted_on, \
            f"movement {entry_id} is dated {settled_on}, not the bank's {posted_on}"
    run.sheet.say(f"- LANDED: match {match_id}, movement(s) {sorted(entry_ids)} now dated "
                  f"{posted_on}")


def _save_full_edit(run: Run, txn_id: int, control: str, value: str) -> None:
    """Save row *txn_id*'s full edit once, with the one control the owner changes.

    The caller records the change on the sheet in the screen's words first.

    Args:
        run: The run.
        txn_id: The row.
        control: The control's name in the form.
        value: What the owner types or picks, as the form submits it.
    """
    page = f"/transactions/{txn_id}/full-edit"
    form = run.operator.form(page)
    assert (form["method"], form["url"]) == ("patch", f"/transactions/{txn_id}"), \
        f"{page}'s first form is {form['method']} {form['url']}"
    run.operator.submit(form, typed={control: value})


def _save_status(run: Run, txn_id: int, status: StatusEnum) -> None:
    """Save row *txn_id*'s full edit with Status set to *status*."""
    run.sheet.typed("Status", status.value)
    _save_full_edit(run, txn_id, "status_id", str(ref_cache.status_id(status)))


def _cancel(run: Run, number: int, row: dict) -> None:
    """Cancel one never-moved row: Status to Projected, then to Cancelled, in its full edit."""
    txn_id = row["transaction_id"]
    page = f"/transactions/{txn_id}/full-edit"
    run.sheet.act(number, f"Cancel {_named(row)}", f"{page} (the row's full edit: Status)")
    for status in (StatusEnum.PROJECTED, StatusEnum.CANCELLED):
        _save_status(run, txn_id, status)
    run.read(_cancelled, run, txn_id)


def _cancelled(run: Run, txn_id: int) -> None:
    """Assert row *txn_id* is Cancelled and holds no movement."""
    txn = db.session.get(Transaction, txn_id)
    assert _status_is(txn, StatusEnum.CANCELLED), f"row {txn_id} is not Cancelled"
    moving = db.session.execute(db.select(TransactionEntry.id).where(
        TransactionEntry.transaction_id == txn_id, TransactionEntry.settled_on.is_not(None),
    )).scalars().all()
    assert not moving, f"row {txn_id} still holds movement(s) {moving}"
    run.sheet.say(f"- LANDED: row {txn_id} Cancelled, no movement left")


def _closed_on(txn_id: int) -> "date | None":
    """Return the day envelope *txn_id* closed on, or ``None`` while it is open.

    Closed is the settled band, the predicate the line list withdraws its X on
    (``purchases_closed`` in ``grid/_transaction_entries.html``).
    """
    txn = db.session.get(Transaction, txn_id)
    return txn.settled_on if txn.status.is_settled else None


def _envelope_state(txn_id: int, entry_id: int) -> tuple:
    """Return what removing line *entry_id* must leave standing in envelope *txn_id*.

    The envelope's status, its settle day with the day's basis and clearing
    link, and every OTHER line's figure, days, basis, link and whether it is
    matched: the reopen and re-close of ruling plan#2439 pass through all of
    them, and the ruling was made on their ending exactly as they began.
    """
    txn = db.session.get(Transaction, txn_id)
    others = db.session.execute(db.select(TransactionEntry).where(
        TransactionEntry.transaction_id == txn_id, TransactionEntry.id != entry_id,
    ).order_by(TransactionEntry.id)).scalars()
    return (
        txn.status_id, txn.settled_on, txn.settled_day_basis_id, txn.reconciled_by_id,
        tuple((other.id, other.amount, other.purchased_on, other.settled_on,
               other.settled_day_basis_id, other.reconciled_by_id, _entry_matched(other.id))
              for other in others),
    )


def _delete(run: Run, number: int, row: dict) -> None:
    """Delete one never-moved envelope line by its X, reopening a closed envelope around it.

    A closed envelope's list renders no X, so on ruling **plan#2439** the
    owner reopens it in its full edit, presses the X, closes it again, and
    types back the day it closed on, which the re-close would otherwise move
    to the day of the act.
    """
    txn_id, entry_id = row["transaction_id"], row["entry_id"]
    page = f"/transactions/{txn_id}/entries"
    closed_on = run.read(_closed_on, txn_id)
    door = f"{page} (the envelope's lines: X)"
    if closed_on is not None:
        door = f"/transactions/{txn_id}/full-edit (Status, Money moved on) and {door}"
    run.sheet.act(number, f"Delete {_named(row)}", door)
    before = run.read(_envelope_state, txn_id, entry_id)
    if closed_on is not None:
        _save_status(run, txn_id, StatusEnum.PROJECTED)
    button = run.operator.button(page, "delete", f"{page}/{entry_id}")
    assert not button["vals"].get("shown_lines"), \
        f"the X on line {entry_id} would free bank line(s) {button['vals']['shown_lines']}"
    run.sheet.say("- PRESS the line's X, and confirm")
    run.operator.press(button)
    if closed_on is not None:
        _save_status(run, txn_id, StatusEnum.DONE)
        run.sheet.typed("Money moved on", closed_on.isoformat())
        _save_full_edit(run, txn_id, "settled_on", closed_on.isoformat())
    run.read(_deleted, run, txn_id, entry_id, before)


def _deleted(run: Run, txn_id: int, entry_id: int, before: tuple) -> None:
    """Assert line *entry_id* is gone and envelope *txn_id* otherwise stands as it did."""
    assert db.session.get(TransactionEntry, entry_id) is None, \
        f"envelope line {entry_id} is still there"
    after = _envelope_state(txn_id, entry_id)
    assert after == before, \
        f"removing line {entry_id} moved envelope {txn_id}: {before} -> {after}"
    run.sheet.say(f"- LANDED: envelope line {entry_id} deleted; envelope {txn_id} and its "
                  "other lines stand as they did")


def _left(run: Run) -> None:
    """Name the lines the import recorded that nothing answered: the owner's to explain."""
    left = db.session.execute(db.select(BankStatementLine).where(
        BankStatementLine.account_id == run.account_id,
        BankStatementLine.id.not_in(run.snapshot.lines),
        ~BankStatementLine.id.in_(db.select(StatementMatchMember.bank_statement_line_id).where(
            StatementMatchMember.bank_statement_line_id.is_not(None))),
    ).order_by(BankStatementLine.posted_on, BankStatementLine.id)).unique().scalars().all()
    run.sheet.say("")
    run.sheet.say(f"## Left for you on the Reconcile page: {len(left)} new line(s)")
    for line in left:
        run.sheet.say(f"- {line.posted_on} {line.amount} {_description(line.id)}")


def _named_untouched(sheet: Sheet, acts_sheet: dict, held: "list[dict]") -> None:
    """Print what no act touches: the held-back match and the card items."""
    sheet.say("")
    sheet.say("## Not performed: held back by ruling plan#2433")
    for pair in held:
        sheet.say(f"- {_named(pair['line'])} to transfer {pair['transfer_id']} "
                  f"({_named(pair['row']) if pair['row'] else 'Projected'}): waits on "
                  f"{HELD_BACK[pair['transfer_id']]}")
    sheet.say("")
    sheet.say(f"## Not performed: {len(acts_sheet['card'])} card item(s), left for plan#2348 "
              "(ruling plan#2343)")
    for item in acts_sheet["card"]:
        movement = item.get("row") or item["line"]
        sheet.say(f"- {item['kind']}: {_named(movement)}")


def _perform(run: Run, acts: tuple, secu: Path) -> None:
    """Every act, in the order the doors force: import, matches, cancels, deletes."""
    matches, cancels, deletes, _ = acts
    number = 1
    _import(run, number, secu)
    for act in matches:
        number += 1
        _match(run, number, act)
    for row in cancels:
        number += 1
        _cancel(run, number, row)
    for row in deletes:
        number += 1
        _delete(run, number, row)
    run.read(_left, run)


def _begin(app, account_id: int, secu: Path, acts_sheet: dict, acts: tuple):
    """Refuse a touched copy, then read what the run starts from.

    Returns:
        ``(owner's id, snapshot, the untouched items' state)``.

    Raises:
        SystemExit: When any act's pre-state has moved (:func:`_moved`).
    """
    with app.app_context():
        user_id = db.session.get(Account, account_id).user_id
        problems = _moved(account_id, hashlib.sha256(secu.read_bytes()).hexdigest(), acts)
        if problems:
            raise SystemExit("this copy is not in the pre-act state:\n  "
                             + "\n  ".join(problems))
        return user_id, Snapshot.capture(), _standing(acts_sheet, acts[3])


def _rehearse(run: Run, acts_sheet: dict, acts: tuple, secu: Path, before: dict) -> None:
    """Perform every act, then assert nothing named untouched moved; save the sheet."""
    try:
        _perform(run, acts, secu)
        after = run.read(_standing, acts_sheet, acts[3])
        moved = sorted(key for key, state in before.items() if state != after[key])
        assert not moved, f"an act touched what no act may: {moved}"
        run.sheet.say("")
        run.sheet.say("Every card item and the held-back match stand as they did before act 1.")
        _named_untouched(run.sheet, acts_sheet, acts[3])
    except (AssertionError, RefusedError) as stop:
        run.sheet.say("")
        run.sheet.say(f"STOPPED: {stop}")
        raise
    finally:
        run.sheet.save()


def main() -> None:
    """Parse the arguments, refuse a touched copy, snapshot it, perform every act."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--clone", required=True, help="the copy to perform on")
    parser.add_argument("--account", required=True, type=int, help="the account reconciled")
    parser.add_argument("--acts", required=True, help="the act sheet reconcile_act_sheet wrote")
    parser.add_argument("--secu", required=True, help="the SECU export the sheet was derived from")
    parser.add_argument("--sheet", required=True, help="the performance sheet to write")
    parser.add_argument("--snapshot", required=True, help="the snapshot to write")
    args = parser.parse_args()
    out = new_file(args.sheet, "--sheet")
    snapshot_path = new_file(args.snapshot, "--snapshot")
    acts_sheet = json.loads(Path(args.acts).expanduser().read_text(encoding="utf-8"))
    secu = Path(args.secu).expanduser()
    if acts_sheet["account"]["id"] != args.account or acts_sheet["secu_export"] != secu.name:
        raise SystemExit("the act sheet was derived for another account or another export")
    acts = _acts(acts_sheet)
    app = connect(args.clone)
    user_id, snapshot, before = _begin(app, args.account, secu, acts_sheet, acts)
    snapshot.write(snapshot_path)
    sheet = Sheet(out)
    sheet.say(f"# Reconcile performance sheet: {acts_sheet['account']['name']} "
              f"(account {args.account})")
    sheet.say(f"Copy {args.clone}, snapshot {snapshot_path.name} at {snapshot.captured_at}; "
              f"app {Path(app_package.__file__).parent}; act sheet {Path(args.acts).name}; "
              f"written {datetime.now(timezone.utc).isoformat()}")
    run = Run(app, Operator(app, sheet, user_id), args.account,
              date.fromisoformat(acts_sheet["books"]["last_day"]), snapshot)
    _rehearse(run, acts_sheet, acts, secu, before)
    print(f"wrote {out} and {snapshot_path}")


if __name__ == "__main__":
    main()
