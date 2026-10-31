"""Derive the ACT SHEET for the one-time reconcile of an account's bank history.

Plan step **balance:X-bk-2** (plan#1192), its task plan#2372, on rulings
**plan#2343** (the card days are the card step's, plan#2348), **plan#2359** (a
Checking row on neither export never moved) and **plan#2426** (a row any
Capital One charge could be is kept as card spending).  The reconcile's acts
are written from the disagreement list
(:func:`app.services.outstanding_difference.disagreement_list`), and this
derives them: every movement the list leaves UNANSWERED on a day it lists
lands in exactly one class, with the act that answers it --

* **TIMING** -- a line SECU posted on another day, the row matched to it on
  the Reconcile page (after the import, for a line only the export holds);
* **TRANSFER** -- a line whose amount a transfer carries, matched to it;
* **CARD** -- a Capital One payment line, a CC Payback row, or a row a Capital
  One charge could be: named, and left for plan#2348;
* **NEVER MOVED** -- a row neither export could be: the row cancelled, or its
  envelope line deleted.

**Generous about the card and strict about cancelling, on purpose** (ruling
plan#2426).  Cancelling is the act that moves money, so a row is cancelled
only when nothing on either export could be it; keeping a row moves nothing
and leaves it for the card step, which builds the card from Capital One's own
history.  So a charge within the Reconcile page's near bound KEEPS a row even
where the row names no store -- weaker evidence than the page asks before it
proposes a MATCH (``statement_match._near``), because a match moves money and
keeping a row does not.

**The import the done test waits on is part of the list.**  plan#1192's done
test is read after an import covering the latest balance's day (ruling
plan#2343), so the books days no import covers yet are classed too, against
the lines only the SECU export holds -- found by the import's own pairing
(:func:`reconcile_inputs.fresh_lines`), and the export must cover every
such day.  Gathering those days and naming their movements is
:mod:`reconcile_inputs`, the half split from this one on its seam.

**It refuses rather than guesses**: a movement no class takes, a day whose
unanswered movements do not add up to its residue (a match on another day
stands behind it), an export the import would refuse, a books day the
comparison cannot take.  A refusal names what it could not class, by id and
day, and writes no sheet -- so a later run cannot perform acts from one.

Run it against a restore, never a live database, and with this checkout's
``app/``: the movements' ids come from the drill-down
(:class:`app.services.bank_agreement.DayRow`), which carries them since this
task.  The sheet carries production figures, so it goes OUTSIDE every
checkout (ruling **R-BAL132**); the terminal gets counts and ids only.
Usage::

    LC_ALL=C.UTF-8 DATABASE_URL=postgresql://.../<the restore> PYTHONPATH=. \\
        python tests/manual/reconcile_act_sheet.py --clone <the restore> \\
        --account <its id> --secu <SECU's CSV export> \\
        --card <Capital One's CSV export> \\
        --out ~/projects/shekel-handoffs/<folder>/act_sheet.json

Reads only: every connection is read-only (:func:`door_rehearsal
.connect_read_only`), and nothing is committed or staged.
"""

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from door_rehearsal import connect_read_only, new_file
from reconcile_inputs import (
    CENT,
    ZERO,
    CardLine,
    ClassedDay,
    Line,
    Row,
    classed_days,
    fresh_lines,
    plan_import,
    read_card_export,
)

from app.extensions import db
from app.models.account import Account
from app.services import (
    balance_at,
    outstanding_difference,
    statement_import,
    statement_match,
)


#: How far before a row a Capital One charge it could be may be dated: the
#: "two months" of ruling plan#2426, as two 31-day months so the window spans
#: any two calendar months.  Measured on the 2026-10-30 copy: the oldest charge
#: a row was recorded for is 29 days old (a Walmart order bought 03-13 and
#: recorded with the 04-10 payment), the classes are the same at every window
#: from 31 to 62 days, and at 90 a January charge starts claiming an April row.
CARD_LOOKBACK = timedelta(days=62)

#: The act each class performs, in the words the sheet prints.
CANCEL_ROW = "cancel row"
DELETE_ENVELOPE_LINE = "delete envelope line"
LEFT_FOR_THE_CARD_STEP = "named; left for plan#2348"


@dataclass
class ActSheet:  # pylint: disable=too-many-instance-attributes
    """Every classed movement, by class, and what could not be classed.

    Pylint: too-many-instance-attributes (8/7) -- the four classes, the two
    lists of what no class took, and the days and books span the sheet is
    about: each is a part of the sheet a reader acts on or checks.

    Attributes:
        books: The books span, ``(first_day, last_day)``.
        days: The days classed.
        timing: ``(row, line, near)`` pairs.
        transfer: ``(line, transfer_id, row or None)``: ``None`` for a
            transfer still Projected, which holds no movement yet.
        never_moved: Rows neither export could be.
        card: ``(kind, movement, card lines)``, kind ``payment``,
            ``payback`` or ``purchase``.
        unclassed_rows: Rows no class took.
        unclassed_lines: Lines no class took.
    """

    books: "tuple[date, date]"
    days: "list[ClassedDay]"
    timing: list
    transfer: list
    never_moved: "list[Row]"
    card: list
    unclassed_rows: "list[Row]"
    unclassed_lines: "list[Line]"


def _near(figure: Decimal, owed: Decimal) -> bool:
    """Return whether *figure* lies within the page's near bound of *owed*.

    The Reconcile page's own bound
    (:data:`~app.services.statement_match.NEAR_MISS_BOUND`), taken of the
    figure being explained, and both figures of one sign.
    """
    if (figure < ZERO) != (owed < ZERO):
        return False
    return abs(figure - owed) <= statement_match.NEAR_MISS_BOUND * abs(owed)


def _days_outside(first: date, last: date, day: date) -> int:
    """Return how many days *day* lies outside ``[first, last]``."""
    if day < first:
        return (first - day).days
    if day > last:
        return (day - last).days
    return 0


def _card_payments(
    sheet: ActSheet, lines: "list[Line]", card: "list[CardLine]",
) -> "list[Line]":
    """Class each outflow Capital One's export shows arriving as a credit.

    A payment moves its exact amount, so the figure is exact; the card dates
    it on or before SECU posts it (0 to 4 days before, measured 2026-10-30),
    inside the page's day window.  One credit explains one line, nearest
    first.

    Returns:
        The lines it did not class, in their order.
    """
    taken: "set[int]" = set()
    rest = []
    for line in lines:
        options = sorted(
            ((line.posted_on - credit.transaction_on).days, index)
            for index, credit in enumerate(card)
            if credit.is_credit and index not in taken
            and credit.amount == -line.amount
            and 0 <= (line.posted_on - credit.transaction_on).days
            <= statement_match.DAY_WINDOW
        )
        if not options:
            rest.append(line)
            continue
        taken.add(options[0][1])
        sheet.card.append(("payment", line, [card[options[0][1]]]))
    return rest


def _transfers(
    sheet: ActSheet,
    lines: "list[Line]",
    rows: "list[Row]",
    legs: "list[statement_match.CandidateRow]",
) -> "tuple[list[Line], list[Row]]":
    """Class each line a transfer carries, by exact amount, nearest first.

    The transfer is either an unanswered transfer movement among the classed
    rows, or a still-Projected transfer LEG the Reconcile page's own candidate
    set offers (:attr:`~app.services.statement_match.ReviewScope.candidates`),
    which holds no movement yet -- within the page's day window of the line,
    as the page bounds a match.  Two transfers equally near one line are
    ambiguous, and the line is left unclassed rather than guessed.

    Returns:
        The lines and the rows it did not class, each in its order.
    """
    taken_rows: "set[int]" = set()
    taken_legs: "set[int]" = set()
    rest = []
    for line in lines:
        options = sorted(
            (
                option for option in [
                    (abs((row.day - line.posted_on).days), row.transfer_id, row)
                    for row in rows
                    if row.transfer_id is not None
                    and row.entry_id not in taken_rows
                    and row.amount == line.amount
                ] + [
                    (_days_outside(*leg.expected_window, line.posted_on),
                     leg.transfer_id, None)
                    for leg in legs
                    if leg.transfer_id not in taken_legs
                    and leg.expected_window is not None
                    and leg.cash_amount == line.amount
                ]
                if option[0] <= statement_match.DAY_WINDOW
            ),
            key=lambda option: (option[0], option[1]),
        )
        if not options or (len(options) > 1 and options[1][0] == options[0][0]):
            rest.append(line)
            continue
        _, transfer_id, row = options[0]
        if row is None:
            taken_legs.add(transfer_id)
        else:
            taken_rows.add(row.entry_id)
        sheet.transfer.append((line, transfer_id, row))
    return rest, [row for row in rows if row.entry_id not in taken_rows]


def _timing(
    sheet: ActSheet,
    rows: "list[Row]",
    lines: "list[Line]",
    later: "list[Line]",
) -> "tuple[list[Line], list[Row]]":
    """Pair each row with a SECU line on another day, by the page's bounds.

    Within the near bound of the row's figure and the day window of its
    purchase..settle days -- the bounds the Reconcile page matches by -- one
    line per row; rows with an exact line first, then the nearest.  A line
    is one of the classed days' own, or one the import will add AFTER the
    books span (*later*), which no classed day holds: "a line SECU posted
    later".  A transfer's side is not a candidate: :func:`_transfers` had it.

    Returns:
        The classed days' lines and the rows it did not pair, each in order.
    """
    pool = lines + later
    found: "dict[int, tuple[Row, list]]" = {}
    for row in rows:
        if row.transfer_id is not None:
            continue
        options = sorted(
            (line.amount != row.amount,
             _days_outside(row.earliest, row.day, line.posted_on), index)
            for index, line in enumerate(pool)
            if _near(line.amount, row.amount)
            and _days_outside(row.earliest, row.day, line.posted_on)
            <= statement_match.DAY_WINDOW
        )
        if options:
            found[row.entry_id] = (row, options)
    taken: "set[int]" = set()
    paired: "set[int]" = set()
    for row, options in sorted(
        found.values(), key=lambda item: (item[1][0], item[0].entry_id),
    ):
        for near, _, index in options:
            if index not in taken:
                taken.add(index)
                paired.add(row.entry_id)
                sheet.timing.append((row, pool[index], near))
                break
    return (
        [line for index, line in enumerate(lines) if index not in taken],
        [row for row in rows if row.entry_id not in paired],
    )


def _adding_up(
    pool: "list[int]", card: "list[CardLine]", owed: Decimal,
) -> "tuple[int, ...] | None":
    """Return the fewest charges of *pool* adding up to *owed*, two or more.

    "Same-store charges adding up to it" (ruling plan#2426): a subset within
    the near bound of the figure, searched whole rather than capped at a
    size, so no group a row could be is missed.  In cents, so the sums kept
    are bounded by the figure itself; the bound's ends round INWARD, so a sum
    is never kept that lies outside it.
    """
    if len(pool) < 2:
        return None
    bound = statement_match.NEAR_MISS_BOUND * owed
    low = math.ceil((owed - bound) / CENT)
    high = math.floor((owed + bound) / CENT)
    reach: "dict[int, tuple[int, ...]]" = {0: ()}
    for index in pool:
        cents = int(card[index].amount / CENT)
        for total, members in list(reach.items()):
            grown = total + cents
            if grown <= high and (
                grown not in reach or len(reach[grown]) > len(members) + 1
            ):
                reach[grown] = members + (index,)
    found = [
        members for total, members in reach.items()
        if low <= total and len(members) >= 2
    ]
    return min(found, key=lambda members: (len(members), members), default=None)


def _charge_options(
    row: Row, card: "list[CardLine]",
) -> "list[tuple[tuple[bool, bool, int, int], tuple[int, ...]]]":
    """Return every charge, or same-store group, *row* could be, best first.

    Ruling plan#2426: a charge within the near bound of its figure, dated in
    the :data:`CARD_LOOKBACK` before it; or charges at a store the row names
    adding up to it.  Best is a charge at a store the row names, then an
    exact figure, then fewer charges, then the nearest.

    Returns:
        ``[(rank, charge indexes), ...]``, best first; empty for a row that
        moved money IN, which no charge is.
    """
    owed = -row.amount
    if owed <= ZERO:
        return []
    pool = [
        index for index, charge in enumerate(card)
        if not charge.is_credit
        and row.earliest - CARD_LOOKBACK <= charge.transaction_on <= row.day
    ]
    options = [
        ((not row.words & card[index].words, card[index].amount != owed, 1,
          (row.day - card[index].transaction_on).days), (index,))
        for index in pool if _near(card[index].amount, owed)
    ]
    group = _adding_up(
        [index for index in pool if row.words & card[index].words], card, owed,
    )
    if group is not None:
        options.append((
            (False, sum(card[index].amount for index in group) != owed,
             len(group),
             max((row.day - card[index].transaction_on).days
                 for index in group)),
            group,
        ))
    return sorted(options)


#: The rank of a row no charge could be, which claims nothing: after every
#: row that can claim, so it never stands ahead of one in the order.
_NO_CHARGE = (True, True, 0, 0)


def _card_purchases(
    sheet: ActSheet,
    paybacks: "list[Row]",
    rows: "list[Row]",
    card: "list[CardLine]",
) -> "list[Row]":
    """Class the rows a Capital One charge could be; return the rest.

    One charge explains one row (ruling plan#2426): the CC Paybacks claim
    theirs first -- each is card spending by its own link whatever it finds --
    then the other rows, those whose best charge names their store first,
    then exact before near, then the row with fewer choices.  A greedy claim
    by evidence, which is how a reader assigns them by hand; it is not a
    search for the most rows kept.

    Returns:
        The rows of *rows* no charge was left for, in their order.
    """
    options = {
        row.entry_id: _charge_options(row, card) for row in paybacks + rows
    }
    taken: "set[int]" = set()
    claimed: "dict[int, tuple[int, ...]]" = {}
    for row in sorted(
        paybacks + rows,
        key=lambda row: (
            row.payback_for is None,
            options[row.entry_id][0][0] if options[row.entry_id] else _NO_CHARGE,
            len(options[row.entry_id]),
            row.entry_id,
        ),
    ):
        for _, charges in options[row.entry_id]:
            if not taken & set(charges):
                taken.update(charges)
                claimed[row.entry_id] = charges
                break
    for row in paybacks:
        sheet.card.append((
            "payback", row,
            [card[index] for index in claimed.get(row.entry_id, ())],
        ))
    rest = []
    for row in rows:
        if row.entry_id in claimed:
            sheet.card.append((
                "purchase", row,
                [card[index] for index in claimed[row.entry_id]],
            ))
        else:
            rest.append(row)
    return rest


def derive(
    account: Account,
    ctx: "balance_at.BalanceContext",
    plan: "statement_import.StatementPlan",
    card: "list[CardLine]",
) -> ActSheet:
    """Return the act sheet for *account*: every classed movement, by class.

    The classes are taken in an order, and the order is the rulings': the
    CARD items by their own facts first, because ruling plan#2343 makes every
    card day the card step's whatever else it could be matched to; then the
    SECU side -- a transfer, then a line on another day -- because a line the
    bank posted is stronger evidence than a charge on another account; then
    the card charges (ruling plan#2426); and NEVER MOVED is what is left.

    **Each pass takes only what the ones before it left**, so no movement can
    land in two classes: the partition is the order, and what no pass takes
    is named as unclassed rather than defaulted into one.  A transfer's side
    no line took is unclassed, since no class but TRANSFER acts on one.

    Raises:
        SystemExit: When the disagreement list has nothing to class, or a
            books day is one neither the comparison nor the export reaches
            (:func:`reconcile_inputs.classed_days`).
    """
    listing = outstanding_difference.disagreement_list(account, ctx)
    if listing is None:
        raise SystemExit(f"account {account.id} has no disagreement list")
    fresh = fresh_lines(plan)
    days = classed_days(account, ctx, listing, plan.parsed, fresh)
    sheet = ActSheet(
        books=(listing.books.first_day, listing.books.last_day), days=days,
        timing=[], transfer=[], never_moved=[], card=[],
        unclassed_rows=[], unclassed_lines=[],
    )
    lines = _card_payments(
        sheet, [line for day in days for line in day.lines], card,
    )
    rows = [row for day in days for row in day.rows]
    paybacks = [row for row in rows if row.payback_for is not None]
    lines, rows = _transfers(
        sheet, lines, [row for row in rows if row.payback_for is None],
        [
            candidate for candidate in statement_match.ReviewScope.build(
                account.user_id, account.id,
            ).candidates.rows
            if candidate.kind is statement_match.RowKind.LEG
        ],
    )
    lines, rows = _timing(
        sheet, rows, lines,
        [line for line in fresh if line.posted_on > sheet.books[1]],
    )
    sheet.unclassed_rows = [row for row in rows if row.transfer_id is not None]
    sheet.never_moved = _card_purchases(
        sheet, paybacks, [row for row in rows if row.transfer_id is None], card,
    )
    sheet.unclassed_lines = lines
    return sheet


def refusals(sheet: ActSheet) -> "list[str]":
    """Return why *sheet* is not a partition of its days, or nothing.

    No movement can be in two classes (:func:`derive` takes each once), so
    what can fail is the other half: a movement NO class took, and a day whose
    unanswered movements do not add up to its residue
    (:attr:`ClassedDay.adds_up`), where a match on another day stands behind
    the difference and no act on this day answers it.  Each sentence names a
    movement by id and day, never by figure.
    """
    found = [
        f"{day.day}: its unanswered movements do not add up to its "
        "difference -- a match on another day stands behind it"
        for day in sheet.days if not day.adds_up
    ]
    found += [
        f"{row.day}: movement {row.entry_id} (a transfer's side) is in no class"
        for row in sheet.unclassed_rows
    ]
    found += [
        f"{line.posted_on}: line "
        f"{'(only in the export)' if line.line_id is None else line.line_id}"
        " is in no class"
        for line in sheet.unclassed_lines
    ]
    return found


def document(sheet: ActSheet, provenance: dict) -> dict:
    """Return *sheet* as the file it is written to."""
    return {
        **provenance,
        "books": {
            "first_day": sheet.books[0].isoformat(),
            "last_day": sheet.books[1].isoformat(),
        },
        "days": [
            {"day": day.day.isoformat(), "residue": str(day.residue)}
            for day in sheet.days
        ],
        "timing": [
            {
                "row": row.named(), "line": line.named(), "near": near,
                "act": (
                    "match the row to SECU's line on the Reconcile page"
                    + (", after the import" if line.line_id is None else "")
                    + ("; the match re-prices it to the bank's figure"
                       if near else "")
                ),
            }
            for row, line, near in sheet.timing
        ],
        "transfer": [
            {
                "line": line.named(), "transfer_id": transfer_id,
                "row": None if row is None else row.named(),
                "act": f"match the line to transfer {transfer_id}",
            }
            for line, transfer_id, row in sheet.transfer
        ],
        "never_moved": [
            {
                "row": row.named(),
                "act": DELETE_ENVELOPE_LINE if row.envelope_line else CANCEL_ROW,
            }
            for row in sheet.never_moved
        ],
        "card": [
            {
                "kind": kind,
                ("line" if kind == "payment" else "row"): movement.named(),
                "card_lines": [line.named() for line in card_lines],
                "act": LEFT_FOR_THE_CARD_STEP,
            }
            for kind, movement, card_lines in sheet.card
        ],
    }


def counts(sheet: ActSheet) -> str:
    """Return the sheet's counts as one line: no figure leaves the file."""
    kinds = defaultdict(int)
    for kind, _, _ in sheet.card:
        kinds[kind] += 1
    return (
        f"{len(sheet.days)} day(s) classed: timing {len(sheet.timing)}, "
        f"transfer {len(sheet.transfer)}, never moved "
        f"{len(sheet.never_moved)}, card {len(sheet.card)} ("
        + ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items()))
        + ")"
    )


def main() -> None:
    """Parse the arguments, derive the sheet, write it or refuse it."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--clone", required=True, help="the restore to read")
    parser.add_argument(
        "--account", required=True, type=int,
        help="the account the SECU export is for; the import's identity "
             "check holds it against the account the file names",
    )
    parser.add_argument("--secu", required=True, help="SECU's CSV export")
    parser.add_argument("--card", required=True, help="Capital One's CSV export")
    parser.add_argument(
        "--out", required=True,
        help="the sheet to write -- outside every checkout (R-BAL132)",
    )
    args = parser.parse_args()
    out = new_file(args.out, "--out")
    secu = Path(args.secu).expanduser()
    card_file = Path(args.card).expanduser()
    card = read_card_export(card_file.read_bytes())
    app = connect_read_only(args.clone)
    with app.app_context():
        account = db.session.get(Account, args.account)
        if account is None:
            raise SystemExit(f"{args.clone} holds no account {args.account}")
        ctx = balance_at.BalanceContext.build(account.user_id)
        sheet = derive(account, ctx, plan_import(account, secu.read_bytes()), card)
        found = refusals(sheet)
        if found:
            print("refused: the classes do not partition the list", file=sys.stderr)
            for sentence in found:
                print(f"  {sentence}", file=sys.stderr)
            raise SystemExit(1)
        written = document(sheet, {
            "sheet": "reconcile act sheet, plan#2372 (rulings plan#2343, "
                     "plan#2359, plan#2426)",
            "database": args.clone,
            "account": {"id": account.id, "name": account.name},
            "read_as_of": ctx.as_of.isoformat(),
            "written": datetime.now(timezone.utc).isoformat(),
            "secu_export": secu.name,
            "card_export": card_file.name,
        })
    out.write_text(json.dumps(written, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"  {counts(sheet)}")


if __name__ == "__main__":
    main()
