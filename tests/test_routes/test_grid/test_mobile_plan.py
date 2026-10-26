"""Grid route tests: the mobile plan tab, jumping to a period, and the summary refusing another
owner's paycheck.
"""

from decimal import Decimal

from app.extensions import db
from app.services import balance_at
from app.services.pay_calendar import DerivedPeriod

from tests._test_helpers import (
    APP_PACKAGE_DIR,
    all_periods,
    current_pay_period,
    derived_span,
    one_off_row_of,
)


class TestMobilePlanTab:
    """Regression locks for the read-only Plan tab on the mobile grid.

    Plan is the forward-looking summary view: a stack of period cards
    showing projected end balances and (on tap) the static line
    items behind each balance.  It is decoupled from the URL's
    ``periods`` / ``offset`` params -- always anchored at
    ``current_period`` and covering
    :data:`app.routes.grid.PLAN_WINDOW_MONTHS` months of the owner's own
    paychecks regardless of how the user is navigating in This Period.

    Pinned contracts:

      - The route emits ``plan_periods`` anchored at
        ``current_period`` and ignores the URL's ``offset``.
      - The partial wraps cards in ``id="plan-accordion"`` and every
        card body carries ``data-bs-parent="#plan-accordion"`` so
        opening one auto-closes the others.
      - Balance class follows the existing ``_balance_row.html``
        pattern: ``balance-negative`` for ``< 0``, ``balance-low``
        for ``0 <= bal < low_balance_threshold``, no class
        otherwise.
      - Row lines inside Plan use ``render_row_static`` -- no
        ``data-mobile-txn-id``, no ``.mobile-card-expansion``, no
        ``_mobile_card_actions.html`` include.
      - Empty income / expense sections are hidden (less clutter on
        a scan-oriented view).
      - Carry-forward: when one ``(rk, period)`` cell matches
        multiple transactions, each renders as its own static
        ``<li>``.
    """

    @staticmethod
    def _render_plan_partial(app, **ctx):
        """Render ``_mobile_plan.html`` with the given context.

        Direct render keeps the structural assertions immune to
        surrounding-page markup drift.  ``app.test_request_context``
        is what makes any ``url_for`` resolve inside the partial
        chain; the macro imports its STATUS_* globals from
        ``app.jinja_globals.register_ref_id_globals``.
        """
        template = app.jinja_env.get_template("grid/_mobile_plan.html")
        # The balance line is a REQUIRED key of the partial since plan step
        # credit_card:CC-4-2 (the static row decides its account chip against
        # it); ``None`` is the "no balance line" value and draws no chip, so
        # the structural assertions below see the rows they always did.  A
        # caller that names its own passes it through ``ctx``.
        ctx.setdefault("account", None)
        with app.test_request_context("/"):
            return template.render(**ctx)

    def test_plan_window_anchored_at_current_period(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The first Plan card's collapse id targets ``current_period.id``.

        Plan walks forward from ``current_period`` regardless of the
        URL.  This test hits ``/grid`` with the default URL state
        and verifies the leftmost Plan card points at the current
        period's id.
        """
        with app.app_context():
            current = current_pay_period(
                seed_user["user"].id,
            )
            assert current is not None

            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-plan"')
            pane = body[pane_start:]
            # The first plan card collapse target id is the partial's
            # explicit anchor on `current_period`.
            assert f'id="plan-period-{current.id}"' in pane
            assert f'data-bs-target="#plan-period-{current.id}"' in pane

    def test_plan_window_decoupled_from_url_offset(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Plan ignores ``?offset=N`` and stays anchored at current.

        The This Period arrow nav drives the URL to
        ``periods=1&offset=N``; Plan would be useless if it followed
        that lead, so the route builds a dedicated ``plan_*`` context
        window independent of ``ctx.start_offset``.  This test pins
        the decoupling by hitting ``/grid?periods=1&offset=2`` (a
        realistic post-arrow URL state) and asserting the leftmost
        Plan card is still the current period.
        """
        with app.app_context():
            current = current_pay_period(
                seed_user["user"].id,
            )
            assert current is not None

            response = auth_client.get("/grid?periods=1&offset=2")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-plan"')
            pane = body[pane_start:]
            # Current period is still the leftmost Plan card even
            # though the URL is asking This Period to start two
            # periods out.
            assert f'data-bs-target="#plan-period-{current.id}"' in pane

    def test_plan_accordion_uses_data_bs_parent(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Each Plan card collapse carries ``data-bs-parent="#plan-accordion"``.

        Bootstrap's accordion behaviour -- opening one card auto-
        closes the others -- is delivered declaratively via this
        attribute.  Removing it would break the mutual-exclusion
        contract the design depends on; this test catches the
        regression.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-plan"')
            pane = body[pane_start:]
            assert 'id="plan-accordion"' in pane
            assert 'data-bs-parent="#plan-accordion"' in pane

    def test_plan_balance_classes_per_threshold(self, app):
        """Balance color class follows the desktop ``_balance_row.html`` pattern.

        Pins the three branches by rendering the partial against
        controlled ``plan_columns`` balances.  No database setup --
        the partial is template logic only for the class assignment.
        """
        from datetime import date as _date  # pylint: disable=import-outside-toplevel

        with app.app_context():
            p_neg = DerivedPeriod(
                period_id=101, period_index=10,
                start_date=_date(2026, 6, 1), end_date=_date(2026, 6, 14),
                end_is_projected=False,
            )
            p_low = DerivedPeriod(
                period_id=102, period_index=11,
                start_date=_date(2026, 6, 15), end_date=_date(2026, 6, 28),
                end_is_projected=False,
            )
            p_ok = DerivedPeriod(
                period_id=103, period_index=12,
                start_date=_date(2026, 6, 29), end_date=_date(2026, 7, 12),
                end_is_projected=False,
            )
            plan_columns = {
                period.period_id: balance_at.GridColumn(
                    balance=balance,
                    income=Decimal("0"), expense=Decimal("0"),
                    net=Decimal("0"), elsewhere=Decimal("0.00"),
                    period_timing=Decimal("0.00"),
                    book_vs_bank=Decimal("0.00"),
                    contribution=Decimal("0.00"), accrual=Decimal("0.00"),
                )
                for period, balance in (
                    (p_neg, Decimal("-150.00")),
                    (p_low, Decimal("250.00")),
                    (p_ok, Decimal("4200.00")),
                )
            }

            html = self._render_plan_partial(
                app,
                plan_periods=[p_neg, p_low, p_ok],
                plan_income_row_keys=[],
                plan_expense_row_keys=[],
                plan_matched_by_row_period={},
                plan_columns=plan_columns,
                plan_row_flags=balance_at.GridRowFlags(
                    elsewhere=False, period_timing=False, book_vs_bank=False,
                    contribution=False, accrual=False,
                ),
                low_balance_threshold=500,
            )

            # Slice the rendered HTML into three per-card chunks so
            # the class assertions stay scoped to the right balance.
            def card_chunk(period_id):
                start = html.index(f'id="plan-period-{period_id}"')
                # Trigger button is rendered ABOVE the collapse body
                # -- back up to find the surrounding accordion-item.
                item_start = html.rfind("accordion-item", 0, start)
                # End of this card = start of the next id="plan-period-..." or end of html.
                item_end = len(html)
                for other_id in (101, 102, 103):
                    if other_id == period_id:
                        continue
                    needle = f'id="plan-period-{other_id}"'
                    pos = html.find(needle)
                    if pos > start and pos < item_end:
                        next_item = html.rfind(
                            "accordion-item", 0, pos,
                        )
                        if next_item > item_start:
                            item_end = next_item
                return html[item_start:item_end]

            neg_chunk = card_chunk(p_neg.period_id)
            low_chunk = card_chunk(p_low.period_id)
            ok_chunk = card_chunk(p_ok.period_id)

            assert "balance-negative" in neg_chunk
            assert "bi-exclamation-triangle-fill" in neg_chunk

            assert "balance-low" in low_chunk
            assert "bi-exclamation-circle" in low_chunk

            assert "balance-negative" not in ok_chunk
            assert "balance-low" not in ok_chunk

    def test_plan_rows_have_no_card_expansion_or_action_bar(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Inside the Plan accordion, no row carries the interactive
        affordances that the This Period rows have.

        Plan is read-only: no Mark Paid form, no Edit Amount button,
        no Open Full button, no per-card expansion collapse, no
        ``data-mobile-txn-id`` (the tap-to-expand attribute).  This
        test seeds a Projected expense in the current period and
        verifies the Plan accordion body for that period is free of
        all interactive markers.
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
                name="Plan-readonly Groceries",
                amount=Decimal("88.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(
                    TxnTypeEnum.EXPENSE,
                ),
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()

            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-plan"')
            # End the slice at the partial's accordion wrapper close
            # so the negative assertions cannot accidentally pick up
            # markup from elsewhere on the page.
            pane_end_marker = '</div>\n{% else %}'  # not present in rendered output
            pane = body[pane_start:]
            # Find the close of the plan-accordion div; the lookup
            # below targets the rendered "no future pay periods"
            # else-branch marker which is absent when periods exist,
            # so pane is the full Plan-tab body.
            del pane_end_marker

            assert "mobile-card-expansion" not in pane
            assert "data-mobile-txn-id" not in pane
            assert "Mark Paid" not in pane
            assert "Edit Amount" not in pane
            assert "Open Full" not in pane
            # Sanity: the static row is still rendering this txn.
            assert "Plan-readonly Groceries" in pane

    def test_plan_hides_empty_income_section(self, app):
        """When a Plan period has no income rows, no Income section
        card renders -- the partial omits the entire ``<div class="card">``
        rather than emitting an empty list.

        Same principle for expenses (covered by the next test).
        Skipping empty sections keeps the scan-oriented Plan view
        from carrying placeholder noise.
        """
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel
        from datetime import date as _date  # pylint: disable=import-outside-toplevel

        with app.app_context():
            period = DerivedPeriod(
                period_id=200, period_index=10,
                start_date=_date(2026, 6, 1), end_date=_date(2026, 6, 14),
                end_is_projected=False,
            )
            rk_exp = SimpleNamespace(
                category_id=1, template_id=None,
                txn_name="Rent", display_name="Rent",
                group_name="Housing",
            )
            txn_exp = SimpleNamespace(
                id=1, name="Rent", settled_amount=None,
                estimated_amount=Decimal("1200.00"),
                status=SimpleNamespace(is_settled=False),
                status_id=99, transfer_id=None,
                credit_payback_for_id=None,
            )
            html = self._render_plan_partial(
                app,
                plan_periods=[period],
                plan_income_row_keys=[],
                plan_expense_row_keys=[rk_exp],
                plan_matched_by_row_period={
                    (rk_exp.category_id, rk_exp.template_id,
                     rk_exp.txn_name, period.period_id): [txn_exp],
                },
                # The Plan row reads its amount off a published map since
                # plan step X-au-c2b (it read a transient
                # ``txn.live_estimated_amount`` behind an ``is defined``
                # guard).  A literal here rather than a resolve: these rows are
                # ``SimpleNamespace`` stand-ins and the assertions below are
                # STRUCTURAL -- which sections render, how many rows -- so what
                # the map contains is not the subject.
                budgets={txn_exp.id: txn_exp.estimated_amount},
                # What the row RECORDED, beside what it plans (plan step
                # X-au-c3).  Unsettled here, so it recorded nothing.
                settled={txn_exp.id: None},
                # And what a tick would RE-BOOK -- nothing here, for the same
                # reason: these rows have never settled, so they retain no
                # record for a re-settle to honour (plan step X-au-c3).
                retained={txn_exp.id: None},
                plan_columns={
                    period.period_id: balance_at.GridColumn(
                        balance=Decimal("3000.00"),
                        income=Decimal("0"),
                        expense=Decimal("1200"),
                        net=Decimal("-1200"),
                        elsewhere=Decimal("0.00"),
                        period_timing=Decimal("0.00"),
                        book_vs_bank=Decimal("0.00"),
                        contribution=Decimal("0.00"),
                        accrual=Decimal("0.00"),
                    ),
                },
                plan_row_flags=balance_at.GridRowFlags(
                    elsewhere=False, period_timing=False, book_vs_bank=False,
                    contribution=False, accrual=False,
                ),
                low_balance_threshold=500,
            )

            # Income section card is absent; Expense section card and
            # its row are present.
            assert "mobile-section-income" not in html
            assert "mobile-section-expense" in html
            assert "Rent" in html

    def test_plan_hides_empty_expense_section(self, app):
        """Empty expense section is also hidden; only the Income
        section renders when there are no expense rows.

        Mirrors the income-empty test for the other half of the
        ``if _income_rows`` / ``if _expense_rows`` symmetry in the
        partial.
        """
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel
        from datetime import date as _date  # pylint: disable=import-outside-toplevel

        with app.app_context():
            period = DerivedPeriod(
                period_id=201, period_index=10,
                start_date=_date(2026, 6, 1), end_date=_date(2026, 6, 14),
                end_is_projected=False,
            )
            rk_inc = SimpleNamespace(
                category_id=2, template_id=None,
                txn_name="Salary", display_name="Salary",
                group_name="Income",
            )
            txn_inc = SimpleNamespace(
                id=2, name="Salary", settled_amount=None,
                estimated_amount=Decimal("2500.00"),
                status=SimpleNamespace(is_settled=False),
                status_id=99, transfer_id=None,
                credit_payback_for_id=None,
            )
            html = self._render_plan_partial(
                app,
                plan_periods=[period],
                plan_income_row_keys=[rk_inc],
                plan_expense_row_keys=[],
                plan_matched_by_row_period={
                    (rk_inc.category_id, rk_inc.template_id,
                     rk_inc.txn_name, period.period_id): [txn_inc],
                },
                # The Plan row reads its amount off a published map since
                # plan step X-au-c2b (it read a transient
                # ``txn.live_estimated_amount`` behind an ``is defined``
                # guard).  A literal here rather than a resolve: these rows are
                # ``SimpleNamespace`` stand-ins and the assertions below are
                # STRUCTURAL -- which sections render, how many rows -- so what
                # the map contains is not the subject.
                budgets={txn_inc.id: txn_inc.estimated_amount},
                # What the row RECORDED, beside what it plans (plan step
                # X-au-c3).  Unsettled here, so it recorded nothing.
                settled={txn_inc.id: None},
                retained={txn_inc.id: None},
                plan_columns={
                    period.period_id: balance_at.GridColumn(
                        balance=Decimal("3000.00"),
                        income=Decimal("2500"),
                        expense=Decimal("0"),
                        net=Decimal("2500"),
                        elsewhere=Decimal("0.00"),
                        period_timing=Decimal("0.00"),
                        book_vs_bank=Decimal("0.00"),
                        contribution=Decimal("0.00"),
                        accrual=Decimal("0.00"),
                    ),
                },
                plan_row_flags=balance_at.GridRowFlags(
                    elsewhere=False, period_timing=False, book_vs_bank=False,
                    contribution=False, accrual=False,
                ),
                low_balance_threshold=500,
            )

            assert "mobile-section-income" in html
            assert "mobile-section-expense" not in html
            assert "Salary" in html

    def test_plan_preserves_carry_forward(self, app):
        """A ``(rk, period)`` cell with multiple matched transactions
        renders each as its own static ``<li>`` row.

        Carry-forward and similar multi-txn-per-cell scenarios are
        load-bearing for envelope reporting (a period can hold both
        the canonical envelope row and a sibling carry-forward
        sweep).  ``render_row_static`` iterates the matched list
        rather than flattening it, mirroring the interactive
        ``render_row_card`` contract.
        """
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel
        from datetime import date as _date  # pylint: disable=import-outside-toplevel

        with app.app_context():
            period = DerivedPeriod(
                period_id=300, period_index=10,
                start_date=_date(2026, 6, 1), end_date=_date(2026, 6, 14),
                end_is_projected=False,
            )
            rk = SimpleNamespace(
                category_id=3, template_id=42,
                txn_name="Groceries", display_name="Groceries",
                group_name="Food",
            )
            txn_a = SimpleNamespace(
                id=10, name="Groceries", settled_amount=None,
                estimated_amount=Decimal("100.00"),
                status=SimpleNamespace(is_settled=False),
                status_id=99, transfer_id=None,
                credit_payback_for_id=None,
            )
            txn_b = SimpleNamespace(
                id=11, name="Groceries", settled_amount=None,
                estimated_amount=Decimal("25.00"),
                status=SimpleNamespace(is_settled=False),
                status_id=99, transfer_id=None,
                credit_payback_for_id=None,
            )
            html = self._render_plan_partial(
                app,
                plan_periods=[period],
                plan_income_row_keys=[],
                plan_expense_row_keys=[rk],
                plan_matched_by_row_period={
                    (rk.category_id, rk.template_id,
                     rk.txn_name, period.period_id): [txn_a, txn_b],
                },
                # The Plan row reads its amount off a published map since
                # plan step X-au-c2b (it read a transient
                # ``txn.live_estimated_amount`` behind an ``is defined``
                # guard).  A literal here rather than a resolve: these rows are
                # ``SimpleNamespace`` stand-ins and the assertions below are
                # STRUCTURAL -- which sections render, how many rows -- so what
                # the map contains is not the subject.
                budgets={
                    txn_a.id: txn_a.estimated_amount,
                    txn_b.id: txn_b.estimated_amount,
                },
                # What each row RECORDED, beside what it plans (plan step
                # X-au-c3).  Both are unsettled, so both recorded nothing.
                settled={txn_a.id: None, txn_b.id: None},
                retained={txn_a.id: None, txn_b.id: None},
                plan_columns={
                    period.period_id: balance_at.GridColumn(
                        balance=Decimal("3000.00"),
                        income=Decimal("0"),
                        expense=Decimal("125"),
                        net=Decimal("-125"),
                        elsewhere=Decimal("0.00"),
                        period_timing=Decimal("0.00"),
                        book_vs_bank=Decimal("0.00"),
                        contribution=Decimal("0.00"),
                        accrual=Decimal("0.00"),
                    ),
                },
                plan_row_flags=balance_at.GridRowFlags(
                    elsewhere=False, period_timing=False, book_vs_bank=False,
                    contribution=False, accrual=False,
                ),
                low_balance_threshold=500,
            )

            # Each amount renders separately -- one <li> per match.
            assert "$100" in html
            assert "$25" in html


class TestMobileJumpToPeriod:
    """Regression locks for the jump-to-period ``<select>`` in the
    "This Period" tab header (Commit 10 of the mobile-first v3
    implementation).

    The select lives in ``app/templates/grid/_mobile_this_period.html``
    below the ``[<] [>]`` arrow row and lets the user reach any
    non-adjacent period in one tap, avoiding N taps on ``[<]``.
    Picking a non-current option fires ``change``, which a delegated
    listener in ``app/static/js/mobile_grid.js`` translates into a
    full GET submit to ``/grid?periods=1&offset=N``.

    These tests pin the structural contract the JS handler and the
    grid route consume:

      - The ``<select name="offset">`` is emitted exactly once per
        page render, inside the ``#mobile-this-period`` tab-pane
        (so the JS handler's ``.closest('#mobile-this-period')``
        guard matches).
      - One ``<option>`` per period in ``all_periods`` -- the option
        list mirrors the user's full visible projection so the user
        can jump to any of them.
      - Option ``value`` is the period's offset relative to
        ``current_period.period_index``, matching the desktop
        selector convention at ``grid/grid.html:24-49`` and the
        partial's own prev/next arrow hrefs.
      - The option for the currently visible period
        (``periods[0]`` / ``period``) carries the ``selected``
        attribute so the picker opens on that row.
      - Method is GET with a hidden ``periods=1`` input -- the GET
        is read-only navigation (no state mutation), so no CSRF
        token is required per CLAUDE.md "State-changing actions
        must use POST".
      - The JS file carries the delegated change handler with the
        ``select[name="offset"]`` selector and the
        ``#mobile-this-period`` scope guard.
    """

    def test_jump_to_select_present(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C10-1: a single ``<select name="offset">`` lives inside the
        ``#mobile-this-period`` tab-pane.

        The select is the jump-to control. Scoping the assertion to
        the pane (not just the document) guards against a future
        regression where a sibling ``<select name="offset">`` lands
        in another tab and double-submits via the same delegated JS
        handler.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-this-period"')
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            assert pane.count('<select name="offset"') == 1
            # The hidden periods=1 input rides with the select so
            # the GET lands at the single-period URL shape.
            assert 'name="periods" value="1"' in pane
            # GET form -- read-only navigation, no CSRF gate.
            assert 'method="get"' in pane

    def test_jump_to_options_match_all_periods(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C10-2: option count equals ``len(all_periods)``.

        ``seed_periods_today`` provisions 10 biweekly periods
        (indices 0..9); the owner's full period list
        returns all 10 to the route, so the rendered select carries
        10 ``<option>`` elements. Each option's ``value`` is the
        offset relative to the current period (period_index 4 under
        ``seed_periods_today``), so the value set is
        ``{-4, -3, -2, -1, 0, 1, 2, 3, 4, 5}``.
        """
        with app.app_context():
            current = current_pay_period(
                seed_user["user"].id,
            )
            assert current is not None
            owner_periods = all_periods(
                seed_user["user"].id,
            )
            assert len(owner_periods) == 10
            assert derived_span(current).period_index == 4

            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-this-period"')
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            # Slice down to the select's option block to keep the
            # count immune to unrelated <option> elements elsewhere
            # in the pane (none exist today, but the slice keeps
            # future additions safe).
            select_start = pane.index('<select name="offset"')
            select_end = pane.index("</select>", select_start)
            select_block = pane[select_start:select_end]
            assert select_block.count("<option ") == len(owner_periods)

            # Spot-check the boundary offsets. period_index 0 -> -4,
            # period_index 9 -> +5 (all under current.period_index=4).
            assert 'value="-4"' in select_block
            assert 'value="5"' in select_block
            # And the current option must exist at value="0".
            assert 'value="0"' in select_block

    def test_jump_to_current_period_selected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C10-3: the option for the currently visible period carries
        ``selected``.

        At the default ``/grid`` URL (``start_offset == 0``),
        ``periods[0]`` (the partial's ``period`` local) equals
        ``current_period``, so the offset-0 option is the selected
        one. Verified by slicing the offset-0 option's opening
        tag and asserting ``selected`` appears inside it.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-this-period"')
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            select_start = pane.index('<select name="offset"')
            select_end = pane.index("</select>", select_start)
            select_block = pane[select_start:select_end]

            # Locate the offset=0 option (the current period under
            # start_offset=0) and slice its full opening tag so the
            # assertion spans the multi-line attribute layout.
            opt_start = select_block.index('value="0"')
            opt_tag_open = select_block.rindex("<option", 0, opt_start)
            opt_tag_close = select_block.index(">", opt_start)
            opt_open = select_block[opt_tag_open:opt_tag_close]
            assert "selected" in opt_open

    def test_jump_to_selected_follows_visible_period(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C10-3 (extended): non-zero ``start_offset`` shifts the
        selected option to the currently visible period.

        At ``?periods=1&offset=2`` the visible period is the one
        with ``period_index == current.period_index + 2 == 6``;
        the select's ``selected`` must move to the offset=2 option
        (and the offset=0 option must NOT carry ``selected``).
        Locks the ``p.id == period.id`` predicate against drift
        toward an unconditional or current-only selection rule.
        """
        with app.app_context():
            response = auth_client.get("/grid?periods=1&offset=2")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-this-period"')
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            select_start = pane.index('<select name="offset"')
            select_end = pane.index("</select>", select_start)
            select_block = pane[select_start:select_end]

            # offset=2 option carries selected.
            opt2_start = select_block.index('value="2"')
            opt2_open = select_block[
                select_block.rindex("<option", 0, opt2_start)
                :select_block.index(">", opt2_start)
            ]
            assert "selected" in opt2_open

            # offset=0 option does NOT carry selected.
            opt0_start = select_block.index('value="0"')
            opt0_open = select_block[
                select_block.rindex("<option", 0, opt0_start)
                :select_block.index(">", opt0_start)
            ]
            assert "selected" not in opt0_open

    def test_jump_to_delegated_handler_in_mobile_grid_js(self):
        """C10 JS-side regression lock: the delegated change handler
        in ``mobile_grid.js`` references the select selector and the
        ``#mobile-this-period`` scope guard.

        The CSP-friendly delegated handler replaces the inline
        ``onchange="this.form.submit()"`` from the plan's draft
        markup (per CLAUDE.md "No inline scripts"). Reading the JS
        file directly (same pattern as
        ``test_swipe_threshold_matches_period_swipe``) locks both
        the selector and the scope so a future refactor cannot
        silently drop either guard.
        """
        # Found from the app package's own location, never from this test's
        # place in the tree.
        js_path = APP_PACKAGE_DIR / "static" / "js" / "mobile_grid.js"
        src = js_path.read_text(encoding="utf-8")

        # The selector targets the jump-to <select>.
        assert 'select[name="offset"]' in src
        # The scope guard limits the handler to the "This Period" pane.
        assert "#mobile-this-period" in src
        # form.submit() is what turns the change into a GET to /grid.
        assert "form.submit()" in src


class TestTheMobileSummaryRefusesAnotherOwnersPaycheck:
    """``/grid/this-period-summary`` answers only for the requester's own periods.

    **This class did not exist until plan step C2-f2b, and the endpoint's IDOR
    guard had no direct test at all** -- which is exactly why it is here, since
    that step REPLACED the guard.  It was ``db.session.get(PayPeriod,
    period_id)`` followed by ``period.user_id != user_id``, written out by
    hand; it is now ``ctx.calendar().period_by_id(period_id)``, and a calendar
    holds ONE owner's paydays, so another owner's id is absent rather than
    rejected.

    Both spellings answer ``204`` -- and the whole risk is that they answer it
    for different REASONS, so a test asserting only the status code would pass
    just as happily against a guard deleted outright.  The first test therefore
    also pins that the body is EMPTY: a 204 carrying the other owner's Net Cash
    Flow and Projected Balance is the leak, and 204 is exactly the status this
    endpoint's swap-nothing no-op already uses for a transient miss.

    The two cases are separate tests rather than one (the planning conventions: merge
    what shares KEYS, split what shares only a READER).  "That id does not
    exist" and "that id is someone else's" are two states this route must
    answer IDENTICALLY, and a single test asserting both cannot show that they
    agree.
    """

    def test_another_owners_period_id_is_answered_204(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_user, seed_second_periods,
    ):
        """A real period id belonging to a DIFFERENT owner leaks nothing."""
        foreign = seed_second_periods[0]
        with app.app_context():
            assert foreign.user_id != seed_user["user"].id, (
                "the fixture must supply another OWNER's period or this "
                "test proves nothing"
            )
        resp = auth_client.get(
            f"/grid/this-period-summary?period_id={foreign.id}",
        )
        assert resp.status_code == 204
        assert resp.data == b"", (
            "a 204 carrying a body would be the leak: this partial renders "
            "the Net Cash Flow and Projected Balance of whatever period it is "
            "given, and that period belongs to another owner"
        )

    def test_a_nonexistent_period_id_is_answered_the_same_way(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"Not found" and "not yours" are indistinguishable to the caller.

        The project's security response rule, on the one endpoint whose answer
        to both is a swap-nothing ``204`` rather than a ``404``.
        """
        resp = auth_client.get("/grid/this-period-summary?period_id=99999999")
        assert resp.status_code == 204
        assert resp.data == b""
