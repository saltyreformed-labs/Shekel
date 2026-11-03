"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_balance_at.py``, moved verbatim by ``tools/split``.
"""

from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import AcctTypeEnum, TxnTypeEnum
from app.models.paycheck_line import PaycheckLine
from app.models.ref import CalcMethod, PaycheckLineKind
from app.models.transaction_template import TransactionTemplate
from app.services import balance_at, income_service, liability_sign, template_amount_service
from app.services.projection_inputs import load_investment_params_for_accounts
from app.services.scenario_resolver import get_baseline_scenario
from app.services.balance_at import BalanceContext
from tests._test_helpers import (
    create_hysa_account,
    create_loan_account,
    create_settled_transfer,
    generate_row_of,
    insert_trueup_event,
    loan_params_for,
    make_every_period_rule,
    one_off_row_of,
)
from tests._definition_price import newest_price


def _no_baseline(user_id):
    """Return a BalanceContext with NO baseline scenario.

    The honest stand-in for the bare ``None`` scenario the seam entries used to
    take: ``get_baseline_scenario`` returns ``None`` for a fresh user, and the
    seam's fail-loud contract turns that into a ``ValueError`` rather than a deep
    ``AttributeError`` on ``scenario.id`` (or a silent ``$0``).  The guard now
    lives on the context, so the no-baseline state is expressed by a context
    carrying ``scenario=None`` -- not by passing ``None`` where a context goes.
    """
    return BalanceContext(user_id=user_id, scenario=None, as_of=date.today())


#: The NEWEST price a definition states when the graded figure is its price
#: on the row's own day: what its ``default_amount`` copy held until plan step
#: X-bp.  Distinct from every amount the cases below state, so a producer
#: reading the newest price instead answers a number no assertion expects
#: (see :func:`_derived_income_row` for how the series is made to hold it).
_NOT_THE_SERIES = Decimal("7.77")


def _derived_income_row(db, seed_user, account, period, amount):
    """A projected income row DECLARED derived, priced by a definition's series.

    **What replaced a monkeypatched read-time repair at plan step X-au-d.**  A
    fold that reads a row's own column contributes ``None`` for this row, and
    one that asks the amount model contributes *amount* -- so "the balance is
    built on the derivation" is a property of the DATA here rather than of a
    patched producer.

    The definition is priced through ``template_amount_service.set_amount``,
    the ONE write door for a price series, effective on the period's own
    start -- which is the due date the engine derives for an every-paycheck
    definition (``compute_due_date``), so ``amount_as_of`` resolves on it.
    The row itself is the engine's (:func:`generate_row_of`, plan step
    balance:X-cf), written into the owner's baseline scenario, which is
    what both callers price against.

    **The newest price is deliberately NOT the graded figure.**  An
    adversarial review of plan step X-au-d found a first version setting both
    to *amount*, which makes the series and the scalar the same number -- and
    ``_stated_amount``'s empty-series refusal is then invisible: replacing that
    refusal with ``return template.default_amount``, the exact fallback its own
    message forbids, survives every case built on this helper.  A distinct
    scalar is what makes the series load-bearing.

    **Passing ``_NOT_THE_SERIES`` to the constructor did not achieve that, and
    plan step balance:X-cf-3 measured it.**  ``set_amount`` re-synced
    ``default_amount`` onto the NEWEST version it states
    (``template_amount_service._resync_scalar``), so the constructor's
    ``7.77`` was overwritten with *amount* on the next line and the resolver
    mutated to read the scalar passed both cases.  The scalar holds
    ``_NOT_THE_SERIES`` the only way it can: as a SECOND version, effective
    the day after this row's due date, which the re-sync copies into the
    column while ``amount_as_of`` on the row's own day still answers
    *amount*.  Red with the mutation, green without.

    Args:
        db: The test session fixture.
        seed_user: The seeded owner bundle.
        account: The account the row's money moves through.
        period: The pay period the row is funded in.
        amount: What the definition states, and therefore what the row is
            worth.

    Returns:
        The flushed :class:`~app.models.transaction.Transaction`.
    """
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=account.id,
        category_id=next(iter(seed_user["categories"].values())).id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
        name="Priced income",
    )
    db.session.add(template)
    db.session.flush()
    template_amount_service.set_amount(
        template, amount, effective_on=period.start_date,
    )
    template_amount_service.set_amount(
        template, _NOT_THE_SERIES,
        effective_on=period.start_date + timedelta(days=1),
    )
    assert newest_price(template) == _NOT_THE_SERIES
    # The definition first, then the cadence onto it (plan step R-F6).
    make_every_period_rule(db.session, template)
    return generate_row_of(template, period)


def _make_hysa(db, seed_user, anchor_period, balance):
    """Create an HYSA account (INTEREST) via the shared factory (5% APY daily).

    Thin adapter over :func:`tests._test_helpers.create_hysa_account` that
    keeps this suite's ``(db, seed_user, ...)`` call convention while the HYSA
    construction itself lives in the one shared home.
    """
    return create_hysa_account(seed_user, db.session, anchor_period, balance)


def _owed_today(account, ctx) -> Decimal:
    """What *account* OWES on the pass's read day, per the seam.

    What ``DebtSchedule.projection_seed`` WAS -- the settled fold's balance at
    ``ctx.as_of``, from which the forward fold started -- until plan step
    recurrence:R16-c-1 replayed a loan's whole timeline from its origination and
    deleted the field with the second fold it seeded.  The assertions below that
    graded the seed grade the same figure through the public scalar.

    **The scalar reports what the loan HOLDS, negative when owed** (ruling
    R-CC47, plan step credit_card:CC-5-5c), so this reads it through
    ``liability_sign.owed`` -- the one flip -- and returns the positive owed
    figure every caller compares against owed dollars and schedule rows'
    ``remaining_balance``.
    """
    return liability_sign.owed(balance_at.balance_at(account, ctx, ctx.as_of))


def _make_mortgage(
    db, seed_user, anchor_period, balance, origination_date, name="Mortgage",
    *, tracked_from=None,
):
    """Create a Mortgage (AMORTIZING) through the shared loan factory.

    ``name`` is parameterised so a test can seed two mortgages in one user
    without colliding on the ``(user_id, name)`` unique constraint.  Returns
    ``(account, loan_params)`` so a caller can append a trueup event (e.g. to
    drive the loan to paid-off / empty-schedule, or to re-anchor it today).
    ``tracked_from`` states *balance* as the balance on that day the way the
    setup door records it (plan step R20: a ``tracking_start``), the mid-life
    import shape; omitted, the loan carries only its origination assertion.

    Delegates to :func:`create_loan_account` rather than re-rolling the
    account-factory + ``LoanParams`` + rate block: the hand-rolled copy this
    replaces never opened the loan's genesis posting ledger, so every mortgage in
    this suite ran on the no-ledger fallback -- a path production never takes.
    """
    acct = create_loan_account(
        seed_user, db.session, name=name, principal=balance,
        rate=Decimal("0.06500"), term=360,
        origination_date=origination_date, payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
        tracked_balance=balance if tracked_from is not None else None,
        tracked_from=tracked_from,
    )
    return acct, loan_params_for(db.session, acct.id)


# The paid-loan fixture's own arithmetic.  The divisor is the $2,000.00 of cash
# the payment actually moved -- NOT the loan's ~$1,498.88 contractual P&I -- because
# the ledger splits the REAL cash and its rows are what the schedule carries for the
# confirmed region (``_payoff.py:285``).  At 6%/yr = 0.005/mo on a $250,000.00
# origination:
#   02-01: interest 250000.00 * 0.005 = 1250.00 -> principal  750.00
#          remaining 250000.00 -  750.00 = 249250.00
#   03-01: interest 249250.00 * 0.005 = 1246.25 -> principal  753.75
#          remaining 249250.00 -  753.75 = 248496.25
PAID_LOAN_LAST_CONFIRMED_REMAINING = Decimal("248496.25")


PAID_LOAN_TRUED_UP_TO = Decimal("200000.00")


def _paid_then_trued_loan(seed_user, db_session, periods):
    """Create a loan with two SETTLED payments and a LATER true-up.

    The shape the forward producers exist for, and the one the suite's fixture
    matrix lacked (plan Section 7.4): real settled cash -- so the schedule
    carries CONFIRMED rows at all -- followed by an operator true-up dated after
    the last of them, so the ledger's confirmed balance and those rows'
    ``remaining_balance`` genuinely disagree.  Without the true-up both derive
    from the same ledger and every assertion over them is vacuous.

    Shared by :class:`TestScalarAndMapAgree` (which needs the shape present) and
    :class:`TestForwardFoldSeedsFromTheConfirmedPresent` (which pins its seeding),
    so the two cannot drift on what "a paid loan" means.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        periods: The ``seed_periods`` list (payments land in indices 1 and 3).

    Returns:
        The committed loan :class:`~app.models.account.Account`.
    """
    # Originated the month before the first payment's 2026-02-01 installment
    # (plan step recurrence:R16-c-2, ruling R-R101): every contractual
    # installment from origination is charged now, so the 2025-01-01 it
    # carried until then read as twelve unpaid months ahead of that payment.
    loan = create_loan_account(
        seed_user, db_session, name="Paid Then Trued",
        principal=Decimal("250000.00"), rate=Decimal("0.06000"),
        term=360, origination_date=date(2026, 1, 1), payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )
    # Settled payments due 2026-02-01 (period 1) and 2026-03-01 (period 3);
    # both pay periods have begun by the frozen 2026-03-20, so the replay
    # confirms both.  Explicit paid instants keep the fixture off the wall clock
    # (``create_settled_transfer`` otherwise settles at ``now()``).
    for idx, paid in ((1, date(2026, 2, 1)), (3, date(2026, 3, 1))):
        create_settled_transfer(
            seed_user, db_session, seed_user["account"], loan,
            periods[idx], amount=Decimal("2000.00"),
            settled_on=paid,
        )
    db_session.commit()
    insert_trueup_event(
        loan_params_for(db_session, loan.id),
        PAID_LOAN_TRUED_UP_TO, date(2026, 3, 15),
    )
    db_session.commit()
    return loan


def _add_flat_deduction(db, profile, account, amount):
    """Add an active flat pre-tax paycheck deduction targeting *account*.

    The growth engine's contribution feed: a flat per-period employee
    contribution into an investment account, picked up by
    :func:`~app.services.projection_inputs.load_payroll_feeds` (active
    profile + active deduction + ``target_account_id``) and PRICED by the
    paycheck engine.  Flushed; the caller commits.

    **It also records which job FUNDS the account** (plan step
    **salary:R14-b**, ruling **R-SAL5**) -- what ``salary:R14-a``'s backfill
    wrote for this exact shape, an account with a deduction naming it being
    funded by that deduction's own profile.  Without the link no employer
    money is modelled at all (developer, 2026-09-04), so an employer-match
    case would assert against ``$0.00`` and pass for the wrong reason.
    """
    flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()
    pre_tax_timing = (
        db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
    )
    ded = PaycheckLine(
        salary_profile_id=profile.id,
        target_account_id=account.id,
        name=f"Contribution {account.name}",
        amount=amount,
        calc_method_id=flat_method.id,
        paycheck_line_kind_id=pre_tax_timing.id,
        is_active=True,
    )
    db.session.add(ded)
    params = load_investment_params_for_accounts([account]).get(account.id)
    if params is not None:
        params.salary_profile_id = profile.id
    db.session.flush()
    return ded


def _configured_annual_rate(account) -> Decimal:
    """Return the annual return the account itself carries.

    Read off the account's own params row rather than restated here, so this
    test's bound moves with the fixture instead of pinning a literal the
    fixture is free to change.
    """
    params = getattr(account, "asset_appreciation_params", None)
    if params is not None:
        return params.annual_appreciation_rate
    return load_investment_params_for_accounts(
        [account],
    )[account.id].assumed_annual_return


def _all_columns(view):
    """Return the view's columns in order, as ``(period_id, column)`` pairs.

    EVERY requested period carries a balance since plan step X-c2b2 -- the fold
    is total -- so there is nothing to filter out.  It used to skip periods the
    projection could not reach (``balance is None``, the pre-anchor columns the
    retired producer omitted), which is exactly the omission finding cash D3
    names; keeping a name for "the columns that have a balance" would now be a
    name for "all of them".
    """
    return list(view.columns.items())


def _assert_grid_view_reconciles(view):
    """Assert the view's rows reconcile to the cent, off the view alone.

    For every adjacent pair of periods the displayed balance delta must equal
    the column's own net plus the "On other accounts" term plus BOTH remainders
    plus BOTH modelled tiers -- ruling R-K's identity in the form ruling R-AH
    measured, with ruling R-DH (f)'s split applied and ruling
    ``credit_card:R-CC16``'s term (plan step CC-4-1)::

        balance[p] - balance[p-1]
            == net[p] + elsewhere[p] + period_timing[p] + book_vs_bank[p]
               + contribution[p] + accrual[p]

    **The ELSEWHERE term is what lets a paycheck-wide net reconcile with a
    one-account balance**: ``net`` sums every member of the owner's cash-flow
    set (checking and its cards) and ``balance`` is one member's, so what the
    paycheck budgets on the other members -- their expense less their income
    -- is the term between them.  ``TestTheRemainderOracleSeesEveryTerm``
    carries the control that a delta short its elsewhere term must not
    reconcile, beside the two it already carried for the modelled tiers.

    The CONTRIBUTION term is not decoration: dropping it breaks the identity on
    53 of 59 period pairs on the real Empower 401(k), worst $181.59 a column (a
    flat-percentage employer match), while including it holds on all 59.  It was
    written here at plan step X-g3a, where the grid's kind gate still admitted
    only INTEREST accounts and the term was therefore identically $0.00, so that
    the oracle was already in its final form when X-g3b's cutover put a figure
    in it.  X-g3b supplies the producer-side control that can distinguish the
    two forms.

    **The remainder became two terms at plan step S1-c** (ruling R-DH (f)).
    They are summed here rather than through a combined accessor because no
    combined accessor survives on :class:`~app.services.balance_at.GridColumn`:
    leaving one would invite a surface to render the sum again, which is the
    figure the ruling exists to delete.  Splitting them cannot change whether
    the identity holds -- both remainders are cent-quantized, so
    ``round(a) + round(b) == round(a + b)`` -- which is why this oracle's
    figures are unchanged by the split and its per-row discrimination lives in
    the tests that assert each row's own value.

    It reads ONE GridColumn per period rather than re-running the subtotal
    producer beside the balance producer, which is the point of plan steps
    X-c2b1 / X-c2b2: the identity is a property of the row set, so an oracle
    that re-derived one side from a second producer would be testing that the
    two producers agree rather than that the row set is coherent.
    """
    items = _all_columns(view)
    assert len(items) >= 2, "need >= 2 periods to reconcile a delta"
    for (_prev_id, prev), (pid, column) in zip(items, items[1:]):
        expected = (
            column.net + column.elsewhere
            + column.period_timing + column.book_vs_bank
            + column.contribution + column.accrual
        )
        assert column.balance - prev.balance == expected, (
            f"period {pid}: balance delta "
            f"{column.balance - prev.balance} != net {column.net} + "
            f"elsewhere {column.elsewhere} + "
            f"period_timing {column.period_timing} + book_vs_bank "
            f"{column.book_vs_bank} + contribution "
            f"{column.contribution} + accrual {column.accrual}"
        )


def _column(
    *, balance="0.00", income="0.00", expense="0.00", net="0.00",
    elsewhere="0.00", period_timing="0.00", book_vs_bank="0.00",
    contribution="0.00", accrual="0.00",
):
    """Build one hand-specified :class:`GridColumn` for the flag tests.

    The visibility rule is pure logic over a column's values and says nothing
    about where they came from, so it is graded on hand-built columns: a
    data-driven oracle would have to seed a shape for each of the rule's arms
    and would still only assert the rule through whatever those shapes happen
    to produce.  ``TestTheRemainderIsWhatTheRowsCannotExplain`` grades the
    producer-built remainders themselves.

    Every parameter is a STRING and every field is a ``Decimal``: neither
    modelled tier is optional since plan step X-g3a (ruling R-AJ (c)), so
    ``None`` is not a value this builder can produce and no test can assert a
    shape the producer cannot be in.

    ``reconciliation`` split into ``period_timing`` and ``book_vs_bank`` at
    plan step S1-c (ruling R-DH (f)), and the builder takes both separately
    rather than one figure it fans out, because R-O's visibility question is
    now asked of each row INDEPENDENTLY -- a builder that could not set them
    apart could not express the case the split exists for.
    """
    return balance_at.GridColumn(
        balance=Decimal(balance),
        income=Decimal(income),
        expense=Decimal(expense),
        net=Decimal(net),
        elsewhere=Decimal(elsewhere),
        period_timing=Decimal(period_timing),
        book_vs_bank=Decimal(book_vs_bank),
        contribution=Decimal(contribution),
        accrual=Decimal(accrual),
    )


def _seed_grid_activity(db, seed_user, periods):
    """Add one projected income and one projected expense to the grid account.

    Gives the columns something to be non-zero ABOUT: an all-empty account
    reports zeros for every figure, so the identity would hold vacuously and
    the assertions above would prove nothing.

    Both rows are dated FORWARD of the read's as-of (``seed_periods_today``
    places today in period 4), so ruling R-G's clamp leaves them in the columns
    they were budgeted to.  A past-dated still-projected row would land at
    ``as_of + 1`` instead -- correct, and the subject of the sibling test.
    """
    scenario = get_baseline_scenario(seed_user["user"].id)
    account = seed_user["account"]
    one_off_row_of(
        periods[6],
        name="Paycheck",
        amount=Decimal("2400.00"),
        user_id=periods[6].user_id,
        account_id=account.id,
        scenario_id=scenario.id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
    )
    one_off_row_of(
        periods[7],
        name="Rent",
        amount=Decimal("1450.00"),
        user_id=periods[7].user_id,
        account_id=account.id,
        scenario_id=scenario.id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
    )
    db.session.commit()
