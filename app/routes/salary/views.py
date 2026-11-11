"""
Shekel Budget App -- Salary route package: projection view + breakdown stubs.

The full-width multi-period salary projection table (a first-class sibling
of the cockpit), plus the retired per-period breakdown URLs, which now
redirect into the cockpit (the paycheck anatomy lives there).  The old
breakdown pages were folded into the cockpit during the Fable 5 salary
rebuild; the endpoint names are preserved as ownership-checked redirect
stubs so existing bookmarks and in-app links keep resolving.
"""

from datetime import date

from flask import abort, redirect, render_template, url_for
from flask_login import current_user

from app.utils.auth_helpers import get_or_404, require_owner
from app.models.salary_profile import SalaryProfile
from app.services import salary_cockpit_service
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import calendar_for
from app.routes.salary._bp import salary_bp
from app.routes.salary._helpers import _get_owned_profile_and_period


@salary_bp.route("/salary/<int:profile_id>/breakdown/<int:period_id>")
@require_owner
def breakdown(profile_id, period_id):
    """Redirect the retired per-period breakdown page to the cockpit.

    Ownership of BOTH the profile and the period is verified before the
    302 so a cross-user id 404s here rather than leaking existence through
    an intermediate redirect (the project's "404 for both 'not found' and
    'not yours'" rule; account-detail precedent).  The cockpit focuses the
    requested profile and period via its ``?profile=&period=`` params.
    """
    profile, period = _get_owned_profile_and_period(
        profile_id, period_id, calendar_for(current_user.id),
    )
    return redirect(url_for(
        "salary.cockpit", profile=profile.id, period=period.period_id,
    ))


@salary_bp.route("/salary/<int:profile_id>/breakdown")
@require_owner
def breakdown_current(profile_id):
    """Redirect the retired current-period breakdown to the cockpit.

    Verifies ownership of ``profile_id`` before redirecting so a
    cross-user request 404s here rather than producing a 302 that leaks
    the existence of the requested profile-id slot (audit commit C-31 /
    F-087).  Focuses the current period when one exists; when the user has
    no pay periods the redirect carries only the profile and the cockpit
    shows its generate-periods blocker.
    """
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    current_period = calendar_for(current_user.id).period_containing(
        date.today(),
    )
    if current_period is None:
        return redirect(url_for("salary.cockpit", profile=profile.id))
    return redirect(url_for(
        "salary.cockpit", profile=profile.id, period=current_period.period_id,
    ))


@salary_bp.route("/salary/<int:profile_id>/projection")
@require_owner
def projection(profile_id):
    """Show salary projection table for all periods."""
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    # The render's ONE read pass, so this page's calendar and its paychecks
    # come off the same derivation (plan step salary:S3-d).  It called
    # ``calendar_for`` directly until then, which is a second derivation of
    # the fact the pass memoizes and left no pricer for the projection to
    # share.  **It COSTS one ``get_baseline_scenario`` query on a page that
    # reads no balance**, and that is the price of the pricer having exactly
    # one source per render rather than a second one built here; said out
    # loud because a cost nobody wrote down is one nobody can revisit.
    # ONE clock for the page, threaded into the pass rather than read twice
    # (ledger row **P55**'s family; ``cockpit`` beside this does the same).
    # ``BalanceContext.build`` defaults ``as_of`` to ``date.today()``, so
    # letting it default while the summary below read its own would put two
    # clock reads in one render.
    today = date.today()
    ctx = BalanceContext.build(current_user.id, as_of=today)
    periods = ctx.calendar().saved()
    # ONE spelling of the projection, shared with the cockpit and with the
    # amount model's own derivation (plan step salary:R14-a, ledger row
    # N-443).  This site wrote the tax-config resolution +
    # ``project_salary`` pair out longhand, as did the other two, over the
    # same calendar; the per-period-year tax resolution (DH-#30) and the
    # profile's pay stubs, which price its taxes since plan step
    # salary:S11-c-2c, live in that one producer now.
    breakdowns = ctx.paychecks().for_profile(profile).over(periods)

    # Pair periods with breakdowns.  The ledger badges and tints each row
    # whose paycheck carries a banner: the engine badges a raise on the one
    # paycheck it first reaches (ruling plan#3092), so the row is the step.
    projection_data = list(zip(periods, breakdowns))

    # Summary framing above the ledger (restyled in P3): the next raise,
    # the next third paycheck, and the per-calendar-year net totals.  All
    # Decimal, derived from the same breakdowns the table renders (DRY).
    # The clock is the PASS's, read once at the top of this function.  It was
    # read here as a second ``date.today()`` until plan step salary:S3-d gave
    # this page a read pass, so a render crossing midnight could answer "the
    # next raise" from one day and "the next third paycheck" from the next --
    # the defect pay-calendar plan step C2-f2d-3 fixed on the cockpit, in the
    # same package and outside ledger row **P55**'s census, which is scoped to
    # ``app/services/**`` and cannot see a ROUTE.
    projection_summary = {
        "next_raise": salary_cockpit_service.next_raise_after(
            projection_data, today,
        ),
        "next_third": salary_cockpit_service.next_third_after(
            projection_data, today,
        ),
        "yearly_nets": salary_cockpit_service.yearly_net_totals(projection_data),
    }

    return render_template(
        "salary/projection.html",
        profile=profile,
        projection_data=projection_data,
        projection_summary=projection_summary,
    )
