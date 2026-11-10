"""
Shekel Budget App -- Registration: one sign-up builds a whole owner.

Everything a new owner needs before they can be shown a screen: the ``User``
row and its settings, the REAL pay calendar they stated, a baseline scenario,
the default Checking account and the category tree.  (It copied the tax law
into per-user rows too until plan step salary:X-at-1; the law has one home,
:mod:`app.tax_law`, and nothing reads the copies already made, which stay in
their tables until plan step salary:X-at-2 drops them.)
:func:`register_user` is the one door, and
:class:`RegistrationSpec` is what a sign-up says.

**Why it is a module of its own, and it is plan step ``pay_calendar:C14-e-3``
that forced the question** (developer, 2026-09-06).  This lived in
:mod:`app.services.auth_service`, which is now two subjects in one file: an
IDENTITY tier -- hashing, verification, the HIBP breach check, the lockout
counters, the sign-in password checks and ``change_password`` -- and this one, which
builds an owner's whole financial baseline and touches eight tables.  The two
share nothing but :func:`~app.services.auth_service.hash_password`, which this
module imports and nothing here is imported back.

**The pressure that surfaced it was the pylint ceiling and the pressure is not
the reason.**  ``auth_service.py`` stood at 971 lines of a 1,000-line maximum
when ``C14-e-3`` had to widen the sign-up payday refusal, and ledger row
**pay_calendar:PC-498** says the remedy for that ceiling is a SPLIT and not a
trim -- a trim deletes argument, which is how the file came to be full.  Four
placements were costed and the developer took this one over moving the single
refusal to :mod:`app.services.pay_schedule_service`, over raising the ceiling,
and over trimming: the ceiling is a forcing function that had by then caught
over-writing twice in one arc, and the split is a DISTINCTION -- who you are
against what you own -- rather than a filing decision made to fit.

**The refusals are asked HERE, before the ``User`` row exists**, which is
:func:`register_user`'s standing property: a validation that fired later would
leave a partly-built owner in the session.  Four of the five belong to
:mod:`app.services.pay_schedule_service` and one,
:func:`_reject_impossible_first_payday`, belongs to this module because
registration is the only door that asks *which day were you last paid* against
a clock.

Pure of Flask: it takes a value object and returns a ``User``.  The session is
:mod:`app.extensions`' and the caller commits.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.category import Category
from app.models.scenario import Scenario
from app.models.user import User, UserSettings
from app.exceptions import ConflictError, ValidationError
from app.services import (
    account_service,
    auth_service,
    pay_calendar,
    pay_era_write,
    pay_period_batch,
    pay_period_write,
    pay_rhythm,
    pay_schedule_service,
)
from app.utils import business_days
from app.utils.dates import display_today

logger = logging.getLogger(__name__)


DEFAULT_CATEGORIES = [
    ("Income", "Salary"),
    ("Income", "Other Income"),
    ("Home", "Mortgage/Rent"),
    ("Home", "Electricity"),
    ("Home", "Gas"),
    ("Home", "Water"),
    ("Home", "Internet"),
    ("Home", "Phone"),
    ("Home", "Home Insurance"),
    ("Auto", "Car Payment"),
    ("Auto", "Car Insurance"),
    ("Auto", "Fuel"),
    ("Auto", "Maintenance"),
    ("Family", "Groceries"),
    ("Family", "Dining Out"),
    ("Family", "Spending Money"),
    ("Family", "Subscriptions"),
    ("Health", "Medical"),
    ("Health", "Dental"),
    ("Financial", "Savings Transfer"),
    ("Financial", "Extra Debt Payment"),
    ("Transfers", "Incoming"),
    ("Transfers", "Outgoing"),
    ("Credit Card", "Payback"),
]


def _reject_impossible_first_payday(
    first_payday: date, rhythm: pay_rhythm.Rhythm, today: date,
) -> None:
    """Refuse a sign-up payday the owner could not have been LAST paid on.

    Registration asks for the owner's MOST RECENT payday, and the rule is one
    sentence: **the paycheck that payday opens must still be running.**  That
    is what makes the rest of registration work with no second step -- sign-up
    day falls inside period 0, so the default account's opening assertion,
    dated today, has a real paycheck to post its correction into rather than
    reaching
    :meth:`~app.services.pay_calendar.PayCalendar.filing_period`'s clamp.

    Each end refuses for its own reason.  A payday whose money has not landed
    is not one that has happened; a paycheck that has already ENDED means a
    later payday arrived that the owner did not name -- and the message
    names it (ruling **R-PC83**, plan step ``pay_calendar:C17-d-2``): the
    payday on the owner's STATED rhythm whose paycheck covers today, which
    is the grid's own spelling ``nominal_payday(first_payday, cadence,
    cadence_steps_to(first_payday, cadence, E - 1))`` -- the last grid day
    strictly before ``E``, the earliest nominal day paid after today.  For a
    day-of-month rhythm that day is the ONE legal answer, the grid being
    fixed by the stated day; for a fixed-days rhythm it is one legal answer
    among several (every first payday from ``E - days`` up to the last one
    paid on or before today), and the message that used to name that set's
    lower bound (``E - cadence_days``, a spelling no month grid has) was
    ruled replaced by the one every kind shares rather than kept beside a
    second for the month kinds.  **The refusal SET is unchanged**: on a
    fixed-days grid ``first_payday + days >= E`` exactly when the last grid
    day before ``E`` is *first_payday* itself.

    **It asks the DERIVATION where that paycheck runs, and that is plan step
    ``pay_calendar:C14-e-3``.**  The rule was arithmetic here --
    ``[first_payday, first_payday + cadence_days - 1]``, accepting
    ``[today - cadence_days + 1, today]`` -- one of the spellings
    :func:`~app.services.pay_calendar.projected_payday`'s census names, agreeing
    with the derivation only while no payday could move.  Same repair ``C14-d``
    made to ``pay_period_batch.reject_backward_payday``.  What the old
    spelling cost is measured in that step's own entry.

    **The lower refusal's message is deliberately about the paycheck, not about
    the owner's arithmetic.**  It read "one of the two does not match the
    other" until an adversarial review of plan step X-ad-a found a user whose
    two answers match perfectly: on payday itself, before the deposit posts,
    "the day money landed" is honestly one cadence ago.  The form's wording was
    the real defect and it was fixed there.

    Args:
        first_payday: The stated most recent payday, read as a day on the
            owner's NOMINAL grid -- the same reading
            ``pay_period_batch.requested_paydays`` gives it, since this value
            becomes both the batch's phase and the first era's
            ``effective_from`` (plan step ``pay_calendar:C17-a``).
        rhythm: The stated cadence and payday convention
            (:class:`~app.services.pay_rhythm.Rhythm`).  ``register_user`` asks
            ``reject_out_of_range_cadence``, ``reject_phase_off_grid`` and
            ``reject_shift_on_short_cadence`` of the pair BEFORE this, so no
            producer call below can be handed an out-of-range question or a
            first payday off the stated grid.
        today: The owner's civil today, read once by the caller so this bound
            and the opening assertion's day cannot straddle midnight.

    Returns:
        The day the schedule will RECORD -- *first_payday* displaced under
        *rhythm*'s convention, resolved here and threaded to the next refusal
        rather than recomputed.  They stopped being one day at ``C14-e-3``.

    Raises:
        ValidationError: The paycheck *first_payday* opens has not started or
            has already ended.  Each message names the offending day and the
            bound it broke.
    """
    opens = pay_calendar.projected_payday(first_payday, rhythm, 0)
    if opens > today:
        raise ValidationError(
            f"Your most recent payday cannot be {first_payday.isoformat()}: "
            f"money lands on {opens.isoformat()} for that paycheck, which has "
            f"not happened yet (today is {today.isoformat()}).  Enter the date "
            f"of the paycheck you have already been paid."
        )
    # **The boundary is ONE value.**  ``projected_payday(fp, rhythm, 1) <=
    # today`` says the same thing, but the message must NAME the payday that
    # works -- two expressions held equal by a monotonicity argument, rule
    # 14's tell.  They ARE equal (both conventions are monotone, so
    # ``shift(next grid day after fp) > today`` exactly when the last grid
    # day before E is fp itself), and the named day is the grid's own:
    # the last day on the owner's stated grid strictly before E, whose
    # paycheck is the one covering today (R-PC83).
    horizon = business_days.earliest_nominal_paid_after(today, rhythm.shift)
    cadence = rhythm.cadence
    covering = pay_calendar.nominal_payday(
        first_payday, cadence,
        pay_calendar.cadence_steps_to(
            first_payday, cadence, horizon - timedelta(days=1),
        ),
    )
    if first_payday < covering:
        closes = pay_calendar.projected_payday(first_payday, rhythm, 1)
        raise ValidationError(
            f"The paycheck starting {opens.isoformat()} has already "
            f"ended: paid {cadence.phrase}, it covered through "
            f"{(closes - timedelta(days=1)).isoformat()}.  On that rhythm "
            f"the paycheck covering today opened on {covering.isoformat()}; "
            f"enter that payday."
        )
    return opens


@dataclass(frozen=True)
class RegistrationSpec:
    """Everything one sign-up states: an identity, and a real pay calendar.

    **The pay-calendar half is plan step X-ad-a** (ruling **R-DB**, and the
    cross-arc fork ruled to it on 2026-08-09).  Registration used to FABRICATE
    a pay period covering ``[today, today + 13]`` because
    :func:`app.services.account_service.create_account` refuses an owner with
    no pay period -- and that invented payday is what then blocked the owner's
    real one: the forward-only batch guard of the day
    (``pay_period_service._reject_overlapping_batch``, replaced at plan step
    C3-b by ``pay_period_write._reject_backward_payday``, now
    ``pay_period_batch.reject_backward_payday``) refused any batch
    starting on or before the latest existing ``end_date``, so ``today + 1``
    through ``today + 13`` were REFUSED outright and every date from
    ``today + 14`` on left a permanent hole in the calendar (finding
    **N-123**).  The remedy is not a better guess: it is to ask.

    Frozen, so a constructed spec is an immutable record of one sign-up
    request, and modelled on
    :class:`app.services.account_service.AccountSpec` for the same reason --
    seven co-loaded values are one concept, not a keyword list.

    **No field carries a default, deliberately.**  ``first_payday`` has no
    defensible one, and giving the other two a default here would put a second
    copy of the app's biweekly premise below the one place that states it
    (``BaseConfig.DEFAULT_PAY_CADENCE_DAYS`` / ``DEFAULT_PAY_PERIOD_HORIZON``,
    applied by the Marshmallow layer and by ``scripts/seed_user.py``).  This
    module reads no Flask config by design -- see the module docstring.
    **That holds for ``history_opens_on`` too, and it is the field where the
    rule is least obvious**: ``None`` is its commonest value and would look
    like a harmless default.  It is now the SAFE reading -- not stated, so
    count only the record -- but a caller still has to reach it deliberately,
    because the field is the difference between an owner who answered and one
    who was never asked.

    Attributes:
        email: The user's email address.  Stripped and lowercased by
            :func:`register_user`, which also re-validates its shape.
        password: The plaintext password (12 characters minimum, 72 UTF-8
            bytes maximum -- bcrypt's hard input cap).
        display_name: The user's display name; stripped, and required to be
            non-empty after stripping.
        first_payday: The owner's most recent SCHEDULED payday -- the day on
            their pay schedule's grid, which under a displacing convention
            is not the day the money moved (plan step ``pay_calendar:C17-c-2a``,
            ledger row **PC-504**; the form says so) -- which opens their
            schedule.  **Last, never next**, and that is the 2026-08-09
            ruling: an answer whose PAYCHECK still covers sign-up day has
            nothing to overlap on an empty calendar, so the default account's
            opening balance has a paycheck to post into and the owner needs no
            second step.  A NEXT payday would leave sign-up day uncovered and
            the opening correction would reach
            :meth:`~app.services.pay_calendar.PayCalendar.filing_period`'s
            clamp.  *The window was spelled ``[today - cadence_days + 1,
            today]`` here until ``pay_calendar:C14-e-3``, arithmetic the
            derivation stopped agreeing with once a payday can move;
            :func:`_reject_impossible_first_payday` owns the rule.*
        rhythm: How often the owner is paid and what their payroll does when
            a payday lands on a weekend or a federal holiday
            (:class:`~app.services.pay_rhythm.Rhythm`).  Persisted
            as the owner's schedule, so extend and the rolling top-up have
            both halves to continue from.  *They used to infer a cadence where
            it was absent -- pay-calendar finding **P8** -- which plan step
            C4-b-2 closed by making the absence unstorable.*  It arrives as
            the PAIR rather than as two fields since plan step **C14-b**
            (ruling **R-PC56**: the convention is asked wherever a cadence
            is), because the two carry a joint rule: a convention that
            displaces a payday needs a cadence longer than the longest run of
            closed days, which
            :func:`~app.services.pay_schedule_service.reject_shift_on_short_cadence`
            refuses in :func:`register_user`'s up-front block.  Neither half
            has a default, and the rule is sharpest for the convention:
            ``NONE`` is both the commonest answer and the value that means
            *nobody has told us*, so a default would state as fact what no
            owner was asked -- the error ruling ``balance:R-IF`` was written
            to correct.
        num_periods: How many periods to generate forward from
            *first_payday*.
        history_opens_on: How far back the owner's paychecks reach, or ``None``
            for NOT STATED, which counts only the recorded paydays (plan step
            **balance:X-bh-2**, ruling **balance:R-IA** as amended
            2026-08-31).  The FLOOR on the
            backward payday rhythm the paycheck engine counts a month position
            and a year-to-date over.  It may not fall after *first_payday*:
            paychecks cannot have begun after the most recent one.  Asked here
            because the app knows the CADENCE and cannot derive when a job
            began, and asked at SIGN-UP because the form is already asking the
            other two halves of the same rhythm.
    """

    email: str
    password: str
    display_name: str
    first_payday: date
    rhythm: pay_rhythm.Rhythm
    num_periods: int
    history_opens_on: "date | None"


def register_user(spec: RegistrationSpec):
    """Register a new user with default settings, a pay calendar, and a baseline.

    Creates a User, UserSettings (with model defaults), the owner's pay
    periods and schedule row, a baseline Scenario, the default Checking
    account with its opening assertion and the default categories --
    atomically.  Does NOT commit; the caller owns the
    transaction.

    **Every refusal happens before the ``User`` row is added**, which is a
    property worth stating: a validation that fired later would leave a
    partly-built owner in the session, protected only by the fact that no
    caller commits after catching.  The pay-calendar inputs are checked in the
    same block as the email and the password for exactly that reason.

    Args:
        spec: The :class:`RegistrationSpec` for this sign-up.

    Returns:
        The newly created User object (settings, periods, schedule, scenario,
        account and categories are attached to the same session).

    Raises:
        ValidationError: The email format is invalid, the display name is
            empty, the password is too short or too long, the cadence falls
            outside ``ck_pay_schedule_cadence_range``, the stated payday is
            not one the owner could have been LAST paid on -- in the future,
            or more than one cadence back -- or the stated pay-history opening
            falls outside ``ck_pay_schedule_history_opens_range`` or after that
            payday.
        ConflictError: A user with the given email already exists.
    """
    # Sanitize inputs.
    email = spec.email.strip().lower()
    display_name = spec.display_name.strip()

    # Validate email format.
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValidationError("Invalid email format.")

    # Validate display name is not empty.
    if not display_name:
        raise ValidationError("Display name is required.")

    # Validate password length.
    if len(spec.password) < 12:
        raise ValidationError("Password must be at least 12 characters.")
    if len(spec.password.encode("utf-8")) > 72:
        raise ValidationError("Password is too long. Please use 72 characters or fewer.")

    # The clock is the USER's, not the process's (ruling R-DH (b)), and it is
    # read ONCE: this same day bounds the stated payday below and dates the
    # opening assertion further down, so the two cannot land on different
    # civil days when a request straddles midnight in the display zone.
    today = display_today()
    # Both write doors' preconditions, asked HERE rather than where they fire,
    # so the claim above is true: the schedule's own bounds (what
    # ``budget.pay_schedule`` may store) and the batch's (how much of one
    # schedule ``record_paydays`` will materialise in a single call), then this
    # module's own question about the day.  Asking them late would let a bad
    # cadence or a zero horizon refuse several statements after the ``User``
    # row exists, under a message about accounts rather than about the input.
    pay_schedule_service.reject_out_of_range_cadence(spec.rhythm.cadence)
    # The stated payday must be a day the stated rhythm pays on (plan step
    # ``pay_calendar:C17-d-2``), asked after the bound admitted the cadence
    # and BEFORE the refusal below runs the grid from that day: a
    # day-of-month rhythm stated from a day it does not pay would otherwise
    # be judged on a grid the owner never stated.  ``record_paydays`` re-asks
    # it in its own precondition block, as it re-asks the two beside it.
    pay_era_write.reject_phase_off_grid(spec.first_payday, spec.rhythm.cadence)
    # The rhythm is refused as a PAIR before the ``User`` row exists.
    # ``record_paydays`` re-asks it as the writer's own rule; asking here is
    # what keeps this block the whole of registration's refusals, exactly as
    # the history-opening pair below.
    pay_schedule_service.reject_shift_on_short_cadence(spec.rhythm)
    pay_schedule_service.reject_out_of_range_history_opening(
        spec.history_opens_on,
    )
    pay_period_batch.reject_out_of_range_batch_size(spec.num_periods)
    opening_payday = _reject_impossible_first_payday(
        spec.first_payday, spec.rhythm, today,
    )
    # Against the day the schedule will RECORD -- the stated payday DISPLACED
    # since plan step ``pay_calendar:C14-e-3``, resolved once above and
    # threaded.  ``set_history_opening`` asks this SAME rule of
    # ``min(pay_periods.start_date)``, so asking the form's day here left a
    # band that passed and was then refused after the ``User`` row existed,
    # under a message naming a day the owner never typed.  Found by an
    # adversarial review of that step; ``credentials.py`` had NAMED the premise
    # -- "that a new owner's first recorded payday is the one the form stated"
    # -- a step before it broke.
    pay_schedule_service.reject_history_opening_after_payday(
        spec.history_opens_on, opening_payday,
    )

    # Check email uniqueness.
    if User.query.filter_by(email=email).first():
        raise ConflictError("An account with this email already exists.")

    # Create user.
    user = User(
        email=email,
        password_hash=auth_service.hash_password(spec.password),
        display_name=display_name,
    )
    db.session.add(user)
    db.session.flush()

    # Create default settings (model defaults handle values).
    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    # The owner's REAL pay calendar, from the payday they stated (plan step
    # X-ad-a, ruling R-DB).  It must exist before the default account:
    # ``create_account`` refuses an owner with no pay periods
    # (``_require_pay_period_schedule``) because the opening balance posts a
    # correction, and ``pay_calendar.PayCalendar.filing_period`` raises when
    # there is no materialised period to file it under (finding **N-192**).
    # ``record_paydays`` writes the ``budget.pay_schedule`` row in the same
    # call -- the cadence rule, plan step C3-b -- which is what kept
    # registration from re-opening pay-calendar finding **P8**, a payday with
    # no schedule row beside it, on every new sign-up.  Since plan step C4-b-2
    # ``fk_pay_periods_schedule`` refuses that pairing outright, so the rule is
    # ENFORCED rather than remembered.  It is not made safe: dropping the
    # cadence rule would make this call raise ``IntegrityError`` and 500 the
    # sign-up form, which is a loud failure rather than a silent wrong owner.
    pay_period_write.record_paydays(
        user_id=user.id,
        first_payday=spec.first_payday,
        num_periods=spec.num_periods,
        rhythm=spec.rhythm,
    )
    # How far back those paychecks reach (plan step balance:X-bh-2, ruling
    # balance:R-IA) -- AFTER the batch, because that call is what creates the
    # ``budget.pay_schedule`` row this writes into (the cadence rule) and this
    # door refuses an owner without one.  It is a separate call rather than an
    # argument to ``record_paydays`` because a batch of paydays does not state
    # when a job began: every extend and regenerate would otherwise have to
    # restate, or deliberately not restate, the owner's answer.  ``None`` is
    # written as ``None``, which is what the column already holds -- the call
    # is unconditional so the field cannot be silently skipped by a future
    # edit that reads it as optional.  A sign-up that leaves the box blank
    # therefore stores an ABSENCE, and the engine counts that owner from their
    # recorded paydays until they say otherwise.
    pay_schedule_service.set_history_opening(user.id, spec.history_opens_on)

    # Create the baseline scenario BEFORE the first account (Build-Order
    # Step 5): ``account_service.create_account`` posts the new account's
    # opening anchor correction into every scenario, and postings are
    # scenario-scoped -- a baseline-less create has nowhere to post and is
    # loudly skipped.  Creating the baseline first keeps "production users
    # get a baseline at registration" true at the moment it matters.
    scenario = Scenario(user_id=user.id, name="Baseline", is_baseline=True)
    db.session.add(scenario)

    # Default Checking account via the canonical factory (E-19): the service
    # writes the Account row and its matching origination
    # AccountAnchorHistory assertion in one call, so the contract is enforced
    # in exactly one place across every Account-creating path (this service,
    # the /accounts route, scripts, fixtures).  It said "both anchor columns"
    # until plan step X-f1e2 -- ruling R-EH deleted those columns two steps
    # earlier and the sentence outlived them.
    # Decimal("0.00") is a real value per E-12, not "missing".
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=checking_type_id,
            name="Checking",
            anchor_balance=Decimal("0.00"),
            # SIGN-UP DAY, and at plan step X-ad-a that stopped being the same
            # thing as the schedule's opening payday.  This read
            # ``bootstrap_period.start_date`` while registration fabricated a
            # period starting today; the owner's real first payday is now up to
            # one cadence in the PAST, and a balance of $0.00 is being asserted
            # NOW rather than as of that payday.  It is still the same clock
            # reading -- ``today``, taken once at the top of this function --
            # which is what keeps the assertion and the period bounding it on
            # one civil day.  Two fields have left this call: the explicit
            # ``anchor_period_id`` at plan step X-f1c3c (an assertion carries a
            # DAY) and the ``notes="origination (sign-up)"`` label at X-f1e2
            # (ruling R-ES -- nothing read it, and the registration INSERT is
            # in ``system.audit_log`` either way).
            observed_on=today,
        ),
    )

    # Create default categories.
    for sort_idx, (group, item) in enumerate(DEFAULT_CATEGORIES):
        db.session.add(Category(
            user_id=user.id,
            group_name=group,
            item_name=item,
            sort_order=sort_idx,
        ))

    return user
