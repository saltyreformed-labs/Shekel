"""The pulse hero: the as-of-today balance with its captions and flags, its identity with the
chart's first point, the cash-flow view whatever the grid account's kind, the producer's
degraded states, and one clock per render (plan step C2-f2e).
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import TxnTypeEnum
from app.services import cash_ledger, dashboard_service
from app.services.dashboard_service import _pulse
from app.services import balance_at
from app.services.balance_at import BalanceContext
from tests._test_helpers import (
    one_off_row_of,
    record_paydays_across_a_hole,
    rhythm_of,
    last_covered_day,
    account_never_asserted,
    add_anchor_history as _add_anchor_history,
    create_hysa_account,
    dashboard_section,
    make_investment_account,
    reassert_balance_on,
    set_default_grid_account,
    settle_instant_on,
)

from ._helpers import _CURRENT_IDX, _TODAY, _add_expense, _add_tracked_expense, _add_entry


# ── Hero: balance, captions, staleness, next paycheck ───────────────


class TestPulseHero:
    """The pulse hero block (as-of-today balance + captions + flags)."""

    def test_hero_carries_account_and_period(self, app, seed_user, seed_periods, db):
        """Hero carries the account name/id and the current period date range.

        The current period is seed_periods[5] (contains the frozen
        2026-03-20); its start_date is 2026-03-13 and end_date 2026-03-26.
        """
        with app.app_context():
            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            hero = result["hero"]
            assert hero["account_id"] == seed_user["account"].id
            assert hero["account_name"] == seed_user["account"].name
            assert hero["period_start_date"] == seed_periods[_CURRENT_IDX].start_date
            assert hero["period_start_date"] == date(2026, 3, 13)
            assert hero["period_end_date"] == last_covered_day(seed_periods[_CURRENT_IDX])
            assert hero["period_end_date"] == date(2026, 3, 26)

    def test_hero_balance_is_the_current_periods_end(
        self, app, seed_user, seed_periods, db,
    ):
        """Hero balance is the current period's END, which is what it says.

        ``_pulse.html`` labels ``#balance-display`` "End of this period", so the
        figure is the hero's own period map at the current period -- and thus IS
        the chart's first point rather than a second producer beside it.  It read
        the as-of-TODAY scalar until plan step X-c2b2 (finding N-60): that was
        legitimate only while the scalar was period-FLAT, and the fold made it
        date-precise, so the two now differ by the whole unpaid remainder of the
        current period.

        With the seed account anchored at $1,000.00 and no transactions the two
        coincide at exactly the anchor, $1,000.00 -- which is why this fixture
        could not have caught the divergence and the label is what settles it.
        """
        with app.app_context():
            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result["hero"]["balance"] == Decimal("1000.00")

    def test_hero_next_paycheck_date_normal(self, app, seed_user, seed_periods, db):
        """next_paycheck_date is the first period starting after today.

        Today is 2026-03-20 (in period 5).  The first period whose
        start_date > today is period 6 (starts 2026-03-27).
        """
        with app.app_context():
            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert (
                result["hero"]["next_paycheck_date"]
                == seed_periods[_CURRENT_IDX + 1].start_date
            )
            assert result["hero"]["next_paycheck_date"] == date(2026, 3, 27)

    def test_next_paycheck_date_none_when_no_future_period(
        self, app, seed_user, db,
    ):
        """No period follows the current one -> next_paycheck_date is None.

        **Asked through the PRODUCER since plan step C2-f1**, where it used to
        call a private ``_next_paycheck_date`` helper that issued its own
        "first period starting after ``date.today()``" query.  That helper is
        deleted: the same render already resolved "the period after the
        current one" for the still-due panel, and the card shows both figures,
        so two queries could put two different paydays on one card.

        The state is built so today is inside the LAST period rather than past
        every period: six paydays from 2026-01-02 at cadence 14 puts the frozen
        2026-03-20 inside 2026-03-13 .. 2026-03-26, which is the last one.
        (``seed_user`` carries a 2024 bootstrap payday too, so the calendar
        holds seven; only the LAST one decides this.)  The
        old fixture (periods entirely in the past) could not reach the producer
        at all -- it answers ``None`` outright with no current period -- so
        this grades the rendered value where that one graded a helper.
        """
        with app.app_context():
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=6,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result is not None
            assert result["hero"]["next_paycheck_date"] is None
            # The still-due panel reads the SAME value, which is the point of
            # threading it: both are ``None`` together or neither is.
            assert result["still_due"]["next_period_start"] is None
            assert result["still_due"]["next_period_end"] is None

    def test_hero_staleness_fresh(self, app, seed_user, seed_periods, db):
        """A recently-updated anchor is NOT stale.

        Default staleness threshold is 14 days; an anchor updated 5 days
        ago is fresh (5 <= 14), so is_stale is False.
        """
        with app.app_context():
            _add_anchor_history(
                db.session, seed_user["account"],
                "1000.00", days_ago=5,
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result["hero"]["is_stale"] is False
            assert result["hero"]["last_updated_date"] is not None

    def test_hero_staleness_stale(self, app, seed_user, seed_periods, db):
        """An anchor older than the threshold IS stale.

        The seeded account asserts on the owner's bootstrap day, years
        before the 20-days-ago row appended here, so that row is the latest.
        20 > 14, so is_stale is True.

        *It used to clear the origination row first; the table is append-only
        since plan step X-f3c-2c and the row is dated years back rather than at
        NOW, so neither the act nor its reason survives.*
        """
        with app.app_context():
            account = seed_user["account"]
            _add_anchor_history(
                db.session, account, "1000.00", days_ago=20,
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result["hero"]["is_stale"] is True

    def test_hero_staleness_never_set(self, app, seed_user, seed_periods, db):
        """A never-set anchor IS stale, with a None last_updated_date.

        The Commit-3 invariant guarantees every account has an origination
        anchor row, so the truly-empty-history state hard-raises in the
        resolver and cannot reach the hero's balance call in production.
        The never-set staleness branch is therefore exercised one call below
        the hero: with the origination row deleted,
        ``cash_ledger.reconciled_through`` answers ``None`` and
        ``_anchor_is_stale`` treats that as stale (the user must set the
        balance).

        **It grades the PRODUCTION accessor since plan step X-f1c3a.**  It
        used to call ``_last_anchor_update_date``, a private wrapper with no
        production caller at all -- so the branch it pinned was reached by
        nothing, and the ``created_at``-keyed helper underneath it (ruling
        R-EP deleted that too) was never exercised on this path.
        """
        with app.app_context():
            # **Built rather than emptied** (plan step X-f3c-2c): an assertion
            # is append-only at the database tier, so an account that has
            # asserted nothing is one the assertion factory never touched.
            account = account_never_asserted(
                seed_user, db.session, name="Silent",
                opening_equity=Decimal("0.00"),
            )
            db.session.commit()

            last_observed = cash_ledger.reconciled_through(
                account.id,
            ).observed_day
            assert last_observed is None
            assert _pulse._anchor_is_stale(
                last_observed, seed_user["settings"], _TODAY,
            ) is True


# ── The hero == first chart point identity ──────────────────────────


class TestHeroChartIdentity:
    """The hero balance IS the chart's first point, and the revert agrees.

    Both read the current period's entry in the SAME folded period map since
    plan step X-c2b2, so their equality is structural rather than a
    coincidence of reservation semantics -- which is why the assertion that
    still earns its keep is the one against
    ``dashboard_service.compute_balance_section``: the anchor-editor's revert
    fragment renders the same ``#balance-display`` control from a SEPARATE
    producer call, and that is the pair that can still drift.

    (Before the cutover the hero was the as-of-TODAY scalar and the chart's
    first point the period-END balance.  They coincided only because that
    scalar was period-FLAT -- it summed the whole period's projected rows
    whatever their dates -- so the identity this class was written for was
    finding cash D2 holding the two together.  The template labels the figure
    "End of this period"; the fold made the scalar date-precise, so the hero
    moved to the date its own label promises.)
    """

    def test_hero_equals_first_chart_point_no_post_dated_entries(
        self, app, seed_user, seed_periods, db,
    ):
        """Hero balance == chart points[0].balance, and == the revert fragment.

        Seed account anchored $1,000.00.  Add a $300.00 projected expense
        and a $1,200.00 projected income in the current period, plus a
        tracked envelope with an entry dated BEFORE today.  The hero and the
        chart's first point are one map entry; the revert fragment reaches
        the same date through its own producer call, so all three must agree.
        """
        with app.app_context():
            current = seed_periods[_CURRENT_IDX]
            _add_expense(
                db.session, seed_user, current, "Rent", "300.00",
                due_date=date(2026, 3, 18),
            )
            one_off_row_of(
                current,
                name="Paycheck",
                amount=Decimal("1200.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
            )
            tracked = _add_tracked_expense(
                db.session, seed_user, current, "Groceries", "150.00",
            )
            _add_entry(
                db.session, seed_user, tracked, "40.00", date(2026, 3, 17),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            hero_balance = result["hero"]["balance"]
            first_point = result["chart"]["points"][0]["balance"]
            assert hero_balance == first_point
            # The anchor-editor's revert fragment renders this same control
            # from its own producer call -- the pair that can still drift.
            assert dashboard_service.compute_balance_section(
                dashboard_section(seed_user["user"].id),
            )["hero"]["balance"] == hero_balance
            # And the first chart point is the current period's end date.
            assert (
                result["chart"]["points"][0]["end_date"]
                == last_covered_day(current)
            )


# ── Cash-flow view for an any-kind grid account (seam reroute lock) ──


class TestPulseCashFlowViewForAnyKindGridAccount:
    """The pulse reads the CASH-FLOW view regardless of the grid account's kind.

    Regression lock for the Level-1 ``balance_at`` seam reroute.
    ``resolve_grid_account`` may return ANY kind -- a user can point the
    dashboard at an HYSA, or the fallback can land on a loan / investment --
    but the pulse is the spending-account runway, so the chart, the "lowest
    point ahead" trough, and the hero must show the PURE TRANSACTION running
    balance, never the kind-correct balance that accrues interest (HYSA),
    amortizes (loan), or compounds (investment).  The reroute briefly wired
    the chart to ``balance_map`` and the hero to the kind-correct
    ``balance_at`` scalar; every prior pulse test used a PLAIN checking
    account, where the cash-flow and kind-correct views coincide, so the
    divergence went uncaught.  These cases pin a non-cash grid account so a
    regression to the kind-correct entries fails loudly.
    """

    def test_hysa_grid_account_chart_and_trough_show_cash_not_interest(
        self, app, seed_user, seed_periods, db,
    ):
        """An HYSA grid account's chart + trough are cash, not accrued interest.

        Make a fresh HYSA (5% APY, daily) the user's default grid account,
        anchored $50,000.00 at ``seed_periods[0]`` (2026-01-02) with NO
        transactions, so the pure-cash running balance is a FLAT $50,000.00 at
        every forward period (anchor carried forward; no interest, no rows).
        The kind-correct map -- what the bug rendered -- compounds 5% APY
        across the ~2.5 months to the current period, so it reads STRICTLY
        ABOVE $50,000.00.  The chart and trough must show the flat cash value;
        had they kept the kind-correct map, the inflated "lowest point ahead"
        would hide a real future dip below zero.
        """
        with app.app_context():
            hysa = create_hysa_account(
                seed_user, db.session, seed_periods[0], Decimal("50000.00"),
            )
            set_default_grid_account(
                db.session, seed_user["user"].id, hysa.id,
            )

            scenario = seed_user["scenario"]
            bctx = BalanceContext.build(seed_user["user"].id)
            current = seed_periods[_CURRENT_IDX]
            cash = balance_at.cash_balance_map(hysa, bctx)
            accrued = balance_at.balance_map(hysa, bctx)

            # The cash truth is hand-computable: anchor carried flat, no rows,
            # no interest -> $50,000.00 at the current period.
            assert cash[current.id] == Decimal("50000.00")
            # Divergence is real (non-vacuous): 5% APY daily over the ~2.5
            # months from the period-0 anchor to the current period accrues
            # interest, so the kind-correct map exceeds the flat cash carry.
            assert accrued[current.id] > Decimal("50000.00")

            section = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )

            # Every charted point is the flat cash value, never the accrued
            # one (a revert to ``balance_map`` would make these == accrued).
            points_by_date = {
                p["end_date"]: p["balance"] for p in section["chart"]["points"]
            }
            for period in seed_periods[_CURRENT_IDX:]:
                assert points_by_date[last_covered_day(period)] == Decimal("50000.00")
                assert points_by_date[last_covered_day(period)] != accrued[period.id]

            # The "lowest point ahead" is the flat cash $50,000.00, not the
            # interest-inflated minimum the kind-correct map would have shown.
            assert section["trough"]["balance"] == Decimal("50000.00")

    def test_investment_grid_account_hero_shows_cash_not_modeled(
        self, app, seed_user, seed_periods, db,
    ):
        """An investment grid account's hero is the cash carry, not modeled growth.

        Make a 401(k) (7% assumed return) the user's default grid account,
        anchored $100,000.00 at ``seed_periods[0]`` with NO contributions, so
        the pure-cash running balance to today is a FLAT $100,000.00.  The
        kind-correct ``balance_at`` scalar -- what the bug's hero used --
        compounds that forward, reading STRICTLY ABOVE $100,000.00 at the
        current period.  The hero must show the cash carry (the runway view),
        matching the chart and the grid for the same account.
        """
        with app.app_context():
            inv = make_investment_account(
                seed_user, db.session, seed_periods[0], Decimal("100000.00"),
            )
            set_default_grid_account(
                db.session, seed_user["user"].id, inv.id,
            )

            scenario = seed_user["scenario"]
            bctx = BalanceContext.build(seed_user["user"].id)
            # The kind-correct scalar (the bug's hero) compounds the anchor
            # forward; assert the divergence is real before locking the fix.
            modeled = balance_at.balance_at(inv, bctx, _TODAY)
            assert modeled > Decimal("100000.00")

            section = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            # The hero is the cash carry: anchor $100,000.00 carried flat to
            # today with no contributions -> $100,000.00, not the modeled value.
            assert section["hero"]["balance"] == Decimal("100000.00")
            assert section["hero"]["balance"] != modeled


# ── compute_pulse_section degraded states ───────────────────────────


class TestPulseSectionDegraded:
    """The pulse producer's None contract for the degraded states."""

    def test_no_current_period_returns_none(self, app, seed_user):
        """No period contains today -> None.

        ``seed_user`` (no ``seed_periods``) has only the 2024 bootstrap period,
        so no SAVED period contains the pass's day, ``containing_index``
        answers ``None`` and the producer short-circuits.  That reader was
        ``pay_period_service.get_current_period`` until pay-calendar plan step
        C2-f2e; the state is the same one and the door is not.
        """
        with app.app_context():
            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result is None

    def test_has_all_region_keys_when_populated(
        self, app, seed_user, seed_periods, db,
    ):
        """A populated pulse section carries exactly the eight region keys."""
        with app.app_context():
            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert set(result.keys()) == {
                "hero", "chart", "trough", "peak",
                "still_due", "street", "due_soon", "due_soon_stations",
            }


# ── One clock and one period per render (plan step C2-f2e) ─────────


# **``TestPeriodIsDerivedNotStored`` was deleted whole at plan step
# ``pay_calendar:C4-c``, and it is worth saying what it did rather than
# leaving a gap.**  Its one case rewrote period 5's stored ``end_date`` to
# the day before the frozen today and asserted the hero region still
# rendered -- where the pre-cutover query matched
# ``start_date <= today <= end_date`` against that column, found no period
# covering today, and blanked the region to a "No pay period covers today"
# CTA for an owner mid-paycheck.  It was plan step C2-f2e's grade.
#
# C4-c dropped the column, so the disagreement it planted is not merely
# unreachable, it is SILENT: assigning to an attribute the model no longer
# maps sets a plain Python attribute, writes no UPDATE, and survives
# ``expire_all`` -- the *born dead* shape ``docs/plans/lessons.md`` names.
# The case and its own premise assertion both went on passing while
# measuring nothing, so it is deleted rather than left green.
#
# The class had documented that only ONE of the disagreement's two
# directions was covered, the other needing hand-edited or legacy rows
# (ledger row **P35**'s owner).  Neither direction exists now: there is one
# span, computed on every read.  The hero's period is graded on ordinary
# schedules by the classes above.


class TestOneClockPerRender:
    """Every day-sensitive figure in the region reads the READ PASS's day.

    Three of them resolved ``date.today()`` for themselves before pay-calendar
    plan step **C2-f2e** -- the hero's staleness count, the street band's
    "Today" marker, and each bill's ``days_until_due`` -- so a render crossing
    midnight could count staleness against one day, place the marker on the
    next, and label a bill against either.  Ledger row **P55** names the class.

    These cases pin the day by PASSING one, which is also what makes them
    honest all year: they assert a figure MOVES with the pass's ``as_of``, so a
    producer that quietly re-read the wall clock would answer the frozen
    fixture day and fail.  A test that only asserted "the marker is where today
    is" would pass on both trees.

    **They could not be written against the merge base at all**, and that is
    the finding rather than a gap in the evidence: ``compute_pulse_section``
    took a ``user_id`` and built its own read pass, so no caller had a day to
    pin.  What CAN be graded on both sides is the period itself, which
    ``TestPeriodIsDerivedNotStored`` did until plan step ``pay_calendar:C4-c``
    deleted it (the note above this class says why) -- run against
    ``5ab457b7`` it failed with the whole region ``None``.
    """

    def test_the_street_marker_follows_the_pass_day(
        self, app, seed_user, seed_periods, db,
    ):
        """The "Today" marker moves with the pass's ``as_of``, not the wall clock.

        Period 5 runs 2026-03-13 .. 2026-03-26.  A pass pinned to 2026-03-13
        puts the marker at offset 0 and a pass pinned to 2026-03-20 at offset
        7 -- ``(2026-03-20 - 2026-03-13).days``.  A producer reading its own
        ``date.today()`` would answer 7 for both, the suite's frozen day.
        """
        with app.app_context():
            opening = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id, as_of=date(2026, 3, 13)),
            )
            later = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id, as_of=_TODAY),
            )

            assert opening["street"]["today_offset"] == 0
            assert later["street"]["today_offset"] == 7
            # Both passes are inside ONE period, so the band itself did not move.
            assert opening["street"]["days_total"] == 13
            assert later["street"]["days_total"] == 13

    def test_days_until_due_counts_from_the_pass_day(
        self, app, seed_user, seed_periods, db,
    ):
        """A bill's ``days_until_due`` counts from the pass's day.

        A $50.00 bill due 2026-03-24 in the current period: 11 days out from a
        pass pinned to 2026-03-13, 4 days out from one pinned to 2026-03-20.
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Water Bill", "50.00", due_date=date(2026, 3, 24),
            )
            db.session.commit()

            def _water(as_of):
                section = dashboard_service.compute_pulse_section(
                    dashboard_section(seed_user["user"].id, as_of=as_of),
                )
                return next(
                    bill for bill in section["due_soon"]
                    if bill["name"] == "Water Bill"
                )

            assert _water(date(2026, 3, 13))["days_until_due"] == 11
            assert _water(_TODAY)["days_until_due"] == 4

    def test_anchor_staleness_counts_from_the_pass_day(
        self, app, seed_user, seed_periods, db,
    ):
        """The hero's ``is_stale`` flag measures from the pass's day.

        The account is asserted on 2026-01-02 here.  The seeded one carries
        only its ORIGINATION assertion, dated on the bootstrap day before the
        calendar (plan step X-f3c-2c), so the day this case turns on is one
        the case states rather than one it inherits.  The default threshold is
        14 days.  A pass pinned to 2026-01-10 is 8 days out and
        NOT stale; the frozen 2026-03-20 is 77 days out and stale.  The
        threshold sits between the two, so the flag has to flip -- which a
        producer reading its own clock could not do.
        """
        with app.app_context():
            reassert_balance_on(
                db.session, seed_user["account"],
                settle_instant_on(date(2026, 1, 2)),
            )
            db.session.commit()
            observed = cash_ledger.reconciled_through(
                seed_user["account"].id,
            ).observed_day
            assert observed == date(2026, 1, 2)
            assert seed_user["settings"].anchor_staleness_days == 14

            fresh = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id, as_of=date(2026, 1, 10)),
            )
            stale = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id, as_of=_TODAY),
            )

            assert fresh["hero"]["is_stale"] is False
            assert stale["hero"]["is_stale"] is True
