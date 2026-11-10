"""What one render runs: each retirement input loaded once, and one walk per distinct plan."""

from app.services import (
    retirement_dashboard_service,
    retirement_levers,
    retirement_plan,
    retirement_readiness,
)
from app.services.balance_at import BalanceContext
from tests._test_helpers import counting_calls

from ._helpers import _seed_projecting_account


#: The producers plan step C2-f2d-2 collapsed, as ``(module path, attribute)``.
#: The three LOADS ran twice per page render and three times per fragment; the
#: WALK ran once more than the render had distinct plans to ask about.
_ONCE_PER_RENDER = (
    ("app.services.retirement_dashboard_service", "load_gap_inputs"),
    ("app.services.retirement_projection", "build_projection_context"),
    ("app.services.retirement_projection", "load_projection_batch"),
)


_PER_PLAN = ("app.services.retirement_projection", "project_accounts_with_batch")


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
        self, app, db, seed_user, seed_periods_today,
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
