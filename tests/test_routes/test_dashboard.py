"""
Shekel Budget App -- Dashboard Route Tests (Terminal Road rebuild, Loop B)

Tests the rebuilt dashboard page and its HTMX endpoints:

  * ``dashboard.page`` -- the pulse region (hero money, still-due totals,
    the projected-end-balance chart JSON, the STREET band's per-bill
    events + "anytime this period" shelf) plus the page-load-only position
    tracks; the degraded states (no account; no pay period covers today).
  * ``dashboard.pulse_section`` -- the ``balanceChanged`` swap target:
    the pulse partial for HTMX requests, a redirect otherwise.
  * ``dashboard.balance_section`` -- the anchor-edit revert fragment
    (``#balance-display``); the partial for HTMX, a redirect otherwise.

Route tests assert response CONTENT, not just status (testing rules).
The retired summary cards (alerts, payday, cash runway, the two-period
bills list, the savings-goal / debt cards) and their route tests were
removed in the same pass -- a sanctioned removal of developer-ruled
features (``docs/design/dashboard_card_audit.md`` "Retirements"), not
test-gaming.

The separate "Due Soon" list was likewise REMOVED by developer ruling
(``docs/design/dashboard_card_audit.md`` "Rebuild decisions" anatomy item
3, locked 2026-06-12: "it's all information that is better handled on the
grid").  The STREET band in ``_pulse.html`` is now the only per-bill
surface -- dated rows as events on the day-by-day axis, undated rows on
the "anytime this period" shelf -- and the still-due chip caption link
carries the next-period total ("next period $X . view in grid", or a
generate link when no next period exists).  The assertions below were
re-pointed at those surviving surfaces under that ruling (the sanctioned
rule-5 exception), not weakened.
"""

from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.utils.dates import add_months, display_today
from tests._test_helpers import (
    one_off_row_of,
    figure_source_columns,
    last_covered_day,
    add_anchor_history as _add_anchor_history,
    add_txn as _add_txn,
    current_pay_period,
    generate_row_of,
    make_expense_template,
)
from app.models.amount_ownership import AmountOwnership


# ── Auth ─────────────────────────────────────────────────────────────


class TestDashboardAuth:
    """Authentication requirements for the dashboard and its endpoints."""

    def test_dashboard_requires_auth(self, app, client):
        """GET /dashboard redirects unauthenticated users to login."""
        with app.app_context():
            resp = client.get("/dashboard")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_root_requires_auth(self, app, client):
        """GET / redirects unauthenticated users to login."""
        with app.app_context():
            resp = client.get("/")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_pulse_section_requires_auth(self, app, client):
        """GET /dashboard/pulse unauthenticated -> login redirect."""
        with app.app_context():
            resp = client.get("/dashboard/pulse")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_balance_section_requires_auth(self, app, client):
        """GET /dashboard/balance unauthenticated -> login redirect."""
        with app.app_context():
            resp = client.get("/dashboard/balance")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]


# ── Page rendering: the pulse region ────────────────────────────────


class TestDashboardPulseRendering:
    """The rebuilt page renders the pulse region (canvas / street / chips)."""

    def test_root_serves_dashboard(self, app, auth_client, seed_user, seed_periods_today):
        """GET / returns 200 with the pulse hero label."""
        with app.app_context():
            resp = auth_client.get("/")
            assert resp.status_code == 200
            assert b"End of this period" in resp.data

    def test_dashboard_url_still_works(self, app, auth_client, seed_user, seed_periods_today):
        """GET /dashboard returns 200 with the same pulse content.

        Re-pointed off the removed "Due Soon" header (audit "Rebuild
        decisions" anatomy item 3) to the surviving street-band head, which
        labels the day-by-day per-bill surface.
        """
        with app.app_context():
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert b"This period, day by day" in resp.data

    def test_root_does_not_contain_grid(self, app, auth_client, seed_user, seed_periods_today):
        """GET / does NOT contain grid-specific content."""
        with app.app_context():
            resp = auth_client.get("/")
            assert resp.status_code == 200
            assert b"grid-table" not in resp.data

    def test_dashboard_has_open_grid_link(self, app, auth_client, seed_user, seed_periods_today):
        """The pulse sky carries an 'Open Grid' link to the working surface."""
        with app.app_context():
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert b"Open Grid" in resp.data

    def test_hero_money_string_renders(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """The hero shows the as-of-today balance via the money macro.

        seed_user's account is anchored at $1,000.00 with no transactions,
        so the as-of-today projected balance is exactly $1,000.00, rendered
        ``$1,000.00`` by the shared money macro.
        """
        with app.app_context():
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The click-to-edit hero control carries the figure.
            assert 'id="balance-display"' in html
            assert "$1,000.00" in html

    def test_still_due_total_renders(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """The still-due-this-period total renders the floored remaining sum.

        Current period bills (B4a remaining basis, transfers included):
          * untracked Rent $300.00 -> contributes effective $300.00.
        Still-due total = $300.00, rendered ``$300.00``.
        """
        with app.app_context():
            cur = current_pay_period(seed_user["user"].id)
            _add_txn(
                db.session, seed_user, cur, "Rent", "300.00",
                due_date=cur.start_date + timedelta(days=2),
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Still due this period" in html
            # The total appears in the sky chip and the street head -- the
            # period's single still-due figure.  (The "Due Soon" header that
            # also carried it was removed: audit "Rebuild decisions" anatomy
            # item 3.)
            assert "$300.00" in html

    def test_still_due_floors_over_budget_envelope_and_includes_transfer(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """Still-due = floored-remaining for tracked rows + transfer included.

        Hand-computed current-period total (B4a remaining floored at zero,
        B4b transfers included):
          * untracked Rent $300.00          -> $300.00
          * over-budget envelope ($100 est,  -> $0.00 (floored, not -$30)
            $130 entries)
          * projected transfer-out $200.00   -> $200.00 (included)
        Total = 300.00 + 0.00 + 200.00 = $500.00.
        """
        # pylint: disable=import-outside-toplevel
        from app.models.ref import AccountType
        from app.models.transaction_entry import TransactionEntry
        from app.services import account_service, transfer_service

        with app.app_context():
            cur = current_pay_period(seed_user["user"].id)

            _add_txn(
                db.session, seed_user, cur, "Rent", "300.00",
                due_date=cur.start_date + timedelta(days=2),
            )

            # Over-budget tracked envelope: $100 budget, $130 spent.  The
            # definition's own row (the engine's, plan step balance:X-cf-4),
            # due on the paycheck's start as an every-paycheck row is.
            template = make_expense_template(
                db.session, seed_user, amount="100.00", name="Groceries",
                category_key="Groceries", is_envelope=True,
            )
            envelope = generate_row_of(template, cur)
            db.session.add(TransactionEntry(
                **figure_source_columns(),
                transaction_id=envelope.id, account_id=envelope.account_id, owner_id=envelope.user_id,
                user_id=seed_user["user"].id,
                amount=Decimal("130.00"),
                description="overspend",
                purchased_on=cur.start_date + timedelta(days=1),
            ))

            # Projected transfer-out from checking -> savings, $200.00.
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Sweep",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(savings)
            db.session.flush()
            transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=cur.id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("200.00")),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=None,
                    due_date=cur.start_date + timedelta(days=4),
                ),
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            # 300.00 + 0.00 (floored) + 200.00 = 500.00.
            assert "$500.00" in html

    def test_chart_json_present_and_first_value_equals_hero(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """data-chart JSON is present; its first value equals the hero figure.

        With no transactions, the as-of-today hero ($1,000.00) equals the
        current period's projected end balance, which is the chart's first
        point by construction.  The route serializes that to a float, so
        the chart's first value is ``1000.0``.
        """
        # pylint: disable=import-outside-toplevel
        import html as html_mod
        import json
        import re

        with app.app_context():
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The canvas carries the serialized chart contract.  Jinja
            # HTML-escapes the JSON string into the attribute, so unescape
            # before parsing (the established chart-JSON route-test pattern).
            match = re.search(r"data-chart='([^']*)'", html)
            assert match is not None, "data-chart attribute missing"
            chart = json.loads(html_mod.unescape(match.group(1)))
            assert chart["labels"], "chart has no labels"
            assert chart["values"], "chart has no values"
            # First value == hero == $1,000.00 -> 1000.0 (float boundary).
            assert chart["values"][0] == 1000.0
            # The hero money string is on the page for the same figure.
            assert "$1,000.00" in html

    def test_due_soon_rows_render_with_dual_entry_amount(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """Dated rows render as STREET events; a tracked row shows dual amount.

        A tracked envelope ($500 budget, $200 entries) renders its dual
        ``$200.00 / $500.00`` amount, and a plain bill renders its single
        amount.  Re-pointed off the removed Due Soon list (audit "Rebuild
        decisions" anatomy item 3) to the STREET band, which now carries
        the per-bill rows via the SAME money-macro output: both bills are
        dated, so both render as street events.
        """
        # pylint: disable=import-outside-toplevel
        from app.models.transaction_entry import TransactionEntry

        with app.app_context():
            cur = current_pay_period(seed_user["user"].id)
            _add_txn(
                db.session, seed_user, cur, "Rent", "1200.00",
                due_date=cur.start_date + timedelta(days=1),
            )
            # The tracked definition's own row (the engine's, plan step
            # balance:X-cf-4): dated on the paycheck's start, so a STREET
            # event like the Rent beside it.
            template = make_expense_template(
                db.session, seed_user, amount="500.00", name="Groceries",
                category_key="Groceries", is_envelope=True,
            )
            tracked = generate_row_of(template, cur)
            db.session.add(TransactionEntry(
                **figure_source_columns(),
                transaction_id=tracked.id, account_id=tracked.account_id, owner_id=tracked.user_id,
                user_id=seed_user["user"].id,
                amount=Decimal("200.00"),
                description="groceries",
                purchased_on=cur.start_date + timedelta(days=1),
            ))
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Rent" in html
            assert "Groceries" in html
            # Plain bill single amount; tracked bill dual amount -- both in
            # the STREET event labels.
            assert "$1,200.00" in html
            assert "$200.00 / $500.00" in html

    def test_due_soon_excludes_settled_bill(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """A settled (done) expense renders no STREET event -- it is paid.

        Re-pointed off the removed Due Soon list (audit "Rebuild decisions"
        anatomy item 3): a paid row drops out of the unpaid-rows query that
        feeds the street, so its name never reaches the response.
        """
        with app.app_context():
            cur = current_pay_period(seed_user["user"].id)
            _add_txn(
                db.session, seed_user, cur, "Already Paid", "500.00",
                status_enum=StatusEnum.DONE, settled_amount="500.00",
                due_date=cur.start_date,
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert "Already Paid" not in resp.data.decode()

    def test_due_soon_empty_state(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """No current-period bills -> page renders, $0.00 totals, no events.

        Re-pointed under the audit's "Rebuild decisions" anatomy item 3:
        the removed Due Soon list took its "No upcoming bills" empty-state
        copy with it.  What is true now is asserted instead -- the page
        renders, the still-due total floors to $0.00 (sky chip + street
        head), and the STREET renders NO per-bill events (the state-suffixed
        ``street__event--`` class is the discriminator; the always-present
        Today marker and period-end station use other classes) and no
        "anytime this period" shelf.
        """
        with app.app_context():
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Still due this period" in html
            assert "$0.00" in html
            # No dated bill events and no undated shelf with zero due rows.
            assert "street__event--" not in html
            assert "Anytime this period" not in html

    def test_peak_chip_renders_highest_point_ahead(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """The peak chip renders 'Highest point ahead' + money + grid link.

        A $1,200.00 projected income two periods out (offset 2) lifts that
        period's projected end balance -- and every period from it forward,
        which carries the windfall -- to 1,000.00 + 1,200.00 = $2,200.00,
        the highest point in the otherwise-flat horizon.  The peak is the
        FIRST period to reach it (offset 2; the earlier flat periods stay
        at 1,000.00).  The peak chip mirrors the trough: the label
        'Highest point ahead', the $2,200.00 money string, and the grid
        deep-link at the peak's offset (``/grid?offset=2`` -- a distinct
        offset no other chip emits, so the link proves the PEAK chip
        rendered).

        NOTE: the peak chip template markup is being added in parallel by
        the design session.  Until that lands this test FAILS (the producer
        already supplies ``pulse.peak``); the failure is expected-pending-
        template, not a producer defect.
        """
        with app.app_context():
            # POSITIONAL, not resolved through the app's own search (plan step
            # C2-f1).  ``seed_periods_today`` puts today in index 4; the peak
            # chip's ``offset=2`` is what this asserts, so a fixture that
            # picked its period with ``period_starting_after`` -- the same
            # search the producer walks -- would move WITH a defect in it and
            # the test would still pass.  Never a producer as its own oracle
            # (``docs/plans/verification.md`` #2).
            cur = seed_periods_today[4]
            assert cur.start_date <= date.today() <= last_covered_day(cur)
            nxt = seed_periods_today[5]
            peak_period = seed_periods_today[6]
            one_off_row_of(
                peak_period,
                name="Windfall",
                amount=Decimal("1200.00"),
                user_id=peak_period.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Highest point ahead" in html
            # 1,000.00 anchor + 1,200.00 income = $2,200.00.
            assert "$2,200.00" in html
            # The peak's grid deep-link at offset 2 (distinct from any other
            # chip's link), proving the PEAK chip rendered it.
            assert 'href="/grid?offset=2"' in html

    def test_next_period_total_renders_in_chip_caption_link(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """The next-period still-due total is the still-due chip caption link.

        Re-pointed off the removed Due Soon section's next-period footer
        line (audit "Rebuild decisions" anatomy item 3, consequence (a):
        the next-period TOTAL moves into the still-due chip as a caption
        link to ``grid.index?offset=1``).  A $175.00 projected bill in the
        NEXT period appears only as that caption total -- its row lives on
        the grid -- so the chip reads "next period $175.00 . view in grid"
        and links to ``/grid?offset=1``.
        """
        with app.app_context():
            # POSITIONAL -- see the peak test above for why the fixture does
            # not ask the search this asserts on.
            cur = seed_periods_today[4]
            assert cur.start_date <= date.today() <= last_covered_day(cur)
            nxt = seed_periods_today[5]
            _add_txn(
                db.session, seed_user, nxt, "Next Period Bill", "175.00",
                due_date=nxt.start_date + timedelta(days=1),
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The chip caption link carries the next-period total and the
            # grid deep-link (the template renders lowercase "next period").
            assert "next period" in html
            assert "$175.00" in html
            assert "view in grid" in html
            assert 'href="/grid?offset=1"' in html
            # The next-period bill's ROW is NOT on the dashboard (grid only).
            assert "Next Period Bill" not in html

    def test_bill_due_on_period_end_does_not_overlap_terminus(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """A bill due on the period-end date is pinned clear of the terminus.

        Such a bill's day_offset equals days_total, so its station sits on
        the exact point as the "period ends / end balance" terminus marker.
        The street pins that station below the line and drops its duplicate
        dot (the ``street__event--at-end`` modifier), so the bill and the
        terminus no longer print on top of each other.
        """
        with app.app_context():
            cur = current_pay_period(seed_user["user"].id)
            _add_txn(
                db.session, seed_user, cur, "End Day Bill", "250.00",
                due_date=last_covered_day(cur),
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The end-date bill renders with the at-end modifier that pins it
            # below the line and drops its duplicate dot.
            assert "street__event--at-end" in html
            assert "End Day Bill" in html
            # The terminus marker is still present (now clear of the bill).
            assert "period ends" in html


# ── Hero captions: staleness, money-macro negative formatting ───────


class TestHeroCaptions:
    """The hero's last-updated caption (staleness) and money formatting."""

    def test_stale_anchor_caption_warns(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """An anchor older than the threshold renders the stale caption.

        The seeded account asserts on the owner's bootstrap day, which is
        years before the 20-days-ago row appended here, so that row is the
        latest.  20 > 14 (default threshold), so the hero's last-updated
        caption carries the stale class + icon.

        *It used to DELETE the origination first, on the ground that the
        factory wrote it "at NOW".  Neither half survives plan step X-f3c-2c:
        the row is dated on the bootstrap day rather than now, and the table is
        append-only, so nothing may delete it.*
        """
        with app.app_context():
            _add_anchor_history(
                db.session, seed_user["account"], "1000.00", days_ago=20,
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "last updated" in html
            # The stale fragment carries the warning class on the caption.
            assert "pulse-stale" in html

    def test_negative_balance_uses_sign_before_dollar(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """A negative as-of-today balance renders -$X.XX, never $-X.XX.

        Anchor $100.00, a $500.00 projected expense due TODAY drives the
        as-of-today balance to 100.00 - 500.00 = -$400.00.  The shared
        money macro must place the sign before the dollar symbol.
        """
        with app.app_context():
            cur = current_pay_period(seed_user["user"].id)
            account = seed_user["account"]
            # The assertion IS the anchor since ruling R-EH; the cache column
            # this used to set beside it is deleted.  The row added below is
            # dated yesterday and the seeded origination on the owner's
            # bootstrap day years earlier, so this one GOVERNS -- which is what
            # the figure below is computed from.
            #
            # *It used to delete the origination first, to make this the
            # account's only assertion.  The table is append-only since plan
            # step X-f3c-2c, and superseding is what an owner does anyway.*
            _add_anchor_history(
                db.session, account, "100.00", days_ago=1,
            )
            _add_txn(
                db.session, seed_user, cur, "Big Bill", "500.00",
                due_date=date.today(),
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "-$400.00" in html
            assert "$-400.00" not in html


# ── Position tracks ──────────────────────────────────────────────────


class TestDashboardTracks:
    """The page-load-only position tracks (savings goals + debt)."""

    def test_savings_goal_track_renders(
        self, app, auth_client, seed_full_user_data_today, db,
    ):
        """An active savings goal renders its name, target, and rail marker.

        seed_full_user_data_today seeds an 'Emergency Fund' goal ($10,000
        target, no target_date and no recurring contribution to the goal
        account).  The Position tier renders the goal name, the $10,000.00
        target destination, and the you-are-here rail marker (positioned
        from progress_pct).  With no contribution the destination caption
        reads 'no recurring contribution'; with no target_date the pace
        pill is correctly absent.
        """
        with app.app_context():
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Position" in html
            assert "Emergency Fund" in html
            assert "$10,000.00" in html
            assert "no recurring contribution" in html
            # The rail marker positions from the goal's progress percent.
            assert "data-rail-pct=" in html

    def test_goal_pace_pill_renders_when_target_date_set(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """A goal with a target_date renders a pace verdict pill.

        pace is None without a target_date (so the pill is absent for the
        default fixture goal); a goal WITH a target_date resolves a pace
        from calculate_trajectory, and the pace_pill macro renders a
        verdict-pill chip.  This locks the pill rendering path.
        """
        # pylint: disable=import-outside-toplevel
        from app.models.savings_goal import SavingsGoal
        from tests._test_helpers import create_savings_account

        with app.app_context():
            acct = create_savings_account(
                seed_user, db.session, "Goal Account",
                Decimal("2500.00"),
            )
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="House Fund",
                target_amount=Decimal("10000.00"),
                # Relative to the APP's today, never a hard-coded calendar
                # date.  ``date(2027, 6, 1)`` was in the future when it was
                # written and becomes the PAST on 2027-06-01, after which
                # ``calculate_trajectory`` resolves no pace and the pill this
                # test exists to lock never renders -- so the test would have
                # started failing that morning.  Caught by the calendar sweep
                # at SHEKEL_FAKE_TODAY=2028-02-29.
                target_date=add_months(display_today(), 10),
            )
            db.session.add(goal)
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "House Fund" in html
            # A pace verdict pill renders (one of Ahead / On track / Behind).
            assert "verdict-pill" in html

    def test_debt_track_renders_marker_and_debt_free(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """A loan renders the D10 debt track: hero figure, caption, percent rail.

        The D10 ruling (2026-07-09) INVERTED the track's presentation:
        the CURRENT total debt is now the hero figure (``track__pos-num``),
        the destination is its caption ("to $0 . loans paid off <Mon YYYY>",
        worded that way as of plan step X-q3 -- the date covers the debts
        that HAVE a payoff model), and the rail's here-label carries only the
        percent ("N% paid").
        The old anatomy (a "$X left" rail mid-label under a "$0" hero) is
        retired; the word "left" no longer appears in the track.

        A $1,000.00 auto loan (create_loan_account) with no recorded
        payments keeps its ledger-derived balance at the origination
        principal regardless of the wall-clock date (the balance is
        payment-record-driven, not schedule-elapsed), so the hero renders
        exactly $1,000.00 and the principal-paid fraction is exactly 0,
        making the rail label read "0% paid".  seed_user has no savings
        goals, so the debt hero is the page's only ``track__pos-num``.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_loan_account

        with app.app_context():
            create_loan_account(seed_user, db.session, name="Auto Loan")
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Debt payoff" in html
            # D10 hero: the CURRENT figure leads, in the pos-num slot
            # (asserted glued to its class so the pulse hero's identical
            # $1,000.00 elsewhere on the page cannot satisfy it).
            assert 'track__pos-num font-mono">$1,000.00' in html
            # D10 caption: the destination, then the projected arrival.
            assert "to $0" in html
            assert "loans paid off" in html
            # D10 rail label: only the percent -- exactly 0% with no
            # payments recorded against the $1,000.00 principal.
            assert "0% paid" in html
            # The route scales the principal-paid fraction to a percent on
            # the rail marker's data attribute.
            assert "data-rail-pct=" in html

    def test_debt_rail_renders_the_percent_not_the_raw_fraction(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """The rail renders 60%, not 0.6 -- the scaled value, not the field.

        Plan step X-u made ``principal_paid_fraction`` a ``DebtSummary`` field,
        and the route's ``_DebtTrackView`` carries the summary WHOLE beside the
        percent it scales from it.  So the template can now reach the figure two
        ways, and only one of them is renderable: ``.principal_paid_pct`` is the
        0-100 float the rail positions from, while ``.summary
        .principal_paid_fraction`` is the ``Decimal`` in ``[0, 1]``.

        The existing marker test cannot tell them apart -- its fixture's
        fraction is exactly 0, which renders "0% paid" either way.  This one
        trues a $1,000.00 loan down to a known $400.00, so the fraction is
        (1000.00 - 400.00) / 1000.00 = 0.6 and the honest rail reads
        **60% paid** where a template reading the raw field would read "1% paid"
        (``"{:.0f}".format(0.6)``) and position the marker at 0.6% of the rail.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_loan_account, insert_trueup_event

        with app.app_context():
            acct = create_loan_account(
                seed_user, db.session, name="Trued Auto Loan",
            )
            insert_trueup_event(acct.loan_params, Decimal("400.00"))
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The fixture really is at $400.00 of $1,000.00, so the fraction is
            # 0.6 -- not the 0 the sibling test's fixture produces.
            assert 'track__pos-num font-mono">$400.00' in html
            assert "60% paid" in html
            assert 'data-rail-pct="60.0' in html
            # And the raw fraction is NOT what reached the rail.
            assert "1% paid" not in html
            assert 'data-rail-pct="0.6"' not in html

    def test_debt_track_renders_its_dti_badge_and_tooltip(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """The debt track's DTI badge and tooltip reach the page.

        Plan step X-s3 re-pointed both from ``tracks.debt.dti_ratio`` /
        ``dti_label`` to ``tracks.debt.summary.dti.ratio`` / ``.label``, and
        X-s3's adversarial review measured that NOTHING covered them: the whole
        of this module passed with the badge and tooltip silently gone, because
        Jinja is on the default ``Undefined`` (no ``StrictUndefined``), so a
        wrong attribute path renders nothing rather than raising.  The
        ``/savings`` twin of this block was covered; this one was not.

        $78,000 / 26 = $3,000 gross biweekly -> $3,000 * 26 / 12 = $6,500
        gross monthly.  A $1,000 auto loan at 5% over 24 months carries a
        $43.87 monthly P&I, so DTI = 43.87 / 6500 * 100 = 0.7% -- 'healthy',
        which is the badge, and the figure the title attribute carries.
        """
        # pylint: disable=import-outside-toplevel
        from app.models.ref import FilingStatus
        from app.models.salary_profile import SalaryProfile
        from tests._test_helpers import create_loan_account, start_test_pay_list

        with app.app_context():
            create_loan_account(
                seed_user, db.session, name="Auto Loan",
                principal=Decimal("1000.00"), rate=Decimal("0.05000"),
                term=24,
            )
            profile = SalaryProfile(
                period_end_days_before_payday=6,
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=db.session.query(FilingStatus).first().id,
                name="DTI Salary",
                state_code="NC",
            )
            db.session.add(profile)
            start_test_pay_list(profile, Decimal("3000.00"))  # $78,000.00 a year / 26
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The tooltip carries the PITI total and the ratio, glued together
            # so a stray "0.7%" elsewhere cannot satisfy it.
            assert 'title="paying $43.87/mo -- DTI 0.7%"' in html
            # The band badge, from the label property derived off that ratio.
            assert "Healthy" in html

    def test_no_goals_no_debt_tracks_absent(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """No goals and no debt -> the tracks section is not rendered.

        The page guards the include on ``tracks.goals or tracks.debt``;
        seed_user has neither, so the Position header is absent.
        """
        with app.app_context():
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert b"<h6>Position</h6>" not in resp.data


# ── Degraded states ──────────────────────────────────────────────────


class TestDashboardDegraded:
    """The two degraded states the page renders instead of the pulse."""

    def test_no_account_neutral_empty_state(self, app, auth_client, seed_user, db):
        """No projectable account -> the setup copy, and no pulse region.

        Deactivate the only account; the page must render the empty state
        (Gate B7) rather than the pulse region.

        **The copy names the KIND that is missing since ledger row P65**
        (2026-08-18): "an account" was false for the owner this state is
        actually reachable by, who has entered one and had it refused.
        """
        # pylint: disable=import-outside-toplevel
        from app.models.account import Account

        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)
            account.is_active = False
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Add a spending account to see your dashboard" in html
            assert "End of this period" not in html

    def test_a_loan_only_owner_still_sees_their_debt_position(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """One mortgage, no spending account -> the setup card AND the tracks.

        **Ledger row P65, ruled 2026-08-18 (developer)**, and measured on both
        sides of pay-calendar plan step C2-f2e, so it is a defect this page had
        rather than one that step introduced.  ``has_account`` is
        ``resolve_grid_account``, which REFUSES an amortizing account by design
        (ruling D4) because this page renders a cash-flow balance -- so an owner
        who has entered a mortgage and nothing else was told to set up an
        account, while ``compute_tracks_section`` computed their whole debt
        position and the template threw it away.

        The position tier reads no grid account and no pay period, which is why
        it can render beside the card rather than instead of it.  The figures
        are the loan's own: a $200,000.00 principal, so the debt track states
        that total.
        """
        # pylint: disable=import-outside-toplevel
        from app.models.account import Account
        from tests._test_helpers import create_loan_account

        with app.app_context():
            create_loan_account(
                seed_user, db.session, name="Only Mortgage",
                principal=Decimal("200000.00"),
            )
            account = db.session.get(Account, seed_user["account"].id)
            account.is_active = False
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The page still cannot project, so the card and no pulse region.
            assert "Add a spending account to see your dashboard" in html
            assert "End of this period" not in html
            # And the position the owner HAS entered is on the page.
            assert "$200,000.00" in html

    def test_account_no_current_period_generate_cta(self, app, auth_client, seed_user):
        """Account but no pay period covers today -> the generate CTA.

        seed_user (no seed_periods_today) has only the 2024 bootstrap
        period, so no period contains today: the pulse producer returns
        None and the page renders the 'No pay period covers today' CTA.
        """
        with app.app_context():
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "No pay period covers today" in html
            assert "Generate Pay Periods" in html


# ── HTMX section endpoints ──────────────────────────────────────────


class TestPulseSection:
    """``dashboard.pulse_section`` -- the balanceChanged swap target."""

    def test_pulse_section_htmx_returns_partial(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """GET /dashboard/pulse with HX-Request -> 200 with the pulse partial.

        The partial (NOT the full page) carries the pulse markers but not
        the base-template chrome (no navbar). A dated current-period bill
        renders in the swapped-in STREET band.  Re-pointed off the removed
        "Due Soon" header (audit "Rebuild decisions" anatomy item 3) to the
        street head + the bill's street event.
        """
        with app.app_context():
            cur = current_pay_period(seed_user["user"].id)
            _add_txn(
                db.session, seed_user, cur, "Rent", "300.00",
                due_date=cur.start_date + timedelta(days=2),
            )
            db.session.commit()

            resp = auth_client.get(
                "/dashboard/pulse", headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "This period, day by day" in html
            assert "Rent" in html
            assert "$300.00" in html
            # Fragment only -- no full-page navbar chrome.
            assert "<nav" not in html

    def test_pulse_section_no_htmx_redirects(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET /dashboard/pulse without HX-Request -> 302 to the page."""
        with app.app_context():
            resp = auth_client.get("/dashboard/pulse")
            assert resp.status_code == 302
            assert "/dashboard" in resp.headers["Location"]

    def test_pulse_section_no_current_period_renders_cta(
        self, app, auth_client, seed_user,
    ):
        """No pay period covers today -> 200 with the generate-periods CTA.

        When the schedule lapses between page load and a ``balanceChanged``
        refresh the producer returns ``None``; the swap target must render
        the "No pay period covers today" CTA rather than 500 on a missing
        hero.  seed_user (no seed_periods_today) has only the bootstrap
        period, so no period contains today.
        """
        with app.app_context():
            resp = auth_client.get(
                "/dashboard/pulse", headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "No pay period covers today" in html
            assert "Generate Pay Periods" in html
            # The CTA fragment carries no pulse hero markup.
            assert 'id="balance-display"' not in html

    def test_pulse_section_companion_blocked(self, companion_client):
        """A companion is blocked from /dashboard/pulse (404, not-yours rule)."""
        resp = companion_client.get(
            "/dashboard/pulse", headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404


class TestBalanceSection:
    """``dashboard.balance_section`` -- the anchor-edit revert fragment."""

    def test_balance_section_htmx_returns_display_fragment(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET /dashboard/balance with HX-Request -> 200 #balance-display.

        The fragment is the click-to-edit hero control the anchor editor
        replaced: it carries ``id="balance-display"``, the as-of-today
        figure ($1,000.00 for the un-transacted seed account), and the
        anchor_form opener with ``revert=dashboard`` (the cancel-path
        contract).
        """
        with app.app_context():
            resp = auth_client.get(
                "/dashboard/balance", headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'id="balance-display"' in html
            assert "$1,000.00" in html
            assert "revert=dashboard" in html
            # Fragment only -- no full-page chrome.
            assert "<nav" not in html

    def test_balance_section_no_htmx_redirects(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET /dashboard/balance without HX-Request -> 302 to the page."""
        with app.app_context():
            resp = auth_client.get("/dashboard/balance")
            assert resp.status_code == 302
            assert "/dashboard" in resp.headers["Location"]

    def test_balance_section_no_account_renders_fallback(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """No resolvable account -> 200 with the neutral no-data fallback.

        If the account is deactivated while the anchor editor is open, the
        Cancel / Escape revert (``dashboard.balance_section``) finds no
        account: the producer returns ``{"hero": None}``.  The fragment
        must render a neutral, non-editable ``#balance-display`` (keeping
        the id so any enclosing swap target stays valid) rather than 500 on
        a missing ``account_id``.
        """
        # pylint: disable=import-outside-toplevel
        from app.models.account import Account

        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)
            account.is_active = False
            db.session.commit()

            resp = auth_client.get(
                "/dashboard/balance", headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'id="balance-display"' in html
            assert "No balance data available" in html
            # No editable anchor opener when there is no account to edit.
            assert "revert=dashboard" not in html

    def test_balance_section_companion_blocked(self, companion_client):
        """A companion is blocked from /dashboard/balance (404)."""
        resp = companion_client.get(
            "/dashboard/balance", headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404


# ── Anchor-edit revert cycle (the dashboard cancel/409 contract) ────


# ``test_anchor_409_conflict_reopens_in_dashboard`` was DELETED at plan step
# X-f1c3c (ruling R-EN), for the reason its investment twin was: the 409 it
# graded does not exist, because a true-up appends rather than overwrites.  The
# Cancel / Escape revert cycle this class is really about is untouched.


class TestAnchorEditRevertCycle:
    """The dashboard anchor-edit Cancel / Escape cycle still threads.

    The hero control opens ``accounts.anchor_form?revert=dashboard``; the
    editor's Cancel reverts to ``dashboard.balance_section`` (mapped by
    ``accounts._anchor_revert_url``), which re-renders the #balance-display
    fragment.  This locks the full round-trip at the route layer.
    """

    def test_anchor_form_opens_with_dashboard_revert(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The editor opened with revert=dashboard cancels to balance_section.

        The Cancel button's hx-get must target ``/dashboard/balance`` (the
        dashboard revert URL), not the grid display cell, so the dashboard
        card is restored rather than stranded on the grid's whole-dollar
        cell.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            resp = auth_client.get(
                f"/accounts/{account_id}/anchor-form?revert=dashboard",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            # Cancel reverts to the dashboard balance fragment.
            assert 'hx-get="/dashboard/balance"' in html
            # The PATCH carries the dashboard revert token through the
            # round-trip, so the success re-render lands on the dashboard
            # card.  It also served a 409 conflict re-open until ruling R-EN
            # deleted that response (plan step X-f1c3c).
            assert "revert=dashboard" in html

# ── Nav bar (unchanged chrome) ──────────────────────────────────────


class TestNavBar:
    """The nav bar's dashboard / budget links and active states."""

    def test_nav_has_dashboard_link(self, app, auth_client, seed_user, seed_periods_today):
        """Nav bar on the grid page contains a 'Dashboard' link."""
        with app.app_context():
            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            assert b"Dashboard" in resp.data

    def test_nav_budget_points_to_grid(self, app, auth_client, seed_user, seed_periods_today):
        """The Budget nav link href contains '/grid'."""
        with app.app_context():
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert b'href="/grid"' in resp.data

    def test_nav_dashboard_active_on_root(self, app, auth_client, seed_user, seed_periods_today):
        """The dashboard nav item is active on /."""
        with app.app_context():
            resp = auth_client.get("/")
            html = resp.data.decode()
            assert 'class="nav-link active" href="/"' in html or \
                   'class="nav-link active" href="/dashboard"' in html

    def test_nav_budget_active_on_grid(self, app, auth_client, seed_user, seed_periods_today):
        """The Budget nav item is active on /grid."""
        with app.app_context():
            resp = auth_client.get("/grid")
            html = resp.data.decode()
            assert 'class="nav-link active" href="/grid"' in html

    def test_no_redirect_loop(self, app, auth_client, seed_user, seed_periods_today):
        """GET / and GET /dashboard both return 200, no redirect loops."""
        with app.app_context():
            resp1 = auth_client.get("/")
            assert resp1.status_code == 200
            resp2 = auth_client.get("/dashboard")
            assert resp2.status_code == 200
