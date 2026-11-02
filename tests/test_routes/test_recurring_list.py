"""
Tests for the unified Recurring surface: GET /templates and the unit toggle.

The unified /templates page (Recurring cluster Loop B) replaces the former
/templates + /transfers lists and the /obligations page.  It lists every
active recurring definition of all three kinds, a summary band, per-section
subtotals, and per row a monthly + per-paycheck equivalent, an engine-backed
next date, and share of section committed total.

These route-level tests assert the rendered surface: all kinds present,
subtotals in the HTML, the management surface shows one-time definitions the
old /obligations page hid, the unit toggle swaps every figure and persists,
and ownership isolation holds.  The producer's arithmetic is locked
separately in ``tests/test_services/test_recurring_view.py``.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.ref import AccountType
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import account_service
from app.utils.dates import month_name
from tests._test_helpers import (
    bind_rule_to_loan,
    create_loan_account,
    insert_trueup_event,
    loan_params_for,
    make_cadence_rule,
    make_loan_payment_template,
    state_template_price,
)
from tests.oracles.recurrence_baseline import (
    EVERY_PERIOD,
    MONTHLY,
    MONTHLY_FIRST,
    QUARTERLY,
)

#: Named so the cycle arithmetic below is not a bare literal.
MONTHS_IN_YEAR = 12


# ── Helpers ──────────────────────────────────────────────────────────


def _rule(tmpl, cadence, *, day_of_month=None, starts_on=None, end_date=None):
    """Author one rule of *cadence* ONTO *tmpl*, through the write door.

    Plan step R7c-b made the two-axis columns NOT NULL, so a rule naming only a
    pattern no longer produces a row; plan step R-F6 put the owning FK on the
    rule, so it cannot be written before the definition it belongs to.
    """
    return make_cadence_rule(
        tmpl, cadence,
        fires_on_day=day_of_month, starts_on=starts_on, end_date=end_date,
    )


def _txn(user, account, category, cadence, amount, *, type_enum, name,
         **rule_kwargs):
    """Create a transaction template, and its cadence when it has one."""
    tmpl = TransactionTemplate(
        user_id=user.id,
        account_id=account.id,
        category_id=category.id,
        transaction_type_id=ref_cache.txn_type_id(type_enum),
        name=name,
        default_amount=Decimal(amount),
    )
    db.session.add(tmpl)
    db.session.flush()
    # Its price, through the one write door as the create route states it:
    # the page prices a definition by its series since plan step X-bp.
    state_template_price(tmpl)
    if cadence is not None:
        _rule(tmpl, cadence, **rule_kwargs)
    return tmpl


def _savings(user, name="Test Savings"):
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=savings_type.id,
            name=name,
            anchor_balance=Decimal("5000.00"),
        ),
    )
    db.session.add(account)
    db.session.flush()
    return account


def _transfer(user, from_account, to_account, cadence, amount, *, name,
              **rule_kwargs):
    """Create a transfer template and its cadence."""
    tmpl = TransferTemplate(
        user_id=user.id,
        from_account_id=from_account.id,
        to_account_id=to_account.id,
        name=name,
        default_amount=Decimal(amount),
    )
    db.session.add(tmpl)
    db.session.flush()
    state_template_price(tmpl)
    _rule(tmpl, cadence, **rule_kwargs)
    return tmpl


# ── Rendering ────────────────────────────────────────────────────────


class TestUnifiedRender:
    """The unified surface renders every kind with correct figures."""

    def test_all_three_kinds_render(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Income, expense, and transfer definitions all appear by name."""
        user = seed_user["user"]
        checking = seed_user["account"]
        category = seed_user["categories"]["Rent"]
        savings = _savings(user)

        _txn(user, checking, category, EVERY_PERIOD, "100.00",
             type_enum=TxnTypeEnum.EXPENSE, name="Electricity")
        _txn(user, checking, category, EVERY_PERIOD, "1500.00",
             type_enum=TxnTypeEnum.INCOME, name="Paycheck")
        _transfer(user, checking, savings, MONTHLY, "500.00",
                  name="Savings Transfer", day_of_month=15)
        db.session.commit()

        resp = auth_client.get("/templates")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Electricity" in html
        assert "Paycheck" in html
        assert "Savings Transfer" in html
        assert "Transfers" in html

    def test_subtotal_renders_monthly(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A biweekly $100 expense shows its $216.67 monthly equivalent.

        100 * 26 / 12 = 216.666... -> $216.67 (ROUND_HALF_UP).
        """
        user = seed_user["user"]
        checking = seed_user["account"]
        category = seed_user["categories"]["Rent"]
        _txn(user, checking, category, EVERY_PERIOD, "100.00",
             type_enum=TxnTypeEnum.EXPENSE, name="Electric")
        db.session.commit()

        html = auth_client.get("/templates").data.decode()
        assert "$216.67" in html
        # The lone recurring expense is 100% of its section's committed total.
        assert "100.0% of section" in html

    def test_a_rule_less_definition_is_not_listed_but_its_archive_is(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The Recurring list shows definitions WITH a rule (R-BAL23).

        **This asserted the opposite until plan step balance:X-bi-7b.**  A
        rule-less transaction definition is a ONE-OFF's since that step: the
        grid popover is its whole lifecycle, so listing it here would give
        every one-off a second door and, once bank import mints one per
        envelope, fill this page with lines that repeat nothing.  The active
        sections ask each definition ``recurs`` and drop the rest.

        **The Archived drawer is deliberately NOT filtered**: an archived
        one-off's row is soft-deleted, so that drawer's Unarchive is the only
        door left to it, and hiding it there would strand the definition.
        """
        user = seed_user["user"]
        checking = seed_user["account"]
        category = seed_user["categories"]["Rent"]
        _txn(user, checking, category, None, "999.00",
             type_enum=TxnTypeEnum.EXPENSE, name="One Time Buy")
        parked = _txn(user, checking, category, None, "5.00",
                      type_enum=TxnTypeEnum.EXPENSE, name="Parked One Off")
        parked.is_active = False
        db.session.commit()

        html = auth_client.get("/templates").data.decode()
        assert "One Time Buy" not in html
        assert "Parked One Off" in html
        drawer = html[html.index("Parked One Off") - 1200:][:2400]
        assert "Archived" in drawer

    def test_a_recurring_definition_is_listed_with_its_cadence(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The firing control for the filter above: a definition WITH a rule renders."""
        user = seed_user["user"]
        checking = seed_user["account"]
        category = seed_user["categories"]["Rent"]
        _txn(user, checking, category, EVERY_PERIOD, "999.00",
             type_enum=TxnTypeEnum.EXPENSE, name="Every Paycheck Buy")
        db.session.commit()

        html = auth_client.get("/templates").data.decode()
        assert "Every Paycheck Buy" in html

    def test_empty_state(self, auth_client, seed_user, db, seed_periods_today):
        """With no definitions the empty-state message renders."""
        html = auth_client.get("/templates").data.decode()
        assert "No active recurring definitions" in html

    def test_body_carries_js_view_hooks(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The body carries the data-* hooks recurring.js drives: the swap
        target, the per-section markers, and the per-row sort keys.  Pinning
        them here means a template edit that drops one is caught rather than
        silently breaking the client-side search / filter / sort.
        """
        user = seed_user["user"]
        checking = seed_user["account"]
        category = seed_user["categories"]["Rent"]
        _txn(user, checking, category, EVERY_PERIOD, "100.00",
             type_enum=TxnTypeEnum.EXPENSE, name="Electric")
        db.session.commit()

        html = auth_client.get("/templates").data.decode()
        assert 'id="recurring-body"' in html
        assert 'data-recurring-section="expense"' in html
        assert "data-recurring-row" in html
        assert 'data-sort-name="electric"' in html
        # The net verdict hero label anchors the summary band.
        assert "Net committed" in html


# ── Unit toggle ──────────────────────────────────────────────────────


class TestUnitToggle:
    """The Monthly / Per-paycheck toggle swaps every figure and persists."""

    def test_default_is_monthly_and_toggle_persists(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Default view is monthly; POSTing the per-paycheck preference swaps
        every figure and persists across requests.

        A monthly $1,300 expense: monthly equivalent $1,300.00,
        per-paycheck 1300 * 12 / 26 = $600.00.  The $600.00 figure appears
        only in the per-paycheck view; the $1,300.00 amount always shows.
        """
        user = seed_user["user"]
        checking = seed_user["account"]
        category = seed_user["categories"]["Rent"]
        _txn(user, checking, category, MONTHLY, "1300.00",
             type_enum=TxnTypeEnum.EXPENSE, name="Rent Bill", day_of_month=1)
        db.session.commit()

        # Default: monthly.  The per-paycheck-only figure is absent.
        html = auth_client.get("/templates").data.decode()
        assert "$1,300.00" in html
        assert "$600.00" not in html

        # Persist the per-paycheck preference.
        post = auth_client.post(
            "/templates/unit-preference", data={"unit": "per_paycheck"},
        )
        assert post.status_code == 302

        # Now every figure is per-paycheck: the $600.00 equivalent shows.
        html2 = auth_client.get("/templates").data.decode()
        assert "$600.00" in html2

        # And the choice persisted on the settings row.
        db.session.refresh(seed_user["settings"])
        assert seed_user["settings"].recurring_show_per_paycheck is True

    def test_invalid_unit_is_ignored(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """An unrecognized unit value leaves the preference unchanged."""
        post = auth_client.post(
            "/templates/unit-preference", data={"unit": "furlongs"},
        )
        assert post.status_code == 302
        db.session.refresh(seed_user["settings"])
        assert seed_user["settings"].recurring_show_per_paycheck is False

    def test_htmx_toggle_returns_body_fragment(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """An HX-Request toggle returns the re-rendered body fragment (200,
        not a redirect) in the chosen unit, and still persists the choice.

        A monthly $1,300 expense: per-paycheck 1300 * 12 / 26 = $600.00.
        The fragment carries that figure and omits the page chrome -- the
        search toolbar lives in list.html, not the swapped body -- so a
        stray full-page render would be caught.
        """
        user = seed_user["user"]
        checking = seed_user["account"]
        category = seed_user["categories"]["Rent"]
        _txn(user, checking, category, MONTHLY, "1300.00",
             type_enum=TxnTypeEnum.EXPENSE, name="Rent Bill", day_of_month=1)
        db.session.commit()

        resp = auth_client.post(
            "/templates/unit-preference",
            data={"unit": "per_paycheck"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$600.00" in html
        assert "Search by name" not in html

        db.session.refresh(seed_user["settings"])
        assert seed_user["settings"].recurring_show_per_paycheck is True

    def test_htmx_invalid_unit_returns_fragment_unchanged(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """An unrecognized unit on an HX request still gets a body fragment
        (200, in the unchanged unit), never a redirect the swap would follow
        and nest a whole page inside #recurring-body.  The preference stays
        put.
        """
        resp = auth_client.post(
            "/templates/unit-preference",
            data={"unit": "furlongs"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        # It is the fragment (not the redirected full page).
        assert "Search by name" not in resp.data.decode()
        db.session.refresh(seed_user["settings"])
        assert seed_user["settings"].recurring_show_per_paycheck is False


# ── Ownership isolation ──────────────────────────────────────────────


class TestUnifiedIDOR:
    """Only the authenticated user's definitions appear."""

    def test_only_current_user_definitions(
        self, auth_client, seed_user, second_user, db, seed_periods_today,
    ):
        """User 2's templates never appear on user 1's Recurring surface."""
        user1 = seed_user["user"]
        user2 = second_user["user"]
        checking1 = seed_user["account"]
        checking2 = second_user["account"]
        category1 = seed_user["categories"]["Rent"]
        category2 = list(second_user["categories"].values())[0]

        _txn(user1, checking1, category1, MONTHLY, "1200.00",
             type_enum=TxnTypeEnum.EXPENSE, name="My Rent", day_of_month=1)
        _txn(user2, checking2, category2, MONTHLY, "900.00",
             type_enum=TxnTypeEnum.EXPENSE, name="Their Rent", day_of_month=1)
        db.session.commit()

        html = auth_client.get("/templates").data.decode()
        assert "My Rent" in html
        assert "Their Rent" not in html


# ── The rendered Recurrence cell (plan step R7a) ──────────────────────


class TestTheRenderedRecurrenceCell:
    """The Recurrence column's WORDS, on the page, for each cadence shape.

    The cell had no rendered-content coverage at all before plan step R7a: the
    only assertion anywhere was the rule-less "One-time" case above, so eight
    Jinja branches reading four rule columns were held up by nothing.  These
    drive the real route and read the real HTML.
    """

    def _row_markup(self, html, name):
        """Return the markup of the row whose definition is *name*.

        Scoped to the row rather than the document: a page-wide substring
        would pass on any other cell that happened to say the same thing.
        """
        assert name in html, f"{name} is not on the page"
        return html[html.index(name):][:1200]

    def test_an_every_paycheck_definition_reads_every_paycheck(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The paycheck-space cadence names no calendar day."""
        user = seed_user["user"]
        _txn(user, seed_user["account"], seed_user["categories"]["Rent"],
             EVERY_PERIOD, "100.00",
             type_enum=TxnTypeEnum.EXPENSE, name="Electric")
        db.session.commit()

        html = auth_client.get("/templates").data.decode()

        assert "Every paycheck" in self._row_markup(html, "Electric")

    def test_a_monthly_definition_names_its_day(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A rule that fires every month is distinguished only by its day."""
        user = seed_user["user"]
        _txn(user, seed_user["account"], seed_user["categories"]["Rent"],
             MONTHLY,
             "100.00", type_enum=TxnTypeEnum.EXPENSE, name="Van Payment",
             day_of_month=22)
        db.session.commit()

        html = auth_client.get("/templates").data.decode()

        assert "Monthly (day 22)" in self._row_markup(html, "Van Payment")

    def test_a_monthly_first_definition_names_the_paycheck(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A deferring placement is named, and its implied day 1 is not."""
        user = seed_user["user"]
        _txn(user, seed_user["account"], seed_user["categories"]["Rent"],
             MONTHLY_FIRST, "100.00",
             type_enum=TxnTypeEnum.EXPENSE, name="Phone Allowance")
        db.session.commit()

        html = auth_client.get("/templates").data.decode()
        row = self._row_markup(html, "Phone Allowance")

        assert "Monthly (first paycheck)" in row
        assert "day 1" not in row

    def test_a_quarterly_definition_now_names_its_day_too(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The uniform shape, ruled 2026-08-08.

        The old cell showed a quarterly rule's month and never its day, while
        showing a yearly rule both -- three branches written independently.
        One function has no room for that difference.
        """
        user = seed_user["user"]
        # The month the cell names is the FIRST OCCURRENCE'S (ruling R-R16),
        # so it is stated by the authored date rather than assigned after the
        # fact -- ``month_of_year`` was dropped at plan step R7c-c and setting
        # it here wrote a Python attribute no column and no reader saw.
        _txn(user, seed_user["account"], seed_user["categories"]["Rent"],
             QUARTERLY, "60.00", type_enum=TxnTypeEnum.EXPENSE,
             name="Mint Mobile", day_of_month=21)
        db.session.commit()

        html = auth_client.get("/templates").data.decode()
        row = self._row_markup(html, "Mint Mobile")

        assert "Quarterly (" in row
        assert " 21)" in row, "the quarterly cell must name its day"

    def test_a_quarterly_definition_authored_before_the_schedule_names_it(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The cell names the rule's OWN first occurrence, wherever it falls.

        This asserted the opposite until plan step R7c-b, and the reversal is
        ruling R-R16.  ``Anchor Disposal`` is quarterly, authored March, day 2,
        on a schedule opening later that year -- and the cell said "Mar", a
        month already behind the schedule, which plan step R7a-1 treated as the
        defect and taught the cell to walk forward from.

        The date is AUTHORED now: 2 March IS when the rule first happens, and
        it is a fact about the rule rather than about whichever schedule the
        owner currently has.  Walking forward made the cell HORIZON-DEPENDENT
        -- the same shape as plan ledger row D10 -- so the same definition read
        differently after a pay-period rebuild that changed nothing about it.

        Nothing is lost from the surface: the row's own "next" column answers
        when the definition next fires, which is the question the walk was
        standing in for, and it answers it for every row rather than only for
        the ones whose authored month is behind the schedule.

        **The date is a whole year before the schedule opens**, so no forward
        walk could land on it by coincidence: a cell that still walked would
        name some month at or after the opening, and every one of those is a
        different string from the one asserted here.
        """
        user = seed_user["user"]
        authored = seed_periods_today[0].start_date.replace(
            year=seed_periods_today[0].start_date.year - 1,
        ).replace(month=3, day=2)
        _txn(user, seed_user["account"], seed_user["categories"]["Rent"],
             QUARTERLY, "45.00", type_enum=TxnTypeEnum.EXPENSE,
             name="Anchor Disposal", starts_on=authored)
        db.session.commit()

        html = auth_client.get("/templates").data.decode()
        row = self._row_markup(html, "Anchor Disposal")

        # ``abbr=True`` because that is the form the list views use, taken
        # from the app's own producer rather than spelled out: ``month_name``
        # exists so the describer and this assertion cannot come to name March
        # two different things.
        assert f"Quarterly ({month_name(3, abbr=True)} 2)" in row, (
            "the cell must name the rule's own authored first occurrence, not "
            "a month walked forward onto the current schedule"
        )

    def test_an_end_date_renders_as_its_own_muted_line(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The closing bound is a separate line, not folded into the phrase."""
        from datetime import timedelta  # pylint: disable=import-outside-toplevel

        user = seed_user["user"]
        end = seed_periods_today[0].start_date + timedelta(days=800)
        # Authored WITH the cadence rather than assigned to the rule
        # afterwards: the rule cannot exist before the definition that owns it
        # (plan step R-F6), so the closing bound rides in with the rest of what
        # the fixture states.
        _txn(user, seed_user["account"], seed_user["categories"]["Rent"],
             MONTHLY, "1500.00", type_enum=TxnTypeEnum.EXPENSE,
             name="Mortgage", day_of_month=1, end_date=end)
        db.session.commit()

        html = auth_client.get("/templates").data.decode()
        row = self._row_markup(html, "Mortgage")

        assert "Monthly (day 1)" in row
        assert f"until {end.strftime('%b %d, %Y')}" in row

    def test_an_archived_definition_still_shows_how_it_repeated(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The Archived drawer's cell comes from the producer too.

        It bypassed the producer entirely before plan step R7a -- raw ORM rows
        handed to a template that computed the phrase itself -- so this row is
        the one the label rewrite could most easily have dropped.
        """
        user = seed_user["user"]
        tmpl = _txn(
            user, seed_user["account"], seed_user["categories"]["Rent"],
            MONTHLY,
            "25.00", type_enum=TxnTypeEnum.EXPENSE, name="Retired Streaming",
            day_of_month=9,
        )
        tmpl.is_active = False
        db.session.commit()

        html = auth_client.get("/templates").data.decode()
        row = self._row_markup(html, "Retired Streaming")

        assert "Archived (1)" in html
        assert "Monthly (day 9)" in row

    def test_an_archived_one_time_definition_reads_one_time(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The drawer's rule-less branch, which has no producer answer at all."""
        user = seed_user["user"]
        tmpl = _txn(
            user, seed_user["account"], seed_user["categories"]["Rent"],
            None, "25.00", type_enum=TxnTypeEnum.EXPENSE,
            name="Retired One Off",
        )
        tmpl.is_active = False
        db.session.commit()

        html = auth_client.get("/templates").data.decode()

        assert "One-time" in self._row_markup(html, "Retired One Off")

    def test_a_retired_loan_payments_stop_line_is_the_loans_closing_date(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The cell names when the LOAN closed, with nothing stored to name it.

        Plan step R7d-d.  Before it the second line came from the cached
        ``end_date`` column; here that column is NULL (asserted), the loan was
        trued to ``$0.00`` on 2026-02-15, and the page still says so.  The date
        is DERIVED through the composed door -- read backward over the loan's
        own events (plan step ``recurrence:R7d-h``) -- so it does not move
        with the day the page is rendered, which is what lets this route test
        run on the ambient clock: on any day from 2026-02-15 on, that loan
        closed on that day.
        """
        loan = create_loan_account(
            seed_user, db.session, name="Retired Van",
            principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
            origination_date=date(2026, 1, 1), payment_day=1,
        )
        insert_trueup_event(
            loan_params_for(db.session, loan.id), Decimal("0.00"),
            anchor_date=date(2026, 2, 15),
        )
        tpl = make_loan_payment_template(
            db.session, seed_user, loan, cadence=MONTHLY, fires_on_day=1,
        )
        # The production door for the opening bound: the first contractual
        # installment (2026-02-01) precedes the closing, so the stop is a date
        # and not "never runs".
        bind_rule_to_loan(tpl.recurrence_rule, loan.id)
        tpl.name = "Retired Van Payment"
        db.session.commit()
        assert tpl.recurrence_rule.end_date is None, (
            "precondition: nothing stored could have supplied the stop line"
        )

        html = auth_client.get("/templates").data.decode()
        row = self._row_markup(html, "Retired Van Payment")

        assert "Monthly (day 1)" in row
        assert "until Feb 15, 2026" in row
