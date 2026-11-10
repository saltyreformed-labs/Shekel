"""What one render opens: one read pass, and one resolution of the dashboard's subject."""

import pytest

from app.services import account_resolver, pay_schedule_service
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import calendar_for
from tests._test_helpers import counting_calls, counting_read_passes

from ._helpers import _seed_projecting_account, _seed_dashboard_owner


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
    def test_a_rolling_owner_opens_two_passes_and_that_is_the_bound(  # pylint: disable=too-many-arguments,too-many-positional-arguments
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

        Pylint: ``too-many-arguments`` / ``too-many-positional-arguments``
        (6/5) -- five fixtures, each read, and the one parametrized path.

        Args:
            app: The ``app`` fixture.
            db: The ``db`` fixture.
            auth_client: The signed-in owner's client.
            seed_user: The ``seed_user`` fixture.
            seed_periods_today: The ``seed_periods_today`` fixture; the
                rolling target is the owner's saved period count plus three.
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
