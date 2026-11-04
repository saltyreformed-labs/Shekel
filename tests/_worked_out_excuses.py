"""The grains the worked-out ledger and the stored one part at, and the one home of an excuse.

Half of X-ew's first leaf's production run (plan step plan#2513, plan task plan#2528), which
:mod:`tests._worked_out_ledger` compares and ``tests/manual/verify_worked_out_ledger.py`` runs:
what a difference is judged at -- a stored posting's or a worked-out line's :data:`Contribution`,
netted per cell -- and which differences the leaf's spec expects ("The proof", item 2), each
counted in a census of the stored entries of its kind (:func:`take_census`).

**An excuse is granted only where a censused stored entry of its kind sits, and only by that
kind's rule** (:func:`classify`; review plan#2521, findings C-H2 and C-L2; the leaf's spec: "Any
other difference is a defect"), so every excused source cell maps to an entry the census names:

* **residue**, where the store's residue nets to $0.00 in the cell -- one (chart account,
  paycheck, day) -- never across paychecks, where it would move two paychecks' statements;
* **a correction filed in another paycheck**, of a kind filed by its day (never a loan payment
  split, filed in its payment's paycheck): the store files part of it outside the paycheck
  today's calendar files its day in, the read files all of it inside that one, and the two net
  equal across paychecks;
* **a transfer family the cash resync skips**, any difference on the family, while the store
  holds an entry of it;
* **a row the cash resync does not walk**, where the read books nothing in the cell and the
  store's lines on that movement net to $0.00 on each chart account: a line a door's own sync
  posted and REVERSED (design report s.9.3: they "rest on the door's own sync having reversed
  them"), its two halves in two cells.  Never a line the read books, nor one nothing reversed,
  which is money the store holds that the records do not (review plan#2521's second review, H1).

A rule that excuses too little fails loudly -- an expected difference comes out UNEXPLAINED and
the run exits 1.  That none excuses too much is shown by the run's planted controls, one just
outside each condition a plant can reach (``tests/manual/verify_worked_out_ledger.py``).  One
condition no plant here reaches is graded by construction only: a skipped family's census entry
(no fixture could build a family the store holds nothing of, since a projected transfer holds no
movement and settling one posts it).

Underscore-prefixed module name keeps pytest from collecting it.
"""

from collections import defaultdict, namedtuple
from decimal import Decimal

from app import ref_cache
from app.enums import PostingSourceEnum

_ZERO = Decimal("0.00")

# The source grains.  A movement-linked stored entry and a worked-out movement
# line are the movement's; a correction (opening, true-up, loan payment
# split) is its kind's, since a correction is derived and comes from no row;
# a stored entry of a movement kind that links nothing is hard-delete residue.
MOVEMENT = "movement"
CORRECTION = "correction"
RESIDUE = "residue"

# The expected kinds of difference (the leaf's spec, "The proof", item 2),
# in the order the census prints them.  The run's controls each name the kind
# whose rule they sit just outside of.
EXPECTED_RESIDUE = "hard-delete residue"
EXPECTED_FILING = "correction filed in another paycheck"
EXPECTED_SKIPPED = "transfer family the cash resync skips"
EXPECTED_UNWALKED = "row the cash resync does not walk"
EXPECTED_KINDS = (
    EXPECTED_RESIDUE, EXPECTED_FILING, EXPECTED_SKIPPED, EXPECTED_UNWALKED,
)

# One stored posting or one worked-out line, at source grain.  ``source`` is
# ``(grain, id)``: a movement's id, or a correction's or residue's source kind
# id.  ``key`` and ``rows`` are its chart account
# (``tests._ledger_parity.ParityChart``): the stored row it sits on, or the
# owner's stored row for a worked-out line's key, ``()`` for none.
# ``entry_id`` is the stored journal entry, ``None`` for a worked-out line.
Contribution = namedtuple(
    "Contribution",
    "source key rows pay_period_id entry_date amount entry_id",
)


def cell_of(contribution):
    """Return a contribution's (chart key, stored rows, paycheck, day) cell."""
    return (
        contribution.key, contribution.rows, contribution.pay_period_id,
        contribution.entry_date,
    )


def source_cell_of(contribution):
    """Return a contribution's (source, key, stored rows, paycheck, day) cell."""
    return (contribution.source,) + cell_of(contribution)


def account_of(contribution):
    """Return a contribution's chart account, ``(key, stored rows)``."""
    return contribution.key, contribution.rows


def _without_paycheck(source_cell):
    """Return a source cell less its paycheck: one correction, wherever filed.

    ``(source, key, stored rows, day)``, the grain a re-filed correction is
    judged at (:func:`_refiled`).
    """
    source, key, rows, _pay_period_id, day = source_cell
    return source, key, rows, day


def _correction_of(contribution):
    """Return the correction a contribution belongs to, across paychecks."""
    return _without_paycheck(source_cell_of(contribution))


def _movement_on_account(contribution):
    """Return a contribution's ``(source, key, stored rows)``: a source on one account.

    A source cell's first three parts, every paycheck and day together: the
    grain the store's report netted a movement at (its
    ``_purchase_dated_nets``, until plan task plan#2771 moved the report onto
    the worked-out ledger).
    """
    return source_cell_of(contribution)[:3]


def nets(contributions, grain):
    """Return each grain cell's net over *contributions*.

    Args:
        contributions: :data:`Contribution` s.
        grain: A function of one contribution giving its cell.

    Returns:
        ``{cell: net}``.
    """
    netted = defaultdict(lambda: _ZERO)
    for contribution in contributions:
        netted[grain(contribution)] += contribution.amount
    return netted


def paired(stored, worked):
    """Return ``{cell: (stored net, worked-out net)}`` over both maps' cells."""
    return {
        cell: (stored.get(cell, _ZERO), worked.get(cell, _ZERO))
        for cell in stored.keys() | worked.keys()
    }


def correction_kind_ids():
    """Return the source kinds a correction is booked under.

    The five the store's statement reader kept as sourceless corrections
    (its ``_correction_dated_nets``, until plan task plan#2771 moved the report
    onto the worked-out ledger).
    """
    return {
        ref_cache.posting_source_id(member)
        for member in (
            PostingSourceEnum.LOAN_OPENING,
            PostingSourceEnum.LOAN_TRUEUP,
            PostingSourceEnum.LOAN_PAYMENT,
            PostingSourceEnum.ACCOUNT_OPENING,
            PostingSourceEnum.ACCOUNT_TRUEUP,
        )
    }


def _filed_by_day_kind_ids():
    """Return the correction kinds filed by their day on today's calendar.

    Every correction but a loan payment's split, which is filed in its
    payment's paycheck (ruling R-BAL102).  These are the kinds whose stored
    paycheck a calendar change can leave behind (ruling plan#2519).
    """
    return correction_kind_ids() - {
        ref_cache.posting_source_id(PostingSourceEnum.LOAN_PAYMENT),
    }


def filed_in(ledger, day):
    """Return the paycheck the owner's calendar files a correction dated *day* in.

    ``PayCalendar.filing_period``, the read's rule for a correction's
    paycheck (ruling plan#2519).

    Args:
        ledger: The :class:`~tests._worked_out_ledger.Ledger`, whose
            calendar it reads.
        day: The correction's day.

    Returns:
        The ``budget.pay_periods`` id.
    """
    return ledger.calendar.filing_period(day).period_id


def take_census(ledger, skipped):
    """Return each expected kind's stored entries: the census.

    The one place an entry is named an expected kind (:func:`_census_kind`);
    :func:`classify` excuses a difference only where one of these sits.

    Args:
        ledger: The :class:`~tests._worked_out_ledger.Ledger`.
        skipped: The transfer ids the cash resync skips.

    Returns:
        ``{kind: [entry, ...]}``, every kind present, each entry its list of
        contributions, by entry id.
    """
    entries = defaultdict(list)
    for contribution in ledger.stored:
        entries[contribution.entry_id].append(contribution)
    filed_by_day = _filed_by_day_kind_ids()
    held = {kind: [] for kind in EXPECTED_KINDS}
    for entry_id in sorted(entries):
        kind = _census_kind(
            ledger, entries[entry_id][0], skipped, filed_by_day,
        )
        if kind is not None:
            held[kind].append(entries[entry_id])
    return held


def _census_kind(ledger, contribution, skipped, filed_by_day):
    """Return the expected kind a stored entry belongs to, by its first leg.

    Residue; a correction of a kind filed by its day, filed outside the
    paycheck today's calendar files its day in; or a movement of
    :func:`_movement_kind`'s.  ``None`` for any other entry.
    """
    grain, ident = contribution.source
    if grain == RESIDUE:
        return EXPECTED_RESIDUE
    if grain == MOVEMENT:
        return _movement_kind(ledger, ident, skipped)
    if ident in filed_by_day and (
            contribution.pay_period_id
            != filed_in(ledger, contribution.entry_date)):
        return EXPECTED_FILING
    return None


def _movement_kind(ledger, movement_id, skipped):
    """Return the expected kind a movement's difference may be, or ``None``.

    A transfer the cash resync skips, or a row it does not walk (design
    report s.9.3): the store's lines on either rest on a door's own sync.
    :func:`classify` narrows each by its rule.
    """
    parent = ledger.parent_of.get(movement_id)
    if parent is None:
        return None
    if parent[0] == "transfer" and parent[1] in skipped:
        return EXPECTED_SKIPPED
    if parent not in ledger.walked:
        return EXPECTED_UNWALKED
    return None


def classify(ledger, differing, skipped, census):
    """Return each differing source cell's expected kind, or ``None``.

    An excuse is granted only inside the footprint of a censused stored
    entry of its kind (:func:`_footprints`), so every excused source cell
    maps to an entry the census names, and then only by its kind's rule:

    * residue: where the store's residue nets to $0.00 in the cell's own
      (chart account, paycheck, day) -- a reversal pair -- never across
      paychecks, where it moves two paychecks' statements (review plan#2521,
      C-L2); residue that does not net to zero in its cell is money the
      records lost;
    * a movement on a transfer the cash resync skips: any difference on the
      family, whose store holds the lines a door's own sync left;
    * a movement on a row the cash resync does not walk: only where the read
      books nothing in the cell and the store's lines on the movement net to
      $0.00 on the cell's chart account -- a line a door's own sync posted
      and reversed (design report s.9.3) -- never a line the read books
      (C-H2 (b)), nor one nothing reversed;
    * a correction: only one :func:`_refiled` names (C-H2 (a)).

    Args:
        ledger: The :class:`~tests._worked_out_ledger.Ledger`.
        differing: ``{source cell: (stored net, worked-out net)}``, every
            source cell whose two nets part.
        skipped: The transfer ids the cash resync skips.
        census: :func:`take_census`' entries.

    Returns:
        ``{source cell: kind name | None}``.
    """
    footprint = _footprints(ledger, census)
    grounds = _Grounds(
        footprint=footprint,
        residue=nets(
            (c for c in ledger.stored if c.source[0] == RESIDUE), cell_of,
        ),
        refiled=_refiled(ledger, footprint[EXPECTED_FILING]),
        standing=nets(
            (c for c in ledger.stored if c.source[0] == MOVEMENT),
            _movement_on_account,
        ),
    )
    return {
        cell: _excused_as(ledger, skipped, grounds, cell, worked)
        for cell, (_stored, worked) in differing.items()
    }


# What :func:`classify` judges every differing source cell against, built
# once: each kind's footprint (:func:`_footprints`); the store's residue net
# per (chart account, paycheck, day) cell; the re-filed corrections
# (:func:`_refiled`); and the store's net of each movement on each chart
# account, every paycheck and day (:func:`_movement_on_account`), $0.00 once a
# door's own sync has reversed what it posted.
_Grounds = namedtuple("_Grounds", "footprint residue refiled standing")


def _excused_as(ledger, skipped, grounds, cell, worked):
    """Return the expected kind one differing source cell is excused as, or ``None``.

    :func:`classify`'s rules, for one cell.

    Args:
        ledger: The :class:`~tests._worked_out_ledger.Ledger`.
        skipped: The transfer ids the cash resync skips.
        grounds: The :data:`_Grounds`.
        cell: The source cell, ``(source, key, stored rows, paycheck, day)``.
        worked: Its worked-out net.
    """
    grain, ident = cell[0]
    if grain == RESIDUE:
        kind = EXPECTED_RESIDUE
        excused = (
            cell[1:] in grounds.footprint[kind]
            and grounds.residue[cell[1:]] == 0
        )
    elif grain == MOVEMENT:
        kind = _movement_kind(ledger, ident, skipped)
        excused = (
            kind == EXPECTED_SKIPPED
            and ledger.parent_of[ident] in grounds.footprint[kind]
        ) or (
            kind == EXPECTED_UNWALKED
            and cell in grounds.footprint[kind] and worked == 0
            and grounds.standing[cell[:3]] == 0
        )
    else:
        kind = EXPECTED_FILING
        excused = _without_paycheck(cell) in grounds.refiled
    return kind if excused else None


def _footprints(ledger, census):
    """Return, per expected kind, where its censused entries can excuse a difference.

    * residue: the (chart account, paycheck, day) cells its legs sit in;
    * a correction filed in another paycheck: its (source, chart account,
      day), across paychecks (:func:`_without_paycheck`);
    * a skipped transfer family: the family, its parent transfer;
    * a row the resync does not walk: the source cells its legs sit in.

    Args:
        ledger: The :class:`~tests._worked_out_ledger.Ledger`.
        census: :func:`take_census`' entries.

    Returns:
        ``{kind: set of what its entries cover}``.
    """
    grains = {
        EXPECTED_RESIDUE: cell_of,
        EXPECTED_FILING: _correction_of,
        EXPECTED_SKIPPED: lambda leg: ledger.parent_of[leg.source[1]],
        EXPECTED_UNWALKED: source_cell_of,
    }
    return {
        kind: {grain(leg) for entry in census[kind] for leg in entry}
        for kind, grain in grains.items()
    }


def _refiled(ledger, censused):
    """Return each correction today's calendar files in another paycheck than the store's.

    A correction is its (source, chart account, day) across paychecks
    (:func:`_without_paycheck`).  It is re-filed when the store files part of
    it outside the paycheck today's calendar files its day in -- a censused
    entry, of a kind filed by its day -- while the read files ALL of it inside
    that paycheck, and the two net equal across paychecks: the same
    correction, its stored paycheck the one the calendar gave when it was
    written (ruling plan#2519).  A correction any line of which the read
    files outside that paycheck is not re-filed: the read is what moved it.

    Args:
        ledger: The :class:`~tests._worked_out_ledger.Ledger`.
        censused: The corrections a censused entry sits in.

    Returns:
        The re-filed corrections.
    """
    stored = nets(_corrections(ledger.stored), _correction_of)
    worked = nets(_corrections(ledger.worked), _correction_of)
    misfiled = {
        _correction_of(c) for c in _corrections(ledger.worked)
        if _correction_of(c) in censused
        and c.pay_period_id != filed_in(ledger, c.entry_date)
    }
    return {
        correction for correction in censused - misfiled
        if stored[correction] == worked[correction]
    }


def _corrections(contributions):
    """Return the contributions that are a correction's."""
    return [c for c in contributions if c.source[0] == CORRECTION]
