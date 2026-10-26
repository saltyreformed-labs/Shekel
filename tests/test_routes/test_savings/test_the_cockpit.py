"""The net-worth cockpit: its chart serializers (the horizon, the milestone axis, the sparklines),
its refresh fragment and its inline balance cell.
"""

import re
from decimal import Decimal

import pytest

from app.extensions import db
from app.services import savings_dashboard_service
from app.services.balance_at import BalanceContext

from ._helpers import _create_small_loan


class TestHorizonSerialization:
    """Unit tests for the Horizon-range Chart.js serializer (P-AC1 Loop B P1).

    ``_serialize_horizon`` is the float boundary for the horizon range: it
    maps the producer's ``Decimal`` band series + net trajectory to floats,
    the annual dates to ``%b %Y`` labels, and each milestone to its chip
    ``label`` plus the fractional axis position ``x`` its ``date`` is spent
    computing (plan step X-s1 -- the ISO ``date`` and the machine ``kind`` this
    used to carry reached no client reader).
    """

    def test_maps_decimals_dates_and_milestones(self):
        """Decimals become floats, dates become labels, milestone dates ISO."""
        # pylint: disable=import-outside-toplevel
        from datetime import date
        from app.routes.savings import _serialize_horizon
        horizon = {
            "dates": [date(2026, 7, 12), date(2026, 12, 31)],
            "net": [Decimal("236184.51"), Decimal("240000.00")],
            "composition": {
                "asset": [Decimal("358034.92"), Decimal("360000.00")],
                "liability": [Decimal("192941.56"), Decimal("190000.00")],
            },
            "milestones": [
                {"date": date(2048, 12, 1), "label": "Debt-free"},
            ],
            "current_index": 0,
        }

        out = _serialize_horizon(horizon)

        assert out["labels"] == ["Jul 2026", "Dec 2026"]
        assert out["net"] == [236184.51, 240000.0]
        assert all(isinstance(v, float) for v in out["net"])
        assert out["composition"]["asset"] == [358034.92, 360000.0]
        assert all(
            isinstance(v, float) for v in out["composition"]["liability"]
        )
        # The milestone serializes its chip ``label`` plus a fractional axis
        # position ``x``, and nothing else (plan step X-s1): the producer's
        # ``date`` is SPENT here computing ``x`` and the client never reads a
        # date.  This one (2048) is beyond the two sample dates (ending
        # 2026-12-31), so ``x`` clamps to the last index (1.0) -- the flag pins
        # to the right edge.
        assert out["milestones"] == [{"label": "Debt-free", "x": 1.0}]
        assert out["current_index"] == 0
        # The serializer's OWN key set, pinned.  The mutation guard beside this
        # proves every key the PRODUCER publishes is consumed; it says nothing
        # about a key the serializer invents, so re-adding ``horizon_end`` --
        # the exact key plan step X-q2 deleted -- would otherwise pass every
        # test in this class.  Found by X-s's adversarial review.
        assert set(out) == {
            "labels", "net", "composition", "milestones", "current_index",
        }

    def test_none_passes_through(self):
        """A ``None`` horizon (no pay periods) serializes to ``None``."""
        # pylint: disable=import-outside-toplevel
        from app.routes.savings import _serialize_horizon
        assert _serialize_horizon(None) is None

    def test_every_published_key_is_read(
        self, app, db, seed_user, seed_periods,
    ):
        """Every key the producer publishes is one this serializer consumes.

        The route's half of the contract plan step X-q2 established, and it is
        a MUTATION rather than a literal: each key is removed from a REAL
        producer payload in turn and the serializer must break on it.  A key a
        future step adds without a consumer therefore fails here, where a list
        of expected names would have passed -- which is finding N-100's own
        history, since ``horizon_end`` and ``is_loan_free`` shipped for months
        as producer outputs no serializer, template or script ever named.

        **It descends into the MILESTONE dicts too** (plan step X-s1, finding
        N-104).  Stopping at the top level is how the milestones' machine
        ``kind`` survived X-q2's certification: a dead key riding inside a live
        one, invisible to a guard that only removes top-level names.

        **The fixture carries BOTH milestone builders' output, and that took
        two corrections** (X-s's adversarial review).  A loan-free fixture
        publishes an EMPTY milestone list, so the descent would iterate nothing
        and pass while proving nothing -- hence the loan, and hence the count
        asserted before the loop rather than trusted (finding N-69's shape).
        With only the loan the list holds STRUCTURAL flags alone, so a key that
        just ``_net_crossing_milestones`` emits was still invisible.  The loan
        is therefore MORTGAGE-shaped (360 months): the horizon runs to its
        payoff plus a year, which is the axis length a ``$500k`` crossing needs
        room on -- a 24-month loan gives a ~3-year window the 401(k) cannot
        grow across, measured.  Both counts are asserted below for the same
        reason the first one is.

        The producer's half is two tests, because the two key sets are pinned
        where each has a non-vacuous fixture:
        ``TestNetWorthHorizon.test_publishes_only_the_keys_the_page_reads``
        pins the TOP-LEVEL set (its loan-free fixture publishes no milestone at
        all, so the nested set cannot be asserted there), and
        ``TestNetWorthHorizon.test_debt_free_milestone_at_payoff`` pins the
        MILESTONE set over a fixture that plants one.
        """
        # pylint: disable=import-outside-toplevel
        from app.routes.savings import _serialize_horizon
        from app.services import savings_dashboard_service
        from tests._test_helpers import make_investment_account
        with app.app_context():
            _create_small_loan(
                seed_user, name="Mortgage",
                principal=Decimal("200000.00"), term=360,
            )
            make_investment_account(
                seed_user, db.session, seed_periods[0], Decimal("300000.00"),
            )
            db.session.commit()
            horizon = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["net_worth"].horizon
            assert horizon is not None
            # The unmutated payload serializes, so a failure below is the
            # missing key and never a broken fixture.
            assert _serialize_horizon(horizon) is not None

            for key in sorted(horizon):
                partial = {
                    name: value for name, value in horizon.items()
                    if name != key
                }
                with pytest.raises(KeyError) as excinfo:
                    _serialize_horizon(partial)
                # The REMOVED key must be the one that raised: a bare
                # ``raises(KeyError)`` would accept an incidental miss from
                # some later refactor and go on reading as proof.
                assert excinfo.value.args[0] == key

            # The nested arm, on the same rule.  The loan's payoff is in the
            # future, so the producer plants at least the debt-free flag.
            assert horizon["milestones"], (
                "the fixture published no milestones, so the nested arm below "
                "would pass without removing anything"
            )
            # BOTH builders contributed, so the union below is a union of two
            # key sets and not one restated.  Told apart by what only a
            # crossing carries -- a "Net $" label -- since the machine ``kind``
            # that used to distinguish them is what X-s1 deleted.  A label is
            # user-collidable in general (finding N-110, ruled acceptable at
            # plan step X-t4: identify a flag by its ``(label, date)`` pair),
            # but this fixture names its own accounts, so the prefix separates
            # the two builders here.
            crossings = [
                m for m in horizon["milestones"]
                if m["label"].startswith("Net $")
            ]
            assert crossings, (
                "the fixture published no net-crossing milestone, so a key "
                "only that builder emits would be invisible below"
            )
            assert len(crossings) < len(horizon["milestones"]), (
                "the fixture published no STRUCTURAL milestone beside them"
            )
            # The UNION of every milestone's keys, not the first one's: the
            # structural flags and the net-worth crossings are built by two
            # different functions, so a key only one of them emits is invisible
            # to a probe that reads ``milestones[0]`` -- and the structural flag
            # sorts first here.  Found by X-s's adversarial review.
            milestone_keys = set()
            for milestone in horizon["milestones"]:
                milestone_keys |= set(milestone)
            for key in sorted(milestone_keys):
                stripped = [
                    {
                        name: value for name, value in milestone.items()
                        if name != key
                    }
                    for milestone in horizon["milestones"]
                ]
                with pytest.raises(KeyError) as excinfo:
                    _serialize_horizon({**horizon, "milestones": stripped})
                assert excinfo.value.args[0] == key


class TestMilestoneAxisX:
    """Tests for the milestone fractional-index helper (P-AC1 Loop B P2).

    ``_milestone_axis_x`` places a milestone (an exact date) on the Horizon
    stream's annual category axis as a fractional index, so the client's flag
    plugin can position the flag between the year-end samples via
    ``getPixelForValue``.
    """

    def test_positions_a_milestone_between_annual_samples(self):
        """A mid-year milestone maps to a fractional index between samples.

        Samples 2026-01-01 / 2026-12-31 / 2027-12-31; a milestone on
        2027-07-01 falls in the index-1..2 bracket, 182 of the 365 days from
        the index-1 sample, so x = 1 + 182 / 365.
        """
        # pylint: disable=import-outside-toplevel
        from datetime import date
        from app.routes.savings import _milestone_axis_x
        dates = [date(2026, 1, 1), date(2026, 12, 31), date(2027, 12, 31)]
        assert _milestone_axis_x(dates, date(2027, 7, 1)) == 1 + 182 / 365

    def test_exact_sample_and_out_of_range_dates_clamp(self):
        """A date on a sample lands on its index; out-of-range dates clamp."""
        # pylint: disable=import-outside-toplevel
        from datetime import date
        from app.routes.savings import _milestone_axis_x
        dates = [date(2026, 1, 1), date(2026, 12, 31), date(2027, 12, 31)]
        # The last sample -> the last index; the middle sample -> index 1.
        assert _milestone_axis_x(dates, date(2027, 12, 31)) == 2.0
        assert _milestone_axis_x(dates, date(2026, 12, 31)) == 1.0
        # Before the first sample -> 0.0; after the last -> the last index.
        assert _milestone_axis_x(dates, date(2020, 1, 1)) == 0.0
        assert _milestone_axis_x(dates, date(2030, 1, 1)) == 2.0


class TestSparklines:
    """Tests for the per-account sparkline SVG-geometry serialization."""

    def test_normalizes_series_to_inverted_svg_polyline(self):
        """A series maps to evenly-spaced x and an inverted y in the viewBox.

        Three descending points (100 -> 50 -> 0) draw a falling line: the
        high value sits at the top (y 0) and the low at the bottom (y 28),
        with x evenly spaced across the 100-wide box.
        """
        # pylint: disable=import-outside-toplevel
        from app.routes.savings import _serialize_sparklines
        result = _serialize_sparklines({
            7: [Decimal("100"), Decimal("50"), Decimal("0")],
        })
        # low=0, span=100, last=2:
        #  i0 v100 -> x 0,   y 28 - 28 = 0
        #  i1 v50  -> x 50,  y 28 - 14 = 14
        #  i2 v0   -> x 100, y 28 - 0  = 28
        assert result[7] == "0.00,0.00 50.00,14.00 100.00,28.00"

    def test_empty_sparklines_serialize_to_empty(self):
        """No informative accounts -> no polylines."""
        # pylint: disable=import-outside-toplevel
        from app.routes.savings import _serialize_sparklines
        assert _serialize_sparklines({}) == {}


class TestCockpitSection:
    """Tests for GET /savings/cockpit -- the balanceChanged refresh fragment."""

    def test_cockpit_section_renders_region_for_htmx(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An HX request returns the cockpit region fragment (hero + cards).

        The region carries the net-worth hero label and the account's
        click-to-edit balance cell, but NOT the ``#cockpit-section`` wrapper
        (that lives in the page) -- proving it is the fragment the
        balanceChanged swap consumes, not the whole page.

        **The hero's FIGURE is asserted at its own anchor** (plan step X-w3,
        rewritten at X-w6 after both adversarial reviews found the first
        version could not fire).

        Two things were wrong with that version and both are worth recording.
        Its arm was ``body.count("$1,000.00") >= 3``, and the fragment carries
        SEVEN occurrences of that figure -- at least three from outside the
        hero region (the account's balance cell, the category group-header
        subtotal, the legend's asset band), so all four hero reads could point
        at the wrong-but-present field and the count still passed.  That is
        ruling R-CF's class, a control that cannot fail, committed one step
        after R-CF.  And its stated reason was false: Jinja renders a bare
        ``{{ value }}`` on a missing attribute as an empty string, but the
        ``money`` macro opens with ``{% if value < 0 %}`` and
        ``Undefined.__lt__`` RAISES -- so a renamed money field 500s and the
        ``status_code == 200`` assertion above already covered that class.

        What is left uncovered by the status check, and is what this test now
        pins, is a producer returning the WRONG FIGURE: a hero reduced over an
        empty set reports ``$0.00`` with a 200.  So each figure is matched
        inside the element that names it.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            resp = auth_client.get(
                "/savings/cockpit", headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            body = resp.data.decode()
            assert "Net worth" in body
            # Seed Checking $1,000.00, no liabilities: hero == assets ==
            # liquid == $1,000.00, and the liability chip is a real $0.00.
            # Each figure is anchored to the element that renders it, so an
            # occurrence elsewhere on the page cannot satisfy the arm.
            assert re.search(
                r'nw-hero__num[^>]*>\s*\$1,000\.00', body,
            ), "the net-worth hero did not render $1,000.00"
            for label, figure in (
                ("Total assets", "$1,000.00"),
                ("Total liabilities", "$0.00"),
                ("Liquid", "$1,000.00"),
            ):
                assert re.search(
                    rf'pulse-chip__label">{label}</div>\s*'
                    rf'<div class="pulse-chip__value[^>]*>\s*'
                    rf'{re.escape(figure)}',
                    body,
                ), f"the {label} chip did not render {figure}"
            assert f'id="acct-balance-{acct_id}"' in body
            assert 'id="cockpit-section"' not in body

    def test_cockpit_section_non_htmx_redirects_to_dashboard(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A non-HX request redirects to the page (the section is a fragment)."""
        with app.app_context():
            resp = auth_client.get("/savings/cockpit")
            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/savings")

    def test_a_loan_cell_renders_its_caption_and_a_cash_cell_does_not(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The loan caption RENDERS, and only for the loan (plan step X-t1).

        The projection became a value object with a ``loan`` field that always
        EXISTS (``None`` for a non-loan), so every template predicate on it had
        to move from ``is defined`` to ``is not none``.  Getting that wrong is
        SILENT: Jinja renders an attribute on ``None`` -- and a mistyped
        attribute name -- as empty rather than raising, so the page still
        returns 200 with the caption simply gone (finding N-111 measured two of
        three template typos degrading that way).

        So this asserts the rendered FIGURES, not the absence of an exception:
        the loan's monthly payment and its rate reach the page, and the cash
        account's cell carries no payment caption at all.

        **Both failure modes were planted and measured** (X-t1): reverting the
        caption predicate to ``is defined`` reaches ``ad.loan.figures`` on the
        cash account -- ``'None' has no attribute 'figures'``, a 500 -- and
        mistyping the payment path raises inside the ``money`` macro rather
        than blanking, because a filter or macro call FORCES the value where a
        bare ``{{ ... }}`` would swallow it.  The status assertion catches the
        first and the figure assertions catch the second.
        """
        with app.app_context():
            loan = _create_small_loan(seed_user)
            db.session.commit()
            resp = auth_client.get(
                "/savings/cockpit", headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            body = resp.data.decode()

            # The loan's own figures, read off the SAME producer the page
            # renders, so this cannot go stale against an amortization change.
            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            loan_ad = next(
                ad for ad in data["account_data"] if ad.account.id == loan.id
            )
            payment = loan_ad.loan.figures.terms.monthly_payment
            assert payment > Decimal("0.00"), (
                "the fixture loan has no monthly payment, so the caption "
                "assertion below would pass on an empty render"
            )
            assert "Monthly Payment" in body
            assert f"${payment:,.2f}" in body
            # The rate caption, formatted as the template formats it
            # (``"%.3f"|format(rate|to_percent)``): 5.000% for this loan.
            rate_pct = loan_ad.loan.figures.terms.current_rate * Decimal("100")
            assert f"{rate_pct:.3f}%" in body

            # The cash cell: no loan caption anywhere in ITS cell.  Sliced from
            # the account-name anchor to the end of that cell so a loan caption
            # elsewhere on the page cannot satisfy it.
            cash_id = seed_user["account"].id
            cash_cell = body.split(f'id="acct-balance-{cash_id}"')[1]
            cash_cell = cash_cell.split("</div>\n    </div>")[0]
            assert "Monthly Payment" not in cash_cell


class TestCockpitBalance:
    """Tests for GET /savings/cockpit/<id>/balance -- the inline-edit revert cell."""

    def test_cockpit_balance_renders_editable_cell_for_htmx(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An HX request returns the account's click-to-edit balance cell.

        The cell carries its account-scoped id and opens the shared anchor
        editor in the cockpit (``accounts``) surface, so Cancel / Escape and
        the save round-trip thread back to this cockpit cell.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            resp = auth_client.get(
                f"/savings/cockpit/{acct_id}/balance",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            body = resp.data.decode()
            assert f'id="acct-balance-{acct_id}"' in body
            assert (
                f'hx-get="/accounts/{acct_id}/anchor-form?revert=accounts"'
                in body
            )

    def test_cockpit_balance_non_htmx_redirects_to_dashboard(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A non-HX request redirects to the dashboard page."""
        with app.app_context():
            acct_id = seed_user["account"].id
            resp = auth_client.get(f"/savings/cockpit/{acct_id}/balance")
            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/savings")

    def test_cockpit_balance_other_users_account_404(
        self, app, auth_client, seed_user, seed_second_user, seed_periods_today,
    ):
        """IDOR: another user's account id returns 404 (not found / not yours)."""
        with app.app_context():
            other_acct_id = seed_second_user["account"].id
            resp = auth_client.get(
                f"/savings/cockpit/{other_acct_id}/balance",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

    def test_cockpit_balance_loan_cell_takes_liability_ink(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A loan (liability) balance cell renders the danger-ink class.

        P-AC4: the owed balance is colored like the liabilities chip, group
        subtotal, and diverging-bar segment -- keyed on the account's
        category, never the figure's sign -- so a reverted loan cell keeps
        its danger ink.  The asset-cell contrast is asserted below.
        """
        with app.app_context():
            loan = _create_small_loan(seed_user)
            resp = auth_client.get(
                f"/savings/cockpit/{loan.id}/balance",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert "acct-card__num--liability" in resp.data.decode()

    def test_cockpit_balance_asset_cell_omits_liability_ink(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A Checking (asset) balance cell omits the liability danger-ink class."""
        with app.app_context():
            acct_id = seed_user["account"].id
            resp = auth_client.get(
                f"/savings/cockpit/{acct_id}/balance",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert "acct-card__num--liability" not in resp.data.decode()


class TestCockpitBalanceKindGate:
    """The D4 / A1 gate: a loan's cockpit balance cell is read-only."""

    def test_cockpit_balance_loan_cell_is_read_only(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A loan's cell shows the balance but never offers the anchor editor.

        Finding B-15's UI door: the cockpit's click-to-edit cell opened
        the CASH anchor editor for a loan card, whose save wrote
        ``accounts.current_anchor_balance`` on the loan.  The cell still
        renders the (ledger-derived) balance with its liability ink; the
        editor affordance (hx-get to anchor-form, the edit modifier, the
        pencil icon) is absent -- a loan's true-up lives on the loan page.
        """
        with app.app_context():
            loan = _create_small_loan(seed_user)
            resp = auth_client.get(
                f"/savings/cockpit/{loan.id}/balance",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            body = resp.data.decode()
            assert "anchor-form" not in body
            assert "acct-card__num--edit" not in body
            assert "acct-card__edit-icon" not in body
            # The balance itself still renders, with the liability ink.
            assert "acct-card__num--liability" in body
