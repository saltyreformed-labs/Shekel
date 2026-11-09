"""
Shekel Budget App -- the stored ledger, as the tests that grade it read it.

What a test still reads of ``budget.journal_entries``, ``budget.account_postings``
and ``budget.ledger_accounts``, by plan step plan#2913 once every assertion of a
figure had moved onto ``tests/_ledger_reads.py``: most moved here from
``tests/_test_helpers.py``, and ``stored_account_net``, ``stored_per_loan_net``,
``stored_trial_balance`` and ``entries_violating_balance`` were written here from
what the package helpers held.  **The callers are the store's own machinery**: the
shape or count of a journal entry or a reversal, a re-sync, the deploy resync,
the balanced-write refusals, chart row creation, the parity tests, and a state
only the store can hold (a forged or cleared entry).  So nothing here is a
second home of a figure, and this module goes with the store.

Underscore-prefixed module name keeps pytest from collecting it.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.transfer import Transfer
from app.services._posting_reconcile import account_chart_row_ids
from app.services.ledger_lines import ChartKey
from app.services.pay_calendar import calendar_for
from app.services.posting_reads import _ledger_account_for
from app.utils.money import round_money
from tests._ledger_reads import (
    family_movement_ids,
    transfer_family_movement_ids,
    transfer_side_movement_ids,
)
from tests._test_helpers import transfer_side_record


def ledger_accounts_for_account(db_session, account_id):
    """Return every ``LedgerAccount`` linked to *account_id*.

    Shared by the ledger-account model / service / backfill suites so the
    one-line lookup is not re-inlined per file (a duplicate-code finding).

    Args:
        db_session: The test ``db.session``.
        account_id: The real account's id whose linked ledger accounts to
            fetch.

    Returns:
        list[:class:`~app.models.ledger_account.LedgerAccount`] -- every
        row carrying this ``account_id``.  Since Step 5's
        ``uq_ledger_accounts_account_kind`` re-key, TWO rows are normal
        once an account has anchor corrections: the ``linked`` row plus
        its ``anchor_equity`` twin (one per account-linked kind).  Callers
        asserting counts or shapes must say WHICH kinds they expect.
    """

    return (
        db_session.query(LedgerAccount)
        .filter_by(account_id=account_id)
        .all()
    )


def ledger_account_of_kind(db_session, account_id, kind_enum):
    """Return an account's ledger row of a given kind (linked / anchor_equity), or None.

    The shared kind-scoped lookup behind :func:`linked_ledger_account` and the
    account-anchor suites' anchor-equity lookups.  Since Step 5's
    ``(account_id, kind_id)`` re-key an account can carry TWO rows sharing its
    ``account_id`` (the ``linked`` row plus its ``anchor_equity`` equity twin),
    so any "the account's ledger account" lookup must say WHICH kind -- a bare
    ``[0]`` on :func:`ledger_accounts_for_account` is insertion-order-dependent.
    Holding the ``(account_id, kind_id)`` query in one place keeps the several
    kind-scoped lookups the posting suites use from drifting (a duplicate-code
    finding otherwise).

    Args:
        db_session: The test ``db.session``.
        account_id: The real account's id.
        kind_enum: The :class:`~app.enums.LedgerAccountKindEnum` member to
            resolve (e.g. ``LINKED`` or ``ANCHOR_EQUITY``).

    Returns:
        The :class:`~app.models.ledger_account.LedgerAccount` of that kind for
        the account, or ``None`` when the account has no such row yet.
    """
    return (
        db_session.query(LedgerAccount)
        .filter_by(
            account_id=account_id,
            kind_id=ref_cache.ledger_account_kind_id(kind_enum),
        )
        .one_or_none()
    )


def transit_ledger_account(db_session, user_id):
    """Return the owner's Transfers-in-transit ledger row, or ``None``.

    A LOOKUP, never a create -- the writer mints the ``transit`` owner bucket
    on the owner's first settled transfer
    (``ledger_account_service.get_or_create_transit_ledger_account``, plan
    step ``balance:X-bi-6-3``, ruling **R-BAL99**), so a suite that finds
    none has caught the writer not booking against it.  Keyed exactly as
    ``uq_ledger_accounts_owner_bucket`` is: the owner, the ``transit`` kind
    and the flag; the class is a function of the kind for a bucket.

    Args:
        db_session: The test ``db.session``.
        user_id: The owner's id.

    Returns:
        The ``transit`` :class:`~app.models.ledger_account.LedgerAccount`, or
        ``None`` when no transfer of the owner's has posted yet.
    """
    return (
        db_session.query(LedgerAccount)
        .filter_by(
            user_id=user_id,
            kind_id=ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.TRANSIT,
            ),
            is_owner_bucket=True,
        )
        .one_or_none()
    )


def stored_chart_key(ledger_account):
    """Return the ``ChartKey`` a STORED chart row stands for, read off its columns.

    The bridge the worked-out ledger's proof maps by (plan#2513: plan#2523
    builds it, the parity test plan#2527 and the production run plan#2528 map
    each worked-out line onto its stored row with it).  It reads the row AS
    STORED -- its ``kind_id``, its ``class_id``, and the one link column its
    shape sets -- and deliberately re-derives nothing: a stored class the
    worked-out rule would not give (a stale snapshot) must surface as a key
    that differs, never be repaired by the mapping.  The record is whichever of
    ``account_id`` / ``category_id`` / ``loan_account_id`` is set; the shape
    CHECKs allow at most one, and an owner bucket sets none.

    A row no worked-out key can name maps to a key no constructor builds: an
    ``orphan``, or a ``category`` row whose category was deleted (its
    ``category_id`` SET NULL, its kind unchanged), whose ``record_id`` is
    ``None``.  The records book those lines on the owner's Uncategorized bucket
    instead, so a nonzero stored net on one is a difference the proof names.

    Args:
        ledger_account: The stored
            :class:`~app.models.ledger_account.LedgerAccount`.

    Returns:
        The :class:`~app.services.ledger_lines.ChartKey` its columns spell.
    """
    kinds = {
        ref_cache.ledger_account_kind_id(member): member
        for member in LedgerAccountKindEnum
    }
    classes = {
        ref_cache.ledger_account_class_id(member): member
        for member in LedgerAccountClassEnum
    }
    # At most one is set: ``ck_ledger_accounts_account_or_category_null`` and
    # ``ck_ledger_accounts_loan_shape`` hold the row to it.
    links = [
        link for link in (
            ledger_account.account_id,
            ledger_account.category_id,
            ledger_account.loan_account_id,
        )
        if link is not None
    ]
    return ChartKey(
        kinds[ledger_account.kind_id],
        classes[ledger_account.class_id],
        links[0] if links else None,
    )


def linked_ledger_account(db_session, account_id):
    """Return the account's LINKED ledger row, never its anchor-equity twin.

    Since Step 5's ``(account_id, kind_id)`` re-key an account with posted
    anchor corrections carries TWO ledger rows sharing its ``account_id``
    (the ``linked`` row plus the ``anchor_equity`` equity twin), so any
    helper that grabs "the account's ledger account" must say WHICH kind --
    a bare ``[0]`` on :func:`ledger_accounts_for_account` is
    insertion-order-dependent.  This is the shared linked-kind lookup the
    posting suites key their cash-leg assertions off (the ``LINKED`` case of
    :func:`ledger_account_of_kind`).

    Args:
        db_session: The test ``db.session``.
        account_id: The real account's id.

    Returns:
        The LINKED :class:`~app.models.ledger_account.LedgerAccount`, or
        ``None`` when the account has no pairing yet.
    """
    return ledger_account_of_kind(
        db_session, account_id, LedgerAccountKindEnum.LINKED,
    )


def loan_correction_entries(db_session, transfer_id, loan_account_id):
    """Return the loan_payment corrections booked at a payment's KEY.

    The ``budget.journal_entries`` rows the loan posting package books for one
    settled payment's split, ordered by id.  **Re-expressed at plan step
    ``balance:X-bi-6-3`` (ruling R-BAL102, a rule-5 class (a) re-expression
    Josh confirmed 2026-09-21):** the split is a DERIVATION keyed
    ``(loan_payment kind, the payment's pay period, its visible day)`` on the
    loan's own chart rows and links NO row, so "the corrections under this
    shadow" is now "the corrections at this payment's key" -- selected by
    source kind, the payment's pay period, its settled day and a leg on the
    loan's chart rows.  Through that step the split carried the shadow's
    ``transaction_id`` and this read it by that link.  **The key is read off
    the TRANSFER and the loan side's RECORD since plan step
    ``balance:X-bi-6-4d-2``** (ruling **R-BAL167** class 4): the record hangs
    off the transfer there and the twins' status and day are no longer kept,
    so the shadow it was passed named only which transfer and which side; it
    read the shadow's own period, scenario and day until then (the loan walk
    has read the record's day since ``X-bi-6-4b``).  **It is keyed by the
    transfer and the loan account since plan step ``balance:X-bi-6-4d-2``**
    (ruling **R-BAL167** class 4): a transfer is one row there and no
    shadow exists to name the side.  A side with no dated record
    has no key and is REFUSED rather than answered ``[]`` (the leaf-2
    adversarial review: an empty answer for a reverted payment would let
    "after the revert the split is gone" pass without reading the ledger);
    a test that holds a reverted or deleted payment reads
    :func:`loan_correction_entries_at` with the key it captured before the
    act.  Shared by the split-service, wiring, backfill and reconciliation
    suites so all read a payment's corrections the same way.

    Args:
        db_session: The test ``db.session``.
        transfer_id: The payment's ``budget.transfers`` id.
        loan_account_id: The loan (to-)account whose side holds the record
            and whose chart rows scope the read.

    Returns:
        list[:class:`~app.models.journal_entry.JournalEntry`] -- the correction
        entries at the payment's key, ascending by id (empty when none).

    Raises:
        ValueError: If the loan side holds no dated record (it has no key).
    """
    record = transfer_side_record(db_session, transfer_id, loan_account_id)
    if record is None or record.settled_on is None:
        raise ValueError(
            f"loan_correction_entries: transfer {transfer_id}'s side "
            f"on account {loan_account_id} holds no dated record and so no "
            f"split key; read loan_correction_entries_at with the key "
            f"captured before the act that released the day."
        )
    transfer = db_session.get(Transfer, transfer_id)
    return loan_correction_entries_at(
        db_session, loan_account_id, transfer.scenario_id,
        transfer.pay_period_id, record.settled_on,
    )


def loan_correction_entries_at(
    db_session, loan_account_id, scenario_id, period_id, day,
):
    """Return the loan_payment corrections at ONE key on a loan's chart rows.

    The key form of :func:`loan_correction_entries`, for a test that holds a
    key's period and day but no dated loan-side record to read the key off --
    after a revert (the side keeps its record with its day
    released, and the correction and the reversal the next reconcile posts
    both sit at the old key), after a hard delete (the transfer is gone, its
    sides' records taken off before it), after a ``$0.00`` close or a
    correction to ``$0.00`` (the side holds no record, and the split is dated
    by the installment the payment skips, ruling **R-BAL139**), or at a key
    the walk prices nothing (a residue planted there).  The ``loan_payment``
    journal entries in *period_id* dated *day* with a leg on any of the loan's
    own chart rows
    (:func:`app.services._posting_reconcile.account_chart_row_ids`: its linked
    row and its per-loan interest / escrow / refund / opening-equity rows) in
    *scenario_id* (postings are scenario-scoped, and a loan paid in two
    scenarios holds one split per scenario at the same key), ascending by id.

    Args:
        db_session: The test ``db.session``.
        loan_account_id: The loan whose chart rows scope the read.
        scenario_id: The budget scenario the split was posted in.
        period_id: The key's ``pay_period_id``.
        day: The key's ``entry_date``.

    Returns:
        list[:class:`~app.models.journal_entry.JournalEntry`], ascending by id
        (empty when nothing is posted at the key).
    """
    on_loan_rows = (
        db_session.query(Posting.journal_entry_id)
        .filter(Posting.ledger_account_id.in_(
            account_chart_row_ids(loan_account_id),
        ))
    )
    return (
        db_session.query(JournalEntry)
        .filter(
            JournalEntry.source_kind_id == ref_cache.posting_source_id(
                PostingSourceEnum.LOAN_PAYMENT
            ),
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.pay_period_id == period_id,
            JournalEntry.entry_date == day,
            JournalEntry.id.in_(on_loan_rows),
        )
        .order_by(JournalEntry.id)
        .all()
    )


def stored_ledger_net(db_session, ledger_account_id, scenario_id):
    """Return the net of every posting leg on a ledger account in a scenario.

    Sums ``budget.account_postings.amount`` over *ledger_account_id* for journal
    entries in *scenario_id*.

    Args:
        db_session: The test ``db.session``.
        ledger_account_id: The ledger account whose legs to sum.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net as a ``Decimal`` (``Decimal("0")`` when none posted).
    """
    return (
        db_session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == ledger_account_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def stored_account_net(account_id, scenario_id):
    """Return the net of an account's LINKED row's legs in a scenario.

    Keyed off the real account (``ledger_accounts.account_id``) and filtered to
    the LINKED kind: an anchor correction lands ``+delta`` on the linked row and
    ``-delta`` on the ``anchor_equity`` twin, which shares the ``account_id``
    column, so a bare ``account_id`` sum would cancel the correction pairwise.

    Args:
        account_id: The real account's id.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net as a ``Decimal`` (``Decimal("0")`` when none posted).
    """
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
        )
        .select_from(Posting)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(
            LedgerAccount.account_id == account_id,
            LedgerAccount.kind_id == ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.LINKED,
            ),
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def stored_per_loan_net(loan_account_id, scenario_id):
    """Return the net of a loan's per-loan chart rows' legs in a scenario.

    Keyed on ``ledger_accounts.loan_account_id``: the loan's interest, escrow,
    refund and opening-equity rows, which carry ``account_id IS NULL``.

    Args:
        loan_account_id: The loan's account id.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net as a ``Decimal`` (``Decimal("0")`` when none posted).
    """
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
        )
        .select_from(Posting)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(
            LedgerAccount.loan_account_id == loan_account_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def stored_trial_balance():
    """Return ``SUM(account_postings.amount)`` over the whole stored ledger."""
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
        )
        .scalar()
    )


def entries_violating_balance():
    """Return ``(entry_id, leg_sum, leg_count)`` for every malformed stored entry.

    A well-formed double-entry has ``leg_sum == 0`` and ``leg_count >= 2``.  Any
    row returned here is a violation -- the per-entry invariant the deferred
    trigger also enforces, re-checked from the ORM side.

    Returns:
        The violating entries, empty for a well-formed journal.
    """
    rows = (
        db.session.query(
            Posting.journal_entry_id,
            db.func.sum(Posting.amount),
            db.func.count(Posting.id),
        )
        .group_by(Posting.journal_entry_id)
        .all()
    )
    return [
        (entry_id, leg_sum, leg_count)
        for entry_id, leg_sum, leg_count in rows
        if leg_sum != 0 or leg_count < 2
    ]


def stored_net_by_date(db_session, ledger_account_id, scenario_id):
    """Return a ledger account's posted net keyed by journal-entry ``entry_date``.

    Sums ``budget.account_postings.amount`` over *ledger_account_id* for journal
    entries in *scenario_id*, grouped by each entry's ``entry_date``.  A date whose
    legs net to zero (a reversed forgery) is still present, at ``Decimal("0.00")``.
    This is an INDEPENDENT re-implementation of the grouped read the
    checked-projection assert performs (production groups via ``_visible_nets``), so
    a suite comparing against it keeps teeth if that production query drifts.  Shared
    by the posting-service checked-projection suite and the loan-route escrow-sync
    suite so both read a ledger's per-date net the same way (``stored_ledger_net`` is the
    scalar-total counterpart).

    Args:
        db_session: The test ``db.session``.
        ledger_account_id: The ledger account whose legs to sum.
        scenario_id: The scenario to scope to.

    Returns:
        A ``{entry_date: Decimal}`` mapping (empty when none posted).
    """
    rows = (
        db_session.query(
            JournalEntry.entry_date,
            db.func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == ledger_account_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .group_by(JournalEntry.entry_date)
        .all()
    )
    return dict(rows)


def _posted_loan_linked_ledger(loan_account_id, scenario_id):
    """Return a loan's linked ledger row, or None when it has no OPENING posting.

    The entry guard the posting window rests on, held in one place so the
    scalar and the per-period form cannot disagree about which loans the ledger
    can answer for.  A loan gets exactly one OPENING-kind leg on its linked
    ledger per scenario (the origination anchor correction, whose linked leg is
    ``-original_principal``), so its absence means the loan is not configured in
    this scenario -- an unconfigured loan, or a what-if the opening was never
    posted into.

    Args:
        loan_account_id: The loan account whose linked ledger to resolve.
        scenario_id: The budget scenario to scope to.

    Returns:
        The linked :class:`~app.models.ledger_account.LedgerAccount`, or ``None``
        when no OPENING leg is posted on it in *scenario_id*.

    Raises:
        PostingError: If the loan account has no linked ledger account at all (a
            broken chart-of-accounts pairing).
    """
    linked = _ledger_account_for(loan_account_id)
    has_opening = db.session.query(
        db.session.query(Posting.id)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == linked.id,
            Posting.posting_kind_id == ref_cache.posting_kind_id(
                PostingKindEnum.OPENING,
            ),
            JournalEntry.scenario_id == scenario_id,
        )
        .exists()
    ).scalar()
    return linked if has_opening else None


def stored_loan_balance_at(loan_account_id, scenario_id, as_of):
    """Return what the POSTED ledger says a loan owed on a date, or None.

    ``owed(as_of) = round_money(-(sum of the loan's linked-ledger postings,
    scenario-scoped, whose ``entry_date`` is on or before *as_of*))`` -- the
    opening (``-original_principal``), every settled payment's net principal (the
    Step-2 cash leg plus the Step-4 split correction), and every true-up.

    **This is the test suite's own window onto the general ledger, and that is
    deliberate.**  Plan step E1e deleted the production readers this replaces
    (``loan_posting_service.confirmed_loan_balance_at`` / ``_map``): the balance
    seam answers a loan from the event FOLD (steps C3b1 / C3b3 / E1d-b) and the
    balance sheet reads the postings through
    :mod:`app.services.ledger_report_service`, so the sum-of-postings
    balance-at-T had no production caller left.  An oracle's window belongs on the
    oracle's side: a window living here cannot be re-exported into a screen.  Since
    plan#2913 the fold and the resolver are graded against the worked-out window,
    :func:`tests._ledger_reads.posted_loan_balance_at`, and this answers only for
    the store's own machinery.

    Two deliberate differences from the deleted reader, both stated so a caller is
    not surprised:

    * it answers ANY date, where the reader raised for ``as_of > today``.  That
      raise was a DOMAIN guard forcing production callers to route a future date
      to the projection; there are no such callers now.  A future date sums the
      same postings (every posted entry is dated today or earlier), so it carries
      the confirmed balance FLAT -- which is exactly what the deleted per-period
      reader did, and what the early-settle parallel run reads at period ends
      beyond today.
    * the per-period form (:func:`stored_loan_balance_map`) is a plain
      per-period call, not a prefix-sum over one grouped load.  The batching
      existed for production read cost; an oracle wants the simplest derivation
      that can be checked by eye.

    Args:
        loan_account_id: The loan account whose posted balance to read.
        scenario_id: The budget scenario to scope to (postings are
            scenario-scoped via ``journal_entries.scenario_id``).
        as_of: The evaluation date.  Only postings whose ``entry_date`` is on or
            before it are summed.

    Returns:
        The posted balance owed as a cent-quantized ``Decimal``, or ``None`` when
        the loan has no OPENING posting in the scenario.
    """
    linked = _posted_loan_linked_ledger(loan_account_id, scenario_id)
    if linked is None:
        return None
    net = (
        db.session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0.00"))
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == linked.id,
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.entry_date <= as_of,
        )
        .scalar()
    )
    # Debit-positive ledger: the linked net is -(owed).  Written ``0 - net``
    # rather than ``-net`` so a zero net (a loan read before its opening's date)
    # yields ``0.00``, never ``-0.00``.
    return round_money(Decimal("0.00") - net)


def stored_loan_balance_map(loan_account_id, scenario_id, periods):
    """Return each period's posted loan balance, keyed by period id, or None.

    The per-period form of :func:`stored_loan_balance_at`, evaluated at each
    period's END date.  A posting can fall mid-period under the one clock (step
    C2), so a payment settled during period P must count in P's balance, which
    ``entry_date <= <the period's last covered day>`` selects.

    **It DERIVES that day rather than reading it off the row** (plan step
    ``pay_calendar:C4-c``, which dropped the column it used to read).  The
    owner's calendar is resolved once and each requested period's derived twin
    supplies the bound, which is :func:`period_window`'s shape and it is here
    for the same reason: a period's end is the day before the NEXT payday, so
    it is a property of the whole payday set rather than of one row.

    **It is the scalar per period, not an independent derivation.**  Comparing
    this against :func:`stored_loan_balance_at` at the same date is therefore
    ``f(x) == f(x)`` -- pin a VALUE, never that identity.  Nor is it directly
    comparable to the production map
    (:func:`app.services.balance_at.positions_period_map`) except over ELAPSED
    periods: that map clamps a BEGUN period to ``min(period.end, ctx.as_of)``
    and answers a FUTURE period from the forward plan, where this window carries
    the confirmed sum flat.  The keys line up; the values only do in the past.

    Returns ``None`` -- not a map of zeros -- when the loan has no OPENING
    posting in the scenario, so a caller can tell "the ledger cannot answer for
    this loan" from "the ledger says zero".

    Args:
        loan_account_id: The loan account whose per-period balances to read.
        scenario_id: The budget scenario to scope to.
        periods: The ``PayPeriod`` rows to key by, in any order and all
            belonging to one user.  Must be non-empty -- an empty request has
            no owner to resolve a calendar for.  The result keys by
            ``period.id`` in PAYDAY order, which is the calendar's.  Postings
            in periods outside this list are still counted -- each period's
            value is a cumulative, not a slice.

    Returns:
        A ``{period.id: Decimal}`` mapping, or ``None`` when the loan has no
        OPENING posting in the scenario.
    """

    if _posted_loan_linked_ledger(loan_account_id, scenario_id) is None:
        return None
    # Materialised once: the owner is read off the first row and the id set off
    # all of them, so a generator argument would be half consumed by the time
    # the owner is asked for.  Every caller passes a list today; this is what
    # keeps that from being a precondition nobody states.
    rows = tuple(periods)
    assert rows, "stored_loan_balance_map needs at least one period to key by"
    owners = {period.user_id for period in rows}
    assert len(owners) == 1, (
        f"stored_loan_balance_map resolves ONE owner's calendar and was given "
        f"periods from {sorted(owners)}; the rest would be silently dropped"
    )
    wanted = {period.id for period in rows}
    calendar = calendar_for(rows[0].user_id)
    answer = {
        period.period_id: stored_loan_balance_at(
            loan_account_id, scenario_id, period.end_date,
        )
        for period in calendar.saved()
        if period.period_id in wanted
    }
    # **One key per REQUESTED period, which is the contract the stored-column
    # version had for free.**  Selecting from the calendar means a period the
    # calendar does not hold -- unflushed, or another owner's -- would simply
    # vanish from the result, and only two of this helper's callers assert the
    # length.  A short map is a caller reading a KeyError several lines later
    # about a period it did pass in.
    assert set(answer) == wanted, (
        f"periods {sorted(wanted - set(answer))} are not in the owner's saved "
        f"calendar, so they were never flushed or belong to someone else"
    )
    return answer


def find_loan_ledger_account(db_session, loan_account_id, kind):
    """Return the per-loan ledger account of *kind*, or None if not created.

    The per-loan interest / escrow / refund ledger account
    (``loan_account_id`` + the ``kind`` discriminator), lazily minted by the
    Step-4 chart resolver on first use.  Shared by the Step-4 split-service and
    wiring suites.

    Args:
        db_session: The test ``db.session``.
        loan_account_id: The loan whose per-loan ledger account to find.
        kind: The :class:`~app.enums.LedgerAccountKindEnum` member
            (``LOAN_INTEREST`` / ``LOAN_ESCROW`` / ``LOAN_REFUND``).

    Returns:
        The :class:`~app.models.ledger_account.LedgerAccount`, or ``None`` when
        the resolver has not minted it yet.
    """
    return (
        db_session.query(LedgerAccount)
        .filter_by(
            loan_account_id=loan_account_id,
            kind_id=ref_cache.ledger_account_kind_id(kind),
        )
        .one_or_none()
    )


def stored_linked_ledger_total(account_id):
    """Return the summed posting amount on one account's LINKED ledger account.

    The account's ledger-native balance as the double-entry ledger holds it:
    the anchor reconcile's whole job is to keep this equal to the account's
    resolved balance assertion plus the settled movements recorded after it.
    Two concurrent reconciles that both computed their delta against the same
    posted state leave the two permanently apart, and this is the left-hand
    side of the assertion that catches it.

    **NOT scenario-scoped, and its one caller depends on that being harmless.**
    It sums every posting on the linked ledger across ALL scenarios, so the
    invariant "linked ledger total == resolved assertion" holds only while the
    database has a single scenario -- which is true today (production creates
    baselines only) and stops being true the moment scenario clone ships.  Said
    here rather than discovered later, because the failure would look like a
    concurrency regression and would not be one.

    Args:
        account_id: The account whose linked ledger to total.

    Returns:
        The sum of every posting on that ledger account as a
        :class:`~decimal.Decimal` (``Decimal("0.00")`` when nothing is posted).

    Raises:
        PostingError: When the account has no linked ledger account.
    """

    # ``_ledger_account_for`` RAISES ``PostingError`` for an unpaired account;
    # it never returns ``None``.  An earlier version of this helper guarded for
    # ``None`` and returned ``$0.00``, which was dead code that would also have
    # reported "nothing posted" for a broken chart of accounts.
    linked = _ledger_account_for(account_id)
    rows = (
        db.session.query(Posting.amount)
        .filter(Posting.ledger_account_id == linked.id)
        .all()
    )
    return sum((amount for (amount,) in rows), Decimal("0.00"))


def family_journal_filter(txn):
    """Return the SQL clause selecting the journal entries of *txn*'s FAMILY.

    **A settled bill's money is posted under its covering movement since plan
    step X-bi-3a** (ruling **R-BAL39**): the status seam mirrors the
    settlement as one ``transaction_entries`` row, the bill's own leg reads
    zero, and the posting writer files the money under
    ``journal_entries.transaction_entry_id`` (the purchase source) rather than
    under ``transaction_id``, a link plan step ``balance:X-bi-6-5``'s second
    leaf (card plan#1845) deleted with the row's own ``transaction`` leg.  A
    suite that read "the row's postings" by ``JournalEntry.transaction_id``
    alone read an empty ledger for a covered bill.  This is the ONE spelling
    of the family read, so the 38 cases the developer confirmed on 2026-09-15
    widen their subject the same way and no figure moves: the row's
    movements' entries.

    The movements are read through ``Transaction.entries`` -- EVERY movement
    of the row since plan step ``balance:X-bi-4a`` (ruling **R-BAL80**: the
    fold and the writer read a row's money as its movements alone, a dated
    purchase's leg as much as the covering movement's; through ``X-bi-3e``
    this read the covering movements only, while the row's own leg carried
    an envelope's un-dated purchases).  A revert KEEPS the mirror, un-dated
    (plan step **X-bi-3e-2**, ruling **R-BAL61**), so a reverted row's family
    includes its survivor's linked postings -- a net-zero pair once the
    door's reconcile has reversed them -- where through 3e-1 the mirror was
    deleted and the pair stood unlinked.  Every reader of this filter sums a
    net or scopes by period, so the pair changes no figure.

    Args:
        txn: The :class:`~app.models.transaction.Transaction`, or its id.

    Returns:
        A SQLAlchemy boolean clause over ``JournalEntry``.
    """
    return JournalEntry.transaction_entry_id.in_(family_movement_ids(txn))


def transfer_family_journal_filter(transfer_id):
    """Return the SQL clause selecting the journal entries of a TRANSFER's family.

    **A settled transfer's money is posted under its two covering movements
    since plan step ``balance:X-bi-6-3``** (rulings **R-BAL45** and
    **R-BAL101**): one entry per side, linked by
    ``journal_entries.transaction_entry_id`` to the movement covering that
    side (the side's record; it hung off the side's shadow until the
    re-parent below), each against the owner's Transfers-in-transit account.  A
    suite read "the transfer's postings" by ``JournalEntry.transfer_id`` until
    plan step ``balance:X-bi-6-5``'s second leaf (card plan#1845) deleted that
    link with the LEGACY one-entry shape it named, which no go-forward settle
    wrote.  This is the ONE spelling of the transfer family read -- the
    records of BOTH sides of the transfer -- so the cases the developer
    confirmed when the shape moved widen their subject the same way and no
    figure per real account moves.

    **Independent SQL over the side links** (plan step
    ``balance:X-bi-6-4d-2``, ruling **R-BAL167** class 4): a record hangs off
    the TRANSFER by ``expense_transfer_id`` / ``income_transfer_id`` since
    that step, and this reads those columns itself rather than through
    ``transfer_legs``, the producer the suite grades -- a helper selecting
    money through the producer under test cannot catch that producer's
    fault.  Until then it read the movements under every shadow of the
    transfer; a record keeps its id across the re-parent, so a journal entry
    that linked it still does.

    Args:
        transfer_id: The ``budget.transfers`` id.

    Returns:
        A SQLAlchemy boolean clause over ``JournalEntry``.
    """
    return JournalEntry.transaction_entry_id.in_(transfer_family_movement_ids(transfer_id))


def transfer_side_journal_filter(transfer_id, account_id):
    """Return the SQL clause selecting ONE side's entries of a transfer's family.

    The entries linked to the record of the transfer's side on *account_id*
    -- the loan-side cash entry of a loan payment, or either side of a cash
    transfer -- since plan step ``balance:X-bi-6-3`` (a side is an entry of
    its own, rulings **R-BAL45** and **R-BAL101**).  The per-side twin of
    :func:`transfer_family_journal_filter`, and independent SQL over the side
    links for its reason: a side's record is on its endpoint (the side keys),
    so the side on *account_id* is the linked record on that account.

    Args:
        transfer_id: The ``budget.transfers`` id.
        account_id: The account the side is on.

    Returns:
        A SQLAlchemy boolean clause over ``JournalEntry``.
    """
    return JournalEntry.transaction_entry_id.in_(transfer_side_movement_ids(transfer_id, account_id))
