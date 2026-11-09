"""Dump every figure a /retirement and /savings render publishes, for a diff.

**The standing byte-identity instrument for those two renders**, and it grades
one leaf at a time.  It was written as ``verify_retirement_pass_cutover.py`` for
plan step C2-f2d-1 (which moved WHERE the producers get their read pass) and
generalised at C2-f2d-2 (which made the two implementations of the retirement
picture into one).  Both leaves make the same promise -- every published figure
comes back identical and a single moved cent is a defect -- so both want the
same dump, and one instrument taught two generations beats two instruments where
only one is ever taught the next thing.

**Why the other harnesses cannot grade it.**
:mod:`tests.manual.verify_reader_baseline` dumps
``savings_dashboard_service.compute_dashboard_data`` and the retirement
projection -- but the ``/retirement`` PAGE is neither of those.  It is the
readiness verdict beside the lever card, and both leaves' whole subject is what
those two share; a harness that calls neither would report "nothing moved" over
the surface that actually changed, which is ``docs/plans/verification.md``
standard 3's free-pass shape.  :mod:`tests.manual.verify_balance_baseline` walks
the seam, which neither leaf touches.

**It runs on BOTH sides of the cutover**, because a harness that compiles on
only one side proves nothing (``docs/plans/lessons.md``).  Two mechanisms carry
that: :func:`_call` reads a ``/savings`` producer's own signature and supplies
whichever of ``user_id`` / ``balance_ctx`` the tree in front of it declares, and
the ``/retirement`` block detects whether :mod:`app.services.retirement_plan`
exists and drives the tree's own entry points either way.

**What it captures, and the ONE thing it deliberately does not.**  The figures
dumped are what a render PUBLISHES: the readiness dict, the levers dict, the
per-account projections, the assumptions rail's blended return, and the what-if
panel's baseline / override / deltas.  It does NOT capture ``compute_gap_data``'s
gross-frame ``gap_analysis``, which C2-f2d-2 deletes: that record was computed at
the possibly-unset stored tax rate and read by exactly one line of ``app/`` --
``_net_frame``, for a pension figure it was handed in the first place -- so it
reached no screen, and dumping a deleted intermediate would report a structural
change as a moved figure.  Every observable it carried flows into ``readiness``
or ``projections``, both captured whole.

**RUN BOTH SIDES ON THE SAME CIVIL DAY.**  Every producer here seeds from the
process clock, so a BEFORE captured yesterday and an AFTER captured today differ
by the calendar rather than by the change.  The clock is deliberately NOT pinned:
pinning it would hide the very thing C2-f2d-1 was about, because on that HEAD
side each producer read the clock for itself and a pinned harness would paper
over the second read.  The run STAMPS the day it captured (:data:`_CAPTURED_ON`)
so a diff across midnight is visible in the first line of the file rather than
inferred from surprise.

**It also counts what a render RUNS**, which is each leaf's end state rather
than its regression gate: read passes under ``pass_counts`` (C2-f2d-1: 2 -> 0
below the route) and producer calls under ``producer_counts`` (C2-f2d-2: the
loaders and the projection walk, each expected to drop).  Those keys are
expected to MOVE, which is why they live apart from the figures -- the diff that
proves a leaf is every FIGURE identical while these fall.

**Usage** (from the repository root, against a production CLONE)::

    DATABASE_URL=postgresql://.../shekel_c2f2d \\
        .venv/bin/python tests/manual/verify_retirement_render.py before.json
    # ... make the change ...
    DATABASE_URL=postgresql://.../shekel_c2f2d \\
        .venv/bin/python tests/manual/verify_retirement_render.py after.json
    diff before.json after.json

Every figure is stringified through :func:`_money` so a ``Decimal`` diff is a
TEXT diff: ``Decimal("1.10")`` and ``Decimal("1.1")`` are equal numerically and
must not be reported as a move, while ``1.10`` -> ``1.11`` must be.

**A COVERAGE HOLE this instrument cannot close, measured 2026-08-16.**  On the
developer's data all three projecting accounts carry the SAME
``assumed_annual_return`` (10.500%), so the balance-weighted blend is that rate
whatever the weights and whatever the rounding: a planted defect that removed
the blended return's percent quantization entirely moved ZERO lines here.  The
blend's WEIGHTING, its zero-balance and zero-rate arms, and its two-decimal
round-trip are therefore ungraded by this file and are pinned by unit tests
instead (``tests/test_services/test_retirement_plan.py``).  Only one of the two
owners on the clone reaches the retirement producers at all -- the other has no
pay cadence and every figure is a recorded refusal -- so this dump grades ONE
owner's data, which is a second reason not to read a clean diff as coverage.

The instrument was shown FIRING on a defect it CAN see: narrowing
:func:`~app.services.retirement_plan.picture_at`'s memo key to the month offset
alone -- the key-narrowing class the C2-f2d-1 seed memo already paid for --
moved 452 lines and collapsed the what-if panel's delta from ``-2.7`` points /
``-$46,583.92`` to zero.
"""

import importlib
import json
import pathlib
import sys
import traceback
from datetime import date
from decimal import Decimal

from app import create_app
from app.extensions import db
from app.models.user import User
from app.services import (
    dashboard_service,
    retirement_dashboard_service,
    retirement_levers,
    retirement_readiness,
    savings_dashboard_service,
)
from app.services.balance_at import BalanceContext

from tests._test_helpers import counting_calls, counting_read_passes

# The module plan step C2-f2d-2 adds, or ``None`` on every tree before it.
# Resolved rather than imported so this file states one boundary in one way:
# what exists here is discovered, never assumed -- the same discipline
# :func:`_before` applies to individual entry points.
try:
    retirement_plan = importlib.import_module("app.services.retirement_plan")
except ImportError:  # pragma: no cover - taken only on the HEAD side
    retirement_plan = None

# The day this capture ran.  Written into the dump so a diff taken across
# midnight names its own cause instead of reading as a defect.
_CAPTURED_ON = date.today()

# The what-if the readiness fragment is exercised at, when the tree has no
# salary-path what-if -- see :func:`_raise_probe` for the one it prefers.
#
# **It was a merit horizon until plan step salary:S3-c**, chosen because that
# axis moved the SALARY PATH and so made the override arm recompute the
# pension, the income target and the projection -- the widest of the three
# what-ifs.  Ruling **R-SAL11** deleted the setting behind it, and the axis
# with it, so this harness would name a keyword neither tree accepts.  The SWR
# is the widest of what remained AND, unlike the return rate, it exists on
# every tree this instrument still has to run on -- which is the property that
# matters here, because a harness that compiles on only one side of a cutover
# proves nothing.
#
# **Plan step salary:S3-f-2b restored a salary-path what-if** -- the per-raise
# end-year probe on the plan point -- and the what-if moved back onto it: on a
# tree whose ``PlanPoint`` carries ``raise_end_years`` and whose owner has a
# recurring raise, the override arm ends the FIRST recurring raise after its
# own effective year, which recomputes the pension, the income target, the
# current paycheck and the payroll feeds.  This SWR constant is the fallback
# for every older tree and for an owner with no recurring raise.  A diff taken
# ACROSS that step therefore moves the ``whatif_override`` and
# ``levers_at_whatif`` blocks for that reason alone; every other block must be
# byte-identical.
_SWR_WHATIF = Decimal("0.0300")

# The pre-C2-f2d-2 ``compute_readiness_whatif`` took its what-ifs as keyword
# arguments; the tree that leaf ships takes ONE ``PlanPoint``.  Held as a dict
# for the same reason :func:`_before` resolves entry points by name: naming a
# keyword the current signature does not have would make this file read as
# broken on the tree it is running on, over a branch that cannot execute there.
_HEAD_WHATIF_KWARGS = {"swr_override": _SWR_WHATIF}


def _money(value):
    """Stringify a Decimal so the diff is textual and exact.

    ``None`` passes through as ``None`` so an absent figure stays
    distinguishable from a zero one.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return f"{value:.6f}"
    return str(value)


def _plain(value, depth=0):
    """Serialise ANY producer result to comparable plain data.

    Generic rather than field-by-field, for the reason
    :mod:`tests.manual.verify_reader_baseline` records: a first draft of that
    harness named the fields it expected with ``getattr(obj, "...", None)`` and
    read ``None`` on both sides over three surfaces it had never captured.
    Walking the structure removes the guess.

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

    A producer that refuses for one owner must not hide every figure for the
    others, and the refusal itself is a fact worth diffing: a reader that starts
    raising where it used to answer has to show up as a MOVE rather than as a
    crashed run.  The clone carries an owner with no pay schedule and no
    baseline scenario, so this arm is live rather than defensive.
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


def _plan_source(user_id, share):
    """A zero-arg source of whatever a producer on THIS tree is handed.

    The two trees hand a producer different things -- a
    :class:`~app.services.balance_at.BalanceContext` before plan step C2-f2d-2,
    a :class:`~app.services.retirement_plan.RetirementInputs` after it -- and
    the harness needs both a SHARED one (what a route does) and a fresh one per
    producer (the topology each leaf replaced).  One factory answers both
    questions on both trees, so :func:`_retirement_figures` never branches on
    the tree for anything but the call shape itself.

    Args:
        user_id: The owner to build for.
        share: True to hand every producer ONE object; False to build a fresh
            one per call.

    Returns:
        A zero-argument callable.
    """
    def _fresh():
        pass_ = BalanceContext.build(user_id)
        if retirement_plan is None:
            return pass_
        return retirement_plan.load_retirement_inputs(pass_)

    if not share:
        return _fresh
    # LAZY, so an owner the loader refuses (no pay cadence -- the clone carries
    # one) raises inside the caller's own :func:`_guard` and is recorded as a
    # RAISE per figure, exactly as it is on the HEAD side.  Building eagerly
    # here aborted the whole dump for that owner instead.
    cached = []

    def _shared():
        if not cached:
            cached.append(_fresh())
        return cached[0]

    return _shared


def _before(module, name):
    """Resolve a HEAD-generation entry point, by name.

    Two kinds of entry live behind this: names the C2-f2d-2 tree DELETED
    (``compute_gap_data``, ``readiness_from_gap_data``,
    ``compute_slider_defaults``) and one it kept under a different signature
    (``compute_readiness_whatif``, which took three keyword what-ifs and now
    takes one ``PlanPoint``).  Naming either statically would make this file
    read as broken on the tree it is running on, over branches that cannot
    execute there.  Resolving by name says what is true: these are the OTHER
    generation's entries, looked up on the tree in front of the harness.

    Args:
        module: The module that owns the entry point.
        name: Its attribute name.

    Returns:
        The callable.

    Raises:
        AttributeError: This tree does not have it -- which means the caller
            took the HEAD branch on a tree that is not HEAD.
    """
    return getattr(module, name)


def _figures_before(source):
    """The published figures on a tree WITHOUT ``retirement_plan``.

    Args:
        source: The :func:`_plan_source` factory, handing out read passes.

    Returns:
        The captured-figure dict (see :func:`_retirement_figures`).
    """
    gap = _guard("gap", lambda: _before(
        retirement_dashboard_service, "compute_gap_data",
    )(source()))
    failed = isinstance(gap, dict) and "RAISED" in gap
    return {
        "readiness": _plain(
            gap if failed
            else _guard("readiness", lambda: _before(
                retirement_readiness, "readiness_from_gap_data",
            )(gap)),
        ),
        "current_return": _plain(
            gap if failed
            else _guard("current_return", lambda: _before(
                retirement_dashboard_service, "compute_slider_defaults",
            )(gap)["current_return"]),
        ),
        "projections": _plain(
            gap if failed else gap["retirement_account_projections"],
        ),
        "salary_profiles": _plain(gap if failed else gap["salary_profiles"]),
        "settings": _plain(gap if failed else gap["settings"]),
        "levers": _plain(_guard("levers", lambda: (
            retirement_levers.compute_lever_data(source())
        ))),
        "whatif_baseline": _plain(_guard("whatif_baseline", lambda: (
            retirement_readiness.compute_readiness_whatif(source())
        ))),
        "whatif_override": _plain(_guard("whatif_override", lambda: (
            # Pylint: ``unexpected-keyword-arg`` -- this call runs ONLY on the
            # HEAD tree, where ``compute_readiness_whatif`` took the three
            # keyword what-ifs ``_before`` documents; the signature pylint
            # resolves is the POST tree's, which this branch never reaches.
            # A both-sides harness names one API the checker cannot see.
            # pylint: disable-next=unexpected-keyword-arg
            _before(retirement_readiness, "compute_readiness_whatif")(
                source(), **_HEAD_WHATIF_KWARGS,
            )
        ))),
    }


def _figures_after(source):
    """The published figures on a tree WITH ``retirement_plan``.

    The same figures under the same keys, read off the one picture producer.
    ``current_return`` is scaled to percent HERE for the same reason the route
    does it: the picture carries the fraction the growth math takes, and the
    rail is the only consumer that wants percent.

    Args:
        source: The :func:`_plan_source` factory, handing out
            :class:`~app.services.retirement_plan.RetirementInputs`.

    Returns:
        The captured-figure dict (see :func:`_retirement_figures`).
    """
    inputs = _guard("inputs", source)
    if isinstance(inputs, dict) and "RAISED" in inputs:
        picture, failed = inputs, True
    else:
        picture = _guard("picture", lambda: retirement_plan.picture_at(
            inputs, inputs.stored_plan,
        ))
        failed = isinstance(picture, dict) and "RAISED" in picture
    return {
        "readiness": _plain(
            picture if failed
            else _guard("readiness", lambda: (
                retirement_readiness.readiness_from_picture(picture)
            )),
        ),
        "current_return": _plain(
            picture if failed
            else picture.blended_return * Decimal("100"),
        ),
        "projections": _plain(picture if failed else picture.projections),
        "salary_profiles": _plain(
            picture if failed else inputs.gap.salary_profiles,
        ),
        "settings": _plain(picture if failed else inputs.gap.settings),
        "levers": _plain(_guard("levers", lambda: (
            retirement_levers.compute_lever_data(source())
        ))),
        # The levers AT THE WHAT-IF, which is what the fragment renders since
        # plan step C2-f2d-4 and what nothing else in this file captures: a
        # harness that only ever solved the stored plan would report "nothing
        # moved" over that step's entire subject.
        "levers_at_whatif": _plain(_guard("levers_at_whatif", lambda: (
            _levers_at_whatif(source())
        ))),
        "whatif_baseline": _plain(_guard("whatif_baseline", lambda: (
            retirement_readiness.compute_readiness_whatif(source())
        ))),
        "whatif_override": _plain(_guard("whatif_override", lambda: (
            _whatif_override(source())
        ))),
        # WHICH point the what-if blocks above were taken at, so a diff that
        # moves them across a step that changed the what-if (salary:S3-f-2b)
        # names its own cause on the line beside them.
        "whatif_point": _plain(_guard("whatif_point", lambda: (
            _whatif_at(source())
        ))),
    }


def _raise_probe(inputs):
    """The per-raise end-year what-if, or ``None`` where the tree or owner has none.

    Ends the owner's FIRST recurring raise (lowest id, the point's own order)
    after its effective year -- the tightest end year the column permits, and
    the probe that moves the most of the salary path.  A raise already stored
    as ending in its effective year makes this the stored plan, which is
    recorded rather than avoided: the dump then shows a zero delta, and that
    is a fact about the owner's data.

    Args:
        inputs: The render's ``RetirementInputs``.

    Returns:
        ``{raise_id: ("year", effective_year)}``, or ``None`` on a tree before
        plan step salary:S3-f-2b or for an owner with no recurring raise.
    """
    end_years = getattr(inputs.stored_plan, "raise_end_years", None)
    if not end_years:
        return None
    raise_id = end_years[0][0]
    row = next(
        raise_obj
        for profile in inputs.gap.salary_profiles
        for raise_obj in profile.raises
        if raise_obj.id == raise_id
    )
    # The start's year: a DATE since plan step salary plan#3048, a year column
    # before it -- read whichever this tree's row carries, so the harness runs
    # on both sides of that step.
    starts = getattr(row, "effective_date", None)
    return {raise_id: ("year", starts.year if starts else row.effective_year)}


def _whatif_at(inputs):
    """The what-if point, on whichever tree is in front of us.

    The C2-f2d-2 tree took three keyword overrides; C2-f2d-4 resolves them
    against the owner's settings through ``plan_with``, so the point is built
    where the settings are; S3-f-2b resolves a per-raise probe there too, and
    it is the what-if this prefers (see :func:`_raise_probe`).

    Args:
        inputs: The render's ``RetirementInputs``.

    Returns:
        The ``PlanPoint``, or ``None`` on a tree with no ``plan_with``.
    """
    builder = getattr(inputs, "plan_with", None)
    if builder is None:
        return None
    probe = _raise_probe(inputs)
    if probe is not None:
        return builder(raise_probes=probe)
    return builder(swr_override=_SWR_WHATIF)


def _whatif_override(inputs):
    """The readiness what-if, called the way this tree's signature takes it."""
    point = _whatif_at(inputs)
    if point is None:
        return retirement_readiness.compute_readiness_whatif(
            inputs, retirement_plan.PlanPoint(**_HEAD_WHATIF_KWARGS),
        )
    return retirement_readiness.compute_readiness_whatif(inputs, point)


def _levers_at_whatif(inputs):
    """The lever card solved AT the what-if, where the tree supports it."""
    point = _whatif_at(inputs)
    if point is None:
        return {"UNSUPPORTED": "this tree solves the levers at the stored plan"}
    return retirement_levers.compute_lever_data(inputs, point)


def _retirement_figures(user_id, share):
    """Every figure the /retirement page and its what-if fragment publish.

    The page derives the retirement picture and shapes the readiness verdict
    from it, renders the assumptions rail's blended return and the per-account
    projections table, and runs the lever card beside all of it; the fragment
    runs the what-if panel and, when a stepper moved, the levers again.  Every
    one is captured, because every one is a figure a person reads.

    Args:
        user_id: The owner to capture.
        share: True to run every producer off ONE loaded object -- the
            production topology -- or False to give each its own.

    Returns:
        dict of published figures, identically keyed on both trees.
    """
    source = _plan_source(user_id, share)
    if retirement_plan is None:
        return _figures_before(source)
    return _figures_after(source)


def _retirement(user_id):
    """The /retirement figures, captured TWICE: per-producer and shared.

    **The per-producer capture alone would not grade either leaf**, and an
    earlier version of this file did only that (adversarial code review,
    2026-08-16).  A dump taken that way runs every producer off its OWN loaded
    object -- which is the topology BEFORE the change, on both sides of the
    diff.  It proves the figures did not move when a producer's inputs arrived
    by parameter instead of being built inside; it says nothing about the
    configuration production now runs, where every producer SHARES one.

    ``shared`` is that configuration, and comparing the two blocks inside a
    single AFTER capture is a self-consistency check needing no HEAD side: if
    sharing moved a figure, these two disagree.  On the HEAD side of C2-f2d-1
    the two blocks were identical by construction (no producer took a pass),
    which is why the key exists on both and the diff stays readable.
    """
    return {
        "per_producer_pass": _retirement_figures(user_id, share=False),
        "shared_pass": _retirement_figures(user_id, share=True),
    }


def _savings(user_id):
    """The /savings page and both narrow producers the budget dashboard runs.

    ``compute_dashboard_data`` carries the Horizon range, which is where the
    second read pass on this page came from: its retirement / investment bands
    reuse the /retirement engine and that engine built its own context.  The two
    narrow producers are captured beside it because they share the loader whose
    calendar read this leaf routes through the pass.
    """
    return {
        "dashboard": _plain(_guard("savings_dashboard", lambda: (
            savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(user_id),
            )
        ))),
        "goal_progress": _plain(_guard("goal_progress", lambda: (
            savings_dashboard_service.compute_goal_progress(
                BalanceContext.build(user_id),
            )
        ))),
        "debt_summary": _plain(_guard("debt_summary", lambda: (
            savings_dashboard_service.compute_debt_summary(
                BalanceContext.build(user_id),
            )
        ))),
        # The budget dashboard's TRACKS section, which is the one production
        # caller that hands ONE pass to both narrow producers -- and therefore
        # the only place the calendar read this leaf moved onto the pass memo
        # runs under a shared pass.  Calling the two producers separately above
        # exercises neither that sharing nor that caller (adversarial code
        # review, 2026-08-16).
        "dashboard_tracks": _plain(_guard("dashboard_tracks", lambda: (
            dashboard_service.compute_tracks_section(
                BalanceContext.build(user_id),
            )
        ))),
    }


def _render_page(user_id):
    """Run ONE /retirement page render, exactly as the route does.

    The route's own call sequence, in one place, because both count blocks
    below measure it and a second spelling of "what the page runs" is how a
    measurement drifts from the thing it claims to measure.

    Args:
        user_id: The owner whose page to render.
    """
    if retirement_plan is None:
        pass_ = BalanceContext.build(user_id)
        gap = _before(
            retirement_dashboard_service, "compute_gap_data",
        )(pass_)
        _before(retirement_readiness, "readiness_from_gap_data")(gap)
        _before(retirement_dashboard_service, "compute_slider_defaults")(gap)
        retirement_levers.compute_lever_data(pass_)
        return
    inputs = retirement_plan.load_retirement_inputs(
        BalanceContext.build(user_id),
    )
    picture = retirement_plan.picture_at(inputs, inputs.stored_plan)
    retirement_readiness.readiness_from_picture(picture)
    retirement_levers.compute_lever_data(inputs)


def _render_fragment(user_id):
    """Run ONE /retirement/readiness fragment render with both steppers moved.

    Args:
        user_id: The owner whose fragment to render.
    """
    if retirement_plan is None:
        pass_ = BalanceContext.build(user_id)
        # Pylint: ``unexpected-keyword-arg`` -- the same both-sides argument as
        # the site in ``_figures_before``: this arm is guarded on the HEAD
        # tree's own absence of ``retirement_plan``, so the signature pylint
        # resolves is not the one this call reaches.
        # pylint: disable-next=unexpected-keyword-arg
        _before(retirement_readiness, "compute_readiness_whatif")(
            pass_, **_HEAD_WHATIF_KWARGS,
        )
        retirement_levers.compute_lever_data(pass_, months_override=12)
        return
    # THE ROUTE'S OWN SEQUENCE.  Since plan step C2-f2d-4 the fragment solves
    # the levers AT the what-if point and computes them on every refresh, so a
    # harness still calling them at the stored plan would measure a render the
    # app no longer performs -- which is the drift this function exists to
    # prevent, stated in its own docstring.
    inputs = retirement_plan.load_retirement_inputs(
        BalanceContext.build(user_id),
    )
    point = _whatif_at(inputs)
    retirement_readiness.compute_readiness_whatif(inputs, point)
    retirement_levers.compute_lever_data(inputs, point, months_override=12)


def _passes_below_route(user_id, render):
    """Count the read passes *render* opens BEYOND the one its route builds.

    The measurement that states C2-f2d-1's end state for ``/retirement``, whose
    route calls more than one producer: the honest question there is not "how
    many passes exist" but "how many does the render open BELOW the door".  The
    route's own pass is therefore built OUTSIDE the counter.

    BEFORE that leaf every producer ignored what it was offered and built its
    own, so this counted one per producer.  AFTER it, this counts ZERO: the
    render's only pass is the one the route opened, which is visible in
    ``app/routes/retirement.py`` rather than inferred from a number.

    Args:
        user_id: The owner to render for.
        render: The :func:`_render_page` / :func:`_render_fragment` callable.

    Returns:
        int -- passes opened below the route.
    """
    with counting_read_passes() as counter:
        _guard("passes_below_route", lambda: render(user_id))
    # *render* opens the ROUTE's pass itself, exactly as the route does, so the
    # answer is the total less that one.  Counting the route's own outside the
    # block instead would measure a different render from the one production
    # runs.
    return counter["n"] - 1


def _savings_page_passes(user_id):
    """Count the read passes ONE ``/savings`` render opens, whole.

    Counted whole rather than below-the-route because that route calls exactly
    one producer, so the producer IS the render's door and opening the pass
    there is correct.  The expected move at C2-f2d-1 was 2 -> 1, not 2 -> 0.
    """
    with counting_read_passes() as counter:
        _guard("savings_page_passes", lambda: (
            savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(user_id),
            )
        ))
    return counter["n"]


def _pass_counts(user_id):
    """How many read passes each render opens, and where.

    Expected to MOVE with C2-f2d-1, which is why it is reported apart from the
    figures: the diff that proves that leaf is every FIGURE identical while
    these drop.
    """
    return {
        "retirement_page_below_route": _passes_below_route(
            user_id, _render_page,
        ),
        "readiness_fragment_below_route": _passes_below_route(
            user_id, _render_fragment,
        ),
        "savings_page_total": _savings_page_passes(user_id),
    }


#: The producers C2-f2d-2 collapses, as ``(module path, attribute)``.  Named
#: rather than discovered, because the claim being measured is about these four
#: specifically: three LOADS the page ran twice, and the projection WALK whose
#: tenth run was the readiness verdict computed a second time.  Counted through
#: the shared ``counting_calls`` instrument rather than a copy of it, so the
#: architecture gate and this harness can never grade different questions.
_COUNTED_PRODUCERS = (
    ("app.services.retirement_dashboard_service", "load_gap_inputs"),
    ("app.services.retirement_projection", "build_projection_context"),
    ("app.services.retirement_projection", "load_projection_batch"),
    ("app.services.retirement_projection", "project_accounts_with_batch"),
)


def _producer_counts(user_id):
    """How many times each collapsed producer runs per render.

    C2-f2d-2's end state, reported apart from the figures for the same reason
    :func:`_pass_counts` is: these are EXPECTED to fall, and the diff that
    proves the leaf is every figure identical beside them.

    On a production clone before that leaf, one page render ran
    ``load_gap_inputs`` twice, ``build_projection_context`` twice,
    ``load_projection_batch`` twice and ``project_accounts_with_batch`` ten
    times -- nine of which are the retire-later search's real candidate dates
    and the tenth of which is the readiness verdict, computed again.

    Args:
        user_id: The owner to render for.

    Returns:
        ``{"page": {...}, "fragment": {...}}``.
    """
    counted = {}
    for label, render in (("page", _render_page),
                          ("fragment", _render_fragment)):
        with counting_calls(*_COUNTED_PRODUCERS) as counts:
            _guard(f"producer_counts:{label}", lambda r=render: r(user_id))
        counted[label] = dict(counts)
    return counted


def _dump_user(user_id):
    """Every published figure, and both count blocks, for one owner."""
    return {
        "retirement": _retirement(user_id),
        "savings": _savings(user_id),
        "pass_counts": _pass_counts(user_id),
        "producer_counts": _producer_counts(user_id),
    }


def main():
    """Dump every user's /retirement and /savings figures to the given path."""
    out_path = pathlib.Path(sys.argv[1])
    app = create_app()
    with app.app_context():
        blob = {"__captured_on__": _CAPTURED_ON.isoformat()}
        for user in db.session.query(User).order_by(User.id).all():
            blob[str(user.id)] = _dump_user(user.id)
    out_path.write_text(
        json.dumps(blob, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    print(f"wrote {out_path} ({len(blob) - 1} users, {_CAPTURED_ON})")


if __name__ == "__main__":
    main()
