"""Balance assertions and openings: an assertion's instant and day, an anchor's history and
override, a balance re-asserted, an account's opening, and the pay-period invariants.
"""

from contextlib import contextmanager
from datetime import date as _real_date, datetime as _real_datetime, timedelta as _real_timedelta
from decimal import Decimal

from sqlalchemy import exc as sa_exc


def require_assertion_instant(at):
    """Return *at*, refusing a plain ``date`` where an INSTANT is required.

    **The mirror of the settle column's type guard, and it exists because the
    X-f1 conversion tripped over exactly this** (finding **N-179**'s other
    direction).  A settle records a civil DAY; an ASSERTION records the instant
    it was typed (``AccountAnchorHistory.created_at``, a ``timestamptz``) and
    DERIVES its business day from it.  A bulk conversion that turned settle
    instants into days caught five assertion sites in the same pass, and each
    then failed deep inside ``to_display_tz`` with ``'date' object has no
    attribute 'tzinfo'`` -- or, had it reached the column, would have been
    stored as MIDNIGHT UTC, which is the previous Eastern evening and therefore
    the previous business day.

    Every assertion builder in this module routes through it, so handing one a
    day fails at the call site with a message that says what to pass.

    Args:
        at: The candidate recording instant.

    Returns:
        *at* unchanged when it is a ``datetime``.

    Raises:
        TypeError: When *at* is a ``date`` that is not a ``datetime``.
    """
    if isinstance(at, _real_date) and not isinstance(at, _real_datetime):
        raise TypeError(
            f"An assertion is pinned by the INSTANT it was recorded at, got "
            f"the civil day {at!r}.  Its business day (``observed_on``) is "
            "DERIVED from that instant in the display timezone, and a bare "
            "date reaches the timestamptz column as midnight UTC -- the "
            "PREVIOUS Eastern evening, so the assertion would be filed a day "
            "early.  Pass an aware-UTC datetime: the suite's own "
            "``_instant(...)`` helper, or ``settle_instant_on(day)``."
        )
    return at


def observed_day_of(instant):
    """Return the civil day an assertion stamped at ``instant`` is ABOUT.

    The ONE place the test helpers state how a pinned recording instant maps to
    an ``AccountAnchorHistory.observed_on``, and every anchor builder below
    routes through it.

    **Why the helpers derive it rather than take it.**  ``observed_on`` is a
    stored, user-supplied column since plan step 2 (ruling R-DH), so a fixture
    CAN set the two independently -- and a handful deliberately should, because
    "the user corrected an anchor's date" is now a reachable state.  But every
    fixture written before the column existed meant "an assertion recorded at
    instant X, about the day X falls on", and each of their hand-computed
    figures depends on that.  Deriving keeps all of them valid unchanged;
    a fixture that wants the two APART says so explicitly, which is exactly the
    right way round for a state that used to be unreachable.

    It is the display-timezone day, not the UTC one (ruling R-DH (b)): midnight
    UTC is the previous EVENING in Eastern, and a fixture that took the UTC day
    would file a late-evening assertion in the next pay period -- the N-132
    shape this suite has now been bitten by five times.

    Args:
        instant: The aware-UTC instant the assertion is stamped at.

    Returns:
        The :class:`datetime.date` the assertion is the closing balance for.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers in ``_loans.py``.
    from app.utils.dates import to_display_date

    return to_display_date(instant)


def add_anchor_history(db_session, account, balance, days_ago=0):
    """Append an :class:`AccountAnchorHistory` row ``days_ago`` before now.

    The shared anchor-history builder for the dashboard route and
    pulse-producer suites (both built an identical ``_add_anchor_history``
    -- a duplicate-code finding).  The ``created_at`` is set to
    ``datetime.now(UTC) - days_ago``; under ``freeze_today`` the patched
    ``datetime.now`` returns the frozen today, so a positive ``days_ago``
    is that many days before the frozen reference.  Flushes; the caller
    commits.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` the anchor
            belongs to.
        balance: The anchor balance (str or Decimal-coercible).
        days_ago: How many days before now to date the row (default 0).

    Returns:
        The created :class:`AccountAnchorHistory` row (flushed).
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers in ``_loans.py``.
    from datetime import datetime, timedelta, timezone
    from app.models.account import AccountAnchorHistory

    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    entry = AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=Decimal(str(balance)),
        created_at=created,
        observed_on=observed_day_of(created),
        # The entered day is the pinned instant's, not today's (**N-299**):
        # the column default is a plain ``display_today`` and a historical row
        # must say what it means rather than inherit the wall clock.
        recorded_on=observed_day_of(created),
    )
    db_session.add(entry)
    db_session.flush()
    return entry


def override_anchor(db_session, account, period, balance, *, at=None):
    """Replace ``account``'s current anchor with ``balance`` on ``period``.

    Appends an :class:`AccountAnchorHistory` row -- the source of truth, and
    since ruling R-EH the ONLY place an asserted balance lives.  It used to
    sync the ``accounts.current_anchor_*`` cache columns alongside, so the
    resolver's cache-reconciliation log would not fire; there is no cache and
    no such log.  The shared form of the
    ``_override_anchor`` helper five suites had each written for themselves --
    a duplicate-code cluster that mattered once the fold made the row's INSTANT
    load-bearing, because a fix applied to one copy would have left the other
    four asserting against a state production cannot reach.

    **The instant defaults to the period's first day IN THE USER'S ZONE**, and
    that default is the point (plan step X-c2b2, the N-8 / X-c2a fixture shape a
    third time; the zone is ruling R-DH (b), 2026-07-31).
    ``AccountAnchorHistory.created_at`` server-defaults to the WALL CLOCK,
    while ``tests/test_services`` freezes today to 2026-03-20 -- so a row
    written by a helper that did not stamp it was asserted MONTHS after its own
    pay period, a state production cannot reach (a true-up files against
    ``get_current_period``).  The shipping producers never noticed: they read
    the LATEST row and ignored its date.  The fold replays every assertion on
    the day it was made, so such a row lands past the end of the window and no
    period ever sees the balance the test asserted.  Pinning it to the period's
    own start makes the fixture deterministic, clock-independent, and shaped
    like a true-up filed on day one of its period -- which keeps every
    hand-computed figure in the suites valid unchanged.

    A test that needs a MID-period assertion (the shape ruling R-L and the
    walk's instant partition exist for) passes ``at`` explicitly.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` to re-anchor.
        period: The :class:`~app.models.pay_period.PayPeriod` the new anchor is
            recorded against; its ``start_date`` is the default instant.
        balance: The new anchor balance (Decimal -- construct from a string).
        at: Optional aware-UTC instant to stamp the row with, overriding the
            period-start default.

    **It took a required ``notes`` label until ruling R-ES** (plan step
    X-f1e2), so a failing suite could tell which fixture wrote the assertion it
    was looking at.  The column is deleted -- nothing in ``app/`` read it -- and
    the diagnostic it served is answered better by the row's own ``id``,
    ``observed_on`` and ``created_at``, which the fixture controls and the
    engine actually orders on.

    Returns:
        The created :class:`AccountAnchorHistory` row (flushed, not committed
        -- the caller owns the transaction boundary).
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from datetime import datetime, time, timezone
    from app.models.account import AccountAnchorHistory
    from app.utils.dates import DISPLAY_TIMEZONE

    # Day one of the period IN THE USER'S ZONE, converted to UTC for storage.
    # Midnight UTC is the previous EVENING in Eastern, so since ruling R-DH (b)
    # -- which reads an instant's display civil day -- it would file this
    # true-up in the PREVIOUS period and empty the anchor column's remainder of
    # the very assertion the fixture just made.
    asserted_at = at if at is not None else datetime.combine(
        period.start_date, time.min, tzinfo=DISPLAY_TIMEZONE,
    ).astimezone(timezone.utc)
    history = AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=balance,
        created_at=asserted_at,
        observed_on=observed_day_of(asserted_at),
        # The entered day is the pinned instant's (**N-299**), as above.
        recorded_on=observed_day_of(asserted_at),
    )
    db_session.add(history)
    db_session.flush()
    return history


def reassert_balance_on(db_session, account, at):
    """Assert *account*'s balance AGAIN, at the instant ``at``.

    **The fixture act that replaced three re-stamping helpers** (plan step
    X-f3c-2c).  ``restamp_opening_assertion`` / ``restamp_latest_assertion``
    UPDATEd a stored :class:`~app.models.account.AccountAnchorHistory` row to
    move its business day and its recording instant onto a day the test had
    chosen.  ``budget.account_anchor_history`` is append-only, so no such act
    exists: an assertion records what a bank said on a day and the only way to
    say something else is to say it again.  This helper is that -- one more
    assertion, carrying the balance that governs unless the caller names
    another, dated on ``at``'s civil day.

    **Why the state it builds is production's own and not a fixture's.**  An
    owner who reconciles twice files two assertions; production's Checking
    account carries 2-3 on each of three days.  The two rows this leaves --
    the origination one ``account_service.create_account`` wrote and this one --
    are exactly what "I opened the account, and later I confirmed the balance
    again" looks like, and the balance every producer reads on every day at or
    after ``at`` is the same figure the re-stamp used to produce, because
    nothing is dated between them.

    **What it does NOT do is move the books.**  ``budget.account_openings`` is
    the account's own opening record and this writes no row there; a caller
    that needs the books earlier than they stand calls
    :func:`open_books_before_the_first_assertion`, which is backward-only and
    says so.  The re-stamp helpers restated the opening as a side effect, which
    made "place this assertion" and "move the books" one act that no door
    performs together.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` asserting.
        at: The aware-UTC instant to record the assertion at.  Its display
            civil day becomes ``observed_on`` -- the day the balance was TRUE
            -- and the instant itself becomes ``created_at``, which orders two
            assertions that share a day.

    Returns:
        The appended :class:`AccountAnchorHistory` row (flushed).

    Raises:
        AssertionError: When *account* carries no assertion to repeat.
            Reachable -- :func:`account_never_asserted` builds exactly such an
            account -- and raised rather than defaulted because a fabricated
            balance is a figure no test author asked for.

    **It took a ``balance`` argument until the adversarial review of this
    step**, so a caller could assert a DIFFERENT figure.  Not one of the 21
    call sites passed one: every caller means "say the same thing again on
    another day", which is the whole act.  A test that wants a different figure
    has :func:`append_balance_assertion`, which takes one and always did.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers in ``_loans.py``.
    from app.models.account import AccountAnchorHistory

    require_assertion_instant(at)
    # FLUSHES first rather than relying on autoflush: a caller that has just
    # staged an assertion in the session would otherwise read the one before
    # it, and a fixture quietly repeating the wrong balance is worse than one
    # that fails.
    db_session.flush()
    # ``.first()`` and not ``.scalar()``: an account may already carry several
    # assertions, and ``Query.scalar`` is ``one()`` underneath -- it raises
    # ``MultipleResultsFound`` on exactly the accounts this helper exists to
    # add one more to.
    governing = (
        db_session.query(AccountAnchorHistory.anchor_balance)
        .filter_by(account_id=account.id)
        .order_by(
            AccountAnchorHistory.observed_on.desc(),
            AccountAnchorHistory.created_at.desc(),
            AccountAnchorHistory.id.desc(),
        )
        .first()
    )
    assert governing is not None, (
        f"account {account.id} carries no assertion to repeat; use "
        "append_balance_assertion to state one"
    )
    balance = governing[0]
    row = AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=balance,
        created_at=at,
        observed_on=observed_day_of(at),
        # The entered day is the pinned instant's, never the wall clock
        # (finding **N-299**): a historical row must say what it means rather
        # than inherit ``display_today()`` from the column default.
        recorded_on=observed_day_of(at),
    )
    db_session.add(row)
    db_session.flush()
    return row


@contextmanager
def append_only_guard_lifted(db_session, table):
    """Lift every append-only arm on *table* for the block's duration.

    **This exists to grade the control UNDERNEATH, and nothing else.**  Since
    plan step X-f3c-2c, ``budget.refuse_append_only_change`` refuses every
    UPDATE, every DELETE and (since X-f3c-2d) every TRUNCATE on the three
    account-history tables, whoever asks --
    which is what makes the rule true, and which also means it SHADOWS the
    controls it sits on top of.  Three of those still matter and would
    otherwise go ungraded:

    * ``fk_transactions_reconciled_by``'s ``ON DELETE RESTRICT``, which refuses
      to un-clear a line by removing the statement it names (ruling **R-FL**);
    * ``ck_books_open_before_movements``' UPDATE and DELETE arms, which exist
      precisely for "a raw ``UPDATE`` moving the governing row's ``opened_on``
      forward, and a raw ``DELETE`` of the governing row" (plan step X-f3c-2b);
    * the posted-only reversal branch of ``account_posting_service``, defensive
      against a history row that vanished from under its journal entry.

    A control nobody can reach is a control nobody can trust, and deleting
    those three because a newer guard happens to stand in front of them would
    trade a measured refusal for an argument.  So a case that grades one lifts
    the outer guard for exactly its own statement and says so.

    **It is not an escape hatch for building fixtures.**  A fixture that wants
    an account with no assertion has :func:`account_never_asserted`; one that
    wants a different asserted day has :func:`reassert_balance_on`.  Neither
    needs this, and both were written so that neither would.

    Restores the trigger in a ``finally``, so a failing assertion inside the
    block cannot leave the rest of the test's transaction unguarded.

    Args:
        db_session: The test ``db.session``.
        table: The schema-qualified table, e.g.
            ``"budget.account_anchor_history"``.  Must be one of
            :data:`app.append_only_infrastructure.APPEND_ONLY_TABLES`; a typo
            would otherwise lift nothing and the case would grade the outer
            guard while claiming to grade the inner one.

    Yields:
        ``None``.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers in ``_loans.py``.
    from app.append_only_infrastructure import (
        APPEND_ONLY_TABLES,
        APPEND_ONLY_TRIGGERS,
    )
    from app.extensions import db

    assert table in APPEND_ONLY_TABLES, (
        f"{table!r} carries no append-only trigger to lift; "
        f"expected one of {APPEND_ONLY_TABLES}"
    )
    # EVERY arm, not just the update one: since X-f3c-2d the guard is three
    # triggers with three timings, and lifting one would leave a case that
    # means to reach the control underneath still refused by another arm.
    enable = [
        db.text(f"ALTER TABLE {table} ENABLE TRIGGER {name}")
        for name in APPEND_ONLY_TRIGGERS
    ]
    # Disabling FIRST is load-bearing rather than tidy: the delete arm is a
    # deferred constraint trigger, so a transaction that has already deleted
    # from this table holds pending trigger events and PostgreSQL then refuses
    # ``ALTER TABLE`` on it outright.
    for name in APPEND_ONLY_TRIGGERS:
        db_session.execute(db.text(
            f"ALTER TABLE {table} DISABLE TRIGGER {name}"
        ))
    try:
        yield
    finally:
        try:
            for statement in enable:
                db_session.execute(statement)
        except (sa_exc.InvalidRequestError, sa_exc.DBAPIError):
            # A case that expects the INNER control to refuse leaves no way to
            # emit further SQL, in one of two shapes: SQLAlchemy refuses
            # ('prepared' after a failed COMMIT, pending-rollback after a
            # failed flush), or PostgreSQL does (``InFailedSqlTransaction``
            # after a constraint aborted the transaction).  Both are caught by
            # name rather than broadly.  Rolling back is what such a case does
            # next anyway; doing it here is what makes the guard come back
            # whether the block passed or raised, which a bare re-enable would
            # not.
            db_session.rollback()
            for statement in enable:
                db_session.execute(statement)


def account_never_asserted(
    seed_user, db_session, name="Unasserted", type_name="Checking",
    opening_equity=None,
):
    """Build an account the assertion factory never touched.

    **The only honest way to reach "this account has asserted nothing" since
    plan step X-f3c-2c**, and the reason is that the state is genuinely
    unreachable through a door.  ``account_service.create_account`` writes an
    origination assertion and CHECKS that it landed -- it is the E-19 / CRIT-01
    invariant -- and ``budget.account_anchor_history`` is append-only at the
    database tier, so nothing may delete that row while the account stands.
    Every suite that needs the state used to reach it by creating an account
    and then deleting its assertions; that act does not exist.

    So this builds the row directly and stops there.  What it produces is an
    account that was never created, which is exactly the premise: the branches
    it reaches -- ``cash_ledger.resolve_anchor``'s ``RuntimeError``,
    ``_asset_fold``'s fail-loud window, ``balance_at.cash_anchor_history``'s
    empty log, ``integrity_check``'s BA-01 -- are the ones that exist because a
    reader must not fabricate a level for an account whose books it cannot
    read.  They are defensive arms for an impossible state, and a fixture that
    could not build one would leave every one of them ungraded.

    **It PAIRS the ledger account**, because that pairing is not part of what
    the fixture is withholding: a posted-ledger reader asked about an account
    with no chart row raises for a different reason, and a case meaning "no
    assertion" would then be graded by the wrong refusal.

    Args:
        seed_user: The ``seed_user`` fixture dict, for the owner.
        db_session: The test ``db.session``.
        name: The account name, unique per owner.
        type_name: The ``ref.account_types`` name.
        opening_equity: When given, an :class:`AccountOpening` row is written
            for the day before today carrying this equity -- for a case whose
            subject is the ASSERTION's absence and which needs the books to be
            readable.  ``None``, the default, withholds that too, which is what
            a case about a missing OPENING record wants.

    Returns:
        The created :class:`~app.models.account.Account`, flushed.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers in ``_loans.py``.
    from datetime import timedelta as _td

    from app import ref_cache
    from app.enums import AccountOpeningSourceEnum
    from app.models.account import Account
    from app.models.account_opening import AccountOpening
    from app.models.ref import AccountType
    from app.services import ledger_account_service
    from app.utils.dates import display_today

    account_type = (
        db_session.query(AccountType).filter_by(name=type_name).one()
    )
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=account_type.id,
        name=name,
    )
    db_session.add(account)
    db_session.flush()
    if opening_equity is not None:
        db_session.add(AccountOpening(
            account_id=account.id,
            opened_on=display_today() - _td(days=1),
            opening_equity=opening_equity,
            source_id=ref_cache.account_opening_source_id(
                AccountOpeningSourceEnum.USER_DECLARED,
            ),
        ))
    ledger_account_service.create_ledger_account_for_account(account)
    db_session.flush()
    return account


def open_books_before_the_first_assertion(
    db_session, account, also_before=None,
):
    """Open *account*'s books before ANY day a fixture could date a row on.

    **The factory shape plan step X-f3c-2b requires** (ruling **R-HG**).
    ``account_service.create_account`` writes the opening record and the
    origination assertion on ONE day, which is right in production -- the owner
    types one balance and it is both -- and it means no movement may be dated on
    or before that day, because an opening equity is the CLOSING balance for its
    own day.  A fixture that then settles a row on or before the account's own
    creation day is building a state the app refuses, and on the frozen suite
    clock that is the ordinary case: ``create_account``'s default ``observed_on``
    and the settle door's default day are the SAME ``display_today()``.

    **It bounds on every day a row could land on and takes the earliest,
    because "the day before the assertion" was measured too tight.**  Route
    suites routinely settle a row days BEFORE the account was created -- a
    correction to money that moved last week -- so an opening one day back
    still refuses them.  The bounds:

    * the account's earliest ASSERTION, which is where ``create_account`` put
      the books;
    * the owner's earliest PAY PERIOD, which is the floor the settle door itself
      applies (``pay_period_service.earliest_recordable_day``, ruling R-EL), so
      nothing a door accepts can precede it;
    * the earliest day the account ALREADY records money moving, so a helper
      called after the rows exist cannot strand them;
    **A matched BANK LINE bounds the books too and is deliberately NOT a term
    here, because it cannot change the answer** (plan step
    balance:X-f3c-2b-2b).  It was added as one and then removed on a proof
    rather than on taste: this helper only ever moves the books BACKWARD (it
    clamps to the standing opening), and the database constraint already
    guarantees the standing opening sits strictly below every line the account
    has matched.  So ``min(matched) - 1`` is never smaller than the clamp, and
    the term is dead in every state the boundary permits.  A defensive term
    that cannot fire is the *born dead* shape ``lessons.md`` names, and it
    would have read as protection nobody had.
    * *also_before*, when the caller knows a day of its own -- a loan's
      ORIGINATION is the one that exists, because its payment schedule runs
      from there and the owner's calendar may start later.

    One day before the earliest of those is the latest day that leaves every
    recordable day recordable.  It is still a real production shape -- books
    that opened before the budget did is exactly what finding **N-368**'s import
    will create for the developer's own Checking account -- and it moves NO
    figure in a fixture: the origination assertion still clears whatever settled
    on its own day, so every correction is what it was.

    **BACKWARD only, and that is a rule rather than an accident.**  The books
    never move forward here, so calling this twice, or calling it on an account
    some other helper already opened earlier, cannot strand a row that was
    legal a moment ago.  The seed owner's calendar reset
    (``_reset_seed_calendar``, in ``tests/_seed_owner_fixtures.py``) learned
    the same rule the same way one table over, and plan step
    ``pay_calendar:C4-b-1`` then measured that its backward-only restatement
    could not move a day in any world the suite's fixtures build and deleted
    it; what enforces the rule there now is a refusal, not a restatement.

    A no-op for an account carrying no assertion, and equally for one carrying
    no opening ROW -- both production-unreachable, both states a raw-model
    fixture can build.  Each answers ``None`` rather than a day nothing wrote.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` whose books to move.
        also_before: An extra civil day the books must precede, or ``None``.

    Returns:
        The civil day the books now open on, or ``None`` when the account
        carries no assertion to place them before.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep avoidance
    # as the loan helpers in ``_loans.py``.
    from app.extensions import db
    from app.models.account import Account, AccountAnchorHistory
    from app.models.pay_period import PayPeriod
    from app.models.transaction import Transaction
    from app.models.transaction_entry import TransactionEntry
    from app.services import account_posting_service

    db_session.flush()
    earliest_assertion = (
        db_session.query(db.func.min(AccountAnchorHistory.observed_on))
        .filter(AccountAnchorHistory.account_id == account.id)
        .scalar()
    )
    if earliest_assertion is None:
        return None
    owner_id = db_session.query(Account.user_id).filter(
        Account.id == account.id,
    ).scalar()
    candidates = [earliest_assertion]
    for value in (
        also_before,
        db_session.query(db.func.min(PayPeriod.start_date))
        .filter(PayPeriod.user_id == owner_id).scalar(),
        db_session.query(db.func.min(Transaction.settled_on))
        .filter(Transaction.account_id == account.id).scalar(),
        db_session.query(db.func.min(TransactionEntry.settled_on))
        .filter(TransactionEntry.account_id == account.id).scalar(),
    ):
        if value is not None:
            candidates.append(value)
    # Clamped against the books as they STAND, so this only ever moves them
    # backward -- see the docstring's rule.  ``_governing_opening_day`` answers
    # ``date.max`` for an account carrying no opening row, which makes the
    # clamp a no-op there rather than a special case.
    standing = _governing_opening_day(db_session, account)
    if standing == _real_date.max:
        # No opening ROW at all, so there is nothing to restate and nothing to
        # return: ``restate_account_opening`` carries the equity forward from
        # the governing row and has none to read.  Answering the COMPUTED day
        # here would name a day the books do not open on, which is worse than
        # answering nothing -- a raw-model fixture can build this state, and
        # the docstring's contract is the day the books NOW open.
        return None
    opened_on = min(min(candidates) - _real_timedelta(days=1), standing)
    if opened_on == standing:
        return opened_on
    restate_account_opening(db_session, account, opened_on)
    # **The posted ledger follows the books** (plan step X-f3c-2b).
    # ``create_account`` has already booked the ``account_opening`` journal
    # entry keyed on the day it asserted, so a restatement afterwards leaves
    # that entry on a day the books no longer open -- the stale-key-plus-
    # reversal shape ``build_seed_user``'s own comment warns about
    # (``tests/_seed_owner_fixtures.py``).
    # The reconcile is idempotent and self-healing, and re-running it is what
    # PRODUCTION does after the same restatement (the deploy's
    # ``backfill_all_account_anchor_postings``), so the fixture takes the same
    # path rather than a shortcut.
    account_posting_service.sync_account_anchor_postings_all_scenarios(
        account.id,
    )
    return opened_on


def governing_opening_row(db_session, account):
    """Return the ``budget.account_openings`` row that GOVERNS *account*.

    **The one place the test tree spells "which restatement is in force"**
    (plan step X-f3c-2b).  The table is append-only and the latest RECORDING
    instant governs (ruling **R-HE**), ``id`` breaking a same-instant tie --
    the same order :func:`app.services.cash_ledger.account_opening_fact` reads
    in Python and ``budget.account_books_opened_on`` reads in SQL, and
    ``TestTheTwoGoverningLookupsElectTheSameRow`` in
    ``tests/test_services/test_books_boundary.py`` is what holds all three to
    it.

    It answers with the ROW rather than through
    :func:`~app.services.cash_ledger.account_opening_fact` because that loader
    RAISES on an account carrying no opening, and the fixture helpers here have
    to tolerate one: a raw-model fixture can build an account the canonical
    factory never touched.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` to read.

    Returns:
        The governing :class:`~app.models.account_opening.AccountOpening`, or
        ``None`` when the account carries none.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep avoidance
    # as the loan helpers in ``_loans.py``.
    from app.models.account_opening import AccountOpening

    return (
        db_session.query(AccountOpening)
        .filter_by(account_id=account.id)
        .order_by(AccountOpening.created_at.desc(), AccountOpening.id.desc())
        .first()
    )


def _governing_opening_day(db_session, account):
    """Return the civil day *account*'s books currently open on.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` to read.

    Returns:
        The governing row's ``opened_on``, or ``date.max`` when the account
        carries none -- a value that makes a ``min()`` against it a no-op,
        which is what a caller comparing against "the books as they stand"
        means for an account with no books yet.
    """
    governing = governing_opening_row(db_session, account)
    return _real_date.max if governing is None else governing.opened_on


def restate_account_opening(db_session, account, opened_on):
    """Append an opening record restating WHEN the account's books opened.

    ``budget.account_openings`` is append-only and the latest recorded row
    governs, so moving the day means inserting a row rather than updating one.
    The EQUITY is carried forward unchanged: a restatement places the opening
    in time and says nothing about how much the books opened with.

    **It writes the row DIRECTLY rather than going through a service, and that
    is the point** (plan step X-f3c-2b).  ``account_service.create_account``
    bounds its ``observed_on`` at ``pay_period_service.earliest_recordable_day``
    -- the calendar's own first day -- so a factory cannot ask it to open books
    before the calendar starts, which is exactly where a fixture needs them.
    The floor is a rule about ASSERTIONS (ruling R-ER), not about openings, and
    production's own restatement is written by migration ``d3b6f1c8a274`` the
    same unbounded way.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` whose books to
            re-date.
        opened_on: The civil day the books now open on.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep avoidance
    # as the loan helpers in ``_loans.py``.
    from app.models.account_opening import AccountOpening

    governing = governing_opening_row(db_session, account)
    if governing is None or governing.opened_on == opened_on:
        return
    db_session.add(AccountOpening(
        account_id=account.id,
        opened_on=opened_on,
        opening_equity=governing.opening_equity,
        source_id=governing.source_id,
    ))
    db_session.flush()


def append_balance_assertion(
    db_session, account, balance, at, recorded_at=None,
):
    """Append one balance ASSERTION (a true-up) at a pinned instant.

    The instant-precise true-up builder the cash-ledger suites share.  See
    :func:`reassert_balance_on` for why the instant (not the day) is the
    thing being pinned.

    **Every column is stated at INSERT** (plan step X-f3c-2c).  It used to
    insert and then re-stamp, on the ground that ``created_at`` carries a
    server default the INSERT would otherwise fill with the wall clock -- which
    is not so: a value supplied to the constructor appears in the INSERT and no
    server default is reached, which is what ``override_anchor`` and
    ``add_anchor_history`` beside it have always relied on.  The table is
    append-only, so the re-stamp had to go; measuring the reason first is what
    made it cost no caller anything.

    **It takes no pay period, because an assertion is filed under none** (ruling
    R-EO deleted ``pay_period_id`` from the row).  It carried a ``period``
    parameter its body never read until 2026-09-03, and 43 call sites
    computed one to pass it -- the shape ruling R-EH deleted from
    ``resolve_anchor``'s ``scenario_id``: a parameter that scopes nothing tells
    its caller the row is filed under something (finding N-393).

    **The row carries TWO clocks and this helper can now separate them**
    (*recorded_at*).  ``observed_on`` is the BUSINESS day the balance was true
    for and ``created_at`` is when it was typed; since plan step 2 made
    ``observed_on`` user-supplied they can disagree, which is precisely the
    shape the retired third statement of "the latest assertion" got wrong
    (finding N-133 / F4).  Defaulting *recorded_at* to *at* keeps every
    existing caller's two clocks equal -- and an adversarial review found that
    default silently disarming the one test written to exercise the
    disagreement, which is why the parameter exists.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` asserting.
        balance: The asserted balance (str or Decimal-coercible).
        at: The aware-UTC instant whose display-timezone day becomes the
            BUSINESS day (``observed_on``).
        recorded_at: The aware-UTC instant to stamp as ``created_at`` -- when
            the row was TYPED.  Defaults to *at*, which makes the two clocks
            agree; pass it to build a back-dated assertion, where the business
            day precedes the recording instant.

    Returns:
        The inserted :class:`AccountAnchorHistory` row (flushed).
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers in ``_loans.py``.
    from app.models.account import AccountAnchorHistory

    require_assertion_instant(at)
    require_assertion_instant(at if recorded_at is None else recorded_at)
    typed_at = at if recorded_at is None else recorded_at
    row = AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=Decimal(str(balance)),
        observed_on=observed_day_of(at),
        created_at=typed_at,
        recorded_on=observed_day_of(typed_at),
    )
    db_session.add(row)
    db_session.flush()
    return row


def assert_pay_period_invariants(db_session, user_id):
    """Assert a user's pay-period structure is not corrupt (Discipline 1).

    The single source of truth for "this user's period structure is
    sound," called after EVERY pay-period mutation test (extend /
    truncate / regenerate / top-up / reset).  A pay period is the spine
    of every financial number in Shekel, and period corruption produces
    a silently wrong balance rather than an error, so this helper exists
    to make that corruption impossible to ship undetected.  See
    ``docs/plans/implementation_plan_pay_period_crud.md`` (Test plan,
    Discipline 1).

    Raises ``AssertionError`` (with a diagnostic) on the first violated
    invariant:

      1. Every account carries at least one balance ASSERTION, so a producer
         can resolve a balance for it.
      2. Every transfer is in one of the user's (still-existing) periods.
      3. No transaction references a pay period that no longer exists.

    **FOUR invariants were DELETED at plan step ``pay_calendar:C4-c``, and
    none of them was replaced.**  They asserted that ``period_index`` was
    unique per user, that ordinal order matched payday order on BOTH
    ``start_date`` and ``end_date``, that the ordinals were contiguous, and
    that no two periods' spans overlapped -- the same functional dependency
    ``integrity_check`` BA-03 / BA-04 policed in weekly SQL and
    ``recurrence._calendar.PeriodCalendar.__post_init__`` policed at the value
    boundary.  All four read columns that no longer exist: a period's ordinal
    is now its position in payday order and its end is the day before the next
    payday, so a duplicate ordinal, an ordinal gap, an order disagreement and
    an overlap are not states this database can hold.  ``uq_pay_periods_user_
    start`` is what remains, and it is a KEY rather than an assertion.

    *This helper carried the WRITE DOOR's exact blind spot while it existed*
    (finding **P5**): it asserted ``cur.start_date > prev.end_date`` and
    nothing about contiguity, so no case in this suite could have failed on a
    gapped write -- which is why finding **P2** was found by reading rather
    than by the suite.  Deleting the four is not a loss of that coverage; it is
    the removal of the state they were the wrong instrument for.

    Args:
        db_session: The test ``db.session``.
        user_id: The user whose pay-period structure to validate.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention); load
    # the models lazily, the same way every helper above does.
    # pylint: disable=import-outside-toplevel
    from app.models.account import Account, AccountAnchorHistory
    from app.models.pay_period import PayPeriod
    from app.models.transaction import Transaction
    from app.models.transfer import Transfer

    period_ids = {
        row.id for row in
        db_session.query(PayPeriod.id).filter_by(user_id=user_id)
    }

    # 1. Anchor integrity: every account carries at least one balance
    #    ASSERTION (E-19 / Commit 3).  It used to assert that the account's
    #    ``current_anchor_period_id`` named one of the user's live periods;
    #    rulings R-EH and R-EO deleted both that column and the assertion's own
    #    pay period, so what survives is the invariant those columns existed to
    #    serve -- an account the resolver can answer for.  A period wipe can no
    #    longer strand an anchor, which is the whole point of the deletion.
    for account in db_session.query(Account).filter_by(user_id=user_id):
        assert db_session.query(AccountAnchorHistory).filter_by(
            account_id=account.id,
        ).count() > 0, (
            f"user {user_id}: account {account.id} carries no balance "
            "assertion, so no producer can resolve a balance for it"
        )

    # 2. Every transfer is in one of the user's own (surviving) periods.  It
    #    also counted two shadows per transfer, each in the transfer's period,
    #    until plan step balance:X-bi-6-4d-2 (ruling R-BAL167 class 4): a
    #    transfer is one row there (ck_transactions_names_no_transfer refused
    #    a row naming one until plan step balance:X-bi-6-5 dropped the
    #    column), so its legs are drawn off the transfer itself and have no
    #    period of their own to disagree with.
    for transfer in db_session.query(Transfer).filter_by(user_id=user_id):
        assert transfer.pay_period_id in period_ids, (
            f"user {user_id}: transfer {transfer.id} is in period "
            f"{transfer.pay_period_id}, not among the user's periods"
        )

    # 3. No transaction (scoped via its account) references a period that
    #    no longer exists -- the CASCADE FK enforces this; re-checking
    #    catches an ORM bypass after a bulk delete.
    orphans = (
        db_session.query(Transaction.id)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(Account.user_id == user_id, PayPeriod.id.is_(None))
        .count()
    )
    assert orphans == 0, (
        f"user {user_id}: {orphans} transaction(s) reference a deleted "
        f"pay period"
    )
