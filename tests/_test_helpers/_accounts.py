"""Accounts: savings and HYSA accounts, an account of any built-in type through the service, and a
balanced journal entry.
"""

from datetime import date as _real_date
from decimal import Decimal

from ._anchors import open_books_before_the_first_assertion


def create_savings_account(
    seed_user, db_session, name, anchor_balance, observed_on=None,
):
    """Create a Savings account via the canonical factory (flushed, uncommitted).

    The shared liquid-account builder for goal-track / savings tests, so
    the stereotyped ``AccountSpec`` + ``create_account`` + ``flush`` block
    is not copied per suite (a duplicate-code finding).  The caller
    commits with its own goal/transaction inserts.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        name: The account name.
        anchor_balance: The opening anchor balance (Decimal).
        observed_on: The civil day the opening balance is asserted for, or
            ``None`` for the factory's default (today).  **A suite whose money
            moves BEFORE today states this** (plan step X-f3c-2c): the
            origination assertion is append-only, so an account opened "today"
            and then handed movements dated in January carries an assertion
            that governs every one of them.  Re-stamping the row afterwards was
            how that used to be repaired, and there is no such act.

    **It took an ``anchor_period_id`` until plan step X-f1c3c** (ruling R-EH):
    an account no longer references a pay period at all, so callers that pinned
    one now pin the assertion's DAY or take the factory's default (today).

    Returns:
        The created Savings :class:`~app.models.account.Account`.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.models.ref import AccountType
    from app.services import account_service

    savings_type = (
        db_session.query(AccountType).filter_by(name="Savings").one()
    )
    spec_kwargs = {
        "user_id": seed_user["user"].id,
        "account_type_id": savings_type.id,
        "name": name,
        "anchor_balance": anchor_balance,
        # ``observed_on`` is left to the factory (today) unless the caller
        # names a day.  See ``create_loan_account`` for why this helper does
        # not take ``create_account_of_type``'s day-before default.
    }
    if observed_on is not None:
        spec_kwargs["observed_on"] = observed_on
    account = account_service.create_account(
        account_service.AccountSpec(**spec_kwargs),
    )
    db_session.add(account)
    db_session.flush()
    # **Its books open the day BEFORE its origination assertion** (plan step
    # X-f3c-2b, ruling **R-HG**).  ``create_account`` defaults ``observed_on``
    # to ``display_today()`` and the settle door defaults a settle day to the
    # SAME ``display_today()``, so a fixture meaning "this account existed,
    # then money moved on it" lands the movement on the very day the books
    # open -- and an opening equity is that day's CLOSING balance, so the
    # movement is inside it and is refused.  Moves no figure: the origination
    # assertion still clears whatever settled on its own day.
    open_books_before_the_first_assertion(db_session, account)
    return account


def create_hysa_account(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    seed_user, db_session, anchor_period, balance,
    apy=Decimal("0.05000"), name="HYSA", compounding=None,
):
    """Create an HYSA account (INTEREST) with InterestParams (default 5% APY daily).

    The shared interest-bearing-account builder (promoted from the
    balance-seam suite's per-file ``_make_hysa`` copy) so the dashboard,
    seam, and net-worth suites build an INTEREST account through one home
    rather than each re-inlining the ``AccountType`` lookup +
    ``create_account`` + ``InterestParams`` block.  Routes the account
    through the canonical ``account_service.create_account`` factory (so it
    gets its origination ``AccountAnchorHistory`` row), then attaches the
    ``InterestParams`` row (APY + daily compounding) that makes the account
    classify INTEREST.  Commits before returning so the account is fully
    resolvable.

    **The opening assertion is DATED at the anchor period's first day, and the
    factory supplies that day to ``create_account`` rather than moving the row
    afterwards** (plan step X-f3c-2c).  Since plan step X-c2a, modelled
    interest accrues only forward of an account's latest balance assertion
    (ruling R-L), and ``create_account`` defaults ``observed_on`` to today.  A
    suite that freezes ``today`` inside its own seeded period range --
    ``tests/test_services`` freezes it to 2026-03-20 -- would otherwise build
    an account asserted months AFTER its own last pay period, a state
    production cannot reach (a true-up files against ``get_current_period``)
    and one in which the account accrues nothing anywhere.  Dating the
    origination at the period's own start makes the fixture deterministic,
    reachable (an account opened on day 1 of its period), and
    clock-independent, and it keeps every hand-computed interest figure in the
    suites valid: the accrual window is then the full anchor period, exactly
    what it was before the rule existed.  A test that needs a MID-period
    assertion (the shape the rule exists for) appends its own.

    **The day now goes through the DOOR, which BOUNDS it** -- an assertion may
    not be dated in the future (``anchor_service.resolve_observation_day``), so
    a caller handing this factory a pay period that starts after today is
    refused, where the old re-stamp wrote that state silently.  That is the
    point rather than a cost: one route suite was building exactly it (an
    account anchored two periods AHEAD of today) and grading a shape no owner
    can produce.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        anchor_period: The :class:`~app.models.pay_period.PayPeriod` to
            anchor the account against; its ``id`` becomes the account's
            ``current_anchor_period_id``.
        balance: The opening anchor balance (Decimal -- construct from a
            string per the coding standard).
        apy: The annual percentage yield as a Decimal fraction (default
            ``Decimal("0.05000")`` for 5%).
        name: The account name (default ``"HYSA"``).
        compounding: The :class:`~app.enums.CompoundingFrequencyEnum` member,
            or ``None`` for DAILY.  Parameterised at plan step X-g4b: the
            helper hardcoded DAILY, so no test anywhere ran a MONTHLY or
            QUARTERLY account through a balance PRODUCER -- and the real Money
            Market compounds MONTHLY, so a regression hardcoding the frequency
            in the replay's rate resolver would have misspriced a live account
            with the whole suite green.

    Returns:
        The created HYSA :class:`~app.models.account.Account`.

    Pylint: ``too-many-arguments`` (7/5) / ``too-many-positional-arguments``
    (7/5) -- a test FACTORY whose parameters are the account's independent
    configurable facts (owner, session, anchor period, balance, rate,
    name, compounding frequency); bundling them would put a
    parameter object between every suite and its fixture.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention); load
    # the models lazily, the same way the loan / investment helpers do.
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import CompoundingFrequencyEnum
    from app.models.interest_params import InterestParams
    from app.models.ref import AccountType
    from app.services import account_service

    hysa_type = db_session.query(AccountType).filter_by(name="HYSA").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=hysa_type.id,
            name=name,
            anchor_balance=balance,
            observed_on=anchor_period.start_date,
        ),
    )
    db_session.add(account)
    db_session.flush()
    db_session.add(InterestParams(
        account_id=account.id,
        apy=apy,
        compounding_frequency_id=ref_cache.compounding_frequency_id(
            compounding or CompoundingFrequencyEnum.DAILY,
        ),
    ))
    # **And then before every day a row could land on** (plan step X-f3c-2b,
    # ruling **R-HG**).  ``create_account`` opens the books on the day it was
    # handed, which is right when the anchor period is the earliest -- and
    # these factories are routinely handed a LATER period while the suite
    # records movements in an earlier one.  Backward-only, so it never undoes
    # the day stated above.
    open_books_before_the_first_assertion(db_session, account)
    db_session.commit()
    return account


# Default opening anchor balance for ledger-account-suite accounts.  The
# Build-Order Step 2 suites never assert on a balance (Commit 2 touches no
# balance math), so a single fixed value keeps the shared factory at four
# parameters and the call sites free of an irrelevant amount.
_LEDGER_SUITE_ANCHOR_BALANCE = Decimal("100.00")


def create_account_via_service(
    seed_user, db_session, type_name, name, anchor_balance=None,
    observed_on=None,
):
    """Call ``account_service.create_account`` for *type_name*, and NOTHING else.

    **The primitive, for the suites whose SUBJECT is what that service
    records** -- ``tests/test_services/test_account_opening.py`` grades the one
    ``budget.account_openings`` row the factory writes, so a fixture that
    appended a second would be grading itself.  Every other caller wants
    :func:`create_account_of_type`, which is this plus the books an account
    that has already existed would have.

    Splitting the two is plan step X-f3c-2b's own correction.  The books move
    was first written as an ``if observed_on is None`` branch inside the shared
    factory, which bound two unrelated things together: a caller naming the
    ASSERTION's day got, as a side effect, books that forbade the settles it
    was about to record.  The twelve callers that pass ``observed_on`` were
    exactly the ones left broken by it.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``, used for the ``AccountType``
            lookup below.
        type_name: The ``ref.account_types`` name (e.g. ``"Checking"``).
        name: The account name.
        anchor_balance: Optional opening anchor balance (``Decimal``);
            ``None`` uses the ledger-suite sentinel.
        observed_on: Optional civil day the opening balance was TRUE.
            Defaults to the day before the frozen today.

    Returns:
        The created :class:`~app.models.account.Account` (flushed,
        uncommitted).
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention); load
    # the models / service lazily, the same way the factory helpers above do.
    # pylint: disable=import-outside-toplevel
    from datetime import timedelta
    from app.models.ref import AccountType
    from app.services import account_service
    from app.utils.dates import display_today

    acct_type = (
        db_session.query(AccountType).filter_by(name=type_name).one()
    )
    return account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=acct_type.id,
            name=name,
            anchor_balance=(
                _LEDGER_SUITE_ANCHOR_BALANCE if anchor_balance is None
                else anchor_balance
            ),
            observed_on=(
                display_today() - timedelta(days=1)
                if observed_on is None else observed_on
            ),
        ),
    )


def create_account_of_type(
    seed_user, db_session, type_name, name, anchor_balance=None,
    observed_on=None,
):
    """Create an account of any built-in type via the canonical factory.

    The shared "build an account of type X" helper for the ledger-account
    (Build-Order Step 2) suites, so the stereotyped ``AccountType`` lookup +
    ``AccountSpec`` + ``create_account`` block is not copied per file (a
    duplicate-code finding).  ``create_account`` fires the Step-2
    ledger-account sync hook, so the returned account already carries its
    paired ``budget.ledger_accounts`` row.  The opening anchor balance
    defaults to a fixed sentinel (the ledger-pairing suites assert on
    pairing, never on balance); the Step-5 account-anchor suites pass an
    explicit ``anchor_balance`` because the opening correction posts exactly
    that value.  The anchor period is resolved by the factory from the day the
    opening is observed on.

    **What it returns is an account that has ALREADY EXISTED**, and both halves
    of that are deliberate.

    * The origination ASSERTION defaults to the day BEFORE today (ruling
      R-DH (a), finding N-133 / F1).  An assertion is the CLOSING balance for
      its civil day, so a settle dated that same day is INSIDE it -- and the
      ordinary settle idiom in these suites is the seam's own ``settled_on =
      display_today()``, which under a frozen clock is TODAY.  An account
      asserted "today" therefore swallows every settle the test then records,
      and the fixture stops exercising the thing it names.
    * The BOOKS are then opened before anything this fixture could date, always
      (plan step X-f3c-2b, ruling **R-HG**).  An opening equity is the closing
      balance for its OWN day, so a movement dated on or before it is not
      absorbed but REFUSED.  ``observed_on`` does not switch this off: it says
      where the assertion goes, which is a different question.

    **A suite whose subject is the record ``create_account`` writes wants
    :func:`create_account_via_service` instead** -- this one appends a second
    ``budget.account_openings`` row, which is the production shape after
    migration ``d3b6f1c8a274`` and is not what a test of the factory means.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        type_name: The ``ref.account_types`` name (e.g. ``"Checking"``,
            ``"Mortgage"``, ``"401(k)"``).
        name: The account name.
        anchor_balance: Optional opening anchor balance (``Decimal``);
            ``None`` uses the ledger-suite sentinel.
        observed_on: Optional civil day the opening balance was TRUE.
            Defaults to the DAY BEFORE the frozen today -- see below.

    Returns:
        The created :class:`~app.models.account.Account` (flushed,
        uncommitted).
    """
    account = create_account_via_service(
        seed_user, db_session, type_name, name, anchor_balance, observed_on,
    )
    # **Its BOOKS open before anything a fixture can date, ALWAYS** (plan step
    # X-f3c-2b, ruling **R-HG**).  ``create_account`` puts the opening record
    # and the origination assertion on ONE day, so a caller settling a row on
    # or before that day is building a state the app refuses -- and the default
    # idiom here settles on the frozen today or earlier.  Unconditional, and
    # not keyed on whether the caller named ``observed_on``: that argument says
    # where the ASSERTION goes, which is a different question, and binding the
    # two left every caller that wanted a dated assertion with books it could
    # not record against.
    open_books_before_the_first_assertion(db_session, account)
    return account


def make_balanced_entry(  # pylint: disable=too-many-arguments
    session, seed_user, *, from_ledger_id, to_ledger_id,
    amount=Decimal("100.00"), transaction_entry_id=None, source_kind=None,
    posting_kind=None,
    period_id=None,
):
    """Create and commit one balanced journal entry (two legs summing to zero).

    The from leg is ``-amount`` (credit), the to leg is ``+amount`` (debit),
    so the deferred balanced trigger passes at commit.  Returns the committed
    :class:`~app.models.journal_entry.JournalEntry`.  Shared by the
    journal-entry model suite and the ledger append-only role-privilege suite
    (previously module-local to the former -- a duplicate-code finding once
    the R4 tests needed the same shape).

    ``source_kind`` / ``posting_kind`` default to a transfer movement's
    shape with no movement linked (``transfer_movement`` / ``transfer``):
    the one-entry ``transfer`` source this default named until plan step
    ``balance:X-bi-6-5``'s second leaf (card plan#1845) is gone, with the
    ``transfer_id`` / ``transaction_id`` links it and the row's own leg
    carried.  A movement-sourced entry passes ``transaction_entry_id=<id>``.
    ``source_kind`` / ``posting_kind`` default to ``None`` (resolved lazily)
    so the enums are not imported at module load, matching this module's
    collection-time-safety convention.

    Args:
        session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict (supplies the user,
            scenario, and bootstrap period).
        from_ledger_id: Ledger account id for the ``-amount`` leg.
        to_ledger_id: Ledger account id for the ``+amount`` leg.
        amount: The leg magnitude (Decimal).
        transaction_entry_id: Optional ``budget.transaction_entries``
            back-link (a movement's).
        source_kind: :class:`~app.enums.PostingSourceEnum` member, or
            ``None`` for TRANSFER_MOVEMENT.
        posting_kind: :class:`~app.enums.PostingKindEnum` member, or
            ``None`` for TRANSFER.
        period_id: Pay period id, or ``None`` for the bootstrap period.

    Returns:
        The committed :class:`~app.models.journal_entry.JournalEntry`.
    """
    # Pylint: ``import-outside-toplevel`` -- same collection-time-safety
    # convention as the helpers above (no app symbols imported at module
    # load); load the enums, cache, and models lazily here.
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import PostingKindEnum, PostingSourceEnum
    from app.models.journal_entry import JournalEntry, Posting

    if source_kind is None:
        source_kind = PostingSourceEnum.TRANSFER_MOVEMENT
    if posting_kind is None:
        posting_kind = PostingKindEnum.TRANSFER
    if period_id is None:
        period_id = seed_user["bootstrap_period"].id
    entry = JournalEntry(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        pay_period_id=period_id,
        entry_date=_real_date(2026, 1, 15),
        source_kind_id=ref_cache.posting_source_id(source_kind),
        transaction_entry_id=transaction_entry_id,
        description="Test entry",
    )
    session.add(entry)
    session.flush()
    kind_id = ref_cache.posting_kind_id(posting_kind)
    session.add(Posting(
        journal_entry_id=entry.id, ledger_account_id=from_ledger_id,
        amount=-amount, posting_kind_id=kind_id,
    ))
    session.add(Posting(
        journal_entry_id=entry.id, ledger_account_id=to_ledger_id,
        amount=amount, posting_kind_id=kind_id,
    ))
    session.commit()
    return entry
