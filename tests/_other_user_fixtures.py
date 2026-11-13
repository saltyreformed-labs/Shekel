"""The users beside the seeded owner: a second owner of each kind, and the companion.

``tests/conftest.py`` registers each fixture here by importing it by name.

**Each owner here is made with two-factor** (plan#3600), as every account past
setup has it, and signs in through the one shared sign-in,
:func:`tests._two_factor_sign_in.sign_in_from_a_trusted_browser`.  The
companion is made without it, as her owner makes her; her client turns it on
(:func:`tests._two_factor_sign_in.sign_in_seed_companion`).
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.user import User, UserSettings
from app.models.scenario import Scenario
from app.models.category import Category
from app.models.ref import AccountType
from app.services import account_service
from app.services.auth_service import hash_password
from tests._seed_owner_fixtures import (
    OWNERS_PAGE,
    SEED_USER_BOOTSTRAP_START,
    SEED_USER_CADENCE_DAYS,
    _reset_seed_calendar,
)
from tests._test_helpers import (
    enable_mfa,
    open_books_before_the_first_assertion,
    open_owner_calendar,
)
from tests._two_factor_sign_in import (
    SEED_COMPANION_EMAIL,
    SEED_COMPANION_PASSWORD,
    sign_in_from_a_trusted_browser,
    sign_in_seed_companion,
)

#: ``second_user``'s credentials, in one place: the fixture writes them and a
#: test signing it in passes them.
SECOND_USER_EMAIL = "other@shekel.local"
SECOND_USER_PASSWORD = "otherpass"

#: ``seed_second_user``'s credentials, in one place: the fixture writes them
#: and :func:`second_auth_client` signs in with them.
SEED_SECOND_USER_EMAIL = "second@shekel.local"
SEED_SECOND_USER_PASSWORD = "secondpass12"


@pytest.fixture()
def second_user(app, db):
    """Create a second user for IDOR and cross-user isolation testing.

    Mirrors the shape of seed_user so the two can be used interchangeably,
    two-factor included (plan#3600).

    Returns:
        dict with keys: user, settings, account, scenario, categories.
    """
    user = User(
        email=SECOND_USER_EMAIL,
        password_hash=hash_password(SECOND_USER_PASSWORD),
        display_name="Other User",
    )
    db.session.add(user)
    db.session.flush()

    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    # The owner's opening pay period and the schedule row beside it, from the
    # writer that owns both; see ``_test_helpers.open_owner_calendar`` for
    # the argument.
    bootstrap_period = open_owner_calendar(
        user.id, SEED_USER_BOOTSTRAP_START, cadence_days=SEED_USER_CADENCE_DAYS,
    )[0]

    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=checking_type.id,
            name="Other Checking",
            anchor_balance=Decimal("500.00"),
            # Day one of its own period -- see ``build_seed_user`` in
            # ``tests/_seed_owner_fixtures.py`` for why the origination must
            # not share a civil day with the settles.
            observed_on=bootstrap_period.start_date,
        ),
    )
    # The BOOKS open the day before that assertion -- see ``build_seed_user``
    # in ``tests/_seed_owner_fixtures.py`` for the argument (plan step
    # X-f3c-2b, ruling **R-HG**).
    open_books_before_the_first_assertion(db.session, account)

    scenario = Scenario(
        user_id=user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    categories = []
    for group, item in [
        ("Income", "Salary"),
        ("Home", "Rent"),
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
    enable_mfa(db.session, user.id)

    return {
        "user": user,
        "settings": settings,
        "account": account,
        "scenario": scenario,
        "categories": {c.item_name: c for c in categories},
        "bootstrap_period": bootstrap_period,
    }


# --- Two-User Isolation Fixtures ------------------------------------------


@pytest.fixture()
def seed_second_user(app, db):
    """Create an independent second user for multi-user isolation testing.

    Mirrors seed_user in structure, two-factor included (plan#3600), but
    creates entirely separate objects with distinguishable names and amounts.

    Returns:
        dict with keys: user, settings, account, scenario, categories.
    """
    user = User(
        email=SEED_SECOND_USER_EMAIL,
        password_hash=hash_password(SEED_SECOND_USER_PASSWORD),
        display_name="Second User",
    )
    db.session.add(user)
    db.session.flush()

    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    # The owner's opening pay period and the schedule row beside it, from the
    # writer that owns both; see ``_test_helpers.open_owner_calendar`` for
    # the argument.
    bootstrap_period = open_owner_calendar(
        user.id, SEED_USER_BOOTSTRAP_START, cadence_days=SEED_USER_CADENCE_DAYS,
    )[0]

    # Baseline scenario before the account, as ``seed_user`` orders them; the
    # order is free since plan task plan#3076 (see ``build_seed_user`` in
    # ``tests/_seed_owner_fixtures.py``), and
    # the worked-out ledger reads the second Checking's $2000.00 opening in
    # whichever scenario it is asked for.
    scenario = Scenario(
        user_id=user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=checking_type.id,
            name="Checking",
            anchor_balance=Decimal("2000.00"),
            # Day one of its own period -- see ``build_seed_user`` in
            # ``tests/_seed_owner_fixtures.py`` for why the origination must
            # not share a civil day with the settles.
            observed_on=bootstrap_period.start_date,
        ),
    )
    # The BOOKS open the day before that assertion -- see ``build_seed_user``
    # in ``tests/_seed_owner_fixtures.py`` for the argument (plan step
    # X-f3c-2b, ruling **R-HG**).
    open_books_before_the_first_assertion(db.session, account)

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
    enable_mfa(db.session, user.id)

    return {
        "user": user,
        "settings": settings,
        "account": account,
        "scenario": scenario,
        "categories": {c.item_name: c for c in categories},
        "bootstrap_period": bootstrap_period,
    }


@pytest.fixture()
def seed_second_periods(app, db, seed_second_user):
    """Rebuild the SECOND owner's schedule as 10 pay periods from 2026-01-02.

    :func:`seed_periods` for the isolation fixtures' other owner, through the
    same door and against the same dates, so a cross-user case compares two
    calendars built the one way.

    Returns:
        List of PayPeriod objects, payday ascending.
    """
    return _reset_seed_calendar(
        seed_second_user, date(2026, 1, 2), 10, SEED_USER_CADENCE_DAYS,
    )


@pytest.fixture()
def second_auth_client(app, db, seed_second_user):
    """Provide an authenticated test client for the second user.

    Creates a NEW test client instance to avoid session conflicts
    with the primary auth_client, and signs it in through the shared
    sign-in, on a browser the second user trusts.
    """
    return sign_in_from_a_trusted_browser(
        app.test_client(), SEED_SECOND_USER_EMAIL, SEED_SECOND_USER_PASSWORD,
        OWNERS_PAGE,
    )


@pytest.fixture()
def seed_companion(app, db, seed_user):
    """Create a companion user linked to the seed_user owner.

    The companion has role_id set to the companion role and
    linked_owner_id pointing to the primary seed_user.

    Returns:
        dict with keys: user, settings.
    """
    from app import ref_cache  # pylint: disable=import-outside-toplevel
    from app.enums import RoleEnum  # pylint: disable=import-outside-toplevel

    companion = User(
        email=SEED_COMPANION_EMAIL,
        password_hash=hash_password(SEED_COMPANION_PASSWORD),
        display_name="Companion User",
        role_id=ref_cache.role_id(RoleEnum.COMPANION),
        linked_owner_id=seed_user["user"].id,
    )
    db.session.add(companion)
    db.session.flush()

    settings = UserSettings(user_id=companion.id)
    db.session.add(settings)
    db.session.commit()

    return {
        "user": companion,
        "settings": settings,
    }


@pytest.fixture()
def companion_client(app, db, seed_companion):
    """A browser signed in as the companion, on a browser she trusts.

    Ruling plan#3434: a companion without two-factor is held at its setup, so
    she gets it first (:func:`tests._two_factor_sign_in.sign_in_seed_companion`);
    ``seed_companion`` alone stays without it.
    """
    return sign_in_seed_companion(app)
