"""Dump every figure ``/investment`` decides, for a HEAD-vs-post diff.

The regression harness for pay-calendar plan step **C2-f2c**, and it exists
because none of the harnesses in ``docs/plans/verification.md`` covers this
surface end to end.  ``verify_balance_baseline`` walks the
:mod:`app.services.balance_at` seam, whose inputs this leaf does not touch;
``verify_reader_baseline`` calls
:func:`~app.services.investment_dashboard_service.compute_dashboard_data` but
neither of the package's two other public entries; ``verify_projection_axis``
calls :func:`~app.services.investment_dashboard_service.compute_growth_chart_data`
but not the dashboard's cards and never the balance hero cell;
``verify_grid_cutover`` reads saved-period windows on a different route.  Each
of them would report "nothing moved" over one third of what this leaf changes,
which is the free-pass shape ``docs/plans/verification.md`` standard 3 asks
about.

It answers *did anything move*, never *is the answer right*.  The proof that
each replacement is correct is the suite's hand-computed cases and the planted
disagreement recorded in the plan; this is the exhaustive regression check
beside them.

**IT ASKS EACH SIDE ITS OWN READER, and no fork is needed to make that true.**
Every probe below drives one of the package's three PUBLIC entries, so the HEAD
run resolves the owner's periods through ``pay_period_service`` and this run
resolves them through the read pass's own
:class:`~app.services.pay_calendar.PayCalendar`, by construction rather than by
a flag this file has to get right.  A harness that computed the post-change
answer on both sides would diff one derivation against itself and come back
byte-identical whatever the route does -- the tautology ``docs/plans/lessons.md``
records.

**What it covers, and why each is here.**

* ``dashboard`` -- :func:`compute_dashboard_data`, the whole first paint: the
  headline balance (read at the current period), the limit and employer cards,
  the growth-since-anchor chip, the default horizon, the contribution prompt
  with its suggested amount, and the initial chart at that horizon.  Every one
  of those is downstream of a period reader this leaf deletes.
* ``chart`` -- :func:`compute_growth_chart_data` at three slider positions and
  with / without a what-if overlay.  Three positions rather than one because
  the axis LENGTH is what the slider moves, and a single position cannot show a
  per-period defect growing with the horizon; the what-if because it runs a
  SECOND projection over the same axis with a different contribution.
* ``hero`` -- :func:`compute_balance_hero_cell`, the anchor editor's revert
  target.  It resolves the current period on its own, so it is the one entry
  whose period read no other harness in the repository exercises at all.
* ``every ACTIVE account, not only the investment ones`` -- the route is
  ``/accounts/<id>/investment`` and answers for any account the owner holds; an
  account with no :class:`~app.models.investment_params.InvestmentParams` takes
  the empty-chart branch, which is a different path through the same readers.
* ``readers`` -- the stored period COUNT beside ``period_containing(as_of)``
  and ``saved()``.  *A ``derived_vs_stored`` probe sat beside it -- per saved
  period, the stored ``end_date`` / ``period_index`` against the derivation's
  -- and plan step ``pay_calendar:C4-c`` dropped both columns, so there is no
  second side left to diff.*

**Two axes it CANNOT vary**, named so the next reader does not over-read a
clean run.  Both are covered by hand-computed cases in
``tests/test_routes/test_investment/`` instead, which is where a state the
production clone does not hold belongs.

1. **One CLOCK -- the day it runs.**  So it cannot exercise an owner whose
   schedule has lapsed, where no period covers today and this package's
   ``current_period`` is ``None`` at four readers.
   ``TestTheProjectionMeetsItsSeedOnALapsedSchedule`` holds that state.
2. **One relationship between the STORED columns and the DERIVATION**, which
   plan step ``pay_calendar:C4-c`` settled by deleting the stored side.  This
   axis was the sharper of the two while it existed, and an earlier draft of
   this paragraph understated it (adversarial review, 2026-08-15): it named
   "the clock in the LAST saved period", as though only a clock position were
   at stake.  It was not.  ``_cards._compute_default_horizon`` reads the LAST
   period's end wherever the clock sits, and three more readers take a derived
   ``end_date`` / ``period_index``, so the exposure was every render on any
   database where the two disagreed.  This harness could not see it on ANY
   database a write door built, which is why ``derived_vs_stored`` reported
   zero mismatches and why no fixture could express one either.  The suite
   planted the disagreement by hand while it was expressible and pinned which
   column won; that draft claimed the axis was covered when nothing covered
   it.

**BYTE-IDENTITY IS THE GATE HERE.**  Every replacement in this leaf is claimed
EQUAL to the query it replaces on any schedule whose stored columns match the
derivation, and ``pay_period_write`` has materialised that derivation on every
write since plan step C3-b.  A moved line is therefore either a stored/derived
disagreement on this database -- itself the finding -- or a defect.

**THE RESULT, and the hole the first clean run hid.**  On a production clone
(``shekel_c2f2c``: 62 saved paydays, ``derived_vs_stored`` mismatches ZERO, so
this database cannot express a stored/derived disagreement at all) the two
sides came back **byte-identical over 39,939 lines** -- 8 accounts, three
public entries each, six chart configurations each.

That first clean run was WORTH LESS THAN IT LOOKED, and saying so is the point.
The owner on the clone had no ``planned_retirement_date``, so
``retirement_year`` and ``retirement_marker_index`` were ``None`` on every one
of those lines -- and the marker is the figure this leaf's ledger row (**P48**)
is about.  The harness was blind on exactly its subject while reporting
success, which is ``docs/plans/lessons.md``'s "ask which axis no case varies".
Setting one (``2035-06-30``, chosen to sit inside the 10- and 40-year axes and
outside the 1-year one, and to make ``default_horizon`` differ from what the
saved-schedule arm answers) made three arms live at once, and the re-run of
BOTH sides was byte-identical again.

**SHOWN FIRING.**  With the date set, the marker was recomputed the way this
leaf's own docstrings warn against -- ``containing(d).period_index`` instead of
``containing_index(d)`` -- and the harness reported **80 moved lines, 40 of
them ``retirement_marker_index`` at 241 against 252**.  The projection axis
opens at calendar ordinal 11, so a view-relative offset and a calendar ordinal
differ by exactly that, and the retirement line lands eleven points late with
no error anywhere.  Restoring the file from a scratchpad copy returned the run
to byte-identical.

**Usage** (from the repository root, against a production CLONE)::

    DATABASE_URL=postgresql://.../shekel_c2f2c \\
        .venv/bin/python tests/manual/verify_investment_cutover.py before.json
    # ... make the change ...
    DATABASE_URL=postgresql://.../shekel_c2f2c \\
        .venv/bin/python tests/manual/verify_investment_cutover.py after.json
    diff before.json after.json

For the HEAD side use ``git worktree add`` -- never ``git checkout``, which
reverts the working tree and discards the change under test
(``docs/plans/lessons.md``).  Copy this file into the HEAD worktree: it does
not exist there, and it is written to run unchanged on both sides.

**RUN BOTH SIDES ON THE SAME CIVIL DAY.**  Every producer here is
clock-dependent: the headline is read at the period covering today, the
projection window opens the day after that period ends, and the default
horizon counts years from today.  A BEFORE captured yesterday and an AFTER
captured today differ by the calendar rather than by the change, and this
harness cannot tell the two apart.  Plan step X-au-c2a measured that mistake at
2,277 spurious moved lines.

**It writes nothing.**  Every entry driven here is a read; unlike ``/grid``,
whose route tops up the rolling window and COMMITS, nothing in this package
reaches a writer.  So the two sides may be captured against the same clone
without one run moving the ground under the other.
"""

import json
import sys
import traceback
from datetime import date
from decimal import Decimal

from app import create_app
from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.user import User
from app.services.balance_at import BalanceContext
from app.services.investment_dashboard_service import (
    compute_balance_hero_cell,
    compute_dashboard_data,
    compute_growth_chart_data,
)
from app.services.pay_calendar import calendar_for


#: The horizon slider positions the growth chart is asked for.  Three rather
#: than one because the axis LENGTH is what the slider moves, and a single
#: position cannot show a per-period defect growing with the horizon.
_HORIZON_YEARS = [1, 10, 40]

#: The what-if overlay inputs.  ``None`` is the single-line chart; the string
#: is the second projection the overlay runs over the SAME axis, so an axis
#: defect shows in both series and a contribution defect in only one.
_WHAT_IFS = [None, "1000"]


def _money(value):
    """Stringify a Decimal so the diff is textual and exact.

    ``Decimal("1.10")`` and ``Decimal("1.1")`` are numerically equal and must
    not be reported as a move, while ``1.10`` -> ``1.11`` must be.  ``None``
    passes through so an absent figure stays distinguishable from a zero one.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return f"{value:.6f}"
    return str(value)


def _plain(value, depth=0):
    """Serialise ANY producer result to comparable plain data.

    Generic rather than field-by-field, for the reason
    :mod:`tests.manual.verify_reader_baseline` records: a draft that named the
    fields it expected reported "identical" over three surfaces it had never
    captured.  Walking the structure removes the guess.

    ORM instances collapse to ``ClassName#id``: their identity is stable across
    the two runs and their attribute graph is unbounded.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return _money(value)
    if isinstance(value, float):
        return f"FLOAT:{value!r}"
    if isinstance(value, date):
        return value.isoformat()
    if depth > 8:
        return "DEPTH"
    if isinstance(value, dict):
        return {str(k): _plain(v, depth + 1) for k, v in sorted(
            value.items(), key=lambda kv: str(kv[0]),
        )}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v, depth + 1) for v in value]
    if hasattr(value, "_sa_instance_state"):
        return f"{type(value).__name__}#{getattr(value, 'id', None)}"
    if hasattr(value, "__dict__"):
        return {
            "__type__": type(value).__name__,
            **{
                k: _plain(v, depth + 1)
                for k, v in sorted(vars(value).items())
                if not k.startswith("_")
            },
        }
    return str(value)


def _guard(label, thunk):
    """Run *thunk*, recording a RAISE rather than aborting the dump.

    A producer that raises on one account must not hide the rest, and the raise
    itself is a fact worth diffing: "raised here, answered there" is a move,
    not a crashed run.
    """
    try:
        return thunk()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {
            "RAISED": type(exc).__name__,
            "message": str(exc)[:400],
            "where": label,
            "tb": traceback.format_exc(limit=3)[-400:],
        }


def _readers(user_id):
    """What the calendar answers, beside a direct COUNT of the table.

    **The STORED side of this probe is gone, and so is the probe beside it**
    (plan step ``pay_calendar:C4-c``).  ``budget.pay_periods`` carried
    ``end_date`` and ``period_index``, so this recorded which paycheck the
    STORED spans said covered today and how many rows disagreed with the
    derivation; neither column exists, and a row is now the payday alone.  The
    row COUNT stays because it is the one stored fact left, and a calendar
    holding a different number of saved periods from the table would be a real
    finding.
    """
    ctx = BalanceContext.build(user_id)
    calendar = calendar_for(user_id)
    derived_current = calendar.period_containing(ctx.as_of)
    return {
        "as_of": ctx.as_of.isoformat(),
        "cadence": calendar.eras[-1].rhythm.cadence.phrase,
        "opening_bound": _plain(calendar.opening_bound()),
        "horizon": _plain(calendar.horizon()),
        "stored_all_periods": db.session.query(PayPeriod).filter_by(
            user_id=user_id,
        ).count(),
        "derived_saved_periods": len(calendar.saved()),
        "derived_current_period_id": (
            None if derived_current is None else derived_current.period_id
        ),
        "derived_current": _plain(derived_current),
    }


def _account_dump(user_id, account):
    """Every figure the three public entries publish for one account."""
    charts = {}
    for horizon in _HORIZON_YEARS:
        for what_if in _WHAT_IFS:
            key = f"h{horizon}:w{what_if or 'none'}"
            charts[key] = _guard(
                f"chart:{account.id}:{key}",
                lambda a=account, h=horizon, w=what_if: _plain(
                    compute_growth_chart_data(user_id, a, h, w),
                ),
            )
    return {
        "name": account.name,
        "dashboard": _guard(
            f"dashboard:{account.id}",
            lambda a=account: _plain(compute_dashboard_data(user_id, a)),
        ),
        "hero": _guard(
            f"hero:{account.id}",
            lambda a=account: _plain(
                compute_balance_hero_cell(user_id, a.id),
            ),
        ),
        "charts": charts,
    }


def _dump_user(user_id):
    """Every probe for one owner."""
    accounts = (
        db.session.query(Account)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(Account.id)
        .all()
    )
    return {
        "readers": _guard("readers", lambda: _readers(user_id)),
        "accounts": {
            str(account.id): _account_dump(user_id, account)
            for account in accounts
        },
    }


def main(out_path):
    """Dump every owner's ``/investment`` figures to *out_path* as JSON."""
    app = create_app()
    with app.app_context():
        payload = {
            "captured_on": date.today().isoformat(),
            "users": {
                str(user.id): _dump_user(user.id)
                for user in db.session.query(User).order_by(User.id).all()
            },
        }
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(
            "usage: verify_investment_cutover.py <out.json>",
        )
    main(sys.argv[1])
