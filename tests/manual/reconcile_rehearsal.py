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
   but the one ruling plan#2433 holds back (:data:`reconcile_post_state.HELD_BACK`).
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
touches them: their state is read into the snapshot before the first act and graded after
the last, and a difference stops the run.

**IT WRITES.**  Before its first write it refuses the name ``shekel``
(:func:`door_rehearsal.connect`) and any copy on which an act's pre-state has moved, whatever
it is called (:func:`reconcile_post_state.moved_markers`), naming every moved marker --
nothing here is idempotent, so a partly performed copy is refused at act zero.  Then it writes
a SNAPSHOT of the copy before act 1 (:class:`reconcile_post_state.Snapshot`), and after each
act it asserts the act LANDED, reading the database rather than the response: the Reconcile
page answers a refused card with a 200 and a receipt saying so (measured 2026-10-30 on the
April Van Loan match).

**After the last act it grades the POST-STATE** (:func:`reconcile_post_state
.post_state_problems`, task plan#2416): every act's own arm again, the untouched items
against the snapshot, and plan#1192's DONE TEST -- every books day Checking's disagreement
list still calls apart is apart by exactly the card items and the held-back act the sheet
names on it, and every other books day agrees.

**``--check`` grades a copy the owner PERFORMED the acts on, and writes nothing**
(:func:`_check`): the same post-state arms, the one set both modes call, on a copy opened
READ-ONLY (:func:`door_rehearsal.connect_read_only`), against the snapshot the rehearsal wrote
of the same dump.  It refuses a copy no act has touched, and it serves no request: every arm
reads the database and the app's services, never a page.

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

Then the check of the copy the owner performed the acts on, restored from a dump taken after
them, against the snapshot the rehearsal of the same morning's dump wrote::

    ... python tests/manual/reconcile_rehearsal.py --check \\
        --clone <the performed copy> --account <its id> \\
        --acts <the same act sheet> --secu <the same export> \\
        --snapshot <the rehearsal's snapshot>
"""

import argparse
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from flask import Flask

import app as app_package
from app import ref_cache
from app.enums import StatementSourceEnum, StatusEnum
from app.extensions import db
from app.models.account import Account
from app.models.statement_import import BankStatementLine, StatementLineSighting
from app.models.statement_match import (
    StatementMatch,
    StatementMatchCreation,
    StatementMatchMember,
)
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services.statement_match import Tab

from door_rehearsal import (
    Operator,
    RefusedError,
    Sheet,
    connect,
    connect_read_only,
    forms_in,
    new_file,
)
from reconcile_post_state import (
    HELD_BACK,
    MatchAct,
    Snapshot,
    act_movements,
    acts_of,
    deleted_lines,
    delete_problems,
    envelope_state,
    import_of,
    import_problems,
    jsonable,
    left_residues,
    line_id_of,
    match_of,
    match_problems,
    cancel_problems,
    moved_markers,
    named,
    post_state_problems,
)


@dataclass
class Run:
    """What every act needs: the app, the owner's session and the sheet.

    Attributes:
        app: The application, pointed at the copy.
        operator: The owner's session.
        account_id: The account reconciled.
        books_last: The books span's last day, which the import must cover.
        digest: SECU's export's SHA-256, which the import stores.
        snapshot: The copy before act 1 (:class:`Snapshot`).
    """

    app: Flask
    operator: Operator
    account_id: int
    books_last: date
    digest: str
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
    run.read(_imported, run)


def _imported(run: Run) -> None:
    """Assert the import landed and covers the books' last day; record what rules filed."""
    problems = import_problems(run.account_id, run.digest, run.books_last)
    assert not problems, problems[0]
    found = import_of(run.account_id, run.digest)
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
    line_id = run.read(line_id_of, run.account_id, act.line)
    assert line_id is not None, f"{named(act.line)} is not recorded"
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
    run.read(_matched, run, act)


def _matched(run: Run, act: MatchAct) -> None:
    """Assert *act*'s line is matched to its movement, now on the line's day."""
    problems = match_problems(run.account_id, act)
    assert not problems, problems[0]
    line_id = line_id_of(run.account_id, act.line)
    run.sheet.say(f"- LANDED: match {match_of(line_id)}, movement(s) "
                  f"{sorted(act_movements(run.account_id, act))} now dated "
                  f"{db.session.get(BankStatementLine, line_id).posted_on}")


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
    run.sheet.act(number, f"Cancel {named(row)}", f"{page} (the row's full edit: Status)")
    for status in (StatusEnum.PROJECTED, StatusEnum.CANCELLED):
        _save_status(run, txn_id, status)
    run.read(_cancelled, run, txn_id)


def _cancelled(run: Run, txn_id: int) -> None:
    """Assert row *txn_id* is Cancelled and holds no movement."""
    problems = cancel_problems(txn_id)
    assert not problems, problems[0]
    run.sheet.say(f"- LANDED: row {txn_id} Cancelled, no movement left")


def _closed_on(txn_id: int) -> "date | None":
    """Return the day envelope *txn_id* closed on, or ``None`` while it is open.

    Closed is the settled band, the predicate the line list withdraws its X on
    (``purchases_closed`` in ``grid/_transaction_entries.html``).
    """
    txn = db.session.get(Transaction, txn_id)
    return txn.settled_on if txn.status.is_settled else None


def _delete(run: Run, number: int, row: dict, gone: "frozenset[int]") -> None:
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
    run.sheet.act(number, f"Delete {named(row)}", door)
    before = run.read(lambda: jsonable(envelope_state(txn_id, gone)))
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
    problems = run.read(delete_problems, txn_id, entry_id, gone, before)
    assert not problems, "\n".join(problems)
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
        sheet.say(f"- {named(pair['line'])} to transfer {pair['transfer_id']} "
                  f"({named(pair['row']) if pair['row'] else 'Projected'}): waits on "
                  f"{HELD_BACK[pair['transfer_id']]}")
    sheet.say("")
    sheet.say(f"## Not performed: {len(acts_sheet['card'])} card item(s), left for plan#2348 "
              "(ruling plan#2343)")
    for item in acts_sheet["card"]:
        movement = item.get("row") or item["line"]
        sheet.say(f"- {item['kind']}: {named(movement)}")


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
    gone = deleted_lines(deletes)
    for row in deletes:
        number += 1
        _delete(run, number, row, gone)
    run.read(_left, run)


def _begin(app, account_id: int, digest: str, acts_sheet: dict, acts: tuple):
    """Refuse a touched copy, then read what the run starts from.

    Returns:
        ``(owner's id, snapshot)``.

    Raises:
        SystemExit: When any act's pre-state has moved (:func:`moved_markers`).
    """
    with app.app_context():
        user_id = db.session.get(Account, account_id).user_id
        problems = moved_markers(account_id, digest, acts)
        if problems:
            raise SystemExit("this copy is not in the pre-act state:\n  "
                             + "\n  ".join(problems))
        return user_id, Snapshot.capture(acts_sheet, acts)


def _say_post_state(sheet: Sheet, acts_sheet: dict, acts: tuple) -> None:
    """Record a post-state that holds, in counts: no figure, no id the acts made."""
    sheet.say("")
    sheet.say("## The post-state")
    sheet.say(f"- every act's arm holds again: the import, {len(acts[0])} match(es), "
              f"{len(acts[1])} cancel(s), {len(acts[2])} envelope line delete(s)")
    sheet.say("- every card item and the held-back match stand as they did before act 1")
    sheet.say(f"- the done test holds: {len(left_residues(acts_sheet, acts[3]))} books day(s) "
              "still apart, each by exactly the card items and the held-back act on it; "
              "every other books day agrees")


def _rehearse(run: Run, acts_sheet: dict, acts: tuple, secu: Path) -> None:
    """Perform every act, then grade the post-state; save the sheet."""
    try:
        _perform(run, acts, secu)
        problems = run.read(post_state_problems, run.account_id, acts_sheet, acts,
                            run.snapshot, run.digest)
        assert not problems, "the post-state fails:\n  " + "\n  ".join(problems)
        _say_post_state(run.sheet, acts_sheet, acts)
        _named_untouched(run.sheet, acts_sheet, acts[3])
    except (AssertionError, RefusedError) as stop:
        run.sheet.say("")
        run.sheet.say(f"STOPPED: {stop}")
        raise
    finally:
        run.sheet.save()


def _check(args: argparse.Namespace, acts_sheet: dict, acts: tuple, digest: str) -> None:
    """Grade a copy the owner PERFORMED the acts on, writing nothing: ``--check``.

    The post-state arms the rehearsal grades after its own last act
    (:func:`post_state_problems`), on a copy every connection of which is
    read-only at the server, against the snapshot the rehearsal wrote of the
    same dump.  **A copy no act has touched is refused rather than graded**:
    every arm would fail and none would name the cause, and the likeliest
    wrong target is a dump taken before the acts.  A PARTLY performed copy
    passes that refusal and fails the arms its missing acts own, which is the
    check's job.

    Raises:
        SystemExit: On a refusal, or when an arm fails -- every failed arm is
            printed above the ``CHECK FAILED`` line.
    """
    snapshot = Snapshot.read(Path(args.snapshot).expanduser())
    app = connect_read_only(args.clone)
    sheet = Sheet(None)
    sheet.say(f"# Reconcile check of a performed copy (read-only): account {args.account}")
    with app.app_context():
        moved = moved_markers(args.account, digest, acts)
        if not moved:
            raise SystemExit("this copy is UNTOUCHED: no act's marker has moved. --check "
                             "grades a copy taken AFTER the acts; was its dump taken before "
                             "them?")
        sheet.say(f"precondition: {len(moved)} act marker(s) have moved on this copy")
        problems = post_state_problems(args.account, acts_sheet, acts, snapshot, digest)
    if problems:
        for problem in problems:
            sheet.say(f"FAILED: {problem}")
        raise SystemExit(f"CHECK FAILED: {len(problems)} arm(s)")
    _say_post_state(sheet, acts_sheet, acts)
    sheet.say("check complete; every connection was read-only")


def main() -> None:
    """Parse the arguments; rehearse every act on a copy, or ``--check`` a performed one."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--clone", required=True,
                        help="the copy: the one the rehearsal performs on, or the performed "
                        "one --check reads")
    parser.add_argument("--account", required=True, type=int, help="the account reconciled")
    parser.add_argument("--acts", required=True, help="the act sheet reconcile_act_sheet wrote")
    parser.add_argument("--secu", required=True, help="the SECU export the sheet was derived from")
    parser.add_argument("--sheet", help="the performance sheet to write: the rehearsal's, "
                        "never --check's")
    parser.add_argument("--snapshot", required=True,
                        help="the copy before act 1: the rehearsal WRITES it, --check READS "
                        "the one the rehearsal of the same dump wrote")
    parser.add_argument("--check", action="store_true",
                        help="grade a copy the owner PERFORMED the acts on, read-only, "
                        "instead of rehearsing them")
    args = parser.parse_args()
    if args.check and args.sheet is not None:
        parser.error("--check writes nothing: no --sheet")
    if not args.check and args.sheet is None:
        parser.error("the rehearsal needs --sheet")
    acts_sheet = json.loads(Path(args.acts).expanduser().read_text(encoding="utf-8"))
    secu = Path(args.secu).expanduser()
    if acts_sheet["account"]["id"] != args.account or acts_sheet["secu_export"] != secu.name:
        raise SystemExit("the act sheet was derived for another account or another export")
    acts = acts_of(acts_sheet)
    digest = hashlib.sha256(secu.read_bytes()).hexdigest()
    if args.check:
        _check(args, acts_sheet, acts, digest)
        return
    out = new_file(args.sheet, "--sheet")
    snapshot_path = new_file(args.snapshot, "--snapshot")
    app = connect(args.clone)
    user_id, snapshot = _begin(app, args.account, digest, acts_sheet, acts)
    snapshot.write(snapshot_path)
    sheet = Sheet(out)
    sheet.say(f"# Reconcile performance sheet: {acts_sheet['account']['name']} "
              f"(account {args.account})")
    sheet.say(f"Copy {args.clone}, snapshot {snapshot_path.name} at {snapshot.captured_at}; "
              f"app {Path(app_package.__file__).parent}; act sheet {Path(args.acts).name}; "
              f"written {datetime.now(timezone.utc).isoformat()}")
    run = Run(app, Operator(app, sheet, user_id), args.account,
              date.fromisoformat(acts_sheet["books"]["last_day"]), digest, snapshot)
    _rehearse(run, acts_sheet, acts, secu)
    print(f"wrote {out} and {snapshot_path}")


if __name__ == "__main__":
    main()
