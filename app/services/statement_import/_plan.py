"""The import's DECISION: what one file would add, and every refusal, before a write.

The first half of the record door, split from :mod:`._record` by plan step
``balance:X-bk-2`` (plan#1192, task plan#2372) when the door's decision became
a public read of its own (:func:`plan_statement`) and the one module stood
past pylint's 1,000-line ceiling.  The seam is the door's own: everything
here READS, and :func:`~._record.record_statement` writes only after
:func:`plan_statement` has returned, so a refused import writes nothing and
a caller that only needs the decision -- the reconcile's act sheet, derived on
a production copy before any act -- performs none of the writes.

**What a file states about a line is that SOURCE's sighting of it** (plan
step ``bank_import:X-f6b-1``, ruling **R-BI10**).  A line is the bank's fact
-- account, day, amount, ordinal -- and every import that shows it writes a
:class:`~app.models.statement_import.StatementLineSighting` carrying its own
wording, id, stated day, balance and category.  So the decision pairs within
a SOURCE (:func:`_pair_group`): by the source's own id, then by its wording,
then by count against the lines only other sources have shown; what is left
is new.  A different wording from a different source is never a restatement
(finding **N-303**), and a re-import of a file the app already holds records
one sighting per line and no line.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no ``flask`` import, and no write of any kind.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatementSourceEnum
from app.exceptions import (
    StatementLineConflict,
    StatementLineIdMoved,
    StatementParseError,
)
from app.extensions import db
from app.models.statement_import import (
    BankStatementLine,
    StatementImport,
    StatementLineSighting,
)

from ._adapters import parse_statement
from ._anchor import ImportedBalance, recorded_opening_before, resolve_anchor
from ._identity import verify_identity
from ._integrity import verify_running_balance
from ._line import (
    KeyedLine,
    ParsedStatement,
    StatementLine,
    fresh_ordinals,
    group_indexes,
    group_key,
    pair_by_statement,
)


def _recorded_groups(
    account_id: int, declared_start: date, declared_end: date,
) -> "dict[tuple[date, Decimal], list[BankStatementLine]]":
    """Return this account's already-recorded lines over the declared window.

    Loaded as ONE query over the day range rather than one per line: a full
    year's export is ~360 lines, and a per-line existence check would be 360
    round trips to answer a question one indexed range scan answers.
    ``idx_bank_statement_lines_account_day`` is the index it uses; the
    sightings each line carries, and the import behind each sighting, ride in
    the same statement (both relationships are ``joined``), because the
    reconciliation reads every one of them.

    **Grouped by ``(posted_on, amount)`` rather than keyed by the full
    identity**, which is what makes the reconciliation set-wise: the recorded
    ordinal is a surrogate this app assigned, so a group is looked up by what
    the BANK stated and the members inside it are then paired on what THIS
    SOURCE stated (:func:`_pair_group`).

    Args:
        account_id: The account being imported into.
        declared_start: The first day the file declares it answers for.
        declared_end: The last.  Every line the file states falls inside the
            window, so every line it could pair with is loaded.

    Returns:
        The recorded lines grouped by the day and amount they share, each
        group ordered by its own ordinal so the count pairing walks a stable
        sequence.
    """
    rows = (
        db.session.query(BankStatementLine)
        .filter(
            BankStatementLine.account_id == account_id,
            BankStatementLine.posted_on >= declared_start,
            BankStatementLine.posted_on <= declared_end,
        )
        .order_by(BankStatementLine.sequence_in_group)
        .all()
    )
    groups: "dict[tuple[date, Decimal], list[BankStatementLine]]" = defaultdict(
        list,
    )
    for row in rows:
        groups[group_key(row.posted_on, row.amount)].append(row)
    return dict(groups)


def _held_ids(
    account_id: int, source_id: int, lines: "list[StatementLine]",
) -> "dict[str, tuple[date, Decimal]]":
    """Return where THIS SOURCE already holds each id the file states.

    ONE query over the whole account rather than over the declared window,
    because the shape it exists to refuse is an id held on a line OUTSIDE the
    window: a source restating a line's day or amount under the same id
    (:func:`_refuse_moved_ids`).  Empty for a file carrying no ids, which
    issues no statement -- SECU's CSV carries none.

    Args:
        account_id: The account being imported into.
        source_id: The import's source; another source's ids are its own.
        lines: The file's lines.

    Returns:
        ``{external_id: (posted_on, amount)}`` for every stated id one of
        this source's sightings carries, the line's group key beside it.
    """
    stated = {line.external_id for line in lines if line.external_id}
    if not stated:
        return {}
    rows = (
        db.session.query(
            StatementLineSighting.external_id,
            BankStatementLine.posted_on, BankStatementLine.amount,
        )
        .join(BankStatementLine, StatementLineSighting.of_its_line())
        .join(StatementImport, StatementLineSighting.of_its_import())
        .filter(
            StatementLineSighting.account_id == account_id,
            StatementImport.source_id == source_id,
            StatementLineSighting.external_id.in_(stated),
        )
        .all()
    )
    return {
        external_id: group_key(posted_on, amount)
        for external_id, posted_on, amount in rows
    }


def _refuse_repeated_ids(lines: "list[StatementLine]") -> None:
    """Refuse a file that states one id on two of its own lines.

    A source names ONE line per id, and every rule downstream rests on it:
    the id step of :func:`_pair_group` maps an id to one recorded line, and
    :func:`_held_ids` reads one group per id.  A file carrying an id twice
    would record two lines under it -- the first pairing to the held line and
    the second minted fresh beside it -- and the next import's lookup would
    hold that id on whichever row came back last.  Found by adversarial
    review 2026-09-18; the unique index the sighting relation retired had
    refused this as a database error.

    Args:
        lines: The file's lines.

    Raises:
        StatementParseError: On the first id the file states twice.  A parse
            refusal, because a source that repeats an id has been read
            wrongly or is defective, and nothing about the account can
            repair that.
    """
    seen: "set[str]" = set()
    for line in lines:
        if not line.external_id:
            continue
        if line.external_id in seen:
            raise StatementParseError(
                f"This file states line id '{line.external_id}' on two "
                f"lines.  A source names one line per id, so the file was "
                f"read wrongly or is defective.  Nothing was imported."
            )
        seen.add(line.external_id)


def _refuse_moved_ids(
    lines: "list[StatementLine]", held: "dict[str, tuple[date, Decimal]]",
) -> None:
    """Refuse a file that states a held id on another day or amount.

    A source names ONE line per id, and the pairing that honours that
    (:func:`_pair_group`, step 1) works within a ``(day, amount)`` group -- so
    an id whose recorded line is in a DIFFERENT group is the one shape the
    pairing cannot reach and recording would make two lines of.  While the id
    lived on the line, ``uq_bank_statement_lines_external_id`` refused that
    state as a database error; this is the same refusal with a sentence.

    Args:
        lines: The file's lines.
        held: :func:`_held_ids`' answer.

    Raises:
        StatementLineIdMoved: On the first stated id whose recorded group is
            not the group this file states it in.
    """
    for line in lines:
        if not line.external_id:
            continue
        recorded = held.get(line.external_id)
        stated = group_key(line.posted_on, line.amount)
        if recorded is not None and recorded != stated:
            raise StatementLineIdMoved(line.external_id, recorded, stated)


def _sightings_from(row: BankStatementLine, source_id: int) -> list:
    """Return *row*'s sightings by THIS source, latest first.

    Latest by :attr:`~app.models.statement_import.StatementImport.act_key`,
    so the wording a same-source re-import is compared against is what this
    source said most recently.

    Args:
        row: A recorded line, its sightings loaded.
        source_id: The source to select.

    Returns:
        The :class:`~app.models.statement_import.StatementLineSighting` rows,
        possibly empty -- another source showed this line.
    """
    return sorted(
        (
            sighting for sighting in row.sightings
            if sighting.statement_import.source_id == source_id
        ),
        key=lambda sighting: sighting.statement_import.act_key,
        reverse=True,
    )


def _refuse_restatement(line: StatementLine, recorded: str) -> None:
    """Refuse when a source restates a line's own DESCRIPTION.

    A statement line is an OBSERVATION, and an observation quietly rewritten is
    what ruling **R-FL** exists to prevent -- so the fact a source states
    ABOUT A LINE must agree with what that same source already stated.

    **What reaches this is a group where THIS SOURCE's own sightings hold a
    member the file no longer states beside an incoming member nothing
    accounts for** (:func:`_pair_group`), which is the only shape that is a
    contradiction rather than a change in what the export covers.  A wording
    another source used is never compared -- two sources call one line two
    things, and reading that as a restatement is finding **N-303**, the
    defect the sighting relation exists to close.  It used to be reached by a
    positional compare, and that fired on two events the bank had not restated
    at all: a re-ordered pair of same-day same-amount lines, and a genuinely
    new line the bank inserted ahead of a recorded one.

    **The running balance is deliberately NOT compared, and that is a measured
    correction rather than a relaxation.**  A running balance is not a fact
    about a line at all: it is a prefix sum over the bank's LISTING ORDER, and
    SECU lists a day's card debits sorted by ascending magnitude rather than by
    arrival.  So a card swipe that finalizes onto a day already listed is
    INSERTED into that day's block, not appended, and every later line on that
    day legitimately gets a different running balance -- while both files
    verify their own chain perfectly.  Comparing it per line refused an honest,
    more-complete re-export of the user's own year-to-date statement, named the
    bank as having restated something it had not, and left that account unable
    to import ever again.  The in-file chain check
    (:func:`~._integrity.verify_running_balance`) is where a balance is graded;
    once recorded it is a sighting's provenance.

    **The two wordings it names are EXAMPLES, not a pairing.**
    :func:`~._line.pair_by_statement` declined to pair them -- that is what
    makes them leftovers -- so with three unaccounted-for incoming lines and
    two unclaimed recorded ones there is no correspondence to state, and a
    message asserting one would be a true sentence about the wrong problem.
    The wording therefore says what the code knows: the file states this, the
    app holds that, at this day and amount.  A first version read "was already
    recorded as X and this file states Y", which asserts a pairing; found by
    adversarial design review 2026-08-20.

    Args:
        line: One incoming line the file states and the app cannot account for.
        recorded: What this source called one recorded line in the same group
            that the file no longer states.

    Raises:
        StatementLineConflict: Always.  The caller has already established that
            this group restates something, so a guard here would be a second
            copy of that decision.
    """
    raise StatementLineConflict(
        line.posted_on, line.amount, recorded, line.description,
    )


@dataclass(frozen=True)
class _Reconciled:
    """What comparing a file against the record DECIDED, before any write.

    Plan step ``bank_import:X-gd-1``.  :func:`_reconcile` used to write as it
    decided, so a refusal on a later group left earlier ones dirty in the
    session -- the caveat the module docstring carried.  Both halves of the
    decision are values now, and the writes happen together after the last
    refusal.

    Attributes:
        fresh: The lines to write, with their ordinals, in the file's own
            order.  Each becomes a line AND this import's sighting of it.
        held: Every ``(incoming, recorded)`` pair the file states a line the
            app already holds by, in group order.  Each becomes this import's
            sighting of the recorded line, and nothing on the line.  It is a
            PAIR and not a row because what the sighting records comes from
            the incoming line.
    """

    fresh: "list[KeyedLine]"
    held: "list[tuple[StatementLine, BankStatementLine]]"


@dataclass(frozen=True)
class _GroupPairing:
    """How one group's incoming lines pair against its recorded ones.

    Attributes:
        held: ``(incoming index, recorded index)`` pairs.
        fresh: The incoming indexes nothing recorded accounts for.
        restated: What THIS source called a recorded member the file no
            longer states, or ``None``.  Set only when :attr:`fresh` is also
            non-empty, which is the contradiction :func:`_refuse_restatement`
            refuses; a same-source member the file merely omits is a shorter
            export and refuses nothing.
    """

    held: "list[tuple[int, int]]"
    fresh: "list[int]"
    restated: "str | None"


def _pair_by_id(
    incoming: "list[StatementLine]", by_source: "list[list]", same: "list[int]",
) -> "tuple[list[tuple[int, int]], list[int]]":
    """Pair incoming lines carrying an id to the lines this source holds them on.

    Step 1 of :func:`_pair_group`.  A source holds one line per id
    (:func:`_refuse_moved_ids`), so the map from id to recorded index is a
    function, and an incoming id it does not hold falls through to the
    wording step.

    Args:
        incoming: The group's incoming lines, in file order.
        by_source: Per recorded line, its sightings by this source
            (:func:`_sightings_from`).
        same: The recorded indexes with at least one such sighting.

    Returns:
        ``(held, unpaired)`` -- the ``(incoming, recorded)`` index pairs, and
        the incoming indexes left for the steps after.
    """
    id_of = {
        sighting.external_id: index
        for index in same
        for sighting in by_source[index]
        if sighting.external_id
    }
    held: "list[tuple[int, int]]" = []
    claimed: "set[int]" = set()
    unpaired: "list[int]" = []
    for index, line in enumerate(incoming):
        target = id_of.get(line.external_id) if line.external_id else None
        if target is not None and target not in claimed:
            held.append((index, target))
            claimed.add(target)
        else:
            unpaired.append(index)
    return held, unpaired


def _pair_group(
    incoming: "list[StatementLine]", recorded: "list[BankStatementLine]",
    source_id: int,
) -> _GroupPairing:
    """Pair one ``(day, amount)`` group's incoming lines to its recorded ones.

    **The pairing rule of ruling R-BI10, in its four steps**, each consuming
    what the one before it left:

    1. An incoming line carrying an id pairs to the recorded line a sighting
       of THIS SOURCE already carries that id on (:func:`_pair_by_id`).  The
       id breaks ties within the group; it is not the identity, and
       :func:`_refuse_moved_ids` has already established that every held
       id's line IS in this group.
    2. The rest pair by WORDING against the recorded lines this source has
       sighted (:func:`~._line.pair_by_statement`, multiset, unchanged) --
       against what this source called each of them most recently.
    3. The rest pair by COUNT, in ordinal order, against the recorded lines
       NO sighting of this source holds: another source showed them, its
       wording is expected to differ, and a different wording across sources
       is never a restatement (finding **N-303**).
    4. The rest are FRESH.

    **The refusal keeps its reach**: after the three pairings, a group where
    this source's OWN sightings still hold a member the file does not state,
    beside an incoming member nothing accounts for, is the same-source
    restatement ruling **R-FL** refuses.  A member another source holds and
    this file does not state is that source's business.

    Args:
        incoming: The file's lines in this group, in file order.
        recorded: The recorded lines in this group, in ordinal order.
        source_id: The import's source.

    Returns:
        The :class:`_GroupPairing`.
    """
    by_source = [_sightings_from(row, source_id) for row in recorded]
    same = [index for index, seen in enumerate(by_source) if seen]
    held, unpaired = _pair_by_id(incoming, by_source, same)
    # 2. By this source's wording, among what this source has sighted and the
    # id step did not claim.
    claimed = {recorded_index for _, recorded_index in held}
    same_open = [index for index in same if index not in claimed]
    pairing = pair_by_statement(
        [incoming[index].description for index in unpaired],
        [by_source[index][0].description for index in same_open],
    )
    held.extend(
        (unpaired[incoming_offset], same_open[recorded_offset])
        for incoming_offset, recorded_offset in pairing.held
    )
    rest = [unpaired[offset] for offset in pairing.fresh]
    # 3. By count, in ordinal order, against what only other sources showed.
    # ``recorded`` arrives in ordinal order, so index order is ordinal order,
    # and neither earlier step can have claimed one of these.
    other = [index for index in range(len(recorded)) if index not in same]
    held.extend(zip(rest, other))
    fresh = rest[len(other):]
    unclaimed_same = [same_open[offset] for offset in pairing.unclaimed]
    return _GroupPairing(
        held=held,
        fresh=fresh,
        restated=(
            by_source[unclaimed_same[0]][0].description
            if fresh and unclaimed_same else None
        ),
    )


def _reconcile(
    lines: "list[StatementLine]",
    already: "dict[tuple[date, Decimal], list[BankStatementLine]]",
    source_id: int,
) -> _Reconciled:
    """Decide what this file adds and what it re-sights, refusing a restatement.

    **The partition is total and it is decided per GROUP**, not per line.  Every
    incoming line either pairs with one the app already holds
    (:func:`_pair_group`) or it is new; and a group where this source's own
    record and the file each hold a member the other cannot account for is
    the restatement ruling **R-FL** refuses.  The ordinal takes no part in
    that decision; it is minted for the fresh lines afterwards
    (:func:`~._line.fresh_ordinals`).

    **It DECIDES and does not write** (plan step ``bank_import:X-gd-1``).  It
    absorbed as it went until then, which put a write ahead of a refusal this
    same loop can still raise; now the caller writes both halves after the
    file's last refusal, which is what lets a merchant word be resolved to a
    row for a file that is going to be recorded rather than for one that is
    about to be refused.

    Args:
        lines: The file's lines, in the file's own order.
        already: What is recorded over the same window, grouped by day and
            amount.
        source_id: The import's source, which is what a sighting is "from".

    Returns:
        The :class:`_Reconciled` decision.

    Raises:
        StatementLineConflict: When this source restates a line it recorded.
    """
    fresh: "list[tuple[int, KeyedLine]]" = []
    held: "list[tuple[StatementLine, BankStatementLine]]" = []
    for key, indexes in group_indexes(lines).items():
        recorded = already.get(key, [])
        pairing = _pair_group(
            [lines[index] for index in indexes], recorded, source_id,
        )
        if pairing.restated is not None:
            _refuse_restatement(
                lines[indexes[pairing.fresh[0]]], pairing.restated,
            )
        for incoming_index, recorded_index in pairing.held:
            held.append((
                lines[indexes[incoming_index]], recorded[recorded_index],
            ))
        ordinals = fresh_ordinals(
            (row.sequence_in_group for row in recorded), len(pairing.fresh),
        )
        for ordinal, incoming_index in zip(ordinals, pairing.fresh):
            fresh.append((
                indexes[incoming_index],
                KeyedLine(
                    line=lines[indexes[incoming_index]],
                    sequence_in_group=ordinal,
                ),
            ))
    # Back into the file's own order.  The groups are walked in first-sighting
    # order and their members in file order, so the concatenation is already
    # close -- but "already close" is not an order, and the staged rows' ids
    # are what ``recent_lines`` breaks ties on.
    return _Reconciled(
        fresh=[keyed for _, keyed in sorted(fresh, key=lambda pair: pair[0])],
        held=held,
    )


@dataclass(frozen=True)
class StatementPlan:
    """What importing one file WOULD do, decided before anything is written.

    **Every refusal the import can raise is behind it**, so it is the whole
    of the import's DECISION: :func:`record_statement` performs exactly the
    plan :func:`plan_statement` returns, and a reader that must know what an
    import would add without performing it -- the reconcile's act sheet (plan
    step ``balance:X-bk-2``, plan#1192), which is derived on a copy before
    any act -- reads the same plan.  So "which lines would this file add" has
    one producer, and the sheet does not restate the import's four-step
    pairing (:func:`_pair_group`) to guess at it.

    Attributes:
        parsed: What the file states (:func:`~._adapters.parse_statement`).
        source_id: Its ``ref.statement_sources`` row.
        identity_is_new: Whether the account's identity at this source is
            still to be RECORDED -- a first import from it.
        reconciled: Which lines are fresh and which the record already holds
            (:func:`_reconcile`).
        balance: What the import makes of the file's own balance, or ``None``
            when the file states none (:func:`~._anchor.resolve_anchor`).
    """

    parsed: ParsedStatement
    source_id: int
    identity_is_new: bool
    reconciled: _Reconciled
    balance: ImportedBalance | None

    @property
    def fresh_lines(self) -> "list[StatementLine]":
        """Return the lines the import would ADD, in the file's own order."""
        return [keyed.line for keyed in self.reconciled.fresh]


def plan_statement(
    account_id: int,
    user_id: int,
    source: StatementSourceEnum,
    payload: bytes,
) -> StatementPlan:
    """Decide what importing *payload* into *account_id* would do, writing nothing.

    The import's refusals, in the order they have to happen: parse, verify
    the file against itself, reconcile the account identity, refuse a
    repeated or moved line id, compare against what is already recorded, and
    resolve the file's balance -- an unexplained balance refuses the file
    rather than being discovered after its lines are staged.  Every step
    reads; none stages a row, so the session is exactly as it was however
    this returns or raises.

    Args:
        account_id: The account the file is for.  The caller has already
            proven *user_id* owns it, as :func:`record_statement`'s has.
        user_id: Its owner.
        source: Which adapter reads the bytes.
        payload: The file's raw bytes.

    Returns:
        The :class:`StatementPlan`.

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
    parsed = parse_statement(source, payload)
    verify_running_balance(parsed.lines)

    source_id = ref_cache.statement_source_id(source)
    identity_is_new = verify_identity(
        account_id, user_id, source_id, parsed.external_account_id,
    )

    _refuse_repeated_ids(parsed.lines)
    _refuse_moved_ids(
        parsed.lines, _held_ids(account_id, source_id, parsed.lines),
    )
    reconciled = _reconcile(
        parsed.lines,
        _recorded_groups(
            account_id, parsed.declared_start, parsed.declared_end,
        ),
        source_id,
    )
    # Read BEFORE the import row exists, so the walk sees only what was
    # recorded before this act -- and resolve the anchor here, with the other
    # refusals, because an unexplained balance must refuse the file rather
    # than be discovered after its lines are staged.
    balance = resolve_anchor(
        parsed,
        recorded_opening_before(account_id, parsed.declared_start),
    )
    return StatementPlan(
        parsed=parsed,
        source_id=source_id,
        identity_is_new=identity_is_new,
        reconciled=reconciled,
        balance=balance,
    )
