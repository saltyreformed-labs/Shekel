"""Loan payments and growing assets: a loan's payment template and a retired payment, and
appreciating and investment accounts.
"""

from decimal import Decimal

from ._loans import insert_trueup_event, create_loan_account, loan_params_for
from ._anchors import open_books_before_the_first_assertion
from ._prices_and_rules import state_template_price, make_cadence_rule


def bind_rule_to_loan(rule, account_id):
    """Bound a fixture's NEWLY built recurrence rule's START to its loan's contract.

    **A test helper since plan step R7d-g-3, moved here from
    ``loan_recurrence_sync``** (plan ledger row **REC-526**): its last
    application caller, the loan dashboard's create door, built its rule
    from :func:`~app.services.loan_recurrence_sync.loan_cadence_start` and
    then called this on the same rule -- the same pair re-derived through the
    same producer, a no-op by construction -- and the generic transfer form's
    call went at R7d-g-2 (ruling **R-R85**).  What fixtures still need is the
    composition the app's doors perform in two steps: author a rule with a
    fixture day, then put the loan's own first contractual installment onto
    it.  Re-stated here over the PUBLIC producers -- ``loan_cadence_spec`` for
    the wanted spec, ``reauthor_rule`` for the write -- with the production
    writer's two idempotency stages (``_sync_loan_cadence``, which the doors
    run at every point a definition becomes the standing payment, compares
    the authored spec and then the resolved one before it writes); its
    refusal translation and its audit event are the doors', not a fixture's.
    A no-op unless *account_id* is a configured loan, so a fixture may call
    it for ANY destination without a type check.

    Takes the rule DIRECTLY rather than looking it up from the account, which
    the production sync must do: that lookup returns the account's OLDEST
    active recurring template, so a fixture building a second recurring
    payment into the same loan would leave the NEW rule unbound while
    re-binding the old one.

    Args:
        rule: The just-built ``RecurrenceRule``, before generation.
        account_id: The transfer's destination account (any kind).

    Raises:
        EmptyAuthoredWindowError: The re-derived first installment falls after
            a stop the rule's owner authored -- the write door's own refusal,
            untranslated: the ``ValidationError`` sentence naming the transfer
            is ``_sync_loan_cadence``'s and belongs to the doors that run it.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.loan_loaders import load_loan_params
    from app.services.loan_recurrence_sync import loan_cadence_spec
    from app.services.pay_calendar import calendar_for
    from app.services.recurrence import (
        reauthor_rule, recurrence_spec, resolve,
    )
    params = load_loan_params(account_id)
    if params is None:
        return
    current = recurrence_spec(rule)
    wanted = loan_cadence_spec(current, params)
    if wanted == current:
        return
    calendar = calendar_for(rule.user_id)
    if resolve(wanted, calendar) == resolve(current, calendar):
        return
    reauthor_rule(rule, wanted, calendar)


def make_loan_payment_template(
    db_session, seed_user, loan_account, amount="200.00", *,
    derive_from_loan=True, extra_principal="0.00", cadence=None,
    fires_on_day=None,
):
    """Create the recurring transfer a LOAN payment actually is.

    :func:`make_transfer_template`'s loan-shaped sibling, and the difference is
    the ``budget.loan_payment_settings`` row.  ``routes/loan/payment_transfer.py``
    writes one on every loan payment IT creates -- it is what carries the MODE
    (``derive_from_loan``) and the standing overpayment -- so a fixture pointing
    the generic builder at a loan builds a definition the LOAN's own door would
    never leave behind.

    **A settings-less recurring transfer into a loan is still REACHABLE**, and
    an adversarial review of plan step R7d-a corrected an earlier version of
    this paragraph that said otherwise: ``POST /transfers`` offers every active
    account as a destination and attaches no settings row
    (``routes/transfers/templates.py``), and production holds ZERO
    ``loan_payment_settings`` rows, so it is the state the developer's own two
    loans are in.  What is unreachable is that state arising from the loan
    dashboard's create-transfer button, which is the door these fixtures stand
    in for.

    **It went unnoticed while nothing read the definition for an unmaterialised
    installment.**  R7d-a made the forward plan price every installment no row
    covers from the loan's own standing payment, so the generic builder's
    arbitrary base -- ``$200.00``, named "To Savings" -- started meaning "this
    is what the owner pays the mortgage", and six tests whose docstrings state
    the CONTRACTUAL figure began asserting against a loan being paid a fifth of
    it.  The numbers were right; the fixture was not.

    Defaults to DERIVE mode, which is what the loan dashboard's own
    create-transfer button writes: the payment IS the loan's P&I plus escrow
    plus any standing extra, so the *amount* is a snapshot and no test has to
    keep it in step with the loan's terms.  Pass ``derive_from_loan=False`` to
    build the MANUAL payment an owner types a figure for.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict (supplies the owner and the
            checking account the payment leaves from).
        loan_account: The destination loan account.
        amount: The price stated into its series before the settings row is
            attached.  In derive mode that series is dormant and the live
            derivation answers; in manual mode it is the figure stated.
        derive_from_loan: The settings row's mode.
        extra_principal: The settings row's standing monthly overpayment.
        cadence: The :class:`~tests.oracles.recurrence_baseline.ShapeCadence`
            to author.  ``None`` -- the default -- authors what the loan door
            (``routes/loan/payment_transfer.py``) authors: a MONTHLY rule on
            the loan's own ``payment_day``, its start bound to the first
            contractual installment through ``bind_rule_to_loan``.  **It was
            the every-paycheck rule :func:`make_transfer_template` authors
            until plan step R16-b-2**, inherited from the fixtures this
            replaced, and the difference was invisible while the forward plan
            synthesized one contractual slot a month whatever the definition's
            cadence said (finding **D48**).  The plan sums each definition on
            its OWN cadence now, so an every-paycheck DERIVE-mode payment pays
            a full P&I twenty-six times a year and retires a 24-month loan in
            sixteen -- which is what that definition says, and not what 46
            fixtures meaning "the loan's own payment" said.  A test about an
            every-paycheck payment states ``EVERY_PERIOD`` here.
        fires_on_day: The day of the month a calendar *cadence* first fires on,
            forwarded to :func:`make_cadence_rule`.  Unread for the default,
            whose start the loan door's sync writes.

    Returns:
        The flushed ``TransferTemplate``, its ``recurrence_rule`` set.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.models.loan_payment_settings import LoanPaymentSettings
    from app.models.transfer_template import TransferTemplate

    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=loan_account.id,
        name=f"Loan Payment {loan_account.id}",
    )
    db_session.add(template)
    db_session.flush()
    # **Priced BEFORE the settings row is attached, and the order is the same
    # one ``track_payment`` takes.**  ``template_amount_service.owns_its_amount``
    # is False for a DERIVE-mode loan payment, so the write door would refuse a
    # version stated after the flip -- and the versions a template already holds
    # stay as the record of what was stated while it was manual, which is what
    # a MANUAL payment's base is read from since plan step X-au-f.
    state_template_price(template, Decimal(amount))
    # Attached through the relationship so it flushes with the template, the
    # way the route attaches it.
    template.settings = LoanPaymentSettings(
        derive_from_loan=derive_from_loan,
        extra_principal=Decimal(extra_principal),
    )
    db_session.flush()
    # The definition first, then the cadence onto it (plan step R-F6), then
    # -- for the door's own shape -- the loan's start onto the cadence
    # (``bind_rule_to_loan``, plan step C9a), the order ``track_payment``
    # takes.
    if cadence is None:
        from app.services.loan_loaders import load_loan_params
        from tests.oracles.recurrence_baseline import MONTHLY

        # An amortizing account with NO params is not a loan the door can
        # bind to (``bind_rule_to_loan`` is a no-op for it); the rule is
        # monthly on the 1st and starts where the schedule's first 1st is.
        params = load_loan_params(loan_account.id)
        rule = make_cadence_rule(
            template, MONTHLY,
            fires_on_day=1 if params is None else params.payment_day,
        )
        bind_rule_to_loan(rule, loan_account.id)
        db_session.flush()
    else:
        make_cadence_rule(template, cadence, fires_on_day=fires_on_day)
    return template


def make_retired_loan_payment(
    db_session, seed_user, *, origination_date, cleared_on, payment_day=1,
    name="Retired Loan",
):
    """Create a MONTHLY loan payment whose loan was trued to ZERO on *cleared_on*.

    The fixture the three surfaces that read a definition's stop share since
    plan step R7d-e -- the Recurring surface, the obligations aggregator and
    the ``/savings`` floor -- because each asserts the same money fact: a
    payment against a loan that is finished leaves the committed totals on the
    day the loan closed.  One builder, so the three cannot describe three
    different loans.

    The loan is ``$12,000.00`` at 5% over 24 months, originating
    *origination_date* with the contractual *payment_day*; its first
    installment is that day of the month AFTER origination
    (``installment_calendar.first_installment_date``: a loan closed 2026-05-01
    with a ``payment_day`` of 1 owes first on 2026-06-01, not on the day it
    closed).  The true-up to ``$0.00`` on *cleared_on* retires it, so its
    closing date is
    *cleared_on* -- the day it LAST became closed (plan step ``recurrence:R7d-h``)
    -- and the definition has fired once wherever *cleared_on* follows the
    first installment, so the derived stop is a date and not "never runs".

    The payment is bound to the loan through the production door for the
    OPENING bound (``bind_rule_to_loan``), so ``starts_on`` is the contract's
    first installment rather than a fixture day.  **Its ``end_date`` column is
    left NULL**: nothing stored could supply the stop, so whatever a reader
    names about it came from the derivation.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict (the owner and the checking
            account the payment leaves from).
        origination_date: The loan's origination date.
        cleared_on: The day the balance is trued to zero.
        payment_day: The contractual day of the month (default 1).
        name: The loan account's name.

    Returns:
        ``(loan, template)``, committed.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from tests.oracles.recurrence_baseline import MONTHLY

    loan = create_loan_account(
        seed_user, db_session, name=name,
        principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
        origination_date=origination_date, payment_day=payment_day,
    )
    insert_trueup_event(
        loan_params_for(db_session, loan.id), Decimal("0.00"),
        anchor_date=cleared_on,
    )
    template = make_loan_payment_template(
        db_session, seed_user, loan, cadence=MONTHLY, fires_on_day=payment_day,
    )
    bind_rule_to_loan(template.recurrence_rule, loan.id)
    db_session.commit()
    return loan, template


def make_appreciating_account(seed_user, db_session, anchor_period, balance, rate):
    """Create a Property account (APPRECIATING) with AssetAppreciationParams.

    The shared appreciating-asset builder for the balance-seam parity
    suite and the cross-page balance-equality lock (promoted from the
    per-suite ``_make_property`` copies).  Routes the account through the
    canonical ``account_service.create_account`` factory (so it gets its
    origination ``AccountAnchorHistory`` row), then attaches the
    ``AssetAppreciationParams`` row that carries the annual appreciation
    rate so the account classifies APPRECIATING.  Commits before
    returning so the account is fully resolvable.

    **The opening assertion is stamped at the anchor period's first day**
    (via :func:`reassert_balance_on`) -- finding N-77, fixed at plan step
    X-g2a for the reason :func:`create_hysa_account` was at X-c2a, and read
    there for the full argument.  ``account_service.create_account`` writes that
    row with the WALL CLOCK, and from plan step X-g2b a Property's appreciation
    accrues only forward of its LATEST assertion (ruling R-Y), so an unpinned
    opening is the newest assertion, lands past the suite's seeded horizon, and
    the account then appreciates NOTHING anywhere -- a state production cannot
    reach.  A test that needs a MID-period or later assertion appends its own,
    exactly as the interest suites do.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        anchor_period: The :class:`~app.models.pay_period.PayPeriod` to
            anchor the account against; its ``id`` becomes the account's
            ``current_anchor_period_id``.
        balance: The user-set market value, used as the anchor balance
            (Decimal -- construct from a string per the coding standard).
        rate: The annual appreciation rate as a Decimal fraction (e.g.
            ``Decimal("0.03000")`` for 3%).

    Returns:
        The created Property :class:`~app.models.account.Account`.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention); load
    # the models lazily, the same way the loan helpers above do.
    # pylint: disable=import-outside-toplevel
    from app.models.asset_appreciation_params import AssetAppreciationParams
    from app.models.ref import AccountType
    from app.services import account_service

    property_type = (
        db_session.query(AccountType).filter_by(name="Property").one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=property_type.id,
            name="House",
            anchor_balance=balance,
            observed_on=anchor_period.start_date,
        ),
    )
    db_session.add(account)
    db_session.flush()
    db_session.add(AssetAppreciationParams(
        account_id=account.id, annual_appreciation_rate=rate,
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


def make_investment_account(
    seed_user, db_session, anchor_period, balance, name="401k",
    employer_type="none", match_pct=None, match_cap_pct=None,
):
    """Create a 401(k) account (INVESTMENT) with InvestmentParams (7% return).

    The shared investment-account builder for the balance-seam parity
    suite and the cross-page balance-equality lock (promoted from the
    per-suite ``_make_401k`` copies).  Routes the account through the
    canonical ``account_service.create_account`` factory, then attaches an
    ``InvestmentParams`` row (7% assumed annual return) so the account
    classifies INVESTMENT.  Commits before returning so the account is
    fully resolvable.

    **The opening assertion is stamped at the anchor period's first day**
    (via :func:`reassert_balance_on`) -- finding N-77, fixed at plan step
    X-g2a; see :func:`make_appreciating_account` beside it and
    :func:`create_hysa_account` for the full argument.  From plan step X-g2b an
    investment's growth accrues only forward of its LATEST assertion (ruling
    R-Y), and the factory writes that row with the WALL CLOCK, so an unpinned
    opening leaves the account growing nowhere.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        anchor_period: The :class:`~app.models.pay_period.PayPeriod` to
            anchor the account against; its ``id`` becomes the account's
            ``current_anchor_period_id``.
        balance: The opening anchor balance (Decimal -- construct from a
            string per the coding standard).
        name: The account name (default ``"401k"``); parameterised so a
            caller can seed two investment accounts for one user without
            colliding on the ``(user_id, name)`` unique constraint.
        employer_type: The :class:`~app.enums.EmployerContributionTypeEnum`
            value (default ``"none"`` -- no employer contribution).
        match_pct: Employer match percentage (Decimal) or ``None``.
        match_cap_pct: Employer match cap percentage (Decimal) or ``None``.

    Returns:
        The created 401(k) :class:`~app.models.account.Account`.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention); load
    # the models lazily, the same way the loan helpers above do.
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import EmployerContributionTypeEnum
    from app.models.investment_params import InvestmentParams
    from app.models.ref import AccountType
    from app.services import account_service

    inv_type = db_session.query(AccountType).filter_by(name="401(k)").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=inv_type.id,
            name=name,
            anchor_balance=balance,
            observed_on=anchor_period.start_date,
        ),
    )
    db_session.add(account)
    db_session.flush()
    db_session.add(InvestmentParams(
        account_id=account.id,
        assumed_annual_return=Decimal("0.07000"),
        employer_contribution_type_id=ref_cache.employer_contribution_type_id(
            EmployerContributionTypeEnum(employer_type),
        ),
        employer_match_percentage=match_pct,
        employer_match_cap_percentage=match_cap_pct,
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
