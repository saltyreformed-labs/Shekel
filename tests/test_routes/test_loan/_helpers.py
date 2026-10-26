"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_loan.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import AcctTypeEnum, StatusEnum
from app.models.escrow_line import EscrowComponentVersion, EscrowLine
from app.extensions import db as _db
from app.models.ref import Status
from app.services.balance_at import BalanceContext
from app.services.recurrence import ClosesOn
from app.services.recurring_definition import resolved_definition
from app.services.transfer_service import (
    TransferSpec,
    create_transfer,
    update_transfer,
)

from tests._test_helpers import (
    add_escrow_line,
    an_entered_day,
    create_loan_account,
    loan_params_for,
    make_loan_payment_template,
    on_both_sides,
)
from tests.oracles.recurrence_baseline import MONTHLY
from app.models.amount_ownership import AmountOwnership


# ── Helpers ──────────────────────────────────────────────────────────

# The day before the seeded calendar's first payday (``seed_periods`` rebuilds
# the owner's schedule from 2026-01-02): the date every loan built through
# ``_create_loan_account`` states its balance for -- the setup door's "as of"
# day.  See that helper.
_TRACKED_FROM = date(2026, 1, 1)


def _create_loan_account(seed_user, db_session, account_type, name, principal,
                         rate, term, orig_date, payment_day, is_arm=False):
    """Helper to create a loan account with params for any amortizing type.

    Test contract: the ``principal`` argument is the value the test
    expects to see displayed as "Current Principal" on the loan
    card.  Pre-Commit-15 this worked because the dashboard rendered
    the unmaintained stored ``current_principal`` column verbatim;
    post-Commit-15 the dashboard reads the resolver's current_balance
    (E-18) which is derived from :class:`LoanAnchorEvent`, not the
    stored column.

    To preserve the test contract without rewriting every caller,
    this helper builds the loan with an ``original_principal`` of
    ``principal + 5000`` (simulating "$5,000 already paid down before
    the test starts") and states the lower ``principal`` as the balance
    "as of" :data:`_TRACKED_FROM` -- the day before the seeded calendar's
    first payday, i.e. "the balance when the owner began tracking this
    loan".  That is the setup door's own write (plan step R20, ruling
    R-R72 part 3): a ``tracking_start`` event, recorded by the shared
    factory exactly as ``loan.create_params`` records it.  The origination
    anchor is SYNTHESIZED from the params (no stored ``LoanAnchorEvent`` --
    matching production's ``create_params`` since the read switch retired
    that write), so the loan carries exactly ONE stored anchor event: the
    tracking-start.  *It was a ``user_trueup`` appended after the fact until
    R20*; the two sources differ in label alone, so no figure moved.

    **The assertion is dated when tracking began, not the day after
    origination** (plan step R16-b-2, rulings R-R71 / R-R72).  A loan's
    balance replays from its LATEST assertion over the CONTRACT's calendar,
    so every contractual month after the assertion that no payment record
    covers is charged.  Dated ``orig_date + 1 day``, as it was until
    R16-b-2, a 2023 mortgage read as unpaid for three years and never
    cleared -- which is what those records said, and not what any test here
    means.  Dated the day before the seeded calendar opens, every payment a
    test records (``seed_periods`` runs from 2026-01-02, so its earliest due
    date is 2026-02-01) falls AFTER the assertion, and only the months a
    test leaves unrecorded between it and the frozen 2026-03-20 today are
    charged.

    When ``principal == 0`` the gap is the full $5,000, so the
    assertion at $0 produces a paid-off loan state -- what
    ``test_refinance_paid_off_loan`` needs.  When ``principal > 0``
    the assertion at ``principal`` produces a partially-paid loan
    state -- what ``test_refinance_principal_auto_calculated`` and
    every other refinance / debt-card test needs.

    Routes through :func:`tests._test_helpers.create_loan_account` (the ONE
    shared loan builder), so the assertion is reconciled into the loan's
    genesis posting ledger in the same transaction that writes it -- exactly
    what ``loan.create_params`` does in production.  ``is_arm`` is set after:
    the flag alone moves no posting, since the rate-period engine's ARM
    branch fires only on a first-adjustment count this helper never sets
    (``rate_period_engine.py``, the ``terms.is_arm and first is not None``
    condition), and the origination rate row holds either way.  The
    hand-rolled block this replaced opened no ledger at all, so every loan
    here exercised the no-ledger fallback production never takes.

    Args:
        seed_user: The ``seed_user`` (or ``seed_second_user``) fixture dict.
        db_session: The test ``db.session``.
        account_type: The :class:`~app.enums.AcctTypeEnum` member to create
            the loan account as.
        name: The account name.
        principal: The loan's current balance (the trueup anchor).
        rate: The origination annual rate as a Decimal fraction.
        term: The loan term in months.
        orig_date: The loan origination date.
        payment_day: The day-of-month payment day.
        is_arm: Whether the loan is adjustable-rate.

    Returns:
        The created loan :class:`~app.models.account.Account`.
    """
    account = create_loan_account(
        seed_user, db_session, name=name,
        principal=principal + Decimal("5000.00"), rate=rate, term=term,
        origination_date=orig_date, payment_day=payment_day,
        account_type=account_type,
        tracked_balance=principal, tracked_from=_TRACKED_FROM,
    )
    params = loan_params_for(db_session, account.id)
    params.is_arm = is_arm
    db_session.commit()
    return account


def _create_auto_loan(seed_user, db_session, name="My Auto Loan"):
    """Helper: auto loan account with params."""
    return _create_loan_account(
        seed_user, db_session, AcctTypeEnum.AUTO_LOAN, name,
        Decimal("25000.00"), Decimal("0.05000"), 60,
        date(2025, 1, 1), 15,
    )


def _payment_template(db_session, seed_user, acct):
    """Return the loan's recurring payment template (the settings doors' URLs name it)."""
    from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

    tpl = (
        db_session.query(TransferTemplate)
        .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
        .first()
    )
    assert tpl is not None
    return tpl


def _create_mortgage(seed_user, db_session, name="My Mortgage"):
    """Helper: mortgage account with params."""
    return _create_loan_account(
        seed_user, db_session, AcctTypeEnum.MORTGAGE, name,
        Decimal("250000.00"), Decimal("0.06500"), 360,
        date(2023, 6, 1), 1,
    )


def _create_other_loan(second_user, db_session,
                       account_type=AcctTypeEnum.AUTO_LOAN):
    """Create a loan account owned by the second user.

    A $20,000 loan (the origination anchor -- the hand-rolled block this
    replaced wrote no trueup, so the origination principal IS the resolved
    balance) at 4% over 48 months, originated 2024-06-01.  Built through the
    shared factory, so its genesis posting ledger is opened with it.
    """
    return create_loan_account(
        second_user, db_session, name="Other Loan",
        principal=Decimal("20000.00"), rate=Decimal("0.04000"), term=48,
        origination_date=date(2024, 6, 1), payment_day=1,
        account_type=account_type,
    )


# ── Escrow Posting-Sync Tests (E1b) ──────────────────────────────────


def _make_sync_loan(seed_user, db_session):
    """Create a $100,000 / 6% mortgage originated 2026-01-01 (clean arithmetic).

    Interest on a payment folded against the origination principal is
    100000 * 0.06 / 12 = 500.00 exactly, so a $1,000 payment splits 500 interest /
    500 principal with no rounding -- the shape the E1b sync tests fold against.
    Originated the month before the one payment's 2026-02-01 installment (plan
    step recurrence:R16-c-2, ruling R-R101): every contractual installment from
    origination is charged now, so the 2023-06-01 it carried until then read as
    thirty-two unpaid months ahead of that payment.
    """
    return create_loan_account(
        seed_user, db_session, name="Sync Mortgage",
        principal=Decimal("100000.00"), rate=Decimal("0.06000"), term=360,
        origination_date=date(2026, 1, 1), payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )


# Each escrow WRITE route, as a (precondition + POST) callable returning the
# response.  Every escrow effective date is strictly AFTER the settled payment's
# 2026-02-01 DUE date -- the forward-only boundary, which is the date its split
# resolves escrow on (ruling D5, finding N-34), NOT the 2026-01-16 pay-period
# start these dates were first chosen against.  So no case moves the settled
# split, and the only ledger change the sync makes is to HEAL the forged cash
# entry, proving the route ran the sync regardless of its escrow op.


def _write_add_escrow(client, db_session, loan):
    """add_escrow: POST a brand-new line, its opening version future-dated."""
    return client.post(
        f"/accounts/{loan.id}/loan/escrow",
        data={"name": "Property Tax", "annual_amount": "4800.00",
              "effective_date": "2026-04-01"},
    )


def _write_delete_escrow(client, db_session, loan):
    """delete_escrow: tombstone a post-boundary line as of today."""
    line = add_escrow_line(
        db_session, loan.id, "Old Tax", Decimal("6000.00"),
        effective_date=date(2026, 3, 1),
    ).line
    db_session.commit()
    return client.post(f"/accounts/{loan.id}/loan/escrow/{line.id}/delete")


def _write_add_version(client, db_session, loan):
    """add_escrow_version: schedule a future change on an existing line."""
    line = add_escrow_line(
        db_session, loan.id, "Tax", Decimal("6000.00"),
        effective_date=date(2026, 3, 1),
    ).line
    db_session.commit()
    return client.post(
        f"/accounts/{loan.id}/loan/escrow/{line.id}/version",
        data={"annual_amount": "7200.00", "effective_date": "2026-05-01"},
    )


def _write_edit_version(client, db_session, loan):
    """edit_escrow_version: edit a future (post-boundary) version's amount."""
    version = add_escrow_line(
        db_session, loan.id, "Tax", Decimal("6000.00"),
        effective_date=date(2026, 4, 1),
    )
    db_session.commit()
    return client.post(
        f"/accounts/{loan.id}/loan/escrow/version/{version.id}/edit",
        data={"annual_amount": "7200.00"},
    )


def _write_delete_version(client, db_session, loan):
    """delete_escrow_version: delete a future scheduled version."""
    version = add_escrow_line(
        db_session, loan.id, "Tax", Decimal("6000.00"),
        effective_date=date(2026, 5, 1),
    )
    db_session.commit()
    return client.post(
        f"/accounts/{loan.id}/loan/escrow/version/{version.id}/delete",
    )


def _write_rename(client, db_session, loan):
    """rename_escrow_line: rename a line in place (display-only)."""
    line = add_escrow_line(
        db_session, loan.id, "Tax", Decimal("6000.00"),
        effective_date=date(2026, 3, 1),
    ).line
    db_session.commit()
    return client.post(
        f"/accounts/{loan.id}/loan/escrow/{line.id}/rename",
        data={"name": "Property Tax"},
    )


def _write_merge(client, db_session, loan):
    """merge_escrow_line: fold a removed predecessor into the active line."""
    boundary = date(2026, 4, 1)
    old = add_escrow_line(
        db_session, loan.id, "Old Tax", Decimal("6000.00"),
        effective_date=date(2026, 3, 1),
    ).line
    db_session.add(EscrowComponentVersion(
        line_id=old.id, effective_date=boundary,
        annual_amount=Decimal("0.00"), is_removed=True,
    ))
    add_escrow_line(
        db_session, loan.id, "New Tax", Decimal("7200.00"),
        effective_date=boundary,
    )
    db_session.commit()
    new = (
        db_session.query(EscrowLine)
        .filter_by(account_id=loan.id, name="New Tax").one()
    )
    return client.post(
        f"/accounts/{loan.id}/loan/escrow/{new.id}/merge",
        data={"source_line_id": old.id},
    )


_ESCROW_WRITES = [
    ("add_escrow", _write_add_escrow),
    ("delete_escrow", _write_delete_escrow),
    ("add_escrow_version", _write_add_version),
    ("edit_escrow_version", _write_edit_version),
    ("delete_escrow_version", _write_delete_version),
    ("rename_escrow_line", _write_rename),
    ("merge_escrow_line", _write_merge),
]


# ── Payoff Chart Shape Tests (Commit 4) ─────────────────────────────


def _parse_chart_array(html, attr):
    """Extract a chart ``data-<attr>='...'`` JSON array.

    The template renders Chart.js datasets via ``|tojson`` inside
    single-quoted HTML attributes, so the content between the
    single quotes is a JSON-encoded list.  Returns the parsed
    Python list, or ``None`` when the attribute is absent.

    Args:
        html: Rendered HTMX fragment.
        attr: Attribute name without the ``data-`` prefix
            (``"original"``, ``"committed"``, ``"accelerated"``,
            ``"labels"``).
    """
    import json as _json  # pylint: disable=import-outside-toplevel
    import re as _re  # pylint: disable=import-outside-toplevel

    match = _re.search(rf"data-{attr}='([^']*)'", html)
    if match is None:
        return None
    return _json.loads(match.group(1))


def _parse_band_chart(html):
    """Extract the loan detail band chart's ``data-chart`` JSON object.

    The Fable 5 band renders a single ``{labels, balance, current_index}`` JSON
    object on ``#loan-balance-chart`` (loan_detail.js splits the balance line at
    ``current_index`` and overlays the payoff lever's preview).  Returns the
    parsed dict, or ``None`` when absent (e.g. a paid-off loan renders no chart).

    Args:
        html: Rendered loan dashboard HTML.
    """
    return _parse_chart_array(html, "chart")


def _label_to_month_tuple(label):
    """Convert a ``"%b %Y"`` chart label to a (year, month) tuple.

    Chart labels are formatted as ``"Mar 2026"`` via
    ``strftime('%b %Y')``; ``strptime`` round-trips them so the
    comparison test can build an ordered (year, month) key for
    today's-month boundary checks.
    """
    from datetime import datetime  # pylint: disable=import-outside-toplevel

    parsed = datetime.strptime(label, "%b %Y")
    return (parsed.year, parsed.month)


# ── Section 5 Regression Baseline ──────────────────────────────────────

# All five amortizing account types with realistic parameters.
_AMORTIZING_TYPES = [
    (AcctTypeEnum.MORTGAGE, Decimal("250000.00"), Decimal("0.06500"), 360, 600),
    (AcctTypeEnum.AUTO_LOAN, Decimal("25000.00"), Decimal("0.05000"), 60, 120),
    (AcctTypeEnum.STUDENT_LOAN, Decimal("45000.00"), Decimal("0.04500"), 120, 300),
    (AcctTypeEnum.PERSONAL_LOAN, Decimal("10000.00"), Decimal("0.08000"), 48, 120),
    (AcctTypeEnum.HELOC, Decimal("50000.00"), Decimal("0.07250"), 180, 360),
]


# ── Payment Integration Tests (Commit 5.1-2) ────────────────────────


def _create_transfer_to_loan(seed_user, loan_account, period, amount,
                              status_enum=StatusEnum.PROJECTED):
    """Create a transfer from checking to loan account via the transfer service.

    Through the production code path, so the transfer is the one the app
    writes; no row is inserted directly.

    **A SETTLED one is created Projected, then settled** through the settle
    verb (``update_transfer`` with *status_enum*), because the create makes
    no settled transfer (ruling **R-BAL379**); it was created in its settled
    status until then, and the settle stores what that create stored: the
    transfer's own figure, and an ``entered`` day at its period's start on
    both sides.  The day is STATED because a settle with none dates both
    sides on the owner's today -- this package's frozen 2026-03-20 -- rather
    than in the payment's own period, which would move every bounded read.
    Period start is the exact visibility these fixtures had before step E1a,
    when a born-settled create left the settle day NULL and every reader fell
    back to the period start, so no test's arithmetic moves.  The settled
    status is Paid (``DONE``): the transfer workflow settles to Done alone,
    so the settle refuses ``RECEIVED``, which no transfer door writes.
    """
    status_id = ref_cache.status_id(status_enum)
    settled = _db.session.get(Status, status_id).is_settled
    transfer = create_transfer(
        TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=loan_account.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(amount),
            status_id=(
                ref_cache.status_id(StatusEnum.PROJECTED) if settled
                else status_id
            ),
            category_id=seed_user["categories"]["Rent"].id,
        ),
    )
    if settled:
        update_transfer(
            transfer.id, seed_user["user"].id, status_id=status_id,
            side_days=on_both_sides(
                seed_user["account"].id, loan_account.id,
                an_entered_day(period.start_date),
            ),
        )
    return transfer


# ── Payment Breakdown Tests (Commit 5.14-1) ────────────────────────


def _add_escrow(db_session, account_id, name, annual_amount,
                inflation_rate=None):
    """Add an escrow line (with one origination-dated version) to a loan.

    Thin wrapper over the shared :func:`tests._test_helpers.add_escrow_line`;
    the version defaults to the loan's origination date, so the escrow is active
    for the current-period breakdown these tests read.
    """
    return add_escrow_line(
        db_session, account_id, name, annual_amount,
        inflation_rate=inflation_rate,
    )


# -- Amortization Schedule Tab Tests (Commit 5.13-1) ------------------


def _create_fresh_mortgage(seed_user, db_session, principal=Decimal("250000.00"),
                           rate=Decimal("0.06500"), term=360, payment_day=1,
                           origination_date=None):
    """Create a mortgage with predictable schedule length.

    By default, origination is the first of last month so the
    schedule's first payment month is this month.  Tests that need
    the schedule to align with specific ``seed_periods`` indices must
    pass ``origination_date`` explicitly so the alignment does not
    drift as today's date advances.

    States no balance after origination, so the schedule aligns with the
    full term (no early-payoff due to a lower current balance).

    Args:
        seed_user: The seed_user fixture dict.
        db_session: Active database session.
        principal: Loan principal (Decimal).  Default $250,000.
        rate: Annual interest rate (Decimal).  Default 6.5%.
        term: Term in months.  Default 360.
        payment_day: Payment day of month.  Default 1.
        origination_date: Optional explicit origination date.  Default
            is the first of last month relative to ``date.today()``.
            Pass an explicit date for tests that depend on schedule
            alignment with fixed-date fixtures.
    """
    if origination_date is None:
        # Origination one month before today so the first payment month
        # is the current month (schedule starts month after origination).
        first_of_this_month = date.today().replace(day=1)
        if first_of_this_month.month == 1:
            origination_date = first_of_this_month.replace(
                year=first_of_this_month.year - 1, month=12,
            )
        else:
            origination_date = first_of_this_month.replace(
                month=first_of_this_month.month - 1,
            )
    return _create_loan_account_exact(
        seed_user, db_session, AcctTypeEnum.MORTGAGE, "Fresh Mortgage",
        principal, rate, term, origination_date, payment_day,
    )


def _create_loan_account_exact(seed_user, db_session, account_type, name,
                                original_principal, rate, term, orig_date,
                                payment_day):
    """Like :func:`_create_loan_account` but with NO stated balance.

    The loan carries only its origination anchor, so ``original_principal``
    IS its resolved balance -- there is no ``+ $5,000`` paid-down gap and no
    assertion event.  (The hand-rolled block this replaced also took a
    ``current_principal``, but it only ever landed in the non-authoritative
    ``LoanParams.current_principal`` column -- dropped at plan step R20 --
    and the loan account's unread anchor balance: with no assertion event,
    the resolver and the genesis ledger both seeded from
    ``original_principal`` regardless.  The parameter is gone rather than
    kept as a decorative no-op.)

    Routes through the shared factory, so the loan's genesis posting ledger is
    opened in the same transaction as its ``LoanParams``.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        account_type: The :class:`~app.enums.AcctTypeEnum` member.
        name: The account name.
        original_principal: The origination principal (the resolved balance).
        rate: The origination annual rate as a Decimal fraction.
        term: The loan term in months.
        orig_date: The loan origination date.
        payment_day: The day-of-month payment day.

    Returns:
        The created loan :class:`~app.models.account.Account`.
    """
    return create_loan_account(
        seed_user, db_session, name=name, principal=original_principal,
        rate=rate, term=term, origination_date=orig_date,
        payment_day=payment_day, account_type=account_type,
    )


# -- Recurrence End Date Auto-Update Tests (Commit 5.9-1) ----------------


def _create_transfer_template(seed_user, db_session, loan_account):
    """Create the recurring LOAN PAYMENT these tests wire a sync through.

    Returns ``(template, rule)`` so tests can inspect both objects.

    **It delegates to the shared builder, and the reason is its settings row.**
    This used to construct the template inline with no
    ``budget.loan_payment_settings`` -- a recurring transfer into a loan that
    is not a loan payment, which ``routes/loan/payment_transfer.py`` cannot
    produce.  Nothing read the difference until plan step **R7d-a** made the
    forward plan price an unmaterialised installment from the loan's own
    standing payment: the inline ``$1,500.00`` then meant "this is what the
    owner pays a $250,000 mortgage", the plan correctly reported a loan that
    never clears, and three tests asserting only that a payoff EXISTS failed on
    a fixture that had stopped describing a payable loan.  The three unused
    parameters (``amount``, ``end_date``, ``name``) went with the inline block;
    no caller passed one.
    """
    template = make_loan_payment_template(
        db_session, seed_user, loan_account,
        cadence=MONTHLY, fires_on_day=1,
    )
    # The shared builders' stated contract is "the caller commits", and every
    # caller of this one goes on to issue a request (plan step balance:X-i3).
    # A request holds its own transaction, so an uncommitted template is one
    # the route cannot see -- the fixture would be describing a state no
    # browser can produce.
    db_session.commit()
    return template, template.recurrence_rule


def _derived_stop_of(template, seed_user):
    """Return the composed door's derived stop for *template*, on a fresh pass."""
    return resolved_definition(
        template, BalanceContext.build(seed_user["user"].id),
    ).closing.derived


def _assert_column_untouched_and_stop_derived(rule, template, seed_user):
    """The post-R7d-g shape every chokepoint case asserts.

    The two closing-bound columns are exactly as the fixture left them
    (NULL), and the payoff the chokepoint moved is the composed door's DERIVED
    answer -- a real closing date, read through the same door generation
    reads -- rather than anything stored.
    """
    _db.session.refresh(rule)
    assert rule.end_date is None, (
        f"a chokepoint wrote end_date={rule.end_date}: a writer survived R7d-g"
    )
    assert rule.max_occurrences is None
    stop = _derived_stop_of(template, seed_user)
    assert isinstance(stop, ClosesOn), stop
    return stop


# ── Refinance Calculator Tests ──────────────────────────────────────────


def _create_exact_mortgage(seed_user, db_session):
    """Create a mortgage with exact known terms for hand-calculated tests.

    Uses equal original and current principal so the contractual payment
    matches exactly: M = P * [r(1+r)^n] / [(1+r)^n - 1] where
    P=200000, r=0.065/12, n=360.  Origination today so remaining
    months = 360.

    Built through the shared factory, so the loan's genesis posting ledger is
    opened with it (as production's ``loan.create_params`` does).
    """
    return create_loan_account(
        seed_user, db_session, name="Exact Test Mortgage",
        principal=Decimal("200000.00"), rate=Decimal("0.06500"), term=360,
        origination_date=date.today(), payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )
