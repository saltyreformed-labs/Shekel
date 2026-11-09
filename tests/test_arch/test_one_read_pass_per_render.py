"""Architecture test: what ONE HTTP render opens, and what it runs twice.

Plan steps **C2-f2d-1** (ledger row **P43**) and **C2-f2d-2** (row **P57**) --
two counts of the same shape, kept in one file because they share one seeding
fixture and one counting discipline, and because the question is the same
question at two levels: what does a render do more than once that it should do
exactly once?

What a read pass is
-------------------

:class:`app.services.balance_at.BalanceContext` pins the three facts every
money answer on a page depends on -- the owner, the baseline scenario, and the
day -- and memoizes what is expensive to derive from them: each loan's ledger
walk, and the owner's pay calendar.  It exists so a render answers those
questions once.

What went wrong, and what it cost
---------------------------------

Nothing enforced that.  Any producer holding a ``user_id`` could call
``BalanceContext.build`` and start a SECOND pass, reading the clock again, and
three did: ``retirement_projection.load_projection_batch`` (a leaf),
``retirement_dashboard_service.compute_gap_data`` and
``retirement_levers.compute_lever_data`` (each via that leaf).  Measured on a
production clone before this leaf: ``/savings`` opened 2 passes and derived the
pay calendar 3 times; ``/retirement`` opened 2; ``/retirement/readiness`` with
a what-if and a lever stepper opened 3.

Two figures published on ONE screen from two passes are two figures computed
against two clocks.  Reproduced on that clone by pinning each pass to a day
either side of a payday: the verdict card's after-tax projected savings read
``$836,398.65`` while the levers card beside it read ``$836,402.83`` -- ``$4.18``
apart.  The two projection AXES differed by one period (517 against 516), which
is not merely a countdown: the contribution lever's annuity factor folds over
the baseline probe's axis, so a one-period difference changes the extra
per-period contribution the page tells the owner to make.  The figures were not
wrong; they contradicted each other, and nothing in the application could
notice.

Why this test is BEHAVIORAL, not a name list
--------------------------------------------

The obvious gate is "these modules may not call ``BalanceContext.build``", and
as a NAME LIST it is the wrong one: it protects exactly the modules someone
remembered to list.  Counting the passes an actual RENDER opens has no list in
it, so a producer wired into one of these routes is covered the day it is wired
in.

**The gate that WOULD be structural is a layer predicate** -- "no module under
``app/services/**`` calls ``BalanceContext.build``", the pass built at the HTTP
boundary and nowhere below it (adversarial design review, 2026-08-16).  That is
not a name list and it is the right end state; **FIVE service modules stand
between here and it**, re-measured 2026-08-27 with
``grep -rl "BalanceContext\\.build(" app/services/`` -- the trailing paren
matters, because the bare name also matches docstrings recording calls that
were deleted.  They are ``calendar_service``,
``investment_dashboard_service/_context``,
``investment_dashboard_service/_orchestrator``, ``loan_recurrence_sync`` and
``tax_report_service``.  ``pay_calendar:C11`` closes them.

**The count went UP at recurrence plan step R7d-c-1 and came back DOWN inside
the same step**, which is worth the two sentences because the intermediate
state is committed.  Its first half had ``period_population`` open a pass
AFTER the write it repopulates, on the ground that a pass taken from above
holds the pre-write calendar and -- from R7d-c-2 -- the pre-write LOAN, which
nothing catches.  The developer refused that as a band-aid (ruling **R-R38**):
the root cause was that ``extend_pay_periods`` did a write and then a
read-dependent write in ONE call, so no caller could get between them.  Its
second half SPLIT the doors, so the route opens the pass between the two
writes, ``period_population`` takes one and builds none, and C11's predicate
needs no carve-out for it.  The paragraph above said FIVE, then SIX, and is
five again.

**This paragraph named EIGHT and three of the names were already wrong**, which
is why it now carries its date.  It listed
``savings_dashboard_service/_orchestrator`` (a door ``C2-f2d-3`` had closed)
and cited ``_orchestrator._pass_for``, a symbol ``git grep`` finds nowhere in
``app/``; and it went on naming both dashboard modules after ``C2-f2e`` retired
them into a package no part of which calls ``build``.  Found by that step's
adversarial design review, in the file the step had just added 281 lines to --
which is the residue rule's own case: editing a file clears that file's stale
claims.  Ledger row **P56** carries the predicate, and asserting it here today
would still fail on the five above, so the gate stays per-render and grows a
render at a time.

**What this file therefore does NOT prove.** It counts read passes, not clock
reads.  That gap was real and is now closed for these renders: until
pay-calendar plan step C2-f2e, ``compute_pension_summary``,
``compute_gap_net_biweekly`` and ``build_employer_salary_basis`` (since
deleted at salary:S3-e-2) each called
``date.today()`` for themselves on the very renders below, so this file could
be green while two cards disagreed across a New Year boundary (ledger row
**P55**, measured at a salary path one year shorter across 2026-12-31 ->
2027-01-01).  All three take the pass's day now, and the gate for THAT claim is
``test_retirement_dashboard_service.TestTheRenderDayOpensTheSalaryPath`` --
still a different question from the one counted here.

One LOAD, and one derivation per plan (C2-f2d-2)
-----------------------------------------------

Sharing the pass left the render loading everything TWICE and deriving the
readiness picture twice.  ``retirement_dashboard_service.compute_gap_data``
computed that picture at the stored retirement date, and
``retirement_levers._probe`` computed it at a shifted one; on a default page
load the lever solver's month-0 probe recomputed, from its own 46 queries,
exactly what the readiness hero had already drawn.  Measured on a production
clone: one page render issued 179 queries of which 86 -- 48% -- were the second
copy, and the two derivations agreed (funded ratio ``0.7463``, required
``$1,120,707.00``, projected after tax ``$836,398.65``) as they had every day
until one of them was edited.

``TestOneLoadPerRender`` counts what a render RUNS rather than what it opens.
Its subject is the four producers that were duplicated -- the gap inputs, the
projection context, the projection batch, and the per-account walk -- and the
walk is the interesting one: its count is NOT one, because the retire-later
lever legitimately probes many candidate retirement dates.  What the assertion
says is that the count equals the number of DISTINCT plans the render asked
about, which is the honest form of "nothing is derived twice".

Test IDs
--------

- ``test_retirement_render_opens_one_read_pass``
- ``test_savings_render_opens_one_read_pass``
- ``test_readiness_whatif_fragment_opens_one_read_pass``
- ``test_the_counter_sees_a_second_pass`` (negative control: the counter
  must be able to fail, or the three above grade nothing)
- ``test_retirement_render_loads_each_input_once``
- ``test_the_page_derives_no_plan_twice``
- ``test_the_load_counter_sees_a_second_load`` (negative control, same argument)
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import EmployerContributionTypeEnum, TxnTypeEnum
from app.models.investment_params import InvestmentParams
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.services import account_resolver, pay_schedule_service
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import calendar_for
from tests._stub_period import stub_period_on
from tests._test_helpers import (
    counting_calls,
    counting_read_passes,
    create_loan_account,
    generate_row_of,
    make_every_period_rule,
    make_investment_account,
    make_salary_profile,
)

#: The producers plan step C2-f2d-2 collapsed, as ``(module path, attribute)``.
#: The three LOADS ran twice per page render and three times per fragment; the
#: WALK ran once more than the render had distinct plans to ask about.
_ONCE_PER_RENDER = (
    ("app.services.retirement_dashboard_service", "load_gap_inputs"),
    ("app.services.retirement_projection", "build_projection_context"),
    ("app.services.retirement_projection", "load_projection_batch"),
)
_PER_PLAN = ("app.services.retirement_projection", "project_accounts_with_batch")

#: The DERIVATION itself, which is what "one calendar per render" is about.
#: A read pass MEMOIZES the calendar, so "one pass" and "one derivation" ought
#: to be the same claim -- and they were not.  Measured across pay-calendar plan
#: step C2-f2d-3: ``/savings`` opened ONE pass and derived the owner's schedule
#: SEVEN times, ``/retirement`` SIX, growing with the owner's investment-account
#: count, because two producers reached ``income_service`` through call chains
#: that held a pass and did not hand it over.  The pass counter beside this one
#: read 1 throughout.  Two counts of one question, and only one of them could
#: see it.
#:
#: **It named the LOADER (``calendar_for``) until plan step C4, and that made
#: it a name list rather than a rule.**  ``pay_calendar`` grew a second loader
#: door -- ``calendar_at_schedule``, for the rolling top-up, which runs BEFORE
#: the render opens its pass and so cannot share one -- and every count here
#: stayed at 1 while ``/grid`` and ``/dashboard`` began deriving twice.  A
#: guard that enumerates doors is blind to the next door by construction;
#: ``derive_periods`` is the one function every ``PayCalendar`` runs through
#: (``PayCalendar.__post_init__``), so counting it cannot be walked around.
_CALENDAR_DOOR = ("app.services.pay_calendar._derive", "derive_periods")

#: The PAYCHECK-PRICER door, as ``(module path, attribute)``.  One
#: :class:`~app.services.income_service.ProfilePaychecks` per salary profile
#: per render is the budget (plan steps **salary:R14-b**, **salary:S3-d**);
#: see :class:`TestOnePaycheckProjectionPerProfilePerRender` for why this door
#: and not ``calculate_paycheck``.
#:
#: **It was ``project_profile`` until plan step salary:S3-d deleted that
#: function.**  Counting a CONSTRUCTION rather than a projection is the same
#: question asked of the new shape: a pricer answers paydays on demand and
#: memoizes them, so building a second one for a profile a render already has
#: a pricer for is exactly the missed-memo defect this class was written
#: after.  What it can no longer see -- because the shape removed it -- is a
#: single producer re-projecting a window it had already projected.
_PROJECTION_DOOR = ("app.services.income_service", "ProfilePaychecks")

#: The amount model's salary-row door, as ``(module path, attribute)``: the
#: ONE producer of a salary row's figure (``salary_net_for``, plan step
#: X-au-d).  Counted by the both-paths case in
#: :class:`TestOnePaycheckProjectionPerProfilePerRender` to prove the render
#: it measures actually priced a row through the amount model, without which
#: "one pricer" is true of a render that never took the second path.
_SALARY_ROW_DOOR = ("app.services.income_service", "salary_net_for")

#: What the budget dashboard resolves about its own SUBJECT, as
#: ``(module path, attribute)``.  A render answers "which account is this page
#: about" and "what are this owner's settings" ONCE; ``/`` answered each twice
#: before pay-calendar plan step C2-f2e -- the account because the route
#: resolved one for its ``has_account`` flag while the pulse producer resolved
#: another, the settings because the producer's own head-of-function resolution
#: loaded the row and then its hero loaded it again for the staleness
#: threshold.  Neither is a pass and neither is a calendar, so the two counters
#: above were blind to both.
_SECTION_RESOLUTION = (
    ("app.services.account_resolver", "resolve_grid_account"),
    ("app.services.dashboard_service._section", "_get_user_settings"),
)


def _seed_projecting_account(db, seed_user, seed_periods_today):
    """Give the owner a 401(k) AND a retirement date, so both engines run.

    Necessary for ``/savings`` and not merely tidy: its Horizon skips the
    /retirement engine entirely -- returning zero bands before it builds a
    projection context at all -- when the owner has no retirement or investment
    account.  A count taken on the bare fixture would therefore read ``1`` on
    the tree this leaf FIXED and on the tree it fixed it FROM, which is the
    arm-that-cannot-fail shape ``docs/plans/lessons.md`` records.

    ``/retirement`` does not need it (both its producers load a projection
    batch before any early return), and it is seeded there anyway so the two
    counts are taken over the same state.

    **The retirement DATE is load-bearing and was missing until 2026-08-16.**
    Without one, ``compute_lever_data`` short-circuits on its ``no_horizon``
    arm before deriving anything, so the retire-later search never runs -- and
    a planted defect that made the lever solver bypass the picture memo
    entirely was measured to pass ``test_the_page_derives_no_plan_twice``
    unchanged.  The gate was grading a render whose expensive half never
    executed.  A horizon far enough out that the plan is NOT already funded
    keeps the binary search bisecting rather than answering at offset 0.

    **It varies on ONE axis, and that bounds what the counts above cover**
    (adversarial design review, 2026-08-16).  A future producer reached only by
    an owner who has a loan, an active goal, a credit card or a current period
    sits behind an early return on this fixture, so its pass would go uncounted.
    Widening the owner is the honest way to widen the gate; the claim these
    tests make is over the state seeded HERE, not over every owner.
    """
    # pylint: disable=import-outside-toplevel
    from app.models.user import UserSettings
    from app.utils.dates import add_months

    settings = (
        db.session.query(UserSettings)
        .filter_by(user_id=seed_user["user"].id)
        .one()
    )
    settings.planned_retirement_date = add_months(date.today(), 240)
    # **The SALARY PROFILE was load-bearing for this count until plan step
    # salary:R14-b, and the reason is recorded because the reason is what
    # decayed.**  ``income_service.get_current_gross_biweekly`` returned at
    # its profile lookup for an owner who had none, and every producer that
    # reached it did so behind that early return -- so a calendar count taken
    # without a profile read the same number on the tree that derived the
    # owner's schedule SEVEN times and on the tree that derives it once.  That
    # was the arm-that-cannot-fail shape one line up, found the same way: by
    # an adversarial review re-running the measurement over a richer owner.
    # That producer is DELETED, and the seam hands the pass's own calendar to
    # ``projection_inputs.load_payroll_feeds`` whether or not a profile
    # exists, so no early return can hide a derivation now.  The profile stays
    # because a richer owner is still the better subject -- it is what gives
    # the feed a paycheck to price -- but this case no longer DEPENDS on it,
    # and a future reader should not infer that it does.
    make_salary_profile(seed_user, db.session)
    account = make_investment_account(
        seed_user, db.session, seed_periods_today[0], Decimal("100000.00"),
    )
    db.session.commit()
    return account


def _seed_dashboard_owner(db, seed_user, seed_periods_today):
    """Widen :func:`_seed_projecting_account`'s owner until ``/`` runs it all.

    **Necessary for the budget dashboard, and the reason is the recorded
    lesson one function up.**  ``/`` runs three producers, and two of them --
    the savings-goal tracks and the debt track -- return an empty answer for an
    owner with no active goal and no loan account.  A count taken on the
    narrower fixture would therefore be taken over a render whose tracks tier
    never executed, which is the arm-that-cannot-fail shape
    ``docs/plans/lessons.md`` records and which cost pay-calendar plan step
    C2-f2d-3 a false measurement.

    Returns:
        The seeded loan :class:`~app.models.account.Account`, so a caller can
        assert the tier it feeds actually rendered.
    """
    # pylint: disable=import-outside-toplevel
    from app.models.savings_goal import SavingsGoal
    from app.utils.dates import add_months, display_today

    _seed_projecting_account(db, seed_user, seed_periods_today)
    loan = create_loan_account(
        seed_user, db.session, name="Dashboard Mortgage",
        principal=Decimal("200000.00"),
    )
    db.session.add(SavingsGoal(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        name="Dashboard Emergency Fund",
        target_amount=Decimal("10000.00"),
        target_date=add_months(display_today(), 24),
        is_active=True,
    ))
    db.session.commit()
    return loan


class TestOneReadPassPerRender:
    """Every render below opens exactly one :class:`BalanceContext`."""

    def test_retirement_render_opens_one_read_pass(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /retirement opens ONE pass, though it runs two producers.

        The route computes the readiness verdict
        (``retirement_dashboard_service.compute_gap_data``) and the two levers
        (``retirement_levers.compute_lever_data``).  Each opened a pass of its
        own before plan step C2-f2d-1, which is what let the verdict card and
        the lever card be computed against different days.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        with counting_read_passes() as counter:
            resp = auth_client.get("/retirement")

        assert resp.status_code == 200
        assert counter["n"] == 1, (
            f"/retirement opened {counter['n']} read passes; the route builds "
            "one and every producer below it must take that one"
        )

    def test_savings_render_opens_one_read_pass(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /savings opens ONE pass, though its Horizon reuses the engine.

        The Horizon range projects the retirement and investment bands through
        the /retirement engine verbatim -- deliberately, so the band is the
        engine's own projection rather than a parallel model -- and that engine
        built a second pass until plan step C2-f2d-1.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        with counting_read_passes() as counter:
            resp = auth_client.get("/savings")

        assert resp.status_code == 200
        assert counter["n"] == 1, (
            f"/savings opened {counter['n']} read passes; the Horizon's "
            "engine reuse must run in the page's own pass"
        )

    def test_dashboard_render_opens_one_read_pass(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET / opens ONE pass, though it runs two producers.

        The budget dashboard's two producers -- the pulse region and the
        position tracks -- each opened a pass of their own until pay-calendar
        plan step C2-f2e (ledger row **P61**), so this render held two and
        derived the owner's pay calendar twice.  It is the same defect
        ``/retirement`` had at C2-f2d-1 and the same remedy: the ROUTE opens
        the pass.
        """
        with app.app_context():
            _seed_dashboard_owner(db, seed_user, seed_periods_today)

        with counting_read_passes() as counter:
            resp = auth_client.get("/")

        assert resp.status_code == 200
        assert counter["n"] == 1, (
            f"/ opened {counter['n']} read passes; the route builds one and "
            "both the pulse region and the position tracks must take it"
        )

    @pytest.mark.parametrize("path", ["/grid", "/"])
    def test_a_rolling_owner_opens_TWO_passes_and_that_is_the_bound(
        self, app, db, auth_client, seed_user, seed_periods_today, path,
    ):
        """With rolling ON and the window SHORT, these renders open TWO.

        **A +1 recurrence plan step R7d-c-1 introduced, pinned here so a later
        session does not read it as a regression.**  The rolling top-up can
        CREATE pay periods, and the recurring rows generated into them are
        resolved in a read pass of their own: a pass taken from the render
        would hold the PRE-write calendar, and from plan step R7d-c-2 the
        pre-write LOAN as well.  The top-up runs inside its own
        ``write_transaction()`` and commits BEFORE the route builds the
        render's pass, exactly so the render sees what it wrote -- so the two
        passes are two database states, not two clocks on one screen, which is
        the defect every other case in this class grades.

        **The second pass is opened by the ROUTE**, in
        :func:`app.routes._period_population.populate_new_periods`, and not by
        the producer: ruling **R-R38** split the doors so the ordering is the
        order of two calls at the HTTP boundary.  What that changes for this
        count is nothing -- it was two before the split and is two after -- and
        what it changes for the census in the module docstring is one module.

        **TWO is the bound.**  A third would mean a producer below the render's
        pass opening one, which is the class's whole subject; a second pass
        inside the repopulation would mean it rebuilding one per template
        (``test_one_read_pass_serves_the_whole_repopulation``).

        **The deficit is asserted, not assumed.**  With the target below the
        periods the owner already has, ``top_up_rolling_window`` returns before
        ``extend_pay_periods`` and this case would silently grade the
        rolling-OFF path -- which every other test here already covers.

        Args:
            path: The render to count, as a URL.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_dashboard_owner(db, seed_user, seed_periods_today)
            before = len(calendar_for(user_id).saved())
            pay_schedule_service.set_rolling(
                user_id, enabled=True, target_periods=before + 3,
            )
            db.session.commit()

        with counting_read_passes() as counter:
            resp = auth_client.get(path)

        assert resp.status_code == 200
        with app.app_context():
            after = len(calendar_for(seed_user["user"].id).saved())
        assert after > before, (
            f"the top-up appended nothing ({before} -> {after} periods), so "
            f"this case graded the rolling-OFF path"
        )
        assert counter["n"] == 2, (
            f"{path} opened {counter['n']} read passes for a rolling owner "
            f"whose window was short; the repopulation opens one AFTER its "
            f"write and the route opens the render's, so anything above two "
            f"is a producer below the render's pass opening its own"
        )

    def test_dashboard_fragments_open_one_read_pass_each(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Each dashboard HTMX fragment opens exactly ONE pass.

        **A PIN rather than a gate, and it says so** (C2-f2e's adversarial
        design review, 2026-08-18): both fragments already opened exactly one
        pass on the merge base, because each ran a single producer that built
        one.  What moved is WHO builds it.  It is here so that a later step
        wiring a second producer onto either fragment -- which is exactly how
        ``/retirement`` acquired its two -- fails at that commit rather than at
        the next measurement.

        The two fragments are their own entry points -- the ``balanceChanged``
        pulse refresh and the anchor editor's revert target -- so "the route is
        the door" has to hold for them separately from the page.  The revert
        target is the interesting one: it renders the same ``#balance-display``
        control the pulse region carries, so a second pass here would let the
        swapped-back fragment disagree with the region around it.
        """
        with app.app_context():
            _seed_dashboard_owner(db, seed_user, seed_periods_today)

        for path in ("/dashboard/pulse", "/dashboard/balance"):
            with counting_read_passes() as counter:
                resp = auth_client.get(path, headers={"HX-Request": "true"})

            assert resp.status_code == 200
            assert counter["n"] == 1, (
                f"{path} opened {counter['n']} read passes; a fragment is a "
                "render and gets one pass like any other"
            )

    def test_readiness_whatif_fragment_opens_one_read_pass(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The what-if fragment opens ONE pass for three computations.

        It publishes the stored-settings baseline, the override, AND the lever
        outcome -- three pictures, three passes before this leaf.  The panel's
        whole product is the DELTA between the first two, which is the
        override's effect only if nothing else about the two reads differs; a
        second clock read is something else differing.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        with counting_read_passes() as counter:
            resp = auth_client.get(
                "/retirement/readiness"
                "?swr=3.5&months=24",
                headers={"HX-Request": "true"},
            )

        assert resp.status_code == 200
        assert counter["n"] == 1, (
            f"the readiness fragment opened {counter['n']} read passes; the "
            "baseline, the what-if and the levers share the route's one"
        )

    def test_the_counter_sees_a_second_pass(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """NEGATIVE CONTROL: the counter must be able to report a failure.

        The three assertions above are worth exactly as much as this one: a
        counter that never incremented would pass every one of them, on this
        tree and on the tree that opened two passes per render alike.

        Stated as a DIFFERENCE rather than against a literal, so it grades the
        instrument and only the instrument.  A hardcoded expectation here would
        be the render's own cost restated -- it would move whenever the counts
        above moved, and a control that fails for the same reason as the thing
        it controls is not a control.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        with counting_read_passes() as render_only:
            resp = auth_client.get("/retirement")
        assert resp.status_code == 200

        with counting_read_passes() as render_plus_one:
            assert auth_client.get("/retirement").status_code == 200
            with app.app_context():
                BalanceContext.build(seed_user["user"].id)

        assert render_plus_one["n"] == render_only["n"] + 1, (
            "the read-pass counter did not see a deliberately opened second "
            "pass, so the assertions beside it grade nothing"
        )


class TestOneSubjectResolutionPerRender:
    """The budget dashboard resolves its account and its settings ONCE.

    **A THIRD count of the same question, for the same reason the second one
    exists** (pay-calendar plan step C2-f2e).
    :class:`TestOneReadPassPerRender` proves the render opens one pass and
    :class:`TestOneCalendarDerivationPerRender` proves it derives one calendar;
    neither can see a render that holds both and still resolves the same
    ACCOUNT twice, because resolving an account opens nothing and derives
    nothing.  ``/`` did exactly that, and so did every one of its fragments
    with the settings row.
    """

    def test_dashboard_resolves_its_subject_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET / resolves the account once and the settings row once.

        Measured at 2 and 2 on the merge base over this fixture's owner --
        with the settings target repointed at ``app.services.dashboard_service``,
        because the package this names does not exist there.  Said because
        "shown firing" means the CLAIM was graded on that tree, not that this
        file ran on it unchanged.
        """
        with app.app_context():
            _seed_dashboard_owner(db, seed_user, seed_periods_today)

        with counting_calls(*_SECTION_RESOLUTION) as counts:
            resp = auth_client.get("/")

        assert resp.status_code == 200
        assert counts == {"resolve_grid_account": 1, "_get_user_settings": 1}, (
            f"/ resolved its own subject {counts}; the route resolves one "
            "section and the page's has_account flag, the pulse hero and the "
            "chart threshold all read that one answer"
        )

    def test_the_pulse_fragment_resolves_its_subject_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The pulse fragment resolves the settings row once, not twice.

        Measured at 2 on the merge base: ``_resolve_section_context`` loaded
        the row to resolve the account and ``compute_pulse_section`` loaded it
        again for the hero's staleness threshold.  This fragment is on the
        ``balanceChanged`` refresh path, so it re-renders on every settle.
        """
        with app.app_context():
            _seed_dashboard_owner(db, seed_user, seed_periods_today)

        with counting_calls(*_SECTION_RESOLUTION) as counts:
            resp = auth_client.get(
                "/dashboard/pulse", headers={"HX-Request": "true"},
            )

        assert resp.status_code == 200
        assert counts == {"resolve_grid_account": 1, "_get_user_settings": 1}, (
            f"the pulse fragment resolved its subject {counts}"
        )

    def test_the_subject_counter_sees_a_second_resolution(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """NEGATIVE CONTROL: the counter must be able to report a failure.

        Stated as a DIFFERENCE rather than against a literal, so it grades the
        instrument and only the instrument -- the same argument the two
        controls above it make.

        **The repeat goes through the MODULE ATTRIBUTE, not through a name this
        file imported**, and that is not style.  ``counting_calls`` patches
        every ``app.*`` module that holds the target, and a test module is not
        one -- so a direct ``from ... import resolve_grid_account`` here would
        call straight past the counter and this control would fail against a
        working instrument.  It did, on the first draft.
        """
        with app.app_context():
            _seed_dashboard_owner(db, seed_user, seed_periods_today)
            settings = seed_user["settings"]

        with counting_calls(*_SECTION_RESOLUTION) as render_only:
            assert auth_client.get("/").status_code == 200
        baseline = render_only["resolve_grid_account"]

        with counting_calls(*_SECTION_RESOLUTION) as render_plus_one:
            assert auth_client.get("/").status_code == 200
            with app.app_context():
                account_resolver.resolve_grid_account(
                    seed_user["user"].id, settings,
                )

        assert render_plus_one["resolve_grid_account"] == baseline + 1, (
            "the resolution counter did not see a deliberately repeated "
            "resolve, so the assertions beside it grade nothing"
        )


class TestOneLoadPerRender:
    """A render loads its inputs once and derives each plan once.

    Plan step **C2-f2d-2**, ledger row **P57**.
    """

    def test_retirement_render_loads_each_input_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /retirement runs each loader exactly once.

        Two producers, one load.  Before this step the verdict producer and the
        lever solver each ran the whole set: the gap inputs (a settings, a
        pension and a salary query plus a full paycheck-engine run), the account
        query behind the projection context, and the batch (deductions,
        contributions, params, and a balance fold per account).
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        with counting_calls(*_ONCE_PER_RENDER) as counts:
            resp = auth_client.get("/retirement")

        assert resp.status_code == 200
        assert counts == {name: 1 for _, name in _ONCE_PER_RENDER}, (
            f"/retirement ran its loaders {counts}; the route loads once and "
            "every producer below it takes what it was handed"
        )

    def test_the_page_derives_no_plan_twice(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The projection walk runs once per DISTINCT plan, never twice for one.

        The count is not 1 and must not be asserted as 1: the retire-later
        lever bisects over real candidate retirement dates and every one of
        them is a different plan that has to be projected.  What this pins is
        that no plan is projected twice -- so the number of walks equals the
        number of distinct :class:`~app.services.retirement_plan.PlanPoint`
        values the render asked about, which the memo makes observable.

        Asserting the walk count against the memo's SIZE rather than against a
        literal is deliberate: a literal would be the binary search's own step
        count restated, and it would have to be edited every time the seeded
        scenario's funding changed -- which is how a gate stops grading its
        subject and starts grading its fixture.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        # pylint: disable=import-outside-toplevel
        from app.services import retirement_levers, retirement_plan, retirement_readiness

        with app.app_context():
            inputs = retirement_plan.load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            with counting_calls(_PER_PLAN) as counts:
                picture = retirement_plan.picture_at(
                    inputs, inputs.stored_plan,
                )
                retirement_readiness.readiness_from_picture(picture)
                retirement_levers.compute_lever_data(inputs)

        walks = counts["project_accounts_with_batch"]
        assert walks == len(inputs.picture_memo), (
            f"the render walked the projection {walks} times for "
            f"{len(inputs.picture_memo)} distinct plans; a plan derived twice "
            "is two independent answers to one question"
        )
        # And the readiness hero's plan is ONE of them, not a private extra:
        # the lever card's month-0 probe is that same object.
        assert inputs.picture_memo[inputs.stored_plan] is picture

    def test_the_load_counter_sees_a_second_load(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """NEGATIVE CONTROL: the load counter must be able to report a failure.

        Same argument as :meth:`TestOneReadPassPerRender
        .test_the_counter_sees_a_second_pass`: a counter that never incremented
        would pass both assertions above, on this tree and on the tree that ran
        every loader twice alike.  Stated as a DIFFERENCE so it grades the
        instrument and not the render's cost.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        # pylint: disable=import-outside-toplevel
        from app.services import retirement_dashboard_service

        with counting_calls(*_ONCE_PER_RENDER) as render_only:
            assert auth_client.get("/retirement").status_code == 200
        baseline = render_only["load_gap_inputs"]

        with counting_calls(*_ONCE_PER_RENDER) as render_plus_one:
            assert auth_client.get("/retirement").status_code == 200
            with app.app_context():
                retirement_dashboard_service.load_gap_inputs(
                    BalanceContext.build(seed_user["user"].id),
                )

        assert render_plus_one["load_gap_inputs"] == baseline + 1, (
            "the call counter did not see a deliberately repeated load, so "
            "the assertions beside it grade nothing"
        )


class TestOneCalendarDerivationPerRender:
    """Every render below derives the owner's pay calendar exactly ONCE.

    **A read pass memoizes the calendar, so this ought to be the pass count
    restated -- and for three renders it was not** (pay-calendar plan step
    C2-f2d-3, found by that step's adversarial design review).  Measured
    through the routes over an owner with a salary profile and three
    investment accounts: ``/savings`` derived it SEVEN times and
    ``/retirement`` SIX, against ONE read pass each, because
    ``income_service.get_current_gross_biweekly`` derived its own from a
    ``user_id`` while every chain reaching it already held the pass.  The
    count grew with the account count, since the seam asks for a
    per-account contribution feed.  *That producer was DELETED at plan step
    salary:R14-b; the measurement is kept as the reason this second count
    exists, not as a live claim about a live function.*

    **This is a SECOND count of the same question and that is the point.**
    ``TestOneReadPassPerRender`` proves the render opens one pass; it cannot
    see a producer that holds the pass and derives anyway, because opening
    nothing is exactly what such a producer does.

    **``/`` joined this class at pay-calendar plan step C2-f2e**, which closed
    ledger row **P61**.  It was excluded on the ground that it opened TWO
    passes -- one per producer -- and so derived twice by construction, where
    pinning 2 would have pinned the defect.  The route opens the one pass now
    and both producers take it, so the budget dashboard is graded here like
    every other render, and its two HTMX fragments with it.
    """

    def test_savings_derives_the_calendar_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /savings derives the owner's pay calendar exactly once."""
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        with counting_calls(_CALENDAR_DOOR) as counts:
            resp = auth_client.get("/savings")

        assert resp.status_code == 200
        assert counts["derive_periods"] == 1, (
            f"/savings derived the pay calendar {counts['derive_periods']} "
            "times; the route opens one read pass and every producer below it "
            "must read that pass's memo"
        )

    def test_retirement_derives_the_calendar_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /retirement derives the owner's pay calendar exactly once."""
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        with counting_calls(_CALENDAR_DOOR) as counts:
            resp = auth_client.get("/retirement")

        assert resp.status_code == 200
        assert counts["derive_periods"] == 1, (
            f"/retirement derived the pay calendar {counts['derive_periods']} "
            "times; see the class docstring"
        )

    def test_dashboard_derives_the_calendar_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET / derives the owner's pay calendar exactly once.

        Measured at **2** on the merge base (pay-calendar plan step C2-f2e's
        own baseline, taken over this fixture's owner): the pulse region
        derived one calendar and the position tracks another, because each
        producer opened its own read pass.

        The render is asserted to have actually PRODUCED both tiers, not merely
        to have returned 200 -- an owner with no goal and no loan renders an
        empty tracks tier, and a count taken over that render would read 1 on
        the broken tree as well as the fixed one.
        """
        with app.app_context():
            _seed_dashboard_owner(db, seed_user, seed_periods_today)

        with counting_calls(_CALENDAR_DOOR) as counts:
            resp = auth_client.get("/")

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Dashboard Emergency Fund" in body, (
            "the goal track did not render, so this count was taken over a "
            "producer that returned early"
        )
        assert "Debt" in body, (
            "the debt track did not render, so this count was taken over a "
            "producer that returned early"
        )
        assert counts["derive_periods"] == 1, (
            f"/ derived the pay calendar {counts['derive_periods']} times; the "
            "route opens one read pass and every producer below it must read "
            "that pass's memo"
        )

    def test_dashboard_fragments_derive_the_calendar_once_each(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Each dashboard HTMX fragment derives the pay calendar exactly once.

        **Only the ``/dashboard/balance`` half of this case can fail on the
        merge base, and it fails at 0 rather than at 2** (C2-f2e's adversarial
        design review, 2026-08-18): that fragment derived the calendar ZERO
        times there because it answered "which period is current" from
        ``pay_period_service.get_current_period`` -- SQL over the stored span,
        against its own ``date.today()``.  So this case pins a +1 the step
        deliberately INTRODUCED, not a defect it removed.  ``/dashboard/pulse``
        derived it once on both trees; that half is a pin.

        The +1 is the trade: the fragment is the anchor editor's revert target
        and swaps back into the pulse region, so both must name the same
        paycheck, and one derivation is what makes that structural rather than
        a coincidence of two queries agreeing.
        """
        with app.app_context():
            _seed_dashboard_owner(db, seed_user, seed_periods_today)

        for path in ("/dashboard/pulse", "/dashboard/balance"):
            with counting_calls(_CALENDAR_DOOR) as counts:
                resp = auth_client.get(path, headers={"HX-Request": "true"})

            assert resp.status_code == 200
            assert counts["derive_periods"] == 1, (
                f"{path} derived the pay calendar {counts['derive_periods']} "
                "times; see the class docstring"
            )

    def test_the_statements_render_derives_the_calendar_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /analytics/income-statement derives the pay calendar once.

        **It joined this class at pay-calendar plan step C2-f3a, and it is a
        PIN of a +1 rather than a count that came down** -- the same honesty
        the ``/dashboard/balance`` case beside it owes.  On the merge base this
        render derived ZERO calendars and issued FOUR statements against
        ``budget.pay_periods`` instead: ``get_current_period``,
        ``get_all_periods``, its own earliest-period query for the year list,
        and a ``db.session.get`` inside ``ledger_report_service`` for the
        heading.  One derivation replaced all four, so the number this asserts
        went UP while the reads went down.

        What it grades from here is the thing that actually goes wrong: a
        second producer wired onto this page and left to resolve its own
        schedule, which is how ``/retirement`` acquired two passes.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        with counting_calls(_CALENDAR_DOOR) as counts:
            resp = auth_client.get(
                "/analytics/income-statement",
                headers={"HX-Request": "true"},
            )

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Income Statement" in body, (
            "the statement did not render, so this count was taken over a "
            "producer that returned early"
        )
        assert "<option value=" in body, (
            "the pay-period selector did not render, so the reader whose "
            "derivation this counts never ran"
        )
        assert counts["derive_periods"] == 1, (
            f"/analytics/income-statement derived the pay calendar "
            f"{counts['derive_periods']} times; the route derives one and "
            "threads it into the window defaults, the selector, the year "
            "list and the report"
        )

    def test_the_cash_detail_page_derives_the_calendar_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /accounts/<id>/details derives the pay calendar exactly once.

        **It joined this class at pay-calendar plan step C4-a-2**, which is the
        step that gave this page a SECOND consumer of the owner's paydays: the
        reconcile panel now dates every row it offers against the derived span
        and scopes its three arms by the calendar's own period ids.  The route
        already held a memoized one on its read pass, and the panel takes that
        value rather than deriving its own -- but nothing GRADED that, so a
        later edit resolving a calendar inside ``reconcile_context`` would have
        been free.  This is the arm that stops it, and the class docstring's
        own history is why: ``/savings`` reached SEVEN derivations against one
        read pass exactly that way.

        The panel is asserted to have RENDERED, not merely the page to have
        returned 200 -- an account whose owner has never asserted a balance
        renders no panel at all, and a count taken over that render would read
        1 on a tree where the panel derived a second calendar.
        """
        account_id = seed_user["account"].id

        with counting_calls(_CALENDAR_DOOR) as counts:
            resp = auth_client.get(f"/accounts/{account_id}/details")

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert f'id="reconcile-panel-{account_id}"' in body, (
            "the reconcile panel did not render, so this count was taken over "
            "a page that never asked the calendar the panel's question"
        )
        assert counts["derive_periods"] == 1, (
            f"/accounts/<id>/details derived the pay calendar "
            f"{counts['derive_periods']} times; the route opens one read pass "
            "and the reconcile panel must take that pass's memoized calendar "
            "rather than resolving its own"
        )

    def test_the_transfer_create_form_derives_the_calendar_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /transfers/new derives the pay calendar once.

        Same shape and same C2-f3a pin: the form read ``get_all_periods`` for
        its start-period ``<select>`` and ``get_current_period`` to preselect
        one -- two reads of ``budget.pay_periods``, zero derivations -- where
        it now derives once and asks it both questions.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        with counting_calls(_CALENDAR_DOOR) as counts:
            resp = auth_client.get("/transfers/new")

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'id="start_period_id"' in body, (
            "the start-period selector did not render, so this count was "
            "taken over a form that never asked the calendar anything"
        )
        assert counts["derive_periods"] == 1, (
            f"/transfers/new derived the pay calendar "
            f"{counts['derive_periods']} times; the route derives one and asks "
            "it both the option set and the preselection"
        )

    def test_the_carry_forward_preview_derives_the_calendar_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET the carry-forward modal derives the pay calendar exactly once.

        **This is ledger row P68, and it is a count that came DOWN.**  Plan step
        C2-f3a replaced this route's ``pay_period_service.get_current_period``
        -- SQL, which derived nothing -- with the calendar's own
        ``period_containing``, so the render then held TWO derivations: the
        route's, and a second inside ``carry_forward_service``, which built a
        ``GenerationSchedule`` for the target period and that value loaded its
        own.  Measured on this fixture at 2 before pay-calendar plan step
        C2-f3c and 1 after.

        What it grades from here is the same thing every case in this class
        does: a producer wired below this route and left to resolve its own
        schedule.  The service takes the route's calendar now and can no longer
        build one, so the way back to 2 is a NEW producer, which is exactly
        what this catches.
        """
        with app.app_context():
            source_id = seed_periods_today[0].id

        with counting_calls(_CALENDAR_DOOR) as counts:
            resp = auth_client.get(
                f"/pay-periods/{source_id}/carry-forward-preview",
                headers={"HX-Request": "true"},
            )

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Carry Forward" in body, (
            "the modal did not render, so this count was taken over a route "
            "that returned before it reached the service"
        )
        assert counts["derive_periods"] == 1, (
            f"the carry-forward preview derived the pay calendar "
            f"{counts['derive_periods']} times; the route derives one and "
            "threads it into both period lookups and the generation seam"
        )

    def test_the_salary_profile_POST_derives_the_calendar_once(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """POST /salary derives the owner's pay calendar exactly once.

        **The only WRITE in this class, and it is here because a write path
        answers the same question a render does.**  ``create_profile`` asks the
        schedule three things -- the opening payday its every-paycheck rule
        starts on, the paycheck count its annual salary is divided by, and the
        reference period the net-pay recompute prices -- and before
        pay-calendar plan step C2-f3c it derived TWO calendars to do it: one in
        ``_paycheck_template`` and one inside the ``GenerationSchedule`` it
        built afterwards.  Two reads of one owner's schedule inside one POST,
        which a concurrent schedule write can separate.

        An adversarial review of that step found the fix ungraded: this class
        covered only GETs, so nothing would have noticed it coming back.

        **The POST and the edit page it redirects to are counted in two
        windows** (plan step salary:X-av-3a).  The edit page prices the pay
        list since that step, so it derives the calendar once of its own; one
        window over both read 2, the POST's one and the page's one, and could
        not tell that from a POST deriving twice.
        """
        with app.app_context():
            filing_status = (
                db.session.query(FilingStatus).filter_by(name="single").one()
            )
            status_id = filing_status.id
            first_payday = seed_periods[0].start_date.isoformat()

        with counting_calls(_CALENDAR_DOOR) as counts:
            resp = auth_client.post("/salary", data={
                "name": "Arch Job",
                "pay_amount": "2884.62",  # $75,000.00 a year / 26
                "pay_payday": first_payday,
                **stub_period_on(first_payday),
                "filing_status_id": status_id,
                "state_code": "NC",
            })

        assert resp.status_code == 302
        with app.app_context():
            created = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=seed_user["user"].id, name="Arch Job")
                .one_or_none()
            )
            assert created is not None, (
                "the profile was not created, so this count was taken over a "
                "POST that returned before it reached the schedule"
            )
            assert created.template is not None, (
                "no template was linked, so the half of the route this counts "
                "never ran"
            )
        assert counts["derive_periods"] == 1, (
            f"POST /salary derived the pay calendar {counts['derive_periods']} "
            f"times; the route derives one and threads it into the template's "
            f"opening bound, its per-paycheck amount and the generate pass"
        )

        with counting_calls(_CALENDAR_DOOR) as page_counts:
            page = auth_client.get(resp.headers["Location"])

        assert page.status_code == 200
        assert "Arch Job" in page.get_data(as_text=True), (
            "the redirect did not land on the new profile's edit page"
        )
        assert page_counts["derive_periods"] == 1, (
            f"the salary edit page derived the pay calendar "
            f"{page_counts['derive_periods']} times; it derives one, for the "
            f"pay list it prices"
        )

    def test_the_count_grows_with_the_account_set_when_a_producer_derives(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """NEGATIVE CONTROL: a per-account derivation would be VISIBLE here.

        The regression these two cases exist for scaled with the owner's
        investment-account count, so the control adds accounts and requires the
        count NOT to move.  A counter that never incremented, or a fixture whose
        producers sat behind an early return, would pass the two assertions
        above on the tree that derived seven times -- which is what happened.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)
            for index in range(2):
                make_investment_account(
                    seed_user, db.session, seed_periods_today[0],
                    Decimal("50000.00"), name=f"extra-401k-{index}",
                )
            db.session.commit()

        with counting_calls(_CALENDAR_DOOR) as counts:
            resp = auth_client.get("/savings")

        assert resp.status_code == 200
        assert counts["derive_periods"] == 1, (
            f"/savings derived the pay calendar {counts['derive_periods']} times "
            "for three investment accounts, so a producer is deriving PER "
            "ACCOUNT rather than reading the read pass's memo"
        )

    @pytest.mark.parametrize("path", ["/grid", "/dashboard"])
    def test_a_rolling_owner_derives_TWICE_and_that_is_the_bound(
        self, app, db, auth_client, seed_user, seed_periods_today, path,
    ):
        """With rolling ON, ``/grid`` and ``/dashboard`` derive exactly TWICE.

        **A pin of a +1 plan step C4 introduced, and the case this whole class
        could not see until it did.**  The rolling top-up counts the owner's
        remaining paychecks, and it runs BEFORE the route opens its read pass
        -- deliberately, so that pass sees any rows the top-up creates.  It
        therefore cannot share the pass's calendar and derives its own.  Before
        C4 it counted ``PayPeriod.end_date >= as_of`` in SQL and derived
        nothing; the column it counted is one plan step C4-c dropped.

        **TWO is the bound, not an aspiration.**  A third would mean a producer
        below the pass resolving its own schedule again, which is what every
        other case here grades; a second top-up derivation would mean the
        deficit path re-deriving on a render that writes nothing.

        **Every other case in this class runs with rolling OFF** -- the column
        server-defaults to false -- so none of them covers this, and none of
        them would have caught the +1 either: the counter named the LOADER
        ``calendar_for`` until this step, and the top-up's door is a different
        one.  Two blindnesses, one render.

        Args:
            path: The render to count, as a URL.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            pay_schedule_service.set_rolling(
                user_id, enabled=True, target_periods=1,
            )
            db.session.commit()

        with counting_calls(_CALENDAR_DOOR) as counts:
            resp = auth_client.get(path)

        assert resp.status_code == 200
        assert counts["derive_periods"] == 2, (
            f"{path} derived the pay calendar "
            f"{counts['derive_periods']} times for a rolling owner; the "
            "top-up derives one (it runs before the pass) and the read pass "
            "derives the other, so anything above two is a producer below "
            "the pass resolving its own schedule"
        )

    @pytest.mark.parametrize("path", [
        "/transactions/new/quick",
        "/transactions/new/full",
        "/transactions/empty-cell",
    ])
    def test_the_grid_cell_fragments_derive_the_calendar_once(
        self, app, db, auth_client, seed_user, seed_periods_today, path,
    ):
        """Each grid empty-cell fragment derives the pay calendar exactly once.

        **A pin of a +1 this step deliberately introduced, not a count that
        came down** -- the same honesty ``/dashboard/balance`` and the income
        statement above owe.  Before pay-calendar plan step **C2-f3e** these
        three derived ZERO calendars and proved the submitted ``period_id``
        with a primary-key ``db.session.get(PayPeriod, ...)`` plus a
        ``row.user_id != current_user.id`` comparison.  They ask the owner's
        own calendar now, so an id another user holds is simply absent and the
        comparison is gone; the price is this one derivation.

        What it grades from here is what every case in this class grades: a
        second producer wired below one of these routes and left to resolve
        its own schedule.  They share ONE resolver
        (``routes/transactions/forms._resolve_grid_cell``), so a second
        derivation reaching any of the three reaches all three.

        The body assertion is not decoration: a refused cell answers 404 with
        no calendar derived at all, which would satisfy a bare ``== 1`` only by
        accident of the number, so each case pins that the fragment it is
        counting actually rendered.
        """
        with app.app_context():
            args = {
                "category_id": seed_user["categories"]["Groceries"].id,
                "period_id": seed_periods_today[3].id,
                "account_id": seed_user["account"].id,
                "transaction_type_id": ref_cache.txn_type_id(
                    TxnTypeEnum.EXPENSE,
                ),
            }

        with counting_calls(_CALENDAR_DOOR) as counts:
            resp = auth_client.get(
                path, query_string=args, headers={"HX-Request": "true"},
            )

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert str(seed_periods_today[3].id) in body, (
            f"{path} did not render the cell it was asked for, so this count "
            "was taken over a fragment that refused before it built anything"
        )
        assert counts["derive_periods"] == 1, (
            f"{path} derived the pay calendar {counts['derive_periods']} times; "
            "the three cell fragments share one resolver and it derives one"
        )


class TestOnePaycheckProjectionPerProfilePerRender:
    """Every render below runs the paycheck ENGINE once per salary profile.

    **The third count of one question, and the one the other two were blind
    to** (plan step **salary:R14-b**).  ``TestOneReadPassPerRender`` proves a
    render opens one pass; ``TestOneCalendarDerivationPerRender`` proves every
    producer below it reads that pass's CALENDAR.  Neither can see a producer
    that holds the pass, reads its calendar, and then runs the paycheck engine
    over the owner's whole saved window again -- which is exactly what
    ``salary:R14-b`` introduced and what an adversarial review of that step
    measured: ``/savings`` ran ``calculate_paycheck`` **61** times on a
    3-account, 10-period fixture where the pre-step tree ran it once per
    account, because ``_contribution_inputs_for_account`` is the batch loader
    over a one-element set and each call re-projected the whole window.

    :class:`~app.services.income_service.ProfilePaychecks` is the door counted
    rather than ``calculate_paycheck`` for two reasons: it is the ONE spelling
    of a profile's projection (ledger row **N-443**, closed at
    ``salary:R14-a``), and the inner call's count is a property of the owner's
    schedule LENGTH, so counting it would make the assertion a function of the
    fixture's payday count rather than of the code under test.

    The budget is ONE per active salary profile: a profile's paychecks are a
    function of the profile, the calendar and the RAISE SET they are priced
    under -- the third since plan step salary:S3-f-1 -- and every render here
    prices the stored set alone, so a second pricer for the same profile is a
    memo that was missed.
    :meth:`~app.services.balance_at.BalanceContext.paychecks` is that memo.
    **The one legitimate second construction is a ``/retirement/readiness``
    request carrying a per-raise probe at a NON-stored year** (plan step
    salary:S3-f-2b): the probed set is a different function, so it is a
    pricer of its own, and :meth:`test_a_probed_raise_set_is_exactly_one_more_pricer`
    pins it at exactly two -- the stored set's and the probed set's -- while
    the same request carrying the STORED year stays at one.

    **Plan step salary:S3-d moved the door and NARROWED what this class has
    left to catch, and the narrowing is worth stating.**  It counted
    ``project_profile`` calls against a ``payroll_breakdowns`` dict passed as
    an OPTIONAL argument -- ``breakdowns=None`` meant "no memo", and two of
    that argument's four callers passed it inside the step that added it,
    which is what this class was written after.  Nothing is passed a dict now;
    a consumer is handed the pricer, so that particular hole is closed by the
    shape rather than by this assertion.

    **What it still catches is a SECOND pricer, and since plan step
    salary:C12 none exists on purpose.**  Until C12,
    :class:`~app.services.income_service.SalaryPricing` derived its own,
    because ``cash_ledger.amount_basis`` was built from an owner and a
    scenario alone and had nothing to hand it (ledger row **P63**); the basis
    is built OVER the pass's pricer now, so a render that prices a salary ROW
    through the amount model and reads a payroll FEED holds one pricer for
    both.  :meth:`test_savings_prices_a_salary_row_and_a_feed_through_ONE_pricer`
    is the case that reads both paths and is the one C12 turned from 2 to 1;
    the three renders above it read one path each and were 1 before.  This
    class stays: it is what keeps a second source from returning.
    """

    @staticmethod
    def _fund_the_account(db, seed_user, account):
        """Name the owner's profile as the account's funding job.

        Without the link (**R-SAL5**) the loader resolves no profile, projects
        nothing, and the count reads ZERO on the fixed tree and the broken one
        alike -- the arm-that-cannot-fail shape this file's own fixture
        docstring warns about one function up.  ``_seed_projecting_account``
        already created the profile; this is what makes it FUND something.
        """
        profile = (
            db.session.query(SalaryProfile)
            .filter_by(user_id=seed_user["user"].id, is_active=True)
            .first()
        )
        params = (
            db.session.query(InvestmentParams)
            .filter_by(account_id=account.id).one()
        )
        params.employer_contribution_type_id = (
            ref_cache.employer_contribution_type_id(
                EmployerContributionTypeEnum.FLAT_PERCENTAGE,
            )
        )
        params.employer_flat_percentage = Decimal("0.0500")
        params.salary_profile_id = profile.id
        db.session.commit()

    def test_savings_projects_each_profile_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /savings builds one paycheck pricer for the owner's profile."""
        with app.app_context():
            account = _seed_projecting_account(
                db, seed_user, seed_periods_today,
            )
            self._fund_the_account(db, seed_user, account)

        with counting_calls(_PROJECTION_DOOR) as counts:
            resp = auth_client.get("/savings")

        assert resp.status_code == 200
        assert counts["ProfilePaychecks"] == 1, (
            f"/savings built the owner's paycheck pricer "
            f"{counts['ProfilePaychecks']} times; every producer below the "
            "route's one read pass must read that pass's pricer"
        )

    def test_retirement_projects_each_profile_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /retirement builds one paycheck pricer for the profile.

        The third of the memo's call sites (``retirement_projection
        .load_projection_batch``), which an adversarial review noted this
        class did not cover even though the site is one of the three.
        """
        with app.app_context():
            account = _seed_projecting_account(
                db, seed_user, seed_periods_today,
            )
            self._fund_the_account(db, seed_user, account)

        with counting_calls(_PROJECTION_DOOR) as counts:
            resp = auth_client.get("/retirement")

        assert resp.status_code == 200
        assert counts["ProfilePaychecks"] == 1, (
            f"/retirement built the owner's paycheck pricer "
            f"{counts['ProfilePaychecks']} times; the page holds one read pass "
            "and its batch load and its seam reads must share that pass's "
            "pricer"
        )

    @staticmethod
    def _seed_a_salary_row(db, seed_user, seed_periods_today):
        """Give the owner's profile a definition and ONE generated paycheck row.

        What makes ``/grid`` price a salary row through the AMOUNT MODEL
        (``ctx.amounts()`` -> ``SalaryPricing.net_for``), which is the second
        of the two paths ledger row **P63** was about.  The row is the
        engine's own (:func:`~tests._test_helpers.generate_row_of`), derived
        and undated by hand, so the amount model dispatches it to the salary
        rule rather than to a stated figure.
        """
        # pylint: disable=import-outside-toplevel
        from app.models.transaction_template import TransactionTemplate

        profile = (
            db.session.query(SalaryProfile)
            .filter_by(user_id=seed_user["user"].id, is_active=True)
            .one()
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=next(iter(seed_user["categories"].values())).id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
            name="Paycheck",
        )
        db.session.add(template)
        db.session.flush()
        make_every_period_rule(db.session, template)
        profile.template_id = template.id
        db.session.flush()
        row = generate_row_of(template, seed_periods_today[0])
        db.session.commit()
        return row

    def test_savings_prices_a_salary_row_and_a_feed_through_ONE_pricer(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /savings builds ONE pricer when it reads BOTH paycheck paths.

        **The case plan step salary:C12 turned from 2 to 1** (ledger row
        **P63**), and the one the three cases above are blind to: each of
        them reads ONE path.  Here the owner's profile FUNDS an investment
        account, so the seam's payroll feed prices the profile through
        ``ctx.paychecks()``, AND the profile's definition has a generated row
        in the reported window, so the cash fold under the same render
        prices that row through the amount model (``ctx.amounts()``).  Until
        C12 the amount model built a pricer of its own over a calendar of its
        own (``cash_ledger.amount_basis`` took two ids and had nothing to
        hand it), so this count read 2 and the calendar count 2 -- measured
        2026-09-12 with ``ctx.amounts()`` mutated back to the pass-less
        constructor as the negative control.  The basis is built over the
        pass's pricer now, so both paths hit one memo.

        ``/savings`` and not ``/grid``, and the reason is recorded because
        the first draft chose the grid: the grid's cash view prices the
        salary row and never asks the feed for a paycheck, so it built ONE
        pricer on the broken tree too and the control could not fire.  The
        amount-model assertion is not decoration either: an owner whose
        profile has no generated row reads 1 on the broken tree, because the
        amount model never prices a paycheck and never builds anything -- so
        the case pins that ``salary_net_for`` ran, which is the path this
        count depends on.  (A first draft asserted the row's id appeared in
        the page body; ``/savings`` renders no transaction id, and a small
        integer is a substring of half a page, so that graded nothing -- an
        adversarial review of this step measured it.)
        """
        with app.app_context():
            account = _seed_projecting_account(
                db, seed_user, seed_periods_today,
            )
            self._fund_the_account(db, seed_user, account)
            self._seed_a_salary_row(db, seed_user, seed_periods_today)

        with counting_calls(_PROJECTION_DOOR) as counts, \
                counting_calls(_CALENDAR_DOOR) as calendars, \
                counting_calls(_SALARY_ROW_DOOR) as rows:
            resp = auth_client.get("/savings")

        assert resp.status_code == 200
        assert rows["salary_net_for"] >= 1, (
            "the amount model priced no salary row on this render, so the "
            "count below cannot see the second path this case exists for"
        )
        assert counts["ProfilePaychecks"] == 1, (
            f"/savings built the owner's paycheck pricer "
            f"{counts['ProfilePaychecks']} times while pricing a salary row "
            "AND a payroll feed; the amount model must read the pass's pricer "
            "(plan step salary:C12), not derive a second one"
        )
        assert calendars["derive_periods"] == 1, (
            f"/savings derived the pay calendar {calendars['derive_periods']} "
            "times; the amount model's pricer is the pass's, over the pass's "
            "one calendar"
        )

    def test_savings_prices_its_current_paycheck_through_the_pricer(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /savings builds the pricer even when the profile funds nothing.

        The ``/savings`` twin of the ``/retirement`` case below, for plan step
        salary:C12-b: with nothing funded the feed loader wires no profile and
        builds nothing, so the ONE pricer this render builds is the one the
        page's current pay is priced through.  On the tree before C12-b --
        a direct ``calculate_paycheck`` in ``_metrics`` -- this count read 0.
        The page prices the current pay on every full render, goal or not
        (the build has no early return ahead of it), so the goal below does
        not gate the count; it is what makes the page PUBLISH a figure off
        that paycheck, so a wrong one is visible on the surface and not only
        in this counter.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import GoalModeEnum, IncomeUnitEnum
        from app.models.savings_goal import SavingsGoal

        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)
            db.session.add(SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="One paycheck",
                goal_mode_id=ref_cache.goal_mode_id(
                    GoalModeEnum.INCOME_RELATIVE,
                ),
                income_unit_id=ref_cache.income_unit_id(
                    IncomeUnitEnum.PAYCHECKS,
                ),
                income_multiplier=Decimal("1.00"),
                is_active=True,
            ))
            db.session.commit()

        with counting_calls(_PROJECTION_DOOR) as counts:
            resp = auth_client.get("/savings")

        assert resp.status_code == 200
        assert counts["ProfilePaychecks"] == 1, (
            f"/savings built the owner's paycheck pricer "
            f"{counts['ProfilePaychecks']} times for a profile that funds no "
            "account; the page's current pay must be priced through the "
            "pass's pricer (plan step salary:C12-b), which is the one "
            "construction this render has left"
        )

    def test_retirement_prices_its_current_paycheck_through_the_pricer(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /retirement builds the pricer even when the profile funds nothing.

        **The case above cannot see plan step salary:S3-f-2a, and this one
        exists because it cannot.**  There the profile FUNDS the account, so
        the feed loader builds its pricer and the count reads 1 whether the
        verdict's current paycheck comes off that pricer or off a direct
        engine call beside it -- the direct call (``_compute_current_pay``,
        the door that priced without the calibration) constructed no
        ``ProfilePaychecks`` at all.  Here nothing funds the account, so the
        feed loader wires no profile and builds nothing: the ONE pricer this
        render builds is the one the current paycheck is priced through.  On
        the tree before S3-f-2a this count read 0.

        It is still within the budget the class states -- one per active
        profile -- and it is the direction the case above is blind to: a
        regression re-pricing the current paycheck outside the pass reads 0
        here and 1 there.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        with counting_calls(_PROJECTION_DOOR) as counts:
            resp = auth_client.get("/retirement")

        assert resp.status_code == 200
        assert counts["ProfilePaychecks"] == 1, (
            f"/retirement built the owner's paycheck pricer "
            f"{counts['ProfilePaychecks']} times for a profile that funds no "
            "account; the verdict's current paycheck must be priced through "
            "the pass's pricer (plan step salary:S3-f-2a), which is the one "
            "construction this render has left"
        )

    @staticmethod
    def _seed_forever_raise(db, seed_user):
        """One recurring 5% January raise from next year, believed forever."""
        # pylint: disable=import-outside-toplevel
        from app.utils.dates import display_today
        from tests._test_helpers import make_recurring_raise

        profile = (
            db.session.query(SalaryProfile)
            .filter_by(user_id=seed_user["user"].id, is_active=True)
            .first()
        )
        effective = display_today().year + 1
        row = make_recurring_raise(
            profile.id, db.session, effective_date=date(effective, 1, 1),
        )
        db.session.commit()
        return row.id, effective

    def test_a_probed_raise_set_is_exactly_one_more_pricer(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """A readiness refresh at a probed end year builds TWO pricers, not N.

        The rail's pair at the STORED answer (``none``, the row's own) is the
        stored plan and builds one pricer -- the probe cannot be told from a
        plain refresh.  At a NON-stored year the request derives the stored
        picture (the what-if panel's baseline) AND the probed one, and the
        probed set is a legitimate second pricer: the current paycheck, the
        payroll feed and every retire-later probe at that set must all read
        that ONE pricer, so the count is exactly two.  Three or more is a
        reader that built its own instead of asking the pass's memo.
        """
        with app.app_context():
            account = _seed_projecting_account(
                db, seed_user, seed_periods_today,
            )
            self._fund_the_account(db, seed_user, account)
            raise_id, effective = self._seed_forever_raise(db, seed_user)

        with counting_calls(_PROJECTION_DOOR) as stored_counts:
            resp = auth_client.get(
                "/retirement/readiness",
                query_string={
                    f"raise_end_mode_{raise_id}": "none",
                    f"raise_end_year_{raise_id}": "",
                },
                headers={"HX-Request": "true"},
            )
        assert resp.status_code == 200
        assert stored_counts["ProfilePaychecks"] == 1, (
            "the rail's pre-filled pair built a second pricer; a probe equal "
            "to the stored year must resolve to the stored plan"
        )

        with counting_calls(_PROJECTION_DOOR) as probed_counts:
            resp = auth_client.get(
                "/retirement/readiness",
                query_string={
                    f"raise_end_mode_{raise_id}": "year",
                    f"raise_end_year_{raise_id}": str(effective),
                },
                headers={"HX-Request": "true"},
            )
        assert resp.status_code == 200
        assert probed_counts["ProfilePaychecks"] == 2, (
            f"a probed readiness refresh built {probed_counts['ProfilePaychecks']} "
            "pricers; the stored set and the probed set are the only two"
        )

    def test_investment_projects_each_profile_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET the investment dashboard builds one pricer for the profile.

        The account carries an employer contribution and NAMES the job that
        funds it, so the feed actually prices a gross -- without the link the
        loader resolves no profile at all and the count would read 0 on the
        fixed tree and the broken one alike.
        """
        with app.app_context():
            account = _seed_projecting_account(
                db, seed_user, seed_periods_today,
            )
            self._fund_the_account(db, seed_user, account)
            account_id = account.id

        with counting_calls(_PROJECTION_DOOR) as counts:
            resp = auth_client.get(f"/accounts/{account_id}/investment")

        assert resp.status_code == 200
        assert "$" in resp.get_data(as_text=True), (
            "the dashboard did not render figures, so this count was taken "
            "over a producer that returned early"
        )
        assert counts["ProfilePaychecks"] == 1, (
            f"/investment built the owner's paycheck pricer "
            f"{counts['ProfilePaychecks']} times; the page holds one read pass "
            "and both its seam reads and its own feed load must share that "
            "pass's pricer"
        )
