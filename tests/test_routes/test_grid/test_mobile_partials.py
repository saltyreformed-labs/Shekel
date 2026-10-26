"""Grid route tests: the mobile this-period partial, the card action bar, and the absent swipe
affordances.
"""

from decimal import Decimal

from app.extensions import db
from app.routes._render_helpers import fragment_amounts
from app.services.pay_calendar import DerivedPeriod, calendar_for

from tests._test_helpers import (
    APP_PACKAGE_DIR,
    current_pay_period,
    derived_span,
    last_covered_day,
    one_off_row_of,
    settle_day_columns,
)


class TestMobileThisPeriodPartial:
    """Regression locks for the mobile "This Period" tab partial.

    The partial at ``app/templates/grid/_mobile_this_period.html`` is
    rendered inside the ``#mobile-this-period`` tab-pane in
    ``_mobile_grid.html``.  These tests assert structural invariants
    of the rendered HTML so subsequent commits cannot silently regress
    the tab layout (default-active flip, period nav arrow hrefs, the
    presence of the income / expense / net / balance sections).

    Mobile / desktop split: ``_mobile_grid.html`` is wrapped in
    ``d-md-none`` and the desktop grid in ``d-none d-md-block``; both
    render server-side regardless of the requesting client, so the
    assertions can inspect the response body without simulating a
    mobile user-agent or viewport.
    """

    def test_this_period_partial_exists(self):
        """C6-1: the new partial file exists at the canonical path.

        Filesystem check; ensures the file landed at the path the
        ``{% include "grid/_mobile_this_period.html" %}`` reference in
        ``_mobile_grid.html`` resolves to.
        """
        # Found from the app package's own location, never from this test's
        # place in the tree.
        partial = APP_PACKAGE_DIR / "templates" / "grid" / "_mobile_this_period.html"
        assert partial.is_file()

    def test_default_active_tab_is_this_period(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C6-5: the "This Period" pill is the default-active tab.

        The Commit 6 default-tab flip moves the ``active`` /
        ``aria-selected="true"`` pair from "Plan" to "This Period";
        the matching tab-pane carries ``show active``.  Lock both so
        a later commit cannot silently flip the default back.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            # Slice each button's full opening tag ('<button ... >') so
            # the assertions span the template's multi-line attribute
            # layout.
            tp_id = 'id="mobile-tab-this-period"'
            tp_open = body[body.rindex("<button", 0, body.index(tp_id)):
                           body.index(">", body.index(tp_id))]
            assert "nav-link active" in tp_open
            assert 'aria-selected="true"' in tp_open

            plan_id = 'id="mobile-tab-plan"'
            plan_open = body[body.rindex("<button", 0, body.index(plan_id)):
                             body.index(">", body.index(plan_id))]
            # Plan tab carries the bare "nav-link" class (no "active").
            assert "nav-link active" not in plan_open
            assert 'aria-selected="false"' in plan_open

            # The tab-pane carries "show active" via its outer class.
            pane_id = 'id="mobile-this-period"'
            pane_open = body[body.rindex("<div", 0, body.index(pane_id)):
                             body.index(">", body.index(pane_id))]
            assert "show active" in pane_open

    def test_this_period_renders_current_period_by_default(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C6-2: the partial renders periods[0] (== current period when
        start_offset == 0).

        At the default URL ``/grid`` the visible window starts at
        ``current_period`` (offset=0), so the period label inside the
        partial's nav header equals ``current_period.label``.
        """
        with app.app_context():

            current = current_pay_period(
                seed_user["user"].id,
            )
            assert current is not None

            response = auth_client.get("/grid")
            assert response.status_code == 200

            # The partial's header div is followed by the period
            # label inside a fw-bold div.  Encode the label so non-ASCII
            # whitespace and quoting are byte-stable.
            #
            # The label comes off the CALENDAR since pay-calendar plan step
            # C4-a-5 deleted ``PayPeriod.label``: the partial renders
            # ``DerivedPeriod.label`` (``grid/_mobile_this_period.html``), and
            # ``current_pay_period`` returns the ORM ROW on purpose because the
            # factories take one.  The two name the same paycheck by
            # construction -- that helper resolves the row FROM the derivation
            # -- so this reads the derived value for the id it just resolved.
            derived = calendar_for(
                seed_user["user"].id,
            ).period_by_id(current.id)
            assert derived.label.encode("utf-8") in response.data
            # The partial-specific collapse IDs prefix with mobile-tp-
            # to avoid colliding with the Plan tab's mobile-income-/mobile-expense-.
            assert f"mobile-tp-income-{current.id}".encode("utf-8") in response.data
            assert f"mobile-tp-expense-{current.id}".encode("utf-8") in response.data

    def test_this_period_includes_income_expense_net_balance(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C6-3: the partial emits the four expected sections.

        Income card (header text "Income"), expense card (header text
        "Expenses"), net cash flow bar ("Net Cash Flow"), projected
        balance card ("Projected Balance").  The mobile-section classes
        carry the brand colors so they double as section markers.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            # Slice to just the This Period pane so the assertions do
            # not leak through to the Plan pane's symmetric markup.
            pane_start = body.index('id="mobile-this-period"')
            # The pane ends at the next sibling tab-pane (mobile-plan).
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            assert "mobile-section-income" in pane
            assert "mobile-section-expense" in pane
            assert "Net Cash Flow" in pane
            assert "Projected Balance" in pane

    def test_this_period_arrows_link_to_offset_neighbors(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C6-4: the prev/next arrows link to offset-1 and offset+1.

        At the default URL ``/grid`` (``start_offset == 0``), the
        partial's ``[<]`` should link to
        ``/grid?periods=1&offset=-1#this-period`` and ``[>]`` to
        ``/grid?periods=1&offset=1#this-period``.  Both carry the
        ``#this-period`` fragment so the page lands back on the same
        tab after the GET.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-this-period"')
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            # Flask url_for renders integer query args inline; assert
            # the canonical href tail rather than the full URL.
            assert "/grid?periods=1&amp;offset=-1#this-period" in pane
            assert "/grid?periods=1&amp;offset=1#this-period" in pane

    def test_this_period_arrows_use_start_offset(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C6-4 (extended): arrows always step from ``start_offset``.

        When the user is at ``?periods=1&offset=2``, the prev arrow
        links to ``offset=1`` and the next arrow to ``offset=3``.  The
        partial uses ``start_offset`` directly, so the formula must
        survive non-zero starting offsets.
        """
        with app.app_context():
            response = auth_client.get("/grid?periods=1&offset=2")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-this-period"')
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            assert "/grid?periods=1&amp;offset=1#this-period" in pane
            assert "/grid?periods=1&amp;offset=3#this-period" in pane


class TestMobileCardActionBar:
    """Regression locks for the per-card inline action bar (Commit 7
    of the mobile-first v3 implementation).

    The bar lives in ``app/templates/grid/_mobile_card_actions.html``
    and is emitted by ``render_row_card`` as a sibling of each card
    ``<li>``, wrapped together in
    ``<div class="mobile-card-wrapper">``.  A delegated tap handler in
    ``app/static/js/mobile_grid.js`` toggles the Bootstrap collapse so
    the user sees ``[Mark Paid]`` and ``[Open Full]`` directly under
    the tapped card.  (The original ``[Edit Amount]`` button was
    removed in the C3 rebuild, audit item C2: its swap target
    ``#txn-cell-<id>`` lives inside the CSS-hidden desktop table, so
    the form it loaded was never visible on mobile -- confirmed dead
    live on 2026-06-11 -- and the Open Full action card now carries
    the amount inputs directly.)

    These tests pin down the structural contract the JS handler and
    the action-bar route consumers depend on:

      - Both new partials (``_mobile_plan.html`` and
        ``_mobile_card_actions.html``) exist at the canonical paths.
      - The Mark Paid form is conditional on the transaction state -
        Projected / Received and other non-terminal statuses get it;
        Done and Settled (the two state-machine terminals for the
        mark-done path) do not (mark_done would reject them via the
        state machine, so omitting the affordance is the honest UX).
      - ``can_edit=False`` (the companion contract per R-7 / D-B of
        the v3 plan) drops the owner-only ``[Open Full]`` button while
        keeping ``[Mark Paid]`` (companions are allowed to mark paid
        per the existing entries-blueprint precedent); ``Edit Amount``
        stays asserted absent for every render path.
      - The Mark Paid form posts to ``transactions.mark_done`` with
        the swap target set to the row's ``#txn-cell-<id>``.
    """

    @staticmethod
    def _render_action_bar(app, txn, can_edit=True):
        """Render ``_mobile_card_actions.html`` directly with the given
        ``txn`` and ``can_edit``.

        Direct render (rather than scraping a full ``/grid`` response)
        keeps the structural assertions immune to surrounding markup
        drift: the test asserts what the partial emits, not where
        it lands in the larger page.  ``app.test_request_context``
        is what makes ``url_for`` resolve inside the template; the
        ``app.jinja_env.globals`` registrations from
        ``app.jinja_globals.register_ref_id_globals`` provide
        ``STATUS_DONE`` / ``STATUS_SETTLED`` without further setup.
        """
        template = app.jinja_env.get_template("grid/_mobile_card_actions.html")
        with app.test_request_context("/"):
            return template.render(txn=txn, can_edit=can_edit)

    def test_plan_partial_exists(self):
        """C7-1: ``_mobile_plan.html`` exists at the canonical path.

        Filesystem check; ensures the partial landed where the
        ``{% include "grid/_mobile_plan.html" %}`` reference in
        ``_mobile_grid.html`` resolves to.
        """
        # Found from the app package's own location, never from this test's
        # place in the tree.
        partial = APP_PACKAGE_DIR / "templates" / "grid" / "_mobile_plan.html"
        assert partial.is_file()

    def test_mobile_card_actions_partial_exists(self):
        """C7-2: ``_mobile_card_actions.html`` exists at the canonical path.

        Filesystem check; ensures the partial landed where the
        ``{% include "grid/_mobile_card_actions.html" %}`` reference
        in ``_grid_row_macros.html``'s ``render_row_card`` resolves to.
        """
        # Found from the app package's own location, never from this test's
        # place in the tree.
        partial = APP_PACKAGE_DIR / "templates" / "grid" / "_mobile_card_actions.html"
        assert partial.is_file()

    def test_action_bar_includes_mark_paid_when_not_settled(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-3: Projected txns get a ``[Mark Paid]`` form in the bar.

        A Projected transaction is in scope for the mark-done state
        transition, so the action bar offers the affordance.  The
        rendered partial must contain a ``hx-post`` form to
        ``transactions.mark_done`` plus the visible button label.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = current_pay_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = one_off_row_of(
                current,
                name="C7-3 Projected Groceries",
                amount=Decimal("42.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()

            rendered = self._render_action_bar(app, txn, can_edit=True)

            assert f'/transactions/{txn.id}/mark-done' in rendered
            assert "Mark Paid" in rendered

    def test_action_bar_excludes_mark_paid_when_paid(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-4: Paid txns do NOT get a ``[Mark Paid]`` form.

        The button would fire an identity re-submit at best and, on the row
        types that settle from entries, a re-price of money that already
        moved.

        **The partial's guard is ``status_id == STATUS_PROJECTED``**, which is
        narrower than the settled band and is the source of truth here: Mark
        Paid is suppressed on Credit and Cancelled rows too.  The specimen was
        ``Settled`` -- the terminal archive -- until plan step
        **balance:X-am** deleted that status; Paid is the same specimen because
        it is equally not-Projected.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = current_pay_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = one_off_row_of(
                current,
                name="C7-4 Paid Groceries",
                amount=Decimal("42.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Groceries"].id,
            )
            # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
            # record is the covering movement written after the flush (X-bi-4b-2).
            txn.status_id = ref_cache.status_id(StatusEnum.DONE)
            for _column, _value in settle_day_columns(current.start_date).items():
                setattr(txn, _column, _value)
            db.session.commit()

            rendered = self._render_action_bar(app, txn, can_edit=True)

            assert f'/transactions/{txn.id}/mark-done' not in rendered
            assert "Mark Paid" not in rendered

    def test_action_bar_excludes_mark_paid_when_done(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-4 (sibling): Done txns also drop the ``[Mark Paid]`` form.

        Mirrors the Settled guard: Done is the other terminal for the
        mark-done path (Done -> Settled is a separate transition,
        and the action bar does not currently expose a Settle action).
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = current_pay_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = one_off_row_of(
                current,
                name="C7-4b Done Groceries",
                amount=Decimal("42.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Groceries"].id,
            )
            # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
            # record is the covering movement written after the flush (X-bi-4b-2).
            txn.status_id = ref_cache.status_id(StatusEnum.DONE)
            for _column, _value in settle_day_columns(current.start_date).items():
                setattr(txn, _column, _value)
            db.session.commit()

            rendered = self._render_action_bar(app, txn, can_edit=True)

            assert f'/transactions/{txn.id}/mark-done' not in rendered
            assert "Mark Paid" not in rendered

    def test_action_bar_excludes_mark_paid_when_received(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-4c: income txns marked Received also drop ``[Mark Paid]``.

        Locks the fix for an income-specific bug that the Playwright
        harness surfaced after a mark-done round-trip:
        ``transactions.mark_done`` sets ``status_id = RECEIVED`` for
        income (not DONE), so the spec's literal
        ``status_id != STATUS_DONE and status_id != STATUS_SETTLED``
        guard missed RECEIVED and kept the Mark Paid button visible
        on already-received income.  Switched to the semantic
        ``Status.is_settled`` boolean, which covered Paid, Received and
        Settled uniformly then and covers the two that remain now.  (The
        DESKTOP action bar has since narrowed further, to
        ``status_id == STATUS_PROJECTED``; this case is the MOBILE card's,
        whose guard is the boolean.  Plan step **balance:X-am** deleted
        ``STATUS_SETTLED`` and neither partial needed an edit.)
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = current_pay_period(
                seed_user["user"].id,
            )
            assert current is not None
            salary_cat = seed_user["categories"]["Salary"]
            txn = one_off_row_of(
                current,
                name="C7-4c Received Salary",
                amount=Decimal("2500.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                category_id=salary_cat.id,
            )
            # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
            # record is the covering movement written after the flush (X-bi-4b-2).
            txn.status_id = ref_cache.status_id(StatusEnum.RECEIVED)
            for _column, _value in settle_day_columns(current.start_date).items():
                setattr(txn, _column, _value)
            db.session.commit()

            rendered = self._render_action_bar(app, txn, can_edit=True)

            assert f'/transactions/{txn.id}/mark-done' not in rendered
            assert "Mark Paid" not in rendered

    def test_action_bar_excludes_edit_when_can_edit_false(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-5: ``can_edit=False`` (companion) drops ``[Open Full]``
        but keeps ``[Mark Paid]``.

        The companion role can mark transactions paid (entries
        blueprint precedent) but cannot open the full-edit action
        card.  The action bar partial's ``{% if can_edit %}`` guard is
        the only thing between the companion render path and the
        owner-only affordance.  ``Edit Amount`` is asserted absent
        here too -- it was removed for every render path in the C3
        rebuild (audit item C2, dead target in the hidden desktop
        table), so its absence is now unconditional.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = current_pay_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = one_off_row_of(
                current,
                name="C7-5 Projected Groceries",
                amount=Decimal("99.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()

            rendered = self._render_action_bar(app, txn, can_edit=False)

            # Mark Paid remains -- companions can mark paid.
            assert f'/transactions/{txn.id}/mark-done' in rendered
            assert "Mark Paid" in rendered
            # Edit Amount and Open Full are gone for the companion path.
            assert "Edit Amount" not in rendered
            assert "Open Full" not in rendered
            assert f'/transactions/{txn.id}/quick-edit' not in rendered
            assert "txn-expand-btn" not in rendered

    def test_action_bar_hx_post_target_is_cell(
        self, app, seed_user, seed_periods_today,
    ):
        """Mark Paid form posts to mark-done targeting the card wrapper.

        Locks the form attributes the action bar's HTMX wiring depends
        on: the ``hx-post`` URL, the ``hx-target`` (the card wrapper id
        ``#card-<prefix-><id>`` -- here prefix-less because
        ``_render_action_bar`` renders the macro without an
        ``id_prefix``), the ``outerHTML`` swap mode, and the hidden
        ``render=mobile_card`` field that routes the response to the
        in-place single-card render (+ ``HX-Trigger: mobileCardSettled``)
        instead of the desktop cell + full-page-reload ``gridRefresh``.
        The old ``#txn-cell-<id>`` target only existed in the
        CSS-hidden desktop table on /grid (and not at all on the
        companion page), so the swap was effectively dead before this
        change.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = current_pay_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = one_off_row_of(
                current,
                name="C7-6 Projected Groceries",
                amount=Decimal("42.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()

            rendered = self._render_action_bar(app, txn, can_edit=True)

            assert f'hx-post="/transactions/{txn.id}/mark-done"' in rendered
            # Prefix-less wrapper id (the helper renders the macro with
            # no id_prefix); the This Period tab uses card-tp-<id>.
            assert f'hx-target="#card-{txn.id}"' in rendered
            assert 'hx-swap="outerHTML"' in rendered
            assert 'name="render" value="mobile_card"' in rendered

    def test_card_wrapper_emits_expansion_sibling_in_grid_page(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C7-integration: the full ``/grid`` page emits the expansion
        sibling next to each mobile card.

        Asserts the macro-level wiring: ``render_row_card`` wraps
        each ``<li>`` in ``<div class="mobile-card-wrapper">`` and
        emits a sibling ``<div class="collapse mobile-card-expansion">``
        bundling progress detail, envelope entries, and the action
        button row.  Without this integration, the unit-level checks
        above would pass while the expansion still never appears on
        the rendered page.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = current_pay_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = one_off_row_of(
                current,
                name="C7-integration Projected Groceries",
                amount=Decimal("31.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()
            txn_id = txn.id

            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            # Wrapper exists; This Period tab carries the
            # per-tab-prefixed expansion id (`tp` prefix).  The Plan
            # tab no longer renders cards via ``render_row_card`` --
            # it uses the narrower ``render_row_static`` (no
            # expansion wrapper, no per-card collapse) -- so the
            # ``plan`` prefix variant is no longer emitted on the
            # rendered page.  The ``id_prefix`` parameter on the
            # macro itself still supports a ``plan`` value (locked
            # by ``test_card_expansion_id_uses_prefix_when_supplied``)
            # in case a future caller needs it; the integration test
            # here pins only the live page contract.
            assert 'class="mobile-card-wrapper"' in body
            assert f'id="card-expansion-tp-{txn_id}"' in body

    def test_card_expansion_id_uses_prefix_when_supplied(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-integration: ``id_prefix`` namespaces the expansion wrapper id.

        The "This Period" and "Plan" tabs render the same window of
        pay periods at the same time, so without per-tab namespacing
        the same txn yields a duplicate ``id="card-expansion-<id>"``
        in two places.  The ``id_prefix`` parameter on
        ``render_row_card`` is the fix: This Period passes
        ``id_prefix='tp'``, Plan passes ``id_prefix='plan'``, and an
        empty prefix preserves the simpler unprefixed form for
        direct-render call sites.

        Locks the three branches explicitly so a future refactor of
        the formula cannot regress one without flagging.  Renders the
        macro through a tiny ``from_string`` template because the
        per-tab id wiring now lives on the wrapper emitted by
        ``render_row_card`` rather than the inner action-button
        partial (the partial used to host its own collapse; the
        unified collapse covers progress + entries + actions and is
        owned by the macro).
        """
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel

        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = current_pay_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = one_off_row_of(
                current,
                name="C7-prefix Projected Groceries",
                amount=Decimal("17.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()

            rk = SimpleNamespace(
                category_id=txn.category_id,
                template_id=None,
                txn_name=txn.name,
                display_name=txn.name,
            )
            period = DerivedPeriod(
                period_id=current.id, period_index=derived_span(current).period_index,
                start_date=current.start_date, end_date=last_covered_day(current),
                end_is_projected=False,
            )
            matched = {
                (
                    rk.category_id, rk.template_id, rk.txn_name,
                    period.period_id,
                ): [txn],
            }

            # The card reads its amount off a published ``budgets`` map
            # since plan step X-au-c2b, where it read a transient
            # ``txn.live_estimated_amount`` behind an ``is defined`` guard.
            # Resolved through the app's own door so the figure rendered here
            # is the one the grid would show.
            # ``settled`` beside it since plan step X-au-c3: the card shows
            # what a row RECORDED when its money has moved, so the macro takes
            # both maps and this render supplies both from the same door.
            amounts = fragment_amounts(txn)
            budgets, settled = amounts.budgets, amounts.settled
            retained = amounts.retained
            tmpl = app.jinja_env.from_string(
                "{% from 'grid/_grid_row_macros.html'"
                " import render_row_card %}"
                "{{ render_row_card(rk, period, matched, {}, budgets, settled,"
                " retained, none, can_edit, prefix) }}",
            )
            with app.test_request_context("/"):
                no_prefix = tmpl.render(
                    rk=rk, period=period, matched=matched, budgets=budgets,
                    settled=settled, retained=retained, can_edit=True, prefix="",
                )
                tp_prefix = tmpl.render(
                    rk=rk, period=period, matched=matched, budgets=budgets,
                    settled=settled, retained=retained, can_edit=True, prefix="tp",
                )
                plan_prefix = tmpl.render(
                    rk=rk, period=period, matched=matched, budgets=budgets,
                    settled=settled, retained=retained, can_edit=True, prefix="plan",
                )

            assert f'id="card-expansion-{txn.id}"' in no_prefix
            assert f'id="card-expansion-tp-{txn.id}"' in tp_prefix
            assert f'id="card-expansion-plan-{txn.id}"' in plan_prefix
            # Prefixed renders must NOT also emit the unprefixed form.
            assert f'id="card-expansion-{txn.id}"' not in tp_prefix
            assert f'id="card-expansion-{txn.id}"' not in plan_prefix

    def test_mobile_grid_includes_plan_partial(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``_mobile_grid.html`` Plan tab body is the
        ``_mobile_plan.html`` include, not inline content.

        The Plan tab body lives in its own partial (Commit 7 of the
        mobile-first v3 implementation; the partial's shape was later
        replaced with a read-only multi-period summary accordion).
        This test pins the call-site wiring by looking for the
        accordion container the partial emits when there is at least
        one plan period.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-plan"')
            pane = body[pane_start:]
            # The Plan accordion container is the partial's load-
            # bearing identifier: every plan-period card carries
            # ``data-bs-parent="#plan-accordion"`` and the mutual-
            # exclusion behavior depends on this id being present.
            assert 'id="plan-accordion"' in pane

    def test_no_inline_style_attr_in_mobile_card_actions(self):
        """Pin the no-inline-style invariant on every button in the
        mobile card action bar.  The project's CSP at
        ``app/__init__.py`` declares ``style-src 'self'`` without
        ``'unsafe-inline'``, so any ``style="..."`` attribute is
        silently dropped by the browser -- which previously left the
        Mark Paid / Edit Amount / Open Full buttons at Bootstrap's
        default ``btn-sm`` height (~38-40 px), 4-6 px short of the
        WCAG 2.5.5 / Apple HIG 44 px touch-target floor enforced by
        the mobile-first v3 plan hard-rule 7.

        The 44 px floor now travels via the ``.btn-touch-44`` utility
        class defined in ``app/static/css/grid.css`` inside the
        ``@media (max-width: 767.98px)`` block.  A regression that
        re-introduced an inline ``style="..."`` would silently shrink
        the buttons; this lock catches the regression at the source.
        """
        # Found from the app package's own location, never from this test's
        # place in the tree, and the read must hold text: a "not in" over an
        # empty read passes on nothing.
        partial = APP_PACKAGE_DIR / "templates" / "grid" / "_mobile_card_actions.html"
        src = partial.read_text(encoding="utf-8")
        assert src, f"read nothing from {partial}"
        assert "style=" not in src, (
            "Inline style= attribute reintroduced into "
            "_mobile_card_actions.html; the project CSP blocks it. "
            "Use the .btn-touch-44 utility class for the 44 px floor."
        )


class TestMobileNoSwipeAffordances:
    """Negative regression locks: swipe-driven affordances are gone.

    Two swipe-driven behaviors used to live in ``mobile_grid.js``:

      - **swipe-to-mark-paid** on individual cards (Commit 9 of the
        mobile-first v3 implementation), removed after Firefox iOS
        leaked the reveal button at rest, WebKit-based browsers
        layered it behind the amount text mid-swipe, and the
        accidental-tap risk on a state-mutating button outweighed
        the one-tap shortcut.  Mark Paid is reachable through the
        inline action bar that expands when the user taps a card.
      - **period-navigation swipe** on the Plan tab-pane, removed
        when the Plan tab was rebuilt as a read-only multi-period
        accordion (the panel-swap logic the gesture drove was the
        broken bit -- a single Next tap left the next panel hidden
        behind a ``d-none`` class that ``style.display=''`` could
        not override).  Period navigation now lives on the This
        Period tab via URL-driven arrows + a jump-to ``<select>``.

    These tests pin the negative contract: neither affordance
    re-appears via a future refactor.  Lifecycle artifacts
    (panel-swap state, swipe handlers, ``Math.abs(dx) > 50``
    threshold, ``.mobile-period-panel`` containers) are all gone
    from ``mobile_grid.js`` and the Plan partial.
    """

    def test_no_swipe_action_button(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Regression lock: the swipe-action-mark-paid button is
        gone from the rendered mobile grid.

        A Projected envelope card is the macro's most permissive
        emission path (it took the button when the feature
        existed); rendering the live ``/grid`` page and asserting
        the absence of the class is the cheapest way to catch a
        revert that re-enables the affordance.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = current_pay_period(
                seed_user["user"].id,
            )
            assert current is not None
            one_off_row_of(
                current,
                name="No-swipe Projected Groceries",
                amount=Decimal("23.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()

            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            assert 'class="swipe-action-mark-paid"' not in body
            # The .mobile-card-row wrapper that only existed to
            # scope the swipe button's containing block is also
            # gone; .mobile-card-wrapper is now the only mobile
            # card grouping div.
            assert 'class="mobile-card-row"' not in body

    def test_no_panel_swap_artifacts_in_mobile_grid_js(self):
        """The panel-swap navigation that used to drive the Plan tab
        is gone from ``mobile_grid.js``.

        The Plan tab is now a read-only multi-period accordion -- a
        Bootstrap collapse stack with ``data-bs-parent`` handling
        mutual exclusion declaratively.  No panel index, no
        ``navigate()`` function, no ``updateLabel()``, no
        ``mobile-prev-btn`` / ``mobile-next-btn`` handlers, no
        ``passive: true`` touch listeners, no ``Math.abs(dx) > 50``
        swipe threshold.  This lock catches a revert that
        re-introduces any of those artifacts.
        """
        # Found from the app package's own location, never from this test's
        # place in the tree, and the read must hold text: a "not in" over an
        # empty read passes on nothing.
        js_path = APP_PACKAGE_DIR / "static" / "js" / "mobile_grid.js"
        mobile_grid_src = js_path.read_text(encoding="utf-8")
        assert mobile_grid_src, f"read nothing from {js_path}"

        # Negative lock on every artifact of the dead panel-swap nav.
        assert "Math.abs(dx) > 50" not in mobile_grid_src
        assert "passive: true" not in mobile_grid_src
        assert "mobile-prev-btn" not in mobile_grid_src
        assert "mobile-next-btn" not in mobile_grid_src
        assert ".mobile-period-panel" not in mobile_grid_src
        assert "navigate(" not in mobile_grid_src
        assert "updateLabel" not in mobile_grid_src

    def test_no_panel_swap_markup_in_mobile_plan_partial(self):
        """The ``.mobile-period-panel`` containers and prev/next
        buttons are gone from the Plan partial.

        Before the read-only summary accordion landed, the partial
        rendered one ``<div class="mobile-period-panel">`` per
        period with sibling ``[<]`` / ``[>]`` arrow buttons; the
        broken panel-swap JS toggled their visibility.  Removing the
        JS without also removing the markup would leave orphaned DOM
        with no handler, so this lock pins the cleanup at both
        layers.
        """
        # Found from the app package's own location, never from this test's
        # place in the tree, and the read must hold text: a "not in" over an
        # empty read passes on nothing.
        partial_path = APP_PACKAGE_DIR / "templates" / "grid" / "_mobile_plan.html"
        partial = partial_path.read_text(encoding="utf-8")
        assert partial, f"read nothing from {partial_path}"
        assert "mobile-period-panel" not in partial
        assert 'id="mobile-prev-btn"' not in partial
        assert 'id="mobile-next-btn"' not in partial
