"""The Spending tab: its hero, breakdown and rail, a refunded category, and the history note."""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.services import status_seam

from tests._test_helpers import create_account_of_type, set_default_grid_account
from tests._test_helpers import add_entry, create_envelope_txn, settlement_if_settling
from app.routes.analytics_view import _bar_pct, _history_note, _size_lens_rows
from app.services.spending_report_service import (
    SeriesPoint,
    SpendingGroupRow,
    SpendingItemRow,
    SpendingWindow,
)

from ._helpers import _shell_autoload_target, _chart_payload, _settled_spending_txn


class TestSpendingTab:
    """Pins for the Spending tab (Slice 3, S-P2): hero, breakdown, rail.

    Today is frozen to 2026-03-20 by the module autouse fixture, so the
    ``seed_periods`` months (Jan-May 2026) are completed and the calendar-
    month windows below are deterministic.
    """

    def test_spending_tab_breakdown_and_hero(self, app, auth_client, seed_user,
                                             seed_periods, db):
        """A January window renders the spent hero, group shares, and scope.

        seed_periods[0] (starts 2026-01-02) carries Rent 1200 (Home),
        Groceries 500 (Family), Car Payment 300 (Auto): total 2000, shares
        60% / 25% / 15%.
        """
        with app.app_context():
            _settled_spending_txn(db, seed_user, seed_periods[0], "Rent",
                                  "Rent", "1200.00")
            _settled_spending_txn(db, seed_user, seed_periods[0], "Food",
                                  "Groceries", "500.00")
            _settled_spending_txn(db, seed_user, seed_periods[0], "Car",
                                  "Car Payment", "300.00")
            db.session.commit()

            resp = auth_client.get(
                "/analytics/spending?year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            # Hero: 1200 + 500 + 300 = 2000, measured scope on Checking.
            assert "$2,000.00" in html
            assert "January 2026" in html
            assert "measured" in html
            assert "Checking" in html
            # Where It Went: groups amount-descending with the group amount.
            assert "Where It Went" in html
            assert "Home" in html
            assert "Family" in html
            assert "Auto" in html
            assert "$1,200.00" in html
            # Shares 60 / 25 / 15 percent.
            assert "60%" in html
            assert "25%" in html
            assert "15%" in html
            # Share bars carry the CSP-safe width attribute.
            assert "data-progress-pct" in html

    def test_spending_tab_comparison_chips(self, app, auth_client, seed_user,
                                           seed_periods, db):
        """A February window shows the vs-January comparison chip.

        January (period[0]) spent 1000; February (period[3], starts
        2026-02-13) spent 1500.  vs-prior delta = 1500 - 1000 = +500 (+50%),
        rendered as a spent-more (danger) direction.
        """
        with app.app_context():
            _settled_spending_txn(db, seed_user, seed_periods[0], "JanRent",
                                  "Rent", "1000.00")
            _settled_spending_txn(db, seed_user, seed_periods[3], "FebRent",
                                  "Rent", "1500.00")
            db.session.commit()

            resp = auth_client.get(
                "/analytics/spending?year=2026&month=2",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            assert "$1,500.00" in html          # February spent hero
            assert "vs January" in html          # vs-prior chip label
            assert "+$500.00" in html            # SIGNED delta (P-AN11 form)
            assert "+50.0%" in html
            # The caption names the prior month's total, so the delta can
            # no longer read as January's total (P-AN11).
            assert "January total $1,000.00" in html
            assert "trend-up" in html            # spent more -> danger dir

    def test_spending_tab_surprises(self, app, auth_client, seed_user,
                                    seed_periods, db):
        """A settled row whose actual differs from estimate is a surprise.

        Electric Bill est 100 actual 145 -> delta +45 (a surprise); Rent
        Exact est 1200 actual 1200 -> delta 0 (not a surprise).  The net over
        ALL surprises is +45.
        """
        with app.app_context():
            _settled_spending_txn(db, seed_user, seed_periods[0], "Electric Bill",
                                  "Rent", "100.00", actual="145.00")
            _settled_spending_txn(db, seed_user, seed_periods[0], "Rent Exact",
                                  "Rent", "1200.00", actual="1200.00")
            db.session.commit()

            resp = auth_client.get(
                "/analytics/spending?year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            assert "Estimate Surprises" in html
            assert "Electric Bill" in html
            assert "+$45.00" in html             # delta 145 - 100
            # Net over ALL surprises = +45 (the est==actual row is excluded).
            assert "net +$45.00" in html

    def test_spending_tab_chart_and_lens_rows(self, app, auth_client,
                                              seed_user, seed_periods, db):
        """The month chart serializes the series; the ledger carries both lenses.

        January (period[0]): Rent 1000, then it stops.  February
        (period[3], starts 2026-02-13): Groceries 460, brand new.  Viewing
        February 2026: the chart's 12 bars end at Feb (index 11 = 460.0)
        with Jan at index 10 (1000.0), pre-history 2025 months null, the
        6-mo avg = 1000.0 (Jan is the only existing trailing month), and
        the history note at Jan 2026.  The By-size lens collapses the
        singleton Family group (Groceries as kin, "new" badge); the
        By-change lens lists the stopped Rent at -$1,000.00.  Top Movers
        and sparklines are retired (D7).
        """
        with app.app_context():
            _settled_spending_txn(db, seed_user, seed_periods[0], "JanRent",
                                  "Rent", "1000.00")
            _settled_spending_txn(db, seed_user, seed_periods[3], "FebFood",
                                  "Groceries", "460.00")
            db.session.commit()

            resp = auth_client.get(
                "/analytics/spending?year=2026&month=2",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            # The in-canvas chart carries the trailing-12 series.
            assert 'id="spending-months-canvas"' in html
            chart = _chart_payload(html)
            assert len(chart["values"]) == 12
            assert chart["viewed_index"] == 11
            assert chart["values"][11] == 460.0    # February (viewed)
            assert chart["values"][10] == 1000.0   # January (comparison)
            assert chart["values"][0] is None      # Mar 2025: pre-history
            assert chart["nav"][11] == {"year": 2026, "month": 2}
            # 6-mo avg: Jan is the only trailing month that exists -> 1000.
            assert chart["avg"] == 1000.0
            assert chart["history_note"] == "settled history begins Jan 2026"

            # Lens pill; singleton Family collapses with Groceries as kin.
            assert "By size" in html
            assert "By change" in html
            assert "spend-kin" in html
            assert "spend-badge-new" in html
            # By change: the stopped Rent appears as a zero-current row.
            assert "spend-chrow" in html
            assert "-$1,000.00" in html
            # The retired surfaces are gone (D7).
            assert "Top Movers" not in html
            assert "spend-spark-svg" not in html

    def test_spending_tab_empty_month(self, app, auth_client, seed_user,
                                      seed_periods):
        """A window with no settled spend renders the zeroed empty breakdown."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/spending?year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "$0.00" in html
            assert "No settled spending in January 2026" in html

    def test_spending_tab_empty_state_no_account(self, app, auth_client,
                                                 seed_user):
        """No active checking account renders the empty state, not a crash."""
        from unittest.mock import patch
        with app.app_context():
            with patch(
                "app.services.spending_report_service.resolve_cash_flow_set",
                return_value=None,
            ):
                resp = auth_client.get(
                    "/analytics/spending",
                    headers={"HX-Request": "true"},
                )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "No active checking account" in html
            assert "Set up an account" in html

    def test_spending_tab_scope_is_the_saved_default_grid_account(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """The route passes ``current_user.settings`` to the service (CC-4-3).

        The report's scope is the set's PRIMARY -- the owner's saved default
        grid account first.  An HYSA saved as the default names itself on the
        scope line; a route that dropped ``user_settings=`` would render
        Checking here and pass every other test in this class.
        """
        with app.app_context():
            hysa = create_account_of_type(
                seed_user, db.session, "HYSA", "Rainy Day",
                anchor_balance=Decimal("1000.00"),
            )
            set_default_grid_account(db.session, seed_user["user"].id, hysa.id)
            db.session.commit()

            resp = auth_client.get(
                "/analytics/spending?year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert "Settled spending from your Rainy Day account." in (
                resp.data.decode()
            )

    def test_spending_tab_default_is_prior_month(self, app, auth_client,
                                                 seed_user, seed_periods):
        """With no month param, the default window is the prior completed month.

        Today is frozen 2026-03-20, so the default is February 2026.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/spending",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert "February 2026" in resp.data.decode()

    def test_spending_tab_non_htmx_renders_shell(self, app, auth_client, seed_user):
        """A non-HTMX GET renders the shell with Spending active (D13)."""
        with app.app_context():
            resp = auth_client.get("/analytics/spending")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "shekel-scroll-pills" in html
            assert _shell_autoload_target(html) == "/analytics/spending"


class TestARefundedCategoryDrawsNoBar:
    """The By-size lens's geometry when a category's total is NEGATIVE.

    Ruling **bank_import:R-II**, plan step ``bank_import:X-gj-2b``.  A merchant
    credit files as a NEGATIVE purchase, so a category whose refunds exceeded
    its purchases comes to a negative total.  ``_totals_by_category`` took an
    ``abs()`` until that step and this geometry could not see one.

    **What a bar answers is *how much of the budget did this consume*, and the
    answer for such a row is none.**  Asserted at the SERVER, because the
    clamp in ``progress_bar.js`` is written as a defence against a malformed
    value -- a refunded category is not malformed, and a money-shaped decision
    left to the browser is one no test in this project drives.
    """

    @staticmethod
    def _group(name, amount, share):
        """One singleton group row worth *amount*."""
        return SpendingGroupRow(
            group_name=name, amount=Decimal(amount), share=Decimal(share),
            delta=Decimal("0.00"), is_new=False,
            items=[SpendingItemRow(
                category_id=1, item_name=name, amount=Decimal(amount),
                share=Decimal(share), delta=Decimal("0.00"), is_new=False,
            )],
        )

    def test_a_negative_group_gets_a_zero_width_bar(self):
        """The refunded row, beside an ordinary one that still scales."""
        rows = _size_lens_rows([
            self._group("Home", "1200.00", "0.93"),
            self._group("Amazon", "-86.67", "-0.07"),
        ])

        assert rows[0]["bar_pct"] == 100.0
        assert rows[1]["bar_pct"] == 0.0, (
            "a category whose refunds exceeded its purchases consumed no "
            "budget, so it draws no bar"
        )
        # The FIGURE is untouched: only the geometry is floored.
        assert rows[1]["amount"] == Decimal("-86.67")
        assert rows[1]["share"] == Decimal("-0.07")

    def test_a_window_of_only_refunds_draws_no_bars_at_all(self):
        """Every row negative, so the largest is too and nothing scales.

        The arm ``max_amount <= 0`` covers, which was reachable before this
        step only for an all-ZERO window.
        """
        rows = _size_lens_rows([
            self._group("Amazon", "-86.67", "0"),
            self._group("Walmart", "-29.79", "0"),
        ])

        assert [row["bar_pct"] for row in rows] == [0.0, 0.0]

    def test_an_ordinary_positive_row_still_scales(self):
        """The control: the floor is on the sign, not on every row."""
        assert _bar_pct(Decimal("50.00"), Decimal("200.00")) == 25.0

    @staticmethod
    def _group_of(name, items):
        """One group row whose amount is the SIGNED sum of its items."""
        total = sum((Decimal(a) for _, a in items), Decimal("0.00"))
        return SpendingGroupRow(
            group_name=name, amount=total, share=Decimal("0"),
            delta=Decimal("0.00"), is_new=False,
            items=[
                SpendingItemRow(
                    category_id=index, item_name=item_name,
                    amount=Decimal(amount), share=Decimal("0"),
                    delta=Decimal("0.00"), is_new=False,
                )
                for index, (item_name, amount) in enumerate(items, start=1)
            ],
        )

    def test_an_ITEM_bar_cannot_exceed_its_own_track(self):
        """A mixed-sign group holds an item BIGGER than any group total.

        Plan step ``bank_import:X-gj-2b-3``, found by adversarial financial
        review.  The denominator was ``max(group.amount ...)``, and every bar
        was bounded at 100% only because an item could not exceed its group:
        while every category total was non-negative, ``item <= group <= max``.
        Ruling **bank_import:R-II** ended that.

        ``Food`` = ``Groceries +600.00`` and ``Restaurants -500.00`` sums to
        ``+100.00`` while holding a ``+600.00`` item; against ``Housing``'s
        ``+300.00`` that item rendered **200.0** -- off its own track, and
        clamped only by ``progress_bar.js``, which is the tier ``_bar_pct``
        says in as many words a money-shaped decision may not be left to.

        Asserts the BOUND over every rendered bar rather than one number, so a
        denominator that merely moves the overflow somewhere else still fails.
        """
        rows = _size_lens_rows([
            self._group_of("Food", [
                ("Groceries", "600.00"), ("Restaurants", "-500.00"),
            ]),
            self._group_of("Housing", [("Rent", "300.00")]),
        ])

        every_bar = [row["bar_pct"] for row in rows] + [
            item["bar_pct"] for row in rows for item in row["item_rows"]
        ]
        assert every_bar, "the case staged no bars, so it graded nothing"
        assert max(every_bar) <= 100.0, (
            f"a bar overflowed its track: {every_bar}"
        )
        # ...and the largest RENDERED row is what fills it, which is the
        # contract the docstring states.
        assert max(every_bar) == 100.0

    def test_a_window_with_no_refunds_scales_exactly_as_before(self):
        """The control the fix turns on: nothing moves without a refund.

        An item cannot exceed its own group while every category is
        non-negative, so the maximum over groups and the maximum over
        everything rendered are the SAME figure -- and this case fails if the
        denominator were changed in a way that moved an ordinary window.
        """
        rows = _size_lens_rows([
            self._group_of("Food", [
                ("Groceries", "600.00"), ("Restaurants", "200.00"),
            ]),
            self._group_of("Housing", [("Rent", "400.00")]),
        ])
        by_name = {row["name"]: row for row in rows}

        # max over groups is Food's 800.00, and no item exceeds it.
        assert by_name["Food"]["bar_pct"] == 100.0
        assert by_name["Housing"]["bar_pct"] == 50.0
        assert [item["bar_pct"] for item in by_name["Food"]["item_rows"]] == [
            75.0, 25.0,
        ]


class TestTheHeroStatesBOTHBasesWhenTheyDiffer:
    """The card carried two undisclosed bases the moment a refund landed.

    Developer ruling 2026-09-01, plan step ``bank_import:X-gj-2b-3``, found by
    adversarial design review.  The hero is the NET (``_spent_total``) and
    every breakdown row's percent is a slice of what MOVED
    (``_breakdown._share_base``).  Ruling **bank_import:R-II** separated the
    two: `Groceries $600.00` beside `Electronics -$500.00` gave a hero of
    `$100.00` over rows reading `55%` and `-45%` -- percentages summing to
    about 9%, and a row six times its own hero.  The second figure is stated
    where they differ so a reader can multiply a row by a number on the card.
    """

    @staticmethod
    def _settled_envelope(db, seed_user, period, name, category_key, amounts):
        """Settle an envelope worth exactly the purchases handed to it.

        A negative CATEGORY total cannot come from a negative transaction --
        ``ck_transactions_estimated_amount`` is ``>= 0`` -- so the refunded
        shape is an ENVELOPE whose entries net below zero, which is the shape
        ruling **bank_import:R-II** actually creates.
        """
        txn = create_envelope_txn(
            seed_user, db.session, period, name, Decimal("0.00"),
        )
        txn.category_id = seed_user["categories"][category_key].id
        for amount in amounts:
            add_entry(
                db.session, seed_user, txn, Decimal(amount),
                period.start_date,
            )
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.DONE),
            settlement=settlement_if_settling(
                txn, ref_cache.status_id(StatusEnum.DONE),
            ),
        )
        db.session.flush()
        return txn

    def test_a_refunded_month_states_what_MOVED_beside_the_net(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """`$600.00` out and `$500.00` back: hero `$100.00`, moved `$1,100.00`."""
        with app.app_context():
            self._settled_envelope(
                db, seed_user, seed_periods[0], "Shop", "Groceries",
                ["600.00"],
            )
            self._settled_envelope(
                db, seed_user, seed_periods[0], "Return", "Rent",
                ["-500.00"],
            )
            db.session.commit()

            html = auth_client.get(
                "/analytics/spending?year=2026&month=1",
                headers={"HX-Request": "true"},
            ).data.decode()
            said = " ".join(html.split())

            assert "$100.00" in said, "the hero is still the NET"
            assert "$1,100.00 moved" in said
            assert "net of refunds above" in said

    def test_an_ORDINARY_month_states_ONE_figure(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """The control: the two bases are the same figure without a refund.

        Printing one number twice would be noise, and this is what makes the
        second line a disclosure rather than decoration -- it fails if the
        line were rendered unconditionally.
        """
        with app.app_context():
            self._settled_envelope(
                db, seed_user, seed_periods[0], "Shop", "Groceries",
                ["600.00"],
            )
            db.session.commit()

            said = " ".join(auth_client.get(
                "/analytics/spending?year=2026&month=1",
                headers={"HX-Request": "true"},
            ).data.decode().split())

            assert "$600.00" in said
            assert "moved" not in said
            assert "net of refunds above" not in said


class TestTheHistoryNoteAgreesWithTheBarsBesideIt:
    """The chart's *settled history begins* caption asks *did money MOVE*.

    Plan step ``bank_import:X-gj-2b-3``.  The note exists to explain a LEADING
    RUN OF EMPTY BARS, and it found the first bar by ``point.total > 0`` on the
    stated premise that a window's total is non-negative.  Ruling
    **bank_import:R-II** ended that premise: a month whose refunds exceeded its
    purchases has a negative total and DRAWS A BAR, so a leading negative month
    got a caption saying history began a month after the chart's own first bar
    -- the sentence contradicting the picture it sits under.

    A window with pay periods but no settled spend is ``Decimal("0")`` and is
    genuinely an empty bar; one before the owner's periods is ``None``.  Both
    still count as no history, which is what the cases below pin.
    """

    @staticmethod
    def _point(year, month, total):
        """One series bar for *month*, worth *total* (``None`` = pre-history)."""
        return SeriesPoint(
            window=SpendingWindow(
                window_type="month", month=month, year=year,
            ),
            total=None if total is None else Decimal(total),
        )

    def test_a_leading_REFUNDED_month_is_where_history_begins(self):
        """A negative first bar is history, so there is no note to make.

        Under ``total > 0`` this returned *settled history begins Feb 2026*
        while January's bar was drawn at ``-50.00`` right beside it.
        """
        assert _history_note([
            self._point(2026, 1, "-50.00"),
            self._point(2026, 2, "100.00"),
        ]) is None

    def test_a_leading_EMPTY_month_still_makes_the_note(self):
        """The control: a zero month really is an empty bar.

        Without this case the class above would pass on a function that never
        returned a note at all.
        """
        assert _history_note([
            self._point(2026, 1, "0.00"),
            self._point(2026, 2, "100.00"),
        ]) == "settled history begins Feb 2026"

    def test_a_leading_PRE_HISTORY_month_still_makes_the_note(self):
        """``None`` is a window before the owner's pay periods, not a zero."""
        assert _history_note([
            self._point(2025, 12, None),
            self._point(2026, 2, "100.00"),
        ]) == "settled history begins Feb 2026"

    def test_a_series_that_moved_nothing_makes_no_note(self):
        """Nothing to date, so nothing is said."""
        assert _history_note([
            self._point(2026, 1, "0.00"),
            self._point(2026, 2, None),
        ]) is None

    def test_a_REFUNDED_month_after_a_zero_one_dates_the_note_to_itself(self):
        """The two halves together: zero is not history and negative is.

        January ``0.00`` then February ``-50.00`` -- the note must name
        February, which is the first bar the chart draws.  Under ``total > 0``
        this returned ``None`` and the leading empty bar went unexplained.
        """
        assert _history_note([
            self._point(2026, 1, "0.00"),
            self._point(2026, 2, "-50.00"),
        ]) == "settled history begins Feb 2026"
