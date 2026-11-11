"""The readiness what-if fragment, and the baselines the dashboard hands it."""

import re
from decimal import Decimal

from app.utils.error_fragments import DESIGNED_FRAGMENT_HEADER, RETARGET_HEADER

from ._helpers import _seed_overfunded, _seed_underfunded


class TestReadinessFragment:
    """Tests for the /retirement/readiness HTMX what-if fragment (P3a)."""

    def test_redirects_without_htmx(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET /retirement/readiness without HX-Request redirects."""
        resp = auth_client.get("/retirement/readiness")
        assert resp.status_code == 302
        assert "/retirement" in resp.headers["Location"]

    def test_requires_auth(self, client, db):
        """Unauthenticated -> redirect to login."""
        resp = client.get(
            "/retirement/readiness", headers={"HX-Request": "true"},
        )
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_baseline_fragment_has_verdict_and_no_deltas(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """No overrides: the verdict renders with both OOB siblings.

        Realigned (P3b): the countdown moved from the fragment to the page
        header, and a STANDALONE fragment response carries the income panel as
        an hx-swap-oob sibling so the meter always tracks the verdict.  No
        delta line renders without overrides.

        **The LEVER card is the second sibling since plan step C2-f2d-4**
        (ledger row **P59**), and this assertion inverted with the developer's
        ruling.  It used to read ``'data-lever=' not in html`` -- the levers
        rode along only when a stepper had moved, because they ignored the
        what-if sliders and so had no new answer to give.  They are solved
        against the displayed assumptions now, which means every refresh has a
        new answer and a card left un-refreshed would state a different plan
        from the verdict beside it.
        """
        _seed_underfunded(seed_user, db.session)
        resp = auth_client.get(
            "/retirement/readiness", headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'data-readiness="verdict"' in html
        assert "% funded" in html
        assert 'id="income-panel"' in html
        assert 'hx-swap-oob="innerHTML"' in html
        assert 'data-readiness="deltas"' not in html
        # GRADE THE ONE TOKEN THE FEATURE HANGS ON, as ONE string.  Asserting
        # the id and `hx-swap-oob` separately passes with the attribute
        # deleted -- `#income-panel` satisfies the second half -- and the whole
        # 9,572-test suite was MEASURED green with it removed (adversarial
        # design review, 2026-08-16), which is `lessons.md`'s N-199 on the very
        # same attribute name.  Without it the lever card never updates and
        # C2-f2d-4 does nothing at all in a browser.
        assert '<div id="levers-region" hx-swap-oob="innerHTML">' in html
        assert 'data-lever="contribution"' in html

    def test_the_levers_are_solved_at_the_what_if_the_hero_shows(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """One response, ONE set of assumptions -- plan step C2-f2d-4, row P59.

        The failure this replaces was visible on screen and captioned nowhere.
        The hero honoured the SWR / return / merit sliders while the lever card
        beside it always solved against the STORED settings, so a page showing
        "65.3% funded" under a 3.5% withdrawal rate told the owner to
        contribute the amount that closes the gap at 4%.  Both figures were
        correct; they were answers to different questions, presented as one
        plan.

        A tighter withdrawal rate makes the plan need MORE, so the solved
        contribution must RISE.  Asserted as a directional inequality against
        the same page's own stored-settings answer rather than against a
        literal, so the case cannot rot into a restatement of the seeded
        scenario's arithmetic.

        The retire-later lever is asserted through its OUTCOME LINES rather
        than through a solved month count, because this fixture is
        deliberately ``not_within_cap`` on that lever (see
        :func:`_seed_underfunded`) and so has no solved delay to compare.  The
        lines render in every state and carry that state's own figures, so a
        lever card that ignored the what-if would return them byte-identical.
        """
        _seed_underfunded(seed_user, db.session)

        def fragment(query):
            """The lever card's solved contribution and its outcome lines."""
            resp = auth_client.get(
                f"/retirement/readiness{query}", headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            amount = re.search(
                r"\+\$([\d,]+\.\d\d)<span class=\"retire-lever-unit\"> /paycheck",
                html,
            )
            assert amount, "the lever card did not render its solved default"
            lines = re.search(
                r'<div id="lever-outcomes".*?</div>', html, re.S,
            )
            assert lines, "the lever card did not render its outcome lines"
            return Decimal(amount.group(1).replace(",", "")), lines.group(0)

        stored_amount, stored_lines = fragment("")
        tighter_amount, tighter_lines = fragment("?swr=2")
        assert tighter_amount > stored_amount, (
            f"a 2% withdrawal rate left the solved contribution at "
            f"{tighter_amount} against {stored_amount} at the stored 4% -- the "
            "lever card is not solving against the displayed what-if"
        )
        assert tighter_lines != stored_lines, (
            "both lever outcome lines came back unchanged under a what-if "
            "that moved the verdict, so the card is stating a different plan "
            "from the hero above it"
        )

    def test_a_moved_stepper_keeps_the_owners_value_under_a_what_if(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """A what-if re-solves the DEFAULT and keeps the entered amount.

        The trap in refreshing the lever card on every slider move: the card
        holds the owner's own stepper entries, so an out-of-band swap that
        re-rendered it from the solved defaults would silently discard what
        they typed.  It does not, because the refresh request carries
        ``.lever-param`` and the producer echoes the submitted amount back --
        so the headline shows the newly solved default while the stepper shows
        the entry, which is the pair a reader needs to compare.
        """
        _seed_underfunded(seed_user, db.session)
        resp = auth_client.get(
            "/retirement/readiness?swr=2&contribution=250.00",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'id="lever-contribution"' in html
        assert 'value="250.00"' in html, (
            "the what-if refresh discarded the owner's stepper entry"
        )
        # And the OUTCOME line states the entered amount, not the solved one.
        assert "+$250.00/paycheck" in html

    def test_swr_override_renders_deltas(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A what-if SWR renders the baseline-vs-override delta line.

        swr=2 (vs the stored 4%) doubles the required target (required =
        gap * 12 / swr), so the funded ratio drops and the delta block
        must render with the "Was ...% funded" baseline and the
        points/dollars changes.
        """
        _seed_underfunded(seed_user, db.session)
        resp = auth_client.get(
            "/retirement/readiness?swr=2", headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'data-readiness="deltas"' in html
        assert "Was " in html
        assert "points" in html

    def test_lever_override_includes_lever_lines(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """months=24 additionally renders the lever outcome lines."""
        _seed_underfunded(seed_user, db.session)
        resp = auth_client.get(
            "/retirement/readiness?months=24",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'data-lever="retire-later"' in html
        assert "+24 months" in html
        # L5b (locked anatomy): the outcome line carries the shortfall
        # dollars -- deterministic sign for _seed_underfunded, whose
        # funded ratio stays far below 1 at +24 months.
        assert "shortfall" in html

    def test_contribution_override_renders_full_lever_content(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A contribution stepper value renders both lever lines in full.

        Consolidated from the retired /retirement/levers fragment tests
        (M2): the readiness fragment is now the ONE rendering path for
        _lever_outcomes.html.  contribution=100.00 renders the displayed
        amount via the money macro WITHOUT the over-headroom flag ($100 is
        under the 23500 / 26 = $903.85 per-period limit headroom -- the
        flag applies to the DISPLAYED amount, the documented P2 contract);
        with no months override the retire-later line reports the
        not-within-180-months degenerate state (a $10k account at 7%
        cannot reach a ~$1.4M target).
        """
        _seed_underfunded(seed_user, db.session)
        resp = auth_client.get(
            "/retirement/readiness?contribution=100.00",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "+$100.00/paycheck" in html
        assert 'data-lever-flag="headroom"' not in html
        assert "cannot close the gap within 180 months" in html

    def test_solved_contribution_line_renders_headroom_flag(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """With no contribution override the SOLVED amount is flagged.

        Consolidated from the retired /retirement/levers fragment tests
        (M2): a months-only request computes the levers with the
        contribution stepper at its solved default (~$1,200+/period for
        _seed_underfunded's ~$1.3M shortfall over an AF of ~1,100), which
        exceeds the 23500 / 26 = $903.85 per-period limit headroom -- the
        honest over-headroom flag renders with the headroom figure, and
        the solved line reports 100.0% funded.
        """
        _seed_underfunded(seed_user, db.session)
        resp = auth_client.get(
            "/retirement/readiness?months=24",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "100.0% funded" in html
        assert 'data-lever-flag="headroom"' in html
        assert "$903.85" in html

    def test_funded_baseline_with_months_override_renders_its_outcome(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A typed months override renders ITS outcome when already funded.

        Acceptance fix 3: pre-fix the already_funded branch swallowed the
        override and re-rendered "no delay needed".  With the $5M
        overfunded scenario and months=24, the line must show the
        override's own picture ("+24 months (retiring <date>) reaches
        X% funded (surplus)") -- funded stays >= 100%, so the surplus
        wording is deterministic -- and the baseline copy must be gone.
        """
        _seed_overfunded(seed_user, db.session)
        resp = auth_client.get(
            "/retirement/readiness?months=24",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "+24 months" in html
        assert "surplus" in html
        assert "no delay needed" not in html

    def test_funded_baseline_with_contribution_override_renders_its_outcome(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A typed contribution override renders ITS outcome when funded.

        The contribution twin of acceptance fix 3: contribution=100.00
        on the overfunded baseline renders "+$100.00/paycheck ... reaches
        X% funded (surplus)" instead of the swallowed "no extra
        contribution needed" copy.
        """
        _seed_overfunded(seed_user, db.session)
        resp = auth_client.get(
            "/retirement/readiness?contribution=100.00",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "+$100.00/paycheck" in html
        assert "no extra contribution needed" not in html

    def test_garbage_params_are_422(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Out-of-bounds what-if params are the assumptions rail at 422.

        Every refusal of this GET is the rail, re-rendered with each message
        on its control and RETARGETED at the rail's region, since plan step
        salary:S3-f-4 (ruling **R-SAL33**; it was a JSON no panel swapped
        in).  The SWR box renders its refusal; the lever steppers are
        controls outside the rail, so theirs render nowhere yet -- the
        response is still the rail, and the rail is clean.
        """
        for query, refused_control in (
            ("swr=-5", "safe_withdrawal_rate"),     # negative percent
            ("months=181", None),                   # above the +180 solver cap
            ("months=abc", None),                   # non-numeric offset
            ("contribution=-1", None),              # negative money
            ("contribution=100001", None),          # above the 100000 bound
            ("contribution=abc", None),             # non-numeric money
        ):
            resp = auth_client.get(
                f"/retirement/readiness?{query}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 422, (
                f"{query}: expected 422, got {resp.status_code}"
            )
            assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1", query
            assert resp.headers.get(RETARGET_HEADER) == "#assumptions-region", query
            html = resp.data.decode()
            assert 'id="assumptions-panel"' in html, query
            if refused_control is None:
                assert "is-invalid" not in html, query
                continue
            control = re.search(
                r'<input[^>]*name="%s"[^>]*>' % refused_control, html,
            ).group(0)
            assert "is-invalid" in control, query


class TestDashboardReadinessContext:
    """P3a: the dashboard passes the direction-D baselines to the template."""

    def test_dashboard_context_carries_readiness_and_levers(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """The render context gains readiness + levers; the page still 200s.

        The current dashboard.html ignores both keys (zero template edits
        in P3a), so the assertion captures the render context via the
        template_rendered signal instead of scraping HTML.
        """
        from flask import template_rendered

        captured = []

        def record(sender, template, context, **extra):
            if template.name == "retirement/dashboard.html":
                captured.append(context)

        template_rendered.connect(record, app)
        try:
            resp = auth_client.get("/retirement")
            assert resp.status_code == 200
        finally:
            template_rendered.disconnect(record, app)

        assert captured, "retirement/dashboard.html was not rendered"
        context = captured[-1]
        assert "readiness" in context
        assert "levers" in context
        # Shape spot-checks: the producers' signature keys are present.
        assert "funded_ratio" in context["readiness"]
        assert "no_horizon" in context["levers"]
