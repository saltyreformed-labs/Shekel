"""Balance-at seam tests: the read pass's reporting domain, rule resolution and calendar.

Split from ``tests/test_services/test_balance_at.py`` by ``tools/split`` (ruling R-BAL301).
"""

from dataclasses import replace
from datetime import date, timedelta

from app.models.pay_period import PayPeriod
from app.services import balance_at
from app.services.recurrence import build_transient_rule, reauthor_rule, recurrence_spec
from app.services.balance_at import BalanceContext
from tests._test_helpers import all_periods, make_expense_template


class TestTheReadPassOwnsTheReportingDomain:
    """Plan step **C2-c**: the seam reads its periods rather than taking them.

    All thirteen per-period seam entries used to take a ``periods`` argument,
    and all eight callers in ``app/`` filled it with the same value -- the
    owner's complete saved period set, read out of ``budget.pay_periods`` with
    its two DERIVED columns attached.  An argument every caller answers
    identically is not a contract; it is the one thing a caller can get wrong,
    and this seam has already measured that mistake at ``$150,000.00`` (a fold
    read against a window missing its own period, recorded on
    ``pay_calendar._derive.derive_periods``).

    So the domain moved onto the read pass, beside the as-of and the scenario
    it is pinned with, and these grade the two properties that move: it is
    derived ONCE per pass, and it is the owner's whole saved calendar.
    """

    def test_the_calendar_is_derived_once_per_pass(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The memo, asserted by IDENTITY rather than by counting queries.

        A pass answers many per-period questions -- the grid's column set, the
        cockpit's net-worth column, an account's cash map -- and each of them
        needs a period's bounds.  Re-deriving the calendar per producer is the
        redundant derivation this whole context object exists to remove
        (``_context``'s own docstring: eleven loan resolutions in one render).
        """
        with app.app_context():
            ctx = balance_at.BalanceContext.build(seed_user["user"].id)
            assert ctx.calendar() is ctx.calendar()

    def test_a_fresh_context_re_derives_it(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A memo whose lifetime is ONE read, not a cache to invalidate.

        The write paths record a payday and then re-render; they build a fresh
        context, which is why there is no invalidation class of bug here.  Pinned
        because "memo, not cache" is the property that makes that true.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            first = balance_at.BalanceContext.build(user_id).calendar()
            second = balance_at.BalanceContext.build(user_id).calendar()
            assert first is not second
            assert first == second

    def test_the_reported_window_is_every_saved_period_in_payday_order(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The domain: the owner's whole schedule, keyed by the real row ids."""
        with app.app_context():
            ctx = balance_at.BalanceContext.build(seed_user["user"].id)
            window = ctx.reported_periods()
            stored = all_periods(seed_user["user"].id)
            assert [period.period_id for period in window] == [
                period.id for period in
                sorted(stored, key=lambda row: row.start_date)
            ]

    def test_every_per_period_entry_answers_that_same_domain(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """One domain, so two entries in one render cannot report two column sets.

        The property the argument could break and the pass cannot: the cash
        map, the kind-correct map and the grid's columns are keyed by exactly
        the periods the pass reports, without any of them being told which.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            ctx = balance_at.BalanceContext.build(user_id)
            account = seed_user["account"]
            expected = [period.period_id for period in ctx.reported_periods()]

            assert list(balance_at.cash_balance_map(account, ctx)) == expected
            assert list(balance_at.balance_map(account, ctx)) == expected
            assert list(
                balance_at.grid_balance_view(account, ctx).columns
            ) == expected

    def test_an_owner_with_no_pay_periods_gets_empty_maps_not_an_error(
        self, app, db, seed_user,
    ):  # pylint: disable=unused-argument
        """A brand-new owner has no columns, which is an answer.

        Load-bearing since ``balance:X-ad-a`` stopped registration writing a
        bootstrap payday: such an owner reaches the grid on their first visit,
        and a raising domain would 500 that page.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            db.session.query(PayPeriod).filter_by(user_id=user_id).delete()
            db.session.commit()
            ctx = balance_at.BalanceContext.build(user_id)

            assert len(ctx.reported_periods()) == 0
            assert balance_at.cash_balance_map(seed_user["account"], ctx) == {}
            assert balance_at.grid_balance_view(
                seed_user["account"], ctx,
            ).columns == {}


class TestTheReadPassResolvesARuleByWhatItSays:
    """Plan step **R16-b-2**: the rule-resolution memo is keyed by the SPEC.

    ``resolved_recurrence_of`` collapses a pass's N resolutions of one rule to
    one.  A first cut keyed the memo by ``rule.id``, and the merge of plan
    step R7d-c-2 -- which has GENERATION read a rule through this memo --
    measured what that proxy costs: ``reauthor_rule`` rewrites a rule's
    columns IN PLACE, so a rule edited and regenerated on one pass was
    regenerated on its pre-edit cadence.  The key is now the derivation's
    actual input, the rule's spec, so a stale entry is unrepresentable.

    Both controls read the memo's IDENTITY (``is``): a hit hands back the
    stored object and a miss builds a new one, so identity is what tells the
    two apart without patching the resolver.
    """

    def test_a_rule_re_authored_on_one_pass_is_resolved_afresh(
        self, app, db, seed_user, seed_periods,
    ):
        """The write door moves the first occurrence; the same pass sees it.

        With the id key the second read returned the first read's object and
        ``after.starts_on`` still named the opening payday -- verified by
        restoring that key and watching this fail.
        """
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)
            rule = template.recurrence_rule
            before = ctx.resolved_recurrence_of(rule)
            assert before.starts_on == seed_periods[0].start_date, (
                "precondition: the every-period rule opens on the first payday"
            )
            reauthor_rule(
                rule,
                replace(
                    recurrence_spec(rule),
                    starts_on=seed_periods[1].start_date,
                ),
                ctx.calendar(),
            )
            db.session.flush()

            after = ctx.resolved_recurrence_of(rule)
            assert after is not before
            assert after.starts_on == seed_periods[1].start_date

            # Re-authored BACK to the first spec, the first entry is served:
            # the key is the value, not "anything but the id" -- a memo
            # bypassed entirely would pass the two assertions above and fail
            # this one.
            reauthor_rule(
                rule,
                replace(
                    recurrence_spec(rule),
                    starts_on=seed_periods[0].start_date,
                ),
                ctx.calendar(),
            )
            db.session.flush()
            assert ctx.resolved_recurrence_of(rule) is before

    def test_rules_stating_one_spec_share_one_resolution(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Two stored rules and a transient one, one spec, one entry.

        The resolver cannot tell two rules with the same spec apart, so
        neither does the memo; and a transient rule (``id`` ``None``) needs
        no special case, because its spec is the key like any other's.

        **Its third assertion was re-expressed at plan step
        ``pay_calendar:C18-a``** under ``CLAUDE.md`` rule 5, the developer
        confirming it 2026-09-22 ("Yes: same cadence, no floor"): since
        ruling **R-PC85** the resolved value carries its definition's BOOKS
        floor, and an owner-less transient rule has no definition, so it
        resolves to the owned value with no books day -- equal in every other
        field -- rather than to the same object.  The two OWNED rules, on one
        account, still share one value.
        """
        with app.app_context():
            first = make_expense_template(db.session, seed_user, name="Rent")
            second = make_expense_template(
                db.session, seed_user, name="Groceries", category_key="Groceries",
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)
            first_spec = recurrence_spec(first.recurrence_rule)
            assert first_spec == recurrence_spec(second.recurrence_rule), (
                "precondition: the two definitions author one spec"
            )
            transient = build_transient_rule(first_spec, ctx.calendar())
            assert transient.id is None, "precondition: unsaved"

            resolved = ctx.resolved_recurrence_of(first.recurrence_rule)
            assert ctx.resolved_recurrence_of(second.recurrence_rule) is resolved
            assert resolved.books_opened_on is not None, (
                "precondition: the owned rules carry their account's books"
            )
            assert ctx.resolved_recurrence_of(transient) == replace(
                resolved, books_opened_on=None,
            )


class TestTheReadPassProjectsOverOneCalendar:
    """Plan step **C2-e**: the FORWARD domain, read off the pass's calendar.

    ``reported_periods`` answers "which periods does the seam REPORT on";
    :meth:`~app.services.pay_calendar.PayCalendar.projection_axis` answers
    "which paychecks does a projection RUN over".  Both come off the ONE
    calendar this pass memoizes, so a render that reports and projects derives
    the owner's paydays once.

    The clamp and the refusals are the VALUE's, graded purely in
    ``test_pay_calendar_value.py``.  What needs a database is what these two
    grade: that a real seeded owner's axis is their real schedule, and that an
    owner with no paydays gets an empty one rather than a 500.
    """

    def test_the_axis_is_the_owners_real_schedule_projected_past_it(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Saved where the schedule reaches, projected at the owner's cadence."""
        with app.app_context():
            ctx = balance_at.BalanceContext.build(seed_user["user"].id)
            saved = ctx.reported_periods()
            axis = ctx.calendar().projection_axis(
                saved[0].start_date, saved[-1].end_date + timedelta(days=90),
            )
            assert list(axis)[:len(saved)] == list(saved)
            assert all(
                period.period_id is None for period in list(axis)[len(saved):]
            )
            assert axis[-1].end_date >= saved[-1].end_date + timedelta(days=90)

    def test_the_axis_and_the_reported_window_share_one_calendar(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """One derivation per pass, so the two domains cannot disagree."""
        with app.app_context():
            ctx = balance_at.BalanceContext.build(seed_user["user"].id)
            saved = ctx.reported_periods()
            assert list(ctx.calendar().projection_axis(
                saved[0].start_date, saved[-1].end_date,
            )) == list(saved)

    def test_an_owner_with_no_paydays_gets_an_empty_axis(
        self, app, db, seed_user,
    ):  # pylint: disable=unused-argument
        """Nothing to project FROM, so nothing is invented -- and no 500."""
        with app.app_context():
            user_id = seed_user["user"].id
            db.session.query(PayPeriod).filter_by(user_id=user_id).delete()
            db.session.commit()
            ctx = balance_at.BalanceContext.build(user_id)
            assert len(ctx.calendar().projection_axis(
                date(2026, 1, 1), date(2036, 1, 1),
            )) == 0
