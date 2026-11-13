"""Owners and their pay schedules: two-factor turned on, a registration and its form, and a
schedule's rhythm, shift and eras.
"""

from datetime import date as _real_date, datetime as _real_datetime, timezone as _real_timezone

from app.enums import BusinessDayShiftEnum
from app.models.user import MfaConfig
from app.services import mfa_service, pay_era_write, pay_rhythm, pay_schedule_service


def enable_mfa(db_session, user_id, backup_codes=()):
    """Turn two-factor on for *user_id* with a fresh secret, and commit.

    A FRESH secret per call, so two users (or two tests) never share a
    replay-prevention step.  The one enrolment of a test account: the private
    copies nine files carried before plan#2173 wrote it here are gone
    (plan#3600).

    **Every shared test owner is made with two-factor** (plan#3600): the
    fixture that creates it calls this, as production holds an account without
    it at setup.  The shared companion, ``seed_companion``, is made without it,
    as her owner makes her.  So a test calls this for her, for an account it
    made itself, or to turn a reset owner's row on again (below), and reads a
    shared owner's secret with
    :func:`tests._two_factor_sign_in.two_factor_secret_of`.

    Args:
        db_session: The test ``db.session``.
        user_id: The ``auth.users.id`` to enrol.
        backup_codes: Plaintext backup codes to store hashed; none by default.

    Returns:
        The plaintext base32 secret, for ``pyotp.TOTP(secret)`` codes.

    An account whose two-factor a reset took away
    (``tests._two_factor_sign_in.reset_two_factor_of``) keeps its disabled
    row, and this turns that row on again, as setting it up again does.

    Raises:
        AssertionError: *user_id* already has two-factor turned on.
    """
    row = db_session.query(MfaConfig).filter_by(user_id=user_id).one_or_none()
    assert row is None or not row.is_enabled, (
        f"user {user_id} already has two-factor: every shared test account is "
        f"made with it, so read its secret with "
        f"tests._two_factor_sign_in.two_factor_secret_of({user_id})"
    )
    if row is None:
        row = MfaConfig(user_id=user_id)
        db_session.add(row)
    secret = mfa_service.generate_totp_secret()
    row.is_enabled = True
    row.totp_secret_encrypted = mfa_service.encrypt_secret(secret)
    row.backup_codes = mfa_service.hash_backup_codes(list(backup_codes), rounds=4)
    row.confirmed_at = _real_datetime.now(_real_timezone.utc)
    db_session.commit()
    return secret


def store_backup_codes(db_session, user_id, backup_codes):
    """Give *user_id*'s two-factor row *backup_codes*, hashed, in place of its own, and commit.

    For a test of a backup code on a shared account, which is made with
    two-factor and no backup codes (:func:`enable_mfa`).

    Args:
        db_session: The test ``db.session``.
        user_id: The ``auth.users.id`` whose row to change; it has two-factor.
        backup_codes: The plaintext backup codes the row then holds.
    """
    row = db_session.query(MfaConfig).filter_by(user_id=user_id, is_enabled=True).one()
    row.backup_codes = mfa_service.hash_backup_codes(list(backup_codes), rounds=4)
    db_session.commit()


def register_form_data(**overrides):
    """Return a complete, valid POST body for ``/register``.

    **One place that knows what the registration form requires**, added at plan
    step X-ad-a when that grew from four fields to seven: the form now asks for
    the owner's real pay schedule because registration stopped inventing one
    (ruling **R-DB**, finding **N-123**).  Twenty-odd call sites across four
    test modules post this body, and a field added to
    ``RegisterSchema`` without a home here would otherwise be twenty-odd edits
    -- which is the shape of test churn that ends in a suite where half the
    posts are subtly different for no stated reason.

    ``last_payday`` is the USER's today (``display_today``, ruling R-DH (b)),
    never ``date.today()``: the service bounds it against the same clock, and a
    process pinned to UTC is already tomorrow at 8pm Eastern -- so a
    process-clock default would be a FUTURE payday, refused, for four hours
    every day.  Today is the safest point in the accepted window
    ``[today - cadence + 1, today]`` because it is valid at every cadence.

    Args:
        **overrides: Field values to replace or add.  Pass ``email`` and
            ``display_name`` per test; pass a field explicitly to exercise its
            refusal (e.g. ``last_payday=""`` for the missing-date path).

    Returns:
        A ``dict`` suitable for ``client.post("/register", data=...)``.
    """
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.config import BaseConfig
    from app.utils.dates import display_today
    body = {
        "email": "newuser@example.com",
        "display_name": "New User",
        "password": "securepass123",
        "confirm_password": "securepass123",
        "last_payday": display_today().isoformat(),
        # The cadence-kind control's three radio arms and every arm's boxes
        # (plan step pay_calendar:C17-d-3, ruling R-PC84): a browser posts
        # the checked arm's token AND every rendered box, the unchosen arms'
        # blank.  Spelled by ``cadence_form_values`` so this body and the
        # settings doors' cases state the wire one way.
        **cadence_form_values(),
        # The payday convention the form's <select> renders (plan step
        # pay_calendar:C14-b), as the ``ref.business_day_shifts`` id that
        # control's chosen <option> carries -- a browser posts an id, not an
        # enum member, and a body that posted anything else would exercise a
        # payload no browser can produce.  ``none`` is what the page
        # preselects for a new owner.
        "shift": str(ref_cache.business_day_shift_id(BusinessDayShiftEnum.NONE)),
        "num_periods": str(BaseConfig.DEFAULT_PAY_PERIOD_HORIZON),
        # EMPTY, because that is what a browser submits for the optional
        # pay-history date nobody touched (plan step balance:X-bh-2): an HTML
        # form posts every control it renders, so a body that omitted the key
        # would exercise a payload no browser can produce.  The schema maps it
        # to ``None``, which means NOT STATED -- the engine counts only the
        # paydays the app records.
        "history_opens_on": "",
    }
    body.update(overrides)
    return body


def cadence_form_values(cadence=None):
    """Return what a schedule form's cadence-kind control and its arms post.

    Plan step ``pay_calendar:C17-d-3``, ruling **R-PC84**: the four doors
    render three RADIO ARMS -- every N days, monthly on a day, twice a month
    on two days -- each holding its own number boxes, and a browser submits
    the checked arm's token under ``cadence_kind`` together with EVERY
    rendered box, the unchosen arms' boxes blank (the every-N-days box
    carries the app's default whichever arm is checked, because the form
    pre-fills it).  A body that posted only the chosen arm would exercise a
    payload no browser can produce, which is the defect class
    :func:`register_form_data`'s own docstring records.

    **The tokens and keys are spelled HERE by hand, not read off the
    schema's wire table**, so a test posting this body grades the
    application's spelling against an independent one: a token or a key
    that drifted in ``_pay_rhythm`` or in the macro would fail here rather
    than agree with itself.

    Args:
        cadence: The :class:`~app.services.pay_rhythm.FixedDays`,
            :class:`~app.services.pay_rhythm.Monthly` or
            :class:`~app.services.pay_rhythm.SemiMonthly` the owner states;
            ``None`` means the form's untouched default, every
            ``DEFAULT_PAY_CADENCE_DAYS`` days.

    Returns:
        dict -- the five wire keys, every value a string as a form posts it.
    """
    # pylint: disable=import-outside-toplevel
    from app.config import BaseConfig
    from app.services.pay_rhythm import FixedDays, Monthly, SemiMonthly
    if cadence is None:
        cadence = FixedDays(BaseConfig.DEFAULT_PAY_CADENCE_DAYS)
    body = {
        "cadence_kind": "fixed_days",
        "cadence_days": str(BaseConfig.DEFAULT_PAY_CADENCE_DAYS),
        "day_of_month": "",
        "first_day_of_month": "",
        "second_day_of_month": "",
    }
    if isinstance(cadence, FixedDays):
        body["cadence_days"] = str(cadence.days)
    elif isinstance(cadence, Monthly):
        body["cadence_kind"] = "monthly"
        body["day_of_month"] = str(cadence.day)
    elif isinstance(cadence, SemiMonthly):
        lower, upper = cadence.days
        body["cadence_kind"] = "semi_monthly"
        body["first_day_of_month"] = str(lower)
        body["second_day_of_month"] = str(upper)
    else:
        raise TypeError(f"cadence_form_values() got {cadence!r}, not a cadence")
    return body


def shift_form_value(shift=BusinessDayShiftEnum.NONE):
    """Return what a schedule form's payday-convention ``<select>`` posts.

    The ``ref.business_day_shifts`` id, as a string, because that is what a
    browser sends for the chosen ``<option>`` -- the four schedule forms render
    the control from ``PAYDAY_SHIFT_OPTIONS``, whose values are ids (plan step
    ``pay_calendar:C14-b``).  A test body that posted a member name or omitted
    the key would exercise a payload no browser can produce, which is the
    defect class ``register_form_data``'s own docstring records.

    Args:
        shift: The convention the control is set to, defaulting to
            :attr:`~app.enums.BusinessDayShiftEnum.NONE` -- what every form
            preselects for an owner who has stated nothing.

    Returns:
        str -- the id to put in a form body.
    """
    return str(shift_id_of(shift))


def shift_id_of(shift=BusinessDayShiftEnum.NONE):
    """Return the ``ref.business_day_shifts.id`` a member is stored as.

    For the handful of tests that build a
    :class:`~app.models.pay_era.PayEra` row DIRECTLY rather than through
    ``pay_era_write.mint_era`` -- constraint cases, which need a row
    the database will accept in every respect except the one under test.
    ``shift_id`` is ``NOT NULL`` with no server default (plan step
    ``pay_calendar:C14-b``; the column moved from the schedule row to the era
    at ``C17-a``), so a row built without it fails on the wrong constraint
    and the case passes for the wrong reason.

    Args:
        shift: The convention, defaulting to
            :attr:`~app.enums.BusinessDayShiftEnum.NONE`.

    Returns:
        int -- the ``ref`` id.
    """
    # pylint: disable=import-outside-toplevel
    from app import ref_cache

    return ref_cache.business_day_shift_id(shift)


def rhythm_of(cadence_days, shift=BusinessDayShiftEnum.NONE):
    """Return a :class:`~app.services.pay_rhythm.Rhythm`.

    Plan step ``pay_calendar:C14-b`` made the pay-schedule writers take the
    cadence and the payday convention as ONE value, because the two carry a
    joint rule and a row written through two statements passes through a state
    neither statement means.  Most tests in this suite are about the paydays
    rather than the convention, so this defaults the second half to ``none`` --
    which is what every schedule holds until an owner answers (**R-PC56**) and
    therefore what those tests were already exercising.

    A helper rather than the constructor spelled out at each of the call sites,
    for the reason ``seed_periods`` gives of the batch it wraps: the default is
    stated ONCE, so the day a test needs a real convention it passes one and
    every other case keeps reading as a cadence.

    **It builds a FIXED-DAYS rhythm**, which since plan step
    ``pay_calendar:C17-d-1`` is one kind of cadence value
    (:class:`~app.services.pay_rhythm.FixedDays`) rather than a bare day
    count; a test about a day-of-month kind (``C17-d-2``) states its
    :class:`~app.services.pay_rhythm.Rhythm` outright.  ``None`` builds
    ``FixedDays(None)`` -- a fixed-days cadence with NO day count, which is
    what the "absent cadence" refusal cases mean by absence; a bare ``None``
    where the VALUE should be is a different state with its own test
    (``TestTheCadenceIsAValueOfAKnownKind``, in
    ``test_pay_calendar_derivation/test_what_the_derivation_refuses.py``).

    Args:
        cadence_days: Days between the paydays.
        shift: The convention, defaulting to
            :attr:`~app.enums.BusinessDayShiftEnum.NONE`.  Pass a displacing
            member only in a test that is ABOUT the convention -- the write
            door refuses one on a cadence below
            :func:`~app.utils.business_days.shortest_collision_free_cadence`.

    Returns:
        The rhythm.
    """
    return pay_rhythm.Rhythm(
        cadence=pay_rhythm.FixedDays(cadence_days), shift=shift,
    )


def era_of(effective_from, cadence_days, shift=BusinessDayShiftEnum.NONE):
    """Return a fixed-days :class:`~app.services.pay_rhythm.Era`.

    :func:`rhythm_of`'s sibling for the era relation (plan step
    ``pay_calendar:C17-a``): the rhythm plus the day it took effect, which is
    also the grid's phase.  The kind is the cadence value's type since plan
    step ``C17-d-1``, so :func:`rhythm_of` states it.

    Args:
        effective_from: The era's first nominal payday.
        cadence_days: Days between the paydays.
        shift: The convention, defaulting to
            :attr:`~app.enums.BusinessDayShiftEnum.NONE` for
            :func:`rhythm_of`'s reason.

    Returns:
        The era.
    """
    return pay_rhythm.Era(
        effective_from=effective_from,
        rhythm=rhythm_of(cadence_days, shift),
    )


#: The phase :func:`eras_of` gives an EMPTY payday set's era.  An empty
#: calendar reads no phase -- there is no last period to close and nothing to
#: project past -- so the day is arbitrary, and it is named rather than drawn
#: from a clock so the value is the same on every run.
EMPTY_CALENDAR_PHASE = _real_date(2026, 1, 2)


def eras_of(paydays, cadence_days, shift=BusinessDayShiftEnum.NONE):
    """Return ONE era phased on the earliest of *paydays*, as a sequence.

    The pure calendar's era argument since plan step ``pay_calendar:C17-b-2``
    (:meth:`PayCalendar.from_paydays` takes the owner's eras where it took a
    :class:`~app.services.pay_rhythm.Rhythm`).  A test that states paydays and
    a cadence means "this owner has been paid every *cadence_days* since
    their first payday", which is exactly the era the migration backfilled
    for every existing owner -- so phasing the era on the earliest payday
    keeps every regular fixture's derivation what it was, and moves exactly
    the last end of a fixture whose last payday is OFF that grid, which is
    what that step does to a real owner.  A test ABOUT eras states them
    outright with :func:`era_of`.

    Args:
        paydays: The ``(period_id, payday)`` pairs the calendar is built
            from, in any order; may be empty, in which case the era's phase
            is :data:`EMPTY_CALENDAR_PHASE`.
        cadence_days: Days between the paydays.
        shift: The convention, defaulting to
            :attr:`~app.enums.BusinessDayShiftEnum.NONE` for
            :func:`rhythm_of`'s reason.

    Returns:
        A one-element tuple holding the era.
    """
    days = [payday for _period_id, payday in paydays]
    return (era_of(min(days) if days else EMPTY_CALENDAR_PHASE, cadence_days, shift),)


def mint_fixture_era(user_id, effective_from, cadence_days,
                     shift=BusinessDayShiftEnum.NONE):
    """Give *user_id* a schedule row and ONE era, without recording a payday.

    The state every payday-holding owner has, for a fixture that then writes
    its ``budget.pay_periods`` rows BY HAND -- a corrupt shape a checker must
    catch.  (A calendar-monthly schedule was the other reason until plan
    step ``pay_calendar:C17-d-2`` gave it a door, ledger row **P78**.)
    ``fk_pay_periods_schedule`` needs the row and a calendar needs the era;
    the ordinary fixtures go through ``record_paydays``, which does both,
    and this is the one line for the fixtures that cannot.

    Plan step ``pay_calendar:C17-a``: it replaces the
    ``upsert_schedule(user_id, rhythm, None)`` line those fixtures carried,
    and it takes the era's day because an era has one.  The extend door steps
    from that day and the era rule tests a batch's first payday against its
    grid, so a fixture that later EXTENDS or RECORDS through the writer must
    state a day on the grid it means -- and since plan step ``C17-b-2`` so
    must a fixture that is only READ: the derivation closes the last period
    and projects past it on the era's grid, so a hand-built schedule whose
    paydays are off the day stated here derives a last end on the grid, not
    one cadence past its last row.

    Args:
        user_id: The owner.
        effective_from: The era's first nominal payday.
        cadence_days: Days between the paydays.
        shift: The convention, defaulting to ``none``.

    Returns:
        The minted :class:`~app.models.pay_era.PayEra` row, flushed.
    """
    pay_schedule_service.ensure_schedule_row(user_id)
    return pay_era_write.mint_era(
        user_id, era_of(effective_from, cadence_days, shift),
    )


def restate_fixture_era(user_id, effective_from, cadence_days,
                        shift=BusinessDayShiftEnum.NONE):
    """Replace every era *user_id* holds with ONE, without touching a payday.

    The fixture form of "force the owner's stored cadence" for a case that
    proves a reader takes the RHYTHM from storage rather than inferring it
    from the rows: the paydays stay exactly where they are and only the era
    moves.  Before plan step ``pay_calendar:C17-a`` that was one
    ``upsert_schedule`` line; an era is a row per rhythm, so the honest
    equivalent retires the owner's eras and mints the one the case states.

    Args:
        user_id: The owner.
        effective_from: The era's first nominal payday.
        cadence_days: Days between the paydays.
        shift: The convention, defaulting to ``none``.

    Returns:
        The minted :class:`~app.models.pay_era.PayEra` row, flushed.
    """
    pay_schedule_service.ensure_schedule_row(user_id)
    pay_era_write.retire_eras(user_id, ())
    return pay_era_write.mint_era(
        user_id, era_of(effective_from, cadence_days, shift),
    )


def strip_owner_schedule(db_session, user_id):
    """Leave *user_id* as an owner who has never generated a schedule.

    Every payday, every era and the ``budget.pay_schedule`` row go, in the
    only order the keys admit: ``fk_pay_periods_schedule`` and, since plan
    step ``pay_calendar:C17-a``, ``fk_pay_eras_schedule`` are both
    ``ON DELETE RESTRICT``, so the children go before the parent.  Six cases
    each spelled the first two deletes by hand before the era relation added a
    third child; this is the one statement of the order so the seventh
    cannot copy it wrongly.

    Bulk deletes with ``synchronize_session=False``, so an instance the case
    already loaded is not touched; the case commits or reads back as it
    needs to.

    Args:
        db_session: The test ``db.session``.
        user_id: The owner to strip.
    """
    # pylint: disable=import-outside-toplevel
    from app.models.pay_era import PayEra
    from app.models.pay_period import PayPeriod
    from app.models.pay_schedule import PaySchedule

    for model in (PayPeriod, PayEra, PaySchedule):
        db_session.query(model).filter_by(user_id=user_id).delete(
            synchronize_session=False,
        )


def registration_spec(**overrides):
    """Return a complete, valid :class:`RegistrationSpec` for service tests.

    The service-tier twin of :func:`register_form_data`, and it exists for the
    same reason: ``registration_service.register_user`` takes one value object whose
    pay-calendar half arrived at plan step X-ad-a, and the tests that call it
    directly should not each restate what a valid sign-up looks like.

    **``cadence_days`` and ``shift`` stay spellable as overrides**, though the
    spec itself carries the pair as one
    :class:`~app.services.pay_rhythm.Rhythm` since plan step
    ``pay_calendar:C14-b``.  A case about the cadence is not a case about the
    convention, and making every such case name both halves would have put the
    default in each of them; assembled here, the default is stated once.  Pass
    ``rhythm=`` directly to state the pair outright.

    Args:
        **overrides: Spec fields to replace.  ``first_payday`` defaults to the
            user's today -- see :func:`register_form_data` for why the clock
            matters.  ``cadence_days`` and ``shift`` are accepted as halves of
            the rhythm and may not be combined with an explicit ``rhythm``.

    Returns:
        The :class:`~app.services.registration_service.RegistrationSpec`.

    Raises:
        TypeError: Both ``rhythm`` and one of its halves were given, which
            would leave which one wins to the order this function happens to
            apply them in.
    """
    # pylint: disable=import-outside-toplevel
    from app.config import BaseConfig
    from app.services.registration_service import RegistrationSpec
    from app.utils.dates import display_today
    halves = {
        key: overrides.pop(key)
        for key in ("cadence_days", "shift") if key in overrides
    }
    if halves and "rhythm" in overrides:
        raise TypeError(
            f"registration_spec() got rhythm and {sorted(halves)}; state the "
            f"pair one way or the other."
        )
    fields = {
        "email": "newuser@example.com",
        "password": "securepass123",
        "display_name": "New User",
        "first_payday": display_today(),
        "rhythm": rhythm_of(
            halves.get("cadence_days", BaseConfig.DEFAULT_PAY_CADENCE_DAYS),
            halves.get("shift", BusinessDayShiftEnum.NONE),
        ),
        "num_periods": BaseConfig.DEFAULT_PAY_PERIOD_HORIZON,
        # The column's own default (plan step balance:X-bh-2): a sign-up that
        # skips the question has stated NOTHING, and the engine counts only
        # that owner's recorded paydays.  A case about the field passes its
        # own value.
        "history_opens_on": None,
    }
    fields.update(overrides)
    return RegistrationSpec(**fields)
