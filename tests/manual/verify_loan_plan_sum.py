"""Dump what a loan's plan SUMS and what its posted ledger BOOKS, and what moves.

The regression harness for recurrence plan steps **R16-b-2** (doors 1-5),
**R16-c-1** (doors 6-7, its claim that merging the two folds moves nothing)
and **R16-c-2** (every door, and the POSTED money).  R16-b-2 makes the
balance seam's ESTIMATED tier sum EVERY definition paying into a loan on its
own cadence, price every occurrence no row answers (ruling **R-R64**), and
charge the CONTRACT's calendar (ruling **R-R68**).  R16-c-2 makes that
calendar the loan's ONE calendar, in the settled walk as well as the plan:
every contractual installment from origination is charged (rulings **R-R72**,
**R-R89**, **R-R100**), so a skipped month owes its interest in the POSTED
ledger too -- the next payment clears the arrears before it reaches principal.
Run it on a worktree at the base commit and on the branch, against the same
clone, and diff the two outputs from line 2.

**Every door prints the posted ledger's inputs as well as the plan**: each
settled payment's split and each anchor's correction off
:func:`~app.services.loan_ledger.walk_loan_ledger`, and the posting TARGETS
the walk implies -- what the next deploy's ``backfill_all_loan_postings``
reconciles the ledger to -- then the plan's payments, the trajectory's
projected splits, the payoff, a balance grid, the what-if lever and the
projected interest.  It reads only producers present on both sides of
R16-c-2, so the one file runs on both trees.

**The BASELINE and the TODAY read are expected byte-identical after
recurrence:R23** (a loan with no unpaid installment after its latest
statement moves nothing), **and seven doors are PLANTED.**  Doors 1-5 were
R16-b-2's; they MUST NOT move under R16-c-2 (each changes the plan's
payments, not the calendar's reach).  **Doors 6 and 7 MUST move under
R16-c-2**, and only as ruled: the reverted installments' months are charged
in the SETTLED walk now, so the latest settled payment clears them first
(its interest grows, its principal shrinks, and its posting target moves by
the same cents), the overdue catch-ups pay pure principal, and every later
balance carries the interest accrued on the un-reduced balance.  Both of the
developer's live loan payments are stated-price, monthly on the contractual
day, and every forward slot the schedule reaches is answered by a row, so on
unmodified data an all-green diff says nothing about whether the new code
runs.  Each door constructs a state in which the tiers differ:

* **DOOR 1 -- D47, a SECOND definition into the Mortgage**: a planted
  ``$500.00`` every-paycheck sweep, the developer's Emergency Fund transfer's
  shape, created into the Mortgage with its id FORCED below the payment's
  (the reachable ordering: the sweep authored first).  The old tier priced
  every uncovered installment from the definition with the lowest id -- the
  sweep -- so the loan read ``None`` against an escrow it does not
  cover; the sum adds the sweep's 26 occurrences a year beside the payment's
  and the payoff comes IN.  Planted fresh rather than re-pointing the archived
  sweep itself, whose soft-deleted undated rows claim every saved paycheck
  and would answer its occurrences until the horizon.
* **DOOR 2 -- D46, the reset door's mid-transaction state**: the Van's rows
  in periods that have STARTED are hard-deleted (what ``reset_pay_periods``
  does before it repopulates) and the plan is read in the hole.  The old tier
  priced an occurrence dated before ``as_of`` by neither tier, so the payoff
  read one installment LATE and door-bounded generation would write one
  installment past the loan's life; the sum prices it as the row generation
  would write (ruling **R-R64**) and the payoff does not move.
* **DOOR 3 -- occurrence identity, an ad-hoc EXTRA beside an unanswered
  installment**: a ``$2,000.00`` projected transfer into the Van dated in a
  month past the saved horizon, whose installment no row answers yet.  The
  old month-slot de-dup treated the extra as that month's payment and
  DROPPED the installment; by occurrence the installment is still owed and
  both reach the plan.
* **DOOR 4 -- D53 and R-R64's cancelled clause, a future row the owner
  REMOVED**: the Van's next projected installment is soft-deleted through the
  transfer door.  The old tier could not see a tombstone and re-synthesized
  the installment; the sum reads the tombstone as the owner's answer -- the
  installment is not paid -- and the contract charges the period anyway
  (ruling **R-R71**), so the payoff moves OUT by one installment.
* **DOOR 5 -- D54, a projected extra in a SEEDED month**: a ``$300.00``
  projected transfer into the Van due five days after its most recently
  settled installment, inside that installment's month.  The old calendar
  charged that month a second time at the extra's date; the seed already
  charged it, so the sum charges it once and the extra pays pure principal.
* **DOORS 6 and 7 -- a DELINQUENT loan (plan step recurrence:R16-c-1)**: the
  settled installments before the latest settled one are REVERTED to
  projected through the status door -- one on the Van (its 2026-06-23 true-up
  leaves it one), TWO on the Mortgage behind its latest settled one.  R16-c-1
  had to walk the skipped months' charges and their catch-ups in contract
  order behind the facts (a first cut applied both charges first, which the
  two-catch-up door read as the Mortgage payoff moving a month and every
  projected balance point with it); both read 0 lines on that fix.  Under
  R16-c-2 the same two doors are where the posted money moves (above).
* **DOOR 8 -- a SETTLED payment due OFF the contractual day** (rulings
  **R-R108**, **R-R109**): the Van's latest settled payment, due on its
  contractual 22nd, is re-dated to the 10th of the next month through the
  transfer door -- inside the same installment's interval, so it pays the
  same installment and the walk charges and splits it exactly as before.
  What the loan page NAMES it by moves: the history card lists it under that
  installment and the schedule dates, numbers and escrow-prices its row
  there, where the base tree used the payment's own due date.  It is the
  positive control for the CARD and SCHED lines below, which no other door
  can move -- production holds no payment due off its loan's day, and doors
  1-7 settle none -- so their zero on every other door is a measurement
  rather than a printer that could not see the change.

**Every door also prints the loan page's two NAMING surfaces** (plan step
R16-c-2's rulings R-R108 and R-R109): the payment-history card's rows and the
schedule's confirmed rows exactly as the route hands them to the template
(:func:`app.routes.loan._helpers.build_schedule_context` -- the row's
number, date, escrow and total).

Nothing it prints carries an id a door's own write assigned, for the reason
``verify_generation_pass.py`` states: PostgreSQL does not roll a sequence
back, so an id-bearing dump reads as a difference between two runs of
identical code.  The posting targets name rows the clone already holds (a
pay period, a ledger account, a reference kind), which no run re-assigns.

**Each door runs alone, and a refused door ends nothing** (finding
plan#1859).  Each opens a savepoint and ``Session.rollback()`` afterwards
discards the whole outer transaction with it, on every path, so the database
is unchanged and no door sees another's writes.  A door the app REFUSES (a
:class:`~app.exceptions.ShekelError`) prints ``# DOOR n REFUSED`` with the
refusal and the run goes on; the exit code is how many doors were refused,
so 0 means every door graded.  Each door picks the row it writes by its own
stated rule, never a pinned date or row id (only the two loans and the Van's
definition are pinned, by ids stable on every production clone), so a
same-day clone always holds a subject the door can take: DOOR 4 took the
first Van row due after ``AS_OF`` whatever its status, which production had
since settled and bank-matched, and the delete door refused it.

**The clone must be STAMPED and it must be production's**: ``occurs_on`` is
what answers an occurrence, and a clone whose loan rows carry NULL there reads
every occurrence as unanswered.  Both loan payments' rows were stamped on
the production clones measured (none NULL: 2026-09-11 and 2026-09-23, and
0 of 59 on the dumps of 2026-09-24 and 2026-09-25).

Usage::

    PYTHONPATH=. DATABASE_URL=postgresql://.../<clone> DATABASE_URL_APP= \\
        .venv/bin/python tests/manual/verify_loan_plan_sum.py

``PYTHONPATH=.`` is load-bearing: a script run by path puts its OWN directory
on ``sys.path``, not the working directory.
"""

import sys
from datetime import date, timedelta
from decimal import Decimal

from app import create_app, ref_cache
from app.models.account import Account
from app.models.amount_ownership import AmountOwnership
from app.models.pay_period import PayPeriod
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum, StatusEnum
from app.exceptions import ShekelError
from app.extensions import db
# Pylint: ``shekel-private-module-import`` -- a white-box harness: it prints the
# schedule rows exactly as the loan route hands them to its template, a read the
# route package does not publish.
from app.routes.loan._helpers import build_schedule_context  # pylint: disable=shekel-private-module-import
from app.services import balance_at, template_amount_service, transfer_service
from app.services.balance_at import BalanceContext
# Pylint: ``shekel-private-module-import`` -- a white-box harness: it prints the
# timeline's payment splits, an intermediate the balance seam does not publish.
from app.services.balance_at._loan_stream import loan_timeline  # pylint: disable=shekel-private-module-import
# Pylint: ``shekel-private-module-import`` -- a white-box harness: it prints the
# plan's payments, an intermediate the balance seam does not publish.
from app.services.balance_at._plan import loan_plan  # pylint: disable=shekel-private-module-import
from app.services.loan_ledger import walk_loan_ledger
from app.services.loan_loaders import load_escrow_lines, load_loan_params
from app.services.loan_posting_service import confirmed_loan_payment_history
# Pylint: ``shekel-private-module-import`` -- a white-box harness: it prints the
# posting targets an anchor correction implies (what the next deploy's backfill
# reconciles to), which the posting package does not publish.
from app.services.loan_posting_service._anchors import (  # pylint: disable=shekel-private-module-import
    anchor_correction_targets,
)
# Pylint: ``shekel-private-module-import`` -- a white-box harness: it prints the
# posting targets a payment split implies (what the next deploy's backfill
# reconciles to), which the posting package does not publish.
from app.services.loan_posting_service._payments import (  # pylint: disable=shekel-private-module-import
    payment_split_targets,
)
from app.services.pay_calendar import calendar_for
from app.services.recurrence import RecurrenceSpec, author_rule
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.dates import add_months

USER_ID = 1
#: The read day every door is measured at.
AS_OF = date(2026, 9, 11)
#: A read on or after every fact the clone records: on plan step R16-c-2's
#: two grade clones (production dumps of 2026-09-24 09:24 and 2026-09-25
#: 06:00, both carrying recurrence:R23) the last loan settle day is 2026-09-23.
TODAY = date(2026, 9, 24)
#: The two live loans and their payment definitions (ids are stable on the
#: production clone; names are printed beside them).
MORTGAGE_ACCOUNT_ID = 3
VAN_ACCOUNT_ID = 8
VAN_TEMPLATE_ID = 9
#: How many months of the projected balance to print per loan.
GRID_MONTHS = 36
#: The years the interest figures are printed for: the Mortgage's origination
#: through the end of its post-contractual extension.
INTEREST_YEARS = range(2019, 2056)


def _outcome_line(label, kind, account, outcome):
    """Print one replay outcome: its dates, its cash and the split of it."""
    print(
        f"{label}\t{kind}\taccount={account.id}\tdue={outcome.due_date}"
        f"\tvisible={outcome.visible_on}\tcharge={outcome.charge_date}"
        f"\tcash={outcome.cash}\tinterest={outcome.interest}"
        f"\tescrow={outcome.escrow}\tprincipal={outcome.principal}"
        f"\texcess={outcome.excess}\tafter={outcome.balance_after}"
    )


def _target_lines(label, kind, account, targets):
    """Print a posting target map with its keys and legs in a stable order."""
    for key in sorted(targets, key=lambda k: (k[2], k[0], k[1])):
        legs = targets[key]
        rendered = ",".join(
            f"{ledger}:{amount}/{kind_id}"
            for ledger, (amount, kind_id) in sorted(legs.items())
        )
        print(
            f"{label}\t{kind}\taccount={account.id}\tsource={key[0]}"
            f"\tperiod={key[1]}\ton={key[2]}\tlegs={rendered}"
        )


def _ledger_lines(label, account):
    """Print what the POSTED ledger books for a loan: its splits and its targets.

    The posted ledger is a projection of the loan's settled walk, reconciled to
    target by every deploy (``backfill_all_loan_postings``), so the targets the
    walk implies ARE what the next deploy writes -- printed here rather than read
    off the clone's stored postings, which the code under test has not synced.
    """
    walk = walk_loan_ledger(account.id, get_baseline_scenario(USER_ID).id)
    for outcome in walk.settled_splits:
        _outcome_line(label, "SETTLED", account, outcome)
    for correction in walk.anchor_corrections:
        print(
            f"{label}\tANCHOR\taccount={account.id}"
            f"\ton={correction.anchor.anchor_date}"
            f"\topening={correction.anchor.is_opening}"
            f"\tbefore={correction.owed_before}"
            f"\tstated={correction.anchor.anchor_balance}"
        )
    _target_lines(
        label, "POST-PAY", account, payment_split_targets(walk.settled_splits),
    )
    _target_lines(
        label, "POST-ANCHOR", account,
        anchor_correction_targets(
            walk.anchor_corrections, USER_ID, calendar_for(USER_ID),
        ),
    )


def _display_lines(label, account, ctx):
    """Print the loan page's history card and the schedule's confirmed rows.

    The two surfaces rulings R-R108 and R-R109 name a payment on by the
    installment it pays.  The card row's field is ``due_date`` on a tree
    before R-R108 and ``installment`` after it, so it is read by whichever the
    tree carries and the one file runs on both.  The schedule's rows are the
    route's own (:func:`app.routes.loan._helpers.build_schedule_context` over
    the seam's confirmed rows), printed while they are confirmed -- they come
    first -- with the number, date, escrow and total the template renders.
    """
    # The card answers only a read on or before today (a later one is a
    # forward projection), and DOOR 2 reads after it on a run before that
    # read day (2026-10-25 on the grade clones): that door states so rather
    # than reading another day.  Its line therefore depends on the RUN's day,
    # so only a base and a branch run on the same day compare.
    if ctx.as_of > date.today():
        card = None
        print(f"{label}\tCARD\taccount={account.id}\tas_of after today")
    else:
        card = confirmed_loan_payment_history(
            account.id, get_baseline_scenario(USER_ID).id, ctx.as_of,
        )
    for row in card or ():
        named = getattr(row, "installment", None) or getattr(row, "due_date")
        print(
            f"{label}\tCARD\taccount={account.id}\t{named}"
            f"\tcash={row.cash}\tprincipal={row.principal}"
            f"\tinterest={row.interest}\tescrow={row.escrow}"
        )
    view = balance_at.confirmed_view(account, ctx)
    if view is None:
        print(f"{label}\tSCHED\taccount={account.id}\tno confirmed view")
        return
    schedule = build_schedule_context(
        view.history_rows, balance_at.loan_installments(account, ctx),
        load_escrow_lines(account.id), load_loan_params(account.id),
    )
    for index, row in enumerate(schedule["amortization_schedule"]):
        if not row.is_confirmed:
            break
        print(
            f"{label}\tSCHED\taccount={account.id}"
            f"\t#{schedule['schedule_row_numbers'][index]}"
            f"\t{row.payment_date}"
            f"\tescrow={schedule['schedule_row_escrow'][index]}"
            f"\ttotal={schedule['schedule_row_totals'][index]}"
            f"\tbalance={row.remaining_balance}"
        )


def _plan_lines(label, account, ctx, *, months=GRID_MONTHS):
    """Print a loan's posted-ledger inputs, its plan, its trajectory and a balance grid.

    Reads only producers both sides of plan step recurrence:R16-c-2 carry, so
    the one file runs on the base worktree and on the branch: the plan's
    PAYMENTS (the plan's charges are deleted by that step and their effect is
    printed where it lands -- on each outcome's split and ``charge`` date).
    The lines print in one fixed order: the ledger, the page's two naming
    surfaces, the payoff, then :func:`_payment_lines`,
    :func:`_trajectory_lines` and :func:`_interest_lines`.
    """
    _ledger_lines(label, account)
    _display_lines(label, account, ctx)
    figures = balance_at.loan_figures(account, ctx)
    print(f"{label}\tPAYOFF\taccount={account.id}\t{figures.payoff_date}")
    _payment_lines(label, account, ctx)
    _trajectory_lines(label, account, ctx, months)
    _interest_lines(label, account, ctx)


def _payment_lines(label, account, ctx):
    """Print the plan's payment counts, then each payment the plan holds."""
    plan = loan_plan(account, ctx)
    estimated = [p for p in plan.payments if p.is_estimated]
    planned = [p for p in plan.payments if not p.is_estimated]
    print(
        f"{label}\tCOUNTS\taccount={account.id}"
        f"\tplanned={len(planned)}\testimated={len(estimated)}"
    )
    for payment in plan.payments:
        print(
            f"{label}\tPAY\taccount={account.id}\tdue={payment.due_date}"
            f"\tvisible={payment.effective_date}\tcash={payment.cash}"
            f"\testimated={payment.is_estimated}"
        )


def _trajectory_lines(label, account, ctx, months):
    """Print the projected splits, the balance grid, the what-if lever and the required extra.

    Args:
        label: The line prefix naming the read.
        account: The loan account.
        ctx: The read's balance context.
        months: How many months of the balance grid to print, after ``ctx.as_of``.
    """
    for outcome in balance_at.loan_installments(account, ctx):
        _outcome_line(label, "PROJECTED", account, outcome)
    grid = [add_months(ctx.as_of, n) for n in range(months + 1)]
    owed = balance_at.positions(account, ctx, grid)
    for on_date in sorted(owed):
        print(f"{label}\tOWED\taccount={account.id}\t{on_date}\t{owed[on_date]}")
    # The what-if lever: the hypothetical extra accrues at the charges behind
    # the projection boundary, a rule R16-c-2 restates.
    for extra in (Decimal("100.00"), Decimal("250.00")):
        what_if = balance_at.loan_what_if_owed_at_dates(
            account, ctx, grid[1::6], extra,
        )
        for on_date in sorted(what_if):
            print(
                f"{label}\tWHAT-IF\taccount={account.id}\textra={extra}"
                f"\t{on_date}\t{what_if[on_date]}"
            )
    target = add_months(ctx.as_of, 60)
    print(
        f"{label}\tREQUIRED\taccount={account.id}\ttarget={target}"
        f"\t{balance_at.loan_required_extra(account, ctx, target)}"
    )


def _interest_lines(label, account, ctx):
    """Print each year's interest and chip figures, then every payment short of its charge."""
    # Schedule A's figure (the year's interest, settled and projected) and
    # the dashboard chip's (the interest the settled payments PAID), every
    # year the loan spans -- the coordinator's grade for finding REC-538
    # (``apply_payment_cash`` reports the CHARGED interest as paid whatever
    # cash moved), which charging every installment could reach if a payment
    # faced more than its cash.
    for year in INTEREST_YEARS:
        print(
            f"{label}\tINTEREST\taccount={account.id}\tyear={year}"
            f"\t{balance_at.loan_interest_in_year(account, ctx, year)}"
        )
        print(
            f"{label}\tCHIP\taccount={account.id}\tyear={year}"
            f"\t{balance_at.loan_interest_paid_in_year(account, ctx, year)}"
        )
    # Every payment whose cash fell below the charge standing over it: a
    # NEGATIVE principal is exactly that (principal = cash - interest -
    # escrow), and REC-538's case is each one of them.
    timeline = loan_timeline(account, ctx)
    short = [o for o in timeline.payment_splits if o.principal < 0]
    print(f"{label}\tSHORTCOUNT\taccount={account.id}\t{len(short)}")
    for outcome in short:
        print(
            f"{label}\tSHORT\taccount={account.id}"
            f"\tprojected={outcome.is_projected}\tdue={outcome.due_date}"
            f"\tcash={outcome.cash}\tinterest={outcome.interest}"
            f"\tescrow={outcome.escrow}\tprincipal={outcome.principal}"
        )


def _loan(account_id):
    """Return the loan account, freshly loaded."""
    return db.session.get(Account, account_id)


def _confirmed_legs(account_id):
    """Return the loan's settled payments visible by ``AS_OF``, as their legs.

    Read off the posted ledger's walk -- each settled split whose
    ``visible_on`` has arrived -- which is the set
    ``loan_ledger.confirmed_shadows_through`` filters by the same
    ``payment_visible_on``.  That function's signature changed at plan step
    R16-c-2's leaf (ruling R-R107 dates a ``$0.00`` close on the loan's
    installment grid, so it takes the origination too); the walk's outcomes
    carry ``source``, ``due_date`` and ``visible_on`` on both trees, so the one
    file keeps running on both.
    """
    walk = walk_loan_ledger(account_id, get_baseline_scenario(USER_ID).id)
    return [
        outcome.source for outcome in walk.settled_splits
        if outcome.visible_on <= AS_OF
    ]


def _last_saved_period():
    """Return the owner's last saved pay period."""
    return (
        db.session.query(PayPeriod)
        .filter_by(user_id=USER_ID)
        .order_by(PayPeriod.start_date.desc())
        .first()
    )


def _period_containing(day):
    """Return the saved pay period covering *day*."""
    ctx = BalanceContext.build(USER_ID, AS_OF)
    period = ctx.calendar().period_containing(day)
    return db.session.get(PayPeriod, period.period_id)


def _adhoc_projected(account_id, period, amount, due):
    """Create a projected ad-hoc transfer of *amount* into *account_id*."""
    checking = (
        db.session.query(Account)
        .filter_by(user_id=USER_ID, is_active=True)
        .order_by(Account.id)
        .first()
    )
    return transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=USER_ID,
            from_account_id=checking.id,
            to_account_id=account_id,
            pay_period_id=period.id,
            scenario_id=get_baseline_scenario(USER_ID).id,
            amount_ownership=AmountOwnership.own(amount),
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            category_id=None,
            name="Planted extra",
            due_date=due,
        ),
    )


def _next_projected_van():
    """Return the Van's next live PROJECTED installment due after ``AS_OF``.

    DOORS 2 and 4 both take this subject.  It is chosen from the data rather
    than pinned to a date or an id, so every same-day clone has one the doors
    can take: the 2026-09-22 installment DOOR 2 was pinned to is Paid on the
    2026-09-24 and 2026-09-25 clones, and DOOR 4's first-row-after-``AS_OF``
    pick (finding plan#1859) took that same row, Paid and bank-matched on the
    2026-10-28 clone, which the delete door refuses.
    """
    return (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == VAN_TEMPLATE_ID,
            Transfer.status_id == ref_cache.status_id(StatusEnum.PROJECTED),
            Transfer.is_deleted.is_(False),
            Transfer.due_date > AS_OF,
        )
        .order_by(Transfer.due_date)
        .first()
    )


def _door_1():
    """DOOR 1: a $500 every-paycheck sweep into the Mortgage (D47)."""
    mortgage_payment = (
        db.session.query(TransferTemplate)
        .filter_by(to_account_id=MORTGAGE_ACCOUNT_ID, is_active=True)
        .order_by(TransferTemplate.id)
        .first()
    )
    sweep = TransferTemplate(
        user_id=USER_ID,
        from_account_id=mortgage_payment.from_account_id,
        to_account_id=MORTGAGE_ACCOUNT_ID,
        name="Sweep into the Mortgage (planted)",
        default_amount=Decimal("500.00"),
        is_active=True,
    )
    db.session.add(sweep)
    db.session.flush()
    # The id tie-break is ascending, so a sweep authored FIRST wins the
    # old tier's pick; forced below the payment's id, the reachable order.
    lowest_free = db.session.execute(
        db.text("SELECT min(id) - 1 FROM budget.transfer_templates")
    ).scalar()
    db.session.execute(
        db.text(
            "UPDATE budget.transfer_templates SET id = :low WHERE id = :new"
        ),
        {"low": lowest_free, "new": sweep.id},
    )
    db.session.expire_all()
    sweep = (
        db.session.query(TransferTemplate)
        .filter_by(name="Sweep into the Mortgage (planted)")
        .one()
    )
    template_amount_service.set_amount(
        sweep, Decimal("500.00"), effective_on=date(2026, 1, 1),
    )
    author_rule(
        RecurrenceSpec(
            user_id=USER_ID,
            interval_n=1,
            unit=RecurrenceUnitEnum.PERIOD,
            placement=PeriodPlacementEnum.CONTAINING_DATE,
            starts_on=AS_OF,
            nominal_day=None,
        ),
        calendar_for(USER_ID),
        sweep,
    )
    db.session.flush()
    print(
        f"# DOOR 1 planted sweep id below the payment's: "
        f"{sweep.id} < {mortgage_payment.id}; starts_on="
        f"{sweep.recurrence_rule.starts_on}"
    )
    ctx1 = BalanceContext.build(USER_ID, AS_OF)
    _plan_lines("D1", _loan(MORTGAGE_ACCOUNT_ID), ctx1, months=12)


def _door_2():
    """DOOR 2: the reset door's hole (D46).

    Read three days after the Van's next PROJECTED installment, whose period
    has then started: the row ``reset_pay_periods`` wipes before it
    repopulates (a reset refuses outright while any row is settled, so a
    settled installment is never the shape).  Hard-deleted (rows CASCADE from
    the period), so no tombstone answers it.
    """
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    as_of_2 = _next_projected_van().due_date + timedelta(days=3)
    started = (
        db.session.query(PayPeriod.id)
        .filter(PayPeriod.user_id == USER_ID, PayPeriod.start_date <= as_of_2)
        .all()
    )
    started_ids = [row[0] for row in started]
    wiped = (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == VAN_TEMPLATE_ID,
            Transfer.pay_period_id.in_(started_ids),
            Transfer.status_id == projected_id,
            Transfer.is_deleted.is_(False),
            Transfer.due_date > AS_OF,
            Transfer.due_date <= as_of_2,
        )
        .all()
    )
    print(
        f"# DOOR 2 as_of={as_of_2}; wiping {len(wiped)} Van rows in "
        "started periods: "
        + ", ".join(sorted(str(row.occurs_on) for row in wiped))
    )
    # Each row goes alone: it took its two shadow rows first until plan
    # step balance:X-bi-6-4d-2 deleted the shadows, and plan step
    # balance:X-bi-6-5 dropped the column they named it by.
    for row in wiped:
        db.session.delete(row)
    db.session.flush()
    ctx2 = BalanceContext.build(USER_ID, as_of_2)
    _plan_lines("D2", _loan(VAN_ACCOUNT_ID), ctx2, months=12)


def _door_3():
    """DOOR 3: an ad-hoc extra past the horizon (occurrence identity)."""
    last = _last_saved_period()
    extra_due = add_months(date(last.start_date.year, last.start_date.month, 10), 1)
    print(f"# DOOR 3 last saved period starts {last.start_date}; extra due {extra_due}")
    _adhoc_projected(VAN_ACCOUNT_ID, last, Decimal("2000.00"), extra_due)
    db.session.flush()
    ctx3 = BalanceContext.build(USER_ID, AS_OF)
    _plan_lines("D3", _loan(VAN_ACCOUNT_ID), ctx3, months=12)


def _door_4():
    """DOOR 4: the Van's next projected installment soft-deleted (D53)."""
    next_row = _next_projected_van()
    print(f"# DOOR 4 soft-deleting the Van row due {next_row.due_date}")
    transfer_service.delete_transfer(next_row.id, USER_ID, soft=True)
    db.session.flush()
    ctx4 = BalanceContext.build(USER_ID, AS_OF)
    _plan_lines("D4", _loan(VAN_ACCOUNT_ID), ctx4, months=12)


def _door_5():
    """DOOR 5: a projected extra in a seeded month (D54)."""
    settled = _confirmed_legs(VAN_ACCOUNT_ID)
    latest = max(settled, key=lambda shadow: shadow.due_date)
    # Five days after the settled installment, inside its own month, so
    # the extra lands in the slot the seed charged.
    extra_due = latest.due_date + (date(2026, 1, 6) - date(2026, 1, 1))
    print(
        f"# DOOR 5 latest settled Van installment due {latest.due_date}; "
        f"extra due {extra_due}"
    )
    _adhoc_projected(
        VAN_ACCOUNT_ID, _period_containing(extra_due), Decimal("300.00"),
        extra_due,
    )
    db.session.flush()
    ctx5 = BalanceContext.build(USER_ID, AS_OF)
    _plan_lines("D5", _loan(VAN_ACCOUNT_ID), ctx5, months=12)


def _door_6():
    """DOOR 6: a DELINQUENT loan, two Van installments reverted.

    Two installments skipped with their rows still PROJECTED behind a later
    SETTLED payment (the shape recurrence:R16-c-1's adversarial review
    measured its first cut moving, +$2.54: the plan's charges for the skipped
    months must walk charge / catch-up / charge / catch-up behind the facts,
    never both charges first).  The two settled Van installments before the
    latest settled one are REVERTED to projected through the status door.
    """
    settled6 = sorted(
        _confirmed_legs(VAN_ACCOUNT_ID), key=lambda shadow: shadow.due_date,
    )
    skipped = settled6[-3:-1]
    print(
        "# DOOR 6 reverting the Van installments due "
        + ", ".join(str(shadow.due_date) for shadow in skipped)
        + f" to projected behind the settled {settled6[-1].due_date}"
    )
    for shadow in skipped:
        transfer_service.update_transfer(
            shadow.transfer.id, USER_ID,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        )
    db.session.flush()
    ctx6 = BalanceContext.build(USER_ID, AS_OF)
    _plan_lines("D6", _loan(VAN_ACCOUNT_ID), ctx6, months=12)


def _door_7():
    """DOOR 7: the same shape on the MORTGAGE, two catch-ups.

    Its latest assertion (2026-05-22) leaves FOUR settled installments behind
    the latest one, so TWO skipped months (07-01, 08-01) stand behind the
    settled 09-01 -- the two-catch-up shape the Van cannot hold (its
    2026-06-23 true-up leaves one installment before its latest settled).  One
    catch-up was identical even under the first cut's clamp; two is where it
    parted.
    """
    settled7 = sorted(
        _confirmed_legs(MORTGAGE_ACCOUNT_ID),
        key=lambda shadow: shadow.due_date,
    )
    skipped7 = settled7[-3:-1]
    print(
        "# DOOR 7 reverting the Mortgage installments due "
        + ", ".join(str(shadow.due_date) for shadow in skipped7)
        + f" to projected behind the settled {settled7[-1].due_date}"
    )
    for shadow in skipped7:
        transfer_service.update_transfer(
            shadow.transfer.id, USER_ID,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        )
    db.session.flush()
    ctx7 = BalanceContext.build(USER_ID, AS_OF)
    _plan_lines("D7", _loan(MORTGAGE_ACCOUNT_ID), ctx7, months=12)


def _door_8():
    """DOOR 8: a SETTLED payment due OFF the contractual day (R-R108, R-R109).

    The Van's latest settled payment is re-dated from its contractual 22nd to
    the 10th of the next month through the transfer door: inside the same
    installment's interval, so the charge it clears, its split and its settled
    day are unchanged, and only what the loan page names it by can move.
    """
    latest8 = max(
        _confirmed_legs(VAN_ACCOUNT_ID), key=lambda shadow: shadow.due_date,
    )
    off_day = add_months(
        date(latest8.due_date.year, latest8.due_date.month, 10), 1,
    )
    print(
        f"# DOOR 8 re-dating the settled Van installment due "
        f"{latest8.due_date} to {off_day}"
    )
    transfer_service.update_transfer(
        latest8.transfer.id, USER_ID, due_date=off_day,
    )
    db.session.flush()
    ctx8 = BalanceContext.build(USER_ID, AS_OF)
    _plan_lines("D8", _loan(VAN_ACCOUNT_ID), ctx8, months=12)


#: The planted doors in run order, each run by :func:`_run_door`.
DOORS = (
    ("DOOR 1", _door_1),
    ("DOOR 2", _door_2),
    ("DOOR 3", _door_3),
    ("DOOR 4", _door_4),
    ("DOOR 5", _door_5),
    ("DOOR 6", _door_6),
    ("DOOR 7", _door_7),
    ("DOOR 8", _door_8),
)


def _run_door(name, door):
    """Run one door in a savepoint of its own, then discard everything it wrote.

    A door the app REFUSES (any :class:`~app.exceptions.ShekelError`, the
    service layer's refusals) is reported by name with its refusal, and the
    run goes on to the next door.  Anything else is a defect in the harness or
    the code under test and ends the run.  ``Session.rollback()`` discards the
    outer transaction with the savepoint on every path, so no door sees
    another's writes and the clone is unchanged.

    Args:
        name: The door's label, printed with a refusal.
        door: The door's function.

    Returns:
        True when the app refused the door, else False.
    """
    db.session.begin_nested()
    try:
        door()
    except ShekelError as exc:
        print(f"# {name} REFUSED {type(exc).__name__}: {exc}")
        return True
    finally:
        db.session.rollback()
    return False


def main():
    """Print the baseline, the TODAY read and the eight doors.

    Returns:
        How many doors the app refused, the run's exit code: 0 when every
        door graded.
    """
    app = create_app()
    with app.app_context():
        print(f"# as_of={AS_OF}")

        # --- BASELINE: unmodified data, expected byte-identical ------------
        ctx = BalanceContext.build(USER_ID, AS_OF)
        for account_id in (MORTGAGE_ACCOUNT_ID, VAN_ACCOUNT_ID):
            _plan_lines("BASE", _loan(account_id), ctx)
        # The same data read on the clone's own day, which sees every
        # recorded fact (the read pass bounds nothing -- ruling R-R91).
        ctx_today = BalanceContext.build(USER_ID, TODAY)
        for account_id in (MORTGAGE_ACCOUNT_ID, VAN_ACCOUNT_ID):
            _plan_lines("TODAY", _loan(account_id), ctx_today)

        refused = [name for name, door in DOORS if _run_door(name, door)]
        print(f"# rolled back; {len(refused)} door(s) refused: {', '.join(refused)}")
    return len(refused)


if __name__ == "__main__":
    sys.exit(main())
