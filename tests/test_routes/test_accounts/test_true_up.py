"""Account route tests: the true-up, a same-day duplicate, and the statement day."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.utils.dates import display_today
from tests._test_helpers import current_pay_period
from app.models.ref import AccountType
from app.services import cash_ledger, pay_period_service

from ._helpers import _create_other_user_account


# ── Anchor Balance (Inline + True-up) ─────────────────────────────


class TestTrueUp:
    """Tests for the grid anchor balance true-up endpoints."""

    @pytest.mark.server_clock
    def test_true_up_updates_balance(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH /accounts/<id>/true-up updates the balance and creates history.

        Re-pin (E-19, Commit 3): under the post-E-19 canonical
        account factory, every account already has a fixture-time
        origination AccountAnchorHistory row before the test fires.
        The true-up therefore produces a SECOND history row.  The
        assertion uses ``.order_by(created_at.desc()).first()`` to
        target the row the true-up just wrote -- the same audit-row
        the prior assertion claimed via ``.one()``.
        """
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "3000.00"},
            )

            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            acct = db.session.get(Account, account_id)
            assert cash_ledger.resolve_anchor(acct).balance == Decimal("3000.00")

            # The true-up's history row is the most recent for this
            # account; the fixture-time origination row is the prior
            # one and is unaffected by the route call.
            history = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account_id)
                .order_by(AccountAnchorHistory.created_at.desc())
                .first()
            )
            assert history.anchor_balance == Decimal("3000.00")

    def test_true_up_records_without_a_current_pay_period(
        self, app, auth_client, seed_user, db,
    ):
        """A true-up with no period covering today is RECORDED, not refused.

        The direct inversion of ``test_true_up_no_current_period``, which
        asserted a 400 and the message "No current pay period found".  Ruling
        R-EO deleted the pay period from a balance assertion, so this door has
        no period to resolve and nothing it can fail to find -- refusing a
        balance the user read off their bank because a BUDGETING artifact is
        missing was finding N-134's shape on the true-up door.

        Non-vacuity: ``seed_user`` alone seeds no period covering today (that
        is what made the old 400 fire), and the assertion is read back out of
        the history table rather than off the response.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            # The PRECONDITION, asserted rather than assumed.  This test is
            # about the no-current-period door, so it grades nothing if the
            # fixture ever starts seeding a period covering today -- it would
            # pass as an ordinary true-up.  ``seed_user``'s bootstrap period is
            # HARDCODED to 2026-01-05..2026-01-18 (``conftest``), not derived
            # from the user's creation date as an earlier version of this
            # comment said, so no period contains any day after 2026-01-18
            # and this is not date-fragile.
            assert current_pay_period(
                seed_user["user"].id,
            ) is None, (
                "this test grades the no-current-period door; the fixture now "
                "seeds a period covering today, so it would pass vacuously"
            )

            before_rows = db.session.query(AccountAnchorHistory).filter_by(
                account_id=account_id,
            ).count()

            # NOT the fixture's own $1,000.00.  A first version of this test
            # asserted the resolver returned "1000.00" -- which ``seed_user``'s
            # origination assertion already says, so a true-up that returned 200
            # and wrote NOTHING passed it.  Proven vacuous by a neutral review,
            # which made ``stage_anchor_true_up`` return before its
            # ``session.add`` and watched this test stay green.
            response = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1750.25"},
            )

            assert response.status_code == 200, response.data[:200]
            db.session.expire_all()
            account = db.session.get(Account, account_id)
            anchor = cash_ledger.resolve_anchor(account)
            assert anchor.balance == Decimal("1750.25")
            # Recorded AS OF the user's civil day, not the process's.
            assert anchor.observed_on == display_today()
            # Exactly one row appended -- an INSERT that also destroyed the
            # origination assertion would satisfy the balance check alone.
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account_id,
            ).count() == before_rows + 1

    def test_true_up_invalid_amount(self, app, auth_client, seed_user, seed_periods_today):
        """An unparseable balance is refused with a 400 the browser RENDERS.

        The contract changed at plan step X-f1c4c, on the developer's ruling,
        and the old one is why: this door answered ``jsonify(errors=...)`` with
        no marker header, and ``base.html`` configures 4xx as ``swap:false`` --
        so a correct 400 with a correct message rendered NOTHING and the form
        sat there.  The test that graded it asserted only that the JSON carried
        an ``errors`` key, which the invisible response satisfied.

        What is graded now is the property that was missing: the response
        carries ``Shekel-Designed-Fragment``, which is the ONLY thing that makes
        htmx swap a 4xx, and its body is the editor re-rendered with the reason
        in it.  Every clause is load-bearing -- drop the header and the user
        sees a dead form again, drop the body check and a bare 400 passes.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            rows_before = db.session.query(AccountAnchorHistory).filter_by(
                account_id=account_id,
            ).count()

            response = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "abc"},
            )

            assert response.status_code == 400
            assert response.headers.get("Shekel-Designed-Fragment") == "1", (
                "a 4xx without the marker header is non-swapping, so the "
                "refusal would render nothing at all"
            )
            html = response.data.decode()
            # The editor came back, in EDIT mode, so the user can correct the
            # value in place rather than losing the surface.
            assert 'name="anchor_balance"' in html
            # ...and it says WHY.  The VALIDATOR's own message, not the field
            # name: ``assert "anchor_balance" in html`` was implied by the line
            # above and could not fail on its own, so a response carrying the
            # editor and no error text at all passed it.
            assert "Not a valid number" in html
            assert 'role="alert"' in html
            # Nothing was WRITTEN, counted rather than read back.  Asserting the
            # resolved balance is still $1,000.00 would be vacuous: that is
            # ``seed_user``'s own origination figure, so a path that appended a
            # fresh $1,000.00 assertion dated today -- which would move
            # ``reconciled_through`` and absorb outstanding purchases -- passes
            # it.  The same trap is documented twenty lines above, on the
            # positive side.
            db.session.expire_all()
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account_id,
            ).count() == rows_before

    def test_true_up_other_users_account(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH /accounts/<id>/true-up for another user's account returns 404.

        IDOR write-path: must verify the anchor balance was not changed.
        """
        with app.app_context():
            other = _create_other_user_account()
            orig_balance = cash_ledger.resolve_anchor(other["account"]).balance

            response = auth_client.patch(
                f"/accounts/{other['account'].id}/true-up",
                data={"anchor_balance": "9999.00"},
            )

            assert response.status_code == 404

            # Prove no state change occurred.
            db.session.expire_all()
            db.session.refresh(other["account"])
            assert cash_ledger.resolve_anchor(other["account"]).balance == orig_balance

    @pytest.mark.parametrize(
        ("revert", "expected"),
        [
            (None, True),
            ("dashboard", False),
            ("accounts", False),
            ("investment", False),
            ("cash", False),
        ],
        ids=["grid", "dashboard", "cockpit", "investment-hero", "cash-hero"],
    )
    def test_the_as_of_snippet_goes_to_the_grid_and_only_the_grid(
        self, app, auth_client, seed_user, seed_periods_today, revert, expected,
    ):
        """The out-of-band "as of" caption update is the GRID's alone.

        **One rule, no per-surface exception list** (plan step X-f1e3): an
        out-of-band caption update goes to the caption nothing else refreshes.

        The grid's ``#anchor-as-of`` sits at page level in ``grid/grid.html``,
        so nothing on that page redraws it and the snippet is the only way it
        can move.  Every other opener re-fetches its own region on the
        ``balanceChanged`` this response fires:

        * the cockpit, the investment hero and the cash hero carry no
          ``#anchor-as-of`` element at all, so a snippet would orphan-target
          (``htmx:oobErrorNoTarget``);
        * the DASHBOARD carries one, and it was emitted there until this step.
          That was a redundant SECOND render of one fact -- ``#pulse-section``
          re-renders the same caption from ``reconciled_through``, which
          ``dashboard_service._pulse`` states is provably equal to the
          ``resolve_anchor`` day the snippet carries -- and it was the
          mechanism that destroyed the back-dated acknowledgement riding on it
          (finding N-199).

        The dashboard case is the one this parametrization exists for: with
        the two original tests covering only ``accounts`` and the default, a
        regression that re-emitted the snippet for the dashboard passed.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            url = f"/accounts/{acct_id}/true-up"
            if revert is not None:
                url = f"{url}?revert={revert}"
            response = auth_client.patch(
                url, data={"anchor_balance": "3210.00"},
            )
            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"
            # **The whole opening tag, so the id and its OUT-OF-BAND-ness are
            # graded together.**  The bare ``hx-swap-oob`` attribute would
            # match whichever fragment happened to ride along (the reconcile
            # prompt and the acknowledgement are out-of-band too), and the id
            # alone leaves the attribute ungraded -- an adversarial review
            # deleted it and measured the whole suite still passing, with the
            # caption then rendering INSIDE the grid balance cell and the
            # page-level ``#anchor-as-of`` never updating again.
            assert ((
                '<small class="text-muted" id="anchor-as-of" '
                'hx-swap-oob="true">'
            ) in response.data.decode()) is expected


class TestTrueUpSameDayDuplicate:
    """Ruling R-EQ: a re-submit of the governing balance writes nothing."""

    def test_double_submit_creates_one_history_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Two identical true-ups same day produce exactly one history row.

        The second submit asserts what the first made governing, so the
        write door appends nothing and the route returns the already-current
        balance -- the user sees idempotent success rather than a 500 or a
        duplicate row.  **It was a unique-index rejection translated by an
        exception handler until plan step X-f1c4b** (ruling R-EQ); the
        user-visible behaviour is unchanged, which is why this test is.
        """
        with app.app_context():
            account_id = seed_user["account"].id

            r1 = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1234.56"},
            )
            assert r1.status_code == 200

            r2 = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1234.56"},
            )
            # Idempotent success: both requests return 200.
            assert r2.status_code == 200

            # Exactly one true-up audit row was added.  Re-pin
            # (E-19, Commit 3): the factory writes a fixture-time
            # origination row at $1000.00 (seed_user's seed balance),
            # so the total count after the double-submit is 2 (one
            # origination + one true-up), not 1.  The claim is that the
            # true-up balance ($1234.56) only appears once -- the second
            # submit changed nothing and so wrote nothing.
            db.session.expire_all()
            history_at_trueup_balance = (
                db.session.query(AccountAnchorHistory)
                .filter_by(
                    account_id=account_id,
                    anchor_balance=Decimal("1234.56"),
                )
                .all()
            )
            assert len(history_at_trueup_balance) == 1, (
                f"Expected 1 anchor history row at the true-up balance "
                f"after double-submit, found {len(history_at_trueup_balance)}; "
                "a submission that changes nothing must write nothing."
            )

    def test_same_day_different_balance_creates_two_rows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Same-day true-ups with different balances both succeed.

        A legitimate same-day correction (the user noticed an error and
        re-trued at a different amount) changes what governs, so it must NOT
        be blocked.
        """
        with app.app_context():
            account_id = seed_user["account"].id

            r1 = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1000.00"},
            )
            r2 = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1100.00"},
            )
            assert r1.status_code == 200
            assert r2.status_code == 200

            # Re-pin (E-19, Commit 3): the factory writes a
            # fixture-time origination row before the test starts.
            # Filter on the true-up balances ($1000, $1100) to verify
            # both distinct true-ups produced their own audit row.  The
            # fixture's origination row is at the same $1000 balance as r1
            # but on an EARLIER day, so r1 changed what governs (the day
            # moved) and appended; r2 then appended the $1100 correction.
            db.session.expire_all()
            history_balances = {
                h.anchor_balance for h in
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account_id)
                .all()
            }
            assert history_balances == {
                Decimal("1000.00"), Decimal("1100.00"),
            }, (
                f"Expected exactly the two true-up balances in the "
                f"history, got {sorted(history_balances)}"
            )


class TestTrueUpStatementDay:
    """The true-up carries the day it was read from (plan step X-f1c4c).

    Rulings **R-EE** (the true-up form gets its own statement date) and
    **R-EI** (it goes on a second line).  The service contract is pinned in
    ``tests/test_services/test_anchor_service.py``; these cases grade the WIRING
    -- that the form offers the field bounded, that the submitted day reaches
    the write door, and that a refusal renders where the submission was made.
    """

    #: Days back from today for a day that is assertable on any calendar day.
    #: ``seed_periods_today`` starts its schedule ``today.weekday() + 56`` days
    #: back, so 20 is inside the floor with room to spare.
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

    def test_the_editor_offers_a_bounded_statement_day(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The date box is present, defaults to today, and carries both bounds.

        The bounds are read from the same functions the seam refuses by, not
        written as literals, so this cannot pass against a second definition of
        the floor.  Default = today is ruling R-EE's "one click stays one
        click": leaving the box alone means "I am reading my bank now".
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            floor = pay_period_service.earliest_recordable_day(
                seed_user["user"].id,
            )
            today = display_today()

            html = auth_client.get(
                f"/accounts/{acct_id}/anchor-form"
            ).data.decode()

            assert 'name="observed_on"' in html
            assert f'value="{today.isoformat()}"' in html
            assert f'min="{floor.isoformat()}"' in html
            assert f'max="{today.isoformat()}"' in html

    def test_a_submitted_day_is_what_the_assertion_carries(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A back-dated true-up through the route lands on the day submitted."""
        with app.app_context():
            acct_id = seed_user["account"].id
            statement_day = self._statement_day(seed_user)

            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up",
                data={
                    "anchor_balance": "1750.25",
                    "observed_on": statement_day.isoformat(),
                },
            )

            assert response.status_code == 200, response.data[:200]
            db.session.expire_all()
            row = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=acct_id, observed_on=statement_day)
                .one()
            )
            assert row.anchor_balance == Decimal("1750.25")
            # Non-vacuity: the stamped day is not the default, so a route that
            # dropped the field could not pass.
            assert statement_day != display_today()

    def test_a_blank_day_means_today(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An empty date box is "now", not a validation error.

        An untouched HTML date input submits ``""``, and ``fields.Date()``
        deserializes that to an error rather than to "absent" -- so without the
        schema's ``@pre_load`` normalizer, clearing the box would 400 instead of
        meaning today.  This is that normalizer's control, submitted the way a
        browser actually submits it.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up",
                data={"anchor_balance": "1751.25", "observed_on": ""},
            )

            assert response.status_code == 200, response.data[:200]
            db.session.expire_all()
            anchor = cash_ledger.resolve_anchor(
                db.session.get(Account, acct_id),
            )
            assert anchor.balance == Decimal("1751.25")
            assert anchor.observed_on == display_today()

    @pytest.mark.parametrize(
        "offset_days, expected_phrase",
        [
            (1, "has not happened yet"),
            (None, "recorded history starts on"),
        ],
        ids=["future-day", "below-the-schedule"],
    )
    def test_an_out_of_bounds_day_is_refused_and_RENDERED(
        self, app, auth_client, seed_user, seed_periods_today,
        offset_days, expected_phrase,
    ):
        """Both bounds refuse with a 400 the browser will actually swap.

        The marker header is the load-bearing assertion: ``base.html``
        configures 4xx as non-swapping, so a refusal without it renders
        nothing and the form appears to ignore the click.  Parametrized over
        the two bounds because they are one surface with one contract -- a fix
        that rendered only the future case would pass a single-case test.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            before = db.session.query(AccountAnchorHistory).filter_by(
                account_id=acct_id,
            ).count()
            floor = pay_period_service.earliest_recordable_day(
                seed_user["user"].id,
            )
            if offset_days is None:
                bad_day = floor - timedelta(days=1)
            else:
                bad_day = display_today() + timedelta(days=offset_days)

            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up",
                data={
                    "anchor_balance": "1750.25",
                    "observed_on": bad_day.isoformat(),
                },
            )

            assert response.status_code == 400
            assert response.headers.get("Shekel-Designed-Fragment") == "1", (
                "a 4xx without the marker header is non-swapping, so this "
                "refusal would render nothing at all"
            )
            html = response.data.decode()
            assert expected_phrase in html
            # The editor came back with the user's own inputs, so the wrong
            # field can be corrected without retyping the right one.
            assert 'name="observed_on"' in html
            assert f'value="{bad_day.isoformat()}"' in html
            assert 'value="1750.25"' in html
            # The re-rendered editor still carries BOTH bounds.  Graded here as
            # well as on the GET because the error path builds its context
            # separately: a template change that dropped them from the
            # rejection render would leave the corrected re-submit unbounded in
            # the browser, and only the GET test would have noticed.
            assert f'min="{floor.isoformat()}"' in html
            assert f'max="{display_today().isoformat()}"' in html
            # Escape reverts from EITHER field.  A substring check passes on the
            # balance input alone, so the count is what pins the date input's
            # own wiring -- ruling R-EI's second line is where the user's cursor
            # ends up, and Escape silently dying there is not visible in review.
            assert html.count('data-action="anchor-cancel-on-escape"') == 2
            # Nothing was written.
            db.session.expire_all()
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=acct_id,
            ).count() == before

    def test_a_rename_through_the_edit_door_moves_no_coverage_boundary(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A rename is not a balance reading (plan step X-f1e, finding N-195).

        This is the defect the deleted door made reachable, tested at the level
        that matters rather than at the field: the edit form PRE-FILLED the
        current balance, so an ordinary rename re-submitted it, and the two
        doors disagreed about whether that is an assertion.  The route's own
        gate ("did the FIELD change") said no; ruling R-EQ's rule in
        ``stage_anchor_true_up`` ("does this change what GOVERNS", the day
        included) said yes -- and the day is what moves ``reconciled_through``,
        which decides which outstanding purchases the walk treats as already
        inside the asserted balance.

        Aligning the route on the service's rule would have made a rename
        absorb purchases the user never reconciled.  Deleting the surface makes
        the question unaskable, and that is what this asserts: after an edit
        through this door, the coverage boundary is exactly where it was.

        It replaces a test that pinned the OLD door's day, whose own docstring
        said it existed only until this step landed.

        **The submitted balance is CHANGED, deliberately.**  An unchanged echo
        -- what the form actually pre-filled -- is the one input the deleted
        route gate short-circuited, so a test built on it passes against the old
        code too and grades nothing.  Measured: a first version of this test did
        exactly that and survived a revert.  A CHANGED balance is what the old
        door staged an assertion from, dated today, which is what moved the
        boundary; so this fails on revert, which is the only reason it is here.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            account = db.session.get(Account, acct_id)
            governing_before = cash_ledger.resolve_anchor(account)
            boundary_before = cash_ledger.reconciled_through(acct_id).observed_day
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )

            response = auth_client.post(f"/accounts/{acct_id}", data={
                "name": "Renamed By Hand",
                "account_type_id": str(checking_type.id),
                "anchor_balance": str(governing_before.balance + Decimal("250.00")),
                "version_id": str(account.version_id),
            }, follow_redirects=True)

            assert response.status_code == 200
            db.session.expire_all()
            assert cash_ledger.reconciled_through(acct_id).observed_day == (
                boundary_before
            ), (
                "a rename must not move the coverage boundary -- doing so "
                "silently absorbs purchases the user never reconciled"
            )
            governing_after = cash_ledger.resolve_anchor(
                db.session.get(Account, acct_id),
            )
            assert (governing_after.balance, governing_after.observed_on) == (
                governing_before.balance, governing_before.observed_on,
            )
            assert db.session.get(Account, acct_id).name == "Renamed By Hand"

    def test_a_refusal_keeps_the_surface_that_opened_the_editor(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Cancel after a refusal returns to the opener, not to the grid cell.

        The editor opens from five surfaces and threads a ``revert`` token so
        Cancel restores the right one.  A rejection RE-RENDERS the editor, so
        the token has to survive the rejection -- otherwise a refused dashboard
        true-up leaves the card able only to revert into the grid's display
        cell, which is not on that page.
        """
        with app.app_context():
            acct_id = seed_user["account"].id

            response = auth_client.patch(
                f"/accounts/{acct_id}/true-up?revert=dashboard",
                data={
                    "anchor_balance": "1750.25",
                    "observed_on": (
                        display_today() + timedelta(days=1)
                    ).isoformat(),
                },
            )

            assert response.status_code == 400
            html = response.data.decode()
            # Both revert affordances -- the Cancel button's hx-get and the
            # Escape handler's data-revert-url -- must point at the dashboard
            # card, exactly as they do on the non-rejected GET.
            assert 'hx-get="/dashboard/balance"' in html, (
                "the revert token was dropped by the rejection path"
            )
            assert 'data-revert-url="/dashboard/balance"' in html
            assert f"/accounts/{acct_id}/anchor-display" not in html
            # The mutation URL keeps the token too, so a corrected re-submit
            # still lands back on the dashboard rather than on the grid.
            assert 'hx-patch="/accounts/'f'{acct_id}/true-up?revert=dashboard"' in html
