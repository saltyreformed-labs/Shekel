"""Grid route tests: the two remainder rows, the contributions row, and the accrual row's sign and
label.
"""

from decimal import Decimal

from app.extensions import db
from app.models.investment_params import InvestmentParams
from app.services.cash_flow_set import CashFlowSet
from app.services import balance_at
from app.services.balance_at import BalanceContext

from tests._test_helpers import (
    create_hysa_account,
    create_loan_account,
    current_pay_period,
    make_investment_account,
    make_salary_profile,
)

from ._helpers import (
    _summary_periods,
    _summary_columns,
    _row_flags,
    _render_grid_footer,
    _render_mobile_card,
    _render_plan_recap,
)


class TestTheTwoRemainderRows:
    """Ruling R-O / R-P / R-DH (f): the two remainder rows, on every surface.

    They carry what the Total Income / Total Expenses rows structurally cannot
    say about the balance change.  Plan step X-c2b2 is where a producer first
    put a non-zero figure in them (measured ``-$788.68`` combined in the real
    Checking account's current column); ruling R-O's rule hides a row whose
    every visible column reports ``0.00``, which is why the RENDER is graded
    here on hand-built columns.  A row whose template nobody ever executed
    would arrive at a cutover unproven, and a cutover is where money moves.

    **This was ONE row, "Timing & true-ups", until plan step S1-c** (ruling
    R-DH (f)).  It summed two facts with different causes and different fixes:
    money landing in a column other than the one it was budgeted to ("Period
    timing", fixed by re-budgeting the row or dating it correctly), and the gap
    between what the app had recorded and what the bank actually held ("Book vs
    bank", fixed by recording the spending it did not know about).  On the
    developer's own data the single row read ``-$4,588.69`` in a column whose
    halves were ``-$427.22`` and ``-$4,161.47`` -- a figure with no action
    attached to it.

    So every test below asserts the two rows SEPARATELY, and the pair that
    matters most is ``test_desktop_footer_renders_one_row_without_the_other``:
    the shared ``reconciliation-row`` class now marks both rows, so the rows
    are told apart here by the LABEL a user reads, which is the thing the
    ruling is actually about.
    """

    #: The labels as the templates escape them into the rendered HTML.
    _TIMING = "Period timing"
    _BOOK = "Book vs bank"

    @staticmethod
    def _columns(*, periods, period_timing="0.00", book_vs_bank="0.00"):
        """Return one GridColumn per period carrying the two remainders."""
        return _summary_columns(
            periods, period_timing=period_timing, book_vs_bank=book_vs_bank,
        )

    @staticmethod
    def _periods():
        """Two period stand-ins carrying what the footer template reads."""
        return _summary_periods()

    def _render_footer(self, app, *, period_timing="0.00", book_vs_bank="0.00",
                       timing_flag=False, book_flag=False):
        """Render the desktop ``<tfoot>`` with given remainders + flags."""
        periods = self._periods()
        return _render_grid_footer(
            app, periods,
            self._columns(
                periods=periods, period_timing=period_timing,
                book_vs_bank=book_vs_bank,
            ),
            _row_flags(period_timing=timing_flag, book_vs_bank=book_flag),
        )

    def test_desktop_footer_renders_both_rows_above_the_balance(self, app):
        """Both rows sit in the tfoot ABOVE Projected End Balance (ruling R-O).

        Placement is the ruling, not a preference: the whole "how this balance
        is reached" chain has to read as one block, so the rows the identity
        binds must be above the balance they explain rather than in the flow
        tbody two sections up.

        The two figures are the halves R-DH (f) was measured on, scaled to this
        fixture, and they are DIFFERENT so a template rendering one column's
        value into both rows fails rather than passing on a coincidence.
        """
        with app.app_context():
            html = self._render_footer(
                app, period_timing="-427.22", book_vs_bank="-160.05",
                timing_flag=True, book_flag=True,
            )

        assert self._TIMING in html
        assert self._BOOK in html
        assert "reconciliation-row" in html
        assert "-$427" in html
        assert "-$160" in html
        # Timing first, then book-vs-bank, then the balance they explain.
        assert html.index(self._TIMING) < html.index(self._BOOK)
        assert html.index(self._BOOK) < html.index("Projected End Balance")

    def test_desktop_footer_renders_one_row_without_the_other(self, app):
        """R-DH (f)'s point: a window with only true-ups shows only that row.

        The property a SHARED flag could not express, and the reason the two
        rows exist.  A period carrying a balance assertion and no timing
        difference renders "Book vs bank" and must NOT render a permanently
        ``$0.00`` "Period timing" line beside it -- a labelled row of zeros
        reads as "measured, and the answer is zero" for a fact that was never
        in question.

        Asserted in both directions, because a template wired to the wrong flag
        passes a one-directional check by luck.
        """
        with app.app_context():
            book_only = self._render_footer(
                app, book_vs_bank="-160.05", book_flag=True,
            )
            timing_only = self._render_footer(
                app, period_timing="-427.22", timing_flag=True,
            )

        assert self._BOOK in book_only
        assert self._TIMING not in book_only
        assert self._TIMING in timing_only
        assert self._BOOK not in timing_only

    def test_desktop_footer_hides_an_all_zero_pair(self, app):
        """An all-zero window renders neither row -- the ordinary cash grid."""
        with app.app_context():
            html = self._render_footer(app)

        assert self._TIMING not in html
        assert self._BOOK not in html
        assert "reconciliation-row" not in html
        # The rest of the footer is untouched.
        assert "Projected End Balance" in html

    def test_desktop_footer_shows_zero_in_the_columns_that_carry_none(self, app):
        """Once a row renders it shows $0 where a column has none.

        The other half of ruling R-O: the row is present for the WHOLE visible
        window, so a column with nothing to explain reads ``$0`` rather than
        blank -- blank would read as "not measured".
        """
        with app.app_context():
            periods = self._periods()
            columns = self._columns(periods=periods)
            columns[periods[0].period_id] = balance_at.GridColumn(
                balance=Decimal("3000.00"), income=Decimal("2400.00"),
                expense=Decimal("1450.00"), net=Decimal("950.00"),
                elsewhere=Decimal("0.00"),
                period_timing=Decimal("-788.68"),
                book_vs_bank=Decimal("0.00"),
                contribution=Decimal("0.00"), accrual=Decimal("0.00"),
            )
            html = _render_grid_footer(
                app, periods, columns, _row_flags(period_timing=True),
            )

        row = html[html.index("reconciliation-row"):]
        row = row[:row.index("</tr>")]
        assert "-$789" in row
        assert "$0" in row

    def test_mobile_this_period_card_renders_both_rows(self, app):
        """Ruling R-P: the mobile summary carries the same two lines.

        Without them the card shows a Net Cash Flow that does not account for
        the balance printed beside it -- the visible contradiction ruling R-K
        refused to ship, on the form factor Mark Paid is used from.
        """
        period = self._periods()[0]
        with app.app_context():
            html = _render_mobile_card(
                app, period,
                self._columns(
                    periods=[period], period_timing="-427.22",
                    book_vs_bank="-160.05",
                ),
                _row_flags(period_timing=True, book_vs_bank=True),
            )

        assert self._TIMING in html
        assert self._BOOK in html
        assert "-$427" in html
        assert "-$160" in html
        assert html.index("Net Cash Flow") < html.index(self._TIMING)
        assert html.index(self._BOOK) < html.index("Projected Balance")

    def test_mobile_this_period_card_asks_each_row_separately(self, app):
        """The card follows the SAME per-row rule, not a shared one."""
        period = self._periods()[0]
        with app.app_context():
            html = _render_mobile_card(
                app, period,
                self._columns(periods=[period], book_vs_bank="-160.05"),
                _row_flags(book_vs_bank=True),
            )

        assert self._BOOK in html
        assert self._TIMING not in html

    def test_mobile_this_period_card_hides_an_all_zero_pair(self, app):
        """The mobile card follows the SAME rule, not its own."""
        period = self._periods()[0]
        with app.app_context():
            html = _render_mobile_card(
                app, period, self._columns(periods=[period]), _row_flags(),
            )

        assert self._TIMING not in html
        assert self._BOOK not in html
        assert "Net Cash Flow" in html

    def test_plan_recap_renders_both_rows(self, app):
        """Ruling R-P again: the Plan tab recap carries both figures too."""
        period = self._periods()[0]
        with app.app_context():
            html = _render_plan_recap(
                app, period,
                self._columns(
                    periods=[period], period_timing="-427.22",
                    book_vs_bank="-160.05",
                ),
                _row_flags(period_timing=True, book_vs_bank=True),
            )

        # The recap is space-constrained, so it abbreviates the two labels.
        assert "Timing" in html
        assert "Bank" in html
        assert "-$427" in html
        assert "-$160" in html

    def test_plan_recap_asks_each_row_separately(self, app):
        """One chip on, the other off -- the recap does not share a flag."""
        period = self._periods()[0]
        with app.app_context():
            html = _render_plan_recap(
                app, period,
                self._columns(periods=[period], period_timing="-427.22"),
                _row_flags(period_timing=True),
            )

        assert "Timing" in html
        assert "Bank" not in html

    def test_plan_recap_hides_an_all_zero_pair(self, app):
        """And hides both on the same rule."""
        period = self._periods()[0]
        with app.app_context():
            html = _render_plan_recap(
                app, period, self._columns(periods=[period]), _row_flags(),
            )

        assert "Timing" not in html
        assert "Bank" not in html

    def test_the_mobile_card_reads_its_OWN_period_not_the_grid_window(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The mobile card's conditional bars are scoped to the period it shows.

        Found in X-c2b1's own review: the initial include was handed the
        DESKTOP window's flags while the card renders ``periods[0]`` alone, so
        a window carrying a figure in some other column would have turned the
        card's bar on for a period that has none -- and the
        ``mobileCardSettled`` refresh, which sees one period and no window,
        would have turned it back off.  A flicker between two renders of the
        same card, and with the redundant per-cell guard now gone (the flag
        alone decides) it would render ``None`` as money.

        Driven from data through the employer-CONTRIBUTION bar.  A 401(k)
        whose balance is asserted on the current period's own payday receives
        nothing in that period and a contribution on every payday after it
        (ruling **R-Z**: a payday at or before the assertion contributes
        nothing), so the default window has contributing columns -- the desktop
        row renders -- whose leftmost period has none, and the mobile bar must
        not.  The shape is asserted at the seam first, so the test cannot pass
        vacuously by failing to construct it.

        **It was the Interest bar until plan step X-f3c-2c, on an HYSA anchored
        two periods AHEAD of today, and that shape is unreachable.**  An
        assertion is a statement about a day that has already happened --
        ``anchor_service.resolve_observation_day`` refuses a future one -- and
        the fixture only built it because it re-stamped the origination row
        after the factory had written it, which an append-only table has no act
        for.  Accrual cannot serve here at all: a modelled account accrues from
        its latest assertion forward, and that assertion can never be later
        than today, so the column CONTAINING today always accrues something.
        The contribution tier can, because what it keys on is a payday's
        position relative to the assertion rather than the clock's.
        """
        salary = make_salary_profile(
            seed_user, db.session, pay=Decimal("3631.74"),  # $94,425.24 a year / 26
        )
        salary.is_active = True
        db.session.flush()
        retirement = make_investment_account(
            seed_user, db.session, seed_periods_today[4],
            Decimal("100000.00"), employer_type="flat_percentage",
        )
        params = (
            db.session.query(InvestmentParams)
            .filter_by(account_id=retirement.id).one()
        )
        params.employer_flat_percentage = Decimal("0.0500")
        # The job that FUNDS the employer contribution (ruling R-SAL5, applied
        # at plan step salary:R14-b): no deduction names this account, so
        # without the link no employer money is modelled at all and the
        # precondition below -- a window that contributes -- cannot hold.
        params.salary_profile_id = salary.id
        db.session.commit()
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            # The windows the route itself cuts (plan step C2-f2b): the
            # visible six off the pass's own calendar, and the ONE-period
            # window the mobile card's flags are asked of.  Built the same way
            # here so the precondition below cannot hold for a window shape
            # production never renders.
            calendar = bctx.calendar()
            current = calendar.period_containing(bctx.as_of)
            window = calendar.window(current.period_index, 6)
            card_window = [window[0]]
            view = balance_at.grid_balance_view(CashFlowSet.single(retirement), bctx)
            # The shape this test needs: the window contributes, its first
            # column does not.
            assert view.row_flags(window).contribution is True
            assert view.row_flags(card_window).contribution is False

        resp = auth_client.get(f"/grid?account_id={retirement.id}&periods=6")
        assert resp.status_code == 200
        html = resp.data.decode()

        # The desktop footer row renders: the window DOES contain a
        # contribution.
        footer = html[html.index('id="grid-summary"'):html.index("</tfoot>")]
        assert "modelled-contribution-row" in footer

        # The mobile card renders the leftmost period, which has none.  It is
        # the last block of the This Period pane, so the Plan pane bounds it.
        card_start = html.index('id="mobile-tp-summary-')
        card = html[card_start:html.index('id="mobile-plan"', card_start)]
        assert "Net Cash Flow" in card, "the card must actually have rendered"
        assert "modelled-contribution-row" not in card

    def test_the_shipped_grid_shows_no_remainder_row_on_a_clean_account(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """End to end: the real page renders neither remainder row here.

        The seeded account has one opening assertion (which ruling R-I keeps
        out of every column) and no rows that settled outside their own period,
        so both remainders are ``$0.00`` throughout and R-O hides both rows.

        **The ABBREVIATED labels are asserted too, and they are not redundant.**
        ``/grid`` also renders ``grid/_mobile_plan.html``, whose space-constrained
        chips say "Timing" and "Bank" rather than the full row labels -- and they
        are gated on ``plan_row_flags``, computed over the PLAN window
        (``app/routes/grid/page.py``) rather than the tfoot's visible window.  So a
        regression could light a permanently-``$0`` chip on the Plan tab while
        the desktop ``reconciliation-row`` stayed hidden and the two full-label
        assertions below stayed green.  HEAD asserted ``b"Timing"``; narrowing to
        the full labels alone would have dropped that surface silently.
        """
        resp = auth_client.get("/grid")
        assert resp.status_code == 200
        assert b"reconciliation-row" not in resp.data
        assert self._TIMING.encode() not in resp.data
        assert self._BOOK.encode() not in resp.data
        # The mobile Plan recap's abbreviated chips, on their own flag window.
        assert b"Timing" not in resp.data
        assert b"Bank" not in resp.data


class TestTheContributionsRow:
    """Ruling R-AH: the modelled tiers are TWO rows, on both form factors.

    A modelled asset has two modelled tiers and the CONTRIBUTION is the larger
    of them on the real Empower 401(k) ($9,624.27 against $8,152.58 over the
    horizon), which is why ruling R-K's identity gained a fourth term.  They are
    two rows rather than one sum because a single summed row can render POSITIVE
    on an account that LOST money: measured at a -10.5% return the market takes
    -$7,366.83 while payroll puts in +$9,624.27, so one row would report
    +$2,257.44 -- a figure that is neither what the market did nor what the user
    put in.

    **Graded on hand-built columns because no producer can reach the row at this
    step, and that is a property of the code rather than of a fixture.**  Plan
    step X-g3a keeps the grid's kind gate, so only an INTEREST account resolves
    the modelled arm, and ``_asset_contributions.contribution_events`` returns
    ``[]`` for every kind but INVESTMENT -- so ``contribution`` is ``0.00`` in
    every column of every account for EVERY POSSIBLE FIXTURE and
    ``row_flags.contribution`` is permanently ``False``.  X-g3b supplies the
    producer-side control on a 401(k) fixture with a real feed.  This is exactly
    how "Timing & true-ups" was graded at X-c2b1 and for the same stated reason.
    """

    def test_desktop_footer_seats_the_row_between_timing_and_the_accrual(
        self, app,
    ):
        """Row ORDER is the model's own (ruling R-AH), not a preference.

        A contribution lands on its pay period's ``start_date`` and
        ``_asset_fold._resolve_days`` applies the day's deltas and THEN accrues
        on the balance the day ENDS holding -- so the money is contributed and
        then earns.  Addition is commutative and the identity holds either way;
        reading order is not, and the rows tell the story in the order the
        replay does.
        """
        with app.app_context():
            periods = _summary_periods()
            html = _render_grid_footer(
                app, periods,
                _summary_columns(
                    periods, period_timing="-788.68",
                    contribution="181.59", accrual="95.98",
                ),
                _row_flags(
                    period_timing=True, book_vs_bank=True,
                    contribution=True, accrual=True,
                ),
            )

        assert "modelled-contribution-row" in html
        assert "$181.59" in html
        # The two remainder rows come first, in ruling R-DH (f)'s order.
        assert html.index("Period timing") < html.index("Book vs bank")
        assert html.index("Book vs bank") < html.index("Contributions")
        assert html.index("Contributions") < html.index(
            "modelled-accrual-row",
        )
        assert html.index("modelled-accrual-row") < html.index(
            "Projected End Balance",
        )

    def test_desktop_footer_hides_an_all_zero_contributions_row(self, app):
        """A window that contributes nothing renders no row -- R-O's rule.

        This is the state EVERY account is in at plan step X-g3a, so it also
        pins that the shipped page is unchanged by the new row.
        """
        with app.app_context():
            periods = _summary_periods()
            html = _render_grid_footer(
                app, periods, _summary_columns(periods), _row_flags(),
            )

        assert "modelled-contribution-row" not in html
        assert "Contributions" not in html
        assert "Projected End Balance" in html

    def test_desktop_footer_shows_zero_where_a_column_contributes_none(
        self, app,
    ):
        """Once on, the row shows ``$0`` rather than blank -- R-O's other half.

        Blank would read as "not measured".  A payday falls in one pay period
        and not its neighbour, so a live 401(k) genuinely has zero columns
        beside contributing ones.
        """
        with app.app_context():
            periods = _summary_periods()
            columns = _summary_columns(periods)
            columns[periods[0].period_id] = balance_at.GridColumn(
                balance=Decimal("3000.00"), income=Decimal("2400.00"),
                expense=Decimal("1450.00"), net=Decimal("950.00"),
                elsewhere=Decimal("0.00"),
                period_timing=Decimal("0.00"),
                book_vs_bank=Decimal("0.00"),
                contribution=Decimal("181.59"), accrual=Decimal("0.00"),
            )
            html = _render_grid_footer(
                app, periods, columns, _row_flags(contribution=True),
            )

        row = html[html.index("modelled-contribution-row"):]
        row = row[:row.index("</tr>")]
        assert "$181.59" in row
        # Cents, so a column that contributed nothing is visibly distinct from
        # one that contributed a sub-dollar amount (developer ruling).
        assert "$0.00" in row

    def test_the_mobile_card_carries_both_bars_in_the_same_order(self, app):
        """Ruling R-P: the mobile summary explains its balance the same way.

        Without both bars the card would show a Net Cash Flow that does not
        account for the balance beside it -- the visible contradiction ruling
        R-K refused to ship, on the form factor Mark Paid is used from.
        """
        period = _summary_periods()[0]
        with app.app_context():
            html = _render_mobile_card(
                app, period,
                _summary_columns(
                    [period], period_timing="-788.68",
                    contribution="181.59", accrual="95.98",
                ),
                _row_flags(
                    period_timing=True, book_vs_bank=True,
                    contribution=True, accrual=True,
                ),
                accrual_label="Growth",
            )

        assert "modelled-contribution-row" in html
        assert "$181.59" in html
        assert html.index("Net Cash Flow") < html.index("Period timing")
        assert html.index("Period timing") < html.index("Book vs bank")
        assert html.index("Book vs bank") < html.index("Contributions")
        assert html.index("Contributions") < html.index("Growth")
        assert html.index("Growth") < html.index("Projected Balance")

    def test_the_mobile_card_hides_an_all_zero_contributions_bar(self, app):
        """The card follows the SAME rule, not its own."""
        period = _summary_periods()[0]
        with app.app_context():
            html = _render_mobile_card(
                app, period, _summary_columns([period]), _row_flags(),
            )

        assert "modelled-contribution-row" not in html
        assert "Contributions" not in html
        assert "Net Cash Flow" in html

    def test_the_plan_recap_carries_both_figures(self, app):
        """Ruling R-P again: the Plan tab recap explains the same chain."""
        period = _summary_periods()[0]
        with app.app_context():
            html = _render_plan_recap(
                app, period,
                _summary_columns(
                    [period], period_timing="-788.68",
                    contribution="181.59", accrual="95.98",
                ),
                _row_flags(
                    period_timing=True, book_vs_bank=True,
                    contribution=True, accrual=True,
                ),
                accrual_label="Appreciation",
            )

        assert "Contributions $181.59" in " ".join(html.split())
        assert html.index("Timing") < html.index("Contributions")
        assert html.index("Contributions") < html.index("Appreciation")

    def test_the_plan_recap_hides_an_all_zero_contributions_figure(self, app):
        """And hides it on the same rule."""
        period = _summary_periods()[0]
        with app.app_context():
            html = _render_plan_recap(
                app, period, _summary_columns([period]), _row_flags(),
            )

        assert "Contributions" not in html


class TestTheAccrualRowSignReachesItsStyling:
    """Finding N-88: a rendered market LOSS must not be styled as a gain.

    The mobile card hard-coded ``text-success`` on the modelled-return figure.
    That was safe only while INTEREST was the sole kind reaching the row --
    ``interest_params`` bounds ``apy >= 0`` -- and the two kinds ruling R-W adds
    are bounded only ``> -1``, with ``asset_appreciation_params`` saying so in
    its own words ("A negative rate is permitted so a future depreciating asset
    (e.g. Vehicle) reuses this table unchanged").  A depreciating Vehicle or a
    401(k) in a down market would have rendered a measured -$142.11 in success
    green, while the desktop footer and the Plan recap rendered the same figure
    colourless -- so the app would also have disagreed with itself across form
    factors, the shape ruling R-P exists to prevent.

    The rule is three-way and stated ONCE (``accrual_class`` / ``accrual_money``
    in ``grid/_grid_row_macros.html``): a gain is the success token with an
    explicit ``+``, a loss is the danger token with the ``-`` the money macro
    already renders, and a column that earned nothing is neither.  So colour is
    never the only signal, which is ``/investment``'s shipped rule in its own
    words, and a ``$0`` column is not reported as a gain.
    """

    @staticmethod
    def _accrual_cell(html):
        """Return just the modelled-accrual row / bar out of *html*.

        Bounded at the element's own closing tag rather than by a character
        count, so a template that grows cannot silently push the figure out of
        the slice and turn an assertion vacuous.  The desktop row closes with
        ``</tr>`` and the mobile bar with ``</div>``; whichever comes first is
        this element's end.
        """
        body = html[html.index("modelled-accrual-row"):]
        ends = [body.index(tag) for tag in ("</tr>", "</div>") if tag in body]
        assert ends, "the modelled-accrual element never closed"
        return body[:min(ends)]

    def test_a_gain_is_green_and_carries_an_explicit_plus(self, app):
        """Desktop, mobile and Plan all render ``+$96`` in the success token."""
        periods = _summary_periods()
        with app.app_context():
            footer = _render_grid_footer(
                app, periods,
                _summary_columns(periods, accrual="95.98"),
                _row_flags(accrual=True),
            )
            card = _render_mobile_card(
                app, periods[0],
                _summary_columns([periods[0]], accrual="95.98"),
                _row_flags(accrual=True),
            )
            recap = _render_plan_recap(
                app, periods[0],
                _summary_columns([periods[0]], accrual="95.98"),
                _row_flags(accrual=True),
            )

        for html in (footer, card):
            cell = self._accrual_cell(html)
            assert "text-success" in cell
            assert "balance-negative" not in cell
            assert "+$95.98" in cell
        assert "+$95.98" in recap
        assert "text-success" in recap

    def test_a_loss_is_the_danger_token_and_never_success(self, app):
        """The N-88 regression itself, on all three surfaces.

        ``-$142`` is ruling R-AH's own measured worst single column at a -10.5%
        return on the real Empower 401(k).  The assertion that ``text-success``
        is ABSENT is the firing control: it is the exact class the mobile card
        hard-coded, so re-introducing it fails here.
        """
        periods = _summary_periods()
        with app.app_context():
            footer = _render_grid_footer(
                app, periods,
                _summary_columns(periods, accrual="-142.11"),
                _row_flags(accrual=True),
            )
            card = _render_mobile_card(
                app, periods[0],
                _summary_columns([periods[0]], accrual="-142.11"),
                _row_flags(accrual=True),
                accrual_label="Growth",
            )
            recap = _render_plan_recap(
                app, periods[0],
                _summary_columns([periods[0]], accrual="-142.11"),
                _row_flags(accrual=True),
                accrual_label="Growth",
            )

        for html in (footer, card):
            cell = self._accrual_cell(html)
            assert "balance-negative" in cell
            assert "text-success" not in cell
            # The SIGN carries the meaning; colour is never the only signal.
            assert "-$142.11" in cell
        assert "-$142.11" in recap
        assert "text-success" not in recap
        assert "balance-negative" in recap

    def test_a_zero_column_is_neither_a_gain_nor_a_loss(self, app):
        """``$0`` renders plain -- not ``+$0`` and not green.

        Ruling R-O renders ``$0`` in every column of a window the row is on
        for, which is a state ``/investment``'s chip never faces, so the
        verbatim ``>= 0`` boundary it uses would paint an empty column as a
        gain.  Zero is neutral here (developer ruling 2026-07-27).
        """
        periods = _summary_periods()
        with app.app_context():
            columns = _summary_columns(periods)
            columns[periods[0].period_id] = balance_at.GridColumn(
                balance=Decimal("3000.00"), income=Decimal("2400.00"),
                expense=Decimal("1450.00"), net=Decimal("950.00"),
                elsewhere=Decimal("0.00"),
                period_timing=Decimal("0.00"),
                book_vs_bank=Decimal("0.00"),
                contribution=Decimal("0.00"), accrual=Decimal("95.98"),
            )
            footer = _render_grid_footer(
                app, periods, columns, _row_flags(accrual=True),
            )

        row = footer[footer.index("modelled-accrual-row"):]
        row = row[:row.index("</tr>")]
        cells = row.split("<td")
        # The accruing column is the first data cell after the sticky label.
        assert "+$95.98" in cells[2]
        assert "text-success" in cells[2]
        # The empty one reports $0.00 and claims nothing about it.  CENTS is
        # what makes these two cells tell different stories: at whole dollars
        # both read "$0" and only the colour distinguished them, and ruling
        # R-O's reason for the row being on screen at all was invisible.
        assert "$0.00" in cells[3]
        assert "+$0" not in cells[3]
        assert "text-success" not in cells[3]
        assert "balance-negative" not in cells[3]


class TestTheAccrualRowLabelIsPerKind:
    """Ruling R-AI: "Interest" on an HYSA, "Growth" on a 401(k), "Appreciation".

    Not a new vocabulary: the app already speaks all three, each on that kind's
    own page.  Those are PHRASES with their own windows baked in rather than
    instances of one string, so the route's map is the canonical source for the
    GRID's row and not a fourth copy of any of them.

    Rejected at the ruling: ONE word for every kind (a fourth vocabulary
    contradicting three shipped pages, and it renames the "Interest" row an
    HYSA has carried since PR #47), and keeping "Interest" everywhere (which
    would label a house's appreciation and a 401(k)'s market return "Interest").
    """

    def test_the_map_is_total_over_the_projection_kinds(self):
        """EVERY ``AccountProjectionKind`` has a word -- no default, no KeyError.

        The lookup is subscripted rather than ``.get``-with-a-default because a
        kind added to the enum without a word here must fail at the render
        rather than label a new kind silently and wrongly.  That only holds if
        the map is total, so this is the test that keeps it total.
        """
        # pylint: disable=import-outside-toplevel
        from app.routes.grid import _ACCRUAL_ROW_LABELS
        from app.services.account_projection import AccountProjectionKind

        assert set(_ACCRUAL_ROW_LABELS) == set(AccountProjectionKind)

    def test_each_kind_resolves_its_own_word(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A real account of each modelled kind gets that kind's word."""
        # pylint: disable=import-outside-toplevel
        from app.routes.grid import _accrual_row_label
        from tests._test_helpers import (
            make_appreciating_account,
            make_investment_account,
        )

        hysa = create_hysa_account(
            seed_user, db.session, seed_periods_today[0], Decimal("5000.00"),
        )
        with app.app_context():
            inv = make_investment_account(
                seed_user, db.session, seed_periods_today[0],
                Decimal("10000.00"),
            )
            prop = make_appreciating_account(
                seed_user, db.session, seed_periods_today[0],
                Decimal("400000.00"), Decimal("0.03000"),
            )
            loan = create_loan_account(
                seed_user, db.session,
                principal=Decimal("240000.00"),
            )

            assert _accrual_row_label(hysa) == "Interest"
            assert _accrual_row_label(inv) == "Growth"
            assert _accrual_row_label(prop) == "Appreciation"
            # A LIABILITY's accrual is interest CHARGED, so it must not be
            # named after an asset's growth.  The row cannot render for this
            # kind today, but ``resolve_grid_account`` can point the grid at a
            # loan (``grid_balance_view`` supports the degenerate cash view for
            # one), so the label is resolved on every such render and a wrong
            # word here is one commit away from being on screen.
            assert _accrual_row_label(loan) == "Interest"
            # PLAIN can never render the row (it resolves no ACCRUAL tier), but
            # it is the account every default /grid render resolves to, so the
            # lookup must answer it rather than raise.  It models no return at
            # all, so no word is truthful and it carries the neutral one.
            assert _accrual_row_label(seed_user["account"]) == "Growth"

    def test_the_zero_accounts_user_resolves_a_word_rather_than_crashing(self):
        """``account=None`` is the real zero-accounts state, not a hypothetical.

        ``_build_grid_view`` carries ``None`` for a user with no account rows at
        all, and ``classify_account(None)`` would ``AttributeError``.  Such a
        user has no columns, so no row ever renders -- but the label is resolved
        in the route before that is known.
        """
        # pylint: disable=import-outside-toplevel
        from app.routes.grid import _accrual_row_label

        assert _accrual_row_label(None) == "Growth"

    def test_the_hysa_grid_page_labels_the_row_interest(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """End to end: the real page renders the word, on both form factors.

        The route-level contract, because that is where ruling R-P binds: the
        desktop ``<tfoot>`` and the mobile This Period card are rendered from
        ONE context variable, so they cannot name the same row differently.
        """
        hysa = create_hysa_account(
            seed_user, db.session, seed_periods_today[0], Decimal("100000.00"),
        )
        resp = auth_client.get(f"/grid?account_id={hysa.id}")
        assert resp.status_code == 200
        html = resp.data.decode()

        footer = html[html.index('id="grid-summary"'):html.index("</tfoot>")]
        row = footer[footer.index("modelled-accrual-row"):]
        assert "Interest" in row[:row.index("</tr>")]

        card_start = html.index('id="mobile-tp-summary-')
        card = html[card_start:html.index('id="mobile-plan"', card_start)]
        bar = card[card.index("modelled-accrual-row"):]
        assert "Interest" in bar[:bar.index("</div>")]

    def test_the_balance_row_refresh_labels_the_row_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The self-refresh partial supplies its own label, not an empty one.

        Each of the three render entries resolves the label independently, so a
        ``balanceChanged`` refresh that dropped it would swap a headless row
        into the footer.
        """
        hysa = create_hysa_account(
            seed_user, db.session, seed_periods_today[0], Decimal("100000.00"),
        )
        resp = auth_client.get(
            f"/grid/balance-row?periods=6&offset=0&account_id={hysa.id}",
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        row = html[html.index("modelled-accrual-row"):]
        assert "Interest" in row[:row.index("</tr>")]

    def test_the_mobile_summary_refresh_labels_the_row_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """And so does the ``mobileCardSettled`` refresh."""
        hysa = create_hysa_account(
            seed_user, db.session, seed_periods_today[0], Decimal("100000.00"),
        )
        with app.app_context():
            current = current_pay_period(
                seed_user["user"].id,
            )
        resp = auth_client.get(
            f"/grid/this-period-summary?period_id={current.id}"
            f"&account_id={hysa.id}",
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        bar = html[html.index("modelled-accrual-row"):]
        assert "Interest" in bar[:bar.index("</div>")]
