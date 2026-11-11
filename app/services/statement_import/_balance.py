"""What the BANK says an account held at the end of a day -- ONE fold, sampled.

An anchored import states a figure for a day (plan step ``bank_import:X-f6e-1``,
ruling **R-GF**), and the recorded lines say what moved on every day around it.
Together they determine the bank's own balance for any day the lines reach::

    bank_balance(D) = anchor.balance + sum(lines posted in (anchor.day, D])

with the sign falling out of which day comes first, so no branch decides it.

**One anchor PER RUN, and every other level in the run is a CHECKPOINT**
(plan step ``balance:X-bj-1b``, finding **N-343**, rulings **R-BAL63** to
**R-BAL66**).  The recorded days fall into RUNS -- contiguous stretches some
import covers (:func:`covered_runs`) -- and a run is as far as any walk can
honestly go, so the choice of what to walk from is a choice per run: the
strongest standing bank level the run reaches, then the latest day.  Every
other standing bank level the run reaches is read as a checkpoint,
``walked - observed``, which is the bank's record checked against itself: two
of its own figures with every line between them recorded either agree to the
cent or one of them was placed on a day it is not the balance for.  Until this
step ONE level was chosen for the whole account, so a ``file_chain`` level in
an old run left every day of a later, disconnected run unpriced -- including
that run's own placed day, whose crossing is empty.  A run holding lines and
no standing bank level anchors on nothing (**R-BAL64**): its days are absent
rather than priced from a level the owner typed, which ruling **R-BAL52**
keeps out of this walk permanently.

**One derivation, sampled at whatever grain the reader asks for**, which is the
discipline the cash side already pays for and this module exists to keep:
:func:`fold_bank_balances` prefix-sums the account's recorded days ONCE and
reads that running total at every requested day -- and at every checkpoint's
day, through the same :func:`_walked` -- and :func:`bank_balance_on` is that
same fold sampled at one.  A scalar that re-walked to its date with its own
window is exactly the shape that put ``$15.96`` between the cash scalar and
the cash series on the real Checking account (``balance_at._cash_flow``), and
a statement account will hold a few hundred recorded days, so there is nothing
to buy by writing the second walk.

**Two readers, and they mean different things by EVIDENCE.**
:func:`~._anchor.recorded_opening_before` asks this for the balance before a
file's first line, and what it learns is that TWO STATEMENTS AGREE -- so it
caps what it returns at
:attr:`~app.enums.StatementBalanceEvidenceEnum.CORROBORATED` and applies its own
weakest-link rule on top, against the anchor of the run that priced the day
(:meth:`BankBalances.anchor_for`).  A REPORT asks this to display a figure and
learns nothing new, so it carries each anchor's own strength unchanged.  The
cap therefore lives at the reader that earns it, never here.  A checkpoint
that DISAGREES changes neither reader's answer (**R-BAL66**): the comparison
is an instrument and never a gate (**R-GF**), and by rank the checkpoint is the
weaker of the two figures, so letting it weaken the anchor would invert the
evidence ladder.

**A day is answered only when the recorded lines REACH it** from its run's
anchor.  The walk is exact only if every line between the two days is
recorded; a gap between imports means lines nobody has imported, and summing
across one yields a confident wrong number.  Such a day is ABSENT from the
fold's result rather than present with a guess -- the same direction
:func:`~._anchor.recorded_opening_before` already fails in, and for the same
reason.

**The windows it reads are trustworthy only because deletion releases
anchors** (:func:`~._anchor.release_anchors_from`): an import's window claims
days its lines covered, and ``delete_import`` takes the lines only it sighted
while a later overlapping import keeps its own window.  Before that release
existed this reported "covered" over a `$150.00` hole.

Services-boundary discipline: no Flask import, no clock read.  It queries, which
is this package's shape (:mod:`._identity`, :mod:`._anchor`) and for the same
reason -- the recorded-history half of one subject is not a different subject.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.enums import StatementBalanceEvidenceEnum
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.anchor_release import AnchorRelease
from app.models.statement_import import BankStatementLine, StatementImport

_ZERO_MONEY = Decimal("0.00")
_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class BankAnchor:
    """The recorded fact a run's derived bank balances are walked from.

    **A value rather than the :class:`~app.models.account.AccountAnchorHistory`
    row it came from**, because what a derivation needs is the three facts
    below and nothing else -- and handing a reader the ORM row invites it to
    reach through to the import's ``declared_start`` and grow a dependency on
    which import happened to win.  The fourth field derives nothing: it is
    the statement's NAME, carried so the agreement page can say which file
    a run walks from (ruling **R-BAL63**), because a re-import legitimately
    places a second figure on the same day and the day alone cannot tell the
    two apart.

    Attributes:
        day: The day the figure is the balance FOR -- the level's solved
            ``observed_on``, never the day the file's header names.
        balance: What the bank said the account held at the end of that day.
        evidence: How strongly that figure is held, as the WEAKEST LINK in the
            chain behind it (ruling **R-GF**).
        file_name: The uploaded name of the statement that stated it.
    """

    day: date
    balance: Decimal
    evidence: StatementBalanceEvidenceEnum
    file_name: str

    @property
    def unconfirmed(self) -> bool:
        """Return whether nothing confirms this figure -- the weakest rung.

        **Decided here, in Python, on the enum MEMBER** (ruling **R-BAL65**),
        which is the project-wide IDs-for-logic rule at the place a template
        makes it easiest to break: ``{% if evidence.name == 'UNCORROBORATED'
        %}`` reads naturally, compares a display string, and sits in the one
        language this project forbids financial reasoning in.  The route
        answered this for the account's ONE anchor until the walk anchored
        per run; a page listing several reads it off each.
        """
        return self.evidence is StatementBalanceEvidenceEnum.UNCORROBORATED


@dataclass(frozen=True)
class Checkpoint:
    """A standing bank level the run's walk reaches and is checked against.

    The bank's record checked against itself (ruling **R-BAL63**): the run
    walks from its anchor, and every OTHER standing bank level the run
    reaches states a figure for a day the walk can price.  With every line
    between the two days recorded, the two figures either agree to the cent
    or one of them is placed on a day it is not the balance for -- most often
    a level whose day the import door had to ASSUME (ruling **R-GN**) rather
    than solve.  It is REPORTED and it moves nothing (**R-BAL66**).

    Attributes:
        day: The level's own day.
        observed: What that statement said the account held at its end.
        walked: What the run's walk says it held -- the anchor's figure plus
            the lines between, read off the same prefix sum every priced day
            is.
        evidence: How strongly the observed figure is held.
        file_name: The uploaded name of the statement that stated it.
    """

    day: date
    observed: Decimal
    walked: Decimal
    evidence: StatementBalanceEvidenceEnum
    file_name: str

    @property
    def difference(self) -> Decimal:
        """Return ``walked - observed``, signed: positive means the walk is higher."""
        return self.walked - self.observed

    @property
    def agrees(self) -> bool:
        """Return whether the walk and the statement say the same figure."""
        return self.difference == _ZERO_MONEY


@dataclass(frozen=True)
class RecordedRun:
    """One contiguous stretch of recorded days, and what prices it.

    Attributes:
        first_day: The run's first recorded day (:func:`covered_runs`).
        last_day: Its last.
        anchor: The :class:`BankAnchor` the run walks from, or ``None`` when
            the run reaches no standing bank level -- an ordinary state, not a
            failure (ruling **R-BAL64**): a file may state no balance at all,
            or state one its own lines cannot reach (a date-range export
            states TODAY's figure), and a placed level may have been released.
            Such a run prices no day.
        checkpoints: Every OTHER standing bank level the run reaches, as
            :class:`Checkpoint` values ascending by day; empty when the run
            holds one level or none.
    """

    first_day: date
    last_day: date
    anchor: "BankAnchor | None"
    checkpoints: "tuple[Checkpoint, ...]"

    def prices(self, day: date) -> bool:
        """Return whether this run's anchor reaches *day*.

        False for an unanchored run, and for a day the run's lines cannot
        carry the walk to (:func:`reaches_end_of`).  The ONE membership test:
        :func:`fold_bank_balances` prices a day through it and
        :meth:`BankBalances.anchor_for` names the anchor through it, so the
        two cannot disagree about which run answered.
        """
        return self.anchor is not None and reaches_end_of(
            (self.first_day, self.last_day), self.anchor.day, day,
        )


@dataclass(frozen=True)
class BankBalances:
    """A fold of the bank's own record, already sampled.

    **TOTAL** (ruling **R-BAL65**): an account with no recorded line has no
    run, one whose runs reach no standing bank level has runs that anchor on
    nothing, and neither is a ``None`` a reader must branch on -- "the bank's
    record prices no day" is ``balances`` being empty.

    Attributes:
        runs: Every recorded run (:class:`RecordedRun`), ascending and
            disjoint, each with its anchor or none and its checkpoints.  The
            ONE home of the run spans: the agreement report derives its
            coverage from these rather than carrying a second list.
        balances: ``{day: the bank's balance at the end of that day}``, holding
            an entry only for the requested days some run's anchor REACHES.  A
            requested day no run prices is absent, so ``.get(day)`` answers
            ``None`` for it -- absence rather than a sentinel, because the
            caller's question is "what does the bank say" and "nothing it
            has recorded says" is a real answer.
    """

    runs: "list[RecordedRun]"
    balances: "dict[date, Decimal]"

    @property
    def prices_any_day(self) -> bool:
        """Return whether ANY run of the bank's record is anchored, so prices a day.

        What tells a gap in coverage from an account the bank's record prices
        nowhere (ruling **bank_import:R-BI35**,
        :func:`~._anchor.window_opening`): an anchored run prices at least its
        anchor's own day, and an account with none has nothing an import
        could be checked against.  Read off the runs this fold already holds,
        so the question costs no second read of the levels -- and it is the
        RUNS' answer rather than "a standing level exists": a standing level
        that no run reaches anchors nothing (:func:`_recorded_runs`), so it
        prices no day and must not count as one.
        """
        return any(run.anchor is not None for run in self.runs)

    def anchor_for(self, day: date) -> "BankAnchor | None":
        """Return the anchor that prices *day*, or ``None`` when none does.

        Non-``None`` exactly when ``balances`` holds *day* (for a day that was
        requested), because both read :meth:`RecordedRun.prices`.  What
        :func:`~._anchor.recorded_opening_before` caps its evidence against
        (ruling **R-BAL66**): the strength of the figure the day was actually
        walked from, not of the strongest level the account holds anywhere.
        """
        run = _run_pricing(self.runs, day)
        return None if run is None else run.anchor


def bank_levels(
    account_id: int,
) -> "list[tuple[AccountAnchorHistory, AnchorRelease | None]]":
    """Return every BANK level of *account_id* with its release, if any.

    ONE fetch that :func:`standing_bank_levels` narrows and
    :func:`~._reads.import_history` reads per import, split so a reader that
    needs the answer for twenty imports at once pays one query rather than
    twenty while still reaching the one predicate the release door acts on.

    Args:
        account_id: The account whose levels to read.

    Returns:
        ``[(level, release or None), ...]`` over the account's levels that
        name a statement (``statement_import_id IS NOT NULL``), ascending by
        level id.  A level whose release is ``None`` STANDS; one with a
        release has been withdrawn, and the release says why.  The LEFT JOIN
        is exact rather than approximate because ``uq_anchor_releases_anchor``
        holds a level to at most one release.
    """
    return (
        db.session.query(AccountAnchorHistory, AnchorRelease)
        .outerjoin(
            AnchorRelease, AnchorRelease.anchor_id == AccountAnchorHistory.id,
        )
        .filter(
            AccountAnchorHistory.account_id == account_id,
            AccountAnchorHistory.statement_import_id.isnot(None),
        )
        .order_by(AccountAnchorHistory.id)
        .all()
    )


def standing_bank_levels(account_id: int) -> "list[AccountAnchorHistory]":
    """Return *account_id*'s bank levels that no release has withdrawn.

    The rows the bank walk may anchor on or check against
    (:func:`fold_bank_balances`) and the rows a line change can release
    (:func:`resting_on`): both questions are about levels that STAND, and
    this is the one narrowing of :func:`bank_levels` that says what standing
    means.

    Args:
        account_id: The account whose levels to read.

    Returns:
        The standing bank levels, ascending by id.
    """
    return [
        level for level, release in bank_levels(account_id) if release is None
    ]


def file_names_of(import_ids) -> "dict[int, str]":
    """Return ``{import_id: file_name}`` for every id in *import_ids*.

    ONE query for every statement a page names rather than one per level or
    per released row.  ``budget.statement_imports`` holds the row for every
    id a level or a release names (both keys cascade or null on its delete),
    so the mapping is total over the ids given and a reader indexes it.

    Args:
        import_ids: The ``budget.statement_imports`` ids to name; duplicates
            and an empty iterable are both fine.

    Returns:
        The mapping, empty when nothing was asked for (no query is issued).
    """
    wanted = set(import_ids)
    if not wanted:
        return {}
    return dict(
        db.session.query(StatementImport.id, StatementImport.file_name)
        .filter(StatementImport.id.in_(wanted))
        .all()
    )


def anchor_evidence(level: AccountAnchorHistory) -> StatementBalanceEvidenceEnum:
    """Return one level's own evidence.

    Args:
        level: The level row; every row carries an ``evidence_id``.

    Returns:
        Its :class:`~app.enums.StatementBalanceEvidenceEnum` member.

    **Resolved from the ID and never from the ref row's ``name``**, which is
    the project-wide IDs-for-logic rule at the one place it is easiest to
    break: ``StatementBalanceEvidenceEnum(<ref row>.name)`` reads naturally
    and turns a display string into a dispatch, where
    ``shekel-refname-compare`` cannot see it because it is a constructor rather
    than a comparison.  Found by adversarial review 2026-08-23.
    """
    # Imported inside the call because ``ref_cache`` imports the models this
    # module imports, so a module-scope import would close a cycle at start.
    # Pylint: ``import-outside-toplevel`` (1/0) -- a real import cycle, not a
    # cost dodge; ``app/models/transaction.py`` takes the same shape.
    from app import ref_cache  # pylint: disable=import-outside-toplevel

    return ref_cache.statement_balance_evidence_member(level.evidence_id)


def _strongest_then_latest(
    levels: "list[AccountAnchorHistory]",
) -> AccountAnchorHistory:
    """Return which of *levels* a run walks from.

    Args:
        levels: The standing bank levels one run reaches.  Must be non-empty.

    Returns:
        The strongest by evidence; among equals the latest day; among those
        the latest recorded (highest id).

    **Chosen by EVIDENCE first, with recency only as a tie-break.**  That is
    the correction of a comment claiming any anchor would serve because they
    are "mutually consistent by construction", which was false twice over: a
    ``file_chain`` anchor is solved against the file's own chain and an
    uncorroborated one against nothing, so neither consults a prior anchor and
    two can disagree freely; and ``created_at`` is the IMPORT ACT's time, not
    the statement's, so an unrelated later import would otherwise displace a
    nearer, stronger anchor.  Refuted by adversarial review 2026-08-23.

    **The strength ORDER is read from the enum, never from the ref row's id.**
    Sorting by ``evidence_id`` would work only while the seed happens to
    INSERT the ladder in order -- a second statement of the ladder, in a
    migration, that nothing reconciles against
    :attr:`~app.enums.StatementBalanceEvidenceEnum.strength`.  It was written
    that way first and was measured BACKWARDS: the seed writes
    ``file_chain, corroborated, uncorroborated``, so ``id DESC`` returned the
    WEAKEST anchor.  A run holds a handful of levels, so the ordering that
    matters is done here over the enum and the tie-break is a sort.

    **Applied per RUN since plan step ``balance:X-bj-1b``** (ruling
    **R-BAL63**); applied once per account before it, which is finding
    **N-343**'s subject.
    """
    ordered = sorted(
        levels,
        # The TIE-BREAK, already applied: ``max`` below returns the FIRST
        # maximal element, so the strongest level with the most recent day
        # wins without a second sort.
        key=lambda level: (level.observed_on, level.id),
        reverse=True,
    )
    return max(ordered, key=lambda level: anchor_evidence(level).strength)


def covered_runs(account_id: int) -> "list[tuple[date, date]]":
    """Return this account's declared windows, merged into contiguous runs.

    Args:
        account_id: The account whose imports to read.

    Returns:
        ``[(first_day, last_day), ...]`` ascending and disjoint, with
        overlapping or ADJACENT windows merged into one run.  Two runs in the
        list are therefore separated by at least one day nobody has imported.

    **Adjacent counts as contiguous** -- a window ending on the 4th and one
    starting on the 5th leave no day unimported between them -- which is what
    makes a run's interior a stretch the recorded lines fully describe.

    **Coverage is the window each import DECLARES, for as long as the import
    exists** (ruling **R-BAL71**, amending **R-BAL53**; plan step
    ``bank_import:X-f6b-1``): a CSV declares its first..last line day, a feed
    sync the window it requested, so a quiet day inside a sync is covered and
    a sync that returned no line still covers what it asked for.  **No
    import is excluded for owning no line**, and the gate that excluded one
    was scaffolding around a defect the sighting relation removed.  An
    adversarial review reproduced that defect end to end on 2026-08-24: a
    re-import of an identical file recorded zero fresh lines while keeping
    the full span, deleting the import that owned the lines took them and
    left the re-import's span claiming 28 unimported days, and the walk
    reported ``$1,000.00`` where the truth was ``$850.00``.  Under the
    sighting relation the re-import SIGHTED those lines, so deleting the
    first import removes nothing they rested on: an import's deletion takes
    only the lines no other import sighted.  What a surviving window claims
    is therefore ITS OWN evidence -- the lines it sighted, and the quiet days
    it declared -- never a line only the deleted import showed; where the
    survivor did not sight a line the deleted import alone held inside its
    window (a same-source disappearance, or a second source that missed
    it), the window stands on what the survivor said, which is what a
    declared window means.
    """
    spans = (
        db.session.query(
            StatementImport.declared_start, StatementImport.declared_end,
        )
        .filter(StatementImport.account_id == account_id)
        .order_by(StatementImport.declared_start)
        .all()
    )
    runs: "list[tuple[date, date]]" = []
    for start, end in spans:
        if runs and start <= runs[-1][1] + _ONE_DAY:
            runs[-1] = (runs[-1][0], max(runs[-1][1], end))
        else:
            runs.append((start, end))
    return runs


def reaches_end_of(
    run: "tuple[date, date]", anchor_day: date, end_day: date,
) -> bool:
    """Return whether one run's recorded lines carry the walk from one day to another.

    Args:
        run: One of this account's :func:`covered_runs`, ``(first, last)``.
        anchor_day: The day the anchor states a balance for.
        end_day: The day whose END balance is being derived.

    Returns:
        True when every day whose lines the walk must cross is inside the
        run.  An empty crossing is reached vacuously, which is the honest
        answer rather than a special case: there is nothing between the two
        days to have missed.

    **The days that must be crossed are the half-open span between them**,
    ``(min, max]``, and the asymmetry is the arithmetic rather than a choice:
    walking from the anchor's end-of-day to another end-of-day applies exactly
    the lines posted after the earlier day and up to and including the later
    one, so the earlier day's OWN lines are already inside the figure being
    walked from.  It follows that the day BEFORE a run's first line is reached
    from any level in the run -- the balance the run's lines start from -- and
    that a level on that day belongs to the run.

    **Over ONE run rather than any of them** since plan step
    ``balance:X-bj-1b``: a crossing lies inside a single run or in none, so
    the question is always asked of the run a level was placed in, and asking
    it of every run would be the account-wide walk this step retired.
    """
    low, high = min(anchor_day, end_day), max(anchor_day, end_day)
    first_needed = low + _ONE_DAY
    if first_needed > high:
        return True
    first, last = run
    return first <= first_needed and last >= high


def bank_daily_movements(
    account_id: int,
) -> "list[tuple[date, Decimal]]":
    """Return what the bank's recorded lines moved on each day, ascending.

    Args:
        account_id: The account whose lines to read.

    Returns:
        ``[(day, signed total), ...]`` -- one entry per DISTINCT day the
        account has recorded lines for, ascending.  Empty for an account with
        no recorded line.

    Keyed by DAY rather than by line, because a balance is a fact about a day's
    end: a boundary that split a day between two of its lines would name a
    moment no bank reports.

    **ONE aggregate over ``bank_statement_lines``, and two readers.**
    :func:`_recorded_day_totals` prefix-sums it into the fold's running total,
    and the books-vs-bank report reads the movements themselves -- which it
    needs WITHOUT an anchor, because comparing what moved requires no level.
    Each spelling its own ``SUM(amount) GROUP BY posted_on`` is the duplicate
    an adversarial review measured on 2026-08-24, in a module whose own
    docstring preaches one derivation sampled.
    """
    return (
        db.session.query(
            BankStatementLine.posted_on,
            db.func.sum(BankStatementLine.amount),
        )
        .filter(BankStatementLine.account_id == account_id)
        .group_by(BankStatementLine.posted_on)
        .order_by(BankStatementLine.posted_on)
        .all()
    )


def _recorded_day_totals(
    account_id: int,
) -> "tuple[list[date], list[Decimal]]":
    """Return this account's recorded days and the running total through each.

    Args:
        account_id: The account whose lines to read.

    Returns:
        ``(days, cumulative)`` -- the DISTINCT days the account has recorded
        lines for, ascending, and the sum of every line posted on or before
        each.  Both empty for an account with no recorded line.

    The prefix-sum half of :func:`bank_daily_movements`: a reader asking for
    one day and a reader asking for two hundred consult the same running total.
    """
    days: "list[date]" = []
    cumulative: "list[Decimal]" = []
    running = _ZERO_MONEY
    for day, amount in bank_daily_movements(account_id):
        running += amount
        days.append(day)
        cumulative.append(running)
    return days, cumulative


def _through(
    days: "list[date]", cumulative: "list[Decimal]", day: date,
) -> Decimal:
    """Return the sum of every recorded line posted on or before *day*.

    Args:
        days: The ascending distinct recorded days.
        cumulative: The running total through each of them.
        day: The day to read the running total at.

    Returns:
        The prefix sum, or ``0.00`` for a day before the first recorded line.
    """
    index = bisect_right(days, day)
    return cumulative[index - 1] if index else _ZERO_MONEY


def _walked(
    anchor: BankAnchor,
    days: "list[date]", cumulative: "list[Decimal]",
    day: date,
) -> Decimal:
    """Return the bank's balance at the end of *day*, walked from *anchor*.

    THE walk, stated once: the anchor's figure plus every line posted after
    the anchor's day and up to *day*, which for an earlier day subtracts out
    of the same expression.  Every priced day and every checkpoint's
    ``walked`` figure is this, so the two cannot be spelled apart.

    Args:
        anchor: The run's :class:`BankAnchor`.
        days: The ascending distinct recorded days.
        cumulative: The running total through each of them.
        day: The day to price.  The caller has established the run reaches it.

    Returns:
        The balance.
    """
    return (
        anchor.balance
        + _through(days, cumulative, day)
        - _through(days, cumulative, anchor.day)
    )


def _recorded_runs(
    account_id: int, days: "list[date]", cumulative: "list[Decimal]",
) -> "list[RecordedRun]":
    """Return every recorded run with its anchor, if any, and its checkpoints.

    Args:
        account_id: The account whose runs and levels to read.
        days: The ascending distinct recorded days.
        cumulative: The running total through each of them.

    Returns:
        One :class:`RecordedRun` per :func:`covered_runs` entry, ascending.

    **A standing bank level belongs to the run that reaches it**
    (:func:`reaches_end_of` to the run's last day), and to at most one, since
    runs are disjoint and never adjacent.  A level whose lines were later
    deleted from under it without a release -- possible only for a level on
    the day BEFORE its file's first line, which rests on no line the delete
    took -- may be reached by no run; it then anchors nothing, checks nothing
    and is listed nowhere, and the next import re-establishes a level.
    """
    standing = standing_bank_levels(account_id)
    names = file_names_of(level.statement_import_id for level in standing)
    runs: "list[RecordedRun]" = []
    for first_day, last_day in covered_runs(account_id):
        reached = sorted(
            (
                level for level in standing
                if reaches_end_of(
                    (first_day, last_day), level.observed_on, last_day,
                )
            ),
            key=lambda level: (level.observed_on, level.id),
        )
        if not reached:
            runs.append(RecordedRun(first_day, last_day, None, ()))
            continue
        chosen = _strongest_then_latest(reached)
        anchor = BankAnchor(
            day=chosen.observed_on,
            balance=Decimal(str(chosen.anchor_balance)),
            evidence=anchor_evidence(chosen),
            file_name=names[chosen.statement_import_id],
        )
        runs.append(RecordedRun(
            first_day,
            last_day,
            anchor,
            tuple(
                Checkpoint(
                    day=level.observed_on,
                    observed=Decimal(str(level.anchor_balance)),
                    walked=_walked(
                        anchor, days, cumulative, level.observed_on,
                    ),
                    evidence=anchor_evidence(level),
                    file_name=names[level.statement_import_id],
                )
                for level in reached
                if level is not chosen
            ),
        ))
    return runs


def _run_pricing(
    runs: "list[RecordedRun]", day: date,
) -> "RecordedRun | None":
    """Return the run whose anchor reaches *day*, or ``None``.

    At most one can (:meth:`RecordedRun.prices`): runs are disjoint and never
    adjacent, so a day and the day before a run's first line each lie within
    reach of one run's levels or none.
    """
    return next((run for run in runs if run.prices(day)), None)


def fold_bank_balances(account_id: int, days: "list[date]") -> BankBalances:
    """Return what the bank's own record says the account held on each day.

    Args:
        account_id: The account to derive for.
        days: The days to sample, in any order; duplicates are harmless.

    Returns:
        The :class:`BankBalances` -- every recorded run, and a figure for each
        requested day some run's anchor REACHES.  A day no run prices is
        absent from :attr:`BankBalances.balances`; an account whose runs hold
        no standing bank level prices none, and one with no recorded line has
        no run.  Never ``None`` (ruling **R-BAL65**).

    **An anchor's own day is always answered**, which is the fold's fixed
    point and worth stating because it is what makes the walk checkable: at
    ``anchor.day`` the crossing is empty, so the figure is the bank's verbatim
    claim and no line has touched it.  A checkpoint's day is answered the same
    way -- and there the answer is the walk's, which is exactly what the
    checkpoint compares the statement's figure to.
    """
    recorded, cumulative = _recorded_day_totals(account_id)
    runs = _recorded_runs(account_id, recorded, cumulative)
    balances: "dict[date, Decimal]" = {}
    for day in days:
        run = _run_pricing(runs, day)
        if run is not None:
            balances[day] = _walked(run.anchor, recorded, cumulative, day)
    return BankBalances(runs=runs, balances=balances)


def bank_balance_on(account_id: int, day: date) -> "Decimal | None":
    """Return what the bank's own record says the account held at *day*'s end.

    Args:
        account_id: The account to derive for.
        day: The civil day whose END balance to answer.

    Returns:
        The balance, or ``None`` when no run's anchor reaches *day*.

    **A SAMPLE of :func:`fold_bank_balances`, never a second walk.**  The two
    would be the same arithmetic today and the codebase has measured what
    happens next: a scalar and a series stating one quantity drifted apart by
    ``$15.96`` on the real Checking account before the cash seam collapsed them
    onto one fold.  One sample costs one extra prefix-sum pass over a few
    hundred days and removes the possibility.
    """
    return fold_bank_balances(account_id, [day]).balances.get(day)
