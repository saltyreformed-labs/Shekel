"""Dump every row a GENERATE PASS writes, for a HEAD-vs-post diff.

The regression harness for recurrence plan step **R7d-c-1**, and it exists
because none of the NINE in ``docs/plans/verification.md`` can see this
change.  Every one of them reads a PRODUCER or a RENDER -- the balance seam,
the savings package, the anchor surfaces, 108 route status codes, the two
confirmed statements, the forward projection axis, ``/investment``,
``/retirement`` and the budget dashboard -- and generation is none of
those things: it is a WRITE, and
what it writes is the input the eight later read.  Running any of them over
this leaf would report "nothing moved" while saying nothing about the pass that
decides which rows exist at all, which is the free-pass shape standard 3 asks
about.

It answers *did anything move*, never *is the answer right*.

**Three doors, because a generate pass is reached three different ways** and
each builds its schedule differently:

* the EXTEND path, ``pay_period_admin.extend_pay_periods`` ->
  ``period_population`` -> both engines, which is where the batch window and
  the post-write calendar meet;
* the whole-schedule generate the template-create, unarchive, salary and
  template-edit routes run, re-driven over every active transaction template;
* the carry-forward PREDICTION, ``rows_to_generate_in_period`` (which
  carry-forward's preview calls), the one read-only consumer of the same
  preamble.

**It compiles and runs on BOTH sides**, which standard 3 requires: the
constructor names, the carry-forward keyword and -- in the leaf's SECOND half
(ruling **R-R38**) -- whether ``extend_pay_periods`` populates at all changed
in this step, so every call is dispatched on what is actually present rather
than on which tree this is, and the side it detected is printed as the first
line.  That line is the only one expected to differ -- diff from line 2.

**The second half is why DOOR 1 has two spellings.**  The door used to record
the paydays and generate the recurring rows in one call; it now records and
returns, and the ROUTE opens the read pass and populates.  A harness that
called only the door would print an empty DOOR 1 on the branch and read as
"the extend stopped generating" -- which is true of the door and false of the
operation.  So it runs whichever composition the tree has, and what it diffs
is the rows an EXTEND produces end to end.

**Nothing it prints carries a sequence-assigned id.**  A period is named by its
PAYDAY and a carry-forward plan by its row's NAME and figures, because
PostgreSQL does not roll a sequence back: a second run against the same
database assigns different ids to identical rows, and an id-bearing dump would
read as a difference that is not one.  Measured: the id-bearing first draft
reported 28 moved lines between two runs of the SAME code.

Everything runs inside ONE transaction that is rolled back, so the database it
is pointed at is unchanged.

Usage::

    PYTHONPATH=. DATABASE_URL=postgresql://.../<clone> \\
        .venv/bin/python tests/manual/verify_generation_pass.py

``PYTHONPATH=.`` is load-bearing and is NOT a quirk of this file: a script
run by path puts its OWN directory on ``sys.path``, not the working
directory, so ``from app import create_app`` fails from ``tests/manual/``.
Measured 2026-08-27 on this repository: the eight harnesses already in
``docs/plans/verification.md`` document the same invocation WITHOUT it and
all of them die the same way.  Reported, not fixed here (``CLAUDE.md``
rule 6).

Run it on a worktree at the base commit and on the branch, against two
IDENTICALLY fresh clones, and diff the two outputs from line 2.
"""
import dataclasses
import inspect
from datetime import timedelta

from app import create_app
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transaction_template import TransactionTemplate
from app.services import (
    carry_forward_service,
    pay_period_admin,
    recurrence_engine,
)
from app.services.generation_schedule import GenerationSchedule
from app.services.pay_calendar import calendar_for
from app.services.scenario_resolver import get_baseline_scenario
from app.services.row_valuation import settled_figure

USER_ID = 1

# --- which side are we on ------------------------------------------------
# ``dataclasses.fields`` and ``getattr`` rather than the names themselves:
# this step RENAMES the constructor, so a harness naming both spellings
# directly cannot compile on either side (standard 3).
_TAKES_PASS = "ctx" in {f.name for f in dataclasses.fields(GenerationSchedule)}
_CF_KW = (
    "balance_ctx"
    if "balance_ctx" in inspect.signature(
        carry_forward_service.carry_forward_unpaid,
    ).parameters
    else "calendar"
)

# The SECOND half of R7d-c-1 (ruling R-R38) splits the extend door: it records
# the paydays and the ROUTE populates them.  Import rather than name-check,
# because the module is what does or does not exist.
try:
    from app.routes._period_population import populate_new_periods
except ImportError:  # the door still populates itself (pre-R-R38 tree)
    populate_new_periods = None


def whole_schedule(user_id):
    """A schedule over every period, built the way this side builds one."""
    if _TAKES_PASS:
        from app.services.balance_at import BalanceContext
        ctx = BalanceContext.build(user_id)
        return getattr(GenerationSchedule, "for_pass")(ctx)
    return getattr(GenerationSchedule, "for_calendar")(calendar_for(user_id))


def cf_argument(user_id):
    """The carry-forward door's schedule argument on this side."""
    if _CF_KW == "balance_ctx":
        from app.services.balance_at import BalanceContext
        return {"balance_ctx": BalanceContext.build(user_id)}
    return {"calendar": calendar_for(user_id)}


def dump_rows(label, txn_ids, xfer_ids, payday_of):
    """Print every named row's identity-bearing columns, sorted.

    Periods are named by their PAYDAY rather than by
    ``budget.pay_periods.id``: a sequence is not rolled back, so a second
    run against the same database assigns different ids for identical
    rows and an id-bearing dump would read as a difference that is not
    one.  Every field printed here is a fact about the row.
    """
    rows = (
        db.session.query(Transaction)
        .filter(Transaction.id.in_(txn_ids or [-1]))
        .order_by(Transaction.id).all()
    )
    for r in rows:
        print(
            f"{label}\tTXN"
            f"\ttemplate={r.template_id}\tpayday={payday_of(r.pay_period_id)}"
            f"\tscenario={r.scenario_id}\tacct={r.account_id}"
            f"\test={r.estimated_amount}\tsettled={settled_figure(r)}"
            f"\tsrc={r.amount_source_id}\tdue={r.due_date}"
            f"\tstatus={r.status_id}\tcat={r.category_id}"
            f"\ttype={r.transaction_type_id}\tname={r.name}"
        )
    xfers = (
        db.session.query(Transfer)
        .filter(Transfer.id.in_(xfer_ids or [-1]))
        .order_by(Transfer.id).all()
    )
    for x in xfers:
        print(
            f"{label}\tXFER"
            f"\ttemplate={x.transfer_template_id}\tpayday={payday_of(x.pay_period_id)}"
            f"\tscenario={x.scenario_id}\tfrom={x.from_account_id}"
            f"\tto={x.to_account_id}\tamount={x.amount}"
            f"\tsrc={x.amount_source_id}\tdue={x.due_date}"
            f"\tstatus={x.status_id}\tname={x.name}"
        )


app = create_app()
with app.app_context():
    print(f"# side: schedule takes {'PASS' if _TAKES_PASS else 'CALENDAR'}; "
          f"carry-forward keyword is {_CF_KW!r}; extend door "
          f"{'SPLITS' if populate_new_periods is not None else 'populates'}")
    scenario = get_baseline_scenario(USER_ID)
    print(f"# baseline scenario: {scenario.id}")

    db.session.begin_nested()

    def payday_of(period_id):
        """The period's payday, or its id when the calendar cannot see it."""
        period = calendar_for(USER_ID).period_by_id(period_id)
        return period.start_date if period is not None else f"?{period_id}"

    # --- DOOR 1: the extend path, through period_population ---------------
    before_txn = {i for (i,) in db.session.query(Transaction.id)}
    before_xfer = {i for (i,) in db.session.query(Transfer.id)}
    new_periods = pay_period_admin.extend_pay_periods(USER_ID, 20)
    if populate_new_periods is not None:
        populate_new_periods(USER_ID, new_periods)
    db.session.flush()
    after_txn = {i for (i,) in db.session.query(Transaction.id)}
    after_xfer = {i for (i,) in db.session.query(Transfer.id)}
    print(f"# DOOR 1 extend: {len(new_periods)} periods, "
          f"{len(after_txn - before_txn)} transactions, "
          f"{len(after_xfer - before_xfer)} transfers")
    dump_rows(
        "EXTEND", after_txn - before_txn, after_xfer - before_xfer,
        payday_of,
    )

    # --- DOOR 2: the template-create path, whole-schedule generate --------
    # Re-drive every active transaction template over the whole schedule, the
    # way templates/crud and the salary paths do.  Nothing is created (the
    # skip predicate sees the rows door 1 just wrote), which is itself the
    # answer worth diffing: a schedule resolved differently would create.
    templates = (
        db.session.query(TransactionTemplate)
        .filter_by(user_id=USER_ID, is_active=True)
        .order_by(TransactionTemplate.id).all()
    )
    schedule = whole_schedule(USER_ID)
    for template in templates:
        created = recurrence_engine.generate_for_template(
            template, schedule, scenario.id,
        )
        print(f"DOOR2\ttemplate={template.id}\tcreated={len(created)}\t"
              f"paydays={sorted(payday_of(r.pay_period_id) for r in created)}")

    # --- DOOR 3: the carry-forward prediction -----------------------------
    calendar = calendar_for(USER_ID)
    saved = calendar.saved()
    source, target = saved[-3], saved[-2]
    preview = carry_forward_service.preview_carry_forward(
        source.period_id, target.period_id, scenario.id, **cf_argument(USER_ID),
    )
    print(f"# DOOR 3 carry-forward preview: {len(preview.plans)} plans")
    for plan in sorted(preview.plans, key=lambda p: (p.kind, p.item.name)):
        print(
            f"CF\t{plan.kind}\t{plan.item.name}"
            f"\tbudget={plan.budget}\tblocked={plan.blocked}"
            f"\treason={plan.block_reason_code}"
            f"\tentries={plan.entries_sum}\tleftover={plan.leftover}"
            f"\tbefore={plan.target_estimated_before}"
            f"\tafter={plan.target_estimated_after}"
            f"\twill_generate={plan.target_will_be_generated}"
        )

    db.session.rollback()
    print("# rolled back")
