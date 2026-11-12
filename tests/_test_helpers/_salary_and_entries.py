"""Salary profiles and entries: a test pay list, a profile and its raises, an envelope entry, a
credit purchase's payback and a purchase marked settled.
"""

from decimal import Decimal

from ._settle_days_and_sides import an_entered_day, settle_day_columns, figure_source_columns


def start_test_pay_list(profile, pay, payday=None):
    """Give a test salary profile its FIRST pay entry, through the create door's writer.

    A profile holds at least one pay entry from plan step salary:X-av-3a on
    (ruling **R-SAL68**), and the create door writes it through
    :func:`~app.services.pay_list_service.start_pay_list` -- so a fixture
    writes it there too, payday check included, rather than constructing the
    row around the door.

    Args:
        profile: The profile, added to the session; flushed here so the
            entry can reference it.
        pay: What one paycheck pays (``Decimal``).
        payday: The payday it pays from.  Defaults to the owner's first
            SAVED payday, which is where migration ``70680a4a7405`` put every
            existing profile's one entry.

    Returns:
        The flushed :class:`~app.models.salary_pay_entry.SalaryPayEntry`.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers in ``_loans.py``.
    from app.extensions import db
    from app.services import pay_list_service
    from app.services.balance_at import BalanceContext
    from app.utils.dates import display_today

    db.session.flush()
    ctx = BalanceContext.build(profile.user_id)
    if payday is None:
        payday = ctx.calendar().saved()[0].start_date
    return pay_list_service.start_pay_list(
        profile, ctx, pay, payday, display_today(),
    )


#: The pay :func:`make_salary_profile` gives a profile when a test names
#: none: ``$75,000.00`` a year at 26 paychecks, rounded to the cent -- the
#: figure migration ``70680a4a7405`` writes for the yearly salary this
#: builder defaulted to until plan step salary:X-av-3a.
DEFAULT_TEST_PAY = Decimal("2884.62")


def make_salary_profile(
    seed_user, db_session, name="Test Salary",
    pay=None, state_code="NC", pay_from=None,
):
    """Build and add an active SalaryProfile for the seed user, with its pay list (uncommitted).

    The shared salary-profile builder so the stereotyped
    ``FilingStatus`` lookup + ``SalaryProfile`` construction block is not
    copied per suite (a duplicate-code finding).  Flushes, because the first
    pay entry references the profile; the caller commits.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        name: The profile name.
        pay: What one paycheck pays (``Decimal``); defaults to
            :data:`DEFAULT_TEST_PAY`.
        state_code: The state code (default ``"NC"``).
        pay_from: The payday the pay is paid from; defaults to the owner's
            first saved payday (:func:`start_test_pay_list`).

    Returns:
        The added :class:`~app.models.salary_profile.SalaryProfile`.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers in ``_loans.py``.
    from app.models.ref import FilingStatus
    from app.models.salary_profile import SalaryProfile

    filing = db_session.query(FilingStatus).first()
    profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        filing_status_id=filing.id,
        name=name,
        state_code=state_code,
    )
    db_session.add(profile)
    start_test_pay_list(
        profile, DEFAULT_TEST_PAY if pay is None else pay, pay_from,
    )
    return profile


def make_recurring_raise(
    profile_id, db_session, *, effective_date,
    percentage=None, terminal_year=None,
):
    """Build and add ONE recurring percentage raise on a profile (uncommitted).

    The shared raise builder for the cases that grade a recurring raise's END
    YEAR reaching a figure (plan step salary:S3-f-2b): the plan point, the
    ``/retirement`` rail's probe and the pricer-count gate each seed the same
    row, and three inline ``SalaryRaise(...)`` blocks were an adversarial
    review's finding.  The caller commits.

    Args:
        profile_id: The :class:`~app.models.salary_profile.SalaryProfile` id.
        db_session: The test ``db.session``.
        effective_date: The day the raise starts, which it repeats on every
            later year it is believed.
        percentage: The fractional raise (Decimal); defaults to
            ``Decimal("0.0500")``, a 5% merit raise.
        terminal_year: The last year it is believed, ``None`` for no end.

    Returns:
        The added :class:`~app.models.salary_raise.SalaryRaise`.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers in ``_loans.py``.
    from app import ref_cache
    from app.enums import RaiseTypeEnum
    from app.models.salary_raise import SalaryRaise

    row = SalaryRaise(
        salary_profile_id=profile_id,
        raise_type_id=ref_cache.raise_type_id(RaiseTypeEnum.MERIT),
        effective_date=effective_date,
        percentage=Decimal("0.0500") if percentage is None else percentage,
        is_recurring=True,
        terminal_year=terminal_year,
    )
    db_session.add(row)
    return row


def add_entry(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    db_session, seed_user, txn, amount, purchased_on,
    *, is_credit=False, settled_on=None, description="purchase",
):
    """Attach one :class:`TransactionEntry` of ``amount`` to ``txn``.

    The shared entry builder so the stereotyped entry construction is not
    copied per suite (a duplicate-code finding).  Flushes; the caller commits.

    **The three bucket flags are parameters (plan step X-c2c2a).**  It built a
    DEBIT-only, outstanding entry until the per-row valuation tests moved to
    ``test_cash_amounts.py``, which needs all three buckets of
    ``cash_ledger._amounts._entry_checking_impact`` -- settled debit,
    outstanding debit, and credit.  Narrowness is why the helper had not
    displaced the hand-rolled copies it exists to prevent: seven suites still
    define their own ``_add_entry`` because this one could not express their
    shape.  Widening it is additive; those suites are not touched here (scope).

    **The reconciled bucket is a DATE, not a flag** (plan step S1-c, ruling
    R-DH (d)).  The stored ``is_cleared`` boolean this replaced was written by
    a bulk UPDATE at anchor true-up, so which bucket a purchase fell in
    depended on the order two buttons were pressed.  A fixture that wants the
    "already inside the asserted balance" bucket therefore passes a
    ``settled_on`` on or before the account's latest ``observed_on`` -- which
    makes the precondition it depends on VISIBLE at the call site instead of
    hidden behind a boolean.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict (supplies ``user_id``).
        txn: The parent :class:`~app.models.transaction.Transaction`.
        amount: The entry amount (Decimal).
        purchased_on: The day the purchase was made.  Never after the user's
            today, which the service refuses (ruling R-M).
        is_credit: True for a credit-card purchase, which never hits checking
            directly (it leaves via the CC Payback sibling) and so only
            REDUCES the reservation whatever its dates say.
        settled_on: The day the bank took the money, or ``None`` (the default)
            for a purchase not yet seen on a statement.  A purchase whose
            ``settled_on`` is at or before the account's latest asserted day is
            already inside that balance and is SUBTRACTED from the reservation;
            every other purchase acts as its floor.  Must not precede
            *purchased_on* (``ck_transaction_entries_settled_not_before_purchase``).
        description: The entry description; the default suits a test that
            does not assert on it.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers in ``_loans.py``.
    from app.models.transaction_entry import TransactionEntry

    db_session.add(TransactionEntry(
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=seed_user["user"].id,
        amount=amount,
        description=description,
        purchased_on=purchased_on,
        **settle_day_columns(settled_on),
        **figure_source_columns(),
        is_credit=is_credit,
    ))
    db_session.flush()


def payback_row_of(db_session, seed_user, envelope, amount, purchased_on):
    """Return the CC PAYBACK the app mints for a credit purchase under *envelope*.

    **The suite's builder for a payback row, on the app's own producer**
    (plan step ``balance:X-bi-7c``, ruling **R-BAL59**).  A payback
    is not a one-off: it carries ``credit_payback_for_id`` as its ONE
    pricing link (``ck_transactions_one_pricing_link``) and is priced by
    the row it repays, and the application has exactly one constructor for
    it -- ``credit_workflow.create_cc_payback_transaction``, which writes it
    into the paycheck AFTER its source's (the one each door hands it) --
    reached by two doors:
    ``credit_workflow.mark_as_credit`` and the one this calls,
    ``entry_credit_workflow.sync_entry_payback``, run after every entry
    mutation.  Four cases built a row through a one-off builder and then set the link
    on it, which the CHECK refuses once that row carries a definition; this
    records the credit purchase through :func:`add_entry` and lets the
    producer answer.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict (the owner).
        envelope: The purchase-tracking
            :class:`~app.models.transaction.Transaction` the card was used
            for; flushed, in a paycheck that has a NEXT one.
        amount: The credit purchase's figure (:class:`~decimal.Decimal`),
            which is what the payback repays.
        purchased_on: The day of the purchase; never after the owner's
            today (ruling R-M).

    Returns:
        The Projected payback :class:`~app.models.transaction.Transaction`,
        flushed, in the paycheck after *envelope*'s.
    """
    # pylint: disable=import-outside-toplevel  -- the module convention.
    from app.services.entry_credit_workflow import sync_entry_payback

    add_entry(
        db_session, seed_user, envelope, amount, purchased_on, is_credit=True,
        description="card purchase",
    )
    payback = sync_entry_payback(envelope.id, seed_user["user"].id)
    assert payback is not None and payback.credit_payback_for_id == envelope.id
    return payback


def mark_purchase_settled(db_session, account, entry, settled_on=None):
    """Record the day the bank took *entry*, and CHECK the anchor covers it.

    The successor to ``add_entry(..., is_cleared=True)`` (plan step S1-c,
    ruling R-DH (d)).  The retired boolean asserted "this purchase is already
    inside the anchor balance" unconditionally, so a fixture could set it on an
    account whose only assertion PREDATED the purchase -- a state production
    cannot reach, because the way a purchase gets inside a declared balance is
    that the user declared the balance after it posted.  That is finding
    N-132 / R8's shape: a fixture asserting a state the app cannot produce
    passes for years and stops discriminating the case it names.

    So this helper does what the flag did AND states the precondition the flag
    let fixtures skip.  It fails loudly rather than silently building an
    unreachable row, and the failure message names both days.

    **The bound is TWO-SIDED, and the upper half was added after the guard's
    first pass let an unreachable row through anyway.**  Checking only
    ``settled_on <= observed_on`` invites the obvious escape: move the
    ASSERTION forward until it covers the purchase.  But an assertion is itself
    bounded -- ``anchor_service.resolve_observation_day`` refuses an
    ``observed_on`` after the user's today, because a balance you have not read
    yet is not an observation -- so an assertion dated into the app's future is
    exactly as unreachable as the anchor-months-earlier row the guard was
    written to stop.  A one-sided guard turns finding N-132's defect into a
    different N-132 defect, which is what the S1-c conversion's own review
    measured on two ``test_cash_fold`` fixtures.  Both halves are checked here
    so neither escape is open.

    The clock is :func:`app.utils.dates.display_today` -- the same one the
    service guard reads, and the one a suite's ``freeze_today`` moves -- never
    ``date.today()``, which would judge a fixture on the process timezone.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` the purchase's parent
            transaction belongs to -- the account whose assertions decide
            whether the purchase is inside a declared balance.
        entry: The :class:`~app.models.transaction_entry.TransactionEntry` to
            settle.
        settled_on: The day the bank took it.  Defaults to the entry's own
            ``purchased_on`` -- "it posted the day I bought it", the shape a
            same-day debit has.

    Returns:
        The updated entry (flushed).

    Raises:
        AssertionError: When the account has never asserted a balance, when its
            latest assertion is for a day BEFORE *settled_on*, or when that
            assertion is dated after the user's today.  In the first two the
            purchase reads as OUTSTANDING however this helper is spelled; in
            the third the account is in a state its own write door refuses.
            Either way a fixture expecting the settled bucket is asking for
            something production cannot produce.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers in ``_loans.py``.
    from app.services.cash_ledger import reconciled_through
    from app.utils.dates import display_today

    settled_on = settled_on or entry.purchased_on
    # The guard asks the PRODUCTION rule, not a lookalike: if this helper
    # spelled the comparison itself it could come to disagree with the
    # reservation it exists to set up, which is the whole shape plan step X-f
    # is about.
    boundary = reconciled_through(account.id)
    observed_on = boundary.observed_day
    assert observed_on is not None, (
        f"account id={account.id} has asserted no balance, so no purchase can "
        f"be inside one; give it an assertion before settling entry "
        f"id={entry.id}"
    )
    assert boundary.covers(settled_on), (
        f"entry id={entry.id} settled {settled_on} is AFTER account "
        f"id={account.id}'s latest asserted day {observed_on}, so the "
        f"projection reads it as outstanding.  Move the account's assertion to "
        f"{settled_on} or later, or the fixture is asserting a state "
        f"production cannot produce."
    )
    today = display_today()
    assert observed_on <= today, (
        f"account id={account.id}'s latest assertion is dated {observed_on}, "
        f"which is AFTER the user's today ({today}); "
        f"anchor_service.resolve_observation_day refuses that at the "
        f"write door, so no production account can be in this state.  Move the "
        f"assertion (and entry id={entry.id}'s dates) back inside the clock "
        f"this suite runs on rather than forward past it."
    )
    # **The pair, through the app's OWN pair writer** (plan step **X-az**): a day
    # written without the basis that says how it is known is unstorable, and a
    # fixture that states half of one builds a row no door could write.
    #
    # **``entered``, and NOT ``asserted``, which a first version wrote** (found
    # by adversarial review 2026-08-22).  The temptation is that this helper's
    # precondition is about an assertion COVERING the day -- but the app's only
    # ``asserted`` writer is ``reconcile_service._purchases.record_settled_days``,
    # which in ONE statement writes the anchor's own ``observed_on`` AND the link
    # naming it.  This helper writes neither: its day is the caller's and may be
    # strictly earlier than the assertion, and it sets no link.  Calling that
    # ``asserted`` builds a row the app cannot reach -- the migration's own
    # backfill classifies exactly that row ``entered`` -- and the matcher would
    # then hand it the BOUND branch of ``expected_window``, a span, for a row
    # production would call a point.  That is finding **N-132**'s shape inside
    # the helper written to prevent N-132.  A caller that wants the panel's own
    # state ticks through the panel.
    # pylint: disable-next=import-outside-toplevel
    from app.services.settle_day import record_settle_day

    record_settle_day(entry, an_entered_day(settled_on))
    db_session.flush()
    return entry
