"""The ONE normalized statement line, and the ONE rule for its identity.

Ruling **R-FP**: *a statement importer is a SOURCE ADAPTER over one normalized
line shape*, so matching, review and fact-writing are source-independent.  This
module is that shape, and the identity rule that makes re-importing a span
harmless.

**Nothing here reads a database, a clock or a request.**  A parser produces
:class:`StatementLine` values, this module decides which of them an account
already holds, and :mod:`._record` is the only place that writes.
Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Sequence


@dataclass(frozen=True)
class StatementLine:  # pylint: disable=too-many-instance-attributes
    """One line as a source stated it, normalized.

    Pylint: too-many-instance-attributes -- **eight because a statement line
    genuinely states eight things** (8/7), not because the value wants
    splitting.  Ruling **R-FP** makes this THE normalized shape every adapter
    produces and every consumer reads, so the field set is the union of what a
    source can say: two days, a figure, two names, two provenance ids and a
    running balance.  Splitting it would put half a line's facts behind a
    nested value nothing asks for alone, which is rule 13's speculative shape,
    and would make each new adapter fill two objects instead of one.
    ``CandidateRow`` and ``CreatedPurchase`` carry the same disable for the
    same reason.

    Attributes:
        posted_on: The civil day the bank POSTED the line.  This is the fact
            the whole arc exists to obtain -- of 110 movements matched to bank
            lines on exact amount, only 33 carried the day the app had recorded
            (finding **N-173**).
        transaction_on: The civil day the bank STATED the transaction itself
            happened, or ``None`` where the source states none.
            **The NULL is the source saying so, not the app not knowing**
            (plan step ``bank_import:X-f6a-3a``): this field held a COPY of
            :attr:`posted_on` for a source that does not distinguish the two,
            so no reader could tell an observed swipe day from a restatement of
            the clearing day -- and a match writes this day onto a purchase's
            ``purchased_on``, where a clearing day would claim the purchase was
            made on the day it cleared.
            **It is NOT bounded by ``posted_on``**: 2 of 361 lines in the
            developer's own SECU export carry an OFX ``DTUSER`` one day AFTER
            their ``DTPOSTED``, both ACH deposits.
        amount: Signed, positive INTO the account -- the same convention
            ``cash_ledger.movement_cash_leg`` uses, so a later match compares
            two figures that already agree about direction.
        description: What the bank called it, verbatim.  **This is the field a
            re-import PAIRS on** (:func:`pair_by_statement`): it is what the
            bank stated ABOUT the line, where the ordinal beside it is what
            this app assigned.
        merchant: What the bank calls the MERCHANT, or ``None`` where the
            source names none.
            **The NULL is the source saying so**, exactly as
            :attr:`transaction_on`'s is, and for a sharper reason: plan step
            ``bank_import:X-f6a-3d`` makes this string the KEY a merchant
            rule is stated against, so a source that cannot name
            a merchant must key NOTHING rather than key something wrong.
            Measured on the developer's own 2026-08-16 exports: SECU's CSV
            names one on **361 of 361** lines, and its OFX truncates 326 of
            those same 361 descriptions to exactly 32 characters -- so dozens
            of distinct merchants arrive as the identical string
            ``POINT OF SALE DEBIT L340 DATE 12``.  A reader that fell back to
            the description would key a policy on that and fire it on every
            one of them; ``None`` fires on nothing.
        source_category: The bank's own category string, or ``None``.
            Provenance only: it is the bank's opinion about a merchant, and
            reading it as a Shekel category would be a reference value no
            ``ref`` table governs.
        external_id: The source's own id for the line (an OFX ``FITID``), or
            ``None`` for a source that has none.  CORROBORATION, never
            identity -- see :func:`pair_by_statement`.
        running_balance: The account balance after this line, or ``None`` for a
            source that does not carry one.  What makes an import able to check
            itself (:func:`~._integrity.verify_running_balance`).
    """

    posted_on: date
    transaction_on: "date | None"
    amount: Decimal
    description: str
    merchant: "str | None" = None
    source_category: "str | None" = None
    external_id: "str | None" = None
    running_balance: "Decimal | None" = None


@dataclass(frozen=True)
class ParsedStatement:
    """Everything ONE file states -- its account, its lines, and its own figure.

    **The per-FILE facts, which a list of per-line ones cannot carry.**  Every
    adapter returned ``(external_account_id, lines)`` until this value existed,
    and the pair worked only because a file had exactly two things to say.  A
    source states more than that: SECU's CSV opens with a ``Balance as of``
    line, and the next adapter will have its own header facts.  Widening a
    tuple would have made every reader positional at the moment it stopped
    being obvious what the positions were.

    Attributes:
        external_account_id: What the FILE calls its own account -- SECU writes
            its name and masked number.  Ruling **R-FP** makes the mapping from
            this to a Shekel account a recorded fact rather than a guess.
        lines: The file's lines in CHRONOLOGICAL order, oldest first.
        declared_start: The first day the source DECLARES this file answers
            for (ruling **R-BAL71**, plan step ``bank_import:X-f6b-1``).
            **What coverage reads, and it is the adapter's to state**: a CSV
            export states no range, so its adapter declares the first..last
            line day; a feed sync declares the window it requested, so a
            quiet day inside it is covered and a sync that returned no line
            still covers what it asked for.  Every recorded line falls inside
            it.
        declared_end: The last such day, inclusive.
        stated_balance: The balance the file's own header CLAIMS, or ``None``
            for a source that states none.  **It is the bank's claim, and the
            day it is the balance FOR is a second fact the lines solve**
            (:mod:`._anchor`, ruling **R-GF**): a bank writes this figure as of
            the export INSTANT, so on the developer's 2026-08-16 export it read
            ``$4,747.63`` -- 2026-08-13's closing -- over a file listing two
            2026-08-14 lines worth ``-$1,006.72``.  It is recorded verbatim and
            never rewritten; what the import worked out about it is a LEVEL
            row naming the import (plan step ``balance:X-bj-1``).
        stated_balance_on: The civil day that header names.  ``None`` exactly
            when :attr:`stated_balance` is -- the two are one fact and a figure
            without its day asserts nothing.
    """

    external_account_id: str
    lines: "list[StatementLine]"
    declared_start: date
    declared_end: date
    stated_balance: "Decimal | None" = None
    stated_balance_on: "date | None" = None


@dataclass(frozen=True)
class KeyedLine:
    """A :class:`StatementLine` with the ordinal that completes its identity.

    Attributes:
        line: The line itself.
        sequence_in_group: Its ordinal among the lines sharing its
            ``(posted_on, amount)`` -- a SURROGATE this app assigns when the
            line is first recorded, never a fact the bank stated.
    """

    line: StatementLine
    sequence_in_group: int

    @property
    def identity(self) -> "tuple[date, Decimal, int]":
        """Return the account-relative part of this line's identity."""
        return (self.line.posted_on, self.line.amount, self.sequence_in_group)


def group_key(posted_on: date, amount: Decimal) -> "tuple[date, Decimal]":
    """Return what a line shares with the lines it is reconciled against.

    **The identity key minus its surrogate half.**  Two lines belong to one
    GROUP when the bank posted them on the same day for the same amount, and a
    group is the unit :func:`pair_by_statement` reconciles: everything the bank
    itself stated about a line is compared inside it, and the ordinal that
    tells two members of one group apart is assigned within it.

    **It takes the two values rather than a line** because both sides of a
    reconciliation are keyed by it -- an incoming :class:`StatementLine` and a
    recorded ``BankStatementLine`` row -- and one spelling of the key is what
    stops the two sides from grouping differently.

    **It does NOT normalise the amount, and a first version did.**  That
    version wrapped it in ``Decimal(str(...))`` to make "a value that is equal
    but differently constructed land in the same bucket", which is a no-op:
    Decimal's equality is numeric and its hash follows, so ``Decimal("-4.7500")``
    and ``Decimal("-4.75")`` are already one dict key -- measured 2026-08-20.
    What the wrapper would have done is launder a FLOAT into a plausible wrong
    value (``Decimal(str(0.1 + 0.2))`` is ``Decimal("0.30000000000000004")``),
    which is the opposite of this project's ``shekel-decimal-from-float``
    posture.  Both sides are ``Decimal`` by construction -- the parser builds
    one from the file's text, and psycopg hands one back for a ``Numeric``
    column -- so a float arriving is a defect that should raise, not round.
    Found by adversarial design review 2026-08-20.

    Args:
        posted_on: The day the bank posted the line.
        amount: Its signed amount, as a ``Decimal``.

    Returns:
        ``(posted_on, amount)``.
    """
    return (posted_on, amount)


def group_indexes(
    lines: "Sequence[StatementLine]",
) -> "dict[tuple[date, Decimal], list[int]]":
    """Return the INDEXES of *lines*, grouped by :func:`group_key`.

    Indexes rather than the lines themselves, so a caller reconciling a group
    can still say which position in ITS OWN file a member came from -- which is
    what lets fresh lines be written back in the file's order however the
    groups are walked.

    Args:
        lines: The source's lines, in the source's own order.

    Returns:
        ``{(posted_on, amount): [index, ...]}``, each list in file order.
    """
    grouped: "dict[tuple[date, Decimal], list[int]]" = defaultdict(list)
    for index, line in enumerate(lines):
        grouped[group_key(line.posted_on, line.amount)].append(index)
    return dict(grouped)


@dataclass(frozen=True)
class GroupPairing:
    """Which lines of one incoming group an account already holds.

    Attributes:
        held: ``(incoming index, recorded index)`` for each incoming line the
            app already holds, both indexes into the sequences given to
            :func:`pair_by_statement`.
        fresh: The incoming indexes with no recorded counterpart.
        unclaimed: The recorded indexes no incoming line claims.
    """

    held: "tuple[tuple[int, int], ...]"
    fresh: "tuple[int, ...]"
    unclaimed: "tuple[int, ...]"

    @property
    def restates(self) -> bool:
        """Return whether this group contains a genuine RESTATEMENT.

        **A restatement is a line the app holds that the file no longer states,
        standing beside a line the file states that the app does not hold.**
        Either alone is an ordinary event and neither is a contradiction: a
        recorded line the file omits is a shorter export, or a disappearance
        that finding **N-301** owns and that nothing here can see; an incoming
        line the app does not hold is simply new.  Together they are the one
        event ruling **R-FL** refuses to absorb silently -- the bank has
        re-worded a line the app recorded as an observation.
        """
        return bool(self.fresh) and bool(self.unclaimed)


def pair_by_statement(
    incoming: "Sequence[str]", recorded: "Sequence[str]",
) -> GroupPairing:
    """Pair one group's incoming lines against the recorded ones by WORDING.

    **The pairing is on what the BANK stated, never on the ordinal this app
    assigned**, and that is plan step ``bank_import:X-f6a-4``'s identity half.
    ``sequence_in_group`` is derived from the order lines happened to appear in
    a file; comparing against it treats an app-assigned number as though the
    bank had supplied it, which is a derived value stored beside its source
    with nothing reconciling the two -- the root cause three of this project's
    arcs exist to remove, sitting inside the import key.

    **Two measured consequences of comparing positionally, both reproduced
    against the shipped code 2026-08-20.**  Given two same-day same-amount
    lines recorded as ``[STARBUCKS, DUNKIN]``:

    * a later export listing the SAME two the other way round refused the whole
      file -- thirty days of genuinely new lines with it -- and told the owner
      the bank had restated a line it had not;
    * a genuinely NEW ``$4.75`` line that the bank INSERTED ahead of the
      recorded one was refused with the same sentence, for a line that had
      never been recorded.  The insertion behaviour is OBSERVED rather than
      hypothetical: it is why :func:`~._plan._refuse_restatement` stopped
      comparing running balances.

    Pairing on the wording answers both correctly and still refuses the event
    the refusal was designed for (:attr:`GroupPairing.restates`).

    **It is a MULTISET pairing, so identical wordings are matched by count.**
    The same coffee twice at the same shop for the same price is two lines with
    one description; recorded ``[COFFEE, COFFEE]`` against incoming
    ``[COFFEE, COFFEE]`` pairs both, and against ``[COFFEE]`` pairs one and
    leaves the other unclaimed.  Neither is a contradiction and neither is a
    duplicate.

    **``external_id`` is not consulted HERE, and since plan step
    ``bank_import:X-f6b-1`` it is consulted BEFORE here** (ruling
    **R-BI10**): the reconciliation pairs an incoming line carrying an id to
    the line a sighting of the same source already carries that id on, and
    hands this function only what that step left.  The id breaks ties within
    a group; it is never the identity, and a source that carries none
    (SECU's CSV) is not thereby unidentifiable.  Both sides given here are
    ONE source's wordings -- what this source called the lines it has already
    sighted -- because a wording compare across sources is what finding
    **N-303** measured as a false restatement.

    Args:
        incoming: The file's descriptions for this group, in file order.
        recorded: The already-recorded descriptions for the same group, in
            whatever order the caller holds them -- the returned indexes are
            into this sequence.

    Returns:
        The :class:`GroupPairing`.
    """
    available: "dict[str, list[int]]" = defaultdict(list)
    for index, description in enumerate(recorded):
        available[description].append(index)

    held = []
    fresh = []
    for index, description in enumerate(incoming):
        candidates = available.get(description)
        if candidates:
            held.append((index, candidates.pop(0)))
        else:
            fresh.append(index)
    claimed = {recorded_index for _, recorded_index in held}
    return GroupPairing(
        held=tuple(held),
        fresh=tuple(fresh),
        unclaimed=tuple(
            index for index in range(len(recorded)) if index not in claimed
        ),
    )


def fresh_ordinals(taken: "Iterable[int]", count: int) -> "list[int]":
    """Return *count* ordinals no line already in this group holds.

    **The ordinal is a surrogate, and this is the only place one is minted.**
    It exists because ``uq_bank_statement_lines_identity`` needs a TOTAL key
    and two genuinely distinct charges can share a day and an amount -- the
    same coffee twice.  What it must never do is collide with a recorded line's
    ordinal, which is what makes it "above everything this group already holds"
    rather than "this line's position in the file": a new line the bank listed
    FIRST is still the group's next member, not its first.

    **What it guarantees is exactly: an ordinal no SURVIVING member of the
    group holds** -- which is all `uq_bank_statement_lines_identity` needs and
    all a caller may rely on.  It counts above the maximum rather than filling
    gaps, so an INTERIOR gap a delete left is not re-used; a gap at the TOP of
    the range IS re-used, because the maximum moves down with it.

    **A first version claimed it never re-used a freed ordinal at all, and that
    was false for the ordinary shape** -- undoing the most recent import -- with
    a test that happened to pin the interior case.  Measured 2026-08-20:
    ``fresh_ordinals([0, 2], 2)`` is ``[3, 4]`` and ``fresh_ordinals([0], 1)``
    is ``[1]``, the address a deleted line held.  The claim was withdrawn
    rather than the code changed, because nothing depends on it: the ordinal is
    a surrogate that addresses a row WITHIN its group, and what cites a row
    outside the group -- ``system.audit_log.row_id``, every foreign key -- cites
    the primary key, which a sequence never re-uses.  Found by adversarial
    design review 2026-08-20.

    Args:
        taken: The ordinals this group's recorded lines already hold.  Empty on
            a first import, which is what makes the first line ordinal ``0``.
        count: How many ordinals are needed.

    Returns:
        *count* ordinals in ascending order, each unused.
    """
    highest = max(taken, default=-1)
    return [highest + 1 + offset for offset in range(count)]
