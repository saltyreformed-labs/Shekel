"""Loans: the split-loan figures, and the writers of a loan account, its rate and anchor events,
its genesis ledger and its escrow lines.
"""

from collections import namedtuple
from datetime import date as _real_date
from decimal import Decimal

from ._anchors import open_books_before_the_first_assertion


# The synthetic split-loan fixture shared verbatim by the three parallel
# loan-posting suites (unit / reconciliation-oracle / wiring): a $250,000 loan
# originated 2025-01-01 at 6%, trued up to $100,000 on 2026-01-10.  The trueup
# balance deliberately differs from origination so a correct interest figure
# proves the walk's anchor reset (a payment before the trueup accrues on
# $250,000, one after on $100,000).  ``p1`` / ``p2`` / ``p3`` are the
# ``seed_periods`` indices whose monthly due dates (payment_day=1) land in
# distinct months after the anchor (02-01 / 03-01 / 04-01).  Single-sourced here
# so a fixture change touches one place, not three; each suite unpacks it into
# its own module-level names.
_SplitLoan = namedtuple(
    "_SplitLoan",
    "origination_principal origination_date rate anchor_balance anchor_date "
    "p1 p2 p3",
)


SPLIT_LOAN = _SplitLoan(
    origination_principal=Decimal("250000.00"),
    origination_date=_real_date(2025, 1, 1),
    rate=Decimal("0.06000"),
    anchor_balance=Decimal("100000.00"),
    anchor_date=_real_date(2026, 1, 10),
    p1=1, p2=3, p3=5,
)


def insert_origination_rate(loan_params, interest_rate):
    """Append the origination :class:`RateHistory` row for a loan.

    Mirrors the production-code pattern in
    :func:`app.routes.loan.create_params` (DH-#56): the loan's base /
    period-0 rate lives in the :class:`RateHistory` row effective at
    origination, not the retired ``LoanParams.interest_rate`` column.
    The resolver raises ``ValueError`` when a loan's rate-change feed is
    empty (no origination row), so every fixture that builds
    :class:`LoanParams` directly and then resolves it (loan dashboard,
    debt strategy, /savings debt card, year-end liability, contractual
    P&I) MUST call this helper after inserting :class:`LoanParams`.

    Args:
        loan_params: The :class:`LoanParams` ORM instance, already
            flushed (``loan_params.account_id`` populated).
        interest_rate: The origination annual rate as a Decimal fraction
            (e.g. ``Decimal("0.06875")`` for 6.875%).

    Returns:
        The newly added :class:`RateHistory` instance,
        ``db.session.add()``'d but not committed.  The caller's existing
        ``db.session.commit()`` carries the row into the same transaction.
    """
    # pylint: disable=import-outside-toplevel  -- avoid module-load
    # circular deps via models package; tests/_test_helpers loads
    # early enough that an unconditional top-level import would
    # snowball into ref_cache / Flask app bootstrapping.
    from app.extensions import db
    from app.models.loan_features import RateHistory

    row = RateHistory(
        account_id=loan_params.account_id,
        effective_date=loan_params.origination_date,
        interest_rate=interest_rate,
        monthly_pi=None,
    )
    db.session.add(row)
    return row


def _sync_loan_ledger(loan_account_id):
    """Reconcile a loan's genesis ledger, exactly as every production writer does.

    The shared write-through step of the three loan fixtures below
    (:func:`create_loan_account`, :func:`insert_trueup_event`,
    :func:`insert_tracking_start_event`).  Every production path that creates or
    re-bases a loan's anchors reconciles the genesis postings in the SAME
    transaction as the source row:

    * ``loan.create_params`` / ``loan.update_params`` call
      :func:`~app.services.loan_posting_service.sync_loan_postings_all_scenarios`
      (``app/routes/loan/params.py:125`` and ``:177``).
    * The balance true-up and the tracking-start opening both route through
      :func:`app.services.loan_anchor_service._append_loan_anchor_and_sync`, which
      appends the event, re-syncs, and commits (``anchor_service.py:390``).

    The property that matters, and the one this helper reproduces, is that the
    event and its postings land in the SAME transaction -- never an event alone.
    This helper flushes rather than commits, keeping the existing fixture contract
    (the caller owns the transaction and commits with the rest of its setup).

    Without this, a fixture-built loan carries anchor rows but NO opening
    posting -- a state production cannot produce, and the one every loan built
    through these helpers used to be in (so it exercised the no-ledger fallback
    while production always took the ledger path).

    **Scope, stated plainly so nobody over-trusts it.**  This reaches only loans
    built through :func:`create_loan_account` / :func:`create_loan_with_trueup` /
    :func:`insert_trueup_event` / :func:`insert_tracking_start_event`.  Roughly
    twenty test modules still hand-roll a loan (a bare ``LoanParams(...)`` insert;
    e.g. ``tests/test_services/test_balance_at/_helpers.py::_make_mortgage``, a near-copy of
    :func:`create_loan_account`).  Such a loan gets a ledger only if one of the
    anchor helpers above happens to be called on it afterwards, and otherwise has
    none at all -- ``test_year_end_summary_service.py``'s hand-built loan is in
    exactly that state.  That coupling is invisible at the call site and is a real
    trap; routing the hand-rolled builders through this factory is a follow-up
    (and a prerequisite for the fail-loud read seam, which turns a ledger-less loan
    into a raise).

    Deliberately calls the PLAIN sync rather than production's
    :func:`~app.services.loan_posting_service.sync_all_scenarios_or_duplicate`:
    the duplicate-translating wrapper exists to turn a user's double-click into
    idempotent success, and it does so by ROLLING BACK.  In a fixture that would
    silently discard the test's setup; a duplicate anchor written by a fixture is
    a bug in the fixture and must fail loud.

    Flushes but does NOT commit -- the caller owns the transaction boundary, the
    same contract the production chokepoints keep.

    **No future-anchor guard, because there is nothing left to guard.**  This
    helper used to assert that no user-asserted anchor was dated after the sync's
    as-of, because the walk DROPPED such an anchor and left the loan half-opened
    (opening present, true-up missing) -- a state that looked ledger-backed and
    was not.  The walk no longer reads a clock: it records every anchor the loan
    carries, whatever its date, and the readers decide what has happened
    (``loan_ledger.walk_loan_ledger``).  So a fixture's anchor
    posts whatever its date, and a fixture can no longer build the half-opened
    loan by accident.

    Args:
        loan_account_id: The loan whose genesis ledger to reconcile.
    """
    # Pylint: ``import-outside-toplevel`` -- same circular-dep avoidance as every
    # other helper in this module: the services package must not load at
    # tests/_test_helpers import time.
    # pylint: disable=import-outside-toplevel
    from app.extensions import db
    from app.services import loan_posting_service

    loan_posting_service.sync_loan_postings_all_scenarios(loan_account_id)
    db.session.flush()


def clear_loan_ledger(loan_account_id):
    """Delete a loan's genesis postings -- the BROKEN state production cannot make.

    The exact inverse of :func:`_sync_loan_ledger`: removes the journal entries
    the loan sync produces (``loan_opening``, ``loan_trueup``, and the
    ``loan_payment`` split corrections) on any of the loan's own ledger accounts,
    leaving the Step-2/3 CASH entries untouched.  The posting ledger is
    append-only (the ORM blocks deletes on ``budget.journal_entries`` /
    ``budget.account_postings``), so it clears them via raw SQL.

    A configured loan with no opening posting is NOT a legitimate state: the
    opening is written in the same transaction as the ``LoanParams``
    (``app/routes/loan/params.py:125``), the Step-4 migration backfilled every
    pre-existing loan, and ``pay_period_admin.reset_pay_periods`` re-syncs.  This
    helper exists so the handful of tests that pin behaviour ON that broken state
    -- the readers' ``None`` contract, and the seam's fail-loud raise -- can
    construct it EXPLICITLY and say so at the call site.

    It is deliberately the only way to build a ledger-less loan THROUGH THESE
    HELPERS (a hand-rolled ``LoanParams`` insert still yields one by omission --
    see :func:`_sync_loan_ledger`).  A boolean ``open_ledger=False`` knob on
    :func:`create_loan_account` would leave a casual escape hatch back onto the
    fallback path this arc exists to delete; a call to something named
    ``clear_loan_ledger`` cannot be made by accident.

    Commits.

    Args:
        loan_account_id: The loan whose genesis ledger to remove.
    """
    # Pylint: ``import-outside-toplevel`` -- same lazy-app-import convention every
    # helper in this module follows.
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import PostingSourceEnum
    from app.extensions import db

    # ID-based, never a name-string compare (the project's ref-table rule): the
    # three source kinds the loan sync emits.
    genesis_source_ids = [
        ref_cache.posting_source_id(source)
        for source in (
            PostingSourceEnum.LOAN_OPENING,
            PostingSourceEnum.LOAN_TRUEUP,
            PostingSourceEnum.LOAN_PAYMENT,
        )
    ]
    # A loan owns two shapes of ledger account: the LINKED one (``account_id``)
    # and its derived opening-equity / interest / escrow / refund ones
    # (``loan_account_id``).  Scope to entries of a genesis kind touching either.
    #
    # The entry ids are resolved to a Python list FIRST, deliberately: the
    # predicate reaches the entry THROUGH its postings, so deleting the postings
    # would empty the predicate and strand every journal-entry header behind it.
    entry_ids = [
        row[0] for row in db.session.execute(db.text("""
            SELECT DISTINCT je.id
            FROM budget.journal_entries je
            JOIN budget.account_postings p ON p.journal_entry_id = je.id
            JOIN budget.ledger_accounts la ON la.id = p.ledger_account_id
            WHERE je.source_kind_id = ANY(:src)
              AND (la.account_id = :a OR la.loan_account_id = :a)
        """), {"a": loan_account_id, "src": genesis_source_ids}).all()
    ]
    if not entry_ids:
        return
    # Legs before the header, for explicitness -- the FK CASCADE would do it
    # either way, and the ordering carries no safety property: the balanced-entry
    # constraint trigger fires AFTER INSERT OR UPDATE only
    # (``app/posting_infrastructure.py:151``), never on DELETE, so an entry cannot
    # be caught mid-delete with too few legs.
    db.session.execute(db.text(
        "DELETE FROM budget.account_postings "
        "WHERE journal_entry_id = ANY(:ids)"
    ), {"ids": entry_ids})
    db.session.execute(db.text(
        "DELETE FROM budget.journal_entries WHERE id = ANY(:ids)"
    ), {"ids": entry_ids})
    db.session.commit()


def insert_trueup_event(loan_params, anchor_balance, anchor_date=None):
    """Append a user-trueup :class:`LoanAnchorEvent` asserting a balance.

    Mirrors the production balance-trueup path
    (:func:`app.services.loan_anchor_service.apply_loan_anchor_true_up`,
    E-18 / Commit 16): the operator asserts a new dated balance and the
    resolver replays forward from this latest event.  Under the
    contractual-schedule balance model, a cash overpayment does NOT
    auto-reduce the balance, so a fixture that needs a loan in a known
    state -- in particular paid off (``anchor_balance`` of
    ``Decimal("0.00")``) -- records it as the explicit operator action
    it now is: a balance true-up, exactly as the user does after making
    an extra or lump-sum payment.

    Like production, the event is RECONCILED INTO POSTINGS in the same
    transaction (:func:`_sync_loan_ledger`): ``apply_loan_anchor_true_up`` appends
    the row and re-syncs every scenario
    (``loan_anchor_service._append_loan_anchor_and_sync``, which then commits; this
    helper leaves the commit to its caller).  An un-reconciled true-up
    does not exist as far as the ledger is concerned -- and the ledger is what
    every loan surface now reads -- so a fixture that only wrote the event left
    the loan reporting its pre-true-up balance on every page.

    Args:
        loan_params: The :class:`LoanParams` ORM instance, already
            flushed (``account_id`` populated).
        anchor_balance: The asserted balance (Decimal); ``0.00`` marks
            the loan paid off.
        anchor_date: The date the balance was asserted.  Defaults to
            ``origination_date + 1 day`` so it sorts strictly after the
            origination event and becomes the resolver's latest anchor.

    Returns:
        The newly added :class:`LoanAnchorEvent` (added and reconciled into
        postings, not committed).
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as insert_origination_rate above.
    from datetime import timedelta
    from app import ref_cache
    from app.enums import LoanAnchorSourceEnum
    from app.extensions import db
    from app.models.loan_anchor_event import LoanAnchorEvent

    if anchor_date is None:
        anchor_date = loan_params.origination_date + timedelta(days=1)
    event = LoanAnchorEvent(
        account_id=loan_params.account_id,
        anchor_date=anchor_date,
        anchor_balance=anchor_balance,
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.USER_TRUEUP,
        ),
    )
    db.session.add(event)
    _sync_loan_ledger(loan_params.account_id)
    return event


def insert_tracking_start_event(loan_params, anchor_balance, anchor_date):
    """Append a ``tracking_start`` :class:`LoanAnchorEvent` (mid-life import).

    Mirrors the production tracking-start paths (the setup door's
    :func:`app.services.loan_anchor_service.stage_loan_tracking_start` since plan
    step ``recurrence:R20``, and the dashboard's
    :func:`app.services.loan_anchor_service.record_loan_tracking_start`): the
    operator began tracking an already-amortizing loan and asserts its real
    balance as of a date.  It is loaded as an ordinary
    ``is_opening=False`` balance ASSERTION
    (:func:`app.services.loan_loaders.load_loan_anchor_facts`) that RESETS the
    genesis walk's running balance at its own date -- the loan still opens at its
    origination (step C1), so a date at/after the tracking-start reads this
    asserted balance and a date before it reads the origination opening held flat.

    Like production, the event is RECONCILED INTO POSTINGS in the same
    transaction (:func:`_sync_loan_ledger`): ``record_loan_tracking_start``
    appends the row and re-syncs every scenario (it shares
    ``loan_anchor_service._append_loan_anchor_and_sync`` with the true-up, which then
    commits; this helper leaves the commit to its caller).

    Args:
        loan_params: The :class:`LoanParams` ORM instance, already flushed.
        anchor_balance: The asserted balance (Decimal).
        anchor_date: The date the balance was asserted.

    Returns:
        The newly added :class:`LoanAnchorEvent` (added and reconciled into
        postings, not committed).
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as insert_trueup_event above.
    from app import ref_cache
    from app.enums import LoanAnchorSourceEnum
    from app.extensions import db
    from app.models.loan_anchor_event import LoanAnchorEvent

    event = LoanAnchorEvent(
        account_id=loan_params.account_id,
        anchor_date=anchor_date,
        anchor_balance=anchor_balance,
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.TRACKING_START,
        ),
    )
    db.session.add(event)
    _sync_loan_ledger(loan_params.account_id)
    return event


def create_loan_account(
    seed_user, db_session, name="Test Loan",
    principal=None, rate=None, term=24,
    origination_date=None, payment_day=1,
    *, account_type=None, anchor_balance=None,
    tracked_balance=None, tracked_from=None,
):
    """Create a loan account with LoanParams, origination event, and rate.

    The ONE shared loan-account builder for every DB-backed loan in the suite.
    Routes the account through the canonical ``account_service.create_account``
    factory (so it gets its origination ``AccountAnchorHistory`` row), inserts a
    ``LoanParams`` row, seeds the origination ``RateHistory`` the loan resolver
    requires, records the balance stated at setup as the ``tracking_start``
    assertion the setup door writes (when a caller states one), and OPENS the
    genesis posting ledger -- so a caller never has to repeat that dance, and
    cannot accidentally omit a step.

    Seventeen suites used to hand-roll this block (a ``LoanParams(...)`` insert
    beside a copy of the account-factory call).  Every one of them omitted the
    ledger open, so their loans silently exercised the no-ledger FALLBACK that
    production never takes -- which is exactly how a $3,455.79 divergence between
    the two balance producers stayed invisible to the whole suite.  They differed
    from this factory in only two respects, which are the two knobs below:
    the account TYPE and the anchor PERIOD.  Everything else was duplication.

    **Always OPENS the loan's genesis ledger** (:func:`_sync_loan_ledger`), which
    is what ``loan.create_params`` does in the same transaction as the
    ``LoanParams`` insert (``app/routes/loan/params.py:125``).  There is no
    opt-out: a configured loan without an opening posting is a state production
    cannot produce, and a suite built on it exercises the no-ledger fallback that
    production never takes.  A test that needs that broken loan on purpose builds
    it explicitly with :func:`clear_loan_ledger`.

    The sync runs after the ``RateHistory`` insert because the genesis walk
    resolves the loan's rate periods, and the resolver raises on an empty
    rate-change feed.

    Commits before returning so the loan is fully resolvable.

    **It writes what ``loan.create_params`` writes, and no more** (plan step
    ``recurrence:R20``, ruling **R-R72** part 3).  The setup form asks for the
    balance "as of" a date, and the door appends a ``tracking_start``
    :class:`LoanAnchorEvent` for it in the same transaction as the params
    whenever the loan originated BEFORE that date -- the mid-life-import
    shape, a loan whose balance today is not what amortizing its origination
    would say.  ``tracked_balance`` / ``tracked_from`` are that pair; given,
    the assertion is appended through :func:`insert_tracking_start_event`
    (the row the door writes, reconciled into the ledger the way the door
    does) before the ledger opens.  Omitted, the loan carries only its
    synthesized origination assertion -- the shape of a loan set up on its
    origination day -- and under ruling R-R71 every contractual month after
    origination that a test leaves unrecorded is CHARGED, so a fixture that
    means "a loan in good standing, mid-life" states its balance.  Until R20
    this factory seeded ``LoanParams.current_principal`` to ``principal``, a
    column the door required and nothing read (finding **REC-519**).

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        name: The account name.
        principal: The original principal (and the account anchor).
            Defaults to ``Decimal("1000.00")``.
        rate: The origination annual rate as a Decimal fraction.  Defaults
            to ``Decimal("0.05000")`` (5%).
        term: The loan term in months (default 24).
        origination_date: The loan origination date (default
            ``date(2026, 1, 1)``).
        payment_day: The day-of-month payment day (default 1).
        account_type: The :class:`~app.enums.AcctTypeEnum` member to create the
            account as; defaults to ``AUTO_LOAN``.  Keyword-only.  Resolved to its
            id through :mod:`app.ref_cache` -- the project's ref-table rule is IDs
            for logic, name strings for display only, so this takes the enum and
            never a name string.  Any amortizing type works (``MORTGAGE`` is the
            other one the suite uses).
        (It took an ``anchor_period`` until plan step X-f1c3c.  **It was LIVE,
        not vestigial** -- it reached ``AccountSpec.anchor_period_id``, which
        ``account_service.create_account`` wrote to BOTH
        ``accounts.current_anchor_period_id`` and the origination assertion's
        own ``pay_period_id``.  Rulings R-EH and R-EO deleted both
        destinations, so the parameter had nowhere left to reach.  A loan's
        balance is ledger-derived from dated ``LoanAnchorEvent`` rows
        regardless, which is why no caller loses an assertion by dropping it.
        *A first version of this note said the parameter had "zero loads in its
        body", which was measured AFTER its use had already been deleted in the
        same pass -- a measurement of a state the author had just created, and
        the reason a claims audit caught it.*)
        anchor_balance: The ACCOUNT's anchor balance, when it must differ from the
            loan's ``principal``; defaults to *principal*.  Keyword-only.  These
            are genuinely two different facts, and production can set them apart:
            the account is created first (with a user-entered anchor) and the loan
            params are configured afterwards, through a separate route.  A test
            that needs to prove the loan path is driving a balance -- rather than
            the generic account-anchor path -- makes them differ, so the anchor is
            a distinguishable decoy rather than the same number twice.
        tracked_balance: The balance the owner states at setup (Decimal), to be
            recorded as a ``tracking_start`` dated *tracked_from*.  Keyword-only;
            the two go together.
        tracked_from: The date that balance is stated for.  Must fall strictly
            AFTER *origination_date*, the one case the door writes for; a
            fixture asking for an assertion on or before origination is asking
            for a row the door never writes, and is refused rather than
            silently given no row.

    Returns:
        The created loan :class:`~app.models.account.Account`.  Its
        :class:`LoanParams` row is reachable via :func:`loan_params_for` when a
        caller needs to append an anchor event to it.

    Raises:
        ValueError: When exactly one of *tracked_balance* / *tracked_from* is
            given, or *tracked_from* is not after *origination_date*.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above; these pull the models/services
    # package, which must not load at tests/_test_helpers import time.
    from app import ref_cache
    from app.enums import AcctTypeEnum, InterestAccrualConventionEnum
    from app.models.loan_params import LoanParams
    from app.services import account_service

    if principal is None:
        principal = Decimal("1000.00")
    if rate is None:
        rate = Decimal("0.05000")
    if origination_date is None:
        origination_date = _real_date(2026, 1, 1)
    if account_type is None:
        account_type = AcctTypeEnum.AUTO_LOAN
    if anchor_balance is None:
        anchor_balance = principal
    if (tracked_balance is None) != (tracked_from is None):
        raise ValueError(
            "tracked_balance and tracked_from state one assertion; give both "
            "or neither"
        )
    if tracked_from is not None and tracked_from <= origination_date:
        raise ValueError(
            f"tracked_from {tracked_from} is not after origination "
            f"{origination_date}: the setup door writes no assertion for a "
            "loan originating on or after the stated date"
        )

    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=ref_cache.acct_type_id(account_type),
            name=name,
            anchor_balance=anchor_balance,
            # ``observed_on`` is left to the factory (today).  A loan's balance
            # is ledger-derived from dated ``LoanAnchorEvent`` rows, not from
            # this cash assertion, so the civil-day partition R-DH governs does
            # not reach it -- and the suites that DO care assert their own day.
            # Recorded rather than "fixed": a blanket day-before
            # default here trips the create-time floor for a fixture whose pay
            # periods start today or later.
        ),
    )
    db_session.add(account)
    db_session.flush()
    # **Its books open before the loan ORIGINATES *and* before the owner's
    # calendar** (plan step X-f3c-2b, ruling **R-HG**).  The comment above
    # explains why this factory leaves ``observed_on`` at the wall clock: a
    # loan's BALANCE is ledger-derived from dated ``LoanAnchorEvent`` rows, so
    # the cash assertion's day decides no figure for it.  Its PAYMENTS are
    # ordinary settled rows on the cash side, though, and they run from the
    # origination forward -- which is before the wall clock in every fixture
    # here.
    #
    # Origination alone was measured too tight.  An installment may be settled
    # EARLY, before the loan closes, and the developer ruled that legitimate:
    # ``test_balance_at.test_loan_origination.TestLoanNotYetOriginated`` pays a
    # 2026-05-01 installment on 2026-03-10 against a loan originating
    # 2026-04-15, which is the ONE shape in which the origination guard is the
    # guard doing the work.  So the bound is the earliest of the origination,
    # the assertion and the owner's calendar, which is what the shared helper
    # already computes.
    open_books_before_the_first_assertion(
        db_session, account, also_before=origination_date,
    )

    params = LoanParams(
        account_id=account.id,
        original_principal=principal,
        interest_accrual_convention_id=ref_cache.interest_accrual_convention_id(
            InterestAccrualConventionEnum.MONTHLY_1_12,
        ),
        term_months=term,
        origination_date=origination_date,
        payment_day=payment_day,
    )
    db_session.add(params)
    db_session.flush()
    insert_origination_rate(params, rate)
    if tracked_from is not None:
        # The door's own order: the assertion is staged before the one ledger
        # open, and this helper's re-sync IS that open.
        insert_tracking_start_event(params, tracked_balance, tracked_from)
    else:
        _sync_loan_ledger(account.id)
    db_session.commit()
    return account


def loan_params_for(db_session, account_id):
    """Return a loan account's :class:`LoanParams` row.

    :func:`create_loan_account` returns the ACCOUNT, but the anchor helpers
    (:func:`insert_trueup_event`, :func:`insert_tracking_start_event`) take its
    params, so callers were each re-writing the same one-line query.  Holds it in
    one place.

    Args:
        db_session: The test ``db.session``.
        account_id: The loan account whose params to load.

    Returns:
        The loan's :class:`~app.models.loan_params.LoanParams` row.

    Raises:
        NoResultFound: If the account has no ``LoanParams`` (not a configured
            loan) -- a fixture bug, surfaced rather than papered over with None.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.models.loan_params import LoanParams

    return db_session.query(LoanParams).filter_by(account_id=account_id).one()


def add_escrow_line(
    db_session, account_id, name, annual_amount, *,
    effective_date=None, inflation_rate=None,
):
    """Create an escrow LINE with one opening version (supersession model).

    The shared escrow-setup builder for tests that exercise app paths reading
    escrow (the loan-payment split, PITI, savings-dashboard PITI, the loan-card
    breakdown).  Inserts the ``EscrowLine`` + ``EscrowComponentVersion`` pair the
    app reads (the supersession escrow model).  When
    ``effective_date`` is omitted it defaults to the loan's ``origination_date``,
    so the escrow is active for the whole loan life (the standing-charge case
    every escrow test intends); pass an explicit date to build a version that
    supersedes an earlier one.  Flushes so ids are assigned; the caller commits.

    Args:
        db_session: The test ``db.session``.
        account_id: The loan account the escrow belongs to.
        name: The escrow line display name.
        annual_amount: The opening version's stored annual amount (Decimal).
        effective_date: The version's effective date; defaults to the loan's
            origination date when omitted.
        inflation_rate: Optional decimal-fraction inflation rate for the version.

    Returns:
        The created :class:`~app.models.escrow_line.EscrowComponentVersion` (its
        ``line`` relationship carries the parent line), so a caller can mutate
        ``annual_amount`` to exercise a live escrow change.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.models.escrow_line import EscrowComponentVersion, EscrowLine
    from app.models.loan_params import LoanParams

    if effective_date is None:
        params = (
            db_session.query(LoanParams).filter_by(account_id=account_id).one()
        )
        effective_date = params.origination_date

    line = EscrowLine(account_id=account_id, name=name)
    db_session.add(line)
    db_session.flush()
    version = EscrowComponentVersion(
        line_id=line.id, effective_date=effective_date,
        annual_amount=annual_amount, inflation_rate=inflation_rate,
    )
    db_session.add(version)
    db_session.flush()
    return version


def create_loan_with_trueup(
    seed_user, db_session, *, origination_principal, anchor_balance,
    anchor_date, rate, origination_date, name="Split Loan", term=360,
    payment_day=1, escrow_annual=None, account_type=None,
):
    """Create an amortizing loan carrying an origination AND a user-trueup anchor.

    The shared "resolvable loan with a controlled latest anchor" builder for the
    Build-Order Step 4 split-service and wiring suites (a duplicate-code finding
    otherwise): routes through :func:`create_loan_account` (origination anchor +
    rate), appends a ``user_trueup`` :class:`LoanAnchorEvent` at
    *anchor_balance* / *anchor_date* -- the LATEST event, so both the split walk
    and the resolver seed their running balance from it -- and an optional
    escrow component effective from origination (so every post-anchor payment's
    date falls in its active range).  Commits, so the loan is fully resolvable.

    Keeping ``origination_principal`` distinct from ``anchor_balance`` lets a
    caller prove the split seeds from the trueup anchor, not origination.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        origination_principal: The original principal (the origination anchor).
        anchor_balance: The user-trueup balance the split walk seeds from.
        anchor_date: The user-trueup anchor date (the post-anchor lower bound).
        rate: The origination annual rate as a Decimal fraction.
        origination_date: The loan origination date.
        name: The account name (default ``"Split Loan"``).
        term: The loan term in months (default 360).
        payment_day: The day-of-month payment day (default 1).
        escrow_annual: Optional annual escrow amount (Decimal); when given, one
            escrow component effective from ``origination_date`` is added.
        account_type: The :class:`~app.enums.AcctTypeEnum` member to create the
            account as; defaults to :func:`create_loan_account`'s own default
            (``AUTO_LOAN``).  Pass ``MORTGAGE`` when the test's assertions depend
            on the loan's KIND rather than just its amortization -- Schedule A's
            mortgage-interest deduction is the case that does, and a fixture that
            takes the default silently pins an auto loan's interest into a
            home-mortgage figure.

    Returns:
        The created loan :class:`~app.models.account.Account`.
    """
    # Pylint: ``import-outside-toplevel`` -- same circular-dep avoidance as the
    # loan helpers above; these pull the models/services package, which must not
    # load at tests/_test_helpers import time.
    from app.models.loan_params import LoanParams  # pylint: disable=import-outside-toplevel

    loan = create_loan_account(
        seed_user, db_session, name=name, principal=origination_principal,
        rate=rate, term=term, origination_date=origination_date,
        payment_day=payment_day, account_type=account_type,
    )
    params = (
        db_session.query(LoanParams).filter_by(account_id=loan.id).one()
    )
    insert_trueup_event(params, anchor_balance, anchor_date)
    if escrow_annual is not None:
        add_escrow_line(
            db_session, loan.id, "Tax & Insurance", escrow_annual,
            effective_date=origination_date,
        )
    db_session.commit()
    return loan
