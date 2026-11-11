"""Which day is a statement's stated balance the balance FOR, and how firmly?

**A bank states its balance as of the EXPORT INSTANT and labels it with the
export's own day, so the day on the header is not the day the figure is for.**
Measured on the developer's own SECU exports: the 2026-08-21 file reads
``Balance as of 08/21/2026,2501.310000`` while its last line is 08-18 and
``2501.31`` is 08-18's closing; the 2026-08-16 file reads ``$4,747.63``, which
is 2026-08-13's closing, over a list containing two 2026-08-14 lines worth
``-$1,006.72``.  Ruling **R-GF**, plan step ``bank_import:X-f6e-1``.

**So the claim and the day it is for are two facts, and the LINES solve the
second.**  Given a known opening -- the balance before the file's first line --
the stated figure is effective at day ``d`` exactly when::

    stated - sum(lines posted on or before d) == opening

over the candidates ``{the day before the declared window} + {every line
day} + {the day the window ends, or the day the header names if earlier}``,
bounded above by the day the header itself names.  **The line days and the
stated day, NOT every day of the window** (ruling **R-BAL74**, amending
**R-BAL71**'s candidate clause; developer 2026-09-18 on X-f6b-1's adversarial
review): a feed sync that returned no line
since the last one still places the bank's figure on the day the bank stated
it, because that day is a candidate in its own right; a CSV, whose window is
its own line extremes, keeps exactly the candidates it had.  The build's
first cut took EVERY day of the window and named the LATEST satisfying one,
which put a figure that lags its file onto the last QUIET day after the line
it is the closing of -- and that is money, not naming: the cash walk treats
an app row settled on or before a level's day as already inside the bank's
figure, so on the developer's own 08-16 lag shape over a weekend a purchase
settled on the Saturday was absorbed into Friday's closing and Monday's
derived balance read `$25.00` high until the next assertion.  Found by
adversarial review 2026-09-18.

**A SOLVED day is only as good as the opening it was solved against**, which is
why :class:`~app.enums.StatementBalanceEvidenceEnum` records the WEAKEST LINK
in the chain behind the figure rather than how the day was worked out.  An
anchor solved against an uncorroborated opening is uncorroborated; recording
the minimum makes that true by construction, and is what stops a re-upload of
the same file from laundering an assumption into a corroboration.

**A file may legitimately state a balance NO day of its own explains**, and
that is measured rather than allowed for: a DATE-RANGE export states the
CURRENT balance, not the range's closing.  The developer's own
2026-01-02..2026-03-31 export, pulled 2026-08-23, reads
``Balance as of 08/23/2026,2459.600000`` -- 145 days past its last line and
`$255.41` from the `$2,715.01` its 139 lines imply.  Such a file records its
CLAIM with no anchor, which is the honest absence rather than a guess.

**Only a file that contradicts ITSELF is refused**, and that needs no evidence
from outside it: a per-line running balance states what the account held on
every day the file covers, so a header the chain reaches on no such day is a
file disagreeing with itself.  An earlier draft refused on a mismatch against
RECORDED history too, and an adversarial review reproduced it rejecting an
honest export while blaming the file for the app's own stale anchor.

**An anchor is a conclusion drawn from lines, so the doors that CHANGE lines
release it** (:func:`release_anchors_from`).  Recording a line at or before an
anchor's day means that anchor was solved without it; deleting an import means
the lines it was solved against are gone.  Both were reproduced as silently
wrong openings -- `$150.00` on the delete path -- before the release existed.
Releasing rather than re-solving is deliberate: the next import re-establishes
an anchor from evidence that is present, where a re-solve would be the app
inferring its way around facts that moved underneath it.

**A placed anchor is a LEVEL, and a release is an APPENDED withdrawal** (plan
step ``balance:X-bj-1``, rulings **R-IS** and **R-JN**).  The solved day and
its evidence were two columns on ``budget.statement_imports`` until that step,
nulled by UPDATE to release; they are a row in the level relation
(:class:`~app.models.account.AccountAnchorHistory`, ``statement_import_id``
set, its amount locked to the file's own claim by key) beside the owner's
true-ups, and a release is an :class:`~app.models.anchor_release.AnchorRelease`
naming the level and the import whose lines undercut it.  A level STANDS when
no release names it; :func:`~._balance.bank_levels` is the one read of that,
and :func:`~._balance.standing_bank_levels` its narrowing.  What this bought: the badge on
the statements page can name a release's cause (finding **BAL-485**), and the
relation is append-only at the database tier as its siblings are.

**Deriving a balance from a recorded anchor is :mod:`._balance`'s**, not this
module's, and the split is the walk/fold one the cash side already pays for: a
FOLD is a balance, a walk is a fact.  This module decides which DAY a claimed
figure is for; that one turns a settled anchor plus the recorded lines into a
balance for any day, and :func:`recorded_opening_before` is one of its two
readers.  Keeping the arithmetic there is what stops the report added at plan
step ``bank_import:X-f6e-2`` becoming a second statement of it.

Services-boundary discipline: no Flask import, no clock read.  It DOES query,
which is :mod:`._identity`'s shape in this package and for the same reason: the
recorded-history half of one subject is not a different subject.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.enums import StatementBalanceEvidenceEnum
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.anchor_release import AnchorRelease

from ._balance import fold_bank_balances, standing_bank_levels
from ._errors import StatementBalanceUnexplained
from ._integrity import opening_balance
from ._line import ParsedStatement

_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class PlacementRelease:
    """Why a placed figure no longer stands, as the statements page shows it.

    A reading of one :class:`~app.models.anchor_release.AnchorRelease` (plan
    step ``balance:X-bj-1``, finding **BAL-485**): the badge that read *not
    placed* for a figure the app itself had withdrawn could not name the
    cause, because the release was an UPDATE that kept nothing.  It is a row
    now, and this is what the page reads off it.

    Attributes:
        placed_on: The day the figure HAD been placed on -- the withdrawn
            level's own ``observed_on``, which the level relation keeps
            because a release edits nothing.
        by_file_name: The file of the import whose fresh lines undercut the
            level, or ``None`` when that import no longer exists -- a delete
            released this level, or the releasing import was itself deleted
            since.  Those are the only two events that leave the cause
            unnamed, so ``None`` is a fact and not a gap.
        lines_changed_from: The earliest day whose recorded lines changed;
            what the badge names when the import cannot be.
        released_at: When the release was recorded.
    """

    placed_on: date
    by_file_name: "str | None"
    lines_changed_from: date
    released_at: datetime


@dataclass(frozen=True)
class ImportedBalance:
    """A file's balance CLAIM and what the import made of it, as one value.

    **Two of its fields are the claim's own columns, two are the level row's,
    and their nullability is the schema's** (plan step ``balance:X-bj-1``):
    ``ck_statement_imports_stated_balance_paired`` holds the claim together,
    and a placed figure is a :class:`~app.models.account.AccountAnchorHistory`
    row whose ``observed_on`` and ``evidence_id`` are both NOT NULL, so
    :attr:`effective_on` and :attr:`evidence` are ``None`` together exactly
    when no level stands.  One value rather than four parameters threaded
    through the door, its receipt and the page, because every one of those
    surfaces needs the same facts together and a reader that had to test them
    separately would be re-deriving what the schema already states.

    **The fifth, :attr:`day_is_solved`, is stored NOWHERE and that is a stated
    limit rather than an oversight** (plan step ``bank_import:X-gc``).  It
    records HOW this import reached its day, which only the resolve knows, and
    it is not derivable afterwards: an assumed day is
    ``min(declared_end, stated_balance_on)``, and a SOLVED day can land on that
    same value by coincidence, so a reader comparing the stored columns would
    call a proven placement a guess.  So the receipt -- which holds this value
    -- can say it and the imports TABLE, which reads stored rows, cannot.
    Giving it a column is a schema decision this step did not take.
    :attr:`opening_known` is the same kind of fact for the same reason (plan
    step ``bank_import:X-f6b-2``, ruling **bank_import:R-BI35**): why an
    unplaced figure was not placed is known at the resolve, so the receipt
    names the cause and the table's badge names both.

    Attributes:
        stated: The figure the file's header claims, verbatim.
        stated_on: The day that header names -- the EXPORT's day.
        effective_on: The day the figure IS the balance for, solved from the
            lines, or ``None`` where the file's own lines cannot reach the day
            it claims.  NOT a copy of :attr:`stated_on`: on the developer's
            2026-08-16 export the two are three days apart, and on his
            2026-01-02..2026-03-31 one this is ``None`` because that header
            states TODAY's balance, 145 days past the file's last line.
        evidence: How strongly the figure is held, as a
            :class:`~app.enums.StatementBalanceEvidenceEnum` member -- the
            WEAKEST link in the chain behind it.  ``None`` exactly when
            :attr:`effective_on` is.
        release: Why a once-placed figure no longer stands
            (:class:`PlacementRelease`), or ``None``.  Set only by the
            statements page's read model (:func:`~._reads.import_history`),
            which is the one surface that shows a figure after the act; the
            receipt reads an import whose level has just been written and
            can carry no release.  ``None`` with :attr:`effective_on` ``None``
            means the figure was never placed.
        opening_known: Whether the resolve had an opening to check the claim
            against -- the file's own chain or the recorded walk (``True``) --
            or none: the window's opening lay in a coverage gap, or the
            account's record priced no day at all (``False``).  Known only at
            the resolve, like :attr:`day_is_solved`, so the read model leaves
            it ``None``.  The receipt reads it to say WHY an unplaced figure
            was not placed (ruling **bank_import:R-BI35**): "no day
            reconciles it with what was recorded" and "nothing recorded
            reaches back to it" call for different acts of the owner.
        day_is_solved: Whether :attr:`effective_on` was WORKED OUT from a
            balance the app already held, rather than assumed.  ``False`` for
            an unplaced figure and for the last arm of :func:`resolve_anchor`,
            which has nothing to solve against and takes the file's last line.

            **The two are not the same quality of fact, and the evidence ladder
            does not separate them.**  ``uncorroborated`` is minted BOTH by a
            solve against an unconfirmed opening -- where the day is proven and
            only the chain behind the figure is weak -- and by the arm that
            guesses the day outright.  A receipt reading the level alone
            reports those identically, which is what
            :func:`~app.routes.accounts.statements._import_flash` was measured
            doing on 2026-08-25: the developer's 2026-01-02..2026-03-31 export,
            whose header names a day **145 days** past its last line and
            `$255.41` from what its own 139 lines imply, reported as an
            ordinary success when it was an account's FIRST import.  Ruling **R-GN**
            (2026-08-25): a guessed day is what earns the warning.
    """

    stated: Decimal
    stated_on: date
    effective_on: date | None
    evidence: StatementBalanceEvidenceEnum | None
    day_is_solved: bool = False
    opening_known: "bool | None" = None
    release: PlacementRelease | None = None

    @property
    def is_anchored(self) -> bool:
        """Return whether this import placed its own figure on a day.

        ONE field is tested rather than two, which the level row's NOT NULL
        shape makes exact rather than economical: a placed figure is a row
        whose day and evidence are both present, so ``effective_on`` and
        ``evidence`` are ``None`` together or not at all.
        """
        return self.effective_on is not None


@dataclass(frozen=True)
class KnownOpening:
    """A balance before a file's first line, and how firmly it is held.

    **The evidence travels WITH the figure, and that is the whole reason this
    is a value rather than a bare ``Decimal``.**  A solve inherits the weakness
    of whatever it solved against, so an opening arriving without its
    provenance would let the caller record a determination it has not earned --
    the defect an adversarial review reproduced in two clicks on 2026-08-23,
    where re-uploading one file walked back to the app's own assumption, found
    the file agreed with it, and turned the receipt green.

    Attributes:
        amount: The balance before the file's first line.
        evidence: The strength of the chain behind that figure.
    """

    amount: Decimal
    evidence: StatementBalanceEvidenceEnum


@dataclass(frozen=True)
class CoverageGap:
    """The day before a window lies in a GAP: the record prices other days, not it.

    Ruling **bank_import:R-BI35** (plan step ``bank_import:X-f6b-2``): what
    :func:`window_opening` answers when no run of the account's recorded
    lines reaches the day before a window's first day, while some run IS
    anchored -- a span nobody imported lies between them, or a raw day the
    bank feed is still re-fetching.  A value of its own rather than the
    ``None`` a first import gets, because the two send
    :func:`resolve_anchor` to different arms: a first import has nothing to
    check against and ASSUMES a day, saying so; a window after a gap has a
    record that could have checked it and did not reach, so its claim is
    recorded UNSOLVED and places no level.  It carries nothing: the window's
    own first day is the receipt's to name.
    """


def weaker_of(
    first: StatementBalanceEvidenceEnum,
    second: StatementBalanceEvidenceEnum,
) -> StatementBalanceEvidenceEnum:
    """Return whichever of two evidence levels is the weaker.

    The whole of the weakest-link rule, stated once so no caller writes its own
    comparison.  The ORDER it reads is
    :attr:`~app.enums.StatementBalanceEvidenceEnum.strength`, declared on the
    enum itself.

    Args:
        first: One evidence level.
        second: The other.

    Returns:
        The weaker member, or either when they are equal.
    """
    return min(first, second, key=lambda member: member.strength)


def _cumulative_by_day(lines: list) -> "dict[date, Decimal]":
    """Return, per day the file covers, the sum of every line up to and including it.

    Args:
        lines: :class:`~._line.StatementLine` values in chronological order.

    Returns:
        ``{day: sum of lines posted on or before day}``, one entry per DISTINCT
        day.  Keyed by day rather than by line index because the solve's
        candidates are days: a balance is a fact about a day's end, so a
        candidate that split a day between two of its lines would name a moment
        no bank reports.
    """
    running = Decimal("0.00")
    totals: "dict[date, Decimal]" = {}
    for line in lines:
        running += line.amount
        totals[line.posted_on] = running
    return totals


def _candidate_days(parsed: ParsedStatement) -> "list[date]":
    """Return the days the stated figure may be placed on, ascending.

    The day before the declared window (a figure that IS the opening states a
    balance no line has moved), every day the file shows a line on, and the
    last day the bank could be stating for -- the window's end, or the day
    the header names where that is earlier -- so a sync over a quiet window
    lands on the day the bank stated.  Deduplicated: for a CSV the window's
    end IS its last line day.

    **The stated day is a candidate only inside the window's own floor.**  A
    header dated before the day before the window names a day the file
    cannot vouch for, and ``budget.level_lies_within_file`` refuses a level
    there -- so it is not offered, and such a file places nothing rather
    than 500ing at the flush.  The pre-existing bound case
    (``test_the_bound_also_covers_the_day_BEFORE_the_first_line``) fired on
    a first cut that offered it.

    Args:
        parsed: The file, with its claim present.

    Returns:
        The candidates, ascending, each at most once.
    """
    floor = parsed.declared_start - _ONE_DAY
    days = {line.posted_on for line in parsed.lines}
    days.add(floor)
    stated_for = min(parsed.declared_end, parsed.stated_balance_on)
    if stated_for >= floor:
        days.add(stated_for)
    return sorted(days)


def solve_effective_day(
    parsed: ParsedStatement, opening: Decimal,
) -> "date | None":
    """Return the day the file's stated balance is the balance for, or ``None``.

    Args:
        parsed: The file: its lines in chronological order, the window it
            declares, and the claim its header makes.  The claim must be
            present -- :func:`resolve_anchor` answers ``None`` for a file
            stating none before reaching here.
        opening: The balance before the window's first day, known
            independently of the stated balance.

    Returns:
        The LATEST of :func:`_candidate_days`, at or before the day the
        header names, satisfying ``stated - sum(lines up to it) == opening``;
        or ``None`` when no candidate day does.

    **The header's day is a BOUND rather than a filter applied afterwards**:
    a bank cannot state a balance for a day it has not reached, and
    ``budget.level_lies_within_file`` refuses such a row, so an unbounded
    solve turned a describable file into a 500.  Found by two independent
    adversarial reviews, 2026-08-23.

    **Two satisfying days are harmless for the BANK's own walk and that is
    proven rather than assumed.**  If ``d1 < d2`` both satisfy it then the
    lines in ``(d1, d2]`` sum to zero, so for any day ``D`` the balance
    derived from the anchor at ``d1`` is ``stated + sum((d1, D])`` and the
    one derived from ``d2`` is ``stated - (sum((d1, d2]) - sum((d1, D]))``,
    which is the same value.  Measured: 0 of the developer's 5 real exports
    admit two.  **It is NOT harmless for the CASH walk**, which absorbs app
    rows by their settle day, and that is why the candidates are the line
    days and the stated day rather than every day of the window (ruling
    **R-BAL74**; the module docstring): a quiet day is never named when a
    line day solves.
    """
    totals = _cumulative_by_day(parsed.lines)
    not_after = parsed.stated_balance_on
    solved = None
    running = Decimal("0.00")
    for day in _candidate_days(parsed):
        if day > not_after:
            break
        # A candidate with no line carries the total through the day before
        # it: the day before the window, and a quiet stated day.
        running = totals.get(day, running)
        if parsed.stated_balance - running == opening:
            solved = day
    return solved


def _refuse_self_contradiction(
    lines: list, stated_balance: Decimal, opening: Decimal,
) -> None:
    """Raise because the file's own CHAIN reaches its header figure on no day.

    Args:
        lines: The file's lines, chronological, carrying a running balance.
        stated_balance: What its header claims.
        opening: The balance its own chain states before the first line.

    Raises:
        StatementBalanceUnexplained: Always.  It carries BOTH figures, because
            the pair is what tells the owner the file disagrees with ITSELF
            rather than with anything the app believes -- which is the only
            disagreement a re-export can be expected to fix.
    """
    implied = opening + _cumulative_by_day(lines)[lines[-1].posted_on]
    raise StatementBalanceUnexplained(
        stated_balance,
        implied,
        f"its header says the account held {stated_balance}, while its own "
        f"per-line running balance puts it at {implied} after "
        f"{lines[-1].posted_on} and reaches that header figure on no day it "
        f"covers",
    )


def window_opening(
    account_id: int, day: date,
) -> "KnownOpening | CoverageGap | None":
    """Return what this account's RECORDED statements say of the day before *day*.

    **The balance before any line posted on *day* is the balance at the END of
    the day before it**, so this asks :func:`~._balance.fold_bank_balances` for
    that one day rather than restating the walk.  That is not tidiness: this
    function and the report added at plan step ``bank_import:X-f6e-2`` both
    answer "what does the bank's own record say this account held", and two
    spellings of one quantity is the shape that put ``$15.96`` between the cash
    scalar and the cash series on the real Checking account.

    **Three answers, from the ONE fold** (ruling **bank_import:R-BI35**, plan
    step ``bank_import:X-f6b-2``): the opening, priced; a :class:`CoverageGap`,
    when the day is unpriced while some run of the record is anchored; or
    ``None``, when no run is anchored at all and so the record prices no day.
    The import door resolves a file's claim against this
    (:func:`resolve_anchor`), and the last two are why: a true first import
    has nothing to check against, and a window after a gap had a record that
    did not reach it.  Whether ANY run is anchored is read off the same fold
    (:attr:`~._balance.BankBalances.prices_any_day`) rather than by asking
    for the standing levels a second time -- the held cut of this step did
    that, and also counted a standing level no run reaches, which prices
    nothing and would have left every later claim unsolved for good.

    Args:
        account_id: The account to walk.
        day: The window's first day; the opening is the balance BEFORE any
            line posted on it.

    Returns:
        The :class:`KnownOpening`, a :class:`CoverageGap`, or ``None``, as
        above.

    **The evidence comes back with the figure, and never stronger than
    ``corroborated``**: :func:`recorded_opening_before` says why.
    """
    day_before = day - timedelta(days=1)
    folded = fold_bank_balances(account_id, [day_before])
    balance = folded.balances.get(day_before)
    if balance is None:
        return CoverageGap() if folded.prices_any_day else None
    return KnownOpening(
        amount=balance,
        evidence=weaker_of(
            StatementBalanceEvidenceEnum.CORROBORATED,
            folded.anchor_for(day_before).evidence,
        ),
    )


def recorded_opening_before(
    account_id: int, day: date,
) -> "KnownOpening | None":
    """Return what this account's RECORDED statements say it held before *day*.

    :func:`window_opening`'s answer with a gap and an unpriced account read
    alike, as ``None``: the question "what did the record say it held", for a
    reader that does not resolve a claim and so does not need to know why
    nothing is said.  One producer, so the two cannot price a day apart.

    Args:
        account_id: The account to walk.
        day: The day to answer for; the balance returned is the one BEFORE any
            line posted on it.

    Returns:
        The :class:`KnownOpening`, or ``None`` when no run of this account's
        recorded lines holds a standing bank level that reaches the day
        before *day*.

    **The evidence comes back with the figure, and never stronger than
    ``corroborated``.**  Reaching an answer here means two statements agree,
    which is exactly what that level means -- and :func:`weaker_of` caps it at
    the anchor's own strength, so an anchor the app merely assumed yields an
    assumption rather than laundering itself into a determination.  **The cap
    is applied HERE and not in the fold**, because it states what THIS reader
    learned: a report displaying the same figure learns nothing new and carries
    the anchor's own strength unchanged.

    **The anchor it is capped against is the one that PRICED the day** (plan
    step ``balance:X-bj-1b``, ruling **R-BAL66**): the walk anchors per run,
    so the day before a file's first line is walked from the anchor of the
    run it sits in, and :meth:`~._balance.BankBalances.anchor_for` names that
    anchor off the same membership the price came from.  A file continuing an
    assumed run therefore opens with a SOLVED day held ``uncorroborated``,
    where the account-wide walk gave it no opening and a guessed day.  A
    checkpoint in that run that disagrees with the walk changes nothing here:
    it is reported on the agreement page, and by rank it is the weaker of the
    two figures.

    **Coverage is why this can answer ``None`` on an account that HAS an
    anchor**, and the direction it fails in is deliberate.  The walk is only
    exact if every line between the anchor and *day* is recorded; a gap between
    two imports means lines nobody has imported, and summing across one yields
    a confident wrong number.  The fold leaves such a day out of its result,
    and the import door then records the claim UNSOLVED (a gap,
    :class:`CoverageGap`) or assumes a day on a true first import, and the
    receipt SAYS which -- an unchecked claim the owner is told about beats a
    checked one that is false.
    """
    opening = window_opening(account_id, day)
    return opening if isinstance(opening, KnownOpening) else None


def resting_on(
    standing: "list[AccountAnchorHistory]", day: date,
    except_import_id: "int | None" = None,
) -> "list[AccountAnchorHistory]":
    """Return which of *standing* rest on lines at or after *day*.

    **THE predicate, stated once** (plan step ``bank_import:X-gr``, finding
    **BI-490**, rule 14).  A placement is a conclusion drawn from the lines at
    or before its own day, so a placement on or after *day* rests on lines a
    change at *day* touches.  :func:`release_anchors_from` acts on this list;
    the delete confirmation counts it BEFORE the rows go
    (:func:`~._reads.import_history`), and the receipt counts it after -- and
    because both reach this one function, the confirmation cannot count
    differently from the act it confirms.  It lived inline in the release as a
    SQL filter until this step, which would have left a preview as a second
    spelling of the same question.

    Args:
        standing: :func:`~._balance.standing_bank_levels`' rows.
        day: The earliest day whose lines changed.
        except_import_id: An import whose own level is left alone -- the one
            whose write this is -- or ``None``.

    Returns:
        The members of *standing* the change undercuts, in *standing*'s order.
    """
    return [
        level for level in standing
        if level.observed_on >= day
        and level.statement_import_id != except_import_id
    ]


def release_anchors_from(account_id: int, day: date, import_id: int) -> int:
    """Withdraw every standing anchor a line change on or after *day* undercut.

    **An anchor is a conclusion drawn from the lines recorded at or before its
    own day, so a write that changes those lines takes the conclusion with
    it.**  Both doors that change them call this:
    :func:`~._record.record_parsed`, the recording walk every source shares,
    with the earliest day it freshly recorded, and
    :func:`~._undo.delete_import` with the earliest day whose
    lines it is about to remove.  Which levels go is :func:`resting_on`'s
    answer over :func:`~._balance.standing_bank_levels`; this function only
    writes.

    **It writes a release row per level and edits nothing** (plan step
    ``balance:X-bj-1``): the level relation is append-only at the database
    tier, and a withdrawal that stays beside the level it withdrew is what
    lets the statements page say WHY a figure is no longer placed.

    Args:
        account_id: The account whose anchors to examine.
        day: The earliest day whose lines changed.
        import_id: The import whose lines changed -- the cause the release
            records, and the import whose OWN level is never released by its
            own lines.  ONE parameter for both roles because they are one
            import at both doors: the recording import solved its level
            against its own complete line list, so those lines never undercut
            it; the delete door names the DOOMED import here before deleting
            it, so the cause reaches ``system.audit_log`` before the key's
            ``SET NULL`` takes it off the row, and that import's own level is
            about to cascade rather than be withdrawn.

    Returns:
        How many anchors were released.

    **Reproduced as money before this existed**, by two independent adversarial
    reviews on 2026-08-23.  A later export inserting a line into a day an
    earlier anchor had already priced left that anchor believing a balance the
    bank had since restated, and the next import solved its own day against it
    -- storing a day two days early under a *corroborated* badge, and refusing
    an honest export with a figure the bank's file never asserted.  Deleting an
    import removed the lines an anchor rested on while a later overlapping
    import kept its span, so the coverage test reported "covered" over a
    `$150.00` hole.  One rule closes both: the evidence moved, so the
    conclusion goes.

    **Released rather than re-solved, deliberately.**  Re-solving would be the
    app inferring its way around facts that have changed underneath it; the
    next import re-establishes an anchor from evidence that is actually
    present, and until then the account honestly holds none.
    """
    released = resting_on(standing_bank_levels(account_id), day, import_id)
    for level in released:
        db.session.add(AnchorRelease(
            account_id=account_id,
            anchor_id=level.id,
            released_by_import_id=import_id,
            lines_changed_from=day,
        ))
    return len(released)


def resolve_anchor(
    parsed: ParsedStatement, recorded: "KnownOpening | CoverageGap | None",
) -> "ImportedBalance | None":
    """Return what this file determines about its own stated balance.

    Args:
        parsed: The file -- its lines in chronological order, the window it
            declares and the claim its header makes, or none.  The two claim
            fields are ``None`` together, which the adapter holds and this
            refuses to depend on: a function whose whole job is to be total
            over its inputs may not rest a guard on another module's
            invariant.  The lines may be EMPTY (a feed sync over a quiet
            window states a balance and no line); only the file-chain arm
            reads a line, and an empty file carries no chain.
        recorded: What the account's already-recorded statements say of the
            day before the window's first day (:func:`window_opening`): the
            balance it held, with the strength of that claim; a
            :class:`CoverageGap`, when the record prices other days and does
            not reach this one; or ``None``, when the record prices no day at
            all -- a true first import.

    Returns:
        The :class:`ImportedBalance`, or ``None`` when the file states no
        balance at all.  A file that DOES state one but whose own lines cannot
        reach the day it claims, or whose opening lies in a coverage gap, gets
        a value with ``effective_on`` and ``evidence`` both ``None`` -- the
        claim recorded, the anchor undetermined, which is the honest absence
        rather than a guess.  :attr:`~ImportedBalance.day_is_solved` says
        which arm answered: the first two WORK OUT a day, the last assumes
        one; :attr:`~ImportedBalance.opening_known` says whether there was an
        opening to work it out against.

    Raises:
        StatementBalanceUnexplained: When the file carries a per-line running
            balance that reaches its own header figure on no day it covers.
            **Only that**: a mismatch against RECORDED history is not the
            file's fault, and an earlier draft refused honest exports for it.

    **Four arms, and each is named rather than reached by falling out of a
    loop.**  A first draft iterated ``(chain, recorded)`` and returned on the
    first non-``None``, which stated a fallback that did not exist -- a chain
    that failed to solve never tried the recorded opening, correctly, but the
    shape said otherwise.  Found by adversarial review 2026-08-23.

    **The assumed arm is a true first import's alone** (ruling
    **bank_import:R-BI35**, plan step ``bank_import:X-f6b-2``).  Until then it
    fired whenever the opening was unpriced, a gap included, and that is the
    ORDINARY night for the bank feed: the day before one of its runs is often
    a raw day, a hole in coverage, so a claim the feed DERIVED (Bridge's
    balance less every line after the run) would have been placed as a level
    every night, badged uncorroborated, with nothing to expose a wrong figure.
    Measured 2026-09-20 on the developer's feed, a line in Bridge's list and
    not in its balance would have put that level 43.40 above the recorded
    walk.  A CSV after a gap took the same arm with an exact figure on a
    guessed day, and the developer's 2026-08-16 lag shape shows the guess
    can be wrong by the lines it ignores (-1,006.72 there).  Recorded
    unsolved, the claim is on the receipt and the statements page, and it
    places nothing.  The cost is stated: a span only a CSV can fill leaves
    every later claim of that run unsolved until someone fills it.
    """
    if parsed.stated_balance is None or parsed.stated_balance_on is None:
        return None
    claim = {
        "stated": parsed.stated_balance, "stated_on": parsed.stated_balance_on,
    }
    chain = opening_balance(parsed.lines)
    if chain is not None:
        # The file states the opening itself, so it is answerable from the file
        # alone -- and a failure to solve is the file contradicting itself.
        solved = solve_effective_day(parsed, chain)
        if solved is None:
            _refuse_self_contradiction(
                parsed.lines, parsed.stated_balance, chain,
            )
        return ImportedBalance(
            **claim,
            effective_on=solved,
            evidence=StatementBalanceEvidenceEnum.FILE_CHAIN,
            day_is_solved=True,
            opening_known=True,
        )
    if isinstance(recorded, KnownOpening):
        # What the account already holds decides, and no failure to solve is
        # the file's fault: the movements explaining a date-range export's
        # header are simply not in it.
        solved = solve_effective_day(parsed, recorded.amount)
        return ImportedBalance(
            **claim,
            effective_on=solved,
            evidence=None if solved is None else recorded.evidence,
            day_is_solved=solved is not None,
            opening_known=True,
        )
    if isinstance(recorded, CoverageGap):
        # The record prices other days and does not reach the day before this
        # window: a span nobody imported, or a raw day the feed is still
        # re-fetching.  Assuming a day here would place the claim as a level
        # with no check (ruling **R-BI35**); recorded unsolved, it is visible
        # and governs nothing.  A window whose opening IS priced solves its
        # own, which the feed's next night is once the hole closes and a CSV
        # gap is once someone fills it.
        return ImportedBalance(
            **claim, effective_on=None, evidence=None, day_is_solved=False,
            opening_known=False,
        )
    # Nothing constrains it: the record prices no day at all, which is what a
    # FIRST import is -- or an account whose every level has since been
    # released, which the next import re-establishes from.  The figure is
    # taken as the balance at the end of the declared window -- what the bank
    # means when nothing is pending; for a CSV that is its last line's day --
    # bounded by the day the header names, because a balance cannot be
    # effective on a day the bank had not reached.
    #
    # **This arm ASSUMES a day rather than solving one, and it says so**
    # (``day_is_solved=False``, the constructor's default made explicit here
    # because this is the one place the answer is False for a PLACED figure).
    # Its evidence level cannot carry that: the arm above mints the same
    # ``uncorroborated`` for a day it PROVED against an unconfirmed opening.
    return ImportedBalance(
        **claim,
        effective_on=min(parsed.declared_end, parsed.stated_balance_on),
        evidence=StatementBalanceEvidenceEnum.UNCORROBORATED,
        day_is_solved=False,
        opening_known=False,
    )
