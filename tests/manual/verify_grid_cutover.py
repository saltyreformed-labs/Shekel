"""Dump every period figure the GRID and the COMPANION decide, for a HEAD-vs-post diff.

The regression harness for pay-calendar plan step **C2-f2b**, and it exists
because none of the six in ``docs/plans/verification.md`` can see this change.
``verify_balance_baseline`` walks the ``balance_at`` seam, whose inputs this
leaf does not touch; ``verify_period_window_cutover`` covers the four producers
``C2-f1`` moved and none of them is a grid surface; ``verify_projection_axis``
reads forward projections and every figure here is a saved period;
``verify_render_surfaces`` reads status codes and body sizes, so it can tell
that ``/grid`` still renders and nothing about WHICH paychecks it rendered.
Running any of them over this leaf would report "nothing moved" while saying
nothing about the six readers it changes -- the free-pass shape ``standard 3``
asks about.

It answers *did anything move*, never *is the answer right*.  The proof that
each replacement is correct is the suite's hand-computed cases and the planted
disagreement recorded below; this is the exhaustive regression check beside
them.

**IT ASKS EACH SIDE ITS OWN READER, and that is the whole design.**  A harness
that computed the post-change answer on both sides would diff one derivation
against itself and come back byte-identical whatever the route does -- the
tautology ``docs/plans/lessons.md`` records.  So every probe below forks on
:data:`_IS_HEAD`, and the fork is keyed on the one thing this leaf DELETES:

======================= ================================= =========================
figure                  HEAD                              this leaf
======================= ================================= =========================
current period          ``get_current_period(user_id)``   ``period_containing(as_of)``
a visible window        ``get_periods_in_range``          ``PayCalendar.window``
the whole domain        ``get_all_periods(user_id)``      ``ctx.reported_periods()``
the companion's period  ``(txns, PayPeriod)``             ``CompanionPageRead``
======================= ================================= =========================

**What it covers, and why each is here.**  Every one of these is a value a
route hands a template, so a moved line is a moved column on screen:

* ``current_period`` anchors the leftmost column, the ``current-period`` header
  class and the Carry Fwd button's per-column visibility, so an off-by-one here
  moves the whole grid.
* ``visible_windows`` at three counts and four offsets exercises the arithmetic
  the retired reader did in SQL (``period_index >= start AND < start + count``)
  -- backwards past the schedule's head, at rest, and forwards past its tail.
* ``plan_window`` is the Plan tab's own 13-period slice, anchored on
  ``current_period`` rather than on the URL, so it moves independently.
* ``all_periods`` is the domain the transaction query is scoped by, so a period
  lost here is a ROW that vanishes rather than a column that shifts.
* ``card_window`` is the ONE-period window the mobile This Period card's four
  conditional bars are decided on (ruling R-O), which was ``periods[:1]``.
* ``row_flags`` is the seam's own visibility answer for each of those windows.
  This leaf changed the TYPE that method takes, and a type change that quietly
  dropped a period would hide a row rather than raise.
* ``companion`` is the linked owner's period on the companion page with its
  prev / next links, whose lookup moved from two ORM queries to the owner's
  calendar.

**BYTE-IDENTITY IS THE GATE HERE.**  Every replacement in this leaf is claimed
EQUAL to the query it replaces on any schedule whose stored columns match the
derivation, and ``pay_period_write`` has materialised that derivation on every
write since plan step C3-b.  A moved line is therefore either a stored/derived
disagreement on this database -- itself the finding -- or a defect.  Read
``derived_vs_stored`` before reading the diff: it says whether this database
can express a disagreement at all.

**Usage** (from the repository root, against a production CLONE)::

    DATABASE_URL=postgresql://.../shekel_c2f2b \\
        .venv/bin/python tests/manual/verify_grid_cutover.py after.json

For the HEAD side use ``git worktree add`` -- never ``git checkout``, which
reverts the working tree and discards the change under test
(``docs/plans/lessons.md``).  Copy this file into the HEAD worktree: it does
not exist there, and it is written to run unchanged on both sides.  Nothing
here imports ``routes.grid``, which is a package on one side and a single file
on the other and whose helpers are private either way.

**RUN BOTH SIDES ON THE SAME CIVIL DAY.**  ``current_period`` and the
companion's default period read the clock, so a BEFORE captured yesterday and
an AFTER captured today differ by the calendar rather than by the change.

**AND RUN BOTH SIDES ON THE SAME DATA.**  Nothing here writes, but a sibling
probe can: ``GET /grid`` calls ``pay_period_rolling.top_up_rolling_window`` and
COMMITS, so any harness that drives the real route creates a pay period on the
clone the first time it runs.  Capturing a BEFORE, driving ``/grid``, then
capturing an AFTER diffs 61 paydays against 62 and reports a change this leaf
did not make.  Re-capture both sides after any run that touches the route.

## What it measured at C2-f2b (2026-08-14)

Against ``shekel_c2f2b``, a clone of the production-shape database at this
branch's migration head: one owner with 62 paydays at cadence 14 and 998
transactions, one companion with none.  **0 end mismatches and 0 index
mismatches**, so the stored columns and the derivation agree everywhere; 12
visible windows, the plan window, the card window, the whole 62-period domain
and all three ``row_flags`` per side: **byte-identical**, 0 raises.

Beside it, every AUTHENTICATED SURFACE was rendered on the same clone through
the real routes and diffed byte for byte -- ``/grid`` at five window shapes
plus ``show_all``, all three self-refresh fragments, the mobile summary's
not-found 204, and both companion pages: **12 pages, content-identical**.
And on production itself, read-only: **62 stored periods, 0 end mismatches, 0
index mismatches**, so this cutover moves nothing there.

**And byte-identity on THAT database proves less than it looks**, which is the
shape ``docs/plans/lessons.md`` records: where stored and derived agree, a
defect that turns on their disagreement moves nothing.  So the disagreement was
PLANTED on a copy -- the period covering today given a stored ``end_date`` one
week past its successor's payday (plan finding **P12**'s live shape), and its
stored ``period_index`` swapped with that successor's, which is the ordinal
disorder ``uq_pay_periods_user_index`` cannot catch.  The run then diffs at
**220 lines**, and the six-column window a user actually lands on is where it
matters:

======================= ==================================================
side                    ``/grid?periods=6&offset=0`` -- (period id, index)
======================= ==================================================
HEAD                    (11, 11) (13, 12) (14, 13) (15, 14) (16, 15) (17, 16)
this leaf               (11, 10) (12, 11) (13, 12) (14, 13) (15, 14) (16, 15)
======================= ==================================================

**HEAD SKIPS PAYCHECK 12 ENTIRELY** -- ``2026-08-27``, holding 12 rows worth
``$5,827.75`` on the grid account -- because its stored ordinal fell below the
window's start while its payday did not.  Twelve rows absent from the columns,
from the subtotals and from the balance row, with no gap on screen to show it:
the six headers read consecutively because they are consecutive PERIODS, just
not consecutive PAYDAYS.  This leaf renders the six paychecks that actually
follow each other, and it cannot do otherwise -- its ordinal IS payday order.
That is the whole argument for the arc, priced.
"""

import json
import sys
import traceback
from datetime import date

from app import create_app
from app.extensions import db
from app.models.user import User
from app.services import balance_at, companion_service, pay_period_service
from app.services.account_resolver import resolve_grid_account
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import calendar_for
from tests._test_helpers import all_periods


#: Which side of the cutover this file is running on, keyed on the reader plan
#: step C2-f2b DELETES.  ``get_current_period`` and ``get_all_periods`` survived
#: this leaf (``C2-f3`` took the first at ``C2-f3a``), so neither could mark the
#: boundary;
#: ``get_periods_in_range`` had all three of its ``app/`` call sites in the grid
#: route and goes with it, which makes its absence the exact marker.
_IS_HEAD = hasattr(pay_period_service, "get_periods_in_range")

#: The ``?offset=`` values to dump a visible window for.  ``0`` is the page a
#: user lands on; ``-2`` and ``2`` walk the arrow nav either side of it; ``-99``
#: is the negative-``first_index`` path, where the window comes back SHORT
#: rather than re-based -- the one arm of the retired SQL that had no
#: equivalent test on the calendar until this leaf wrote one.
_OFFSETS = (-99, -2, 0, 2)

#: ``?periods=`` values: the default, the desktop selector's widest option, and
#: the single column the mobile This Period tab navigates with.
_COUNTS = (6, 52, 1)

#: The Plan tab's forward window AT A BIWEEKLY CADENCE.  Copied rather than
#: imported because the constant lived in ``routes.grid`` on one side of the
#: diff this harness graded and ``routes.grid.page`` on the other, and a
#: harness that cannot import on both sides grades nothing.
#:
#: **It is no longer a constant in production.**  Recurrence plan step R-F17
#: replaced ``PLAN_WINDOW_PERIODS`` with ``PLAN_WINDOW_MONTHS``, resolved
#: through ``PayCadence.paychecks_within`` -- so this 13 is the answer for the
#: developer's own 14-day cadence and for no other.  The harness is a
#: before/after instrument for a shipped cutover, run against that owner, so
#: the literal still grades what it was written to grade; a harness pointed at
#: any other cadence must derive it instead.
_PLAN_WINDOW_PERIODS = 13


def _guard(label, produce):
    """Run *produce*, returning its value or a recorded failure.

    A raise on one side and a value on the other is the most important thing
    this harness can report, so nothing is allowed to abort the run.
    """
    try:
        return produce()
    except Exception as exc:  # noqa: BLE001 -- a harness records every failure
        return {
            "RAISED": type(exc).__name__,
            "message": str(exc),
            "where": label,
            "traceback": traceback.format_exc().splitlines()[-3:],
        }


def _period(period):
    """Stringify one period as the four facts every consumer of it reads.

    Takes ``period_id`` where the value has one and ``id`` where it does not,
    because the two sides of this diff hand over different TYPES for the same
    paycheck -- an ORM ``PayPeriod`` on HEAD, a ``DerivedPeriod`` after.  That
    rename is the change, so it is normalised away here: what is being diffed
    is WHICH paycheck, not what the attribute is called.
    """
    if period is None:
        return None
    return {
        "id": getattr(period, "period_id", None) or getattr(period, "id", None),
        "index": period.period_index,
        "start": period.start_date.isoformat(),
        "end": period.end_date.isoformat(),
    }


def _window(periods):
    """Stringify a run of periods, however the side under test spells one."""
    return [_period(period) for period in periods]


def _current_period(user_id, calendar, ctx):
    """Return the paycheck the grid opens on, per this side's reader.

    **The HEAD arm is GONE and its reader with it** (plan step C2-f3a deleted
    ``pay_period_service.get_current_period``), so this is no longer a branch.
    The parameter list keeps ``user_id`` because every ``_probe`` here takes
    the same three, and a signature that varied per probe is what the shared
    driver below exists to avoid.
    """
    del user_id  # noqa: F841 -- see the docstring; the HEAD arm is deleted
    return calendar.period_containing(ctx.as_of)


def _periods_in_range(user_id, calendar, first_index, count):
    """Return *count* periods from ordinal *first_index*, per this side's reader."""
    if _IS_HEAD:
        return pay_period_service.get_periods_in_range(
            user_id, first_index, count,
        )
    return calendar.window(first_index, count)


def _all_periods(user_id, ctx):
    """Return the owner's whole reported domain, per this side's reader."""
    if _IS_HEAD:
        return all_periods(user_id)
    return ctx.reported_periods()


def _flags(view, periods):
    """Dump the seam's four conditional-row answers for *periods*.

    Whatever ``row_flags`` accepts on this side is what the caller passes -- a
    list of ORM rows before this leaf, a ``PeriodWindow`` after -- because both
    come straight out of the readers above.
    """
    flags = view.row_flags(periods)
    return {
        "period_timing": flags.period_timing,
        "book_vs_bank": flags.book_vs_bank,
        "contribution": flags.contribution,
        "accrual": flags.accrual,
    }


# **``_derived_vs_stored`` was deleted at plan step ``pay_calendar:C4-c``.**
# It counted, per owner, where the stored ``end_date`` and ``period_index``
# disagreed with what the paydays derive -- the premise this file's
# byte-identity gate rested on, since a moved line was either such a
# disagreement or a defect and the gate could not tell them apart without
# it.  C4-c dropped both columns.  There is one answer now and the question
# has no second side: a moved line here is a defect, full stop.


def _visible_windows(user_id, calendar, current):
    """Dump every visible window the grid can be asked for.

    The two numbers are the route's own: ``periods`` and ``offset`` off the
    query string, resolved to ``current.period_index + offset``.  Asked through
    the reader each side has rather than through ``_resolve_grid_context``,
    which is private to a module that is a package on one side and a file on
    the other, and which takes a Flask request.
    """
    windows = {}
    for count in _COUNTS:
        for offset in _OFFSETS:
            first = current.period_index + offset
            windows[f"periods={count},offset={offset}"] = _guard(
                f"window({first}, {count})",
                lambda f=first, c=count: _window(
                    _periods_in_range(user_id, calendar, f, c),
                ),
            )
    return windows


def _companion_figures():
    """Dump the companion page's period and its two navigation links.

    Reached through ``companion_service`` rather than the route so this runs
    with no request context.  The return SHAPE changed at C2-f2b (a 2-tuple
    became a bundle), so both spellings are accepted: what is diffed is which
    paycheck and which neighbours, not how they were packed.
    """
    companion = (
        db.session.query(User)
        .filter(User.linked_owner_id.isnot(None))
        .order_by(User.id)
        .first()
    )
    if companion is None:
        return {"companion": None}
    result = companion_service.get_visible_transactions(companion.id)
    if hasattr(result, "period"):
        period, transactions = result.period, result.transactions
    else:
        transactions, period = result
    calendar = calendar_for(companion.linked_owner_id)
    return {
        "companion_user_id": companion.id,
        "owner_id": companion.linked_owner_id,
        "period": _period(period),
        "previous": _period(calendar.period_starting_before(period.start_date)),
        "next": _period(calendar.period_starting_after(period.start_date)),
        "visible_transaction_ids": sorted(txn.id for txn in transactions),
    }


def _grid_figures(user_id):
    """Dump every period figure one owner's ``/grid`` render decides."""
    ctx = BalanceContext.build(user_id)
    calendar = calendar_for(user_id)
    current = _current_period(user_id, calendar, ctx)
    if current is None:
        return {"current_period": None, "note": "no period covers as_of"}

    account = resolve_grid_account(user_id, None, None)
    view = (
        balance_at.grid_balance_view(account, ctx)
        if account is not None else balance_at.empty_grid_view()
    )
    visible = _periods_in_range(user_id, calendar, current.period_index, 6)
    plan = _periods_in_range(
        user_id, calendar, current.period_index, _PLAN_WINDOW_PERIODS,
    )
    card = _periods_in_range(user_id, calendar, current.period_index, 1)

    return {
        "as_of": ctx.as_of.isoformat(),
        "grid_account_id": None if account is None else account.id,
        "current_period": _period(current),
        "all_periods": _guard(
            "all_periods", lambda: _window(_all_periods(user_id, ctx)),
        ),
        "visible_windows": _visible_windows(user_id, calendar, current),
        "plan_window": _window(plan),
        "card_window": _window(card),
        "column_ids": sorted(view.columns),
        "row_flags": {
            "visible": _guard("flags(visible)", lambda: _flags(view, visible)),
            "plan": _guard("flags(plan)", lambda: _flags(view, plan)),
            "card": _guard("flags(card)", lambda: _flags(view, card)),
        },
    }


def main(out_path):
    """Write the dump for every owner on this database to *out_path*."""
    app = create_app()
    with app.app_context():
        owners = (
            db.session.query(User)
            .filter(User.linked_owner_id.is_(None))
            .order_by(User.id)
            .all()
        )
        # The SIDE is printed, never written: it is the one value that must
        # differ between the two runs, so putting it in the file would make
        # every diff non-empty and hide the byte-identity this harness exists
        # to show.
        payload = {
            "run_on": date.today().isoformat(),
            "owners": {
                str(owner.id): _guard(
                    f"owner {owner.id}",
                    lambda oid=owner.id: _grid_figures(oid),
                )
                for owner in owners
            },
            "companion": _guard("companion", _companion_figures),
        }
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    print(f"wrote {out_path} (side={'HEAD' if _IS_HEAD else 'C2-f2b'})")


if __name__ == "__main__":
    main(sys.argv[1])
