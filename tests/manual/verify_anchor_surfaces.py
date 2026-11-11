"""Dump every ANCHOR-derived figure the balance seam's harness cannot see.

``verify_balance_baseline.py`` walks :mod:`app.services.balance_at` exhaustively,
and that is its limit: a figure read from a balance ASSERTION without going
through the seam is structurally invisible to it.  Finding **N-181** is what that
costs -- a backfill moved a payment-timeliness metric onto a day nothing had
observed, the seam harness reported byte-identical, and the step called itself
"no figure moves".  Every anchor-touching step since has had to hand-write a
probe for these surfaces and has left it in a session scratchpad, which means the
next step re-writes it or skips it.  This is that probe, in the repository.

**The seven surfaces, and why each is outside the seam:**

* **The grid HEADER's starting figure and its "as of" caption** -- read from the
  account's latest assertion beside the projection, not from it
  (``routes.grid._grid_view_and_anchor``).
* **The reconcile panel** -- outstanding purchases partitioned against the
  assertion's day (``reconcile_service.outstanding_set``).  Not a balance at
  all, which is exactly N-181's class.
* **The dashboard balance section and the PULSE hero** -- both resolve the
  account's assertion for their own caption and staleness test
  (``dashboard_service.compute_balance_section``,
  ``dashboard_service.compute_pulse_section``).
* **The savings dashboard, including the ARCHIVED drawer** -- an archived
  account receives no seam call at all, so its "Last Balance" line is a direct
  assertion read (``savings_dashboard_service.compute_dashboard_data``).
* **Property market value / home equity** -- the property's asserted value is
  its market value (``home_equity_service.resolve_home_equity``).
* **The retirement table's seeds** -- the balances the projection starts from
  (``retirement_plan.picture_at``).

**Usage** (from the repository root), the same before/after shape as the seam
harness::

    DATABASE_URL=postgresql://.../shekel_clone_before \\
        .venv/bin/python tests/manual/verify_anchor_surfaces.py before.json
    # ... make the change ...
    DATABASE_URL=postgresql://.../shekel_clone_after \\
        .venv/bin/python tests/manual/verify_anchor_surfaces.py after.json
    diff before.json after.json

It answers "did anything move", never "is the answer right" -- read the seam
harness's own docstring for that distinction, and always run a POSITIVE CONTROL
(move one cent on one assertion and re-diff) before trusting an empty diff.

A surface whose producer raises is recorded as its exception text rather than
crashing the run: a probe that dies on account 3 has silently stopped covering
accounts 4 through 9, and an empty diff would then mean nothing.
"""

from __future__ import annotations

import json
import pathlib
import sys
from decimal import Decimal

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so ``app`` is not importable when this is run as
# ``.venv/bin/python tests/manual/verify_anchor_surfaces.py`` -- the same
# bootstrap, for the same reason, as ``verify_balance_baseline.py`` beside it.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``app`` before it runs.
# pylint: disable=wrong-import-position
from app import create_app
from app.extensions import db
from app.models.account import Account
from app.models.user import User
from app.services import (
    balance_at,
    cash_ledger,
    dashboard_service,
    home_equity_service,
    reconcile_service,
    retirement_plan,
    savings_dashboard_service,
)
from app.services.balance_at import BalanceContext


def _plain(value):
    """Return *value* rendered as a stable, JSON-safe, diff-friendly scalar.

    ``Decimal`` becomes its exact string (never a float -- a float round-trip
    is precisely the money defect this project's standards forbid), dates
    become ISO strings, and anything else recurses through containers.  Objects
    the probe does not model are reduced to ``repr``, which still DIFFS: a
    changed field inside one shows up as a changed line.

    Args:
        value: Any producer output.

    Returns:
        A JSON-serialisable structure.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "__dict__"):
        return {
            str(k): _plain(v)
            for k, v in sorted(vars(value).items())
            if not k.startswith("_")
        }
    return repr(value)


def _guarded(label, producer):
    """Run *producer*, returning its output or its failure -- never raising.

    Args:
        label: The surface's name, for the failure record.
        producer: A zero-argument callable.

    Returns:
        The ``_plain``-rendered output, or ``{"__error__": "..."}``.
    """
    try:
        return _plain(producer())
    except Exception as exc:  # pylint: disable=broad-except
        # Deliberate: this is a measurement harness, not application code, and
        # its job is to keep covering the remaining surfaces after one fails.
        # A narrow except here would let an unmodelled producer error abort the
        # run and turn an empty diff into a false all-clear.
        return {"__error__": f"{label}: {type(exc).__name__}: {exc}"}


def _account_surfaces(account, balance_ctx):
    """Return every per-account anchor-derived figure outside the seam.

    Args:
        account: The :class:`~app.models.account.Account` to probe.
        balance_ctx: The read pass's ``BalanceContext`` -- which since plan
            step C2-c also carries the pay periods every per-period seam entry
            reports over, so this no longer threads a period list.

    Returns:
        A dict of surface name to rendered figure.
    """
    anchor = cash_ledger.resolve_anchor(account)
    return {
        # The header's two halves come off ONE object since ruling R-EP; both
        # are captured so a split would show as two changed lines, not one.
        "grid_header_starting_balance": _plain(anchor.balance),
        "grid_header_as_of": _plain(anchor.observed_on),
        "grid_view": _guarded(
            "grid_view",
            lambda: balance_at.grid_balance_view(account, balance_ctx),
        ),
        "reconcile_outstanding": _guarded(
            "reconcile_outstanding",
            # Asked against the assertion the PANEL is reconciled against
            # (``governing_anchor``, the same resolver the route uses), whose
            # own day is the partition the panel draws: rows and purchases on
            # or before it whose posting day has never been recorded.
            #
            # **This surface recorded ``__error__`` on both sides of every A/B
            # run between plan steps X-f3a-1 and pay-calendar C4-a-2**, because
            # the call still passed a bare ``observed_on`` where the producer
            # had begun taking the whole ``AnchorPoint``.  Two equal errors
            # DIFF CLEAN, so the panel read as "unchanged" while being measured
            # not at all -- the harness failure this file's own ``_guarded``
            # docstring exists to bound, landing on the one surface here that
            # offers money to be moved.  Read the printed error count, never
            # the empty diff alone.
            lambda: reconcile_service.outstanding_set(
                reconcile_service.Statement(
                    balance_ctx.calendar(), account.id,
                    cash_ledger.governing_anchor(account.id),
                ),
                balance_ctx,
            ),
        ),
        "home_equity": _guarded(
            "home_equity",
            lambda: home_equity_service.resolve_home_equity(
                account, balance_ctx,
            ),
        ),
    }


def _user_surfaces(user_id):
    """Return every whole-user anchor-derived surface outside the seam.

    Args:
        user_id: The owner to probe.

    Returns:
        A dict of surface name to rendered figure.
    """
    return {
        # Both take the RESOLVED SECTION since pay-calendar plan step C2-f2e,
        # which is what the route hands them; resolving it here is the route's
        # own two lines rather than a stand-in for them.
        "dashboard_balance_section": _guarded(
            "dashboard_balance_section",
            lambda: dashboard_service.compute_balance_section(
                dashboard_service.resolve_section(
                    BalanceContext.build(user_id),
                ),
            ),
        ),
        "dashboard_pulse": _guarded(
            "dashboard_pulse",
            lambda: dashboard_service.compute_pulse_section(
                dashboard_service.resolve_section(
                    BalanceContext.build(user_id),
                ),
            ),
        ),
        "savings_dashboard": _guarded(
            "savings_dashboard",
            lambda: savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(user_id),
            ),
        ),
        "retirement_gap": _guarded(
            "retirement_gap",
            lambda: _stored_picture(user_id),
        ),
    }


def _stored_picture(user_id):
    """Return the retirement picture at the owner's STORED plan.

    **The two lines this replaces named `retirement_plan.STORED_PLAN`, which
    does not exist and never did on this branch or its merge base** (found by
    C2-f2e's adversarial code review, 2026-08-18): every probe that reached
    them recorded an ``AttributeError`` in the dump instead of a figure, so
    this harness's retirement surface had been silently dead. The stored plan
    is a PROPERTY of the render's loaded inputs
    (:attr:`~app.services.retirement_plan.RetirementInputs.stored_plan`), which
    is also why the two must come from ONE ``load_retirement_inputs`` call --
    a picture derived at another load's plan point is a different render.

    Args:
        user_id: The owner to picture.

    Returns:
        The :class:`~app.services.retirement_plan.RetirementPicture`.
    """
    inputs = retirement_plan.load_retirement_inputs(
        BalanceContext.build(user_id),
    )
    return retirement_plan.picture_at(inputs, inputs.stored_plan)


def main(out_path):
    """Write the whole-database anchor-surface snapshot to *out_path*.

    Args:
        out_path: Destination JSON path.
    """
    app = create_app()
    with app.app_context():
        snapshot = {}
        users = db.session.query(User).order_by(User.id).all()
        for user in users:
            balance_ctx = balance_at.BalanceContext.build(user.id)
            accounts = (
                db.session.query(Account)
                .filter_by(user_id=user.id)
                .order_by(Account.id)
                .all()
            )
            snapshot[f"user:{user.id}"] = {
                "surfaces": _user_surfaces(user.id),
                "accounts": {
                    # Keyed by id AND name: an id-only key hides a rename, and
                    # a name-only key collides across archived duplicates.
                    f"{account.id}:{account.name}": _account_surfaces(
                        account, balance_ctx,
                    )
                    for account in accounts
                },
            }
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, indent=2, sort_keys=True)
        account_count = sum(
            len(u["accounts"]) for u in snapshot.values()
        )
        errors = json.dumps(snapshot).count('"__error__"')
        print(
            f"wrote {out_path}: {len(users)} users, {account_count} accounts, "
            f"{errors} producer errors recorded"
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit("usage: verify_anchor_surfaces.py <out.json>")
    main(sys.argv[1])
