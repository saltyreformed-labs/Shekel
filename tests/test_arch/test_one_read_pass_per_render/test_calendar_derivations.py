"""What one render, or one salary write, derives: the owner's pay calendar, exactly once."""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import TxnTypeEnum
from app.models.ref import FilingStatus
from app.models.salary_pay_entry import SalaryPayEntry
from app.models.salary_profile import SalaryProfile
from app.services import pay_schedule_service
from tests._stub_period import stub_period_on
from tests._form_controls import rendered_form_controls
from tests._test_helpers import (
    counting_calls,
    freeze_today,
    make_investment_account,
)

from ._helpers import _CALENDAR_DOOR, _seed_projecting_account, _seed_dashboard_owner

#: The regeneration's engine call, as ``(module path, attribute)``.  Counted
#: beside the calendar so a salary write's case proves the regeneration it
#: grades ran inside the window (task plan#3106): a profile with no template,
#: or an owner with no baseline scenario, returns before this call and derives
#: no calendar there, so a count of one would be true of a regeneration that
#: never ran.
_REGENERATION_DOOR = ("app.services.recurrence_engine", "regenerate_for_template")


def _create_form(db, seed_periods):
    """Return the create form's fields for a job paid $2,884.62 from the first saved payday.

    Read inside an app context: the filing status is looked up by its row.

    Args:
        db: The ``db`` fixture.
        seed_periods: The ``seed_periods`` fixture; its first payday, 01-02,
            is the pay's payday and the stub's Pay Date, six days after its
            Period End (``stub_period_on``).
    """
    filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
    first_payday = seed_periods[0].start_date.isoformat()
    return {
        "name": "Arch Job",
        "pay_amount": "2884.62",  # $75,000.00 a year / 26
        "pay_payday": first_payday,
        **stub_period_on(first_payday),
        "filing_status_id": filing_status.id,
        "state_code": "NC",
    }


def _created_profile_id(app, db, auth_client, seed_user, seed_periods):
    """Create the job through POST /salary, outside any counting window, and return its id.

    Through the create door rather than a builder, because a salary write's
    regeneration needs the paycheck template only that door links.

    Args:
        app: The ``app`` fixture.
        db: The ``db`` fixture.
        auth_client: The signed-in owner's client.
        seed_user: The ``seed_user`` fixture.
        seed_periods: The ``seed_periods`` fixture.
    """
    with app.app_context():
        form = _create_form(db, seed_periods)
    assert auth_client.post("/salary", data=form).status_code == 302
    with app.app_context():
        return db.session.query(SalaryProfile).filter_by(
            user_id=seed_user["user"].id, name="Arch Job",
        ).one().id


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

    @pytest.mark.usefixtures("seed_periods_today")
    def test_the_cash_detail_page_derives_the_calendar_once(
        self, auth_client, seed_user,
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
        self, app, auth_client, seed_periods_today,
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

    def test_the_salary_profile_post_derives_the_calendar_once(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """POST /salary derives the owner's pay calendar exactly once.

        **The first WRITE in this class, and it is here because a write path
        answers the same question a render does.**  ``create_profile`` asks the
        schedule several things -- today whether the stub's Pay Date and the
        first pay entry's payday are paydays, the opening payday its
        every-paycheck rule starts on, and the periods its generate pass writes
        into -- and before pay-calendar plan step C2-f3c it derived TWO
        calendars for what it asked then: one in ``_paycheck_template`` and one
        inside the ``GenerationSchedule`` it built afterwards.  Two reads of
        one owner's schedule inside one POST, which a concurrent schedule write
        can separate.  The update and Fix doors joined it at task plan#3106.

        An adversarial review of that step found the fix ungraded: this class
        covered only GETs, so nothing would have noticed it coming back.

        **The POST and the edit page it redirects to are counted in two
        windows** (plan step salary:X-av-3a).  The edit page prices the pay
        list since that step, so it derives the calendar once of its own; one
        window over both read 2, the POST's one and the page's one, and could
        not tell that from a POST deriving twice.
        """
        with app.app_context():
            form = _create_form(db, seed_periods)

        with counting_calls(_CALENDAR_DOOR) as counts:
            resp = auth_client.post("/salary", data=form)

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
            f"times; the route derives one and threads it into the two payday "
            f"checks, the template's opening bound and the generate pass"
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

    def test_the_salary_update_post_derives_the_calendar_once(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, app, db, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """POST /salary/<id> with a stub's two dates derives the pay calendar once.

        Task plan#3106.  ``update_profile`` turns a posted Pay Date and Period
        End into the days the job's pay period ends before payday (plan#3048's
        task plan#3099), asking the owner's calendar whether the Pay Date is a
        payday, and the regeneration after it walks the same calendar.  The
        door derived its own and the regeneration's pass a second: measured at
        **2** on this case before the task.  The door builds one pass and hands
        it to both.

        The POST is the edit form as the page renders it, with a new Period
        End, so the half of the door that reads the calendar runs.  The gap
        moving from 6 to 3, and the regeneration being reached once inside the
        window, are asserted beside the count: a door that refused, or a
        regeneration that returned early, derives one calendar too.

        Pylint: ``too-many-arguments`` / ``too-many-positional-arguments``
        (6/5) -- six fixtures, each read: the job is created through the
        signed-in client from the saved calendar on a frozen day, and read back
        through the session.
        """
        freeze_today(monkeypatch, date(2026, 3, 20))
        profile_id = _created_profile_id(app, db, auth_client, seed_user, seed_periods)
        page = auth_client.get(f"/salary/{profile_id}/edit")
        controls = rendered_form_controls(
            page.get_data(as_text=True), f"/salary/{profile_id}",
        )
        # On 03-20 the form shows back the 03-13 payday and its Period End,
        # 03-07; 03-13 - 03-10 = a gap of 3.
        assert controls["stub_pay_date"] == ["2026-03-13"]
        controls["stub_period_end"] = ["2026-03-10"]

        with counting_calls(_CALENDAR_DOOR, _REGENERATION_DOOR) as counts:
            resp = auth_client.post(f"/salary/{profile_id}", data=controls)

        assert resp.status_code == 302
        with app.app_context():
            db.session.expire_all()
            assert db.session.get(
                SalaryProfile, profile_id,
            ).period_end_days_before_payday == 3, (
                "the posted pair did not become the gap, so the door refused "
                "before the half of it this counts"
            )
        assert counts["regenerate_for_template"] == 1, (
            "the regeneration did not run inside the window, so this count "
            "never saw the second pass it would build"
        )
        assert counts["derive_periods"] == 1, (
            f"POST /salary/<id> derived the pay calendar "
            f"{counts['derive_periods']} times; the door builds one read pass "
            f"and hands it to the gap's payday check and the regeneration"
        )

    def test_the_pay_entry_fix_post_derives_the_calendar_once(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, app, db, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """POST /salary/pay/<id>/fix moving an entry's payday derives the calendar once.

        Task plan#3106, the update door's shape at the Fix door (plan step
        salary:X-av-3a): ``fix_entry`` asks the owner's calendar whether a
        CHANGED payday is a payday, and the regeneration after it walks the
        same calendar.  Measured at **2** on this case before the task, one
        pass for ``fix_entry`` and one inside the regeneration; the door builds
        one and hands it to both.

        The payday is moved, from 01-02 to 01-16, because an unchanged payday
        is not asked again (ruling R-SAL53's shape) and a Fix of the amount
        alone derives only the regeneration's calendar.  The entry moving and
        the regeneration being reached once are asserted beside the count.

        Pylint: ``too-many-arguments`` / ``too-many-positional-arguments``
        (6/5) -- six fixtures, each read, as in the update door's case above.
        """
        freeze_today(monkeypatch, date(2026, 3, 20))
        profile_id = _created_profile_id(app, db, auth_client, seed_user, seed_periods)
        with app.app_context():
            entry = db.session.get(SalaryProfile, profile_id).pay_entries[0]
            entry_id = entry.id
            assert entry.payday == date(2026, 1, 2)
        page = auth_client.get(f"/salary/{profile_id}/edit")
        controls = rendered_form_controls(
            page.get_data(as_text=True), f"/salary/pay/{entry_id}/fix",
        )
        controls["payday"] = ["2026-01-16"]

        with counting_calls(_CALENDAR_DOOR, _REGENERATION_DOOR) as counts:
            resp = auth_client.post(f"/salary/pay/{entry_id}/fix", data=controls)

        assert resp.status_code == 302
        with app.app_context():
            db.session.expire_all()
            assert db.session.get(SalaryPayEntry, entry_id).payday == date(2026, 1, 16), (
                "the entry did not move, so the door refused before the half "
                "of it this counts"
            )
        assert counts["regenerate_for_template"] == 1, (
            "the regeneration did not run inside the window, so this count "
            "never saw the second pass it would build"
        )
        assert counts["derive_periods"] == 1, (
            f"POST /salary/pay/<id>/fix derived the pay calendar "
            f"{counts['derive_periods']} times; the door builds one read pass "
            f"and hands it to the payday check and the regeneration"
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
    @pytest.mark.usefixtures("seed_periods_today")
    def test_a_rolling_owner_derives_twice_and_that_is_the_bound(
        self, app, db, auth_client, seed_user, path,
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
            app: The ``app`` fixture.
            db: The ``db`` fixture.
            auth_client: The signed-in owner's client.
            seed_user: The ``seed_user`` fixture.
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
        self, app, auth_client, seed_user, seed_periods_today, path,
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
