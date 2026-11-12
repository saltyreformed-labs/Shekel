"""Transfers and settled cash: the seam's confirmed and cash views, a projected or settled
transfer, a settled cash transaction, and the default grid account.
"""

from decimal import Decimal

from app.models.amount_ownership import AmountOwnership

from ._settle_days_and_sides import _UNSET_SETTLED_ON, typed, settlement_if_settling
from ._rows import one_off_row_of
from ._amount_bases_and_stubs import amount_basis_for_scenario


def seam_confirmed_view(loan_account_id, scenario_id, as_of):
    """Return a loan's seam CONFIRMED view at an explicit scenario and as-of.

    The walk-built :class:`~app.services.loan_resolver.ConfirmedLedgerView`
    (:func:`app.services.balance_at.confirmed_view`) -- the balance the loan's
    recorded events fold to, plus its confirmed schedule rows -- which since plan
    step E1d-b is what the resolver's confirmed slice seeds from, replacing the
    deleted posted-ledger reader ``loan_payment_service.confirmed_loan_view``.

    Several suites build an INDEPENDENT ``compute_payoff_scenarios`` reference to
    grade a seam figure against, and every one of them needs this same seed: a
    reference composed WITHOUT it would be the un-seeded anchor replay, which is a
    different trajectory (it is blind to how much cash a payment actually moved).
    Sharing one helper is what keeps those references honest and identical.

    Pins the scenario and the as-of EXPLICITLY rather than through
    ``BalanceContext.build``, because the callers are frozen-clock or
    named-scenario tests whose baseline lookup must not be re-derived.

    Args:
        loan_account_id: The loan account whose confirmed view to build.
        scenario_id: The scenario to scope the walk to.
        as_of: The evaluation date (the resolver's NOW).

    Returns:
        The ``ConfirmedLedgerView``, or ``None`` when the seam withholds one (not
        a configured loan, no scenario, a future date, or not yet originated).
    """
    # Pylint: import-outside-toplevel -- helper-local, matching this module's
    # convention of importing app symbols inside the helper that needs them.
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    from app.models.account import Account  # pylint: disable=import-outside-toplevel
    from app.models.scenario import Scenario  # pylint: disable=import-outside-toplevel
    from app.services import balance_at  # pylint: disable=import-outside-toplevel

    loan = db.session.get(Account, loan_account_id)
    return balance_at.confirmed_view(loan, balance_at.BalanceContext(
        user_id=loan.user_id,
        scenario=db.session.get(Scenario, scenario_id),
        as_of=as_of,
    ))


def contract_forward_references(loan_params, scenario_id, as_of, extra):
    """Return ``(scenarios, with_extra)``: the contract, and the contract plus *extra*.

    The INDEPENDENT reference the loan-page and payoff-oracle suites grade the
    balance seam's forward fold against.  ``scenarios`` is the composer's
    :class:`~app.services.loan_resolver.PayoffScenarios` -- the ledger-derived
    confirmed history and ``project_forward`` over the CONTRACT from the
    replayed starting state, a walk that amortizes month by month and reads no
    payment record.  ``with_extra`` is that same engine walk with *extra* a
    month on top (``project_forward``'s own what-if ``extra_monthly``), over
    the same starting state: the seam's confirmed view for the balance and the
    contractual slice for the starting date and the remaining month count, so
    the reference reads nothing off the fold it grades.

    *The composer produced this reference itself -- its ACCELERATED slice,
    ``compute_payoff_scenarios(extra_monthly=...)`` -- until plan step R7d-g-3
    deleted the composer's planned slices (ruling **R-R88**); the engine's
    contract-plus-extra projection is the same arithmetic, called directly.*

    Args:
        loan_params: The loan's :class:`LoanParams`.
        scenario_id: The scenario to scope the feed and the confirmed view to.
        as_of: The evaluation date (the replay / projection boundary).
        extra: The what-if extra per month; ``0.00`` makes ``with_extra`` the
            contract's own rows.

    Returns:
        ``(scenarios, with_extra)``: the composer's result and the engine's
        contract-plus-extra rows (``AmortizationRow``, in date order).
    """
    # Pylint: import-outside-toplevel -- helper-local, matching this module's
    # convention of importing app symbols inside the helper that needs them.
    from app.services import (  # pylint: disable=import-outside-toplevel
        loan_loaders, loan_payment_service, loan_resolver,
    )
    from app.services.amortization_engine import (  # pylint: disable=import-outside-toplevel
        ProjectionInputs, project_forward,
    )

    ctx_loan = loan_payment_service.load_loan_context(
        loan_params.account_id, amount_basis_for_scenario(scenario_id),
        loan_params,
    )
    confirmed = seam_confirmed_view(loan_params.account_id, scenario_id, as_of)
    scenarios = loan_resolver.compute_payoff_scenarios(
        loan_inputs=loan_resolver.LoanInputs(
            loan_params, loan_loaders.load_loan_anchor_facts(loan_params),
            ctx_loan.payments, ctx_loan.rate_changes,
        ),
        as_of=as_of,
        confirmed_view=confirmed,
    )
    contract = scenarios.original_forward
    with_extra = project_forward(
        ProjectionInputs(
            starting_balance=confirmed.balance,
            starting_date=contract[0].payment_date,
            remaining_months=len(contract),
            payment_day=loan_params.payment_day,
            terms_schedule=loan_resolver.engine_terms(
                loan_params, ctx_loan.rate_changes,
            ),
        ),
        extra_monthly=extra,
    )
    return scenarios, with_extra


def seam_cash_balance_at(account, scenario_id, as_of):
    """Return what the SEAM says an account's cash balance is on a date.

    :func:`app.services.balance_at.cash_balance_at` -- the cash FOLD sampled at
    one date, and since plan step X-c2b2 the figure every cash-flow surface
    renders.  Shared by the five pay-period CRUD suites, which need a balance
    PROBE rather than a balance test: what they assert is that extending,
    truncating, regenerating, resetting or topping up a schedule leaves the
    projection marching correctly, and the balance is how they read that.

    They probed ``_cash_engine.balance_as_of_date`` until plan step X-c2b3
    deleted it.  Routing them here rather than at a replacement producer is the
    point: a schedule-mutation suite should read the figure a SCREEN shows, so a
    mutation that corrupts the projection fails these tests instead of passing
    them against a producer no screen reads.

    **The reader's NOW and the valuation date are DIFFERENT dates here, and
    conflating them would silently zero every probe.**  ``cash_balance_at``
    takes both: ``ctx.as_of`` is the reader's now -- the floor a still-projected
    row's landing day is clamped UP to, because a plan cannot already have
    happened (ruling R-G) -- while the trailing argument is the date being
    valued.  Passing the valuation date as both would clamp every projected row
    to ``valuation + 1`` and answer with the assertions alone.  So the now comes
    from :meth:`BalanceContext.build`, which is the app's own default
    (``date.today()`` inside ``balance_at._context``) and therefore the date a
    ``freeze_today`` suite has patched -- a helper-local ``date.today()`` would
    read the WALL clock instead, which is the N-65 / N-8 trap.

    ``scenario_id`` is load-bearing rather than decorative: the context resolves
    the BASELINE, so a caller probing any other scenario is refused here instead
    of being answered about a scenario it did not write to.

    Args:
        account: The cash account to value (session-attached).
        scenario_id: The scenario the caller wrote its rows under.  Must be the
            user's baseline, which is what every current caller uses.
        as_of: The calendar date to value at, a civil ``date``.

    Returns:
        The cent-quantized ``Decimal`` cash-flow balance at *as_of*.
    """
    # Pylint: import-outside-toplevel -- helper-local, matching this module's
    # convention of importing app symbols inside the helper that needs them.
    from app.services import balance_at  # pylint: disable=import-outside-toplevel

    ctx = balance_at.BalanceContext.build(account.user_id)
    assert ctx.scenario_id == scenario_id, (
        f"seam_cash_balance_at probes the BASELINE scenario "
        f"({ctx.scenario_id}), but was asked for {scenario_id}"
    )
    return balance_at.cash_balance_at(account, ctx, as_of)


def create_transfer(
    seed_user, db_session, from_account, to_account, period,
    amount=Decimal("100.00"), *, due_date=None, name=None, scenario=None,
):
    """Create a PROJECTED transfer via the real service; it is one row.

    It came with two shadow rows until plan step ``balance:X-bi-6-4d-2`` made a
    transfer one row (ruling **R-BAL258**).

    The create half of :func:`create_settled_transfer`, which now builds on this
    one -- so both helpers route through ``transfer_service.create_transfer``,
    the sole transfer-creation chokepoint, and a test cannot accidentally
    construct a transfer that bypasses a write guard production enforces.

    Exposed separately because a test may need a transfer that is NOT settled
    (a projection), or one carrying an explicit ``due_date``: on a LOAN payment
    the due date is the installment the payment satisfies, which decides whether
    the write is even allowed (ruling R-C, plan step C9b) and which installment
    the fold splits it against.

    Flushes via the service; the caller commits.

    Args:
        seed_user: The ``seed_user`` fixture dict (supplies ``user_id`` and the
            baseline scenario).
        db_session: The test ``db.session`` (unused directly -- the service owns
            the session -- but accepted so call sites read uniformly).
        from_account: The account money leaves (the from-side).
        to_account: The account money enters (the to-side).
        period: The :class:`~app.models.pay_period.PayPeriod` to place the
            transfer in.
        amount: The transfer amount (Decimal).  Defaults to
            ``Decimal("100.00")``.
        due_date: Optional due date stored on the transfer.  ``None`` (the
            default) leaves it unset, which is what the
            ad-hoc transfer form produces; a loan payment's installment then
            falls back to the loan's first payment day on or after its
            pay-period start
            (:func:`app.services.installment_calendar.monthly_due_date`).
        name: Optional transfer display name.
        scenario: The :class:`~app.models.scenario.Scenario` to place the
            transfer in.  Defaults to the seed user's baseline.

    Returns:
        The created (Projected) :class:`~app.models.transfer.Transfer`.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache
    from app.enums import StatusEnum
    from app.services import transfer_service

    scenario_id = (
        seed_user["scenario"].id if scenario is None else scenario.id
    )
    return transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=from_account.id,
            to_account_id=to_account.id,
            pay_period_id=period.id,
            scenario_id=scenario_id,
            amount_ownership=AmountOwnership.own(amount),
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            category_id=None,
            name=name,
            due_date=due_date,
        ),
    )


def create_settled_transfer(
    seed_user, db_session, from_account, to_account, period,
    amount=Decimal("100.00"), settled_amount=None,
    settled_on=None, name=None, scenario=None, due_date=None,
):
    """Create an ad-hoc transfer and settle it (Paid), returning the parent.

    The shared settled-transfer builder for the posting-ledger (Build-Order
    Step 2) backfill / lifecycle suites.  Routes the whole thing through
    ``transfer_service`` -- the sole transfer writer -- so the transfer and
    each side's payment record obey every transfer invariant, exactly as
    production produces them.  The transfer is created Projected, then
    transitioned to Paid via ``update_transfer`` (the same ``mark_done``
    chokepoint the route uses).  It built "a settled transfer with two real
    shadows" until plan step ``balance:X-bi-6-4d-2`` made a transfer one row
    (ruling **R-BAL258**).

    **It PRESSES Paid, and a Paid press states no day** (ruling **R-BAL163**,
    plan step ``balance:X-bi-6-4c-3``): both sides BORROW the day it was
    pressed, so both read ``borrowed`` on *settled_on*.  It stated that day as
    ``entered`` on the pair until that step, which is what the one-box popover
    recorded; a test whose subject is a day the owner TYPED states it per side
    through the verb (``side_days``) rather than through this builder.

    **The press lands on *settled_on* by pinning the writer's ONE clock read**
    -- the day a pair with no evidence borrows
    (``transfer_service._status``'s ``display_today``) -- for the length of the
    settle, and nothing else.  Not ``time_machine``: a suite running under
    :func:`freeze_today` has replaced every module's ``datetime`` with a class
    whose ``now`` a traveller cannot reach, and those are exactly the loan
    suites that pin a past day.  So the build CHECKS its own result: both sides
    must read ``borrowed`` on *settled_on* afterwards, and a pin that stopped
    reaching the writer (the read moved) fails here, naming itself, instead of
    settling every fixture on today in silence.

    Flushes via the service; the caller commits.

    Args:
        seed_user: The ``seed_user`` fixture dict (supplies ``user_id`` and
            the baseline scenario).
        db_session: The test ``db.session``.  The service owns the writes;
            this reads the two sides back for the check above.
        from_account: The :class:`~app.models.account.Account` money leaves
            (the from-side).
        to_account: The account money enters (the to-side).
        period: The :class:`~app.models.pay_period.PayPeriod` to place the
            transfer in.
        amount: The transfer amount (Decimal).  Defaults to
            ``Decimal("100.00")``.
        settled_amount: When not ``None``, the figure the owner typed as what
            moved, passed to the settle as a person's typed figure
            (:func:`typed`).  Defaults to ``None``: the transfer settles at
            *amount*.
        settled_on: The civil DAY Paid is pressed on, which both sides then
            borrow.  ``None`` (the default) presses it on the owner's today.
            A loan test reading a PAST balance must pin this to the day it
            wants the payment visible from -- balance step C2 keys visibility
            on the SETTLED date -- typically the period's ``start_date``.  It
            took an INSTANT until plan step X-f1 and callers wrapped their day
            in ``settle_instant_on``; the column stores the day now, so the
            day is passed directly.  A fixture that genuinely needs the broken
            settled-with-no-day row builds it with the bare :func:`add_txn`,
            which is what that builder is for: no door writes one (a
            :class:`~app.services.transfer_service.SideDay` cannot wrap
            ``None``).
        name: Optional transfer display name.
        due_date: Optional due date stored on the transfer, passed straight
            through to :func:`create_transfer`.
            ``None`` (the default) leaves it unset, and a loan payment's
            installment then falls back to the loan's first payment day on or
            after its pay-period start
            (:func:`app.services.installment_calendar.monthly_due_date`).  Pass
            it when a test needs two settled loan payments in ONE accrual
            period at DIFFERENT installments -- that fallback resolves both to
            the same date, so it cannot express that shape.
        scenario: The :class:`~app.models.scenario.Scenario` to place the
            transfer in.  Defaults to ``None``, which uses
            the seed user's baseline scenario (``seed_user["scenario"]``);
            pass a non-baseline scenario to exercise multi-scenario isolation
            (the Commit-6 reconciliation oracle).

    Returns:
        The settled (Paid) parent :class:`~app.models.transfer.Transfer`.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from unittest import mock

    from app import ref_cache
    from app.enums import SettledDayBasisEnum, StatusEnum
    from app.models.transaction_entry import TransactionEntry
    from app.services import transfer_service
    from app.services.settle_day import SettleDay, recorded_settle_day
    from app.services.transfer_service import _status as transfer_status

    transfer = create_transfer(
        seed_user, db_session, from_account, to_account, period,
        amount=amount, name=name, scenario=scenario, due_date=due_date,
    )
    update_kwargs = {"status_id": ref_cache.status_id(StatusEnum.DONE)}
    if settled_amount is not None:
        # The service's key is the VALUE -- the figure and who wrote it, a
        # person's here as the popover's would be (plan step X-bi-3e-1) --
        # under the name the door reads; ``update_transfer`` ignores a key it
        # does not know, so the old column-named key would settle at the plan
        # and say nothing.
        update_kwargs["figure"] = typed(settled_amount)
    if settled_on is None:
        transfer_service.update_transfer(
            transfer.id, seed_user["user"].id, **update_kwargs
        )
        return transfer
    with mock.patch.object(
        transfer_status, "display_today", return_value=settled_on,
    ):
        transfer_service.update_transfer(
            transfer.id, seed_user["user"].id, **update_kwargs
        )
    pressed = SettleDay(day=settled_on, basis=SettledDayBasisEnum.BORROWED)
    # Each side's day is its RECORD's since plan step ``balance:X-bi-6-4d-2``
    # (R-BAL167 class 4: read by its own SQL over the side links, not through
    # the producer under test); a ``$0.00`` close keeps no record on either
    # side (R-BAL141), so it has no day to read.
    sides = [
        recorded_settle_day(record)
        for record in db_session.query(TransactionEntry).filter(
            (TransactionEntry.expense_transfer_id == transfer.id)
            | (TransactionEntry.income_transfer_id == transfer.id),
        )
    ]
    expected = (
        [] if settled_amount is not None and not settled_amount
        else [pressed, pressed]
    )
    assert sides == expected, (
        f"create_settled_transfer pressed Paid on {settled_on} but the sides "
        f"read {sides}: the pin on the writer's clock read no longer reaches "
        "it (see this builder's docstring)"
    )
    return transfer


def create_settled_cash_transaction(  # pylint: disable=too-many-arguments
    seed_user, db_session, period, amount,
    *, account=None, scenario=None, is_income=False,
    category=None, settled_amount=None, name="Cash Txn",
    settled_on=_UNSET_SETTLED_ON,
):
    """Create an ordinary (non-transfer) transaction and settle it go-forward.

    The cash analog of :func:`create_settled_transfer` for the Build-Order
    Step 3 posting-ledger oracle: a ONE-OFF placed through
    :func:`one_off_row_of` (plan step balance:X-bi-7c; it built a link-less
    row owning its figure until then, the shape the cutover deleted), then
    settled through :func:`settle_cash_row` -- the two REAL go-forward
    production primitives, the status seam and the posting builder, in the
    order the mark-done route applies them.  So the returned transaction is
    genuinely settled (``status.is_settled``, ``settled_on`` stamped) AND its
    confirmed cash effect is posted to the double-entry ledger, exactly as
    production produces it when a user marks a transaction Paid / Received.

    Flushes via the builder; the caller commits.

    Args:
        seed_user: The ``seed_user`` (or ``seed_second_user``) fixture dict --
            supplies the default account / scenario and the owning ``user_id``.
        db_session: The test ``db.session``.
        period: The :class:`~app.models.pay_period.PayPeriod` to place the
            transaction in.
        amount: The definition's price (Decimal) -- the confirmed effect when
            no *settled_amount* is given.
        account: The :class:`~app.models.account.Account` the cash leg lands on;
            defaults to ``seed_user["account"]`` (the Checking account).
        scenario: The :class:`~app.models.scenario.Scenario` to place the
            transaction in; defaults to the seed user's baseline scenario.  Pass
            a non-baseline scenario to exercise multi-scenario isolation.
        is_income: When True, an Income transaction settling to Received (cash
            leg positive); otherwise an Expense settling to Paid (cash leg
            negative).
        category: The :class:`~app.models.category.Category` the counter leg
            files under, or ``None``.
        settled_amount: The figure the owner typed at the settle, or ``None``
            for the row's own (see :func:`settle_cash_row`).
        name: The transaction name.
        settled_on: See :func:`settle_cash_row`.

    Returns:
        The settled :class:`~app.models.transaction.Transaction`, flushed.
    """
    # pylint: disable=import-outside-toplevel  -- the module convention.
    from app import ref_cache
    from app.enums import TxnTypeEnum

    account = seed_user["account"] if account is None else account
    scenario = seed_user["scenario"] if scenario is None else scenario
    type_id = ref_cache.txn_type_id(
        TxnTypeEnum.INCOME if is_income else TxnTypeEnum.EXPENSE
    )
    return settle_cash_row(
        one_off_row_of(
            period, name=name, amount=amount,
            user_id=period.user_id, account_id=account.id,
            scenario_id=scenario.id, transaction_type_id=type_id,
            category_id=None if category is None else category.id,
        ),
        is_income=is_income, settled_amount=settled_amount,
        settled_on=settled_on,
    )


def settle_cash_row(
    txn, *, is_income=False, settled_amount=None, settled_on=_UNSET_SETTLED_ON,
):
    """Settle the Projected row *txn* go-forward and post its cash effect.

    The settle half of :func:`create_settled_cash_transaction`, its own
    function since plan step balance:X-bi-7c so a case that builds its row
    another way settles it through the same two primitives without
    re-spelling either (ruling **R-BAL59**; the legacy link-less builder
    was its first other caller, until the cutover ``balance:X-bi-7d-2``
    deleted that shape).  Income settles to Received and expenses to Paid
    (Done) --
    the same split the mark-done route applies (``mutations.py``).  A plain
    transaction carries no entries, so its effect is its resolved figure;
    callers needing the envelope debit-only effect attach credit entries
    separately (the backfill / lifecycle suites cover that path).

    Args:
        txn: The Projected :class:`~app.models.transaction.Transaction`,
            flushed.
        is_income: When True the row settles to Received, else to Paid.
        settled_amount: The figure the owner typed, or ``None`` for the
            row's own -- which :func:`settlement_if_settling` records as the
            panel's prefill coming back untouched (``derived``).
        settled_on: The civil day the money moved.  Omit it for the seam's
            own stamp; pass a day to pin the cash somewhere specific, through
            the app's own pair writer and the seam's mirror (see the comment).
            Passing ``None`` explicitly leaves the seam's stamp too.

    Returns:
        *txn*, settled and posted, flushed.
    """
    # pylint: disable=import-outside-toplevel  -- the module convention.
    from app import ref_cache
    from app.enums import SettledDayBasisEnum, StatusEnum
    from app.services import posting_service, status_seam
    from app.services.settle_day import SettleDay, record_settle_day
    from app.services.status_seam._covering import sync_covering_movement

    # Settle through the real go-forward path: the seam flips the status and
    # stamps settled_on, the optional manual actual is applied AFTER (as the route
    # does), and the builder reconciles the ledger to the confirmed effect last.
    settled_status = StatusEnum.RECEIVED if is_income else StatusEnum.DONE
    # A settle RECORDS what moved (plan step X-au-c3), and this goes through
    # :func:`settlement_if_settling` rather than calling
    # ``Settlement.from_settle`` directly so the ECHO rule applies here too: a
    # ``settled_amount`` equal to the row's own figure is the panel's prefill
    # coming back untouched, which the app records as ``derived``.  Calling
    # ``from_settle`` with the raw submission recorded ``corrected`` for it --
    # two fixture doors answering one rule two ways, which is the drift this
    # helper exists to prevent (found by adversarial review, 2026-08-17).
    status_seam.apply_status_change(
        txn, ref_cache.status_id(settled_status),
        settlement=settlement_if_settling(
            txn, ref_cache.status_id(settled_status), settled_amount,
        ),
    )
    if settled_on is not _UNSET_SETTLED_ON:
        # Through the app's OWN pair writer (plan step **X-az**), never as a
        # bare column assignment: the day and its basis are welded by
        # ``ck_transactions_settle_day_basis_pairing``, and a fixture that moved
        # one and left the other would build a row the app cannot write.  The
        # seam above already stamped ``entered``; re-stating it keeps the pair
        # written in one act whichever day wins.
        #
        # **Pinned AROUND the seam on purpose, and the seam's mirror is then
        # asked to follow** (plan step **X-bi-3a**).  The seam refuses a day
        # that has not happened (ruling R-EJ), and the anchor-reconciliation
        # suites build their civil-day partitions on a server-now origin with
        # events a day or three after it -- so the pin stays a bare one, as it
        # always was.  What may NOT stay bare is the row's covering movement:
        # the seam wrote it dated with its own stamp, and a row moved to
        # another day while its movement kept today's is a state no door can
        # produce (the ledger partitioned one spend on two sides of an anchor,
        # measured on five cases 2026-09-15).  So the pin is followed by the
        # seam's own mirror -- one rule, not a fixture's restatement of it.
        record_settle_day(
            txn,
            SettleDay(day=settled_on, basis=SettledDayBasisEnum.ENTERED),
        )
        sync_covering_movement(txn, now_settled=True, settlement=None)
    posting_service.sync_transaction_postings(txn)
    return txn


def set_default_grid_account(db_session, user_id, account_id):
    """Point a user's default grid account at *account_id* (re-queried, flushed).

    Sets ``UserSettings.default_grid_account_id`` so
    ``account_resolver.resolve_grid_account``'s tier-1 picks this account --
    the way a test makes the dashboard / grid render a NON-checking account.
    Re-queries the live ``UserSettings`` row rather than mutating the
    ``seed_user["settings"]`` object directly: the account factories above
    (:func:`create_hysa_account`, :func:`make_investment_account`, ...) commit
    internally, which detaches the fixture's settings instance, so a write to
    it would be silently dropped.  Flushes so the change is visible to the
    producer under test in the same session.

    Args:
        db_session: The test ``db.session``.
        user_id: The user whose default grid account to set.
        account_id: The account id to point the default at.

    Returns:
        The live :class:`~app.models.user.UserSettings` row, updated.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention the factory helpers above follow.
    from app.models.user import UserSettings

    settings = (
        db_session.query(UserSettings).filter_by(user_id=user_id).first()
    )
    settings.default_grid_account_id = account_id
    db_session.flush()
    return settings
