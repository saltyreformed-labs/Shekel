"""Account route tests: the difference a true-up shows before it is saved, and the balance history
card.
"""

import re
from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import CompoundingFrequencyEnum
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.utils.dates import display_today
from tests._test_helpers import all_periods
from app.models.interest_params import InterestParams
from app.models.ref import AccountType
from app.services import account_service, balance_at, pay_period_service

from ._helpers import _create_other_user_account


class TestAnchorDifference:
    """The true-up form shows the difference BEFORE it is saved.

    Plan step **X-f2-a**, ruling **R-EU** -- R-DH (f)'s second half.  The
    figure's own contract is pinned in
    ``tests/test_services/test_balance_at/test_records.py::TestRecordsBalanceAt``; these
    cases grade the WIRING: that the editor mounts the region without previewing
    its own prefill, that the two boxes reach the producer by the write door's
    parsing rules, and that a state the save would refuse is refused here.
    """

    STATEMENT_DAYS_BACK = 20

    def _statement_day(self, seed_user):
        """Return a past day inside both bounds, asserting the fixture affords it."""
        day = display_today() - timedelta(days=self.STATEMENT_DAYS_BACK)
        floor = pay_period_service.earliest_recordable_day(seed_user["user"].id)
        assert floor <= day, (
            f"the schedule starts {floor}, so {day} is not assertable; "
            "seed_periods_today no longer affords 20 days of history"
        )
        return day

    def _preview(self, auth_client, account_id, **params):
        """GET the preview region, returning its decoded body."""
        return auth_client.get(
            f"/accounts/{account_id}/anchor-difference", query_string=params,
        ).data.decode()

    def test_the_editor_mounts_the_region_without_previewing_its_prefill(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The region is present, empty, and NOT triggered on load.

        **The ``load`` trigger is the defect, not an optimisation.**  The editor
        opens prefilled with the governing balance, so a preview on open reads
        "You entered <a figure the user did not enter>" and captions the gap as
        the app's fault -- over records that may be perfectly complete.  Pressing
        Enter on that prefill then asserts the stale balance.  Asserting the
        ABSENCE of the trigger is the only way this stays fixed: the feature
        works either way, and only this control says which way is correct.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            html = auth_client.get(
                f"/accounts/{acct_id}/anchor-form"
            ).data.decode()

            assert f'id="anchor-difference-{acct_id}"' in html
            assert f'/accounts/{acct_id}/anchor-difference' in html
            assert 'hx-include="closest form"' in html
            trigger = re.search(
                r'id="anchor-difference-\d+"[^>]*hx-trigger="([^"]*)"', html,
            )
            assert trigger is not None, "the region carries no hx-trigger"
            spec = trigger.group(1)
            assert "load" not in spec
            # ONE trigger spec, measured in Chromium against the repo's own
            # vendored htmx: a ``keyup`` + ``change`` PAIR costs a second
            # identical fold on blur, because htmx tracks ``changed`` per spec
            # and the ``change`` arm sees the typed value for the first time.
            assert "," not in spec, f"more than one trigger spec: {spec!r}"

    def test_the_region_targets_itself_rather_than_inheriting_the_forms(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The preview swaps into ITSELF, never into the form containing it.

        **``hx-target`` is an INHERITED htmx attribute, and this region sits
        inside the true-up form, which declares ``hx-target="this"`` for its own
        PATCH.**  htmx resolves an inherited ``"this"`` to the element that
        DECLARED the attribute, never to the element issuing the request
        (``getTarget`` -> ``closest(elt, '[hx-target]')``).  So a region with no
        target of its own lands its ``innerHTML`` response on the FORM: the
        first preview replaces the balance box, the date box and both buttons
        with the difference table, mid-keystroke.

        Reproduced in a real browser against production data before this test
        existed.  Typing ``2193.69`` with a pause after ``21`` fired the preview,
        the editor ceased to exist, and the htmx trace read
        ``elt=div#anchor-difference-1 target=form.d-inline-block`` followed by
        ``htmx:beforeCleanupElement`` on the balance input and the Save button.
        No balance could be entered and none was ever recorded -- the account
        sat at a stale anchor while every projection flowed forward from it.

        **Asserting the attribute is the only thing that keeps this fixed.**  The
        route, the fragment and the trigger were all correct; a response body
        cannot show where it will be swapped, so every route test here passed
        with the editor being destroyed on each preview.  The sibling test above
        pins the trigger for the same reason -- both are attributes no assertion
        over the rendered figures can see.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            html = auth_client.get(
                f"/accounts/{acct_id}/anchor-form"
            ).data.decode()

            region = re.search(
                rf'<div id="anchor-difference-{acct_id}"[^>]*>', html,
            )
            assert region is not None, "the preview region is not rendered"
            target = re.search(r'hx-target="([^"]*)"', region.group(0))
            assert target is not None, (
                "the preview region declares no hx-target, so it inherits the "
                "form's and swaps over the editor"
            )
            # "this" resolves to the declaring element -- now the region itself;
            # its own id is the equally correct spelling.  Anything else names
            # an element outside the region.
            assert target.group(1) in ("this", f"#anchor-difference-{acct_id}"), (
                f"the preview targets {target.group(1)!r}, not itself"
            )

    def test_it_renders_records_entered_and_difference(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """All three figures render, against the RECORDS.

        The entered balance is placed a known distance below the records', so a
        response that echoed one figure twice, or subtracted the other way,
        could not pass.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = seed_user["account"]
            day = self._statement_day(seed_user)
            records = balance_at.records_balance_at(
                account, balance_at.BalanceContext.build(user_id), day,
            )
            entered = records - Decimal("48.25")

            html = self._preview(
                auth_client, account.id,
                anchor_balance=str(entered), observed_on=day.isoformat(),
            )

            assert "Your records" in html
            assert f"${records:,.2f}" in html
            assert f"${entered:,.2f}" in html
            assert "-$48.25" in html
            assert day.strftime("%b %-d, %Y") in html

    def test_it_reports_the_records_gap_on_a_day_already_recorded(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Re-opening a recorded day compares against the RECORDS, not the entry.

        The defect that sent this leaf back: the first build compared against the
        account's current balance, which an assertion RESETS -- so on a
        correction it reported the gap between the user's two successive guesses,
        with a sign that can oppose the real one.  Both figures are hand-computed
        here so a regression to the reset value fails rather than merely
        differing.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = seed_user["account"]
            day = self._statement_day(seed_user)
            records = balance_at.records_balance_at(
                account, balance_at.BalanceContext.build(user_id), day,
            )

            auth_client.patch(
                f"/accounts/{account.id}/true-up",
                data={
                    "anchor_balance": str(records + Decimal("625.00")),
                    "observed_on": day.isoformat(),
                },
            )
            db.session.expire_all()

            entered = records + Decimal("445.00")
            html = self._preview(
                auth_client, account.id,
                anchor_balance=str(entered), observed_on=day.isoformat(),
            )

            assert f"${records:,.2f}" in html
            assert "+$445.00" not in html  # the macro renders a bare figure
            assert "$445.00" in html
            # The reset value must be absent: it is what the broken build showed.
            assert f"${records + Decimal('625.00'):,.2f}" not in html
            # And the sign is the TRUE one -- money the records do not account
            # for -- not the "-$180.00" a comparison against the entry produces.
            assert "money Shekel has not accounted for" in html

    def test_the_three_verdicts_each_render_their_own_sentence(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Negative, zero and positive each get their own caption.

        The sign's MEANING is decided in the route (``difference_verdict``) and
        the partial only maps it to copy -- but nothing asserted the mapping, so
        a wrong caption was invisible to the suite.  All three branches, one
        case, because the value of this control is that it covers the set.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = seed_user["account"]
            day = self._statement_day(seed_user)
            records = balance_at.records_balance_at(
                account, balance_at.BalanceContext.build(user_id), day,
            )

            for delta, expected in (
                (Decimal("-25.00"), "spend Shekel has not recorded"),
                (Decimal("0.00"), "your records agree with this balance"),
                (Decimal("25.00"), "money Shekel has not accounted for"),
            ):
                html = self._preview(
                    auth_client, account.id,
                    anchor_balance=str(records + delta),
                    observed_on=day.isoformat(),
                )
                assert expected in html, f"delta {delta} rendered: {html!r}"

    def test_a_modelled_account_previews_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An account carrying a modelled tier renders an EMPTY region.

        A brokerage is not being reconciled against a bank statement, and its
        recorded cash is a fraction of what its screens show -- so a difference
        drawn against it would caption a model-vs-market gap as untracked spend
        (finding **N-213**).
        """
        from tests._test_helpers import make_investment_account  # pylint: disable=import-outside-toplevel

        with app.app_context():
            user_id = seed_user["user"].id
            periods = all_periods(user_id)
            inv = make_investment_account(
                seed_user, db.session, periods[0], Decimal("10000.00"),
            )
            db.session.commit()

            html = self._preview(
                auth_client, inv.id, anchor_balance="10500.00",
            )

            assert html.strip() == ""

    def test_a_blank_balance_box_previews_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Clearing the balance leaves an EMPTY region, not a placeholder.

        A rendered ``$0.00`` difference is indistinguishable from "your records
        agree", so the empty state has to actually be empty.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            day = self._statement_day(seed_user)

            html = self._preview(
                auth_client, acct_id,
                anchor_balance="", observed_on=day.isoformat(),
            )

            assert html.strip() == ""

    def test_a_blank_date_box_means_today(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An empty date box previews TODAY, the same default the save applies.

        ``resolve_observation_day`` owns "when is an assertion dated" for both
        anchor write doors (ruling R-ER); this path calls it rather than reading
        a clock of its own.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            html = self._preview(
                auth_client, acct_id,
                anchor_balance="1000.00", observed_on="",
            )

            assert display_today().strftime("%b %-d, %Y") in html

    def test_an_unparseable_day_is_refused_not_defaulted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A date box holding a non-date says so instead of previewing today.

        Blank means "now"; ``2026-13-40`` means the user is mid-edit on a day
        that is not a day, and previewing today's figure under a date box showing
        something else would caption a figure with the wrong day.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            html = self._preview(
                auth_client, acct_id,
                anchor_balance="1000.00", observed_on="2026-13-40",
            )

            assert "Enter a date" in html
            assert "Your records" not in html
            assert display_today().strftime("%b %-d, %Y") not in html
            # The schema's FIELD NAME must not reach the user: the box they see
            # is labelled "Balance as of", not "observed_on".
            assert "observed_on" not in html

    def test_a_future_day_is_refused_by_the_write_door_s_own_rule(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A day the save would refuse previews the refusal, not a figure."""
        with app.app_context():
            acct_id = seed_user["account"].id
            tomorrow = display_today() + timedelta(days=1)

            html = self._preview(
                auth_client, acct_id,
                anchor_balance="1000.00", observed_on=tomorrow.isoformat(),
            )

            assert "has not happened yet" in html
            assert "Your records" not in html

    def test_a_day_below_the_schedule_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The FLOOR is previewed as a refusal too, not only the ceiling."""
        with app.app_context():
            user_id = seed_user["user"].id
            acct_id = seed_user["account"].id
            below = pay_period_service.earliest_recordable_day(
                user_id,
            ) - timedelta(days=1)

            html = self._preview(
                auth_client, acct_id,
                anchor_balance="1000.00", observed_on=below.isoformat(),
            )

            assert "recorded history starts" in html
            assert "Your records" not in html

    def test_it_refuses_an_amortizing_loan(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A loan previews the kind refusal, matching both anchor doors.

        Reachable as a RACE rather than an ordinary click -- an account's kind is
        editable -- and the region answers the same sentence ``anchor_form`` and
        ``true_up`` do rather than a balance nobody may assert here.
        """
        from tests._test_helpers import create_loan_account  # pylint: disable=import-outside-toplevel

        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Preview Gate Loan",
                principal=Decimal("10000.00"), rate=Decimal("0.05000"),
            )
            db.session.commit()

            html = self._preview(
                auth_client, loan.id, anchor_balance="1000.00",
            )

            assert "not a cash anchor" in html
            assert "Your records" not in html

    def test_another_users_account_is_not_previewable(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The preview is an ownership-scoped read, like every other anchor route.

        It renders a real balance, so an unscoped one would be an IDOR that leaks
        another owner's figure through a route nobody thinks of as a balance
        endpoint.  404 exactly -- ``get_or_404`` collapses not-found and
        not-yours, and admitting a redirect here would let an auth regression
        pass this control.
        """
        with app.app_context():
            other = _create_other_user_account()

            response = auth_client.get(
                f"/accounts/{other['account'].id}/anchor-difference",
                query_string={"anchor_balance": "1000.00"},
            )

            assert response.status_code == 404
            assert b"Your records" not in response.data


class TestTheBalanceHistoryCard:
    """The durable record of a balance assertion -- plan step X-f2-b (R-EV).

    The card closes finding **N-205**: before it, the only evidence a
    back-dated assertion landed was an 8-second toast, and no template read
    ``AccountAnchorHistory`` at all except the GOVERNING assertion's "as of"
    caption -- which by definition is never the back-dated row.

    Every figure is graded at the producer
    (``test_services/test_balance_at/test_records.py::TestCashAnchorHistory``); what is
    graded HERE is the route's own three decisions -- which rows are shown by
    default, that the card is reachable only by its owner, and that it refreshes
    when a write appends to it.
    """

    @staticmethod
    def _assert_balance(auth_client, account_id, balance, observed_on=None):
        """Record a balance through the real PATCH route."""
        data = {"anchor_balance": balance}
        if observed_on is not None:
            data["observed_on"] = observed_on.isoformat()
        response = auth_client.patch(
            f"/accounts/{account_id}/true-up", data=data,
        )
        assert response.status_code == 200

    def test_the_page_carries_the_log_and_refreshes_it_on_a_write(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The card renders on the page and re-fetches on ``balanceChanged``.

        The refresh is not decoration: a true-up APPENDS an assertion, so a
        card left un-refreshed would omit the very row the user just recorded
        -- the defect the card exists to close, one layer up.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            self._assert_balance(auth_client, acct_id, "1234.56")

            html = auth_client.get(
                f"/accounts/{acct_id}/details",
            ).data.decode()

            assert "Balance history" in html
            assert f'id="balance-history-{acct_id}"' in html
            assert f"/accounts/{acct_id}/balance-history" in html
            region = html.split(f'id="balance-history-{acct_id}"', 1)[1]
            assert 'hx-trigger="balanceChanged from:body"' in region.split(
                "</div>", 1,
            )[0]
            assert "$1,234.56" in html

    def test_the_fragment_shows_a_write_the_page_predates(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The refresh target really does pick up a newly appended row.

        The region's trigger is asserted above; this is the other half -- that
        what it fetches has moved.  Without it a card wired to a stale producer
        would pass the markup test and still show yesterday's log forever.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            before = auth_client.get(
                f"/accounts/{acct_id}/balance-history",
            ).data.decode()
            assert "$4,321.00" not in before

            self._assert_balance(auth_client, acct_id, "4321.00")

            after = auth_client.get(
                f"/accounts/{acct_id}/balance-history",
            ).data.decode()
            assert "$4,321.00" in after

    def test_the_default_view_is_capped_and_says_what_it_hides(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Past twelve rows the card discloses the rest rather than growing.

        The real Checking account carries 57 assertions over 133 days and grows
        by about 13 a month, so the loan twin's "render every row" shape -- it
        has ONE anchor -- would make the log the page's dominant element
        (developer ruling 1, 2026-08-10).
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            today = display_today()
            # 14 true-ups on distinct days, plus the account's opening = 15.
            for offset in range(14, 0, -1):
                self._assert_balance(
                    auth_client, acct_id, f"{1000 + offset}.00",
                    observed_on=today - timedelta(days=offset),
                )

            html = auth_client.get(
                f"/accounts/{acct_id}/balance-history",
            ).data.decode()

            # One header, fifteen assertions, and the OPENING pinned in the
            # ``<tfoot>`` beneath them (plan step X-f3c-2b): the books opening
            # is not an assertion, so it is rendered as its own row rather
            # than badged onto whichever assertion shares its day -- a day
            # that no longer exists once the books are moved back to reach the
            # account's earliest record.
            assert html.count("<tr>") == 1 + 15 + 1, (
                "every assertion is in the DOM -- the disclosure hides rows, "
                "it does not drop them"
            )
            assert "Show all 15" in html
            assert "12 of 15 recorded" in html
            # The rows past the cap are behind the collapse rather than gone.
            # Bounded at the collapsed ``<tbody>``'s own close: the OPENING
            # row sits in the table's ``<tfoot>``, AFTER it, and is pinned
            # visible whether the disclosure is open or not (plan step
            # X-f3c-2b) -- so an unbounded slice would count it as hidden.
            hidden = html.split('class="collapse"', 1)[1].split("</tbody>", 1)[0]
            assert hidden.count("<tr>") == 3

    def test_the_rows_shown_are_the_most_recently_RECORDED(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A back-dated write is visible without expanding the disclosure.

        The card's whole job is giving that write a retrieval path (**N-205**),
        and sorting by the day a balance was TRUE defeats it: a balance
        recorded today for a day three weeks back sits at that day's position,
        which on the real Checking account is 40 rows down.  So the default
        view is chosen by ``recorded_on`` and rendered in ``observed_on`` order.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            today = display_today()
            for offset in range(14, 0, -1):
                self._assert_balance(
                    auth_client, acct_id, f"{2000 + offset}.00",
                    observed_on=today - timedelta(days=offset),
                )
            # NOW record an old statement.  Its ``observed_on`` is older than
            # every row above, so an ``observed_on``-ranked cap would bury it.
            self._assert_balance(
                auth_client, acct_id, "1777.77",
                observed_on=today - timedelta(days=30),
            )

            html = auth_client.get(
                f"/accounts/{acct_id}/balance-history",
            ).data.decode()
            shown = html.split('class="collapse"', 1)[0]

            assert "$1,777.77" in shown, (
                "the row just recorded must be visible without expanding, or "
                "the card does not answer the question it exists for"
            )
            # And it announces that it is back-dated, so it is not read as a
            # balance that was true today.
            #
            # **Scoped to the row under test.**  A bare ``"entered" in shown``
            # is near-vacuous here: every row in this fixture is back-dated by
            # construction, so the caption is present whether or not THIS row
            # carries one.  Slicing to the cell that holds the figure is what
            # makes the assertion about the row it names.
            row = shown.split("$1,777.77", 1)[0].rsplit("<tr>", 1)[1]
            assert f"entered {today.strftime('%b %-d, %Y')}" in row, (
                "the back-dated row must name the day it was TYPED, or a "
                "reader cannot tell it from a balance that was true that day"
            )

    def test_an_ordinary_row_does_not_claim_to_be_back_dated(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The non-vacuity control for the caption above.

        A same-day true-up records one civil day under both names, so the
        caption must be absent -- one that fired on every row would stop
        distinguishing anything.

        **It said "leaves the two CLOCKS equal" until finding N-299**, and that
        was the defect in one sentence: the entered day was PostgreSQL's and
        the observed day the application's, so a same-day true-up left them
        equal only where the two clocks agreed.  Under the calendar sweep they
        do not, and this control was red on every matrix date from 2026-08-10.

        **Scoped to the row under test, for the reason its sibling above gives**
        (plan step X-f3c-2c).  The account's ORIGINATION row is genuinely
        back-dated now -- ``build_seed_user`` asserts the opening balance for
        the owner's 2024 bootstrap day and the write records today -- so a bare
        ``"entered" not in html`` would fail on a caption that is telling the
        truth about a different row.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            self._assert_balance(
                auth_client, acct_id, "1500.00", observed_on=display_today(),
            )

            html = auth_client.get(
                f"/accounts/{acct_id}/balance-history",
            ).data.decode()

            assert "$1,500.00" in html
            row = html.split("$1,500.00", 1)[0].rsplit("<tr>", 1)[1]
            assert "entered" not in row

    def test_the_entered_day_is_stored_not_read_back_off_created_at(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """The caption's day is the app's, and moving ``created_at`` cannot move it.

        **The regression control for finding N-299**, and it is what makes the
        two sweep tests above non-vacuous on an ordinary clock: they only
        distinguish a stored day from a derived one on a calendar position
        where the Python and PostgreSQL clocks disagree, which is exactly the
        position the sweep runs at and the ordinary suite never reaches.

        This reaches it directly instead.  A row is WRITTEN whose two clocks
        already disagree -- one civil day under both stored names, and a
        recording instant three days later -- which is the state the midnight
        race produces in production, where ``display_today()`` answers one
        civil day and PostgreSQL's ``now()`` the next.  The caption must stay
        ABSENT: the day the card compares is stored, not read back off the
        instant.  A revert to the derived property fails here with an
        ``entered`` caption naming the instant's day.

        **The divergence is INSERTED rather than edited in** (plan step
        X-f3c-2c).  It used to record a same-day true-up through the real door
        and then move that row's ``created_at`` three days;
        ``budget.account_anchor_history`` is append-only, and the production
        state this case is about is an INSERT whose two clocks land apart,
        never an UPDATE that pulls them apart afterwards.  The door is still
        exercised, one line up, for the claim that it records the
        APPLICATION's day.

        ``created_at`` keeps its own job either way: it orders assertions that
        share an ``observed_on``.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            today = display_today()
            self._assert_balance(
                auth_client, acct_id, "1400.00", observed_on=today,
            )
            written = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=acct_id)
                .order_by(AccountAnchorHistory.id.desc())
                .first()
            )
            assert written.recorded_on == today, (
                "the write door must record the APPLICATION's civil day"
            )

            db.session.add(AccountAnchorHistory(
                account_id=acct_id,
                anchor_balance=Decimal("1500.00"),
                observed_on=today,
                recorded_on=today,
                created_at=written.created_at + timedelta(days=3),
            ))
            db.session.commit()

            html = auth_client.get(
                f"/accounts/{acct_id}/balance-history",
            ).data.decode()

            assert "$1,500.00" in html
            row = html.split("$1,500.00", 1)[0].rsplit("<tr>", 1)[1]
            assert "entered" not in row, (
                "the entered day is a stored fact; deriving it from "
                "created_at is what made the caption compare two clocks"
            )

    def test_a_modelled_account_shows_no_reconciliation_columns(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """An HYSA logs what was declared and claims no reconciliation.

        The walk sees recorded CASH only, so an accruing account's difference
        would name interest as untracked spend -- on production the HYSA's one
        row would read $4,863.56 against a $5,363.56 balance (finding N-213).
        The log itself still renders, because "what did I declare, and when" is
        a fact for every kind.
        """
        with app.app_context():
            hysa_type = db.session.query(AccountType).filter_by(
                name="HYSA",
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hysa_type.id,
                    name="HYSA History",
                    anchor_balance=Decimal("5000.00"),
                ),
            )
            db.session.add(InterestParams(
                account_id=acct.id, apy=Decimal("0.04500"),
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
            db.session.commit()

            html = auth_client.get(
                f"/accounts/{acct.id}/balance-history",
            ).data.decode()

            assert "$5,000.00" in html
            assert "Ledger" not in html
            assert "Correction" not in html
            assert "nothing here to reconcile against" in html

    def test_a_plain_account_shows_them(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The non-vacuity control for the case above.

        Same route, same fixture shape, only the KIND differs -- so the missing
        columns there are a property of the account rather than of the card
        never rendering them at all.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            self._assert_balance(auth_client, acct_id, "1500.00")

            html = auth_client.get(
                f"/accounts/{acct_id}/balance-history",
            ).data.decode()

            assert "Ledger" in html
            assert "Correction" in html

    def test_the_opening_is_its_own_row_and_the_assertions_publish_their_pair(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The books are a row of their own; the ASSERTIONS carry the pair.

        **Two changes, one after the other, and the second is this step's.**
        The card withheld both reconciliation cells on the opening until plan
        step X-f3c-2a, because the opening's "ledger" was the records summed
        from a zero seed and the gap between it and the asserted balance was
        opening EQUITY rather than a correction -- rendering it would have told
        the owner their records were off by that equity on the day the account
        opened, which is false.

        Ruling **R-HG** (plan step X-f3c-2b) then separated the two records
        outright: an opening equity is the CLOSING balance for
        ``opened_on``, so an account whose books were moved back to reach its
        earliest record carries NO assertion on that day and there is nothing
        to hang an "Opening" badge on.  The books are rendered as their own
        ``<tfoot>`` row, and its Ledger / Correction cells are empty BY
        CONSTRUCTION -- nothing precedes an opening for a correction to be
        measured against, which is the opposite of the suppression above.

        So the dashes are EXPECTED here, and exactly two of them; the seeded
        account's own declaration agrees with the books it opened, so its
        assertion row publishes a ``$0.00`` correction -- a figure, not a dash.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            html = auth_client.get(
                f"/accounts/{acct_id}/balance-history",
            ).data.decode()

            assert "Opening" in html
            # The two empty-by-construction cells, and NO others: an assertion
            # row that withheld its pair would push this past two.
            assert html.count(">--<") == 2
            # And the correction the assertion publishes is a real figure.
            assert "$0.00" in html

    def test_a_companion_cannot_reach_the_card(
        self, app, companion_client, seed_user, seed_periods_today,
    ):
        """The owner-role decorator on the fragment is graded, not assumed.

        The COMPANION is the case that matters rather than an unrelated
        stranger, for the reason the sibling reconcile route's twin gives: a
        companion is authenticated AND holds granted access to this owner's
        transactions, so ``@require_owner`` refusing them is a real decision.
        This card publishes MORE than that panel does -- every balance the
        owner has ever recorded, with the day each was entered.

        404 rather than 403, per the project's security response rule, so the
        status cannot be used as an existence oracle.
        """
        with app.app_context():
            account_id = seed_user["account"].id

        assert companion_client.get(
            f"/accounts/{account_id}/balance-history",
        ).status_code == 404

    def test_another_users_account_is_not_reachable(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """404 for both "not found" and "not yours" (the security rule).

        The card publishes an account's whole balance history, so an
        unscoped read here would leak more than most: every figure the owner
        has ever recorded, with dates.
        """
        with app.app_context():
            other = _create_other_user_account()
            response = auth_client.get(
                f"/accounts/{other['account'].id}/balance-history",
            )
            assert response.status_code == 404
