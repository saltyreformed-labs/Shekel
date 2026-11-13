"""The seeded owner, the bare user, and the fixtures that rebuild an owner's calendar.

The seeded owner's credentials and :func:`build_seed_user` are here, beside ``seed_user``
and the pay-period fixtures that reset that owner's schedule.  ``tests/conftest.py``
registers each fixture here by importing it by name.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.user import User, UserSettings
# ``Account`` is intentionally not imported here: every test fixture
# constructs accounts via ``app.services.account_service.create_account``,
# the canonical post-E-19 factory.  Tests that need to exercise the
# storage-tier NOT NULL constraint directly use raw SQL inserts.
from app.models.scenario import Scenario
from app.models.category import Category
from app.models.ref import AccountType
from app.services import account_service, pay_period_write
from app.services.auth_service import hash_password
from app.utils.dates import display_today
from tests._test_helpers import (
    mint_fixture_era,
    open_books_before_the_first_assertion,
    open_owner_calendar,
    rebuild_calendar,
    rhythm_of,
)


# --- Data Fixtures --------------------------------------------------------


@pytest.fixture()
def bare_user(app, db):
    """A minimal user fixture: User + UserSettings only.

    No account, no pay periods, no scenario.  Tests that exercise
    services in isolation (e.g. ``pay_period_service``) and want a
    clean user state with no pre-existing pay periods use this
    instead of ``seed_user``, which seeds a bootstrap period (it had to
    for the NOT NULL anchor columns ruling R-EH deleted, then for account
    create's refusal of an owner with none, until ruling plan#3318).

    Returns:
        dict with keys: user, settings.
    """
    user = User(
        email="bare@shekel.local",
        password_hash=hash_password("barepass-12345"),
        display_name="Bare User",
    )
    db.session.add(user)
    db.session.flush()

    settings = UserSettings(user_id=user.id)
    db.session.add(settings)
    db.session.commit()

    return {"user": user, "settings": settings}


@pytest.fixture()
def bare_auth_client(app, db, client, bare_user):  # pylint: disable=unused-argument
    """Provide an authenticated test client for the bare user.

    Companion to ``auth_client``: bare_user has no account, no
    scenario, no categories, and (importantly for pay_period_service
    tests) no bootstrap pay period.  Use this in tests that need a
    logged-in session against a user with no pre-existing financial
    data.
    """
    resp = client.post("/login", data={
        "email": "bare@shekel.local",
        "password": "barepass-12345",
    })
    assert resp.status_code == 302, (
        f"bare_auth_client login failed with status {resp.status_code}"
    )
    return client


@pytest.fixture()
def bare_user_with_cadence(db, bare_user):
    """``bare_user`` plus the cadence row every payday-holding owner has.

    **For the cases that must hand-build a CORRUPT pay period** -- a duplicate
    ``period_index``, an index out of calendar order, a gap, an overlapping
    span.  Those shapes exist to prove a checker or a migration pre-flight
    catches them, and no application door can produce any of them, so
    ``_test_helpers.open_owner_calendar`` is not the answer there the way plan
    step ``pay_calendar:C4-b-1`` made it the answer for ordinary owners.

    What such a case still needs is the half of the owner that is NOT corrupt:
    since plan step ``pay_calendar:C4-b-2``, ``fk_pay_periods_schedule``
    refuses a pay period whose owner holds no ``budget.pay_schedule`` row, so
    ``bare_user`` alone can no longer carry one.  This fixture is that rule
    stated ONCE rather than a schedule-and-era line copied into each case --
    four of them today, and the fifth would be the one that copies it wrongly.
    The era's day is inert for hand-built rows at plan step
    ``pay_calendar:C17-a`` (``mint_fixture_era`` says why).

    The cadence is 14 and no case should assert on it: these owners' periods
    are written by hand and are deliberately inconsistent with any cadence.
    It exists to satisfy the key, not to describe the rows.

    Returns:
        ``bare_user``'s own dict, unchanged -- the schedule row is a side
        effect on the database, not a new key, so a case can swap this fixture
        in for ``bare_user`` without touching anything else it reads.
    """
    mint_fixture_era(bare_user["user"].id, date(2026, 1, 2), 14)
    db.session.commit()
    return bare_user


@pytest.fixture()
def bare_periods(app, db, bare_user):
    """Generate 10 pay periods for ``bare_user`` starting 2026-01-02.

    The bare_user-companion to ``seed_periods`` for tests that test
    the pay-period service contract in isolation: bare_user has no
    pre-existing periods, so the generated periods take indices
    0..9, exactly as the pre-Commit-3 ``seed_periods`` did against
    seed_user.

    Returns:
        List of PayPeriod objects.
    """

    periods = pay_period_write.record_paydays(
        user_id=bare_user["user"].id,
        first_payday=date(2026, 1, 2),
        num_periods=10,
        rhythm=rhythm_of(14),
    )
    db.session.commit()
    return periods


#: The seeded owner's credentials, in ONE place: :func:`build_seed_user`
#: writes them and :func:`log_in_seed_user` posts them, and a fixture that
#: seeded one email while the login form posted another would fail as a 401
#: naming neither.
SEED_USER_EMAIL = "test@shekel.local"
SEED_USER_PASSWORD = "testpass"

#: The day the seeded owner's BOOTSTRAP pay period starts, which is also the
#: day their Checking account's origination assertion is dated for and one day
#: after the day its books open.
#:
#: **Named because it is now a fixture CONTRACT rather than an implementation
#: detail** (plan step X-f3c-2c).  ``budget.account_anchor_history`` is
#: append-only, so no fixture can re-home that assertion onto whatever calendar
#: a periods fixture builds: the seeded account asserts here and only here
#: until a test says otherwise.  A case that turns on which day was last
#: asserted therefore either states its own (see
#: ``_test_helpers.reassert_balance_on``) or names this one.  *This named
#: ``_drop_seed_user_bootstrap`` as what could no longer re-home it; plan step
#: ``pay_calendar:C4-b-1`` deleted that function for the door it was
#: re-implementing, and the property is the append-only table's rather than any
#: one caller's.*
SEED_USER_BOOTSTRAP_START = date(2024, 1, 5)

#: The cadence the seeded owner's schedule runs at, in days.
#:
#: Named beside the opening payday because plan step ``pay_calendar:C4-b-1``
#: made it a STORED fact rather than an inferred one: the bootstrap batch goes
#: through ``pay_period_write.record_paydays``, which upserts the owner's
#: ``budget.pay_schedule`` row at whatever this says (the cadence rule, plan
#: step C3-b).  It was the literal ``13`` in a ``timedelta`` before that -- a
#: period LENGTH standing in for the cadence it derives from -- and the two
#: differ by one, which is exactly the off-by-one a fixture computing a derived
#: column by hand is free to make.
SEED_USER_CADENCE_DAYS = 14


def log_in_seed_user(client):
    """Log *client* in as the seeded owner through the real login form.

    Split out of ``auth_client`` because a test starting from a seeded start
    state already HAS the owner and must not seed a second one, but still
    needs the same session cookie the form issues -- so the two share this
    rather than posting their own copy of the credentials.

    Args:
        client: A Flask test client.

    Returns:
        The same client, now carrying a logged-in session.
    """
    resp = client.post("/login", data={
        "email": SEED_USER_EMAIL,
        "password": SEED_USER_PASSWORD,
    })
    assert resp.status_code == 302, (
        f"login as {SEED_USER_EMAIL} failed with status {resp.status_code}"
    )
    return client


def build_seed_user(db):
    """Create and return a test user with settings, account, and scenario.

    The ``seed_user`` fixture is a one-line call to this, and so is any seeded
    start state that needs the same owner (plan step balance:X-be-2).  It is a
    function rather than only a fixture so those two describe the SAME user
    instead of two definitions that drift -- a world built from a copy of this
    would be a second answer to "what does a seeded owner look like".

    Args:
        db: The Flask-SQLAlchemy extension to write through.

    Returns:
        dict with keys: user, settings, account, scenario, categories,
        bootstrap_period.  ORM objects, live in the caller's session.
    """
    user = User(
        email=SEED_USER_EMAIL,
        password_hash=hash_password(SEED_USER_PASSWORD),
        display_name="Test User",
    )
    db.session.add(user)
    db.session.flush()

    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    # The owner's OPENING pay period, written by the door that owns the
    # table (plan step pay_calendar:C4-b-1).  It precedes the Checking
    # account below, as registration's does (``create_account`` refused an
    # owner with none until ruling plan#3318).  This was a hand-built
    # ``PayPeriod(...)`` -- three columns set by the fixture, two of them
    # (``end_date``, ``period_index``) values the writer DERIVES -- and no
    # ``budget.pay_schedule`` row beside it, which is a state
    # ``pay_period_write.record_paydays`` cannot produce and
    # ``registration_service.register_user`` therefore never produces either.  The
    # seeded owner was consequently the one shape production does not have:
    # paydays with no recorded cadence, pay-calendar finding **P8**, which
    # ``pay_schedule_service.resolve_schedule`` had to carry an inferring
    # fallback for.  Going through the writer makes the seeded owner's
    # calendar the same object production's is.
    #
    # The row it writes is byte-identical to the one this replaced --
    # ``end_date`` is ``start + cadence - 1`` and ``period_index`` is 0 for a
    # single-payday batch -- so nothing dated against it moves.  What is NEW
    # is the ``budget.pay_schedule`` row the same call upserts (the cadence
    # rule, plan step C3-b).
    #
    # The day is an arbitrary Friday well before any test's typical 2026
    # range, so it stays out of the way of date-anchored assertions.  The
    # period is exposed as ``seed_user["bootstrap_period"]`` so tests that
    # create additional accounts inline can anchor them to it.
    bootstrap_period = open_owner_calendar(
        user.id, SEED_USER_BOOTSTRAP_START, cadence_days=SEED_USER_CADENCE_DAYS,
    )[0]

    # Baseline scenario before the account, as registration orders them.  The
    # order once mattered: ``create_account`` posted the opening's anchor
    # correction into every scenario until plan task plan#3076 deleted that
    # sync.  It reads no scenario now (plan task plan#3332), and the worked-out
    # ledger reads the $1000.00 opening in whichever scenario it is asked for.
    scenario = Scenario(
        user_id=user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    # Default Checking account via the canonical factory.  Tests that
    # later true-up the anchor produce additional AccountAnchorHistory
    # rows; the factory's origination row is here from t0 so the
    # post-Commit-4 resolver reads a consistent event stream.
    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=checking_type.id,
            name="Checking",
            anchor_balance=Decimal("1000.00"),
            # Day one of the owner's bootstrap period -- the production shape,
            # an account opened on day one of its own period (ruling R-DH, plan
            # step 2).  Without it the origination asserts on the WALL-CLOCK
            # day, years after that 2024 period; until ruling R-EO its own
            # ``pay_period_id`` named the period too, two clocks on one row.
            #
            # **It is what makes "and then things happened" say so.**  An
            # assertion is the CLOSING balance for its civil day, so a settle
            # dated that day is INSIDE it.  ``tests/test_services/conftest.py``
            # freezes today, and the ordinary settle idiom dates the row on the
            # user's today -- so an origination left on the
            # frozen clock lands on the very civil day the settles do, and every
            # fixture meaning "an account existed, then money moved" silently
            # became "money moved on the opening's own day".  Those fixtures
            # passed only while the OPENING carried a partition exception
            # (finding N-133 / F1); this is N-132's shape one layer up.
            #
            # Supplied to the factory rather than re-stamped afterwards, as a
            # door supplies it.  (Until plan task plan#3076 ``create_account``
            # also posted the opening's anchor correction keyed on this day,
            # so a later re-stamp left the stored ledger holding a stale key
            # plus its reversal in every seeded database.)
            observed_on=bootstrap_period.start_date,
        ),
    )
    # **The BOOKS open the day before that assertion** (plan step X-f3c-2b,
    # ruling **R-HG**).  The paragraph above moved the ORIGINATION off the wall
    # clock so it would not share a civil day with the settles; the books
    # themselves need the same separation for a stronger reason.  An opening
    # equity is the CLOSING balance for its own day, so a movement dated on it
    # is inside the figure and is refused outright -- where an assertion merely
    # absorbs one.  Fixtures settling on ``bootstrap_period.start_date`` are
    # ordinary here, and the opening is the only one of the two that forbids
    # them.
    #
    # Restated rather than supplied, because ``create_account``'s day bound
    # (``resolve_observation_day``) floors ``observed_on`` at the calendar's own
    # first day and would refuse the day before it.  That floor is a rule about
    # ASSERTIONS (ruling R-ER); an opening is not one, which is why production's
    # own repair (migration ``d3b6f1c8a274``) writes the row directly too.  It
    # moves no figure: the origination assertion still clears whatever settled
    # on its own day, so every correction is what it was.
    open_books_before_the_first_assertion(db.session, account)

    # Create default categories.
    categories = []
    for group, item in [
        ("Income", "Salary"),
        ("Home", "Rent"),
        ("Auto", "Car Payment"),
        ("Family", "Groceries"),
        ("Credit Card", "Payback"),
    ]:
        cat = Category(
            user_id=user.id,
            group_name=group,
            item_name=item,
        )
        db.session.add(cat)
        categories.append(cat)
    db.session.flush()

    db.session.commit()

    return {
        "user": user,
        "settings": settings,
        "account": account,
        "scenario": scenario,
        "categories": {c.item_name: c for c in categories},
        "bootstrap_period": bootstrap_period,
    }


@pytest.fixture()
def seed_user(app, db):  # pylint: disable=unused-argument
    """Create and return a test user with settings, account, and scenario.

    Returns:
        dict with keys: user, settings, account, scenario, categories.
    """
    return build_seed_user(db)


def _reset_seed_calendar(owner, first_payday, num_periods, cadence_days):
    """Rebuild *owner*'s WHOLE pay-period schedule through the production door.

    **Plan step ``pay_calendar:C4-b-1``.**  This replaces
    ``_drop_seed_user_bootstrap``, 135 lines that carried out a schedule reset
    by hand: it re-stated the account's opening, deleted the opening pay period
    with a bare ``query.delete()``, renumbered every survivor with raw SQL
    (``SET period_index = period_index - 1``), and then re-posted the anchor
    corrections that delete had disposed.

    **The application already has a door that does that job**:
    ``pay_period_admin.reset_pay_periods``, the settings-page correction that
    wipes every period and rebuilds the schedule from a new opening payday
    (re-syncing the loan genesis postings and the account anchor corrections
    onto it, until plan task plan#3076).  It was not "exactly" the hand-rolled
    sequence -- it did strictly MORE, and the extra was one of the three
    differences below.  Each difference was a defect in the hand-rolled version:

    * it renumbered ``period_index`` ITSELF -- a fixture computing by hand the
      derived column this arc exists to delete, in raw SQL the ORM cannot see;
    * it never re-synced LOAN genesis postings, so a world holding a loan would
      have been rebuilt with that loan's opening entries missing;
    * it recorded the new batch BESIDE the surviving opening period and deleted
      that period afterwards, so the new periods were written at indices 1..N
      and pulled back to 0..N-1.  The door retires and records in ONE
      ``record_paydays`` call, so they are 0..N-1 when they are written.

    **It refutes ledger row N-392's diagnosis**, which says the seeded account
    reaches its resting state "through a DELETE of the pay period it was opened
    against, which no production path performs".  ``reset_pay_periods`` deletes
    every pay period the owner has, the account's own opening period included,
    and it is reachable from ``POST /pay-periods/reset``.  What was true is
    narrower: no production path performed the hand-rolled SEQUENCE.

    **The re-statement of the account's opening is deliberately NOT carried
    over, and that is measured rather than argued.**  The deleted helper moved
    the books to ``min(governing.opened_on, new_anchor.start_date - 1 day)`` --
    backward only.  Every owner the root conftest's fixtures build opens its
    books the day before :data:`SEED_USER_BOOTSTRAP_START`, and every calendar
    this function is asked for starts later than that, so the ``min`` always
    chose the day already stored: the call could not move a day in any world
    those fixtures build.  ``TestTheResetDoesNotMoveTheBooks`` in
    ``tests/test_arch/test_the_seeded_calendar_comes_from_the_writer.py`` holds
    that as a property rather than leaving it as this paragraph's claim.

    Args:
        owner: The owner dict a seed fixture returned; ``owner["user"]`` is
            whose schedule is rebuilt.
        first_payday: The opening payday of the rebuilt schedule.
        num_periods: How many periods to build from it.
        cadence_days: Days between them, persisted as the owner's cadence by
            the writer (the cadence rule, plan step C3-b).

    Returns:
        The owner's periods, PAYDAY ascending -- which is ``period_index``
        ascending by construction, and is the spelling that survives the column
        drop at plan step ``pay_calendar:C4-c``.
    """
    # The books bound the opening payday; :func:`rebuild_calendar` refuses one
    # at or before them, which is the door every caller reaches rather than
    # this wrapper alone (ruling ``balance:R-HG``).
    return rebuild_calendar(
        owner["user"].id, first_payday, num_periods, cadence_days,
    )


@pytest.fixture()
def seed_periods(app, db, seed_user):
    """Rebuild the seeded owner's schedule as 10 pay periods from 2026-01-02.

    The owner opens with one period at :data:`SEED_USER_BOOTSTRAP_START`, and
    this RESETS that schedule to the one the test wants through
    :func:`_reset_seed_calendar` -- so the returned periods occupy indices
    0..9 because the writer derived them, not because anything renumbered
    them afterwards.

    Returns:
        List of PayPeriod objects, payday ascending.
    """
    return _reset_seed_calendar(
        seed_user, date(2026, 1, 2), 10, SEED_USER_CADENCE_DAYS,
    )


def _today_relative_start_date():
    """Return start_date that places today in period 4 of a 10-period biweekly run.

    Period 4 is the middle of a 10-period window, leaving 4 historical
    periods and 5 future periods.  The start is aligned to the most
    recent Monday so period boundaries fall on weekdays consistently.
    Used by ``seed_periods_today``-style fixtures so that "which paycheck
    contains today" -- ``PayCalendar.period_containing``, and
    ``pay_period_service.get_current_period`` before pay-calendar plan step
    C2-f3a deleted it -- always answers a real period regardless of the
    wall-clock date.
    """
    today = display_today()
    return today - timedelta(days=today.weekday() + 4 * 14)


def build_periods_today(db, seed_user):
    """Generate 10 biweekly pay periods so today falls in period 4.

    The ``seed_periods_today`` fixture is a one-line call to this, and so is
    any seeded start state needing the same calendar (plan step
    balance:X-be-2), for the reason :func:`build_seed_user` is a function.

    **It reads ``display_today()``, so a caller that freezes it freezes this.**
    A world built from this is therefore built once per SESSION and dropped at
    its end -- see the block comment in ``tests/_seeded_start_states.py``; one
    kept across midnight would place today in a period this no longer builds.

    Args:
        db: The Flask-SQLAlchemy extension.  **Unread since plan step
            ``pay_calendar:C4-b-1``** -- the write goes through
            :func:`_reset_seed_calendar`, which reaches the session itself --
            and kept because it is how the two external callers
            (``test_no_baseline_policy``, the seeded-state builders) declare
            that the ``db`` fixture must be active before this runs.
            **This is the shape ledger row ``balance:N-393`` records as a
            defect** -- an argument its body never reads -- and the reason it
            is kept anyway is the one thing that row's subject does not have: a
            pytest fixture parameter is a DEPENDENCY DECLARATION, so removing
            it would not tidy a signature, it would let a caller run this
            before the database fixture had built a session.  Named rather
            than left for the next reviewer to re-derive.
        seed_user: The owner dict :func:`build_seed_user` returned.

    Returns:
        List of PayPeriod objects, payday ascending.
    """
    return _reset_seed_calendar(
        seed_user, _today_relative_start_date(), 10, SEED_USER_CADENCE_DAYS,
    )


@pytest.fixture()
def seed_periods_today(app, db, seed_user):  # pylint: disable=unused-argument
    """Generate 10 biweekly pay periods so today falls in period 4.

    Use this fixture when the test exercises a code path that asks which
    paycheck contains today (``PayCalendar.period_containing``, directly or
    via a route handler).  Use the regular ``seed_periods`` fixture when the
    test asserts on specific calendar dates (due_date filters,
    year-end summaries for tax_year=2026, loan origination alignment).

    A test must use one or the other, never both -- they would write
    overlapping pay_periods rows for the same user.

    Returns:
        List of PayPeriod objects, ordered by period_index.
    """
    return build_periods_today(db, seed_user)


@pytest.fixture()
def seed_schedule_at_cadence(app, db, seed_user):
    """Return a factory that seeds the owner a schedule at a CHOSEN cadence.

    The parameterised sibling of :func:`seed_periods_today`, which is fixed at
    14 days.  Every other periods fixture in this file is biweekly, so until
    recurrence plan step **R-F17** no test could tell a derivation from the
    hardcoded 26 that stood for "a year" -- which is exactly why ledger row
    **F-17** shipped uncovered.  A test that asserts on a cadence-derived
    window uses this; one that only needs "today is in a paycheck" keeps the
    cheaper fixture.

    Like :func:`seed_periods_today` it RESETS the seeded owner's schedule
    through :func:`_reset_seed_calendar`, so ``period_index`` runs from 0
    because the writer derived it, and it places today in period
    *periods_before* -- **for a cadence of 7 days or more**.  The first payday
    is aligned back to the most recent Monday first, so at a shorter cadence
    that up-to-six-day alignment is itself worth a period or more and today
    lands later than *periods_before*.  No caller passes below 7; the bound is
    stated rather than left as a claim that happens to hold.

    Args:
        app: The Flask app fixture (for the application context the writer
            needs).
        db: The SQLAlchemy ``db`` fixture.
        seed_user: The owner these periods belong to.

    Returns:
        ``_seed(cadence_days, num_periods=40, periods_before=4)`` -> the
        owner's periods, payday ascending.  40 is wide enough that a full year
        of paychecks fits at cadences of 14 days or longer; a weekly case asks
        for more.

        **``periods_before`` used to be bounded by the writer's forward-only
        rule and no longer is** (plan step ``pay_calendar:C4-b-1``).  While this
        fixture APPENDED beside the seeded owner's opening payday,
        ``pay_period_batch.reject_backward_payday`` refused a first payday
        earlier than one full cycle after it -- so four periods of history at a
        300-day cadence started three years before that payday and was refused.
        Going through the reset door retires every existing period in the same
        ``record_paydays`` call that records the new ones, and that refusal
        returns early on an empty surviving set, so a long-cadence case is free
        to ask for as much history as it wants.  The default still matches
        :func:`seed_periods_today`.
    """
    def _seed(cadence_days, num_periods=40, periods_before=4):
        today = display_today()
        first_payday = today - timedelta(
            days=today.weekday() + periods_before * cadence_days,
        )
        return _reset_seed_calendar(
            seed_user, first_payday, num_periods, cadence_days,
        )
    return _seed


@pytest.fixture()
def auth_client(app, db, client, seed_user):  # pylint: disable=unused-argument
    """Provide an authenticated test client.

    Logs in via the login form to get a proper session.
    """
    return log_in_seed_user(client)


@pytest.fixture()
def seed_periods_52(app, db, seed_user):
    """Rebuild the seeded owner's schedule as 52 pay periods from 2026-01-02.

    :func:`seed_periods` at a two-year horizon, for the FIN cases that need
    production-scale data volumes.  Same door, same derivation.

    Returns:
        List of PayPeriod objects, payday ascending.
    """
    return _reset_seed_calendar(
        seed_user, date(2026, 1, 2), 52, SEED_USER_CADENCE_DAYS,
    )
