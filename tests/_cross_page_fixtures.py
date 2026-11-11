"""The cross-page balance lock's fixtures: one monthly calendar, one account of each kind.

``tests/conftest.py`` registers each fixture here by importing it by name.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import BusinessDayShiftEnum
from app.services import pay_rhythm
from app.services.pay_calendar import calendar_for
from app.utils.dates import display_today
from tests._ledger_reads import posted_loan_balance_at
from tests._test_helpers import (
    create_loan_account,
    figure_source_columns,
    generate_row_of,
    insert_trueup_event,
    make_appreciating_account,
    make_expense_template,
    make_investment_account,
    open_books_before_the_first_assertion,
    rebuild_calendar_on,
    settle_day_columns,
)


def _build_cross_page_calendar_periods(db, user):
    """Build 36 calendar-monthly pay periods for the cross-page lock fixtures.

    The shared period-construction step behind ``seed_cross_page_account``
    and every per-kind cross-page fixture (loan / property / investment /
    secured).  Resets the owner's schedule to one period per calendar month
    spanning ``[today.year - 1, today.year + 1]``, then returns the full
    ordered period list and the anchor period (the calendar month containing
    today).

    Monthly (not biweekly) periods are deliberate: the anchor period's last
    covered day IS a calendar month-end -- each payday is the 1st, so each
    period runs to the day before the next 1st -- and the C9-3 boundary
    invariant of the seam's
    :func:`app.services.balance_at.cash_balance_at` then makes the calendar
    surface's projected month-end balance equal the anchor-period balance for
    the same data.  The anchor being the month containing today also
    lets ``PayCalendar.period_containing`` land on it with no date-mock
    plumbing.

    **It goes through the reset door on a MONTHLY rhythm since plan step
    ``pay_calendar:C17-d-2``** (ledger row **P78**, closed there).  Until
    that step no door could write this schedule -- ``record_paydays`` spaced
    a batch at ONE day count and calendar months are 28 to 31 days apart --
    so the rows were built by hand beside a fixed-days era of 31 that
    disagreed with them (the LAST period's projected end, and every
    horizon a surface derives from the cadence, read a 31-day owner).  The
    day-of-month kind is the door: ``Rhythm(Monthly(1), NONE)`` states the
    calendar these rows always were, the writer records it, and the era
    answers 12 paychecks a year and exactly *n* within *n* months.

    Args:
        db: The SQLAlchemy ``db`` fixture.
        user: The owning :class:`~app.models.user.User` (its ``id`` scopes
            the periods).

    Returns:
        ``(all_periods, anchor_period)`` -- the user's full period list in
        payday order (the 36 monthly periods) and the anchor period
        containing today.
    """
    today = display_today()
    first_year = today.year - 1
    created = rebuild_calendar_on(
        user.id, date(first_year, 1, 1), 36,
        pay_rhythm.Rhythm(pay_rhythm.Monthly(1), BusinessDayShiftEnum.NONE),
    )
    all_periods = created

    # The anchor period is the calendar month containing today, and the
    # containment is ASSERTED through the calendar rather than off the row:
    # plan step ``pay_calendar:C4-c`` dropped ``end_date``, and this fixture's
    # whole point is that its DERIVED spans are the calendar months.  The
    # created list runs chronologically from January of ``first_year``, so
    # today's month sits at index ``12 + (today.month - 1)``.
    anchor_period = created[12 + (today.month - 1)]
    anchor_span = calendar_for(user.id).period_by_id(anchor_period.id)
    assert anchor_span is not None and anchor_span.covers(today), (
        f"anchor_period {anchor_span} does not contain today={today}; "
        f"fixture invariant broken"
    )
    return all_periods, anchor_period


def _neutralize_seed_checking(db, seed_user, anchor_period):
    """Re-anchor the ``seed_user`` Checking account to $0 at *anchor_period*.

    The per-kind cross-page fixtures isolate a single non-cash account so
    the AGGREGATE surfaces (year-end net worth, the savings net-worth
    trend) reflect ONLY that account.  ``seed_user`` always provisions one
    Checking account at a $1,000 anchor, so this neutralises it: it appends
    a $0 ``AccountAnchorHistory`` row dated on *anchor_period*'s first day, and
    that day is later than the origination assertion's, so ``resolve_anchor``
    -- which orders on ``(observed_on, created_at, id)`` -- returns $0.  A $0
    asset anchored at the current period contributes 0 to every net-worth sum
    from the current period forward and gates the trend at the current
    period, so the per-kind account's value stands alone.

    The account is re-fetched by primary key because the period-build
    commit expires every object loaded upstream in ``seed_user``; setting
    attributes on an expired instance does not reliably re-mark the row
    dirty in every SQLAlchemy ORM mode (the same refetch
    ``seed_cross_page_account`` performs for its own anchor override).

    Args:
        db: The SQLAlchemy ``db`` fixture.
        seed_user: The ``seed_user`` fixture dict.
        anchor_period: The period to re-anchor the Checking account to (the
            current / anchor period of the per-kind fixture).
    """
    # Pylint: ``import-outside-toplevel`` -- Account is intentionally not
    # imported at conftest top (every fixture builds accounts via the
    # canonical factory); load it lazily, as seed_cross_page_account does.
    # pylint: disable=import-outside-toplevel
    from app.models.account import Account
    from tests._test_helpers import override_anchor

    account = db.session.get(Account, seed_user["account"].id)
    open_books_before_the_first_assertion(db.session, account)
    override_anchor(
        db.session, account, anchor_period, Decimal("0.00"),
    )


@pytest.fixture()
def seed_cross_page_account(app, db, seed_user):
    """Factory fixture for the PT-01 cross-page balance equality lock (HIGH-01).

    Returns a callable ``build(anchor_balance, expense_amount, entries)``
    that materialises one symptom-tuple case shared across every
    balance-rendering surface (grid, /savings, /accounts checking detail,
    dashboard, year-end net-worth per-account, calendar).  The fixture
    realises the structural premise of HIGH-01 -- the developer's worst
    two symptoms (#1 $160 grid vs $114.29 /savings; #5 /accounts matches
    nowhere) had zero falsifying tests until this lock landed (Commit 11).

    The realised data shape is invariant across cases:

      * The user's existing seed_user bootstrap pay period is removed and
        replaced with 24 calendar-monthly periods spanning
        ``[today.year - 1, today.year + 1]``.  Monthly (not biweekly)
        periods are chosen deliberately so the anchor period's
        ``end_date`` IS a calendar month-end -- the C9-3 boundary
        invariant of the seam's :func:`app.services.balance_at.cash_balance_at`
        then guarantees the calendar surface's projected month-end balance
        equals the anchor-period balance for the same data.
        Without that alignment a mid-period month-end would silently make
        the calendar surface look like a divergence even when the
        underlying math agrees, defeating the cross-page lock.
      * The anchor period is the calendar month containing today (so the
        dashboard's containment lookup and the grid's window both naturally
        land on the anchor period without any date-mock plumbing).  Those
        two named ``get_current_period`` and ``get_periods_in_range`` until
        pay-calendar plan steps C2-f2b and C2-f3a deleted them; the
        alignment is what matters and it is unchanged.
      * The account anchor is overridden -- via a fresh
        ``AccountAnchorHistory`` row + cache-column update, latest-wins
        per E-19 -- to the case's ``anchor_balance``.  ``seed_user``'s
        factory-default $1,000 anchor is irrelevant here.
      * A single Projected envelope expense in the anchor period, the
        engine's own row of a definition whose stated price is
        ``expense_amount`` (plan step balance:X-cf), and the supplied entries
        list, each entry dated ``anchor_period.start_date`` (so all
        entries fall on or before any month-end ``as_of`` the calendar
        surface evaluates).

    The factory returns a context dict keyed:

      * ``user_id``, ``account``, ``account_id``, ``scenario``,
        ``scenario_id``: identifiers callers pass to every surface.
      * ``all_periods``, ``anchor_period``: the period list and the
        chosen anchor period.
      * ``year``, ``month``: ``anchor_period.start_date.year /
        .month`` -- the calendar/year-end surfaces consume these.

    The five remediation-plan cases (PT-01 base, zero anchor, negative
    overdraft, credit-only entries, uncleared-floor) are realised by the
    test's ``@pytest.mark.parametrize`` block, not baked into the
    fixture, so a future case can be added in one place without growing
    a conftest variant.

    Returns:
        Callable ``(anchor_balance, expense_amount, entries) ->
        dict``.  Each entry is a 3-tuple ``(amount, is_credit,
        is_settled)`` -- amount as ``Decimal`` (from string), and the two
        booleans as the entries-aware reduction's discriminants.
        ``is_settled`` stamps the purchase's ``settled_on`` with the anchor
        period's start day, which is the assertion's own ``observed_on``
        here, so the purchase reads as already inside the asserted balance
        (ruling R-DH (d)); ``False`` leaves it NULL and outstanding.
    """
    # pylint: disable=import-outside-toplevel
    from app.models.account import Account
    from app.models.transaction_entry import TransactionEntry
    from tests._test_helpers import override_anchor

    def _build(
        anchor_balance: Decimal,
        expense_amount: Decimal,
        entries: list[tuple[Decimal, bool, bool]],
    ) -> dict:
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]

        # Build the calendar-monthly period grid and locate the anchor
        # month containing today via the shared period builder (which also
        # documents why the seed_user bootstrap is deliberately kept).
        all_periods, anchor_period = _build_cross_page_calendar_periods(
            db, user,
        )

        # Override the anchor balance and matching history row.  The
        # ``seed_user`` factory already wrote an origination history
        # row at $1,000 against the bootstrap period; appending a
        # newer row makes ``resolve_anchor`` (latest-wins by
        # ``created_at``) return the case's balance instead.  The
        # cache columns are updated in the same flush so the
        # resolver's cache-reconciliation path stays quiet (cache ==
        # latest event), which keeps the test log free of spurious
        # ``EVT_ANCHOR_CACHE_RECONCILED`` entries.
        #
        # Re-fetch ``account`` against the live session because the
        # earlier ``db.session.commit()`` (after the new period
        # inserts) expires every object loaded in this session
        # (``expires_on_commit=True``).  Setting attributes on an
        # expired instance whose load fixture lives upstream in
        # ``seed_user`` does not reliably re-mark the row dirty in
        # every SQLAlchemy ORM mode -- the symptom is a silent
        # ``EVT_ANCHOR_CACHE_RECONCILED`` log entry on every surface
        # read because the cache column did not actually move.
        # Refetching by primary key gives us a known-attached
        # instance whose attribute assignments are guaranteed to
        # mark the row dirty for the next flush.
        account = db.session.get(Account, seed_user["account"].id)
        open_books_before_the_first_assertion(db.session, account)
        override_anchor(
            db.session, account, anchor_period, anchor_balance,
        )

        # Single Projected envelope expense in the anchor period, generated by
        # the engine from a definition that states its price and repeats
        # every paycheck (plan step balance:X-cf).  ``is_envelope=True`` on
        # the template is what makes the entries-aware reduction applicable
        # -- a generated row defers to its template's flag -- while a
        # non-envelope one would short-circuit to ``effective_amount``
        # regardless of entries.
        template = make_expense_template(
            db.session, seed_user, amount=str(expense_amount),
            name="PT-01 envelope expense", category_key="Groceries",
            is_envelope=True,
        )
        txn = generate_row_of(template, anchor_period)

        # Entries all dated on anchor_period.start_date -- a date that
        # is on or before every month-end ``as_of`` the calendar
        # surface evaluates, so the E-27 entry-date cut is a no-op
        # for this fixture and the calendar surface's balance equals
        # the resolver's anchor-period balance by construction.
        for amount, is_credit, is_settled in entries:
            db.session.add(TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=user.id,
                amount=amount,
                description="PT-01 entry",
                purchased_on=anchor_period.start_date,
                # The assertion ``override_anchor`` wrote is observed on this
                # same day, so a purchase settled on it is INSIDE that balance
                # -- the state the retired ``settled_on=anchor_period.start_date`` flag named.
                **settle_day_columns(
                    anchor_period.start_date if is_settled else None
                ),
                is_credit=is_credit,
            ))
        db.session.commit()

        return {
            "user_id": user.id,
            "account": account,
            "account_id": account.id,
            "scenario": scenario,
            "scenario_id": scenario.id,
            "all_periods": all_periods,
            "anchor_period": anchor_period,
            "year": anchor_period.start_date.year,
            "month": anchor_period.start_date.month,
        }

    return _build


@pytest.fixture()
def cross_page_loan_ctx(db, seed_user):
    """Single isolated amortizing loan for the cross-page equality lock.

    Builds the shared calendar-monthly period grid, neutralises the
    seed_user Checking account to $0 (so the AGGREGATE surfaces -- year-end
    net worth, the savings net-worth trend -- reflect the loan alone), then
    creates ONE amortizing loan whose original principal P differs from its
    trued-up current balance C: ``create_loan_account`` sets P = $240,000 at
    origination, and an ``insert_trueup_event`` dated today asserts C =
    $200,000.  The trueup makes the resolver schedule today-forward (first
    payment next month), so every period up to and including the anchor
    reports C held flat -- NEVER P.  ``C != P`` is what makes the boundary
    assertion non-tautological (returning P at a pre-payment period is the
    exact historical bug PR #44 / aba0242 fixed).

    Returns a ctx dict mirroring ``seed_cross_page_account`` plus ``C``,
    ``P``, and ``pre_anchor_period`` (a period strictly before the anchor,
    where the balance map must equal C and not P).
    """
    # Pylint: ``import-outside-toplevel`` -- LoanParams is loaded lazily,
    # the Account-class convention this conftest follows (no model packages
    # imported at module top).
    # pylint: disable=import-outside-toplevel
    from app.models.loan_params import LoanParams

    user = seed_user["user"]
    scenario = seed_user["scenario"]
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    today = date.today()
    original_principal = Decimal("240000.00")  # P
    current_balance = Decimal("200000.00")     # C (trued up today); C != P
    loan = create_loan_account(
        seed_user, db.session, name="Cross-Page Loan",
        principal=original_principal, term=360,
        origination_date=date(today.year - 1, 1, 1),
    )
    params = db.session.query(LoanParams).filter_by(account_id=loan.id).one()
    insert_trueup_event(params, current_balance, anchor_date=today)
    db.session.commit()

    # Pre-anchor period: the period immediately before the anchor (current)
    # period.  The trueup is dated today and the loan's first scheduled
    # payment is next month, so this period reports C held flat -- the
    # boundary the bug used to fill with the original principal P.
    anchor_pos = next(
        i for i, p in enumerate(all_periods) if p.id == anchor_period.id
    )
    pre_anchor_period = all_periods[anchor_pos - 1]

    return {
        "user_id": user.id,
        "account": loan,
        "account_id": loan.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "C": current_balance,
        "P": original_principal,
        "pre_anchor_period": pre_anchor_period,
    }


@pytest.fixture()
def cross_page_loan_unpaid_ctx(db, seed_user):
    """An amortizing loan originated in the PAST with NO payment and NO true-up.

    The loan shape the cross-page lock could not previously see: the one in which
    a schedule-walking producer CAN phantom-pay the debt down.
    ``cross_page_loan_ctx`` asserts a true-up dated TODAY, which re-anchors the
    resolver's schedule today-forward and so leaves no past-dated UNPAID rows
    behind; this fixture deliberately leaves them.  Originated 18 months ago at
    $240,000 over 360 months, never paid and never trued up, the resolver's
    schedule carries ~17 PROJECTED installments dated on or before today.

    Every one of those rows is a payment that was never made, so not one dollar of
    principal was ever paid: the honest balance is the full $240,000 opening, on
    EVERY surface and at EVERY period.  A producer that walked the schedule instead
    of the ledger would report LESS.

    The loan IS opened in the ledger (``create_loan_account`` writes through
    production's reconcile path), exactly as a real configured loan is -- so on
    the current code every surface reads the ledger and agrees at $240,000, and
    the accompanying test PASSES.  It is a regression lock, not a reproducer: it
    holds the line against a schedule producer being reintroduced for the past,
    which is what let the /savings tile and the net-worth trend disagree on their
    own 'today' point (a no-ledger path production never took, but every loan test
    did).

    Returns a ctx dict mirroring ``cross_page_loan_ctx``, plus ``P`` (the original
    principal, which is also the correct balance everywhere) and
    ``past_period`` (a period strictly before today, where an unpaid projected
    installment would have phantom-paid the debt down).
    """
    user = seed_user["user"]
    scenario = seed_user["scenario"]
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    today = date.today()
    original_principal = Decimal("240000.00")  # P -- never paid down
    loan = create_loan_account(
        seed_user, db.session, name="Never-Paid Loan",
        principal=original_principal, rate=Decimal("0.06000"), term=360,
        origination_date=today - timedelta(days=548),
    )
    db.session.commit()

    anchor_pos = next(
        i for i, p in enumerate(all_periods) if p.id == anchor_period.id
    )
    past_period = all_periods[anchor_pos - 1]

    return {
        "user_id": user.id,
        "account": loan,
        "account_id": loan.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "P": original_principal,
        "past_period": past_period,
    }


def _unseeded_replay_balance(loan_id, scenario_id, as_of):
    """Return a loan's un-seeded schedule-replay balance (the pre-switch value).

    :func:`tests._test_helpers.unseeded_replay_balance`, the suite's ONE
    assembly of that replay (plan step **balance:X-bl-2b**).  Kept as a name
    here because the cross-page fixtures below read like prose with it.
    """
    # Pylint: ``import-outside-toplevel`` -- the app services are loaded lazily,
    # the convention every helper in this conftest follows.
    # pylint: disable=import-outside-toplevel
    from tests._test_helpers import unseeded_replay_balance

    return unseeded_replay_balance(loan_id, scenario_id, as_of)


@pytest.fixture()
def cross_page_loan_off_schedule_ctx(db, seed_user):
    """A GENESIS loan with an OFF-SCHEDULE confirmed payment (the read switch).

    Unlike ``cross_page_loan_ctx`` -- which posts no genesis and so resolves via
    the anchor-replay fallback -- this loan is OPENED in the ledger (a settled
    payment fires the genesis sync: opening + true-up corrections + the payment
    split) and its one confirmed payment pays cash far above the scheduled P&I,
    so the REAL principal it books down diverges from the schedule replay.  The
    read switch (plan Section 8) makes the SCALAR surfaces -- the /savings tile
    (``_compute_loan_account``) and the loan-detail page (the ``balance_at``
    seam scalar since plan C4) -- read that real ledger balance, NOT the replay.

    Returns the reader-shaped ctx plus ``ledger`` (the genesis reader's balance,
    what the surfaces must now show) and ``replay`` (the un-seeded resolver's
    schedule balance, what they showed BEFORE the switch); the test asserts the
    two DIVERGE so the "surfaces == ledger" equality is non-vacuous.  Since the
    Section 9 per-period read switch, the year-end and net-worth-trend MAP
    surfaces ALSO read the confirmed ledger off-schedule, so the whole
    four-surface set agrees on the real balance (see
    ``test_all_surfaces_read_the_ledger_off_schedule``).
    """
    # Pylint: ``import-outside-toplevel`` -- the app services / test helpers are
    # loaded lazily, the convention every fixture in this conftest follows.
    # pylint: disable=import-outside-toplevel
    from tests._test_helpers import (
        create_loan_with_trueup,
        create_settled_transfer,
    )

    user = seed_user["user"]
    scenario = seed_user["scenario"]
    today = date.today()
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    # One OFF-SCHEDULE payment ($5,000 cash vs ~$1,499 scheduled P&I) two months
    # before today, so its pay period has begun and the ledger books the extra
    # ~$3,500 of real principal the schedule replay drops on the floor.
    anchor_idx = next(
        i for i, p in enumerate(all_periods) if p.id == anchor_period.id
    )
    payment_period = all_periods[anchor_idx - 2]

    # Origination two years back at $250k, trued up to $200k the day before the
    # payment's pay period begins, 6% fixed.  origination != anchor so the walk
    # seeds from the true-up, and the loan carries the opening + true-up
    # genesis corrections.  The true-up sits after every installment before the
    # payment's own (the first due day on or after its period start): every
    # contractual installment is charged since plan step recurrence:R16-c-2,
    # so the year-back true-up it carried until then left a year of months
    # standing for the one payment to clear (ruling R-R103).
    loan = create_loan_with_trueup(
        seed_user, db.session,
        origination_principal=Decimal("250000.00"),
        anchor_balance=Decimal("200000.00"),
        anchor_date=payment_period.start_date - timedelta(days=1),
        rate=Decimal("0.06000"),
        origination_date=date(today.year - 2, 1, 1),
        name="Off-Schedule Loan",
    )
    # Settled on its own period start (a past date), so it is visible today under
    # C2's settled-date clock regardless of the UTC/display-tz offset.
    create_settled_transfer(
        seed_user, db.session, seed_user["account"], loan, payment_period,
        amount=Decimal("5000.00"),
        settled_on=payment_period.start_date,
    )
    db.session.commit()

    # The genesis reader (what the surfaces now read) vs the un-seeded schedule
    # replay (what they read before the switch): off-schedule they DIVERGE.
    ledger = posted_loan_balance_at(
        loan.id, scenario.id, today,
    )
    replay = _unseeded_replay_balance(loan.id, scenario.id, today)

    return {
        "user_id": user.id,
        "account": loan,
        "account_id": loan.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "ledger": ledger,
        "replay": replay,
    }


@pytest.fixture()
def cross_page_property_ctx(db, seed_user):
    """Single isolated appreciating Property, anchor == current period.

    Builds the shared period grid, neutralises the seed_user Checking to
    $0, then creates ONE Property whose user-set market value is V =
    $400,000, anchored at the current period.

    **Anchor == current no longer means the surfaces read V** (plan step X-g2b,
    ruling R-Y): the anchor period earns its own days now, where the shipped
    producer split on ``period_index > anchor_idx`` and served that period from
    a flat carry.  What the surfaces must still agree on is the VALUE, whatever
    it is -- which is what the cross-page classes assert, and what makes them a
    wiring lock rather than a figure pin.  Returns a ctx dict mirroring
    ``seed_cross_page_account`` plus ``V``, the ASSERTED value the modelled one
    is measured against.
    """
    user = seed_user["user"]
    scenario = seed_user["scenario"]
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    value = Decimal("400000.00")  # V
    prop = make_appreciating_account(
        seed_user, db.session, anchor_period, value, Decimal("0.03000"),
    )

    return {
        "user_id": user.id,
        "account": prop,
        "account_id": prop.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "V": value,
    }


@pytest.fixture()
def cross_page_investment_ctx(db, seed_user):
    """Single isolated Investment, anchor == current period, no contribution.

    Builds the shared period grid, neutralises the seed_user Checking to
    $0, then creates ONE 401(k) whose balance is V = $100,000, anchored at
    the current period, with no employer match and no contribution feed.
    Anchor == current used to mean every surface read the anchor balance V,
    because the growth projection skipped the anchor period entirely.  **Ruling
    R-Y gives that period its own days** (plan step X-g2b), so the surfaces read
    V plus that growth; what they must still do is agree, which is what the
    consuming test asserts -- reading the expected figure from the seam and
    pinning it strictly above V.  Returns a ctx dict mirroring
    ``seed_cross_page_account`` plus ``V``, the ASSERTED balance.
    """
    user = seed_user["user"]
    scenario = seed_user["scenario"]
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    value = Decimal("100000.00")  # V
    inv = make_investment_account(seed_user, db.session, anchor_period, value)

    return {
        "user_id": user.id,
        "account": inv,
        "account_id": inv.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "V": value,
    }


@pytest.fixture()
def cross_page_investment_past_anchor_ctx(db, seed_user):
    """Single isolated Investment anchored 6 monthly periods IN THE PAST.

    The model-from-anchor divergence fixture the Level 1 savings-tile reroute
    (the :mod:`app.services.balance_at` seam) unlocks.  A 401(k) with opening
    balance ``V0`` = $100,000 and a 7% assumed return
    (``make_investment_account``), anchored 6 monthly periods BEFORE today's
    period, with no contribution feed -- so the only post-anchor movement is
    compounding.  The cash-basis carry holds ``V0`` flat to today; the
    kernel's model-from-anchor map compounds ``V0`` forward to today, so the
    modeled balance at the current period is STRICTLY GREATER than ``V0``.
    That gap is what makes the cross-producer lock non-tautological: before
    the reroute the /savings tile read the flat ``V0`` while the year-end and
    net-worth-trend surfaces read the modeled value, and the reroute makes the
    tile adopt the modeled value the other kernel surfaces already report.

    Contrast ``cross_page_investment_ctx`` (anchor == current), where the
    divergence is only that period's own growth (ruling R-Y) rather than a whole
    anchor-to-today span.

    The seed_user Checking is neutralised to $0 so the AGGREGATE surfaces
    (year-end net worth, the savings net-worth trend) reflect the investment
    alone.  Returns a ctx dict mirroring the other per-kind fixtures plus
    ``V0`` (the flat cash-basis carry) and ``current_period`` (today's period,
    where every surface is read); ``anchor_period`` here is today's period
    (the dashboard's current period), NOT the account's past anchor.
    """
    user = seed_user["user"]
    scenario = seed_user["scenario"]
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    # Anchor the investment 6 monthly periods before today's period so the
    # model-from-anchor map compounds it forward across that gap.  The grid
    # puts today's period well above index 6 (bootstrap at 0, then 12 prior
    # monthly periods before today's month), so ``anchor_pos - 6`` is always a
    # real past period.
    anchor_pos = next(
        i for i, p in enumerate(all_periods) if p.id == anchor_period.id
    )
    past_anchor_period = all_periods[anchor_pos - 6]

    opening_balance = Decimal("100000.00")  # V0 -- the flat cash-basis carry
    inv = make_investment_account(
        seed_user, db.session, past_anchor_period, opening_balance,
    )

    return {
        "user_id": user.id,
        "account": inv,
        "account_id": inv.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "current_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "V0": opening_balance,
    }


@pytest.fixture()
def cross_page_secured_ctx(db, seed_user):
    """Property (PV) secured by a mortgage (MC) for the home-equity relationship.

    Builds the shared period grid, neutralises the seed_user Checking to
    $0, then creates ONE Property (market value PV = $400,000, anchored at
    the current period) and ONE mortgage trued up to current balance MC =
    $250,000 today, linked via ``mortgage.collateral_account_id =
    property.id`` so the property's ``secured_loans`` backref lists the
    mortgage.  The relationship under test: market value (PV) minus total
    secured debt (MC) is the equity, on the equity producer's own basis.

    **Since plan step X-g2b the /savings tile reads the MODELLED property value
    while that producer still reads the ``current_anchor_balance`` cache
    column** (finding N-83, scheduled for its own commit), so the consuming test
    asserts the two differ by exactly that gap rather than matching.  Returns
    PV, MC, and both account handles plus the standard period keys.
    """
    # Pylint: ``import-outside-toplevel`` -- Account / LoanParams are loaded
    # lazily, the Account-class convention this conftest follows (no model
    # packages imported at module top).
    # pylint: disable=import-outside-toplevel
    from app.models.account import Account
    from app.models.loan_params import LoanParams

    user = seed_user["user"]
    scenario = seed_user["scenario"]
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    today = date.today()
    property_value = Decimal("400000.00")    # PV
    mortgage_balance = Decimal("250000.00")  # MC
    prop = make_appreciating_account(
        seed_user, db.session, anchor_period, property_value,
        Decimal("0.03000"),
    )
    mortgage = create_loan_account(
        seed_user, db.session, name="Cross-Page Mortgage",
        principal=Decimal("300000.00"), term=360,
        origination_date=date(today.year - 1, 1, 1),
    )
    params = db.session.query(LoanParams).filter_by(
        account_id=mortgage.id,
    ).one()
    insert_trueup_event(params, mortgage_balance, anchor_date=today)
    # Re-fetch the mortgage by primary key so setting the collateral FK
    # reliably marks the row dirty (the create_loan_account commit expired
    # it, the same refetch reason as _neutralize_seed_checking).
    mortgage = db.session.get(Account, mortgage.id)
    mortgage.collateral_account_id = prop.id
    db.session.commit()

    return {
        "user_id": user.id,
        "property_account": prop,
        "property_account_id": prop.id,
        "mortgage_account": mortgage,
        "mortgage_account_id": mortgage.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "PV": property_value,
        "MC": mortgage_balance,
    }
