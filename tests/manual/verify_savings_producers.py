"""Dump every figure the /savings producer package answers, for a HEAD-vs-post diff.

Added at plan step X-t (2026-07-28) and kept, because the three steps after it
(X-u, X-v, X-w) all change these same producers.  ``tests/manual/verify_balance_baseline.py`` reads
the ``balance_at`` seam DIRECTLY, so for a step whose whole surface is ABOVE the
seam -- the savings-dashboard package, its serializers, and the dashboard debt
track -- it is byte-identical whatever the step did.  A free pass that reads as
proof.  This dumps the layer that step actually changes:

* ``compute_dashboard_data`` in full: every per-account projection field (the
  nested loan figures included), the grid grouping + subtotals, goals, the
  emergency metrics, the archived list, the debt summary with its payoff
  outlook and DTI block, the net-worth today figures, the 2-year series with
  its composition bands, the whole Horizon range with its milestones, and the
  sparklines;
* the NARROW producers (debt summary, goal progress, and the per-account
  cockpit balance cell for every account) plus the principal-paid fraction,
  which was a narrow producer of its own until plan step X-u;
* the ROUTE's serialized ``data-chart`` payload (the float boundary);
* ``dashboard_service.compute_tracks_section`` -- the debt track that
  carries the summary.

**NORMALIZED across the intended shape changes**, so a deliberate diff cannot
hide an accidental one.  **A NORMALIZATION SHIM IS DELETED BY THE STEP AFTER THE
ONE THAT NEEDED IT**, because each adds a branch that can never be taken on the
current tree, and a file whose readers all tolerate several dead shapes is one
that can no longer answer this harness's own question (Section 7.2 -- can it SEE
the code under test?).

**Plan step X-w5 paid the two that were owed.**  The X-t1 shim (the per-account
projection's pre-value-object ``loan_figures`` / ``loan_params`` keys) was due to
go when X-v shipped and did not; the X-u shim (the deleted
``compute_debt_principal_progress`` producer and the ``DebtTrack`` wrapper) was
due here.  Both are gone, and each was verified dead by RUNNING this file
against the tree it was written for.

**What remains is X-w's OWN tolerance, and X-x deletes it**: :func:`_get` reads
a field off a dict OR a value object, and :func:`_today_figures` and the
archived row read both spellings of a renamed figure.  Plan steps X-w and X-aa
turned SEVEN containers on this path into frozen value objects (rulings R-CG /
R-CH / R-CI / R-CO),
so those branches are what let this ONE file produce the same blob on the
pre-X-w tree and the post-X-w one -- which is the only thing that makes the
diff mean anything.

**Usage** (from the repository root)::

    DATABASE_URL=postgresql://.../shekel \\
        .venv/bin/python tests/manual/verify_savings_producers.py before.json
    # ... make the change ...
    DATABASE_URL=postgresql://.../shekel \\
        .venv/bin/python tests/manual/verify_savings_producers.py after.json
    diff before.json after.json

Run it before AND after, and use ``git worktree`` for the HEAD side -- never
``git checkout``, which reverts the working tree and discards the change under
test.

This used to say to run it against BOTH ``shekel`` and ``shekel_f3_final``,
which carried different account sets.  **They converged** (measured 2026-08-07:
identical nine accounts, one transaction apart) -- ``shekel`` was re-cloned from
production and ``shekel_f3_final`` refreshed from it.  See
``verify_balance_baseline.py`` for why a genuinely DIFFERENT second dataset is
still worth building when a step is data-shape-sensitive.

It answers "did anything move", never "is the answer right": two figures
identical here can both be wrong (finding N-69).  A step's PROOF is its firing
controls and its hand-computed oracles; this is the REGRESSION check beside
them, and it was shown FIRING on a planted one-cent defect in
``_project_one_account`` (56 diff lines, reaching the projections, the chart
payload, the net worth and the debt summary) before plan step X-t trusted it.

**One blind spot, measured 2026-08-11 and stated here rather than rediscovered.**
``_compute_avg_monthly_expenses`` publishes ``max(historical, floor)``, so a
change to the COMMITTED-TEMPLATE floor is invisible to this harness whenever
the historical average is the larger of the two -- which it is on the current
data (``$6,988.08`` against ``$5,945.13``).  A planted defect in the
monthly-equivalent conversion for MONTH-unit templates moved **zero** lines
here while moving 220 in the Recurring-surface harness beside it.  That is the
``max()`` behaving correctly and this file reporting honestly; what it means is
that "no diff here" is NOT evidence for a change to
``obligations_aggregator``'s conversion.  Diff the Recurring surface for that.

Like its ``verify_*`` siblings here it is deliberately outside pytest's
collection (``pytest.ini`` sets ``python_files = test_*.py``): it needs a
populated database chosen by the operator, not the seeded test template.
"""

import json
import pathlib
import sys
from decimal import Decimal

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so ``app`` is not importable when this is run as
# ``.venv/bin/python tests/manual/verify_savings_producers.py`` -- the same
# bootstrap its sibling ``verify_balance_baseline.py`` carries, and for the same
# reason: this loads the app in-process because the figures it dumps are
# PRODUCER calls, not a rendered page.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``app`` before it runs.
# pylint: disable=wrong-import-position
from app import create_app
from app.extensions import db
from app.models.user import User
from app.routes.savings import _serialize_net_worth_chart, _serialize_sparklines
from app.services import balance_at, dashboard_service
from app.services import savings_dashboard_service
from app.services.balance_at import BalanceContext
from app.services.scenario_resolver import get_baseline_scenario


def _get(obj, name):
    """Read *name* off a dict OR a dataclass, returning None when absent."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _money(value):
    """JSON-stable string for a Decimal (or None)."""
    if value is None:
        return None
    return f"{Decimal(value):.6f}"


def _date(value):
    """ISO string for a date (or None)."""
    return None if value is None else value.isoformat()


def _terms(terms):
    """Every field of the seam's LoanTerms.

    ``is_arm`` and ``recorded_start`` were missing until plan step
    recurrence:R16-c-2, so this dump could not see a change to either; on a
    tree whose LoanTerms has no ``recorded_start`` (before that step)
    :func:`_get` reads it as ``None``, an intended shape change on every loan.
    ``convention``, the loan's interest accrual convention, is a field since
    plan step recurrence:R16-d and dumped as its member's value; on a tree
    before that step it reads ``None``, the same intended shape change.
    """
    if terms is None:
        return None
    convention = _get(terms, "convention")
    return {
        "monthly_payment": _money(_get(terms, "monthly_payment")),
        "current_rate": _money(_get(terms, "current_rate")),
        "is_originated": _get(terms, "is_originated"),
        "is_arm": _get(terms, "is_arm"),
        "recorded_start": _date(_get(terms, "recorded_start")),
        "convention": None if convention is None else convention.value,
    }


def _figures(figures):
    """Every field of the seam's LoanFigures.

    ``closing_date`` (plan step recurrence:R7d-h) was missing until plan step
    recurrence:R16-c-2; on a tree older than R7d-h :func:`_get` reads it as
    ``None``.
    """
    if figures is None:
        return None
    return {
        "payoff_date": _date(_get(figures, "payoff_date")),
        "closing_date": _date(_get(figures, "closing_date")),
        "is_retired": _get(figures, "is_retired"),
        "is_paid_off": _get(figures, "is_paid_off"),
        "terms": _terms(_get(figures, "terms")),
    }


def _loan_halves(ad):
    """The loan figures + params of one projection.

    The X-t1 shim -- a fallback to the pre-value-object ``loan_figures`` /
    ``loan_params`` dict keys -- was deleted at plan step X-w5.  ``loan`` is a
    :class:`~app.services.savings_dashboard_service._types.LoanDetail` or
    ``None``, and ``None`` IS "this account is not a configured loan".
    """
    loan = _get(ad, "loan")
    figures = None if loan is None else _get(loan, "figures")
    params = None if loan is None else _get(loan, "params")
    return {
        "loan_figures": _figures(figures),
        "loan_original_principal": _money(_get(params, "original_principal")),
        "loan_params_account_id": _get(params, "account_id"),
    }


def _balances(ad, seam_maps):
    """The projection's DENSE period map, every column.

    **Added at plan step X-w6 (ruling R-CM), and the reason is the finding.**
    Plan step X-w folded the dense map onto the projection, which means the
    three NARROW producers now carry one their consumers do not read -- and this
    file dumped every projection field EXCEPT this one, while
    ``verify_balance_baseline.py`` never runs this package at all.  So a defect
    confined to ``AccountProjection.balances`` on a narrow-producer path was
    invisible to BOTH instruments: exactly Section 8's "a census and a gate can
    be blind the same way, and then they confirm each other".

    Dumped in full rather than as a digest.  A digest answers "did the map
    change" and this file's whole job is to answer "which figure moved"; 60
    columns per account is a few hundred lines of blob against an instrument
    that already carries the whole daily series one layer down.

    **What this does and does not reach, stated so it is not discovered later.**
    It covers every projection the FULL build produces and the one
    ``compute_account_balance_cell`` returns.  It cannot reach the maps inside
    ``compute_debt_summary`` and ``compute_goal_progress``, because those
    producers reduce projections into a summary and return no projection at
    all -- so their ``balances`` is internal by construction and a change to it
    is only observable through a figure this file already dumps.

    **On a PRE-X-w tree it falls back to the seam's own map**, and that is not a
    courtesy -- it is what keeps this one file producing the same blob on both
    trees, which is the only reason its diff means anything.  The projection had
    no such field before plan step X-w1; dumping ``null`` there would make every
    account's map read as a change and drown the comparison.  Falling back to
    :func:`app.services.balance_at.build_maps` compares the NEW projection's map
    against the SEAM's map for the same account, which is exactly ruling R-CG's
    claim -- so the cross-tree diff proves it directly instead of leaving it to
    be inferred from the figures downstream.  It goes when plan step X-x deletes
    X-w's tolerances.

    Args:
        ad: The per-account projection (or the narrow balance cell).
        seam_maps: ``{account_id: period map}`` from the seam, used only when
            *ad* carries no ``balances`` field of its own.
    """
    balances = _get(ad, "balances")
    if balances is None:
        balances = seam_maps.get(_get(ad, "account").id)
    if balances is None:
        return None
    return {str(period_id): _money(value) for period_id, value in balances.items()}


def _projection(ad, seam_maps):
    """One per-account projection, every field."""
    account = _get(ad, "account")
    interest = _get(ad, "interest_params")
    investment = _get(ad, "investment_params")
    return {
        "account_id": account.id,
        "account_name": account.name,
        "current_balance": _money(_get(ad, "current_balance")),
        "balances": _balances(ad, seam_maps),
        "projected": {
            label: _money(value)
            for label, value in sorted((_get(ad, "projected") or {}).items())
        },
        "needs_setup": _get(ad, "needs_setup"),
        "is_liability": _get(ad, "is_liability"),
        "interest_apy": _money(_get(interest, "apy")),
        "investment_return": _money(_get(investment, "assumed_annual_return")),
        **_loan_halves(ad),
    }


def _outlook(outlook):
    """The three states of the payoff outlook."""
    if outlook is None:
        return None
    return {
        "all_clear_on": _date(_get(outlook, "all_clear_on")),
        "never_clears": _get(outlook, "never_clears"),
        "is_loan_free": _get(outlook, "is_loan_free"),
    }


def _debt_summary(summary):
    """The debt summary's money fields, its outlook, its DTI block, its rail.

    ``principal_paid_fraction`` is dumped HERE since plan step X-w5.  It was
    excluded, and carried by three top-level keys instead, because plan step
    X-u's own tree had no such field on the summary -- a shim for a shape that
    has not existed since ``e2cdc589``.  Dumping it in one place covers both the
    full build's summary and the narrow producer's, which is what those three
    keys were reaching for.
    """
    if summary is None:
        return None
    dti = _get(summary, "dti")
    return {
        "total_debt": _money(_get(summary, "total_debt")),
        "total_monthly_payments": _money(
            _get(summary, "total_monthly_payments"),
        ),
        "weighted_avg_rate": _money(_get(summary, "weighted_avg_rate")),
        # RENAMED from ``revolving_debt`` at plan step credit_card:CC-5-5c
        # (ruling R-CC68), so both spellings are read -- the same tolerance
        # the archived rows carry below, so the blob compares across the rename.
        "debt_without_payoff_date": _money(
            _get(summary, "debt_without_payoff_date")
            if _get(summary, "debt_without_payoff_date") is not None
            else _get(summary, "revolving_debt"),
        ),
        "payoff_outlook": _outlook(_get(summary, "payoff_outlook")),
        "principal_paid_fraction": _money(_principal_fraction(summary)),
        "dti_ratio": _money(_get(dti, "ratio")),
        "dti_label": _get(dti, "label"),
    }


def _series(series):
    """The 2-year trend: periods, net, and every composition band.

    Read through :func:`_get` because plan step X-w3 turned the series and its
    period descriptors into frozen value objects (ruling R-CI); the tolerance is
    what lets this ONE file produce the same blob on the HEAD tree and the new
    one, and it goes when plan step X-x deletes X-w's tolerances.

    **A period point is its ``end_date`` and nothing else.**  It carried a
    ``period_index`` until plan step X-w6 deleted that field for having no
    production reader (ruling R-CL), so dumping it here would make an INTENDED
    shape change read as a per-point diff on every cross-tree run -- the exact
    thing this file's normalization discipline exists to prevent.  The window's
    identity is fully carried by the dates, which are dumped and which the chart
    is actually built from.
    """
    if series is None:
        return None
    return {
        "periods": [
            {"end_date": _date(_get(point, "end_date"))}
            for point in _get(series, "periods")
        ],
        "net": [_money(value) for value in _get(series, "net")],
        "current_index": _get(series, "current_index"),
        "composition": {
            band: [_money(value) for value in values]
            for band, values in sorted(_get(series, "composition").items())
        },
    }


def _horizon(horizon):
    """The Horizon range: dates, net, bands, and every milestone flag."""
    if horizon is None:
        return None
    return {
        "dates": [_date(value) for value in horizon["dates"]],
        "current_index": horizon["current_index"],
        "net": [_money(value) for value in horizon["net"]],
        "composition": {
            band: [_money(value) for value in values]
            for band, values in sorted(horizon["composition"].items())
        },
        "milestones": [
            {"date": _date(m["date"]), "label": m["label"]}
            for m in horizon["milestones"]
        ],
    }


def _today_figures(region):
    """The hero and its three chips, wherever this tree keeps them.

    Pre-X-w3 they are four keys spread flat on the region dict; post-X-w3 they
    are the four fields of the region's ``today`` value object.  Both normalize
    to the same four blob entries, so the shape change is invisible here and a
    FIGURE change is not.
    """
    today = _get(region, "today")
    source = region if today is None else today
    return {
        name: _money(_get(source, name))
        for name in (
            "net_worth", "total_assets", "total_liabilities", "liquid",
        )
    }


def _goal(datum):
    """One goal progress row, every money figure.

    Read through :func:`_get` because plan step X-w4 turned the row into a
    frozen ``GoalProgress`` (ruling R-CI) and plan step X-aa turned its nested
    trajectory into a ``GoalTrajectory`` (ruling R-CO); both tolerances go with
    X-w's others.

    **The ``trajectory is None`` branch this carried is GONE** (X-aa's
    adversarial review).  ``calculate_trajectory`` returned a full dict on the
    old tree and returns a full value object on the new one -- it has never
    returned ``None`` -- so that was not a cross-tree tolerance, it was the same
    guard-that-cannot-fail this very step deletes from ``savings/dashboard.html``,
    sitting in the function the step was rewriting.  (Contrast
    :func:`_principal_fraction` below, whose ``None`` guard is LIVE: a user with
    no loan accounts genuinely has no summary.)
    """
    trajectory = _get(datum, "trajectory")
    return {
        "goal_id": _get(datum, "goal").id,
        "current_balance": _money(_get(datum, "current_balance")),
        "progress_pct": _money(_get(datum, "progress_pct")),
        "remaining_periods": _get(datum, "remaining_periods"),
        "required_contribution": _money(_get(datum, "required_contribution")),
        "resolved_target": _money(_get(datum, "resolved_target")),
        "monthly_contribution": _money(_get(datum, "monthly_contribution")),
        "income_descriptor": _get(datum, "income_descriptor"),
        "has_salary_data": _get(datum, "has_salary_data"),
        "trajectory": {
            "months_to_goal": _get(trajectory, "months_to_goal"),
            "projected_completion_date": _date(
                _get(trajectory, "projected_completion_date"),
            ),
            "pace": _get(trajectory, "pace"),
            "required_monthly": _money(_get(trajectory, "required_monthly")),
        },
    }


def _principal_fraction(summary):
    """The principal-paid fraction of one :class:`DebtSummary`, or ``None``.

    A plain field read since plan step X-w5.  It was tolerant of a tree where
    the summary had no such attribute -- the pre-X-u shape, where the only
    producer of the fraction was the second debt producer X-u deleted -- and
    that branch has been dead since ``e2cdc589``.  The dump is still tolerant of
    a ``None`` SUMMARY (a user with no loan accounts), which is a live state.
    """
    return None if summary is None else summary.principal_paid_fraction


def _tracks(section):
    """The budget dashboard's tracks section (the debt track's composition)."""
    if section is None:
        return None
    out = {}
    for key, value in sorted(section.items()):
        if key == "debt":
            # The track's ``debt`` IS the ``DebtSummary``.  It was a
            # ``DebtTrack`` wrapping one until plan step X-u deleted the
            # wrapper, and the shim that unwrapped either shape went at X-w5.
            out[key] = _debt_summary(value)
        elif key == "goals":
            out[key] = [
                {
                    "name": row["name"],
                    "account_id": row["account_id"],
                    "current_balance": _money(row["current_balance"]),
                    "progress_pct": _money(row["progress_pct"]),
                    "target_amount": _money(row["target_amount"]),
                    "target_date": _date(row["target_date"]),
                    "pace": row["pace"],
                    "projected_completion_date": _date(
                        row["projected_completion_date"],
                    ),
                    "required_monthly": _money(row["required_monthly"]),
                    "monthly_contribution": _money(row["monthly_contribution"]),
                }
                for row in value
            ]
        else:
            out[key] = repr(value)
    return out


def _dump_user(user_id):
    """Every figure the savings package answers for one user.

    **A user with NO BASELINE SCENARIO is recorded as skipped, and that is a
    normalization, not an omission** (plan step X-v2).  Both real databases
    carry one such user -- the COMPANION, who owns no accounts, no pay periods
    and no scenario by design (``scripts/integrity_check`` DC-08 excludes that
    role for exactly this reason) and who cannot reach `/savings` at all,
    because ``require_owner`` 404s her off every balance surface.

    The two trees answer that state differently ON PURPOSE: before X-v2 this
    package returned a blob of fabricated ``$0.00`` figures for her, and after
    it the seam raises :class:`~app.exceptions.BaselineMissingError` for one
    application-level handler to answer.  Recording the SKIP keeps this one file
    producing the same blob on both trees, which is what makes the rest of the
    diff mean something -- and keeps the row visible, so a user who LOSES a
    baseline shows up as a change rather than vanishing.
    """
    if get_baseline_scenario(user_id) is None:
        return {"skipped": "no baseline scenario"}
    data = savings_dashboard_service.compute_dashboard_data(
        BalanceContext.build(user_id),
    )
    account_data = data["account_data"]
    # The pre-X-w fallback for ``_balances`` (see it for why).  Built from
    # the seam directly, so it is the SAME entry both trees answer from.
    seam_maps = balance_at.build_maps(
        [_get(ad, "account") for ad in account_data],
        BalanceContext.build(user_id),
    )
    # ONE narrow debt build per user, shared by the two keys that read it.
    narrow_summary = savings_dashboard_service.compute_debt_summary(
        BalanceContext.build(user_id),
    )
    return {
        "account_data": [_projection(ad, seam_maps) for ad in account_data],
        "grouped_accounts": {
            label: [_get(ad, "account").id for ad in group]
            for label, group in data["grouped_accounts"].items()
        },
        "group_subtotals": {
            label: _money(value)
            for label, value in data["group_subtotals"].items()
        },
        "property_equity": [
            {
                "account_id": _get(row, "account").id,
                "equity": _money(_get(_get(row, "equity"), "equity")),
                "ltv": _money(_get(_get(row, "equity"), "ltv")),
            }
            for row in data["property_equity"]
        ],
        "goal_data": [_goal(datum) for datum in data["goal_data"]],
        # The emergency-fund coverage became a frozen ``SavingsCoverage`` at
        # plan step X-aa (ruling R-CO); read field by field so the blob is the
        # same on either tree.  Goes with X-w's other tolerances at X-x.
        "emergency_metrics": {
            name: _money(_get(data["emergency_metrics"], name))
            for name in ("months_covered", "paychecks_covered", "years_covered")
        },
        "total_savings": _money(data["total_savings"]),
        "avg_monthly_expenses": _money(data["avg_monthly_expenses"]),
        "savings_accounts": [acct.id for acct in data["savings_accounts"]],
        # The archived rows became a frozen ``ArchivedAccount`` at plan step
        # X-w2 and the figure was RENAMED (ruling R-CH), so both spellings are
        # read here -- this file's own tolerance for the shape change it is
        # measuring, which is what lets it produce the same blob on the HEAD
        # tree and the new one.  It goes when plan step X-x deletes X-w's
        # tolerances, exactly as this file's header requires.
        # An archived DEBT is an ``ArchivedDebt`` carrying ``owed`` since plan
        # step credit_card:CC-5-5c (ruling R-CC67); dumped under its own key so
        # a debt's figure never compares against an asset's assertion.
        "archived_accounts": [
            {
                "account_id": _get(row, "account").id,
                "owed": _money(_get(row, "owed")),
                "current_balance": _money(
                    _get(row, "last_anchor_balance")
                    if _get(row, "last_anchor_balance") is not None
                    else _get(row, "current_balance"),
                ),
            }
            for row in data["archived_accounts"]
        ],
        "debt_summary": _debt_summary(data["debt_summary"]),
        # The region became a frozen ``_NetWorthRegion`` at plan step X-w3, with
        # the four today figures COMPOSED on a ``today`` field where they used
        # to be spread flat (ruling R-CI).  ``_today_figures`` normalizes both,
        # so the blob is the same on either tree; plan step X-x deletes it.
        "net_worth": {
            **_today_figures(data["net_worth"]),
            "series": _series(_get(data["net_worth"], "series")),
            "horizon": _horizon(_get(data["net_worth"], "horizon")),
        },
        "sparklines": {
            str(account_id): [_money(value) for value in values]
            for account_id, values in sorted(data["sparklines"].items())
        },
        # The presentation boundary: the exact JSON the canvas carries.
        "chart_json": json.loads(_serialize_net_worth_chart(data["net_worth"])),
        "sparkline_points": {
            str(key): value
            for key, value in sorted(
                _serialize_sparklines(data["sparklines"]).items(),
            )
        },
        # The narrow producers, each answering over its own restricted
        # load -- the "identical figures by construction" promise, measured.
        # There were four until plan step X-u deleted the second debt one.
        # The principal-paid fraction rides inside BOTH debt summaries since
        # plan step X-w5, where the two top-level keys that used to carry it
        # across the X-u shape change were deleted with the shim.
        "narrow_debt_summary": _debt_summary(narrow_summary),
        "narrow_goal_progress": [
            _goal(datum)
            for datum in savings_dashboard_service.compute_goal_progress(
                BalanceContext.build(user_id),
            )
        ],
        "narrow_balance_cells": {
            str(_get(ad, "account").id): _cell(
                user_id, _get(ad, "account").id, seam_maps,
            )
            for ad in account_data
        },
        "tracks_section": _tracks(
            dashboard_service.compute_tracks_section(
                BalanceContext.build(user_id),
            ),
        ),
    }


def _cell(user_id, account_id, seam_maps):
    """The cockpit balance cell for one account -- the NARROW projection.

    Its dense map is dumped too since plan step X-w6 (ruling R-CM): this is the
    one narrow producer that returns a projection, so it is where a defect
    confined to a narrow path's ``balances`` becomes visible to this file.
    """
    cell = savings_dashboard_service.compute_account_balance_cell(
        BalanceContext.build(user_id), account_id,
    )
    if cell is None:
        return None
    return {
        "account_id": _get(cell, "account").id,
        "current_balance": _money(_get(cell, "current_balance")),
        "balances": _balances(cell, seam_maps),
        "is_liability": _get(cell, "is_liability"),
    }


def main():
    """Dump every user's savings-producer figures to the given path."""
    out_path = pathlib.Path(sys.argv[1])
    app = create_app()
    with app.app_context():
        blob = {}
        for user in db.session.query(User).order_by(User.id).all():
            blob[str(user.id)] = _dump_user(user.id)
    out_path.write_text(
        json.dumps(blob, indent=2, sort_keys=True), encoding="utf-8",
    )
    print(f"wrote {out_path} ({len(blob)} users)")


if __name__ == "__main__":
    main()
