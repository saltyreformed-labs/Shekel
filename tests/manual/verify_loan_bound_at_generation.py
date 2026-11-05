"""Dump what a generate pass writes for a definition that pays into a LOAN.

The regression harness for recurrence plan step **R7d-c-2**, which moves
``recurrence_engine.resolve_generation_plan`` onto the composed door
(``recurring_definition.read_definition``), so generation is narrowed by the
loan's DERIVED closing bound rather than by the payoff some chokepoint last
cached into ``budget.recurrence_rules.end_date``.

**Why ``verify_generation_pass.py`` cannot answer this and this file exists
beside it.**  That harness (plan step R7d-c-1) extends the schedule by 20
periods and dumps every row three doors write.  Twenty periods is ten months,
and the developer's nearer loan pays off in 2029 -- past the horizon -- so its
closing bound is never reached and the dump is byte-identical whatever the
bound says.  A green diff there is the "measured nothing" shape: this file
extends PAST the payoff, which is the only place the bound can be observed at
all.

**Five doors, and THREE are PLANTED on purpose.**  Doors 1 and 2 are live
doors on unmodified data and are expected to be byte-identical across this
step -- the stored column and the resolver agree on the developer's own data
(re-measured 2026-09-11 on a dev clone AND read off production: rule 40 stores
``2048-12-01`` and resolves ``ClosesOn(2048-12-01)``; rule 48 stores
``2029-02-22`` and resolves ``ClosesOn(2029-02-22)``).  An all-green diff
would therefore say nothing about whether the new code runs, so DOORS 3, 4 and
5 construct the three states in which the two answers differ, and the diff
MUST move there.

* **DOOR 1 -- EXTEND past the payoff**, through
  ``pay_period_admin.extend_pay_periods`` and
  ``routes/_period_population.populate_new_periods``: both engines, the batch
  window, and the post-write pass.  Then the FIXED POINT: a fresh pass reads
  the payoff again, the maintain pass runs over the rows just written, and a
  whole-schedule generate runs a second time -- all three must report the
  same payoff and no further row.
* **DOOR 2 -- REGENERATE the pay-period tail**, the door plan ledger row
  **D46** is about: it DELETES the not-yet-started tail (rows CASCADE) and
  repopulates, so the pass that resolves the bound runs while the schedule is
  mid-rebuild.  It prints the window on both sides of the hole.
* **DOOR 3 (planted) -- the template-edit MAINTAIN pass after the loan's
  payoff has MOVED EARLIER**: a balance true-up is appended and reconciled
  into the ledger (the shape ``loan_anchor_service.apply_loan_anchor_true_up``
  records) WITHOUT the chokepoint's column sync, so the column still says the
  old payoff while the loan folds to zero sooner.  ``transfer_recurrence.
  regenerate_for_template`` is the one pass that can RETIRE rows: on the
  base tree it honours the stale column and retires nothing; on the branch
  it follows the loan and retires every projected installment past the new
  payoff.  A first draft edited the definition's AMOUNT here, which since
  ``balance:X-au-g`` moves nothing for a loan payment priced from its terms
  -- the payoff read ``2029-02-22`` before and after, so the door measured
  a maintain pass over an unchanged bound.
* **DOOR 4 -- a SECOND definition into the same loan, carrying an owner-
  authored "Ends on" past the loan's payoff.**  Reachable through
  ``POST /transfers``: ``sync_recurring_payment_bounds`` re-bounds only the
  template ``active_recurring_transfer_template`` returns, so a second
  recurring transfer into one loan keeps whatever bound its FORM authored
  (plan ledger row **D50**).  On the base tree that authored date is the only
  bound generation applies and rows are written years past the loan's life; on
  the branch the loan's own window is ANDed with it and they stop at the
  payoff.
* **DOOR 5 -- the loan's OWN payment with its column planted STALE**, plan
  ledger row **D35**'s measured shape: ``end_date`` re-authored to
  ``2029-01-22`` against a derived payoff of ``2029-02-22``.  On the base tree
  generation stops at the column and the ``$531.94`` installment due
  ``2029-02-22`` is never written; on the branch the column is read as the
  cache it is (ruling **R-R56**) and the derived stop is the whole answer.
  **Since plan step R7d-g DOOR 5 reads the other way**: the writers are
  gone, a date planted in that column is the owner's word (ruling
  **R-R82**), and generation honours it -- so a re-run against a tree at or
  past R7d-g reports DOOR 5 stopping at the planted date, which is that
  tree working, not the R7d-c-2 branch regressing.

**Nothing it prints carries a sequence-assigned id**, for the reason
``verify_generation_pass.py`` states: PostgreSQL does not roll a sequence back,
so an id-bearing dump reads as a difference between two runs of identical code.
Rows are named by their occurrence, their due date and their amount.

Every door runs from the SAME base state: each opens a nested transaction and
``Session.rollback()`` afterwards discards the whole outer transaction with it
(SQLAlchemy 2.0 rolls back to the top, not to the savepoint), so the database
it is pointed at is unchanged and no door sees another's writes.

**DOOR 3's stale state is planted by construction, and the class it stands in
for is reachable.**  The true-up door itself (``loan_anchor_service.
apply_loan_anchor_true_up``) always runs the column sync, so "a true-up with
the column left behind" is not a state THAT door leaves.  What is reachable is
the class: a chokepoint that moves the payoff without running the sync -- a
stated-amount edit on the payment's template, and every pay-period door --
leaves the same shape, a column behind the fold.  The true-up is simply the
cheapest way to move the payoff by a known amount.

**Re-measured 2026-09-11 on the tree the step shipped on** -- R16-b-2 merged
(``feat/r16-b-2`` at ``8c654712``), the clone ``shekel_r7dc2`` upgraded to
``6fc77e86d76f``; base = that tree with generation ROUND the door, branch =
through it.  DOORS 1 and 2 byte-identical: 101 and 78 rows, ``2048-12-01`` /
``2029-02-22`` on every reading, the fixed point at zero on both trees.  DOOR
3: base retires nothing (35 rows) against the branch's 18 (17 rows).  DOOR 4 is
where R16-b-2 changed the question: the summed plan puts the Van's payoff at
``2028-11-22`` with the ``$50.00`` sweep beside it, and on the base tree
NEITHER definition's rows follow that -- the Van's 35 run to the column's
``2029-02-22``, the sweep's 65 to ``2031-08-22``, and the maintain pass over
both retires none of them -- where on the branch the sweep writes 32 rows to
``2028-11-22`` and the Van's maintain pass RETIRES the three rows past it
(``2028-12-22``, ``2029-01-22``, ``2029-02-22``: the door's extend ran before
the sweep was planted, so the Van stood at its solo payoff's 35 rows), which
is also what writes ``2028-11-22`` into the Van's column on the branch alone,
because retiring a loan-payment row runs the chokepoint's sync.  The
``D4-MAINTAINED`` COUNT lines carry the retirement (35 -> 32); ``maintain()``
prints only what a pass creates.  The sum R16-b-2 folds reaches the ROWS only
through this step.  DOOR 5: 34 against 35.  Outputs diffed from line 2: 104
lines, every one in doors 3, 4 and 5.

**The clone must be STAMPED.**  ``occurs_on`` is filled by
``scripts/stamp_occurrences.py``, which ``entrypoint.sh`` runs after migrations
-- a clone brought up with ``flask db upgrade`` alone has it NULL on rows that
predate plan step R17, and a maintain pass then retires and recreates every
row it touches, which swamps the signal this file is looking for.

Usage::

    PYTHONPATH=. DATABASE_URL=postgresql://.../<clone> DATABASE_URL_APP= \\
        .venv/bin/python tests/manual/verify_loan_bound_at_generation.py

``PYTHONPATH=.`` is load-bearing: a script run by path puts its OWN directory
on ``sys.path``, not the working directory.

Run it on a worktree at the base commit and on the branch, against the same
clone (it rolls back), and diff the two outputs from line 2.
"""
import inspect
from dataclasses import replace
from datetime import date
from decimal import Decimal

from app import create_app
from app import ref_cache
from app.enums import (
    LoanAnchorSourceEnum,
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.exceptions import RecurrenceConflict
from app.extensions import db
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.routes._period_population import populate_new_periods
from app.services import (
    loan_posting_service,
    pay_period_admin,
    recurrence_engine,
    template_amount_service,
    transfer_recurrence,
)
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    EndsOnDate,
    RecurrenceSpec,
    author_rule,
    reauthor_rule,
    recurrence_spec,
)
from app.services.recurring_definition import resolved_definition
from app.services.scenario_resolver import get_baseline_scenario
from tests._test_helpers import bind_rule_to_loan

USER_ID = 1

#: How far to extend the schedule.  The nearer loan's derived payoff is
#: 2029-02-22 and the saved horizon is 2028-08-23, so a 20-period extend (the
#: sibling harness's) never reaches the bound.  80 biweekly periods is ~37
#: months, which clears it with room for the bound to MOVE and still be seen.
EXTEND_PERIODS = 80

#: The loan-payment definitions to dump.  Named by template id because a
#: template's NAME is editable and its id is not; both are printed.
LOAN_TEMPLATE_IDS = (2, 9)

#: The Van Loan's payment, plan ledger row D35's subject.
VAN_TEMPLATE_ID = 9

#: D35's measured stale cache, one month before the derived payoff.
STALE_END = date(2029, 1, 22)

#: DOOR 3's true-up: the Van Loan's balance asserted at this figure on this
#: day, which folds to zero well before the ``2029-02-22`` the column caches.
TRUEUP_BALANCE = Decimal("6000.00")
TRUEUP_DATE = date(2026, 9, 10)

_TAKES_THE_DOOR = "read_definition" in inspect.getsource(
    recurrence_engine.resolve_generation_plan,
)


def dump(label, template_ids, as_name=None):
    """Print every generated transfer of *template_ids*, ascending, id-free.

    *as_name* replaces the template id in the output for a template this
    harness CREATES: its id comes from a sequence, and PostgreSQL does not
    roll a sequence back, so printing it would read as a difference between
    two runs of identical code.
    """
    rows = (
        db.session.query(Transfer)
        .filter(Transfer.transfer_template_id.in_(template_ids))
        .all()
    )
    rows.sort(key=lambda r: (
        r.transfer_template_id,
        r.occurs_on or date.min,
        r.due_date or date.min,
    ))
    print(f"{label}\tCOUNT\tn={len(rows)}")
    for row in rows:
        print(
            f"{label}\tXFER"
            f"\ttemplate={as_name or row.transfer_template_id}"
            f"\toccurs_on={row.occurs_on}\tdue={row.due_date}"
            f"\tstatus={row.status_id}"
            f"\tfrom={row.from_account_id}\tto={row.to_account_id}"
        )


def windows(label, templates, as_name=None):
    """Print the stored bound and the door's composed closing for each template.

    *as_name* replaces the template id for a harness-created template; see
    :func:`dump`.  A FRESH pass per call, deliberately: this is read between
    writes, and a pass memoises the loan for its life.
    """
    ctx = BalanceContext.build(USER_ID)
    for template in templates:
        rule = template.recurrence_rule
        resolved = resolved_definition(template, ctx)
        print(
            f"{label}\tWINDOW\ttemplate={as_name or template.id}"
            f"\tname={template.name!r}"
            f"\tstored_end_date={None if rule is None else rule.end_date}"
            f"\tstored_max_occurrences="
            f"{None if rule is None else rule.max_occurrences}"
            f"\tclosing={None if resolved is None else resolved.closing}"
        )


def extend_past_the_payoff():
    """Extend the schedule until the nearer loan's payoff is inside it.

    Every door below runs from the SAME base state, and each that needs the
    horizon extends for itself.  Sharing one extend across the doors is what
    a first draft did, and it was wrong in a way that reads as a result: the
    ``Session.rollback()`` that ends a door unwinds the whole transaction,
    shared extend included, so DOOR 4 generated against the ORIGINAL
    2028-08-23 horizon and stopped 6 months short of the bound it exists to
    measure.
    """
    new_periods = pay_period_admin.extend_pay_periods(USER_ID, EXTEND_PERIODS)
    populate_new_periods(USER_ID, new_periods)
    db.session.flush()
    return new_periods


def maintain(template, label):
    """Run the template-edit maintain pass over *template* and report it."""
    ctx = BalanceContext.build(USER_ID)
    try:
        created = transfer_recurrence.regenerate_for_template(
            template, GenerationSchedule.for_pass(ctx), ctx.scenario_id,
        )
        outcome = f"created={len(created)} conflict=none"
    except RecurrenceConflict as exc:
        outcome = (
            f"RecurrenceConflict(overridden={len(exc.overridden)},"
            f"deleted={len(exc.deleted)},retained={len(exc.retained)})"
        )
    db.session.flush()
    print(f"# {label} regenerate_for_template: {outcome}")


app = create_app()
with app.app_context():
    print(
        f"# side: resolve_generation_plan "
        f"{'TAKES' if _TAKES_THE_DOOR else 'GOES ROUND'} the composed door"
    )
    scenario = get_baseline_scenario(USER_ID)
    templates = [db.session.get(TransferTemplate, i) for i in LOAN_TEMPLATE_IDS]
    print(f"# baseline scenario: {scenario.id}")
    print(f"# horizon at start: {calendar_for(USER_ID).horizon()}")
    windows("START", templates)

    # --- DOOR 1: extend PAST the payoff, then the FIXED POINT ------------
    db.session.begin_nested()
    new_periods = extend_past_the_payoff()
    print(f"# DOOR 1 extend: {len(new_periods)} periods, "
          f"horizon {calendar_for(USER_ID).horizon()}")
    windows("D1", templates)
    dump("D1", LOAN_TEMPLATE_IDS)
    # The payoff generation just bounded itself by is folded over the rows it
    # wrote, so ask again on a fresh pass, maintain, and generate once more:
    # a fixed point reports the same closing and no further row.
    windows("D1-AFTER", templates)
    maintain(db.session.get(TransferTemplate, VAN_TEMPLATE_ID), "DOOR 1")
    ctx_again = BalanceContext.build(USER_ID)
    again = transfer_recurrence.generate_for_template(
        db.session.get(TransferTemplate, VAN_TEMPLATE_ID),
        GenerationSchedule.for_pass(ctx_again), ctx_again.scenario_id,
    )
    db.session.flush()
    print(f"# DOOR 1 second whole-schedule generate: created={len(again)}")
    windows("D1-FIXED", templates)
    dump("D1-FIXED", LOAN_TEMPLATE_IDS)
    db.session.rollback()

    # --- DOOR 2: regenerate the tail, and read the window IN THE HOLE ----
    # Plan ledger row D46: the truncate deletes the not-yet-started tail and
    # the repopulate runs after it, so the pass that resolves the bound sees a
    # loan whose forward plan is missing the rows the same transaction is
    # about to write back.  The window is printed on both sides of that hole.
    db.session.begin_nested()
    extend_past_the_payoff()
    calendar = calendar_for(USER_ID)
    # Rebuild from the FIRST not-yet-started period, so the rebuilt tail
    # spans the payoff and the repopulation is what writes the loan's rows.
    # A first draft rebuilt from the extended schedule's LAST period, which
    # put the whole payoff span in a confirmed gap: the door then rebuilt
    # nothing the Van fires in, both sides printed the six rows the extend
    # had left behind, and "byte-identical" measured nothing (the reviewer's
    # M4).
    first_open = next(
        period for period in calendar.saved().periods
        if period.start_date > date.today()
    )
    regen = pay_period_admin.regenerate_pay_periods(
        USER_ID, first_open.start_date, 80, calendar.eras[-1].rhythm,
        confirm_discard=True,
    )
    db.session.flush()
    windows("D2-HOLE", templates)
    populate_new_periods(USER_ID, regen)
    db.session.flush()
    print(f"# DOOR 2 regenerate: {len(regen)} periods, "
          f"horizon {calendar_for(USER_ID).horizon()}")
    windows("D2", templates)
    dump("D2", LOAN_TEMPLATE_IDS)
    db.session.rollback()

    # --- DOOR 3: the maintain pass after the payoff moved EARLIER ---------
    # A true-up appended and reconciled the way the production door records
    # one, minus the chokepoint's column sync: the loan now folds to zero
    # sooner than the column says, and the maintain pass is the one that can
    # RETIRE the projected installments past the new payoff.
    db.session.begin_nested()
    extend_past_the_payoff()
    van = db.session.get(TransferTemplate, VAN_TEMPLATE_ID)
    db.session.add(LoanAnchorEvent(
        account_id=van.to_account_id,
        anchor_date=TRUEUP_DATE,
        anchor_balance=TRUEUP_BALANCE,
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.USER_TRUEUP,
        ),
    ))
    loan_posting_service.sync_loan_postings_all_scenarios(van.to_account_id)
    db.session.flush()
    print(f"# DOOR 3 true-up: balance {TRUEUP_BALANCE} on {TRUEUP_DATE}, "
          f"column left at {van.recurrence_rule.end_date}")
    windows("D3-PRE", templates)
    maintain(van, "DOOR 3 after the true-up")
    windows("D3", templates)
    dump("D3", (VAN_TEMPLATE_ID,))
    db.session.rollback()

    # --- DOOR 4: a SECOND definition into the loan, bound past the payoff -
    # Reachable through POST /transfers (plan ledger row D50): the bound sync
    # re-bounds only the template ``active_recurring_transfer_template``
    # returns, so this one keeps the "Ends on" its form authored.
    db.session.begin_nested()
    extend_past_the_payoff()
    van = db.session.get(TransferTemplate, VAN_TEMPLATE_ID)
    second = TransferTemplate(
        user_id=USER_ID,
        name="Van extra principal (planted)",
        from_account_id=van.from_account_id,
        to_account_id=van.to_account_id,
        is_active=True,
    )
    db.session.add(second)
    db.session.flush()
    template_amount_service.set_amount(
        second, Decimal("50.00"), effective_on=date(2026, 1, 1),
    )
    rule = author_rule(
        RecurrenceSpec(
            user_id=USER_ID,
            interval_n=1,
            unit=RecurrenceUnitEnum.MONTH,
            placement=PeriodPlacementEnum.CONTAINING_DATE,
            starts_on=date(2026, 9, 22),
            nominal_day=None,
            end_bound=EndsOnDate(on=date(2035, 12, 22)),
        ),
        calendar_for(USER_ID),
        second,
    )
    bind_rule_to_loan(rule, second.to_account_id)
    db.session.flush()
    ctx4 = BalanceContext.build(USER_ID)
    print(f"# DOOR 4 horizon={calendar_for(USER_ID).horizon()} "
          f"rule starts_on={rule.starts_on} end_date={rule.end_date}")
    windows("D4-PRE", [second], as_name="PLANTED")
    created = transfer_recurrence.generate_for_template(
        second, GenerationSchedule.for_pass(ctx4), ctx4.scenario_id,
    )
    db.session.flush()
    occurrences = sorted(row.occurs_on for row in created if row.occurs_on)
    print(
        f"# DOOR 4 second definition: created={len(created)} "
        f"first={occurrences[0] if occurrences else None} "
        f"last={occurrences[-1] if occurrences else None}"
    )
    windows("D4", [second], as_name="PLANTED")
    dump("D4", (second.id,), as_name="PLANTED")
    # The second definition's rows now feed the PLANNED tier, which the
    # ESTIMATED tier never priced (it reads the STANDING payment alone, plan
    # ledger row D47), so the payoff read before those rows existed is not the
    # payoff read after them.  The maintain pass over BOTH definitions is
    # where that settles; print what it retires and where it lands.
    windows("D4-AFTER", [van], as_name=None)
    maintain(van, "DOOR 4 Van after the second definition")
    maintain(second, "DOOR 4 second definition")
    windows("D4-MAINTAINED", [van])
    windows("D4-MAINTAINED", [second], as_name="PLANTED")
    dump("D4-MAINTAINED", (VAN_TEMPLATE_ID,))
    dump("D4-MAINTAINED", (second.id,), as_name="PLANTED")
    db.session.rollback()

    # --- DOOR 5: the loan's OWN payment with its column planted STALE ----
    # Plan ledger row D35's measured shape, re-authored through the write door
    # so the stored state is one the chokepoints could have left: a cache one
    # installment EARLIER than the derived payoff.  Planted BEFORE the extend
    # so the population pass is the one that meets it.
    db.session.begin_nested()
    van = db.session.get(TransferTemplate, VAN_TEMPLATE_ID)
    reauthor_rule(
        van.recurrence_rule,
        replace(
            recurrence_spec(van.recurrence_rule),
            end_bound=EndsOnDate(on=STALE_END),
        ),
        calendar_for(USER_ID),
    )
    db.session.flush()
    windows("D5-PRE", [van])
    extend_past_the_payoff()
    print(f"# DOOR 5 planted end_date={van.recurrence_rule.end_date}, "
          f"horizon {calendar_for(USER_ID).horizon()}")
    windows("D5", [van])
    dump("D5", (VAN_TEMPLATE_ID,))
    db.session.rollback()

    print("# rolled back")
