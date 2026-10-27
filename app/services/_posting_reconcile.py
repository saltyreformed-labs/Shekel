"""Shared reconcile primitives for the correction-posting packages.

The pieces :mod:`app.services.loan_posting_service` (Build-Order Step 4 +
the read switch) and :mod:`app.services.account_posting_service` (Build-Order
Step 5) genuinely share, extracted here -- from the loan package's former
``_common`` module -- so neither package re-spells them (a ``duplicate-code``
finding) and both agree exactly:

* :func:`account_owner_id` -- resolve any account's owner id (both packages
  need it for the per-account equity ledger and the entry header).
* :func:`filing_calendar_for` -- the owner's pay calendar, refused when it is
  empty (both packages file every correction's entry under a period so that
  every per-period reader can place it; ``journal_entries.pay_period_id``'s
  ``NOT NULL`` EXPRESSES that rather than causing it -- ruling
  **pay_calendar:R-PC53**).
* :func:`summed_posting_legs` -- the grouped "what is already posted" query
  shape every posted-leg reader shares.
* :func:`account_chart_row_ids` -- the ids of one real account's own chart
  rows, through either link the table carries.
* :func:`posted_correction_legs` -- the posted anchor-correction reader,
  keyed by ``(source_kind_id, pay_period_id, entry_date)`` and scoped to
  entries touching any of ONE account's chart rows.
* :func:`delta_legs` -- turn a ``target`` ledger-leg map and the ``posted``
  ledger-leg map into the balanced DELTA legs that move posted to target.
* :func:`merge_target_legs` -- sum a correction's legs into a target bucket
  (the same-key merge both anchor reconciles apply).
* :func:`emit_correction_entry` -- the one definition of what a sourceless
  correction journal entry looks like (every concrete FK NULL, dated at the
  correction's civil date, described from its source kind).  An anchor's
  opening or true-up, and since plan step ``balance:X-bi-6-3`` (ruling
  **R-BAL102**) a loan payment's SPLIT: a derivation of the walk is keyed
  ``(kind, period, date)`` and links no row.
* :func:`emit_correction_deltas` -- the reconcile LOOP itself: union the
  target and posted keys, emit one balanced delta per key that differs.

Flask-isolated and commit-free like its consumers: plain data in, ORM
objects or plain values out; flushes only through the shared balanced-write
path; the caller owns the transaction boundary.
"""

import logging
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PostingSourceEnum
from app.extensions import db
from app.models.account import Account
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.services.pay_calendar import PayCalendar, calendar_for
from app.services.posting_reads import PostingError
from app.services._posting_write import (
    _MAX_DESCRIPTION_LENGTH,
    _PostingLeg,
    _emit_balanced_entry,
)

logger = logging.getLogger(__name__)

_ZERO_MONEY = Decimal("0.00")

# The correction reconcile key: (journal ``source_kind_id``,
# ``pay_period_id``, civil ``entry_date``).  Shared by the loan and account
# anchor reconciles and, since plan step ``balance:X-bi-6-3`` (ruling
# **R-BAL102**), the loan payment SPLIT -- a correction is a DERIVATION of a
# walk and has no concrete source FK to key on, so each is keyed by its source
# kind (opening / true-up / payment split) plus the period / date pair every
# reconcile in this ledger keys on.  The split carried the loan-side shadow's
# ``transaction_id`` until that step, and every teardown of that row owed it
# a reversal first; a key with no row has nothing to lose.
#
# **The period is in the key because R2 is an attribution rule, not a
# convention** (plan step X-ai-r, finding N-161).  A correction "carries the
# PAY PERIOD of the postings it reverses -- read back from the ledger per
# period, never the source row's current period"
# (:mod:`app.services.posting_service`).  The other two reconciles in this
# ledger obey it structurally by carrying the period in their key --
# ``_posting_write.posted_by_period`` for a movement (a transaction's and a
# transfer's own sources too, until plan#1845 deleted them) and
# the loan payment split's own reader (folded into :func:`posted_correction_legs`
# at ``balance:X-bi-6-3``), both ``(pay_period_id, entry_date)``.  This one did not: it read
# the posted side keyed ``(source_kind_id, entry_date)`` with no period, so
# it could not know WHICH period it was correcting, and the account reconcile
# re-supplied one from the source row's current period -- verbatim what the
# rule forbids.  Measured on production data, that filed a ``$2,854.36``
# reversal of period-5 postings into period 6.  The period is therefore not
# decoration on this tuple: it is the half of the key that makes a reversal
# land where the postings it undoes live.
CorrectionKey = tuple[int, int, date]
# The target/posted leg map: {ledger_account_id: (signed amount, posting_kind_id)}.
LegMap = dict[int, tuple[Decimal, int]]


def account_owner_id(account_id: int) -> int | None:
    """Return an account's owner (``auth.users.id``), or ``None`` if absent.

    The shared owner resolver for the correction-posting packages: the loan
    anchor reconcile and all-scenarios sync need the loan account's owner
    (its per-loan equity account, its pay periods, its baseline scenario),
    and the account anchor reconcile needs the non-loan account's owner (its
    ``anchor_equity`` twin and the entry header) -- the identical query, an
    amortizing loan being an :class:`~app.models.account.Account` row like
    any other.  ``None`` only for a missing / deleted account, which every
    caller treats as "nothing to reconcile".

    Args:
        account_id: The account whose owner to resolve.

    Returns:
        The owner's user id, or ``None`` when the account row is absent.
    """
    return (
        db.session.query(Account.user_id)
        .filter(Account.id == account_id)
        .scalar()
    )


def filing_calendar_for(account_id: int) -> tuple[int, PayCalendar] | None:
    """Return an account's owner and the calendar its corrections file against.

    **The one door both anchor reconciles take to the pay calendar** (plan step
    C2-d).  Each of them keys every correction's entry on the pay period it
    files under, and both had the same four lines to get there; one call
    replaces both.

    **It returns the owner WITH the calendar, and that pairing is the whole
    job.**  The loader it replaced, ``loan_ledger.owner_pay_periods``, took an
    ``account_id`` and JOINED through it, so a loan could not be resolved
    against another user's schedule even by a caller's mistake.  A first cut of
    this function took ``(account_id, owner_id)`` and correlated them nowhere,
    which downgraded a structural guarantee on a money-write path to a
    convention -- and said in its own docstring that it had not.  Resolving the
    owner HERE and handing both back restores it at no cost: the caller needed
    :func:`account_owner_id` anyway, so this is the same one query, moved to
    where the pairing is made.

    **It refuses NOTHING, and that is deliberate** (developer ruling,
    2026-08-10).  A first cut raised a :class:`PostingError` here when the
    owner had no usable period, which read as a guard and was not one: every
    caller returns early on an empty corrections list, so
    :meth:`~app.services.pay_calendar.PayCalendar.filing_period` is always
    reached at least once and already refuses that exact state -- with a
    message naming the user, the day, and how many of the calendar's paydays
    are materialised.  Two statements of one predicate, and they had ALREADY
    drifted when an adversarial review found them: this one tested "no periods
    at all" while both writers' docstrings promised "no MATERIALISED period",
    which differ for the unsaved-payday calendar plan step C3's writer builds.
    Aligning them by hand would have been the band-aid; deleting one leaves
    the rule stated once, where it is enforced.  What is lost is the ACCOUNT id
    in the message, against finding **N-192**'s requirement only that the
    failure be LOUD -- which it is, unhandled, from a nearer statement of why.

    The whole payday set, never a window:
    :func:`app.services.pay_calendar.calendar_for` has no window argument, and
    ledger row **P14** is why -- a partial payday set renumbers every
    ``period_index`` from zero and gives its last period a cadence-projected
    end.  Filing reads neither, but the calendar it is asked of must still be
    the owner's whole one, because the next question asked of it may.

    Args:
        account_id: The account whose corrections are being reconciled.

    Returns:
        ``(owner_id, calendar)``, or ``None`` when the account row is absent --
        which every caller treats as "nothing to reconcile", the same
        disposition :func:`account_owner_id` already had there.

    Raises:
        PayCalendarError: The owner's paydays cannot define a calendar.  Not
            reachable from any live write door -- every one bounds
            ``cadence_days`` to 1..365 and ``uq_pay_periods_user_start``
            forbids a repeated payday -- but it is declared because this door
            put a DERIVATION in front of a money write where a plain query
            stood, and the composition is what a caller has to reason about.
            *This clause used to carry a third hazard and plan step C4-b-2
            deleted it.*  The cadence was INFERRED from the last period's
            stored length for an owner with no ``budget.pay_schedule`` row
            (``pay_schedule_service.resolve_cadence``), which is plan finding
            **P8**'s state, and that inference was called load-bearing here on
            the ground that it "is exactly what a freshly-registered owner
            has".  Plan step X-ad-a falsified the ground -- registration writes
            the schedule row beside the paydays -- and C4-b-2 removed the state
            itself: ``fk_pay_periods_schedule`` makes an owner with paydays and
            no cadence row unstorable, so the cadence this door reads is the
            stored column and nothing derived.  The composition is still what
            a caller has to reason about; what it composes is now one read.
    """
    owner_id = account_owner_id(account_id)
    if owner_id is None:
        return None
    return owner_id, calendar_for(owner_id)


def summed_posting_legs(extra_columns: list, filters: list):
    """Return a grouped query summing each ledger's posted amount and kind.

    The shared shape of the "what is already posted" readers -- via
    :func:`posted_correction_legs`, the loan and account correction readers
    (the loan payment split's own reader was a third consumer until plan step
    ``balance:X-bi-6-3`` folded the split into the correction reconcile) --
    so no consumer re-spells the ``SUM(amount) GROUP BY ledger, kind`` join (a
    ``duplicate-code`` finding).  Sums ``Posting.amount`` per ledger account
    (carrying its single posting kind, since a correction ledger always holds
    one kind), joined to the owning :class:`JournalEntry`.

    Args:
        extra_columns: Extra group-key columns prepended to the SELECT and
            GROUP BY (``source_kind_id`` + ``pay_period_id`` + ``entry_date``
            for the correction readers, a correction having no source FK to
            select on).
        filters: The entry-scoping filter expressions.

    Returns:
        A SQLAlchemy ``Query``, NOT executed.  Each row unpacks as
        ``(*extra_columns, ledger_account_id, net_amount, posting_kind_id)``.
    """
    return (
        db.session.query(
            *extra_columns,
            Posting.ledger_account_id,
            db.func.sum(Posting.amount),
            Posting.posting_kind_id,
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(*filters)
        .group_by(
            *extra_columns,
            Posting.ledger_account_id,
            Posting.posting_kind_id,
        )
    )


def account_chart_row_ids(account_id: int):
    """Return the ids of every chart row belonging to ONE real account.

    An account's own rows are the ones reachable from it through either link
    ``budget.ledger_accounts`` carries: ``account_id`` (its ``linked`` row and,
    since ruling **R-FO**, its per-account COUNTER rows) and
    ``loan_account_id`` (a loan's per-loan interest / escrow / refund /
    opening rows).  A category, fallback or orphan row belongs to no account
    and is reachable through neither.

    Args:
        account_id: The real ``budget.accounts`` id whose chart rows to name.

    Returns:
        A SQLAlchemy ``Query`` yielding ``(ledger_account_id,)``, NOT executed
        -- callers use it as an ``IN`` subquery.
    """
    return (
        db.session.query(LedgerAccount.id)
        .filter(db.or_(
            LedgerAccount.account_id == account_id,
            LedgerAccount.loan_account_id == account_id,
        ))
    )


def posted_correction_legs(
    account_id: int, scenario_id: int, source_kind_ids: list[int],
) -> dict[CorrectionKey, LegMap]:
    """Return the posted correction legs for ONE account, by key.

    Sums ``account_postings.amount`` over every journal entry in
    *scenario_id* whose source kind is one of *source_kind_ids* and that
    touches ANY of the account's own chart rows
    (:func:`account_chart_row_ids`), grouped by ``(source_kind_id,
    pay_period_id, entry_date, ledger_account_id, posting_kind_id)``.  This is
    the "already posted" side the correction reconciles compare their targets
    against, read straight from the ledger so a reversal negates exactly what
    was posted, reuses the kind it was posted with, and lands in the PERIOD it
    was posted in.

    **The scope is the ACCOUNT's rows, not its LINKED row, and that difference
    is load-bearing since ruling R-FO** (plan step X-f3d).  This read was
    scoped to entries carrying a leg on the linked ledger, on the stated ground
    that "every anchor correction carries a linked leg" -- true while an
    account had exactly ONE counter row, because the target's linked leg and
    its counter leg are negatives and their deltas vanish together.  R-FO gives
    an account a SECOND counter row, and re-pointing a correction from one to
    the other moves no money on the linked ledger at all: the delta entry it
    emits carries a reversal leg and a fresh leg and NO linked leg.  Scoped to
    the linked row, that entry is invisible to this reader, so the next
    reconcile computes the same delta again -- measured on a production clone,
    a second backfill pass re-emitted all 14 re-point deltas, and every pass
    after it would have too.  Scoping to the account's own rows sees the whole
    correction whatever it touches, and the reconcile is idempotent again.

    **Grouping by ``pay_period_id`` is the R2 attribution rule made
    structural** (plan step X-ai-r; see the :data:`CorrectionKey` comment for
    the measurement).  Without it a key spanning two periods reads back as
    one blob, and the reversal of postings in one period is filed against the
    other.  A journal entry carries exactly one source kind, one period and
    one date, so every entry's legs land in exactly one key and each key's
    posted side sums to zero -- which is what makes each key's delta a
    balanced entry on its own.

    Args:
        account_id: The real account whose corrections to sum -- the loan for
            the loan pair, the non-loan account for the account pair.
        scenario_id: The budget scenario to scope to.
        source_kind_ids: The correction source-kind ids to select (the loan
            or account opening / true-up pair).

    Returns:
        ``{(source_kind_id, pay_period_id, entry_date): {ledger_account_id:
        (net, kind_id)}}``; empty when no correction is posted yet.
    """
    entry_ids = (
        db.session.query(Posting.journal_entry_id)
        .filter(Posting.ledger_account_id.in_(
            account_chart_row_ids(account_id),
        ))
    )
    rows = summed_posting_legs(
        [
            JournalEntry.source_kind_id,
            JournalEntry.pay_period_id,
            JournalEntry.entry_date,
        ],
        [
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.source_kind_id.in_(source_kind_ids),
            JournalEntry.id.in_(entry_ids),
        ],
    ).all()
    posted: dict[CorrectionKey, LegMap] = {}
    for source_kind_id, period_id, entry_date, ledger_id, net, kind_id in rows:
        posted.setdefault(
            (source_kind_id, period_id, entry_date), {},
        )[ledger_id] = (net, kind_id)
    return posted


def delta_legs(target: LegMap, posted: LegMap) -> list[_PostingLeg]:
    """Return the balanced delta legs bringing *posted* to *target*.

    For each ledger account touched by either side, the leg amount is
    ``target_amount - posted_amount``; a zero delta is dropped (no leg written).
    Because *target* sums to zero by construction and every *posted* entry is
    balanced, the non-zero deltas also sum to zero -- so the result is either
    empty (already at target, an idempotent no-op) or has ``>= 2`` legs that
    :func:`app.services.posting_service._emit_balanced_entry` accepts.

    The kind labels each leg's economic nature: a leg present on the target side
    takes its target kind; a leg present ONLY on the posted side (a component the
    new target dropped to zero) is reversed with the kind it was posted under, so
    a reversal never loses the kind it is undoing.

    Args:
        target: ``{ledger_account_id: (amount, posting_kind_id)}`` the ledger
            should net to (empty to reverse everything to zero).
        posted: ``{ledger_account_id: (net_amount, posting_kind_id)}`` currently
            posted (empty when nothing is posted yet).

    Returns:
        The balanced :class:`~app.services.posting_service._PostingLeg` deltas
        (empty when *posted* already equals *target*).
    """
    legs: list[_PostingLeg] = []
    for ledger_id in sorted(set(target) | set(posted)):
        target_amount, target_kind = target.get(ledger_id, (_ZERO_MONEY, None))
        posted_amount, posted_kind = posted.get(ledger_id, (_ZERO_MONEY, None))
        delta = target_amount - posted_amount
        if delta == 0:
            continue
        kind_id = target_kind if target_kind is not None else posted_kind
        legs.append(_PostingLeg(ledger_id, delta, kind_id))
    return legs


def merge_target_legs(bucket: LegMap, legs: LegMap) -> None:
    """Sum a correction's legs into a per-key target *bucket*, in place.

    The same-key merge both anchor reconciles apply when two anchors share
    one reconcile key -- same source kind, same pay period AND same civil day.
    Each ledger's amounts add, so the merged target expresses the anchors'
    combined jump (landing on the later anchor's value, since each
    correction's delta already accounts for the prior one).  The posting kind
    is shared within a key by construction (one key holds one source kind,
    whose legs all carry that kind), so the incoming kind simply wins.

    **Adding the period to the key narrowed NOTHING here, and a draft of this
    docstring claimed it did** ("two same-day assertions filed against
    different periods now key apart").  That described R-DZ's target key, which
    ruling R-EA replaced: the period is DERIVED from the assertion's day, so
    two assertions sharing a day share a period BY CONSTRUCTION and can never
    key apart.  The correction is kept visible because the claim survived the
    ruling that falsified it, in the shared primitive both halves read.

    It survives only because an assertion has no source FK for the ledger to
    key on, so two assertions genuinely indistinguishable in the key merge
    into one entry.  Giving a correction that identity is what deletes this
    (plan step X-ai-s) -- and it is the ONLY thing that can, since no part of
    the current key can tell two same-day assertions apart.

    Args:
        bucket: The key's accumulated ``{ledger_account_id: (amount,
            posting_kind_id)}`` target, mutated in place.
        legs: One correction's balanced legs to add (may be empty for a
            correction that books nothing).
    """
    for ledger_id, (amount, kind_id) in legs.items():
        prev_amount, _prev_kind = bucket.get(ledger_id, (_ZERO_MONEY, kind_id))
        bucket[ledger_id] = (prev_amount + amount, kind_id)


def _correction_description(source_kind_id: int, entry_date: date) -> str:
    """Return the human label for a correction entry (display only).

    ``"<Loan|Account> <opening balance|balance true-up> as of <date>"`` for an
    anchor correction and ``"Loan payment split as of <date>"`` for the loan
    payment split (plan step ``balance:X-bi-6-3``, ruling **R-BAL102**; it
    named the shadow while the split was keyed by one), resolved from the
    entry's source kind and truncated to the description column width.  Never
    read for logic.

    Args:
        source_kind_id: The entry's journal source kind id (one of the five
            correction kinds).
        entry_date: The correction's civil date.

    Returns:
        The truncated description string.

    Raises:
        PostingError: If *source_kind_id* is not one of the five correction
            source kinds -- a caller emitting a correction entry under a
            source that links a row is a broken invariant, so it fails loudly
            rather than fabricating a label.
    """
    labels: dict[int, str] = {
        ref_cache.posting_source_id(PostingSourceEnum.LOAN_OPENING):
            "Loan opening balance",
        ref_cache.posting_source_id(PostingSourceEnum.LOAN_TRUEUP):
            "Loan balance true-up",
        ref_cache.posting_source_id(PostingSourceEnum.LOAN_PAYMENT):
            "Loan payment split",
        ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING):
            "Account opening balance",
        ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_TRUEUP):
            "Account balance true-up",
    }
    label = labels.get(source_kind_id)
    if label is None:
        raise PostingError(
            f"Source kind {source_kind_id} is not a correction source; no "
            f"correction entry may be emitted under it."
        )
    return (
        f"{label} as of {entry_date.isoformat()}"
    )[:_MAX_DESCRIPTION_LENGTH]


def emit_correction_entry(
    owner_id: int,
    scenario_id: int,
    key: CorrectionKey,
    legs: list,
) -> JournalEntry:
    """Emit one balanced correction delta entry (opening, true-up or split).

    The one definition of a sourceless correction's journal header, shared by
    the loan and account anchor reconciles and the loan payment split (plan
    step ``balance:X-bi-6-3``, ruling **R-BAL102**): ``transaction_entry_id``,
    the one row link an entry carries since plan#1845, NULL (a correction is
    a derivation and links no row; ``source_kind_id`` disambiguates it), and
    described from its source kind (:func:`_correction_description`).  Writes
    the balanced *legs* through the shared balanced-write path.  Flushes; does
    not commit.

    **Every column that identifies the entry comes off the KEY**, including
    the NOT NULL ``pay_period_id``.  It was a separate parameter until plan
    step X-ai-r put the period in the key; passing it alongside made it
    possible for the period an entry is FILED under to differ from the period
    its delta was COMPUTED against, which is finding N-161's defect stated as
    a signature.  With one source there is nothing left to disagree.

    Args:
        owner_id: The account owner's user id.
        scenario_id: The budget scenario the correction lives in.
        key: The ``(source_kind_id, pay_period_id, entry_date)`` the delta
            reconciles -- and, by the paragraph above, the entry's own
            source kind, period and date.
        legs: The balanced delta legs from :func:`delta_legs`.

    Returns:
        The persisted delta :class:`~app.models.journal_entry.JournalEntry`.

    Raises:
        PostingError: If *key* names a non-correction source kind, or the
            legs do not balance (via the shared balanced-write path).
    """
    source_kind_id, pay_period_id, entry_date = key
    entry = JournalEntry(
        user_id=owner_id,
        scenario_id=scenario_id,
        pay_period_id=pay_period_id,
        entry_date=entry_date,
        source_kind_id=source_kind_id,
        transaction_entry_id=None,
        description=_correction_description(source_kind_id, entry_date),
    )
    _emit_balanced_entry(entry, legs)
    logger.info(
        "Posted correction (source %d as of %s, period %d) as journal "
        "entry %d",
        source_kind_id, entry_date, pay_period_id, entry.id,
    )
    return entry


def emit_correction_deltas(
    owner_id: int,
    scenario_id: int,
    *,
    target: dict[CorrectionKey, LegMap],
    posted: dict[CorrectionKey, LegMap],
) -> None:
    """Emit one balanced delta entry per correction key that differs.

    The reconcile LOOP both anchor packages share -- and, since plan step
    ``balance:X-bi-6-3`` (ruling **R-BAL102**), the loan payment split, which
    the loan package reconciles beside its anchors in ONE call: over the
    UNION of the target and posted keys in sorted order, take each key's
    ``target - posted`` deltas (:func:`delta_legs`) and, when any are
    non-zero, emit them as one balanced entry (
    :func:`emit_correction_entry`).  That covers every lifecycle in
    one shape -- a key present only in *target* posts fresh, a key present
    only in *posted* reverses to zero, and a key in both adjusts by the
    difference.  Idempotent: a re-run at the same state computes every delta
    as zero and writes nothing.

    **It is here rather than in each package because after plan step X-ai-r
    the two loops are identical.**  The loan reconcile resolved its entry's
    period inside this loop (from the anchor date) while the account
    reconcile carried a parallel ``{key: period}`` map; putting the period in
    the key left both spelling the same five lines, which is a
    ``duplicate-code`` finding waiting on the next edit to either.  The
    difference that remains between the two packages -- WHERE an assertion's
    period comes from -- now lives entirely in how each builds its target
    keys, which is where a genuine difference belongs.

    Each key's deltas balance by construction, so no key can produce a
    single-legged entry: a target sums to zero (every correction contributes
    a leg and its negative) and the posted side sums to zero per key (a
    journal entry carries one source kind, one period and one date, so all
    of its legs fall in the same key).  Flushes but does not commit -- the
    caller owns the transaction.

    **``target`` and ``posted`` are KEYWORD-ONLY, deliberately.**  They are the
    same type, adjacent, and swapping them yields ``posted - target`` -- a
    perfectly balanced, sign-INVERTED entry that the balanced-write path
    accepts, no checker sees, and no trial balance catches.  Naming them at the
    call site is what makes that inversion unwritable rather than merely
    unlikely.

    Args:
        owner_id: The account owner's user id.
        scenario_id: The budget scenario to reconcile within.
        target: ``{key: {ledger_account_id: (amount, kind_id)}}`` the ledger
            should net to per key (a key with an empty leg map reverses that
            key to zero).
        posted: ``{key: {ledger_account_id: (net, kind_id)}}`` currently
            posted, from :func:`posted_correction_legs`.

    Raises:
        PostingError: If a key names a non-correction source kind, or its
            legs do not balance (both via
            :func:`emit_correction_entry`).
    """
    for key in sorted(set(target) | set(posted)):
        legs = delta_legs(target.get(key, {}), posted.get(key, {}))
        if not legs:
            continue
        emit_correction_entry(owner_id, scenario_id, key, legs)
