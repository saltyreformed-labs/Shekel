"""An account's opening and true-ups as ledger legs: the correction rule.

A non-loan account's books open at its stored opening equity and are corrected
at every balance assertion to the balance the owner asserted (Build-Order Step
5, the loan genesis pattern generalised), and this module is the RULE for what
each correction books, keyed by chart key:

    linked line           (anchor_balance - ledger_before)   [opening | trueup]
    counter line          (ledger_before - anchor_balance)   [opening | trueup]
                          -----------------------------------
                          0

``ledger_before`` is the records' own running total just before the assertion
(:func:`._account_walk.account_corrections`; the records' since plan task
plan#2525).  The counter
line is ruling **R-FO**'s dispatch over the account's projection kind
(:func:`~._chart.anchor_correction_counter_kind`).  The one public answer is
:func:`account_correction_targets`, which the worked-out read sums; it lived
in the stored ledger's writer (``account_posting_service._anchors``) until
plan task plan#2526 moved it here, and the writer reconciled to it until plan
task plan#3133 deleted it.

**The period is DERIVED FROM THE DAY, and since plan step X-f1c3c that is the
app's ONLY day-to-period rule** (ruling R-DH: *"an assertion's period and the
civil day it was true are two statements of one fact, and the moment they can
be set independently they can disagree"*).  Every anchor correction, cash and
loan alike, is filed through
:meth:`app.services.pay_calendar.PayCalendar.filing_period` against the entry's
own date, on the owner's calendar
(:func:`app.services.pay_calendar.calendar_for`).  **The reader that windows by
paycheck files it, never this rule** (finding plan#2875,
:meth:`app.services.ledger_lines.LedgerLine.filed_period_id`): a correction's
line names no paycheck, so the balance sheet and the re-type refusal, which
read none, ask no calendar.

**That rule became ONE clamp at plan step C2-d.**  It was
``loan_ledger.resolve_anchor_pay_period`` -- containment, else the latest
period ENDING before the day, else the earliest -- a three-branch chain across
two functions, one of them a public export of the LOAN package that this cash
package had to import (finding **N-169**).  The 2026-08-10 ruling named what
the chain was actually computing: *the latest period STARTING on or before the
day, else the earliest*.  The equivalence to that chain, the PRECONDITION it
holds under, and the proofs covering each half are stated once at
:meth:`app.services.pay_calendar.PayCalendar.filing_period` rather than
repeated here and in the loan twin.

**That sentence used to carry a limit, and finding N-170 was the limit** --
closed structurally rather than repaired.  A WRITE-side resolver,
``account_service.resolve_anchor_period_id``, fell back to the user's EARLIEST
period when no period contained the day, while this one falls back to the LATEST
period ending before it: they agreed inside the schedule and diverged maximally
for a day AFTER it, index 0 against index 60 on a 61-period production calendar.
Ruling R-EO deleted the assertion's stored period and ruling R-EH deleted the
account's anchor columns, which left that resolver with no caller at all, so it
is DELETED and one rule remains.

One limit does survive, and it belongs to the ledger rather than to a
disagreement.  An entry's ``pay_period_id`` and its ``entry_date`` are NOT "two
columns that cannot drift": whenever no period contains the day, the entry is
filed in a period its own date falls outside, by construction and deliberately
-- the alternative is a correction filed under no paycheck at all, which every
per-period reader would then be unable to place; the ``NOT NULL`` on
``journal_entries.pay_period_id`` EXPRESSES that rather than causing it (ruling
**pay_calendar:R-PC53**).  What the derivation buys is that
the drift is a function of the CALENDAR rather than of which clock a writer
happened to read, and that it self-heals the moment the containing period
exists.

**What that replaced, and the two ways it was wrong** (finding N-161).  The
key was ``(source kind, entry date)`` with the period carried alongside it in
a parallel map, written last-fact-wins from ``account_anchor_history``'s stored
``pay_period_id`` -- the source row's CURRENT period, verbatim what R2 forbade
(the stored ledger's ``posting_service``: a correction "carries the PAY PERIOD
of the postings it reverses ... never the source row's current period").  Measured
on production data: two assertions observed 2026-06-03 whose stored periods are
5 and 6 had posted as ``+$3,054.36`` in period 5 and ``-$2,854.36`` in period
6 -- one key could not tell them apart, so the second reconciled as a delta
against the first's whole posted amount, and its reversal of period-5 postings
was filed into period 6.

**Reading the STORED period was the second defect, and a first build of this
step kept it.**  ``account_anchor_history.pay_period_id`` was not an
independent fact -- it was a CACHE of this same derivation, written by
``resolve_anchor_period_id`` from the same day, and ruling R-EO has since
deleted BOTH.  The row that made the defect visible was filed by a broken
clock (created 21:28 Eastern on period 5's last
day, stored against period 6 -- exactly the case
``routes/accounts/crud.py``'s own comment was written to prevent: *"the grid
buckets the correction by ``observed_on`` and the ledger stamps it with
``pay_period_id``, so the two surfaces disagree by the whole correction"*).
Projecting that cache would have made the posted ledger disagree with the grid's
"Book vs bank" row by the whole correction, permanently.  Measured on a
production clone across 61 periods: deriving from the day agrees with
:func:`app.services.balance_at.grid_balance_view` on EVERY period; reading the
stored column disagrees on two.  **The comparison is over TRUE-UPS, and saying
so is load-bearing**: ``_assertion_sums`` folds every ASSERTION correction,
while the ledger also keeps an ``account_opening`` entry in a period -- so an
unscoped comparison could not have agreed on 61 and a draft of this docstring
implied one had.  *That sentence read ``corrections[1:]`` and "excluding the
OPENING because ruling R-I moves it into the fold's seed" until plan step
X-f3c-2a; the fold seeds from the stored opening equity now, so no correction is
excluded there and the ledger's opening entry books that same stored fact.*
The agreement is pinned by
``TestLedgerAgreesWithTheGridOnAssertionPeriods`` rather than left as a
measurement in prose (finding N-169's other half).  A mis-filed row is a defect
in the ROW (finding N-168), repaired at its source -- and until it is, the
ledger states the day's truth rather than inheriting the row's error.

Two same-day assertions always share a key, the day alone saying WHEN, so they
still merge into one entry; separating THOSE needs an identity the ledger does
not carry yet (plan step X-ai-s).
"""

from decimal import Decimal

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum
from app.models.account import Account
from app.services.account_projection import classify_account

from ._account_walk import AccountAnchorCorrection
from ._chart import (
    ChartKey,
    account_counter_key,
    anchor_correction_counter_kind,
    linked_key,
)
from ._targets import ChartLegMap, CorrectionKey, merge_target_legs


def _account_correction_kinds(
    correction: AccountAnchorCorrection,
) -> tuple[PostingSourceEnum, PostingKindEnum]:
    """Return the (journal source kind, posting leg kind) for one correction.

    The account's OPENING EQUITY books the opening (source ``account_opening``,
    leg kind ``opening``); every balance assertion books a TRUE-UP (source
    ``account_trueup``, leg kind ``trueup``).  The leg kinds are the same
    ``opening`` / ``trueup`` pair the loan corrections use (REUSED by design --
    the journal SOURCE distinguishes account from loan corrections).

    **Keyed off a STORED fact since plan step X-f3c-2a**
    (:attr:`~._account_walk.AccountAnchorCorrection.opens_the_books`, which the walk
    sets from ``budget.account_openings``).  It was keyed off
    ``CashAnchorFact.is_opening`` -- the account's earliest assertion by
    ``(observed_on, created_at, id)`` -- so a BACK-DATED assertion re-elected
    the opening and re-dated its journal entry, and the previous opening
    silently became a true-up.  *That key said ``(created_at, id)`` until plan
    step X-an-b, before plan step 2 made ``observed_on`` user-supplied, and
    trusting it tagged a ``$1,307.66`` true-up as the account's OPENING.  Two
    repairs of one ordering; the third was to stop inferring the answer.*

    Args:
        correction: The correction from :func:`._account_walk.account_corrections`
            whose kinds to resolve.

    Returns:
        ``(PostingSourceEnum, PostingKindEnum)`` -- ``(ACCOUNT_OPENING,
        OPENING)`` for the opening-equity correction, else ``(ACCOUNT_TRUEUP,
        TRUEUP)``.
    """
    if correction.opens_the_books:
        return PostingSourceEnum.ACCOUNT_OPENING, PostingKindEnum.OPENING
    return PostingSourceEnum.ACCOUNT_TRUEUP, PostingKindEnum.TRUEUP


def _correction_delta(correction: AccountAnchorCorrection) -> Decimal:
    """Return what one anchor correction books on the account's linked ledger.

    ``anchor_balance - ledger_before``: the jump from what the walked ledger
    said just before this assertion to what the assertion declares.  Zero means
    the correction books NOTHING, which is the one predicate two callers share
    -- :func:`_counter_keys` (which names no counter key for a correction that
    books nothing) and :func:`_account_anchor_correction_target` (which
    returns an empty target for it) -- so it is stated once rather than
    spelled twice and left to drift.

    Args:
        correction: The anchor correction from :func:`._account_walk.account_corrections`.

    Returns:
        The signed linked-ledger delta as a ``Decimal``.
    """
    return correction.target_balance - correction.ledger_before


def _counter_keys(
    account: Account,
    corrections: list[AccountAnchorCorrection],
) -> list[ChartKey | None]:
    """Return each correction's COUNTER chart key, ``None`` where it books nothing.

    Ruling **R-FO** made the counter leg a dispatch over the account's
    projection kind (:func:`~._chart.anchor_correction_counter_kind`),
    so an account can need up to two counter lines: ``anchor_equity`` for the
    correction that OPENS its books and, for a modelled account,
    ``interest_income`` or ``unrealized_change`` for the ones after it.  This
    walks the corrections once, in order, and returns one entry per correction
    -- so the caller never has to re-derive which line a correction belongs to.
    It resolved and minted each counter's stored row as it went until plan
    task plan#2524, as ``_counter_ledger_accounts``; it names the line's
    :class:`~app.services.ledger_lines.ChartKey` and touches no stored row.

    **"The opening" is the account's EARLIEST ASSERTION, and keying it on the
    DELTA series instead was tried and REFUSED.**  The tempting refinement is
    "the first correction that books a non-zero delta", which answers the case
    where the create form's ``$0`` pre-fill left the earliest assertion booking
    nothing.  It breaks a case that matters more: an account whose opening books
    nothing because THE RECORDS ALREADY EXPLAIN IT -- a Roth opened at
    ``$1,000.00`` with a ``$1,000.00`` settled transfer already dated before it
    -- would then have its first real ``$150.00`` market gain treated as capital
    and booked to equity, which is the defect ruling R-FO exists to close,
    reintroduced through the back door (adversarial review, 2026-08-14).
    ``is_opening`` is also a property of the ASSERTION HISTORY where a
    delta-keyed rule is a property of ONE SCENARIO's settled records: the same
    correction could open the books in one scenario and not in another.  The
    ``$0`` pre-fill was the real defect and it is fixed where it lives -- the
    create form now ASKS, which is ruling R-EX's own argument about the
    registration payday applied to the other figure nobody can default.

    **A key is named only where a correction really books into it.**  A
    correction whose ``ledger_before`` already equals its anchor balance names
    no key, so it books no line.  While the stored ledger's writer stood (until
    plan task plan#3133), that was also why it minted no row for one
    (``_posting_write.resolve_chart_targets``), so a fresh $0 account kept zero
    ledger rows and stayed hard-deletable; it resolved each key once per
    reconcile, which is what this function's reuse of a resolved row was for
    (the resolver issued a query and Checking carried 102 corrections).

    Args:
        account: The non-loan :class:`~app.models.account.Account` being
            reconciled, with ``account_type`` loaded (the dispatch classifies
            it).
        corrections: The account's corrections from
            :func:`._account_walk.account_corrections`, chronological.

    Returns:
        One entry per correction, in the same order: its counter
        :class:`~app.services.ledger_lines.ChartKey`, or ``None`` when the
        correction books nothing.

    Raises:
        ValueError: If the dispatch has no rule for the account's projection
            kind.
    """
    projection_kind = classify_account(account)
    counters: list[ChartKey | None] = []
    for correction in corrections:
        if _correction_delta(correction) == 0:
            counters.append(None)
            continue
        counters.append(account_counter_key(
            anchor_correction_counter_kind(
                projection_kind, is_opening=correction.opens_the_books,
            ),
            account.id,
        ))
    return counters


def _account_anchor_correction_target(
    correction: AccountAnchorCorrection,
    linked: ChartKey,
    counter: ChartKey | None,
) -> ChartLegMap:
    """Build the two-leg target for one anchor correction, or empty when it books nothing.

    The linked leg is ``anchor_balance - ledger_before`` (tagged ``opening``
    or ``trueup``); the COUNTER leg is its negative, so the two sum to zero and
    the linked ledger's implied balance moves from ``ledger_before`` to the
    asserted value.  A correction whose ``ledger_before`` already equals the
    anchor balance books NOTHING -- an empty target, so no zero leg is booked.

    **The counter leg NAMES what the difference was** (ruling **R-FO**): the
    equity opening, interest income, or change in value, decided by
    :func:`_counter_keys` and handed in here.  Only the counter side
    moves; the linked leg, its amount and its posting kind are exactly what they
    were before that ruling, which is why the whole change is balance-neutral on
    every account balance the app reports.

    Args:
        correction: The anchor correction from :func:`._account_walk.account_corrections`.
        linked: The account's own line's chart key, built once by
            :func:`account_correction_targets`.
        counter: This correction's counter chart key from
            :func:`_counter_keys`, or ``None`` when that function found the
            correction books nothing -- the same predicate this function
            applies, through the same :func:`_correction_delta`, so the two
            cannot disagree about which corrections have a counter.

    Returns:
        ``{chart key: (amount, posting_kind_id)}`` (the two balanced legs, or
        empty when the correction books nothing).
    """
    delta = _correction_delta(correction)
    if delta == 0:
        return {}
    _source_enum, posting_kind_enum = _account_correction_kinds(correction)
    posting_kind_id = ref_cache.posting_kind_id(posting_kind_enum)
    return {
        linked: (delta, posting_kind_id),
        counter: (-delta, posting_kind_id),
    }


def _account_anchor_correction_targets(
    corrections: list[AccountAnchorCorrection],
    linked: ChartKey,
    counters: list[ChartKey | None],
) -> dict[CorrectionKey, ChartLegMap]:
    """Merge an account's corrections into per-(source, day) targets.

    Groups every correction by its ``(source_kind_id, None, civil
    entry_date)`` key -- a correction is budgeted in no paycheck
    (:data:`._targets.CorrectionKey`) -- and sums the legs within each group
    (:func:`._targets.merge_target_legs`), so two
    anchors sharing their kind and day net to a single balanced target that
    lands the ledger on the LATER value -- exactly the combined jump they
    express (each correction's delta already accounts for the prior one).  A
    correction that books nothing still creates its key with an empty leg
    map, which books no line (the stored ledger's reconcile reversed an entry
    it had posted under that key to zero, until plan task plan#3133 deleted
    it).

    **WHEN is ONE fact**: the entry's date IS the assertion's
    ``observed_on``, and the paycheck a reader files it in is the one that day
    files under (:meth:`app.services.ledger_lines.LedgerLine.filed_period_id`,
    the derivation the loan twin's lines take too and the rule ruling R-DH
    states).  The key carried that paycheck, derived here, until finding
    plan#2875.  The stored ``account_anchor_history.pay_period_id`` was
    deliberately NOT read here while it stood -- it was a cache of this same
    derivation, and a row whose clock split it from its own day would have put
    the ledger permanently at odds with the grid; ruling R-EO deleted the
    column.  See the module docstring for the production measurement.

    Args:
        corrections: The account's corrections from
            :func:`._account_walk.account_corrections`, chronological.
        linked: The account's own line's chart key, built once by
            :func:`account_correction_targets`.
        counters: One counter chart key per correction, in the SAME order, from
            :func:`_counter_keys` -- ``None`` where the correction books
            nothing.  Positional rather than keyed because which line a
            correction belongs to is a property of its POSITION in the series
            (the first booking opens the books), not of anything on the row.

    Returns:
        ``{(source_kind_id, None, entry_date): {chart key: (amount,
        kind_id)}}``.
    """
    targets: dict[CorrectionKey, ChartLegMap] = {}
    for correction, counter in zip(corrections, counters, strict=True):
        source_enum, _posting_kind = _account_correction_kinds(correction)
        # The correction's journal entry is dated the day the assertion is the
        # CLOSING BALANCE for, read off the fact rather than re-derived from
        # its recording instant (ruling R-DH).  It was
        # ``_utc_civil_date(asserted_at)``, a second statement of a rule
        # ``cash_anchor_facts`` already resolves -- and one that put a
        # late-evening Eastern true-up on the next day's entry.
        observed_on = correction.observed_on
        key = (ref_cache.posting_source_id(source_enum), None, observed_on)
        merge_target_legs(
            targets.setdefault(key, {}),
            _account_anchor_correction_target(correction, linked, counter),
        )
    return targets


def account_correction_targets(
    account: Account,
    corrections: list[AccountAnchorCorrection],
) -> dict[CorrectionKey, ChartLegMap]:
    """Return an account's opening and true-up legs, keyed per correction.

    The ONE answer to "what does this account's anchor history book", which
    the worked-out read sums (plan task plan#2526; the stored ledger's writer
    reconciled to it, ``account_posting_service.reconcile_account_anchor_corrections``,
    until plan task plan#3133 deleted it): each correction's two legs --
    its linked leg on the account's own line and its counter leg on the line
    ruling **R-FO** names (:func:`_counter_keys`) -- merged per ``(source
    kind, entry date)`` (:func:`_account_anchor_correction_targets`).
    A correction that books nothing keeps its key with no legs, which books
    no line.

    Args:
        account: The non-loan account, with ``account_type`` loaded.
        corrections: Its corrections from
            :func:`._account_walk.account_corrections`, chronological.

    Returns:
        ``{(source_kind_id, None, entry_date): {chart key: (amount,
        posting_kind_id)}}``.

    Raises:
        ValueError: If the counter dispatch has no rule for the account's
            projection kind.
    """
    return _account_anchor_correction_targets(
        corrections,
        linked_key(account),
        _counter_keys(account, corrections),
    )
