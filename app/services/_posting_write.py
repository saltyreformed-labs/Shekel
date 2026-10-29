"""Shared balanced-write primitives for the posting ledger (leaf module).

The pieces every ledger WRITER composes -- the leg record
(:class:`_PostingLeg`), the single balanced-write path
(:func:`_emit_balanced_entry`), the description width, and since plan step
``balance:X-bi-3b`` the two derivations a source's TYPE decides
(:func:`ledger_class_of`, :func:`posting_kind_of`) -- in a LEAF module
below every writer, so the correction packages can import them without
importing :mod:`app.services.posting_service` itself.  It also held the entry's
civil-date rule as ``_utc_civil_date`` until ruling R-DH (2026-07-31) moved that
day to the USER's timezone.  The day is a stored column since plan step X-f1
(ruling R-EC), and a ROW's is read through
:func:`app.utils.balance_predicates.settled_day`.  *This said the derivation
lived once there, "which every writer and both folds share", until leaf
balance:X-bi-6-4d-1: the cash fold reads its movements' stored day directly,
and the loan fold reads a leg's off the leg since that leaf.*

Why a leaf and not the writer module: Build-Order Step 5's effect-time
self-heal makes ``posting_service`` call into
:mod:`app.services.account_posting_service` at its sync tails (a
function-local import; the account package is the higher layer there),
while the account package needs exactly these primitives.  Importing them
FROM ``posting_service`` would close an import cycle
(``posting_service -> account_posting_service -> posting_service``);
holding them in a leaf breaks it structurally -- the same resolution the
accounts blueprint used (``app/routes/accounts/_bp.py``).
``posting_service`` remains the ledger's one PUBLIC surface.  The modules
that import this leaf directly are the writer and its own private leaf
(:mod:`app.services._posting_purchases`) and the correction core
:mod:`app.services._posting_reconcile`, which the account and loan posting
packages build on.

Flask-isolated and commit-free like its consumers: flushes so the caller
sees assigned ids; the caller owns the transaction boundary.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountClassEnum, PostingKindEnum, PostingSourceEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.services.posting_reads import PostingError
from app.services.transfer_legs import TransferLeg

logger = logging.getLogger(__name__)

# A double-entry journal entry has at least two legs (one debit, one credit).
# Mirrors the ``COUNT(*) >= 2`` half of the deferred balanced-journal trigger
# (``app.posting_infrastructure``); named so the service-side backstop and the
# DB backstop read as the same rule.
_MIN_POSTING_LEGS = 2

# ``budget.journal_entries.description`` is ``VARCHAR(200)``.  The human label
# is truncated to fit, mirroring the historical backfill's ``LEFT(..., 200)``
# so the go-forward and backfilled entries carry identically-shaped
# descriptions.
_MAX_DESCRIPTION_LENGTH = 200

def is_transfer_leg(parent) -> bool:
    """Return whether *parent* is one leg of a transfer.

    The ONE spelling of the writer's shape dispatch (plan step
    ``balance:X-bi-6-3``, ruling **R-BAL101**): a transfer's movement books
    against the owner's transit account under the ``transfer_movement``
    source with the ``transfer`` leg kind, and the sites that decide that --
    the counter leg, the source kind, the leg kind, whether it posts -- ask
    this rather than each testing the shape for themselves (the leaf's
    adversarial review counted three spellings).

    **It asks what the parent IS, not a shadow's column** (leaf
    ``X-bi-6-4a``, ruling **R-BAL106**): every door hands the writer the
    parent :func:`app.services.transfer_legs.movement_parent` resolved, a
    plan row or a :class:`~app.services.transfer_legs.TransferLeg`, so no
    door of this writer reaches a movement's parent through
    ``transaction.transfer_id``.  One reader of the column remained beside
    it: the deploy resync's row selector kept shadow rows out of its ROW arm
    (the fold's ``_movements_of`` twin) until plan step ``X-bi-6-5`` deleted
    that exclusion and then the column.
    The loan ledger's stale-movement probe (``loan_posting_service._sync``)
    was a second until leaf ``X-bi-6-4b``, which gave it
    :func:`~app.services.transfer_legs.movement_parent`, and spelled that
    function's shadow chain as its own loader options until leaf
    ``X-bi-6-4d-1`` (ledger row **BAL-579**).  Through ``X-bi-6-3`` this was
    ``txn.transfer_id is not None`` over the shadow row itself.

    Args:
        parent: A movement's parent: a plan row or a transfer leg.

    Returns:
        ``True`` for a transfer leg.
    """
    return isinstance(parent, TransferLeg)


def ledger_class_of(txn) -> LedgerAccountClassEnum:
    """Return the chart class *txn*'s counter leg books to, by its TYPE.

    An income row's counter account is an INCOME-class category account and
    an expense row's an EXPENSE-class one -- the one mapping the movement
    writer (``_posting_purchases._purchase_target``) reads, shared with the
    row's own writer from plan step ``balance:X-bi-3b`` (when a movement's
    class became its PARENT's, ruling **R-BAL35**) until ``balance:X-bi-4a``
    deleted that writer, so the second would otherwise have spelled the
    mapping again.  ``transaction_type_id`` is immutable, so the class is
    stable across the row's life.

    Args:
        txn: The transaction, income or expense.

    Returns:
        :attr:`LedgerAccountClassEnum.INCOME` or ``.EXPENSE``.
    """
    return (
        LedgerAccountClassEnum.INCOME if txn.is_income
        else LedgerAccountClassEnum.EXPENSE
    )


def posting_kind_of(parent) -> int:
    """Return the ``ref.posting_kinds`` id every leg of *parent*'s money carries.

    Both legs of an ordinary-transaction entry carry the same kind, by the
    transaction type; no Step-3 reader differentiates per-leg kind.  A
    MOVEMENT's legs carry its PARENT's kind (plan step ``balance:X-bi-3b``): a
    purchase against an envelope is an ``expense`` posting and a paycheck's
    covering movement an ``income`` one, for the one reason
    :func:`ledger_class_of` gives -- and a transfer leg's covering movement
    a ``transfer`` one (plan step ``balance:X-bi-6-3``, ruling **R-BAL101**),
    because a transfer between the owner's own accounts is neither income nor
    expense, which is why its counter leg is the transit account and not a
    category (ruling **R-BAL45**).  The kind the one-entry transfer shape
    carried is the kind its two per-movement entries carry.

    Args:
        parent: An income or expense transaction, or a transfer leg
            (:func:`is_transfer_leg`).

    Returns:
        The stored id of :attr:`PostingKindEnum.TRANSFER` for a leg's
        money, else of :attr:`PostingKindEnum.INCOME` or ``.EXPENSE``.
    """
    if is_transfer_leg(parent):
        return ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)
    return ref_cache.posting_kind_id(
        PostingKindEnum.INCOME if parent.is_income else PostingKindEnum.EXPENSE
    )


@dataclass(frozen=True)
class _PostingLeg:
    """One signed leg to write into a balanced journal entry.

    The unit the shared balanced-write path (:func:`_emit_balanced_entry`)
    consumes, so the transfer lifecycle, the cash lifecycle, and the loan /
    account correction packages describe their legs the same way.
    ``amount`` is debit-positive / credit-negative; see the
    :mod:`app.services.posting_service` module docstring for the sign
    convention.

    Attributes:
        ledger_account_id: ``budget.ledger_accounts.id`` the leg lands in.
        amount: The signed leg amount (``Decimal``); non-zero (a zero leg is
            refused by ``ck_account_postings_amount_nonzero``).
        posting_kind_id: ``ref.posting_kinds.id`` for the leg's economic
            nature.
    """

    ledger_account_id: int
    amount: Decimal
    posting_kind_id: int


def _emit_balanced_entry(
    entry: JournalEntry, legs: "list[_PostingLeg]"
) -> JournalEntry:
    """Persist a journal entry and its legs, enforcing the balanced invariant.

    The single balanced-write path every posting source shares (Step 2's
    transfers; Step 3's cash; the Step 4 / Step 5 correction packages).
    Validates the two cross-row invariants the deferred
    ``ck_account_postings_balanced`` trigger enforces -- at least two legs,
    and legs summing to zero -- BEFORE the write, so an unbalanced entry
    fails loudly at the call site with a clear message instead of as an
    opaque deferred error at COMMIT.  The service is the first backstop;
    the DB trigger is the second (the house "service + DB backstop"
    pattern).

    Adds the entry with its legs via the ``postings`` relationship cascade
    (one flush assigns the entry id and inserts the legs with their FK) and
    flushes so the caller sees assigned ids.  Does NOT commit.

    Args:
        entry: The unsaved :class:`~app.models.journal_entry.JournalEntry`
            header, with every column already set by the caller.
        legs: The :class:`_PostingLeg` list to attach; balanced by
            construction for every current source.

    Returns:
        The persisted *entry* (flushed, with ``id`` and ``postings`` set).

    Raises:
        PostingError: If *legs* has fewer than two entries or does not sum
            to zero.
    """
    if len(legs) < _MIN_POSTING_LEGS:
        raise PostingError(
            f"A journal entry needs at least {_MIN_POSTING_LEGS} legs; "
            f"got {len(legs)}."
        )
    total = sum((leg.amount for leg in legs), Decimal("0"))
    if total != 0:
        raise PostingError(
            f"Journal entry legs must sum to 0 (debit-positive double "
            f"entry); got {total}."
        )

    db.session.add(entry)
    for leg in legs:
        entry.postings.append(
            Posting(
                ledger_account_id=leg.ledger_account_id,
                amount=leg.amount,
                posting_kind_id=leg.posting_kind_id,
            )
        )
    db.session.flush()
    return entry


def posted_by_period(source_filter) -> "dict[tuple[int, date], dict[int, Decimal]]":
    """Return a source's posted legs grouped by (pay period, entry date).

    The "already posted" side of the source reconcile: sums
    ``account_postings.amount`` across every journal entry matching
    *source_filter* (a movement's ``transaction_entry_id`` under its source
    kind), grouped by the entry's ``pay_period_id``, its ``entry_date``, and
    the leg's ledger account.  Reading the posted side back per PERIOD is what lets a reversal
    land in the period of the postings it reverses (the 2026-07-02 adversarial
    review's R2 attribution rule): a source row whose ``pay_period_id`` later
    moved (the revert-and-move PATCH) reverses into its ORIGINAL period, so the
    net-zero pair never straddles periods and a later period truncate cannot
    strand half of it.

    **Per ENTRY DATE as well as period since plan step E1a (finding N-13).**
    ``entry_date`` is the day the recorded event happened (step C2's one
    clock), and the balance fold counts each event from that day -- so a
    correct NET in the right period at the WRONG date is still a wrong ledger
    (the per-visible-date checked-projection assert catches exactly that).
    Keying the reconcile by ``(period, entry_date)`` makes a stale-dated
    posting a first-class delta: it is reversed AT ITS OWN DATE and re-posted
    at the source's current settle date, instead of surviving because its
    period's amount happened to match.  This also retires the old
    latest-``entry_date`` heuristic a reversal-only entry inherited -- each
    key carries the exact date its delta nets against.

    Args:
        source_filter: The SQLAlchemy filter expression selecting the source's
            journal entries: its concrete link (``transaction_entry_id``, the
            one an entry can carry since plan#1845) AND its
            ``source_kind_id``.  The link alone is not a source since plan
            step ``balance:X-bi-6-3`` (ruling **R-BAL101**'s clause): on the
            base the loan payment's split correction shared the loan-side
            shadow's ``transaction_id`` with the transaction source, so a
            filter by link alone would have summed the split into that
            source's posted side and the delta would have reversed it, and
            a guard that skipped shadows was the only fence keeping that
            read unreachable.  Every writer's filter names its kind
            (:func:`emit_typed_source_deltas` adds it; the correction
            packages state it themselves), and the split links no row at all
            since ruling **R-BAL102**.

    Returns:
        ``{(pay_period_id, entry_date): {ledger_account_id: net Decimal}}``
        over the source's posted legs (empty when nothing is posted yet; a
        fully-reversed account appears with a ``Decimal("0")`` net, its delta
        then dropping out).
    """
    rows = (
        db.session.query(
            JournalEntry.pay_period_id,
            JournalEntry.entry_date,
            Posting.ledger_account_id,
            db.func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(source_filter)
        .group_by(
            JournalEntry.pay_period_id,
            JournalEntry.entry_date,
            Posting.ledger_account_id,
        )
        .all()
    )
    posted: "dict[tuple[int, date], dict[int, Decimal]]" = {}
    for period_id, entry_date, ledger_id, net in rows:
        posted.setdefault((period_id, entry_date), {})[ledger_id] = net
    return posted


def reconcile_periods(
    targets: "dict[tuple[int, date], dict[int, Decimal]]",
    posted: "dict[tuple[int, date], dict[int, Decimal]]",
    kind_id: int,
) -> "dict[tuple[int, date], list[_PostingLeg]]":
    """Return the balanced delta legs per (period, entry date) bringing *posted* to *targets*.

    The keyed core of the reconcile both sync functions share: for every
    ``(pay_period_id, entry_date)`` either side touches, the delta per ledger
    account is ``target - posted``; zero deltas drop.  Within one key the
    non-zero deltas always sum to zero -- a target sums to zero by
    construction, and the posted side sums to zero because every prior entry
    balanced and an entry lives in exactly one (period, date) -- so each key's
    legs form one balanced entry (never a single leg).

    A stale-DATED posting (finding N-13: a settled ``settled_on`` edit moves the
    event's day but no amount) therefore reconciles as two keys: its old date
    reverses to zero and the target's date posts fresh -- converging in ONE
    pass, so a repeat sync writes nothing.

    Args:
        targets: ``{(pay_period_id, entry_date): {ledger_account_id: amount}}``
            the ledger should net to (at most the source's current period at
            its settle date; empty to reverse everything to zero).
        posted: The :func:`posted_by_period` net map.
        kind_id: The posting kind stamped on every delta leg (both legs of a
            transfer / transaction entry carry the source's one kind).

    Returns:
        ``{(pay_period_id, entry_date): [_PostingLeg, ...]}`` for every key
        with a non-zero delta; empty when the ledger is already at target.
    """
    legs_by_key: "dict[tuple[int, date], list[_PostingLeg]]" = {}
    for key in sorted(set(targets) | set(posted)):
        key_target = targets.get(key, {})
        key_posted = posted.get(key, {})
        legs = [
            _PostingLeg(ledger_id, delta, kind_id)
            for ledger_id in sorted(set(key_target) | set(key_posted))
            if (
                delta := key_target.get(ledger_id, Decimal("0"))
                - key_posted.get(ledger_id, Decimal("0"))
            ) != 0
        ]
        if legs:
            legs_by_key[key] = legs
    return legs_by_key


def emit_source_deltas(
    *,
    targets: "dict[tuple[int, date], dict[int, Decimal]]",
    source_filter,
    kind_id: int,
    build_entry: "Callable[[int, date], JournalEntry]",
    log_label: str,
) -> "list[JournalEntry]":
    """Reconcile ONE source's posted legs to *targets*, emitting the deltas.

    **The reconcile-to-target algorithm itself, stated once.**  Read back what
    the source has already posted per ``(pay period, entry date)``, take the
    per-ledger-account difference against what it should hold, and emit one
    balanced entry per key that differs -- writing nothing at all when it is
    already at target.  Every writer in the ledger is that sentence over a
    different target: the transfer sync, the transaction sync and the purchase
    sync (plan step X-f3b, ruling **R-FM**), which is when three copies of the
    five-line body became a measured ``duplicate-code`` finding.

    What a caller still owns is the only thing that differs -- WHAT the target
    is.  Everything downstream of that is here.

    Args:
        targets: ``{(pay_period_id, entry_date): {ledger_account_id: amount}}``
            the ledger should net to; EMPTY to reverse the source to zero.
        source_filter: The SQLAlchemy filter selecting this source's journal
            entries (see :func:`posted_by_period`).
        kind_id: The posting kind stamped on every emitted leg.
        build_entry: The entry-header closure
            (:func:`source_entry_builder`).
        log_label: Names the source in the per-entry INFO line.

    Returns:
        The emitted delta entries, or ``[]`` when the ledger is already at
        target (the idempotent no-op every caller's contract promises).
    """
    legs_by_key = reconcile_periods(
        targets, posted_by_period(source_filter), kind_id,
    )
    if not legs_by_key:
        return []
    return emit_keyed_delta_entries(legs_by_key, build_entry, log_label)


def source_entry_builder(
    *,
    user_id: int,
    scenario_id: int,
    source_kind_id: int,
    description: str,
    transaction_entry_id: int,
) -> "Callable[[int, date], JournalEntry]":
    """Return the ``build_entry`` closure for ONE source's delta entries.

    The ONE statement of what a source-linked journal entry header IS.  It
    served three writers -- the transfer sync, the transaction sync and the
    purchase sync (plan step X-f3b, ruling **R-FM**), which differed in the
    source KIND, which concrete FK carried the linkage, and the human
    description -- until plan step ``balance:X-bi-6-5``'s second leaf (card
    plan#1845) deleted the first two with their links, so the movement
    writer is the one source-linked writer and its link the one FK.

    **The per-key fields are this closure's and the rest are the caller's**:
    ``pay_period_id`` and ``entry_date`` ARE the reconcile key (the R2
    attribution rule, per-date since plan step E1a), so they arrive per call and
    nothing else does.

    Args:
        user_id: The owning user (tenancy).
        scenario_id: The scenario the source lives in.
        source_kind_id: ``ref.posting_sources.id`` for this source's kind.
        description: The human label, already truncated by the caller to
            :data:`_MAX_DESCRIPTION_LENGTH` -- the column's own width, applied
            where the label is composed rather than here, so a caller cannot be
            handed a silently-clipped string it thinks it chose.
        transaction_entry_id: The movement the entry links (its one
            concrete source FK).

    Returns:
        A callable taking ``(pay_period_id, entry_date)`` and returning the
        UNSAVED :class:`~app.models.journal_entry.JournalEntry` header.
    """
    def build(period_id: int, entry_date: date) -> JournalEntry:
        """Build one delta entry header for its ``(period, date)`` key."""
        return JournalEntry(
            user_id=user_id,
            scenario_id=scenario_id,
            pay_period_id=period_id,
            entry_date=entry_date,
            source_kind_id=source_kind_id,
            description=description,
            transaction_entry_id=transaction_entry_id,
        )

    return build


def emit_typed_source_deltas(
    parent,
    movement,
    *,
    targets: "dict[tuple[int, date], dict[int, Decimal]]",
    source: PostingSourceEnum,
    log_label: str,
) -> "list[JournalEntry]":
    """Reconcile ONE movement whose money is TYPED by *parent* to *targets*.

    :func:`emit_source_deltas` for a source a parent types -- each MOVEMENT
    (``_posting_purchases.emit_purchase_deltas``), and a row's OWN cash leg
    from plan step ``balance:X-bi-3b``, when a movement's legs took its
    parent's kind (:func:`posting_kind_of`) and the two emits came to differ
    in nothing but WHICH source they named, until plan step
    ``balance:X-bi-6-5``'s second leaf (card plan#1845) deleted the row's
    with its ``transaction_id`` link.  Everything the parent decides --
    the kind, the owner (``parent.user_id``: the one home ruling
    ``pay_calendar:C13-b`` gave a row's owner, a transfer's own for a leg),
    the scenario -- is read off *parent* here; everything the source decides
    arrives by argument.

    Args:
        parent: What the legs are typed by -- their kind, owner and
            scenario: a transaction, or a transfer LEG (leaf ``X-bi-6-4a``;
            its movement's legs carry the ``transfer`` kind,
            :func:`posting_kind_of`).
        movement: The movement (a ``transaction_entries`` row) whose legs
            these are.  Its id is both the entry header's link and, with
            *source*, the filter that reads the source's posted legs back, so
            the two cannot name different rows; its description, truncated
            here to :data:`_MAX_DESCRIPTION_LENGTH`, is the header's label.
        targets: What the ledger should net to, per ``(pay period, entry
            date)``; EMPTY to reverse the source to zero.
        source: The ``ref.posting_sources`` kind this source posts under.  It
            is stamped on the header AND filtered on when the posted side is
            read back, so two sources that ever share one link (the loan
            payment split shared the loan-side shadow's ``transaction_id``
            with the transaction source until ruling **R-BAL102** gave it no
            link at all) never reconcile each other away.
        log_label: Names the source in the per-entry INFO line.

    Returns:
        The emitted delta entries; ``[]`` when the ledger is already at target.
    """
    source_kind_id = ref_cache.posting_source_id(source)
    return emit_source_deltas(
        targets=targets,
        source_filter=db.and_(
            JournalEntry.transaction_entry_id == movement.id,
            JournalEntry.source_kind_id == source_kind_id,
        ),
        kind_id=posting_kind_of(parent),
        build_entry=source_entry_builder(
            user_id=parent.user_id,
            scenario_id=parent.scenario_id,
            source_kind_id=source_kind_id,
            description=movement.description[:_MAX_DESCRIPTION_LENGTH],
            transaction_entry_id=movement.id,
        ),
        log_label=log_label,
    )


def emit_keyed_delta_entries(
    legs_by_key: "dict[tuple[int, date], list[_PostingLeg]]",
    build_entry: "Callable[[int, date], JournalEntry]",
    log_label: str,
) -> "list[JournalEntry]":
    """Emit one balanced delta entry per ``(pay period, entry date)`` key.

    The ONE emission loop behind every per-key reconcile-to-target writer --
    the transfer sync, the transaction sync, and the loan-payment correction
    reconcile (steps E1a / N-13 re-keyed all three from per-period to
    per-``(pay_period_id, entry_date)``, and three copies of the loop was a
    measured ``duplicate-code`` finding).  Keys are emitted in sorted order
    so a run's entries are deterministic; each entry carries its key's period
    and date (the R2 attribution rule, per-date since E1a: a reversal lands
    at the exact date of the postings it reverses, the target entry at the
    source's settle date -- both of which ARE the key).

    Args:
        legs_by_key: The non-empty delta legs per key (the caller returns
            early on an empty map, keeping its own no-op contract explicit).
        build_entry: Builds the UNSAVED entry header for one key -- the
            caller's closure over its source row (user / scenario / source
            kind / linkage / description).  Called once per key with
            ``(pay_period_id, entry_date)``; this loop owns setting nothing
            on it, so the header stays entirely the caller's.
        log_label: Names the source in the per-entry INFO line (e.g.
            ``"transfer 42"``), so the shared loop logs as informatively as
            the three loops it replaced.

    Returns:
        The persisted delta entries, in key order.

    Raises:
        PostingError: From :func:`_emit_balanced_entry`, if a key's legs do
            not balance (impossible for a reconcile's per-key deltas -- both
            sides of a key sum to zero -- so a raise here means the caller's
            targets are broken).
    """
    entries = []
    for (period_id, entry_date), legs in sorted(legs_by_key.items()):
        entry = build_entry(period_id, entry_date)
        _emit_balanced_entry(entry, legs)
        logger.info(
            "Posted %s ledger deltas %s in period %d dated %s as journal "
            "entry %d",
            log_label,
            {leg.ledger_account_id: leg.amount for leg in legs},
            period_id,
            entry_date.isoformat(),
            entry.id,
        )
        entries.append(entry)
    return entries
